# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-28. Full change history: git log for this file.

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
    assert m.current_pr() is None


# -- pr-link records --------------------------------------------------------


def _pr_line(number, repo="episode6/collins"):
    return {
        "type": "pr-link",
        "sessionId": "s1",
        "prNumber": number,
        "prUrl": f"https://github.com/{repo}/pull/{number}",
        "prRepository": repo,
        "timestamp": "2026-07-27T00:43:57.325Z",
    }


def test_no_pr_link_means_no_pr(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_question_line("q1")])
    m = TranscriptModel(p)
    m.update()
    assert m.current_pr() is None


def test_last_pr_link_wins(tmp_path):
    """Claude re-emits pr-link on resume/compact, and a session can move to a
    second PR; the most recent record is the one that counts."""
    p = tmp_path / "s.jsonl"
    _write(p, [_pr_line(40), _question_line("q1"), _pr_line(55)])
    m = TranscriptModel(p)
    m.update()
    pr = m.current_pr()
    assert (pr.number, pr.repository) == (55, "episode6/collins")
    assert pr.url.endswith("/pull/55")


def test_pr_link_is_picked_up_incrementally(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_question_line("q1")])
    m = TranscriptModel(p)
    m.update()
    assert m.current_pr() is None
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_pr_line(55)) + "\n")
    assert m.update() is True
    assert m.current_pr().number == 55


def test_repeated_pr_link_is_not_a_change(tmp_path):
    """The same link re-emitted must not report a change — it would retrigger
    footer work on every poll for the rest of the session."""
    p = tmp_path / "s.jsonl"
    _write(p, [_pr_line(55)])
    m = TranscriptModel(p)
    assert m.update() is True
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_pr_line(55)) + "\n")
    assert m.update() is False
    assert m.current_pr().number == 55


def test_set_path_clears_the_pr(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_pr_line(55)])
    m = TranscriptModel(p)
    m.update()
    m.set_path(tmp_path / "other.jsonl")
    assert m.current_pr() is None


def test_truncated_transcript_clears_the_pr(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_pr_line(55)])
    m = TranscriptModel(p)
    m.update()
    _write(p, [{"type": "mode"}])  # rewritten shorter, no pr-link
    m.update()
    assert m.current_pr() is None
