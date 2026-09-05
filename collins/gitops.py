# New in the ghackett fork of agent-session-manager (GPL-3.0).
# Portions adapted from sadick254/hunk-commit-log (the log reader) and
# joshedler/hunk-git-lite (the git runner shape) (MIT, © 2026 Sadick,
# © 2026 Josh Edler); see collins/THIRD_PARTY_LICENSES.md.

"""Git, as the git page's native panels run it: one runner, the argv for
every read and mutation, and the runners that turn them into gitmodel's
values.

The commits list reads pages of `git log` (read_page), the `↑` marks
(unpushed_shas), the working tree's status (read_status) and the local
branches for the parent picker (local_branches); the action row stages and
unstages everything (stage_all, unstage_all), commits the index (commit,
commit_fixup) and asks first whether a commit may be made at all
(in_progress_operation, staged_paths). Every runner takes *run*
(subprocess.run by default) and a timeout — hunkctl.commit_subject's shape
— passes *cwd*, captures both streams as text, catches OSError and
SubprocessError, and never raises: a git that is missing, slow or
refuses answers a GitResult that says so, and the caller decides what to
tell the user. The argv builders are separate from the runners so the
unit tests pin them without git (tests/test_gitops.py), and the runners
are exercised against a temp repository when git is on PATH.

Nothing here imports a widget; every runner is meant for the sidebar's
worker threads (blocking work never runs on the main loop). What comes
back is foreign content and is bounded by gitmodel's parsers.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import gitinfo, hunkctl
from .gitmodel import LOG_FORMAT, BranchRef, Commit, Status, parse_log, parse_status_v2
from .i18n import _

# Reads and the two whole-index mutations: milliseconds in any repository
# worth working in; a git slower than this answers "couldn't be asked".
GIT_TIMEOUT_S = 5.0
# A commit runs the user's hooks and maybe a signer, on a worker thread, so
# the deadline only has to catch a git that will never come back (a
# pinentry nobody can see). A test suite in a pre-commit hook fits.
COMMIT_TIMEOUT_S = 600.0

# "Not on any remote": the revision arguments that leave only the commits
# no remote-tracking ref reaches. Not `@{upstream}..HEAD` — after a rebase
# onto a pushed base that range holds the base's own pushed commits, a
# branch pushed without `-u` has no upstream at all, and a branch pushed
# to a second remote is on that remote.
NOT_ON_ANY_REMOTE: tuple[str, ...] = ("--not", "--remotes")

# The markers git leaves in its directory while an operation waits on the
# user, in the order they are checked (a rebase beats a merge beats a
# cherry-pick), and the words the commit gate names them by.
_IN_PROGRESS_MARKERS: tuple[tuple[str, str], ...] = (
    ("rebase-merge", "rebase"),
    ("rebase-apply", "rebase"),
    ("MERGE_HEAD", "merge"),
    ("CHERRY_PICK_HEAD", "cherry-pick"),
    ("REVERT_HEAD", "revert"),
)

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class GitResult:
    """What one git invocation came back with. *ok* is "exit status 0";
    a git that couldn't be run at all is ok=False with the reason in
    *stderr*."""

    ok: bool
    stdout: str
    stderr: str


def first_line(text: str | None) -> str:
    """The first non-blank line of *text*, stripped — what a toast shows of
    git's stderr; "" for nothing."""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def run_git(
    cwd: str | Path | None, argv: Sequence[str], run=subprocess.run, timeout: float = GIT_TIMEOUT_S
) -> GitResult:
    """`git *argv` in *cwd*, both streams captured as text, as a GitResult.
    Never raises: no cwd, no git on PATH, a timeout and any other
    SubprocessError come back ok=False with the exception's text as
    stderr."""
    if not cwd:
        return GitResult(False, "", "no working directory")
    try:
        result = run(["git", *argv], cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as err:
        return GitResult(False, "", str(err) or err.__class__.__name__)
    return GitResult(
        getattr(result, "returncode", 1) == 0, result.stdout or "", result.stderr or ""
    )


# -- argv builders -----------------------------------------------------------------


def log_argv(range_args: Sequence[str], limit: int) -> list[str]:
    """["log", "--no-decorate", LOG_FORMAT, "-n", limit, *range_args, "--"]:
    one page of commits, newest first, for a revision range passed through
    verbatim (`main..HEAD`, `main`, nothing for HEAD); the trailing `--`
    keeps a branch that shares a file's name from being read as a path."""
    return ["log", "--no-decorate", LOG_FORMAT, "-n", str(max(1, int(limit))), *range_args, "--"]


def unpushed_argv() -> list[str]:
    """["rev-list", "HEAD", "--not", "--remotes", "--"]: every commit on HEAD
    that no remote-tracking ref reaches (see NOT_ON_ANY_REMOTE)."""
    return ["rev-list", "HEAD", *NOT_ON_ANY_REMOTE, "--"]


def has_remote_tracking_argv() -> list[str]:
    """["for-each-ref", "--count=1", "--format=%(refname)", "refs/remotes/"]:
    whether any remote-tracking ref exists at all — without one there is
    nothing to be unpushed against, and `HEAD --not --remotes` would walk
    and flag a local-only repository's whole history."""
    return ["for-each-ref", "--count=1", "--format=%(refname)", "refs/remotes/"]


def status_argv() -> list[str]:
    """["--no-optional-locks", "status", "--porcelain=v2", "-z",
    "--untracked-files=all"]: the working tree's status the way
    gitmodel.parse_status_v2 reads it; `--no-optional-locks` (as
    gitinfo's status reads) keeps it off the index lock, so it can't
    collide with the agent's own git in the same repository."""
    return ["--no-optional-locks", "status", "--porcelain=v2", "-z", "--untracked-files=all"]


def local_branches_argv() -> list[str]:
    """["for-each-ref", "--format=%(refname:short)", "--sort=refname",
    "refs/heads"]: every local branch, by name."""
    return ["for-each-ref", "--format=%(refname:short)", "--sort=refname", "refs/heads"]


def staged_paths_argv() -> list[str]:
    """["diff", "--cached", "--name-only", "-z"]: the paths the index differs
    from HEAD in — what a commit now would carry."""
    return ["diff", "--cached", "--name-only", "-z"]


def stage_all_argv() -> list[str]:
    """["add", "-A"]: stage everything, untracked files included."""
    return ["add", "-A"]


def unstage_all_argv() -> list[str]:
    """["reset", "-q"]: the index back to HEAD, the working tree untouched."""
    return ["reset", "-q"]


def commit_argv(summary: str, body: str | None = None) -> list[str]:
    """["commit", "-q", "-m", summary, ("-m", body)]: git joins the two with
    a blank line, and `-m` means no editor is ever opened on a terminal
    nobody watches. An empty body is no body; a summary starting with a
    dash is still the value of -m."""
    argv = ["commit", "-q", "-m", summary]
    if body:
        argv += ["-m", body]
    return argv


def fixup_argv(sha: str) -> list[str]:
    """["commit", "-q", "-m", "fixup! <sha>"]: the index as a fixup of
    *sha* — the full sha rather than `--fixup=`'s copy of the target's
    title (titles repeat, hashes don't; `rebase --autosquash` matches
    either form)."""
    return ["commit", "-q", "-m", f"fixup! {sha}"]


def rev_parse_argv(rev: str) -> list[str]:
    """["rev-parse", "--verify", "--quiet", rev]"""
    return ["rev-parse", "--verify", "--quiet", rev]


# -- runners ---------------------------------------------------------------------


def read_page(
    cwd: str | Path | None,
    range_args: Sequence[str],
    page_size: int,
    pages: int = 1,
    run=subprocess.run,
    timeout: float = GIT_TIMEOUT_S,
) -> tuple[list[Commit], bool]:
    """(commits, more) for the first *pages* pages of *page_size* commits
    in *range_args*: one `git log` asking for one commit past the window
    (the limit+1 trick), so *more* says whether a `load more…` row is
    due without a second call. ([], False) when git couldn't answer."""
    wanted = max(1, int(page_size)) * max(1, int(pages))
    result = run_git(cwd, log_argv(range_args, wanted + 1), run=run, timeout=timeout)
    if not result.ok:
        return [], False
    commits = parse_log(result.stdout)
    return commits[:wanted], len(commits) > wanted


def unpushed_shas(cwd: str | Path | None, run=subprocess.run, timeout: float = GIT_TIMEOUT_S) -> set[str]:
    """The shas on HEAD that no remote-tracking ref has — what the commits
    list marks `↑`. Empty without a remote-tracking ref at all (nothing to
    be unpushed against; see has_remote_tracking_argv), and when git
    couldn't answer."""
    tracking = run_git(cwd, has_remote_tracking_argv(), run=run, timeout=timeout)
    if not tracking.ok or not tracking.stdout.strip():
        return set()
    result = run_git(cwd, unpushed_argv(), run=run, timeout=timeout)
    if not result.ok:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if _FULL_SHA.match(line.strip())}


