# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The loopback transport and the service core's pty half
(collins.api.loopback, collins.service.core), against real ptys: every
message validated both ways, the replies and events of the protocol, the
refusals a client sees, and what a client going away leaves behind."""

import os
import sys
import time

import pytest
from gi.repository import GLib

from collins.api import loopback, protocol
from collins.service.core import ServiceCore

CAT = "/bin/cat"


class Client:
    """A client's two callbacks, recorded."""

    def __init__(self):
        self.output = []  # (pty, data, flags)
        self.events = []

    def on_output(self, pty, data, flags):
        self.output.append((pty, bytes(data), flags))

    def on_event(self, event):
        self.events.append(event)

    def live(self, pty):
        return b"".join(d for p, d, f in self.output if p == pty and f == 0)


def pump(seconds=0.5, until=None):
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ctx.iteration(False)
        if until is not None and until():
            return True
        time.sleep(0.005)
    return until() if until is not None else True


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", CAT)
    records = {}
    core = ServiceCore(
        state_dir=tmp_path / "pty",
        record=lambda pty_id, row: records.__setitem__(pty_id, row),
        get_setting=lambda key: True,
    )
    srv = loopback.LoopbackServer(core)
    srv.records = records
    yield srv
    srv.shutdown()
    pump(0.3)


def spawn(client, cwd="/tmp", cols=80, rows=24, kind="agent"):
    reply = client.request({"t": "spawn", "kind": kind, "cwd": cwd, "cols": cols, "rows": rows})
    return reply["pty"]


def test_spawn_attach_type_and_see_the_echo(server):
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty = spawn(ends_client := client)
    reply = ends_client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    assert reply["active"] is True and reply["sized_for"] == "laptop" and reply["cols"] == 80
    redraw = [f for p, d, f in ends.output if p == pty and f & protocol.FLAG_REDRAW]
    assert redraw and redraw[-1] & protocol.FLAG_REDRAW_END
    client.send_input(pty, b"hello\r")
    assert pump(2, lambda: b"hello\r\nhello" in ends.live(pty))
    assert client.screen_of(pty).rows()[0] == "hello"
    assert client.pty_of(pty).child_pid() == server.records[pty]["pid"]
    announce = [e for e in ends.events if e["t"] == "pty"]
    assert announce and announce[-1]["pid"] == client.pty_of(pty).child_pid()
    assert announce[-1]["cwd"] == "/tmp" and announce[-1]["kind"] == "agent"


def child_environ(pid):
    with open(f"/proc/{pid}/environ", "rb") as f:
        return dict(item.split(b"=", 1) for item in f.read().split(b"\0") if b"=" in item)


def test_the_spawn_environment_follows_the_progress_setting(server, tmp_path):
    """The service's own environment (here a bare one, so a test run from
    inside an agent tab, which carries the spoofs itself, proves nothing)
    plus VTE's variables, plus the two progress declarations only while the
    setting is on."""
    bare = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}
    core = ServiceCore(state_dir=tmp_path / "pty1", get_setting=lambda key: True, environment=lambda: bare)
    srv = loopback.LoopbackServer(core)
    ends = Client()
    try:
        client = srv.connect(ends.on_output, ends.on_event)
        pty = spawn(client)
        env = child_environ(client.pty_of(pty).child_pid())
    finally:
        srv.shutdown()
        pump(0.3)
    assert env[b"TERM"] == b"xterm-256color" and env[b"COLORTERM"] == b"truecolor"
    assert env[b"ConEmuANSI"] == b"ON" and env[b"TERM_PROGRAM"] == b"kitty"
    assert env[b"VTE_VERSION"] == b"8400"

    core = ServiceCore(state_dir=tmp_path / "pty2", get_setting=lambda key: False, environment=lambda: bare)
    other = loopback.LoopbackServer(core)
    try:
        c2 = other.connect(ends.on_output, ends.on_event)
        pty2 = spawn(c2)
        env2 = child_environ(c2.pty_of(pty2).child_pid())
        assert b"ConEmuANSI" not in env2 and b"TERM_PROGRAM" not in env2
        assert env2[b"TERM"] == b"xterm-256color"
    finally:
        other.shutdown()
        pump(0.3)


def test_messages_are_validated_both_ways(server):
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event)
    with pytest.raises(ValueError, match="spawn"):
        client.request({"t": "spawn", "kind": "agent"})  # cwd missing
    with pytest.raises(ValueError):
        client.request({"t": "resize", "pty": 1, "cols": 80, "rows": 24})  # an event, not a request
    with pytest.raises(ValueError):
        client.send_event({"t": "resize", "pty": 1})  # cols missing
    with pytest.raises(ValueError):
        client.request({"t": "no-such-type"})
    # A service event the protocol refuses never reaches the callback.
    sink = client.sink_for(1)
    sink.send_event({"t": "pty-exited"})  # pty and status missing
    assert ends.events == []


