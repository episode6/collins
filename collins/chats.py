"""Throwaway chat directories backing the sidebar's virtual "Chats" project
— unrelated to the streaming chat tabs in chatsession.py.

A "chat" is an ordinary agent session whose working directory is a
disposable folder under CHATS_DIR instead of a real project. Everything
here is plain path bookkeeping, kept free of widget code so it stays
unit-testable headless.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from gi.repository import Gio, GLib

from .sessions import CLAUDE_CONFIG

# Override with COLLINS_CHATS_DIR for demos, tests and development.
CHATS_DIR = Path(
    os.environ.get("COLLINS_CHATS_DIR")
    or Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    / "collins"
    / "chats"
)


def _roots() -> tuple[str, ...]:
    """The chats root, both as configured and fully resolved: transcripts
    record the physical cwd, which differs when the root sits behind a
    symlink."""
    root = os.path.normpath(str(CHATS_DIR))
    real = os.path.realpath(root)
    return (root,) if real == root else (root, real)


def is_chat_cwd(cwd: str | None) -> bool:
    """True when cwd points inside the chats root (never the root itself).

    Purely lexical — the directory may no longer exist on disk.
    """
    if not cwd:
        return False
    path = os.path.normpath(cwd)
    return any(path.startswith(root + os.sep) for root in _roots())


def create_chat_dir() -> str:
    """Make a fresh throwaway directory for one chat. OSError propagates."""
    CHATS_DIR.mkdir(parents=True, exist_ok=True)
    return tempfile.mkdtemp(prefix="chat-", dir=CHATS_DIR)


def trust_chat_dir(cwd: str) -> None:
    """Pre-trust a chat directory in the agent CLI's config, so launching in
    a directory we just created (empty) skips the folder-trust prompt.

    Trust is per-directory — it is not inherited from the chats root — and
    the CLI rewrites its config wholesale when a session ends, so an entry
    written while another session runs can occasionally be clobbered. That's
    fine: the cost is the trust prompt appearing, exactly as it would today.
    Best-effort by design — every failure is swallowed.
    """
    if not is_chat_cwd(cwd):
        return
    try:
        try:
            config = json.loads(Path(CLAUDE_CONFIG).read_text(encoding="utf-8"))
        except FileNotFoundError:
            config = {}
        if not isinstance(config, dict):
            return
        projects = config.setdefault("projects", {})
        # The CLI keys trust by the physical working directory; write the
        # resolved path too when the chats root sits behind a symlink.
        keys = {os.path.normpath(cwd), os.path.realpath(cwd)}
        if all(projects.get(key, {}).get("hasTrustDialogAccepted") is True for key in keys):
            return
        for key in keys:
            entry = projects.setdefault(key, {})
            if isinstance(entry, dict):
                entry["hasTrustDialogAccepted"] = True
        tmp = Path(str(CLAUDE_CONFIG) + ".tmp")
        tmp.write_text(json.dumps(config, indent=2), encoding="utf-8")
        tmp.replace(CLAUDE_CONFIG)
    except (OSError, ValueError, TypeError, AttributeError):
        pass


def delete_chat_dir(cwd: str) -> str | None:
    """Permanently remove a chat directory tree.

    Returns an error message, or None on success (a directory that is
    already gone counts as success).
    """
    if not is_chat_cwd(cwd):
        return f"not a chat directory: {cwd}"
    try:
        shutil.rmtree(cwd)
    except FileNotFoundError:
        pass
    except OSError as err:
        return str(err)
    return None


def trash_chat_dir(cwd: str) -> str | None:
    """Send a chat directory to the system trash (reversible, like the
    transcript it belongs to). Returns an error message, or None; callers
    treat a failed trash as "leave it for the startup sweep"."""
    if not is_chat_cwd(cwd):
        return f"not a chat directory: {cwd}"
    if not os.path.lexists(cwd):
        return None
    try:
        Gio.File.new_for_path(cwd).trash(None)
    except GLib.Error as err:
        return err.message
    return None


def ensure_chat_dir(cwd: str | None) -> None:
    """Recreate a missing chat directory so resuming a chat never hits the
    terminal's fall-back-to-$HOME path. No-op for non-chat cwds; creation
    failure is left to that same terminal fallback."""
    if cwd and is_chat_cwd(cwd):
        try:
            os.makedirs(cwd, exist_ok=True)
        except OSError:
            pass


def sweep_orphan_chat_dirs(referenced_cwds: set[str]) -> None:
    """Reap chat directories no discovered session points at (left behind
    by trashed transcripts, e.g.). Only empty ones: rmdir refuses non-empty
    directories, which is exactly the safety net a restorable trash entry
    or stray user artifact needs."""
    referenced = {os.path.normpath(c) for c in referenced_cwds if c}
    referenced |= {os.path.realpath(c) for c in referenced_cwds if c}
    try:
        entries = list(Path(CHATS_DIR).iterdir())
    except OSError:
        return
    for entry in entries:
        path = os.path.normpath(str(entry))
        if path in referenced or os.path.realpath(path) in referenced:
            continue
        try:
            os.rmdir(path)
        except OSError:
            pass
