from pathlib import Path

from collins.activity import ActivityTracker, EchoGate, SpinnerWatch, TranscriptActivity


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


def test_a_mark_can_carry_its_own_idle_window():
    # A detached session's transcript grows in bursts — nothing lands while a
    # response generates or a tool runs — so its marks pass a wider window and
    # the pole rides out the quiet in between.
    tracker, clock, timers, changes = make_tracker(idle_s=2.0)
    tracker.mark("a", idle_s=10.0)
    clock.advance(8.0)
    timers.tick()
    assert tracker.is_busy("a")  # far past IDLE_S, still inside its own window
    clock.advance(2.5)
    timers.tick()
    assert not tracker.is_busy("a")
    assert changes == [("a", True), ("a", False)]


def test_the_latest_mark_decides_the_window():
    # A session that stops being detached (its tab reopened, say) is back on
    # the terminal's short window from its next mark.
    tracker, clock, timers, _changes = make_tracker(idle_s=2.0)
    tracker.mark("a", idle_s=10.0)
    clock.advance(1.0)
    tracker.mark("a")
    clock.advance(2.5)
    timers.tick()
    assert not tracker.is_busy("a")


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


# -- EchoGate: telling the agent's output from the terminal's answers ---------

GRID = (80, 24)


def make_gate(quiet_s=0.25, *, armed=True):
    # Armed by default: most tests are about telling echoes from output on a
    # tab whose user has already sent something. The startup hold — a fresh
    # gate counting nothing until the first submit — is tested unarmed.
    clock = FakeClock()
    gate = EchoGate(quiet_s=quiet_s, clock=clock)
    if armed:
        gate.arm()
    return gate, clock


def test_output_out_of_nowhere_counts():
    gate, _clock = make_gate()
    assert gate.counts(GRID)


def test_the_redraw_answering_a_keystroke_does_not_count():
    # The agent renders what the user types, so a keypress comes back as real
    # child output a few milliseconds later.
    gate, clock = make_gate()
    gate.counts(GRID)
    gate.poked()
    clock.advance(0.02)
    assert not gate.counts(GRID)


def test_output_after_the_echo_window_counts_again():
    # Enter was pressed, and what follows is the turn it started.
    gate, clock = make_gate()
    gate.counts(GRID)
    gate.poked()
    clock.advance(0.3)
    assert gate.counts(GRID)


def test_a_burst_of_typing_keeps_the_gate_shut():
    gate, clock = make_gate()
    gate.counts(GRID)
    for _ in range(5):
        gate.poked()
        clock.advance(0.1)
        assert not gate.counts(GRID)


def test_a_reflow_does_not_count():
    # A resize (or the first time a tab is shown at its real size) repaints the
    # whole screen with nothing having arrived from the agent.
    gate, _clock = make_gate()
    gate.counts(GRID)
    assert not gate.counts((100, 30))


def test_output_at_the_new_size_counts():
    gate, _clock = make_gate()
    gate.counts(GRID)
    gate.counts((100, 30))  # the reflow itself
    assert gate.counts((100, 30))


def test_the_first_redraw_seen_counts_once_armed():
    # No previous size to compare against doesn't read as a reflow.
    gate, _clock = make_gate()
    assert gate.counts(GRID)


# -- EchoGate: the startup hold ------------------------------------------------


def test_startup_paint_does_not_count():
    # A freshly spawned agent CLI paints a whole welcome screen without the
    # user having asked for anything; none of it may start a pole.
    gate, clock = make_gate(armed=False)
    assert not gate.counts(GRID)
    clock.advance(5.0)  # not a timing window: it never opens on its own
    assert not gate.counts(GRID)


def test_a_submitted_carriage_return_arms_the_gate():
    # An injected prompt or a question-card answer submits through the pty as
    # a "\r"; output past the echo window is then the turn it started.
    gate, clock = make_gate(armed=False)
    gate.counts(GRID)
    gate.poked("\x1b[B\x1b[B\r")
    clock.advance(0.3)
    assert gate.counts(GRID)


def test_a_spawn_time_shell_command_does_not_arm():
    # A tab that launches its CLI by feeding the shell ends the line with a
    # newline, not a carriage return: the CLI starting is still not a turn.
    gate, clock = make_gate(armed=False)
    gate.counts(GRID)
    gate.poked("claude --continue\n")
    clock.advance(0.3)
    assert not gate.counts(GRID)


