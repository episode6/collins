# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-28. Full change history: git log for this file.

"""Session sidebar: search, project accordion, favorites, selection mode.

Rows bind to SessionItem properties, so renames/stars/status changes update
in place; the list is only rebuilt when the store reports an order change.

Emits:
  open-session     (SessionItem, bool fork)
  open-many        (list[SessionItem])
  trash-many       (list[SessionItem])
  archive-many     (list[SessionItem])
  open-placeholder  (str placeholder id)
  close-placeholder (str placeholder id)
"""

from __future__ import annotations

import shutil
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from .formatting import format_size
from .i18n import _
from .models import FAV_GROUP, SessionItem
from .providers import get_provider
from .store import SessionStore
from .usagepanel import UsagePanel

_GHOSTTY = shutil.which("ghostty")
_ELLIPSIZE_END = 3  # Pango.EllipsizeMode.END
_ELLIPSIZE_START = 1  # Pango.EllipsizeMode.START


def _abbreviate_path(path: str | None) -> str:
    """Show the folder path with $HOME collapsed to ~ for compactness."""
    if not path:
        return ""
    try:
        home = str(Path.home())
    except OSError:
        return path
    if path == home:
        return "~"
    if path.startswith(home + "/"):
        return "~" + path[len(home):]
    return path


def _group_state_key(group_key: tuple) -> str:
    """Stable string identity for a sidebar group, for persisted state."""
    return f"{group_key[0]}:{group_key[1]}"


