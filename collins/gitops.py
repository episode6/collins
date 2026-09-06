# New in the ghackett fork of agent-session-manager (GPL-3.0).
# Portions adapted from sadick254/hunk-commit-log (the log reader) and
# joshedler/hunk-git-lite (the git runner shape) (MIT, © 2026 Sadick,
# © 2026 Josh Edler); see collins/THIRD_PARTY_LICENSES.md.

"""Git, as the git page's native panels run it: one runner, the argv for
every read and mutation, and the runners that turn them into gitmodel's
values.

The commits list reads pages of `git log` (read_page), the `↑` marks
(unpushed_shas), the stack of local branches between the default branch
and HEAD (stack_branches) and the working tree's status (read_status); the
action row stages and
unstages everything (stage_all, unstage_all), commits the index (commit,
commit_fixup) and asks first whether a commit may be made at all
(in_progress_operation, staged_paths).

The native diff view reads whole loads through read_diff — hunk's argv
(diff_argv, show_argv, with the `a/` `b/` prefixes pinned by
DIFF_PREFIX_ARGS so a patch fed back to `git apply` always has them), a
`--numstat -z` pre-pass (numstat_argv) that turns files over diffmodel's
caps into placeholders, untracked files synthesized one `diff --no-index`
at a time (untracked_diff_argv, at most MAX_UNTRACKED_DIFFS) and the
working tree's status in the same call — re-reads one file's patch at
action time (file_patch), fetches a blob for a gap or an image (file_at,
side_ref), and carries gitpatch's plans out (apply_patch with its
`--3way` retry, stage_paths, unstage_paths, checkout_paths). Its watch
compares tree_state_signature. Every runner takes *run*
(subprocess.run by default) and a timeout — gitloads.commit_subject's shape
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

import hashlib
import os
import re
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import diffmodel, gitinfo, gitloads
from .diffmodel import File
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
# How far down from HEAD stack_branches looks for branch tips: a stack is
# a few branches of a few commits each, and the walk runs on every move of
# the tree; a branch that far off its trunk lists its commits all the same,
# under the current group.
MAX_STACK_WALK = 10_000

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

# A whole load — a branch against its parent on a big repository, with
# rename detection — is slower than the panels' reads; git only has to
# come back at all. Applies and the file-grain mutations keep GIT_TIMEOUT_S.
DIFF_TIMEOUT_S = 30.0
# hunk's own `-c` options in front of every diff it reads (its
# DIFF_PREFIX_NORMALIZATION_ARGS), and for the same reason: a patch the
# view re-reads is fed back to `git apply`, and a user's `diff.noprefix` /
# `diff.mnemonicPrefix` / custom prefixes would leave the headers without
# the `a/` and `b/` the apply strips (with `diff.noprefix=true`, `git diff |
# git apply --cached -` fails with "git diff header lacks filename
# information"). `core.quotePath=true` keeps non-ASCII paths in the
# `"\303\251"` form diffmodel's parser unquotes.
DIFF_PREFIX_ARGS: tuple[str, ...] = (
    "-c", "core.quotePath=true",
    "-c", "diff.noprefix=false",
    "-c", "diff.mnemonicPrefix=false",
    "-c", "diff.srcPrefix=a/",
    "-c", "diff.dstPrefix=b/",
)
# What every diff read carries, hunk's `git diff` flags: no external diff
# driver (its output is not a patch), renames paired, no ANSI.
DIFF_ARGS: tuple[str, ...] = ("--no-ext-diff", "--find-renames", "--no-color")
# `git apply` for every patch the view writes: `--recount` trusts the
# lines over the `@@` counts (gitpatch's are exact; the flag costs
# nothing), `--unidiff-zero` turns off apply's rule that a hunk without
# trailing context must sit at the end of the file — the user's
# `diff.context` may be 0, and a partial patch that keeps only a `+` line
# after its demoted context has no trailing context anywhere. `-p1` is
# apply's default, and the patches always carry `a/` `b/`
# (DIFF_PREFIX_ARGS).
APPLY_ARGS: tuple[str, ...] = ("apply", "--recount", "--unidiff-zero")
# How many untracked files read_diff synthesizes a patch for, each a `git
# diff --no-index` of its own: a build directory nobody ignored is
# thousands, and the load runs on every reload. The rest are listed by
# status alone.
MAX_UNTRACKED_DIFFS = 200
# How many working-tree paths tree_state_signature stats (size + mtime) on
# top of the status and numstat: the edits neither of those sees. A stat
# is cheap, but a tree with thousands of changed paths reloads on the
# counts alone.
MAX_STAT_PATHS = 2_000
# The ref file_at_argv names the index by: `:path` is git's spelling.
INDEX_REF = ""
# The most file_at hands back — a blob for a gap's context or an image
# preview; over this is nothing (the caller says "too large").
MAX_BLOB_BYTES = 64 * 1024 * 1024
# What git says on stderr when a `--3way` apply left conflict markers
# (exit status 1, the tree changed all the same).
_WITH_CONFLICTS = "with conflicts"
_DEV_NULL = "/dev/null"
_STATUS_UNTRACKED = "?"


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


def run_git_bytes(
    cwd: str | Path | None,
    argv: Sequence[str],
    stdin: bytes | None = None,
    run=subprocess.run,
    timeout: float = GIT_TIMEOUT_S,
    ok_statuses: Iterable[int] = (0,),
) -> GitResult:
    """run_git in binary mode: *stdin* handed to git as it is and both
    streams captured as bytes, then decoded as UTF-8 with replacement.
    Text mode would fold a CRLF file's `\\r\\n` to `\\n` in the patch (and
    in the patch fed back to `git apply`, which then matches nothing);
    binary mode keeps the bytes, and the decode is one lossy step the
    view can show (a file with non-UTF-8 bytes reads with U+FFFD in it,
    and a partial patch of it is refused by apply's context check — the
    file-grain mutations take no patch and are unaffected). *ok_statuses*
    says which exit statuses count as ok: `diff --no-index` exits 1 when
    the two differ, which is the answer wanted. Never raises."""
    if not cwd:
        return GitResult(False, "", "no working directory")
    try:
        result = run(["git", *argv], cwd=str(cwd), input=stdin, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as err:
        return GitResult(False, "", str(err) or err.__class__.__name__)
    return GitResult(
        getattr(result, "returncode", 1) in tuple(ok_statuses),
        _decode(result.stdout),
        _decode(result.stderr),
    )


def _decode(data: object) -> str:
    if isinstance(data, bytes):
        return data.decode("utf-8", "replace")
    return data if isinstance(data, str) else ""


@dataclass(frozen=True)
class DiffRead:
    """What read_diff came back with: the *files* of the load (diffmodel's,
    placeholders and untracked files included, in path order), the working
    tree's *status* when the load is one of its two sides (None for a
    commit, branch or range, and when `git status` couldn't be asked), and
    whether the diff itself could be read — *ok* False with the reason in
    *error* (git's first stderr line, or why the load was refused) means
    *files* is empty because nothing was read, not because nothing
    changed."""

    files: tuple[File, ...] = ()
    status: Status | None = None
    ok: bool = True
    error: str = ""


@dataclass(frozen=True)
class ApplyResult(GitResult):
    """apply_patch's answer: a GitResult plus whether the `--3way` retry is
    what answered (*three_way*) and whether that retry left conflict
    markers in the tree (*conflicts*: git exits 1 saying "Applied patch to
    'path' with conflicts." — the file and the index are changed, unlike
    every other failure, which changes nothing)."""

    three_way: bool = False
    conflicts: bool = False


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


def branch_tips_argv() -> list[str]:
    """["for-each-ref", "--format=%(objectname) %(refname:short)",
    "refs/heads"]: every local branch with the commit it points at."""
    return ["for-each-ref", "--format=%(objectname) %(refname:short)", "refs/heads"]


def stack_walk_argv(lower: str | None, upper: str, limit: int) -> list[str]:
    """["rev-list", "--topo-order", "-n", limit, "<lower>..<upper>", "--"]:
    the commits *upper* has that *lower* hasn't, children before parents —
    so *upper*'s own commit comes first, and a branch tip nearer *upper*
    comes before one further down. All of *upper* without a *lower*."""
    rev = f"{lower}..{upper}" if lower else upper
    return ["rev-list", "--topo-order", "-n", str(max(1, int(limit))), rev, "--"]


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


def merge_base_argv(a: str, b: str) -> list[str]:
    """["merge-base", a, b]: the commit a symmetric range `a...b` is
    measured from."""
    return ["merge-base", a, b]


# -- argv builders: the diff view ------------------------------------------------------


def literal_pathspec(path: str) -> str:
    """`:(literal)path`: *path* as the one file — or directory, which still
    matches everything under it — it names. Without the magic git reads a
    pathspec as a glob: `foo[1].txt` names foo1.txt too, `a*b.txt` names
    axb.txt, and a `checkout -- foo[1].txt` the user confirmed for one
    file discards the other's changes as well. Every path a caller hands
    the builders after `--` goes through here."""
    return f":(literal){path}"


def _exclude_pathspec(path: str) -> str:
    """`:(exclude,literal)path`: keeps *path* out of a diff; alone after
    `--` it means "everything but"."""
    return f":(exclude,literal){path}"


def _pathspecs(pathspecs: Sequence[str], excludes: Sequence[str] = ()) -> list[str]:
    """The tail after `--`: every pathspec literal, then every exclude."""
    return [*(literal_pathspec(path) for path in pathspecs), *(_exclude_pathspec(path) for path in excludes)]


def _range_args(load: object, parent_target: str | None) -> list[str] | None:
    """The revision part of a `git diff` for *load*: [] for the unstaged
    working tree, ["--staged"] for the index, ["<parent_target>...HEAD"]
    for the branch (None without a parent: there is nothing to measure
    against), ["a...b"] for a range. None for a commit (that is a `show`)
    and anything malformed."""
    if load == "unstaged":
        return []
    if load == "staged":
        return ["--staged"]
    if load == "branch":
        return [f"{parent_target}...HEAD"] if parent_target else None
    if gitloads.is_range(load):
        return [gitloads.range_of(load)]
    return None


def show_argv(ref: str, pathspecs: Sequence[str] = (), excludes: Sequence[str] = ()) -> list[str]:
    """[*DIFF_PREFIX_ARGS, "show", "--format=", "--no-ext-diff",
    "--find-renames", "--no-color", ref, "--", *literal pathspecs,
    *excludes]: one commit's diff with no message, hunk's `hunk show`."""
    return [*DIFF_PREFIX_ARGS, "show", "--format=", *DIFF_ARGS, ref, "--", *_pathspecs(pathspecs, excludes)]


def diff_argv(
    load: object,
    parent_target: str | None = None,
    untracked: bool = True,
    pathspecs: Sequence[str] = (),
    excludes: Sequence[str] = (),
) -> list[str] | None:
    """The whole read of *load* (a gitloads.Loaded), hunk's `hunk diff` /
    `hunk show` argv: [*DIFF_PREFIX_ARGS, "diff", "--no-ext-diff",
    "--find-renames", "--no-color", <nothing | --staged | parent...HEAD |
    a...b>, "--", *pathspecs, *excludes], or show_argv for a commit load.
    Every pathspec goes on as `:(literal)path` (literal_pathspec) and
    every exclude as `:(exclude,literal)path`: the paths came out of a
    diff or a status and name files, never globs. None for a branch load
    without *parent_target*, and for a load that is none of the five.
    *untracked* is carried for symmetry with read_diff and changes
    nothing here: git's diff never lists untracked files — read_diff
    synthesizes them (untracked_diff_argv) when the flag is on."""
    del untracked
    if gitloads.is_show(load):
        return show_argv(gitloads.show_ref(load), pathspecs, excludes)
    revisions = _range_args(load, parent_target)
    if revisions is None:
        return None
    return [*DIFF_PREFIX_ARGS, "diff", *DIFF_ARGS, *revisions, "--", *_pathspecs(pathspecs, excludes)]


def numstat_argv(
    load: object, parent_target: str | None = None, pathspecs: Sequence[str] = ()
) -> list[str] | None:
    """read_diff's pre-pass, the same load as `--numstat -z`: ["diff",
    "--numstat", "-z", *DIFF_ARGS, <revisions>, "--", *literal
    pathspecs], or ["show", "--format=", "--numstat", "-z", *DIFF_ARGS,
    ref, "--", *literal pathspecs] for a commit. No prefix options:
    numstat prints bare paths. None where diff_argv is."""
    if gitloads.is_show(load):
        ref = gitloads.show_ref(load)
        return ["show", "--format=", "--numstat", "-z", *DIFF_ARGS, ref, "--", *_pathspecs(pathspecs)]
    revisions = _range_args(load, parent_target)
    if revisions is None:
        return None
    return ["diff", "--numstat", "-z", *DIFF_ARGS, *revisions, "--", *_pathspecs(pathspecs)]


def untracked_diff_argv(path: str) -> list[str]:
    """[*DIFF_PREFIX_ARGS, "diff", "--no-color", "--no-ext-diff",
    "--no-index", "--", "/dev/null", path]: an untracked file as the
    new-file patch git itself writes — header, mode, `Binary files … differ`
    when it is one. Exits 1 (the two differ), which run_git_bytes is told
    is fine."""
    return [*DIFF_PREFIX_ARGS, "diff", "--no-color", "--no-ext-diff", "--no-index", "--", _DEV_NULL, path]


def file_patch_argv(
    load: object, path: str, previous_path: str | None = None, parent_target: str | None = None
) -> list[str] | None:
    """One file's patch of *load*, re-read at action time so its numbers
    are exact: the extension's readFilePatch — [*DIFF_PREFIX_ARGS, "diff",
    ("--staged",) *DIFF_ARGS, "--", *literal paths] for a working-tree
    side (a rename names both paths so the patch carries the rename
    record), and diff_argv with the paths as pathspecs for a commit,
    branch or range. None where diff_argv is."""
    paths = [previous_path, path] if previous_path and previous_path != path else [path]
    return diff_argv(load, parent_target, pathspecs=paths)


def file_at_argv(ref: str, path: str) -> list[str]:
    """["show", "<ref>:<path>"]: the blob *path* is at *ref* — INDEX_REF
    ("") for the index (`:path`), else a revision (`HEAD:path`,
    `<sha>^:path`, `<merge-base>:path`). *path* is repository-relative, as
    the diff names it (`ref:./path` would be relative to the cwd)."""
    return ["show", f"{ref}:{path}"]


def side_ref(
    load: object, side: str, parent_target: str | None = None, merge_base: str | None = None
) -> str | None:
    """Where one *side* (diffmodel.OLD / NEW) of *load* reads a whole file
    from, for file_at: INDEX_REF for the index, a revision for a commit,
    None for the working tree itself (read from disk). The unstaged load
    is index → working tree; the staged load HEAD → index; a commit
    `<ref>^` → `<ref>`; the branch and a range read their old side at the
    *merge_base* the caller resolved (gitops.merge_base) — the parent's or
    the left half's tip when it couldn't be (a later commit on the parent
    then shows in the old text, never in the diff) — and their new side at
    HEAD / the right half. None for the old side too when there is nothing
    to name (a branch load with no parent), which reads as "no such
    side"."""
    old = side == diffmodel.OLD
    if load == "unstaged":
        return INDEX_REF if old else None
    if load == "staged":
        return "HEAD" if old else INDEX_REF
    if gitloads.is_show(load):
        ref = gitloads.show_ref(load)
        return f"{ref}^" if old else ref
    if load == "branch":
        if not parent_target:
            return None
        return (merge_base or parent_target) if old else "HEAD"
    halves = gitloads.range_halves(gitloads.range_of(load))
    if halves is None:
        return None
    return (merge_base or halves[0]) if old else halves[1]


