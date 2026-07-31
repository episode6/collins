import pytest

from collins.caffeine import (
    DURATION_KEYS,
    INDEFINITE,
    duration_label,
    duration_seconds,
    format_remaining,
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
