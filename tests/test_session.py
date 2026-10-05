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

import json
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

# GLib's priorities (session.py's GLib, so the test needs no gi import of
# its own): a timeout's, and an idle's when none is given.
PRIORITY_DEFAULT = session_mod.GLib.PRIORITY_DEFAULT
PRIORITY_DEFAULT_IDLE = session_mod.GLib.PRIORITY_DEFAULT_IDLE

PROMPT = "❯\xa0"
COLS = 80
SHELL_PID = 4242
AGENT_PGRP = 5151


class FakeScheduler:
    """A main loop with a hand-wound clock: `advance(ms)` runs everything
    due by then, in time order, then by GLib priority (a timeout and a
    PRIORITY_DEFAULT idle ahead of a default-idle one), then in the order
    they were added — with GLib's repeat-while-True contract. Every idle is
    recorded with its priority (`idles`), so a landing that must not be
    starved can be checked for PRIORITY_DEFAULT. Background work runs
    inline."""

    def __init__(self) -> None:
        self.now = 0
        self.wall = 1_000_000.0  # what time() answers: seconds, set by hand
        self._seq = 0
        # (due, priority, seq, interval, fn, args)
        self._queue: list[tuple[int, int, int, int, object, tuple]] = []
        self.idles: list[tuple[str, int]] = []  # (callback name, priority)
        self.monitors: dict[str, object] = {}

    def _add(self, ms, priority, fn, args):
        self._seq += 1
        self._queue.append((self.now + ms, priority, self._seq, ms, fn, args))
        return self._seq

    def timeout_add(self, ms, fn, *args):
        return self._add(ms, PRIORITY_DEFAULT, fn, args)

    def idle_add(self, fn, *args, priority=PRIORITY_DEFAULT_IDLE):
        self.idles.append((getattr(fn, "__name__", repr(fn)), priority))
        return self._add(0, priority, fn, args)

    def idle_priority(self, name: str) -> int:
        """The priority the last idle named *name* was added at."""
        return next(p for n, p in reversed(self.idles) if n == name)

    def background(self, fn, *args):
        fn(*args)

    def time(self):
        return self.wall

    def monitor_file(self, path, on_changed):
        scheduler = self

        class Monitor:
            cancelled = False

            def cancel(self):
                self.cancelled = True
                scheduler.monitors.pop(path, None)

        self.monitors[path] = on_changed
        return Monitor()

    def pending(self) -> int:
        return len(self._queue)

    def advance(self, ms: int) -> None:
        until = self.now + ms
        while True:
            due = [item for item in self._queue if item[0] <= until]
            if not due:
                break
            item = min(due, key=lambda it: (it[0], it[1], it[2]))
            self._queue.remove(item)
            when, priority, _seq, interval, fn, args = item
            self.now = when
            if fn(*args):
                self._add(interval, priority, fn, args)
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
        self.is_mapped = True
        self.chips: list = []
        self.landed: list[tuple] = []
        self.resolved: list[str] = []
        self.forks: list[str] = []
        self.cwds: list = []
        self.inputs: list[str] = []  # what write_text announced, in order

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

    def mapped(self):
        return self.is_mapped

    def shown_prs(self):
        return self.chips

    def transcript_reset(self):
        self.events.append(("reset",))
        self.chips = []

    def transcript_landed(self, prs, lookup_empty):
        self.landed.append((list(prs), lookup_empty))
        self.chips = list(prs)

    def session_resolved(self, session_id):
        self.resolved.append(session_id)

    def fork_resolved(self, session_id):
        self.forks.append(session_id)

    def cwd_polled(self, cwd):
        self.cwds.append(cwd)

    def input_sent(self, text):
        self.inputs.append(text)

    def painted(self) -> list[str]:
        return [e[1] for e in self.events if e[0] == "paint"]


class FakeSink:
    def __init__(self) -> None:
        self.is_alive = True
        self.seeded: list[str] = []
        self.refused = 0
        self.ended_count = 0  # cuts that ended with nothing to seed

    def alive(self):
        return self.is_alive

    def seed(self, text):
        self.seeded.append(text)

    def refuse(self):
        self.refused += 1

    def ended(self):
        self.ended_count += 1


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


# -- the transcript resolver --------------------------------------------------------


