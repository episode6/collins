# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""A text box that grows with its text at once, not when GTK gets round to it.

`HeightHold` rides a `Gtk.TextView` sitting directly in a `Gtk.ScrolledWindow`
that propagates its natural height between a min and a max content height —
the composer's box, the PR page's comment and reply boxes. On every change to
the buffer it lays the text out with Pango at the view's width and holds the
scroller's minimum content height there, from a frame callback, until the
view's own measure reaches it (textheight.py says why the view lags: its
layout finishes on an idle the app's own landings starve). Then the minimum
goes back to the box's own and the view measures itself again, so shrinking,
scrolling past the cap and docking behave exactly as before.
"""

import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from . import textheight  # noqa: E402

# The text window is the view's width less a pixel kept for the cursor after
# a line's last character (GtkTextView's SPACE_FOR_CURSOR).
_SPACE_FOR_CURSOR = 1

# The view's wrap mode as Pango's; NONE (absent) lays each line out whole.
_PANGO_WRAP = {
    Gtk.WrapMode.CHAR: Pango.WrapMode.CHAR,
    Gtk.WrapMode.WORD: Pango.WrapMode.WORD,
    Gtk.WrapMode.WORD_CHAR: Pango.WrapMode.WORD_CHAR,
}


class HeightHold:
    """Keep *scroller* at the height *view*'s text needs. *lowest* is the
    scroller's own minimum content height, which a hold falls back to;
    *highest* its maximum. `set_enabled(False)` stands it down for a box
    that stops growing with its text (the composer docked, which fills its
    pane instead)."""

    def __init__(
        self, view: Gtk.TextView, scroller: Gtk.ScrolledWindow, lowest: int, highest: int
    ) -> None:
        self._view = view
        self._scroller = scroller
        self._lowest = lowest
        self._highest = highest
        self._enabled = True
        self._tick: int | None = None
        self._since = 0.0
        # The last estimate, keyed by what it was made of: a hold re-checks
        # every frame, and the text only changes on an edit.
        self._estimate: tuple[str, int, int] | None = None
        view.get_buffer().connect("changed", lambda *_a: self.hold())

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self.release()

    def hold(self) -> None:
        """Start (or restart) a hold: the next frame measures the text."""
        if not self._enabled:
            return
        self._since = time.monotonic()
        if self._tick is None:
            self._tick = self._view.add_tick_callback(self._on_tick)

    def release(self) -> None:
        if self._tick is not None:
            self._view.remove_tick_callback(self._tick)
            self._tick = None
        self._scroller.set_min_content_height(self._lowest)

    def _on_tick(self, view: Gtk.TextView, _clock) -> bool:
        if time.monotonic() - self._since > textheight.HOLD_S:
            self._tick = None
            self._scroller.set_min_content_height(self._lowest)
            return GLib.SOURCE_REMOVE
        width = view.get_width()
        if width <= 0:
            return GLib.SOURCE_CONTINUE  # not allocated yet: the next frame
        want = self._wanted_height(width)
        natural = view.measure(Gtk.Orientation.VERTICAL, width).natural
        if textheight.caught_up(natural, want, self._highest):
            self._tick = None
            self._scroller.set_min_content_height(self._lowest)
            return GLib.SOURCE_REMOVE
        floor = textheight.floor_for(want, self._lowest, self._highest)
        if self._scroller.get_min_content_height() != floor:
            self._scroller.set_min_content_height(floor)
        return GLib.SOURCE_CONTINUE

    def _wanted_height(self, width: int) -> int:
        buffer = self._view.get_buffer()
        found, end = buffer.get_iter_at_line(textheight.PREFIX_LINES)
        if not found:
            end = buffer.get_end_iter()
        text = textheight.measured_prefix(buffer.get_text(buffer.get_start_iter(), end, True))
        if self._estimate is not None and self._estimate[:2] == (text, width):
            return self._estimate[2]
        view = self._view
        wrap = width - view.get_left_margin() - view.get_right_margin() - _SPACE_FOR_CURSOR
        mode = _PANGO_WRAP.get(view.get_wrap_mode())
        # One layout per paragraph, each rounded to whole pixels, as the
        # view lays its buffer lines out; one layout of the whole text
        # rounds once and comes out a pixel short every few lines.
        layout = view.create_pango_layout("")
        if mode is not None:
            layout.set_width(max(1, wrap) * Pango.SCALE)
            layout.set_wrap(mode)
        height = view.get_top_margin() + view.get_bottom_margin()
        for paragraph in text.split("\n"):
            layout.set_text(paragraph, -1)
            height += layout.get_pixel_size()[1]
        self._estimate = (text, width, height)
        return height
