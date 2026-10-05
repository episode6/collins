# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The uploads directory: what a client dropped or pasted, on the
service's machine (split-service spec §3.11, §3.23; PR-2.7).

A dropped or pasted image, and a dropped file, has to exist on the
service for the CLI to read it, so the client sends its bytes
(`PUT /api/upload?session=<id>&name=<basename>`, `api.server`) and
mentions the path that comes back. The bytes land here:

- ``~/.local/share/collins/uploads/<session id>/<unique name>`` for an
  upload with a session id, removed with the session (`remove`, called by
  `store.SessionStore.trash_many` and `delete`, which the archive sweep
  runs through too);
- ``~/.local/share/collins/uploads/_pending/<unique name>`` for one with
  none (D37: the new-chat screen, a tab whose id has not resolved yet),
  never moved — the typed mention already names the path — and swept once
  older than `dropimages.PRUNE_AFTER_SECONDS` (`sweep_pending`, the
  service's housekeeping tick).

The name is reduced to a basename (`safe_name`) and the file is written
by file descriptor (`write`: the directory opened ``O_DIRECTORY |
O_NOFOLLOW``, the file created ``O_EXCL | O_NOFOLLOW`` relative to it), so
no name a client sends reaches outside its directory and no symlink
planted there is followed. `MAX_BYTES` caps one upload (64 MiB).

Stdlib only: the service writes it, the tests drive it headless.
"""

from __future__ import annotations

import errno
import os
import re
import shutil
import stat
import time
from collections.abc import Callable
from pathlib import Path

from .dropimages import PRUNE_AFTER_SECONDS

# The most one upload may be (§3.11).
MAX_BYTES = 64 * 1024 * 1024
# The directory an upload with no session id lands in (D37).
PENDING = "_pending"
# The longest name kept (bytes, UTF-8): a filesystem's NAME_MAX less the
# room a "-NNN" uniquifier takes.
NAME_MAX = 200
# How many "-N" names are tried before giving up (a burst of drops of one
# name; past it something is wrong).
MAX_NAME_ATTEMPTS = 1000
# A session id as a directory name: what the CLI mints (a UUID), with room
# for an id of another shape, never a path.
_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def root() -> Path:
    """``$XDG_DATA_HOME/collins/uploads`` (``COLLINS_UPLOADS_DIR`` for tests
    and captures)."""
    override = os.environ.get("COLLINS_UPLOADS_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "collins" / "uploads"


def valid_session(session_id: object) -> bool:
    """Whether *session_id* can name an upload directory (and is not the
    pending one's)."""
    return isinstance(session_id, str) and bool(_SESSION_RE.match(session_id)) and session_id != PENDING


def directory(session_id: str | None) -> Path:
    """The directory an upload for *session_id* lands in (`PENDING`'s for
    None)."""
    if session_id is None:
        return root() / PENDING
    if not valid_session(session_id):
        raise ValueError("not a session id")
    return root() / session_id


def safe_name(name: object) -> str | None:
    """*name* reduced to a basename a file can be created under, or None
    when nothing usable is left: the last path component, control
    characters dropped, no leading dots (a hidden file nobody sees), at
    most `NAME_MAX` bytes with the extension kept."""
    if not isinstance(name, str):
        return None
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = _CONTROL_RE.sub("", base).strip().lstrip(".")
    if not base:
        return None
    stem, dot, ext = base.rpartition(".")
    if not dot or not stem or len(ext) > 16:
        stem, ext = base, ""
    while len((stem + ("." + ext if ext else "")).encode("utf-8")) > NAME_MAX and stem:
        stem = stem[:-1]
    if not stem:
        return None
    return stem + ("." + ext if ext else "")


def _candidates(name: str):
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    suffix = "." + ext if ext else ""
    yield name
    for attempt in range(2, MAX_NAME_ATTEMPTS + 1):
        yield f"{stem}-{attempt}{suffix}"


def _open_dir(path: Path) -> int:
    """*path*, made 0700 when missing (its parents too), opened as a
    directory without following a symlink at its last component."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)


def write(session_id: str | None, name: object, data: bytes) -> Path:
    """Write *data* under a fresh name made from *name* in the session's
    upload directory (`PENDING`'s with no id) and return the file's path.
    Raises `ValueError` for a name or id that names nothing, a body past
    `MAX_BYTES`; `OSError` when the disk refuses."""
    if len(data) > MAX_BYTES:
        raise ValueError("too large")
    safe = safe_name(name)
    if safe is None:
        raise ValueError("not a file name")
    folder = directory(session_id)
    root_fd = _open_dir(folder.parent)
    try:
        try:
            os.mkdir(folder.name, 0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        dir_fd = os.open(folder.name, flags, dir_fd=root_fd)
    finally:
        os.close(root_fd)
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        for candidate in _candidates(safe):
            try:
                fd = os.open(candidate, flags, 0o600, dir_fd=dir_fd)
            except FileExistsError:
                continue
            try:
                view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
            except OSError:
                os.close(fd)
                try:
                    os.unlink(candidate, dir_fd=dir_fd)
                except OSError:
                    pass
                raise
            os.close(fd)
            return folder / candidate
        raise OSError(errno.EEXIST, f"no free name for {safe}")
    finally:
        os.close(dir_fd)


def inside(path: str | os.PathLike, session_id: str | None) -> bool:
    """Whether *path* (already resolved) is inside the session's upload
    directory or the pending one."""
    real = os.path.realpath(os.fspath(path))
    folders = [directory(None)]
    if valid_session(session_id):
        folders.append(directory(session_id))
    for folder in folders:
        base = os.path.realpath(folder)
        if real.startswith(base.rstrip(os.sep) + os.sep):
            return True
    return False


def remove(session_id: str, trash: Callable[[str], str | None] | None = None) -> str | None:
    """Remove the session's upload directory: to the trash through *trash*
    (`store._trash_file`'s shape: the path, an error or None) when given,
    else unlinked whole. The pending directory is never a session's.
    Returns the error or None (nothing there is not an error)."""
    if not valid_session(session_id):
        return None
    folder = directory(session_id)
    try:
        info = os.lstat(folder)
    except FileNotFoundError:
        return None
    except OSError as err:
        return str(err)
    if not stat.S_ISDIR(info.st_mode):
        return None  # a symlink or a file planted under the name: not ours to follow
    if trash is not None:
        error = trash(str(folder))
        if error is None:
            return None
    try:
        shutil.rmtree(folder)
    except FileNotFoundError:
        return None
    except OSError as err:
        return str(err)
    return None


def sweep_pending(now: float | None = None, max_age: float = PRUNE_AFTER_SECONDS) -> list[str]:
    """Unlink the pending uploads older than *max_age* (D37: a week).
    Never raises; returns the names that went."""
    now = time.time() if now is None else now
    folder = directory(None)
    gone: list[str] = []
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return gone
    for entry in entries:
        try:
            old = now - entry.stat(follow_symlinks=False).st_mtime > max_age
            if entry.is_file(follow_symlinks=False) and old:
                os.unlink(entry.path)
                gone.append(entry.name)
            elif entry.is_symlink():
                os.unlink(entry.path)  # nothing writes a link here
        except OSError:
            continue
    return gone
