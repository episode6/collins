# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-26. Full change history: git log for this file.

import json

import pytest

from collins.sessions import (
    configured_mcp_servers,
    discover_sessions,
    export_markdown,
    first_message_uuid,
    parse_details,
    read_mcp_config,
    resume_cwd,
    session_from_file,
)


def test_session_from_file_claude(tmp_path):
    p = tmp_path / "abc.jsonl"
    p.write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}, "cwd": "/proj"})
        + "\n",
        encoding="utf-8",
    )
    session = session_from_file(p)
    assert session is not None
    assert session.session_id == "abc"
    assert session.provider == "claude"  # detected from top-level "type"
    assert session.cwd == "/proj"
    assert session.created == session.mtime  # no timestamps -> mtime fallback


def test_session_from_file_missing(tmp_path):
    assert session_from_file(tmp_path / "nope.jsonl") is None


@pytest.fixture(autouse=True)
def _isolate_claude(monkeypatch):
    """These are Claude-discovery tests: force Claude available regardless of PATH."""
    import collins.providers as providers_mod

    monkeypatch.setattr(providers_mod.ClaudeProvider, "available", lambda self: True)


def test_discover_finds_only_real_sessions(projects_dir):
    root, ids = projects_dir
    sessions = discover_sessions()
    assert len(sessions) == 3  # noise files excluded
    assert {s.session_id for s in sessions} == set(ids.values())


def test_discover_skips_title_scratch_project(projects_dir, app_state):
    # Headless title-generation runs write transcripts under the scratch
    # project; they must never surface as sessions (that would re-trigger
    # titling forever).
    import json as _json
    import uuid as _uuid

    from conftest import make_transcript_lines

    from collins.titles import scratch_dir, scratch_project_dirname

    root, ids = projects_dir
    scratch_project = root / scratch_project_dirname()
    scratch_project.mkdir()
    sid = str(_uuid.uuid4())
    lines = make_transcript_lines(str(scratch_dir()), "Summarize the following coding-agent prompt")
    (scratch_project / f"{sid}.jsonl").write_text(
        "\n".join(_json.dumps(line) for line in lines), encoding="utf-8"
    )
    assert {s.session_id for s in discover_sessions()} == set(ids.values())


def test_discover_skips_metadata_only_stubs(projects_dir):
    # Claude's worktree agent runs leave transcripts holding only ai-title /
    # agent-name lines: no cwd, no user message. They can't be resumed and
    # would otherwise surface phantom projects named after the worktree path.
    import json as _json
    import uuid as _uuid

    root, ids = projects_dir
    stub_project = root / "-home-user-alpha--claude-worktrees-some-branch"
    stub_project.mkdir()
    sid = str(_uuid.uuid4())
    lines = [
        {"type": "ai-title", "aiTitle": "Some title", "sessionId": sid},
        {"type": "agent-name", "agentName": "Some title", "sessionId": sid},
    ]
    (stub_project / f"{sid}.jsonl").write_text(
        "\n".join(_json.dumps(line) for line in lines), encoding="utf-8"
    )
    assert {s.session_id for s in discover_sessions()} == set(ids.values())


def test_discover_keeps_session_with_preview_but_no_cwd(projects_dir):
    # Only fully-metadata stubs are dropped: a transcript with a real user
    # message still surfaces even if no line carries a cwd.
    import json as _json
    import uuid as _uuid

    root, ids = projects_dir
    sid = str(_uuid.uuid4())
    lines = [
        {
            "type": "user",
            "timestamp": "2026-06-01T10:00:00.000Z",
            "message": {"role": "user", "content": "Do the thing"},
        },
    ]
    (root / "-home-user-alpha" / f"{sid}.jsonl").write_text(
        "\n".join(_json.dumps(line) for line in lines), encoding="utf-8"
    )
    assert sid in {s.session_id for s in discover_sessions()}


def test_discover_extracts_cwd_and_preview(projects_dir):
    _root, ids = projects_dir
    by_id = {s.session_id: s for s in discover_sessions()}
    alpha = by_id[ids["alpha1"]]
    assert alpha.cwd == "/home/user/alpha"
    assert alpha.preview == "Build the alpha feature"
    assert alpha.project_name == "alpha"


def test_discover_extracts_created_timestamp(projects_dir):
    from datetime import datetime

    expected = datetime.fromisoformat("2026-06-01T10:00:00+00:00").timestamp()
    for session in discover_sessions():
        assert session.created == expected


def test_discover_sorted_newest_first(projects_dir):
    sessions = discover_sessions()
    mtimes = [s.mtime for s in sessions]
    assert mtimes == sorted(mtimes, reverse=True)


