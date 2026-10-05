# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The unit suite's in-process transport over a `ServiceCore`.

The product's one transport is the socket (`collins.api.server` and
`collins.api.client`, PR-1.12b; the Phase 1 loopback was deleted with it,
D21). The core's tests still want to drive a `ServiceCore` with the same
dicts and bytes a client sends, synchronously, on one GLib loop, with no
socket and no thread: this harness does that. A `LoopbackServer` wraps a
core; every `LoopbackClient` it connects implements the core's `Client`
protocol and validates every message both ways through `api.protocol`, so
what a test sends is what a socket would carry. A request is answered
before `request` returns; an event or an output frame reaches the client's
callback before the pty server's call returns, and every live frame is
reported drained as the callback takes it.

`LoopbackLink` is an `apilink.Link` over a `LoopbackClient` for the
mirrors' tests (`remotestate`, `remotestore`, `remoteprs`, ...). Test code
only: nothing in `collins/` imports this module.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from collins import apilink
from collins.api import protocol
from collins.api.protocol import RequestRefused

__all__ = ["LoopbackClient", "LoopbackLink", "LoopbackServer", "RequestRefused"]

log = logging.getLogger(__name__)

OutputCallback = Callable[[int, bytes, int], None]
EventCallback = Callable[[dict], None]


class _PtySink:
    __slots__ = ("client", "pty", "device")

    def __init__(self, client: LoopbackClient, pty: int) -> None:
        self.client = client
        self.pty = pty
        self.device = client.device

    @property
    def term(self) -> dict:
        return self.client.term

    def send_output(self, data: bytes, flags: int) -> None:
        header = protocol.unpack_header(protocol.pack_header(protocol.TAG_OUTPUT, flags, self.pty, 0))
        if protocol.check_frame(header, protocol.SERVICE) is not None:
            return
        self.client._on_output(self.pty, data, flags)
        if flags == 0 and data:
            self.client._server.core.ptys.drained(self.pty, self, len(data))

    def send_event(self, event: dict) -> None:
        self.client._deliver_event(event)

    def drop_queued(self) -> None:
        pass


class LoopbackClient:
    def __init__(
        self, server: LoopbackServer, on_output: OutputCallback, on_event: EventCallback, device: str = ""
    ) -> None:
        self._server = server
        self._on_output = on_output
        self._on_event = on_event
        self.device = device
        self.term: dict = {}
        self._next_id = 1
        self._sinks: dict[int, _PtySink] = {}
        self.closed = False

    # the core's Client protocol
    def sink_for(self, pty: int) -> _PtySink:
        sink = self._sinks.get(pty)
        if sink is None:
            sink = self._sinks[pty] = _PtySink(self, pty)
        return sink

    def forget(self, pty: int) -> None:
        self._sinks.pop(pty, None)

    def deliver(self, event: dict) -> None:
        if not self.closed:
            self._deliver_event(event)

    # what a tab calls
    def request(self, message: dict) -> dict:
        if self.closed:
            raise RequestRefused(protocol.ERROR_GONE, "the connection is closed", {})
        message = dict(message)
        message["id"] = self._next_id
        self._next_id += 1
        checked = protocol.validate(message, protocol.CLIENT)
        if isinstance(checked, protocol.Refusal):
            raise ValueError(f"{message.get('t')}: {checked.msgid.format_map(checked.args)}")
        if checked.kind != protocol.REQUEST:
            raise ValueError(f"{checked.type} is an event, not a request")
        raw = self._server.core.handle(checked, self)
        answer = protocol.validate_response(raw, checked.type)
        if isinstance(answer, protocol.Refusal):
            raise ValueError(f"{checked.type}: the service's reply was not valid: {answer.msgid}")
        if not answer.ok:
            raise RequestRefused(
                answer.error or protocol.ERROR_FAILED, answer.msgid or "", dict(answer.args or {})
            )
        return dict(answer.fields)

    def send_event(self, message: dict) -> None:
        if self.closed:
            return
        checked = protocol.validate(dict(message), protocol.CLIENT)
        if isinstance(checked, protocol.Refusal):
            raise ValueError(f"{message.get('t')}: {checked.msgid.format_map(checked.args)}")
        self._server.core.deliver(checked, self)

    def send_input(self, pty: int, data: bytes) -> None:
        if self.closed or not data:
            return
        header = protocol.unpack_header(protocol.pack_header(protocol.TAG_INPUT, 0, pty, 0))
        if protocol.check_frame(header, protocol.CLIENT) is not None:
            return
        self._server.core.input(pty, data, self)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._server.core.client_gone(self)
        self._sinks.clear()
        self._server._clients.discard(self)

    def _deliver_event(self, event: dict) -> None:
        checked = protocol.validate(dict(event), protocol.SERVICE)
        if isinstance(checked, protocol.Refusal):
            log.error("inproc: the service sent an event the protocol refuses: %s", checked.msgid)
            return
        if checked.type == "pty-exited":
            self.forget(int(checked.get("pty")))
        try:
            self._on_event(dict(event))
        except Exception:
            log.exception("inproc: a client's event callback failed")


class LoopbackServer:
    def __init__(self, core) -> None:
        self.core = core
        self._clients: set[LoopbackClient] = set()

    def connect(self, on_output: OutputCallback, on_event: EventCallback, device: str = "") -> LoopbackClient:
        client = LoopbackClient(self, on_output, on_event, device)
        self._clients.add(client)
        self.core.client_connected(client)
        return client

    def write_panel_history(self, key: str, shells: dict[int, int | str]) -> None:
        self.core.write_panel_history(key, shells)

    def shutdown(self) -> None:
        for client in list(self._clients):
            client.close()
        self.core.shutdown()


class LoopbackLink(apilink.Link):
    """An `apilink.Link` over a `LoopbackClient`: build it first, then connect
    the client with `dispatch` as its event callback (`bind`)."""

    def __init__(self) -> None:
        super().__init__()
        self.client = None

    def bind(self, client) -> None:
        self.client = client

    def _request(self, message: dict) -> dict:
        if self.client is None:
            raise RequestRefused(protocol.ERROR_GONE, "Not connected to the service", {})
        try:
            return self.client.request(message)
        except ValueError as error:
            log.error("link: %s is not a valid request: %s", message.get("t"), error)
            raise RequestRefused(protocol.ERROR_INVALID, str(error), {}) from None

    def send_event(self, message: dict) -> None:
        if self.client is None:
            return
        try:
            self.client.send_event(message)
        except ValueError as error:
            log.error("link: %s is not a valid event: %s", message.get("t"), error)
