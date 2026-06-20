"""Shared chat-bubble rendering: markdown → Pango markup + bubble/chip widgets.

Used by both the live streaming chat (`chatsessionview`) and the session replay
view (`replayview`).
"""

from __future__ import annotations

import re

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_HEADING_RE = re.compile(r"(?m)^\s*#{1,6}\s+(.*)$")
_BULLET_RE = re.compile(r"(?m)^(\s*)[-*]\s+")
_SENT_A, _SENT_B = chr(0xE000), chr(0xE001)  # PUA sentinels survive markup escaping


def md_to_pango(text: str) -> str:
    """Render common markdown as Pango markup; code spans are protected first."""
    stash: list[str] = []

    def keep(markup: str) -> str:
        stash.append(markup)
        return f"{_SENT_A}{len(stash) - 1}{_SENT_B}"

    text = _FENCE_RE.sub(lambda m: keep(f"<tt>{GLib.markup_escape_text(m.group(1).rstrip())}</tt>"), text)
    text = _INLINE_CODE_RE.sub(lambda m: keep(f"<tt>{GLib.markup_escape_text(m.group(1))}</tt>"), text)
    text = GLib.markup_escape_text(text)
    text = _HEADING_RE.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    text = _BOLD_RE.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    text = _BULLET_RE.sub(lambda m: f"{m.group(1)}• ", text)
    for i, markup in enumerate(stash):
        text = text.replace(f"{_SENT_A}{i}{_SENT_B}", markup)
    return text


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