def side_bytes(
    cwd: str | Path | None,
    load: object,
    side: str,
    path: str,
    previous_path: str | None = None,
    parent_target: str | None = None,
    merge_base: str | None = None,
    run=subprocess.run,
    timeout: float = DIFF_TIMEOUT_S,
) -> bytes | None:
    """The whole file on one *side* of *load* — the diff view's context
    reader (DiffView.load's `context_reader(file, side)`), for a gap's
    lines and an image's before/after: side_ref names where the side reads
    from, file_at reads it. The old side of a rename is read under
    *previous_path*. None where side_ref names nothing (a branch load with
    no *parent_target*, a foreign range) — except the unstaged load's new
    side, which is the working tree itself — and for whatever file_at
    can't read (no such file on that side, too big, unsafe)."""
    ref = side_ref(load, side, parent_target, merge_base)
    if ref is None and not (load == "unstaged" and side == diffmodel.NEW):
        return None
    where = previous_path if side == diffmodel.OLD and previous_path else path
    return file_at(cwd, ref, where, run=run, timeout=timeout)


def apply_argv(cached: bool, reverse: bool, three_way: bool = False) -> list[str]:
    """["apply", "--recount", "--unidiff-zero", ("--cached",) ("--reverse",)
    ("--3way",) "-"]: the patch on stdin, to the index only with *cached*
    (the working tree untouched — a stage, or an unstage in *reverse*),
    else to the working tree with the index not consulted (a discard or a
    revert, both *reverse*). *three_way* is apply_patch's retry: a
    three-way merge from the blobs the patch's `index` lines name, which
    also stages the result (`--3way` implies `--index`) and may leave
    conflict markers."""
    argv = list(APPLY_ARGS)
    if cached:
        argv.append("--cached")
    if reverse:
        argv.append("--reverse")
    if three_way:
        argv.append("--3way")
    argv.append("-")
    return argv


