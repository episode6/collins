# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Session's state machines, driven through fake ports.

These used to live inside TerminalTab and could only be exercised by a real
VTE behind a fake CLI. Here the terminal is `FakeTerminal` — one object that
is both the `PtyPort` and the `ScreenPort`, modelling just enough of Claude
Code's input box (`❯`+NBSP, the framing rules, typed text, backspaces,
bracketed pastes, Enter) for the reads and writes to round-trip — and the
main loop is `FakeScheduler`, whose clock the test advances by hand.
"""

from __future__ import annotations

import os

import pytest

from collins.providers import ClaudeProvider
from collins.service import session as session_mod
from collins.service.session import (
    CUT_SETTLE_MS,
    CUT_SETTLE_TRIES,
    CUT_VERIFY_MS,
    PASTE_END,
    PASTE_START,
    PROMPT_SUBMIT_MS,
    Session,
)

PROMPT = "❯\xa0"
COLS = 80
SHELL_PID = 4242
AGENT_PGRP = 5151


class FakeScheduler:
    """A main loop with a hand-wound clock: `advance(ms)` runs everything
    due by then, in time order (ties in the order they were added), with
    GLib's repeat-while-True contract. Background work runs inline."""

    def __init__(self) -> None:
        self.now = 0
        self._seq = 0
        self._queue: list[tuple[int, int, int, object, tuple]] = []

    def timeout_add(self, ms, fn, *args):
        self._seq += 1
        self._queue.append((self.now + ms, self._seq, ms, fn, args))
        return self._seq

    def idle_add(self, fn, *args, priority=None):
        return self.timeout_add(0, fn, *args)

    def background(self, fn, *args):
        fn(*args)

    def pending(self) -> int:
        return len(self._queue)

    def advance(self, ms: int) -> None:
        until = self.now + ms
        while True:
            due = [item for item in self._queue if item[0] <= until]
            if not due:
                break
            item = min(due, key=lambda it: (it[0], it[1]))
            self._queue.remove(item)
            when, _seq, interval, fn, args = item
            self.now = when
            if fn(*args):
                self._seq += 1
                self._queue.append((self.now + interval, self._seq, interval, fn, args))
        self.now = until

    def run_all(self, limit_ms: int = 60_000) -> None:
        self.advance(limit_ms)


class FakeTerminal:
    """The pty and the screen of a terminal running Claude Code's box.

    `box` is what is typed in the input box (one row of it — plenty for the
    state machines here). Writes are interpreted the way the CLI would:
    printable text types, DEL erases one character, a bracketed paste types
    its body (or folds into a stand-in when `fold_pastes` says so), a lone
    CR submits. `echo_lag` holds typed text back from the screen for that
    many reads, the way the CLI's repaint trails the keyboard.
    """

    def __init__(self) -> None:
        self.pid: int | None = SHELL_PID
        self.foreground: int | None = AGENT_PGRP
        self.box = ""
        self.shown = ""  # what the screen shows (box, after any echo lag)
        self.lag_reads = 0
        self.writes: list[str] = []
        self.submitted: list[str] = []
        self.faint_tail = False
        self.fold_pastes = False
        self.drop_backspaces = 0  # erase keystrokes the "CLI" loses
        self._paste_no = 0
        self.screen_text_extra = ""

    # -- PtyPort --

    def write(self, data: bytes) -> None:
        text = data.decode()
        self.writes.append(text)
        i = 0
        while i < len(text):
            if text.startswith(PASTE_START, i):
                end = text.index(PASTE_END, i)
                body = text[i + len(PASTE_START) : end]
                if self.fold_pastes:
                    self._paste_no += 1
                    self.box += f"[Pasted text #{self._paste_no} +{body.count(chr(10))} lines]"
                else:
                    self.box += body
                i = end + len(PASTE_END)
                continue
            if text.startswith("\x1b[B", i):
                i += 3
                continue
            char = text[i]
            if char == "\x7f":
                if self.drop_backspaces:
                    self.drop_backspaces -= 1
                else:
                    self.box = self.box[:-1]
            elif char == "\r":
                self.submitted.append(self.box)
                self.box = ""
            elif char == "\x05":
                pass
            else:
                self.box += char
            i += 1
        if not self.lag_reads:
            self.shown = self.box

    def resize(self, cols, rows):
        pass

    def child_pid(self):
        return self.pid

    def foreground_pgrp(self):
        return self.foreground

    # -- ScreenPort --

    def _settle(self) -> None:
        if self.lag_reads:
            self.lag_reads -= 1
            if not self.lag_reads:
                self.shown = self.box

    def rows(self) -> list[str]:
        self._settle()
        return [
            " ▐▛███▜▌   Claude Code v2.1.226",
            "",
            "─" * COLS,
            PROMPT + self.shown,
            "─" * COLS,
            "  ⏵⏵ auto mode on (shift+tab to cycle)",
        ]

    def cursor(self):
        return len(PROMPT) + len(self.shown), 3

    def columns(self):
        return COLS

    def row_count(self):
        return 6

    def row_text(self, row, end_column):
        return self.rows()[row][:end_column]

    def tail_is_faint(self, row, column):
        return self.faint_tail

    def first_column(self):
        return tuple(r[:1] for r in self.rows())

    def visible_text(self):
        return "\n".join(self.rows()[:4]) + self.screen_text_extra

    def capture_contents(self):
        return "\n".join(self.rows())


