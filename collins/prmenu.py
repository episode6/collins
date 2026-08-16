"""A session's pull requests as a popover list: one row per PR, titles and all.

Two buttons open this same list. The ellipsis beside the tab footer's PR chips
— shown only while the row is too narrow for every chip — opens it for the tab
in front of you, off a list its poll keeps current; the combined mark ahead of
a sidebar row's title opens it (on a right-click, and on a plain click for a
session with no tab) for a session whose tab may not even be open, off the list
saved for that session — which is why that one refreshes before it shows
anything (see SessionRow._fill_pr_menu).

The widgets live here rather than beside either caller so both get the same
menu: the same mark column, the same ellipsizing title, the same click that
opens the PR page and closes the menu behind it.

Left-clicking a PR in the list asks what to *do* with it; right-clicking one
opens its page in the browser. A mark standing for one PR reads the other way
round — the plain click opens that PR's page in Collins and the actions are its
context menu (see attach_view and attach_actions, and SessionRow's mark, which
does the same once its session has a tab to open the page in) — because a list
is a question about which PR and a mark is not.

That submenu is the same list of actions wherever it is opened from (practions
decides what a PR offers, under an "Open on GitHub" row every PR has), so it is
built here too: from a row it slides in over the list and leads back with a
header row, and from a footer chip — which has no list behind it — it opens on
the chip as a menu of its own.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from . import dialogs, practions  # noqa: E402
from .copylabel import open_tooltip, open_uri  # noqa: E402
from .i18n import _  # noqa: E402
from .prstatus import (  # noqa: E402
    BADGE_CONFLICT,
    BADGE_FAILED,
    BADGE_PASSED,
    BADGE_PENDING,
    BADGE_UNRESOLVED,
    PullRequest,
    combined_badge,
    combined_state,
    describe,
    invalidate,
    menu_name,
)

log = logging.getLogger(__name__)

# The marks sit with caption-sized text, so the base icon takes 12px rather
# than a symbolic icon's stock 16.
MERGED_ICON_PX = 12
# The status badge and how far it hangs off the base's bottom-left corner:
# small enough to read as a satellite of the base, offset enough that most of
# it sits over the corner rather than over the drawing.
BADGE_PX = 8
BADGE_OVERHANG_PX = 3
# The column the status marks share (a base icon plus its overhang), so the
# titles beside them line up; how wide a title gets before it ellipsizes; and
# how tall the list gets before it scrolls.
MARK_COLUMN_PX = MERGED_ICON_PX + BADGE_OVERHANG_PX
# The sidebar's combined mark (see combined_icon) stands in a line of title
# text and is the only colored thing on the row, so it takes the size the row's
# other symbolic icons do rather than the chips' caption size — badge and
# overhang scaled to match.
ROW_ICON_PX = 16
ROW_BADGE_PX = 10
MAX_CHARS = 48
MAX_HEIGHT_PX = 400
# Every PR mark is GitHub's own iconography, colored as on the PR page (the
# shades follow the light/dark scheme and live in app.py). The base icon says
# what the PR *is*: grey while it's a draft — or while nothing has been
# fetched, where grey reads as "nothing known" rather than a wrongly green
# all-clear — green once it's ready for review, purple when it lands, red when
# it is closed without landing.
_BASE_ICONS = {
    "MERGED": ("git-merge-symbolic", "pr-merged"),
    "OPEN": ("git-pull-request-symbolic", "pr-open"),
    "DRAFT": ("git-pull-request-draft-symbolic", "pr-draft"),
    "CLOSED": ("git-pull-request-closed-symbolic", "pr-closed"),
}
# Nothing fetched yet, or (for a row's combined mark) a set of PRs whose states
# disagree about how they ended. Grey, the same "nothing known" a draft wears.
_BASE_FALLBACK = ("git-pull-request-symbolic", "pr-draft")
# The badge says how the PR stands. Both merge blockers — a failed check and
# a conflicting branch — get the same red x; the warning triangle is the
# softer "someone is waiting on a reply", which only shows once nothing
# blocks the merge (see PullRequest.badge).
_BADGE_ICONS = {
    BADGE_FAILED: ("x-circle-fill-symbolic", "pr-checks-failed"),
    BADGE_CONFLICT: ("x-circle-fill-symbolic", "pr-conflict"),
    BADGE_PENDING: ("circle-fill-symbolic", "pr-checks-pending"),
    BADGE_UNRESOLVED: ("alert-fill-symbolic", "pr-unresolved"),
    BADGE_PASSED: ("check-circle-fill-symbolic", "pr-checks-passed"),
}
# What a popover is currently showing: the PR whose actions are up (None while
# it is the list), the way back out of those actions (None when the actions are
# the whole menu, as for a footer chip or a session with one PR), and the list
# itself — the contents a submenu leads back to, which a refresh can replace
# without disturbing the submenu on top of it.
_ACTIONS = "_collins_actions_pr"
_ACTIONS_BACK = "_collins_actions_back"
_LIST = "_collins_pr_list"
# Each action's mark, by key: the icon says who carries the action out —
# GitHub's own glyphs (colored as on the PR page: merge purple, open green)
# for the actions gh runs, Claude's mark for the ones that send the session a
# prompt or summon the review workflow. Lives here rather than on the Action
# because icons are the menu's business and practions stays Gtk-free.
_ACTION_ICONS = {
    practions.READY: ("git-pull-request-symbolic", "pr-open"),
    practions.MERGE: ("git-merge-symbolic", "pr-merged"),
    practions.AUTO_MERGE: ("git-merge-symbolic", "pr-merged"),
    # The two the PR page keeps behind its button (see practions.alternate_actions).
    # Each wears the half of itself the merge glyph doesn't already say: the
    # sidebar's own archive mark, and the closed-PR base icon in its red.
    practions.MERGE_ARCHIVE: ("archive-symbolic", None),
    practions.CLOSE: ("git-pull-request-closed-symbolic", "pr-closed"),
    practions.REBASE: ("agent-claude-symbolic", None),
    practions.REVIEW: ("agent-claude-symbolic", None),
    practions.FIX_CI: ("agent-claude-symbolic", None),
    practions.COMMENTS: ("agent-claude-symbolic", None),
    practions.NEW_PR: ("agent-claude-symbolic", None),
}


@dataclass(frozen=True)
class ActionHost:
    """The session behind a PR menu, as the few things its actions need.

    A PR's actions are mostly GitHub's business, but the ones that send a
    prompt are the session's: whether it is somewhere a prompt can be sent,
    whether its working tree has anything to open a pull request for, and how
    to send one. The footer's chips answer all of that from the tab they are
    in; a sidebar row answers it through the window, whose tab that session
    may not even have. Hence callables rather than values: a menu built once
    is opened much later, and what a terminal is waiting at changes by the
    keystroke.
    """

    # Why a prompt can't be sent to this session right now — no tab open, or an
    # input that isn't empty (Provider.takes_prompt) — and "" when one can. A
    # sentence rather than a flag because it is shown: the actions it blocks
    # stay in the menu, greyed out, carrying this as their tooltip.
    prompt_block: Callable[[], str]
    # Whether its terminal's cwd is a repo with uncommitted work in it. Costs a
    # `git status`, so practions only asks when a PR's state could use it.
    has_changes: Callable[[], bool]
    # Type an action's prompt into that session, send it, and put it in front.
    send_prompt: Callable[[str], None]
    # An action changed the PR on GitHub; re-read its status.
    refresh: Callable[[], None]
    # Open the PR's native page (prview) docked beside the session — the
    # "View in Collins" row. None where no such page can be shown, and on the
    # page's own actions menu, which mustn't offer to open itself again.
    view_pr: Callable[[PullRequest], None] | None = None
    # The unresolved badge's deep link: the same page, landed at its first
    # unresolved thread (prview.reveal_unresolved). Its row only shows while
    # the PR awaits a reply; None hides it wherever no page can be shown —
    # except on the page's own menu, where it stays as an in-page jump.
    view_unresolved: Callable[[PullRequest], None] | None = None
    # Whether a merge asks before it goes ahead (the confirm_merges setting,
    # see practions.confirmation). A callable like the rest: a menu built once
    # is opened much later, and Preferences may have been visited in between.
    # Defaulted so a host built without an opinion asks, which is what every
    # merge did before the setting existed.
    confirm_merges: Callable[[], bool] = lambda: True
    # Archive the session this PR belongs to — what "Merge and archive" does
    # once its merge has landed (practions.MERGE_ARCHIVE). None where there is
    # no session to put away: a host built for a record rather than for a live
    # tab, which is why that action is only ever offered on the PR page docked
    # inside the session it would archive.
    archive: Callable[[], None] | None = None


def new_popover(position: Gtk.PositionType) -> Gtk.Popover:
    """An empty PR list, ready for `fill`.

    No arrow, like the terminal's own context menu and like every
    GtkPopoverMenu: the tail is a separate render node from the body it points
    out of, and the edge they share only rasterizes cleanly when it lands on a
    whole device pixel. At a display scale of 1.25 it lands on a half one, both
    shapes anti-alias against it, and the two coverages composite to 75% — a
    row of background showing through the join, which reads as an arrow
    floating a pixel off its menu. That is GTK's own (a stock GtkPopover does
    it, under every GSK renderer) and no CSS of ours reaches it. A menu that
    opens on its button doesn't need a tail to say what it belongs to.
    """
    popover = Gtk.Popover()
    popover.set_position(position)
    popover.set_has_arrow(False)
    popover.add_css_class("menu")
    # Its own class as well: the list inside is buttons, and the rules its
    # opener's neighbourhood has for those (the footer's are tight and flat)
    # would otherwise reach into it — a popover is a child of the widget it is
    # attached to.
    popover.add_css_class("pr-menu")
    return popover


def _mark(
    state: str | None, badge_name: str | None, base_px: int, badge_px: int
) -> Gtk.Widget:
    """One two-icon mark: a state's base icon, with a status badge on its corner.

    The base icon carries the state (see `_BASE_ICONS`); the one status worth
    acting on rides its bottom-left corner as a smaller badge (see
    `PullRequest.badge` for which wins the slot — the tooltip still carries
    everything, via describe). The mark is always the same size, badge or no
    badge — base plus overhang in both directions — so a chip doesn't resize
    and its neighbours don't move when a badge comes or goes.

    Where the base sits inside that box is what the badge decides. A badged
    mark fills the box: base against the top-right corner, badge hanging into
    the bottom-left, and the two together read as centered on it. Alone, the
    base held in that same corner does not — it reads as a small icon pushed
    up and to the right of its slot, next to neighbours that look centered in
    theirs. So an unbadged base is centered in the box instead: the overhang
    is split around it rather than spent on one side. Bases no longer share an
    exact left edge down a column of mixed marks, which is the trade — an
    icon's own center is what the eye lines up, not the corner of the box it
    was drawn in, and the sidebar's agent mark (see .agent-mark in app.py) is
    centered on this same slot for the same reason.

    The overhang scales with the badge: it is how much of the badge hangs off
    the base, and at a bigger badge a fixed one would leave the mark sitting
    over the drawing instead of beside its corner.
    """
    overhang = max(1, round(badge_px * BADGE_OVERHANG_PX / BADGE_PX))
    name, css_class = _BASE_ICONS.get(state or "", _BASE_FALLBACK)
    base = Gtk.Image.new_from_icon_name(name)
    base.set_pixel_size(base_px)
    base.add_css_class(css_class)
    badge = _BADGE_ICONS.get(badge_name or "")
    if badge is not None:
        base.set_margin_start(overhang)
        base.set_margin_bottom(overhang)
    else:
        # Split, not halved: an odd overhang can't be, and the leftover pixel
        # goes to the side the badge would have come from, leaving the base a
        # half-pixel nearer the box's center than the corner it starts at.
        near = (overhang + 1) // 2
        base.set_margin_start(near)
        base.set_margin_end(overhang - near)
        base.set_margin_bottom(near)
        base.set_margin_top(overhang - near)
    mark = Gtk.Overlay(child=base)
    if badge is not None:
        name, css_class = badge
        image = Gtk.Image.new_from_icon_name(name)
        image.set_pixel_size(badge_px)
        image.add_css_class(css_class)
        image.set_halign(Gtk.Align.START)
        image.set_valign(Gtk.Align.END)
        mark.add_overlay(image)
    return mark


def status_icon(pr: PullRequest) -> Gtk.Widget:
    """One PR's state and status as a mark, at the size the chips use."""
    return _mark(pr.state, pr.badge, MERGED_ICON_PX, BADGE_PX)


