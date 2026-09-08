# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Tests for gitops: the argv the git page's native panels hand git (pinned
without git), the runners against a fake `run` and, when git is on PATH,
against a temp repository — commit, fixup, stage all, the `↑` marks, the
commit gate's in-progress check. Ports of the former collins-git
extension's `bun test` cases (test/git.test.ts,
test/commit.integration.test.ts).

The diff view's half: the diff / show / numstat argv, the apply and
file-grain argv, read_diff against a repository with an untracked file, a
rename, a binary and a too-large file, file_patch, file_at, merge_base,
apply_patch cached / reverse / worktree and its `--3way` retry, the paths
mutations and tree_state_signature."""

import shutil
import subprocess
from pathlib import Path

import pytest

from collins import diffmodel, gitinfo, gitmodel, gitops
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
    assert gitops.branch_tips_argv() == [
        "for-each-ref", "--format=%(objectname) %(refname:short)", "refs/heads",
    ]
    assert gitops.stack_walk_argv("main", "HEAD", 50) == [
        "rev-list", "--topo-order", "-n", "50", "main..HEAD", "--",
    ]
    assert gitops.stack_walk_argv(None, "HEAD", 0) == ["rev-list", "--topo-order", "-n", "1", "HEAD", "--"]
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


SHA_C = "c" * 40
SHA_D = "d" * 40


def test_stack_branches_orders_the_tips_by_the_walk_and_drops_the_head_tip():
    """HEAD at SHA_A over SHA_B over SHA_C (the walk, children first) with
    branches at each: the tip at HEAD is the current branch (or a twin of
    it) and is left out; the rest come nearest first, twins at one commit
    by name; a tip outside the walk (SHA_D: on the trunk) is not in the
    stack; unsafe names are dropped."""
    tips = (
        f"{SHA_A} feat\n{SHA_B} step2\n{SHA_C} step1\n{SHA_C} alt\n{SHA_D} main\n{SHA_B} -odd\n{SHA_B} a b\n"
    )
    run = fake_runner({"for-each-ref": ok(tips), "rev-list": ok(f"{SHA_A}\n{SHA_B}\n{SHA_C}\n")})
    assert gitops.stack_branches("/repo", "main", run=run) == [
        BranchRef("step2", "step2"), BranchRef("alt", "alt", ("step1",)),
    ]
    assert run.calls[0][0][1:] == gitops.branch_tips_argv()
    assert run.calls[1][0][1:] == gitops.stack_walk_argv("main", "HEAD", gitops.MAX_STACK_WALK)


def test_read_stack_merges_twins_at_one_commit_and_lists_the_tips_at_head():
    """Two branches at SHA_C are one BranchRef (the first by name, the
    other its twin); the tips at HEAD's own commit — the current branch
    and a twin — come back sorted as the second half, not in the stack."""
    tips = f"{SHA_A} feat\n{SHA_A} feat-twin\n{SHA_B} step2\n{SHA_C} step1\n{SHA_C} alt\n{SHA_D} main\n"
    run = fake_runner({"for-each-ref": ok(tips), "rev-list": ok(f"{SHA_A}\n{SHA_B}\n{SHA_C}\n")})
    stack, head = gitops.read_stack("/repo", "main", run=run)
    assert stack == [BranchRef("step2", "step2"), BranchRef("alt", "alt", ("step1",))]
    assert head == ["feat", "feat-twin"]
    assert stack[1].label == "alt / step1"
    assert gitops.read_stack("/repo", "main", run=fake_runner({"for-each-ref": failed("x")})) == ([], [])


def test_stack_branches_is_empty_when_git_cant_answer_or_the_targets_are_unsafe():
    assert gitops.stack_branches("/repo", "main", run=fake_runner({"for-each-ref": failed("x")})) == []
    run = fake_runner({"for-each-ref": ok(f"{SHA_B} step\n"), "rev-list": failed("bad revision")})
    assert gitops.stack_branches("/repo", "main", run=run) == []
    # No tips at all: the walk is not even asked for.
    run = fake_runner({"for-each-ref": ok("")})
    assert gitops.stack_branches("/repo", "main", run=run) == [] and len(run.calls) == 1
    # An empty walk (HEAD is on the trunk): nothing.
    run = fake_runner({"for-each-ref": ok(f"{SHA_B} step\n"), "rev-list": ok("")})
    assert gitops.stack_branches("/repo", "main", run=run) == []
    assert gitops.stack_branches("/repo", "-x", run=fake_runner({})) == []
    assert gitops.stack_branches("/repo", "main", "a..b", run=fake_runner({})) == []


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


def test_revert_names_the_mode_and_refuses_an_unsafe_sha():
    run = fake_runner({"revert": ok()})
    assert gitops.revert("/repo", SHA_A, True, run=run).ok
    argv, kwargs = run.calls[0]
    assert argv == ["git", "revert", "--no-edit", SHA_A]
    assert kwargs["timeout"] == gitops.COMMIT_TIMEOUT_S
    assert gitops.revert("/repo", SHA_A, False, run=run).ok
    assert run.calls[1][0] == ["git", "revert", "--no-commit", SHA_A]
    assert run.calls[2][0] == ["git", "revert", "--quit"]  # REVERT_HEAD forgotten, the index kept
    refused = gitops.revert("/repo", "--no-edit", True, run=run)
    assert not refused.ok and len(run.calls) == 3

    # A quit that fails is reported: the reverse change is staged, but the
    # sequencer state stays until the user quits it by hand.
    def revert_answers(argv):
        return failed("could not remove REVERT_HEAD") if "--quit" in argv else ok()

    stuck = gitops.revert("/repo", SHA_A, False, run=fake_runner({"revert": revert_answers}))
    assert not stuck.ok
    assert "git revert --quit" in stuck.stderr and "could not remove REVERT_HEAD" in stuck.stderr


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


def test_in_progress_tells_am_and_the_sequencer_apart(tmp_path):
    assert gitops.in_progress(tmp_path) is None
    (tmp_path / "rebase-apply").mkdir()
    assert gitops.in_progress(tmp_path) == gitops.InProgress("rebase", "rebase")
    (tmp_path / "rebase-apply" / "applying").write_text("")
    assert gitops.in_progress(tmp_path) == gitops.InProgress("am", "git am")
    assert gitops.in_progress_operation(tmp_path) == "git am"
    (tmp_path / "rebase-apply" / "applying").unlink()
    (tmp_path / "rebase-apply").rmdir()
    # A lone sequencer (a multi-commit run between two steps): its todo
    # says whether it picks or reverts; an unreadable one is a cherry-pick.
    sequencer = tmp_path / "sequencer"
    sequencer.mkdir()
    assert gitops.in_progress(tmp_path) == gitops.InProgress("cherry-pick", "cherry-pick")
    (sequencer / "todo").write_text(f"# a comment\nrevert {SHA_A} second\n")
    assert gitops.in_progress(tmp_path) == gitops.InProgress("revert", "revert")
    (sequencer / "todo").write_text(f"pick {SHA_A} second\nrevert {SHA_A} third\n")
    assert gitops.in_progress(tmp_path).kind == "cherry-pick"
    # A CHERRY_PICK_HEAD beside it wins, as the markers are ordered.
    (tmp_path / "REVERT_HEAD").write_text(SHA_A)
    assert gitops.in_progress(tmp_path).kind == "revert"


def test_continue_and_abort_argv_take_only_git_operations():
    assert gitops.continue_argv("rebase") == ["rebase", "--continue"]
    assert gitops.abort_argv("cherry-pick") == ["cherry-pick", "--abort"]
    assert gitops.continue_argv("am") == ["am", "--continue"]
    for bad in ("push", "", "rebase --abort"):
        with pytest.raises(ValueError):
            gitops.continue_argv(bad)
        with pytest.raises(ValueError):
            gitops.abort_argv(bad)
    assert not gitops.continue_operation("/repo", "push", run=fake_runner({})).ok
    assert not gitops.abort_operation("/repo", "push", run=fake_runner({})).ok


def test_no_editor_env_sets_git_editor_to_true():
    env = gitops.no_editor_env({"PATH": "/bin", "GIT_EDITOR": "vim"})
    assert env == {"PATH": "/bin", "GIT_EDITOR": "true"}
    assert gitops.no_editor_env()["GIT_EDITOR"] == "true"


def test_continue_operation_runs_without_an_editor():
    seen = {}

    def run(argv, **kwargs):
        seen["argv"] = argv
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert gitops.continue_operation("/repo", "merge", run=run, env={"HOME": "/h"}).ok
    assert seen["argv"] == ["git", "merge", "--continue"]
    assert seen["env"] == {"HOME": "/h", "GIT_EDITOR": "true"}

    def run_abort(argv, **kwargs):
        seen["abort"] = (argv, "env" in kwargs)
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert gitops.abort_operation("/repo", "rebase", run=run_abort).ok
    assert seen["abort"] == (["git", "rebase", "--abort"], False)


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
    make_repo — plus `base`, a second branch at the same commit."""
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
def test_revert_commits_or_stages_the_reverse_and_a_conflict_leaves_revert_head(repo):
    (repo / "f.txt").write_text("two\n")
    _git(repo, "commit", "-qam", "second")
    second = _git(repo, "rev-parse", "HEAD").strip()

    assert gitops.revert(repo, second, False).ok
    assert (repo / "f.txt").read_text() == "one\n"
    assert gitops.staged_paths(repo) == ["f.txt"]
    assert _git(repo, "log", "-1", "--format=%s") == "second\n"
    assert gitops.in_progress_operation(gitinfo.git_dir(repo)) is None
    _git(repo, "reset", "-q", "--hard", "HEAD")

    assert gitops.revert(repo, second, True).ok
    assert (repo / "f.txt").read_text() == "one\n"
    assert _git(repo, "log", "-1", "--format=%s").startswith("Revert \"second\"")
    assert gitops.staged_paths(repo) == []

    # A conflicting revert stops with REVERT_HEAD behind: the sidebar's
    # words name --continue / --abort off in_progress_operation.
    (repo / "f.txt").write_text("three\n")
    _git(repo, "commit", "-qam", "third")
    conflicted = gitops.revert(repo, second, True)
    assert not conflicted.ok
    assert gitops.in_progress_operation(gitinfo.git_dir(repo)) == "revert"
    status = gitops.read_status(repo)
    assert [row.code for row in status.unstaged] == ["U"]  # what the sidebar's words key on


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
def test_stack_branches_reads_the_stack_off_a_repository(repo):
    """main ← step1 ← step2 ← feat, one commit each: feat's stack is step2
    then step1; step2's is step1; a branch at main's own commit (`base`)
    is on the trunk and never in a stack; a twin of HEAD is left out."""
    assert gitops.stack_branches(repo, "main") == []  # on main itself
    for name in ("step1", "step2", "feat"):
        _git(repo, "checkout", "-qb", name)
        (repo / f"{name}.txt").write_text(f"{name}\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", name)
    _git(repo, "branch", "twin")
    assert gitops.stack_branches(repo, "main") == [BranchRef("step2", "step2"), BranchRef("step1", "step1")]
    assert gitops.stack_branches(repo, "main", "step2") == [BranchRef("step1", "step1")]
    assert gitops.stack_branches(repo, "step1") == [BranchRef("step2", "step2")]
    assert gitops.stack_branches(repo, None)[:2] == [BranchRef("step2", "step2"), BranchRef("step1", "step1")]
    # No floor: the trunk's tips too — base and main sit on one commit, one ref.
    assert BranchRef("base", "base", ("main",)) in gitops.stack_branches(repo, None)
    assert gitops.read_stack(repo, "main")[1] == ["feat", "twin"]  # HEAD's own tips
    _git(repo, "checkout", "-q", "step2")
    assert gitops.stack_branches(repo, "main") == [BranchRef("step1", "step1")]