class DirClaude(ClaudeProvider):
    """Claude Code with its transcripts read from plain directories: one per
    cwd, under *root*, named as the CLI names them."""

    def __init__(self, root) -> None:
        self.root = root

    def _dir(self, cwd):
        return self.root / cwd.strip("/").replace("/", "-")

    def transcripts_for_cwd(self, cwd):
        directory = self._dir(cwd)
        return sorted(directory.glob("*.jsonl")) if directory.is_dir() else []

    def write(self, cwd, name, mtime=None):
        directory = self._dir(cwd)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.jsonl"
        path.write_text("")
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path


@pytest.fixture
def resolving(agent, tmp_path, monkeypatch):
    """A fresh session (no id) over a DirClaude provider, its transcript
    tail kept off the disk's real update thread."""
    provider = DirClaude(tmp_path)
    term = FakeTerminal()
    host = FakeHost()
    clock = FakeScheduler()
    monkeypatch.setattr(session_mod.proctree, "process_cwd", lambda pid: None)
    session = Session(provider=provider, pty=term, screen=term, host=host, scheduler=clock)
    return session, provider, host, clock


def test_a_fresh_session_binds_to_the_transcript_it_writes(resolving):
    session, provider, host, clock = resolving
    provider.write("/proj", "old-session")
    session.start_resolver("/proj")
    clock.advance(session_mod.RESOLVER_POLL_MS)
    assert host.resolved == []  # the one already there is somebody else's
    path = provider.write("/proj", "new-session")
    clock.advance(session_mod.RESOLVER_POLL_MS)
    assert host.resolved == ["new-session"]
    assert session.session_id == "new-session"
    assert session.transcript_path == str(path)
    pending = clock.pending()
    clock.advance(session_mod.RESOLVER_POLL_MS * 3)
    assert host.resolved == ["new-session"]  # resolved once, and the poll is gone
    assert clock.pending() <= pending


def test_a_continue_adopts_the_newest_existing_transcript(resolving):
    session, provider, host, clock = resolving
    session.command_override = "claude --continue"
    provider.write("/proj", "older", mtime=1000)
    provider.write("/proj", "newest", mtime=2000)
    session.start_resolver("/proj")
    clock.advance(session_mod.RESOLVER_POLL_MS)
    assert host.resolved == ["newest"]


def test_a_sandboxed_fork_reports_its_id_and_stays_bound(resolving):
    session, provider, host, clock = resolving
    session.session_id = "original"
    session.fork = True
    session.fork_resolve = True
    session.start_resolver("/proj")
    provider.write("/proj", "the-fork")
    clock.advance(session_mod.RESOLVER_POLL_MS)
    assert host.forks == ["the-fork"]
    assert host.resolved == []
    assert session.session_id == "original"
    assert session.transcript_path is None


def test_a_resolved_session_never_arms(resolving):
    session, provider, host, clock = resolving
    session.session_id = "known"
    session.start_resolver("/proj")
    assert clock.pending() == 0


def test_a_background_tab_pauses_and_resumes_on_map(resolving):
    session, provider, host, clock = resolving
    host.is_mapped = False
    session.start_resolver("/proj")
    clock.advance(session_mod.RESOLVER_POLL_MS * (session_mod.RESOLVER_BACKGROUND_TICKS + 1))
    assert clock.pending() == 0  # paused
    provider.write("/proj", "arrived-meanwhile")
    session.arm_resolver()  # the tab is shown again
    clock.advance(session_mod.RESOLVER_POLL_MS)
    # Re-arming re-baselines: what appeared during the pause is not ours.
    assert host.resolved == []
    provider.write("/proj", "ours")
    clock.advance(session_mod.RESOLVER_POLL_MS)
    assert host.resolved == ["ours"]


def test_a_foreground_tab_polls_on(resolving):
    session, provider, host, clock = resolving
    session.start_resolver("/proj")
    clock.advance(session_mod.RESOLVER_POLL_MS * (session_mod.RESOLVER_BACKGROUND_TICKS + 5))
    assert clock.pending() == 1


def test_a_closed_tab_stops_resolving(resolving):
    session, provider, host, clock = resolving
    session.start_resolver("/proj")
    host.is_alive = False
    clock.advance(session_mod.RESOLVER_POLL_MS)
    assert clock.pending() == 0


