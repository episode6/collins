"""Tests for gitinfo: current_branch, which is pure filesystem parsing and
needs no git, and has_changes/ignored_names, the calls that shell out to it."""

import shutil
import subprocess
from pathlib import Path

import pytest

from collins.gitinfo import current_branch, has_changes, ignored_names


def make_repo(root: Path, head: str = "ref: refs/heads/main\n") -> Path:
    git_dir = root / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "HEAD").write_text(head)
    return root


def test_branch_at_repo_root(tmp_path):
    repo = make_repo(tmp_path / "repo")
    assert current_branch(repo) == "main"


def test_branch_from_subdirectory(tmp_path):
    repo = make_repo(tmp_path / "repo")
    sub = repo / "a" / "b"
    sub.mkdir(parents=True)
    assert current_branch(sub) == "main"


def test_branch_name_with_slashes(tmp_path):
    repo = make_repo(tmp_path / "repo", head="ref: refs/heads/feature/fix-thing\n")
    assert current_branch(repo) == "feature/fix-thing"


def test_not_a_repo(tmp_path):
    assert current_branch(tmp_path) is None


def test_missing_directory(tmp_path):
    assert current_branch(tmp_path / "gone") is None


def test_empty_cwd():
    assert current_branch(None) is None
    assert current_branch("") is None


def test_detached_head_shows_abbreviated_hash(tmp_path):
    repo = make_repo(tmp_path / "repo", head="0123456789abcdef0123456789abcdef01234567\n")
    assert current_branch(repo) == "01234567"


def test_missing_head_file(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    assert current_branch(tmp_path / "repo") is None


def test_worktree_pointer_file_absolute(tmp_path):
    main = make_repo(tmp_path / "main")
    wt_git_dir = main / ".git" / "worktrees" / "wt"
    wt_git_dir.mkdir(parents=True)
    (wt_git_dir / "HEAD").write_text("ref: refs/heads/wt-branch\n")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {wt_git_dir}\n")
    assert current_branch(worktree) == "wt-branch"


def test_worktree_pointer_file_relative(tmp_path):
    main = make_repo(tmp_path / "main")
    wt_git_dir = main / ".git" / "worktrees" / "wt"
    wt_git_dir.mkdir(parents=True)
    (wt_git_dir / "HEAD").write_text("ref: refs/heads/wt-branch\n")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: ../main/.git/worktrees/wt\n")
    assert current_branch(worktree) == "wt-branch"


def test_broken_pointer_file(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text("not a gitdir line\n")
    assert current_branch(worktree) is None


# -- has_changes ------------------------------------------------------------

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git isn't on PATH")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A real repository with one commit in it, and a clean tree."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "a.txt").write_text("one\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-qm", "first")
    return root


@needs_git
def test_a_committed_tree_has_no_changes(repo):
    assert has_changes(repo) is False


@needs_git
def test_an_edited_file_counts(repo):
    (repo / "a.txt").write_text("two\n")
    assert has_changes(repo) is True


@needs_git
def test_a_staged_file_counts(repo):
    (repo / "b.txt").write_text("new\n")
    _git(repo, "add", "b.txt")
    assert has_changes(repo) is True


@needs_git
def test_an_untracked_file_counts(repo):
    """It would go into the pull request too, so it counts as work to open one
    for."""
    (repo / "b.txt").write_text("new\n")
    assert has_changes(repo) is True


@needs_git
def test_an_ignored_file_does_not(repo):
    """git leaves it out and so would the pull request."""
    (repo / ".gitignore").write_text("junk\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-qm", "ignore junk")
    (repo / "junk").write_text("noise\n")
    assert has_changes(repo) is False


@needs_git
def test_a_subdirectory_answers_for_its_repo(repo):
    sub = repo / "a" / "b"
    sub.mkdir(parents=True)
    (sub / "c.txt").write_text("deep\n")
    assert has_changes(sub) is True


@needs_git
def test_somewhere_that_isnt_a_repository(tmp_path):
    assert has_changes(tmp_path) is False


def test_nowhere_at_all(tmp_path):
    assert has_changes(None) is False
    assert has_changes("") is False
    assert has_changes(tmp_path / "gone") is False


def test_no_git_on_path(repo, monkeypatch):
    """Nothing to ask, so nothing is claimed."""
    monkeypatch.setattr("collins.gitinfo.shutil.which", lambda _name: None)
    assert has_changes(repo) is False


def test_a_git_that_never_answers(repo, monkeypatch):
    """A repository slower than the timeout is treated as clean rather than
    holding up the menu built on the answer."""

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("git", 2.0)

    monkeypatch.setattr("collins.gitinfo.subprocess.run", timeout)
    assert has_changes(repo) is False


# -- ignored_names ------------------------------------------------------------


@needs_git
def test_ignored_entries_are_reported(repo):
    (repo / ".gitignore").write_text("junk\nbuild/\n")
    (repo / "junk").write_text("noise\n")
    (repo / "build").mkdir()
    assert ignored_names(repo, ["a.txt", "junk", "build", ".gitignore"]) == {"junk", "build"}


@needs_git
def test_ignores_apply_in_subdirectories(repo):
    (repo / ".gitignore").write_text("*.log\n")
    sub = repo / "sub"
    sub.mkdir()
    (sub / "x.log").write_text("noise\n")
    (sub / "x.txt").write_text("real\n")
    assert ignored_names(sub, ["x.log", "x.txt"]) == {"x.log"}


@needs_git
def test_nothing_ignored(repo):
    """check-ignore exits 1 here, which is an answer, not a failure."""
    assert ignored_names(repo, ["a.txt"]) == set()


@needs_git
def test_ignored_name_with_spaces(repo):
    """-z framing keeps awkward filenames intact both ways."""
    (repo / ".gitignore").write_text("with space\n")
    assert ignored_names(repo, ["with space", "a.txt"]) == {"with space"}


@needs_git
def test_not_a_repository_means_nothing_ignored(tmp_path):
    (tmp_path / "junk").write_text("noise\n")
    assert ignored_names(tmp_path, ["junk"]) == set()


def test_no_names_asks_nothing(repo):
    assert ignored_names(repo, []) == set()
    assert ignored_names(None, ["junk"]) == set()


def test_ignored_names_without_git(repo, monkeypatch):
    monkeypatch.setattr("collins.gitinfo.shutil.which", lambda _name: None)
    assert ignored_names(repo, ["a.txt"]) == set()
