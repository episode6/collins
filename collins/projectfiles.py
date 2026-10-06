# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The project's directory reads, run by the service (split-service spec
§3.23, PR-2.4 and PR-2.5).

What the editor's file tree, quick open and the follow rule read off the
disk: a directory's entries (`list_entries`, `list_dir`), every file
under a root (`walk_files`), the repository a path sits in
(`repository_root`), the symlink-resolving containment check every
`fs.*` request is confined by (`is_inside`) and whether the editor should
follow the agent's working directory (`follow_scope`); and what the
tree's context menu does to the disk: a rename in place
(`rename_target`, `rename_entry`), a paste that never overwrites
(`unique_target`, `paste_target`, `paste_entries`) and a new folder
(`make_directory`).

**Placement is exclusive** (split-service spec D44, §3.23): "never over
anything" holds against every writer, not only Collins' own requests —
the agent writes in the same folders by design, and a client's write
must land inside its root even when something planted a symlink at the
name between the check and the write. The rule that holds everything
together is that **the service never names a path again once it has
lost its hold on it (D47)**: a path is resolved once, to a directory
descriptor, and every operation after that names an entry relative to
a held descriptor (`dir_fd=`). `rename_entry`, `paste_entries` and
`make_directory` each open the directory they work in (`_open_dir`:
`O_PATH|O_DIRECTORY`, which asks search permission only, so a folder
that can be entered but not listed takes a rename, a mkdir and a paste
as it did before the holds; symlinks followed on the way in: a
symlinked root is legitimate), read
the held directory's current canonical path from `/proc/self/fd/<fd>`
(`_held_path`) and confine *that* — the destination and a rename's
directory inside the request's root (`held_inside`: the kernel's
answer compared as it stands, not resolved again), a paste source's
parent inside a known root through `source_allowed` (the service's,
which compares it the same way; a root itself, whose parent is inside
no root, is therefore no source for a client that is not `local`) —
then nothing
re-resolves: the destination is `(dst_fd, name)`, the source
`(src_fd, name)`. The path
pre-checks (`rename_target`, `paste_target`, `_exists`) stay as the
cheap refusals, not the authority.

The copy (`_copy_entry`): a link as a link (`os.symlink` under `dir_fd`,
`EEXIST` the name taken); a regular file through
`O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW` (a planted link never followed),
its source opened `O_NOFOLLOW` and read only when it is a regular file,
the bytes by a `sendfile` loop with shutil's fallback rule
(`_copy_bytes`), the metadata on the destination descriptor before it
is closed and never by path (`_copy_metadata`: the xattrs by
descriptor with `shutil._copyxattr`'s swallow set, then `fchmod` — a
read-only file takes no xattr once it is read-only — then `utime(fd,
ns=)`: `copy2`'s parity); a tree by an fd-relative walk (`_copy_tree`,
`_walk`: `os.scandir(fd)`, kinds by `os.stat(name, dir_fd=…,
follow_symlinks=False)`, `mkdir` / `open` / `symlink` under `dir_fd`,
errors collected as `shutil.Error`), never `shutil.copytree`, which
walks and stats by path.

The move (a cut's paste, and a rename, which is a move within one held
directory; `_move_exclusive`), in order: `renameat2(src_fd, name,
dst_fd, newname, RENAME_NOREPLACE)` through `ctypes`
(`_rename_noreplace`), the one exclusive primitive with `rename`'s own
semantics for a file, a directory, a link, a FIFO alike — no
placeholder, no ownership or read permission needed, the inode kept;
where the flag is not supported (`EINVAL`, `ENOSYS`, `ENOTSUP`, or no
such symbol: NFS, a FUSE mount without `rename2`, an old FAT, another
libc) the placeholder path (`_move_by_placeholder`: an exclusive empty
file whose descriptor is held across the `os.rename` that replaces it,
or `os.mkdir`'s empty directory, which the rename replaces only while
it is empty — `ENOTEMPTY` means someone put something in it, so it
stays and the name counts as taken; the fallback's accepted limit is
that a `rename` that succeeds replaces what is at the name at that
instant); `EXDEV` from either, the copy path (`_move_by_copy`: the
exclusive copy, its source and destination descriptors held, then the
source removed, as `shutil.move` does, only while its name still holds
what was copied: else the name is left and the move is done). What a
failed operation
leaves (D46): the service removes only what it made in this operation,
only while it still holds it (`_undo`: `(st_dev, st_ino)` of
`fstat(fd)` against `lstat(name, dir_fd=…)`, a held descriptor pinning
the inode so its number cannot be reused meanwhile), and only when it
holds nothing of anyone else's — a file placeholder whose rename
failed, a file copy that failed after bytes went, a file copied across
filesystems whose source then could not be unlinked (the entry `failed`
with the `unlink`'s words, the source intact, nothing at the
destination); not a directory placeholder with something in it, not a
tree copied across whose `rmtree` failed (both halves kept, as
`shutil.move` left them), not a tree copy that failed partway.
`EEXIST` at a paste's target is the next name of `unique_target`'s
sequence (`paste_entries`' loop: try, and on `EEXIST` the next;
`no_room` after the hundredth); at a rename's it is `exists`;
`make_directory`'s `os.mkdir` under the held parent is exclusive by
itself. No lock serializes the operations: with exclusive placement two
requests cannot land one name, and a lock would hold every client's
rename behind a long copy.

