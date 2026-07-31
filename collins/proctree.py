"""Reading process state out of /proc.

Split out of `terminal.py` so it carries no GTK imports and stays testable on
CI, which has no GTK stack (see tests/conftest.py).
"""

from __future__ import annotations

import os

# A session's process tree is a handful of levels deep at most; the cap only
# stops a pathological tree (or a /proc that lies) from recursing forever.
_MAX_DEPTH = 8


def process_cwd(pid: int | None) -> str | None:
    """The working directory of *pid*, or None if it can't be read."""
    if not pid or pid <= 0:
        return None
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def process_children(pid: int) -> list[int]:
    """Direct children of *pid*, or [] when the kernel won't say."""
    try:
        with open(f"/proc/{pid}/task/{pid}/children") as fh:
            return [int(p) for p in fh.read().split()]
    except (OSError, ValueError):
        return []


def is_agent_process(pid: int, cli: str) -> bool:
    """Whether *pid* looks like the agent CLI rather than something it spawned.

    The whole command line is searched, not argv[0]: a launcher execs a
    versioned binary living under a directory named after the CLI, so the name
    shows up in the path even when the program is called something else.
    """
    if not cli:
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            cmdline = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return False
    return cli in cmdline


def agent_descendant_cwd(pid: int, cli: str, depth: int = _MAX_DEPTH) -> str | None:
    """The cwd of the deepest agent process at or below *pid*, or None when
    *pid* is not an agent process at all.

    The head of a terminal's foreground process group is not always the process
    that moves. A daemon-hosted session keeps a wrapper (`claude bg-pty-host`,
    `claude attach`) at the head of the group and runs the real agent as its
    child, and only that child follows the session into a git worktree —
    reading the leader's cwd reports the directory the session started in
    forever.

    Only processes that look like the CLI are followed, so a tool call that
    shells out and cd's somewhere else is never mistaken for the agent.
    """
    if depth <= 0 or not is_agent_process(pid, cli):
        return None
    for child in process_children(pid):
        found = agent_descendant_cwd(child, cli, depth - 1)
        if found is not None:
            return found
    return process_cwd(pid)
