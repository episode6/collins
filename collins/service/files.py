# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Files over the API, the service's end (split-service spec §3.23).

Started in PR-2.1 with the one request the git page needs, `fs.trash`
(a discarded untracked file goes to the trash on the service's machine,
through `Gio.File.trash` — never an unlink — the same mover
`gitops.run_plan` is handed for OP_TRASH), and the confinement rule every
`fs.*` and `git.*` request shares: a path a client names must be inside
a root the service knows (`allowed`): a live session's working
directory, a session's cwd or project root the store knows, or anything
for a client that proved it is `local` (§3.2: same machine, same uid, a
client already trusted with a shell). `is_inside` resolves symlinks, so a
link out of a root is outside it.

PR-2.3 adds the editor's requests, served by `Files`:

- `fs.read {path, max}`: the file's bytes, decoded as UTF-8 or, when they
  are not UTF-8, as latin-1 with the encoding flagged (so the save writes
  them back the same way); `binary` (a NUL in the first 8 KiB) with an
  empty text; the `mtime` (microseconds) the save that follows expects
  and the `size`. Refused `refused` over `max` (FILE_TEXT_MAX at most),
  for a path that is not a regular file, and outside every root.
- `fs.write {path, text, expect_mtime, encoding}`: the text written the
  way `GtkSource.FileSaver` wrote it (`write_file`: in place for a hard
  link, a read-only directory or another owner's file, else by a
  temporary file and one `os.replace` with the old mode, a new file
  created under the umask), **after** the file's mtime is compared with
  `expect_mtime` — right before the write, off the descriptor or the
  stat the replace follows: a file that moved underneath the client is
  refused `stale` and left exactly as it is (the client raises its
  "changed on disk" dialog and asks again with `expect_mtime: null` to
  overwrite). The reply's mtime and size are the written descriptor's. A
  text latin-1 cannot carry is written as UTF-8 and the reply says so.
- `fs.watch {path, kind: file, handle, mtime}` / `fs.unwatch {handle}`:
  one `Gio.FileMonitor` per client and handle (`_FileWatch`, the editor's
  monitor moved here; at most MAX_WATCHES_PER_CLIENT), seeded by a stat
  on a thread compared against the client's `mtime` (a file that already
  differs is one `file-changed` at once), then debounced 300 ms and one
  `file-changed {handle, path, mtime, size, gone}` per burst whose stat
  moved.
- Confinement for the read and the write runs on the worker, against the
  roots computed on the main loop (`confined`): the path is resolved
  through its symlinks right before the open, so a link swapped in
  between points nowhere outside a root.

PR-2.4 adds the tree's, quick open's and the roots' requests:

