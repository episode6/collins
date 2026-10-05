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
name between the check and the write. So nothing here lands by a call
that replaces what it finds: a file copy opens its target
`O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW` (`_copy_file_exclusive`: the source
opened `O_NOFOLLOW` too and read only when it is a regular file, the
bytes by a `sendfile` loop with `copyfileobj` as shutil's fallback, then
`copystat`); a tree is `shutil.copytree` with that copy function and
`dirs_exist_ok` False (its own `makedirs` at the top and at every
subdirectory is the exclusive step, and a link in the source stays a
link, `os.symlink` failing `EEXIST` on a planted name).

A move (a cut's paste, and a rename, which is a move within one
directory; `_move_exclusive`) is first `renameat2(RENAME_NOREPLACE)`
through `ctypes` (`_rename_noreplace`): the one exclusive primitive with
`rename`'s own semantics for a file, a directory, a link, a FIFO alike —
no placeholder, no ownership or read permission needed, the inode kept.
Where the flag is not supported (`EINVAL`, `ENOSYS`, `ENOTSUP`, or no
such symbol in libc: NFS, a FUSE mount without `rename2`, an old FAT,
another libc) the placeholder path (`_move_by_placeholder`): a file's
target is created `O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW` and then replaced
by `os.rename`, which replaces it without following anything at the
name; a directory's is `os.mkdir` then `os.rename`, which replaces only
the empty directory just made (an entry planted inside it fails
`ENOTEMPTY`: the name counts as taken and the placeholder stays with
what is not ours). Across filesystems (`EXDEV` from either) the exclusive
copy and then the source removed, as `shutil.move` does
(`_move_by_copy`). What a failed move leaves (D46): only what this
operation made and that holds nothing of anyone else's is removed — a
file placeholder whose rename failed (only while `lstat` still shows the
inode made), a file copied across filesystems whose source then cannot
be unlinked (the entry `failed` with the `unlink`'s words, the source
intact, nothing at the destination); a tree copied across whose
`rmtree` then fails is kept with both halves on disk, since part of the
source may already be gone. `EEXIST` at a paste's target is the next
name of `unique_target`'s sequence (`paste_entries`' loop: try, and on
`EEXIST` the next; `no_room` after the hundredth); at a rename's it is
`exists`; `make_directory`'s `os.mkdir` is exclusive already. No lock
serializes the operations: with exclusive placement two requests cannot
land one name, and a lock would hold every client's rename behind a
long copy.

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
    try:
        _move_exclusive(path, landing)
    except FileExistsError:
        return None, RenameError.EXISTS
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
# What a first `sendfile` answers when it cannot serve this pair of
# descriptors (`shutil._fastcopy_sendfile`'s give-up set): `copyfileobj`.
_NO_SENDFILE_ERRNOS = frozenset({errno.EINVAL, errno.ENOTSOCK, errno.ENOSYS, errno.EOPNOTSUPP})
_COPY_CHUNK = 8 * 1024 * 1024


def _rename_noreplace(source: str | Path, target: str | Path) -> bool:
    """`renameat2(AT_FDCWD, source, AT_FDCWD, target, RENAME_NOREPLACE)`:
    True once *source* is at *target*; False where the flag is not
    supported (`_NOT_HERE_ERRNOS`, or no `renameat2` in libc), so the
    caller takes the placeholder path; `FileExistsError` when the name is
    taken; any other failure (`EXDEV` among them, the caller's copy path)
    its `OSError`."""
    if _RENAMEAT2 is None:
        return False
    rc = _RENAMEAT2(_AT_FDCWD, os.fsencode(source), _AT_FDCWD, os.fsencode(target), _RENAME_NOREPLACE)
    if rc == 0:
        return True
    code = ctypes.get_errno()
    if code == errno.EEXIST:
        raise FileExistsError(code, os.strerror(code), os.fspath(source), None, os.fspath(target))
    if code in _NOT_HERE_ERRNOS:
        return False
    raise OSError(code, os.strerror(code), os.fspath(source), None, os.fspath(target))


def _copy_bytes(src_fd: int, dst_fd: int) -> None:
    """The bytes of *src_fd* onto *dst_fd*: `os.sendfile` in a loop until
    it answers 0, `copyfileobj`'s read-and-write loop only when the first
    call fails with nothing written and an errno of `_NO_SENDFILE_ERRNOS`
    (shutil's own rule; `ENOSPC`, and any failure after bytes went, raise)."""
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


def _copy_file_exclusive(source: str | Path, target: str | Path) -> None:
    """One file (or one symlink, copied as a link) placed at *target*,
    which must not be there: the target is opened `O_CREAT|O_EXCL|
    O_NOFOLLOW`, so a name taken in between — a file, a planted symlink —
    fails `FileExistsError` and nothing is written through it; a link is
    `os.symlink`, which fails the same way. The bytes (`_copy_bytes`),
    then `copystat` (mode and times, as `copy2` keeps them). The source
    is opened `O_NOFOLLOW` (a source swapped for a link after the `islink`
    check is `ELOOP`, never read through past the caller's confinement)
    and non-blocking, and read only when it is a regular file: a FIFO or a
    device is refused rather than read (a FIFO would wait for a writer,
    `/dev/zero` would fill the disk). `shutil.copytree`'s copy function
    for a tree."""
    source, target = os.fspath(source), os.fspath(target)
    if os.path.islink(source):
        os.symlink(os.readlink(source), target)
        shutil.copystat(source, target, follow_symlinks=False)
        return
    src_fd = os.open(source, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY | os.O_NOFOLLOW)
    try:
        if not stat_mod.S_ISREG(os.fstat(src_fd).st_mode):
            raise OSError(errno.ENOTSUP, f"Not a regular file: {os.path.basename(source)}")
        dst_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NOCTTY, 0o666)
        try:
            _copy_bytes(src_fd, dst_fd)
        finally:
            os.close(dst_fd)
    finally:
        os.close(src_fd)
    shutil.copystat(source, target, follow_symlinks=False)


