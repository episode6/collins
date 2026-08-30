# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The in-app notification card: what a session says to a user who is in
Collins but looking at another session.

A card slides in at the top-right of the window's content, under the header
bar, when the notification center's delivery table says "card" (see
notifycenter.delivery): a message from `notify_user`, a bell, or — with the
*Announce finished runs* setting — a finished run, from a tab that isn't
the selected one. The desktop's own notification would land on top of the
very app that raised it and say nothing about which of six sessions is
asking; this says, and clicking anywhere on it goes there.

The anatomy is the design canvas's: a 32px tile wearing the project's icon,
the session title in bold beside the kind's mark (the yellow bell for a
bell, the green dot for a finished run, the update arrow for a newer
Collins, nothing for a message — the body is the message), the row's age
dim at the right, two lines of body, the project's name as a dim footer
(none for an update, which has no project), and a small circular × at the
top right.
The × dismisses the card and nothing else: the history row stays unread and
keeps counting, because the user chose not to go and the badge means
"waiting for you", exactly as the sidebar's flag stays until the tab is
visited. The card is not the keyboard's business (the sheet is the
keyboardable surface) and Escape never touches it: a maximized page owns
that key (see maximized-page-owns-keyboard).

The cards live in `NotificationCards`, a box added to the window's one
full-window overlay (MainWindow.lightbox_overlay) and sized to its cards,
so the rest of the overlay stays the terminal's to click. Each card is a
Gtk.Revealer, added unrevealed and revealed on the next main-loop turn so
GTK animates the slide (and stops animating it, for free, when the
desktop's animations are off). Six seconds later it un-reveals and is
dropped once the transition ends; a pointer anywhere inside the box holds
every card's clock. At most three cards stand at once — a fourth pushes
the oldest out at once — and the history has the rest.

The whole card is clickable, but it is a box with a click gesture rather
than a Gtk.Button: the × inside is a real button, and GtkButton's own
gesture runs in the capture phase and claims the sequence, so a button
wrapping a button would take the ×'s clicks for its own. The box's gesture
bubbles, and the × — being the deeper widget and a capture-phase claimant
— wins every press that lands on it.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from . import notifycenter  # noqa: E402
from .i18n import _  # noqa: E402
from .notifycenter import Notification  # noqa: E402
from .notifypanel import BELL_ICON, FINISHED_MARK_ICON, UPDATE_MARK_ICON  # noqa: E402

# How many cards stand at once; a fourth pushes the oldest out.
MAX_CARDS = 3
# How long a card stays, from the moment it is revealed, pointer permitting.
AUTO_HIDE_MS = 6000
# A card whose clock was paused comes back for at least this long, so a
# pointer that only passed through can't drop a card the instant it leaves.
RESUME_MIN_MS = 1000
# The slide, as the canvas draws it.
SLIDE_MS = 260
# The card's width, and where the stack sits: the canvas's 14px from the
# window's right edge, and 12px under the header bar (measured, not
# assumed: the overlay wraps the toolbar view, so the header is inside it).
CARD_WIDTH = 372
GAP_UNDER_HEADER = 12
MARGIN_END = 14


class NotificationCard(Gtk.Revealer):
    """One card. Holds its Notification and, for a tab that had no session
    id when it spoke, the Adw.TabPage it came from — so a click can still
    find the tab once the placeholder id it was posted under has resolved
    into a session id nothing in the notification names."""

    def __init__(
        self,
        notification: Notification,
        texture: Gdk.Texture | None,
        fallback_icon_name: str,
        page: Adw.TabPage | None,
        *,
        on_open: Callable[[NotificationCard], None],
        on_dismiss: Callable[[NotificationCard], None],
        on_gone: Callable[[NotificationCard], None],
    ) -> None:
        super().__init__(
            transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
            transition_duration=SLIDE_MS,
            reveal_child=False,
            halign=Gtk.Align.END,
        )
        self.notification = notification
        self.page = page
        self._on_open = on_open
        self._on_dismiss = on_dismiss
        self._on_gone = on_gone
        self._timeout: int | None = None
        self._deadline: float | None = None  # monotonic seconds
        self._remaining_ms = AUTO_HIDE_MS
        self._paused = False
        self._shown = False
        self._leaving = False
        self._scheme_class = ""

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        body.add_css_class("card")
        body.add_css_class("activatable")  # Adwaita's hover wash for a clickable card
        body.add_css_class("notification-card")
        # One width for every card, whatever its body says: the size request
        # is the floor, and the clamp around it is the ceiling — a wrapping
        # label's natural width is its whole text on one line, and without
        # the clamp a long message would make its card wider than its
        # neighbours.
        body.set_size_request(CARD_WIDTH, -1)
        self._body = body
        clamp = Adw.Clamp(maximum_size=CARD_WIDTH, tightening_threshold=CARD_WIDTH, child=body)

        tile = Gtk.Box(valign=Gtk.Align.START, halign=Gtk.Align.START)
        tile.add_css_class("notification-card-tile")
        # The tile's size is the CSS's (32px min); the icon sits centred in
        # it by alignment alone. It must not expand: a child's expand flag
        # climbs to every ancestor that hasn't set its own, so an expanding
        # icon would make the tile a second expander beside the text column,
        # and a Box splits its slack equally between expanders — a short
        # message's card would then start its text half a card to the right.
        icon = Gtk.Image(pixel_size=20, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        if texture is not None:
            icon.set_from_paintable(texture)  # the project's own artwork, its own colours
        else:
            icon.set_from_icon_name(fallback_icon_name)
        tile.append(icon)
        body.append(tile)
        self._tile = tile

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
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
        title.add_css_class("notification-card-title")
        head.append(title)
        # The age at the moment the card goes up; a card lives seconds, so
        # it is never refreshed (the sheet's rows are, on their own clock).
        when = Gtk.Label(label=notifycenter.relative_time(notification.when))
        when.add_css_class("caption")
        when.add_css_class("dim-label")
        head.append(when)
        column.append(head)
        text = Gtk.Label(
            label=notifycenter.row_body(notification),
            xalign=0,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            lines=2,
            ellipsize=Pango.EllipsizeMode.END,
        )
        text.add_css_class("notification-card-body")
        column.append(text)
        if notification.project:
            footer = Gtk.Label(label=notification.project, xalign=0, ellipsize=Pango.EllipsizeMode.END)
            footer.add_css_class("caption")
            footer.add_css_class("dim-label")
            column.append(footer)
        body.append(column)
        self._column = column

        self.close_button = Gtk.Button(
            icon_name="window-close-symbolic",
            valign=Gtk.Align.START,
            tooltip_text=_("Dismiss"),
        )
        self.close_button.add_css_class("flat")
        self.close_button.add_css_class("circular")
        self.close_button.add_css_class("notification-card-close")
        self.close_button.connect("clicked", lambda *_: self._on_dismiss(self))
        body.append(self.close_button)

        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.connect("released", self._on_released)
        body.add_controller(click)

        self.set_child(clamp)
        self.connect("notify::child-revealed", self._on_child_revealed)

    def set_scheme_class(self, css_class: str) -> None:
        """Pin the card light or dark (*css_class* is one of
        notifycenter.CARD_SCHEME_CLASSES' values), or "" to follow the app
        again. The class goes on the body — the widget Adwaita's .card
        paints — where the app's CSS re-pins the card colors under it."""
        if css_class == self._scheme_class:
            return
        if self._scheme_class:
            self._body.remove_css_class(self._scheme_class)
        if css_class:
            self._body.add_css_class(css_class)
        self._scheme_class = css_class

    @staticmethod
    def _kind_mark(kind: str) -> Gtk.Image | None:
        """The same three marks the sheet's rows wear (see notifypanel), in
        the same two colours."""
        if kind == notifycenter.KIND_BELL:
            mark = Gtk.Image(icon_name=BELL_ICON, pixel_size=12, valign=Gtk.Align.CENTER)
            mark.add_css_class("notification-kind-bell")
            return mark
        if kind == notifycenter.KIND_FINISHED:
            mark = Gtk.Image(icon_name=FINISHED_MARK_ICON, pixel_size=8, valign=Gtk.Align.CENTER)
            mark.add_css_class("notification-kind-finished")
            return mark
        if kind == notifycenter.KIND_UPDATE:
            mark = Gtk.Image(icon_name=UPDATE_MARK_ICON, pixel_size=12, valign=Gtk.Align.CENTER)
            mark.add_css_class("notification-kind-update")
            return mark
        return None

    # -- clicks -----------------------------------------------------------------

    def _on_released(self, gesture: Gtk.GestureClick, _n: int, x: float, y: float) -> None:
        """A press that ends inside the card is a click on it. The × never
        gets here: its own gesture claimed the sequence in the capture
        phase, and a claimed sequence is denied to the gestures below."""
        if not self._body.contains(x, y):
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._on_open(self)

    def activate(self) -> None:
        """What a click does, for the headless check."""
        self._on_open(self)

    # -- the clock --------------------------------------------------------------

    def reveal(self) -> None:
        """Slide in, and start the clock."""
        self._shown = True
        self.set_reveal_child(True)
        self.arm(AUTO_HIDE_MS)

    def arm(self, ms: int) -> None:
        self._cancel()
        self._remaining_ms = ms
        self._deadline = time.monotonic() + ms / 1000
        self._timeout = GLib.timeout_add(ms, self._on_timeout)

    def pause(self) -> None:
        """The pointer is over the stack: hold the clock where it is."""
        if self._timeout is None:
            return
        remaining = int((self._deadline - time.monotonic()) * 1000) if self._deadline else 0
        self._remaining_ms = max(remaining, 0)
        self._cancel()
        self._paused = True

    def resume(self) -> None:
        """The pointer left: run the rest of the clock, a second at least."""
        if not self._paused:
            return
        self._paused = False
        self.arm(max(self._remaining_ms, RESUME_MIN_MS))

    def _cancel(self) -> None:
        if self._timeout is not None:
            GLib.source_remove(self._timeout)
            self._timeout = None
        self._deadline = None

    def _on_timeout(self) -> bool:
        self._timeout = None
        self.slide_out()
        return GLib.SOURCE_REMOVE

    def slide_out(self) -> None:
        """Slide out; the widget is dropped when the slide ends (the
        child-revealed notify). A card that never got its reveal has no
        transition to end, so it goes at once — and one caught mid-slide-in
        has no child-revealed edge to come (the property is still False),
        so a timeout the length of the slide drops it instead. The drop is
        idempotent, so the notify and the timeout can both fire. Named for
        what it does rather than `hide`, which is Gtk.Widget's and means
        something else."""
        self._cancel()
        self._paused = False
        self._leaving = True
        if not self._shown or not self.get_reveal_child():
            self._on_gone(self)
            return
        self.set_reveal_child(False)
        GLib.timeout_add(SLIDE_MS + 50, self._on_slide_out_ended)

    @property
    def leaving(self) -> bool:
        """Whether the card is on its way out (slide_out was called) — still
        a child until its slide ends, but standing for nothing."""
        return self._leaving

    def _on_slide_out_ended(self) -> bool:
        if not self.get_reveal_child():
            self._on_gone(self)
        return GLib.SOURCE_REMOVE

    def _on_child_revealed(self, *_args) -> None:
        if not self.get_child_revealed() and not self.get_reveal_child():
            self._on_gone(self)


class NotificationCards(Gtk.Box):
    """The stack of cards in a window's overlay, newest on top.

    `on_open(card)` is the window's: it goes to the card's session (or
    page) and marks the row read. The card itself knows nothing about
    windows or the center. `project_icon(notification)` is the texture the
    sheet's rows use, rasterized at the tile's size here.
    """

    def __init__(
        self,
        header: Gtk.Widget,
        *,
        on_open: Callable[[NotificationCard], None],
        project_icon: Callable[[Notification, int], Gdk.Texture | None],
        fallback_icon_name: str,
    ) -> None:
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            halign=Gtk.Align.END,
            valign=Gtk.Align.START,
            margin_end=MARGIN_END,
        )
        self.add_css_class("notification-cards")
        self._header = header
        self._on_open = on_open
        self._project_icon = project_icon
        self._fallback_icon_name = fallback_icon_name
        # The notification_color_scheme setting, as the class a card wears
        # (see apply_settings); "" until the window pushes its settings in.
        self._scheme_class = ""
        # The pointer over any card pauses every card's clock. The box is
        # sized to its cards, so "over the box" is "over a card" (or the
        # 8px between two), and GTK counts a descendant as inside.
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self.pause_all())
        motion.connect("leave", lambda *_: self.resume_all())
        self.add_controller(motion)
        self._motion = motion

    def attach(self, overlay: Gtk.Overlay) -> None:
        """Float over *overlay*'s child. Added before the lightbox ever is,
        so a lightbox — added later, and so above — still covers the
        cards."""
        overlay.add_overlay(self)

    def apply_settings(self, settings: dict) -> None:
        """The card's own light/dark (notification_color_scheme), pushed
        in with the rest of the settings at build and on every Preferences
        change: every card standing changes at once, and every card after
        goes up wearing it."""
        self._scheme_class = notifycenter.card_scheme_class(settings.get("notification_color_scheme"))
        for card in self.cards():
            card.set_scheme_class(self._scheme_class)

    # -- showing ----------------------------------------------------------------

    def show(self, notification: Notification, *, page: Adw.TabPage | None = None) -> NotificationCard:
        """Put a card up for *notification*, newest on top, and reveal it on
        the next main-loop turn so the slide animates. *page* is the tab
        the notification came from, for one posted under a placeholder id
        (see NotificationCard)."""
        self._place_under_header()
        # A card already up for this row — a bell coalesced onto its unread
        # row, rung again — is replaced rather than joined: the new one wears
        # the new count, and two cards for one row would be one thing twice.
        standing = self.card_for(notification.id)
        if standing is not None:
            standing.slide_out()
        card = NotificationCard(
            notification,
            self._project_icon(notification, 20),
            self._fallback_icon_name,
            page,
            on_open=self._on_open,
            on_dismiss=lambda c: c.slide_out(),
            on_gone=self._remove,
        )
        card.set_scheme_class(self._scheme_class)
        self.prepend(card)
        # Only the cards actually standing count towards the three: one
        # sliding out is still a child until its slide ends, and pushing a
        # standing card out for its sake would leave two on screen. (Not
        # "revealed": a burst of cards in one main-loop turn are all still
        # waiting on their reveal, and every one of them stands.)
        standing_cards = [c for c in self.cards() if not c.leaving]
        for stale in standing_cards[MAX_CARDS:]:
            stale.slide_out()

        def reveal() -> bool:
            if card.get_parent() is self:
                card.reveal()
                if self._motion.contains_pointer():
                    card.pause()
            return GLib.SOURCE_REMOVE

        GLib.idle_add(reveal, priority=GLib.PRIORITY_DEFAULT)
        return card

    def _place_under_header(self) -> None:
        """Sit GAP_UNDER_HEADER below the header bar, wherever the bar's
        bottom edge is right now. Measured on every show — the bar's height
        follows the font and the theme, and a card is up for seconds, so
        the moment it appears is the moment the measurement is worth
        having — from the allocation when the bar has one, else from the
        bar's own natural height (a window that has never been drawn)."""
        height = self._header.get_height()
        if height <= 0:
            height = self._header.measure(Gtk.Orientation.VERTICAL, -1)[1]
        self.set_margin_top(max(height, 0) + GAP_UNDER_HEADER)

    def _remove(self, card: NotificationCard) -> None:
        if card.get_parent() is self:
            self.remove(card)

    # -- reading and clearing ------------------------------------------------------

    def cards(self) -> list[NotificationCard]:
        """The cards standing, top (newest) first."""
        found: list[NotificationCard] = []
        child = self.get_first_child()
        while child is not None:
            if isinstance(child, NotificationCard):
                found.append(child)
            child = child.get_next_sibling()
        return found

    def card_for(self, notification_id: str) -> NotificationCard | None:
        return next((c for c in self.cards() if c.notification.id == notification_id), None)

    def dismiss(self, notification_id: str) -> None:
        """Take down the card for a row that was read some other way (its
        session visited, the sheet's row clicked): a card for a message
        already read would be the banner nobody asked for."""
        card = self.card_for(notification_id)
        if card is not None:
            card.slide_out()

    def dismiss_session(self, session_id: str) -> None:
        """Take down every card for *session_id* — the user is at the tab
        now (see MainWindow._clear_unread), and the cards said to go there.
        A "" key matches nothing."""
        if not session_id:
            return
        for card in self.cards():
            if card.notification.session_id == session_id:
                card.slide_out()

    def dismiss_all(self) -> None:
        for card in self.cards():
            card.slide_out()

    def pause_all(self) -> None:
        for card in self.cards():
            card.pause()

    def resume_all(self) -> None:
        for card in self.cards():
            card.resume()
