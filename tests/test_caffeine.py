import pytest

from collins.caffeine import (
    ACTIVE_GRACE_S,
    DEFAULT_GRACE_MIN,
    DURATION_KEYS,
    INDEFINITE,
    WHILE_ACTIVE,
    countdown_tooltip,
    duration_label,
    duration_seconds,
    follow_poll,
    follows_activity,
    format_remaining,
    grace_seconds,
    toggle_tooltip,
)


def test_durations_offered():
    assert DURATION_KEYS == (WHILE_ACTIVE, "1h", "2h", "3h", "6h", "12h", INDEFINITE)


@pytest.mark.parametrize(
    "key,seconds",
    [("1h", 3600), ("2h", 7200), ("3h", 10800), ("6h", 21600), ("12h", 43200)],
)
def test_timed_durations(key, seconds):
    assert duration_seconds(key) == seconds


def test_indefinite_has_no_deadline():
    assert duration_seconds(INDEFINITE) is None


@pytest.mark.parametrize("key", ["", "4h", "1", "90m", "0h", "indefinitely"])
def test_unknown_keys_read_as_indefinite(key):
    # A stale or hand-edited setting may leave Caffeine Mode on longer than the
    # user meant, which they can see and undo — never cut it short unasked.
    assert duration_seconds(key) is None


def test_while_active_has_no_fixed_length():
    # Its deadline comes from the sessions, so there is no span to count down
    # until they all stop; the key is what tells the two open-ended options
    # apart.
    assert duration_seconds(WHILE_ACTIVE) is None
    assert follows_activity(WHILE_ACTIVE)


@pytest.mark.parametrize("key", ["", INDEFINITE, "1h", "12h", "Active", "while-active", "nonsense"])
def test_only_the_exact_key_follows_the_sessions(key):
    # An unknown key falls through to indefinite, which keeps the machine
    # awake — never to a mode that would turn it off five minutes later.
    assert not follows_activity(key)


def test_grace_defaults_to_five_minutes():
    assert ACTIVE_GRACE_S == 300
    assert format_remaining(ACTIVE_GRACE_S) == "5:00"
    assert DEFAULT_GRACE_MIN == 5
    assert grace_seconds(DEFAULT_GRACE_MIN) == ACTIVE_GRACE_S


def test_the_grace_setting_is_in_minutes():
    assert grace_seconds(1) == 60
    assert grace_seconds(30) == 1800
    assert grace_seconds("15") == 900  # a hand-edited state.json still counts
    assert grace_seconds(15.0) == 900  # a whole number of minutes, however typed


@pytest.mark.parametrize("bad", [None, "", "soon", "2.5", 2.5, 0, -5])
def test_a_bad_grace_setting_falls_back_to_the_default(bad):
    # Garbage must never read as a zero-length grace that lets the machine
    # sleep the instant work stops — the default is the honest fallback. A
    # fractional value counts as garbage too: whole minutes only, never a
    # silent truncation to fewer than asked for.
    assert grace_seconds(bad) == ACTIVE_GRACE_S


def test_working_sessions_leave_nothing_to_count_down():
    # No deadline while the machine is being used for something — which is also
    # what keeps a countdown out of the header. Holding already, there is
    # nothing to do either.
    assert follow_poll(working=True, holding=True, deadline=None, now=1000) == (None, None)
    # ...including one dropped mid-countdown when work starts again.
    assert follow_poll(working=True, holding=True, deadline=1200, now=1000) == (None, None)


def test_the_grace_is_armed_when_the_last_session_stops():
    assert follow_poll(working=False, holding=True, deadline=None, now=1000) == (
        1000 + ACTIVE_GRACE_S,
        None,
    )


def test_a_running_grace_is_left_alone():
    # Poll after poll with nothing working, the same deadline comes back — the
    # countdown runs down instead of resetting to 5:00 every second.
    deadline, _ = follow_poll(working=False, holding=True, deadline=None, now=1000)
    for now in (1001, 1030, 1100, 1290):
        assert follow_poll(working=False, holding=True, deadline=deadline, now=now) == (
            deadline,
            None,
        )


def test_work_resets_the_grace():
    # The reset the feature promises, poll by poll: idle arms it, a burst of
    # work clears it, and going quiet again starts the five minutes over rather
    # than resuming what was left of them.
    first, _ = follow_poll(working=False, holding=True, deadline=None, now=1000)
    cleared, _ = follow_poll(working=True, holding=True, deadline=first, now=1200)
    assert cleared is None
    second, _ = follow_poll(working=False, holding=True, deadline=cleared, now=1400)
    assert second == 1400 + ACTIVE_GRACE_S
    assert second > first


