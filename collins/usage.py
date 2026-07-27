# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Claude subscription usage: the data behind the sidebar usage panel.

Claude Code's ``/usage`` screen is fed by an OAuth endpoint rather than a CLI
subcommand, so this module reads the access token Claude Code already stores
in ``~/.claude/.credentials.json`` and issues the same GET request. The file
is only ever read — refreshing an expired token is Claude Code's job, and the
panel simply reports the login as expired until that happens.

Widget-free by design (no ``gi`` imports) so the parser and fetcher are unit
testable, mirroring ``titles.py``: the HTTP transport is injectable, the
credentials path is overridable via ``COLLINS_CLAUDE_CREDENTIALS``, and
``COLLINS_USAGE_FIXTURE`` short-circuits the network entirely with a canned
response for screenshots and e2e runs.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

_OAUTH_BETA = "oauth-2025-04-20"
_HTTP_TIMEOUT_S = 15

# The three /usage bars, in display order. Unknown kinds sort after these.
_KIND_ORDER = {"session": 0, "weekly_all": 1, "weekly_scoped": 2}


def credentials_path() -> Path:
    return Path(
        os.environ.get("COLLINS_CLAUDE_CREDENTIALS")
        or Path.home() / ".claude" / ".credentials.json"
    )


class UsageError(Exception):
    """A failed usage fetch. ``kind`` tells the panel which message to show:
    no-credentials, expired, auth, http, network or parse."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class UsageBar:
    kind: str
    percent: int  # clamped to 0-100 for rendering
    raw_percent: int  # as reported; may exceed 100
    severity: str
    resets_at: datetime | None
    model_name: str | None = None


@dataclass(frozen=True)
class UsageCredits:
    enabled: bool
    used: float
    limit: float | None
    currency: str
    spend_limit_reached: bool


@dataclass(frozen=True)
class UsageSnapshot:
    bars: list[UsageBar] = field(default_factory=list)
    credits: UsageCredits | None = None
    subscription: str = ""
    fetched_at: float = 0.0  # time.time() of the fetch


def read_credentials(path: Path | None = None) -> tuple[str, str]:
    """Return ``(access_token, subscription_type)`` from Claude Code's
    credentials file. Never refreshes anything."""
    path = path or credentials_path()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as err:
        raise UsageError("no-credentials", f"cannot read {path}: {err}") from err
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    token = (oauth or {}).get("accessToken")
    if not token:
        raise UsageError("no-credentials", f"no OAuth token in {path}")
    expires_ms = oauth.get("expiresAt")
    if isinstance(expires_ms, (int, float)) and expires_ms / 1000 <= time.time():
        raise UsageError("expired", "Claude Code OAuth token has expired")
    return token, str(oauth.get("subscriptionType") or "")


def _parse_resets_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_bar(entry: object) -> UsageBar | None:
    if not isinstance(entry, dict):
        return None
    try:
        raw_percent = round(float(entry.get("percent")))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    scope = entry.get("scope")
    model = scope.get("model") if isinstance(scope, dict) else None
    model_name = model.get("display_name") if isinstance(model, dict) else None
    return UsageBar(
        kind=str(entry.get("kind") or ""),
        percent=max(0, min(100, raw_percent)),
        raw_percent=raw_percent,
        severity=str(entry.get("severity") or "normal"),
        resets_at=_parse_resets_at(entry.get("resets_at")),
        model_name=str(model_name) if model_name else None,
    )


def _parse_credits(data: dict) -> UsageCredits | None:
    """Credits status from ``extra_usage`` when present, else the newer
    ``spend`` block. ``None`` when the account has neither."""
    extra = data.get("extra_usage")
    if isinstance(extra, dict) and extra.get("is_enabled"):
        exponent = extra.get("decimal_places")
        scale = 10 ** exponent if isinstance(exponent, int) else 1
        used = extra.get("used_credits")
        limit = extra.get("monthly_limit")
        return UsageCredits(
            enabled=True,
            used=(used / scale) if isinstance(used, (int, float)) else 0.0,
            limit=(limit / scale) if isinstance(limit, (int, float)) else None,
            currency=str(extra.get("currency") or "USD"),
            spend_limit_reached=bool(extra.get("spend_limit_reached")),
        )
    spend = data.get("spend")
    if isinstance(spend, dict) and spend.get("enabled"):
        used_obj = spend.get("used") if isinstance(spend.get("used"), dict) else {}
        exponent = used_obj.get("exponent")
        scale = 10 ** exponent if isinstance(exponent, int) else 100
        amount = used_obj.get("amount_minor")
        limit_obj = spend.get("limit") if isinstance(spend.get("limit"), dict) else None
        limit_minor = (limit_obj or {}).get("amount_minor")
        return UsageCredits(
            enabled=True,
            used=(amount / scale) if isinstance(amount, (int, float)) else 0.0,
            limit=(limit_minor / scale) if isinstance(limit_minor, (int, float)) else None,
            currency=str(used_obj.get("currency") or "USD"),
            spend_limit_reached=str(spend.get("severity") or "") == "exceeded",
        )
    return None


def parse_snapshot(data: dict, subscription: str = "") -> UsageSnapshot:
    """Build a snapshot from the endpoint's response dict, tolerating null,
    malformed and unknown fields (the schema is undocumented)."""
    bars = [bar for bar in map(_parse_bar, data.get("limits") or []) if bar]
    bars.sort(key=lambda b: _KIND_ORDER.get(b.kind, len(_KIND_ORDER)))
    return UsageSnapshot(
        bars=bars,
        credits=_parse_credits(data),
        subscription=subscription,
        fetched_at=time.time(),
    )


def _http_get(url: str, headers: dict[str, str]) -> str:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_S) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        kind = "auth" if err.code in (401, 403) else "http"
        raise UsageError(kind, f"usage endpoint returned HTTP {err.code}") from err
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        raise UsageError("network", f"usage endpoint unreachable: {err}") from err


def fetch_snapshot(
    transport: Callable[[str, dict[str, str]], str] = _http_get,
    path: Path | None = None,
) -> UsageSnapshot:
    """Fetch and parse the current usage. Raises UsageError on any failure.

    With ``COLLINS_USAGE_FIXTURE`` set, the response is read from that JSON
    file instead — no credentials or network involved.
    """
    fixture = os.environ.get("COLLINS_USAGE_FIXTURE")
    if fixture:
        try:
            data = json.loads(Path(fixture).read_text())
        except (OSError, ValueError) as err:
            raise UsageError("network", f"cannot read fixture {fixture}: {err}") from err
        return parse_snapshot(data, subscription="fixture")
    token, subscription = read_credentials(path)
    body = transport(
        USAGE_URL,
        {"Authorization": f"Bearer {token}", "anthropic-beta": _OAUTH_BETA},
    )
    try:
        data = json.loads(body)
    except ValueError as err:
        raise UsageError("parse", f"usage response is not JSON: {err}") from err
    if not isinstance(data, dict):
        raise UsageError("parse", "usage response is not a JSON object")
    return parse_snapshot(data, subscription)


def time_until(target: datetime | None, now: datetime | None = None) -> str:
    """Compact untranslated duration until ``target``: "45m", "3h", "2d 4h".
    Empty string when unknown or already past."""
    if target is None:
        return ""
    now = now or datetime.now(timezone.utc)
    seconds = (target - now).total_seconds()
    if seconds <= 0:
        return ""
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{max(minutes, 1)}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h" if hours else f"{days}d"
