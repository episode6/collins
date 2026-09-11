# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The sandbox mount plan (collins.sandboxplan): aibox's cases — the overlay
home first, masks only inside allowed trees, the agent socket masked in a
shared /tmp, the enclosing repository and a worktree's common git dir, a
private XDG_RUNTIME_DIR — plus Collins' own additions and refusals."""

import json
import os
import stat
import subprocess
import sys

import pytest

from collins import sandboxplan
from collins.sandboxplan import Inputs, PlanRefused, build_plan


@pytest.fixture
def no_shared_tmp(monkeypatch):
    """pytest's tmp_path lives under /tmp, which a real plan shares
    read-write: with it shared, a fake home under it would be reachable
    inside, and the protect-check would refuse every plan. The system's
    shared scratch is /var/tmp alone for these tests."""
    monkeypatch.setattr(sandboxplan, "RW_ABSOLUTE", ("/var/tmp",))


@pytest.fixture
def home(tmp_path, no_shared_tmp):
    """A fake real home with a workspace beside Collins' data dirs."""
    home = tmp_path / "home"
    (home / "work" / "repo").mkdir(parents=True)
    (home / ".local" / "share" / "collins" / "sandbox-home").mkdir(parents=True)
    return home


def _inputs(home, **kw):
    base = dict(
        workspace=str(home / "work" / "repo"),
        home=str(home),
        sandbox_home=str(home / ".local" / "share" / "collins" / "sandbox-home"),
        runtime_dir="/run/user/1000",
        protected=(
            str(home / ".config" / "collins"),
            str(home / ".local" / "state" / "collins"),
            str(home / ".cache" / "collins"),
        ),
    )
    base.update(kw)
    return Inputs(**base)


def _binds(plan, flag):
    """(src, dest) pairs of every *flag* in the plan's bwrap args."""
    args = plan["bwrap_args"]
    return [(args[i + 1], args[i + 2]) for i, a in enumerate(args) if a == flag]


def _index(plan, *needle):
    args = plan["bwrap_args"]
    n = len(needle)
    for i in range(len(args) - n + 1):
        if tuple(args[i : i + n]) == needle:
            return i
    raise AssertionError(f"{needle} not in {args}")


# -- shape --------------------------------------------------------------------


def test_overlay_home_is_the_first_mount(home):
    plan = build_plan(_inputs(home))
    args = plan["bwrap_args"]
    assert args[:3] == ["--unshare-user", "--unshare-pid", "--die-with-parent"]
    assert args[3:6] == ["--bind", plan["inputs"]["sandbox_home"], str(home)]
    # Everything else stacks on it: the system, the home shares, the workspace.
    assert _index(plan, "--ro-bind-try", "/usr", "/usr") > 5
    assert _index(plan, "--bind", str(home / "work" / "repo"), str(home / "work" / "repo")) > 5


def test_the_plan_is_a_json_document_with_the_inputs(home):
    plan = build_plan(_inputs(home, grants=(str(home / "work" / "other"),)))
    assert plan["version"] == sandboxplan.PLAN_VERSION
    assert plan["workspace"] == str(home / "work" / "repo")
    assert plan["setenv"] == {"HOME": str(home)}
    assert set(plan["unsetenv"]) == set(sandboxplan.SCRUB_ENV)
    assert plan["gh_token"] is False
    assert plan["inputs"]["grants"] == [str(home / "work" / "other")]
    assert plan["inputs"]["share_gh"] is False
    assert plan["inputs"]["protect_settings"] is True
    json.dumps(plan)  # serialisable as written


def test_every_path_in_the_plan_is_absolute(home):
    plan = build_plan(_inputs(home))
    args = plan["bwrap_args"]
    for i, arg in enumerate(args):
        if arg in ("--bind", "--bind-try", "--ro-bind", "--ro-bind-try", "--dev-bind-try"):
            assert args[i + 1].startswith("/") and args[i + 2].startswith("/"), args[i : i + 3]
        if arg in ("--tmpfs", "--chdir"):
            assert args[i + 1].startswith("/")


