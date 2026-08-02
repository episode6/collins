"""Visual bell: flash a widget by pulsing the .bell-flash CSS class on it.

One flash is one run of the bell-flash animation in app.py's CSS; the class
is held slightly longer than the animation so the fade-out finishes before
the class comes off.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

# How long .bell-flash stays on a widget. Must outlast the CSS animation in
# app.py, which fades the flash out on its own. Public so a caller that
# replaces a mid-flash widget can tell whether a flash is still owed.
FLASH_MS = 450

# Widgets mid-flash, each with the timeout source that ends its flash.
_active: dict[Gtk.Widget, int] = {}


def flash(widget: Gtk.Widget) -> None:
    """Flash a widget once. A bell arriving mid-flash is folded into it —
    restarting the CSS animation would need a frame without the class, and
    one flash already tells the story."""
    if widget in _active:
        return

    def clear() -> bool:
        _active.pop(widget, None)
        widget.remove_css_class("bell-flash")
        return GLib.SOURCE_REMOVE

    widget.add_css_class("bell-flash")
    _active[widget] = GLib.timeout_add(FLASH_MS, clear)
