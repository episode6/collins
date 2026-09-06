# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""A changed image, shown as pictures: **Before** beside **After**.

The diff is the wrong renderer for a picture — git says ``Binary files a/
icon.png and b/icon.png differ`` and stops; an SVG spills a screen of path
data that says a shape changed without ever showing it. So both places
Collins shows a diff of images draw them instead: the PR page's Files view
(prfileimages, whose blobs come from `gh`) and the git page's native diff
view (diffview, whose blobs come from `gitops.file_at`). This module is the
drawing they share; each caller says which sides a file has and how the
bytes of each are fetched (an `ImageSide`), and gets back a row of pictures.

A side's bytes are fetched through `pictures.fetch` — once per key per run,
on a worker thread, three at a time — so a PR that regenerates thirty
screenshots fills its sections in progressively. The picture is decoded no
bigger than it is drawn, measures height-for-width in the column, animates
when it is a GIF, and hands itself to the lightbox at full size on a click.
A blob that won't come or won't decode degrades to a dimmed stand-in
carrying the reason in its tooltip, and the other side stays a picture.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk, Pango  # noqa: E402

from . import pictures, svgtexture  # noqa: E402
from .i18n import _  # noqa: E402
from .lightbox import present_image_lightbox  # noqa: E402

# What one preview may take of the section's height. Shorter than a body
# image's 400px: a file section is a header, up to two pictures and a patch,
# and two 400px-tall previews would push the diff itself off the screen.
MAX_HEIGHT = 320
# The width a preview is decoded for — a ceiling, not a size. The column is
# the wider half of the view and the picture shrinks to whatever it is
# really allocated; this only bounds what gets decoded.
_DECODE_WIDTH = 900
# The size a small SVG is rasterized up to. Vector artwork loses nothing by
# being drawn bigger, and most of the SVGs a repository changes are icons:
# a 22-pixel panel glyph shown at 22 pixels is a preview of nothing.
_SVG_MIN_PX = 160

# Decoded previews kept between rebuilds, newest last — bodyimages' cache,
# for its reason: a landed refresh rebuilds every section, and re-decoding a
# screenshot per rebuild is a visible hitch on the main loop, while holding
# them all would be an unbounded pile of textures. Keyed by blob (commit and
# all), so a push decodes the new picture rather than reusing the old one.
_PAINTABLE_CACHE = 24

_paintables: dict[str, Gdk.Paintable] = {}


@dataclass(frozen=True)
class ImageSide:
    """One picture a changed image has. *key* is what `pictures.fetch`
    remembers the fetched file by (name the commit, not the branch, so a
    moved branch fetches new bytes); *path* is the file's repository path,
    for the stand-in's name and the lightbox's caption; *caption* is
    "Before" / "After" when two sides are shown and "" alone; *fetcher* is
    the blocking half — called on a worker thread, returning the file the
    bytes were saved to, raising when they can't be had."""

    key: str
    path: str
    caption: str
    fetcher: Callable[[], Path]


def preview_row(sides: Sequence[ImageSide]) -> Gtk.Widget | None:
    """The pictures for a file's *sides*, side by side (homogeneous when
    there are two, so before and after share the width) — or None for no
    sides at all. Main thread only: the fetches start here and land later;
    the widget handed back is where they land."""
    if not sides:
        return None
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    row.set_homogeneous(len(sides) > 1)
    for side in sides:
        row.append(side_column(side))
    return row


def side_column(side: ImageSide) -> Gtk.Widget:
    """One picture's column: its caption (when it has one), then the slot
    the fetch lands in."""
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    column.set_halign(Gtk.Align.START)
    column.set_valign(Gtk.Align.START)
    if side.caption:
        label = Gtk.Label(label=side.caption, xalign=0.0)
        label.add_css_class("caption")
        label.add_css_class("dim-label")
        column.append(label)
    # A box rather than an Adw.Bin, for bodyimages' reason: a bin's layout
    # manager advertises a constant size request, which cuts the picture's
    # height-for-width off from the column above it.
    slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    slot.add_css_class("pr-file-image")
    _swap(slot, _placeholder(side.path))
    pictures.fetch(
        side.key,
        lambda file, error: _fill(slot, side, file, error),
        fetcher=side.fetcher,
    )
    column.append(slot)
    return column