- `fs.stat {path, root}`: what is at the path (`stat_path`: the kind a
  symlink names, a file's size, the mtime) and `inside`, whether it
  resolves inside *root* (the asking editor's) or, with none, inside any
  root the service knows. Any path may be stat'ed: a stat leaks nothing a
  shell could not (§3.23).
- `fs.list {path, hidden, root}`: `projectfiles.list_entries` of the
  directory (the tree's order and skip rules, at most FS_LIST_MAX and
  `truncated`), each entry marked `ignored` by one batched check-ignore
  in it (`gitinfo.ignored_names`, which the tree ran on the main loop
  before). Confined to `allowed` and to *root* (`is_inside` on the
  resolved directory, on the worker).
- `fs.walk {root, hidden}`: `projectfiles.walk_files` of an allowed
  root, at most FS_WALK_MAX files and `truncated`.
- `fs.watch {path, kind: dir, handle}`: `_DirWatch`, a
  `monitor_directory` per client and handle (under the same bound as the
  file watches), debounced 300 ms into one `dir-changed {handle, path}`
  per burst. The client lists the directory again; nothing is stat'ed.
  Every watch is confined on a thread and installed when that lands
  (`_pending`), and one Gio cannot make a monitor for is refused.

PR-2.5 adds the tree's file operations, the rules in `projectfiles`
(moved there from `editorfiles`, the client's module):

- `fs.rename {path, target, root}`: `projectfiles.rename_entry`: a rename
  in place (the target is the same directory's and exactly the name
  asked, else refused `not_a_name`), never over anything (`exists`: at
  the check, and at the placement, which is exclusive, D44), of an entry
  that is there (`missing`), both resolved inside *root* (`outside`: a
  rename across roots, or through a link out). A refusal carries the
  rule as its `reason` (`protocol.FS_RENAME_REASONS`) for the client's
  own words. The reply's `mtime` is the renamed file's (null for a
  folder): an editor holding it open takes it.
- `fs.paste {entries, target, cut, root}`: `projectfiles.paste_entries`
  into the folder *target* inside *root*: a copy, or a move for a cut,
  never over anything (a taken name lands as "name (copy)", and the
  placement is exclusive against every writer: a name taken in between
  is the next "(copy N)", D44). Each source is confined on the worker to
  `allowed` (a client that is not `local` may name a source only inside
  a root the service knows: `source_outside`, per entry; one inside
  *another* known root is allowed, D43), asked twice
  (`source_confinement`): about the source resolved through its links,
  then about the held path of the directory it is read from (D47:
  `projectfiles` works under held directory descriptors, so a parent
  swapped for a link out after the first answer is refused by the
  second; neither path is resolved again, D48). The rule is therefore
  "a source whose parent directory is inside a known root": a root
  itself, whose parent is inside no root, is no source for a client
  that is not `local` (no tree row names one). The `results`, one per entry,
  carry its landing with the landed file's `mtime` (null for a folder or
  a failure) or its `PasteError` (an open string: a client maps one it
  does not know to `failed`), and travel chunked past a frame. At most
  `FS_PASTE_MAX` entries per request; the client sends a longer clipboard
  in slices (D42).
- `fs.mkdir {path, root}`: `projectfiles.make_directory`: one folder
  inside *root*, never over anything; a refusal's `reason` is one of
  `protocol.FS_MKDIR_REASONS`.
- Each request's *root* is confined on the worker (`_confine`, as the
  listing's is), never on the main loop: the known roots are computed
  there and every resolving check runs on the thread.

PR-2.6 adds the bare root-name links' read:

- `fs.names {root}`: `names_reply`, the names in an allowed root that are
  not directories (a symlink to a directory is one), at most
  FS_NAMES_MAX and `truncated` (the sorted first ones: collected, sorted,
  then cut, D45), on a thread. The client holds a
  `fs.watch {kind: dir}` on the root and asks again on its `dir-changed`.

**Nothing blocks the main loop**: every read, write and stat runs on a
daemon thread and answers through a `protocol.Deferred` settled on the
main loop at `PRIORITY_DEFAULT` (`_later`; a worker that raises settles a
`failed` refusal, so the client is never left waiting).

GLib and Gio only; nothing here imports GTK.
"""

from __future__ import annotations

import errno
import logging
import os
import stat as stat_mod
import tempfile
import threading
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from gi.repository import Gio, GLib

from .. import gitinfo, gitops
from ..api import protocol
from ..projectfiles import (
    MkdirError,
    PasteOutcome,
    RenameError,
    held_inside,
    is_inside,
    list_entries,
    make_directory,
    paste_entries,
    rename_entry,
    walk_files,
)
from ..sessions import worktree_project_root

log = logging.getLogger(__name__)

# A NUL in this many leading bytes makes a file binary (editorfiles'
# sniff, now the service's).
BINARY_SNIFF_BYTES = 8192
# The editor's monitor debounce, now the service's: long enough that a
# run of quick writes (an agent's edit tool) coalesces into one check.
WATCH_DEBOUNCE_MS = 300


def roots(core) -> list[str]:
    """Every directory a client may name a path under: the live sessions'
    working directories (where they started and where their agent is
    now), and every session the store knows, with its project root."""
    found: list[str] = []
    for record in list(getattr(core, "sessions", {}).values()):
        session = getattr(record, "session", None)
        for cwd in (getattr(session, "cwd", None), _agent_cwd(session)):
            if cwd:
                found.append(str(cwd))
    store = getattr(core, "store", None)
    if store is not None:
        try:
            sessions = store.all_sessions()
        except Exception:
            sessions = []
        for session in sessions:
            cwd = getattr(session, "cwd", None)
            if cwd:
                found.append(str(cwd))
                project = worktree_project_root(cwd)
                if project:
                    found.append(project)
    return found


def _agent_cwd(session) -> str | None:
    try:
        return session.current_agent_cwd() if session is not None else None
    except Exception:
        return None


def allowed(core, client, path: str | Path) -> bool:
    """Whether *client* may name *path* (see the module docstring)."""
    if getattr(client, "local", False):
        return True
    return any(is_inside(root, path) for root in roots(core))


def allowed_cwd(core, client, cwd: object) -> str | None:
    """*cwd* as the directory a request runs in, or None when it is not
    text, not a directory the service can see, or not an allowed one."""
    if not isinstance(cwd, str) or not cwd or not os.path.isdir(cwd):
        return None
    return cwd if allowed(core, client, cwd) else None


def trash_paths(root: str, paths: Sequence[str]) -> gitops.GitResult:
    """`gitops.run_plan`'s mover for OP_TRASH on the service: each path
    under *root* to the system trash through Gio (never an unlink — the
    file exists nowhere else). Worker thread; the first failure is the
    answer."""
    for path in paths:
        try:
            Gio.File.new_for_path(os.path.join(root, path)).trash(None)
        except GLib.Error as exc:
            return gitops.GitResult(False, "", exc.message or "trash failed")
    return gitops.GitResult(True, "", "")


def trash_absolute(paths: Iterable[str]) -> tuple[list[str], str | None]:
    """`fs.trash`'s work: each absolute path to the trash, stopping at
    the first failure: (the paths trashed, the failure's words or None)."""
    done: list[str] = []
    for path in paths:
        try:
            Gio.File.new_for_path(path).trash(None)
        except GLib.Error as exc:
            return done, exc.message or "trash failed"
        done.append(path)
    return done, None


def handle_trash(core, client, message: protocol.Message, later=None) -> dict | protocol.Deferred:
    """`fs.trash {paths}`: every path confined (`allowed`), then trashed;
    a path the machine's trash refuses (Gio refuses "system internal"
    mounts, a tmpfs /tmp included) is `failed` with Gio's words, and
    nothing is unlinked in its place in this PR (`removed` stays empty:
    the unlink behind a confirmation is a later chunk's). The trash
    itself (a copy across filesystems, a slow mount) runs off the main
    loop through *later* (`gitfeed.GitFeed._later`: a thread and a
    `Deferred`); with none it runs inline (a test's)."""
    paths = [str(p) for p in message.get("paths") or ()]
    for path in paths:
        if not os.path.isabs(path) or not allowed(core, client, path):
            return protocol.refuse(
                message.id, protocol.ERROR_REFUSED, "{path} is outside every root the service knows",
                {"path": path[: protocol.ARG_TEXT_MAX]},
            )
    re_id = message.id

    def work() -> dict:
        trashed, failure = trash_absolute(paths)
        if failure is not None:
            return protocol.refuse(
                re_id, protocol.ERROR_FAILED, "Could not move to the trash: {error}",
                {"error": failure[: protocol.ARG_TEXT_MAX]},
            )
        return protocol.reply(re_id, trashed=trashed, removed=[])

    return later("fs-trash", work, re_id) if later is not None else work()


# -- the editor's files (PR-2.3) -----------------------------------------------------------


def mtime_us(st: os.stat_result) -> int:
    """A stat's mtime as the wire carries it: microseconds since the epoch
    (`protocol._MTIME`; nanoseconds would pass the integer bound)."""
    return st.st_mtime_ns // 1000


def file_stat(path: str) -> tuple[int | None, int | None, bool]:
    """(mtime, size, gone) of *path*: what `file-changed` carries and what
    `fs.write` compares. A path that is not a regular file is gone."""
    try:
        st = os.stat(path)
    except OSError:
        return None, None, True
    if not stat_mod.S_ISREG(st.st_mode):
        return None, None, True
    return mtime_us(st), st.st_size, False


class ReadRefused(Exception):
    """`read_file` could not answer: the refusal's code, words and args.
    The words name no file: the client puts the name in front."""

    def __init__(self, error: str, msgid: str, details: dict | None = None) -> None:
        super().__init__(msgid)
        self.error = error
        self.msgid = msgid
        self.details = details or {}  # not `args`: that is BaseException's


OUTSIDE_MSGID = "The path is outside every root the service knows"
TOO_LARGE_MSGID = "The file is too large to open in the editor ({size} bytes, over {max})"


def confined(path: str, roots: Sequence[str] | None) -> str:
    """*path* resolved through its symlinks (the file itself), or a
    `ReadRefused` ``refused`` when *roots* (the service's, computed on the
    main loop) is given and the resolved file is inside none of them. The
    check runs here, on the worker, right before the open: a link swapped
    between the request and the read points nowhere outside."""
    real = os.path.realpath(path)
    if roots is not None and not any(is_inside(root, real) for root in roots):
        raise ReadRefused(protocol.ERROR_REFUSED, OUTSIDE_MSGID)
    return real


def _os_error(exc: OSError) -> str:
    return (exc.strerror or str(exc))[: protocol.ARG_TEXT_MAX]


def read_file(path: str, max_bytes: int, roots: Sequence[str] | None = None) -> dict:
    """`fs.read`'s work (a worker thread): the reply's fields, or a
    `ReadRefused`. The open is non-blocking and the file's kind is read
    off the open descriptor, so a FIFO or a device swapped in for the file
    is refused rather than waited on."""
    real = confined(path, roots)
    try:
        fd = os.open(real, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)
    except FileNotFoundError:
        raise ReadRefused(protocol.ERROR_GONE, "The file is not there") from None
    except OSError as exc:
        raise ReadRefused(
            protocol.ERROR_FAILED, "Couldn't read the file: {error}", {"error": _os_error(exc)}
        ) from None
    try:
        st = os.fstat(fd)
        if not stat_mod.S_ISREG(st.st_mode):
            raise ReadRefused(protocol.ERROR_REFUSED, "The path is not a file")
        if st.st_size > max_bytes:
            raise ReadRefused(
                protocol.ERROR_REFUSED, TOO_LARGE_MSGID, {"size": st.st_size, "max": max_bytes},
            )
        with os.fdopen(fd, "rb") as fh:
            fd = -1
            data = fh.read(max_bytes + 1)
    except OSError as exc:
        raise ReadRefused(
            protocol.ERROR_FAILED, "Couldn't read the file: {error}", {"error": _os_error(exc)}
        ) from None
    finally:
        if fd >= 0:
            os.close(fd)
    if len(data) > max_bytes:  # grew between the stat and the read
        raise ReadRefused(
            protocol.ERROR_REFUSED, TOO_LARGE_MSGID, {"size": len(data), "max": max_bytes},
        )
    fields = {"mtime": mtime_us(st), "size": len(data), "binary": False}
    if b"\x00" in data[:BINARY_SNIFF_BYTES]:
        return {**fields, "text": "", "encoding": protocol.FILE_ENCODING_UTF8, "binary": True}
    try:
        text = data.decode("utf-8")
        encoding = protocol.FILE_ENCODING_UTF8
    except UnicodeDecodeError:
        text = data.decode("latin-1")
        encoding = protocol.FILE_ENCODING_LATIN1
    return {**fields, "text": text, "encoding": encoding}


class WriteStale(Exception):
    """`write_file` found the file's mtime moved from `expect_mtime`."""

    def __init__(self, mtime: int | None) -> None:
        super().__init__("stale")
        self.mtime = mtime


def _encode(text: str, encoding: str) -> tuple[bytes, str]:
    try:
        return text.encode(encoding), encoding
    except (UnicodeEncodeError, LookupError):
        return text.encode("utf-8"), protocol.FILE_ENCODING_UTF8


def _write_in_place(fd: int, data: bytes, expect_mtime: int | None) -> os.stat_result:
    """*data* over the open file *fd*, which is not truncated until the
    compare passed: the mtime is read off the descriptor right before the
    write."""
    st = os.fstat(fd)
    if expect_mtime is not None and mtime_us(st) != expect_mtime:
        raise WriteStale(mtime_us(st))
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]
    os.ftruncate(fd, len(data))
    os.fsync(fd)
    return os.fstat(fd)


def write_file(
    path: str, text: str, expect_mtime: int | None, encoding: str, roots: Sequence[str] | None = None
) -> dict:
    """`fs.write`'s work (a worker thread): the text to the file *path*
    names, through any symlink (`confined`: a link inside the project to a
    file inside it is written as the file, never turned into a copy —
    what `g_file_replace` did), after the mtime check (`WriteStale` when it
    moved, nothing written; an OSError is the caller's `failed`).

    How it is written follows the saver this replaces: a file that is one
    of several hard links, one in a directory the service cannot write
    (a writable file in a read-only directory), or one another user or
    group owns is written **in place** (open, compare off the descriptor,
    write, truncate, fsync), so every link sees the text and the owner and
    mode stay; any other existing file by a temporary file beside it,
    given the old mode, compared again right before the one `os.replace`
    (the window between the compare and the write is the replace alone;
    a file the user may not write, 0444 of their own, is refused
    Permission denied as `g_file_replace` refused it, never swapped out);
    a file that is not there is created ``0o666`` under the umask, as
    GLib creates one. The reply's fields: the mtime and size read off the
    written descriptor (the temporary's, before the replace: the inode
    keeps them through the rename, so the next `file-changed` and the
    next save compare against exactly what was written), and the encoding
    written (UTF-8 when latin-1 could not carry the text)."""
    real = confined(path, roots)
    data, encoding = _encode(text, encoding)
    try:
        st: os.stat_result | None = os.stat(real)
    except FileNotFoundError:
        st = None
    if st is not None and not stat_mod.S_ISREG(st.st_mode):
        raise ReadRefused(protocol.ERROR_REFUSED, "The path is not a file")
    if expect_mtime is not None and (st is None or mtime_us(st) != expect_mtime):
        raise WriteStale(mtime_us(st) if st is not None else None)
    directory = os.path.dirname(real) or "."
    if st is None:
        fd = os.open(real, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NOCTTY, 0o666)
        try:
            written = _write_in_place(fd, data, None)
        finally:
            os.close(fd)
        return {"mtime": mtime_us(written), "size": written.st_size, "encoding": encoding}
    in_place = (
        st.st_nlink > 1
        or not os.access(directory, os.W_OK)
        or (st.st_uid, st.st_gid) != (os.getuid(), os.getgid())
    )
    if in_place:
        fd = os.open(real, os.O_WRONLY | os.O_NOFOLLOW | os.O_NOCTTY)
        try:
            written = _write_in_place(fd, data, expect_mtime)
        finally:
            os.close(fd)
        return {"mtime": mtime_us(written), "size": written.st_size, "encoding": encoding}
    if not os.access(real, os.W_OK):
        # A read-only file of the user's own (0444 in a writable directory):
        # the replace could swap it out, but the saver this replaces refused
        # with Permission denied, and so does this (a chmod is a decision).
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), real)
    fd, temp = tempfile.mkstemp(prefix=".collins-", suffix=".tmp", dir=directory)
    try:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
            os.fsync(fd)
            os.fchmod(fd, stat_mod.S_IMODE(st.st_mode))
            written = os.fstat(fd)
        finally:
            os.close(fd)
        # The compare again, as close to the replace as a stat can be.
        now = file_stat(real)
        if expect_mtime is not None and (now[2] or now[0] != expect_mtime):
            raise WriteStale(now[0])
        os.replace(temp, real)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
    return {"mtime": mtime_us(written), "size": written.st_size, "encoding": encoding}


