# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Git over the API, the service's end (collins.service.gitfeed,
service.files; split-service spec §3.23, PR-2.1, D33): every builder
round-trips by name and args through `git.run` against a temp
repository, an unknown builder and bad args are refused, a cwd outside
every root is refused for a client that is not local, `git.info` answers
every read at once (the heads only when the refs digest moved), `git.plan`
applies a plan and refuses `stale` without applying when a key moved,
the trash goes through the service's mover, `git-changed` fires on an
index write and not on a `.git` basename event, the blob read answers
200 with a tag, 304 on it and 404 for nothing, and a request that runs
git is a `Deferred` the loopback pumps for.

Real git on a temp repository (skipped without git), the core over the
in-process harness (tests/inproc.py) with the feed's thread and landing
made inline, so a reply settles before `request` returns."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import inproc
import pytest
from gi.repository import Gio, GLib

from collins import diffmodel, gitfiles, gitops, gitpatch
from collins.api import protocol
from collins.service import files, gitfeed
from collins.service.core import ServiceCore

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git isn't on PATH")
pytestmark = needs_git


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=Test", *args],
        cwd=repo, check=True, capture_output=True, text=True,
    )
    return result.stdout


def make_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / "a.txt").write_text("one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nten\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-qm", "first")
    git(repo, "checkout", "-qb", "feat")
    (repo / "a.txt").write_text("ONE\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nTEN\n")
    (repo / "new.txt").write_text("hello\n")
    return repo


@pytest.fixture
def served(tmp_path, monkeypatch):
    """A core whose feed answers inline, a local loopback client and a
    repository."""
    monkeypatch.setenv("SHELL", "/bin/cat")
    core = ServiceCore(state_dir=tmp_path / "pty", get_setting=lambda key: True)
    core.git = gitfeed.GitFeed(core, dispatch=lambda fn: fn(), spawn=lambda fn, name: fn())
    server = inproc.LoopbackServer(core)
    events: list[dict] = []
    client = server.connect(lambda *a: None, events.append, device="laptop")
    client.local = True
    repo = make_repo(tmp_path)
    yield core, client, repo, events
    server.shutdown()


def run(client, builder: str, args: dict | None = None, cwd=None, **extra) -> dict:
    message = {"t": "git.run", "cwd": str(cwd), "builder": builder, "args": args or {}, **extra}
    return client.request(message)


# -- git.run ------------------------------------------------------------------------------


