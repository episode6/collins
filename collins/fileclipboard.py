# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Files on the system clipboard: what the editor's file tree puts there on
Copy/Cut, and what it reads back on Paste.

Its own module because the two halves live apart — the tree writes the
clipboard (filetree.py), the pane reads it (editor.py, which is what knows
whether a moved file is open in a tab) — and both have to agree on the same
payloads. Three of them go out on every copy, so the clipboard is worth
something outside Collins too:

- `Gdk.FileList`, which every GTK app understands;
- `text/uri-list`, which everything else does;
- `x-special/gnome-copied-files`, the only one that can say *cut* — a plain
  URI list pasted into Nautilus is always a copy.

The same three are read back, in that reverse order of preference: the GNOME
payload first (it carries the cut flag), then whatever GDK can turn into a
file list.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject  # noqa: E402

from . import editorfiles  # noqa: E402

GNOME_COPIED_FILES = "x-special/gnome-copied-files"
URI_LIST = "text/uri-list"

_READ_CHUNK = 8192
# A path list past this isn't a path list. Reading stops there rather than
# letting another app's idea of a clipboard payload grow without bound.
_MAX_PAYLOAD_BYTES = 1 << 20


def set_files(clipboard: Gdk.Clipboard, paths: list[str], cut: bool = False) -> None:
    """Put *paths* on *clipboard* as a file copy — or a cut, which only the
    GNOME payload can express (see the module docstring)."""
    files = [Gio.File.new_for_path(path) for path in paths]
    uris = [file.get_uri() for file in files]
    payload = editorfiles.format_copied_files(uris, cut)
    providers = [
        Gdk.ContentProvider.new_for_value(_value(Gdk.FileList, Gdk.FileList.new_from_list(files))),
        Gdk.ContentProvider.new_for_bytes(GNOME_COPIED_FILES, GLib.Bytes.new(payload.encode())),
        # CRLF and a trailing terminator: RFC 2483's line endings, which some
        # readers are strict about.
        Gdk.ContentProvider.new_for_bytes(
            URI_LIST, GLib.Bytes.new(("\r\n".join(uris) + "\r\n").encode())
        ),
        # Last, so it is only ever picked by something that wants text: the
        # paths as they'd be typed, for a terminal or an editor.
        Gdk.ContentProvider.new_for_value(_value(str, "\n".join(paths))),
    ]
    clipboard.set_content(Gdk.ContentProvider.new_union(providers))


def _value(gtype, content) -> GObject.Value:
    """*content* boxed as a `GObject.Value` of *gtype* — what
    `Gdk.ContentProvider.new_for_value` takes, and what PyGObject won't build
    from a bare Python object on its own."""
    return GObject.Value(gtype, content)


def has_files(clipboard: Gdk.Clipboard) -> bool:
    """Whether *clipboard* holds anything a paste could act on. Synchronous
    (it only looks at the advertised formats, never at the data), so a menu
    can grey Paste out as it opens."""
    formats = clipboard.get_formats()
    return (
        formats.contain_mime_type(GNOME_COPIED_FILES)
        or formats.contain_mime_type(URI_LIST)
        or formats.contain_gtype(Gdk.FileList)
    )


def read_files(clipboard: Gdk.Clipboard, on_ready: Callable[[list[str], bool], None]) -> None:
    """Read *clipboard*'s files, then call `on_ready(paths, cut)` — with an
    empty list when there is nothing local on it. Always asynchronous: the
    data may still have to come across from another process, and a sync read
    would freeze the window while it did."""
    formats = clipboard.get_formats()
    if formats.contain_mime_type(GNOME_COPIED_FILES):
        _read_gnome_payload(clipboard, on_ready)
    elif formats.contain_mime_type(URI_LIST) or formats.contain_gtype(Gdk.FileList):
        _read_file_list(clipboard, on_ready)
    else:
        on_ready([], False)


def _read_gnome_payload(clipboard: Gdk.Clipboard, on_ready: Callable[[list[str], bool], None]) -> None:
    def opened(_clipboard, result) -> None:
        try:
            stream, _mime = clipboard.read_finish(result)
        except GLib.Error:
            stream = None
        if stream is None:
            # The owner advertised the format and then failed to hand it over;
            # the file list may still be readable.
            _read_file_list(clipboard, on_ready)
            return
        _read_all(stream, lambda data: on_ready(*editorfiles.parse_copied_files(data)))

    clipboard.read_async([GNOME_COPIED_FILES], GLib.PRIORITY_DEFAULT, None, opened)


def _read_file_list(clipboard: Gdk.Clipboard, on_ready: Callable[[list[str], bool], None]) -> None:
    """`Gdk.FileList` covers `text/uri-list` too — GDK deserializes one into
    the other — so this is the whole non-GNOME half. Never a cut: nothing
    outside the GNOME payload can say so."""

    def read(_clipboard, result) -> None:
        try:
            value = clipboard.read_value_finish(result)
        except GLib.Error:
            on_ready([], False)
            return
        files = value.get_files() if isinstance(value, Gdk.FileList) else []
        on_ready([path for file in files if (path := file.get_path()) is not None], False)

    clipboard.read_value_async(Gdk.FileList.__gtype__, GLib.PRIORITY_DEFAULT, None, read)


def _read_all(stream: Gio.InputStream, on_done: Callable[[str], None]) -> None:
    """Drain *stream* into one decoded string, a chunk per main-loop turn."""
    chunks: list[bytes] = []

    def finish() -> None:
        on_done(b"".join(chunks).decode("utf-8", "replace"))

    def read_chunk() -> None:
        stream.read_bytes_async(_READ_CHUNK, GLib.PRIORITY_DEFAULT, None, got_chunk)

    def got_chunk(_stream, result) -> None:
        try:
            data = stream.read_bytes_finish(result).get_data()
        except GLib.Error:
            data = b""
        if not data:
            finish()
            return
        chunks.append(data)
        if sum(len(chunk) for chunk in chunks) >= _MAX_PAYLOAD_BYTES:
            finish()
            return
        read_chunk()

    read_chunk()
