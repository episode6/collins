# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-01. Full change history: git log for this file.

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
    assert m.pull_requests() == []


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


def test_no_pr_link_means_no_prs(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_question_line("q1")])
    m = TranscriptModel(p)
    m.update()
    assert m.pull_requests() == []


def test_every_linked_pr_is_kept_oldest_first(tmp_path):
    """A session opens PRs one after another; the footer shows all of them, in
    the order they were opened."""
    p = tmp_path / "s.jsonl"
    _write(p, [_pr_line(40), _question_line("q1"), _pr_line(55), _pr_line(61)])
    m = TranscriptModel(p)
    m.update()
    prs = m.pull_requests()
    assert [pr.number for pr in prs] == [40, 55, 61]
    assert prs[0].repository == "episode6/collins"
    assert prs[2].url.endswith("/pull/61")


def test_pr_link_is_picked_up_incrementally(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_question_line("q1")])
    m = TranscriptModel(p)
    m.update()
    assert m.pull_requests() == []
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_pr_line(55)) + "\n")
    assert m.update() is True
    assert [pr.number for pr in m.pull_requests()] == [55]


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
    assert [pr.number for pr in m.pull_requests()] == [55]


def test_a_re_emitted_link_does_not_reorder_the_list(tmp_path):
    """Claude re-emits earlier links on resume/compact: the oldest PR must keep
    its place at the head of the row rather than jumping to the end."""
    p = tmp_path / "s.jsonl"
    _write(p, [_pr_line(40), _pr_line(55), _pr_line(40)])
    m = TranscriptModel(p)
    m.update()
    assert [pr.number for pr in m.pull_requests()] == [40, 55]


def test_set_path_clears_the_prs(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_pr_line(55)])
    m = TranscriptModel(p)
    m.update()
    m.set_path(tmp_path / "other.jsonl")
    assert m.pull_requests() == []


def test_truncated_transcript_clears_the_prs(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_pr_line(55)])
    m = TranscriptModel(p)
    m.update()
    _write(p, [{"type": "mode"}])  # rewritten shorter, no pr-link
    m.update()
    assert m.pull_requests() == []


# -- relocation (the CLI re-homes a transcript when the session changes dir) --


def _move(src, dst_dir, name):
    """What the CLI does on entering a worktree: same file, new project dir."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / name
    src.rename(dst)
    return dst


def test_relocate_keeps_what_was_already_parsed(tmp_path):
    old = tmp_path / "proj-repo" / "s.jsonl"
    old.parent.mkdir()
    _write(old, [_pr_line(40), _question_line("q1")])
    m = TranscriptModel(old)
    m.update()
    assert [pr.number for pr in m.pull_requests()] == [40]
    assert m.pending_question() is not None

    new = _move(old, tmp_path / "proj-repo-worktree", "s.jsonl")
    m.relocate(new)

    assert m.path == new
    # Nothing re-read yet, and nothing lost.
    assert [pr.number for pr in m.pull_requests()] == [40]
    assert m.pending_question() is not None


def test_relocate_reads_only_what_was_appended_after_the_move(tmp_path):
    old = tmp_path / "proj-repo" / "s.jsonl"
    old.parent.mkdir()
    _write(old, [_pr_line(40)])
    m = TranscriptModel(old)
    m.update()

    new = _move(old, tmp_path / "proj-repo-worktree", "s.jsonl")
    m.relocate(new)
    assert m.update() is False  # same bytes, nothing new to ingest

    _write(new, [_pr_line(40), _pr_line(55)])
    assert m.update() is True
    assert [pr.number for pr in m.pull_requests()] == [40, 55]


def test_relocate_keeps_tailing_across_the_move(tmp_path):
    """The regression: a session that moves keeps reporting new PRs."""
    old = tmp_path / "proj-repo" / "s.jsonl"
    old.parent.mkdir()
    _write(old, [_pr_line(40)])
    m = TranscriptModel(old)
    m.update()

    new = _move(old, tmp_path / "proj-repo-worktree", "s.jsonl")
    # Without relocate the model tails a path that no longer exists.
    assert m.update() is False
    assert m.path.exists() is False

    m.relocate(new)
    _write(new, [_pr_line(40), _pr_line(55)])
    assert m.update() is True
    assert [pr.number for pr in m.pull_requests()] == [40, 55]


def test_set_path_resets_where_relocate_does_not(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_pr_line(40)])
    m = TranscriptModel(p)
    m.update()
    assert [pr.number for pr in m.pull_requests()] == [40]

    m.set_path(p)  # a different session's file: start clean
    assert m.pull_requests() == []


def test_relocate_onto_a_shorter_file_starts_over(tmp_path):
    old = tmp_path / "proj-repo" / "s.jsonl"
    old.parent.mkdir()
    _write(old, [_pr_line(40), _pr_line(55), _pr_line(60)])
    m = TranscriptModel(old)
    m.update()
    assert [pr.number for pr in m.pull_requests()] == [40, 55, 60]

    new = tmp_path / "proj-repo-worktree" / "s.jsonl"
    new.parent.mkdir()
    _write(new, [_pr_line(77)])  # shorter than the read offset
    old.unlink()
    m.relocate(new)
    assert m.update() is True
    assert [pr.number for pr in m.pull_requests()] == [77]


def test_relocate_accepts_a_string_path(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_pr_line(40)])
    m = TranscriptModel(p)
    m.update()
    m.relocate(str(p))
    assert m.path == p


# -- agent-touched files -------------------------------------------------------


def _touch_line(tool, key, path):
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": tool,
                         "input": {key: path}}],
        },
    }


def test_touched_files_records_write_tools_newest_first(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [
        _touch_line("Write", "file_path", "/proj/a.py"),
        _touch_line("Edit", "file_path", "/proj/b.py"),
        _touch_line("NotebookEdit", "notebook_path", "/proj/c.ipynb"),
    ])
    m = TranscriptModel(p)
    assert m.update() is True
    assert m.touched_files() == ["/proj/c.ipynb", "/proj/b.py", "/proj/a.py"]


def test_touched_files_ignores_reads_and_other_tools(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [
        _touch_line("Read", "file_path", "/proj/read.py"),
        _touch_line("Glob", "path", "/proj"),
        _touch_line("Bash", "command", "ls"),
    ])
    m = TranscriptModel(p)
    m.update()
    assert m.touched_files() == []


def test_retouching_a_file_moves_it_to_the_front(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [
        _touch_line("Write", "file_path", "/proj/a.py"),
        _touch_line("Write", "file_path", "/proj/b.py"),
        _touch_line("Edit", "file_path", "/proj/a.py"),
    ])
    m = TranscriptModel(p)
    m.update()
    assert m.touched_files() == ["/proj/a.py", "/proj/b.py"]


def test_retouching_the_most_recent_file_is_not_a_change(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_touch_line("Edit", "file_path", "/proj/a.py")])
    m = TranscriptModel(p)
    m.update()
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_touch_line("Edit", "file_path", "/proj/a.py")) + "\n")
    assert m.update() is False
    assert m.touched_files() == ["/proj/a.py"]


def test_touched_files_capped_at_30(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_touch_line("Write", "file_path", f"/proj/f{i}.py") for i in range(40)])
    m = TranscriptModel(p)
    m.update()
    touched = m.touched_files()
    assert len(touched) == 30
    assert touched[0] == "/proj/f39.py"


def test_set_path_clears_touched_files(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_touch_line("Write", "file_path", "/proj/a.py")])
    m = TranscriptModel(p)
    m.update()
    m.set_path(None)
    assert m.touched_files() == []
