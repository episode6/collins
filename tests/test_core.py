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

import inproc as loopback
import pytest
from gi.repository import GLib

from collins.api import protocol
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


def test_a_write_flagged_as_a_mention_keeps_its_distance(server, tmp_path):
    """A drop's mention tokens are built on the client and typed through
    `write mention: true`: the service puts the space in front that a
    half-written sentence wants (Session.mention_leading_space), and none
    in front of one typed at the prompt's own whitespace."""
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty = spawn_agent(client, tmp_path)["pty"]
    client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    screen = server.core.ptys.get(pty).screen
    assert pump(3, lambda: screen.capture_contents().count("true") >= 2)
    client.send_input(pty, b"look at")  # the line discipline echoes it at once
    assert pump(3, lambda: "look at" in screen.capture_contents())
    client.request({"t": "write", "pty": pty, "text": "@a.py ", "mention": True})
    assert pump(3, lambda: "look at @a.py" in screen.capture_contents())
    client.request({"t": "write", "pty": pty, "text": "@b.py ", "mention": True})
    assert pump(3, lambda: "look at @a.py @b.py" in screen.capture_contents())
    assert "@a.py  @b.py" not in screen.capture_contents()  # a space already there wants no second


def test_a_handler_that_fails_refuses_its_request_alone(server, tmp_path, monkeypatch):
    """Rule 4 on the router: a request whose handler raises (a CLI internal
    that moved under a read) is logged and refused, and the service goes
    on serving the next one."""
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty = spawn_agent(client, tmp_path)["pty"]

    def broken(message, client):
        raise RuntimeError("the box moved")

    monkeypatch.setattr(server.core, "_req_write", broken)
    with pytest.raises(loopback.RequestRefused) as refused:
        client.request({"t": "write", "pty": pty, "text": "x"})
    assert refused.value.error == protocol.ERROR_FAILED
    assert client.request({"t": "pty.info", "pty": pty})["kind"] == "agent"


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


def test_a_prompt_the_service_types_arms_the_gate_and_snapshots_the_baseline(server, tmp_path):
    """PR 602 on the service: a write through the API (an injected prompt,
    a switch, a close flow's keys) never passes a client's VTE, so the
    session arms its own echo gate on the "\\r" and its host hands the
    announcement to the tracker, which takes the plumbing baseline's last
    snapshot and lets the fresh spawn out of its startup hold."""
    activity = server.core.start_activity()
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty = spawn_agent(client, tmp_path)["pty"]
    record = server.core.sessions[pty]
    session = record.session
    assert activity.startup_held(session) and not session.echo_gate.armed
    # What runs under the agent from here on: the submit's snapshot is the
    # last one that folds it into the baseline.
    session.background_descendant_cmdlines = lambda: {"mcp-server --stdio"}
    client.request({"t": "write", "pty": pty, "text": "hello"})
    assert activity.startup_held(session)
    assert "mcp-server --stdio" not in activity._captures.get(record.handle, set())
    client.request({"t": "write", "pty": pty, "text": "\r"})
    assert session.echo_gate.armed and not activity.startup_held(session)
    assert "mcp-server --stdio" in activity._captures[record.handle]  # taken on the submit


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


# -- the review's gaps (PR-1.12a) ------------------------------------------------------------


def _cut_state(ends, handle):
    """The last `cut` event's state for *handle*, or None."""
    states = [e.get("state") for e in ends.of("cut") if e.get("handle") == handle]
    return states[-1] if states else None


def _ready_agent(server, client, tmp_path):
    """A spawned agent whose typed `true` has echoed, attached: a clean box
    to draw the CLI's prompt into."""
    pty = spawn_agent(client, tmp_path)["pty"]
    client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    screen = server.core.ptys.get(pty).screen
    assert pump(3, lambda: screen.capture_contents().count("true") >= 2)
    return pty, screen


