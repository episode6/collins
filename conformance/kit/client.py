# SPDX-License-Identifier: MIT
# Copyright (c) 2026 episode6, Inc.
#
# Part of the conformance kit for the Collins service API (conformance/),
# licensed MIT like the specification it tests (see conformance/LICENSE),
# unlike the rest of Collins. Standard library only.
"""A client of the service API, as the kit drives it.

One `Client` is one `client_id`: a primary connection that carries the
subscription, every event and every output and input frame, and, when
asked, a sync channel that answers requests and sends nothing unasked
(docs/api/index.md, Connections). Requests are `call`s answered on the
connection that asked; everything else the server sends lands in
`events`, in the per-pty output buffers (live and redraw apart) and in the
blob streams of chunked replies. Every frame is validated against the
protocol module as it arrives, and a frame that does not validate is
recorded in `violations` for the wire check to report.

The kit is single-threaded in its reading: a `call` or a `wait_for`
drains the connection's queue in the calling thread, so events are
dispatched in order, and an output frame is acked as it is fed
(`ack {pty, offset}`, more often than the spec's "every 64 KiB or 100 ms",
which it allows).
"""

from __future__ import annotations

import itertools
import json
import time
import uuid
from dataclasses import dataclass, field

from . import proto, ws

CALL_TIMEOUT_S = 10.0


class ClientError(Exception):
    pass


class Timeout(ClientError):
    pass


@dataclass
class Reply:
    ok: bool
    fields: dict = field(default_factory=dict)
    error: str = ""
    msgid: str = ""
    args: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    elapsed: float = 0.0
    chunked: str = ""  # the field that travelled as blob frames, if one did

    def __getitem__(self, name):
        return self.fields[name]

    def get(self, name, default=None):
        return self.fields.get(name, default)

    def describe(self) -> str:
        if self.ok:
            return "ok " + json.dumps(self.fields, ensure_ascii=False)[:200]
        try:
            text = self.msgid.format(**self.args)
        except (KeyError, IndexError, ValueError):
            text = self.msgid
        return f"{self.error}: {text}"


@dataclass
class Event:
    type: str
    fields: dict
    when: float
    connection: str  # "primary" or "sync"

    def get(self, name, default=None):
        return self.fields.get(name, default)

    def __getitem__(self, name):
        return self.fields[name]


@dataclass
class OutputFrame:
    pty: int
    offset: int
    flags: int
    payload: bytes
    when: float


class Connection:
    """One WebSocket, with its hello."""

    def __init__(self, client: Client, name: str, channel: str | None) -> None:
        self.client = client
        self.name = name
        self.channel = channel
        self.ws = ws.WebSocket(client.sock_path)
        self.hello_reply: Reply | None = None
        self.pending: dict[int, Reply] = {}
        self.blobs: dict[int, bytearray] = {}

    def connect(self) -> None:
        self.ws.connect()

    def send(self, message: dict) -> None:
        p = proto.load()
        self.ws.send_text(p.encode(message))

    def send_binary(self, data: bytes) -> None:
        self.ws.send_binary(data)

    def pump(self, timeout: float | None) -> bool:
        """Take one message off the wire and dispatch it. False on timeout."""
        item = self.ws.recv(timeout)
        if item is None:
            return False
        kind, payload = item
        now = time.monotonic()
        client = self.client
        if kind == "text":
            if len(payload) > proto.load().MAX_FRAME:
                client.violations.append(
                    f"{self.name}: a text frame of {len(payload)} characters, over MAX_FRAME"
                )
            try:
                message = json.loads(payload)
            except ValueError:
                client.violations.append(f"{self.name}: a text frame that is not JSON")
                return True
            if not isinstance(message, dict):
                client.violations.append(f"{self.name}: a text frame that is not an object")
                return True
            if "re" in message and "t" not in message:
                self._reply(message, now)
            else:
                client._event(self, message, now)
            return True
        if kind == "binary":
            if len(payload) > proto.load().MAX_FRAME:
                client.violations.append(
                    f"{self.name}: a binary frame of {len(payload)} bytes, over MAX_FRAME"
                )
            client._binary(self, payload, now)
            return True
        if kind == "ping":
            client.pings += 1
            return True
        if kind == "error":
            client.violations.append(f"{self.name}: {payload}")
            return True
        return True

    def _reply(self, message: dict, now: float) -> None:
        p = proto.load()
        re_id = p.response_id(message)
        if re_id is None:
            self.client.violations.append(f"{self.name}: a reply with no usable re")
            return
        # A chunked reply: its field travelled ahead as blob frames.
        chunked = next((f for f in p.CHUNKED_FIELDS if message.get(f + "_chunked")), "")
        joined = p.join_reply(message, bytes(self.blobs.pop(re_id & p.STREAM_MASK, b"")))
        if joined is None:
            self.client.violations.append(f"{self.name}: a chunked reply whose chunks did not add up")
            joined = message
        self.pending[re_id] = Reply(
            ok=bool(joined.get("ok")),
            fields={k: v for k, v in joined.items() if k not in ("re", "ok", "error", "msgid", "args")},
            error=str(joined.get("error", "")),
            msgid=str(joined.get("msgid", "")),
            args=dict(joined.get("args") or {}) if isinstance(joined.get("args"), dict) else {},
            raw=joined,
            elapsed=now,
            chunked=chunked,
        )

    def close(self) -> None:
        self.ws.close()


