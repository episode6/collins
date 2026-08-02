# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""Which sessions are working *right now*, so the sidebar can say so.

A session's status says where it is running — in a tab, or detached — but not
whether the agent is doing anything. That is what the barber pole on a row's
guide line answers, and it needs a signal that says "output is flowing" and,
harder, "output stopped".

One source is the agent's own word — Claude Code announces its busy state
through the terminal as ConEmu-style OSC 9;4 progress sequences, which VTE
parses into a *termprop* on the tab's terminal (see `ProgressWatch`). Every
other source is inferred, and stays wired as the fallback for a CLI (or a
configuration) that doesn't speak progress:

- a **tab**'s terminal emits ``contents-changed`` on every redraw, which the
  window feeds to `ActivityTracker.mark`. It is noisy by design — a spinner
  frame counts — but silence is what matters, and a prompt sitting idle is
  silent. `EchoGate` throws out the redraws the *app* caused, which are
  otherwise indistinguishable from a working agent.
- an open tab whose terminal has gone quiet may still have a **background
  process** running below the agent — a dev server, a long build — that
  produces no terminal output of its own. The window polls each open tab's
  process tree (`proctree.has_live_descendant`) and marks the session for as
  long as one is found.
- a tab's screen also shows the agent's own **thinking spinner** while it
  works, and `SpinnerWatch` reads it — not by matching its glyphs, but by
  noticing first-column motion between samples of the visible screen. It is
  the one tab source that needs no gate: it can start a pole `EchoGate`
  would have to wave through first, such as a freshly attached background
  agent mid-turn that nothing was ever typed at.

All of them funnel into one tracker: "busy" means output seen within the
source's idle window — `IDLE_S` for a terminal, which redraws continuously
while its agent works, and `PROCESS_IDLE_S` for a process-tree sighting.
Detached (`/bg`) sessions have no source at all: their only signal would be
transcript growth, and the pole that once rode it is gone — a detached row
shows its still yellow guide line whatever the agent is doing.

Nothing here touches GTK — the timer is injected — so the whole thing is
testable without a display.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable

# How long output has to stop before a session reads as idle. Long enough to
# ride out the pauses inside a turn — an agent thinking between tool calls
# prints nothing for a beat — and short enough that a finished turn stops the
# pole while the user is still looking at the row. This is the window for a
# tab's terminal, which repaints continuously (spinner frames count) while
# its agent works.
IDLE_S = 2.0

# How often the sweep looks for sessions that went quiet. Only the moment a
# session stops being busy is this coarse; starting is immediate, on the first
# byte of output.
SWEEP_MS = 250

# How often an open tab's process tree is checked for something the agent left
# running below it — a background job (a dev server, a long build) that keeps
# no output flowing to the terminal, and so would otherwise read as idle the
# moment its last line scrolled by. Coarser than the sweep because it costs a
# handful of /proc reads per open tab.
PROCESS_POLL_MS = 2000

# The window a live descendant keeps a session's pole up for. Wider than
# PROCESS_POLL_MS so ordinary scheduling jitter never closes the gap between
# two sightings of the same still-running process.
PROCESS_IDLE_S = 5.0

# How often a tab's screen is read for spinner motion, at most. Sampling hangs
# off ``contents-changed``, so an idle terminal is never read at all; while the
# agent works its spinner repaints roughly every 100ms, and this decides which
# of those repaints get compared. Deliberately not a clean multiple of that
# frame clock: sampling exactly one glyph-cycle apart would see the same frame
# every time and read a live spinner as still.
SPINNER_SAMPLE_S = 0.35

# Two first-column changes at most this far apart read as animation; farther
# apart they are unrelated one-off repaints — a prompt redrawn now, a scroll a
# minute later — and starting a pole on those would undo what EchoGate is for.
# Wide enough (four samples) to ride out a coincidence sample: two frames of a
# live spinner that happened to show the same glyph.
SPINNER_STREAK_S = 4 * SPINNER_SAMPLE_S

# The idle window for a progress-termprop mark. The termprop is edge-triggered
# — one busy hint as a turn starts, one clear as it ends — not a heartbeat, so
# the window has to be able to ride out a long turn on its own. It is a safety
# net rather than the signal: the clear is what normally ends the pole, and
# this only catches a CLI killed too abruptly to send one (SIGKILL emits no
# final OSC). In practice redraw marks keep arriving alongside and refresh the
# deadline far more often than this anyway.
PROGRESS_IDLE_S = 60.0