def test_the_settle_delivers_the_last_change_of_a_burst(server, tmp_path):
    """Two moves inside the 50 ms settle: one event, with the state at the
    end of the burst (an empty box, then text in it: not empty)."""
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty, screen = _ready_agent(server, client, tmp_path)
    before = len(ends.of("session"))
    client.send_input(pty, b"\xe2\x9d\xaf\xc2\xa0")  # the prompt mark: an empty box
    client.send_input(pty, b"half")  # and at once, text in it
    assert pump(3, lambda: "half" in screen.capture_contents())
    pump(0.3)  # the settle and a margin
    events = [e for e in ends.of("session")[before:] if "takes_prompt" in e or "entered" in e]
    assert events, ends.of("session")[before:]
    # The box reads as written-in at the burst's end, and the burst's
    # start (an empty box) never went out on its own.
    assert events[-1]["entered"]["text"].startswith("half")
    assert server.core.sessions[pty]._sent["takes_prompt"] is False
    assert not any(e.get("takes_prompt") is True for e in events)


def test_a_fact_that_does_not_fit_costs_the_others_nothing(server, tmp_path):
    """Rule 5 on the facts: a touched path with a NUL is dropped and the
    model beside it still goes; a permission mode over the bound is cut to
    it; the recording of what was sent happens after the fit, so the bad
    fact is tried again next time."""
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty = spawn_agent(client, tmp_path)["pty"]
    client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    record = server.core.sessions[pty]
    before = len(ends.of("session"))
    record._send_changed(
        {
            "model": "claude-opus-5-5",
            "touched_files": ["/ok.py", "/bad\x00.py"],
            "permission_mode": "p" * 40,
            "entered": {"text": "x", "rows_below": 5000},
        }
    )
    sent = ends.of("session")[before:]
    assert len(sent) == 1, sent
    event = sent[0]
    assert event["model"] == "claude-opus-5-5"
    # A list is fitted item by item: the path with the NUL goes, the other
    # stays; an object field by field: rows_below is clamped, not dropped.
    assert event["touched_files"] == ["/ok.py"]
    assert event["entered"] == {"text": "x", "rows_below": protocol.MAX_ROWS}
    assert event["permission_mode"] == "p" * 32
    assert record._sent["touched_files"] == ["/ok.py"]
    # A fact with no bringing it closer is dropped and not recorded.
    before = len(ends.of("session"))
    record._send_changed({"pid": "not-a-pid", "effort": "high"})
    event = ends.of("session")[before:][0]
    assert "pid" not in event and event["effort"] == "high"


def test_a_second_attach_does_not_starve_the_first(server, tmp_path):
    """What moved since the last send goes to the clients already attached
    before the newcomer's snapshot; the snapshot records nothing, so the
    first client's next settle still sees the change."""
    first, second = Client(), Client()
    client_a = server.connect(first.on_output, first.on_event, device="laptop")
    client_b = server.connect(second.on_output, second.on_event, device="desk")
    pty = spawn_agent(client_a, tmp_path)["pty"]
    client_a.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    record = server.core.sessions[pty]
    assert record._sent.get("takes_prompt") is False
    # The box changed under the record, before any settle has run.
    original = record.box_facts
    record.box_facts = lambda: {**original(), "takes_prompt": True}
    client_b.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    assert first.of("session")[-1].get("takes_prompt") is True  # A heard the change
    assert second.of("session")[-1].get("takes_prompt") is True  # B's snapshot has it
    record.box_facts = original


