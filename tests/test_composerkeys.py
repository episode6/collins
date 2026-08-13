import pytest

from collins.composerkeys import NEWLINE, PASS, SEND, enter_action, restore_text

RETURN = 0xFF0D
KP_ENTER = 0xFF8D
ISO_ENTER = 0xFE34
SHIFT = 1 << 0
CTRL = 1 << 2
# A modifier the decision must ignore (Alt/Mod1).
ALT = 1 << 3

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
