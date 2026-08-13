# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The composer: a GUI prompt box for the agent's terminal.

A `ComposerView` is a spell-checked multi-line text box with Send, attach
and close buttons — everything the CLI's own input box isn't when a prompt
outgrows one line. It owns no terminal plumbing at all: it announces
``send-requested`` / ``close-requested`` and its host (terminal.py's overlay
today, a dock panel page later) decides what those mean — cut text out of
the CLI's box on the way in, type it back or submit it on the way out.

libspelling is a hard dependency, the same bargain as GtkSourceView (which
the spell-check adapter here is built for): nothing degrades without it, and
a missing typelib exits with an install hint instead of a traceback — the
`.deb` and the AUR package both pull it in; only a source checkout can hit
this.

The box matches the terminal's font on purpose: the text is going to *be*
terminal text a moment later, and a composer drawn in the UI font would read
as a different place rather than a better view of the same one.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, GObject, Gtk, Pango  # noqa: E402

try:
    gi.require_version("Spelling", "1")
    from gi.repository import Spelling
except (ValueError, ImportError):
    raise SystemExit(
        "Collins requires libspelling, which isn't installed. Install it "
        "(Debian/Ubuntu: gir1.2-spelling-1, Fedora/Arch: libspelling) "
        "and relaunch."
    ) from None

from . import composerkeys, dropimages  # noqa: E402
from .editor import GtkSource  # noqa: E402
from .i18n import _  # noqa: E402

# The prview composer's "grows with the text, then scrolls" bounds, a little
# taller: prompts run longer than PR comments.
_MIN_CONTENT_HEIGHT = 64
_MAX_CONTENT_HEIGHT = 240

_ESCAPE_KEYVAL = 0xFF1B  # GDK_KEY_Escape


