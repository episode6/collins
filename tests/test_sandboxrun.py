# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The host-side launcher (collins.sandboxrun): reads a plan, feeds bwrap
its arguments over a pipe, refuses rather than runs unsandboxed."""

import json
import os
import subprocess
import sys

import pytest

from collins import sandboxrun

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _plan(tmp_path, **overrides):
    plan = {
        "version": 1,
        "bwrap_args": ["--unshare-user", "--bind", "/tmp/ws", "/tmp/ws", "--chdir", "/tmp/ws"],
        "unsetenv": ["SSH_AUTH_SOCK"],
        "setenv": {"HOME": "/home/u"},
        "gh_token": False,
    }
    plan.update(overrides)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    return str(path)


def test_stdlib_only():
    source = open(sandboxrun.__file__, encoding="utf-8").read()
    assert "from . import" not in source
    assert "from collins" not in source
    assert "import gi" not in source


def test_split_argv():
    assert sandboxrun.split_argv(["/p.json", "--", "claude", "--resume", "x"]) == (
        "/p.json",
        ["claude", "--resume", "x"],
    )
    for bad in ([], ["/p.json"], ["/p.json", "--"], ["/p.json", "claude"], ["--", "claude"]):
        with pytest.raises(ValueError):
            sandboxrun.split_argv(bad)


def test_read_plan_validates_the_shape(tmp_path):
    plan = sandboxrun.read_plan(_plan(tmp_path))
    assert plan["bwrap_args"][0] == "--unshare-user"
    for bad in (
        {"bwrap_args": []},
        {"bwrap_args": "x"},
        {"bwrap_args": ["--x", 3]},
        {"bwrap_args": ["--x\0y"]},
        {"unsetenv": "HOME"},
        {"unsetenv": ["A=B"]},
        {"setenv": ["HOME"]},
        {"setenv": {"HO=ME": "/x"}},
        {"setenv": {"HOME": 1}},
        {"gh_token": "yes"},
    ):
        with pytest.raises(sandboxrun.PlanError):
            sandboxrun.read_plan(_plan(tmp_path, **bad))
    (tmp_path / "list.json").write_text("[1]")
    with pytest.raises(sandboxrun.PlanError):
        sandboxrun.read_plan(str(tmp_path / "list.json"))
    (tmp_path / "junk.json").write_text("{")
    with pytest.raises(sandboxrun.PlanError):
        sandboxrun.read_plan(str(tmp_path / "junk.json"))
    with pytest.raises(sandboxrun.PlanError):
        sandboxrun.read_plan(str(tmp_path / "missing.json"))


def test_bwrap_args_scrub_then_set_then_token(tmp_path):
    plan = sandboxrun.read_plan(_plan(tmp_path))
    args = sandboxrun.bwrap_args(plan, None)
    assert args[:6] == plan["bwrap_args"]
    assert args[6:] == ["--unsetenv", "SSH_AUTH_SOCK", "--setenv", "HOME", "/home/u"]
    args = sandboxrun.bwrap_args(plan, "gho_abc")
    assert args[-3:] == ["--setenv", "GH_TOKEN", "gho_abc"]


def test_encode_args_is_nul_separated():
    assert sandboxrun.encode_args(["a", "b c", "ü"]) == b"a\0b c\0\xc3\xbc\0"


def test_args_fd_carries_the_payload_through_a_pipe_or_a_memfd():
    for payload in (b"small\0", b"x" * 200_000):
        fd = sandboxrun.args_fd(payload)
        try:
            assert os.get_inheritable(fd)
            data = b""
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                data += chunk
            assert data == payload
        finally:
            os.close(fd)


def test_main_execs_bwrap_with_the_args_fd(tmp_path, monkeypatch):
    fake = tmp_path / "bwrap"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("COLLINS_BWRAP", str(fake))
    calls = []

    def fake_exec(path, argv):
        calls.append((path, argv))
        fd = int(argv[2])
        data = b""
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
        calls.append(data)

    rc = sandboxrun.main([_plan(tmp_path), "--", "claude", "--resume", "id"], exec_fn=fake_exec)
    assert rc == 0
    path, argv = calls[0]
    assert path == str(fake)
    assert argv[0] == str(fake) and argv[1] == "--args"
    assert argv[3:] == ["--", "claude", "--resume", "id"]
    assert calls[1] == sandboxrun.encode_args(
        [
            "--unshare-user", "--bind", "/tmp/ws", "/tmp/ws", "--chdir", "/tmp/ws",
            "--unsetenv", "SSH_AUTH_SOCK", "--setenv", "HOME", "/home/u",
        ]
    )


def test_main_refuses_without_bwrap_or_a_plan(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("COLLINS_BWRAP", str(tmp_path / "missing"))
    called = []
    assert sandboxrun.main([_plan(tmp_path), "--", "claude"], exec_fn=lambda *a: called.append(a)) == 2
    assert "bubblewrap" in capsys.readouterr().err
    assert called == []  # never runs the command outside a box
    record = lambda *a: called.append(a)  # noqa: E731
    assert sandboxrun.main([str(tmp_path / "nope.json"), "--", "claude"], exec_fn=record) == 2
    assert sandboxrun.main(["bad"], exec_fn=record) == 2
    assert called == []


def test_gh_token_is_asked_of_the_host_and_handed_in(tmp_path, monkeypatch):
    fake = tmp_path / "bwrap"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("COLLINS_BWRAP", str(fake))
    monkeypatch.setattr(sandboxrun, "host_gh_token", lambda: "gho_secret")
    seen = {}

    def fake_exec(path, argv):
        seen["data"] = os.read(int(argv[2]), 65536)

    sandboxrun.main([_plan(tmp_path, gh_token=True), "--", "claude"], exec_fn=fake_exec)
    assert b"--setenv\0GH_TOKEN\0gho_secret\0" in seen["data"]
    # Logged out on the host: the plan still runs, without the variable.
    monkeypatch.setattr(sandboxrun, "host_gh_token", lambda: None)
    sandboxrun.main([_plan(tmp_path, gh_token=True), "--", "claude"], exec_fn=fake_exec)
    assert b"GH_TOKEN" not in seen["data"]


def test_host_gh_token_reads_gh(tmp_path, monkeypatch):
    gh = tmp_path / "gh"
    gh.write_text("#!/bin/sh\necho gho_fromgh\n")
    gh.chmod(0o755)
    monkeypatch.setattr(sandboxrun.shutil, "which", lambda name: str(gh))
    assert sandboxrun.host_gh_token() == "gho_fromgh"
    gh.write_text("#!/bin/sh\nexit 1\n")
    assert sandboxrun.host_gh_token() is None
    monkeypatch.setattr(sandboxrun.shutil, "which", lambda name: None)
    assert sandboxrun.host_gh_token() is None


def test_the_module_runs_end_to_end_with_a_fake_bwrap(tmp_path):
    """`python3 -m collins.sandboxrun` for real, against a bwrap that dumps
    what it was fed over the fd and then runs the command."""
    fake = tmp_path / "bwrap"
    fake.write_text(
        "#!/bin/sh\n"
        "# $1=--args $2=fd $3=-- rest=command\n"
        "fd=$2; shift 3\n"
        f"tr '\\0' '\\n' <&$fd > {tmp_path}/fed.txt\n"
        'exec "$@"\n'
    )
    fake.chmod(0o755)
    env = dict(os.environ, COLLINS_BWRAP=str(fake), PYTHONPATH=REPO_ROOT)
    result = subprocess.run(
        [sys.executable, "-m", "collins.sandboxrun", _plan(tmp_path), "--", "/bin/echo", "inside"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "inside"
    fed = (tmp_path / "fed.txt").read_text().splitlines()
    assert fed[:2] == ["--unshare-user", "--bind"]
    assert fed[-3:] == ["--setenv", "HOME", "/home/u"]
