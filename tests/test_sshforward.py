# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The ssh forward (collins.sshforward, spec §3.2, §3.24, PR-3.1) with a
fake `subprocess.run` and `Popen`: the two argv, exit 255 reported with
ssh's last line, the local socket unlinked first (and a non-socket
refused), the child's death as the transport's `lost`, and the transport's
find/start/connect_failed round."""

import errno
import json
import os
import signal
import socket
import subprocess
import threading
import time

import pytest

from collins import connections, sshforward

CONNECTION = connections.Connection(
    id="a" * 32, name="box", kind="ssh", target="collins@localhost:2222"
)
PLAIN = connections.Connection(id="b" * 32, name="plain", kind="ssh", target="build.example.net")


# ---- argv ---------------------------------------------------------------------------


def test_print_socket_argv_is_batch_mode_with_the_port():
    argv = sshforward.print_socket_argv(CONNECTION)
    assert argv[0] == "ssh"
    # The remote command is one fixed string: a POSIX `sh -c` outer layer
    # (single quotes only, literal in every shell family) running the box's
    # login shell as `-lic` for the POSIX family and fish, `sh -lc` otherwise
    # (Q3 of PR 623's review, amended by its verification).
    assert argv[-2] == "collins@localhost"
    assert argv[-1] == sshforward.remote_command("collins-service --print-socket")
    assert argv[-1].startswith("sh -c 'case \"${SHELL##*/}\" in bash|zsh|dash|sh|ksh|mksh|fish) ")
    # -lc first; -lic only on 127 (the second verification of PR 623, V1).
    assert '"$SHELL" -lc "exec collins-service --print-socket"; rc=$?; ' in argv[-1]
    fallback = 'if [ "$rc" -eq 127 ]; then exec "$SHELL" -lic "exec collins-service --print-socket"; fi; '
    assert fallback in argv[-1]
    assert 'exit "$rc";; ' in argv[-1]
    assert argv[-1].endswith('*) exec sh -lc "exec collins-service --print-socket";; esac\'')
    assert argv[-1].count("'") == 2 and "!" not in argv[-1]  # the outer quotes only; no csh history
    assert "-o" in argv and "BatchMode=yes" in argv
    assert argv[argv.index("-p") + 1] == "2222"
    for option in ("ControlMaster=no", "ControlPath=none", "RemoteCommand=none", "RequestTTY=no"):
        assert option in argv
    assert f"ServerAliveInterval={sshforward.SERVER_ALIVE_INTERVAL_S}" in argv
    # No port: no -p; the destination is the bare host.
    plain = sshforward.print_socket_argv(PLAIN)
    assert "-p" not in plain and plain[-2] == "build.example.net"


def test_print_socket_argv_carries_a_remote_app_id():
    other = connections.Connection(
        id="c" * 32, name="x", kind="ssh", target="h", remote_app_id="com.episode6.Collins.Debug"
    )
    argv = sshforward.print_socket_argv(other)
    assert argv[-1] == sshforward.remote_command(
        "collins-service --print-socket --app-id com.episode6.Collins.Debug"
    )
    assert 'exec collins-service --print-socket --app-id com.episode6.Collins.Debug"' in argv[-1]


def test_forward_argv_is_the_spec_command():
    argv = sshforward.forward_argv(CONNECTION, "/run/user/1/collins/remote/a.sock", "/home/c/api.sock")
    assert argv[0] == "ssh"
    assert argv[argv.index("-o") + 1] == "ExitOnForwardFailure=yes"
    for option in ("ServerAliveInterval=15", "ServerAliveCountMax=3", "BatchMode=yes"):
        assert option in argv
    assert "-N" in argv
    assert argv[argv.index("-L") + 1] == "/run/user/1/collins/remote/a.sock:/home/c/api.sock"
    assert argv[-1] == "collins@localhost"
    assert argv[argv.index("-p") + 1] == "2222"


def test_argv_refuses_a_connection_with_no_valid_target():
    bad = connections.Connection(id="d" * 32, name="x", kind="ssh", target="-oProxyCommand=x h")
    with pytest.raises(ValueError):
        sshforward.print_socket_argv(bad)
    with pytest.raises(ValueError):
        sshforward.forward_argv(bad, "/l", "/r")


# ---- the local socket -----------------------------------------------------------------


def test_local_socket_path_is_under_the_runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")  # nothing is created by the path alone
    path = sshforward.local_socket_path("a" * 32, "com.episode6.Collins")
    assert path == "/run/user/1000/collins/remote/com.episode6.Collins/" + "a" * 32 + ".sock"
    # Scoped by app id (N1): the debug instance shares ui-state.json and the
    # connection ids, never a forward's path.
    debug = sshforward.local_socket_path("a" * 32, "com.episode6.Collins.Debug")
    assert debug != path and debug.endswith("/com.episode6.Collins.Debug/" + "a" * 32 + ".sock")


def test_local_socket_path_falls_back_to_the_state_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", "/home/u/.local/state")
    path = sshforward.local_socket_path("a" * 32, "com.episode6.Collins")
    assert path == "/home/u/.local/state/collins/remote/com.episode6.Collins/" + "a" * 32 + ".sock"


def test_a_too_long_runtime_dir_takes_the_hashed_stand_in(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / ("x" * 120)))
    path = sshforward.local_socket_path("a" * 32)
    assert len(path.encode()) <= sshforward.api_server.SOCKET_PATH_MAX + len("a" * 32 + ".sock") + 1
    assert "collins-" in path and str(tmp_path) not in path


def test_unlink_socket_removes_a_socket_and_refuses_anything_else(tmp_path):
    path = tmp_path / "s.sock"
    sshforward.unlink_socket(str(path))  # nothing there: fine
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.bind(str(path))
    assert path.exists()
    sshforward.unlink_socket(str(path))
    assert not path.exists()
    (tmp_path / "f").write_text("not a socket")
    with pytest.raises(OSError):
        sshforward.unlink_socket(str(tmp_path / "f"))
    assert (tmp_path / "f").exists()


# ---- the words ------------------------------------------------------------------------


def test_last_line_is_the_last_non_empty_line_cleaned_and_bounded():
    denied = b"Welcome\n\nPermission denied (publickey).\r\n\n"
    assert sshforward.last_line(denied) == "Permission denied (publickey)."
    assert sshforward.last_line(None) == "" and sshforward.last_line(b"\n\n") == ""
    assert sshforward.last_line("a\x1bb\x07 ") == "ab"
    assert len(sshforward.last_line(b"x" * 1000)) == sshforward.DETAIL_MAX


def test_exit_text_names_the_target_and_the_detail():
    text = sshforward.exit_text(CONNECTION, 255, "Connection closed by remote host")
    assert "collins@localhost:2222" in text and "Connection closed by remote host" in text
    assert "closed" in sshforward.exit_text(CONNECTION, 0, "")
    assert "(exit 1)" in sshforward.exit_text(CONNECTION, 1, "")
    # A killed child (Popen's -N) says the signal, not "exit -9".
    assert "signal 9" in sshforward.exit_text(CONNECTION, -9, "")


# ---- --print-socket -------------------------------------------------------------------


class FakeRun:
    def __init__(self, returncode=0, stdout=b"", stderr=b"", raise_=None):
        self.calls = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.raise_ = raise_

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.raise_ is not None:
            raise self.raise_
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, self.stderr)


def test_print_socket_reads_the_path_off_the_last_line():
    run = FakeRun(stdout=b"motd line\n/home/collins/.local/state/collins/com.episode6.Collins/api.sock\n")
    result = sshforward.print_socket(CONNECTION, run=run)
    assert result.ok and result.path == "/home/collins/.local/state/collins/com.episode6.Collins/api.sock"
    argv, kwargs = run.calls[0]
    assert argv == sshforward.print_socket_argv(CONNECTION)
    assert kwargs["stdin"] is subprocess.DEVNULL and kwargs["timeout"] == sshforward.PRINT_SOCKET_TIMEOUT_S


def test_print_socket_reports_exit_255_with_the_last_stderr_line():
    run = FakeRun(returncode=255, stderr=b"banner\ncollins@localhost: Permission denied (publickey).\n")
    result = sshforward.print_socket(CONNECTION, run=run)
    assert not result.ok and result.code == 255 and result.batch_mode_hint
    assert "Permission denied (publickey)." in result.text and "collins@localhost:2222" in result.text


def test_print_socket_reports_the_remote_commands_failure():
    run = FakeRun(returncode=1, stderr=b"collins-service: no service answered on /x\n")
    result = sshforward.print_socket(CONNECTION, run=run)
    assert not result.ok and result.code == 1 and not result.batch_mode_hint
    assert "exit 1" in result.text and "no service answered" in result.text


def test_print_socket_reports_a_timeout_and_a_missing_ssh():
    timed_out = FakeRun(raise_=subprocess.TimeoutExpired(["ssh"], 10, stderr=b"slow\n"))
    result = sshforward.print_socket(CONNECTION, run=timed_out, timeout=10)
    assert not result.ok and "10 s" in result.text and result.detail == "slow"
    result = sshforward.print_socket(CONNECTION, run=FakeRun(raise_=FileNotFoundError("ssh")))
    assert not result.ok and "ssh is not installed" in result.text


def test_print_socket_refuses_a_bad_path():
    result = sshforward.print_socket(CONNECTION, run=FakeRun(stdout=b"relative/path\n"))
    assert not result.ok and "no socket path" in result.text
    result = sshforward.print_socket(CONNECTION, run=FakeRun(stdout=b""))
    assert not result.ok


# ---- the forward child ----------------------------------------------------------------


class FakeProc:
    """A Popen whose stderr the test writes and whose exit the test sets."""

    def __init__(self, argv, **kwargs):
        self.argv = argv
        self.kwargs = kwargs
        self.pid = 4242
        self._code = None
        self._exit = threading.Event()
        read_fd, write_fd = os.pipe()
        self.stderr = os.fdopen(read_fd, "rb", buffering=0)
        self._write_fd = write_fd
        self.terminated = 0

    def poll(self):
        return self._code

    def wait(self, timeout=None):
        if not self._exit.wait(timeout):
            raise subprocess.TimeoutExpired(self.argv, timeout)
        return self._code

    def say(self, text: bytes):
        os.write(self._write_fd, text)

    def exit(self, code: int):
        self._code = code
        os.close(self._write_fd)
        self._exit.set()

    def terminate(self):
        self.terminated += 1
        self.exit(-15)

    def kill(self):
        self.exit(-9)


def test_forward_unlinks_the_socket_first_and_reports_its_exit(tmp_path):
    local = tmp_path / "a.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.bind(str(local))
    exits = []
    procs = []

    def popen(argv, **kwargs):
        proc = FakeProc(argv, **kwargs)
        procs.append(proc)
        return proc

    forward = sshforward.Forward(
        CONNECTION, "/remote/api.sock", local_path=str(local),
        on_exit=lambda fwd, code, detail: exits.append((fwd, code, detail)), popen=popen,
    )
    forward.start()
    assert not local.exists()  # unlinked before the child binds it
    proc = procs[0]
    assert proc.argv == sshforward.forward_argv(CONNECTION, str(local), "/remote/api.sock")
    assert proc.kwargs["stdin"] is subprocess.DEVNULL and proc.kwargs["stderr"] is subprocess.PIPE
    assert forward.alive
    proc.say(b"Warning: Permanently added\n")
    proc.say(b"client_loop: send disconnect: Broken pipe\n")
    proc.exit(255)
    forward._reader.join(5)
    assert exits == [(forward, 255, "client_loop: send disconnect: Broken pipe")]
    assert not forward.alive


def test_forward_refuses_a_non_socket_at_its_path(tmp_path):
    local = tmp_path / "a.sock"
    local.write_text("a file")
    forward = sshforward.Forward(CONNECTION, "/r", local_path=str(local), popen=FakeProc)
    with pytest.raises(OSError):
        forward.start()
    assert local.read_text() == "a file" and forward.proc is None


def test_forward_stop_is_quiet(tmp_path):
    exits = []
    procs = []

    def popen(argv, **kwargs):
        procs.append(FakeProc(argv, **kwargs))
        return procs[-1]

    forward = sshforward.Forward(
        CONNECTION, "/r", local_path=str(tmp_path / "a.sock"),
        on_exit=lambda *a: exits.append(a), popen=popen,
    )
    forward.start()
    forward.stop()
    forward._reader.join(5)
    assert procs[0].terminated == 1 and exits == []


# ---- the transport --------------------------------------------------------------------


class FakeForward:
    made = []

    def __init__(self, connection, remote_path, *, local_path=None, on_exit=None):
        self.connection = connection
        self.remote_path = remote_path
        self.local_path = local_path
        self.on_exit = on_exit
        self.started = 0
        self.stopped = 0
        self.alive = False
        FakeForward.made.append(self)

    def start(self):
        self.started += 1
        self.alive = True

    def stop(self):
        self.stopped += 1
        self.alive = False

    def die(self, code, detail):
        self.alive = False
        self.on_exit(self, code, detail)


def _transport(tmp_path, monkeypatch, results, probe="live"):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    FakeForward.made = []
    lost = []
    landed = []

    def land(fn):
        landed.append(fn)
        fn()

    queue = list(results)
    transport = sshforward.RemoteTransport(
        CONNECTION,
        on_lost=lost.append,
        land=land,
        print_socket=lambda c: queue.pop(0),
        make_forward=FakeForward,
        probe=lambda path: probe,
    )
    return transport, lost


def test_transport_find_start_and_the_forwards_death_is_lost(tmp_path, monkeypatch):
    ok = sshforward.PrintSocketResult(ok=True, path="/remote/api.sock")
    transport, lost = _transport(tmp_path, monkeypatch, [ok, ok])
    path, state = transport.find("app")
    assert path == sshforward.local_socket_path(CONNECTION.id) and state == "none"
    assert transport.start("app") == sshforward.STARTED_HOW
    forward = FakeForward.made[0]
    assert forward.remote_path == "/remote/api.sock" and forward.local_path == path and forward.started == 1
    assert transport.find("app") == (path, "live")
    forward.die(255, "Connection closed by remote host")
    assert len(lost) == 1 and "Connection closed by remote host" in lost[0]
    assert transport.find("app") == (path, "none")
    # The next start runs print-socket again and makes a new forward.
    transport.start("app")
    assert len(FakeForward.made) == 2 and FakeForward.made[1].started == 1


def test_transport_start_raises_the_print_sockets_word(tmp_path, monkeypatch):
    failed = sshforward.PrintSocketResult(ok=False, code=255, text="ssh could not connect to x: nope")
    transport, lost = _transport(tmp_path, monkeypatch, [failed])
    with pytest.raises(sshforward.ForwardError, match="nope"):
        transport.start("app")
    assert FakeForward.made == [] and lost == []


def test_a_failed_connect_makes_the_next_find_none(tmp_path, monkeypatch):
    ok = sshforward.PrintSocketResult(ok=True, path="/remote/api.sock")
    transport, _lost = _transport(tmp_path, monkeypatch, [ok, ok])
    transport.start("app")
    assert transport.find("app")[1] == "live"
    transport.connect_failed()
    assert transport.find("app")[1] == "none"
    transport.start("app")  # replaces the forward: the old one stopped, no loss reported
    assert FakeForward.made[0].stopped == 1 and len(FakeForward.made) == 2
    assert transport.find("app")[1] == "live"


def test_a_replaced_forwards_late_death_is_not_a_loss(tmp_path, monkeypatch):
    ok = sshforward.PrintSocketResult(ok=True, path="/remote/api.sock")
    transport, lost = _transport(tmp_path, monkeypatch, [ok, ok])
    transport.start("app")
    old = FakeForward.made[0]
    transport.connect_failed()
    transport.start("app")
    old.die(255, "late")
    assert lost == []
    transport.stop()
    assert FakeForward.made[1].stopped == 1
    FakeForward.made[1].die(0, "")
    assert lost == [] and transport.find("app")[1] == "none"


# ---- PR 623's review: the printed path, a stop mid-start, the pid file ------------------


@pytest.mark.parametrize(
    "line",
    [
        "/tmp/x-${R623_SECRET}-%u.sock",  # ssh expands ${VAR} and %u on this side (M3)
        "/tmp/x-%d.sock",
        "/tmp/a:b.sock",  # misparses -L
        "/tmp/x y.sock",
        "/tmp/~x.sock",
        "relative/path",
        "",
        "/" + "a" * 108,  # past sun_path on the far side
    ],
)
def test_print_socket_refuses_a_path_ssh_would_expand_or_misparse(line):
    result = sshforward.print_socket(CONNECTION, run=FakeRun(stdout=(line + "\n").encode()))
    assert not result.ok and "no socket path" in result.text


def test_forward_argv_refuses_a_bad_path_on_either_side():
    with pytest.raises(ValueError):
        sshforward.forward_argv(CONNECTION, "/run/user/1/x.sock", "/tmp/${HOME}.sock")
    with pytest.raises(ValueError):
        sshforward.forward_argv(CONNECTION, "/run/user/1/%u.sock", "/tmp/api.sock")


def test_exit_127_says_the_path():
    run = FakeRun(returncode=127, stderr=b"bash: line 1: collins-service: command not found\n")
    result = sshforward.print_socket(CONNECTION, run=run)
    assert not result.ok and "command not found" in result.text
    assert "login shell's PATH" in result.text


def test_a_stop_during_print_socket_spawns_nothing(tmp_path, monkeypatch):
    """M2(b): the worker is in --print-socket when a switch or a quit stops
    the transport; the start must raise and leave no forward alive."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    FakeForward.made = []
    entered = threading.Event()
    release = threading.Event()

    def blocking_print_socket(connection):
        entered.set()
        release.wait(5)
        return sshforward.PrintSocketResult(ok=True, path="/remote/api.sock")

    transport = sshforward.RemoteTransport(
        CONNECTION, on_lost=lambda text: None, print_socket=blocking_print_socket,
        make_forward=FakeForward, probe=lambda path: "live",
    )
    outcome = {}

    def work():
        try:
            transport.start("app")
        except sshforward.ForwardError as exc:
            outcome["error"] = str(exc)

    worker = threading.Thread(target=work)
    worker.start()
    assert entered.wait(5)
    transport.stop()  # lands while print-socket runs
    release.set()
    worker.join(5)
    assert outcome == {"error": "stopped"}
    assert not any(f.alive for f in FakeForward.made)
    assert transport.forward is None and transport.find("app")[1] == "none"


