# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-08. Full change history: git log for this file.

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

import logging
import shutil
import threading
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from . import footerapps, openwith, prmenu
from .chats import is_chat_cwd
from .flash import FLASH_MS, flash
from .formatting import format_size
from .i18n import _
from .models import CHATS_GROUP, FAV_GROUP, SessionItem
from .projecticons import project_icon_data
from .providers import get_provider
from .prstatus import (
    PullRequest,
    describe_all,
    from_records,
    known,
    resync,
    sweep,
    to_records,
)
from .scrolling import offset_into_view
from .sessions import Session, project_name_for_cwd, resume_cwd, worktree_project_root
from .store import SessionStore
from .svgtexture import svg_texture
from .usagepanel import UsagePanel

log = logging.getLogger(__name__)

_GHOSTTY = shutil.which("ghostty")
_ELLIPSIZE_END = 3  # Pango.EllipsizeMode.END
_ELLIPSIZE_START = 1  # Pango.EllipsizeMode.START

# Row treatment per session status. Both tab statuses share one class (see
# row.session-child.running in app.py) — read or unread, the session has a tab
# open, and its title reads at full strength while every other row's dims.
# "background", i.e. running detached, colors the guide line instead and dims
# with the rest, as there is no tab to return to.
_STATUS_CSS = {
    "open": "running",
    "attention": "running",
    "background": "detached",
}
# The statuses that mean "this session has a tab open right now".
_IN_TAB_STATUSES = ("open", "attention")
# Set while the agent is producing output (see activity.py), on top of the
# status class: it is `.running.busy` that turns the guide line into a moving
# barber pole, so a stale flag on a row that is no longer running paints
# nothing. Only tab sessions ever get it — a detached (/bg) row has no
# activity source and keeps its still yellow line.
_BUSY_CSS = "busy"
# A finished run nobody has looked at yet: sets the guide line pulsing slowly
# in green until the user returns to the session's tab (see
# SessionItem.unread).
# A tab-only signal: window._sync_status drops the flag from any row whose tab
# is gone, and the detached yellow outranks it in CSS besides, so a
# backgrounded session never pulses. The busy pole outranks it too, so a
# session that starts a new turn unread moves again and goes back to pulsing
# when that turn also runs out.
_UNREAD_CSS = "unread"
# The user stopped Claude mid-task and nothing has happened since (see
# sessions.py's transcript scan): paints the guide line red, the same channel
# the detached and unread colors use. The busy pole outranks it too, so a
# resumed session moves instead of sitting on a stale interruption.
_INTERRUPTED_CSS = "interrupted"

# When SessionRow.begin_archiving hands a row its second act — collapsing the
# slot the slide-out emptied — relative to the slide's start. A shade past the
# 250ms transform/opacity transition (see row.session-child.archiving in
# app.py), so the slide finishes before the ground moves under it.
_ARCHIVE_COLLAPSE_MS = 260
# ...and when the whole ghost-out has played: the collapse above plus its
# 200ms min-height/border transition. What the window waits before landing a
# tabless archive (see _archive_session there) — with no tab to shut down
# there is no natural delay, and an archive landing sooner rebuilds the list
# right over the animation's first frames.
ARCHIVE_GHOST_MS = _ARCHIVE_COLLAPSE_MS + 200

# How long an arriving row takes to slide down out from behind the row above
# it — a new thread's placeholder, or a session Undo just un-archived (see
# _slide_row_in). The archive's own slide, read backwards: a row leaves in
# 250ms, so a row arrives in 250ms too.
_ARRIVE_MS = 250

# How far a project header's icon sits from the row's own left edge: the
# theme's sidebar-row padding (8px in Adwaita) plus .group-header's own 10px.
_HEADER_ICON_OFFSET = 18

# App icons in a project's "open in…" menu rows: symbolic-icon sized, so a row
# is no taller than the plain menu items above and below it.
_OPEN_WITH_ICON_PX = 16

# The waiting state badge: matches the row's other symbolic icons (project
# icon, action buttons), which all render at 16px.
_STATE_BADGE_ICON_PX = 16


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
        self._texture = svg_texture(self._icon_svg, icon_size)
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
        texture = svg_texture(self._icon_svg, size)
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


def _arrive_by_slide(row: Gtk.ListBoxRow, body: Gtk.Widget) -> None:
    """Give `row` an arrival: instead of appearing, it grows out from under
    the row above it, `body` sliding down as if it had been behind that row
    all along. What a new thread's placeholder plays as it is added, and a
    session row restored by Undo plays on its way back from the archive.

    The body goes into a revealer with nothing revealed, which measures zero
    and clips what is sliding through it — the whole trick: neither a widget
    margin (negative ones do nothing in GTK4) nor a CSS transform would take
    the content out of the row's height, so the slot would stand open at full
    height while the text slid into it. The row gives up its own min-height
    for the duration too (.arriving, in app.py), so the slot grows with the
    revealer instead of standing open at full height from the first frame,
    and the rows below slide down ahead of the arrival rather than after it.
    With the desktop's animations off the revealer snaps and child-revealed
    lands at once, so nothing here needs a reduced-motion branch of its own.
    """
    revealer = Gtk.Revealer(
        transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
        transition_duration=_ARRIVE_MS,
        reveal_child=False,
    )
    revealer.set_child(body)
    row.set_child(revealer)
    row.add_css_class("arriving")

    def on_arrived(rev: Gtk.Revealer, _pspec) -> None:
        # The slide done, hand the row's height back to its own min-height —
        # a no-op in pixels, the body inside carrying the same height (see
        # row.session-child > revealer > box in app.py), so the row doesn't
        # jump as the class comes off.
        if rev.get_child_revealed():
            row.remove_css_class("arriving")

    revealer.connect("notify::child-revealed", on_arrived)

    def start() -> bool:
        revealer.set_reveal_child(True)
        return GLib.SOURCE_REMOVE

    # An idle later, so the row is mapped by the time the reveal starts: a
    # revealer told to open before it is on screen has nowhere to animate
    # and simply appears open.
    GLib.idle_add(start)


