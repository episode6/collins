# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The ssh runs (collins.sshremote) with a fake `subprocess.run` and `Popen`:
the two argv, the failures as codes, the local socket unlinked first (and a
non-socket refused), the pid file and its sweep, the checks on a recorded pid
with and without /proc (a fake `ps`), a discovery command through the wrapper
under real shells, the remote command under a real bash, and that the module
stays one vendorable file."""

import ast
import errno
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from collins import sshremote

TARGET = sshremote.parse_target("collins@localhost:2222")


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


def test_print_socket_argv_is_batch_mode_with_the_port():
    argv = sshremote.print_socket_argv(TARGET)
    assert argv[0] == "ssh"
    # The remote command is one fixed string: a POSIX `sh -c` outer layer
    # (single quotes only, literal in every shell family) running the box's
    # login shell as `-lic` for the POSIX family and fish, `sh -lc` otherwise
    # (Q3 of PR 623's review, amended by its verification).
    assert argv[-2] == "collins@localhost"
    assert argv[-1] == sshremote.remote_command("collins-service --print-socket")
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
    assert f"ServerAliveInterval={sshremote.SERVER_ALIVE_INTERVAL_S}" in argv
    # No port: no -p; the destination is the bare host.
    plain = sshremote.print_socket_argv(sshremote.parse_target("build.example.net"))
    assert "-p" not in plain and plain[-2] == "build.example.net"


def test_print_socket_argv_carries_a_remote_app_id():
    argv = sshremote.print_socket_argv(sshremote.parse_target("h"), "com.episode6.Collins.Debug")
    assert argv[-1] == sshremote.remote_command(
        "collins-service --print-socket --app-id com.episode6.Collins.Debug"
    )
    assert 'exec collins-service --print-socket --app-id com.episode6.Collins.Debug"' in argv[-1]


def test_forward_argv_is_the_spec_command():
    argv = sshremote.forward_argv(TARGET, "/run/user/1/collins/remote/a.sock", "/home/c/api.sock")
    assert argv[0] == "ssh"
    assert argv[argv.index("-o") + 1] == "ExitOnForwardFailure=yes"
    for option in ("ServerAliveInterval=15", "ServerAliveCountMax=3", "BatchMode=yes"):
        assert option in argv
    assert "-N" in argv
    assert argv[argv.index("-L") + 1] == "/run/user/1/collins/remote/a.sock:/home/c/api.sock"
    assert argv[-1] == "collins@localhost"
    assert argv[argv.index("-p") + 1] == "2222"


def test_forward_argv_refuses_a_bad_path_on_either_side():
    with pytest.raises(ValueError):
        sshremote.forward_argv(TARGET, "/run/user/1/x.sock", "/tmp/${HOME}.sock")
    with pytest.raises(ValueError):
        sshremote.forward_argv(TARGET, "/run/user/1/%u.sock", "/tmp/api.sock")


def test_unlink_socket_removes_a_socket_and_refuses_anything_else(tmp_path):
    path = tmp_path / "s.sock"
    sshremote.unlink_socket(str(path))  # nothing there: fine
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.bind(str(path))
    assert path.exists()
    sshremote.unlink_socket(str(path))
    assert not path.exists()
    (tmp_path / "f").write_text("not a socket")
    with pytest.raises(OSError):
        sshremote.unlink_socket(str(tmp_path / "f"))
    assert (tmp_path / "f").exists()


def test_last_line_is_the_last_non_empty_line_cleaned_and_bounded():
    denied = b"Welcome\n\nPermission denied (publickey).\r\n\n"
    assert sshremote.last_line(denied) == "Permission denied (publickey)."
    assert sshremote.last_line(None) == "" and sshremote.last_line(b"\n\n") == ""
    assert sshremote.last_line("a\x1bb\x07 ") == "ab"
    assert len(sshremote.last_line(b"x" * 1000)) == sshremote.DETAIL_MAX


def test_print_socket_reads_the_path_off_the_last_line():
    run = FakeRun(stdout=b"motd line\n/home/collins/.local/state/collins/com.episode6.Collins/api.sock\n")
    result = sshremote.print_socket(TARGET, run=run)
    assert result.ok and result.path == "/home/collins/.local/state/collins/com.episode6.Collins/api.sock"
    argv, kwargs = run.calls[0]
    assert argv == sshremote.print_socket_argv(TARGET)
    assert kwargs["stdin"] is subprocess.DEVNULL and kwargs["timeout"] == sshremote.PRINT_SOCKET_TIMEOUT_S


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
    result = sshremote.print_socket(TARGET, run=FakeRun(stdout=(line + "\n").encode()))
    assert not result.ok and result.failure == (sshremote.BAD_PATH if line else sshremote.HANDOVER)


@pytest.mark.parametrize(
    "line",
    [
        "/home/jo.doe@corp.example/.local/state/collins/com.episode6.Collins/api.sock",
        "/home/jo.doe@corp.example/a+b,c=d.sock",
    ],
)
def test_print_socket_takes_an_ad_home(line):
    """N4: ssh passes `@ + , =` through -L untouched; an SSSD/AD home holds them."""
    result = sshremote.print_socket(TARGET, run=FakeRun(stdout=(line + "\n").encode()))
    assert result.ok and result.path == line
    assert sshremote.forward_argv(TARGET, "/run/user/1/collins/remote/x/a.sock", line)


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

    forward = sshremote.Forward(
        TARGET, "/remote/api.sock", local_path=str(local),
        on_exit=lambda fwd, code, detail: exits.append((fwd, code, detail)), popen=popen,
    )
    forward.start()
    assert not local.exists()  # unlinked before the child binds it
    proc = procs[0]
    assert proc.argv == sshremote.forward_argv(TARGET, str(local), "/remote/api.sock")
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
    forward = sshremote.Forward(TARGET, "/r", local_path=str(local), popen=FakeProc)
    with pytest.raises(OSError):
        forward.start()
    assert local.read_text() == "a file" and forward.proc is None


def test_forward_stop_is_quiet(tmp_path):
    exits = []
    procs = []

    def popen(argv, **kwargs):
        procs.append(FakeProc(argv, **kwargs))
        return procs[-1]

    forward = sshremote.Forward(
        TARGET, "/r", local_path=str(tmp_path / "a.sock"),
        on_exit=lambda *a: exits.append(a), popen=popen,
    )
    forward.start()
    forward.stop()
    forward._reader.join(5)
    assert procs[0].terminated == 1 and exits == []


def test_forward_start_writes_the_pid_file_and_stop_removes_it(tmp_path):
    local = tmp_path / "a.sock"
    procs = []

    def popen(argv, **kwargs):
        procs.append(FakeProc(argv, **kwargs))
        return procs[-1]

    forward = sshremote.Forward(TARGET, "/remote/api.sock", local_path=str(local), popen=popen)
    forward.start()
    record = sshremote.read_pid_file(sshremote.pid_path(str(local)))
    assert record["ssh"] == 4242 and record["client"] == os.getpid()
    forward.stop()
    forward._reader.join(5)
    assert not os.path.exists(sshremote.pid_path(str(local)))


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
        sshremote.write_pid_file(local, child.pid)
        record = sshremote.read_pid_file(sshremote.pid_path(local))
        assert record == {"ssh": child.pid, "client": os.getpid(), "socket": local}
        assert os.stat(sshremote.pid_path(local)).st_mode & 0o777 == 0o600
        ours = sshremote.is_our_forward(child.pid, local)
        ended = sshremote.end_stale_forward(local)
        if ours:
            assert ended == child.pid
            child.wait(5)
            assert child.poll() is not None
        else:
            assert ended is None and child.poll() is None
        assert not os.path.exists(sshremote.pid_path(local))
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_the_sweep_touches_no_living_clients_forward_and_no_reused_pid(tmp_path):
    directory = str(tmp_path)
    live_local = os.path.join(directory, "live.sock")
    # Another client of this device, alive (this process): left alone.
    sshremote.write_pid_file(live_local, 2)
    # A client that is gone (a pid nobody has), whose "ssh" pid is this
    # process: not an ssh, so nothing is signalled; the file is removed.
    dead_local = os.path.join(directory, "dead.sock")
    path = sshremote.pid_path(dead_local)
    with open(path, "w") as fh:
        json.dump({"ssh": os.getpid(), "client": 2**22 - 1, "socket": dead_local}, fh)
    (tmp_path / "junk.pid").write_text("not json")
    assert sshremote.sweep_orphans(directory) == []
    assert os.path.exists(sshremote.pid_path(live_local))
    assert not os.path.exists(path)


def test_read_pid_file_validates(tmp_path):
    path = str(tmp_path / "x.pid")
    for raw in ('{"ssh": "4", "client": 5, "socket": "/a"}', '{"ssh": 4, "client": 5, "socket": "/a b"}',
                "[]", "x" * 5000, '{"ssh": 0, "client": 5, "socket": "/a"}'):
        with open(path, "w") as fh:
            fh.write(raw)
        assert sshremote.read_pid_file(path) is None
    assert sshremote.read_pid_file(str(tmp_path / "none")) is None


def test_the_sweep_ignores_a_record_naming_a_socket_elsewhere(tmp_path):
    """N5: a record's `socket` must be the file's own `<name>.sock`; the
    sweep neither unlinks a pid file elsewhere nor signals for it."""
    directory = tmp_path / "remote"
    directory.mkdir()
    elsewhere = tmp_path / "out-x"
    elsewhere.mkdir()
    target_pid = sshremote.pid_path(str(elsewhere / "thing.sock"))
    pathlib_write = open(target_pid, "w")
    pathlib_write.write("{}")
    pathlib_write.close()
    with open(directory / "aaaa.pid", "w") as fh:
        json.dump({"ssh": os.getpid(), "client": 2**22 - 1, "socket": str(elsewhere / "thing.sock")}, fh)
    assert sshremote.sweep_orphans(str(directory)) == []
    assert os.path.exists(target_pid)  # untouched
    assert os.path.exists(directory / "aaaa.pid")  # ignored, not removed


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
        if not sshremote.is_our_forward(child.pid, local):
            pytest.skip("this bash could not rename its argv")

        def refused(pid, flags=0):
            raise OSError(errno.ENOSYS, "no pidfd_open here")

        monkeypatch.setattr(os, "pidfd_open", refused, raising=False)
        assert sshremote._signal_forward(child.pid, local, signal.SIGTERM) is True
        child.wait(5)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()

    def gone(pid, flags=0):
        raise OSError(errno.ESRCH, "no such process")

    monkeypatch.setattr(os, "pidfd_open", gone, raising=False)
    assert sshremote._signal_forward(child.pid, local, signal.SIGTERM) is False


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
        ["/usr/bin/bash", "-c", sshremote.remote_command(command)],
        env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=20, check=False,
    )


@pytest.mark.skipif(not os.path.exists("/usr/bin/bash"), reason="needs bash")
def test_a_path_set_only_in_the_interactive_rc_is_found_by_the_lic_round(tmp_path):
    """pipx's ensurepath writes ~/.bashrc, behind Debian's guard: -lc exits
    127, and the wrapper's -lic round finds it."""
    home = _login_shell_home(tmp_path, path_only_interactive=True, handover=False)
    completed = _run_wrapper(home)
    assert completed.returncode == 0, completed.stderr
    assert sshremote.last_line(completed.stdout) == (
        "/home/u/.local/state/collins/com.episode6.Collins/api.sock"
    )