def state_icon_name(state: str | None) -> str:
    """The bare icon name for a PR state — for slots that take a themed icon
    rather than a widget, like a panel page's tab. Color is the widget marks'
    affordance; a tab icon carries the shape alone."""
    return _BASE_ICONS.get(state or "", _BASE_FALLBACK)[0]


def check_image(state: str, px: int = ROW_ICON_PX) -> Gtk.Widget:
    """One CI check's verdict as a colored icon (see prdetail.PrCheck.state:
    a prstatus badge name). The PR view's check rows reuse the badge
    iconography at row-icon size, so a red x means the same thing everywhere."""
    name, css_class = _BADGE_ICONS.get(state, _BADGE_ICONS[BADGE_PENDING])
    image = Gtk.Image.new_from_icon_name(name)
    image.set_pixel_size(px)
    if css_class is not None:
        image.add_css_class(css_class)
    return image


def combined_icon(prs: Iterable[PullRequest]) -> Gtk.Widget:
    """A whole session's pull requests as a single mark, at row-icon size.

    The sidebar has one slot per row, not one per PR, so the list is reduced
    before it is drawn: `combined_state` picks the base and `combined_badge`
    picks the badge, each of them reading the set the way the row's reader
    would — the least-settled state, and the loudest thing still to do.

    Bigger than the footer's mark, because it sits in a line of title text
    rather than among caption-sized chips, and because it is the only thing on
    the row carrying a color.
    """
    prs = list(prs)
    return _mark(combined_state(prs), combined_badge(prs), ROW_ICON_PX, ROW_BADGE_PX)


