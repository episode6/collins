"""The in-app socket service the MCP shim relays session tool calls through.

One `SessionToolService` listens on the per-instance Unix socket named in the
`--mcp-config` file Collins writes (see `mcptools`); every session's shim
(`mcp_shim.py`) connects here. The wire is newline-delimited JSON, framed and
bounded by `mcptools`; the protocol is three ops — a `hello` carrying the
shim's pid (the only session identity there is), then `list` and `call`
round-trips correlated by id.

The service knows no tools and no widgets: it takes injected `list_tools()`
and `dispatch(pid, tool, args)` callables, so the whole connection machinery
is testable headless — CI has Gio/GLib but no GTK stack (tests/conftest.py).
Everything runs on the GLib main loop; Gio's async socket API means no
threads, and per connection the loop is strictly read → reply → read, so a
peer that stops reading its replies stalls only itself, never this process.
A dispatcher that can't answer at once (`mcptools.DeferredResult` — a
`show_image` downloading a URL) just makes that reply late: the connection
goes quiet until it lands, which is the same invariant, held longer.

The socket sits in a user-private directory, but any local process of the
user's can still connect — treat every frame as untrusted input: a peer
whose first line isn't a well-formed hello, or that breaks framing in any
way, is disconnected rather than guessed at. The hello's pid is load-bearing
for authorization (the dispatcher walks /proc ancestry from it to decide
which tab a call may act on), so it is never taken on faith: it must match
the peer's kernel-verified pid (SO_PEERCRED), or the connection is dropped.
Argument validation against the tool schemas is the dispatcher's job
(`mcptools.validate_args`), not ours.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from gi.repository import Gio, GLib

from . import mcptools

# `_reply_for`'s third answer, beside a frame and a drop: the dispatcher took
# the call but hasn't finished it (`mcptools.DeferredResult`), so this
# connection simply goes quiet until it does.
_PENDING = object()


def _result_frame(rid, ok: bool, text: str) -> dict:
    """One call's reply: the success message, or the error string."""
    if ok:
        return {"id": rid, "ok": True, "message": text}
    return {"id": rid, "ok": False, "error": text}


class _Client:
    """One connected shim: its streams, and the pid its hello declared."""

    def __init__(self, connection: Gio.SocketConnection) -> None:
        self.connection = connection
        self.input = Gio.DataInputStream.new(connection.get_input_stream())
        self.output = connection.get_output_stream()
        self.pid: int | None = None  # set by the hello; None = not greeted yet


