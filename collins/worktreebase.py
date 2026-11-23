"""A new session worktree branches from the remote's default branch, as
it is now.

The CLI cuts a `-w` worktree itself, on the base its `worktree.baseRef`
setting names: "fresh" (its default) is `origin/HEAD`'s branch, "head"
the checkout's local HEAD. "fresh" is best-effort: the CLI fetches only
when the repository hasn't been fetched in a day, gives up after five
seconds and takes the cached ref, falls back to local HEAD when
`origin/HEAD` is unset, and inside a sandbox can't write the read-only
checkout's `.git` at all. So Collins does two things for every new
worktree launch:

* the command carries `--settings` with `worktree.baseRef` "fresh"
  (`BASE_REF_SETTINGS`), which outranks a user's or a project's "head";
* before the command is typed, the service fetches the default branch on
  the host (`freshen`), so the ref the CLI reads is the remote's now and
  its own fetch has nothing left to do.

`freshen` fails soft: no repository, no `origin`, a fetch that fails or
outruns its bound each return a line to show, and the launch goes ahead
on whatever ref is there. GTK-free; runs git, so call it off the main
loop.
"""

from __future__ import annotations

import json
import os
import subprocess

# The CLI's own settings key for a `-w` worktree's base, pinned to the
# remote's default branch for every launch Collins types.
BASE_REF_SETTINGS = json.dumps({"worktree": {"baseRef": "fresh"}}, separators=(",", ":"))

# The remote the CLI bases a "fresh" worktree on.
REMOTE = "origin"

# Bounds on each git run (seconds): a launch waits on them.
LOCAL_TIMEOUT_S = 5
NETWORK_TIMEOUT_S = 20

# A fetch that has no terminal to ask on must not wait for one.
_ENV_OVERRIDES = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}


def _git(cwd: str, *args: str, timeout: float) -> subprocess.CompletedProcess | None:
    env = dict(os.environ)
    env.update(_ENV_OVERRIDES)
    try:
        return subprocess.run(
            ["git", "-C", cwd, *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return None
    except OSError:
        return subprocess.CompletedProcess(args, 127, "", "git is not installed")


def _last_line(proc: subprocess.CompletedProcess | None) -> str:
    if proc is None:
        return "timed out"
    lines = [line.strip() for line in (proc.stderr or "").splitlines() if line.strip()]
    return (lines[-1] if lines else f"exit status {proc.returncode}")[:200]


def default_branch(cwd: str, *, timeout: float = LOCAL_TIMEOUT_S) -> str | None:
    """The branch `origin/HEAD` points at (`main`), or None when unset."""
    proc = _git(
        cwd, "symbolic-ref", "--quiet", "--short", f"refs/remotes/{REMOTE}/HEAD", timeout=timeout
    )
    if proc is None or proc.returncode != 0:
        return None
    ref = proc.stdout.strip()
    prefix = f"{REMOTE}/"
    if not ref.startswith(prefix) or len(ref) == len(prefix):
        return None
    return ref[len(prefix):]


def freshen(cwd: str, *, network_timeout: float = NETWORK_TIMEOUT_S) -> tuple[str, str]:
    """Fetch the remote's default branch for the repository *cwd* is in.

    Returns (outcome, detail): ``("fetched", "origin/main")``; ``("skipped",
    "")`` where there is nothing to do (not a repository, no `origin`);
    ``("failed", why)`` where the ref may be stale. An unset `origin/HEAD`
    is asked of the remote first (`git remote set-head origin --auto`), as
    without it the CLI would branch from local HEAD."""
    inside = _git(cwd, "rev-parse", "--is-inside-work-tree", timeout=LOCAL_TIMEOUT_S)
    if inside is None or inside.returncode != 0 or inside.stdout.strip() != "true":
        return "skipped", ""
    remote = _git(cwd, "remote", "get-url", REMOTE, timeout=LOCAL_TIMEOUT_S)
    if remote is None or remote.returncode != 0:
        return "skipped", ""
    branch = default_branch(cwd)
    if branch is None:
        proc = _git(cwd, "remote", "set-head", REMOTE, "--auto", timeout=network_timeout)
        if proc is None or proc.returncode != 0:
            return "failed", _last_line(proc)
        branch = default_branch(cwd)
        if branch is None:
            return "failed", f"{REMOTE} has no default branch"
    proc = _git(
        cwd, "fetch", "--quiet", "--no-tags", REMOTE,
        f"+refs/heads/{branch}:refs/remotes/{REMOTE}/{branch}",
        timeout=network_timeout,
    )
    if proc is None or proc.returncode != 0:
        return "failed", _last_line(proc)
    return "fetched", f"{REMOTE}/{branch}"
