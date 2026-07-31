# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Scroll arithmetic for bringing a row of a list into view.

Kept apart from the widgets that use it so the rules are testable without a
GTK stack (see tests/conftest.py for why that matters).
"""

from __future__ import annotations


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
