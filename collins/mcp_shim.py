"""The stdio MCP server Collins registers with every session it launches.

The agent CLI spawns this module (`python -m collins.mcp_shim`, pointed at by
the `--mcp-config` file Collins writes) and speaks MCP to it over stdio. The
shim itself knows no tools: it relays `tools/list` and `tools/call` to the
running Collins instance over the Unix socket named by `COLLINS_MCP_SOCKET`,
so the tool set lives app-side and adding a tool never changes this file.

Two rules shape everything here:

- **Stdlib only, and nothing from the rest of `collins`.** The shim runs as a
  child of the agent CLI, not of Collins, and must not drag in GTK — or even
  sibling modules, so a mismatched or half-upgraded install can't break
  session startup. The wire constants shared with `collins.mcptools` are
  mirrored by hand for the same reason.
- **Never break the session.** Collins being gone (quit, crashed, stale
  config) degrades to an empty tool list and clean "Collins is not running"
  tool errors; the MCP handshake always succeeds. A failed round trip marks
  the connection dead and the next request reconnects from scratch, which is
  what heals a Collins restart — there are no retry loops within a request.

Stdout carries protocol bytes only (MCP's stdio framing is one JSON-RPC 2.0
message per line); debugging goes to the file named by `COLLINS_SHIM_LOG`,
if set. Error strings are agent-facing English, deliberately untranslated.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading

_CONNECT_TIMEOUT = 1.0
_LIST_TIMEOUT = 3.0
_CALL_TIMEOUT = 15.0

# Mirrors collins.mcptools.MAX_LINE (see the stdlib-only rule above).
_MAX_LINE = 1024 * 1024

# The newest MCP revision this hand-rolled surface implements; sent back when
# the client doesn't name one itself.
_PROTOCOL_VERSION = "2025-06-18"

_NOT_RUNNING = "Collins is not running"
_NO_REPLY = "Collins did not respond in time"


def _log(text: str) -> None:
    path = os.environ.get("COLLINS_SHIM_LOG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text.rstrip("\n") + "\n")
    except OSError:
        pass


class _AppUnavailable(Exception):
    """Collins can't be reached at all: no socket configured, nothing
    listening, or the connection died mid-request."""


class _AppTimeout(Exception):
    """Collins is listening but didn't answer within the deadline."""


class _AppLink:
    """The lazy connection to the Collins socket service.

    Nothing is dialed until the first request needs it, and any failure tears
    the connection down so the next request starts fresh. One round trip is in
    flight at a time — the app replies in order, correlated by id, and the
    lock keeps a send and its readline paired.
    """

    def __init__(self, socket_path: str | None):
        self._path = socket_path
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._reader = None
        self._next_id = 0

    def request(self, payload: dict, timeout: float) -> dict:
        """Send one op to Collins and return its reply dict.

        Raises _AppUnavailable / _AppTimeout; either way the connection is
        already dropped and the next request will reconnect.
        """
        with self._lock:
            if self._sock is None:
                self._connect()
            self._next_id += 1
            message = dict(payload, id=self._next_id)
            try:
                self._sock.settimeout(timeout)
                self._sock.sendall(json.dumps(message).encode("utf-8") + b"\n")
                line = self._reader.readline(_MAX_LINE + 1)
            except TimeoutError:
                self._drop()
                raise _AppTimeout() from None
            except OSError:
                self._drop()
                raise _AppUnavailable() from None
            # Empty read is EOF (Collins went away); a line that fills the
            # limit without its newline is over-long — either way the stream
            # can't be trusted any further.
            if not line.endswith(b"\n"):
                self._drop()
                raise _AppUnavailable()
            try:
                reply = json.loads(line)
            except ValueError:
                self._drop()
                raise _AppUnavailable() from None
            if not isinstance(reply, dict) or reply.get("id") != message["id"]:
                self._drop()
                raise _AppUnavailable()
            return reply

    def _connect(self) -> None:
        if not self._path:
            raise _AppUnavailable()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(_CONNECT_TIMEOUT)
            sock.connect(self._path)
            # The hello identifies this shim by pid — that's the only session
            # identity there is; Collins walks /proc ancestry from it to a tab.
            hello = {"op": "hello", "pid": os.getpid(), "v": 1}
            sock.sendall(json.dumps(hello).encode("utf-8") + b"\n")
        except OSError:
            sock.close()
            raise _AppUnavailable() from None
        self._sock = sock
        self._reader = sock.makefile("rb")

    def _drop(self) -> None:
        for closable in (self._reader, self._sock):
            if closable is not None:
                try:
                    closable.close()
                except OSError:
                    pass
        self._reader = None
        self._sock = None


