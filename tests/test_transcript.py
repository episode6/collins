# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-26. Full change history: git log for this file.

import json

from collins.transcript import TranscriptModel


def _write(path, lines):
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")


def _question_line(qid):
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me ask."},
                {
                    "type": "tool_use",
                    "id": qid,
                    "name": "AskUserQuestion",
                    "input": {"questions": [{"question": "Which DB?", "header": "DB",
                                             "multiSelect": False,
                                             "options": [{"label": "Postgres"}, {"label": "SQLite"}]}]},
                },
            ],
        },
    }


def _answer_line(qid):
    return {"type": "user", "message": {"role": "user",
            "content": [{"type": "tool_result", "tool_use_id": qid, "content": "ok"}]}}


def test_detects_pending_question(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_question_line("q1")])
    m = TranscriptModel(p)
    assert m.update() is True
    pending = m.pending_question()
    assert pending is not None
    assert pending.tool_use_id == "q1"
    assert pending.questions[0]["question"] == "Which DB?"


def test_resolved_question_is_not_pending(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_question_line("q1"), _answer_line("q1")])
    m = TranscriptModel(p)
    m.update()
    assert m.pending_question() is None


def test_latest_unanswered_wins(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_question_line("q1"), _answer_line("q1"), _question_line("q2")])
    m = TranscriptModel(p)
    m.update()
    assert m.pending_question().tool_use_id == "q2"


def test_incremental_resolution(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_question_line("q1")])
    m = TranscriptModel(p)
    m.update()
    assert m.pending_question() is not None
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_answer_line("q1")) + "\n")
    assert m.update() is True
    assert m.pending_question() is None


def test_no_change_when_nothing_new(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_question_line("q1")])
    m = TranscriptModel(p)
    assert m.update() is True
    assert m.update() is False  # no new bytes


def test_missing_file(tmp_path):
    m = TranscriptModel(tmp_path / "nope.jsonl")
    assert m.update() is False
    assert m.pending_question() is None