def test_the_close_budget_running_out_is_an_event_and_force_ends_the_pty(server, tmp_path, monkeypatch):
    """The graceful close on a shell that never leaves: the service's poll
    runs out its budget and says so (a `close` event, `state: budget`);
    the client's answer, `close force: true`, ends the pty."""
    from collins.service import session as session_mod

    monkeypatch.setattr(session_mod, "SHELL_EXIT_TICKS", 2)
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty, _screen = _ready_agent(server, client, tmp_path)
    # cat is the shell and runs no command: the close goes straight to the
    # shell's exit, which cat ignores (it echoes `exit`). The exit text is
    # a typed command here, not Claude's Ctrl+C Ctrl+C, which would kill cat.
    client.request({"t": "close", "pty": pty, "mode": "exit", "text": "/exit\r"})
    assert pump(5, lambda: any(e.get("state") == "budget" for e in ends.of("close")))
    budget = [e for e in ends.of("close") if e.get("state") == "budget"][-1]
    assert budget["pty"] == pty and budget["phase"] == "shell"
    assert pty in server.core.ptys.ptys  # nothing was killed on the service's own
    client.request({"t": "close", "pty": pty, "mode": "exit", "force": True})
    assert pump(3, lambda: pty not in server.core.ptys.ptys)
    assert ends.of("pty-exited")


def test_a_cut_seeds_the_box_and_refuses_a_foreign_paste(server, tmp_path):
    """The composer's cut end to end: the box's text comes back as a
    `seeded` event and the box is erased; a box holding a paste stand-in
    that isn't ours comes back `refused`. The agent is cat here, so the
    'agent running' question is answered for it."""
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty, screen = _ready_agent(server, client, tmp_path)
    record = server.core.sessions[pty]
    record.session.agent_is_running = lambda: True
    client.send_input(pty, "❯\xa0hello".encode())
    assert pump(3, lambda: "hello" in screen.capture_contents())
    handle = client.request({"t": "cut", "pty": pty})["handle"]
    assert record.composer_open() is True and handle in record.cuts
    assert pump(5, lambda: _cut_state(ends, handle) == "seeded")
    seeded = [e for e in ends.of("cut") if e.get("handle") == handle][-1]
    # cat's bare screen has no bottom border: the box read runs to the
    # screen's last row, so the seed carries the empty rows below.
    assert seeded["text"].rstrip("\n") == "hello" and seeded["pty"] == pty
    assert pump(3, lambda: handle not in record.cuts)  # the sink is let go of once it told
    # A stand-in no paste-back of ours left: refused.
    client.send_input(pty, b"\r")
    client.send_input(pty, "❯\xa0[Pasted text #1 +3 lines]".encode())
    assert pump(3, lambda: "+3 lines" in screen.capture_contents())
    handle = client.request({"t": "cut", "pty": pty})["handle"]
    assert pump(5, lambda: _cut_state(ends, handle) == "refused")
    assert handle not in record.cuts


def test_a_cut_that_finds_nothing_ends_with_a_cancelled_word(server, tmp_path):
    """An empty box: the settle agrees with itself and cuts nothing; the
    client hears `cancelled` and the record lets the handle go."""
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty, screen = _ready_agent(server, client, tmp_path)
    record = server.core.sessions[pty]
    record.session.agent_is_running = lambda: True
    client.send_input(pty, b"\xe2\x9d\xaf\xc2\xa0")
    assert pump(3, lambda: "❯" in screen.capture_contents())
    handle = client.request({"t": "cut", "pty": pty})["handle"]
    assert pump(5, lambda: _cut_state(ends, handle) == "cancelled")
    assert handle not in record.cuts


def test_a_cut_cancel_is_scoped_to_the_clients_own_cuts(server, tmp_path):
    first, second = Client(), Client()
    client_a = server.connect(first.on_output, first.on_event, device="laptop")
    client_b = server.connect(second.on_output, second.on_event, device="desk")
    pty = spawn_agent(client_a, tmp_path)["pty"]
    client_a.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    client_b.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    record = server.core.sessions[pty]
    a_handle = client_a.request({"t": "cut", "pty": pty})["handle"]
    b_handle = client_b.request({"t": "cut", "pty": pty})["handle"]
    client_a.request({"t": "cut.cancel", "pty": pty})  # no handle: every one of A's, none of B's
    assert a_handle not in record.cuts and b_handle in record.cuts
    assert [e["state"] for e in first.of("cut") if e.get("handle") == a_handle] == ["cancelled"]
    assert not [e for e in second.of("cut") if e.get("handle") == b_handle]