def status_mark(pr: PullRequest) -> Gtk.Widget:
    """`status_icon`, sized for the menus' mark column.

    Always returns something — every PR has a base icon, fetched status or not
    — so titles line up down the menu.
    """
    mark = status_icon(pr)
    mark.set_size_request(MARK_COLUMN_PX, -1)
    return mark


def loading_mark() -> Gtk.Widget:
    """The mark column while a row's status is being fetched.

    A spinner in the slot the mark will land in: the list is readable the
    moment it opens (the titles and numbers are already known), and the one
    part of it that is still coming says so where it will appear.
    """
    spinner = Gtk.Spinner(spinning=True)
    spinner.set_size_request(MARK_COLUMN_PX, MERGED_ICON_PX)
    return spinner


def fill(
    popover: Gtk.Popover,
    prs: Iterable[PullRequest],
    loading: bool = False,
    host: ActionHost | None = None,
) -> None:
    """Put *prs* into *popover* as its list, oldest first.

    Every PR the session has picked up, in the order the work happened, with
    the titles the footer's chips have no room for. Rebuilt per call rather
    than kept in sync: statuses move under it, and it is only ever on screen
    for as long as someone is reading it.

    With a *host*, every row leads into that PR's actions on a plain click
    (its page moves to the right-click) — and the list this call built is what
    the submenu's header leads back to.
    """
    prs = list(prs)  # closed over by the rows' way back from the submenu
    rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    for pr in prs:
        name = Gtk.Label(label=menu_name(pr), xalign=0.0, hexpand=True)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(MAX_CHARS)
        # Its own label, so a long title ellipsizes without taking the one
        # thing that identifies the PR with it.
        number = Gtk.Label(label=f"(#{pr.number})")
        number.add_css_class("dim-label")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.append(loading_mark() if loading else status_mark(pr))
        row.append(name)
        row.append(number)
        button = Gtk.Button(child=row)
        button.add_css_class("flat")
        button.add_css_class("pr-menu-row")  # menu-sized, and lit under the pointer
        detail = describe(pr) + "\n" + pr.url
        if host is None:
            # No actions to offer, so the click keeps opening the page.
            button.set_tooltip_text(open_tooltip(detail))
            button.connect("clicked", _on_row_clicked, popover, pr.url)
        else:
            button.set_tooltip_text(
                detail + "\n" + _("Click for actions") + "\n" + _("Right-click to open")
            )
            button.connect(
                "clicked",
                lambda _b, pr=pr: show_actions(popover, pr, host, back=lambda: refill(popover)),
            )
            _on_click(
                button,
                Gdk.BUTTON_SECONDARY,
                lambda button=button, pr=pr: _on_row_clicked(button, popover, pr.url),
            )
        rows.append(button)
    # A session with a lot of PRs would otherwise open a popover taller than
    # the window it is in. A list that isn't that long doesn't scroll at all,
    # though — and mustn't be allowed to: a scrollable view won't measure
    # shorter than a usable scrolling area (44px), which a single 36px row is,
    # so it left 8px of stray space under the only row.
    scroller = Gtk.ScrolledWindow(child=rows)
    overflows = rows.measure(Gtk.Orientation.VERTICAL, -1).natural > MAX_HEIGHT_PX
    scroller.set_policy(
        Gtk.PolicyType.NEVER,
        Gtk.PolicyType.AUTOMATIC if overflows else Gtk.PolicyType.NEVER,
    )
    scroller.set_propagate_natural_width(True)
    scroller.set_propagate_natural_height(True)
    scroller.set_max_content_height(MAX_HEIGHT_PX)
    popover.set_child(scroller)
    setattr(popover, _ACTIONS, None)
    setattr(popover, _ACTIONS_BACK, None)
    setattr(popover, _LIST, (prs, loading, host))


