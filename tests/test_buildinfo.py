"""Tests for buildinfo: the debug build's launch-time snapshot of the source
checkout — commit, branch, and dirtiness — shown in the About dialog."""

import shutil
import subprocess
from pathlib import Path

import pytest

from collins import buildinfo
from collins.buildinfo import BuildInfo, _read

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git isn't on PATH")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A real repository with one commit in it, and a clean tree."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "a.txt").write_text("one\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-qm", "first commit")
    return root


# -- _read -------------------------------------------------------------------


@needs_git
def test_reads_commit_branch_and_clean_tree(repo):
    info = _read(repo)
    assert info == BuildInfo(
        sha=_git(repo, "log", "-1", "--format=%h"),
        title="first commit",
        branch="main",
        dirty=False,
    )


@needs_git
def test_dirty_tree_is_noticed(repo):
    (repo / "a.txt").write_text("two\n")
    assert _read(repo).dirty is True


@needs_git
def test_detached_head_reports_no_branch(repo):
    _git(repo, "checkout", "-q", "--detach")
    info = _read(repo)
    assert info.branch is None
    assert info.sha  # the commit itself is still identified


@needs_git
def test_not_a_repository(tmp_path):
    assert _read(tmp_path) is None


# -- capture/captured ---------------------------------------------------------


def test_nothing_captured_by_default(monkeypatch):
    monkeypatch.setattr(buildinfo, "_captured", None)
    assert buildinfo.captured() is None


@needs_git
def test_capture_records_the_source_root(repo, monkeypatch):
    monkeypatch.setattr(buildinfo, "_captured", None)
    monkeypatch.setattr(buildinfo, "_SOURCE_ROOT", repo)
    buildinfo.capture()
    assert buildinfo.captured().title == "first commit"


# -- chip/describe -------------------------------------------------------------


def test_chip_is_build_metadata():
    info = BuildInfo(sha="abc1234", title="Fix the thing", branch="main", dirty=False)
    assert info.chip() == "+abc1234"
    assert BuildInfo("abc1234", "t", "main", dirty=True).chip() == "+abc1234.dirty"


def test_describe_clean_on_a_branch():
    info = BuildInfo(sha="abc1234", title="Fix the thing", branch="main", dirty=False)
    assert info.describe() == (
        "Debug Build Info:\n"
        "Commit: [abc1234] Fix the thing\n"
        "Branch: main\n"
        "\nThe worktree was clean at launch"
    )


def test_describe_dirty_and_detached():
    info = BuildInfo(sha="abc1234", title="Fix the thing", branch=None, dirty=True)
    assert info.describe() == (
        "Debug Build Info:\n"
        "Commit: [abc1234] Fix the thing\n"
        "Branch: (detached HEAD)\n"
        "\nThe worktree had uncommitted changes at launch"
    )