def test_refusals_carry_the_protocols_error_and_a_msgid(server):
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event)
    with pytest.raises(loopback.RequestRefused) as caught:
        client.request({"t": "attach", "pty": 99, "cols": 80, "rows": 24})
    assert caught.value.error == protocol.ERROR_GONE and caught.value.details == {"pty": 99}
    with pytest.raises(loopback.RequestRefused) as caught:
        client.request({"t": "spawn", "kind": "agent", "cwd": "/nonexistent/dir", "cols": 80, "rows": 24})
    assert caught.value.error == protocol.ERROR_FAILED
    assert "failed to start shell" in caught.value.msgid
    assert "/nonexistent/dir" in caught.value.details["msg"]
    assert server.core.ptys.ptys == {}  # nothing left in the table
    with pytest.raises(loopback.RequestRefused) as caught:
        client.request({"t": "service.status"})
    assert caught.value.error == protocol.ERROR_UNKNOWN


def test_resize_focus_and_theme_events_reach_the_pty_server(server):
    a, b = Client(), Client()
    ca = server.connect(a.on_output, a.on_event, device="a")
    cb = server.connect(b.on_output, b.on_event, device="b")
    pty = spawn(ca)
    ca.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    cb.request({"t": "attach", "pty": pty, "cols": 100, "rows": 30})
    assert server.core.ptys.get(pty).cols == 80  # b is not active: its size waits
    cb.send_event({"t": "focus", "pty": pty, "focused": True})
    assert server.core.ptys.get(pty).cols == 100  # b took the size over
    ca.send_event({"t": "resize", "pty": pty, "cols": 90, "rows": 25})
    assert server.core.ptys.get(pty).cols == 100  # a is not active now
    cb.send_event({"t": "resize", "pty": pty, "cols": 90, "rows": 25})
    assert (server.core.ptys.get(pty).cols, server.core.ptys.get(pty).rows) == (90, 25)
    assert [e for e in a.events if e["t"] == "pty"][-1]["active"] is False
    cb.send_event({"t": "theme", "term": {"vte": 8400, "fg": "#ffffff", "bg": "#000000", "scheme": "dark"}})
    assert server.core.ptys.get(pty).state.dark is True
    # The theme is b's own: b is active, so b's colours answer; a's later
    # theme is remembered as a's and changes nothing for b's pty.
    assert server.core.ptys.get(pty).state.background == (0, 0, 0)
    ca.send_event({"t": "theme", "term": {"bg": "#102030"}})
    assert ca.term == {"bg": "#102030"} and cb.term["bg"] == "#000000"
    assert server.core.ptys.get(pty).state.background == (0, 0, 0)
    ca.send_event({"t": "focus", "pty": pty, "focused": True})
    assert server.core.ptys.get(pty).state.background == (0x1010, 0x2020, 0x3030)


def test_paint_reaches_the_model_and_every_client(server):
    a, b = Client(), Client()
    ca = server.connect(a.on_output, a.on_event)
    cb = server.connect(b.on_output, b.on_event)
    pty = spawn(ca)
    ca.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    cb.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    ca.request({"t": "paint", "pty": pty, "text": "\r\n[note] painted\r\n"})
    assert b"[note] painted" in a.live(pty) and b"[note] painted" in b.live(pty)
    assert "[note] painted" in ca.screen_of(pty).capture_contents()


def test_a_client_going_away_detaches_and_ends_nothing(server):
    a, b = Client(), Client()
    ca = server.connect(a.on_output, a.on_event)
    cb = server.connect(b.on_output, b.on_event)
    pty = spawn(ca)
    ca.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    cb.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    ca.close()
    assert ca.closed and len(server.core.ptys.get(pty).attachments) == 1
    assert server.core.ptys.get(pty).child_pid() is not None
    cb.send_input(pty, b"still\r")
    assert pump(2, lambda: b"still\r\nstill" in b.live(pty))
    with pytest.raises(loopback.RequestRefused):
        ca.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})


def test_close_ends_the_pty_and_every_client_hears_it(server):
    a = Client()
    ca = server.connect(a.on_output, a.on_event)
    pty = spawn(ca)
    ca.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    ca.request({"t": "close", "pty": pty, "mode": "exit"})
    assert pump(3, lambda: any(e["t"] == "pty-exited" for e in a.events))
    exited = [e for e in a.events if e["t"] == "pty-exited"][0]
    assert exited["pty"] == pty and exited["status"] in (-1, 0, None)
    assert pty not in server.core.ptys.ptys and server.records[pty] is None
    assert pty not in ca._sinks  # the sink went with the pty


def test_nothing_in_the_service_or_the_transport_loads_gtk():
    for name in ("collins.api.loopback", "collins.service.core"):
        assert name in sys.modules
    assert not any(m in sys.modules for m in ("gi.repository.Gtk", "gi.repository.Vte"))
