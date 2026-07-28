# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-27. Full change history: git log for this file.

"""SessionStore: the single source of truth between disk and UI.

Owns discovery (off the main thread), file monitoring, grouping/ordering,
and all state mutations. The UI listens to the `refreshed` signal and to
SessionItem property notifications; items are reused across refreshes, so
property bindings survive and full list rebuilds only happen when the
row *order* actually changes.
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, GObject  # noqa: E402

from . import panelhistory
from .models import FAV_GROUP, SessionItem
from .providers import available_providers
from .sessions import Session, discover_sessions, is_discoverable_transcript
from .state import AppState, merge_project_order, move_in_order
from .titles import TitleGenerator, fallback_title

_DEBOUNCE_MS = 2000


def _trash_file(path: Path) -> str | None:
    """Move one file to the system trash. Returns an error message or None."""
    try:
        Gio.File.new_for_path(str(path)).trash(None)
    except GLib.Error as err:
        return err.message
    return None


def _relative_time(dt: datetime) -> str:
    delta = datetime.now() - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    if seconds < 7 * 86400:
        return f"{seconds // 86400}d ago"
    return dt.strftime("%Y-%m-%d")


class SessionStore(GObject.Object):
    __gsignals__ = {
        # order_changed: True when rows were re-spliced (UI must rebuild rows)
        "refreshed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self.model = Gio.ListStore(item_type=SessionItem)
        self.sessions: dict[str, Session] = {}
        self.group_counts: dict[tuple, int] = {}
        # Projects with no rows under their group (all sessions hidden or
        # favorited): (group_key, label, cwd), newest first.
        self.empty_groups: list[tuple[tuple, str, str | None]] = []
        # Project names in display order (persisted user order + new projects
        # appended alphabetically), covering hidden projects too.
        self.resolved_project_order: list[str] = []
        self.show_hidden = False

        # Delivers on the worker thread; hop to the main loop before mutating.
        self._titles = TitleGenerator(
            lambda session_id, title: GLib.idle_add(self._on_title_generated, session_id, title)
        )
        self._items: dict[str, SessionItem] = {}
        self._last_sessions: list[Session] = []
        self._first_scan = True
        self._regen_pending: set[str] = set()  # ids whose regen should replace a manual name
        self._monitors: list[Gio.FileMonitor] = []
        self._refresh_queued = False
        self._scanning = False

    # -- discovery -----------------------------------------------------------

    def start(self) -> None:
        self.refresh()
        self._setup_monitors()

    def refresh(self) -> None:
        """Rescan every installed agent's sessions off the main thread."""
        if self._scanning:
            return
        self._scanning = True

        def work() -> None:
            sessions = discover_sessions()
            GLib.idle_add(self._on_scanned, sessions)

        threading.Thread(target=work, daemon=True).start()

    def _on_scanned(self, sessions: list[Session]) -> bool:
        self._scanning = False
        self._last_sessions = sessions
        if self._first_scan:
            self._first_scan = False
            self._backfill_names(sessions)
        self._apply()
        self._setup_monitors()  # pick up new project dirs
        self._request_titles(sessions)
        return GLib.SOURCE_REMOVE

    def _backfill_names(self, sessions: list[Session]) -> None:
        """On the first scan of a run, give every unnamed pre-existing session
        a cheap local title (the first words of its prompt) — persisted, so
        the API titling below only ever sees sessions created while the app
        runs, not a user's entire backlog."""
        if not self.state.get_setting("auto_title_sessions"):
            return
        names: dict[str, str] = {}
        for session in sessions:
            session_id = session.session_id
            if (
                session.preview
                and not self.state.get_name(session_id)
                and not self.state.get_generated_name(session_id)
            ):
                names[session_id] = fallback_title(session.preview)
        if names:
            self.state.set_generated_names(names)

    def _request_titles(self, sessions: list[Session]) -> None:
        """Queue background API titling for sessions that have no name after
        the launch backfill — i.e. sessions that appeared while the app is
        running. A session whose transcript has no real user prompt yet is
        picked up on a later refresh, once its preview appears."""
        if not self.state.get_setting("auto_title_sessions"):
            return
        for session in sessions:
            session_id = session.session_id
            if (
                session.preview
                and not self.state.get_name(session_id)
                and not self.state.get_generated_name(session_id)
            ):
                self._titles.submit(session_id, session.preview)

    def regenerate_name(self, session_id: str) -> None:
        """Right-click → Regenerate name: re-title one session via the API,
        replacing any existing generated or manual name once it arrives."""
        session = self.sessions.get(session_id)
        if session is None or not session.preview:
            return
        self._regen_pending.add(session_id)
        self._titles.submit(session_id, session.preview, force=True)

    def _on_title_generated(self, session_id: str, title: str) -> bool:
        self.state.set_generated_name(session_id, title)
        if session_id in self._regen_pending:
            self._regen_pending.discard(session_id)
            # An explicit regeneration replaces a manual rename too; the
            # automatic path never touches manually named sessions.
            self.state.set_name(session_id, "")
        self._apply()
        return GLib.SOURCE_REMOVE

    def _apply(self) -> None:
        """Project current sessions + app state into the list model."""
        sessions = self._last_sessions
        self.sessions = {s.session_id: s for s in sessions}

        visible = [
            s
            for s in sessions
            if self.show_hidden
            or not (
                self.state.is_hidden(s.session_id)
                or self.state.is_project_hidden(s.project_name)
                or self.forward_state(s) == "moved"
            )
        ]
        favorites = [s for s in visible if self.state.is_favorite(s.session_id)]
        rest = [s for s in visible if not self.state.is_favorite(s.session_id)]

        # Sessions within a group are sorted by creation time (newest first) so
        # rows don't jump around as sessions get activity; the groups
        # themselves follow the user-arranged persisted order.
        grouped: dict[tuple, list[Session]] = {}
        for session in rest:
            grouped.setdefault(("proj", session.project_name), []).append(session)
        for group_sessions in grouped.values():
            group_sessions.sort(key=lambda s: s.created, reverse=True)

        virtual = self.state.get_virtual_projects()
        previous_order = self.resolved_project_order
        self.resolved_project_order = merge_project_order(
            self.state.get_project_order(),
            [s.project_name for s in sessions] + list(virtual),
        )
        rank = {name: i for i, name in enumerate(self.resolved_project_order)}

        ordered: list[tuple[Session, tuple, str]] = [
            (s, FAV_GROUP, "Favorites") for s in favorites
        ]
        for key in sorted(grouped, key=lambda k: rank.get(k[1], len(rank))):
            ordered.extend((s, key, key[1]) for s in grouped[key])

        self.group_counts = {}
        items: list[SessionItem] = []
        for session, group_key, group_label in ordered:
            item = self._items.get(session.session_id)
            if item is None:
                item = SessionItem(session)
                self._items[session.session_id] = item
            self._update_item(item, session, group_key, group_label)
            items.append(item)
            self.group_counts[group_key] = self.group_counts.get(group_key, 0) + 1

        # Projects whose sessions are all hidden (or all favorited) get no
        # rows above, but should still show an empty header in the sidebar so
        # their "new session" button stays reachable.
        empty_groups: list[tuple[tuple, str, str | None]] = []
        for session in sessions:
            key = ("proj", session.project_name)
            if key in grouped or any(g[0] == key for g in empty_groups):
                continue
            if not self.show_hidden and self.state.is_project_hidden(session.project_name):
                continue
            empty_groups.append((key, session.project_name, self.project_cwd(session.project_name)))
        # Virtual projects: kept deliberately after their last session went
        # away, so they hang on as headers until removed. A project that has
        # sessions again is already covered above — never list it twice.
        for name, cwd in virtual.items():
            key = ("proj", name)
            if key in grouped or any(g[0] == key for g in empty_groups):
                continue
            if not self.show_hidden and self.state.is_project_hidden(name):
                continue
            empty_groups.append((key, name, cwd or None))
        empty_groups.sort(key=lambda g: rank.get(g[1], len(rank)))
        # The sidebar interleaves empty headers with session groups by
        # resolved order, so an order change alone must trigger a rebuild.
        empty_changed = (
            empty_groups != self.empty_groups
            or previous_order != self.resolved_project_order
        )
        self.empty_groups = empty_groups

        wanted_ids = {item.session_id for item in items}
        for session_id in list(self._items):
            if session_id not in wanted_ids:
                del self._items[session_id]

        current_ids = [self.model.get_item(i).session_id for i in range(self.model.get_n_items())]
        order_changed = current_ids != [item.session_id for item in items] or empty_changed
        if order_changed:
            self.model.splice(0, self.model.get_n_items(), items)
        self.emit("refreshed", order_changed)

    def forward_state(self, session: Session) -> str:
        """How a session's forward (recorded when a legacy /bg forked it)
        affects its row: "moved" — the fork is discovered, this row is
        replaced by it; "syncing" — the fork's transcript exists and a scan
        will surface it, keep the row visible but disabled until then; "" —
        no forward, or the fork's transcript vanished (e.g. trashed) or never
        became a real session (a fork whose agent died leaving a
        metadata-only stub), so the forward is stale and the row behaves
        normally again."""
        target = self.state.resolve_forward(session.session_id)
        if target == session.session_id:
            return ""
        if target in self.sessions:
            return "moved"
        if is_discoverable_transcript(Path(session.jsonl_path).parent / f"{target}.jsonl"):
            return "syncing"
        return ""

    def _update_item(self, item: SessionItem, session: Session, group_key: tuple, group_label: str) -> None:
        item.session = session
        item.group_key = group_key
        item.group_label = group_label
        updates = {
            "display_name": self.display_name(session),
            "subtitle": _relative_time(session.last_active),
            "preview": session.preview,
            "favorite": self.state.is_favorite(session.session_id),
            "state": session.state,
            "syncing": self.forward_state(session) == "syncing",
        }
        for prop, value in updates.items():
            if item.get_property(prop) != value:
                item.set_property(prop, value)

    def display_name(self, session: Session) -> str:
        return (
            self.state.get_name(session.session_id)
            or self.state.get_generated_name(session.session_id)
            or session.preview
            or session.session_id[:8]
        )

    # -- file monitoring -------------------------------------------------------

    def _setup_monitors(self) -> None:
        for monitor in self._monitors:
            monitor.cancel()
        self._monitors = []
        paths: list = []
        for provider in available_providers():
            paths += provider.watch_dirs()
        for path in paths:
            try:
                monitor = Gio.File.new_for_path(str(path)).monitor_directory(
                    Gio.FileMonitorFlags.NONE, None
                )
            except GLib.Error:
                continue
            monitor.connect("changed", self._on_fs_event)
            self._monitors.append(monitor)

    def _on_fs_event(self, _monitor, _file, _other, _event) -> None:
        if self._refresh_queued:
            return
        self._refresh_queued = True
        GLib.timeout_add(_DEBOUNCE_MS, self._debounced_refresh)

    def _debounced_refresh(self) -> bool:
        self._refresh_queued = False
        self.refresh()
        return GLib.SOURCE_REMOVE

    # -- lookups ---------------------------------------------------------------

    def get_item(self, session_id: str) -> SessionItem | None:
        return self._items.get(session_id)

    def get_session(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    # -- mutations (all UI changes go through here) ------------------------------

    def rename(self, session_id: str, name: str) -> None:
        self.state.set_name(session_id, name)
        self._apply()

    def toggle_favorite(self, session_id: str) -> None:
        self.state.toggle_favorite(session_id)
        self._apply()

    def set_hidden(self, session_id: str, hidden: bool) -> None:
        self.state.set_hidden(session_id, hidden)
        self._apply()

    def set_favorites(self, session_ids: list[str], favorite: bool) -> None:
        for session_id in session_ids:
            if self.state.is_favorite(session_id) != favorite:
                self.state.toggle_favorite(session_id)
        self._apply()

    def hide_many(self, session_ids: list[str]) -> None:
        for session_id in session_ids:
            self.state.set_hidden(session_id, True)
        self._apply()

    def hidden_sessions(self) -> list[Session]:
        """Every session the sidebar hides: individually hidden ones plus
        everything inside a hidden project. Independent of `show_hidden`,
        which only controls whether those rows are currently drawn."""
        return [
            s
            for s in self._last_sessions
            if self.state.is_hidden(s.session_id)
            or self.state.is_project_hidden(s.project_name)
        ]

    def project_cwd(self, project_name: str) -> str | None:
        """A project's working directory, from its most recent session that
        records one — what the group header's "new session here" button needs,
        and what a virtual project has to remember once its sessions are gone."""
        return next(
            (s.cwd for s in self._last_sessions if s.project_name == project_name and s.cwd),
            None,
        )

    def keep_projects(self, project_names: list[str]) -> None:
        """Keep these projects in the sidebar after their sessions go: they
        stay as empty headers, with their folder, until removed."""
        self.state.keep_virtual_projects(
            {name: self.project_cwd(name) or "" for name in project_names}
        )
        self._apply()

    def forget_project(self, project_name: str) -> None:
        """Drop a kept (virtual) project from the sidebar. Its sessions, if any
        ever come back, bring the group back with them."""
        self.state.forget_virtual_project(project_name)
        self._apply()

    def hidden_breakdown(self) -> list[tuple[str, int, int]]:
        """What hidden_sessions() covers, per project: (project name, hidden
        count, total sessions in that project), biggest first. A project whose
        hidden count equals its total loses every session it has — deleting
        them drops it from the sidebar altogether."""
        totals: dict[str, int] = {}
        for session in self._last_sessions:
            totals[session.project_name] = totals.get(session.project_name, 0) + 1
        hidden: dict[str, int] = {}
        for session in self.hidden_sessions():
            hidden[session.project_name] = hidden.get(session.project_name, 0) + 1
        return sorted(
            ((name, count, totals[name]) for name, count in hidden.items()),
            key=lambda row: (-row[1], row[0]),
        )

    def set_project_hidden(self, project_name: str, hidden: bool) -> None:
        self.state.set_project_hidden(project_name, hidden)
        self._apply()

    def record_forward(self, old_id: str, new_id: str) -> None:
        """A backgrounded session continued under a new id (a legacy /bg
        fork): carry the user's metadata (and panel history) over, hide the
        stale original row, and remember the forward so opening the old
        session redirects."""
        self.state.forward_session(old_id, new_id)
        panelhistory.copy(old_id, new_id)
        self._apply()

    def move_project(self, name: str, before: str | None) -> None:
        """Move a project in the sidebar order, before `before` (or to the
        end). Persists the full resolved order, so hidden projects keep
        their slot too."""
        order = merge_project_order(
            self.state.get_project_order(),
            {s.project_name for s in self._last_sessions}
            | set(self.state.get_virtual_projects())
            | {name},
        )
        self.state.set_project_order(move_in_order(order, name, before))
        self._apply()

    def set_show_hidden(self, show: bool) -> None:
        self.show_hidden = show
        self._apply()

    def set_status(self, session_id: str, status: str) -> None:
        item = self._items.get(session_id)
        if item is not None and item.status != status:
            item.status = status

    def set_backgrounding(self, session_id: str, flag: bool) -> None:
        item = self._items.get(session_id)
        if item is not None and item.backgrounding != flag:
            item.backgrounding = flag

    def trash(self, session_id: str) -> str | None:
        """Move the transcript to trash. Returns an error message or None."""
        return self.trash_many([session_id]).get(session_id)

    def trash_many(self, session_ids: list[str]) -> dict[str, str]:
        """Move several transcripts to trash, refreshing the list once at the
        end. Returns the error message per session id that failed; ids missing
        from the result were trashed."""
        errors: dict[str, str] = {}
        trashed: set[str] = set()
        for session_id in session_ids:
            session = self.sessions.get(session_id)
            if session is None:
                errors[session_id] = "session not found"
                continue
            error = _trash_file(session.jsonl_path)
            if error:
                errors[session_id] = error
            else:
                trashed.add(session_id)
        if trashed:
            self._last_sessions = [
                s for s in self._last_sessions if s.session_id not in trashed
            ]
            self._hide_orphaned_forwards(trashed)
            self._apply()
        return errors

    def _hide_orphaned_forwards(self, gone: set[str]) -> None:
        """A row suppressed as "moved" (a legacy /bg fork took its place) comes
        back the moment the fork's transcript disappears: forward_state reads
        the forward as stale. Keep those rows out of sight — they were already
        invisible, and trashing something else is no reason to resurface
        them."""
        for session in self._last_sessions:
            if self.state.resolve_forward(session.session_id) in gone:
                self.state.set_hidden(session.session_id, True)

    def delete(self, session_id: str) -> str | None:
        """Permanently delete the transcript file (irreversible). Returns an
        error message or None."""
        session = self.sessions.get(session_id)
        if session is None:
            return "session not found"
        try:
            Path(session.jsonl_path).unlink(missing_ok=True)
        except OSError as err:
            return str(err)
        self._last_sessions = [s for s in self._last_sessions if s.session_id != session_id]
        self._apply()
        return None
