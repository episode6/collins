# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Scroll arithmetic: bringing a row of a list into view, and keeping a list
that grows downward parked on its newest row.

Kept apart from the widgets that use it so the rules are testable without a
GTK stack (see tests/conftest.py for why that matters).
"""

from __future__ import annotations

# How far off the bottom edge still counts as being parked at it, for a list
# that follows its own growth (the attachments panel, the chat view). One
# shared number rather than one per view: the two would drift apart silently,
# and "how far from the bottom is still the bottom" is the same question in
# both. Generous on purpose — a nudge of the wheel shouldn't unpark a list,
# and reading a row properly means scrolling well clear of this.
BOTTOM_SLACK = 48.0


def offset_into_view(
    row_y: float, row_height: float, value: float, page_size: float
) -> float:
    """The scroll offset that shows the row, moving the list as little as
    possible.

    `row_y` is the row's top edge in the scrolled content's coordinates, and
    `value`/`page_size` describe the window onto that content. A row already
    fully on screen leaves the offset untouched; anything else is pulled to
    the nearest edge, so a row just off the bottom rises by exactly the gap
    rather than jumping to the top of the viewport. A row taller than the
    viewport aligns to its top — that end carries the title.
    """
    if row_y < value:
        return row_y
    if row_y + row_height > value + page_size:
        return min(row_y, row_y + row_height - page_size)
    return value


def bottom(upper: float, page_size: float) -> float:
    """The scroll offset that parks a list on its last row.

    Never negative: content shorter than the viewport has no bottom to park
    on, and its whole scroll range is 0.
    """
    return max(0.0, upper - page_size)


def at_bottom(
    value: float, upper: float, page_size: float, slack: float = BOTTOM_SLACK
) -> bool:
    """Whether a list is close enough to its bottom edge to count as parked
    there — i.e. whether it should follow the next thing that lands.

    Within *slack* of the edge counts, and so does past it: a viewport taller
    than its content puts the one valid offset (0) above `bottom`.
    """
    return value >= bottom(upper, page_size) - slack
