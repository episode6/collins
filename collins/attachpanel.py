# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The attachments panel: every image a session has seen, in one column.

`attachrecords` writes down each image a session puts on screen; this is
where that list is finally shown — a narrow column of previews sliding in
from the terminal's right edge, newest at the top, one row per picture.
Clicking a row opens the lightbox it came out of, with the caption the agent
gave it; right-clicking offers the picture to another app, to the file
manager, or to the clipboard, and can strike a row off the list.

Host-agnostic like `ComposerView`, and for the same reason: the view is
raised over the terminal today and will be a docked panel page tomorrow, so
it announces `close-requested` and takes what it can't know — whether the
session could open a file in its editor, how to say something to the
terminal, what forgetting a record means — as injected callbacks.

A row is a preview, and a preview is not a full-size decode: `pictures`
scales while decoding so a hundred screenshots cost a column's worth of
memory rather than a screenshot's worth each, and rows fill one at a time
from the idle loop so a panel opening on a long session appears at once and
fills top-down instead of freezing on ninety-nine decodes.

The list is a record of what the session saw, not a claim about what is
still there. A file that has been deleted keeps its row and draws a stand-in
— that is the honest answer, and the path in the tooltip is often the whole
reason someone opened the panel.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import pictures  # noqa: E402
from .attachrecords import Attachment  # noqa: E402
from .i18n import _  # noqa: E402

# How wide the panel rides over the terminal. Wide enough that a screenshot
# is recognizable, narrow enough to leave the terminal readable underneath —
# a docked page (PR C) takes the strip's own width instead.
PANEL_WIDTH = 280
# The tallest a preview grows. A portrait phone screenshot would otherwise
# be a whole panel's worth of one picture.
_THUMB_HEIGHT = 200


