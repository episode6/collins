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


BOX = "0123456789abcdef0123456789abcdef"
OTHER_BOX = "fedcba9876543210fedcba9876543210"


def _root(home):
    """The sandbox root under the fake home: where every box lives."""
    return home / ".local" / "share" / "collins" / "sandbox"


@pytest.fixture
def home(tmp_path, no_shared_tmp):
    """A fake real home with a workspace beside Collins' data dirs, and
    one box under the sandbox root."""
    home = tmp_path / "home"
    (home / "work" / "repo").mkdir(parents=True)
    (_root(home) / BOX / "home").mkdir(parents=True)
    (_root(home) / BOX / "grants").mkdir()
    return home


def _inputs(home, box=BOX, **kw):
    base = dict(
        workspace=str(home / "work" / "repo"),
        home=str(home),
        sandbox_home=str(_root(home) / box / "home"),
        box=box,
        sandbox_root=str(_root(home)),
        carrier=str(_root(home) / box / "grants"),
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
        build_plan(
            _inputs(
                home,
                workspace=str(home.parent),
                sandbox_home=f"/var/tmp/sbx/{BOX}/home",
                sandbox_root="/var/tmp/sbx",
                carrier=f"/var/tmp/sbx/{BOX}/grants",
            )
        )


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


def test_refuses_a_workspace_that_nests_with_the_sandbox_root(home):
    # Inside this box's own home, inside another box's, the root itself,
    # and above it: no box is built on, or around, any box's home.
    inside = _root(home) / BOX / "home" / "proj"
    inside.mkdir()
    with pytest.raises(PlanRefused, match="must not nest"):
        build_plan(_inputs(home, workspace=str(inside)))
    other = _root(home) / OTHER_BOX / "home" / "proj"
    other.mkdir(parents=True)
    with pytest.raises(PlanRefused, match="must not nest"):
        build_plan(_inputs(home, workspace=str(other)))
    with pytest.raises(PlanRefused, match="must not nest"):
        build_plan(_inputs(home, workspace=str(_root(home))))
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


def test_sandbox_root_honours_the_override(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLINS_SANDBOX_ROOT", str(tmp_path / "sbx"))
    assert sandboxplan.sandbox_root() == str(tmp_path / "sbx")
    assert sandboxplan.box_dir(BOX) == str(tmp_path / "sbx" / BOX)
    monkeypatch.delenv("COLLINS_SANDBOX_ROOT")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    root = tmp_path / "data" / "collins" / "sandbox"
    assert sandboxplan.sandbox_root() == str(root)
    assert sandboxplan.box_home(BOX) == str(root / BOX / "home")
    assert sandboxplan.box_carrier(BOX) == str(root / BOX / "grants")
    assert sandboxplan.box_anchor(BOX, "mnt") == str(root / BOX / "anchors" / "mnt")


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
    # Every box's home, as one path: nothing may carry the root inside.
    monkeypatch.setenv("COLLINS_SANDBOX_ROOT", str(tmp_path / "boxes"))
    assert str(tmp_path / "boxes") in sandboxplan.protected_paths("com.example.App")


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
    def __init__(self, grants=(), boxes=(), **settings):
        self._grants = list(grants)
        self._boxes = set(boxes)
        self._settings = settings

    def get_sandbox_grants(self, workspace):
        return list(self._grants)

    def get_setting(self, key):
        return self._settings.get(key, False)

    def sandbox_boxes(self):
        return set(self._boxes)


def test_gather_inputs_reads_state_and_the_environment(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    monkeypatch.setenv("COLLINS_SANDBOX_ROOT", str(tmp_path / "sbx"))
    monkeypatch.setattr(sandboxplan, "resolved_claude", lambda: ("/opt/claude/bin/claude", "/opt/claude/bin"))
    monkeypatch.setattr(sandboxplan, "anchor_roots", lambda: ("/media", "/mnt"))
    state = _State(grants=[str(other)], sandbox_share_gh=True, sandbox_settings_editable=True)
    with pytest.raises(ValueError):
        sandboxplan.gather_inputs(str(ws), "com.example.App", state, "not-a-box")
    inputs = sandboxplan.gather_inputs(str(ws), "com.example.App", state, BOX)
    assert inputs.box == BOX
    assert inputs.sandbox_root == str(tmp_path / "sbx")
    assert inputs.carrier == str(tmp_path / "sbx" / BOX / "grants")
    assert inputs.anchors == (
        (str(tmp_path / "sbx" / BOX / "anchors" / "media"), "/media"),
        (str(tmp_path / "sbx" / BOX / "anchors" / "mnt"), "/mnt"),
    )
    assert inputs.workspace == str(ws.resolve())
    assert inputs.claude_dir == "/opt/claude/bin"
    assert inputs.grants == (str(other),)  # as written; the guard resolves itself
    assert inputs.share_gh is True
    assert inputs.share_ssh is False
    assert inputs.protect_settings is False
    assert inputs.ssh_auth_sock == "/tmp/agent.sock"
    assert inputs.runtime_dir == str(tmp_path / "run")
    assert inputs.sandbox_home == str(tmp_path / "sbx" / BOX / "home")
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
    monkeypatch.setenv("COLLINS_SANDBOX_ROOT", str(tmp_path / "sbx"))
    monkeypatch.setattr(sandboxplan, "resolved_claude", lambda: None)
    monkeypatch.setattr(sandboxplan.shutil, "which", lambda name: None)
    app_id = "com.episode6.Collins.e2e-abc123"
    assert app_id not in mcptools.STABLE_APP_IDS
    config = mcptools.config_path(app_id)
    os.makedirs(os.path.dirname(config), exist_ok=True)
    open(config, "w").write("{}")
    inputs = sandboxplan.gather_inputs(str(ws), app_id, _State(), BOX)
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
    monkeypatch.delenv("COLLINS_SANDBOX_ROOT", raising=False)
    monkeypatch.setenv("COLLINS_BWRAP", _fake_bwrap(tmp_path, "exit 0"))
    monkeypatch.setattr(sandboxplan, "anchor_roots", lambda: ("/mnt",))
    host_config = tmp_path / "claude.json"
    host_config.write_text(json.dumps({"projects": {str(ws): {"hasTrustDialogAccepted": True}}}))
    monkeypatch.setattr(sandboxplan.sessions, "CLAUDE_CONFIG", host_config)
    monkeypatch.setattr(sandboxplan, "resolved_claude", lambda: None)
    path = sandboxplan.prepare_launch(str(ws), "com.example.App", _State(), BOX)
    assert path is not None and os.path.exists(path)
    plan = json.load(open(path))
    assert plan["workspace"] == str(ws.resolve())
    assert plan["inputs"]["box"] == BOX
    box = _root(home) / BOX
    sbx = box / "home"
    assert (sbx / ".claude.json").exists()
    assert json.load(open(sbx / ".claude.json"))["projects"][str(ws)]["hasTrustDialogAccepted"]
    # The box's own directories, each the user's alone, and its lease.
    for made in (_root(home), box, sbx, box / "grants", box / "anchors" / "mnt"):
        assert made.is_dir(), made
        assert stat.S_IMODE(made.stat().st_mode) == 0o700, made
    lease = json.load(open(box / "lease"))
    assert lease == {"pid": os.getpid(), "app_id": "com.example.App"}
    for rel in sandboxplan.RW_HOME_ALWAYS:
        assert (home / rel).is_dir(), rel
    # A second launch in another box shares nothing under the root.
    second = sandboxplan.prepare_launch(str(ws), "com.example.App", _State(), OTHER_BOX)
    assert json.load(open(second))["inputs"]["sandbox_home"] == str(_root(home) / OTHER_BOX / "home")
    # A refused workspace yields no plan, quietly; so does a bad box id.
    assert sandboxplan.prepare_launch(str(home), "com.example.App", _State(), BOX) is None
    assert sandboxplan.prepare_launch(str(ws), "com.example.App", _State(), "../escape") is None
    assert sandboxplan.prepare_launch(str(ws), "com.example.App", _State(), "") is None
    # And no box at all without bubblewrap.
    sandboxplan.reset_probe()
    monkeypatch.setenv("COLLINS_BWRAP", str(tmp_path / "missing"))
    assert sandboxplan.prepare_launch(str(ws), "com.example.App", _State(), BOX) is None


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
    """A real bwrap over real plans: the mounts are consistent enough for
    a shell, the workspace is the cwd inside, the home is the box's own —
    a file written to `$HOME` in one box is absent from another's — and
    the carrier and an anchor are there and read-only. Skipped where the
    probe says no box can be built."""
    monkeypatch.delenv("COLLINS_BWRAP", raising=False)
    if not os.path.exists("/usr/bin/bwrap") or sandboxplan.probe():
        pytest.skip("no bubblewrap box on this machine")
    home = tmp_path / "home"
    ws = home / "proj"
    ws.mkdir(parents=True)
    root = tmp_path / "sbx"

    def run(box, script):
        for made in ("home", "grants", "anchors/mnt"):
            (root / box / made).mkdir(parents=True, exist_ok=True)
        plan = build_plan(
            Inputs(
                workspace=str(ws),
                home=str(home),
                sandbox_home=str(root / box / "home"),
                box=box,
                sandbox_root=str(root),
                carrier=str(root / box / "grants"),
                anchors=((str(root / box / "anchors" / "mnt"), "/mnt"),),
                protected=(),
            )
        )
        argv = ["/usr/bin/bwrap", *plan["bwrap_args"]]
        for var in plan["unsetenv"]:
            argv += ["--unsetenv", var]
        for k, v in plan["setenv"].items():
            argv += ["--setenv", k, v]
        result = subprocess.run(
            [*argv, "--", "/bin/sh", "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip().splitlines()

    probe = (
        'echo "$PWD $HOME"; touch "$HOME/inside"; '
        "mkdir /run/collins/grants/x 2>/dev/null && echo carrier-writable; "
        "mkdir /mnt/x 2>/dev/null && echo anchor-writable; "
        "test -d /run/collins/grants && test -d /mnt && echo both-there"
    )
    assert run(BOX, probe) == [f"{ws} {home}", "both-there"]
    assert (root / BOX / "home" / "inside").exists()  # $HOME inside is the box's home
    assert not (home / "inside").exists()
    # Another box, the same workspace: its own home, blind to the first's.
    seen = run(OTHER_BOX, 'test -e "$HOME/inside" && echo leaked; touch "$HOME/other"')
    assert seen == []
    assert (root / OTHER_BOX / "home" / "other").exists()
    assert not (root / BOX / "home" / "other").exists()
    assert not (root / OTHER_BOX / "home" / "inside").exists()


# ---- reading a launched plan back: the chip, a sibling's derivation ----------


class _GrantState:
    """A state with grants keyed the way AppState keys them — by box — the
    defaults of each project, and the boxes its sessions name, saving
    nothing."""

    def __init__(self, grants=None, boxes=(), **settings):
        self.grants = dict(grants or {})
        self.defaults = {}
        self.boxes = set(boxes)
        self._settings = settings

    def sandbox_boxes(self):
        return set(self.boxes)

    def get_sandbox_grants(self, box):
        return list(self.grants.get(box) or [])

    def set_sandbox_grants(self, box, grants):
        if grants:
            self.grants[box] = list(grants)
        else:
            self.grants.pop(box, None)

    def sandbox_grant_boxes(self):
        return set(self.grants)

    def get_sandbox_project_grants(self, key):
        return list(self.defaults.get(key) or [])

    def set_sandbox_project_grants(self, key, grants):
        if grants:
            self.defaults[key] = list(grants)
        else:
            self.defaults.pop(key, None)

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
    plan, path = _written(home, tmp_path, anchors=((str(_root(home) / BOX / "anchors" / "mnt"), "/mnt"),))
    assert sandboxplan.load_plan(path) is not None
    inputs = plan["inputs"]
    without_box = {k: v for k, v in inputs.items() if k != "box"}
    without_anchors = {k: v for k, v in inputs.items() if k != "anchors"}
    for broken in (
        {**plan, "inputs": "nope"},
        {**plan, "inputs": {**inputs, "workspace": "relative"}},
        {**plan, "inputs": {**inputs, "grants": ["../x"]}},
        {**plan, "inputs": {**inputs, "share_gh": "yes"}},
        {**plan, "workspace": 7},
        {**plan, "bwrap_args": []},
        # A plan from before the boxes, and one that lost a piece of its box.
        {**plan, "version": 1},
        {**plan, "version": 1, "inputs": without_box},
        {**plan, "inputs": without_box},
        {**plan, "inputs": {**inputs, "box": BOX.upper()}},
        {**plan, "inputs": {**inputs, "carrier": None}},
        {**plan, "inputs": {**inputs, "carrier": "relative"}},
        {**plan, "inputs": without_anchors},
        {**plan, "inputs": {**inputs, "anchors": [["/only-one"]]}},
        {**plan, "inputs": {**inputs, "anchors": [["/a", "../b"]]}},
        {**plan, "inputs": {**inputs, "anchors": "nope"}},
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
    anchors = (
        (str(_root(home) / BOX / "anchors" / "mnt"), "/mnt"),
        (str(_root(home) / BOX / "anchors" / "srv"), "/srv"),
    )
    parent = build_plan(_inputs(home, grants=(str(lib),), share_gh=True, anchors=anchors))
    sibling = sandboxplan.derive_plan(parent, str(ws / "sub"), OTHER_BOX)
    # The same workspace, shares and grants, around a box of its own: the
    # home, the carrier and every anchor are the sibling's…
    own = _root(home) / OTHER_BOX
    assert sibling["inputs"]["box"] == OTHER_BOX
    assert sibling["inputs"]["sandbox_home"] == str(own / "home")
    assert sibling["inputs"]["carrier"] == str(own / "grants")
    assert sibling["inputs"]["anchors"] == [
        [str(own / "anchors" / "mnt"), "/mnt"],
        [str(own / "anchors" / "srv"), "/srv"],
    ]
    binds = _binds(sibling, "--bind")
    assert binds[0] == (str(own / "home"), str(home))
    assert (str(own / "grants"), sandboxplan.CARRIER_DEST) in binds
    assert (str(own / "anchors" / "mnt"), "/mnt") in binds
    assert (str(own / "anchors" / "srv"), "/srv") in binds
    # …and the parent's box is named nowhere.
    assert BOX not in json.dumps(sibling)
    # Everything else is the parent's, mount for mount.
    swap = lambda pairs: [(s.replace(BOX, OTHER_BOX), d) for s, d in pairs]  # noqa: E731
    assert binds == swap(_binds(parent, "--bind"))
    assert _binds(sibling, "--bind-try") == _binds(parent, "--bind-try")
    assert _binds(sibling, "--ro-bind-try") == _binds(parent, "--ro-bind-try")
    for key in ("workspace", "grants", "share_gh", "share_ssh", "protect_settings"):
        assert sibling["inputs"][key] == parent["inputs"][key], key
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
    assert sandboxplan.derive_plan(parent, str(lib), OTHER_BOX)["cwd"] == str(lib)
    with pytest.raises(PlanRefused, match="outside the sandbox"):
        sandboxplan.derive_plan(parent, str(home / ".ssh"), OTHER_BOX)
    with pytest.raises(PlanRefused):
        sandboxplan.derive_plan(parent, str(home / "work"), OTHER_BOX)
    # Its own box, by a real id: never the parent's, never a path.
    with pytest.raises(PlanRefused):
        sandboxplan.derive_plan(parent, str(ws / "sub"), BOX)
    with pytest.raises(PlanRefused, match="box id"):
        sandboxplan.derive_plan(parent, str(ws / "sub"), "../../etc")


def _host(monkeypatch, tmp_path, home, state, **kw):
    """A SandboxHost over the fake home, its protected paths under it, a
    fake bwrap that says yes, and no host claude on PATH."""
    sandboxplan.reset_probe()
    monkeypatch.setenv("COLLINS_BWRAP", _fake_bwrap(tmp_path, "exit 0"))
    monkeypatch.setattr(sandboxplan.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("COLLINS_SANDBOX_ROOT", str(_root(home)))
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setattr(sandboxplan, "resolved_claude", lambda: None)
    monkeypatch.setattr(sandboxplan.shutil, "which", lambda name: None)
    monkeypatch.setattr(sandboxplan, "anchor_roots", lambda: ("/mnt", "/srv"))
    return sandboxplan.SandboxHost("com.example.App", state, **kw)


def test_host_allow_and_revoke_hold_grants_to_the_guard(monkeypatch, tmp_path, home, fresh_probe):
    ws = home / "work" / "repo"
    lib = home / "work" / "lib"
    lib.mkdir()
    (home / ".ssh").mkdir()
    state = _GrantState()
    host = _host(monkeypatch, tmp_path, home, state)
    assert host.grants(BOX) == []
    assert host.allow(BOX, str(ws), str(lib)) == ""
    assert host.allow(BOX, str(ws), str(lib)) == ""  # twice is once
    assert host.grants(BOX) == [str(lib)]
    assert state.grants == {BOX: [str(lib)]}
    # Refusals, each with its reason: a secret, an ancestor of one, $HOME,
    # /, Collins' own state, the sandbox homes, a file, the workspace itself.
    assert "reaches" in host.allow(BOX, str(ws), str(home / ".ssh"))
    assert host.allow(BOX, str(ws), str(home)) == "the home directory itself"
    assert host.allow(BOX, str(ws), "/") == "the whole filesystem"
    assert "reaches" in host.allow(BOX, str(ws), str(home / ".config" / "collins"))
    # The root, another box's home, something inside one, an ancestor of
    # the root: no box is granted another's home.
    other_home = _root(home) / OTHER_BOX / "home"
    (other_home / "dev").mkdir(parents=True)
    for refused in (
        _root(home),
        other_home,
        other_home / "dev",
        _root(home) / BOX / "home",
        _root(home).parent,
    ):
        assert host.allow(BOX, str(ws), str(refused)) == "reaches the sandbox homes", refused
    assert host.allow(BOX, str(ws), str(lib / "missing")) == "not a directory"
    assert host.allow(BOX, str(ws), str(ws / "sub")) == "already inside the workspace"
    assert host.allow(BOX, str(ws), "relative/path") == "not an absolute path"
    assert host.grants(BOX) == [str(lib)]
    host.revoke(BOX, str(lib))
    assert host.grants(BOX) == []
    assert state.grants == {}
    host.revoke(BOX, str(lib))  # nothing to revoke: no error


def test_host_plan_stale_compares_the_launched_policy_with_the_state(
    monkeypatch, tmp_path, home, fresh_probe
):
    ws = home / "work" / "repo"
    lib = home / "work" / "lib"
    lib.mkdir()
    state = _GrantState()
    host = _host(monkeypatch, tmp_path, home, state)
    path = host.prepare_launch(str(ws), BOX)
    assert path is not None
    assert host.plan_stale(path, str(ws)) is False
    # A grant added since the launch: the running box doesn't have it.
    assert host.allow(BOX, str(ws), str(lib)) == ""
    assert host.plan_stale(path, str(ws)) is True
    host.revoke(BOX, str(lib))
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
    host_config = tmp_path / "claude.json"
    host_config.write_text(
        json.dumps({"projects": {str(ws / "sub"): {"hasTrustDialogAccepted": True}}})
    )
    monkeypatch.setattr(sandboxplan.sessions, "CLAUDE_CONFIG", host_config)
    parent_path = host.prepare_launch(str(ws), BOX)
    assert parent_path is not None
    before = set(os.listdir(_root(home)))
    sibling_path, box, reason = host.derive(parent_path, str(ws / "sub"))
    assert reason == "" and sibling_path is not None
    assert sandboxplan.valid_box_id(box) and box != BOX
    assert os.path.dirname(sibling_path) == os.path.dirname(parent_path)
    assert stat.S_IMODE(os.stat(sibling_path).st_mode) == 0o600
    sibling = sandboxplan.load_plan(sibling_path)
    parent = sandboxplan.load_plan(parent_path)
    assert sibling["cwd"] == str(ws / "sub")
    assert sibling["inputs"]["box"] == box
    assert sibling["inputs"]["grants"] == parent["inputs"]["grants"]
    assert BOX not in json.dumps(sibling)
    # The box is there, made and seeded like any other, and held.
    own = _root(home) / box
    assert set(os.listdir(_root(home))) == before | {box}
    for made in (own / "home", own / "grants", own / "anchors" / "mnt", own / "anchors" / "srv"):
        assert made.is_dir(), made
    seeded = json.load(open(own / "home" / ".claude.json"))
    assert seeded["projects"][str(ws / "sub")]["hasTrustDialogAccepted"] is True
    assert host.held(box)
    assert json.load(open(own / "lease"))["pid"] == os.getpid()
    assert host.discard_box(box) is False  # held: not while its tab is coming
    # Outside the box: no file, no box, the reason instead.
    refused, no_box, reason = host.derive(parent_path, str(home / ".ssh"))
    assert refused is None and no_box == "" and "outside the sandbox" in reason
    # No parent plan to derive from.
    refused, no_box, reason = host.derive(None, str(ws))
    assert refused is None and no_box == "" and "can't be read" in reason
    assert set(os.listdir(_root(home))) == before | {box}
    sandboxplan.release_plan(parent_path)
    sandboxplan.release_plan(sibling_path)
    # Let go, and named by no session, it goes.
    host.release(box)
    assert not (own / "lease").exists()
    assert host.discard_box(box) is True
    assert not own.exists()


# ---- a box of the session's own: ids, the carrier, the anchors ----------------


def test_a_box_id_is_32_lowercase_hex_and_nothing_else():
    assert sandboxplan.valid_box_id(BOX)
    minted = sandboxplan.new_box_id()
    assert sandboxplan.valid_box_id(minted) and minted != sandboxplan.new_box_id()
    for bad in (
        BOX.upper(),
        BOX[:31],
        BOX + "0",
        BOX[:16] + "/" + BOX[17:],
        "..",
        "../" + BOX[3:],
        BOX[:31] + "g",
        BOX[:31] + "\n",
        "",
        None,
        7,
        BOX.encode(),
    ):
        assert not sandboxplan.valid_box_id(bad), bad
        with pytest.raises(ValueError):
            sandboxplan.box_dir(bad)
        with pytest.raises(ValueError):
            sandboxplan.box_home(bad)
    for bad_top in ("", ".", "..", "a/b", "/mnt"):
        with pytest.raises(ValueError):
            sandboxplan.box_anchor(BOX, bad_top)


def test_anchor_roots_are_the_real_top_level_directories(tmp_path):
    root = tmp_path / "slash"
    for name in ("mnt", "media", "data", "usr", "home", "tmp", "run", "lost+found"):
        (root / name).mkdir(parents=True)
    (root / "swap.img").write_text("")
    (root / "link").symlink_to(root / "data")
    assert sandboxplan.anchor_roots(str(root)) == (
        str(root / "data"),
        str(root / "media"),
        str(root / "mnt"),
    )
    assert sandboxplan.anchor_roots(str(tmp_path / "missing")) == ()
    # The machine's own: nothing the system tables or the home live in.
    for top in sandboxplan.anchor_roots():
        assert os.path.basename(top) not in sandboxplan.NEVER_ANCHORED
        assert os.path.dirname(top) == "/"


def _anchors(home, box=BOX, *dests):
    return tuple(
        (str(_root(home) / box / "anchors" / os.path.basename(dest)), dest) for dest in dests
    )


def _remounts(plan):
    args = plan["bwrap_args"]
    return [args[i + 1] for i, a in enumerate(args) if a == "--remount-ro"]


def test_the_carrier_and_the_anchors_are_bound_then_remounted_read_only(home):
    (home / ".m2").mkdir()
    (home / ".m2" / "settings.xml").write_text("<settings/>")
    plan = build_plan(_inputs(home, anchors=_anchors(home, BOX, "/mnt", "/srv")))
    args = plan["bwrap_args"]
    binds = _binds(plan, "--bind")
    box = _root(home) / BOX
    assert (str(box / "grants"), sandboxplan.CARRIER_DEST) in binds
    assert (str(box / "anchors" / "mnt"), "/mnt") in binds
    assert (str(box / "anchors" / "srv"), "/srv") in binds
    # Never --ro-bind: it is recursive, and would make a live grant that
    # already sits in the carrier read-only in a box launched later.
    ro = _binds(plan, "--ro-bind") + _binds(plan, "--ro-bind-try")
    assert not any(dest in (sandboxplan.CARRIER_DEST, "/mnt", "/srv") for _src, dest in ro)
    # Bound early — after the system and before the home's shares and the
    # workspace, which land on top…
    ws = str(home / "work" / "repo")
    for dest in (sandboxplan.CARRIER_DEST, "/mnt", "/srv"):
        src = next(s for s, d in binds if d == dest)
        assert _index(plan, "--ro-bind-try", "/usr", "/usr") < _index(plan, "--bind", src, dest)
        assert _index(plan, "--bind", src, dest) < _index(plan, "--bind", ws, ws)
    # …and every one remounted read-only after the last mount, before --proc.
    assert sorted(_remounts(plan)) == sorted([sandboxplan.CARRIER_DEST, "/mnt", "/srv"])
    mount_flags = ("--bind", "--bind-try", "--ro-bind", "--ro-bind-try", "--tmpfs")
    last_mount = max(i for i, a in enumerate(args[: args.index("--proc")]) if a in mount_flags)
    for i, arg in enumerate(args):
        if arg == "--remount-ro":
            assert last_mount < i < args.index("--proc")
    assert plan["inputs"]["box"] == BOX
    assert plan["inputs"]["carrier"] == str(box / "grants")
    assert plan["inputs"]["anchors"] == [
        [str(box / "anchors" / "mnt"), "/mnt"],
        [str(box / "anchors" / "srv"), "/srv"],
    ]
    assert plan["version"] == 2


def test_an_anchor_the_plan_binds_over_is_dropped_with_no_remount(home):
    ws = home / "work" / "repo"
    lib = home / "work" / "lib"
    lib.mkdir()
    anchors = (
        (str(_root(home) / BOX / "anchors" / "a"), str(ws)),  # the workspace itself
        (str(_root(home) / BOX / "anchors" / "b"), str(lib)),  # a grant
        (str(_root(home) / BOX / "anchors" / "c"), str(home / "work")),  # holds the workspace
        (str(_root(home) / BOX / "anchors" / "d"), "/mnt"),
        (str(_root(home) / BOX / "anchors" / "e"), "/mnt"),  # twice: once
        (str(_root(home) / BOX / "anchors" / "f"), "/"),
        (str(_root(home) / BOX / "anchors" / "g"), "relative"),
    )
    plan = build_plan(_inputs(home, grants=(str(lib),), anchors=anchors))
    # The remount acts on the topmost mount at a path: it must never be
    # the workspace, or a grant.
    assert sorted(_remounts(plan)) == sorted(
        [sandboxplan.CARRIER_DEST, str(home / "work"), "/mnt"]
    )
    assert [dest for _src, dest in plan["inputs"]["anchors"]] == [str(home / "work"), "/mnt"]
    binds = _binds(plan, "--bind")
    assert (str(ws), str(ws)) in binds
    assert not any(dest == str(ws) and "anchors" in src for src, dest in binds)
    assert not any(dest == str(lib) and "anchors" in src for src, dest in binds)
    # An anchor above the workspace stays, under it: the workspace lands
    # on top, and only the anchor is remounted.
    held = next(src for src, dest in binds if dest == str(home / "work"))
    assert _index(plan, "--bind", held, str(home / "work")) < _index(plan, "--bind", str(ws), str(ws))
    # The repository's two directories and a worktree's common git dir.
    (ws / ".git").mkdir()
    repo_anchors = (
        (str(_root(home) / BOX / "anchors" / "a"), str(ws / ".git")),
        (str(_root(home) / BOX / "anchors" / "b"), str(ws / ".claude")),
    )
    plan = build_plan(_inputs(home, anchors=repo_anchors))
    assert _remounts(plan) == [sandboxplan.CARRIER_DEST]


def test_an_anchor_that_would_cover_an_earlier_mount_is_dropped(home):
    """The anchors come after the home, the system tables, the runtime dir
    and TMPDIR: one bound over a directory holding any of them would hide
    it. A home at /data/home/u makes /data such a root."""
    anchors = (
        (str(_root(home) / BOX / "anchors" / "a"), str(home.parent)),  # holds the home
        (str(_root(home) / BOX / "anchors" / "b"), str(home)),  # the home itself
        (str(_root(home) / BOX / "anchors" / "c"), "/run"),  # holds the runtime dir
        (str(_root(home) / BOX / "anchors" / "d"), "/scratch"),  # holds TMPDIR
        (str(_root(home) / BOX / "anchors" / "e"), "/var"),  # holds /var/tmp
        (str(_root(home) / BOX / "anchors" / "f"), "/mnt"),
    )
    plan = build_plan(_inputs(home, anchors=anchors, tmpdir="/scratch/tmp"))
    assert _remounts(plan) == [sandboxplan.CARRIER_DEST, "/mnt"]
    assert _binds(plan, "--bind")[0] == (str(_root(home) / BOX / "home"), str(home))


def test_two_boxes_plans_share_no_path_under_the_root(home):
    (_root(home) / OTHER_BOX / "home").mkdir(parents=True)
    one = build_plan(_inputs(home, BOX, anchors=_anchors(home, BOX, "/mnt")))
    two = build_plan(_inputs(home, OTHER_BOX, anchors=_anchors(home, OTHER_BOX, "/mnt")))
    root = str(_root(home))
    under = lambda plan: {a for a in plan["bwrap_args"] if a.startswith(root + "/")}  # noqa: E731
    assert under(one) and under(two)
    assert under(one).isdisjoint(under(two))
    assert all(a.startswith(f"{root}/{BOX}/") for a in under(one))
    assert all(a.startswith(f"{root}/{OTHER_BOX}/") for a in under(two))
    # The same mounts otherwise, in the same places.
    assert _binds(one, "--bind-try") == _binds(two, "--bind-try")
    assert [d for _s, d in _binds(one, "--bind")] == [d for _s, d in _binds(two, "--bind")]


def test_a_grant_that_reaches_the_sandbox_root_is_never_bound(home):
    other_home = _root(home) / OTHER_BOX / "home"
    (other_home / "dev").mkdir(parents=True)
    grants = (
        str(_root(home)),
        str(other_home),
        str(other_home / "dev"),
        str(_root(home) / BOX / "home"),
        str(_root(home).parent),
    )
    plan = build_plan(_inputs(home, grants=grants))
    assert plan["inputs"]["grants"] == []
    sources = [src for src, _d in _binds(plan, "--bind-try")]
    assert not any(grant in sources for grant in grants)
    notes = [n for n in plan["notes"] if "skipped" in n]
    assert len(notes) == len(grants)
    assert all("reaches the sandbox homes" in n for n in notes)
    # A root reached through a symlink is the root.
    (home / "alias").symlink_to(_root(home))
    plan = build_plan(_inputs(home, grants=(str(home / "alias" / OTHER_BOX / "home"),)))
    assert plan["inputs"]["grants"] == []
    # And a share that would carry the root is what the protect-check refuses.
    with pytest.raises(PlanRefused, match="must stay outside"):
        build_plan(
            _inputs(
                home,
                sandbox_root=str(home / ".m2" / "boxes"),
                sandbox_home=str(home / ".m2" / "boxes" / BOX / "home"),
                carrier=str(home / ".m2" / "boxes" / BOX / "grants"),
                protected=(str(home / ".m2" / "boxes"),),
            )
        )


# ---- grants are a session's: keyed by its box ----------------------------------

THIRD_BOX = "00000000000000000000000000000000"


def _grants_bound(plan):
    """The directories a plan binds as grants: its record of them, checked
    against its arguments."""
    binds = _binds(plan, "--bind-try")
    for grant in plan["inputs"]["grants"]:
        assert (grant, grant) in binds
    return plan["inputs"]["grants"]


def test_two_boxes_of_one_workspace_hold_separate_grants(
    monkeypatch, tmp_path, home, fresh_probe
):
    """Two sessions of the same project, side by side: a directory allowed
    to one is in the other's list no more than in its plan."""
    ws = home / "work" / "repo"
    lib = home / "work" / "lib"
    lib.mkdir()
    state = _GrantState()
    host = _host(monkeypatch, tmp_path, home, state)
    assert host.allow(BOX, str(ws), str(lib)) == ""
    assert host.grants(BOX) == [str(lib)]
    assert host.grants(OTHER_BOX) == []
    assert state.grants == {BOX: [str(lib)]}
    one = build_plan(sandboxplan.gather_inputs(str(ws), "com.example.App", state, BOX))
    two = build_plan(sandboxplan.gather_inputs(str(ws), "com.example.App", state, OTHER_BOX))
    assert one["workspace"] == two["workspace"]
    assert _grants_bound(one) == [str(lib)]
    assert _grants_bound(two) == []
    assert not any(str(lib) in arg for arg in two["bwrap_args"])
    # A session started afterwards, in the same workspace: none either.
    later = build_plan(sandboxplan.gather_inputs(str(ws), "com.example.App", state, THIRD_BOX))
    assert _grants_bound(later) == []
    # Taking it back in one touches no other.
    assert host.allow(OTHER_BOX, str(ws), str(lib)) == ""
    host.revoke(BOX, str(lib))
    assert state.grants == {OTHER_BOX: [str(lib)]}
    # No box, no grant: a path is not a key, and neither is nothing.
    for bad in ("", str(ws), "../" + BOX, BOX.upper(), None):
        assert host.allow(bad, str(ws), str(lib)) == "this session has no sandbox yet", bad
    assert state.grants == {OTHER_BOX: [str(lib)]}
    # "Already inside the workspace" is still about the session's workspace.
    (ws / "sub").mkdir()
    assert host.allow(BOX, str(ws), str(ws / "sub")) == "already inside the workspace"


def test_a_worktree_session_and_its_repositorys_are_two_boxes_with_two_lists(
    monkeypatch, tmp_path, home, fresh_probe
):
    repo = home / "work" / "repo"
    wt = repo / ".claude" / "worktrees" / "brave-otter"
    wt.mkdir(parents=True)
    lib = home / "work" / "lib"
    other = home / "work" / "other"
    lib.mkdir()
    other.mkdir()
    state = _GrantState()
    host = _host(monkeypatch, tmp_path, home, state)
    assert host.allow(BOX, str(repo), str(lib)) == ""
    assert host.allow(OTHER_BOX, str(wt), str(other)) == ""
    assert state.grants == {BOX: [str(lib)], OTHER_BOX: [str(other)]}
    in_repo = build_plan(sandboxplan.gather_inputs(str(repo), "com.example.App", state, BOX))
    in_tree = build_plan(sandboxplan.gather_inputs(str(wt), "com.example.App", state, OTHER_BOX))
    assert _grants_bound(in_repo) == [str(lib)]
    assert _grants_bound(in_tree) == [str(other)]
    assert "grants_key" not in in_repo["inputs"] and "grants_key" not in in_tree["inputs"]


def test_load_plan_reads_a_plan_with_or_without_a_grants_key(home, tmp_path):
    plan, path = _written(home, tmp_path, grants=(str(home / "work" / "lib"),))
    assert "grants_key" not in plan["inputs"]
    assert sandboxplan.load_plan(path) is not None
    # One an earlier build of the stack wrote still carries the key.
    carried = {**plan, "inputs": {**plan["inputs"], "grants_key": str(home / "work" / "repo")}}
    with open(path, "w") as fh:
        json.dump(carried, fh)
    loaded = sandboxplan.load_plan(path)
    assert loaded is not None and loaded["inputs"]["box"] == BOX
    assert plan["version"] == sandboxplan.PLAN_VERSION == 2


def test_plan_stale_reads_the_box_from_the_plan(monkeypatch, tmp_path, home, fresh_probe):
    ws = home / "work" / "repo"
    lib = home / "work" / "lib"
    lib.mkdir()
    state = _GrantState()
    host = _host(monkeypatch, tmp_path, home, state)
    one = host.prepare_launch(str(ws), BOX)
    two = host.prepare_launch(str(ws), OTHER_BOX)
    assert host.allow(BOX, str(ws), str(lib)) == ""
    # The same workspace, asked about both: only the box that was allowed
    # something has anything to restart for.
    assert host.plan_stale(one, str(ws)) is True
    assert host.plan_stale(two, str(ws)) is False
    assert host.plan_stale(one, str(ws), live=[str(lib)]) is False


def test_a_siblings_grants_are_its_parents_as_launched(monkeypatch, tmp_path, home, fresh_probe):
    ws = home / "work" / "repo"
    (ws / "sub").mkdir()
    lib = home / "work" / "lib"
    later = home / "work" / "later"
    pinned = home / "work" / "pinned"
    for made in (lib, later, pinned):
        made.mkdir()
    state = _GrantState({BOX: [str(lib)]})
    host = _host(monkeypatch, tmp_path, home, state)
    # A default of the project the parent was never given.
    assert host.set_project_default(str(ws), str(pinned), True) == ""
    parent = host.prepare_launch(str(ws), BOX)
    path, box, reason = host.derive(parent, str(ws / "sub"))
    assert reason == "" and path is not None
    sibling = sandboxplan.load_plan(path)
    # Its list says what its plan binds: the parent's static grants.
    assert host.grants(box) == [str(lib)] == _grants_bound(sibling)
    assert state.grants == {BOX: [str(lib)], box: [str(lib)]}
    assert host.plan_stale(path, str(ws)) is False
    # None of the project's defaults that its parent's plan lacks.
    assert str(pinned) not in host.grants(box)
    assert not any(str(pinned) in arg for arg in sibling["bwrap_args"])
    # What the parent is allowed afterwards reaches neither the sibling's
    # list nor its plan; nor the other way round.
    assert host.allow(BOX, str(ws), str(later)) == ""
    assert host.grants(box) == [str(lib)]
    assert not any(str(later) in arg for arg in sandboxplan.load_plan(path)["bwrap_args"])
    assert host.allow(box, str(ws), str(pinned)) == ""
    assert host.grants(BOX) == [str(lib), str(later)]
    # A sibling that is refused records nothing.
    before = dict(state.grants)
    refused, no_box, reason = host.derive(parent, str(home / "work" / "other"))
    assert refused is None and no_box == "" and reason
    assert state.grants == before


def test_forget_box_drops_the_grants_of_a_box_no_session_names(
    monkeypatch, tmp_path, home, fresh_probe
):
    lib = home / "work" / "lib"
    state = _GrantState({BOX: [str(lib)], OTHER_BOX: [str(lib)]}, boxes={BOX})
    host = _host(monkeypatch, tmp_path, home, state)
    discarded = []
    monkeypatch.setattr(host, "discard_box_async", discarded.append)
    host.forget_box(OTHER_BOX)  # nobody's
    host.forget_box(BOX)  # a session's
    assert state.grants == {BOX: [str(lib)]}
    # Discarding is asked for either way: it refuses what is named itself.
    assert discarded == [OTHER_BOX, BOX]
    for bad in ("", "not a box", "../" + BOX):
        host.forget_box(bad)
    assert discarded == [OTHER_BOX, BOX]
    assert state.grants == {BOX: [str(lib)]}


class _SessionState(_GrantState):
    """…and the session → box map, as AppState keeps it."""

    def __init__(self, *args, sessions=None, **kw):
        super().__init__(*args, **kw)
        self.sessions = dict(sessions or {})

    def sandbox_box(self, session_id):
        return self.sessions.get(session_id, "")

    def set_sandboxed(self, session_id, sandboxed, box=None):
        self.sessions[session_id] = self.sessions.get(session_id, "") if box is None else box

    def sandbox_boxes(self):
        return {box for box in self.sessions.values() if box}


def test_a_continue_tab_takes_over_the_grants_of_the_box_the_session_had(
    monkeypatch, tmp_path, home, fresh_probe
):
    ws = home / "work" / "repo"
    lib = home / "work" / "lib"
    other = home / "work" / "other"
    mine = home / "work" / "mine"
    pinned = home / "work" / "pinned"
    for made in (lib, other, mine, pinned):
        made.mkdir()
    state = _SessionState(
        {BOX: [str(lib), str(other)], OTHER_BOX: [str(mine), str(other)]},
        sessions={"s": BOX},
    )
    host = _host(monkeypatch, tmp_path, home, state)
    host.set_project_default(str(ws), str(pinned), True)
    forgotten = []
    monkeypatch.setattr(host, "discard_box_async", forgotten.append)
    # The session had BOX; the --continue tab launched in OTHER_BOX, with
    # nothing but what it was allowed meanwhile.
    assert host.settle_box("s", OTHER_BOX, str(ws), owed=True) is True  # a delivery is owed
    assert state.sessions == {"s": OTHER_BOX}
    assert state.grants == {OTHER_BOX: [str(mine), str(other), str(lib)]}  # the old box's went with it
    assert forgotten == [BOX]
    # No defaults: the list the user left the session with is what it has.
    assert str(pinned) not in state.grants[OTHER_BOX]
    # Again, the same box: nothing to settle.
    assert host.settle_box("s", OTHER_BOX, str(ws), owed=False) is False
    assert forgotten == [BOX]


def test_a_continue_tab_on_a_session_with_no_box_gets_the_defaults_then(
    monkeypatch, tmp_path, home, fresh_probe
):
    ws = home / "work" / "repo"
    pinned = home / "work" / "pinned"
    mine = home / "work" / "mine"
    pinned.mkdir()
    mine.mkdir()
    state = _SessionState({BOX: [str(mine)]}, sessions={"sticky": ""})
    host = _host(monkeypatch, tmp_path, home, state)
    host.set_project_default(str(ws), str(pinned), True)
    monkeypatch.setattr(host, "discard_box_async", lambda box: None)
    # It was unsandboxed, or sticky with no box: seeded now, after what
    # the tab was allowed before it resolved.
    assert host.settle_box("unsandboxed", BOX, str(ws), owed=True) is True
    assert state.grants == {BOX: [str(mine), str(pinned)]}
    assert state.sessions["unsandboxed"] == BOX
    assert host.settle_box("sticky", OTHER_BOX, str(ws), owed=True) is True
    assert state.grants[OTHER_BOX] == [str(pinned)]
    # A new session's box was seeded when it was minted: not again, so a
    # default the user took from it before it resolved stays gone.
    box = host.mint_box(str(ws))
    host.revoke(box, str(pinned))
    assert host.settle_box("new", box, str(ws), owed=False) is False
    assert host.grants(box) == []
    assert state.sessions["new"] == box
    # A sibling's box, and one with no workspace to read defaults by.
    assert host.settle_box("x", "not a box", str(ws), owed=True) is False
    assert host.settle_box("", BOX, str(ws), owed=True) is False
    third = host.mint_box(str(ws), seed=False)
    assert host.settle_box("y", third, None, owed=True) is True
    assert host.grants(third) == []


def test_prune_grants(monkeypatch, tmp_path, home, fresh_probe):
    lib = home / "work" / "lib"
    gone, on_disk, named = THIRD_BOX, OTHER_BOX, BOX
    state = _GrantState(
        {gone: [str(lib)], on_disk: [str(lib)], named: [str(lib)]}, boxes={named}
    )
    host = _host(monkeypatch, tmp_path, home, state)
    (_root(home) / on_disk / "home").mkdir(parents=True)
    import shutil

    shutil.rmtree(_root(home) / named)  # a session names it: kept, directory or none
    assert not (_root(home) / gone).exists()
    assert host.prune_grants() == 1
    assert state.grants == {on_disk: [str(lib)], named: [str(lib)]}
    assert host.prune_grants() == 0
    # The directory swept since, at the next start.
    shutil.rmtree(_root(home) / on_disk)
    assert host.prune_grants() == 1
    assert state.grants == {named: [str(lib)]}
    # Whether or not this instance owns the root: it is its own state.
    scratch = tmp_path / "scratch.json"
    scratch.write_text("{}")
    (_root(home) / "owner").write_text(json.dumps({"state": str(scratch)}))
    state.grants[gone] = [str(lib)]
    assert host.prune_grants() == 1


# ---- a project's defaults: a template for new sessions -------------------------


def test_project_key(tmp_path, home):
    repo = home / "work" / "repo"
    wt = repo / ".claude" / "worktrees" / "brave-otter"
    wt.mkdir(parents=True)
    lib = home / "work" / "lib"
    lib.mkdir()
    assert sandboxplan.project_key(str(wt)) == str(repo.resolve())
    assert sandboxplan.project_key(str(wt / "src")) == str(repo.resolve())
    assert sandboxplan.project_key(str(repo)) == str(repo.resolve())
    assert sandboxplan.project_key(str(lib)) == str(lib.resolve())
    link = tmp_path / "link"
    link.symlink_to(wt)
    assert sandboxplan.project_key(str(link)) == str(repo.resolve())
    assert not hasattr(sandboxplan, "grants_key")


def test_set_project_default(monkeypatch, tmp_path, home, fresh_probe):
    ws = home / "work" / "repo"
    lib = home / "work" / "lib"
    lib.mkdir()
    (ws / "sub").mkdir()
    (home / ".ssh").mkdir()
    state = _GrantState()
    host = _host(monkeypatch, tmp_path, home, state)
    assert host.project_grants(str(ws)) == []
    assert host.is_project_default(str(ws), str(lib)) is False
    assert host.set_project_default(str(ws), str(lib), True) == ""
    assert host.set_project_default(str(ws), str(lib) + "/", True) == ""  # twice: one entry
    assert host.project_grants(str(ws)) == [str(lib)]
    assert host.is_project_default(str(ws), str(lib)) is True
    assert state.defaults == {str(ws.resolve()): [str(lib)]}
    # The guard a grant is held to.
    assert "reaches" in host.set_project_default(str(ws), str(home / ".ssh"), True)
    assert host.set_project_default(str(ws), str(home), True) == "the home directory itself"
    assert host.set_project_default(str(ws), str(_root(home)), True) == (
        "reaches the sandbox homes"
    )
    assert host.set_project_default(str(ws), "relative", True) == "not an absolute path"
    # Without the two checks that are about one session on one day: a
    # directory inside this workspace, and one that isn't there (yet).
    assert host.set_project_default(str(ws), str(ws / "sub"), True) == ""
    assert host.set_project_default(str(ws), str(home / "work" / "not-yet"), True) == ""
    assert host.project_grants(str(ws)) == [
        str(lib), str(ws / "sub"), str(home / "work" / "not-yet"),
    ]
    # No session's list was touched by any of it.
    assert state.grants == {}
    # Off again; removing what isn't there is nothing.
    assert host.set_project_default(str(ws), str(ws / "sub"), False) == ""
    assert host.set_project_default(str(ws), str(home / "never"), False) == ""
    assert host.set_project_default(str(ws), str(home / ".ssh"), False) == ""
    assert host.project_grants(str(ws)) == [str(lib), str(home / "work" / "not-yet")]


def test_a_worktree_session_shares_its_repositorys_defaults(
    monkeypatch, tmp_path, home, fresh_probe
):
    repo = home / "work" / "repo"
    wt = repo / ".claude" / "worktrees" / "brave-otter"
    wt.mkdir(parents=True)
    lib = home / "work" / "lib"
    lib.mkdir()
    state = _GrantState()
    host = _host(monkeypatch, tmp_path, home, state)
    assert host.set_project_default(str(wt), str(lib), True) == ""
    assert list(state.defaults) == [str(repo.resolve())]  # one list
    assert host.project_grants(str(repo)) == host.project_grants(str(wt)) == [str(lib)]
    assert host.is_project_default(str(repo), str(lib))
    # …and a new session of either starts with it.
    assert host.grants(host.mint_box(str(repo))) == [str(lib)]
    assert host.grants(host.mint_box(str(wt))) == [str(lib)]
    # Another project has its own.
    assert host.project_grants(str(lib)) == []
    assert host.grants(host.mint_box(str(home / "work" / "elsewhere"))) == []


def test_mint_box_seeds_a_copy(monkeypatch, tmp_path, home, fresh_probe):
    ws = home / "work" / "repo"
    lib = home / "work" / "lib"
    other = home / "work" / "other"
    lib.mkdir()
    other.mkdir()
    state = _GrantState()
    host = _host(monkeypatch, tmp_path, home, state)
    host.set_project_default(str(ws), str(lib), True)
    box = host.mint_box(str(ws))
    assert sandboxplan.valid_box_id(box)
    assert host.grants(box) == [str(lib)]
    plan = build_plan(sandboxplan.gather_inputs(str(ws), "com.example.App", state, box))
    assert _grants_bound(plan) == [str(lib)]  # from its launch, as a plain bind
    # A copy: the two lists have nothing to do with each other from here.
    host.set_project_default(str(ws), str(other), True)
    host.set_project_default(str(ws), str(lib), False)
    assert host.project_grants(str(ws)) == [str(other)]
    assert host.grants(box) == [str(lib)]  # a default removed takes it from no session
    host.revoke(box, str(lib))
    host.allow(box, str(ws), str(lib))
    assert host.project_grants(str(ws)) == [str(other)]  # and the reverse
    # The next one starts with what the defaults are now, a different id.
    second = host.mint_box(str(ws))
    assert second != box and host.grants(second) == [str(other)]
    # Without seeding: no entry at all.
    bare = host.mint_box(str(ws), seed=False)
    assert host.grants(bare) == [] and bare not in state.grants


def test_a_box_minted_before_a_default_was_added_doesnt_hold_it(
    monkeypatch, tmp_path, home, fresh_probe
):
    ws = home / "work" / "repo"
    lib = home / "work" / "lib"
    lib.mkdir()
    state = _GrantState()
    host = _host(monkeypatch, tmp_path, home, state)
    box = host.mint_box(str(ws))
    path = host.prepare_launch(str(ws), box)
    assert host.set_project_default(str(ws), str(lib), True) == ""
    assert host.grants(box) == []
    assert box not in state.grants
    assert host.plan_stale(path, str(ws)) is False  # nothing to restart for
    after = build_plan(sandboxplan.gather_inputs(str(ws), "com.example.App", state, box))
    assert _grants_bound(after) == []
    assert not any(str(lib) in arg for arg in after["bwrap_args"])


def test_a_default_this_session_cant_be_given_is_skipped_and_stays(
    monkeypatch, tmp_path, home, fresh_probe
):
    ws = home / "work" / "repo"
    lib = home / "work" / "lib"
    gone = home / "work" / "gone"
    lib.mkdir()
    gone.mkdir()
    (ws / "sub").mkdir()
    state = _GrantState()
    host = _host(monkeypatch, tmp_path, home, state)
    for path in (ws / "sub", gone, lib, home / "dotfiles"):
        assert host.set_project_default(str(ws), str(path), True) == ""
    gone.rmdir()  # no longer a directory
    (home / "dotfiles" / "ssh").mkdir(parents=True)
    (home / ".ssh").symlink_to(home / "dotfiles" / "ssh")  # now a secret's ancestor
    box = host.mint_box(str(ws))
    assert host.grants(box) == [str(lib)]
    # Every one of them is still a default: another session, another day.
    assert host.project_grants(str(ws)) == [
        str(ws / "sub"), str(gone), str(lib), str(home / "dotfiles"),
    ]
    # seed_box adds to what a box holds already, once.
    (home / "work" / "mine").mkdir()
    assert host.allow(box, str(ws), str(home / "work" / "mine")) == ""
    host.revoke(box, str(lib))
    assert host.seed_box(box, str(ws)) == [str(home / "work" / "mine"), str(lib)]
    assert host.seed_box(box, str(ws)) == [str(home / "work" / "mine"), str(lib)]
    assert host.seed_box("not a box", str(ws)) == []


# ---- scrubbing the home before a launch ---------------------------------------


def _scrub_plan(home, *mounts):
    """A plan's worth of arguments: (flag, source, destination) triples,
    and ("--tmpfs", destination) pairs."""
    args = ["--bind", "/nonexistent/home", str(home)]
    for mount in mounts:
        args += list(mount)
    return {"bwrap_args": args}


def test_scrub_home_removes_a_symlink_at_a_destination(tmp_path):
    home = tmp_path / "home"
    src = home / "work" / "lib"
    src.mkdir(parents=True)
    (src / "precious.txt").write_text("keep")
    box_home = tmp_path / "box" / "home"
    (box_home / "work").mkdir(parents=True)
    (box_home / "work" / "lib").symlink_to(src)  # what a live grant left, or a plant
    notes = sandboxplan.scrub_home(
        str(box_home), _scrub_plan(home, ("--bind-try", str(src), str(src))), str(home)
    )
    assert not os.path.lexists(box_home / "work" / "lib")
    assert (box_home / "work").is_dir()
    assert (src / "precious.txt").read_text() == "keep"
    assert len(notes) == 1 and "symlink" in notes[0] and str(src) in notes[0]


def test_scrub_home_removes_a_symlink_at_a_parent_component(tmp_path):
    home = tmp_path / "home"
    src = home / "work" / "deep" / "lib"
    src.mkdir(parents=True)
    victim = tmp_path / "victim"
    (victim / "deep" / "lib").mkdir(parents=True)
    (victim / "deep" / "lib" / "precious.txt").write_text("keep")
    (victim / "other.txt").write_text("keep")
    box_home = tmp_path / "box" / "home"
    box_home.mkdir(parents=True)
    (box_home / "work").symlink_to(victim)
    notes = sandboxplan.scrub_home(
        str(box_home), _scrub_plan(home, ("--bind", str(src), str(src))), str(home)
    )
    assert not os.path.lexists(box_home / "work")
    # Never followed: everything the link pointed at is as it was.
    assert (victim / "deep" / "lib" / "precious.txt").read_text() == "keep"
    assert (victim / "other.txt").read_text() == "keep"
    assert sorted(p.name for p in victim.iterdir()) == ["deep", "other.txt"]
    assert len(notes) == 1


def test_scrub_home_clears_a_file_where_a_directory_goes(tmp_path):
    home = tmp_path / "home"
    src = home / "work" / "lib"
    src.mkdir(parents=True)
    file_src = home / ".gitconfig"
    file_src.write_text("[user]")
    box_home = tmp_path / "box" / "home"
    (box_home / "work").mkdir(parents=True)
    (box_home / "work" / "lib").write_text("in the way")
    (box_home / ".gitconfig").mkdir()  # an empty directory where a file goes
    (box_home / ".cache").write_text("a file where a parent directory goes")
    plan = _scrub_plan(
        home,
        ("--bind-try", str(src), str(src)),
        ("--ro-bind-try", str(file_src), str(file_src)),
        ("--tmpfs", str(home / ".cache" / "scratch")),
    )
    notes = sandboxplan.scrub_home(str(box_home), plan, str(home))
    assert not os.path.lexists(box_home / "work" / "lib")
    assert not os.path.lexists(box_home / ".gitconfig")
    assert not os.path.lexists(box_home / ".cache")
    assert len(notes) == 3


def test_scrub_home_leaves_what_matches_and_what_it_cannot_settle(tmp_path):
    home = tmp_path / "home"
    src = home / "work" / "lib"
    src.mkdir(parents=True)
    file_src = home / ".gitconfig"
    file_src.write_text("[user]")
    other_file = home / ".bashrc"
    other_file.write_text("")
    box_home = tmp_path / "box" / "home"
    (box_home / "work" / "lib").mkdir(parents=True)  # bwrap's own stub
    (box_home / "work" / "lib" / "left-by-the-agent").write_text("x")
    (box_home / ".gitconfig").write_text("")  # a file stub for a file
    (box_home / ".bashrc").mkdir()  # a non-empty directory where a file goes
    (box_home / ".bashrc" / "inside").write_text("x")
    (box_home / "unrelated").symlink_to(tmp_path)  # on no mount's path
    (box_home / "notes.txt").write_text("the agent's own")
    plan = _scrub_plan(
        home,
        ("--bind-try", str(src), str(src)),
        ("--ro-bind-try", str(file_src), str(file_src)),
        ("--ro-bind-try", str(other_file), str(other_file)),
        # A source that doesn't exist is a bind bwrap skips: nothing to clear.
        ("--bind-try", str(home / "missing"), str(home / "unrelated")),
        # Destinations outside the home are none of the home's business.
        ("--ro-bind-try", "/usr", "/usr"),
        ("--tmpfs", "/run/user/1000"),
    )
    assert sandboxplan.scrub_home(str(box_home), plan, str(home)) == []
    assert (box_home / "work" / "lib" / "left-by-the-agent").exists()
    assert (box_home / ".gitconfig").is_file()
    assert (box_home / ".bashrc" / "inside").exists()
    assert (box_home / "unrelated").is_symlink()
    assert (box_home / "notes.txt").read_text() == "the agent's own"
    # No home at all, or the home a symlink: nothing happens, quietly.
    assert sandboxplan.scrub_home(str(tmp_path / "nowhere"), plan, str(home)) == []
    (tmp_path / "linked-home").symlink_to(box_home)
    (box_home / "work" / "lib" / "left-by-the-agent").unlink()
    (box_home / "work" / "lib").rmdir()
    (box_home / "work" / "lib").symlink_to(src)
    assert sandboxplan.scrub_home(str(tmp_path / "linked-home"), plan, str(home)) == []
    assert (box_home / "work" / "lib").is_symlink()


def test_scrub_home_touches_nothing_outside_the_home(tmp_path):
    """The planted link points at a directory full of files, under the very
    names the mounts would have: every one survives."""
    home = tmp_path / "home"
    (home / "work" / "lib").mkdir(parents=True)
    (home / ".claude").mkdir()
    (home / ".gitconfig").write_text("[user]")
    victim = tmp_path / "victim"
    (victim / "work" / "lib").mkdir(parents=True)
    (victim / ".claude").mkdir()
    files = [
        victim / "precious.txt",
        victim / ".gitconfig",
        victim / "work" / "lib" / "source.py",
        victim / ".claude" / "settings.json",
    ]
    for path in files:
        path.write_text("keep")
    (victim / "work" / "link").symlink_to(victim / "precious.txt")
    before = sorted(str(p.relative_to(victim)) for p in victim.rglob("*"))
    box = tmp_path / "box"
    box.mkdir()
    (box / "home").symlink_to(victim)  # the home itself
    plan = _scrub_plan(
        home,
        ("--bind-try", str(home / "work" / "lib"), str(home / "work" / "lib")),
        ("--bind-try", str(home / ".claude"), str(home / ".claude")),
        ("--ro-bind-try", str(home / ".gitconfig"), str(home / ".gitconfig")),
    )
    assert sandboxplan.scrub_home(str(box / "home"), plan, str(home)) == []
    # And planted deeper: every top-level name of the home a link to it.
    (box / "home").unlink()
    (box / "home").mkdir()
    for name in ("work", ".claude", ".gitconfig"):
        (box / "home" / name).symlink_to(victim / name)
    notes = sandboxplan.scrub_home(str(box / "home"), plan, str(home))
    assert len(notes) == 3
    assert list((box / "home").iterdir()) == []
    assert sorted(str(p.relative_to(victim)) for p in victim.rglob("*")) == before
    for path in files:
        assert path.read_text() == "keep"


def test_scrub_home_skips_a_destination_inside_an_earlier_mount(tmp_path):
    """A mount under the workspace lands in the workspace's own directory,
    not in the home: what the home holds under that name is no mount
    point, and is left alone."""
    home = tmp_path / "home"
    ws = home / "work" / "repo"
    (ws / ".git").mkdir(parents=True)
    box_home = tmp_path / "box" / "home"
    (box_home / "work" / "repo").mkdir(parents=True)
    (box_home / "work" / "repo" / ".git").symlink_to(tmp_path)
    plan = _scrub_plan(
        home,
        ("--bind", str(ws), str(ws)),
        ("--bind-try", str(ws / ".git"), str(ws / ".git")),
    )
    assert sandboxplan.scrub_home(str(box_home), plan, str(home)) == []
    assert (box_home / "work" / "repo" / ".git").is_symlink()


def test_prepare_launch_scrubs_the_home_and_says_so(
    monkeypatch, tmp_path, home, fresh_probe
):
    ws = home / "work" / "repo"
    lib = home / "work" / "lib"
    lib.mkdir()
    (lib / "precious.txt").write_text("keep")
    state = _GrantState({BOX: [str(lib)]})
    host = _host(monkeypatch, tmp_path, home, state)
    box_home = _root(home) / BOX / "home"
    (box_home / "work").mkdir()
    (box_home / "work" / "lib").symlink_to(sandboxplan.CARRIER_DEST + "/lib-0a1b2c3d")
    path = host.prepare_launch(str(ws), BOX)
    assert path is not None
    assert not os.path.lexists(box_home / "work" / "lib")
    plan = sandboxplan.load_plan(path)
    assert any("symlink" in note and str(lib) in note for note in plan["notes"])
    assert (lib / "precious.txt").read_text() == "keep"


# ---- removing a box ---------------------------------------------------------------


@pytest.fixture
def root(monkeypatch, tmp_path):
    """A sandbox root of the test's own."""
    root = tmp_path / "boxes"
    root.mkdir()
    monkeypatch.setenv("COLLINS_SANDBOX_ROOT", str(root))
    return root


def _make_box(root, box=BOX):
    for made in ("home/dev/project", "grants", "anchors/mnt/data"):
        (root / box / made).mkdir(parents=True)
    (root / box / "home" / ".claude.json").write_text("{}")
    (root / box / "home" / "dev" / "project" / "main.py").write_text("print()")
    return root / box


def test_remove_box_removes_a_tree_without_following_its_symlinks(root, tmp_path):
    box = _make_box(root)
    victim = tmp_path / "victim"
    (victim / "deep").mkdir(parents=True)
    (victim / "deep" / "precious.txt").write_text("keep")
    (victim / "file.txt").write_text("keep")
    (box / "home" / "to-dir").symlink_to(victim)
    (box / "home" / "to-file").symlink_to(victim / "file.txt")
    (box / "home" / "dev" / "dangling").symlink_to(tmp_path / "nothing")
    (box / "home" / "dev" / "up").symlink_to("../../..")
    # What an agent leaves behind: a read-only directory with files in it.
    locked = box / "home" / "locked"
    locked.mkdir()
    (locked / "inside").write_text("x")
    locked.chmod(0o555)
    assert sandboxplan.remove_box(BOX, mounts=lambda: ["/", "/proc", str(tmp_path / "elsewhere")])
    assert not box.exists()
    assert root.is_dir()
    assert (victim / "deep" / "precious.txt").read_text() == "keep"
    assert (victim / "file.txt").read_text() == "keep"
    # Gone already, or never there: nothing removed, no error.
    assert sandboxplan.remove_box(BOX, mounts=lambda: []) is False
    with pytest.raises(ValueError):
        sandboxplan.remove_box("../victim", mounts=lambda: [])
    with pytest.raises(ValueError):
        sandboxplan.remove_box("", mounts=lambda: [])


def test_remove_box_refuses_a_box_with_a_mount_under_it(root):
    box = _make_box(root)
    everything = sorted(str(p) for p in box.rglob("*"))
    for point in (
        str(box / "grants" / "lib-0a1b2c3d"),
        str(box / "anchors" / "mnt" / "data"),
        str(box / "home"),
        str(box),
    ):
        assert sandboxplan.remove_box(BOX, mounts=lambda point=point: ["/", point]) is False
        assert sorted(str(p) for p in box.rglob("*")) == everything
    # A mount table that can't be read is no licence to delete.

    def unreadable():
        raise OSError("no /proc")

    assert sandboxplan.remove_box(BOX, mounts=unreadable) is False
    assert sorted(str(p) for p in box.rglob("*")) == everything
    # A mount beside the box, or under a name it is a prefix of, is not under it.
    assert sandboxplan.remove_box(BOX, mounts=lambda: [str(root / OTHER_BOX), str(box) + "x"])
    assert not box.exists()


def test_remove_box_sees_a_mount_through_a_symlinked_root(monkeypatch, tmp_path):
    real = tmp_path / "real-boxes"
    real.mkdir()
    (tmp_path / "boxes").symlink_to(real)
    monkeypatch.setenv("COLLINS_SANDBOX_ROOT", str(tmp_path / "boxes"))
    box = _make_box(real)
    # The kernel names the mount by its real path.
    point = str(real / BOX / "grants" / "lib")
    assert sandboxplan.remove_box(BOX, mounts=lambda: [point]) is False
    assert box.is_dir()
    assert sandboxplan.remove_box(BOX, mounts=lambda: [])
    assert not box.exists()


def test_remove_box_stops_at_another_device(root):
    """An entry on another device is a mount the mount table didn't show:
    the removal stops before anything is touched."""
    box = _make_box(root)
    everything = sorted(str(p) for p in box.rglob("*"))

    def stat_with_a_mount(name, *, dir_fd=None, follow_symlinks=True):
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        if name == "data":  # anchors/mnt/data: the last directory walked
            return os.stat_result((*st[:2], st.st_dev + 1, *st[3:]))
        return st

    assert sandboxplan.remove_box(BOX, mounts=lambda: [], stat_fn=stat_with_a_mount) is False
    assert sorted(str(p) for p in box.rglob("*")) == everything
    assert sandboxplan.remove_box(BOX, mounts=lambda: [])
    assert not box.exists()


def test_mount_points_reads_the_kernels_table():
    text = (
        "36 35 98:0 / / rw,noatime shared:1 - ext4 /dev/sda1 rw\n"
        "412 36 0:60 / /home/u/my\\040repo rw,nosuid shared:230 - fuse.bindfs collins:123 rw\n"
        "short line\n"
    )
    assert sandboxplan.mount_points(text) == ["/", "/home/u/my repo"]
    assert "/" in sandboxplan.mount_points()  # the real one


def test_discard_box_removes_only_what_nothing_needs(monkeypatch, tmp_path, home, fresh_probe):
    state = _GrantState()
    host = _host(monkeypatch, tmp_path, home, state)
    box = _make_box(_root(home), OTHER_BOX)
    # A box a session of the state names.
    state.boxes = {OTHER_BOX}
    assert host.discard_box(OTHER_BOX) is False
    assert box.is_dir()
    state.boxes = set()
    # A box another running process holds (pid 1 always runs).
    (box / "lease").write_text(json.dumps({"pid": 1, "app_id": "com.example.Other"}))
    assert host.discard_box(OTHER_BOX) is False
    assert box.is_dir()
    # A box this process holds.
    (box / "lease").unlink()
    host.hold(OTHER_BOX)
    assert host.discard_box(OTHER_BOX) is False
    host.hold(OTHER_BOX)  # twice: counted
    host.release(OTHER_BOX)
    assert host.held(OTHER_BOX)
    assert (box / "lease").exists()
    assert host.discard_box(OTHER_BOX) is False
    host.release(OTHER_BOX)
    assert not host.held(OTHER_BOX)
    assert not (box / "lease").exists()
    # A lease of a process that no longer runs is no lease.
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    (box / "lease").write_text(json.dumps({"pid": dead.pid, "app_id": "com.example.Gone"}))
    assert host.discard_box(OTHER_BOX) is True
    assert not box.exists()
    # Never anything but a box.
    assert host.discard_box("../" + BOX) is False
    assert host.discard_box("") is False
    host.release("not a box")  # quietly
    host.hold("not a box")
    assert not host.held("not a box")


def test_a_lease_that_lost_its_shape_is_no_lease(root):
    box = _make_box(root)
    for junk in ("", "not json", "[]", '{"pid": "1"}', '{"pid": true}', '{"pid": -4}', "{}"):
        (box / "lease").write_text(junk)
        assert sandboxplan.read_lease(BOX) is None, junk
    assert not sandboxplan.lease_live(None)
    (box / "lease").write_text(json.dumps({"pid": os.getpid(), "app_id": "x"}))
    assert sandboxplan.lease_live(sandboxplan.read_lease(BOX))
    # Another process's lease is not this one's to remove.
    (box / "lease").write_text(json.dumps({"pid": 1, "app_id": "x"}))
    host = sandboxplan.SandboxHost("com.example.App", _GrantState())
    host.release(BOX)
    assert (box / "lease").exists()
    # A planted link where the lease goes is never written through.
    (box / "lease").unlink()
    victim = root / "victim.json"
    (box / "lease").symlink_to(victim)
    assert sandboxplan.write_lease(BOX, "com.example.App") is False
    assert not victim.exists()


def test_sweep_boxes_leaves_what_is_not_a_box(monkeypatch, tmp_path, home, fresh_probe):
    state = _GrantState(boxes={BOX})
    host = _host(monkeypatch, tmp_path, home, state)
    root = _root(home)
    unused = _make_box(root, OTHER_BOX)
    third = "00000000000000000000000000000000"
    held = _make_box(root, third)
    (held / "lease").write_text(json.dumps({"pid": 1, "app_id": "com.example.Other"}))
    (root / "not-a-box").mkdir()  # a name that is not a box id
    (root / "not-a-box" / "keep.txt").write_text("keep")
    (root / BOX.upper()).mkdir()
    (root / "notes.txt").write_text("keep")
    assert host.sweep_boxes() == 1
    assert not unused.exists()
    assert held.is_dir()
    assert (root / BOX).is_dir()  # a session's
    assert (root / "not-a-box" / "keep.txt").read_text() == "keep"
    assert (root / BOX.upper()).is_dir()
    assert (root / "notes.txt").read_text() == "keep"
    assert host.sweep_boxes() == 0
    monkeypatch.setenv("COLLINS_SANDBOX_ROOT", str(tmp_path / "no-root"))
    assert host.sweep_boxes() == 0


def test_the_sweep_is_the_owning_states_alone(monkeypatch, tmp_path, home, fresh_probe):
    """An instance on a scratch state.json beside the user's sandbox root
    (every e2e check, every capture) names none of the user's boxes: its
    sweep must remove none of them."""
    root = _root(home)
    mine = tmp_path / "config" / "collins" / "state.json"
    mine.parent.mkdir(parents=True)
    mine.write_text("{}")
    scratch = tmp_path / "scratch" / "collins" / "state.json"
    scratch.parent.mkdir(parents=True)
    scratch.write_text("{}")
    owner = _host(monkeypatch, tmp_path, home, _GrantState(boxes={BOX}), state_file=str(mine))
    assert owner.sweep_boxes() == 0  # claims the root
    assert json.load(open(root / "owner")) == {"state": str(mine)}
    box = _make_box(root, OTHER_BOX)
    visitor = sandboxplan.SandboxHost("com.example.E2E", _GrantState(), str(scratch))
    assert visitor.owns_root() is False
    assert visitor.sweep_boxes() == 0
    assert box.is_dir() and (root / BOX).is_dir()
    assert json.load(open(root / "owner")) == {"state": str(mine)}
    # An instance that knows no state file claims nothing and sweeps
    # nothing of a root somebody owns.
    nameless = sandboxplan.SandboxHost("com.example.E2E", _GrantState())
    assert nameless.sweep_boxes() == 0
    assert box.is_dir()
    # The owner's own sweep takes what nothing names.
    assert owner.sweep_boxes() == 1
    assert not box.exists() and (root / BOX).is_dir()
    # The owning state file gone (a scratch tree cleaned up, a moved
    # config): the next instance takes the root over.
    box = _make_box(root, OTHER_BOX)
    mine.unlink()
    assert visitor.sweep_boxes() == 2  # it names neither box
    assert json.load(open(root / "owner")) == {"state": str(scratch)}
    # An owner file that lost its shape is no owner.
    (root / "owner").write_text("not json")
    assert owner.owns_root() is True
    assert json.load(open(root / "owner")) == {"state": str(mine)}


def test_the_first_launch_claims_an_unowned_root(monkeypatch, tmp_path, home, fresh_probe):
    """The root is claimed the moment this state has a box in it — not at
    the next startup's sweep, by which time a scratch instance could have
    found it unowned and swept it."""
    mine = tmp_path / "config" / "collins" / "state.json"
    mine.parent.mkdir(parents=True)
    mine.write_text("{}")
    scratch = tmp_path / "scratch" / "state.json"
    scratch.parent.mkdir()
    scratch.write_text("{}")
    root = _root(home)
    host = _host(monkeypatch, tmp_path, home, _GrantState(), state_file=str(mine))
    assert not (root / "owner").exists()
    path = host.prepare_launch(str(home / "work" / "repo"), BOX)
    assert path is not None
    assert json.load(open(root / "owner")) == {"state": str(mine)}
    # Somebody else's root stays somebody else's.
    visitor = sandboxplan.SandboxHost("com.example.E2E", _GrantState(), str(scratch))
    assert visitor.prepare_launch(str(home / "work" / "repo"), OTHER_BOX) is not None
    assert json.load(open(root / "owner")) == {"state": str(mine)}
    assert visitor.sweep_boxes() == 0
    assert (root / BOX).is_dir()


def test_a_box_is_held_from_before_it_is_made(monkeypatch, tmp_path, home, fresh_probe):
    """The startup sweep runs on a thread: a box must never be on disk
    unheld, not even while its launch is still being prepared."""
    ws = home / "work" / "repo"
    (ws / "sub").mkdir()
    host = _host(monkeypatch, tmp_path, home, _GrantState())
    root = _root(home)
    seen = []
    real_seed = sandboxplan.seed_home

    def seed(home_dir, *args):
        box = os.path.basename(os.path.dirname(home_dir))
        lease = sandboxplan.read_lease(box)
        seen.append((box, host.held(box), lease and lease["pid"], host.discard_box(box)))
        return real_seed(home_dir, *args)

    monkeypatch.setattr(sandboxplan, "seed_home", seed)
    path = host.prepare_launch(str(ws), OTHER_BOX)
    assert path is not None
    assert seen == [(OTHER_BOX, True, os.getpid(), False)]
    assert (root / OTHER_BOX / "home").is_dir()
    # A sibling's box likewise.
    sibling, box, _reason = host.derive(path, str(ws / "sub"))
    assert sibling is not None
    assert seen[1] == (box, True, os.getpid(), False)
    # A launch that can't be prepared lets go of what it held.
    third = "00000000000000000000000000000000"
    assert host.prepare_launch(str(home), third) is None
    assert not host.held(third)
    assert sandboxplan.read_lease(third) is None
    assert host.discard_box(third) is True
    # …once: a hold taken twice is still let go of once per launch.
    assert host.held(OTHER_BOX)
    host.release(OTHER_BOX)
    assert not host.held(OTHER_BOX)


def test_discard_box_async_runs_off_the_calling_thread(monkeypatch, tmp_path, home, fresh_probe):
    import threading

    host = _host(monkeypatch, tmp_path, home, _GrantState())
    box = _make_box(_root(home), OTHER_BOX)
    seen = []
    done = threading.Event()
    real = host.discard_box

    def discard(name):
        seen.append(threading.current_thread().name)
        try:
            return real(name)
        finally:
            done.set()

    monkeypatch.setattr(host, "discard_box", discard)
    host.discard_box_async(OTHER_BOX)
    assert done.wait(10)
    assert seen == ["sandbox-discard"]
    assert not box.exists()
    host.discard_box_async("not a box")  # nothing started
    assert seen == ["sandbox-discard"]


def test_the_module_stays_gtk_free():
    assert "gi.repository.Gtk" not in sys.modules