def test_the_resolver_follows_into_a_worktree_but_not_into_its_past(
    resolving, agent, monkeypatch
):
    session, provider, host, clock = resolving
    worktree = "/proj/.claude/worktrees/w1"
    monkeypatch.setattr(
        session_mod.proctree, "agent_descendant_cwd", lambda pid, cli: worktree
    )
    monkeypatch.setattr(session_mod, "worktree_shares_project", lambda live, cwd: True)
    armed = clock.wall
    provider.write(worktree, "recycled", mtime=armed - 60)  # an older session's
    session.start_resolver("/proj")
    provider.write(worktree, "ours", mtime=armed + 1)  # born in the same tick
    clock.advance(session_mod.RESOLVER_POLL_MS)
    assert host.resolved == ["ours"]


def test_unstarted_thread_is_a_fresh_empty_session(resolving):
    session, provider, host, clock = resolving
    assert not session.unstarted_thread()  # never resolving: not a new thread
    session.start_resolver("/proj")
    assert session.unstarted_thread()
    session.pty.write(b"typed")
    assert not session.unstarted_thread()


# -- the transcript tail --------------------------------------------------------------


def _transcript(tmp_path, *entries):
    path = tmp_path / "t.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return path


def _reply(model="claude-opus-4-8", text="done"):
    return {
        "type": "assistant",
        "cwd": "/proj",
        "timestamp": "2026-06-01T10:00:05.000Z",
        "message": {"role": "assistant", "model": model, "content": [{"type": "text", "text": text}]},
    }


def test_a_transcript_read_lands_and_arms_the_ledger(rig, tmp_path):
    session, _term, host, clock = rig
    path = _transcript(tmp_path, _reply())
    session.set_transcript_path(path)
    assert host.events[0] == ("reset",)
    assert str(path) in clock.monitors
    clock.advance(0)  # the landing (background work runs inline here)
    assert host.landed == [([], False)]
    assert session.finish_ledger.armed
    assert session.current_model() == "claude-opus-4-8"


def test_the_monitor_debounces_into_one_read(rig, tmp_path):
    session, _term, host, clock = rig
    path = _transcript(tmp_path, _reply())
    session.set_transcript_path(path)
    clock.advance(0)
    on_changed = clock.monitors[str(path)]
    for _ in range(5):
        on_changed()
    clock.advance(session_mod.TRANSCRIPT_DEBOUNCE_MS)
    assert len(host.landed) == 2


def test_the_poll_stops_with_the_tab(rig, tmp_path):
    session, _term, host, clock = rig
    session.set_transcript_path(_transcript(tmp_path, _reply()))
    clock.advance(session_mod.PROMPT_POLL_MS)
    landed = len(host.landed)
    assert landed >= 2
    host.is_alive = False
    clock.advance(session_mod.PROMPT_POLL_MS * 3)
    assert len(host.landed) == landed


def test_a_read_in_flight_carries_a_discover_to_the_next(rig, tmp_path, monkeypatch):
    session, _term, host, clock = rig
    looked = []
    monkeypatch.setattr(session, "_look_up_branch_pr", lambda: looked.append(1))
    session.set_transcript_path(_transcript(tmp_path, _reply()))
    # The read the path started hasn't landed: the click waits for it.
    session.request_update(discover=True)
    assert looked == []
    clock.advance(0)
    session.request_update()
    assert looked == [1]
    clock.advance(0)
    assert host.landed[-1] == ([], True)  # looked, and found nothing


def test_attached_and_restored_prs_join_the_tracked_list(rig, tmp_path, monkeypatch):
    from collins.prstatus import PullRequest, to_records

    monkeypatch.setattr(session_mod, "enrich", lambda pr: pr)
    session, _term, host, clock = rig
    session.set_transcript_path(_transcript(tmp_path, _reply()))
    clock.advance(0)
    attached = PullRequest(url="https://github.com/o/r/pull/2", number=2)
    assert session.attach_pr(attached)
    assert not session.attach_pr(attached)
    clock.advance(0)
    assert [pr.url for pr in host.landed[-1][0]] == [attached.url]
    restored = PullRequest(url="https://github.com/o/r/pull/1", number=1)
    session.restore_prs(to_records([restored]))
    clock.advance(0)
    # The saved order puts the restored one first.
    assert [pr.url for pr in host.landed[-1][0]] == [restored.url, attached.url]
    session.set_transcript_path(None)
    assert session.tracked_prs == {} and session.restored_prs == []


# -- activity -------------------------------------------------------------------------


