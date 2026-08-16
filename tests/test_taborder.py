from collins.taborder import neighbour_tab, tab_order


def reordered(row_ids, row_order):
    """The row ids in the order their tabs would end up in, which reads more
    like the tab bar than a list of indices does."""
    return [row_ids[i] for i in tab_order(row_ids, row_order)]


def test_tabs_follow_the_sidebar_top_to_bottom():
    assert reordered(["c", "a", "b"], ["a", "b", "c"]) == ["a", "b", "c"]


def test_an_already_ordered_bar_is_left_alone():
    assert tab_order(["a", "b", "c"], ["a", "b", "c"]) == [0, 1, 2]


def test_rows_without_a_tab_are_simply_skipped():
    assert reordered(["d", "b"], ["a", "b", "c", "d"]) == ["b", "d"]


def test_tabs_with_no_row_collect_at_the_right_end():
    # A replay and a chat tab (no sidebar row) among two session tabs.
    assert reordered([None, "b", None, "a"], ["a", "b"]) == ["a", "b", None, None]


def test_tabs_with_no_row_keep_their_order_among_themselves():
    order = tab_order([None, "a", None], ["a"])
    assert order == [1, 0, 2]  # the first rowless tab stays ahead of the second


def test_tabs_sharing_a_row_keep_their_order():
    # A fork is bound to the session it forked, so both tabs claim one row.
    assert tab_order(["a", "b", "a"], ["a", "b"]) == [0, 2, 1]


def test_every_tab_survives_the_sort():
    row_ids = ["e", None, "a", "c", None, "b"]
    assert sorted(tab_order(row_ids, ["a", "b", "c", "d", "e"])) == list(range(len(row_ids)))


def test_an_empty_sidebar_leaves_the_bar_as_it_is():
    assert tab_order(["a", "b"], []) == [0, 1]


def test_no_tabs_at_all():
    assert tab_order([], ["a", "b"]) == []


def test_the_screen_falls_to_the_next_tab_along():
    assert neighbour_tab(1, 3) == 2


def test_the_last_tab_falls_back_to_the_one_before_it():
    assert neighbour_tab(2, 3) == 1


def test_the_only_tab_has_nowhere_to_go():
    assert neighbour_tab(0, 1) is None


def test_a_neighbour_on_its_own_way_out_is_passed_over():
    # Tabs 2 and 3 are draining too: the screen falls back to tab 0 instead.
    assert neighbour_tab(1, 4, {1, 2, 3}) == 0


def test_every_other_tab_draining_leaves_nowhere_to_go():
    assert neighbour_tab(0, 3, {0, 1, 2}) is None
