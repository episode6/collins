# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The service's end of the API: a `Soup.Server` on the Unix socket.

Split-service spec §3.20. `ApiServer` listens on ``api.sock`` beside
``mcp.sock`` in the 0700 runtime directory (`socket_path`; with no
``$XDG_RUNTIME_DIR``, under the state directory, the fallback the sandbox
plan directory uses), ``chmod 0600`` after `listen`, and never calls
`Soup.Server.get_uris()` (F7's three traps). A stale socket file whose
connect is refused is unlinked before `listen`; one that answers means a
service is running (`probe_socket`), and `service.main` exits 0 then.

The WebSocket handler at ``/api/ws`` builds a `Connection` per WebSocket
and groups connections by the hello's ``client_id`` into one
`SocketClient` each: the core's `Client` protocol (`device`, `term`,
`sink_for`, `forget`, `deliver`) over the client's **primary** connection,
which carries the subscription, the attaches and every event; the **sync
channel** (a second WebSocket whose hello carries the same ``client_id``
and ``channel: "sync"``, D26) answers the requests a client blocks on, and
nothing is ever sent down it unasked. A request from either connection is
answered on the connection that asked. The primary closing ends the client
(`ServiceCore.client_gone`: nothing changes on the service but "no client
is attached", §3.1 rule 5) and closes its sync channel; the sync channel
closing alone drops only itself.

Text frames are `protocol.decode`d and `validate`d (rule 5: every frame is
foreign content); a request goes to `ServiceCore.handle` and its reply is
`encode`d back, a client event to `ServiceCore.deliver`, a binary frame is
`unpack_frame`d, `check_frame`d and ``TAG_INPUT`` goes to
`ServiceCore.input`. The server itself serves `hello` (`negotiate` the
protocol window, a client outside it refused ``protocol`` with both
versions and closed after the reply; `client_id`, `device`, `locale` and
`term` recorded; the reply carries `PROTOCOL`, `MIN_PROTOCOL`, the package
version, the state's `service_id`, the hostname, the caps and the proof
file), `local` (the hex against the 32 random bytes written to
``local-proof`` at start, D11) and `service.restart` (``now``: handed to
the `on_restart` callback `service.main` gives it; ``idle``: handed to it
once the core's `restart_when_idle` finds no session busy; ``cancel``:
that wait called off, PR-1.12c). Any request before hello
is refused ``sequence``.

**Flow control** (§3.2, §3.20). libsoup has no backpressure signal (F15),
so the client acks: an `ack {pty, offset}` event every `ACK_BYTES` fed to
its VTE or `ACK_MS` after the last unacked frame. A sink hands libsoup at
most `ACK_WINDOW` unacked live bytes and keeps the rest in its own pending
list, released as acks arrive; each ack is reported to
`PtyServer.drained`, whose own 4 MiB bound cuts a client that never acks
off and redraws it (the existing fallback). `drop_queued()` discards the
sink's pending list; what libsoup already took is sent. Output frames are
`pack_frame(TAG_OUTPUT, flags, pty, offset)` with `offset` the live bytes
sent to this client for this pty so far; a redraw's frames carry the same
counter and are neither counted nor acked.

Gio and libsoup only; nothing here imports GTK.
"""

from __future__ import annotations

import errno
import logging
import os
import secrets
import socket
import stat
from collections import deque
from collections.abc import Callable

import gi

gi.require_version("Soup", "3.0")
from gi.repository import Gio, GLib, Soup  # noqa: E402

from .. import __version__, mcptools, sandboxplan
from . import protocol

log = logging.getLogger(__name__)

WS_PATH = "/api/ws"
# Plain HTTP on the same listener (§3.2): a blob's bytes, never a path.
BLOB_PATH = "/api/blob"
# The header a blob GET names its client by (the hello's client_id), so
# the `local` capability's confinement applies to it (gitfeed.blob).
CLIENT_HEADER = "Collins-Client"
SOCKET_NAME = "api.sock"
PROOF_NAME = "local-proof"
HELLO_TIMEOUT_S = 5  # a connection with no hello by then is closed


# ---- paths -----------------------------------------------------------------------


# A Unix socket's path is bounded by the kernel (sun_path, 108 bytes with
# the NUL): what the fallback below must stay under.
SOCKET_PATH_MAX = 100


def runtime_dir(app_id: str) -> str:
    """The directory ``api.sock`` and ``local-proof`` live in: the per-id
    runtime directory beside ``mcp.sock`` when ``$XDG_RUNTIME_DIR`` is set
    (user-private, short paths), else Collins' own state directory, which
    no sandbox box carries and no other user reads (never the shared temp
    directory: §3.2). A state directory deep enough to push the socket's
    path past the kernel's bound (an e2e check's scratch tree under a long
    app id) falls back to a user-private, id-hashed directory under the
    temp directory, 0700 like the rest."""
    if os.environ.get("XDG_RUNTIME_DIR"):
        directory = mcptools.runtime_dir(app_id)
    else:
        directory = os.path.join(sandboxplan.state_base(), "collins", app_id)
    if len(os.path.join(directory, SOCKET_NAME).encode()) <= SOCKET_PATH_MAX:
        return directory
    return _short_runtime_dir(directory)


def _short_runtime_dir(intended: str) -> str:
    """A stand-in for *intended* when its socket path would pass the
    kernel's bound: ``<tmp>/collins-<uid>/<sha1(intended)[:16]>``. The
    hash is of the whole intended directory, so two runtime trees with
    one app id never share a stand-in. The parent lives in the shared
    temp directory, so it is made 0700 and then checked: a directory
    somebody else pre-created there is refused (OSError), never used."""
    import hashlib
    import stat
    import tempfile

    parent = os.path.join(tempfile.gettempdir(), f"collins-{os.getuid()}")
    try:
        os.mkdir(parent, 0o700)
    except FileExistsError:
        pass
    info = os.lstat(parent)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise OSError(errno.EACCES, f"{parent} is not a private directory of this user")
    digest = hashlib.sha1(intended.encode()).hexdigest()[:16]
    return os.path.join(parent, digest)


def lock_path(app_id: str) -> str:
    """The single-instance lock a service holds for its life (`service.main`)."""
    return os.path.join(runtime_dir(app_id), "service.lock")


def socket_path(app_id: str) -> str:
    return os.path.join(runtime_dir(app_id), SOCKET_NAME)


def proof_path(app_id: str) -> str:
    return os.path.join(runtime_dir(app_id), PROOF_NAME)


def probe_socket(path: str, timeout: float = 1.0) -> str:
    """What is at *path*: ``"live"`` (a service answers), ``"stale"`` (a
    socket file nobody listens on) or ``"none"`` (no file)."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return "none"
    if not stat.S_ISSOCK(mode):
        return "stale"
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(timeout)
    try:
        probe.connect(path)
    except OSError as exc:
        if exc.errno in (errno.ECONNREFUSED, errno.ENOENT):
            return "stale"
        # Anything else (a timeout, a reset, EACCES) is a listener in some
        # state, not an absence: never unlink over it.
        log.debug("probing %s: %s; taken as live", path, exc)
        return "live"
    else:
        return "live"
    finally:
        probe.close()


