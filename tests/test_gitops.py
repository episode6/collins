# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Tests for gitops: the argv the git page's native panels hand git (pinned
without git), the runners against a fake `run` and, when git is on PATH,
against a temp repository — commit, fixup, stage all, the `↑` marks, the
commit gate's in-progress check. Ports of the collins-git extension's
`bun test` cases (test/git.test.ts, test/commit.integration.test.ts)."""

import shutil
import subprocess
from pathlib import Path

import pytest

from collins import gitinfo, gitops
from collins.gitmodel import LOG_FORMAT, BranchRef, Commit, Status, StatusRow

SHA_A = "bdda3818b622d8af5190c55f25c15356d76c7806"
SHA_B = "8a681ae56e3d0a1015c5fcce494db95597a5326b"


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def fake_runner(answers: dict):
    """A `run` that records every call and answers from a table keyed by
    the first non-option argument after `git`."""
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        assert argv[0] == "git"
        assert kwargs["capture_output"] and kwargs["text"] and kwargs["cwd"] == "/repo"
        key = next((arg for arg in argv[1:] if not arg.startswith("-")), "")
        answer = answers.get(key)
        if callable(answer):
            answer = answer(argv)
        return answer if answer is not None else _Result(1, "", f"no answer for {' '.join(argv)}")

    run.calls = calls
    return run


def ok(stdout: str = "") -> _Result:
    return _Result(0, stdout, "")


def failed(stderr: str) -> _Result:
    return _Result(1, "", stderr)


# -- the runner -------------------------------------------------------------------


def test_run_git_wraps_the_result_and_passes_the_cwd_and_timeout():
    run = fake_runner({"status": ok("out")})
    result = gitops.run_git("/repo", ["status"], run=run)
    assert result == gitops.GitResult(True, "out", "")
    argv, kwargs = run.calls[0]
    assert argv == ["git", "status"]
    assert kwargs["timeout"] == gitops.GIT_TIMEOUT_S
    assert gitops.run_git("/repo", ["status"], run=run, timeout=9.0) and run.calls[1][1]["timeout"] == 9.0
    assert gitops.run_git(Path("/repo"), ["status"], run=run).ok


def test_run_git_never_raises():
    def timeout(argv, **_kw):
        raise subprocess.TimeoutExpired(argv, 1)

    def missing(argv, **_kw):
        raise FileNotFoundError("git")

    slow = gitops.run_git("/repo", ["log"], run=timeout)
    assert not slow.ok and "timed out" in slow.stderr
    gone = gitops.run_git("/repo", ["log"], run=missing)
    assert not gone.ok and "git" in gone.stderr
    assert not gitops.run_git(None, ["log"], run=missing).ok
    assert not gitops.run_git("", ["log"], run=missing).ok
    refused = gitops.run_git("/repo", ["log"], run=lambda argv, **_kw: _Result(128, "", "fatal: nope"))
    assert refused == gitops.GitResult(False, "", "fatal: nope")
    assert gitops.run_git("/repo", ["log"], run=lambda argv, **_kw: _Result(0, None, None)) == (
        gitops.GitResult(True, "", "")
    )


def test_first_line():
    assert gitops.first_line("error: bad\nhint: more\n") == "error: bad"
    assert gitops.first_line("\n\n  spaced  \nnext") == "spaced"
    assert gitops.first_line("") == ""
    assert gitops.first_line(None) == ""


# -- builders ----------------------------------------------------------------------


def test_log_argv():
    assert gitops.log_argv(["main..HEAD"], 20) == [
        "log", "--no-decorate", LOG_FORMAT, "-n", "20", "main..HEAD", "--",
    ]
    assert gitops.log_argv([], 5) == ["log", "--no-decorate", LOG_FORMAT, "-n", "5", "--"]
    assert gitops.log_argv(["HEAD", "--not", "--remotes"], 0)[4] == "1"


def test_the_other_read_builders():
    assert gitops.unpushed_argv() == ["rev-list", "HEAD", "--not", "--remotes", "--"]
    assert gitops.has_remote_tracking_argv() == [
        "for-each-ref", "--count=1", "--format=%(refname)", "refs/remotes/",
    ]
    assert gitops.status_argv() == [
        "--no-optional-locks", "status", "--porcelain=v2", "-z", "--untracked-files=all",
    ]
    assert gitops.local_branches_argv() == [
        "for-each-ref", "--format=%(refname:short)", "--sort=refname", "refs/heads",
    ]
    assert gitops.staged_paths_argv() == ["diff", "--cached", "--name-only", "-z"]
    assert gitops.rev_parse_argv("HEAD^") == ["rev-parse", "--verify", "--quiet", "HEAD^"]


def test_mutation_builders():
    assert gitops.stage_all_argv() == ["add", "-A"]
    assert gitops.unstage_all_argv() == ["reset", "-q"]
    assert gitops.commit_argv("A summary") == ["commit", "-q", "-m", "A summary"]
    assert gitops.commit_argv("A summary", "A body\nof two lines.") == [
        "commit", "-q", "-m", "A summary", "-m", "A body\nof two lines.",
    ]
    assert gitops.commit_argv("A summary", "") == ["commit", "-q", "-m", "A summary"]
    assert gitops.commit_argv("A summary", None) == ["commit", "-q", "-m", "A summary"]
    # A summary that starts with a dash is still the value of -m.
    assert gitops.commit_argv("-x looks like a flag") == ["commit", "-q", "-m", "-x looks like a flag"]
    assert gitops.fixup_argv(SHA_A) == ["commit", "-q", "-m", f"fixup! {SHA_A}"]


# -- runners against a fake run ----------------------------------------------------------


def test_read_page_uses_the_limit_plus_one_trick():
    records = [f"{i:040x}\x00{i:07x}\x00c{i}\x1e\n" for i in range(1, 8)]

    def log(argv):
        limit = int(argv[argv.index("-n") + 1])
        return ok("".join(records[:limit]))

    run = fake_runner({"log": log})
    commits, more = gitops.read_page("/repo", ["main..HEAD"], 3, run=run)
    assert [c.subject for c in commits] == ["c1", "c2", "c3"]
    assert more is True
    assert run.calls[0][0] == ["git", "log", "--no-decorate", LOG_FORMAT, "-n", "4", "main..HEAD", "--"]
    commits, more = gitops.read_page("/repo", ["main..HEAD"], 3, pages=2, run=run)
    assert len(commits) == 6 and more is True
    assert run.calls[1][0][5] == "7"
    commits, more = gitops.read_page("/repo", [], 5, pages=2, run=run)
    assert len(commits) == 7 and more is False
    refused = fake_runner({"log": failed("bad revision")})
    assert gitops.read_page("/repo", ["x"], 5, run=refused) == ([], False)


def test_unpushed_shas_asks_the_same_question_of_every_commit_on_head():
    tracked = {"for-each-ref": ok("refs/remotes/origin/main\n")}
    run = fake_runner({**tracked, "rev-list": ok(f"{SHA_A}\n{SHA_B}\nnot a sha\n")})
    assert gitops.unpushed_shas("/repo", run=run) == {SHA_A, SHA_B}
    assert run.calls[0][0] == ["git", "for-each-ref", "--count=1", "--format=%(refname)", "refs/remotes/"]
    assert run.calls[1][0] == ["git", "rev-list", "HEAD", "--not", "--remotes", "--"]
    assert gitops.unpushed_shas("/repo", run=fake_runner({**tracked, "rev-list": failed("")})) == set()


def test_unpushed_shas_marks_nothing_and_never_walks_without_a_remote_tracking_ref():
    run = fake_runner({"for-each-ref": ok(""), "rev-list": ok(f"{SHA_A}\n")})
    assert gitops.unpushed_shas("/repo", run=run) == set()
    assert [call[0][1] for call in run.calls] == ["for-each-ref"]
    assert gitops.unpushed_shas("/repo", run=fake_runner({"for-each-ref": failed("x")})) == set()


def test_read_status_parses_the_porcelain_and_is_none_when_git_fails():
    text = f"1 .M N... 100644 100644 100644 {'a' * 40} {'a' * 40} a.txt\x00? new.txt\x00"
    run = fake_runner({"status": ok(text)})
    assert gitops.read_status("/repo", run=run) == Status(
        unstaged=(StatusRow("a.txt", "M"), StatusRow("new.txt", "?")), staged=()
    )
    assert run.calls[0][0][1:] == gitops.status_argv()
    assert gitops.read_status("/repo", run=fake_runner({"status": failed("not a git repository")})) is None
    assert gitops.read_status("/repo", run=fake_runner({"status": ok("")})) == Status()


def test_local_branches_one_name_per_line_gated():
    run = fake_runner({"for-each-ref": ok("base\nmain\n\nfeat/x\n-odd\na b\n")})
    assert gitops.local_branches("/repo", run=run) == ["base", "main", "feat/x"]
    assert run.calls[0][0][1:] == gitops.local_branches_argv()
    assert gitops.local_branches("/repo", run=fake_runner({"for-each-ref": failed("x")})) == []


def test_staged_paths_splits_on_nul_and_is_empty_when_git_fails():
    assert gitops.staged_paths("/repo", run=fake_runner({"diff": ok("a.txt\0dir/b c.txt\0")})) == [
        "a.txt", "dir/b c.txt",
    ]
    assert gitops.staged_paths("/repo", run=fake_runner({"diff": ok("")})) == []
    assert gitops.staged_paths("/repo", run=fake_runner({"diff": failed("not a git repository")})) == []


def test_commit_and_fixup_pass_the_long_timeout():
    run = fake_runner({"commit": ok()})
    assert gitops.commit("/repo", "A summary", run=run).ok
    argv, kwargs = run.calls[0]
    assert argv == ["git", "commit", "-q", "-m", "A summary"]
    assert kwargs["timeout"] == gitops.COMMIT_TIMEOUT_S >= 600.0
    gitops.commit("/repo", "A summary", "A body", run=run)
    assert run.calls[1][0] == ["git", "commit", "-q", "-m", "A summary", "-m", "A body"]
    assert gitops.commit_fixup("/repo", SHA_A, run=run).ok
    assert run.calls[2][0] == ["git", "commit", "-q", "-m", f"fixup! {SHA_A}"]
    assert run.calls[2][1]["timeout"] == gitops.COMMIT_TIMEOUT_S
    refused = gitops.commit_fixup("/repo", "-x", run=run)
    assert not refused.ok and len(run.calls) == 3
    slow = gitops.commit("/repo", "x", run=fake_runner({"commit": ok()}), timeout=1.0)
    assert slow.ok


def test_stage_all_and_unstage_all():
    run = fake_runner({"add": ok(), "reset": failed("fatal: no")})
    assert gitops.stage_all("/repo", run=run).ok
    assert run.calls[0][0] == ["git", "add", "-A"]
    result = gitops.unstage_all("/repo", run=run)
    assert result == gitops.GitResult(False, "", "fatal: no")
    assert run.calls[1][0] == ["git", "reset", "-q"]


def test_head_abbrev():
    assert gitops.head_abbrev("/repo", run=fake_runner({"rev-parse": ok("bdda381\n")})) == "bdda381"
    assert gitops.head_abbrev("/repo", run=fake_runner({"rev-parse": ok("nope!\n")})) is None
    assert gitops.head_abbrev("/repo", run=fake_runner({"rev-parse": failed("unborn")})) is None


def test_is_root_commit():
    assert gitops.is_root_commit("/repo", SHA_A, run=fake_runner({"rev-parse": ok(f"{SHA_B}\n")})) is False
    assert gitops.is_root_commit("/repo", SHA_A, run=fake_runner({"rev-parse": failed("")})) is True
    assert gitops.is_root_commit("/repo", SHA_A, run=fake_runner({"rev-parse": ok("garbage")})) is True
    run = fake_runner({"rev-parse": ok(f"{SHA_B}\n")})
    assert gitops.is_root_commit("/repo", "a..b", run=run) is True
    assert run.calls == []


def test_unpushed_in_group_lists_the_groups_commits_on_no_remote_never_upstream():
    record = f"{SHA_A}\x00bdda381\x00ours\x1e\n"
    run = fake_runner({"log": ok(record)})
    assert gitops.unpushed_in_group("/repo", "main", 20, run=run) == [Commit(SHA_A, "bdda381", "ours")]
    assert run.calls[0][0] == [
        "git", "log", "--no-decorate", LOG_FORMAT, "-n", "20", "main..HEAD", "--not", "--remotes", "--",
    ]
    gitops.unpushed_in_group("/repo", None, 5, run=run)
    assert run.calls[1][0][6:] == ["HEAD", "--not", "--remotes", "--"]
    assert gitops.unpushed_in_group("/repo", "main", 5, run=fake_runner({"log": failed("bad")})) == []
    assert gitops.unpushed_in_group("/repo", "a b", 5, run=run) == [] and len(run.calls) == 2


# -- in_progress_operation ------------------------------------------------------------


def test_in_progress_operation_reads_the_markers_in_order(tmp_path):
    assert gitops.in_progress_operation(tmp_path) is None
    assert gitops.in_progress_operation(None) is None
    assert gitops.in_progress_operation("") is None
    assert gitops.in_progress_operation(tmp_path / "missing") is None
    (tmp_path / "CHERRY_PICK_HEAD").write_text(SHA_A)
    assert gitops.in_progress_operation(tmp_path) == "cherry-pick"
    (tmp_path / "MERGE_HEAD").write_text(SHA_A)
    assert gitops.in_progress_operation(tmp_path) == "merge"  # a merge beats a cherry-pick
    (tmp_path / "rebase-apply").mkdir()
    assert gitops.in_progress_operation(tmp_path) == "rebase"
    (tmp_path / "rebase-merge").mkdir()
    assert gitops.in_progress_operation(str(tmp_path)) == "rebase"
    for name in ("rebase-merge", "rebase-apply"):
        (tmp_path / name).rmdir()
    (tmp_path / "MERGE_HEAD").unlink()
    (tmp_path / "CHERRY_PICK_HEAD").unlink()
    (tmp_path / "REVERT_HEAD").write_text(SHA_A)
    assert gitops.in_progress_operation(tmp_path) == "revert"


# -- against a real repository ------------------------------------------------------------

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git isn't on PATH")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def _mark_pushed(repo: Path) -> None:
    """Pretend the checked-out branch was pushed: a remote-tracking ref
    `origin/<branch>` at HEAD and the branch tracking it, so `@{upstream}`
    resolves and `--remotes` sees it — no network, no second repository."""
    branch = _git(repo, "symbolic-ref", "--short", "HEAD").strip()
    _git(repo, "update-ref", f"refs/remotes/origin/{branch}", "HEAD")
    _git(repo, "config", "remote.origin.url", "/nowhere")
    _git(repo, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    _git(repo, "config", f"branch.{branch}.remote", "origin")
    _git(repo, "config", f"branch.{branch}.merge", f"refs/heads/{branch}")


@pytest.fixture
def repo(tmp_path):
    """main at one commit (`first`, f.txt), like scripts/check_git_page.py's
    make_repo — plus `base`, another branch the parent picker can name."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "f.txt").write_text("one\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "first")
    _git(root, "branch", "base")
    return root


