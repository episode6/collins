# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Claude models available to the CLI's login.

The model pickers in Preferences (session titles, icon generation) offer
live choices, queried from the Models API with the OAuth token the claude
CLI keeps in ``~/.claude/.credentials.json`` — the same login the whole app
is built on, so no separate API key is needed. When the query can't be made
(logged out, offline), the CLI's own version-agnostic aliases keep the
pickers and the settings usable.

The catalog barely moves — a handful of models, changing a few times a
year — so the query is read-only and cached hard: for a day, and to disk
(``$XDG_CACHE_HOME/collins/models.json``) so a restart doesn't pay for it
again — unless what came back was a single model, which is not a catalog
anyone can pick from: that serves, but with no lifetime at all, so the next
picker open and the next launch both ask again. A failed query never evicts;
the last good list keeps serving however stale it is, and a run of failures
backs off rather than making every picker open wait out the network timeout.
Preferences' Refresh button (`refresh_models`) ignores all of that and asks
the API outright, for when a model ships and the cached day hasn't turned
over yet.

Every query says how it went through the module logger: what came back at
INFO (`COLLINS_LOG=INFO`), and anything that failed at WARNING, which the
default level already shows.

Both settings default to "" — automatic — which resolve_model() turns into
the newest model of that setting's preferred tier (Haiku for titles, Sonnet
for icons), or, should the tier ever be dropped, the newest model of the
weakest tier still offered.

Reading an id back out is here too: short_name() turns one into the name a
person would say, for the footer's "which model is this session on?".

The CLI's own default — what a launch with no ``--model`` runs on — is read
the way the CLI reads it (cli_default_model): the ``ANTHROPIC_MODEL``
environment variable, else the ``"model"`` key of its settings files, most
specific first: managed policy, then the project's ``.claude/settings.local.
json`` and ``.claude/settings.json``, then ``~/.claude/settings.json`` (the
file ``/model`` writes). None of them set: the CLI falls back to a built-in
per-plan default it writes nowhere, and the answer is None — "Default", with
no name to put to it until a session answers.

Kept GTK-free (like projecticons/titles) so the ranking, resolution and
naming logic is unit-testable headless.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
_MODELS_URL = "https://api.anthropic.com/v1/models"
_TIMEOUT_S = 10
_CACHE_TTL_S = 86_400  # a day: the catalog changes a few times a year
# A one-model answer is not this account's catalog — the API replied, but with
# something no picker can offer a choice from (a truncated page, a login that
# briefly sees one model). It is still better than the aliases, so it is kept
# and saved; it just never counts as fresh, so the next ask — the next picker
# open, or the next launch reading the saved file — queries again.
_MIN_TRUSTED_MODELS = 2
# After a failed query, serve the cache for this long before asking the
# network again — offline, the alternative is every picker open blocking for
# the full timeout. A forced refresh ignores it.
_RETRY_AFTER_FAILURE_S = 300
_MAX_PAGES = 5  # the catalog is ~a dozen models; more pages means something is wrong
_CACHE_VERSION = 1  # bumped if the file's shape changes; a file from another version is ignored

# Weakest tier first. An id that names none of these is treated as beyond
# the strongest tier, so an unrecognized future model is never picked as
# "the weakest model" by accident.
_TIERS = ("haiku", "sonnet", "opus", "fable", "mythos")

# The order the pickers list the tier families in: newest generation first,
# down to the smallest. A model whose id names none of these is a family we
# don't know at all (so, newer still) — it sorts above them all, clustered
# with its own kind, via `_model_group`'s unrecognized branch. Distinct from
# `_TIERS`, which ranks by strength to pick a default; this is only
# presentation. Order here, not strength: `mythos` leads because it's the
# newest-generation family, above `fable`, even though a genuinely unknown
# family still outranks it as newer-than-anything-we-name.
_DISPLAY_ORDER = ("mythos", "fable", "opus", "sonnet", "haiku")


@dataclass(frozen=True)
class ClaudeModel:
    id: str
    display_name: str
    created_at: str = ""  # ISO 8601; "" sorts oldest


