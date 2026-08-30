"""Tests for model discovery and resolution (collins.claudemodels)."""

import json
import logging
import os
import threading
import time

import pytest

from collins import claudemodels
from collins.claudemodels import (
    FALLBACK_MODELS,
    NO_MODEL,
    ClaudeModel,
    default_model,
    parse_models,
    resolve_model,
    short_name,
)


def _m(model_id: str, created: str = "") -> ClaudeModel:
    return ClaudeModel(model_id, model_id, created)


def _catalog(*ids: str) -> list[ClaudeModel]:
    """A list the cache will actually keep.

    Anything shorter than `_MIN_TRUSTED_MODELS` expires the moment it lands,
    so a test about the TTL, the saved file or the backoff has to hand over a
    plausible catalog rather than one model.
    """
    return [_m(i) for i in (ids or ("claude-opus-5", "claude-haiku-4-5"))]


@pytest.fixture(autouse=True)
def cold_cache(tmp_path, monkeypatch):
    """A private cache directory and a cold module for every test.

    The cache now outlives the process, so without this a developer's real
    ~/.cache/collins/models.json would seed the module and tests that expect a
    query would quietly stop making one.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(claudemodels, "_cached", None)
    monkeypatch.setattr(claudemodels, "_cached_at", 0.0)
    monkeypatch.setattr(claudemodels, "_failed_at", 0.0)
    monkeypatch.setattr(claudemodels, "_disk_read", False)


def _restart():
    """What the next launch sees: the file, and nothing in memory."""
    claudemodels._cached = None
    claudemodels._cached_at = 0.0
    claudemodels._failed_at = 0.0
    claudemodels._disk_read = False


def _fetches(models, calls=None):
    """A fetch_models stand-in that counts its calls in *calls*."""

    def fetch(timeout=None):
        if calls is not None:
            calls.append(1)
        return list(models)

    return fetch


# -- parse_models -------------------------------------------------------------


def test_parse_models_reads_api_shape():
    payload = {
        "data": [
            {"id": "claude-sonnet-5", "display_name": "Claude Sonnet 5", "created_at": "2026-02-01"},
            {"id": "claude-haiku-4-5", "display_name": "Claude Haiku 4.5", "created_at": "2025-10-01"},
        ]
    }
    models = parse_models(payload)
    assert [m.id for m in models] == ["claude-sonnet-5", "claude-haiku-4-5"]
    assert models[0].display_name == "Claude Sonnet 5"
    assert models[1].created_at == "2025-10-01"


def _effort_block(**levels):
    block = {"supported": True}
    for level, supported in levels.items():
        block[level] = {"supported": supported}
    return {"effort": block}


def test_parse_models_reads_the_effort_levels():
    # The API names each level on its own; the model keeps the ones it takes,
    # in the CLI's order, whatever order the API listed them in.
    payload = {
        "data": [
            {
                "id": "claude-opus-4-6",
                "capabilities": _effort_block(max=True, low=True, medium=True, high=True, xhigh=False),
            },
            {"id": "claude-haiku-4-5", "capabilities": {"effort": {"supported": False}}},
            {"id": "claude-opus-5", "capabilities": {"effort": {"supported": True}}},
            {"id": "opus"},  # an entry with no capabilities block can't say
            {"id": "claude-x", "capabilities": {"effort": "yes"}},  # junk block: can't say
        ]
    }
    models = parse_models(payload)
    assert models[0].efforts == ("low", "medium", "high", "max")
    assert models[1].efforts == ()
    assert models[2].efforts == claudemodels.EFFORT_LEVELS  # supported as a whole: all of them
    assert models[3].efforts is None
    assert models[4].efforts is None


def test_saved_cache_keeps_the_effort_levels(monkeypatch):
    catalog = [
        ClaudeModel("claude-opus-5", "Claude Opus 5", "2026-02-01", claudemodels.EFFORT_LEVELS),
        ClaudeModel("claude-opus-4-6", "Claude Opus 4.6", "2026-01-01", ("low", "medium", "high")),
        ClaudeModel("claude-haiku-4-5", "Claude Haiku 4.5", "2025-10-01", ()),
        ClaudeModel("claude-old", "Old", "2025-01-01", None),
    ]
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(catalog))
    claudemodels.available_models()
    _restart()
    # (The list comes back sorted for display; the point is every model's
    # levels — known, none, or unknown — survive the trip through the file.)
    by_id = {m.id: m for m in claudemodels.cached_models()}
    assert by_id == {m.id: m for m in catalog}


def test_model_efforts_reads_the_saved_catalog(monkeypatch):
    catalog = [
        ClaudeModel("claude-opus-5", "Claude Opus 5", "2026-02-01", ("low", "high")),
        ClaudeModel("claude-haiku-4-5", "Claude Haiku 4.5", "2025-10-01", ()),
    ]
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(catalog))
    assert claudemodels.model_efforts("claude-opus-5") is None  # nothing cached yet
    claudemodels.available_models()
    assert claudemodels.model_efforts("claude-opus-5") == ("low", "high")
    assert claudemodels.model_efforts("claude-opus-5[1m]") == ("low", "high")  # /model's suffix
    assert claudemodels.model_efforts("claude-haiku-4-5") == ()
    assert claudemodels.model_efforts("opus") is None  # an alias: the catalog can't say
    assert claudemodels.model_efforts("") is None


def test_parse_models_skips_junk():
    payload = {
        "data": [
            "not-a-dict",
            {"no": "id"},
            {"id": ""},
            {"id": 42},
            {"id": "claude-opus-5"},  # missing name falls back to the id
        ]
    }
    models = parse_models(payload)
    assert [m.id for m in models] == ["claude-opus-5"]
    assert models[0].display_name == "claude-opus-5"


def test_parse_models_tolerates_garbage_payload():
    assert parse_models(None) == []
    assert parse_models({"data": None}) == []
    assert parse_models([]) == []


# -- default_model ------------------------------------------------------------


def test_default_is_newest_sonnet():
    models = [
        _m("claude-sonnet-4-6", "2025-11-01"),
        _m("claude-opus-5", "2026-07-24"),
        _m("claude-sonnet-5", "2026-02-01"),
        _m("claude-haiku-4-5", "2025-10-01"),
    ]
    assert default_model(models) == "claude-sonnet-5"


def test_default_without_sonnet_is_weakest_tier():
    models = [
        _m("claude-opus-5", "2026-07-24"),
        _m("claude-haiku-4-5", "2025-10-01"),
        _m("claude-fable-5", "2026-06-01"),
    ]
    assert default_model(models) == "claude-haiku-4-5"


def test_default_weakest_tier_prefers_newest():
    models = [
        _m("claude-3-haiku", "2024-03-01"),
        _m("claude-haiku-4-5", "2025-10-01"),
        _m("claude-opus-5", "2026-07-24"),
    ]
    assert default_model(models) == "claude-haiku-4-5"


def test_unknown_ids_never_read_as_weakest():
    models = [
        _m("claude-mystery-9", "2027-01-01"),
        _m("claude-opus-5", "2026-07-24"),
    ]
    assert default_model(models) == "claude-opus-5"


def test_default_with_no_list_is_the_tier_alias():
    assert default_model([]) == "sonnet"
    assert default_model([], prefer="haiku") == "haiku"


def test_default_prefers_the_requested_tier():
    models = [
        _m("claude-sonnet-5", "2026-02-01"),
        _m("claude-haiku-4-5", "2025-10-01"),
        _m("claude-opus-5", "2026-07-24"),
    ]
    assert default_model(models, prefer="haiku") == "claude-haiku-4-5"


def test_default_preferred_tier_dropped_falls_to_weakest():
    models = [
        _m("claude-sonnet-4-6", "2025-11-01"),
        _m("claude-sonnet-5", "2026-02-01"),
        _m("claude-opus-5", "2026-07-24"),
    ]
    # No Haiku offered: the weakest tier left is Sonnet, newest first.
    assert default_model(models, prefer="haiku") == "claude-sonnet-5"


def test_fallback_aliases_resolve_to_the_tier():
    assert default_model(list(FALLBACK_MODELS)) == "sonnet"
    assert default_model(list(FALLBACK_MODELS), prefer="haiku") == "haiku"


# -- resolve_model / pick_model ----------------------------------------------


def test_explicit_setting_wins():
    models = [_m("claude-sonnet-5", "2026-02-01")]
    assert resolve_model("claude-haiku-4-5", models) == "claude-haiku-4-5"
    assert resolve_model("  opus  ", models) == "opus"


def test_blank_setting_resolves_to_default():
    models = [_m("claude-sonnet-5", "2026-02-01")]
    assert resolve_model("", models) == "claude-sonnet-5"
    assert resolve_model(None, models) == "claude-sonnet-5"


def test_pick_model_with_explicit_setting_needs_no_query():
    # Would hit the network for a blank setting; an explicit one never does.
    assert claudemodels.pick_model("claude-opus-5") == "claude-opus-5"


def test_no_model_is_never_a_model(monkeypatch):
    # The pickers' None is the caller's decision, not a fourth answer: both
    # resolvers refuse it rather than hand it — or any model — to --model.
    monkeypatch.setattr(
        claudemodels, "available_models", lambda: pytest.fail("no query for None")
    )
    models = [_m("claude-sonnet-5", "2026-02-01")]
    with pytest.raises(ValueError):
        resolve_model(NO_MODEL, models)
    with pytest.raises(ValueError):
        resolve_model(f" {NO_MODEL} ", models, prefer="haiku")
    with pytest.raises(ValueError):
        claudemodels.pick_model(NO_MODEL)


def test_available_models_single_flight(monkeypatch):
    # Concurrent callers on a cold cache share one fetch; the queued ones
    # re-read the cache the winner filled instead of querying again.
    calls = []

    def slow_fetch(timeout=None):
        calls.append(1)
        time.sleep(0.2)
        return _catalog()

    monkeypatch.setattr(claudemodels, "fetch_models", slow_fetch)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(claudemodels.available_models()))
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert all(r and r[0].id == "claude-opus-5" for r in results)


def test_cached_models_never_queries(monkeypatch):
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches([], calls := []))
    assert claudemodels.cached_models() is None  # nothing saved, nothing fetched
    models = [claudemodels.ClaudeModel("claude-opus-5", "Claude Opus 5")]
    claudemodels._cached = models
    cached = claudemodels.cached_models()
    assert cached == models
    assert cached is not models  # a copy: the cache can't be mutated through it
    assert calls == []


# -- the cache: lifetime, disk, failures ---------------------------------------


def test_cache_lives_for_a_day():
    assert claudemodels._CACHE_TTL_S == 86_400


def test_fresh_cache_is_not_requeried(monkeypatch):
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog(), calls := []))
    assert [m.id for m in claudemodels.available_models()] == ["claude-opus-5", "claude-haiku-4-5"]
    for _ in range(3):
        claudemodels.available_models()
    assert len(calls) == 1  # the rest came out of the cache


def test_a_lone_model_is_not_cached(monkeypatch):
    # One model is not a catalog anyone can pick from: it serves, because it
    # still beats the aliases, but it expires on arrival so the next ask heals
    # it instead of waiting out the day.
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog("claude-opus-5"), calls := []))
    assert [m.id for m in claudemodels.available_models()] == ["claude-opus-5"]
    claudemodels.available_models()
    assert len(calls) == 2  # asked again rather than serving the one it had

    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog(), calls))
    assert [m.id for m in claudemodels.available_models()] == ["claude-opus-5", "claude-haiku-4-5"]
    claudemodels.available_models()
    assert len(calls) == 3  # a real catalog lands and the TTL applies again


def test_a_lone_saved_model_is_requeried_next_launch(monkeypatch):
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog("claude-opus-5"), calls := []))
    claudemodels.available_models()
    assert claudemodels.cache_path().exists()  # saved: better than the aliases

    _restart()
    assert [m.id for m in claudemodels.cached_models()] == ["claude-opus-5"]  # shown at once
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog(), calls))
    claudemodels.available_models()
    assert len(calls) == 2  # and asked about, however recently it was written


def test_a_lone_model_still_serves_inside_the_failure_backoff(monkeypatch):
    # Zero TTL is not zero patience: with the API refusing, a one-model list
    # is what there is, and every picker open must not pay the timeout again.
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog("claude-opus-5")))
    claudemodels.available_models()

    monkeypatch.setattr(claudemodels, "fetch_models", _fetches([], calls := []))
    for _ in range(3):
        assert [m.id for m in claudemodels.available_models()] == ["claude-opus-5"]
    assert len(calls) == 1


def test_a_lone_model_warns_that_it_will_be_asked_again(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="collins.claudemodels")
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog("claude-opus-5")))
    claudemodels.available_models()
    assert "only 1 model(s)" in caplog.text


def test_stale_cache_is_requeried(monkeypatch):
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog(), calls := []))
    claudemodels.available_models()
    claudemodels._cached_at = time.time() - claudemodels._CACHE_TTL_S - 1
    claudemodels.available_models()
    assert len(calls) == 2


def test_cache_survives_a_restart(monkeypatch):
    monkeypatch.setattr(
        claudemodels,
        "fetch_models",
        _fetches(
            [
                ClaudeModel("claude-opus-5", "Claude Opus 5", "2026-02-01"),
                ClaudeModel("claude-haiku-4-5", "Claude Haiku 4.5", "2025-10-01"),
            ],
            calls := [],
        ),
    )
    claudemodels.available_models()
    assert claudemodels.cache_path().exists()
    assert len(calls) == 1

    _restart()
    # The next launch has the list before it asks anyone: display names and
    # dates included, and cached_models() alone is enough to get it.
    cached = claudemodels.cached_models()
    assert cached == [
        ClaudeModel("claude-opus-5", "Claude Opus 5", "2026-02-01"),
        ClaudeModel("claude-haiku-4-5", "Claude Haiku 4.5", "2025-10-01"),
    ]
    assert claudemodels.available_models() == cached
    assert len(calls) == 1  # still the one query, a process ago


def test_saved_cache_ages_out_across_a_restart(monkeypatch):
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog(), calls := []))
    claudemodels.available_models()
    stale = json.loads(claudemodels.cache_path().read_text())
    stale["fetched_at"] = time.time() - claudemodels._CACHE_TTL_S - 1
    claudemodels.cache_path().write_text(json.dumps(stale))

    _restart()
    claudemodels.available_models()
    assert len(calls) == 2


def test_saved_cache_from_the_future_is_stale(monkeypatch):
    # A clock change or a copied home directory; re-querying is the cheap
    # mistake, and a failure would keep the list anyway.
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog(), calls := []))
    claudemodels.available_models()
    ahead = json.loads(claudemodels.cache_path().read_text())
    ahead["fetched_at"] = time.time() + 86_400
    claudemodels.cache_path().write_text(json.dumps(ahead))

    _restart()
    claudemodels.available_models()
    assert len(calls) == 2


@pytest.mark.parametrize(
    "text",
    [
        "not json at all",
        json.dumps({"version": 999, "fetched_at": 0, "data": [{"id": "claude-opus-5"}]}),
        # no fetched_at
        json.dumps({"version": claudemodels._CACHE_VERSION, "data": [{"id": "claude-opus-5"}]}),
        json.dumps({"version": claudemodels._CACHE_VERSION, "fetched_at": 0, "data": []}),
        # the shape saved before the effort levels were read
        json.dumps({"version": 1, "fetched_at": 0, "data": [{"id": "claude-opus-5"}]}),
        json.dumps([1, 2, 3]),
    ],
)
def test_unusable_saved_cache_is_ignored(text, monkeypatch):
    path = claudemodels.cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog(), calls := []))
    assert claudemodels.cached_models() is None
    assert [m.id for m in claudemodels.available_models()] == ["claude-opus-5", "claude-haiku-4-5"]
    assert len(calls) == 1


def test_failed_query_keeps_the_cached_results(monkeypatch):
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog()))
    claudemodels.available_models()
    claudemodels._cached_at = time.time() - claudemodels._CACHE_TTL_S - 1

    monkeypatch.setattr(claudemodels, "fetch_models", _fetches([]))  # offline now
    models = claudemodels.refresh_models()
    # the stale list still serves
    assert [m.id for m in models] == ["claude-opus-5", "claude-haiku-4-5"]
    assert claudemodels.cache_failed() is True
    assert claudemodels.cache_path().exists()  # and the file is not cleared either


def test_failed_query_with_nothing_cached_is_empty(monkeypatch):
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches([]))
    assert claudemodels.available_models() == []  # the callers fall back to the aliases


def test_failed_query_backs_off(monkeypatch):
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog()))
    claudemodels.available_models()
    claudemodels._cached_at = time.time() - claudemodels._CACHE_TTL_S - 1

    monkeypatch.setattr(claudemodels, "fetch_models", _fetches([], calls := []))
    claudemodels.available_models()
    for _ in range(3):
        claudemodels.available_models()  # every picker open, offline
    assert len(calls) == 1  # one timeout paid, not four

    claudemodels._failed_at = time.time() - claudemodels._RETRY_AFTER_FAILURE_S - 1
    claudemodels.available_models()
    assert len(calls) == 2  # the backoff lapses and it tries again


def test_refresh_ignores_the_ttl_and_the_backoff(monkeypatch):
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog(), calls := []))
    claudemodels.available_models()
    assert len(calls) == 1
    claudemodels.available_models()
    assert len(calls) == 1  # cache is fresh

    models = claudemodels.refresh_models()
    assert len(calls) == 2  # asked anyway
    assert [m.id for m in models] == ["claude-opus-5", "claude-haiku-4-5"]
    assert claudemodels.cache_failed() is False

    claudemodels._failed_at = time.time()  # a failure moments ago
    claudemodels.refresh_models()
    assert len(calls) == 3


def test_refresh_rewrites_the_saved_list(monkeypatch):
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog()))
    claudemodels.available_models()
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog("claude-fable-5", "claude-sonnet-5")))
    claudemodels.refresh_models()

    _restart()
    assert [m.id for m in claudemodels.cached_models()] == ["claude-fable-5", "claude-sonnet-5"]


def test_cache_fetched_at_dates_the_list(monkeypatch):
    assert claudemodels.cache_fetched_at() == 0.0  # nothing cached
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog()))
    before = time.time()
    claudemodels.available_models()
    assert before <= claudemodels.cache_fetched_at() <= time.time()

    _restart()
    assert before <= claudemodels.cache_fetched_at() <= time.time()  # read back off disk


def test_a_cache_that_cannot_be_written_is_not_fatal(monkeypatch, tmp_path, caplog):
    # A read-only cache dir costs a query next launch and nothing else; the
    # models still reach the caller.
    caplog.set_level(logging.WARNING, logger="collins.claudemodels")
    readonly = tmp_path / "readonly"
    readonly.mkdir()
    readonly.chmod(0o500)
    monkeypatch.setattr(claudemodels, "cache_path", lambda: readonly / "models.json")
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog()))
    try:
        assert [m.id for m in claudemodels.available_models()] == [
            "claude-opus-5",
            "claude-haiku-4-5",
        ]
    finally:
        readonly.chmod(0o700)
    if os.getuid() != 0:  # root writes anywhere, so there is nothing to warn about
        assert "cannot save the list" in caplog.text


def test_cache_failed_tracks_the_last_attempt(monkeypatch):
    assert claudemodels.cache_failed() is False  # nothing has been tried yet

    monkeypatch.setattr(claudemodels, "fetch_models", _fetches([]))
    claudemodels.available_models()
    assert claudemodels.cache_failed() is True

    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog()))
    claudemodels.available_models()
    assert claudemodels.cache_failed() is False  # a success clears it again

    monkeypatch.setattr(claudemodels, "fetch_models", _fetches([]))
    claudemodels.refresh_models()
    assert claudemodels.cache_failed() is True


def test_cache_failed_survives_the_backoff(monkeypatch):
    # The case a per-call flag can't see: inside the backoff the stale list is
    # served with no query made at all, so the call has nothing to report
    # while the list on screen is exactly as wrong as it was a minute ago.
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog()))
    claudemodels.available_models()
    claudemodels._cached_at = time.time() - claudemodels._CACHE_TTL_S - 1

    monkeypatch.setattr(claudemodels, "fetch_models", _fetches([], calls := []))
    claudemodels.available_models()
    assert len(calls) == 1 and claudemodels.cache_failed() is True

    claudemodels.available_models()  # served out of the backoff, no query
    assert len(calls) == 1
    assert claudemodels.cache_failed() is True  # still broken, and still says so


def test_a_fresh_cache_hit_is_not_a_failure(monkeypatch):
    # "No query happened" must not read as "the query failed" — the pickers
    # would cry wolf on every page open with a perfectly good day-old list.
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog(), calls := []))
    claudemodels.available_models()
    claudemodels.available_models()
    assert len(calls) == 1  # the second was a cache hit
    assert claudemodels.cache_failed() is False


def test_cache_failed_is_false_on_a_saved_list(monkeypatch):
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog()))
    claudemodels.available_models()
    _restart()
    assert claudemodels.cached_models() is not None
    assert claudemodels.cache_failed() is False  # last run's success carries no failure


# -- the logs ------------------------------------------------------------------


def test_a_successful_query_logs_what_came_back(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="collins.claudemodels")
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog()))
    claudemodels.available_models()
    text = caplog.text
    assert "querying" in text
    assert "2 received" in text and "claude-opus-5" in text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_failed_query_warns_and_names_the_fallback(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="collins.claudemodels")
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches([]))
    claudemodels.available_models()
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "query failed" in warnings[0] and "aliases" in warnings[0]


def test_a_failed_query_warns_that_the_cache_still_serves(monkeypatch, caplog):
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog()))
    claudemodels.available_models()
    caplog.set_level(logging.WARNING, logger="collins.claudemodels")
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches([]))
    claudemodels.refresh_models()
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "keeping the 2 cached model(s)" in warnings[0]


def test_a_missing_token_says_so(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="collins.claudemodels")
    monkeypatch.setattr(claudemodels, "_oauth_token", lambda: None)
    assert claudemodels.fetch_models() == []
    assert "no OAuth token" in caplog.text


def test_a_refused_request_says_why(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="collins.claudemodels")
    monkeypatch.setattr(claudemodels, "_oauth_token", lambda: "sk-test")

    def boom(request, timeout=None):
        raise OSError("Network is unreachable")

    monkeypatch.setattr(claudemodels.urllib.request, "urlopen", boom)
    assert claudemodels.fetch_models() == []
    assert "Network is unreachable" in caplog.text


def test_a_restart_logs_the_list_it_loaded(monkeypatch, caplog):
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(_catalog()))
    claudemodels.available_models()
    _restart()
    caplog.set_level(logging.INFO, logger="collins.claudemodels")
    claudemodels.cached_models()
    assert "2 loaded from" in caplog.text and "fetched 0s ago" in caplog.text


# -- credentials --------------------------------------------------------------


def test_oauth_token_read_and_absent(tmp_path):
    original = claudemodels.CREDENTIALS_PATH
    try:
        claudemodels.CREDENTIALS_PATH = tmp_path / "credentials.json"
        assert claudemodels._oauth_token() is None  # no file
        claudemodels.CREDENTIALS_PATH.write_text("not json")
        assert claudemodels._oauth_token() is None
        claudemodels.CREDENTIALS_PATH.write_text(json.dumps({"claudeAiOauth": {}}))
        assert claudemodels._oauth_token() is None
        claudemodels.CREDENTIALS_PATH.write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "tok-123"}})
        )
        assert claudemodels._oauth_token() == "tok-123"
    finally:
        claudemodels.CREDENTIALS_PATH = original


# -- short_name ---------------------------------------------------------------


def test_short_name_reads_current_ids():
    assert short_name("claude-opus-5") == "Opus 5"
    assert short_name("claude-sonnet-5") == "Sonnet 5"
    assert short_name("claude-fable-5") == "Fable 5"
    assert short_name("claude-opus-4-8") == "Opus 4.8"


def test_short_name_drops_a_date_stamp():
    assert short_name("claude-haiku-4-5-20251001") == "Haiku 4.5"


def test_short_name_reads_the_old_id_order():
    """Pre-2025 ids put the version ahead of the tier."""
    assert short_name("claude-3-5-sonnet-20241022") == "Sonnet 3.5"


def test_short_name_unwraps_cloud_and_variant_ids():
    assert short_name("us.anthropic.claude-sonnet-4-20250514-v1:0") == "Sonnet 4"
    assert short_name("claude-opus-5[1m]") == "Opus 5"


def test_short_name_of_a_bare_alias_is_the_tier():
    assert short_name("opus") == "Opus"


def test_short_name_hands_back_what_it_cannot_read():
    """Better a long name than a wrong one — an id naming no known tier is
    shown as it came."""
    assert short_name("some-future-model-9") == "some-future-model-9"
    assert short_name("") == ""


# -- sort_models --------------------------------------------------------------


def test_sort_groups_families_in_display_order():
    # Mythos, then Fable, Opus, Sonnet, Haiku — regardless of how they arrive.
    models = [
        _m("claude-haiku-4-5"),
        _m("claude-sonnet-5"),
        _m("claude-opus-5"),
        _m("claude-fable-5"),
        _m("claude-mythos-5"),
    ]
    assert [m.id for m in claudemodels.sort_models(models)] == [
        "claude-mythos-5",
        "claude-fable-5",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-haiku-4-5",
    ]


def test_sort_puts_mythos_above_fable():
    models = [_m("claude-fable-5"), _m("claude-mythos-5")]
    assert [m.id for m in claudemodels.sort_models(models)] == [
        "claude-mythos-5",
        "claude-fable-5",
    ]


def test_sort_orders_within_a_family_by_version_newest_first():
    # Numeric and newest-first: 5 > 4.10 > 4.8 > 4.1, so 5 leads the family.
    # An alphabetical sort of the shown names would wrongly wedge "4.10"
    # between "4.1" and "4.8".
    models = [
        ClaudeModel("claude-opus-4-8", "Claude Opus 4.8"),
        ClaudeModel("claude-opus-5", "Claude Opus 5"),
        ClaudeModel("claude-opus-4-1", "Claude Opus 4.1"),
        ClaudeModel("claude-opus-4-10", "Claude Opus 4.10"),
    ]
    assert [m.id for m in claudemodels.sort_models(models)] == [
        "claude-opus-5",
        "claude-opus-4-10",
        "claude-opus-4-8",
        "claude-opus-4-1",
    ]


def test_sort_handles_mixed_length_versions_in_a_family():
    # A bare-major snapshot (version tuple (4,), a date but no minor) and a
    # later point release (4, 5) live in the catalog at once. Newest-first must
    # put 4.5 above bare 4 — not the reverse, which Python's tuple prefix rule
    # would give a naive negate of unequal-length tuples.
    models = [
        ClaudeModel("us.anthropic.claude-sonnet-4-20250514-v1:0", "Sonnet 4"),
        ClaudeModel("claude-sonnet-4-5", "Sonnet 4.5"),
        ClaudeModel("claude-sonnet-5", "Sonnet 5"),
    ]
    assert [m.display_name for m in claudemodels.sort_models(models)] == [
        "Sonnet 5",
        "Sonnet 4.5",
        "Sonnet 4",
    ]


def test_sort_puts_unknown_families_on_top_clustered():
    # A family named nowhere in _DISPLAY_ORDER sorts above every named one —
    # above Mythos too — and its models stay together rather than scattering.
    models = [
        _m("claude-opus-5"),
        _m("claude-zephyr-6"),
        _m("claude-mythos-5"),
        _m("claude-haiku-4-5"),
        _m("claude-zephyr-5"),
    ]
    assert [m.id for m in claudemodels.sort_models(models)] == [
        "claude-zephyr-6",
        "claude-zephyr-5",
        "claude-mythos-5",
        "claude-opus-5",
        "claude-haiku-4-5",
    ]


def test_available_models_comes_out_grouped(monkeypatch):
    jumbled = _catalog("claude-haiku-4-5", "claude-fable-5", "claude-sonnet-5", "claude-opus-5")
    monkeypatch.setattr(claudemodels, "fetch_models", _fetches(jumbled))
    assert [m.id for m in claudemodels.available_models()] == [
        "claude-fable-5",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-haiku-4-5",
    ]


def test_fallback_models_are_in_display_order():
    assert [m.id for m in FALLBACK_MODELS] == ["opus", "sonnet", "haiku"]


# -- grouped_models -----------------------------------------------------------


def test_grouped_models_splits_sorted_catalog_by_family():
    # One inner list per family, in display order, each version-ordered — the
    # runs a picker draws a divider between.
    models = [
        _m("claude-haiku-4-5"),
        _m("claude-opus-4-8"),
        _m("claude-opus-5"),
        _m("claude-fable-5"),
    ]
    groups = claudemodels.grouped_models(models)
    assert [[m.id for m in g] for g in groups] == [
        ["claude-fable-5"],
        ["claude-opus-5", "claude-opus-4-8"],
        ["claude-haiku-4-5"],
    ]


def test_grouped_models_clusters_each_unknown_family_on_its_own():
    models = [
        _m("claude-opus-5"),
        _m("claude-zephyr-5"),
        _m("claude-mythos-5"),
    ]
    groups = claudemodels.grouped_models(models)
    assert [[m.id for m in g] for g in groups] == [
        ["claude-zephyr-5"],
        ["claude-mythos-5"],
        ["claude-opus-5"],
    ]


def test_grouped_models_of_nothing_is_empty():
    assert claudemodels.grouped_models([]) == []


# -- the CLI's own default ------------------------------------------------------


def _settings_home(monkeypatch, tmp_path, user: dict | None = None):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    settings = home / ".claude" / "settings.json"
    if user is not None:
        settings.write_text(json.dumps(user))
    elif settings.exists():
        settings.unlink()
    monkeypatch.setattr(claudemodels.Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.setattr(claudemodels, "_MANAGED_SETTINGS", tmp_path / "managed.json")
    return home


def test_cli_default_model_reads_the_user_settings(monkeypatch, tmp_path):
    _settings_home(monkeypatch, tmp_path, {"model": "claude-fable-5[1m]"})
    assert claudemodels.cli_default_model(str(tmp_path / "proj")) == "claude-fable-5[1m]"
    assert claudemodels.cli_default_model(None) == "claude-fable-5[1m]"


def test_cli_default_model_is_none_when_nothing_sets_one(monkeypatch, tmp_path):
    _settings_home(monkeypatch, tmp_path, {"theme": "dark", "model": ""})
    assert claudemodels.cli_default_model(str(tmp_path / "proj")) is None
    _settings_home(monkeypatch, tmp_path, None)  # no file at all
    assert claudemodels.cli_default_model(None) is None


def test_cli_default_model_prefers_the_more_specific_file(monkeypatch, tmp_path):
    _settings_home(monkeypatch, tmp_path, {"model": "user"})
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "settings.json").write_text(json.dumps({"model": "project"}))
    assert claudemodels.cli_default_model(str(proj)) == "project"
    (proj / ".claude" / "settings.local.json").write_text(json.dumps({"model": "local"}))
    assert claudemodels.cli_default_model(str(proj)) == "local"
    (tmp_path / "managed.json").write_text(json.dumps({"model": "managed"}))
    assert claudemodels.cli_default_model(str(proj)) == "managed"
    # Another project sees none of that project's files.
    assert claudemodels.cli_default_model(str(tmp_path / "other")) == "managed"


def test_cli_default_model_env_outranks_every_file(monkeypatch, tmp_path):
    _settings_home(monkeypatch, tmp_path, {"model": "user"})
    monkeypatch.setenv("ANTHROPIC_MODEL", "opus")
    assert claudemodels.cli_default_model(None) == "opus"


def test_cli_default_model_reads_a_settings_env_block_last(monkeypatch, tmp_path):
    _settings_home(monkeypatch, tmp_path, {"env": {"ANTHROPIC_MODEL": "haiku"}})
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    assert claudemodels.cli_default_model(str(proj)) == "haiku"
    # A file's own model key wins over any file's env block.
    (proj / ".claude" / "settings.json").write_text(json.dumps({"model": "sonnet"}))
    assert claudemodels.cli_default_model(str(proj)) == "sonnet"


def test_cli_default_model_honours_claude_config_dir(monkeypatch, tmp_path):
    _settings_home(monkeypatch, tmp_path, {"model": "home"})
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "settings.json").write_text(json.dumps({"model": "elsewhere"}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(other))
    assert claudemodels.cli_default_model(None) == "elsewhere"


def test_cli_default_effort_reads_the_key_effort_saves(monkeypatch, tmp_path):
    _settings_home(monkeypatch, tmp_path, {"model": "opus", "effortLevel": "xhigh"})
    assert claudemodels.cli_default_effort(str(tmp_path / "proj")) == "xhigh"
    assert claudemodels.cli_default_effort(None) == "xhigh"
    _settings_home(monkeypatch, tmp_path, {"model": "opus"})
    assert claudemodels.cli_default_effort(None) is None


def test_cli_default_effort_walks_the_same_chain_as_the_model(monkeypatch, tmp_path):
    _settings_home(monkeypatch, tmp_path, {"effortLevel": "user"})
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "settings.json").write_text(json.dumps({"effortLevel": "project"}))
    assert claudemodels.cli_default_effort(str(proj)) == "project"
    # A file's env block counts after every file's own key.
    (tmp_path / "managed.json").write_text(json.dumps({"env": {"CLAUDE_CODE_EFFORT_LEVEL": "env"}}))
    assert claudemodels.cli_default_effort(str(proj)) == "project"
    (tmp_path / "managed.json").write_text(json.dumps({"effortLevel": "managed"}))
    assert claudemodels.cli_default_effort(str(proj)) == "managed"
    # The environment's pin outranks them all — the CLI says as much in /effort.
    monkeypatch.setenv("CLAUDE_CODE_EFFORT_LEVEL", "low")
    assert claudemodels.cli_default_effort(str(proj)) == "low"


def test_cli_default_effort_shrugs_off_junk(monkeypatch, tmp_path):
    _settings_home(monkeypatch, tmp_path, {"effortLevel": 3, "env": "x"})
    assert claudemodels.cli_default_effort(None) is None
    _settings_home(monkeypatch, tmp_path, {"effortLevel": "  "})
    assert claudemodels.cli_default_effort(None) is None


def test_cli_default_model_shrugs_off_junk_files(monkeypatch, tmp_path):
    home = _settings_home(monkeypatch, tmp_path, None)
    (home / ".claude" / "settings.json").write_text("{not json")
    assert claudemodels.cli_default_model(None) is None
    (home / ".claude" / "settings.json").write_text(json.dumps(["a list"]))
    assert claudemodels.cli_default_model(None) is None
    (home / ".claude" / "settings.json").write_text(json.dumps({"model": 5, "env": "x"}))
    assert claudemodels.cli_default_model(None) is None
