"""Click-to-copy behaviour for the footer working-directory labels."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from .formatting import display_path  # noqa: E402
from .i18n import _  # noqa: E402

_FLASH_MS = 1200  # how long the "Copied" confirmation replaces the path


def copy_tooltip(path: str) -> str:
    """Tooltip for a copyable path label: the full path plus the click hint."""
    return path + "\n" + _("Click to copy")


def enable_copy_on_click(label: Gtk.Label, get_path: Callable[[], str | None]) -> None:
    """Copy the full path to the clipboard when the label is clicked.

    The label briefly shows a confirmation, then restores itself from
    `get_path` (re-read at restore time, so a path that changed mid-flash
    comes back current).
    """
    label.set_cursor(Gdk.Cursor.new_from_name("pointer"))
    flash_source: list[int] = []

    def restore() -> bool:
        flash_source.clear()
        path = get_path()
        label.set_text(display_path(path) if path else "")
        return GLib.SOURCE_REMOVE

    def on_released(_gesture, _n_press, _x, _y) -> None:
        path = get_path()
        if not path:
            return
        label.get_clipboard().set(path)
        label.set_text(_("Copied to clipboard"))
        if flash_source:
            GLib.source_remove(flash_source.pop())
        flash_source.append(GLib.timeout_add(_FLASH_MS, restore))

    click = Gtk.GestureClick()
    click.connect("released", on_released)
    label.add_controller(click)
