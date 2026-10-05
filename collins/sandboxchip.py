# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The footer's *Sandboxed* chip: what a sandboxed session's box holds, the
directories this session is allowed, and the restart that applies a change.

A `Gtk.MenuButton` built like the model chip beside it (a popover opening
upwards from the footer), showing the plan the session was *launched*
with — read back from the plan file (sandboxplan.load_plan), never
re-derived: the workspace, whether the GitHub CLI login and the SSH agent
were shared in, whether `~/.claude/settings.json` is protected. Under
that, two lists. The grants of **this session** (sandboxplan.SandboxHost,
by the session's box — a directory is allowed to one session and no
other): each with a remove button and a pin that makes it a default of
the project, and *Allow a directory…* through the file
chooser, held to the same guard a launch applies (a secret, an ancestor
of one, `$HOME`, `/`, Collins' own state — refused, with the reason in a
toast). And the **project's defaults**: what a *new* session of the
project starts allowed — a template copied into a session's list when
its box is minted, so marking or removing one changes no session that
exists, this one included. A grant lands in state.json and, where the machine can do it, is
mounted into the running box at once (sandboxgrants.GrantMounts): its row
says *live*. One that couldn't be says *after restart*, and one the
launched plan binds that has since been taken back says *until restart*;
when the state's grants are not what the box holds, or a share changed, a
*Restart to apply* row runs the graceful exit and resumes the session in
the same tab (terminal.TerminalTab.restart_sandboxed). Then the **session
tools** this session is offered, a check each, folded under a line that
counts them: every tool runs on the host, so a sandboxed session starts
with a short list and this is where one session's differs. The *Sandboxed
shell* row opens a shell inside the same box — the answer to "what can
the agent see?".

Everything the chip shows or changes is the service's to decide (split-
service spec §3.9, PR-1.11): it is a client of the `sandbox.*` requests
(`service.sandbox`, over the host and the live grants of sandboxplan and
sandboxgrants) and of the `sandbox` events that say what became of a grant
in the running box; this module only draws the answers. Absent from every
unsandboxed tab.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

from . import mcptools, sandboxgrants, sandboxplan, tokensettings  # noqa: E402
from .api.protocol import RequestRefused  # noqa: E402
from .formatting import display_path  # noqa: E402
from .i18n import _, translate  # noqa: E402

# The shield glyph: the chip, and the tab icon of a sandboxed panel shell.
# A filled path in data/icons/hicolor/scalable/actions/.
ICON = "sandbox-shield-symbolic"

# The pin on a session's row: the directory is a default of the project.
# From the icon theme (adwaita-icon-theme ships it), like list-remove.
PIN_ICON = "view-pin-symbolic"

_CHIP_ICON_PX = 12

# The most the unfolded tools list takes of the popover before it scrolls:
# about seven checks.
_TOOLS_MAX_HEIGHT = 210

# How long an unbidden dock open waits after the event that asked for it
# (terminal._PR_PAGE_SETTLE_MS, kept here so this module imports no tab).
_DOCK_SETTLE_MS = 250


