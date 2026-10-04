# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The redraw guard's state machine (collins.redrawguard): raised on the
first redraw frame or an attach, lowered only by the answer to its latest
sentinel, or by the watchdog."""

from collins import redrawguard
from collins.redrawguard import FLAG_REDRAW, FLAG_REDRAW_END, RedrawGuard, sentinel


class Timers:
    def __init__(self):
        self.armed = {}
        self.next = 1

    def schedule(self, ms, fn):
        self.armed[self.next] = (ms, fn)
        self.next += 1
        return self.next - 1

    def cancel(self, source):
        self.armed.pop(source, None)

    def fire_all(self):
        for _, (_ms, fn) in list(self.armed.items()):
            fn()
        self.armed.clear()


def answer(generation):
    index = redrawguard.SENTINEL_BASE + generation % redrawguard.SENTINEL_SPAN
    return b"\x1b]4;%d;rgb:1111/2222/3333\x07" % index


def guard():
    timers = Timers()
    return RedrawGuard(timers.schedule, timers.cancel), timers


def test_an_attach_raises_and_the_latest_answer_lowers():
    g, timers = guard()
    assert g.on_commit(b"a")  # down: everything passes
    g.raise_for_attach()
    assert g.up and g.generation == 1
    assert g.on_frame(FLAG_REDRAW) == (False, None)  # announced: no reset here
    reset, sent = g.on_frame(FLAG_REDRAW | FLAG_REDRAW_END)
    assert (reset, sent) == (False, sentinel(1))
    assert not g.on_commit(b"\x1b[I")  # a focus report: swallowed
    assert not g.on_commit(answer(1))  # the answer: swallowed, and the guard lowers
    assert not g.up and g.dropped == 2 and timers.armed == {}
    assert g.on_commit(b"typed")


def test_a_redraw_not_announced_resets_first():
    g, _ = guard()
    reset, sent = g.on_frame(FLAG_REDRAW)
    assert reset and sent is None and g.up and g.in_redraw
    assert g.on_frame(FLAG_REDRAW) == (False, None)  # the next frames of the same redraw
    assert g.on_frame(FLAG_REDRAW_END) == (False, sentinel(1))
    assert g.on_frame(0) == (False, None)  # live output: nothing to do


def test_only_the_latest_sentinels_answer_lowers_the_guard():
    g, _ = guard()
    g.raise_for_attach()
    g.on_frame(FLAG_REDRAW | FLAG_REDRAW_END)
    g.raise_for_attach()  # a second attach before the first was answered
    assert g.generation == 2
    g.on_frame(FLAG_REDRAW | FLAG_REDRAW_END)
    assert not g.on_commit(answer(1)) and g.up  # the first sentinel's answer: stale
    assert not g.on_commit(answer(2)) and not g.up


def test_the_watchdog_lowers_a_guard_nothing_answered():
    g, timers = guard()
    g.raise_for_attach()
    assert len(timers.armed) == 1 and list(timers.armed.values())[0][0] == redrawguard.WATCHDOG_MS
    timers.fire_all()
    assert not g.up and g.expired == 1 and not g.in_redraw
    assert g.on_commit(b"typed")


def test_an_answer_arriving_after_the_watchdog_is_swallowed_once():
    g, timers = guard()
    g.raise_for_attach()
    timers.fire_all()
    assert not g.up
    assert not g.on_commit(answer(1))  # the late answer: swallowed, not typed
    assert g.on_commit(answer(1))  # only once
    assert g.on_commit(b"typed")
    g.raise_for_attach()
    timers.fire_all()
    assert g.on_commit(answer(1))  # an older generation's late answer is not the one waited for
    assert not g.on_commit(answer(2))


def test_a_failed_attach_lowers_the_guard_at_once():
    g, timers = guard()
    g.raise_for_attach()
    g.attach_failed()
    assert not g.up and timers.armed == {}


def test_raising_again_rearms_the_watchdog_once():
    g, timers = guard()
    g.raise_for_attach()
    first = list(timers.armed)[0]
    g.raise_for_attach()
    assert first not in timers.armed and len(timers.armed) == 1


def test_an_answer_with_another_index_or_a_malformed_one_is_ignored():
    g, _ = guard()
    g.raise_for_attach()
    assert not g.on_commit(b"\x1b]4;18;rgb:0000/0000/0000\x07") and g.up
    assert not g.on_commit(b"\x1b]4;17;rgb:zzzz\x07") and g.up  # the index, a colour that is not one
    assert not g.on_commit(b"\x1b]4;" + b"9" * 400 + b";rgb:0000/0000/0000\x07") and g.up
    assert not g.on_commit(answer(1)) and not g.up


def test_sentinel_indices_stay_in_the_palettes_free_range():
    for generation in (1, 2, 239, 240, 241, 10**6):
        index = int(sentinel(generation).split(b";")[1])
        assert 16 <= index <= 255
