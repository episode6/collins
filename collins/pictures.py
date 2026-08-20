# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Images in a narrow column: the file behind a URL, and a picture that fits.

Two problems every panel that shows images inside a scrolling column runs
into, solved once here rather than once per panel — the pictures a PR body
embeds (`bodyimages`) and the ones a session has seen (`attachpanel`) both
draw from this.

**The file behind a URL.** `fetch` turns a remote image into a file on disk
once per run and hands every later caller the same one. The download goes
through `remoteimages` — the same capped, timeout-bounded, redirect-gated
fetch `show_image` uses, into the same pruned cache directory — on a worker
thread, landing back on the main loop. A caller whose images aren't at a
public URL passes its own fetcher instead (the PR view's file previews ask
`gh` for the blob: see prblobs) and keeps everything around it. Failures are remembered too: a URL
that 404s must not be re-fetched by every rebuild. A cached file that has
since been pruned (downloads live a day) counts as a miss rather than a hit,
so what a caller is handed is a file that is really there — which is what
lets a click pass it to the lightbox, or to another app.

**A picture that fits.** `Gtk.Picture` reports its paintable's size in both
directions and never relates them, so in a column narrower than the image it
asks for the full natural height and paints a scaled-down image floating in
the middle of the leftover space. `BoundedPicture` measures height-for-width
instead, and `thumbnail` decodes at the size the column will draw at, so a
gallery of screenshots costs a gallery's worth of memory rather than a
screenshot's worth each.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from . import animatedimage, remoteimages  # noqa: E402

log = logging.getLogger(__name__)

# The tallest an image renders inline by default; past it the picture is
# scaled down (never cropped) and the lightbox holds the full-size look.
MAX_HEIGHT = 400
# The narrowest an image is ever measured at, and so the floor its minimum
# height comes from (see BoundedPicture.do_measure). Narrower than any panel
# an image is readable in, which is the point: it is a bound, not a size
# anything actually gets.
_FLOOR_WIDTH = 120
# How many downloads may be in flight at once. A PR body may carry twenty
# screenshots, and a page opening shouldn't burst twenty sockets — the same
# reasoning as avatars._GATE, one size up because these are bigger.
_GATE = threading.Semaphore(3)

# url -> the file it was fetched to, or None when the fetch failed. Kept for
# the run, uncapped, unlike the decoded-picture cache in bodyimages: what is
# held here is a path and a message, and evicting one wouldn't free anything
# worth freeing — it would only make a URL already on disk download again.
# The files themselves are bounded by remoteimages' own 24h prune.
_files: dict[str, Path | None] = {}
_errors: dict[str, str] = {}  # url -> why it failed, for a fallback's tooltip
_waiting: dict[str, list[Callable[[Path | None, str | None], None]]] = {}


def fetch(
    url: str,
    on_ready: Callable[[Path | None, str | None], None],
    fetcher: Callable[[], Path] | None = None,
) -> None:
    """Hand *on_ready* the file *url* was downloaded into, or why it wasn't.

    Main thread only (it reads the caches). *on_ready* is called straight
    away for a URL already fetched this run — a caller showing a placeholder
    first should put it up before calling, so a cache hit replaces it in the
    same frame rather than flashing.

    *fetcher* replaces the download for a caller whose images don't come off
    a public URL: the PR view's file previews ask `gh` for a blob instead
    (see prblobs), and want the caching, the thread, the in-flight coalescing
    and the remembered failure all the same. *url* is then only a cache key —
    it is never parsed — so it has to name the bytes exactly (a commit, not a
    branch). The call is made on the worker thread and may raise; whatever it
    raises becomes the failure the caller is handed."""
    if url in _files:
        cached = _files[url]
        if cached is None or cached.exists():
            on_ready(cached, _errors.get(url))
            return
        # A cached path whose file has since been pruned is not a cache hit:
        # fetch it again rather than hand out a path that points at nothing.
        forget(url)
    if url in _waiting:
        _waiting[url].append(on_ready)
        return
    _waiting[url] = [on_ready]
    threading.Thread(
        target=_fetch, args=(url, fetcher), name="image-fetch", daemon=True
    ).start()


def forget(url: str) -> None:
    """Drop what is remembered about *url*, so the next `fetch` really fetches."""
    _files.pop(url, None)
    _errors.pop(url, None)


def _fetch(url: str, fetcher: Callable[[], Path] | None = None) -> None:
    path = error = None
    try:
        with _GATE:
            path = (
                fetcher()
                if fetcher is not None
                else remoteimages.fetch_to_file(url, remoteimages.default_directory())
            )
    except remoteimages.FetchError as failure:
        error = str(failure)
    except Exception as failure:  # a fetch must never take the thread with it
        error = str(failure)
        log.debug("pictures: fetching %s failed", url, exc_info=True)
    GLib.idle_add(_landed, url, path, error)


