# SPDX-License-Identifier: MIT
# Copyright (c) 2026 episode6, Inc.
#
# Part of the conformance kit for the Collins service API (conformance/),
# licensed MIT like the specification it tests (see conformance/LICENSE),
# unlike the rest of Collins. Standard library only.
"""``python3 -m conformance <socket path> [options]``: test a server of the API.

The kit is a client. It says hello, reads the families the server claims
in `caps`, and runs the checks of every level up to `--level` and of every
claimed family (or the ones `--family` names, less `--skip-family`). A
table of pass / fail / skip per check ends the run; the exit status is 1
when any check failed. README.md says what each level proves.
"""

from __future__ import annotations

import argparse
import os
import sys

if sys.version_info < (3, 12):
    sys.exit("the conformance kit needs Python 3.12 or newer")

from .kit import proto  # noqa: E402
from .kit.checks import families, level0, level2, sessions, terminal  # noqa: E402
from .kit.reporter import FAIL, CheckFailed, Reporter, Result  # noqa: E402
from .kit.runner import Options, Run  # noqa: E402

FAMILIES = (
    "git",
    "files",
    "prs",
    "sandbox",
    "tools",
    "jobs",
    "usage",
    "background",
    "shells",
    "composer",
    "internal",
)


def parse(argv: list[str]) -> Options:
    parser = argparse.ArgumentParser(
        prog="python3 -m conformance",
        description="Hold a server of the Collins service API to its published specification.",
    )
    parser.add_argument(
        "sock", help="the server's Unix socket (what `collins-service --print-socket` prints)"
    )
    parser.add_argument(
        "--level", type=int, default=2, choices=(0, 1, 2), help="the highest level to check (default 2)"
    )
    parser.add_argument(
        "--family",
        action="append",
        default=None,
        metavar="F",
        help="check only this family (repeatable); default: every family the server claims",
    )
    parser.add_argument(
        "--skip-family", action="append", default=[], metavar="F", help="leave this family out (repeatable)"
    )
    parser.add_argument(
        "--fake-claude",
        default=None,
        metavar="PATH",
        help="the fake claude, as a path on the server's machine (default: the kit's own)",
    )
    parser.add_argument(
        "--scratch",
        default="",
        metavar="DIR",
        help="a directory on the server's machine the kit may write in (default: a temporary one)",
    )
    parser.add_argument(
        "--replay",
        action="store_true",
        help="also replay the recorded Claude Code streams through the fake (data/streams)",
    )
    parser.add_argument(
        "--with-gh", action="store_true", help="run the `prs` checks that need the GitHub CLI"
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="TEXT",
        help="run only the groups whose name contains TEXT (repeatable)",
    )
    parser.add_argument("--json", default="", metavar="FILE", help="write the results as JSON to FILE too")
    parser.add_argument(
        "--keep-scratch", action="store_true", help="leave the temporary scratch directory behind"
    )
    parser.add_argument(
        "--vte-bytes",
        action="store_true",
        help="fail a query answered in another terminal's voice (the page's SHOULD; a note otherwise)",
    )
    parser.add_argument(
        "--spawn-budget",
        type=float,
        default=5.0,
        metavar="SECONDS",
        help="seconds the fake's box may take to take a prompt after the spawn (default 5)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    options = Options(
        sock=args.sock,
        level=args.level,
        families=set(args.family) if args.family is not None else None,
        skip_families=set(args.skip_family),
        replay=args.replay,
        with_gh=args.with_gh,
        verbose=args.verbose,
        json_out=args.json,
        only=args.only,
        keep_scratch=args.keep_scratch,
        vte_bytes=args.vte_bytes,
        spawn_budget=args.spawn_budget,
    )
    if args.fake_claude:
        options.fake_claude = os.path.abspath(args.fake_claude)
    if args.scratch:
        options.scratch = os.path.abspath(args.scratch)
    return options


def main(argv: list[str] | None = None) -> int:
    options = parse(sys.argv[1:] if argv is None else argv)
    if not os.path.exists(options.sock):
        print(f"no socket at {options.sock}", file=sys.stderr)
        return 2
    try:
        proto.load()
    except FileNotFoundError as error:
        print(error, file=sys.stderr)
        return 2
    reporter = Reporter(verbose=options.verbose)
    run = Run(options, reporter)
    print(f"conformance kit against {options.sock}", flush=True)
    try:
        if _wanted(options, "level 0"):
            _group(reporter, "level 0", level0.run_level0, run)
        else:
            _group(reporter, "level 0", level0.run_level0_hello_only, run)
        if run.hello is None:
            print("no hello: nothing more can be checked", file=sys.stderr)
        else:
            if options.level >= 1 and _wanted(options, "terminal"):
                _group(reporter, "terminal", terminal.run_terminal, run)
            if options.level >= 1 and _wanted(options, "sessions"):
                _group(reporter, "sessions", sessions.run_sessions, run)
            if options.level >= 2 and _wanted(options, "level 2"):
                _group(reporter, "level 2", level2.run_level2, run)
            if _wanted(options, "families"):
                _group(reporter, "families", families.run_families, run)
        reporter.group("the wire")
        reporter.run("every frame the server sent validated", check_violations, run)
    finally:
        run.cleanup()
    print()
    print(reporter.table())
    if options.json_out:
        with open(options.json_out, "w", encoding="utf-8") as handle:
            handle.write(reporter.to_json())
    return reporter.exit_code()


def _group(reporter: Reporter, name: str, run_group, run: Run) -> None:
    """A group whose own code fails is a failed check of that group, not the
    end of the run: the kit must be able to fail, never to crash."""
    try:
        run_group(run)
    except Exception as error:  # noqa: BLE001
        import traceback

        reporter.group(name)
        reporter.record(Result(name, "the group's own code", FAIL, f"{type(error).__name__}: {error}"))
        if reporter.verbose:
            traceback.print_exc()
        run.close_all()


def _wanted(options: Options, group: str) -> bool:
    return not options.only or any(text in group for text in options.only)


def check_violations(run: Run, notes: list[str]) -> None:
    seen = []
    for c in run.all_clients:
        seen.extend(c.violations)
    if seen:
        raise CheckFailed("; ".join(dict.fromkeys(seen)))
    notes.append(f"{sum(len(c.events) for c in run.all_clients)} events over {len(run.all_clients)} clients")


if __name__ == "__main__":
    sys.exit(main())
