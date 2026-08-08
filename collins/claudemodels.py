# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Claude models available to the CLI's login.

The model pickers in Preferences (session titles, icon generation) offer
live choices, queried from the Models API with the OAuth token the claude
CLI keeps in ``~/.claude/.credentials.json`` — the same login the whole app
is built on, so no separate API key is needed. The query is read-only and
cached for an hour; when it can't be made (logged out, offline), the CLI's
own version-agnostic aliases keep the pickers and the settings usable.

Both settings default to "" — automatic — which resolve_model() turns into
the newest model of that setting's preferred tier (Haiku for titles, Sonnet
for icons), or, should the tier ever be dropped, the newest model of the
weakest tier still offered.

Kept GTK-free (like projecticons/titles) so the ranking and resolution
logic is unit-testable headless.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
_MODELS_URL = "https://api.anthropic.com/v1/models"
_TIMEOUT_S = 10
_CACHE_TTL_S = 3600
_MAX_PAGES = 5  # the catalog is ~a dozen models; more pages means something is wrong

# Weakest tier first. An id that names none of these is treated as beyond
# the strongest tier, so an unrecognized future model is never picked as
# "the weakest model" by accident.
_TIERS = ("haiku", "sonnet", "opus", "fable", "mythos")


@dataclass(frozen=True)
class ClaudeModel:
    id: str
    display_name: str
    created_at: str = ""  # ISO 8601; "" sorts oldest


# What the pickers fall back to when the API can't be asked: the CLI's
# stable aliases, each resolving to the latest model of its tier.
FALLBACK_MODELS = (
    ClaudeModel("haiku", "Haiku (latest)"),
    ClaudeModel("sonnet", "Sonnet (latest)"),
    ClaudeModel("opus", "Opus (latest)"),
)


def _oauth_token() -> str | None:
    try:
        data = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    token = (data.get("claudeAiOauth") or {}).get("accessToken")
    return token if isinstance(token, str) and token else None


def parse_models(payload) -> list[ClaudeModel]:
    """The models in one API response page, junk entries skipped."""
    models = []
    for entry in (payload.get("data") or []) if isinstance(payload, dict) else []:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue
        name = entry.get("display_name")
        created = entry.get("created_at")
        models.append(
            ClaudeModel(
                model_id,
                name if isinstance(name, str) and name else model_id,
                created if isinstance(created, str) else "",
            )
        )
    return models


def fetch_models(timeout: float = _TIMEOUT_S) -> list[ClaudeModel]:
    """One live Models API query; [] when the token, network, or API says no."""
    token = _oauth_token()
    if token is None:
        return []
    models: list[ClaudeModel] = []
    after = None
    for _ in range(_MAX_PAGES):
        url = _MODELS_URL + (f"?after_id={after}" if after else "")
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "oauth-2025-04-20",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
        except Exception:  # URLError, HTTPError, timeout, bad JSON, ...
            return []
        models.extend(parse_models(payload))
        if not (isinstance(payload, dict) and payload.get("has_more")):
            break
        after = payload.get("last_id")
        if not after:
            break
    return models


_lock = threading.Lock()
_cached: list[ClaudeModel] | None = None
_cached_at = 0.0


def available_models() -> list[ClaudeModel]:
    """The model list, from cache or a fresh query when stale.

    Blocking (up to the network timeout) — call from a worker thread. A
    failed refresh falls back to whatever was cached before, and [] means
    the API has never answered this run.
    """
    global _cached, _cached_at
    with _lock:
        if _cached is not None and time.monotonic() - _cached_at < _CACHE_TTL_S:
            return list(_cached)
    models = fetch_models()
    with _lock:
        if models:
            _cached = models
            _cached_at = time.monotonic()
        return list(models or _cached or [])


def _tier(model_id: str) -> int:
    for rank, name in enumerate(_TIERS):
        if name in model_id:
            return rank
    return len(_TIERS)


def default_model(models: list[ClaudeModel], prefer: str = "sonnet") -> str:
    """The automatic choice: the newest model of the *prefer* tier — or,
    should that tier ever be dropped, the newest model of the weakest tier
    left. With no list to choose from, the CLI's own alias for the tier."""
    preferred = [m for m in models if _tier(m.id) == _TIERS.index(prefer)]
    if preferred:
        return max(preferred, key=lambda m: m.created_at).id
    if models:
        weakest = min(_tier(m.id) for m in models)
        pool = [m for m in models if _tier(m.id) == weakest]
        return max(pool, key=lambda m: m.created_at).id
    return prefer


def resolve_model(setting: str | None, models: list[ClaudeModel], prefer: str = "sonnet") -> str:
    """What a headless run should pass to --model: the user's explicit
    choice when the setting holds one, else the automatic default."""
    setting = (setting or "").strip()
    return setting or default_model(models, prefer)


def pick_model(setting: str | None, prefer: str = "sonnet") -> str:
    """resolve_model over the live list. Blocking on first use (the list
    may need querying) — call from the worker thread that runs the CLI."""
    setting = (setting or "").strip()
    if setting:
        return setting  # an explicit choice needs no query at all
    return default_model(available_models(), prefer)