# ---- the sink -------------------------------------------------------------------


class _Sink:
    """One client's view of one pty, as the pty server sees it (its sink
    protocol: `send_output`, `send_event`, `drop_queued`, `device`, `term`)
    with the ack window of §3.20 in front of libsoup's unbounded queue."""

    __slots__ = ("client", "pty", "sent", "acked", "pending", "pending_bytes")

    def __init__(self, client: SocketClient, pty: int) -> None:
        self.client = client
        self.pty = pty
        self.sent = 0  # live bytes handed to libsoup for this pty
        self.acked = 0  # live bytes the client said it fed
        self.pending: deque[bytes] = deque()  # live frames waiting on the window
        self.pending_bytes = 0

    @property
    def device(self) -> str:
        return self.client.device

    @property
    def term(self) -> dict:
        return self.client.term

    def send_output(self, data: bytes, flags: int) -> None:
        if flags:
            # A redraw's frames: outside the window and never acked.
            frame = protocol.pack_frame(protocol.TAG_OUTPUT, flags, self.pty, self.sent, data)
            self.client.send_binary(frame)
            return
        if not data:
            return
        if self.pending or self.sent - self.acked >= protocol.ACK_WINDOW:
            self.pending.append(bytes(data))
            self.pending_bytes += len(data)
            return
        self._emit(data)

    def _emit(self, data: bytes) -> None:
        frame = protocol.pack_header(protocol.TAG_OUTPUT, 0, self.pty, self.sent)
        self.sent += len(data)
        self.client.send_binary(frame + bytes(data))

    def send_event(self, event: dict) -> None:
        self.client.deliver(event)

    def drop_queued(self) -> None:
        """Discard what of this pty is still in the sink's own list; what
        libsoup already took is sent. The redraw that follows supersedes
        it. `acked` stays where it is: what libsoup holds is still unacked,
        and moving the mark would open a fresh window on top of it (one
        window per fallback cycle until the pong timeout cut the client)."""
        self.pending.clear()
        self.pending_bytes = 0

    def ack(self, offset: int) -> int:
        """The client fed live output up to *offset*: release what the
        window now allows and return how many bytes the ack covered."""
        if offset <= self.acked:
            return 0
        covered = min(offset, self.sent) - self.acked
        self.acked = max(self.acked, min(offset, self.sent))
        while self.pending and self.sent - self.acked < protocol.ACK_WINDOW:
            data = self.pending.popleft()
            self.pending_bytes -= len(data)
            self._emit(data)
        return covered