def refill(popover: Gtk.Popover) -> None:
    """Put the list back up, as it stands now.

    What a submenu's header leads back to. "As it stands now" rather than "as
    it was left": a fetch that landed while the submenu was open (see `update`)
    has been waiting here, so stepping back never returns to spinners that
    stopped spinning a minute ago.
    """
    prs, loading, host = getattr(popover, _LIST, ([], False, None))
    fill(popover, prs, loading, host)


def update(popover: Gtk.Popover, prs: Iterable[PullRequest], host: ActionHost | None) -> None:
    """Land a refreshed list into an open menu, wherever the reader is in it.

    On the list, that means rebuilding it. Inside a *submenu* — actions someone
    stepped into from the list — it means leaving what is on screen alone,
    because swapping a menu out from under the pointer is how a click lands on
    something nobody chose; the new list goes behind it, for the way back.

    Actions with nothing behind them are the exception (a session with a single
    PR opens straight into them, see SessionRow._fill_pr_menu). There they are
    not a submenu but the whole menu, standing where the list would have stood
    and refreshing as the list would: what the fetch just learned is the
    difference between offering to merge a PR and offering to fix its build.
    Only an actual change rebuilds, so a fetch that confirms what was already
    there leaves the rows exactly where the pointer left them.
    """
    prs = list(prs)
    child = popover.get_child()
    if child is not None and not child.get_sensitive():
        # An action is running in here: the menu was made insensitive for the
        # duration and a spinner is turning in the row that started it (see
        # _start_action). Rebuilding now would hand back a live menu that says
        # nothing is happening, so the merge in flight could be started twice.
        return
    shown = getattr(popover, _ACTIONS, None)
    if shown is None:
        fill(popover, prs, host=host)
        return
    setattr(popover, _LIST, (prs, False, host))
    if getattr(popover, _ACTIONS_BACK, None) is not None or host is None:
        return  # a submenu: leave it, the list behind it is now current
    if len(prs) != 1:
        fill(popover, prs, host=host)  # no longer a list of one, so show the list
    elif prs[0] != shown:
        show_actions(popover, prs[0], host)


