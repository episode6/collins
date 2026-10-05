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
client already trusted with a shell). The editor's `fs.read` / `fs.write`
/ `fs.watch` and the rest of the table land in PR-2.3 onwards.

GLib and Gio only; nothing here imports GTK.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Sequence
from pathlib import Path

from gi.repository import Gio, GLib

from .. import gitops
from ..api import protocol
from ..editorfiles import is_inside
from ..sessions import worktree_project_root

log = logging.getLogger(__name__)


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


def handle_trash(core, client, message: protocol.Message) -> dict:
    """`fs.trash {paths}`: every path confined (`allowed`), then trashed;
    a path the machine's trash refuses (Gio refuses "system internal"
    mounts, a tmpfs /tmp included) is `failed` with Gio's words, and
    nothing is unlinked in its place in this PR (`removed` stays empty:
    the unlink behind a confirmation is a later chunk's)."""
    paths = [str(p) for p in message.get("paths") or ()]
    for path in paths:
        if not os.path.isabs(path) or not allowed(core, client, path):
            return protocol.refuse(
                message.id, protocol.ERROR_REFUSED, "{path} is outside every root the service knows",
                {"path": path[: protocol.ARG_TEXT_MAX]},
            )
    trashed, failure = trash_absolute(paths)
    if failure is not None:
        return protocol.refuse(
            message.id, protocol.ERROR_FAILED, "Could not move to the trash: {error}",
            {"error": failure[: protocol.ARG_TEXT_MAX]},
        )
    return protocol.reply(message.id, trashed=trashed, removed=[])