def _swap(slot: Gtk.Box, child: Gtk.Widget) -> None:
    """Put *child* in the slot, in place of whatever stood there."""
    old = slot.get_first_child()
    while old is not None:
        slot.remove(old)
        old = slot.get_first_child()
    slot.append(child)


def _fill(slot: Gtk.Box, side: ImageSide, file: Path | None, error: str | None) -> None:
    """Put the fetched picture in *slot* — or the stand-in, when the blob
    didn't arrive or won't decode."""
    path, caption = side.path, side.caption
    paintable = _cached(side.key, file) if file is not None else None
    if paintable is None:
        # The fetch's own reason when it had one; ours when the blob arrived
        # and nothing here could decode it (an SVG on a machine with no
        # librsvg, a format gdk-pixbuf doesn't know).
        why = error or _("That file isn't an image Collins can display.")
        _swap(slot, _stand_in(path, "image-missing-symbolic", why))
        return
    picture = pictures.BoundedPicture(paintable, max_height=MAX_HEIGHT)
    picture.set_tooltip_text(f"{path}\n{caption}" if caption else path)
    picture.set_cursor(Gdk.Cursor.new_from_name("pointer", None))
    click = Gtk.GestureClick()
    click.connect(
        "pressed",
        lambda _gesture, _n, _x, _y: present_image_lightbox(
            picture,
            file,
            caption=f"{path} — {caption}" if caption else path,
            origin=path,
        ),
    )
    picture.add_controller(click)
    _swap(slot, picture)


def _cached(key: str, file: Path) -> Gdk.Paintable | None:
    """`_paintable`, decoded once per blob per run."""
    held = _paintables.get(key)
    if held is not None:
        return held
    loaded = _paintable(file)
    if loaded is None:
        return None
    while len(_paintables) >= _PAINTABLE_CACHE:
        _paintables.pop(next(iter(_paintables)))
    _paintables[key] = loaded
    return loaded


def _paintable(file: Path) -> Gdk.Paintable | None:
    """The fetched blob as something to draw, decoded no bigger than it is
    shown.

    SVGs go through `svgtexture` rather than the shared thumbnail path: it
    forces the SVG loader (a repository's bytes must not get to pick a codec
    by sniffing) and it scales a small icon *up* to a size worth looking at,
    which is what most of the SVGs in a diff are. Everything else —
    screenshots and GIFs, which is what the rest of them are — goes through
    `pictures`, animation and decode caps and all.
    """
    if file.suffix.lower() == ".svg":
        try:
            svg = file.read_bytes()
        except OSError:
            return None
        return svgtexture.svg_texture_fit(svg, _DECODE_WIDTH, MAX_HEIGHT, _SVG_MIN_PX)
    return pictures.thumbnail(file, _DECODE_WIDTH, MAX_HEIGHT)


def _placeholder(path: str) -> Gtk.Widget:
    """What stands in while the blob is fetched — the same short dimmed row
    bodyimages uses, for the same reason: the layout shifts when the picture
    lands, and a tall stand-in shifts it further."""
    return _stand_in(path, "image-x-generic-symbolic", None)


def _stand_in(path: str, icon_name: str, error: str | None) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    box.add_css_class("pr-file-image-standin")
    icon = Gtk.Image.new_from_icon_name(icon_name)
    icon.add_css_class("dim-label")
    box.append(icon)
    label = Gtk.Label(label=PurePosixPath(path).name, xalign=0.0)
    label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
    label.add_css_class("dim-label")
    box.append(label)
    if error is not None:
        box.set_tooltip_text(error)
    return box
