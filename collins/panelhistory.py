# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Per-session persistence of the secondary terminal panel's scrollback.

When a session tab closes, the panel's text contents are written here so
re-opening the tab (even after an app restart) can replay them into the
fresh panel shell. History files live in the XDG state dir — the standard
home for history data — one plain-text file per session.
"""

from __future__ import annotations

import os
import shutil
import string
from pathlib import Path

_STATE_BASE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
_HISTORY_DIR = _STATE_BASE / "collins" / "panel_history"

# Cap per session, trimmed from the front on a line boundary. VTE's ring
# buffer already bounds a capture to the scrollback setting; this is a
# backstop against pathological long-line dumps.
_MAX_BYTES = 1_000_000

_SAFE_CHARS = set(string.ascii_letters + string.digits + "._-")


def _path(session_id: str) -> Path | None:
    """History file for a session id, or None for ids that can't safely be
    used as a filename (real ids are UUIDs, so this never rejects in practice)."""
    if not session_id or not set(session_id) <= _SAFE_CHARS or session_id.startswith("."):
        return None
    return _HISTORY_DIR / f"{session_id}.txt"


def _trim(text: str) -> str:
    """Keep the last _MAX_BYTES of text, cut on a line boundary."""
    data = text.encode("utf-8")
    if len(data) <= _MAX_BYTES:
        return text
    tail = data[-_MAX_BYTES:]
    newline = tail.find(b"\n")
    if newline != -1:
        tail = tail[newline + 1 :]
    return tail.decode("utf-8", errors="ignore")


def save(session_id: str, text: str) -> None:
    """Persist the panel scrollback for a session; blank text clears it."""
    path = _path(session_id)
    if path is None:
        return
    text = text.rstrip()
    if not text:
        delete(session_id)
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".txt.tmp")
        tmp.write_text(_trim(text) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass  # history is best-effort; never break a tab close over it


def load(session_id: str) -> str | None:
    path = _path(session_id)
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8").rstrip()
    except OSError:
        return None
    return text or None


def copy(old_id: str, new_id: str) -> None:
    """Duplicate one session's history to another (a backgrounded session
    continues under a new id). Missing source or existing target = no-op."""
    src, dst = _path(old_id), _path(new_id)
    if src is None or dst is None:
        return
    try:
        if src.is_file() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    except OSError:
        pass  # best-effort, like save()


def delete(session_id: str) -> None:
    path = _path(session_id)
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