def test_parse_details_counts(projects_dir):
    _root, ids = projects_dir
    session = next(s for s in discover_sessions() if s.session_id == ids["alpha1"])
    details = parse_details(session.jsonl_path)
    assert details.user_messages == 1  # tool_result entry is not a user message
    assert details.assistant_messages == 1
    assert details.tool_calls == 1
    assert details.models == ["claude-opus-4-8"]
    assert details.input_tokens == 100
    assert details.output_tokens == 50
    assert details.cache_read_tokens == 2000
    assert details.first_timestamp == "2026-06-01T10:00:00.000Z"
    assert details.last_timestamp == "2026-06-01T10:01:00.000Z"
    assert details.file_size > 0


def test_parse_details_collects_recent_messages(projects_dir):
    _root, ids = projects_dir
    session = next(s for s in discover_sessions() if s.session_id == ids["alpha1"])
    details = parse_details(session.jsonl_path)
    # First user text message + the assistant's text reply; tool_result is skipped.
    assert ("user", "Build the alpha feature") in details.messages
    assert ("assistant", "Hello!") in details.messages
    assert all(role in ("user", "assistant") for role, _ in details.messages)


def test_parse_details_counts_mcp_tools(tmp_path):
    entry = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "model": "claude-opus-4-8",
            "content": [
                {"type": "tool_use", "id": "1", "name": "mcp__gitlab__get_issue", "input": {}},
                {"type": "tool_use", "id": "2", "name": "mcp__gitlab__list_issues", "input": {}},
                {"type": "tool_use", "id": "3", "name": "Bash", "input": {}},
            ],
        },
    }
    path = tmp_path / "s.jsonl"
    path.write_text(json.dumps(entry), encoding="utf-8")
    details = parse_details(path)
    assert details.mcp_tools == {"gitlab": 2}
    assert details.tool_calls == 3


def test_configured_mcp_servers(monkeypatch, tmp_path):
    import collins.sessions as sessions_mod

    config = tmp_path / "claude.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {"gitlab": {}, "playwright": {}},
                "projects": {"/home/u/proj": {"mcpServers": {"local-thing": {}}}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sessions_mod, "CLAUDE_CONFIG", config)
    assert configured_mcp_servers("/home/u/proj") == ["gitlab", "local-thing", "playwright"]
    assert configured_mcp_servers("/unknown") == ["gitlab", "playwright"]
    assert configured_mcp_servers(None) == ["gitlab", "playwright"]


def test_read_mcp_config(monkeypatch, tmp_path):
    import collins.sessions as sessions_mod

    config = tmp_path / "claude.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "gitlab": {"type": "stdio", "command": "npx", "args": ["-y", "gitlab-mcp"]},
                    "linear": {"type": "http", "url": "https://mcp.linear.app/sse"},
                },
                "projects": {
                    "/home/u/proj": {"mcpServers": {"local-thing": {"command": "./serve"}}},
                    "/home/u/empty": {"mcpServers": {}},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sessions_mod, "CLAUDE_CONFIG", config)
    mcp = read_mcp_config()
    assert not mcp.is_empty
    assert [s.name for s in mcp.global_servers] == ["gitlab", "linear"]
    assert "stdio · npx -y gitlab-mcp" == mcp.global_servers[0].summary
    assert "http · https://mcp.linear.app/sse" == mcp.global_servers[1].summary
    # only the project with servers shows up
    assert [p for p, _ in mcp.project_servers] == ["/home/u/proj"]


def test_read_mcp_config_missing_file(monkeypatch, tmp_path):
    import collins.sessions as sessions_mod

    monkeypatch.setattr(sessions_mod, "CLAUDE_CONFIG", tmp_path / "nope.json")
    assert read_mcp_config().is_empty


def test_configured_mcp_servers_missing_file(monkeypatch, tmp_path):
    import collins.sessions as sessions_mod

    monkeypatch.setattr(sessions_mod, "CLAUDE_CONFIG", tmp_path / "nope.json")
    assert configured_mcp_servers("/whatever") == []


def test_tail_state_waiting(monkeypatch, tmp_path):
    import collins.sessions as sessions_mod

    root = tmp_path / "projects" / "-home-u-proj"
    root.mkdir(parents=True)
    monkeypatch.setattr(sessions_mod, "CLAUDE_PROJECTS_DIR", tmp_path / "projects")

    def write(name, last_assistant_text):
        sid = name
        lines = [
            {"type": "user", "cwd": "/home/u/proj", "message": {"content": "do the thing"}},
            {"type": "assistant", "message": {"role": "assistant", "model": "claude-opus-4-8",
                                              "content": [{"type": "text", "text": last_assistant_text}]}},
        ]
        (root / f"{sid}.jsonl").write_text(
            "\n".join(json.dumps(line) for line in lines), encoding="utf-8"
        )

    # valid UUID-shaped stems are required by discovery
    waiting_id = "11111111-1111-1111-1111-111111111111"
    done_id = "22222222-2222-2222-2222-222222222222"
    write(waiting_id, "Which database should I use, Postgres or SQLite?")
    write(done_id, "Done — all tests pass.")

    states = {s.session_id: s.state for s in sessions_mod.discover_sessions()}
    assert states[waiting_id] == "waiting"
    assert states[done_id] == ""


