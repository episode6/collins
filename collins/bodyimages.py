# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The images a PR body embeds, fetched and shown in place.

`formatting.split_body` finds them; this module puts them on screen. One
`image()` call per `BodyImage` returns a slot that shows a dim placeholder
at once and the picture when (and if) its download lands — the avatars
pattern, one altitude up: gated daemon threads, the widget filled from an
idle callback, both caches only ever touched on the main loop, and a
failure that degrades to exactly what the body showed before this feature
existed (the alt text, linking out).

Downloads go through `remoteimages`, the same capped, timeout-bounded,
redirect-gated fetch `show_image` uses, into the same pruned cache
directory — so a fetched body image is a real file on disk, which is what
lets a click hand it to the lightbox with "Open With…" and zoom intact.
Nothing about a PR is worth a second copy of that logic.

Pictures are `_BoundedPicture`: GTK's own `Gtk.Picture` has no
height-for-width, so a 1200x800 screenshot in a 320px panel would ask for
800px of height and paint 213px of image in the middle of it. This one
measures the height the width actually implies, and caps the width at
whatever makes the image `_MAX_HEIGHT` tall — a screenshot rides in the
conversation instead of burying it, and the lightbox is one click away for
a proper look. Animated GIFs animate (see animatedimage), because the demo
GIFs that PR bodies carry are the whole reason to render an image rather
than name it.

Everything here is repository content: the URL was held to http(s) before
it arrived, the alt text only reaches a widget through markup escaping, and
the bytes only ever become a paintable — never markup, never a path the
user's shell sees.
"""

from __future__ import annotations

import logging
import threading
import urllib.parse
from pathlib import Path, PurePosixPath

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from . import animatedimage, remoteimages  # noqa: E402
from .formatting import BodyImage  # noqa: E402
from .i18n import _  # noqa: E402
from .lightbox import present_image_lightbox  # noqa: E402

log = logging.getLogger(__name__)

# The tallest an image renders inline; past it the picture is scaled down
# (never cropped) and the lightbox holds the full-size look.
_MAX_HEIGHT = 400
# The narrowest an image is ever measured at, and so the floor its minimum
# height comes from (see _BoundedPicture.do_measure). Narrower than any
# panel a PR page is readable in, which is the point: it is a bound, not a
# size anything actually gets.
_FLOOR_WIDTH = 120
# How many downloads may be in flight at once. A body may carry twenty
# screenshots, and a PR page opening shouldn't burst twenty sockets — the
# same reasoning as avatars._GATE, one size up because these are bigger.
_GATE = threading.Semaphore(3)
# Decoded pictures kept between rebuilds, newest last. A refresh rebuilds
# every card, and re-decoding a screenshot per rebuild is a visible hitch on
# the main loop; holding them all instead would be an unbounded pile of
# textures, so the oldest fall out and re-decode if they're ever needed.
_PAINTABLE_CACHE = 24

# url -> the file it was fetched to, or None when the fetch failed. Failures
# are cached too: a URL that 404s must not be retried on every rebuild.
_files: dict[str, Path | None] = {}
_errors: dict[str, str] = {}  # url -> why it failed, for the fallback's tooltip
_waiting: dict[str, list] = {}  # fetch in flight -> slots to fill when it lands
_paintables: dict[str, Gdk.Paintable] = {}  # url -> decoded picture content


def image(entry: BodyImage) -> Gtk.Widget:
    """A slot for *entry*: a placeholder now, the picture when it arrives.

    Main thread only (it builds widgets and reads the caches).
    """
    # A box, not an Adw.Bin: a bin's layout manager advertises a constant
    # size request, which cuts the picture's height-for-width off from the
    # column above it (GTK then measures the two directions independently
    # and warns that the height it is handing out is under the minimum).
    slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    slot.add_css_class("pr-body-image")
    slot.set_halign(Gtk.Align.START)
    if entry.url in _files:
        cached = _files[entry.url]
        # A cached path whose file has since been pruned (downloads live a
        # day) is not a cache hit — fetch it again rather than showing a
        # picture that can no longer be opened.
        if cached is None or cached.exists():
            _fill(slot, entry, cached)
            return slot
        del _files[entry.url]
        _paintables.pop(entry.url, None)
    _swap(slot, _placeholder(entry))
    if entry.url in _waiting:
        _waiting[entry.url].append((slot, entry))
        return slot
    _waiting[entry.url] = [(slot, entry)]
    threading.Thread(
        target=_fetch, args=(entry.url,), name="pr-body-image", daemon=True
    ).start()
    return slot


def _fetch(url: str) -> None:
    path = error = None
    try:
        with _GATE:
            path = remoteimages.fetch_to_file(url, remoteimages.default_directory())
    except remoteimages.FetchError as failure:
        error = str(failure)
    except Exception as failure:  # a fetch must never take the thread with it
        error = str(failure)
        log.debug("bodyimages: fetching %s failed", url, exc_info=True)
    GLib.idle_add(_landed, url, path, error)


def _landed(url: str, path: Path | None, error: str | None) -> bool:
    _files[url] = path
    if error is not None:
        _errors[url] = error
    for slot, entry in _waiting.pop(url, []):
        _fill(slot, entry, path)
    return GLib.SOURCE_REMOVE


def _swap(slot: Gtk.Box, child: Gtk.Widget) -> None:
    """Put *child* in the slot, in place of whatever stood there."""
    old = slot.get_first_child()
    while old is not None:
        slot.remove(old)
        old = slot.get_first_child()
    slot.append(child)


def _fill(slot: Gtk.Box, entry: BodyImage, path: Path | None) -> None:
    """Put the fetched picture in *slot* — or the fallback link, when the
    fetch failed or what came back won't decode."""
    paintable = _paintable(entry.url, path) if path is not None else None
    if paintable is None:
        _swap(slot, _fallback(entry))
        return
    picture = _BoundedPicture(paintable, entry.width)
    picture.set_tooltip_text(entry.alt or entry.url)
    picture.set_cursor(Gdk.Cursor.new_from_name("pointer", None))
    click = Gtk.GestureClick()
    click.connect(
        "pressed",
        lambda _gesture, _n, _x, _y: present_image_lightbox(
            picture, path, caption=entry.alt or None, origin=entry.url
        ),
    )
    picture.add_controller(click)
    _swap(slot, picture)


