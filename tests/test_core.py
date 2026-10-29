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

from collins import proctree
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


def test_a_spawn_says_where_it_started_and_the_fallback_is_the_services(server, tmp_path, monkeypatch):
    """D39 (PR-2.8): the client sends the cwd it has and never looks; a
    cwd that is no directory on the service starts the pty in the
    service's home, and the reply and `pty.info` say where it started
    (the pty's row: tests/test_core_ptys.py)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")

    def shell(cwd) -> dict:
        return client.request({"t": "spawn", "kind": "shell", "cwd": str(cwd), "cols": 80, "rows": 24})

    # A directory that is there: the shell starts in it.
    there = shell(project)
    assert there["cwd"] == str(project)
    assert server.core.ptys.get(there["pty"]).cwd == str(project)
    assert client.request({"t": "pty.info", "pty": there["pty"]})["cwd"] == str(project)
    started = server.core.ptys.get(there["pty"])
    assert pump(2, lambda: proctree.process_cwd(started.child_pid()) == str(project))
    # One that is gone, and a file where a directory was asked for: the
    # service's home, said three ways, and the child really is there.
    (tmp_path / "a-file").write_text("x")
    for missing in (tmp_path / "gone", tmp_path / "a-file"):
        reply = shell(missing)
        pty = server.core.ptys.get(reply["pty"])
        assert reply["cwd"] == str(home) == pty.cwd
        assert client.request({"t": "pty.info", "pty": reply["pty"]})["cwd"] == str(home)
        assert pump(2, lambda pty=pty: proctree.process_cwd(pty.child_pid()) == str(home))
    # An agent's: the session's own fallback (the repository of a worktree
    # that is gone, else home), reported the same way.
    agent = spawn_agent(client, tmp_path / "gone")
    session = server.core.sessions[agent["pty"]].session
    assert agent["cwd"] == session.cwd == str(home)
    kept = spawn_agent(client, project)
    assert kept["cwd"] == str(project) == server.core.sessions[kept["pty"]].session.cwd


def test_spawn_cwd_is_the_directory_or_the_services_home(tmp_path, monkeypatch):
    from collins.service.core import spawn_cwd

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert spawn_cwd(str(tmp_path)) == str(tmp_path)
    for bad in (str(tmp_path / "gone"), "", None, 7):
        assert spawn_cwd(bad) == str(tmp_path / "home")
    link = tmp_path / "link"
    link.symlink_to(tmp_path)  # a link to a directory is one (the shell `cd`s through it)
    assert spawn_cwd(str(link)) == str(link)


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


def test_a_cut_whose_text_would_not_frame_is_refused_before_the_erase(server, tmp_path, monkeypatch):
    """D55 over the loopback: a box whose text, expanded, is over the `cut`
    event's byte budget is not cut. The `cut` request is refused with a
    msgid the client translates, the pty receives no erase keystroke, no
    `cut` event is sent, and the box still holds its text; the composer is
    open (empty) over it as the service sees it. A box that fits is cut."""
    from collins.service.session import CUT_TOO_LARGE_MSGID

    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty, screen = _ready_agent(server, client, tmp_path)
    record = server.core.sessions[pty]
    record.session.agent_is_running = lambda: True
    client.send_input(pty, "❯\xa0hello there".encode())
    assert pump(3, lambda: "hello there" in screen.capture_contents())
    writes = []
    monkeypatch.setattr(server.core.ptys, "write", lambda pty_id, data: writes.append(data))
    monkeypatch.setattr(protocol, "EVENT_TEXT_BYTES", 8)  # "hello there" is more than the frame may carry
    with pytest.raises(loopback.RequestRefused) as refused:
        client.request({"t": "cut", "pty": pty})
    assert refused.value.error == protocol.ERROR_REFUSED and refused.value.msgid == CUT_TOO_LARGE_MSGID
    pump(0.5)
    assert writes == [] and not record.cuts and not ends.of("cut")
    assert "hello there" in screen.capture_contents()
    assert record.composer_open() is True  # open, empty, over the intact box
    # With room for it, the same box is cut.
    monkeypatch.setattr(protocol, "EVENT_TEXT_BYTES", protocol.MAX_FRAME - 4096)
    handle = client.request({"t": "cut", "pty": pty})["handle"]
    assert pump(5, lambda: _cut_state(ends, handle) == "seeded")
    assert writes, "the erase keystrokes went to the pty"


def test_a_stale_cuts_cancel_by_handle_leaves_the_newer_cut_alive(server, tmp_path):
    """The tab calls a stale seed's cut off by its handle (the unknown-
    handle stash of `_on_cut_event`): a cancel naming a handle the record
    has let go of ends nothing, least of all the chain a newer composer's
    cut is running; a cancel naming the live cut ends that one."""
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty = spawn_agent(client, tmp_path)["pty"]
    client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    record = server.core.sessions[pty]
    stale = client.request({"t": "cut", "pty": pty})["handle"]
    newer = client.request({"t": "cut", "pty": pty})["handle"]  # supersedes: `stale` is over
    assert stale not in record.cuts and record._chain.handle == newer
    client.request({"t": "cut.cancel", "pty": pty, "handle": stale})
    assert newer in record.cuts and record._chain is not None and record._chain.handle == newer
    assert _cut_state(ends, newer) is None  # the newer cut was told nothing
    client.request({"t": "cut.cancel", "pty": pty, "handle": newer})
    assert newer not in record.cuts and record._chain is None
    assert _cut_state(ends, newer) == "cancelled"


def test_a_stash_with_a_lone_surrogate_still_reaches_the_client(server, tmp_path):
    """The stash's byte fit runs inside the event's try: a text the measure
    could once not take (a lone surrogate) is still told, as far as the
    protocol carries it, rather than lost with an exception."""
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty = spawn_agent(client, tmp_path)["pty"]
    client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    record = server.core.sessions[pty]
    record.stash_draft("kept \ud800 draft")  # must not raise
    record.stash_draft("kept draft")
    assert [e["text"] for e in ends.of("composer") if e.get("what") == "stash"][-1] == "kept draft"


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


# -- the box a sandboxed resume or fork runs in (should-fix 6) ----------------------------------


class FakeBoxHost:
    """The SandboxHost half `_box_for_launch` asks: boxes minted, tool
    switches copied, every call recorded."""

    def __init__(self):
        self.calls = []
        self.minted = 0

    def mint_box(self, workspace, seed=True):
        self.minted += 1
        box = f"{self.minted:032x}"
        self.calls.append(("mint", workspace, seed, box))
        return box

    def copy_tools(self, source, box, only_off=False):
        self.calls.append(("copy_tools", source, box))


def _core_with_state(app_state, tmp_path):
    service_state = app_state.AppState(migrate=True, device=False)
    core = ServiceCore(state=service_state, state_dir=tmp_path / "pty")
    core.sandbox_host = FakeBoxHost()
    return core, service_state


def test_a_sandboxed_fork_gets_a_box_of_its_own_seeded_from_its_origin(app_state, tmp_path):
    """What MainWindow.open_session minted before the sessions were the
    service's: a fork's box is minted unseeded (`seed=False`: no project
    defaults) and starts with a copy of the origin's grants and tool
    switches; the origin keeps its own box."""
    from collins import providers

    core, state = _core_with_state(app_state, tmp_path)
    origin_id, origin_box = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0", "a" * 32
    state.set_sandboxed(origin_id, True, box=origin_box)
    state.set_sandbox_grants(origin_box, ["/srv/data", "/opt/tools"])
    options = providers.SessionOptions(sandbox=True)
    message = {"session": origin_id, "fork": True, "cwd": str(tmp_path), "sandbox": True}
    forked = core._box_for_launch(message, options)
    box = forked.sandbox_box
    assert box and box != origin_box
    assert core.sandbox_host.calls == [
        ("mint", str(tmp_path), False, box),
        ("copy_tools", origin_box, box),
    ]
    assert state.get_sandbox_grants(box) == ["/srv/data", "/opt/tools"]
    assert state.get_sandbox_grants(origin_box) == ["/srv/data", "/opt/tools"]
    assert state.sandbox_box(origin_id) == origin_box  # the fork's id is the resolver's to record
    assert forked.sandbox is True


def test_a_sandboxed_resume_with_no_box_yet_is_minted_one_and_recorded(app_state, tmp_path):
    """A session recorded before the boxes (or back from the trash) gets a
    box seeded with its project's defaults, recorded against its id; a
    resume whose box is known keeps it and mints nothing; a fresh session
    (no id) is left to the launch's own minting."""
    from collins import providers

    core, state = _core_with_state(app_state, tmp_path)
    session_id = "1f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
    options = providers.SessionOptions(sandbox=True)
    resumed = core._box_for_launch({"session": session_id, "cwd": str(tmp_path)}, options)
    box = resumed.sandbox_box
    assert box and core.sandbox_host.calls == [("mint", str(tmp_path), True, box)]
    assert state.sandbox_box(session_id) == box
    # Known: no mint, the recorded box.
    again = core._box_for_launch({"session": session_id, "cwd": str(tmp_path)}, options)
    assert again.sandbox_box == box and len(core.sandbox_host.calls) == 1
    # Fresh: nothing to look up, the options untouched.
    fresh = core._box_for_launch({"cwd": str(tmp_path)}, options)
    assert fresh is options and len(core.sandbox_host.calls) == 1


