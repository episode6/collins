# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The finish judge on the service (collins.service.finish): a finish edge
is a turn ending only when the session's transcript has moved since the
last counted one; an unmoved edge is held for the transcript's word and
dropped when none comes (what MainWindow._judge_finish / _hold_finish did,
PR-1.12a)."""

from collins.activity import FINISH, FINISH_CONFIRM_S, FINISH_DUPLICATE, FinishLedger
from collins.service.finish import FinishJudge


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

    def fire(self):
        for source, (_ms, fn, args) in list(self.due.items()):
            self.due.pop(source, None)
            fn(*args)


class FakeSession:
    def __init__(self, handle="s-1"):
        self.handle = handle
        self.finish_ledger = FinishLedger()
        self.stamp = (1, 1)
        self.size = 100
        self.updates = 0

    def finish_witness(self):
        return self.stamp, self.size

    def request_update(self):
        self.updates += 1


def make(session=None):
    sessions = {"sid": session} if session is not None else {}
    landed = []
    timers = Timers()
    judge = FinishJudge(sessions.get, landed.append, timers.add, timers.remove)
    return judge, landed, timers, sessions


def test_a_session_the_service_does_not_hold_passes():
    judge, landed, _timers, _sessions = make()
    assert judge.judge("sid") == FINISH
    judge.edge("sid")
    assert landed == ["sid"]


def test_an_unarmed_ledger_passes_every_edge():
    session = FakeSession()
    judge, landed, _timers, _sessions = make(session)
    assert judge.judge("sid") == FINISH
    judge.edge("sid")
    judge.edge("sid")
    assert landed == ["sid", "sid"]


def test_a_moved_transcript_lands_the_edge():
    session = FakeSession()
    session.finish_ledger.arm((1, 1), 100)
    judge, landed, timers, _sessions = make(session)
    session.stamp = (2, 1)
    judge.edge("sid")
    assert landed == ["sid"] and judge.held == {} and not timers.due


def test_an_unmoved_transcript_holds_the_edge_and_re_reads():
    session = FakeSession()
    session.finish_ledger.arm((1, 1), 100)
    judge, landed, timers, _sessions = make(session)
    judge.edge("sid")
    assert landed == [] and "sid" in judge.held and session.updates == 1
    assert timers.due and next(iter(timers.due.values()))[0] == int(FINISH_CONFIRM_S * 1000)
    # One held edge per session; a second inside the window is absorbed.
    judge.edge("sid")
    assert session.updates == 1 and len(timers.due) == 1


def test_a_read_landing_with_the_stamp_moved_delivers_the_held_edge():
    session = FakeSession()
    session.finish_ledger.arm((1, 1), 100)
    judge, landed, timers, _sessions = make(session)
    judge.edge("sid")
    session.stamp = (1, 2)
    judge.transcript_landed(session)
    assert landed == ["sid"] and judge.held == {} and not timers.due


def test_a_landing_for_another_session_leaves_the_hold():
    session = FakeSession()
    session.finish_ledger.arm((1, 1), 100)
    judge, landed, _timers, _sessions = make(session)
    judge.edge("sid")
    judge.transcript_landed(FakeSession("s-other"))
    assert landed == [] and "sid" in judge.held


def test_the_window_running_out_drops_an_unchanged_edge():
    session = FakeSession()
    session.finish_ledger.arm((1, 1), 100)
    judge, landed, timers, _sessions = make(session)
    judge.edge("sid")
    timers.fire()
    assert landed == [] and judge.held == {}
    assert session.finish_ledger.decide((1, 1), 100) == FINISH_DUPLICATE


def test_a_grown_file_is_promoted_to_a_finish_at_the_end_of_the_window():
    session = FakeSession()
    session.finish_ledger.arm((1, 1), 100)
    judge, landed, timers, _sessions = make(session)
    judge.edge("sid")
    session.size = 160  # growth the parser didn't understand is still growth
    timers.fire()
    assert landed == ["sid"]


def test_a_new_turn_drops_the_held_edge():
    session = FakeSession()
    session.finish_ledger.arm((1, 1), 100)
    judge, landed, timers, _sessions = make(session)
    judge.edge("sid")
    judge.drop("sid")
    assert judge.held == {} and not timers.due
    timers.fire()
    assert landed == []


def test_a_session_gone_under_the_hold_lands_nothing():
    session = FakeSession()
    session.finish_ledger.arm((1, 1), 100)
    judge, landed, timers, sessions = make(session)
    judge.edge("sid")
    del sessions["sid"]
    timers.fire()
    assert landed == [] and judge.held == {}


def test_stop_forgets_every_hold():
    session = FakeSession()
    session.finish_ledger.arm((1, 1), 100)
    judge, _landed, timers, _sessions = make(session)
    judge.edge("sid")
    judge.stop()
    assert judge.held == {} and not timers.due
