"""Tests for prstatus — parsing Claude Code's pr-link records, its gh status
cache, and the `gh pr view` refresh Collins runs when that cache is stale."""

import json
import os
import subprocess
import time

import pytest

from collins import prstatus
from collins.prstatus import (
    CACHE_MAX_AGE_S,
    PullRequest,
    describe,
    enrich,
    parse_pr_link,
    refresh,
    state_text,
)

URL = "https://github.com/episode6/collins/pull/55"
_TTL_S = prstatus._TTL_S
_ERROR_TTL_S = prstatus._ERROR_TTL_S


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

    def write(payload, age_s=0):
        path.write_text(json.dumps(payload) if isinstance(payload, (dict, list)) else payload,
                        encoding="utf-8")
        if age_s:  # backdate it: only a recent cache is trusted
            stamp = time.time() - age_s
            os.utime(path, (stamp, stamp))

    return write


class _Clock:
    """Stand-in for the monotonic clock the status TTLs are measured against."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture(autouse=True)
def scheduled(monkeypatch):
    """Keep tests off the real `gh`: collects the URLs refreshes were scheduled
    for, and clears the module's status cache around every test."""
    urls: list[str] = []
    monkeypatch.setattr(prstatus, "_schedule", urls.append)
    monkeypatch.setattr(prstatus, "_gh_missing", False)
    prstatus._statuses.clear()
    prstatus._inflight.clear()
    yield urls
    prstatus._statuses.clear()
    prstatus._inflight.clear()