def _write(message: dict) -> None:
    data = json.dumps(message).encode("utf-8") + b"\n"
    _log("> " + data.decode("utf-8", errors="replace"))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def _result(rid, result: dict) -> None:
    _write({"jsonrpc": "2.0", "id": rid, "result": result})


def _error(rid, code: int, message: str) -> None:
    _write({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})


def _tool_text(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _handle_initialize(rid, params: dict) -> None:
    version = params.get("protocolVersion")
    _result(rid, {
        "protocolVersion": version if isinstance(version, str) and version else _PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "collins", "version": "1.0"},
    })


def _handle_tools_list(rid, link: _AppLink) -> None:
    try:
        reply = link.request({"op": "list"}, _LIST_TIMEOUT)
    except _AppTimeout:
        _error(rid, -32000, _NO_REPLY)
        return
    except _AppUnavailable:
        # The degraded steady state: session runs, Collins just has nothing
        # to offer it. Not an error — errors here would nag every launch
        # that outlives the app.
        _result(rid, {"tools": []})
        return
    tools = reply.get("tools") if reply.get("ok") else None
    _result(rid, {"tools": tools if isinstance(tools, list) else []})


def _handle_tools_call(rid, link: _AppLink, params: dict) -> None:
    name = params.get("name")
    if not isinstance(name, str) or not name:
        _error(rid, -32602, "Invalid params: missing tool name")
        return
    args = params.get("arguments")
    try:
        reply = link.request(
            {"op": "call", "tool": name, "args": args if isinstance(args, dict) else {}},
            _CALL_TIMEOUT,
        )
    except _AppTimeout:
        _result(rid, _tool_text(_NO_REPLY, is_error=True))
        return
    except _AppUnavailable:
        _result(rid, _tool_text(_NOT_RUNNING, is_error=True))
        return
    if reply.get("ok"):
        _result(rid, _tool_text(str(reply.get("message") or "Done.")))
    else:
        _result(rid, _tool_text(str(reply.get("error") or "Collins reported an error."), is_error=True))


def _handle(link: _AppLink, msg: dict) -> None:
    method = msg.get("method")
    rid = msg.get("id")
    if not isinstance(method, str):
        if rid is not None:
            _error(rid, -32600, "Invalid Request")
        return
    if rid is None:
        # Notifications (notifications/initialized, cancellations, …) need no
        # reply, and JSON-RPC forbids replying to them at all.
        return
    params = msg.get("params")
    params = params if isinstance(params, dict) else {}
    if method == "initialize":
        _handle_initialize(rid, params)
    elif method == "ping":
        _result(rid, {})
    elif method == "tools/list":
        _handle_tools_list(rid, link)
    elif method == "tools/call":
        _handle_tools_call(rid, link, params)
    else:
        _error(rid, -32601, f"Method not found: {method}")


def main() -> int:
    link = _AppLink(os.environ.get("COLLINS_MCP_SOCKET"))
    stdin = sys.stdin.buffer
    while True:
        line = stdin.readline()
        if not line:
            return 0  # the CLI closed our stdin; the session is over
        _log("< " + line.decode("utf-8", errors="replace"))
        if not line.strip():
            continue
        try:
            try:
                msg = json.loads(line)
            except ValueError:
                _error(None, -32700, "Parse error")
                continue
            if isinstance(msg, dict):
                _handle(link, msg)
            else:
                _error(None, -32600, "Invalid Request")
        except BrokenPipeError:
            # The CLI went away mid-reply; every reply path lands here. Point
            # stdout at devnull so the interpreter's exit-time flush of the
            # half-written buffer can't raise the same error again.
            try:
                os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
            except OSError:
                pass
            return 0


if __name__ == "__main__":
    sys.exit(main())