def test_the_runtime_dir_is_a_private_tmpfs_with_the_socket_on_top(home):
    sock = "/run/user/1000/collins/app/mcp.sock"
    plan = build_plan(_inputs(home, socket_file=sock))
    tmpfs = _index(plan, "--tmpfs", "/run/user/1000")
    bind = _index(plan, "--bind", sock, sock)
    assert tmpfs < bind  # the socket lands on the tmpfs, read-write
    # Only the socket, never its directory (the plan files live beside it).
    assert (os.path.dirname(sock), os.path.dirname(sock)) not in _binds(plan, "--bind")
    assert (os.path.dirname(sock), os.path.dirname(sock)) not in _binds(plan, "--bind-try")


def test_workspace_chdir_proc_and_dev_close_the_plan(home):
    plan = build_plan(_inputs(home))
    args = plan["bwrap_args"]
    ws = str(home / "work" / "repo")
    assert args[-2:] == ["--chdir", ws]
    assert _index(plan, "--proc", "/proc") < _index(plan, "--dev", "/dev") < len(args) - 2
    assert ("/dev/kvm", "/dev/kvm") in _binds(plan, "--dev-bind-try")


# -- masks ----------------------------------------------------------------------


def test_masks_only_inside_allowed_trees(home):
    (home / ".ssh").mkdir()
    (home / ".cargo").mkdir()
    (home / ".cargo" / "credentials.toml").write_text("token")
    (home / ".gradle").mkdir()
    (home / ".gradle" / "gradle.properties").write_text("secret")
    (home / ".netrc").write_text("machine x")
    plan = build_plan(_inputs(home))
    args = plan["bwrap_args"]
    # ~/.ssh and ~/.netrc sit in the real home, which the overlay hides
    # entirely: nothing reaches them, so nothing is masked.
    assert ("--tmpfs", str(home / ".ssh")) not in zip(args, args[1:], strict=False)
    assert ("/dev/null", str(home / ".netrc")) not in _binds(plan, "--ro-bind")
    # ~/.cargo and ~/.gradle are shared, so the secrets inside them are
    # carved back out with a deeper mount.
    assert ("/dev/null", str(home / ".cargo" / "credentials.toml")) in _binds(plan, "--ro-bind")
    assert ("/dev/null", str(home / ".gradle" / "gradle.properties")) in _binds(plan, "--ro-bind")
    # …and only ones that exist: bwrap would create the destination.
    assert ("/dev/null", str(home / ".cargo" / "credentials")) not in _binds(plan, "--ro-bind")


def test_a_directory_secret_in_a_shared_tree_is_a_tmpfs(home):
    # A grant of ~/.config would reach ~/.config/gh; it is refused as a
    # grant, but the mask rule is exercised through the share path instead:
    # sharing gh keeps ~/.config/gh unmasked while ~/.config/gcloud, reached
    # the same way, is not.
    (home / ".config" / "gh").mkdir(parents=True)
    (home / ".config" / "gcloud").mkdir(parents=True)
    plan = build_plan(_inputs(home, share_gh=True))
    args = plan["bwrap_args"]
    pairs = list(zip(args, args[1:], strict=False))
    assert ("--tmpfs", str(home / ".config" / "gh")) not in pairs
    assert (str(home / ".config" / "gh"), str(home / ".config" / "gh")) in _binds(
        plan, "--ro-bind-try"
    )
    # Not reached by anything: ~/.config itself is never shared.
    assert ("--tmpfs", str(home / ".config" / "gcloud")) not in pairs
    assert plan["gh_token"] is True
    assert plan["inputs"]["share_gh"] is True


def test_masks_come_after_every_share(home):
    (home / ".m2").mkdir()
    (home / ".m2" / "settings.xml").write_text("<settings/>")
    plan = build_plan(_inputs(home, grants=(str(home / "work" / "other"),)))
    mask = _index(plan, "--ro-bind", "/dev/null", str(home / ".m2" / "settings.xml"))
    assert mask > _index(plan, "--bind-try", str(home / ".m2"), str(home / ".m2"))
    assert mask > _index(plan, "--bind-try", str(home / "work" / "other"), str(home / "work" / "other"))


