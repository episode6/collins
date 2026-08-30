# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The new-chat screen: what a fresh session's tab shows before its first
prompt is sent.

The project's icon and name sit centered over the native composer (see
composer.py), with a checkbox for the worktree launch in its Send row;
nothing is running behind it yet. Send hands the text, the checkbox and
the model pick up to the tab, which spawns the agent and types the prompt
in once the CLI is at its input box (TerminalTab.begin_session). With
nothing in the box the button reads *Empty Session* instead, and sends all
the same: the agent starts with no prompt, sitting at its own input box,
the way a console launch always opened.

The model picker sits in the composer's own Send row too, where the
running session's switch menu sits, and chooses the ``--model`` that
launch passes.
It opens on
*Default* — the CLI's own default, named after what the CLI's settings
resolve it to (claudemodels.cli_default_model), so the screen says what a
plain launch would run on — and a pick here is for this launch alone: the
default is left as it was, and the tab is on the CLI's configured model
with nothing passed. A model the tab was opened with (a start_session
caller's) seeds the picker instead of the default. The effort picker
beside it chooses the
``--effort`` on the same terms (claudemodels.cli_default_effort names its
*Default*), and lists only the levels the model the launch will run on
takes. The screen owns no launch plumbing and no
persistence — it announces ``send-requested`` and ``changed`` and its host
(the tab, then the window) decides what those mean, the same division the
composer itself draws.

The composer here is built without its chrome (no close, no dock button):
on this screen it is the page rather than a stand-in raised over one.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, GObject, Gtk, Pango  # noqa: E402

from . import claudemodels, modelmenu  # noqa: E402
from .chats import is_chat_cwd  # noqa: E402
from .composer import ComposerView  # noqa: E402
from .formatting import display_path  # noqa: E402
from .i18n import _  # noqa: E402
from .projecticons import project_icon_data  # noqa: E402
from .sessions import project_name_for_cwd  # noqa: E402
from .svgtexture import svg_texture  # noqa: E402

# The project icon over the title, in logical pixels — the sidebar's icon
# writ large. Rasterized at twice that so a HiDPI display gets real pixels
# rather than an upscaled blur.
_ICON_PX = 72

# The content column: the composer wants room for a prompt, but a box that
# stretched across a wide monitor would be a text field nobody could read
# back; this is the terminal_max_width neighbourhood, narrowed a little.
_CLAMP_PX = 760


class NewChatView(Gtk.Box):
    """The screen itself (see module docstring).

    *cwd* is the directory the session will start in; *pick_attach*,
    *file_reference* and *notify* are the composer's injected callbacks,
    passed straight through (ComposerView says what each is for).
    *worktree_default* is the project's effective "new sessions use a
    worktree" value, which the checkbox starts on; *is_git* False leaves the
    checkbox out — the flag has no meaning outside a checkout. *model* is
    the ``--model`` the tab was opened with (a start_session caller's pick,
    "" for none), which the picker starts on; *pick_model* False leaves the
    picker out — the provider has no model flag. *effort* and *pick_effort*
    are the effort picker's pair, on the same terms.
    """

    __gsignals__ = {
        # The Send: the prompt ("" = an empty session, nothing typed in),
        # whether the worktree box is ticked, the model to launch with
        # ("" = the CLI's default, nothing passed), and the effort level
        # likewise.
        "send-requested": (GObject.SignalFlags.RUN_FIRST, None, (str, bool, str, str)),
        # The text, the checkbox, the model or the effort changed — what the
        # draft's keeper debounces.
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(
        self,
        cwd: str,
        pick_attach: Callable[[], None],
        file_reference: Callable[[str], str | None],
        notify: Callable[[str], None],
        worktree_default: bool,
        is_git: bool,
        model: str = "",
        pick_model: bool = True,
        effort: str = "",
        pick_effort: bool = True,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("new-chat")
        self._cwd = cwd
        self._worktree_touched = False
        self._model = (model or "").strip()
        self._pick_model = bool(pick_model)
        self._effort = (effort or "").strip()
        self._pick_effort = bool(pick_effort)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        column.set_valign(Gtk.Align.CENTER)
        column.set_margin_top(24)
        column.set_margin_bottom(24)
        column.set_margin_start(18)
        column.set_margin_end(18)

        chat = is_chat_cwd(cwd)
        icon = Gtk.Image(pixel_size=_ICON_PX, halign=Gtk.Align.CENTER)
        texture = None if chat else svg_texture(project_icon_data(cwd), _ICON_PX * 2)
        if texture is not None:
            icon.set_from_paintable(texture)
        else:
            icon.set_from_icon_name("chat-bubble-symbolic" if chat else "folder-symbolic")
            icon.add_css_class("dim-label")
        icon.add_css_class("new-chat-icon")
        icon.set_margin_bottom(12)
        column.append(icon)

        title = Gtk.Label(
            label=_("New chat") if chat else project_name_for_cwd(cwd),
            halign=Gtk.Align.CENTER,
            justify=Gtk.Justification.CENTER,
            wrap=True,
        )
        title.add_css_class("title-1")
        column.append(title)
        if not chat:
            subtitle = Gtk.Label(label=display_path(cwd), halign=Gtk.Align.CENTER)
            subtitle.add_css_class("dim-label")
            subtitle.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            subtitle.set_max_width_chars(60)
            column.append(subtitle)

        # The picker rides in the composer's Send row, on the button the
        # running session's switch menu would use — a MenuButton, which
        # re-measures its popover as the catalog fills in from a worker
        # thread while the menu is open.
        popover = (
            modelmenu.new_launch_model_popover(
                default_model=lambda: claudemodels.cli_default_model(self._cwd),
                choice=lambda: self._model,
                on_pick=self._on_model_picked,
            )
            if self._pick_model
            else None
        )
        # The effort picker's levels follow the model the launch will run
        # on: the pick, or the CLI's default when nothing is picked.
        effort_popover = (
            modelmenu.new_launch_effort_popover(
                default_effort=lambda: claudemodels.cli_default_effort(self._cwd),
                choice=lambda: self._effort,
                launch_model=self._launch_model,
                on_pick=self._on_effort_picked,
            )
            if self._pick_effort
            else None
        )
        self.composer = ComposerView(
            pick_attach=pick_attach,
            file_reference=file_reference,
            notify=notify,
            model_popover=popover,
            chrome=False,
            model_tooltip=_("Model to start this session on"),
            effort_popover=effort_popover,
            effort_tooltip=_("Effort level to start this session at"),
        )
        self.composer.add_css_class("new-chat-composer")
        self.composer.set_margin_top(24)
        self.composer.connect("send-requested", self._on_send)
        self.composer.connect("text-changed", self._on_text_changed)
        self._name_model()
        self._name_effort()
        self._name_send()
        column.append(self.composer)

        # The checkbox rides at the left of the Send row, across from the
        # pickers and the buttons: one row holds everything the launch is
        # sent with. Its label is short on purpose — two pickers' worth of
        # names sit across from it, and the tooltip carries the rest.
        self._worktree = Gtk.CheckButton(label=_("New git worktree"))
        self._worktree.set_valign(Gtk.Align.CENTER)
        self._worktree.set_tooltip_text(
            _("Work in a fresh worktree of this project, apart from its uncommitted changes")
        )
        self._worktree.set_active(bool(worktree_default))
        self._worktree.set_visible(bool(is_git))
        self._worktree.connect("toggled", self._on_worktree_toggled)
        self.composer.add_row_option(self._worktree)

        clamp = Adw.Clamp(child=column, maximum_size=_CLAMP_PX, tightening_threshold=_CLAMP_PX)
        clamp.set_vexpand(True)
        clamp.set_valign(Gtk.Align.FILL)
        scroller = Gtk.ScrolledWindow(child=clamp, vexpand=True, hexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.append(scroller)

    # -- the draft's parts ----------------------------------------------------

    def text(self) -> str:
        return self.composer.peek_text()

    def set_text(self, text: str) -> None:
        """Seed the box with a kept draft, cursor at the end."""
        self.composer.set_text(text)

    def worktree_choice(self) -> bool | None:
        """The checkbox as the user left it, or None while it still follows
        the project's default (what the draft record keeps — see
        newchat.draft_record)."""
        if not self._worktree.get_visible():
            return None
        return self._worktree.get_active() if self._worktree_touched else None

    def worktree(self) -> bool:
        """Whether the box is ticked right now (False with no box at all)."""
        return self._worktree.get_visible() and self._worktree.get_active()

    def set_worktree_choice(self, choice: bool | None) -> None:
        """Put a kept draft's checkbox back: an explicit choice ticks (or
        clears) the box and counts as touched; None leaves the default."""
        if choice is None:
            return
        self._worktree_touched = True
        # set_active fires "toggled" (and so "changed") only when the value
        # actually flips; a restored choice that happens to equal the box's
        # current state still has to announce itself, since the box just
        # became an explicit choice the draft record must keep.
        toggles = self._worktree.get_active() != bool(choice)
        self._worktree.set_active(bool(choice))
        if not toggles:
            self.emit("changed")

    def model(self) -> str:
        """The model the launch passes as --model: a picked id, or "" for
        the CLI's default (nothing passed) — also what the draft record
        keeps. Always "" without a picker."""
        return self._model if self._pick_model else ""

    def set_model(self, model: str | None) -> None:
        """Put a kept draft's pick back ("" or None = the default)."""
        model = (model or "").strip()
        if model == self._model:
            return
        self._model = model
        self._name_model()
        self.emit("changed")

    def effort(self) -> str:
        """The effort level the launch passes as --effort: a picked level,
        or "" for the CLI's default (nothing passed) — also what the draft
        record keeps. Always "" without a picker."""
        return self._effort if self._pick_effort else ""

    def set_effort(self, effort: str | None) -> None:
        """Put a kept draft's pick back ("" or None = the default)."""
        effort = (effort or "").strip()
        if effort == self._effort:
            return
        self._effort = effort
        self._name_effort()
        self.emit("changed")

    # -- behaviour ------------------------------------------------------------

    def focus(self) -> None:
        self.composer.focus_view()

    def has_focus_within(self) -> bool:
        return self.composer.has_focus_within()

    def _on_worktree_toggled(self, *_a) -> None:
        self._worktree_touched = True
        self.emit("changed")

    def _on_text_changed(self, *_a) -> None:
        self._name_send()
        self.emit("changed")

    def _name_send(self) -> None:
        """The Send button says what pressing it launches: *Send* with a
        prompt in the box, *Empty Session* with nothing in it (whitespace
        counts as nothing — see _on_send)."""
        if self.text().strip():
            self.composer.set_send_label(_("Send"))
        else:
            self.composer.set_send_label(
                _("Empty Session"), _("Start the session with no prompt")
            )

    def _on_model_picked(self, model: str) -> None:
        self.set_model(model)
        # A level the new model can't take would be an --effort the CLI
        # refuses: the pick falls back to the default rather than ride
        # along. A model the catalog can't speak for keeps it.
        allowed = claudemodels.model_efforts(self._launch_model() or "")
        if self._effort and allowed is not None and self._effort not in allowed:
            self.set_effort("")

    def _launch_model(self) -> str | None:
        """The model the launch will run on: the pick, else the CLI's own
        default as its settings name it (None when they don't)."""
        return self._model or claudemodels.cli_default_model(self._cwd)

    def _on_effort_picked(self, effort: str) -> None:
        self.set_effort(effort)

    def _name_effort(self) -> None:
        """The effort button reads what the launch will run at: the picked
        level's name, or Default with the CLI's own in brackets when the
        settings name one — re-read like the model's, for the same reason."""
        if self._effort:
            self.composer.set_effort_name(modelmenu.effort_label(self._effort))
        else:
            self.composer.set_effort_name(
                modelmenu.default_effort_label(claudemodels.cli_default_effort(self._cwd))
            )

    def _name_model(self) -> None:
        """The picker button reads what the launch will run on: the picked
        model's name, or Default with the CLI's own default in brackets when
        the settings name one (re-read here, so a screen coming back from a
        draft shows today's default, not the one it was opened over)."""
        if self._model:
            self.composer.set_model_name(modelmenu.model_label(self._model))
        else:
            self.composer.set_model_name(
                modelmenu.default_label(claudemodels.cli_default_model(self._cwd))
            )

    def _on_send(self, _view, text: str) -> None:
        # Enter (or Ctrl+Enter, as the setting has it) and the button come
        # through here alike. Nothing but whitespace is the empty session:
        # the agent is launched with no prompt at all rather than sent a
        # line of spaces.
        if not text.strip():
            text = ""
        self.emit("send-requested", text, self.worktree(), self.model(), self.effort())


def is_git_checkout(cwd: str) -> bool:
    """Whether the worktree flag means anything in *cwd* — `.git` is a file
    in worktree checkouts, so either form counts (the window's own test)."""
    return (Path(cwd) / ".git").exists()