def test_a_stop_right_after_the_spawn_ends_the_child(tmp_path, monkeypatch):
    """The other gap: stopped between forward.start() and the return."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    FakeForward.made = []
    ok = sshforward.PrintSocketResult(ok=True, path="/remote/api.sock")
    holder = {}

    class StoppingForward(FakeForward):
        def start(self):
            super().start()
            holder["transport"].stop()  # the stop lands the instant the child is up

    transport = sshforward.RemoteTransport(
        CONNECTION, on_lost=lambda text: None, print_socket=lambda c: ok,
        make_forward=StoppingForward, probe=lambda path: "live",
    )
    holder["transport"] = transport
    with pytest.raises(sshforward.ForwardError, match="stopped"):
        transport.start("app")
    assert FakeForward.made[0].stopped >= 1 and not FakeForward.made[0].alive
    assert transport.forward is None


def test_a_death_in_the_first_instant_is_this_forwards(tmp_path, monkeypatch):
    """Nit 6: `self.forward` is known before start(), so a child that dies at
    once is reported, not dropped as a stranger's."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    FakeForward.made = []
    lost = []
    ok = sshforward.PrintSocketResult(ok=True, path="/remote/api.sock")

    class DyingForward(FakeForward):
        def start(self):
            super().start()
            self.die(255, "Connection closed by remote host")

    transport = sshforward.RemoteTransport(
        CONNECTION, on_lost=lost.append, print_socket=lambda c: ok,
        make_forward=DyingForward, probe=lambda path: "live",
    )
    transport.start("app")
    assert len(lost) == 1 and "Connection closed by remote host" in lost[0]


