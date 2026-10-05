# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Files on the system clipboard: what the editor's file tree puts there on
Copy/Cut, and what it reads back on Paste.

Its own module because the two halves live apart — the tree writes the
clipboard (filetree.py), the pane reads it (editor.py, which is what knows
whether a moved file is open in a tab) — and both have to agree on the same
payloads. Every path is a path on the service's machine (split-service
spec §3.23, D35), so what goes out depends on `remotefiles.clipboard_scope`:

- always, Collins' own payload (`x-collins/copied-files`: the operation,
  then one `collins://<service id>/<path>` URI per line), the one a paste
  within Collins reads, and the only one that can say *cut*; and the
  paths as plain text, for a terminal or an editor;
- only for a `local` client (the service's files are this machine's), the
  three everything else understands: `Gdk.FileList`, `text/uri-list` and
  `x-special/gnome-copied-files` (the GNOME file managers' cut flag), all
  of them `file:` URIs. A client that is not local puts no `file:` URI on
  the clipboard: it would name a file on the wrong machine.

The same are read back, in that order of preference: Collins' own payload
first (another service's URIs are dropped: its paths mean nothing here),
then — when local — the GNOME payload and whatever GDK can turn into a
file list; a payload that yields no path falls through to the next, so a
local client reading another local Collins' copy still gets the `file:`
formats beside it. A `file:` URI from a file manager is a path on this
machine, which the service can only paste when it is this machine.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject  # noqa: E402

from . import editorfiles, remotefiles  # noqa: E402

GNOME_COPIED_FILES = "x-special/gnome-copied-files"
URI_LIST = "text/uri-list"
# Collins' own: the GNOME payload's shape over `collins://` URIs (D35).
COLLINS_COPIED_FILES = "x-collins/copied-files"

_READ_CHUNK = 8192
# A path list past this isn't a path list. Reading stops there rather than
# letting another app's idea of a clipboard payload grow without bound.
_MAX_PAYLOAD_BYTES = 1 << 20


def set_files(
    clipboard: Gdk.Clipboard,
    paths: list[str],
    cut: bool = False,
    scope: remotefiles.ClipboardScope | None = None,
) -> None:
    """Put *paths* (the service's) on *clipboard* as a file copy — or a cut,
    which only the GNOME payload and Collins' own can express (see the
    module docstring). *scope* is the link's (`remotefiles.clipboard_scope`
    when None)."""
    scope = scope or remotefiles.clipboard_scope()
    providers = []
    if scope.service_id is not None:
        collins_uris = [editorfiles.collins_uri(scope.service_id, path) for path in paths]
        providers.append(
            Gdk.ContentProvider.new_for_bytes(
                COLLINS_COPIED_FILES,
                GLib.Bytes.new(editorfiles.format_copied_files(collins_uris, cut).encode()),
            )
        )
    if scope.local:
        files = [Gio.File.new_for_path(path) for path in paths]
        uris = [file.get_uri() for file in files]
        payload = editorfiles.format_copied_files(uris, cut)
        providers += [
            Gdk.ContentProvider.new_for_value(_value(Gdk.FileList, Gdk.FileList.new_from_list(files))),
            Gdk.ContentProvider.new_for_bytes(GNOME_COPIED_FILES, GLib.Bytes.new(payload.encode())),
            # CRLF and a trailing terminator: RFC 2483's line endings, which some
            # readers are strict about.
            Gdk.ContentProvider.new_for_bytes(
                URI_LIST, GLib.Bytes.new(("\r\n".join(uris) + "\r\n").encode())
            ),
        ]
    # Last, so it is only ever picked by something that wants text: the
    # paths as they'd be typed, for a terminal or an editor.
    providers.append(Gdk.ContentProvider.new_for_value(_value(str, "\n".join(paths))))
    clipboard.set_content(Gdk.ContentProvider.new_union(providers))


def _value(gtype, content) -> GObject.Value:
    """*content* boxed as a `GObject.Value` of *gtype* — what
    `Gdk.ContentProvider.new_for_value` takes, and what PyGObject won't build
    from a bare Python object on its own."""
    return GObject.Value(gtype, content)


def has_files(clipboard: Gdk.Clipboard, scope: remotefiles.ClipboardScope | None = None) -> bool:
    """Whether *clipboard* holds anything a paste could act on. Synchronous
    (it only looks at the advertised formats, never at the data), so a menu
    can grey Paste out as it opens. A `file:` payload counts only for a
    `local` client (the module docstring); Collins' own payload counts for
    any client with a service, since the formats alone cannot say whose
    service its URIs name (`read_files` drops another service's)."""
    scope = scope or remotefiles.clipboard_scope()
    formats = clipboard.get_formats()
    if formats.contain_mime_type(COLLINS_COPIED_FILES) and scope.service_id is not None:
        return True
    return scope.local and (
        formats.contain_mime_type(GNOME_COPIED_FILES)
        or formats.contain_mime_type(URI_LIST)
        or formats.contain_gtype(Gdk.FileList)
    )


def read_files(
    clipboard: Gdk.Clipboard,
    on_ready: Callable[[list[str], bool], None],
    scope: remotefiles.ClipboardScope | None = None,
) -> None:
    """Read *clipboard*'s files, then call `on_ready(paths, cut)` — with an
    empty list when there is nothing on it the service could paste. Always
    asynchronous: the data may still have to come across from another
    process, and a sync read would freeze the window while it did."""
    scope = scope or remotefiles.clipboard_scope()
    formats = clipboard.get_formats()
    # The formats to try, in order of preference; a payload that yields no
    # path (Collins' own naming another service's files; an owner that
    # advertised a format and then failed to hand it over) falls through
    # to the next, so a local client beside another local Collins still
    # reads the `file:` formats the same clipboard carries.
    attempts: list[Callable[[Callable[[list[str], bool], None]], None]] = []
    if formats.contain_mime_type(COLLINS_COPIED_FILES) and scope.service_id is not None:
        attempts.append(lambda done: _read_payload(clipboard, COLLINS_COPIED_FILES, scope, done))
    if scope.local and formats.contain_mime_type(GNOME_COPIED_FILES):
        attempts.append(lambda done: _read_payload(clipboard, GNOME_COPIED_FILES, scope, done))
    if scope.local and (formats.contain_mime_type(URI_LIST) or formats.contain_gtype(Gdk.FileList)):
        attempts.append(lambda done: _read_file_list(clipboard, done))

    def next_attempt() -> None:
        if not attempts:
            on_ready([], False)
            return
        attempt = attempts.pop(0)

        def done(paths: list[str], cut: bool) -> None:
            if paths or not attempts:
                on_ready(paths, cut)
            else:
                next_attempt()

        attempt(done)

    next_attempt()


def _read_payload(
    clipboard: Gdk.Clipboard,
    mime: str,
    scope: remotefiles.ClipboardScope,
    on_ready: Callable[[list[str], bool], None],
) -> None:
    """Collins' own payload or the GNOME one: the operation, then URIs,
    parsed for the scope (`editorfiles.parse_copied_files`). An owner
    that advertised the format and then failed to hand it over reads as
    nothing (the caller's next format, if any)."""

    def opened(_clipboard, result) -> None:
        try:
            stream, _mime = clipboard.read_finish(result)
        except GLib.Error:
            stream = None
        if stream is None:
            on_ready([], False)
            return
        _read_all(
            stream,
            lambda data: on_ready(*editorfiles.parse_copied_files(data, scope.service_id, scope.local)),
        )

    clipboard.read_async([mime], GLib.PRIORITY_DEFAULT, None, opened)


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
