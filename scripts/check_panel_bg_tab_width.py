#!/usr/bin/env python3
"""E2E check: a page opened in an unshown dock is sized when the dock shows.

A panel page can open itself into a background tab's dock — the agent
attaching its pull request while another session's tab is selected. An
unselected tab's widgets are unmapped and measure 0, so the fresh split's
PanedSizer.apply() used to exhaust its bounded allocation wait, give up
without ever setting a position, and GTK's natural-size layout squeezed
the new column to its minimum width when the tab was finally selected —
however much spare gutter the layout had. apply() now *parks* on the
paned's "map" signal instead and restarts the wait at first show (see
PanedSizer._park), and this check pins that behavior:

  - an open into an unmapped dock ends parked: the apply-pending gate
    still up, a map hook connected, no divider position invented;
  - a second apply while parked replaces the hook rather than stacking;
  - showing the dock lands the divider at the very width a foreground
    open would have chosen — here the spare-gutter floor, twice the
    page's declared column_floor (see panelsizing.spare_floor).

The unselected tab is played by a Gtk.Stack: like Adw.TabView, it maps
only its visible child, so the dock on the hidden page is exactly a
background tab's — present in the widget tree, unmapped, unallocated.

Run under a display (the e2e runner provides one):

    python3 scripts/check_panel_bg_tab_width.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk  # noqa: E402

Adw.init()

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
    """Enough PanelPage protocol for the strip and dock (see
    check_panel_layout.py, whose stand-in this copies)."""

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
        return False

    def apply_settings(self, settings):
        pass


class FakePage(FakeShell):
    """A PR-page stand-in: the 320px birth minimum, declared as the
    column_floor the dock doubles out of spare gutter."""

    page_kind = "page"
    column_floor = 320

    def __init__(self, number):
        super().__init__(number)
        self.set_size_request(320, -1)


def make_dock():
    terminal = Gtk.Label(label="agent", hexpand=True, vexpand=True)
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
    dock.set_size_lookup(lambda _scope, _mode: 500)
    # The terminal stops growing at 800px in a 1600px window, so the spare
    # gutter can pay a 320px page its doubled floor: 640px.
    dock.apply_settings({"terminal_max_width": 800})
    return dock


def wait(ms: int) -> None:
    done = []
    GLib.timeout_add(ms, lambda: (done.append(1), GLib.SOURCE_REMOVE)[1])
    ctx = GLib.MainContext.default()
    while not done:
        ctx.iteration(True)


def wait_until(what: str, cond, timeout_ms: int = 5000) -> bool:
    deadline = GLib.get_monotonic_time() + timeout_ms * 1000
    ctx = GLib.MainContext.default()
    while GLib.get_monotonic_time() < deadline:
        if cond():
            return True
        ctx.iteration(False)
    print(f"FAIL  timed out waiting for {what}")
    return False


def main() -> int:
    dock = make_dock()
    # The stack stands in for the tab view: only its visible child maps,
    # so the dock starts exactly as a background tab's would.
    stack = Gtk.Stack()
    front = Gtk.Label(label="the selected tab")
    stack.add_named(front, "front")
    stack.add_named(dock, "dock")
    stack.set_visible_child(front)
    window = Gtk.Window(default_width=1600, default_height=900)
    window.set_child(stack)
    window.present()

    if not wait_until("window allocation", lambda: front.get_width() > 0):
        return 1
    check("hidden dock is unmapped", not dock.get_mapped(), dock.get_mapped())

    page = FakePage(1)
    dock.open_page(page)
    rec = dock._page_rec("right")
    check("page split has a sizer", rec is not None)
    if rec is None:
        return 1
    paned, sizer = rec.paned, rec.sizer

    # Sit out the apply's bounded allocation window (10 x 50ms) with room
    # to spare: the paned never allocates, so the apply must end parked on
    # the paned's map — gate up, hook connected, no position invented.
    if not wait_until("the apply to park", lambda: sizer._park_handler != 0):
        return 1
    check("gate stays up while parked", sizer._apply_pending)
    check("hidden paned still has no extent", paned.get_width() == 0, paned.get_width())

    # A newer apply while parked replaces the hook rather than stacking.
    first_hook = sizer._park_handler
    sizer.apply()
    if not wait_until(
        "the second apply to park", lambda: sizer._park_handler not in (0, first_hook)
    ):
        return 1
    check("gate still up after re-park", sizer._apply_pending)

    # The tab is selected: the dock maps, the parked apply resumes, and the
    # column opens at the spare-gutter floor — 2 x 320 = 640 — not at the
    # page's minimum, which is where the pre-park give-up left it.
    stack.set_visible_child(dock)
    if not wait_until("paned allocation", lambda: paned.get_width() > 0):
        return 1
    if not wait_until("the resumed apply to settle", lambda: not sizer._apply_pending):
        return 1
    check("park hook is spent", sizer._park_handler == 0, sizer._park_handler)
    total = paned.get_width()
    check(
        "column opens at the doubled floor",
        paned.get_position() == total - 640,
        (paned.get_position(), total),
    )
    if not wait_until("page allocation", lambda: page.get_width() > 0):
        return 1
    check("page got the floor, not its minimum", page.get_width() >= 600, page.get_width())

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
