import pytest

from collins.panelkeys import escape_restores

ESCAPE = 0xFF1B
SHIFT = 1 << 0
LOCK = 1 << 1  # Caps Lock
CTRL = 1 << 2
ALT = 1 << 3  # Mod1
MOD5 = 1 << 7  # where a layout's AltGr often lands
SUPER = 1 << 26
HYPER = 1 << 27
META = 1 << 28

CHORDS = [SHIFT, CTRL, ALT, SUPER, HYPER, META, CTRL | SHIFT, CTRL | ALT]


class Page:
    """A page that answers holds_escape, and counts being asked."""

    def __init__(self, holds: bool) -> None:
        self.holds = holds
        self.asked = 0

    def __call__(self) -> bool:
        self.asked += 1
        return self.holds


def test_bare_escape_restores_a_page_that_wants_nothing():
    page = Page(False)
    assert escape_restores(ESCAPE, 0, page) is True
    assert page.asked == 1


def test_a_page_holding_escape_keeps_it():
    # A maximized shell with vim in it: the key belongs to vim.
    page = Page(True)
    assert escape_restores(ESCAPE, 0, page) is False


@pytest.mark.parametrize("state", CHORDS)
def test_modified_escape_is_somebody_elses_chord(state):
    page = Page(False)
    assert escape_restores(ESCAPE, state, page) is False
    assert page.asked == 0  # not even asked: it was never the restore key


@pytest.mark.parametrize("state", [LOCK, MOD5, LOCK | MOD5])
def test_lock_and_level_bits_still_restore(state):
    # Caps Lock on, or a layout parking AltGr in Mod5, is not a chord.
    assert escape_restores(ESCAPE, state, Page(False)) is True


@pytest.mark.parametrize(
    "keyval", [0x061, 0xFF08, 0xFF0D, 0x020, 0xFF09]  # a, BackSpace, Return, space, Tab
)
def test_other_keys_never_restore(keyval):
    page = Page(False)
    assert escape_restores(keyval, 0, page) is False
    assert escape_restores(keyval, CTRL, page) is False
    # Every keystroke in a maximized shell goes through here, and asking a
    # page whether it holds Escape can cost a syscall.
    assert page.asked == 0
