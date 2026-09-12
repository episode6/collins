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

A check that fails (or times out) gets exactly one retry; a pass on the
retry is reported as "flaky" and does not fail the suite, so a rare one-off
flake doesn't block a PR while still staying visible in the summary.

The checks need a display. On a dev machine, run the whole suite behind the
headless compositor wrapper so no window ever appears on screen:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \
        python3 scripts/run_e2e.py

In CI there is no compositor; Xvfb provides the display instead (see
the e2e job in .github/workflows/ci.yml).

Options:
    --only SUBSTR   run only checks whose filename contains SUBSTR
                    (repeatable; a check runs if it matches any)
    --shard I/N     run only the I-th of N time-balanced shards (1-based);
                    CI runs the suite as five of these in parallel
    --timeout SECS  per-check timeout, default 300
    --list          print the discovered checks and exit

Sharding is by measured wall time, not by count: CHECK_SECONDS below holds
each check's seconds from a CI run, and `shard()` deals the checks out
longest-first, each to the shard with the least time so far (LPT), so the
five shards finish together instead of one dragging a 30 s check behind
eight 3 s ones. A check missing from the table (new, or renamed) is dealt
in at DEFAULT_SECONDS — it still runs, in exactly one shard — and gets a
real weight the next time the table is refreshed from a run's logs (the
balance-e2e-shards skill's refresh_weights.py does that and previews the
deal; `--list --shard` here shows one shard's estimate).

Adding a new e2e check means dropping a scripts/check_<name>.py that exits
0 on success — discovery picks it up, no registration step. A check that
can't run where it finds itself exits 77 (SKIP_EXIT) with a printed reason:
it is reported as skipped, never retried, and never fails the suite.
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

# Seconds per check on CI's runner (ubuntu-latest, Xvfb), from the e2e job
# of run 34133941442 (main, 2026-09-07). Weights only — being off by a few
# seconds costs balance, never correctness. Refresh with the
# balance-e2e-shards skill when a check grows or a shard drifts.
CHECK_SECONDS = {
    "check_archive_worktree.py": 28.4,
    "check_composer_paste_back.py": 17.1,
    "check_new_chat.py": 16.9,
    "check_welcome.py": 16.5,
    "check_sandbox_policy.py": 16.5,
    "check_terminal_tools.py": 14.1,
    "check_start_session.py": 12.6,
    "check_pr_refresh_on_finish.py": 11.6,
    "check_notifications.py": 9.9,
    "check_git_page.py": 9.4,
    "check_show_diff.py": 8.0,
    "check_background_session.py": 7.6,
    "check_hide_on_close.py": 7.1,
    "check_worktree_fallback.py": 7.1,
    "check_pr_body_blocks.py": 6.7,
    "check_git_prefs.py": 6.3,
    "check_composer_paste.py": 6.2,
    "check_composer_spell_click.py": 5.9,
    "check_token_use_prefs.py": 5.0,
    "check_auto_delete.py": 4.9,
    "check_icon_dialog_none.py": 4.0,
    "check_panel_resize_save.py": 3.9,
    "check_composer_draft.py": 3.7,
    "check_pr_page_focus.py": 3.2,
    "check_pr_page_patch.py": 3.2,
    "check_status_icon.py": 3.1,
    "check_project_row_click.py": 3.0,
    "check_root_name_links.py": 2.8,
    "check_panel_bg_tab_width.py": 1.8,
    "check_editor_narrow.py": 1.2,
    "check_notify_badge.py": 1.0,
    # Skips in CI's container (no user namespace for bubblewrap), so its
    # weight is what a skip costs; a machine that can build a box spends
    # about five seconds more.
    "check_sandbox_launch.py": 1.0,
    "check_panel_layout.py": 0.7,
    "check_tab_drag.py": 0.5,
    "check_composer_spelling_optional.py": 0.4,
}
# A check the table doesn't know: about the median, so a new check neither
# vanishes into the busiest shard nor tips the lightest one over.
DEFAULT_SECONDS = 6.0


def check_seconds(path):
    return CHECK_SECONDS.get(os.path.basename(path), DEFAULT_SECONDS)


