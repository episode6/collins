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

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

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


# What libspelling names every correction item, with the replacement word
# as both label and target (spelling-menu.c).
CORRECT_ACTION = "spelling.correct"


def corrections(view) -> list:
    """Every correction the adapter's menu is currently offering.

    Found by action name rather than by walking to a known position: the
    menu's shape differs between libspelling releases — its corrections
    section doesn't even implement iterate_item_links — while the action
    and the label-is-the-word convention are stable. Descends through both
    link kinds because which one holds the corrections has moved too.
    """
    found = []

    def walk(model) -> None:
        for i in range(model.get_n_items()):
            action = model.get_item_attribute_value(i, "action", None)
            if action is not None and action.get_string() == CORRECT_ACTION:
                label = model.get_item_attribute_value(i, "label", None)
                if label is not None:
                    found.append(label.get_string())
            for link in ("section", "submenu"):
                child = model.get_item_link(i, link)
                if child is not None:
                    walk(child)

    walk(view._adapter.get_menu_model())
    return found


def composer_has_spell_click(view) -> bool:
    """Whether the view carries the right-click spell-targeting gesture."""
    controllers = view._view.observe_controllers()
    for i in range(controllers.get_n_items()):
        controller = controllers.get_item(i)
        if (
            isinstance(controller, Gtk.GestureClick)
            and controller.get_button() == Gdk.BUTTON_SECONDARY
            and controller.get_propagation_phase() == Gtk.PropagationPhase.CAPTURE
        ):
            return True
    return False


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

    # libspelling 0.2 (Ubuntu 24.04, and so the runners) has no
    # update_corrections(), and without a synchronous rebuild moving the
    # caret would leave the menu stale rather than mis-aimed. ComposerView
    # installs no gesture there; check that degrade and stop.
    if not hasattr(view._adapter, "update_corrections"):
        check("libspelling 0.2: no gesture installed", not composer_has_spell_click(view))
        check(
            "…so a misspelling is still tagged, just not aimed at",
            view._buffer.get_iter_at_offset(TEXT.index("recieve") + 1).has_tag(
                view._adapter.get_tag()
            ),
        )
        print(f"\n{PASSED} passed, {FAILED} failed (libspelling 0.2 degrade path)")
        return 1 if FAILED else 0

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

    # Switched off (the composer_spell_click setting), a right-click is
    # inert again: the caret stays, the menu keeps whatever the caret's own
    # word gave it. This is the opt-out, so it has to be the *whole* of the
    # behaviour that goes away.
    view.set_spell_click(False)
    view._buffer.place_cursor(tail_iter)
    pump(600)
    parked = view._buffer.get_property("cursor-position")
    right_click(view, misspelled + 2)
    check(
        "switched off, clicking a misspelling moves nothing",
        view._buffer.get_property("cursor-position") == parked,
    )
    check("…and offers nothing", corrections(view) == [])

    # And back on again without rebuilding the view — the setting is read on
    # the click, so Preferences takes hold in a composer that is already open.
    view.set_spell_click(True)
    right_click(view, misspelled + 2)
    check("switched back on, it aims again", corrections(view)[:1] == ["receive"])

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
