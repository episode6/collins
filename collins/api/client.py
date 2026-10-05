# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The client's end of the API: `SocketLink`, an `apilink.Link` over the
service's Unix socket.

Split-service spec §3.20, D26. One `SocketLink` is one client of one
service, with **two WebSockets** on a `Soup.Session` over
`Gio.UnixSocketAddress`:

- the **primary** carries the `hello`, the `subscribe`, every event the
  service sends (the subscription's, the ptys', the sessions', `tool`,
  `job`, ...), every output frame, every input frame, every client event
  (`resize`, `focus`, `theme`, `ack`, `tool-reply`) and the requests made
  with `send`, whose replies land in callbacks on the main loop;
- the **sync channel** (its hello carrying the same ``client_id`` and
  ``channel: "sync"``) answers `call`: the calling thread, the main thread
  included, blocks on a `threading.Event` with a 10 s timeout (``gone``
  after it). Nothing the service sends unasked ever comes down it, so a
  main-thread `call` never has to run the main loop to see its reply, and
  the eighteen synchronous sites of F18 keep their shape.

Both connections are driven by one **daemon thread** running its own
`GLib.MainContext` (libsoup's objects live there and are only touched
there: every send is posted to it). What arrives is handed to the main
loop through one ordered landing queue, drained at `PRIORITY_DEFAULT`
(CI's Xvfb starves the idle priority), so events, output frames and the
replies of `send` reach the mirrors and the terminals in the order the
service sent them. The one request `call` sends on the primary is
`subscribe`: its snapshot arrives as events ahead of its reply on the same
connection, and the main thread drains the queue while it waits, so the
mirrors are filled when `subscribe` returns, as they were on the loopback
(the connection manager asks for it at startup and on every reconnect, and
nowhere else does a main-thread call drain).

**Flow control** (§3.2): every live output frame fed is counted per pty,
and an `ack {pty, offset}` goes back every `ACK_BYTES` or `ACK_MS` after
the last unacked frame, whichever first; the service holds its window on
them (`api.server`). A redraw's frames are not acked.

**Lost** (§3.2, F15): both connections set the keepalives; the
connection's `error` signal, or any `closed` the link did not ask for,
makes the link lost: every pending call fails ``gone``, the other channel
is closed, and `on_lost` is called once on the main loop. The link keeps
its handlers and its `PtyClient`s and can `connect` again
(`connection.ConnectionManager` does, with its backoff); a `PtyClient`
is what a tab or a panel shell holds in place of Phase 1's loopback
client: `request`, `send_input`, `send_event`, `close`, `closed`, with
output frames and events routed to it by the ptys it spawned or attached.

Gio and libsoup only; nothing here imports GTK. Every frame is foreign
content (rule 5): decoded and validated on its way in, dropped when it
does not fit.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
import uuid
from collections.abc import Callable

import gi

gi.require_version("Soup", "3.0")
from gi.repository import Gio, GLib, Soup  # noqa: E402

from .. import __version__, apilink
from . import protocol
from .protocol import RequestRefused

log = logging.getLogger(__name__)

WS_URL = "ws://collins" + "/api/ws"
CALL_TIMEOUT_S = 10.0
CONNECT_TIMEOUT_S = 10.0

OutputCallback = Callable[[int, bytes, int], None]
EventCallback = Callable[[dict], None]


class ConnectionLost(Exception):
    """The connection could not be made or was lost; `str` says why."""


def new_client_id() -> str:
    return str(uuid.uuid4())


class _Pending:
    """A request waiting on its reply: a blocking waiter (`event`) or a
    pair of callbacks landed on the main loop."""

    __slots__ = ("type", "event", "result", "on_reply", "on_refused", "land")

    def __init__(self, type_: str, *, event=None, on_reply=None, on_refused=None, land=True) -> None:
        self.type = type_
        self.event = event
        self.result = None
        self.on_reply = on_reply
        self.on_refused = on_refused
        self.land = land


class _Channel:
    """One WebSocket and its pending requests (requests are answered on the
    connection that asked)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.ws: Soup.WebsocketConnection | None = None
        self.pending: dict[int, _Pending] = {}
        self.closing = False  # the link asked for the close

    @property
    def open(self) -> bool:
        return self.ws is not None and self.ws.get_state() == Soup.WebsocketState.OPEN


class SocketLink(apilink.Link):
    """See the module docstring. *on_lost* is called on the main loop once
    per loss; *on_output* hears every output frame that no `PtyClient`
    claimed (a test's recorder; None drops them)."""

    def __init__(
        self,
        path: str,
        *,
        client_id: str | None = None,
        device: str = "",
        locale: str = "",
        term: dict | None = None,
        version: str = __version__,
        on_lost: Callable[[str], None] | None = None,
        on_output: OutputCallback | None = None,
    ) -> None:
        super().__init__()
        self.path = path
        self.client_id = client_id or new_client_id()
        self.device = device[: protocol.HOST_MAX]
        self.locale = locale
        self.term = dict(term or {})
        self.version = version
        self.on_lost = on_lost
        self._on_output = on_output
        self.hello: dict = {}
        self.local = False
        self.connected = False
        self._lock = threading.Lock()
        self._next_id = 1
        self._primary = _Channel(protocol.CHANNEL_PRIMARY)
        self._sync = _Channel(protocol.CHANNEL_SYNC)
        self._session: Soup.Session | None = None
        self._pty_clients: list[PtyClient] = []
        # Flow control: live bytes fed per pty, and what of them is acked.
        self._fed: dict[int, int] = {}
        self._acked: dict[int, int] = {}
        self._ack_source = 0
        # The landing queue: what the I/O thread hands the main loop.
        self._landings: queue.SimpleQueue = queue.SimpleQueue()
        self._drain_scheduled = False
        self._lost_reported = False
        # The I/O thread and its context.
        self._context = GLib.MainContext.new()
        self._loop = GLib.MainLoop.new(self._context, False)
        self._thread = threading.Thread(target=self._run, name="collins-api", daemon=True)
        self._thread.start()

    # -- the I/O thread ----------------------------------------------------------------

    def _run(self) -> None:
        self._context.push_thread_default()
        try:
            self._loop.run()
        finally:
            self._context.pop_thread_default()

    def _post(self, fn: Callable[[], None]) -> None:
        """Run *fn* on the I/O thread."""
        source = GLib.idle_source_new()
        source.set_priority(GLib.PRIORITY_DEFAULT)

        def run(*_args):
            try:
                fn()
            except Exception:
                log.exception("api client: a posted call failed")
            return GLib.SOURCE_REMOVE

        source.set_callback(run)
        source.attach(self._context)

    def _on_io_thread(self) -> bool:
        return threading.current_thread() is self._thread

    # -- landing on the main loop ------------------------------------------------------

    def _land(self, fn: Callable[[], None]) -> None:
        self._landings.put(fn)
        with self._lock:
            if self._drain_scheduled:
                return
            self._drain_scheduled = True
        GLib.idle_add(self._drain, priority=GLib.PRIORITY_DEFAULT)

    def _drain(self) -> bool:
        with self._lock:
            self._drain_scheduled = False
        while True:
            try:
                fn = self._landings.get_nowait()
            except queue.Empty:
                return GLib.SOURCE_REMOVE
            try:
                fn()
            except Exception:
                log.exception("api client: a landing failed")

    def _drain_until(self, done: Callable[[], bool], timeout: float) -> bool:
        """The main thread runs landings as they come until *done*."""
        deadline = time.monotonic() + timeout
        while not done():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                fn = self._landings.get(timeout=min(remaining, 0.25))
            except queue.Empty:
                continue
            try:
                fn()
            except Exception:
                log.exception("api client: a landing failed")
        return True

    # -- connecting --------------------------------------------------------------------

    def connect(self, timeout: float = CONNECT_TIMEOUT_S) -> dict:
        """Open both channels and say hello on each (blocking, from any
        thread but the I/O thread). Returns the service's hello reply.
        Raises `ConnectionLost` when the socket does not answer, the
        protocol windows do not meet (the message names both versions) or
        the hello is refused."""
        if self._on_io_thread():
            raise RuntimeError("connect() cannot run on the link's own thread")
        self.close()
        self._lost_reported = False
        deadline = time.monotonic() + timeout
        self._session_reset()
        self._open(self._primary, deadline)
        hello = self._hello(self._primary, deadline)
        agreed = protocol.negotiate(
            protocol.PROTOCOL, protocol.MIN_PROTOCOL, int(hello["protocol"]), int(hello["min_protocol"])
        )
        if agreed is None:
            self.close()
            raise ConnectionLost(
                f"the service speaks protocol {hello['protocol']} and this Collins speaks {protocol.PROTOCOL}"
            )
        self._open(self._sync, deadline)
        self._hello(self._sync, deadline)
        self.hello = hello
        self.connected = True
        return hello

    def _session_reset(self) -> None:
        done = threading.Event()

        def make():
            self._session = Soup.Session(remote_connectable=Gio.UnixSocketAddress.new(self.path))
            done.set()

        self._post(make)
        done.wait(CONNECT_TIMEOUT_S)

    def _open(self, channel: _Channel, deadline: float) -> None:
        done = threading.Event()
        box: dict = {}

        def finished(session, result):
            try:
                box["ws"] = session.websocket_connect_finish(result)
            except GLib.Error as err:
                box["error"] = err.message
            done.set()

        def start():
            message = Soup.Message.new("GET", WS_URL)
            self._session.websocket_connect_async(message, None, None, GLib.PRIORITY_DEFAULT, None, finished)

        self._post(start)
        if not done.wait(max(0.0, deadline - time.monotonic())) or "ws" not in box:
            self.close()
            raise ConnectionLost(box.get("error") or f"no answer from {self.path}")
        ws = box["ws"]
        channel.ws = ws
        channel.closing = False

        def wire():
            ws.set_max_incoming_payload_size(protocol.MAX_INCOMING)
            ws.set_keepalive_interval(protocol.KEEPALIVE_INTERVAL_S)
            try:
                ws.set_keepalive_pong_timeout(protocol.KEEPALIVE_PONG_TIMEOUT_S)
            except AttributeError:  # libsoup before 3.6
                log.warning("api client: this libsoup has no keepalive pong timeout")
            ws.connect("message", self._on_message, channel)
            ws.connect("error", self._on_error, channel)
            ws.connect("closed", self._on_closed, channel)
            done2.set()

        done2 = threading.Event()
        self._post(wire)
        if not done2.wait(max(0.0, deadline - time.monotonic())):
            self.close()
            raise ConnectionLost(f"the {channel.name} channel could not be wired in time")

    def _hello(self, channel: _Channel, deadline: float) -> dict:
        message = {
            "t": "hello",
            "protocol": protocol.PROTOCOL,
            "min_protocol": protocol.MIN_PROTOCOL,
            "version": self.version,
            "client_id": self.client_id,
            "device": self.device,
        }
        if self.locale:
            message["locale"] = self.locale[:64]
        if self.term:
            message["term"] = dict(self.term)
        if channel is self._sync:
            message["channel"] = protocol.CHANNEL_SYNC
        try:
            return self._blocking(channel, message, max(0.1, deadline - time.monotonic()))
        except RequestRefused as refusal:
            self.close()
            if refusal.error == protocol.ERROR_PROTOCOL:
                raise ConnectionLost(refusal.msgid.format_map(refusal.details)) from None
            raise ConnectionLost(f"hello refused: {refusal.msgid}") from None

    def prove_local(self) -> bool:
        """Read the proof file the hello named from this client's own
        filesystem and send it (`local`). True when the service took it."""
        proof = (self.hello or {}).get("local_proof") or {}
        path = proof.get("path")
        if not path:
            return False
        try:
            with open(path, "rb") as fh:
                data = fh.read(protocol.LOCAL_PROOF_MAX + 1)
        except OSError:
            return False
        if not data or len(data) > protocol.LOCAL_PROOF_MAX:
            return False
        try:
            self.call({"t": "local", "proof": data.hex()})
        except RequestRefused:
            return False
        self.local = True
        return True

    def close(self) -> None:
        """Close both channels: asked for, so not a loss."""
        self.connected = False
        for channel in (self._primary, self._sync):
            with self._lock:
                ws = channel.ws
                channel.closing = True
                channel.ws = None
            if ws is None:
                continue

            def do_close(ws=ws):
                try:
                    if ws.get_state() == Soup.WebsocketState.OPEN:
                        ws.close(Soup.WebsocketCloseCode.NORMAL, None)
                except GLib.Error:
                    pass

            self._post(do_close)
        self._fail_pending(self._primary, "the connection was closed")
        self._fail_pending(self._sync, "the connection was closed")
        self._cancel_ack_timer()

    def shutdown(self) -> None:
        """Close and stop the I/O thread (the link is finished with)."""
        self.close()
        self._post(self._loop.quit)

    # -- requests --------------------------------------------------------------------

    def _mint(self) -> int:
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
        return request_id

    def _check(self, message: dict) -> protocol.Message:
        checked = protocol.validate(message, protocol.CLIENT)
        if isinstance(checked, protocol.Refusal):
            text = checked.msgid.format_map(checked.args)
            log.error("api client: %s is not a valid message: %s", message.get("t"), text)
            raise RequestRefused(protocol.ERROR_INVALID, text, {})
        return checked

    def _send_text(self, channel: _Channel, message: dict) -> None:
        text = protocol.encode(message)

        def do_send():
            ws = channel.ws
            if ws is None or ws.get_state() != Soup.WebsocketState.OPEN:
                return
            try:
                ws.send_text(text)
            except GLib.Error as exc:
                log.error("api client: send failed: %s", exc)

        self._post(do_send)

    def _blocking(self, channel: _Channel, message: dict, timeout: float) -> dict:
        """Send on *channel* and wait for the reply (any thread but the I/O
        one)."""
        if self._on_io_thread():
            raise RuntimeError("a blocking call cannot run on the link's own thread")
        message = dict(message)
        message["id"] = self._mint()
        checked = self._check(message)
        if checked.kind != protocol.REQUEST:
            raise RequestRefused(protocol.ERROR_INVALID, f"{checked.type} is an event, not a request", {})
        pending = _Pending(checked.type, event=threading.Event())
        with self._lock:
            if channel.ws is None:
                raise RequestRefused(protocol.ERROR_GONE, "Not connected to the service", {})
            channel.pending[message["id"]] = pending
        self._send_text(channel, message)
        if not pending.event.wait(timeout):
            with self._lock:
                channel.pending.pop(message["id"], None)
            raise RequestRefused(protocol.ERROR_GONE, "The service did not answer in time", {})
        return self._settle(pending)

    @staticmethod
    def _settle(pending: _Pending) -> dict:
        kind, value = pending.result
        if kind == "ok":
            return value
        raise value

    def _request(self, message: dict) -> dict:
        """`Link.call`: the sync channel, blocking; `subscribe` on the
        primary with the main thread draining (see the module docstring)."""
        if not self.connected:
            raise RequestRefused(protocol.ERROR_GONE, "Not connected to the service", {})
        if message.get("t") == "subscribe" and threading.current_thread() is threading.main_thread():
            return self._primary_blocking(message, CALL_TIMEOUT_S)
        return self._blocking(self._sync, message, CALL_TIMEOUT_S)

    def _primary_blocking(self, message: dict, timeout: float) -> dict:
        done: list = []
        self.send(message, lambda fields: done.append(("ok", fields)), lambda r: done.append(("err", r)))
        if not self._drain_until(lambda: bool(done), timeout):
            raise RequestRefused(protocol.ERROR_GONE, "The service did not answer in time", {})
        kind, value = done[0]
        if kind == "ok":
            return value
        raise value

    def send(self, message: dict, on_reply=None, on_refused=None) -> None:
        """A request on the primary; its reply lands on the main loop."""
        message = dict(message)
        message["id"] = self._mint()
        try:
            checked = self._check(message)
        except RequestRefused as refusal:
            if on_refused is not None:
                on_refused(refusal)
            return
        if checked.kind != protocol.REQUEST:
            if on_refused is not None:
                on_refused(RequestRefused(protocol.ERROR_INVALID, f"{checked.type} is an event", {}))
            return
        pending = _Pending(checked.type, on_reply=on_reply, on_refused=on_refused)
        with self._lock:
            if not self.connected or self._primary.ws is None:
                refusal = RequestRefused(protocol.ERROR_GONE, "Not connected to the service", {})
                if on_refused is not None:
                    on_refused(refusal)
                else:
                    log.warning("api client: %s refused: not connected", checked.type)
                return
            self._primary.pending[message["id"]] = pending
        self._send_text(self._primary, message)

    def send_event(self, message: dict) -> None:
        """A client event on the primary (`resize`, `focus`, `theme`,
        `tool-reply`, `ack`)."""
        if not self.connected:
            return
        try:
            self._check(message)
        except RequestRefused:
            return
        self._send_text(self._primary, dict(message))

    def send_input(self, pty: int, data: bytes) -> None:
        """A `0x02` frame on the primary."""
        if not self.connected or not data:
            return
        try:
            frame = protocol.pack_frame(protocol.TAG_INPUT, 0, pty, 0, bytes(data))
        except ValueError as exc:
            log.error("api client: an input frame the protocol refuses: %s", exc)
            return

        def do_send():
            ws = self._primary.ws
            if ws is None or ws.get_state() != Soup.WebsocketState.OPEN:
                return
            try:
                ws.send_message(Soup.WebsocketDataType.BINARY, GLib.Bytes.new(frame))
            except GLib.Error as exc:
                log.error("api client: send failed: %s", exc)

        self._post(do_send)

    # -- inbound (the I/O thread) -------------------------------------------------------

    def _on_message(self, ws, kind, data: GLib.Bytes, channel: _Channel) -> None:
        if ws is not channel.ws:
            return  # a connection this link has already let go of
        payload = data.get_data() or b""
        try:
            if kind == Soup.WebsocketDataType.TEXT:
                self._on_text(channel, payload)
            elif channel is self._primary:
                self._on_binary(payload)
        except Exception:
            log.exception("api client: a frame's handling failed")

    def _on_text(self, channel: _Channel, payload: bytes) -> None:
        try:
            message = protocol.decode(payload)
        except ValueError as exc:
            log.warning("api client: a frame the protocol can't read: %s", exc)
            return
        re_id = protocol.response_id(message)
        if re_id is not None:
            self._on_response(channel, re_id, message)
            return
        if channel is not self._primary:
            log.warning("api client: an event on the sync channel: %s", message.get("t"))
            return
        checked = protocol.validate(message, protocol.SERVICE)
        if isinstance(checked, protocol.Refusal):
            log.warning("api client: an event the protocol refuses: %s", checked.msgid)
            return
        if checked.kind != protocol.EVENT:
            return
        event = dict(message)
        self._land(lambda: self._dispatch_event(event))

    def _on_response(self, channel: _Channel, re_id: int, message: dict) -> None:
        with self._lock:
            pending = channel.pending.pop(re_id, None)
        if pending is None:
            return
        answer = protocol.validate_response(message, pending.type)
        if isinstance(answer, protocol.Refusal):
            result = ("err", RequestRefused(protocol.ERROR_INVALID, answer.msgid, dict(answer.args)))
        elif answer.ok:
            result = ("ok", dict(answer.fields))
        else:
            result = (
                "err",
                RequestRefused(answer.error or protocol.ERROR_FAILED, answer.msgid or "", dict(answer.args)),
            )
        self._complete(pending, result)

    def _complete(self, pending: _Pending, result) -> None:
        pending.result = result
        if pending.event is not None:
            pending.event.set()
            return

        def land():
            kind, value = pending.result
            if kind == "ok":
                if pending.on_reply is not None:
                    pending.on_reply(value)
            elif pending.on_refused is not None:
                pending.on_refused(value)
            else:
                log.warning("api client: %s refused: %s", pending.type, value.msgid)

        self._land(land)

    def _on_binary(self, payload: bytes) -> None:
        try:
            header, data = protocol.unpack_frame(payload)
        except ValueError as exc:
            log.warning("api client: a binary frame the protocol can't read: %s", exc)
            return
        if protocol.check_frame(header, protocol.SERVICE) is not None or header.tag != protocol.TAG_OUTPUT:
            return
        self._land(lambda: self._dispatch_output(header.stream, data, header.flags))

    def _on_error(self, ws, error: GLib.Error, channel: _Channel) -> None:
        if channel.closing or ws is not channel.ws:
            return
        self._lost(f"{channel.name}: {error.message}")

    def _on_closed(self, ws, channel: _Channel) -> None:
        # A close this link asked for, or the late close of a connection a
        # reconnect has already replaced, is not a loss.
        if channel.closing or ws is not channel.ws:
            return
        self._lost(f"{channel.name}: the service closed the connection")

    def _lost(self, reason: str) -> None:
        """The link is lost (any thread): fail everything, close the other
        channel, tell the owner once on the main loop."""
        with self._lock:
            if self._lost_reported:
                return
            self._lost_reported = True
        log.warning("api client: connection lost: %s", reason)
        self.close()
        if self.on_lost is not None:
            self._land(lambda: self.on_lost(reason))

    def _fail_pending(self, channel: _Channel, why: str) -> None:
        with self._lock:
            pending = list(channel.pending.values())
            channel.pending.clear()
        for entry in pending:
            self._complete(entry, ("err", RequestRefused(protocol.ERROR_GONE, why, {})))

    # -- dispatch (the main loop) -------------------------------------------------------

    def _dispatch_event(self, event: dict) -> None:
        pty = event.get("pty")
        if isinstance(pty, int):
            claimed = False
            for client in list(self._pty_clients):
                if pty in client.ptys:
                    claimed = True
                    client.on_event(event)
            if not claimed and event.get("t") in ("pty", "pty-exited"):
                for client in list(self._pty_clients):
                    client.on_event(event)
            if event.get("t") == "pty-exited":
                for client in self._pty_clients:
                    client.ptys.discard(pty)
                self._fed.pop(pty, None)
                self._acked.pop(pty, None)
        self.dispatch(event)

    def _dispatch_output(self, pty: int, data: bytes, flags: int) -> None:
        claimed = False
        for client in list(self._pty_clients):
            if pty in client.ptys:
                claimed = True
                client.on_output(pty, data, flags)
        if not claimed and self._on_output is not None:
            self._on_output(pty, data, flags)
        if flags == 0 and data:
            self._count_fed(pty, len(data))

    # -- acks -------------------------------------------------------------------------

    def _count_fed(self, pty: int, n: int) -> None:
        fed = self._fed.get(pty, 0) + n
        self._fed[pty] = fed
        if fed - self._acked.get(pty, 0) >= protocol.ACK_BYTES:
            self._ack(pty)
        elif not self._ack_source:
            self._ack_source = GLib.timeout_add(
                protocol.ACK_MS, self._ack_all, priority=GLib.PRIORITY_DEFAULT
            )

    def _ack(self, pty: int) -> None:
        fed = self._fed.get(pty, 0)
        if fed > self._acked.get(pty, 0):
            self._acked[pty] = fed
            self.send_event({"t": "ack", "pty": pty, "offset": fed})

    def _ack_all(self) -> bool:
        self._ack_source = 0
        for pty in list(self._fed):
            self._ack(pty)
        return GLib.SOURCE_REMOVE

    def _cancel_ack_timer(self) -> None:
        if self._ack_source:
            GLib.source_remove(self._ack_source)
            self._ack_source = 0
        self._fed.clear()
        self._acked.clear()

    # -- the per-tab clients -----------------------------------------------------------

    def pty_client(self, on_output: OutputCallback, on_event: EventCallback, device: str = "") -> PtyClient:
        """A client for one tab or panel shell: the surface Phase 1's
        loopback client had (`request`, `send_input`, `send_event`,
        `close`, `closed`), fed the output frames and events of the ptys it
        spawns or attaches."""
        client = PtyClient(self, on_output, on_event, device or self.device)
        self._pty_clients.append(client)
        return client

    def _forget_pty_client(self, client: PtyClient) -> None:
        if client in self._pty_clients:
            self._pty_clients.remove(client)


class PtyClient:
    """One tab's (or panel shell's) view of the link. Built by
    `SocketLink.pty_client`."""

    def __init__(
        self, link: SocketLink, on_output: OutputCallback, on_event: EventCallback, device: str
    ) -> None:
        self.link = link
        self.on_output = on_output
        self.on_event = on_event
        self.device = device
        self.ptys: set[int] = set()
        self.closed = False

    @property
    def term(self) -> dict:
        return self.link.term

    def request(self, message: dict) -> dict:
        """`Link.call`, with the ptys this client shows kept up to date
        (an `attach` or a `spawn` claims its pty; `detach` releases it)."""
        if self.closed:
            raise RequestRefused(protocol.ERROR_GONE, "the connection is closed", {})
        kind = message.get("t")
        if kind == "attach" and isinstance(message.get("pty"), int):
            # Claimed before the reply: the redraw's frames may land first.
            self.ptys.add(message["pty"])
        reply = self.link.call(message)
        if kind == "spawn" and isinstance(reply.get("pty"), int):
            self.ptys.add(reply["pty"])
        elif kind == "detach" and isinstance(message.get("pty"), int):
            self.ptys.discard(message["pty"])
        return reply

    def claim(self, pty: int) -> None:
        """Hear *pty*'s frames and events without a request (a reattach
        after a reconnect claims before it asks)."""
        self.ptys.add(pty)

    def send_event(self, message: dict) -> None:
        if not self.closed:
            self.link.send_event(message)

    def send_input(self, pty: int, data: bytes) -> None:
        if not self.closed:
            self.link.send_input(pty, data)

    def close(self) -> None:
        """The tab is done with its ptys: nothing is sent (a tab detaches
        or closes its pty itself); the client just stops hearing."""
        if self.closed:
            return
        self.closed = True
        self.ptys.clear()
        self.link._forget_pty_client(self)


def default_locale() -> str:
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(name)
        if value:
            return value.split(":")[0][:64]
    return ""
