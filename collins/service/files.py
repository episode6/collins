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
from ..editorfiles import is_inside  # noqa: F401  (gitfeed reads it as files.is_inside)
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


class Confinement:
    """What one client may name, in two halves (PR 608's Fable review,
    should-fix 2): `confinement(core, client)` on the main loop reads the
    `local` flag and the roots' names (attribute reads: cheap at any
    store size), and `allows(path)` on the request's worker does the
    resolving — `editorfiles.is_inside`'s rule, a symlink-resolving
    compare, each root resolved once per request and kept — so a
    thousand-root store costs the main loop nothing per `git.*` or
    `fs.*` request (the resolves were 45 ms a call at 1200 roots)."""

    __slots__ = ("local", "roots", "_resolved")

    def __init__(self, local: bool, roots: Sequence[str]) -> None:
        self.local = bool(local)
        self.roots = tuple(roots)
        self._resolved: dict[str, Path | None] = {}

    def allows(self, path: str | Path) -> bool:
        """Whether the client may name *path* (worker thread: resolves)."""
        if self.local:
            return True
        try:
            resolved = Path(path).resolve()
        except OSError:
            return False
        for root in self.roots:
            resolved_root = self._root(root)
            if resolved_root is not None and (resolved == resolved_root or resolved_root in resolved.parents):
                return True
        return False

    def _root(self, root: str) -> Path | None:
        if root not in self._resolved:
            try:
                self._resolved[root] = Path(root).resolve()
            except OSError:
                self._resolved[root] = None
        return self._resolved[root]


def confinement(core, client) -> Confinement:
    """The client's `Confinement` for one request: decided on the main
    loop without a resolve (a `local` client's names no root)."""
    if getattr(client, "local", False):
        return Confinement(True, ())
    return Confinement(False, roots(core))


def allowed(core, client, path: str | Path) -> bool:
    """Whether *client* may name *path* (see the module docstring): both
    halves at once, for a caller already off the main loop or a test."""
    return confinement(core, client).allows(path)


def cwd_text(cwd: object) -> str | None:
    """*cwd* as the directory a request runs in, or None when it is not
    text or not a directory the service can see (one stat; the
    confinement is the worker's, `Confinement.allows`)."""
    if not isinstance(cwd, str) or not cwd or not os.path.isdir(cwd):
        return None
    return cwd


def allowed_cwd(core, client, cwd: object) -> str | None:
    """`cwd_text` and `allowed` at once (a caller off the main loop)."""
    text = cwd_text(cwd)
    return text if text is not None and allowed(core, client, text) else None


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


def handle_trash(core, client, message: protocol.Message, later=None) -> dict | protocol.Deferred:
    """`fs.trash {paths}`: every path confined (`allowed`), then trashed;
    a path the machine's trash refuses (Gio refuses "system internal"
    mounts, a tmpfs /tmp included) is `failed` with Gio's words, and
    nothing is unlinked in its place in this PR (`removed` stays empty:
    the unlink behind a confirmation is a later chunk's). The trash
    itself (a copy across filesystems, a slow mount) runs off the main
    loop through *later* (`gitfeed.GitFeed._later`: a thread and a
    `Deferred`); with none it runs inline (a test's)."""
    paths = [str(p) for p in message.get("paths") or ()]
    re_id = message.id
    conf = confinement(core, client)

    def outside(path: str) -> dict:
        return protocol.refuse(
            re_id, protocol.ERROR_REFUSED, "{path} is outside every root the service knows",
            {"path": path[: protocol.ARG_TEXT_MAX]},
        )

    for path in paths:
        if not os.path.isabs(path):
            return outside(path)

    def work() -> dict:
        for path in paths:  # the resolves, on the worker
            if not conf.allows(path):
                return outside(path)
        trashed, failure = trash_absolute(paths)
        if failure is not None:
            return protocol.refuse(
                re_id, protocol.ERROR_FAILED, "Could not move to the trash: {error}",
                {"error": failure[: protocol.ARG_TEXT_MAX]},
            )
        return protocol.reply(re_id, trashed=trashed, removed=[])

    return later("fs-trash", work, re_id) if later is not None else work()
