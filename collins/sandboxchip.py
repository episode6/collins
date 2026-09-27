# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The footer's *Sandboxed* chip: what a sandboxed session's box holds, the
workspace's allowed directories, and the restart that applies a change.

A `Gtk.MenuButton` built like the model chip beside it (a popover opening
upwards from the footer), showing the plan the session was *launched*
with — read back from the plan file (sandboxplan.load_plan), never
re-derived: the workspace, whether the GitHub CLI login and the SSH agent
were shared in, whether `~/.claude/settings.json` is protected. Under
that, the grants recorded for this project (sandboxplan.SandboxHost):
each with a remove button, and *Allow a directory…* through the file
chooser, held to the same guard a launch applies (a secret, an ancestor
of one, `$HOME`, `/`, Collins' own state — refused, with the reason in a
toast). A grant lands in state.json and, where the machine can do it, is
mounted into the running box at once (sandboxgrants.GrantMounts): its row
says *live*. One that couldn't be says *after restart*, and one the
launched plan binds that has since been taken back says *until restart*;
when the state's grants are not what the box holds, or a share changed, a
*Restart to apply* row runs the graceful exit and resumes the session in
the same tab (terminal.TerminalTab.restart_sandboxed). The *Sandboxed
shell* row opens a shell inside the same box — the answer to "what can
the agent see?".

Everything the chip decides is the host's (GTK-free, in sandboxplan and
sandboxgrants); this module only draws it. Absent from every unsandboxed
tab.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

from . import sandboxgrants, sandboxplan  # noqa: E402
from .formatting import display_path  # noqa: E402
from .i18n import _  # noqa: E402

# The shield glyph: the chip, and the tab icon of a sandboxed panel shell.
# A filled path in data/icons/hicolor/scalable/actions/.
ICON = "sandbox-shield-symbolic"

_CHIP_ICON_PX = 12

# How long an unbidden dock open waits after the event that asked for it
# (terminal._PR_PAGE_SETTLE_MS, kept here so this module imports no tab).
_DOCK_SETTLE_MS = 250


