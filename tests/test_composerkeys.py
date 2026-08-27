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
    expand_pasted_back,
    paste_pieces,
    pasted_back,
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


def test_restore_text_reads_carriage_returns_as_newlines():
    assert restore_text("a\r\nb\rc\r\n") == "a\nb\nc"


# -- paste-back pieces ------------------------------------------------------
#
# The CLI folds a paste of more than 800 characters or more than two line
# breaks into a "[Pasted text #N +M lines]" stand-in; a piece has to stay
# under both.


def _lines(n: int, width: int = 10) -> str:
    return "\n".join(f"{i:0{width}d}" for i in range(n))


def test_paste_pieces_keeps_a_small_draft_whole():
    assert paste_pieces("one\ntwo\nthree") == ["one\ntwo\nthree"]
    assert paste_pieces("plain") == ["plain"]
    assert paste_pieces("") == []


def test_paste_pieces_concatenate_to_the_text():
    text = _lines(40, 30) + "\n" + "x" * 1500 + "\n\n\nend"
    assert "".join(paste_pieces(text)) == text


def test_paste_pieces_hold_at_most_two_line_breaks():
    pieces = paste_pieces(_lines(7))
    assert all(piece.count("\n") <= 2 for piece in pieces)
    # The break that would be a third opens the next piece instead.
    assert pieces[0] == "0000000000\n0000000001\n0000000002"
    assert pieces[1] == "\n0000000003\n0000000004"
    assert pieces[2] == "\n0000000005\n0000000006"


def test_paste_pieces_cut_a_long_line():
    pieces = paste_pieces("y" * 1000)
    assert [len(piece) for piece in pieces] == [400, 400, 200]
    assert all(len(piece) <= 400 for piece in paste_pieces("z" * 399 + "\n" + "z" * 399))


def test_paste_pieces_count_code_points_not_utf16_units():
    # 500 astral characters are 1000 UTF-16 units, past the CLI's 800; two
    # pieces of 400 code points are 800 units each at worst, which is not.
    pieces = paste_pieces("🙂" * 500)
    assert [len(piece) for piece in pieces] == [400, 100]


def test_paste_pieces_never_end_on_a_focus_report_tail():
    # The CLI trims a trailing "[I" / "[O" off every paste it receives.
    tail = "x" * 398 + "[I" + "rest"
    pieces = paste_pieces(tail)
    assert pieces[0] == "x" * 398 + "[" and pieces[1] == "Irest"
    assert paste_pieces("see [O") == ["see [", "O"]
    assert "".join(paste_pieces(tail)) == tail


# -- reading the paste-back off the screen ----------------------------------


def test_pasted_back_finds_every_piece_showing_in_full():
    pieces = ["one\ntwo\nthree", "\nfour"]
    assert pasted_back("one\ntwo\nthree\nfour", pieces) == {}


def test_pasted_back_forgives_the_read_its_spacing():
    # Row-end spaces dropped, a wrap's space guessed, a tab widened.
    pieces = ["alpha beta \n\tgamma", "\ndelta"]
    assert pasted_back("alpha beta\n    gamma\ndelta", pieces) == {}
    assert pasted_back("alpha  beta\ngamma delta", pieces) == {}


def test_pasted_back_maps_a_stand_in_to_its_piece():
    pieces = ["a\nb\nc", "\nd\ne"]
    screen = "[Pasted text #4 +2 lines][Pasted text #5 +2 lines]"
    assert pasted_back(screen, pieces) == {
        "[Pasted text #4 +2 lines]": "a\nb\nc",
        "[Pasted text #5 +2 lines]": "\nd\ne",
    }


def test_pasted_back_mixes_folded_and_showing_pieces():
    pieces = ["a\nb\nc", "\nd", "\ne\nf"]
    screen = "[Pasted text #1 +2 lines]\nd[Pasted text #2 +2 lines] typed after"
    assert pasted_back(screen, pieces) == {
        "[Pasted text #1 +2 lines]": "a\nb\nc",
        "[Pasted text #2 +2 lines]": "\ne\nf",
    }


def test_pasted_back_reads_a_wrapped_stand_in():
    pieces = ["a\nb\nc"]
    assert pasted_back("[Pasted text #12\n+2 lines]", pieces) == {
        "[Pasted text #12 +2 lines]": "a\nb\nc"
    }


def test_pasted_back_takes_a_one_line_piece_as_a_bare_stand_in():
    piece = "w" * 400
    assert pasted_back("[Pasted text #2]", [piece]) == {"[Pasted text #2]": piece}


def test_pasted_back_refuses_a_stand_in_of_the_wrong_size():
    # A stand-in folding a different number of lines is somebody else's.
    assert pasted_back("[Pasted text #1 +5 lines]", ["a\nb\nc"]) is None


def test_pasted_back_refuses_a_screen_that_isnt_the_pieces():
    assert pasted_back("something else", ["a\nb"]) is None
    assert pasted_back("[Image #1]", ["a\nb"]) is None
    assert pasted_back("", ["a"]) is None


# -- putting a stand-in back into the composer ------------------------------


def test_expand_pasted_back_puts_the_text_back():
    record = {"[Pasted text #1 +2 lines]": "a\nb\nc"}
    assert expand_pasted_back("[Pasted text #1 +2 lines] and more", record) == "a\nb\nc and more"
    assert expand_pasted_back("[Pasted text #1\n+2 lines]", record) == "a\nb\nc"


def test_expand_pasted_back_leaves_plain_text_alone():
    assert expand_pasted_back("nothing folded here", {}) == "nothing folded here"


@pytest.mark.parametrize(
    "screen",
    [
        "[Pasted text #7 +3 lines]",
        "[Pasted text #7]",
        "[Image #1]",
        "[Audio #2]",
        "[...Truncated text #3 +900 lines...]",
        "[Pasted text #1 +2 lines] then [Pasted text #9 +2 lines]",
    ],
)
def test_expand_pasted_back_refuses_a_stand_in_that_isnt_ours(screen):
    record = {"[Pasted text #1 +2 lines]": "a\nb\nc"}
    assert expand_pasted_back(screen, record) is None


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
