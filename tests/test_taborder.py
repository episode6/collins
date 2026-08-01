from collins.taborder import tab_order


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
