#!/usr/bin/env python3
"""Wiring check for the editor pane's one-column layout — run on a dev
machine.

Exercises the GTK side of `editor_narrow_width` that the unit tests in
tests/test_editorfiles.py (the `pane_layout` decision) can't reach: a real
EditorPane whose Adw.BreakpointBin judges the pane's own allocation, the
paned children it hides and shows, the back button beside the tabs, and the
setting arriving through apply_settings while the pane is on screen.

This is a script, not a pytest test, on purpose: tests/conftest.py blocks
the GTK-stack namespaces for the whole suite so local runs reproduce CI
(which installs python3-gi only — no gir packages, no display). Testing
widgets for real means running this by hand:

    python3 scripts/check_editor_narrow.py

A window is shown (an unmapped pane never allocates, and the breakpoint
only speaks from an allocation), so run it under a headless display to keep
it off your screen — CI's Xvfb, or
.agents/capture-screenshots/scripts/with-headless-display.sh locally. The
pane's width is set by an Adw.Clamp around it rather than by resizing the
window: a compositor may argue with a window about its size, never with a
clamp about its child's.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

from collins.editor import EditorPane  # noqa: E402

PASSED = 0
FAILED = 0

WIDE = 900
NARROW = 400
THRESHOLD = 500


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  ok  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}  {detail}")


def wait_until(what: str, cond, timeout_ms: int = 5000) -> bool:
    deadline = GLib.get_monotonic_time() + timeout_ms * 1000
    ctx = GLib.MainContext.default()
    while GLib.get_monotonic_time() < deadline:
        if cond():
            return True
        ctx.iteration(False)
    print(f"FAIL  timed out waiting for {what}")
    return False


def columns(pane: EditorPane) -> tuple[bool, bool, bool]:
    """(picker shown, file column shown, back button shown)."""
    return (
        pane._left.get_visible(),
        pane._editors.get_visible(),
        pane._back_btn.get_visible(),
    )


def main() -> int:
    root = tempfile.mkdtemp(prefix="collins-editor-narrow-")
    try:
        return run(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def run(root: str) -> int:
    first = os.path.join(root, "first.py")
    second = os.path.join(root, "second.txt")
    with open(first, "w") as fh:
        fh.write("print('hello')\n")
    with open(second, "w") as fh:
        fh.write("plain text\n")

    pane = EditorPane(root)
    pane.apply_settings({"editor_narrow_width": THRESHOLD})
    clamp = Adw.Clamp(maximum_size=WIDE, tightening_threshold=WIDE, child=pane)
    window = Gtk.Window(default_width=1200, default_height=800)
    window.set_child(clamp)
    window.present()

    def set_width(width: int) -> None:
        clamp.set_maximum_size(width)
        clamp.set_tightening_threshold(width)

    if not wait_until("pane allocation", lambda: pane.get_width() >= WIDE - 1):
        return 1
    check("wide pane shows both columns", columns(pane) == (True, True, False), columns(pane))
    check("wide pane is not narrow", not pane._narrow)
    paned = pane._breakpoint_bin.get_child()
    # The first-map idle re-asserts the tree's default width; let it land
    # so the position compared after the round trip is the settled one.
    if not wait_until("tree width settles", lambda: paned.get_position() > 0):
        return 1
    tree_width = paned.get_position()

    # -- narrow, nothing open: the picker alone ------------------------------
    set_width(NARROW)
    if not wait_until("breakpoint applies", lambda: pane._narrow):
        return 1
    check(
        "narrow pane with nothing open shows the picker", columns(pane) == (True, False, False), columns(pane)
    )

    # -- a file opens: the file column, with the back button -----------------
    pane.open_file(first)
    if not wait_until("file column shows", lambda: pane._editors.get_visible()):
        return 1
    check(
        "open file hides the picker, shows the back button",
        columns(pane) == (False, True, True),
        columns(pane),
    )
    check(
        "the tab bar's start slot holds the back button",
        pane._tab_bar.get_start_action_widget() is pane._back_btn,
    )
    selected = pane._tab_view.get_selected_page()
    check("the opened file is the selected tab", selected is not None and selected.get_title() == "first.py")

    # -- back: the picker again, with focus ----------------------------------
    pane._back_btn.emit("clicked")
    if not wait_until("picker shows again", lambda: pane._left.get_visible()):
        return 1
    check("back hides the file column", columns(pane) == (True, False, False), columns(pane))
    focus = window.get_focus()
    check("back focuses the tree", focus is not None and focus.is_ancestor(pane._tree), focus)
    check("the tab stays open behind the picker", pane._tab_view.get_n_pages() == 1)

    # -- re-opening an already open file also comes back to it ---------------
    pane.open_file(first)
    if not wait_until("file column shows for an open file", lambda: pane._editors.get_visible()):
        return 1
    check("existing page brings the file column back", columns(pane) == (False, True, True), columns(pane))

    # -- closing the last tab: nothing to show but the picker ----------------
    pane._tab_view.close_page(selected)
    if not wait_until("last tab closes to the picker", lambda: pane._left.get_visible()):
        return 1
    check(
        "no pages means the picker",
        pane._tab_view.get_n_pages() == 0 and columns(pane) == (True, False, False),
        columns(pane),
    )
    focus = window.get_focus()
    check("closing the last tab focuses the tree", focus is not None and focus.is_ancestor(pane._tree), focus)

    # -- back to wide: both columns, the tree width intact -------------------
    pane.open_file(first)
    pane.open_file(second)
    if not wait_until("two files open", lambda: pane._tab_view.get_n_pages() == 2):
        return 1
    set_width(WIDE)
    if not wait_until("breakpoint unapplies", lambda: not pane._narrow):
        return 1
    check("wide again shows both columns", columns(pane) == (True, True, False), columns(pane))
    if not wait_until("paned re-allocates", lambda: pane._left.get_width() > 0):
        return 1
    check(
        "tree width survived the round trip",
        paned.get_position() == tree_width,
        (paned.get_position(), tree_width),
    )

    # -- the setting, live: 0 never collapses; a threshold re-judges --------
    pane.apply_settings({"editor_narrow_width": 0})
    set_width(NARROW)
    if not wait_until("narrow allocation", lambda: pane.get_width() <= NARROW):
        return 1
    check(
        "threshold 0 keeps both columns when narrow",
        not pane._narrow and columns(pane) == (True, True, False),
        columns(pane),
    )
    pane.apply_settings({"editor_narrow_width": THRESHOLD})
    if not wait_until("a restored threshold collapses in place", lambda: pane._narrow):
        return 1
    check(
        "with files open the file column is what shows", columns(pane) == (False, True, True), columns(pane)
    )
    check("focus_default takes the file when it shows", pane._editors.get_visible())
    pane._back_btn.emit("clicked")
    pane.focus_default()
    focus = window.get_focus()
    check(
        "focus_default takes the tree while the picker shows",
        focus is not None and focus.is_ancestor(pane._tree),
        focus,
    )

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
