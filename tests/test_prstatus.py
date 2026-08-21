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
    combined_badge,
    combined_state,
    describe,
    describe_all,
    discover_pr,
    enrich,
    from_record,
    from_records,
    invalidate,
    known,
    lookup_pr,
    menu_name,
    merge_ordered,
    newest_title,
    newest_titled,
    parse_pr_link,
    parse_pr_url,
    refresh,
    resync,
    state_text,
    sweep,
    to_record,
    to_records,
)

URL = "https://github.com/episode6/collins/pull/55"
_TTL_S = prstatus._TTL_S
_ERROR_TTL_S = prstatus._ERROR_TTL_S
_SETTLED_TTL_S = prstatus._SETTLED_TTL_S
_PROBE_S = prstatus._PROBE_S
HEAD = "271949c0f3b7a8d0e2c4b6a1f9e8d7c6b5a4f3e2"


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
    monkeypatch.setattr(prstatus, "_schedule_probe", lambda url: None)
    monkeypatch.setattr(prstatus, "_gh_missing", False)
    monkeypatch.setattr(prstatus, "_viewer", "")  # the run's remembered login
    prstatus._statuses.clear()
    prstatus._inflight.clear()
    prstatus._probes.clear()
    yield urls
    prstatus._statuses.clear()
    prstatus._inflight.clear()
    prstatus._probes.clear()


@pytest.fixture
def probed(monkeypatch):
    """Collects the URLs probes were scheduled for (see prstatus.probe)."""
    urls: list[str] = []
    monkeypatch.setattr(prstatus, "_schedule_probe", urls.append)
    return urls


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


# -- parse_pr_url -----------------------------------------------------------


def test_parse_pr_url_reads_number_and_repository_off_the_url():
    pr = parse_pr_url(URL)
    assert pr == PullRequest(number=55, url=URL, repository="episode6/collins")
    assert pr.title is None and pr.state is None  # left for the next fetch


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://github.com/episode6/collins/issues/55",
        "https://github.com/episode6/collins/pull/55/files",
        "https://github.com/episode6/collins/pull/",
        "http://github.com/episode6/collins/pull/55",  # https only
        "--version",  # the argv-injection shape _FETCHABLE exists for
        None,
        55,
    ],
)
def test_parse_pr_url_rejects_anything_not_shaped_like_a_pr_page(url):
    assert parse_pr_url(url) is None


def test_parse_pr_url_accepts_what_a_record_round_trip_accepts():
    """A URL accepted here must be one to_record will persist — attach_pr
    hands its result straight down the tab's write to the PR hub."""
    assert to_record(parse_pr_url(URL)) is not None


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


# -- the TTL follows the PR's state; running checks get probed --------------


def _running(**overrides):
    """A stored status for a live PR whose checks are still running."""
    entry = {"state": "OPEN", "checks": {"passed": 1, "failed": 0, "pending": 1}, "head": HEAD}
    entry.update(overrides)
    return entry


def test_a_settled_pr_is_refetched_far_less_often(scheduled, clock, gh):
    """Nothing about a merged PR changes short of a reopen, so a poll that
    refetched it every minute would be spending gh on history."""
    gh({"state": "MERGED", "checks": {"passed": 3, "failed": 0, "pending": 0}})
    refresh(URL)
    scheduled.clear()
    clock.advance(_TTL_S * 3)
    enrich(parse_pr_link(_link()))
    assert scheduled == []
    clock.advance(_SETTLED_TTL_S)
    enrich(parse_pr_link(_link()))
    assert scheduled == [URL]


def test_a_pr_with_running_checks_is_probed_between_fetches(scheduled, probed, clock, gh):
    """The minute between fetches is filled with cheap probes: the first one
    right after the fetch (it only learns the ETag), then one per interval."""
    gh(_running())
    refresh(URL)
    enrich(parse_pr_link(_link()))
    assert scheduled == [] and probed == [URL]
    prstatus._probes[URL] = (clock(), HEAD, 'W/"a"')
    prstatus._inflight.clear()  # the probe landed
    clock.advance(_PROBE_S - 1)
    enrich(parse_pr_link(_link()))
    assert probed == [URL]  # not yet
    clock.advance(1)
    enrich(parse_pr_link(_link()))
    assert probed == [URL, URL]


@pytest.mark.parametrize(
    "entry",
    [
        _running(checks={"passed": 2, "failed": 0, "pending": 0}),  # nothing running
        _running(state="MERGED"),  # settled, whatever its checks say
        _running(state="CLOSED"),
        _running(head=None),  # a warm start: nothing to ask about
        None,  # a failed fetch
    ],
)
def test_only_a_live_pr_with_running_checks_is_probed(scheduled, probed, clock, gh, entry):
    gh(entry)
    refresh(URL)
    enrich(parse_pr_link(_link()))
    assert probed == []


def test_a_due_fetch_outranks_a_probe(scheduled, probed, clock, gh):
    """At the minute mark the full fetch goes out, not another probe."""
    gh(_running())
    refresh(URL)
    clock.advance(_TTL_S)
    enrich(parse_pr_link(_link()))
    assert scheduled == [URL] and probed == []


def test_one_probe_in_flight_per_pr(scheduled, probed, clock, gh):
    gh(_running())
    refresh(URL)
    enrich(parse_pr_link(_link()))
    enrich(parse_pr_link(_link()))
    assert probed == [URL]


@pytest.fixture
def check_runs(monkeypatch):
    """Stub the conditional check-runs call; returns (setter, calls)."""
    calls: list[tuple] = []
    answer = {"value": (None, False)}

    def fake(host, repository, head, etag):
        calls.append((host, repository, head, etag))
        return answer["value"]

    monkeypatch.setattr(prstatus, "_check_runs", fake)

    def serve(etag, changed):
        answer["value"] = (etag, changed)

    return serve, calls


def test_a_probe_that_finds_nothing_new_fetches_nothing(
    scheduled, clock, gh, check_runs, monkeypatch
):
    serve, calls = check_runs
    gh(_running())
    refresh(URL)
    fetches = []
    monkeypatch.setattr(prstatus, "_run_gh", lambda url: fetches.append(url) or _running())
    serve('W/"a"', False)
    prstatus._inflight.add(URL)
    prstatus.probe(URL)
    assert fetches == []
    assert URL not in prstatus._inflight
    assert prstatus._probes[URL] == (clock(), HEAD, 'W/"a"')
    assert calls == [("github.com", "episode6/collins", HEAD, None)]


def test_a_probe_that_finds_a_change_fetches_in_full(scheduled, clock, gh, check_runs):
    """A changed ETag is the whole point: the full status is fetched right
    then, and what it says reaches the listeners like any other fetch."""
    serve, calls = check_runs
    gh(_running())
    refresh(URL)
    prstatus._probes[URL] = (clock(), HEAD, 'W/"a"')
    gh(_running(checks={"passed": 2, "failed": 0, "pending": 0}))
    heard = []
    prstatus.add_listener(heard.append)
    try:
        serve('W/"b"', True)
        prstatus._inflight.add(URL)
        prstatus.probe(URL)
    finally:
        prstatus.remove_listener(heard.append)
    assert heard == [URL]
    assert enrich(parse_pr_link(_link())).pending == 0
    assert URL not in prstatus._inflight
    assert calls == [("github.com", "episode6/collins", HEAD, 'W/"a"')]


