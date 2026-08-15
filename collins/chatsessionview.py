# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-15. Full change history: git log for this file.

"""A chat tab backed by a live headless `claude -p` stream-json session.

Renders the session as chat bubbles with token-by-token streaming (the payoff of
driving the headless channel instead of tailing the transcript). The chat can do
real work — every tool use (Edit / Write / Bash / …) pauses for an explicit
approval card, so nothing touches the project without the user clicking Allow.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from .chatbubbles import (  # noqa: E402
    arm_tool_chip,
    make_bubble,
    make_label,
    make_tool_chip,
    set_bubble_text,
)
from .chatsession import Event, make_chat_session  # noqa: E402
from .copylabel import copy_tooltip, enable_copy_on_click  # noqa: E402
from .formatting import display_path  # noqa: E402
from .gitinfo import current_branch  # noqa: E402
from .i18n import _  # noqa: E402
from .providers import ChatVariant, Provider  # noqa: E402
from .scrolling import at_bottom, bottom  # noqa: E402


class ChatSessionTab(Gtk.Box):
    """A live chat conversation with a headless agent process."""

    def __init__(
        self,
        cwd: str | None,
        provider: Provider,
        variant: ChatVariant,
        resume_session_id: str = "",
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.provider = provider
        self.variant = variant
        self._assistant_label: Gtk.Label | None = None
        self._assistant_buf = ""
        self._typing: Gtk.Widget | None = None
        self._stick = True
        self._scroll_source: int | None = None
        self._ended = False
        self._always_allowed: set[str] = set()
        self._pending_card: Gtk.Widget | None = None
        # Tool chips shown at content_block_start, before their input had
        # streamed; the turn's full assistant message retrofits file paths
        # onto them (see the "tool_input" event).
        self._unarmed_chips: list[tuple[str, Gtk.Widget]] = []

        info = Gtk.Label(label=self._banner_text(), xalign=0.0)
        info.add_css_class("dim-label")
        info.set_margin_top(6)
        info.set_margin_start(12)
        info.set_margin_end(12)
        self.append(info)

        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._list.set_margin_top(10)
        self._list.set_margin_bottom(10)
        self._list.set_margin_start(12)
        self._list.set_margin_end(12)
        self._scroller = Gtk.ScrolledWindow(vexpand=True, child=self._list)
        self._scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        vadj = self._scroller.get_vadjustment()
        vadj.connect("value-changed", self._on_scroll)
        vadj.connect("notify::upper", self._on_upper_changed)
        self.append(self._scroller)

        self.append(self._build_compose())
        if cwd:
            self.append(self._build_footer(cwd))

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        if resume_session_id:
            self._notice(_("Continuing the previous session — earlier messages aren't shown here."))

        # The session calls back on its reader thread → hop to the GTK main loop.
        self._session = make_chat_session(
            provider, variant, cwd, lambda ev: GLib.idle_add(self._on_event, ev), resume_session_id
        )
        self.connect("unrealize", lambda *_: self._session.close())
        self._session.start()
        GLib.idle_add(self._focus_entry)

    def _focus_entry(self) -> bool:
        # Wrapper: grab_focus() returns True, which idle_add would treat as
        # SOURCE_CONTINUE — re-focusing (and select-all-ing) the entry forever.
        self._entry.grab_focus()
        return GLib.SOURCE_REMOVE

    def _banner_text(self) -> str:
        name = self.provider.name
        if not self.variant.writeable:
            return _("Read-only chat with {name} — analyses and answers, never edits.").format(name=name)
        if self.variant.gated:
            return _("Chat with {name} — every file edit and command asks your permission first.").format(
                name=name
            )
        return _("Chat with {name} — ⚠ runs edits and commands automatically, without asking.").format(
            name=name
        )

    def _build_footer(self, cwd: str) -> Gtk.Widget:
        """Slim status row showing the directory (and branch) the chat session
        works in."""
        label = Gtk.Label(label=display_path(cwd), xalign=0.0)
        label.set_ellipsize(Pango.EllipsizeMode.START)
        label.set_tooltip_text(copy_tooltip(cwd))
        label.add_css_class("caption")
        label.add_css_class("dim-label")
        enable_copy_on_click(label, lambda: cwd)
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.add_css_class("tab-footer")
        footer.append(label)
        branch = current_branch(cwd)
        if branch:
            # dividers flanking the branch label; the 8px box spacing on
            # each side of them matches the footer's own 8px edge padding
            footer.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
            branch_label = Gtk.Label(label=f"⎇ {branch}")
            branch_label.set_ellipsize(Pango.EllipsizeMode.END)
            branch_label.set_max_width_chars(24)
            branch_label.set_tooltip_text(copy_tooltip(branch))
            branch_label.add_css_class("caption")
            branch_label.add_css_class("dim-label")
            enable_copy_on_click(branch_label, lambda: branch, lambda b: f"⎇ {b}")
            footer.append(branch_label)
            footer.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        return footer

    # -- compose ---------------------------------------------------------------

    def _build_compose(self) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.set_margin_start(12)
        row.set_margin_end(12)
        row.set_margin_top(6)
        row.set_margin_bottom(12)
        placeholder = _("Ask {name}…").format(name=self.provider.name)
        self._entry = Gtk.Entry(hexpand=True, placeholder_text=placeholder)
        self._entry.connect("activate", lambda *_: self._send())
        self._send_btn = Gtk.Button(icon_name="document-send-symbolic", tooltip_text=_("Send"))
        self._send_btn.add_css_class("suggested-action")
        self._send_btn.connect("clicked", lambda *_: self._send())
        self._stop_btn = Gtk.Button(icon_name="process-stop-symbolic", tooltip_text=_("Stop"), visible=False)
        self._stop_btn.connect("clicked", lambda *_: self._session.interrupt())
        row.append(self._entry)
        row.append(self._stop_btn)
        row.append(self._send_btn)
        return row

    def _on_key(self, _ctrl, keyval, _keycode, state) -> bool:
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter) and not (
            state & Gdk.ModifierType.SHIFT_MASK
        ):
            if self._entry.get_text().strip():
                self._send()
                return True
        return False

    def _send(self) -> None:
        text = self._entry.get_text().strip()
        if not text or self._ended:
            return
        self._entry.set_text("")
        self._list.append(self._bubble(text, "user"))
        self._assistant_label = None
        self._assistant_buf = ""
        self._show_typing(True)
        self._set_busy(True)
        self._session.send(text)
        self._queue_scroll()

    def _set_busy(self, busy: bool) -> None:
        self._send_btn.set_sensitive(not busy)
        self._stop_btn.set_visible(busy)

    # -- event handling (main thread, via idle_add) ----------------------------

    def _on_event(self, ev: Event) -> bool:
        if ev.kind == "text":
            self._append_assistant(ev.text)
        elif ev.kind == "tool":
            self._show_typing(False)
            self._assistant_label = None  # text after a tool starts a fresh bubble
            chip = self._tool_chip(ev.tool_name)
            self._list.append(chip)
            self._unarmed_chips.append((ev.tool_name, chip))
            del self._unarmed_chips[:-8]  # only the current turn's chips matter
            self._queue_scroll()
        elif ev.kind == "tool_input":
            self._arm_chip(ev)
        elif ev.kind == "permission":
            self._on_permission(ev)
        elif ev.kind == "turn_end":
            self._show_typing(False)
            self._set_busy(False)
            self._assistant_label = None
        elif ev.kind == "rate_limit":
            if ev.rate_status and ev.rate_status != "allowed":
                self._notice(_("Rate limited — try again later."))
        elif ev.kind == "error":
            self._show_typing(False)
            self._set_busy(False)
            self._notice(_("Error: {msg}").format(msg=ev.text))
        elif ev.kind == "exit":
            self._show_typing(False)
            self._set_busy(False)
            if not self._ended:
                self._ended = True
                self._notice(_("Session ended."))
                self._entry.set_sensitive(False)
                self._send_btn.set_sensitive(False)
        return GLib.SOURCE_REMOVE

    def _append_assistant(self, delta: str) -> None:
        if self._assistant_label is None:
            self._show_typing(False)
            self._assistant_label = make_label("assistant")
            align = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, halign=Gtk.Align.START)
            align.append(self._assistant_label)
            self._list.append(align)
        self._assistant_buf += delta
        set_bubble_text(self._assistant_label, self._assistant_buf, "assistant")
        self._queue_scroll()

    def _arm_chip(self, ev: Event) -> None:
        """A tool call's complete input arrived: if it names a file, make the
        matching chip (the oldest unarmed one for that tool) open it in the
        editor. Chips whose tool has no file just stop being tracked."""
        for i, (name, chip) in enumerate(self._unarmed_chips):
            if name == ev.tool_name:
                del self._unarmed_chips[i]
                path = self._chip_path(ev.tool_input or {})
                if path:
                    arm_tool_chip(chip, path, self._open_in_editor)
                return

    @staticmethod
    def _chip_path(tool_input: dict) -> str:
        """The file a tool call touches, per the same input keys the terminal
        tabs' transcript tail reads — never bare "path", which is usually a
        directory (Glob, Grep)."""
        if not isinstance(tool_input, dict):
            return ""
        for key in ("file_path", "notebook_path"):
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _open_in_editor(self, path: str) -> None:
        self.activate_action("win.open-in-editor", GLib.Variant("(sii)", (path, 0, 0)))

    # -- permissions -----------------------------------------------------------

    def _on_permission(self, ev: Event) -> None:
        self._show_typing(False)
        self._assistant_label = None
        if ev.tool_name in self._always_allowed:
            self._session.respond_permission(ev.request_id, True, ev.tool_input or {})
            self._list.append(self._tool_chip(_("Auto-allowed {tool}").format(tool=ev.tool_name)))
            self._show_typing(True)
            self._queue_scroll()
            return
        self._pending_card = self._permission_card(ev)
        self._list.append(self._pending_card)
        self._queue_scroll()

    def _permission_card(self, ev: Event) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("chat-card")

        header = Gtk.Label(
            label=_("{name} wants to use {tool}").format(name=self.provider.name, tool=ev.tool_name),
            xalign=0.0,
        )
        header.add_css_class("chat-card-header")
        card.append(header)

        summary = self._permission_summary(ev.tool_name, ev.tool_input or {})
        detail = ev.text or summary
        if detail:
            body = Gtk.Label(label=detail, xalign=0.0, wrap=True, selectable=True)
            body.add_css_class("dim-label")
            body.set_max_width_chars(64)
            card.append(body)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.END)
        deny = Gtk.Button(label=_("Deny"))
        deny.connect("clicked", lambda *_: self._resolve_permission(ev, card, allow=False))
        always = Gtk.Button(label=_("Always allow {tool}").format(tool=ev.tool_name))
        always.connect("clicked", lambda *_: self._resolve_permission(ev, card, allow=True, always=True))
        allow = Gtk.Button(label=_("Allow once"))
        allow.add_css_class("suggested-action")
        allow.connect("clicked", lambda *_: self._resolve_permission(ev, card, allow=True))
        buttons.append(deny)
        buttons.append(always)
        buttons.append(allow)
        card.append(buttons)
        return card

    def _resolve_permission(self, ev: Event, card: Gtk.Widget, allow: bool, always: bool = False) -> None:
        if always:
            self._always_allowed.add(ev.tool_name)
        self._session.respond_permission(
            ev.request_id,
            allow,
            ev.tool_input or {},
            message=_("The user declined this action."),
        )
        # Freeze the card into a static outcome line so it can't be clicked twice.
        if self._pending_card is card:
            self._pending_card = None
        if card.get_parent() is self._list:
            self._list.remove(card)
        if allow and always:
            note = _("Always allowing {tool}.").format(tool=ev.tool_name)
        elif allow:
            note = _("Allowed {tool}.").format(tool=ev.tool_name)
        else:
            note = _("Denied {tool}.").format(tool=ev.tool_name)
        self._list.append(self._tool_chip(note))
        self._show_typing(True)
        self._queue_scroll()

    @staticmethod
    def _permission_summary(tool: str, tool_input: dict) -> str:
        if not isinstance(tool_input, dict):
            return ""
        for key in ("command", "file_path", "path", "pattern", "url", "prompt"):
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    # -- widgets ---------------------------------------------------------------

    def _bubble(self, text: str, role: str) -> Gtk.Widget:
        return make_bubble(text, role)

    def _tool_chip(self, name: str) -> Gtk.Widget:
        return make_tool_chip(name)

    def _notice(self, text: str) -> None:
        label = Gtk.Label(label=text, xalign=0.0, wrap=True)
        label.add_css_class("dim-label")
        self._list.append(label)
        self._queue_scroll()

    def _show_typing(self, on: bool) -> None:
        if on and self._typing is None:
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.START)
            box.add_css_class("chat-bubble")
            box.add_css_class("chat-assistant")
            box.append(Gtk.Spinner(spinning=True, valign=Gtk.Align.CENTER))
            box.append(Gtk.Label(label=_("{name} is thinking…").format(name=self.provider.name)))
            self._typing = box
            self._list.append(box)
            self._queue_scroll()
        elif not on and self._typing is not None:
            self._list.remove(self._typing)
            self._typing = None

    # -- scrolling -------------------------------------------------------------

    def _on_scroll(self, adj: Gtk.Adjustment) -> None:
        self._stick = at_bottom(adj.get_value(), adj.get_upper(), adj.get_page_size())

    def _on_upper_changed(self, adj: Gtk.Adjustment, _pspec) -> None:
        if self._stick:
            adj.set_value(bottom(adj.get_upper(), adj.get_page_size()))

    def _queue_scroll(self) -> None:
        if self._stick and self._scroll_source is None:
            self._scroll_source = GLib.timeout_add(60, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> bool:
        self._scroll_source = None
        adj = self._scroller.get_vadjustment()
        adj.set_value(bottom(adj.get_upper(), adj.get_page_size()))
        return GLib.SOURCE_REMOVE
