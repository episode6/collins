#!/usr/bin/env python3
"""Wiring check for the composer's optional libspelling dependency.

Exercises both sides of composer.py's `Spelling = None` fallback that the
unit tests can't reach: with libspelling present a `ComposerView` wires the
spell-check adapter (extra menu + "spelling" action group), and with it
absent the view still constructs and moves text — a plain box, no adapter.

This is a script, not a pytest test, on purpose: tests/conftest.py blocks
the GTK-stack namespaces for the whole suite so local runs reproduce CI
(which installs python3-gi only — no gir packages, no display). Testing
widgets for real means running this by hand:

    python3 scripts/check_composer_spelling_optional.py

No window is ever shown; the views are driven unrealized.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

import collins.composer as composer_mod  # noqa: E402


def _build_view() -> composer_mod.ComposerView:
    return composer_mod.ComposerView(
        pick_attach=lambda: None,
        file_reference=lambda path: path,
        notify=lambda message: None,
    )


def _has_secondary_capture_click(widget) -> bool:
    """Whether *widget* carries the composer's right-click spell gesture."""
    controllers = widget.observe_controllers()
    for i in range(controllers.get_n_items()):
        controller = controllers.get_item(i)
        if (
            isinstance(controller, Gtk.GestureClick)
            and controller.get_button() == Gdk.BUTTON_SECONDARY
            and controller.get_propagation_phase() == Gtk.PropagationPhase.CAPTURE
        ):
            return True
    return False


def _check_text_roundtrip(view: composer_mod.ComposerView) -> None:
    view.set_text("teh composer stil works")
    assert view.peek_text() == "teh composer stil works", view.peek_text()
    assert view.take_text() == "teh composer stil works"
    assert view.peek_text() == ""


def main() -> int:
    Gtk.init()

    # The e2e stack installs libspelling (it is the common case), so the
    # real import must have found it — otherwise this check is silently
    # exercising only the fallback half.
    assert composer_mod.Spelling is not None, "e2e environment is missing libspelling"

    view = _build_view()
    assert view._adapter is not None
    # The extra menu must be the adapter's own model — GtkSource.View has a
    # default extra menu of its own, so non-None alone proves nothing.
    assert view._view.get_extra_menu() is view._adapter.get_menu_model()
    # The corrections menu is built from the insertion cursor, which GTK4
    # does not move on a right-click, so the view aims it itself from a
    # CAPTURE-phase secondary click (see ComposerView._on_secondary_press).
    # Without that gesture the menu still opens, just about the wrong word —
    # a silent failure no other check would catch.
    # Gated on libspelling 0.4's update_corrections(): without a synchronous
    # rebuild the composer deliberately installs no gesture (see
    # ComposerView.__init__), so 0.2 must show the opposite.
    if hasattr(view._adapter, "update_corrections"):
        assert _has_secondary_capture_click(view._view), "spell-click gesture missing"
    else:
        assert not _has_secondary_capture_click(view._view), (
            "libspelling 0.2 cannot refresh the menu in time; no gesture belongs here"
        )
    _check_text_roundtrip(view)
    print("with libspelling OK: adapter wired, its menu installed, text moves")

    # A machine without the typelib: composer.py's import fallback leaves
    # the module attribute None, and construction must skip the adapter
    # wiring entirely instead of crashing.
    real_spelling = composer_mod.Spelling
    composer_mod.Spelling = None
    try:
        bare = _build_view()
        assert bare._adapter is None, "fallback must leave _adapter None"
        # The view keeps GtkSource.View's stock extra menu; what must be
        # missing is the spelling adapter's model from the wired path above.
        assert bare._view.get_extra_menu() is not view._adapter.get_menu_model()
        # No adapter to aim, so no gesture either: the wiring lives inside
        # the same branch, and a stray one would be reaching for a None.
        assert not _has_secondary_capture_click(bare._view), (
            "fallback must not install the spell-click gesture"
        )
        _check_text_roundtrip(bare)
    finally:
        composer_mod.Spelling = real_spelling
    print("without libspelling OK: no adapter, no spelling menu, text still moves")

    # A machine can also have the typelib but not the shared library it
    # references (GitHub's runners shipped exactly that): the import
    # succeeds and the first real call raises GLib.Error. Same degrade.
    class _BrokenChecker:
        @staticmethod
        def get_default():
            raise GLib.Error("libspelling-1.so.1: cannot open shared object file")

    class _BrokenAdapter:
        # Resolved before the get_default() argument raises; never called.
        @staticmethod
        def new(buffer, checker):
            raise AssertionError("unreachable: get_default() raises first")

    class _BrokenSpelling:
        Checker = _BrokenChecker
        TextBufferAdapter = _BrokenAdapter

    composer_mod.Spelling = _BrokenSpelling
    try:
        broken = _build_view()
        assert broken._adapter is None, "broken lib must leave _adapter None"
        assert not _has_secondary_capture_click(broken._view), (
            "broken lib must not install the spell-click gesture"
        )
        _check_text_roundtrip(broken)
    finally:
        composer_mod.Spelling = real_spelling
    print("with a library-less typelib OK: degrades the same way")

    print("ALL COMPOSER SPELLING CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