def test_agent_socket_masked_in_a_shared_tmp(home):
    sock = "/var/tmp/ssh-abc/agent.1"
    plan = build_plan(_inputs(home, ssh_auth_sock=sock))
    assert ("/dev/null", sock) in _binds(plan, "--ro-bind")
    assert "SSH_AUTH_SOCK" in plan["unsetenv"]
    # Under the private runtime dir nothing reaches it, so nothing is masked.
    plan = build_plan(_inputs(home, ssh_auth_sock="/run/user/1000/gcr/ssh"))
    assert ("/dev/null", "/run/user/1000/gcr/ssh") not in _binds(plan, "--ro-bind")


def test_sharing_the_ssh_agent_binds_its_directory_and_keeps_the_variable(home):
    sock = "/run/user/1000/gcr/ssh"
    plan = build_plan(_inputs(home, ssh_auth_sock=sock, share_ssh=True))
    assert ("/run/user/1000/gcr", "/run/user/1000/gcr") in _binds(plan, "--bind-try")
    assert "SSH_AUTH_SOCK" not in plan["unsetenv"]
    assert "SSH_AGENT_PID" not in plan["unsetenv"]
    assert "GPG_AGENT_INFO" in plan["unsetenv"]
    assert plan["inputs"]["share_ssh"] is True
    # The switch with no agent to share does nothing, and says so.
    plan = build_plan(_inputs(home, share_ssh=True))
    assert plan["inputs"]["share_ssh"] is False
    assert "SSH_AUTH_SOCK" in plan["unsetenv"]


def test_a_unix_docker_host_is_masked_and_unset(home):
    plan = build_plan(_inputs(home, docker_host="unix:///var/tmp/docker.sock"))
    assert ("/dev/null", "/var/tmp/docker.sock") in _binds(plan, "--ro-bind")
    assert "DOCKER_HOST" in plan["unsetenv"]
    plan = build_plan(_inputs(home, docker_host="tcp://10.0.0.1:2375"))
    assert "DOCKER_HOST" not in plan["unsetenv"]


# -- the settings files -------------------------------------------------------------


def test_settings_json_is_bound_read_only_over_itself_when_it_exists(home):
    (home / ".claude").mkdir()
    settings = home / ".claude" / "settings.json"
    settings.write_text("{}")
    plan = build_plan(_inputs(home))
    assert (str(settings), str(settings)) in _binds(plan, "--ro-bind")
    # ~/.claude itself stays read-write and shared, as aibox has it.
    assert (str(home / ".claude"), str(home / ".claude")) in _binds(plan, "--bind-try")
    # settings.local.json doesn't exist here, so nothing is emitted for it
    # (bwrap would create the destination).
    local = home / ".claude" / "settings.local.json"
    assert (str(local), str(local)) not in _binds(plan, "--ro-bind")
    local.write_text("{}")
    plan = build_plan(_inputs(home))
    assert (str(local), str(local)) in _binds(plan, "--ro-bind")


def test_the_switch_leaves_settings_json_writable(home):
    (home / ".claude").mkdir()
    settings = home / ".claude" / "settings.json"
    settings.write_text("{}")
    plan = build_plan(_inputs(home, protect_settings=False))
    assert (str(settings), str(settings)) not in _binds(plan, "--ro-bind")
    assert plan["inputs"]["protect_settings"] is False


# -- the enclosing repository ---------------------------------------------------------


def test_enclosing_repo_binds_only_git_and_claude(home):
    repo = home / "work" / "repo"
    (repo / ".git").mkdir()
    (repo / "src").mkdir()
    ws = repo / "src"
    plan = build_plan(_inputs(home, workspace=str(ws)))
    assert (str(ws), str(ws)) in _binds(plan, "--bind")
    assert (str(repo / ".git"), str(repo / ".git")) in _binds(plan, "--bind-try")
    assert (str(repo / ".claude"), str(repo / ".claude")) in _binds(plan, "--bind-try")
    # The parent's working tree stays out.
    assert (str(repo), str(repo)) not in _binds(plan, "--bind")
    assert (str(repo), str(repo)) not in _binds(plan, "--bind-try")


