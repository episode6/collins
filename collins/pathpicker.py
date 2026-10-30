# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The path picker: a folder chooser over the service's files (split-service
spec §3.11, §3.24; PR-3.2, D60).

A client that is not on the service's machine cannot hand the desktop's
own chooser a folder of the service's, so the seven sites that pick one
(`choosers.choose_folder`: a new session's project folder, *Add project*,
continue, new chat, the clone destination in its dialog and in
Preferences, a sandbox grant) open this instead. Folders only (D56):
*Attach file* picks a file of this device's and uploads it.

An `Adw.Dialog`: *Cancel* and *Open* in the header; the path entry (a
path of the service's, `~` its home, expanded by the service) with *Up*,
*Show hidden* and *New folder* beside it; the shortcuts (the service's
home, the project roots the store lists) on the left; the folder's
subfolders on the right, filtered by the name being typed, a click moving
into one (a link to a folder too, marked). Tab completes the name. A
listing the service cut says so in a last, inert row; one it refused
shows its words in place of the list. *Open* (or Enter) asks the
service's `fs.stat` whether the typed or navigated path is a folder and
hands it to *on_done* as the service echoed it, never resolved; the
dialog closes then, and only then.

It browses the whole filesystem, as the native dialog does, over the two
requests that are the picker's own and unconfined (D60: `fs.dirs`,
`fs.newdir`); it never sends the editor's confined `fs.list` or
`fs.mkdir`. The model is `pathpickermodel`'s and GTK-free; every request
runs off the main loop (`remotefiles.off_main`) and lands at
`GLib.PRIORITY_DEFAULT`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import apilink, remotefiles  # noqa: E402
from . import pathpickermodel as model  # noqa: E402
from .i18n import N_, _  # noqa: E402
from .sessions import worktree_project_root  # noqa: E402

log = logging.getLogger(__name__)


def project_roots() -> list[str]:
    """The project folders of the sessions the store lists (the app's
    mirror, read with no round trip): a worktree's project, else the
    session's cwd; the chats aside."""
    store = getattr(Gio.Application.get_default(), "store", None)
    if store is None:
        return []
    roots: list[str] = []
    for index in range(store.model.get_n_items()):
        item = store.model.get_item(index)
        if getattr(item, "group_key", ("proj",))[0] != "proj":
            continue
        cwd = getattr(item.session, "cwd", None)
        if cwd:
            roots.append(worktree_project_root(cwd) or cwd)
    return roots


def _row(icon: str, text: str, mark: str | None = None) -> Gtk.Box:
    box = Gtk.Box(spacing=8, margin_top=4, margin_bottom=4, margin_start=6, margin_end=6)
    box.append(Gtk.Image.new_from_icon_name(icon))
    label = Gtk.Label(label=text, xalign=0.0, hexpand=True)
    label.set_ellipsize(Pango.EllipsizeMode.END)
    box.append(label)
    if mark:
        box.append(Gtk.Image.new_from_icon_name(mark))
    return box


