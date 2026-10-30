# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The verification of PR 629 (PR-3.6, the keeper) as tests: N-M1 (a keeper
that exists but cannot be read or spoken to leaves every row, model, plan
and box, and a session it holds is refused a second spawn), N-M2 (an
adopted id-less session's resolver judges a transcript by its first
record's timestamp and never binds an id another live pty holds), N-N1
(the flat pre-3.6 model files are pruned). GTK-free."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import inproc
from test_keeper import pump

from collins import sandboxplan
from collins.api import protocol
from collins.service import keeper
from collins.service.core import HELD_BY_KEEPER_MSGID, ServiceCore
from collins.service.session import Session

# -- N-M1 ------------------------------------------------------------------------------------


def test_an_unread_keeper_leaves_everything_and_refuses_a_second_spawn(tmp_path, app_state, monkeypatch):
    """N-M1 and N-S1/N-S2: with `keeper_unread` (another protocol, a keeper
    that did not answer) the adoption touches nothing, the sandbox sweeps
    are skipped, `service.status` says so, and a spawn of a session a kept
    row names is refused."""
    state = app_state.AppState()
    app = "com.example.Held"
    row = {"kind": "agent", "cwd": "/tmp", "cols": 80, "rows": 24, "session": "s5"}
    state.set_pty(app, 5, row)
    models = tmp_path / "pty" / app
    models.mkdir(parents=True)
    (models / "5.model").write_text("{}")
    swept = []
    monkeypatch.setattr(sandboxplan, "sweep_plans", lambda *a, **k: swept.append("plans") or 0)
    monkeypatch.setattr(sandboxplan.SandboxHost, "sweep_boxes", lambda self: swept.append("boxes") or 0)
    monkeypatch.setattr(sandboxplan, "probe_async", lambda cb: None)
    core = ServiceCore(state=state, state_dir=tmp_path / "pty", app_id=app)
    core.mark_keeper_unread("it speaks another protocol")
    assert core.adopt_ptys() == []
    assert state.get_ptys(app) == {5: row}
    assert (models / "5.model").exists()
    core.start_sandbox_host(app)
    assert swept == [] and core.sandbox_host is not None and core.sandbox_grants is not None
    assert core.held_session_ids() == {"s5"}
    srv = inproc.LoopbackServer(core)
    try:
        client = srv.connect(lambda *a: None, lambda e: None)
        status = client.request({"t": "service.status"})
        assert status["keeper_unread"] is True and status["keeper_held"] == 1 and "keeper" not in status
        spawn = {"t": "spawn", "kind": "agent", "cwd": "/tmp", "cols": 80, "rows": 24, "session": "s5"}
        try:
            client.request(spawn)
        except protocol.RequestRefused as refused:
            assert refused.error == protocol.ERROR_REFUSED and refused.msgid == HELD_BY_KEEPER_MSGID
        else:
            raise AssertionError("a second spawn of a held session was not refused")
        # Another session is spawned as ever (the service is usable).
        reply = client.request({"t": "spawn", "kind": "shell", "cwd": "/tmp", "cols": 80, "rows": 24})
        assert reply["pty"] >= 1
    finally:
        srv.shutdown()
        pump(lambda: not core.ptys.ptys, timeout=8, what="the shell to end")


def test_no_keeper_process_at_all_drops_the_rows(tmp_path, app_state):
    """N-M1's other half: with no keeper for the app id (none answers, no
    pid file names a live keeper) the rows name dead ptys and go, as a
    keeperless service always dropped them."""
    state = app_state.AppState()
    app = "com.example.Gone"
    state.set_pty(app, 5, {"kind": "agent", "cwd": "/tmp", "cols": 80, "rows": 24, "session": "s5"})
    core = ServiceCore(state=state, state_dir=tmp_path / "pty", app_id=app)
    assert core.adopt_ptys() == []
    assert state.get_ptys(app) == {} and core.held_session_ids() == set()


# -- N-M2 ------------------------------------------------------------------------------------


def _transcript(path: Path, created: float) -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(created))
    path.write_text(json.dumps({"type": "user", "timestamp": stamp, "cwd": "/w"}) + "\n")


class _Provider:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def transcripts_for_cwd(self, cwd):
        return sorted(self.directory.glob("*.jsonl"))

    def session_id_for_transcript(self, path):
        return Path(path).stem


