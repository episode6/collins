"""Tests for the preferences search bar's GTK-free matcher (collins.prefssearch)."""

from collins import prefslayout
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
    text = "Session title model"
    assert matches("session title", text)
    assert not matches("session prompts", text)


def test_words_may_appear_in_any_order():
    text = "Session title model"
    assert matches("title session", text)
    assert matches("model title", text)


def test_words_need_not_be_adjacent():
    assert matches("start worktree", "Start new sessions in a git worktree")


def test_punctuation_in_the_query_is_matched_literally():
    # "Ctrl+C" is how the setting spells it, so that is what has to match.
    assert matches("ctrl+c", "Ctrl+C copies selected text")
    assert not matches("ctrl+x", "Ctrl+C copies selected text")


def test_the_notifications_group_answers_to_the_words_people_use():
    # The group's own search words (prefs.py hands them to the search bar
    # through _searchable): each of the spec's keywords finds the group.
    text = " ".join(prefslayout.NOTIFICATION_SEARCH_TERMS)
    for word in ("notification", "notify", "bell", "sound", "chime", "badge", "unread"):
        assert matches(word, text), word
    assert not matches("terminal", text)
