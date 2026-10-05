# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Files over the API, the client's end (split-service spec §3.23, PR-2.3).

The editor opens no file of the project's and writes none: every path is
a path on the service's machine, and this module is how `editor.py`
reaches them.

- `read(path)` is `fs.read`: the file's text with the encoding the
  service decoded it as, its mtime (what the save that follows expects)
  and its size, or `binary`. `write(path, text, expect_mtime, encoding)`
  is `fs.write`: the new mtime, or a `RequestRefused` whose error is
  ``stale`` (`is_stale`) when the file moved underneath the editor since
  its last read or write — the editor's "changed on disk" dialog. Both
  block on the link's sync channel, so the editor calls them on a worker
  thread and lands the answer at `GLib.PRIORITY_DEFAULT`.
- `Watcher` is the editor's file monitors, installed on the service:
  `watch(path, listener)` sends `fs.watch` under a handle this client
  mints and hands every `file-changed` event for that handle to the
  listener (on the main loop, where the link lands events); `unwatch`
  sends `fs.unwatch`. A reconnect loses the service's watches, so
  `reset()` sends every live watch again.

PR-2.4 adds the tree's, quick open's and the roots' reads, blocking on
the sync channel like `read` (a worker thread's; `off_main` is the
pattern: the work on a daemon thread, its answer landed at
`GLib.PRIORITY_DEFAULT`):

