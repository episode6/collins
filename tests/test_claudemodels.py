"""Tests for model discovery and resolution (collins.claudemodels)."""

import json

from collins import claudemodels
from collins.claudemodels import (
    FALLBACK_MODELS,
    ClaudeModel,
    default_model,
    parse_models,
    resolve_model,
    short_name,
)


def _m(model_id: str, created: str = "") -> ClaudeModel:
    return ClaudeModel(model_id, model_id, created)


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


def test_cached_models_never_queries():
    original = claudemodels._cached
    try:
        claudemodels._cached = None
        assert claudemodels.cached_models() is None  # never answered this run
        models = [claudemodels.ClaudeModel("claude-opus-5", "Claude Opus 5")]
        claudemodels._cached = models
        cached = claudemodels.cached_models()
        assert cached == models
        assert cached is not models  # a copy: the cache can't be mutated through it
    finally:
        claudemodels._cached = original


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