class AttachmentsView(Gtk.Box):
    """The panel widget itself (see module docstring).

    *open_image(attachment, path)* shows one: the host has the lightbox's
    editor gating in hand, and *path* is a local file — a remote image is
    downloaded before it is ever passed on. *forget(key)* drops a record
    from the session's list, and *notify(message)* is where the things that
    can only be said in words go (the terminal's feed_message).
    """

    __gsignals__ = {
        "close-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(
        self,
        open_image: Callable[[Attachment, str], None],
        forget: Callable[[str], None],
        notify: Callable[[str], None],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("attachments-panel")
        self._open_image = open_image
        self._forget = forget
        self._notify = notify
        self._records: dict[str, Attachment] = {}
        self._rows: dict[str, _Row] = {}
        # Rows built but not yet decoded, oldest first, and the idle that
        # works through them (0 when none is armed).
        self._pending: list[_Row] = []
        self._fill_source = 0

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.add_css_class("attachments-header")
        title = Gtk.Label(label=_("Attachments"), xalign=0.0, hexpand=True)
        title.add_css_class("heading")
        header.append(title)
        close = Gtk.Button(
            icon_name="window-close-symbolic",
            tooltip_text=_("Close the attachments panel"),
        )
        close.add_css_class("flat")
        close.connect("clicked", lambda *_a: self.emit("close-requested"))
        header.append(close)
        self.append(header)

        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._list.add_css_class("attachments-list")
        scroller = Gtk.ScrolledWindow(child=self._list, vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._stack = Gtk.Stack(vexpand=True)
        self._stack.add_named(scroller, "list")
        self._stack.add_named(_empty_state(), "empty")
        self._stack.set_visible_child_name("empty")
        self.append(self._stack)

        # One action group for the whole panel rather than one per row: a
        # context menu is built per right-click and names its row by key,
        # which is the one thing a record is identified by anyway.
        actions = Gio.SimpleActionGroup()
        for name, handler in (
            ("open-with", self._on_open_with),
            ("show-folder", self._on_show_folder),
            ("copy", self._on_copy),
            ("forget", self._on_forget),
        ):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", handler)
            actions.add_action(action)
        self.insert_action_group("attachments", actions)

        # Escape closes it, the way it closes the composer — but only from
        # inside: opening the panel deliberately leaves the keyboard in the
        # terminal (a gallery is for looking at, and what is typed while it
        # is up was typed at the agent), so this is the exit for someone who
        # tabbed into the rows.
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    def _on_key(self, _controller, keyval: int, _keycode: int, _state) -> bool:
        if keyval != Gdk.KEY_Escape:
            return False
        self.emit("close-requested")
        return True

    # -- the list --------------------------------------------------------------

    def set_records(self, attachments: list[Attachment]) -> None:
        """Show *attachments*, newest first, keeping the rows already up.

        Called on every sighting, so it diffs rather than rebuilds: a row
        whose key is still in the list keeps its widget, its decoded preview
        and its place in the scroll, and only the order and the labels are
        brought into line. A rebuild would scroll the panel back to the top
        and re-decode every picture each time a new one landed.
        """
        self._records = {one.key: one for one in attachments}
        for key in [key for key in self._rows if key not in self._records]:
            row = self._rows.pop(key)
            self._list.remove(row)
        previous: _Row | None = None
        for one in attachments:
            row = self._rows.get(one.key)
            if row is None:
                row = _Row(one, self)
                self._rows[one.key] = row
                self._list.append(row)
                self._pending.append(row)
                self._schedule_fill()
            else:
                row.update(one)
            if row.get_prev_sibling() is not previous:
                # None puts it at the head, which is where a new sighting of
                # an image seen before belongs.
                self._list.reorder_child_after(row, previous)
            previous = row
        self._stack.set_visible_child_name("list" if attachments else "empty")

    def _schedule_fill(self) -> None:
        if self._fill_source == 0:
            self._fill_source = GLib.idle_add(self._fill_next)

    def _fill_next(self) -> bool:
        """Decode one waiting row, then hand the loop back.

        A hundred decodes in one go is a frozen window; one per idle turn is
        a panel that paints immediately and fills from the top, which is
        where the pictures anyone opened the panel for are.
        """
        self._fill_source = 0
        while self._pending:
            row = self._pending.pop(0)
            if row.get_parent() is None:
                continue  # struck off the list while it waited its turn
            row.load()
            break
        if self._pending:
            self._schedule_fill()
        return GLib.SOURCE_REMOVE

    # -- opening ---------------------------------------------------------------

    def open(self, one: Attachment) -> None:
        """A row was activated: show it in the lightbox."""
        self._with_local_file(one, lambda path: self._open_image(one, path))

    def _with_local_file(self, one: Attachment, then: Callable[[str], None]) -> None:
        """Hand *then* a real file for *one*, downloading it if it is remote.

        A record keeps the URL an image came from, never the cache copy it
        landed in (those are pruned after a day), so everything that wants a
        file — the lightbox, another app — comes through here and gets one
        that exists right now. A local file that has since been deleted, and
        a download that fails, are both said out loud: the row is still
        there, so silence would read as a click that did nothing.
        """
        if not one.remote:
            if not os.path.isfile(one.key):
                self._notify(_("that image isn't on disk any more: {path}").format(path=one.key))
                return
            then(one.key)
            return

        def landed(path: Path | None, error: str | None) -> None:
            if self.get_root() is None:
                return  # the panel went away while the download ran
            if path is None:
                self._notify(
                    _("couldn't download that image: {reason}").format(
                        reason=error or one.key
                    )
                )
                return
            then(str(path))

        pictures.fetch(one.key, landed)

    # -- the context menu ------------------------------------------------------

    def popup_menu(self, row: _Row, one: Attachment, x: float, y: float) -> None:
        """The right-click menu for *one*, pointing at where it was clicked."""
        menu = Gio.Menu()
        menu.append_item(_item(_("Open With…"), "attachments.open-with", one.key))
        if not one.remote:
            # A remote image's own folder is the download cache, which is
            # nobody's idea of where that picture lives.
            menu.append_item(_item(_("Show in Folder"), "attachments.show-folder", one.key))
        menu.append_item(
            _item(
                _("Copy Address") if one.remote else _("Copy Path"),
                "attachments.copy",
                one.key,
            )
        )
        removal = Gio.Menu()
        # No confirm: this drops one record from a list, never a file, and
        # the next time the session shows that picture it comes back.
        removal.append_item(_item(_("Remove From List"), "attachments.forget", one.key))
        menu.append_section(None, removal)

        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(row)
        popover.set_has_arrow(False)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()

    def _on_open_with(self, _action, target: GLib.Variant) -> None:
        one = self._records.get(target.get_string())
        if one is not None:
            self._with_local_file(one, self._launch)

    def _launch(self, path: str) -> None:
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(path))
        launcher.set_always_ask(True)  # "another app": always show the chooser
        launcher.launch(self.get_root(), None, _launched)

    def _on_show_folder(self, _action, target: GLib.Variant) -> None:
        one = self._records.get(target.get_string())
        if one is None or one.remote:
            return
        if not os.path.exists(one.key):
            self._notify(_("that image isn't on disk any more: {path}").format(path=one.key))
            return
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(one.key))
        launcher.open_containing_folder(self.get_root(), None, None)

    def _on_copy(self, _action, target: GLib.Variant) -> None:
        self.get_clipboard().set(target.get_string())

    def _on_forget(self, _action, target: GLib.Variant) -> None:
        self._forget(target.get_string())


