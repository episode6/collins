"""Tests for the preferences search bar's GTK-free matcher (collins.prefssearch)."""

from collins.prefssearch import matches


def test_empty_query_matches_everything():
    assert matches("", "Scrollback lines")
    assert matches("   ", "Scrollback lines")
    assert matches("", "")


def test_substring_match_is_unanchored():
    assert matches("back", "Scrollback lines")
    assert not matches("backs", "Scrollback lines")


def test_match_is_case_insensitive():
    assert matches("DRACULA", "Dracula")
    assert matches("dracula", "DRACULA")


def test_every_word_has_to_appear():
    text = "Auto-generate session titles"
    assert matches("session titles", text)
    assert not matches("session prompts", text)


def test_words_may_appear_in_any_order():
    text = "Auto-generate session titles"
    assert matches("titles session", text)
    assert matches("titles auto", text)


def test_words_need_not_be_adjacent():
    assert matches("start worktree", "Start new sessions in a git worktree")


def test_punctuation_in_the_query_is_matched_literally():
    # "Ctrl+C" is how the setting spells it, so that is what has to match.
    assert matches("ctrl+c", "Ctrl+C copies selected text")
    assert not matches("ctrl+x", "Ctrl+C copies selected text")
