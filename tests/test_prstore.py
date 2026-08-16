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


@pytest.fixture
def attached(store):
    """The (session, url) pairs the store announced as newly attached."""
    events = []
    store.connect("pr-attached", lambda _store, sid, url: events.append((sid, url)))
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


def test_a_subscriber_writing_back_what_it_adopted_does_not_carousel(store, state, heard):
    """The subscribers' echo, at the level this GTK-free suite can reach: a
    session-changed handler that re-reads the saved list and writes it back —
    the shape of a tab adopting somebody else's write — must not re-emit.
    The handler runs re-entrantly inside the emission, before the writer's
    own bookkeeping continues, which is exactly the ordering a widget test
    would be probing."""

    def adopt(_store, session_id):
        store.set_records(session_id, state.get_session_prs(session_id))

    store.connect("session-changed", adopt)
    store.set_records(SESSION, [{"number": 55, "url": URL}])
    assert heard == [("session", SESSION)]


def test_a_subscriber_amending_a_write_lands_both(store, state, heard):
    """Re-entrant writes that *do* change something are legal, and settle: a
    handler that amends what was written (a tab appending the PR only it
    knows about) emits once more, converges on the amendment, and stops."""
    amended = [{"number": 55, "url": URL}, {"number": 56, "url": OTHER_URL}]

    def amend(_store, session_id):
        if state.get_session_prs(session_id) != amended:
            store.set_records(session_id, amended)

    store.connect("session-changed", amend)
    store.set_records(SESSION, [{"number": 55, "url": URL}])
    assert state.get_session_prs(SESSION) == amended
    assert heard == [("session", SESSION), ("session", SESSION)]


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


# -- newly attached pull requests -------------------------------------------


def test_a_first_sighting_is_announced(store, attached):
    store.set_records(SESSION, [{"number": 55, "url": URL}])
    assert attached == [(SESSION, URL)]


def test_only_the_newcomer_is_announced(store, attached):
    """A write that keeps one PR and adds another says only what is new —
    the poll that rewrites the whole list every time it learns a title must
    not read as the session picking every PR up again."""
    store.set_records(SESSION, [{"number": 55, "url": URL}])
    store.set_records(
        SESSION,
        [{"number": 55, "url": URL, "title": "Add the thing"}, {"number": 56, "url": OTHER_URL}],
    )
    assert attached == [(SESSION, URL), (SESSION, OTHER_URL)]


def test_a_pr_is_announced_once_per_session(store, attached):
    """The once-per-PR promise, over the writes a session actually makes: a
    reorder is not an arrival, and neither is a resume — the saved list
    written back as it was found."""
    records = [{"number": 55, "url": URL}, {"number": 56, "url": OTHER_URL}]
    store.set_records(SESSION, records)
    store.set_records(SESSION, list(reversed(records)))
    store.set_records(SESSION, list(records))
    assert attached == [(SESSION, URL), (SESSION, OTHER_URL)]


def test_a_pr_the_session_lost_is_new_again(store, attached):
    """"Once per PR" means once per *association*, not once ever: the saved
    list is all that is remembered, so a PR taken off it and put back is a
    second arrival.

    Nothing a live session does reaches this — every writer sends the whole
    list it holds, and the one path that empties a list is forgetting the
    session outright (MainWindow._forget_transcript, after its transcript is
    gone). Should that id ever come back, it comes back as a stranger, and
    saying so is the right answer.
    """
    store.set_records(SESSION, [{"number": 55, "url": URL}])
    store.set_records(SESSION, [])
    store.set_records(SESSION, [{"number": 55, "url": URL}])
    assert attached == [(SESSION, URL), (SESSION, URL)]


def test_the_newcomer_arrives_after_the_list_it_is_on(store, state, attached):
    """Ordering the subscribers rely on: by the time a handler hears about a
    PR, the saved list it can read already carries it."""
    seen = []
    store.connect(
        "pr-attached", lambda _s, sid, _url: seen.append(list(state.get_session_prs(sid)))
    )
    store.set_records(SESSION, [{"number": 55, "url": URL}])
    assert seen == [[{"number": 55, "url": URL}]]


def test_attach_announces_what_it_appended(store, attached):
    store.set_records(SESSION, [{"number": 55, "url": URL}])
    store.attach(SESSION, [prstatus.parse_pr_url(URL), prstatus.parse_pr_url(OTHER_URL)])
    assert attached == [(SESSION, URL), (SESSION, OTHER_URL)]


def test_an_unusable_record_is_not_announced(store, attached):
    """The URL goes out to handlers that hand it to `gh`, so it passes the
    same gate every restored PR passes — a record that can't be read back
    is not an arrival."""
    store.set_records(SESSION, [{"number": 55, "url": "https://example.com/pull/55"}])
    assert attached == []


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
