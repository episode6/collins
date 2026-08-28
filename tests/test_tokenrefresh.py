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


@pytest.fixture(autouse=True)
def _fresh_attempt_state(monkeypatch):
    """The single-flight flag and cooldown clock are module-global; every
    test starts with no attempt made yet."""
    monkeypatch.setattr(tokenrefresh, "_running", False)
    monkeypatch.setattr(tokenrefresh, "_last_attempt", None)
    monkeypatch.setattr(tokenrefresh, "_failures", 0)


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


# -- maybe_repair --------------------------------------------------------------


def test_maybe_repair_is_off_under_the_fixture_harness(credentials, monkeypatch):
    monkeypatch.setenv("COLLINS_USAGE_FIXTURE", "/nowhere/usage.json")
    assert tokenrefresh.maybe_repair(lambda: None) is None


def test_maybe_repair_skips_a_cli_less_install(credentials, monkeypatch):
    monkeypatch.setattr(tokenrefresh.clisetup, "on_path", lambda: False)
    events = []
    monkeypatch.setattr(tokenrefresh, "refresh", lambda: events.append("refresh") or True)
    thread = tokenrefresh.maybe_repair(lambda: events.append("cb"))
    thread.join(5)
    assert events == []


def test_maybe_repair_trusts_the_caller_over_the_file(credentials, monkeypatch):
    # The auth case: the file holds an unexpired token, but the server just
    # refused it — the observed error outranks token_expired().
    _write_credentials(credentials)
    monkeypatch.setattr(tokenrefresh.clisetup, "on_path", lambda: True)
    events = []
    monkeypatch.setattr(tokenrefresh, "refresh", lambda: events.append("refresh") or True)
    monkeypatch.setattr(claudemodels, "cache_failed", lambda: False)
    thread = tokenrefresh.maybe_repair(lambda: events.append("cb"))
    thread.join(5)
    assert events == ["refresh", "cb"]


def test_attempts_are_cooled_down(credentials, monkeypatch):
    # A broken login reports the same failure on every poll; only the first
    # report inside the cooldown spends a run — success or not. The refused
    # calls are turned away at the peek, before any thread exists.
    _write_credentials(credentials)
    monkeypatch.setattr(tokenrefresh.clisetup, "on_path", lambda: True)
    events = []
    monkeypatch.setattr(tokenrefresh, "refresh", lambda: events.append("refresh") or False)
    tokenrefresh.maybe_repair(lambda: events.append("cb")).join(5)
    assert tokenrefresh.maybe_repair(lambda: events.append("cb")) is None
    assert tokenrefresh.maybe_repair(lambda: events.append("cb")) is None
    assert events == ["refresh"]


def test_cooldown_expiry_allows_another_attempt(credentials, monkeypatch):
    _write_credentials(credentials)
    monkeypatch.setattr(tokenrefresh.clisetup, "on_path", lambda: True)
    events = []
    monkeypatch.setattr(tokenrefresh, "refresh", lambda: events.append("refresh") or False)
    tokenrefresh.maybe_repair(lambda: events.append("cb")).join(5)
    monkeypatch.setattr(
        tokenrefresh,
        "_last_attempt",
        tokenrefresh._last_attempt - tokenrefresh._REPAIR_COOLDOWN_S,
    )
    tokenrefresh.maybe_repair(lambda: events.append("cb")).join(5)
    assert events == ["refresh", "refresh"]


def test_launch_attempt_counts_toward_the_cooldown(credentials, monkeypatch):
    # The launch check and a panel's first failing poll notice the same dead
    # login; the launch's attempt is the one that runs.
    _write_credentials(credentials, expires_in_s=-60)
    monkeypatch.setattr(tokenrefresh.clisetup, "on_path", lambda: True)
    events = []
    monkeypatch.setattr(tokenrefresh, "refresh", lambda: events.append("refresh") or False)
    tokenrefresh.maybe_start(lambda: events.append("cb")).join(5)
    assert tokenrefresh.maybe_repair(lambda: events.append("cb")) is None
    assert events == ["refresh"]


def _rewind_cooldown(monkeypatch):
    """Pretend the current cooldown has just elapsed."""
    monkeypatch.setattr(
        tokenrefresh,
        "_last_attempt",
        tokenrefresh._last_attempt - tokenrefresh._cooldown_s(),
    )


def test_consecutive_failures_double_the_cooldown(credentials, monkeypatch):
    # A login no run can fix keeps failing; every failure doubles the wait
    # before the next attempt — an hour, two, four — so a broken login
    # stops costing a subprocess an hour.
    _write_credentials(credentials)
    monkeypatch.setattr(tokenrefresh.clisetup, "on_path", lambda: True)
    events = []
    monkeypatch.setattr(tokenrefresh, "refresh", lambda: events.append("refresh") or False)
    hour = tokenrefresh._REPAIR_COOLDOWN_S
    tokenrefresh.maybe_repair(lambda: None).join(5)
    assert tokenrefresh._cooldown_s() == hour
    _rewind_cooldown(monkeypatch)
    tokenrefresh.maybe_repair(lambda: None).join(5)
    assert tokenrefresh._cooldown_s() == 2 * hour
    # An hour past the second failure is inside its two-hour cooldown.
    monkeypatch.setattr(tokenrefresh, "_last_attempt", tokenrefresh._last_attempt - hour)
    assert tokenrefresh.maybe_repair(lambda: None) is None
    _rewind_cooldown(monkeypatch)
    tokenrefresh.maybe_repair(lambda: None).join(5)
    assert tokenrefresh._cooldown_s() == 4 * hour
    assert events == ["refresh"] * 3


def test_the_cooldown_caps_at_a_day(monkeypatch):
    monkeypatch.setattr(tokenrefresh, "_failures", 40)
    assert tokenrefresh._cooldown_s() == tokenrefresh._REPAIR_COOLDOWN_MAX_S


def test_a_success_resets_the_backoff(credentials, monkeypatch):
    _write_credentials(credentials)
    monkeypatch.setattr(tokenrefresh.clisetup, "on_path", lambda: True)
    monkeypatch.setattr(claudemodels, "cache_failed", lambda: False)
    monkeypatch.setattr(tokenrefresh, "_failures", 5)
    monkeypatch.setattr(tokenrefresh, "refresh", lambda: True)
    tokenrefresh.maybe_repair(lambda: None).join(5)
    assert tokenrefresh._failures == 0
    assert tokenrefresh._cooldown_s() == tokenrefresh._REPAIR_COOLDOWN_S


def test_a_running_attempt_is_single_flight(credentials, monkeypatch):
    assert tokenrefresh._begin_attempt()
    assert not tokenrefresh._begin_attempt()  # running
    with tokenrefresh._attempt_lock:
        tokenrefresh._running = False
    assert not tokenrefresh._begin_attempt()  # done running, but cooling down