def checkout_paths_argv(paths: Sequence[str]) -> list[str]:
    """["checkout", "-q", "--", *literal paths]: the paths back the way
    the index has them — a whole-file discard, and what restores a file
    deleted in the working tree (a binary too, no patch needed). Literal
    (literal_pathspec) because this one is destructive and confirmed for
    the paths named, not for whatever a glob reading of them matches."""
    return ["checkout", "-q", "--", *_pathspecs(paths)]


def add_paths_argv(paths: Sequence[str]) -> list[str]:
    """["add", "-A", "--", *literal paths]: stage the paths as they are, a
    deletion or an untracked file included; a rename's two paths make one
    `R`."""
    return ["add", "-A", "--", *_pathspecs(paths)]


def reset_paths_argv(paths: Sequence[str]) -> list[str]:
    """["reset", "-q", "--", *literal paths]: the paths' index entries
    back to HEAD's, the working tree untouched."""
    return ["reset", "-q", "--", *_pathspecs(paths)]


def safe_path(path: object) -> bool:
    """Whether *path* can go on a git argv as a repository-relative path
    without being read as something else: a non-empty str within
    diffmodel.MAX_PATH_CHARS, not absolute, not a drive path, no leading
    "-" (an option), no NUL or newline (the NUL-separated readers), no `..`
    component (outside the repository)."""
    if not isinstance(path, str) or not path or len(path) > diffmodel.MAX_PATH_CHARS:
        return False
    if path.startswith(("-", "/")) or re.match(r"^[A-Za-z]:", path):
        return False
    if "\0" in path or "\n" in path or "\r" in path:
        return False
    return ".." not in path.split("/")