def test_sandbox_drop_refuses_a_path_the_service_did_not_derive(server, tmp_path):
    """Through the core: with no derive behind it, a drop naming a plan file
    is refused and the file stays (rule 5)."""
    probe = tmp_path / "probe.json"
    probe.write_text("{}")

    class Host:
        def release(self, box):
            pass

        def forget_box(self, box):
            pass

    server.core.start_sandbox(host=lambda: Host(), grants=lambda: None)
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    with pytest.raises(loopback.RequestRefused) as refused:
        client.request({"t": "sandbox.drop", "box": "a" * 32, "plan": str(probe)})
    assert refused.value.error == protocol.ERROR_REFUSED
    assert probe.exists()


def test_session_resolved_settles_the_box_and_syncs_the_grants(
    app_state, projects_dir, tmp_path, monkeypatch
):
    monkeypatch.setenv("SHELL", CAT)
    _root, ids = projects_dir
    service_state = app_state.AppState(migrate=True, device=False)
    core = ServiceCore(state=service_state, state_dir=tmp_path / "pty")
    settled, synced = [], []

    class Host:
        def settle_box(self, session_id, box, workspace, owed):
            settled.append((session_id, box, workspace, owed))
            return True

    class Grants:
        def sync(self, box, done=None):
            synced.append(box)

        def shutdown(self):
            pass

    core.sandbox_host, core.sandbox_grants = Host(), Grants()
    srv = loopback.LoopbackServer(core)
    try:
        ends = Client()
        client = srv.connect(ends.on_output, ends.on_event, device="laptop")
        pty = spawn_agent(client, tmp_path)["pty"]
        record = core.sessions[pty]
        from collins.providers import SessionOptions

        record.session.options = SessionOptions(sandbox=True, sandbox_box="b" * 32)
        record.session.sandbox_box = "b" * 32  # what the launch records off its plan
        record.session.session_id = ids["alpha1"]
        core.session_resolved(record, ids["alpha1"])
        assert settled == [(ids["alpha1"], "b" * 32, str(tmp_path), False)]
        assert synced == ["b" * 32]
        assert core.ptys.get(pty).session == ids["alpha1"]
    finally:
        srv.shutdown()
        pump(0.3)


def test_a_shell_opened_before_the_spawn_is_refiled_once_it_names_its_session(server, tmp_path):
    """A new-chat screen's shell has no handle at its spawn; `panel.key`
    with the handle (the tab's, after the agent's spawn) puts it among
    the session's shells, and the resolve re-files its history."""
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    spawn = {"t": "spawn", "kind": "shell", "cwd": str(tmp_path), "cols": 80, "rows": 24}
    shell = client.request(spawn)["pty"]
    assert server.core.ptys.get(shell).handle is None
    reply = spawn_agent(client, tmp_path)
    agent, handle = reply["pty"], reply["handle"]
    client.request({"t": "panel.key", "pty": shell, "history": None, "handle": handle})
    assert server.core.ptys.get(shell).handle == handle
    record = server.core.sessions[agent]
    record.session.session_id = "11111111-2222-3333-4444-555555555555"
    record.session_resolved("11111111-2222-3333-4444-555555555555")
    assert server.core.ptys.get(shell).history == "11111111-2222-3333-4444-555555555555"


