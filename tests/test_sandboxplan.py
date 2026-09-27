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


def test_sharing_the_ssh_agent_binds_the_socket_and_keeps_the_variable(home):
    sock = "/run/user/1000/keyring/ssh"
    plan = build_plan(_inputs(home, ssh_auth_sock=sock, share_ssh=True))
    # The socket file alone, on top of the runtime dir's tmpfs — never its
    # directory, which on a desktop also holds the keyring's control socket.
    assert (sock, sock) in _binds(plan, "--bind-try")
    assert ("/run/user/1000/keyring", "/run/user/1000/keyring") not in _binds(plan, "--bind-try")
    assert _index(plan, "--bind-try", sock, sock) > _index(plan, "--tmpfs", "/run/user/1000")
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


def test_a_symlinked_settings_json_refuses_the_plan(home):
    # A symlink can't be pinned: the link itself stays replaceable from
    # inside, and bwrap can't create the destination through it. Refused
    # rather than built unprotected — unless the switch says writable.
    (home / ".claude").mkdir()
    (home / "dotfiles").mkdir()
    real = home / "dotfiles" / "settings.json"
    real.write_text("{}")
    (home / ".claude" / "settings.json").symlink_to(real)
    with pytest.raises(PlanRefused, match="symlink"):
        build_plan(_inputs(home))
    plan = build_plan(_inputs(home, protect_settings=False))
    assert plan["inputs"]["protect_settings"] is False