class PlaceholderRow(Gtk.ListBoxRow):
    """Transient stand-in for a just-opened tab whose session id is still
    unknown (no transcript on disk yet). Swapped for a real SessionRow once
    the store discovers the session.

    `arriving` plays the row in rather than having it appear: see
    _arrive_by_slide.
    """

    def __init__(
        self,
        placeholder_id: str,
        group_key: tuple,
        sidebar: SessionSidebar,
        arriving: bool = False,
    ) -> None:
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
        # Full strength, like the real rows with a tab open: dimming now means
        # "no tab", and a placeholder is standing in for one that is starting.
        label.add_css_class("session-title")
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

        if arriving:
            _arrive_by_slide(self, box)
        else:
            self.set_child(box)


class NewThreadRow(Gtk.ListBoxRow):
    """The row an empty project would have if it had a session: clicking it
    starts one, exactly as the header's + button does.

    A project with nothing under it is all header and no list, and the one
    thing to do there is start a thread — so the space where its first session
    will go says so. It stands for no session, so it reads like every row
    without a tab open, and gets that for free from the two rules keyed on the
    status classes it never carries (both in app.py): dimmed, through
    row.session-child:not(.running) .session-title, and without the card
    outline a running or detached row is drawn in, through
    row.session-child:not(.running):not(.detached) — the group is empty, and
    an outlined row there would say otherwise.
    """

    def __init__(self, group_key: tuple, cwd: str | None) -> None:
        super().__init__()
        self.group_key = group_key
        self.cwd = cwd
        self.set_selectable(False)
        self.add_css_class("session-child")  # a real row's metrics and indent

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        box.set_valign(Gtk.Align.CENTER)  # match SessionRow in a taller row
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(4)  # match SessionRow's title inset

        label = Gtk.Label(label=_("New Thread"), xalign=0.0, hexpand=True)
        label.add_css_class("session-title")
        label.set_ellipsize(_ELLIPSIZE_END)
        box.append(label)

        self.set_child(box)
        self.set_tooltip_text(
            _("New chat")
            if group_key == CHATS_GROUP
            else _("New session in {path}").format(path=_abbreviate_path(cwd))
        )

    def start_session(self) -> None:
        """Do what the + button on this row's project header does."""
        if self.group_key == CHATS_GROUP:
            self.activate_action("win.new-session-in-chats", None)
        elif self.cwd:
            self.activate_action("win.new-session-in", GLib.Variant("s", self.cwd))