# What the pickers fall back to when the API can't be asked: the CLI's
# stable aliases, each resolving to the latest model of its tier. In display
# order (see `_DISPLAY_ORDER`) so the fallback reads like the live list.
FALLBACK_MODELS = (
    ClaudeModel("opus", "Opus (latest)"),
    ClaudeModel("sonnet", "Sonnet (latest)"),
    ClaudeModel("haiku", "Haiku (latest)"),
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
    """One live Models API query; [] when the token, network, or API says no.

    Every way of coming back empty says so in the log first — a missing token
    and a refused request are the same [] to the caller but very different
    things to fix, and the pickers quietly falling back to the aliases is
    exactly the symptom that needs a reason attached to it.
    """
    token = _oauth_token()
    if token is None:
        log.warning(
            "models: no OAuth token in %s — is the claude CLI logged in?", CREDENTIALS_PATH
        )
        return []
    models: list[ClaudeModel] = []
    after = None
    for page in range(_MAX_PAGES):
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
        except Exception as err:  # URLError, HTTPError, timeout, bad JSON, ...
            log.warning("models: GET %s failed on page %d: %r", url, page + 1, err)
            return []
        models.extend(parse_models(payload))
        if not (isinstance(payload, dict) and payload.get("has_more")):
            break
        after = payload.get("last_id")
        if not after:
            break
    return models


# -- the cache ----------------------------------------------------------------

_lock = threading.Lock()
_fetch_lock = threading.Lock()  # single-flight: one live query at a time
_disk_lock = threading.Lock()  # the once-per-run read of the saved list
_cached: list[ClaudeModel] | None = None
_cached_at = 0.0  # wall clock, not monotonic: it has to mean something to the next run
_failed_at = 0.0  # when the last query failed, for the retry backoff
_disk_read = False


def cache_path() -> Path:
    """Where the saved list lives.

    Under the app's cache directory (honoring ``XDG_CACHE_HOME`` the way
    `dropimages.cache_directory` does, so tests and the screenshot harness
    relocate it along with the rest of the app's state) because that is
    exactly what it is: losing the file costs one query, never a setting.
    """
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "collins" / "models.json"


def _read_disk() -> tuple[list[ClaudeModel], float] | None:
    """The list the last run saved, with the wall-clock time it was fetched.

    None for every way of not having one — no file yet, a file written by
    another version of this format, unreadable, or holding nothing usable.
    All of those are ordinary (a first run is the common case), so none of
    them are louder than debug.
    """
    path = cache_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.debug("models: no saved list at %s yet", path)
        return None
    except (OSError, ValueError) as err:
        log.debug("models: cannot read %s: %r", path, err)
        return None
    if not isinstance(payload, dict) or payload.get("version") != _CACHE_VERSION:
        log.debug("models: ignoring %s — not a v%d cache", path, _CACHE_VERSION)
        return None
    models = parse_models(payload)
    fetched_at = payload.get("fetched_at")
    if not models or not isinstance(fetched_at, (int, float)) or isinstance(fetched_at, bool):
        log.debug("models: ignoring %s — no usable models in it", path)
        return None
    return models, float(fetched_at)


def _write_disk(models: list[ClaudeModel], fetched_at: float) -> None:
    """Save *models* for the next run. Best effort: a cache that can't be
    written costs a query next launch, not anything anyone need act on."""
    path = cache_path()
    payload = {
        "version": _CACHE_VERSION,
        "fetched_at": fetched_at,
        # The API's own field names, so _read_disk hands the file straight to
        # parse_models and there is only one shape to keep in step.
        "data": [
            {"id": m.id, "display_name": m.display_name, "created_at": m.created_at}
            for m in models
        ],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)  # atomic: a half-written file would poison every later run
    except OSError as err:
        log.warning("models: cannot save the list to %s: %r", path, err)
        return
    log.debug("models: saved %d model(s) to %s", len(models), path)


def _age(seconds: float) -> str:
    """A cache age for a log line: "40s", "3m", "2h", "6d"."""
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86_400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86_400)}d"


def _ttl(models: list[ClaudeModel]) -> float:
    """How long *models* may serve before being asked about again.

    The full day for a real catalog; nothing at all for a list too short to
    be one (see `_MIN_TRUSTED_MODELS`), which still serves but is re-queried
    at the first opportunity.
    """
    return _CACHE_TTL_S if len(models) >= _MIN_TRUSTED_MODELS else 0.0


def _fresh(fetched_at: float, now: float, models: list[ClaudeModel]) -> bool:
    """Is *models*, fetched at *fetched_at*, still inside its TTL?

    A negative age — a saved list claiming to come from the future, which a
    clock change or a copied home directory can produce — counts as stale.
    Re-querying costs one request and a failure keeps the list anyway, so
    erring toward the network is the cheap mistake here.
    """
    return 0 <= now - fetched_at < _ttl(models)


