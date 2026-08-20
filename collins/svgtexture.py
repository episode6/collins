# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Rasterizing SVG bytes into textures.

Shared by the sidebar (project icons on group rows) and the Generate Icon
dialog (previewing a generated icon before it is saved), which sit on
opposite sides of an import cycle (sidebar → prmenu → dialogs) and so can't
lend the helper to each other — and by the PR view's file previews
(prfileimages), which rasterize a repository's own SVG rather than an
icon of ours, and want it fitted to a box instead of squared off.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib  # noqa: E402


def svg_texture(svg: bytes | None, size: int) -> Gdk.Texture | None:
    """Rasterize icon bytes at the target size, forced through the SVG
    pixbuf loader. Forcing the type keeps repo-controlled bytes away from
    gdk-pixbuf's content sniffing (which would otherwise route a crafted
    file to any installed codec), and decoding at the target size bounds the
    raster surface regardless of the document's own canvas dimensions."""
    if svg is None:
        return None
    try:
        loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    except GLib.Error:  # SVG loader not installed
        return None
    loader.set_size(size, size)
    try:
        loader.write(svg)
        loader.close()
    except GLib.Error:
        try:
            loader.close()
        except GLib.Error:
            pass
        return None
    pixbuf = loader.get_pixbuf()
    if pixbuf is None:
        return None
    return Gdk.Texture.new_for_pixbuf(pixbuf)


def svg_texture_fit(
    svg: bytes | None, max_width: int, max_height: int, min_long_edge: int = 0
) -> Gdk.Texture | None:
    """`svg_texture`'s sibling for artwork that isn't square: rasterize *svg*
    at its own proportions, inside a *max_width* x *max_height* box.

    A document smaller than *min_long_edge* is scaled *up* to it — an SVG is
    vector, so there is nothing to lose, and a 22-pixel panel icon rendered
    at 22 pixels is a preview nobody can review. The box still wins: a
    document that would exceed it after that comes back fitted to it.

    Same forced loader type as `svg_texture`, for the same reason: these are
    a repository's bytes, and content sniffing would let a file named .svg
    pick any codec on the machine. None when the SVG loader isn't installed
    or the document won't parse.

    The box is enforced twice: once through ``size-prepared`` (the cheap
    way — librsvg then rasterizes straight to the size we want) and once on
    what actually came back. The second pass is for the loader that reports
    no intrinsic size at all: this librsvg answers with the viewBox for a
    document that carries no width/height, and with 300x300 for one that
    carries neither, but that is a version's behavior rather than a promise,
    and a preview must not be able to decode a document's own idea of how
    big it is. Only the *cap* is re-applied there — a raster scaled up after
    the fact is just blurry, which is not what the minimum is for.
    """
    if not svg:
        return None
    try:
        loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    except GLib.Error:  # SVG loader not installed
        return None

    def prepared(_loader, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            return
        long_edge = max(width, height)
        up = max(1.0, min_long_edge / long_edge) if min_long_edge else 1.0
        fit = min(max_width / width, max_height / height)
        scale = min(up, fit)
        loader.set_size(max(1, round(width * scale)), max(1, round(height * scale)))

    loader.connect("size-prepared", prepared)
    try:
        loader.write(svg)
        loader.close()
    except GLib.Error:
        try:
            loader.close()
        except GLib.Error:
            pass
        return None
    pixbuf = _within(loader.get_pixbuf(), max_width, max_height)
    return Gdk.Texture.new_for_pixbuf(pixbuf) if pixbuf is not None else None


def _within(
    pixbuf: GdkPixbuf.Pixbuf | None, max_width: int, max_height: int
) -> GdkPixbuf.Pixbuf | None:
    """*pixbuf* if it is already inside the box, a scaled copy if it isn't."""
    if pixbuf is None:
        return None
    width, height = pixbuf.get_width(), pixbuf.get_height()
    if width <= 0 or height <= 0:
        return None
    if width <= max_width and height <= max_height:
        return pixbuf
    fit = min(max_width / width, max_height / height)
    return pixbuf.scale_simple(
        max(1, round(width * fit)),
        max(1, round(height * fit)),
        GdkPixbuf.InterpType.BILINEAR,
    )
