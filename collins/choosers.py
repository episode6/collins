# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Choosing a folder of the service's (split-service spec §3.11, §3.24;
PR-3.2).

`choose_folder` is what the seven sites that pick a folder the service
will use call (the project folder for a new session, *Add project*,
continue, new chat, the clone destination in its dialog and in
Preferences, a sandbox grant). A client on the service's machine (the
`local` proof, D11: `apilink.is_local()`) gets the desktop's own
`Gtk.FileDialog`, as it always did: its folders are the service's. Any
other client gets `pathpicker.PathPicker`, which lists the service's
folders over its own unconfined `fs.dirs` and makes one with `fs.newdir`
(D60; never the editor's confined `fs.list` / `fs.mkdir`). Either way *on_done(path)* is called with the
folder picked, a path on the service's machine, and a dismissal calls
nothing.

The six other `Gtk.FileDialog` sites stay native, each ruled (§3.11,
D56): the Markdown export and the notification sound are this device's
files; the CLI browse and *Open session transcript* are hidden local
extras; *Attach file* picks a file of this device's and, for a client
that is not local, uploads it as a drop does.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from . import apilink, pathpicker  # noqa: E402


def choose_folder(
    parent: Gtk.Widget | None,
    title: str,
    initial: str | None,
    on_done: Callable[[str], None],
) -> None:
    """Pick a folder of the service's machine, starting at *initial* (one
    of its folders, or None); *on_done(path)* with the pick. *parent* is a
    widget in the window the chooser belongs to (the window itself, a
    dialog, a popover's button)."""
    if apilink.is_local():
        dialog = Gtk.FileDialog(title=title, modal=True)
        # A `local` client's folders are this device's: only then is the
        # starting folder one the desktop's chooser can look for.
        if initial and os.path.isdir(initial):
            dialog.set_initial_folder(Gio.File.new_for_path(initial))
        root = parent.get_root() if parent is not None else None
        window = root if isinstance(root, Gtk.Window) else None

        def picked(dialog: Gtk.FileDialog, result) -> None:
            try:
                folder = dialog.select_folder_finish(result)
            except GLib.Error:
                return  # dismissed
            path = folder.get_path() if folder is not None else None
            if path:
                on_done(path)

        dialog.select_folder(window, None, picked)
        return
    pathpicker.pick_folder(parent, initial, on_done, title=title)
