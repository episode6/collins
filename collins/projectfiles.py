# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The project's directory reads, run by the service (split-service spec
§3.23, PR-2.4).

What the editor's file tree, quick open and the follow rule read off the
disk: a directory's entries (`list_entries`, `list_dir`), every file
under a root (`walk_files`), the repository a path sits in
(`repository_root`), the symlink-resolving containment check every
`fs.*` request is confined by (`is_inside`) and whether the editor should
follow the agent's working directory (`follow_scope`).

They moved here out of `editorfiles` (a module the client calls, which
the pathless walker reads) when the tree and quick open went over the
API: the service runs them for `fs.list`, `fs.walk`, `fs.stat` and
`cwd.settle` (`service/files.py`, `service/session.py`), and the client
reaches them only through those requests, the way `gitfiles.py` holds
the `.git` reads behind `git.info` (PR-2.1). The rename and paste rules
still in `editorfiles` (PR-2.5's) call `is_inside` from here until they
move too.

Stdlib only (plus `sessions.worktree_project_root`, a string rule)."""

from __future__ import annotations

import enum
import os
from collections import deque
from pathlib import Path

from .sessions import worktree_project_root

# Skipped wherever a directory is listed or expanded: build output,
# dependency trees, and VCS internals nobody wants cluttering a "look at
# what the agent just wrote" file tree.
SKIP_DIR_NAMES = {".git", "node_modules", "__pycache__", ".venv", "target", "dist", "build"}

# A directory this size is either a build artifact that slipped past
# SKIP_DIR_NAMES or a mistake; either way the tree stops rather than stalling
# on it. Sorted first, so what's dropped is always the tail, alphabetically.
# The wire's bound too (protocol.FS_LIST_MAX).
MAX_DIR_ENTRIES = 5000
# The most files quick open indexes under one root (protocol.FS_WALK_MAX),
# and the most folders a walk queues: a tree of empty folders would
# otherwise grow the queue without bound (review of PR 611).
WALK_CAP = 20_000
WALK_DIRS_CAP = 50_000

# An entry's kind, as `fs.list` answers it: a file, a directory, or a
# symlink to a directory inside the root (shown as a folder, never expanded:
# the tree never follows a link, even one that stays inside).
KIND_FILE = "file"
KIND_DIR = "dir"
KIND_SYMLINK = "symlink"


class FollowScope(enum.Enum):
    """How the editor should react to the session's working directory moving
    somewhere new (see `follow_scope`)."""

    NONE = "none"  # not a move: same place, or nowhere worth following
    AUTO = "auto"  # still the same project — re-root without asking
    OFFER = "offer"  # somewhere else entirely — offer it, don't take it


def is_inside(root: str | Path, path: str | Path) -> bool:
    """Whether *path* resolves to somewhere inside *root* — the guard against
    a symlink walking the file tree out of the project."""
    try:
        resolved_root = Path(root).resolve()
        resolved_path = Path(path).resolve()
    except OSError:
        return False
    return resolved_path == resolved_root or resolved_root in resolved_path.parents


def list_entries(
    path: str | Path, show_hidden: bool = False, root: str | Path | None = None
) -> tuple[list[tuple[str, str]], bool]:
    """Sorted `(name, kind)` entries directly inside *path* (kind one of
    KIND_FILE, KIND_DIR, KIND_SYMLINK) and whether MAX_DIR_ENTRIES cut the
    list: directories (and links to them) first, then case-insensitive by
    name. Skips dotfiles unless `show_hidden`, VCS/dependency directories
    (`SKIP_DIR_NAMES`), and anything that is neither a regular file nor a
    directory (FIFOs, sockets, devices — never worth showing, never worth
    opening). When *root* is given, a symlink resolving outside it is
    skipped too — file symlinks included, so an untrusted repo can't
    surface (and the editor can't write through) `leak.txt ->
    ~/.ssh/id_rsa`. A symlink to a file inside it is a file; one to a
    directory inside it is KIND_SYMLINK."""
    try:
        entries = list(Path(path).iterdir())
    except OSError:
        return [], False
    result: list[tuple[str, str]] = []
    for entry in entries:
        name = entry.name
        if not show_hidden and name.startswith("."):
            continue
        try:
            is_dir = entry.is_dir()
            is_file = entry.is_file()
            is_symlink = entry.is_symlink()
        except OSError:
            continue
        if not is_dir and not is_file:
            continue
        if root is not None and is_symlink and not is_inside(root, entry):
            continue
        if is_dir and name in SKIP_DIR_NAMES:
            continue
        kind = (KIND_SYMLINK if is_symlink else KIND_DIR) if is_dir else KIND_FILE
        result.append((name, kind))
    result.sort(key=lambda item: (item[1] == KIND_FILE, item[0].casefold()))
    return result[:MAX_DIR_ENTRIES], len(result) > MAX_DIR_ENTRIES


def list_dir(
    path: str | Path, show_hidden: bool = False, root: str | Path | None = None
) -> list[tuple[str, bool]]:
    """`list_entries` as `(name, is_dir)` pairs, truncated at
    MAX_DIR_ENTRIES (a symlinked directory is a directory here)."""
    entries, _truncated = list_entries(path, show_hidden, root)
    return [(name, kind != KIND_FILE) for name, kind in entries]


def walk_files(
    root: str | Path, show_hidden: bool = False, cap: int = WALK_CAP, dirs_cap: int = WALK_DIRS_CAP
) -> tuple[list[str], bool]:
    """Every file under *root* as project-relative POSIX paths, breadth-first
    (so shallow files land early and quick-open's ties favour them). Reuses
    `list_entries`' skip rules — hidden files, SKIP_DIR_NAMES, irregular
    nodes, symlinks escaping *root* — and never descends into a symlinked
    directory at all, exactly like the file tree's expansion rule, so a link
    cycle can't wedge the walk. Returns `(paths, truncated)`; *truncated* is
    True when the *cap* (files) or *dirs_cap* (folders queued) stopped the
    walk early."""
    root = Path(root)
    paths: list[str] = []
    queue: deque[tuple[Path, str]] = deque([(root, "")])
    queued = 1
    dirs_cut = False
    while queue:
        directory, prefix = queue.popleft()
        entries, _truncated = list_entries(directory, show_hidden, root=root)
        for name, kind in entries:
            if kind == KIND_DIR:
                if queued >= dirs_cap:
                    dirs_cut = True  # the folders already queued are still walked
                    continue
                queued += 1
                queue.append((directory / name, f"{prefix}{name}/"))
            elif kind == KIND_FILE:
                if len(paths) >= cap:
                    return paths, True
                paths.append(f"{prefix}{name}")
    return paths, dirs_cut


def repository_root(path: str | Path) -> str | None:
    """The top of the git repository *path* sits in, or None when it is in
    none. A couple of stat calls rather than a `git` process, and `.git` may
    be a directory (a checkout) or a pointer file (a worktree, a submodule) —
    either one tops the search out."""
    try:
        start = Path(path)
        for directory in (start, *start.parents):
            if (directory / ".git").exists():
                return str(directory)
    except OSError:
        return None
    return None


def follow_scope(root: str | Path, cwd: str | None) -> FollowScope:
    """Whether an editor rooted at *root* should move to *cwd*.

    The session's agent moves on its own — into a Claude worktree under the
    repository (`<repo>/.claude/worktrees/<name>`), back out of one, or down
    into a subdirectory — and the editor is meant to be showing whatever the
    agent is working on. Anywhere inside the same repository is that same
    project seen from a different angle, so it is followed silently (AUTO);
    `.claude` being a dotfile makes the worktree case the one that matters
    most, since the file tree hides it outright from the repository root.

    Anywhere *else* is a different project, and re-rooting there would
    silently swap out every open file. That is offered, never taken (OFFER).
    """
    if not cwd:
        return FollowScope.NONE
    try:
        if not Path(cwd).is_dir():
            return FollowScope.NONE
        if os.path.realpath(cwd) == os.path.realpath(root):
            return FollowScope.NONE
    except OSError:
        return FollowScope.NONE
    # The boundary is the repository, not wherever the pane happens to be
    # rooted at this moment — an editor that already followed the agent down
    # into a worktree or a subdirectory has to be able to follow it back out
    # again, and comparing against its current root would read that as leaving
    # the project. A Claude worktree names its repository outright; anything
    # else walks up to the enclosing checkout, and a directory in no
    # repository at all is its own boundary.
    project = worktree_project_root(str(root)) or repository_root(root) or str(root)
    return FollowScope.AUTO if is_inside(project, cwd) else FollowScope.OFFER