class GroupHeaderRow(Gtk.ListBoxRow):
    """A real row acting as a group header, so it stays visible when the
    group's session rows are filtered out (collapsed)."""

    def __init__(
        self,
        group_key: tuple,
        group_label: str,
        count: int,
        collapsed: bool,
        cwd: str | None = None,
        sidebar: SessionSidebar | None = None,
    ) -> None:
        super().__init__()
        self.group_key = group_key
        self.cwd = cwd
        self.set_selectable(False)

        if sidebar is not None and group_key != FAV_GROUP:
            right_click = Gtk.GestureClick(button=3)
            right_click.connect(
                "pressed", lambda _g, _n, x, y: sidebar.show_group_menu(self, x, y)
            )
            self.add_controller(right_click)

            # Project headers can be dragged to rearrange the sidebar order
            # (favorites stays pinned first).
            drag = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
            drag.connect("prepare", self._on_drag_prepare)
            drag.connect("drag-begin", self._on_drag_begin)
            self.add_controller(drag)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.add_css_class("group-header")

        self._arrow = Gtk.Image()
        self._arrow.add_css_class("dim-label")
        box.append(self._arrow)

        icon = Gtk.Image.new_from_icon_name(
            "starred-symbolic" if group_key == FAV_GROUP else "folder-symbolic"
        )
        icon.add_css_class("dim-label")
        box.append(icon)

        label = Gtk.Label(label=group_label.upper(), xalign=0.0, hexpand=True)
        label.add_css_class("caption-heading")
        label.add_css_class("dim-label")
        label.set_ellipsize(_ELLIPSIZE_END)
        box.append(label)

        count_label = Gtk.Label(label=str(count))
        count_label.add_css_class("count-badge")
        count_label.add_css_class("dim-label")
        count_label.set_visible(count > 0)
        box.append(count_label)

        if cwd:
            new_btn = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
            new_btn.add_css_class("flat")
            new_btn.set_tooltip_text(
                _("New session in {path}").format(path=_abbreviate_path(cwd))
            )
            new_btn.connect(
                "clicked",
                lambda *_: self.activate_action("win.new-session-in", GLib.Variant("s", cwd)),
            )
            box.append(new_btn)

        self.set_child(box)
        self.set_collapsed(collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        self._arrow.set_from_icon_name("pan-end-symbolic" if collapsed else "pan-down-symbolic")

    def _on_drag_prepare(self, _source: Gtk.DragSource, _x: float, _y: float) -> Gdk.ContentProvider:
        value = GObject.Value(GObject.TYPE_STRING, self.group_key[1])
        return Gdk.ContentProvider.new_for_value(value)

    def _on_drag_begin(self, source: Gtk.DragSource, _drag: Gdk.Drag) -> None:
        source.set_icon(Gtk.WidgetPaintable.new(self), 0, 0)


class PlaceholderRow(Gtk.ListBoxRow):
    """Transient stand-in for a just-opened tab whose session id is still
    unknown (no transcript on disk yet). Swapped for a real SessionRow once
    the store discovers the session."""

    def __init__(self, placeholder_id: str, group_key: tuple, sidebar: SessionSidebar) -> None:
        super().__init__()
        self.placeholder_id = placeholder_id
        self.group_key = group_key
        self.add_css_class("session-child")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        box.set_margin_top(4)  # match SessionRow: the flat button fills the row
        box.set_margin_bottom(4)

        dot = Gtk.Box(valign=Gtk.Align.CENTER)
        dot.add_css_class("status-dot")
        dot.add_css_class("open")
        box.append(dot)

        label = Gtk.Label(label=_("New Thread"), xalign=0.0, hexpand=True)
        label.set_margin_start(8)  # match SessionRow's name label
        label.add_css_class("dim-label")
        label.set_ellipsize(_ELLIPSIZE_END)
        box.append(label)

        # There is no session to archive yet, so the slot the archive button
        # occupies closes the tab instead (through the usual busy-tab
        # confirmation flow).
        close_btn = Gtk.Button(icon_name="tab-close-symbolic", valign=Gtk.Align.CENTER)
        close_btn.add_css_class("flat")
        close_btn.set_tooltip_text(_("Close tab"))
        close_btn.connect(
            "clicked", lambda *_: sidebar.emit("close-placeholder", placeholder_id)
        )
        box.append(close_btn)

        self.set_child(box)


class SessionRow(Gtk.ListBoxRow):
    def __init__(self, item: SessionItem, sidebar: SessionSidebar) -> None:
        super().__init__()
        self.item = item
        self._sidebar = sidebar
        self.add_css_class("session-child")  # indented, with a left guide line

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(0)  # theme row padding alone ≈ the 10px dot-to-title gap
        box.set_margin_end(0)  # theme row padding + the flat button's inset suffice

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)

        self.check = Gtk.CheckButton(valign=Gtk.Align.CENTER, visible=False)
        self.check.connect("toggled", lambda c: sidebar.on_row_check_toggled(self, c.get_active()))
        top.append(self.check)

        self.dot = Gtk.Box(valign=Gtk.Align.CENTER)
        self.dot.add_css_class("status-dot")
        top.append(self.dot)

        name_label = Gtk.Label(xalign=0.0, hexpand=True)
        name_label.set_margin_start(8)  # ~half the highlight-edge-to-dot distance
        name_label.set_ellipsize(_ELLIPSIZE_END)
        top.append(name_label)

        time_label = Gtk.Label(valign=Gtk.Align.CENTER)
        time_label.set_margin_start(8)  # match the 10px gaps around the status dot
        time_label.add_css_class("dim-label")
        time_label.add_css_class("caption")
        top.append(time_label)

        self._state_badge = Gtk.Image(valign=Gtk.Align.CENTER)
        top.append(self._state_badge)

        # Archived rows are only listed while "Show archived sessions" is on,
        # and toggling rebuilds the list, so the icon never goes stale.
        archived = sidebar.store.state.is_archived(item.session_id)
        archive_btn = Gtk.Button(
            icon_name="unarchive-symbolic" if archived else "archive-symbolic",
            valign=Gtk.Align.CENTER,
        )
        archive_btn.add_css_class("flat")
        archive_btn.set_tooltip_text(_("Restore session") if archived else _("Archive session"))
        archive_btn.connect(
            "clicked",
            lambda *_: self.activate_action("win.archive-session", GLib.Variant("s", item.session_id)),
        )
        top.append(archive_btn)
        box.append(top)

        path_label = Gtk.Label(xalign=0.0)
        path_label.set_ellipsize(_ELLIPSIZE_START)  # keep the tail (the leaf dir) visible
        path_label.add_css_class("dim-label")
        path_label.add_css_class("caption")
        path_label.set_label(_abbreviate_path(item.session.cwd))
        path_label.set_visible(sidebar.show_folder_path and bool(item.session.cwd))
        self._path_label = path_label
        box.append(path_label)

        self.set_child(box)

        # Property bindings: released automatically when either side is finalized.
        flags = GObject.BindingFlags.SYNC_CREATE
        item.bind_property("display-name", name_label, "label", flags)
        item.bind_property("subtitle", time_label, "label", flags)
        # A session mid-/bg-handoff stays visible but disabled, so it can't
        # be opened into a stale state: either its legacy fork isn't scanned
        # yet ("syncing") or the detach isn't confirmed yet ("backgrounding").
        self._syncing_handler = item.connect("notify::syncing", self._on_sensitive_changed)
        self._backgrounding_handler = item.connect(
            "notify::backgrounding", self._on_sensitive_changed
        )
        self._on_sensitive_changed(item, None)

        # Status dot + state badge need CSS-class updates: plain signals,
        # detached on unroot.
        self._status_handler = item.connect("notify::status", self._on_status_changed)
        self._state_handler = item.connect("notify::state", self._on_state_changed)
        self._on_status_changed(item, None)
        self._on_state_changed(item, None)

        right_click = Gtk.GestureClick(button=3)
        right_click.connect("pressed", self._on_right_click)
        self.add_controller(right_click)

    def update_folder_path(self, show: bool) -> None:
        self._path_label.set_visible(show and bool(self.item.session.cwd))

    def do_unroot(self) -> None:
        if self._status_handler is not None:
            self.item.disconnect(self._status_handler)
            self._status_handler = None
        if self._state_handler is not None:
            self.item.disconnect(self._state_handler)
            self._state_handler = None
        if self._syncing_handler is not None:
            self.item.disconnect(self._syncing_handler)
            self._syncing_handler = None
        if self._backgrounding_handler is not None:
            self.item.disconnect(self._backgrounding_handler)
            self._backgrounding_handler = None
        Gtk.ListBoxRow.do_unroot(self)

    def _on_sensitive_changed(self, item: SessionItem, _pspec) -> None:
        self.set_sensitive(not (item.syncing or item.backgrounding))

    def _on_status_changed(self, item: SessionItem, _pspec) -> None:
        for css in ("open", "attention", "background"):
            self.dot.remove_css_class(css)
        if item.status:
            self.dot.add_css_class(item.status)

    def _on_state_changed(self, item: SessionItem, _pspec) -> None:
        badge = self._state_badge
        for css in ("waiting-badge", "interrupted-badge"):
            badge.remove_css_class(css)
        if item.state == "waiting":
            badge.set_from_icon_name("dialog-question-symbolic")
            badge.add_css_class("waiting-badge")
            badge.set_tooltip_text(_("Claude is waiting for your reply"))
            badge.set_visible(True)
        elif item.state == "interrupted":
            badge.set_from_icon_name("process-stop-symbolic")
            badge.add_css_class("interrupted-badge")
            badge.set_tooltip_text(_("You interrupted Claude here"))
            badge.set_visible(True)
        else:
            badge.set_visible(False)

    def _on_right_click(self, _gesture, _n_press: int, x: float, y: float) -> None:
        self._sidebar.show_row_menu(self, x, y)


