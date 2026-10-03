# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""A session tab's logic, out of the widget: `Session`.

`terminal.TerminalTab` used to be both the widget and everything the widget's
terminal was *for* — reading the agent's input box, typing into it, cutting a
prompt out of it for the composer. Those state machines could only be tested
by driving a real VTE behind a fake CLI. `Session` holds them now, GTK-free,
and reaches its terminal through two ports (`ports.PtyPort` to write,
`ports.ScreenPort` to read; spec §3.5). `TerminalTab` builds one per tab over
adapters on its own `Vte.Terminal` (`terminal.VtePtyPort`,
`terminal.VteScreenPort`) and keeps every public name it had as a forwarder,
so nothing that talks to a tab had to change.

What lives here:

- the session's identity as launched: id, fork, provider, options, the
  command override, the directory it was handed;
- reading the agent's input box: `takes_prompt`, `entered_prompt`,
  `prompt_block`, the visible screen, the worktree-exit dialog, the screen's
  first column;
- writing to it: `inject_prompt` (and its unfocused, bracketed-paste
  sibling), the model and effort switches, the composer's open-cut with its
  settle and verify rounds, and the close's paste-back;
- the process questions those writes are gated on: whether the agent is
  running, its pid, whether something other than the shell owns the
  terminal — and the rest of the /proc walks: the agent's cwd, what runs
  below it, whose ancestry an MCP caller is;
- the transcript and its tail: the file monitor, the poll, the off-thread
  parse and its landing, the PRs collected from it (tracked, restored,
  attached), the model, effort and permission mode it names;
