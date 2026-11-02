# SPDX-License-Identifier: MIT
# Copyright (c) 2026 episode6, Inc.
#
# Part of the conformance kit for the Collins service API (conformance/),
# licensed MIT like the specification it tests (see conformance/LICENSE),
# unlike the rest of Collins. Standard library only.
"""The kit's reference for an attach reply's `modes`, from docs/api/terminal.md.

The page says what the list holds: the modes the redraw moves off a fresh
terminal's defaults, on or off, each written as the bytes between `CSI` and
the end of its sequence (`"?1004h"`, `"7l"`, `">5u"`); `?3`, `?25`, `?1048`
and the alternate-screen modes left out, since the redraw handles them
itself; the kitty keyboard flags as a stack whose base and pushed entries
the redraw asserts in order; `RIS` clearing everything; `DECSTR` resetting
the modes and forgetting the region. This module reads a byte stream for
exactly that and answers what the list should be, so a check can hold a
server's reply to it. It is written from the page and the page's own
examples; a fresh terminal's defaults come from the DECRQM table the kit
ships (`data/decrqm-modes.json`), where a state of 1 or 3 is "set on a
fresh terminal" and 2 or 4 "reset".

The reading is deliberately small: the handful of control functions that
change what `modes` says, found by one regular expression over the stream,
dispatched by their final byte. Everything else in the stream is passed
over.
"""

from __future__ import annotations

import re

# One regular expression finds what this module reads: a control sequence
# (CSI, with an optional private marker, parameters, intermediates and the
# final byte) or the two-byte RIS.
CONTROL = re.compile(
    rb"\x1b(?:c|\[(?P<marker>[?>=<!]?)(?P<params>[0-9;:]*)(?P<inter>[\x20-\x2f]*)(?P<final>[\x40-\x7e]))"
)

# The modes the redraw handles itself and the list never names (terminal.md,
# "An attach's redraw").
LEFT_OUT = frozenset({"?3", "?25", "?1048", "?47", "?1047", "?1049"})

KITTY_ENTRIES_KEPT = 32  # pushed entries a server keeps at most, per the page's informative half


def fresh_terminal(decrqm_table: dict) -> dict[str, bool]:
    """Which modes are on and off on a fresh terminal, keyed as the page
    writes a mode (`"?1004"`, `"4"`), from VTE's DECRQM answers: 1 and 3
    are set, 2 and 4 reset; a mode the table lacks is unknown and absent."""
    picture: dict[str, bool] = {}
    for section, prefix in (("private", "?"), ("ansi", "")):
        for number, state in decrqm_table.get(section, {}).items():
            picture[f"{prefix}{int(number)}"] = state in (1, 3)
    return picture


def _numbers(params: bytes) -> list[int]:
    return [int(part) for part in re.split(rb"[;:]", params) if part.isdigit()]


class ExpectedModes:
    """Feed it the bytes a pty saw; ask it what `modes` must list."""

    def __init__(self, decrqm_table: dict) -> None:
        self._fresh = fresh_terminal(decrqm_table)
        self.asserted: dict[str, bool] = {}  # every mode the stream set or reset, as the page keys it
        self.kitty: list[int] = [0]  # [0] the flags in force with nothing pushed; the rest, pushed entries
        self.other_keys = 0  # the modifyOtherKeys level
        self.scroll_top = 1  # DECSTBM's top row, which a cursor report in origin mode counts from

    # -- reading ------------------------------------------------------------------

    def feed(self, data: bytes) -> None:
        for match in CONTROL.finditer(data):
            if match.group("marker") is None:  # RIS
                self._reset_everything()
                continue
            handler = self._HANDLERS.get(match.group("final"))
            if handler is not None:
                handler(self, match.group("marker"), match.group("params"), match.group("inter"))

    def _reset_everything(self) -> None:
        self.asserted.clear()
        self.kitty = [0]
        self.other_keys = 0
        self.scroll_top = 1

    def _set_or_reset(self, marker: bytes, params: bytes, inter: bytes, on: bool) -> None:
        if inter or marker not in (b"", b"?"):
            return
        prefix = marker.decode()
        for number in _numbers(params):
            self.asserted[f"{prefix}{number}"] = on

    def _mode_h(self, marker, params, inter) -> None:
        self._set_or_reset(marker, params, inter, True)

    def _mode_l(self, marker, params, inter) -> None:
        self._set_or_reset(marker, params, inter, False)

    def _soft_reset(self, marker, params, inter) -> None:
        # `CSI ! p` is DECSTR: the modes back to their defaults, the region forgotten.
        if marker == b"!" or inter == b"!":
            self.asserted.clear()
            self.scroll_top = 1

    def _region(self, marker, params, inter) -> None:
        if marker or inter:
            return
        numbers = _numbers(params)
        self.scroll_top = numbers[0] if numbers and numbers[0] > 0 else 1

    def _kitty(self, marker, params, inter) -> None:
        if inter:
            return
        numbers = _numbers(params)
        if marker == b">":  # push
            if len(self.kitty) - 1 < KITTY_ENTRIES_KEPT:
                self.kitty.append(numbers[0] if numbers else 0)
        elif marker == b"<":  # pop
            for _ in range(numbers[0] if numbers else 1):
                if len(self.kitty) > 1:
                    self.kitty.pop()
        elif marker == b"=":  # set the flags in force: the top entry, or the base
            flags = numbers[0] if numbers else 0
            how = numbers[1] if len(numbers) > 1 else 1
            current = self.kitty[-1]
            self.kitty[-1] = {1: flags, 2: current | flags}.get(how, current & ~flags)

    def _other_keys(self, marker, params, inter) -> None:
        if marker == b">" and not inter:
            numbers = _numbers(params)
            if numbers[:1] == [4]:
                self.other_keys = numbers[1] if len(numbers) > 1 else 0

    _HANDLERS = {
        b"h": _mode_h,
        b"l": _mode_l,
        b"p": _soft_reset,
        b"r": _region,
        b"u": _kitty,
        b"m": _other_keys,
    }

    # -- answering ----------------------------------------------------------------

    def origin_mode(self) -> bool:
        return self.asserted.get("?6", self._fresh.get("?6", False))

    def picture_now(self) -> dict[str, bool]:
        """The modes as the stream left them: a fresh terminal's, overlaid
        with what the stream asserted."""
        now = dict(self._fresh)
        now.update(self.asserted)
        return now

    def expected(self) -> set[str]:
        """What `modes` must list: every mode whose state now differs from a
        fresh terminal's (a mode VTE does not know counts only when set, since
        its reset is nothing to assert), spelled as the page spells them, less
        the ones the redraw handles itself; then the kitty flags, base first
        and pushed entries after, and modifyOtherKeys when set."""
        now = self.picture_now()
        moved = {
            label
            for label, on in now.items()
            if label in self.asserted
            and label not in LEFT_OUT
            and (on != self._fresh[label] if label in self._fresh else on)
        }
        listing = {f"{label}{'h' if now[label] else 'l'}" for label in moved}
        base, *pushed = self.kitty
        if base:
            listing.add(f"={base}u")
        listing.update(f">{entry}u" for entry in pushed)
        if self.other_keys:
            listing.add(f">4;{self.other_keys}m")
        return listing


def modes_after(data: bytes, decrqm_table: dict) -> set[str]:
    """The `modes` list a server should answer an attach with after *data*."""
    reference = ExpectedModes(decrqm_table)
    reference.feed(data)
    return reference.expected()