def test_a_probe_sends_the_etag_only_for_the_same_head(scheduled, clock, gh, check_runs):
    """An ETag describes one commit's check-runs. After a push the head is a
    different commit, and comparing against the old tag would read as a
    change that the fetch reporting the push already covered."""
    serve, calls = check_runs
    gh(_running())
    refresh(URL)
    serve('W/"a"', False)
    prstatus.probe(URL)
    prstatus.probe(URL)
    pushed = "f" * 40
    gh(_running(head=pushed))
    refresh(URL)
    serve('W/"z"', False)
    prstatus.probe(URL)
    assert [call[3] for call in calls] == [None, 'W/"a"', None]
    assert prstatus._probes[URL][1:] == (pushed, 'W/"z"')


def test_a_probe_keeps_its_etag_through_an_unanswered_ask(scheduled, clock, gh, check_runs):
    serve, calls = check_runs
    gh(_running())
    refresh(URL)
    prstatus._probes[URL] = (clock(), HEAD, 'W/"a"')
    serve(None, False)  # offline, say
    prstatus.probe(URL)
    assert prstatus._probes[URL] == (clock(), HEAD, 'W/"a"')


def test_a_probe_that_blows_up_lets_go_of_the_pr(scheduled, monkeypatch):
    def boom(url):
        raise RuntimeError("boom")

    monkeypatch.setattr(prstatus, "_run_probe", boom)
    prstatus._inflight.add(URL)
    prstatus.probe(URL)
    assert URL not in prstatus._inflight


def test_a_probe_of_an_unknown_pr_asks_nothing(scheduled, check_runs):
    serve, calls = check_runs
    prstatus.probe(URL)
    assert calls == [] and URL not in prstatus._probes