def test_a_linked_worktree_brings_its_common_git_dir(home):
    repo = home / "work" / "repo"
    (repo / ".git" / "worktrees" / "wt").mkdir(parents=True)
    wt = repo / ".claude" / "worktrees" / "wt"
    wt.mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {repo / '.git' / 'worktrees' / 'wt'}\n")
    plan = build_plan(_inputs(home, workspace=str(wt)))
    assert (str(wt), str(wt)) in _binds(plan, "--bind")
    assert (str(wt / ".git"), str(wt / ".git")) in _binds(plan, "--bind-try")
    assert (str(repo / ".git"), str(repo / ".git")) in _binds(plan, "--bind-try")
    assert any("common git dir" in note for note in plan["notes"])


def test_repo_root_and_worktree_common_git(tmp_path):
    assert sandboxplan.repo_root(str(tmp_path)) is None
    (tmp_path / ".git").mkdir()
    (tmp_path / "a" / "b").mkdir(parents=True)
    assert sandboxplan.repo_root(str(tmp_path / "a" / "b")) == str(tmp_path)
    git_file = tmp_path / "wt.git"
    git_file.write_text("not a gitdir line\n")
    assert sandboxplan.worktree_common_git(git_file) is None
    git_file.write_text("gitdir: /srv/repo/.git/worktrees/x\n")
    assert sandboxplan.worktree_common_git(git_file) == "/srv/repo/.git"
    git_file.write_text("gitdir: ../repo/.git/worktrees/x\n")  # relative: resolved beside the file
    assert sandboxplan.worktree_common_git(git_file) == str(tmp_path.parent / "repo" / ".git")
    git_file.write_text("gitdir: /nothing/here\n")  # no .git component
    assert sandboxplan.worktree_common_git(git_file) is None
    assert sandboxplan.worktree_common_git(tmp_path / "missing") is None


# -- Collins' own pieces ----------------------------------------------------------------


def test_collins_pieces_are_bound_read_only_before_the_workspace(home):
    ws = str(home / "work" / "repo")
    plan = build_plan(
        _inputs(
            home,
            claude_dir=str(home / ".local" / "share" / "claude"),
            prefix=str(home / "venv"),
            package_parent=str(home / "src" / "collins"),
            config_dir=str(home / ".local" / "share" / "collins" / "app"),
        )
    )
    for path in (
        str(home / ".local" / "share" / "claude"),
        str(home / "venv"),
        str(home / "src" / "collins"),
        str(home / ".local" / "share" / "collins" / "app"),
    ):
        assert _index(plan, "--ro-bind-try", path, path) < _index(plan, "--bind", ws, ws)


def test_pieces_under_usr_are_not_bound_twice(home):
    plan = build_plan(
        _inputs(
            home,
            claude_dir="/usr/local/bin",
            prefix="/usr",
            package_parent="/usr/lib/python3/dist-packages",
        )
    )
    ro = _binds(plan, "--ro-bind-try")
    assert ("/usr/local/bin", "/usr/local/bin") not in ro
    assert ("/usr/lib/python3/dist-packages", "/usr/lib/python3/dist-packages") not in ro
    assert ro.count(("/usr", "/usr")) == 1


# -- refusals -------------------------------------------------------------------------


def test_refuses_home_and_root_as_a_workspace(home):
    with pytest.raises(PlanRefused):
        build_plan(_inputs(home, workspace=str(home)))
    with pytest.raises(PlanRefused):
        build_plan(_inputs(home, workspace="/"))


def test_refuses_a_workspace_that_nests_with_the_sandbox_home(home):
    inside = home / ".local" / "share" / "collins" / "sandbox-home" / "proj"
    inside.mkdir()
    with pytest.raises(PlanRefused):
        build_plan(_inputs(home, workspace=str(inside)))
    with pytest.raises(PlanRefused):
        build_plan(_inputs(home, workspace=str(home / ".local")))