@pytest.mark.skipif(not os.path.exists("/usr/bin/bash"), reason="needs bash")
def test_a_bashrc_that_hands_the_shell_on_does_not_swallow_the_command(tmp_path):
    """V1: `exec zsh` at the end of ~/.bashrc. With the PATH in the login
    profile the -lc round runs the command and never reads the rc."""
    home = _login_shell_home(tmp_path, path_only_interactive=False, handover=True)
    completed = _run_wrapper(home)
    assert completed.returncode == 0, completed.stderr
    assert sshremote.last_line(completed.stdout).endswith("/api.sock")


@pytest.mark.skipif(not os.path.exists("/usr/bin/bash"), reason="needs bash")
def test_both_shapes_at_once_are_reported_as_the_handover(tmp_path):
    """The PATH only in the rc *and* the rc handing the shell on: -lc is
    127, -lic runs the rc, `exec sh` takes over, the command never runs
    (exit 0, nothing printed) — and print_socket names the hand-over."""
    home = _login_shell_home(tmp_path, path_only_interactive=True, handover=True)
    completed = _run_wrapper(home)
    assert completed.returncode == 0 and sshremote.last_line(completed.stdout) == ""
    result = sshremote.print_socket(TARGET, run=FakeRun(returncode=0, stdout=b""))
    assert not result.ok and result.failure == sshremote.HANDOVER
    # A non-empty but bad path is not called a hand-over.
    result = sshremote.print_socket(TARGET, run=FakeRun(returncode=0, stdout=b"relative\n"))
    assert result.failure == sshremote.BAD_PATH


