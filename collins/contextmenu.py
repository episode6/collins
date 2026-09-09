# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Popping a hand-built context menu at the pointer.

Every right-click menu in Collins is built the same way: a Gio.Menu made on
the spot, a Gtk.PopoverMenu over it, parented on the clicked widget and
pointed at the click. The one thing that must not happen there is popping
it up in the same main-loop iteration it was built in. A hand-parented
popover measures its surface once, at popup(), and GTK fills a sectioned
menu in two steps: the items land at once, the separators between the
sections only from an idle GtkMenuSectionBox schedules (its separator
sync). A popup() that runs before that idle sizes the surface without
the separators, and the menu scrolls by exactly their height — a
three-item menu with a scrollbar (the git page's files list, 2026-09-09).

popup_at parents and points the popover, then pops it from a zero timeout:
one iteration later, after the separator sync (both run at
GLib.PRIORITY_DEFAULT; the sync was queued first, so it dispatches first),
and never starved under CI's Xvfb the way a default-idle callback is.
The delay is a single main-loop turn — nothing a hand can notice. It also
unparents the popover once closed, from the main loop for the same reason.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402


def popup_at(
    popover: Gtk.Popover,
    parent: Gtk.Widget,
    x: float,
    y: float,
    *,
    halign: Gtk.Align | None = None,
) -> None:
    """Parent *popover* on *parent*, point it at (*x*, *y*) in the parent's
    coordinates, and pop it up on the next main-loop turn (see the module
    docstring for why not now). *halign* START keeps a wide menu from
    centering on the click. The popover unparents itself once closed."""
    popover.set_parent(parent)
    popover.set_has_arrow(False)
    if halign is not None:
        popover.set_halign(halign)
    rect = Gdk.Rectangle()
    rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
    popover.set_pointing_to(rect)
    popover.connect("closed", lambda p: GLib.idle_add(p.unparent, priority=GLib.PRIORITY_DEFAULT))

    def pop() -> bool:
        # The parent can go between the click and this turn (a row the
        # store just rebuilt): a popover with no root has nowhere to pop.
        if popover.get_parent() is not None and popover.get_root() is not None:
            popover.popup()
        return False

    GLib.timeout_add(0, pop)