@pytest.fixture
def clock(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(prstatus, "_now", clock)
    return clock


@pytest.fixture
def gh(monkeypatch):
    """Stub the `gh pr view` call; returns a setter for its next result."""

    def serve(entry):
        monkeypatch.setattr(prstatus, "_run_gh", lambda url: entry)

    return serve


# -- parse_pr_link ----------------------------------------------------------


def test_parses_a_real_record():
    pr = parse_pr_link(_link())
    assert pr == PullRequest(number=55, url=URL, repository="episode6/collins")
    assert pr.slug == "episode6/collins#55"
    assert pr.glyph is None


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
    assert pr.glyph == "✗"


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


# -- the CLI cache goes stale -----------------------------------------------


def test_a_stale_cli_cache_is_not_trusted(cache):
    """Only FleetView refreshes that file, so an old one can be days out of
    date — better a bare number than a wrong glyph."""
    cache({URL: {"state": "OPEN", "checks": {"passed": 0, "failed": 1, "pending": 0}}},
          age_s=CACHE_MAX_AGE_S + 60)
    pr = enrich(parse_pr_link(_link()))
    assert (pr.state, pr.failed) == (None, None)
    assert pr.glyph is None


def test_a_recent_cli_cache_still_warms_the_chip(cache):
    cache({URL: {"state": "OPEN", "checks": {"passed": 2, "failed": 0, "pending": 0}}},
          age_s=CACHE_MAX_AGE_S - 60)
    assert enrich(parse_pr_link(_link())).glyph == "✓"


# -- our own gh refresh -----------------------------------------------------


def test_our_status_overrides_the_cli_cache(cache, gh):
    cache({URL: {"state": "OPEN", "checks": {"passed": 0, "failed": 1, "pending": 0}}})
    gh({"state": "MERGED", "checks": {"passed": 2, "failed": 0, "pending": 0}})
    refresh(URL)
    pr = enrich(parse_pr_link(_link()))
    assert (pr.state, pr.passed, pr.failed) == ("MERGED", 2, 0)
    assert pr.merged is True


def test_enrich_schedules_a_refresh_for_an_unknown_pr(scheduled):
    enrich(parse_pr_link(_link()))
    assert scheduled == [URL]


def test_only_one_refresh_is_in_flight_per_pr(scheduled):
    for _ in range(3):  # the footer polls every second
        enrich(parse_pr_link(_link()))
    assert scheduled == [URL]


def test_a_fresh_status_is_not_refetched(scheduled, clock, gh):
    gh({"state": "OPEN", "checks": {"passed": 1, "failed": 0, "pending": 0}})
    refresh(URL)
    scheduled.clear()
    clock.advance(_TTL_S - 1)
    enrich(parse_pr_link(_link()))
    assert scheduled == []
    clock.advance(1)
    enrich(parse_pr_link(_link()))
    assert scheduled == [URL]


def test_a_stale_status_is_shown_while_its_refresh_runs(clock, gh):
    """A minute-old glyph beats a blank one; the refresh lands a poll later."""
    gh({"state": "OPEN", "checks": {"passed": 1, "failed": 0, "pending": 0}})
    refresh(URL)
    clock.advance(_TTL_S * 10)
    assert enrich(parse_pr_link(_link())).glyph == "✓"


def test_a_failed_fetch_backs_off_further(scheduled, clock, gh, cache):
    """An offline or unauthenticated gh shouldn't be retried every minute — and
    a fresh CLI cache still covers the chip meanwhile."""
    cache({URL: {"state": "OPEN", "checks": {"passed": 3, "failed": 0, "pending": 0}}})
    gh(None)
    refresh(URL)
    scheduled.clear()
    assert enrich(parse_pr_link(_link())).glyph == "✓"  # from the CLI cache
    clock.advance(_TTL_S * 2)
    enrich(parse_pr_link(_link()))
    assert scheduled == []
    clock.advance(_ERROR_TTL_S)
    enrich(parse_pr_link(_link()))
    assert scheduled == [URL]


def test_nothing_is_fetched_once_gh_is_known_missing(scheduled, monkeypatch):
    monkeypatch.setattr(prstatus, "_gh_missing", True)
    assert enrich(parse_pr_link(_link())).glyph is None
    assert scheduled == []


@pytest.mark.parametrize(
    "url",
    [
        "--version",  # a transcript's prUrl is untrusted; never hand it to argv
        "-x",
        "https://github.com/episode6/collins/issues/55",
        "https://github.com/episode6/collins/pull/55; rm -rf /",
        "https://github.com/episode6/collins/pull/",
        "not a url at all",
    ],
)
def test_only_pr_page_urls_are_fetched(scheduled, url):
    enrich(PullRequest(55, url))
    assert scheduled == []


# -- shaping gh's output ----------------------------------------------------


@pytest.mark.parametrize(
    "rollup,counts",
    [
        ([], (0, 0, 0)),
        (None, (0, 0, 0)),
        ([{"conclusion": "SUCCESS", "status": "COMPLETED"}], (1, 0, 0)),
        ([{"conclusion": "SKIPPED"}, {"conclusion": "NEUTRAL"}], (2, 0, 0)),
        ([{"conclusion": "FAILURE"}, {"conclusion": "ERROR"}], (0, 2, 0)),
        ([{"conclusion": None, "status": "IN_PROGRESS"}], (0, 0, 1)),  # still running
        ([{"state": "PENDING"}], (0, 0, 1)),  # a StatusContext, not a CheckRun
        ([{"state": "SUCCESS"}], (1, 0, 0)),
        ([{"conclusion": "CANCELLED", "status": "COMPLETED"}], (0, 1, 0)),  # unknown → look
        (["nope", None, {"conclusion": "SUCCESS"}], (1, 0, 0)),  # junk entries skipped
    ],
)
def test_check_counts_follow_the_rollup(rollup, counts):
    passed, failed, pending = counts
    assert prstatus._counts(rollup) == {
        "passed": passed, "failed": failed, "pending": pending
    }


@pytest.mark.parametrize(
    "data,state",
    [
        ({"state": "OPEN", "isDraft": True}, "DRAFT"),  # gh reports draft separately
        ({"state": "OPEN", "isDraft": False}, "OPEN"),
        ({"state": "MERGED", "isDraft": True}, "MERGED"),
        ({"state": "CLOSED"}, "CLOSED"),
        ({"state": ""}, None),
        ({"state": 7}, None),
        ({}, None),
    ],
)
def test_state_folds_in_draftness(data, state):
    assert prstatus._state(data) == state


def _completed(stdout="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def test_run_gh_shapes_a_real_reply(monkeypatch):
    reply = json.dumps({
        "isDraft": True,
        "state": "OPEN",
        "statusCheckRollup": [
            {"__typename": "CheckRun", "conclusion": "SUCCESS", "status": "COMPLETED",
             "name": "lint"},
            {"__typename": "CheckRun", "conclusion": None, "status": "IN_PROGRESS",
             "name": "test"},
        ],
    })
    monkeypatch.setattr(prstatus.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(prstatus.subprocess, "run", lambda *a, **kw: _completed(reply))
    assert prstatus._run_gh(URL) == {
        "state": "DRAFT", "checks": {"passed": 1, "failed": 0, "pending": 1}
    }


@pytest.mark.parametrize(
    "outcome",
    [
        _completed("", 1),  # no such PR, not logged in, ...
        _completed("not json"),
        _completed("[]"),  # valid json, wrong shape
        subprocess.TimeoutExpired("gh", 10),
        OSError("boom"),
    ],
)
def test_run_gh_degrades_to_no_status(monkeypatch, outcome):
    def run(*_args, **_kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(prstatus.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(prstatus.subprocess, "run", run)
    assert prstatus._run_gh(URL) is None


def test_run_gh_gives_up_when_gh_is_absent(monkeypatch):
    monkeypatch.setattr(prstatus.shutil, "which", lambda _: None)
    assert prstatus._run_gh(URL) is None
    assert prstatus._gh_missing is True


def test_run_gh_passes_the_url_as_an_argument(monkeypatch):
    """No shell, and the URL trails the subcommand — `gh pr view <url>` needs no
    repository cwd, which is what lets this run from anywhere."""
    seen = []
    monkeypatch.setattr(prstatus.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(
        prstatus.subprocess, "run",
        lambda argv, **kw: seen.append((argv, kw)) or _completed('{"state": "OPEN"}'),
    )
    prstatus._run_gh(URL)
    argv, kwargs = seen[0]
    assert argv[:4] == ["/usr/bin/gh", "pr", "view", URL]
    assert kwargs["timeout"] == prstatus._GH_TIMEOUT_S
    assert "shell" not in kwargs


# -- chip rendering ---------------------------------------------------------


@pytest.mark.parametrize(
    "counts,glyph",
    [
        ((None, None, None), None),  # nothing cached
        ((0, 0, 0), None),  # a PR with no checks configured
        ((3, 0, 0), "✓"),
        ((2, 1, 0), "✗"),  # a failure outranks passes
        ((2, 1, 4), "✗"),  # ...and outranks pending runs
        ((2, 0, 1), "●"),
    ],
)
def test_glyph_summarizes_checks(counts, glyph):
    passed, failed, pending = counts
    pr = PullRequest(55, URL, passed=passed, failed=failed, pending=pending)
    assert pr.glyph == glyph


def test_a_merged_pr_drops_its_check_glyph():
    """The purple merge mark replaces it — how CI went on the way in is history."""
    pr = PullRequest(55, URL, state="MERGED", passed=2, failed=0, pending=0)
    assert (pr.merged, pr.glyph) == (True, None)


@pytest.mark.parametrize("state", [None, "OPEN", "DRAFT", "CLOSED", "SOMETHING_NEW"])
def test_every_other_state_keeps_the_glyph(state):
    pr = PullRequest(55, URL, state=state, passed=2, failed=0, pending=0)
    assert (pr.merged, pr.glyph) == (False, "✓")


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