def _safe_paths(paths: object) -> tuple[str, ...] | None:
    """*paths* as a tuple when every one is a safe_path and there is at
    least one; None otherwise."""
    if isinstance(paths, str | bytes) or not isinstance(paths, Iterable):
        return None
    listed = tuple(paths)
    if not listed or not all(safe_path(path) for path in listed):
        return None
    return listed


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


def stack_branches(
    cwd: str | Path | None,
    lower: str | None,
    upper: str = "HEAD",
    run=subprocess.run,
    timeout: float = GIT_TIMEOUT_S,
) -> list[BranchRef]:
    """The stack under *upper*: every local branch whose tip is a commit
    *upper* has and *lower* (the default branch's target) hasn't, nearest
    *upper* first — what the commits list groups by, and what names the
    branch the current one stacks on (the first of them). A tip at
    *upper*'s own commit is left out (that is the current branch, or a
    twin of it with no commits of its own), and so is any name
    gitloads.safe_ref refuses (it ends up in an argv). Two git runs: the
    branch tips (branch_tips_argv) and the walk (stack_walk_argv, capped at
    MAX_STACK_WALK commits — a tip further down than that is not seen);
    [] when either couldn't be asked, or the targets aren't safe."""
    if (lower is not None and not gitloads.safe_ref(lower)) or not gitloads.safe_ref(upper):
        return []
    tips = run_git(cwd, branch_tips_argv(), run=run, timeout=timeout)
    if not tips.ok:
        return []
    by_sha: dict[str, list[str]] = {}
    for line in tips.stdout.splitlines():
        sha, _sep, name = line.strip().partition(" ")
        if _FULL_SHA.match(sha) and gitloads.safe_ref(name):
            by_sha.setdefault(sha, []).append(name)
    if not by_sha:
        return []
    walk = run_git(cwd, stack_walk_argv(lower, upper, MAX_STACK_WALK), run=run, timeout=timeout)
    if not walk.ok:
        return []
    shas = [line.strip() for line in walk.stdout.splitlines() if _FULL_SHA.match(line.strip())]
    stack: list[BranchRef] = []
    for sha in shas[1:]:  # the first is upper's own commit
        for name in sorted(by_sha.get(sha, ())):
            stack.append(BranchRef(name, name))
    return stack


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
    if not gitloads.safe_ref(sha):
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
    if not gitloads.safe_ref(sha):
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
    if parent_target is not None and not gitloads.safe_ref(parent_target):
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


