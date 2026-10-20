"""The git page's freshness table (collins.gitfresh; split-service spec
§3.23, PR-2.2, should-fix 2 of PR 610's review): the sequences the e2e
checks walk, GTK-free — an event on a hidden page is one compare on its
map; one during a mutation is retried; a mid-mutation state is dropped by
the mutation's own read; a state beside a read in flight is confirmed;
another cwd drops what was owed."""

from __future__ import annotations

from collins import gitfresh
from collins.gitfresh import NO_STATE, NOTHING, Actions, Freshness, Page

SHOWN = Page(mapped=True, busy=False, loading=False, working=True)
HIDDEN = Page(mapped=False, busy=False, loading=False, working=True)
BUSY = Page(mapped=True, busy=True, loading=False, working=True)
READING = Page(mapped=True, busy=False, loading=True, working=True)
COMMIT = Page(mapped=True, busy=False, loading=False, working=False)


def _drawn(state: str = "s0") -> Freshness:
    fresh = Freshness()
    fresh.read_started(busy=False)
    assert fresh.read_landed(state, working=True, reread=False) == NOTHING
    return fresh


# -- a shown page -------------------------------------------------------------------------


def test_an_event_on_a_shown_page_compares_at_once_and_a_moved_state_reloads():
    fresh = _drawn()
    assert fresh.compare(SHOWN) == Actions(compare=True, counted=True)
    assert fresh.state_event("s1", SHOWN) == Actions(reload=True)
    assert fresh.tree_state == "s1"
    # The same state again: nothing.
    assert fresh.state_event("s1", SHOWN) == NOTHING
    assert not fresh.pending


def test_check_now_is_an_uncounted_compare():
    fresh = _drawn()
    assert fresh.compare(SHOWN, counted=False) == Actions(compare=True, counted=False)


def test_a_state_on_a_commit_load_is_nothing():
    fresh = _drawn(None)
    assert fresh.state_event("s1", COMMIT) == NOTHING
    assert fresh.tree_state is None and not fresh.pending


# -- hidden: one compare on the map (§3.15) -------------------------------------------------


def test_an_event_while_hidden_is_owed_and_taken_once_on_the_map():
    fresh = _drawn()
    assert fresh.compare(HIDDEN) == NOTHING
    assert fresh.state_event("s1", HIDDEN) == NOTHING
    assert fresh.pending and fresh.compare_due and fresh.deferred_state == "s1"
    # Three events while hidden are still one compare and the last state.
    fresh.compare(HIDDEN)
    fresh.state_event("s2", HIDDEN)
    # The map: check_now's compare (uncounted), then the kept state.
    assert fresh.compare(SHOWN, counted=False) == Actions(compare=True)
    assert fresh.take_deferred(SHOWN) == Actions(reload=True)
    assert fresh.tree_state == "s2" and not fresh.pending
    # Nothing owed now: a second map compares nothing.
    assert fresh.take_deferred(SHOWN) == NOTHING


def test_a_kept_state_equal_to_the_drawn_one_reloads_nothing_on_the_map():
    fresh = _drawn("s0")
    fresh.state_event("s0", HIDDEN)
    assert fresh.pending
    assert fresh.take_deferred(SHOWN) == NOTHING
    assert not fresh.pending


def test_a_kept_state_stays_while_the_page_is_hidden_still():
    fresh = _drawn()
    fresh.state_event("s1", HIDDEN)
    assert fresh.take_deferred(HIDDEN) == NOTHING
    assert fresh.deferred_state == "s1"


# -- a mutation in flight: retried --------------------------------------------------------


def test_an_event_during_a_mutation_is_retried_until_it_landed():
    fresh = _drawn()
    assert fresh.compare(BUSY) == Actions(retry=True)
    assert fresh.state_event("s1", BUSY) == Actions(retry=True)
    assert fresh.compare_due and fresh.deferred_state == "s1"
    # Still busy: look again, nothing else.
    assert fresh.retry(BUSY) == Actions(retry=True)
    assert fresh.compare_due and fresh.deferred_state == "s1"
    # Landed with no reload of its own: the compare (counted), then the state.
    assert fresh.retry(SHOWN) == Actions(compare=True, counted=True)
    assert not fresh.compare_due
    assert fresh.take_deferred(SHOWN) == Actions(reload=True)
    assert fresh.tree_state == "s1"


def test_a_retry_with_nothing_owed_compares_nothing():
    fresh = _drawn()
    assert fresh.retry(SHOWN) == NOTHING
    assert fresh.take_deferred(SHOWN) == NOTHING


