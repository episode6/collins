# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Drop-zone geometry for the panel dock's edge docking, GTK-free.

While a panel page's tab is being dragged, the dock overlays drop zones
on every visible leaf: four edge zones (split that leaf and put the page
in a new strip there) and, on strips only, a center zone (join as a tab
at the pointer's position — which is also how tabs reorder within their
own strip). This module is the pure geometry — which zone a pointer
position lands in, the rectangle to highlight for it, and where among a
row of tabs a drop inserts — so the rules are unit-testable without a
display. The GTK realization (`DropZones` in paneldnd.py) feeds it leaf
bounds and draws whatever it answers.

A zone is one of "left" | "right" | "above" | "below" | "center", matching
`docktree.split` sides. Each leaf carries its own set of *allowed* zones —
the terminal never offers center, a single-page drag source offers
nothing (every drop would reassemble the same layout) — and a pointer
over a leaf with no allowed zone hits nothing.
"""

from __future__ import annotations

EDGE_ZONES = ("left", "right", "above", "below")

# The center zone claims the middle of the leaf; pointers outside this
# central fraction (per axis) fall to the nearest edge instead.
_CENTER_FRACTION = 0.5


def zone_at(width: float, height: float, x: float, y: float, allowed) -> str | None:
    """The zone at (*x*, *y*) inside a *width*×*height* leaf, or None.

    Center wins when allowed and the pointer sits in the middle half of
    both axes; otherwise the nearest allowed edge (by edge distance in
    fractions of the axis, so wide-but-short leaves still split
    sensibly) takes it. Points outside the rect, degenerate rects, and
    leaves allowing nothing answer None."""
    if width <= 0 or height <= 0 or not 0 <= x <= width or not 0 <= y <= height:
        return None
    margin = (1 - _CENTER_FRACTION) / 2
    if (
        "center" in allowed
        and margin * width <= x <= (1 - margin) * width
        and margin * height <= y <= (1 - margin) * height
    ):
        return "center"
    edges = {
        "left": x / width,
        "right": (width - x) / width,
        "above": y / height,
        "below": (height - y) / height,
    }
    candidates = [(dist, zone) for zone, dist in edges.items() if zone in allowed]
    if not candidates:
        return None
    return min(candidates)[1]


def hit(leaves, x: float, y: float) -> tuple[int, str] | None:
    """Which leaf and zone the dock-relative point (*x*, *y*) lands in.

    *leaves* is a sequence of `(lx, ly, width, height, allowed)` rects in
    dock coordinates; leaves never overlap, so the first containing rect
    decides. Returns `(index, zone)`, or None over no leaf / no zone."""
    for index, (lx, ly, width, height, allowed) in enumerate(leaves):
        if lx <= x <= lx + width and ly <= y <= ly + height:
            zone = zone_at(width, height, x - lx, y - ly, allowed)
            return None if zone is None else (index, zone)
    return None


def insert_index(centers, x: float) -> int:
    """Where a tab dropped at *x* inserts among tabs whose horizontal
    centers are *centers* (in the same coordinate space, any order): before
    the first tab whose center lies right of the drop point. The result
    counts every listed tab — a same-strip reorder subtracts the dragged
    tab itself when it started left of the target (see
    PanelStrip.reorder_to)."""
    return sum(1 for center in centers if center < x)


def zone_rect(
    lx: float, ly: float, width: float, height: float, zone: str
) -> tuple[float, float, float, float]:
    """The highlight rectangle for *zone* of the leaf at
    (*lx*, *ly*, *width*, *height*): the half of the leaf the new strip
    would occupy, or the whole leaf for center (joining takes it all)."""
    if zone == "left":
        return (lx, ly, width / 2, height)
    if zone == "right":
        return (lx + width / 2, ly, width / 2, height)
    if zone == "above":
        return (lx, ly, width, height / 2)
    if zone == "below":
        return (lx, ly + height / 2, width, height / 2)
    if zone == "center":
        return (lx, ly, width, height)
    raise ValueError(f"unknown zone: {zone!r}")
