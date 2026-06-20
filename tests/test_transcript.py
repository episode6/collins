import json

from claude_session_manager.transcript import TranscriptModel


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
    m = TranscriptModel(p, "claude")
    assert m.update() is True
    pending = m.pending_question()
    assert pending is not None
    assert pending.tool_use_id == "q1"
    assert pending.questions[0]["question"] == "Which DB?"


def test_resolved_question_is_not_pending(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_question_line("q1"), _answer_line("q1")])
    m = TranscriptModel(p, "claude")
    m.update()
    assert m.pending_question() is None


def test_latest_unanswered_wins(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_question_line("q1"), _answer_line("q1"), _question_line("q2")])
    m = TranscriptModel(p, "claude")
    m.update()
    assert m.pending_question().tool_use_id == "q2"


def test_incremental_resolution(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_question_line("q1")])
    m = TranscriptModel(p, "claude")
    m.update()
    assert m.pending_question() is not None
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_answer_line("q1")) + "\n")
    assert m.update() is True
    assert m.pending_question() is None


def test_non_claude_provider_has_no_questions(tmp_path):
    # Cursor transcripts have no AskUserQuestion; the model stays empty.
    p = tmp_path / "c.jsonl"
    _write(p, [{"role": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}])
    m = TranscriptModel(p, "cursor")
    assert m.update() is False
    assert m.pending_question() is None


def test_no_change_when_nothing_new(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_question_line("q1")])
    m = TranscriptModel(p, "claude")
    assert m.update() is True
    assert m.update() is False  # no new bytes


def test_missing_file(tmp_path):
    m = TranscriptModel(tmp_path / "nope.jsonl", "claude")
    assert m.update() is False
    assert m.pending_question() is None