They moved here out of `editorfiles` (a module the client calls, which
the pathless walker reads) when the tree and quick open went over the
API: the service runs them for `fs.list`, `fs.walk`, `fs.stat` and
`cwd.settle` (`service/files.py`, `service/session.py`), and the client
reaches them only through those requests, the way `gitfiles.py` holds
the `.git` reads behind `git.info` (PR-2.1). The rename, paste and new
folder rules followed in PR-2.5, served as `fs.rename`, `fs.paste` and
`fs.mkdir`.

Stdlib only (plus `sessions.worktree_project_root`, a string rule)."""

from __future__ import annotations

import ctypes
import enum
import errno
import os
import shutil
import stat as stat_mod
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
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


# -- renaming, pasting and a new folder (PR-2.5) -------------------------------------------
#
# The file tree's Rename, Copy / Cut / Paste rules, run by the service for
# `fs.rename` / `fs.paste` / `fs.mkdir` (`service/files.py`). They moved
# here from `editorfiles` with the directory reads' reason: the client
# calls `editorfiles`, which the pathless walker reads, and these touch
# the disk. The two enums are re-exported from `editorfiles` for the
# client's messages, which switch on them; `rename_name_error` is pure and
# the client runs it first, so an empty or path-shaped name never makes a
# round trip.

# How many "(copy N)" names a paste will try before giving up on finding a
# free one (see `unique_target`).
_MAX_COPY_SUFFIXES = 100


class RenameError(enum.Enum):
    """Why a rename asked for in the file tree can't happen. Each one gets
    its own message in editor.py — "that didn't work" says nothing about
    which of these it was. The values cross the wire as `fs.rename`'s
    refusal ``reason``."""

    EMPTY = "empty"
    NOT_A_NAME = "not_a_name"  # a path, not a name: separators, "." or ".."
    EXISTS = "exists"
    MISSING = "missing"  # what's being renamed is already gone
    OUTSIDE = "outside"


class PasteError(enum.Enum):
    """Why something on the clipboard can't be pasted where it was asked for.
    One entry per rule, for the same reason `RenameError` has them: "that
    didn't work" says nothing about which rule it broke. The values cross
    the wire in `fs.paste`'s per-entry results."""

    MISSING = "missing"  # what the clipboard names is no longer on disk
    OUTSIDE = "outside"  # the destination isn't inside the project
    # The source is inside no root the service knows (a client that is not
    # `local` may only name what a session exposed: §3.23)
    SOURCE_OUTSIDE = "source_outside"
    NOT_A_DIR = "not_a_dir"  # the destination folder is gone
    INTO_ITSELF = "into_itself"  # a folder pasted into itself or its contents
    NO_ROOM = "no_room"  # every "(copy N)" name is taken
    FAILED = "failed"  # the copy/move itself failed; `message` says why


def name_error(name: str) -> RenameError | None:
    """Whether *name* (already stripped) is a bare name an entry can have:
    `RenameError.EMPTY`, `NOT_A_NAME` (a path, "." or "..", a NUL), or None
    when it is. Pure; `rename_name_error` and `make_directory` share it."""
    if not name:
        return RenameError.EMPTY
    # The .name comparison catches separators (and "." on its own, whose name
    # is empty); ".." survives it, and "\0" is the one character Path carries
    # happily right up to the syscall that rejects it.
    if name in (".", "..") or "\x00" in name or Path(name).name != name:
        return RenameError.NOT_A_NAME
    return None


def rename_name_error(path: str | Path, new_name: str) -> RenameError | None | bool:
    """The pure half of `rename_target`: `RenameError.EMPTY` or
    `NOT_A_NAME` when *new_name* is no bare name, False when it is the
    name *path* already has (nothing to do), None when the rename is
    worth asking the disk about. No disk is read: the client runs this
    before the request."""
    name = new_name.strip()
    verdict = name_error(name)
    if verdict is not None:
        return verdict
    if name == Path(path).name:
        return False
    return None


def rename_target(
    root: str | Path, path: str | Path, new_name: str
) -> tuple[Path | None, RenameError | None]:
    """Where renaming *path* to *new_name* would land: `(target, None)` for a
    rename worth doing, `(None, None)` when the name is unchanged (nothing to
    do, and nothing to complain about), `(None, error)` otherwise.

    Only ever a rename *in place* — the entry keeps its directory, so this
    takes a bare name and refuses anything with a path in it. Everything
    else is checked here rather than left to `os.rename`, whose own answer
    to renaming onto an existing file is to silently replace it."""
    path = Path(path)
    verdict = rename_name_error(path, new_name)
    if verdict is False:
        return None, None
    if verdict is not None:
        return None, verdict
    name = new_name.strip()
    try:
        if not path.exists() and not path.is_symlink():
            return None, RenameError.MISSING
    except OSError:
        return None, RenameError.MISSING
    target = path.parent / name
    # Belt and braces behind the bare-name check above: the same rule the
    # tree and the editor apply to everything else they touch — nothing
    # outside the project. The entry itself has to be inside it too (a
    # rename across roots, or of something the root does not hold).
    if not is_inside(root, path.parent) or not is_inside(root, target):
        return None, RenameError.OUTSIDE
    try:
        if target.exists() or target.is_symlink():
            return None, RenameError.EXISTS
    except OSError:
        return None, RenameError.EXISTS
    return target, None


