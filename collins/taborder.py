"""Where tabs sit in the bar, and which one the screen falls to.

The tab bar reads left to right as the session list reads top to bottom, so
the two panes never disagree about where a session lives. The window supplies
one sidebar row id per open tab (None for a tab no row stands for — a chat, a
replay, a session that has since been archived) and gets back the order those
tabs should sit in.
"""

from __future__ import annotations

from collections.abc import Container, Sequence


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


def neighbour_tab(index: int, n_pages: int, skip: Container[int] = ()) -> int | None:
    """The position of the tab to show in place of the one at *index*, or None
    when there is no other tab to show.

    The next tab along, or — at the right-hand end of the bar — the previous
    one, which is the order AdwTabView itself picks when a page closes.
    Positions in *skip* are passed over: a tab that is on its own way out is
    no better a place to land than the one being left.
    """
    for position in [*range(index + 1, n_pages), *range(index - 1, -1, -1)]:
        if position not in skip:
            return position
    return None