def _on_row_clicked(button: Gtk.Button, popover: Gtk.Popover, url: str) -> None:
    popover.popdown()
    open_uri(button, url)


def _on_click(widget: Gtk.Widget, button: int, callback: Callable[[], None]) -> None:
    """Call *callback* when *widget* is clicked with *button*, and only then.

    The gesture claims the click, so it never doubles up with whatever else
    the widget answers a click with — a row that opens its actions on a plain
    click mustn't also step into the submenu on the way to the browser.
    """

    def pressed(gesture: Gtk.GestureClick, _n_press: int, _x: float, _y: float) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        callback()

    gesture = Gtk.GestureClick(button=button)
    gesture.connect("pressed", pressed)
    widget.add_controller(gesture)


# -- the actions submenu ---------------------------------------------------


def showing_actions(popover: Gtk.Popover) -> bool:
    """Whether *popover* is currently showing a PR's actions rather than the list.

    What tells a landing refresh which of the two it is looking at (see
    `update`) — a list that refreshes itself, as the sidebar's does behind its
    spinners, must not rebuild a submenu the user has stepped into.
    """
    return getattr(popover, _ACTIONS, None) is not None


def show_actions(
    popover: Gtk.Popover,
    pr: PullRequest,
    host: ActionHost,
    back: Callable[[], None] | None = None,
) -> None:
    """Show *pr*'s actions in *popover*, replacing whatever was in it.

    *back* is how the header row returns to the list this was opened from;
    without one (a footer chip's own menu) the header is just a title, since
    there is nothing behind it.
    """
    setattr(popover, _ACTIONS, pr)
    setattr(popover, _ACTIONS_BACK, back)
    rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    rows.append(_header(pr, back))
    separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    separator.set_margin_top(3)
    separator.set_margin_bottom(3)
    rows.append(separator)
    if host.view_pr is not None:
        rows.append(_view_row(popover, pr, host.view_pr))
    if host.view_unresolved is not None and pr.awaiting_reply:
        rows.append(_unresolved_row(popover, pr, host.view_unresolved))
    rows.append(_open_row(popover, pr))
    for action in practions.actions_for(pr, host.prompt_block(), host.has_changes):
        rows.append(_action_row(popover, pr, action, host))
    popover.set_child(rows)


