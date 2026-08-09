#!/usr/bin/env python3
"""Wiring check for PanelDock layout capture/restore — run on a dev machine.

Exercises the GTK side of dock-layout persistence that the unit tests in
tests/test_panellayout.py (validation/prune/migration) and
tests/test_docktree.py (tree mutations) can't reach: building a multi-strip
dock out of real PanelStrips, serializing it with capture_layout, and
rebuilding it into a fresh dock with restore_layout — ordinals, scrollback
routing, home marker, hidden-strip semantics and all.

This is a script, not a pytest test, on purpose: tests/conftest.py blocks
the GTK-stack namespaces for the whole suite so local runs reproduce CI
(which installs python3-gi only — no gir packages, no display). Testing
widgets for real means running this by hand:

    python3 scripts/check_panel_layout.py

No window is ever shown, and the shells are stand-ins (no VTE spawns, no
processes) — only the strip/dock plumbing is real.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk  # noqa: E402

Adw.init()

from collins import panellayout  # noqa: E402
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
    """A PanelPage of kind shell without the VTE: enough protocol for the
    strip and dock, plus the persistence hooks the real PanelTerminal has."""

    page_kind = "shell"

    __gsignals__ = {
        "shell-exited": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "bell": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, number):
        super().__init__()
        self.number = number
        self.hist = 0
        self.restored_text = None

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
        self.restored_text = restore_text

    def capture_contents(self):
        return f"scrollback-{self.hist}"

    def clear(self):
        pass

    def page_state(self):
        return {"kind": "shell", "hist": self.hist}


_WINDOWS = []  # keep roots alive for the run


def make_dock(home="bottom") -> PanelDock:
    """A dock wired the way TerminalTab wires the real one: strips spawn
    FakeShells numbered and ordinal'd by the dock."""
    terminal = Gtk.Label(label="agent")
    dock = PanelDock(terminal, None, home)

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
    window.set_child(dock)
    _WINDOWS.append(window)
    return dock


def drain() -> None:
    ctx = GLib.MainContext.default()
    while ctx.pending():
        ctx.iteration(False)


def split_layout():
    """A dock with the home strip below the terminal (2 shells, one moved
    to a satellite right of the terminal), and its captured layout."""
    dock = make_dock()
    dock.show_home()
    home = dock.strips()[0]
    second = home.new_shell()
    drain()
    dock.split_move(home, second, dock._terminal, "right")
    drain()
    check("split produced two strips", len(dock.strips()) == 2, dock.strips())
    return dock, dock.capture_layout()


def shape(node):
    """Structure fingerprint, ignoring sizes (nothing allocates headlessly)."""
    if "terminal" in node:
        return "terminal"
    if "strip" in node:
        strip = node["strip"]
        return (
            "strip",
            strip["open"],
            strip["home"],
            tuple((page["kind"], page["hist"]) for page in strip["pages"]),
        )
    return ("split", node["split"], node["managed"], shape(node["a"]), shape(node["b"]))


def main() -> int:
    print("capture:")
    dock, layout = split_layout()
    check("capture validates unchanged", panellayout.validate(layout) == layout, layout)
    check(
        "capture survives prune to shells",
        panellayout.prune(layout, {"shell"}) == layout,
    )
    check(
        "shell texts keyed by ordinal",
        dock.capture_shell_texts() == {0: "scrollback-0", 1: "scrollback-1"},
        dock.capture_shell_texts(),
    )
    check("never-used dock captures None", make_dock().capture_layout() is None)

    print("restore:")
    dock2 = make_dock()
    dock2.set_home_position(layout["mode"])
    dock2.seed_home_sizes(layout.get("sizes", {}))
    dock2.restore_layout(layout["tree"], {0: "one", 1: "two"})
    drain()
    check("two strips rebuilt", len(dock2.strips()) == 2, dock2.strips())
    shells = dock2.shell_pages()
    check(
        "hist ordinals restored",
        sorted(shell.hist for shell in shells) == [0, 1],
        [shell.hist for shell in shells],
    )
    check(
        "scrollback routed by ordinal",
        {shell.hist: shell.restored_text for shell in shells} == {0: "one", 1: "two"},
        {shell.hist: shell.restored_text for shell in shells},
    )
    check("home marker restored", dock2._home_strip is not None and dock2.home_visible)
    relayout = dock2.capture_layout()
    check(
        "recapture keeps the structure",
        shape(relayout["tree"]) == shape(layout["tree"]),
        (shape(relayout["tree"]), shape(layout["tree"])),
    )
    extra = dock2.strips()[0].new_shell()
    check("new ordinals continue after restored ones", extra.hist == 2, extra.hist)

    print("hidden home strip:")
    dock3 = make_dock()
    dock3.show_home()
    drain()
    dock3.hide_home()
    drain()
    hidden = dock3.capture_layout()
    node = hidden["tree"]["b"] if "strip" in hidden["tree"].get("b", {}) else hidden["tree"]["a"]
    check("hidden strip captures open=false", node["strip"]["open"] is False, hidden)
    dock4 = make_dock()
    dock4.restore_layout(hidden["tree"], {})
    drain()
    check(
        "restores hidden, shell running",
        dock4._home_strip is not None
        and not dock4.home_visible
        and len(dock4.shell_pages()) == 1,
    )
    dock4.show_home()
    check("Ctrl+J reveals it", dock4.home_visible)

    print("guards:")
    dock5 = make_dock()
    dock5.show_home()  # the user got there first
    drain()
    before = len(dock5.strips())
    dock5.restore_layout(layout["tree"], {})
    drain()
    check("restore refuses a dock already split", len(dock5.strips()) == before)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
