# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""*Connect to…*: the connections this device knows and a form to add one
(split-service spec §3.24, D2, PR-3.1).

The list is `connections.Connections` over this device's `ui-state.json`
(`app.connections`): *This computer* first, then every ssh connection, the
one in use marked. *Connect* hands the selected one to
`App.switch_connection`, which detaches every tab without a dialog (the
sessions keep running where they are), resets the mirrors and reconnects
through `sshforward`; the window shows "Reconnecting to <name>" meanwhile.

*Add ssh connection* takes a name and ``user@host[:port]``. *Test* runs
`sshforward.print_socket` (``ssh -o BatchMode=yes TARGET collins-service
--print-socket``, 10 s) on a thread and shows the socket's path, or ssh's
last line with what exit 255 means under BatchMode (a key in the agent or
without a passphrase; the host already in known_hosts). Nothing typed here
is ever formatted into a shell line: `connections.parse_target` refuses a
target ssh could read as an option, and `sshforward` builds an argv.
"""

from __future__ import annotations

import logging
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import connections, sshforward  # noqa: E402
from .i18n import _  # noqa: E402

log = logging.getLogger(__name__)


class ConnectDialog(Adw.Dialog):
    """See the module docstring. *app* is the `App` (its `connections`,
    `current_connection` and `switch_connection`)."""

    def __init__(self, app) -> None:
        super().__init__(title=_("Connections"))
        self._app = app
        self._rows: list[tuple[Gtk.ListBoxRow, connections.Connection]] = []
        self._testing = False
        self._closed = False
        self.set_content_width(520)
        self.set_follows_content_size(True)

        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda *_a: self.close())
        header.pack_start(cancel)
        self.connect_btn = Gtk.Button(label=_("Connect"))
        self.connect_btn.add_css_class("suggested-action")
        self.connect_btn.set_sensitive(False)
        self.connect_btn.connect("clicked", lambda *_a: self._connect_selected())
        header.pack_end(self.connect_btn)

        body = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=24,
            margin_top=12,
            margin_bottom=24,
            margin_start=24,
            margin_end=24,
        )

        group = Adw.PreferencesGroup(
            title=_("Services"),
            description=_(
                "The Collins service this window shows. Switching detaches every tab; "
                "the sessions keep running where they are."
            ),
        )
        self.list = Gtk.ListBox()
        self.list.add_css_class("boxed-list")
        self.list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list.connect("row-selected", lambda *_a: self._sync_connect())
        self.list.connect("row-activated", lambda *_a: self._connect_selected())
        group.add(self.list)
        body.append(group)

        add = Adw.PreferencesGroup(
            title=_("Add ssh connection"),
            description=_(
                "ssh runs in batch mode: the key must be in your ssh agent or need no "
                "passphrase, and the host must already be in known_hosts. On the host, "
                "collins-service must be on the login shell's PATH."
            ),
        )
        self.name_row = Adw.EntryRow(title=_("Name"))
        self.target_row = Adw.EntryRow(title=_("user@host[:port]"))
        self.target_row.connect("entry-activated", lambda *_a: self._add())
        add.add(self.name_row)
        add.add(self.target_row)
        body.append(add)

        self.status = Gtk.Label(wrap=True, xalign=0.0, selectable=True)
        self.status.add_css_class("dim-label")
        self.status.set_visible(False)
        body.append(self.status)

        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        self.test_btn = Gtk.Button(label=_("Test"))
        self.test_btn.connect("clicked", lambda *_a: self._test())
        self.add_btn = Gtk.Button(label=_("Add"))
        self.add_btn.connect("clicked", lambda *_a: self._add())
        buttons.append(self.test_btn)
        buttons.append(self.add_btn)
        body.append(buttons)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(body)
        self.set_child(view)
        self.connect("closed", lambda *_a: setattr(self, "_closed", True))
        self._rebuild()

    # -- the list ----------------------------------------------------------------------

    def _rebuild(self) -> None:
        self.list.remove_all()
        self._rows = []
        current = self._app.current_connection
        for connection in [connections.LOCAL, *self._app.connections.all()]:
            row = Adw.ActionRow(
                title=GLib.markup_escape_text(
                    _("This computer") if connection.is_local else connection.name
                ),
                subtitle=GLib.markup_escape_text(
                    _("The Collins service on this machine") if connection.is_local
                    else connection.target
                ),
            )
            row.add_prefix(Gtk.Image.new_from_icon_name(
                "computer-symbolic" if connection.is_local else "network-server-symbolic"
            ))
            if connection.id == current.id:
                check = Gtk.Image.new_from_icon_name("object-select-symbolic")
                check.set_tooltip_text(_("Connected"))
                row.add_suffix(check)
            elif not connection.is_local:
                remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
                remove.add_css_class("flat")
                remove.set_tooltip_text(_("Remove"))
                remove.connect("clicked", lambda _b, c=connection: self._remove(c))
                row.add_suffix(remove)
            self.list.append(row)
            self._rows.append((row, connection))
        self._sync_connect()

    def select(self, connection_id: str) -> bool:
        for row, connection in self._rows:
            if connection.id == connection_id:
                self.list.select_row(row)
                return True
        return False

    def selected(self) -> connections.Connection | None:
        row = self.list.get_selected_row()
        for candidate, connection in self._rows:
            if candidate is row:
                return connection
        return None

    def _sync_connect(self) -> None:
        connection = self.selected()
        self.connect_btn.set_sensitive(
            connection is not None and connection.id != self._app.current_connection.id
        )

    def _remove(self, connection: connections.Connection) -> None:
        self._app.connections.remove(connection.id)
        self._rebuild()

    def _connect_selected(self) -> None:
        connection = self.selected()
        if connection is None or connection.id == self._app.current_connection.id:
            return
        self.close()
        self._app.switch_connection(connection)

    # -- the form ------------------------------------------------------------------------

    def _say(self, text: str, error: bool = False) -> None:
        self.status.set_text(text)
        self.status.set_visible(bool(text))
        if error:
            self.status.remove_css_class("dim-label")
            self.status.add_css_class("error")
        else:
            self.status.remove_css_class("error")
            self.status.add_css_class("dim-label")

    def _typed(self, need_name: bool) -> connections.Connection | None:
        """What the form holds as a connection, or None with the reason shown."""
        name = self.name_row.get_text().strip()
        target = self.target_row.get_text().strip()
        if connections.parse_target(target) is None:
            self._say(_("Enter the host as user@host or user@host:port."), error=True)
            return None
        if need_name and not connections.valid_name(name):
            self._say(_("Give the connection a name."), error=True)
            return None
        if need_name and any(c.name == name for c in self._app.connections.all()):
            self._say(_("A connection named {name} exists already.").format(name=name), error=True)
            return None
        # A Test with no name yet: the target stands in, cut to a name's
        # length (a valid target can be longer than NAME_MAX).
        return connections.new_ssh(name or target[: connections.NAME_MAX], target)

    def _test(self) -> None:
        if self._testing:
            return
        connection = self._typed(need_name=False)
        if connection is None:
            return
        self._testing = True
        self.test_btn.set_sensitive(False)
        self._say(_("Connecting to {target}…").format(target=connection.target))

        def work() -> None:
            result = sshforward.print_socket(connection)
            GLib.idle_add(lambda: self._tested(result), priority=GLib.PRIORITY_DEFAULT)

        threading.Thread(target=work, name="collins-ssh-test", daemon=True).start()

    def _tested(self, result: sshforward.PrintSocketResult) -> bool:
        self._testing = False
        if self._closed:
            return GLib.SOURCE_REMOVE
        self.test_btn.set_sensitive(True)
        if result.ok:
            self._say(_("A Collins service answers at {path}").format(path=result.path))
        else:
            text = result.text
            if result.batch_mode_hint:
                text = f"{text}\n{_(sshforward.MSG_BATCHMODE)}"
            self._say(text, error=True)
        return GLib.SOURCE_REMOVE

    def _add(self) -> None:
        connection = self._typed(need_name=True)
        if connection is None:
            return
        try:
            self._app.connections.add(connection)
        except ValueError as exc:
            if str(exc) == "full":
                self._say(_("No more connections can be added."), error=True)
            else:
                self._say(_("Enter the host as user@host or user@host:port."), error=True)
            return
        self.name_row.set_text("")
        self.target_row.set_text("")
        self._say(_("Added {name}.").format(name=connection.name))
        self._rebuild()
        self.select(connection.id)
