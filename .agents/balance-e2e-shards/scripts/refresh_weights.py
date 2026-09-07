#!/usr/bin/env python3
"""Refresh scripts/run_e2e.py's CHECK_SECONDS table from a CI run.

Reads the e2e job logs of one ci.yml run through `gh`, takes each check's
wall time off the runner's summary lines, and rewrites the table (and the
comment above it naming the run and date) in place. Then prints the deal
the new table produces, so a lopsided shard is visible before pushing.

    refresh_weights.py                 # latest green run of ci.yml on main
    refresh_weights.py --run 34134677209
    refresh_weights.py --dry-run       # print, touch nothing
    refresh_weights.py --shards 6      # preview the deal at another count

A flaky check counts its passing attempt only (the summary line sums both
attempts; the first attempt's time is on the "retrying" line). A failed or
timed-out check keeps its old weight. A check in the run that the repo no
longer has is dropped; a check in the repo the run never ran (new since
the run) keeps its old weight, or DEFAULT_SECONDS if it has none.

Run from the repo root of the worktree whose table you are refreshing.
"""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys

REPO = "episode6/collins"
FINAL_RE = re.compile(r"=== (check_\w+\.py): (PASS|FLAKY|FAIL|TIMEOUT) \(([\d.]+)s\) ===")
FIRST_RE = re.compile(r"=== (check_\w+\.py): (FAIL|TIMEOUT) \(([\d.]+)s\), retrying ===")
# Line-bounded on purpose: a DOTALL `.*` inside a repeated group backtracks
# exponentially over a file this size.
TABLE_RE = re.compile(
    r"# Seconds per check on CI's runner[^\n]*\n(?:#[^\n]*\n)*"
    r"CHECK_SECONDS = \{\n(?:    [^\n]*\n)*\}\n"
)


def gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def latest_green_run():
    runs = json.loads(gh(
        "run", "list", "--repo", REPO, "--workflow", "ci.yml", "--branch", "main",
        "--status", "success", "--limit", "1", "--json", "databaseId",
    ))
    if not runs:
        sys.exit("no successful ci.yml run on main")
    return runs[0]["databaseId"]


def e2e_jobs(run_id):
    run = json.loads(gh(
        "run", "view", str(run_id), "--repo", REPO, "--json", "jobs,createdAt,headBranch"))
    jobs = [j for j in run["jobs"] if re.fullmatch(r"e2e(-shard \(\d+\))?", j["name"])]
    if not jobs:
        sys.exit(f"run {run_id} has no e2e jobs")
    return run["createdAt"][:10], run["headBranch"], jobs


def timings_from_log(text):
    """{check: seconds} for the checks the log reports as pass or flaky."""
    first = {m.group(1): float(m.group(3)) for m in FIRST_RE.finditer(text)}
    out = {}
    for name, status, secs in FINAL_RE.findall(text):
        secs = float(secs)
        if status == "PASS":
            out[name] = secs
        elif status == "FLAKY":
            out[name] = round(secs - first.get(name, 0.0), 1)
    return out


def load_runner(path):
    spec = importlib.util.spec_from_file_location("run_e2e", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def render_table(weights, run_id, branch, date):
    lines = [
        "# Seconds per check on CI's runner (ubuntu-latest, Xvfb), from the e2e job",
        f"# of run {run_id} ({branch}, {date}). Weights only — being off by a few",
        "# seconds costs balance, never correctness. Refresh with the",
        "# balance-e2e-shards skill when a check grows or a shard drifts.",
        "CHECK_SECONDS = {",
    ]
    for name, secs in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f'    "{name}": {secs:.1f},')
    lines.append("}")
    return "\n".join(lines) + "\n"


def print_deal(runner, weights, shards):
    checks = runner.discover(only=[])
    runner.CHECK_SECONDS = weights
    loads = []
    for i in range(1, shards + 1):
        part = runner.shard(checks, i, shards)
        loads.append(sum(map(runner.check_seconds, part)))
        print(f"  shard {i}/{shards}: {loads[-1]:6.1f}s  ({len(part)} checks)")
    print(f"  spread: {max(loads) - min(loads):.1f}s")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=int, help="ci.yml run id (default: latest green on main)")
    parser.add_argument("--shards", type=int, default=4, help="shard count to preview the deal at")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--runner", default="scripts/run_e2e.py")
    args = parser.parse_args()

    run_id = args.run or latest_green_run()
    date, branch, jobs = e2e_jobs(run_id)
    measured = {}
    for job in jobs:
        log = gh("run", "view", "--repo", REPO, "--job", str(job["databaseId"]), "--log")
        got = timings_from_log(log)
        print(f"{job['name']}: {len(got)} timed checks")
        measured.update(got)

    runner = load_runner(args.runner)
    present = {os.path.basename(p) for p in runner.discover(only=[])}
    old = dict(runner.CHECK_SECONDS)
    new = {}
    for name in sorted(present):
        new[name] = measured.get(name, old.get(name, runner.DEFAULT_SECONDS))
    for name in sorted(present - set(measured)):
        print(f"  not in run {run_id}, kept: {name} = {new[name]:.1f}s")
    for name in sorted(set(old) - present):
        print(f"  gone from the repo, dropped: {name}")

    print(f"\ndeal with the old table, {args.shards} shards:")
    print_deal(runner, old, args.shards)
    print(f"deal with run {run_id}'s table, {args.shards} shards:")
    print_deal(runner, new, args.shards)

    if args.dry_run:
        print("\n(dry run, table not written)")
        return 0
    src = open(args.runner, encoding="utf-8").read()
    if not TABLE_RE.search(src):
        sys.exit(f"{args.runner}: could not find the CHECK_SECONDS table")
    src = TABLE_RE.sub(lambda _m: render_table(new, run_id, branch, date), src, count=1)
    open(args.runner, "w", encoding="utf-8").write(src)
    print(f"\nwrote {len(new)} weights to {args.runner}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
