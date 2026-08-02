"""A session's pull requests as a popover list: one row per PR, titles and all.

Two buttons open this same list. The caret beside the tab footer's PR chips
opens it for the tab in front of you, off a list its poll keeps current; the
GitHub button on a sidebar row opens it for a session whose tab may not even
be open, off the list saved for that session — which is why that one refreshes
before it shows anything (see SessionRow._fill_pr_menu).

The widgets live here rather than beside either caller so both get the same
menu: the same mark column, the same ellipsizing title, the same click that
opens the PR page and closes the menu behind it.

Left-clicking a PR asks what to *do* with it; right-clicking one opens its
page. That submenu is the same list of actions wherever it is opened from
(practions decides what a PR offers, under an "Open on GitHub" row every PR
has), so it is built here too: from a row it slides in over the list and leads
back with a header row, and from a footer chip — which has no list behind it —
it opens on the chip as a menu of its own.
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
    CHECKS_FAILED,
    CHECKS_PASSED,
    CHECKS_PENDING,
    PullRequest,
    describe,
    invalidate,
    menu_name,
)

log = logging.getLogger(__name__)

# The merge mark sits with caption-sized text, so it takes the same 12px as the
# glyphs it replaces rather than a symbolic icon's stock 16.
MERGED_ICON_PX = 12
# The column the status marks share, so the titles beside them line up; how
# wide a title gets before it ellipsizes; and how tall the list gets before it
# scrolls.
MARK_COLUMN_PX = 14
MAX_CHARS = 48
MAX_HEIGHT_PX = 400
# Each CI mark is colored like its counterpart on the PR page; the shades
# themselves follow the light/dark scheme and live in app.py.
CHECKS_CSS = {
    CHECKS_PASSED: "pr-checks-passed",
    CHECKS_FAILED: "pr-checks-failed",
    CHECKS_PENDING: "pr-checks-pending",
}
# What a popover is currently showing: the PR whose actions are up (None while
# it is the list), and the list itself — the contents a submenu leads back to,
# which a refresh can replace without disturbing the submenu on top of it.
_ACTIONS = "_collins_actions_pr"
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
    practions.REVIEW: ("agent-claude-symbolic", None),
    practions.FIX_CI: ("agent-claude-symbolic", None),
    practions.NEW_PR: ("agent-claude-symbolic", None),
}


@dataclass(frozen=True)
class ActionHost:
    """The session behind a PR menu, as the four things its actions need.

    A PR's actions are mostly GitHub's business, but the ones that send a
    prompt are the session's: whether it is somewhere a prompt can be sent,
    whether its working tree has anything to open a pull request for, and how
    to send one. The footer's chips answer all of that from the tab they are
    in; a sidebar row answers it through the window, whose tab that session
    may not even have. Hence callables rather than values: a menu built once
    is opened much later, and what a terminal is waiting at changes by the
    keystroke.
    """

    # Whether the session has a tab open, at an empty prompt (Provider.takes_prompt).
    takes_prompt: Callable[[], bool]
    # Whether its terminal's cwd is a repo with uncommitted work in it. Costs a
    # `git status`, so practions only asks when a PR's state could use it.
    has_changes: Callable[[], bool]
    # Type an action's prompt into that session, send it, and put it in front.
    send_prompt: Callable[[str], None]
    # An action changed the PR on GitHub; re-read its status.
    refresh: Callable[[], None]


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


def status_mark(pr: PullRequest) -> Gtk.Widget:
    """A PR's status as one widget: its CI glyph, or the merge mark.

    Always returns something, so titles line up down the menu even beside a PR
    whose status hasn't been fetched yet.
    """
    if pr.merged:
        mark: Gtk.Widget = Gtk.Image.new_from_icon_name("git-merge-symbolic")
        mark.set_pixel_size(MERGED_ICON_PX)
        mark.add_css_class("pr-merged")
    else:
        glyph = pr.glyph
        mark = Gtk.Label(label=glyph or "")
        mark.set_css_classes(["caption", CHECKS_CSS.get(glyph or "", "dim-label")])
    mark.set_size_request(MARK_COLUMN_PX, -1)
    return mark


def loading_mark() -> Gtk.Widget:
    """The mark column while a row's status is being fetched.

    A spinner in the slot the glyph will land in: the list is readable the
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

    On the list, that means rebuilding it. Inside a PR's actions, it means
    leaving what is on screen alone — swapping a menu out from under the
    pointer is how a click lands on something nobody chose — and putting the
    new list behind it, for the way back.
    """
    if showing_actions(popover):
        setattr(popover, _LIST, (list(prs), False, host))
    else:
        fill(popover, prs, host=host)


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
    rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    rows.append(_header(pr, back))
    separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    separator.set_margin_top(3)
    separator.set_margin_bottom(3)
    rows.append(separator)
    rows.append(_open_row(popover, pr))
    for action in practions.actions_for(pr, host.takes_prompt(), host.has_changes):
        rows.append(_action_row(popover, pr, action, host))
    popover.set_child(rows)


def attach_actions(chip: Gtk.Widget, pr: PullRequest, host: ActionHost) -> None:
    """Give a footer chip *pr*'s actions on a plain click.

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

    _on_click(chip, Gdk.BUTTON_PRIMARY, open_menu)


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


def _action_row(
    popover: Gtk.Popover, pr: PullRequest, action: practions.Action, host: ActionHost
) -> Gtk.Widget:
    """One action as a row: its mark, what it does, and a spinner for while it
    is doing it."""
    label = Gtk.Label(label=action.label, xalign=0.0, hexpand=True)
    spinner = Gtk.Spinner()
    spinner.set_visible(False)
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
    row.append(spinner)
    button = Gtk.Button(child=row)
    button.add_css_class("flat")
    button.add_css_class("pr-menu-row")
    if action.tooltip:
        button.set_tooltip_text(action.tooltip)
    button.connect("clicked", _on_action_clicked, popover, pr, action, host, spinner)
    return button


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
    """
    root = button.get_root()
    if action.prompt:
        popover.popdown()
        host.send_prompt(action.prompt)
        return
    if action.confirm is not None:
        popover.popdown()
        dialogs.confirm_dialog(
            root,
            action.confirm.heading,
            action.confirm.body,
            action.confirm.label,
            lambda: _start_action(pr, action, host, root, None, None),
            destructive=False,
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
