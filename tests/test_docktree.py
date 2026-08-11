# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Unit tests for the dock's split tree (docktree)."""

import pytest

from collins.docktree import DockTree, Leaf, Split


def test_fresh_tree_is_one_leaf():
    tree = DockTree("term")
    assert tree.leaves() == ["term"]
    assert len(tree) == 1
    assert "term" in tree
    assert isinstance(tree.root, Leaf)


def test_split_below_puts_new_leaf_in_end_slot():
    tree = DockTree("term")
    split = tree.split("term", "strip", "below")
    assert split.orientation == "v"
    assert split.a.value == "term"
    assert split.b.value == "strip"
    assert tree.root is split
    assert tree.leaves() == ["term", "strip"]


def test_split_right_is_horizontal_end_slot():
    tree = DockTree("term")
    split = tree.split("term", "strip", "right")
    assert split.orientation == "h"
    assert (split.a.value, split.b.value) == ("term", "strip")


def test_split_left_and_above_put_new_leaf_first():
    for side, orientation in (("left", "h"), ("above", "v")):
        tree = DockTree("term")
        split = tree.split("term", "strip", side)
        assert split.orientation == orientation
        assert (split.a.value, split.b.value) == ("strip", "term")


def test_split_a_nested_leaf_keeps_the_rest_intact():
    tree = DockTree("term")
    outer = tree.split("term", "s1", "below")
    inner = tree.split("s1", "s2", "right")
    assert tree.root is outer
    assert outer.b is inner
    assert inner.parent is outer
    assert tree.leaves() == ["term", "s1", "s2"]


def test_split_unknown_side_rejected():
    tree = DockTree("term")
    with pytest.raises(ValueError):
        tree.split("term", "strip", "diagonal")


def test_split_missing_value_rejected():
    tree = DockTree("term")
    with pytest.raises(ValueError):
        tree.split("ghost", "strip", "below")


def test_split_duplicate_value_rejected():
    tree = DockTree("term")
    tree.split("term", "strip", "below")
    with pytest.raises(ValueError):
        tree.split("term", "strip", "right")


def test_remove_promotes_sibling_to_root():
    tree = DockTree("term")
    split = tree.split("term", "strip", "below")
    dissolved, sibling = tree.remove("strip")
    assert dissolved is split
    assert sibling.value == "term"
    assert tree.root is sibling
    assert sibling.parent is None
    assert tree.leaves() == ["term"]


def test_remove_promotes_sibling_into_grandparent_slot():
    tree = DockTree("term")
    outer = tree.split("term", "s1", "below")
    tree.split("s1", "s2", "right")
    dissolved, sibling = tree.remove("s1")
    assert dissolved.orientation == "h"
    assert sibling.value == "s2"
    assert outer.b is sibling
    assert sibling.parent is outer
    assert tree.leaves() == ["term", "s2"]


def test_remove_promoted_subtree_keeps_structure():
    tree = DockTree("term")
    tree.split("term", "s1", "below")
    inner = tree.split("s1", "s2", "right")
    tree.remove("term")
    assert tree.root is inner
    assert inner.parent is None
    assert tree.leaves() == ["s1", "s2"]


def test_remove_last_leaf_rejected():
    tree = DockTree("term")
    with pytest.raises(ValueError):
        tree.remove("term")


def test_remove_missing_value_rejected():
    tree = DockTree("term")
    tree.split("term", "strip", "below")
    with pytest.raises(ValueError):
        tree.remove("ghost")


def test_leaves_are_in_spatial_order():
    tree = DockTree("term")
    tree.split("term", "s1", "below")
    tree.split("term", "s2", "left")
    assert tree.leaves() == ["s2", "term", "s1"]


def test_separator_orientation_is_how_two_leaves_are_laid_out():
    """What the panel's rotate button asks: is this strip stacked with the
    terminal, or beside it? Either side of the split answers the same."""
    tree = DockTree("term")
    below = tree.split("term", "s1", "below")
    beside = tree.split("term", "s2", "right")
    assert tree.separator_of("s1", "term") is below
    assert tree.separator_of("term", "s1") is below
    assert tree.separator_of("s2", "term").orientation == "h"
    assert beside.orientation == "h"


def test_separator_is_the_dividing_split_not_the_nearest_parent():
    """A tab split off inside the bottom strip inserts splits *under* the
    divider that sizes the panel against the terminal: both halves still
    answer "stacked", and both find that same outer divider."""
    tree = DockTree("term")
    outer = tree.split("term", "s1", "below")
    inner = tree.split("s1", "s2", "right")
    assert inner is not outer
    assert tree.separator_of("s1", "term") is outer
    assert tree.separator_of("s2", "term") is outer
    assert tree.separator_of("s2", "s1") is inner


def test_separator_of_unseparated_values_rejected():
    tree = DockTree("term")
    tree.split("term", "s1", "below")
    with pytest.raises(ValueError):
        tree.separator_of("s1", "ghost")  # not in the tree
    with pytest.raises(ValueError):
        tree.separator_of("s1", "s1")  # nothing divides a leaf from itself
    with pytest.raises(ValueError):
        tree.separator_of("ghost", "term")


def test_separator_finds_widget_values():
    """The branch scan compares values itself rather than going through
    `find`, so it has to match widgets the same way (see
    test_identity_match_beats_equality_for_widgets)."""

    class Widget:  # widgets compare by identity
        pass

    term, s1, s2 = Widget(), Widget(), Widget()
    tree = DockTree(term)
    below = tree.split(term, s1, "below")
    beside = tree.split(term, s2, "right")
    assert tree.separator_of(s1, term) is below
    assert tree.separator_of(s2, term) is beside
    assert tree.separator_of(s1, s2) is below  # s2 rides the terminal's branch


def test_next_leaf_cycles():
    tree = DockTree("term")
    tree.split("term", "s1", "below")
    tree.split("s1", "s2", "right")
    assert tree.next_leaf("term") == "s1"
    assert tree.next_leaf("s1") == "s2"
    assert tree.next_leaf("s2") == "term"


def test_next_leaf_single_leaf_returns_itself():
    tree = DockTree("term")
    assert tree.next_leaf("term") == "term"


def test_next_leaf_missing_value_rejected():
    tree = DockTree("term")
    with pytest.raises(ValueError):
        tree.next_leaf("ghost")


def test_slot_and_sibling_helpers():
    tree = DockTree("term")
    split = tree.split("term", "strip", "below")
    assert split.slot_of(split.a) == "a"
    assert split.slot_of(split.b) == "b"
    assert split.sibling_of(split.a) is split.b
    assert split.sibling_of(split.b) is split.a
    with pytest.raises(ValueError):
        split.slot_of(Leaf("other"))


def test_identity_match_beats_equality_for_widgets():
    class Widget:  # widgets compare by identity
        pass

    term, strip = Widget(), Widget()
    tree = DockTree(term)
    tree.split(term, strip, "below")
    assert tree.find(strip).value is strip
    assert tree.next_leaf(term) is strip


def test_split_nodes_are_split_instances():
    tree = DockTree("term")
    node = tree.split("term", "strip", "below")
    assert isinstance(node, Split)