def test_focus_reports_and_typing_do_not_arm():
    gate, clock = make_gate(armed=False)
    gate.counts(GRID)
    for text in ("\x1b[I", "h", "i", "\x1b[O"):
        gate.poked(text)
        clock.advance(0.3)
    assert not gate.counts(GRID)


def test_arm_reports_the_enter_key_directly():
    # The window arms on the bare Enter keypress itself, independent of how
    # VTE encodes the key for the child.
    gate, _clock = make_gate(armed=False)
    gate.counts(GRID)
    gate.arm()
    assert gate.counts(GRID)


# -- SpinnerWatch: first-column motion is an agent working ---------------------


def make_watch(sample_s=0.35, streak_s=1.4):
    clock = FakeClock()
    return SpinnerWatch(sample_s=sample_s, streak_s=streak_s, clock=clock), clock


def column(*chars):
    """A screen's first column: the given line-start characters, padded with
    blank rows to the test grid's height."""
    return tuple(chars) + ("",) * (GRID[1] - len(chars))


def spin(watch, clock, glyph, step=0.35):
    clock.advance(step)
    return watch.sample(column("❯", glyph), GRID)


def test_the_first_sample_is_a_baseline_not_motion():
    watch, _clock = make_watch()
    assert not watch.sample(column("❯", "✻"), GRID)


def test_a_still_screen_is_not_motion():
    # A prompt sitting idle redraws nothing; even sampled forever it stays quiet.
    watch, clock = make_watch()
    watch.sample(column("❯"), GRID)
    for _ in range(5):
        clock.advance(0.35)
        assert not watch.sample(column("❯"), GRID)


def test_a_spinner_cycling_at_a_line_start_is_motion():
    # The one change right after the baseline could be anything; the change on
    # its heels is what makes it an animation.
    watch, clock = make_watch()
    watch.sample(column("❯", "✻"), GRID)
    assert not spin(watch, clock, "✽")
    assert spin(watch, clock, "·")
    assert spin(watch, clock, "✢")


def test_a_single_repaint_is_not_motion():
    # A submitted prompt (or a spawn-time welcome paint) redraws the screen
    # once; nothing follows it, so no pole starts.
    watch, clock = make_watch()
    watch.sample(column("❯"), GRID)
    clock.advance(0.35)
    watch.sample(column("❯", "✻"), GRID)
    for _ in range(5):
        clock.advance(0.35)
        assert not watch.sample(column("❯", "✻"), GRID)


def test_changes_far_apart_are_unrelated_repaints():
    watch, clock = make_watch()
    watch.sample(column("❯"), GRID)
    assert not spin(watch, clock, "✻")
    assert not spin(watch, clock, "✽", step=60.0)  # a minute later: no streak
    assert spin(watch, clock, "·")  # but it seeded one: the next change counts


def test_motion_survives_a_coincidence_sample():
    # Sampling can land on the same glyph twice (the cycle aliasing against
    # the sample clock); the streak window is wider than one such miss.
    watch, clock = make_watch()
    watch.sample(column("❯", "✻"), GRID)
    spin(watch, clock, "✽")
    assert not spin(watch, clock, "✽")  # same frame again: no change seen
    assert spin(watch, clock, "·")


def test_scrolling_output_is_motion():
    # Lines marching up the screen change first characters everywhere. That
    # counts on purpose: flowing output is what the pole is for.
    lines = ["def f():", "    pass", "ok", "$ ", "❯"]
    watch, clock = make_watch()
    watch.sample(column(*[line[:1] for line in lines]), GRID)
    for _ in range(2):
        clock.advance(0.35)
        lines = lines[1:] + [lines[0]]
        motion = watch.sample(column(*[line[:1] for line in lines]), GRID)
    assert motion


def test_a_reflow_resets_the_baseline():
    # A resize rewraps every line; comparing across it would read the whole
    # screen as motion. The streak dies with it, so the next real change has
    # to earn a new one.
    watch, clock = make_watch()
    watch.sample(column("❯", "✻"), GRID)
    spin(watch, clock, "✽")
    clock.advance(0.35)
    assert not watch.sample(column("❯", "·"), (100, 30))
    clock.advance(0.35)
    assert not watch.sample(column("❯", "✢"), (100, 30))
    clock.advance(0.35)
    assert watch.sample(column("❯", "✻"), (100, 30))


def test_due_throttles_the_screen_reads():
    watch, clock = make_watch()
    assert watch.due()  # nothing sampled yet
    watch.sample(column("❯"), GRID)
    assert not watch.due()
    clock.advance(0.2)
    assert not watch.due()
    clock.advance(0.2)
    assert watch.due()
