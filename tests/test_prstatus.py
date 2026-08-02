"""Tests for prstatus — parsing Claude Code's pr-link records, its gh status
cache, the `gh pr view` refresh Collins runs when that cache is stale, the
branch lookup behind the footer's refresh button, and the records a session's
PRs are persisted as."""

import json
import os
import subprocess
import threading
import time
from dataclasses import replace

import pytest

from collins import prstatus
from collins.prstatus import (
    CACHE_MAX_AGE_S,
    PullRequest,
    describe,
    discover_pr,
    enrich,
    forget_status,
    from_record,
    from_records,
    invalidate,
    menu_name,
    merge_ordered,
    parse_pr_link,
    refresh,
    resync,
    state_text,
    to_record,
    to_records,
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
    assert pr.badge is None


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
    assert pr.badge == "failed"


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
    date — better a bare number than a wrong mark."""
    cache({URL: {"state": "OPEN", "checks": {"passed": 0, "failed": 1, "pending": 0}}},
          age_s=CACHE_MAX_AGE_S + 60)
    pr = enrich(parse_pr_link(_link()))
    assert (pr.state, pr.failed) == (None, None)
    assert pr.badge is None


def test_a_recent_cli_cache_still_warms_the_chip(cache):
    cache({URL: {"state": "OPEN", "checks": {"passed": 2, "failed": 0, "pending": 0}}},
          age_s=CACHE_MAX_AGE_S - 60)
    assert enrich(parse_pr_link(_link())).passed == 2


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
    """A minute-old status beats a blank one; the refresh lands a poll later."""
    gh({"state": "OPEN", "checks": {"passed": 1, "failed": 0, "pending": 0}})
    refresh(URL)
    clock.advance(_TTL_S * 10)
    assert enrich(parse_pr_link(_link())).passed == 1


def test_a_failed_fetch_backs_off_further(scheduled, clock, gh, cache):
    """An offline or unauthenticated gh shouldn't be retried every minute — and
    a fresh CLI cache still covers the chip meanwhile."""
    cache({URL: {"state": "OPEN", "checks": {"passed": 3, "failed": 0, "pending": 0}}})
    gh(None)
    refresh(URL)
    scheduled.clear()
    assert enrich(parse_pr_link(_link())).passed == 3  # from the CLI cache
    clock.advance(_TTL_S * 2)
    enrich(parse_pr_link(_link()))
    assert scheduled == []
    clock.advance(_ERROR_TTL_S)
    enrich(parse_pr_link(_link()))
    assert scheduled == [URL]


def test_nothing_is_fetched_once_gh_is_known_missing(scheduled, monkeypatch):
    monkeypatch.setattr(prstatus, "_gh_missing", True)
    assert enrich(parse_pr_link(_link())).state is None
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


# -- invalidate: the refresh button's half of a refresh ----------------------


def test_invalidating_makes_the_next_enrich_refetch(scheduled, clock, gh):
    """Clicking refresh on a PR whose status is a few seconds old still refetches
    — a TTL that ignores the click would make the button a lie."""
    gh({"state": "OPEN", "checks": {"passed": 1, "failed": 0, "pending": 0}})
    refresh(URL)
    scheduled.clear()
    enrich(parse_pr_link(_link()))
    assert scheduled == []  # still fresh
    invalidate(URL)
    enrich(parse_pr_link(_link()))
    assert scheduled == [URL]


def test_invalidating_keeps_the_status_until_the_new_one_lands(scheduled, clock, gh):
    """A click must refresh the chip in place, not blank it for a poll: the
    status we have stands until the refetch replaces it."""
    gh({"state": "OPEN", "checks": {"passed": 2, "failed": 0, "pending": 0}})
    refresh(URL)
    invalidate(URL)
    assert enrich(parse_pr_link(_link())).passed == 2  # still there, refetch pending
    assert scheduled == [URL]


def test_invalidating_a_failed_fetch_retries_it_now(scheduled, clock, gh):
    """The error backoff is there to stop pointless retries, not to stop the
    user asking for one — a click gets its fetch either way."""
    gh(None)
    refresh(URL)
    scheduled.clear()
    invalidate(URL)
    enrich(parse_pr_link(_link()))
    assert scheduled == [URL]


def test_invalidating_leaves_other_prs_alone(scheduled, clock, gh):
    other = "https://github.com/episode6/collins/pull/56"
    gh({"state": "OPEN", "checks": {"passed": 1, "failed": 0, "pending": 0}})
    refresh(URL)
    refresh(other)
    scheduled.clear()
    invalidate(URL)
    enrich(PullRequest(56, other))
    assert scheduled == []


def test_invalidating_an_unknown_pr_is_harmless(scheduled):
    invalidate("https://github.com/episode6/collins/pull/999")
    assert scheduled == []


# -- discover_pr: finding a PR by its branch --------------------------------


@pytest.fixture
def gh_json(monkeypatch):
    """Stub gh_json; returns (setter, calls) for asserting what gh was asked."""
    calls: list[tuple[list[str], str | None]] = []
    reply: list[object] = [None]

    def fake(args, cwd=None):
        calls.append((args, cwd))
        return reply[0]

    monkeypatch.setattr(prstatus, "gh_json", fake)
    return (lambda value: reply.__setitem__(0, value)), calls


def _found(number=74, created="2026-07-30T02:19:00Z", **overrides):
    """One entry of a `gh pr list --head <branch>` reply."""
    entry = {
        "number": number,
        "url": f"https://github.com/episode6/collins/pull/{number}",
        "createdAt": created,
        "state": "OPEN",
        "isDraft": False,
        "statusCheckRollup": [{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}],
    }
    entry.update(overrides)
    return entry


_DISCOVERED = [_found()]


def test_discovers_the_branch_pr_with_its_status(gh_json):
    serve, calls = gh_json
    serve(_DISCOVERED)
    pr = discover_pr("/home/me/dev/collins", "pr-chip-status-refresh")
    assert (pr.number, pr.url) == (74, _found()["url"])
    assert pr.repository == "episode6/collins"  # read back out of the URL
    assert (pr.state, pr.passed, pr.failed) == ("OPEN", 1, 1)
    assert pr.badge == "failed"
    args, cwd = calls[0]
    assert args[:4] == ["pr", "list", "--head", "pr-chip-status-refresh"]
    assert cwd == "/home/me/dev/collins"  # the branch means nothing outside it


def test_a_discovered_status_counts_as_ours(gh_json, scheduled):
    """One call answers "which PR?" and "how is its CI?", so the poll that
    follows the click must not immediately fetch the same thing again."""
    serve, _calls = gh_json
    serve(_DISCOVERED)
    pr = discover_pr("/home/me/dev/collins", "some-branch")
    scheduled.clear()
    assert enrich(pr).badge == "failed"  # the status the lookup itself came back with
    assert scheduled == []


def test_the_most_recent_pr_on_the_branch_wins(gh_json):
    """Branches get reused: a merged PR then fresh work on the same name. The
    newest is the one being worked on now."""
    serve, _calls = gh_json
    serve([
        _found(70, "2026-06-01T10:00:00Z", state="MERGED"),
        _found(75, "2026-07-29T09:00:00Z"),
        _found(58, "2026-05-02T10:00:00Z", state="CLOSED"),
    ])
    assert discover_pr("/home/me/dev/collins", "feat/x").number == 75


def test_the_higher_number_breaks_a_tie(gh_json):
    """gh timestamps to the second, so two PRs opened in the same second are
    ordered by number instead of by dict order."""
    serve, _calls = gh_json
    serve([_found(80, "2026-07-30T02:19:00Z"), _found(81, "2026-07-30T02:19:00Z")])
    assert discover_pr("/home/me/dev/collins", "feat/x").number == 81


def test_a_merged_pr_is_still_a_discovery(gh_json):
    """The branch's only PR being merged is an answer, not a failure: the chip
    should say merged rather than say nothing."""
    serve, _calls = gh_json
    serve([_found(70, state="MERGED", statusCheckRollup=[])])
    pr = discover_pr("/home/me/dev/collins", "feat/x")
    assert (pr.number, pr.state, pr.merged) == (70, "MERGED", True)


def test_discovery_asks_for_closed_prs_too(gh_json):
    """--state all: a branch whose PR has landed still answers which PR it was."""
    serve, calls = gh_json
    serve(_DISCOVERED)
    discover_pr("/home/me/dev/collins", "feat/x")
    args, _cwd = calls[0]
    assert "--state" in args and args[args.index("--state") + 1] == "all"
    assert args[args.index("--limit") + 1] == str(prstatus._DISCOVER_LIMIT)


def test_unusable_entries_are_skipped_not_fatal(gh_json):
    """One malformed entry among several must not lose the good one."""
    serve, _calls = gh_json
    serve([
        "not a dict",
        _found(90, "2026-07-30T03:00:00Z", url="https://github.com/o/r/issues/90"),
        {**_found(91, "2026-07-30T02:00:00Z"), "number": True},
        _found(60, "2026-01-01T00:00:00Z"),
    ])
    assert discover_pr("/home/me/dev/collins", "feat/x").number == 60


def test_a_missing_timestamp_sorts_last(gh_json):
    serve, _calls = gh_json
    serve([_found(92, created=None), _found(61, "2026-01-01T00:00:00Z")])
    assert discover_pr("/home/me/dev/collins", "feat/x").number == 61


@pytest.mark.parametrize(
    "cwd,branch",
    [
        (None, "main"),  # no working directory yet
        ("/home/me/dev/collins", None),  # not a git repo
        ("/home/me/dev/collins", ""),  # detached head
        ("/home/me/dev/collins", "--version"),  # never let a flag be a branch
        ("/home/me/dev/collins", "-x"),
        ("/home/me/dev/collins", "main; rm -rf /"),
        ("/home/me/dev/collins", "$(id)"),
    ],
)
def test_discovery_asks_nothing_without_a_usable_branch(gh_json, cwd, branch):
    serve, calls = gh_json
    serve(_DISCOVERED)
    assert discover_pr(cwd, branch) is None
    assert calls == []


def test_discovery_allows_the_branch_names_git_allows(gh_json):
    serve, calls = gh_json
    serve(_DISCOVERED)
    for branch in ("main", "feat/pr-chip", "release-1.2", "user.name/fix+2"):
        assert discover_pr("/home/me/dev/collins", branch) is not None
    assert [args[3] for args, _cwd in calls] == [
        "main", "feat/pr-chip", "release-1.2", "user.name/fix+2",
    ]


_URL = "https://github.com/episode6/collins/pull/74"


@pytest.mark.parametrize(
    "reply",
    [
        None,  # gh failed: not logged in, not a repo, ...
        [],  # no PR for this branch
        {"number": 74, "url": _URL},  # an object where a list belongs
        [{}],
        [{"number": 74}],  # no url
        [{"url": _URL}],  # no number
        [{"number": True, "url": _URL}],  # bool is not a PR number
        [{"number": "74", "url": _URL}],
        [{"number": 74, "url": "https://github.com/episode6/collins/issues/74"}],
        [{"number": 74, "url": "not a url"}],
    ],
)
def test_discovery_degrades_to_no_pr(gh_json, reply):
    serve, _calls = gh_json
    serve(reply)
    assert discover_pr("/home/me/dev/collins", "main") is None


def test_discovery_is_skipped_once_gh_is_known_missing(gh_json, monkeypatch):
    serve, calls = gh_json
    serve(_DISCOVERED)
    monkeypatch.setattr(prstatus, "_gh_missing", True)
    assert discover_pr("/home/me/dev/collins", "main") is None
    assert calls == []


def test_discovery_passes_the_branch_as_an_argument(monkeypatch):
    """The real path down to argv: no shell, and the branch travels as one word
    behind --head rather than spliced into a string."""
    seen = []
    monkeypatch.setattr(prstatus.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(
        prstatus.subprocess, "run",
        lambda argv, **kw: seen.append((argv, kw)) or _completed(json.dumps(_DISCOVERED)),
    )
    assert discover_pr("/home/me/dev/collins", "main").number == 74
    argv, kwargs = seen[0]
    assert argv[:5] == ["/usr/bin/gh", "pr", "list", "--head", "main"]
    assert kwargs["cwd"] == "/home/me/dev/collins"
    assert "shell" not in kwargs


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
        "title": "Keep the yellow line on a backgrounded session",
        "isDraft": True,
        "state": "OPEN",
        "mergeable": "MERGEABLE",
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
        "state": "DRAFT",
        "checks": {"passed": 1, "failed": 0, "pending": 1},
        "title": "Keep the yellow line on a backgrounded session",
        "mergeable": "MERGEABLE",
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


# -- gh run for its effect (the PR menu's actions) --------------------------


def test_gh_run_reports_success_without_a_word(monkeypatch):
    seen = []
    monkeypatch.setattr(prstatus.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(
        prstatus.subprocess, "run",
        lambda argv, **kw: seen.append((argv, kw)) or _completed(),
    )
    assert prstatus.gh_run(["pr", "ready", URL]) == (True, "")
    argv, kwargs = seen[0]
    assert argv == ["/usr/bin/gh", "pr", "ready", URL]
    # A merge waits on GitHub doing the merge, so it gets longer than a status
    # fetch — and still can't hang the thread it runs on.
    assert kwargs["timeout"] == prstatus._GH_ACTION_TIMEOUT_S
    assert "shell" not in kwargs


def test_gh_run_hands_back_ghs_own_complaint(monkeypatch):
    """The menu item the user picked has to say why nothing happened, and gh
    already said it better than a generic message would."""
    failed = subprocess.CompletedProcess([], 1, stdout="", stderr="Pull request is not mergeable\n")
    monkeypatch.setattr(prstatus.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(prstatus.subprocess, "run", lambda *a, **kw: failed)
    assert prstatus.gh_run(["pr", "merge", URL]) == (False, "Pull request is not mergeable")


def test_gh_run_caps_what_github_can_put_in_a_dialog(monkeypatch):
    noisy = subprocess.CompletedProcess([], 1, stdout="", stderr="x" * 5000)
    monkeypatch.setattr(prstatus.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(prstatus.subprocess, "run", lambda *a, **kw: noisy)
    ok, message = prstatus.gh_run(["pr", "merge", URL])
    assert ok is False and len(message) == prstatus._MAX_GH_ERROR


def test_gh_run_says_so_when_gh_is_missing(monkeypatch):
    monkeypatch.setattr(prstatus.shutil, "which", lambda _: None)
    ok, message = prstatus.gh_run(["pr", "ready", URL])
    assert ok is False and "gh" in message


@pytest.mark.parametrize(
    "url,repository",
    [
        (URL, "episode6/collins"),
        ("https://github.enterprise.io/team/app/pull/3", "team/app"),
        ("https://github.com/episode6/collins/issues/55", None),
        ("--version", None),
        (None, None),
    ],
)
def test_repository_for_is_the_gate_every_gh_call_goes_through(url, repository):
    assert prstatus.repository_for(url) == repository


# -- chip rendering ---------------------------------------------------------


@pytest.mark.parametrize(
    "counts,badge",
    [
        ((None, None, None), None),  # nothing cached
        ((0, 0, 0), None),  # a PR with no checks configured earns no check mark
        ((3, 0, 0), "passed"),
        ((2, 1, 0), "failed"),  # a failure outranks passes
        ((2, 1, 4), "failed"),  # ...and outranks pending runs
        ((2, 0, 1), "pending"),  # runs still going outrank the passes so far
    ],
)
def test_badge_summarizes_checks(counts, badge):
    passed, failed, pending = counts
    pr = PullRequest(55, URL, passed=passed, failed=failed, pending=pending)
    assert pr.badge == badge


def test_a_conflict_takes_the_badge_from_pending_runs():
    """One badge slot: a branch GitHub can't merge is the thing to act on,
    whatever the runs still going come back with."""
    pr = PullRequest(55, URL, state="OPEN", passed=1, failed=0, pending=2,
                     mergeable="CONFLICTING")
    assert pr.badge == "conflict"


def test_a_failed_check_takes_the_badge_from_a_conflict():
    """A red build needs fixing whichever way the branch gets remerged."""
    pr = PullRequest(55, URL, state="OPEN", passed=1, failed=1, pending=0,
                     mergeable="CONFLICTING")
    assert pr.badge == "failed"


def test_a_clean_conflict_still_badges():
    pr = PullRequest(55, URL, state="DRAFT", passed=3, failed=0, pending=0,
                     mergeable="CONFLICTING")
    assert pr.badge == "conflict"


def test_a_merged_pr_drops_its_badge():
    """The purple base mark says it all — how CI went on the way in is history."""
    pr = PullRequest(55, URL, state="MERGED", passed=2, failed=1, pending=0)
    assert (pr.merged, pr.badge) == (True, None)


@pytest.mark.parametrize("state", [None, "OPEN", "DRAFT", "CLOSED", "SOMETHING_NEW"])
def test_every_other_state_keeps_the_badge(state):
    pr = PullRequest(55, URL, state=state, passed=2, failed=1, pending=0)
    assert (pr.merged, pr.badge) == (False, "failed")


# -- describe (the tooltip's long form) -------------------------------------


def test_describe_carries_slug_state_and_checks():
    pr = PullRequest(55, URL, "episode6/collins", state="DRAFT", passed=1, failed=1, pending=0)
    assert describe(pr) == "episode6/collins#55 · Draft pull request · 1 passed, 1 failed"


def test_describe_without_cached_status():
    assert describe(PullRequest(55, URL, "episode6/collins")) == "episode6/collins#55"


def test_describe_lists_pending_runs():
    pr = PullRequest(55, URL, "episode6/collins", state="OPEN", passed=2, failed=0, pending=3)
    assert describe(pr) == "episode6/collins#55 · Open pull request · 2 passed, 3 pending"


def test_describe_omits_zero_counts():
    pr = PullRequest(55, URL, None, state="MERGED", passed=0, failed=0, pending=0)
    assert describe(pr) == "#55 · Merged pull request"


def test_unknown_states_pass_through():
    assert state_text("SOMETHING_NEW") == "SOMETHING_NEW"


# -- persisting a session's PRs ---------------------------------------------


def test_record_keeps_identity_and_drops_status():
    """Status is refetched every run; writing it down would age on disk."""
    pr = PullRequest(55, URL, "episode6/collins", state="OPEN", passed=2, failed=1, pending=0)
    assert to_record(pr) == {"number": 55, "url": URL, "repository": "episode6/collins"}


def test_a_merged_pr_stays_merged_on_disk():
    """The one status that can't go stale, so the mark is up before gh answers."""
    pr = PullRequest(55, URL, "episode6/collins", state="MERGED", passed=2)
    assert to_record(pr)["state"] == "MERGED"
    assert from_record(to_record(pr)).merged is True


def test_forget_status_keeps_only_the_merge():
    pr = PullRequest(55, URL, "episode6/collins", state="MERGED", passed=2, failed=1, pending=3)
    assert forget_status(pr) == PullRequest(55, URL, "episode6/collins", state="MERGED")
    open_pr = replace(pr, state="OPEN")
    assert forget_status(open_pr) == PullRequest(55, URL, "episode6/collins")


def test_records_roundtrip_in_order():
    prs = [PullRequest(n, f"https://github.com/episode6/collins/pull/{n}") for n in (40, 55, 61)]
    assert [pr.number for pr in from_records(to_records(prs))] == [40, 55, 61]


def test_a_pr_that_cannot_be_refreshed_is_not_written():
    """An unfetchable URL on disk buys nothing and puts an unvalidated one there."""
    assert to_record(PullRequest(55, "not-a-url")) is None
    assert to_records([PullRequest(55, "not-a-url"), PullRequest(56, URL)]) == [
        {"number": 56, "url": URL}
    ]


@pytest.mark.parametrize(
    "record",
    [
        "not a record",
        {"url": URL},  # no number
        {"number": "55", "url": URL},
        {"number": True, "url": URL},  # bool is an int subclass
        {"number": 55},  # no url
        {"number": 55, "url": "https://github.com/episode6/collins/issues/55"},
        {"number": 55, "url": "--version"},  # would reach a gh argv
        {"number": 55, "url": "file:///etc/passwd"},
    ],
)
def test_untrustworthy_records_are_dropped(record):
    """Records start life in a transcript, and come back out to a browser and
    to gh: having written them ourselves earns no shortcut."""
    assert from_record(record) is None
    assert from_records([record, {"number": 56, "url": URL}]) == [PullRequest(56, URL)]


def test_from_records_tolerates_junk_in_place_of_a_list():
    assert from_records({"number": 55, "url": URL}) == []


# -- merge_ordered (saved list + transcript links) --------------------------


def _prs(*numbers, repo="episode6/collins"):
    return [PullRequest(n, f"https://github.com/{repo}/pull/{n}") for n in numbers]


def _numbers(prs):
    return [pr.number for pr in prs]


def test_merge_keeps_the_transcripts_order():
    assert _numbers(merge_ordered([], _prs(40, 55, 61))) == [40, 55, 61]


def test_merge_keeps_a_saved_pr_the_transcript_never_had():
    """A PR found by branch lookup lives only in the saved list — and it was
    found after the links around it, so it stays where it was saved."""
    assert _numbers(merge_ordered(_prs(40, 55, 61), _prs(40, 55))) == [40, 55, 61]


def test_merge_restores_links_that_aged_out_of_the_saved_list():
    """The saved list is capped, the transcript isn't: the older links come
    back ahead of the saved ones rather than after them."""
    saved, links = _prs(3, 4, 5), _prs(1, 2, 3, 4, 5)
    assert _numbers(merge_ordered(saved, links)) == [1, 2, 3, 4, 5]


def test_merge_places_a_saved_only_pr_between_the_links_it_sat_between():
    saved, links = _prs(40, 99, 61), _prs(40, 55, 61)
    assert _numbers(merge_ordered(saved, links)) == [40, 99, 55, 61]


def test_merge_is_idempotent():
    """The row re-merges its own output on every poll; it must settle."""
    once = merge_ordered(_prs(40, 99, 61), _prs(40, 55, 61))
    assert _numbers(merge_ordered(once, _prs(40, 55, 61))) == _numbers(once)


def test_merge_prefers_the_saved_copy_of_a_pr():
    """Saved carries what the row has learned since — a merge, most of all."""
    saved = [replace(_prs(55)[0], state="MERGED")]
    assert merge_ordered(saved, _prs(55))[0].merged is True


def test_merge_drops_duplicates_within_a_side():
    assert _numbers(merge_ordered(_prs(55, 55), _prs(55))) == [55]


# -- titles and the caret menu's lines ---------------------------------------


def test_enrich_fills_the_title(cache):
    cache({URL: {"state": "OPEN", "checks": {}, "title": "Track every PR"}})
    assert enrich(parse_pr_link(_link())).title == "Track every PR"


def test_enrich_keeps_a_title_the_entry_does_not_carry(cache):
    """The CLI's own cache has no title field; a warm start from it must not
    blank the title a gh reply (or the saved list) already supplied."""
    cache({URL: {"state": "OPEN", "checks": {"passed": 1}}})
    pr = replace(parse_pr_link(_link()), title="Track every PR")
    assert enrich(pr).title == "Track every PR"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Fix  the\n spinner", "Fix the spinner"),  # a repo's text, made one line
        ("   ", None),
        ("", None),
        (None, None),
        (42, None),
        ("x" * 500, "x" * 200),
    ],
)
def test_titles_are_taken_from_a_repository_carefully(raw, expected):
    assert prstatus._title(raw) == expected