def test_the_pid_file_is_written_and_a_stale_forward_ended(tmp_path):
    """Q2: the ssh child's pid, this client's and the socket, beside the
    socket; a later start on the same socket ends the recorded child when
    its command line is still that forward's."""
    local = str(tmp_path / "a.sock")
    # A real child standing in for ssh, its argv carrying the -L the sweep
    # looks for (bash's `exec -a` names it ssh; a sh without it leaves the
    # name, and the sweep must then touch nothing).
    child = subprocess.Popen(
        ["bash", "-c", f'exec -a ssh bash -c "sleep 300; true" x -N -L {local}:/remote/api.sock'],
        stdin=subprocess.DEVNULL,
    )
    try:
        time.sleep(0.3)
        sshforward.write_pid_file(local, child.pid)
        record = sshforward.read_pid_file(sshforward.pid_path(local))
        assert record == {"ssh": child.pid, "client": os.getpid(), "socket": local}
        assert os.stat(sshforward.pid_path(local)).st_mode & 0o777 == 0o600
        ours = sshforward.is_our_forward(child.pid, local)
        ended = sshforward.end_stale_forward(local)
        if ours:
            assert ended == child.pid
            child.wait(5)
            assert child.poll() is not None
        else:
            assert ended is None and child.poll() is None
        assert not os.path.exists(sshforward.pid_path(local))
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_the_sweep_touches_no_living_clients_forward_and_no_reused_pid(tmp_path):
    directory = str(tmp_path)
    live_local = os.path.join(directory, "live.sock")
    # Another client of this device, alive (this process): left alone.
    sshforward.write_pid_file(live_local, 2)
    # A client that is gone (a pid nobody has), whose "ssh" pid is this
    # process: not an ssh, so nothing is signalled; the file is removed.
    dead_local = os.path.join(directory, "dead.sock")
    path = sshforward.pid_path(dead_local)
    with open(path, "w") as fh:
        json.dump({"ssh": os.getpid(), "client": 2**22 - 1, "socket": dead_local}, fh)
    (tmp_path / "junk.pid").write_text("not json")
    assert sshforward.sweep_orphans(directory) == []
    assert os.path.exists(sshforward.pid_path(live_local))
    assert not os.path.exists(path)


