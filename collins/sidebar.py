# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-31. Full change history: git log for this file.

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
import threading
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk  # noqa: E402

from . import footerapps, openwith, prmenu
from .chats import is_chat_cwd
from .formatting import format_size
from .i18n import _
from .models import CHATS_GROUP, FAV_GROUP, SessionItem
from .projecticons import project_icon_data
from .providers import get_provider
from .prstatus import PullRequest, from_records, resync, to_records
from .scrolling import offset_into_view
from .sessions import project_name_for_cwd
from .store import SessionStore
from .usagepanel import UsagePanel

_GHOSTTY = shutil.which("ghostty")
_ELLIPSIZE_END = 3  # Pango.EllipsizeMode.END
_ELLIPSIZE_START = 1  # Pango.EllipsizeMode.START

# Row highlight per session status. Both tab statuses share one fill (see
# row.session-child.running in app.py) — read or unread, the session has a tab
# open; "background", i.e. running detached, colors the guide line instead, as
# there is no tab to return to.
_STATUS_CSS = {
    "open": "running",
    "attention": "running",
    "background": "detached",
}
# The statuses that mean "this session has a tab open right now".
_IN_TAB_STATUSES = ("open", "attention")

# How far a project header's icon sits from the row's own left edge: the
# theme's sidebar-row padding (8px in Adwaita) plus .group-header's own 10px.
_HEADER_ICON_OFFSET = 18

# App icons in a project's "open in…" menu rows: symbolic-icon sized, so a row
# is no taller than the plain menu items above and below it.
_OPEN_WITH_ICON_PX = 16


def _session_child_indent(icon_size: int) -> int:
    """Left margin for a session row, so its card starts right where the icon
    of the project header above it ends.

    A widget margin, added on top of the margin the theme gives every sidebar
    row — that part is shared with the header, so it cancels out and only the
    header's icon offset plus the icon's width has to be matched.
    """
    return _HEADER_ICON_OFFSET + icon_size


def _open_with_row(icon: Gio.Icon | None, label: str, action: str, target: GLib.Variant) -> Gtk.Widget:
    """A menu row that shows an app's icon beside its name.

    A menu model can't do this: GtkModelButton takes an "icon" attribute but
    only draws it when the item has no text, so a plain Gio.MenuItem would
    silently drop the icon. Custom widgets slotted into the popover (the same
    trick prmenu.py's list is built from) can show both.
    """
    image = Gtk.Image.new_from_gicon(icon or Gio.ThemedIcon.new("application-x-executable"))
    image.set_pixel_size(_OPEN_WITH_ICON_PX)
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    box.append(image)
    box.append(Gtk.Label(label=label, xalign=0.0, hexpand=True))

    button = Gtk.Button(child=box)
    button.add_css_class("flat")
    button.add_css_class("open-with-row")  # menu-sized, and lit under the pointer
    button.connect("clicked", _on_open_with_clicked, action, target)
    return button


def _on_open_with_clicked(button: Gtk.Button, action: str, target: GLib.Variant) -> None:
    button.activate_action(action, target)
    popover = button.get_ancestor(Gtk.Popover)
    if popover is not None:
        popover.popdown()


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