def test_menu_name_is_the_title():
    pr = PullRequest(79, URL, "episode6/collins", title="Keep the yellow line")
    assert menu_name(pr) == "Keep the yellow line"


def test_menu_name_without_a_title_falls_back_to_the_repository():
    assert menu_name(PullRequest(79, URL, "episode6/collins")) == "episode6/collins"


def test_menu_name_without_anything_still_names_the_pr():
    assert menu_name(PullRequest(79, URL)) == "Pull request"


def test_describe_leads_with_the_title():
    pr = PullRequest(55, URL, "episode6/collins", title="Track every PR", state="OPEN")
    assert describe(pr) == "Track every PR · episode6/collins#55 · Open pull request"


def test_a_title_is_saved_and_read_back():
    """Not a status: a title doesn't go stale, and the menu wants it on the
    first frame rather than one gh call later."""
    pr = PullRequest(55, URL, "episode6/collins", title="Track every PR")
    assert to_record(pr)["title"] == "Track every PR"
    assert from_record(to_record(pr)).title == "Track every PR"


def test_forget_status_keeps_the_title():
    pr = PullRequest(55, URL, "episode6/collins", title="Track every PR", state="OPEN", passed=2)
    assert forget_status(pr) == PullRequest(55, URL, "episode6/collins", title="Track every PR")