def test_read_pid_file_validates(tmp_path):
    path = str(tmp_path / "x.pid")
    for raw in ('{"ssh": "4", "client": 5, "socket": "/a"}', '{"ssh": 4, "client": 5, "socket": "/a b"}',
                "[]", "x" * 5000, '{"ssh": 0, "client": 5, "socket": "/a"}'):
        with open(path, "w") as fh:
            fh.write(raw)
        assert sshforward.read_pid_file(path) is None
    assert sshforward.read_pid_file(str(tmp_path / "none")) is None


def test_forward_start_writes_the_pid_file_and_stop_removes_it(tmp_path):
    local = tmp_path / "a.sock"
    procs = []

    def popen(argv, **kwargs):
        procs.append(FakeProc(argv, **kwargs))
        return procs[-1]

    forward = sshforward.Forward(CONNECTION, "/remote/api.sock", local_path=str(local), popen=popen)
    forward.start()
    record = sshforward.read_pid_file(sshforward.pid_path(str(local)))
    assert record["ssh"] == 4242 and record["client"] == os.getpid()
    forward.stop()
    forward._reader.join(5)
    assert not os.path.exists(sshforward.pid_path(str(local)))


# ---- the verification of PR 623 ---------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "/home/jo.doe@corp.example/.local/state/collins/com.episode6.Collins/api.sock",
        "/home/jo.doe@corp.example/a+b,c=d.sock",
    ],
)
def test_print_socket_takes_an_ad_home(line):
    """N4: ssh passes `@ + , =` through -L untouched; an SSSD/AD home holds them."""
    result = sshforward.print_socket(CONNECTION, run=FakeRun(stdout=(line + "\n").encode()))
    assert result.ok and result.path == line
    assert sshforward.forward_argv(CONNECTION, "/run/user/1/collins/remote/x/a.sock", line)