class ComposerView(Gtk.Box):
    """The composer widget itself, host-agnostic (see module docstring).

    *pick_attach* is the attach button's click, injected by the host because
    picking a file needs the session's cwd and provider — the pick lands
    back here through `insert_mention`.
    """

    __gsignals__ = {
        "send-requested": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "close-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # The chrome's dock/float toggle; what docking means is the host's
        # business, like every other signal here.
        "dock-toggle-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, pick_attach: Callable[[], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("composer-panel")
        self._enter_sends = True
        self._font_provider: Gtk.CssProvider | None = None

        self._buffer = GtkSource.Buffer()
        # No language, no brackets: this is prose bound for a prompt, and
        # the one GtkSource behavior wanted from the buffer is that
        # libspelling's adapter is built for it.
        self._buffer.set_highlight_matching_brackets(False)
        self._view = GtkSource.View(buffer=self._buffer)
        self._view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._view.set_monospace(True)
        self._view.set_top_margin(6)
        self._view.set_bottom_margin(6)
        self._view.set_left_margin(8)
        self._view.set_right_margin(8)
        self._view.set_accepts_tab(False)

        self._adapter = Spelling.TextBufferAdapter.new(
            self._buffer, Spelling.Checker.get_default()
        )
        self._view.set_extra_menu(self._adapter.get_menu_model())
        self._view.insert_action_group("spelling", self._adapter)
        self._adapter.set_enabled(True)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self._view.add_controller(keys)

        scroller = Gtk.ScrolledWindow(child=self._view)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_has_frame(True)
        scroller.set_min_content_height(_MIN_CONTENT_HEIGHT)
        scroller.set_max_content_height(_MAX_CONTENT_HEIGHT)
        scroller.set_propagate_natural_height(True)
        self._scroller = scroller
        self.append(scroller)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        attach = Gtk.Button(
            icon_name="mail-attachment-symbolic",
            tooltip_text=_("Attach file"),
        )
        attach.add_css_class("flat")
        attach.connect("clicked", lambda *_a: pick_attach())
        row.append(attach)
        row.append(Gtk.Box(hexpand=True))
        self._dock_btn = Gtk.Button()
        self._dock_btn.add_css_class("flat")
        self._dock_btn.connect(
            "clicked", lambda *_a: self.emit("dock-toggle-requested")
        )
        row.append(self._dock_btn)
        close = Gtk.Button(
            icon_name="window-close-symbolic",
            tooltip_text=_("Close composer and keep the text in the terminal"),
        )
        close.add_css_class("flat")
        close.connect("clicked", lambda *_a: self.emit("close-requested"))
        row.append(close)
        send = Gtk.Button(label=_("Send"))
        send.add_css_class("suggested-action")
        send.connect("clicked", lambda *_a: self.emit("send-requested", self.peek_text()))
        row.append(send)
        self.append(row)
        self._docked = False
        self.set_docked(False)

    # -- text ------------------------------------------------------------------

    def set_text(self, text: str) -> None:
        """Seed the box (the cut CLI prompt on open), cursor at the end."""
        self._buffer.set_text(text)
        self._buffer.place_cursor(self._buffer.get_end_iter())

    def peek_text(self) -> str:
        return self._buffer.get_text(
            self._buffer.get_start_iter(), self._buffer.get_end_iter(), True
        )

    def take_text(self) -> str:
        """Read and clear the box — closing and sending both empty it."""
        text = self.peek_text()
        self._buffer.set_text("")
        return text

    def insert_mention(self, text: str) -> None:
        """Insert mention token(s) at the cursor, spaced off a half-written
        word the same way the terminal's drop path is (dropimages)."""
        cursor = self._buffer.get_iter_at_mark(self._buffer.get_insert())
        line_start = cursor.copy()
        line_start.set_line_offset(0)
        before = self._buffer.get_text(line_start, cursor, True)
        space = dropimages.leading_space(before, dropimages.cell_width(before))
        self._buffer.insert_at_cursor(space + text)
        self.focus_view()

    # -- behavior --------------------------------------------------------------

    def set_enter_sends(self, enter_sends: bool) -> None:
        self._enter_sends = bool(enter_sends)

    def set_docked(self, docked: bool) -> None:
        """Dress the widget for its host: docked (a panel page below the
        terminal) drops the floating card's rounded top and grows the text
        view to fill the page, since a panel tab has real height to give
        where the overlay only borrowed the terminal's bottom edge. The
        chrome's toggle button swaps meaning with the mode."""
        self._docked = bool(docked)
        if self._docked:
            self.add_css_class("docked")
            self._scroller.set_max_content_height(-1)
            self._scroller.set_vexpand(True)
            self._dock_btn.set_icon_name("view-restore-symbolic")
            self._dock_btn.set_tooltip_text(_("Float the composer over the terminal"))
        else:
            self.remove_css_class("docked")
            self._scroller.set_max_content_height(_MAX_CONTENT_HEIGHT)
            self._scroller.set_vexpand(False)
            self._dock_btn.set_icon_name("go-bottom-symbolic")
            self._dock_btn.set_tooltip_text(_("Dock the composer below the terminal"))

    def set_font(self, font: str) -> None:
        """Match the terminal's font setting; "" (VTE's default) falls back
        to the view's own monospace flag. The editor's per-view CSS provider
        pattern (editor._apply_font)."""
        if self._font_provider is not None:
            self._view.get_style_context().remove_provider(self._font_provider)
            self._font_provider = None
        if not font:
            return
        desc = Pango.FontDescription.from_string(font)
        size = desc.get_size() / Pango.SCALE
        unit = "pt" if not desc.get_size_is_absolute() else "px"
        css = f'textview {{ font-family: "{desc.get_family()}"; font-size: {size}{unit}; }}'
        provider = Gtk.CssProvider()
        provider.load_from_string(css)
        self._view.get_style_context().add_provider(
            provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._font_provider = provider

    def focus_view(self) -> None:
        self._view.grab_focus()

    def has_focus_within(self) -> bool:
        root = self.get_root()
        focus = root.get_focus() if root else None
        return focus is not None and (focus is self or focus.is_ancestor(self))

    def _on_key(self, _controller, keyval: int, _keycode: int, state) -> bool:
        if keyval == _ESCAPE_KEYVAL:
            self.emit("close-requested")
            return True
        action = composerkeys.enter_action(int(keyval), int(state), self._enter_sends)
        if action == composerkeys.SEND:
            self.emit("send-requested", self.peek_text())
            return True
        if action == composerkeys.NEWLINE:
            # Inserted by hand for every newline chord: the view would take
            # a bare Enter itself, but Ctrl+Enter it ignores and Shift+Enter
            # it treats as a bare one — one path keeps the three identical.
            self._buffer.insert_at_cursor("\n")
            self._view.scroll_to_mark(self._buffer.get_insert(), 0.0, False, 0.0, 0.0)
            return True
        return False


class ComposerPage(Adw.Bin):
    """The composer as a dock panel page (panelstrip's PanelPage protocol).

    A thin wrapper around the one *live* `ComposerView` — docking reparents
    the view between the overlay revealer and this bin, never rebuilds it,
    so text, cursor and undo history ride along. `page_state` persists the
    placement only: a restored layout gets a fresh empty composer, drafts
    are not written to disk.

    *on_closed(page)* fires when the page's tab really closes (the strip's
    `page_closed` hook — an X, a bulk close, the chrome's close button
    routed through the dock), while the view is still inside: the host
    rescues it back to the overlay and applies paste-back semantics there.
    """

    page_kind = "composer"

    def __init__(self, view: ComposerView, on_closed: Callable[[ComposerPage], None]) -> None:
        super().__init__()
        self._on_closed = on_closed
        self.set_child(view)

    def take_view(self) -> ComposerView:
        """Detach and return the live view (undock, or close-time rescue)."""
        view = self.get_child()
        self.set_child(None)
        return view

    # -- PanelPage protocol ----------------------------------------------------

    def page_title(self) -> str:
        return _("Composer")

    def page_icon(self) -> str | None:
        return "document-edit-symbolic"

    def grab_page_focus(self) -> None:
        view = self.get_child()
        if view is not None:
            view.focus_view()

    def has_page_focus(self) -> bool:
        view = self.get_child()
        return view.has_focus_within() if view is not None else False

    def page_busy(self) -> bool:
        # An unsent draft is deliberately not "busy": closing doesn't lose
        # it — page_closed pastes it back into the agent's input box, the
        # same road the overlay's close takes — so no confirm is owed.
        return False

    def apply_settings(self, settings: dict) -> None:
        view = self.get_child()
        if view is None:
            return
        view.set_font(settings.get("font") or "")
        view.set_enter_sends(bool(settings.get("composer_enter_sends", True)))

    def page_state(self) -> dict:
        return {"kind": "composer"}

    def page_closed(self) -> None:
        self._on_closed(self)