def test_refuses_a_plan_that_would_carry_collins_state(home):
    # A workspace above state.json carries it: refused outright.
    (home / ".config").mkdir()
    with pytest.raises(PlanRefused, match="must stay outside"):
        build_plan(_inputs(home, workspace=str(home / ".config")))
    # A grant that would carry it is dropped (and noted), and the plan
    # still refuses if anything else reaches it.
    plan = build_plan(_inputs(home, grants=(str(home / ".cache"),)))
    assert (str(home / ".cache"), str(home / ".cache")) not in _binds(plan, "--bind-try")
    assert any("skipped" in note for note in plan["notes"])


def test_refuses_invalid_paths(home):
    with pytest.raises(PlanRefused):
        build_plan(_inputs(home, workspace="relative/path"))
    with pytest.raises(PlanRefused):
        build_plan(_inputs(home, workspace=str(home / "work" / ".." / "repo")))
    with pytest.raises(PlanRefused):
        build_plan(_inputs(home, workspace=str(home / "work") + "/bad\nname"))


def test_valid_path():
    assert sandboxplan.valid_path("/a/b")
    assert sandboxplan.valid_path("/")
    assert not sandboxplan.valid_path("a/b")
    assert not sandboxplan.valid_path("/a/../b")
    assert not sandboxplan.valid_path("/a/./b")
    assert not sandboxplan.valid_path("/a\nb")
    assert not sandboxplan.valid_path("/a\0b")
    assert not sandboxplan.valid_path("")
    assert not sandboxplan.valid_path(None)
    assert not sandboxplan.valid_path("/" + "x" * sandboxplan.MAX_PATH)


# -- grants and the guard -----------------------------------------------------------


def test_grants_are_read_write_binds_after_the_workspace(home):
    other = str(home / "work" / "other")
    ws = str(home / "work" / "repo")
    plan = build_plan(_inputs(home, grants=(other,)))
    assert _index(plan, "--bind-try", other, other) > _index(plan, "--bind", ws, ws)


def test_guard_sensitive_refuses_secrets_their_ancestors_and_home(home):
    h = str(home)
    protected = (str(home / ".config" / "collins"),)
    guard = lambda p: sandboxplan.guard_sensitive(p, h, protected, str(home / "sbx"))  # noqa: E731
    assert guard(str(home / "work" / "other")) == ""
    assert guard("/") != ""
    assert guard(h) != ""
    assert guard(str(home / ".ssh")) != ""
    assert guard(str(home / ".ssh" / "id_ed25519")) != ""  # inside a secret
    assert guard(str(home / ".config")) != ""  # an ancestor of ~/.config/gh
    assert guard(str(home / ".config" / "collins")) != ""
    assert guard(str(home / ".config" / "collins" / "x")) != ""
    assert guard(str(home / "sbx")) != ""
    assert guard(str(home / "sbx" / "dev")) != ""
    assert guard("relative") != ""
    assert guard(str(home / ".configuration")) == ""  # a name prefix is not an ancestor


def test_a_refused_grant_is_never_bound(home):
    plan = build_plan(_inputs(home, grants=(str(home / ".ssh"), str(home / ".config"))))
    binds = _binds(plan, "--bind-try")
    assert (str(home / ".ssh"), str(home / ".ssh")) not in binds
    assert (str(home / ".config"), str(home / ".config")) not in binds
    assert plan["inputs"]["grants"] == []


# -- the host side ------------------------------------------------------------------


def test_seed_home_copies_claude_json_once(tmp_path):
    host = tmp_path / "claude.json"
    host.write_text('{"projects": {}}')
    sbx = tmp_path / "sbx"
    sandboxplan.seed_home(str(sbx), host)
    assert stat.S_IMODE(sbx.stat().st_mode) == 0o700
    seeded = sbx / ".claude.json"
    assert seeded.read_text() == '{"projects": {}}'
    assert stat.S_IMODE(seeded.stat().st_mode) == 0o600
    host.write_text('{"projects": {"changed": true}}')
    sandboxplan.seed_home(str(sbx), host)
    assert seeded.read_text() == '{"projects": {}}'  # diverged: never re-seeded