def test_every_builder_round_trips_by_name_and_args(served):
    """Each registered builder, called by name with sample args, makes the
    argv the client's matcher parses back to the same name and args, and
    runs on the service (a read-only sample each; git answers)."""
    core, client, repo, _events = served
    samples = {
        "status_argv": {},
        "unpushed_argv": {},
        "has_remote_tracking_argv": {},
        "branch_tips_argv": {},
        "staged_paths_argv": {},
        "log_argv": {"range_args": ["HEAD"], "limit": 5},
        "stack_walk_argv": {"lower": "main", "upper": "HEAD", "limit": 10},
        "rev_parse_argv": {"rev": "HEAD"},
        "resolve_commit_argv": {"ref": "HEAD"},
        "head_abbrev_argv": {},
        "merge_base_argv": {"a": "main", "b": "HEAD"},
        "show_argv": {"ref": "HEAD", "pathspecs": ["a.txt"], "excludes": []},
        "diff_argv": {
            "load": "unstaged", "parent_target": None, "untracked": True, "pathspecs": [], "excludes": []
        },
        "numstat_argv": {"load": "unstaged", "parent_target": None, "pathspecs": []},
        "untracked_diff_argv": {"path": "new.txt"},
        "conflict_diff_argv": {"paths": ["a.txt"]},
        "file_at_argv": {"ref": "HEAD", "path": "a.txt"},
        "unmerged_stages_argv": {"path": "a.txt"},
        "commit_subject_argv": {"ref": "HEAD"},
        "commit_message_argv": {"ref": "HEAD"},
        "status_porcelain_argv": {},
        "check_ignore_argv": {},
    }
    for name, args in samples.items():
        argv = gitops.build(name, args)
        matched = gitops.match_argv(argv)
        assert matched is not None and gitops.build(*matched) == argv, name
        reply = run(client, name, args, cwd=repo, stdin="x\0" if name == "check_ignore_argv" else "")
        assert reply["status"] is not None and not reply["unreachable"], (name, reply)
    # The mutating builders round-trip the same way, without a run here.
    for name, args in {
        "commit_argv": {"summary": "s", "body": None},
        "fixup_argv": {"sha": "a" * 40},
        "revert_argv": {"sha": "a" * 40, "commit": True},
        "revert_quit_argv": {},
        "continue_argv": {"kind": "rebase"},
        "abort_argv": {"kind": "merge"},
        "apply_argv": {"cached": True, "reverse": False, "three_way": False},
        "checkout_side_argv": {"side": "ours", "paths": ["a.txt"]},
        "checkout_paths_argv": {"paths": ["a.txt"]},
        "add_paths_argv": {"paths": ["a.txt"]},
        "reset_paths_argv": {"paths": ["a.txt"]},
        "remove_paths_argv": {"paths": ["a.txt"]},
        "stage_all_argv": {},
        "unstage_all_argv": {},
        "pull_argv": {},
        "checkout_branch_argv": {"branch": "main"},
    }.items():
        argv = gitops.build(name, args)
        matched = gitops.match_argv(argv)
        assert matched is not None and gitops.build(*matched) == argv, name
    assert set(samples) | {
        "commit_argv", "fixup_argv", "revert_argv", "revert_quit_argv", "continue_argv", "abort_argv",
        "apply_argv", "checkout_side_argv", "checkout_paths_argv", "add_paths_argv", "reset_paths_argv",
        "remove_paths_argv", "stage_all_argv", "unstage_all_argv", "pull_argv", "checkout_branch_argv",
    } == set(gitops.BUILDERS)


def test_run_answers_gits_status_and_streams(served):
    core, client, repo, _events = served
    reply = run(client, "status_argv", cwd=repo)
    assert reply["status"] == 0 and "a.txt" in reply["stdout"] and reply["stderr"] == ""
    reply = run(client, "rev_parse_argv", {"rev": "nope"}, cwd=repo)
    assert reply["status"] == 1 and not reply["unreachable"]


def test_run_refuses_an_unknown_builder_and_bad_args(served):
    core, client, repo, _events = served
    with pytest.raises(inproc.RequestRefused) as refused:
        run(client, "push_argv", cwd=repo)
    assert refused.value.error == protocol.ERROR_UNKNOWN
    with pytest.raises(inproc.RequestRefused) as refused:
        run(client, "commit_argv", {"summary": 5}, cwd=repo)
    assert refused.value.error == protocol.ERROR_INVALID
    with pytest.raises(inproc.RequestRefused) as refused:
        run(client, "checkout_branch_argv", {"branch": "--force"}, cwd=repo)
    assert refused.value.error == protocol.ERROR_INVALID
    with pytest.raises(inproc.RequestRefused) as refused:
        run(client, "status_argv", {"extra": 1}, cwd=repo)
    assert refused.value.error == protocol.ERROR_INVALID


def test_run_confines_the_cwd(served, tmp_path):
    core, client, repo, _events = served
    with pytest.raises(inproc.RequestRefused) as refused:
        run(client, "status_argv", cwd=tmp_path / "missing")
    assert refused.value.error == protocol.ERROR_REFUSED
    client.local = False
    with pytest.raises(inproc.RequestRefused) as refused:
        run(client, "status_argv", cwd=repo)
    assert refused.value.error == protocol.ERROR_REFUSED
    # A root the store knows (a session's cwd) admits the directory under it.
    core.store = type("Store", (), {"all_sessions": lambda self: [type("S", (), {"cwd": str(repo)})()]})()
    assert run(client, "status_argv", cwd=repo)["status"] == 0
    sub = repo / "sub"
    sub.mkdir()
    assert run(client, "status_argv", cwd=sub)["status"] == 0