def test_the_grace_running_out_is_a_doze_not_an_off():
    # The countdown reaching zero releases the machine but ends nothing: the
    # deadline is cleared rather than left to read as a 0:00 countdown, and
    # the mode keeps polling.
    deadline, _ = follow_poll(working=False, holding=True, deadline=None, now=1000)
    assert follow_poll(working=False, holding=True, deadline=deadline, now=deadline) == (
        None,
        "release",
    )
    # A poll that missed the exact moment still releases.
    assert follow_poll(working=False, holding=True, deadline=deadline, now=deadline + 7) == (
        None,
        "release",
    )


def test_a_dozing_mode_lets_the_machine_rest():
    # Idle and not holding: no fresh grace is armed — that would wake the
    # countdown for nothing — and no inhibitor is touched, however long the
    # doze has lasted.
    for now in (2000, 5_000_000):
        assert follow_poll(working=False, holding=False, deadline=None, now=now) == (None, None)


def test_work_wakes_a_dozing_mode():
    # The morning-after case — the whole reason dozing exists: sessions idle
    # all night under a doze, and the first turn of the day takes the machine
    # back, no matter how long it rested.
    assert follow_poll(working=True, holding=False, deadline=None, now=9_999_999) == (
        None,
        "take",
    )


def test_the_grace_unit_follows_its_caller():
    # The app polls a monotonic clock in microseconds, so now/grace only have
    # to agree with each other.
    micros = ACTIVE_GRACE_S * 1_000_000
    deadline, _ = follow_poll(
        working=False, holding=True, deadline=None, now=5_000_000, grace=micros
    )
    assert deadline == 5_000_000 + micros


def test_labels():
    assert duration_label("1h") == "1 hour"  # singular
    assert duration_label("12h") == "12 hours"
    assert duration_label(INDEFINITE) == "Indefinitely"
    assert duration_label("nonsense") == "Indefinitely"
    assert duration_label(WHILE_ACTIVE) == "Until idle"


@pytest.mark.parametrize("on", [False, True])
def test_tooltip_only_promises_the_screen_when_it_is_kept_on(on):
    kept = toggle_tooltip(on=on, keep_screen_on=True)
    free = toggle_tooltip(on=on, keep_screen_on=False)
    assert "screen" in kept and "screen" in free  # both say what happens to it
    assert kept != free
    # Whatever the setting, the computer staying awake is never in doubt.
    assert "computer" in kept and "computer" in free


@pytest.mark.parametrize("keep_screen_on", [True, False])
def test_while_active_tooltip_says_why_it_is_on(keep_screen_on):
    # With sessions working there is no countdown showing, so the tooltip is
    # the only thing that can say what Caffeine Mode is waiting on.
    tooltip = toggle_tooltip(on=True, keep_screen_on=keep_screen_on, while_active=True)
    assert "working" in tooltip
    assert tooltip != toggle_tooltip(on=True, keep_screen_on=keep_screen_on)
    # It still says what "awake" covers, exactly like every other case.
    assert "screen" in tooltip and "computer" in tooltip
    assert tooltip != toggle_tooltip(
        on=True, keep_screen_on=not keep_screen_on, while_active=True
    )


@pytest.mark.parametrize("keep_screen_on", [True, False])
def test_dozing_tooltip_promises_nothing_it_is_not_holding(keep_screen_on):
    # A dozing mode inhibits nothing: the cup is lit because the mode is
    # armed, and the tooltip must say the machine may sleep — not claim a
    # screen held on that the grace already let go of.
    tooltip = toggle_tooltip(
        on=True, keep_screen_on=keep_screen_on, while_active=True, dozing=True
    )
    assert "dozing" in tooltip
    # It still says what "awake" will cover once work resumes.
    assert "screen" in tooltip and "computer" in tooltip
    assert tooltip != toggle_tooltip(on=True, keep_screen_on=keep_screen_on, while_active=True)
    assert tooltip != toggle_tooltip(
        on=True, keep_screen_on=not keep_screen_on, while_active=True, dozing=True
    )


def test_tooltip_distinguishes_on_from_off():
    for keep_screen_on in (True, False):
        assert toggle_tooltip(on=True, keep_screen_on=keep_screen_on) != toggle_tooltip(
            on=False, keep_screen_on=keep_screen_on
        )


@pytest.mark.parametrize("keep_screen_on", [True, False])
def test_countdown_tooltip_carries_the_time_and_the_screen(keep_screen_on):
    tooltip = countdown_tooltip(3600, keep_screen_on=keep_screen_on)
    assert format_remaining(3600) in tooltip
    assert "screen" in tooltip
    assert tooltip != countdown_tooltip(3600, keep_screen_on=not keep_screen_on)


@pytest.mark.parametrize(
    "seconds,text",
    [
        (43200, "12:00:00"),
        (3600, "1:00:00"),
        (3599, "59:59"),
        (599, "9:59"),
        (60, "1:00"),
        (9, "0:09"),
        (0, "0:00"),
        (-5, "0:00"),  # a deadline just missed never reads as negative
    ],
)
def test_format_remaining(seconds, text):
    assert format_remaining(seconds) == text
