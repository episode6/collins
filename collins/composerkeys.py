# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""What a key press means inside the composer's text box.

The composer sends on Enter by default, with a setting that swaps sending to
Ctrl+Enter (leaving bare Enter a newline, for people who write prompts like
GitHub comments). Shift+Enter is a newline in both modes -- it is the
terminal's own "literal newline" chord, so it must never send. Everything
about that decision is a pure function of the key, its modifiers and the
setting, so it lives here where the tests can reach it: CI has no GTK
typelibs, which is also why the keyvals and modifier bits below are spelled
as integers instead of Gdk constants (they are ABI, fixed by X11's keysymdef
and GDK's ModifierType, not values that drift).
"""

from __future__ import annotations

# GDK_KEY_Return, GDK_KEY_KP_Enter, GDK_KEY_ISO_Enter.
_RETURN_KEYVALS = frozenset({0xFF0D, 0xFF8D, 0xFE34})

# Gdk.ModifierType bit positions.
_SHIFT_MASK = 1 << 0
_CONTROL_MASK = 1 << 2

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


def restore_text(text: str) -> str:
    """The composer text as it should be typed back into the CLI's box.

    Interior newlines ride along fine -- a chunk fed all at once reads as a
    paste, and a paste's newlines are line breaks in the box -- but trailing
    ones are dropped: they would leave the cursor on an empty continuation
    row, one stray Enter away from submitting what the user chose to put
    back rather than send.
    """
    return text.rstrip("\n")
