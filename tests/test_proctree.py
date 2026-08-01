import os
import subprocess
import sys
import time

import pytest

from collins import proctree

pytestmark = pytest.mark.skipif(
    not os.path.isdir("/proc/self"), reason="needs a Linux /proc"
)

# A wrapper that parks in one directory and runs its child in another: the
# shape a daemon-hosted agent session has, where only the child follows the
# session into a worktree.
_CHILD = "import time  # {tag}\ntime.sleep(30)\n"
_PARENT = (
    "import subprocess, sys, time  # {tag}\n"
    "subprocess.Popen([sys.executable, '-c', {child!r}], cwd={cwd!r})\n"
    "time.sleep(30)\n"
)


def _spawn_tree(parent_dir, child_dir, parent_tag, child_tag):
    """A python parent in *parent_dir* whose python child sits in *child_dir*.
    Each process's tag lands in its command line, which is what marks it as an
    agent process."""
    child_code = _CHILD.format(tag=child_tag)
    parent_code = _PARENT.format(tag=parent_tag, child=child_code, cwd=str(child_dir))
    proc = subprocess.Popen(
        [sys.executable, "-c", parent_code], cwd=str(parent_dir)
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:  # wait for the child to actually exist
        if proctree.process_children(proc.pid):
            return proc
        time.sleep(0.05)
    proc.kill()
    proc.wait(timeout=5)
    pytest.skip("child process never appeared")


@pytest.fixture
def tree(tmp_path):
    procs = []

    def make(parent_tag, child_tag):
        parent_dir = tmp_path / "repo"
        # Deliberately not a literal ".claude/worktrees/..." path: it would put
        # the CLI's name into the parent's own command line (the child's cwd is
        # spelled out there) and every process would look like the agent.
        child_dir = tmp_path / "repo" / "worktrees" / "wt"
        child_dir.mkdir(parents=True, exist_ok=True)
        proc = _spawn_tree(parent_dir, child_dir, parent_tag, child_tag)
        procs.append(proc)
        return proc, str(parent_dir), str(child_dir)

    yield make
    for proc in procs:
        proc.kill()
        proc.wait(timeout=5)


def test_finds_agent_child_that_moved_into_a_worktree(tree):
    """The regression: the group leader stays put, the agent child moves."""
    proc, _parent_dir, child_dir = tree("claude-wrapper", "claude-agent")
    assert proctree.agent_descendant_cwd(proc.pid, "claude") == child_dir


def test_reading_the_leader_alone_reports_the_stale_directory(tree):
    """Why the descent is needed: the leader's own cwd never followed."""
    proc, parent_dir, child_dir = tree("claude-wrapper", "claude-agent")
    assert proctree.process_cwd(proc.pid) == parent_dir
    assert parent_dir != child_dir


def test_ignores_children_that_are_not_the_agent(tree):
    """A tool call that shells out and cd's elsewhere must not win."""
    proc, parent_dir, _child_dir = tree("claude-wrapper", "some-build-script")
    assert proctree.agent_descendant_cwd(proc.pid, "claude") == parent_dir


def test_returns_none_when_the_process_is_not_the_agent(tree):
    proc, _parent_dir, _child_dir = tree("unrelated-program", "also-unrelated")
    assert proctree.agent_descendant_cwd(proc.pid, "claude") is None


def test_no_cli_name_matches_nothing(tree):
    proc, _parent_dir, _child_dir = tree("claude-wrapper", "claude-agent")
    assert proctree.agent_descendant_cwd(proc.pid, "") is None


def test_depth_limit_stops_the_descent(tree):
    """At depth 0 nothing is inspected, so the agent child is never reached."""
    proc, _parent_dir, _child_dir = tree("claude-wrapper", "claude-agent")
    assert proctree.agent_descendant_cwd(proc.pid, "claude", depth=0) is None


def test_has_live_descendant_true_when_something_runs_below_the_agent(tree):
    """A build script the agent shelled out to (or left running) is a live
    child of the deepest agent process — exactly what a background job left
    running looks like."""
    proc, _parent_dir, _child_dir = tree("claude-wrapper", "some-build-script")
    assert proctree.has_live_descendant(proc.pid, "claude") is True


def test_has_live_descendant_false_with_nothing_left_running(tree):
    """The deepest agent process here is the leaf child, which has spawned
    nothing of its own."""
    proc, _parent_dir, _child_dir = tree("claude-wrapper", "claude-agent")
    assert proctree.has_live_descendant(proc.pid, "claude") is False


def test_has_live_descendant_false_when_not_the_agent(tree):
    proc, _parent_dir, _child_dir = tree("unrelated-program", "also-unrelated")
    assert proctree.has_live_descendant(proc.pid, "claude") is False


def test_missing_pids_are_not_fatal():
    assert proctree.process_cwd(None) is None
    assert proctree.process_cwd(0) is None
    assert proctree.process_cwd(-1) is None
    assert proctree.process_children(2**31 - 1) == []
    assert proctree.is_agent_process(2**31 - 1, "claude") is False
    assert proctree.agent_descendant_cwd(2**31 - 1, "claude") is None
    assert proctree.has_live_descendant(2**31 - 1, "claude") is False


def test_self_is_an_agent_process_when_the_name_is_in_the_command_line():
    # This test process runs under pytest, so match on something certainly present.
    assert proctree.is_agent_process(os.getpid(), sys.executable.split("/")[-1])
    assert not proctree.is_agent_process(os.getpid(), "definitely-not-in-argv-xyz")