def test_a_junk_title_on_disk_is_dropped():
    assert from_record({"number": 55, "url": URL, "title": ["not", "a", "title"]}).title is None


# -- resync (what opening a sidebar row's PR menu runs) ----------------------


@pytest.fixture
def gh_calls(monkeypatch):
    """Stub `gh pr view` per URL, recording every call. Returns (urls, replies):
    put an entry (or None) in *replies* under a URL to serve it."""
    urls: list[str] = []
    replies: dict[str, dict | None] = {}
    lock = threading.Lock()

    def run(url):
        with lock:  # resync fetches several at once
            urls.append(url)
        return replies.get(url)

    monkeypatch.setattr(prstatus, "_run_gh", run)
    return urls, replies


def _reply(title, passed=1):
    return {"state": "OPEN", "checks": {"passed": passed, "failed": 0, "pending": 0}, "title": title}


OTHER_URL = "https://github.com/episode6/collins/pull/56"


def test_resync_fetches_every_pr_and_keeps_the_order(gh_calls):
    urls, replies = gh_calls
    replies[URL] = _reply("Track every PR")
    replies[OTHER_URL] = _reply("Give the list room", passed=2)
    out = resync([PullRequest(55, URL), PullRequest(56, OTHER_URL)])
    assert sorted(urls) == [URL, OTHER_URL]
    assert [pr.number for pr in out] == [55, 56]
    assert [pr.title for pr in out] == ["Track every PR", "Give the list room"]
    assert [(pr.state, pr.passed) for pr in out] == [("OPEN", 1), ("OPEN", 2)]