class SessionSidebar(Gtk.Box):
    """AdwToolbarView is a final type, so we wrap one instead of subclassing."""

    __gsignals__ = {
        "open-session": (GObject.SignalFlags.RUN_FIRST, None, (object, bool)),
        "open-many": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "trash-many": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "archive-many": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "open-placeholder": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "close-placeholder": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, store: SessionStore) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.store = store
        self._view = Adw.ToolbarView(vexpand=True)
        self.append(self._view)
        # Claude subscription usage readout, tucked under the session list.
        self.usage_panel = UsagePanel()
        self.usage_panel.set_visible(bool(store.state.get_setting("show_usage_panel")))
        self.append(self.usage_panel)
        self._collapsed: set[tuple] = set()
        self._selection_mode = False
        self._selected: set[str] = set()
        self._rows: dict[str, SessionRow] = {}
        self._header_rows: dict[tuple, GroupHeaderRow] = {}
        self._placeholders: dict[str, str] = {}  # placeholder id -> cwd
        self._placeholder_rows: dict[str, PlaceholderRow] = {}
        self._active_session_id: str | None = None
        self.show_folder_path = bool(store.state.get_setting("show_folder_path"))

        store.connect("refreshed", self._on_store_refreshed)

        # -- header ---------------------------------------------------------
        header = Adw.HeaderBar()
        # The content header (right pane) carries the window controls; without
        # AdwOverlaySplitView coordinating the two bars, hide them here so they
        # aren't duplicated at the pane boundary.
        header.set_show_end_title_buttons(False)
        header.set_title_widget(Adw.WindowTitle(title=_("Sessions")))

        self.select_btn = Gtk.ToggleButton(icon_name="object-select-symbolic")
        self.select_btn.set_tooltip_text(_("Select sessions"))
        self.select_btn.connect("toggled", lambda b: self._set_selection_mode(b.get_active()))
        header.pack_start(self.select_btn)

        menu = Gio.Menu()
        menu.append(_("Open session file…"), "win.open-session-file")
        menu.append(_("Show archived sessions"), "win.show-archived")
        menu.append(_("Delete archived sessions…"), "win.trash-archived")
        menu.append(_("MCP servers"), "win.mcp-servers")
        menu.append(_("Preferences"), "win.preferences")
        menu.append(_("About Collins"), "win.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu))

        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_btn.set_tooltip_text(_("Refresh session list"))
        refresh_btn.set_action_name("win.refresh")
        header.pack_end(refresh_btn)
        self._view.add_top_bar(header)

        # -- search + accordion controls --------------------------------------
        self.search_entry = Gtk.SearchEntry(placeholder_text=_("Search sessions…"), hexpand=True)
        self.search_entry.connect("search-changed", lambda *_: self._invalidate())

        collapse_all = Gtk.Button(icon_name="pan-up-symbolic")
        collapse_all.add_css_class("flat")
        collapse_all.set_tooltip_text(_("Collapse all groups"))
        collapse_all.connect("clicked", lambda *_: self._set_all_collapsed(True))

        expand_all = Gtk.Button(icon_name="pan-down-symbolic")
        expand_all.add_css_class("flat")
        expand_all.set_tooltip_text(_("Expand all groups"))
        expand_all.connect("clicked", lambda *_: self._set_all_collapsed(False))

        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        search_box.set_margin_start(8)
        search_box.set_margin_end(8)
        search_box.set_margin_bottom(6)
        search_box.append(self.search_entry)
        search_box.append(collapse_all)
        search_box.append(expand_all)
        self._view.add_top_bar(search_box)

        # -- list ------------------------------------------------------------
        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-activated", self._on_row_activated)
        self.list.set_filter_func(self._filter_row)

        # Accept project headers dragged to a new position in the list.
        self._indicator_row: Gtk.ListBoxRow | None = None
        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.connect("motion", self._on_drop_motion)
        drop.connect("leave", lambda *_: self._set_drop_indicator(None, True))
        drop.connect("drop", self._on_drop)
        self.list.add_controller(drop)

        scrolled = Gtk.ScrolledWindow(child=self.list)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        empty = Adw.StatusPage(
            icon_name="folder-symbolic",
            title=_("No sessions found"),
            description=_("Run claude in a project directory first — "
            "sessions will appear here automatically."),
        )
        empty.add_css_class("compact")

        self._content_stack = Gtk.Stack()
        self._content_stack.add_named(scrolled, "list")
        self._content_stack.add_named(empty, "empty")
        self._view.set_content(self._content_stack)

        self._view.add_bottom_bar(self._build_action_bar())

        # -- status footer ----------------------------------------------------
        self.footer = Gtk.Label()
        self.footer.add_css_class("dim-label")
        self.footer.add_css_class("caption")
        self.footer.set_margin_top(4)
        self.footer.set_margin_bottom(6)
        self.footer.set_ellipsize(_ELLIPSIZE_END)
        self._view.add_bottom_bar(self.footer)

        # Populate from whatever the shared store already holds (a sibling
        # window may have triggered the scan before this sidebar connected).
        if store.model.get_n_items():
            self._on_store_refreshed(store, True)

    # -- store sync ------------------------------------------------------------

    def _on_store_refreshed(self, store: SessionStore, order_changed: bool) -> None:
        self._selected &= set(store.sessions)
        if order_changed:
            self._rebuild_rows()
        self._update_selection_label()
        self._invalidate()
        # Kept (virtual) projects are headers with nothing under them, but they
        # are still a session list — not the "no sessions yet" status page.
        self._content_stack.set_visible_child_name(
            "empty" if not (store.sessions or store.empty_groups or self._placeholders) else "list"
        )
        self.update_footer()

    def update_footer(self) -> None:
        sessions = self.store.sessions.values()
        # Kept projects have no sessions to count them, but they are projects.
        projects = {s.project_name for s in sessions} | {
            label for _key, label, _cwd in self.store.empty_groups
        }
        open_tabs = sum(
            1
            for sid in self.store.sessions
            if (item := self.store.get_item(sid)) and item.status in ("open", "attention")
        )
        parts = [
            _("{n} sessions").format(n=len(sessions)),
            _("{n} projects").format(n=len(projects)),
            format_size(sum(s.size for s in sessions)),
        ]
        if open_tabs:
            parts.append(_("{n} open").format(n=open_tabs))
        self.footer.set_label(" · ".join(parts))

    def refresh_folder_path(self) -> None:
        """Re-read the 'show folder path' setting and update existing rows."""
        self.show_folder_path = bool(self.store.state.get_setting("show_folder_path"))
        for row in self._rows.values():
            row.update_folder_path(self.show_folder_path)

    def refresh_usage_panel(self) -> None:
        """Re-read the 'show usage panel' setting. Hiding unmaps the panel,
        which stops its poll timer on the next tick."""
        self.usage_panel.set_visible(bool(self.store.state.get_setting("show_usage_panel")))

    def _rebuild_rows(self) -> None:
        self.list.remove_all()
        self._rows = {}
        self._header_rows = {}
        self._placeholder_rows = {}

        # Directory per project group (from its most recent session with a
        # cwd), so headers can offer a "new session here" button. Favorites
        # mixes projects, so it never gets one.
        items_by_group: dict[tuple, list[SessionItem]] = {}
        group_cwds: dict[tuple, str] = {}
        for i in range(self.store.model.get_n_items()):
            item = self.store.model.get_item(i)
            key = item.group_key
            items_by_group.setdefault(key, []).append(item)
            if key != FAV_GROUP and key not in group_cwds and item.session.cwd:
                group_cwds[key] = item.session.cwd

        # Every header in display order: favorites pinned first, then all
        # projects — with or without visible session rows (empty ones keep
        # their "new session" button reachable) — in the user's order.
        headers: list[tuple[tuple, str, str | None]] = []
        if FAV_GROUP in items_by_group:
            headers.append((FAV_GROUP, _("Favorites"), None))
        empty_by_key = {key: (label, cwd) for key, label, cwd in self.store.empty_groups}
        for name in self.store.resolved_project_order:
            key = ("proj", name)
            if key in items_by_group:
                headers.append((key, name, group_cwds.get(key)))
            elif key in empty_by_key:
                headers.append((key, *empty_by_key[key]))

        # Transient "New Thread" rows for tabs whose session isn't resolved
        # yet, grouped under the project of their working directory. A project
        # with no sessions on disk still needs a header to hang them from.
        placeholders_by_group: dict[tuple, list[str]] = {}
        for pid, cwd in self._placeholders.items():
            placeholders_by_group.setdefault(self._placeholder_group_key(cwd), []).append(pid)
        known_keys = {key for key, _label, _cwd in headers}
        for key, pids in placeholders_by_group.items():
            if key not in known_keys:
                headers.append((key, key[1], self._placeholders[pids[0]]))

        # Expansion is persisted per group; unknown groups start collapsed.
        self._collapsed = {
            key
            for key, _label, _cwd in headers
            if not self.store.state.is_group_expanded(_group_state_key(key))
        }
        # Keep just-opened tabs visible: their groups ignore a collapsed state.
        self._collapsed -= set(placeholders_by_group)

        for key, label, cwd in headers:
            header = GroupHeaderRow(
                key,
                label,
                self.store.group_counts.get(key, 0) + len(placeholders_by_group.get(key, ())),
                key in self._collapsed,
                cwd=cwd,
                sidebar=self,
            )
            self._header_rows[key] = header
            self.list.append(header)
            for pid in placeholders_by_group.get(key, ()):
                prow = PlaceholderRow(pid, key, self)
                if pid == self._active_session_id:
                    prow.add_css_class("active-tab")
                self._placeholder_rows[pid] = prow
                self.list.append(prow)
            for item in items_by_group.get(key, []):
                row = SessionRow(item, self)
                if item.session_id == self._active_session_id:
                    row.add_css_class("active-tab")
                self._rows[item.session_id] = row
                self.list.append(row)
        self._apply_selection_to_rows()

    def _apply_selection_to_rows(self) -> None:
        for row in self._rows.values():
            row.check.set_visible(self._selection_mode)
            row.check.set_active(row.item.session_id in self._selected)

    def set_active_session(self, session_id: str | None) -> None:
        """Highlight the row of the session (or new-session placeholder)
        shown in the currently selected tab."""
        if session_id == self._active_session_id:
            return
        previous = self._row_for(self._active_session_id)
        if previous is not None:
            previous.remove_css_class("active-tab")
        self._active_session_id = session_id
        row = self._row_for(session_id)
        if row is not None:
            row.add_css_class("active-tab")

    def _row_for(self, row_id: str | None) -> Gtk.ListBoxRow | None:
        return self._rows.get(row_id) or self._placeholder_rows.get(row_id)

    # -- new-session placeholders ---------------------------------------------

    @staticmethod
    def _placeholder_group_key(cwd: str) -> tuple:
        """Group a placeholder the way Session.project_name groups sessions."""
        return ("proj", Path(cwd).name or cwd)

    def add_placeholder(self, placeholder_id: str, cwd: str) -> None:
        """Show a transient "New Thread" row for a tab with no session yet."""
        self._placeholders[placeholder_id] = cwd
        self._rebuild_rows()
        self._invalidate()
        self._content_stack.set_visible_child_name("list")

    def remove_placeholder(self, placeholder_id: str) -> None:
        if self._placeholders.pop(placeholder_id, None) is None:
            return
        self._rebuild_rows()
        self._invalidate()
        if not self.store.sessions and not self._placeholders:
            self._content_stack.set_visible_child_name("empty")

    def focus_search(self) -> None:
        self.search_entry.grab_focus()

    # -- filtering / grouping ----------------------------------------------------

    def _invalidate(self) -> None:
        self.list.invalidate_filter()

    def _group_has_match(self, group_key: tuple, query: str) -> bool:
        for i in range(self.store.model.get_n_items()):
            item = self.store.model.get_item(i)
            if item.group_key == group_key and query in item.search_text:
                return True
        return False

    def _filter_row(self, row: Gtk.ListBoxRow) -> bool:
        query = self.search_entry.get_text().strip().lower()
        if isinstance(row, GroupHeaderRow):
            # Headers stay visible when collapsed; during search, only for groups with matches.
            return self._group_has_match(row.group_key, query) if query else True
        if isinstance(row, PlaceholderRow):
            # Nothing to search yet; the group itself is never collapsed while
            # it holds a placeholder, but respect a collapse made afterwards.
            return not query and row.group_key not in self._collapsed
        if query:
            return query in row.item.search_text  # search ignores collapsed state
        return row.item.group_key not in self._collapsed

    def _toggle_group(self, group_key: tuple) -> None:
        expanded = group_key in self._collapsed
        if expanded:
            self._collapsed.discard(group_key)
        else:
            self._collapsed.add(group_key)
        self.store.state.set_group_expanded(_group_state_key(group_key), expanded)
        header = self._header_rows.get(group_key)
        if header is not None:
            header.set_collapsed(group_key in self._collapsed)
        self._invalidate()

    def _set_all_collapsed(self, collapsed: bool) -> None:
        self._collapsed = set(self._header_rows) if collapsed else set()
        self.store.state.set_groups_expanded(
            [_group_state_key(key) for key in self._header_rows], not collapsed
        )
        for group_key, header in self._header_rows.items():
            header.set_collapsed(group_key in self._collapsed)
        self._invalidate()

    # -- drag & drop project reordering ---------------------------------------

    def _project_headers(self) -> list[str]:
        """Project names in current display order (favorites excluded)."""
        return [key[1] for key in self._header_rows if key != FAV_GROUP]

    def _last_visible_row(self) -> Gtk.ListBoxRow | None:
        child = self.list.get_last_child()
        while child is not None:
            if isinstance(child, Gtk.ListBoxRow) and self._filter_row(child):
                return child
            child = child.get_prev_sibling()
        return None

    def _drop_anchor(self, y: float) -> tuple[str | None, Gtk.ListBoxRow | None, bool]:
        """Where a project dragged to `y` would land.

        Returns (project to insert before — None means the end, row to draw
        the insertion indicator on, whether the indicator goes above it).
        """
        names = self._project_headers()
        if not names:
            return None, None, True
        row = self.list.get_row_at_y(int(y))
        if row is None:  # pointer below the last row
            return None, self._last_visible_row(), False
        key = row.item.group_key if isinstance(row, SessionRow) else row.group_key
        if key == FAV_GROUP:  # favorites stays pinned first
            return names[0], self._header_rows.get(("proj", names[0])), True
        name = key[1]
        if isinstance(row, GroupHeaderRow):
            ok, bounds = row.compute_bounds(self.list)
            if ok and y - bounds.origin.y < bounds.size.height / 2:
                return name, row, True
        # Bottom half of a header, or one of its session rows: after this group.
        index = names.index(name) + 1 if name in names else len(names)
        if index >= len(names):
            return None, self._last_visible_row(), False
        return names[index], self._header_rows.get(("proj", names[index])), True

    def _set_drop_indicator(self, row: Gtk.ListBoxRow | None, above: bool) -> None:
        if self._indicator_row is not None and self._indicator_row is not row:
            self._indicator_row.remove_css_class("drop-above")
            self._indicator_row.remove_css_class("drop-below")
        self._indicator_row = row
        if row is not None:
            row.add_css_class("drop-above" if above else "drop-below")
            row.remove_css_class("drop-below" if above else "drop-above")

    def _on_drop_motion(self, _target: Gtk.DropTarget, _x: float, y: float) -> Gdk.DragAction:
        _before, row, above = self._drop_anchor(y)
        self._set_drop_indicator(row, above)
        return Gdk.DragAction.MOVE

    def _on_drop(self, _target: Gtk.DropTarget, value, _x: float, y: float) -> bool:
        self._set_drop_indicator(None, True)
        name = str(value)
        if ("proj", name) not in self._header_rows:
            return False  # not one of our headers (e.g. stray text drag)
        before, _row, _above = self._drop_anchor(y)
        if before == name:
            return False
        self.store.move_project(name, before)
        return True

    # -- activation ----------------------------------------------------------

    def _on_row_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if isinstance(row, GroupHeaderRow):
            self._toggle_group(row.group_key)
            return
        if isinstance(row, PlaceholderRow):
            if not self._selection_mode:  # focus the still-unbound tab
                self.emit("open-placeholder", row.placeholder_id)
            return
        if self._selection_mode:
            row.check.set_active(not row.check.get_active())
            return
        self.emit("open-session", row.item, False)

    # -- context menu ------------------------------------------------------------

    def show_row_menu(self, row: SessionRow, x: float, y: float) -> None:
        session_id = row.item.session_id
        variant = GLib.Variant("s", session_id)

        def item(label: str, action: str) -> Gio.MenuItem:
            menu_item = Gio.MenuItem.new(label, None)
            menu_item.set_action_and_target_value(f"win.{action}", variant)
            return menu_item

        provider = get_provider(row.item.session.provider)

        open_section = Gio.Menu()
        open_section.append_item(item(_("Open"), "open-session"))
        if _GHOSTTY:
            open_section.append_item(item(_("Open in Ghostty"), "open-ghostty"))
        if provider.supports_fork:
            open_section.append_item(item(_("Fork session"), "fork-session"))
        for cv in provider.chat_variants():
            if cv.label:
                label = _("Continue in chat ({mode})").format(mode=_(cv.label))
            else:
                label = _("Continue in chat")
            chat_item = Gio.MenuItem.new(label, None)
            chat_item.set_action_and_target_value(
                "win.resume-chat", GLib.Variant("s", f"{cv.key}:{session_id}")
            )
            open_section.append_item(chat_item)

        edit_section = Gio.Menu()
        edit_section.append_item(item(_("Rename…"), "rename-session"))
        edit_section.append_item(item(_("Regenerate name"), "regenerate-name"))
        fav_label = (
            _("Remove from favorites") if self.store.state.is_favorite(session_id) else _("Add to favorites")
        )
        edit_section.append_item(item(fav_label, "toggle-favorite"))
        edit_section.append_item(item(_("Details…"), "session-details"))
        edit_section.append_item(item(_("Replay…"), "replay-session"))
        edit_section.append_item(item(_("Copy session ID"), "copy-session-id"))
        edit_section.append_item(item(_("Export as Markdown…"), "export-session"))
        edit_section.append_item(item(_("Reveal transcript"), "reveal-transcript"))

        danger_section = Gio.Menu()
        archive_label = (
            _("Restore session") if self.store.state.is_archived(session_id) else _("Archive session")
        )
        danger_section.append_item(item(archive_label, "archive-session"))
        danger_section.append_item(item(_("Move transcript to trash…"), "trash-session"))
        danger_section.append_item(item(_("Delete permanently…"), "delete-session"))

        menu = Gio.Menu()
        menu.append_section(None, open_section)
        menu.append_section(None, edit_section)
        menu.append_section(None, danger_section)
        self._popup_menu(menu, row, x, y)

    def show_group_menu(self, row: GroupHeaderRow, x: float, y: float) -> None:
        project_name = row.group_key[1]

        open_section = Gio.Menu()
        if row.cwd:
            new_item = Gio.MenuItem.new(_("New session here"), None)
            new_item.set_action_and_target_value(
                "win.new-session-in", GLib.Variant("s", row.cwd)
            )
            open_section.append_item(new_item)

        danger_section = Gio.Menu()
        archive_label = (
            _("Restore project")
            if self.store.state.is_project_archived(project_name)
            else _("Archive project")
        )
        archive_item = Gio.MenuItem.new(archive_label, None)
        archive_item.set_action_and_target_value(
            "win.archive-project", GLib.Variant("s", project_name)
        )
        danger_section.append_item(archive_item)

        # A project kept after its last session went away has nothing left to
        # archive — dropping it is the only way it ever leaves the sidebar.
        if self.store.state.is_virtual_project(project_name):
            forget_item = Gio.MenuItem.new(_("Remove project from sidebar"), None)
            forget_item.set_action_and_target_value(
                "win.forget-project", GLib.Variant("s", project_name)
            )
            danger_section.append_item(forget_item)

        menu = Gio.Menu()
        menu.append_section(None, open_section)
        menu.append_section(None, danger_section)
        self._popup_menu(menu, row, x, y)

    def _popup_menu(self, menu: Gio.Menu, row: Gtk.ListBoxRow, x: float, y: float) -> None:
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(row)
        popover.set_has_arrow(False)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()

    # -- selection mode ------------------------------------------------------------

    def _build_action_bar(self) -> Gtk.ActionBar:
        self.action_bar = Gtk.ActionBar()
        self.action_bar.set_revealed(False)

        self.sel_label = Gtk.Label(label="0 selected")
        self.sel_label.add_css_class("dim-label")
        self.action_bar.pack_start(self.sel_label)

        all_btn = Gtk.Button(label=_("All"))
        all_btn.add_css_class("flat")
        all_btn.set_tooltip_text(_("Select all (filtered) sessions"))
        all_btn.connect("clicked", lambda *_: self._select_all(True))
        self.action_bar.pack_start(all_btn)

        none_btn = Gtk.Button(label=_("None"))
        none_btn.add_css_class("flat")
        none_btn.set_tooltip_text(_("Clear selection"))
        none_btn.connect("clicked", lambda *_: self._select_all(False))
        self.action_bar.pack_start(none_btn)

        for icon, tooltip, callback in (
            ("user-trash-symbolic", _("Move selected transcripts to trash…"), self._bulk_trash),
            ("archive-symbolic", _("Archive selected"), self._bulk_archive),
            ("non-starred-symbolic", _("Remove selected from favorites"), lambda: self._bulk_favorite(False)),
            ("starred-symbolic", _("Add selected to favorites"), lambda: self._bulk_favorite(True)),
            ("tab-new-symbolic", _("Open selected in tabs"), self._bulk_open),
        ):
            button = Gtk.Button(icon_name=icon)
            button.add_css_class("flat")
            button.set_tooltip_text(tooltip)
            button.connect("clicked", lambda _b, cb=callback: cb())
            self.action_bar.pack_end(button)
        return self.action_bar

    def _set_selection_mode(self, active: bool) -> None:
        self._selection_mode = active
        if not active:
            self._selected.clear()
        self._apply_selection_to_rows()
        self.action_bar.set_revealed(active)
        self._update_selection_label()

    def on_row_check_toggled(self, row: SessionRow, active: bool) -> None:
        if active:
            self._selected.add(row.item.session_id)
        else:
            self._selected.discard(row.item.session_id)
        self._update_selection_label()

    def _update_selection_label(self) -> None:
        self.sel_label.set_label(f"{len(self._selected)} selected")

    def _select_all(self, selected: bool) -> None:
        for row in self._rows.values():
            if selected and not self._filter_row(row):
                continue  # respect the current search filter
            row.check.set_active(selected)

    def _selected_items(self) -> list[SessionItem]:
        return [
            item for sid in self._selected if (item := self.store.get_item(sid)) is not None
        ]

    def _bulk_open(self) -> None:
        self.emit("open-many", self._selected_items())

    def _bulk_favorite(self, favorite: bool) -> None:
        self.store.set_favorites([i.session_id for i in self._selected_items()], favorite)

    def _bulk_archive(self) -> None:
        self.emit("archive-many", self._selected_items())

    def _bulk_trash(self) -> None:
        items = self._selected_items()
        if items:
            self.emit("trash-many", items)