def test_redraws_count_only_once_a_turn_was_asked_for(rig):
    session, _term, _host, _clock = rig
    assert not session.redraw_counts(startup_held=False)
    session.echo_gate.arm()
    assert session.redraw_counts(startup_held=False)


def test_the_sessions_own_writes_reach_the_echo_gate(rig):
    """A prompt the app types (new chat, composer, start_session) takes the
    service's road, never the VTE's commit: the session pokes and arms its
    own gate, and tells the host before the bytes land, while the pty is
    still pristine (the baseline's last snapshot)."""
    session, term, host, _clock = rig
    boxes: list[str] = []
    host.input_sent = lambda text: boxes.append(term.box)  # the box as the host hears it
    assert not session.echo_gate.armed
    session.write_text("hello")
    assert term.box == "hello" and not session.echo_gate.armed
    session.write_text("\r")
    assert term.box == ""  # submitted
    assert session.echo_gate.armed
    # Told before each write landed: the "\r" was announced with "hello"
    # still in the box.
    assert boxes == ["", "hello"]


def test_a_progress_quiet_overrules_the_redraw(rig):
    session, _term, _host, _clock = rig
    session.echo_gate.arm()
    assert session.progress.reading(1) == "mark"
    assert session.progress.reading(0) == "finish"
    assert not session.redraw_counts(startup_held=False)


def test_a_session_without_termprops_has_no_progress_watch(agent):
    term = FakeTerminal()
    session = Session(
        provider=ClaudeProvider(), pty=term, screen=term, host=FakeHost(),
        scheduler=FakeScheduler(), progress=False,
    )
    assert session.progress is None


# -- the cwd poll and follow ------------------------------------------------------------


def test_the_cwd_poll_ticks_while_the_session_lives(rig, agent):
    """The poll runs for as long as the host is alive — mapped or not: on
    the service the host has no screen, and `agent_cwd` is a fact every
    client reads (PR-1.12a) — and stops once the host is gone."""
    session, _term, host, clock = rig
    session.start_cwd_poll()
    assert host.cwds == ["/work"]
    clock.advance(session_mod.CWD_POLL_MS * 2)
    assert host.cwds == ["/work"] * 3
    host.is_mapped = False
    clock.advance(session_mod.CWD_POLL_MS * 2)
    assert len(host.cwds) == 5  # an unmapped host still hears it
    host.is_alive = False
    clock.advance(session_mod.CWD_POLL_MS * 3)
    assert len(host.cwds) == 5
    host.is_alive = True
    session.start_cwd_poll()  # a new life: polls again
    clock.advance(session_mod.CWD_POLL_MS)
    assert len(host.cwds) == 7


def test_a_move_settles_once(rig, tmp_path):
    session, _term, _host, _clock = rig
    root = tmp_path / "repo"
    worktree = root / ".claude" / "worktrees" / "w1"
    worktree.mkdir(parents=True)
    assert session.settle_cwd(str(worktree), str(root)) is None  # first sight
    scope = session.settle_cwd(str(worktree), str(root))
    assert scope is not None
    assert session.settle_cwd(str(worktree), str(root)) is None  # acted on already
    assert session.settle_cwd(str(root), str(root)) is None
    assert session.settle_cwd(str(root), str(root)) is None  # back home: no move
    assert session.settle_cwd(str(worktree), str(root)) is None
    assert session.settle_cwd(str(worktree), str(root)) == scope  # a new move


# -- launching -------------------------------------------------------------------------


class LaunchClaude(ClaudeProvider):
    """Claude Code with commands that don't depend on PATH."""

    def new_command(self, options=None):
        flags = " -w" if options is not None and options.worktree else ""
        return "claude" + flags

    def resume_command(self, session_id, fork=False, options=None):
        return f"claude --resume {session_id}"


class FakeSandboxHost:
    """sandboxplan.SandboxHost's launch half: boxes minted, plans written to
    *root*, leases and forgets recorded."""

    def __init__(self, root) -> None:
        self.root = root
        self.calls: list[tuple] = []
        self.plans = 0

    def mint_box(self, workspace, seed=True):
        self.calls.append(("mint", workspace, seed))
        return "box-1"

    def prepare_launch(self, cwd, box, worktree=""):
        self.plans += 1
        path = self.root / f"plan-{self.plans}.json"
        path.write_text("{}")
        self.calls.append(("prepare", cwd, box, worktree))
        return str(path)

    def release(self, box):
        self.calls.append(("release", box))

    def forget_box(self, box):
        self.calls.append(("forget", box))


