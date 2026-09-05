"""Tests for gitinfo: current_branch, default_branch and github_url, which are
pure filesystem parsing and need no git, the git page's readers (repo_root,
index_mtime, head_sha, resolve_branch, tree_signature — files only, too), and
has_changes/change_summary/ignored_names, the calls that shell out to it."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from collins.gitinfo import (
    change_summary,
    current_branch,
    default_branch,
    git_dir,
    github_url,
    has_changes,
    head_sha,
    ignored_names,
    index_mtime,
    parent_branch,
    remote_branch_name,
    remote_refs_signature,
    repo_root,
    resolve_branch,
    tree_signature,
)


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


# -- repo_root ----------------------------------------------------------------


def test_repo_root_at_root_and_below(tmp_path):
    repo = make_repo(tmp_path / "repo")
    sub = repo / "a" / "b"
    sub.mkdir(parents=True)
    assert repo_root(repo) == repo
    assert repo_root(sub) == repo


def test_repo_root_of_a_worktree_is_the_worktree(tmp_path):
    """The directory holding the pointer file, not the main checkout the
    pointer names: hunk is spawned there and diffs that tree."""
    main = make_repo(tmp_path / "main")
    wt_git_dir = main / ".git" / "worktrees" / "wt"
    wt_git_dir.mkdir(parents=True)
    (wt_git_dir / "HEAD").write_text("ref: refs/heads/wt-branch\n")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {wt_git_dir}\n")
    assert repo_root(worktree) == worktree
    assert repo_root(worktree / "missing") is None


def test_repo_root_outside_a_repository(tmp_path):
    assert repo_root(tmp_path) is None
    assert repo_root(None) is None
    assert repo_root("") is None


# -- index_mtime --------------------------------------------------------------


def test_index_mtime_reads_the_index(tmp_path):
    repo = make_repo(tmp_path / "repo")
    assert index_mtime(repo) is None  # no index yet
    index = repo / ".git" / "index"
    index.write_bytes(b"DIRC")
    os.utime(index, ns=(1_000_000_000, 1_234_567_890_123))
    assert index_mtime(repo) == 1_234_567_890_123
    assert index_mtime(tmp_path) is None


def test_index_mtime_is_the_worktrees_own(tmp_path):
    """A linked worktree keeps its own index under .git/worktrees/<name>."""
    main = make_repo(tmp_path / "main")
    (main / ".git" / "index").write_bytes(b"main")
    wt_git_dir = main / ".git" / "worktrees" / "wt"
    wt_git_dir.mkdir(parents=True)
    (wt_git_dir / "HEAD").write_text("ref: refs/heads/wt\n")
    (wt_git_dir / "commondir").write_text("../..\n")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {wt_git_dir}\n")
    assert index_mtime(worktree) is None
    (wt_git_dir / "index").write_bytes(b"wt")
    assert index_mtime(worktree) == (wt_git_dir / "index").stat().st_mtime_ns


# -- head_sha -----------------------------------------------------------------

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40


def test_head_sha_through_a_loose_ref(tmp_path):
    repo = make_repo(tmp_path / "repo")
    with_local_branch(repo, "main")
    assert head_sha(repo) == "0123456789abcdef0123456789abcdef01234567"


def test_head_sha_through_packed_refs(tmp_path):
    repo = make_repo(tmp_path / "repo")
    (repo / ".git" / "packed-refs").write_text(
        f"# pack-refs with: peeled fully-peeled sorted\n{SHA_A} refs/heads/main\n^{SHA_B}\n"
    )
    assert head_sha(repo) == SHA_A


def test_head_sha_prefers_the_loose_ref(tmp_path):
    """A loose ref is newer than the packed copy (git updates the loose file
    and leaves packed-refs until the next gc)."""
    repo = make_repo(tmp_path / "repo")
    (repo / ".git" / "packed-refs").write_text(f"{SHA_A} refs/heads/main\n")
    with_local_branch(repo, "main")
    assert head_sha(repo) == "0123456789abcdef0123456789abcdef01234567"


def test_head_sha_detached(tmp_path):
    repo = make_repo(tmp_path / "repo", head=f"{SHA_C}\n")
    assert head_sha(repo) == SHA_C


def test_head_sha_unborn_and_outside(tmp_path):
    repo = make_repo(tmp_path / "repo")
    assert head_sha(repo) is None  # ref: refs/heads/main, no such ref yet
    assert head_sha(tmp_path) is None


def test_head_sha_in_a_worktree_reads_the_common_refs(tmp_path):
    main = make_repo(tmp_path / "main")
    (main / ".git" / "packed-refs").write_text(f"{SHA_B} refs/heads/wt\n")
    wt_git_dir = main / ".git" / "worktrees" / "wt"
    wt_git_dir.mkdir(parents=True)
    (wt_git_dir / "HEAD").write_text("ref: refs/heads/wt\n")
    (wt_git_dir / "commondir").write_text("../..\n")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {wt_git_dir}\n")
    assert head_sha(worktree) == SHA_B


# -- resolve_branch -----------------------------------------------------------


def with_remote_branch(repo: Path, remote: str, name: str, sha: str) -> None:
    ref = repo / ".git" / "refs" / "remotes" / remote / name
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(f"{sha}\n")


def test_resolve_branch_local(tmp_path):
    repo = make_repo(tmp_path / "repo")
    with_local_branch(repo, "main")
    assert resolve_branch(repo, "main") == ("main", "0123456789abcdef0123456789abcdef01234567")


def test_resolve_branch_falls_back_to_the_remote_in_rank_order(tmp_path):
    """A clone that never checked main out locally still diffs against
    origin/main; origin outranks upstream outranks anything else."""
    repo = with_remotes(
        tmp_path / "repo",
        '[remote "zed"]\n\turl = x\n' + UPSTREAM.format(url="y") + ORIGIN.format(url="z"),
    )
    with_remote_branch(repo, "zed", "main", SHA_C)
    with_remote_branch(repo, "upstream", "main", SHA_B)
    assert resolve_branch(repo, "main") == ("upstream/main", SHA_B)
    with_remote_branch(repo, "origin", "main", SHA_A)
    assert resolve_branch(repo, "main") == ("origin/main", SHA_A)


def test_resolve_branch_packed_remote(tmp_path):
    repo = with_remotes(tmp_path / "repo", ORIGIN.format(url="z"))
    (repo / ".git" / "packed-refs").write_text(f"{SHA_A} refs/remotes/origin/main\n")
    assert resolve_branch(repo, "main") == ("origin/main", SHA_A)


def test_resolve_branch_none(tmp_path):
    repo = with_remotes(tmp_path / "repo", ORIGIN.format(url="z"))
    assert resolve_branch(repo, "main") is None
    assert resolve_branch(tmp_path, "main") is None
    assert resolve_branch(repo, None) is None


@pytest.mark.parametrize("name", ["", " ", "-main", "--staged", "a..b", "a...b", "ma in", "main\n"])
def test_resolve_branch_rejects_names_that_read_as_arguments(tmp_path, name):
    """The name ends up in hunk's argv; anything git could read as an option
    or a range never gets there."""
    repo = make_repo(tmp_path / "repo")
    with_local_branch(repo, "main")
    assert resolve_branch(repo, name) is None


# -- parent_branch ------------------------------------------------------------


def test_parent_branch_takes_the_first_candidate_the_tree_can_name(tmp_path):
    """The attached PR's base beats the Preferences default, which beats the
    default branch — each only when the repository has it."""
    repo = with_remotes(tmp_path / "repo", ORIGIN.format(url="z"))
    with_remote_head(repo, "origin", "ref: refs/remotes/origin/main\n")
    with_local_branch(repo, "main")
    with_local_branch(repo, "base")
    with_local_branch(repo, "develop")
    assert parent_branch(repo, ("base", "develop")) == "base"
    assert parent_branch(repo, (None, "develop")) == "develop"
    assert parent_branch(repo, ("", "develop")) == "develop"
    assert parent_branch(repo, (None, None)) == "main"
    assert parent_branch(repo, ()) == "main"


def test_parent_branch_falls_through_a_candidate_the_tree_lacks(tmp_path):
    """A base nothing here can diff against, a name typed for another
    repository, or one that reads as an option: the next rung, never a
    disabled load."""
    repo = with_remotes(tmp_path / "repo", ORIGIN.format(url="z"))
    with_remote_head(repo, "origin", "ref: refs/remotes/origin/main\n")
    with_local_branch(repo, "main")
    with_local_branch(repo, "develop")
    assert parent_branch(repo, ("nosuch", "develop")) == "develop"
    assert parent_branch(repo, ("-x", "develop")) == "develop"
    assert parent_branch(repo, ("a..b", "develop")) == "develop"
    assert parent_branch(repo, ("nosuch", "-x")) == "main"


def test_parent_branch_names_a_branch_only_the_remote_has(tmp_path):
    """A name is returned, not a target: resolve_branch finds origin/develop,
    the caller resolves "develop" to that itself."""
    repo = with_remotes(tmp_path / "repo", ORIGIN.format(url="z"))
    with_remote_branch(repo, "origin", "develop", SHA_A)
    with_local_branch(repo, "main")
    assert parent_branch(repo, (None, "develop")) == "develop"


def test_parent_branch_takes_a_remote_qualified_name_as_the_branch_behind_it(tmp_path):
    """"origin/develop" — the form git prints and people type — is the
    branch develop when origin has it: the name comes back bare, and the
    caller's resolve_branch lands on the same ref. Only after the name
    failed as written, so a local branch with a slash in it keeps its
    whole name."""
    repo = with_remotes(tmp_path / "repo", ORIGIN.format(url="z"))
    with_remote_head(repo, "origin", "ref: refs/remotes/origin/main\n")
    with_local_branch(repo, "main")
    with_remote_branch(repo, "origin", "develop", SHA_A)
    with_local_branch(repo, "release/v1")
    assert parent_branch(repo, ("origin/develop",)) == "develop"
    assert resolve_branch(repo, "develop") == ("origin/develop", SHA_A)
    assert parent_branch(repo, ("release/v1",)) == "release/v1"
    # A slashed name that is neither: a remote the config lacks, a branch
    # the remote lacks, or a prefix alone — the next rung.
    assert parent_branch(repo, ("nosuch/develop", "main")) == "main"
    assert parent_branch(repo, ("origin/nosuch",)) == "main"
    assert parent_branch(repo, ("origin/",)) == "main"


def test_remote_branch_name(tmp_path):
    repo = with_remotes(tmp_path / "repo", ORIGIN.format(url="z"))
    with_remote_branch(repo, "origin", "develop", SHA_A)
    with_remote_branch(repo, "origin", "release/v2", SHA_B)
    assert remote_branch_name(repo, "origin/develop") == "develop"
    assert remote_branch_name(repo, "origin/release/v2") == "release/v2"
    assert remote_branch_name(repo, "develop") is None
    assert remote_branch_name(repo, "upstream/develop") is None  # no such remote
    assert remote_branch_name(repo, "origin/main") is None  # origin has no main
    assert remote_branch_name(repo, "origin/") is None
    assert remote_branch_name(repo, "/develop") is None
    assert remote_branch_name(repo, "-origin/develop") is None
    assert remote_branch_name(repo, "origin/a..b") is None
    assert remote_branch_name(repo, None) is None
    assert remote_branch_name(tmp_path, "origin/develop") is None
    assert remote_branch_name(None, "origin/develop") is None


def test_parent_branch_with_nothing_to_name(tmp_path):
    repo = make_repo(tmp_path / "repo", head="ref: refs/heads/feature\n")
    assert parent_branch(repo, ("nosuch",)) is None
    assert parent_branch(tmp_path, ("main",)) is None
    assert parent_branch(None, ("main",)) is None


# -- tree_signature -----------------------------------------------------------


def test_tree_signature_outside_a_repository(tmp_path):
    assert tree_signature(tmp_path, "main") is None


def test_tree_signature_moves_with_index_head_and_base(tmp_path):
    repo = make_repo(tmp_path / "repo", head="ref: refs/heads/feat\n")
    with_local_branch(repo, "main")
    feat = repo / ".git" / "refs" / "heads" / "feat"
    feat.write_text(f"{SHA_A}\n")
    index = repo / ".git" / "index"
    index.write_bytes(b"1")
    os.utime(index, ns=(1_000, 1_000))
    first = tree_signature(repo, "main")
    assert first == (1_000, SHA_A, "0123456789abcdef0123456789abcdef01234567")
    assert tree_signature(repo, "main") == first  # stable while nothing moves

    os.utime(index, ns=(2_000, 2_000))
    second = tree_signature(repo, "main")
    assert second != first

    feat.write_text(f"{SHA_B}\n")
    third = tree_signature(repo, "main")
    assert third != second

    (repo / ".git" / "refs" / "heads" / "main").write_text(f"{SHA_C}\n")
    assert tree_signature(repo, "main") != third


def test_tree_signature_without_a_base(tmp_path):
    repo = make_repo(tmp_path / "repo", head=f"{SHA_A}\n")
    assert tree_signature(repo, None) == (None, SHA_A, None)


# -- git_dir ------------------------------------------------------------------


def test_git_dir_is_the_worktrees_own(tmp_path):
    """The commit gate's markers (MERGE_HEAD, rebase-merge) live in the
    worktree's git directory, not the common one."""
    main = make_repo(tmp_path / "main")
    assert git_dir(main) == main / ".git"
    assert git_dir(main / "missing") is None
    wt_git_dir = main / ".git" / "worktrees" / "wt"
    wt_git_dir.mkdir(parents=True)
    (wt_git_dir / "HEAD").write_text("ref: refs/heads/wt\n")
    (wt_git_dir / "commondir").write_text("../..\n")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {wt_git_dir}\n")
    assert git_dir(worktree) == wt_git_dir
    sub = worktree / "a"
    sub.mkdir()
    assert git_dir(sub) == wt_git_dir
    assert git_dir(tmp_path) is None
    assert git_dir(None) is None
    assert git_dir("") is None