class PathPicker(Adw.Dialog):
    """See the module docstring. *on_done(path)* is called once, with the
    folder picked; a dismissed picker calls nothing."""

    def __init__(
        self,
        title: str,
        initial: str | None,
        on_done: Callable[[str], None],
        accept_label: str | None = None,
    ) -> None:
        super().__init__(title=title)
        self._on_done = on_done
        self._home = apilink.service_home()
        self._listing = model.Listing()
        self._debounce: int | None = None
        self._quiet = False  # the entry's text set by the picker, not typed
        self._opening = False
        self._closed = False
        self.set_content_width(640)
        self.set_content_height(520)

        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda *_a: self.close())
        header.pack_start(cancel)
        self.open_btn = Gtk.Button(label=accept_label or _("Open"))
        self.open_btn.add_css_class("suggested-action")
        self.open_btn.connect("clicked", lambda *_a: self._open())
        header.pack_end(self.open_btn)

        self.entry = Gtk.Entry(hexpand=True)
        self.entry.set_placeholder_text(_("A folder on the service's machine"))
        self.entry.connect("changed", self._on_changed)
        self.entry.connect("activate", lambda *_a: self._open())
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.entry.add_controller(keys)

        self.up_btn = Gtk.Button(icon_name="go-up-symbolic", tooltip_text=_("Enclosing folder"))
        self.up_btn.connect("clicked", lambda *_a: self._go_up())
        # Off at every open and never saved (§3.24): the request's `hidden`.
        self.hidden_btn = Gtk.ToggleButton(
            icon_name="view-reveal-symbolic", tooltip_text=_("Show hidden folders")
        )
        self.hidden_btn.connect("toggled", lambda *_a: self._relist())
        self.new_btn = Gtk.MenuButton(icon_name="folder-new-symbolic", tooltip_text=_("New folder"))
        self.new_btn.set_popover(self._new_folder_popover())

        bar = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=12, margin_end=12)
        bar.append(self.up_btn)
        bar.append(self.entry)
        bar.append(self.hidden_btn)
        bar.append(self.new_btn)

        self.places = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.places.add_css_class("navigation-sidebar")
        self.places.connect("row-activated", self._on_place)
        places_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.places)
        places_scroll.set_size_request(170, -1)

        self.rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.rows.add_css_class("navigation-sidebar")
        self.rows.connect("row-activated", self._on_row)
        self.status = Adw.StatusPage(icon_name="folder-symbolic")
        self.status.add_css_class("compact")
        self.stack = Gtk.Stack(hexpand=True)
        self.stack.add_named(
            Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.rows, vexpand=True), "rows"
        )
        self.stack.add_named(self.status, "status")

        panes = Gtk.Box(vexpand=True)
        panes.append(places_scroll)
        panes.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        panes.append(self.stack)

        self.note = Gtk.Label(xalign=0.0, wrap=True, margin_start=12, margin_end=12, margin_bottom=8)
        self.note.add_css_class("dim-label")
        self.note.set_visible(False)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(bar)
        body.append(Gtk.Separator())
        body.append(panes)
        body.append(self.note)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(body)
        self.set_child(view)
        self.connect("closed", self._on_closed)

        self._fill_places()
        self.go(initial if initial and model.well_formed(initial) else (self._home or "~"))
        self.set_focus(self.entry)

    # -- moving about --------------------------------------------------------------------

    def go(self, folder: str) -> None:
        """Show *folder*: the entry says it, and its subfolders are asked
        for at once (no debounce: a click, a shortcut, *Up*)."""
        self._cancel_debounce()
        self._set_text(model.entry_text(folder))
        self._list(model.split(model.entry_text(folder))[0], "")

    def _go_up(self) -> None:
        above = model.parent(self._listing.path or "")
        if above is not None:
            self.go(above)

    def _relist(self) -> None:
        self._cancel_debounce()
        self._list_for_text()

    def _on_place(self, _box: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        path = getattr(row, "path", None)
        if path:
            self.go(path)

    def _on_row(self, _box: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        folder = self._listing.path
        name = getattr(row, "folder", None)
        if folder and name:
            self.go(model.child(folder, name))

    def _set_text(self, text: str) -> None:
        self._quiet = True
        try:
            self.entry.set_text(text)
            self.entry.set_position(-1)
        finally:
            self._quiet = False

    # -- typing --------------------------------------------------------------------------

    def _on_changed(self, _entry: Gtk.Entry) -> None:
        self.note.set_visible(False)
        if self._quiet:
            return
        self._cancel_debounce()
        self._debounce = GLib.timeout_add(model.LIST_DEBOUNCE_MS, self._on_debounced)

    def _on_debounced(self) -> bool:
        self._debounce = None
        self._list_for_text()
        return GLib.SOURCE_REMOVE

    def _cancel_debounce(self) -> None:
        if self._debounce is not None:
            GLib.source_remove(self._debounce)
            self._debounce = None

    def _list_for_text(self) -> None:
        where = model.split(self.entry.get_text())
        if where is None:
            self._show_status(_("Type a full path"), _("A path on the service's machine starts with / or ~"))
            return
        self._list(*where)

    def _list(self, folder: str, prefix: str) -> None:
        hidden = self.hidden_btn.get_active()
        if not self._listing.needs(folder, hidden):
            if self._listing.pending:
                # This folder's answer is on its way: nothing to show yet
                # (`Listing.ask` dropped the previous folder's rows).
                self._show_status(_("Listing…"), "", icon="content-loading-symbolic")
            else:
                self._show_rows(prefix)
            return
        token = self._listing.ask(folder, hidden)
        self._show_status(_("Listing…"), "", icon="content-loading-symbolic")
        remotefiles.off_main(
            lambda: model.list_folders(folder, hidden),
            lambda status, value: self._listed(token, folder, status, value),
            name="path-picker-list",
        )

    def _listed(self, token: int, asked: str, status: str, value: object) -> None:
        if self._closed:
            return
        if status == "ok":
            path, rows, truncated = value
            landed = self._listing.land(token, rows, path=path, truncated=truncated)
        else:
            landed = self._listing.land(token, None, error=remotefiles.refusal_words(value))
        if not landed:
            return
        text = self.entry.get_text()
        if status == "ok" and text.strip() == model.entry_text(asked) and path != asked:
            # Untouched since the move: the entry shows the folder as the
            # service listed it (a `~` expanded there, §3.24).
            self._set_text(model.entry_text(path))
            self._listing.folder = model.split(model.entry_text(path))[0]
            text = self.entry.get_text()
        where = model.split(text)
        self._show_rows(where[1] if where is not None and where[0] == self._listing.folder else "")

    def _show_rows(self, prefix: str) -> None:
        listing = self._listing
        if listing.error:
            # The service's words in place of the list, as the native
            # dialog shows a folder it cannot read.
            self._show_status(_("Can't list this folder"), listing.error, icon="folder-symbolic")
            return
        self.rows.remove_all()
        shown = model.matching(listing.rows, prefix)
        for folder in shown:
            row = Gtk.ListBoxRow()
            row.folder = folder.name
            row.set_child(
                _row("folder-symbolic", folder.name, "emblem-symbolic-link-symbolic" if folder.link else None)
            )
            if folder.link:
                row.set_tooltip_text(_("A link to a folder"))
            self.rows.append(row)
        if listing.truncated:
            last = Gtk.ListBoxRow(activatable=False, selectable=False)
            note = Gtk.Label(label=_("Only the first folders are listed"), xalign=0.0, margin_start=6)
            note.add_css_class("dim-label")
            last.set_child(note)
            self.rows.append(last)
        if not shown and not listing.truncated:
            self._show_status(_("No folders here") if not prefix else _("No folder starts with that"), "")
            return
        self.stack.set_visible_child_name("rows")

    def _show_status(self, title: str, description: str, icon: str = "folder-symbolic") -> None:
        self.status.set_title(title)
        self.status.set_description(GLib.markup_escape_text(description) if description else None)
        self.status.set_icon_name(icon)
        self.stack.set_visible_child_name("status")

    def _say(self, text: str) -> None:
        self.note.set_text(text)
        self.note.set_visible(True)

    def _on_key(self, _controller, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        if keyval not in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab) or state & Gdk.ModifierType.SHIFT_MASK:
            return False
        text = self.entry.get_text()
        where = model.split(text)
        listing = self._listing
        if where is None or where[0] != listing.folder or listing.pending or listing.error:
            # The rows are not of the folder typed yet (or are still on their
            # way): ask now if need be, and let the next Tab complete
            # against them.
            self._cancel_debounce()
            self._list_for_text()
            return True
        completed = model.complete(text, self._listing.rows)
        if completed is not None:
            self._set_text(completed)
            self._list_for_text()
        return True

    # -- the shortcuts -------------------------------------------------------------------

    def _fill_places(self) -> None:
        self.places.remove_all()
        for label, path in model.shortcuts(self._home, project_roots()):
            row = Gtk.ListBoxRow()
            row.path = path
            home = not label
            icon = "user-home-symbolic" if home else "folder-symbolic"
            row.set_child(_row(icon, _("Home") if home else label))
            row.set_tooltip_text(path)
            self.places.append(row)

    # -- new folder ----------------------------------------------------------------------

    def _new_folder_popover(self) -> Gtk.Popover:
        popover = Gtk.Popover()
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6,
            margin_top=6, margin_bottom=6, margin_start=6, margin_end=6,
        )
        title = Gtk.Label(label=_("New folder"), xalign=0.0)
        title.add_css_class("heading")
        self.new_entry = Gtk.Entry(placeholder_text=_("Folder name"), width_chars=24)
        self.new_entry.connect("activate", lambda *_a: self._make_folder())
        self.new_note = Gtk.Label(xalign=0.0, wrap=True, max_width_chars=30)
        self.new_note.add_css_class("error")
        self.new_note.set_visible(False)
        self.create_btn = Gtk.Button(label=_("Create"), halign=Gtk.Align.END)
        self.create_btn.add_css_class("suggested-action")
        self.create_btn.connect("clicked", lambda *_a: self._make_folder())
        for child in (title, self.new_entry, self.new_note, self.create_btn):
            box.append(child)
        popover.set_child(box)
        popover.connect("show", lambda *_a: self._reset_new_folder())
        return popover

    def _reset_new_folder(self) -> None:
        self.new_entry.set_text("")
        self.new_note.set_visible(False)
        self.create_btn.set_sensitive(True)
        GLib.idle_add(lambda: self.new_entry.grab_focus() and False)

    def _make_folder(self) -> None:
        name = self.new_entry.get_text()
        error = model.new_folder_error(name)
        folder = self._listing.path
        if error is None and not folder:
            error = N_("List a folder first")
        if error is not None:
            self.new_note.set_text(_(error))
            self.new_note.set_visible(True)
            return
        self.create_btn.set_sensitive(False)
        remotefiles.off_main(
            lambda: model.make_folder(folder, name),
            lambda status, value: self._made(status, value),
            name="path-picker-newdir",
        )

    def _made(self, status: str, value: object) -> None:
        if self._closed:
            return
        self.create_btn.set_sensitive(True)
        if status != "ok":
            self.new_note.set_text(remotefiles.refusal_words(value))
            self.new_note.set_visible(True)
            return
        self.new_btn.popdown()
        self._listing.folder = None  # the folder it went in has a new row
        self.go(value)  # the picker enters the folder it made (§3.24)

    # -- open ----------------------------------------------------------------------------

    def _open(self) -> None:
        if self._opening:
            return
        text = self.entry.get_text().strip()
        if not model.well_formed(text):
            self._say(_("Type a full path: one that starts with / or ~"))
            return
        self._opening = True
        self.open_btn.set_sensitive(False)
        path = text.rstrip("/") or "/"
        remotefiles.off_main(
            lambda: model.stat_folder(path),
            lambda status, value: self._opened(path, status, value),
            name="path-picker-open",
        )

    def _opened(self, path: str, status: str, value: object) -> None:
        if self._closed:
            return
        self._opening = False
        self.open_btn.set_sensitive(True)
        if status != "ok":
            self._say(remotefiles.refusal_words(value))
            return
        if value is None:
            self._say(_("There is no folder at {path}").format(path=path))
            return
        done = self._on_done
        self.close()
        done(value)

    def _on_closed(self, *_a) -> None:
        self._closed = True
        self._cancel_debounce()


def pick_folder(
    parent: Gtk.Widget | None,
    initial: str | None,
    on_done: Callable[[str], None],
    title: str | None = None,
    accept_label: str | None = None,
) -> PathPicker:
    """Open the picker over *parent* (a widget in the window it belongs
    to), starting at *initial* (a folder of the service's; its home when
    None); *on_done(path)* with the folder picked. The shape of the seven
    folder sites (§3.24), through `choosers.choose_folder`."""
    picker = PathPicker(title or _("Choose a folder"), initial, on_done, accept_label=accept_label)
    picker.present(parent)
    return picker