def test_run_is_a_deferred_the_server_settles_later(served):
    """The core answers a run with a Deferred (never a blocking reply);
    the harness pumps until it settles — here inline, so at once."""
    core, client, repo, _events = served
    frame = {"t": "git.run", "id": 7, "cwd": str(repo), "builder": "status_argv"}
    raw = core.handle(protocol.validate(frame, protocol.CLIENT), client)
    assert isinstance(raw, protocol.Deferred) and raw.settled and raw.reply["ok"]


def test_run_never_runs_on_the_main_loop(served):
    """With a real thread the reply settles later, on the dispatcher."""
    core, client, repo, _events = served
    landed: list = []
    core.git = gitfeed.GitFeed(core, dispatch=landed.append)  # the real thread, a captured landing
    frame = {"t": "git.run", "id": 8, "cwd": str(repo), "builder": "status_argv"}
    raw = core.handle(protocol.validate(frame, protocol.CLIENT), client)
    assert isinstance(raw, protocol.Deferred) and not raw.settled
    deadline = time.monotonic() + 5
    while not landed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert landed
    landed[0]()
    assert raw.settled and raw.reply["status"] == 0


def test_run_environment_names(served):
    core, client, repo, _events = served
    core._environment = lambda: {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/")}
    for env in (protocol.GIT_ENV_DEFAULT, protocol.GIT_ENV_NO_EDITOR, protocol.GIT_ENV_NO_PROMPT):
        assert run(client, "status_argv", cwd=repo, env=env)["status"] == 0


# -- git.info -------------------------------------------------------------------------------


def test_info_answers_every_read_at_once(served):
    core, client, repo, _events = served
    reply = client.request({"t": "git.info", "cwd": str(repo), "changes": True, "state": True})
    assert reply["root"] == str(repo) and reply["branch"] == "feat" and reply["default_branch"] == "main"
    assert reply["head"] == git(repo, "rev-parse", "HEAD").strip()
    assert reply["heads"]["main"] == reply["heads"]["feat"] == reply["head"]
    assert reply["remotes"] == [] and reply["markers"] == [] and reply["operation"] is None
    assert reply["changes"] == {"staged": False, "unstaged": True}
    assert reply["state"] == gitops.tree_state_signature(repo)
    info = gitfiles.GitInfo.from_fields(reply)
    assert info.resolve_branch("main") == ("main", reply["head"])
    # The heads stay home when the client holds the same refs digest.
    again = client.request({"t": "git.info", "cwd": str(repo), "known_refs": reply["refs"]})
    assert "heads" not in again and again["refs"] == reply["refs"]
    git(repo, "branch", "other")
    moved = client.request({"t": "git.info", "cwd": str(repo), "known_refs": reply["refs"]})
    assert moved["refs"] != reply["refs"] and "other" in moved["heads"]
    outside = client.request({"t": "git.info", "cwd": str(repo.parent)})
    assert outside["root"] is None


def test_info_names_the_operation(served):
    core, client, repo, _events = served
    (repo / ".git" / "MERGE_HEAD").write_text("a" * 40 + "\n")
    reply = client.request({"t": "git.info", "cwd": str(repo)})
    assert reply["operation"] == "merge" and reply["markers"] == ["MERGE_HEAD"]


def test_sizes(served):
    core, client, repo, _events = served
    reply = client.request({"t": "git.sizes", "cwd": str(repo), "paths": ["new.txt", "gone.txt", "../x"]})
    assert reply["sizes"] == {"new.txt": 6, "gone.txt": None}


# -- git.plan -------------------------------------------------------------------------------


def _hunk_plan(repo: Path, index: int):
    read = gitops.read_diff(repo, "unstaged")
    file = next(f for f in read.files if f.path == "a.txt")
    plan = gitpatch.plan_hunk(file, index, "unstaged", gitops.file_patch(repo, "unstaged", "a.txt"))
    assert isinstance(plan, gitpatch.Plan), plan
    return file, plan


def _plan_message(repo: Path, plan: gitpatch.Plan, keys: list[str], **extra) -> dict:
    return {
        "t": "git.plan",
        "cwd": str(repo),
        "load": "unstaged",
        "path": "a.txt",
        "op": plan.op,
        "paths": list(plan.paths),
        "patch": plan.patch,
        "keys": keys,
        **extra,
    }


def test_plan_applies_with_its_keys_in_the_fresh_patch(served):
    core, client, repo, _events = served
    file, plan = _hunk_plan(repo, 0)
    key = diffmodel.stable_key(file, file.hunks[0])
    reply = client.request(_plan_message(repo, plan, [key]))
    assert reply["applied"] and not reply["conflicts"]
    assert gitops.staged_paths(repo) == ["a.txt"]
    assert "ONE" in git(repo, "diff", "--cached")


def test_plan_is_refused_stale_and_nothing_applied_when_a_key_moved(served):
    core, client, repo, _events = served
    file, plan = _hunk_plan(repo, 0)
    key = diffmodel.stable_key(file, file.hunks[0])
    # Hunk 0 moves under the plan: its key is no longer in the fresh patch.
    (repo / "a.txt").write_text("ONE!\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nTEN\n")
    with pytest.raises(inproc.RequestRefused) as refused:
        client.request(_plan_message(repo, plan, [key]))
    assert refused.value.error == protocol.ERROR_STALE
    assert gitops.staged_paths(repo) == []


def test_plan_refuses_a_malformed_plan(served):
    core, client, repo, _events = served
    _file, plan = _hunk_plan(repo, 0)
    with pytest.raises(inproc.RequestRefused) as refused:
        client.request(_plan_message(repo, plan, [], paths=["../etc/passwd"]))
    assert refused.value.error == protocol.ERROR_INVALID
    with pytest.raises(inproc.RequestRefused) as refused:
        client.request(_plan_message(repo, plan, [], patch=None))
    assert refused.value.error == protocol.ERROR_INVALID


def test_plan_trashes_through_the_services_mover(served, monkeypatch):
    core, client, repo, _events = served
    moved: list = []

    def mover(root, paths):
        moved.append((root, tuple(paths)))
        return gitops.GitResult(True, "", "")

    monkeypatch.setattr(files, "trash_paths", mover)
    plan = gitpatch.Plan(gitpatch.OP_TRASH, ("new.txt",), None, None, "")
    reply = client.request({**_plan_message(repo, plan, []), "path": "new.txt"})
    assert reply["applied"] and moved == [(str(repo), ("new.txt",))]


def test_fs_trash_is_confined_and_uses_gio(served, monkeypatch):
    core, client, repo, _events = served
    taken: list = []
    monkeypatch.setattr(
        files, "trash_absolute", lambda paths: (taken.extend(paths), (list(paths), None))[1]
    )
    reply = client.request({"t": "fs.trash", "paths": [str(repo / "new.txt")]})
    assert reply["trashed"] == [str(repo / "new.txt")] and taken == [str(repo / "new.txt")]
    client.local = False
    with pytest.raises(inproc.RequestRefused) as refused:
        client.request({"t": "fs.trash", "paths": [str(repo / "new.txt")]})
    assert refused.value.error == protocol.ERROR_REFUSED


# -- the watch ------------------------------------------------------------------------------


def pump(seconds: float, until=None) -> bool:
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ctx.iteration(False)
        if until is not None and until():
            return True
        time.sleep(0.005)
    return until() if until is not None else True


def test_watch_fires_on_an_index_write_and_not_on_a_git_basename_event(served):
    core, client, repo, events = served
    state = gitops.tree_state_signature(repo)
    client.request({"t": "git.watch", "cwd": str(repo), "handle": "p1", "files": ["a.txt"], "state": state})
    assert core.git.watching(client, str(repo))
    watch = core.git.watch_of(client, "p1")
    # A `.git` basename event schedules nothing.
    watch._on_event(None, Gio.File.new_for_path(str(repo / ".git")), None, None)
    assert watch._debounce == 0
    # The index moves (git add): the tree state digest moves with it.
    git(repo, "add", "a.txt")
    watch._on_event(None, Gio.File.new_for_path(str(repo / "a.txt")), None, None)
    assert watch._debounce != 0
    assert pump(2.0, lambda: any(e.get("t") == "git-changed" for e in events))
    event = next(e for e in events if e["t"] == "git-changed")
    assert event["cwd"] == str(repo) and event["state"] == gitops.tree_state_signature(repo) != state
    assert event["tree"] and event["refs"]
    # The same state again is no event.
    before = len(events)
    watch.check()
    pump(0.3)
    assert len(events) == before
    client.request({"t": "git.unwatch", "handle": "p1"})
    assert not core.git.watching(client, str(repo))


def test_two_pages_on_one_tree_are_two_watches(served):
    """Watches are the client's per handle, not per cwd: a second page's
    watch of the same tree replaces nothing, and one page's unwatch
    leaves the other's up; the same handle again replaces that one."""
    core, client, repo, _events = served
    client.request({"t": "git.watch", "cwd": str(repo), "handle": "page-a", "files": ["a.txt"]})
    client.request({"t": "git.watch", "cwd": str(repo), "handle": "page-b", "files": []})
    first_a = core.git.watch_of(client, "page-a")
    assert first_a is not None and core.git.watch_of(client, "page-b") is not None
    assert first_a.paths == ["a.txt"]
    client.request({"t": "git.unwatch", "handle": "page-b"})
    assert core.git.watching(client, str(repo)) and core.git.watch_of(client, "page-a") is first_a
    client.request({"t": "git.watch", "cwd": str(repo), "handle": "page-a", "files": ["new.txt"]})
    assert first_a._stopped and core.git.watch_of(client, "page-a").paths == ["new.txt"]
    client.request({"t": "git.unwatch", "handle": "page-a"})
    assert not core.git.watching(client, str(repo))


def test_watch_seeded_by_the_clients_state_reports_an_edit_in_the_window(served):
    """The seed is the client's: a tree that already differs from it at the
    first compare is a move (the edit between the read and the watch)."""
    core, client, repo, events = served
    stale_seed = "0" * 40
    client.request({"t": "git.watch", "cwd": str(repo), "handle": "p1", "files": [], "state": stale_seed})
    assert any(e.get("t") == "git-changed" for e in events)


def test_a_client_going_away_drops_its_watches(served):
    core, client, repo, _events = served
    client.request({"t": "git.watch", "cwd": str(repo), "handle": "p1", "files": []})
    assert core.git.watching(client, str(repo))
    core.client_gone(client)
    assert not core.git.watching(client, str(repo))


def test_a_compare_that_raises_leaves_the_watch_checking_again(served, monkeypatch):
    core, client, repo, _events = served
    client.request({"t": "git.watch", "cwd": str(repo), "handle": "p1", "files": []})
    watch = core.git.watch_of(client, "p1")

    def boom(cwd):
        raise OSError("gone")

    monkeypatch.setattr(gitfeed, "signatures", boom)
    watch.check()
    assert not watch._checking  # landed through the failure: the next event still compares


# -- the builders hold the wire's text to their grammar (D33) -------------------------


@pytest.mark.parametrize(
    ("builder", "args"),
    [
        ("log_argv", {"range_args": ["--output=/x"], "limit": 5}),
        ("log_argv", {"range_args": ["HEAD"], "limit": "5"}),
        ("stack_walk_argv", {"lower": "--output=/x", "upper": "HEAD", "limit": 10}),
        ("stack_walk_argv", {"lower": None, "upper": "--all", "limit": 10}),
        ("rev_parse_argv", {"rev": "--show-toplevel"}),
        ("resolve_commit_argv", {"ref": "--output=/x"}),
        ("merge_base_argv", {"a": "--all", "b": "HEAD"}),
        ("show_argv", {"ref": "--output=/x", "pathspecs": [], "excludes": []}),
        ("show_argv", {"ref": "HEAD", "pathspecs": ["../../etc/passwd"], "excludes": []}),
        ("diff_argv", {"load": "branch", "parent_target": "--output=/x", "untracked": True,
                       "pathspecs": [], "excludes": []}),
        ("diff_argv", {"load": "unstaged", "parent_target": None, "untracked": True,
                       "pathspecs": ["/etc/passwd"], "excludes": []}),
        ("diff_argv", {"load": {"show": "--output=/x"}, "parent_target": None, "untracked": True,
                       "pathspecs": [], "excludes": []}),
        ("numstat_argv", {"load": "branch", "parent_target": "--output=/x", "pathspecs": []}),
        ("numstat_argv", {"load": {"range": "--a...b"}, "parent_target": None, "pathspecs": []}),
        ("untracked_diff_argv", {"path": "/etc/passwd"}),
        ("untracked_diff_argv", {"path": "../outside.txt"}),
        ("conflict_diff_argv", {"paths": ["../x"]}),
        ("file_at_argv", {"ref": "--output=/x", "path": "a.txt"}),
        ("file_at_argv", {"ref": "HEAD", "path": "../a.txt"}),
        ("unmerged_stages_argv", {"path": "/abs"}),
        ("checkout_side_argv", {"side": "ours", "paths": ["../x"]}),
        ("checkout_paths_argv", {"paths": ["/x"]}),
        ("add_paths_argv", {"paths": ["a\nb"]}),
        ("reset_paths_argv", {"paths": ["..", "a"]}),
        ("remove_paths_argv", {"paths": [""]}),
        ("fixup_argv", {"sha": "--amend"}),
        ("revert_argv", {"sha": "--continue", "commit": True}),
        ("commit_subject_argv", {"ref": "--output=/x"}),
        ("commit_message_argv", {"ref": "a b"}),
        ("checkout_branch_argv", {"branch": "--orphan"}),
        ("continue_argv", {"kind": "gc"}),
        ("abort_argv", {"kind": "--all"}),
    ],
)
def test_an_option_shaped_argument_is_refused_invalid_by_every_builder(served, tmp_path, builder, args):
    """The service runs what its own builder makes of a client's args, so
    the builder is where free text is held to the grammar: a revision or
    path that would read as an option (`--output=` writes a file anywhere)
    or walk out of the tree is ValueError in the builder and `invalid` on
    the wire — and nothing was written outside the repository."""
    core, client, repo, _events = served
    with pytest.raises(ValueError):
        gitops.build(builder, args)
    with pytest.raises(inproc.RequestRefused) as refused:
        run(client, builder, args, cwd=repo)
    assert refused.value.error == protocol.ERROR_INVALID
    assert not Path("/x").exists() and not (tmp_path / "x").exists()


def test_a_long_branch_lists_its_commits_through_the_service(served):
    """A 148-char branch (git accepts it) is a revision to the builders:
    `log_argv` over it runs and answers git's exit 0."""
    core, client, repo, _events = served
    long_branch = "feature/" + "x" * 140
    git(repo, "branch", long_branch)
    reply = run(client, "log_argv", {"range_args": [f"{long_branch}..HEAD"], "limit": 5}, cwd=repo)
    assert reply["status"] == 0 and not reply["unreachable"]
    reply = run(client, "rev_parse_argv", {"rev": long_branch}, cwd=repo)
    assert reply["status"] == 0


def test_the_untracked_read_cannot_name_a_file_outside_the_tree(served, tmp_path):
    core, client, repo, _events = served
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("s3cret\n")
    with pytest.raises(inproc.RequestRefused) as refused:
        run(client, "untracked_diff_argv", {"path": str(secret)}, cwd=repo)
    assert refused.value.error == protocol.ERROR_INVALID


def test_a_run_whose_work_raises_still_answers(served, monkeypatch):
    core, client, repo, _events = served

    def boom(*a, **k):
        raise RuntimeError("no")

    monkeypatch.setattr(gitfeed, "run_git_raw", boom)
    with pytest.raises(inproc.RequestRefused) as refused:
        run(client, "status_argv", {}, cwd=repo)
    assert refused.value.error == protocol.ERROR_FAILED


# -- symlinks out of the tree ------------------------------------------------------------


def test_a_symlink_out_of_the_tree_is_neither_read_nor_sized(served, tmp_path):
    core, client, repo, _events = served
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside bytes")
    (repo / "link.txt").symlink_to(outside)
    assert gitfeed.read_blob(str(repo), gitops.AT_WORKTREE, "", "link.txt", None)[0] == 404
    assert gitfeed.blob_tag(str(repo), gitops.AT_WORKTREE, "", "link.txt") is None
    reply = client.request({"t": "git.sizes", "cwd": str(repo), "paths": ["link.txt", "new.txt"]})
    assert reply["sizes"] == {"link.txt": None, "new.txt": 6}


def test_a_blob_over_the_cap_is_413_on_every_side(served, monkeypatch):
    core, client, repo, _events = served
    monkeypatch.setattr(gitops, "MAX_BLOB_BYTES", 4)
    assert gitfeed.read_blob(str(repo), gitops.AT_WORKTREE, "", "a.txt", None)[0] == 413
    assert gitfeed.read_blob(str(repo), gitops.AT_REF, "HEAD", "a.txt", None)[0] == 413
    assert gitfeed.read_blob(str(repo), gitops.AT_INDEX, "", "a.txt", None)[0] == 413
    assert gitfeed.read_blob(str(repo), gitops.AT_REF, "HEAD", "missing.txt", None)[0] == 404


# -- the blob -------------------------------------------------------------------------------


def test_blob_read_tags_304s_and_404s(served):
    core, client, repo, _events = served
    status, headers, body = gitfeed.read_blob(str(repo), gitops.AT_WORKTREE, "", "a.txt", None)
    assert status == 200 and body.startswith(b"ONE\n") and headers["ETag"]
    assert gitfeed.read_blob(str(repo), gitops.AT_WORKTREE, "", "a.txt", headers["ETag"])[0] == 304
    status, headers2, body = gitfeed.read_blob(str(repo), gitops.AT_REF, "HEAD", "a.txt", None)
    assert status == 200 and body.startswith(b"one\n")
    sha = git(repo, "rev-parse", "HEAD").strip()
    assert headers2["ETag"] == f'"{sha}:a.txt"'
    assert gitfeed.read_blob(str(repo), gitops.AT_REF, "HEAD", "a.txt", headers2["ETag"])[0] == 304
    assert gitfeed.read_blob(str(repo), gitops.AT_REF, "HEAD", "new.txt", None)[0] == 404
    status, headers3, body = gitfeed.read_blob(str(repo), gitops.AT_INDEX, "", "a.txt", None)
    assert status == 200 and body.startswith(b"one\n") and ":a.txt" in headers3["ETag"]


def test_blob_route_confines_and_answers_through_the_feed(served):
    core, client, repo, _events = served
    answers: list = []
    core.git.blob(client, f"kind=git&cwd={repo}&at=worktree&path=a.txt", None, lambda *a: answers.append(a))
    assert answers[0][0] == 200
    bad = f"kind=git&cwd={repo}&at=ref&ref=--bad&path=a.txt"
    core.git.blob(client, bad, None, lambda *a: answers.append(a))
    assert answers[1][0] == 400
    client.local = False
    core.git.blob(client, f"kind=git&cwd={repo}&at=worktree&path=a.txt", None, lambda *a: answers.append(a))
    assert answers[2][0] == 403
