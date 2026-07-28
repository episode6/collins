"""Click behaviour for the footer labels: copy the text (working directory,
branch), or open a link (pull request)."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from .formatting import display_path  # noqa: E402
from .i18n import _  # noqa: E402

_FLASH_MS = 1200  # how long the "Copied" confirmation replaces the path


def copy_tooltip(text: str) -> str:
    """Tooltip for a copyable label: the full text plus the click hint."""
    return text + "\n" + _("Click to copy")


def open_tooltip(text: str) -> str:
    """Tooltip for a label that opens a link: the detail plus the click hint."""
    return text + "\n" + _("Click to open")


def enable_open_on_click(label: Gtk.Label, get_uri: Callable[[], str | None]) -> None:
    """Open the label's URI in the user's browser when it is clicked.

    The label's own text is usually a short stand-in for the link (a PR
    number, say), so the URI comes from `get_uri` rather than the label.
    """
    label.set_cursor(Gdk.Cursor.new_from_name("pointer"))

    def on_launched(launcher: Gtk.UriLauncher, result) -> None:
        try:
            launcher.launch_finish(result)
        except GLib.Error:
            pass  # no browser, or the user dismissed the chooser

    def on_released(_gesture, _n_press, _x, _y) -> None:
        uri = get_uri()
        if not uri:
            return
        Gtk.UriLauncher.new(uri).launch(label.get_root(), None, on_launched)

    click = Gtk.GestureClick()
    click.connect("released", on_released)
    label.add_controller(click)


def enable_copy_on_click(
    label: Gtk.Label,
    get_text: Callable[[], str | None],
    format_text: Callable[[str], str] = display_path,
) -> None:
    """Copy the label's full text to the clipboard when it is clicked.

    The label briefly shows a confirmation, then restores itself from
    `get_text` rendered through `format_text` (re-read at restore time, so a
    value that changed mid-flash comes back current).
    """
    label.set_cursor(Gdk.Cursor.new_from_name("pointer"))
    flash_source: list[int] = []

    def restore() -> bool:
        flash_source.clear()
        text = get_text()
        label.set_text(format_text(text) if text else "")
        return GLib.SOURCE_REMOVE

    def on_released(_gesture, _n_press, _x, _y) -> None:
        text = get_text()
        if not text:
            return
        label.get_clipboard().set(text)
        label.set_text(_("Copied to clipboard"))
        if flash_source:
            GLib.source_remove(flash_source.pop())
        flash_source.append(GLib.timeout_add(_FLASH_MS, restore))

    click = Gtk.GestureClick()
    click.connect("released", on_released)
    label.add_controller(click)
