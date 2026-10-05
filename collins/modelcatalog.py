# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""The model catalog and the CLI's defaults, as a client reads them.

`claudemodels` keeps the catalog (its cache, the fetch behind it, the
saved list) and reads the CLI's settings files; both are the service
machine's (split-service spec §3.15, "Token use", PR-1.11). This module
offers the pickers, Preferences and the new-chat screen the same names
they called on `claudemodels`, each a request to the service:
`cached_models`, `available_models` and `refresh_models` (`models.get`;
the last two fetch, so call them from a worker thread, as before),
`cache_fetched_at` and `cache_failed` (the same reply's other fields),
`model_efforts` (read off the cached catalog), `cli_default_model` and
`cli_default_effort` (`models.defaults`). Each read asks: the cache is
the service's and answering from it costs no network (a later phase may
keep the reply as a mirror pushed by events).

The rest of `claudemodels` is pure list work (`grouped_models`,
`short_name`, `catalog_id`, `default_model`, `FALLBACK_MODELS`...) and is
used from there directly. With no link (a unit test), every read answers
what `claudemodels` would with no catalog at all. GTK-free.
"""

from __future__ import annotations

from . import apilink, claudemodels
from .api.protocol import RequestRefused
from .claudemodels import ClaudeModel


def _ask(fetch: bool = False, refresh: bool = False) -> dict:
    try:
        reply = apilink.call({"t": "models.get", "fetch": fetch, "refresh": refresh})
    except RequestRefused:
        reply = {"cached": False, "models": [], "fetched_at": 0.0, "failed": True}
    return reply


def cached_models() -> list[ClaudeModel] | None:
    reply = _ask()
    if not reply.get("cached"):
        return None
    return claudemodels.sort_models(claudemodels.models_from_records(reply.get("models")))


def available_models() -> list[ClaudeModel]:
    return claudemodels.sort_models(claudemodels.models_from_records(_ask(fetch=True).get("models")))


def refresh_models() -> list[ClaudeModel]:
    return claudemodels.sort_models(claudemodels.models_from_records(_ask(refresh=True).get("models")))


def cache_fetched_at() -> float:
    reply = _ask()
    value = reply.get("fetched_at")
    return float(value) if isinstance(value, (int, float)) and reply.get("cached") else 0.0


def cache_failed() -> bool:
    return bool(_ask().get("failed"))


def model_efforts(model_id: str) -> tuple[str, ...] | None:
    model_id = (model_id or "").strip().removesuffix("[1m]")
    for model in cached_models() or ():
        if model.id == model_id:
            return model.efforts
    return None


def _defaults(cwd: str | None, model: str | None) -> dict:
    message: dict = {"t": "models.defaults"}
    if cwd and cwd.startswith("/"):
        message["cwd"] = cwd
    if model:
        message["model"] = model
    try:
        return apilink.call(message)
    except RequestRefused:
        return {}


def cli_default_model(cwd: str | None = None) -> str | None:
    return _defaults(cwd, None).get("model") or None


def cli_default_effort(cwd: str | None = None, model: str | None = None) -> str | None:
    return _defaults(cwd, model).get("effort") or None
