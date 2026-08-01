"""Tests for the quick-open dialog's GTK-free fuzzy matcher (collins.fuzzy)."""

from collins.fuzzy import match, rank

# -- match --------------------------------------------------------------------


def test_match_requires_subsequence():
    assert match("edt", "collins/editor.py") is not None
    assert match("zzz", "collins/editor.py") is None


def test_match_is_case_insensitive():
    assert match("readme", "README.md") is not None
    assert match("EDITOR", "collins/editor.py") is not None


def test_empty_query_matches_everything():
    assert match("", "a/b/c.py") is not None
    assert match("", "top.py") is not None


def test_empty_query_prefers_shallow_paths():
    assert match("", "top.py") > match("", "a/b/deep.py")


def test_basename_match_beats_directory_match():
    # Both contain "edit"; the one whose *file name* matches must win.
    assert match("edit", "collins/editor.py") > match("edit", "editing/notes.txt")


def test_name_start_beats_scattered_letters():
    assert match("fuzzy", "collins/fuzzy.py") > match("fuzzy", "for/us/lazy/zip/many.py")


def test_consecutive_run_beats_gaps():
    assert match("state", "collins/state.py") > match("state", "collins/stale_gate.py")


def test_segment_starts_reward_path_typing():
    # Typing path initials ("cf" for collins/fuzzy.py) is a common pattern.
    assert match("cf", "collins/fuzzy.py") is not None


def test_shallow_path_wins_ties():
    assert match("app", "app.py") > match("app", "x/y/app.py")


# -- rank ---------------------------------------------------------------------


def test_rank_orders_best_first_and_caps():
    paths = ["a/b/editor.py", "editor.py", "edict.txt", "unrelated.md"]
    ranked = rank("edit", paths, limit=2)
    assert len(ranked) == 2
    assert ranked[0] == "editor.py"


def test_rank_drops_non_matches():
    assert rank("zzz", ["a.py", "b.py"], limit=10) == []


def test_rank_empty_query_keeps_caller_order_on_equal_depth():
    paths = ["b.py", "a.py"]
    assert rank("", paths, limit=10) == ["b.py", "a.py"]
