"""Click behaviour for anything standing in for text or a link: copy what it
says (the footer's working directory and branch), or open where it points (a
pull request, from a footer chip or a sidebar row's mark)."""

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


def open_uri(widget: Gtk.Widget, uri: str | None) -> None:
    """Open *uri* in the user's browser, from *widget*'s window. Blank: no-op.

    For anything already clickable in its own right — a button in a menu, say.
    Plain labels and boxes want `enable_open_on_click`, which is this plus the
    gesture and the cursor to advertise it.
    """
    if not uri:
        return

    def on_launched(launcher: Gtk.UriLauncher, result) -> None:
        try:
            launcher.launch_finish(result)
        except GLib.Error:
            pass  # no browser, or the user dismissed the chooser

    Gtk.UriLauncher.new(uri).launch(widget.get_root(), None, on_launched)


def enable_open_on_click(
    widget: Gtk.Widget,
    get_uri: Callable[[], str | None],
    button: int = Gdk.BUTTON_PRIMARY,
) -> None:
    """Open the widget's URI in the user's browser when *button* clicks it.

    What's on screen is usually a short stand-in for the link (a PR number and
    its state mark, say), so the URI comes from `get_uri` rather than the
    widget — and a chip built from several widgets can hand over the box, so
    every part of it opens the same link.

    One button only, never a default GtkGestureClick answering to all of them:
    a chip can put a different meaning on its other button (the footer's PR
    chips open their actions menu on the primary and the browser on the
    secondary), and both firing on one click would open the browser along
    with the menu.
    """
    widget.set_cursor(Gdk.Cursor.new_from_name("pointer"))

    def on_released(_gesture, _n_press, _x, _y) -> None:
        open_uri(widget, get_uri())

    click = Gtk.GestureClick(button=button)
    click.connect("released", on_released)
    widget.add_controller(click)


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