def rename_entry(
    root: str | Path, path: str | Path, target: str | Path
) -> tuple[Path | None, RenameError | None]:
    """`fs.rename`'s work: *path* renamed to *target*, which must be the
    same directory's (`rename_target` decides, from *target*'s name; a
    target in another directory is `NOT_A_NAME`: a rename never moves
    things elsewhere — a paste of a cut does), and exactly the name asked
    for (`rename_target` trims surrounding whitespace for the dialog; a
    request is refused `NOT_A_NAME` rather than landed somewhere the reply
    does not say). `(target, None)` once it is done, `(None, None)` for an
    unchanged name, `(None, error)` when it was refused: `EXISTS` when the
    name was free at the check and taken by the time of the placement,
    which is exclusive (`_move_exclusive`, D44); an `OSError` from the
    rename itself is the caller's. Both paths are normalized first
    (`sub/..` names its parent, not an entry called "..")."""
    path, target = Path(os.path.normpath(path)), Path(os.path.normpath(target))
    if target.parent != path.parent or target.name != target.name.strip():
        return None, RenameError.NOT_A_NAME
    landing, error = rename_target(root, path, target.name)
    if landing is None:
        return None, error
    # The hold (D47): the directory opened once and confined by the
    # kernel's own path of it; the rename names the two entries under it.
    try:
        fd = _open_dir(path.parent)
    except FileNotFoundError:
        return None, RenameError.MISSING
    except NotADirectoryError:
        return None, RenameError.MISSING
    try:
        held = _held_path(fd)
        if held is None:
            return None, RenameError.MISSING
        if not held_inside(root, held):
            return None, RenameError.OUTSIDE
        try:
            _move_exclusive(fd, path.name, fd, landing.name)
        except FileExistsError:
            return None, RenameError.EXISTS
    finally:
        _close(fd)
    return landing, None


def _exists(path: Path) -> bool:
    """Whether *path* is taken — a broken symlink included, which `exists()`
    alone says nothing about and which `rename`/`copy` would still clobber.
    An unreadable answer counts as taken: nothing here should write over
    something it couldn't look at."""
    try:
        return path.exists() or path.is_symlink()
    except OSError:
        return True


def _copy_split(name: str) -> tuple[str, str]:
    """*name* cut into the part "(copy)" goes after and the extension it goes
    before. `Path.suffix` alone stops at the last dot, which makes
    `archive.tar.gz` into `archive.tar (copy).gz`; the `.tar` of a compressed
    tarball is part of the extension, and that pair is the one compound
    suffix worth the exception — the same one GNOME's own file manager
    makes. A leading dot is a name, not an extension: `.bashrc` splits whole,
    so a dotfile's copy stays a dotfile."""
    stem, suffix = Path(name).stem, Path(name).suffix
    if suffix and Path(stem).suffix == ".tar":
        stem, suffix = Path(stem).stem, ".tar" + suffix
    return stem, suffix


def unique_target(directory: str | Path, name: str) -> Path | None:
    """Where an entry called *name* can land in *directory* without replacing
    anything: `name` itself when it is free, then "name (copy).ext",
    "name (copy 2).ext"… None once even those are taken (a directory holding
    a hundred copies of one name is doing something else entirely).

    Never handing back an existing path is the point: both `shutil.copy2` and
    `shutil.move` overwrite what they land on without a word, and a paste is
    nobody's idea of a way to delete a file."""
    directory = Path(directory)
    stem, suffix = _copy_split(name)
    for attempt in range(_MAX_COPY_SUFFIXES + 1):
        if attempt == 0:
            candidate = name
        elif attempt == 1:
            candidate = f"{stem} (copy){suffix}"
        else:
            candidate = f"{stem} (copy {attempt}){suffix}"
        target = directory / candidate
        if not _exists(target):
            return target
    return None


def paste_target(
    root: str | Path, dest_dir: str | Path, source: str | Path, move: bool = False
) -> tuple[Path | None, PasteError | None]:
    """Where pasting *source* into *dest_dir* would land: `(target, None)` for
    a paste worth doing, `(None, None)` when there is nothing to do (a cut
    entry pasted back into the folder it came from), `(None, error)` otherwise.

    *source* is deliberately allowed to live outside the project — a copy
    taken in a file manager is exactly what paste is for (`paste_entries`'
    *source_allowed* is where the service draws its own line) — but the
    destination never is, and a folder can't be pasted into itself or into
    anything it contains, which would either fail halfway or recurse."""
    dest = Path(dest_dir)
    src = Path(source)
    if not is_inside(root, dest):
        return None, PasteError.OUTSIDE
    if not dest.is_dir():
        return None, PasteError.NOT_A_DIR
    if not _exists(src):
        return None, PasteError.MISSING
    if src.is_dir() and is_inside(src, dest):
        return None, PasteError.INTO_ITSELF
    if move and _same_dir(src.parent, dest):
        return None, None  # already where the paste would put it
    target = unique_target(dest, src.name)
    if target is None:
        return None, PasteError.NO_ROOM
    return target, None


def _same_dir(one: Path, other: Path) -> bool:
    try:
        return one.resolve() == other.resolve()
    except OSError:
        return False


@dataclass
class PasteOutcome:
    """What became of one clipboard entry. *target* is where it landed (None
    when it didn't), *error* why not, and *message* the OS's own words for a
    `FAILED` one."""

    source: Path
    target: Path | None = None
    error: PasteError | None = None
    message: str = ""


# -- the holds (D47) --------------------------------------------------------------------------
#
# A path is resolved once, to a directory descriptor; everything after
# that names an entry relative to a held descriptor (`dir_fd=`), so a
# component swapped for a symlink after the check carries no read and no
# write anywhere. The path pre-checks above (`rename_target`,
# `paste_target`) stay as the cheap refusals; the held descriptor is the
# authority.