@pytest.mark.parametrize(
    ("run", "failure", "code"),
    [
        (FakeRun(returncode=255, stderr=b"Permission denied (publickey).\n"), sshremote.CONNECT_FAILED, 255),
        (FakeRun(returncode=1, stderr=b"collins-service: no service answered\n"), sshremote.REMOTE_FAILED, 1),
        (FakeRun(returncode=127, stderr=b"collins-service: command not found\n"), sshremote.NOT_ON_PATH, 127),
        (FakeRun(returncode=127, stderr=b"sh: 1: /usr/bin/zsh: not found\n"), sshremote.NO_SHELL, 127),
        (FakeRun(stdout=b"relative/path\n"), sshremote.BAD_PATH, 0),
        (FakeRun(stdout=b""), sshremote.HANDOVER, 0),
        (FakeRun(raise_=FileNotFoundError("ssh")), sshremote.NO_SSH, None),
        (FakeRun(raise_=PermissionError("ssh")), sshremote.OS_ERROR, None),
        (FakeRun(raise_=subprocess.TimeoutExpired(["ssh"], 10, stderr=b"slow\n")), sshremote.TIMED_OUT, None),
    ],
)
def test_every_failure_of_the_run_is_a_code_and_never_a_word(run, failure, code):
    result = sshremote.print_socket(TARGET, run=run)
    assert not result.ok and result.failure == failure and result.code == code and result.path == ""


