# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-15. Full change history: git log for this file.

import json

from collins.transcript import TranscriptModel


def _write(path, lines):
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")


def test_no_change_when_nothing_new(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_touch_line("Write", "file_path", "/proj/a.py")])
    m = TranscriptModel(p)
    assert m.update() is True
    assert m.update() is False  # no new bytes


def test_missing_file(tmp_path):
    m = TranscriptModel(tmp_path / "nope.jsonl")
    assert m.update() is False
    assert m.touched_files() == []
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
    _write(p, [_touch_line("Write", "file_path", "/proj/a.py")])
    m = TranscriptModel(p)
    m.update()
    assert m.pull_requests() == []


def test_every_linked_pr_is_kept_oldest_first(tmp_path):
    """A session opens PRs one after another; the footer shows all of them, in
    the order they were opened."""
    p = tmp_path / "s.jsonl"
    _write(p, [_pr_line(40), _touch_line("Write", "file_path", "/p/a.py"),
               _pr_line(55), _pr_line(61)])
    m = TranscriptModel(p)
    m.update()
    prs = m.pull_requests()
    assert [pr.number for pr in prs] == [40, 55, 61]
    assert prs[0].repository == "episode6/collins"
    assert prs[2].url.endswith("/pull/61")


def test_pr_link_is_picked_up_incrementally(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_touch_line("Write", "file_path", "/proj/a.py")])
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
    _write(old, [_pr_line(40), _touch_line("Write", "file_path", "/proj/a.py")])
    m = TranscriptModel(old)
    m.update()
    assert [pr.number for pr in m.pull_requests()] == [40]
    assert m.touched_files() == ["/proj/a.py"]

    new = _move(old, tmp_path / "proj-repo-worktree", "s.jsonl")
    m.relocate(new)

    assert m.path == new
    # Nothing re-read yet, and nothing lost.
    assert [pr.number for pr in m.pull_requests()] == [40]
    assert m.touched_files() == ["/proj/a.py"]


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


# -- which model is answering --------------------------------------------------


def _reply_line(model, text="ok", sidechain=False):
    line = {
        "type": "assistant",
        "message": {"role": "assistant", "model": model,
                    "content": [{"type": "text", "text": text}]},
    }
    if sidechain:
        line["isSidechain"] = True
    return line


def test_no_reply_means_no_model(tmp_path):
    """A session that hasn't answered yet names no model — the footer has
    nothing to show until it does."""
    p = tmp_path / "s.jsonl"
    _write(p, [{"type": "user", "message": {"role": "user", "content": "hi"}}])
    m = TranscriptModel(p)
    m.update()
    assert m.model() is None


def test_model_comes_from_the_latest_reply(tmp_path):
    """A session can switch models mid-run; what it is answering with now is
    what the footer says."""
    p = tmp_path / "s.jsonl"
    _write(p, [_reply_line("claude-opus-5"), _reply_line("claude-sonnet-5")])
    m = TranscriptModel(p)
    m.update()
    assert m.model() == "claude-sonnet-5"


def test_a_switch_is_a_change_but_the_same_model_is_not(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_reply_line("claude-opus-5")])
    m = TranscriptModel(p)
    assert m.update() is True
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_reply_line("claude-opus-5")) + "\n")
    assert m.update() is False  # every turn repeats it; that is not news
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_reply_line("claude-sonnet-5")) + "\n")
    assert m.update() is True
    assert m.model() == "claude-sonnet-5"


def test_synthetic_replies_do_not_count(tmp_path):
    """The CLI's own interjections (API errors, interrupts) are stamped
    <synthetic>: no model answered them, so the real one stands."""
    p = tmp_path / "s.jsonl"
    _write(p, [_reply_line("claude-opus-5"), _reply_line("<synthetic>", "API Error")])
    m = TranscriptModel(p)
    m.update()
    assert m.model() == "claude-opus-5"


def test_subagent_replies_do_not_count(tmp_path):
    """A Haiku search agent runs inside an Opus session; the session is still
    on Opus."""
    p = tmp_path / "s.jsonl"
    _write(p, [_reply_line("claude-opus-5"),
               _reply_line("claude-haiku-4-5-20251001", sidechain=True)])
    m = TranscriptModel(p)
    m.update()
    assert m.model() == "claude-opus-5"


