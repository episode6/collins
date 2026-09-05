from collins.activity import (
    ActivityTracker,
    BackgroundBusyWatch,
    EchoGate,
    ProgressWatch,
    SpinnerWatch,
)


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


def make_finish_tracker(idle_s=2.0):
    clock, timers, changes, finished = FakeClock(), FakeTimers(), [], []
    tracker = ActivityTracker(
        lambda sid, busy: changes.append((sid, busy)),
        on_finished=finished.append,
        idle_s=idle_s,
        clock=clock,
        add_timeout=timers.add,
        remove_timeout=timers.remove,
    )
    return tracker, clock, timers, changes, finished


def test_output_running_out_on_its_own_is_a_finish():
    tracker, clock, timers, changes, finished = make_finish_tracker(idle_s=2.0)
    tracker.mark("a")
    clock.advance(2.5)
    timers.tick()
    # The busy transition lands first, so a finish handler sees the pole
    # already down when it flags the row.
    assert changes == [("a", True), ("a", False)]
    assert finished == ["a"]


def test_an_explicit_clear_is_teardown_not_a_finish():
    # clear() means the tab closed or the detach ended — nothing completed,
    # so nothing should be flagged as freshly done.
    tracker, _clock, _timers, changes, finished = make_finish_tracker()
    tracker.mark("a")
    tracker.clear("a")
    assert changes == [("a", True), ("a", False)]
    assert finished == []


def test_stop_reports_no_finishes():
    tracker, _clock, _timers, _changes, finished = make_finish_tracker()
    tracker.mark("a")
    tracker.stop()
    assert finished == []


def test_finish_is_the_sweeps_finish_without_the_wait():
    # The agent said the turn is over (progress termprop clear): pole down
    # now, and the run flagged complete — no idle window to wait out.
    tracker, _clock, _timers, changes, finished = make_finish_tracker()
    tracker.mark("a")
    tracker.finish("a")
    assert changes == [("a", True), ("a", False)]
    assert finished == ["a"]


def test_finish_on_an_idle_session_is_a_no_op():
    # The CLI clears progress repeatedly at shutdown; only the first one that
    # actually ends a run may flag anything.
    tracker, _clock, _timers, changes, finished = make_finish_tracker()
    tracker.finish("a")
    tracker.mark("a")
    tracker.finish("a")
    tracker.finish("a")
    assert changes == [("a", True), ("a", False)]
    assert finished == ["a"]


# -- ProgressWatch --------------------------------------------------------


def make_progress_watch(quiet_s=2.0):
    clock = FakeClock()
    return ProgressWatch(quiet_s=quiet_s, clock=clock), clock


def test_every_busy_hint_asks_for_a_mark():
    # ACTIVE, ERROR, INDETERMINATE, PAUSED — the agent calls all of them
    # mid-turn (a permission prompt reports a clear instead, observed).
    watch, _clock = make_progress_watch()
    for hint in (1, 2, 3, 4):
        assert watch.reading(hint) == "mark"


def test_a_clear_before_any_busy_hint_means_nothing():
    # A tab whose CLI never spoke progress keeps its inferred poles: a stray
    # clear (or VTE reporting the property unset) may not cut them down.
    watch, _clock = make_progress_watch()
    assert watch.reading(None) is None
    assert watch.reading(0) is None


def test_a_clear_after_a_busy_hint_is_a_finish():
    watch, _clock = make_progress_watch()
    watch.reading(3)
    assert watch.reading(None) == "finish"
    # VTE reports the CLI's "remove progress" as no value, but an explicit
    # INACTIVE reads the same.
    watch.reading(1)
    assert watch.reading(0) == "finish"


def test_quiet_covers_the_beat_after_a_finish():
    watch, clock = make_progress_watch(quiet_s=2.0)
    assert not watch.quiet()  # nothing has finished yet
    watch.reading(3)
    assert not watch.quiet()  # mid-turn: redraws count as ever
    watch.reading(None)
    assert watch.quiet()  # trailing repaints may not restart the pole
    clock.advance(2.5)
    assert not watch.quiet()  # the beat passed; inference is back in charge


def test_another_source_can_open_the_same_quiet_window():
    # A tab attached to a background agent never gets a termprop clear (the
    # daemon spawns the agent without the declarations the CLI's progress
    # emission is gated on), so the agent list's "went idle" ends its turn —
    # and has to silence the trailing repaints the same way.
    watch, clock = make_progress_watch(quiet_s=2.0)
    watch.turn_ended()
    assert watch.quiet()
    clock.advance(2.5)
    assert not watch.quiet()