def test_print_socket_keeps_the_last_line_of_stderr_as_the_detail():
    run = FakeRun(returncode=255, stderr=b"banner\nPermission denied (publickey).\n")
    assert sshremote.print_socket(TARGET, run=run).detail == "Permission denied (publickey)."
    timed_out = FakeRun(raise_=subprocess.TimeoutExpired(["ssh"], 10, stderr=b"slow\n"))
    assert sshremote.print_socket(TARGET, run=timed_out).detail == "slow"


def test_print_socket_argv_refuses_an_app_id_a_shell_could_read():
    for app_id in ("a b", "x;reboot", "$(id)", "a'b"):
        with pytest.raises(ValueError):
            sshremote.print_socket_argv(TARGET, app_id)


@pytest.mark.parametrize(
    ("text", "destination", "port"),
    [
        ("collins@localhost:2222", "collins@localhost", 2222),
        ("build.example.net", "build.example.net", None),
    ],
)
def test_parse_target_reads_the_grammar(text, destination, port):
    target = sshremote.parse_target(text)
    assert target.destination == destination and target.port == port


@pytest.mark.parametrize(
    "text", ["-oProxyCommand=x", "a b", "h:0", "h:99999", "u@", "", None, "h..x", "x" * 400]
)
def test_parse_target_refuses_anything_else(text):
    assert sshremote.parse_target(text) is None


# ---- the checks on a recorded pid, on any POSIX system ---------------------------------------


LOCAL = "/run/user/1/collins/remote/a.sock"
FORWARD_ARGS = f"ssh -o BatchMode=yes -N -L {LOCAL}:/home/c/api.sock me@box"


@pytest.fixture
def no_proc(tmp_path, monkeypatch):
    """A system with no /proc, as a Mac is."""
    monkeypatch.setattr(sshremote, "PROC_DIR", str(tmp_path / "no-proc"))


def _fake_ps(tmp_path, monkeypatch, body: str) -> Path:
    """`sshremote.PS` is a script with *body*; it records its arguments."""
    script = tmp_path / "ps"
    script.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{tmp_path}/ps.argv"\n{body}\n')
    script.chmod(0o755)
    monkeypatch.setattr(sshremote, "PS", str(script))
    return tmp_path / "ps.argv"


def _prints(tmp_path, monkeypatch, text: str) -> None:
    out = tmp_path / "ps.out"
    out.write_text(text)
    _fake_ps(tmp_path, monkeypatch, f'cat "{out}"')


def _kernel_without_pidfd(monkeypatch) -> None:
    monkeypatch.delattr(os, "pidfd_open", raising=False)
    monkeypatch.delattr(signal, "pidfd_send_signal", raising=False)


@pytest.mark.usefixtures("no_proc")
@pytest.mark.parametrize(
    "args", [FORWARD_ARGS, f"/usr/bin/ssh -N -L {LOCAL}:/r h", f"ssh -L {LOCAL}:/r -N h"]
)
def test_without_proc_ps_recognises_the_forward(tmp_path, monkeypatch, args):
    _prints(tmp_path, monkeypatch, args + "\n")
    assert sshremote.is_our_forward(4242, LOCAL)
    assert (tmp_path / "ps.argv").read_text().split() == ["-o", "args=", "-p", "4242"]


