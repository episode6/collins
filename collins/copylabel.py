"""Click behaviour for anything standing in for text or a link: copy what it
says (the footer's working directory and branch), or open where it points (a
pull request, from a menu row or the PR page's own header button) — with the
link itself a right-click away on the buttons that open one."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from .formatting import display_path  # noqa: E402
from .i18n import _  # noqa: E402

_FLASH_MS = 1200  # how long the "Copied" confirmation replaces the path
# What a button wears while that confirmation is its own face (see
# enable_copy_on_secondary_click) — the app's one "this worked" glyph.
_COPIED_ICON = "check-circle-fill-symbolic"


def copy_tooltip(text: str) -> str:
    """Tooltip for a copyable label: the full text plus the click hint."""
    return text + "\n" + _("Click to copy")


def open_tooltip(text: str) -> str:
    """Tooltip for a label that opens a link: the detail plus the click hint."""
    return text + "\n" + _("Click to open")


def copy_hint() -> str:
    """The extra tooltip line for a link button that also copies its link.

    A right-click nobody is told about is a right-click nobody finds — the
    same reason the PR page's action button names its own alternates.
    """
    return _("Right-click to copy the link")


def open_uri(widget: Gtk.Widget, uri: str | None) -> None:
    """Open *uri* in the user's browser, from *widget*'s window. Blank: no-op.

    For anything already clickable in its own right — a button in a menu, say,
    or a widget with a gesture of its own on the button that opens links.
    """
    if not uri:
        return

    def on_launched(launcher: Gtk.UriLauncher, result) -> None:
        try:
            launcher.launch_finish(result)
        except GLib.Error:
            pass  # no browser, or the user dismissed the chooser

    Gtk.UriLauncher.new(uri).launch(widget.get_root(), None, on_launched)


def enable_copy_on_secondary_click(
    button: Gtk.Button,
    get_text: Callable[[], str | None],
) -> None:
    """Copy what *button* points at to the clipboard on a right-click.

    For an icon button that opens a link on a plain click — the PR page's
    GitHub button. The URL is most of what such a page is wanted for away
    from Collins (pasted into a message, a terminal, another agent's
    prompt), and the button that opens it is already under the pointer.

    Confirmation is the button's own face: it wears a checkmark for the
    same beat a copyable label reads "Copied to clipboard", then goes back
    to the icon it was built with. A tooltip can't say it — the pointer
    that asked for the copy is holding one open — and these buttons sit
    nowhere near a toast overlay. `button` must therefore be an icon
    button; anything else has no face to borrow.

    GtkButton answers the primary button and only that, so this never
    doubles up with the click that opens the link.
    """
    icon = button.get_icon_name() or ""
    flash_source: list[int] = []

    def restore() -> bool:
        flash_source.clear()
        button.set_icon_name(icon)
        return GLib.SOURCE_REMOVE

    def on_pressed(gesture: Gtk.GestureClick, _n_press: int, _x: float, _y: float) -> None:
        text = get_text()
        if not text:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        button.get_clipboard().set(text)
        button.set_icon_name(_COPIED_ICON)
        if flash_source:
            GLib.source_remove(flash_source.pop())
        flash_source.append(GLib.timeout_add(_FLASH_MS, restore))

    secondary = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
    secondary.connect("pressed", on_pressed)
    button.add_controller(secondary)


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
