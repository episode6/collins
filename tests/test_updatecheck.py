# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The once-a-day update check (collins.updatecheck): versions compared,
GitHub's answer parsed, the cache and the once-a-day rule, the gates, and
the notification a newer release becomes."""

import io
import json
import threading
import urllib.error

import pytest

from collins import notifycenter, updatecheck
from collins.notifycenter import KIND_UPDATE, NotificationCenter
from collins.updatecheck import (
    FAILED,
    INTERVAL_S,
    NOT_MODIFIED,
    RELEASES_URL,
    RETRY_AFTER_FAILURE_S,
    Release,
    check,
    due,
    is_newer,
    parse_release,
    parse_version,
    read_record,
    write_record,
)

NOW = 1_800_000_000.0
NEWER = Release(version="0.1.3", tag="v0.1.3", url="https://github.com/episode6/collins/releases/tag/v0.1.3")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch, app_state):
    """The cache under a temp XDG_CACHE_HOME, a fixed running version, no
    harness variables in the way, and the single-flight flag down."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("COLLINS_USAGE_FIXTURE", raising=False)
    monkeypatch.delenv("COLLINS_APP_ID", raising=False)
    monkeypatch.setattr(updatecheck, "running_version", lambda: "0.1.2")
    monkeypatch.setattr(updatecheck, "_running", False)


# -- versions -------------------------------------------------------------------


def test_parse_version_reads_tags_and_snapshots():
    assert parse_version("v0.1.3") == ((0, 1, 3), 1)
    assert parse_version("0.1.3") == ((0, 1, 3), 1)
    assert parse_version("0.1.2.dev0") == ((0, 1, 2), 0)
    assert parse_version("0.1.2rc1") == ((0, 1, 2), 0)
    assert parse_version("0.1.2.post1") == ((0, 1, 2), 2)
    assert parse_version("1.2") == parse_version("1.2.0")
    assert parse_version("V2") == ((2,), 1)


def test_parse_version_refuses_what_is_not_one():
    assert parse_version("latest") is None
    assert parse_version("") is None
    assert parse_version(None) is None
    assert parse_version("v0.1.3-final-final") is None


def test_is_newer_orders_releases_and_snapshots():
    assert is_newer("v0.1.3", "0.1.2")
    assert is_newer("0.1.10", "0.1.9")
    assert is_newer("0.2", "0.1.9")
    assert not is_newer("0.1.2", "0.1.2")
    assert not is_newer("0.1.1", "0.1.2")
    # main runs as a snapshot of the release it precedes: that release is
    # an update to it, and it is not an update to that release.
    assert is_newer("0.1.2", "0.1.2.dev0")
    assert not is_newer("0.1.2.dev0", "0.1.2")
    assert not is_newer("0.1.1", "0.1.2.dev0")


def test_is_newer_is_false_for_anything_unparsable():
    assert not is_newer("latest", "0.1.2")
    assert not is_newer("0.1.3", "unknown")
    assert not is_newer(None, "0.1.2")


# -- the API's answer -----------------------------------------------------------


def test_parse_release_reads_the_tag_and_page():
    payload = {"tag_name": "v0.1.3", "html_url": NEWER.url, "draft": False, "prerelease": False}
    assert parse_release(payload) == NEWER


def test_parse_release_falls_back_to_the_releases_page():
    assert parse_release({"tag_name": "v0.1.3"}).url == RELEASES_URL
    assert parse_release({"tag_name": "v0.1.3", "html_url": "http://evil.example/"}).url == RELEASES_URL
    assert parse_release({"tag_name": "v0.1.3", "html_url": 7}).url == RELEASES_URL


def test_parse_release_refuses_drafts_prereleases_and_non_versions():
    assert parse_release({"tag_name": "v0.1.3", "draft": True}) is None
    assert parse_release({"tag_name": "v0.1.3", "prerelease": True}) is None
    assert parse_release({"tag_name": "nightly"}) is None
    assert parse_release({"html_url": NEWER.url}) is None
    assert parse_release(None) is None
    assert parse_release([]) is None
    assert parse_release({"message": "Not Found"}) is None


# -- the cache ------------------------------------------------------------------


def test_record_round_trips_and_drops_garbage():
    assert read_record() == {}
    saved = {"checked_at": NOW, "latest": "0.1.3", "url": NEWER.url, "etag": 'W/"abc"', "notified": "0.1.3"}
    write_record(saved)
    assert read_record() == {
        "checked_at": NOW,
        "latest": "0.1.3",
        "url": NEWER.url,
        "etag": 'W/"abc"',
        "notified": "0.1.3",
    }
    # A hand edit: wrong types are dropped, not raised on.
    updatecheck.cache_path().write_text(
        json.dumps({"version": 1, "checked_at": "yesterday", "failed_at": True, "latest": 3, "notified": ""})
    )
    assert read_record() == {}