def attach_view(
    chip: Gtk.Widget, pr: PullRequest, view: Callable[[PullRequest], None]
) -> None:
    """Give a footer chip *pr*'s page on a plain click.

    The pointer cursor comes with it: a chip that opens something on a click
    should say so before it is clicked, the way the linked labels beside it do.
    """
    chip.set_cursor(Gdk.Cursor.new_from_name("pointer"))
    _on_click(chip, Gdk.BUTTON_PRIMARY, lambda: view(pr))


def attach_actions(chip: Gtk.Widget, pr: PullRequest, host: ActionHost) -> None:
    """Give a footer chip *pr*'s actions on a right-click.

    Where a context menu belongs, and it leaves the plain click free for the
    thing a chip is most often clicked for — reading the PR (see attach_view).
    Everything else the chip can do is in here, the browser included.

    The list's submenu has a popover to borrow; a chip has none, so each
    opening builds one on the chip and lets go of it when it closes — the
    chips are rebuilt whenever a status moves, and a popover outliving the
    chip it pointed at would be a menu attached to nothing.
    """

    def open_menu() -> None:
        popover = new_popover(Gtk.PositionType.TOP)
        popover.set_parent(chip)
        popover.connect("closed", lambda menu: GLib.idle_add(menu.unparent))
        show_actions(popover, pr, host)
        popover.popup()

    _on_click(chip, Gdk.BUTTON_SECONDARY, open_menu)


def _header(pr: PullRequest, back: Callable[[], None] | None) -> Gtk.Widget:
    """Which PR the actions below belong to, and the way back to the list.

    Same shape as a list row — mark column, title, number — so stepping into
    the submenu reads as the row you clicked moving to the top, with the
    status mark it had swapped for the arrow that leads back.
    """
    name = Gtk.Label(label=menu_name(pr), xalign=0.0, hexpand=True)
    name.set_ellipsize(Pango.EllipsizeMode.END)
    name.set_max_width_chars(MAX_CHARS)
    number = Gtk.Label(label=f"(#{pr.number})")
    number.add_css_class("dim-label")
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    if back is None:
        row.append(status_mark(pr))
        row.append(name)
        row.append(number)
        row.add_css_class("pr-menu-title")
        return row
    arrow = Gtk.Image.new_from_icon_name("go-previous-symbolic")
    arrow.set_pixel_size(MERGED_ICON_PX)
    arrow.set_size_request(MARK_COLUMN_PX, -1)
    arrow.add_css_class("dim-label")
    row.append(arrow)
    row.append(name)
    row.append(number)
    button = Gtk.Button(child=row)
    button.add_css_class("flat")
    button.add_css_class("pr-menu-row")
    button.set_tooltip_text(_("Back to the pull requests"))
    button.connect("clicked", lambda *_a: back())
    return button


def _view_row(
    popover: Gtk.Popover, pr: PullRequest, view: Callable[[PullRequest], None]
) -> Gtk.Widget:
    """The native way to read a PR: its page docked beside the session.

    Listed above "Open on GitHub" — the in-app view is the nearer of the two
    readings — and only when the host can show one (see ActionHost.view_pr).
    """
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    row.append(_mark_icon("view-paged-symbolic", None))
    row.append(Gtk.Label(label=_("View in Collins"), xalign=0.0, hexpand=True))
    button = Gtk.Button(child=row)
    button.add_css_class("flat")
    button.add_css_class("pr-menu-row")
    button.set_tooltip_text(_("Open this pull request's page beside the session"))

    def clicked(*_args) -> None:
        popover.popdown()
        view(pr)

    button.connect("clicked", clicked)
    return button


