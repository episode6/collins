import pytest

from collins.composerkeys import (
    AUTOSHOW_MODES,
    DOCK,
    FLOAT,
    NEWLINE,
    OFF,
    PASS,
    SEND,
    autoshow_mode,
    draft_to_restore,
    enter_action,
    restore_text,
    spell_click_moves_caret,
    stashable_draft,
    typing_opens_composer,
)

RETURN = 0xFF0D
KP_ENTER = 0xFF8D
ISO_ENTER = 0xFE34
SHIFT = 1 << 0
CTRL = 1 << 2
# A modifier the decision must ignore (Alt/Mod1).
ALT = 1 << 3

SUPER = 1 << 26
LOCK = 1 << 1  # Caps Lock
MOD5 = 1 << 7  # where a layout's AltGr often lands

ENTERS = [RETURN, KP_ENTER, ISO_ENTER]


@pytest.mark.parametrize("keyval", ENTERS)
def test_enter_sends_mode(keyval):
    assert enter_action(keyval, 0, True) == SEND
    assert enter_action(keyval, SHIFT, True) == NEWLINE
    assert enter_action(keyval, CTRL, True) == NEWLINE


@pytest.mark.parametrize("keyval", ENTERS)
def test_ctrl_enter_sends_mode(keyval):
    assert enter_action(keyval, 0, False) == NEWLINE
    assert enter_action(keyval, SHIFT, False) == NEWLINE
    assert enter_action(keyval, CTRL, False) == SEND


@pytest.mark.parametrize("enter_sends", [True, False])
def test_shift_beats_ctrl(enter_sends):
    # Ctrl+Shift+Enter never sends: Shift is the terminal's own literal
    # newline chord and always wins.
    assert enter_action(RETURN, SHIFT | CTRL, enter_sends) == NEWLINE


@pytest.mark.parametrize("enter_sends", [True, False])
def test_other_modifiers_are_ignored(enter_sends):
    assert enter_action(RETURN, ALT, enter_sends) == enter_action(
        RETURN, 0, enter_sends
    )
    assert enter_action(RETURN, ALT | CTRL, enter_sends) == enter_action(
        RETURN, CTRL, enter_sends
    )


def test_non_return_keys_pass():
    for keyval in (0x061, 0xFF08, 0xFF1B, 0x020):  # a, BackSpace, Escape, space
        assert enter_action(keyval, 0, True) == PASS
        assert enter_action(keyval, CTRL, False) == PASS


@pytest.mark.parametrize("char", ["a", "Z", "7", " ", ".", "'", "é", "ß", "€", "字"])
def test_typing_opens_composer_on_characters(char):
    assert typing_opens_composer(char, 0)
    assert typing_opens_composer(char, SHIFT)  # a capital is still a character
    assert typing_opens_composer(char, LOCK)
    assert typing_opens_composer(char, MOD5)  # nor is AltGr a chord


@pytest.mark.parametrize("state", [CTRL, ALT, SUPER, CTRL | SHIFT, ALT | SHIFT])
def test_typing_leaves_chords_to_the_terminal(state):
    assert not typing_opens_composer("a", state)


@pytest.mark.parametrize("char", ["", "\r", "\n", "\t", "\x1b", "\x08", "\x7f"])
def test_typing_ignores_keys_that_are_not_characters(char):
    # Enter, Tab, Escape and Backspace produce a control code or nothing at
    # all; none of them is text, and the terminal keeps every one.
    assert not typing_opens_composer(char, 0)


@pytest.mark.parametrize("char", ["/", "!", "#", "@"])
def test_typing_leaves_the_input_boxes_own_openers(char):
    # The CLI's slash-command, bash, memory and file-picker modes start
    # here, with completion the composer can't offer — the keystroke
    # belongs to the box.
    assert not typing_opens_composer(char, 0)
    assert not typing_opens_composer(char, SHIFT)


def test_typing_takes_only_one_character_at_a_time():
    assert not typing_opens_composer("ab", 0)


def test_restore_text_strips_trailing_newlines():
    assert restore_text("hello\n") == "hello"
    assert restore_text("hello\n\n\n") == "hello"


def test_restore_text_keeps_interior_and_leading_newlines():
    assert restore_text("a\nb\nc") == "a\nb\nc"
    assert restore_text("\na\nb") == "\na\nb"


def test_restore_text_plain_and_empty():
    assert restore_text("hello") == "hello"
    assert restore_text("") == ""
    assert restore_text("\n\n") == ""
    # Trailing spaces are the user's; only newlines are a submit hazard.
    assert restore_text("hello \n") == "hello "


@pytest.mark.parametrize("mode", AUTOSHOW_MODES)
def test_autoshow_mode_keeps_known_values(mode):
    assert autoshow_mode(mode) == mode


def test_autoshow_modes_are_the_three_the_setting_offers():
    assert AUTOSHOW_MODES == (OFF, FLOAT, DOCK)


@pytest.mark.parametrize(
    "setting", [None, "", "on", True, False, 1, "Docked", "float ", ["dock"]]
)
def test_autoshow_mode_falls_back_to_off(setting):
    # Showing a composer is the opt-in half, so anything unreadable — a
    # missing setting, a hand-edited word, an older Collins's boolean —
    # must land on off rather than conjure one.
    assert autoshow_mode(setting) == OFF


def test_stashable_draft_keeps_real_text():
    assert stashable_draft("hello") == "hello"
    # Kept verbatim: the box's own spacing is the user's draft, not ours to
    # tidy — only the paste-back (restore_text) has a submit hazard to dodge.
    assert stashable_draft("  hello\n\n") == "  hello\n\n"


@pytest.mark.parametrize("text", ["", " ", "\n", "\n \t\n"])
def test_stashable_draft_drops_an_emptied_box(text):
    assert stashable_draft(text) == ""


def test_draft_to_restore_seeds_an_empty_box():
    assert draft_to_restore("draft", "") == "draft"
    assert draft_to_restore("draft", " \n") == "draft"


def test_draft_to_restore_leaves_a_written_box_alone():
    # Whatever is in there was written after the draft was set aside — a cut
    # CLI prompt, the keystroke that raised the composer — and outranks it.
    assert draft_to_restore("draft", "typed") == ""


def test_draft_to_restore_with_nothing_stashed():
    assert draft_to_restore("", "") == ""
    assert draft_to_restore("", "typed") == ""


def test_spell_click_moves_caret_without_a_selection():
    assert spell_click_moves_caret(0, None)
    assert spell_click_moves_caret(42, None)


def test_spell_click_keeps_a_selection_it_lands_in():
    assert not spell_click_moves_caret(5, (3, 8))
    # Boundaries count as inside: an edge click must not cost the selection.
    assert not spell_click_moves_caret(3, (3, 8))
    assert not spell_click_moves_caret(8, (3, 8))


def test_spell_click_moves_caret_outside_a_selection():
    assert spell_click_moves_caret(2, (3, 8))
    assert spell_click_moves_caret(9, (3, 8))


def test_spell_click_takes_selection_bounds_in_either_order():
    assert not spell_click_moves_caret(5, (8, 3))
    assert spell_click_moves_caret(9, (8, 3))


def test_spell_click_treats_an_empty_selection_as_none():
    assert spell_click_moves_caret(4, (4, 4))