class SessionToolService:
    """Owns the listening socket and every live shim connection.

    `list_tools()` returns the MCP tool table to serve; `dispatch(pid, tool,
    args)` runs one call and returns `(ok, text)` — the success message or the
    error string — or a `mcptools.DeferredResult` it will resolve with that
    pair later. Both are invoked on the GLib main loop.
    """

    def __init__(
        self,
        socket_path: str,
        list_tools: Callable[[], list],
        dispatch: Callable[[int, str, object], mcptools.ToolResult],
    ) -> None:
        self._path = socket_path
        self._list_tools = list_tools
        self._dispatch = dispatch
        self._service: Gio.SocketService | None = None
        self._clients: set[_Client] = set()

    def start(self) -> None:
        """Bind the socket and start accepting. Raises on failure.

        A leftover socket file is unlinked first: it can only be ours — the
        path is keyed by application id and GApplication uniqueness keeps two
        instances of one id from running — so it's the residue of a SIGKILL,
        not another listener.
        """
        # sun_path is 108 bytes including the NUL. Gio doesn't check: it
        # silently truncates, "listening" on a path no shim will ever dial —
        # the one failure shape the caller couldn't see. Raise instead so
        # startup logs it and skips the feature. Real paths live under
        # $XDG_RUNTIME_DIR and sit far below the limit.
        if len(os.fsencode(self._path)) > 107:
            raise OSError(f"socket path too long for AF_UNIX: {self._path}")
        os.makedirs(os.path.dirname(self._path), mode=0o700, exist_ok=True)
        try:
            os.unlink(self._path)
        except OSError:
            pass
        service = Gio.SocketService.new()
        service.add_address(
            Gio.UnixSocketAddress.new(self._path),
            Gio.SocketType.STREAM,
            Gio.SocketProtocol.DEFAULT,
            None,
        )
        service.connect("incoming", self._on_incoming)
        self._service = service

    def stop(self) -> None:
        """Stop accepting, drop every connection, remove the socket file."""
        if self._service is not None:
            self._service.stop()
            self._service.close()
            self._service = None
        for client in list(self._clients):
            self._close(client)
        try:
            os.unlink(self._path)
        except OSError:
            pass

    # -- connection handling -------------------------------------------------

    def _on_incoming(self, _service, connection: Gio.SocketConnection, _source) -> bool:
        # Holding the client in the set is what keeps the connection alive —
        # Gio drops its own reference when this handler returns.
        client = _Client(connection)
        self._clients.add(client)
        self._read_next(client)
        return True

    def _read_next(self, client: _Client) -> None:
        client.input.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_line, client)

    def _on_line(self, stream: Gio.DataInputStream, result, client: _Client) -> None:
        try:
            line, _length = stream.read_line_finish(result)
        except GLib.Error:
            self._close(client)
            return
        if line is None:  # EOF: the session (or its shim) went away
            self._close(client)
            return
        try:
            message = mcptools.decode_message(line)
        except ValueError:
            self._close(client)
            return
        if client.pid is None:
            self._greet(client, message)
            return
        reply = self._reply_for(client, message)
        if reply is _PENDING:
            return  # a deferred call: _send happens when it resolves
        if reply is None:
            self._close(client)
            return
        self._send(client, reply)

    def _greet(self, client: _Client, message: dict) -> None:
        """The connection's first frame must be a well-formed, honest hello.

        The declared pid is checked against the peer's real pid as the kernel
        reports it (SO_PEERCRED): the pid is what authorizes a call to act on
        a specific tab, and any local process of the user's can open this
        socket, so a client claiming another process's pid — say, a tab's
        actual `claude` — must be dropped, not believed.
        """
        pid = message.get("pid")
        if (
            message.get("op") != "hello"
            or not isinstance(pid, int)
            or isinstance(pid, bool)
            or pid <= 0
            or pid != self._peer_pid(client.connection)
        ):
            self._close(client)
            return
        client.pid = pid
        self._read_next(client)

    @staticmethod
    def _peer_pid(connection: Gio.SocketConnection) -> int | None:
        """The kernel's answer for who holds the other end of *connection*,
        or None when it won't say (which fails the hello: this is Linux-only
        code already — the dispatcher reads /proc — so credentials being
        unavailable means something is wrong, not that we should trust the
        peer's own claim instead)."""
        try:
            return connection.get_socket().get_credentials().get_unix_pid()
        except GLib.Error:
            return None

    def _reply_for(self, client: _Client, message: dict) -> dict | object | None:
        """The reply frame for one request, `_PENDING` when the answer comes
        later, or None for a peer worth dropping."""
        rid = message.get("id")
        if rid is None:
            return None
        op = message.get("op")
        if op == "list":
            return {"id": rid, "ok": True, "tools": self._list_tools()}
        if op == "call":
            tool = message.get("tool")
            if not isinstance(tool, str) or not tool:
                return {"id": rid, "ok": False, "error": "Missing tool name"}
            result = self._dispatch(client.pid, tool, message.get("args"))
            if isinstance(result, mcptools.DeferredResult):
                # Nothing is read from this client until the reply goes out,
                # so a slow call stalls its own session and no other.
                result.watch(
                    lambda ok, text: self._send(client, _result_frame(rid, ok, text))
                )
                return _PENDING
            return _result_frame(rid, *result)
        return None

    def _send(self, client: _Client, reply: dict) -> None:
        if client not in self._clients:
            return  # a deferred reply that outlived its connection
        try:
            data = mcptools.encode_message(reply)
        except ValueError:
            self._close(client)
            return
        client.output.write_all_async(
            data, GLib.PRIORITY_DEFAULT, None, self._on_sent, client
        )

    def _on_sent(self, stream, result, client: _Client) -> None:
        try:
            stream.write_all_finish(result)
        except GLib.Error:
            self._close(client)
            return
        # Only now ask for the next request: read → reply → read is what lets
        # a peer that floods requests without reading replies stall itself
        # instead of queueing unbounded work here.
        self._read_next(client)

    def _close(self, client: _Client) -> None:
        if client not in self._clients:
            return  # an async callback racing a stop(); already closed
        self._clients.discard(client)
        try:
            client.connection.close(None)
        except GLib.Error:
            pass