# -- runners: the diff view ---------------------------------------------------------


def _root(cwd: str | Path | None) -> str | None:
    """Where the diff view's git runs: the working tree's root
    (gitinfo.repo_root, no subprocess). A diff names its files relative
    to the root whatever directory it ran in, but a pathspec given to
    `diff -- <path>`, `add -- <path>` or `apply` is read against the
    process's cwd — from a subdirectory (an agent that cd'd into
    `packages/foo`) the same paths name nothing, and `apply` silently
    skips paths outside it. *cwd* itself when no root is found (git then
    says "not a git repository")."""
    if not cwd:
        return None
    root = gitinfo.repo_root(cwd)
    return str(root) if root is not None else str(cwd)


def _load_refusal(load: object, parent_target: str | None, pathspecs: Sequence[str]) -> str | None:
    """Why *load* can't be read, or None: not one of the five, a branch
    load without a parent, an unsafe parent or pathspec."""
    if not gitloads.loaded_ok(load):
        return "not a load"
    if load == "branch" and not parent_target:
        return "no parent branch"
    if parent_target is not None and not gitloads.safe_ref(parent_target):
        return "unsafe parent"
    if any(not safe_path(spec) for spec in pathspecs):
        return "unsafe pathspec"
    return None


def _placeholder(path: str, additions: int, deletions: int, untracked: bool = False) -> File:
    """A KIND_TOO_LARGE File for a file the read skipped: no hunks, no
    patch, the numstat's counts (0/0 when it was skipped on size alone)."""
    return File(
        path=path,
        previous_path=None,
        kind=diffmodel.KIND_TOO_LARGE,
        old_mode=None,
        new_mode=None,
        similarity=None,
        untracked=untracked,
        hunks=(),
        additions=additions,
        deletions=deletions,
        patch="",
        patch_hash=hashlib.sha1(b"").hexdigest(),
    )


