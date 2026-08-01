from pathlib import Path

from collins.activity import ActivityTracker, TranscriptActivity


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeTimers:
    """Stands in for GLib's timeout, so a sweep can be stepped by hand."""

    def __init__(self) -> None:
        self.callbacks: dict[int, callable] = {}
        self.removed: list[int] = []
        self._next = 1

    def add(self, _interval_ms, callback):
        source = self._next
        self._next += 1
        self.callbacks[source] = callback
        return source

    def remove(self, source):
        self.removed.append(source)
        self.callbacks.pop(source, None)

    def tick(self) -> None:
        for source, callback in list(self.callbacks.items()):
            if not callback():
                self.callbacks.pop(source, None)


def make_tracker(idle_s=2.0):
    clock, timers, changes = FakeClock(), FakeTimers(), []
    tracker = ActivityTracker(
        lambda sid, busy: changes.append((sid, busy)),
        idle_s=idle_s,
        clock=clock,
        add_timeout=timers.add,
        remove_timeout=timers.remove,
    )
    return tracker, clock, timers, changes


def test_first_output_reports_busy():
    tracker, _clock, _timers, changes = make_tracker()
    tracker.mark("a")
    assert tracker.is_busy("a")
    assert changes == [("a", True)]


def test_repeat_output_is_not_a_change():
    # mark() is called at redraw frequency; only transitions may reach the UI.
    tracker, clock, _timers, changes = make_tracker()
    tracker.mark("a")
    for _ in range(20):
        clock.advance(0.05)
        tracker.mark("a")
    assert changes == [("a", True)]


def test_goes_idle_once_output_stops():
    tracker, clock, timers, changes = make_tracker(idle_s=2.0)
    tracker.mark("a")
    clock.advance(1.9)
    timers.tick()
    assert tracker.is_busy("a")  # still inside the idle window
    clock.advance(0.2)
    timers.tick()
    assert not tracker.is_busy("a")
    assert changes == [("a", True), ("a", False)]


def test_output_during_the_idle_window_keeps_it_busy():
    tracker, clock, timers, changes = make_tracker(idle_s=2.0)
    tracker.mark("a")
    for _ in range(5):
        clock.advance(1.5)
        tracker.mark("a")
        timers.tick()
    assert changes == [("a", True)]


def test_sessions_time_out_independently():
    tracker, clock, timers, changes = make_tracker(idle_s=2.0)
    tracker.mark("a")
    clock.advance(1.5)
    tracker.mark("b")
    clock.advance(1.0)  # a is 2.5s stale, b only 1.0s
    timers.tick()
    assert changes == [("a", True), ("b", True), ("a", False)]
    assert tracker.busy() == {"b"}


def test_sweep_only_runs_while_something_is_busy():
    # An app with nothing working schedules nothing at all.
    tracker, clock, timers, _changes = make_tracker(idle_s=2.0)
    assert not timers.callbacks
    tracker.mark("a")
    assert len(timers.callbacks) == 1
    clock.advance(3)
    timers.tick()
    assert not timers.callbacks
    tracker.mark("a")  # and starts again on the next output
    assert len(timers.callbacks) == 1


def test_clear_stops_it_without_waiting():
    tracker, _clock, timers, changes = make_tracker()
    tracker.mark("a")
    tracker.clear("a")
    assert changes == [("a", True), ("a", False)]
    assert not timers.callbacks  # nothing left to time out


def test_clear_of_an_idle_session_says_nothing():
    tracker, _clock, _timers, changes = make_tracker()
    tracker.clear("a")
    assert changes == []


def test_stop_drops_everything():
    tracker, _clock, timers, _changes = make_tracker()
    tracker.mark("a")
    tracker.stop()
    assert not tracker.busy()
    assert not timers.callbacks


def test_empty_session_id_is_ignored():
    # A tab whose session id hasn't resolved yet has nothing to mark busy.
    tracker, _clock, _timers, changes = make_tracker()
    tracker.mark("")
    assert changes == []


class FakeStats:
    def __init__(self, readings: dict[str, tuple[float, int] | None]) -> None:
        self.readings = readings

    def __call__(self, path: Path):
        return self.readings.get(path.name)


def test_first_sighting_is_a_baseline_not_activity():
    # Without a previous reading, a transcript written a week ago looks exactly
    # like one written a moment ago.
    marks: list[str] = []
    stats = FakeStats({"a.jsonl": (100.0, 10)})
    watcher = TranscriptActivity(marks.append, stat=stats)
    watcher.poll({"a": Path("a.jsonl")})
    assert marks == []


def test_growth_marks_the_session_busy():
    marks: list[str] = []
    stats = FakeStats({"a.jsonl": (100.0, 10)})
    watcher = TranscriptActivity(marks.append, stat=stats)
    watcher.poll({"a": Path("a.jsonl")})
    stats.readings["a.jsonl"] = (101.0, 40)
    watcher.poll({"a": Path("a.jsonl")})
    assert marks == ["a"]
    watcher.poll({"a": Path("a.jsonl")})  # unchanged since
    assert marks == ["a"]


def test_unreadable_transcript_is_skipped():
    # A /bg fork whose transcript is still a stub the CLI hasn't written.
    marks: list[str] = []
    stats = FakeStats({"a.jsonl": None})
    watcher = TranscriptActivity(marks.append, stat=stats)
    watcher.poll({"a": Path("a.jsonl")})
    watcher.poll({"a": Path("a.jsonl")})
    assert marks == []


def test_a_session_that_comes_back_is_baselined_again():
    # It stopped being detached, ran on in a tab, and detached again: comparing
    # against the size it had before would report activity that isn't there.
    marks: list[str] = []
    stats = FakeStats({"a.jsonl": (100.0, 10)})
    watcher = TranscriptActivity(marks.append, stat=stats)
    watcher.poll({"a": Path("a.jsonl")})
    watcher.poll({})  # no longer detached
    stats.readings["a.jsonl"] = (200.0, 999)
    watcher.poll({"a": Path("a.jsonl")})
    assert marks == []
