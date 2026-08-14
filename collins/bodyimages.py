# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The images a PR body embeds, fetched and shown in place.

`formatting.split_body` finds them; this module puts them on screen. One
`image()` call per `BodyImage` returns a slot that shows a dim placeholder
at once and the picture when (and if) its download lands — the avatars
pattern, one altitude up: the download and the height-for-width picture
both come from `pictures`, and what's left here is a PR body's own half of
it, including a failure that degrades to exactly what the body showed
before this feature existed (the alt text, linking out).

Animated GIFs animate (see animatedimage, via pictures), because the demo
GIFs that PR bodies carry are the whole reason to render an image rather
than name it.

Everything here is repository content: the URL was held to http(s) before
it arrived, the alt text only reaches a widget through markup escaping, and
the bytes only ever become a paintable — never markup, never a path the
user's shell sees.
"""

from __future__ import annotations

import urllib.parse
from pathlib import Path, PurePosixPath

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from . import animatedimage, pictures  # noqa: E402
from .formatting import BodyImage  # noqa: E402
from .i18n import _  # noqa: E402
from .lightbox import present_image_lightbox  # noqa: E402

# Decoded pictures kept between rebuilds, newest last. A refresh rebuilds
# every card, and re-decoding a screenshot per rebuild is a visible hitch on
# the main loop; holding them all instead would be an unbounded pile of
# textures, so the oldest fall out and re-decode if they're ever needed.
_PAINTABLE_CACHE = 24

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
    # The placeholder goes up first and is replaced in the same frame when
    # the file is already here (pictures.fetch answers a hit straight away).
    _swap(slot, _placeholder(entry))
    pictures.fetch(entry.url, lambda path, error: _fill(slot, entry, path, error))
    return slot


def _swap(slot: Gtk.Box, child: Gtk.Widget) -> None:
    """Put *child* in the slot, in place of whatever stood there."""
    old = slot.get_first_child()
    while old is not None:
        slot.remove(old)
        old = slot.get_first_child()
    slot.append(child)


def _fill(slot: Gtk.Box, entry: BodyImage, path: Path | None, error: str | None) -> None:
    """Put the fetched picture in *slot* — or the fallback link, when the
    fetch failed or what came back won't decode."""
    paintable = _paintable(entry.url, path) if path is not None else None
    if paintable is None:
        _swap(slot, _fallback(entry, error))
        return
    picture = pictures.BoundedPicture(paintable, entry.width)
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


def _fallback(entry: BodyImage, error: str | None) -> Gtk.Widget:
    """What a body showed before this feature existed: the alt text as a
    link out, plus the reason it isn't a picture — the fetch's own, or (a
    fetch that landed a file nothing could decode) this module's."""
    widget = _stand_in(entry, "image-missing-symbolic", link=True)
    widget.set_tooltip_text(error or _("That file isn't an image Collins can display."))
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