def _landed(url: str, path: Path | None, error: str | None) -> bool:
    _files[url] = path
    if error is not None:
        _errors[url] = error
    for on_ready in _waiting.pop(url, []):
        on_ready(path, error)
    return GLib.SOURCE_REMOVE


def thumbnail(
    path: str | Path, width: int, max_height: int = MAX_HEIGHT
) -> Gdk.Paintable | None:
    """The image at *path*, decoded no bigger than the box it is drawn in.

    `animatedimage.load` decodes at full size, which is the right answer for
    the one picture a lightbox shows and the wrong one for a gallery: a
    hundred screenshots held as full-resolution textures is hundreds of
    megabytes for a column that paints them a few hundred pixels wide. So
    anything that doesn't already fit in *width* x *max_height* is scaled
    while it is decoded — the full-size copy is never held at all — and
    anything that does fit is loaded as it is, since upscaling only makes it
    blurrier. Both bounds are needed: a tall narrow picture is inside the
    column's width and still decodes to megabytes nobody sees, because a
    preview that tall is drawn as a sliver (see BoundedPicture) either way.

    GIFs go through `animatedimage` whatever their size: an animation shown
    as a still is exactly the picture that misleads, and its clock stops
    itself whenever nobody is drawing it, so one scrolled out of view costs
    nothing. None when the file can't be decoded at all — every caller has a
    "couldn't show this" stand-in, and this never raises into one.
    """
    path = Path(path)
    if path.suffix.lower() == ".gif":
        return animatedimage.load(path)
    info = GdkPixbuf.Pixbuf.get_file_info(str(path))
    known = info is not None and info[0] is not None
    if not known or (info[1] <= width and info[2] <= max_height):
        # An unrecognized file lands here too: gdk-pixbuf doesn't know the
        # format, and GdkTexture's own decoders get their turn.
        return animatedimage.load(path)
    try:
        # Both bounds with preserve_aspect: gdk-pixbuf fits the picture
        # inside the box, so whichever dimension is the binding one decides.
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            str(path), width, max_height, True
        )
    except GLib.Error:
        return animatedimage.load(path)
    return Gdk.Texture.new_for_pixbuf(pixbuf)


class BoundedPicture(Gtk.Picture):
    """A picture that measures like an image in a column of text.

    `Gtk.Picture` reports its paintable's size in both directions and never
    relates them, so in a column narrower than the image it asks for the
    full natural height and paints a scaled-down image floating in the
    middle of the leftover space. Measuring height-for-width instead makes
    the widget exactly as tall as the width implies, and capping the natural
    width at the one that makes the image *max_height* tall keeps a tall
    screenshot from taking the whole panel.

    *width* is a width the content itself asked for (an `<img width=>`), and
    is a ceiling like the other one: never wider than asked for, and never
    wider than the picture actually is.
    """

    __gtype_name__ = "CollinsBoundedPicture"

    def __init__(
        self, paintable: Gdk.Paintable, width: int = 0, max_height: int = MAX_HEIGHT
    ) -> None:
        super().__init__()
        self.set_paintable(paintable)
        self.set_can_shrink(True)
        self.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.set_halign(Gtk.Align.START)
        self._width = max(1, paintable.get_intrinsic_width())
        self._height = max(1, paintable.get_intrinsic_height())
        self._cap = self._width
        if self._height > max_height:
            self._cap = max(1, round(max_height * self._width / self._height))
        if width:
            self._cap = min(self._cap, width)

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple:
        if orientation == Gtk.Orientation.HORIZONTAL:
            # Zero minimum width: a picture is the one thing in a card that
            # should give way rather than force the panel wider.
            return (0, self._cap, -1, -1)
        if for_size <= 0:
            # Nobody has said how wide yet. The natural height is the capped
            # image; the minimum is a true lower bound — the height at the
            # narrowest width worth showing one at. It has to be a bound that
            # holds at *every* width, or GTK warns each time a parent
            # measures this widget for the height it just worked out (which
            # is smaller than the height at the cap). The floor is itself
            # capped: a picture narrower than _FLOOR_WIDTH — a 22px icon in a
            # PR's file preview — is never drawn wider than it is, and a
            # minimum taken at a width it can't reach claims height nothing
            # will ever paint (GTK warns about that one too).
            floor = min(_FLOOR_WIDTH, self._cap)
            return (
                max(1, round(floor * self._height / self._width)),
                max(1, round(self._cap * self._height / self._width)),
                -1,
                -1,
            )
        # A real width: the height it implies, as minimum *and* natural. Not
        # a lower figure, however shrinkable a picture is — a column taller
        # than its viewport is allocated by minimums, so a picture that can
        # measure as less gets exactly that: a sliver where the screenshot
        # should be, while every label beside it keeps its size. An image is
        # content; it makes the column scroll rather than giving way to it.
        height = max(1, round(min(for_size, self._cap) * self._height / self._width))
        return (height, height, -1, -1)