def _under(path: str, pathspecs: Sequence[str]) -> bool:
    """Whether an untracked *path* is one the *pathspecs* would have
    matched: no pathspecs match everything; a pathspec matches itself and
    everything under it as a directory."""
    if not pathspecs:
        return True
    return any(path == spec or path.startswith(spec.rstrip("/") + "/") for spec in pathspecs)


def _file_size(path: str) -> int | None:
    """st_size of *path*, None when it can't be stat'd (gone since the
    status was read, unreadable)."""
    try:
        return os.stat(path).st_size
    except OSError:
        return None


def _untracked_files(
    root: str, status: Status, pathspecs: Sequence[str], run, timeout: float
) -> list[File]:
    """The synthesized new-file patches for the status' untracked paths
    (the first MAX_UNTRACKED_DIFFS of them, in status order): one
    `diff --no-index -- /dev/null path` each (exit 1 is the answer), a
    file over TOO_LARGE_BYTES on disk skipped for a placeholder without a
    read, one that vanished or that git wouldn't diff dropped."""
    files: list[File] = []
    seen: set[str] = set()
    for row in status.unstaged:
        if row.code != _STATUS_UNTRACKED or row.path in seen or not safe_path(row.path):
            continue
        if not _under(row.path, pathspecs):
            continue
        seen.add(row.path)
        if len(seen) > MAX_UNTRACKED_DIFFS:
            break
        size = _file_size(os.path.join(root, row.path))
        if size is None:
            continue
        if size > diffmodel.TOO_LARGE_BYTES:
            files.append(_placeholder(row.path, 0, 0, untracked=True))
            continue
        result = run_git_bytes(
            root, untracked_diff_argv(row.path), run=run, timeout=timeout, ok_statuses=(0, 1)
        )
        if not result.ok or not result.stdout.startswith("diff --git "):
            continue
        files.extend(diffmodel.parse(result.stdout, untracked=True))
    return files


def read_diff(
    cwd: str | Path | None,
    load: object,
    parent_target: str | None = None,
    untracked: bool = True,
    pathspecs: Sequence[str] = (),
    run=subprocess.run,
    timeout: float = DIFF_TIMEOUT_S,
) -> DiffRead:
    """Everything the diff view shows for *load* (a gitloads.Loaded), in one
    call from the repository root, the way hunk reads a changeset:

    1. numstat_argv — the pre-pass; every path over diffmodel's
       TOO_LARGE_LINES is excluded from the diff (`:(exclude,literal)`) and
       stands in the result as a KIND_TOO_LARGE placeholder carrying its
       counts. hunk's other cap, TOO_LARGE_BYTES, is applied after the
       read, by diffmodel.parse on each stanza's own text: a numstat
       says how many lines moved, not how heavy the patch is, and the
       placeholder comes out the same — only the read is not saved.
    2. diff_argv (or show_argv) — the patch stream, diffmodel.parse'd.
    3. For a working-tree side, status_argv — the status the sidebar's
       letters come from, returned as DiffRead.status; and for the
       unstaged side with *untracked* on, the untracked files' synthesized
       patches (_untracked_files, capped at MAX_UNTRACKED_DIFFS).

    Files come back in path order (git's own, with the placeholders and
    untracked files slotted in). A branch load needs *parent_target*; a
    range load reads its `a...b`; *pathspecs* narrow every step (an
    untracked file is kept when it is one of them or under one). ok=False
    with the reason when the load is refused or git couldn't read the
    diff; a status that couldn't be read is None with the files intact."""
    refusal = _load_refusal(load, parent_target, pathspecs)
    if refusal is not None:
        return DiffRead((), None, False, refusal)
    root = _root(cwd)
    if root is None:
        return DiffRead((), None, False, "no working directory")
    numstat = run_git_bytes(root, numstat_argv(load, parent_target, pathspecs), run=run, timeout=timeout)
    if not numstat.ok:
        return DiffRead((), None, False, first_line(numstat.stderr) or "numstat failed")
    counts = diffmodel.parse_numstat(numstat.stdout)
    skipped = {
        path: (added, deleted)
        for path, (added, deleted) in counts.items()
        if diffmodel.too_large(added, deleted, 0) and safe_path(path)
    }
    argv = diff_argv(load, parent_target, untracked, pathspecs, sorted(skipped))
    result = run_git_bytes(root, argv, run=run, timeout=timeout)
    if not result.ok:
        return DiffRead((), None, False, first_line(result.stderr) or "diff failed")
    files = diffmodel.parse(result.stdout)
    files.extend(_placeholder(path, added, deleted) for path, (added, deleted) in skipped.items())
    status: Status | None = None
    if load in ("unstaged", "staged"):
        status = read_status(root, run=run, timeout=timeout)
        if load == "unstaged" and untracked and status is not None:
            files.extend(_untracked_files(root, status, pathspecs, run, timeout))
    files.sort(key=lambda file: file.path)
    return DiffRead(tuple(files[: diffmodel.MAX_FILES]), status, True, "")