@pytest.mark.usefixtures("no_proc")
@pytest.mark.parametrize(
    "output",
    [
        "",
        "\n\n",
        f"vim {FORWARD_ARGS}\n",  # names it, but is something else
        f"/opt/ssh-wrapper -N -L {LOCAL}:/r h\n",
        f"sshd: -N -L {LOCAL}:/r\n",
        f"ssh: {LOCAL}:/r [mux] -N -L\n",
        f"ssh -L {LOCAL}:/r h\n",  # no -N
        f"ssh -N {LOCAL}:/r h\n",  # no -L
        f"ssh -N -L {LOCAL} h\n",  # the socket with no colon
        f"ssh -N -L {LOCAL}.old:/r h\n",  # another socket that begins the same
        f"ssh -N -L /prefix{LOCAL}:/r h\n",  # one that ends the same
        "ssh -N -L /run/user/1/other.sock:/r h\n",
        f"ssh -N -D {LOCAL}:/r -L /other.sock:/r h\n",  # the path is not -L's
        f"ssh -N -L\n{LOCAL}:/r\n",  # two lines: not one process's answer
        f"{FORWARD_ARGS}\n{FORWARD_ARGS}\n",  # a ps that ignored -p
        "[ssh] <defunct>\n",  # a zombie, procps
        "(ssh)\n",  # a zombie, BSD
        "\ufffd\ufffd garbage\n",
        "ssh -N -L " + "x" * sshremote.PID_FILE_MAX + f" -L {LOCAL}:/r h\n",  # past the bound
    ],
)
def test_a_ps_answer_that_is_not_the_forwards_own_is_not_ours(tmp_path, monkeypatch, output):
    _prints(tmp_path, monkeypatch, output)
    assert not sshremote.is_our_forward(4242, LOCAL)


@pytest.mark.usefixtures("no_proc")
def test_ps_is_asked_for_a_width_a_path_fits_in(tmp_path, monkeypatch):
    """Its width is undefined on a pipe, and COLUMNS is POSIX's override."""
    monkeypatch.setenv("COLUMNS", "20")
    seen = tmp_path / "columns"
    _fake_ps(tmp_path, monkeypatch, f'echo "$COLUMNS" > "{seen}"; echo "{FORWARD_ARGS}"')
    assert sshremote.is_our_forward(4242, LOCAL)
    assert seen.read_text().strip() == str(sshremote.PID_FILE_MAX)


@pytest.mark.usefixtures("no_proc")
def test_a_line_cut_inside_the_socket_is_not_ours(tmp_path, monkeypatch):
    _prints(tmp_path, monkeypatch, f"ssh -N -L {LOCAL[:-3]}\n")
    assert not sshremote.is_our_forward(4242, LOCAL)


@pytest.mark.usefixtures("no_proc")
def test_a_ps_that_fails_or_hangs_or_is_missing_is_not_ours(tmp_path, monkeypatch):
    _fake_ps(tmp_path, monkeypatch, f"echo '{FORWARD_ARGS}'; exit 1")
    assert not sshremote.is_our_forward(4242, LOCAL)  # the words do not count on a failure
    _fake_ps(tmp_path, monkeypatch, f"echo '{FORWARD_ARGS}'; exec sleep 30")
    monkeypatch.setattr(sshremote, "PS_TIMEOUT_S", 0.2)
    started = time.monotonic()
    assert not sshremote.is_our_forward(4242, LOCAL)
    assert time.monotonic() - started < 5
    monkeypatch.setattr(sshremote, "PS", str(tmp_path / "no-such-ps"))
    assert not sshremote.is_our_forward(4242, LOCAL)


@pytest.mark.parametrize("pid", [0, -1, -4242])
def test_a_pid_that_names_a_group_is_nothing(tmp_path, monkeypatch, pid):
    """To `kill` these are process groups, and ps would read them as options."""
    argv = _fake_ps(tmp_path, monkeypatch, f"echo '{FORWARD_ARGS}'")
    monkeypatch.setattr(sshremote, "PROC_DIR", str(tmp_path / "no-proc"))
    killed = []
    monkeypatch.setattr(os, "kill", lambda *a: killed.append(a))
    assert not sshremote.pid_alive(pid) and not sshremote.is_our_forward(pid, LOCAL)
    assert killed == [] and not argv.exists()


def test_proc_is_read_first_and_ps_is_not_run_when_it_answers(tmp_path, monkeypatch):
    argv = _fake_ps(tmp_path, monkeypatch, f"echo '{FORWARD_ARGS}'")
    proc = tmp_path / "proc"
    (proc / "77").mkdir(parents=True)
    (proc / "77" / "cmdline").write_bytes(FORWARD_ARGS.replace(" ", "\0").encode() + b"\0")
    (proc / "78").mkdir()
    (proc / "78" / "cmdline").write_bytes(b"")  # a zombie's, on Linux: it exists, and says nothing
    monkeypatch.setattr(sshremote, "PROC_DIR", str(proc))
    assert sshremote.is_our_forward(77, LOCAL)
    assert not sshremote.is_our_forward(78, LOCAL)
    assert not argv.exists()
    assert sshremote.is_our_forward(79, LOCAL)  # no such entry: ps answers
    assert argv.exists()