def test_resync_refetches_a_status_that_is_still_fresh(gh_calls, clock):
    """The whole point of the button: what `enrich` would call current is a
    minute old, and someone has just asked to see it."""
    urls, replies = gh_calls
    replies[URL] = _reply("Track every PR")
    refresh(URL)
    urls.clear()
    assert resync([PullRequest(55, URL)])[0].passed == 1
    assert urls == [URL]


def test_resync_leaves_a_pr_alone_when_the_fetch_fails(gh_calls):
    """Offline, and the row still reads: the title it was saved with stays."""
    pr = PullRequest(55, URL, "episode6/collins", title="Track every PR")
    assert resync([pr]) == [pr]


def test_resync_does_not_spend_a_fetch_on_a_merged_pr(gh_calls):
    urls, _replies = gh_calls
    pr = PullRequest(55, URL, title="Track every PR", state="MERGED")
    assert resync([pr]) == [pr]
    assert urls == []


def test_resync_fetches_a_merged_pr_that_never_learned_its_title(gh_calls):
    urls, replies = gh_calls
    replies[URL] = {"state": "MERGED", "checks": {}, "title": "Track every PR"}
    out = resync([PullRequest(55, URL, state="MERGED")])
    assert urls == [URL]
    assert out[0].title == "Track every PR"
    assert out[0].merged