def file_patch(
    cwd: str | Path | None,
    load: object,
    path: str,
    previous_path: str | None = None,
    parent_target: str | None = None,
    run=subprocess.run,
    timeout: float = GIT_TIMEOUT_S,
) -> str | None:
    """One file's patch of *load*, re-read now (file_patch_argv) — what
    gitpatch's planners take as the fresh text: "" when the file has no
    change on that side any more (the planners read it as stale), None
    when git couldn't be asked, the load has no argv, or a path isn't
    safe."""
    if not safe_path(path) or (previous_path is not None and not safe_path(previous_path)):
        return None
    if _load_refusal(load, parent_target, ()) is not None:
        return None
    argv = file_patch_argv(load, path, previous_path, parent_target)
    if argv is None:
        return None
    result = run_git_bytes(_root(cwd), argv, run=run, timeout=timeout)
    return result.stdout if result.ok else None


def file_at(
    cwd: str | Path | None,
    ref: str | None,
    path: str,
    run=subprocess.run,
    timeout: float = DIFF_TIMEOUT_S,
) -> bytes | None:
    """The bytes of *path* at *ref* (side_ref's word): the working tree's
    file read from disk for None, `git show :path` for INDEX_REF, `git
    show <ref>:path` for a revision. None when there is no such file there
    (a new file's old side, a deleted file's new side), the blob is over
    MAX_BLOB_BYTES, the ref or path isn't safe, or git couldn't be
    asked."""
    if not safe_path(path):
        return None
    root = _root(cwd)
    if root is None:
        return None
    if ref is None:
        try:
            full = os.path.join(root, path)
            if os.path.getsize(full) > MAX_BLOB_BYTES:
                return None
            with open(full, "rb") as handle:
                return handle.read(MAX_BLOB_BYTES + 1)[:MAX_BLOB_BYTES]
        except OSError:
            return None
    if ref != INDEX_REF and not gitloads.safe_ref(ref):
        return None
    try:
        result = run(["git", *file_at_argv(ref, path)], cwd=root, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if getattr(result, "returncode", 1) != 0 or not isinstance(result.stdout, bytes):
        return None
    return result.stdout if len(result.stdout) <= MAX_BLOB_BYTES else None


def merge_base(
    cwd: str | Path | None, a: str, b: str, run=subprocess.run, timeout: float = GIT_TIMEOUT_S
) -> str | None:
    """The full sha of `git merge-base a b` — where a symmetric range
    `a...b` starts, which is where its old-side files are read from
    (side_ref). None when either ref isn't safe, they share no history,
    or git couldn't be asked."""
    if not gitloads.safe_ref(a) or not gitloads.safe_ref(b):
        return None
    result = run_git(cwd, merge_base_argv(a, b), run=run, timeout=timeout)
    sha = result.stdout.strip()
    return sha if result.ok and _FULL_SHA.match(sha) else None


def apply_patch(
    cwd: str | Path | None,
    patch: str,
    cached: bool,
    reverse: bool,
    three_way: bool = False,
    run=subprocess.run,
    timeout: float = GIT_TIMEOUT_S,
) -> ApplyResult:
    """`git apply` of *patch* (apply_argv) from the repository root, the
    patch on stdin as UTF-8. With *three_way*, a plain apply that git
    refuses ("patch does not apply": the file's context moved since the
    patch was taken — a revert from an older commit) is retried with
    `--3way`, the result saying so (ApplyResult.three_way) and whether the
    merge left conflict markers (ApplyResult.conflicts: exit 1, the tree
    and the index changed — the one failure here that changes anything).
    The retry only helps a working-tree apply of a patch whose `index`
    lines name blobs the repository has; it is never tried first, because
    `--3way` also stages what it applies. An empty patch is refused
    without a call."""
    if not patch or not isinstance(patch, str):
        return ApplyResult(False, "", "empty patch")
    root = _root(cwd)
    data = patch.encode("utf-8", "replace")
    first = run_git_bytes(root, apply_argv(cached, reverse), stdin=data, run=run, timeout=timeout)
    if first.ok or not three_way:
        return ApplyResult(first.ok, first.stdout, first.stderr)
    argv = apply_argv(cached, reverse, three_way=True)
    retry = run_git_bytes(root, argv, stdin=data, run=run, timeout=timeout)
    conflicts = not retry.ok and _WITH_CONFLICTS in retry.stderr
    return ApplyResult(retry.ok, retry.stdout, retry.stderr, three_way=True, conflicts=conflicts)


def _paths_mutation(
    cwd: str | Path | None, paths: Sequence[str], argv_for, run, timeout: float
) -> GitResult:
    listed = _safe_paths(paths)
    if listed is None:
        return GitResult(False, "", "no safe paths")
    return run_git(_root(cwd), argv_for(listed), run=run, timeout=timeout)


def stage_paths(
    cwd: str | Path | None, paths: Sequence[str], run=subprocess.run, timeout: float = GIT_TIMEOUT_S
) -> GitResult:
    """`git add -A -- <paths>` (add_paths_argv) from the repository root;
    refused without a call when a path isn't safe_path."""
    return _paths_mutation(cwd, paths, add_paths_argv, run, timeout)


def unstage_paths(
    cwd: str | Path | None, paths: Sequence[str], run=subprocess.run, timeout: float = GIT_TIMEOUT_S
) -> GitResult:
    """`git reset -q -- <paths>` (reset_paths_argv) from the repository
    root; refused without a call when a path isn't safe_path."""
    return _paths_mutation(cwd, paths, reset_paths_argv, run, timeout)


def checkout_paths(
    cwd: str | Path | None, paths: Sequence[str], run=subprocess.run, timeout: float = GIT_TIMEOUT_S
) -> GitResult:
    """`git checkout -q -- <paths>` (checkout_paths_argv) from the
    repository root — destructive: the caller has confirmed. Refused
    without a call when a path isn't safe_path."""
    return _paths_mutation(cwd, paths, checkout_paths_argv, run, timeout)


def tree_state_signature(
    cwd: str | Path | None, run=subprocess.run, timeout: float = GIT_TIMEOUT_S
) -> str | None:
    """What the diff view's watch compares after a file monitor fires: one
    hex digest of `git status --porcelain=v2 -z` (which paths changed,
    how, untracked included), `git diff --numstat -z` (how much — a
    second edit to an already-modified file moves the counts, not the
    letter) and, for every path the working-tree side lists (at most
    MAX_STAT_PATHS of them), the file's size and mtime — an edit that
    rewrites a changed line moves neither the letter nor the counts, and
    would otherwise never reload. Unchanged means nothing to reload; the
    index and HEAD moves are gitinfo.tree_signature's, on the tick. None
    when either git read couldn't be made — a None never equals anything,
    so the caller reloads."""
    root = _root(cwd)
    status = run_git_bytes(root, status_argv(), run=run, timeout=timeout)
    if not status.ok:
        return None
    numstat = run_git_bytes(root, numstat_argv("unstaged"), run=run, timeout=timeout)
    if not numstat.ok:
        return None
    digest = hashlib.sha1()
    digest.update(status.stdout.encode("utf-8", "replace"))
    digest.update(b"\0\0")
    digest.update(numstat.stdout.encode("utf-8", "replace"))
    digest.update(b"\0\0")
    for row in parse_status_v2(status.stdout).unstaged[:MAX_STAT_PATHS]:
        try:
            stat = os.stat(os.path.join(str(root), row.path))
        except OSError:
            mark = b"gone"
        else:
            mark = f"{stat.st_size}:{stat.st_mtime_ns}".encode()
        digest.update(row.path.encode("utf-8", "replace") + b"\0" + mark + b"\0")
    return digest.hexdigest()
