# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Per-session persistence of the panel shells' scrollback.

When a session tab closes, each shell page's text contents are written
here so re-opening the session (even after an app restart) can replay
them into fresh shells. History files live in the XDG state dir — the
standard home for history data — one plain-text file per shell, keyed by
the shell's persistent *ordinal*: ordinal 0 keeps the pre-tabs
`<session>.txt` name (so history saved before the panel grew tabs
restores into it), higher ordinals are `<session>.<ordinal>.txt`.

Ordinals are assigned once per shell and never renumbered — pages can
move between dock strips, so a file addressed by tab position would drift
away from its shell. Files saved by the positional era adopt their index
as their ordinal on first load (the names are identical), and `save_all`
takes the live mapping as an explicit keep-set: ordinals it doesn't
mention belong to closed shells and their files are dropped.
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


def _path(session_id: str, ordinal: int = 0) -> Path | None:
    """History file for one of a session's shells, or None for ids that
    can't safely be used as a filename (real ids are UUIDs, so this never
    rejects in practice)."""
    if not session_id or not set(session_id) <= _SAFE_CHARS or session_id.startswith("."):
        return None
    if ordinal == 0:
        return _HISTORY_DIR / f"{session_id}.txt"
    return _HISTORY_DIR / f"{session_id}.{ordinal}.txt"


def _ordinal_paths(session_id: str) -> list[tuple[int, Path]]:
    """Existing history files for a session, ordered by shell ordinal."""
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


def ordinals(session_id: str) -> list[int]:
    """The shell ordinals with a saved history file, ascending — counting
    the saved shells without reading their text (legacy migration sizes an
    old layout by this)."""
    return [ordinal for ordinal, _path_ in _ordinal_paths(session_id)]


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


def save(session_id: str, text: str, ordinal: int = 0) -> None:
    """Persist one shell's scrollback; blank text clears that shell's file."""
    path = _path(session_id, ordinal)
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


def save_all(session_id: str, texts: dict[int, str]) -> None:
    """Persist every live shell's scrollback, one file per ordinal. The
    mapping's keys are the explicit keep-set: files under ordinals it
    doesn't mention belong to shells that no longer exist — closing a
    shell deletes its history."""
    if _path(session_id) is None:
        return
    stale = [path for ordinal, path in _ordinal_paths(session_id) if ordinal not in texts]
    for ordinal, text in texts.items():
        save(session_id, text, ordinal)
    for path in stale:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def load(session_id: str, ordinal: int = 0) -> str | None:
    path = _path(session_id, ordinal)
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8").rstrip()
    except OSError:
        return None
    return text or None


def load_all(session_id: str) -> dict[int, str]:
    """Every saved shell's scrollback by ordinal, ascending. Shells that
    saved nothing (blank at close) have no file and simply don't appear."""
    texts = {}
    for ordinal, _file in _ordinal_paths(session_id):
        text = load(session_id, ordinal)
        if text:
            texts[ordinal] = text
    return texts


def copy(old_id: str, new_id: str) -> None:
    """Duplicate one session's history (every shell, ordinals kept) to
    another (a backgrounded session continues under a new id). Missing
    source or a target with any existing history = no-op."""
    if _path(new_id) is None or _ordinal_paths(new_id):
        return
    for ordinal, src in _ordinal_paths(old_id):
        dst = _path(new_id, ordinal)
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        except OSError:
            pass  # best-effort, like save()


def delete(session_id: str) -> None:
    """Remove every shell's history file for a session."""
    for _ordinal, path in _ordinal_paths(session_id):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
