"""worktreebase: a new session worktree branches from the remote's default
branch as it is now (the fetch before a `-w` launch, and the setting the
launch carries)."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from collins import worktreebase

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def git(cwd, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        env={
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
            "PATH": "/usr/bin:/bin",
        },
    ).stdout.strip()


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """An upstream with a `trunk` default branch, and a clone of it."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git(upstream, "init", "-q", "-b", "trunk")
    git(upstream, "commit", "-q", "--allow-empty", "-m", "one")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(upstream), str(clone))
    return upstream, clone


def test_the_setting_pins_the_fresh_base():
    assert json.loads(worktreebase.BASE_REF_SETTINGS) == {"worktree": {"baseRef": "fresh"}}


def test_freshen_fetches_what_the_remote_has_now(repos):
    upstream, clone = repos
    git(upstream, "commit", "-q", "--allow-empty", "-m", "two")
    assert git(clone, "rev-parse", "origin/trunk") != git(upstream, "rev-parse", "trunk")
    assert worktreebase.freshen(str(clone)) == ("fetched", "origin/trunk")
    assert git(clone, "rev-parse", "origin/trunk") == git(upstream, "rev-parse", "trunk")


def test_freshen_works_from_a_subdirectory(repos):
    _upstream, clone = repos
    sub = clone / "sub"
    sub.mkdir()
    assert worktreebase.freshen(str(sub)) == ("fetched", "origin/trunk")


def test_an_unset_origin_head_is_asked_of_the_remote(repos):
    _upstream, clone = repos
    git(clone, "remote", "set-head", "origin", "--delete")
    assert worktreebase.default_branch(str(clone)) is None
    assert worktreebase.freshen(str(clone)) == ("fetched", "origin/trunk")
    assert worktreebase.default_branch(str(clone)) == "trunk"


def test_nothing_to_do_without_a_repository_or_an_origin(tmp_path):
    assert worktreebase.freshen(str(tmp_path)) == ("skipped", "")
    git(tmp_path, "init", "-q")
    assert worktreebase.freshen(str(tmp_path)) == ("skipped", "")


def test_an_unreachable_remote_fails_soft(repos):
    upstream, clone = repos
    shutil.rmtree(upstream)
    outcome, why = worktreebase.freshen(str(clone))
    assert outcome == "failed" and why
