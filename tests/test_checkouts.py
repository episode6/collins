# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Is a directory a git checkout (collins.checkouts, split-service spec
§3.23, PR-2.6): the rule over `fs.stat`'s kind of the `.git` marker (a
directory is a clone, a file a worktree's pointer; nothing there or a
dangling link is neither), and `ask` as the request: the marker's path,
the answer landing through `remotefiles.off_main`, and False for every
failure."""

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

    def stat_path(path, root=None):
        asked.append(path)
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
