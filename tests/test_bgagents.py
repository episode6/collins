# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The background agents on the service (collins.service.bgagents, spec
§3.22, PR-1.12d), over the real store and state and a fake `claude agents`
runner: a /bg handoff whose fork turns up within the watch's 30 tries is
recorded and its rows go from disabled-and-pending to running; one whose
agent never turns up is recorded on disk and then cleared; the replay at
start pairs a pending detach left by a previous service; the agent list's
word on working agents feeds the tracker while a session is attached to
one; and the gate (`can_background`) is the service's."""

from __future__ import annotations

import pytest

from collins.providers import BackgroundAgent
from collins.service.bgagents import PENDING, RUNNING, WATCH_ATTEMPTS, BackgroundAgents
from collins.sessions import discover_sessions
from collins.store import SessionStore


class FakeProvider:
    """The agent CLI: `background_agents` answers the next of *answers*
    (the last one again once they run out)."""

    id = "claude"
    supports_fork = True

    def __init__(self, *answers):
        self.answers = list(answers) or [[]]
        self.asked = 0

    def background_agents(self, include_finished=False):
        self.asked += 1
        index = min(self.asked - 1, len(self.answers) - 1)
        return list(self.answers[index])

    def transcripts_for_cwd(self, cwd):
        return []

    def background_exit(self):
        return "/bg\r"


class FakePoller:
    def __init__(self):
        self.background_ids: set[str] = set()
        self._on_change = lambda changed: None
        self.started = None
        self.polling = None
        self.refreshes = 0

    def start(self, dirs):
        self.started = list(dirs)

    def stop(self):
        pass

    def set_polling(self, enabled):
        self.polling = enabled

    def refresh(self):
        self.refreshes += 1

    def report(self, ids):
        """What BackgroundStatusPoller._apply does with a fetch."""
        changed = set(ids) ^ self.background_ids
        self.background_ids = set(ids)
        if changed:
            self._on_change(changed)


class Timers:
    def __init__(self):
        self.pending: dict[int, tuple] = {}
        self._next = 1

    def add(self, ms, fn, *args):
        source = self._next
        self._next += 1
        self.pending[source] = (ms, fn, args)
        return source

    def remove(self, source):
        self.pending.pop(source, None)

    def fire(self, ms):
        for source, (when, fn, args) in list(self.pending.items()):
            if when == ms:
                del self.pending[source]
                fn(*args)


class FakeSession:
    def __init__(self, session_id, provider, cwd="/home/user/alpha", attached_background=False):
        self.session_id = session_id
        self.provider = provider
        self.fork = False
        self.sandboxed = False
        self.attached_background = attached_background
        self.cwd = cwd
        self.nudged = 0

    def current_agent_cwd(self):
        return self.cwd

    def nudge_exit(self):
        self.nudged += 1


class FakeRecord:
    def __init__(self, session):
        self.session = session
        self.exited = False
        self.handle = "s-1"


class Activity:
    def __init__(self):
        self.readings = []

    def background_busy(self, ids):
        self.readings.append(ids)


@pytest.fixture
def world(app_state, projects_dir):
    _root, ids = projects_dir
    state = app_state.AppState(migrate=True, device=False)
    store = SessionStore(state)
    store._last_sessions = discover_sessions()
    store._apply()
    return store, state, ids


def make(world, provider, *records, deferred=None, activity=None, busy=None, settings=None):
    store, state, _ids = world
    timers = Timers()
    poller = FakePoller()
    sleeps = []

    def spawn(work):
        if deferred is not None:
            deferred.append(work)
        else:
            work()

    bg = BackgroundAgents(
        store=store,
        state=state,
        records=lambda: list(records),
        activity=activity,
        get_setting=(settings or {}).get,
        fetch_ids=lambda: set(),
        fetch_busy=busy or (lambda: set()),
        watch_dirs=lambda: ["/nowhere/jobs"],
        get_provider=lambda _name: provider,
        timeout_add=timers.add,
        source_remove=timers.remove,
        land=lambda fn, *args: fn(*args),
        spawn=spawn,
        sleep=sleeps.append,
        poller=poller,
    )
    return bg, poller, timers, sleeps