def test_seed_home_without_a_host_config(tmp_path):
    sbx = tmp_path / "sbx"
    sandboxplan.seed_home(str(sbx), tmp_path / "missing.json")
    assert sbx.is_dir()
    assert not (sbx / ".claude.json").exists()


def test_mirror_trust_copies_only_the_trust_flag(tmp_path):
    host = tmp_path / "claude.json"
    ws = str(tmp_path / "proj" / "sub")
    host.write_text(
        json.dumps(
            {
                "projects": {
                    str(tmp_path / "proj"): {
                        "hasTrustDialogAccepted": True,
                        "allowedTools": ["Bash"],
                    },
                    "/elsewhere": {"hasTrustDialogAccepted": True},
                },
                "mcpServers": {"x": {}},
            }
        )
    )
    sbx = tmp_path / "sbx"
    sbx.mkdir()
    (sbx / ".claude.json").write_text(json.dumps({"projects": {ws: {"foo": 1}}}))
    assert sandboxplan.mirror_trust(str(sbx), ws, host)
    box = json.loads((sbx / ".claude.json").read_text())
    # The ancestor's answer travels (the CLI honours ancestors); its other
    # keys and unrelated projects do not; the box's own entries survive.
    assert box["projects"][str(tmp_path / "proj")] == {"hasTrustDialogAccepted": True}
    assert box["projects"][ws] == {"foo": 1}
    assert "/elsewhere" not in box["projects"]
    assert "mcpServers" not in box


def test_mirror_trust_with_nothing_to_copy(tmp_path):
    host = tmp_path / "claude.json"
    host.write_text(json.dumps({"projects": {"/other": {"hasTrustDialogAccepted": True}}}))
    sbx = tmp_path / "sbx"
    sbx.mkdir()
    (sbx / ".claude.json").write_text("{}")
    assert not sandboxplan.mirror_trust(str(sbx), str(tmp_path / "proj"), host)
    assert (sbx / ".claude.json").read_text() == "{}"
    # An unreadable host file or box copy is a quiet no.
    assert not sandboxplan.mirror_trust(str(sbx), str(tmp_path / "proj"), tmp_path / "nope")
    (sbx / ".claude.json").write_text("not json")
    assert not sandboxplan.mirror_trust(str(sbx), str(tmp_path / "proj"), host)


def test_write_and_release_plan(tmp_path):
    directory = tmp_path / "sandbox"
    path = sandboxplan.write_plan({"version": 1, "bwrap_args": ["--x"]}, str(directory))
    assert os.path.dirname(path) == str(directory)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert json.loads(open(path).read())["bwrap_args"] == ["--x"]
    sandboxplan.release_plan(path)
    assert not os.path.exists(path)
    sandboxplan.release_plan(path)  # gone already: fine
    sandboxplan.release_plan(None)


def test_sandbox_home_dir_honours_the_override(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLINS_SANDBOX_HOME", str(tmp_path / "sbx"))
    assert sandboxplan.sandbox_home_dir() == str(tmp_path / "sbx")
    monkeypatch.delenv("COLLINS_SANDBOX_HOME")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert sandboxplan.sandbox_home_dir() == str(tmp_path / "data" / "collins" / "sandbox-home")


def test_protected_paths_follow_the_xdg_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "k"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "r"))
    paths = sandboxplan.protected_paths("com.example.App")
    assert str(tmp_path / "c" / "collins") in paths
    assert str(tmp_path / "s" / "collins") in paths
    assert str(tmp_path / "k" / "collins") in paths
    assert sandboxplan.plan_dir("com.example.App") in paths
    assert sandboxplan.plan_dir("com.example.App").endswith("/collins/com.example.App/sandbox")


class _State:
    def __init__(self, grants=(), **settings):
        self._grants = list(grants)
        self._settings = settings

    def get_sandbox_grants(self, workspace):
        return list(self._grants)

    def get_setting(self, key):
        return self._settings.get(key, False)


