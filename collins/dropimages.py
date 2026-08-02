# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""GTK-free helpers for images dropped onto an agent terminal.

An image dragged in as raw data (from a browser, a screenshot tool, an
image viewer) has no path an @-mention could name, so a copy is written
here and the mention points at the copy. Dropped *files* never pass
through this module — their reference points at the original.

Kept GTK-free (like editorfiles.py/gitinfo.py) so this stays unit-testable
headless; terminal.py owns the drop target and turns Gdk values into the
bytes saved here.

The copies live under the user's cache directory rather than /tmp: the
mention only gets *read* when the user submits the prompt — maybe minutes
later, maybe after a reboot in a resumed session — and /tmp doesn't
survive that. Cache is the XDG spot for "regeneratable, fine to delete":
stale copies are pruned after PRUNE_AFTER_SECONDS on the next drop, so
the directory can't grow without bound.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

PRUNE_AFTER_SECONDS = 7 * 24 * 60 * 60  # a week: past any plausible submit

# Bounds the rename loop when a burst of drops lands in the same second;
# hitting it means something is wrong (a clock stuck at one value), and
# failing beats spinning.
_MAX_NAME_ATTEMPTS = 1000


def default_directory() -> Path:
    """Where dropped-image copies are kept. Honors XDG_CACHE_HOME (the
    same resolution GLib.get_user_cache_dir does) so tests and the
    screenshot harness relocate it along with the rest of the app's
    state."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "collins" / "dropped-images"


def save_png(data: bytes, directory: Path, timestamp: float | None = None) -> Path:
    """Write *data* to a fresh ``drop-YYYYMMDD-HHMMSS[-N].png`` under
    *directory* (created if missing) and return its path.

    The name is timestamped so a directory listing reads as a history, and
    opened with 'x' so two drops in the same second (or two app instances)
    get distinct files instead of one clobbering the other.
    """
    if timestamp is None:
        timestamp = time.time()
    directory.mkdir(parents=True, exist_ok=True)
    stem = time.strftime("drop-%Y%m%d-%H%M%S", time.localtime(timestamp))
    for attempt in range(_MAX_NAME_ATTEMPTS):
        name = f"{stem}.png" if attempt == 0 else f"{stem}-{attempt + 1}.png"
        path = directory / name
        try:
            with open(path, "xb") as fh:
                fh.write(data)
        except FileExistsError:
            continue
        return path
    raise OSError(f"no free dropped-image name under {directory}")


def prune_stale(directory: Path, now: float | None = None) -> None:
    """Delete copies older than PRUNE_AFTER_SECONDS. Called on each drop
    rather than on startup so an unused feature costs nothing; errors are
    swallowed because pruning is best-effort housekeeping — a file that
    won't delete must not break the drop that triggered it."""
    if now is None:
        now = time.time()
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    for path in entries:
        try:
            if path.is_file() and now - path.stat().st_mtime > PRUNE_AFTER_SECONDS:
                path.unlink()
        except OSError:
            continue
