# New in the ghackett fork of agent-session-manager (GPL-3.0).

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from collins.usage import (
    USAGE_URL,
    UsageError,
    fetch_snapshot,
    parse_snapshot,
    read_credentials,
    time_until,
)

# Trimmed from a live response of the OAuth usage endpoint (2026-07-26).
SAMPLE_RESPONSE = {
    "five_hour": {"utilization": 48.0, "resets_at": "2026-07-26T23:50:00+00:00"},
    "seven_day": {"utilization": 17.0, "resets_at": "2026-08-02T09:00:00+00:00"},
    "seven_day_opus": None,
    "some_future_field": {"mystery": True},
    "extra_usage": {
        "is_enabled": False,
        "monthly_limit": None,
        "used_credits": None,
        "currency": None,
        "user_disabled": True,
        "spend_limit_reached": False,
        "credits_ever_enabled": True,
    },
    "limits": [
        {
            "kind": "weekly_scoped",
            "group": "weekly",
            "percent": 32,
            "severity": "normal",
            "resets_at": "2026-08-02T09:00:00+00:00",
            "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None},
            "is_active": False,
        },
        {
            "kind": "session",
            "group": "session",
            "percent": 48,
            "severity": "warning",
            "resets_at": "2026-07-26T23:50:00+00:00",
            "scope": None,
            "is_active": True,
        },
        {
            "kind": "weekly_all",
            "group": "weekly",
            "percent": 17,
            "severity": "normal",
            "resets_at": "2026-08-02T09:00:00+00:00",
            "scope": None,
            "is_active": False,
        },
    ],
    "spend": {
        "used": {"amount_minor": 0, "currency": "USD", "exponent": 2},
        "limit": None,
        "percent": 0,
        "severity": "normal",
        "enabled": False,
    },
}


# -- parse_snapshot ------------------------------------------------------------


def test_parse_three_bars_in_display_order():
    snap = parse_snapshot(SAMPLE_RESPONSE, subscription="max")
    assert [b.kind for b in snap.bars] == ["session", "weekly_all", "weekly_scoped"]
    assert [b.percent for b in snap.bars] == [48, 17, 32]
    assert snap.bars[0].severity == "warning"
    assert snap.bars[1].severity == "normal"
    assert snap.subscription == "max"


def test_parse_scoped_bar_carries_model_name():
    snap = parse_snapshot(SAMPLE_RESPONSE)
    scoped = snap.bars[2]
    assert scoped.model_name == "Fable"
    assert snap.bars[0].model_name is None  # scope: null


def test_parse_resets_at_is_aware_datetime():
    snap = parse_snapshot(SAMPLE_RESPONSE)
    resets = snap.bars[0].resets_at
    assert resets == datetime(2026, 7, 26, 23, 50, tzinfo=timezone.utc)


def test_parse_tolerates_malformed_limit_entries():
    data = {
        "limits": [
            None,
            {},
            "junk",
            {"kind": "session", "percent": "not a number"},
            {"kind": "future_kind", "percent": 150, "resets_at": "garbage"},
        ]
    }
    snap = parse_snapshot(data)
    assert len(snap.bars) == 1
    bar = snap.bars[0]
    assert bar.kind == "future_kind"
    assert bar.percent == 100  # clamped
    assert bar.raw_percent == 150  # preserved
    assert bar.resets_at is None


def test_parse_missing_limits_and_credits():
    snap = parse_snapshot({})
    assert snap.bars == []
    assert snap.credits is None


def test_parse_credits_from_extra_usage():
    data = {
        "extra_usage": {
            "is_enabled": True,
            "used_credits": 1250,
            "monthly_limit": 5000,
            "decimal_places": 2,
            "currency": "USD",
            "spend_limit_reached": False,
        }
    }
    credits = parse_snapshot(data).credits
    assert credits is not None
    assert credits.enabled
    assert credits.used == 12.50
    assert credits.limit == 50.00
    assert not credits.spend_limit_reached


