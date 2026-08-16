#!/usr/bin/env python3
"""Wiring check for PanelDock layout capture/restore — run on a dev machine.

Exercises the GTK side of dock-layout persistence that the unit tests in
tests/test_panellayout.py (validation/prune/migration) and
tests/test_docktree.py (tree mutations) can't reach: building a multi-strip
dock out of real PanelStrips, serializing it with capture_layout, and
rebuilding it into a fresh dock with restore_layout — ordinals, scrollback
routing, home marker, hidden-strip semantics and all. It ends on the same
kind of check for `close_recent_page`, the walk out through the panel tabs
Ctrl+W makes before it reaches the session tab.

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


class FakePage(FakeShell):
    """The same stand-in, docked as a page rather than a shell — what
    `open_page` puts in a strip (a PR view, the attachments list)."""

    page_kind = "page"


class FakeTerminal(Gtk.Label):
    """The agent terminal at a width of our choosing: nothing is ever shown
    in this script, so no widget here is allocated. `open_page` asks the
    terminal how wide it is (see PanelDock._split_is_free), and get_width is
    a plain method a Python subclass can answer for."""

    def __init__(self, width=0):
        super().__init__(label="agent")
        self._width = width

    def get_width(self):
        return self._width


_WINDOWS = []  # keep roots alive for the run


def make_dock(home="bottom", terminal=None) -> PanelDock:
    """A dock wired the way TerminalTab wires the real one: strips spawn
    FakeShells numbered and ordinal'd by the dock."""
    terminal = terminal if terminal is not None else Gtk.Label(label="agent")
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


def check_close_recent() -> None:
    """Ctrl+W's first stop: `close_recent_page` walks out through the panel
    tabs one press at a time and only then answers False, which is the
    window's cue to close the session tab itself (win.close-tab)."""
    print("close-recent:")
    dock = make_dock()
    dock.show_home()
    home = dock.strips()[0]
    second = home.new_shell()
    drain()
    dock.split_move(home, second, dock._terminal, "right")
    drain()
    closed = dock.close_recent_page()
    drain()
    check(
        "closes the last-touched tab, leaving the other",
        closed and second not in dock.pages() and len(dock.pages()) == 1,
        dock.pages(),
    )
    closed = dock.close_recent_page()
    drain()
    check("the next press takes the tab behind it", closed and dock.pages() == [], dock.pages())
    check("an emptied dock keeps no strips", dock.strips() == [], dock.strips())
    check("nothing on show answers False", dock.close_recent_page() is False)

    hidden = make_dock()
    hidden.show_home()
    drain()
    hidden.hide_home()
    drain()
    check(
        "a hidden home strip is not closed unseen",
        hidden.close_recent_page() is False and len(hidden.pages()) == 1,
        hidden.pages(),
    )

    maxed = make_dock()
    maxed.show_home()
    drain()
    strip = maxed.strips()[0]
    maxed.maximize_page(strip, strip.selected_page_widget())
    drain()
    closed = maxed.close_recent_page()
    drain()
    check(
        "a maximized page comes down and closes",
        closed and maxed.maximized_page is None and maxed.pages() == [],
        (maxed.maximized_page, maxed.pages()),
    )


def open_page_dock(width: int, max_width: int, seed: int = 400, home="bottom") -> PanelDock:
    """A dock whose terminal is *width* px across and stops growing at
    *max_width*, with *seed* px as the app-wide size for docked-page strips
    (0 = never sized, so a new strip opens at the default fraction)."""
    dock = make_dock(home=home, terminal=FakeTerminal(width))
    dock.apply_settings({"terminal_max_width": max_width})
    dock.set_size_lookup(lambda _scope, _mode, s=seed: s)
    return dock