@needs_git
def test_resolve_group_branches(repo):
    _git(repo, "checkout", "-qb", "feat/x")
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


# == the diff view ====================================================================

PREFIX = list(gitops.DIFF_PREFIX_ARGS)
DIFF = ["--no-ext-diff", "--find-renames", "--no-color"]
SHOW = {"show": SHA_A}
RANGE = {"range": "main...feature"}


def lit(path: str) -> str:
    """The path as the builders put it after `--`: `:(literal)path`."""
    return f":(literal){path}"


def _subcommand(argv: list[str]) -> str:
    """The git subcommand of *argv*: past `git`, the `-c key=value` pairs
    and any option."""
    rest = argv[1:]
    while rest:
        head = rest[0]
        if head == "-c":
            rest = rest[2:]
        elif head.startswith("-"):
            rest = rest[1:]
        else:
            return head
    return ""


class _BytesResult:
    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def bytes_runner(answers: dict):
    """A `run` for the binary-mode runners: records every call, checks the
    call is binary (no `text`), answers from a table keyed by subcommand —
    a callable answer sees (argv, stdin)."""
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        assert argv[0] == "git" and kwargs["capture_output"] and kwargs["cwd"] == "/repo"
        # read_status is the panels' text-mode reader; every patch read is
        # binary, or CRLF would fold.
        assert "text" not in kwargs or _subcommand(argv) == "status", "a diff read must not fold CRLF"
        answer = answers.get(_subcommand(argv))
        if callable(answer):
            answer = answer(argv, kwargs.get("input"))
        if isinstance(answer, _Result):  # read_status runs in text mode
            return answer
        if answer is None:
            return _BytesResult(1, b"", f"no answer for {' '.join(argv)}".encode())
        return answer

    run.calls = calls
    return run


def _calls_of(run, subcommand: str) -> list[list[str]]:
    """The argv (past `git`) of every recorded call of *subcommand*."""
    return [argv[1:] for argv, _kw in run.calls if _subcommand(argv) == subcommand]


# -- argv builders -------------------------------------------------------------------


def test_diff_argv_spells_hunks_read_for_every_load():
    assert gitops.diff_argv("unstaged") == [*PREFIX, "diff", *DIFF, "--"]
    assert gitops.diff_argv("staged") == [*PREFIX, "diff", *DIFF, "--staged", "--"]
    assert gitops.diff_argv("branch", "main") == [*PREFIX, "diff", *DIFF, "main...HEAD", "--"]
    assert gitops.diff_argv(RANGE) == [*PREFIX, "diff", *DIFF, "main...feature", "--"]
    assert gitops.diff_argv(SHOW) == gitops.show_argv(SHA_A)
    assert gitops.show_argv(SHA_A) == [*PREFIX, "show", "--format=", *DIFF, SHA_A, "--"]
    # Pathspecs go on literal — `foo[1].txt` is one file, not a glob over
    # foo1.txt as well — and the excludes after them.
    assert gitops.literal_pathspec("foo[1].txt") == ":(literal)foo[1].txt"
    assert gitops.diff_argv("unstaged", pathspecs=["a.txt", "dir/"]) == [
        *PREFIX, "diff", *DIFF, "--", lit("a.txt"), lit("dir/"),
    ]
    assert gitops.diff_argv("staged", pathspecs=["a*b.txt"], excludes=["big.txt"]) == [
        *PREFIX, "diff", *DIFF, "--staged", "--", lit("a*b.txt"), ":(exclude,literal)big.txt",
    ]
    assert gitops.show_argv(SHA_A, ["f.txt"], ["big.txt"]) == [
        *PREFIX, "show", "--format=", *DIFF, SHA_A, "--", lit("f.txt"), ":(exclude,literal)big.txt",
    ]
    # untracked is read_diff's concern; the argv is the same either way.
    assert gitops.diff_argv("unstaged", untracked=False) == gitops.diff_argv("unstaged", untracked=True)
    # A branch without a parent has nothing to measure against.
    assert gitops.diff_argv("branch", None) is None
    assert gitops.diff_argv("branch", "") is None
    for garbage in ("wat", {"show": "a b"}, {"range": "a..b"}, {"range": "a...b...c"}, None, 3, {}):
        assert gitops.diff_argv(garbage) is None, garbage


