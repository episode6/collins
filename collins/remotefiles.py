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

`install(link)` wires the module's watcher (the app, once the link is
connected; a harness, its own link); the reads and writes go through
`apilink.current()` as every other ask of the service. GTK-free; the
unit tests drive it through a fake link.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

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


class Watcher:
    """The editor's file watches on the service (see the module
    docstring). *link_of* is where the link comes from (`apilink.current`
    by default: the app's, or a harness's)."""

    def __init__(self, link_of: Callable[[], apilink.Link | None] = apilink.current) -> None:
        self._link_of = link_of
        self._lock = threading.Lock()
        self._next = 1
        self._watches: dict[str, tuple[str, Listener]] = {}  # handle -> (path, listener)
        self._installed_on: apilink.Link | None = None

    def install(self, link: apilink.Link) -> None:
        """Hear the link's `file-changed` events (once per link: a
        reconnect keeps the link and its handlers)."""
        if self._installed_on is link:
            return
        if self._installed_on is not None:
            self._installed_on.off("file-changed", self._on_changed)
        link.on("file-changed", self._on_changed)
        self._installed_on = link

    def uninstall(self) -> None:
        if self._installed_on is not None:
            self._installed_on.off("file-changed", self._on_changed)
            self._installed_on = None

    @property
    def installed(self) -> bool:
        """Whether a link with the `files` capability is installed: with
        none (an older service) no watch is sent, and the editor's reads
        and writes come back refused."""
        return self._installed_on is not None

    def watch(self, path: str, listener: Listener) -> str:
        """Watch *path* for *listener*: the handle, which `unwatch` takes.
        Non-blocking (`send`); a refusal is logged and the watch kept, so
        a reconnect's `reset` asks again. With no link installed the watch
        is kept and nothing is sent (a service with no `files` cap)."""
        with self._lock:
            handle = f"w{self._next}"
            self._next += 1
            self._watches[handle] = (path, listener)
        self._send_watch(handle, path)
        return handle

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
            live = list(self._watches.items())
        for handle, (path, _listener) in live:
            self._send_watch(handle, path)

    def _send_watch(self, handle: str, path: str) -> None:
        link = self._link_of()
        if link is None or not self.installed:
            return
        link.send(
            {"t": "fs.watch", "path": path, "kind": protocol.WATCH_FILE, "handle": handle},
            on_refused=lambda refusal: log.warning(
                "remotefiles: a watch of %s was refused: %s", path, refusal.msgid
            ),
        )

    def _on_changed(self, event: dict) -> None:
        handle = event.get("handle")
        with self._lock:
            known = self._watches.get(str(handle))
        if known is None:
            return
        _path, listener = known
        try:
            listener(dict(event))
        except Exception:
            log.exception("remotefiles: a file-changed listener failed")


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
