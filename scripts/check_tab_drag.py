#!/usr/bin/env python3
"""Wiring check for per-tab panel drags — run on a dev machine.

Exercises the private-widget side of panel-page dragging that no pytest
test can reach (tests/conftest.py blocks the GTK stack so local runs
reproduce CI): the AdwTab walk that mounts a custom drag source on every
strip tab, the indicator-icon handle, re-wiring after a transfer, the
foreign-page gate, and the positional center-drop arithmetic against real
tab bounds.

    python3 scripts/check_tab_drag.py

What it can't exercise: the gesture-claim contest with AdwTabBox's own
drag (needs a pointer) — verify by hand that dragging a tab shows the
title chip and drop zones, never Adwaita's native tab drag.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk  # noqa: E402

Adw.init()

from collins import paneldnd  # noqa: E402
from collins.paneldock import PanelDock  # noqa: E402
from collins.panelstrip import PanelStrip  # noqa: E402

PASSED = 0
FAILED = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  ok  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}  {detail}")


class FakeShell(Gtk.Box):
    page_kind = "shell"

    __gsignals__ = {
        "shell-exited": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "bell": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, number):
        super().__init__()
        self.number = number
        self.hist = 0

    def page_title(self):
        return f"Terminal {self.number}"

    def page_icon(self):
        return None

    def grab_page_focus(self):
        pass

    def has_page_focus(self):
        return False

    def page_busy(self):
        return False

    def apply_settings(self, settings):
        pass

    def open_shell(self, cwd, restore_text=None):
        pass

    def capture_contents(self):
        return ""

    def clear(self):
        pass

    def page_state(self):
        return {"kind": "shell", "hist": self.hist}


_WINDOWS = []


def make_dock() -> PanelDock:
    terminal = Gtk.Label(label="agent")
    dock = PanelDock(terminal, None, "bottom")

    def make_shell():
        shell = FakeShell(dock.next_shell_number())
        shell.hist = dock.next_hist_ordinal()
        return shell

    def factory():
        strip = PanelStrip(shell_factory=make_shell)
        strip.set_cwd_lookup(lambda: None)
        return strip

    dock._strip_factory = factory
    window = Gtk.Window()
    window.set_default_size(900, 500)
    window.set_child(dock)
    _WINDOWS.append(window)
    return dock


def drain() -> None:
    ctx = GLib.MainContext.default()
    while ctx.pending():
        ctx.iteration(False)


def our_source(widget) -> Gtk.DragSource | None:
    controllers = widget.observe_controllers()
    for i in range(controllers.get_n_items()):
        c = controllers.get_item(i)
        if isinstance(c, Gtk.DragSource):
            return c
    return None


def _new_page_while_off_is_unwired(dock) -> bool:
    """A shell opened while the fallback is active must not get a handle."""
    dock2 = make_dock()
    dock2.apply_settings({"panel_tab_drag_handles": False})
    dock2.show_home()
    strip = dock2.strips()[0]
    drain()
    widget = strip.pages()[0]
    page = strip.tab_view.get_nth_page(0)
    tab = paneldnd._find_tab(strip.tab_bar, widget)
    return (
        page.get_indicator_icon() is None
        and (tab is None or our_source(tab) is None)
        and strip._grip.get_visible()
    )


def main() -> int:
    print("wiring:")
    dock = make_dock()
    _WINDOWS[-1].present()
    dock.show_home()
    home = dock.strips()[0]
    second = home.new_shell()
    drain()

    tabs = [paneldnd._find_tab(home.tab_bar, w) for w in home.pages()]
    check("AdwTab found for every page", all(t is not None for t in tabs), tabs)
    check(
        "drag source mounted on each tab",
        all(t is not None and our_source(t) is not None for t in tabs),
    )
    check(
        "handle indicator on each page",
        all(
            home.tab_view.get_nth_page(i).get_indicator_icon() is not None
            for i in range(home.page_count)
        ),
    )

    print("transfer re-wires:")
    dock.split_move(home, second, dock._terminal, "right")
    drain()
    other = [s for s in dock.strips() if s is not home][0]
    tab = paneldnd._find_tab(other.tab_bar, second)
    check("moved page's fresh tab is wired", tab is not None and our_source(tab) is not None)

    print("foreign pages stay unwired:")
    foreign = Gtk.Label(label="not a panel page")
    page = other.tab_view.append(foreign)
    drain()
    check("no indicator on a foreign page", page.get_indicator_icon() is None)
    ftab = paneldnd._find_tab(other.tab_bar, foreign)
    check(
        "no drag source on a foreign tab",
        ftab is None or our_source(ftab) is None,
    )
    other.tab_view.close_page(page)
    drain()

    print("reorder arithmetic:")
    a, b = home.pages()[0], home.new_shell(select=False)
    c = home.new_shell(select=False)
    drain()

    def order():
        return [w.number for w in home.pages()]

    start = order()
    home.reorder_to(a, 3)  # drop past everything: A goes last
    check("move right lands before the insert point", order() == start[1:] + [start[0]], order())
    home.reorder_to(a, 0)  # back to the front
    check("move left lands at the insert point", order() == start, order())
    home.reorder_to(c, 2)  # between the current first two? no — before itself: no-op zone
    check("reorder to own slot is a no-op", order() == start, order())

    print("fallback setting:")
    dock.apply_settings({"panel_tab_drag_handles": False})
    drain()
    off_tabs = [paneldnd._find_tab(home.tab_bar, w) for w in home.pages()]
    check(
        "sources unmounted when the setting turns off",
        all(t is not None and our_source(t) is None for t in off_tabs),
    )
    check(
        "indicators cleared",
        all(
            home.tab_view.get_nth_page(i).get_indicator_icon() is None
            for i in range(home.page_count)
        ),
    )
    check("fallback grip shown", home._grip.get_visible())
    grip_src = our_source(home._grip)
    check("grip carries a drag source", grip_src is not None)
    dock.apply_settings({"panel_tab_drag_handles": True})
    drain()
    on_tabs = [paneldnd._find_tab(home.tab_bar, w) for w in home.pages()]
    check(
        "sources remounted when it turns back on",
        all(t is not None and our_source(t) is not None for t in on_tabs),
    )
    check("grip hidden again", not home._grip.get_visible())
    check(
        "new pages while off stay unwired",
        _new_page_while_off_is_unwired(dock),
    )

    print("insert position from real bounds:")
    zones = dock._zones
    tab_a = paneldnd._find_tab(home.tab_bar, a)
    ok, bounds = tab_a.compute_bounds(zones)
    if not ok:
        check("tab bounds resolve against the overlay", False)
    else:
        left_of_a = bounds.get_x() - 5
        check(
            "drop left of the first tab inserts at 0",
            paneldnd.insert_position(home, zones, left_of_a) == 0,
            paneldnd.insert_position(home, zones, left_of_a),
        )
        check(
            "drop far right appends",
            paneldnd.insert_position(home, zones, 10_000) == home.page_count,
            paneldnd.insert_position(home, zones, 10_000),
        )

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
