"""Tests for gitinfo: current_branch, default_branch and github_url, which are
pure filesystem parsing and need no git, and has_changes/ignored_names, the calls that shell
out to it."""

import shutil
import subprocess
from pathlib import Path

import pytest

from collins.gitinfo import current_branch, default_branch, github_url, has_changes, ignored_names


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


# -- github_url ---------------------------------------------------------------


def with_remotes(root: Path, config: str) -> Path:
    """A repository whose config holds *config* — the remote stanzas under
    test, indented the way git writes them."""
    repo = make_repo(root)
    (repo / ".git" / "config").write_text(config)
    return repo


ORIGIN = '[remote "origin"]\n\turl = {url}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n'


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/episode6/collins.git",
        "https://github.com/episode6/collins",
        "https://ghackett@github.com/episode6/collins.git",
        "http://github.com/episode6/collins",
        "https://www.github.com/episode6/collins",
        "git@github.com:episode6/collins.git",
        "git@github.com:episode6/collins",
        "ssh://git@github.com/episode6/collins.git",
        "ssh://git@github.com:22/episode6/collins.git",
        "git://github.com/episode6/collins.git",
    ],
)
def test_every_way_a_github_remote_is_written(tmp_path, url):
    repo = with_remotes(tmp_path / "repo", ORIGIN.format(url=url))
    assert github_url(repo) == "https://github.com/episode6/collins"


def test_from_a_subdirectory(tmp_path):
    repo = with_remotes(tmp_path / "repo", ORIGIN.format(url="git@github.com:e6/c.git"))
    sub = repo / "a" / "b"
    sub.mkdir(parents=True)
    assert github_url(sub) == "https://github.com/e6/c"


def test_origin_wins_over_other_remotes(tmp_path):
    config = (
        '[remote "fork"]\n\turl = git@github.com:someone/collins.git\n'
        + ORIGIN.format(url="git@github.com:episode6/collins.git")
    )
    repo = with_remotes(tmp_path / "repo", config)
    assert github_url(repo) == "https://github.com/episode6/collins"


def test_upstream_when_origin_is_elsewhere(tmp_path):
    """The remotes are walked in order until one has a GitHub page: origin on
    another host doesn't end the search."""
    config = (
        ORIGIN.format(url="git@gitlab.com:episode6/collins.git")
        + '[remote "upstream"]\n\turl = git@github.com:episode6/collins.git\n'
    )
    repo = with_remotes(tmp_path / "repo", config)
    assert github_url(repo) == "https://github.com/episode6/collins"


def test_an_unconventional_remote_name_still_counts(tmp_path):
    repo = with_remotes(tmp_path / "repo", '[remote "gh"]\n\turl = git@github.com:e6/c.git\n')
    assert github_url(repo) == "https://github.com/e6/c"


def test_other_sections_are_not_remotes(tmp_path):
    """A url outside a remote stanza — `[lfs]`, say — isn't one."""
    config = "[core]\n\tbare = false\n[lfs]\n\turl = https://github.com/e6/c.git\n"
    assert github_url(with_remotes(tmp_path / "repo", config)) is None


@pytest.mark.parametrize(
    "url",
    [
        "git@gitlab.com:episode6/collins.git",
        "https://github.example.com/episode6/collins.git",
        "https://notgithub.com/episode6/collins",
        "/srv/git/collins.git",
        "../sibling-checkout",
        "https://github.com/episode6",  # no repo half
        "https://github.com/episode6/collins/extra",
        "https://github.com/../..",
        "javascript:alert(1)//github.com/e6/c",
        "",
    ],
)
def test_remotes_with_no_github_page(tmp_path, url):
    assert github_url(with_remotes(tmp_path / "repo", ORIGIN.format(url=url))) is None


def test_no_remotes_at_all(tmp_path):
    assert github_url(with_remotes(tmp_path / "repo", "[core]\n\tbare = false\n")) is None


def test_no_config_file(tmp_path):
    assert github_url(make_repo(tmp_path / "repo")) is None


def test_not_a_repository(tmp_path):
    assert github_url(tmp_path) is None
    assert github_url(None) is None
    assert github_url(tmp_path / "gone") is None


def test_a_worktree_reads_the_main_checkouts_config(tmp_path):
    """The linked worktree has a HEAD of its own but no config; `commondir`
    is what leads back to the one with the remotes in it."""
    main = with_remotes(tmp_path / "main", ORIGIN.format(url="git@github.com:e6/c.git"))
    wt_git_dir = main / ".git" / "worktrees" / "wt"
    wt_git_dir.mkdir(parents=True)
    (wt_git_dir / "HEAD").write_text("ref: refs/heads/wt-branch\n")
    (wt_git_dir / "commondir").write_text("../..\n")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {wt_git_dir}\n")
    assert github_url(worktree) == "https://github.com/e6/c"


# -- default_branch -----------------------------------------------------------


def with_remote_head(repo: Path, remote: str, head: str) -> None:
    """What a clone records about *remote*'s default branch: its
    `refs/remotes/<remote>/HEAD`, a symbolic ref in the usual case."""
    ref_dir = repo / ".git" / "refs" / "remotes" / remote
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / "HEAD").write_text(head)


def with_local_branch(repo: Path, name: str) -> None:
    ref = repo / ".git" / "refs" / "heads" / name
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text("0123456789abcdef0123456789abcdef01234567\n")


