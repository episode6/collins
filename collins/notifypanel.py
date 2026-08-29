# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The bell in the header bar and the notification history it opens.

Two widgets and the split that hosts one of them. `NotificationBell` is the
header's toggle: a bell wearing the unread count as an accent pill (the same
number the tray icon and the dock badge show — traymodel.badge_text caps it
at "9+" for all three). `NotificationSheet` is the history: every row the
notification center holds, unread first, in a sheet that slides in over the
content from the right edge and stays while the user triages.

The sheet is libadwaita's own: `wrap_content` puts an Adw.OverlaySplitView
around the window's content stack, collapsed, with the sheet as its end
sidebar — which buys the scrim, the slide transition and Escape-to-close
without a line of animation code, and puts the sheet in the right place, under
the header bar rather than over it, because the header bar belongs to the
toolbar view the split sits inside. The bell's `active` and the split's
`show-sidebar` are bound both ways (see MainWindow), so the Escape the split
answers turns the bell off too.

Nothing here decides anything. What the tooltip says, how old a row is, how
a coalesced bell counts itself and which rows are "Unread" versus "Earlier"
are notifycenter's helpers, worked out and unit-tested without a toolkit;
this module lays them out. Reading, too, is the center's: a click on a row
goes to its session and marks that row read, and nothing else in the sheet
— not opening it, not scrolling it — reads a row on the user's behalf. Going
to the session is reading; so is saying so (Mark all read, or a row's own
Mark read). The one number the widgets read from a setting is the footer's
sound name, and the footer only names it.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import notifycenter, traymodel  # noqa: E402
from .i18n import _  # noqa: E402
from .notifycenter import Notification, NotificationCenter  # noqa: E402

# Bundled under data/icons and namespaced, so an installed icon theme's own
# bell can never shadow it (see installed-icons-shadow-bundled). The theme's
# preferences-system-notifications-symbolic lives under symbolic/legacy/ and
# "legacy" means it can go.
BELL_ICON = "collins-bell-symbolic"
# The synthetic row's mark: a dot, coloured the sidebar's finished-run green.
FINISHED_MARK_ICON = "circle-fill-symbolic"

# The sheet's one width. Wide enough for a two-line body beside a 16px icon
# and a time, narrow enough to leave the terminal's left two thirds alone at
# the window's default size.
SHEET_WIDTH = 380

# The setting the footer names (its picker arrives with the sound itself).
SOUND_SETTING = "notification_sound"

ACTION_PREFIX = "notify"


class NotificationBell(Gtk.Overlay):
    """The header bar's bell: a toggle button with the unread count over its
    top-right corner.

    An overlay around the button rather than inside it, so the badge can sit
    on the button's corner — over its padding, where a badge belongs — instead
    of over the bell's own artwork. The badge ignores the pointer, so a click
    on the number is a click on the bell.
    """

    def __init__(self) -> None:
        super().__init__()
        self.button = Gtk.ToggleButton(icon_name=BELL_ICON)
        self.button.add_css_class("flat")
        self.set_child(self.button)
        self._badge = Gtk.Label(
            halign=Gtk.Align.END,
            valign=Gtk.Align.START,
            can_target=False,
            visible=False,
        )
        self._badge.add_css_class("notification-badge")
        self.add_overlay(self._badge)
        self.set_unread(0)

    def set_unread(self, unread: int) -> None:
        """Show *unread* on the badge — nothing at zero — and say it in the
        tooltip. traymodel.badge_text is the tray's own cap, so the header
        and the panel never disagree past nine."""
        text = traymodel.badge_text(unread)
        self._badge.set_label(text)
        self._badge.set_visible(bool(text))
        self.button.set_tooltip_text(notifycenter.bell_tooltip(unread))

    def badge_text(self) -> str:
        """What the badge shows right now ("" while hidden) — for the headless
        checks, which can't read a label off a screen."""
        return self._badge.get_label() if self._badge.get_visible() else ""


def wrap_content(content: Gtk.Widget, sheet: Gtk.Widget) -> Adw.OverlaySplitView:
    """The split that hosts the sheet: *content* stays where it is, the sheet
    becomes a collapsed end sidebar that shows over it on demand.

    Collapsed from the start and never un-collapsed: the sheet is a sheet,
    not a pane, whatever the window's width. A fixed width, both bounds the
    same, so the split's width fraction never gets a say.
    """
    split = Adw.OverlaySplitView(
        collapsed=True,
        sidebar_position=Gtk.PackType.END,
        min_sidebar_width=SHEET_WIDTH,
        max_sidebar_width=SHEET_WIDTH,
        show_sidebar=False,
    )
    split.set_content(content)
    split.set_sidebar(sheet)
    return split