class SessionRow(Gtk.ListBoxRow):
    """`arriving` plays the row in rather than having it appear — what a
    session restored by Undo gets, coming back from the archive it just left:
    see _arrive_by_slide."""

    def __init__(
        self, item: SessionItem, sidebar: SessionSidebar, arriving: bool = False
    ) -> None:
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

        # Leading the title: everything this session's pull requests amount to,
        # as one mark (see prmenu.combined_icon). It is the row's only color,
        # and it is a button — clicking it opens the same list the tab footer's
        # ellipsis shows, from a row whose tab needn't be open (or exist) to
        # read it, and consumes the click that would otherwise open the session.
        # A session with no PRs has no mark, and the title starts where the
        # check would leave it.
        self._pr_menu = prmenu.new_popover(Gtk.PositionType.BOTTOM)
        pr_btn = Gtk.MenuButton(valign=Gtk.Align.CENTER, popover=self._pr_menu)
        pr_btn.add_css_class("flat")
        # .pr-mark trades a button's even padding for the placement a mark in a
        # line of text wants: tucked in against the guide line, held off the
        # title (see app.py, which styles the button node inside this one).
        pr_btn.add_css_class("pr-mark")
        pr_btn.set_create_popup_func(self._fill_pr_menu)
        self._pr_btn = pr_btn
        top.append(pr_btn)

        name_label = Gtk.Label(xalign=0.0, hexpand=True)
        # The title is what carries "this session has a tab open": app.py dims
        # it on every row that doesn't (see row.session-child:not(.running)).
        name_label.add_css_class("session-title")
        name_label.set_ellipsize(_ELLIPSIZE_END)
        top.append(name_label)
        self._name_label = name_label

        # Only the waiting state badges the row (interrupted colors the guide
        # line instead — see _on_state_changed), so the badge is fixed here
        # and only its visibility ever changes.
        self._state_badge = Gtk.Image(valign=Gtk.Align.CENTER)
        self._state_badge.set_from_icon_name("waiting-question-symbolic")
        self._state_badge.add_css_class("waiting-badge")
        self._state_badge.set_tooltip_text(_("Claude is waiting for your reply"))
        self._state_badge.set_pixel_size(_STATE_BADGE_ICON_PX)
        self._state_badge.set_margin_start(2)
        top.append(self._state_badge)

        time_label = Gtk.Label(valign=Gtk.Align.CENTER)
        time_label.set_margin_start(8)  # keep the timestamp off a long title
        time_label.add_css_class("dim-label")
        time_label.add_css_class("caption")

        # Stop / background act on the tab this session is open in — the same
        # two operations as the header buttons, for a row that isn't
        # necessarily the focused tab. Hidden unless a tab is actually open on
        # the session (see _on_status_changed).
        # A stop square, not an X: exiting a session isn't deleting anything,
        # and the close-style X read like it was.
        stop_btn = Gtk.Button(
            icon_name="media-playback-stop-symbolic", valign=Gtk.Align.CENTER
        )
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
        # Timeout for the ghost-out's second phase (see begin_archiving).
        self._archive_collapse: int | None = None

        self._prs: list[PullRequest] = []
        self._pr_fetch = 0  # generation: a slow fetch can't land on a later opening
        # What a PR's actions need of the session behind them. From a row, the
        # session is only reachable through the window — which is also the only
        # thing holding the tab whose prompt they would go to.
        self._pr_host = prmenu.ActionHost(
            # Asked of the window and of nothing else: it looks the session's
            # tab up and asks that tab. The row's own status property says the
            # same thing a beat later (it is set from the same lookup), and
            # testing it here as well only ever subtracted — a row whose status
            # hadn't caught up dropped every prompt action from its menu while
            # the tab sat there ready for one.
            prompt_block=lambda: sidebar.prompt_block(self.item.session_id),
            has_changes=lambda: sidebar.has_changes(self.item.session_id),
            send_prompt=lambda prompt: self.activate_action(
                "win.send-prompt", GLib.Variant("(ss)", (self.item.session_id, prompt))
            ),
            refresh=self._refresh_prs,
        )
        self.sync_prs()

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
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

        hover = Gtk.EventControllerMotion()
        hover.connect("enter", self._on_hover_enter)
        hover.connect("leave", self._on_hover_leave)
        self.add_controller(hover)

        # Ellipsised text is recoverable on hover: the row answers tooltip
        # queries with the full title (and folder path, when shown), but only
        # for labels that are actually truncated — repeating fully visible
        # text would be noise. On the row, not the labels, so the whole row
        # width is a hover target; the action buttons' own tooltips still win
        # while the pointer is on them.
        self.set_has_tooltip(True)
        self.connect("query-tooltip", self._on_query_tooltip)
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

        if arriving:
            _arrive_by_slide(self, box)
        else:
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

        # Status highlight, busy pole and state badge need CSS-class updates:
        # plain signals, detached on unroot.
        self._status_handler = item.connect("notify::status", self._on_status_changed)
        self._state_handler = item.connect("notify::state", self._on_state_changed)
        self._busy_handler = item.connect("notify::busy", self._on_busy_changed)
        self._unread_handler = item.connect("notify::unread", self._on_unread_changed)
        self._on_status_changed(item, None)
        self._on_state_changed(item, None)
        self._on_busy_changed(item, None)
        self._on_unread_changed(item, None)

        right_click = Gtk.GestureClick(button=3)
        right_click.connect("pressed", self._on_right_click)
        self.add_controller(right_click)

    def sync_prs(self) -> None:
        """Re-read the session's saved PRs; the mark is only there with one.

        Called as the row is built and again whenever the session's tab reports
        a new list, so a session that opens its first PR grows the mark without
        waiting for the sidebar to be rebuilt around it.

        The saved list carries the status it was saved with (see
        prstatus.to_record), and `known` puts anything this run has since
        fetched over the top of it — a dictionary lookup, no file and no `gh`,
        safe on the main loop. So a mark reads as the last thing anything knew
        rather than as "nothing known", and it tracks an open tab's own poll:
        the tab saves its list whenever its chips change, this re-reads it, and
        the status the tab just fetched is sitting there waiting.
        """
        self._prs = [
            known(pr)
            for pr in from_records(
                self._sidebar.store.state.get_session_prs(self.item.session_id)
            )
        ]
        self._sync_pr_mark()

    def apply_prs(self, prs: list[PullRequest]) -> None:
        """Land a freshly fetched list on this row (see SessionSidebar's sweep).

        A menu fetch already in flight is dropped: this list is newer than
        anything it will come back with, and its results would only overwrite
        them with what the sweep already replaced.
        """
        self._pr_fetch += 1
        self._prs = list(prs)
        self._sync_pr_mark()
        if self._pr_menu.get_visible():
            prmenu.update(self._pr_menu, prs, self._pr_host)

    def _sync_pr_mark(self) -> None:
        """Rebuild the leading mark from the row's current list.

        Rebuilt rather than patched, like the footer's chips: which icons a
        mark is made of depends on the state it shows, and rows are cheap.
        """
        self._pr_btn.set_visible(bool(self._prs))
        if not self._prs:
            return
        self._pr_btn.set_child(prmenu.combined_icon(self._prs))
        self._pr_btn.set_tooltip_text(describe_all(self._prs))

    def _fill_pr_menu(self, _button: Gtk.MenuButton) -> None:
        """Put the saved list up at once, and refresh it behind a spinner.

        The footer's copy of this menu is filled from a poll that is already
        keeping it current. This one has no poll behind it: what it starts from
        is the saved list plus whatever this run has fetched, which may be
        yesterday's answer. So opening the menu is what fetches — the rows go
        up immediately, since the titles and numbers are the readable part,
        with a spinner in the column each status will land in.

        A session with a single PR skips the list entirely and opens that PR's
        actions, the way a footer chip does: a list of one asks which of one,
        and the answer is a click that only ever leads to the same place. There
        is no way back from it because there is nothing behind it, and the
        refresh lands in the actions themselves (see prmenu.update).
        """
        if len(self._prs) == 1:
            prmenu.show_actions(self._pr_menu, self._prs[0], self._pr_host)
        else:
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
        self._sync_pr_mark()  # the status the menu just fetched is the row's too
        self._sidebar.store.state.set_session_prs(self.item.session_id, to_records(prs))
        self._sidebar.store.apply_pr_title(self.item.session_id)
        if self._pr_menu.get_visible():
            prmenu.update(self._pr_menu, prs, self._pr_host)
        return GLib.SOURCE_REMOVE

    def _on_query_tooltip(
        self, _row: Gtk.Widget, _x: int, _y: int, _keyboard: bool, tooltip: Gtk.Tooltip
    ) -> bool:
        lines = [
            label.get_label()
            for label in (self._name_label, self._path_label)
            if label.get_visible() and label.get_layout().is_ellipsized()
        ]
        if not lines:
            return False
        tooltip.set_text("\n".join(lines))
        return True

    def _on_hover_enter(self, *_args) -> None:
        self._action_stack.set_visible_child_name("hover")

    def _on_hover_leave(self, *_args) -> None:
        # Opening the PR menu grabs the pointer, which arrives here as a leave;
        # that costs the row its hover buttons and nothing more. The menu hangs
        # off the mark ahead of the title, which is always shown — a popover
        # goes down with the widget it is attached to, and the buttons in this
        # stack are no longer that widget.
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
        if self._busy_handler is not None:
            self.item.disconnect(self._busy_handler)
            self._busy_handler = None
        if self._unread_handler is not None:
            self.item.disconnect(self._unread_handler)
            self._unread_handler = None
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

    def begin_archiving(self, settled: bool = False) -> None:
        """Ghost the row out ahead of its archive actually landing.

        Archiving a session whose tab is open first shuts that tab down, which
        can take seconds — so the row acknowledges the click now: it slides out
        of the panel (the .archiving class, a CSS transition), and once the
        slide has finished the emptied slot closes up (.archiving-gone plus
        hiding the content box, so the row's height is all animatable
        min-height and border). The zero-height row then waits in the list for
        the rebuild that really removes it — or for restore_archiving, if
        closing the tab is cancelled.

        `settled` is for a rebuild that lands mid-wait (see _rebuild_rows): the
        replacement widget takes both classes before it is ever mapped, so it
        starts collapsed instead of replaying the animation.
        """
        if self._archive_collapse is not None or self.has_css_class("archiving"):
            return
        self.add_css_class("archiving")
        # An invisible row shouldn't answer clicks or offer tooltips.
        self.set_sensitive(False)
        if settled:
            self._collapse_archiving()
        else:
            self._archive_collapse = GLib.timeout_add(
                _ARCHIVE_COLLAPSE_MS, self._collapse_archiving
            )

    def _collapse_archiving(self) -> bool:
        self._archive_collapse = None
        self.add_css_class("archiving-gone")
        self.get_child().set_visible(False)
        return GLib.SOURCE_REMOVE

    def restore_archiving(self) -> None:
        """Bring back a row whose archive was cancelled: an instant snap on
        purpose — the classes leave with their transitions, so nothing slides
        back in slow motion under a user who just said "keep it"."""
        if self._archive_collapse is not None:
            GLib.source_remove(self._archive_collapse)
            self._archive_collapse = None
        self.remove_css_class("archiving")
        self.remove_css_class("archiving-gone")
        self.get_child().set_visible(True)
        self.set_sensitive(True)

    def _on_status_changed(self, item: SessionItem, _pspec) -> None:
        # The row itself carries the status: a session running in a tab keeps
        # its title at full strength (the class the dimming rule tests for),
        # and one running detached colors its left guide line instead.
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

    def _on_busy_changed(self, item: SessionItem, _pspec) -> None:
        # Only the class goes on and off here: which pole a busy row gets —
        # blue in a tab, yellow detached — is the status class's business, so
        # a session that backgrounds itself mid-turn keeps moving and simply
        # changes color.
        if item.busy:
            self.add_css_class(_BUSY_CSS)
        else:
            self.remove_css_class(_BUSY_CSS)

    def _on_unread_changed(self, item: SessionItem, _pspec) -> None:
        if item.unread:
            self.add_css_class(_UNREAD_CSS)
        else:
            self.remove_css_class(_UNREAD_CSS)

    def _on_state_changed(self, item: SessionItem, _pspec) -> None:
        # Waiting is a question to answer, so it stays an icon at the row's
        # right edge; interrupted is a status, so it speaks through the guide
        # line like the other statuses that color it (detached, unread).
        self._state_badge.set_visible(item.state == "waiting")
        if item.state == "interrupted":
            self.add_css_class(_INTERRUPTED_CSS)
        else:
            self.remove_css_class(_INTERRUPTED_CSS)

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
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.append(self._view)
        # Claude subscription usage readout, tucked under the session list.
        self.usage_panel = UsagePanel(store.state)
        self.usage_panel.set_visible(bool(store.state.get_setting("show_usage_panel")))
        inner.append(self.usage_panel)
        # Where the window's "session archived" snackbar floats: over the
        # panel's own bottom edge — usage panel included — rather than the
        # window's, so the toast reads as the list acknowledging what it just
        # did instead of as an app-wide banner.
        self.toast_overlay = Adw.ToastOverlay(child=inner, vexpand=True)
        self.append(self.toast_overlay)
        self._collapsed: set[tuple] = set()
        self._selection_mode = False
        self._selected: set[str] = set()
        # Sessions mid-archive whose rows are ghosted out (see begin_archiving):
        # deliberately in-memory only — a refresh restoring a ghost early is an
        # accepted cosmetic miss, not state worth persisting.
        self._archiving: set[str] = set()
        self._rows: dict[str, SessionRow] = {}
        self._header_rows: dict[tuple, GroupHeaderRow] = {}
        # The stand-in row under each group that has nothing else in it.
        self._new_thread_rows: dict[tuple, NewThreadRow] = {}
        self._placeholders: dict[str, str] = {}  # placeholder id -> cwd
        self._placeholder_rows: dict[str, PlaceholderRow] = {}
        self._row_order: list[str] = []  # session/placeholder ids, top to bottom
        # Placeholders whose tab is producing output right now. Kept here
        # rather than on the row, which any rebuild throws away — a new thread
        # printing its first turn is exactly when rows come and go.
        self._busy_placeholders: set[str] = set()
        # Placeholders whose tab finished a run nobody has looked at, on the
        # same terms (see SessionItem.unread for the flag's meaning).
        self._unread_placeholders: set[str] = set()
        # Placeholders whose row hasn't played its arrival yet: set as the
        # placeholder is added and spent by the rebuild that first builds its
        # row (see add_placeholder), so the slide happens once. A rebuild
        # landing mid-slide replaces the row with one that is simply there —
        # the same trade the archive ghost makes in reverse, and the window a
        # rebuild would have to hit is a quarter of a second wide.
        self._arriving_placeholders: set[str] = set()
        # Sessions whose row should play the same arrival when it next gets
        # built — the ones Undo is bringing back from the archive: set just
        # before the un-archive lands (see begin_arrival) and spent by the
        # rebuild that follows it, on the placeholder set's terms above.
        self._arriving_sessions: set[str] = set()
        self._active_session_id: str | None = None
        # "Why can't this session be sent a prompt right now?", for a row's PR
        # actions (see SessionRow._pr_host) — the empty string when it can.
        # Only the window can answer — the tab is its — so it replaces this the
        # moment it builds the sidebar; until then, nothing can be sent
        # anywhere.
        self.prompt_block: Callable[[str], str] = lambda _session_id: _(
            "This session has no tab open."
        )
        # "Does this session's terminal have uncommitted work in front of it?",
        # for the same rows' "Open pull request". Replaced by the window on the
        # same terms, and no for as long as it hasn't been.
        self.has_changes: Callable[[str], bool] = lambda _session_id: False
        # "Where is this session's terminal right now?", for a row's
        # "Open In…" — the live answer only an open tab can give. Replaced by
        # the window on the same terms; None means no tab, and the transcript
        # answers instead (see _session_cwd).
        self.live_cwd: Callable[[str], str | None] = lambda _session_id: None
        # "This session's PR list has changed under you" — for a session whose
        # tab is open, whose own copy of that list would otherwise overwrite
        # what a sweep just found on the next poll. Replaced by the window on
        # the same terms as the callables above; with no tab there is nothing
        # to tell, and the saved list stands on its own.
        self.prs_updated: Callable[[str, list], None] = lambda _session_id, _records: None
        # Whether a PR sweep is running (see refresh_pull_requests): one click
        # of the refresh button at a time, however long gh takes.
        self._pr_sweep = False
        # Scrolling the list is deferred to an idle callback (see
        # _schedule_scroll): the offset to restore and the row to reveal when
        # it runs, plus the id of the pending source.
        self._pending_offset: float | None = None
        self._pending_row: str | None = None
        self._scroll_source: int | None = None
        self._activated_row_id: str | None = None
        # The row mid-bell-flash and when its flash expires (monotonic µs).
        # Kept here rather than on the row, which any rebuild throws away —
        # and a bell often rides in with the very state change that triggers
        # one (see _rebuild_rows, which re-flashes the replacement row).
        self._flashing_row: tuple[str, int] | None = None
        self.show_folder_path = bool(store.state.get_setting("show_folder_path"))

        # The group menu's "New sessions use a worktree" checkbox. Gio renders
        # a checkbox only for a stateful boolean action, and those can't carry
        # a per-project target (string-param + state renders as radios) — but
        # only one group menu is ever open, so show_group_menu re-arms this
        # one action with the project it is about before each popup.
        self._worktree_menu_project = ""
        self._project_worktree_action = Gio.SimpleAction.new_stateful(
            "project-worktree", None, GLib.Variant.new_boolean(False)
        )
        self._project_worktree_action.connect("change-state", self._on_project_worktree)
        actions = Gio.SimpleActionGroup()
        actions.add_action(self._project_worktree_action)
        self.insert_action_group("sidebar", actions)

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
        self._refresh_btn.set_tooltip_text(_("Refresh session list and pull requests"))
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
        # Only this list's scrollbar sits flush against the panel border (see
        # .sidebar-scroll in app.py); every other scroller keeps stock spacing.
        scrolled.add_css_class("sidebar-scroll")
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
        for child in (
            *self._rows.values(),
            *self._placeholder_rows.values(),
            *self._new_thread_rows.values(),
        ):
            child.set_margin_start(indent)

    def _project_icon_size(self) -> int:
        return int(self.store.state.get_setting("project_icon_size") or 16)

    def _rebuild_rows(self) -> None:
        self._remember_scroll()
        self.list.remove_all()
        self._rows = {}
        self._header_rows = {}
        self._new_thread_rows = {}
        self._placeholder_rows = {}
        self._row_order = []

        # Directory per project group (from its most recent session with a
        # cwd), so headers can offer a "new session here" button. Favorites
        # mixes projects, so it never gets one. A worktree session answers
        # with its repository, not the worktree (matches store.project_cwd).
        items_by_group: dict[tuple, list[SessionItem]] = {}
        group_cwds: dict[tuple, str] = {}
        for i in range(self.store.model.get_n_items()):
            item = self.store.model.get_item(i)
            key = item.group_key
            items_by_group.setdefault(key, []).append(item)
            if key != FAV_GROUP and key not in group_cwds and item.session.cwd:
                group_cwds[key] = (
                    worktree_project_root(item.session.cwd) or item.session.cwd
                )

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
                ph_cwd = self._placeholders[pids[0]]
                headers.append((key, key[1], worktree_project_root(ph_cwd) or ph_cwd))

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
            # A group with nothing under it gets the row that starts its first
            # session — but only where there is a session to start: Favorites
            # is a view onto other projects, and a virtual project whose folder
            # is unknown has nowhere to start one (both are exactly the groups
            # the header's + button skips).
            if (
                not items_by_group.get(key)
                and not placeholders_by_group.get(key)
                and (key == CHATS_GROUP or cwd)
            ):
                new_thread = NewThreadRow(key, cwd)
                new_thread.set_margin_start(child_indent)
                self._new_thread_rows[key] = new_thread
                self.list.append(new_thread)
            for pid in placeholders_by_group.get(key, ()):
                arriving = pid in self._arriving_placeholders
                self._arriving_placeholders.discard(pid)
                prow = PlaceholderRow(pid, key, self, arriving=arriving)
                prow.set_margin_start(child_indent)
                if pid == self._active_session_id:
                    prow.add_css_class("active-tab")
                if pid in self._busy_placeholders:
                    prow.add_css_class(_BUSY_CSS)
                if pid in self._unread_placeholders:
                    prow.add_css_class(_UNREAD_CSS)
                self._placeholder_rows[pid] = prow
                self._row_order.append(pid)
                self.list.append(prow)
            for item in items_by_group.get(key, []):
                arriving = item.session_id in self._arriving_sessions
                self._arriving_sessions.discard(item.session_id)
                row = SessionRow(item, self, arriving=arriving)
                row.set_margin_start(child_indent)
                if item.session_id == self._active_session_id:
                    row.add_css_class("active-tab")
                self._rows[item.session_id] = row
                self._row_order.append(item.session_id)
                self.list.append(row)
        self._apply_selection_to_rows()
        # Rows mid-archive stay ghosted across a rebuild — rebuilds happen for
        # unrelated reasons in the seconds an archive takes, and each one
        # replaces the ghost with a fresh, fully visible widget. Ids whose
        # archive has since landed drop out here: with "show archived" off the
        # row is gone from the list, and with it on the row should return as an
        # ordinary archived row rather than stay a ghost.
        self._archiving = {
            sid
            for sid in self._archiving
            if sid in self._rows and not self.store.state.is_archived(sid)
        }
        for sid in self._archiving:
            self._rows[sid].begin_archiving(settled=True)
        self._update_collapse_button()
        # A rebuild inside the flash window replaced the flashing row with a
        # fresh widget that never got the CSS class — and bells often ride in
        # with the very state change (new session, archive, reorder) that
        # forces a rebuild. Give the replacement the flash still owed to it.
        if self._flashing_row is not None:
            row_id, deadline = self._flashing_row
            self._flashing_row = None
            if GLib.get_monotonic_time() < deadline:
                self.flash_row(row_id)
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
        """A session's PRs changed: re-read them on that session's row.

        The window calls this whenever a tab saves a new list — so a session
        that has just opened its first PR gains the mark that opens it; the
        rows themselves are only rebuilt when the list's order changes, which
        opening a PR isn't — and whenever an open tab's chips change how they
        read, so the row's mark follows the status that tab just fetched
        instead of waiting for the next sweep.
        """
        row = self._rows.get(session_id)
        if row is not None:
            row.sync_prs()

    # -- pull request sweep ----------------------------------------------------

    def refresh_pull_requests(self) -> None:
        """Re-read every listed session's pull requests, off the main thread.

        The other half of the header's refresh button (see window's "refresh"
        action): rescanning the sessions says which rows exist, this says what
        each of their marks should be showing. Every session the sidebar isn't
        keeping out of sight is asked about — archived ones, and sessions in an
        archived project, are not: a panel-wide sweep is `gh` calls, and rows
        nobody is looking at shouldn't cost any.

        A session with no PRs is still worth including: what the sweep does
        with its directory is look for one (see prstatus.sweep), which is the
        only way a PR opened by hand ever reaches a row whose transcript will
        never mention it.

        Only the live half of each session's directory is read here — asking
        its tab, which only the main loop may do. The fallback for a session
        with no tab is a transcript tail per session, and a panel's worth of
        those is not something to read between two frames, so it waits for the
        thread (see _sweep_prs).
        """
        if self._pr_sweep:
            return
        targets = [
            (
                session_id,
                from_records(self.store.state.get_session_prs(session_id)),
                session,
                self.live_cwd(session_id),
            )
            for session_id, session in self.store.sessions.items()
            if not self.store.is_out_of_sight(session)
        ]
        if not targets:
            return
        self._pr_sweep = True
        self._set_refresh_busy(True)
        threading.Thread(
            target=self._sweep_prs, args=(targets,), name="pr-sweep", daemon=True
        ).start()

    def _sweep_prs(
        self, targets: list[tuple[str, list[PullRequest], Session, str | None]]
    ) -> None:
        """Run the sweep. Off the main loop — it is a `gh` call per directory,
        after a transcript tail for every session whose tab isn't open."""
        try:
            swept = sweep(
                (session_id, prs, live or resume_cwd(session))
                for session_id, prs, session, live in targets
            )
        except Exception:
            log.debug("sidebar: PR sweep failed", exc_info=True)
            swept = {}
        GLib.idle_add(self._prs_swept, swept)

    def _prs_swept(self, swept: dict[str, list[PullRequest]]) -> bool:
        """Land a sweep: onto every row it covers, its open tab, and disk.

        A session whose list is unchanged is not written back — status is no
        part of a record, so there would be nothing new to write — but both
        places that show the session hear about it either way, because the
        status that came back is the point of the exercise: the row is handed
        the swept list for its mark, and the tab is told so its chips leave
        whatever they were showing before the button was clicked.
        """
        self._pr_sweep = False
        self._set_refresh_busy(False)
        for session_id, prs in swept.items():
            records = to_records(prs)
            if records != self.store.state.get_session_prs(session_id):
                self.store.state.set_session_prs(session_id, records)
                self.store.apply_pr_title(session_id)
            # An open tab re-derives this list from its own sources every poll,
            # so a PR the branch lookup just found would otherwise be written
            # now and overwritten a second later; handing it over also puts the
            # tab's own chips on the status this sweep fetched.
            self.prs_updated(session_id, records)
            row = self._rows.get(session_id)
            if row is not None:
                row.apply_prs(prs)
        return GLib.SOURCE_REMOVE

    def _set_refresh_busy(self, busy: bool) -> None:
        """Turn the header's refresh button into a spinner while it works.

        Rescanning sessions is over in a blink, so the button never used to say
        anything; a sweep is `gh` calls over a whole panel and takes seconds,
        which needs saying — and needs the second click that would run it all
        again to be impossible while it does.
        """
        if busy:
            self._refresh_btn.set_child(Gtk.Spinner(spinning=True))
            self._refresh_btn.set_tooltip_text(_("Refreshing pull requests…"))
        else:
            self._refresh_btn.set_icon_name("view-refresh-symbolic")
            self._refresh_btn.set_tooltip_text(_("Refresh session list and pull requests"))
        self._refresh_btn.set_sensitive(not busy)

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

    def begin_archiving(self, session_id: str) -> None:
        """Slide a session's row out ahead of its archive landing.

        Called by the window as it starts an archive (see _archive_session
        there), which can take seconds when a tab has to shut down first: the
        row answers now, and the id is remembered so a rebuild in the waiting
        window re-ghosts the replacement row instead of popping it back.
        """
        row = self._rows.get(session_id)
        if row is None:
            return
        self._archiving.add(session_id)
        row.begin_archiving()

    def clear_archiving(self, session_id: str) -> None:
        """The archive was cancelled (its tab-close dialog declined): whatever
        stage of ghosting the row reached, bring it back."""
        self._archiving.discard(session_id)
        row = self._rows.get(session_id)
        if row is not None:
            row.restore_archiving()

    def begin_arrival(self, session_id: str) -> None:
        """Have a session's row slide in from above when it is next built.

        Called by the window just before Undo lands an un-archive (see
        _undo_archive there): the restore reorders the store, and the rebuild
        that follows plays the returning row in instead of popping it into
        place."""
        self._arriving_sessions.add(session_id)

    def flash_row(self, row_id: str) -> None:
        """Visual bell relay: flash the row standing for a ringing session (or
        placeholder). Nothing to do when the search filter or an archive has
        taken the row out of the list."""
        row = self._row_for(row_id)
        if row is not None:
            flash(row)
            self._flashing_row = (row_id, GLib.get_monotonic_time() + FLASH_MS * 1000)

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
        """Show a transient "New Thread" row for a tab with no session yet.

        The row slides down out from behind its project header rather than
        appearing under it (see PlaceholderRow.slide_in) — the sidebar answers
        the click, and the new thread is where the eye already is.

        Except where the group's "New Thread" offer row (see NewThreadRow) is
        what it replaces: that row is already in the slot saying the same
        words, so there is nothing to announce, and the offer row leaving as
        the placeholder grew from nothing would jump every row below it up and
        back down again.
        """
        self._placeholders[placeholder_id] = cwd
        if self._placeholder_group_key(cwd) not in self._new_thread_rows:
            self._arriving_placeholders.add(placeholder_id)
        self._rebuild_rows()
        self._invalidate()

    def set_placeholder_busy(self, placeholder_id: str, busy: bool) -> None:
        """Pole a "New Thread" row while its tab is working.

        A placeholder stands for a session the store hasn't discovered yet, so
        it has no SessionItem to carry the flag the way every other row does —
        and printing its first turn is precisely when a new thread is working.
        """
        if busy:
            self._busy_placeholders.add(placeholder_id)
        else:
            self._busy_placeholders.discard(placeholder_id)
        row = self._placeholder_rows.get(placeholder_id)
        if row is None:
            return
        if busy:
            row.add_css_class(_BUSY_CSS)
        else:
            row.remove_css_class(_BUSY_CSS)

    def set_placeholder_unread(self, placeholder_id: str, unread: bool) -> None:
        """Flag (or clear) a "New Thread" row's finished-and-unseen state, the
        way set_placeholder_busy carries the busy flag: the row has no
        SessionItem yet, so the sidebar holds it across rebuilds."""
        if unread:
            self._unread_placeholders.add(placeholder_id)
        else:
            self._unread_placeholders.discard(placeholder_id)
        row = self._placeholder_rows.get(placeholder_id)
        if row is None:
            return
        if unread:
            row.add_css_class(_UNREAD_CSS)
        else:
            row.remove_css_class(_UNREAD_CSS)

    def has_placeholder(self, placeholder_id: str) -> bool:
        return placeholder_id in self._placeholders

    def placeholder_unread(self, placeholder_id: str) -> bool:
        return placeholder_id in self._unread_placeholders

    def remove_placeholder(self, placeholder_id: str) -> None:
        self._busy_placeholders.discard(placeholder_id)
        self._unread_placeholders.discard(placeholder_id)
        self._arriving_placeholders.discard(placeholder_id)
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
        if isinstance(row, NewThreadRow):
            # No session behind it to match a query against, and it folds away
            # with its group like the rows it stands in for.
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
        if isinstance(row, NewThreadRow):
            if not self._selection_mode:  # there is no session here to select
                row.start_session()
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
                label = _("Continue in native chat ({mode}) (experimental)").format(mode=_(cv.label))
            else:
                label = _("Continue in native chat (experimental)")
            chat_item = Gio.MenuItem.new(label, None)
            chat_item.set_action_and_target_value(
                "win.resume-chat", GLib.Variant("s", f"{cv.key}:{session_id}")
            )
            open_section.append_item(chat_item)

        # The session's own working directory, not the project's: a session
        # running in a worktree or subdirectory hands that folder over. Chat
        # sessions live in throwaway directories nobody wants opened — but the
        # gate is on the *resolved* directory, so a chat that moved into a real
        # worktree (which resume_cwd deliberately honors) still gets the menu.
        rows: list[Gtk.Widget] = []
        cwd = self._session_cwd(row.item.session)
        if cwd and not is_chat_cwd(cwd):
            open_section.append_submenu(_("Open In…"), self._open_with_menu(cwd, rows))

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
        # Recovery for a row whose background agent it lost track of (e.g. a
        # /bg handoff the app never got to pair): find the agent and link it.
        edit_section.append_item(item(_("Repair session link"), "repair-session"))

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
        self._popup_menu(menu, row, x, y, rows)

    def show_group_menu(self, row: GroupHeaderRow, x: float, y: float) -> None:
        if row.group_key == CHATS_GROUP:
            # The Chats group is permanent: nothing to archive or remove.
            menu = Gio.Menu()
            menu.append(_("New chat"), "win.new-session-in-chats")
            self._popup_menu(menu, row, x, y)
            return

        project_name = row.group_key[1]

        open_section = Gio.Menu()
        is_git = bool(row.cwd) and (Path(row.cwd) / ".git").exists()
        if row.cwd:
            new_item = Gio.MenuItem.new(_("New session here"), None)
            new_item.set_action_and_target_value(
                "win.new-session-in", GLib.Variant("s", row.cwd)
            )
            open_section.append_item(new_item)

        # One-off launch the other way around from the project's effective
        # worktree choice — for trying the other method without touching the
        # default or the project's pin.
        if is_git:
            alt_label = (
                _("New session here (no worktree)")
                if self.store.state.worktree_for_project(project_name)
                else _("New session here (in a worktree)")
            )
            alt_item = Gio.MenuItem.new(alt_label, None)
            alt_item.set_action_and_target_value(
                "win.new-session-in-inverted", GLib.Variant("s", row.cwd)
            )
            open_section.append_item(alt_item)

        # Worktree launches only mean something in a git checkout (`.git` is a
        # file in worktree checkouts, so either form counts) — elsewhere the
        # checkbox is omitted rather than left to silently do nothing.
        if is_git and not self.store.state.is_virtual_project(project_name):
            self._worktree_menu_project = project_name
            self._project_worktree_action.set_state(
                GLib.Variant.new_boolean(
                    self.store.state.worktree_for_project(project_name)
                )
            )
            open_section.append(_("New sessions use a worktree"), "sidebar.project-worktree")

        # Anything cosmetic about the project's row itself.
        looks_section = Gio.Menu()
        if row.cwd:
            icon_item = Gio.MenuItem.new(_("Generate Icon"), None)
            icon_item.set_action_and_target_value(
                "win.generate-icon", GLib.Variant("s", row.cwd)
            )
            looks_section.append_item(icon_item)

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

        rows: list[Gtk.Widget] = []
        if row.cwd:
            open_section.append_submenu(_("Open In…"), self._open_with_menu(row.cwd, rows))

        menu = Gio.Menu()
        menu.append_section(None, open_section)
        menu.append_section(None, looks_section)
        menu.append_section(None, danger_section)
        self._popup_menu(menu, row, x, y, rows)

    def _on_project_worktree(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        """The group menu's checkbox: pin the choice for the project the menu
        was armed with (see __init__)."""
        action.set_state(value)
        if self._worktree_menu_project:
            self.store.state.set_project_worktree(
                self._worktree_menu_project, value.get_boolean()
            )

    def _session_cwd(self, session: Session) -> str | None:
        """Where the session is working right now, for its row's "Open In…".

        The session's recorded cwd is only where it *started* — an agent that
        moved into a worktree or subdirectory left it behind long ago. An open
        tab knows its live directory (see live_cwd); for everything else the
        transcript's tail holds the last cwd the agent recorded, which is also
        what resuming and "Open in Ghostty" use.
        """
        return self.live_cwd(session.session_id) or resume_cwd(session)

    def _open_with_menu(self, cwd: str, rows: list[Gtk.Widget]) -> Gio.Menu:
        """The "Open In…" submenu: ways to hand a folder — a project's, or a
        single session's working directory — to another app: the user's own
        picks (the footer apps, in their configured order), then the file
        manager and terminal the desktop hands out. Each entry is just the
        app's name; the submenu label already says what picking one does.

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
                info.get_display_name(),
                "win.open-folder-app",
                GLib.Variant("(ss)", (app_id, cwd)),
            )

        # Role labels rather than app names for these two: what you get is
        # whatever the desktop nominates, and the icon already says which.
        # Skipped when the user has added that very app themselves.
        manager = openwith.default_file_manager()
        if manager is None or manager.get_id() not in configured:
            icon = manager.get_icon() if manager else Gio.ThemedIcon.new("folder-symbolic")
            add(icon, _("File Manager"), "win.open-folder", GLib.Variant("s", cwd))

        terminal = openwith.default_terminal()
        if terminal is not None and terminal.get_id() not in configured:
            icon = terminal.get_icon() or Gio.ThemedIcon.new("utilities-terminal-symbolic")
            add(icon, _("Terminal"), "win.open-folder-terminal", GLib.Variant("s", cwd))
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