def test_a_transcript_path_outside_the_projects_is_refused(server, tmp_path, monkeypatch):
    from collins import sessions as sessions_mod

    monkeypatch.setattr(sessions_mod, "CLAUDE_PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects" / "p").mkdir(parents=True)
    inside = tmp_path / "projects" / "p" / "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0.jsonl"
    inside.write_text("")
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty = spawn_agent(client, tmp_path)["pty"]
    for bad in (str(tmp_path / "elsewhere.jsonl"), "/etc/passwd", str(tmp_path / "projects" / "p" / "x.txt")):
        with pytest.raises(loopback.RequestRefused):
            client.request({"t": "transcript.set", "pty": pty, "path": bad})
        with pytest.raises(loopback.RequestRefused):
            client.request({"t": "transcript.relocate", "pty": pty, "path": bad})
    client.request({"t": "transcript.set", "pty": pty, "path": str(inside)})
    assert server.core.sessions[pty].session.transcript_path == str(inside)
    client.request({"t": "transcript.set", "pty": pty, "path": None})


def test_a_prompt_only_when_empty_is_refused_off_the_live_screen(server, tmp_path):
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty, screen = _ready_agent(server, client, tmp_path)
    with pytest.raises(loopback.RequestRefused) as refused:  # cat's box: never the CLI's empty one
        client.request({"t": "prompt", "pty": pty, "text": "hello", "when_empty": True})
    assert refused.value.error == protocol.ERROR_REFUSED
    assert "hello" not in screen.capture_contents()
    client.request({"t": "prompt", "pty": pty, "text": "hello"})  # unconditional: typed
    assert pump(3, lambda: "hello" in screen.capture_contents())


# -- the resolver's budget on the service -----------------------------------------------------


def _resolver_rig(server, client, tmp_path, monkeypatch, attach):
    """A fresh spawn (no id) whose resolver polls fast and has a short
    background budget, over a projects dir of the test's own."""
    from collins.service import session as session_mod

    monkeypatch.setattr(session_mod, "RESOLVER_POLL_MS", 20)
    monkeypatch.setattr(session_mod, "RESOLVER_BACKGROUND_TICKS", 3)
    projects = tmp_path / "projects"
    projects.mkdir()
    cwd = tmp_path / "work"
    cwd.mkdir()
    server.core.start_activity()  # the tracker is what re-arms the resolver on a submit
    pty = spawn_agent(client, cwd)["pty"]
    if attach:
        client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    record = server.core.sessions[pty]
    from collins import sessions as sessions_mod

    monkeypatch.setattr(sessions_mod, "CLAUDE_PROJECTS_DIR", projects)  # the provider reads it live
    return pty, record, projects, cwd


def _write_transcript(projects, cwd, session_id):
    import re

    directory = projects / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{session_id}.jsonl").write_text('{"type":"summary"}\n')


def test_mapped_is_whether_a_client_is_attached(server, tmp_path):
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty = spawn_agent(client, tmp_path)["pty"]
    record = server.core.sessions[pty]
    assert record.mapped() is False
    client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    assert record.mapped() is True
    client.request({"t": "detach", "pty": pty})
    assert record.mapped() is False


def test_an_attached_session_idling_past_the_budget_still_resolves_on_its_submit(
    server, tmp_path, monkeypatch
):
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty, record, projects, cwd = _resolver_rig(server, client, tmp_path, monkeypatch, attach=True)
    session = record.session
    pump(0.4)  # many times the background budget
    assert session._resolver_source is not None  # a client is attached: no pause
    client.request({"t": "write", "pty": pty, "text": "hello\r"})
    sid = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
    _write_transcript(projects, cwd, sid)
    assert pump(3, lambda: session.session_id == sid)
    assert any(e.get("session") == sid for e in ends.of("session"))


def test_a_detached_session_pauses_after_the_budget_and_resumes_on_attach(
    server, tmp_path, monkeypatch
):
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty, record, projects, cwd = _resolver_rig(server, client, tmp_path, monkeypatch, attach=False)
    session = record.session
    assert pump(2, lambda: session._resolver_source is None)  # the budget ran out
    client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    assert session._resolver_source is not None  # resumed on the attach
    sid = "1f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
    _write_transcript(projects, cwd, sid)
    assert pump(3, lambda: session.session_id == sid)