# A directory the service only works *in* (the destination, a rename's
# directory, a paste source's parent, a mkdir's parent): `O_PATH` needs
# search permission on it and nothing more, as `os.rename`, `os.mkdir` and
# `copy2` did before the holds (D48), `/proc/self/fd` names it all the
# same, and every `dir_fd=` call takes it. It cannot be listed or
# `fchmod`ed, which only a tree's own folders are.
_HOLD_FLAGS = os.O_PATH | os.O_DIRECTORY
# A tree's own folder, source or new: listed by the walk, its metadata set
# on the descriptor, and never reached through a link.
_DIR_NOFOLLOW_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOCTTY | os.O_NOFOLLOW
# A file made exclusively at a name: a planted link is never followed.
_EXCL_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NOCTTY
_DELETED_SUFFIX = " (deleted)"


def _open_dir(path: str | Path) -> int:
    """The directory at *path* held to work in (`_HOLD_FLAGS`; symlinks
    followed on the way in: a symlinked root or a linked subdirectory
    inside the project is legitimate); the caller confines `_held_path`
    of it."""
    return os.open(path, _HOLD_FLAGS)


def _held_path(fd: int) -> str | None:
    """The held directory's current canonical path, the kernel's own
    (`/proc/self/fd/<fd>`, its `d_path`: one parent per directory): None
    when the directory is gone (" (deleted)" after its name) or has no
    path from here (a detached mount reads "(unreachable)/…", which is
    not absolute). A directory really *named* "x (deleted)" is told from
    a removed one by what that path names now: the held inode itself, or
    not."""
    path = os.readlink(f"/proc/self/fd/{fd}")
    if not path.startswith("/"):
        return None
    if path.endswith(_DELETED_SUFFIX):
        try:
            if not _same_inode(os.stat(path), os.fstat(fd)):
                return None
        except OSError:
            return None
    return path


def held_inside(root: str | Path, held: str) -> bool:
    """Whether the held directory's own path *held* (`_held_path`) is
    *root* or under it. *root* is resolved through its symlinks (a
    symlinked root is legitimate); *held* is the kernel's canonical
    answer and is compared as it stands, never resolved again (D47: a
    component of it turned into a link since would otherwise be
    followed). The service's `source_allowed` answers with it too
    (`service/files.py`, D48): the same holds for an already resolved
    path, which is what its first question is about."""
    try:
        resolved = os.path.realpath(root)
    except OSError:
        return False
    return held == resolved or held.startswith(resolved.rstrip("/") + "/")


def _same_inode(one: os.stat_result, other: os.stat_result) -> bool:
    return (one.st_dev, one.st_ino) == (other.st_dev, other.st_ino)


def _holds(dir_fd: int, name: str, fd: int) -> bool:
    """Whether `(dir_fd, name)` still names the inode the open *fd* holds
    (`lstat` against `fstat`; a held descriptor pins the number, so
    nothing swapped over the name can carry it). A name that is gone, or
    cannot be read, does not."""
    try:
        return _same_inode(os.lstat(name, dir_fd=dir_fd), os.fstat(fd))
    except OSError:
        return False


def _undo(dir_fd: int, name: str, fd: int) -> None:
    """D46's undo of a thing made at `(dir_fd, name)` whose descriptor *fd*
    is still open: unlinked only while the name still holds that inode
    (`_holds`); every `OSError` swallowed. The caller closes *fd*
    afterwards. No call unlinks by inode, so the gap between the `lstat`
    and the `unlink` remains, two syscalls wide, and is accepted."""
    try:
        if _holds(dir_fd, name, fd):
            os.unlink(name, dir_fd=dir_fd)
    except OSError:
        pass


def _close(*fds: int | None) -> None:
    for fd in fds:
        if fd is not None and fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass


# -- the copy ------------------------------------------------------------------------------

# What a first `sendfile` answers when it cannot serve this pair of
# descriptors (`shutil._fastcopy_sendfile`'s give-up set): the
# read-and-write loop instead.
_NO_SENDFILE_ERRNOS = frozenset({errno.EINVAL, errno.ENOTSOCK, errno.ENOSYS, errno.EOPNOTSUPP})
# What an xattr the kernel will not copy answers (`shutil._copyxattr`'s
# swallow set), per name and for the list itself.
_XATTR_ERRNOS = frozenset({errno.ENOTSUP, errno.ENODATA, errno.EINVAL, errno.EPERM, errno.EACCES})
_COPY_CHUNK = 8 * 1024 * 1024


def _copy_bytes(src_fd: int, dst_fd: int) -> None:
    """The bytes of *src_fd* onto *dst_fd*: `os.sendfile` in a loop until
    it answers 0, a read-and-write loop only when the first call fails
    with nothing written and an errno of `_NO_SENDFILE_ERRNOS` (shutil's
    own rule; `ENOSPC`, and any failure after bytes went, raise)."""
    offset = 0
    try:
        while True:
            sent = os.sendfile(dst_fd, src_fd, offset, _COPY_CHUNK)
            if sent == 0:
                return
            offset += sent
    except OSError as err:
        if offset != 0 or err.errno not in _NO_SENDFILE_ERRNOS:
            raise
    os.lseek(src_fd, 0, os.SEEK_SET)
    while True:
        chunk = os.read(src_fd, _COPY_CHUNK)
        if not chunk:
            return
        view = memoryview(chunk)
        while view:
            view = view[os.write(dst_fd, view) :]


