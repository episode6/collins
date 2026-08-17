# New in the ghackett fork of agent-session-manager (GPL-3.0).

import json
import time
from pathlib import Path

import pytest

from collins.remotearchive import bridge_session_id, sync_archived, sync_archived_async

# -- bridge_session_id ---------------------------------------------------------


def _transcript(tmp_path, lines: list) -> Path:
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    return path


def _bridge_line(session_id: str = "cse_01AbCdEf") -> dict:
    return {
        "type": "bridge-session",
        "bridgeSessionId": session_id,
        "sessionId": "0000-local",
        "lastSequenceNum": 3,
    }


def test_bridge_session_id_absent(tmp_path):
    path = _transcript(tmp_path, [{"type": "user"}, {"type": "assistant"}])
    assert bridge_session_id(path) is None


def test_bridge_session_id_found(tmp_path):
    path = _transcript(tmp_path, [{"type": "user"}, _bridge_line("cse_01xyz")])
    assert bridge_session_id(path) == "cse_01xyz"


def test_bridge_session_id_last_record_wins(tmp_path):
    path = _transcript(tmp_path, [_bridge_line("cse_old"), _bridge_line("cse_new")])
    assert bridge_session_id(path) == "cse_new"


def test_bridge_session_id_rejects_unsafe_ids(tmp_path):
    # An id that could escape the URL path never comes back.
    path = _transcript(tmp_path, [_bridge_line("cse_bad/../../evil")])
    assert bridge_session_id(path) is None


def test_bridge_session_id_tolerates_junk(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text(
        'not json but mentions "bridge-session"\n'
        + json.dumps({"type": "bridge-session", "bridgeSessionId": 42})
        + "\n"
        + json.dumps(_bridge_line("cse_ok"))
        + "\n"
    )
    assert bridge_session_id(path) == "cse_ok"


def test_bridge_session_id_missing_file(tmp_path):
    assert bridge_session_id(tmp_path / "nope.jsonl") is None


# -- sync_archived -------------------------------------------------------------


@pytest.fixture
def credentials(tmp_path, monkeypatch):
    path = tmp_path / "credentials.json"
    path.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "tok-123",
                    "expiresAt": (time.time() + 3600) * 1000,
                    "subscriptionType": "max",
                }
            }
        )
    )
    monkeypatch.setenv("COLLINS_CLAUDE_CREDENTIALS", str(path))
    return path


class _Transport:
    def __init__(self, status: int = 200):
        self.status = status
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, headers: dict) -> int:
        self.calls.append((url, headers))
        return self.status


def test_sync_archives_the_bridge_session(tmp_path, credentials):
    path = _transcript(tmp_path, [_bridge_line("cse_01xyz")])
    transport = _Transport(200)
    assert sync_archived(path, True, transport=transport) is True
    (url, headers), = transport.calls
    assert url == "https://api.anthropic.com/v1/code/sessions/cse_01xyz/archive"
    assert headers["Authorization"] == "Bearer tok-123"
    assert headers["anthropic-version"] == "2023-06-01"


def test_sync_restore_hits_unarchive(tmp_path, credentials):
    path = _transcript(tmp_path, [_bridge_line("cse_01xyz")])
    transport = _Transport(200)
    assert sync_archived(path, False, transport=transport) is True
    assert transport.calls[0][0].endswith("/cse_01xyz/unarchive")


def test_sync_treats_409_as_done(tmp_path, credentials):
    # 409 is the endpoint's "already in that state" — as good as a 200.
    path = _transcript(tmp_path, [_bridge_line()])
    assert sync_archived(path, True, transport=_Transport(409)) is True


def test_sync_http_failure_is_swallowed(tmp_path, credentials):
    path = _transcript(tmp_path, [_bridge_line()])
    assert sync_archived(path, True, transport=_Transport(500)) is False


def test_sync_without_bridge_record_stays_offline(tmp_path, credentials):
    # The common case: never remote-controlled, so no request goes out.
    path = _transcript(tmp_path, [{"type": "user"}])
    transport = _Transport(200)
    assert sync_archived(path, True, transport=transport) is False
    assert transport.calls == []


def test_sync_without_credentials_is_swallowed(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLINS_CLAUDE_CREDENTIALS", str(tmp_path / "nope.json"))
    path = _transcript(tmp_path, [_bridge_line()])
    transport = _Transport(200)
    assert sync_archived(path, True, transport=transport) is False
    assert transport.calls == []


def test_sync_never_raises(tmp_path, credentials):
    path = _transcript(tmp_path, [_bridge_line()])

    def explode(url, headers):
        raise RuntimeError("boom")

    assert sync_archived(path, True, transport=explode) is False


def test_sync_async_empty_batch_spawns_nothing(monkeypatch):
    import collins.remotearchive as mod

    def no_threads(*args, **kwargs):  # pragma: no cover - the assertion is the point
        raise AssertionError("no thread should start for an empty batch")

    monkeypatch.setattr(mod.threading, "Thread", no_threads)
    sync_archived_async([], True)
