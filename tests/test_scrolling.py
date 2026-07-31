# New in the ghackett fork of agent-session-manager (GPL-3.0).

from collins.scrolling import offset_into_view

PAGE = 400.0


def test_row_already_in_view_does_not_move_the_list():
    assert offset_into_view(row_y=120, row_height=40, value=100, page_size=PAGE) == 100


def test_row_flush_with_each_edge_counts_as_in_view():
    assert offset_into_view(row_y=100, row_height=40, value=100, page_size=PAGE) == 100
    assert offset_into_view(row_y=460, row_height=40, value=100, page_size=PAGE) == 100


def test_row_above_the_view_rises_to_the_top_edge():
    assert offset_into_view(row_y=60, row_height=40, value=100, page_size=PAGE) == 60


def test_row_below_the_view_moves_up_by_the_gap_only():
    # 20px of the row hang past the bottom, so the list moves exactly 20px.
    assert offset_into_view(row_y=480, row_height=40, value=100, page_size=PAGE) == 120


def test_row_far_below_the_view_lands_at_the_bottom_edge():
    assert offset_into_view(row_y=2000, row_height=40, value=0, page_size=PAGE) == 1640


def test_row_taller_than_the_view_aligns_to_its_top():
    assert offset_into_view(row_y=300, row_height=900, value=0, page_size=PAGE) == 300


def test_top_of_the_list_from_anywhere():
    assert offset_into_view(row_y=0, row_height=40, value=900, page_size=PAGE) == 0
