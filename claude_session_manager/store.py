# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-26. Full change history: git log for this file.

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

from .models import FAV_GROUP, SessionItem
from .providers import available_providers
from .sessions import Session, discover_sessions
from .state import AppState, merge_project_order, move_in_order
from .titles import TitleGenerator

_DEBOUNCE_MS = 2000


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
        self._apply()
        self._setup_monitors()  # pick up new project dirs
        self._request_titles(sessions)
        return GLib.SOURCE_REMOVE

    def _request_titles(self, sessions: list[Session]) -> None:
        """Queue background title generation for sessions that have no name
        yet. A session whose transcript has no real user prompt yet is picked
        up on a later refresh, once its preview appears."""
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

    def _on_title_generated(self, session_id: str, title: str) -> bool:
        self.state.set_generated_name(session_id, title)
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

        previous_order = self.resolved_project_order
        self.resolved_project_order = merge_project_order(
            self.state.get_project_order(), (s.project_name for s in sessions)
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
            cwd = next(
                (s.cwd for s in sessions if s.project_name == session.project_name and s.cwd),
                None,
            )
            empty_groups.append((key, session.project_name, cwd))
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

    def set_project_hidden(self, project_name: str, hidden: bool) -> None:
        self.state.set_project_hidden(project_name, hidden)
        self._apply()

    def move_project(self, name: str, before: str | None) -> None:
        """Move a project in the sidebar order, before `before` (or to the
        end). Persists the full resolved order, so hidden projects keep
        their slot too."""
        order = merge_project_order(
            self.state.get_project_order(),
            {s.project_name for s in self._last_sessions} | {name},
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

    def trash(self, session_id: str) -> str | None:
        """Move the transcript to trash. Returns an error message or None."""
        session = self.sessions.get(session_id)
        if session is None:
            return "session not found"
        try:
            Gio.File.new_for_path(str(session.jsonl_path)).trash(None)
        except GLib.Error as err:
            return err.message
        self._last_sessions = [s for s in self._last_sessions if s.session_id != session_id]
        self._apply()
        return None

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
