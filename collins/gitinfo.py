"""Best-effort git repository info, read straight from `.git` (no subprocess).

Cheap enough to call from the tab footer's 2s poll: finding the branch is a
couple of stat calls and one small file read, with no `git` processes spawned.
"""

from __future__ import annotations

from pathlib import Path

_REF_PREFIX = "ref:"
_BRANCH_REF_PREFIX = "refs/heads/"


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