def test_a_mid_mutation_state_is_dropped_by_the_mutations_own_read():
    """The service's tick can see a mutation half done; the mutation's
    landing re-reads (its worker samples the state after it), and that
    read drops the kept state: the retry compares and reloads nothing."""
    fresh = _drawn()
    fresh.state_event("half-done", BUSY)
    fresh.compare(BUSY)
    fresh.read_started(busy=False)  # _on_mutated's _read_diff, the mutation landed
    assert fresh.deferred_state is NO_STATE
    assert fresh.read_landed("s1", working=True, reread=False) == NOTHING
    assert fresh.retry(SHOWN) == Actions(compare=True, counted=True)
    assert fresh.take_deferred(SHOWN) == NOTHING
    assert fresh.tree_state == "s1"


def test_a_read_started_while_the_mutation_is_still_out_keeps_the_state():
    """A read parked behind one in flight, or asked while the mutation
    still runs, samples nothing new: the kept state stands."""
    fresh = _drawn()
    fresh.state_event("half-done", BUSY)
    fresh.read_started(busy=True)
    assert fresh.deferred_state == "half-done"


def test_a_kept_state_taken_beside_a_read_in_flight_waits_for_its_landing():
    """The retry's compare may start a read; the kept state looked at
    after it is then the read's to confirm, not a second reload."""
    fresh = _drawn()
    fresh.state_event("s1", BUSY)
    assert fresh.take_deferred(READING) == NOTHING
    assert fresh.watch_stale == "s1" and fresh.deferred_state is NO_STATE
    assert fresh.read_landed("s1", working=True, reread=False) == NOTHING  # the read drew it
    assert fresh.tree_state == "s1"


# -- a state beside a read in flight: confirmed ---------------------------------------------


def test_a_state_beside_a_read_in_flight_is_confirmed_when_it_differs():
    fresh = _drawn()
    assert fresh.state_event("s1", READING) == NOTHING
    assert fresh.watch_stale == "s1"
    assert fresh.read_landed("s0", working=True, reread=False) == Actions(confirm=True)
    assert fresh.watch_stale is None and fresh.tree_state == "s0"
    # Confirmed the same as drawn: the event's sample predated the read's.
    assert fresh.confirmed("s0", loading=False) == NOTHING
    assert fresh.tree_state == "s0"


def test_a_confirmed_state_that_differs_or_cannot_be_had_reloads():
    fresh = _drawn()
    fresh.state_event("s1", READING)
    fresh.read_landed("s0", working=True, reread=False)
    assert fresh.confirmed("s2", loading=False) == Actions(reload=True)
    assert fresh.tree_state == "s2"
    fresh.state_event("s3", READING)
    fresh.read_landed("s2", working=True, reread=False)
    assert fresh.confirmed(None, loading=False) == Actions(reload=True)
    assert fresh.tree_state is None


def test_a_confirmation_answered_while_another_read_is_out_is_dropped():
    fresh = _drawn()
    fresh.state_event("s1", READING)
    fresh.read_landed("s0", working=True, reread=False)
    assert fresh.confirmed("s9", loading=True) == NOTHING
    assert fresh.tree_state == "s0"


def test_a_landing_that_rereads_at_once_confirms_nothing():
    fresh = _drawn()
    fresh.state_event("s1", READING)
    assert fresh.read_landed("s0", working=True, reread=True) == NOTHING
    assert fresh.watch_stale is None


def test_a_stale_state_equal_to_the_read_s_own_confirms_nothing():
    fresh = _drawn()
    fresh.state_event("s1", READING)
    assert fresh.read_landed("s1", working=True, reread=False) == NOTHING


def test_a_commit_load_landing_confirms_nothing():
    fresh = _drawn()
    fresh.watch_stale = "s1"
    assert fresh.read_landed(None, working=False, reread=False) == NOTHING
    assert fresh.tree_state is None


# -- another cwd: what was owed is the other tree's -----------------------------------------


def test_dropping_the_watch_forgets_what_was_owed_but_not_the_drawn_state():
    fresh = _drawn()
    fresh.compare(HIDDEN)
    fresh.state_event("s1", HIDDEN)
    fresh.state_event("s2", READING)
    fresh.drop()
    assert not fresh.pending and fresh.watch_stale is None and fresh.deferred_state is NO_STATE
    assert fresh.tree_state == "s0"
    assert fresh.take_deferred(SHOWN) == NOTHING


def test_actions_are_false_when_nothing_is_asked():
    assert not NOTHING and not Actions(counted=True)
    assert Actions(compare=True) and Actions(retry=True) and Actions(reload=True) and Actions(confirm=True)
    assert gitfresh.NO_STATE is not None