def _load_disk_once() -> None:
    """Seed the in-memory cache from the file, once per process.

    The first picker of the run gets the last run's list immediately rather
    than the aliases plus a wait. Only ever fills an empty cache: a list this
    run fetched itself is newer than anything on disk by definition.
    """
    global _cached, _cached_at, _disk_read
    with _disk_lock:
        if _disk_read:
            return
        entry = _read_disk()
        _disk_read = True
        if entry is None:
            return
        models, fetched_at = entry
        with _lock:
            if _cached is not None:
                return
            _cached, _cached_at = models, fetched_at
    log.info(
        "models: %d loaded from %s, fetched %s ago",
        len(models),
        cache_path(),
        _age(max(0.0, time.time() - fetched_at)),
    )


def _serve_cache() -> list[ClaudeModel] | None:
    """The cache, when it is still good enough to answer with as it stands:
    inside its TTL (which a too-short list has none of), or stale but with a
    query having just failed."""
    now = time.time()
    with _lock:
        if _cached is None:
            return None
        if _fresh(_cached_at, now, _cached):
            log.debug(
                "models: %d served from cache, fetched %s ago",
                len(_cached),
                _age(max(0.0, now - _cached_at)),
            )
            return list(_cached)
        if 0 <= now - _failed_at < _RETRY_AFTER_FAILURE_S:
            # Stale, but a query failed moments ago: don't make this caller
            # wait out the same timeout to be told the same thing.
            log.debug(
                "models: cache is stale but a query failed %s ago; serving the %d cached model(s)",
                _age(max(0.0, now - _failed_at)),
                len(_cached),
            )
            return list(_cached)
    return None


def _query(force: bool) -> tuple[list[ClaudeModel], bool]:
    """The list, and whether the API answered on this call.

    Where the TTL, the saved list, the failure backoff and the single-flight
    lock all meet; `available_models` and `refresh_models` are the two ways in.
    """
    global _cached, _cached_at, _failed_at
    _load_disk_once()
    if not force:
        served = _serve_cache()
        if served is not None:
            return served, False
    with _fetch_lock:
        if not force:
            served = _serve_cache()  # filled while queued here
            if served is not None:
                return served, False
        log.info("models: querying %s%s", _MODELS_URL, " (forced refresh)" if force else "")
        started = time.monotonic()
        models = fetch_models()
        elapsed = time.monotonic() - started
        now = time.time()
        cached_age = ""
        with _lock:
            if models:
                _cached, _cached_at, _failed_at = models, now, 0.0
                result, ok = list(models), True
            else:
                _failed_at = now
                result, ok = (list(_cached) if _cached is not None else []), False
                if result:
                    cached_age = _age(max(0.0, now - _cached_at))
    if ok:
        log.info(
            "models: %d received in %.2fs — %s",
            len(result),
            elapsed,
            ", ".join(m.id for m in result),
        )
        _write_disk(result, now)
        if len(result) < _MIN_TRUSTED_MODELS:
            log.warning(
                "models: only %d model(s) came back — serving it, but it expires "
                "immediately; the next ask queries again",
                len(result),
            )
    elif result:
        log.warning(
            "models: query failed after %.2fs; keeping the %d cached model(s) fetched %s ago",
            elapsed,
            len(result),
            cached_age,
        )
    else:
        log.warning(
            "models: query failed after %.2fs and nothing is cached — "
            "the pickers fall back to the CLI's aliases",
            elapsed,
        )
    return result, ok


def available_models() -> list[ClaudeModel]:
    """The model list, from cache or a fresh query once the cache is stale.

    Blocking (up to the network timeout) — call from a worker thread. A failed
    query falls back to whatever was cached before, from this run or the last
    one, and [] means no query has ever succeeded.

    Concurrent callers on a stale cache queue on one fetch rather than fanning
    out into duplicate queries (a menu reopened before its first fetch lands,
    say): whoever holds the fetch lock asks the API, and the queued callers
    re-read the cache it just filled.
    """
    return sort_models(_query(force=False)[0])


def refresh_models() -> list[ClaudeModel]:
    """Query the API now, TTL and backoff ignored — Preferences' Refresh.

    Same return as `available_models`: the list, cached one and all if the
    query failed. Whether it failed is `cache_failed`, so that one question
    has one answer however the caller got here. Blocking — call from a worker
    thread.
    """
    return sort_models(_query(force=True)[0])


