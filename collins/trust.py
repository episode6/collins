"""Folder trust: the agent CLI's "is this a project you trust?" consent,
asked by Collins before it launches in a directory for the first time.

The CLI keeps the answer in its own config (`~/.claude.json`), one entry per
directory, and honours it for **descendants** too: a session starting in an
unrecorded directory is trusted when any ancestor is (verified against the
CLI, 2026-08-05). That inheritance is what makes trusting a repository cover
the worktrees the agent later creates under `<repo>/.claude/worktrees/`, and
why the check here walks the whole ancestor chain instead of looking up one
key — recording trust per directory would ask again for every project under
an already-trusted parent.

Plain path and config bookkeeping, kept free of widget code so it stays
unit-testable headless; the dialog that asks the question lives in
dialogs.py. The config surgery is Claude Code's format — the only agent
Collins ships a provider for. A second agent with its own trust store would
need its own reader/writer here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import sessions
from .sessions import worktree_project_root

_TRUST_KEY = "hasTrustDialogAccepted"


def trust_root(cwd: str) -> str:
    """The directory the trust question is really about: the repository for a
    Claude-managed worktree (trusting it covers every worktree inside), the
    directory itself otherwise."""
    return worktree_project_root(cwd) or os.path.normpath(cwd)


def _keys(cwd: str) -> list[str]:
    """Config keys for a directory: the path as given and fully resolved. The
    CLI keys trust by the physical directory, which differs when the path
    passes through a symlink."""
    path = os.path.normpath(cwd)
    real = os.path.realpath(cwd)
    return [path] if real == path else [path, real]


def _ancestors(cwd: str) -> list[str]:
    """A directory and every parent above it, for both spellings of the path
    — the chain an inherited trust decision can sit anywhere along."""
    chain: list[str] = []
    for start in _keys(cwd):
        path = Path(start)
        for candidate in (path, *path.parents):
            key = str(candidate)
            if key not in chain:
                chain.append(key)
    return chain


def _projects() -> dict:
    """The CLI config's per-directory records, or {} when it can't be read.
    Read live (never cached): the CLI rewrites the file as sessions end."""
    try:
        config = json.loads(Path(sessions.CLAUDE_CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(config, dict):
        return {}
    projects = config.get("projects")
    return projects if isinstance(projects, dict) else {}


def is_trusted(cwd: str) -> bool:
    """Whether the agent would launch in `cwd` without asking — true when the
    directory or any ancestor has been trusted."""
    if not cwd:
        return False
    projects = _projects()
    if not projects:
        return False
    for key in _ancestors(cwd):
        entry = projects.get(key)
        if isinstance(entry, dict) and entry.get(_TRUST_KEY) is True:
            return True
    return False


def trust_dir(cwd: str) -> bool:
    """Record `cwd` as trusted in the agent CLI's config, so the launch that
    follows doesn't ask the same question again inside the terminal.

    The CLI rewrites its config wholesale when a session ends, so an entry
    written while another session runs can occasionally be clobbered. That's
    fine: the cost is being asked once more. Best-effort by design — every
    failure is swallowed, and reported as False for callers that care.
    """
    if not cwd:
        return False
    config_path = Path(sessions.CLAUDE_CONFIG)
    try:
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            config = {}
        if not isinstance(config, dict):
            return False
        projects = config.setdefault("projects", {})
        if not isinstance(projects, dict):
            return False
        for key in _keys(cwd):
            entry = projects.setdefault(key, {})
            if isinstance(entry, dict):
                entry[_TRUST_KEY] = True
        config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(config_path) + ".tmp")
        tmp.write_text(json.dumps(config, indent=2), encoding="utf-8")
        tmp.replace(config_path)
        return True
    except (OSError, ValueError, TypeError, AttributeError):
        return False