def test_one_line_can_carry_both_a_model_and_a_touch(tmp_path):
    """Tool-use turns are stamped with a model like any other reply; the same
    pass reads both out of them."""
    p = tmp_path / "s.jsonl"
    line = _touch_line("Write", "file_path", "/proj/a.py")
    line["message"]["model"] = "claude-opus-5"
    _write(p, [line])
    m = TranscriptModel(p)
    m.update()
    assert m.model() == "claude-opus-5"
    assert m.touched_files() == ["/proj/a.py"]


def test_set_path_clears_the_model(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_reply_line("claude-opus-5")])
    m = TranscriptModel(p)
    m.update()
    m.set_path(tmp_path / "other.jsonl")
    assert m.model() is None


def test_relocate_keeps_the_model(tmp_path):
    """Entering a worktree moves the file, not the session."""
    old = tmp_path / "proj-repo" / "s.jsonl"
    old.parent.mkdir()
    _write(old, [_reply_line("claude-opus-5")])
    m = TranscriptModel(old)
    m.update()
    m.relocate(_move(old, tmp_path / "proj-repo-worktree", "s.jsonl"))
    assert m.model() == "claude-opus-5"


# -- images the conversation named ------------------------------------------


def _said(text, *, role="assistant", cwd=None, at=None, sidechain=False):
    line = {
        "type": role,
        "message": {"role": role, "content": [{"type": "text", "text": text}]},
    }
    if cwd:
        line["cwd"] = cwd
    if at:
        line["timestamp"] = at
    if sidechain:
        line["isSidechain"] = True
    return line


def _png(tmp_path, name):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG")
    return path


def test_images_named_in_either_side_of_the_conversation_are_listed(tmp_path):
    mine = _png(tmp_path, "mine.png")
    theirs = _png(tmp_path, "theirs.png")
    p = tmp_path / "s.jsonl"
    _write(p, [
        _said(f"look at {mine}", role="user"),
        _said(f"and here's {theirs}"),
    ])
    m = TranscriptModel(p)
    assert m.update() is True
    assert {one.key for one in m.attachments()} == {str(mine), str(theirs)}


def test_a_mentioned_image_carries_its_line_as_context(tmp_path):
    shot = _png(tmp_path, "shot.png")
    p = tmp_path / "s.jsonl"
    _write(p, [_said(f"the toolbar is off by a pixel: {shot}")])
    m = TranscriptModel(p)
    m.update()
    (one,) = m.attachments()
    assert one.context == "the toolbar is off by a pixel:"
    assert one.caption is None


def test_a_relative_mention_resolves_against_the_message_cwd(tmp_path):
    shot = _png(tmp_path, "docs/shot.png")
    p = tmp_path / "s.jsonl"
    _write(p, [_said("see docs/shot.png", cwd=str(tmp_path))])
    m = TranscriptModel(p)
    m.update()
    assert [one.key for one in m.attachments()] == [str(shot)]


def test_a_mention_is_dated_by_the_message_that_made_it(tmp_path):
    """A transcript is read from byte 0, so a morning's worth of messages
    lands in one poll: stamping them `now` would file them all together."""
    from datetime import datetime

    shot = _png(tmp_path, "shot.png")
    p = tmp_path / "s.jsonl"
    _write(p, [_said(f"{shot} here", at="2026-06-01T10:00:00.000Z")])
    m = TranscriptModel(p)
    m.update()
    (one,) = m.attachments()
    assert one.at == one.last == datetime.fromisoformat("2026-06-01T10:00:00+00:00").timestamp()


def test_tool_input_and_output_are_not_scanned(tmp_path):
    """The bulk of a transcript is listings and diffs; a gallery of the
    images a conversation was about can't be drawn from those."""
    _png(tmp_path, "a.png")
    p = tmp_path / "s.jsonl"
    _write(p, [
        _touch_line("Write", "file_path", str(tmp_path / "a.png")),
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": f"{tmp_path}/a.png"},
        ]}},
    ])
    m = TranscriptModel(p)
    m.update()
    assert m.attachments() == []


def test_a_subagents_images_are_its_own(tmp_path):
    shot = _png(tmp_path, "shot.png")
    p = tmp_path / "s.jsonl"
    _write(p, [_said(f"found {shot}", sidechain=True)])
    m = TranscriptModel(p)
    m.update()
    assert m.attachments() == []