def test_numstat_argv_is_the_same_load_without_prefixes():
    assert gitops.numstat_argv("unstaged") == ["diff", "--numstat", "-z", *DIFF, "--"]
    assert gitops.numstat_argv("staged", pathspecs=["x"]) == [
        "diff", "--numstat", "-z", *DIFF, "--staged", "--", lit("x"),
    ]
    assert gitops.numstat_argv(SHOW, pathspecs=["x"]) == [
        "show", "--format=", "--numstat", "-z", *DIFF, SHA_A, "--", lit("x"),
    ]
    assert gitops.numstat_argv("branch", "main") == ["diff", "--numstat", "-z", *DIFF, "main...HEAD", "--"]
    assert gitops.numstat_argv(RANGE) == ["diff", "--numstat", "-z", *DIFF, "main...feature", "--"]
    assert gitops.numstat_argv(SHOW) == ["show", "--format=", "--numstat", "-z", *DIFF, SHA_A, "--"]
    assert gitops.numstat_argv("branch") is None and gitops.numstat_argv("nope") is None


def test_untracked_and_file_patch_and_file_at_argv():
    assert gitops.untracked_diff_argv("n.txt") == [
        *PREFIX, "diff", "--no-color", "--no-ext-diff", "--no-index", "--", "/dev/null", "n.txt",
    ]
    assert gitops.file_patch_argv("unstaged", "f.txt") == [*PREFIX, "diff", *DIFF, "--", lit("f.txt")]
    assert gitops.file_patch_argv("staged", "f.txt") == [
        *PREFIX, "diff", *DIFF, "--staged", "--", lit("f.txt"),
    ]
    # A rename names both paths so the patch carries the rename record;
    # a previous path equal to the path is named once.
    assert gitops.file_patch_argv("staged", "new.txt", "old.txt") == [
        *PREFIX, "diff", *DIFF, "--staged", "--", lit("old.txt"), lit("new.txt"),
    ]
    assert gitops.file_patch_argv("unstaged", "f.txt", "f.txt") == [
        *PREFIX, "diff", *DIFF, "--", lit("f.txt"),
    ]
    assert gitops.file_patch_argv(SHOW, "f.txt") == [
        *PREFIX, "show", "--format=", *DIFF, SHA_A, "--", lit("f.txt"),
    ]
    assert gitops.file_patch_argv("branch", "f.txt", parent_target="main") == [
        *PREFIX, "diff", *DIFF, "main...HEAD", "--", lit("f.txt"),
    ]
    assert gitops.file_patch_argv("branch", "f.txt") is None
    assert gitops.file_at_argv(gitops.INDEX_REF, "dir/f.txt") == ["show", ":dir/f.txt"]
    assert gitops.file_at_argv("HEAD", "f.txt") == ["show", "HEAD:f.txt"]
    assert gitops.file_at_argv(f"{SHA_A}^", "f.txt") == ["show", f"{SHA_A}^:f.txt"]


def test_side_ref_names_where_each_side_of_a_load_reads_a_whole_file_from():
    OLD, NEW = diffmodel.OLD, diffmodel.NEW
    # The working tree: index → disk (None); the index: HEAD → index.
    assert (gitops.side_ref("unstaged", OLD), gitops.side_ref("unstaged", NEW)) == (gitops.INDEX_REF, None)
    assert (gitops.side_ref("staged", OLD), gitops.side_ref("staged", NEW)) == ("HEAD", gitops.INDEX_REF)
    assert (gitops.side_ref(SHOW, OLD), gitops.side_ref(SHOW, NEW)) == (f"{SHA_A}^", SHA_A)
    # A branch and a range read their old side at the merge base the caller
    # resolved, else the parent's / left half's tip.
    assert gitops.side_ref("branch", OLD, "main", SHA_B) == SHA_B
    assert gitops.side_ref("branch", OLD, "main") == "main"
    assert gitops.side_ref("branch", NEW, "main", SHA_B) == "HEAD"
    assert gitops.side_ref("branch", OLD, None) is None and gitops.side_ref("branch", NEW, None) is None
    assert gitops.side_ref(RANGE, OLD, merge_base=SHA_B) == SHA_B
    assert gitops.side_ref(RANGE, OLD) == "main" and gitops.side_ref(RANGE, NEW) == "feature"
    assert gitops.side_ref("nope", OLD) is None and gitops.side_ref({"range": "a..b"}, NEW) is None


def test_apply_and_paths_argv():
    base = ["apply", "--recount", "--unidiff-zero"]
    assert gitops.apply_argv(cached=True, reverse=False) == [*base, "--cached", "-"]
    assert gitops.apply_argv(cached=True, reverse=True) == [*base, "--cached", "--reverse", "-"]
    assert gitops.apply_argv(cached=False, reverse=True) == [*base, "--reverse", "-"]
    assert gitops.apply_argv(cached=False, reverse=False) == [*base, "-"]
    assert gitops.apply_argv(cached=False, reverse=True, three_way=True) == [
        *base, "--reverse", "--3way", "-",
    ]
    # The paths are literal: a confirmed discard of `foo[1].txt` must not
    # also check out foo1.txt.
    assert gitops.checkout_paths_argv(["a", "b[1]"]) == ["checkout", "-q", "--", lit("a"), lit("b[1]")]
    assert gitops.add_paths_argv(["old", "new"]) == ["add", "-A", "--", lit("old"), lit("new")]
    assert gitops.reset_paths_argv(["a"]) == ["reset", "-q", "--", lit("a")]
    assert gitops.merge_base_argv("main", "HEAD") == ["merge-base", "main", "HEAD"]


def test_safe_path():
    for path in ("f.txt", "dir/sub/f.txt", "with space.txt", "é.txt", ".hidden", "a..b", "dir/-dash"):
        assert gitops.safe_path(path), path
    unsafe = ("", "-flag", "/abs", "C:x", "../up", "dir/../up", "a\0b", "a\nb", "a\rb", None, 3, "x" * 513)
    for path in unsafe:
        assert not gitops.safe_path(path), path


# -- runners against a fake run ------------------------------------------------------------


