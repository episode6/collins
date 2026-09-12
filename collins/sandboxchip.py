# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The footer's *Sandboxed* chip: what a sandboxed session's box holds, the
workspace's allowed directories, and the restart that applies a change.

A `Gtk.MenuButton` built like the model chip beside it (a popover opening
upwards from the footer), showing the plan the session was *launched*
with — read back from the plan file (sandboxplan.load_plan), never
re-derived: the workspace, whether the GitHub CLI login and the SSH agent
were shared in, whether `~/.claude/settings.json` is protected. Under
that, the grants recorded for this workspace (sandboxplan.SandboxHost):
each with a remove button, and *Allow a directory…* through the file
chooser, held to the same guard a launch applies (a secret, an ancestor
of one, `$HOME`, `/`, Collins' own state — refused, with the reason in a
toast). A grant lands in state.json and applies at the next launch; when
the plan the state would build now differs from the launched one, a
*Restart to apply* row runs the graceful exit and resumes the session in
the same tab (terminal.TerminalTab.restart_sandboxed). The *Sandboxed
shell* row opens a shell inside the same box — the answer to "what can
the agent see?".

Everything the chip decides is the host's (GTK-free, in sandboxplan);
this module only draws it. Absent from every unsandboxed tab.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

from . import sandboxplan  # noqa: E402
from .formatting import display_path  # noqa: E402
from .i18n import _  # noqa: E402

# The shield glyph: the chip, and the tab icon of a sandboxed panel shell.
# A filled path in data/icons/hicolor/scalable/actions/.
ICON = "sandbox-shield-symbolic"

_CHIP_ICON_PX = 12