def test_two_app_ids_never_share_a_forward_and_a_living_client_is_left_alone(tmp_path, monkeypatch):
    """N1: `collins` and `./start-debug` share ui-state.json (and so a
    connection id) — their forwards live under their own app id's
    directory, and a record whose client still lives (another process) is
    never taken over: its ssh stays, its pid file stays."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    real = sshforward.RemoteTransport(
        CONNECTION, on_lost=lambda t: None, print_socket=lambda c: None,
        make_forward=FakeForward, probe=lambda p: "live", app_id="com.episode6.Collins",
    )
    debug = sshforward.RemoteTransport(
        CONNECTION, on_lost=lambda t: None, print_socket=lambda c: None,
        make_forward=FakeForward, probe=lambda p: "live", app_id="com.episode6.Collins.Debug",
    )
    assert real.local_path != debug.local_path
    assert os.path.dirname(real.local_path) != os.path.dirname(debug.local_path)
    # A living foreign client's forward on this very path (a reused client
    # pid, or a second client of the same app id): the stand-in child is a
    # renamed ssh with the -L in its argv, its "client" another living pid
    # (this test's parent process).
    local = str(tmp_path / "a.sock")
    child = subprocess.Popen(
        ["bash", "-c", f'exec -a ssh bash -c "sleep 300; true" x -N -L {local}:/remote/api.sock'],
        stdin=subprocess.DEVNULL,
    )
    try:
        time.sleep(0.3)
        with open(sshforward.pid_path(local), "w") as fh:
            json.dump({"ssh": child.pid, "client": os.getppid(), "socket": local}, fh)
        assert sshforward.end_stale_forward(local) is None
        assert child.poll() is None  # alive
        assert os.path.exists(sshforward.pid_path(local))  # and its record kept
        # The same record naming this process as the client is ours to end.
        with open(sshforward.pid_path(local), "w") as fh:
            json.dump({"ssh": child.pid, "client": os.getpid(), "socket": local}, fh)
        if sshforward.is_our_forward(child.pid, local):
            assert sshforward.end_stale_forward(local) == child.pid
            child.wait(5)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_the_sweep_ignores_a_record_naming_a_socket_elsewhere(tmp_path):
    """N5: a record's `socket` must be the file's own `<name>.sock`; the
    sweep neither unlinks a pid file elsewhere nor signals for it."""
    directory = tmp_path / "remote"
    directory.mkdir()
    elsewhere = tmp_path / "out-x"
    elsewhere.mkdir()
    target_pid = sshforward.pid_path(str(elsewhere / "thing.sock"))
    pathlib_write = open(target_pid, "w")
    pathlib_write.write("{}")
    pathlib_write.close()
    with open(directory / "aaaa.pid", "w") as fh:
        json.dump({"ssh": os.getpid(), "client": 2**22 - 1, "socket": str(elsewhere / "thing.sock")}, fh)
    assert sshforward.sweep_orphans(str(directory)) == []
    assert os.path.exists(target_pid)  # untouched
    assert os.path.exists(directory / "aaaa.pid")  # ignored, not removed


# ---- the second verification of PR 623: the login shell's two startup shapes -------------


def _login_shell_home(tmp_path, *, path_only_interactive: bool, handover: bool):
    """A scratch HOME for a real bash: a fake collins-service in a bin dir,
    a .bash_profile that sets the login PATH (with or without that dir) and
    sources .bashrc, and a .bashrc guarded the Debian way (returns for a
    non-interactive shell) that then adds the dir (when the PATH is
    interactive-only) and/or hands the shell on with `exec sh`."""
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "collins-service"
    fake.write_text("#!/bin/sh\necho /home/u/.local/state/collins/com.episode6.Collins/api.sock\n")
    fake.chmod(0o755)
    login_path = "/usr/bin:/bin" if path_only_interactive else f"{bin_dir}:/usr/bin:/bin"
    (home / ".bash_profile").write_text(
        f'export PATH="{login_path}"\n[ -f ~/.bashrc ] && . ~/.bashrc\n'
    )
    rc = ["case $- in *i*) ;; *) return;; esac"]
    if path_only_interactive:
        rc.append(f'export PATH="{bin_dir}:$PATH"')
    if handover:
        rc.append("exec sh")  # stands for `exec zsh` / `exec fish` / auto-tmux
    (home / ".bashrc").write_text("\n".join(rc) + "\n")
    return home


def _run_wrapper(home, command="collins-service --print-socket"):
    env = {"HOME": str(home), "SHELL": "/usr/bin/bash", "PATH": "/usr/bin:/bin", "LANG": "C"}
    # The outer layer as sshd would run it: the user's shell with -c.
    return subprocess.run(
        ["/usr/bin/bash", "-c", sshforward.remote_command(command)],
        env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=20, check=False,
    )


@pytest.mark.skipif(not os.path.exists("/usr/bin/bash"), reason="needs bash")
def test_a_path_set_only_in_the_interactive_rc_is_found_by_the_lic_round(tmp_path):
    """pipx's ensurepath writes ~/.bashrc, behind Debian's guard: -lc exits
    127, and the wrapper's -lic round finds it."""
    home = _login_shell_home(tmp_path, path_only_interactive=True, handover=False)
    completed = _run_wrapper(home)
    assert completed.returncode == 0, completed.stderr
    assert sshforward.last_line(completed.stdout) == (
        "/home/u/.local/state/collins/com.episode6.Collins/api.sock"
    )