def test_tail_state_interrupted(monkeypatch, tmp_path):
    import collins.sessions as sessions_mod

    root = tmp_path / "projects" / "-home-u-proj"
    root.mkdir(parents=True)
    monkeypatch.setattr(sessions_mod, "CLAUDE_PROJECTS_DIR", tmp_path / "projects")
    sid = "44444444-4444-4444-4444-444444444444"
    lines = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Working on it…"}]}},
        {"type": "user", "cwd": "/home/u/proj",
         "message": {"content": "[Request interrupted by user]"}},
    ]
    (root / f"{sid}.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines), encoding="utf-8"
    )
    state = next(s.state for s in sessions_mod.discover_sessions() if s.session_id == sid)
    assert state == "interrupted"


def test_tail_state_user_replied_after_question(monkeypatch, tmp_path):
    import collins.sessions as sessions_mod

    root = tmp_path / "projects" / "-home-u-proj"
    root.mkdir(parents=True)
    monkeypatch.setattr(sessions_mod, "CLAUDE_PROJECTS_DIR", tmp_path / "projects")
    sid = "33333333-3333-3333-3333-333333333333"
    lines = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Which one?"}]}},
        {"type": "user", "cwd": "/home/u/proj", "message": {"content": "the second one"}},
    ]
    (root / f"{sid}.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines), encoding="utf-8"
    )
    state = next(s.state for s in sessions_mod.discover_sessions() if s.session_id == sid)
    assert state == ""  # user already replied → not waiting


def _write_transcript(path, cwds):
    lines = [
        json.dumps({"type": "user", "message": {"content": f"msg {i}"}, "cwd": cwd})
        for i, cwd in enumerate(cwds)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_resume_cwd_prefers_last_transcript_cwd(tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    p = tmp_path / "abc.jsonl"
    _write_transcript(p, ["/proj", str(worktree)])
    session = session_from_file(p)
    assert session.cwd == "/proj"  # sidebar grouping keeps the starting dir
    assert resume_cwd(session) == str(worktree)


def test_resume_cwd_falls_back_when_last_dir_gone(tmp_path):
    p = tmp_path / "abc.jsonl"
    _write_transcript(p, ["/proj", str(tmp_path / "removed-worktree")])
    session = session_from_file(p)
    assert resume_cwd(session) == "/proj"


def test_resume_cwd_without_cwd_entries(tmp_path):
    p = tmp_path / "abc.jsonl"
    p.write_text(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n",
                 encoding="utf-8")
    session = session_from_file(p)
    assert resume_cwd(session) is None  # nothing recorded anywhere -> None


def test_first_message_uuid(tmp_path):
    p = tmp_path / "abc.jsonl"
    lines = [
        {"type": "ai-title", "aiTitle": "T"},  # /bg forks start with metadata lines
        {"type": "mode"},
        {"type": "user", "uuid": "uuid-1", "message": {"role": "user", "content": "hi"}},
        {"type": "assistant", "uuid": "uuid-2", "message": {"role": "assistant", "content": []}},
    ]
    p.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    assert first_message_uuid(p) == "uuid-1"


def test_first_message_uuid_none_when_unavailable(tmp_path):
    p = tmp_path / "abc.jsonl"
    p.write_text(json.dumps({"type": "mode"}) + "\nnot json\n", encoding="utf-8")
    assert first_message_uuid(p) is None
    assert first_message_uuid(tmp_path / "missing.jsonl") is None


def test_export_markdown(projects_dir):
    _root, ids = projects_dir
    session = next(s for s in discover_sessions() if s.session_id == ids["alpha1"])
    md = export_markdown(session.jsonl_path, "Alpha feature", session.session_id, session.cwd)
    assert md.startswith("# Alpha feature")
    assert f"`{session.session_id}`" in md
    assert "### You\n\nBuild the alpha feature" in md
    assert "### Claude\n\nHello!" in md
    assert "*Used `Bash`*" in md  # tool call noted


def test_parse_details_handles_garbage(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json\n{\"type\": 12}\n[]\n", encoding="utf-8")
    details = parse_details(bad)
    assert details.user_messages == 0
    assert details.models == []


def test_discover_handles_missing_dir(monkeypatch, tmp_path):
    import collins.sessions as sessions_mod

    monkeypatch.setattr(sessions_mod, "CLAUDE_PROJECTS_DIR", tmp_path / "nope")
    assert discover_sessions() == []
