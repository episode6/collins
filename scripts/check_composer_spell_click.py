#!/usr/bin/env python3
"""Behaviour check for the composer's right-click spell targeting.

libspelling builds its corrections from the word under the *insertion
cursor* and offers no way to aim them anywhere else, and GTK4's text view
pops its context menu without moving that cursor — so a right-click on a
squiggle lists corrections for wherever the caret was parked, usually
nothing. ComposerView._on_secondary_press moves the caret itself, gated on
libspelling's own misspelling tag so that a click on ordinary text still
changes nothing (the caret is where a Paste from that same menu lands).

Both halves of that gate are checked here: a click on a squiggle moves the
caret and refreshes the menu, a click on correctly-spelled text leaves the
buffer exactly as it was. Neither is reachable from the unit tests — the
corrections live in a GMenuModel the adapter rebuilds — nor from
check_composer_spelling_optional.py, which drives its views unrealized.
This one needs a realized window: hit-testing a click position means the
view has to have laid its text out.

    python3 scripts/check_composer_spell_click.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

import collins.composer as composer_mod  # noqa: E402

TEXT = "The quick brown fox recieve teh end"
PASSED = 0
FAILED = 0


def check(label: str, condition: bool) -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ok  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label}")


def pump(ms: int = 600) -> None:
    """Spin the main loop: libspelling tags and re-checks from idles."""
    context = GLib.MainContext.default()
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.002)


def corrections(view) -> list:
    """The labels in the adapter menu's corrections section."""
    menu = view._adapter.get_menu_model()
    outer = menu.iterate_item_links(0)
    outer.next()
    section = outer.get_value().iterate_item_links(0)
    section.next()
    model = section.get_value()
    out = []
    for i in range(model.get_n_items()):
        label = model.get_item_attribute_value(i, "label", None)
        out.append(label.get_string() if label else None)
    return out


def widget_xy(view, offset: int):
    """Widget coordinates of the character at *offset*, for a fake click."""
    location = view._view.get_iter_location(view._buffer.get_iter_at_offset(offset))
    return view._view.buffer_to_window_coords(
        Gtk.TextWindowType.WIDGET,
        location.x + location.width // 2,
        location.y + location.height // 2,
    )


def right_click(view, offset: int) -> None:
    x, y = widget_xy(view, offset)
    view._on_secondary_press(None, 1, float(x), float(y))


def main() -> int:
    Adw.init()

    view = composer_mod.ComposerView(
        pick_attach=lambda: None,
        file_reference=lambda path: path,
        notify=lambda message: None,
    )
    assert view._adapter is not None, "e2e environment is missing libspelling"

    window = Gtk.Window()
    window.set_default_size(700, 300)
    window.set_child(view)
    window.present()
    pump(800)

    view.set_text(TEXT)
    pump(700)

    tail = len(TEXT)  # after "end", spelled correctly: no corrections there

    # The squiggle is tagged, ordinary text is not — the gate the whole
    # behaviour hangs on.
    tag = view._adapter.get_tag()
    at = lambda word: view._buffer.get_iter_at_offset(TEXT.index(word) + 1)  # noqa: E731
    check("a misspelling carries the tag", at("recieve").has_tag(tag))
    check("correct text does not", not at("brown").has_tag(tag))

    # Park the caret on correctly-spelled text: this is the state a
    # right-click finds in practice, and the menu is empty in it.
    view._buffer.place_cursor(view._buffer.get_iter_at_offset(tail))
    pump(400)
    check("caret on a correct word offers nothing", corrections(view) == [])

    # Clicking a squiggle moves the caret there and fills the menu.
    right_click(view, TEXT.index("recieve") + 2)
    check(
        "clicking a misspelling moves the caret",
        view._buffer.get_property("cursor-position") != tail,
    )
    check("…and offers its corrections", corrections(view)[:1] == ["receive"])

    right_click(view, TEXT.index("teh") + 1)
    check("a second misspelling re-aims the menu", corrections(view)[:1] == ["the"])

    # Clicking ordinary text must change nothing at all: not the caret, not
    # the menu. This is the half that keeps Paste landing where it used to.
    view._buffer.place_cursor(view._buffer.get_iter_at_offset(tail))
    pump(400)
    before = view._buffer.get_property("cursor-position")
    right_click(view, TEXT.index("brown") + 1)
    check(
        "clicking correct text leaves the caret alone",
        view._buffer.get_property("cursor-position") == before,
    )
    check("…and offers nothing", corrections(view) == [])

    # libspelling lifts the squiggle off the word the caret sits in, so
    # that word reads as untagged and the handler leaves it alone — which
    # is right, its corrections come from that same caret and are already
    # the ones being asked for. Worth pinning: it is why every case below
    # parks the insert mark away from the word it is about to click.
    misspelled = TEXT.index("recieve")
    tail_iter = view._buffer.get_iter_at_offset(tail)
    view._buffer.place_cursor(view._buffer.get_iter_at_offset(misspelled + 2))
    pump(600)
    check(
        "the caret's own word loses its tag",
        not view._buffer.get_iter_at_offset(misspelled + 2).has_tag(tag),
    )
    view._buffer.place_cursor(tail_iter)
    pump(600)
    check(
        "…and gets it back when the caret leaves",
        view._buffer.get_iter_at_offset(misspelled + 2).has_tag(tag),
    )

    # A selection the click lands inside survives — right-clicking a
    # selection to copy it must not spend it on one word's spelling. The
    # insert mark goes to the far end so the word stays tagged and the
    # handler gets as far as the selection check at all.
    view._buffer.select_range(
        view._buffer.get_iter_at_offset(0),
        view._buffer.get_iter_at_offset(misspelled + len("recieve")),
    )
    pump(600)
    right_click(view, misspelled + 2)
    check("a right-click inside a selection keeps it", bool(view._buffer.get_selection_bounds()))

    # Outside it, the caret moves and the selection goes, as anywhere else.
    view._buffer.select_range(
        view._buffer.get_iter_at_offset(3),
        view._buffer.get_iter_at_offset(0),
    )
    pump(600)
    right_click(view, misspelled + 2)
    check("outside one, the click takes it", not view._buffer.get_selection_bounds())

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