# How long after a termprop finish a redraw may not *start* a new pole. The
# CLI announces a turn's end before its screen goes still — the prompt box
# repaints, the working indicator fades out — and those trailing redraws pass
# an armed EchoGate, which would blip the pole back up for IDLE_S right after
# the honest instant-down. A genuinely new turn is never held back: the Enter
# pre-mark and the next busy hint both bypass this window.
PROGRESS_QUIET_S = IDLE_S

# How long a redraw goes on counting as the answer to something the app sent.
# Measured on a live agent TUI, its reply lands 10-30ms after the bytes leave
# the terminal; a quarter second is slack for a loaded machine. Nothing real is
# lost by being generous: a turn that starts on the keystroke keeps producing
# output long after the window closes.
ECHO_S = 0.25


class ActivityTracker:
    """The set of session ids whose agent is producing output right now.

    `mark` is cheap and expected to be called at redraw frequency: it only
    stamps a clock. The transition *out* of busy is found by a sweep that runs
    only while something is busy, so an idle app schedules nothing at all.

    *on_change* is called with (session_id, busy) on each transition, never for
    a repeat mark.

    *on_finished* is called (after on_change) only when a session's idle window
    runs out — the agent's output genuinely stopped coming. An explicit clear()
    is teardown, not a finish: its tab closed or its detach ended, and neither
    should read as "a run just completed".
    """

    def __init__(
        self,
        on_change: Callable[[str, bool], None],
        *,
        on_finished: Callable[[str], None] | None = None,
        idle_s: float = IDLE_S,
        sweep_ms: int = SWEEP_MS,
        clock: Callable[[], float] = time.monotonic,
        add_timeout: Callable[[int, Callable[[], bool]], int] | None = None,
        remove_timeout: Callable[[int], None] | None = None,
    ) -> None:
        self._on_change = on_change
        self._on_finished = on_finished
        self._idle_s = idle_s
        self._sweep_ms = sweep_ms
        self._clock = clock
        self._add_timeout = add_timeout or _glib_add_timeout
        self._remove_timeout = remove_timeout or _glib_remove_timeout
        self._deadlines: dict[str, float] = {}  # busy session id -> when it reads idle
        self._sweep: int | None = None

    def mark(self, session_id: str, *, idle_s: float | None = None) -> None:
        """Record that *session_id* just produced output.

        *idle_s* is how long this mark keeps the session busy, defaulting to
        the tracker's window. A sparse source — a transcript that grows only
        when a turn lands — passes a wider one than a terminal that redraws
        continuously. The latest mark decides: a session whose signal changes
        (its tab reopened, say) is on the new window from its next mark.
        """
        if not session_id:
            return
        fresh = session_id not in self._deadlines
        window = self._idle_s if idle_s is None else idle_s
        self._deadlines[session_id] = self._clock() + window
        if fresh:
            self._start_sweep()
            self._on_change(session_id, True)

    def clear(self, session_id: str) -> None:
        """Drop *session_id* now, without waiting out the idle window — its tab
        closed, or it stopped running detached, so there is nothing left to be
        busy."""
        if self._deadlines.pop(session_id, None) is None:
            return
        self._stop_sweep_if_idle()
        self._on_change(session_id, False)

    def finish(self, session_id: str) -> None:
        """Drop *session_id* now *as a completed run*: the agent itself said
        the turn is over (a progress termprop clear), so this is the sweep's
        timeout finish without the wait — on_change, then on_finished — where
        clear() is teardown and reports no finish at all. A session that
        isn't busy has no run to complete: no-op, so the CLI's repeated
        shutdown clears can't flag anything twice."""
        if self._deadlines.pop(session_id, None) is None:
            return
        self._stop_sweep_if_idle()
        self._on_change(session_id, False)
        if self._on_finished is not None:
            self._on_finished(session_id)

    def is_busy(self, session_id: str) -> bool:
        return session_id in self._deadlines

    def busy(self) -> set[str]:
        return set(self._deadlines)

    def stop(self) -> None:
        """Release the sweep timer (window teardown). Leaves no callbacks
        pointing at a window that is going away."""
        self._deadlines.clear()
        self._stop_sweep_if_idle()

    # -- the sweep ----------------------------------------------------------

    def _start_sweep(self) -> None:
        if self._sweep is None:
            self._sweep = self._add_timeout(self._sweep_ms, self._on_sweep)

    def _stop_sweep_if_idle(self) -> None:
        if self._sweep is not None and not self._deadlines:
            self._remove_timeout(self._sweep)
            self._sweep = None

    def _on_sweep(self) -> bool:
        now = self._clock()
        for session_id in [sid for sid, deadline in self._deadlines.items() if deadline <= now]:
            del self._deadlines[session_id]
            self._on_change(session_id, False)
            if self._on_finished is not None:
                self._on_finished(session_id)
        if not self._deadlines:
            self._sweep = None
            return False  # nothing left to time out; mark() starts it again
        return True


