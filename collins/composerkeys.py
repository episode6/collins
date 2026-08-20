# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""What a key press means inside the composer's text box, what opens one,
and what a new session opens it as.

The composer sends on Enter by default, with a setting that swaps sending to
Ctrl+Enter (leaving bare Enter a newline, for people who write prompts like
GitHub comments). Shift+Enter is a newline in both modes -- it is the
terminal's own "literal newline" chord, so it must never send. Everything
about that decision is a pure function of the key, its modifiers and the
setting, so it lives here where the tests can reach it: CI has no GTK
typelibs, which is also why the keyvals and modifier bits below are spelled
as integers instead of Gdk constants (they are ABI, fixed by X11's keysymdef
and GDK's ModifierType, not values that drift).

The composer_new_sessions setting is here for the same reason: its three
words are shared by the preference row that writes them and the tab that
acts on them, and reading one back is likewise pure. So are the two rules
for the draft stash -- what a close keeps when it can't type the text back
into the CLI's box, and when a reopening composer is seeded with it.
"""

from __future__ import annotations

# GDK_KEY_Return, GDK_KEY_KP_Enter, GDK_KEY_ISO_Enter.
_RETURN_KEYVALS = frozenset({0xFF0D, 0xFF8D, 0xFE34})

# Gdk.ModifierType bit positions.
_SHIFT_MASK = 1 << 0
_CONTROL_MASK = 1 << 2
_ALT_MASK = 1 << 3  # Mod1
_SUPER_MASK = 1 << 26
_HYPER_MASK = 1 << 27
_META_MASK = 1 << 28

SEND = "send"
NEWLINE = "newline"
PASS = "pass"


def enter_action(keyval: int, state: int, enter_sends: bool) -> str:
    """``"send"``, ``"newline"`` or ``"pass"`` for a composer key press.

    Only Return-family keyvals answer anything but ``"pass"``. Shift+Enter
    is always a newline; with *enter_sends* a bare Enter sends and
    Ctrl+Enter is a newline escape hatch, without it those two swap.
    """
    if keyval not in _RETURN_KEYVALS:
        return PASS
    shift = bool(state & _SHIFT_MASK)
    ctrl = bool(state & _CONTROL_MASK)
    if shift:
        return NEWLINE
    if enter_sends:
        return NEWLINE if ctrl else SEND
    return SEND if ctrl else NEWLINE


# Modifiers that make a key a chord rather than a character. Shift and the
# lock/level bits are deliberately absent: Shift is how a capital arrives,
# and AltGr (Mod2..Mod5, by layout) is how half of Europe types one.
_CHORD_MASK = _CONTROL_MASK | _ALT_MASK | _SUPER_MASK | _HYPER_MASK | _META_MASK

# First characters the CLI's own input box hears as a mode switch, not as
# text: Claude Code opens its slash-command menu on "/", bash mode on "!",
# memory mode on "#" and its file picker on "@", each with completion the
# composer has no answer for. Typed into an empty box they belong to the
# box, so the composer leaves them there -- an opener is exactly the
# keystroke whose menu the user is after. Only as the first character:
# once the composer is up they are ordinary text in it, and a mention
# written there parses out of the submitted text just as well.
_PROMPT_OPENERS = frozenset("/!#@")


def typing_opens_composer(char: str, state: int) -> bool:
    """Whether typing *char* should raise the composer and take it along.

    The composer_on_typing setting's half of the decision that can be made
    from the keyboard alone (the other half is the screen: only an empty
    agent input box is ever typed away from -- see
    TerminalTab._typing_opens_composer). *char* is the character the key
    would produce, "" for a key that produces none.

    A character is anything the user could have meant as text: printable,
    unmodified but for Shift and the level shifts a layout needs. Chords
    belong to the terminal, and so do the box's own openers.
    """
    if len(char) != 1 or char < " " or char == "\x7f":
        return False
    if state & _CHORD_MASK:
        return False
    return char not in _PROMPT_OPENERS


# What a session Collins starts fresh opens its composer as (the
# composer_new_sessions setting; see TerminalTab.autoshow_composer).
OFF = "off"
FLOAT = "float"  # raised over the agent terminal, as Ctrl+. does
DOCK = "dock"  # its own panel page below the terminal
AUTOSHOW_MODES = (OFF, FLOAT, DOCK)


def autoshow_mode(setting) -> str:
    """The composer a new session should open with, read off a saved setting.

    Anything unrecognized -- a hand-edited settings file, a value some later
    Collins wrote and this one doesn't know -- reads as ``OFF``. Showing the
    composer is the opt-in half of this setting, so an answer we can't read
    must never conjure one.
    """
    return setting if setting in AUTOSHOW_MODES else OFF


def restore_text(text: str) -> str:
    """The composer text as it should be typed back into the CLI's box.

    Interior newlines ride along fine -- a chunk fed all at once reads as a
    paste, and a paste's newlines are line breaks in the box -- but trailing
    ones are dropped: they would leave the cursor on an empty continuation
    row, one stray Enter away from submitting what the user chose to put
    back rather than send.
    """
    return text.rstrip("\n")


def stashable_draft(text: str) -> str:
    """The composer text worth keeping when a close can't type it back.

    A close whose paste-back is refused -- the agent has left the terminal,
    where a pasted draft would be commands rather than a prompt -- hands the
    text here instead of dropping it (TerminalTab._stash_draft). Only text
    with something in it is kept: a box holding nothing but whitespace is a
    box the user emptied, and re-seeding that into the next composer would
    just be a stray space to delete.
    """
    return text if text.strip() else ""


def draft_to_restore(stashed: str, current: str) -> str:
    """The stashed draft a reopening composer should be seeded with, or "".

    A draft is only ever put back into a box with nothing in it: whatever is
    there now was written after the draft was set aside -- the CLI prompt an
    open cut out of the input box, the keystroke that raised the composer --
    and it must not be typed over. Whitespace alone counts as empty, the
    same way `stashable_draft` reads it.
    """
    return stashed if stashed and not current.strip() else ""
