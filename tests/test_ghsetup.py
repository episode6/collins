"""Tests for ghsetup — the launch-time question of whether the GitHub CLI is
installed and signed in, and for prstatus.gh_succeeds, the exit-code-only call
it asks with."""

import subprocess

import pytest

from collins import ghsetup, prstatus


def _completed(returncode=0, stdout=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


@pytest.fixture(autouse=True)
def _gh_present(monkeypatch):
    """A gh on PATH, and the missing-gh latch cleared — it is a module global
    that outlives one test's monkeypatched `which`."""
    monkeypatch.setattr(prstatus, "_gh_missing", False)
    monkeypatch.setattr(prstatus.shutil, "which", lambda _: "/usr/bin/gh")


def test_missing_when_gh_is_not_on_path(monkeypatch):
    monkeypatch.setattr(ghsetup.shutil, "which", lambda _: None)
    # No subprocess at all: the question is answered by PATH alone.
    monkeypatch.setattr(
        prstatus.subprocess, "run", lambda *a, **kw: pytest.fail("gh was run anyway")
    )
    assert ghsetup.check() == ghsetup.MISSING


def test_logged_out_when_gh_holds_no_credentials(monkeypatch):
    monkeypatch.setattr(prstatus.subprocess, "run", lambda *a, **kw: _completed(1))
    assert ghsetup.check() == ghsetup.LOGGED_OUT


def test_ready_when_gh_has_a_token(monkeypatch):
    monkeypatch.setattr(prstatus.subprocess, "run", lambda *a, **kw: _completed(0, "gho_secret"))
    assert ghsetup.check() == ghsetup.READY


def test_signed_in_is_asked_locally(monkeypatch):
    """`gh auth token` rather than `gh auth status`: status validates the token
    against GitHub, so being offline would read as being signed out."""
    seen = []
    monkeypatch.setattr(
        prstatus.subprocess,
        "run",
        lambda argv, **kw: seen.append(argv) or _completed(0, "gho_secret"),
    )
    ghsetup.check()
    assert seen == [["/usr/bin/gh", "auth", "token"]]


def test_gh_succeeds_is_the_exit_code_and_nothing_else(monkeypatch):
    monkeypatch.setattr(prstatus.subprocess, "run", lambda *a, **kw: _completed(0, "gho_secret"))
    assert prstatus.gh_succeeds(["auth", "token"]) is True
    monkeypatch.setattr(prstatus.subprocess, "run", lambda *a, **kw: _completed(1, "gho_secret"))
    assert prstatus.gh_succeeds(["auth", "token"]) is False


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("gh", 10), OSError("boom")])
def test_gh_succeeds_survives_a_call_that_cannot_run(monkeypatch, failure):
    def run(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(prstatus.subprocess, "run", run)
    assert prstatus.gh_succeeds(["auth", "token"]) is False


def test_gh_succeeds_without_gh_at_all(monkeypatch):
    monkeypatch.setattr(prstatus.shutil, "which", lambda _: None)
    assert prstatus.gh_succeeds(["auth", "token"]) is False


def test_install_url_is_githubs_own_page():
    """Collins links the install rather than transcribing one: which package
    manager to name is a question this app can't answer."""
    assert ghsetup.INSTALL_URL == "https://cli.github.com/"