def shard(paths, index, count):
    """The paths that shard `index` (1-based) of `count` runs, in the
    order discover() gave them.

    Longest-processing-time-first: walk the checks heaviest first and hand
    each to the shard with the least time so far. Equal weights go by
    name and ties between shards go to the lower number, so every runner
    computes the same deal from the same set of files — a shard never
    needs to know what the others were given.
    """
    if not 1 <= index <= count:
        raise ValueError(f"shard {index}/{count} is out of range")
    loads = [0.0] * count
    mine = set()
    heaviest_first = sorted(
        paths, key=lambda p: (-check_seconds(p), os.path.basename(p)))
    for path in heaviest_first:
        lightest = loads.index(min(loads))
        loads[lightest] += check_seconds(path)
        if lightest == index - 1:
            mine.add(path)
    return [p for p in paths if p in mine]


def parse_shard(text):
    try:
        index, count = (int(part) for part in text.split("/"))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected I/N, e.g. 2/5, got {text!r}") from None
    if count < 1 or not 1 <= index <= count:
        raise argparse.ArgumentTypeError(f"shard {text} is out of range")
    return index, count


def discover(only):
    paths = sorted(glob.glob(os.path.join(SCRIPTS_DIR, "check_*.py")))
    if only:
        paths = [p for p in paths if any(s in os.path.basename(p) for s in only)]
    return paths


# A check that exits with this says it could not run here and did not fail:
# the autotools convention. check_sandbox_launch.py uses it where the machine
# can't give bubblewrap a user namespace (a CI container may not). A skip is
# never retried and never fails the run, but it is printed as what it is
# rather than counted as a pass.
SKIP_EXIT = 77


def run_check(path, timeout, use_dbus):
    """Run one check script; return (status, seconds) where status is
    'pass', 'skip', 'fail', or 'timeout'."""
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
    if code == SKIP_EXIT:
        return "skip", time.monotonic() - start
    return ("pass" if code == 0 else "fail"), time.monotonic() - start


def write_github_summary(results, shard_spec=None):
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    icons = {"pass": "✅", "flaky": "⚠️", "skip": "⏭️", "fail": "❌", "timeout": "⏰"}
    with open(summary_path, "a", encoding="utf-8") as f:
        title = "## E2E checks"
        if shard_spec:
            title += f" (shard {shard_spec[0]}/{shard_spec[1]})"
        f.write(f"{title}\n\n")
        f.write("| Check | Result | Time |\n|---|---|---|\n")
        for name, status, secs in results:
            f.write(f"| `{name}` | {icons[status]} {status} | {secs:.1f}s |\n")
        f.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", action="append", default=[], metavar="SUBSTR")
    parser.add_argument("--shard", type=parse_shard, default=None, metavar="I/N")
    parser.add_argument("--timeout", type=int, default=300, metavar="SECS")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    checks = discover(args.only)
    if args.shard:
        checks = shard(checks, *args.shard)
    if not checks:
        print("run_e2e: no checks matched", file=sys.stderr)
        return 2
    if args.list:
        for path in checks:
            print(f"{check_seconds(path):6.1f}s  {os.path.basename(path)}")
        print(f"{sum(map(check_seconds, checks)):6.1f}s  total (estimated)")
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
        if status not in ("pass", "skip"):
            print(f"=== {name}: {status.upper()} ({secs:.1f}s), retrying ===",
                  flush=True)
            status2, secs2 = run_check(path, args.timeout, use_dbus)
            secs += secs2
            status = "flaky" if status2 == "pass" else status2
        print(f"=== {name}: {status.upper()} ({secs:.1f}s) ===", flush=True)
        results.append((name, status, secs))

    print("\n=== e2e summary ===")
    for name, status, secs in results:
        print(f"  {status.upper():7}  {secs:6.1f}s  {name}")
    failed = [r for r in results if r[1] not in ("pass", "flaky", "skip")]
    skipped = [r for r in results if r[1] == "skip"]
    total = sum(secs for _, _, secs in results)
    tail = f", {len(skipped)} skipped" if skipped else ""
    ran = len(results) - len(failed) - len(skipped)
    print(f"  {ran}/{len(results)} passed{tail} in {total:.1f}s")
    write_github_summary(results, args.shard)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