def _paintable(url: str, path: Path) -> Gdk.Paintable | None:
    cached = _paintables.get(url)
    if cached is not None:
        return cached
    loaded = animatedimage.load(path)
    if loaded is None:
        _errors.setdefault(url, _("That file isn't an image Collins can display."))
        return None
    while len(_paintables) >= _PAINTABLE_CACHE:
        _paintables.pop(next(iter(_paintables)))
    _paintables[url] = loaded
    return loaded


def _placeholder(entry: BodyImage) -> Gtk.Widget:
    """What stands in while the download runs: the image's own name, dimmed,
    in a frame the picture will replace. Deliberately short — the layout
    shifts when the real size arrives, and a tall stand-in shifts it more."""
    return _stand_in(entry, "image-x-generic-symbolic", link=False)


def _fallback(entry: BodyImage) -> Gtk.Widget:
    """What a body showed before this feature existed: the alt text as a
    link out, plus the reason it isn't a picture."""
    widget = _stand_in(entry, "image-missing-symbolic", link=True)
    error = _errors.get(entry.url)
    if error:
        widget.set_tooltip_text(error)
    return widget


def _stand_in(entry: BodyImage, icon_name: str, link: bool) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    box.add_css_class("pr-body-image-standin")
    icon = Gtk.Image.new_from_icon_name(icon_name)
    icon.add_css_class("dim-label")
    box.append(icon)
    label = Gtk.Label(xalign=0.0)
    label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
    label.add_css_class("dim-label")
    text = entry.alt or _name(entry.url)
    if link:
        label.set_markup(
            f'<a href="{GLib.markup_escape_text(entry.url)}">'
            f"{GLib.markup_escape_text(text)}</a>"
        )
    else:
        label.set_label(text)
    box.append(label)
    return box


def _name(url: str) -> str:
    """The file name a URL ends in — what to call an image with no alt text
    while it loads. Anything unparseable falls back to the whole URL."""
    try:
        name = PurePosixPath(urllib.parse.urlsplit(url).path).name
    except ValueError:
        return url
    return name or url


class _BoundedPicture(Gtk.Picture):
    """A picture that measures like an image in a column of text.

    `Gtk.Picture` reports its paintable's size in both directions and never
    relates them, so in a column narrower than the image it asks for the
    full natural height and paints a scaled-down image floating in the
    middle of the leftover space. Measuring height-for-width instead makes
    the widget exactly as tall as the width implies, and capping the
    natural width at the one that makes the image `_MAX_HEIGHT` tall keeps
    a tall screenshot from taking the whole panel.
    """

    __gtype_name__ = "CollinsBoundedPicture"

    def __init__(self, paintable: Gdk.Paintable, width: int = 0) -> None:
        super().__init__()
        self.set_paintable(paintable)
        self.set_can_shrink(True)
        self.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.set_halign(Gtk.Align.START)
        self._width = max(1, paintable.get_intrinsic_width())
        self._height = max(1, paintable.get_intrinsic_height())
        self._cap = self._width
        if self._height > _MAX_HEIGHT:
            self._cap = max(1, round(_MAX_HEIGHT * self._width / self._height))
        if width:
            # An <img width=> the author set: never wider than they asked
            # for, and never wider than the picture actually is (upscaling
            # a screenshot only makes it blurrier).
            self._cap = min(self._cap, width)

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple:
        if orientation == Gtk.Orientation.HORIZONTAL:
            # Zero minimum width: a picture is the one thing in a card that
            # should give way rather than force the panel wider.
            return (0, self._cap, -1, -1)
        if for_size <= 0:
            # Nobody has said how wide yet. The natural height is the
            # capped image; the minimum is a true lower bound — the height
            # at the narrowest width worth showing one at. It has to be a
            # bound that holds at *every* width, or GTK warns each time a
            # parent measures this widget for the height it just worked out
            # (which is smaller than the height at the cap).
            return (
                max(1, round(_FLOOR_WIDTH * self._height / self._width)),
                max(1, round(self._cap * self._height / self._width)),
                -1,
                -1,
            )
        # A real width: the height it implies, as minimum *and* natural.
        # Not a lower figure, however shrinkable a picture is — a
        # conversation column taller than its viewport is allocated by
        # minimums, so a picture that can measure as less gets exactly that:
        # a sliver where the screenshot should be, while every label beside
        # it keeps its size. A body image is content; it makes the column
        # scroll rather than giving way to it.
        height = max(1, round(min(for_size, self._cap) * self._height / self._width))
        return (height, height, -1, -1)