def _copy_metadata(src_fd: int, dst_fd: int, sst: os.stat_result) -> None:
    """`copy2`'s parity, on the destination descriptor before it is closed
    and never by path (a link swapped in at the name carries nothing):
    the xattrs the kernel lets a user copy (the swallow set per name and
    for the list, as `shutil._copyxattr`), then the mode bits, then the
    atime and mtime to the nanosecond. The xattrs go before the mode for
    `copystat`'s own reason: a `user.` xattr needs write permission on
    the inode, so a file made read-only first would lose them, silently
    (`EACCES` is in the swallow set). *sst* is `fstat(src_fd)`, the file
    that was copied; no stat of the source path again."""
    try:
        names = os.listxattr(src_fd)
    except OSError as err:
        if err.errno not in _XATTR_ERRNOS:
            raise
        names = []
    for name in names:
        try:
            os.setxattr(dst_fd, name, os.getxattr(src_fd, name))
        except OSError as err:
            if err.errno not in _XATTR_ERRNOS:
                raise
    os.fchmod(dst_fd, stat_mod.S_IMODE(sst.st_mode))
    os.utime(dst_fd, ns=(sst.st_atime_ns, sst.st_mtime_ns))


_PIN_FLAGS = os.O_PATH | os.O_NOFOLLOW


def _copy_entry(src_fd: int, name: str, dst_fd: int, newname: str) -> tuple[int, int]:
    """One entry of the held directory *src_fd* copied to *newname* in the
    held *dst_fd*. A link as a link: `os.symlink` (`EEXIST` the name
    taken), its times by name with `follow_symlinks=False`, swallowed (a
    link has no mode on Linux, and `NOFOLLOW` means a link swapped in
    cannot carry the write anywhere), then pinned `O_PATH|O_NOFOLLOW`. A
    regular file: the source opened `O_NOFOLLOW` (a link swapped in after
    the stat is `ELOOP`) and non-blocking, and read only when it is a
    regular file (`ENOTSUP` "Not a regular file" for a FIFO, a device, a
    socket: a FIFO would wait for a writer, `/dev/zero` would fill the
    disk); the target through `O_CREAT|O_EXCL|O_NOFOLLOW` (`EEXIST` the
    name taken; a planted link never followed); the bytes by
    `_copy_bytes`; the metadata on the descriptor (`_copy_metadata`). A
    file copy that fails once its target was made is undone (`_undo`,
    D46).

    Returns `(dfd, sfd)`, both still open and the caller's to close: the
    destination's descriptor (the copy's, or the new link's pin) for a
    move's undo, and the source's (the file that was read, or the source
    link's pin) for a move's check that the source name still holds what
    was copied. Held, each pins its inode number."""
    st = os.stat(name, dir_fd=src_fd, follow_symlinks=False)
    if stat_mod.S_ISLNK(st.st_mode):
        sfd = os.open(name, _PIN_FLAGS, dir_fd=src_fd)
        try:
            os.symlink(os.readlink(name, dir_fd=src_fd), newname, dir_fd=dst_fd)
            try:
                os.utime(newname, ns=(st.st_atime_ns, st.st_mtime_ns), dir_fd=dst_fd, follow_symlinks=False)
            except OSError:
                pass
            dfd = os.open(newname, _PIN_FLAGS, dir_fd=dst_fd)
        except BaseException:
            _close(sfd)
            raise
        return dfd, sfd
    if not stat_mod.S_ISREG(st.st_mode):
        raise OSError(errno.ENOTSUP, f"Not a regular file: {name}")
    sfd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY | os.O_NOFOLLOW, dir_fd=src_fd)
    try:
        sst = os.fstat(sfd)
        if not stat_mod.S_ISREG(sst.st_mode):
            raise OSError(errno.ENOTSUP, f"Not a regular file: {name}")
        dfd = os.open(newname, _EXCL_FLAGS, 0o666, dir_fd=dst_fd)
        try:
            _copy_bytes(sfd, dfd)
            _copy_metadata(sfd, dfd, sst)
        except BaseException:
            _undo(dst_fd, newname, dfd)
            _close(dfd)
            raise
    except BaseException:
        _close(sfd)
        raise
    return dfd, sfd


def _copy_tree(
    src_fd: int, name: str, dst_fd: int, newname: str, labels: tuple[str, str] | None = None
) -> int:
    """The directory *name* of the held *src_fd* copied to *newname* in the
    held *dst_fd*: the source opened `O_DIRECTORY|O_NOFOLLOW` (a link or
    a file swapped in is refused, never followed: Linux answers `ENOTDIR`
    for a link under that pair of flags, `ELOOP` only without
    `O_DIRECTORY`), `os.mkdir` (`EEXIST` the name taken), the new
    directory opened the same way (one emptied and replaced by a link in
    between is refused the same, the entry `failed`), then the walk
    (`_walk`), everything relative to descriptors; `shutil.copytree` is
    not used, since it walks and stats by path. Errors are collected per
    entry and raised together as `shutil.Error` after the walk, the tree
    landed short (`copytree`'s own shape: `(source, target, words)` per
    entry, the two paths spelled from *labels*, the tree's two paths as
    the request named them — words for the banner, never opened).
    Returns the top source directory's
    descriptor, still open, for a move's check that the source name
    still holds what was copied (the caller closes it); closed here on
    any failure."""
    sfd = os.open(name, _DIR_NOFOLLOW_FLAGS, dir_fd=src_fd)
    try:
        os.mkdir(newname, 0o777, dir_fd=dst_fd)
        dfd = os.open(newname, _DIR_NOFOLLOW_FLAGS, dir_fd=dst_fd)
        try:
            errors: list[tuple[str, str, str]] = []
            _walk(sfd, dfd, *(labels or (name, newname)), errors)
        finally:
            _close(dfd)
        if errors:
            raise shutil.Error(errors)
    except BaseException:
        _close(sfd)
        raise
    return sfd