# -- remote_refs_signature --------------------------------------------------------


def test_remote_refs_signature_outside_a_repository(tmp_path):
    assert remote_refs_signature(tmp_path) is None
    assert remote_refs_signature(None) is None


def test_remote_refs_signature_stays_put_without_remote_refs(tmp_path):
    repo = make_repo(tmp_path / "repo")
    first = remote_refs_signature(repo)
    assert first == (None, ())
    with_local_branch(repo, "main")  # a local ref is not what it watches
    assert remote_refs_signature(repo) == first


def test_remote_refs_signature_moves_with_a_pushed_ref_and_packed_refs(tmp_path):
    """git writes a loose ref by renaming a lock file into the ref's
    directory; that directory's mtime is what moves — including one
    nested under a slashed branch name — and packed-refs on a gc."""
    repo = make_repo(tmp_path / "repo")
    with_remote_branch(repo, "origin", "main", SHA_A)
    first = remote_refs_signature(repo)
    assert first is not None and first[0] is None
    assert [name for name, _stamp in first[1]] == ["refs/remotes", "refs/remotes/origin"]
    assert remote_refs_signature(repo) == first  # stable while nothing moves

    origin = repo / ".git" / "refs" / "remotes" / "origin"
    os.utime(origin, ns=(2_000, 2_000))  # a push: `main.lock` renamed over `main`
    second = remote_refs_signature(repo)
    assert second != first

    with_remote_branch(repo, "origin", "release/v1", SHA_B)  # a new directory under origin
    third = remote_refs_signature(repo)
    assert third != second
    assert "refs/remotes/origin/release" in [name for name, _stamp in third[1]]
    os.utime(origin / "release", ns=(3_000, 3_000))
    fourth = remote_refs_signature(repo)
    assert fourth != third

    packed = repo / ".git" / "packed-refs"
    packed.write_text(f"{SHA_A} refs/remotes/origin/main\n")
    os.utime(packed, ns=(4_000, 4_000))
    fifth = remote_refs_signature(repo)
    assert fifth != fourth and fifth[0] == 4_000
    os.utime(packed, ns=(5_000, 5_000))
    assert remote_refs_signature(repo) != fifth