class EchoGate:
    """One terminal's "did the agent do this, or did we?" filter.

    ``contents-changed`` says the visible terminal changed, not that the child
    produced anything of its own. Three of the ways it fires have nothing to do
    with the agent working, and all three are answers to the app:

    - the user types. The agent renders every keystroke itself (it holds the
      terminal in raw mode), so a keypress comes back as real child output.
    - a tab is switched to. VTE reports the focus change to the child, which
      redraws — on both terminals, the one being left and the one arrived at.
    - the terminal is reflowed: a window resize, a panel divider drag, a font
      zoom, or simply the first time a tab is shown at its real size. VTE
      repaints with nothing at all having arrived from the child.

    The first two are announced by VTE's ``commit`` — anything the app sends
    the child, keystrokes and focus and mouse reports alike — so the window
    reports them with `poked`. The third shows up as a different grid size than
    the last redraw came at, which `counts` notices on its own.

    The gate also starts *held*: a terminal nothing has ever been sent to has
    no turn to be working on, yet its agent CLI paints a whole welcome screen
    at spawn — a burst of real child output that would otherwise start a pole
    on a tab the user only just opened. The first submit arms the gate for the
    life of the tab: a carriage return in a commit (a typed Enter, an injected
    prompt's send, a question card's answer — every way a turn starts goes
    through the pty as one), or the window's own report of a bare Enter via
    `arm`, which doesn't rely on what VTE encodes the key as.

    Discounting only decides whether a pole may *start*: the window still marks
    a session it already believes is working, so typing at an agent mid-turn
    can never stall its pole.
    """

    def __init__(
        self,
        *,
        quiet_s: float = ECHO_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._quiet_s = quiet_s
        self._clock = clock
        self._armed = False  # something has been submitted; poles may start
        self._poked_at: float | None = None
        self._grid: tuple[int, int] | None = None  # (columns, rows) at the last redraw

    def arm(self) -> None:
        """A turn was just asked for; redraws may mean work from here on."""
        self._armed = True

    def poked(self, text: str = "") -> None:
        """The app just sent this terminal's child *text*.

        A carriage return in it is a submit — never part of a spawn-time
        initial command (those end in a newline) or a focus report — so it
        arms the gate as a side effect.
        """
        self._poked_at = self._clock()
        if "\r" in text:
            self._armed = True

    def counts(self, grid: tuple[int, int]) -> bool:
        """Whether the redraw arriving now — at *grid* columns and rows — is
        the agent working rather than the terminal answering us."""
        reflowed = self._grid is not None and grid != self._grid
        self._grid = grid
        if not self._armed or reflowed:
            return False
        if self._poked_at is None:
            return True
        return self._clock() - self._poked_at >= self._quiet_s


class SpinnerWatch:
    """One terminal's "is something animating at a line start?" detector.

    While the agent works, its CLI draws a status line whose *text* is a
    moving target — the verb is random and user-configurable, the trailing
    hint configurable too — but whose animated indicator is always the first
    character of its line. So nothing here matches glyphs: the window samples
    the first character of every visible screen row, and a row that keeps
    changing between recent samples is an animation, which is an agent
    working. That covers the spinner sitting still on an otherwise quiet
    screen and, just as deliberately, output scrolling through — flowing
    output is exactly what the pole shows.

    The first column is what makes the signal echo-proof without a gate:
    keystrokes echo after the prompt marker, mid-line; a focus repaint
    redraws the same text; and a reflow arrives at a different grid, which
    resets the baseline instead of comparing across it.

    One change alone is not animation — a submitted prompt repaints the
    screen once, and so does a spawn-time welcome paint. `sample` only
    reports motion when the *previous* change was recent (`SPINNER_STREAK_S`),
    which a live spinner refreshes every sample and a one-off repaint never
    does. The cost is one extra sample (~`SPINNER_SAMPLE_S`) of latency on a
    pole this watch starts; poles the gate starts are as immediate as ever.

    `due` is the throttle, split from `sample` so the caller can skip the
    screen read entirely between samples — reading text out of VTE is the
    expensive half of the job.
    """

    def __init__(
        self,
        *,
        sample_s: float = SPINNER_SAMPLE_S,
        streak_s: float = SPINNER_STREAK_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sample_s = sample_s
        self._streak_s = streak_s
        self._clock = clock
        self._sampled_at: float | None = None
        self._changed_at: float | None = None  # when a sample last saw a change
        self._column: tuple[str, ...] | None = None  # first chars at the last sample
        self._grid: tuple[int, int] | None = None  # (columns, rows) it was read at

    def due(self) -> bool:
        """Whether enough time has passed that a fresh sample is worth taking."""
        return self._sampled_at is None or self._clock() - self._sampled_at >= self._sample_s

    def sample(self, first_column: Iterable[str], grid: tuple[int, int]) -> bool:
        """Record the screen's first column as read now, at *grid* columns and
        rows, and say whether it shows animation — this sample changed, and on
        the heels of another change."""
        column = tuple(first_column)
        now = self._clock()
        self._sampled_at = now
        reflowed = grid != self._grid
        previous, self._column, self._grid = self._column, column, grid
        if reflowed:  # rewrapped text moves every line; nothing comparable
            self._changed_at = None
            return False
        if previous is None or column == previous:
            return False
        streak = self._changed_at is not None and now - self._changed_at <= self._streak_s
        self._changed_at = now
        return streak


# Vte.ProgressHint values, spelled out so this module stays GTK-free. VTE maps
# ConEmu's OSC 9;4 states onto these one-to-one; every state but INACTIVE means
# the agent calls itself mid-turn. A cleared property (the CLI sent state 0)
# reads back from VTE as *no value*, not as INACTIVE — callers pass that as
# None, and reading() treats the two identically.
PROGRESS_HINT_INACTIVE = 0
_BUSY_HINTS = frozenset({1, 2, 3, 4})  # ACTIVE, ERROR, INDETERMINATE, PAUSED


class ProgressWatch:
    """One terminal's progress-termprop interpreter: the agent's own word.

    Claude Code announces its busy state through the terminal as ConEmu-style
    OSC 9;4 progress sequences — a busy hint as a turn starts, a clear as it
    ends — which VTE parses into the ``vte.progress.hint`` termprop the window
    forwards here. Unlike every other tab source this is not inference, so it
    gets the one power no inferred source can be trusted with: ending the pole
    the instant the agent says the turn is over, instead of waiting out an
    idle window.

    `reading` maps each hint change to the pole action it asks for: ``"mark"``
    for a busy hint (with the wide `PROGRESS_IDLE_S` window — the termprop is
    edge-triggered, and only its own clear normally ends the pole), ``"finish"``
    for a clear, or None. The finish is gated on this tab having spoken a busy
    hint before — a tab whose CLI never emits progress keeps its inferred
    poles untouched, and one whose CLI stops emitting mid-session (a version
    downgrade, say) falls back to inference rather than fighting it.

    `quiet` is the finish's shadow: for a beat after the agent calls a turn
    over, its trailing repaints — the prompt box returning, the working
    indicator fading — must not read as a new turn starting, or the honest
    instant-down would blip right back up for `IDLE_S`. Only redraw-inferred
    pole *starts* defer to it; a real new turn arrives by other roads (the
    Enter pre-mark, the next busy hint) and is never held back.
    """

    def __init__(
        self,
        *,
        quiet_s: float = PROGRESS_QUIET_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._quiet_s = quiet_s
        self._clock = clock
        self._spoken = False  # a busy hint has been seen; clears mean something
        self._finished_at: float | None = None

    def reading(self, hint: int | None) -> str | None:
        """The pole action a hint change asks for: "mark", "finish", or None.

        *hint* is the termprop's new value — None for cleared, which is how
        VTE reports the CLI's "remove progress" state.
        """
        if hint in _BUSY_HINTS:
            self._spoken = True
            return "mark"
        if not self._spoken:
            return None
        self._finished_at = self._clock()
        return "finish"

    def quiet(self) -> bool:
        """Whether a turn just ended here, so a redraw arriving now is its
        trailing repaint rather than evidence of a new one."""
        return self._finished_at is not None and self._clock() - self._finished_at < self._quiet_s


def _glib_add_timeout(interval_ms: int, callback: Callable[[], bool]) -> int:
    from gi.repository import GLib

    return GLib.timeout_add(interval_ms, callback)


def _glib_remove_timeout(source: int) -> None:
    from gi.repository import GLib

    GLib.source_remove(source)