def read_status(cwd: str | Path | None, run=subprocess.run, timeout: float = GIT_TIMEOUT_S) -> Status | None:
    """The working tree's status (gitmodel.parse_status_v2), None when git
    couldn't report it."""
    result = run_git(cwd, status_argv(), run=run, timeout=timeout)
    return parse_status_v2(result.stdout) if result.ok else None


def local_branches(cwd: str | Path | None, run=subprocess.run, timeout: float = GIT_TIMEOUT_S) -> list[str]:
    """Every local branch, sorted by name — the parent picker's rows. Only
    names hunkctl.safe_ref accepts (a branch is one, but the list ends up
    in an argv); [] when git couldn't answer."""
    result = run_git(cwd, local_branches_argv(), run=run, timeout=timeout)
    if not result.ok:
        return []
    names = [line.strip() for line in result.stdout.splitlines()]
    return [name for name in names if hunkctl.safe_ref(name)]


def staged_paths(cwd: str | Path | None, run=subprocess.run, timeout: float = GIT_TIMEOUT_S) -> list[str]:
    """The paths the index differs from HEAD in, NUL-safe; [] when nothing
    is staged, and when git couldn't answer (the commit gate then says
    "nothing staged", which is the safe refusal)."""
    result = run_git(cwd, staged_paths_argv(), run=run, timeout=timeout)
    if not result.ok:
        return []
    return [path for path in result.stdout.split("\0") if path]