@pytest.mark.skipif(not os.path.exists("/usr/bin/bash"), reason="needs bash")
def test_a_bashrc_that_hands_the_shell_on_does_not_swallow_the_command(tmp_path):
    """V1: `exec zsh` at the end of ~/.bashrc. With the PATH in the login
    profile the -lc round runs the command and never reads the rc."""
    home = _login_shell_home(tmp_path, path_only_interactive=False, handover=True)
    completed = _run_wrapper(home)
    assert completed.returncode == 0, completed.stderr
    assert sshforward.last_line(completed.stdout).endswith("/api.sock")


@pytest.mark.skipif(not os.path.exists("/usr/bin/bash"), reason="needs bash")
def test_both_shapes_at_once_are_reported_as_the_handover(tmp_path):
    """The PATH only in the rc *and* the rc handing the shell on: -lc is
    127, -lic runs the rc, `exec sh` takes over, the command never runs
    (exit 0, nothing printed) — and print_socket names the hand-over."""
    home = _login_shell_home(tmp_path, path_only_interactive=True, handover=True)
    completed = _run_wrapper(home)
    assert completed.returncode == 0 and sshforward.last_line(completed.stdout) == ""
    result = sshforward.print_socket(CONNECTION, run=FakeRun(returncode=0, stdout=b""))
    assert not result.ok and "no socket path" in result.text and "interactive startup" in result.text
    # A non-empty but bad path is not called a hand-over.
    result = sshforward.print_socket(CONNECTION, run=FakeRun(returncode=0, stdout=b"relative\n"))
    assert "interactive startup" not in result.text