# -- the tree, quick open and roots (PR-2.4) ------------------------------------------------


def stat_path(path: str) -> dict:
    """`fs.stat`'s kind, size and mtime of *path* (a worker thread). The
    kind follows a symlink to what it names (`file`, `dir`, `other`);
    `symlink` is a link that names nothing (dangling, a loop), `missing`
    nothing at all. The size is a file's, the mtime anything's that is
    there (microseconds, `mtime_us`)."""
    try:
        st = os.stat(path)
    except OSError:
        try:
            linked = stat_mod.S_ISLNK(os.lstat(path).st_mode)
        except OSError:
            linked = False
        return {"kind": "symlink" if linked else "missing", "size": None, "mtime": None}
    if stat_mod.S_ISREG(st.st_mode):
        return {"kind": "file", "size": st.st_size, "mtime": mtime_us(st)}
    kind = "dir" if stat_mod.S_ISDIR(st.st_mode) else "other"
    return {"kind": kind, "size": None, "mtime": mtime_us(st)}


def source_confinement(roots: Sequence[str] | None) -> Callable[[str], bool]:
    """`paste_entries`' *source_allowed* for a client whose known roots are
    *roots* (None for a `local` one, which may name anything): whether a
    path is one of them or under one. `paste_entries` asks twice per
    source, about the source resolved through its links and then about
    the held path of the directory it reads from, and both are canonical
    already, so the answer compares the path as it stands against each
    root's realpath (`projectfiles.held_inside`) and never resolves it
    again: resolved again, a held directory outside every root whose old
    path was meanwhile replaced by a link into one would read as inside
    (D47, D48). Run on the worker."""

    def source_allowed(path: str) -> bool:
        return roots is None or any(held_inside(root, path) for root in roots)

    return source_allowed


