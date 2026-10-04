# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""The redraw guard of the split's client (spec §3.4), GTK-free.

A client's VTE is painted from the service's screen model whenever it
attaches, falls behind (§3.2's flow control) or is resized for another
client: a **redraw**, frames flagged ``REDRAW`` ending in one flagged
``REDRAW_END``. A redraw is synthesized by the service and holds no query,
so anything the VTE says on its own account while it is being repainted
(an answer to a query the preamble re-asserts a mode with, a focus report
the reset provokes) is the VTE's, not the person's, and must not reach
the pty as if typed. From the redraw's first frame until the VTE has
answered a **sentinel** fed after its last frame, every commit is dropped.

The sentinel carries a generation: ``OSC 4 ; <index> ; ?``, which VTE
answers with the index echoed (``OSC 4 ; <index> ; rgb:…``), the index
being ``SENTINEL_BASE + generation mod SENTINEL_SPAN``. A guard raised
again before the previous sentinel was answered (two attaches back to
back; a flow-control redraw landing on a screen mid-redraw) bumps the
generation, and only the answer to the **latest** sentinel lowers it: an
older answer, or one the reset threw away, is swallowed with the rest.
(DSR 5, F3's sentinel, answers ``CSI 0 n`` whatever was asked, so it
cannot tell two sentinels apart.) The guard never stays up for good: a
watchdog of `WATCHDOG_MS` lowers it and says so, for an attach whose
redraw never came (the request failed, the sink was cut mid-redraw).

`RedrawGuard` is the state machine; `ptyclient.ClientTerminal` owns the
VTE, the client and the timer and asks it what to do.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

log = logging.getLogger(__name__)

FLAG_REDRAW = 0x01
FLAG_REDRAW_END = 0x02
WATCHDOG_MS = 2000
SENTINEL_BASE = 16  # palette indices 16 to 255: never the 16 a theme sets
SENTINEL_SPAN = 240
_ANSWER_RE = re.compile(rb"\x1b\]4;(\d{1,3});rgb:[0-9a-fA-F/]{1,20}(?:\x07|\x1b\\)")


def sentinel(generation: int) -> bytes:
    return b"\x1b]4;%d;?\x07" % (SENTINEL_BASE + generation % SENTINEL_SPAN)


class RedrawGuard:
    """See the module docstring. *schedule(ms, fn)* arms a one-shot timer
    and returns its id, *cancel(id)* disarms it (GLib's timeout_add and
    source_remove; fakes in the tests)."""

    def __init__(
        self, schedule: Callable[[int, Callable[[], None]], int], cancel: Callable[[int], None]
    ) -> None:
        self._schedule = schedule
        self._cancel = cancel
        self.up = False
        self.in_redraw = False
        self.generation = 0
        self._watchdog = 0
        self.dropped = 0  # commits swallowed, for the checks
        self.expired = 0  # watchdog expiries, for the checks and the log

    # -- the service's frames

    def on_frame(self, flags: int) -> tuple[bool, bytes | None]:
        """A frame arrived with *flags*: (reset the VTE first, the
        sentinel to feed after the frame or None)."""
        reset = False
        if flags & FLAG_REDRAW and not self.in_redraw:
            # The first frame of a redraw not announced by `raise_for_attach`:
            # the screen it lands on is stale, so the VTE is reset first.
            self.in_redraw = True
            reset = True
            self._raise()
        if flags & FLAG_REDRAW_END and self.in_redraw:
            self.in_redraw = False
            return reset, sentinel(self.generation)
        return reset, None

    def raise_for_attach(self) -> None:
        """An attach is being asked for: the VTE is about to be reset by
        the caller, the redraw follows."""
        self.in_redraw = True
        self._raise()

    def attach_failed(self) -> None:
        """The attach request raised: no redraw is coming."""
        self.in_redraw = False
        self._lower("the attach failed")

    def _raise(self) -> None:
        self.generation += 1
        self.up = True
        if self._watchdog:
            self._cancel(self._watchdog)
        self._watchdog = self._schedule(WATCHDOG_MS, self._on_watchdog)

    def _on_watchdog(self) -> None:
        self._watchdog = 0
        if self.up:
            self.expired += 1
            self.in_redraw = False
            log.warning("redraw guard: no answer to the sentinel in %d ms; lowering it", WATCHDOG_MS)
            self._lower("the watchdog")

    def _lower(self, why: str) -> None:
        self.up = False
        if self._watchdog:
            self._cancel(self._watchdog)
            self._watchdog = 0

    # -- the VTE's commits

    def on_commit(self, data: bytes) -> bool:
        """What the VTE committed: True when it may go to the pty, False
        when the guard swallowed it (the sentinel's answer lowers the
        guard; the answer itself never goes)."""
        if not self.up:
            return True
        self.dropped += 1
        wanted = SENTINEL_BASE + self.generation % SENTINEL_SPAN
        for match in _ANSWER_RE.finditer(data):
            if int(match.group(1)) == wanted:
                self._lower("the sentinel's answer")
                break
        return False
