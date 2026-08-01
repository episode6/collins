"""Which sessions are working *right now*, so the sidebar can say so.

A session's status says where it is running — in a tab, or detached — but not
whether the agent is doing anything. That is what the barber pole on a row's
guide line answers, and it needs a signal that says "output is flowing" and,
harder, "output stopped".

There is no such signal from the agent, so every source here is inferred:

- a **tab**'s terminal emits ``contents-changed`` on every redraw, which the
  window feeds to `ActivityTracker.mark`. It is noisy by design — a spinner
  frame counts — but silence is what matters, and a prompt sitting idle is
  silent. `EchoGate` throws out the redraws the *app* caused, which are
  otherwise indistinguishable from a working agent.
- a **detached** (`/bg`) session has no terminal to listen to, so
  `TranscriptActivity` watches its transcript instead: the JSONL only grows
  while the agent is producing turns.
- an open tab whose terminal has gone quiet may still have a **background
  process** running below the agent — a dev server, a long build — that
  produces no terminal output of its own. The window polls each open tab's
  process tree (`proctree.has_live_descendant`) and marks the session for as
  long as one is found.

All three funnel into one tracker: "busy" means output seen within the
source's idle window — `IDLE_S` for a terminal, which redraws continuously
while its agent works, the wider `DETACHED_IDLE_S` for a transcript, which
grows only in bursts, and `PROCESS_IDLE_S` for a process-tree sighting.

Nothing here touches GTK — the timer is injected — so the whole thing is
testable without a display.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

# How long output has to stop before a session reads as idle. Long enough to
# ride out the pauses inside a turn — an agent thinking between tool calls
# prints nothing for a beat — and short enough that a finished turn stops the
# pole while the user is still looking at the row. This is the window for a
# tab's terminal, which repaints continuously (spinner frames count) while
# its agent works.
IDLE_S = 2.0

# The window for a detached session, whose only signal is its transcript. The
# JSONL grows in bursts: nothing is appended while a response is generating or
# a tool is running, only when the finished message or result lands, so quiet
# gaps of several seconds are normal mid-turn. `IDLE_S` would flicker the pole
# off inside every one of them; this rides them out. The cost of being
# generous is only that the pole lingers this long after the final turn.
DETACHED_IDLE_S = 15.0

# How often the sweep looks for sessions that went quiet. Only the moment a
# session stops being busy is this coarse; starting is immediate, on the first
# byte of output.
SWEEP_MS = 250

# How often a detached session's transcript is stat'ed. Coarser than the sweep
# because it costs a syscall per detached session, and because a background
# agent's turns are seconds apart, not milliseconds.
TRANSCRIPT_POLL_MS = 1000

# How often an open tab's process tree is checked for something the agent left
# running below it — a background job (a dev server, a long build) that keeps
# no output flowing to the terminal, and so would otherwise read as idle the
# moment its last line scrolled by. Coarser than the sweep for the same reason
# as the transcript poll: it costs a handful of /proc reads per open tab.
PROCESS_POLL_MS = 2000

# The window a live descendant keeps a session's pole up for. Wider than
# PROCESS_POLL_MS so ordinary scheduling jitter never closes the gap between
# two sightings of the same still-running process.
PROCESS_IDLE_S = 5.0

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
    """

    def __init__(
        self,
        on_change: Callable[[str, bool], None],
        *,
        idle_s: float = IDLE_S,
        sweep_ms: int = SWEEP_MS,
        clock: Callable[[], float] = time.monotonic,
        add_timeout: Callable[[int, Callable[[], bool]], int] | None = None,
        remove_timeout: Callable[[int], None] | None = None,
    ) -> None:
        self._on_change = on_change
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


class TranscriptActivity:
    """Turns transcript growth into activity marks, for sessions with no tab.

    A detached agent's only visible sign of life is its JSONL getting longer,
    so each poll compares (mtime, size) against the last one. The *first* sight
    of a session is a baseline, never a mark: without a previous reading there
    is no way to tell a session that just wrote a turn from one whose file has
    sat untouched for a week.
    """

    def __init__(
        self,
        mark: Callable[[str], None],
        *,
        stat: Callable[[Path], tuple[float, int] | None] | None = None,
    ) -> None:
        self._mark = mark
        self._stat = stat or _stat_transcript
        self._seen: dict[str, tuple[float, int]] = {}

    def poll(self, transcripts: Mapping[str, Path]) -> None:
        """Stat every given transcript, marking the ones that grew.

        *transcripts* maps session id to file, and is expected to shrink as
        sessions stop running detached; readings for sessions no longer in it
        are dropped, so one that comes back is baselined afresh rather than
        compared against a stale size.
        """
        for session_id, path in transcripts.items():
            reading = self._stat(path)
            if reading is None:  # never written, or gone: nothing to compare
                continue
            previous = self._seen.get(session_id)
            self._seen[session_id] = reading
            if previous is not None and reading != previous:
                self._mark(session_id)
        self.forget_all_but(transcripts)

    def forget_all_but(self, keep: Iterable[str]) -> None:
        for session_id in set(self._seen) - set(keep):
            del self._seen[session_id]


def _stat_transcript(path: Path) -> tuple[float, int] | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_mtime, info.st_size


def _glib_add_timeout(interval_ms: int, callback: Callable[[], bool]) -> int:
    from gi.repository import GLib

    return GLib.timeout_add(interval_ms, callback)


def _glib_remove_timeout(source: int) -> None:
    from gi.repository import GLib

    GLib.source_remove(source)