class Client:
    """One `client_id` on the service: a primary and, optionally, a sync channel."""

    def __init__(
        self,
        sock_path: str,
        device: str = "conformance",
        client_id: str | None = None,
        version: str = "0.1",
        term: dict | None = None,
        locale: str | None = None,
    ) -> None:
        self.sock_path = sock_path
        self.device = device
        self.client_id = client_id or ("kit-" + uuid.uuid4().hex[:12])
        self.version = version
        self.term = term
        self.locale = locale
        self.ids = itertools.count(1)
        self.primary = Connection(self, "primary", None)
        self.sync: Connection | None = None
        self.events: list[Event] = []
        self.output: dict[int, bytearray] = {}
        self.frames: dict[int, list[OutputFrame]] = {}
        self.redraws: dict[int, list[list[OutputFrame]]] = {}
        self._redraw_open: dict[int, list[OutputFrame]] = {}
        self.acked: dict[int, int] = {}
        self.violations: list[str] = []
        self.pings = 0
        self.hello: Reply | None = None
        self.caps: set[str] = set()
        self.listeners: list = []
        self.local_proved = False

    # -- connecting -----------------------------------------------------------

    def hello_message(self, channel: str | None = None, protocol: int | None = None, **extra) -> dict:
        p = proto.load()
        message = {
            "t": "hello",
            "protocol": p.PROTOCOL if protocol is None else protocol,
            "version": self.version,
            "client_id": self.client_id,
            "device": self.device,
        }
        if self.locale:
            message["locale"] = self.locale
        if self.term is not None:
            message["term"] = self.term
        if channel:
            message["channel"] = channel
        message.update(extra)
        return message

    def connect(self, with_sync: bool = False, **hello_extra) -> Reply:
        """Open the primary (and the sync channel), say hello on each."""
        self.primary.connect()
        reply = self.call_on(self.primary, self.hello_message(**hello_extra))
        self.primary.hello_reply = reply
        self.hello = reply
        if reply.ok:
            self.caps = set(reply.get("caps") or [])
        if with_sync:
            self.open_sync()
        return reply

    def open_sync(self) -> Reply:
        self.sync = Connection(self, "sync", "sync")
        self.sync.connect()
        reply = self.call_on(self.sync, self.hello_message(channel="sync"))
        self.sync.hello_reply = reply
        return reply

    def close(self) -> None:
        if self.sync is not None:
            self.sync.close()
        self.primary.close()

    def abort(self) -> None:
        if self.sync is not None:
            self.sync.ws.abort()
        self.primary.ws.abort()

    # -- requests -------------------------------------------------------------

    def call_on(self, connection: Connection, message: dict, timeout: float = CALL_TIMEOUT_S) -> Reply:
        request_id = message.get("id")
        if request_id is None:
            request_id = next(self.ids)
            message = {**message, "id": request_id}
        started = time.monotonic()
        p = proto.load()
        frames, slim = p.split_request(message)
        for frame in frames:
            connection.send_binary(frame)
        connection.send(slim)
        deadline = started + timeout
        while request_id not in connection.pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Timeout(f"{message.get('t')}: no reply within {timeout:.0f} s")
            connection.pump(min(remaining, 0.5))
        reply = connection.pending.pop(request_id)
        reply.elapsed = reply.elapsed - started
        self._validate_reply(message.get("t"), reply)
        return reply

    def call(self, type_: str, timeout: float = CALL_TIMEOUT_S, **fields) -> Reply:
        """A request on the primary, answered there."""
        return self.call_on(self.primary, {"t": type_, **fields}, timeout)

    def call_sync(self, type_: str, timeout: float = CALL_TIMEOUT_S, **fields) -> Reply:
        """A request on the sync channel (opened on demand)."""
        if self.sync is None:
            self.open_sync()
        return self.call_on(self.sync, {"t": type_, **fields}, timeout)

    def send_event(self, type_: str, **fields) -> None:
        self.primary.send({"t": type_, **fields})

    def send_raw(self, message: dict) -> None:
        self.primary.send(message)

    def send_input(self, pty: int, data: bytes) -> None:
        p = proto.load()
        self.primary.send_binary(p.pack_frame(p.TAG_INPUT, 0, pty, 0, data))

    def _validate_reply(self, type_: str | None, reply: Reply) -> None:
        p = proto.load()
        if type_ not in p.TYPES or p.TYPES[type_].request is None:
            return
        verdict = p.validate_response(reply.raw, type_)
        if isinstance(verdict, p.Refusal):
            self.violations.append(f"reply to {type_} did not validate: {verdict.msgid} {verdict.args}")

    # -- what arrives unasked -------------------------------------------------

    def _event(self, connection: Connection, message: dict, now: float) -> None:
        p = proto.load()
        name = message.get("t")
        verdict = p.validate(message, p.SERVICE)
        if isinstance(verdict, p.Refusal):
            self.violations.append(f"{connection.name}: event {name!r} did not validate: {verdict.msgid}")
        if connection.channel == "sync":
            self.violations.append(f"sync channel carried an unasked {name!r}")
        event = Event(str(name), {k: v for k, v in message.items() if k != "t"}, now, connection.name)
        self.events.append(event)
        for listener in list(self.listeners):
            listener(event)

    def _binary(self, connection: Connection, payload: bytes, now: float) -> None:
        p = proto.load()
        try:
            header, data = p.unpack_frame(payload)
        except ValueError as error:
            self.violations.append(f"{connection.name}: {error}")
            return
        refusal = p.check_frame(header, p.SERVICE)
        if refusal is not None:
            self.violations.append(f"{connection.name}: binary frame refused: {refusal.msgid}")
            return
        if connection.channel == "sync" and header.tag == p.TAG_OUTPUT:
            self.violations.append("sync channel carried an output frame")
        if header.tag == p.TAG_BLOB:
            connection.blobs.setdefault(header.stream, bytearray()).extend(data)
            return
        frame = OutputFrame(header.stream, header.offset, header.flags, data, now)
        self.frames.setdefault(header.stream, []).append(frame)
        if header.flags & p.FLAG_REDRAW:
            open_redraw = self._redraw_open.setdefault(header.stream, [])
            open_redraw.append(frame)
            if header.flags & p.FLAG_REDRAW_END:
                self.redraws.setdefault(header.stream, []).append(open_redraw)
                self._redraw_open[header.stream] = []
            return
        self.output.setdefault(header.stream, bytearray()).extend(data)
        fed = header.offset + len(data)
        self.acked[header.stream] = fed
        try:
            self.send_event("ack", pty=header.stream, offset=fed)
        except ws.Closed:
            pass

    # -- waiting --------------------------------------------------------------

    def pump(self, timeout: float = 0.0) -> None:
        """Dispatch whatever has arrived on the primary within *timeout*."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if not self.primary.pump(max(remaining, 0.0)):
                return
            if remaining <= 0:
                return

    def wait_for(self, predicate, timeout: float, since: int = 0) -> Event | None:
        """The first event (from index *since*) that satisfies *predicate*,
        pumping the primary until one arrives or *timeout* passes."""
        deadline = time.monotonic() + timeout
        index = since
        while True:
            while index < len(self.events):
                event = self.events[index]
                index += 1
                if predicate(event):
                    return event
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self.primary.pump(min(remaining, 0.5))

    def wait_until(self, predicate, timeout: float) -> bool:
        """Pump until *predicate()* holds or *timeout* passes."""
        deadline = time.monotonic() + timeout
        while True:
            if predicate():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return bool(predicate())
            self.primary.pump(min(remaining, 0.2))

    def wait_output(self, pty: int, needle: bytes, timeout: float, start: int = 0) -> int:
        """Pump until the pty's live output holds *needle* at or after
        *start*; the index found, or -1."""
        found = [-1]

        def check() -> bool:
            found[0] = bytes(self.output.get(pty, b"")).find(needle, start)
            return found[0] >= 0

        self.wait_until(check, timeout)
        return found[0]

    def wait_quiet(self, pty: int, quiet_s: float, timeout: float) -> bool:
        """Pump until the pty has written something and then nothing for
        *quiet_s*: a just-spawned shell has drawn its prompt and taken the
        terminal, so a line typed now is read by the shell and not left in
        the terminal's cooked-mode buffer, where its echo would show on the
        screen before the shell has seen it."""
        deadline = time.monotonic() + timeout
        last_len = -1
        last_change = time.monotonic()
        while time.monotonic() < deadline:
            length = self.output_len(pty)
            if length != last_len:
                last_len, last_change = length, time.monotonic()
            elif length > 0 and time.monotonic() - last_change >= quiet_s:
                return True
            self.primary.pump(0.05)
        return False

    def output_text(self, pty: int) -> str:
        return bytes(self.output.get(pty, b"")).decode("utf-8", "replace")

    def output_len(self, pty: int) -> int:
        return len(self.output.get(pty, b""))

    def events_of(self, type_: str, since: int = 0) -> list[Event]:
        return [event for event in self.events[since:] if event.type == type_]

    def session_events(self, pty: int, since: int = 0) -> list[Event]:
        return [e for e in self.events[since:] if e.type == "session" and e.get("pty") == pty]

    def session_facts(self, pty: int) -> dict:
        """What the session events said so far about *pty*, merged in order."""
        facts: dict = {}
        for event in self.session_events(pty):
            facts.update(event.fields)
        return facts

    def item_facts(self, session: str) -> dict:
        facts: dict = {}
        for event in self.events:
            if event.type == "item" and event.get("session") == session:
                facts.update(event.fields)
        return facts

    # -- convenience ----------------------------------------------------------

    def subscribe(self, timeout: float = CALL_TIMEOUT_S) -> tuple[Reply, list[Event]]:
        """`subscribe`, answering with the reply and the snapshot's events."""
        before = len(self.events)
        reply = self.call("subscribe", timeout=timeout)
        return reply, self.events[before:]

    def attach(self, pty: int, cols: int, rows: int, timeout: float = CALL_TIMEOUT_S) -> Reply:
        return self.call("attach", timeout=timeout, pty=pty, cols=cols, rows=rows)

    def wait_redraw(self, pty: int, timeout: float, count: int = 1) -> list[OutputFrame] | None:
        """Pump until the *count*th complete redraw of *pty* has arrived."""
        if self.wait_until(lambda: len(self.redraws.get(pty, [])) >= count, timeout):
            return self.redraws[pty][count - 1]
        return None