def _unresolved_row(
    popover: Gtk.Popover, pr: PullRequest, view: Callable[[PullRequest], None]
) -> Gtk.Widget:
    """The unresolved badge's deep link: the native page, landed on the
    conversation that is waiting.

    Only built while the PR awaits a reply — the row answers the warning
    triangle, and a menu offering to show unresolved comments on a PR with
    none would be the app disagreeing with itself. Wears the badge's own
    mark, so the row visibly answers it.
    """
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    row.append(_mark_icon("alert-fill-symbolic", "pr-unresolved"))
    row.append(Gtk.Label(label=_("View unresolved comments"), xalign=0.0, hexpand=True))
    button = Gtk.Button(child=row)
    button.add_css_class("flat")
    button.add_css_class("pr-menu-row")
    button.set_tooltip_text(
        _("Open this pull request's page at its first unresolved thread")
    )

    def clicked(*_args) -> None:
        popover.popdown()
        view(pr)

    button.connect("clicked", clicked)
    return button


def _open_row(popover: Gtk.Popover, pr: PullRequest) -> Gtk.Widget:
    """The one action every PR offers: its page on GitHub, in the browser.

    First in the menu, ahead of whatever practions has for this PR's state,
    and there whatever that state is — a merged pull request over a clean tree
    has run out of things Collins can do to it, but its page is still worth a
    visit, and a menu with this row in it never opens onto a title and a gap.
    Built here rather than in practions because opening a browser is a Gtk
    affair, and practions stays importable without one.
    """
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    # GitHub's own mark in the column the header's status mark sits in, so the
    # label lines up under the title; uncolored, like the sidebar's button.
    row.append(_mark_icon("github-symbolic", None))
    row.append(Gtk.Label(label=_("Open on GitHub"), xalign=0.0, hexpand=True))
    button = Gtk.Button(child=row)
    button.add_css_class("flat")
    button.add_css_class("pr-menu-row")
    button.set_tooltip_text(open_tooltip(pr.url))
    button.connect("clicked", _on_row_clicked, popover, pr.url)
    return button


def _mark_icon(icon_name: str, css_class: str | None) -> Gtk.Widget:
    """An action row's mark, sized and slotted like the header's status mark."""
    icon = Gtk.Image.new_from_icon_name(icon_name)
    icon.set_pixel_size(MERGED_ICON_PX)
    icon.set_size_request(MARK_COLUMN_PX, -1)
    if css_class is not None:
        icon.add_css_class(css_class)
    return icon


def action_icon(key: str) -> Gtk.Widget:
    """The mark action *key* carries in the menu, in the menu's mark column.

    For anywhere that lists actions *outside* a menu — the GitHub CLI notice
    shows a few of them to say what gh unlocks — so the icons there are the
    ones that will be on the rows themselves.

    An action with no mark of its own still takes the column's width, so a
    list stays a column either way. The menu's own rows reach that same width
    by indenting the label instead (see `_action_row`), which is the one place
    the two paths part company: a row there is a button and an empty image in
    it would be one more thing for a click to land on. Here there is no
    click, so an empty image is the simpler way to hold the space.
    """
    return _mark_icon(*_ACTION_ICONS.get(key, ("", None)))


def action_button(action: practions.Action, spinner: Gtk.Spinner | None = None) -> Gtk.Button:
    """*action* as a menu row's button: its mark, what it does, its tooltip.

    Public because the PR page builds a menu of its own — the alternates
    behind its action button (see prview) — and a menu that looks like the
    chips' menus is one menu the reader has learned once. *spinner*, where the
    caller has somewhere to run one, sits at the row's end; the page spins its
    button instead and passes none.

    Nothing is connected here: what a row does when it is picked belongs to
    whoever is showing it.
    """
    label = Gtk.Label(label=action.label, xalign=0.0, hexpand=True)
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    # The mark keeps the label under the header's title, where the plain
    # indent used to; an action the map doesn't know keeps the indent, so a
    # future action without a mark still lines up rather than jutting out.
    mark = _ACTION_ICONS.get(action.key)
    if mark is not None:
        row.append(_mark_icon(*mark))
    else:
        label.set_margin_start(MARK_COLUMN_PX + 8)
    row.append(label)
    if spinner is not None:
        row.append(spinner)
    button = Gtk.Button(child=row)
    button.add_css_class("flat")
    button.add_css_class("pr-menu-row")
    tooltip = "\n".join(part for part in (action.tooltip, action.blocked) if part)
    if tooltip:
        button.set_tooltip_text(tooltip)
    return button


