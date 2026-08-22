# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The keybindings catalogue, turned into things GTK can act on.

keybindings.py knows the actions and the accelerator strings; this is the
half that needs a display: shortcut controllers for the window and the
editor, the application's accelerator table, and a matcher the terminals'
key handlers ask about a press (those handlers can't be GTK shortcuts — a
terminal's copy only fires when there is a selection, and the newline
chord feeds bytes to a child, so they stay hand-rolled and consult the
catalogue instead).
"""

from __future__ import annotations

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, Gtk  # noqa: E402

from . import keybindings  # noqa: E402

# The modifiers a binding is compared on. Locks and the layout-switch
# modifiers (Mod2+) are masked off so Caps Lock or a Num Lock'd keypad
# doesn't turn a chord into a stranger.
_MODIFIER_MASK = int(Gtk.accelerator_get_default_mod_mask())


def _parse(accelerator: str) -> tuple[int, int] | None:
    """(lower-cased keyval, modifiers) for *accelerator*, or None for a key
    name GDK doesn't know — the string layer validates the shape, only GDK
    knows the keysym table."""
    ok, keyval, mods = Gtk.accelerator_parse(accelerator)
    if not ok or keyval == 0:
        return None
    return Gdk.keyval_to_lower(keyval), int(mods) & _MODIFIER_MASK


def shortcut_controller(
    custom, prefix: str, phase: Gtk.PropagationPhase
) -> Gtk.ShortcutController:
    """A controller firing every `prefix.*` binding as a named action."""
    controller = Gtk.ShortcutController()
    controller.set_propagation_phase(phase)
    for action, accelerators in keybindings.resolve(custom).items():
        if not action.startswith(prefix + "."):
            continue
        for accelerator in accelerators:
            trigger = Gtk.ShortcutTrigger.parse_string(accelerator)
            if trigger is None:
                continue
            controller.add_shortcut(Gtk.Shortcut.new(trigger, Gtk.NamedAction.new(action)))
    return controller


def apply_app_accels(app: Gio.Application, custom) -> None:
    """Point every `app.*` binding at its accelerators — a rebinding
    replaces the old table entry, an unbinding empties it."""
    for action, accelerators in keybindings.resolve(custom).items():
        if action.startswith("app."):
            app.set_accels_for_action(action, list(accelerators))


class KeyMatcher:
    """Answers "is this press the chord for *action*?" for the handlers
    that read key events themselves. Built once per settings change, not
    per press: the parse is a table lookup per accelerator."""

    def __init__(self, custom) -> None:
        self._chords: dict[str, frozenset[tuple[int, int]]] = {}
        for action, accelerators in keybindings.resolve(custom).items():
            parsed = (_parse(a) for a in accelerators)
            self._chords[action] = frozenset(p for p in parsed if p is not None)

    @classmethod
    def from_settings(cls, settings: dict) -> KeyMatcher:
        return cls(settings.get(keybindings.SETTING))

    def matches(self, action: str, event: Gdk.KeyEvent) -> bool:
        """Whether *event* is one of *action*'s chords, by GDK's own rule —
        the one the window's shortcut triggers use — so Shift counts on a
        letter (Ctrl+Shift+C is not Ctrl+C) but not on a symbol it had to
        produce (Ctrl+Shift+= is the same press as Ctrl++)."""
        return any(
            event.matches(keyval, Gdk.ModifierType(mods)) == Gdk.KeyMatch.EXACT
            for keyval, mods in self._chords.get(action, ())
        )


def accelerator_for_press(event: Gdk.KeyEvent) -> str | None:
    """The accelerator string a key press would be saved as, or None for a
    press that isn't a binding on its own: a bare modifier, or a lock key.
    GDK supplies the keyval + modifiers that would match the press again
    (Shift folded into the symbol it produced, kept on a letter), and
    letters are lower-cased so `<Shift>T` and `<Shift>t` agree."""
    if _is_modifier(event.get_keyval()):
        return None
    matched, keyval, mods = event.get_match()
    if not matched or keyval == 0:
        return None
    name = Gtk.accelerator_name(Gdk.keyval_to_lower(keyval), Gdk.ModifierType(int(mods) & _MODIFIER_MASK))
    if not name:
        return None
    try:
        return keybindings.canonical(name)
    except keybindings.InvalidAccelerator:
        return None


_MODIFIER_KEYVALS = frozenset((
    Gdk.KEY_Shift_L, Gdk.KEY_Shift_R,
    Gdk.KEY_Control_L, Gdk.KEY_Control_R,
    Gdk.KEY_Alt_L, Gdk.KEY_Alt_R,
    Gdk.KEY_Super_L, Gdk.KEY_Super_R,
    Gdk.KEY_Meta_L, Gdk.KEY_Meta_R,
    Gdk.KEY_Hyper_L, Gdk.KEY_Hyper_R,
    Gdk.KEY_ISO_Level3_Shift, Gdk.KEY_ISO_Level5_Shift,
    Gdk.KEY_Caps_Lock, Gdk.KEY_Num_Lock, Gdk.KEY_Scroll_Lock,
))


def _is_modifier(keyval: int) -> bool:
    return keyval in _MODIFIER_KEYVALS