def _confine(roots: Sequence[str] | None, *paths: str) -> None:
    """Each of *paths* resolves inside one of *roots* (the service's,
    computed on the main loop; None for a `local` client, which may name
    anything), else a `ReadRefused` ``refused``. Run on the worker: the
    containment check resolves every root and path through its symlinks."""
    if roots is None:
        return
    for path in paths:
        if not any(is_inside(root, path) for root in roots):
            raise ReadRefused(protocol.ERROR_REFUSED, OUTSIDE_MSGID)


def list_reply(path: str, hidden: bool, root: str) -> dict:
    """`fs.list`'s fields (a worker thread): the entries with git's
    ignored names marked, and whether the listing was cut. A directory
    that does not resolve inside *root* is refused (`ReadRefused`)."""
    if not is_inside(root, path):
        raise ReadRefused(protocol.ERROR_REFUSED, OUTSIDE_MSGID)
    if not os.path.isdir(path):
        raise ReadRefused(protocol.ERROR_GONE, protocol.FOLDER_GONE_MSGID)
    entries, truncated = list_entries(path, hidden, root=root)
    ignored = gitinfo.ignored_names(path, [name for name, _kind in entries]) if entries else set()
    return {
        "entries": [{"name": name, "kind": kind, "ignored": name in ignored} for name, kind in entries],
        "truncated": truncated,
    }


def walk_reply(root: str, hidden: bool) -> dict:
    """`fs.walk`'s fields (a worker thread)."""
    if not os.path.isdir(root):
        raise ReadRefused(protocol.ERROR_GONE, protocol.FOLDER_GONE_MSGID)
    paths, truncated = walk_files(root, hidden, cap=protocol.FS_WALK_MAX)
    return {"paths": paths, "truncated": truncated}


def names_reply(root: str) -> dict:
    """`fs.names`'s fields (a worker thread): the names in *root* that are
    not directories, a symlink to a directory counting as one (what the
    client's `os.scandir` test did). A name over FS_NAME_MAX or one that
    is not text is left out. Every name is collected and sorted before
    the cut (D45, PR-2.8): past FS_NAMES_MAX the reply is the sorted first
    FS_NAMES_MAX and says `truncated`, the same names on every ask
    (`scandir`'s own order is the directory's, and differs between asks
    and filesystems)."""
    names: list[str] = []
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.is_dir():
                    continue
                name = entry.name
                if len(name) > protocol.FS_NAME_MAX:
                    continue
                try:
                    name.encode("utf-8")
                except UnicodeEncodeError:
                    continue
                names.append(name)
    except OSError:
        raise ReadRefused(protocol.ERROR_GONE, "The folder is not there") from None
    names.sort()
    truncated = len(names) > protocol.FS_NAMES_MAX
    return {"names": names[: protocol.FS_NAMES_MAX], "truncated": truncated}


