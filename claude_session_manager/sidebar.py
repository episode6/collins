"""Session sidebar: search, project accordion, favorites, selection mode.

Rows bind to SessionItem properties, so renames/stars/status changes update
in place; the list is only rebuilt when the store reports an order change.

Emits:
  open-session   (SessionItem, bool fork)
  open-many      (list[SessionItem])
  trash-many     (list[SessionItem])
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
    ) -> None:
        super().__init__()
        self.group_key = group_key
        self.set_selectable(False)

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


class SessionRow(Gtk.ListBoxRow):
    def __init__(self, item: SessionItem, sidebar: SessionSidebar) -> None:
        super().__init__()
        self.item = item
        self._sidebar = sidebar
        self.add_css_class("session-child")  # indented, with a left guide line

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(10)
        box.set_margin_end(12)

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

        agent_icon = Gtk.Image.new_from_icon_name(item.provider_icon)
        agent_icon.set_valign(Gtk.Align.CENTER)
        agent_icon.add_css_class("dim-label")
        agent_icon.set_tooltip_text(item.provider_label)
        top.append(agent_icon)

        time_label = Gtk.Label(valign=Gtk.Align.CENTER)
        time_label.add_css_class("dim-label")
        time_label.add_css_class("caption")
        top.append(time_label)

        self._state_badge = Gtk.Image(valign=Gtk.Align.CENTER)
        top.append(self._state_badge)

        star = Gtk.Button(valign=Gtk.Align.CENTER)
        star.add_css_class("flat")
        star.connect(
            "clicked",
            lambda *_: self.activate_action("win.toggle-favorite", GLib.Variant("s", item.session_id)),
        )
        top.append(star)

        rename = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER)
        rename.add_css_class("flat")
        rename.set_tooltip_text(_("Rename session"))
        rename.connect(
            "clicked",
            lambda *_: self.activate_action("win.rename-session", GLib.Variant("s", item.session_id)),
        )
        top.append(rename)
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
        item.bind_property(
            "favorite", star, "icon-name", flags,
            lambda _b, fav: "starred-symbolic" if fav else "non-starred-symbolic",
        )
        item.bind_property(
            "favorite", star, "tooltip-text", flags,
            lambda _b, fav: _("Remove from favorites") if fav else _("Add to favorites"),
        )

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
        Gtk.ListBoxRow.do_unroot(self)

    def _on_status_changed(self, item: SessionItem, _pspec) -> None:
        for css in ("open", "attention"):
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
    }

    def __init__(self, store: SessionStore) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.store = store
        self._view = Adw.ToolbarView(vexpand=True)
        self.append(self._view)
        self._collapsed: set[tuple] = set()
        self._known_groups: set[tuple] = set()  # groups seen before (for collapse-by-default)
        self._selection_mode = False
        self._selected: set[str] = set()
        self._rows: dict[str, SessionRow] = {}
        self._header_rows: dict[tuple, GroupHeaderRow] = {}
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
        menu.append(_("Show hidden sessions"), "win.show-hidden")
        menu.append(_("MCP servers"), "win.mcp-servers")
        menu.append(_("Preferences"), "win.preferences")
        menu.append(_("About Agent Session Manager"), "win.about")
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
        self._content_stack.set_visible_child_name("empty" if not store.sessions else "list")
        self.update_footer()

    def update_footer(self) -> None:
        sessions = self.store.sessions.values()
        projects = {s.project_name for s in sessions}
        open_tabs = sum(
            1 for sid in self.store.sessions if (item := self.store.get_item(sid)) and item.status
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

    def _rebuild_rows(self) -> None:
        self.list.remove_all()
        self._rows = {}
        self._header_rows = {}

        # Collapse project groups by default — but only the first time each is
        # seen, so manual expand/collapse choices survive live refreshes.
        groups = []
        # Directory per project group (from its most recent session with a
        # cwd), so headers can offer a "new session here" button. Favorites
        # mixes projects, so it never gets one.
        group_cwds: dict[tuple, str] = {}
        for i in range(self.store.model.get_n_items()):
            item = self.store.model.get_item(i)
            key = item.group_key
            if key not in groups:
                groups.append(key)
            if key != FAV_GROUP and key not in group_cwds and item.session.cwd:
                group_cwds[key] = item.session.cwd
        for key in groups:
            if key not in self._known_groups:
                self._collapsed.add(key)
        self._known_groups = set(groups)

        previous_group: tuple | None = None
        for i in range(self.store.model.get_n_items()):
            item = self.store.model.get_item(i)
            if item.group_key != previous_group:
                header = GroupHeaderRow(
                    item.group_key,
                    _("Favorites") if item.group_key == FAV_GROUP else item.group_label,
                    self.store.group_counts.get(item.group_key, 0),
                    item.group_key in self._collapsed,
                    cwd=group_cwds.get(item.group_key),
                )
                self._header_rows[item.group_key] = header
                self.list.append(header)
                previous_group = item.group_key
            row = SessionRow(item, self)
            self._rows[item.session_id] = row
            self.list.append(row)
        self._apply_selection_to_rows()

    def _apply_selection_to_rows(self) -> None:
        for row in self._rows.values():
            row.check.set_visible(self._selection_mode)
            row.check.set_active(row.item.session_id in self._selected)

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
        if query:
            return query in row.item.search_text  # search ignores collapsed state
        return row.item.group_key not in self._collapsed

    def _toggle_group(self, group_key: tuple) -> None:
        if group_key in self._collapsed:
            self._collapsed.discard(group_key)
        else:
            self._collapsed.add(group_key)
        header = self._header_rows.get(group_key)
        if header is not None:
            header.set_collapsed(group_key in self._collapsed)
        self._invalidate()

    def _set_all_collapsed(self, collapsed: bool) -> None:
        self._collapsed = set(self.store.group_counts) if collapsed else set()
        for group_key, header in self._header_rows.items():
            header.set_collapsed(group_key in self._collapsed)
        self._invalidate()

    # -- activation ----------------------------------------------------------

    def _on_row_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if isinstance(row, GroupHeaderRow):
            self._toggle_group(row.group_key)
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
        hide_label = _("Unhide session") if self.store.state.is_hidden(session_id) else _("Hide session")
        danger_section.append_item(item(hide_label, "hide-session"))
        danger_section.append_item(item(_("Move transcript to trash…"), "trash-session"))
        danger_section.append_item(item(_("Delete permanently…"), "delete-session"))

        menu = Gio.Menu()
        menu.append_section(None, open_section)
        menu.append_section(None, edit_section)
        menu.append_section(None, danger_section)

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
            ("view-conceal-symbolic", _("Hide selected"), self._bulk_hide),
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

    def _bulk_hide(self) -> None:
        self.store.hide_many([i.session_id for i in self._selected_items()])

    def _bulk_trash(self) -> None:
        items = self._selected_items()
        if items:
            self.emit("trash-many", items)
