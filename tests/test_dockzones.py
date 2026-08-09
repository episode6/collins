# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Unit tests for the dock's drop-zone geometry (dockzones)."""

import pytest

from collins.dockzones import EDGE_ZONES, hit, zone_at, zone_rect

ALL = EDGE_ZONES + ("center",)


# -- zone_at -----------------------------------------------------------------


def test_center_wins_in_the_middle_when_allowed():
    assert zone_at(400, 200, 200, 100, ALL) == "center"
    assert zone_at(400, 200, 110, 60, ALL) == "center"  # just inside the middle half


def test_middle_without_center_goes_to_nearest_edge():
    # Dead center with no center zone: ties broken deterministically, but
    # slightly off-center picks the closest edge by axis fraction.
    assert zone_at(400, 200, 40, 100, EDGE_ZONES) == "left"
    assert zone_at(400, 200, 360, 100, EDGE_ZONES) == "right"
    assert zone_at(400, 200, 200, 20, EDGE_ZONES) == "above"
    assert zone_at(400, 200, 200, 180, EDGE_ZONES) == "below"


def test_edges_outside_the_center_band():
    assert zone_at(400, 200, 30, 100, ALL) == "left"
    assert zone_at(400, 200, 370, 100, ALL) == "right"
    assert zone_at(400, 200, 200, 10, ALL) == "above"
    assert zone_at(400, 200, 200, 190, ALL) == "below"


def test_corner_picks_the_closer_edge_by_fraction():
    # 10px into a 400px width (2.5%) vs 20px into a 200px height (10%):
    # the left edge is fractionally closer even though 20px > 10px flipped.
    assert zone_at(400, 200, 10, 20, EDGE_ZONES) == "left"
    # Mirrored: 10px into the height (5%) vs 40px into the width (10%).
    assert zone_at(400, 200, 40, 10, EDGE_ZONES) == "above"


def test_allowed_filters_edges():
    # A pointer hugging a disallowed edge falls to the nearest allowed one.
    assert zone_at(400, 200, 5, 100, ("right",)) == "right"
    assert zone_at(400, 200, 5, 100, ()) is None


def test_out_of_rect_and_degenerate_answer_none():
    assert zone_at(400, 200, -1, 100, ALL) is None
    assert zone_at(400, 200, 401, 100, ALL) is None
    assert zone_at(400, 200, 200, 201, ALL) is None
    assert zone_at(0, 200, 0, 100, ALL) is None
    assert zone_at(400, 0, 200, 0, ALL) is None


# -- hit ---------------------------------------------------------------------

LEAVES = [
    (0, 0, 400, 300, EDGE_ZONES),  # the terminal: edges only
    (0, 300, 400, 100, ALL),  # a strip below it
]


def test_hit_resolves_leaf_and_zone():
    assert hit(LEAVES, 200, 20) == (0, "above")
    assert hit(LEAVES, 200, 350) == (1, "center")
    assert hit(LEAVES, 10, 350) == (1, "left")


def test_hit_misses_between_and_outside_leaves():
    assert hit(LEAVES, 200, 450) is None  # below everything
    assert hit([], 10, 10) is None


def test_hit_center_over_terminal_is_no_zone_only_if_disallowed():
    # Dead center of the terminal leaf: center isn't allowed there, so the
    # nearest edge answers instead — never None inside an edge-only leaf.
    assert hit(LEAVES, 200, 150) in ((0, z) for z in EDGE_ZONES)


def test_hit_leaf_with_no_zones_swallows_the_point():
    # A drag's single-page source strip allows nothing: pointing at it
    # hits no zone, and does NOT fall through to a leaf behind it.
    leaves = [(0, 0, 400, 100, ())]
    assert hit(leaves, 200, 50) is None


# -- zone_rect ---------------------------------------------------------------


def test_zone_rects_are_the_occupied_half():
    assert zone_rect(0, 0, 400, 200, "left") == (0, 0, 200, 200)
    assert zone_rect(0, 0, 400, 200, "right") == (200, 0, 200, 200)
    assert zone_rect(0, 0, 400, 200, "above") == (0, 0, 400, 100)
    assert zone_rect(0, 0, 400, 200, "below") == (0, 100, 400, 100)
    assert zone_rect(0, 0, 400, 200, "center") == (0, 0, 400, 200)


def test_zone_rect_offsets_by_leaf_origin():
    assert zone_rect(100, 300, 400, 200, "below") == (100, 400, 400, 100)


def test_zone_rect_rejects_unknown_zone():
    with pytest.raises(ValueError):
        zone_rect(0, 0, 400, 200, "middle")