def test_resync_never_hands_an_unfetchable_url_to_gh(gh_calls):
    urls, _replies = gh_calls
    pr = PullRequest(55, "--version")
    assert resync([pr]) == [pr]
    assert urls == []


def test_resync_fetches_nothing_once_gh_is_known_missing(gh_calls, monkeypatch):
    urls, _replies = gh_calls
    monkeypatch.setattr(prstatus, "_gh_missing", True)
    assert resync([PullRequest(55, URL)]) == [PullRequest(55, URL)]
    assert urls == []


# -- mergeability ------------------------------------------------------------


def test_a_definite_mergeable_verdict_is_kept():
    entry = prstatus._entry({"state": "OPEN", "mergeable": "CONFLICTING"})
    assert entry["mergeable"] == "CONFLICTING"


@pytest.mark.parametrize("value", ["UNKNOWN", "", None, 7])
def test_an_indefinite_mergeable_verdict_is_no_answer(value):
    """GitHub computes mergeability lazily; gh says UNKNOWN until it lands.
    That is "not known yet", never a state to show."""
    assert prstatus._entry({"state": "OPEN", "mergeable": value})["mergeable"] is None


def test_enrich_fills_mergeability(gh):
    gh({"state": "OPEN", "checks": {"passed": 1, "failed": 0, "pending": 0},
        "mergeable": "CONFLICTING"})
    refresh(URL)
    pr = enrich(parse_pr_link(_link()))
    assert pr.mergeable == "CONFLICTING"
    assert pr.conflicting is True