def _walk(sfd: int, dfd: int, src_label: str, dst_label: str, errors: list) -> None:
    """One directory of the tree copy, *sfd* into *dfd*: each entry's kind
    from a stat relative to *sfd* (never `entry.is_dir()`, whose
    `DT_UNKNOWN` fallback stats by a relative name); a directory recurses
    with its own `mkdir` and two `O_NOFOLLOW` opens under *sfd* / *dfd*
    (the top's order: the source, the `mkdir`, the new directory), a link
    and a regular file take `_copy_entry`, anything else is an error
    entry; an entry's `OSError` (a name taken inside the new tree, a link
    planted where a directory was just made: never followed) is collected in
    *errors* and the walk goes on; the directory's own metadata goes on
    *dfd* after its entries."""
    with os.scandir(sfd) as entries:
        names = [entry.name for entry in entries]
    for entry in names:
        src_path, dst_path = os.path.join(src_label, entry), os.path.join(dst_label, entry)
        try:
            st = os.stat(entry, dir_fd=sfd, follow_symlinks=False)
            if stat_mod.S_ISDIR(st.st_mode):
                csfd = os.open(entry, _DIR_NOFOLLOW_FLAGS, dir_fd=sfd)
                try:
                    os.mkdir(entry, 0o777, dir_fd=dfd)
                    cdfd = os.open(entry, _DIR_NOFOLLOW_FLAGS, dir_fd=dfd)
                    try:
                        _walk(csfd, cdfd, src_path, dst_path, errors)
                    finally:
                        _close(cdfd)
                finally:
                    _close(csfd)
            elif stat_mod.S_ISLNK(st.st_mode) or stat_mod.S_ISREG(st.st_mode):
                _close(*_copy_entry(sfd, entry, dfd, entry))
            else:
                raise OSError(errno.ENOTSUP, f"Not a regular file: {entry}")
        except OSError as err:
            errors.append((src_path, dst_path, str(err)))
    try:
        _copy_metadata(sfd, dfd, os.fstat(sfd))
    except OSError as err:
        errors.append((src_label, dst_label, str(err)))


# -- the move ------------------------------------------------------------------------------

# `renameat2(2)` through glibc's wrapper (Linux >= 3.15, glibc >= 2.28): the
# exclusive move. Looked up once, in a try: no libc, or none with the
# symbol (another libc, the macOS port), means the placeholder path.
_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
try:
    _RENAMEAT2 = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
except (OSError, AttributeError):
    _RENAMEAT2 = None
if _RENAMEAT2 is not None:
    _RENAMEAT2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    _RENAMEAT2.restype = ctypes.c_int
# What `renameat2` answers where the flag is not supported (an old kernel,
# NFS, a FUSE mount without `rename2`, an old FAT): the placeholder path.
_NOT_HERE_ERRNOS = frozenset({errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP})


def _rename_noreplace(src_fd: int, name: str | Path, dst_fd: int, newname: str | Path) -> bool:
    """`renameat2(src_fd, name, dst_fd, newname, RENAME_NOREPLACE)` (a
    name is relative to its held directory, or absolute under
    `_AT_FDCWD`): True once *name* is at *newname*; False where the flag
    is not supported (`_NOT_HERE_ERRNOS`, or no `renameat2` in libc), so
    the caller takes the placeholder path; `FileExistsError` when the
    name is taken; `ValueError` for a name holding NUL, as `os.rename`
    raises (`c_char_p` would cut it there); any other failure (`EXDEV`
    among them, the caller's copy path) its `OSError`."""
    if _RENAMEAT2 is None:
        return False
    src_b, dst_b = os.fsencode(name), os.fsencode(newname)
    if b"\x00" in src_b or b"\x00" in dst_b:
        raise ValueError("embedded null byte")
    rc = _RENAMEAT2(src_fd, src_b, dst_fd, dst_b, _RENAME_NOREPLACE)
    if rc == 0:
        return True
    code = ctypes.get_errno()
    if code == errno.EEXIST:
        raise FileExistsError(code, os.strerror(code), os.fspath(name), None, os.fspath(newname))
    if code in _NOT_HERE_ERRNOS:
        return False
    raise OSError(code, os.strerror(code), os.fspath(name), None, os.fspath(newname))


def _is_dir_entry(dir_fd: int, name: str) -> bool:
    """A directory itself, not a link to one (moved and copied as a link)."""
    return stat_mod.S_ISDIR(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode)


def _move_exclusive(
    src_fd: int, name: str, dst_fd: int, newname: str, labels: tuple[str, str] | None = None
) -> None:
    """*name* of the held *src_fd* moved to *newname* in the held *dst_fd*,
    which must not be there (a rename, or a cut's paste), in the spec's
    order: `_rename_noreplace`; where the flag is not supported, the
    placeholder path (`_move_by_placeholder`); `EXDEV` from either, the
    copy path (`_move_by_copy`). `FileExistsError` when the name is
    taken; any other `OSError` is the move's failure, which leaves
    nothing of this operation's behind (D46)."""
    try:
        if _rename_noreplace(src_fd, name, dst_fd, newname):
            return
        _move_by_placeholder(src_fd, name, dst_fd, newname)
        return
    except OSError as err:
        if err.errno != errno.EXDEV:
            raise
    _move_by_copy(src_fd, name, dst_fd, newname, labels)