def cached_models() -> list[ClaudeModel] | None:
    """Whatever the cache holds, however stale — never the network, so safe on
    the main loop. None means no query has ever succeeded, in this run or an
    earlier one; the caller shows its own stand-in and asks available_models()
    off-thread.

    Does read the saved list on its first call of the run (one small local
    file), so a picker opening before any worker thread lands still starts on
    real models rather than the aliases.
    """
    _load_disk_once()
    with _lock:
        return sort_models(_cached) if _cached is not None else None


def cache_fetched_at() -> float:
    """When the cached list was fetched (unix time), or 0.0 with no cache at
    all. What Preferences dates its "12 models, updated 3h ago" line from."""
    _load_disk_once()
    with _lock:
        return _cached_at if _cached is not None else 0.0


def cache_failed() -> bool:
    """Whether the last query attempt failed, with none succeeding since.

    Every picker asks this rather than each reading a flag off its own call,
    so "is the list I'm showing the product of something being broken?" has
    one answer no matter how the caller got here — a page opening on the saved
    list, a background query that just failed, or a Refresh.

    A per-call flag couldn't answer it anyway. Inside the failure backoff a
    stale list is served with no query made at all, so the *call* has nothing
    to report while the list on screen is exactly as wrong as it was a minute
    ago; and on a plain cache hit "no query happened" and "the query failed"
    would be the same False.
    """
    with _lock:
        return _failed_at > 0


_DATE_LEN = 8  # a YYYYMMDD stamp in an id (claude-haiku-4-5-20251001)


def _id_parts(model_id: str) -> list[str]:
    """A model id broken into its meaningful tokens, packaging stripped:
    the cloud providers' ``us.anthropic.`` / ``-v1:0`` wrappers and a
    ``[1m]`` context-window suffix. ``claude-opus-5`` -> ``[claude, opus, 5]``."""
    ident = (model_id or "").strip()
    base = ident.split(":", 1)[0]  # -v1:0 (Bedrock/Vertex)
    base = base.rsplit(".", 1)[-1]  # us.anthropic.
    base = base.split("[", 1)[0]  # claude-opus-5[1m] (a context-window variant)
    return [part for part in base.replace("_", "-").split("-") if part]


def _model_group(model_id: str) -> str:
    """The tier family a model belongs to, for grouping the picker list.

    One of `_DISPLAY_ORDER` when the id names it; otherwise a best-effort
    token pulled from the id (the tier slot: the first part that isn't the
    ``claude`` prefix or a number) so an unrecognized family still clusters
    with its own kind rather than scattering through the list.
    """
    parts = _id_parts(model_id)
    known = next((part for part in parts if part in _DISPLAY_ORDER), None)
    if known is not None:
        return known
    unknown = next((p for p in parts if p != "claude" and not p.isdigit()), None)
    return unknown if unknown is not None else (model_id or "").strip()


def _group_rank(group: str) -> int:
    """Where a family sits in the list: 1..N for the known families in
    `_DISPLAY_ORDER`, and 0 — above them all — for any unrecognized one."""
    try:
        return _DISPLAY_ORDER.index(group) + 1
    except ValueError:
        return 0


def _version_key(model_id: str) -> tuple[int, ...]:
    """The version in an id as a tuple of ints, for ordering within a family:
    ``(4, 8)`` for ``claude-opus-4-8``, ``()`` for a bare alias. Compared
    number-by-number, so Opus 4.8 sorts before Opus 4.10 and Opus 9 before
    Opus 10 — orderings a lexicographic compare of the shown name gets wrong.
    The date stamp some ids carry (``…-20251001``) is not a version part."""
    return tuple(
        int(p) for p in _id_parts(model_id) if p.isdigit() and len(p) != _DATE_LEN
    )


def sort_models(models: list[ClaudeModel]) -> list[ClaudeModel]:
    """The catalog grouped by tier family for display: unrecognized (newer)
    families first, then Mythos, Fable, Opus, Sonnet, Haiku; each family
    ordered by version, newest first — so Opus 5 leads its family, above 4.8.

    Within a family the version tuples are padded to a common width (a missing
    minor version reads as ``.0``) and then negated component-by-component to
    sort newest first while the family order itself stays ascending. The
    padding matters: without it Python's prefix rule makes a shorter tuple
    compare *smaller*, so a bare ``sonnet-4`` snapshot would wrongly sort above
    the newer ``sonnet-4-5`` point release. Versions are non-negative, so the
    negation is just "reverse that one part of the key"."""
    versions = {model.id: _version_key(model.id) for model in models}
    width = max((len(v) for v in versions.values()), default=0)

    def key(model: ClaudeModel):
        group = _model_group(model.id)
        padded = versions[model.id] + (0,) * (width - len(versions[model.id]))
        newest_first = tuple(-n for n in padded)
        return (_group_rank(group), group, newest_first, model.id)

    return sorted(models, key=key)


