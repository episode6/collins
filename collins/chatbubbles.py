# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-01. Full change history: git log for this file.
"""Shared chat-bubble widgets: message bubbles and tool chips.

Used by both the live streaming chat (`chatsessionview`) and the session replay
view (`replayview`). The markdown → Pango rendering they set on these labels
lives in `formatting`, which stays free of GTK.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from .formatting import md_to_pango  # noqa: E402
from .i18n import _  # noqa: E402


def make_label(role: str) -> Gtk.Label:
    """A bubble label (wrapping, selectable, capped width) with role styling."""
    label = Gtk.Label(xalign=0.0, selectable=True)
    label.set_wrap(True)
    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    label.set_max_width_chars(64)
    label.add_css_class("chat-bubble")
    label.add_css_class("chat-user" if role == "user" else "chat-assistant")
    return label


def set_bubble_text(label: Gtk.Label, text: str, role: str) -> None:
    """Set bubble text — markdown-rendered for the assistant, plain for the user."""
    if role == "assistant":
        try:
            label.set_markup(md_to_pango(text))
            return
        except GLib.GError:
            pass  # malformed markup → fall through to plain
    label.set_label(text)


def make_bubble(text: str, role: str) -> Gtk.Widget:
    """A left/right-aligned chat bubble for a finished message."""
    label = make_label(role)
    set_bubble_text(label, text, role)
    align = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    align.set_halign(Gtk.Align.END if role == "user" else Gtk.Align.START)
    align.append(label)
    return align


def make_tool_chip(text: str) -> Gtk.Widget:
    """A compact dim chip for a tool call."""
    chip = Gtk.Label(label=f"🔧 {text}", xalign=0.0)
    chip.set_wrap(True)
    chip.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    chip.add_css_class("chat-tool")
    return chip


def arm_tool_chip(chip: Gtk.Widget, path: str, on_open: Callable[[str], None]) -> None:
    """Upgrade a tool chip that turned out to name a file: show the file name
    and open it in the editor on click. Safe to call once per chip only —
    the caller stops tracking a chip the moment it is armed."""
    name = Path(path).name
    chip.set_label(f"{chip.get_label()} — {name}")
    chip.set_tooltip_text(_("Open {name} in the editor").format(name=name))
    chip.add_css_class("chat-tool-link")
    chip.set_cursor(Gdk.Cursor.new_from_name("pointer"))
    click = Gtk.GestureClick()
    click.connect("released", lambda *_a: on_open(path))
    chip.add_controller(click)