def test_record_from_another_version_or_broken_is_ignored():
    updatecheck.cache_path().parent.mkdir(parents=True)
    updatecheck.cache_path().write_text(json.dumps({"version": 99, "checked_at": NOW}))
    assert read_record() == {}
    updatecheck.cache_path().write_text("{not json")
    assert read_record() == {}
    updatecheck.cache_path().write_text("[]")
    assert read_record() == {}


def test_due_once_a_day_and_an_hour_after_a_failure():
    assert due({}, NOW)
    assert not due({"checked_at": NOW - INTERVAL_S + 60}, NOW)
    assert due({"checked_at": NOW - INTERVAL_S - 1}, NOW)
    assert not due({"failed_at": NOW - RETRY_AFTER_FAILURE_S + 60}, NOW)
    assert due({"failed_at": NOW - RETRY_AFTER_FAILURE_S - 1}, NOW)
    # An old answer and a recent failure: the failure's hour governs.
    assert not due({"checked_at": NOW - 2 * INTERVAL_S, "failed_at": NOW - 60}, NOW)
    # A clock that went back is due, not a day away.
    assert due({"checked_at": NOW + 3600}, NOW)
    assert due({"failed_at": NOW + 3600}, NOW)


# -- check ----------------------------------------------------------------------


@pytest.fixture
def fetched(monkeypatch):
    """What the next fetch answers, and what it was asked with."""
    calls: list = []
    answer = {"value": (NEWER, 'W/"new"')}

    def fake(etag=None, timeout=None):
        calls.append(etag)
        return answer["value"]

    monkeypatch.setattr(updatecheck, "fetch_latest", fake)
    return calls, answer


def test_check_announces_a_newer_release_once(fetched):
    calls, _answer = fetched
    assert check(NOW) == NEWER
    record = read_record()
    assert record["checked_at"] == NOW
    assert record["latest"] == "0.1.3" and record["url"] == NEWER.url
    assert record["etag"] == 'W/"new"' and record["notified"] == "0.1.3"
    # The next day, the same release: told already.
    assert check(NOW + INTERVAL_S + 1) is None
    assert calls == [None, 'W/"new"']
    assert read_record()["checked_at"] == NOW + INTERVAL_S + 1


def test_check_is_quiet_when_not_due(fetched):
    calls, _answer = fetched
    write_record({"checked_at": NOW - 60})
    assert check(NOW) is None
    assert calls == []


def test_check_is_quiet_when_up_to_date(fetched):
    _calls, answer = fetched
    answer["value"] = (Release("0.1.2", "v0.1.2", RELEASES_URL), None)
    assert check(NOW) is None
    record = read_record()
    assert record["latest"] == "0.1.2" and "notified" not in record and "etag" not in record


def test_check_records_a_failure_and_never_announces_one(fetched):
    _calls, answer = fetched
    write_record({"checked_at": NOW - 2 * INTERVAL_S, "latest": "0.1.3", "etag": 'W/"old"'})
    answer["value"] = (FAILED, None)
    assert check(NOW) is None
    record = read_record()
    assert record["failed_at"] == NOW
    assert record["checked_at"] == NOW - 2 * INTERVAL_S  # not an answer
    assert record["latest"] == "0.1.3"  # what was known is kept
    # An hour later, an answer clears the failure.
    answer["value"] = (NEWER, None)
    assert check(NOW + RETRY_AFTER_FAILURE_S + 1) == NEWER
    assert "failed_at" not in read_record()


def test_check_not_modified_stands_on_the_saved_answer(fetched):
    calls, answer = fetched
    write_record({"checked_at": NOW - 2 * INTERVAL_S, "latest": "0.1.3", "url": NEWER.url, "etag": 'W/"old"'})
    answer["value"] = (NOT_MODIFIED, 'W/"old"')
    # Saved but never announced (a hand-edited file, or a check that died
    # between the write and the announce): it is announced now.
    assert check(NOW) == NEWER
    assert calls == ['W/"old"']
    record = read_record()
    assert record["checked_at"] == NOW and record["notified"] == "0.1.3" and record["etag"] == 'W/"old"'
    assert check(NOW + INTERVAL_S + 1) is None


