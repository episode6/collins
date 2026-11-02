# SPDX-License-Identifier: MIT
# Copyright (c) 2026 episode6, Inc.
#
# Part of the conformance kit for the Collins service API (conformance/),
# licensed MIT like the specification it tests (see conformance/LICENSE),
# unlike the rest of Collins. Standard library only.
"""What an attach reply's `modes` list should hold after a stream.

docs/api/terminal.md: `modes` is the set of modes the redraw moves off a
fresh terminal's defaults, each as the bytes between `CSI` and the end of
its sequence, less `?3`, `?25`, `?1048` and the alternate-screen modes,
which the redraw handles itself; the kitty keyboard flags are a stack
whose base and pushed entries the redraw asserts in order. This module
follows the mode sets and resets, the kitty stack and modifyOtherKeys
through a byte stream, and names the expected list, with the defaults
taken from the DECRQM table the kit ships (a mode whose default state is
"set" is off its default when reset, and the other way round).

It is a reading of the page, not of any terminal's code: a small CSI
tokenizer, enough for the published scenarios.
"""

from __future__ import annotations

import re

CSI_RE = re.compile(rb"\x1b\[([?>=<!]?)([0-9;:]*)([\x20-\x2f]*)([\x40-\x7e])")
HANDLED_ELSEWHERE = {"?3", "?25", "?1048", "?47", "?1047", "?1049"}


class ModeTracker:
    def __init__(self, decrqm: dict) -> None:
        self.private_defaults = {int(k): v for k, v in decrqm.get("private", {}).items()}
        self.ansi_defaults = {int(k): v for k, v in decrqm.get("ansi", {}).items()}
        self.reset()

    def reset(self) -> None:
        self.private: dict[int, bool] = {}
        self.ansi: dict[int, bool] = {}
        self.kitty_base = 0
        self.kitty_stack: list[int] = []
        self.modify_other_keys = 0
        self.region_top = 1  # the scroll region's top row (DECSTBM), for a cursor report in origin mode

    def feed(self, data: bytes) -> None:
        position = 0
        while True:
            esc = data.find(b"\x1b", position)
            if esc < 0:
                return
            if data[esc : esc + 2] == b"\x1bc":
                self.reset()
                position = esc + 2
                continue
            match = CSI_RE.match(data, esc)
            if match is None:
                position = esc + 1
                continue
            prefix, params, intermediate, final = (
                match.group(1).decode(),
                match.group(2).decode(),
                match.group(3).decode(),
                match.group(4).decode(),
            )
            position = match.end()
            if (intermediate == "!" or prefix == "!") and final == "p":  # DECSTR: modes to their defaults
                self.private.clear()
                self.ansi.clear()
                self.region_top = 1
                continue
            if intermediate:
                continue
            if final == "r" and prefix == "":
                numbers = [int(x) for x in params.split(";") if x.isdigit()]
                self.region_top = numbers[0] if numbers and numbers[0] > 0 else 1
                continue
            if final in ("h", "l") and prefix in ("", "?"):
                on = final == "h"
                for part in params.split(";"):
                    if part.isdigit():
                        (self.private if prefix == "?" else self.ansi)[int(part)] = on
            elif final == "u":
                numbers = [int(x) for x in re.split(r"[;:]", params) if x.isdigit()]
                if prefix == ">":
                    if len(self.kitty_stack) < 32:
                        self.kitty_stack.append(numbers[0] if numbers else 0)
                elif prefix == "<":
                    count = numbers[0] if numbers else 1
                    for _ in range(count):
                        if self.kitty_stack:
                            self.kitty_stack.pop()
                elif prefix == "=":
                    flags = numbers[0] if numbers else 0
                    mode = numbers[1] if len(numbers) > 1 else 1
                    current = self.kitty_stack[-1] if self.kitty_stack else self.kitty_base
                    if mode == 1:
                        value = flags
                    elif mode == 2:
                        value = current | flags
                    else:
                        value = current & ~flags
                    if self.kitty_stack:
                        self.kitty_stack[-1] = value
                    else:
                        self.kitty_base = value
            elif final == "m" and prefix == ">":
                numbers = [int(x) for x in params.split(";") if x.isdigit()]
                if numbers[:1] == [4]:
                    self.modify_other_keys = numbers[1] if len(numbers) > 1 else 0

    def _default_on(self, defaults: dict[int, int], mode: int) -> bool | None:
        """Whether the mode is set on a fresh terminal: True, False, or None
        for a mode VTE does not know (its reset is nothing to assert)."""
        state = defaults.get(mode)
        if state is None:
            return None
        if state in (1, 3):
            return True
        return False

    def expected(self) -> set[str]:
        out: set[str] = set()
        for mode, on in self.private.items():
            label = f"?{mode}"
            if label in HANDLED_ELSEWHERE:
                continue
            default = self._default_on(self.private_defaults, mode)
            if default is None and not on:
                continue
            if default is None or on != default:
                out.add(f"{label}{'h' if on else 'l'}")
        for mode, on in self.ansi.items():
            default = self._default_on(self.ansi_defaults, mode)
            if default is None and not on:
                continue
            if default is None or on != default:
                out.add(f"{mode}{'h' if on else 'l'}")
        if self.kitty_base:
            out.add(f"={self.kitty_base}u")
        for entry in self.kitty_stack:
            out.add(f">{entry}u")
        if self.modify_other_keys:
            out.add(f">4;{self.modify_other_keys}m")
        return out


def expected_modes(data: bytes, decrqm: dict) -> set[str]:
    tracker = ModeTracker(decrqm)
    tracker.feed(data)
    return tracker.expected()
