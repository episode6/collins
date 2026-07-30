import json
import threading

import pytest

from collins import bgstatus
from collins.bgstatus import (
    BackgroundStatusPoller,
    fetch_background_ids,
    match_background_fork,
)
from collins.providers import BackgroundAgent, ClaudeProvider, Provider


class FakeThread:
    """Captures thread targets instead of running them, so tests drive the
    fetch → _apply flow synchronously."""

    started: list["FakeThread"] = []

    def __init__(self, target=None, daemon=None) -> None:
        self.target = target

    def start(self) -> None:
        FakeThread.started.append(self)


@pytest.fixture
def fake_threads(monkeypatch):
    FakeThread.started = []
    monkeypatch.setattr(threading, "Thread", FakeThread)
    return FakeThread.started


def test_apply_diffs_sets_and_notifies():
    changes = []
    poller = BackgroundStatusPoller(fetch=set, on_change=changes.append)
    poller._apply({"a", "b"})
    assert poller.background_ids == {"a", "b"}
    assert changes == [{"a", "b"}]
    # Symmetric difference: departed ids re-sync too, not just arrivals.
    poller._apply({"b", "c"})
    assert poller.background_ids == {"b", "c"}
    assert changes[-1] == {"a", "c"}


def test_apply_unchanged_set_does_not_notify():
    changes = []
    poller = BackgroundStatusPoller(fetch=set, on_change=changes.append)
    poller._apply({"a"})
    poller._apply({"a"})
    assert changes == [{"a"}]


def test_apply_none_keeps_last_known_ids():
    # A fetch that raised reports None: keep the last-known set rather than
    # clearing every dot on a transient failure.
    changes = []
    poller = BackgroundStatusPoller(fetch=set, on_change=changes.append)
    poller._apply({"a"})
    poller._apply(None)
    assert poller.background_ids == {"a"}
    assert changes == [{"a"}]


def test_refresh_coalesces_concurrent_requests(fake_threads):
    poller = BackgroundStatusPoller(fetch=lambda: {"a"})
    poller.refresh()
    poller.refresh()  # lands while the first fetch is "in flight"
    poller.refresh()
    assert len(fake_threads) == 1
    # Completing the in-flight fetch runs exactly one queued follow-up.
    poller._apply({"a"})
    assert len(fake_threads) == 2
    poller._apply({"a"})
    assert len(fake_threads) == 2


def test_refresh_after_stop_is_a_noop(fake_threads):
    poller = BackgroundStatusPoller(fetch=set)
    poller.stop()
    poller.refresh()
    assert fake_threads == []


def test_set_polling_starts_once_and_stops(fake_threads):
    poller = BackgroundStatusPoller(fetch=set)
    poller.set_polling(True)
    poller.set_polling(True)  # idempotent: one timer, one refresh
    assert len(fake_threads) == 1
    assert poller._poll_source is not None
    poller.set_polling(False)
    assert poller._poll_source is None
    poller.stop()


def test_fetch_background_ids_unions_providers(monkeypatch):
    monkeypatch.setattr(
        bgstatus,
        "available_providers",
        lambda: [ClaudeProvider()],
    )
    monkeypatch.setattr(
        ClaudeProvider,
        "background_agents",
        lambda self: [
            BackgroundAgent(session_id="s1", job_id="j1", cwd="/p"),
            BackgroundAgent(session_id="s2", job_id="j2", cwd="/q"),
        ],
    )
    assert fetch_background_ids() == {"s1", "s2"}


# -- pairing a backgrounded session with its agent ----------------------------


class FakeProvider(Provider):
    """A provider whose agent list and transcripts are handed to it."""

    id = "fake"

    def __init__(self, agents, transcripts=None):
        self._agents = agents
        self._transcripts = transcripts or {}  # session id -> path

    def background_agents(self):
        return self._agents

    def transcripts_for_cwd(self, cwd):
        return [p for sid, p in self._transcripts.items() if sid in self._by_cwd(cwd)]

    def _by_cwd(self, cwd):
        return {a.session_id for a in self._agents if a.cwd == cwd}


def write_transcript(tmp_path, session_id: str, uuid: str | None) -> "object":
    """A fork's transcript: a real conversation copy, or the metadata-only stub
    the CLI leaves for a background agent that hasn't done any work yet."""
    path = tmp_path / f"{session_id}.jsonl"
    if uuid is None:
        lines = [{"type": "ai-title", "aiTitle": "T", "sessionId": session_id}]
    else:
        lines = [
            {"type": "user", "uuid": uuid, "cwd": "/proj", "message": {"content": "hi"}},
        ]
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return path