def test_gather_inputs_reads_state_and_the_environment(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    monkeypatch.setenv("COLLINS_SANDBOX_HOME", str(tmp_path / "sbx"))
    monkeypatch.setattr(sandboxplan, "resolved_claude", lambda: ("/opt/claude/bin/claude", "/opt/claude/bin"))
    state = _State(grants=[str(other)], sandbox_share_gh=True, sandbox_settings_editable=True)
    inputs = sandboxplan.gather_inputs(str(ws), "com.example.App", state)
    assert inputs.workspace == str(ws.resolve())
    assert inputs.claude_dir == "/opt/claude/bin"
    assert inputs.grants == (str(other.resolve()),)
    assert inputs.share_gh is True
    assert inputs.share_ssh is False
    assert inputs.protect_settings is False
    assert inputs.ssh_auth_sock == "/tmp/agent.sock"
    assert inputs.runtime_dir == str(tmp_path / "run")
    assert inputs.sandbox_home == str(tmp_path / "sbx")
    # No socket and no config file on this fake instance: neither is bound.
    assert inputs.socket_file is None
    assert inputs.config_dir is None
    assert sandboxplan.plan_dir("com.example.App") in inputs.protected


# -- the probe ---------------------------------------------------------------------


@pytest.fixture
def fresh_probe():
    sandboxplan.reset_probe()
    yield
    sandboxplan.reset_probe()


def _fake_bwrap(tmp_path, body):
    path = tmp_path / "bwrap"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return str(path)


def test_probe_without_bwrap(monkeypatch, tmp_path, fresh_probe):
    monkeypatch.setenv("COLLINS_BWRAP", str(tmp_path / "missing"))
    assert sandboxplan.probe_reason() is None
    assert sandboxplan.available() is False
    assert sandboxplan.probe_reason() == sandboxplan.REASON_NO_BWRAP


def test_probe_with_a_bwrap_that_refuses(monkeypatch, tmp_path, fresh_probe):
    monkeypatch.setenv("COLLINS_BWRAP", _fake_bwrap(tmp_path, "echo 'no userns' >&2; exit 1"))
    assert sandboxplan.available() is False
    assert sandboxplan.probe_reason() == sandboxplan.REASON_NO_USERNS


def test_probe_with_a_bwrap_that_works(monkeypatch, tmp_path, fresh_probe):
    monkeypatch.setenv("COLLINS_BWRAP", _fake_bwrap(tmp_path, "exit 0"))
    assert sandboxplan.available() is True
    assert sandboxplan.probe_reason() == ""
    # Cached: a bwrap that vanishes after the probe doesn't change the answer.
    monkeypatch.setenv("COLLINS_BWRAP", str(tmp_path / "missing"))
    assert sandboxplan.available() is True
    sandboxplan.reset_probe()
    assert sandboxplan.available() is False


def test_probe_async_lands_the_verdict(monkeypatch, tmp_path, fresh_probe):
    import threading

    monkeypatch.setenv("COLLINS_BWRAP", _fake_bwrap(tmp_path, "exit 0"))
    landed = threading.Event()
    seen = []
    sandboxplan.probe_async(lambda reason: (seen.append(reason), landed.set()))
    assert landed.wait(10)
    assert seen == [""]
    assert sandboxplan.probe_reason() == ""


@pytest.mark.skipif(not os.path.exists("/usr/bin/bwrap"), reason="needs the real bubblewrap")
def test_the_real_probe_runs(monkeypatch, fresh_probe):
    monkeypatch.delenv("COLLINS_BWRAP", raising=False)
    reason = sandboxplan.probe()
    # Either verdict is a fact about this machine; the probe must just land one.
    assert reason in ("", sandboxplan.REASON_NO_USERNS)


def test_prepare_launch_writes_a_plan_and_seeds_the_home(
    monkeypatch, tmp_path, fresh_probe, no_shared_tmp
):
    home = tmp_path / "home"
    home.mkdir()
    ws = home / "proj"
    ws.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(sandboxplan.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.delenv("COLLINS_SANDBOX_HOME", raising=False)
    monkeypatch.setenv("COLLINS_BWRAP", _fake_bwrap(tmp_path, "exit 0"))
    host_config = tmp_path / "claude.json"
    host_config.write_text(json.dumps({"projects": {str(ws): {"hasTrustDialogAccepted": True}}}))
    monkeypatch.setattr(sandboxplan.sessions, "CLAUDE_CONFIG", host_config)
    monkeypatch.setattr(sandboxplan, "resolved_claude", lambda: None)
    path = sandboxplan.prepare_launch(str(ws), "com.example.App", _State())
    assert path is not None and os.path.exists(path)
    plan = json.load(open(path))
    assert plan["workspace"] == str(ws.resolve())
    sbx = home / ".local" / "share" / "collins" / "sandbox-home"
    assert (sbx / ".claude.json").exists()
    assert json.load(open(sbx / ".claude.json"))["projects"][str(ws)]["hasTrustDialogAccepted"]
    for rel in sandboxplan.RW_HOME_ALWAYS:
        assert (home / rel).is_dir(), rel
    # A refused workspace yields no plan, quietly.
    assert sandboxplan.prepare_launch(str(home), "com.example.App", _State()) is None
    # And no box at all without bubblewrap.
    sandboxplan.reset_probe()
    monkeypatch.setenv("COLLINS_BWRAP", str(tmp_path / "missing"))
    assert sandboxplan.prepare_launch(str(ws), "com.example.App", _State()) is None


def test_resolved_claude_binds_the_native_install_whole(monkeypatch, tmp_path):
    home = tmp_path / "home"
    versions = home / ".local" / "share" / "claude" / "versions"
    versions.mkdir(parents=True)
    real = versions / "2.1.268"
    real.write_text("#!/bin/sh\n")
    real.chmod(0o755)
    launcher = home / ".local" / "bin" / "claude"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(real)
    monkeypatch.setattr(sandboxplan.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(sandboxplan.shutil, "which", lambda name: str(launcher))
    assert sandboxplan.resolved_claude() == (str(real), str(home / ".local" / "share" / "claude"))
    npm = tmp_path / "npm" / "bin" / "claude"
    npm.parent.mkdir(parents=True)
    npm.write_text("#!/bin/sh\n")
    monkeypatch.setattr(sandboxplan.shutil, "which", lambda name: str(npm))
    assert sandboxplan.resolved_claude() == (str(npm), str(npm.parent))
    monkeypatch.setattr(sandboxplan.shutil, "which", lambda name: None)
    assert sandboxplan.resolved_claude() is None


def test_the_plan_runs_a_real_box_when_the_machine_allows(
    monkeypatch, tmp_path, fresh_probe, no_shared_tmp
):
    """A real bwrap over a real plan: the mounts are consistent enough for
    /bin/true, the workspace is the cwd inside, and the home is the
    sandbox home. Skipped where the probe says no box can be built."""
    monkeypatch.delenv("COLLINS_BWRAP", raising=False)
    if not os.path.exists("/usr/bin/bwrap") or sandboxplan.probe():
        pytest.skip("no bubblewrap box on this machine")
    home = tmp_path / "home"
    ws = home / "proj"
    ws.mkdir(parents=True)
    sbx = tmp_path / "sbx"
    sbx.mkdir()
    plan = build_plan(
        Inputs(workspace=str(ws), home=str(home), sandbox_home=str(sbx), protected=())
    )
    argv = ["/usr/bin/bwrap", *plan["bwrap_args"]]
    for var in plan["unsetenv"]:
        argv += ["--unsetenv", var]
    for k, v in plan["setenv"].items():
        argv += ["--setenv", k, v]
    result = subprocess.run(
        [*argv, "--", "/bin/sh", "-c", 'echo "$PWD $HOME"; touch "$HOME/inside"'],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"{ws} {home}"
    assert (sbx / "inside").exists()  # $HOME inside is the sandbox home
    assert not (home / "inside").exists()


def test_the_module_stays_gtk_free():
    assert "gi.repository.Gtk" not in sys.modules