- the transcript resolver that binds a fresh session to the id the CLI
  mints (and reports a sandboxed fork's);
- activity: the echo gate, the spinner watch, the progress watch and the
  finish ledger the window's ActivityTracker is fed through, and the
  redraw verdict they give;
- the cwd poll and the settling of a move the editor follows.

What stays in the tab: every widget, overlay, dialog and the footer, the
composer itself (the cut reaches it through a `CutSink`), the dock.

The host. A Session tells its tab what happened, and asks it the few things
only a widget knows, through a `SessionHost` — a plain listener object
(`typing.Protocol`), chosen over GObject signals so a test can hand in a
recorder and read back exactly what was asked and painted, in order, with no
main loop. The tab implements it with a small private adapter
(`terminal._TabHost`) so the tab's own namespace stays the tab's.

The scheduler. Every timer, idle and thread goes through a `Scheduler`
(`GLibScheduler` by default: `GLib.timeout_add`, `GLib.idle_add`, a daemon
thread). A test passes a fake whose clock it advances by hand, so a state
machine whose steps are 50 ms to 1.5 s apart runs in microseconds and in a
fixed order (tests/test_session.py has the fake). The delays and the
priorities are the ones the tab always used — the PRIORITY_DEFAULT landings
included, which CI's Xvfb would otherwise starve.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any, Protocol

from gi.repository import Gio, GLib

from .. import activity, composerkeys, editorfiles, proctree
from ..gitinfo import current_branch
from ..i18n import _
from ..providers import EnteredPrompt, Provider
from ..prstatus import (
    PullRequest,
    discover_pr,
    enrich,
    from_records,
    invalidate,
    merge_ordered,
)
from ..sessions import worktree_shares_project
from ..transcript import TranscriptModel
from .ports import PtyPort, ScreenPort

# The transcript tail: how long a burst of file-change events is let settle
# before the read, and the backstop poll for a monitor that missed one.
TRANSCRIPT_DEBOUNCE_MS = 400
PROMPT_POLL_MS = 1000
# A session links every PR that passes through its tool output, including ones
# it only read, so the row is bounded: it tracks (and saves, and refreshes)
# the newest this many, and a session that busy has stopped caring about its
# first. How many of them are on screen is a question of width, not of this
# (see terminal.PrChipRow).
MAX_PR_CHIPS = 20
# The transcript resolver's poll, and how many unmapped ticks it spends
# before pausing until the tab is shown again (~3 min).
RESOLVER_POLL_MS = 1500
RESOLVER_BACKGROUND_TICKS = 120
# The cwd poll that feeds the footer; it only ticks while the tab is mapped.
CWD_POLL_MS = 2000
# How many consecutive cwd polls a new working directory has to survive before
# the editor follows it (see settle_cwd). Two is enough to ride out the flap
# around a CLI starting, exiting or being forked, and still lands inside the
# pause after a worktree is entered.
EDITOR_FOLLOW_TICKS = 2

# How long an injected prompt is left sitting in the input before the Return
# that sends it (see inject_prompt). Long enough that the CLI has stopped
# reading the text as a paste, short enough that nobody watching sees a pause.
PROMPT_SUBMIT_MS = 250
# The bracketed-paste control sequences (ESC[200~ … ESC[201~) that tell a
# terminal app the text between them was pasted, not typed — so its newlines
# stay literal instead of each submitting. See inject_prompt_unfocused.
PASTE_START = "\x1b[200~"
PASTE_END = "\x1b[201~"
# The composer's open-cut, which is a run of screen reads either side of an
# erase (see Session.begin_cut). The read is taken once CUT_SETTLE_READS of
# them CUT_SETTLE_MS apart agree — 150ms of a still input box, measured
# against the CLI (2.1.226), which finishes echoing a burst of typing 30-100ms
# after the last key. A box still moving after CUT_SETTLE_TRIES of them is
# not cut at all: erasing a read that is still catching up would take the
# characters it hasn't shown yet with it. The erase is then checked again
# CUT_VERIFY_MS apart — each gap measured from the check before it, so the
# last one lands about a second and a half after the cut — widening because a
# busy CLI can take a while to work through a line of backspaces.
CUT_SETTLE_MS = 50
CUT_SETTLE_READS = 4
CUT_SETTLE_TRIES = 12
CUT_VERIFY_MS = (150, 400, 900)


def bracketed_paste(text: str) -> str:
    """*text* wrapped as one bracketed paste, safe to feed to a CLI's input.

    Carriage returns are normalized to newlines (a bare CR reads as Enter —
    a submit mid-prompt), and any paste-end marker already in the text is
    dropped so the agent's own prompt can't close the wrapper early and leave
    its tail arriving as live keystrokes.
    """
    body = text.replace("\r\n", "\n").replace("\r", "\n").replace(PASTE_END, "")
    return f"{PASTE_START}{body}{PASTE_END}"


def _prompt_read(prompt: EnteredPrompt | None) -> tuple[str, int] | None:
    """What two settle reads of the CLI's input box compare, so that "the
    box is still empty" counts as agreement too (see Session._settle_cut)."""
    return None if prompt is None else (prompt.text, prompt.rows_below)


class Scheduler(Protocol):
    """Timers, idles and threads, as a Session asks for them."""

    def timeout_add(self, ms: int, fn: Callable[..., bool], *args: Any) -> int:
        """Call ``fn(*args)`` in *ms* milliseconds, and again every *ms*
        for as long as it returns True (GLib.timeout_add's contract)."""
        ...

    def idle_add(self, fn: Callable[..., bool], *args: Any, priority: int = ...) -> int:
        """Call ``fn(*args)`` from the main loop at *priority* (default:
        GLib.PRIORITY_DEFAULT_IDLE); safe to call from any thread."""
        ...

    def background(self, fn: Callable[..., None], *args: Any) -> None:
        """Run ``fn(*args)`` off the main loop, on a daemon thread."""
        ...

    def time(self) -> float:
        """The wall clock, in seconds (what file mtimes are compared with)."""
        ...

    def monitor_file(self, path: str, on_changed: Callable[[], None]) -> Any:
        """Call *on_changed* whenever the file at *path* changes; returns a
        handle with a ``cancel()``, or None when nothing can watch it."""
        ...


class GLibScheduler:
    """The real `Scheduler`: GLib's main loop, a daemon thread, and a
    `Gio.FileMonitor` (Gio, like the store's monitors: no GTK)."""

    def timeout_add(self, ms: int, fn: Callable[..., bool], *args: Any) -> int:
        return GLib.timeout_add(ms, fn, *args)

    def idle_add(
        self, fn: Callable[..., bool], *args: Any, priority: int = GLib.PRIORITY_DEFAULT_IDLE
    ) -> int:
        return GLib.idle_add(fn, *args, priority=priority)

    def background(self, fn: Callable[..., None], *args: Any) -> None:
        threading.Thread(target=fn, args=args, daemon=True).start()

    def time(self) -> float:
        return time.time()

    def monitor_file(self, path: str, on_changed: Callable[[], None]) -> Any:
        try:
            monitor = Gio.File.new_for_path(path).monitor_file(Gio.FileMonitorFlags.NONE, None)
        except GLib.Error:
            return None
        monitor.connect("changed", lambda *_args: on_changed())
        return monitor


class SessionHost(Protocol):
    """What a Session tells its tab, and the few things it asks of it."""

    def alive(self) -> bool:
        """Whether the tab is still in a window — False once it has been
        closed (or detached on its way out); the polls stop then."""
        ...

    def paint(self, text: str) -> None:
        """Put a "[session manager]" line on the terminal for the user (not
        into the pty: nothing reaches the agent)."""
        ...

    def focus_terminal(self) -> None:
        """Put the keyboard in the agent's terminal."""
        ...

    def composer_open(self) -> bool:
        """Whether the composer is up over (or docked beside) the terminal:
        the CLI's box is then the composer's to manage."""
        ...

    def refocus_composer(self) -> None:
        """Give the keyboard back to the open composer once the popover that
        asked for a switch has closed (the tab defers it to an idle)."""
        ...

    def resend_composed(self) -> None:
        """A composer send that waited out a cut's settle: send what the
        composer holds now, if it is still open."""
        ...

    def stash_draft(self, text: str) -> None:
        """Keep *text* as the tab's composer draft — a draft the terminal
        wouldn't take."""
        ...

    def mapped(self) -> bool:
        """Whether the tab is on screen: the cwd poll ticks only while it
        is, and the resolver spends its background budget while it isn't."""
        ...

    def shown_prs(self) -> list[PullRequest]:
        """The PRs the footer's chips show right now — what a branch
        lookup marks due first. Read on the update thread, as the tab's
        own list always was (it is replaced wholesale, never mutated)."""
        ...

    def transcript_reset(self) -> None:
        """The session was pointed at another transcript: what the tab
        showed from the old one (its chips, its model and effort, the
        attachments restored for it) goes."""
        ...

    def transcript_landed(self, prs: list[PullRequest], lookup_empty: bool) -> None:
        """A transcript read landed on the main loop. *prs* is what the
        chips show (the newest MAX_PR_CHIPS, with status); *lookup_empty* is
        a branch lookup that found nothing."""
        ...

    def session_resolved(self, session_id: str) -> None:
        """The resolver bound a fresh session to the id the CLI minted."""
        ...

    def fork_resolved(self, session_id: str) -> None:
        """A sandboxed fork's resolver found the forked conversation's id."""
        ...

    def cwd_polled(self, cwd: str | None) -> None:
        """One tick of the cwd poll: where the agent is working now."""
        ...


class CutSink(Protocol):
    """Where an open-cut lands: the composer that asked for it."""

    def alive(self) -> bool:
        """Whether this composer is still the tab's, and still open."""
        ...

    def seed(self, text: str) -> None:
        """Put the cut text in the composer (appended, cursor at the end)."""
        ...

    def refuse(self) -> None:
        """The box holds a paste no read can recover: put back whatever the
        composer gathered meanwhile, lower it without restoring, and say
        why."""
        ...


class Session:
    """One agent session's logic, behind a pty and a screen (see the module
    docstring for what lives here and what stays in the tab)."""

    def __init__(
        self,
        *,
        provider: Provider,
        pty: PtyPort,
        screen: ScreenPort,
        host: SessionHost,
        session_id: str | None = None,
        fork: bool = False,
        options=None,
        command_override: str | None = None,
        cwd: str | None = None,
        jsonl_path: str | Path | None = None,
        progress: bool = True,
        scheduler: Scheduler | None = None,
    ) -> None:
        """*progress* says whether the terminal parses the agent's own
        progress announcements (VTE's termprop; a VTE too old has none), so
        whether there is a `ProgressWatch` to feed."""
        self.provider = provider
        self.pty = pty
        self.screen = screen
        self.host = host
        self.session_id = session_id
        self.fork = fork
        # The SessionOptions the session was launched with (a start_session
        # caller's model and permission mode, the sandbox decision), or None.
        self.options = options
        self.command_override = command_override
        # The directory the tab was handed, until the spawn settles on where
        # the shell actually runs: what cwd readers (the dock's shells, the
        # composer's file picker) answer with while nothing is spawned — the
        # new-chat screen's whole life.
        self.cwd: str | None = cwd
        self.scheduler: Scheduler = scheduler or GLibScheduler()
        # The text an open-cut took out of the CLI's box and hasn't proved
        # gone yet — what a leftover has to match before anything erases it
        # (see _verify_cut) — and the number of the cut that took it, which
        # anything writing to that box on its own account bumps to call the
        # rounds still in flight off.
        self.cut_pending: str | None = None
        self.cut_seq = 0
        # Whether a cut is still deciding what the box holds, and a send that
        # arrived while it was (see send_composed).
        self.cut_settling = False
        self.send_after_settle = False
        # A /model or /effort switch (the command, and the chat's line for
        # an agent that isn't running) held back by a cut still settling.
        self.switch_after_settle: tuple[str, str] | None = None
        # What the last close's paste-back turned into on the CLI's screen:
        # every "[Pasted text #N +M lines]" stand-in the CLI folded a piece
        # of it into, mapped to that piece's text, so the next open can put
        # the draft itself in the composer rather than the stand-in (see
        # restore_draft). `paste_back_pending` holds the pieces from the
        # moment they are fed until a screen read has said how they landed;
        # `paste_back_agent` is the CLI process the stand-ins belong to —
        # their numbers start over with a new one.
        self.pasted_back: dict[str, str] = {}
        self.paste_back_pending: list[str] | None = None
        self.paste_back_agent: int | None = None

        # The transcript, and its tail: the file monitor, the backstop poll,
        # the debounce, and whether an off-thread parse is in flight.
        self.transcript = TranscriptModel(jsonl_path)
        self._transcript_monitor = None
        self._transcript_refresh_source: int | None = None
        self._poll_source: int | None = None
        self._updating = False
        self._pr_discover = False  # a click's search, waiting for a free tick
        # Every PR this session has opened, oldest first: url -> PR with the
        # last status known for it (see _collect_prs). Replaced wholesale,
        # never mutated in place — the update thread reads it while the main
        # loop writes it.
        self.tracked_prs: dict[str, PullRequest] = {}
        self.restored_prs: list[PullRequest] = []  # this session's, from a previous run
        # PRs the session named itself, via the attach_pr tool. Folded into
        # every collection rather than written into tracked_prs, which an
        # in-flight update replaces wholesale when it lands (see attach_pr).
        # Replaced wholesale too, for the same thread-safety reason.
        self.attached_prs: dict[str, PullRequest] = {}

        # The transcript resolver: a fresh session has no id until the CLI
        # writes its transcript, which this polls for.
        self._resolver_source: int | None = None
        self._resolver_attempts = 0
        self.resolver_cwd: str | None = None  # set iff this session resolves its own transcript
        self._baselined_dirs: set[str] = set()  # dirs whose pre-existing transcripts are excluded
        self._known_transcripts: set[Path] = set()  # transcripts predating this session
        self._resolver_armed_at = 0.0  # wall-clock time polling (re)started
        self.fork_resolve = False  # a sandboxed fork: report the new id, don't bind to it

        # Activity: the per-terminal watches the window's ActivityTracker is
        # fed through (see activity.py) — the echo gate, the spinner, the
        # agent's own progress word — and the finish ledger, armed by the
        # first transcript read that lands (_apply_update) and asked by the
        # window at each finish edge.
        self.echo_gate = activity.EchoGate()
        self.spinner = activity.SpinnerWatch()
        self.progress: activity.ProgressWatch | None = (
            activity.ProgressWatch() if progress else None
        )
        self.finish_ledger = activity.FinishLedger()

        # The cwd poll, and following the agent's working directory (see
        # settle_cwd): the cwd it has to hold still at, how many polls it has
        # held it for, and the last one already acted on — offered and
        # declined counts as acted on, so a banner ignored doesn't come back
        # every two seconds.
        self._cwd_source: int | None = None
        self._follow_pending: str | None = None
        self._follow_ticks = 0
        self._follow_settled: str | None = None

    # -- writing to the pty ---------------------------------------------------

    def write_text(self, text: str) -> None:
        """Type *text* into the terminal's child, as keystrokes."""
        self.pty.write(text.encode())

    # -- the processes behind the terminal -----------------------------------

    def child_pid(self) -> int | None:
        """The shell spawned on the pty, or None while there is none."""
        return self.pty.child_pid()

    def candidate_pids(self) -> list[int]:
        """Pids worth searching for the agent process: the terminal's
        foreground process group leader, then the child originally spawned.

        The group leader is not always the process that moves — a
        daemon-hosted session leaves a wrapper at its head and runs the agent
        as its child — so both ends are worth trying.
        """
        pids = []
        foreground = self.pty.foreground_pgrp()
        if foreground is not None:
            pids.append(foreground)
        child = self.pty.child_pid()
        if child is not None:
            pids.append(child)
        return pids

    def has_running_command(self) -> bool:
        """True when something other than the spawned shell owns the
        terminal's foreground — the cue terminal emulators use for
        close-confirmation (e.g. claude, here)."""
        child = self.pty.child_pid()
        if child is None:
            return False
        foreground = self.pty.foreground_pgrp()
        if foreground is None:
            return False
        try:
            return foreground not in (-1, os.getpgid(child))
        except OSError:
            return False

    def agent_is_running(self) -> bool:
        """Whether the provider's CLI is alive in this terminal right now —
        the same descendant search current_agent_cwd runs, minus its
        shell-cwd fallbacks. False means whatever is at the prompt is not
        the agent (a plain shell, or something the user launched)."""
        cli = getattr(self.provider, "cli", "") or ""
        return any(
            proctree.agent_descendant_cwd(pid, cli) is not None for pid in self.candidate_pids()
        )

    def agent_pid(self) -> int | None:
        """The agent CLI's own process, found below either candidate pid."""
        cli = getattr(self.provider, "cli", "") or ""
        for pid in self.candidate_pids():
            agent = proctree.agent_descendant_pid(pid, cli)
            if agent is not None:
                return agent
        return None

    # -- reading the agent's input box --------------------------------------

    def takes_prompt(self) -> bool:
        """Whether a prompt sent right now would land in an empty input box.

        The provider reads that off the screen (see Provider.takes_prompt); all
        this does is find what it reads — the line the cursor is on, how far
        into it the cursor sits, and whether the rest of that line is the
        agent's own dim ghost text — and rule out a terminal with no agent
        left in it.
        """
        if self.pty.child_pid() is None:
            return False
        column, row = self.screen.cursor()
        text = self.screen.row_text(row, self.screen.columns())
        # What the line says is enough to say yes to an empty input, and that
        # is the answer nearly every time this is asked; only a line that reads
        # as written-in is worth a second look at how it was drawn.
        if self.provider.takes_prompt(text, column):
            return True
        return self.provider.takes_prompt(text, column, self.screen.tail_is_faint(row, column))

    def prompt_block(self) -> str:
        """Why a prompt sent to this session wouldn't land, or "" when it would.

        The sentence a PR menu greys its prompt actions out with (see
        prmenu.ActionHost). One line covers every no: an agent that has exited,
        one mid-turn, one at a permission dialog and one with half a sentence
        already typed are all "not at an empty input", and the fix for all four
        is to look at the terminal.
        """
        return "" if self.takes_prompt() else _("This session isn't at an empty prompt.")

    def entered_prompt(self) -> EnteredPrompt | None:
        """The prompt typed into the agent's input box and not yet sent, or
        None with no agent, an empty box (takes_prompt — which also rules
        out the box's dim ghost suggestion, indistinguishable from typed
        text in a plain-text read), or no box on screen at all.

        The screen is read the way the other readers do — one cursor-anchored
        snapshot, never adjustment-derived grid rows (see
        terminal._resolve_wrapped_at for why), split back into screen rows
        (ScreenPort.rows) — but reaching *past* the cursor too: continuation
        rows sit below it whenever the cursor was arrowed back up into the box.
        """
        if self.pty.child_pid() is None or self.takes_prompt():
            return None
        _column, cursor_row = self.screen.cursor()
        rows = self.screen.rows()
        return self.provider.entered_prompt(rows, cursor_row, self.screen.columns())

    def visible_screen_text(self) -> str:
        """Everything on the terminal's visible screen, as plain text.

        Anchored to the cursor rather than to the scroll position, like the
        other screen readers here: what the user has scrolled back to never
        changes what the provider is shown. "" with no child running.
        """
        if self.pty.child_pid() is None:
            return ""
        return self.screen.visible_text()

    def worktree_exit_prompt_keystrokes(self) -> str | None:
        """Keystrokes that accept the agent's "leaving a worktree" dialog if
        it's showing right now, or None if it isn't (see
        Provider.worktree_exit_prompt). The whole visible screen, not just
        the cursor's line — this dialog is a multi-line menu, not something
        drawn at the input prompt."""
        if self.pty.child_pid() is None:
            return None
        return self.provider.worktree_exit_prompt(self.visible_screen_text())

    def screen_first_column(self) -> tuple[tuple[str, ...], tuple[int, int]] | None:
        """The first character of each visible screen row ("" for a blank
        one), with the (columns, rows) grid it was read at — what the
        window's SpinnerWatch compares between samples — or None with no
        child to be busy. Anchored to the cursor like the other screen
        readers, so the user scrolling back never changes what is read."""
        if self.pty.child_pid() is None:
            return None
        return self.screen.first_column(), (self.screen.columns(), self.screen.row_count())

    # -- typing prompts and switches -----------------------------------------

    def inject_prompt(self, text: str) -> None:
        """Type *text* into the agent, send it, and put the tab in front.

        What the PR menu's prompt actions do. Only offered while
        `takes_prompt` says the input is empty, so nothing of the user's is
        ever sent along with it.

        The Return goes in a second write, a beat later, rather than on the
        end of the first: an agent CLI reads a chunk arriving all at once as a
        paste, and a Return inside a paste is a newline in the box — it left
        the prompt typed out and waiting for someone to press enter. Arriving
        on its own, after the input has settled, it submits.
        """
        self.post_prompt(text)
        self.host.focus_terminal()

    def inject_prompt_unfocused(self, text: str) -> None:
        """Submit *text* to the agent without taking focus or the view — what
        a background spawn does with the prompt the start_session tool handed
        it (see App._mcp_start_session). inject_prompt's sibling, minus the
        grab: a session no one is looking at must not pull the keyboard over.

        Where inject_prompt only ever carried the PR menu's one-liners, a tool
        prompt is arbitrary user text and often multi-line. It is wrapped in an
        explicit bracketed paste so its newlines stay literal in the box
        however the write is chunked — the CLI keeps bracketed paste on — and
        any carriage returns (a stray submit mid-prompt) or paste-end markers
        (an early close of the wrapper) are stripped first. The submitting
        Return still travels on its own a beat later (post_prompt), after the
        paste has closed, so the whole thing lands as one turn.
        """
        self.post_prompt(bracketed_paste(text))

    def post_prompt(self, text: str) -> None:
        """Type *text* into the agent and submit it a beat later (see
        inject_prompt for why the Return travels alone) — without touching
        focus, for the callers that shouldn't move it (a switch, while the
        composer holds the keyboard)."""
        self.write_text(text)
        self.scheduler.timeout_add(PROMPT_SUBMIT_MS, self._submit_prompt)

    def _submit_prompt(self) -> bool:
        self.write_text("\r")
        return GLib.SOURCE_REMOVE

    def switch_model(self, model_id: str) -> None:
        """Post the provider's model-switch command to the chat — what a
        pick in either model menu (the footer label's, the composer's)
        means. The command is a prompt like any other to the terminal; the
        CLI answers it in the transcript, and the footer label follows
        within a poll. See _post_switch for how it reaches the box."""
        command = self.provider.model_switch_command(model_id)
        if command is None:
            return
        self._post_switch(command, _("Model switch: the agent isn't running in this tab"))

    def switch_effort(self, effort: str) -> None:
        """Post the provider's effort-switch command to the chat — what a
        pick in either effort menu (the footer chip's, the composer's)
        means, on the same terms as switch_model: the CLI answers in the
        transcript, and the chip follows within a poll."""
        command = self.provider.effort_switch_command(effort)
        if command is None:
            return
        self._post_switch(command, _("Effort switch: the agent isn't running in this tab"))

    def _post_switch(self, command: str, not_running: str) -> None:
        """Type a switch *command* into the CLI's box — the road both
        switch_model and switch_effort take. *not_running* is the chat's
        line when there is no agent to type it to.

        With the composer up, the CLI's box is the composer's to manage —
        emptied by the open-cut — so the command types straight in and the
        composer stays exactly as it was, draft and all: switching models
        mid-draft is the point of putting a picker there. The two cut races
        the composer's own send can hit apply unchanged (send_composed tells
        them in full): a cut still settling holds the command back and
        _end_settling lets it go, and one still proving the box empty gets
        finished first, a beat ahead of the command.

        Without a composer the box is the user's, so the command is only
        posted at an empty prompt — inject_prompt's own bargain — and the
        chat says why when it isn't.
        """
        if not self.agent_is_running():
            self.host.paint(not_running)
            return
        if self.host.composer_open():
            if self.cut_settling:
                self.switch_after_settle = (command, not_running)
                return
            leftover = self._take_cut_leftover()
            if leftover:
                self.write_text(leftover)
                self.scheduler.timeout_add(CUT_VERIFY_MS[0], self._post_after_cut, command)
            else:
                self.post_prompt(command)
            # The keyboard goes back to the draft: the popover's close is
            # about to hand focus to the button that opened it, so the
            # re-grab waits out that close (popovers undo a grab made during
            # their own action).
            self.host.refocus_composer()
            return
        block = self.prompt_block()
        if block:
            self.host.paint(block)
            return
        self.inject_prompt(command)

    def _post_after_cut(self, text: str) -> bool:
        self.post_prompt(text)
        return GLib.SOURCE_REMOVE

    # -- the composer's send -------------------------------------------------

    def send_composed(self, text: str, clear: Callable[[], None]) -> None:
        """Submit *text*, the composer's draft, to the agent — the composer's
        Send once the tab has dealt with an empty draft. *clear* empties
        the composer (and lowers it, unless it is docked) the moment the
        send is sure to go ahead.

        Not re-gated on takes_prompt: the box was emptied at open, and
        anything typed into the terminal since submits along with this,
        same as if the user had pressed Enter there. It IS re-gated on the
        agent still being in the terminal — the text-then-Return of a submit
        aimed at a shell would *execute* the draft — and an undeliverable
        send keeps the panel up with the draft in it, losing nothing.

        A send can outrun the open-cut, which is a chain of screen reads
        and takes a beat (see begin_cut). Two beats to outrun, and one each:

        * A cut still deciding what the box holds is *waited* for, never
          worked around — the box would otherwise keep the prompt that was
          about to be taken out of it, and typing this one after it sends
          the two jammed together. `_end_settling` sends for us the moment
          it knows (`SessionHost.resend_composed`).

        * A cut that has erased but not yet proved the box empty carries
          its last check here: whatever it still can't account for is
          erased first, a beat ahead of the prompt rather than in front of
          it in the same write — a chunk opening with backspaces is a
          chunk the CLI could read as pasted text.
        """
        if self.cut_settling:
            self.send_after_settle = True
            return
        if not self.agent_is_running():
            self.host.paint(_("Composer: the agent isn't running in this tab"))
            return
        leftover = self._take_cut_leftover()
        clear()
        if leftover:
            self.write_text(leftover)
            self.scheduler.timeout_add(CUT_VERIFY_MS[0], self._inject_after_cut, text)
            return
        self.inject_prompt(text)

    def _inject_after_cut(self, text: str) -> bool:
        self.inject_prompt(text)
        return GLib.SOURCE_REMOVE

    def _take_cut_leftover(self) -> str | None:
        """What a cut still pending left in the box, as the keys that erase
        it — and the cut called off: the text about to be typed is not a
        cut's to erase."""
        leftover = (
            self._leftover_cut_keys(self.cut_pending) if self.cut_pending is not None else None
        )
        self.cut_pending = None
        self.cut_seq += 1
        return leftover

    def cancel_cut(self) -> None:
        """Call off any cut in flight: the box is about to hold the
        composer's text again (a close's paste-back), and no round of an
        older cut may erase that."""
        self.cut_pending = None
        self.cut_seq += 1

    # -- the composer's open-cut ---------------------------------------------

    def begin_cut(self, sink: CutSink) -> None:
        """Take the typed-but-unsent prompt out of the CLI's input box and
        into the composer behind *sink* — the open-cut, run as a chain of
        screen reads.

        Both halves of it need a beat, which is why this isn't an inline
        read at open:

        * The **read** is only worth trusting once the screen has stopped
          moving. The CLI echoes what was typed a repaint later, so a
          composer opened from the keyboard the instant a prompt was typed
          reads a line still catching up — and erasing that read would eat
          the characters it hadn't shown yet, which no later round can get
          back. A run of identical reads is the settle test, and a box
          that never settles is left alone.

        * The **erase** is checked afterwards, because a read can fall
          short of the buffer it renders even settled: an invisible
          trailing space is dropped, and so is the space a wrap ate
          between two long words. The erase is one backspace per character
          read, running backwards from the end, so a read one character
          short leaves the box holding the *first* character of the prompt
          — which the composer's copy starts with too, so the send that
          follows types it twice.

        Nothing is cut when there is nothing to take or the provider can't
        clear its box safely: no half-cut that leaves the text behind for
        a send to duplicate.
        """
        self.cut_pending = None
        self.cut_seq += 1
        self.cut_settling = True
        self._settle_cut(sink, self.cut_seq, None, 0, 0)

    def _settle_cut(
        self,
        sink: CutSink,
        seq: int,
        previous: EnteredPrompt | None,
        agreed: int,
        attempt: int,
    ) -> bool:
        """One settle read, cutting once *agreed* of them in a row match.

        An empty box answers None to every read, which agrees with itself
        like any other answer: the ordinary open settles on the fourth read
        and cuts nothing.

        Every way out of here ends the settling, because a send held back
        for it (`send_after_settle`) has to be let go of on all of them.
        """
        if not self._cut_alive(sink, seq):
            self._end_settling()
            return GLib.SOURCE_REMOVE
        prompt = self.entered_prompt()
        agreed = agreed + 1 if _prompt_read(prompt) == _prompt_read(previous) else 1
        if agreed >= CUT_SETTLE_READS:
            self._apply_cut(sink, seq, prompt)
            self._end_settling()
            return GLib.SOURCE_REMOVE
        if attempt >= CUT_SETTLE_TRIES:
            self._end_settling()  # never still: the box keeps its text
            return GLib.SOURCE_REMOVE
        self.scheduler.timeout_add(
            CUT_SETTLE_MS, self._settle_cut, sink, seq, prompt, agreed, attempt + 1
        )
        return GLib.SOURCE_REMOVE

    def _end_settling(self) -> None:
        """The cut has decided; send whatever was waiting on it.

        The waiting send is re-taken from the composer rather than replayed
        from the text it carried, because a cut that landed has just seeded
        that box: what goes out is the CLI's text and the draft written
        under it, in the order they were written, which is what the send
        would have carried had it come a moment later.

        A model or effort switch held the same way goes first — it was asked
        of the session the prompt is about to be sent to — unless a send is
        waiting too, in which case the switch yields the box and re-posts
        itself once the send has typed and submitted (a beat past the
        send's slowest path), through the ordinary "no composer over the
        box" road."""
        self.cut_settling = False
        held = self.switch_after_settle
        self.switch_after_settle = None
        if held is not None and not self.send_after_settle:
            self._post_switch(*held)
        elif held is not None:
            self.scheduler.timeout_add(
                CUT_VERIFY_MS[0] + 2 * PROMPT_SUBMIT_MS, self._switch_after_send, held
            )
        if not self.send_after_settle:
            return
        self.send_after_settle = False
        self.host.resend_composed()

    def _switch_after_send(self, held: tuple[str, str]) -> bool:
        self._post_switch(*held)
        return GLib.SOURCE_REMOVE

    def _apply_cut(self, sink: CutSink, seq: int, prompt: EnteredPrompt | None) -> None:
        """Erase the settled read from the box, seed it into the composer,
        and start checking that the box really emptied.

        What is seeded is the read with any stand-in of ours expanded back
        into the draft it folded (see `restore_draft`); the erase still
        works from the read as drawn, which is what the verify rounds
        compare against — a stand-in goes on the first backspace that
        reaches it, and the ones budgeted for its characters land on an
        empty box. A stand-in that isn't ours lowers the composer instead
        (`CutSink.refuse`): the box holds a paste no read can recover, and
        an empty box is the one thing a cut must never make of it."""
        if prompt is None or not prompt.text.strip():
            self.pasted_back = {}  # nothing folded is left on screen
            return
        text = self._expand_box_read(prompt.text)
        if text is None:
            sink.refuse()
            return
        keys = self.provider.clear_prompt_keys(prompt)
        if not keys:
            return
        self.write_text(keys)
        self.cut_pending = prompt.text
        self.pasted_back = {}  # spent: the stand-ins are being erased
        sink.seed(text)
        self.scheduler.timeout_add(CUT_VERIFY_MS[0], self._verify_cut, sink, seq, 0)

    def _verify_cut(self, sink: CutSink, seq: int, index: int) -> bool:
        """Re-read the box after an erase and finish the job if it fell
        short (see begin_cut for how it can).

        Only a leftover the cut can account for is touched: the erase runs
        backwards from the end, so whatever it failed to reach is a prefix
        of what was read. Anything else on that line got there some other
        way — the user typing into the terminal, the agent redrawing — and
        is left alone, which also ends the checking.
        """
        if not self._cut_alive(sink, seq) or self.cut_pending is None:
            return GLib.SOURCE_REMOVE
        keys = self._leftover_cut_keys(self.cut_pending)
        if keys is None:
            self.cut_pending = None  # emptied, or not ours to erase
            return GLib.SOURCE_REMOVE
        self.write_text(keys)
        if index + 1 < len(CUT_VERIFY_MS):
            self.scheduler.timeout_add(
                CUT_VERIFY_MS[index + 1], self._verify_cut, sink, seq, index + 1
            )
        return GLib.SOURCE_REMOVE

    def _leftover_cut_keys(self, cut: str) -> str | None:
        """Keystrokes erasing what a cut of *cut* left in the input box, or
        None when the box is empty or holds something that cut can't
        account for (see _verify_cut).

        An erase still queued reads as the whole prompt, which is a prefix
        of itself: the answer is another full line of backspaces, and the
        two lines of them meet an emptied box between them — where the
        spare ones are no-ops."""
        left = self.entered_prompt()
        if left is None or not left.text or not cut.startswith(left.text):
            return None
        return self.provider.clear_prompt_keys(left)

    def _cut_alive(self, sink: CutSink, seq: int) -> bool:
        """Whether cut *seq* still has a composer to cut into and an agent
        to cut from.

        A composer closed mid-chain has already typed its text back into
        the box (a close), and a send has just typed a prompt into it — no
        later round of a chain may erase *those*, and bumping `cut_seq` is
        how each of them says so."""
        return seq == self.cut_seq and sink.alive() and self.agent_is_running()

    # -- the close's paste-back ----------------------------------------------

    def restore_draft(self, text: str) -> bool:
        """A closing composer's draft goes back into the CLI's input box —
        or, when there is no agent there to take it, nowhere: False, and
        the tab stashes it.

        Into the box it goes as pieces, each a bracketed paste small enough
        that the CLI shows it in full (`composerkeys.paste_pieces`) rather
        than folding it into a "[Pasted text #N +M lines]" stand-in, which
        the next open's cut would take at face value — the draft behind it
        unreadable and, once the stand-in was erased, gone. Should a piece
        be folded anyway (a CLI with other limits), a read of the box a beat
        later writes down which stand-in holds which piece, and the next
        open puts the piece back in the composer in the stand-in's place
        (see `_verify_paste_back`, `_apply_cut`).
        """
        if not self.agent_is_running():
            return False
        restored = composerkeys.restore_text(text)
        if restored:
            pieces = composerkeys.paste_pieces(restored)
            self.write_text("".join(bracketed_paste(piece) for piece in pieces))
            self.pasted_back = {}
            self.paste_back_pending = pieces
            self.paste_back_agent = self.agent_pid()
            self.scheduler.timeout_add(CUT_VERIFY_MS[0], self._verify_paste_back, pieces, 0)
        return True

    def _verify_paste_back(self, pieces: list[str], index: int) -> bool:
        """Read how a close's paste-back landed, on the cut's own verify
        schedule: the first read that finds the box holding the pieces
        settles it, and a beat that finds no box (the agent mid-redraw) or
        a box that doesn't align yet (a repaint caught halfway) leaves them
        pending for the next. Pieces still unsettled after the last beat —
        an open's cut has emptied the box already, say — are given up on:
        nothing is recorded, and a stand-in seen later reads as somebody
        else's."""
        if self.paste_back_pending is not pieces:
            return GLib.SOURCE_REMOVE  # a later close, or an open got there first
        prompt = self.entered_prompt()
        if prompt is not None:
            self._settle_paste_back(prompt.text)
        if self.paste_back_pending is pieces:
            if index + 1 < len(CUT_VERIFY_MS):
                self.scheduler.timeout_add(
                    CUT_VERIFY_MS[index + 1], self._verify_paste_back, pieces, index + 1
                )
            else:
                self.paste_back_pending = None
        return GLib.SOURCE_REMOVE

    def _settle_paste_back(self, screen: str) -> None:
        """Align the pending pieces with *screen* and, if they fit, record
        the stand-ins among them. Pieces that don't fit stay pending — the
        read may have caught the box mid-draw — for the next read to try."""
        pieces = self.paste_back_pending
        if pieces is None:
            return
        record = composerkeys.pasted_back(screen, pieces)
        if record is None:
            return
        self.pasted_back = record
        self.paste_back_pending = None

    def _expand_box_read(self, screen: str) -> str | None:
        """*screen* (an `entered_prompt` read) with the stand-ins the last
        paste-back left replaced by the text they hold, or None when it
        holds a stand-in that isn't ours — what an open must not cut. A
        record from another CLI process doesn't count: stand-in numbers
        start over with each one."""
        if self.paste_back_pending is not None:
            self._settle_paste_back(screen)
        record = self.pasted_back
        if record and self.paste_back_agent != self.agent_pid():
            record = {}  # the /proc walk is only paid while there is a record to scope
        return composerkeys.expand_pasted_back(screen, record)

    def foreign_paste_in_box(self) -> bool:
        """Whether the CLI's box holds a paste Collins can't read (see
        `_expand_box_read`) — asked before a composer is raised over it.
        Pieces still unaligned after this glance are given the benefit of
        the doubt: the cut's settled read is the one that decides."""
        prompt = self.entered_prompt()
        if prompt is None:
            return False
        expanded = self._expand_box_read(prompt.text)
        if expanded is None and self.paste_back_pending is not None:
            return False
        return expanded is None

    # -- where the agent is, and what runs below it --------------------------

    def current_agent_cwd(self) -> str | None:
        """Best-effort cwd of what's running in the agent terminal: the
        foreground process if any (the agent may have cd'd into a worktree),
        else the shell, else the directory the session started in.

        Each candidate's agent descendants are searched before falling back
        to the candidate itself; see `candidate_pids`.
        """
        cli = getattr(self.provider, "cli", "") or ""
        for pid in self.candidate_pids():
            cwd = proctree.agent_descendant_cwd(pid, cli)
            if cwd is not None:
                return cwd
            cwd = proctree.process_cwd(pid)
            if cwd is not None:
                return cwd
        return self.cwd

    def owns_pid_ancestors(self, ancestors: set[int]) -> bool:
        """Whether one of *ancestors* is a process this session's terminal runs.

        *ancestors* is a pid plus its whole parent chain (proctree.
        ancestor_pids) — how a session MCP tool call is traced back to the
        tab whose shell spawned its `claude`: the shim that sent it is a
        child of that CLI, so the tab's own processes sit in its ancestry.
        Both candidate ends are tested (see `candidate_pids`); a daemon-
        hosted process descends from systemd instead, matches no tab
        anywhere, and gets the dispatcher's clean identity error.
        """
        return any(pid in ancestors for pid in self.candidate_pids())

    def has_background_descendant(self, ignore: Collection[str] = frozenset()) -> bool:
        """Whether the agent has something still running below it right now —
        a tool call in flight, or a background job (a dev server, a long
        build) it started and left running. An extra "still working" signal
        for a session whose terminal has otherwise gone quiet; see
        `ActivityTracker` in activity.py.

        *ignore* is the session's plumbing baseline — cmdlines of the MCP
        servers the CLI keeps alive for its whole life, which are children of
        the agent but never work (see proctree.has_live_descendant)."""
        cli = getattr(self.provider, "cli", "") or ""
        return any(proctree.has_live_descendant(pid, cli, ignore) for pid in self.candidate_pids())

    def background_descendant_cmdlines(self) -> set[str]:
        """The cmdlines of everything running directly below this session's
        agent right now. Sampled while nothing has ever been submitted to a
        freshly spawned tab, this is the agent's own plumbing — the baseline
        `has_background_descendant` is later told to ignore."""
        cli = getattr(self.provider, "cli", "") or ""
        cmdlines: set[str] = set()
        for pid in self.candidate_pids():
            cmdlines |= proctree.descendant_cmdlines(pid, cli)
        return cmdlines

    def current_permission_mode(self) -> str:
        """Best-effort permission mode of the agent right now: the last mode
        its transcript recorded (the CLI stamps every user turn, and every
        shift+tab change, with one), else the mode the session was launched
        with, else "" — the CLI's default. What start_session inherits into
        a spawned sibling."""
        mode = self.transcript.permission_mode()
        if mode:
            return mode
        return self.options.permission_mode if self.options else ""

    def current_model(self) -> str:
        """Best-effort model of the agent right now: the one its transcript
        recorded on the last reply (a full id; ``/model`` and fast-mode
        switches included), else the --model the session was launched with,
        else "" — the CLI's configured default. What start_session inherits
        into a spawned sibling."""
        model = self.transcript.model()
        if model:
            return model
        return self.options.model if self.options else ""

    def current_effort(self) -> str:
        """Best-effort effort level of the agent right now: the one its
        transcript stamped on the last reply (``/effort`` switches
        included), else the --effort the session was launched with, else ""
        — the CLI's configured default. What start_session inherits into a
        spawned sibling."""
        effort = self.transcript.effort()
        if effort:
            return effort
        return self.options.effort if self.options else ""

    # -- activity ---------------------------------------------------------------

    def redraw_counts(self, startup_held: bool) -> bool:
        """Whether a redraw of the terminal reads as the agent working — the
        verdict the window marks the session busy on (MainWindow.
        _on_terminal_output). *startup_held* is the window's word that a
        fresh spawn's ungated sources are still held (MainWindow.
        _startup_held).

        It counts unless the terminal is merely answering the app — see
        EchoGate. Second opinion, gate or no gate: motion in the screen's
        first column is the agent's own spinner (or its output scrolling
        through), however the redraw showing it was caused — see
        SpinnerWatch. Sampled even when the gate already said yes, so the
        watch always has a fresh baseline to compare the next redraw against
        — and even while the startup hold discounts the verdict: a spawning
        CLI's welcome paint animates too, and it is no turn. Unless the
        agent itself just called the turn over: these redraws are its
        trailing repaints (the prompt box returning, the indicator fading),
        and starting a pole on them would blip the instant-down the termprop
        finish just delivered. See ProgressWatch.quiet.
        """
        agent_output = self.echo_gate.counts((self.screen.columns(), self.screen.row_count()))
        if self.spinner.due() and (reading := self.screen_first_column()) is not None:
            spinning = self.spinner.sample(*reading) and not startup_held
            agent_output = spinning or agent_output
        if self.progress is not None and self.progress.quiet():
            agent_output = False
        return agent_output

    # -- the transcript ------------------------------------------------------

    def set_transcript_path(self, jsonl_path: str | Path | None) -> None:
        """Tail a transcript for what the session reads out of it (touched
        files, pull requests, model, effort, attachments). Used on resume,
        and again once a brand-new session's file appears on disk."""
        self.transcript.set_path(jsonl_path)
        # Another session's PRs (and another session's model); re-read from the
        # new transcript below, and the PRs restored again by the window once
        # this session is known.
        self.tracked_prs = {}
        self.restored_prs = []
        self.host.transcript_reset()
        self._watch_transcript(jsonl_path)

    @property
    def transcript_path(self) -> str | None:
        """The transcript this session is tailing, or None."""
        path = self.transcript.path
        return str(path) if path else None

    def finish_witness(self) -> tuple[tuple[int, int], int | None]:
        """What the transcript says right now, for the finish ledger: its
        stamp (turn ends and replies parsed so far) and the file's size on
        disk (None without a file) — the second witness the ledger's final
        verdict weighs. A `stat` on the main thread: cheap, and read at the
        edge itself rather than off the last landing, so growth the parser
        hasn't seen yet still shows."""
        size = None
        path = self.transcript.path
        if path is not None:
            try:
                size = path.stat().st_size
            except OSError:
                size = None
        return self.transcript.stamp, size

    def relocate_transcript(self, jsonl_path: str | Path) -> None:
        """Follow this session's transcript to a new path.

        Entering a worktree makes the CLI re-key the session's transcript
        under a project directory named for the new working directory, which
        moves the file out from under the monitor watching it. Nothing about
        the session changed, so unlike `set_transcript_path` this keeps the
        chips and everything already parsed — it only re-aims the tail and the
        monitor at where the file lives now.
        """
        self.transcript.relocate(jsonl_path)
        self._watch_transcript(jsonl_path)

    def _watch_transcript(self, jsonl_path: str | Path | None) -> None:
        """Point the file monitor at *jsonl_path* and kick off a read."""
        if self._transcript_monitor is not None:
            self._transcript_monitor.cancel()
            self._transcript_monitor = None
        if jsonl_path:
            self._transcript_monitor = self.scheduler.monitor_file(
                str(jsonl_path), self._on_transcript_event
            )
            self._ensure_poll()
            self.request_update()

    def _ensure_poll(self) -> None:
        if self._poll_source is None:
            self._poll_source = self.scheduler.timeout_add(PROMPT_POLL_MS, self._poll)

    def _poll(self) -> bool:
        if not self.host.alive():  # tab closed/detached → stop ticking
            self._poll_source = None
            return GLib.SOURCE_REMOVE
        self.request_update()
        return GLib.SOURCE_CONTINUE

    def _on_transcript_event(self) -> None:
        if self._transcript_refresh_source is not None:
            return
        self._transcript_refresh_source = self.scheduler.timeout_add(
            TRANSCRIPT_DEBOUNCE_MS, self._debounced_update
        )

    def _debounced_update(self) -> bool:
        self._transcript_refresh_source = None
        self.request_update()
        return GLib.SOURCE_REMOVE

    def request_update(self, discover: bool = False) -> None:
        """Parse newly-appended transcript bytes off the main thread (big
        tool-result lines would otherwise freeze the UI), then land the
        result on the main loop (`_apply_update`).

        `discover` asks the branch which PR it has. It is only ever set by the
        footer's refresh button; a request that arrives while one is running is
        carried to the next poll rather than dropped, so the click always gets
        its lookup.
        """
        if self._updating:
            self._pr_discover = self._pr_discover or discover
            return
        self._updating = True
        looking = discover or self._pr_discover
        self._pr_discover = False

        def work() -> None:
            try:
                self.transcript.update()
            except Exception:
                pass
            found = self._look_up_branch_pr() if looking else None
            try:
                tracked = self._collect_prs(found)
                # reads the gh status cache, so it belongs on this thread too;
                # a session with no linked PR touches no files at all
                prs = [self._enriched(pr) for pr in tracked[-MAX_PR_CHIPS:]]
            except Exception:
                tracked, prs = None, self.host.shown_prs()  # leave the chips as they are
            # PRIORITY_DEFAULT, not the idle default: this landing is what
            # resets _updating, and a default-idle callback can be starved
            # indefinitely by a busy frame clock (GTK's layout/paint phases
            # outrank it) — under CI's Xvfb it never ran at all, wedging the
            # gate and dropping every later update. A timeout-priority landing
            # cannot be starved by redraw.
            self.scheduler.idle_add(
                self._apply_update,
                prs,
                looking and found is None,
                tracked,
                priority=GLib.PRIORITY_DEFAULT,
            )

        self.scheduler.background(work)

    def _collect_prs(self, found: PullRequest | None) -> list[PullRequest]:
        """Every PR this session knows about, oldest first. On the update thread.

        Four sources, in the order a PR can first be known from them: the list
        restored from a previous run, the transcript's pr-links, the PRs the
        session attached itself (the attach_pr tool), and whatever the refresh
        button just found on the branch. A URL is only ever added — a PR the
        session opened stays on the row once the branch has moved on, which is
        the whole point of showing all of them.

        Uncapped, and it must stay that way even though the row isn't: cap the
        list here and the PRs trimmed off the front would come back from the
        transcript on the next poll — as the *newest* entries — and the row
        would spin.
        """
        try:
            links = self.transcript.pull_requests()
        except Exception:
            links = []
        collected = merge_ordered(self.tracked_prs.values(), links)
        for attached in self.attached_prs.values():
            if all(pr.url != attached.url for pr in collected):
                collected.append(attached)
        if found is not None and all(pr.url != found.url for pr in collected):
            collected.append(found)  # a PR nothing else knows about: it is the newest
        return collected

    def _enriched(self, pr: PullRequest) -> PullRequest:
        """*pr* with its title and CI status, fetching them when due.

        A merged PR that already has a title is left alone: it has no checks
        left to run and shows no badge anyway, so an old chip on a long-lived
        session never costs another `gh` call. One with no title still asks
        once — the PR menu has a line to fill, and a list saved before
        Collins knew about titles has nothing in it.
        """
        return pr if pr.merged and pr.title else (enrich(pr) or pr)

    def _look_up_branch_pr(self) -> PullRequest | None:
        """The refresh button's own path to a PR: whatever branch is checked out
        right now, then gh. Runs on the update thread.

        cwd and branch are re-read here rather than taken from the footer's 2s
        poll, so a click straight after a checkout asks about the branch the user
        is actually on instead of the one the last tick happened to see.

        Every chip already on the row is marked due first, so one click
        refreshes the lot — status is the other half of what the button is for,
        and a branch that turns up nothing still leaves the row up to date.
        """
        for pr in self.host.shown_prs():
            if not pr.merged:
                invalidate(pr.url)
        cwd = self.current_agent_cwd()
        try:
            return discover_pr(cwd, current_branch(cwd))
        except Exception:
            return None

    def _apply_update(
        self,
        prs: list[PullRequest] | None = None,
        lookup_empty: bool = False,
        tracked: list[PullRequest] | None = None,
    ) -> bool:
        """Land an update's results on the main loop.

        *prs* is what the row shows (the newest MAX_PR_CHIPS, with status);
        *tracked* is everything the session knows about, which is what the
        next collection starts from — None when the update failed and the
        row is being left alone.
        """
        self._updating = False
        if not self.finish_ledger.armed and self.transcript.loaded:
            # The first full read: where finishes are measured from. A tab
            # whose file never appears (a CLI with transcript saving off, a
            # fresh spawn before its resolver binds) stays unarmed, and its
            # edges pass as they always have.
            self.finish_ledger.arm(*self.finish_witness())
        if tracked is not None:
            # The shown ones come back with status, and they keep it: it is
            # what the chips fall back to when a poll brings nothing new (a
            # failed fetch, or no fetch at all), and what gets saved for the
            # next run. A fetch that does land replaces it wholesale.
            shown = {pr.url: pr for pr in prs or []}
            self.tracked_prs = {pr.url: shown.get(pr.url, pr) for pr in tracked}
            self._merge_restored()
        self.host.transcript_landed(prs or [], lookup_empty)
        return GLib.SOURCE_REMOVE

    def restore_prs(self, records: object) -> None:
        """Re-adopt the PRs saved for this session.

        The window calls this (through the tab) once the session is known,
        and the hub's session-changed calls it again for every list somebody
        else writes while the tab is open. The transcript's own pr-links come
        back on the next poll anyway, but a PR a branch lookup found is
        written down nowhere else, and a PR that was already merged shows its
        mark before any `gh` call goes out.
        """
        restored = from_records(records)
        if not restored:
            return
        self.restored_prs = restored
        self._merge_restored()
        self.request_update()

    def attach_pr(self, pr: PullRequest) -> bool:
        """Adopt a PR named from outside the transcript — the attach_pr
        session tool. False when the session already tracks it.

        Kept in a dict of its own rather than written into tracked_prs: an
        update already in flight when the call lands replaces that wholesale,
        so a direct write could be lost. _collect_prs folds these in on every
        pass instead, and the update requested here gets the new chip its
        title and status.
        """
        if pr.url in self.tracked_prs or pr.url in self.attached_prs:
            return False
        self.attached_prs = {**self.attached_prs, pr.url: pr}
        self.request_update()
        return True

    def _merge_restored(self) -> None:
        """Put this session's restored PRs back at the head of the tracked list.

        Replayed after every update lands, not just once: an update that was
        already in flight when the window restored (opening a tab starts one
        immediately) would otherwise finish and overwrite the restore with the
        list it had snapshotted before it. The saved order decides where a PR
        the transcript never mentions belongs; the live copy of one it does
        mention wins on everything except its place in the row.
        """
        if not self.restored_prs:
            return
        live = list(self.tracked_prs.values())
        merged = {pr.url: pr for pr in merge_ordered(self.restored_prs, live)}
        merged.update({pr.url: pr for pr in live})  # positions keep, values don't
        self.tracked_prs = merged

    # -- the transcript resolver ------------------------------------------------

    def start_resolver(self, cwd: str) -> None:
        """Find the transcript of the session this terminal is about to
        start in *cwd*. The transcript only appears once the first prompt is
        sent, which can be arbitrarily long after the tab opens: poll for as
        long as the tab is in the foreground; in the background allow ~3 min
        before pausing, and resume whenever the tab is brought back
        (`arm_resolver`, which the tab calls on every map)."""
        self.resolver_cwd = cwd
        self.arm_resolver()

    def arm_resolver(self) -> None:
        if self.resolver_cwd is None or (self.session_id is not None and not self.fork_resolve):
            return  # never started for this session, or already resolved
        self._resolver_attempts = 0
        if self._resolver_source is not None:
            return  # already polling; just refresh the background budget
        # A brand-new session must attach to a transcript that appeared while
        # polling: the newest one *existing* at (re)start belongs to some other
        # session — a submitted prompt creates the file well within the ~3 min
        # background budget, so anything from a pause can't be ours either.
        # `--continue` (command_override) reuses the newest existing
        # transcript, which is exactly the session it resumes.
        self._known_transcripts = (
            set(self.provider.transcripts_for_cwd(self.resolver_cwd))
            if self.command_override is None
            else set()
        )
        self._baselined_dirs = {self.resolver_cwd}
        # Stamp the instant polling *first* starts, before any prompt has
        # created a transcript. A worktree we later follow into may hold
        # transcripts from an older, recycled session, but those predate this
        # moment; a transcript stamped after it is our own (see
        # _resolve_transcript). Anchor it to the first arm only: a backgrounded
        # tab that pauses unresolved (~3 min) and resumes on re-map re-runs
        # this and re-baselines its worktree — pushing arm time forward here
        # would let that re-baseline exclude our own transcript if the agent
        # had since gone quiet (mtime now behind a later arm time), the very
        # failure this gate exists to prevent.
        if not self._resolver_armed_at:
            self._resolver_armed_at = self.scheduler.time()
        self._resolver_source = self.scheduler.timeout_add(
            RESOLVER_POLL_MS, self._resolve_transcript
        )

    def _predates_resolver(self, path: Path) -> bool:
        """Whether `path` was last written before this resolver armed — i.e.
        belongs to an older session, not one this tab is waiting on. A file we
        can't stat is treated as *not* predating, so a transient error never
        baselines out (and thus loses) a transcript that might be ours."""
        try:
            return path.stat().st_mtime < self._resolver_armed_at
        except OSError:
            return False

    def _resolve_transcript(self) -> bool:
        if not self.host.alive():
            self._resolver_source = None
            return GLib.SOURCE_REMOVE
        cands = [
            p
            for p in self.provider.transcripts_for_cwd(self.resolver_cwd)
            if p not in self._known_transcripts
        ]
        # A worktree launch (claude -w) moves the agent into a worktree under
        # the launch dir before the first prompt, and its transcript is keyed
        # by the *worktree's* cwd — the launch dir's key never sees it. Follow
        # the agent into any worktree of this tab's own project, with the same
        # baseline discipline as the launch dir: the CLI recycles unchanged
        # worktrees, so a transcript from an older, recycled session may sit
        # in a worktree we follow into, and we must not attach to that.
        #
        # But baseline out only transcripts that predate this resolver: a fast
        # `claude -w` writes its first transcript line within ~1s of creating
        # the worktree, tighter than our 1.5s poll, so the tick that first
        # sees the moved cwd can *also* see our own just-born transcript
        # already present. Excluding everything present at that moment (the
        # old behavior) would swallow it and the tab would never bind. An
        # older session's transcript predates _resolver_armed_at; our own is
        # stamped after it.
        #
        # worktree_shares_project matches on the *project root*, not the launch
        # dir: when this tab was itself launched from inside a worktree (a
        # background session spawned by an agent already in one), the new
        # worktree is rooted at the main repo, so live's root is that repo
        # while the launch dir is the caller's worktree — both collapse to the
        # same root.
        live = self.current_agent_cwd()
        if live and live != self.resolver_cwd and worktree_shares_project(
            live, self.resolver_cwd
        ):
            if live not in self._baselined_dirs:
                self._baselined_dirs.add(live)
                if self.command_override is None:
                    self._known_transcripts |= {
                        p
                        for p in self.provider.transcripts_for_cwd(live)
                        if self._predates_resolver(p)
                    }
            cands += [
                p
                for p in self.provider.transcripts_for_cwd(live)
                if p not in self._known_transcripts
            ]
        try:
            path = max(cands, key=lambda p: p.stat().st_mtime, default=None)
        except OSError:
            path = None
        if path is not None:
            if self.fork_resolve:
                # A sandboxed fork: the new conversation's id, reported and
                # nothing more — the tab stays bound to the original.
                self.fork_resolve = False
                forked = self.provider.session_id_for_transcript(path)
                if forked and forked != self.session_id:
                    self.host.fork_resolved(forked)
                self._resolver_source = None
                return GLib.SOURCE_REMOVE
            self.set_transcript_path(str(path))
            if self.session_id is None:
                self.session_id = self.provider.session_id_for_transcript(path)
                self.host.session_resolved(self.session_id)
            self._resolver_source = None
            return GLib.SOURCE_REMOVE
        if self.host.mapped():
            self._resolver_attempts = 0  # foreground tab: keep polling indefinitely
        else:
            self._resolver_attempts += 1
            if self._resolver_attempts > RESOLVER_BACKGROUND_TICKS:
                self._resolver_source = None  # pause until the next map
                return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def unstarted_thread(self) -> bool:
        """Whether this is still a New Thread with nothing in it: a
        brand-new session — not resumed, forked or continued, and its first
        prompt never sent, so no transcript has appeared to resolve a
        session id — sitting at an empty input box right now. Closing one
        loses nothing. Anything typed into the box (takes_prompt says no)
        makes it a thread worth asking about."""
        return (
            self.resolver_cwd is not None
            and self.session_id is None
            and self.command_override is None
            and self.takes_prompt()
        )

    # -- the cwd poll ----------------------------------------------------------

    def start_cwd_poll(self) -> None:
        """Read the agent's cwd now, and every CWD_POLL_MS for as long as
        the tab stays mapped (`SessionHost.cwd_polled` gets each reading).
        The tab calls this on every map: a tab switch refreshes at once."""
        self.host.cwd_polled(self.current_agent_cwd())
        if self._cwd_source is None:
            self._cwd_source = self.scheduler.timeout_add(CWD_POLL_MS, self._cwd_tick)

    def _cwd_tick(self) -> bool:
        if not self.host.mapped():  # hidden/closed tab → resume on next map
            self._cwd_source = None
            return GLib.SOURCE_REMOVE
        self.host.cwd_polled(self.current_agent_cwd())
        return GLib.SOURCE_CONTINUE

    def settle_cwd(self, cwd: str | None, root: str) -> editorfiles.FollowScope | None:
        """Whether the agent has *moved* to *cwd*, as the editor rooted at
        *root* should see it: the move's scope once it has settled, None
        while it hasn't (or when it is no move at all).

        Rides the cwd poll rather than adding one of its own — the value is
        already in hand — but acts on far less of it: the agent's cwd is read
        from a live process tree, and it flaps. A worktree launch moves it
        before the first prompt; a restarted background job forks a fresh
        process at the old directory; between the CLI exiting and the shell
        being read the fallback answer is the directory the tab started in.
        So a new directory has to hold still across consecutive polls before
        it counts as a move, and each settled answer is given exactly once —
        an offer the user ignored must not come back every two seconds.

        Where the agent went decides the scope: still inside the same
        project (a worktree, most often) and the editor simply follows;
        anywhere else and it only offers. See `editorfiles.follow_scope`."""
        if cwd != self._follow_pending:
            self._follow_pending = cwd
            self._follow_ticks = 1
            return None
        self._follow_ticks += 1
        if self._follow_ticks < EDITOR_FOLLOW_TICKS or cwd == self._follow_settled:
            return None
        scope = editorfiles.follow_scope(root, cwd)
        if scope is editorfiles.FollowScope.NONE:
            # Back where it already was — including the fallback the tab
            # started at, which is how leaving a worktree usually reads.
            self._follow_settled = None
            return None
        self._follow_settled = cwd
        return scope
