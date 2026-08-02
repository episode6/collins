# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Per-session persistence of the secondary terminal panel's scrollback.

When a session tab closes, each panel tab's text contents are written here
so re-opening the session (even after an app restart) can replay them into
fresh panel shells. History files live in the XDG state dir — the standard
home for history data — one plain-text file per panel tab: the first tab
keeps the pre-tabs `<session>.txt` name (so history saved before the panel
grew tabs restores into it), later tabs are `<session>.<index>.txt`.
"""

from __future__ import annotations

import os
import shutil
import string
from pathlib import Path

_STATE_BASE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
_HISTORY_DIR = _STATE_BASE / "collins" / "panel_history"

# Cap per panel tab, trimmed from the front on a line boundary. VTE's ring
# buffer already bounds a capture to the scrollback setting; this is a
# backstop against pathological long-line dumps.
_MAX_BYTES = 1_000_000

_SAFE_CHARS = set(string.ascii_letters + string.digits + "._-")


def _path(session_id: str, index: int = 0) -> Path | None:
    """History file for one of a session's panel tabs, or None for ids that
    can't safely be used as a filename (real ids are UUIDs, so this never
    rejects in practice)."""
    if not session_id or not set(session_id) <= _SAFE_CHARS or session_id.startswith("."):
        return None
    if index == 0:
        return _HISTORY_DIR / f"{session_id}.txt"
    return _HISTORY_DIR / f"{session_id}.{index}.txt"


def _indexed_paths(session_id: str) -> list[tuple[int, Path]]:
    """Existing history files for a session, ordered by panel-tab index."""
    base = _path(session_id)
    if base is None:
        return []
    found = [(0, base)] if base.is_file() else []
    prefix = f"{session_id}."
    try:
        for path in _HISTORY_DIR.glob(f"{session_id}.*.txt"):
            suffix = path.name[len(prefix) : -len(".txt")]
            if suffix.isdigit() and int(suffix) > 0:
                found.append((int(suffix), path))
    except OSError:
        pass
    return sorted(found)


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


def save(session_id: str, text: str, index: int = 0) -> None:
    """Persist one panel tab's scrollback; blank text clears that tab's file."""
    path = _path(session_id, index)
    if path is None:
        return
    text = text.rstrip()
    try:
        if not text:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".txt.tmp")
        tmp.write_text(_trim(text) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass  # history is best-effort; never break a tab close over it


def save_all(session_id: str, texts: list[str]) -> None:
    """Persist every panel tab's scrollback, one file per tab in order, then
    drop files from tabs that no longer exist — closing a panel tab deletes
    its history."""
    if _path(session_id) is None:
        return
    stale = [path for index, path in _indexed_paths(session_id) if index >= len(texts)]
    for index, text in enumerate(texts):
        save(session_id, text, index)
    for path in stale:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def load(session_id: str, index: int = 0) -> str | None:
    path = _path(session_id, index)
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8").rstrip()
    except OSError:
        return None
    return text or None


def load_all(session_id: str) -> list[str]:
    """Every saved panel tab's scrollback, in tab order. Tabs that saved
    nothing (blank shells) have no file and simply don't appear."""
    texts = []
    for index, _file in _indexed_paths(session_id):
        text = load(session_id, index)
        if text:
            texts.append(text)
    return texts


def copy(old_id: str, new_id: str) -> None:
    """Duplicate one session's history (every panel tab) to another (a
    backgrounded session continues under a new id). Missing source or a
    target with any existing history = no-op."""
    if _path(new_id) is None or _indexed_paths(new_id):
        return
    for index, src in _indexed_paths(old_id):
        dst = _path(new_id, index)
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        except OSError:
            pass  # best-effort, like save()


def delete(session_id: str) -> None:
    """Remove every panel tab's history file for a session."""
    for _index, path in _indexed_paths(session_id):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
