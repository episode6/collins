# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""What a `Session` needs of a terminal, as two ports.

A session writes to a pty and reads a screen; which pty and which screen is
not its business. Today both are the tab's own `Vte.Terminal`
(`terminal.VtePtyPort`, `terminal.VteScreenPort`); later the service's pty
and its headless screen model (spec §3.5, the split's swaps 2 and 3). The
unit suite drives a `Session` through fakes of both.

Coordinates. Every row index a `ScreenPort` hands out or takes is counted
from the top of the *visible screen as anchored to the cursor*: the screen's
first row is the cursor's row minus the screen's height plus one (never
above the buffer's first row). That anchoring is what every reader of the
agent's screen has always used — what the user has scrolled back to never
changes what the agent's box reads as — and it is the natural frame of a
headless screen model, which has no scrollback position at all. Columns are
cells (a wide character fills two), never characters.

Beyond §3.5's list each port has the methods a moved reader genuinely
needed, every one documented where it is declared: `ScreenPort.row_count`
(the echo gate's grid), `ScreenPort.row_text` (the cursor's one line, read
on its own on every key press) and `ScreenPort.visible_text` (the screen as
one joined read, which the CLI's error and dialog matchers were written
against).
"""

from __future__ import annotations

from typing import Protocol


class PtyPort(Protocol):
    """What a Session writes to and asks of a pty."""

    def write(self, data: bytes) -> None:
        """Send *data* to the child, as if typed."""
        ...

    def resize(self, cols: int, rows: int) -> None:
        """Set the pty's window size (the child sees a SIGWINCH)."""
        ...

    def child_pid(self) -> int | None:
        """The pid of the process spawned on the pty (the user's shell),
        or None while nothing is spawned — before the spawn lands, or for
        a spawn that failed."""
        ...

    def foreground_pgrp(self) -> int | None:
        """The pty's foreground process group (``tcgetpgrp``), or None
        with no pty or when it can't be read."""
        ...


class ScreenPort(Protocol):
    """What a Session reads off the screen. Rows are cursor-anchored screen
    rows (see the module docstring), columns are cells."""

    def rows(self) -> list[str]:
        """The screen's rows from its first through the bottom of the
        buffer, each one screen row — soft-wrapped lines split back on
        their row boundaries, by cells. What `entered_prompt` reads the
        input box out of, which reaches past the cursor: continuation rows
        sit below it whenever the cursor was arrowed back up into the box."""
        ...

    def cursor(self) -> tuple[int, int]:
        """The cursor as (column, row), the row an index into `rows`."""
        ...

    def columns(self) -> int:
        """The screen's width in cells."""
        ...

    def row_count(self) -> int:
        """The screen's height in rows.

        Not in §3.5's list: the echo gate compares redraws by the grid they
        were drawn at, (columns, rows), and a resize is the one redraw it
        must never discount (activity.EchoGate.counts)."""
        ...

    def tail_is_faint(self, row: int, column: int) -> bool:
        """Whether row *row* from *column* to its end is drawn dim (SGR 2)
        — the agent's ghost suggestion, which reads as typed text in any
        plain-text read."""
        ...

    def first_column(self) -> tuple[str, ...]:
        """The first character of each screen row ("" for a blank one),
        one entry per row of the screen's height — what the spinner watch
        compares between samples."""
        ...

    def capture_contents(self) -> str:
        """The terminal's whole text, scrollback included, as plain text."""
        ...

    def row_text(self, row: int, end_column: int) -> str:
        """What row *row* says from its start up to *end_column* (cells,
        exclusive). Trailing cells that were never written aren't reported.

        Not in §3.5's list: `takes_prompt` is asked on every key press the
        terminal doesn't otherwise claim, and reads the cursor's one line —
        a whole-screen read for it would be the wrong cost, and a row cut
        back out of a joined read is not always the same text (a wide
        character straddling a wrap)."""
        ...

    def visible_text(self) -> str:
        """The screen from its first row through the cursor's, as one plain
        read: soft-wrapped rows joined as the terminal joins them, hard
        line breaks as newlines.

        Not in §3.5's list: the CLI's worktree error line and its "keep or
        remove this worktree?" dialog are matched against this text
        (providers.ClaudeProvider.worktree_launch_failed /
        worktree_exit_prompt), and rows re-joined with newlines would split
        a wrapped line the matchers read whole."""
        ...
