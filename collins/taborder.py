"""Tab-bar order derived from the sidebar's row order.

The tab bar reads left to right as the session list reads top to bottom, so
the two panes never disagree about where a session lives. The window supplies
one sidebar row id per open tab (None for a tab no row stands for — a chat, a
replay, a session that has since been archived) and gets back the order those
tabs should sit in.
"""

from __future__ import annotations

from collections.abc import Sequence


def tab_order(row_ids: Sequence[str | None], row_order: Sequence[str]) -> list[int]:
    """Indices into `row_ids`, in the order their tabs belong left to right.

    Sorting is stable, which is what makes the two interesting cases behave:
    tabs sharing a row (a fork sits alongside the session it forked) and tabs
    with no row at all keep the order they already had, and the rowless ones
    collect at the right-hand end rather than scattering through the bar.
    """
    rank = {row_id: i for i, row_id in enumerate(row_order)}
    last = len(row_order)  # no row of its own: after every tab that has one
    return sorted(range(len(row_ids)), key=lambda i: rank.get(row_ids[i], last))
