"""Reading process state out of /proc.

Split out of `terminal.py` so it carries no GTK imports and stays testable on
CI, which has no GTK stack (see tests/conftest.py).
"""

from __future__ import annotations

import os
from collections.abc import Collection

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


def process_ppid(pid: int) -> int | None:
    """The parent pid of *pid*, or None when it can't be read.

    Parsed from /proc/<pid>/status rather than /proc/<pid>/stat: the stat
    line embeds the command name in parentheses — a name that may itself
    contain spaces and parentheses — so field-splitting stat is a hazard
    the status file doesn't have.
    """
    try:
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                if line.startswith("PPid:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


# The upward walk gets a looser cap than _MAX_DEPTH: an MCP shim's chain up
# to the app crosses claude, its wrappers, the tab's shell and Collins
# itself, and how deep that stack runs isn't ours to bound.
_MAX_ANCESTORS = 32


def ancestor_pids(pid: int, limit: int = _MAX_ANCESTORS) -> set[int]:
    """*pid* plus every ancestor of it, following parent links toward init.

    Membership tests against this set answer "was this process launched
    from under that one?" — how a tool call arriving from an MCP shim is
    traced back to the terminal tab whose shell spawned its `claude`. The
    walk stops at pid 1, at an unreadable /proc entry (the set still holds
    what was collected), or after *limit* steps, so a /proc that lies can't
    loop forever.
    """
    seen: set[int] = set()
    while pid > 1 and pid not in seen and len(seen) < limit:
        seen.add(pid)
        parent = process_ppid(pid)
        if parent is None:
            break
        pid = parent
    return seen


def is_agent_process(pid: int, cli: str) -> bool:
    """Whether *pid* looks like the agent CLI rather than something it spawned.

    The whole command line is searched, not argv[0]: a launcher execs a
    versioned binary living under a directory named after the CLI, so the name
    shows up in the path even when the program is called something else.
    """
    if not cli:
        return False
    cmdline = process_cmdline(pid)
    return cmdline is not None and cli in cmdline


def process_cmdline(pid: int) -> str | None:
    """The full command line of *pid* as one space-joined string, or None
    when it can't be read.

    An *empty* cmdline also reads as None: that is what a zombie — exited,
    not yet reaped — reports, and a process that no longer runs is not
    evidence of anything happening.
    """
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    text = raw.rstrip(b"\0").replace(b"\0", b" ").decode("utf-8", errors="replace")
    return text or None


def _deepest_agent_pid(pid: int, cli: str, depth: int = _MAX_DEPTH) -> int | None:
    """The pid of the deepest agent process at or below *pid*, or None when
    *pid* is not an agent process at all.

    The head of a terminal's foreground process group is not always the process
    that moves. A daemon-hosted session keeps a wrapper (`claude bg-pty-host`,
    `claude attach`) at the head of the group and runs the real agent as its
    child, and only that child follows the session into a git worktree —
    reading the leader's own state reports what the session started with
    forever.

    Only processes that look like the CLI are followed, so a tool call that
    shells out and cd's somewhere else, or spawns something of its own, is
    never mistaken for the agent.
    """
    if depth <= 0 or not is_agent_process(pid, cli):
        return None
    for child in process_children(pid):
        found = _deepest_agent_pid(child, cli, depth - 1)
        if found is not None:
            return found
    return pid


def agent_descendant_cwd(pid: int, cli: str, depth: int = _MAX_DEPTH) -> str | None:
    """The cwd of the deepest agent process at or below *pid*, or None when
    *pid* is not an agent process at all. See `_deepest_agent_pid`."""
    agent_pid = _deepest_agent_pid(pid, cli, depth)
    return process_cwd(agent_pid) if agent_pid is not None else None


def descendant_cmdlines(pid: int, cli: str) -> set[str]:
    """The cmdlines of everything running directly below the agent process at
    or below *pid* — empty when *pid* is not an agent process, or when the
    agent has spawned nothing. Zombies (whose cmdline reads empty) are not
    running and don't appear.

    This is both halves of the baseline scheme in one shape: sampled on a
    freshly spawned tab it lists the agent's own plumbing (its MCP servers —
    the baseline), and sampled later it lists plumbing plus whatever work is
    in flight, ready to be compared against that baseline.
    """
    agent_pid = _deepest_agent_pid(pid, cli)
    if agent_pid is None:
        return set()
    cmdlines = set()
    for child in process_children(agent_pid):
        cmdline = process_cmdline(child)
        if cmdline is not None:
            cmdlines.add(cmdline)
    return cmdlines


def has_live_descendant(pid: int, cli: str, ignore: Collection[str] = frozenset()) -> bool:
    """Whether the agent process at or below *pid* has spawned anything still
    running below it right now — anything beyond *ignore*, that is.

    A tool call in flight looks exactly like a background job the agent
    started and left running (a dev server, a long build): both are a live
    child of the agent process. Used as a "the session is still working"
    signal alongside terminal and transcript output — see `activity.py`.

    Not every child is work, though: the CLI spawns its stdio MCP servers at
    startup and keeps them for its whole life, so "has any child at all"
    would read as busy forever on a session with MCP configured. *ignore*
    carries the cmdlines of that known plumbing — the session's captured
    baseline plus the servers Collins itself configures — and children
    matching it don't count.
    """
    return any(cmdline not in ignore for cmdline in descendant_cmdlines(pid, cli))
