# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""The loopback: the service/client API inside one process.

Phase 1 of the split (spec §3.5, swap 2) puts the tab's terminal on the
service's pty server before any socket exists. This is the transport for
that: a `LoopbackServer` wraps a `service.core.ServiceCore`, and every
`LoopbackClient` it connects talks to it in **the same dicts and bytes the
socket will carry**, every message going through `api.protocol.validate`
on the way in and `validate_response` on the way back, so by the time
PR-1.12 swaps the loopback for the socket the protocol has carried every
e2e check for a release cycle. There is no framing (no JSON, no 16-byte
header: the dicts and the bytes are handed over as they are) and no
queueing: a request is answered before `request` returns, an event or an
output frame reaches the client's callback before `send_output` returns,
all on the one GLib loop, which is also why the loopback reports every
live frame as drained the moment the callback took it.

Two shortcuts the socket will not have, for the `Session` that still runs
in the client's process through Phase 1: `screen_of(pty)` (the model the
session's `ScreenPort` reads) and `pty_of(pty)` (the pty object its
`PtyPort` asks for the child's pid and foreground group). PR-1.10 moves
the session into the service and both go. A panel shell (PR-1.8) reads
through the same two (its text for `read_terminal`, its foreground, its
shell's cwd), and the tab's panel-history save, whose key and moment are
still the tab's in Phase 1, is a third on the server's end:
`LoopbackServer.write_panel_history`, the core writing from its models.

**This module is deleted at the end of Phase 1** (D21): hard requirement 1
says same-machine goes through the API, and an in-process mode left behind
would be a second product to test.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..service.core import ServiceCore
from . import protocol

log = logging.getLogger(__name__)

OutputCallback = Callable[[int, bytes, int], None]  # (pty, data, flags)
EventCallback = Callable[[dict], None]


class RequestRefused(Exception):
    """A request the service refused: `error` (one of `protocol.ERRORS`),
    the `msgid` and its `details` (the msgid's args; not `args`, which is
    `BaseException`'s tuple), for the client to translate."""

    def __init__(self, error: str, msgid: str, details: dict) -> None:
        super().__init__(error, msgid)
        self.error = error
        self.msgid = msgid
        self.details = details


class _PtySink:
    """One client's view of one pty, as the pty server sees it (its sink
    protocol: `send_output`, `send_event`, `drop_queued`, `device`)."""

    __slots__ = ("client", "pty", "device")

    def __init__(self, client: LoopbackClient, pty: int) -> None:
        self.client = client
        self.pty = pty
        self.device = client.device

    def send_output(self, data: bytes, flags: int) -> None:
        header = protocol.unpack_header(protocol.pack_header(protocol.TAG_OUTPUT, flags, self.pty, 0))
        refusal = protocol.check_frame(header, protocol.SERVICE)
        if refusal is not None:
            log.error("loopback: a bad output frame for pty %d: %s", self.pty, refusal.msgid)
            return
        self.client._on_output(self.pty, data, flags)
        if flags == 0 and data:
            # Taken by the callback, so written out: nothing queues here.
            self.client._server.core.ptys.drained(self.pty, self, len(data))

    def send_event(self, event: dict) -> None:
        self.client._deliver_event(event)

    def drop_queued(self) -> None:
        pass  # nothing is ever queued on a loopback


class LoopbackClient:
    """A client's end of the loopback. Built by `LoopbackServer.connect`."""

    def __init__(
        self,
        server: LoopbackServer,
        on_output: OutputCallback,
        on_event: EventCallback,
        device: str = "",
    ) -> None:
        self._server = server
        self._on_output = on_output
        self._on_event = on_event
        self.device = device
        self._next_id = 1
        self._sinks: dict[int, _PtySink] = {}
        self.closed = False

    # -- the core's Client protocol

    def sink_for(self, pty: int) -> _PtySink:
        sink = self._sinks.get(pty)
        if sink is None:
            sink = self._sinks[pty] = _PtySink(self, pty)
        return sink

    def forget(self, pty: int) -> None:
        self._sinks.pop(pty, None)

    # -- what the tab calls

    def request(self, message: dict) -> dict:
        """Send a request (``{"t": ..., ...}``, the id minted here) and
        return its ok reply's fields. Raises `RequestRefused` for a refusal
        and `ValueError` for a message the protocol does not accept, as the
        socket's client would."""
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
        """A client event (`resize`, `focus`, `theme`)."""
        if self.closed:
            return
        checked = protocol.validate(dict(message), protocol.CLIENT)
        if isinstance(checked, protocol.Refusal):
            raise ValueError(f"{message.get('t')}: {checked.msgid.format_map(checked.args)}")
        self._server.core.deliver(checked, self)

    def send_input(self, pty: int, data: bytes) -> None:
        """A `0x02` frame: what the VTE committed."""
        if self.closed or not data:
            return
        header = protocol.unpack_header(protocol.pack_header(protocol.TAG_INPUT, 0, pty, 0))
        if protocol.check_frame(header, protocol.CLIENT) is not None:
            return
        self._server.core.input(pty, data, self)

    def close(self) -> None:
        """The client goes away: every pty it showed is detached, nothing
        else changes (§3.1 rule 5)."""
        if self.closed:
            return
        self.closed = True
        self._server.core.client_gone(self)
        self._sinks.clear()
        self._server._clients.discard(self)

    # -- the shortcuts (see the module docstring)

    def screen_of(self, pty: int):
        return self._server.core.screen_of(pty)

    def pty_of(self, pty: int):
        return self._server.core.pty_of(pty)

    # -- the server's way in

    def _deliver_event(self, event: dict) -> None:
        checked = protocol.validate(dict(event), protocol.SERVICE)
        if isinstance(checked, protocol.Refusal):
            log.error("loopback: the service sent an event the protocol refuses: %s", checked.msgid)
            return
        if checked.type == "pty-exited":
            self.forget(int(checked.get("pty")))
        try:
            self._on_event(dict(event))
        except Exception:
            log.exception("loopback: a client's event callback failed")


class LoopbackServer:
    """The service's end: one per `ServiceCore`."""

    def __init__(self, core: ServiceCore) -> None:
        self.core = core
        self._clients: set[LoopbackClient] = set()

    def connect(self, on_output: OutputCallback, on_event: EventCallback, device: str = "") -> LoopbackClient:
        client = LoopbackClient(self, on_output, on_event, device)
        self._clients.add(client)
        self.core.client_connected(client)
        return client

    def write_panel_history(self, key: str, shells: dict[int, int | str]) -> None:
        """The tab's panel-history save (`ServiceCore.write_panel_history`):
        a shortcut, see the module docstring."""
        self.core.write_panel_history(key, shells)

    def shutdown(self) -> None:
        for client in list(self._clients):
            client.close()
        self.core.shutdown()