class _SectionRow(Gtk.ListBoxRow):
    """An "Unread" / "Earlier" heading, with a count in a grey pill where
    there is one to show. Not a row anyone can pick: it is not activatable,
    not selectable, and skipped by the keyboard."""

    def __init__(self, title: str, count: int | None = None) -> None:
        super().__init__(activatable=False, selectable=False, focusable=False)
        self.add_css_class("notification-section")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label = Gtk.Label(label=title, xalign=0, hexpand=True)
        label.add_css_class("caption-heading")
        label.add_css_class("dim-label")
        box.append(label)
        if count:
            pill = Gtk.Label(label=str(count), valign=Gtk.Align.CENTER)
            pill.add_css_class("count-badge")
            pill.add_css_class("caption")
            box.append(pill)
        self.set_child(box)


class _NotificationRow(Gtk.ListBoxRow):
    """One notification, laid out as the canvas draws it: the project's icon,
    then the kind's mark beside the session title with the row's age at the
    far right, and the body underneath on at most two lines.

    An unread row wears the sidebar's green guide line (the same keyframes
    as the row it summarises, see the .unread CSS in app.py) and a faint
    accent fill; a read one dims its title and loses the line. The row keeps
    its Notification so the sheet's click and context menu can name it
    without a lookup.
    """

    def __init__(
        self,
        notification: Notification,
        texture: Gdk.Texture | None,
        fallback_icon_name: str,
        now: float,
    ) -> None:
        super().__init__()
        self.notification = notification
        self.add_css_class("notification-row")
        if not notification.read:
            self.add_css_class("unread")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        icon = Gtk.Image(valign=Gtk.Align.START, pixel_size=16)
        icon.set_margin_top(2)
        if texture is not None:
            icon.set_from_paintable(texture)  # the project's own artwork, its own colours
        else:
            icon.set_from_icon_name(fallback_icon_name)
            icon.add_css_class("dim-label")
        box.append(icon)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        mark = self._kind_mark(notification.kind)
        if mark is not None:
            head.append(mark)
        title = Gtk.Label(
            label=notification.title or _("Untitled session"),
            xalign=0,
            hexpand=True,
            ellipsize=Pango.EllipsizeMode.END,
        )
        title.add_css_class("notification-title")
        if notification.read:
            title.add_css_class("dim-label")
        head.append(title)
        when = Gtk.Label(label=notifycenter.relative_time(notification.when, now))
        when.add_css_class("caption")
        when.add_css_class("dim-label")
        head.append(when)
        column.append(head)

        body = Gtk.Label(
            label=notifycenter.row_body(notification),
            xalign=0,
            wrap=True,
            lines=2,
            ellipsize=Pango.EllipsizeMode.END,
        )
        body.add_css_class("caption")
        body.add_css_class("dim-label")
        column.append(body)
        box.append(column)
        self.set_child(box)

    @staticmethod
    def _kind_mark(kind: str) -> Gtk.Image | None:
        """The glyph that says what kind of thing this was: the bell for a
        bell, the green dot for a finished run, nothing for a message (the
        body is the message; it needs no announcing)."""
        if kind == notifycenter.KIND_BELL:
            mark = Gtk.Image(icon_name=BELL_ICON, pixel_size=12, valign=Gtk.Align.CENTER)
            mark.add_css_class("notification-kind-bell")
            return mark
        if kind == notifycenter.KIND_FINISHED:
            mark = Gtk.Image(icon_name=FINISHED_MARK_ICON, pixel_size=8, valign=Gtk.Align.CENTER)
            mark.add_css_class("notification-kind-finished")
            return mark
        return None