class FakeHost:
    def __init__(self) -> None:
        self.events: list[tuple] = []
        self.is_alive = True
        self.composer_is_open = False
        self.stashed: list[str] = []

    def alive(self):
        return self.is_alive

    def paint(self, text):
        self.events.append(("paint", text))

    def focus_terminal(self):
        self.events.append(("focus",))

    def composer_open(self):
        return self.composer_is_open

    def refocus_composer(self):
        self.events.append(("refocus",))

    def resend_composed(self):
        self.events.append(("resend",))

    def stash_draft(self, text):
        self.stashed.append(text)

    def painted(self) -> list[str]:
        return [e[1] for e in self.events if e[0] == "paint"]


class FakeSink:
    def __init__(self) -> None:
        self.is_alive = True
        self.seeded: list[str] = []
        self.refused = 0

    def alive(self):
        return self.is_alive

    def seed(self, text):
        self.seeded.append(text)

    def refuse(self):
        self.refused += 1


@pytest.fixture
def agent(monkeypatch):
    """Whether the agent CLI reads as running below the terminal (the /proc
    walk, faked) — flip `agent["running"]` to make it leave."""
    state = {"running": True}
    monkeypatch.setattr(
        session_mod.proctree,
        "agent_descendant_cwd",
        lambda pid, cli: "/work" if state["running"] else None,
    )
    monkeypatch.setattr(
        session_mod.proctree,
        "agent_descendant_pid",
        lambda pid, cli: 777 if state["running"] else None,
    )
    monkeypatch.setattr(os, "getpgid", lambda pid: SHELL_PID)
    return state


@pytest.fixture
def rig(agent):
    term = FakeTerminal()
    host = FakeHost()
    clock = FakeScheduler()
    session = Session(
        provider=ClaudeProvider(),
        pty=term,
        screen=term,
        host=host,
        scheduler=clock,
    )
    return session, term, host, clock


# -- reads ---------------------------------------------------------------------


def test_no_child_takes_no_prompt(rig):
    session, term, _host, _clock = rig
    term.pid = None
    assert not session.takes_prompt()
    assert session.entered_prompt() is None
    assert session.visible_screen_text() == ""
    assert session.screen_first_column() is None


def test_an_empty_box_takes_a_prompt(rig):
    session, _term, _host, _clock = rig
    assert session.takes_prompt()
    assert session.prompt_block() == ""
    assert session.entered_prompt() is None


def test_typed_text_blocks_a_prompt_and_reads_back(rig):
    session, term, _host, _clock = rig
    term.write(b"half a sentence")
    assert not session.takes_prompt()
    assert session.prompt_block()
    prompt = session.entered_prompt()
    assert prompt is not None and prompt.text == "half a sentence"