class _Host:
    def __init__(self, held: set[str]) -> None:
        self.held = held
        self.resolved: list[str] = []

    def alive(self):
        return True

    def mapped(self):
        return True

    def session_resolved(self, sid):
        self.resolved.append(sid)

    def running_session_ids(self):
        return set(self.held)

    def transcript_reset(self):
        pass


class _Scheduler:
    def __init__(self) -> None:
        self.timers: list = []

    def time(self):
        return time.time()

    def timeout_add(self, ms, fn):
        self.timers.append(fn)
        return 1


def _resolver(directory: Path, held: set[str], since: float):
    """A `Session` with only what its resolver reads, as the reviewer's
    probe built it, armed as an adopted session is (`since`)."""
    s = Session.__new__(Session)
    s.provider, s.host, s.scheduler = _Provider(directory), _Host(held), _Scheduler()
    s.session_id = None
    s.fork_resolve = False
    s.continue_recent = False
    s.resolver_cwd = None
    s._resolver_source = None
    s._resolver_armed_at = 0.0
    s._baseline_by_created = False
    s._resolver_attempts = 0
    s._baselined_dirs = set()
    s._known_transcripts = set()
    s.transcript = type("T", (), {"set_path": lambda self, p: None})()
    s._transcript_monitor = None
    s._transcript_refresh_source = None
    s._poll_source = None
    s._updating = False
    s.tracked_prs = {}
    s.restored_prs = []
    s.set_transcript_path = lambda p: None
    s.current_agent_cwd = lambda: str(directory)
    s.start_resolver(str(directory), since=since)
    return s


def test_an_adopted_resolver_judges_by_the_first_record_and_skips_held_ids(tmp_path):
    """N-M2 (the reviewer's probe): an old idle session's transcript written
    a minute ago (its mtime past the shell's start, its first record an
    hour before it), a sibling another live pty runs, and the adopted
    session's own transcript, created after its shell started: the own one
    binds, the other two never do, whatever their mtimes."""
    now = time.time()
    shell_start = now - 60
    old = tmp_path / "11111111-1111-1111-1111-111111111111.jsonl"
    sib = tmp_path / "22222222-2222-2222-2222-222222222222.jsonl"
    own = tmp_path / "33333333-3333-3333-3333-333333333333.jsonl"
    _transcript(old, now - 3600)  # began an hour ago, written just now
    _transcript(sib, now - 30)  # began after the shell, held by a live pty
    s = _resolver(tmp_path, held={sib.stem}, since=shell_start)
    assert s._baseline_by_created is True
    # The first tick: the old one is baselined by its first record, the
    # sibling is held elsewhere; nothing of the adopted session's yet.
    assert s._resolve_transcript() is True and s.host.resolved == []
    _transcript(own, now - 10)  # the adopted CLI's first prompt lands
    os.utime(old, None)  # the old one written again, newest by mtime
    s._resolve_transcript()
    assert s.host.resolved == [own.stem] and s.session_id == own.stem


def test_a_fresh_resolver_is_unchanged_by_the_held_rule_except_for_held_ids(tmp_path):
    """Without `since` the baseline is as it was (every transcript present
    at the arm is another session's); the held rule applies to every
    resolver, so a live pty's id is never taken even by a fresh one."""
    now = time.time()
    sib = tmp_path / "22222222-2222-2222-2222-222222222222.jsonl"
    own = tmp_path / "33333333-3333-3333-3333-333333333333.jsonl"
    s = _resolver(tmp_path, held={sib.stem}, since=None)
    assert s._baseline_by_created is False
    _transcript(sib, now)
    _transcript(own, now)
    os.utime(sib, (now + 5, now + 5))  # the held one is the newest
    s._resolve_transcript()
    assert s.host.resolved == [own.stem]


# -- N-N1 ------------------------------------------------------------------------------------


def test_the_flat_pre36_model_files_are_pruned_once(tmp_path):
    """The models' root used to hold `<pty id>.model` right under it; a
    service prunes those (no pty of that layout survived) and leaves the
    other app ids' directories alone."""
    from collins.service import ptyserver

    root = tmp_path / "pty"
    (root / "com.example.Other").mkdir(parents=True)
    (root / "com.example.Other" / "3.model").write_text("{}")
    (root / "7.model").write_text("{}")
    (root / "notes.txt").write_text("")
    server = ptyserver.PtyServer(state_dir=root / "com.example.Me")
    assert server.prune_models() == 1
    assert not (root / "7.model").exists()
    assert (root / "com.example.Other" / "3.model").exists() and (root / "notes.txt").exists()
    assert keeper.pid_alive(os.getpid())