def test_a_re_mentioned_image_is_not_a_change(tmp_path):
    """Repeating a path must not report a change on every poll — it would
    have the tab rewriting state.json for the rest of the session."""
    shot = _png(tmp_path, "shot.png")
    p = tmp_path / "s.jsonl"
    _write(p, [_said(f"{shot} again", at="2026-06-01T10:00:00.000Z")])
    m = TranscriptModel(p)
    assert m.update() is True
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_said(f"{shot} again", at="2026-06-01T10:00:00.000Z")) + "\n")
    assert m.update() is False


def test_a_later_mention_moves_an_image_up_the_list(tmp_path):
    a, b = _png(tmp_path, "a.png"), _png(tmp_path, "b.png")
    p = tmp_path / "s.jsonl"
    _write(p, [
        _said(f"{a}", at="2026-06-01T10:00:00.000Z"),
        _said(f"{b}", at="2026-06-01T10:05:00.000Z"),
        _said(f"{a} once more", at="2026-06-01T10:09:00.000Z"),
    ])
    m = TranscriptModel(p)
    m.update()
    assert [one.key for one in m.attachments()] == [str(a), str(b)]


def _sent_line(files, caption=None, *, cwd=None, sidechain=False):
    inp = {"files": files, "status": "normal"}
    if caption is not None:
        inp["caption"] = caption
    line = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "SendUserFile",
                         "input": inp}],
        },
    }
    if cwd:
        line["cwd"] = cwd
    if sidechain:
        line["isSidechain"] = True
    return line


def test_images_sent_with_senduserfile_are_listed_with_their_caption(tmp_path):
    """An agent that hands the user pictures without naming them in prose
    still fills the panel: the tool call is the only place the paths and
    the caption ever appear."""
    dark = _png(tmp_path, "sheet-dark.png")
    light = _png(tmp_path, "sheet-light.png")
    p = tmp_path / "s.jsonl"
    _write(p, [_sent_line([str(dark), str(light)], "icon candidates")])
    m = TranscriptModel(p)
    assert m.update() is True
    by_key = {one.key: one for one in m.attachments()}
    assert set(by_key) == {str(dark), str(light)}
    assert all(one.caption == "icon candidates" for one in by_key.values())
    assert all(one.source == "lightbox" for one in by_key.values())


def test_a_sent_non_image_is_not_listed(tmp_path):
    report = tmp_path / "report.txt"
    report.write_text("words")
    p = tmp_path / "s.jsonl"
    _write(p, [_sent_line([str(report)])])
    m = TranscriptModel(p)
    m.update()
    assert m.attachments() == []


def test_a_sent_relative_path_resolves_against_the_message_cwd(tmp_path):
    shot = _png(tmp_path, "out/shot.png")
    p = tmp_path / "s.jsonl"
    _write(p, [_sent_line(["out/shot.png"], cwd=str(tmp_path))])
    m = TranscriptModel(p)
    m.update()
    assert [one.key for one in m.attachments()] == [str(shot)]


def test_a_sent_file_that_is_gone_is_not_listed(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_sent_line([str(tmp_path / "never-was.png")])])
    m = TranscriptModel(p)
    m.update()
    assert m.attachments() == []


def test_a_subagents_sent_images_are_its_own(tmp_path):
    shot = _png(tmp_path, "shot.png")
    p = tmp_path / "s.jsonl"
    _write(p, [_sent_line([str(shot)], sidechain=True)])
    m = TranscriptModel(p)
    m.update()
    assert m.attachments() == []


def test_a_garbled_senduserfile_call_is_ignored(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [
        _sent_line("not-a-list"),
        _sent_line([None, 7, {}]),
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "SendUserFile", "input": None},
        ]}},
    ])
    m = TranscriptModel(p)
    m.update()
    assert m.attachments() == []


def test_set_path_clears_the_images(tmp_path):
    shot = _png(tmp_path, "shot.png")
    p = tmp_path / "s.jsonl"
    _write(p, [_said(f"{shot}")])
    m = TranscriptModel(p)
    m.update()
    m.set_path(tmp_path / "other.jsonl")
    assert m.attachments() == []