def _dispatch_default(fn: Callable[[], object]) -> None:
    GLib.idle_add(lambda: fn() and False, priority=GLib.PRIORITY_DEFAULT)


def _spawn_default(fn: Callable[[], None], name: str) -> None:
    threading.Thread(target=fn, name=name, daemon=True).start()


# The most watches one client may hold: every tab's open files, its tree's
# expanded folders (a collapsed one's watch is dropped) and quick open's
# roots, on one connection. One Gio monitor is one inotify watch, and the
# kernel's default allows 8192 per user and up to a million on current
# systemd; a client past this has leaked them (review of PR 611: 512 was
# shared by every tab).
MAX_WATCHES_PER_CLIENT = 4096

# The words of a refused rename or mkdir, by the rule it broke (the
# client has its own, keyed by the refusal's `reason`; these are for a
# client that does not). They name no file: the client puts the name in
# front.
RENAME_MSGIDS = {
    RenameError.EMPTY: "A name is needed",
    RenameError.NOT_A_NAME: "A rename keeps its folder and takes a bare name",
    RenameError.EXISTS: "The name is taken",
    RenameError.MISSING: "The entry is not there",
    RenameError.OUTSIDE: OUTSIDE_MSGID,
}
MKDIR_MSGIDS = {
    MkdirError.NOT_A_NAME: "A folder takes a bare name",
    MkdirError.EXISTS: "The name is taken",
    MkdirError.OUTSIDE: OUTSIDE_MSGID,
    MkdirError.NO_PARENT: "The folder it would go in is not there",
}


def _paste_result(outcome: PasteOutcome) -> dict:
    """One `fs.paste` result (`protocol._FS_PASTE_RESULT`) from an outcome:
    where it landed, the landed file's mtime (null for a folder, a link
    that names no file, or a failure: what an editor holding a moved file
    open takes, `_retarget_open`), the rule it broke."""
    mtime = None
    if outcome.target is not None:
        mtime, _size, gone = file_stat(str(outcome.target))
        if gone:
            mtime = None
    return {
        "source": str(outcome.source),
        "target": None if outcome.target is None else str(outcome.target),
        "mtime": mtime,
        "error": None if outcome.error is None else outcome.error.value,
        "message": outcome.message[: protocol.ARG_TEXT_MAX],
    }