def test_a_proc_entry_that_says_another_program_is_not_ours(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    (proc / "77").mkdir(parents=True)
    (proc / "77" / "cmdline").write_bytes(f"vim\0-N\0-L\0{LOCAL}:/r\0".encode())
    monkeypatch.setattr(sshremote, "PROC_DIR", str(proc))
    assert not sshremote.is_our_forward(77, LOCAL)


def test_pid_alive_reads_no_proc(monkeypatch):
    """A Mac has none: this process is alive and a reaped child is not,
    with `/proc` out of the question."""
    child = subprocess.Popen(["true"])
    child.wait()

    def no_files(*_a, **_k):
        raise AssertionError("pid_alive opened a file")

    monkeypatch.setattr("builtins.open", no_files)
    assert sshremote.pid_alive(os.getpid())
    assert not sshremote.pid_alive(child.pid)
    assert not sshremote.pid_alive(2**70)  # past what kill takes: nothing, not a crash


def test_pid_alive_counts_another_users_process(monkeypatch):
    def refused(_pid, _sig):
        raise PermissionError(errno.EPERM, "not yours")

    monkeypatch.setattr(os, "kill", refused)
    assert sshremote.pid_alive(4242)


def _sleeper() -> subprocess.Popen:
    """A real process to aim signals at, not an ssh by any reading."""
    return subprocess.Popen(["sleep", "300"], stdin=subprocess.DEVNULL)


@pytest.mark.usefixtures("no_proc")
@pytest.mark.parametrize("output", ["", "sleep 300\n", f"vim {FORWARD_ARGS}\n", "\n"])
def test_a_bad_ps_parse_signals_nothing(tmp_path, monkeypatch, output):
    """The road a Mac takes (no pidfd, no /proc): whatever `ps` says that is
    not the forward's own line leaves the process running."""
    _kernel_without_pidfd(monkeypatch)
    _prints(tmp_path, monkeypatch, output)
    child = _sleeper()
    try:
        assert sshremote._signal_forward(child.pid, LOCAL, signal.SIGTERM) is False
        time.sleep(0.1)
        assert child.poll() is None
    finally:
        child.kill()
        child.wait()


@pytest.mark.usefixtures("no_proc")
def test_a_stale_forward_is_ended_without_proc(tmp_path, monkeypatch):
    """The whole of `end_stale_forward` on a system with neither /proc nor
    pidfd: the client is gone, `ps` says the pid is still the forward, so it
    is signalled, and the pid file goes."""
    _kernel_without_pidfd(monkeypatch)
    local = str(tmp_path / "a.sock")
    _prints(tmp_path, monkeypatch, f"ssh -N -L {local}:/r h\n")
    gone = subprocess.Popen(["true"])
    gone.wait()
    child = _sleeper()
    reaper = threading.Thread(target=child.wait, daemon=True)
    reaper.start()
    try:
        with open(sshremote.pid_path(local), "w") as fh:
            json.dump({"ssh": child.pid, "client": gone.pid, "socket": local}, fh)
        assert sshremote.end_stale_forward(local) == child.pid
        reaper.join(5)
        assert child.returncode == -signal.SIGTERM
        assert not os.path.exists(sshremote.pid_path(local))
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


@pytest.mark.usefixtures("no_proc")
def test_a_reused_pid_is_left_alone_without_proc(tmp_path, monkeypatch):
    _kernel_without_pidfd(monkeypatch)
    local = str(tmp_path / "a.sock")
    _prints(tmp_path, monkeypatch, f"ssh -N -L {tmp_path}/other.sock:/r h\n")
    gone = subprocess.Popen(["true"])
    gone.wait()
    child = _sleeper()
    try:
        with open(sshremote.pid_path(local), "w") as fh:
            json.dump({"ssh": child.pid, "client": gone.pid, "socket": local}, fh)
        assert sshremote.end_stale_forward(local) is None
        time.sleep(0.1)
        assert child.poll() is None
    finally:
        child.kill()
        child.wait()


def _named_ssh(local: str, body: str) -> subprocess.Popen:
    """A real child whose command line reads as the forward's (bash's
    `exec -a` names it ssh)."""
    return subprocess.Popen(
        ["bash", "-c", f'exec -a ssh bash -c "{body}" x -N -L {local}:/remote/api.sock'],
        stdin=subprocess.DEVNULL,
    )


@pytest.mark.skipif(shutil.which("ps") is None or shutil.which("bash") is None, reason="needs ps and bash")
@pytest.mark.usefixtures("no_proc")
def test_the_real_ps_reads_a_real_forward(tmp_path, monkeypatch):
    # A narrow COLUMNS in the environment, and a path past 80 columns: `ps`
    # cuts its line to a width, and a cut path would never be recognised.
    monkeypatch.setenv("COLUMNS", "20")
    directory = tmp_path / ("d" * 60)
    directory.mkdir()
    local = str(directory / "a.sock")
    child = _named_ssh(local, "sleep 300; true")
    try:
        time.sleep(0.3)
        assert sshremote.is_our_forward(child.pid, local)
        assert not sshremote.is_our_forward(child.pid, str(tmp_path / "b.sock"))
        assert not sshremote.is_our_forward(os.getpid(), local)
    finally:
        child.kill()
        child.wait()
    assert not sshremote.is_our_forward(child.pid, local)  # reaped: ps prints nothing


@pytest.mark.skipif(shutil.which("ps") is None or shutil.which("bash") is None, reason="needs ps and bash")
@pytest.mark.parametrize("with_proc", [True, False])
def test_a_zombie_forward_is_there_but_is_not_ours(tmp_path, monkeypatch, with_proc):
    """`end_stale_forward`'s wait ends on a child this process has not
    reaped: signal 0 still finds it, and neither /proc nor ps calls it ssh."""
    if not with_proc:
        monkeypatch.setattr(sshremote, "PROC_DIR", str(tmp_path / "no-proc"))
    local = str(tmp_path / "a.sock")
    child = _named_ssh(local, "exit 0")
    try:
        os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)  # dead, not reaped
        assert sshremote.pid_alive(child.pid)
        assert not sshremote.is_our_forward(child.pid, local)
    finally:
        child.wait()


