"""Replay a past session: step through its transcript as native chat bubbles.

Read-only and historical — reuses the transcript turn reader (`replaymodel`) and
the shared bubble rendering (`chatbubbles`). A stepper reveals turns one at a time
(or all at once) so you can re-watch how a session unfolded.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from .chatbubbles import make_bubble, make_tool_chip  # noqa: E402
from .i18n import _  # noqa: E402
from .replaymodel import Turn, read_session_turns  # noqa: E402
from .sessions import Session  # noqa: E402

_PLAY_INTERVAL_MS = 800


class ReplayTab(Gtk.Box):
    """Steps through a recorded session's turns as chat bubbles."""

    def __init__(self, session: Session, provider_id: str = "claude") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._turns: list[Turn] = read_session_turns(session.jsonl_path, provider_id)
        self._shown = 0
        self._play_source: int | None = None

        self.append(self._build_toolbar())

        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._list.set_margin_top(10)
        self._list.set_margin_bottom(10)
        self._list.set_margin_start(12)
        self._list.set_margin_end(12)
        self._scroller = Gtk.ScrolledWindow(vexpand=True, child=self._list)
        self._scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.append(self._scroller)

        if not self._turns:
            self._list.append(self._placeholder())
            self._set_controls_sensitive(False)
        else:
            self._reveal_to(1)  # start on the first turn
        self.connect("unrealize", lambda *_: self._stop_play())

    # -- toolbar ---------------------------------------------------------------

    def _build_toolbar(self) -> Gtk.Widget:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.set_margin_top(6)
        bar.set_margin_bottom(6)
        bar.set_margin_start(12)
        bar.set_margin_end(12)
        bar.add_css_class("toolbar")

        self._prev_btn = Gtk.Button(icon_name="media-skip-backward-symbolic", tooltip_text=_("Previous"))
        self._prev_btn.connect("clicked", lambda *_: self._reveal_to(self._shown - 1))
        self._play_btn = Gtk.Button(icon_name="media-playback-start-symbolic", tooltip_text=_("Play"))
        self._play_btn.connect("clicked", lambda *_: self._toggle_play())
        self._next_btn = Gtk.Button(icon_name="media-skip-forward-symbolic", tooltip_text=_("Next"))
        self._next_btn.connect("clicked", lambda *_: self._reveal_to(self._shown + 1))
        self._all_btn = Gtk.Button(label=_("Show all"))
        self._all_btn.connect("clicked", lambda *_: self._reveal_to(len(self._turns)))

        self._progress = Gtk.Label(hexpand=True, xalign=1.0)
        self._progress.add_css_class("dim-label")

        for w in (self._prev_btn, self._play_btn, self._next_btn, self._all_btn, self._progress):
            bar.append(w)
        self._update_progress()
        return bar

    def _set_controls_sensitive(self, on: bool) -> None:
        for b in (self._prev_btn, self._play_btn, self._next_btn, self._all_btn):
            b.set_sensitive(on)

    # -- revealing -------------------------------------------------------------

    def _widget_for(self, turn: Turn) -> Gtk.Widget:
        if turn.kind == "tool":
            return make_tool_chip(turn.text)
        if turn.kind == "question":
            first = turn.questions[0] if turn.questions else {}
            lines = [f"❓ {first.get('header') or _('Question')}"]
            if first.get("question"):
                lines.append(first["question"])
            for opt in first.get("options") or []:
                lines.append(f"- {opt.get('label', '')}")
            return make_bubble("\n".join(lines), "assistant")
        return make_bubble(turn.text, turn.role)

    def _reveal_to(self, n: int) -> None:
        n = max(0, min(n, len(self._turns)))
        if n > self._shown:
            for turn in self._turns[self._shown:n]:
                self._list.append(self._widget_for(turn))
        elif n < self._shown:
            for _ignore in range(self._shown - n):
                child = self._list.get_last_child()
                if child is not None:
                    self._list.remove(child)
        self._shown = n
        if n >= len(self._turns):
            self._stop_play()
        self._update_progress()
        GLib.idle_add(self._scroll_to_bottom)

    def _update_progress(self) -> None:
        total = len(self._turns)
        self._progress.set_label(f"{self._shown} / {total}")
        self._prev_btn.set_sensitive(self._shown > 0)
        at_end = self._shown >= total
        self._next_btn.set_sensitive(not at_end)
        self._all_btn.set_sensitive(not at_end)
        self._play_btn.set_sensitive(not at_end or self._play_source is not None)

    # -- play ------------------------------------------------------------------

    def _toggle_play(self) -> None:
        if self._play_source is not None:
            self._stop_play()
        elif self._shown < len(self._turns):
            self._play_btn.set_icon_name("media-playback-pause-symbolic")
            self._play_source = GLib.timeout_add(_PLAY_INTERVAL_MS, self._play_tick)

    def _play_tick(self) -> bool:
        if self.get_root() is None or self._shown >= len(self._turns):
            self._stop_play()
            return GLib.SOURCE_REMOVE
        self._reveal_to(self._shown + 1)
        return GLib.SOURCE_CONTINUE

    def _stop_play(self) -> None:
        if self._play_source is not None:
            GLib.source_remove(self._play_source)
            self._play_source = None
        self._play_btn.set_icon_name("media-playback-start-symbolic")

    # -- misc ------------------------------------------------------------------

    def _placeholder(self) -> Gtk.Widget:
        label = Gtk.Label(label=_("Nothing to replay yet."), xalign=0.0)
        label.add_css_class("dim-label")
        return label

    def _scroll_to_bottom(self) -> bool:
        adj = self._scroller.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        return GLib.SOURCE_REMOVE