# ---- the client ------------------------------------------------------------------


class SocketClient:
    """One client of the service: every connection with one ``client_id``.
    The core's `Client` protocol; see the module docstring."""

    def __init__(self, server: ApiServer, client_id: str) -> None:
        self.server = server
        self.client_id = client_id
        self.device = ""
        self.locale = ""
        self.term: dict = {}
        self.local = False
        self.primary: Connection | None = None
        self.sync: Connection | None = None
        self._sinks: dict[int, _Sink] = {}
        self.gone = False

    # -- the core's Client protocol

    def sink_for(self, pty: int) -> _Sink:
        sink = self._sinks.get(pty)
        if sink is None:
            sink = self._sinks[pty] = _Sink(self, pty)
        return sink

    def forget(self, pty: int) -> None:
        self._sinks.pop(pty, None)

    def deliver(self, event: dict) -> None:
        """An event for this client: validated, then sent down the primary
        (never the sync channel, D26)."""
        if self.gone or self.primary is None:
            return
        checked = protocol.validate(dict(event), protocol.SERVICE)
        if isinstance(checked, protocol.Refusal):
            log.error("api: the service sent an event the protocol refuses: %s", checked.msgid)
            return
        if checked.type == "pty-exited":
            self.forget(int(checked.get("pty")))
        self.primary.send_text(event)

    # -- what the connections ask

    def send_binary(self, frame: bytes) -> None:
        if not self.gone and self.primary is not None:
            self.primary.send_binary(frame)

    def ack(self, pty: int, offset: int) -> None:
        sink = self._sinks.get(pty)
        if sink is None:
            return
        covered = sink.ack(offset)
        if covered > 0:
            self.server.core.ptys.drained(pty, sink, covered)

    def connection_gone(self, connection: Connection) -> None:
        if connection is self.sync:
            self.sync = None
            return
        if connection is not self.primary:
            return
        self.primary = None
        self.end()

    def end(self) -> None:
        """The client is gone however it went (§3.1 rule 5)."""
        if self.gone:
            return
        self.gone = True
        if self.sync is not None:
            self.sync.close()
            self.sync = None
        if self.primary is not None:
            self.primary.close()
            self.primary = None
        try:
            self.server.core.client_gone(self)
        except Exception:
            log.exception("api: client_gone failed for %s", self.client_id)
        self._sinks.clear()
        self.server.clients.pop(self.client_id, None)