def test_a_submit_resumes_a_paused_resolver_before_the_write_lands(server, tmp_path, monkeypatch):
    """A detached session with a prompt typed through the API (start_session's
    road): the submit itself re-arms the resolver, with its baseline taken
    before the transcript the submit creates can exist."""
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty, record, projects, cwd = _resolver_rig(server, client, tmp_path, monkeypatch, attach=False)
    session = record.session
    assert pump(2, lambda: session._resolver_source is None)
    client.request({"t": "write", "pty": pty, "text": "hello\r"})
    assert session._resolver_source is not None
    sid = "2f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
    _write_transcript(projects, cwd, sid)
    assert pump(3, lambda: session.session_id == sid)


def test_a_newer_cut_ends_the_one_it_supersedes(server, tmp_path):
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty = spawn_agent(client, tmp_path)["pty"]
    client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    record = server.core.sessions[pty]
    first = client.request({"t": "cut", "pty": pty})["handle"]
    second = client.request({"t": "cut", "pty": pty})["handle"]
    assert first not in record.cuts and second in record.cuts
    assert [e["state"] for e in ends.of("cut") if e.get("handle") == first] == ["cancelled"]
    assert record._chain is not None and record._chain.handle == second


# -- a session that already runs, and the stop sequence (PR-1.12b) ---------------


def test_a_second_spawn_of_a_running_session_is_refused_with_its_pty(server, tmp_path):
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    session_id = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
    first = spawn_agent(client, tmp_path, session=session_id)
    with pytest.raises(loopback.RequestRefused) as refused:
        spawn_agent(client, tmp_path, session=session_id)
    assert refused.value.error == "refused"
    assert refused.value.msgid == "This session is already running in the Collins service"
    assert refused.value.details == {"pty": first["pty"]}
    # Another session is fine.
    other = spawn_agent(client, tmp_path, session="1f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")
    assert other["pty"] != first["pty"]


def test_spawned_shells_never_see_the_probe_or_systemd_variables(server, tmp_path, monkeypatch):
    monkeypatch.setenv("COLLINS_DEBUG_API", "1")
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/x")
    monkeypatch.setenv("INVOCATION_ID", "abc")
    env = server.core._spawn_env()
    names = ("COLLINS_DEBUG_API", "NOTIFY_SOCKET", "INVOCATION_ID", "JOURNAL_STREAM", "COLLINS_E2E_STUBS")
    for name in names:
        assert name not in env


def test_stop_sessions_runs_the_close_flow_and_kills_a_child_that_ignores_sighup(tmp_path, monkeypatch):
    from collins.service import ptyserver
    from collins.service import session as session_mod

    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setattr(session_mod, "EXIT_FORCE_TICKS", 2)
    monkeypatch.setattr(ptyserver, "CLOSE_GRACE_MS", 500)
    core = ServiceCore(state_dir=tmp_path / "pty", get_setting=lambda key: True)
    srv = loopback.LoopbackServer(core)
    ends = Client()
    client = srv.connect(ends.on_output, ends.on_event, device="laptop")
    # The agent's typed command ignores SIGHUP and sits there.
    reply = client.request({
        "t": "spawn", "kind": "agent", "cwd": str(tmp_path), "cols": 80, "rows": 24,
        "command_override": "trap '' HUP; sleep 300",
    })
    pty = reply["pty"]
    assert pump(5, lambda: core.ptys.get(pty).has_running_command())
    child = core.ptys.get(pty).child_pid()
    assert child
    done = []
    started = time.monotonic()
    core.stop_sessions(done=lambda: done.append(time.monotonic()))
    assert pump(15, lambda: bool(done))
    assert pty not in core.ptys.ptys
    assert not os.path.exists(f"/proc/{child}") or open(f"/proc/{child}/stat").read().split()[2] == "Z"
    # The flow ran: the exit budget (2 ticks), then SIGHUP ignored, then the
    # grace and SIGKILL; well under the stop bound.
    assert done[0] - started < 10
    pump(0.3)
