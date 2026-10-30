# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The verification of PR 629 (PR-3.6, the keeper) as tests: N-M1 (a keeper
that exists but cannot be read or spoken to leaves every row, model, plan
and box, and a session it holds is refused a second spawn), N-M2 (an
adopted id-less session's resolver judges a transcript by its first
record's timestamp and never binds an id another live pty holds), N-N1
(the flat pre-3.6 model files are pruned), and its round 2: V2-S1 (a row
is held only while its child and the unread keeper live), V2-S2 (the
hand-over by duplicates, a non-master refused), V2-N1 (`read_pid` names
the keeper of this socket), and its round 3: V3-N1 (a master is a device,
never a name), V3-N2 (a bare cmdline is the default socket's keeper only).
GTK-free."""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

import inproc
import pytest
from test_keeper import ENV, KeeperProcess, pump

from collins import sandboxplan
from collins.api import protocol
from collins.service import keeper
from collins.service.core import HELD_BY_KEEPER_MSGID, ServiceCore
from collins.service.session import Session


@pytest.fixture
def runtime():
    import shutil
    import tempfile

    directory = tempfile.mkdtemp(prefix="ck-")
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def kept(runtime):
    proc = KeeperProcess(runtime)
    yield proc
    proc.stop()


# -- N-M1 ------------------------------------------------------------------------------------


def test_an_unread_keeper_leaves_everything_and_refuses_a_second_spawn(tmp_path, app_state, monkeypatch):
    """N-M1 and N-S1/N-S2: with `keeper_unread` (another protocol, a keeper
    that did not answer) the adoption touches nothing, the sandbox sweeps
    are skipped, `service.status` says so, and a spawn of a session a kept
    row names is refused."""
    import subprocess

    state = app_state.AppState()
    app = "com.example.Held"
    child = subprocess.Popen(["sleep", "60"], stdin=subprocess.DEVNULL)  # the held session's shell
    row = {"kind": "agent", "cwd": "/tmp", "cols": 80, "rows": 24, "session": "s5", "pid": child.pid}
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
        child.kill()
        child.wait()


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


# -- V2-S1 -----------------------------------------------------------------------------------


def test_a_held_row_is_dropped_once_its_child_or_keeper_is_gone(tmp_path, app_state):
    """V2-S1: a row counts as held only while its child and the unread
    keeper live; with the keeper stopped as the dialog advises, the rows
    are dropped, their models pruned, `keeper_unread` cleared and the
    session resumes as any ended one does."""
    import subprocess
    import sys

    state = app_state.AppState()
    app = "com.example.Stale"
    live = subprocess.Popen(["sleep", "60"], stdin=subprocess.DEVNULL)
    dead = subprocess.Popen(["true"])
    dead.wait()
    try:
        state.set_pty(app, 1, {"kind": "agent", "cwd": "/tmp", "pid": live.pid, "session": "s-live"})
        state.set_pty(app, 2, {"kind": "agent", "cwd": "/tmp", "pid": dead.pid, "session": "s-dead"})
        models = tmp_path / "pty" / app
        models.mkdir(parents=True)
        for n in (1, 2):
            (models / f"{n}.model").write_text("{}")
        keeper_proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        core = ServiceCore(state=state, state_dir=tmp_path / "pty", app_id=app)
        core.mark_keeper_unread("it did not answer", pid=keeper_proc.pid)
        # The dead child's row goes at once; the live one is held.
        assert core.held_session_ids() == {"s-live"}
        assert set(state.get_ptys(app)) == {1} and core.keeper_unread
        assert not (models / "2.model").exists() and (models / "1.model").exists()
        # The keeper stopped: nothing is held any more, the service is no
        # longer unread, and the last row and model go.
        keeper_proc.kill()
        keeper_proc.wait()
        assert core.held_session_ids() == set()
        assert core.keeper_unread is False and core.keeper_mismatch == 0
        assert state.get_ptys(app) == {} and not (models / "1.model").exists()
    finally:
        live.kill()
        live.wait()


# -- V2-S2 -----------------------------------------------------------------------------------


def test_the_keeper_refuses_to_adopt_what_is_not_a_pty_master(kept):
    """V2-S2: an `adopt` whose fd is not a pty master (a number the service
    closed and the next open took) is refused `invalid`, the fd closed."""
    client = kept.client()
    r, w = os.pipe()
    try:
        before = len(_fds(kept.pid))
        try:
            client.adopt(9, os.getpid(), r)
        except keeper.KeeperError as refused:
            assert refused.reply["error"] == "invalid" and "pty master" in refused.reply["detail"]
        else:
            raise AssertionError("a pipe was adopted as a master")
        assert client.list(with_fds=False) == []
        assert len(_fds(kept.pid)) <= before
    finally:
        os.close(r)
        os.close(w)


def _fds(pid: int) -> list[str]:
    try:
        return os.listdir(f"/proc/{pid}/fd")
    except OSError:
        return []


def test_the_hand_over_uses_duplicates_it_closes_after(kept, runtime, tmp_path):
    """V2-S2: `_keeper_lost` duplicates each master for the worker, so a
    master closed and its number reused on the main loop meanwhile is never
    what reaches the new keeper; the duplicates are closed once handed."""
    from test_keeper import KeeperProcess, Recorder, Sink, kill_child, make_server, settle

    recorder = Recorder()
    second: list[KeeperProcess] = []
    gate = {"go": False}

    def restart():
        while not gate["go"]:
            time.sleep(0.02)
        proc = KeeperProcess(runtime)
        second.append(proc)
        return proc.client()

    server = make_server(tmp_path, kept.client(), recorder, keeper_restart=restart)
    sink = Sink()
    first = server.spawn("shell", ["cat"], "/tmp", ENV, 80, 24)
    master = server.get(first).master
    try:
        kept.stop(signal.SIGKILL)
        server.attach(first, sink, 80, 24)
        server.write(first, b"x", sink)  # the write fails on the dead keeper's absence? no: the pty lives
        server._keeper_lost(keeper.KeeperError(32, "gone"))  # the worker now waits at the gate
        pump(lambda: server._keeper_restarting, timeout=2, what="the restart to begin")
        # Meanwhile the original master's number is closed and reused by
        # another file on the main loop.
        server._close_master(server.get(first))
        reused = os.open("/dev/null", os.O_RDONLY)
        assert reused == master  # the lowest free number: the one just closed
        gate["go"] = True
        pump(lambda: bool(second) and server.keeper is not None and server.keeper.pid == second[0].pid,
             timeout=8, what="the replacement keeper")  # fmt: skip
        listed = server.keeper.list(with_fds=True)
        try:
            assert [e["id"] for e in listed] == [first] and listed[0]["adopted"]
            assert keeper.is_pty_master(listed[0]["fd"])  # the duplicate, not /dev/null
        finally:
            for e in listed:
                os.close(e["fd"])
        os.close(reused)
    finally:
        pty = server.ptys.get(first)
        if pty is not None and pty.pid:
            kill_child(pty.pid)
        server.shutdown()
        pump(lambda: not server.ptys, timeout=8, what="the ptys to end")
        for proc in second:
            proc.stop()
        settle(0.1)


# -- V2-N1 -----------------------------------------------------------------------------------


def test_read_pid_names_only_the_keeper_of_this_socket(kept):
    """V2-N1: a pid file whose pid is another socket's keeper (a recycled
    pid) is not this socket's keeper."""
    assert keeper.read_pid(kept.socket) == kept.pid
    assert keeper.is_keeper_process(kept.pid, kept.socket) is True
    assert keeper.is_keeper_process(kept.pid, "/elsewhere/k.sock") is False
    assert keeper.is_keeper_process(kept.pid) is True
    assert keeper.is_keeper_process(os.getpid(), kept.socket) is False


def test_a_regular_file_named_ptmx_is_no_master(tmp_path, monkeypatch):
    """V3-N1: `is_pty_master` never decides by a name. A regular file named
    ``ptmx`` is refused outright (ENOTTY), and where the ioctl is unknown
    to the kernel the fallback reads the device (character 5:2), so the
    same file is refused there too while a real master still passes."""
    import fcntl
    import pty

    fake = tmp_path / "ptmx"
    fake.write_bytes(b"not a tty")
    master, slave = pty.openpty()
    regular = os.open(fake, os.O_RDWR)
    try:
        assert keeper.is_pty_master(master) is True
        assert keeper.is_pty_master(slave) is False
        assert keeper.is_pty_master(regular) is False

        def unknown_ioctl(*_args, **_kwargs):
            raise OSError(22, "Invalid argument")

        monkeypatch.setattr(fcntl, "ioctl", unknown_ioctl)
        assert keeper.is_pty_master(master) is True
        assert keeper.is_pty_master(regular) is False
        assert keeper.is_pty_master(slave) is False
    finally:
        os.close(master)
        os.close(slave)
        os.close(regular)


def test_a_bare_keeper_cmdline_serves_only_the_default_socket(tmp_path, monkeypatch):
    """V3-N2: a cmdline naming neither ``--socket`` nor ``--app-id`` (the
    unit's bare ``collins-keeper``) is the default app id's keeper and no
    other socket's."""
    import subprocess
    import sys

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    proc = subprocess.Popen(
        ["collins-keeper", "-c", "import time; time.sleep(60)"],
        executable=sys.executable,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # Popen returns when the exec succeeded, a beat before the kernel
        # publishes the new argv: /proc/<pid>/cmdline reads empty meanwhile.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with open(f"/proc/{proc.pid}/cmdline", "rb") as fh:
                if fh.read():
                    break
            time.sleep(0.01)
        default = keeper.default_socket_path(keeper.DEFAULT_APP_ID)
        assert default == str(tmp_path / "collins" / keeper.DEFAULT_APP_ID / "keeper.sock")
        assert keeper.is_keeper_process(proc.pid) is True
        assert keeper.is_keeper_process(proc.pid, default) is True
        other = keeper.default_socket_path("com.episode6.Collins.Capture")
        assert keeper.is_keeper_process(proc.pid, other) is False
        assert keeper.is_keeper_process(proc.pid, str(tmp_path / "k.sock")) is False
        monkeypatch.delenv("XDG_RUNTIME_DIR")
        assert keeper.is_keeper_process(proc.pid, default) is False
    finally:
        proc.kill()
        proc.wait()