def test_run_git_bytes_keeps_the_bytes_and_hands_stdin_through():
    seen = {}

    def run(argv, **kwargs):
        seen.update(kwargs)
        return _BytesResult(1, b"a\r\nb\xff", b"error: x\n")

    result = gitops.run_git_bytes("/repo", ["apply", "-"], stdin=b"patch\r\n", run=run, ok_statuses=(0, 1))
    assert result == gitops.GitResult(True, "a\r\nb�", "error: x\n")
    assert seen["input"] == b"patch\r\n" and "text" not in seen and seen["timeout"] == gitops.GIT_TIMEOUT_S
    assert not gitops.run_git_bytes("/repo", ["apply", "-"], stdin=b"", run=run).ok
    assert not gitops.run_git_bytes(None, ["apply"], run=run).ok

    def boom(argv, **_kw):
        raise subprocess.TimeoutExpired(argv, 1)

    assert not gitops.run_git_bytes("/repo", ["diff"], run=boom).ok


def test_read_diff_refuses_a_bad_load_without_a_call():
    run = bytes_runner({})
    for load, parent, specs, reason in (
        ("nope", None, (), "not a load"),
        ("branch", None, (), "no parent branch"),
        ("branch", "a b", (), "unsafe parent"),
        ("unstaged", None, ("-x",), "unsafe pathspec"),
    ):
        read = gitops.read_diff("/repo", load, parent, pathspecs=specs, run=run)
        assert read == gitops.DiffRead((), None, False, reason), (load, parent, specs)
    assert run.calls == []
    no_cwd = gitops.read_diff(None, "unstaged", run=run)
    assert no_cwd == gitops.DiffRead((), None, False, "no working directory")


PATCH_F = (
    "diff --git a/f.txt b/f.txt\n"
    "index 5626abf..f719efd 100644\n"
    "--- a/f.txt\n"
    "+++ b/f.txt\n"
    "@@ -1 +1 @@\n"
    "-one\n"
    "+two\n"
)
PATCH_N = (
    "diff --git a/n.txt b/n.txt\n"
    "new file mode 100644\n"
    "index 0000000..3e75765\n"
    "--- /dev/null\n"
    "+++ b/n.txt\n"
    "@@ -0,0 +1 @@\n"
    "+new\n"
)
STATUS_V2 = "1 .M N... 100644 100644 100644 5626abf f719efd f.txt\0? n.txt\0? big.txt\0"


def _fake_working_tree(numstat: bytes = b"1\t1\tf.txt\x0030000\t0\tbig.txt\x00"):
    def diff(argv, _stdin):
        if "--numstat" in argv:
            return _BytesResult(0, numstat)
        if "--no-index" in argv:
            assert argv[-1] in ("n.txt", "big.txt"), argv
            return _BytesResult(1, PATCH_N.encode()) if argv[-1] == "n.txt" else _BytesResult(1, b"")
        return _BytesResult(0, PATCH_F.encode())

    return bytes_runner({"diff": diff, "status": ok(STATUS_V2)})


def test_read_diff_excludes_the_too_large_paths_and_stands_a_placeholder_in(monkeypatch):
    monkeypatch.setattr(gitops, "_file_size", lambda path: 4)
    run = _fake_working_tree()
    read = gitops.read_diff("/repo", "unstaged", run=run)
    assert read.ok and read.error == ""
    assert [(f.path, f.kind, f.untracked) for f in read.files] == [
        ("big.txt", diffmodel.KIND_TOO_LARGE, False),
        ("f.txt", diffmodel.KIND_CHANGE, False),
        ("n.txt", diffmodel.KIND_NEW, True),
    ]
    big = read.files[0]
    assert (big.additions, big.deletions, big.hunks, big.patch) == (30000, 0, (), "")
    assert read.status == Status(
        unstaged=(StatusRow("f.txt", "M"), StatusRow("n.txt", "?"), StatusRow("big.txt", "?")), staged=()
    )
    diffs = _calls_of(run, "diff")
    assert diffs[0] == gitops.numstat_argv("unstaged")
    assert diffs[1] == [*PREFIX, "diff", *DIFF, "--", ":(exclude,literal)big.txt"]
    assert _calls_of(run, "status") == [gitops.status_argv()]
    # The untracked reads, in status order: n.txt synthesized, big.txt
    # (untracked too in this status, and over the line cap) tried and,
    # answering nothing, dropped.
    assert [argv[-1] for argv in diffs[2:]] == ["n.txt", "big.txt"]
    assert all("--no-index" in argv for argv in diffs[2:])


def test_read_diff_without_untracked_and_on_the_other_loads(monkeypatch):
    monkeypatch.setattr(gitops, "_file_size", lambda path: 4)
    run = _fake_working_tree(numstat=b"1\t1\tf.txt\x00")
    read = gitops.read_diff("/repo", "unstaged", untracked=False, run=run)
    assert [f.path for f in read.files] == ["f.txt"] and read.status is not None
    assert all("--no-index" not in argv for argv in _calls_of(run, "diff"))
    # The staged side reads status for the sidebar but synthesizes nothing.
    run = _fake_working_tree(numstat=b"1\t1\tf.txt\x00")
    read = gitops.read_diff("/repo", "staged", run=run)
    assert [f.path for f in read.files] == ["f.txt"] and read.status is not None
    assert all("--no-index" not in argv for argv in _calls_of(run, "diff"))
    assert _calls_of(run, "diff")[1] == [*PREFIX, "diff", *DIFF, "--staged", "--"]
    # A commit, a branch and a range: no status at all.
    def show(argv, _stdin):
        return _BytesResult(0, b"" if "--numstat" in argv else PATCH_F.encode())

    for load, parent in ((SHOW, None), ("branch", "main"), (RANGE, None)):
        run = _fake_working_tree(numstat=b"1\t1\tf.txt\x00")
        chosen = bytes_runner({"show": show}) if load is SHOW else run
        read = gitops.read_diff("/repo", load, parent, run=chosen)
        assert read.ok and [f.path for f in read.files] == ["f.txt"] and read.status is None, load
        assert _calls_of(chosen, "status") == []


def test_read_diff_reports_a_failed_read_and_survives_a_failed_status():
    run = bytes_runner({"diff": lambda argv, _s: _BytesResult(128, b"", b"fatal: not a git repository\n")})
    read = gitops.read_diff("/repo", "unstaged", run=run)
    assert read == gitops.DiffRead((), None, False, "fatal: not a git repository")

    def diff(argv, _stdin):
        return _BytesResult(0, b"1\t1\tf.txt\x00" if "--numstat" in argv else PATCH_F.encode())

    run = bytes_runner({"diff": diff, "status": failed("fatal: index locked")})
    read = gitops.read_diff("/repo", "unstaged", run=run)
    assert read.ok and [f.path for f in read.files] == ["f.txt"] and read.status is None


def test_read_diff_pathspecs_narrow_the_reads_and_the_untracked_files(monkeypatch):
    monkeypatch.setattr(gitops, "_file_size", lambda path: 4)
    run = _fake_working_tree(numstat=b"1\t1\tf.txt\x00")
    read = gitops.read_diff("/repo", "unstaged", pathspecs=["f.txt"], run=run)
    assert [f.path for f in read.files] == ["f.txt"]
    assert _calls_of(run, "diff")[0][-1] == lit("f.txt") and _calls_of(run, "diff")[1][-1] == lit("f.txt")
    # Narrowed to the untracked file: git's diff has nothing, the
    # synthesis keeps only the file the pathspec names (or one under it).
    def narrowed(argv, _stdin):
        if "--no-index" in argv:
            return _BytesResult(1, PATCH_N.encode())
        return _BytesResult(0, b"")

    run = bytes_runner({"diff": narrowed, "status": ok(STATUS_V2)})
    read = gitops.read_diff("/repo", "unstaged", pathspecs=["n.txt"], run=run)
    assert [f.path for f in read.files] == ["n.txt"]
    assert [argv[-1] for argv in _calls_of(run, "diff") if "--no-index" in argv] == ["n.txt"]
    run = bytes_runner({"diff": narrowed, "status": ok("? dir/a.txt\0? dirt.txt\0")})
    read = gitops.read_diff("/repo", "unstaged", pathspecs=["dir"], run=run)
    assert [argv[-1] for argv in _calls_of(run, "diff") if "--no-index" in argv] == ["dir/a.txt"]