def test_the_cli_cache_carries_no_mergeability(cache):
    """Its entries predate the field, so a warm start says "not known yet"."""
    cache({URL: {"state": "OPEN", "checks": {"passed": 1, "failed": 0, "pending": 0}}})
    pr = enrich(parse_pr_link(_link()))
    assert pr.mergeable is None
    assert pr.conflicting is False


@pytest.mark.parametrize("state", ["MERGED", "CLOSED", None])
def test_only_a_live_pr_counts_as_conflicting(state):
    """Whatever gh reports, a PR that is going nowhere has no conflict worth
    showing."""
    pr = PullRequest(55, URL, state=state, mergeable="CONFLICTING")
    assert pr.conflicting is False


def test_describe_names_a_conflict():
    pr = PullRequest(55, URL, "episode6/collins", state="OPEN",
                     mergeable="CONFLICTING", passed=2, failed=0, pending=0)
    assert describe(pr) == (
        "episode6/collins#55 · Open pull request · Has merge conflicts · 2 passed"
    )


def test_forget_status_drops_mergeability():
    """Whether a branch still merges goes stale with every push to either
    side, so it is refetched, never remembered."""
    pr = PullRequest(55, URL, "episode6/collins", state="OPEN", mergeable="CONFLICTING")
    assert forget_status(pr).mergeable is None