class SandboxChip(Gtk.MenuButton):
    """See the module docstring. The callables keep the chip free of any
    tab or app import: *box* is the id of the box this session runs in
    ("" before the launch settled), *link* the connection to the service
    (`apilink.current`), *handle* the session's handle on the service (who
    asks: a restart is its own), *on_open_shell* opens a sandboxed panel shell,
    *on_toast* floats a message. Everything it shows or changes is a
    `sandbox.*` request the service decides (service.sandbox)."""

    def __init__(
        self,
        box: Callable[[], str],
        link: Callable[[], object],
        handle: Callable[[], str],
        on_open_shell: Callable[[], object],
        on_toast: Callable[[str], object],
    ) -> None:
        super().__init__()
        self._box = box
        self._link = link
        self._handle = handle
        self._on_open_shell = on_open_shell
        self._on_toast = on_toast
        # The paths an allow is waiting to hear about from the running box
        # (a `sandbox` event says what became of each).
        self._awaiting: set[str] = set()
        # Whether the tools list is unfolded: the popover is drawn again
        # at every change, and a list that folded itself at each tick
        # would be no list to work in.
        self._tools_expanded = False
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
        self._listening = None
        self.connect("realize", lambda *_a: self._listen())
        self.connect("destroy", lambda *_a: self._unlisten())

    # -- the service ------------------------------------------------------------

    def _listen(self) -> None:
        link = self._link()
        if link is not None and self._listening is None:
            link.on("sandbox", self._on_event)
            self._listening = link

    def _unlisten(self) -> None:
        if self._listening is not None:
            self._listening.off("sandbox", self._on_event)
            self._listening = None

    def _ask(self, message: dict) -> dict | None:
        """One `sandbox.*` request for this session's box; None when there
        is no box, no link, or the service refused (the refusal, then, in
        `self._refusal`)."""
        self._refusal = ""
        box = self._box()
        link = self._link()
        if not sandboxplan.valid_box_id(box) or link is None:
            return None
        self._listen()
        try:
            return link.call({**message, "box": box, "handle": self._handle()})
        except RequestRefused as refusal:
            self._refusal = translate(refusal.msgid, refusal.details)
            return None

    # -- the popover ----------------------------------------------------------

    def _clear(self) -> None:
        while (child := self._content.get_first_child()) is not None:
            self._content.remove(child)

    def _rebuild(self) -> None:
        self._clear()
        reply = self._ask({"t": "sandbox.plan"})
        plan = sandboxplan.checked_plan(reply.get("plan")) if reply is not None else None
        if plan is None:
            self._content.append(_caption(_("The sandbox plan for this session can't be read")))
            return
        drawn = self._ask({"t": "sandbox.grants"}) or {}
        inputs = plan["inputs"]
        workspace = inputs["workspace"]

        title = Gtk.Label(label=_("Sandboxed"), xalign=0.0)
        title.add_css_class("heading")
        self._content.append(title)
        self._content.append(_path_label(workspace))
        # A launch narrowed to its worktree: the checkout it started in is
        # inside too, and can't be written.
        launch_dir = sandboxplan.plan_launch_dir(plan)
        if launch_dir:
            row = _caption(_("Read-only: {path}").format(path=display_path(launch_dir)))
            row.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            row.set_max_width_chars(48)
            row.set_tooltip_text(launch_dir)
            self._content.append(row)
        for text in (
            _("GitHub CLI login: shared")
            if inputs.get("share_gh")
            else _("GitHub CLI login: not shared"),
            _("SSH agent: shared") if inputs.get("share_ssh") else _("SSH agent: not shared"),
            _("Settings and hooks: read-only")
            if inputs.get("protect_settings")
            else _("Settings and hooks: writable"),
        ):
            self._content.append(_caption(text))
        # What was to be read-only and isn't: a symlink can't be pinned.
        for path in sandboxplan.plan_unpinned(plan):
            row = _caption(_("Writable, a symlink: {path}").format(path=display_path(path)))
            row.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            row.set_max_width_chars(48)
            row.set_tooltip_text(path)
            self._content.append(row)

        self._content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        heading = Gtk.Label(label=_("Allowed directories"), xalign=0.0)
        heading.add_css_class("caption-heading")
        self._content.append(heading)
        self._content.append(_caption(_("For this session only")))
        defaults = list(drawn.get("defaults") or [])
        rows = list(drawn.get("grants") or [])
        if not rows:
            self._content.append(_caption(_("None — the workspace only")))
        hosted = bool(drawn.get("hosted"))
        for row in rows:
            self._content.append(self._grant_row(workspace, row, defaults, hosted))
        allow = Gtk.Button(label=_("Allow a directory…"))
        allow.set_halign(Gtk.Align.START)
        allow.set_sensitive(bool(drawn.get("hosted")))
        allow.connect("clicked", lambda *_a: self._pick_directory(workspace))
        self._content.append(allow)

        # What a new session of the project starts allowed. Nothing here
        # touches the list above, or any session that exists.
        self._content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        heading = Gtk.Label(label=_("New sessions of this project"), xalign=0.0)
        heading.add_css_class("caption-heading")
        self._content.append(heading)
        if not defaults:
            self._content.append(_caption(_("None")))
        for path in defaults:
            self._content.append(self._default_row(workspace, path))

        self._content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        self._content.append(self._tools_section(drawn))

        self._content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        stale = bool(drawn.get("stale"))
        if stale and drawn.get("can_restart"):
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

    def _grant_row(self, workspace: str, drawn: dict, defaults: list[str], hosted: bool) -> Gtk.Widget:
        grant = drawn.get("path", "")
        status = drawn.get("status", "")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        label = _path_label(grant)
        label.set_hexpand(True)
        row.append(label)
        if status == sandboxgrants.LIVE:
            tag = _caption(_("live"))
            tip = _(
                "Mounted into the running session. Restart the session to make it a plain bind."
            )
            if drawn.get("linked") is False and drawn.get("inside"):
                tip += " " + _("Inside the sandbox at {inside}").format(inside=drawn["inside"])
            tag.set_tooltip_text(tip)
            row.append(tag)
        elif status == sandboxgrants.PENDING:
            tag = _caption(_("after restart"))
            if drawn.get("reason"):
                tag.set_tooltip_text(drawn["reason"])
            row.append(tag)
        elif status == sandboxgrants.LEAVING:
            # Bound by the plan the box runs on, and no longer granted:
            # nothing takes a bind out of a running box.
            label.add_css_class("dim-label")
            row.append(_caption(_("until restart")))
            return row
        row.append(self._pin(workspace, grant, defaults, hosted))
        remove = Gtk.Button.new_from_icon_name("list-remove-symbolic")
        remove.add_css_class("flat")
        remove.set_valign(Gtk.Align.CENTER)
        remove.set_tooltip_text(_("Stop allowing this directory"))
        remove.connect("clicked", lambda *_a: self._revoke(grant))
        row.append(remove)
        return row

    def _pin(self, workspace: str, grant: str, defaults: list[str], hosted: bool) -> Gtk.ToggleButton:
        """The toggle that makes a directory this session holds a default
        of its project: what new sessions start allowed."""
        pinned = os.path.normpath(grant) in defaults
        pin = Gtk.ToggleButton(icon_name=PIN_ICON, active=pinned)
        pin.add_css_class("flat")
        pin.set_valign(Gtk.Align.CENTER)
        pin.set_sensitive(hosted)
        pin.set_tooltip_text(
            _("Allowed in new sessions of this project")
            if pinned
            else _("Allow in new sessions of this project")
        )
        pin.connect("toggled", self._on_pin_toggled, workspace, grant)
        return pin

    def _default_row(self, workspace: str, path: str) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        label = _path_label(path)
        label.set_hexpand(True)
        row.append(label)
        remove = Gtk.Button.new_from_icon_name("list-remove-symbolic")
        remove.add_css_class("flat")
        remove.set_valign(Gtk.Align.CENTER)
        remove.set_tooltip_text(_("Stop allowing this directory in new sessions"))
        remove.connect("clicked", lambda *_a: self.set_project_default(workspace, path, False))
        row.append(remove)
        return row

    def _tools_section(self, drawn: dict) -> Gtk.Widget:
        """The session tools this session is offered: a check per tool,
        folded away under a line that counts them. Every tool runs on the
        host, outside the box, so a sandboxed session starts with a short
        list (Preferences → Sandbox) and this is where one session's
        differs from it."""
        names = mcptools.tool_names()
        tools = drawn.get("tools") or {}
        available = drawn.get("available") or {}
        offered = [name for name in names if tools.get(name)]
        expander = Gtk.Expander(
            label=_("Session tools: {on} of {all} on").format(on=len(offered), all=len(names))
        )
        expander.set_expanded(self._tools_expanded)
        expander.connect("notify::expanded", self._on_tools_expanded)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        body.set_margin_top(4)
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        # A list that scrolls: thirteen checks under everything else the
        # popover holds would be taller than a small window.
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_propagate_natural_height(True)
        scroller.set_propagate_natural_width(True)
        scroller.set_max_content_height(_TOOLS_MAX_HEIGHT)
        scroller.set_child(rows)
        body.append(scroller)
        for name in names:
            title, subtitle = tokensettings.mcp_tool_label(name)
            check = Gtk.CheckButton(label=title)
            on_here = bool(available.get(name))
            check.set_active(name in offered)
            check.set_sensitive(on_here and sandboxplan.valid_box_id(self._box()))
            check.set_tooltip_text(
                subtitle
                if on_here
                else _("{name} is switched off for every session in Preferences").format(name=name)
            )
            check.connect("toggled", self._on_tool_toggled, name)
            rows.append(check)
        body.append(_caption(_("Off is refused at once; on reaches the session when it restarts")))
        if drawn.get("overridden"):
            reset = Gtk.Button(label=_("Use the defaults"))
            reset.add_css_class("flat")
            reset.set_halign(Gtk.Align.START)
            reset.set_tooltip_text(
                _("Offer this session what Preferences → Sandbox offers sandboxed sessions")
            )
            reset.connect("clicked", lambda *_a: self.reset_tools())
            body.append(reset)
        expander.set_child(body)
        return expander

    # -- actions ---------------------------------------------------------------

    def _on_tools_expanded(self, expander: Gtk.Expander, _pspec) -> None:
        self._tools_expanded = expander.get_expanded()

    def _on_tool_toggled(self, check: Gtk.CheckButton, name: str) -> None:
        self.set_tool(name, check.get_active())

    def set_tool(self, name: str, on: bool) -> bool:
        """Offer this session the tool *name*, or stop offering it —
        this session's box, and no other; whether the state says so now.
        A refusal is a toast."""
        reply = self._ask({"t": "sandbox.tools", "tools": {name: bool(on)}})
        if reply is None and self._refusal:
            self._on_toast(self._refusal)
        self._refresh()
        return reply is not None

    def reset_tools(self) -> None:
        """This session is offered what the defaults say again."""
        self._ask({"t": "sandbox.tools", "reset": True})
        self._refresh()

    def _on_pin_toggled(self, pin: Gtk.ToggleButton, workspace: str, grant: str) -> None:
        self.set_project_default(workspace, grant, pin.get_active())

    def set_project_default(self, workspace: str, path: str, on: bool) -> bool:
        """Make *path* a default of this session's project, or stop it
        being one; whether the list says so now. A refusal is a toast. The
        session's own list, and every other session's, is left as it is."""
        kind = "sandbox.allow" if on else "sandbox.revoke"
        reply = self._ask({"t": kind, "path": path, "scope": "project"})
        if reply is None and self._refusal:
            self._on_toast(
                _("Can't make {path} a default: {reason}").format(
                    path=display_path(path), reason=self._refusal
                )
            )
        # Drawn again either way: the pin goes back after a refusal, and
        # the list below follows the toggle.
        self._refresh()
        return reply is not None

    def _revoke(self, grant: str) -> None:
        # Out of the running box, where it arrived live; the rows are drawn
        # again once it has gone (the `sandbox` event).
        self._ask({"t": "sandbox.revoke", "path": grant, "scope": "session"})
        self._rebuild()

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
        """Grant *path* to this session — its box, and no other — and say
        what became of it: the guard's refusal, or, once the live grants
        have tried it in the running box, where it stands there (the
        service decides both; *workspace* is the plan's, which the service
        reads for itself)."""
        shown = display_path(path)
        path = os.path.normpath(path)
        self._awaiting.add(path)
        reply = self._ask({"t": "sandbox.allow", "path": path, "scope": "session"})
        if reply is None:
            self._awaiting.discard(path)
            if self._refusal:
                self._on_toast(_("Can't allow {path}: {reason}").format(path=shown, reason=self._refusal))
            self._refresh()
            return
        if not reply.get("live"):
            self._awaiting.discard(path)
            self._on_toast(_("Allowed {path} — restart the session to apply").format(path=shown))
        self._refresh()

    def _on_event(self, event: dict) -> None:
        """The service's `sandbox` event: a grant of this session's box
        landed in the running box, or left it."""
        if event.get("box") != self._box():
            return
        path = event.get("path") or ""
        if path in self._awaiting and not event.get("revoked"):
            self._awaiting.discard(path)
            self._on_delivered(path, event.get("delivery"))
            return
        self._refresh()

    def _on_delivered(self, path: str, delivery: dict | None) -> None:
        """The live grants tried *path* in this session's box: one toast,
        by what became of it there."""
        shown = display_path(path)
        if delivery is not None and delivery.get("status") == sandboxgrants.LIVE:
            if delivery.get("linked"):
                text = _("Allowed {path}").format(path=shown)
            else:
                text = _("Allowed {path} — inside the sandbox at {inside}").format(
                    path=shown, inside=delivery.get("inside", "")
                )
        else:
            text = _("Allowed {path} — restart the session to apply").format(path=shown)
        self._on_toast(text)
        self._refresh()

    def _restart(self, *_args) -> None:
        self.get_popover().popdown()
        self._ask({"t": "sandbox.restart"})

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