- `stat_path(path, root)` is `fs.stat`: the kind (a symlink followed), a
  file's size, the mtime and `inside`, whether the path resolves inside
  *root* (the asking editor's; every root the service knows when None).
- `list_dir(path, root, hidden)` is `fs.list`: the directory's entries in
  the tree's order with the ignored names marked, and `truncated`.
- `walk_root(root, hidden)` is `fs.walk`: every file under the root, relative,
  at most 20 000, and `truncated`.
- `Watcher.watch(path, listener, kind=WATCH_DIR)` watches a directory's
  entries; its listener hears `dir-changed {handle, path}`.

`install(link)` wires the module's watcher (the app, once the link is
connected; a harness, its own link); the reads and writes go through
`apilink.current()` as every other ask of the service. GTK-free; the
unit tests drive it through a fake link.
"""

from __future__ import annotations

import inspect
import logging
import threading
import weakref
from collections.abc import Callable
from dataclasses import dataclass

from gi.repository import GLib

from . import apilink
from .api import protocol
from .api.protocol import RequestRefused
from .i18n import _

log = logging.getLogger(__name__)

# A read or write of a file of FILE_TEXT_MAX over a loaded service.
CALL_TIMEOUT_S = 60.0

Listener = Callable[[dict], None]


@dataclass(frozen=True)
class FileText:
    """What `fs.read` answered."""

    text: str
    encoding: str
    mtime: int
    size: int
    binary: bool


@dataclass(frozen=True)
class Written:
    """What `fs.write` answered."""

    mtime: int
    size: int
    encoding: str


@dataclass(frozen=True)
class Stat:
    """What `fs.stat` answered."""

    kind: str
    size: int | None
    mtime: int | None
    inside: bool

    @property
    def is_file(self) -> bool:
        return self.kind == "file"

    @property
    def is_dir(self) -> bool:
        return self.kind == "dir"


@dataclass(frozen=True)
class Entry:
    """One entry of an `fs.list`: a file, a directory, or a symlink to a
    directory inside the root (a folder never expanded)."""

    name: str
    kind: str
    ignored: bool

    @property
    def is_dir(self) -> bool:
        return self.kind != "file"

    @property
    def expandable(self) -> bool:
        return self.kind == "dir"


def refusal_words(refusal: RequestRefused) -> str:
    """A refusal's msgid translated and filled, for a banner (§3.14); the
    code when the service sent no words."""
    try:
        return _(refusal.msgid).format_map(refusal.details) if refusal.msgid else refusal.error
    except (KeyError, IndexError, ValueError):
        return refusal.msgid


def is_stale(refusal: RequestRefused) -> bool:
    return refusal.error == protocol.ERROR_STALE


def read(path: str, max_bytes: int | None = None) -> FileText:
    """`fs.read` of *path*: blocking, on the caller's thread (a worker's).
    Raises `RequestRefused` (``refused`` over the cap or outside every
    root, ``gone`` for a file that is not there, ``failed``)."""
    message: dict = {"t": "fs.read", "path": path}
    if max_bytes is not None:
        message["max"] = int(max_bytes)
    fields = apilink.call(message, timeout=CALL_TIMEOUT_S)
    return FileText(
        text=str(fields.get("text", "")),
        encoding=str(fields.get("encoding") or protocol.FILE_ENCODING_UTF8),
        mtime=int(fields.get("mtime", 0)),
        size=int(fields.get("size", 0)),
        binary=bool(fields.get("binary", False)),
    )


def write(
    path: str, text: str, expect_mtime: int | None, encoding: str = protocol.FILE_ENCODING_UTF8
) -> Written:
    """`fs.write` of *text* to *path*: blocking, on the caller's thread.
    *expect_mtime* is the mtime the last read or write answered (None:
    write regardless). Raises `RequestRefused`; ``stale`` (`is_stale`)
    when the file moved and nothing was written."""
    message = {
        "t": "fs.write",
        "path": path,
        "text": text,
        "expect_mtime": expect_mtime,
        "encoding": encoding if encoding in protocol.FILE_ENCODINGS else protocol.FILE_ENCODING_UTF8,
    }
    fields = apilink.call(message, timeout=CALL_TIMEOUT_S)
    return Written(
        mtime=int(fields.get("mtime", 0)),
        size=int(fields.get("size", 0)),
        encoding=str(fields.get("encoding") or protocol.FILE_ENCODING_UTF8),
    )


def stat_path(path: str, root: str | None = None) -> Stat:
    """`fs.stat` of *path*: blocking, on the caller's thread (a worker's).
    *root* is what `inside` is of (every root the service knows when
    None). Raises `RequestRefused`."""
    message: dict = {"t": "fs.stat", "path": str(path)}
    if root is not None:
        message["root"] = str(root)
    fields = apilink.call(message, timeout=CALL_TIMEOUT_S)
    size = fields.get("size")
    mtime = fields.get("mtime")
    return Stat(
        kind=str(fields.get("kind") or "missing"),
        size=int(size) if isinstance(size, int) else None,
        mtime=int(mtime) if isinstance(mtime, int) else None,
        inside=bool(fields.get("inside", False)),
    )


def list_dir(path: str, root: str, hidden: bool = False) -> tuple[list[Entry], bool]:
    """`fs.list` of the directory *path* under the tree's *root*:
    (entries, truncated). Blocking; raises `RequestRefused`."""
    fields = apilink.call(
        {"t": "fs.list", "path": str(path), "hidden": bool(hidden), "root": str(root)},
        timeout=CALL_TIMEOUT_S,
    )
    entries = [
        Entry(str(item.get("name")), str(item.get("kind")), bool(item.get("ignored")))
        for item in fields.get("entries") or ()
        if isinstance(item, dict)
    ]
    return entries, bool(fields.get("truncated", False))


def walk_root(root: str, hidden: bool = False) -> tuple[list[str], bool]:
    """`fs.walk` of *root*: (relative paths, truncated). Blocking; raises
    `RequestRefused`."""
    fields = apilink.call({"t": "fs.walk", "root": str(root), "hidden": bool(hidden)}, timeout=CALL_TIMEOUT_S)
    return [str(p) for p in fields.get("paths") or ()], bool(fields.get("truncated", False))


def off_main(
    work: Callable[[], object], land: Callable[[str, object], None], name: str = "remotefiles"
) -> None:
    """*work* (a blocking call of the service's) on a daemon thread;
    `land(kind, value)` on the main loop at `GLib.PRIORITY_DEFAULT` with
    ``("ok", result)`` or ``("refused", RequestRefused)`` — anything else
    the work raised (the link gone mid-call, a bug) lands as a ``failed``
    refusal, never a hang."""

    def run() -> None:
        try:
            result: tuple[str, object] = ("ok", work())
        except RequestRefused as refusal:
            result = ("refused", refusal)
        except Exception as exc:
            log.exception("remotefiles: %s failed", name)
            result = ("refused", RequestRefused(protocol.ERROR_FAILED, str(exc), {}))
        GLib.idle_add(lambda: land(*result) and False, priority=GLib.PRIORITY_DEFAULT)

    threading.Thread(target=run, name=name, daemon=True).start()


class Watcher:
    """The editor's file watches on the service (see the module
    docstring). *link_of* is where the link comes from (`apilink.current`
    by default: the app's, or a harness's)."""

    def __init__(self, link_of: Callable[[], apilink.Link | None] = apilink.current) -> None:
        self._link_of = link_of
        self._lock = threading.Lock()
        self._next = 1
        # handle -> _Watch; a listener that is a bound method is held
        # weakly (`weakref.WeakMethod`), so a pane dropped without its
        # `shutdown` does not live on through its watches: a dead listener's
        # watch is dropped at the next event.
        self._watches: dict[str, _Watch] = {}
        self._installed_on: apilink.Link | None = None

    def install(self, link: apilink.Link) -> None:
        """Hear the link's `file-changed` events (once per link: a
        reconnect keeps the link and its handlers)."""
        if self._installed_on is link:
            return
        if self._installed_on is not None:
            for name in _EVENTS:
                self._installed_on.off(name, self._on_changed)
        for name in _EVENTS:
            link.on(name, self._on_changed)
        self._installed_on = link

    def uninstall(self) -> None:
        if self._installed_on is not None:
            for name in _EVENTS:
                self._installed_on.off(name, self._on_changed)
            self._installed_on = None

    @property
    def installed(self) -> bool:
        """Whether a link with the `files` capability is installed: with
        none (an older service) no watch is sent, and the editor's reads
        and writes come back refused."""
        return self._installed_on is not None

    def watch(
        self, path: str, listener: Listener, mtime: int | None = None, kind: str = protocol.WATCH_FILE
    ) -> str:
        """Watch *path* for *listener*: the handle, which `unwatch` takes.
        *kind* is WATCH_FILE (`file-changed`) or WATCH_DIR (a directory's
        entries, `dir-changed`; no *mtime*).
        *mtime* is the file as the caller last read or wrote it: the
        service's first stat is compared against it, so a change in
        between is one `file-changed` at once (`update` keeps it current
        for a reconnect's `reset`). Non-blocking (`send`); a refusal is
        logged and the watch kept, so a reconnect's `reset` asks again.
        With no link installed the watch is kept and nothing is sent (a
        service with no `files` cap)."""
        with self._lock:
            handle = f"w{self._next}"
            self._next += 1
            self._watches[handle] = _Watch(path, listener, mtime, kind)
        self._send_watch(handle)
        return handle

    def update(self, handle: str | None, mtime: int | None) -> None:
        """The file was read or written again: *mtime* is what a
        reconnect's `reset` seeds the watch with."""
        if handle is None:
            return
        with self._lock:
            watch = self._watches.get(handle)
            if watch is not None:
                watch.mtime = mtime

    def unwatch(self, handle: str | None) -> None:
        if handle is None:
            return
        with self._lock:
            known = self._watches.pop(handle, None)
        if known is None or not self.installed:
            return
        link = self._link_of()
        if link is not None:
            link.send({"t": "fs.unwatch", "handle": handle}, on_refused=lambda _r: None)

    def watching(self, handle: str) -> bool:
        with self._lock:
            return handle in self._watches

    def reset(self) -> None:
        """A reconnect: the service's watches are gone; every live one is
        sent again under its handle."""
        with self._lock:
            live = list(self._watches)
        for handle in live:
            self._send_watch(handle)

    def _send_watch(self, handle: str) -> None:
        link = self._link_of()
        with self._lock:
            watch = self._watches.get(handle)
        if link is None or not self.installed or watch is None:
            return
        path = watch.path
        message = {"t": "fs.watch", "path": path, "kind": watch.kind, "handle": handle}
        if watch.mtime is not None and watch.kind == protocol.WATCH_FILE:
            message["mtime"] = watch.mtime
        link.send(
            message,
            on_refused=lambda refusal: log.warning(
                "remotefiles: a watch of %s was refused: %s", path, refusal.msgid
            ),
        )

    def _on_changed(self, event: dict) -> None:
        handle = str(event.get("handle"))
        with self._lock:
            watch = self._watches.get(handle)
        if watch is None:
            return
        listener = watch.listener()
        if listener is None:
            self.unwatch(handle)  # its owner is gone: the watch goes with it
            return
        try:
            listener(dict(event))
        except Exception:
            log.exception("remotefiles: a %s listener failed", event.get("t"))


class _Watch:
    """One watch of the client's: the path, the listener (weakly, for a
    bound method) and the mtime the service's watch is seeded with."""

    __slots__ = ("path", "_ref", "mtime", "kind")

    def __init__(
        self, path: str, listener: Listener, mtime: int | None, kind: str = protocol.WATCH_FILE
    ) -> None:
        self.path = path
        self.mtime = mtime
        self.kind = kind
        if inspect.ismethod(listener):
            self._ref: Callable[[], Listener | None] = weakref.WeakMethod(listener)
        else:
            self._ref = lambda: listener

    def listener(self) -> Listener | None:
        return self._ref()


# The events a watch's listener hears: a file's, a directory's.
_EVENTS = ("file-changed", "dir-changed")

_WATCHER = Watcher()


def watcher() -> Watcher:
    return _WATCHER


def install(link: apilink.Link) -> None:
    """Hear *link*'s `file-changed` events (the app, once connected)."""
    _WATCHER.install(link)


def uninstall() -> None:
    _WATCHER.uninstall()


def reset() -> None:
    """A reconnect: the watches are installed on the service again."""
    _WATCHER.reset()