@pytest.fixture
def launch(agent, tmp_path):
    def make(**kwargs):
        term = FakeTerminal()
        term.pid = None  # nothing spawned yet
        host = FakeHost()
        host.spawns = []
        host.spawn_shell = lambda cwd, env: host.spawns.append((cwd, env))
        host.sandbox_changed = lambda: host.events.append(("sandbox",))
        host.mark_stale_shells = lambda: host.events.append(("stale",))
        host.process_exited = lambda status: host.events.append(("exited", status))
        clock = FakeScheduler()
        session = Session(
            provider=LaunchClaude(), pty=term, screen=term, host=host, scheduler=clock, **kwargs
        )
        return session, term, host, clock

    return make


def _spawned(session, term) -> None:
    """What the tab does once VTE says the shell is up."""
    term.pid = SHELL_PID
    session.shell_spawned()


def test_a_fresh_launch_spawns_the_shell_and_types_the_command(launch, tmp_path):
    session, term, host, clock = launch(cwd=str(tmp_path))
    session.spawn(str(tmp_path), None)
    assert len(host.spawns) == 1
    cwd, env = host.spawns[0]
    assert cwd == str(tmp_path)
    assert "ConEmuANSI=ON" in env and "TERM_PROGRAM=kitty" in env
    assert term.writes == []
    _spawned(session, term)
    assert term.writes == ["claude\n"]
    assert session.initial_command == "claude"


def test_the_progress_spoof_can_be_off(launch, tmp_path):
    session, _term, host, _clock = launch(progress_env=False)
    session.spawn(str(tmp_path), None)
    assert host.spawns[0][1] is None


def test_a_missing_directory_falls_back_home_and_says_so(launch, tmp_path):
    session, _term, host, _clock = launch()
    gone = str(tmp_path / "gone")
    session.spawn(gone, None)
    assert host.spawns[0][0] == str(session_mod.Path.home())
    assert any("no longer exists" in line for line in host.painted())
    assert session.cwd == str(session_mod.Path.home())


def test_a_resume_types_the_resume(launch, tmp_path):
    session, term, host, _clock = launch(session_id="abc", cwd=str(tmp_path))
    session.spawn(str(tmp_path), "abc")
    _spawned(session, term)
    assert term.writes == ["claude --resume abc\n"]
    assert not session.worktree_launch


def test_a_failed_worktree_launch_is_retyped_without_it(launch, tmp_path, agent):
    from collins.providers import SessionOptions

    session, term, host, clock = launch(options=SessionOptions(worktree=True))
    session.spawn(str(tmp_path), None)
    _spawned(session, term)
    assert term.writes == ["claude -w\n"]
    assert session.worktree_launch
    clock.advance(session_mod.WORKTREE_LAUNCH_POLL_MS)
    assert len(term.writes) == 1  # nothing on screen yet
    agent["running"] = False
    term.screen_text_extra = "\nError creating worktree: not trusted"
    clock.advance(session_mod.WORKTREE_LAUNCH_POLL_MS)
    assert term.writes[-1] == "claude\n"
    assert not session.options.worktree
    assert not session.worktree_launch
    assert any("couldn't create a worktree" in line for line in host.painted())


def test_an_error_quoted_by_a_running_agent_is_never_typed_over(launch, tmp_path):
    from collins.providers import SessionOptions

    session, term, host, clock = launch(options=SessionOptions(worktree=True))
    session.spawn(str(tmp_path), None)
    _spawned(session, term)
    term.screen_text_extra = "\nError creating worktree: as quoted in a reply"
    clock.advance(session_mod.WORKTREE_LAUNCH_POLL_MS * (session_mod.WORKTREE_LAUNCH_POLL_TICKS + 2))
    assert term.writes == ["claude -w\n"]
    assert not session.worktree_launch  # the watch ran out: the launch worked


def test_the_new_chat_prompt_goes_in_at_the_empty_box(launch, tmp_path):
    session, term, host, clock = launch()
    session.hold_new_chat_prompt("hello there")
    session.spawn(str(tmp_path), None)
    session.start_new_chat_prompt_poll()
    clock.advance(session_mod.NEW_CHAT_PROMPT_POLL_MS)
    assert term.submitted == []  # no shell yet: no box
    _spawned(session, term)
    term.box = term.shown = ""  # the CLI is up, at its empty box
    term.writes.clear()
    clock.advance(session_mod.NEW_CHAT_PROMPT_POLL_MS + PROMPT_SUBMIT_MS)
    assert term.submitted == ["hello there"]
    assert session.new_chat_prompt is None
    assert ("focus",) in host.events


