# SPDX-License-Identifier: MIT
# Copyright (c) 2026 episode6, Inc.
#
# Part of the conformance kit for the Collins service API (conformance/),
# licensed MIT like the specification it tests (see conformance/LICENSE),
# unlike the rest of Collins. Standard library only.
"""The kit's results: one row per check, a table at the end, exit 1 on a fail.

A check ends `pass`, `fail` (with what was seen against what was expected)
or `skip` (with the reason: a family the server does not claim, an option
not given, a check whose precondition another check failed). A `pass` may
carry a note (a SHOULD the server did not meet, a measurement) that is
printed but does not fail the run.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field

PASS = "pass"
FAIL = "fail"
SKIP = "skip"


@dataclass
class Result:
    group: str
    name: str
    outcome: str
    detail: str = ""
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)


class CheckFailed(Exception):
    """Raised inside a check: the check fails with this detail."""


class CheckSkipped(Exception):
    """Raised inside a check: the check is skipped with this reason."""


class Reporter:
    def __init__(self, out=None, verbose: bool = False) -> None:
        self.out = out or sys.stdout
        self.verbose = verbose
        self.results: list[Result] = []
        self._group = ""

    def group(self, name: str) -> None:
        self._group = name
        if self.verbose:
            print(f"\n== {name}", file=self.out, flush=True)

    def record(self, result: Result) -> None:
        self.results.append(result)
        mark = {PASS: "ok  ", FAIL: "FAIL", SKIP: "skip"}[result.outcome]
        line = f"{mark} {result.group}: {result.name}"
        if result.outcome != PASS and result.detail:
            line += f" -- {result.detail}"
        for note in result.notes:
            line += f"\n       note: {note}"
        print(line, file=self.out, flush=True)

    def run(self, name: str, check, *args, **kwargs):
        """Run *check*, record its outcome, return what it returned (True
        when it returned nothing; None on a fail or a skip). A check raises
        `CheckFailed` or `CheckSkipped`, or returns a value; any other
        exception is a fail with its text."""
        started = time.monotonic()
        notes: list[str] = []
        kwargs["notes"] = notes
        try:
            value = check(*args, **kwargs)
        except CheckFailed as failure:
            self.record(Result(self._group, name, FAIL, str(failure), time.monotonic() - started, notes))
            return None
        except CheckSkipped as skipped:
            self.record(Result(self._group, name, SKIP, str(skipped), time.monotonic() - started, notes))
            return None
        except Exception as error:  # noqa: BLE001 - a check that blew up is a fail, not a crash
            detail = f"{type(error).__name__}: {error}"
            self.record(Result(self._group, name, FAIL, detail, time.monotonic() - started, notes))
            return None
        self.record(Result(self._group, name, PASS, "", time.monotonic() - started, notes))
        return True if value is None else value

    def skip(self, name: str, reason: str) -> None:
        self.record(Result(self._group, name, SKIP, reason))

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.outcome == FAIL)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.outcome == PASS)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.outcome == SKIP)

    def table(self) -> str:
        """The results as a text table, grouped."""
        width = max([len(f"{r.group}: {r.name}") for r in self.results] + [10])
        lines = []
        for result in self.results:
            label = f"{result.group}: {result.name}".ljust(width)
            tail = result.detail if result.outcome != PASS else ""
            lines.append(f"{result.outcome:<4} {label}  {tail}".rstrip())
        lines.append("")
        lines.append(f"{self.passed} passed, {self.failed} failed, {self.skipped} skipped")
        return "\n".join(lines)

    def summary(self) -> str:
        return f"{self.passed} passed, {self.failed} failed, {self.skipped} skipped"

    def to_json(self) -> str:
        return json.dumps(
            [
                {
                    "group": r.group,
                    "name": r.name,
                    "outcome": r.outcome,
                    "detail": r.detail,
                    "seconds": round(r.seconds, 3),
                    "notes": r.notes,
                }
                for r in self.results
            ],
            indent=1,
        )

    def exit_code(self) -> int:
        return 1 if self.failed else 0
