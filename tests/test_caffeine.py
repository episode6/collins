import pytest

from collins.caffeine import (
    DURATION_KEYS,
    INDEFINITE,
    countdown_tooltip,
    duration_label,
    duration_seconds,
    format_remaining,
    toggle_tooltip,
)


def test_durations_offered():
    assert DURATION_KEYS == ("1h", "2h", "3h", "6h", "12h", INDEFINITE)


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


def test_labels():
    assert duration_label("1h") == "1 hour"  # singular
    assert duration_label("12h") == "12 hours"
    assert duration_label(INDEFINITE) == "Indefinitely"
    assert duration_label("nonsense") == "Indefinitely"


@pytest.mark.parametrize("on", [False, True])
def test_tooltip_only_promises_the_screen_when_it_is_kept_on(on):
    kept = toggle_tooltip(on=on, keep_screen_on=True)
    free = toggle_tooltip(on=on, keep_screen_on=False)
    assert "screen" in kept and "screen" in free  # both say what happens to it
    assert kept != free
    # Whatever the setting, the computer staying awake is never in doubt.
    assert "computer" in kept and "computer" in free


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