# -- the spawn's roads (the Fable review of PR 603, should-fix 4 and nit 12) ------------------


def test_a_spawn_naming_a_transcript_outside_the_projects_is_refused(server, tmp_path, monkeypatch):
    """Probe P1 of the review: `transcript.set` refused an outside path
    and `spawn` tailed it. The third road a client names a transcript by
    is confined like the other two; a spawn under the projects dir goes."""
    from collins import sessions as sessions_mod

    monkeypatch.setattr(sessions_mod, "CLAUDE_PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects" / "p").mkdir(parents=True)
    inside = tmp_path / "projects" / "p" / "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0.jsonl"
    inside.write_text("")
    outside = tmp_path / "outside.jsonl"
    outside.write_text("")
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    for bad in (str(outside), "/etc/passwd", str(tmp_path / "projects" / "p" / "x.txt")):
        with pytest.raises(loopback.RequestRefused) as refused:
            spawn_agent(client, tmp_path, session="0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0", jsonl_path=bad)
        assert refused.value.error == protocol.ERROR_REFUSED
    assert not server.core.sessions and not server.core.ptys.ptys
    pty = spawn_agent(
        client, tmp_path, session="0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0", jsonl_path=str(inside)
    )["pty"]
    assert server.core.sessions[pty].session.transcript_path == str(inside)


def test_a_spawn_that_fails_after_its_pty_is_live_closes_the_pty(server, tmp_path, monkeypatch):
    """Nit 12: an exception in the session's set-up after `spawn_agent_pty`
    registered the pty (the resolver, the new-chat poll, the tracker) is
    the router's `failed`; the live pty is closed rather than left in
    `sessions` with no client ever told to attach or close it."""
    from collins.service import session as session_mod

    def broken(self):
        raise RuntimeError("the poll's timer")

    monkeypatch.setattr(session_mod.Session, "start_new_chat_prompt_poll", broken)
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    with pytest.raises(loopback.RequestRefused) as refused:
        spawn_agent(client, tmp_path, prompt="hello")
    assert refused.value.error == protocol.ERROR_FAILED
    assert pump(3, lambda: not server.core.ptys.ptys)  # SIGHUP'd, reaped, its row gone
    assert not server.core.sessions


# -- the attach snapshot's busy and landed (the Fable review of PR 603, should-fix 2 and 3) --


def test_a_client_attaching_mid_turn_is_told_busy_as_it_stands(server, tmp_path):
    """Probe P2 of the review: the tracker's mark reached the attached
    client, but a second client's snapshot said idle, because the verdict
    bypassed the record's sent facts. A tab attaching to a live session
    mid-turn (PR 605) would show an idle pole until the next edge."""
    activity = server.core.start_activity()
    first = Client()
    client = server.connect(first.on_output, first.on_event, device="laptop")
    pty = spawn_agent(client, tmp_path)["pty"]
    client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    record = server.core.sessions[pty]
    activity.tracker.mark(record.handle)
    assert first.of("session")[-1]["busy"] is True
    assert activity.tracker.is_busy(record.handle)
    second = Client()
    other = server.connect(second.on_output, second.on_event, device="desk")
    other.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    assert second.of("session")[0]["busy"] is True
    activity.tracker.clear(record.handle)
    assert first.of("session")[-1]["busy"] is False and second.of("session")[-1]["busy"] is False
    third = Client()
    late = server.connect(third.on_output, third.on_event, device="phone")
    late.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    assert third.of("session")[0]["busy"] is False


def test_a_client_attaching_after_a_landing_is_told_landed(server, tmp_path):
    """Should-fix 3: the tab acts on the transcript facts (model label, PR
    chips, agent files, attachments) only under `landed`, which went to
    the clients attached at the landing alone. The snapshot carries it
    once a read has landed, with `lookup_empty` as the last landing said,
    and drops it again on a reset."""
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty = spawn_agent(client, tmp_path)["pty"]
    record = server.core.sessions[pty]
    assert "landed" not in record.snapshot()
    client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    assert "landed" not in ends.of("session")[0]
    record.session.has_running_command = lambda: True  # a detach from a shell alone would close it
    client.request({"t": "detach", "pty": pty})
    record.transcript_landed([], True)
    snapshot = record.snapshot()
    assert snapshot["landed"] is True and snapshot["lookup_empty"] is True
    client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    attached = [e for e in ends.of("session") if "cwd" in e][-1]  # the snapshot, not a changed-fields event
    assert attached["landed"] is True and attached["lookup_empty"] is True
    assert isinstance(protocol.validate(dict(attached), protocol.SERVICE), protocol.Message)
    record.transcript_reset()
    assert "landed" not in record.snapshot() and record.snapshot()["lookup_empty"] is False
    record.transcript_landed([], False)
    assert record.snapshot()["landed"] is True and record.snapshot()["lookup_empty"] is False


def test_a_landing_whose_fact_does_not_fit_still_goes(server, tmp_path):
    """Nit 13: `transcript_landed` and `transcript_reset` route the facts
    through `_fit` as `refresh_facts` does, so one fact that doesn't fit
    the protocol (a touched path with a NUL) is dropped alone and the
    landing still reaches the client."""
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty = spawn_agent(client, tmp_path)["pty"]
    client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    record = server.core.sessions[pty]
    record.session.transcript.touched_files = lambda: ["/ok.py", "/bad\0.py"]
    record.session.transcript.model = lambda: "claude-opus-5-5"
    before = len(ends.of("session"))
    record.transcript_landed([], False)
    landed = [e for e in ends.of("session")[before:] if e.get("landed")]
    assert landed and landed[-1]["model"] == "claude-opus-5-5"
    assert landed[-1]["touched_files"] == ["/ok.py"]
    record.transcript_reset()
    reset = [e for e in ends.of("session") if e.get("reset")]
    assert reset and reset[-1]["touched_files"] == ["/ok.py"]


# -- the events frame (the Fable review of PR 603, should-fix 1) ------------------------------


def test_the_snapshot_previews_the_held_prompt_and_leaves_the_paste_back_record_out(server, tmp_path):
    """A `session` event of 256 x TEXT_MAX (the paste-back record) or a
    TEXT_MAX `new_chat_prompt` could pass MAX_FRAME, and the socket's
    sender would drop it whole. The prompt goes as a PREVIEW_MAX preview
    and the record stays on the service, where the checks probe it."""
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty = spawn_agent(client, tmp_path, prompt="p" * (protocol.PREVIEW_MAX * 3))["pty"]
    record = server.core.sessions[pty]
    record.session.pasted_back = {"[Pasted text #1 +2 lines]": "x" * 100_000}
    record.session.paste_back_pending = ["y" * 100_000]
    client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    first = ends.of("session")[0]
    assert first["new_chat_prompt"] == "p" * protocol.PREVIEW_MAX
    assert "pasted_back" not in first and "paste_back_pending" not in first
    assert len(protocol.encode(first).encode("utf-8")) <= protocol.MAX_FRAME
    # The probe still reads the record off the session itself.
    assert record.session.paste_back_pending == ["y" * 100_000]


def test_a_cuts_seed_is_told_whole():
    """D55: the seed is never cut back — a text that would not frame is
    refused before the erase (`cut_fits_now`), so what reaches the sink is
    the box's text whole; the record's `seed_fits` is the frame's budget."""
    from collins.service import hosting

    class Sink:
        events: list = []

        def send_event(self, event):
            self.events.append(event)

    class Record:
        pty_id = 7
        cuts: dict = {}

    sink = Sink()
    hosting.CutSink(Record(), sink, "cut-2")._tell("seeded", "a" * 200_000)
    assert sink.events[-1]["text"] == "a" * 200_000
    assert len(protocol.encode(sink.events[-1]).encode("utf-8")) <= protocol.MAX_FRAME


def test_the_transcript_lists_are_cut_to_their_byte_budgets(server, tmp_path):
    from collins import attachrecords

    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    pty = spawn_agent(client, tmp_path)["pty"]
    record = server.core.sessions[pty]
    attachments = [
        attachrecords.Attachment(key=f"/x/{i}/" + "a" * 4000, kind="image", caption="c" * 4000)
        for i in range(1000)
    ]
    record.session.transcript.attachments = lambda: attachments
    record.session.transcript.touched_files = lambda: [f"/t/{i}/" + "f" * 4000 for i in range(64)]
    facts = record.transcript_facts()
    for name, budget in protocol.SESSION_LIST_BYTES.items():
        assert protocol.json_bytes(facts[name]) <= budget, name
    assert 0 < len(facts["attachments"]) < 1000 and 0 < len(facts["touched_files"]) < 64
    client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    assert len(protocol.encode(ends.of("session")[0]).encode("utf-8")) <= protocol.MAX_FRAME

# -- a session that already runs, and the stop sequence (PR-1.12b) ---------------