# ---- a discovery command ------------------------------------------------------------------


def test_print_socket_argv_takes_another_command():
    argv = sshremote.print_socket_argv(TARGET, command="clinic-server --socket-path")
    assert argv[-1] == sshremote.remote_command("clinic-server --socket-path")
    assert '"$SHELL" -lc "exec clinic-server --socket-path"; rc=$?; ' in argv[-1]
    assert "collins-service" not in argv[-1]
    # The rest of the run is what it was: only the command differs.
    assert argv[:-1] == sshremote.print_socket_argv(TARGET)[:-1]
    # The default is the documented command, byte for byte.
    assert sshremote.print_socket_argv(TARGET) == sshremote.print_socket_argv(
        TARGET, command=sshremote.REMOTE_COMMAND
    )


def test_a_remote_app_id_follows_any_command():
    argv = sshremote.print_socket_argv(TARGET, "com.x.Y", "clinic-server")
    assert argv[-1] == sshremote.remote_command("clinic-server --app-id com.x.Y")


@pytest.mark.parametrize(
    "command",
    ["", "   ", "a\0b", "a\nb", "a\rb", "a\x1bb", "a\tb", "a\x7fb", "x" * 513, None, 3, ["a"]],
)
def test_a_command_that_does_not_fit_is_refused(command):
    assert not sshremote.valid_command(command)
    with pytest.raises(ValueError):
        sshremote.print_socket_argv(TARGET, command=command)


@pytest.mark.parametrize("command", ["x", "x" * 512, "/opt/é/x --flag 'a b' \"c\" $HOME"])
def test_a_command_that_fits_is_taken(command):
    assert sshremote.valid_command(command)
    assert sshremote.print_socket_argv(TARGET, command=command)


def test_print_socket_runs_the_command_it_is_given():
    run = FakeRun(stdout=b"/run/clinic/api.sock\n")
    result = sshremote.print_socket(TARGET, run=run, command="clinic-server")
    assert result.ok and result.path == "/run/clinic/api.sock"
    assert run.calls[0][0] == sshremote.print_socket_argv(TARGET, command="clinic-server")


@pytest.mark.parametrize(
    ("command", "program"),
    [
        ("collins-service --print-socket", "collins-service"),
        ("/opt/clinic/bin/clinic-server --x", "clinic-server"),
        ("'/opt/my apps/clinic' --x", "clinic"),
        ('"unbalanced --x', '"unbalanced'),
        ("''", ""),
    ],
)
def test_command_program_is_what_a_shell_would_name(command, program):
    assert sshremote.command_program(command) == program


@pytest.mark.parametrize(
    ("command", "stderr", "failure"),
    [
        ("clinic-server", b"bash: line 1: clinic-server: command not found\n", sshremote.NOT_ON_PATH),
        ("/opt/x/clinic-server", b"sh: 1: exec: /opt/x/clinic-server: not found\n", sshremote.NOT_ON_PATH),
        ("clinic-server", b"sh: 1: /usr/bin/zsh: not found\n", sshremote.NO_SHELL),
        # A word about the default program says nothing about this one's.
        ("clinic-server", b"collins-service: not found\n", sshremote.NO_SHELL),
        ("''", b"sh: 1: exec: : not found\n", sshremote.NO_SHELL),
    ],
)
def test_exit_127_is_read_against_the_commands_own_program(command, stderr, failure):
    run = FakeRun(returncode=127, stderr=stderr)
    assert sshremote.print_socket(TARGET, run=run, command=command).failure == failure