def _done(stdout="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def _api_reply(status, etag=None, body="{}"):
    lines = [f"HTTP/2.0 {status}", "Access-Control-Allow-Origin: *"]
    if etag is not None:
        lines.append(f"Etag: {etag}")
    lines += ["X-Ratelimit-Remaining: 4976", "", body]
    return "\r\n".join(lines)


@pytest.mark.parametrize(
    "sent,reply,expected",
    [
        # nothing changed: gh exits 1 for the 304, and the tag is kept
        ('W/"a"', _done(_api_reply("304 Not Modified", '"a"'), 1), ('"a"', False)),
        # changed: a 200 with a different tag
        ('W/"a"', _done(_api_reply("200 OK", 'W/"b"')), ('W/"b"', True)),
        # the first ask has nothing to compare to
        (None, _done(_api_reply("200 OK", 'W/"b"')), ('W/"b"', False)),
        # a 200 that somehow lost its tag can't be compared either
        ('W/"a"', _done(_api_reply("200 OK")), (None, False)),
        # failures of every shape are "no"
        ('W/"a"', _done(_api_reply("404 Not Found", 'W/"x"'), 1), (None, False)),
        ('W/"a"', _done("", 1), (None, False)),
        ('W/"a"', _done("not http at all"), (None, False)),
        ('W/"a"', subprocess.TimeoutExpired("gh", 10), (None, False)),
        ('W/"a"', OSError("boom"), (None, False)),
    ],
)
def test_check_runs_reads_the_reply_headers(monkeypatch, sent, reply, expected):
    def run(*_args, **_kwargs):
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(prstatus.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(prstatus.subprocess, "run", run)
    assert prstatus._check_runs("github.com", "episode6/collins", HEAD, sent) == expected


def test_check_runs_asks_conditionally_on_the_right_host(monkeypatch):
    seen = []

    def run(argv, **_kwargs):
        seen.append(argv[1:])
        return _done(_api_reply("304 Not Modified", '"a"'), 1)

    monkeypatch.setattr(prstatus.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(prstatus.subprocess, "run", run)
    prstatus._check_runs("ghe.example.com", "org/repo", HEAD, 'W/"a"')
    assert seen == [[
        "api", "-i", "--hostname", "ghe.example.com",
        f"repos/org/repo/commits/{HEAD}/check-runs?per_page={prstatus._GH_PAGE}",
        "-H", 'If-None-Match: W/"a"',
    ]]


def test_check_runs_sends_no_condition_on_the_first_ask(monkeypatch):
    seen = []

    def run(argv, **_kwargs):
        seen.append(argv[1:])
        return _done(_api_reply("200 OK", 'W/"a"'))

    monkeypatch.setattr(prstatus.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(prstatus.subprocess, "run", run)
    prstatus._check_runs("github.com", "org/repo", HEAD, None)
    assert "-H" not in seen[0]


@pytest.mark.parametrize(
    "value,head",
    [
        (HEAD, HEAD),
        ("271949c", None),  # abbreviated: not what gh reports
        ("x" * 40, None),
        ("", None),
        (None, None),
        (7, None),
    ],
)
def test_only_a_full_commit_id_is_kept_as_the_head(value, head):
    assert prstatus._sha(value) == head


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


# -- lookup_pr -----------------------------------------------------------------


def test_lookup_answers_with_ghs_own_url(gh_json):
    serve, calls = gh_json
    serve(
        {
            "url": "https://github.com/episode6/collins/pull/74",
            "title": "A fine change",
            "state": "OPEN",
            "isDraft": False,
            "statusCheckRollup": [{"conclusion": "SUCCESS"}],
        }
    )
    pr = lookup_pr(("74", "--repo", "episode6/collins"))
    assert (pr.number, pr.repository) == (74, "episode6/collins")
    assert (pr.title, pr.state, pr.passed) == ("A fine change", "OPEN", 1)
    args, cwd = calls[0]
    assert args[:3] == ["pr", "view", "74"]
    assert args[args.index("--json") + 1].startswith("url,")  # identity rides the reply
    assert cwd is None


def test_lookup_runs_a_bare_number_in_its_directory(gh_json):
    serve, calls = gh_json
    serve({"url": "https://github.com/episode6/collins/pull/74", "state": "OPEN"})
    assert lookup_pr(("74",), cwd="/home/me/dev/collins").number == 74
    assert calls[0][1] == "/home/me/dev/collins"


def test_lookup_status_counts_as_ours(gh_json, scheduled):
    """The lookup's one call answered "how is its CI?" too; the poll that
    follows must not refetch it."""
    serve, _calls = gh_json
    serve(
        {
            "url": "https://github.com/episode6/collins/pull/74",
            "state": "OPEN",
            "statusCheckRollup": [{"conclusion": "FAILURE"}],
        }
    )
    pr = lookup_pr(("74", "--repo", "episode6/collins"))
    scheduled.clear()
    assert enrich(pr).badge == "failed"
    assert scheduled == []


def test_lookup_none_when_gh_cannot_answer(gh_json):
    serve, _calls = gh_json
    serve(None)  # no gh, not logged in, an issue number, ...
    assert lookup_pr(("74", "--repo", "episode6/collins")) is None


def test_lookup_rejects_an_answer_outside_the_gate(gh_json):
    serve, _calls = gh_json
    serve({"url": "https://example.com/not/a/pr", "state": "OPEN"})
    assert lookup_pr(("74", "--repo", "episode6/collins")) is None


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
        "comments": [
            {"author": {"login": "claude"}, "viewerDidAuthor": False, "isMinimized": False,
             "createdAt": "2026-08-13T12:00:00Z"},
        ],
        "commits": [
            {"oid": "271949c", "committedDate": "2026-08-13T09:30:00Z"},
        ],
        "autoMergeRequest": None,
        "headRefOid": HEAD,
    })
    monkeypatch.setattr(prstatus.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(prstatus.subprocess, "run", lambda *a, **kw: _completed(reply))
    assert prstatus._run_gh(URL) == {
        "state": "DRAFT",
        "checks": {"passed": 1, "failed": 0, "pending": 1},
        "title": "Keep the yellow line on a backgrounded session",
        "mergeable": "MERGEABLE",
        "unresolved": True,
        "claude_replied": True,
        "pushed_since": False,
        "auto_merge": False,
        "head": HEAD,
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


def test_check_verdict_names_the_badge():
    assert prstatus.check_verdict({"conclusion": "SUCCESS"}) == prstatus.BADGE_PASSED
    assert prstatus.check_verdict({"state": "ERROR"}) == prstatus.BADGE_FAILED
    assert prstatus.check_verdict({"conclusion": None, "status": "QUEUED"}) \
        == prstatus.BADGE_PENDING


# -- raw-output and stdin transport (the detail view's half) ------------------


def test_gh_text_hands_back_raw_stdout(monkeypatch):
    seen = []
    monkeypatch.setattr(prstatus.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(
        prstatus.subprocess, "run",
        lambda argv, **kw: seen.append((argv, kw)) or _completed("diff --git a/x b/x\n"),
    )
    assert prstatus.gh_text(["pr", "diff", URL]) == "diff --git a/x b/x\n"
    argv, kwargs = seen[0]
    assert argv[1:] == ["pr", "diff", URL]
    assert kwargs["timeout"] == prstatus._GH_ACTION_TIMEOUT_S  # a diff-sized wait
    assert kwargs["errors"] == "replace"  # a diff carries whatever bytes the repo does
    assert "shell" not in kwargs


def test_gh_json_takes_a_timeout(monkeypatch):
    """The poll budget stays the default; an on-demand caller can wait longer."""
    seen = []
    monkeypatch.setattr(prstatus.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(
        prstatus.subprocess, "run",
        lambda argv, **kw: seen.append(kw) or _completed("{}"),
    )
    assert prstatus.gh_json(["pr", "view", URL]) == {}
    assert prstatus.gh_json(
        ["pr", "view", URL], timeout=prstatus._GH_ACTION_TIMEOUT_S) == {}
    assert [kw["timeout"] for kw in seen] \
        == [prstatus._GH_TIMEOUT_S, prstatus._GH_ACTION_TIMEOUT_S]


def test_gh_text_degrades_on_failure(monkeypatch):
    monkeypatch.setattr(prstatus.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(prstatus.subprocess, "run", lambda *a, **kw: _completed("", 1))
    assert prstatus.gh_text(["pr", "diff", URL]) is None


def test_gh_text_drops_an_oversized_reply(monkeypatch):
    monkeypatch.setattr(prstatus.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(prstatus.subprocess, "run", lambda *a, **kw: _completed("x" * 100))
    assert prstatus.gh_text(["pr", "diff", URL], max_bytes=99) is None
    assert prstatus.gh_text(["pr", "diff", URL], max_bytes=100) == "x" * 100


def test_gh_bytes_never_decodes_the_reply(monkeypatch):
    """An image blob is not text: decoding it with replacement would hand back
    something that is no longer the file (see prblobs)."""
    seen = []
    png = b"\x89PNG\r\n\x1a\n\xff\xfe\x00"
    monkeypatch.setattr(prstatus.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(
        prstatus.subprocess, "run",
        lambda argv, **kw: seen.append((argv, kw))
        or subprocess.CompletedProcess([], 0, stdout=png, stderr=b""),
    )
    assert prstatus.gh_bytes(["api", "repos/o/n/contents/x.png"]) == png
    argv, kwargs = seen[0]
    assert argv[1:] == ["api", "repos/o/n/contents/x.png"]
    assert "text" not in kwargs and "errors" not in kwargs
    assert kwargs["timeout"] == prstatus._GH_ACTION_TIMEOUT_S


def test_gh_bytes_degrades_on_failure(monkeypatch):
    """Including the failure with a bytes stderr — a 404 for a blob that isn't
    at that commit is the everyday case, and must never raise."""
    monkeypatch.setattr(prstatus.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(
        prstatus.subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            [], 1, stdout=b"", stderr=b"gh: Not Found (HTTP 404)"),
    )
    assert prstatus.gh_bytes(["api", "repos/o/n/contents/x.png"]) is None


def test_gh_bytes_drops_an_oversized_blob(monkeypatch):
    monkeypatch.setattr(prstatus.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(
        prstatus.subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            [], 0, stdout=b"x" * 100, stderr=b""),
    )
    assert prstatus.gh_bytes(["api", "x"], max_bytes=99) is None
    assert prstatus.gh_bytes(["api", "x"], max_bytes=100) == b"x" * 100


def test_gh_run_feeds_stdin_rather_than_argv(monkeypatch):
    """A comment body must never be an argv entry; it travels as gh's input."""
    seen = []
    monkeypatch.setattr(prstatus.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(
        prstatus.subprocess, "run",
        lambda argv, **kw: seen.append((argv, kw)) or _completed(),
    )
    ok, message = prstatus.gh_run(
        ["pr", "comment", URL, "--body-file", "-"], stdin="A comment body.\n")
    assert (ok, message) == (True, "")
    argv, kwargs = seen[0]
    assert kwargs["input"] == "A comment body.\n"
    assert "A comment body.\n" not in argv


# -- who is signed in (what tells a PR page it is looking at your own PR) -----


def test_viewer_login_is_asked_once_and_kept(monkeypatch):
    calls = []
    monkeypatch.setattr(
        prstatus, "gh_json",
        lambda args, cwd=None, timeout=None: calls.append(args) or {"login": "ghackett"},
    )
    assert prstatus.viewer_login() == "ghackett"
    assert prstatus.viewer_login() == "ghackett"
    assert calls == [["api", "user"]]  # the second answer came out of the run's memory


def test_viewer_login_doesnt_remember_a_failure(monkeypatch):
    """Offline, signed out, or a reply that isn't a user: all of them are
    states that get better, so the next caller asks again."""
    replies = [None, {"login": ""}, {"login": 7}, {"login": "x" * 200}, {"login": "gh"}]
    monkeypatch.setattr(
        prstatus, "gh_json", lambda args, cwd=None, timeout=None: replies.pop(0)
    )
    assert [prstatus.viewer_login() for _ in range(4)] == ["", "", "", ""]
    assert prstatus.viewer_login() == "gh"


def test_viewer_login_stays_off_a_machine_without_gh(monkeypatch):
    calls = []
    monkeypatch.setattr(prstatus, "_gh_missing", True)
    monkeypatch.setattr(
        prstatus, "gh_json", lambda args, cwd=None, timeout=None: calls.append(args)
    )
    assert prstatus.viewer_login() == ""
    assert calls == []


# -- absorbing a detail fetch -------------------------------------------------

# Shaped like the detail view's `gh pr view --json` reply (prdetail): a strict
# superset of what the summary fetch asks for, extra fields and all.
_DETAIL_REPLY = {
    "number": 55,
    "url": URL,
    "title": "Delete hidden sessions",
    "state": "OPEN",
    "isDraft": False,
    "body": "The full description — a field the summary fetch never asks for.",
    "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "COMPLETED"}],
    "mergeable": "MERGEABLE",
    "comments": [{"viewerDidAuthor": False, "isMinimized": False}],
}


def test_absorb_counts_as_a_fetch_of_our_own():
    prstatus.absorb(URL, _DETAIL_REPLY)
    pr = known(PullRequest(number=55, url=URL))
    assert pr.title == "Delete hidden sessions"
    assert (pr.state, pr.passed, pr.mergeable) == ("OPEN", 1, "MERGEABLE")
    assert pr.unresolved is True


def test_absorb_stamps_the_ttl(scheduled, clock):
    """The poll right after opening the view must not refetch what the view
    just loaded — and the TTL after that, polling resumes as normal."""
    prstatus.absorb(URL, _DETAIL_REPLY)
    pr = enrich(PullRequest(number=55, url=URL))
    assert pr.title == "Delete hidden sessions"
    assert scheduled == []
    clock.advance(_TTL_S)
    enrich(pr)
    assert scheduled == [URL]


def test_absorb_ignores_what_it_cannot_use():
    prstatus.absorb("https://example.com/not-a-pr", _DETAIL_REPLY)
    prstatus.absorb(URL, "junk")
    prstatus.absorb(URL, None)
    assert prstatus._statuses == {}


def test_summarize_reduces_a_detail_reply():
    pr = prstatus.summarize(URL, _DETAIL_REPLY)
    assert (pr.number, pr.repository) == (55, "episode6/collins")
    assert pr.title == "Delete hidden sessions"
    assert (pr.passed, pr.failed, pr.pending) == (1, 0, 0)
    assert pr.unresolved is True
    assert prstatus._statuses == {}  # pure: absorb is the writing half


def test_summarize_rejects_what_is_not_a_pr():
    assert prstatus.summarize("https://example.com/x", _DETAIL_REPLY) is None
    assert prstatus.summarize(URL, []) is None


# -- listeners (what the PR hub fans out — see prstore) -----------------------


def test_a_changed_fetch_notifies_listeners(scheduled, gh):
    heard = []
    prstatus.add_listener(heard.append)
    gh({"state": "OPEN", "checks": {"passed": 1, "failed": 0, "pending": 0}})
    prstatus.refresh(URL)
    assert heard == [URL]


def test_a_fetch_that_came_back_the_same_says_nothing(scheduled, gh):
    heard = []
    prstatus.add_listener(heard.append)
    gh({"state": "OPEN", "checks": {"passed": 1, "failed": 0, "pending": 0}})
    prstatus.refresh(URL)
    prstatus.refresh(URL)
    assert heard == [URL]


def test_a_failed_fetch_over_a_real_entry_is_still_news(scheduled, gh):
    """The entry moved — a widget reading `known` now gets the saved record's
    status back instead of the fetched one, so it deserves the redraw."""
    heard = []
    prstatus.add_listener(heard.append)
    prstatus.absorb(URL, _DETAIL_REPLY)
    gh(None)
    prstatus.refresh(URL)
    assert heard == [URL, URL]


def test_a_broken_listener_neither_breaks_the_write_nor_its_peers(scheduled):
    def explode(_url):
        raise RuntimeError("boom")

    heard = []
    prstatus.add_listener(explode)
    prstatus.add_listener(heard.append)
    prstatus.absorb(URL, _DETAIL_REPLY)
    assert heard == [URL]
    assert known(PullRequest(number=55, url=URL)).state == "OPEN"


def test_a_removed_listener_hears_nothing_more(scheduled):
    heard = []
    prstatus.add_listener(heard.append)
    prstatus.remove_listener(heard.append)
    prstatus.absorb(URL, _DETAIL_REPLY)
    assert heard == []


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
    "state,merged,closed,settled",
    [
        ("MERGED", True, False, True),
        ("CLOSED", False, True, True),
        ("OPEN", False, False, False),
        ("DRAFT", False, False, False),
        (None, False, False, False),  # unfetched: unknown, not finished
        ("SOMETHING_NEW", False, False, False),
    ],
)
def test_a_pr_knows_how_it_ended(state, merged, closed, settled):
    pr = PullRequest(55, URL, state=state)
    assert (pr.merged, pr.closed, pr.settled) == (merged, closed, settled)


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


@pytest.mark.parametrize("state", ["MERGED", "CLOSED"])
def test_a_settled_pr_drops_its_badge(state):
    """The base mark says it all — purple or red, how CI went on the way in or
    out is history."""
    pr = PullRequest(55, URL, state=state, passed=2, failed=1, pending=0)
    assert (pr.settled, pr.badge) == (True, None)


@pytest.mark.parametrize("state", [None, "OPEN", "DRAFT", "SOMETHING_NEW"])
def test_every_live_state_keeps_the_badge(state):
    """Including the states we've never heard of: unknown isn't finished."""
    pr = PullRequest(55, URL, state=state, passed=2, failed=1, pending=0)
    assert (pr.settled, pr.badge) == (False, "failed")


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


def test_a_record_carries_the_whole_pr():
    """Status is saved with it, so a mark reads as the last answer rather than
    as "nothing known" until the run's first fetch."""
    pr = PullRequest(55, URL, "episode6/collins", title="Track every PR", state="OPEN",
                     passed=2, failed=1, pending=0, mergeable="CONFLICTING", unresolved=True)
    assert to_record(pr) == {
        "number": 55,
        "url": URL,
        "repository": "episode6/collins",
        "title": "Track every PR",
        "state": "OPEN",
        "checks": {"passed": 2, "failed": 1, "pending": 0},
        "mergeable": "CONFLICTING",
        "unresolved": True,
    }
    assert from_record(to_record(pr)) == pr


def test_a_record_carries_auto_merge_too():
    """The button the page draws follows it (see practions.header_actions), so
    it survives a restart the same way the check counts do."""
    pr = PullRequest(55, URL, "episode6/collins", state="OPEN", pending=1, auto_merge=True)
    assert to_record(pr)["auto_merge"] is True
    assert from_record(to_record(pr)) == pr
    assert "auto_merge" not in to_record(PullRequest(55, URL, state="OPEN"))
    assert from_record({"number": 55, "url": URL, "auto_merge": "yes"}).auto_merge is False


def test_a_record_says_only_what_was_known():
    """Nothing fetched yet: no state, no counts, no null-filled keys either."""
    assert to_record(PullRequest(55, URL)) == {"number": 55, "url": URL}
    assert from_record(to_record(PullRequest(55, URL))) == PullRequest(55, URL)


@pytest.mark.parametrize("state", ["OPEN", "DRAFT", "MERGED", "CLOSED"])
def test_every_state_gh_reports_survives_the_run(state):
    pr = PullRequest(55, URL, "episode6/collins", state=state, passed=2)
    assert to_record(pr)["state"] == state
    assert from_record(to_record(pr)).state == state


@pytest.mark.parametrize("state", ["SOMETHING_NEW", 7, "", None, True])
def test_a_state_nothing_recognizes_is_not_read_back(state):
    """A saved list is a file, and a mark gets built out of whatever it says."""
    assert from_record({"number": 55, "url": URL, "state": state}).state is None
    assert to_record(PullRequest(55, URL, state=state)).get("state") is None


@pytest.mark.parametrize(
    "checks",
    [
        "lots",
        {"passed": "3"},
        {"passed": True},  # bool is an int subclass
        {"passed": -1},
        {"passed": 10_000},  # not a count of anything about one PR
    ],
)
def test_saved_check_counts_are_revalidated(checks):
    assert from_record({"number": 55, "url": URL, "checks": checks}).passed is None


def test_saved_mergeability_and_comments_are_revalidated():
    record = {"number": 55, "url": URL, "mergeable": "PROBABLY", "unresolved": "yes"}
    restored = from_record(record)
    assert (restored.mergeable, restored.unresolved) == (None, False)


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


def test_newest_title_is_the_last_titled_records():
    """Two titled PRs: the later one is what the session gets renamed to."""
    records = [
        {"number": 40, "url": "https://github.com/episode6/collins/pull/40", "title": "Old work"},
        {"number": 55, "url": URL, "title": "New work"},
    ]
    assert newest_title(records) == "New work"


def test_newest_title_skips_untitled_records():
    """A fresh pr-link has no title until gh answers; it must not blank the
    name a titled predecessor already provided."""
    records = [
        {"number": 40, "url": "https://github.com/episode6/collins/pull/40", "title": "Old work"},
        {"number": 55, "url": URL},
    ]
    assert newest_title(records) == "Old work"


def test_newest_title_none_without_any_title():
    assert newest_title([{"number": 55, "url": URL}]) is None
    assert newest_title([]) is None
    assert newest_title("junk") is None


def test_newest_titled_is_the_whole_pull_request():
    """What the row menu's "Rename to match PR" reads: it names the PR the
    rename would use, so a title on its own isn't enough."""
    records = [
        {"number": 40, "url": "https://github.com/episode6/collins/pull/40", "title": "Old work"},
        {"number": 55, "url": URL, "title": "New work"},
        {"number": 56, "url": "https://github.com/episode6/collins/pull/56"},
    ]
    pr = newest_titled(records)
    assert pr is not None
    assert (pr.number, pr.title) == (55, "New work")


def test_newest_titled_none_without_any_title():
    assert newest_titled([{"number": 55, "url": URL}]) is None
    assert newest_titled([]) is None
    assert newest_titled("junk") is None


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


def test_resync_does_not_spend_a_fetch_on_a_merged_pr(gh_calls, scheduled):
    urls, _replies = gh_calls
    pr = PullRequest(55, URL, title="Track every PR", state="MERGED")
    assert resync([pr]) == [pr]
    assert urls == []
    assert scheduled == []  # nor one in the background on the way out


def test_resync_still_asks_about_a_closed_pr(gh_calls):
    """Closing is reversible: a reopened PR must not need a restart to show,
    so only a merge buys a PR out of the fetch."""
    urls, replies = gh_calls
    replies[URL] = {"state": "OPEN", "checks": {}, "title": "Track every PR"}
    out = resync([PullRequest(55, URL, title="Track every PR", state="CLOSED")])
    assert urls == [URL]
    assert out[0].state == "OPEN"


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


def test_mergeability_survives_the_run():
    """Stale beats blank: a conflict that was there last night is the best
    answer available until the next fetch says otherwise."""
    pr = PullRequest(55, URL, "episode6/collins", state="OPEN", mergeable="CONFLICTING")
    assert from_record(to_record(pr)).conflicting is True


# -- unresolved comments -----------------------------------------------------


def _comment(mine, minimized=False, login=None, when=None):
    """One entry of gh's comments list, as small as the check needs."""
    comment = {"viewerDidAuthor": mine, "isMinimized": minimized}
    if login is not None:
        comment["author"] = {"login": login}
    if when is not None:
        comment["createdAt"] = when
    return comment


def test_someone_elses_last_word_is_unresolved():
    comments = [_comment(True), _comment(False)]
    assert prstatus._entry({"comments": comments})["unresolved"] is True


def test_our_own_last_word_is_resolved():
    comments = [_comment(False), _comment(True)]
    assert prstatus._entry({"comments": comments})["unresolved"] is False


@pytest.mark.parametrize("comments", [[], None, "chatty", 7])
def test_no_comments_is_nothing_to_answer(comments):
    assert prstatus._entry({"comments": comments})["unresolved"] is False


def test_a_minimized_last_comment_does_not_count():
    """GitHub collapses spam and off-topic; the last word is the last one
    still standing."""
    comments = [_comment(True), _comment(False, minimized=True)]
    assert prstatus._entry({"comments": comments})["unresolved"] is False


def test_a_malformed_last_comment_is_skipped_not_flagged():
    comments = [_comment(True), "not a comment"]
    assert prstatus._entry({"comments": comments})["unresolved"] is False


def test_a_missing_authorship_stamp_reads_as_someone_elses():
    """viewerDidAuthor gone missing means gh no longer says whose the comment
    is; erring toward "look at it" beats silently swallowing a reply."""
    assert prstatus._entry({"comments": [{"isMinimized": False}]})["unresolved"] is True


def test_unresolved_comments_badge_on_passing_checks():
    pr = PullRequest(55, URL, state="OPEN", passed=2, failed=0, pending=0,
                     mergeable="MERGEABLE", unresolved=True)
    assert pr.badge == "unresolved"


def test_unresolved_comments_badge_without_any_checks():
    """A PR with no checks earns no green mark, but an unanswered comment is
    still worth a triangle."""
    pr = PullRequest(55, URL, state="OPEN", passed=0, failed=0, pending=0,
                     unresolved=True)
    assert pr.badge == "unresolved"


@pytest.mark.parametrize(
    "counts,mergeable,expected",
    [
        ((1, 1, 0), "MERGEABLE", "failed"),  # a red build outranks the reply
        ((1, 0, 0), "CONFLICTING", "conflict"),  # so does a branch that won't merge
        ((1, 0, 2), "MERGEABLE", "pending"),  # checks not passed yet: wait for them
    ],
)
def test_merge_blockers_outrank_unresolved_comments(counts, mergeable, expected):
    passed, failed, pending = counts
    pr = PullRequest(55, URL, state="OPEN", passed=passed, failed=failed,
                     pending=pending, mergeable=mergeable, unresolved=True)
    assert pr.badge == expected


@pytest.mark.parametrize("state", ["MERGED", "CLOSED", None])
def test_only_a_live_pr_awaits_a_reply(state):
    pr = PullRequest(55, URL, state=state, unresolved=True)
    assert pr.awaiting_reply is False
    assert pr.badge != "unresolved"


def test_describe_names_unresolved_comments():
    pr = PullRequest(55, URL, "episode6/collins", state="OPEN",
                     passed=2, failed=0, pending=0, unresolved=True)
    assert describe(pr) == (
        "episode6/collins#55 · Open pull request · Has unresolved comments · 2 passed"
    )


def test_enrich_fills_unresolved(gh):
    gh({"state": "OPEN", "checks": {"passed": 1, "failed": 0, "pending": 0},
        "unresolved": True})
    refresh(URL)
    pr = enrich(parse_pr_link(_link()))
    assert pr.unresolved is True
    assert pr.badge == "unresolved"


def test_the_cli_cache_carries_no_comments(cache):
    """Its entries predate the field, so a warm start says "nothing waiting"."""
    cache({URL: {"state": "OPEN", "checks": {"passed": 1, "failed": 0, "pending": 0}}})
    assert enrich(parse_pr_link(_link())).unresolved is False


def test_unresolved_comments_survive_the_run():
    """A reply flips it, but until one is fetched the row still says someone
    was waiting — which is the last thing anyone knew."""
    pr = PullRequest(55, URL, "episode6/collins", state="OPEN", unresolved=True)
    assert from_record(to_record(pr)).awaiting_reply is True


# -- auto-merge (see practions: it decides which merge button the page draws) -


def test_gh_answering_with_a_request_means_auto_merge_is_on():
    """gh hands back the request itself — who asked and by which method — or
    null; only the presence of one is kept."""
    assert prstatus._entry({"autoMergeRequest": {"enabledBy": {"login": "ghackett"}}})[
        "auto_merge"
    ] is True
    assert prstatus._entry({"autoMergeRequest": None})["auto_merge"] is False
    assert prstatus._entry({})["auto_merge"] is False


@pytest.mark.parametrize("state", ["OPEN", "DRAFT"])
def test_a_live_pr_can_be_waiting_to_merge_itself(state):
    assert PullRequest(55, URL, state=state, auto_merge=True).auto_merging is True


@pytest.mark.parametrize("state", ["MERGED", "CLOSED", None])
def test_only_a_live_pr_is_auto_merging(state):
    """GitHub keeps reporting the request that landed a PR; a merged one isn't
    waiting on anything."""
    assert PullRequest(55, URL, state=state, auto_merge=True).auto_merging is False


def test_describe_says_the_pr_is_merging_itself():
    pr = PullRequest(55, URL, "episode6/collins", state="OPEN",
                     passed=2, failed=0, pending=1, auto_merge=True)
    assert describe(pr) == (
        "episode6/collins#55 · Open pull request · Merging when checks pass "
        "· 2 passed, 1 pending"
    )


def test_enrich_fills_auto_merge(gh):
    gh({"state": "OPEN", "checks": {"passed": 1, "failed": 0, "pending": 1},
        "auto_merge": True})
    refresh(URL)
    assert enrich(parse_pr_link(_link())).auto_merging is True


def test_the_cli_cache_carries_no_auto_merge(cache):
    """Its entries have no such field, so a warm start says "not enabled" —
    which only ever leaves the merge offer where it was."""
    cache({URL: {"state": "OPEN", "checks": {"passed": 1, "failed": 0, "pending": 1}}})
    assert enrich(parse_pr_link(_link())).auto_merge is False


# -- who had the last word (see practions: it decides the review offer) ------


@pytest.mark.parametrize("login", ["claude", "Claude", "claude[bot]", "claude-code[bot]"])
def test_claudes_last_word_is_recognized(login):
    """The workflow comments under a handful of logins, app suffix and all."""
    comments = [_comment(True, login="ghackett"), _comment(False, login=login)]
    assert prstatus._entry({"comments": comments})["claude_replied"] is True


@pytest.mark.parametrize("login", ["ghackett", "claudia", "dependabot[bot]", None])
def test_anybody_elses_last_word_is_not_claudes(login):
    """An unrecognized login reads as a person, which leaves the review offer
    where it has always been."""
    comments = [_comment(False, login="claude"), _comment(False, login=login)]
    assert prstatus._entry({"comments": comments})["claude_replied"] is False


def test_our_own_last_word_is_not_claudes():
    comments = [_comment(False, login="claude"), _comment(True, login="ghackett")]
    entry = prstatus._entry({"comments": comments})
    assert (entry["unresolved"], entry["claude_replied"]) == (False, False)


def test_a_minimized_comment_from_claude_does_not_count():
    """Both bits read the same last-comment: the last word is the last one
    still standing."""
    comments = [_comment(True, login="ghackett"), _comment(False, minimized=True, login="claude")]
    assert prstatus._entry({"comments": comments})["claude_replied"] is False


@pytest.mark.parametrize("comments", [[], None, "chatty", 7])
def test_no_comments_is_nobodys_last_word(comments):
    assert prstatus._entry({"comments": comments})["claude_replied"] is False


def test_a_malformed_author_is_not_claude():
    comments = [_comment(False, login=""), {"author": "claude"}]
    assert prstatus._entry({"comments": comments})["claude_replied"] is False


def test_enrich_fills_claude_replied(gh):
    gh({"state": "OPEN", "checks": {"passed": 1, "failed": 0, "pending": 0},
        "unresolved": True, "claude_replied": True})
    refresh(URL)
    assert enrich(parse_pr_link(_link())).claude_replied is True


def test_the_cli_cache_says_nothing_about_who_commented(cache):
    """Its entries have no comments at all, so a warm start offers the review
    — the safe half of that guess: an offer too many beats one missing."""
    cache({URL: {"state": "OPEN", "checks": {"passed": 1, "failed": 0, "pending": 0}}})
    assert enrich(parse_pr_link(_link())).claude_replied is False


def test_claudes_last_word_survives_the_run():
    """Otherwise a restart puts the review offer back under a review Claude has
    already given."""
    pr = PullRequest(55, URL, "episode6/collins", state="OPEN", claude_replied=True)
    assert from_record(to_record(pr)).claude_replied is True


# -- and whether the code moved after it (the other half of that offer) ------

NOON = "2026-08-13T12:00:00Z"
MORNING = "2026-08-13T09:30:00Z"
EVENING = "2026-08-13T18:45:00Z"


def _pushed(comments, commits):
    return prstatus._entry({"comments": comments, "commits": commits})["pushed_since"]


def _commit(when):
    """One entry of gh's commits list, as small as the check needs."""
    return {"oid": "271949c", "committedDate": when}


def test_a_commit_after_the_newest_comment_is_a_push():
    """The review was of code the branch has since moved off."""
    assert _pushed([_comment(False, login="claude", when=NOON)], [_commit(EVENING)]) is True


def test_a_commit_before_the_newest_comment_is_not():
    """The ordinary shape of a review: it read the branch as it stands."""
    assert _pushed([_comment(False, login="claude", when=NOON)],
                   [_commit(MORNING), _commit(MORNING)]) is False


def test_the_newest_commit_is_the_one_that_counts():
    """Order isn't assumed — the whole list is read for its latest stamp."""
    commits = [_commit(EVENING), _commit(MORNING)]
    assert _pushed([_comment(False, login="claude", when=NOON)], commits) is True


def test_no_comment_at_all_is_nothing_to_be_pushed_since():
    assert _pushed([], [_commit(EVENING)]) is False


@pytest.mark.parametrize("commits", [None, [], "pushed", 7, [{"committedDate": "soon"}]])
def test_commits_gh_cant_answer_for_leave_the_offer_standing(commits):
    """One offer too many beats one missing, as with an unstamped comment."""
    assert _pushed([_comment(False, login="claude", when=NOON)], commits) is True


def test_an_unstamped_comment_leaves_the_offer_standing():
    assert _pushed([_comment(False, login="claude")], [_commit(MORNING)]) is True


def test_a_paged_commit_list_cant_say_when_the_branch_moved():
    """gh reads one page of commits, oldest first, so a full one is missing
    exactly the commits this is asking about."""
    page = [_commit(MORNING) for _ in range(prstatus._GH_PAGE)]
    assert _pushed([_comment(False, login="claude", when=NOON)], page) is True
    assert _pushed([_comment(False, login="claude", when=NOON)], page[:-1]) is False


def test_a_push_after_claudes_review_is_worth_asking_about_again():
    """Both bits together are what the menu reads (see practions)."""
    reviewed = PullRequest(55, URL, state="OPEN", claude_replied=True)
    assert reviewed.claude_had_the_last_word is True
    assert replace(reviewed, pushed_since=True).claude_had_the_last_word is False
    assert PullRequest(55, URL, state="OPEN", pushed_since=True).claude_had_the_last_word is False


def test_enrich_fills_pushed_since(gh):
    gh({"state": "OPEN", "checks": {"passed": 1, "failed": 0, "pending": 0},
        "claude_replied": True, "pushed_since": True})
    refresh(URL)
    assert enrich(parse_pr_link(_link())).claude_had_the_last_word is False


def test_a_push_after_a_review_survives_the_run():
    """Otherwise a restart takes the offer away from a branch that has moved."""
    pr = PullRequest(55, URL, "episode6/collins", state="OPEN",
                     claude_replied=True, pushed_since=True)
    assert from_record(to_record(pr)).pushed_since is True


# -- known (what a sidebar row rebuilds its mark from) -----------------------


def test_known_hands_back_the_status_already_fetched(gh_calls):
    _urls, replies = gh_calls
    replies[URL] = _reply("Track every PR", passed=3)
    refresh(URL)
    pr = known(PullRequest(55, URL))
    assert (pr.title, pr.state, pr.passed) == ("Track every PR", "OPEN", 3)


def test_known_never_schedules_a_fetch(scheduled):
    """It runs on the main loop, where a fetch is a subprocess nobody asked
    for — an unfetched PR simply has no status on it yet."""
    pr = PullRequest(55, URL, title="Track every PR")
    assert known(pr) == pr
    assert scheduled == []


def test_known_ignores_the_cli_cache(cache, scheduled):
    """Reading it is a stat and a JSON parse, and the marks are rebuilt far too
    often for that; whatever is in it reaches them through `enrich` anyway."""
    cache({URL: {"state": "OPEN", "checks": {"passed": 1, "failed": 0, "pending": 0}}})
    assert known(PullRequest(55, URL)).state is None
    assert scheduled == []


# -- combined_state / combined_badge (a session's whole list as one mark) ----


def _pr(number, **overrides):
    return PullRequest(
        number, f"https://github.com/episode6/collins/pull/{number}", **overrides
    )


@pytest.mark.parametrize(
    "states, expected",
    [
        ([], None),                              # no PRs at all
        (["OPEN"], "OPEN"),
        (["MERGED"], "MERGED"),
        (["MERGED", "MERGED"], "MERGED"),        # all landed
        (["MERGED", "OPEN"], "OPEN"),            # one still up for review
        (["MERGED", "OPEN", "DRAFT"], "DRAFT"),  # work in progress outranks it
        (["DRAFT", "MERGED"], "DRAFT"),
        (["CLOSED"], "CLOSED"),                  # abandoned, and nothing else
        (["CLOSED", "CLOSED"], "CLOSED"),
        (["MERGED", "CLOSED"], None),            # ended both ways: claims neither
        ([None, "MERGED"], None),                # one unfetched: nothing known
        ([None, "CLOSED"], None),
        (["CLOSED", "OPEN"], "OPEN"),
        (["CLOSED", "DRAFT"], "DRAFT"),
    ],
)
def test_combined_state_reads_the_least_settled_first(states, expected):
    assert combined_state([_pr(n, state=s) for n, s in enumerate(states)]) == expected


def test_a_failed_check_anywhere_takes_the_badge():
    prs = [_pr(1, state="OPEN", passed=3), _pr(2, state="OPEN", failed=1, passed=1)]
    assert combined_badge(prs) == "failed"


def test_a_conflict_anywhere_takes_the_badge_next():
    prs = [_pr(1, state="OPEN", passed=3), _pr(2, state="OPEN", mergeable="CONFLICTING")]
    assert combined_badge(prs) == "conflict"


def test_unanswered_comments_outrank_running_checks():
    """The row answers "does this session need me?" — a comment does, a check
    still running does not. (The per-PR badge ranks these the other way round,
    where the question is what blocks that one merge.)"""
    prs = [_pr(1, state="OPEN", pending=2), _pr(2, state="OPEN", passed=1, unresolved=True)]
    assert combined_badge(prs) == "unresolved"


def test_pending_checks_take_the_badge_over_a_clean_sweep():
    prs = [_pr(1, state="OPEN", passed=3), _pr(2, state="OPEN", pending=1)]
    assert combined_badge(prs) == "pending"


def test_the_green_check_is_earned_by_every_live_pr():
    prs = [_pr(1, state="OPEN", passed=3), _pr(2, state="DRAFT", passed=1)]
    assert combined_badge(prs) == "passed"


def test_a_live_pr_with_no_checks_withholds_the_green_check():
    """One it never ran would be a lie on a single chip, and it is a lie about
    the session too."""
    prs = [_pr(1, state="OPEN", passed=3), _pr(2, state="OPEN")]
    assert combined_badge(prs) is None


@pytest.mark.parametrize("state", ["MERGED", "CLOSED"])
def test_settled_prs_abstain_from_the_badge(state):
    """Nothing about a merged or closed one can change, and its base already
    says how it ended: they neither withhold the green check nor earn one."""
    assert combined_badge([_pr(1, state=state, passed=1)]) is None
    assert combined_badge([_pr(1, state=state), _pr(2, state="OPEN", passed=1)]) == "passed"


def test_a_closed_prs_last_red_build_is_not_the_rows_problem():
    """The review's catch: a closed PR used to count as live, so a red build it
    carried out of the door badged the row that had nothing left to fix."""
    assert combined_badge([_pr(1, state="CLOSED", failed=1)]) is None
    assert combined_badge([_pr(1, state="CLOSED", pending=2)]) is None
    assert combined_badge([_pr(1, state="CLOSED", unresolved=True)]) is None


def test_no_prs_is_no_badge():
    assert combined_badge([]) is None


def test_describe_all_gives_a_line_to_every_pr():
    prs = [_pr(1, state="OPEN", passed=1), _pr(2, state="MERGED")]
    assert describe_all(prs).splitlines() == [describe(prs[0]), describe(prs[1])]


def test_describe_all_counts_the_ones_it_stops_naming():
    prs = [_pr(n, state="OPEN") for n in range(prstatus._MAX_TOOLTIP_PRS + 3)]
    lines = describe_all(prs).splitlines()
    assert len(lines) == prstatus._MAX_TOOLTIP_PRS + 1
    assert lines[-2] == "and 3 more"


def test_describe_all_names_the_newest_however_long_the_list():
    prs = [_pr(n, state="OPEN") for n in range(prstatus._MAX_TOOLTIP_PRS + 3)]
    lines = describe_all(prs).splitlines()
    # The one a right-click on the mark opens, so the hint under this list can
    # be read against it.
    assert lines[-1] == describe(prs[-1])
    assert lines[0] == describe(prs[0])


# -- sweep (what the sidebar's refresh button runs over every listed row) ----


@pytest.fixture
def branches(monkeypatch):
    """Stub the branch each directory is on; returns the dict to fill in."""
    heads: dict[str, str] = {}
    monkeypatch.setattr(prstatus, "current_branch", heads.get)
    return heads


def test_sweep_finds_the_branch_pr_a_session_never_knew_about(gh_json, gh_calls, branches):
    serve, _calls = gh_json
    serve(_DISCOVERED)
    branches["/home/me/dev/collins"] = "feat/x"
    swept = sweep([("s1", [], "/home/me/dev/collins")])
    assert [pr.number for pr in swept["s1"]] == [74]
    assert swept["s1"][0].badge == "failed"  # the lookup's own status came with it


def test_sweep_asks_each_directory_once(gh_json, gh_calls, branches):
    """Sessions sharing a worktree share its branch, and a panel of them must
    not become a panel of identical gh calls."""
    serve, calls = gh_json
    serve(_DISCOVERED)
    branches["/home/me/dev/collins"] = "feat/x"
    swept = sweep([
        ("s1", [], "/home/me/dev/collins"),
        ("s2", [], "/home/me/dev/collins"),
    ])
    assert len(calls) == 1
    assert [pr.number for pr in swept["s2"]] == [74]


def test_sweep_does_not_refetch_what_the_lookup_just_answered(gh_json, gh_calls, branches):
    serve, _calls = gh_json
    serve(_DISCOVERED)
    branches["/home/me/dev/collins"] = "feat/x"
    urls, _replies = gh_calls
    sweep([("s1", [], "/home/me/dev/collins")])
    assert urls == []


def test_sweep_refreshes_every_saved_pr(gh_json, gh_calls, branches):
    urls, replies = gh_calls
    replies[URL] = _reply("Track every PR")
    replies[OTHER_URL] = _reply("Give the list room", passed=2)
    swept = sweep([
        ("s1", [PullRequest(55, URL)], None),
        ("s2", [PullRequest(56, OTHER_URL)], None),
    ])
    assert sorted(urls) == [URL, OTHER_URL]
    assert swept["s1"][0].title == "Track every PR"
    assert swept["s2"][0].passed == 2


def test_sweep_fetches_a_shared_pr_once(gh_json, gh_calls, branches):
    """Two sessions on one PR (a fork, or the same work resumed twice) cost a
    single call between them."""
    urls, replies = gh_calls
    replies[URL] = _reply("Track every PR")
    swept = sweep([("s1", [PullRequest(55, URL)], None), ("s2", [PullRequest(55, URL)], None)])
    assert urls == [URL]
    assert swept["s1"] == swept["s2"]


def test_sweep_appends_a_discovery_to_what_the_session_had(gh_json, gh_calls, branches):
    """A PR the session opened earlier stays, oldest first: the branch has
    simply moved on to a newer one."""
    serve, _calls = gh_json
    serve(_DISCOVERED)
    branches["/home/me/dev/collins"] = "feat/x"
    _urls, replies = gh_calls
    replies[URL] = _reply("Track every PR")
    swept = sweep([("s1", [PullRequest(55, URL)], "/home/me/dev/collins")])
    assert [pr.number for pr in swept["s1"]] == [55, 74]


def test_sweep_never_lists_the_same_pr_twice(gh_json, gh_calls, branches):
    serve, _calls = gh_json
    serve(_DISCOVERED)
    branches["/home/me/dev/collins"] = "feat/x"
    swept = sweep([("s1", [PullRequest(74, _found()["url"])], "/home/me/dev/collins")])
    assert [pr.number for pr in swept["s1"]] == [74]


def test_sweep_asks_nothing_for_a_session_with_no_directory(gh_json, gh_calls, branches):
    serve, calls = gh_json
    serve(_DISCOVERED)
    assert sweep([("s1", [], None)]) == {"s1": []}
    assert calls == []


def test_sweep_survives_a_lookup_that_blows_up(monkeypatch, gh_calls, branches):
    """One unreadable repository can't cost every other row its refresh."""

    def explode(args, cwd=None):
        raise OSError("no such repository")

    monkeypatch.setattr(prstatus, "gh_json", explode)
    branches["/home/me/dev/gone"] = "feat/x"
    _urls, replies = gh_calls
    replies[URL] = _reply("Track every PR")
    swept = sweep([
        ("s1", [], "/home/me/dev/gone"),
        ("s2", [PullRequest(55, URL)], None),
    ])
    assert swept["s1"] == []
    assert swept["s2"][0].title == "Track every PR"


def test_sweep_spends_no_call_on_a_merged_pr(gh_json, gh_calls, branches, scheduled):
    """A merged PR that knows its title is what most old rows carry, and a
    sidebar full of them must cost nothing at all — including in the
    background, which is what reading one back through `enrich` would start."""
    urls, _replies = gh_calls
    pr = PullRequest(55, URL, title="Track every PR", state="MERGED")
    assert sweep([("s1", [pr], None)]) == {"s1": [pr]}
    assert urls == []
    assert scheduled == []


def test_sweep_still_asks_about_a_closed_pr(gh_json, gh_calls, branches):
    """The sweep is the click that would show a closed PR reopened."""
    urls, _replies = gh_calls
    pr = PullRequest(55, URL, title="Track every PR", state="CLOSED")
    sweep([("s1", [pr], None)])
    assert urls == [URL]
