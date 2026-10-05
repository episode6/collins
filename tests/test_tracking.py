# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The activity tracker on the service (collins.service.tracking, D29,
PR-1.12a), through fakes: a verdict reaches a session with no row as a
`session` field by handle and a session with rows as the store's flags; a
counted finish flags the rows, tells the session's clients and announces,
with the window's three exemptions; a resolved session's handle edge
passes while its id edge is judged; the background-busy poll's answer
marks and finishes the attached session."""

from collins.activity import PROGRESS_FINISH_GRACE_S, EchoGate, FinishLedger
from collins.service.tracking import ServiceActivity

SID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
FORK = "1f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"


class Timers:
    def __init__(self):
        self.due = {}
        self.next = 1

    def add(self, ms, fn, *args):
        source = self.next
        self.next += 1
        self.due[source] = (ms, fn, args)
        return source

    def remove(self, source):
        self.due.pop(source, None)


class FakeProgress:
    def __init__(self):
        self.ended = 0

    def turn_ended(self):
        self.ended += 1


class FakeSession:
    def __init__(self, handle, session_id=None, attached_background=False):
        self.handle = handle
        self.session_id = session_id
        self.attached_background = attached_background
        self.echo_gate = EchoGate()
        self.progress = FakeProgress()
        self.finish_ledger = FinishLedger()
        self.stamp, self.size = (1, 1), 100
        self.updates = 0

    def finish_witness(self):
        return self.stamp, self.size

    def request_update(self):
        self.updates += 1

    def background_descendant_cmdlines(self):
        return set(getattr(self, "cmdlines", ()))

    def has_background_descendant(self, ignore):
        return False


class FakeRecord:
    def __init__(self, session):
        self.session = session
        self.handle = session.handle
        self.sent = []
        self.on_settled = None

    def send(self, fields, active_only=False):
        self.sent.append(dict(fields))

    def refresh_process_facts(self):
        pass


class Item:
    def __init__(self):
        self.busy = False
        self.unread = False
        self.backgrounding = False
        self.status = "open"


class FakeStore:
    def __init__(self, *ids):
        self.items = {sid: Item() for sid in ids}
        self.represented = {}  # session id -> the rows standing for it

    def connect(self, name, fn):
        pass

    def rows_representing(self, session_id):
        return list(self.represented.get(session_id, [session_id] if session_id in self.items else []))

    def get_item(self, session_id):
        return self.items.get(session_id)

    def set_busy(self, session_id, flag):
        self.items[session_id].busy = flag

    def set_unread(self, session_id, flag):
        self.items[session_id].unread = flag


class FakeState:
    def __init__(self):
        self.chains = {}
        self.baselines = {}

    def forward_chain(self, session_id):
        return list(self.chains.get(session_id, [session_id]))

    def get_process_baseline(self, session_id):
        return set(self.baselines.get(session_id, ()))

    def set_process_baseline(self, session_id, cmdlines):
        self.baselines[session_id] = set(cmdlines)


def make(*records, store=None, state=None, fetch=None):
    timers = Timers()
    announced = []
    activity = ServiceActivity(
        records=lambda: list(records),
        store=store,
        state=state,
        get_setting=lambda key: True,
        timeout_add=timers.add,
        source_remove=timers.remove,
        land=lambda fn, *args: fn(*args),
        fetch_busy=fetch or (lambda: set()),
        announce=announced.append,
    )
    return activity, timers, announced


# -- the verdicts' two roads ---------------------------------------------------------


def test_a_session_with_no_row_hears_busy_and_finished_by_handle():
    record = FakeRecord(FakeSession("s-1"))
    activity, _timers, announced = make(record)
    activity.tracker.mark("s-1")
    assert record.sent == [{"busy": True}]
    activity.tracker.finish("s-1")
    assert record.sent == [{"busy": True}, {"busy": False}, {"finished": True}]
    assert announced == []  # the placeholder's flag is the tab's; nothing to announce yet


def test_a_session_with_rows_is_the_stores_busy_flag():
    store, state = FakeStore(SID), FakeState()
    record = FakeRecord(FakeSession("s-1", SID))
    activity, _timers, _announced = make(record, store=store, state=state)
    activity.tracker.mark(SID)
    assert store.items[SID].busy is True and record.sent == []
    activity.tracker.clear(SID)
    assert store.items[SID].busy is False


def test_a_resolved_sessions_handle_edge_passes_while_its_id_edge_is_judged():
    """Both keys are marked on every verdict (`_keys`): the handle's
    finish reaches the tab for its placeholder with no transcript to judge
    by; the id's goes through the judge, where an armed ledger that saw
    no movement holds it."""
    store, state = FakeStore(SID), FakeState()
    session = FakeSession("s-1", SID)
    session.finish_ledger.arm((1, 1), 100)
    record = FakeRecord(session)
    activity, timers, announced = make(record, store=store, state=state)
    activity.tracker.mark("s-1")
    activity.tracker.mark(SID)
    activity.tracker.finish("s-1")
    assert {"finished": True} in record.sent
    activity.tracker.finish(SID)
    assert SID in activity.judge.held and session.updates == 1
    assert store.items[SID].unread is False and announced == []
    session.stamp = (2, 1)
    activity.transcript_landed(record)
    assert store.items[SID].unread is True and announced == [SID]
    assert record.sent.count({"finished": True}) == 2  # the id's, now counted


# -- the session's own writes --------------------------------------------------------


def test_the_sessions_own_submit_takes_the_baselines_last_snapshot():
    """An injected prompt, a switch or a close flow's keys never arrive as
    an input frame: the session's host announces them (`input_sent`) and
    the tracker snapshots the plumbing baseline on the "\\r", as it does
    for a typed Enter (PR 602's `_on_input_sent`, moved with the tracker).
    The gate itself the session pokes; no pre-emptive mark is taken."""
    state = FakeState()
    session = FakeSession("s-1", SID)
    record = FakeRecord(session)
    activity, _timers, _announced = make(record, state=state)
    activity.started(record, fresh=True)
    assert activity.startup_held(session)
    session.cmdlines = {"mcp-server --stdio"}
    activity.input_sent(record, "hello")
    assert "mcp-server --stdio" not in activity._captures.get("s-1", set())
    activity.input_sent(record, "\r")
    assert "mcp-server --stdio" in activity._captures["s-1"]
    assert state.baselines[SID] == {"mcp-server --stdio"}
    assert not activity.tracker.busy()  # the pole waits for the agent's own hint
    # The gate armed by the session's own poke releases the hold; from then
    # on the baseline is frozen.
    session.echo_gate.poked("\r")
    assert not activity.startup_held(session)
    session.cmdlines = {"mcp-server --stdio", "bash -c work"}
    activity.input_sent(record, "\r")
    assert state.baselines[SID] == {"mcp-server --stdio"}


# -- a counted finish and its exemptions ---------------------------------------------


def test_a_counted_finish_flags_the_rows_tells_the_clients_and_announces():
    store, state = FakeStore(SID), FakeState()
    record = FakeRecord(FakeSession("s-1", SID))
    activity, _timers, announced = make(record, store=store, state=state)
    activity.tracker.mark(SID)
    activity.tracker.finish(SID)
    assert store.items[SID].unread is True
    assert record.sent[-1] == {"finished": True} and announced == [SID]


def test_a_row_still_running_under_another_of_its_ids_is_not_finished():
    """A /bg fork mid-turn: the origin's row stands for the fork too, and
    the fork is busy — neither a flag nor an announcement until the
    fork's own edge."""
    store, state = FakeStore(SID), FakeState()
    state.chains[SID] = [SID, FORK]
    origin, fork = FakeRecord(FakeSession("s-1", SID)), FakeRecord(FakeSession("s-2", FORK))
    activity, _timers, announced = make(origin, fork, store=store, state=state)
    activity.tracker.mark(SID)
    activity.tracker.mark(FORK)
    activity.tracker.finish(SID)
    assert store.items[SID].unread is False and announced == []
    assert {"finished": True} not in origin.sent


def test_a_detaching_sessions_parting_clear_is_a_handoff_not_a_turn():
    store, state = FakeStore(SID), FakeState()
    store.items[SID].backgrounding = True  # the window's flag, a client's
    record = FakeRecord(FakeSession("s-1", SID))
    activity, _timers, announced = make(record, store=store, state=state)
    activity.tracker.mark(SID)
    activity.tracker.finish(SID)
    assert store.items[SID].unread is False and announced == []
    assert {"finished": True} not in record.sent


def test_a_detached_row_with_no_tab_keeps_its_status_line():
    """A row running as a background agent with no tab: its line is the
    yellow of its status, never a flag — but the finish is real (the
    agent list said so) and the clients still hear it, as the window's
    `_refresh_prs_after_run` ran for it too."""
    store, state = FakeStore(SID), FakeState()
    store.items[SID].status = "background"
    record = FakeRecord(FakeSession("s-1", SID))
    activity, _timers, announced = make(record, store=store, state=state)
    activity.tracker.mark(SID)
    activity.tracker.finish(SID)
    assert store.items[SID].unread is False
    assert record.sent[-1] == {"finished": True} and announced == [SID]


def test_a_new_turn_drops_a_held_finish():
    store, state = FakeStore(SID), FakeState()
    session = FakeSession("s-1", SID)
    session.finish_ledger.arm((1, 1), 100)
    record = FakeRecord(session)
    activity, _timers, _announced = make(record, store=store, state=state)
    activity.tracker.mark(SID)
    activity.tracker.finish(SID)
    assert SID in activity.judge.held
    activity.tracker.mark(SID)
    assert activity.judge.held == {}


# -- the background-busy poll ------------------------------------------------------


def test_the_agent_lists_word_marks_and_finishes_the_attached_session():
    store, state = FakeStore(SID), FakeState()
    session = FakeSession("s-1", SID, attached_background=True)
    record = FakeRecord(session)
    answers = [set(), {SID}]
    activity, timers, _announced = make(record, store=store, state=state, fetch=lambda: answers.pop())
    activity.started(record, fresh=False)  # starts the poll: the first answer is fetched at once
    assert store.items[SID].busy is True
    activity._poll_background_busy()  # the next tick: the agent went idle
    assert session.progress.ended == 1
    assert activity.tracker.finish_pending(SID)  # finished with the progress grace, not outright
    assert activity.tracker.is_busy(SID)
    activity.stop()


def test_only_an_attached_sessions_agent_counts_and_no_answer_changes_nothing():
    store, state = FakeStore(SID, FORK), FakeState()
    record = FakeRecord(FakeSession("s-1", SID, attached_background=True))
    activity, _timers, _announced = make(record, store=store, state=state)
    activity._apply_background_busy({FORK})  # busy, but no live session is attached to it
    assert store.items[FORK].busy is False and not activity.tracker.busy()
    activity._apply_background_busy({SID})
    assert store.items[SID].busy is True
    activity._apply_background_busy(None)  # a failed read: no answer, nothing finished
    assert store.items[SID].busy is True and not activity.tracker.finish_pending(SID)


def test_the_attached_session_is_found_through_the_forward_chain():
    """The agent list names the conversation's newest id; the live session
    attached to it may be under an earlier one of the chain."""
    store, state = FakeStore(SID), FakeState()
    state.chains[FORK] = [SID, FORK]
    record = FakeRecord(FakeSession("s-1", SID, attached_background=True))
    activity, _timers, _announced = make(record, store=store, state=state)
    activity._apply_background_busy({FORK})
    assert activity.tracker.is_busy(FORK)
    activity._apply_background_busy(set())
    assert activity.tracker.finish_pending(FORK) and record.session.progress.ended == 1
    assert PROGRESS_FINISH_GRACE_S > 0