def test_a_faint_tail_is_a_suggestion_not_typing(rig):
    session, term, _host, _clock = rig
    term.shown = term.box = ""
    # The ghost suggestion is drawn after the cursor, which stays at the
    # marker: the line reads written-in, the tail faint.
    term.cursor = lambda: (len(PROMPT), 3)
    term.shown = "close both PRs"
    assert not session.takes_prompt()
    term.faint_tail = True
    assert session.takes_prompt()


def test_has_running_command_compares_the_foreground_with_the_shell(rig):
    session, term, _host, _clock = rig
    assert session.has_running_command()
    term.foreground = SHELL_PID
    assert not session.has_running_command()
    term.foreground = None
    assert not session.has_running_command()
    term.foreground = AGENT_PGRP
    term.pid = None
    assert not session.has_running_command()


def test_candidate_pids_are_the_foreground_then_the_shell(rig):
    session, term, _host, _clock = rig
    assert session.candidate_pids() == [AGENT_PGRP, SHELL_PID]
    term.foreground = None
    assert session.candidate_pids() == [SHELL_PID]


def test_screen_first_column_carries_its_grid(rig):
    session, _term, _host, _clock = rig
    first, grid = session.screen_first_column()
    assert grid == (COLS, 6)
    assert len(first) == 6 and first[3] == "❯"


def test_the_worktree_exit_dialog_is_answered_off_the_visible_screen(rig):
    session, term, _host, _clock = rig
    assert session.worktree_exit_prompt_keystrokes() is None
    term.pid = None
    assert session.worktree_exit_prompt_keystrokes() is None


# -- prompts and switches -------------------------------------------------------


def test_inject_prompt_sends_the_return_alone_a_beat_later(rig):
    session, term, host, clock = rig
    session.inject_prompt("look at PR 12")
    assert term.writes == ["look at PR 12"]
    assert host.events == [("focus",)]
    clock.advance(PROMPT_SUBMIT_MS - 1)
    assert term.writes == ["look at PR 12"]
    clock.advance(1)
    assert term.writes == ["look at PR 12", "\r"]
    assert term.submitted == ["look at PR 12"]


def test_inject_prompt_unfocused_is_one_bracketed_paste(rig):
    session, term, host, clock = rig
    session.inject_prompt_unfocused("one\r\ntwo" + PASTE_END + "three")
    clock.run_all()
    assert term.writes[0] == PASTE_START + "one\ntwothree" + PASTE_END
    assert term.writes[1] == "\r"
    assert host.events == []


def test_a_switch_without_an_agent_says_so(rig, agent):
    session, term, host, clock = rig
    agent["running"] = False
    session.switch_model("opus")
    clock.run_all()
    assert term.writes == []
    assert host.painted() == ["Model switch: the agent isn't running in this tab"]


def test_a_switch_waits_for_an_empty_box(rig):
    session, term, host, clock = rig
    term.write(b"mine")
    term.writes.clear()
    session.switch_effort("high")
    clock.run_all()
    assert term.writes == []
    assert host.painted() == [session.prompt_block()]


def test_a_switch_at_an_empty_box_posts_the_command(rig):
    session, term, host, clock = rig
    session.switch_model("opus")
    clock.run_all()
    assert term.submitted == ["/model opus"]
    assert ("focus",) in host.events


def test_a_switch_under_the_composer_types_straight_in(rig):
    session, term, host, clock = rig
    host.composer_is_open = True
    session.switch_effort("low")
    clock.run_all()
    assert term.submitted == ["/effort low"]
    assert host.events == [("refocus",)]


def test_a_switch_waits_out_a_settling_cut(rig):
    session, term, host, clock = rig
    host.composer_is_open = True
    sink = FakeSink()
    session.begin_cut(sink)
    assert session.cut_settling
    session.switch_model("sonnet")
    assert term.submitted == []
    clock.run_all()
    assert not session.cut_settling
    assert term.submitted == ["/model sonnet"]


