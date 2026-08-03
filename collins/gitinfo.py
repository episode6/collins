# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Best-effort git repository info, read straight from `.git` where it can be.

Finding the branch is a couple of stat calls and one small file read, with no
`git` processes spawned — cheap enough for the tab footer's 2s poll. Asking
whether the tree is dirty (`has_changes`) or which entries are ignored
(`ignored_names`) can't be answered that way, so those shell out and are only
ever asked on demand.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_REF_PREFIX = "ref:"
_BRANCH_REF_PREFIX = "refs/heads/"

# Long enough for `git status` in a repository of any size worth working in,
# short enough that a menu built on the answer doesn't visibly stall waiting
# for it. A repository slower than this is treated as clean.
_STATUS_TIMEOUT_S = 2.0

# `ignored_names` runs on the GTK main loop (the file tree asks while
# building rows), so its budget is much tighter than the menu-building
# _STATUS_TIMEOUT_S: `check-ignore` reads only the exclude files, never the
# index, and answers in milliseconds — a repository that can't make this
# deadline just renders undimmed rather than stalling every expand.
_IGNORE_TIMEOUT_S = 0.5


def current_branch(cwd: str | Path | None) -> str | None:
    """Name of the branch checked out in the repo enclosing *cwd*.

    Returns None when *cwd* is empty, missing, or not inside a git repo.
    A detached HEAD yields the abbreviated commit hash instead of a name.
    Handles worktrees/submodules, whose `.git` is a pointer file.
    """
    if not cwd:
        return None
    start = Path(cwd)
    if not start.is_dir():
        return None
    for directory in (start, *start.parents):
        git = directory / ".git"
        if git.is_dir():
            return _read_head(git)
        if git.is_file():  # worktree or submodule: "gitdir: <real git dir>"
            git_dir = _resolve_gitdir_pointer(git)
            return _read_head(git_dir) if git_dir else None
    return None


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
    with the agent's own git commands in the same repository.

    False for every question that can't be answered — no cwd, no git, not a
    repository, a git that took too long. What is built on the answer is a
    menu item claiming there is something to open a pull request *for*, so
    anything short of git saying so means no.
    """
    if not cwd or not Path(cwd).is_dir():
        return False
    git = shutil.which("git")
    if git is None:
        return False
    try:
        result = subprocess.run(
            [git, "--no-optional-locks", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=_STATUS_TIMEOUT_S,
            cwd=str(cwd),
        )
    except (OSError, subprocess.SubprocessError) as err:
        log.debug("gitinfo: git status in %s failed: %s", cwd, err)
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def ignored_names(directory: str | Path | None, names: list[str]) -> set[str]:
    """Which of *names* (entries directly inside *directory*) git ignores.

    One batched `git check-ignore --stdin -z` per call — the file tree asks
    once per directory listing (on expand and on the debounced refresh), never
    per row, so this stays one short-lived process per user action. `-z` on
    both ends keeps any filename byte-clean in transit.

    The caller is on the GTK main loop, so this is kept cheap: outside a
    repository no process is spawned at all (a pure-filesystem `.git` walk,
    like `current_branch`'s, answers first), and inside one the subprocess
    gets only `_IGNORE_TIMEOUT_S` before the answer becomes "nothing".

    Empty set for every case that can't be answered — no git on PATH, not a
    repository, a timeout. What is built on the answer is only a dimmed row,
    so anything short of git saying "ignored" means shown at full strength.
    """
    if not directory or not names or not _in_repository(Path(directory)):
        return set()
    git = shutil.which("git")
    if git is None:
        return set()
    try:
        result = subprocess.run(
            [git, "--no-optional-locks", "check-ignore", "-z", "--stdin"],
            input="\0".join(names) + "\0",
            capture_output=True,
            text=True,
            timeout=_IGNORE_TIMEOUT_S,
            cwd=str(directory),
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as err:
        log.debug("gitinfo: git check-ignore in %s failed: %s", directory, err)
        return set()
    # 0 = some ignored, 1 = none ignored; anything else (128: not a repo,
    # bad input) means "don't know", which reads the same as "none".
    if result.returncode != 0:
        return set()
    return {name for name in result.stdout.split("\0") if name}


def _in_repository(start: Path) -> bool:
    """Whether *start* is inside a git repository — a couple of stat calls
    (`.git` may be a directory, or a worktree/submodule pointer file), so a
    tree outside any repository never pays for a `git` process."""
    if not start.is_dir():
        return False
    return any((directory / ".git").exists() for directory in (start, *start.parents))


def _resolve_gitdir_pointer(git_file: Path) -> Path | None:
    try:
        text = git_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("gitdir:"):
            target = line[len("gitdir:") :].strip()
            if target:  # Path("/a") / "/abs" keeps the absolute target as-is
                return git_file.parent / target
    return None


def _read_head(git_dir: Path) -> str | None:
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if head.startswith(_REF_PREFIX):
        ref = head[len(_REF_PREFIX) :].strip()
        if ref.startswith(_BRANCH_REF_PREFIX):
            return ref[len(_BRANCH_REF_PREFIX) :] or None
        return ref or None
    return head[:8] or None  # detached HEAD
