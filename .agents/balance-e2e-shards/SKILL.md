---
name: balance-e2e-shards
description: >-
  How to rebalance Collins' sharded e2e CI job: refresh the per-check
  timing table (CHECK_SECONDS in scripts/run_e2e.py) from a CI run with the
  bundled refresh_weights.py, read the deal it prints, and change the shard
  count (ci.yml matrix + the --shard N/4 string + the docs that say "four").
  Use when an e2e-shard job runs noticeably longer than its siblings, when a
  check was added, removed, renamed or got much slower, when the user asks to
  rebalance / re-weight / re-shard the e2e tests or to add or remove shards,
  or when tests/test_run_e2e.py fails on a stale CHECK_SECONDS name.
---

# Balancing the e2e shards

CI runs `scripts/run_e2e.py` as four matrix legs, `e2e-shard (1..4)`, each
given `--shard N/4`. The runner deals every `scripts/check_*.py` to a shard
by weight: `CHECK_SECONDS` (a dict in `run_e2e.py`) holds each check's
seconds from one CI run, `shard()` walks the checks heaviest-first and hands
each to the shard with the least time so far. Balance is only as good as
the table. A check the table lacks weighs `DEFAULT_SECONDS`; a table entry
whose check is gone fails `tests/test_run_e2e.py`.

The legs fan into a bare `e2e` job because the main-branch ruleset requires
a check by that name. Never rename or drop it.

## Refresh the weights (a shard drifted, a check changed)

From the repo root of the worktree you are editing:

```bash
python3 .agents/balance-e2e-shards/scripts/refresh_weights.py --dry-run   # look first
python3 .agents/balance-e2e-shards/scripts/refresh_weights.py             # then write
```

By default it takes the latest green `ci.yml` run on main; `--run <id>` names
another (a PR run whose shards you are staring at, say — the id is in the
job URLs `gh pr checks` prints). It reads every `e2e` / `e2e-shard (N)` job
log through `gh`, rewrites the table sorted heaviest-first with the run id
and date in the comment above it, and prints the deal before and after:

```
deal with run 34134677209's table, 4 shards:
  shard 1/4:   58.0s  (7 checks)
  …
  spread: 0.4s
```

Read the spread. LPT cannot land closer than the heaviest single check's
share of the tail, so a spread under ~10 s is as good as it gets; wider
means the table was stale. Then:

1. `python3 -m pytest tests/test_run_e2e.py -q` (stale names, balance).
2. `git diff scripts/run_e2e.py` — sanity-read the numbers. A check that
   jumped by a lot is either a real slowdown worth a look or a flaky first
   attempt (the script counts only the passing attempt, but a slow pass is
   still a slow pass).
3. Commit the table alone or with the change that moved it. `run_e2e.py` is
   fork-new: no GPL notice to bump.

What the script keeps, drops and cannot see:

- A check in the repo the run never ran (added after it) keeps its old
  weight, else `DEFAULT_SECONDS`. Refresh again after the check's first
  green run to give it a real number.
- A check the run timed but the repo no longer has is dropped.
- A check that failed or timed out in the run keeps its old weight.
- Job wall time is more than check time: every leg also pays checkout and
  the image pull (~25 s). Compare legs by the step-summary tables, not by
  the job clock.

Refreshing by hand is the same recipe: `gh run view --job <id> --log`, the
`=== check_x.py: PASS (8.2s) ===` lines, into the dict. The step summary of
each shard (`E2E checks (shard N/4)`) shows the same numbers.

## Change the shard count

The count lives in more places than the matrix. For N shards:

- `.github/workflows/ci.yml`: the `shard:` matrix list and the
  `--shard ${{ matrix.shard }}/4` denominator, and the `e2e-shard` job
  comment's worst-case arithmetic (checks per shard × 2 × 120 s must stay
  well under `timeout-minutes`).
- `scripts/run_e2e.py`'s docstring says "four"; `AGENTS.md`, `.agents/collins-testing/SKILL.md`
  and `.agents/collins-packaging-and-ci/SKILL.md` say `(1..4)` / `N/4`.
- Nothing in the runner or the tests hard-codes 4 beyond the docstring;
  `tests/test_run_e2e.py` deals at 4 but only asserts balance, so it holds
  at any count.

Preview the deal at the new count before touching anything:

```bash
python3 .agents/balance-e2e-shards/scripts/refresh_weights.py --dry-run --shards 6
```

More shards buy less than they look: each leg pays the same ~25 s of setup,
and the heaviest check (~30 s) is a floor no split gets under. Four legs
already sit near that floor; six would each spend as long on setup as on
checks.

## When a leg is slow but the table is fresh

That is not a balance problem. Look at the leg's step summary for one check
with a large time — a retry doubles a check's time (`⚠️ flaky`), a hang
costs the full `--timeout` twice. Fix the check; the `collins-testing`
skill lists the CI-only traps (idle starvation, a dialog on top, clipboard
timing) that make a check flaky under Xvfb and not on a desk.