# Every character the wrapper's layers would read for themselves, in the
# positions a person would plausibly write them.
AWKWARD = (
    """--a 'single quoted' "double quoted" back\\\\slash \\$dollar \\`tick\\` it\\'s bang! """
    """'$HOME' "$HOME" $HOME"""
)
AWKWARD_ARGS = [
    "--a", "single quoted", "double quoted", "back\\slash", "$dollar", "`tick`",
    "it's", "bang!", "$HOME", "{home}", "{home}",
]


@pytest.mark.parametrize("command", [AWKWARD, "x", "$(printf a)", "a\\", "!", "'", '"'])
def test_the_wrapper_keeps_its_promises_whatever_the_command_holds(command):
    """The outer layer goes through the user's own shell of any family: only
    its two single quotes, no history mark, no doubled backslash (fish reads
    that inside single quotes)."""
    wrapper = sshremote.remote_command(command)
    assert wrapper.count("'") == 2 and wrapper.startswith("sh -c '") and wrapper.endswith("'")
    assert "!" not in wrapper and "\\\\" not in wrapper and "\n" not in wrapper


@pytest.mark.parametrize("outer", ["bash", "dash", "zsh", "fish", "tcsh", "mksh"])
@pytest.mark.parametrize("login", ["bash", "dash", "zsh"])
def test_a_command_reaches_the_login_shell_as_written(tmp_path, outer, login):
    """The whole road but ssh: *outer* (the login shell, as sshd runs it with
    -c) runs the wrapper, which runs *login* as $SHELL, which reads the command.
    Quotes group, `$HOME` expands there and only there, nothing is lost."""
    outer_path, login_path = shutil.which(outer), shutil.which(login)
    if outer_path is None or login_path is None:
        pytest.skip(f"needs {outer} and {login}")
    home = tmp_path / "home"
    home.mkdir()
    program = tmp_path / "clinic-server"
    program.write_text(
        '#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > "$REPORT"\necho /run/c/api.sock\n'
    )
    program.chmod(0o755)
    report = tmp_path / "report"
    env = {
        "HOME": str(home), "SHELL": login_path, "PATH": "/usr/bin:/bin", "LANG": "C",
        "REPORT": str(report),
    }
    completed = subprocess.run(
        [outer_path, "-c", sshremote.remote_command(f"{program} {AWKWARD}")],
        env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=20, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert sshremote.last_line(completed.stdout) == "/run/c/api.sock"
    expected = [arg.format(home=home) if arg == "{home}" else arg for arg in AWKWARD_ARGS]
    assert report.read_text().splitlines() == expected


# ---- what makes it vendorable --------------------------------------------------------------


def test_it_imports_only_the_standard_library():
    """The module is MIT so that another client can take this one file: it
    imports nothing of Collins, and nothing it would have to install."""
    imported = set()
    for node in ast.walk(ast.parse(Path(sshremote.__file__).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative imports"
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names)


def test_it_runs_as_one_file_copied_out_of_the_tree(tmp_path):
    """Vendored: a copy in another directory, in an isolated interpreter that
    cannot see this checkout, still parses a target and builds both argv."""
    copy = tmp_path / "vendored_sshremote.py"
    copy.write_text(Path(sshremote.__file__).read_text(encoding="utf-8"), encoding="utf-8")
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import vendored_sshremote as s\n"
        "t = s.parse_target('me@host:2222')\n"
        "assert s.print_socket_argv(t)[0] == 'ssh'\n"
        "assert '-N' in s.forward_argv(t, '/run/a.sock', '/run/b.sock')\n"
        "assert 'other --flag' in s.print_socket_argv(t, command='other --flag')[-1]\n"
        "assert s.pid_alive(__import__('os').getpid())\n"
        "assert not s.is_our_forward(__import__('os').getpid(), '/run/a.sock')\n"
        "assert not any(m == 'collins' or m.startswith('collins.') for m in sys.modules)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", code, str(tmp_path)],
        capture_output=True, cwd=tmp_path, timeout=30, check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_it_carries_no_user_facing_translation_and_no_private_reference():
    """The words are the caller's, and a public file cites no private spec
    or review round."""
    text = Path(sshremote.__file__).read_text(encoding="utf-8")
    assert "i18n" not in text and "translate" not in text
    for word in ("§", "PR-", "PR 6", "split-service", "~/specs", "verification", "ruled 20"):
        assert word not in text, word