def grouped_models(models: list[ClaudeModel]) -> list[list[ClaudeModel]]:
    """The sorted catalog split into runs of one tier family each, in display
    order — what a picker draws a divider between. Each inner list is one
    family's models, already version-ordered; the family label itself isn't
    returned because the pickers show the models, not the group name."""
    families: list[list[ClaudeModel]] = []
    last_group = None
    for model in sort_models(models):
        group = _model_group(model.id)
        if group != last_group:
            families.append([])
            last_group = group
        families[-1].append(model)
    return families


def short_name(model_id: str) -> str:
    """A model id written the way a person says it: "Opus 5", "Haiku 4.5".

    For the places that have an id and no room for it — the tab footer names
    the model a session is answering with, and the id it reads off the
    transcript is both longer than the row can spare and noisier than the
    question ("which model?") deserves.

    Ids are pulled apart rather than looked up, so a model released after this
    build still reads correctly: the tier is the part that names one of
    `_TIERS`, the version is whatever digits sit around it (a date stamp
    aside), and everything else — the `claude-` prefix, a cloud provider's
    `us.anthropic.` and `-v1:0` wrappers — is packaging. An id that names no
    known tier is handed back whole; better a long name than a wrong one.
    """
    parts = _id_parts(model_id)
    tier = next((part for part in parts if part in _TIERS), None)
    if tier is None:
        return (model_id or "").strip()
    version = [p for p in parts if p.isdigit() and len(p) != _DATE_LEN]
    return " ".join(filter(None, (tier.capitalize(), ".".join(version))))


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


# Where the CLI reads its settings from, and in what order — the more
# specific file wins, so the first one naming a model is the answer. Managed
# (policy) settings outrank the user's own; a project's local file outranks
# its shared one, which outranks the user file. The user file lives under
# CLAUDE_CONFIG_DIR when that is set, as the CLI's does.
_MANAGED_SETTINGS = Path("/etc/claude-code/managed-settings.json")
_MODEL_ENV = "ANTHROPIC_MODEL"


def _cli_config_dir() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured) if configured else Path.home() / ".claude"


def _settings_files(cwd: str | None) -> list[Path]:
    files = [_MANAGED_SETTINGS]
    if cwd:
        files += [
            Path(cwd) / ".claude" / "settings.local.json",
            Path(cwd) / ".claude" / "settings.json",
        ]
    files.append(_cli_config_dir() / "settings.json")
    return files


def _read_settings(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def cli_default_model(cwd: str | None = None) -> str | None:
    """The model a ``claude`` launched in *cwd* with no ``--model`` runs on,
    as far as it can be told from outside the CLI (see the module docstring
    for the chain), or None when nothing sets one and the CLI's own built-in
    default — unknowable until a session answers — is what applies.

    The value is handed back as written: an alias, a full id, or one with
    the ``[1m]`` context-window suffix ``/model`` can leave behind; all are
    things ``--model`` takes, and short_name reads any of them. A settings
    file's ``env`` block can set ``ANTHROPIC_MODEL`` too, and that counts
    after every file's own ``model`` key, since the CLI applies those blocks
    to the environment before it looks."""
    from_env = (os.environ.get(_MODEL_ENV) or "").strip()
    if from_env:
        return from_env
    settings = [_read_settings(path) for path in _settings_files(cwd)]
    for data in settings:
        model = data.get("model")
        if isinstance(model, str) and model.strip():
            return model.strip()
    for data in settings:
        env = data.get("env")
        model = env.get(_MODEL_ENV) if isinstance(env, dict) else None
        if isinstance(model, str) and model.strip():
            return model.strip()
    return None


def pick_model(setting: str | None, prefer: str = "sonnet") -> str:
    """resolve_model over the live list. Blocking on first use (the list
    may need querying) — call from the worker thread that runs the CLI."""
    setting = (setting or "").strip()
    if setting:
        return setting  # an explicit choice needs no query at all
    return default_model(available_models(), prefer)
