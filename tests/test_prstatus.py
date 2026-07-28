"""Tests for prstatus — parsing Claude Code's pr-link records and gh cache."""

import json

import pytest

from collins import prstatus
from collins.prstatus import PullRequest, describe, enrich, parse_pr_link, state_text

URL = "https://github.com/episode6/collins/pull/55"


def _link(**overrides):
    entry = {
        "type": "pr-link",
        "sessionId": "aae52998-4062-4be1-be1f-40e746c76e56",
        "prNumber": 55,
        "prUrl": URL,
        "prRepository": "episode6/collins",
        "timestamp": "2026-07-27T00:43:57.325Z",
    }
    entry.update(overrides)
    return entry


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """Redirect the gh status cache at a temp file; returns a writer for it."""
    path = tmp_path / "gh-pr-status-cache.json"
    monkeypatch.setattr(prstatus, "PR_STATUS_CACHE", path)

    def write(payload):
        path.write_text(json.dumps(payload) if isinstance(payload, (dict, list)) else payload,
                        encoding="utf-8")

    return write


# -- parse_pr_link ----------------------------------------------------------


def test_parses_a_real_record():
    pr = parse_pr_link(_link())
    assert pr == PullRequest(number=55, url=URL, repository="episode6/collins")
    assert pr.slug == "episode6/collins#55"
    assert pr.label == "#55"


def test_repository_is_optional():
    assert parse_pr_link(_link(prRepository=None)).slug == "#55"


@pytest.mark.parametrize(
    "entry",
    [
        {"type": "assistant", "prNumber": 55, "prUrl": URL},  # wrong record type
        _link(prNumber="55"),  # number as a string
        _link(prNumber=True),  # bool is an int subclass; not a PR number
        _link(prUrl=""),
        _link(prUrl=None),
        {"type": "pr-link"},  # metadata-only
    ],
)
def test_rejects_malformed_records(entry):
    assert parse_pr_link(entry) is None


# -- enrich -----------------------------------------------------------------


def test_enrich_fills_state_and_checks(cache):
    cache({URL: {"number": 55, "state": "DRAFT",
                 "checks": {"passed": 1, "failed": 1, "pending": 0}}})
    pr = enrich(parse_pr_link(_link()))
    assert (pr.state, pr.passed, pr.failed, pr.pending) == ("DRAFT", 1, 1, 0)
    assert pr.label == "#55 ✗"


def test_enrich_ignores_other_prs(cache):
    cache({"https://github.com/episode6/collins/pull/44": {"state": "OPEN"}})
    assert enrich(parse_pr_link(_link())).state is None


@pytest.mark.parametrize("payload", ["not json at all", "[]", '"a string"'])
def test_enrich_survives_an_unusable_cache(cache, payload):
    cache(payload)
    assert enrich(parse_pr_link(_link())).state is None


def test_enrich_survives_a_missing_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(prstatus, "PR_STATUS_CACHE", tmp_path / "gone.json")
    assert enrich(parse_pr_link(_link())).state is None


def test_enrich_of_nothing_is_nothing(cache):
    cache({URL: {"state": "OPEN"}})
    assert enrich(None) is None


def test_enrich_skips_junk_check_counts(cache):
    cache({URL: {"state": 7, "checks": {"passed": "1", "failed": None}}})
    pr = enrich(parse_pr_link(_link()))
    assert (pr.state, pr.passed, pr.failed) == (None, None, None)


# -- chip rendering ---------------------------------------------------------


@pytest.mark.parametrize(
    "counts,label",
    [
        ((None, None, None), "#55"),  # nothing cached
        ((0, 0, 0), "#55"),  # a PR with no checks configured
        ((3, 0, 0), "#55 ✓"),
        ((2, 1, 0), "#55 ✗"),  # a failure outranks passes
        ((2, 1, 4), "#55 ✗"),  # ...and outranks pending runs
        ((2, 0, 1), "#55 ●"),
    ],
)
def test_label_summarizes_checks(counts, label):
    passed, failed, pending = counts
    pr = PullRequest(55, URL, passed=passed, failed=failed, pending=pending)
    assert pr.label == label


# -- describe (the tooltip's long form) -------------------------------------


def test_describe_carries_slug_state_and_checks():
    pr = PullRequest(55, URL, "episode6/collins", "DRAFT", passed=1, failed=1, pending=0)
    assert describe(pr) == "episode6/collins#55 · Draft pull request · 1 passed, 1 failed"


def test_describe_without_cached_status():
    assert describe(PullRequest(55, URL, "episode6/collins")) == "episode6/collins#55"


def test_describe_lists_pending_runs():
    pr = PullRequest(55, URL, "episode6/collins", "OPEN", passed=2, failed=0, pending=3)
    assert describe(pr) == "episode6/collins#55 · Open pull request · 2 passed, 3 pending"


def test_describe_omits_zero_counts():
    pr = PullRequest(55, URL, None, "MERGED", passed=0, failed=0, pending=0)
    assert describe(pr) == "#55 · Merged pull request"


def test_unknown_states_pass_through():
    assert state_text("SOMETHING_NEW") == "SOMETHING_NEW"