def item(store, session_id):
    return store.get_item(session_id)


# -- the handoff -------------------------------------------------------------------------


def test_a_fork_matched_within_the_watch_is_recorded_and_its_row_runs(world):
    store, state, ids = world
    old = ids["alpha1"]
    fork = "fork-0001"
    # Listed before the /bg: nothing. Then three tries see nothing new, the
    # fourth sees the fork in the session's directory.
    nothing = []
    found = [BackgroundAgent(session_id=fork, job_id="j1", cwd="/home/user/alpha")]
    provider = FakeProvider(nothing, nothing, nothing, nothing, found)
    session = FakeSession(old, provider)
    record = FakeRecord(session)
    deferred = []
    bg, poller, timers, sleeps = make(world, provider, record, deferred=deferred)
    bg.start()
    assert poller.started == ["/nowhere/jobs"]

    bg.handoff(record)
    # Before the watch says anything: on disk, the rows disabled and yellow.
    assert old in state.get_pending_detaches()
    assert item(store, old).backgrounding is True
    assert item(store, old).background == PENDING
    assert bg.detaching_now(old) and bg.is_detached(old)
    assert item(store, ids["alpha2"]).can_background is False  # the gate is shut app-wide

    deferred.pop()()  # the watch's thread
    assert len(sleeps) == 3 < WATCH_ATTEMPTS
    assert state.resolve_forward(old) == fork
    assert old not in state.get_pending_detaches()
    assert item(store, old).backgrounding is False  # confirmed: openable again
    assert item(store, old).background == PENDING  # until the agent list says so
    assert poller.refreshes == 1
    timers.fire(700)
    assert session.nudged == 1

    poller.report({fork})  # the agent list lists the fork
    assert item(store, old).background == RUNNING
    assert old not in bg.pending
    assert bg.is_detached(old)


def test_a_detach_whose_agent_never_appears_is_recorded_then_cleared(world):
    store, state, ids = world
    old = ids["alpha1"]
    provider = FakeProvider([])
    record = FakeRecord(FakeSession(old, provider))
    deferred = []
    bg, _poller, _timers, sleeps = make(world, provider, record, deferred=deferred)
    bg.start()
    bg.handoff(record)
    recorded = state.get_pending_detaches()[old]
    assert recorded == {"provider": "claude", "cwd": "/home/user/alpha", "uuid": recorded["uuid"]}
    deferred.pop()()  # the watch's thread: thirty tries, then it gives up
    assert len(sleeps) == WATCH_ATTEMPTS
    assert old not in state.get_pending_detaches()
    assert item(store, old).backgrounding is False
    assert item(store, old).background == ""
    assert not bg.is_detached(old)
    assert state.resolve_forward(old) == old


def test_a_detach_in_place_keeps_the_id_and_the_safety_timer_lets_go(world):
    store, state, ids = world
    old = ids["alpha1"]
    provider = FakeProvider([], [BackgroundAgent(session_id=old, job_id="j1", cwd="/home/user/alpha")])
    record = FakeRecord(FakeSession(old, provider))
    bg, _poller, timers, _sleeps = make(world, provider, record)
    bg.start()
    bg.handoff(record)
    assert state.resolve_forward(old) == old  # detached in place: nothing to record
    assert item(store, old).backgrounding is False
    assert item(store, old).background == PENDING
    timers.fire(45_000)  # never listed: the assumption goes
    assert item(store, old).background == ""


# -- across restarts -----------------------------------------------------------------------


def test_the_replay_at_start_pairs_a_pending_detach(world):
    store, state, ids = world
    old, gone = ids["alpha1"], ids["beta1"]
    state.set_pending_detach(old, provider="claude", cwd="/home/user/alpha", uuid="")
    state.set_pending_detach(gone, provider="claude", cwd="/home/user/beta", uuid="")
    provider = FakeProvider([BackgroundAgent(session_id="fork-0002", job_id="j2", cwd="/home/user/alpha")])
    bg, _poller, _timers, _sleeps = make(world, provider)
    bg.start()
    assert state.resolve_forward(old) == "fork-0002"
    assert state.resolve_forward(gone) == gone  # nothing matched: dropped
    assert state.get_pending_detaches() == {}