def test_apply_patch_retries_with_three_way_only_when_asked_and_reports_conflicts():
    def apply(argv, stdin):
        assert stdin == b"a\r\nb\n"
        if "--3way" in argv:
            return _BytesResult(0, b"", b"Applied patch to 'f.txt' cleanly.\n")
        return _BytesResult(1, b"", b"error: patch failed: f.txt:2\nerror: f.txt: patch does not apply\n")

    run = bytes_runner({"apply": apply})
    plain = gitops.apply_patch("/repo", "a\r\nb\n", cached=False, reverse=True, run=run)
    assert plain == gitops.ApplyResult(
        False, "", "error: patch failed: f.txt:2\nerror: f.txt: patch does not apply\n"
    )
    assert not plain.three_way and not plain.conflicts and len(run.calls) == 1
    retried = gitops.apply_patch("/repo", "a\r\nb\n", cached=False, reverse=True, three_way=True, run=run)
    assert retried.ok and retried.three_way and not retried.conflicts
    assert [argv[1:] for argv, _kw in run.calls[1:]] == [
        gitops.apply_argv(False, True), gitops.apply_argv(False, True, three_way=True),
    ]

    def conflicting(argv, _stdin):
        if "--3way" in argv:
            return _BytesResult(1, b"", b"Applied patch to 'f.txt' with conflicts.\nU f.txt\n")
        return _BytesResult(1, b"", b"error: f.txt: patch does not apply\n")

    run = bytes_runner({"apply": conflicting})
    result = gitops.apply_patch("/repo", "x\n", cached=False, reverse=True, three_way=True, run=run)
    assert (result.ok, result.three_way, result.conflicts) == (False, True, True)
    assert gitops.first_line(result.stderr) == "Applied patch to 'f.txt' with conflicts."
    # A plain success never retries; an empty patch is refused without a call.
    run = bytes_runner({"apply": lambda argv, _s: _BytesResult(0)})
    assert gitops.apply_patch("/repo", "x\n", cached=True, reverse=False, three_way=True, run=run).ok
    assert len(run.calls) == 1
    assert gitops.apply_patch("/repo", "", cached=True, reverse=False, run=run) == gitops.ApplyResult(
        False, "", "empty patch"
    )
    assert len(run.calls) == 1


def test_paths_mutations_refuse_an_unsafe_path_without_a_call():
    run = fake_runner({"add": ok(), "reset": ok(), "checkout": ok()})
    assert gitops.stage_paths("/repo", ["a.txt", "b/c.txt"], run=run).ok
    assert gitops.unstage_paths("/repo", ["a.txt"], run=run).ok
    assert gitops.checkout_paths("/repo", ["a.txt"], run=run).ok
    assert [argv[1:] for argv, _kw in run.calls] == [
        ["add", "-A", "--", lit("a.txt"), lit("b/c.txt")],
        ["reset", "-q", "--", lit("a.txt")],
        ["checkout", "-q", "--", lit("a.txt")],
    ]
    for bad in (["-x"], [], ["../up"], "a.txt", [""], ["ok", "/abs"]):
        assert gitops.stage_paths("/repo", bad, run=run) == gitops.GitResult(False, "", "no safe paths"), bad
        assert not gitops.unstage_paths("/repo", bad, run=run).ok
        assert not gitops.checkout_paths("/repo", bad, run=run).ok
    assert len(run.calls) == 3


def test_file_patch_and_merge_base_against_a_fake():
    run = bytes_runner({"diff": lambda argv, _s: _BytesResult(0, PATCH_F.encode())})
    assert gitops.file_patch("/repo", "unstaged", "f.txt", run=run) == PATCH_F
    assert _calls_of(run, "diff") == [gitops.file_patch_argv("unstaged", "f.txt")]
    assert gitops.file_patch("/repo", "unstaged", "-f", run=run) is None
    assert gitops.file_patch("/repo", "unstaged", "f.txt", "../x", run=run) is None
    assert gitops.file_patch("/repo", "branch", "f.txt", run=run) is None
    assert gitops.file_patch("/repo", "nope", "f.txt", run=run) is None
    assert len(run.calls) == 1
    failing = bytes_runner({})
    assert gitops.file_patch("/repo", "staged", "f.txt", run=failing) is None
    run = fake_runner({"merge-base": ok(f"{SHA_B}\n")})
    assert gitops.merge_base("/repo", "main", "HEAD", run=run) == SHA_B
    assert gitops.merge_base("/repo", "a b", "HEAD", run=run) is None
    assert gitops.merge_base("/repo", "main", "a..b", run=run) is None
    assert gitops.merge_base("/repo", "main", "HEAD", run=fake_runner({"merge-base": ok("nonsense")})) is None
    assert gitops.merge_base("/repo", "main", "HEAD", run=fake_runner({})) is None


def test_tree_state_signature_hashes_status_and_numstat():
    run = bytes_runner({"status": _BytesResult(0, b"1 .M x\x00"), "diff": _BytesResult(0, b"1\t0\tx\x00")})
    first = gitops.tree_state_signature("/repo", run=run)
    assert first and len(first) == 40
    assert _calls_of(run, "status") == [gitops.status_argv()]
    assert _calls_of(run, "diff") == [gitops.numstat_argv("unstaged")]
    moved = bytes_runner({"status": _BytesResult(0, b"1 .M x\x00"), "diff": _BytesResult(0, b"2\t0\tx\x00")})
    assert gitops.tree_state_signature("/repo", run=moved) != first
    status_only = bytes_runner({"status": _BytesResult(0, b"1 .M x\x00")})
    assert gitops.tree_state_signature("/repo", run=status_only) is None
    assert gitops.tree_state_signature("/repo", run=bytes_runner({})) is None


# -- runners against a temp repository ------------------------------------------------------


def _write(root: Path, path: str, content: str | bytes) -> None:
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        (root / path).write_bytes(content.encode())
    else:
        (root / path).write_bytes(content)


def _files(read: gitops.DiffRead) -> list[tuple[str, str, bool]]:
    return [(f.path, f.kind, f.untracked) for f in read.files]


@pytest.fixture
def tree(repo):
    """The repo with every kind the view draws: f.txt changed, big.txt
    (one committed line) rewritten to 20 001 lines, img.bin's bytes
    changed, an untracked n.txt and an untracked binary bin.dat."""
    _write(repo, "big.txt", "x\n")
    _write(repo, "img.bin", b"\x00\x01\x02")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "second")
    _write(repo, "f.txt", "two\n")
    _write(repo, "big.txt", "y\n" * 20_001)
    _write(repo, "img.bin", b"\x00\x02\x03")
    _write(repo, "n.txt", "new\n")
    _write(repo, "bin.dat", b"\x00\xff\x00")
    return repo