def _move_by_placeholder(src_fd: int, name: str, dst_fd: int, newname: str) -> None:
    """The move where `renameat2` cannot refuse to replace: a placeholder
    made exclusively at *newname*, then `os.rename` under both held
    directories, which replaces it without following anything at the
    name. For a directory the placeholder is `os.mkdir`'s empty
    directory, which the rename replaces only while it is empty:
    `ENOTEMPTY` (or `EEXIST`) means someone put something in it, so it
    stays (it holds what is not ours) and the name counts as taken
    (`FileExistsError`); any other failure removes it and is the move's.
    For anything else the placeholder is an empty file created
    `O_CREAT|O_EXCL|O_NOFOLLOW`, its descriptor **held across the
    rename** (so its inode number cannot be reused meanwhile); a failed
    rename undoes it (`_undo`: only while the name still holds that
    inode, never something swapped over it) and is the move's failure,
    as `rename` refused it before the split. The fallback's limit,
    accepted (D44's row): a `rename` that succeeds replaces what is at
    the name at that instant, so something swapped over the placeholder
    between its creation and the rename is replaced, on filesystems
    without `RENAME_NOREPLACE` only."""
    if _is_dir_entry(src_fd, name):
        os.mkdir(newname, dir_fd=dst_fd)
        try:
            os.rename(name, newname, src_dir_fd=src_fd, dst_dir_fd=dst_fd)
        except OSError as err:
            if err.errno in (errno.ENOTEMPTY, errno.EEXIST):
                raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), name, None, newname) from err
            try:
                os.rmdir(newname, dir_fd=dst_fd)
            except OSError:
                pass
            raise
        return
    pfd = os.open(newname, _EXCL_FLAGS, 0o600, dir_fd=dst_fd)
    try:
        try:
            os.rename(name, newname, src_dir_fd=src_fd, dst_dir_fd=dst_fd)
        except OSError:
            _undo(dst_fd, newname, pfd)
            raise
    finally:
        _close(pfd)


def _move_by_copy(
    src_fd: int, name: str, dst_fd: int, newname: str, labels: tuple[str, str] | None = None
) -> None:
    """The move across filesystems: the exclusive copy of the file (both
    its descriptors held) or the tree (its top source descriptor held),
    then the source removed, as `shutil.move` does — only while the
    source name still holds what was copied (`_holds`: `lstat` against
    `fstat` of the held source). Else the name is left (it is gone, or
    what is there now is not what was copied; a tree can only have been
    replaced once it was emptied) and the move counts as done. A file
    whose source then cannot be unlinked is undone at the destination
    (`_undo` with the held descriptor: the source intact, since an
    `unlink` is all-or-nothing, nothing at the destination, a retry the
    same, D46) and the `unlink`'s error is the move's; an `unlink` that
    finds the source gone in the two calls since the check is the move
    done, never an undo (the copy would be the only one left). A tree
    whose `rmtree` fails is kept with both halves (`rmtree` removes what
    it can before it raises, so the copy may be the one complete set)."""
    if _is_dir_entry(src_fd, name):
        sfd = _copy_tree(src_fd, name, dst_fd, newname, labels)
        try:
            if _holds(src_fd, name, sfd):
                shutil.rmtree(name, dir_fd=src_fd)
        finally:
            _close(sfd)
        return
    dfd, sfd = _copy_entry(src_fd, name, dst_fd, newname)
    try:
        if _holds(src_fd, name, sfd):
            try:
                os.unlink(name, dir_fd=src_fd)
            except FileNotFoundError:
                pass
            except OSError:
                _undo(dst_fd, newname, dfd)
                raise
    finally:
        _close(dfd, sfd)


def _place(
    src_fd: int, name: str, dst_fd: int, newname: str, move: bool, labels: tuple[str, str] | None = None
) -> None:
    """One attempt at landing *name* of the held *src_fd* at *newname* in
    the held *dst_fd*, exclusively (see the module docstring):
    `FileExistsError` when the name is taken. *labels* are the two paths
    as the request named them, for a tree copy's error entries."""
    if move:
        _move_exclusive(src_fd, name, dst_fd, newname, labels)
    elif _is_dir_entry(src_fd, name):
        _close(_copy_tree(src_fd, name, dst_fd, newname, labels))
    else:
        _close(*_copy_entry(src_fd, name, dst_fd, newname))


def paste_entries(
    root: str | Path,
    dest_dir: str | Path,
    sources: list[str],
    move: bool = False,
    source_allowed: Callable[[str], bool] | None = None,
) -> list[PasteOutcome]:
    """Paste every entry in *sources* into *dest_dir* — copying, or moving
    when *move* (a cut). One outcome per source, in order: a clipboard holding
    several files is normal (it came from a file manager), and one of them
    being gone is no reason to drop the rest. *source_allowed*, when given,
    is asked about each source first (the service's confinement: a source
    inside no root it knows is `SOURCE_OUTSIDE` for a client that is not
    `local`), on the source resolved through its symlinks (the cheap
    refusal), and then about the held path of the directory the source
    is read from (the authority, D47: `_paste_one`). Each source is
    normalized first (`sub/..` is the parent, not an entry called "..").

    The placement is exclusive (D44): `paste_target` picks the first free
    name, `_place` lands on it only if it is still free, and a name taken
    in between (another request's paste, the agent, a planted symlink)
    is `FileExistsError`, answered by the next name of `unique_target`'s
    sequence — until it runs out (`NO_ROOM`).

    Symlinks are copied as symlinks rather than followed: the tree already
    refuses to show one that leaves the project, and following one here would
    quietly duplicate whatever it points at into the repo."""
    outcomes: list[PasteOutcome] = []
    dest = Path(dest_dir)
    # The destination held once for the whole clipboard (D47), at the first
    # entry that passed its path checks: opened, then confined by the
    # kernel's own path of it; a destination that cannot be held refuses
    # every entry the way `paste_target` would.
    hold: tuple[int | None, PasteError | None, str] | None = None
    try:
        for source in sources:
            src = Path(os.path.normpath(source))
            if source_allowed is not None and not source_allowed(os.path.realpath(src)):
                outcomes.append(PasteOutcome(src, None, PasteError.SOURCE_OUTSIDE))
                continue
            target, error = paste_target(root, dest, src, move)
            if target is None:
                outcomes.append(PasteOutcome(src, None, error))
                continue
            if hold is None:
                hold = _hold_destination(root, dest)
            dst_fd, dest_error, dest_message = hold
            if dest_error is not None or dst_fd is None:
                outcomes.append(PasteOutcome(src, None, dest_error, dest_message))
                continue
            outcomes.append(_paste_one(dst_fd, dest, src, target, move, source_allowed))
    finally:
        if hold is not None:
            _close(hold[0])
    return outcomes