class Files:
    """The editor's `fs.*` requests and the file watches (see the module
    docstring). *core* is the `ServiceCore` (for the roots a path is
    confined to); *dispatch* lands a thread's answer on the main loop and
    *spawn* runs one (a test's inline)."""

    def __init__(
        self,
        core,
        dispatch: Callable[[Callable[[], object]], None] | None = None,
        spawn: Callable[[Callable[[], None], str], None] | None = None,
    ) -> None:
        self.core = core
        self._dispatch = dispatch or _dispatch_default
        self._spawn = spawn or _spawn_default
        self._watches: dict[tuple[int, str], _FileWatch | _DirWatch] = {}
        # (client, handle) -> the token of the watch being confined on a
        # thread: an `fs.unwatch`, a newer `fs.watch` of the handle or the
        # client going away while it is out makes its landing install nothing.
        self._pending: dict[tuple[int, str], object] = {}

    # -- routing -------------------------------------------------------------------------

    def handle(self, message: protocol.Message, client) -> dict | protocol.Deferred:
        kind = message.type
        if kind == "fs.read":
            return self.read(message, client)
        if kind == "fs.write":
            return self.write(message, client)
        if kind == "fs.watch":
            return self.watch(message, client)
        if kind == "fs.unwatch":
            return self.unwatch(message, client)
        if kind == "fs.stat":
            return self.stat(message, client)
        if kind == "fs.list":
            return self.listing(message, client)
        if kind == "fs.walk":
            return self.walk(message, client)
        if kind == "fs.rename":
            return self.rename(message, client)
        if kind == "fs.paste":
            return self.paste(message, client)
        if kind == "fs.mkdir":
            return self.mkdir(message, client)
        if kind == "fs.names":
            return self.names(message, client)
        return protocol.refuse(message.id, protocol.ERROR_UNKNOWN, "{type}: not served here", {"type": kind})

    def client_gone(self, client) -> None:
        for key, watch in list(self._watches.items()):
            if key[0] == id(client):
                watch.stop()
                del self._watches[key]
        for key in [k for k in self._pending if k[0] == id(client)]:
            del self._pending[key]

    def shutdown(self) -> None:
        for watch in self._watches.values():
            watch.stop()
        self._watches.clear()
        self._pending.clear()

    def _path(self, message: protocol.Message, field: str = "path") -> str | dict:
        """The request's path (its *field*), an absolute one; else the refusal."""
        path = message.get(field)
        if not isinstance(path, str) or not os.path.isabs(path):
            return protocol.refuse(
                message.id, protocol.ERROR_REFUSED, OUTSIDE_MSGID,
                {"path": str(path)[: protocol.ARG_TEXT_MAX]},
            )
        return path

    def _roots(self, client) -> list[str] | None:
        """The roots the worker confines a path to (`confined`), computed
        here on the main loop where the store lives; None for a `local`
        client, which may name anything."""
        return None if getattr(client, "local", False) else roots(self.core)

    def _later(self, message: protocol.Message, name: str, work: Callable[[], dict]) -> protocol.Deferred:
        """*work* on a thread, its reply dict settled on the main loop; a
        worker that raises settles a `failed` refusal."""
        deferred = protocol.Deferred()

        def run() -> None:
            try:
                reply = work()
            except Exception as exc:
                log.exception("files: %s failed on its thread", name)
                reply = protocol.refuse(
                    message.id, protocol.ERROR_FAILED, "{what} failed: {error}",
                    {"what": name, "error": str(exc)[: protocol.ARG_TEXT_MAX]},
                )
            self._dispatch(lambda: deferred.settle(reply))

        self._spawn(run, name)
        return deferred

    # -- fs.read ---------------------------------------------------------------------------

    def read(self, message: protocol.Message, client) -> dict | protocol.Deferred:
        path = self._path(message)
        if isinstance(path, dict):
            return path
        max_bytes = message.get("max")
        if not isinstance(max_bytes, int) or max_bytes < 1:
            max_bytes = protocol.FILE_TEXT_MAX
        max_bytes = min(max_bytes, protocol.FILE_TEXT_MAX)
        allowed_roots = self._roots(client)

        def work() -> dict:
            try:
                fields = read_file(path, max_bytes, allowed_roots)
            except ReadRefused as refused:
                return protocol.refuse(message.id, refused.error, refused.msgid, refused.details)
            return protocol.reply(message.id, **fields)

        return self._later(message, "fs-read", work)

    # -- fs.write --------------------------------------------------------------------------

    def write(self, message: protocol.Message, client) -> dict | protocol.Deferred:
        path = self._path(message)
        if isinstance(path, dict):
            return path
        text = message.get("text")
        if not isinstance(text, str):
            return protocol.refuse(
                message.id, protocol.ERROR_INVALID, "{field} must be a string", {"field": "text"}
            )
        expect = message.get("expect_mtime")
        expect_mtime = expect if isinstance(expect, int) and not isinstance(expect, bool) else None
        encoding = message.get("encoding")
        if encoding not in protocol.FILE_ENCODINGS:
            encoding = protocol.FILE_ENCODING_UTF8
        allowed_roots = self._roots(client)

        def work() -> dict:
            try:
                fields = write_file(path, text, expect_mtime, encoding, allowed_roots)
            except ReadRefused as refused:
                return protocol.refuse(message.id, refused.error, refused.msgid, refused.details)
            except WriteStale:
                return protocol.refuse(message.id, protocol.ERROR_STALE, "The file changed on disk")
            except OSError as exc:
                return protocol.refuse(
                    message.id, protocol.ERROR_FAILED, "Couldn't save the file: {error}",
                    {"error": _os_error(exc)},
                )
            return protocol.reply(message.id, **fields)

        return self._later(message, "fs-write", work)

    # -- fs.stat, fs.list, fs.walk (PR-2.4) ------------------------------------------------

    def stat(self, message: protocol.Message, client) -> dict | protocol.Deferred:
        """`fs.stat {path, root}`: `stat_path` and `inside`, on a thread.
        Unconfined (a stat leaks nothing a shell could not); `inside` is
        of *root* when the request names one, else of every root the
        service knows (computed here, on the main loop, where the store
        lives)."""
        path = self._path(message)
        if isinstance(path, dict):
            return path
        root = message.get("root")
        inside_of = [root] if isinstance(root, str) and os.path.isabs(root) else roots(self.core)

        def work() -> dict:
            fields = stat_path(path)
            inside = fields["kind"] != "missing" and any(is_inside(r, path) for r in inside_of)
            return protocol.reply(message.id, inside=inside, **fields)

        return self._later(message, "fs-stat", work)

    def listing(self, message: protocol.Message, client) -> dict | protocol.Deferred:
        """`fs.list {path, hidden, root}`: `list_reply` on a thread, for a
        directory the client may name inside the *root* it names (checked
        on the worker, against the resolved directory). The root the
        client names is what a listed symlink must stay inside
        (`list_entries`), so it is held to the same rule as the path: a
        known root or under one, never a looser ancestor that would let a
        link out of the project be listed (review of PR 611). The known
        roots are computed here, on the main loop; the resolving checks
        run on the worker (`_confine`)."""
        path = self._path(message)
        if isinstance(path, dict):
            return path
        root = self._path(message, "root")
        if isinstance(root, dict):
            return root
        allowed_roots = self._roots(client)
        hidden = bool(message.get("hidden"))

        def work() -> dict:
            try:
                _confine(allowed_roots, path, root)
                fields = list_reply(path, hidden, root)
            except ReadRefused as refused:
                return protocol.refuse(message.id, refused.error, refused.msgid, refused.details)
            return protocol.reply(message.id, **fields)

        return self._later(message, "fs-list", work)

    def walk(self, message: protocol.Message, client) -> dict | protocol.Deferred:
        """`fs.walk {root, hidden}`: `walk_reply` on a thread, for a root
        the client may name (a session's: `_confine` on the worker against
        the roots computed here)."""
        root = self._path(message, "root")
        if isinstance(root, dict):
            return root
        allowed_roots = self._roots(client)
        hidden = bool(message.get("hidden"))

        def work() -> dict:
            try:
                _confine(allowed_roots, root)
                fields = walk_reply(root, hidden)
            except ReadRefused as refused:
                return protocol.refuse(message.id, refused.error, refused.msgid, refused.details)
            return protocol.reply(message.id, **fields)

        return self._later(message, "fs-walk", work)

    # -- fs.rename, fs.paste, fs.mkdir (PR-2.5) --------------------------------------------

    def rename(self, message: protocol.Message, client) -> dict | protocol.Deferred:
        """`fs.rename {path, target, root}`: `rename_entry` on a thread
        (see the module docstring); the refusal's `reason` is the rule.
        The `root` is one the client may name: confined on the worker
        (`_confine`, as `fs.list`'s is) against the roots computed here."""
        path = self._path(message)
        if isinstance(path, dict):
            return path
        target = self._path(message, "target")
        if isinstance(target, dict):
            return target
        root = self._path(message, "root")
        if isinstance(root, dict):
            return root
        allowed_roots = self._roots(client)

        def work() -> dict:
            try:
                _confine(allowed_roots, root)
            except ReadRefused as refused:
                return protocol.refuse(message.id, refused.error, refused.msgid, refused.details)
            try:
                landed, error = rename_entry(root, path, target)
            except OSError as exc:
                return protocol.refuse(
                    message.id, protocol.ERROR_FAILED, "Couldn't rename: {error}", {"error": _os_error(exc)}
                )
            if error is not None:
                return protocol.refuse(
                    message.id,
                    protocol.ERROR_GONE if error is RenameError.MISSING else protocol.ERROR_REFUSED,
                    RENAME_MSGIDS[error],
                    {"reason": error.value},
                )
            if landed is None:
                return protocol.reply(message.id, mtime=None)  # the name came back unchanged
            mtime, _size, gone = file_stat(str(landed))
            return protocol.reply(message.id, mtime=None if gone else mtime)

        return self._later(message, "fs-rename", work)

    def paste(self, message: protocol.Message, client) -> dict | protocol.Deferred:
        """`fs.paste {entries, target, cut, root}`: `paste_entries` on a
        thread, the `root` and each source confined there (the roots
        computed here, on the main loop; None for a `local` client). The
        reply's `results` carry each landed file's mtime (`_paste_result`)
        and travel chunked past a frame (`CHUNKED_JSON_FIELDS`)."""
        entries = message.get("entries")
        if not isinstance(entries, list) or not all(isinstance(e, str) and os.path.isabs(e) for e in entries):
            return protocol.refuse(
                message.id, protocol.ERROR_INVALID, "{field} must be a list of absolute paths",
                {"field": "entries"},
            )
        target = self._path(message, "target")
        if isinstance(target, dict):
            return target
        root = self._path(message, "root")
        if isinstance(root, dict):
            return root
        cut = bool(message.get("cut"))
        allowed_roots = self._roots(client)

        source_allowed = source_confinement(allowed_roots)

        def work() -> dict:
            try:
                _confine(allowed_roots, root)
            except ReadRefused as refused:
                return protocol.refuse(message.id, refused.error, refused.msgid, refused.details)
            outcomes = paste_entries(root, target, list(entries), cut, source_allowed)
            return protocol.reply(message.id, results=[_paste_result(o) for o in outcomes])

        return self._later(message, "fs-paste", work)

    def mkdir(self, message: protocol.Message, client) -> dict | protocol.Deferred:
        """`fs.mkdir {path, root}`: `make_directory` on a thread (the
        `root` confined there); the refusal's `reason` is the rule."""
        path = self._path(message)
        if isinstance(path, dict):
            return path
        root = self._path(message, "root")
        if isinstance(root, dict):
            return root
        allowed_roots = self._roots(client)

        def work() -> dict:
            try:
                _confine(allowed_roots, root)
            except ReadRefused as refused:
                return protocol.refuse(message.id, refused.error, refused.msgid, refused.details)
            try:
                error = make_directory(root, path)
            except OSError as exc:
                return protocol.refuse(
                    message.id, protocol.ERROR_FAILED, "Couldn't make the folder: {error}",
                    {"error": _os_error(exc)},
                )
            if error is not None:
                return protocol.refuse(
                    message.id, protocol.ERROR_REFUSED, MKDIR_MSGIDS[error], {"reason": error.value}
                )
            return protocol.reply(message.id)

        return self._later(message, "fs-mkdir", work)

    def names(self, message: protocol.Message, client) -> dict | protocol.Deferred:
        """`fs.names {root}`: `names_reply` on a thread, for a root the
        client may name (a session's, or anything for a `local` client:
        `_confine` on the worker against the roots computed here, as
        `walk` does; review of PR 614: the resolving check does not
        belong on the main loop)."""
        root = self._path(message, "root")
        if isinstance(root, dict):
            return root
        allowed_roots = self._roots(client)

        def work() -> dict:
            try:
                _confine(allowed_roots, root)
                fields = names_reply(root)
            except ReadRefused as refused:
                return protocol.refuse(message.id, refused.error, refused.msgid, refused.details)
            return protocol.reply(message.id, **fields)

        return self._later(message, "fs-names", work)

    # -- the watch -------------------------------------------------------------------------

    def watch(self, message: protocol.Message, client) -> dict | protocol.Deferred:
        """`fs.watch {path, kind, handle, mtime}`: a `_FileWatch` (`kind:
        file`, seeded with the client's `mtime` when it sends one) or a
        `_DirWatch` (`kind: dir`) under the client's handle (the same
        handle again replaces). The path is confined on a thread against
        the roots computed here (review of PR 611: `allowed` resolves every
        root on the main loop), and the watch installed when that lands —
        unless an `fs.unwatch` of the handle, a newer `fs.watch` of it or
        the client going away came first (`_pending`). A monitor Gio cannot
        make (inotify's limit reached) is refused `failed`, never a watch
        that silently watches nothing."""
        path = self._path(message)
        if isinstance(path, dict):
            return path
        kind = message.get("kind")
        if kind not in protocol.WATCH_KINDS:
            return protocol.refuse(
                message.id, protocol.ERROR_REFUSED, "A {kind} watch is not served",
                {"kind": str(kind)[: protocol.SHORT_MAX]},
            )
        handle = str(message.get("handle"))
        key = (id(client), handle)
        seed = message.get("mtime")
        allowed_roots = self._roots(client)
        token = object()
        self._pending[key] = token
        deferred = protocol.Deferred()

        def install(confined: bool) -> dict:
            if self._pending.get(key) is not token:
                return protocol.reply(message.id)  # unwatched or superseded meanwhile
            del self._pending[key]
            if not confined:
                return protocol.refuse(
                    message.id, protocol.ERROR_REFUSED, OUTSIDE_MSGID, {"path": path[: protocol.ARG_TEXT_MAX]}
                )
            old = self._watches.pop(key, None)
            if old is not None:
                old.stop()
            elif sum(1 for k in self._watches if k[0] == id(client)) >= MAX_WATCHES_PER_CLIENT:
                return protocol.refuse(
                    message.id, protocol.ERROR_REFUSED, "Too many watches for one client ({max})",
                    {"max": MAX_WATCHES_PER_CLIENT},
                )
            if kind == protocol.WATCH_DIR:
                watch: _FileWatch | _DirWatch = _DirWatch(self, path, handle, client)
            else:
                watch = _FileWatch(self, path, handle, client, seed if isinstance(seed, int) else None)
            failure = watch.start()
            if failure is not None:
                watch.stop()
                return protocol.refuse(
                    message.id, protocol.ERROR_FAILED, "Couldn't watch it: {error}",
                    {"error": failure[: protocol.ARG_TEXT_MAX]},
                )
            self._watches[key] = watch
            return protocol.reply(message.id)

        def run() -> None:
            try:
                _confine(allowed_roots, path)
                confined = True
            except ReadRefused:
                confined = False
            except Exception:
                log.exception("files: confining a watch failed on its thread")
                confined = False
            self._dispatch(lambda: deferred.settle(install(confined)))

        self._spawn(run, "fs-watch-confine")
        return deferred

    def unwatch(self, message: protocol.Message, client) -> dict:
        key = (id(client), str(message.get("handle")))
        self._pending.pop(key, None)
        watch = self._watches.pop(key, None)
        if watch is not None:
            watch.stop()
        return protocol.reply(message.id)

    def watching(self, client, handle: str) -> bool:
        return (id(client), handle) in self._watches


