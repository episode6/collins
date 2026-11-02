# SPDX-License-Identifier: MIT
# Copyright (c) 2026 episode6, Inc.
#
# Part of the conformance kit for the Collins service API (conformance/),
# licensed MIT like the specification it tests (see conformance/LICENSE),
# unlike the rest of Collins. Standard library only.
"""Driving the fake's probe from the kit's side of the pty.

The probe runs inside the pty (`fake_claude.py --probe`, or the fake's
``probe`` prompt) with the terminal in raw mode. The kit types it commands
as input frames, one per line, and reads its reports off the pty's output
stream: each is an ``OSC 777 ; <text> ST`` string, which the server
forwards untouched and no terminal draws. ``Q <seq> <hex>`` writes bytes
to the terminal and reports the answer that came back within half a
second as hex, so the kit sees the server's reply as the child saw it;
``B <hex>`` / ``P <seq>`` paint a stream in pieces (a typed line stays
short); ``E <seq>`` reports the environment; ``Z <seq>`` the pty's size.
"""

from __future__ import annotations

import re
import time

from . import client as apiclient
from .reporter import CheckFailed

REPORT = re.compile(rb"\x1b\]777;([^\x1b\x07]*)(?:\x1b\\|\x07)")
LINE_HEX = 1024  # hex characters per typed line
REPLY_TIMEOUT_S = 8.0


def parse_reports(output: bytes) -> list[str]:
    """Every probe report in *output*, in order."""
    return [match.group(1).decode("utf-8", "replace") for match in REPORT.finditer(output)]


def parse_hex_report(text: str) -> tuple[str, bytes]:
    """A ``R <seq> <hex>`` report as (seq, bytes); an empty hex is no bytes."""
    parts = text.split(" ", 2)
    if len(parts) < 2 or parts[0] != "R":
        raise ValueError(f"not a reply report: {text!r}")
    seq = parts[1]
    hex_text = parts[2] if len(parts) > 2 else ""
    try:
        return seq, bytes.fromhex(hex_text)
    except ValueError as error:
        raise ValueError(f"bad hex in report {text!r}") from error


class Probe:
    def __init__(self, client: apiclient.Client, pty: int) -> None:
        self.client = client
        self.pty = pty
        self.seq = 0
        self.scanned = 0  # how far into the output the reports were read
        self.reports: list[str] = []

    def _type(self, line: str) -> None:
        self.client.send_input(self.pty, (line + "\n").encode("ascii"))

    def _scan(self) -> None:
        data = bytes(self.client.output.get(self.pty, b""))
        new = data[self.scanned :]
        # A report cut by a frame boundary: leave the unfinished tail unscanned.
        last_start = new.rfind(b"\x1b]777;")
        end = len(new)
        if last_start >= 0 and REPORT.match(new, last_start) is None:
            end = last_start
        self.reports.extend(parse_reports(new[:end]))
        self.scanned += end

    def wait_report(self, prefix: str, timeout: float = REPLY_TIMEOUT_S) -> str:
        """The first unread report starting with *prefix*."""
        deadline = time.monotonic() + timeout
        consumed = 0
        while True:
            self._scan()
            for index, report in enumerate(self.reports):
                if report.startswith(prefix):
                    del self.reports[index]
                    return report
            consumed = len(self.reports)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CheckFailed(
                    f"no probe report {prefix!r} within {timeout:.0f} s "
                    f"(reports: {self.reports[-3:]}, {consumed} unread)"
                )
            self.client.pump(min(remaining, 0.2))

    def ready(self, timeout: float = 15.0) -> None:
        self.wait_report("READY", timeout)

    def query(self, data: bytes, timeout: float = REPLY_TIMEOUT_S) -> bytes:
        """Write *data* to the terminal; what came back within half a second."""
        self.seq += 1
        seq = str(self.seq)
        self._type(f"Q {seq} {data.hex()}")
        _, answer = parse_hex_report(self.wait_report(f"R {seq} ", timeout))
        return answer

    def query_split(self, first: bytes, second: bytes, timeout: float = REPLY_TIMEOUT_S) -> bytes:
        """Write *first*, then *second* a tenth of a second later, with nothing
        of the probe's in between; what came back within half a second."""
        self.seq += 1
        seq = str(self.seq)
        self._type(f"W {seq} {first.hex()} {second.hex()}")
        _, answer = parse_hex_report(self.wait_report(f"R {seq} ", timeout))
        return answer

    def paint(self, data: bytes, timeout: float = REPLY_TIMEOUT_S) -> None:
        """Write *data* to the terminal in pieces and wait for the probe to
        have done so (and swallowed any answers the bytes drew)."""
        hex_text = data.hex()
        for start in range(0, len(hex_text), LINE_HEX):
            self._type(f"B {hex_text[start : start + LINE_HEX]}")
        self.seq += 1
        seq = str(self.seq)
        self._type(f"P {seq}")
        self.wait_report(f"D {seq}", timeout)

    def environment(self) -> dict[str, str]:
        self.seq += 1
        seq = str(self.seq)
        self._type(f"E {seq}")
        _, data = parse_hex_report(self.wait_report(f"R {seq} "))
        out = {}
        for part in data.decode("utf-8", "replace").split(";"):
            name, _, value = part.partition("=")
            out[name] = value
        return out

    def size(self) -> tuple[int, int]:
        self.seq += 1
        seq = str(self.seq)
        self._type(f"Z {seq}")
        _, data = parse_hex_report(self.wait_report(f"R {seq} "))
        cols, _, rows = data.decode().partition("x")
        return int(cols), int(rows)

    def leave(self) -> None:
        self._type("X")