def test_match_detects_an_in_place_detach():
    provider = FakeProvider([BackgroundAgent(session_id="old", job_id="j1", cwd="/proj")])
    # The session's own id in the agent list means it detached in place, so
    # there is no fork to record.
    assert match_background_fork(provider, "old", "/proj", "u-1", set()) == ""


def test_match_pairs_a_fork_by_first_message_uuid(tmp_path):
    agents = [
        BackgroundAgent(session_id="other", job_id="j1", cwd="/proj"),
        BackgroundAgent(session_id="fork", job_id="j2", cwd="/proj"),
    ]
    provider = FakeProvider(
        agents,
        {
            "other": write_transcript(tmp_path, "other", "u-other"),
            "fork": write_transcript(tmp_path, "fork", "u-1"),
        },
    )
    # /bg copies the conversation verbatim, uuids included — so the matching
    # first message identifies the fork even among same-cwd candidates.
    assert match_background_fork(provider, "old", "/proj", "u-1", set()) == "fork"


def test_match_falls_back_to_cwd_for_a_stub_fork(tmp_path):
    agents = [BackgroundAgent(session_id="fork", job_id="j1", cwd="/proj")]
    provider = FakeProvider(agents, {"fork": write_transcript(tmp_path, "fork", None)})
    # An idle background agent's transcript is a metadata stub with no messages
    # to compare, so the working directory is all there is to go on.
    assert match_background_fork(provider, "old", "/proj", "u-1", set()) == "fork"


def test_match_skips_agents_that_predate_the_detach(tmp_path):
    agents = [BackgroundAgent(session_id="fork", job_id="j1", cwd="/proj")]
    provider = FakeProvider(agents, {"fork": write_transcript(tmp_path, "fork", None)})
    assert match_background_fork(provider, "old", "/proj", "u-1", {"fork"}) is None


def test_match_rejects_a_same_cwd_agent_from_another_conversation(tmp_path):
    agents = [BackgroundAgent(session_id="fork", job_id="j1", cwd="/proj")]
    provider = FakeProvider(agents, {"fork": write_transcript(tmp_path, "fork", "u-other")})
    # Both uuids are known and they disagree: a shared working directory (two
    # tabs on the same project backgrounded at once) must not pair them.
    assert match_background_fork(provider, "old", "/proj", "u-1", set()) is None


def test_match_declines_ambiguous_cwd_when_strict(tmp_path):
    agents = [
        BackgroundAgent(session_id="f1", job_id="j1", cwd="/proj"),
        BackgroundAgent(session_id="f2", job_id="j2", cwd="/proj"),
    ]
    provider = FakeProvider(
        agents,
        {
            "f1": write_transcript(tmp_path, "f1", None),
            "f2": write_transcript(tmp_path, "f2", None),
        },
    )
    # Replaying a pending detach after a restart has no "agents that predate
    # the /bg" set to narrow things down, so two stub candidates in the same
    # directory are a coin flip — and a wrong pairing would hide a good row
    # and point it at someone else's agent.
    assert match_background_fork(provider, "old", "/proj", "", set(), unique_cwd=True) is None
    # One candidate is unambiguous.
    provider._agents = agents[:1]
    assert match_background_fork(provider, "old", "/proj", "", set(), unique_cwd=True) == "f1"


def test_match_skips_an_agent_another_row_already_claims(tmp_path):
    agents = [
        BackgroundAgent(session_id="claimed", job_id="j1", cwd="/proj"),
        BackgroundAgent(session_id="fork", job_id="j2", cwd="/proj"),
    ]
    provider = FakeProvider(
        agents,
        {
            "claimed": write_transcript(tmp_path, "claimed", None),
            "fork": write_transcript(tmp_path, "fork", None),
        },
    )
    # Replaying a pending detach passes the agents other sessions already
    # forward to: one of two same-cwd stubs is spoken for, which leaves the
    # other unambiguous.
    assert (
        match_background_fork(provider, "old", "/proj", "", {"claimed"}, unique_cwd=True)
        == "fork"
    )


def test_match_returns_none_without_a_cwd_to_compare():
    provider = FakeProvider([BackgroundAgent(session_id="fork", job_id="j1", cwd="/proj")])
    assert match_background_fork(provider, "old", "", "", set()) is None
    assert match_background_fork(provider, "old", None, None, set()) is None
