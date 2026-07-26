# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-26. Full change history: git log for this file.
"""A native card for an agent's structured prompt (Claude's AskUserQuestion).

Shown as an overlay on the terminal: instead of navigating the TUI, the user
clicks an option and we feed the matching keystrokes back to the agent. Detection
and content come from the transcript (reliable); only the answer keystrokes are
best-effort, with an "Answer in terminal" fallback.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .i18n import _
from .providers import Provider


def build_question_card(
    questions: list,
    provider: Provider,
    on_answer: Callable[[list, int], None],
    on_dismiss: Callable[[], None],
) -> Gtk.Widget:
    """Build the prompt card widget for an AskUserQuestion payload."""
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    card.add_css_class("chat-card")
    card.set_halign(Gtk.Align.CENTER)
    card.set_valign(Gtk.Align.END)
    card.set_margin_start(12)
    card.set_margin_end(12)
    card.set_margin_bottom(12)
    card.set_size_request(420, -1)

    # Single-question, single-select prompts can be auto-answered; everything else
    # (multi-select, multiple questions) is safer to finish in the terminal.
    auto = len(questions) == 1 and provider.answer_keystrokes(questions, 0) is not None

    first = questions[0] if questions else {}
    header = first.get("header") or _("Question")
    if len(questions) > 1:
        header = _("{n} questions").format(n=len(questions))
    head = Gtk.Label(label=header, xalign=0.0)
    head.add_css_class("chat-card-header")
    card.append(head)

    question_text = first.get("question") or ""
    if question_text:
        q = Gtk.Label(label=question_text, xalign=0.0, wrap=True)
        q.add_css_class("heading")
        card.append(q)

    if auto:
        for idx, opt in enumerate(first.get("options") or []):
            card.append(_option_button(questions, idx, opt, on_answer))
    else:
        for opt in first.get("options") or []:
            card.append(_option_row(opt))
        note = Gtk.Label(label=_("This prompt needs the terminal to answer."), xalign=0.0)
        note.add_css_class("dim-label")
        card.append(note)

    in_term = Gtk.Button(label=_("Answer in terminal"))
    in_term.set_halign(Gtk.Align.END)
    in_term.connect("clicked", lambda *_: on_dismiss())
    card.append(in_term)
    return card


def _option_button(
    questions: list, idx: int, opt: dict, on_answer: Callable[[list, int], None]
) -> Gtk.Widget:
    btn = Gtk.Button()
    btn.add_css_class("chat-option")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    title = Gtk.Label(label=opt.get("label", ""), xalign=0.0, wrap=True)
    title.add_css_class("heading")
    box.append(title)
    desc_text = opt.get("description") or ""
    if desc_text:
        desc = Gtk.Label(label=desc_text, xalign=0.0, wrap=True)
        desc.add_css_class("dim-label")
        box.append(desc)
    btn.set_child(box)
    btn.connect("clicked", lambda *_: on_answer(questions, idx))
    return btn


def _option_row(opt: dict) -> Gtk.Widget:
    row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    row.add_css_class("chat-option-static")
    row.append(Gtk.Label(label=f"• {opt.get('label', '')}", xalign=0.0, wrap=True))
    return row
