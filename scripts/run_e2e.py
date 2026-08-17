#!/usr/bin/env python3
"""Run every e2e check in scripts/check_*.py as one suite.

Each check_*.py is a self-contained end-to-end check: it stages its own
throwaway scratch tree and app id, drives real widgets (and where needed a
real VTE child behind a `claude` shim), and exits non-zero on failure. This
runner is the thin harness that turns those scripts into a suite: it
discovers them, runs them serially — each under its own private D-Bus
session bus when `dbus-run-session` is available, so a check that owns bus
names (check_status_icon.py) never collides with the user's desktop or a
previous check — enforces a per-check timeout, and reports a summary.

The checks need a display. On a dev machine, run the whole suite behind the
headless compositor wrapper so no window ever appears on screen:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \
        python3 scripts/run_e2e.py

In CI there is no compositor; Xvfb provides the display instead (see
.github/workflows/e2e.yml).

Options:
    --only SUBSTR   run only checks whose filename contains SUBSTR
                    (repeatable; a check runs if it matches any)
    --timeout SECS  per-check timeout, default 300
    --list          print the discovered checks and exit

Adding a new e2e check means dropping a scripts/check_<name>.py that exits
0 on success — discovery picks it up, no registration step.
"""

import argparse
import glob
import os
import shutil
import signal
import subprocess
import sys
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def discover(only):
    paths = sorted(glob.glob(os.path.join(SCRIPTS_DIR, "check_*.py")))
    if only:
        paths = [p for p in paths if any(s in os.path.basename(p) for s in only)]
    return paths


def run_check(path, timeout, use_dbus):
    """Run one check script; return (status, seconds) where status is
    'pass', 'fail', or 'timeout'."""
    cmd = [sys.executable, path]
    if use_dbus:
        cmd = ["dbus-run-session", "--"] + cmd
    start = time.monotonic()
    # A check spawns real children (VTEs, shims); its own process group lets
    # a timeout take the whole tree down rather than orphaning them.
    proc = subprocess.Popen(cmd, start_new_session=True)
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        return "timeout", time.monotonic() - start
    return ("pass" if code == 0 else "fail"), time.monotonic() - start


def write_github_summary(results):
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    icons = {"pass": "✅", "fail": "❌", "timeout": "⏰"}
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write("## E2E checks\n\n")
        f.write("| Check | Result | Time |\n|---|---|---|\n")
        for name, status, secs in results:
            f.write(f"| `{name}` | {icons[status]} {status} | {secs:.1f}s |\n")
        f.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", action="append", default=[], metavar="SUBSTR")
    parser.add_argument("--timeout", type=int, default=300, metavar="SECS")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    checks = discover(args.only)
    if not checks:
        print("run_e2e: no checks matched", file=sys.stderr)
        return 2
    if args.list:
        for path in checks:
            print(os.path.basename(path))
        return 0

    use_dbus = shutil.which("dbus-run-session") is not None
    if not use_dbus:
        print(
            "run_e2e: dbus-run-session not found; checks share the ambient "
            "session bus",
            file=sys.stderr,
        )

    results = []
    for i, path in enumerate(checks, 1):
        name = os.path.basename(path)
        print(f"\n=== [{i}/{len(checks)}] {name} ===", flush=True)
        status, secs = run_check(path, args.timeout, use_dbus)
        print(f"=== {name}: {status.upper()} ({secs:.1f}s) ===", flush=True)
        results.append((name, status, secs))

    print("\n=== e2e summary ===")
    for name, status, secs in results:
        print(f"  {status.upper():7}  {secs:6.1f}s  {name}")
    failed = [r for r in results if r[1] != "pass"]
    total = sum(secs for _, _, secs in results)
    print(f"  {len(results) - len(failed)}/{len(results)} passed in {total:.1f}s")
    write_github_summary(results)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