def _project_icon_texture(svg: bytes | None, size: int) -> Gdk.Texture | None:
    """Rasterize project-icon bytes at the target icon size, forced through
    the SVG pixbuf loader. Forcing the type keeps repo-controlled bytes away
    from gdk-pixbuf's content sniffing (which would otherwise route a crafted
    file to any installed codec), and decoding at icon size bounds the
    raster surface regardless of the document's own canvas dimensions."""
    if svg is None:
        return None
    try:
        loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    except GLib.Error:  # SVG loader not installed
        return None
    loader.set_size(size, size)
    try:
        loader.write(svg)
        loader.close()
    except GLib.Error:
        try:
            loader.close()
        except GLib.Error:
            pass
        return None
    pixbuf = loader.get_pixbuf()
    if pixbuf is None:
        return None
    return Gdk.Texture.new_for_pixbuf(pixbuf)


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
        icon_size: int = 16,
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
            # (favorites and the virtual Chats group stay pinned first).
            if group_key[0] == "proj":
                drag = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
                drag.connect("prepare", self._on_drag_prepare)
                drag.connect("drag-begin", self._on_drag_begin)
                self.add_controller(drag)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.add_css_class("group-header")

        # One image doubles as project icon and collapse caret: the icon at
        # rest, the caret while the pointer is over the row.
        self._icon_svg = project_icon_data(cwd) if group_key[0] == "proj" else None
        self._texture = _project_icon_texture(self._icon_svg, icon_size)
        if self._texture is None:
            self._icon_svg = None  # unrenderable — stay on the fallback
            if group_key == FAV_GROUP:
                self._fallback_icon_name = "starred-symbolic"
            elif group_key == CHATS_GROUP:
                self._fallback_icon_name = "chat-bubble-symbolic"
            else:
                self._fallback_icon_name = "folder-symbolic"
        icon = Gtk.Image(valign=Gtk.Align.CENTER)
        icon.set_pixel_size(icon_size)
        self._icon = icon
        box.append(icon)

        self._hovered = False
        self._collapsed = collapsed
        hover = Gtk.EventControllerMotion()
        hover.connect("enter", self._on_hover_enter)
        hover.connect("leave", self._on_hover_leave)
        self.add_controller(hover)

        label = Gtk.Label(label=group_label.upper(), xalign=0.0, hexpand=True, valign=Gtk.Align.CENTER)
        label.add_css_class("caption-heading")
        label.add_css_class("dim-label")
        label.set_ellipsize(_ELLIPSIZE_END)
        box.append(label)

        count_label = Gtk.Label(label=str(count))
        count_label.add_css_class("count-badge")
        count_label.add_css_class("dim-label")
        count_label.set_visible(count > 0)
        box.append(count_label)

        if group_key == CHATS_GROUP:
            # Chats has no fixed folder: every new chat gets a fresh
            # throwaway directory of its own.
            new_btn = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
            new_btn.add_css_class("flat")
            new_btn.set_tooltip_text(_("New chat"))
            new_btn.connect(
                "clicked", lambda *_: self.activate_action("win.new-session-in-chats", None)
            )
            box.append(new_btn)
        elif cwd:
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
        self._collapsed = collapsed
        self._update_icon()

    def set_icon_size(self, size: int) -> None:
        self._icon.set_pixel_size(size)
        # A custom icon's texture was rasterized at the old size; re-render
        # it so it stays sharp instead of scaling.
        texture = _project_icon_texture(self._icon_svg, size)
        if texture is not None:
            self._texture = texture
        self._update_icon()

    def _update_icon(self) -> None:
        if self._hovered:
            self._icon.set_from_icon_name(
                "pan-end-symbolic" if self._collapsed else "pan-down-symbolic"
            )
            self._icon.add_css_class("dim-label")
        elif self._texture is not None:
            # The project ships its own icon; shown at the same size as the
            # symbolic icons, but in its own colors — no dim-label recoloring.
            self._icon.set_from_paintable(self._texture)
            self._icon.remove_css_class("dim-label")
        else:
            self._icon.set_from_icon_name(self._fallback_icon_name)
            self._icon.add_css_class("dim-label")

    def _on_hover_enter(self, _ctrl: Gtk.EventControllerMotion, _x: float, _y: float) -> None:
        self._hovered = True
        self._update_icon()

    def _on_hover_leave(self, _ctrl: Gtk.EventControllerMotion) -> None:
        self._hovered = False
        self._update_icon()

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
        self.add_css_class(_STATUS_CSS["open"])  # it stands for a live tab

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        box.set_valign(Gtk.Align.CENTER)  # match SessionRow in a taller row
        box.set_margin_top(4)  # match SessionRow: the flat button fills the row
        box.set_margin_bottom(4)
        box.set_margin_start(4)  # match SessionRow's title inset

        label = Gtk.Label(label=_("New Thread"), xalign=0.0, hexpand=True)
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

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        box.set_valign(Gtk.Align.CENTER)  # the row is taller than its content
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(4)  # air between the guide line and the title
        box.set_margin_end(0)  # theme row padding + the flat button's inset suffice

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)

        self.check = Gtk.CheckButton(valign=Gtk.Align.CENTER, visible=False)
        self.check.connect("toggled", lambda c: sidebar.on_row_check_toggled(self, c.get_active()))
        top.append(self.check)

        name_label = Gtk.Label(xalign=0.0, hexpand=True)
        name_label.set_ellipsize(_ELLIPSIZE_END)
        top.append(name_label)

        self._state_badge = Gtk.Image(valign=Gtk.Align.CENTER)
        top.append(self._state_badge)

        time_label = Gtk.Label(valign=Gtk.Align.CENTER)
        time_label.set_margin_start(8)  # keep the timestamp off a long title
        time_label.add_css_class("dim-label")
        time_label.add_css_class("caption")

        # Stop / background act on the tab this session is open in — the same
        # two operations as the header buttons, for a row that isn't
        # necessarily the focused tab. Hidden unless a tab is actually open on
        # the session (see _on_status_changed).
        stop_btn = Gtk.Button(icon_name="tab-close-symbolic", valign=Gtk.Align.CENTER)
        stop_btn.add_css_class("flat")
        stop_btn.set_tooltip_text(_("Exit session and close tab"))
        stop_btn.connect(
            "clicked",
            lambda *_: self.activate_action("win.stop-session", GLib.Variant("s", item.session_id)),
        )
        self._stop_btn = stop_btn

        bg_btn = Gtk.Button(icon_name="document-save-symbolic", valign=Gtk.Align.CENTER)
        bg_btn.add_css_class("flat")
        bg_btn.set_tooltip_text(_("Background session and close tab"))
        bg_btn.connect(
            "clicked",
            lambda *_: self.activate_action(
                "win.background-session", GLib.Variant("s", item.session_id)
            ),
        )
        self._bg_btn = bg_btn
        # Providers that can't detach never offer backgrounding.
        self._can_background = get_provider(item.session.provider).background_exit() is not None

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
        # A row a /bg fork replaced isn't archived — it's out of sight because
        # the fork's row stands in for it — so neither archiving nor restoring
        # it would change anything the user can see. No button.
        archive_btn.set_visible(sidebar.store.forward_state(item.session) != "moved")
        self._archive_btn = archive_btn

        # Leading the row's actions: the pull requests this session opened —
        # the same list the tab footer's caret shows, from a row whose tab
        # needn't be open (or exist) to read it. Clicking the button opens the
        # menu and nothing else: a button consumes the click that would
        # otherwise activate the row and open the session.
        self._pr_menu = prmenu.new_popover(Gtk.PositionType.BOTTOM)
        self._pr_menu.connect("closed", self._on_pr_menu_closed)
        pr_btn = Gtk.MenuButton(
            icon_name="github-symbolic", valign=Gtk.Align.CENTER, popover=self._pr_menu
        )
        pr_btn.add_css_class("flat")
        pr_btn.set_tooltip_text(_("Pull requests from this session"))
        pr_btn.set_create_popup_func(self._fill_pr_menu)
        self._pr_btn = pr_btn
        self._prs: list[PullRequest] = []
        self._pr_fetch = 0  # generation: a slow fetch can't land on a later opening
        # What a PR's actions need of the session behind them. From a row, the
        # session is only reachable through the window — which is also the only
        # thing holding the tab whose prompt they would go to.
        self._pr_host = prmenu.ActionHost(
            takes_prompt=lambda: (
                self.item.status in _IN_TAB_STATUSES
                and sidebar.takes_prompt(self.item.session_id)
            ),
            has_changes=lambda: sidebar.has_changes(self.item.session_id),
            send_prompt=lambda prompt: self.activate_action(
                "win.send-prompt", GLib.Variant("(ss)", (self.item.session_id, prompt))
            ),
            refresh=self._refresh_prs,
        )
        self.sync_prs()

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        actions.append(pr_btn)
        actions.append(stop_btn)
        actions.append(bg_btn)
        actions.append(archive_btn)  # rightmost: the one action every row has

        # Timestamp and row actions share one slot, so the row reads as either
        # "when" or "what can I do here", never both: at rest the timestamp
        # sits flush right and the title gets the freed width, on hover the
        # buttons take the slot over. A stack (rather than set_visible on each
        # side) keeps the taller of the two heights reserved — otherwise the
        # row grows on hover and the list below it jumps. None of the actions
        # are pointer-only: archiving is in the row's context menu, and
        # stopping/backgrounding are in the header of the session's own tab.
        self._action_stack = Gtk.Stack(hhomogeneous=False, vhomogeneous=True)
        self._action_stack.add_named(time_label, "rest")
        self._action_stack.add_named(actions, "hover")
        top.append(self._action_stack)

        self._hovered = False
        hover = Gtk.EventControllerMotion()
        hover.connect("enter", self._on_hover_enter)
        hover.connect("leave", self._on_hover_leave)
        self.add_controller(hover)
        box.append(top)

        path_label = Gtk.Label(xalign=0.0)
        path_label.set_ellipsize(_ELLIPSIZE_START)  # keep the tail (the leaf dir) visible
        path_label.add_css_class("dim-label")
        path_label.add_css_class("caption")
        path_label.set_label(_abbreviate_path(item.session.cwd))
        # Chat sessions live in throwaway directories — the path is noise.
        path_label.set_visible(
            sidebar.show_folder_path
            and bool(item.session.cwd)
            and not is_chat_cwd(item.session.cwd)
        )
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
        # The background button has its own gate on top of the row's: a /bg is
        # only safe once this session is registered and no other handoff is
        # still waiting for its new id.
        self._can_background_handler = item.connect(
            "notify::can-background", self._on_can_background_changed
        )
        self._on_can_background_changed(item, None)

        # Status highlight + state badge need CSS-class updates: plain signals,
        # detached on unroot.
        self._status_handler = item.connect("notify::status", self._on_status_changed)
        self._state_handler = item.connect("notify::state", self._on_state_changed)
        self._on_status_changed(item, None)
        self._on_state_changed(item, None)

        right_click = Gtk.GestureClick(button=3)
        right_click.connect("pressed", self._on_right_click)
        self.add_controller(right_click)

    def sync_prs(self) -> None:
        """Re-read the session's saved PRs; the button is only there with one.

        Called as the row is built and again whenever the session's tab reports
        a new list, so a session that opens its first PR grows the button
        without waiting for the sidebar to be rebuilt around it.
        """
        self._prs = from_records(self._sidebar.store.state.get_session_prs(self.item.session_id))
        self._pr_btn.set_visible(bool(self._prs))

    def _fill_pr_menu(self, _button: Gtk.MenuButton) -> None:
        """Put the saved list up at once, and refresh it behind a spinner.

        The footer's copy of this menu is filled from a poll that is already
        keeping it current. This one has only what was saved for the session,
        which is titles and merges and no CI status at all (see
        prstatus.to_record) — a status that survived a restart would be a
        yesterday's check, so none is kept. Opening the menu is therefore what
        fetches: the rows go up immediately, since the titles and numbers are
        the readable part, with a spinner in the column each status will land
        in.
        """
        prmenu.fill(self._pr_menu, self._prs, loading=True, host=self._pr_host)
        self._refresh_prs()

    def _refresh_prs(self) -> None:
        """Fetch every PR's title and status, and land them (see below).

        Called as the menu opens, and again after an action changed one of
        them on GitHub — a merge from the submenu is the one case where what
        the list says goes stale the moment it is read.
        """
        self._pr_fetch += 1
        token = self._pr_fetch
        prs = list(self._prs)
        threading.Thread(
            target=self._resync_prs, args=(token, prs), name="pr-menu", daemon=True
        ).start()

    def _resync_prs(self, token: int, prs: list[PullRequest]) -> None:
        """Fetch every PR's title and status. Runs off the main loop."""
        refreshed = resync(prs)
        GLib.idle_add(self._pr_menu_refreshed, token, refreshed)

    def _pr_menu_refreshed(self, token: int, prs: list[PullRequest]) -> bool:
        """Land a refresh: into the menu if it is still up, and onto disk.

        Saved either way — a title learned here is worth keeping whether or not
        anyone is still reading the menu it was fetched for, and it is what the
        next opening (and the footer, if the session is opened in a tab) starts
        from. A fetch overtaken by a later opening is dropped whole: that
        opening has its own on the way.
        """
        if token != self._pr_fetch:
            return GLib.SOURCE_REMOVE
        self._prs = prs
        self._sidebar.store.state.set_session_prs(self.item.session_id, to_records(prs))
        if self._pr_menu.get_visible():
            prmenu.update(self._pr_menu, prs, self._pr_host)
        return GLib.SOURCE_REMOVE

    def _on_hover_enter(self, *_args) -> None:
        self._hovered = True
        self._action_stack.set_visible_child_name("hover")

    def _on_hover_leave(self, *_args) -> None:
        # The pointer leaving isn't the whole story while the PR menu is open:
        # a popover takes a pointer grab, and the grab arrives here as a leave.
        # Swapping the buttons back out for the timestamp would hide the button
        # the menu hangs off — and a popover goes down with the widget it is
        # attached to, so the menu would close in the act of being opened.
        self._hovered = False
        if not self._pr_menu.get_visible():
            self._action_stack.set_visible_child_name("rest")

    def _on_pr_menu_closed(self, _popover: Gtk.Popover) -> None:
        if not self._hovered:
            self._action_stack.set_visible_child_name("rest")

    def update_folder_path(self, show: bool) -> None:
        self._path_label.set_visible(
            show and bool(self.item.session.cwd) and not is_chat_cwd(self.item.session.cwd)
        )

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
        if self._can_background_handler is not None:
            self.item.disconnect(self._can_background_handler)
            self._can_background_handler = None
        Gtk.ListBoxRow.do_unroot(self)

    def _on_sensitive_changed(self, item: SessionItem, _pspec) -> None:
        self.set_sensitive(not (item.syncing or item.backgrounding))

    def _on_can_background_changed(self, item: SessionItem, _pspec) -> None:
        """Grey the background button out — rather than hide it — while the
        handoff would be unsafe, and say why: the button coming and going as a
        session resolves would read as a glitch."""
        self._bg_btn.set_sensitive(item.can_background)
        self._bg_btn.set_tooltip_text(
            _("Background session and close tab")
            if item.can_background
            else _("Backgrounding is unavailable until this session is registered "
                   "and any handoff in progress finishes")
        )

    def _on_status_changed(self, item: SessionItem, _pspec) -> None:
        # The card itself carries the status: a session running in a tab gets a
        # brighter background, and one running detached colors its left guide
        # line instead.
        for css in _STATUS_CSS.values():
            self.remove_css_class(css)
        status_css = _STATUS_CSS.get(item.status)
        if status_css is not None:
            self.add_css_class(status_css)

        # Stopping and backgrounding both work on the session's tab, so they
        # only make sense while one is open: a detached (/bg) or idle session
        # has no tab to exit or hand over to the background.
        in_tab = item.status in _IN_TAB_STATUSES
        self._stop_btn.set_visible(in_tab)
        self._bg_btn.set_visible(in_tab and self._can_background)

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
        # The rows were rebuilt, so row_order() may read differently: the tab
        # bar follows the list, and this is what tells it to catch up.
        "rows-reordered": (GObject.SignalFlags.RUN_FIRST, None, ()),
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
        self._row_order: list[str] = []  # session/placeholder ids, top to bottom
        self._active_session_id: str | None = None
        # "Is this session sitting at an empty prompt?", for a row's PR actions
        # (see SessionRow._pr_host). Only the window can answer — the tab is
        # its — so it replaces this the moment it builds the sidebar; until
        # then, and for a session it has no tab for, the answer is no.
        self.takes_prompt: Callable[[str], bool] = lambda _session_id: False
        # "Does this session's terminal have uncommitted work in front of it?",
        # for the same rows' "Open pull request". Replaced by the window on the
        # same terms, and no for as long as it hasn't been.
        self.has_changes: Callable[[str], bool] = lambda _session_id: False
        # Scrolling the list is deferred to an idle callback (see
        # _schedule_scroll): the offset to restore and the row to reveal when
        # it runs, plus the id of the pending source.
        self._pending_offset: float | None = None
        self._pending_row: str | None = None
        self._scroll_source: int | None = None
        self._activated_row_id: str | None = None
        self.show_folder_path = bool(store.state.get_setting("show_folder_path"))

        store.connect("refreshed", self._on_store_refreshed)

        # -- header ---------------------------------------------------------
        header = Adw.HeaderBar()
        # The content header (right pane) carries the window controls; without
        # AdwOverlaySplitView coordinating the two bars, hide them here so they
        # aren't duplicated at the pane boundary.
        header.set_show_end_title_buttons(False)
        self._header = header
        self._window_title = Adw.WindowTitle(title=_("Sessions"))
        header.set_title_widget(self._window_title)

        # Search is folded away behind a button: it costs the list a whole row
        # when it sits there permanently, and it is reached a handful of times
        # a session. Opening it hands the entry everything between the header's
        # buttons (see _set_search_active).
        self.search_entry = Gtk.SearchEntry(placeholder_text=_("Search sessions…"), hexpand=True)
        self.search_entry.connect("search-changed", lambda *_: self._invalidate())
        self.search_entry.connect("stop-search", lambda *_: self.search_btn.set_active(False))

        menu = Gio.Menu()
        menu.append(_("Select multiple sessions"), "win.select-sessions")
        menu.append(_("Open session file…"), "win.open-session-file")
        menu.append(_("Show archived sessions"), "win.show-archived")
        menu.append(_("Delete archived sessions…"), "win.trash-archived")
        menu.append(_("MCP servers"), "win.mcp-servers")
        menu.append(_("Preferences"), "win.preferences")
        menu.append(_("About Collins"), "win.about")
        # Menu and search share the left end, so the header's weight is even:
        # two buttons on each side of the title.
        self._menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        header.pack_start(self._menu_btn)

        self.search_btn = Gtk.ToggleButton(icon_name="system-search-symbolic")
        self.search_btn.set_tooltip_text(_("Search sessions"))
        self.search_btn.connect("toggled", lambda b: self._set_search_active(b.get_active()))
        header.pack_start(self.search_btn)

        self._refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        self._refresh_btn.set_tooltip_text(_("Refresh session list"))
        self._refresh_btn.set_action_name("win.refresh")
        header.pack_end(self._refresh_btn)

        # One button for both directions: it folds every group away, and once
        # nothing is left expanded it opens them all again.
        self._collapse_btn = Gtk.Button()
        self._collapse_btn.connect(
            "clicked", lambda *_: self._set_all_collapsed(not self._all_collapsed())
        )
        header.pack_end(self._collapse_btn)
        self._update_collapse_button()
        self._view.add_top_bar(header)

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
        self._scrolled = scrolled

        # No "no sessions yet" status page: the permanent Chats header means
        # the list is never empty.
        self._view.set_content(scrolled)

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
        self.update_footer()

    def update_footer(self) -> None:
        sessions = self.store.sessions.values()
        # Kept projects have no sessions to count them, but they are projects.
        # All chat sessions together count as one pseudo-project.
        projects = {s.project_name for s in sessions if not is_chat_cwd(s.cwd)} | {
            label for _key, label, _cwd in self.store.empty_groups
        }
        if any(is_chat_cwd(s.cwd) for s in sessions):
            projects.add("Chats")
        open_tabs = sum(
            1
            for sid in self.store.sessions
            if (item := self.store.get_item(sid)) and item.status in _IN_TAB_STATUSES
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

    def refresh_project_icon_size(self) -> None:
        """Re-read the 'project icon size' setting and resize existing
        header icons in place; session rows re-indent to stay aligned just
        past the new icon width."""
        size = self._project_icon_size()
        for header in self._header_rows.values():
            header.set_icon_size(size)
        indent = _session_child_indent(size)
        for child in (*self._rows.values(), *self._placeholder_rows.values()):
            child.set_margin_start(indent)

    def _project_icon_size(self) -> int:
        return int(self.store.state.get_setting("project_icon_size") or 16)

    def _rebuild_rows(self) -> None:
        self._remember_scroll()
        self.list.remove_all()
        self._rows = {}
        self._header_rows = {}
        self._placeholder_rows = {}
        self._row_order = []

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
        # The virtual Chats project is always there — it's where "start a
        # session without picking a folder" lives, sessions or not.
        headers.append((CHATS_GROUP, _("Chats"), None))
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

        icon_size = self._project_icon_size()
        child_indent = _session_child_indent(icon_size)
        for key, label, cwd in headers:
            header = GroupHeaderRow(
                key,
                label,
                self.store.group_counts.get(key, 0) + len(placeholders_by_group.get(key, ())),
                key in self._collapsed,
                cwd=cwd,
                sidebar=self,
                icon_size=icon_size,
            )
            self._header_rows[key] = header
            self.list.append(header)
            for pid in placeholders_by_group.get(key, ()):
                prow = PlaceholderRow(pid, key, self)
                prow.set_margin_start(child_indent)
                if pid == self._active_session_id:
                    prow.add_css_class("active-tab")
                self._placeholder_rows[pid] = prow
                self._row_order.append(pid)
                self.list.append(prow)
            for item in items_by_group.get(key, []):
                row = SessionRow(item, self)
                row.set_margin_start(child_indent)
                if item.session_id == self._active_session_id:
                    row.add_css_class("active-tab")
                self._rows[item.session_id] = row
                self._row_order.append(item.session_id)
                self.list.append(row)
        self._apply_selection_to_rows()
        self._update_collapse_button()
        self.emit("rows-reordered")

    def row_order(self) -> list[str]:
        """Every session and placeholder row's id, top to bottom.

        Rows a collapsed group or the search filter is hiding are in here too:
        this is what the list holds, not what is on screen. The tab bar orders
        itself by this, and a tab has no business moving because a group was
        folded away or a search narrowed the list.
        """
        return list(self._row_order)

    def _apply_selection_to_rows(self) -> None:
        for row in self._rows.values():
            row.check.set_visible(self._selection_mode)
            row.check.set_active(row.item.session_id in self._selected)

    def sync_session_prs(self, session_id: str) -> None:
        """A session's PR list changed: re-read it on that session's row.

        The window calls this whenever a tab saves a new list, so a session
        that has just opened its first PR gains the button that opens it — the
        rows themselves are only rebuilt when the list's order changes, which
        opening a PR isn't.
        """
        row = self._rows.get(session_id)
        if row is not None:
            row.sync_prs()

    def set_active_session(self, session_id: str | None) -> None:
        """Highlight the row of the session (or new-session placeholder)
        shown in the currently selected tab, and scroll it into view."""
        # A row activated here put the tab on screen itself, so it is already
        # visible; anything else — the tab bar, a shortcut, a closing tab
        # handing the selection on — may well have made a row active that sits
        # far outside the scrolled view.
        clicked_here = self._activated_row_id == session_id
        self._activated_row_id = None
        if session_id == self._active_session_id:
            return
        previous = self._row_for(self._active_session_id)
        if previous is not None:
            previous.remove_css_class("active-tab")
        self._active_session_id = session_id
        row = self._row_for(session_id)
        if row is not None:
            row.add_css_class("active-tab")
        if session_id is not None and not clicked_here:
            self._scroll_row_into_view(session_id)

    def _row_for(self, row_id: str | None) -> Gtk.ListBoxRow | None:
        return self._rows.get(row_id) or self._placeholder_rows.get(row_id)

    # -- scrolling -------------------------------------------------------------

    def _scroll_row_into_view(self, row_id: str) -> None:
        """Ask for a row to be shown, whether or not it exists yet: a row that
        becomes active while the store is mid-refresh is only built by the
        rebuild that follows."""
        self._pending_row = row_id
        self._schedule_scroll()

    def _remember_scroll(self) -> None:
        """Note where the list stands, to be restored once it is rebuilt.

        A rebuild drops every row and re-adds it, and an empty list has
        nothing to scroll, so the offset would otherwise collapse to the top
        on every refresh that reorders the store — yanking the list away from
        whatever the user was looking at. Restoring the raw offset can still
        leave the active row half off the edge when the rebuild removes a row
        above it (exactly what archiving does), so an active row that is on
        screen now is asked for again afterwards; the ask costs nothing when
        it stayed put.
        """
        adjustment = self._scrolled.get_vadjustment()
        self._pending_offset = adjustment.get_value()
        if self._pending_row is None and self._row_on_screen(self._active_session_id):
            self._pending_row = self._active_session_id
        self._schedule_scroll()

    def _schedule_scroll(self) -> None:
        """Run _apply_scroll once the pending work settles.

        Below the frame clock's redraw priority, so rows appended in this turn
        of the loop have been laid out by the time their position is read.
        """
        if self._scroll_source is None:
            self._scroll_source = GLib.idle_add(self._apply_scroll, priority=GLib.PRIORITY_LOW)

    def _row_bounds(self, row_id: str | None) -> tuple[float, float] | None:
        """(top, height) of a row within the list, or None when it has no
        place on screen — unknown id, or filtered out by a collapsed group or
        the search entry."""
        row = self._row_for(row_id)
        if row is None or not row.get_child_visible():
            return None
        ok, bounds = row.compute_bounds(self.list)
        return (bounds.origin.y, bounds.size.height) if ok else None

    def _row_on_screen(self, row_id: str | None) -> bool:
        bounds = self._row_bounds(row_id)
        if bounds is None:
            return False
        adjustment = self._scrolled.get_vadjustment()
        top, height = bounds
        return (
            top + height > adjustment.get_value()
            and top < adjustment.get_value() + adjustment.get_page_size()
        )

    def _apply_scroll(self) -> bool:
        self._scroll_source = None
        adjustment = self._scrolled.get_vadjustment()
        if self._pending_offset is not None:
            # Where the list was before the rebuild; clamped for us if it has
            # since grown shorter than that.
            adjustment.set_value(self._pending_offset)
            self._pending_offset = None
        bounds = self._row_bounds(self._pending_row)
        self._pending_row = None
        if bounds is not None:
            top, height = bounds
            adjustment.set_value(
                offset_into_view(
                    top, height, adjustment.get_value(), adjustment.get_page_size()
                )
            )
        return GLib.SOURCE_REMOVE

    # -- new-session placeholders ---------------------------------------------

    @staticmethod
    def _placeholder_group_key(cwd: str) -> tuple:
        """Group a placeholder the way the store's _group_key groups sessions."""
        if is_chat_cwd(cwd):
            return CHATS_GROUP
        return ("proj", project_name_for_cwd(cwd))

    def add_placeholder(self, placeholder_id: str, cwd: str) -> None:
        """Show a transient "New Thread" row for a tab with no session yet."""
        self._placeholders[placeholder_id] = cwd
        self._rebuild_rows()
        self._invalidate()

    def remove_placeholder(self, placeholder_id: str) -> None:
        if self._placeholders.pop(placeholder_id, None) is None:
            return
        self._rebuild_rows()
        self._invalidate()

    # -- search ------------------------------------------------------------------

    def _set_search_active(self, active: bool) -> None:
        """Swap the sidebar's title for the search entry, and back.

        The entry takes the title's place, so it stretches across everything
        between the header's buttons — those all stay put and reachable, and
        the X the search button turns into folds the entry away again.
        """
        self.search_btn.set_icon_name(
            "window-close-symbolic" if active else "system-search-symbolic"
        )
        self.search_btn.set_tooltip_text(_("Close search") if active else _("Search sessions"))
        if active:
            self._header.set_title_widget(self.search_entry)
            self.search_entry.grab_focus()
        else:
            self.search_entry.set_text("")  # closing search unfilters the list
            self._header.set_title_widget(self._window_title)

    def focus_search(self) -> None:
        """Open search if it is folded away, and put the cursor in it."""
        self.search_btn.set_active(True)
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
        self._update_collapse_button()
        self._invalidate()

    def _set_all_collapsed(self, collapsed: bool) -> None:
        self._collapsed = set(self._header_rows) if collapsed else set()
        self.store.state.set_groups_expanded(
            [_group_state_key(key) for key in self._header_rows], not collapsed
        )
        for group_key, header in self._header_rows.items():
            header.set_collapsed(group_key in self._collapsed)
        self._update_collapse_button()
        self._invalidate()

    def _all_collapsed(self) -> bool:
        return bool(self._header_rows) and set(self._header_rows) <= self._collapsed

    def _update_collapse_button(self) -> None:
        """Point the accordion toggle at whichever move is left to make."""
        expands = self._all_collapsed()
        self._collapse_btn.set_icon_name("pan-down-symbolic" if expands else "pan-up-symbolic")
        self._collapse_btn.set_tooltip_text(
            _("Expand all groups") if expands else _("Collapse all groups")
        )

    # -- drag & drop project reordering ---------------------------------------

    def _project_headers(self) -> list[str]:
        """Project names in current display order (pinned groups excluded)."""
        return [key[1] for key in self._header_rows if key[0] == "proj"]

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
        if key in (FAV_GROUP, CHATS_GROUP):  # pinned groups stay first
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
                self._activated_row_id = row.placeholder_id
                self.emit("open-placeholder", row.placeholder_id)
            return
        if self._selection_mode:
            row.check.set_active(not row.check.get_active())
            return
        # Remembered until the tab selection comes back around, so
        # set_active_session can tell this row apart from one made active
        # somewhere else and leave the list where the click found it.
        self._activated_row_id = row.item.session_id
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
        # A row a /bg fork replaced is out of sight because the fork stands in
        # for it, not because it's archived — archiving/restoring it would be
        # a no-op, so the menu doesn't offer it (trash/delete still apply).
        if self.store.forward_state(row.item.session) != "moved":
            archive_label = (
                _("Restore session")
                if self.store.state.is_archived(session_id)
                else _("Archive session")
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
        if row.group_key == CHATS_GROUP:
            # The Chats group is permanent: nothing to archive or remove.
            menu = Gio.Menu()
            menu.append(_("New chat"), "win.new-session-in-chats")
            self._popup_menu(menu, row, x, y)
            return

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
        rows: list[Gtk.Widget] = []
        if row.cwd:
            menu.append_section(None, self._open_with_section(row.cwd, rows))
        menu.append_section(None, danger_section)
        self._popup_menu(menu, row, x, y, rows)

    def _open_with_section(self, cwd: str, rows: list[Gtk.Widget]) -> Gio.Menu:
        """Ways to hand the project's folder to another app: the user's own
        picks (the footer apps, in their configured order), then the file
        manager and terminal the desktop hands out.

        Each is a custom widget rather than a menu item — see _open_with_row —
        so it can show the app's own icon; the built widgets are appended to
        *rows* for _popup_menu to slot into the popover, in order.
        """
        section = Gio.Menu()

        def add(icon: Gio.Icon | None, label: str, action: str, target: GLib.Variant) -> None:
            item = Gio.MenuItem.new(None, None)
            item.set_attribute_value("custom", GLib.Variant("s", f"open-with-{len(rows)}"))
            section.append_item(item)
            rows.append(_open_with_row(icon, label, action, target))

        configured = set()
        for app_id, info in footerapps.resolve_apps(
            list(self.store.state.get_setting("footer_apps") or [])
        ):
            configured.add(app_id)
            add(
                info.get_icon(),
                _("Open in {name}").format(name=info.get_display_name()),
                "win.open-folder-app",
                GLib.Variant("(ss)", (app_id, cwd)),
            )

        # Role labels rather than app names for these two: what you get is
        # whatever the desktop nominates, and the icon already says which.
        # Skipped when the user has added that very app themselves.
        manager = openwith.default_file_manager()
        if manager is None or manager.get_id() not in configured:
            icon = manager.get_icon() if manager else Gio.ThemedIcon.new("folder-symbolic")
            add(icon, _("Open in File Manager"), "win.open-folder", GLib.Variant("s", cwd))

        terminal = openwith.default_terminal()
        if terminal is not None and terminal.get_id() not in configured:
            icon = terminal.get_icon() or Gio.ThemedIcon.new("utilities-terminal-symbolic")
            add(icon, _("Open in Terminal"), "win.open-folder-terminal", GLib.Variant("s", cwd))
        return section

    def _popup_menu(
        self,
        menu: Gio.Menu,
        row: Gtk.ListBoxRow,
        x: float,
        y: float,
        custom_rows: list[Gtk.Widget] | None = None,
    ) -> None:
        popover = Gtk.PopoverMenu.new_from_model(menu)
        for index, widget in enumerate(custom_rows or ()):
            popover.add_child(widget, f"open-with-{index}")
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

    def set_selection_mode(self, active: bool) -> None:
        """Show a checkbox on every row and reveal the bulk action bar. Driven
        by the window's "Select multiple sessions" menu item."""
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