def _copy_tree_exclusive(source: Path, target: Path) -> None:
    """A directory tree placed at *target*: `copytree`'s own `makedirs`
    (with `dirs_exist_ok` False) is the exclusive step at the top and at
    every subdirectory, every file goes through `_copy_file_exclusive`
    and every link stays a link (`os.symlink`, exclusive too). A name
    taken at the top is `FileExistsError` (the caller's next name); one
    taken deeper is one of the `shutil.Error`'s entries (the tree landed
    short, as `copytree` leaves it)."""
    shutil.copytree(source, target, symlinks=True, copy_function=_copy_file_exclusive)


def _is_real_dir(path: Path) -> bool:
    """A directory itself, not a link to one (moved and copied as a link)."""
    return path.is_dir() and not path.is_symlink()


def _move_exclusive(source: Path, target: Path) -> None:
    """*source* moved to *target*, which must not be there (a rename, or a
    cut's paste): `_rename_noreplace` first; where the flag is not
    supported, the placeholder path (`_move_by_placeholder`); `EXDEV` from
    either, the copy path (`_move_by_copy`). `FileExistsError` when the
    name is taken; any other `OSError` is the move's failure, which leaves
    nothing of this operation's behind (D46)."""
    try:
        if _rename_noreplace(source, target):
            return
        _move_by_placeholder(source, target)
        return
    except OSError as err:
        if err.errno != errno.EXDEV:
            raise
    _move_by_copy(source, target)


def _move_by_placeholder(source: Path, target: Path) -> None:
    """The move where `renameat2` cannot refuse to replace: a placeholder
    made exclusively at *target*, then `os.rename`, which replaces it
    without following anything at the name. For a directory the
    placeholder is `os.mkdir`'s empty directory, which the rename
    replaces only while it is empty: `ENOTEMPTY` (or `EEXIST`) means
    someone put something in it, so it stays (it holds what is not ours)
    and the name counts as taken (`FileExistsError`); any other failure
    removes it and is the move's. For a file, a link or anything else
    the placeholder is an empty file created `O_CREAT|O_EXCL|O_NOFOLLOW`,
    its `(st_dev, st_ino)` kept; a failed rename removes it only while
    `lstat` still shows that inode (never something swapped over it) and
    is the move's failure, as `rename` refused it before the split."""
    if _is_real_dir(source):
        os.mkdir(target)
        try:
            os.rename(source, target)
        except OSError as err:
            if err.errno in (errno.ENOTEMPTY, errno.EEXIST):
                raise FileExistsError(
                    errno.EEXIST, os.strerror(errno.EEXIST), os.fspath(source), None, os.fspath(target)
                ) from err
            try:
                os.rmdir(target)
            except OSError:
                pass
            raise
        return
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NOCTTY, 0o600)
    try:
        st = os.fstat(fd)
        made = (st.st_dev, st.st_ino)
    finally:
        os.close(fd)
    try:
        os.rename(source, target)
    except OSError:
        try:
            now = os.lstat(target)
            if (now.st_dev, now.st_ino) == made:
                os.unlink(target)
        except OSError:
            pass
        raise


def _move_by_copy(source: Path, target: Path) -> None:
    """The move across filesystems: the exclusive copy of the file (or
    the tree), then the source removed, as `shutil.move` does. A file
    whose source then cannot be unlinked (its folder not writable) is
    removed again from the destination and the `unlink`'s error is the
    move's: the source intact, nothing at the destination, a retry the
    same again (D46). A tree whose `rmtree` fails is kept: `rmtree`
    removes what it can before it raises, so part of the source may be
    gone and the copy may be the one complete set; both halves stay, as
    `shutil.move` left them."""
    if _is_real_dir(source):
        _copy_tree_exclusive(source, target)
        shutil.rmtree(source)
        return
    _copy_file_exclusive(source, target)
    try:
        os.unlink(source)
    except OSError:
        try:
            os.unlink(target)
        except OSError:
            pass
        raise


def _place(source: Path, target: Path, move: bool) -> None:
    """One attempt at landing *source* at *target*, exclusively (see the
    module docstring): `FileExistsError` when the name is taken."""
    if move:
        _move_exclusive(source, target)
    elif _is_real_dir(source):
        _copy_tree_exclusive(source, target)
    else:
        _copy_file_exclusive(source, target)


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
    `local`), on the source resolved through its symlinks. Each source is
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
    for source in sources:
        src = Path(os.path.normpath(source))
        if source_allowed is not None and not source_allowed(os.path.realpath(src)):
            outcomes.append(PasteOutcome(src, None, PasteError.SOURCE_OUTSIDE))
            continue
        target, error = paste_target(root, dest_dir, src, move)
        if target is None:
            outcomes.append(PasteOutcome(src, None, error))
            continue
        try:
            for _attempt in range(_MAX_COPY_SUFFIXES + 1):
                try:
                    _place(src, target, move)
                    break
                except FileExistsError:
                    target = unique_target(dest_dir, src.name)
                    if target is None:
                        break
            else:
                target = None
        except (OSError, shutil.Error) as err:
            message = getattr(err, "strerror", None) or str(err)
            outcomes.append(PasteOutcome(src, None, PasteError.FAILED, message))
            continue
        if target is None:
            outcomes.append(PasteOutcome(src, None, PasteError.NO_ROOM))
            continue
        outcomes.append(PasteOutcome(src, target))
    return outcomes


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
    try:
        os.mkdir(path)
    except FileExistsError:
        return MkdirError.EXISTS
    return None
