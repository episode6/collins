import io
import json

from claude_session_manager.chatsession import (
    ChatSession,
    StreamParser,
    make_chat_session,
)
from claude_session_manager.providers import ChatVariant


def _feed(parser, *entries):
    """Feed each dict as its own newline-terminated JSON line."""
    events = []
    for e in entries:
        events.extend(parser.feed((json.dumps(e) + "\n").encode()))
    return events


def test_init_captures_session_id():
    p = StreamParser()
    (ev,) = _feed(p, {"type": "system", "subtype": "init", "session_id": "abc-123"})
    assert ev.kind == "init"
    assert ev.session_id == "abc-123"


def test_streaming_text_deltas():
    p = StreamParser()
    events = _feed(
        p,
        {"type": "stream_event", "event": {"type": "content_block_start", "index": 0,
                                           "content_block": {"type": "text", "text": ""}}},
        {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "text_delta", "text": "hi "}}},
        {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "text_delta", "text": "there"}}},
    )
    texts = [e.text for e in events if e.kind == "text"]
    assert "".join(texts) == "hi there"


def test_thinking_delta():
    p = StreamParser()
    events = _feed(p, {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                       "delta": {"type": "thinking_delta", "thinking": "hmm"}}})
    assert [e.text for e in events if e.kind == "thinking"] == ["hmm"]


def test_tool_use_chip():
    p = StreamParser()
    events = _feed(p, {"type": "stream_event", "event": {"type": "content_block_start", "index": 1,
                       "content_block": {"type": "tool_use", "id": "t1", "name": "Read", "input": {}}}})
    tools = [e for e in events if e.kind == "tool"]
    assert len(tools) == 1 and tools[0].tool_name == "Read"


def test_rate_limit_event():
    p = StreamParser()
    (ev,) = _feed(p, {"type": "rate_limit_event",
                      "rate_limit_info": {"status": "allowed", "resetsAt": 1781283600,
                                          "rateLimitType": "five_hour"}})
    assert ev.kind == "rate_limit"
    assert ev.rate_status == "allowed"
    assert ev.resets_at == 1781283600


def test_result_turn_end():
    p = StreamParser()
    (ev,) = _feed(p, {"type": "result", "subtype": "success", "is_error": False,
                      "result": "hi there friend", "total_cost_usd": 0.0489})
    assert ev.kind == "turn_end"
    assert ev.text == "hi there friend"
    assert round(ev.cost_usd, 4) == 0.0489


def test_result_error():
    p = StreamParser()
    (ev,) = _feed(p, {"type": "result", "subtype": "error", "is_error": True,
                      "result": "rate limited"})
    assert ev.kind == "error"
    assert "rate limited" in ev.text


def test_partial_line_buffering():
    p = StreamParser()
    line = json.dumps({"type": "system", "subtype": "init", "session_id": "x"}) + "\n"
    # split mid-line across two feeds → exactly one event, only once the newline arrives
    assert p.feed(line[:10].encode()) == []
    events = p.feed(line[10:].encode())
    assert len(events) == 1 and events[0].kind == "init"


def test_permission_request_parsed():
    p = StreamParser()
    (ev,) = _feed(p, {
        "type": "control_request",
        "request_id": "rid-1",
        "request": {
            "subtype": "can_use_tool",
            "tool_name": "Write",
            "display_name": "Write",
            "input": {"file_path": "/a/b.txt", "content": "hi"},
            "description": "b.txt",
            "tool_use_id": "toolu_9",
        },
    })
    assert ev.kind == "permission"
    assert ev.request_id == "rid-1"
    assert ev.tool_name == "Write"
    assert ev.tool_input == {"file_path": "/a/b.txt", "content": "hi"}
    assert ev.text == "b.txt"
    assert ev.tool_use_id == "toolu_9"


def test_other_control_requests_ignored():
    p = StreamParser()
    req = {"type": "control_request", "request_id": "r", "request": {"subtype": "initialize"}}
    assert _feed(p, req) == []


class _FakeProc:
    """Minimal Popen stand-in capturing what gets written to stdin."""

    def __init__(self):
        self.stdin = io.BytesIO()
        self.returncode = None

    def poll(self):
        return self.returncode


def _session_with_proc():
    sess = ChatSession.__new__(ChatSession)
    sess._proc = _FakeProc()
    return sess


def test_respond_permission_allow_envelope():
    sess = _session_with_proc()
    sess.respond_permission("rid-1", True, {"file_path": "/a", "content": "x"})
    sent = json.loads(sess._proc.stdin.getvalue().decode())
    assert sent == {
        "type": "control_response",
        "response": {
            "subtype": "success",
            "request_id": "rid-1",
            "response": {"behavior": "allow", "updatedInput": {"file_path": "/a", "content": "x"}},
        },
    }


def test_respond_permission_deny_envelope():
    sess = _session_with_proc()
    sess.respond_permission("rid-2", False, {"x": 1}, message="nope")
    sent = json.loads(sess._proc.stdin.getvalue().decode())
    assert sent["response"]["response"] == {"behavior": "deny", "message": "nope"}


def test_resume_seeds_session_id():
    # The session knows the id before its init event, so the first turn's
    # --resume targets the existing session.
    cv = ChatVariant(key="default")
    csess = make_chat_session(object(), cv, None, lambda ev: None, "claude-id")
    assert isinstance(csess, ChatSession)
    assert csess.session_id == "claude-id"


def test_unknown_and_status_lines_ignored():
    p = StreamParser()
    events = _feed(
        p,
        {"type": "system", "subtype": "status", "status": "requesting"},
        {"type": "stream_event", "event": {"type": "message_start", "message": {}}},
        {"type": "stream_event", "event": {"type": "message_stop"}},
    )
    assert events == []
