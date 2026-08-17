#!/usr/bin/env python3
"""E2E check: a divider drag during the apply/settle window still saves.

A docked page's PanedSizer guards remember() behind the apply-pending gate
so the clamps a fresh layout throws off never overwrite the remembered
size (see PanedSizer.apply). The gate used to swallow user drags with the
clamps: a divider grabbed while the panel was still settling — in practice
the first moments after a PR page opens, spinner still up — kept its
dragged size on screen but was never recorded, so it never became the
app-wide default. remember() now cedes the gate to a live drag exactly as
the settle checkpoints do, and this check pins all three sides of that:

  - a position change during the gate with no drag (a clamp) stays
    unrecorded — the original protection holds;
  - the same change with a live drag is recorded and emitted;
  - a plain resize after the gate records and emits as it always has;
  - and once more against a *live* apply()/settle chain, with the drag
    landing mid-settle — the timing the bug lived in — proving the cede
    also invalidates the queued checkpoints instead of being re-asserted
    over.

Real widgets in a real presented window (the sizer arithmetic needs the
paned allocated); the drag itself is the one thing simulated, by stubbing
_drag_active — synthesizing a real pointer grab headlessly isn't worth the
flake, and the settle checkpoints already trust the same probe.

Run under a display (the e2e runner provides one):

    python3 scripts/check_panel_resize_save.py
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


class FakePage(FakeShell):
    page_kind = "page"

    def __init__(self, number):
        super().__init__(number)
        self.set_size_request(320, -1)  # a PR page's birth minimum width


def make_dock(events):
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
    dock.connect(
        "size-changed",
        lambda _d, scope, key, size: events.append((scope, key, size)),
    )
    return dock, terminal


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
    events = []
    dock, _terminal = make_dock(events)
    window = Gtk.Window(default_width=1600, default_height=900)
    window.set_child(dock)
    window.present()

    if not wait_until("window allocation", lambda: dock.get_width() > 0):
        return 1

    dock.open_page(FakePage(1))
    rec = dock._page_rec("right")
    check("page split has a sizer", rec is not None)
    if rec is None:
        return 1
    paned, sizer = rec.paned, rec.sizer
    if not wait_until("paned allocation", lambda: paned.get_width() > 0):
        return 1
    # Let the open's own apply/settle chain finish before staging gates by
    # hand (its timeouts would otherwise fight the scenarios below). The
    # chain starts from an idle, which a starved main loop (CI's Xvfb) may
    # never dispatch — a gate still up after the wait is force-cancelled the
    # same way a superseding apply would cancel it, not a failure.
    wait_until("open's settle chain", lambda: not sizer._apply_pending, 2000)
    sizer._apply_seq += 1
    sizer._apply_pending = False
    wait(600)  # flush any pending size-changed debounce
    events.clear()

    total = paned.get_width()
    before = sizer.remembered("right")

    # 1. A clamp mid-gate: position moves, no drag — must stay unrecorded.
    sizer._apply_pending = True
    paned.set_position(total - 640)
    wait(600)
    check(
        "clamp during the gate is not recorded",
        sizer.remembered("right") == before and not events,
        (sizer.remembered("right"), events),
    )

    # 2. A drag mid-gate: same move with a live gesture — cedes and records.
    sizer._drag_active = lambda: True
    paned.set_position(total - 700)
    check("drag during the gate opens it", not sizer._apply_pending)
    check(
        "drag during the gate is recorded",
        sizer.remembered("right") == 700,
        sizer.remembered("right"),
    )
    wait(600)
    check(
        "drag during the gate emits size-changed",
        events == [("page", "right", 700)],
        events,
    )
    events.clear()

    # 3. A plain resize with the gate down still records and emits.
    sizer._drag_active = lambda: False
    paned.set_position(total - 750)
    wait(600)
    check(
        "resize after the gate emits size-changed",
        events == [("page", "right", 750)],
        events,
    )
    events.clear()

    # 4. The real chain: apply() runs for real and the drag lands mid-settle,
    #    the timing the bug lived in. Only the gesture probe is stubbed — the
    #    gate, the position() idle and the queued settle checkpoints are all
    #    live, so this also pins that the cede's seq bump invalidates them
    #    (a surviving checkpoint would re-assert the old target over the
    #    drag). ~80ms lands between the 50ms and 150ms checkpoints; even on a
    #    starved main loop the drag still meets the gate up and cedes, just
    #    against the allocation-wait phase instead of a settle.
    sizer.apply()
    wait(80)
    check("apply gate is up when the drag lands", sizer._apply_pending)
    sizer._drag_active = lambda: True
    paned.set_position(total - 680)
    sizer._drag_active = lambda: False
    check("mid-settle drag opens the gate", not sizer._apply_pending)
    check(
        "mid-settle drag is recorded",
        sizer.remembered("right") == 680,
        sizer.remembered("right"),
    )
    wait(600)
    check(
        "mid-settle drag emits size-changed",
        events == [("page", "right", 680)],
        events,
    )
    check(
        "no leftover settle re-asserts the old size",
        paned.get_position() == total - 680,
        (paned.get_position(), total),
    )

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