def test_a_turn_ended_elsewhere_does_not_make_the_tab_spoken_for():
    # turn_ended is not this tab announcing progress: a later clear it never
    # preceded with a busy hint still means nothing.
    watch, _clock = make_progress_watch()
    watch.turn_ended()
    assert watch.reading(None) is None


# -- BackgroundBusyWatch: the agent list's word on a detached agent -----------


def test_every_busy_background_agent_is_marked():
    watch = BackgroundBusyWatch()
    assert watch.reading({"a", "b"}) == ({"a", "b"}, set())


def test_an_agent_that_goes_idle_is_finished():
    watch = BackgroundBusyWatch()
    watch.reading({"a", "b"})
    # `b` is still working; only `a`'s run is over.
    assert watch.reading({"b"}) == ({"b"}, {"a"})


def test_an_agent_that_leaves_the_list_is_finished_too():
    # Its job ended, or its tab closed and the window stopped watching it:
    # either way it isn't working here any more.
    watch = BackgroundBusyWatch()
    watch.reading({"a"})
    assert watch.reading(set()) == (set(), {"a"})


def test_an_agent_never_seen_working_has_no_run_to_finish():
    # The same rule ProgressWatch applies to a tab that never spoke a busy
    # hint: a pole this watch didn't raise is not its to cut down.
    watch = BackgroundBusyWatch()
    assert watch.reading(set()) == (set(), set())
    watch.reading({"a"})
    watch.reading(set())
    assert watch.reading(set()) == (set(), set())  # finished once, not again


def test_a_still_working_agent_is_marked_every_reading():
    # The mark's idle window is a backstop, not a heartbeat's replacement:
    # every poll refreshes it for as long as the agent is working.
    watch = BackgroundBusyWatch()
    for _ in range(3):
        assert watch.reading(["a"]) == ({"a"}, set())


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


def test_armed_is_readable_for_the_fresh_spawn_hold():
    # On a freshly spawned CLI the window holds even the ungated pole
    # starters until the first submit, by reading the gate's state directly.
    gate, _clock = make_gate(armed=False)
    assert not gate.armed
    gate.poked("h")  # typing alone is not a submit
    assert not gate.armed
    gate.poked("\r")
    assert gate.armed


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


def test_a_finish_with_grace_waits_for_the_next_busy_hint():
    # The CLI clears its progress hint for a beat between tool calls, so a
    # clear only arms the finish; the busy hint that follows disarms it, and
    # nothing is flagged for a turn still going.
    tracker, clock, timers, changes, finished = make_finish_tracker()
    tracker.mark("a", idle_s=60.0)
    tracker.finish("a", grace_s=3.0)
    assert tracker.is_busy("a")  # the pole stays up through the wait
    assert tracker.finish_pending("a")
    clock.advance(1.0)
    timers.tick()
    tracker.resume("a")
    tracker.mark("a", idle_s=60.0)
    clock.advance(5.0)
    timers.tick()
    assert tracker.is_busy("a")
    assert not tracker.finish_pending("a")
    assert changes == [("a", True)]
    assert finished == []


def test_a_finish_with_grace_lands_when_the_grace_runs_out():
    tracker, clock, timers, changes, finished = make_finish_tracker()
    tracker.mark("a", idle_s=60.0)
    tracker.finish("a", grace_s=3.0)
    clock.advance(2.9)
    timers.tick()
    assert finished == []
    # Redraw marks inside the grace (the prompt box returning) don't push
    # the finish out: only a busy hint, through resume(), does.
    tracker.mark("a")
    clock.advance(0.2)
    timers.tick()
    assert not tracker.is_busy("a")
    assert changes == [("a", True), ("a", False)]
    assert finished == ["a"]


def test_repeated_clears_keep_the_first_grace_deadline():
    tracker, clock, timers, _changes, finished = make_finish_tracker()
    tracker.mark("a", idle_s=60.0)
    tracker.finish("a", grace_s=3.0)
    clock.advance(2.0)
    tracker.finish("a", grace_s=3.0)
    clock.advance(1.1)
    timers.tick()
    assert finished == ["a"]


def test_clear_and_stop_drop_an_armed_finish():
    tracker, clock, timers, _changes, finished = make_finish_tracker()
    tracker.mark("a", idle_s=60.0)
    tracker.finish("a", grace_s=3.0)
    tracker.clear("a")  # teardown: its tab closed
    assert not tracker.finish_pending("a")
    tracker.mark("b", idle_s=60.0)
    tracker.finish("b", grace_s=3.0)
    tracker.stop()
    clock.advance(4.0)
    timers.tick()
    assert finished == []


def test_a_grace_finish_on_an_idle_session_is_a_no_op():
    tracker, _clock, _timers, changes, _finished = make_finish_tracker()
    tracker.finish("a", grace_s=3.0)
    assert not tracker.finish_pending("a")
    assert changes == []
