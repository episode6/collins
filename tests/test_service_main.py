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
    monkeypatch.setattr(service_main, "login_shell_environment", lambda: {})
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
