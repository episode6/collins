# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""`collins-service`'s entry point (collins.service.main, spec §3.20): the
login-shell capture and its merge, `sd_notify`, `--check`, `--print-socket`
against a live socket, a stale socket unlinked and a live one respected
by `ApiServer.listen`."""

import errno
import io
import os
import socket
import subprocess
import threading
import time

import pytest

from collins.api import server as api_server
from collins.service import main as service_main

# -- the environment ---------------------------------------------------------------


def test_the_login_shell_capture_parses_nul_pairs(tmp_path):
    shell = tmp_path / "sh"
    shell.write_text("#!/bin/sh\nprintf 'FOO=bar\\0PATH=/opt/bin:/usr/bin\\0BROKEN\\0=nokey\\0'\n")
    shell.chmod(0o755)
    env = service_main.login_shell_environment(str(shell))
    assert env == {"FOO": "bar", "PATH": "/opt/bin:/usr/bin"}


def test_the_capture_fails_soft(tmp_path):
    assert service_main.login_shell_environment(str(tmp_path / "missing")) == {}
    slow = tmp_path / "slow"
    slow.write_text("#!/bin/sh\nsleep 5\n")
    slow.chmod(0o755)
    assert service_main.login_shell_environment(str(slow), timeout=0.2) == {}


def _rounds(capture):
    """The parts of a capture a test compares: what it found and which
    rounds counted."""
    return capture.environment, capture.login, capture.interactive


def _fake_shell(tmp_path, name, body):
    shell = tmp_path / name
    shell.write_text("#!/bin/sh\n" + body)
    shell.chmod(0o755)
    return str(shell)


def test_a_round_with_no_path_counts_as_nothing(tmp_path):
    greeting = _fake_shell(tmp_path, "greeter", "printf 'hello there\\n'\n")
    assert _rounds(service_main.capture_login_shell(greeting)) == ({}, False, False)
    # A round that exits 1 with no output (auto-tmux refusing) is the same.
    refused = _fake_shell(tmp_path, "refuser", "exit 1\n")
    assert _rounds(service_main.capture_login_shell(refused)) == ({}, False, False)
    # Only the interactive round answers: it counts, the login round does not.
    mixed = _fake_shell(
        tmp_path, "mixed", 'case "$1" in -lic) printf "PATH=/x/bin\\0A=1\\0";; *) echo hi;; esac\n'
    )
    assert _rounds(service_main.capture_login_shell(mixed)) == ({"PATH": "/x/bin", "A": "1"}, False, True)


def test_a_greeting_before_env_does_not_take_the_first_variable(tmp_path):
    shell = _fake_shell(tmp_path, "greeter2", "printf 'hello\\nPATH=/x\\0A=1\\0'\n")
    assert _rounds(service_main.capture_login_shell(shell)) == ({"PATH": "/x", "A": "1"}, True, True)
    two_lines = _fake_shell(tmp_path, "greeter3", "printf 'hi\\nthere\\nSHELL=/bin/x\\0PATH=/y\\0'\n")
    assert service_main.capture_login_shell(two_lines).environment == {"SHELL": "/bin/x", "PATH": "/y"}


def test_the_interactive_round_overlays_the_login_round_path_included(tmp_path):
    shell = _fake_shell(
        tmp_path,
        "twin",
        'case "$1" in -lic) printf "PATH=/i/bin\\0B=i\\0";; *) printf "PATH=/l/bin\\0A=l\\0B=l\\0";; esac\n',
    )
    capture = service_main.capture_login_shell(shell)
    assert _rounds(capture) == ({"PATH": "/i/bin", "A": "l", "B": "i"}, True, True)


def test_a_slow_round_beside_a_fast_one_returns_within_one_timeout(tmp_path):
    fast_lc = _fake_shell(
        tmp_path, "fast_lc", 'case "$1" in -lic) exec sleep 10;; *) printf "PATH=/l/bin\\0";; esac\n'
    )
    started = time.monotonic()
    capture = service_main.capture_login_shell(fast_lc, timeout=1.0)
    assert _rounds(capture) == ({"PATH": "/l/bin"}, True, False)
    assert time.monotonic() - started < 1.8
    slow_lc = _fake_shell(
        tmp_path, "slow_lc", 'case "$1" in -lc) exec sleep 10;; *) printf "PATH=/i/bin\\0";; esac\n'
    )
    started = time.monotonic()
    capture = service_main.capture_login_shell(slow_lc, timeout=1.0)
    assert _rounds(capture) == ({"PATH": "/i/bin"}, False, True)
    assert time.monotonic() - started < 1.8


def test_two_slow_rounds_share_one_deadline(tmp_path):
    slow = _fake_shell(tmp_path, "slow2", "exec sleep 10\n")
    started = time.monotonic()
    assert _rounds(service_main.capture_login_shell(slow, timeout=1.0)) == ({}, False, False)
    assert time.monotonic() - started < 1.8  # one timeout, not two


def test_a_grandchild_in_a_session_of_its_own_does_not_hold_the_deadline(tmp_path):
    """`setsid cmd &` in an rc leaves the killed group and keeps the pipe
    open; the drain after the kill must not wait for it."""
    shell = _fake_shell(
        tmp_path,
        "daemonizer",
        'case "$1" in -lic) setsid sleep 3 & printf "PATH=/x\\0";; *) printf "PATH=/l/bin\\0";; esac\n',
    )
    started = time.monotonic()
    capture = service_main.capture_login_shell(shell, timeout=0.5)
    assert time.monotonic() - started < 1.3
    assert capture.login and not capture.interactive


def _login_shell_home(tmp_path, *, path_only_interactive: bool, handover: bool):
    """A scratch HOME for a real bash: a bin dir, a .bash_profile that sets
    the login PATH (with or without that dir) and sources .bashrc, and a
    .bashrc guarded the Debian way (returns for a non-interactive shell)
    that then adds the dir (when the PATH is interactive-only) and/or hands
    the shell on with `exec sh` (standing for `exec zsh` / `exec fish` /
    auto-tmux). Returns (home, env, bin_dir)."""
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    login_path = "/usr/bin:/bin" if path_only_interactive else f"{bin_dir}:/usr/bin:/bin"
    (home / ".bash_profile").write_text(f'export PATH="{login_path}"\n[ -f ~/.bashrc ] && . ~/.bashrc\n')
    rc = ["case $- in *i*) ;; *) return;; esac"]
    if path_only_interactive:
        rc.append(f'export PATH="{bin_dir}:$PATH"')
    if handover:
        rc.append("exec sh")
    (home / ".bashrc").write_text("\n".join(rc) + "\n")
    env = {"HOME": str(home), "SHELL": "/usr/bin/bash", "PATH": "/usr/bin:/bin", "LANG": "C"}
    return home, env, bin_dir


needs_bash = pytest.mark.skipif(not os.path.exists("/usr/bin/bash"), reason="needs bash")


@needs_bash
def test_a_path_in_the_login_profile_is_found_by_both_rounds(tmp_path):
    _, env, bin_dir = _login_shell_home(tmp_path, path_only_interactive=False, handover=False)
    capture = service_main.capture_login_shell("/usr/bin/bash", timeout=20.0, env=env)
    assert capture.login and capture.interactive
    assert capture.environment["PATH"].split(":")[0] == str(bin_dir)


@needs_bash
def test_a_path_only_in_the_guarded_rc_is_found_by_the_interactive_round(tmp_path):
    _, env, bin_dir = _login_shell_home(tmp_path, path_only_interactive=True, handover=False)
    login_only = subprocess.run(
        ["/usr/bin/bash", "-lc", "env -0"],
        env=env, stdin=subprocess.DEVNULL, capture_output=True, check=False,
    )
    assert str(bin_dir) not in service_main._parse_env0(login_only.stdout)["PATH"]  # the login round lacks it
    capture = service_main.capture_login_shell("/usr/bin/bash", timeout=20.0, env=env)
    assert capture.login and capture.interactive
    assert capture.environment["PATH"].split(":")[0] == str(bin_dir)  # the overlay holds it


@needs_bash
def test_an_rc_that_hands_the_shell_on_costs_only_the_interactive_round(tmp_path):
    _, env, bin_dir = _login_shell_home(tmp_path, path_only_interactive=False, handover=True)
    capture = service_main.capture_login_shell("/usr/bin/bash", timeout=20.0, env=env)
    assert capture.login and not capture.interactive
    assert capture.environment["PATH"].split(":")[0] == str(bin_dir)  # the login PATH kept


@needs_bash
def test_a_path_only_in_a_rc_that_hands_the_shell_on_is_not_seen(tmp_path):
    _, env, bin_dir = _login_shell_home(tmp_path, path_only_interactive=True, handover=True)
    capture = service_main.capture_login_shell("/usr/bin/bash", timeout=20.0, env=env)
    assert capture.login and not capture.interactive
    assert str(bin_dir) not in capture.environment["PATH"]


def test_the_login_shell_overlays_the_service_but_not_what_is_protected():
    service = {
        "PATH": "/scratch/bin:/usr/bin", "COLLINS_APP_ID": "x", "XDG_CONFIG_HOME": "/s", "HOME": "/h",
        "EDITOR": "nano", "PYTHONPATH": "/wt",
    }
    captured = {
        "PATH": "/home/u/.local/bin:/usr/bin:/opt/bin", "COLLINS_APP_ID": "real", "XDG_CONFIG_HOME": "/u",
        "EDITOR": "vi", "PYTHONPATH": "/theirs", "GOPATH": "/go",
    }
    merged = service_main.merge_environment(service, captured)
    assert merged["COLLINS_APP_ID"] == "x" and merged["XDG_CONFIG_HOME"] == "/s"  # protected
    assert merged["PYTHONPATH"] == "/wt" and merged["HOME"] == "/h"
    assert merged["EDITOR"] == "vi" and merged["GOPATH"] == "/go"  # the shell's win
    # The shell's PATH order, with what the service had in front put back first.
    assert merged["PATH"] == "/scratch/bin:/home/u/.local/bin:/usr/bin:/opt/bin"


# -- sd_notify --------------------------------------------------------------------


def test_sd_notify_sends_a_datagram(tmp_path, monkeypatch):
    path = str(tmp_path / "notify.sock")
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.bind(path)
        sock.settimeout(2)
        monkeypatch.setenv("NOTIFY_SOCKET", path)
        assert service_main.sd_notify("READY=1") is True
        assert sock.recv(64) == b"READY=1"


def test_sd_notify_takes_the_address_the_service_kept(tmp_path, monkeypatch):
    """Nit 7 of PR 604's review: `start` pops NOTIFY_SOCKET right after
    READY=1, so STOPPING=1 read from the environment never went. The
    `Service` keeps the address and hands it to both."""
    import socket

    path = str(tmp_path / "n.sock")
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.bind(path)
        sock.settimeout(2)
        monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
        assert service_main.sd_notify("STOPPING=1", path) is True
        assert sock.recv(64) == b"STOPPING=1"
        assert service_main.sd_notify("STOPPING=1", "") is False
        assert service_main.sd_notify("STOPPING=1") is False  # None: the environment, which has none


def test_sd_notify_without_a_socket_is_false(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert service_main.sd_notify() is False
    monkeypatch.setenv("NOTIFY_SOCKET", "/nonexistent/notify.sock")
    assert service_main.sd_notify() is False


# -- the socket -------------------------------------------------------------------


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    (tmp_path / "run").mkdir()
    return tmp_path / "run"


def _live_socket(path: str):
    """A plain listener standing in for a service: `probe_socket` says
    live while it is accepting."""
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(1)

    def accept():
        try:
            while True:
                conn, _ = listener.accept()
                conn.close()
        except OSError:
            pass

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    return listener


def test_probe_socket_tells_none_stale_and_live(runtime):
    path = api_server.socket_path("com.example.Probe")
    assert api_server.probe_socket(path) == "none"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(path)
    stale.close()  # the file stays, nobody listens
    assert api_server.probe_socket(path) == "stale"
    os.unlink(path)
    listener = _live_socket(path)
    try:
        assert api_server.probe_socket(path) == "live"
    finally:
        listener.close()


def test_print_socket_prints_a_live_services_path(runtime, monkeypatch):
    app_id = "com.example.Print"
    path = api_server.socket_path(app_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    listener = _live_socket(path)
    spawned = []
    monkeypatch.setattr("collins.connection.spawn_service", lambda *a, **k: spawned.append(a))
    out = io.StringIO()
    try:
        assert service_main.print_socket(app_id, out=out) == 0
    finally:
        listener.close()
    assert out.getvalue().strip() == path
    assert spawned == []  # a live service is not started again


def test_print_socket_starts_one_and_waits(runtime, monkeypatch):
    app_id = "com.example.Start"
    path = api_server.socket_path(app_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    holder = {}

    def fake_spawn(app_id_, env=None):
        holder["listener"] = _live_socket(path)
        return 4242

    monkeypatch.setattr("collins.connection.spawn_service", fake_spawn)
    out = io.StringIO()
    try:
        assert service_main.print_socket(app_id, out=out, wait_s=5) == 0
    finally:
        holder["listener"].close()
    assert out.getvalue().strip() == path


def test_print_socket_gives_up_when_nothing_comes_up(runtime, monkeypatch, capsys):
    monkeypatch.setattr("collins.connection.spawn_service", lambda *a, **k: 1)
    assert service_main.print_socket("com.example.Never", wait_s=0.3) == 1


class _Core:
    """What `ApiServer` reads of a core at listen time."""

    state = None
    ptys = None

    def client_connected(self, client):
        pass

    def client_gone(self, client):
        pass


def test_listen_unlinks_a_stale_socket_and_writes_the_proof(runtime):
    app_id = "com.example.Stale"
    path = api_server.socket_path(app_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(path)
    stale.close()
    server = api_server.ApiServer(_Core(), app_id)
    try:
        assert server.listen() == path
        assert api_server.probe_socket(path) == "live"
        assert oct(os.stat(path).st_mode & 0o777) == "0o600"
        proof = api_server.proof_path(app_id)
        assert oct(os.stat(proof).st_mode & 0o777) == "0o600"
        assert len(open(proof, "rb").read()) == 32
    finally:
        server.stop()
    assert not os.path.exists(path) and not os.path.exists(api_server.proof_path(app_id))


def test_listen_respects_a_live_socket(runtime):
    app_id = "com.example.Live"
    path = api_server.socket_path(app_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    listener = _live_socket(path)
    try:
        with pytest.raises(OSError):
            api_server.ApiServer(_Core(), app_id).listen()
        assert api_server.probe_socket(path) == "live"  # untouched
    finally:
        listener.close()


# -- --check ----------------------------------------------------------------------


def test_check_reports_and_exits_one_on_a_missing_requirement(runtime, monkeypatch):
    monkeypatch.setattr(service_main, "lingering", lambda: False)
    monkeypatch.setattr(service_main, "capture_login_shell", lambda: service_main.LoginCapture())
    monkeypatch.setattr(service_main.shutil, "which", lambda name, path=None: None)
    out = io.StringIO()
    assert service_main.check("com.example.Check", out=out) == 1
    text = out.getvalue()
    assert "lingering:    off" in text and "claude:       not on" in text and "(none)" in text
    monkeypatch.setattr(
        service_main.shutil, "which", lambda name, path=None: f"/usr/bin/{name}" if name == "claude" else None
    )
    out = io.StringIO()
    assert service_main.check("com.example.Check", out=out) == 0
    assert "/usr/bin/claude" in out.getvalue()


def test_parse_args_defaults_the_app_id_from_the_environment(monkeypatch):
    monkeypatch.setenv("COLLINS_APP_ID", "com.example.Env")
    assert service_main.parse_args([]).app_id == "com.example.Env"
    assert service_main.parse_args(["--app-id", "x", "--check"]).check is True
    monkeypatch.delenv("COLLINS_APP_ID")
    assert service_main.parse_args([]).app_id == "com.episode6.Collins"


def test_the_descriptor_limit_is_raised_to_the_hard_limit_at_most_the_ceiling(monkeypatch):
    """Nit 1 of the 2026-10-18 review of PR 613: two descriptors per level
    of a tree copy against systemd's 1024 soft limit. The soft limit goes
    up to the hard one (at most the ceiling), never down, and a refused
    `setrlimit` is None, not a crash."""
    import resource

    calls: list[tuple[int, int]] = []

    def getrlimit(which):
        return (1024, 524288)

    def setrlimit(which, limits):
        calls.append(limits)

    monkeypatch.setattr(resource, "getrlimit", getrlimit)
    monkeypatch.setattr(resource, "setrlimit", setrlimit)
    assert service_main.raise_descriptor_limit() == 65536 and calls == [(65536, 524288)]
    calls.clear()
    monkeypatch.setattr(resource, "getrlimit", lambda which: (1024, 4096))
    assert service_main.raise_descriptor_limit() == 4096 and calls == [(4096, 4096)]
    calls.clear()
    monkeypatch.setattr(resource, "getrlimit", lambda which: (100000, 524288))
    assert service_main.raise_descriptor_limit() == 100000 and calls == []  # never lowered
    monkeypatch.setattr(resource, "getrlimit", lambda which: (1024, resource.RLIM_INFINITY))
    assert service_main.raise_descriptor_limit() == 65536 and calls == [(65536, resource.RLIM_INFINITY)]
    calls.clear()

    def refuse(which, limits):
        raise ValueError("not allowed")

    monkeypatch.setattr(resource, "getrlimit", lambda which: (1024, 524288))
    monkeypatch.setattr(resource, "setrlimit", refuse)
    assert service_main.raise_descriptor_limit() is None


def test_the_entry_point_is_registered():
    from pathlib import Path

    import tomllib

    root = Path(__file__).resolve().parent.parent
    scripts = tomllib.loads((root / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["collins-service"] == "collins.service.main:main"
    assert subprocess.run  # the module is importable with its subprocess use


def test_the_socket_path_stays_under_the_kernels_bound(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", "/tmp/s")  # short enough: the state directory it is
    short = api_server.socket_path("com.example.Short")
    assert short == "/tmp/s/collins/com.example.Short/api.sock"
    deep = tmp_path / ("deep" * 20)
    monkeypatch.setenv("XDG_STATE_HOME", str(deep))
    long = api_server.socket_path("com.episode6.Collins.E2E.rcollinssandboxpolicyab12cd34ef")
    assert len(long.encode()) <= api_server.SOCKET_PATH_MAX
    assert not long.startswith(str(deep))
    assert long.endswith("/api.sock")
    # The same id resolves to the same place every time.
    assert api_server.socket_path("com.episode6.Collins.E2E.rcollinssandboxpolicyab12cd34ef") == long


# -- the lock, the probe before any side effect, start to SIGTERM ---------------


def test_the_lock_is_held_once(runtime):
    fd = service_main.take_lock("com.example.Lock")
    try:
        with pytest.raises(OSError) as second:
            service_main.take_lock("com.example.Lock")
        assert second.value.errno == errno.EADDRINUSE
    finally:
        os.close(fd)
    os.close(service_main.take_lock("com.example.Lock"))  # free again once released


def test_a_live_socket_stops_the_service_before_any_side_effect(runtime, tmp_path, monkeypatch):
    app_id = "com.example.Probe2"
    path = api_server.socket_path(app_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    listener = _live_socket(path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    try:
        with pytest.raises(OSError) as raised:
            service_main.Service(app_id)
        assert raised.value.errno == errno.EADDRINUSE
    finally:
        listener.close()
    assert not (tmp_path / "config").exists()  # no state file was written


def test_main_exits_zero_only_for_already_running(runtime, monkeypatch, capsys):
    app_id = "com.example.Exit"
    path = api_server.socket_path(app_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    listener = _live_socket(path)
    try:
        assert service_main.main(["--app-id", app_id]) == 0
    finally:
        listener.close()
    assert path in capsys.readouterr().out

    class Broken(service_main.Service):
        def __init__(self, app_id):
            raise OSError(errno.ENAMETOOLONG, "too long")

    monkeypatch.setattr(service_main, "Service", Broken)
    assert service_main.main(["--app-id", "com.example.Broken"]) == 1


def test_start_sigterm_records_resume_and_removes_the_socket(tmp_path):
    import shutil
    import signal

    from liveservice import LiveService

    live = LiveService(tmp_path, app_id="com.example.Stop")
    try:
        live.start()
        socket_path = live.socket
        proof = api_server.proof_path(live.app_id)
        assert os.path.exists(proof)
        assert live.stop(signal.SIGTERM) == 0
        assert not os.path.exists(socket_path) and not os.path.exists(proof)
        state = live.state()
        assert state.get("resume_on_start", []) == [] and "service_id" in state
    finally:
        live.stop()
        shutil.rmtree(live.runtime, ignore_errors=True)


def test_a_second_service_exits_zero_on_the_lock(tmp_path):
    import shutil
    import subprocess
    import sys

    from liveservice import LiveService

    live = LiveService(tmp_path, app_id="com.example.Second")
    try:
        live.start()
        second = subprocess.run(
            [sys.executable, "-m", "collins.service.main", "--app-id", live.app_id],
            env=live.env, cwd=live.env["PYTHONPATH"], capture_output=True, text=True, timeout=30,
        )
        assert second.returncode == 0 and second.stdout.strip() == live.socket
        assert live.proc.poll() is None  # the first is untouched
    finally:
        live.stop()
        shutil.rmtree(live.runtime, ignore_errors=True)


def test_the_short_runtime_dir_hashes_the_whole_directory_and_checks_its_parent(tmp_path, monkeypatch):
    import stat
    import tempfile

    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path / "t"))
    (tmp_path / "t").mkdir()
    tempfile.tempdir = None  # re-read TMPDIR
    try:
        app_id = "com.episode6.Collins.E2E.rcollinssandboxpolicyab12cd34ef"
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ("a" * 90)))
        one = api_server.socket_path(app_id)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ("b" * 90)))
        two = api_server.socket_path(app_id)
        assert one != two  # one app id, two runtime trees: two stand-ins
        parent = os.path.dirname(os.path.dirname(one))
        assert stat.S_IMODE(os.lstat(parent).st_mode) == 0o700
        # A parent somebody else could have made: refused, never used.
        os.chmod(parent, 0o775)
        with pytest.raises(OSError):
            api_server.socket_path(app_id)
    finally:
        tempfile.tempdir = None


def test_check_names_what_the_shell_rounds_found(runtime, monkeypatch):
    monkeypatch.setattr(service_main, "lingering", lambda: True)
    monkeypatch.setattr(
        service_main.shutil, "which", lambda name, path=None: f"/usr/bin/{name}" if name == "claude" else None
    )
    own = "the service runs on its own environment"
    handed = "login profile read; the interactive rc handed the shell on \u2014 exec zsh, tmux?"
    seen = "so a PATH set only there is not seen: put it in ~/.profile or ~/.zprofile"
    cases = [
        (dict(login=True, interactive=True, started=True), "login profile and interactive rc read"),
        (dict(login=True, started=True), f"{handed} \u2014 {seen}"),
        (
            dict(login=True, started=True, timed_out=True),
            f"{handed}, or did not answer within 5 s \u2014 {seen}",
        ),
        (dict(interactive=True, started=True), "interactive rc read; the login profile did not answer"),
        (dict(started=True, timed_out=True), f"no answer within 5 s: {own}"),
        (dict(timed_out=True), f"no answer within 5 s: {own}"),
        (
            dict(error="No such file or directory"),
            f"/usr/bin/zsh could not be run: No such file or directory; {own}",
        ),
        (dict(started=True), f"no PATH read from either round; {own}"),
    ]
    for flags, wording in cases:
        monkeypatch.setattr(
            service_main,
            "capture_login_shell",
            lambda flags=flags: service_main.LoginCapture(shell="/usr/bin/zsh", **flags),
        )
        out = io.StringIO()
        assert service_main.check("com.example.Check", out=out) == 0  # not counted in `missing`
        lines = out.getvalue().splitlines()
        assert lines[lines.index(next(x for x in lines if x.startswith("socket:"))) + 1] == (
            f"shell:        /usr/bin/zsh ({wording})"
        )


def test_the_capture_says_why_nothing_answered(tmp_path):
    missing = service_main.capture_login_shell(str(tmp_path / "no-such-shell"))
    assert not missing.started and not missing.timed_out
    assert missing.error == "No such file or directory" and missing.shell == str(tmp_path / "no-such-shell")
    greeter = _fake_shell(tmp_path, "greeter4", "printf 'hello\\n'\n")
    quiet = service_main.capture_login_shell(greeter)
    assert quiet.started and not quiet.timed_out and quiet.error == ""
    slow = _fake_shell(tmp_path, "slow3", "exec sleep 10\n")
    late = service_main.capture_login_shell(slow, timeout=0.5)
    assert late.started and late.timed_out
    body = 'case "$1" in -lic) exec sleep 10;; *) printf "PATH=/l\\0";; esac\n'
    fast = _fake_shell(tmp_path, "fast3", body)
    assert service_main.capture_login_shell(fast, timeout=0.5).timed_out