def test_check_not_modified_with_nothing_saved_drops_the_etag(fetched):
    _calls, answer = fetched
    write_record({"etag": 'W/"orphan"'})
    answer["value"] = (NOT_MODIFIED, 'W/"orphan"')
    assert check(NOW) is None
    record = read_record()
    assert "etag" not in record and record["checked_at"] == NOW


# -- fetch: gh first, anonymous after ------------------------------------------


def test_fetch_prefers_a_usable_gh(monkeypatch):
    monkeypatch.setattr(updatecheck, "gh_usable", lambda: True)
    monkeypatch.setattr(updatecheck, "fetch_via_gh", lambda timeout: NEWER)
    monkeypatch.setattr(
        updatecheck, "fetch_anonymous", lambda *a, **k: pytest.fail("anonymous path taken with gh usable")
    )
    assert updatecheck.fetch_latest('W/"x"') == (NEWER, None)


def test_fetch_falls_back_when_gh_cannot_be_used(monkeypatch):
    asked: list = []
    monkeypatch.setattr(updatecheck, "gh_usable", lambda: False)
    monkeypatch.setattr(updatecheck, "fetch_via_gh", lambda timeout: pytest.fail("gh asked while unusable"))
    def anonymous(etag, timeout):
        asked.append(etag)
        return NEWER, 'W/"a"'

    monkeypatch.setattr(updatecheck, "fetch_anonymous", anonymous)
    assert updatecheck.fetch_latest('W/"x"') == (NEWER, 'W/"a"')
    assert asked == ['W/"x"']


def test_fetch_falls_back_when_gh_did_not_answer(monkeypatch):
    monkeypatch.setattr(updatecheck, "gh_usable", lambda: True)
    monkeypatch.setattr(updatecheck, "fetch_via_gh", lambda timeout: None)
    monkeypatch.setattr(updatecheck, "fetch_anonymous", lambda etag, timeout: (FAILED, None))
    assert updatecheck.fetch_latest() == (FAILED, None)


def test_gh_usable_needs_gh_on_path_and_credentials(monkeypatch):
    monkeypatch.setattr(updatecheck.shutil, "which", lambda name: None)
    monkeypatch.setattr(updatecheck, "gh_succeeds", lambda args: pytest.fail("asked without gh"))
    assert not updatecheck.gh_usable()
    monkeypatch.setattr(updatecheck.shutil, "which", lambda name: "/usr/bin/gh")
    asked: list = []
    monkeypatch.setattr(updatecheck, "gh_succeeds", lambda args: asked.append(args) or False)
    assert not updatecheck.gh_usable()
    assert asked == [["auth", "token"]]
    monkeypatch.setattr(updatecheck, "gh_succeeds", lambda args: True)
    assert updatecheck.gh_usable()


def test_fetch_via_gh_asks_the_releases_endpoint(monkeypatch):
    asked: list = []

    def gh_json(args, timeout=None):
        asked.append(args)
        return {"tag_name": "v0.1.3", "html_url": NEWER.url}

    monkeypatch.setattr(updatecheck, "gh_json", gh_json)
    assert updatecheck.fetch_via_gh() == NEWER
    assert asked[0][:2] == ["api", "repos/episode6/collins/releases/latest"]
    monkeypatch.setattr(updatecheck, "gh_json", lambda args, timeout=None: None)
    assert updatecheck.fetch_via_gh() is None


class _Response(io.BytesIO):
    def __init__(self, payload, etag=None):
        super().__init__(json.dumps(payload).encode())
        self.headers = {"ETag": etag} if etag else {}

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def test_fetch_anonymous_sends_no_token_and_keeps_the_etag(monkeypatch):
    seen: list = []

    def urlopen(request, timeout=None):
        seen.append(request)
        return _Response({"tag_name": "v0.1.3", "html_url": NEWER.url}, etag='W/"fresh"')

    monkeypatch.setattr(updatecheck.urllib.request, "urlopen", urlopen)
    assert updatecheck.fetch_anonymous('W/"stale"') == (NEWER, 'W/"fresh"')
    request = seen[0]
    assert request.full_url == "https://api.github.com/repos/episode6/collins/releases/latest"
    assert request.get_header("If-none-match") == 'W/"stale"'
    assert request.get_header("Authorization") is None
    assert request.get_header("User-agent", "").startswith("collins/")


def test_fetch_anonymous_reads_a_304_as_unchanged(monkeypatch):
    def urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 304, "Not Modified", {}, None)

    monkeypatch.setattr(updatecheck.urllib.request, "urlopen", urlopen)
    assert updatecheck.fetch_anonymous('W/"same"') == (NOT_MODIFIED, 'W/"same"')