def _action_row(
    popover: Gtk.Popover, pr: PullRequest, action: practions.Action, host: ActionHost
) -> Gtk.Widget:
    """One action as a row: its mark, what it does, and a spinner for while it
    is doing it.

    An action the session can't take right now (see practions.Action.blocked)
    is that same row, unpressable and saying why — the badge that sent the user
    here is still on the PR, so the menu owes them the answer rather than the
    gap where it was.
    """
    spinner = Gtk.Spinner()
    spinner.set_visible(False)
    button = action_button(action, spinner)
    if not action.blocked:
        button.connect("clicked", _on_action_clicked, popover, pr, action, host, spinner)
        return button
    # Insensitive widgets are skipped when GTK picks what the pointer is over,
    # so a tooltip set on the button itself would never be shown — the reason
    # the row is dead would be readable nowhere. Hand it to a wrapper that
    # stays sensitive: the pick lands there instead, and the tooltip with it.
    button.set_sensitive(False)
    button.set_hexpand(True)  # the wrapper is the row now; the button still fills it
    wrapper = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    wrapper.set_tooltip_text(button.get_tooltip_text())
    wrapper.append(button)
    return wrapper


def _on_action_clicked(
    button: Gtk.Button,
    popover: Gtk.Popover,
    pr: PullRequest,
    action: practions.Action,
    host: ActionHost,
    spinner: Gtk.Spinner,
) -> None:
    """Pick an action apart into the three kinds there are.

    Typing into the session happens here and now, so the menu goes with it. An
    action that asks first can't keep the menu either — a dialog takes the
    pointer grab a popover is holding, closing it — so those run with the menu
    already down. What is left runs under a spinner in its own row, which is
    the only case where there is anything to watch.

    Whether an action asks at all is `practions.confirmation`'s answer rather
    than the action's own: a merge stops asking when the setting says so, and
    then it is one of the ones that runs under the spinner.
    """
    root = button.get_root()
    if action.prompt:
        popover.popdown()
        host.send_prompt(action.prompt)
        return
    confirm = practions.confirmation(action, host.confirm_merges())
    if confirm is not None:
        popover.popdown()
        dialogs.confirm_dialog(
            root,
            confirm.heading,
            confirm.body,
            confirm.label,
            lambda: _start_action(pr, action, host, root, None, None),
            destructive=confirm.destructive,
            confirm_class=(
                practions.MERGE_CONFIRM_CSS if action.key in practions.MERGES else None
            ),
        )
        return
    _start_action(pr, action, host, root, popover, spinner)


def _start_action(
    pr: PullRequest,
    action: practions.Action,
    host: ActionHost,
    root: Gtk.Root | None,
    popover: Gtk.Popover | None,
    spinner: Gtk.Spinner | None,
) -> None:
    """Run *action* against *pr* off the main loop, then land what happened.

    The menu is left alone until the answer arrives: gh takes a second or two
    over a merge, and a menu that vanished on the click would leave nothing
    saying anything was happening. Insensitive, though — one merge is enough.
    """
    if spinner is not None:
        spinner.set_visible(True)
        spinner.start()
    if popover is not None and popover.get_child() is not None:
        popover.get_child().set_sensitive(False)

    def work() -> None:
        try:
            error = practions.perform(action.key, pr)
        except Exception:  # a menu item must never take the app down with it
            log.debug("prmenu: %s on %s failed", action.key, pr.url, exc_info=True)
            error = _("Collins couldn't run that action.")
        GLib.idle_add(_action_landed, pr, action, host, root, popover, error)

    threading.Thread(target=work, name="pr-action", daemon=True).start()


def _action_landed(
    pr: PullRequest,
    action: practions.Action,
    host: ActionHost,
    root: Gtk.Root | None,
    popover: Gtk.Popover | None,
    error: str | None,
) -> bool:
    """Close the menu, then either say what went wrong or go and re-read the PR.

    Success is deliberately quiet: what changed is on the PR, and the chip (or
    the row's list) is about to show it — a merged PR turns purple within the
    second. Only a failure has something to say that nothing else will.
    """
    if popover is not None:
        popover.popdown()
    if error:
        if root is not None:
            dialogs.error_dialog(root, _("{action} failed").format(action=action.label), error)
        return GLib.SOURCE_REMOVE
    invalidate(pr.url)
    host.refresh()
    return GLib.SOURCE_REMOVE
