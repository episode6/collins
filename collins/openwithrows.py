# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Menu rows that show an app's icon beside its name — the "Open In…"
submenus of the session sidebar (a folder to an app) and of the git
page's files list (a file to an app).

A menu model can't draw these: GtkModelButton takes an "icon" attribute
but only draws it when the item has no text, so a plain Gio.MenuItem would
silently drop the icon. Custom widgets slotted into the popover (the same
trick prmenu.py's list is built from) can show both. add_icon_row builds
the placeholder item and the widget together; the caller hands the
collected widgets to its popover under the slot names (slot_name) before
it pops up.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

# App icons in an "open in…" menu row: symbolic-icon sized, so a row is no
# taller than the plain menu items above and below it.
ICON_PX = 16

# Gap between that icon and its label. Not a round number because it is what
# lands the label on the text column Adwaita's own menu items use: the row
# sits among plain items, and a label two pixels off the ones above and
# below it is the sort of thing you see without seeing why.
ICON_GAP = 8

_SLOT_PREFIX = "open-with-"


def slot_name(index: int) -> str:
    """The popover child name of the *index*th row (add_child's id)."""
    return f"{_SLOT_PREFIX}{index}"


def open_with_row(icon: Gio.Icon | None, label: str, action: str, target: GLib.Variant) -> Gtk.Widget:
    """A menu row that shows an app's icon beside its name and activates
    *action* with *target* when clicked, popping its popover down."""
    image = Gtk.Image.new_from_gicon(icon or Gio.ThemedIcon.new("application-x-executable"))
    image.set_pixel_size(ICON_PX)
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=ICON_GAP)
    box.append(image)
    box.append(Gtk.Label(label=label, xalign=0.0, hexpand=True))

    button = Gtk.Button(child=box)
    button.add_css_class("flat")
    button.add_css_class("open-with-row")  # menu-sized, and lit under the pointer
    button.connect("clicked", _on_open_with_clicked, action, target)
    return button


def add_icon_row(
    section: Gio.Menu,
    rows: list[Gtk.Widget],
    icon: Gio.Icon | None,
    label: str,
    action: str,
    target: GLib.Variant,
) -> None:
    """Append an item to *section* that is drawn as an icon beside its label.

    The item is an empty placeholder naming a slot, and the widget filling
    that slot goes on *rows* for the popover to add under the same name
    (slot_them) — so the two are built together here, and every caller
    sharing one popover shares one *rows* list to keep the numbering
    straight.
    """
    item = Gio.MenuItem.new(None, None)
    item.set_attribute_value("custom", GLib.Variant("s", slot_name(len(rows))))
    section.append_item(item)
    rows.append(open_with_row(icon, label, action, target))


def slot_them(popover: Gtk.PopoverMenu, rows: list[Gtk.Widget]) -> None:
    """Put the rows add_icon_row collected into their slots of *popover*."""
    for index, widget in enumerate(rows):
        popover.add_child(widget, slot_name(index))


def _on_open_with_clicked(button: Gtk.Button, action: str, target: GLib.Variant) -> None:
    button.activate_action(action, target)
    popover = button.get_ancestor(Gtk.Popover)
    if popover is not None:
        popover.popdown()
