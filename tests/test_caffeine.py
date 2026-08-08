import pytest

from collins.caffeine import (
    ACTIVE_GRACE_S,
    DURATION_KEYS,
    INDEFINITE,
    WHILE_ACTIVE,
    countdown_tooltip,
    duration_label,
    duration_seconds,
    follows_activity,
    format_remaining,
    toggle_tooltip,
)


def test_durations_offered():
    assert DURATION_KEYS == ("1h", "2h", "3h", "6h", "12h", WHILE_ACTIVE, INDEFINITE)


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


def test_grace_is_five_minutes():
    assert ACTIVE_GRACE_S == 300
    assert format_remaining(ACTIVE_GRACE_S) == "5:00"


def test_labels():
    assert duration_label("1h") == "1 hour"  # singular
    assert duration_label("12h") == "12 hours"
    assert duration_label(INDEFINITE) == "Indefinitely"
    assert duration_label("nonsense") == "Indefinitely"
    assert duration_label(WHILE_ACTIVE) == "While active"


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
