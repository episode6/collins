import json

from collins.providers import BackgroundAgent, Provider
from collins.window import _match_background_fork


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
    assert _match_background_fork(provider, "old", "/proj", "u-1", set()) == ""


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
    assert _match_background_fork(provider, "old", "/proj", "u-1", set()) == "fork"


def test_match_falls_back_to_cwd_for_a_stub_fork(tmp_path):
    agents = [BackgroundAgent(session_id="fork", job_id="j1", cwd="/proj")]
    provider = FakeProvider(agents, {"fork": write_transcript(tmp_path, "fork", None)})
    # An idle background agent's transcript is a metadata stub with no messages
    # to compare, so the working directory is all there is to go on.
    assert _match_background_fork(provider, "old", "/proj", "u-1", set()) == "fork"


def test_match_skips_agents_that_predate_the_detach(tmp_path):
    agents = [BackgroundAgent(session_id="fork", job_id="j1", cwd="/proj")]
    provider = FakeProvider(agents, {"fork": write_transcript(tmp_path, "fork", None)})
    assert _match_background_fork(provider, "old", "/proj", "u-1", {"fork"}) is None


def test_match_rejects_a_same_cwd_agent_from_another_conversation(tmp_path):
    agents = [BackgroundAgent(session_id="fork", job_id="j1", cwd="/proj")]
    provider = FakeProvider(agents, {"fork": write_transcript(tmp_path, "fork", "u-other")})
    # Both uuids are known and they disagree: a shared working directory (two
    # tabs on the same project backgrounded at once) must not pair them.
    assert _match_background_fork(provider, "old", "/proj", "u-1", set()) is None


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
    assert _match_background_fork(provider, "old", "/proj", "", set(), unique_cwd=True) is None
    # One candidate is unambiguous.
    provider._agents = agents[:1]
    assert _match_background_fork(provider, "old", "/proj", "", set(), unique_cwd=True) == "f1"


def test_match_returns_none_without_a_cwd_to_compare():
    provider = FakeProvider([BackgroundAgent(session_id="fork", job_id="j1", cwd="/proj")])
    assert _match_background_fork(provider, "old", "", "", set()) is None
    assert _match_background_fork(provider, "old", None, None, set()) is None
