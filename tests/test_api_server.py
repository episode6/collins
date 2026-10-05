# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The API server (collins.api.server, spec §3.20): a real `Soup.Server` on
a scratch socket driven by a raw libsoup client on the one GLib loop, no
subprocess, no thread. The hello window, `sequence` before hello, the
local proof right and wrong, an oversized text payload refused with the
connection kept, the ack accounting reported to the pty server, and the
sync channel's requests answered on it while the subscription's events
land on the primary."""

import os
import time

import gi
import pytest

gi.require_version("Soup", "3.0")
import inproc  # noqa: E402,F401  (the harness; keeps the suite's import style)
from gi.repository import Gio, GLib, Soup  # noqa: E402

from collins.api import protocol  # noqa: E402
from collins.api import server as api_server  # noqa: E402
from collins.service.core import ServiceCore  # noqa: E402

CAT = "/bin/cat"


def pump(seconds=0.5, until=None):
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ctx.iteration(False)
        if until is not None and until():
            return True
        time.sleep(0.005)
    return until() if until is not None else True


class Raw:
    """A raw WebSocket client on the default context."""

    def __init__(self, path: str):
        self.ws = None
        self.error = None
        self.texts = []  # decoded text frames, in order
        self.frames = []  # (header, payload) of binary frames
        self.closed = False
        self._next = 1
        session = Soup.Session(remote_connectable=Gio.UnixSocketAddress.new(path))
        message = Soup.Message.new("GET", "ws://collins/api/ws")
        session.websocket_connect_async(message, None, None, GLib.PRIORITY_DEFAULT, None, self._connected)
        self._session = session
        assert pump(5, lambda: self.ws is not None or self.error is not None), "no connection"
        assert self.error is None, self.error

    def _connected(self, session, result):
        try:
            self.ws = session.websocket_connect_finish(result)
        except GLib.Error as err:
            self.error = err.message
            return
        self.ws.set_max_incoming_payload_size(protocol.MAX_INCOMING)
        self.ws.connect("message", self._on_message)
        self.ws.connect("closed", lambda *_: setattr(self, "closed", True))

    def _on_message(self, _ws, kind, data):
        payload = data.get_data() or b""
        if kind == Soup.WebsocketDataType.TEXT:
            self.texts.append(protocol.decode(payload))
        else:
            self.frames.append(protocol.unpack_frame(payload))

    def send(self, message: dict):
        self.ws.send_text(protocol.encode(message))

    def send_raw(self, text: str):
        self.ws.send_text(text)

    def send_binary(self, frame: bytes):
        self.ws.send_message(Soup.WebsocketDataType.BINARY, GLib.Bytes.new(frame))

    def request(self, message: dict, timeout=5.0) -> dict:
        message = dict(message)
        message["id"] = self._next
        self._next += 1
        self.send(message)
        reply = {}

        def arrived():
            for text in self.texts:
                if protocol.response_id(text) == message["id"]:
                    reply.update(text)
                    return True
            return False

        assert pump(timeout, arrived), f"no reply to {message['t']}"
        return reply

    def events(self, kind=None):
        return [t for t in self.texts if "t" in t and (kind is None or t["t"] == kind)]

    def hello(self, client_id="client-1", channel=None, **extra):
        message = {
            "t": "hello",
            "protocol": protocol.PROTOCOL,
            "min_protocol": protocol.MIN_PROTOCOL,
            "version": "0.0.0",
            "client_id": client_id,
            "device": "laptop",
            **extra,
        }
        if channel:
            message["channel"] = channel
        return self.request(message)

    def close(self):
        if self.ws is not None and self.ws.get_state() == Soup.WebsocketState.OPEN:
            self.ws.close(Soup.WebsocketCloseCode.NORMAL, None)
        pump(0.2)


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", CAT)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    (tmp_path / "run").mkdir()
    core = ServiceCore(state_dir=tmp_path / "pty", get_setting=lambda key: True)
    server = api_server.ApiServer(core, "com.example.Api", debug=True)
    path = server.listen()
    yield server, path
    server.stop()
    core.shutdown()
    pump(0.3)


# -- hello ------------------------------------------------------------------------


def test_hello_answers_the_services_facts(served):
    server, path = served
    client = Raw(path)
    reply = client.hello()
    assert reply["ok"] and reply["protocol"] == protocol.PROTOCOL
    assert reply["min_protocol"] == protocol.MIN_PROTOCOL
    assert set(reply["caps"]) == {"local", "debug"}
    assert reply["local_proof"] == {"path": server.proof_file, "length": 32}
    assert reply["service_id"] and reply["version"]
    assert "client-1" in server.clients and server.clients["client-1"].device == "laptop"
    client.close()
    assert pump(2, lambda: "client-1" not in server.clients)


def test_a_client_outside_the_window_is_refused_and_closed(served):
    _server, path = served
    client = Raw(path)
    reply = client.hello(protocol=99, min_protocol=99)
    assert reply["ok"] is False and reply["error"] == "protocol"
    assert reply["args"] == {"client": 99, "service": protocol.PROTOCOL}
    assert pump(2, lambda: client.closed)


def test_anything_before_hello_is_sequence(served):
    _server, path = served
    client = Raw(path)
    reply = client.request({"t": "service.status"})
    assert reply["ok"] is False and reply["error"] == "sequence"
    client.send({"t": "theme", "term": {"vte": 8400}})  # an event: dropped, no answer
    client.send_binary(protocol.pack_frame(protocol.TAG_INPUT, 0, 1, 0, b"x"))
    pump(0.2)
    assert reply == client.texts[-1]
    client.close()


# -- local ------------------------------------------------------------------------


def test_local_takes_the_proof_file_and_refuses_the_wrong_bytes(served):
    server, path = served
    client = Raw(path)
    client.hello()
    wrong = client.request({"t": "local", "proof": "ab" * 32})
    assert wrong["ok"] is False and wrong["error"] == "refused"
    assert server.clients["client-1"].local is False
    with open(server.proof_file, "rb") as fh:
        proof = fh.read().hex()
    right = client.request({"t": "local", "proof": proof})
    assert right["ok"] is True and server.clients["client-1"].local is True
    client.close()


# -- bounds -----------------------------------------------------------------------


def test_an_oversized_payload_is_refused_and_the_connection_kept(served):
    _server, path = served
    client = Raw(path)
    client.hello()
    pty = client.request({"t": "spawn", "kind": "shell", "cwd": "/tmp", "cols": 80, "rows": 24})["pty"]
    big = {"t": "paint", "id": 77, "pty": pty, "text": "x" * (2 * 1024 * 1024)}
    import json

    client.send_raw(json.dumps(big))  # protocol.encode would refuse it: a broken peer's frame
    assert pump(5, lambda: any(protocol.response_id(t) == 77 for t in client.texts))
    reply = [t for t in client.texts if protocol.response_id(t) == 77][0]
    assert reply["ok"] is False and reply["error"] == "invalid"
    assert not client.closed
    status = client.request({"t": "service.status"})
    assert status["ok"] and status["ptys"] == 1 and status["pid"] == os.getpid()
    client.close()


# -- flow control -----------------------------------------------------------------


def test_output_frames_carry_offsets_and_acks_reach_the_pty_server(served):
    server, path = served
    drained = []
    real = server.core.ptys.drained
    server.core.ptys.drained = lambda pty, sink, n: (drained.append((pty, n)), real(pty, sink, n))
    client = Raw(path)
    client.hello()
    pty = client.request({"t": "spawn", "kind": "shell", "cwd": "/tmp", "cols": 80, "rows": 24})["pty"]
    client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    assert pump(2, lambda: any(h.flags & protocol.FLAG_REDRAW_END for h, _ in client.frames))
    client.send_binary(protocol.pack_frame(protocol.TAG_INPUT, 0, pty, 0, b"hello\r"))
    assert pump(3, lambda: b"hello" in b"".join(p for h, p in client.frames if h.flags == 0))
    live = [(h, p) for h, p in client.frames if h.flags == 0 and h.stream == pty]
    offsets = [h.offset for h, _ in live]
    assert offsets[0] == 0
    assert all(offsets[i + 1] == offsets[i] + len(live[i][1]) for i in range(len(live) - 1))
    fed = sum(len(p) for _, p in live)
    assert drained == []
    client.send({"t": "ack", "pty": pty, "offset": fed})
    assert pump(2, lambda: drained == [(pty, fed)])
    client.send({"t": "ack", "pty": pty, "offset": fed})  # a repeat covers nothing
    pump(0.2)
    assert drained == [(pty, fed)]
    client.close()


def test_the_sink_holds_output_past_the_window_until_acked(served, monkeypatch):
    server, path = served
    monkeypatch.setattr(protocol, "ACK_WINDOW", 64)
    client = Raw(path)
    client.hello()
    pty = client.request({"t": "spawn", "kind": "shell", "cwd": "/tmp", "cols": 200, "rows": 24})["pty"]
    client.request({"t": "attach", "pty": pty, "cols": 200, "rows": 24})
    pump(0.3)
    sink = server.clients["client-1"].sink_for(pty)
    # The first chunk fills the window (it is sent whole, whatever its
    # size); the next one waits in the sink's own list.
    client.send_binary(protocol.pack_frame(protocol.TAG_INPUT, 0, pty, 0, b"0123456789" * 20 + b"\r"))
    assert pump(3, lambda: sink.sent >= 64)
    client.send_binary(protocol.pack_frame(protocol.TAG_INPUT, 0, pty, 0, b"abcdefghij" * 20 + b"\r"))
    assert pump(3, lambda: sink.pending_bytes > 0)
    sent_before = sink.sent
    client.send({"t": "ack", "pty": pty, "offset": sent_before})
    assert pump(2, lambda: sink.sent > sent_before)
    sink.drop_queued()
    assert sink.pending_bytes == 0 and not sink.pending
    client.close()


# -- the sync channel -------------------------------------------------------------


def test_the_sync_channel_answers_requests_and_its_events_land_on_the_primary(served):
    server, path = served
    primary = Raw(path)
    primary.hello(client_id="c")
    sync = Raw(path)
    sync.hello(client_id="c", channel="sync")
    client = server.clients["c"]
    assert client.primary is not None and client.sync is not None
    assert len(server.clients) == 1
    # A spawn asked on the sync channel is answered there; the attach's
    # redraw and the pty's events go down the primary.
    pty = sync.request({"t": "spawn", "kind": "shell", "cwd": "/tmp", "cols": 80, "rows": 24})["pty"]
    reply = sync.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    assert reply["ok"] is True
    assert pump(2, lambda: any(h.flags & protocol.FLAG_REDRAW_END for h, _ in primary.frames))
    assert sync.frames == []
    sync.request({"t": "close", "pty": pty, "mode": "kill"})
    assert pump(3, lambda: primary.events("pty-exited"))
    assert not sync.events("pty-exited")
    # The sync channel closing alone leaves the client; the primary closing ends it.
    sync.close()
    assert pump(2, lambda: client.sync is None)
    assert "c" in server.clients
    primary.close()
    assert pump(2, lambda: "c" not in server.clients)


def test_a_sync_hello_needs_its_primary_first(served):
    _server, path = served
    sync = Raw(path)
    reply = sync.hello(client_id="lonely", channel="sync")
    assert reply["ok"] is False and reply["error"] == "sequence"
    sync.close()


def test_the_subscription_fills_over_the_primary(app_state, projects_dir, tmp_path, monkeypatch):
    from collins.sessions import discover_sessions
    from collins.store import SessionStore

    monkeypatch.setenv("SHELL", CAT)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    (tmp_path / "run").mkdir()
    _root, ids = projects_dir
    state = app_state.AppState(migrate=True, device=False)
    core = ServiceCore(state=state, state_dir=tmp_path / "pty")
    store = SessionStore(state)
    core.start_store(store)
    store._last_sessions = discover_sessions()
    store._apply()
    server = api_server.ApiServer(core, "com.example.Sub")
    path = server.listen()
    try:
        primary = Raw(path)
        assert primary.hello(client_id="c")["service_id"] == state.service_id
        sync = Raw(path)
        sync.hello(client_id="c", channel="sync")
        reply = sync.request({"t": "subscribe"})
        assert reply["ok"] is True and reply["items"] >= 2
        assert pump(2, lambda: primary.events("rows"))
        items = {e["session"] for e in primary.events("item")}
        assert ids["alpha1"] in items
        assert not sync.events("item") and not sync.events("rows")
        # A state write from the sync channel: its event comes back on the primary.
        sync.request({"t": "state.set", "key": "names", "entry": ids["alpha1"], "value": "Renamed"})
        assert pump(2, lambda: any(
            e.get("key") == "names" and e.get("value") == "Renamed" for e in primary.events("state.set")
        ))
        primary.close()
        sync.close()
    finally:
        server.stop()
        core.shutdown()
        pump(0.3)


# -- takeovers, foreign ids, bad frames, the cut-off, restart ------------------


def test_a_second_primary_takes_a_client_id_over(served):
    server, path = served
    first = Raw(path)
    first.hello(client_id="c")
    old = server.clients["c"]
    second = Raw(path)
    second.hello(client_id="c")
    assert server.clients["c"] is not old and old.gone
    assert pump(2, lambda: first.closed)
    assert second.request({"t": "service.status"})["ok"]
    second.close()


def test_a_sync_hello_on_a_foreign_id_is_sequence(served):
    _server, path = served
    primary = Raw(path)
    primary.hello(client_id="mine")
    sync = Raw(path)
    reply = sync.hello(client_id="theirs", channel="sync")
    assert reply["ok"] is False and reply["error"] == "sequence"
    primary.close()
    sync.close()


def test_bad_binary_frames_are_dropped_and_the_connection_kept(served):
    server, path = served
    client = Raw(path)
    client.send_binary(b"\x02\x00")  # input before hello, and too short for a header
    client.hello()
    client.send_binary(b"\x02\x00\x00")  # a header too short
    client.send_binary(protocol.pack_frame(protocol.TAG_OUTPUT, 0, 1, 0, b"x"))  # a service's tag
    client.send_binary(protocol.pack_frame(protocol.TAG_INPUT, 0, 999, 0, b"x"))  # no such pty
    pump(0.3)
    assert not client.closed
    assert client.request({"t": "service.status"})["ok"]
    client.close()


def test_an_event_before_hello_is_dropped(served):
    _server, path = served
    client = Raw(path)
    client.send({"t": "ack", "pty": 1, "offset": 10})
    client.send({"t": "theme", "term": {"vte": 8400}})
    pump(0.3)
    assert client.texts == [] and not client.closed
    client.close()


def test_the_cut_off_redraws_and_acks_after_a_drop_still_count(served, monkeypatch):
    from collins.service import ptyserver

    server, path = served
    monkeypatch.setattr(protocol, "ACK_WINDOW", 4096)
    monkeypatch.setattr(ptyserver, "QUEUE_BYTES", 16 * 1024)
    drained = []
    real = server.core.ptys.drained
    server.core.ptys.drained = lambda pty, sink, n: (drained.append(n), real(pty, sink, n))
    client = Raw(path)
    client.hello()
    pty = client.request({"t": "spawn", "kind": "shell", "cwd": "/tmp", "cols": 200, "rows": 24})["pty"]
    client.request({"t": "attach", "pty": pty, "cols": 200, "rows": 24})
    assert pump(2, lambda: any(h.flags & protocol.FLAG_REDRAW_END for h, _ in client.frames))
    client.frames.clear()
    sink = server.clients["client-1"].sink_for(pty)
    dropped = []
    real_drop = api_server._Sink.drop_queued
    monkeypatch.setattr(api_server._Sink, "drop_queued", lambda s: (dropped.append(s.acked), real_drop(s)))
    # Never acked: the server counts past QUEUE_BYTES, cuts the sink off
    # (drop_queued) and redraws it.
    for _ in range(6):
        client.send_binary(protocol.pack_frame(protocol.TAG_INPUT, 0, pty, 0, b"y" * 4000 + b"\r"))
    assert pump(5, lambda: dropped and any(h.flags & protocol.FLAG_REDRAW for h, _ in client.frames))
    assert sink.acked == dropped[0]  # the drop moved no ack mark
    # An ack after the drop is still reported, and reopens the window.
    sent = sink.sent
    client.send({"t": "ack", "pty": pty, "offset": sent})
    assert pump(2, lambda: sum(drained) > 0 and sink.acked == sent)
    client.close()


def test_service_restart_now_idle_and_cancel_are_served(tmp_path, monkeypatch):
    """PR-1.12c serves `idle` (the core waits for no session busy) and
    `cancel` (that wait called off) beside `now`."""
    monkeypatch.setenv("SHELL", CAT)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    (tmp_path / "run").mkdir()
    core = ServiceCore(state_dir=tmp_path / "pty", get_setting=lambda key: True)

    class Busy:
        count = 1

        def busy_count(self):
            return self.count

        def stop(self):
            pass

    core.activity = Busy()
    restarts = []
    server = api_server.ApiServer(core, "com.example.Restart", on_restart=restarts.append)
    path = server.listen()
    try:
        client = Raw(path)
        client.hello()
        idle = client.request({"t": "service.restart", "when": "idle"})
        assert idle["ok"] is True and core.restart_pending
        cancel = client.request({"t": "service.restart", "when": "cancel"})
        assert cancel["ok"] is True and not core.restart_pending
        assert restarts == []
        now = client.request({"t": "service.restart", "when": "now"})
        assert now["ok"] is True
        assert pump(2, lambda: restarts == ["now"])
        client.close()
    finally:
        server.stop()
        core.shutdown()
        pump(0.3)


def test_a_connection_with_no_hello_is_closed_after_the_timeout(served, monkeypatch):
    monkeypatch.setattr(api_server, "HELLO_TIMEOUT_S", 1)
    _server, path = served
    client = Raw(path)
    assert pump(3, lambda: client.closed)