@needs_git
def test_read_diff_reads_the_working_tree_with_every_kind(tree):
    read = gitops.read_diff(tree, "unstaged")
    assert read.ok and read.error == ""
    assert _files(read) == [
        ("big.txt", diffmodel.KIND_TOO_LARGE, False),
        ("bin.dat", diffmodel.KIND_BINARY, True),
        ("f.txt", diffmodel.KIND_CHANGE, False),
        ("img.bin", diffmodel.KIND_BINARY, False),
        ("n.txt", diffmodel.KIND_NEW, True),
    ]
    big, bin_dat, f, img, n = read.files
    assert (big.additions, big.deletions, big.patch) == (20_001, 1, "")
    assert bin_dat.new_mode == "100644" and "Binary files /dev/null and b/bin.dat differ" in bin_dat.patch
    assert [line.text for line in f.hunks[0].lines] == ["one", "two"]
    assert n.hunks[0].lines[0].text == "new" and n.patch.startswith("diff --git a/n.txt b/n.txt\n")
    assert img.hunks == ()
    assert read.status == Status(
        unstaged=(
            StatusRow("big.txt", "M"), StatusRow("f.txt", "M"), StatusRow("img.bin", "M"),
            StatusRow("bin.dat", "?"), StatusRow("n.txt", "?"),
        ),
        staged=(),
    )
    # Untracked off: the status still lists them, the files don't.
    without = gitops.read_diff(tree, "unstaged", untracked=False)
    assert [f.path for f in without.files] == ["big.txt", "f.txt", "img.bin"]
    assert without.status == read.status
    # From a subdirectory the paths are still the repository's.
    (tree / "sub").mkdir()
    assert _files(gitops.read_diff(tree / "sub", "unstaged", pathspecs=["f.txt", "n.txt"])) == [
        ("f.txt", diffmodel.KIND_CHANGE, False), ("n.txt", diffmodel.KIND_NEW, True),
    ]


@needs_git
def test_read_diff_reads_a_staged_rename_a_commit_a_branch_and_a_range(tree):
    _git(tree, "checkout", "-q", "--", "f.txt", "big.txt", "img.bin")
    _git(tree, "mv", "f.txt", "g.txt")
    staged = gitops.read_diff(tree, "staged")
    assert _files(staged) == [("g.txt", diffmodel.KIND_RENAME, False)]
    assert (staged.files[0].previous_path, staged.files[0].similarity) == ("f.txt", 100)
    assert staged.status.staged == (StatusRow("g.txt", "R", "f.txt"),)
    assert staged.status.unstaged == (StatusRow("bin.dat", "?"), StatusRow("n.txt", "?"))
    _git(tree, "checkout", "-qb", "feature")
    _git(tree, "commit", "-qm", "rename f to g")
    sha = _git(tree, "rev-parse", "HEAD").strip()
    shown = gitops.read_diff(tree, {"show": sha})
    assert shown.ok and shown.status is None
    assert _files(shown) == [("g.txt", diffmodel.KIND_RENAME, False)]
    assert shown.files[0].hunks == () and shown.files[0].previous_path == "f.txt"
    branch = gitops.read_diff(tree, "branch", "main")
    assert _files(branch) == _files(shown) and branch.status is None
    ranged = gitops.read_diff(tree, {"range": "main...feature"})
    assert _files(ranged) == _files(shown)
    assert not gitops.read_diff(tree, "branch", None).ok
    assert not gitops.read_diff(tree, {"show": "nosuch"}).ok
    assert "nosuch" in gitops.read_diff(tree, {"show": "nosuch"}).error


