# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Project icons over the API (split-service spec §3.23, PR-2.7).

A project's `project-icon.svg` is the service's file: it serves the bytes
as `GET /api/blob?kind=icon&root=…` (`service.blobs.read_icon`, its gate
first) and this module fetches them into the blob cache
(`blobcache.fetch`, `If-None-Match` against the file's mtime and size, so
a look at an unchanged icon is a free `304`), reads them back
(`blobcache.read`) and applies `projecticons.usable_icon_bytes` again —
rule 5: an icon from the service is as untrusted as one from the repo.

`icon_bytes(root, on_ready)` is what the sidebar's project headers, the
new-chat screen and the notifications ask: it answers at once with what is
held for *root* (None before the first fetch, or for a project with no
icon) and, when that is older than `REFRESH_S`, fetches in the background
and calls *on_ready(data)* on the main loop (`PRIORITY_DEFAULT`) when the
answer differs from what it said. `forget(root)` drops what is held (an
icon just saved). GLib only; the fetch runs on a daemon thread.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from gi.repository import GLib

from . import blobcache, projecticons

log = logging.getLogger(__name__)

# How long an answer is trusted before the next ask re-checks it (a 304
# when nothing moved): a sidebar rebuild asks every project at once.
REFRESH_S = 10.0
# Fetches in flight at once (a sidebar of thirty projects opening).
_GATE = threading.Semaphore(3)

_held: dict[str, tuple[bytes | None, float]] = {}
_waiting: dict[str, list[Callable[[bytes | None], None]]] = {}


def icon_bytes(root: str | None, on_ready: Callable[[bytes | None], None] | None = None) -> bytes | None:
    """What is held for *root*'s icon now; see the module docstring. Main
    thread."""
    if not root:
        return None
    held = _held.get(root)
    now = time.monotonic()
    if held is not None and now - held[1] < REFRESH_S:
        return held[0]
    current = held[0] if held is not None else None
    callbacks = _waiting.get(root)
    if callbacks is not None:
        if on_ready is not None:
            callbacks.append(on_ready)
        return current
    _waiting[root] = [on_ready] if on_ready is not None else []
    threading.Thread(target=_fetch, args=(root, current), name="icon-fetch", daemon=True).start()
    return current


def forget(root: str | None = None) -> None:
    """Drop what is held for *root* (every root with None)."""
    if root is None:
        _held.clear()
    else:
        _held.pop(root, None)


def _fetch(root: str, before: bytes | None) -> None:
    data: bytes | None = None
    try:
        with _GATE:
            path = blobcache.fetch(blobcache.icon_url(root), ".svg")
        data = blobcache.read(path)
    except ValueError:
        data = None  # no icon (404), a refused root, or no service
    except Exception:
        log.debug("remoteicons: fetching the icon of %s failed", root, exc_info=True)
        data = None
    if data is not None and not projecticons.usable_icon_bytes(data):
        data = None
    GLib.idle_add(_landed, root, before, data, priority=GLib.PRIORITY_DEFAULT)


def _landed(root: str, before: bytes | None, data: bytes | None) -> bool:
    _held[root] = (data, time.monotonic())
    for on_ready in _waiting.pop(root, []):
        if data != before:
            try:
                on_ready(data)
            except Exception:
                log.exception("remoteicons: a listener failed")
    return GLib.SOURCE_REMOVE
