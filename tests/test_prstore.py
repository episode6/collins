"""PrStore: the hub every surface reads a session's pull requests through.

Its two promises are the ones under test here: a write that changes a
session's saved list is persisted and announced exactly once (and an
unchanged one not at all), and a status fetch made anywhere in the app comes
out of the hub as a signal — which is what lets a refresh in one surface
update every other one.
"""

import pytest

from collins import prstatus
from collins.prstore import PrStore

URL = "https://github.com/episode6/collins/pull/55"
OTHER_URL = "https://github.com/episode6/collins/pull/56"
SESSION = "11111111-2222-3333-4444-555555555555"

# A full `gh pr view` reply, as absorb() receives one from the PR page.
REPLY = {
    "title": "Add the thing",
    "state": "OPEN",
    "isDraft": False,
    "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "COMPLETED"}],
    "mergeable": "MERGEABLE",
    "comments": [],
    "commits": [],
}


@pytest.fixture
def state(app_state):
    return app_state.AppState()


@pytest.fixture
def store(state):
    """A PrStore whose emissions land inline — the dispatch that in the app
    hops a worker-thread fetch onto the main loop is a direct call here."""
    prstatus._statuses.clear()
    store = PrStore(state, dispatch=lambda callback, *args: callback(*args))
    yield store
    prstatus._statuses.clear()


@pytest.fixture
def heard(store):
    """Every signal the store emits, in order, as ("status"|"session", arg)."""
    events = []
    store.connect("status-changed", lambda _store, url: events.append(("status", url)))
    store.connect("session-changed", lambda _store, sid: events.append(("session", sid)))
    return events


# -- the session → PRs association ------------------------------------------


def test_a_write_persists_and_announces_once(store, state, heard):
    records = [{"number": 55, "url": URL}]
    store.set_records(SESSION, records)
    assert state.get_session_prs(SESSION) == records
    assert heard == [("session", SESSION)]


def test_an_identical_write_is_swallowed_whole(store, state, heard):
    """The loop-breaker: a subscriber that writes back what it adopted must
    not start a carousel of session-changed emissions."""
    records = [{"number": 55, "url": URL}]
    store.set_records(SESSION, records)
    store.set_records(SESSION, list(records))
    assert heard == [("session", SESSION)]


def test_an_empty_write_drops_the_session(store, state, heard):
    store.set_records(SESSION, [{"number": 55, "url": URL}])
    store.set_records(SESSION, [])
    assert state.get_session_prs(SESSION) == []
    assert heard == [("session", SESSION), ("session", SESSION)]


def test_a_sessionless_write_goes_nowhere(store, heard):
    store.set_records("", [{"number": 55, "url": URL}])
    assert heard == []


def test_attach_appends_only_what_is_new(store, state, heard):
    """The first-prompt attacher's contract: newcomers go after what the
    session already has, and a PR both sides know keeps the saved copy —
    it carries what has been learned about it since."""
    store.set_records(SESSION, [{"number": 55, "url": URL, "title": "Saved title"}])
    store.attach(
        SESSION, [prstatus.parse_pr_url(URL), prstatus.parse_pr_url(OTHER_URL)]
    )
    records = state.get_session_prs(SESSION)
    assert [record["url"] for record in records] == [URL, OTHER_URL]
    assert records[0]["title"] == "Saved title"
    assert heard == [("session", SESSION), ("session", SESSION)]


def test_attach_with_nothing_new_says_nothing(store, heard):
    store.set_records(SESSION, [{"number": 55, "url": URL}])
    store.attach(SESSION, [prstatus.parse_pr_url(URL)])
    assert heard == [("session", SESSION)]


def test_prs_wears_the_freshest_status(store):
    """Reading a session's list lays whatever this run has fetched over the
    saved records — the same view every widget builds its marks from."""
    store.set_records(SESSION, [{"number": 55, "url": URL}])
    prstatus.absorb(URL, REPLY)
    (pr,) = store.prs(SESSION)
    assert (pr.title, pr.state, pr.passed) == ("Add the thing", "OPEN", 1)


# -- status fan-out ----------------------------------------------------------


def test_a_fetch_anywhere_comes_out_as_a_signal(store, heard):
    """absorb() stands in for every write path — a poll's refresh, a sweep's,
    the PR page folding its own reply back in: they all land in the same
    cache, and the hub tells whoever is showing that URL."""
    prstatus.absorb(URL, REPLY)
    assert heard == [("status", URL)]


def test_a_fetch_that_changed_nothing_says_nothing(store, heard):
    prstatus.absorb(URL, REPLY)
    prstatus.absorb(URL, dict(REPLY))
    assert heard == [("status", URL)]


def test_invalidate_is_not_news(store, heard):
    """Marking a status due re-stamps its TTL; what is known didn't move,
    so nobody needs telling."""
    prstatus.absorb(URL, REPLY)
    prstatus.invalidate(URL)
    assert heard == [("status", URL)]
