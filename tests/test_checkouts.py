# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Is a directory a git checkout (collins.checkouts, split-service spec
§3.23, PR-2.6): the rule over `fs.stat`'s kind of the `.git` marker (a
directory is a clone, a file a worktree's pointer; nothing there or a
dangling link is neither), and `ask` as the request: the marker's path,
the answer landing through `remotefiles.off_main`, and False for every
failure; and `ask_answer`, the same question for a launch that waits on it,
where a failure is its own answer (None) and the wait is short."""

from __future__ import annotations

import pytest

from collins import checkouts, remotefiles
from collins.api import protocol


@pytest.mark.parametrize(
    "kind,expected",
    [("dir", True), ("file", True), ("missing", False), ("symlink", False), ("other", False), (None, False)],
)
def test_the_rule_over_the_markers_kind(kind, expected):
    assert checkouts.is_checkout(kind) is expected


def test_the_marker_is_the_dot_git_under_the_directory():
    assert checkouts.git_marker("/srv/p") == "/srv/p/.git"
    assert checkouts.git_marker("/srv/p/") == "/srv/p/.git"
    assert checkouts.git_marker("/") == "/.git"


TIMEOUTS: list[float | None] = []


def _run_inline(monkeypatch, stat):
    """`off_main` with the worker and the landing run inline; *stat* answers
    `remotefiles.stat_path`."""
    asked: list[str] = []

    def off_main(work, land, name="x"):
        try:
            result = ("ok", work())
        except protocol.RequestRefused as refusal:
            result = ("refused", refusal)
        land(*result)

    def stat_path(path, root=None, timeout=None):
        asked.append(path)
        TIMEOUTS.append(timeout)
        if isinstance(stat, Exception):
            raise stat
        return remotefiles.Stat(stat, None, None, False)

    monkeypatch.setattr(remotefiles, "off_main", off_main)
    monkeypatch.setattr(remotefiles, "stat_path", stat_path)
    return asked


def test_ask_stats_the_marker_and_answers(monkeypatch):
    answers: list[bool] = []
    asked = _run_inline(monkeypatch, "file")
    checkouts.ask("/srv/p", answers.append)
    assert asked == ["/srv/p/.git"] and answers == [True]


def test_ask_is_false_for_a_missing_marker_a_refusal_and_no_directory(monkeypatch):
    answers: list[bool] = []
    _run_inline(monkeypatch, "missing")
    checkouts.ask("/srv/p", answers.append)
    _run_inline(monkeypatch, protocol.RequestRefused(protocol.ERROR_GONE, "Not connected", {}))
    checkouts.ask("/srv/p", answers.append)
    asked = _run_inline(monkeypatch, "dir")
    checkouts.ask("", answers.append)  # no directory: nothing to ask
    assert answers == [False, False, False] and asked == []


def test_ask_answer_tells_no_checkout_from_could_not_ask(monkeypatch):
    """The verification of PR 614 (S2): for a launch the user asked to
    isolate, "the service could not say" must not read as "no checkout",
    or the session starts in the checkout the worktree was to protect."""
    answers: list[bool | None] = []
    _run_inline(monkeypatch, "dir")
    checkouts.ask_answer("/srv/p", answers.append)
    _run_inline(monkeypatch, "missing")
    checkouts.ask_answer("/srv/p", answers.append)
    _run_inline(monkeypatch, protocol.RequestRefused(protocol.ERROR_FAILED, "timed out", {}))
    checkouts.ask_answer("/srv/p", answers.append)
    _run_inline(monkeypatch, protocol.RequestRefused(protocol.ERROR_GONE, "Not connected", {}))
    checkouts.ask_answer("/srv/p", answers.append)
    asked = _run_inline(monkeypatch, "dir")
    checkouts.ask_answer("", answers.append)  # no directory: nothing to ask, and no checkout
    assert answers == [True, False, None, None, False] and asked == []
    assert checkouts.answer_of("refused", object()) is None
    assert checkouts.answer_of("ok", remotefiles.Stat("file", None, None, False)) is True


def test_a_held_launchs_ask_and_an_offers_both_wait_ten_seconds(monkeypatch):
    """The 2026-10-18 review's nit 5: an offer (a project header's menu) has
    a person waiting on it too, so it gets the launch's 10 s and never the
    files' minute."""
    _run_inline(monkeypatch, "dir")
    del TIMEOUTS[:]
    checkouts.ask_answer("/srv/p", lambda _answer: None)
    checkouts.ask("/srv/p", lambda _answer: None)
    assert TIMEOUTS == [checkouts.ANSWER_TIMEOUT_S] * 2 and checkouts.ANSWER_TIMEOUT_S == 10.0


def test_stat_path_passes_a_callers_timeout_and_defaults_to_the_files(monkeypatch):
    calls: list[float] = []

    def call(message, timeout=None):
        calls.append(timeout)
        return {"kind": "dir"}

    monkeypatch.setattr(remotefiles.apilink, "call", call)
    remotefiles.stat_path("/srv/p/.git", timeout=10.0)
    remotefiles.stat_path("/srv/p/.git")
    assert calls == [10.0, remotefiles.CALL_TIMEOUT_S]