def test_a_new_chat_prompt_the_agent_never_took_is_stashed(launch, tmp_path, agent):
    session, term, host, clock = launch()
    session.hold_new_chat_prompt("keep me")
    session.spawn(str(tmp_path), None)
    _spawned(session, term)
    term.box = term.shown = "x"  # never an empty box
    agent["running"] = False
    term.foreground = SHELL_PID  # the shell owns the terminal: the CLI left
    session.start_new_chat_prompt_poll()
    clock.advance(session_mod.NEW_CHAT_PROMPT_POLL_MS * session_mod.NEW_CHAT_IDLE_SHELL_TICKS)
    assert host.stashed == ["keep me"]
    assert any("didn't start" in line for line in host.painted())


# -- the sandbox -------------------------------------------------------------------------


def test_a_sandboxed_launch_writes_its_plan_and_registers(launch, tmp_path):
    from collins.providers import SessionOptions

    box_host = FakeSandboxHost(tmp_path)
    session, term, host, clock = launch(
        options=SessionOptions(sandbox=True), sandbox_host=lambda: box_host
    )
    session.spawn(str(tmp_path), None)
    assert session.sandboxed
    assert session.sandbox_box == "box-1"
    assert session.sandbox_plan_path and os.path.isfile(session.sandbox_plan_path)
    assert session.options.sandbox_plan == session.sandbox_plan_path
    assert ("mint", str(tmp_path), True) in box_host.calls
    assert ("sandbox",) in host.events


def test_no_box_means_an_unsandboxed_launch_without_bypass(launch, tmp_path):
    from collins.providers import SessionOptions

    session, _term, host, _clock = launch(
        options=SessionOptions(sandbox=True, permission_mode="bypassPermissions")
    )
    session.spawn(str(tmp_path), None)
    assert not session.sandboxed
    assert session.options.permission_mode == ""
    assert any("no sandbox could be built" in line for line in host.painted())


def test_the_restart_resumes_in_a_rebuilt_box(launch, tmp_path, agent):
    from collins.providers import SessionOptions

    box_host = FakeSandboxHost(tmp_path)
    session, term, host, clock = launch(
        options=SessionOptions(sandbox=True),
        sandbox_host=lambda: box_host,
        session_id="sid",
    )
    session.spawn(str(tmp_path), "sid")
    _spawned(session, term)
    first_plan = session.sandbox_plan_path
    assert session.can_restart_sandboxed()
    assert session.restart_sandboxed()
    assert not session.can_restart_sandboxed()  # one at a time
    assert term.writes[-1] == "\x03\x03"
    clock.advance(session_mod.RESTART_POLL_MS)
    term.foreground = SHELL_PID  # the CLI exits
    clock.advance(session_mod.RESTART_POLL_MS)
    assert term.writes[-1] == "claude --resume sid\n"
    assert ("stale",) in host.events
    assert session.sandbox_plan_path != first_plan
    assert not os.path.exists(first_plan)
    assert session.restart_ticks is None


def test_a_restart_that_never_exits_gives_up(launch, tmp_path):
    from collins.providers import SessionOptions

    box_host = FakeSandboxHost(tmp_path)
    session, term, host, clock = launch(
        options=SessionOptions(sandbox=True), sandbox_host=lambda: box_host, session_id="s"
    )
    session.spawn(str(tmp_path), "s")
    _spawned(session, term)
    session.restart_sandboxed()
    clock.advance(session_mod.RESTART_POLL_MS * session_mod.RESTART_GIVE_UP_TICKS)
    nudges = [w for w in term.writes if w == "\x03\x03"]
    assert len(nudges) == 1 + len(session_mod.RESTART_NUDGE_TICKS)
    assert any("wasn't restarted" in line for line in host.painted())
    assert session.can_restart_sandboxed()