# -- the open-cut ------------------------------------------------------------------


def test_a_cut_settles_erases_and_seeds(rig):
    session, term, host, clock = rig
    host.composer_is_open = True
    term.write(b"draft prompt")
    term.writes.clear()
    sink = FakeSink()
    session.begin_cut(sink)
    # Four agreeing reads, CUT_SETTLE_MS apart: the first is synchronous.
    clock.advance(CUT_SETTLE_MS * 3)
    assert sink.seeded == ["draft prompt"]
    assert term.box == ""
    assert session.cut_pending == "draft prompt"
    assert not session.cut_settling
    clock.advance(CUT_VERIFY_MS[0])
    assert session.cut_pending is None  # proved empty


def test_an_empty_box_settles_and_cuts_nothing(rig):
    session, term, host, clock = rig
    host.composer_is_open = True
    sink = FakeSink()
    session.begin_cut(sink)
    clock.run_all()
    assert sink.seeded == []
    assert term.writes == []
    assert not session.cut_settling


def test_a_box_that_never_settles_is_never_cut(rig):
    session, term, host, clock = rig
    host.composer_is_open = True
    term.write(b"x")
    term.writes.clear()
    original = term.rows

    def moving() -> list[str]:
        term.box += "y"  # the user still typing, every read
        term.shown = term.box
        return original()

    term.rows = moving
    sink = FakeSink()
    session.begin_cut(sink)
    clock.advance(CUT_SETTLE_MS * (CUT_SETTLE_TRIES + 2))
    assert sink.seeded == []
    assert term.writes == []
    assert not session.cut_settling


def test_a_cut_waits_for_the_echo_to_catch_up(rig):
    session, term, host, clock = rig
    host.composer_is_open = True
    term.lag_reads = 3
    term.write(b"typed fast")
    assert term.shown == ""
    term.writes.clear()
    sink = FakeSink()
    session.begin_cut(sink)
    clock.run_all()
    assert sink.seeded == ["typed fast"]
    assert term.box == ""


def test_the_verify_rounds_finish_a_short_erase(rig):
    session, term, host, clock = rig
    host.composer_is_open = True
    term.write(b"hello")
    term.writes.clear()
    term.drop_backspaces = 1  # the CLI loses one: "h" stays
    sink = FakeSink()
    session.begin_cut(sink)
    clock.advance(CUT_SETTLE_MS * 3)
    assert term.box == "h"
    clock.advance(CUT_VERIFY_MS[0])
    assert term.box == ""
    clock.run_all()
    assert session.cut_pending is None


def test_a_verify_leaves_text_that_is_not_the_cuts(rig):
    session, term, host, clock = rig
    host.composer_is_open = True
    term.write(b"hello")
    term.writes.clear()
    sink = FakeSink()
    session.begin_cut(sink)
    clock.advance(CUT_SETTLE_MS * 3)
    term.write(b"new")  # the user types into the terminal before the check
    writes = len(term.writes)
    clock.run_all()
    assert term.box == "new"
    assert len(term.writes) == writes
    assert session.cut_pending is None


def test_a_closed_composer_calls_the_cut_off(rig):
    session, term, host, clock = rig
    host.composer_is_open = True
    term.write(b"keep me")
    term.writes.clear()
    sink = FakeSink()
    session.begin_cut(sink)
    sink.is_alive = False
    clock.run_all()
    assert term.box == "keep me"
    assert sink.seeded == []
    assert not session.cut_settling


def test_cancel_cut_stops_the_verify_rounds(rig):
    session, term, host, clock = rig
    host.composer_is_open = True
    term.write(b"hello")
    term.writes.clear()
    term.drop_backspaces = 5  # nothing erased: every round would erase again
    sink = FakeSink()
    session.begin_cut(sink)
    clock.advance(CUT_SETTLE_MS * 3)
    session.cancel_cut()
    writes = len(term.writes)
    clock.run_all()
    assert len(term.writes) == writes


