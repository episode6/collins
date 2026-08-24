# New in the ghackett fork of agent-session-manager (GPL-3.0).

import json
import subprocess
import time
from pathlib import Path

import pytest

import collins.sessions as sessions_mod
import collins.titles as titles
from collins import claudemodels, tokenrefresh


def _write_credentials(path: Path, expires_in_s: float = 3600) -> None:
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


@pytest.fixture
def credentials(tmp_path, monkeypatch):
    """A credentials path the module reads (via COLLINS_CLAUDE_CREDENTIALS);
    starts nonexistent — each test writes the state it needs."""
    path = tmp_path / "credentials.json"
    monkeypatch.setenv("COLLINS_CLAUDE_CREDENTIALS", str(path))
    return path


@pytest.fixture
def scratch(app_state, tmp_path, monkeypatch):
    """Keep scratch_workdir's transcript sweep off the real projects dir."""
    monkeypatch.setattr(sessions_mod, "CLAUDE_PROJECTS_DIR", tmp_path / "projects")


# -- token_expired / token_valid ----------------------------------------------


def test_no_credentials_is_not_expired(credentials):
    # Not logged in at all: nothing a throwaway run could fix.
    assert not tokenrefresh.token_expired()
    assert not tokenrefresh.token_valid()


def test_expired_token_is_expired(credentials):
    _write_credentials(credentials, expires_in_s=-60)
    assert tokenrefresh.token_expired()
    assert not tokenrefresh.token_valid()


def test_live_token_is_valid(credentials):
    _write_credentials(credentials)
    assert not tokenrefresh.token_expired()
    assert tokenrefresh.token_valid()


# -- refresh -------------------------------------------------------------------


def test_refresh_without_cli_is_false(credentials, monkeypatch):
    _write_credentials(credentials, expires_in_s=-60)
    monkeypatch.setattr("shutil.which", lambda name: None)
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))
    assert tokenrefresh.refresh() is False
    assert ran == []


def test_refresh_runs_cli_in_scratch_and_reads_the_file(
    credentials, scratch, monkeypatch
):
    _write_credentials(credentials, expires_in_s=-60)
    calls = {}

    def fake_run(argv, **kwargs):
        calls["argv"] = argv
        calls["cwd"] = kwargs.get("cwd")
        # What the real CLI does at startup: rewrite the credentials file.
        _write_credentials(credentials)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert tokenrefresh.refresh() is True
    assert calls["argv"][:2] == ["/usr/bin/claude", "-p"]
    assert "--model" in calls["argv"]
    # The throwaway run lives in titles' scratch tree, so discovery skips it.
    assert Path(calls["cwd"]).parent == titles.scratch_dir()


def test_refresh_trusts_the_file_over_the_exit_code(credentials, scratch, monkeypatch):
    # The token refresh happens at CLI startup; a turn that then fails can
    # still have refreshed it.
    _write_credentials(credentials, expires_in_s=-60)

    def fake_run(argv, **kwargs):
        _write_credentials(credentials)
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert tokenrefresh.refresh() is True


def test_refresh_that_changes_nothing_is_false(credentials, scratch, monkeypatch):
    _write_credentials(credentials, expires_in_s=-60)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **k: subprocess.CompletedProcess(argv, 0, stdout="ok", stderr=""),
    )
    assert tokenrefresh.refresh() is False


def test_refresh_timeout_is_false(credentials, scratch, monkeypatch):
    _write_credentials(credentials, expires_in_s=-60)

    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout") or 0)

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert tokenrefresh.refresh() is False


# -- maybe_start ---------------------------------------------------------------


def test_maybe_start_is_off_under_the_fixture_harness(credentials, monkeypatch):
    monkeypatch.setenv("COLLINS_USAGE_FIXTURE", "/nowhere/usage.json")
    assert tokenrefresh.maybe_start(lambda: None) is None


def test_maybe_start_leaves_a_live_token_alone(credentials, monkeypatch):
    _write_credentials(credentials)
    monkeypatch.setattr(tokenrefresh.clisetup, "on_path", lambda: True)
    events = []
    monkeypatch.setattr(tokenrefresh, "refresh", lambda: events.append("refresh") or True)
    thread = tokenrefresh.maybe_start(lambda: events.append("cb"))
    thread.join(5)
    assert events == []


def test_maybe_start_skips_a_cli_less_install(credentials, monkeypatch):
    _write_credentials(credentials, expires_in_s=-60)
    monkeypatch.setattr(tokenrefresh.clisetup, "on_path", lambda: False)
    events = []
    monkeypatch.setattr(tokenrefresh, "refresh", lambda: events.append("refresh") or True)
    thread = tokenrefresh.maybe_start(lambda: events.append("cb"))
    thread.join(5)
    assert events == []


def test_maybe_start_refreshes_retries_models_then_signals(credentials, monkeypatch):
    _write_credentials(credentials, expires_in_s=-60)
    monkeypatch.setattr(tokenrefresh.clisetup, "on_path", lambda: True)
    events = []
    monkeypatch.setattr(tokenrefresh, "refresh", lambda: events.append("refresh") or True)
    monkeypatch.setattr(claudemodels, "cache_failed", lambda: True)
    monkeypatch.setattr(claudemodels, "refresh_models", lambda: events.append("models") or [])
    thread = tokenrefresh.maybe_start(lambda: events.append("cb"))
    thread.join(5)
    assert events == ["refresh", "models", "cb"]


def test_maybe_start_skips_the_models_retry_when_nothing_failed(
    credentials, monkeypatch
):
    _write_credentials(credentials, expires_in_s=-60)
    monkeypatch.setattr(tokenrefresh.clisetup, "on_path", lambda: True)
    events = []
    monkeypatch.setattr(tokenrefresh, "refresh", lambda: events.append("refresh") or True)
    monkeypatch.setattr(claudemodels, "cache_failed", lambda: False)
    monkeypatch.setattr(claudemodels, "refresh_models", lambda: events.append("models") or [])
    thread = tokenrefresh.maybe_start(lambda: events.append("cb"))
    thread.join(5)
    assert events == ["refresh", "cb"]


def test_maybe_start_failed_refresh_never_signals(credentials, monkeypatch):
    _write_credentials(credentials, expires_in_s=-60)
    monkeypatch.setattr(tokenrefresh.clisetup, "on_path", lambda: True)
    events = []
    monkeypatch.setattr(tokenrefresh, "refresh", lambda: events.append("refresh") or False)
    thread = tokenrefresh.maybe_start(lambda: events.append("cb"))
    thread.join(5)
    assert events == ["refresh"]
