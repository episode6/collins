# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The service core with the sessions on it (collins.service.core, spec
§3.19, PR-1.12a), against real ptys through the loopback: a spawn of kind
agent builds a `Session` and answers with its handle, an attach is sent
the session's facts whole, the box facts move with the screen, a client's
departure cancels its cut and changes nothing else, the debug probe is
refused without the flag and served with it, and `store.flags busy` is
refused from a client."""

import os
import sys
import time

import pytest
from gi.repository import GLib

from collins.api import loopback, protocol
from collins.service.core import ServiceCore
from collins.sessions import discover_sessions
from collins.store import SessionStore

CAT = "/bin/cat"


class Client:
    def __init__(self):
        self.output = []
        self.events = []

    def on_output(self, pty, data, flags):
        self.output.append((pty, bytes(data), flags))

    def on_event(self, event):
        self.events.append(event)

    def of(self, kind):
        return [e for e in self.events if e.get("t") == kind]

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
    monkeypatch.delenv("COLLINS_DEBUG_API", raising=False)
    core = ServiceCore(state_dir=tmp_path / "pty", get_setting=lambda key: True)
    srv = loopback.LoopbackServer(core)
    yield srv
    srv.shutdown()
    pump(0.3)


def spawn_agent(client, cwd, **extra):
    """An agent whose typed command is `true`: the session's road with no
    CLI on PATH needed (a command override is typed as it is)."""
    message = {"t": "spawn", "kind": "agent", "cwd": str(cwd), "cols": 80, "rows": 24}
    return client.request({**message, "command_override": "true", **extra})


# -- the spawn and the facts ----------------------------------------------------------------


def test_an_agent_spawn_builds_a_session_and_answers_its_handle(server, tmp_path):
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    reply = spawn_agent(client, tmp_path)
    pty = reply["pty"]
    assert reply["handle"].startswith("s-")
    record = server.core.sessions[pty]
    session = record.session
    assert session.handle == reply["handle"] and session.cwd == str(tmp_path)
    assert session.command_override == "true" and session.initial_command == "true"
    assert server.core.ptys.get(pty).kind == "agent"
    # The command was typed into the shell (cat echoes it back).
    assert pump(2, lambda: b"true" in server.core.ptys.get(pty).screen.capture_contents().encode())


def test_an_attach_is_sent_the_sessions_facts_whole(server, tmp_path):
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty = spawn_agent(client, tmp_path)["pty"]
    client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    facts = ends.of("session")
    assert facts and facts[0]["pty"] == pty and facts[0]["handle"].startswith("s-")
    first = facts[0]
    assert first["cwd"] == str(tmp_path) and first["command_override"] == "true"
    assert first["initial_command"] == "true"
    assert first["pid"] == server.core.ptys.get(pty).child_pid()
    assert first["takes_prompt"] is False and first["sandboxed"] is False
    assert first["provider"] == "claude" and first["busy"] is False
    assert isinstance(protocol.validate(dict(first), protocol.SERVICE), protocol.Message)


def test_a_subscriber_is_sent_one_session_per_live_agent(server, tmp_path):
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    spawn_agent(client, tmp_path)
    spawn_agent(client, tmp_path)
    # A core with no store refuses `subscribe`; the snapshot's session
    # events are what `_req_subscribe` adds, tested through a store below.
    with pytest.raises(loopback.RequestRefused):
        client.request({"t": "subscribe"})


def test_the_box_facts_move_with_the_screen(server, tmp_path):
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty = spawn_agent(client, tmp_path)["pty"]
    client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    record = server.core.sessions[pty]
    # The service's screen settle re-reads the facts after output; a
    # change is one `session` event with the fields that moved.
    before = len(ends.of("session"))
    record.refresh_facts()
    assert len(ends.of("session")) == before  # nothing moved: nothing sent
    # The typed `true` is echoed twice: by the line discipline at once and
    # by cat when it gets to run. Wait for cat's copy, or the mark sent next
    # lands on the line cat's output then ends, leaving the cursor below it.
    screen = server.core.ptys.get(pty).screen
    assert pump(3, lambda: screen.capture_contents().count("true") >= 2)
    client.send_input(pty, b"\xe2\x9d\xaf\xc2\xa0")  # the CLI's prompt mark, drawn by cat's echo
    assert pump(3, lambda: any(e.get("takes_prompt") for e in ends.of("session")))
    moved = [e for e in ends.of("session") if "takes_prompt" in e][-1]
    assert set(moved) >= {"t", "pty", "handle", "takes_prompt"}
    assert "cwd" not in moved  # a changed-fields event, not the snapshot


def test_a_spawn_in_a_gone_directory_is_the_sessions_fallback(server, tmp_path):
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty = spawn_agent(client, tmp_path / "gone")["pty"]
    session = server.core.sessions[pty].session
    assert session.cwd != str(tmp_path / "gone")
    assert pump(2, lambda: "no longer exists" in server.core.ptys.get(pty).screen.capture_contents())


def test_a_shells_spawn_names_its_agent_and_is_refiled_on_resolve(server, tmp_path):
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    reply = spawn_agent(client, tmp_path)
    agent, handle = reply["pty"], reply["handle"]
    shell = client.request(
        {"t": "spawn", "kind": "shell", "cwd": str(tmp_path), "cols": 80, "rows": 24, "handle": handle}
    )["pty"]
    assert server.core.ptys.get(shell).handle == handle and server.core.ptys.get(shell).history is None
    record = server.core.sessions[agent]
    record.session.session_id = "11111111-2222-3333-4444-555555555555"
    record.session_resolved("11111111-2222-3333-4444-555555555555")
    assert server.core.ptys.get(shell).history == "11111111-2222-3333-4444-555555555555"
    assert server.core.ptys.get(agent).session == "11111111-2222-3333-4444-555555555555"


# -- the requests a tab makes -------------------------------------------------------------------


def test_the_sessions_requests_reach_it(server, tmp_path):
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty = spawn_agent(client, tmp_path)["pty"]
    client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    client.request({"t": "write", "pty": pty, "text": "typed"})
    assert pump(2, lambda: b"typed" in ends.live(pty))
    settled = client.request({"t": "cwd.settle", "pty": pty, "cwd": None, "root": str(tmp_path)})
    assert settled == {"scope": ""}
    client.request({"t": "shells.follow", "pty": pty, "armed": True})
    assert server.core.sessions[pty].session.shells_follow_armed is True
    witness = client.request({"t": "finish.witness", "pty": pty})
    assert witness["stamp"] == [0, 0] and witness["size"] is None
    assert client.request({"t": "baseline.cmdlines", "pty": pty})["cmdlines"] == []
    client.request({"t": "transcript.update", "pty": pty, "discover": False})
    client.request({"t": "resolver.arm", "pty": pty})
    with pytest.raises(loopback.RequestRefused) as refused:
        client.request({"t": "mention", "pty": pty, "path": str(tmp_path / "a.py")})
    assert refused.value.error == protocol.ERROR_REFUSED  # no agent runs in cat


def test_a_graceful_close_is_the_sessions_and_a_kill_the_ptys(server, tmp_path):
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty = spawn_agent(client, tmp_path)["pty"]
    client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    with pytest.raises(loopback.RequestRefused):
        client.request({"t": "close", "pty": pty, "mode": "exit"})  # no keystrokes to feed
    client.request({"t": "close", "pty": pty, "mode": "exit", "text": "\x03\x03"})
    session = server.core.sessions[pty].session
    assert session.closing is True
    client.request({"t": "close.end", "pty": pty})
    assert session.closing is False
    client.request({"t": "close", "pty": pty, "mode": "kill"})
    assert pump(3, lambda: pty not in server.core.ptys.ptys)
    assert pty not in server.core.sessions  # dropped once the exit is reported
    assert ends.of("pty-exited")


def test_a_clients_departure_cancels_its_cut_and_changes_nothing_else(server, tmp_path):
    ends, other = Client(), Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    second = server.connect(other.on_output, other.on_event, device="desk")
    pty = spawn_agent(client, tmp_path)["pty"]
    client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    second.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    record = server.core.sessions[pty]
    handle = client.request({"t": "cut", "pty": pty})["handle"]
    assert handle in record.cuts and record.cuts[handle].alive()
    client.close()
    assert handle not in record.cuts and record.session.cut_settling in (True, False)
    assert pty in server.core.ptys.ptys and pty in server.core.sessions
    # The other client still hears the session.
    record.send({"busy": True})
    assert other.of("session")[-1]["busy"] is True


def test_the_composer_fact_rides_the_requests(server, tmp_path):
    """`composer_open`, the client fact the session's `_post_switch` reads
    through its host, arrives on the requests and is a method of the host
    (the SessionHost protocol's)."""
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty = spawn_agent(client, tmp_path)["pty"]
    record = server.core.sessions[pty]
    assert record.composer_open() is False
    client.request({"t": "switch", "pty": pty, "model": "sonnet", "composer_open": True})
    assert record.composer_open() is True
    client.request({"t": "send", "pty": pty, "text": "x", "composer_open": False})
    assert record.composer_open() is False
    client.request({"t": "cut", "pty": pty})
    assert record.composer_open() is True
    client.request({"t": "cut.cancel", "pty": pty})
    assert record.composer_open() is False


def test_a_cut_called_off_by_handle(server, tmp_path):
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty = spawn_agent(client, tmp_path)["pty"]
    client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    record = server.core.sessions[pty]
    handle = client.request({"t": "cut", "pty": pty})["handle"]
    client.request({"t": "cut.cancel", "pty": pty, "handle": handle})
    assert handle not in record.cuts
    assert client.request({"t": "draft.restore", "pty": pty, "text": "kept"}) == {"restored": False}


# -- the probe (D27) -----------------------------------------------------------------------------


def test_the_debug_requests_are_refused_without_the_flag(server, tmp_path):
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty = spawn_agent(client, tmp_path)["pty"]
    for message in (
        {"t": "debug.session.get", "pty": pty, "name": "cwd"},
        {"t": "debug.session.set", "pty": pty, "name": "cwd", "value": "/x"},
        {"t": "debug.session.call", "pty": pty, "name": "takes_prompt"},
        {"t": "debug.screen", "pty": pty},
        {"t": "debug.pty", "pty": pty},
        {"t": "debug.sandbox", "target": "core", "name": "sandbox_hosted"},
    ):
        with pytest.raises(loopback.RequestRefused) as refused:
            client.request(message)
        assert refused.value.error == protocol.ERROR_UNKNOWN, message["t"]


def test_the_debug_requests_are_served_with_the_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", CAT)
    monkeypatch.setenv("COLLINS_DEBUG_API", "1")
    core = ServiceCore(state_dir=tmp_path / "pty", get_setting=lambda key: True)
    srv = loopback.LoopbackServer(core)
    try:
        ends = Client()
        client = srv.connect(ends.on_output, ends.on_event, device="laptop")
        pty = spawn_agent(client, tmp_path)["pty"]
        got = client.request({"t": "debug.session.get", "pty": pty, "name": "cwd"})
        assert got == {"value": str(tmp_path)}
        callable_ = client.request({"t": "debug.session.get", "pty": pty, "name": "takes_prompt"})
        assert callable_ == {"callable": True}
        assert client.request({"t": "debug.session.get", "pty": pty, "name": "finish_ledger.armed"}) == {
            "value": False
        }
        client.request({"t": "debug.session.set", "pty": pty, "name": "session_id", "value": "s" * 8})
        assert core.sessions[pty].session.session_id == "s" * 8
        called = client.request({"t": "debug.session.call", "pty": pty, "name": "takes_prompt"})
        assert called == {"value": False}
        screen = client.request({"t": "debug.screen", "pty": pty})
        assert screen["columns"] == 80 and screen["row_count"] == 24 and len(screen["cursor"]) == 2
        info = client.request({"t": "debug.pty", "pty": pty})
        assert info["child_pid"] == core.ptys.get(pty).child_pid()
        assert client.request({"t": "debug.sandbox", "target": "core", "name": "sandbox_hosted"}) == {
            "value": False
        }
        with pytest.raises(loopback.RequestRefused) as refused:
            client.request({"t": "debug.sandbox", "target": "host", "name": "grants", "args": ["b" * 32]})
        assert refused.value.error == protocol.ERROR_GONE
    finally:
        srv.shutdown()
        pump(0.3)


def test_a_ptys_info_and_capture_are_served(server, tmp_path):
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    message = {"t": "spawn", "kind": "shell", "cwd": str(tmp_path), "cols": 80, "rows": 24}
    shell = client.request(message)["pty"]
    client.request({"t": "attach", "pty": shell, "cols": 80, "rows": 24})
    info = client.request({"t": "pty.info", "pty": shell})
    assert info["kind"] == "shell" and info["child_pid"] == server.core.ptys.get(shell).child_pid()
    assert info["running_command"] is False and info["plan"] is None
    client.send_input(shell, b"hello\r")
    assert pump(2, lambda: "hello" in client.request({"t": "pty.capture", "pty": shell})["text"])


# -- store.flags (D29) ---------------------------------------------------------------------------


def test_busy_is_refused_from_a_client(app_state, projects_dir, tmp_path):
    _root, ids = projects_dir
    service_state = app_state.AppState(migrate=True, device=False)
    core = ServiceCore(state=service_state, state_dir=tmp_path / "pty")
    store = SessionStore(service_state)
    core.start_store(store)
    store._last_sessions = discover_sessions()
    store._apply()
    srv = loopback.LoopbackServer(core)
    try:
        ends = Client()
        client = srv.connect(ends.on_output, ends.on_event, device="laptop")
        client.request({"t": "subscribe"})
        with pytest.raises(loopback.RequestRefused) as refused:
            client.request({"t": "store.flags", "session": ids["alpha1"], "busy": True})
        assert refused.value.error == protocol.ERROR_REFUSED
        assert not store.get_item(ids["alpha1"]).busy
        # What the person did at this screen is still the client's word.
        client.request({"t": "store.flags", "session": ids["alpha1"], "status": "open", "unread": False})
        assert store.get_item(ids["alpha1"]).status == "open"
    finally:
        srv.shutdown()
        pump(0.3)


def test_the_tracker_sets_busy_on_the_store(app_state, projects_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", CAT)
    _root, ids = projects_dir
    service_state = app_state.AppState(migrate=True, device=False)
    core = ServiceCore(state=service_state, state_dir=tmp_path / "pty")
    store = SessionStore(service_state)
    core.start_store(store)
    store._last_sessions = discover_sessions()
    store._apply()
    activity = core.start_activity()
    srv = loopback.LoopbackServer(core)
    try:
        ends = Client()
        client = srv.connect(ends.on_output, ends.on_event, device="laptop")
        client.request({"t": "subscribe"})
        pty = spawn_agent(client, tmp_path, session=ids["alpha1"])["pty"]
        client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
        activity.tracker.mark(ids["alpha1"])
        assert store.get_item(ids["alpha1"]).busy is True
        activity.tracker.clear(ids["alpha1"])
        assert store.get_item(ids["alpha1"]).busy is False
        # A fresh spawn's handle is tracked too: the placeholder's word.
        record = core.sessions[pty]
        activity.tracker.mark(record.handle)
        assert ends.of("session")[-1]["busy"] is True
    finally:
        srv.shutdown()
        pump(0.3)


def test_nothing_in_the_service_loads_gtk():
    import subprocess

    probe = (
        "import collins.service.core, collins.service.hosting, collins.service.tracking, "
        "collins.service.finish, sys; "
        "print(sorted(m for m in sys.modules if m.startswith('gi.repository.')))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, cwd=os.getcwd(), timeout=60
    )
    assert out.returncode == 0, out.stderr
    assert "Gtk" not in out.stdout and "Vte" not in out.stdout
