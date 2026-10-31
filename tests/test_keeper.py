# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The keeper (`collins.service.keeper`, split-service spec §3.10 item 2,
PR-3.6): the cases of `scripts/spike_split_6_keeper.py` as tests, then the
pty server adopting through it and a real service restarted under a
running shell. GTK-free: a keeper subprocess, real ptys, and for the
server's half the GLib main context driven by hand."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

import pytest

from collins.service import keeper, termscreen

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COUNTER = "i=0; while :; do i=$((i+1)); echo $i; done"
TICKER = (
    "import signal, os, sys, time\n"
    "def size(*a):\n"
    "    s = os.get_terminal_size(0)\n"
    "    print('WINCH' if a else 'START', s.lines, s.columns, flush=True)\n"
    "signal.signal(signal.SIGWINCH, size)\n"
    "size()\n"
    "n = 0\n"
    "while True:\n"
    "    n += 1\n"
    "    print('tick', n, flush=True)\n"
    "    time.sleep(0.02)\n"
)
ENV = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp")}


def proc_stat(pid: int) -> dict | None:
    try:
        with open(f"/proc/{pid}/stat") as f:
            text = f.read()
    except OSError:
        return None
    fields = text[text.rindex(")") + 2 :].split()
    return {
        "state": fields[0], "ppid": int(fields[1]), "pgrp": int(fields[2]), "session": int(fields[3]),
        "tty_nr": int(fields[4]), "tpgid": int(fields[5]),
    }  # fmt: skip


def wchar(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/io") as f:
            match = re.search(r"^wchar: (\d+)$", f.read(), re.M)
    except OSError:
        return None
    return int(match.group(1)) if match else None


def wait_for(test, seconds: float = 5.0, what: str = "condition") -> None:
    deadline = time.monotonic() + seconds
    while not test():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.02)


def blocked(pid: int, seconds: float = 5.0) -> bool:
    """Whether the child stopped writing: asleep at 30 looks over 300 ms with
    its wchar standing still (the spike's `blocked`)."""
    start = time.monotonic()
    while time.monotonic() - start < seconds:
        before = wchar(pid)
        asleep = True
        for _ in range(30):
            stat = proc_stat(pid)
            if stat is None or stat["state"] != "S":
                asleep = False
                break
            time.sleep(0.01)
        if asleep and wchar(pid) == before:
            return True
    return False


def drain(fd: int, quiet: float = 0.3, max_bytes: int = 2 * 1024 * 1024, max_s: float = 1.5) -> bytes:
    """Everything readable off *fd* until it has been quiet for *quiet* s,
    or *max_bytes* came, or *max_s* passed (`yes` and the ticker are never
    quiet)."""
    os.set_blocking(fd, False)
    out = bytearray()
    start = last = time.monotonic()
    while time.monotonic() - last < quiet and len(out) < max_bytes and time.monotonic() - start < max_s:
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            time.sleep(0.005)
            continue
        except OSError:
            break
        if not data:
            break
        out += data
        last = time.monotonic()
    return bytes(out)


class KeeperProcess:
    def __init__(self, directory: str) -> None:
        self.socket = os.path.join(directory, "k.sock")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "collins.service.keeper", "--socket", self.socket],
            cwd=REPO,
            env={**ENV, "PYTHONPATH": REPO},
            stdin=subprocess.DEVNULL,
        )
        wait_for(lambda: os.path.exists(self.socket), 10, "the keeper's socket")

    @property
    def pid(self) -> int:
        return self.proc.pid

    def client(self, wait_s: float = 5.0) -> keeper.KeeperClient:
        """A connected client; retried for *wait_s*: a keeper started over
        a dead one's socket file is listening a moment after the file
        shows (the service's `connect_keeper` retries the same way)."""
        deadline = time.monotonic() + wait_s
        while True:
            client = keeper.KeeperClient(self.socket)
            try:
                client.connect()
                return client
            except keeper.KeeperError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def stop(self, sig: int = signal.SIGTERM) -> int | None:
        if self.proc.poll() is None:
            self.proc.send_signal(sig)
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(5)
        return self.proc.returncode


@pytest.fixture
def runtime(tmp_path):
    # A short directory: a socket path is bounded at 107 bytes.
    import tempfile

    directory = tempfile.mkdtemp(prefix="ck-")
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def kept(runtime):
    proc = KeeperProcess(runtime)
    yield proc
    proc.stop()


