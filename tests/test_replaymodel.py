# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-26. Full change history: git log for this file.

import json

from claude_session_manager.replaymodel import read_session_turns


def _write(path, lines):
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")


def test_claude_full_turns_in_order(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [
        {"type": "mode"},  # noise, ignored
        {"type": "user", "message": {"role": "user", "content": "Build it"}},
        {"type": "user", "message": {"role": "user", "content": "<command-name>/exit</command-name>"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "..."},
            {"type": "text", "text": "On it."},
            {"type": "tool_use", "id": "t1", "name": "Edit", "input": {"file_path": "/a/b/main.py"}},
            {"type": "tool_use", "id": "q1", "name": "AskUserQuestion",
             "input": {"questions": [{"header": "DB", "question": "Which?", "options": [{"label": "PG"}]}]}},
        ]}},
        # tool_result user message → not a bubble
        {"type": "user", "message": {"role": "user",
         "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
    ])
    turns = read_session_turns(p)
    summary = [(t.role, t.kind, t.text or t.tool_name) for t in turns]
    assert summary == [
        ("user", "message", "Build it"),
        ("assistant", "message", "On it."),
        ("assistant", "tool", "Edit main.py"),   # tool summary
        ("assistant", "question", "AskUserQuestion"),
    ]
    assert turns[2].tool_name == "Edit"
    assert turns[3].questions[0]["question"] == "Which?"


def test_bash_tool_summary(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [{"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "b", "name": "Bash", "input": {"command": "ls -la /tmp"}}]}}])
    (turn,) = read_session_turns(p)
    assert turn.kind == "tool" and turn.text == "Bash: ls -la /tmp"


def test_noise_message_dropped(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [{"type": "assistant", "message": {"role": "assistant",
               "content": [{"type": "text", "text": "No response requested."}]}}])
    assert read_session_turns(p) == []


def test_missing_and_empty(tmp_path):
    assert read_session_turns(None) == []
    assert read_session_turns(tmp_path / "nope.jsonl") == []