def test_a_second_spawn_of_a_running_session_is_refused_with_its_pty(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/sh")  # a real shell: the typed command runs
    core = ServiceCore(state_dir=tmp_path / "pty", get_setting=lambda key: True)
    server = loopback.LoopbackServer(core)
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    session_id = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
    first = spawn_agent(client, tmp_path, session=session_id, command_override="sleep 300")
    assert pump(5, lambda: server.core.ptys.get(first["pty"]).has_running_command())
    with pytest.raises(loopback.RequestRefused) as refused:
        spawn_agent(client, tmp_path, session=session_id)
    assert refused.value.error == "refused"
    assert refused.value.msgid == "This session is already running in the Collins service"
    assert refused.value.details == {"pty": first["pty"]}
    # Another session is fine.
    other = spawn_agent(client, tmp_path, session="1f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")
    assert other["pty"] != first["pty"]
    server.shutdown()
    pump(0.3)


def test_a_fork_of_a_running_session_is_spawned_not_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/sh")
    core = ServiceCore(state_dir=tmp_path / "pty", get_setting=lambda key: True)
    server = loopback.LoopbackServer(core)
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    session_id = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
    origin = spawn_agent(client, tmp_path, session=session_id, command_override="sleep 300")
    assert pump(5, lambda: core.ptys.get(origin["pty"]).has_running_command())
    fork = spawn_agent(client, tmp_path, session=session_id, fork=True, command_override="sleep 300")
    assert fork["pty"] != origin["pty"]
    assert pump(5, lambda: core.ptys.get(fork["pty"]).has_running_command())
    assert origin["pty"] in core.ptys.ptys  # the origin's pty is not closed for it
    # The fork's pty names no session in the table: no stand-in for its origin.
    assert core.agent_pty_sessions()[fork["pty"]] is None
    server.shutdown()
    pump(0.3)


def test_opening_the_origin_while_its_fork_runs_is_not_the_forks_pty(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/sh")
    core = ServiceCore(state_dir=tmp_path / "pty", get_setting=lambda key: True)
    server = loopback.LoopbackServer(core)
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    session_id = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
    fork = spawn_agent(client, tmp_path, session=session_id, fork=True, command_override="sleep 300")
    assert pump(5, lambda: core.ptys.get(fork["pty"]).has_running_command())
    origin = spawn_agent(client, tmp_path, session=session_id)
    assert origin["pty"] != fork["pty"]
    assert fork["pty"] in core.ptys.ptys  # neither refused with nor closed for
    server.shutdown()
    pump(0.3)


def test_a_session_whose_cli_exited_leaves_the_table(app_state, projects_dir, tmp_path, monkeypatch):
    """The shell alone on an agent pty is no running session: its row
    leaves the table (a table `pty-exited`, the pty itself alive) and the
    item's `running` goes off; the CLI coming back puts it back."""
    monkeypatch.setenv("SHELL", "/bin/sh")
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
        pty = spawn_agent(client, tmp_path, session=ids["alpha1"], command_override="sleep 2")["pty"]
        record = core.sessions[pty]
        assert pump(5, lambda: record.session.has_running_command())
        record.refresh_process_facts()
        assert core.running_sessions() == {ids["alpha1"]}
        assert pump(6, lambda: not record.session.has_running_command())
        record.refresh_process_facts()
        assert pty in core.ptys.ptys  # the shell lives on
        assert core.running_sessions() == set()
        assert any(e.get("table") and e["pty"] == pty and e["status"] is None for e in ends.of("pty-exited"))
        moved = [e for e in ends.of("item") if e["session"] == ids["alpha1"] and "running" in e]
        assert moved[-1]["running"] is False
        assert core.table_row(pty) is None
    finally:
        srv.shutdown()
        pump(0.3)


def test_spawned_shells_never_see_the_probe_or_systemd_variables(server, tmp_path, monkeypatch):
    monkeypatch.setenv("COLLINS_DEBUG_API", "1")
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/x")
    monkeypatch.setenv("INVOCATION_ID", "abc")
    env = server.core._spawn_env()
    names = ("COLLINS_DEBUG_API", "NOTIFY_SOCKET", "INVOCATION_ID", "JOURNAL_STREAM", "COLLINS_E2E_STUBS")
    for name in names:
        assert name not in env


def _live_in_group(pgrp: int) -> list[int]:
    """The pids of the processes in *pgrp* that are not zombies."""
    found = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat") as fh:
                stat = fh.read()
        except OSError:
            continue
        fields = stat.rsplit(")", 1)[1].split()
        if len(fields) > 3 and fields[0] != "Z" and int(fields[2]) == pgrp:
            found.append(int(name))
    return found


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
    # The agent's typed command ignores HUP, INT and TERM in a process
    # group of its own (the shell's job control) and sits there.
    reply = client.request({
        "t": "spawn", "kind": "agent", "cwd": str(tmp_path), "cols": 80, "rows": 24,
        "command_override": "sh -c 'trap \"\" HUP INT TERM; while :; do sleep 1; done'",
    })
    pty = reply["pty"]
    assert pump(5, lambda: core.ptys.get(pty).has_running_command())
    child = core.ptys.get(pty).child_pid()
    assert child
    job = core.ptys.get(pty).foreground_pgrp()
    assert job and job != child
    done = []
    started = time.monotonic()
    core.stop_sessions(done=lambda: done.append(time.monotonic()))
    assert pump(15, lambda: bool(done))
    assert pty not in core.ptys.ptys
    assert not os.path.exists(f"/proc/{child}") or open(f"/proc/{child}/stat").read().split()[2] == "Z"
    # The job's own group went too: nothing alive is left in it (a
    # container with no reaper as pid 1 keeps the killed ones as zombies,
    # which `killpg(pgrp, 0)` would still find).
    assert pump(5, lambda: not _live_in_group(job)), _live_in_group(job)
    # The flow ran: the exit budget (2 ticks), then SIGHUP ignored, then the
    # grace and SIGKILL; well under the stop bound.
    assert done[0] - started < 10
    pump(0.3)


def _closing_server(tmp_path, monkeypatch):
    """A core on /bin/sh with the force budgets cut to two ticks and a
    short kill grace, and an attached agent whose typed command ignores
    Ctrl+C in a group of its own: the graceful close can only run out."""
    from collins.service import ptyserver
    from collins.service import session as session_mod

    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setattr(session_mod, "EXIT_FORCE_TICKS", 2)
    monkeypatch.setattr(session_mod, "BG_FORCE_TICKS", 2)
    monkeypatch.setattr(session_mod, "EXIT_NUDGE_TICKS", ())
    monkeypatch.setattr(session_mod, "BG_NUDGE_TICKS", ())
    monkeypatch.setattr(ptyserver, "CLOSE_GRACE_MS", 500)
    core = ServiceCore(state_dir=tmp_path / "pty", get_setting=lambda key: True)
    srv = loopback.LoopbackServer(core)
    ends = Client()
    client = srv.connect(ends.on_output, ends.on_event, device="laptop")
    reply = client.request({
        "t": "spawn", "kind": "agent", "cwd": str(tmp_path), "cols": 80, "rows": 24,
        "command_override": "sh -c 'trap \"\" INT; exec sleep 300'",
    })
    pty = reply["pty"]
    client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    assert pump(5, lambda: core.ptys.get(pty).has_running_command())
    return srv, core, client, ends, pty


@pytest.mark.parametrize("mode", ["exit", "background"])
@pytest.mark.parametrize("leave", ["client_gone", "detach"])
def test_a_close_budget_with_no_client_attached_ends_the_pty(tmp_path, monkeypatch, mode, leave):
    """PR 605's review, must-fix 1 (the reviewer's probe): a graceful close
    whose client went away mid-drain — the tab detached, the quit insisted
    on, the client crashed. The budget used to be told to the attachments
    alone, so with none the poll ended and the CLI the user asked to stop
    ran on for good. The service now forces the close itself: the pty and
    its child are gone after the budget, the record's `closing` is False.
    For the exit flow and the /bg handoff's alike."""
    srv, core, client, ends, pty = _closing_server(tmp_path, monkeypatch)
    try:
        child = core.ptys.get(pty).child_pid()
        job = core.ptys.get(pty).foreground_pgrp()
        record = core.sessions[pty]
        client.request({"t": "close", "pty": pty, "mode": mode, "text": "\x03\x03"})
        assert record.session.closing
        if leave == "client_gone":
            client.close()
        else:
            client.request({"t": "detach", "pty": pty})
        assert not core.ptys.get(pty).attachments
        assert pump(10, lambda: pty not in core.ptys.ptys), "the pty outlived the budget"
        assert record.session.closing is False
        assert pump(5, lambda: not _live_in_group(job)), _live_in_group(job)
        assert not os.path.exists(f"/proc/{child}") or open(f"/proc/{child}/stat").read().split()[2] == "Z"
        assert not any(e.get("state") == "budget" for e in ends.of("close"))
    finally:
        srv.shutdown()
        pump(0.3)


def test_a_close_budget_told_to_a_client_that_then_leaves_is_forced(tmp_path, monkeypatch):
    """The budget ran out with the client attached (the `close` event went
    to it, the window would force the close) and the client detached
    before answering: the last detach is what forces it."""
    srv, core, client, ends, pty = _closing_server(tmp_path, monkeypatch)
    try:
        record = core.sessions[pty]
        client.request({"t": "close", "pty": pty, "mode": "exit", "text": "\x03\x03"})
        assert pump(5, lambda: any(e.get("state") == "budget" for e in ends.of("close")))
        pump(0.5)
        assert pty in core.ptys.ptys and record.session.closing and record.budget_spent
        client.request({"t": "detach", "pty": pty})
        assert pump(5, lambda: pty not in core.ptys.ptys)
        assert record.session.closing is False and record.budget_spent is False
    finally:
        srv.shutdown()
        pump(0.3)


def test_the_shells_exit_ends_a_close_in_flight(tmp_path, monkeypatch):
    """Nit 11: `closing` stayed True on a record whose shell had exited
    mid-close (harmless, misleading in a dump); `shell_exited` ends it."""
    srv, core, client, ends, pty = _closing_server(tmp_path, monkeypatch)
    try:
        record = core.sessions[pty]
        client.request({"t": "close", "pty": pty, "mode": "exit", "text": "\x03\x03"})
        assert record.session.closing
        core.ptys.close(pty)  # the pty ends under the close, as a kill would
        assert pump(5, lambda: pty not in core.ptys.ptys)
        assert record.session.closing is False
    finally:
        srv.shutdown()
        pump(0.3)


def _orphan_rig(tmp_path, monkeypatch, hold_s=1.5, shell="/bin/sh"):
    """A core on *shell* with the shell-alone startup hold cut to *hold_s*
    and a short kill grace; the caller spawns."""
    from collins.service import hosting, ptyserver

    monkeypatch.setenv("SHELL", shell)
    monkeypatch.setattr(ptyserver, "CLOSE_GRACE_MS", 500)
    monkeypatch.setattr(hosting, "ORPHAN_HOLD_S", hold_s)
    core = ServiceCore(state_dir=tmp_path / "pty", get_setting=lambda key: True)
    srv = loopback.LoopbackServer(core)
    ends = Client()
    client = srv.connect(ends.on_output, ends.on_event, device="laptop")
    return srv, core, client, ends


@pytest.mark.parametrize("leave", ["detach", "client_gone"])
def test_a_shell_alone_nobody_shows_is_closed_at_the_holds_end(tmp_path, monkeypatch, leave):
    """PR 605's verification: a CLI that never came up (no `claude`, a bad
    `claude_cli_path`, a failed sandbox launch) left a shell the client's
    `cli_exited()` could not tell from a CLI still starting, so a plain
    Quit detached it and recorded it; on the service it was a shell alone
    with no attachment and no running row, closed only by a resume of that
    session, so it was orphaned for the service's life. The last client's
    detach (or its departure) from an agent pty whose CLI is not running
    closes the pty — after the startup hold, since a CLI never seen running
    may still be the shell's rc before it forks the typed command."""
    srv, core, client, ends = _orphan_rig(tmp_path, monkeypatch)
    try:
        # The typed command is `true`: the "CLI" never comes up, the shell is alone.
        pty = spawn_agent(client, tmp_path)["pty"]
        client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
        screen = core.ptys.get(pty).screen
        assert pump(3, lambda: "true" in screen.capture_contents())
        assert not core.ptys.get(pty).has_running_command()
        record = core.sessions[pty]
        if leave == "detach":
            client.request({"t": "detach", "pty": pty})
        else:
            client.close()
        assert record._orphan_recheck is not None  # held, one re-check scheduled
        pump(0.8)
        assert pty in core.ptys.ptys  # inside the hold: kept
        assert pump(5, lambda: pty not in core.ptys.ptys), "the shell alone outlived the hold"
        assert pump(3, lambda: pty not in core.sessions)
    finally:
        srv.shutdown()
        pump(0.3)


def test_a_launch_whose_cli_comes_up_inside_the_hold_is_kept(tmp_path, monkeypatch):
    """The window the hold is for: a detach (or a client crash) right after
    a new-chat Send, while the shell still runs its rc. `sleep 1; exec sleep
    300` is that shell: no command of its own for a second, then the 'CLI'.
    Detached at once, the launch is kept, and still there after the hold.
    The slow rc is a wrapper shell that sleeps in the shell's own group (no
    job of its own, so `has_running_command` is False, as under a real rc),
    then execs /bin/sh, which runs the typed command."""
    slow = tmp_path / "slowsh"
    slow.write_text('#!/bin/sh\nsleep 1\nexec /bin/sh "$@"\n')
    slow.chmod(0o755)
    srv, core, client, ends = _orphan_rig(tmp_path, monkeypatch, shell=str(slow))
    try:
        pty = spawn_agent(client, tmp_path, command_override="sleep 300")["pty"]
        client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
        client.request({"t": "detach", "pty": pty})
        record = core.sessions[pty]
        assert pty in core.ptys.ptys and record._orphan_recheck is not None
        assert pump(5, lambda: core.ptys.get(pty).has_running_command())
        pump(1.8)  # past the hold's end and its re-check
        assert pty in core.ptys.ptys and record._orphan_recheck is None
        assert record.cli_ever_ran
    finally:
        srv.shutdown()
        pump(0.3)


def test_a_detached_sessions_cli_leaving_by_itself_closes_the_shell(tmp_path, monkeypatch):
    """The other way round: the session is detached while its CLI runs,
    then the CLI quits on its own. The table says so (`pty-exited`, status
    null, `table: true`) and the shell alone goes with it at once — no hold,
    the CLI was seen running; nobody was attached, so nothing else would
    ever close it."""
    srv, core, client, ends = _orphan_rig(tmp_path, monkeypatch, hold_s=600.0)
    try:
        pty = spawn_agent(client, tmp_path, command_override="sleep 2")["pty"]
        client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
        assert pump(5, lambda: core.ptys.get(pty).has_running_command())
        assert core.sessions[pty].cli_ever_ran
        client.request({"t": "detach", "pty": pty})
        pump(0.3)
        assert pty in core.ptys.ptys  # the CLI runs: a detached session, not an orphan
        # (The table's `pty-exited` goes to subscribers; this client is none.)
        assert pump(10, lambda: pty not in core.ptys.ptys), "the shell left by the CLI outlived it"
    finally:
        srv.shutdown()
        pump(0.3)


def test_an_attach_inside_the_hold_cancels_the_recheck(tmp_path, monkeypatch):
    srv, core, client, ends = _orphan_rig(tmp_path, monkeypatch)
    try:
        pty = spawn_agent(client, tmp_path)["pty"]
        client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
        client.request({"t": "detach", "pty": pty})
        record = core.sessions[pty]
        assert record._orphan_recheck is not None
        client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
        assert record._orphan_recheck is None
        pump(2.0)  # past where the hold would have ended
        assert pty in core.ptys.ptys  # shown: kept
    finally:
        srv.shutdown()
        pump(0.3)


def test_a_shown_shell_and_a_closing_one_are_left_alone(server, tmp_path):
    """The rule's edges: a shell alone a client still shows stays (a tab
    at the shell prompt), and a pty mid-close is the close flow's."""
    ends, other = Client(), Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    second = server.connect(other.on_output, other.on_event, device="desk")
    pty, _screen = _ready_agent(server, client, tmp_path)
    second.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    record = server.core.sessions[pty]
    record.cli_ever_ran = True  # past the startup hold's question
    client.request({"t": "detach", "pty": pty})
    pump(0.3)
    assert pty in server.core.ptys.ptys  # the second client shows it
    record.session.closing = True
    second.request({"t": "detach", "pty": pty})
    pump(0.3)
    assert pty in server.core.ptys.ptys  # a close flow owns it
    record.session.closing = False
    assert record.close_if_orphan_shell("the test asks") is True
    assert record.close_if_orphan_shell("asked again") is False  # the master is gone: no second force
    assert pump(3, lambda: pty not in server.core.ptys.ptys)


def test_a_resume_of_a_shell_only_pty_closes_it_and_spawns(server, tmp_path):
    """The CLI exited and the shell sits on the pty (a quit before
    1.12c's attach): the resume is not refused; the old pty is closed and
    a fresh one takes the session."""
    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    session_id = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
    first = spawn_agent(client, tmp_path, session=session_id)  # `true`: the CLI is gone at once
    assert pump(5, lambda: not server.core.ptys.get(first["pty"]).has_running_command())
    second = spawn_agent(client, tmp_path, session=session_id)
    assert second["pty"] != first["pty"]
    assert pump(5, lambda: first["pty"] not in server.core.ptys.ptys)
    assert second["pty"] in server.core.ptys.ptys


# -- the lifecycle (PR-1.12c, §3.21) ----------------------------------------------------


class _Busy:
    """A tracker stand-in: `busy_count` is whatever the test says."""

    def __init__(self, count):
        self.count = count

    def busy_count(self):
        return self.count

    def stop(self):
        pass


def test_a_restart_when_idle_waits_for_no_session_busy(server, monkeypatch):
    core = server.core
    monkeypatch.setattr(core, "RESTART_POLL_MS", 20)
    core.activity = _Busy(2)
    restarted = []
    core.restart_when_idle(lambda: restarted.append(True))
    assert core.restart_pending
    pump(0.2)
    assert restarted == []  # still busy: it waits
    core.activity.count = 0
    assert pump(1, lambda: restarted == [True])
    assert not core.restart_pending
    pump(0.1)
    assert restarted == [True]  # once


def test_a_restart_when_idle_is_called_off_by_cancel(server, monkeypatch):
    core = server.core
    monkeypatch.setattr(core, "RESTART_POLL_MS", 20)
    core.activity = _Busy(1)
    restarted = []
    core.restart_when_idle(lambda: restarted.append(True))
    assert core.cancel_restart() is True
    core.activity.count = 0
    pump(0.2)
    assert restarted == [] and not core.restart_pending
    assert core.cancel_restart() is False  # nothing waits


def test_a_restart_when_idle_with_nothing_busy_restarts_at_once(server):
    core = server.core
    core.activity = _Busy(0)
    restarted = []
    core.restart_when_idle(lambda: restarted.append(True))
    assert restarted == [True] and not core.restart_pending


def test_the_pty_table_names_the_sessions_and_goes_to_subscribers(
    app_state, projects_dir, tmp_path, monkeypatch
):
    monkeypatch.setenv("SHELL", "/bin/sh")  # a real shell: the typed CLI runs
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
        assert all(e.get("running") is False for e in ends.of("item"))
        resumed = spawn_agent(client, tmp_path, session=ids["alpha1"], command_override="sleep 300")["pty"]
        fresh = spawn_agent(client, tmp_path, command_override="sleep 300")["pty"]
        # No row until the CLI runs (the /proc poll's word, read by hand here).
        for pty in (resumed, fresh):
            record = core.sessions[pty]
            assert pump(5, lambda record=record: record.session.has_running_command())
            record.refresh_process_facts()
        rows = {e["pty"]: e for e in ends.of("pty") if e.get("table")}
        assert rows[resumed]["session"] == ids["alpha1"] and rows[resumed]["kind"] == "agent"
        assert "session" not in rows[fresh]  # not resolved yet: no id
        assert core.running_sessions() == {ids["alpha1"]}
        running = [e for e in ends.of("item") if e["session"] == ids["alpha1"] and "running" in e]
        assert running and running[-1]["running"] is True
        # A second subscriber's snapshot carries both rows, table-flagged.
        later = Client()
        other = srv.connect(later.on_output, later.on_event, device="desktop")
        other.request({"t": "subscribe"})
        snapshot = {e["pty"]: e for e in later.of("pty")}
        assert set(snapshot) >= {resumed, fresh} and all(e.get("table") for e in snapshot.values())
        item = [e for e in later.of("item") if e["session"] == ids["alpha1"]][0]
        assert item["running"] is True
        # The exit: the table's word to every subscriber, and running off.
        client.request({"t": "close", "pty": resumed, "mode": "kill"})
        assert pump(8, lambda: any(
            e.get("table") and e["pty"] == resumed for e in ends.of("pty-exited")
        ))
        assert core.running_sessions() == set()

        def last_running():
            moved = [e for e in ends.of("item") if e["session"] == ids["alpha1"] and "running" in e]
            return moved[-1]["running"]

        assert pump(2, lambda: last_running() is False)
        for event in ends.of("pty") + ends.of("pty-exited"):
            assert isinstance(protocol.validate(dict(event), protocol.SERVICE), protocol.Message)
    finally:
        srv.shutdown()
        pump(0.3)


def test_a_restart_now_after_idle_does_not_fire_the_idle_one_too(server, monkeypatch):
    core = server.core
    monkeypatch.setattr(core, "RESTART_POLL_MS", 20)
    core.activity = _Busy(1)
    restarted = []
    core.restart_when_idle(lambda: restarted.append("idle"))
    # `now` cancels the waiting one first (api.server.ApiServer.restart).
    assert core.cancel_restart() is True
    restarted.append("now")
    core.activity.count = 0
    pump(0.2)
    assert restarted == ["now"]


def test_shutdown_cancels_a_waiting_restart(tmp_path, monkeypatch):
    core = ServiceCore(state_dir=tmp_path / "pty", get_setting=lambda key: True)
    monkeypatch.setattr(core, "RESTART_POLL_MS", 20)
    core.activity = _Busy(1)
    restarted = []
    core.restart_when_idle(lambda: restarted.append(True))
    core.shutdown()
    core.activity = _Busy(0)
    pump(0.2)
    assert restarted == [] and not core.restart_pending


# -- the background agents and the rest of PR-1.12d (§3.22) ---------------------------------


class _Poller:
    """The agent list's poller, standing still: what it lists is set by hand."""

    def __init__(self):
        self.background_ids = set()
        self._on_change = lambda changed: None

    def set_on_change(self, on_change):
        self._on_change = on_change

    def start(self, dirs):
        pass

    def stop(self):
        pass

    def set_polling(self, enabled):
        pass

    def refresh(self):
        pass


class _Host:
    def __init__(self):
        self.forgotten = []

    def forget_box(self, box):
        self.forgotten.append(box)


def _store_core(app_state, tmp_path):
    service_state = app_state.AppState(migrate=True, device=False)
    core = ServiceCore(state=service_state, state_dir=tmp_path / "pty")
    store = SessionStore(service_state)
    core.start_store(store)
    store._last_sessions = discover_sessions()
    store._apply()
    poller = _Poller()
    core.start_background(poller=poller, watch_dirs=lambda: [], fetch_busy=lambda: set())
    srv = loopback.LoopbackServer(core)
    ends = Client()
    client = srv.connect(ends.on_output, ends.on_event, device="laptop")
    client.request({"t": "subscribe"})
    return core, store, poller, srv, client, ends


def test_the_background_handoff_flags_are_refused_from_a_client(app_state, projects_dir, tmp_path):
    _root, ids = projects_dir
    _core, store, _poller, srv, client, _ends = _store_core(app_state, tmp_path)
    try:
        for flag in ("backgrounding", "can_background"):
            with pytest.raises(loopback.RequestRefused) as refused:
                client.request({"t": "store.flags", "session": ids["alpha1"], flag: True})
            assert refused.value.error == protocol.ERROR_REFUSED
        assert not store.get_item(ids["alpha1"]).backgrounding
        assert not store.get_item(ids["alpha1"]).can_background
        # The person's own word at their screen still lands.
        client.request({"t": "store.flags", "session": ids["alpha1"], "status": "open"})
        assert store.get_item(ids["alpha1"]).status == "open"
    finally:
        srv.shutdown()
        pump(0.3)


def test_the_background_fact_reaches_the_clients_as_an_item_field(app_state, projects_dir, tmp_path):
    _root, ids = projects_dir
    core, _store, _poller, srv, _client, ends = _store_core(app_state, tmp_path)
    try:
        core.background.mark_backgrounding(ids["alpha1"])
        fields = [e for e in ends.of("item") if e["session"] == ids["alpha1"]]
        assert any(e.get("backgrounding") is True for e in fields)
        assert any(e.get("background") == "pending" for e in fields)
    finally:
        srv.shutdown()
        pump(0.3)


def test_a_worktree_check_reads_the_transcript_on_the_service(app_state, projects_dir, tmp_path):
    import json

    root, ids = projects_dir
    repo = tmp_path / "repo"
    worktree = repo / ".claude" / "worktrees" / "oasis"
    (repo / ".git").mkdir(parents=True)
    worktree.mkdir(parents=True)
    (worktree / "readme.md").write_text("hi\n", encoding="utf-8")
    transcript = next(root.glob(f"*/{ids['alpha1']}.jsonl"))
    record = {"worktreePath": str(worktree), "worktreeName": "oasis", "worktreeBranch": "worktree-oasis"}
    with transcript.open("a", encoding="utf-8") as fh:
        fh.write("\n" + json.dumps({"type": "worktree-state", "worktreeSession": record}) + "\n")
    core, _store, poller, srv, client, ends = _store_core(app_state, tmp_path)

    def check(session_id):
        """The ``worktree.check`` job's result (the transcript read on the
        job's thread, the sharing judged back on the main loop)."""
        message = {"t": "job.start", "kind": "worktree.check", "args": {"session": session_id}}
        job = client.request(message)["job"]

        def done():
            return [e for e in ends.of("job") if e["job"] == job and e["state"] != "running"]

        assert pump(5, lambda: bool(done()))
        assert done()[-1]["state"] == "done"
        return done()[-1]["result"]

    try:
        reply = check(ids["alpha1"])
        assert reply["removable"]["worktreePath"] == str(worktree) and reply["shares"] is False
        plain = check(ids["beta1"])
        assert plain["removable"] is None and plain["shares"] is False
        assert check("not-a-session") == {"removable": None, "shares": False}
        # A background agent works in there: not the client's to take.
        poller.background_ids = {ids["alpha2"]}
        core.store.get_session(ids["alpha2"]).cwd = str(worktree)
        shared = check(ids["alpha1"])
        assert shared["shares"] is True
    finally:
        srv.shutdown()
        pump(0.3)


def test_a_worktree_check_that_never_lands_says_shared(app_state, projects_dir, tmp_path, monkeypatch):
    """A main loop that does not land the sharing verdict within
    `LANDING_WAIT_S` answers ``shares: True``: the safe direction (a false
    "unshared" trashes a worktree an agent works in; a false "shared" keeps
    one). PR 606's verification nit."""
    from collins.service import core as core_mod

    _root, ids = projects_dir
    core, _store, _poller, srv, _client, _ends = _store_core(app_state, tmp_path)
    try:
        monkeypatch.setattr(core_mod, "LANDING_WAIT_S", 0.05)
        core.background._land = lambda fn, *args: None  # the landing never runs
        result = core._worktree_check_job(None, {"session": ids["alpha1"]})
        assert result == {"removable": None, "shares": True}
        # Nothing to judge without a session: no wait, no claim.
        assert core._worktree_check_job(None, {}) == {"removable": None, "shares": False}
    finally:
        srv.shutdown()
        pump(0.3)


def test_store_forget_removes_the_history_file_and_the_box(app_state, projects_dir, tmp_path):
    from collins import panelhistory

    _root, ids = projects_dir
    sid = ids["alpha1"]
    box = "0123456789abcdef0123456789abcdef"
    core, store, _poller, srv, client, _ends = _store_core(app_state, tmp_path)
    host = _Host()
    core.sandbox_host = host
    try:
        panelhistory.save(sid, "$ make\nok\n")
        assert panelhistory.load(sid)
        state = core.state
        state.set_sandboxed(sid, True, box=box)
        state.set_session_draft(sid, "half a prompt")
        state.set_session_attachments(sid, [{"path": "/tmp/x.png"}])
        # Its transcript is still there: not a client's to forget.
        with pytest.raises(loopback.RequestRefused):
            client.request({"t": "store.forget", "session": sid})
        assert panelhistory.load(sid) and state.sandbox_box(sid) == box and host.forgotten == []
        _gone(store, sid, ids["beta1"])  # trashed: the store lets go first
        client.request({"t": "store.forget", "session": sid})
        assert panelhistory.load(sid) is None
        assert host.forgotten == [box]
        assert state.is_sandboxed(sid) and state.sandbox_box(sid) == ""  # the sticky flag stays
        assert state.get_session_draft(sid) == "" and state.get_session_attachments(sid) == []
        # An unsandboxed session's forget marks nothing and forgets no box.
        client.request({"t": "store.forget", "session": ids["beta1"]})
        assert not state.is_sandboxed(ids["beta1"]) and host.forgotten == [box]
    finally:
        srv.shutdown()
        pump(0.3)


def _gone(store, *session_ids):
    """What a trash or a delete does to the store before the forget: the
    sessions leave its scan."""
    store._last_sessions = [s for s in store._last_sessions if s.session_id not in session_ids]
    store._apply()


def test_store_forget_keeps_a_live_sessions_box_and_takes_an_unknown_id(
    app_state, projects_dir, tmp_path, monkeypatch
):
    monkeypatch.setenv("SHELL", CAT)
    _root, ids = projects_dir
    sid = ids["alpha1"]
    box = "fedcba9876543210fedcba9876543210"
    core, store, _poller, srv, client, _ends = _store_core(app_state, tmp_path)
    host = _Host()
    core.sandbox_host = host
    try:
        pty = spawn_agent(client, tmp_path)["pty"]
        core.sessions[pty].session.sandbox_box = box  # a live session runs in the box
        core.state.set_sandboxed(sid, True, box=box)
        _gone(store, sid)
        client.request({"t": "store.forget", "session": sid})
        assert core.state.sandbox_box(sid) == "" and host.forgotten == []  # kept for the live one
        client.request({"t": "store.forget", "session": "00000000-aaaa-bbbb-cccc-000000000000"})
        assert host.forgotten == [] and not core.state.is_sandboxed("00000000-aaaa-bbbb-cccc-000000000000")
    finally:
        srv.shutdown()
        pump(0.3)


def test_a_moved_transcript_is_followed_on_the_service(app_state, projects_dir, tmp_path, monkeypatch):
    """`sync_transcript_paths` (what the window's `_sync_transcript_paths`
    did): a live session whose transcript moved to another project
    directory (worktree entry) is re-aimed on the store's refresh, and again
    on a second move; a fork and a session whose path still exists are
    left alone."""
    monkeypatch.setenv("SHELL", CAT)
    root, ids = projects_dir
    core, store, _poller, srv, client, _ends = _store_core(app_state, tmp_path)
    try:
        moving = core.sessions[spawn_agent(client, tmp_path, session=ids["alpha1"])["pty"]].session
        staying = core.sessions[spawn_agent(client, tmp_path, session=ids["alpha2"])["pty"]].session
        fork = core.sessions[spawn_agent(client, tmp_path, session=ids["beta1"], fork=True)["pty"]].session
        for session, sid in ((moving, ids["alpha1"]), (staying, ids["alpha2"]), (fork, ids["beta1"])):
            session.set_transcript_path(str(store.get_session(sid).jsonl_path))
        fork_path = fork.transcript_path
        staying_path = staying.transcript_path

        def move(sid, project):
            old = store.get_session(sid).jsonl_path
            new_dir = root / project
            new_dir.mkdir(exist_ok=True)
            new = new_dir / f"{sid}.jsonl"
            old.rename(new)
            store._last_sessions = discover_sessions()
            store._apply()
            return str(new)

        first = move(ids["alpha1"], "-home-user-alpha--claude-worktrees-one")
        assert moving.transcript_path == first
        second = move(ids["alpha1"], "-home-user-alpha--claude-worktrees-two")
        assert moving.transcript_path == second
        move(ids["beta1"], "-home-user-beta--claude-worktrees-one")
        assert fork.transcript_path == fork_path  # a fork tails what it was pointed at
        assert staying.transcript_path == staying_path
    finally:
        srv.shutdown()
        pump(0.3)


def test_the_poll_setting_written_by_a_client_reaches_the_poller(app_state, projects_dir, tmp_path):
    core, _store, poller, srv, client, _ends = _store_core(app_state, tmp_path)
    seen = []
    poller.set_polling = seen.append

    def setting(value):
        return {"t": "state.set", "key": "settings", "entry": "background_status_poll", "value": value}

    try:
        client.request(setting(True))
        client.request(setting(False))
        assert seen == [True, False]
    finally:
        srv.shutdown()
        pump(0.3)


@pytest.fixture
def cli_dir(tmp_path, monkeypatch):
    """A `claude` in a folder of its own, off a PATH of /usr/bin alone, and
    no PATH entry left over from an earlier apply."""
    from collins import clisetup

    tools = tmp_path / "tools"
    tools.mkdir()
    cli = tools / "claude"
    cli.write_text("#!/bin/sh\n")
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(clisetup, "_appended", None)
    monkeypatch.setattr(clisetup, "known_locations", lambda: [tmp_path / "nowhere" / "claude", cli])
    return cli


def test_the_cli_is_the_services_to_find_and_judge(app_state, tmp_path, cli_dir):
    """The welcome gate and Preferences ask the service where its claude
    is (service.status's cli, cli.check) and what a typed path is worth."""
    _core, _store, _poller, srv, client, _ends = _store_core(app_state, tmp_path)
    try:
        assert client.request({"t": "service.status"})["cli"] == ""
        asked = client.request({"t": "cli.check"})
        assert asked["found"] == "" and asked["detected"] == str(cli_dir)
        assert "status" not in asked
        judged = client.request({"t": "cli.check", "path": str(cli_dir)})
        assert judged["status"] == "ok" and "detected" not in judged
        assert client.request({"t": "cli.check", "path": "/nowhere/claude"})["status"] == "missing"
        versioned = tmp_path / "versions" / "2.1.226"
        versioned.parent.mkdir()
        versioned.write_text("#!/bin/sh\n")
        versioned.chmod(0o755)
        assert client.request({"t": "cli.check", "path": str(versioned)})["status"] == "versioned"
    finally:
        srv.shutdown()
        pump(0.3)


def test_the_cli_path_written_by_a_client_goes_on_the_services_path(app_state, tmp_path, cli_dir):
    """The welcome's and Preferences' answer reaches the service's PATH at
    once, so the next cli.check finds it; cleared, the entry comes off."""
    from collins import clisetup

    _core, _store, _poller, srv, client, _ends = _store_core(app_state, tmp_path)

    def setting(value):
        return {"t": "state.set", "key": "settings", "entry": clisetup.PATH_SETTING, "value": value}

    try:
        client.request(setting(str(cli_dir)))
        assert os.environ["PATH"].split(os.pathsep)[-1] == str(cli_dir.parent)
        assert client.request({"t": "cli.check"})["found"] == str(cli_dir)
        assert client.request({"t": "service.status"})["cli"] == str(cli_dir)
        client.request(setting(""))
        assert str(cli_dir.parent) not in os.environ["PATH"].split(os.pathsep)
        assert client.request({"t": "cli.check"})["found"] == ""
    finally:
        srv.shutdown()
        pump(0.3)


def test_an_agent_spawn_in_a_chat_folder_makes_it_again(app_state, projects_dir, tmp_path, monkeypatch):
    from collins import chats

    monkeypatch.setenv("SHELL", CAT)
    monkeypatch.setattr(chats, "CHATS_DIR", tmp_path / "chats")
    folder = tmp_path / "chats" / "chat-swept"
    assert chats.is_chat_cwd(str(folder)) and not folder.exists()
    core, _store, _poller, srv, client, _ends = _store_core(app_state, tmp_path)
    try:
        pty = spawn_agent(client, folder)["pty"]
        assert folder.is_dir()
        assert core.sessions[pty].session.cwd == str(folder)  # not the fallback to $HOME
    finally:
        srv.shutdown()
        pump(0.3)


def test_a_sandboxed_sessions_background_close_is_refused(app_state, projects_dir, tmp_path, monkeypatch):
    """§5: the CLI's daemon would respawn a backgrounded agent outside its
    box, so the service's gate refuses the /bg and marks nothing."""
    monkeypatch.setenv("SHELL", CAT)
    _root, ids = projects_dir
    watches = []
    core, store, _poller, srv, client, _ends = _store_core(app_state, tmp_path)
    core.background._spawn = watches.append
    try:
        pty = spawn_agent(client, tmp_path, session=ids["alpha1"])["pty"]
        session = core.sessions[pty].session
        monkeypatch.setattr(type(session), "sandboxed", property(lambda self: True))
        with pytest.raises(loopback.RequestRefused) as refused:
            client.request({"t": "close", "pty": pty, "mode": "background", "text": "/bg\r"})
        assert refused.value.msgid == "sandboxed"
        assert watches == [] and core.state.get_pending_detaches() == {}
        assert not store.get_item(ids["alpha1"]).backgrounding
        assert not core.background.pending
    finally:
        srv.shutdown()
        pump(0.3)


def test_service_status_says_whether_gh_is_ready(server, monkeypatch):
    from collins import ghsetup

    ends = Client()
    client = server.connect(ends.on_output, ends.on_event, device="laptop")
    assert "gh" not in client.request({"t": "service.status"})  # not asked yet
    monkeypatch.setattr(ghsetup, "check", lambda: ghsetup.LOGGED_OUT)
    server.core.check_gh()
    assert pump(2, lambda: server.core.gh_status is not None)
    assert client.request({"t": "service.status"})["gh"] == "logged-out"
    monkeypatch.setattr(ghsetup, "check", lambda: ghsetup.READY)
    server.core.check_gh(fresh=True)
    assert "gh" not in client.request({"t": "service.status"})  # asked again: no stale answer
    assert pump(2, lambda: server.core.gh_status == ghsetup.READY)
    assert client.request({"t": "service.status"})["gh"] == "ready"
    # A drop while a check runs: that check's answer predates the drop and
    # is not kept; the one after it is.
    answers = iter([ghsetup.MISSING, ghsetup.LOGGED_OUT])
    monkeypatch.setattr(ghsetup, "check", lambda: next(answers))
    server.core.check_gh(fresh=True)
    server.core.check_gh(fresh=True)
    assert pump(3, lambda: server.core.gh_status is not None)
    assert server.core.gh_status == ghsetup.LOGGED_OUT


def test_the_cli_is_looked_up_on_the_session_environments_path(tmp_path, monkeypatch):
    """PR-3.0 item 10: `service.status`'s `cli` and `cli.check`'s `found`
    are a lookup on the PATH the service spawns its shells with (the login
    shell's, merged), not on the process's own: a service started by a
    non-interactive ssh command has no `~/.local/bin` on its own PATH and a
    shell that finds the CLI there."""
    from collins import clisetup

    monkeypatch.setattr(clisetup, "_appended", None)
    monkeypatch.setattr(clisetup, "_search_base", None)
    bare = tmp_path / "process-bin"
    bare.mkdir()
    monkeypatch.setenv("PATH", str(bare))  # the process's PATH: no claude on it
    captured = tmp_path / "login-bin"
    captured.mkdir()
    cli = captured / "claude"
    cli.write_text("#!/bin/sh\nexit 0\n")
    cli.chmod(0o755)
    environment = {**os.environ, "PATH": f"{bare}{os.pathsep}{captured}"}
    core = ServiceCore(state_dir=tmp_path / "pty", environment=lambda: dict(environment))
    srv = loopback.LoopbackServer(core)
    try:
        ends = Client()
        client = srv.connect(ends.on_output, ends.on_event, device="laptop")
        assert clisetup.found_at() is None  # the process's own lookup misses
        assert client.request({"t": "service.status"})["cli"] == str(cli)
        assert client.request({"t": "cli.check"})["found"] == str(cli)
        # An environment without it: "" (none), whatever the process has.
        environment["PATH"] = str(bare)
        assert client.request({"t": "service.status"})["cli"] == ""
        # A stored answer applied on the service is searched too, on top of
        # the captured environment (a snapshot taken before it was applied).
        assert clisetup.apply(str(cli)) is True
        assert client.request({"t": "service.status"})["cli"] == str(cli)
    finally:
        clisetup.apply("")
        srv.shutdown()
        pump(0.3)


def test_the_archive_sweep_runs_on_the_service(app_state, projects_dir, tmp_path, monkeypatch):
    import time as time_mod

    from collins import autodelete

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    _root, ids = projects_dir
    core, store, poller, srv, _client, ends = _store_core(app_state, tmp_path)
    try:
        state = core.state
        state.update_settings({autodelete.SETTING_COUNT: 1, autodelete.SETTING_UNIT: "days"})
        old = time_mod.time() - 3 * 86400
        for sid in (ids["alpha2"], ids["beta1"]):
            state.set_archived(sid, True)
            state.archived_at[sid] = old
        poller.background_ids = {ids["beta1"]}  # still running as an agent: skipped
        monkeypatch.setattr(store, "trash_many", lambda sids: {})
        trashed = core.sweep_archived()
        assert trashed == [ids["alpha2"]]
        assert not state.is_archived(ids["alpha2"]) and state.is_archived(ids["beta1"])
        assert [e["session"] for e in ends.of("forgotten")] == [ids["alpha2"]]
        assert core.sweep_archived() is None  # once a day
    finally:
        srv.shutdown()
        pump(0.3)


def test_a_background_close_starts_the_handoff_on_the_service(app_state, projects_dir, tmp_path, monkeypatch):
    """§3.22: the fork watch's thread starts when the session's close
    {mode: background} runs; the pending detach is on disk and the rows are
    disabled and pending before the /bg is typed."""
    from collins import providers

    monkeypatch.setenv("SHELL", CAT)
    monkeypatch.setattr(providers.ClaudeProvider, "background_agents", lambda self, **_kw: [])
    _root, ids = projects_dir
    service_state = app_state.AppState(migrate=True, device=False)
    core = ServiceCore(state=service_state, state_dir=tmp_path / "pty")
    store = SessionStore(service_state)
    core.start_store(store)
    store._last_sessions = discover_sessions()
    store._apply()
    watches = []
    core.start_background(poller=_Poller(), watch_dirs=lambda: [], spawn=watches.append)
    srv = loopback.LoopbackServer(core)
    try:
        ends = Client()
        client = srv.connect(ends.on_output, ends.on_event, device="laptop")
        client.request({"t": "subscribe"})
        pty = spawn_agent(client, tmp_path, session=ids["alpha1"])["pty"]
        assert store.get_item(ids["alpha1"]).can_background is True  # the gate: a live, registered session
        client.request({"t": "close", "pty": pty, "mode": "background", "text": "/bg\r"})
        assert len(watches) == 1  # the watch's thread
        assert ids["alpha1"] in service_state.get_pending_detaches()
        item = store.get_item(ids["alpha1"])
        assert item.backgrounding is True and item.background == "pending"
        assert item.can_background is False  # one handoff at a time
        sent = [e for e in ends.of("item") if e["session"] == ids["alpha1"]]
        assert any(e.get("backgrounding") is True for e in sent)
    finally:
        srv.shutdown()
        pump(0.3)


def test_the_repair_job_matches_off_the_loop_and_records_the_link(
    app_state, projects_dir, tmp_path, monkeypatch
):
    """`session.repair` (PR-1.12d): the inputs read on the main loop, the
    match on the job's thread, the forward recorded back on the main loop
    before the job's outcome; nothing matching is ``found: None``."""
    from collins.service import bgagents as bgagents_mod

    _root, ids = projects_dir
    core, store, _poller, srv, client, ends = _store_core(app_state, tmp_path)
    try:
        asked = []

        def match(provider, old_id, cwd, uuid, claimed, unique_cwd=False):
            asked.append((old_id, cwd, set(claimed), unique_cwd))
            return "fork-0003" if old_id == ids["alpha1"] else None

        monkeypatch.setattr(bgagents_mod.bgstatus, "match_background_fork", match)

        def repair(session_id):
            message = {"t": "job.start", "kind": "session.repair", "args": {"session": session_id}}
            return client.request(message)["job"]

        def outcome(job_id):
            done = [e for e in ends.of("job") if e["job"] == job_id and e["state"] != "running"]
            return done[-1] if done else None

        job = repair(ids["alpha1"])
        assert pump(5, lambda: outcome(job) is not None)
        assert outcome(job)["state"] == "done" and outcome(job)["result"] == {"found": "fork-0003"}
        assert core.state.resolve_forward(ids["alpha1"]) == "fork-0003"
        assert asked[0][0] == ids["alpha1"] and asked[0][1] == "/home/user/alpha" and asked[0][3] is True
        missing = repair(ids["beta1"])
        assert pump(5, lambda: outcome(missing) is not None)
        assert outcome(missing)["result"] == {"found": None}
        assert core.state.resolve_forward(ids["beta1"]) == ids["beta1"]
        unknown = repair("nope")
        assert pump(5, lambda: outcome(unknown) is not None)
        assert outcome(unknown)["state"] == "refused"
    finally:
        srv.shutdown()
        pump(0.3)


def test_the_sweep_skips_a_live_agent_pty_whose_cli_is_not_up(app_state, projects_dir, tmp_path, monkeypatch):
    """Deviation 7: the sweep's "running" is any live agent pty, as the old
    "a tab is open" was — a tab sitting at its shell with the CLI gone, or a
    spawn the resolver has not bound, is skipped like a running CLI, not
    trashed and closed under whoever is looking at it (PR 606's second
    review, should-fix 2)."""
    import time as time_mod

    from collins import autodelete

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("SHELL", CAT)
    _root, ids = projects_dir
    core, store, _poller, srv, client, ends = _store_core(app_state, tmp_path)
    try:
        state = core.state
        state.update_settings({autodelete.SETTING_COUNT: 1, autodelete.SETTING_UNIT: "days"})
        old = time_mod.time() - 3 * 86400
        for sid in (ids["alpha2"], ids["beta1"]):
            state.set_archived(sid, True)
            state.archived_at[sid] = old
        # A tab on alpha2 whose CLI never came up (cat is the shell; the
        # typed command is echoed, not run): no running CLI, a live pty.
        pty = spawn_agent(client, tmp_path, session=ids["alpha2"])["pty"]
        assert ids["alpha2"] not in set(core.agent_pty_sessions().values())
        assert core.agent_pty_sessions(include_finished=True)[pty] == ids["alpha2"]
        trashed_sids = []
        monkeypatch.setattr(store, "trash_many", lambda sids: trashed_sids.extend(sids) or {})
        assert core.sweep_archived() == [ids["beta1"]]
        assert trashed_sids == [ids["beta1"]]
        assert state.is_archived(ids["alpha2"])  # kept: somebody's tab shows it
        assert [e["session"] for e in ends.of("forgotten")] == [ids["beta1"]]
    finally:
        srv.shutdown()
        pump(0.3)


def test_the_orphan_uploads_sweep_knows_the_live_and_listed_sessions(
    app_state, projects_dir, tmp_path, monkeypatch
):
    """Should-fix 1 of the PR 612 review: the uploads of a session neither a
    pty runs nor the store lists are swept on the housekeeping tick, after
    the store's first scan and never before it, with the known ids gathered
    on the main loop and the sweep itself handed to a thread."""
    from collins import uploads
    from collins.service import gitfeed

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("COLLINS_UPLOADS_DIR", str(tmp_path / "uploads"))
    service_state = app_state.AppState(migrate=True, device=False)
    core = ServiceCore(state=service_state, state_dir=tmp_path / "pty")
    store = SessionStore(service_state)
    core.start_store(store)
    monkeypatch.setattr(core, "sweep_archived", lambda: None)
    monkeypatch.setattr(core, "sweep_pending_uploads", lambda: None)
    monkeypatch.setattr(core, "check_gh", lambda fresh=False: None)
    monkeypatch.setattr(core, "agent_pty_sessions", lambda include_finished=False: {1: "live-1", 2: None})
    swept = []
    monkeypatch.setattr(uploads, "sweep_orphans", lambda known, *a, **kw: swept.append(set(known)) or [])
    monkeypatch.setattr(gitfeed, "spawn_default", lambda fn, name: fn())
    try:
        core.start_housekeeping()
        core.sweep_orphan_uploads()
        assert swept == []  # before the first scan every upload would read as an orphan
        store._last_sessions = discover_sessions()
        store._apply()
        assert len(swept) == 1
        listed = {s.session_id for s in store.all_sessions()}
        assert listed and swept[0] == listed | {"live-1"}
        core._sweep_tick()
        assert len(swept) == 2
    finally:
        core.shutdown()


def test_the_orphan_uploads_sweep_distrusts_an_empty_scan_and_a_missing_projects_dir(
    app_state, projects_dir, tmp_path, monkeypatch
):
    """The verification of d212775: `providers.discover` answers [] when
    the projects directory is not there (a transient unmount, an unreadable
    directory) and the store applies it, so a tick then would have removed
    every non-live upload directory older than a day. A scan that listed no
    session is not trusted, and the thread checks the projects directory
    is readable before the uploads are scanned; nothing is removed either
    way."""
    from collins import sessions, uploads
    from collins.service import gitfeed

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("COLLINS_UPLOADS_DIR", str(tmp_path / "uploads"))
    service_state = app_state.AppState(migrate=True, device=False)
    core = ServiceCore(state=service_state, state_dir=tmp_path / "pty")
    store = SessionStore(service_state)
    core.start_store(store)
    monkeypatch.setattr(core, "sweep_archived", lambda: None)
    monkeypatch.setattr(core, "sweep_pending_uploads", lambda: None)
    monkeypatch.setattr(core, "check_gh", lambda fresh=False: None)
    monkeypatch.setattr(core, "agent_pty_sessions", lambda include_finished=False: {1: "live-1"})
    monkeypatch.setattr(gitfeed, "spawn_default", lambda fn, name: fn())
    stale = uploads.write("gone-1", "a.png", b"x").parent
    os.utime(stale, (time.time() - 2 * uploads.ORPHAN_AFTER_SECONDS,) * 2)
    try:
        # An empty scan applied: a live pty is known, but the store's half is nothing.
        store._last_sessions = []
        store._apply()
        core.sweep_orphan_uploads()
        assert stale.exists()
        # A real scan, but the projects directory is gone by the time the thread runs.
        store._last_sessions = discover_sessions()
        assert store.all_sessions()
        store._apply()
        mounted = sessions.CLAUDE_PROJECTS_DIR
        monkeypatch.setattr(sessions, "CLAUDE_PROJECTS_DIR", tmp_path / "unmounted")
        core.sweep_orphan_uploads()
        assert stale.exists()
        # Both in order: the sweep runs and the orphan goes.
        monkeypatch.setattr(sessions, "CLAUDE_PROJECTS_DIR", mounted)
        core.sweep_orphan_uploads()
        assert not stale.exists()
    finally:
        core.shutdown()


def test_the_orphan_uploads_sweep_is_refused_on_staged_sessions_over_an_unnamed_uploads_root(
    app_state, projects_dir, tmp_path, monkeypatch, caplog
):
    """The 612 follow-up of the PR 613 verification: a scratch service
    (staged sessions, as every check, capture and this fixture has) with
    no `COLLINS_UPLOADS_DIR` would have judged the uploads under the
    default root, the user's real ones, by sessions that are not the
    user's. It does not sweep at all, says so once, and sweeps again the
    moment the uploads are named for it; on the real projects directory
    (the user's instance, `start-debug`) it is never refused."""
    from collins import sessions, uploads
    from collins.service import gitfeed

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    # The "real" uploads: the default root, reached through XDG_DATA_HOME alone.
    monkeypatch.delenv("COLLINS_UPLOADS_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    service_state = app_state.AppState(migrate=True, device=False)
    core = ServiceCore(state=service_state, state_dir=tmp_path / "pty")
    store = SessionStore(service_state)
    core.start_store(store)
    monkeypatch.setattr(core, "agent_pty_sessions", lambda include_finished=False: {})
    monkeypatch.setattr(gitfeed, "spawn_default", lambda fn, name: fn())
    stale = uploads.write("the-users-own", "a.png", b"x").parent
    assert stale == tmp_path / "data" / "collins" / "uploads" / "the-users-own"
    os.utime(stale, (time.time() - 2 * uploads.ORPHAN_AFTER_SECONDS,) * 2)
    try:
        store._last_sessions = discover_sessions()
        store._apply()
        assert store.all_sessions() and sessions.CLAUDE_PROJECTS_DIR != sessions.DEFAULT_PROJECTS_DIR
        with caplog.at_level("WARNING", logger="collins.service.core"):
            core.sweep_orphan_uploads()
            core.sweep_orphan_uploads()
        assert stale.exists()
        assert len([r for r in caplog.records if "orphan uploads sweep refused" in r.getMessage()]) == 1
        # The real projects directory (here: the default made to be the staged one): swept.
        monkeypatch.setattr(sessions, "DEFAULT_PROJECTS_DIR", sessions.CLAUDE_PROJECTS_DIR)
        core.sweep_orphan_uploads()
        assert not stale.exists()
        # Staged sessions with the uploads named for them: swept too.
        monkeypatch.setattr(sessions, "DEFAULT_PROJECTS_DIR", tmp_path / "the-clis-own")
        monkeypatch.setenv("COLLINS_UPLOADS_DIR", str(tmp_path / "uploads"))
        stale = uploads.write("gone-1", "a.png", b"x").parent
        os.utime(stale, (time.time() - 2 * uploads.ORPHAN_AFTER_SECONDS,) * 2)
        core.sweep_orphan_uploads()
        assert not stale.exists()
    finally:
        core.shutdown()


def test_the_first_sweep_waits_for_the_stores_first_scan(app_state, projects_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    service_state = app_state.AppState(migrate=True, device=False)
    core = ServiceCore(state=service_state, state_dir=tmp_path / "pty")
    store = SessionStore(service_state)
    core.start_store(store)
    swept = []
    monkeypatch.setattr(core, "sweep_archived", lambda: swept.append(store.applied))
    monkeypatch.setattr(core, "check_gh", lambda fresh=False: None)
    try:
        core.start_housekeeping()
        assert swept == []  # no scan yet: a sweep would stamp the day for nothing
        store._last_sessions = discover_sessions()
        store._apply()
        assert swept == [True]
        store._apply()
        assert swept == [True]  # once: the timer takes it from there
        assert core._sweep_source
    finally:
        core.shutdown()
        assert not core._sweep_source