def _hold_destination(root: str | Path, dest: Path) -> tuple[int | None, PasteError | None, str]:
    """The destination directory opened and confined inside *root* by its
    held path: `(fd, None, "")`, or `(None, error, message)` as
    `paste_target` would refuse it (`NOT_A_DIR` for one that is gone,
    `OUTSIDE` for one whose held path is outside the root)."""
    try:
        fd = _open_dir(dest)
    except (FileNotFoundError, NotADirectoryError):
        return None, PasteError.NOT_A_DIR, ""
    except OSError as err:
        return None, PasteError.FAILED, err.strerror or str(err)
    try:
        held = _held_path(fd)
    except OSError as err:
        _close(fd)
        return None, PasteError.FAILED, err.strerror or str(err)
    if held is None:
        _close(fd)
        return None, PasteError.NOT_A_DIR, ""
    if not held_inside(root, held):
        _close(fd)
        return None, PasteError.OUTSIDE, ""
    return fd, None, ""


def _paste_one(
    dst_fd: int,
    dest: Path,
    src: Path,
    target: Path,
    move: bool,
    source_allowed: Callable[[str], bool] | None,
) -> PasteOutcome:
    """One entry of a paste, its source's directory held and confined
    (`source_allowed` on the held directory's path: a source inside no
    known root, a parent swapped for a link out meanwhile, is
    `SOURCE_OUTSIDE`; one that is gone `MISSING`), then placed at the
    first free name `paste_target` chose, and on `EEXIST` at the next of
    `unique_target`'s sequence until it runs out (`NO_ROOM`)."""
    try:
        src_fd = _open_dir(src.parent)
    except (FileNotFoundError, NotADirectoryError):
        return PasteOutcome(src, None, PasteError.MISSING)
    except OSError as err:
        return PasteOutcome(src, None, PasteError.FAILED, err.strerror or str(err))
    landing: Path | None = target
    try:
        held = _held_path(src_fd)
        if held is None:
            return PasteOutcome(src, None, PasteError.MISSING)
        if source_allowed is not None and not source_allowed(held):
            return PasteOutcome(src, None, PasteError.SOURCE_OUTSIDE)
        for _attempt in range(_MAX_COPY_SUFFIXES + 1):
            try:
                _place(src_fd, src.name, dst_fd, landing.name, move, (str(src), str(landing)))
                break
            except FileExistsError:
                landing = unique_target(dest, src.name)
                if landing is None:
                    break
        else:
            landing = None
    except (OSError, shutil.Error) as err:
        message = getattr(err, "strerror", None) or str(err)
        return PasteOutcome(src, None, PasteError.FAILED, message)
    finally:
        _close(src_fd)
    if landing is None:
        return PasteOutcome(src, None, PasteError.NO_ROOM)
    return PasteOutcome(src, landing)


class MkdirError(enum.Enum):
    """Why `make_directory` did not: the values are `fs.mkdir`'s refusal
    ``reason``."""

    NOT_A_NAME = "not_a_name"
    EXISTS = "exists"
    OUTSIDE = "outside"
    NO_PARENT = "no_parent"  # the folder it would go in is not there


def make_directory(root: str | Path, path: str | Path) -> MkdirError | None:
    """`fs.mkdir`'s work: one new folder at *path*, inside *root* (its
    parent resolved through its symlinks has to be inside the root too, so
    a link out of the project makes nothing outside it); never one over
    something that is there (`os.mkdir` is exclusive: a name taken between
    the check and the call is `EXISTS` too). An `OSError` from the mkdir
    itself is the caller's."""
    path = Path(path)
    if name_error(path.name) is not None:
        return MkdirError.NOT_A_NAME
    if not is_inside(root, path.parent) or not is_inside(root, path):
        return MkdirError.OUTSIDE
    if not path.parent.is_dir():
        return MkdirError.NO_PARENT
    if _exists(path):
        return MkdirError.EXISTS
    # The hold (D47): the parent opened once and confined by the kernel's
    # own path of it; the mkdir names the entry under it, exclusive by
    # itself.
    try:
        fd = _open_dir(path.parent)
    except (FileNotFoundError, NotADirectoryError):
        return MkdirError.NO_PARENT
    try:
        held = _held_path(fd)
        if held is None:
            return MkdirError.NO_PARENT
        if not held_inside(root, held):
            return MkdirError.OUTSIDE
        try:
            os.mkdir(path.name, dir_fd=fd)
        except FileExistsError:
            return MkdirError.EXISTS
    finally:
        _close(fd)
    return None