def test_the_shells_exit_releases_the_box(launch, tmp_path):
    from collins.providers import SessionOptions

    box_host = FakeSandboxHost(tmp_path)
    session, term, host, clock = launch(
        options=SessionOptions(sandbox=True), sandbox_host=lambda: box_host
    )
    session.spawn(str(tmp_path), None)
    plan = session.sandbox_plan_path
    session.shell_exited(0)
    assert not os.path.exists(plan)
    assert host.events[-1] == ("exited", 0)
    clock.advance(0)
    assert ("release", "box-1") in box_host.calls and ("forget", "box-1") in box_host.calls


# -- closing -----------------------------------------------------------------------------


@pytest.fixture
def closing(rig):
    session, term, host, clock = rig
    forced = []
    return session, term, host, clock, forced


def test_a_close_exits_and_nudges_on_budget(closing):
    session, term, host, clock, forced = closing
    session.begin_close("\x03\x03", False, lambda: forced.append(True))
    assert term.writes == ["\x03\x03"]
    clock.advance(session_mod.CLOSE_POLL_MS * session_mod.EXIT_NUDGE_TICKS[0])
    assert term.writes == ["\x03\x03", "\x03\x03"]  # the nudge
    clock.advance(session_mod.CLOSE_POLL_MS * session_mod.EXIT_FORCE_TICKS)
    assert forced == [True]
    pending = clock.pending()
    clock.advance(session_mod.CLOSE_POLL_MS * 5)
    assert forced == [True] and clock.pending() == pending


def test_a_handoff_gets_the_longer_budget(closing):
    session, term, host, clock, forced = closing
    session.begin_close("/bg\r", True, lambda: forced.append(True))
    clock.advance(session_mod.CLOSE_POLL_MS * session_mod.EXIT_FORCE_TICKS)
    assert forced == []
    clock.advance(session_mod.CLOSE_POLL_MS * session_mod.BG_FORCE_TICKS)
    assert forced == [True]
    nudges = [w for w in term.writes if w == "\x03\x03"]
    assert len(nudges) == len(session_mod.BG_NUDGE_TICKS)


def test_the_worktree_dialog_is_answered_keep(closing):
    session, term, host, clock, forced = closing
    term.screen_text_extra = "\n ❯ Keep worktree\n   Remove worktree"
    session.begin_close("\x03\x03", False, lambda: forced.append(True))
    clock.advance(session_mod.CLOSE_POLL_MS)
    assert term.writes[-1] == "\r"


def test_the_shell_is_told_to_exit_once_then_forced(closing):
    from collins.shellinput import shell_command

    session, term, host, clock, forced = closing
    session.begin_close("\x03\x03", False, lambda: forced.append(True))
    term.foreground = SHELL_PID  # the CLI is gone
    clock.advance(session_mod.CLOSE_POLL_MS)
    assert term.writes[-1] == shell_command("exit\r")
    term.foreground = AGENT_PGRP  # even if something takes the terminal again
    clock.advance(session_mod.CLOSE_POLL_MS * (session_mod.SHELL_EXIT_TICKS - 1))
    assert forced == []
    clock.advance(session_mod.CLOSE_POLL_MS)
    assert term.writes.count(shell_command("exit\r")) == 1
    assert "\x03\x03" not in term.writes[1:]  # never a CLI's exit at the shell
    assert forced == [True]


def test_end_close_stops_the_poll(closing):
    session, term, host, clock, forced = closing
    session.begin_close("\x03\x03", False, lambda: forced.append(True))
    session.end_close()
    clock.advance(session_mod.CLOSE_POLL_MS * 50)
    assert forced == [] and term.writes == ["\x03\x03"]


def test_a_nudge_skips_a_closed_tab_and_an_exited_cli(rig):
    session, term, host, _clock = rig
    host.is_alive = False
    session.nudge_exit()
    assert term.writes == []
    host.is_alive = True
    term.foreground = SHELL_PID
    session.nudge_exit()
    assert term.writes == []
    term.foreground = AGENT_PGRP
    session.nudge_exit()
    assert term.writes == ["\x03\x03"]


# -- landing priorities ------------------------------------------------------------------


def test_the_fake_runs_default_priority_idles_first():
    clock = FakeScheduler()
    order = []
    clock.idle_add(lambda: order.append("idle"))
    clock.idle_add(lambda: order.append("default"), priority=PRIORITY_DEFAULT)
    clock.advance(0)
    assert order == ["default", "idle"]