class NotificationSheet(Gtk.Box):
    """The history: a toolbar, the rows in two sections, and a footer.

    The sheet reads the center and nothing else, and every change it makes
    goes back through the center, which announces it — so the rows are
    rebuilt from the list on every `changed()`, coalesced on an idle (the
    center calls listeners synchronously, and the placeholder → real-row
    handoff makes two calls in a row). Two hundred rows at most; rebuilding
    them is cheaper than diffing them and never shows a stale row.

    What a row does is the window's to say, through the callables here:
    `on_open(notification)` goes to the session (the window knows which
    tab a placeholder's key names, and which window a session is in),
    `on_preferences()` opens the dialog on the Notifications group,
    `project_icon(notification)` is the texture the sidebar would draw for
    the row's project (None for the generic icon), and `sound_name()` is
    what the footer says after "Sound:". `on_close()` is the × — the window
    turns the bell off, and the binding does the rest.
    """

    def __init__(
        self,
        center: NotificationCenter,
        *,
        on_open: Callable[[Notification], None],
        on_preferences: Callable[[], None],
        on_close: Callable[[], None],
        project_icon: Callable[[Notification], Gdk.Texture | None],
        sound_name: Callable[[], str],
        fallback_icon_name: str,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("notification-sheet")
        self.add_css_class("background")
        self._center = center
        self._on_open = on_open
        self._on_preferences = on_preferences
        self._project_icon = project_icon
        self._sound_name = sound_name
        self._fallback_icon_name = fallback_icon_name
        self._refresh_source: int | None = None
        # One popover for every row's context menu, parented to the list
        # rather than to a row: rows are rebuilt on every change, and a
        # popover whose parent goes away under it is a GTK warning at best.
        self._menu: Gtk.PopoverMenu | None = None
        # Textures are rasterized per project, not per row: a sheet full of
        # one project's rows should not decode its icon two hundred times.
        self._icon_cache: dict[str, Gdk.Texture | None] = {}

        # -- toolbar ----------------------------------------------------------
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        toolbar.add_css_class("toolbar")
        title = Gtk.Label(label=_("Notifications"), xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        title.add_css_class("heading")
        title.set_margin_start(6)
        toolbar.append(title)
        self.mark_all_button = Gtk.Button(tooltip_text=_("Mark every notification read"))
        self.mark_all_button.set_child(
            Adw.ButtonContent(icon_name="object-select-symbolic", label=_("Mark all read"))
        )
        self.mark_all_button.add_css_class("flat")
        self.mark_all_button.connect("clicked", lambda *_: self._center.mark_all_read())
        toolbar.append(self.mark_all_button)
        self.clear_button = Gtk.Button(tooltip_text=_("Remove every notification"))
        self.clear_button.set_child(
            Adw.ButtonContent(icon_name="user-trash-symbolic", label=_("Clear"))
        )
        self.clear_button.add_css_class("flat")
        self.clear_button.connect("clicked", lambda *_: self._center.clear())
        toolbar.append(self.clear_button)
        self.close_button = Gtk.Button(icon_name="window-close-symbolic", tooltip_text=_("Close"))
        self.close_button.add_css_class("flat")
        self.close_button.add_css_class("circular")
        self.close_button.connect("clicked", lambda *_: on_close())
        toolbar.append(self.close_button)
        self.append(toolbar)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # -- the rows, or the empty state -------------------------------------
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-activated", self._on_row_activated)
        secondary = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        secondary.connect("pressed", self._on_secondary_click)
        self.list.add_controller(secondary)
        scroller = Gtk.ScrolledWindow(
            child=self.list, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER
        )
        self.empty = Adw.StatusPage(
            icon_name=BELL_ICON,
            title=_("No notifications"),
            description=_("Messages from sessions you aren't looking at, and bells, land here."),
            vexpand=True,
        )
        self.empty.add_css_class("compact")
        self._stack = Gtk.Stack(vexpand=True)
        self._stack.add_named(self.empty, "empty")
        self._stack.add_named(scroller, "list")
        self.append(self._stack)

        # -- footer -----------------------------------------------------------
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.add_css_class("notification-footer")
        self._sound_label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self._sound_label.add_css_class("caption")
        self._sound_label.add_css_class("dim-label")
        footer.append(self._sound_label)
        prefs = Gtk.Button(label=_("Preferences…"))
        prefs.add_css_class("flat")
        prefs.add_css_class("link")
        prefs.add_css_class("caption")
        prefs.connect("clicked", lambda *_: self._on_preferences())
        footer.append(prefs)
        self.append(footer)

        actions = Gio.SimpleActionGroup()
        mark_read = Gio.SimpleAction(name="mark-read", parameter_type=GLib.VariantType("s"))
        mark_read.connect("activate", lambda _a, p: self._center.mark_read(p.get_string()))
        actions.add_action(mark_read)
        remove = Gio.SimpleAction(name="remove", parameter_type=GLib.VariantType("s"))
        remove.connect("activate", lambda _a, p: self._center.remove(p.get_string()))
        actions.add_action(remove)
        self.insert_action_group(ACTION_PREFIX, actions)

        self.refresh()

    # -- keeping up with the center --------------------------------------------

    def schedule_refresh(self) -> None:
        """Rebuild on the next idle, once, however many changes land first."""
        if self._refresh_source is None:
            self._refresh_source = GLib.idle_add(self._refresh_idle, priority=GLib.PRIORITY_DEFAULT)

    def _refresh_idle(self) -> bool:
        self._refresh_source = None
        self.refresh()
        return GLib.SOURCE_REMOVE

    def refresh(self) -> None:
        """Rebuild every row from the center's list, now."""
        if self._refresh_source is not None:
            GLib.source_remove(self._refresh_source)
            self._refresh_source = None
        self._icon_cache.clear()
        while (row := self.list.get_row_at_index(0)) is not None:
            self.list.remove(row)
        unread, earlier = notifycenter.split_rows(self._center.rows())
        now = GLib.get_real_time() / 1_000_000
        if unread:
            self.list.append(_SectionRow(_("Unread"), len(unread)))
            for notification in unread:
                self.list.append(self._row(notification, now))
        if earlier:
            self.list.append(_SectionRow(_("Earlier")))
            for notification in earlier:
                self.list.append(self._row(notification, now))
        any_rows = bool(unread or earlier)
        self._stack.set_visible_child_name("list" if any_rows else "empty")
        self.mark_all_button.set_sensitive(bool(unread))
        self.clear_button.set_sensitive(any(r.kind != notifycenter.KIND_FINISHED for r in unread + earlier))
        self._sound_label.set_label(_("Sound: {name}").format(name=self._sound_name()))

    def _row(self, notification: Notification, now: float) -> _NotificationRow:
        key = notification.session_id or notification.project
        if key not in self._icon_cache:
            self._icon_cache[key] = self._project_icon(notification)
        return _NotificationRow(notification, self._icon_cache[key], self._fallback_icon_name, now)

    def rows(self) -> list[_NotificationRow]:
        """The notification rows in list order, headings left out — what the
        headless check reads."""
        found: list[_NotificationRow] = []
        index = 0
        while (row := self.list.get_row_at_index(index)) is not None:
            if isinstance(row, _NotificationRow):
                found.append(row)
            index += 1
        return found

    def take_focus(self) -> bool:
        """Put the keyboard in the sheet — the first row, or the × when there
        is none — so Escape reaches the split that closes it and the arrow
        keys walk the list. Called by the window once the sheet is shown."""
        rows = self.rows()
        target: Gtk.Widget = rows[0] if rows else self.close_button
        target.grab_focus()
        return GLib.SOURCE_REMOVE

    # -- what a row does ---------------------------------------------------------

    def _on_row_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if not isinstance(row, _NotificationRow):
            return
        notification = row.notification
        self._on_open(notification)
        self._center.mark_read(notification.id)

    def _on_secondary_click(self, gesture: Gtk.GestureClick, _n: int, x: float, y: float) -> None:
        row = self.list.get_row_at_y(int(y))
        if not isinstance(row, _NotificationRow):
            return
        menu = self._context_menu(row.notification)
        if menu is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        if self._menu is None:
            self._menu = Gtk.PopoverMenu.new_from_model(menu)
            self._menu.set_parent(self.list)
            self._menu.set_has_arrow(False)
            self._menu.set_halign(Gtk.Align.START)
        else:
            self._menu.set_menu_model(menu)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        self._menu.set_pointing_to(rect)
        self._menu.popup()

    @staticmethod
    def _context_menu(notification: Notification) -> Gio.Menu | None:
        """Mark read for an unread row, Remove for any row the center will
        drop (a synthetic row leaves with its green, not by hand). None when
        neither applies — a read synthetic row has no menu."""
        menu = Gio.Menu()
        target = GLib.Variant("s", notification.id)
        if not notification.read:
            item = Gio.MenuItem.new(_("Mark read"), None)
            item.set_action_and_target_value(f"{ACTION_PREFIX}.mark-read", target)
            menu.append_item(item)
        if notification.kind != notifycenter.KIND_FINISHED:
            item = Gio.MenuItem.new(_("Remove"), None)
            item.set_action_and_target_value(f"{ACTION_PREFIX}.remove", target)
            menu.append_item(item)
        return menu if menu.get_n_items() else None

    def do_unroot(self) -> None:
        """A popover parented by hand must be unparented by hand, or GTK
        complains at dispose time."""
        if self._menu is not None:
            self._menu.unparent()
            self._menu = None
        Gtk.Widget.do_unroot(self)

