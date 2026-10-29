# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The ssh forward (collins.sshforward, spec §3.2, §3.24, PR-3.1) with a
fake `subprocess.run` and `Popen`: the two argv, exit 255 reported with
ssh's last line, the local socket unlinked first (and a non-socket
refused), the child's death as the transport's `lost`, and the transport's
find/start/connect_failed round."""

import os
import socket
import subprocess
import threading

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
    assert argv[-3:] == ["collins@localhost", "collins-service", "--print-socket"]
    assert "-o" in argv and "BatchMode=yes" in argv
    assert argv[argv.index("-p") + 1] == "2222"
    assert "ControlMaster=no" in argv and "ControlPath=none" in argv
    assert f"ServerAliveInterval={sshforward.SERVER_ALIVE_INTERVAL_S}" in argv
    # No port: no -p; the destination is the bare host.
    plain = sshforward.print_socket_argv(PLAIN)
    assert "-p" not in plain and plain[-3] == "build.example.net"


def test_print_socket_argv_carries_a_remote_app_id():
    other = connections.Connection(
        id="c" * 32, name="x", kind="ssh", target="h", remote_app_id="com.episode6.Collins.Debug"
    )
    argv = sshforward.print_socket_argv(other)
    assert argv[-4:] == ["collins-service", "--print-socket", "--app-id", "com.episode6.Collins.Debug"]


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
    path = sshforward.local_socket_path("a" * 32)
    assert path == "/run/user/1000/collins/remote/" + "a" * 32 + ".sock"


def test_local_socket_path_falls_back_to_the_state_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", "/home/u/.local/state")
    path = sshforward.local_socket_path("a" * 32)
    assert path == "/home/u/.local/state/collins/remote/" + "a" * 32 + ".sock"


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