@needs_git
def test_staged_paths_lists_what_the_index_differs_from_head_in(repo):
    assert gitops.staged_paths(repo) == []
    (repo / "f.txt").write_text("two\n")
    assert gitops.staged_paths(repo) == []  # the working tree alone does not count
    _git(repo, "add", "f.txt")
    (repo / "dir name").mkdir()
    (repo / "dir name" / "a b.txt").write_text("x\n")
    _git(repo, "add", "-A")
    assert sorted(gitops.staged_paths(repo)) == ["dir name/a b.txt", "f.txt"]


@needs_git
def test_commit_with_a_summary_then_with_a_body(repo):
    (repo / "f.txt").write_text("two\n")
    _git(repo, "add", "f.txt")
    result = gitops.commit(repo, "Second thing")
    assert result.ok and result.stderr == ""
    assert _git(repo, "log", "-1", "--format=%B") == "Second thing\n\n"
    assert gitops.staged_paths(repo) == []
    (repo / "f.txt").write_text("three\n")
    _git(repo, "add", "f.txt")
    assert gitops.commit(repo, "Third thing", "Because the second\nwas not enough.").ok
    assert _git(repo, "log", "-1", "--format=%B") == "Third thing\n\nBecause the second\nwas not enough.\n\n"
    (repo / "f.txt").write_text("four\n")
    _git(repo, "add", "f.txt")
    assert gitops.commit(repo, "Fourth", "").ok
    assert _git(repo, "log", "-1", "--format=%B") == "Fourth\n\n"
    assert gitops.head_abbrev(repo) == _git(repo, "rev-parse", "--short", "HEAD").strip()