class _Row(Gtk.Button):
    """One image in the list: a preview with its one-line label under it.

    A button, not a box with a click controller: this is the panel's whole
    interaction, and a button is what gets the focus ring, the keyboard
    activation and the hover feedback for free. The right-click gesture sits
    beside its own — GtkButton only ever claims the primary one.
    """

    __gtype_name__ = "CollinsAttachmentRow"

    def __init__(self, one: Attachment, view: AttachmentsView) -> None:
        super().__init__()
        self.add_css_class("flat")
        self.add_css_class("attachment-row")
        self._view = view
        self._one = one
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._slot.add_css_class("attachment-thumb")
        box.append(self._slot)
        self._label = Gtk.Label(xalign=0.0)
        self._label.set_ellipsize(Pango.EllipsizeMode.END)
        self._label.add_css_class("attachment-caption")
        box.append(self._label)
        self.set_child(box)
        self.connect("clicked", lambda *_a: self._view.open(self._one))
        right_click = Gtk.GestureClick(button=3)
        right_click.connect("pressed", self._on_right_click)
        self.add_controller(right_click)
        self.update(one)

    def update(self, one: Attachment) -> None:
        """Re-label the row for a fresh sighting of the same image — the
        picture is the same file, so the preview is left alone."""
        self._one = one
        self._label.set_label(one.label)
        # The label is one ellipsized line, so the tooltip carries it whole,
        # over the path or URL it came from — which is half of what anyone
        # opens this panel to find. Plain text, never markup: a caption is
        # agent output and a path is whatever the file system holds.
        self.set_tooltip_text(
            one.key if one.key == one.label else f"{one.label}\n{one.key}"
        )

    def load(self) -> None:
        """Decode the preview (see AttachmentsView._fill_next for the when).

        A remote image is downloaded first, and both halves may take a
        moment, so the row stands empty until one of them lands — its label
        is up from the start, which is what the row is mostly read for.
        """
        if self._one.remote:
            pictures.fetch(self._one.key, self._remote_landed)
            return
        self._show(pictures.thumbnail(self._one.key, PANEL_WIDTH, _THUMB_HEIGHT))

    def _remote_landed(self, path: Path | None, error: str | None) -> None:
        if self.get_parent() is None:
            return  # struck off the list while the download ran
        self._show(
            pictures.thumbnail(path, PANEL_WIDTH, _THUMB_HEIGHT)
            if path is not None
            else None,
            error=error,
        )

    def _show(self, paintable, error: str | None = None) -> None:
        """Put the decoded preview in the slot, or a stand-in in its place."""
        old = self._slot.get_first_child()
        while old is not None:
            self._slot.remove(old)
            old = self._slot.get_first_child()
        if paintable is None:
            self._slot.append(_missing(error, remote=self._one.remote))
            return
        picture = pictures.BoundedPicture(paintable, max_height=_THUMB_HEIGHT)
        picture.set_halign(Gtk.Align.FILL)
        self._slot.append(picture)

    def _on_right_click(self, gesture: Gtk.GestureClick, _n, x: float, y: float) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._view.popup_menu(self, self._one, x, y)


def _missing(error: str | None, remote: bool) -> Gtk.Widget:
    """What stands in for a picture that isn't there: the record is a log of
    what the session saw, so the row stays and says why it can't show it."""
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    box.add_css_class("attachment-standin")
    icon = Gtk.Image.new_from_icon_name("image-missing-symbolic")
    icon.add_css_class("dim-label")
    box.append(icon)
    label = Gtk.Label(
        label=_("Couldn't be downloaded") if remote else _("No longer on disk"),
        xalign=0.0,
    )
    label.set_ellipsize(Pango.EllipsizeMode.END)
    label.add_css_class("dim-label")
    box.append(label)
    if error:
        box.set_tooltip_text(error)
    return box


def _empty_state() -> Gtk.Widget:
    """What a session that has shown nothing yet has to say for itself. The
    panel is offered before there is anything in it on purpose — a gallery
    nobody can find until it is already full is a gallery nobody finds."""
    box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=6,
        valign=Gtk.Align.CENTER,
        halign=Gtk.Align.CENTER,
    )
    box.add_css_class("attachments-empty")
    icon = Gtk.Image.new_from_icon_name("image-x-generic-symbolic")
    icon.set_pixel_size(32)
    icon.add_css_class("dim-label")
    box.append(icon)
    title = Gtk.Label(label=_("No images yet"))
    title.add_css_class("heading")
    box.append(title)
    subtitle = Gtk.Label(label=_("Pictures this session shows you collect here."))
    subtitle.set_wrap(True)
    subtitle.set_justify(Gtk.Justification.CENTER)
    subtitle.add_css_class("dim-label")
    box.append(subtitle)
    return box


def _item(label: str, action: str, key: str) -> Gio.MenuItem:
    """A menu item aimed at one record. The key travels as a target value
    rather than in a detailed action string, which would have to be quoted
    around a path nobody here chose."""
    item = Gio.MenuItem.new(label, None)
    item.set_action_and_target_value(action, GLib.Variant("s", key))
    return item


def _launched(launcher: Gtk.FileLauncher, result) -> None:
    try:
        launcher.launch_finish(result)
    except GLib.Error:
        pass  # no handler, or the user dismissed the chooser
