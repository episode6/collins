# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Best-effort git repository info: what the footer, the sidebar's menus and
the git page's freshness check ask about a repository, answered without a
`git` process.

Finding the branch (`current_branch`), the branch the repository treats as
its trunk (`default_branch`) or the repository's page on GitHub
(`github_url`) is a couple of stat calls and one small file read — cheap
enough for the tab footer's 2s poll, and for a context menu that asks on
every right-click. Asking whether the tree is dirty (`has_changes`,
`change_summary`) or which entries are ignored (`ignored_names`) can't be
answered that way, so those run git — through gitops' runner (`run_git`,
`run_git_bytes`), like every other git call — and are only ever asked on
demand.

**Where the reads run** (split-service spec §3.23, PR-2.1). The `.git`
reads themselves are `gitfiles` (stdlib only), which the service runs: the
client's copy of this module answers from a per-cwd **mirror** of the
service's `git.info` reply (`remotegit` installs it with `set_reader`),
refreshed by the page's tick (`refresh`), aged out after `MAX_AGE_S` on any
other read and refreshed by the service's `git-changed` events. With no
reader set (the service's own code, the unit tests over a temp repository)
every function reads the files itself, as before. `has_changes` and
`change_summary` are the mirror's `changes`, fetched fresh on demand
(`git.info` with ``changes``, one `git status` on the service), so the
on-demand rule holds either way.

The git page reads the same facts for its freshness check: where the
working tree root is (`repo_root`), when the index last moved
(`index_mtime`), what HEAD and the parent branch point at (`head_sha`,
`resolve_branch`, `base_ref`), folded into one comparable `tree_signature`
— and, for its commits list, when any ref last moved (`refs_signature`) —
and names the branch it measures the current one against when git shows
no stack (`parent_branch`: the first of the host's candidates the tree can
resolve, else the default branch). Its commit gate looks for git's
in-progress markers in the worktree's own git directory, which `git_dir`
names (and which the mirror carries as `operation`: `gitops.in_progress_at`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from pathlib import Path

from . import gitfiles
from .gitfiles import OPERATION_MARKERS, GitInfo

__all__ = [
    "OPERATION_MARKERS",
    "GitInfo",
    "base_ref",
    "change_summary",
    "check_ignore_argv",
    "current_branch",
    "default_branch",
    "git_dir",
    "github_url",
    "has_changes",
    "head_sha",
    "ignored_names",
    "index_mtime",
    "info",
    "operation_markers",
    "parent_branch",
    "reader",
    "refresh",
    "refs_signature",
    "remote_branch_name",
    "repo_root",
    "resolve_branch",
    "set_reader",
    "status_porcelain_argv",
    "tree_signature",
]

log = logging.getLogger(__name__)

# The whole-tree status check (`has_changes`, `change_summary`): one
# subprocess, asked on demand only, with a budget that fits a large tree and
# refuses a hung git.
_STATUS_TIMEOUT_S = 2.0
# `ignored_names` runs once per tree listing (on the service's `fs.list`
# worker since PR-2.4; it ran on the GTK main loop before), so its budget is
# short: past it the rows simply aren't dimmed.
_IGNORE_TIMEOUT_S = 0.5

# How old a mirror entry may be before a read refreshes it (the footer's
# tick is 2 s; a right-click a moment after it reads the tick's answer).
MAX_AGE_S = 1.0

# The client's reader (remotegit): `reader(cwd, max_age=, changes=, state=)`
# -> GitInfo, or None when the service could not be asked at all (then the
# answer is "not a repository" rather than the files of this machine, which
# are not the service's). None: the local files are read.
Reader = Callable[..., "GitInfo | None"]
_reader: Reader | None = None

_safe_branch_name = gitfiles.safe_branch_name


def set_reader(fn: Reader | None) -> None:
    """Install the client's `git.info` mirror as this module's source (the
    app, through `remotegit.install`); None reads the local files again."""
    global _reader
    _reader = fn


def reader() -> Reader | None:
    return _reader


def info(cwd: str | Path | None, max_age: float = MAX_AGE_S, changes: bool = False) -> GitInfo | None:
    """The mirror's entry for *cwd*, refreshed when older than *max_age*
    (or when *changes* is wanted: a status is never served stale); None
    with no reader installed."""
    if _reader is None or not cwd:
        return None
    return _reader(str(cwd), max_age=max_age, changes=changes)


def refresh(cwd: str | Path | None) -> None:
    """Re-read *cwd*'s entry now (the git page's tick, before it compares
    the signatures; its open; a mutation it made, whose signatures it
    re-seeds from the answer). The one read that waits on the main
    thread, `remotegit.MAIN_THREAD_TIMEOUT_S` at most. No-op with no
    reader: the files are always fresh."""
    if _reader is not None and cwd:
        _reader(str(cwd), max_age=0.0, wait=True)


def _local(cwd: str | Path | None) -> bool:
    return _reader is None


def current_branch(cwd: str | Path | None) -> str | None:
    """Name of the branch checked out in the repo enclosing *cwd*.

    Returns None when *cwd* is empty, missing, or not inside a git repo.
    A detached HEAD yields the abbreviated commit hash instead of a name.
    Handles worktrees/submodules, whose `.git` is a pointer file.
    """
    if not _local(cwd):
        entry = info(cwd)
        return entry.branch if entry is not None else None
    git = gitfiles.git_dir(cwd)
    return gitfiles.read_head(git) if git else None


def default_branch(cwd: str | Path | None) -> str | None:
    """The branch the repository enclosing *cwd* treats as its trunk, or None.

    What the remote says first — `refs/remotes/<remote>/HEAD`, a clone's
    record of the remote's default branch, remotes ranked the way
    `github_url` ranks them — and, for a repository that was never cloned
    (no remote HEAD at all), whichever of `main` and `master` exists as a
    local branch. Read off the repository's files like `current_branch`, so
    a context menu can ask on every right-click; in a worktree the refs are
    the main checkout's (`commondir`), which is where every worktree's
    branches live.

    None outside a repository and for one whose trunk can't be named. The
    caller offers to check the branch out, so a guess would be worse than
    no answer.
    """
    if not _local(cwd):
        entry = info(cwd)
        return entry.default_branch if entry is not None else None
    git = gitfiles.git_dir(cwd)
    if git is None:
        return None
    return gitfiles.default_branch_of(gitfiles.common_dir(git))


def parent_branch(cwd: str | Path | None, candidates: Iterable[str | None]) -> str | None:
    """The branch the current one is measured against — the git page's "vs"
    load, the sidebar's commit groups: the first of *candidates* (in
    order; None and "" are skipped) that `resolve_branch` finds in the
    repository enclosing *cwd*, as a local or remote-tracking ref, else
    `default_branch(cwd)`.

    The whole automatic rung in one place: the host passes the attached
    PR's base, then the default parent from Preferences, and a candidate
    the tree can't name — a base nothing here can diff against, a name
    typed for another repository, one that reads as an option — falls
    through rather than disabling the load. A branch NAME comes back
    ("main", never "origin/main"): callers resolve it to a target
    themselves. A candidate written the way git prints a remote branch —
    "origin/develop", the form people type first — is taken as the branch
    behind it when that remote has it (`remote_branch_name`), so the
    Preferences entry needn't know the rule. None outside a repository, or
    when nothing resolves and the trunk can't be named either.
    """
    for name in candidates:
        if not name:
            continue
        if resolve_branch(cwd, name) is not None:
            return name
        stripped = remote_branch_name(cwd, name)
        if stripped is not None:
            return stripped
    return default_branch(cwd)


def remote_branch_name(cwd: str | Path | None, name: str | None) -> str | None:
    """The branch a remote-qualified *name* points at — "develop" for
    "origin/develop" — when its first segment names a remote in the
    repository's config and that remote has the branch
    (refs/remotes/<remote>/<rest>); None otherwise, and for a *name* that
    isn't safe as an argument. Asked only after the name failed to resolve
    as written, so a local branch that happens to carry a slash
    ("release/v1") is never mistaken for a remote's.

    What comes back is a NAME: `resolve_branch` turns it into the local
    branch when the tree has one, else the ranked remote's — which, for a
    remote that isn't the first-ranked one, may name another remote's copy
    of the branch than the one typed.
    """
    if not _local(cwd):
        entry = info(cwd)
        return entry.remote_branch_name(name) if entry is not None else None
    if not gitfiles.safe_branch_name(name) or "/" not in name:
        return None
    remote, _, rest = name.partition("/")
    if not remote or not rest:
        return None
    git = gitfiles.git_dir(cwd)
    if git is None:
        return None
    common = gitfiles.common_dir(git)
    if remote not in gitfiles.remote_urls(common / "config"):
        return None
    return rest if gitfiles.ref_sha(common, f"refs/remotes/{remote}/{rest}") else None


def github_url(cwd: str | Path | None) -> str | None:
    """The GitHub page of the repository enclosing *cwd*, or None.

    Read out of the repository's own `config` rather than asked of `git` or
    `gh`, so the sidebar can ask while building a context menu. In a worktree
    the config is the main checkout's (`commondir`); in a submodule it is the
    submodule's own, which is what its remote — and so its page — should be.

    None for everything that isn't a repository with a github.com remote:
    no cwd, no `.git`, a config with no remotes, remotes on another host, a
    remote path that isn't a plain `owner/repo`. The caller offers a menu item
    claiming there is a page to open, so anything short of certain means no.
    """
    if not _local(cwd):
        entry = info(cwd)
        return entry.github_url if entry is not None else None
    git = gitfiles.git_dir(cwd)
    if git is None:
        return None
    return gitfiles.github_url_of(gitfiles.common_dir(git))


def has_changes(cwd: str | Path | None) -> bool:
    """Whether the repo enclosing *cwd* has work in it that isn't committed.

    Staged, unstaged and untracked all count: all three are changes a new pull
    request would be opened for, which is the one question this answers (see
    practions.NEW_PR). Ignored files don't — `git status` leaves them out, and
    so does the pull request.

    A subprocess (like `ignored_names`), and the reason it is asked on demand
    rather than from the footer's poll: "is this tree dirty?" means comparing
    every tracked file against the index, which is `git status`' whole job and
    not something to re-derive off `.git`. `--no-optional-locks` keeps it from
    taking the index lock or writing a refreshed index, so it can't collide
    with the agent's own git commands in the same repository. Over the API it
    is `git.info`'s ``changes``, asked fresh (never the mirror's old answer;
    on the main thread for `remotegit.MAIN_THREAD_TIMEOUT_S` at most, past
    which the answer is the mirror's last, or False).

    False for every question that can't be answered — no cwd, no git, not a
    repository, a git that took too long. What is built on the answer is a
    menu item claiming there is something to open a pull request *for*, so
    anything short of git saying so means no.
    """
    staged, unstaged = change_summary(cwd)
    return staged or unstaged


def change_summary(cwd: str | Path | None) -> tuple[bool, bool]:
    """(staged, unstaged) for the repo enclosing *cwd*, from one `git status`.

    *staged* is any entry with a status in the index column (the first of
    the two porcelain columns), *unstaged* any entry with one in the
    worktree column or an untracked (`??`) entry — the split the git page's
    footer entry point opens on (see gitloads.initial_mode: the working tree
    while anything in it is dirty, the index when only that is). The same
    `--no-optional-locks status --porcelain` as `has_changes`, with the same
    2 s budget, and like it asked on demand only — never from the poll.

    (False, False) whenever git can't answer, for the same reason
    `has_changes` says False: a wrong "staged" would open the page on the
    index when the tree is what changed.
    """
    if not _local(cwd):
        entry = info(cwd, max_age=0.0, changes=True)
        if entry is None or entry.changes is None:
            return False, False
        return entry.changes
    return summarize_status(_status_porcelain(cwd))


def summarize_status(status: str | None) -> tuple[bool, bool]:
    """change_summary's reading of a `status --porcelain` text: (staged,
    unstaged); (False, False) for None (git couldn't answer)."""
    if status is None:
        return False, False
    staged = unstaged = False
    for line in status.splitlines():
        if len(line) < 2:
            continue
        if line.startswith("??"):
            unstaged = True
            continue
        if line.startswith("!!"):
            continue  # ignored entries only show up with --ignored, but be safe
        if line[0] not in " ?!":
            staged = True
        if line[1] != " ":
            unstaged = True
    return staged, unstaged


def status_porcelain_argv() -> list[str]:
    """["--no-optional-locks", "status", "--porcelain"]: the whole-tree
    status has_changes / change_summary read (one of the builders promoted
    for the API, D33)."""
    return ["--no-optional-locks", "status", "--porcelain"]


def check_ignore_argv() -> list[str]:
    """["--no-optional-locks", "check-ignore", "-z", "--stdin"]: which of
    the NUL-separated names on stdin git ignores (ignored_names')."""
    return ["--no-optional-locks", "check-ignore", "-z", "--stdin"]


def _status_porcelain(cwd: str | Path | None) -> str | None:
    """`git --no-optional-locks status --porcelain` in *cwd*, or None when git
    can't answer: no cwd, no git on PATH, not a repository, a non-zero exit,
    a run longer than _STATUS_TIMEOUT_S."""
    return gitfiles.status_porcelain(cwd, _STATUS_TIMEOUT_S)


def ignored_names(directory: str | Path | None, names: list[str]) -> set[str]:
    """Which of *names* (entries directly inside *directory*) git ignores.

    One batched `git check-ignore --stdin -z` per call — the service's
    `fs.list` asks once per directory listing for the file tree (on expand
    and on each `dir-changed`), never per row, so this stays one short-lived
    process per listing. `-z` on both ends keeps any filename byte-clean in
    transit.

    It ran on the GTK main loop until PR-2.4 and is still kept cheap: outside a
    repository no process is spawned at all (a pure-filesystem `.git` walk,
    like `current_branch`'s, answers first — over the API the mirror's
    answer), and inside one the subprocess gets only `_IGNORE_TIMEOUT_S`
    before the answer becomes "nothing".

    Empty set for every case that can't be answered — no git on PATH, not a
    repository, a timeout. What is built on the answer is only a dimmed row,
    so anything short of git saying "ignored" means shown at full strength.
    """
    if not directory or not names:
        return set()
    if _local(directory):
        if not gitfiles.in_repository(Path(directory)) or not gitfiles.has_git():
            return set()
    elif repo_root(directory) is None:
        return set()
    from . import gitops  # at call time: gitops imports this module

    try:
        stdin = ("\0".join(names) + "\0").encode("utf-8")
    except UnicodeError as err:  # a name that isn't text (surrogate-escaped bytes)
        log.debug("gitinfo: git check-ignore in %s failed: %s", directory, err)
        return set()
    result = gitops.run_git_bytes(directory, check_ignore_argv(), stdin=stdin, timeout=_IGNORE_TIMEOUT_S)
    # 0 = some ignored, 1 = none ignored; anything else (128: not a repo,
    # bad input) means "don't know", which reads the same as "none" — and
    # so does a git that couldn't be run at all (a timeout, not on PATH).
    if not result.ok:
        return set()
    return {name for name in result.stdout.split("\0") if name}


def repo_root(cwd: str | Path | None) -> Path | None:
    """The working tree root enclosing *cwd*: the directory holding the
    nearest `.git` entry (a directory, or a worktree/submodule pointer file).
    None outside a repository. Over the API the path is the service's."""
    if not _local(cwd):
        entry = info(cwd)
        return Path(entry.root) if entry is not None and entry.root else None
    return gitfiles.repo_root(cwd)


def index_mtime(cwd: str | Path | None) -> int | None:
    """The mtime of the repository's index file (in the worktree's own git
    dir, not the common dir), microseconds since the epoch on both paths
    (gitfiles.index_mtime's unit). None when there is no index yet or it
    can't be stat'd."""
    if not _local(cwd):
        entry = info(cwd)
        return entry.index_mtime if entry is not None else None
    git = gitfiles.git_dir(cwd)
    return gitfiles.index_mtime(git) if git is not None else None


def head_sha(cwd: str | Path | None) -> str | None:
    """The commit HEAD points at: a symbolic HEAD resolved through the loose
    ref or packed-refs in the common dir, a detached HEAD's own hash. None
    outside a repository or for an unborn branch."""
    if not _local(cwd):
        entry = info(cwd)
        return entry.head if entry is not None else None
    git = gitfiles.git_dir(cwd)
    return gitfiles.head_sha(git) if git is not None else None


def resolve_branch(cwd: str | Path | None, name: str | None) -> tuple[str, str] | None:
    """(*target*, sha) for a branch a diff can name: ("main", sha) when
    refs/heads/<name> exists, else ("<remote>/<name>", sha) for the first
    remote in rank order that has refs/remotes/<remote>/<name>. None when
    neither exists, outside a repository, or for a *name* that isn't safe
    as an argument (empty, whitespace, leading "-", containing "..").

    The remote fallback is what lets a clone that never checked `main` out
    locally still diff against it: `origin/main` is a ref git knows just as
    well. The name is what ends up on git's argv, hence the gate on it.
    """
    if not _local(cwd):
        entry = info(cwd)
        return entry.resolve_branch(name) if entry is not None else None
    if not gitfiles.safe_branch_name(name):
        return None
    git = gitfiles.git_dir(cwd)
    if git is None:
        return None
    common = gitfiles.common_dir(git)
    sha = gitfiles.ref_sha(common, f"{gitfiles.BRANCH_REF_PREFIX}{name}")
    if sha:
        return name, sha
    for remote in gitfiles.ranked_remotes(common):
        sha = gitfiles.ref_sha(common, f"refs/remotes/{remote}/{name}")
        if sha:
            return f"{remote}/{name}", sha
    return None


def base_ref(cwd: str | Path | None, base: str | None) -> str | None:
    """The sha *base* resolves to (see resolve_branch), or None."""
    resolved = resolve_branch(cwd, base)
    return resolved[1] if resolved else None


def operation_markers(cwd: str | Path | None) -> tuple[str, ...]:
    """Which of OPERATION_MARKERS exist in the repository's own git
    directory (a worktree's, where git keeps them) — empty outside a
    repository or with nothing half-finished. Stats only, no git: part
    of tree_signature, so the page notices `git merge --quit` and its
    kin, which forget an operation without touching the index or HEAD."""
    if not _local(cwd):
        entry = info(cwd)
        return entry.markers if entry is not None else ()
    return gitfiles.operation_markers(gitfiles.git_dir(cwd))


def tree_signature(cwd: str | Path | None, base: str | None) -> tuple | None:
    """(index_mtime(cwd), head_sha(cwd), base_ref(cwd, base),
    operation_markers(cwd)) — what the git page compares on the footer's
    2 s poll; any element moving means the loaded diff is stale (the
    markers: an operation started, finished, aborted or quit, which the
    page's in-progress bar follows). None outside a repository."""
    if not _local(cwd):
        entry = info(cwd)
        if entry is None or entry.root is None:
            return None
        resolved = entry.resolve_branch(base)
        return entry.index_mtime, entry.head, resolved[1] if resolved else None, entry.markers
    if gitfiles.git_dir(cwd) is None:
        return None
    return index_mtime(cwd), head_sha(cwd), base_ref(cwd, base), operation_markers(cwd)


def git_dir(cwd: str | Path | None) -> Path | None:
    """The git directory of the repository enclosing *cwd* — a worktree's
    own (`.git/worktrees/<name>`), not the common one, which is where git
    leaves its in-progress markers (MERGE_HEAD, rebase-merge, …) and the
    index. None outside a repository. Over the API the path is the
    service's: a name, not a directory this machine can read."""
    if not _local(cwd):
        entry = info(cwd)
        return Path(entry.git_dir) if entry is not None and entry.git_dir else None
    return gitfiles.git_dir(cwd)


def refs_signature(cwd: str | Path | None) -> tuple | str | None:
    """What moves when a ref is written — a commit on another branch (in
    another worktree), a branch created or deleted, a push, a fetch —
    folded into one comparable value for the git page's poll
    (gitfiles.refs_signature; over the API the service's digest of it, a
    string: the page only ever compares two). None outside a repository."""
    if not _local(cwd):
        entry = info(cwd)
        return entry.refs if entry is not None and entry.root is not None else None
    git = gitfiles.git_dir(cwd)
    if git is None:
        return None
    return gitfiles.refs_signature(git)


def _in_repository(start: Path) -> bool:
    return gitfiles.in_repository(start)