class SandboxChip(Gtk.MenuButton):
    """See the module docstring. The callables keep the chip free of any
    tab or app import: *plan_path* is the launched plan file (None until
    the launch settled), *host* the SandboxHost (None when the app never
    set one), *can_restart* / *on_restart* the tab's restart, *on_open_shell*
    opens a sandboxed panel shell, *on_toast* floats a message."""

    def __init__(
        self,
        plan_path: Callable[[], str | None],
        host: Callable[[], sandboxplan.SandboxHost | None],
        can_restart: Callable[[], bool],
        on_restart: Callable[[], object],
        on_open_shell: Callable[[], object],
        on_toast: Callable[[str], object],
    ) -> None:
        super().__init__()
        self._plan_path = plan_path
        self._host = host
        self._can_restart = can_restart
        self._on_restart = on_restart
        self._on_open_shell = on_open_shell
        self._on_toast = on_toast
        self.add_css_class("flat")
        icon = Gtk.Image.new_from_icon_name(ICON)
        icon.set_pixel_size(_CHIP_ICON_PX)
        icon.add_css_class("dim-label")
        label = Gtk.Label(label=_("Sandboxed"))
        label.add_css_class("caption")
        label.add_css_class("dim-label")
        face = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        face.append(icon)
        face.append(label)
        self.set_child(face)
        self.set_tooltip_text(_("This session runs inside a sandbox — click to see what is inside"))
        # Filled on every show, so the grants list and the restart row
        # track the state; a MenuButton re-measures its popover as the
        # content changes, where a hand-parented one would not.
        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._content.set_margin_top(6)
        self._content.set_margin_bottom(6)
        self._content.set_margin_start(6)
        self._content.set_margin_end(6)
        popover = Gtk.Popover()
        popover.set_position(Gtk.PositionType.TOP)
        popover.set_child(self._content)
        popover.connect("show", lambda *_a: self._rebuild())
        self.set_popover(popover)

    # -- the popover ----------------------------------------------------------

    def _clear(self) -> None:
        while (child := self._content.get_first_child()) is not None:
            self._content.remove(child)

    def _rebuild(self) -> None:
        self._clear()
        plan_path = self._plan_path()
        plan = sandboxplan.load_plan(plan_path)
        if plan is None:
            self._content.append(_caption(_("The sandbox plan for this session can't be read")))
            return
        inputs = plan["inputs"]
        workspace = inputs["workspace"]

        title = Gtk.Label(label=_("Sandboxed"), xalign=0.0)
        title.add_css_class("heading")
        self._content.append(title)
        self._content.append(_path_label(workspace))
        for text in (
            _("GitHub CLI login: shared")
            if inputs.get("share_gh")
            else _("GitHub CLI login: not shared"),
            _("SSH agent: shared") if inputs.get("share_ssh") else _("SSH agent: not shared"),
            _("~/.claude/settings.json: protected")
            if inputs.get("protect_settings")
            else _("~/.claude/settings.json: editable"),
        ):
            self._content.append(_caption(text))

        self._content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        heading = Gtk.Label(label=_("Allowed directories"), xalign=0.0)
        heading.add_css_class("caption-heading")
        self._content.append(heading)
        host = self._host()
        grants = host.grants(workspace) if host is not None else list(inputs.get("grants", []))
        launched = set(inputs.get("grants", []))
        if not grants:
            self._content.append(_caption(_("None — the workspace only")))
        for grant in grants:
            self._content.append(self._grant_row(workspace, grant, applied=grant in launched))
        allow = Gtk.Button(label=_("Allow a directory…"))
        allow.set_halign(Gtk.Align.START)
        allow.set_sensitive(host is not None)
        allow.connect("clicked", lambda *_a: self._pick_directory(workspace))
        self._content.append(allow)

        self._content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        if host is not None and self._can_restart() and host.plan_stale(plan_path, workspace):
            restart = Gtk.Button(label=_("Restart to apply"))
            restart.add_css_class("suggested-action")
            restart.set_halign(Gtk.Align.START)
            restart.set_tooltip_text(
                _("The session runs in a box built before the grants or shares changed; "
                  "exit it and resume it here with the new plan")
            )
            restart.connect("clicked", self._restart)
            self._content.append(restart)
        elif host is not None and host.plan_stale(plan_path, workspace):
            # Stale, but this tab can't restart itself: a fork (it holds its
            # origin's id), a sibling running its parent's derived plan, or
            # a session whose id hasn't resolved yet. Say so rather than
            # leave the "after restart" tags above unexplained.
            self._content.append(
                _caption(
                    _("The sandbox changed since this session started — "
                      "this session can't apply it from here")
                )
            )
        shell = Gtk.Button(label=_("Sandboxed shell"))
        shell.set_halign(Gtk.Align.START)
        shell.set_tooltip_text(_("Open a shell inside this session's sandbox"))
        shell.connect("clicked", self._open_shell)
        self._content.append(shell)

    def _grant_row(self, workspace: str, grant: str, applied: bool) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        label = _path_label(grant)
        label.set_hexpand(True)
        row.append(label)
        if not applied:
            row.append(_caption(_("after restart")))
        remove = Gtk.Button.new_from_icon_name("list-remove-symbolic")
        remove.add_css_class("flat")
        remove.set_valign(Gtk.Align.CENTER)
        remove.set_tooltip_text(_("Stop allowing this directory"))
        remove.connect("clicked", lambda *_a: self._revoke(workspace, grant))
        row.append(remove)
        return row

    # -- actions ---------------------------------------------------------------

    def _revoke(self, workspace: str, grant: str) -> None:
        host = self._host()
        if host is not None:
            host.revoke(workspace, grant)
        self._rebuild()

    def _pick_directory(self, workspace: str) -> None:
        """*Allow a directory…*: the desktop's folder chooser, then the guard.
        The chooser is modal over the window and closes this popover; the
        outcome — allowed, or refused with the reason — goes out as a toast
        either way, so the answer is seen without reopening the chip."""
        dialog = Gtk.FileDialog(title=_("Allow a directory"), modal=True)
        dialog.set_initial_folder(Gio.File.new_for_path(workspace))
        root = self.get_root()
        window = root if isinstance(root, Gtk.Window) else None
        dialog.select_folder(window, None, self._on_directory_picked, workspace)

    def _on_directory_picked(self, dialog: Gtk.FileDialog, result, workspace: str) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return  # cancelled
        path = folder.get_path() if folder is not None else None
        if not path:
            return
        host = self._host()
        if host is None:
            return
        reason = host.allow(workspace, path)
        shown = display_path(path)
        if reason:
            self._on_toast(_("Can't allow {path}: {reason}").format(path=shown, reason=reason))
        else:
            self._on_toast(_("Allowed {path} — restart the session to apply").format(path=shown))
        if self.get_popover().get_visible():
            self._rebuild()

    def _restart(self, *_args) -> None:
        self.get_popover().popdown()
        self._on_restart()

    def _open_shell(self, *_args) -> None:
        self.get_popover().popdown()
        # After the popover's own focus restore has run, so the shell's
        # focus grab isn't undone by it (see the GTK skill).
        def open_shell() -> bool:
            self._on_open_shell()
            return GLib.SOURCE_REMOVE

        GLib.idle_add(open_shell, priority=GLib.PRIORITY_DEFAULT)


def _caption(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0.0)
    label.add_css_class("caption")
    label.add_css_class("dim-label")
    return label


def _path_label(path: str) -> Gtk.Label:
    """A path in the popover: shortened as the footer shows paths, its tail
    kept when it is too long, the full path in the tooltip."""
    label = Gtk.Label(label=display_path(path), xalign=0.0)
    label.set_ellipsize(Pango.EllipsizeMode.START)
    label.set_max_width_chars(48)
    label.set_tooltip_text(path)
    return label
