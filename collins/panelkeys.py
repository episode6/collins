# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""What a key press means to a panel page floating over the whole session tab.

A page the tab row's overlay button has maximized (see paneldock) comes back
down on Escape — but only on a *bare* Escape, and only when the page itself
has no use for the key. Whether a given press is that Escape is a pure
function of the keyval, its modifiers and the page's answer, so it lives here
where the tests can reach it: CI has no GTK typelibs, which is also why the
keyval and modifier bits below are spelled as integers rather than Gdk
constants (they are ABI, fixed by X11's keysymdef and GDK's ModifierType,
not values that drift). Mirrors composerkeys, which does the same job for the
composer's text box.
"""

from __future__ import annotations

from collections.abc import Callable

_ESCAPE_KEYVAL = 0xFF1B  # GDK_KEY_Escape

# Gdk.ModifierType bit positions.
_SHIFT_MASK = 1 << 0
_CONTROL_MASK = 1 << 2
_ALT_MASK = 1 << 3  # Mod1
_SUPER_MASK = 1 << 26
_HYPER_MASK = 1 << 27
_META_MASK = 1 << 28

# Any modifier at all makes it somebody's chord rather than the plain key.
# Shift counts here, unlike in composerkeys: Shift is how a capital arrives,
# but there is no shifted Escape to arrive — Shift+Escape is only ever a
# binding, and the dock has none.
_CHORD_MASK = (
    _SHIFT_MASK | _CONTROL_MASK | _ALT_MASK | _SUPER_MASK | _HYPER_MASK | _META_MASK
)


def escape_restores(keyval: int, state: int, holds_escape: Callable[[], bool]) -> bool:
    """Whether this key press should put a maximized panel page back down.

    *holds_escape* answers whether the page wants the key for itself — a
    shell hands Escape to whatever owns its foreground, so vim and pagers
    keep working full-window. It is consulted *only* once the press is known
    to be a bare Escape, because answering it can cost a syscall and no
    ordinary keystroke should be paying for that.
    """
    if keyval != _ESCAPE_KEYVAL or state & _CHORD_MASK:
        return False
    return not holds_escape()
