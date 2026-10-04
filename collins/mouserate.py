# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""The mouse rule of the split (spec §3.3 "Mouse"), GTK-free.

Fullscreen mode turns on any-motion tracking, and VTE commits one SGR
report per pointer event (F11): a fast mouse is thousands of bytes a
second nobody wants on a link. A no-button motion report that names the
cell of the one before it is dropped, and the rest are coalesced to the
latest one per `MOTION_COALESCE_MS`; a press, a release, a wheel step and
a drag (motion with a button down) are never dropped, and flush whatever
motion was pending ahead of them so the order the program sees is the
order the pointer moved. `ptyclient.ClientTerminal` runs every commit
through a `MotionCoalescer` and owns the timer that calls `flush`.
"""

from __future__ import annotations

import re

MOTION_COALESCE_MS = 30
# An SGR mouse report: button code, column, row, M (press/motion) or m
# (release). Motion with no button held has code 35 (32 + 3), plus any
# modifier bits (4 shift, 8 meta, 16 control).
MOUSE_RE = re.compile(rb"\x1b\[<(\d{1,5});(\d{1,5});(\d{1,5})([Mm])")
_NO_BUTTON = 3
_MOTION_BIT = 32
_WHEEL_BIT = 64
_EXTRA_BUTTONS_BIT = 128  # buttons 8 to 11: a drag with one held is never plain


def is_plain_motion(code: int) -> bool:
    """A motion report with no button held: the only kind ever dropped."""
    return (
        bool(code & _MOTION_BIT)
        and not code & (_WHEEL_BIT | _EXTRA_BUTTONS_BIT)
        and (code & 3) == _NO_BUTTON
    )


class MotionCoalescer:
    """Feed it every commit, get back what to send now; `flush()` what it
    is holding. A pending plain-motion report is held until the caller's
    timer calls `flush` or until anything that is not a plain motion
    arrives."""

    def __init__(self) -> None:
        self.pending: bytes | None = None
        self.last_cell: tuple[int, int] | None = None

    def take(self, data: bytes) -> tuple[bytes, bool]:
        """(what to send now, whether something is now pending)."""
        out = bytearray()
        pos = 0
        for match in MOUSE_RE.finditer(data):
            if match.start() > pos:
                out += self._flush_into(data[pos : match.start()])
            code = int(match.group(1))
            if is_plain_motion(code) and match.group(4) == b"M":
                cell = (int(match.group(2)), int(match.group(3)))
                if cell != self.last_cell:  # the cell of the report before: dropped
                    self.last_cell = cell
                    self.pending = match.group(0)  # the latest one wins
            else:
                self.last_cell = None
                out += self._flush_into(match.group(0))
            pos = match.end()
        if pos < len(data):
            out += self._flush_into(data[pos:])
        return bytes(out), self.pending is not None

    def _flush_into(self, then: bytes) -> bytes:
        """Anything that is not a plain motion goes out behind the motion
        pending before it, so order is kept."""
        held, self.pending = self.pending, None
        return (held or b"") + then

    def flush(self) -> bytes:
        held, self.pending = self.pending, None
        return held or b""