def test_fetch_anonymous_fails_quietly(monkeypatch):
    def refused(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 403, "rate limited", {}, None)

    monkeypatch.setattr(updatecheck.urllib.request, "urlopen", refused)
    assert updatecheck.fetch_anonymous() == (FAILED, None)

    def offline(request, timeout=None):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(updatecheck.urllib.request, "urlopen", offline)
    assert updatecheck.fetch_anonymous() == (FAILED, None)
    def no_release(request, timeout=None):
        return _Response({"message": "?"})

    monkeypatch.setattr(updatecheck.urllib.request, "urlopen", no_release)
    assert updatecheck.fetch_anonymous() == (FAILED, None)


# -- the gates and the thread ---------------------------------------------------


def test_harnessed_by_fixture_or_a_throwaway_app_id(monkeypatch):
    assert not updatecheck.harnessed()
    monkeypatch.setenv("COLLINS_APP_ID", "com.episode6.Collins")
    assert not updatecheck.harnessed()
    monkeypatch.setenv("COLLINS_APP_ID", "com.episode6.Collins.Debug")
    assert not updatecheck.harnessed()
    monkeypatch.setenv("COLLINS_APP_ID", "com.episode6.Collins.E2E.r123")
    assert updatecheck.harnessed()
    monkeypatch.delenv("COLLINS_APP_ID")
    monkeypatch.setenv("COLLINS_USAGE_FIXTURE", "/tmp/usage.json")
    assert updatecheck.harnessed()


def test_enabled_is_the_switch(app_state):
    assert updatecheck.enabled()
    app_state.AppState().set_setting(updatecheck.SETTING, False)
    assert not updatecheck.enabled()


def test_maybe_start_runs_the_check_off_thread_and_announces(monkeypatch):
    heard: list = []
    threads: list = []
    monkeypatch.setattr(updatecheck, "check", lambda: threads.append(threading.current_thread()) or NEWER)
    thread = updatecheck.maybe_start(heard.append)
    assert thread is not None
    thread.join(5)
    assert heard == [NEWER]
    assert threads and threads[0] is not threading.main_thread()
    assert not updatecheck._running


def test_maybe_start_says_nothing_for_nothing(monkeypatch):
    heard: list = []
    monkeypatch.setattr(updatecheck, "check", lambda: None)
    thread = updatecheck.maybe_start(heard.append)
    thread.join(5)
    assert heard == []


def test_maybe_start_refuses_the_switch_off_the_harness_and_a_running_check(monkeypatch, app_state):
    monkeypatch.setattr(updatecheck, "check", lambda: pytest.fail("checked while refused"))
    monkeypatch.setattr(updatecheck, "_running", True)
    assert updatecheck.maybe_start(lambda r: None) is None
    monkeypatch.setattr(updatecheck, "_running", False)
    monkeypatch.setenv("COLLINS_APP_ID", "com.episode6.Collins.E2E.r1")
    assert updatecheck.maybe_start(lambda r: None) is None
    monkeypatch.delenv("COLLINS_APP_ID")
    app_state.AppState().set_setting(updatecheck.SETTING, False)
    assert updatecheck.maybe_start(lambda r: None) is None


# -- the notification -----------------------------------------------------------


def test_notification_is_keyed_by_version_and_carries_the_page():
    center = NotificationCenter(clock=lambda: NOW)
    row = updatecheck.notification(center, NEWER)
    assert row.kind == KIND_UPDATE
    assert row.id == notifycenter.update_id("0.1.3")
    assert row.session_id == "" and row.project == ""
    assert "0.1.3" in row.title
    assert "0.1.2" in row.body
    assert row.url == NEWER.url
    assert row.when == NOW
    # No page named: the releases page.
    assert updatecheck.notification(center, Release("0.1.3", "v0.1.3", "")).url == RELEASES_URL


def test_retire_drops_rows_the_running_version_has_caught_up_with():
    center = NotificationCenter(clock=lambda: NOW)
    center.post(updatecheck.notification(center, Release("0.1.2", "v0.1.2", RELEASES_URL)))
    center.post(center.make(notifycenter.KIND_MESSAGE, "s1", "Fix it", "alpha", "Look"))
    assert updatecheck.retire(center, "0.1.2") == 1
    assert [row.kind for row in center.rows()] == [notifycenter.KIND_MESSAGE]
    center.post(updatecheck.notification(center, NEWER))
    assert updatecheck.retire(center, "0.1.2") == 0
    assert updatecheck.retire(center, "0.1.4") == 1
    assert updatecheck.retire(center, "0.1.4") == 0
