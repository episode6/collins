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
  terminal.

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
from collections.abc import Callable
from typing import Any, Protocol

from gi.repository import GLib

from .. import composerkeys, proctree
from ..i18n import _
from ..providers import EnteredPrompt, Provider
from .ports import PtyPort, ScreenPort

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


class GLibScheduler:
    """The real `Scheduler`: GLib's main loop and a daemon thread."""

    def timeout_add(self, ms: int, fn: Callable[..., bool], *args: Any) -> int:
        return GLib.timeout_add(ms, fn, *args)

    def idle_add(
        self, fn: Callable[..., bool], *args: Any, priority: int = GLib.PRIORITY_DEFAULT_IDLE
    ) -> int:
        return GLib.idle_add(fn, *args, priority=priority)

    def background(self, fn: Callable[..., None], *args: Any) -> None:
        threading.Thread(target=fn, args=args, daemon=True).start()


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
        scheduler: Scheduler | None = None,
    ) -> None:
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