def test_signal_forward_falls_back_when_pidfd_open_is_refused(tmp_path, monkeypatch):
    """V2: ENOSYS / EPERM / EINVAL from pidfd_open take the check-then-kill
    road; ESRCH is "gone"."""
    local = str(tmp_path / "a.sock")
    child = subprocess.Popen(
        ["bash", "-c", f'exec -a ssh bash -c "sleep 300; true" x -N -L {local}:/remote/api.sock'],
        stdin=subprocess.DEVNULL,
    )
    try:
        time.sleep(0.3)
        if not sshforward.is_our_forward(child.pid, local):
            pytest.skip("this bash could not rename its argv")

        def refused(pid, flags=0):
            raise OSError(errno.ENOSYS, "no pidfd_open here")

        monkeypatch.setattr(os, "pidfd_open", refused, raising=False)
        assert sshforward._signal_forward(child.pid, local, signal.SIGTERM) is True
        child.wait(5)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()

    def gone(pid, flags=0):
        raise OSError(errno.ESRCH, "no such process")

    monkeypatch.setattr(os, "pidfd_open", gone, raising=False)
    assert sshforward._signal_forward(child.pid, local, signal.SIGTERM) is False


# ---- the third verification of PR 623: the two words -------------------------------------


def test_the_handover_word_blames_no_one_file():
    """W1: the exec may sit in ~/.profile or ~/.bash_profile as well as
    ~/.bashrc; the word names the kind of file and the fix that holds for
    each (the PATH above the line, or a tty guard)."""
    result = sshforward.print_socket(CONNECTION, run=FakeRun(returncode=0, stdout=b""))
    assert "login or interactive startup file" in result.text
    assert "~/.profile, ~/.bash_profile, ~/.bashrc" in result.text
    assert "[ -t 0 ]" in result.text
    assert "Put the PATH in ~/.profile" not in result.text


def test_exit_127_blames_the_path_only_when_the_shell_names_collins_service():
    """W2: a $SHELL that is not installed makes both rounds exit 127 too;
    the PATH hint is for a shell that said "collins-service: not found"."""
    run = FakeRun(returncode=127, stderr=b"bash: line 1: collins-service: command not found\n")
    result = sshforward.print_socket(CONNECTION, run=run)
    assert "login shell's PATH" in result.text and "is $SHELL installed" not in result.text
    run = FakeRun(returncode=127, stderr=b"sh: 1: /usr/bin/zsh: not found\n")
    result = sshforward.print_socket(CONNECTION, run=run)
    assert "is $SHELL installed there?" in result.text and "login shell's PATH" not in result.text
    assert "(exit 127)" in result.text and "/usr/bin/zsh: not found" in result.text