class _FileWatch:
    """One client's watch of one file, by its handle: the editor's
    `Gio.FileMonitor`, debounce and stat, here. The first stat (on a
    thread, right after the monitor is installed) seeds `last`; given the
    client's *seed* mtime it is compared against that, so a file that
    already differs from what the client read is one `file-changed` at
    once (a change between the read and the watch, or while the client was
    away). Every later burst whose stat differs is one more."""

    def __init__(self, files: Files, path: str, handle: str, client, seed: int | None = None) -> None:
        self.files = files
        self.path = path
        self.handle = handle
        self.client = client
        self.seed = seed
        self._monitor: Gio.FileMonitor | None = None
        self._debounce = 0
        self._checking = False
        self._stale = False
        self._stopped = False
        self.last: tuple[int | None, int | None, bool] | None = None

    def start(self) -> str | None:
        """The monitor and the seed's stat; Gio's words when it could make
        no monitor (the watch is then refused, not kept watching nothing)."""
        try:
            monitor = Gio.File.new_for_path(self.path).monitor_file(Gio.FileMonitorFlags.NONE, None)
        except GLib.Error as exc:
            log.info("files: no monitor on %s: %s", self.path, exc.message)
            return exc.message or "no monitor"
        monitor.connect("changed", self._on_event)
        self._monitor = monitor
        self.check()  # the seed, against the client's
        return None

    def stop(self) -> None:
        self._stopped = True
        if self._monitor is not None:
            self._monitor.cancel()
            self._monitor = None
        if self._debounce:
            GLib.source_remove(self._debounce)
            self._debounce = 0

    def _on_event(self, *_args) -> None:
        if self._stopped:
            return
        if self._debounce:
            return  # the burst already has its check queued
        self._debounce = GLib.timeout_add(WATCH_DEBOUNCE_MS, self._fire)

    def _fire(self) -> bool:
        self._debounce = 0
        self.check()
        return GLib.SOURCE_REMOVE

    def check(self) -> None:
        if self._stopped:
            return
        if self._checking:
            self._stale = True
            return
        self._checking = True
        path = self.path

        def work() -> None:
            found = file_stat(path)
            self.files._dispatch(lambda: self._checked(found))

        self.files._spawn(work, "fs-watch")

    def _checked(self, found: tuple[int | None, int | None, bool]) -> None:
        self._checking = False
        if self._stopped:
            return
        stale, self._stale = self._stale, False
        if self.last is None:
            # The seed: a move since the client's own mtime is reported.
            self.last = found
            if self.seed is not None and (found[2] or found[0] != self.seed):
                self._deliver(found)
        elif found != self.last:
            self.last = found
            self._deliver(found)
        if stale:
            self.check()

    def _deliver(self, found: tuple[int | None, int | None, bool]) -> None:
        mtime, size, gone = found
        try:
            self.client.deliver(
                {
                    "t": "file-changed",
                    "handle": self.handle,
                    "path": self.path,
                    "mtime": mtime,
                    "size": size,
                    "gone": gone,
                }
            )
        except Exception:
            log.exception("files: a file-changed delivery failed")


