# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The ssh forward as Collins uses it (collins.sshforward, spec §3.2, §3.24,
PR-3.1): the connection's words for each failure, where its socket lives, the
child's death as the transport's `lost`, and the transport's
find/start/connect_failed round. The ssh runs themselves are test_sshremote.py."""

import json
import os
import subprocess
import threading
import time

import pytest

from collins import connections, sshforward, sshremote

CONNECTION = connections.Connection(
    id="a" * 32, name="box", kind="ssh", target="collins@localhost:2222"
)


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


def test_exit_text_names_the_target_and_the_detail():
    text = sshforward.exit_text(CONNECTION, 255, "Connection closed by remote host")
    assert "collins@localhost:2222" in text and "Connection closed by remote host" in text
    assert "closed" in sshforward.exit_text(CONNECTION, 0, "")
    assert "(exit 1)" in sshforward.exit_text(CONNECTION, 1, "")
    # A killed child (Popen's -N) says the signal, not "exit -9".
    assert "signal 9" in sshforward.exit_text(CONNECTION, -9, "")


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
        with open(sshremote.pid_path(local), "w") as fh:
            json.dump({"ssh": child.pid, "client": os.getppid(), "socket": local}, fh)
        assert sshremote.end_stale_forward(local) is None
        assert child.poll() is None  # alive
        assert os.path.exists(sshremote.pid_path(local))  # and its record kept
        # The same record naming this process as the client is ours to end.
        with open(sshremote.pid_path(local), "w") as fh:
            json.dump({"ssh": child.pid, "client": os.getpid(), "socket": local}, fh)
        if sshremote.is_our_forward(child.pid, local):
            assert sshremote.end_stale_forward(local) == child.pid
            child.wait(5)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


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


def test_a_connection_with_no_valid_target_is_refused():
    bad = connections.Connection(id="d" * 32, name="x", kind="ssh", target="-oProxyCommand=x h")
    with pytest.raises(ValueError):
        sshforward.print_socket(bad, run=FakeRun())
    with pytest.raises(ValueError):
        sshforward._make_forward(bad, "/r", local_path="/l", on_exit=None)


def test_print_socket_hands_on_the_connections_target_and_app_id():
    other = connections.Connection(
        id="c" * 32, name="x", kind="ssh", target="me@h:2222", remote_app_id="com.episode6.Collins.Debug"
    )
    run = FakeRun(stdout=b"motd\n/run/user/1/api.sock\n")
    result = sshforward.print_socket(other, run=run)
    assert result.ok and result.path == "/run/user/1/api.sock" and result.text == ""
    assert run.calls[0][0] == sshremote.print_socket_argv(
        sshremote.Target(host="h", user="me", port=2222), "com.episode6.Collins.Debug"
    )


def test_make_forward_builds_the_connections_forward():
    forward = sshforward._make_forward(CONNECTION, "/remote/api.sock", local_path="/run/a.sock", on_exit=None)
    assert forward.target == sshremote.Target(host="localhost", user="collins", port=2222)
    assert forward.argv == sshremote.forward_argv(forward.target, "/run/a.sock", "/remote/api.sock")


@pytest.mark.parametrize(
    "failure",
    [
        sshremote.NO_SSH, sshremote.TIMED_OUT, sshremote.OS_ERROR, sshremote.CONNECT_FAILED,
        sshremote.REMOTE_FAILED, sshremote.NOT_ON_PATH, sshremote.NO_SHELL, sshremote.BAD_PATH,
        sshremote.HANDOVER,
    ],
)
def test_every_failure_code_has_its_word(failure):
    plain = sshremote.PrintSocketResult(ok=False, code=1, detail="why", failure=failure)
    assert sshforward._failure_text(plain, "box", 10.0)
