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
    enter_action,
    restore_text,
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


@pytest.mark.parametrize("char", ["a", "Z", "7", " ", ".", "@", "é", "ß", "€", "字"])
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


@pytest.mark.parametrize("char", ["/", "!", "#"])
def test_typing_leaves_the_input_boxes_own_openers(char):
    # The CLI's slash-command, bash and memory modes start here, with
    # completion the composer can't offer — the keystroke belongs to the box.
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