class _DirWatch:
    """One client's watch of one directory's entries, by its handle (the
    tree's monitor per expanded folder, quick open's on its root, here): a
    `monitor_directory`, debounced WATCH_DEBOUNCE_MS into one
    `dir-changed {handle, path}` per burst of events. Nothing is stat'ed
    or listed: the client lists again (`fs.list`, `fs.walk`)."""

    def __init__(self, files: Files, path: str, handle: str, client) -> None:
        self.files = files
        self.path = path
        self.handle = handle
        self.client = client
        self._monitor: Gio.FileMonitor | None = None
        self._debounce = 0
        self._stopped = False

    def start(self) -> str | None:
        """The monitor; Gio's words when it could make none (refused)."""
        try:
            monitor = Gio.File.new_for_path(self.path).monitor_directory(Gio.FileMonitorFlags.NONE, None)
        except GLib.Error as exc:
            log.info("files: no directory monitor on %s: %s", self.path, exc.message)
            return exc.message or "no monitor"
        monitor.connect("changed", self._on_event)
        self._monitor = monitor
        return None

    def stop(self) -> None:
        self._stopped = True
        if self._monitor is not None:
            self._monitor.cancel()
            self._monitor = None
        if self._debounce:
            GLib.source_remove(self._debounce)
            self._debounce = 0

    def _on_event(self, *_args) -> None:
        if self._stopped or self._debounce:
            return  # stopped, or the burst already has its event queued
        self._debounce = GLib.timeout_add(WATCH_DEBOUNCE_MS, self._fire)

    def _fire(self) -> bool:
        self._debounce = 0
        if not self._stopped:
            try:
                self.client.deliver({"t": "dir-changed", "handle": self.handle, "path": self.path})
            except Exception:
                log.exception("files: a dir-changed delivery failed")
        return GLib.SOURCE_REMOVE