def kill_child(pid: int) -> None:
    for sig in (signal.SIGCONT, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return


# -- the spike's cases ---------------------------------------------------------------------


def test_yes_the_reader_dies_the_child_blocks_and_resumes_under_the_next(kept):
    """The spike's `yes` case: the keeper is the child's parent; with no
    reader the kernel's buffer fills and the writer blocks; a reader that
    takes the master from `list` sees it write again, its parent, group,
    session and controlling terminal unchanged."""
    client = kept.client()
    pid, master = client.spawn(1, ["yes"], "/tmp", ENV, 120, 40)
    try:
        before = proc_stat(pid)
        assert before["ppid"] == kept.pid
        assert len(drain(master, 0.2)) > 4096
        os.close(master)  # the service died: nobody reads
        assert blocked(pid), "the child kept writing with no reader"
        listed = client.list(with_fds=True)
        assert [e["id"] for e in listed] == [1] and listed[0]["pid"] == pid and listed[0]["alive"]
        fd = listed[0]["fd"]
        assert len(drain(fd, 0.5)) > 100_000  # it writes again
        after = proc_stat(pid)
        assert all(before[k] == after[k] for k in ("ppid", "pgrp", "session", "tty_nr", "tpgid"))
        os.close(fd)
        client.signal(1, signal.SIGTERM)
        status, alive = client.reap(1, wait=3.0)
        assert not alive and status is not None and os.WIFSIGNALED(status) and os.WTERMSIG(status) == 15
        assert client.list(with_fds=False) == []
    finally:
        kill_child(pid)


def test_counter_the_stream_is_whole_across_two_readers(kept):
    """The spike's `counter` case: a numbered stream read by one "service",
    then by the next from the master `list` hands back, is the stream
    byte for byte. (The spike's loss, one read a dying service had taken
    and not written down, cannot happen to a reader that closes its fd:
    nothing it read is lost here, which is the point of the check.)"""
    client = kept.client()
    pid, master = client.spawn(7, ["sh", "-c", COUNTER], "/tmp", ENV, 120, 40)
    try:
        ring = bytearray(drain(master, 0.3))
        os.close(master)
        assert blocked(pid)
        fd = client.list(with_fds=True)[0]["fd"]
        ring += drain(fd, 0.3)
        os.close(fd)
        numbers = [int(n) for n in re.findall(rb"(\d+)\r\n", bytes(ring))]
        assert numbers and numbers == list(range(1, len(numbers) + 1))
    finally:
        kill_child(pid)


def test_winch_resize_through_the_keeper_and_the_shared_flags(kept):
    """The spike's `winch` case: `resize` through the keeper delivers a
    SIGWINCH with no service alive; TIOCSWINSZ and tcgetpgrp work on the
    handed fd; O_NONBLOCK set on the service's duplicate shows on the
    keeper's (one open file description)."""
    client = kept.client()
    pid, master = client.spawn(3, [sys.executable, "-c", TICKER], "/tmp", ENV, 100, 30)
    try:
        text = bytearray(drain(master, 0.6))
        assert b"START 30 100" in text
        assert os.tcgetpgrp(master) == proc_stat(pid)["pgrp"]
        keeper.set_window_size(master, 90, 25)
        text += drain(master, 0.6)
        assert b"WINCH 25 90" in text
        assert client.list(with_fds=False)[0]["nonblock"] is True  # drain set it
        os.set_blocking(master, True)
        assert client.list(with_fds=False)[0]["nonblock"] is False
        os.close(master)  # no service alive
        client.resize(3, 132, 50)
        time.sleep(0.3)
        fd = client.list(with_fds=True)[0]["fd"]
        assert keeper.get_window_size(fd) == (132, 50)
        after = drain(fd, 0.6)
        assert b"WINCH 50 132" in after
        ticks = [int(n) for n in re.findall(rb"tick (\d+)\r\n", bytes(text) + after)]
        assert ticks and ticks == list(range(1, len(ticks) + 1))  # whole across the gap
        os.close(fd)
    finally:
        kill_child(pid)


def test_orphan_the_keeper_dies_the_child_and_the_reader_live_on(kept):
    """The spike's `orphan` case: the keeper killed with SIGKILL leaves the
    child alive and reparented; the service reads on and sees the exit as
    EIO, with no status to be had."""
    client = kept.client()
    pid, master = client.spawn(1, [sys.executable, "-c", TICKER], "/tmp", ENV, 80, 24)
    try:
        assert b"tick" in drain(master, 0.3)
        kept.stop(signal.SIGKILL)
        time.sleep(0.3)
        assert keeper.pid_alive(pid) and proc_stat(pid)["ppid"] != kept.pid
        assert b"tick" in drain(master, 0.3)
        with pytest.raises(keeper.KeeperError):
            client.list(with_fds=False)
        os.kill(pid, signal.SIGTERM)
        wait_for(lambda: not keeper.pid_alive(pid), 5, "the child to go")
        os.set_blocking(master, True)
        with pytest.raises(OSError):
            while os.read(master, 65536):
                pass
    finally:
        kill_child(pid)
        try:
            os.close(master)
        except OSError:
            pass


def test_reap_adopt_and_the_refusals(kept, tmp_path):
    client = kept.client()
    # A spawn whose exec fails answers the child's errno and what it could
    # not reach, the master closed and the child reaped (nothing listed).
    with pytest.raises(OSError) as missing:
        client.spawn(1, ["/nonexistent/program"], "/tmp", ENV, 80, 24)
    assert missing.value.errno == 2 and missing.value.filename == "/nonexistent/program"
    with pytest.raises(OSError) as nodir:
        client.spawn(1, ["sh"], str(tmp_path / "gone"), ENV, 80, 24)
    assert nodir.value.errno == 2 and nodir.value.filename == str(tmp_path / "gone")
    assert client.list(with_fds=False) == []
    pid, master = client.spawn(5, ["sleep", "30"], "/tmp", ENV, 80, 24)
    try:
        with pytest.raises(keeper.KeeperError) as dup:
            client.spawn(5, ["sleep", "30"], "/tmp", ENV, 80, 24)
        assert dup.value.reply["error"] == "duplicate"
        with pytest.raises(keeper.KeeperError) as unknown:
            client.resize(99, 80, 24)
        assert unknown.value.reply["error"] == "unknown"
        # A reap of a living child: alive, no status, the entry kept.
        assert client.reap(5, wait=0) == (None, True)
        assert len(client.list(with_fds=False)) == 1
        # adopt: a master the test forked itself, handed over; the keeper
        # cannot wait for it, so its end is `/proc`'s word, status None.
        own_pid, own_master = keeper.spawn_on_pty(["sleep", "30"], "/tmp", ENV, 80, 24)
        client.adopt(8, own_pid, own_master)
        listed = {e["id"]: e for e in client.list(with_fds=False)}
        assert listed[8]["adopted"] is True and listed[8]["alive"] is True
        os.kill(own_pid, signal.SIGKILL)
        os.waitpid(own_pid, 0)
        assert client.reap(8, wait=1.0) == (None, False)
        assert 8 not in {e["id"] for e in client.list(with_fds=False)}
        os.close(own_master)
    finally:
        kill_child(pid)
    # A protocol the keeper does not speak is refused with its own.
    reply, _ = client.call({"t": "list", "fds": False})
    assert reply["v"] == keeper.KEEPER_PROTOCOL and reply["keeper"] == kept.pid
    keeper._send(client.sock, {"t": "list", "v": 99})
    line, _ = keeper._recv_line(client.sock, client._buffer)
    # The refusal says how many ptys the keeper holds (`held`): what a newer
    # service decides by (PR-3.6's ruling on an older keeper).
    assert json.loads(line) == {
        "ok": False, "error": "protocol", "protocol": 1, "held": 1, "keeper": kept.pid, "v": 1,
    }


def test_sigterm_ends_every_child_and_removes_the_socket(kept):
    """`KillMode=process`: a stop signals the keeper alone, so the keeper
    ends its children itself (SIGHUP, then SIGKILL) and leaves no socket
    or pid file behind."""
    client = kept.client()
    pid, master = client.spawn(1, ["sh", "-c", "trap '' HUP; sleep 60"], "/tmp", ENV, 80, 24)
    os.close(master)
    assert keeper.read_pid(kept.socket) == kept.pid
    assert kept.stop(signal.SIGTERM) == 0
    assert not keeper.pid_alive(pid)
    assert not os.path.exists(kept.socket) and keeper.read_pid(kept.socket) is None


@pytest.mark.skipif(shutil.which("systemd-run") is None, reason="no systemd-run")
def test_systemd_killmode_process_leaves_the_child(runtime):
    """The spike's `systemd` case: the keeper as a transient user unit with
    `KillMode=process`, its main process killed: the child survives in the
    unit's cgroup. Skipped where no user manager answers."""
    socket_path = os.path.join(runtime, "u.sock")
    unit = f"collins-test-keeper-{os.getpid()}.service"
    started = subprocess.run(
        ["systemd-run", "--user", "--collect", "--quiet", "--unit", unit, "-p", "KillMode=process",
         "-E", f"PYTHONPATH={REPO}", sys.executable, "-m", "collins.service.keeper", "--socket", socket_path],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=20,
    )  # fmt: skip
    if started.returncode != 0:
        pytest.skip(f"systemd-run refused: {started.stderr.strip()}")
    child = None
    try:
        wait_for(lambda: os.path.exists(socket_path), 10, "the unit's socket")
        client = keeper.KeeperClient(socket_path)
        client.connect()
        main_pid = client.pid
        # The master stays open here, as the service's duplicate does: a pty
        # whose last master closes hangs its child up, which is no KillMode.
        child, master = client.spawn(1, ["sleep", "40"], "/tmp", ENV, 80, 24)
        subprocess.run(["systemctl", "--user", "kill", "--kill-whom=main", "--signal=SIGKILL", unit],
                       check=False, capture_output=True, timeout=20)  # fmt: skip
        wait_for(lambda: not keeper.pid_alive(main_pid), 5, "the keeper to die")
        time.sleep(0.5)
        assert keeper.pid_alive(child), "KillMode=process: the child must survive its keeper"
        os.close(master)
    finally:
        if child is not None:
            kill_child(child)
        subprocess.run(["systemctl", "--user", "stop", unit], check=False, capture_output=True, timeout=20)
        subprocess.run(["systemctl", "--user", "reset-failed", unit], check=False, capture_output=True,
                       timeout=20)  # fmt: skip


# -- the saved modes ---------------------------------------------------------------------------


def test_the_mode_tracker_dumps_and_restores_what_an_attach_re_asserts():
    """The saved model carries the tracker's modes (PR-3.6): a restored
    tracker's preamble is the original's, and anything off is refused."""
    from collins.service import termstream

    tracker = termstream.ModeTracker()
    events: list = []
    tracker.set_mode(1049, True, True, events)  # the alternate screen
    tracker.set_mode(2004, True, True, events)  # bracketed paste
    tracker.set_mode(1004, True, True, events)  # focus reports
    tracker.set_mode(25, True, False, events)  # cursor hidden
    tracker.kitty_push(5)
    tracker.modify_other_keys = 2
    tracker.scroll_region = (2, 20)
    tracker.designate(1, b"0")
    data = json.loads(json.dumps(tracker.dump()))  # as the file round-trips it
    restored = termstream.ModeTracker()
    assert restored.restore(data) is True
    assert restored.preamble() == tracker.preamble() and restored.assertions() == tracker.assertions()
    assert restored.alt_screen and restored.alt_mode == 1049 and restored.kitty_flags == 5
    assert restored.scroll_region == (2, 20) and restored.charsets[1] == b"0"
    untouched = termstream.ModeTracker()
    for bad in (None, "x", {"private": {"a": True}}, {"charsets": ["B"]}, {"kitty_stack": [999]}):
        assert untouched.restore(bad) is False
    assert untouched.preamble() == termstream.ModeTracker().preamble()


# -- the pty server through the keeper ------------------------------------------------------


def pump(condition, timeout: float = 5.0, what: str = "condition"):
    from gi.repository import GLib

    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        if not context.iteration(False):
            time.sleep(0.002)


def settle(seconds: float = 0.05):
    from gi.repository import GLib

    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not context.iteration(False):
            time.sleep(0.002)


class Sink:
    def __init__(self):
        self.frames = []
        self.events = []

    def send_output(self, data, flags):
        self.frames.append((data, flags))

    def send_event(self, event):
        self.events.append(event)

    def drop_queued(self):
        pass

    def live(self):
        return b"".join(d for d, f in self.frames if f == 0)


class Recorder:
    def __init__(self):
        self.rows = {}

    def record(self, pty_id, row):
        if row is None:
            self.rows.pop(pty_id, None)
        else:
            self.rows[pty_id] = row


def make_server(tmp_path, client, recorder, **kwargs):
    from collins.service import ptyserver

    return ptyserver.PtyServer(
        state_dir=tmp_path / "pty", record=recorder.record, keeper=client, next_id=1, **kwargs
    )


def test_the_server_spawns_through_the_keeper_suspends_and_the_next_adopts(kept, tmp_path):
    """A server spawns `cat` through the keeper (the child is the keeper's,
    the pty kept), a sink types, the model is saved; `suspend` closes the
    server's master and leaves the row and the model; a second server
    adopts from the keeper's list and the row, its model holding the typed
    text, repaints, and typing reaches the same child. The child's exit is
    learnt through the keeper with its status."""
    from collins.service import ptyserver

    recorder = Recorder()
    a = make_server(tmp_path, kept.client(), recorder)
    sink = Sink()
    pty_id = a.spawn("shell", ["cat"], "/tmp", ENV, 80, 24, session="s1", history="s1", ordinal=2)
    pty = a.get(pty_id)
    assert pty.kept and proc_stat(pty.pid)["ppid"] == kept.pid
    a.attach(pty_id, sink, 80, 24)
    a.write(pty_id, b"kept across the restart\r", sink)
    # Two copies on screen: the line discipline's echo and cat's own.
    pump(lambda: pty.screen.rows()[:2] == ["kept across the restart"] * 2, what="the model")
    row = dict(recorder.rows[pty_id])
    assert row["history"] == "s1" and row["ordinal"] == 2 and row["pid"] == pty.pid
    a.suspend()
    assert a.ptys == {} and pty_id in recorder.rows  # the row stays
    assert pty.model_path().exists()
    saved = ptyserver.read_model_file(pty.model_path())
    assert saved["format"] == termscreen.MODEL_FORMAT and "modes" in saved
    child = pty.pid
    assert keeper.pid_alive(child)
    # The next service.
    b = make_server(tmp_path, kept.client(), recorder)
    listed = b.keeper.list(with_fds=True)
    assert [e["id"] for e in listed] == [pty_id]
    adopted = b.adopt(pty_id, listed[0]["fd"], listed[0]["pid"], recorder.rows[pty_id])
    assert adopted.kept and adopted.pid == child and adopted.history == "s1" and adopted.ordinal == 2
    assert adopted.screen.rows()[:2] == ["kept across the restart"] * 2
    assert b._next_id == pty_id + 1
    b.repaint(pty_id)
    assert (adopted.cols, adopted.rows) == (80, 25)
    pump(lambda: (adopted.cols, adopted.rows) == (80, 24), what="the repaint's second step")
    sink2 = Sink()
    b.attach(pty_id, sink2, 80, 24)
    redraw = b"".join(d for d, f in sink2.frames if f & ptyserver.FLAG_REDRAW)
    assert b"kept across the restart" in redraw
    b.write(pty_id, b"and after\r", sink2)
    pump(lambda: adopted.screen.rows()[2:4] == ["and after"] * 2, what="the echo after adoption")
    assert b"and after" in sink2.live()
    # EOF -> the keeper's reap -> pty-exited with the status.
    b.write(pty_id, b"\x04", sink2)  # cat ends at EOF
    pump(lambda: any(e["t"] == "pty-exited" for e in sink2.events), what="pty-exited")
    exited = next(e for e in sink2.events if e["t"] == "pty-exited")
    assert exited["status"] == 0 and pty_id not in recorder.rows and not adopted.model_path().exists()
    assert b.keeper.list(with_fds=False) == []
    settle(0.1)


def test_a_lost_keeper_is_restarted_and_handed_the_masters(kept, runtime, tmp_path):
    """`_keeper_lost`: the keeper killed under a running server; the restart
    callable brings another, which is handed every kept master with
    `adopt`; the child's exit then carries no status (`pty-exited` with
    `status: null`), the spike's F16."""
    recorder = Recorder()
    second = []

    def restart():
        proc = KeeperProcess(runtime)
        second.append(proc)
        return proc.client()

    server = make_server(tmp_path, kept.client(), recorder, keeper_restart=restart)
    sink = Sink()
    pty_id = server.spawn("shell", ["cat"], "/tmp", ENV, 80, 24)
    server.attach(pty_id, sink, 80, 24)
    child = server.get(pty_id).pid
    try:
        kept.stop(signal.SIGKILL)
        assert keeper.pid_alive(child)
        # The next call on the dead keeper (the kept poll) brings the new one.
        pump(lambda: bool(second) and server.keeper is not None and server.keeper.pid == second[0].pid,
             timeout=6, what="a new keeper")  # fmt: skip
        listed = server.keeper.list(with_fds=False)
        assert listed and listed[0]["id"] == pty_id and listed[0]["adopted"] and listed[0]["pid"] == child
        server.write(pty_id, b"still here\r", sink)
        pump(lambda: b"still here" in sink.live(), what="the echo through the orphaned child")
        os.kill(child, signal.SIGKILL)
        pump(lambda: any(e["t"] == "pty-exited" for e in sink.events), timeout=6, what="pty-exited")
        assert next(e for e in sink.events if e["t"] == "pty-exited")["status"] is None
    finally:
        server.shutdown()  # first: no restart, no poll, once the keepers go
        pump(lambda: not server.ptys, timeout=8, what="the ptys to end")
        kill_child(child)
        for proc in second:
            proc.stop()


# -- a real service restarted under a running shell -----------------------------------------


def test_a_service_restart_keeps_the_shell_and_the_next_service_adopts_it(tmp_path):
    """`collins-service` with a keeper: a shell spawned over the API and
    typed into, the service stopped with SIGTERM (exit 0, the row kept,
    `resume_on_start` empty, the child alive, the keeper alive), a second
    service on the same state adopts it: `service.status` counts the pty
    and names the keeper, an attach's redraw holds what was typed, and
    typing reaches the same child."""
    import shutil as _shutil

    from liveservice import LiveService

    from collins.api import protocol
    from collins.api.client import SocketLink

    live = LiveService(tmp_path, app_id="com.example.Keeper", keeper=True)
    live.env["SHELL"] = "/bin/sh"
    child = None
    try:
        live.start()
        keeper_pid = live.keeper_pid()
        assert keeper_pid is not None and keeper.pid_alive(keeper_pid)
        link = SocketLink(live.socket, app_id=live.app_id, device="a")
        link.connect()
        status = link.call({"t": "service.status"})
        assert status["keeper"] == keeper_pid
        output = []
        client = link.pty_client(lambda pty, data, flags: output.append((pty, data, flags)), lambda e: None)
        spawn = {"t": "spawn", "kind": "shell", "cwd": str(tmp_path), "cols": 100, "rows": 30}
        pty = client.request(spawn)["pty"]
        client.request({"t": "attach", "pty": pty, "cols": 100, "rows": 30})
        link.send_input(pty, b"echo kept-$((40+2))\r")
        pump(lambda: any(b"kept-42" in d for p, d, f in output if f == 0), timeout=10, what="the echo")
        child = live.state()["ptys"][live.app_id][str(pty)]["pid"]  # the table by app id (M4)
        link.shutdown()
        assert live.stop(signal.SIGTERM) == 0
        state = live.state()
        assert str(pty) in state["ptys"][live.app_id] and state.get("resume_on_start", []) == []
        assert keeper.pid_alive(child) and keeper.pid_alive(keeper_pid)
        model = tmp_path / "pty" / live.app_id / f"{pty}.model"  # the models by app id (M4)
        assert model.exists()
        # The next service.
        live.proc = None
        live.start()
        link = SocketLink(live.socket, app_id=live.app_id, device="b")
        link.connect()
        status = link.call({"t": "service.status"})
        assert status["ptys"] == 1 and status["keeper"] == keeper_pid
        output = []
        client = link.pty_client(lambda pty, data, flags: output.append((pty, data, flags)), lambda e: None)
        reply = client.request({"t": "attach", "pty": pty, "cols": 100, "rows": 30})
        assert reply["cols"] == 100
        # The redraw's frames land on the primary channel after the reply.
        pump(lambda: any(f & protocol.FLAG_REDRAW_END for p, d, f in output), timeout=10, what="the redraw")
        redraw = b"".join(d for p, d, f in output if f & protocol.FLAG_REDRAW)
        assert b"kept-42" in redraw
        info = client.request({"t": "pty.info", "pty": pty})
        assert info["child_pid"] == child
        link.send_input(pty, b"echo again-$((40+3))\r")
        pump(lambda: any(b"again-43" in d for p, d, f in output if f == 0), timeout=10, what="the echo after")
        link.shutdown()
    finally:
        live.close()
        if child is not None:
            kill_child(child)
        _shutil.rmtree(live.runtime, ignore_errors=True)