@needs_git
def test_side_bytes_reads_each_side_of_a_load_the_way_the_view_asks(repo):
    OLD, NEW = diffmodel.OLD, diffmodel.NEW
    # The working tree: index on the old side, the disk on the new.
    _write(repo, "f.txt", "two\n")
    assert gitops.side_bytes(repo, "unstaged", OLD, "f.txt") == b"one\n"
    assert gitops.side_bytes(repo, "unstaged", NEW, "f.txt") == b"two\n"
    _git(repo, "add", "f.txt")
    _write(repo, "f.txt", "three\n")
    assert gitops.side_bytes(repo, "staged", OLD, "f.txt") == b"one\n"
    assert gitops.side_bytes(repo, "staged", NEW, "f.txt") == b"two\n"
    # A rename's old side is read under the previous path; a commit's old
    # side at its parent; a branch with no parent names nothing (not the
    # disk); a range's old side at the merge base the caller resolved.
    _git(repo, "checkout", "-q", "--", "f.txt")
    _git(repo, "checkout", "-qb", "feature")
    _git(repo, "mv", "f.txt", "g.txt")
    _write(repo, "g.txt", "two\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "rename")
    sha = _git(repo, "rev-parse", "HEAD").strip()
    show = {"show": sha}
    assert gitops.side_bytes(repo, show, OLD, "g.txt", previous_path="f.txt") == b"one\n"
    assert gitops.side_bytes(repo, show, NEW, "g.txt", previous_path="f.txt") == b"two\n"
    assert gitops.side_bytes(repo, show, OLD, "g.txt") is None  # no g.txt in the parent
    assert gitops.side_bytes(repo, "branch", OLD, "g.txt", previous_path="f.txt") is None
    base = gitops.merge_base(repo, "main", "HEAD")
    assert gitops.side_bytes(repo, "branch", OLD, "g.txt", "f.txt", "main", base) == b"one\n"
    assert gitops.side_bytes(repo, "branch", NEW, "g.txt", "f.txt", "main", base) == b"two\n"
    assert gitops.side_bytes(repo, {"range": "main...feature"}, OLD, "g.txt", "f.txt") == b"one\n"
    assert gitops.side_bytes(repo, {"range": "a..b"}, NEW, "g.txt") is None
    assert gitops.side_bytes(repo, "unstaged", NEW, "-x") is None


@needs_git
def test_read_diff_keeps_crlf_and_the_patch_applies_back(repo):
    _write(repo, "crlf.txt", "a\r\nb\r\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "crlf")
    _write(repo, "crlf.txt", "a\r\nc\r\n")
    read = gitops.read_diff(repo, "unstaged")
    (file,) = read.files
    assert file.patch.endswith("-b\r\n+c\r\n")
    assert gitops.file_patch(repo, "unstaged", "crlf.txt") == file.patch
    assert gitops.apply_patch(repo, file.patch, cached=True, reverse=False).ok
    assert gitops.read_status(repo) == Status(unstaged=(), staged=(StatusRow("crlf.txt", "M"),))
    assert gitops.file_at(repo, gitops.INDEX_REF, "crlf.txt") == b"a\r\nc\r\n"


@needs_git
def test_file_patch_file_at_and_merge_base(tree):
    patch = gitops.file_patch(tree, "unstaged", "f.txt")
    assert patch.startswith("diff --git a/f.txt b/f.txt\n") and "+two" in patch
    assert gitops.file_patch(tree, "staged", "f.txt") == ""
    assert gitops.file_patch(tree, "unstaged", "nosuch.txt") == ""
    assert gitops.file_at(tree, None, "f.txt") == b"two\n"
    assert gitops.file_at(tree, gitops.INDEX_REF, "f.txt") == b"one\n"
    assert gitops.file_at(tree, "HEAD", "f.txt") == b"one\n"
    assert gitops.file_at(tree, "HEAD", "img.bin") == b"\x00\x01\x02"
    assert gitops.file_at(tree, "HEAD^", "big.txt") is None  # not there yet
    assert gitops.file_at(tree, "HEAD", "n.txt") is None
    assert gitops.file_at(tree, None, "nosuch.txt") is None
    assert gitops.file_at(tree, "nosuch", "f.txt") is None
    assert gitops.file_at(tree, "HEAD", "-f") is None
    assert gitops.file_at(tree, "a b", "f.txt") is None
    _git(tree, "checkout", "-qb", "feature")
    _git(tree, "commit", "-qam", "on feature")
    main_sha = _git(tree, "rev-parse", "main").strip()
    assert gitops.merge_base(tree, "main", "HEAD") == main_sha
    assert gitops.merge_base(tree, "main", "feature") == main_sha
    assert gitops.merge_base(tree, "main", "nosuch") is None


@needs_git
def test_apply_patch_cached_reverse_and_in_the_working_tree(repo):
    _write(repo, "f.txt", "two\n")
    patch = gitops.file_patch(repo, "unstaged", "f.txt")
    assert gitops.apply_patch(repo, patch, cached=True, reverse=False).ok
    assert gitops.read_status(repo) == Status(unstaged=(), staged=(StatusRow("f.txt", "M"),))
    staged = gitops.file_patch(repo, "staged", "f.txt")
    assert gitops.apply_patch(repo, staged, cached=True, reverse=True).ok
    assert gitops.read_status(repo) == Status(unstaged=(StatusRow("f.txt", "M"),), staged=())
    # A discard: the reverse in the working tree, the index untouched.
    assert gitops.apply_patch(repo, patch, cached=False, reverse=True).ok
    assert gitops.read_status(repo) == Status() and (repo / "f.txt").read_text() == "one\n"
    # Nothing to apply any more: apply refuses and nothing changes.
    failed_apply = gitops.apply_patch(repo, patch, cached=False, reverse=True)
    assert not failed_apply.ok and not failed_apply.three_way and "does not apply" in failed_apply.stderr
    # From a subdirectory, the same (apply would otherwise skip the path).
    (repo / "sub").mkdir()
    _write(repo, "f.txt", "two\n")
    assert gitops.apply_patch(repo / "sub", patch, cached=True, reverse=False).ok
    assert gitops.read_status(repo).staged == (StatusRow("f.txt", "M"),)


@needs_git
def test_apply_patch_three_way_retry_reverts_a_hunk_whose_context_moved(repo):
    """A revert of an older commit's change from `show`: a later commit
    edited a context line, so the plain apply fails; `--3way` merges it
    (and stages the result, as `--3way` does). A later commit inside the
    change itself conflicts, and the result says so."""
    _write(repo, "f.txt", "".join(f"{i}\n" for i in range(1, 31)))
    _git(repo, "commit", "-qam", "thirty")
    _write(repo, "f.txt", (repo / "f.txt").read_text().replace("5\n", "five\n"))
    _git(repo, "commit", "-qam", "five")
    sha = _git(repo, "rev-parse", "HEAD").strip()
    (file,) = gitops.read_diff(repo, {"show": sha}).files
    _write(repo, "f.txt", (repo / "f.txt").read_text().replace("8\n", "eight\n"))
    _git(repo, "commit", "-qam", "eight")
    plain = gitops.apply_patch(repo, file.patch, cached=False, reverse=True)
    assert not plain.ok and gitops.read_status(repo) == Status()
    result = gitops.apply_patch(repo, file.patch, cached=False, reverse=True, three_way=True)
    assert (result.ok, result.three_way, result.conflicts) == (True, True, False)
    text = (repo / "f.txt").read_text().splitlines()
    assert text[3:8] == ["4", "5", "6", "7", "eight"]
    assert gitops.read_status(repo) == Status(unstaged=(), staged=(StatusRow("f.txt", "M"),))
    # A conflict: the change itself was edited later.
    _git(repo, "reset", "-q", "--hard")
    _write(repo, "f.txt", (repo / "f.txt").read_text().replace("five\n", "FIVE\n"))
    _git(repo, "commit", "-qam", "FIVE")
    result = gitops.apply_patch(repo, file.patch, cached=False, reverse=True, three_way=True)
    assert (result.ok, result.three_way, result.conflicts) == (False, True, True)
    assert "<<<<<<<" in (repo / "f.txt").read_text()
    assert gitops.first_line(result.stderr) == "Applied patch to 'f.txt' with conflicts."


@needs_git
def test_stage_unstage_and_checkout_paths(tree):
    assert gitops.stage_paths(tree, ["n.txt", "f.txt"]).ok
    assert gitops.read_status(tree).staged == (StatusRow("f.txt", "M"), StatusRow("n.txt", "A"))
    assert gitops.unstage_paths(tree, ["f.txt"]).ok
    status = gitops.read_status(tree)
    assert status.staged == (StatusRow("n.txt", "A"),) and StatusRow("f.txt", "M") in status.unstaged
    assert gitops.checkout_paths(tree, ["f.txt"]).ok
    assert (tree / "f.txt").read_text() == "one\n"
    assert StatusRow("f.txt", "M") not in gitops.read_status(tree).unstaged
    # A rename staged as one R by naming both paths.
    _git(tree, "reset", "-q")
    (tree / "f.txt").rename(tree / "g.txt")
    assert gitops.stage_paths(tree, ["f.txt", "g.txt"]).ok
    assert gitops.read_status(tree).staged == (StatusRow("g.txt", "R", "f.txt"),)
    # A deleted file restored from the index; a path git doesn't know fails
    # the way git says.
    (tree / "big.txt").unlink()
    assert gitops.checkout_paths(tree, ["big.txt"]).ok and (tree / "big.txt").exists()
    missing = gitops.checkout_paths(tree, ["nosuch.txt"])
    assert not missing.ok and "did not match" in missing.stderr
    # From a subdirectory the paths are still the repository's.
    (tree / "sub").mkdir()
    assert gitops.stage_paths(tree / "sub", ["bin.dat"]).ok
    assert StatusRow("bin.dat", "A") in gitops.read_status(tree).staged


@needs_git
def test_run_plan_carries_out_every_op_from_the_root(tree):
    """gitops.run_plan is what the page hands a gitpatch.Plan to: add,
    reset and checkout of the paths, the caller's trash mover, and the
    applies through apply_patch."""
    from collins import gitpatch

    def plan(op: str, paths=("f.txt",), patch=None) -> gitpatch.Plan:
        return gitpatch.Plan(op, tuple(paths), patch, None, "done")

    assert gitops.run_plan(tree / "sub" if (tree / "sub").exists() else tree, plan(gitpatch.OP_ADD)).ok
    assert StatusRow("f.txt", "M") in gitops.read_status(tree).staged
    assert gitops.run_plan(tree, plan(gitpatch.OP_RESET)).ok
    assert StatusRow("f.txt", "M") in gitops.read_status(tree).unstaged
    # The applies: the file's patch staged by hunk, then reversed out of the index.
    patch = gitops.file_patch(tree, "unstaged", "f.txt")
    assert patch
    assert gitops.run_plan(tree, plan(gitpatch.OP_APPLY_CACHED, patch=patch)).ok
    assert StatusRow("f.txt", "M") in gitops.read_status(tree).staged
    cached = gitops.file_patch(tree, "staged", "f.txt")
    assert gitops.run_plan(tree, plan(gitpatch.OP_APPLY_CACHED_REVERSE, patch=cached)).ok
    assert StatusRow("f.txt", "M") not in gitops.read_status(tree).staged
    # A reverse apply into the working tree takes the edit back out.
    assert gitops.run_plan(tree, plan(gitpatch.OP_APPLY_WORKTREE_REVERSE, patch=patch)).ok
    assert (tree / "f.txt").read_text() == "one\n"
    # Applied twice it fails the way git says; nothing is retried without three_way.
    again = gitops.run_plan(tree, plan(gitpatch.OP_APPLY_WORKTREE_REVERSE, patch=patch))
    assert not again.ok and not again.three_way and "does not apply" in again.stderr
    # An empty patch is refused before git is asked.
    assert not gitops.run_plan(tree, plan(gitpatch.OP_APPLY_CACHED, patch="")).ok
    # Checkout puts the index's copy back.
    _write(tree, "f.txt", "three\n")
    assert gitops.run_plan(tree, plan(gitpatch.OP_CHECKOUT)).ok
    assert (tree / "f.txt").read_text() == "one\n"
    # The trash is the caller's mover, handed the root and the safe paths.
    moved: list[tuple[str, tuple[str, ...]]] = []

    def trash(root: str, paths) -> gitops.GitResult:
        moved.append((root, tuple(paths)))
        return gitops.GitResult(True, "", "")

    assert gitops.run_plan(tree, plan(gitpatch.OP_TRASH, paths=("n.txt",)), trash=trash).ok
    assert moved == [(str(tree), ("n.txt",))]
    assert not gitops.run_plan(tree, plan(gitpatch.OP_TRASH, paths=("n.txt",))).ok  # no mover
    assert not gitops.run_plan(tree, plan(gitpatch.OP_TRASH, paths=("../n.txt",)), trash=trash).ok
    assert len(moved) == 1

    def raising(root: str, paths) -> gitops.GitResult:
        raise OSError("read-only")

    refused = gitops.run_plan(tree, plan(gitpatch.OP_TRASH, paths=("n.txt",)), trash=raising)
    assert not refused.ok and refused.stderr == "read-only"
    assert not gitops.run_plan(tree, plan("nonsense")).ok
    assert not gitops.run_plan(tree.parent / "nowhere", plan(gitpatch.OP_ADD)).ok


@needs_git
def test_paths_with_glob_characters_name_one_file_each(repo):
    """`foo[1].txt` beside foo1.txt, `a*b.txt` beside axb.txt: every read
    and mutation names exactly the file it was given — as a glob pathspec
    the bracket form matches foo1.txt too, and a confirmed discard of one
    file wiped the other's changes."""
    for name in ("foo1.txt", "foo[1].txt", "a*b.txt", "axb.txt"):
        _write(repo, name, "one\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "globs")
    for name in ("foo1.txt", "foo[1].txt", "a*b.txt", "axb.txt"):
        _write(repo, name, f"changed {name}\n")
    _write(repo, "new[2].txt", "fresh\n")
    _write(repo, "new2.txt", "fresh\n")
    # Reads: one stanza for the named file, the sibling absent.
    assert _files(gitops.read_diff(repo, "unstaged", pathspecs=["foo[1].txt"])) == [
        ("foo[1].txt", diffmodel.KIND_CHANGE, False),
    ]
    assert _files(gitops.read_diff(repo, "unstaged", pathspecs=["new[2].txt"])) == [
        ("new[2].txt", diffmodel.KIND_NEW, True),
    ]
    patch = gitops.file_patch(repo, "unstaged", "foo[1].txt")
    assert patch.count("diff --git ") == 1 and patch.startswith("diff --git a/foo[1].txt b/foo[1].txt\n")
    assert gitops.file_patch(repo, "unstaged", "a*b.txt").count("diff --git ") == 1
    # Mutations: the sibling is untouched.
    assert gitops.stage_paths(repo, ["a*b.txt"]).ok
    assert gitops.read_status(repo).staged == (StatusRow("a*b.txt", "M"),)
    assert gitops.unstage_paths(repo, ["a*b.txt"]).ok
    assert gitops.read_status(repo).staged == ()
    assert gitops.checkout_paths(repo, ["foo[1].txt"]).ok
    assert (repo / "foo[1].txt").read_text() == "one\n"
    assert (repo / "foo1.txt").read_text() == "changed foo1.txt\n"
    assert StatusRow("foo1.txt", "M") in gitops.read_status(repo).unstaged
    # The too-large exclude stays literal beside a literal include.
    _write(repo, "big[1].txt", "x\n")
    _write(repo, "big1.txt", "x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "bigs")
    _write(repo, "big[1].txt", "y\n" * 20_001)
    _write(repo, "big1.txt", "z\n")
    read = gitops.read_diff(repo, "unstaged", pathspecs=["big[1].txt", "big1.txt"], untracked=False)
    assert _files(read) == [
        ("big1.txt", diffmodel.KIND_CHANGE, False), ("big[1].txt", diffmodel.KIND_TOO_LARGE, False),
    ]


@needs_git
def test_tree_state_signature_moves_with_the_working_tree(repo, tmp_path):
    clean = gitops.tree_state_signature(repo)
    assert clean and gitops.tree_state_signature(repo) == clean
    _write(repo, "f.txt", "two\n")
    edited = gitops.tree_state_signature(repo)
    assert edited != clean
    # A second edit keeps the status letter and moves the numstat.
    _write(repo, "f.txt", "two\nthree\n")
    assert gitops.tree_state_signature(repo) not in (clean, edited)
    # A third edit keeps the letter and the counts (the same number of
    # lines changed): the file's size and mtime move the signature.
    counted = gitops.tree_state_signature(repo)
    _write(repo, "f.txt", "two\nfour!\n")
    assert gitops.tree_state_signature(repo) not in (clean, edited, counted)
    _write(repo, "n.txt", "new\n")
    assert gitops.tree_state_signature(repo) not in (clean, edited, counted)
    outside = tmp_path / "outside"
    outside.mkdir()
    assert gitops.tree_state_signature(outside) is None


@needs_git
def test_continue_and_abort_operation_against_a_stopped_revert_and_cherry_pick(repo):
    (repo / "f.txt").write_text("two\n")
    _git(repo, "commit", "-qam", "second")
    second = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "f.txt").write_text("three\n")
    _git(repo, "commit", "-qam", "third")
    third = _git(repo, "rev-parse", "HEAD").strip()
    git_dir = gitinfo.git_dir(repo)

    # A revert stopped on conflicts: continue is refused while the path
    # is unmerged (git's words), then lands once it is resolved and
    # staged — with no editor, the message git prepared stands.
    assert not gitops.revert(repo, second, True).ok
    assert gitops.in_progress(git_dir) == gitops.InProgress("revert", "revert")
    assert gitmodel.unmerged_count(gitops.read_status(repo)) == 1
    refused = gitops.continue_operation(repo, "revert")
    assert not refused.ok and refused.stderr
    assert gitops.in_progress(git_dir).kind == "revert"
    (repo / "f.txt").write_text("one\n")
    _git(repo, "add", "f.txt")
    assert gitops.continue_operation(repo, "revert").ok
    assert gitops.in_progress(git_dir) is None
    assert _git(repo, "log", "-1", "--format=%s").startswith('Revert "second"')
    assert _git(repo, "status", "--porcelain") == ""

    # A cherry-pick stopped on conflicts, aborted: the tree goes back.
    _git(repo, "checkout", "-q", "-b", "side", "base")
    (repo / "f.txt").write_text("side\n")
    _git(repo, "commit", "-qam", "side")
    pick = subprocess.run(["git", "cherry-pick", third], cwd=repo, capture_output=True, text=True)
    assert pick.returncode != 0
    assert gitops.in_progress(git_dir) == gitops.InProgress("cherry-pick", "cherry-pick")
    assert gitops.abort_operation(repo, "cherry-pick").ok
    assert gitops.in_progress(git_dir) is None
    assert (repo / "f.txt").read_text() == "side\n"
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "log", "-1", "--format=%s") == "side\n"
    # With nothing in progress, both are git's own refusals.
    assert not gitops.abort_operation(repo, "cherry-pick").ok
    assert not gitops.continue_operation(repo, "merge").ok