def test_parse_credits_falls_back_to_spend():
    data = {
        "extra_usage": {"is_enabled": False},
        "spend": {
            "enabled": True,
            "used": {"amount_minor": 340, "currency": "USD", "exponent": 2},
            "limit": {"amount_minor": 2000, "currency": "USD", "exponent": 2},
            "severity": "normal",
        },
    }
    credits = parse_snapshot(data).credits
    assert credits is not None
    assert credits.used == 3.40
    assert credits.limit == 20.00
    assert credits.currency == "USD"


def test_parse_disabled_credits_are_none():
    assert parse_snapshot(SAMPLE_RESPONSE).credits is None


# -- read_credentials ----------------------------------------------------------


def _write_credentials(tmp_path, expires_in_s: float = 3600):
    path = tmp_path / "credentials.json"
    path.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "tok-123",
                    "expiresAt": (time.time() + expires_in_s) * 1000,
                    "subscriptionType": "max",
                }
            }
        )
    )
    return path


def test_read_credentials_ok(tmp_path):
    token, subscription = read_credentials(_write_credentials(tmp_path))
    assert token == "tok-123"
    assert subscription == "max"


def test_read_credentials_missing_file(tmp_path):
    with pytest.raises(UsageError) as err:
        read_credentials(tmp_path / "nope.json")
    assert err.value.kind == "no-credentials"


def test_read_credentials_expired(tmp_path):
    with pytest.raises(UsageError) as err:
        read_credentials(_write_credentials(tmp_path, expires_in_s=-60))
    assert err.value.kind == "expired"


def test_read_credentials_no_token(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({"claudeAiOauth": {}}))
    with pytest.raises(UsageError) as err:
        read_credentials(path)
    assert err.value.kind == "no-credentials"


# -- fetch_snapshot ------------------------------------------------------------


def test_fetch_sends_bearer_and_beta_headers(tmp_path):
    calls = []

    def transport(url, headers):
        calls.append((url, headers))
        return json.dumps(SAMPLE_RESPONSE)

    snap = fetch_snapshot(transport, path=_write_credentials(tmp_path))
    assert len(snap.bars) == 3
    assert snap.subscription == "max"
    url, headers = calls[0]
    assert url == USAGE_URL
    assert headers["Authorization"] == "Bearer tok-123"
    assert headers["anthropic-beta"] == "oauth-2025-04-20"


def test_fetch_propagates_transport_errors(tmp_path):
    def transport(url, headers):
        raise UsageError("auth", "401")

    with pytest.raises(UsageError) as err:
        fetch_snapshot(transport, path=_write_credentials(tmp_path))
    assert err.value.kind == "auth"


def test_fetch_rejects_non_json(tmp_path):
    with pytest.raises(UsageError) as err:
        fetch_snapshot(lambda u, h: "not json", path=_write_credentials(tmp_path))
    assert err.value.kind == "parse"


def test_fetch_uses_fixture_env(tmp_path, monkeypatch):
    fixture = tmp_path / "usage.json"
    fixture.write_text(json.dumps(SAMPLE_RESPONSE))
    monkeypatch.setenv("COLLINS_USAGE_FIXTURE", str(fixture))

    def transport(url, headers):
        raise AssertionError("transport must not be called with a fixture set")

    snap = fetch_snapshot(transport, path=tmp_path / "missing-credentials.json")
    assert len(snap.bars) == 3


# -- time_until ----------------------------------------------------------------


def test_time_until_formats():
    now = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    assert time_until(now + timedelta(minutes=45), now) == "45m"
    assert time_until(now + timedelta(seconds=30), now) == "1m"
    assert time_until(now + timedelta(hours=3), now) == "3h"
    assert time_until(now + timedelta(hours=3, minutes=20), now) == "3h 20m"
    assert time_until(now + timedelta(days=2, hours=4), now) == "2d 4h"
    assert time_until(now + timedelta(days=2), now) == "2d"
    assert time_until(now - timedelta(minutes=1), now) == ""
    assert time_until(None, now) == ""