def in_progress_operation(git_dir: str | Path | None) -> str | None:
    """What is half-finished in the repository whose git directory is
    *git_dir* (gitinfo.git_dir: a worktree's own, not the common one) —
    _("rebase"), _("merge"), _("cherry-pick") or _("revert") — or None
    when nothing is, or there is no directory to look in. File checks
    only, no git: a commit made while one of these waits would be that
    operation's next step, not the user's, so the commit buttons ask this
    before they ask for a message."""
    if not git_dir:
        return None
    base = Path(git_dir)
    for marker, name in _IN_PROGRESS_MARKERS:
        try:
            if (base / marker).exists():
                return _(name)
        except OSError:
            continue
    return None


def is_root_commit(
    cwd: str | Path | None, sha: str, run=subprocess.run, timeout: float = GIT_TIMEOUT_S
) -> bool:
    """Whether *sha* has no parent — `rev-parse --verify --quiet <sha>^`
    names nothing — which is when the fixup confirm's command says
    `--root`. True too when git couldn't answer or *sha* isn't safe to
    ask about: the command is named, never run, and `--root` is the
    harmless guess."""
    if not hunkctl.safe_ref(sha):
        return True
    result = run_git(cwd, rev_parse_argv(f"{sha}^"), run=run, timeout=timeout)
    return not (result.ok and _FULL_SHA.match(result.stdout.strip()))