# ---- a connection -----------------------------------------------------------------


class Connection:
    """One WebSocket. Before its hello it belongs to no client."""

    def __init__(self, server: ApiServer, ws: Soup.WebsocketConnection) -> None:
        self.server = server
        self.ws = ws
        self.client: SocketClient | None = None
        self.channel = protocol.CHANNEL_PRIMARY
        self.closed = False
        ws.set_max_incoming_payload_size(protocol.MAX_INCOMING)
        ws.set_keepalive_interval(protocol.KEEPALIVE_INTERVAL_S)
        try:
            ws.set_keepalive_pong_timeout(protocol.KEEPALIVE_PONG_TIMEOUT_S)
        except AttributeError:  # libsoup before 3.6
            log.warning("api: this libsoup has no keepalive pong timeout; a dead client lingers")
        ws.connect("message", self._on_message)
        ws.connect("error", self._on_error)
        ws.connect("closed", self._on_closed)
        # A connection that never says hello is closed after HELLO_TIMEOUT_S.
        self._hello_source = GLib.timeout_add(HELLO_TIMEOUT_S * 1000, self._hello_overdue)

    def _hello_overdue(self) -> bool:
        self._hello_source = 0
        if self.client is None and not self.closed:
            log.info("api: a connection said no hello in %d s; closing it", HELLO_TIMEOUT_S)
            self.close()
        return GLib.SOURCE_REMOVE

    # -- sending

    def send_text(self, message: dict) -> None:
        if self.closed or self.ws.get_state() != Soup.WebsocketState.OPEN:
            return
        try:
            self.ws.send_text(protocol.encode(message))
        except (ValueError, GLib.Error) as exc:
            log.error("api: could not send %s: %s", message.get("t") or message.get("re"), exc)

    def send_binary(self, frame: bytes) -> None:
        if self.closed or self.ws.get_state() != Soup.WebsocketState.OPEN:
            return
        try:
            self.ws.send_message(Soup.WebsocketDataType.BINARY, GLib.Bytes.new(frame))
        except GLib.Error as exc:
            log.error("api: could not send a binary frame: %s", exc)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if getattr(self, "_hello_source", 0):
            GLib.source_remove(self._hello_source)
            self._hello_source = 0
        if self.ws.get_state() == Soup.WebsocketState.OPEN:
            try:
                self.ws.close(Soup.WebsocketCloseCode.NORMAL, None)
            except GLib.Error:
                pass

    # -- receiving

    def _on_message(self, _ws, kind, data: GLib.Bytes) -> None:
        payload = data.get_data() or b""
        try:
            if kind == Soup.WebsocketDataType.TEXT:
                self._on_text(payload)
            else:
                self._on_binary(payload)
        except Exception:
            log.exception("api: a frame's handling failed")

    def _on_text(self, payload: bytes) -> None:
        try:
            message = protocol.decode(payload)
        except ValueError as exc:
            log.warning("api: a frame the protocol can't read: %s", exc)
            return
        checked = protocol.validate(message, protocol.CLIENT)
        if isinstance(checked, protocol.Refusal):
            reply = checked.to_message()
            if reply is not None:
                self.send_text(reply)
            return
        if checked.kind == protocol.REQUEST:
            answer = self._answer(checked)
            if isinstance(answer, protocol.Deferred):
                # A handler that answers off the main loop (PR-2.1): the
                # reply is sent when it settles, on this connection, as
                # any other. `then` runs at once for one already settled.
                answer.then(lambda raw: self.send_reply(self._checked(checked, raw)))
            else:
                self.send_reply(answer)
        else:
            self._event(checked)

    def send_reply(self, reply: dict) -> None:
        """A response, split into TAG_BLOB frames and a slim reply when it
        would not fit a frame (`protocol.split_reply`: git's stdout)."""
        try:
            frames, slim = protocol.split_reply(reply)
        except ValueError as exc:
            log.error("api: the reply to %s cannot be sent: %s", reply.get("re"), exc)
            re_id = reply.get("re")
            if isinstance(re_id, int):
                self.send_text(
                    protocol.refuse(re_id, protocol.ERROR_FAILED, "The service's reply was too large to send")
                )
            return
        for frame in frames:
            self.send_binary(frame)
        self.send_text(slim)

    def _answer(self, message: protocol.Message) -> dict | protocol.Deferred:
        who = self.client.client_id if self.client else "?"
        log.debug("api: %s on %s from %s", message.type, self.channel, who)
        if message.type == "hello":
            return self.server.hello(self, message)
        if self.client is None:
            return protocol.refuse(
                message.id, protocol.ERROR_SEQUENCE, "{type} before hello", {"type": message.type}
            )
        if message.type == "local":
            return self.server.local(self.client, message)
        if message.type == "service.restart":
            return self.server.restart(self.client, message)
        raw = self.server.core.handle(message, self.client)
        if isinstance(raw, protocol.Deferred):
            return raw
        return self._checked(message, raw)

    def _checked(self, message: protocol.Message, raw: dict) -> dict:
        """*raw*, the core's reply to *message*, validated against the
        type's reply shape (a reply that does not fit is the service's bug,
        answered `failed` and logged)."""
        answer = protocol.validate_response(raw, message.type)
        if isinstance(answer, protocol.Refusal):
            log.error("api: the reply to %s does not validate: %s", message.type, answer.msgid)
            return protocol.refuse(
                message.id, protocol.ERROR_FAILED, "{type}: the service's reply was not valid",
                {"type": message.type},
            )
        if message.type == "service.status" and answer.ok:
            raw = dict(raw)
            raw["clients"] = len(self.server.clients)
        return raw

    def _event(self, event: protocol.Message) -> None:
        if self.client is None:
            return  # an event before hello: dropped, nobody to answer
        if event.type == "ack":
            self.client.ack(int(event.get("pty")), int(event.get("offset")))
            return
        self.server.core.deliver(event, self.client)

    def _on_binary(self, payload: bytes) -> None:
        if self.client is None:
            return
        try:
            header, data = protocol.unpack_frame(payload)
        except ValueError as exc:
            log.warning("api: a binary frame the protocol can't read: %s", exc)
            return
        refusal = protocol.check_frame(header, protocol.CLIENT)
        if refusal is not None:
            log.warning("api: %s", refusal.msgid.format_map(refusal.args))
            return
        if header.tag == protocol.TAG_INPUT and data:
            self.server.core.input(header.stream, data, self.client)

    def _on_error(self, _ws, error: GLib.Error) -> None:
        log.info("api: a connection errored: %s", error.message)

    def _on_closed(self, _ws) -> None:
        self.closed = True
        if getattr(self, "_hello_source", 0):
            GLib.source_remove(self._hello_source)
            self._hello_source = 0
        self.server.connection_gone(self)


