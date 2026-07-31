"""A session's pull requests as a popover list: one row per PR, titles and all.

Two buttons open this same list. The caret beside the tab footer's PR chips
opens it for the tab in front of you, off a list its poll keeps current; the
GitHub button on a sidebar row opens it for a session whose tab may not even
be open, off the list saved for that session — which is why that one refreshes
before it shows anything (see SessionRow._fill_pr_menu).

The widgets live here rather than beside either caller so both get the same
menu: the same mark column, the same ellipsizing title, the same click that
opens the PR page and closes the menu behind it.
"""

from __future__ import annotations

from collections.abc import Iterable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from .copylabel import open_tooltip, open_uri  # noqa: E402
from .prstatus import (  # noqa: E402
    CHECKS_FAILED,
    CHECKS_PASSED,
    CHECKS_PENDING,
    PullRequest,
    describe,
    menu_name,
)

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


def fill(popover: Gtk.Popover, prs: Iterable[PullRequest], loading: bool = False) -> None:
    """Put *prs* into *popover* as its list, oldest first.

    Every PR the session has picked up, in the order the work happened, with
    the titles the footer's chips have no room for. Rebuilt per call rather
    than kept in sync: statuses move under it, and it is only ever on screen
    for as long as someone is reading it.
    """
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
        button.set_tooltip_text(open_tooltip(describe(pr) + "\n" + pr.url))
        button.connect("clicked", _on_row_clicked, popover, pr.url)
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


def _on_row_clicked(button: Gtk.Button, popover: Gtk.Popover, url: str) -> None:
    popover.popdown()
    open_uri(button, url)