def commit(
    cwd: str | Path | None,
    summary: str,
    body: str | None = None,
    run=subprocess.run,
    timeout: float = COMMIT_TIMEOUT_S,
) -> GitResult:
    """`git commit -q -m <summary> [-m <body>]` (commit_argv), with the
    long timeout: hooks and signing run."""
    return run_git(cwd, commit_argv(summary, body), run=run, timeout=timeout)


def commit_fixup(
    cwd: str | Path | None, sha: str, run=subprocess.run, timeout: float = COMMIT_TIMEOUT_S
) -> GitResult:
    """`git commit -q -m "fixup! <sha>"` (fixup_argv), with the long
    timeout. A *sha* that isn't safe as an argument is refused without a
    call."""
    if not hunkctl.safe_ref(sha):
        return GitResult(False, "", f"not a commit: {sha!r}")
    return run_git(cwd, fixup_argv(sha), run=run, timeout=timeout)


def stage_all(cwd: str | Path | None, run=subprocess.run, timeout: float = GIT_TIMEOUT_S) -> GitResult:
    """`git add -A`."""
    return run_git(cwd, stage_all_argv(), run=run, timeout=timeout)


def unstage_all(cwd: str | Path | None, run=subprocess.run, timeout: float = GIT_TIMEOUT_S) -> GitResult:
    """`git reset -q`."""
    return run_git(cwd, unstage_all_argv(), run=run, timeout=timeout)


def head_abbrev(cwd: str | Path | None, run=subprocess.run, timeout: float = GIT_TIMEOUT_S) -> str | None:
    """HEAD's abbreviated sha (`rev-parse --short HEAD`) for the commit
    toast; None when git couldn't say, or said something that isn't one."""
    result = run_git(cwd, ["rev-parse", "--short", "HEAD"], run=run, timeout=timeout)
    text = result.stdout.strip()
    return text if result.ok and re.fullmatch(r"[0-9a-f]{4,40}", text) else None


def unpushed_in_group(
    cwd: str | Path | None,
    parent_target: str | None,
    limit: int,
    run=subprocess.run,
    timeout: float = GIT_TIMEOUT_S,
) -> list[Commit]:
    """The commits Fix up may fold into, newest first: those of the
    current group (`<parent_target>..HEAD`, or all of HEAD without a
    parent) that no remote-tracking ref reaches — the only ones a fixup
    may target without rewriting what somebody else has. The parent's
    own commits are out whether pushed or not (a fixup for one of those
    belongs on the parent branch); the rest are filtered by
    NOT_ON_ANY_REMOTE, exactly what the list marks `↑`. [] when git
    couldn't answer, or the target isn't safe."""
    if parent_target is not None and not hunkctl.safe_ref(parent_target):
        return []
    head = [f"{parent_target}..HEAD"] if parent_target else ["HEAD"]
    result = run_git(cwd, log_argv([*head, *NOT_ON_ANY_REMOTE], limit), run=run, timeout=timeout)
    return parse_log(result.stdout) if result.ok else []


def resolve_group_branches(
    cwd: str | Path | None, parent_name: str | None, default_name: str | None
) -> tuple[BranchRef | None, BranchRef | None]:
    """(parent, default) as BranchRefs the commits list can be built on,
    through gitinfo.resolve_branch (a local branch by name, else the
    ranked remote's copy — no git process): a parent the tree can't name
    falls back to the default; a default it can't name is None."""
    default = _branch_ref(cwd, default_name)
    parent = _branch_ref(cwd, parent_name) or default
    return parent, default


def _branch_ref(cwd: str | Path | None, name: str | None) -> BranchRef | None:
    resolved = gitinfo.resolve_branch(cwd, name)
    return BranchRef(name, resolved[0]) if resolved else None
