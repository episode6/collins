# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The automatic delete of archived sessions (collins.autodelete): the
span, the expiry test, the once-a-day cache, the sweep, and the archive
stamps AppState keeps for it."""

import json

import pytest

from collins import autodelete
from collins.autodelete import (
    INTERVAL_S,
    SETTING_COUNT,
    SETTING_UNIT,
    UNITS,
    describe,
    due,
    expired,
    expired_for_settings,
    maybe_sweep,
    read_record,
    retention_seconds,
    stamp_missing,
    write_record,
)
from collins.state import DEFAULT_SETTINGS

NOW = 1_800_000_000.0
DAY = 24 * 3600


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


# -- the span ------------------------------------------------------------------


def test_default_is_never():
    assert DEFAULT_SETTINGS[SETTING_COUNT] == 0
    assert DEFAULT_SETTINGS[SETTING_UNIT] in {value for value, _l, _s in UNITS}
    assert retention_seconds(DEFAULT_SETTINGS[SETTING_COUNT], DEFAULT_SETTINGS[SETTING_UNIT]) is None
    assert describe(0, "months") is None


@pytest.mark.parametrize(
    ("count", "unit", "seconds"),
    [
        (1, "days", DAY),
        (2, "weeks", 14 * DAY),
        (3, "months", 90 * DAY),
        (1, "years", 365 * DAY),
        (7.0, "days", 7 * DAY),  # a float that is a whole number is fine
    ],
)
def test_retention_by_unit(count, unit, seconds):
    assert retention_seconds(count, unit) == seconds
    assert describe(count, unit) == f"{int(count)} {unit}"


@pytest.mark.parametrize(
    ("count", "unit"),
    [
        (0, "days"),
        (-1, "days"),
        (1.5, "days"),
        (True, "days"),
        ("3", "days"),
        (None, "days"),
        (3, "fortnights"),
        (3, None),
        (1000, "days"),
    ],
)
def test_retention_never_for_anything_odd(count, unit):
    # A hand-edited state.json, a bool, a string, a unit that isn't ours:
    # every one of them is "never" — a span nobody asked for never deletes.
    assert retention_seconds(count, unit) is None


# -- expiry --------------------------------------------------------------------


def test_expired_picks_stamps_at_least_the_span_old():
    stamps = {
        "old": NOW - 31 * DAY,
        "exactly": NOW - 30 * DAY,
        "fresh": NOW - 29 * DAY,
        "future": NOW + DAY,  # a clock that went back: not old
        "junk": "yesterday",
        "bool": True,
    }
    assert expired(stamps, 30 * DAY, NOW) == ["old", "exactly"]


def test_expired_is_nothing_for_never():
    assert expired({"old": NOW - 400 * DAY}, None, NOW) == []


def test_expired_for_settings_reads_the_pair():
    stamps = {"old": NOW - 8 * DAY, "fresh": NOW - 6 * DAY}
    settings = {SETTING_COUNT: 1, SETTING_UNIT: "weeks"}
    assert expired_for_settings(settings, stamps, NOW) == ["old"]
    assert expired_for_settings({SETTING_COUNT: 0, SETTING_UNIT: "weeks"}, stamps, NOW) == []
    assert expired_for_settings({}, stamps, NOW) == []


# -- the cache -----------------------------------------------------------------


def test_record_roundtrip_and_shape_guard(tmp_path):
    assert read_record() == {}
    write_record({"swept_at": NOW})
    assert read_record() == {"swept_at": NOW}
    path = autodelete.cache_path()
    assert path == tmp_path / "cache" / "collins" / "archive-sweep.json"
    path.write_text(json.dumps({"version": 99, "swept_at": NOW}))
    assert read_record() == {}
    path.write_text(json.dumps({"version": 1, "swept_at": "noon"}))
    assert read_record() == {}
    path.write_text("not json")
    assert read_record() == {}


def test_due_once_a_day():
    assert due({}, NOW)
    assert not due({"swept_at": NOW - INTERVAL_S + 1}, NOW)
    assert due({"swept_at": NOW - INTERVAL_S}, NOW)
    # The clock went back past the file: due, not a day away.
    assert due({"swept_at": NOW + 3600}, NOW)


# -- the sweep -----------------------------------------------------------------


def _settings(count=1, unit="months"):
    return {SETTING_COUNT: count, SETTING_UNIT: unit}


def test_sweep_does_nothing_while_off():
    calls = []
    assert maybe_sweep(_settings(0), {"old": NOW - 400 * DAY}, calls.append, NOW) is None
    assert calls == []
    assert read_record() == {}  # no stamp either: nothing ran


def test_sweep_trashes_the_expired_and_stamps_the_day():
    stamps = {"old": NOW - 31 * DAY, "fresh": NOW - DAY}
    calls = []

    def trash(ids):
        calls.append(list(ids))
        return []

    assert maybe_sweep(_settings(), stamps, trash, NOW) == ["old"]
    assert calls == [["old"]]
    assert read_record() == {"swept_at": NOW}
    # Within the day, nothing — not even for a fresh expiry.
    stamps["fresh"] = NOW - 40 * DAY
    assert maybe_sweep(_settings(), stamps, trash, NOW + 3600) is None
    assert calls == [["old"]]
    # A day on, the next one goes.
    assert maybe_sweep(_settings(), stamps, trash, NOW + INTERVAL_S) == ["old", "fresh"]


def test_sweep_with_nothing_expired_still_counts_as_done():
    calls = []
    assert maybe_sweep(_settings(), {"fresh": NOW - DAY}, calls.append, NOW) == []
    assert calls == []
    assert read_record() == {"swept_at": NOW}


def test_sweep_reports_what_the_window_refused():
    def trash(ids):
        return ["running"]  # skipped: still running

    stamps = {"running": NOW - 60 * DAY, "old": NOW - 60 * DAY}
    assert maybe_sweep(_settings(), stamps, trash, NOW) == ["old"]
    # The day is stamped all the same; the skipped one comes round tomorrow.
    assert read_record() == {"swept_at": NOW}


# -- the stamps ----------------------------------------------------------------


def test_stamp_missing_starts_the_clock_now_and_drops_strays():
    out = stamp_missing({"a", "b"}, {"a": NOW - DAY, "gone": NOW - 5 * DAY, "c": "x"}, NOW)
    assert out == {"a": NOW - DAY, "b": NOW}


def test_state_stamps_archives_and_drops_them_on_restore(app_state, monkeypatch):
    import collins.state as state_mod

    monkeypatch.setattr(state_mod.time, "time", lambda: NOW)
    state = app_state.AppState()
    state.set_archived("sid-1", True)
    assert state.archived_since("sid-1") == NOW
    # A second archive keeps the first stamp: the first is the one that counts.
    monkeypatch.setattr(state_mod.time, "time", lambda: NOW + DAY)
    state.set_archived("sid-1", True)
    assert state.archived_since("sid-1") == NOW

    fresh = app_state.AppState()
    assert fresh.archived_since("sid-1") == NOW
    fresh.set_archived("sid-1", False)
    assert fresh.archived_since("sid-1") is None
    assert "sid-1" not in json.loads(app_state._STATE_FILE.read_text())["archived_at"]


def test_state_stamps_pre_existing_archives_at_first_read(app_state, monkeypatch):
    # A state.json from before the stamp existed: the clock starts at this
    # read, never earlier.
    import collins.state as state_mod

    monkeypatch.setattr(state_mod.time, "time", lambda: NOW)
    app_state._CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    app_state._STATE_FILE.write_text(json.dumps({"archived": ["sid-1"]}), encoding="utf-8")
    state = app_state.AppState()
    assert state.archived_since("sid-1") == NOW
    assert state.archived_since("sid-2") is None