def test_plugins_and_a_real_launcher_are_pinned_read_only(home):
    plugins = home / ".claude" / "plugins"
    plugins.mkdir(parents=True)
    launcher = home / ".local" / "bin" / "claude"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n")
    plan = build_plan(_inputs(home, claude_launcher=str(launcher)))
    ro = _binds(plan, "--ro-bind")
    assert (str(plugins), str(plugins)) in ro
    assert (str(launcher), str(launcher)) in ro
    assert _index(plan, "--ro-bind", str(launcher), str(launcher)) > _index(
        plan, "--bind-try", str(home / ".local" / "bin"), str(home / ".local" / "bin")
    )
    # The native installer's launcher is a symlink in the shared ~/.local/bin:
    # nothing can pin it, and the plan says so.
    launcher.unlink()
    launcher.symlink_to("/nonexistent/claude")
    plan = build_plan(_inputs(home, claude_launcher=str(launcher)))
    assert (str(launcher), str(launcher)) not in _binds(plan, "--ro-bind")
    assert any("not pinned" in note for note in plan["notes"])
    # A launcher no shared tree reaches (an npm prefix outside home) is
    # already read-only or absent: nothing to pin.
    plan = build_plan(_inputs(home, claude_launcher="/opt/npm/bin/claude"))
    assert ("/opt/npm/bin/claude", "/opt/npm/bin/claude") not in _binds(plan, "--ro-bind")
    # The switch that unprotects settings.json unpins these too.
    plan = build_plan(_inputs(home, claude_launcher=str(launcher), protect_settings=False))
    assert (str(plugins), str(plugins)) not in _binds(plan, "--ro-bind")


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
            config_file=str(home / ".local" / "share" / "collins" / "app" / "mcp.json"),
        )
    )
    for path in (
        str(home / ".local" / "share" / "claude"),
        str(home / "venv"),
        str(home / "src" / "collins"),
        str(home / ".local" / "share" / "collins" / "app" / "mcp.json"),
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
    # A strict ancestor of home carries the whole real home; refused too.
    with pytest.raises(PlanRefused, match="ancestor of the home"):
        build_plan(_inputs(home, workspace=str(home.parent), sandbox_home="/var/tmp/sbx"))


def test_refuses_a_secret_as_a_workspace(home):
    # The workspace is a read-write bind held to the grant rule: a secret,
    # something inside one, or an ancestor of one is never a workspace.
    (home / ".ssh").mkdir()
    with pytest.raises(PlanRefused, match=".ssh"):
        build_plan(_inputs(home, workspace=str(home / ".ssh")))
    (home / ".config" / "gh").mkdir(parents=True)
    with pytest.raises(PlanRefused, match="reaches"):
        build_plan(_inputs(home, workspace=str(home / ".config")))
    with pytest.raises(PlanRefused, match="reaches"):
        build_plan(_inputs(home, workspace=str(home / ".config" / "gh" / "x")))
    # A secret that is a symlink into a checkout: the target is refused too.
    (home / "dotfiles" / "ssh").mkdir(parents=True)
    (home / ".gnupg").symlink_to(home / "dotfiles" / "ssh")
    with pytest.raises(PlanRefused, match=".gnupg"):
        build_plan(_inputs(home, workspace=str(home / "dotfiles" / "ssh")))


def test_refuses_a_workspace_that_nests_with_the_sandbox_home(home):
    inside = home / ".local" / "share" / "collins" / "sandbox-home" / "proj"
    inside.mkdir()
    with pytest.raises(PlanRefused):
        build_plan(_inputs(home, workspace=str(inside)))
    with pytest.raises(PlanRefused):
        build_plan(_inputs(home, workspace=str(home / ".local")))


def test_refuses_a_plan_that_would_carry_collins_state(home):
    # A workspace above state.json carries it: refused outright.
    (home / ".local" / "state" / "collins").mkdir(parents=True)
    with pytest.raises(PlanRefused, match="reaches"):
        build_plan(_inputs(home, workspace=str(home / ".local" / "state")))
    # And the protect-check catches what no guard names: a state directory
    # kept somewhere a shared tree (~/.claude here) happens to carry.
    with pytest.raises(PlanRefused, match="must stay outside"):
        build_plan(_inputs(home, protected=(str(home / ".claude" / "state"),)))
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


def test_guard_path_sees_through_symlinks(home):
    h = str(home)
    (home / "dotfiles" / "ssh").mkdir(parents=True)
    (home / ".ssh").symlink_to(home / "dotfiles" / "ssh")
    guard = lambda p: sandboxplan.guard_path(p, h, (), str(home / "sbx"))  # noqa: E731
    # The link, its target, and anything inside either.
    assert guard(str(home / ".ssh")) != ""
    assert guard(str(home / "dotfiles" / "ssh")) != ""
    assert guard(str(home / "dotfiles" / "ssh" / "keys")) != ""
    assert guard(str(home / "dotfiles")) != ""  # an ancestor of the target
    assert guard(str(home / "work" / "other")) == ""
    # A grant spelled through a link to a secret's parent.
    (home / "link-to-home").symlink_to(home)
    assert guard(str(home / "link-to-home" / ".config")) != ""
    assert guard(str(home / "link-to-home")) != ""
    # A home reached through a symlinked parent (/home -> /var/home).
    alias = home.parent / "alias"
    alias.symlink_to(home.parent)
    assert sandboxplan.guard_path(str(alias / home.name / ".aws"), h) != ""
    assert sandboxplan.guard_path(str(alias / home.name / "work"), h) == ""


def test_a_symlinked_grant_to_a_secret_is_never_bound(home):
    (home / "dotfiles" / "ssh").mkdir(parents=True)
    (home / ".ssh").symlink_to(home / "dotfiles" / "ssh")
    plan = build_plan(_inputs(home, grants=(str(home / ".ssh"), str(home / "dotfiles" / "ssh"))))
    binds = _binds(plan, "--bind-try")
    assert (str(home / ".ssh"), str(home / ".ssh")) not in binds
    assert (str(home / "dotfiles" / "ssh"), str(home / "dotfiles" / "ssh")) not in binds
    assert plan["inputs"]["grants"] == []


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


def test_seed_home_never_follows_a_planted_symlink(tmp_path):
    # The agent owns the sandbox home: a dangling `.claude.json` link
    # planted from inside must not become a copy of the host config at
    # wherever it points.
    host = tmp_path / "claude.json"
    host.write_text('{"projects": {}}')
    sbx = tmp_path / "sbx"
    sbx.mkdir()
    victim = tmp_path / "victim.json"
    (sbx / ".claude.json").symlink_to(victim)
    sandboxplan.seed_home(str(sbx), host)
    assert not victim.exists()
    assert (sbx / ".claude.json").is_symlink()


def test_mirror_trust_never_writes_through_a_planted_symlink(tmp_path):
    host = tmp_path / "claude.json"
    ws = str(tmp_path / "proj")
    host.write_text(json.dumps({"projects": {ws: {"hasTrustDialogAccepted": True}}}))
    sbx = tmp_path / "sbx"
    sbx.mkdir()
    victim = tmp_path / "victim.json"
    victim.write_text("precious")
    # The destination itself a link to a host file: refused, untouched.
    (sbx / ".claude.json").symlink_to(victim)
    assert not sandboxplan.mirror_trust(str(sbx), ws, host)
    assert victim.read_text() == "precious"
    # A regular destination writes through a fresh private temp file, so a
    # planted `.claude.json.tmp` link is never the write target either.
    (sbx / ".claude.json").unlink()
    (sbx / ".claude.json").write_text("{}")
    (sbx / ".claude.json.tmp").symlink_to(victim)
    assert sandboxplan.mirror_trust(str(sbx), ws, host)
    assert victim.read_text() == "precious"
    assert not (sbx / ".claude.json").is_symlink()
    assert json.loads((sbx / ".claude.json").read_text())["projects"][ws] == {
        "hasTrustDialogAccepted": True
    }
    assert stat.S_IMODE((sbx / ".claude.json").stat().st_mode) == 0o600
    leftovers = [p.name for p in sbx.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [".claude.json.tmp"]  # the plant, and nothing of ours


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


def test_sweep_plans_clears_the_instance_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    directory = sandboxplan.plan_dir("com.example.App")
    assert sandboxplan.sweep_plans("com.example.App") == 0  # no directory yet
    for _ in range(3):
        sandboxplan.write_plan({"version": 1, "bwrap_args": ["--x"]}, directory)
    open(os.path.join(directory, "keep.txt"), "w").write("")
    assert sandboxplan.sweep_plans("com.example.App") == 3
    assert os.listdir(directory) == ["keep.txt"]


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


def test_plan_dir_leaves_the_temp_dir_when_there_is_no_runtime_dir(monkeypatch, tmp_path):
    """With no XDG_RUNTIME_DIR — a Collins started outside a desktop login
    session, and CI — mcptools falls back to the temp directory, which
    every box shares read-write: a plan file there would be inside the
    sandbox and the protect-check would refuse every launch. The state
    directory, which no box carries, holds them instead."""
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    directory = sandboxplan.plan_dir("com.example.App")
    assert directory == str(tmp_path / "s" / "collins" / "sandbox" / "com.example.App")
    # Inside a protected tree, like the runtime-dir spelling: a plan a box
    # could reach is exactly what the protect-check refuses.
    protected = sandboxplan.protected_paths("com.example.App")
    assert str(tmp_path / "s" / "collins") in protected
    assert directory in protected


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
    assert inputs.grants == (str(other),)  # as written; the guard resolves itself
    assert inputs.share_gh is True
    assert inputs.share_ssh is False
    assert inputs.protect_settings is False
    assert inputs.ssh_auth_sock == "/tmp/agent.sock"
    assert inputs.runtime_dir == str(tmp_path / "run")
    assert inputs.sandbox_home == str(tmp_path / "sbx")
    # No socket and no config file on this fake instance: neither is bound.
    assert inputs.socket_file is None
    assert inputs.config_file is None
    assert sandboxplan.plan_dir("com.example.App") in inputs.protected


def test_a_generated_app_id_builds_a_plan(monkeypatch, tmp_path, no_shared_tmp):
    """For an id outside mcptools.STABLE_APP_IDS the mcp.json lives in the
    runtime dir beside the plan directory: only the file is bound, so the
    protect-check passes — every e2e instance runs on such an id."""
    from collins import mcptools

    home = tmp_path / "home"
    ws = home / "proj"
    ws.mkdir(parents=True)
    monkeypatch.setattr(sandboxplan.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("COLLINS_SANDBOX_HOME", str(tmp_path / "sbx"))
    monkeypatch.setattr(sandboxplan, "resolved_claude", lambda: None)
    monkeypatch.setattr(sandboxplan.shutil, "which", lambda name: None)
    app_id = "com.episode6.Collins.e2e-abc123"
    assert app_id not in mcptools.STABLE_APP_IDS
    config = mcptools.config_path(app_id)
    os.makedirs(os.path.dirname(config), exist_ok=True)
    open(config, "w").write("{}")
    inputs = sandboxplan.gather_inputs(str(ws), app_id, _State())
    assert inputs.config_file == config
    plan = build_plan(inputs)
    assert (config, config) in _binds(plan, "--ro-bind-try")
    assert not any(src == os.path.dirname(config) for src, _d in _binds(plan, "--ro-bind-try"))


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


# ---- reading a launched plan back: the chip, a sibling's derivation ----------


class _GrantState:
    """A state with grants keyed the way AppState keys them, saving nothing."""

    def __init__(self, grants=None, **settings):
        self.grants = dict(grants or {})
        self._settings = settings

    def get_sandbox_grants(self, workspace):
        return list(self.grants.get(workspace) or [])

    def set_sandbox_grants(self, workspace, grants):
        if grants:
            self.grants[workspace] = list(grants)
        else:
            self.grants.pop(workspace, None)

    def get_setting(self, key):
        return self._settings.get(key, False)


def _written(home, tmp_path, **kw):
    plan = build_plan(_inputs(home, **kw))
    return plan, sandboxplan.write_plan(plan, str(tmp_path / "plans"))


def test_load_plan_reads_back_what_was_written(home, tmp_path):
    plan, path = _written(home, tmp_path, grants=(str(home / "work" / "lib"),))
    loaded = sandboxplan.load_plan(path)
    assert loaded == json.loads(json.dumps(plan))
    assert loaded["inputs"]["workspace"] == str(home / "work" / "repo")
    assert sandboxplan.load_plan(None) is None
    assert sandboxplan.load_plan(str(tmp_path / "missing.json")) is None


def test_load_plan_refuses_a_plan_that_lost_its_shape(home, tmp_path):
    plan, path = _written(home, tmp_path)
    for broken in (
        {**plan, "inputs": "nope"},
        {**plan, "inputs": {**plan["inputs"], "workspace": "relative"}},
        {**plan, "inputs": {**plan["inputs"], "grants": ["../x"]}},
        {**plan, "inputs": {**plan["inputs"], "share_gh": "yes"}},
        {**plan, "workspace": 7},
        {**plan, "bwrap_args": []},
    ):
        with open(path, "w") as fh:
            json.dump(broken, fh)
        assert sandboxplan.load_plan(path) is None, broken


def test_plan_reaches_holds_a_sibling_to_the_workspace_and_the_grants(home, tmp_path):
    ws = home / "work" / "repo"
    lib = home / "work" / "lib"
    lib.mkdir()
    (ws / "sub").mkdir()
    plan = build_plan(_inputs(home, grants=(str(lib),)))
    assert sandboxplan.plan_reaches(plan, str(ws)) == ""
    assert sandboxplan.plan_reaches(plan, str(ws / "sub")) == ""
    assert sandboxplan.plan_reaches(plan, str(lib)) == ""
    assert sandboxplan.plan_reaches(plan, str(lib / "deeper")) == ""
    outside = sandboxplan.plan_reaches(plan, str(home / ".ssh"))
    assert "outside the sandbox's workspace" in outside and str(ws) in outside
    assert sandboxplan.plan_reaches(plan, str(home / "work")) != ""  # an ancestor is outside
    assert sandboxplan.plan_reaches(plan, str(home / "work" / "repository")) != ""  # a prefix
    assert sandboxplan.plan_reaches(plan, "relative") != ""
    # A symlink into the workspace resolves to somewhere the box reaches.
    link = tmp_path / "link"
    link.symlink_to(ws / "sub")
    assert sandboxplan.plan_reaches(plan, str(link)) == ""


def test_derive_plan_reissues_the_parents_box_for_the_siblings_directory(home):
    ws = home / "work" / "repo"
    (ws / "sub").mkdir()
    lib = home / "work" / "lib"
    lib.mkdir()
    parent = build_plan(_inputs(home, grants=(str(lib),), share_gh=True))
    sibling = sandboxplan.derive_plan(parent, str(ws / "sub"))
    # The same mounts, shares and grants — only the starting directory moved.
    assert _binds(sibling, "--bind") == _binds(parent, "--bind")
    assert _binds(sibling, "--ro-bind-try") == _binds(parent, "--ro-bind-try")
    assert sibling["inputs"] == parent["inputs"]
    assert sibling["gh_token"] is True
    assert sibling["workspace"] == str(ws)
    assert sibling["cwd"] == str(ws / "sub")
    args = sibling["bwrap_args"]
    assert args[args.index("--chdir") + 1] == str(ws / "sub")
    assert args.count("--chdir") == 1
    assert any("derived" in note for note in sibling["notes"])
    # The parent is untouched.
    assert parent["bwrap_args"][parent["bwrap_args"].index("--chdir") + 1] == str(ws)
    # A grant is a fine place to start a sibling; ~/.ssh is not.
    assert sandboxplan.derive_plan(parent, str(lib))["cwd"] == str(lib)
    with pytest.raises(PlanRefused, match="outside the sandbox"):
        sandboxplan.derive_plan(parent, str(home / ".ssh"))
    with pytest.raises(PlanRefused):
        sandboxplan.derive_plan(parent, str(home / "work"))


def _host(monkeypatch, tmp_path, home, state):
    """A SandboxHost over the fake home, its protected paths under it, a
    fake bwrap that says yes, and no host claude on PATH."""
    sandboxplan.reset_probe()
    monkeypatch.setenv("COLLINS_BWRAP", _fake_bwrap(tmp_path, "exit 0"))
    monkeypatch.setattr(sandboxplan.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("COLLINS_SANDBOX_HOME", str(home / ".local" / "share" / "collins" / "sandbox-home"))
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setattr(sandboxplan, "resolved_claude", lambda: None)
    monkeypatch.setattr(sandboxplan.shutil, "which", lambda name: None)
    return sandboxplan.SandboxHost("com.example.App", state)


def test_host_allow_and_revoke_hold_grants_to_the_guard(monkeypatch, tmp_path, home, fresh_probe):
    ws = home / "work" / "repo"
    lib = home / "work" / "lib"
    lib.mkdir()
    (home / ".ssh").mkdir()
    state = _GrantState()
    host = _host(monkeypatch, tmp_path, home, state)
    assert host.grants(str(ws)) == []
    assert host.allow(str(ws), str(lib)) == ""
    assert host.allow(str(ws), str(lib)) == ""  # twice is once
    assert host.grants(str(ws)) == [str(lib)]
    assert state.grants == {str(ws.resolve()): [str(lib)]}
    # Refusals, each with its reason: a secret, an ancestor of one, $HOME,
    # /, Collins' own state, the sandbox home, a file, the workspace itself.
    assert "reaches" in host.allow(str(ws), str(home / ".ssh"))
    assert host.allow(str(ws), str(home)) == "the home directory itself"
    assert host.allow(str(ws), "/") == "the whole filesystem"
    assert "reaches" in host.allow(str(ws), str(home / ".config" / "collins"))
    assert host.allow(str(ws), str(home / ".local" / "share" / "collins" / "sandbox-home")) == (
        "reaches the sandbox home"
    )
    assert host.allow(str(ws), str(lib / "missing")) == "not a directory"
    assert host.allow(str(ws), str(ws / "sub")) == "already inside the workspace"
    assert host.allow(str(ws), "relative/path") == "not an absolute path"
    assert host.grants(str(ws)) == [str(lib)]
    host.revoke(str(ws), str(lib))
    assert host.grants(str(ws)) == []
    assert state.grants == {}
    host.revoke(str(ws), str(lib))  # nothing to revoke: no error


def test_host_plan_stale_compares_the_launched_policy_with_the_state(
    monkeypatch, tmp_path, home, fresh_probe
):
    ws = home / "work" / "repo"
    lib = home / "work" / "lib"
    lib.mkdir()
    state = _GrantState()
    host = _host(monkeypatch, tmp_path, home, state)
    path = host.prepare_launch(str(ws))
    assert path is not None
    assert host.plan_stale(path, str(ws)) is False
    # A grant added since the launch: the running box doesn't have it.
    assert host.allow(str(ws), str(lib)) == ""
    assert host.plan_stale(path, str(ws)) is True
    host.revoke(str(ws), str(lib))
    assert host.plan_stale(path, str(ws)) is False
    # A share flipped since the launch.
    state._settings["sandbox_share_gh"] = True
    assert host.plan_stale(path, str(ws)) is True
    state._settings["sandbox_share_gh"] = False
    state._settings["sandbox_settings_editable"] = True
    assert host.plan_stale(path, str(ws)) is True
    # Nothing to compare against: not stale.
    assert host.plan_stale(None, str(ws)) is False
    assert host.plan_stale(str(tmp_path / "gone.json"), str(ws)) is False
    sandboxplan.release_plan(path)


def test_host_derive_writes_the_siblings_plan_beside_the_parents(
    monkeypatch, tmp_path, home, fresh_probe
):
    ws = home / "work" / "repo"
    (ws / "sub").mkdir()
    host = _host(monkeypatch, tmp_path, home, _GrantState())
    parent_path = host.prepare_launch(str(ws))
    assert parent_path is not None
    sibling_path, reason = host.derive(parent_path, str(ws / "sub"))
    assert reason == "" and sibling_path is not None
    assert os.path.dirname(sibling_path) == os.path.dirname(parent_path)
    assert stat.S_IMODE(os.stat(sibling_path).st_mode) == 0o600
    sibling = sandboxplan.load_plan(sibling_path)
    assert sibling["cwd"] == str(ws / "sub")
    assert sibling["inputs"] == sandboxplan.load_plan(parent_path)["inputs"]
    # Outside the box: no file, the reason instead.
    refused, reason = host.derive(parent_path, str(home / ".ssh"))
    assert refused is None and "outside the sandbox" in reason
    # No parent plan to derive from.
    refused, reason = host.derive(None, str(ws))
    assert refused is None and "can't be read" in reason
    sandboxplan.release_plan(parent_path)
    sandboxplan.release_plan(sibling_path)


def test_the_module_stays_gtk_free():
    assert "gi.repository.Gtk" not in sys.modules