@needs_git
def test_commit_with_nothing_staged_fails_the_way_git_says_it_does(repo):
    result = gitops.commit(repo, "Nothing")
    assert not result.ok
    assert "nothing to commit" in result.stdout + result.stderr or "no changes added" in result.stdout


@needs_git
def test_stage_all_and_unstage_all_move_the_index(repo):
    (repo / "f.txt").write_text("two\n")
    (repo / "new.txt").write_text("new\n")
    status = gitops.read_status(repo)
    assert status == Status(unstaged=(StatusRow("f.txt", "M"), StatusRow("new.txt", "?")), staged=())
    assert gitops.stage_all(repo).ok
    assert gitops.read_status(repo) == Status(
        unstaged=(), staged=(StatusRow("f.txt", "M"), StatusRow("new.txt", "A"))
    )
    assert sorted(gitops.staged_paths(repo)) == ["f.txt", "new.txt"]
    assert gitops.unstage_all(repo).ok
    assert gitops.read_status(repo) == status
    assert gitops.staged_paths(repo) == []


@needs_git
def test_read_page_walks_the_log_newest_first(repo):
    for n in (2, 3, 4):
        (repo / "f.txt").write_text(f"{n}\n")
        _git(repo, "commit", "-qam", f"commit {n}")
    commits, more = gitops.read_page(repo, ["HEAD"], 2)
    assert [c.subject for c in commits] == ["commit 4", "commit 3"] and more
    commits, more = gitops.read_page(repo, ["HEAD"], 2, pages=2)
    assert [c.subject for c in commits] == ["commit 4", "commit 3", "commit 2", "first"] and not more
    commits, more = gitops.read_page(repo, ["base..HEAD"], 20)
    assert [c.subject for c in commits] == ["commit 4", "commit 3", "commit 2"] and not more
    assert all(len(c.sha) == 40 and c.sha.startswith(c.abbrev) for c in commits)
    assert gitops.read_page(repo, ["nosuch..HEAD"], 20) == ([], False)


