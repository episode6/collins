# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""The marks on each session's diff, kept by the service (split-service
spec §3.7, §3.8, PR-1.11).

A session's notes and highlights (`diffnotes.MarkStore`) are the
service's: kept per session (by its id, or by its handle before the id
resolves), persisted under the id in state.json's ``diff_notes``, and
published as `diff.notes` events (every session's in the subscribe
snapshot, then each change). The git page's own store is the mirror: a
change made there (a note typed into the view, an edit, a remove, a
clear, the prune a reload makes, the marks an agent's tool call landed
while the page was open) is applied there first, against the diff the
page has loaded, and sent whole as `diff.set-notes`; the echo is the
event.

With no client attached the diff tools work on this store (§3.7):
`annotate_diff`, `highlight_diff` and `clear_diff_marks` land here, and
`diff_context` answers, from the service's own read of the session's diff
(`gitops.read_diff` of the load a pending show_diff names, else the
working tree's unstaged changes), on a thread, the reply deferred until
it lands. Through Phase 1 a session's marks live as long as its tab does,
as they always did: the tab writes them empty when its page goes, so what
state.json keeps across a restart is only what a crash left.

GLib only; nothing here imports GTK.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from .. import diffnotes, gitinfo, gitloads, gitops, mcptools
from ..api import protocol

log = logging.getLogger(__name__)


def marks_event(key: str, store: diffnotes.MarkStore, session: bool) -> dict:
    notes, highlights = store.export()
    event = {"t": "diff.notes", "notes": notes, "highlights": highlights}
    event["session" if session else "handle"] = key
    return event


class DiffNotes:
    """See the module docstring. *state* is the service's AppState,
    *broadcast(event)* tells every subscriber, *get_setting* reads the
    service's settings (the parent branch), *dispatch* lands a thread's
    answer on the main loop and *spawn* runs one (a test's inline)."""

    def __init__(
        self,
        state,
        broadcast: Callable[[dict], None],
        get_setting: Callable[[str], object] = lambda key: None,
        dispatch: Callable | None = None,
        spawn: Callable | None = None,
    ) -> None:
        self.state = state
        self._broadcast = broadcast
        self._get_setting = get_setting
        self._stores: dict[str, diffnotes.MarkStore] = {}
        if dispatch is None:
            from gi.repository import GLib

            def dispatch(fn, *args):
                GLib.idle_add(lambda: fn(*args) and False, priority=GLib.PRIORITY_DEFAULT)

        self._dispatch = dispatch
        self._spawn = spawn or (lambda fn: threading.Thread(target=fn, name="diff-read", daemon=True).start())

    def store(self, key: str) -> diffnotes.MarkStore:
        """The marks kept under *key* (a session id, or a handle), loaded
        from state.json the first time a session id is asked for."""
        store = self._stores.get(key)
        if store is None:
            store = diffnotes.MarkStore()
            saved = self.state.get_diff_notes(key)
            if saved:
                store.load(saved.get("notes"), saved.get("highlights"))
            self._stores[key] = store
        return store

    def subscribe(self, deliver: Callable[[dict], None]) -> int:
        """The snapshot: every session's saved marks."""
        sent = 0
        for session_id in sorted(self.state.diff_notes):
            try:
                deliver(marks_event(session_id, self.store(session_id), session=True))
            except Exception:
                log.exception("diffs: a subscriber's delivery failed")
            sent += 1
        return sent

    def _changed(self, key: str, session: bool) -> None:
        store = self.store(key)
        if session:
            notes, highlights = store.export()
            self.state.set_diff_notes(key, {"notes": notes, "highlights": highlights})
            self.state.save()
        self._broadcast(marks_event(key, store, session))

    def set_notes(self, message: protocol.Message) -> dict:
        """`diff.set-notes`: a client's page's marks, whole."""
        session = message.get("session")
        key = session or message.get("handle")
        store = self.store(key)
        before = store.export()
        store.load(message.get("notes"), message.get("highlights"))
        if store.export() != before:
            self._changed(key, bool(session))
        return protocol.reply(message.id)

    # -- the diff tools with no client attached (§3.7) ---------------------------

    def _key(self, session) -> tuple[str, bool]:
        return (session.session_id, True) if session.session_id else (session.handle, False)

    def _read(self, session, then: Callable[[object], tuple[bool, str]]) -> mcptools.ToolResult:
        """Read the session's diff on a thread and answer with *then(read)*
        on the main loop, where *read* is (files, root, loaded, breadcrumb)
        or the reason there is none."""
        cwd = session.current_agent_cwd() or session.cwd
        root = gitinfo.repo_root(cwd)
        if root is None:
            return False, "The session's working directory isn't inside a git repository"
        pending = self.state.get_pending_diffs().get(session.session_id or "", {})
        loaded = gitloads.show_diff_load(pending.get("what")) if pending else None
        loaded = loaded or "unstaged"
        deferred = mcptools.DeferredResult()

        def work() -> None:
            parent = gitinfo.default_branch(cwd) if loaded == "branch" else None
            if gitloads.show_ref(loaded):
                sha = gitloads.resolve_commit(cwd, gitloads.show_ref(loaded))
                if not sha:
                    self._dispatch(deferred.resolve, False, "That commit isn't in the repository")
                    return
                read_load = {gitloads.SHOW_KEY: sha}
            else:
                read_load = loaded
            read = gitops.read_diff(cwd, read_load, parent, bool(self._get_setting("git_untracked")))
            if not read.ok:
                self._dispatch(deferred.resolve, False, read.error or "git couldn't read the diff")
                return
            crumb = gitloads.breadcrumb(read_load, gitinfo.current_branch(cwd), parent)
            got = (tuple(read.files), str(root), read_load, crumb)

            def land() -> None:
                try:
                    deferred.resolve(*then(got))
                except Exception:
                    log.exception("diffs: a diff tool failed")
                    deferred.resolve(False, "Collins couldn't act on the diff")

            self._dispatch(land)

        self._spawn(work)
        # Already in (an inline read): the answer itself, as a page answers.
        return deferred.result() if deferred.resolved else deferred

    def context(self, session, args: dict) -> mcptools.ToolResult:
        def answer(got) -> tuple[bool, str]:
            files, _root, loaded, crumb = got
            store = self.store(self._key(session)[0])
            context = mcptools.DiffContext(
                loaded=loaded,
                breadcrumb=crumb,
                files=files,
                notes=tuple(store.notes()),
                highlights=tuple(store.highlights()),
            )
            return True, mcptools.diff_context_reply(
                context,
                files=args.get("files", True),
                patch=args.get("patch", False),
                notes=args.get("notes", False),
            )

        return self._read(session, answer)

    def _resolver(self, session, root: str):
        return lambda raw: gitloads.diff_file_path(raw, root, session.current_agent_cwd())

    def annotate(self, session, args: dict) -> mcptools.ToolResult:
        def answer(got) -> tuple[bool, str]:
            files, root, _loaded, _crumb = got
            specs = mcptools.note_specs(args["notes"], self._resolver(session, root))
            if isinstance(specs, str):
                return False, f"No notes added: {specs}"
            key, is_session = self._key(session)
            added = self.store(key).add_notes(files, specs, diffnotes.AGENT)
            if isinstance(added, str):
                return False, f"No notes added: {added}"
            self._changed(key, is_session)
            return True, mcptools.annotate_reply([note.id for note in added])

        return self._read(session, answer)

    def highlight(self, session, args: dict) -> mcptools.ToolResult:
        def answer(got) -> tuple[bool, str]:
            files, root, _loaded, _crumb = got
            specs = mcptools.highlight_specs(args["marks"], self._resolver(session, root))
            if isinstance(specs, str):
                return False, f"No highlights added: {specs}"
            key, is_session = self._key(session)
            added = self.store(key).add_highlights(files, specs)
            if isinstance(added, str):
                return False, f"No highlights added: {added}"
            self._changed(key, is_session)
            return True, mcptools.highlight_reply(len(added))

        return self._read(session, answer)

    def clear(self, session, args: dict) -> mcptools.ToolResult:
        targets = mcptools.clear_targets(args)
        if isinstance(targets, str):
            return False, targets

        def answer(got) -> tuple[bool, str]:
            _files, root, _loaded, _crumb = got
            path = None
            if "file" in args:
                path = self._resolver(session, root)(args["file"])
                if path is None:
                    return False, f"'file' must be a path inside the repository: {args['file']!r}"
            notes, highlights = targets
            key, is_session = self._key(session)
            store = self.store(key)
            gone_notes = store.clear(path, notes=True, include_user=bool(args.get("user"))) if notes else None
            gone_highlights = store.clear(path, highlights=True) if highlights else None
            if gone_notes or gone_highlights:
                self._changed(key, is_session)
            return True, mcptools.clear_reply(gone_notes, gone_highlights, path)

        return self._read(session, answer)

