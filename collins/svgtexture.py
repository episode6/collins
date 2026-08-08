# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Rasterizing SVG bytes into textures.

Shared by the sidebar (project icons on group rows) and the Generate Icon
dialog (previewing a generated icon before it is saved), which sit on
opposite sides of an import cycle (sidebar → prmenu → dialogs) and so can't
lend the helper to each other.
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
