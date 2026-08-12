# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Loading an image file as a paintable that animates if the file does.

Every place Collins puts a picture on screen — the lightbox, the editor's
image page, the inline images in a PR body — loaded its file with
`Gdk.Texture.new_from_filename`, which decodes exactly one frame. That is
the right answer for a PNG and the wrong one for the animated GIFs the
screenshot workflow produces to demo a change: the demo silently arrived as
a screenshot of its first frame. `load` is the drop-in replacement — a
`Gdk.Texture` for a still image, a self-driving `_Animation` paintable for a
multi-frame GIF — so a caller keeps handing whatever it gets to
`Gtk.Picture.new_for_paintable` and reading its intrinsic size, and gets the
animation for free.

GTK4 has no animation decoder of its own; gdk-pixbuf's `PixbufAnimation` is
the only one in the stack (deprecated, with nothing replacing it — the
warnings it raises are noted and deliberate). Only a `.gif` takes that path:
it is the one animated format the loader handles, and everything else is
better served by GdkTexture's own decoders.

The frame clock is the paintable's own, and it stops itself. A paintable
can't see whether its widget is mapped, on a hidden panel tab, or scrolled
out of view — but it can see whether anyone *drew* it since the last frame,
which is the same question one step later: `do_snapshot` marks the paintable
drawn, and a tick that finds it undrawn removes itself and leaves the next
snapshot to start the clock again. So an off-screen GIF costs one final
timeout and then nothing, and no call site has to wire up map/unmap.
"""

from __future__ import annotations

import logging
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, GObject  # noqa: E402

log = logging.getLogger(__name__)

# Browsers clamp absurdly fast GIF frames rather than obeying them (a 0ms
# delay is common, and honoring it would spin the main loop); 100ms for
# anything under 20ms is their long-standing convention, and it keeps a
# hostile file from turning into a busy loop.
_MIN_DELAY_MS = 20
_CLAMPED_DELAY_MS = 100


def load(path: str | Path) -> Gdk.Paintable | None:
    """The file at *path* as a paintable, animated if it is an animated GIF.

    None when it can't be decoded — every caller already has a "couldn't
    show this" path, and this function never raises into one.
    """
    path = Path(path)
    if path.suffix.lower() == ".gif":
        animation = _animation(path)
        if animation is not None:
            return animation
    try:
        return Gdk.Texture.new_from_filename(str(path))
    except GLib.Error:
        return None


def _animation(path: Path) -> _Animation | None:
    """An `_Animation` for a multi-frame GIF; None for anything else — a
    still GIF included, so it goes through GdkTexture's decoder like every
    other still image rather than through a one-frame animation."""
    try:
        animation = GdkPixbuf.PixbufAnimation.new_from_file(str(path))
    except GLib.Error:
        return None  # not decodable as an animation; the texture path may still be
    if animation.is_static_image():
        return None
    if animation.get_width() <= 0 or animation.get_height() <= 0:
        return None
    try:
        return _Animation(animation)
    except GLib.Error:
        log.debug("animatedimage: %s has no first frame", path, exc_info=True)
        return None


class _Animation(GObject.Object, Gdk.Paintable):
    """An animated GIF as a paintable whose contents change over time.

    Intrinsic size is the animation's and never changes (hence the SIZE
    flag), so a widget measuring it once measures it right; only the
    contents are invalidated, frame by frame.
    """

    __gtype_name__ = "CollinsAnimatedImage"

    def __init__(self, animation: GdkPixbuf.PixbufAnimation) -> None:
        super().__init__()
        self._iter = animation.get_iter(None)
        self._width = animation.get_width()
        self._height = animation.get_height()
        self._frame = self._texture()
        self._tick = 0  # the pending frame timeout, 0 when the clock is stopped
        self._drawn = False  # has anyone snapshotted us since the last tick?

    # -- Gdk.Paintable ------------------------------------------------------

    def do_snapshot(self, snapshot: Gdk.Snapshot, width: float, height: float) -> None:
        self._drawn = True
        if self._tick == 0:
            self._schedule()
        if self._frame is not None:
            self._frame.snapshot(snapshot, width, height)

    def do_get_intrinsic_width(self) -> int:
        return self._width

    def do_get_intrinsic_height(self) -> int:
        return self._height

    def do_get_intrinsic_aspect_ratio(self) -> float:
        return self._width / self._height

    def do_get_flags(self) -> Gdk.PaintableFlags:
        return Gdk.PaintableFlags.SIZE

    # -- the frame clock ----------------------------------------------------

    def _texture(self) -> Gdk.Texture | None:
        pixbuf = self._iter.get_pixbuf()
        return Gdk.Texture.new_for_pixbuf(pixbuf) if pixbuf is not None else self._frame

    def _schedule(self) -> None:
        """Arm the next frame. A negative delay is gdk-pixbuf saying this
        frame is the last one, which ends the clock for good."""
        delay = self._iter.get_delay_time()
        if delay < 0:
            return
        if delay < _MIN_DELAY_MS:
            delay = _CLAMPED_DELAY_MS
        self._tick = GLib.timeout_add(delay, self._advance)

    def _advance(self) -> bool:
        self._tick = 0
        if not self._drawn:
            # Nobody has drawn us since the last frame: the widget is
            # unmapped, on a background tab, or scrolled away. Stop; the
            # next snapshot starts the clock again.
            return GLib.SOURCE_REMOVE
        self._drawn = False
        self._iter.advance(None)
        self._frame = self._texture()
        self.invalidate_contents()
        self._schedule()  # per-frame delays differ, so each frame arms the next
        return GLib.SOURCE_REMOVE