class SandboxChip(Gtk.MenuButton):
    """See the module docstring. The callables keep the chip free of any
    tab or app import: *plan_path* is the launched plan file (None until
    the launch settled), *host* the SandboxHost (None when the app never
    set one), *grants* the live grants (None likewise) and *box* the id of
    the box this session runs in, *can_restart* / *on_restart* the tab's
    restart, *on_open_shell* opens a sandboxed panel shell, *on_toast*
    floats a message."""

    def __init__(
        self,
        plan_path: Callable[[], str | None],
        host: Callable[[], sandboxplan.SandboxHost | None],
        grants: Callable[[], sandboxgrants.GrantMounts | None],
        box: Callable[[], str],
        can_restart: Callable[[], bool],
        on_restart: Callable[[], object],
        on_open_shell: Callable[[], object],
        on_toast: Callable[[str], object],
    ) -> None:
        super().__init__()
        self._plan_path = plan_path
        self._host = host
        self._grants = grants
        self._box = box
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
        mounts = self._live_grants()
        box = self._box()
        launched = list(inputs.get("grants", []))
        grants = host.grants(workspace) if host is not None else list(launched)
        # The state's grants, then the ones the launched plan binds that
        # have been taken back since — drawn only where a grant can arrive
        # live, since only there does the difference show.
        rows = [(grant, self._status(mounts, box, grant, launched)) for grant in grants]
        if mounts is not None:
            rows += [(grant, sandboxgrants.LEAVING) for grant in launched if grant not in grants]
        if not rows:
            self._content.append(_caption(_("None — the workspace only")))
        for grant, status in rows:
            delivery = mounts.delivery(box, grant) if mounts is not None else None
            self._content.append(self._grant_row(workspace, grant, status, delivery))
        allow = Gtk.Button(label=_("Allow a directory…"))
        allow.set_halign(Gtk.Align.START)
        allow.set_sensitive(host is not None)
        allow.connect("clicked", lambda *_a: self._pick_directory(workspace))
        self._content.append(allow)

        self._content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        # What is mounted into the running box counts as held, whether or
        # not the rows above are drawn from it.
        every = self._grants()
        live = every.live_paths(box) if every is not None else ()
        stale = host is not None and host.plan_stale(plan_path, workspace, live=live)
        if stale and self._can_restart():
            restart = Gtk.Button(label=_("Restart to apply"))
            restart.add_css_class("suggested-action")
            restart.set_halign(Gtk.Align.START)
            restart.set_tooltip_text(
                _("The session runs in a box built before the grants or shares changed; "
                  "exit it and resume it here with the new plan")
            )
            restart.connect("clicked", self._restart)
            self._content.append(restart)
        elif stale:
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

    def _live_grants(self) -> sandboxgrants.GrantMounts | None:
        """The live grants, when a grant can arrive live here and this
        session's box is one they know; None otherwise, and the rows are
        then drawn from the launched plan alone."""
        mounts = self._grants()
        if mounts is None or not mounts.registered(self._box()) or mounts.capable():
            return None
        return mounts

    @staticmethod
    def _status(mounts, box: str, grant: str, launched: list[str]) -> str:
        if mounts is not None:
            return mounts.status(box, grant)
        return sandboxgrants.STATIC if grant in launched else sandboxgrants.PENDING

    def _grant_row(self, workspace: str, grant: str, status: str, delivery=None) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        label = _path_label(grant)
        label.set_hexpand(True)
        row.append(label)
        if status == sandboxgrants.LIVE:
            tag = _caption(_("live"))
            tip = _(
                "Mounted into the running session. Restart the session to make it a plain bind."
            )
            if delivery is not None and not delivery.linked:
                tip += " " + _("Inside the sandbox at {inside}").format(inside=delivery.inside)
            tag.set_tooltip_text(tip)
            row.append(tag)
        elif status == sandboxgrants.PENDING:
            tag = _caption(_("after restart"))
            if delivery is not None and delivery.reason:
                tag.set_tooltip_text(delivery.reason)
            row.append(tag)
        elif status == sandboxgrants.LEAVING:
            # Bound by the plan the box runs on, and no longer granted:
            # nothing takes a bind out of a running box.
            label.add_css_class("dim-label")
            row.append(_caption(_("until restart")))
            return row
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
        mounts = self._grants()
        if mounts is not None:
            # Out of the running box, where it arrived live; the rows are
            # drawn again once it has gone.
            mounts.revoke(workspace, grant, lambda _gone: self._land(self._refresh))
        self._rebuild()

    def _land(self, call, *args) -> None:
        """Run *call* on the main loop. The live grants answer on their own
        thread; default priority, since an idle one starves under a
        display whose frame clock never goes quiet."""

        def landed() -> bool:
            call(*args)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(landed, priority=GLib.PRIORITY_DEFAULT)

    def _refresh(self) -> None:
        if self.get_popover().get_visible():
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
        if path:
            self.allow_directory(workspace, path)

    def allow_directory(self, workspace: str, path: str) -> None:
        """Grant *path* to this session's project and say what became of
        it: the guard's refusal, or — once the live grants have tried it in
        every running box of the project — where it stands in this one."""
        host = self._host()
        if host is None:
            return
        reason = host.allow(workspace, path)
        shown = display_path(path)
        if reason:
            self._on_toast(_("Can't allow {path}: {reason}").format(path=shown, reason=reason))
            self._refresh()
            return
        mounts = self._grants()
        if mounts is None:
            self._on_toast(_("Allowed {path} — restart the session to apply").format(path=shown))
            self._refresh()
            return
        path = os.path.normpath(path)
        mounts.allow(
            workspace, path, lambda delivered: self._land(self._on_delivered, path, delivered)
        )
        self._refresh()

    def _on_delivered(self, path: str, delivered: list) -> None:
        """The live grants tried *path* in every box of the project: one
        toast, by what became of it in this session's own."""
        box = self._box()
        mounts = self._grants()
        own = next((d for d in delivered if d.box == box), None)
        if own is None and mounts is not None:
            own = mounts.delivery(box, path)
        shown = display_path(path)
        if own is not None and own.status == sandboxgrants.LIVE:
            if own.linked:
                text = _("Allowed {path}").format(path=shown)
            else:
                text = _("Allowed {path} — inside the sandbox at {inside}").format(
                    path=shown, inside=own.inside
                )
        else:
            text = _("Allowed {path} — restart the session to apply").format(path=shown)
        self._on_toast(text)
        self._refresh()

    def _restart(self, *_args) -> None:
        self.get_popover().popdown()
        self._on_restart()

    def _open_shell(self, *_args) -> None:
        self.get_popover().popdown()
        # A beat after the popover's own teardown and focus restore, not an
        # idle inside it: opening a panel page is dock surgery, and doing
        # that from the cascade that announced the trigger has segfaulted
        # GTK's Wayland backend before (the footer-chip lesson in
        # AGENTS.md). The delay is the dock's own settle (terminal.
        # _PR_PAGE_SETTLE_MS), and it also keeps the shell's focus grab
        # from being undone by the popover's restore (see the GTK skill).
        def open_shell() -> bool:
            self._on_open_shell()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(_DOCK_SETTLE_MS, open_shell)


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