UPSTREAM = '[remote "upstream"]\n\turl = {url}\n\tfetch = +refs/heads/*:refs/remotes/upstream/*\n'


def test_default_branch_is_the_remote_head(tmp_path):
    repo = with_remotes(tmp_path / "repo", ORIGIN.format(url="git@github.com:e6/c.git"))
    with_remote_head(repo, "origin", "ref: refs/remotes/origin/main\n")
    assert default_branch(repo) == "main"


def test_default_branch_named_anything(tmp_path):
    """The remote's word is taken as-is: a trunk called `develop` or
    `release/2.0` is what the checkout item should offer."""
    repo = with_remotes(tmp_path / "repo", ORIGIN.format(url="git@github.com:e6/c.git"))
    with_remote_head(repo, "origin", "ref: refs/remotes/origin/release/2.0\n")
    with_local_branch(repo, "main")
    assert default_branch(repo) == "release/2.0"


def test_default_branch_origin_outranks_upstream(tmp_path):
    repo = with_remotes(
        tmp_path / "repo",
        UPSTREAM.format(url="git@github.com:up/c.git") + ORIGIN.format(url="git@github.com:e6/c.git"),
    )
    with_remote_head(repo, "upstream", "ref: refs/remotes/upstream/master\n")
    with_remote_head(repo, "origin", "ref: refs/remotes/origin/main\n")
    assert default_branch(repo) == "main"


def test_default_branch_falls_through_a_remote_without_a_head(tmp_path):
    """`git remote add` writes no HEAD ref; the next remote that has one
    answers."""
    repo = with_remotes(
        tmp_path / "repo",
        UPSTREAM.format(url="git@github.com:up/c.git") + ORIGIN.format(url="git@github.com:e6/c.git"),
    )
    with_remote_head(repo, "upstream", "ref: refs/remotes/upstream/trunk\n")
    assert default_branch(repo) == "trunk"


def test_default_branch_ignores_a_detached_remote_head(tmp_path):
    repo = with_remotes(tmp_path / "repo", ORIGIN.format(url="git@github.com:e6/c.git"))
    with_remote_head(repo, "origin", "0123456789abcdef0123456789abcdef01234567\n")
    with_local_branch(repo, "master")
    assert default_branch(repo) == "master"


def test_default_branch_ignores_a_remote_head_pointing_elsewhere(tmp_path):
    """A symbolic ref outside the remote's own namespace isn't a default
    branch of that remote, whatever wrote it."""
    repo = with_remotes(tmp_path / "repo", ORIGIN.format(url="git@github.com:e6/c.git"))
    with_remote_head(repo, "origin", "ref: refs/heads/main\n")
    assert default_branch(repo) is None


def test_default_branch_without_remotes_is_main_or_master(tmp_path):
    repo = make_repo(tmp_path / "repo")
    with_local_branch(repo, "master")
    assert default_branch(repo) == "master"
    with_local_branch(repo, "main")
    assert default_branch(repo) == "main"


def test_default_branch_packed(tmp_path):
    """After `git gc` the loose ref is gone and `packed-refs` holds it —
    with its header line and the peeled `^` lines tags carry."""
    repo = make_repo(tmp_path / "repo")
    (repo / ".git" / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted\n"
        "0123456789abcdef0123456789abcdef01234567 refs/heads/master\n"
        "89abcdef0123456789abcdef0123456789abcdef refs/tags/v1\n"
        "^0123456789abcdef0123456789abcdef01234567\n"
    )
    assert default_branch(repo) == "master"


def test_default_branch_nowhere_to_be_found(tmp_path):
    repo = make_repo(tmp_path / "repo", head="ref: refs/heads/feature\n")
    with_local_branch(repo, "feature")
    assert default_branch(repo) is None


def test_default_branch_outside_a_repository(tmp_path):
    assert default_branch(tmp_path) is None
    assert default_branch(tmp_path / "gone") is None
    assert default_branch(None) is None


def test_default_branch_in_a_worktree_reads_the_main_checkouts_refs(tmp_path):
    main = with_remotes(tmp_path / "main", ORIGIN.format(url="git@github.com:e6/c.git"))
    with_remote_head(main, "origin", "ref: refs/remotes/origin/main\n")
    wt_git_dir = main / ".git" / "worktrees" / "wt"
    wt_git_dir.mkdir(parents=True)
    (wt_git_dir / "HEAD").write_text("ref: refs/heads/wt-branch\n")
    (wt_git_dir / "commondir").write_text("../..\n")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {wt_git_dir}\n")
    assert default_branch(worktree) == "main"
    assert current_branch(worktree) == "wt-branch"


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


# -- github_url, against a real repository -------------------------------------
# (down here for the `repo` fixture, which the parsing tests above don't need)


@needs_git
def test_a_remote_git_itself_wrote(repo):
    """The config git writes for `remote add` is a form this parses."""
    _git(repo, "remote", "add", "origin", "git@github.com:episode6/collins.git")
    assert github_url(repo) == "https://github.com/episode6/collins"


@needs_git
def test_a_real_worktrees_config(repo, tmp_path):
    """The commondir hop, as git lays it out rather than as the unit test
    above fakes it."""
    _git(repo, "remote", "add", "origin", "https://github.com/episode6/collins.git")
    worktree = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "wt-branch", str(worktree))
    assert github_url(worktree) == "https://github.com/episode6/collins"