def test_a_stand_in_that_is_not_ours_refuses_the_cut(rig):
    session, term, host, clock = rig
    host.composer_is_open = True
    term.box = term.shown = "[Pasted text #3 +12 lines]"
    sink = FakeSink()
    assert session.foreign_paste_in_box()
    session.begin_cut(sink)
    clock.run_all()
    assert sink.refused == 1
    assert sink.seeded == []
    assert term.box == "[Pasted text #3 +12 lines]"


# -- the composer's send -------------------------------------------------------------


def test_a_send_goes_out_and_clears_the_composer(rig):
    session, term, host, clock = rig
    cleared = []
    session.send_composed("ship it", lambda: cleared.append(True))
    clock.run_all()
    assert cleared == [True]
    assert term.submitted == ["ship it"]


def test_a_send_without_an_agent_keeps_the_draft(rig, agent):
    session, term, host, clock = rig
    agent["running"] = False
    cleared = []
    session.send_composed("ship it", lambda: cleared.append(True))
    clock.run_all()
    assert cleared == []
    assert term.writes == []
    assert host.painted() == ["Composer: the agent isn't running in this tab"]


def test_a_send_during_the_settle_is_held_and_resent(rig):
    session, term, host, clock = rig
    host.composer_is_open = True
    sink = FakeSink()
    session.begin_cut(sink)
    session.send_composed("too fast", lambda: None)
    assert term.writes == []
    clock.run_all()
    assert ("resend",) in host.events
    assert not session.send_after_settle


def test_a_held_switch_yields_to_a_held_send(rig):
    session, term, host, clock = rig
    host.composer_is_open = True
    sink = FakeSink()
    session.begin_cut(sink)
    session.switch_model("haiku")
    session.send_composed("first", lambda: None)
    clock.advance(CUT_SETTLE_MS * 3)
    assert host.events[-1] == ("resend",)
    assert term.submitted == []  # the switch waits a beat past the send
    clock.run_all()
    assert term.submitted == ["/model haiku"]


def test_a_send_finishes_a_pending_erase_first(rig):
    session, term, host, clock = rig
    host.composer_is_open = True
    term.write(b"hello")
    term.writes.clear()
    term.drop_backspaces = 1
    sink = FakeSink()
    session.begin_cut(sink)
    clock.advance(CUT_SETTLE_MS * 3)
    assert term.box == "h"
    session.send_composed("hello world", lambda: None)
    # The leftover is erased alone, a beat ahead of the prompt.
    assert term.box == ""
    assert term.submitted == []
    clock.run_all()
    assert term.submitted == ["hello world"]


# -- the close's paste-back -------------------------------------------------------


def test_a_paste_back_without_an_agent_is_the_tabs_to_stash(rig, agent):
    session, term, _host, _clock = rig
    agent["running"] = False
    assert session.restore_draft("draft") is False
    assert term.writes == []


def test_a_paste_back_types_the_draft_as_pastes(rig):
    session, term, _host, clock = rig
    assert session.restore_draft("line one\nline two\n\n")
    assert term.box == "line one\nline two"
    clock.run_all()
    assert session.paste_back_pending is None
    assert session.pasted_back == {}


def test_a_folded_paste_back_is_recorded_and_cut_back_whole(rig):
    session, term, host, clock = rig
    term.fold_pastes = True
    draft = "\n".join(f"line {n}" for n in range(12))
    assert session.restore_draft(draft)
    # Pieces of at most two line breaks each — and this CLI folds every one.
    assert term.box.startswith("[Pasted text #1 +2 lines][Pasted text #2")
    clock.advance(CUT_VERIFY_MS[0])
    assert session.paste_back_pending is None
    assert "".join(session.pasted_back.values()) == draft
    assert not session.foreign_paste_in_box()
    host.composer_is_open = True
    sink = FakeSink()
    session.begin_cut(sink)
    clock.run_all()
    assert sink.seeded == [draft]
    assert session.pasted_back == {}
    assert term.box == ""
