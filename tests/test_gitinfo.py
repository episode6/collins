"""Tests for gitinfo.current_branch — pure filesystem parsing, no git needed."""

from pathlib import Path

from collins.gitinfo import current_branch


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