@needs_git
def test_in_progress_operation_names_a_merge_once_merge_head_exists(repo):
    git_dir = gitinfo.git_dir(repo)
    assert git_dir == repo / ".git"
    assert gitops.in_progress_operation(git_dir) is None
    (git_dir / "MERGE_HEAD").write_text(_git(repo, "rev-parse", "HEAD"))
    assert gitops.in_progress_operation(git_dir) == "merge"


@needs_git
def test_unpushed_the_groups_commits_on_no_remote_tracking_ref_not_upstream(repo):
    # No remote at all: nothing is marked, and the fixup list is what the
    # group holds regardless (there is nothing pushed to protect).
    (repo / "f.txt").write_text("two\n")
    _git(repo, "commit", "-qam", "second")
    assert gitops.unpushed_shas(repo) == set()
    assert [c.subject for c in gitops.unpushed_in_group(repo, None, 20)] == ["second", "first"]
    assert [c.subject for c in gitops.unpushed_in_group(repo, None, 1)] == ["second"]
    assert [c.subject for c in gitops.unpushed_in_group(repo, "HEAD~1", 20)] == ["second"]

    # main pushed; feat forks, is pushed, and main moves on (pushed too).
    _mark_pushed(repo)
    assert gitops.unpushed_in_group(repo, None, 20) == []
    _git(repo, "checkout", "-qb", "feat")
    (repo / "f.txt").write_text("feat\n")
    _git(repo, "commit", "-qam", "feat work")
    _mark_pushed(repo)
    _git(repo, "checkout", "-q", "main")
    (repo / "g.txt").write_text("g\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "main moved on (pushed)")
    _mark_pushed(repo)
    _git(repo, "checkout", "-q", "feat")
    _git(repo, "rebase", "-q", "main")
    (repo / "f.txt").write_text("local\n")
    _git(repo, "commit", "-qam", "feat local only")
    # The rebase put main's pushed commit into @{upstream}..HEAD; it is on origin/main, so not here.
    assert _git(repo, "log", "--format=%s", "@{upstream}..HEAD") == (
        "feat local only\nfeat work\nmain moved on (pushed)\n"
    )
    group = gitops.unpushed_in_group(repo, "main", 20)
    assert [c.subject for c in group] == ["feat local only", "feat work"]
    marks = gitops.unpushed_shas(repo)
    assert marks == {c.sha for c in group}
    # Pushed since (a push without -u leaves no upstream; the ref is what counts).
    _git(repo, "update-ref", "refs/remotes/origin/feat", "HEAD")
    assert gitops.unpushed_in_group(repo, "main", 20) == []
    assert gitops.unpushed_shas(repo) == set()


@needs_git
def test_commit_fixup_writes_the_full_sha_and_the_named_autosquash_folds_it_in(repo):
    _mark_pushed(repo)
    (repo / "f.txt").write_text("two\n")
    _git(repo, "commit", "-qam", "second")
    (repo / "g.txt").write_text("g\n")
    _git(repo, "add", "g.txt")
    _git(repo, "commit", "-qm", "third")
    target = next(c for c in gitops.unpushed_in_group(repo, None, 20) if c.subject == "second")
    assert not gitops.is_root_commit(repo, target.sha)
    first = gitops.read_page(repo, ["HEAD"], 10)[0][-1]
    assert first.subject == "first" and gitops.is_root_commit(repo, first.sha)

    (repo / "f.txt").write_text("two, fixed\n")
    _git(repo, "add", "f.txt")
    assert gitops.commit_fixup(repo, target.sha).ok
    assert _git(repo, "log", "-1", "--format=%s") == f"fixup! {target.sha}\n"
    assert gitops.staged_paths(repo) == []
    # The command the confirm names does what it promises — with `-i`, since
    # `--autosquash` alone is ignored by every git before 2.44; the todo
    # editor is a no-op here, as a user saving the pre-arranged todo is.
    command = gitmodel_autosquash(target.abbrev)
    result = subprocess.run(
        ["git", "-c", "sequence.editor=:", *command.split()[1:]],
        cwd=repo, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert _git(repo, "log", "--format=%s", "-3") == "third\nsecond\nfirst\n"
    assert _git(repo, "show", "HEAD~1:f.txt") == "two, fixed\n"


def gitmodel_autosquash(abbrev: str) -> str:
    from collins.gitmodel import autosquash_command

    return autosquash_command(abbrev, False)


@needs_git
def test_local_branches_and_resolve_group_branches(repo):
    assert gitops.local_branches(repo) == ["base", "main"]
    _git(repo, "checkout", "-qb", "feat/x")
    assert gitops.local_branches(repo) == ["base", "feat/x", "main"]
    assert gitops.resolve_group_branches(repo, "base", "main") == (
        BranchRef("base", "base"), BranchRef("main", "main"),
    )
    # A parent the tree can't name falls back to the default; a default it
    # can't name is None.
    main = BranchRef("main", "main")
    assert gitops.resolve_group_branches(repo, "nosuch", "main") == (main, main)
    assert gitops.resolve_group_branches(repo, None, "main")[0] == BranchRef("main", "main")
    assert gitops.resolve_group_branches(repo, "base", "nosuch") == (BranchRef("base", "base"), None)
    assert gitops.resolve_group_branches(repo, None, None) == (None, None)
    assert gitops.resolve_group_branches(repo, "-x", "a..b") == (None, None)
    # A branch only the remote has resolves to the remote's copy.
    _git(repo, "update-ref", "refs/remotes/origin/develop", "HEAD")
    _git(repo, "config", "remote.origin.url", "/nowhere")
    assert gitops.resolve_group_branches(repo, "develop", "main")[0] == BranchRef("develop", "origin/develop")
