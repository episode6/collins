# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The new-chat screen: what a fresh session's tab shows before its first
prompt is sent.

The project's icon and name sit centered over the native composer (see
composer.py) and a checkbox for the worktree launch; nothing is running
behind it yet. Send hands the text and the checkbox up to the tab, which
spawns the agent and types the prompt in once the CLI is at its input box
(TerminalTab.begin_session). The screen owns no launch plumbing and no
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
    checkbox out — the flag has no meaning outside a checkout.
    """

    __gsignals__ = {
        # The Send: the prompt, and whether the worktree box is ticked.
        "send-requested": (GObject.SignalFlags.RUN_FIRST, None, (str, bool)),
        # The text or the checkbox changed — what the draft's keeper debounces.
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
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("new-chat")
        self._cwd = cwd
        self._worktree_touched = False

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

        self.composer = ComposerView(
            pick_attach=pick_attach,
            file_reference=file_reference,
            notify=notify,
            chrome=False,
        )
        self.composer.add_css_class("new-chat-composer")
        self.composer.set_margin_top(24)
        self.composer.connect("send-requested", self._on_send)
        self.composer.connect("text-changed", lambda *_a: self.emit("changed"))
        column.append(self.composer)

        self._worktree = Gtk.CheckButton(label=_("Start in a new git worktree"))
        self._worktree.set_halign(Gtk.Align.START)
        self._worktree.set_margin_top(6)
        self._worktree.set_tooltip_text(
            _("Work in a fresh worktree of this project, apart from its uncommitted changes")
        )
        self._worktree.set_active(bool(worktree_default))
        self._worktree.set_visible(bool(is_git))
        self._worktree.connect("toggled", self._on_worktree_toggled)
        column.append(self._worktree)

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
        with_handler = self._worktree.get_active() != bool(choice)
        self._worktree.set_active(bool(choice))
        if not with_handler:
            self.emit("changed")

    # -- behaviour ------------------------------------------------------------

    def focus(self) -> None:
        self.composer.focus_view()

    def has_focus_within(self) -> bool:
        return self.composer.has_focus_within()

    def _on_worktree_toggled(self, *_a) -> None:
        self._worktree_touched = True
        self.emit("changed")

    def _on_send(self, _view, text: str) -> None:
        # Nothing but whitespace sends nothing: there is no box to close, so
        # the empty Send simply stays on the screen it was pressed on.
        if not text.strip():
            return
        self.emit("send-requested", text, self.worktree())


def is_git_checkout(cwd: str) -> bool:
    """Whether the worktree flag means anything in *cwd* — `.git` is a file
    in worktree checkouts, so either form counts (the window's own test)."""
    return (Path(cwd) / ".git").exists()