def test_remote_refs_signature_reads_the_common_dir_of_a_worktree(tmp_path):
    main = make_repo(tmp_path / "main")
    with_remote_branch(main, "origin", "main", SHA_A)
    wt_git_dir = main / ".git" / "worktrees" / "wt"
    wt_git_dir.mkdir(parents=True)
    (wt_git_dir / "HEAD").write_text("ref: refs/heads/wt\n")
    (wt_git_dir / "commondir").write_text("../..\n")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {wt_git_dir}\n")
    assert remote_refs_signature(worktree) == remote_refs_signature(main)
    assert remote_refs_signature(worktree)[1] != ()


def test_remote_refs_signature_bounds_its_walk(tmp_path, monkeypatch):
    monkeypatch.setattr("collins.gitinfo._REMOTE_REFS_DIR_LIMIT", 3)
    repo = make_repo(tmp_path / "repo")
    for name in ("a/b", "c/d", "e/f", "g/h"):
        with_remote_branch(repo, "origin", name, SHA_A)
    signature = remote_refs_signature(repo)
    assert len(signature[1]) == 3


# -- change_summary -----------------------------------------------------------


@needs_git
def test_change_summary_clean(repo):
    assert change_summary(repo) == (False, False)


@needs_git
def test_change_summary_staged_only(repo):
    (repo / "b.txt").write_text("new\n")
    _git(repo, "add", "b.txt")
    assert change_summary(repo) == (True, False)


@needs_git
def test_change_summary_unstaged_only(repo):
    (repo / "a.txt").write_text("two\n")
    assert change_summary(repo) == (False, True)


@needs_git
def test_change_summary_untracked_counts_as_unstaged(repo):
    (repo / "b.txt").write_text("new\n")
    assert change_summary(repo) == (False, True)


@needs_git
def test_change_summary_both(repo):
    (repo / "a.txt").write_text("two\n")
    _git(repo, "add", "a.txt")
    (repo / "a.txt").write_text("three\n")
    assert change_summary(repo) == (True, True)


@needs_git
def test_change_summary_outside_a_repository(tmp_path):
    assert change_summary(tmp_path) == (False, False)
    assert change_summary(None) == (False, False)


def test_change_summary_when_git_never_answers(repo, monkeypatch):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("git", 2.0)

    monkeypatch.setattr("collins.gitinfo.subprocess.run", timeout)
    assert change_summary(repo) == (False, False)