# ---- the server -----------------------------------------------------------------


class ApiServer:
    """See the module docstring. *core* is the `ServiceCore`; *on_restart*
    is called with the `service.restart` request's ``when`` and the asking
    client once the reply has been sent (an idle later)."""

    def __init__(
        self,
        core,
        app_id: str,
        *,
        debug: bool = False,
        on_restart: Callable[[str], None] | None = None,
        version: str = __version__,
    ) -> None:
        self.core = core
        self.app_id = app_id
        self.debug = debug
        self.on_restart = on_restart
        self.version = version
        self.path = socket_path(app_id)
        self.proof_file = proof_path(app_id)
        self.proof = b""
        self.server: Soup.Server | None = None
        self.clients: dict[str, SocketClient] = {}
        self.connections: set[Connection] = set()
        self.accepting = False

    # -- lifecycle

    def listen(self) -> str:
        """Listen on the socket, write the proof. Returns the socket's path.
        Raises `OSError` for a live socket (another service) and
        `GLib.Error` when libsoup cannot listen."""
        if len(os.fsencode(self.path)) > 107:
            # sun_path is 108 bytes with the NUL; Gio truncates silently.
            raise OSError(errno.ENAMETOOLONG, f"socket path too long for AF_UNIX: {self.path}")
        directory = os.path.dirname(self.path)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)
        found = probe_socket(self.path)
        if found == "live":
            raise OSError(errno.EADDRINUSE, f"a service is already listening on {self.path}")
        if found == "stale":
            os.unlink(self.path)
        self.server = Soup.Server()
        self.server.add_websocket_handler(WS_PATH, None, None, self._on_websocket)
        self.server.add_handler(BLOB_PATH, self._on_blob)
        self.server.listen(Gio.UnixSocketAddress.new(self.path), Soup.ServerListenOptions(0))
        os.chmod(self.path, 0o600)  # F7: created 0775 under the default umask
        self.proof = secrets.token_bytes(protocol.LOCAL_PROOF_BYTES)
        self._write_proof()
        self.accepting = True
        return self.path

    def _write_proof(self) -> None:
        tmp = self.proof_file + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, self.proof)
        finally:
            os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.proof_file)

    def stop_accepting(self) -> None:
        """Stop taking new connections (the socket file goes), keep the
        open ones until `stop`."""
        self.accepting = False
        if self.server is not None:
            self.server.disconnect()
        for path in (self.path, self.proof_file):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                log.warning("api: could not remove %s: %s", path, exc)

    def stop(self) -> None:
        """Close every connection and the listener."""
        self.stop_accepting()
        for client in list(self.clients.values()):
            client.end()
        for connection in list(self.connections):
            connection.close()
        self.server = None

    # -- connections

    def _on_blob(self, _server, msg: Soup.ServerMessage, _path, query, *_rest) -> None:
        """`GET /api/blob?kind=git&…` (§3.11, §3.23): the blob's bytes with
        its ETag, answered off the main loop — the message is paused while
        the service's thread reads it, and unpaused with the answer. The
        request names its client with the ``Collins-Client`` header (the
        hello's client_id, for the `local` capability's confinement); a
        request with none, or one of a client the server does not know,
        is confined like a remote client's. ``kind`` other than ``git``
        is 404 until the later chunks serve it."""
        if not self.accepting or msg.get_method() != "GET":
            msg.set_status(405, None)
            return
        params = dict(query or {})
        # The header is taken at its word, like the hello's client_id: an
        # id is a grouping key, not a credential (§3.16 — whoever reaches
        # this 0600 socket is the service's own uid and already trusted
        # with a shell). Naming a `local` client's id widens a GET to any
        # path that client could read itself; it proves nothing to a peer
        # who could not already reach the socket. Phase 4's token, when a
        # listener is not a Unix socket, is what makes this a credential.
        client_id = msg.get_request_headers().get_one(CLIENT_HEADER) or ""
        client = self.clients.get(client_id)
        if params.get("kind") != "git":
            msg.set_status(404, None)
            return
        feed = getattr(self.core, "git", None)
        if feed is None:
            msg.set_status(404, None)
            return
        if_none_match = msg.get_request_headers().get_one("If-None-Match")
        uri = msg.get_uri()
        raw_query = uri.get_query() if uri is not None else ""
        msg.pause()

        def respond(status: int, headers: dict, body: bytes) -> None:
            try:
                response = msg.get_response_headers()
                for name, value in headers.items():
                    response.replace(name, value)
                if body:
                    msg.set_response(headers.get("Content-Type") or "application/octet-stream",
                                     Soup.MemoryUse.COPY, body)
                msg.set_status(status, None)
            finally:
                msg.unpause()

        feed.blob(client, raw_query or "", if_none_match, respond)

    def _on_websocket(self, _server, _msg, _path, ws, *_rest) -> None:
        if not self.accepting:
            try:
                ws.close(Soup.WebsocketCloseCode.GOING_AWAY, None)
            except GLib.Error:
                pass
            return
        connection = Connection(self, ws)
        self.connections.add(connection)

    def connection_gone(self, connection: Connection) -> None:
        self.connections.discard(connection)
        if connection.client is not None:
            connection.client.connection_gone(connection)

    # -- the requests the server serves itself

    def hello(self, connection: Connection, message: protocol.Message) -> dict:
        agreed = protocol.negotiate(
            protocol.PROTOCOL,
            protocol.MIN_PROTOCOL,
            int(message.get("protocol")),
            message.get("min_protocol"),
        )
        if agreed is None:
            GLib.idle_add(connection.close, priority=GLib.PRIORITY_DEFAULT)
            return protocol.refuse(
                message.id,
                protocol.ERROR_PROTOCOL,
                "This Collins speaks protocol {client} and the service speaks {service}",
                {"client": int(message.get("protocol")), "service": protocol.PROTOCOL},
            )
        if connection.client is not None:
            return protocol.refuse(message.id, protocol.ERROR_SEQUENCE, "hello was already said")
        client_id = str(message.get("client_id"))
        channel = message.get("channel") or protocol.CHANNEL_PRIMARY
        client = self.clients.get(client_id)
        if channel == protocol.CHANNEL_SYNC:
            if client is None or client.primary is None:
                return protocol.refuse(
                    message.id, protocol.ERROR_SEQUENCE, "a sync channel needs its primary connection first"
                )
            if client.sync is not None:
                client.sync.close()
            client.sync = connection
        else:
            if client is not None and client.primary is not None:
                # The same client_id again (a reconnect whose old connection
                # has not yet been seen to close): the newcomer takes the id
                # over, by design. Every client that reaches this socket is
                # already trusted with a shell (same uid, §3.2), so an id is
                # a grouping key, not a credential.
                old = client
                old.end()
            client = SocketClient(self, client_id)
            client.primary = connection
            client.device = str(message.get("device") or "")[: protocol.HOST_MAX]
            client.locale = str(message.get("locale") or "")
            client.term = dict(message.get("term") or {})
            self.clients[client_id] = client
            self.core.client_connected(client)
        connection.client = client
        connection.channel = channel
        caps = [protocol.CAP_LOCAL]
        if self.debug:
            caps.append(protocol.CAP_DEBUG)
        state = getattr(self.core, "state", None)
        service_id = str(getattr(state, "service_id", "") or "") or "service"
        return protocol.reply(
            message.id,
            protocol=agreed,
            min_protocol=protocol.MIN_PROTOCOL,
            version=self.version,
            service_id=service_id,
            host=socket.gethostname()[: protocol.HOST_MAX],
            caps=caps,
            local_proof={"path": self.proof_file, "length": len(self.proof)},
        )

    def local(self, client: SocketClient, message: protocol.Message) -> dict:
        proof = str(message.get("proof") or "")
        if self.proof and secrets.compare_digest(proof, self.proof.hex()):
            client.local = True
            return protocol.reply(message.id)
        return protocol.refuse(message.id, protocol.ERROR_REFUSED, "The proof does not match this service's")

    def restart(self, client: SocketClient, message: protocol.Message) -> dict:
        when = str(message.get("when"))
        if self.on_restart is None:
            return protocol.refuse(message.id, protocol.ERROR_REFUSED, "This service cannot restart itself")
        if when == "cancel":
            # A restart waiting for the sessions to be idle, called off
            # (a no-op when none waits).
            self.core.cancel_restart()
            return protocol.reply(message.id)
        if when == "idle":
            # The service does the waiting (§3.21): the core polls the
            # tracker's busy count and restarts once it reads 0.
            self.core.restart_when_idle(lambda: self._restart_later("idle"))
            return protocol.reply(message.id)
        self.core.cancel_restart()
        GLib.idle_add(self._restart_later, when, priority=GLib.PRIORITY_DEFAULT)
        return protocol.reply(message.id)

    def _restart_later(self, when: str) -> bool:
        try:
            self.on_restart(when)
        except Exception:
            log.exception("api: the restart failed")
        return GLib.SOURCE_REMOVE