def check_open_page_splits() -> None:
    """open_page's join-don't-split rule yielding to spare width: past the
    maximum terminal width the terminal is sitting on gutter it will never
    use, so a second page opens in a column of its own rather than as
    another tab in the first one's strip."""
    print("open-page splits:")
    wide = open_page_dock(2400, 1200)  # 1200 px of gutter, 408 of it needed
    wide.open_page(FakePage(1))
    drain()
    second = FakePage(2)
    wide.open_page(second)
    drain()
    check(
        "a terminal past its maximum gives the second page a column",
        len(wide.strips()) == 2,
        wide.strips(),
    )
    check(
        "the new column lands beside the terminal",
        wide._strip_past_terminal("h") is wide._strip_of(second),
    )

    tight = open_page_dock(1400, 1200)  # 200 px of gutter: not enough
    tight.open_page(FakePage(1))
    drain()
    tight.open_page(FakePage(2))
    drain()
    check(
        "a terminal with no room to spare takes the tab",
        len(tight.strips()) == 1 and tight.strips()[0].page_count == 2,
        tight.strips(),
    )

    unlimited = open_page_dock(4000, 0)  # no maximum: the terminal uses it all
    unlimited.open_page(FakePage(1))
    drain()
    unlimited.open_page(FakePage(2))
    drain()
    check(
        "no maximum width means no free room",
        len(unlimited.strips()) == 1 and unlimited.strips()[0].page_count == 2,
        unlimited.strips(),
    )

    # Nothing ever sized: the column opens at the default fraction of the
    # terminal, which is what has to fit in the gutter.
    unsized = open_page_dock(2400, 1200, seed=0)
    unsized.open_page(FakePage(1))
    drain()
    unsized.open_page(FakePage(2))
    drain()
    check(
        "an unsized column is measured at its real width",
        len(unsized.strips()) == 2,
        unsized.strips(),
    )
    unsized_tight = open_page_dock(1800, 1200, seed=0)
    unsized_tight.open_page(FakePage(1))
    drain()
    unsized_tight.open_page(FakePage(2))
    drain()
    check(
        "...and refused when that width won't fit",
        len(unsized_tight.strips()) == 1 and unsized_tight.strips()[0].page_count == 2,
        unsized_tight.strips(),
    )

    # A right-docked home strip is a strip on that axis like any other: with
    # room to spare the page takes its own column rather than a seat in the
    # shells' tab row, and the panel Ctrl+J toggles is left as it was — same
    # pages, same home role, same divider against the terminal.
    right_home = open_page_dock(2400, 1200, home="right")
    right_home.show_home()
    drain()
    home_strip = right_home._home_strip
    page = FakePage(1)
    right_home.open_page(page)
    drain()
    check(
        "a right-docked panel isn't joined when there's room beside it",
        len(right_home.strips()) == 2 and right_home._strip_of(page) is not home_strip,
        right_home.strips(),
    )
    check(
        "...and keeps its shells, its home role and its divider",
        right_home._home_strip is home_strip
        and len(home_strip.shell_pages()) == 1
        and right_home._home_rec() is not None,
        (right_home._home_strip, home_strip.shell_pages()),
    )
    tight_home = open_page_dock(1400, 1200, home="right")
    tight_home.show_home()
    drain()
    tight_home.open_page(FakePage(1))
    drain()
    check(
        "with no room it joins the panel as before",
        len(tight_home.strips()) == 1 and tight_home.strips()[0] is tight_home._home_strip,
        tight_home.strips(),
    )

    below = open_page_dock(2400, 1200)
    below.open_page(FakePage(1), side="below")
    drain()
    below.open_page(FakePage(2), side="below")
    drain()
    check(
        "spare width doesn't split the bottom axis",
        len(below.strips()) == 1 and below.strips()[0].page_count == 2,
        below.strips(),
    )


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
    check(
        "restored tab titles stay unique",
        sorted(shell.number for shell in shells) == [1, 2],
        [shell.number for shell in shells],
    )
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

    check_open_page_splits()
    check_close_recent()

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