# -- the busy feed -------------------------------------------------------------------------


def test_the_busy_ids_feed_the_tracker_while_a_session_is_attached(world):
    _store, _state, ids = world
    provider = FakeProvider([])
    attached = FakeRecord(FakeSession(ids["alpha1"], provider, attached_background=True))
    activity = Activity()
    records = []
    bg, _poller, timers, _sleeps = make(
        world, provider, attached, activity=activity, busy=lambda: {ids["alpha1"]}
    )
    bg._records = lambda: list(records)
    bg.sync_busy_poll()
    assert activity.readings == [] and not timers.pending  # nobody attached: no poll
    records.append(attached)
    bg.sync_busy_poll()
    assert activity.readings == [{ids["alpha1"]}]  # the first answer at once
    timers.fire(next(iter(timers.pending.values()))[0])
    assert len(activity.readings) == 2
    records.clear()
    bg.sync_busy_poll()
    assert not timers.pending


# -- the gate and the setting -----------------------------------------------------------


def test_the_gate_is_the_services_and_one_handoff_shuts_it(world):
    store, _state, ids = world
    provider = FakeProvider([])
    one = FakeRecord(FakeSession(ids["alpha1"], provider))
    two = FakeRecord(FakeSession(ids["alpha2"], provider))
    deferred = []
    bg, _poller, _timers, _sleeps = make(world, provider, one, two, deferred=deferred)
    bg.start()
    assert item(store, ids["alpha1"]).can_background and item(store, ids["alpha2"]).can_background
    assert not item(store, ids["beta1"]).can_background  # no live session
    bg.handoff(one)
    assert not item(store, ids["alpha2"]).can_background
    bg.confirm_backgrounding(ids["alpha1"])
    assert item(store, ids["alpha2"]).can_background


def test_the_poll_setting_is_read_on_the_service(world):
    provider = FakeProvider([])
    settings = {"background_status_poll": True}
    bg, poller, _timers, _sleeps = make(world, provider, settings=settings)
    bg.start()
    assert poller.polling is True
    settings["background_status_poll"] = False
    bg.settings_changed()
    assert poller.polling is False


def test_a_worktree_a_background_agent_works_in_is_shared(world):
    store, _state, ids = world
    provider = FakeProvider([])
    bg, poller, _timers, _sleeps = make(world, provider)
    bg.start()
    assert not bg.worktree_shared(ids["alpha2"], "/home/user/alpha")
    poller.report({ids["alpha1"]})
    assert bg.worktree_shared(ids["alpha2"], "/home/user/alpha")
    assert bg.worktree_shared(ids["alpha1"], "/elsewhere")  # runs on as an agent itself
    assert bg.session_is_running(ids["alpha1"]) and not bg.session_is_running(ids["beta1"])


def test_stop_leaves_no_timer_and_no_listener_and_lands_nothing(world):
    store, state, ids = world
    old = ids["alpha1"]
    provider = FakeProvider([])
    attached = FakeRecord(FakeSession(ids["alpha2"], provider, attached_background=True))
    deferred = []
    bg, _poller, timers, _sleeps = make(world, provider, attached, deferred=deferred, activity=Activity())
    bg.start()
    bg.sync_busy_poll()
    bg.mark_backgrounding(old)
    assert timers.pending  # the safety timer and the busy poll
    handler = bg._refreshed_handler
    assert store.handler_is_connected(handler)
    bg.stop()
    assert not timers.pending and not bg.pending
    assert not store.handler_is_connected(handler)  # the store's refreshes reach it no more
    # A repair or a watch landing after the stop changes nothing.
    bg.repair_landed({"session": old, "old": old}, "fork-0009")
    bg._on_backgrounded(attached, old, "fork-0009")
    assert state.resolve_forward(old) == old