def test_gate_resetting_landings_are_never_starvable(launch, tmp_path, monkeypatch):
    """CLAUDE.md's main-loop rule: a landing that resets a gate or advances
    a pipeline runs at PRIORITY_DEFAULT (CI's Xvfb starves default-idle
    forever). These are the ones the session marks so."""
    from collins.providers import SessionOptions

    box_host = FakeSandboxHost(tmp_path)
    session, term, host, clock = launch(
        options=SessionOptions(sandbox=True), sandbox_host=lambda: box_host, session_id="sid"
    )

    # The transcript read's landing, which resets the update gate.
    session.request_update()
    assert clock.idle_priority("_apply_update") == PRIORITY_DEFAULT

    # A sandboxed fallback without its worktree, once the box is unmounted.
    session.reserved_worktree = str(tmp_path / "wt")
    session.relaunch_without_worktree()
    assert clock.idle_priority("_type_without_worktree") == PRIORITY_DEFAULT

    # The restart: the relaunch once the box is unmounted, and the resume
    # once a reaped worktree has been put back.
    session.spawn(str(tmp_path), "sid")
    _spawned(session, term)
    session.restart_sandboxed()
    term.foreground = SHELL_PID
    clock.advance(session_mod.RESTART_POLL_MS)
    assert clock.idle_priority("_relaunch_sandboxed") == PRIORITY_DEFAULT
    session.reserved_worktree = str(tmp_path / "wt")
    monkeypatch.setattr(
        session_mod, "recreatable_worktree",
        lambda path, wt: {"worktreePath": session.reserved_worktree},
    )
    monkeypatch.setattr(session_mod, "recreate_worktree", lambda state: True)
    session.restart_ticks = 1
    session._relaunch_sandboxed()
    assert clock.idle_priority("_type_restart") == PRIORITY_DEFAULT

    # The shell's exit: the box released and forgotten.
    session.shell_exited(0)
    assert clock.idle_priority("gone") == PRIORITY_DEFAULT


def test_the_recreated_worktree_spawn_lands_at_default_priority(launch, tmp_path, monkeypatch):
    """The recreated-worktree landing advances the pipeline, so it lands at
    PRIORITY_DEFAULT like every other gate-resetting landing (it was the
    one default-idle landing until PR-1.7). The shell is spawned in the
    reaped worktree's directory at once, made empty (PR-1.12a: the spawn
    answers with its pty), and the command waits for the checkout."""
    session, term, host, clock = launch(session_id="sid")
    worktree = tmp_path / "wt"
    monkeypatch.setattr(
        session_mod, "recreatable_worktree",
        lambda path, cwd: {"worktreePath": str(worktree)},
    )
    monkeypatch.setattr(session_mod, "recreate_worktree", lambda state: True)
    session.spawn(str(tmp_path), "sid")
    assert worktree.is_dir() and host.spawns and host.spawns[0][0] == str(worktree)
    assert session.cwd == str(worktree) and session.initial_command is None
    _spawned(session, term)
    assert term.writes == []  # nothing typed before the checkout is back
    assert clock.idle_priority("_recreated") == PRIORITY_DEFAULT
    clock.advance(0)
    assert term.writes == ["claude --resume sid\n"]
    assert session.initial_command == "claude --resume sid"


def test_a_worktree_that_cannot_be_put_back_falls_back_with_a_warning(
    launch, tmp_path, monkeypatch
):
    """The checkout never came back: the shell, already up in the emptied
    directory, is moved to where _finish_spawn's fallback would have
    started it, and the command typed there."""
    session, term, host, clock = launch(session_id="sid")
    worktree = tmp_path / "wt"
    monkeypatch.setattr(
        session_mod, "recreatable_worktree",
        lambda path, cwd: {"worktreePath": str(worktree)},
    )
    monkeypatch.setattr(session_mod, "recreate_worktree", lambda state: False)
    released = []
    monkeypatch.setattr(session_mod.sandboxplan, "release_worktree", lambda p: released.append(p))
    monkeypatch.setattr(session_mod, "worktree_project_root", lambda p: str(tmp_path))
    session.spawn(str(tmp_path), "sid")
    _spawned(session, term)
    clock.advance(0)
    assert released == [str(worktree)]
    assert session.cwd == str(tmp_path)
    assert any("no longer exists" in line for line in host.painted())
    assert term.writes[0].endswith(f"cd {tmp_path}\n") and term.writes[-1] == "claude --resume sid\n"
