import os
import shutil
import time
from pathlib import Path

from claude_session_manager import providers
from claude_session_manager.providers import (
    ClaudeProvider,
    CursorProvider,
    _decode_cursor_cwd,
    _strip_user_query,
    available_providers,
    get_provider,
)
from claude_session_manager.sessions import discover_sessions

# -- Cursor discovery ---------------------------------------------------------


def test_cursor_discovery(cursor_projects_dir):
    _root, ids = cursor_projects_dir
    sessions = discover_sessions()
    assert {s.session_id for s in sessions} == set(ids.values())
    assert all(s.provider == "cursor" for s in sessions)


def test_cursor_preview_strips_user_query(cursor_projects_dir):
    _root, ids = cursor_projects_dir
    by_id = {s.session_id: s for s in discover_sessions()}
    one = by_id[ids["one"]]
    assert one.preview == "Build foo"
    assert "<user_query>" not in one.preview


def test_cursor_waiting_state(cursor_projects_dir):
    _root, ids = cursor_projects_dir
    by_id = {s.session_id: s for s in discover_sessions()}
    assert by_id[ids["two"]].state == "waiting"  # assistant's last message ends with "?"
    assert by_id[ids["one"]].state == ""


def test_cursor_parse_details(cursor_projects_dir):
    _root, ids = cursor_projects_dir
    by_id = {s.session_id: s for s in discover_sessions()}
    details = CursorProvider().parse_details(by_id[ids["one"]].jsonl_path)
    assert details.user_messages == 1
    assert details.assistant_messages == 1
    # Cursor transcripts carry no token/model data.
    assert details.input_tokens == 0
    assert details.models == []
    assert ("user", "Build foo") in details.messages
    assert ("assistant", "Done.") in details.messages


# -- transcript resolution ----------------------------------------------------


def test_claude_transcripts_for_cwd(projects_dir):
    _root, ids = projects_dir
    claude = ClaudeProvider()
    stems = {p.stem for p in claude.transcripts_for_cwd("/home/user/alpha")}
    assert {ids["alpha1"], ids["alpha2"]} <= stems
    assert "not-a-session" not in stems  # non-uuid noise is ignored
    assert claude.transcripts_for_cwd("/home/user/nope") == []
    assert claude.transcripts_for_cwd("") == []


def test_claude_latest_transcript_for_cwd(projects_dir):
    root, ids = projects_dir
    newest = root / "-home-user-alpha" / f"{ids['alpha1']}.jsonl"
    future = time.time() + 100
    os.utime(newest, (future, future))
    assert ClaudeProvider().latest_transcript_for_cwd("/home/user/alpha") == newest


def test_cursor_transcripts_for_cwd(cursor_projects_dir):
    _root, ids = cursor_projects_dir
    cursor = CursorProvider()
    paths = cursor.transcripts_for_cwd("/home/user/foo")
    assert [p.stem for p in paths] == [ids["one"]]
    assert cursor.transcripts_for_cwd("/home/user/nope") == []


def test_session_id_for_transcript():
    assert ClaudeProvider().session_id_for_transcript(Path("/p/abc-123.jsonl")) == "abc-123"
    # Cursor's id is the per-session directory, not the file name.
    cursor_path = Path("/p/agent-transcripts/uid-1/whatever.jsonl")
    assert CursorProvider().session_id_for_transcript(cursor_path) == "uid-1"


# -- helpers ------------------------------------------------------------------


def test_strip_user_query():
    assert _strip_user_query("<user_query>\nhi there\n</user_query>") == "hi there"
    assert _strip_user_query("plain text") == "plain text"


def test_decode_cursor_cwd_roundtrip(tmp_path):
    real = tmp_path / "myproj"
    real.mkdir()
    encoded = str(real).lstrip("/").replace("/", "-")
    assert _decode_cursor_cwd(encoded) == str(real)


def test_decode_cursor_cwd_handles_literal_dash(tmp_path):
    real = tmp_path / "a-b-c"
    real.mkdir()
    encoded = str(real).lstrip("/").replace("/", "-")
    assert _decode_cursor_cwd(encoded) == str(real)


def test_decode_cursor_cwd_nonexistent_falls_back():
    assert _decode_cursor_cwd("nope-xyz-qqq") == "/nope/xyz/qqq"


# -- registry + commands ------------------------------------------------------


def test_available_providers_gating(monkeypatch):
    monkeypatch.setattr(providers.ClaudeProvider, "available", lambda self: True)
    monkeypatch.setattr(providers.CursorProvider, "available", lambda self: False)
    assert [p.id for p in available_providers()] == ["claude"]


def test_resume_and_new_commands(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cli: f"/usr/bin/{cli}")
    claude = ClaudeProvider()
    assert claude.resume_command("abc") == "/usr/bin/claude --resume abc"
    assert claude.resume_command("abc", fork=True) == "/usr/bin/claude --resume abc --fork-session"
    cursor = CursorProvider()
    assert cursor.resume_command("xyz") == "/usr/bin/cursor-agent --resume xyz"
    # Cursor doesn't support forking, so --fork-session is never appended.
    assert cursor.resume_command("xyz", fork=True) == "/usr/bin/cursor-agent --resume xyz"
    assert cursor.new_command() == "/usr/bin/cursor-agent"


def test_commands_none_when_cli_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cli: None)
    assert ClaudeProvider().resume_command("abc") is None
    assert ClaudeProvider().new_command() is None


def test_chat_variants(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cli: f"/usr/bin/{cli}")
    claude = [v for v in ClaudeProvider().chat_variants()]
    assert [(v.key, v.transport, v.writeable, v.gated) for v in claude] == [
        ("default", "stdin_stream", True, True)
    ]
    cursor = CursorProvider().chat_variants()
    assert [(v.key, v.transport, v.writeable, v.gated) for v in cursor] == [
        ("ask", "spawn_resume", False, False),
        ("trusted", "spawn_resume", True, False),
    ]


def test_chat_variants_empty_when_cli_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cli: None)
    assert ClaudeProvider().chat_variants() == []
    assert CursorProvider().chat_variants() == []


def test_cursor_chat_turn_command(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cli: f"/usr/bin/{cli}")
    cur = CursorProvider()
    # first turn (no session id), read-only ask mode
    assert cur.chat_turn_command("ask", "hello", "") == [
        "/usr/bin/cursor-agent", "-p", "--output-format", "stream-json",
        "--stream-partial-output", "--trust", "--mode", "ask", "hello",
    ]
    # follow-up turn resumes the session; trusted mode uses --force
    assert cur.chat_turn_command("trusted", "go", "sid-9") == [
        "/usr/bin/cursor-agent", "-p", "--output-format", "stream-json",
        "--stream-partial-output", "--trust", "--resume", "sid-9", "--force", "go",
    ]


def test_cursor_chat_turn_command_none_when_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cli: None)
    assert CursorProvider().chat_turn_command("ask", "hi", "") is None


def test_new_command_options(monkeypatch):
    from claude_session_manager.providers import SessionOptions
    monkeypatch.setattr(shutil, "which", lambda cli: f"/usr/bin/{cli}")
    claude = ClaudeProvider()
    opts = SessionOptions(model="opus", permission_mode="plan", add_dirs=("/extra",))
    assert claude.new_command(opts) == (
        "/usr/bin/claude --model opus --permission-mode plan --add-dir /extra"
    )
    assert claude.new_command() == "/usr/bin/claude"  # no options → bare command
    assert claude.continue_command() == "/usr/bin/claude --continue"
    # Cursor maps permission mode to --mode and has no --add-dir.
    cursor = CursorProvider()
    copts = SessionOptions(model="gpt-5", permission_mode="ask", add_dirs=("/x",))
    assert cursor.new_command(copts) == "/usr/bin/cursor-agent --model gpt-5 --mode ask"
    assert cursor.continue_command() == "/usr/bin/cursor-agent --continue"


def test_provider_option_lists():
    assert ClaudeProvider().supports_add_dir is True
    assert CursorProvider().supports_add_dir is False
    assert ("opus", "Opus") in ClaudeProvider().session_models()
    assert any(v == "plan" for v, _l in ClaudeProvider().permission_modes())
    assert any(v == "ask" for v, _l in CursorProvider().permission_modes())


def test_claude_chat_command_resume(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cli: f"/usr/bin/{cli}")
    cmd = ClaudeProvider().chat_command("sess-42")
    assert cmd[-2:] == ["--resume", "sess-42"]
    # no resume id → no --resume flag
    assert "--resume" not in ClaudeProvider().chat_command()


def test_get_provider_default():
    assert get_provider("cursor").id == "cursor"
    assert get_provider("unknown-agent").id == "claude"  # legacy/unknown -> Claude


def test_graceful_exit_text():
    assert ClaudeProvider().graceful_exit() == "/exit\r"
    assert CursorProvider().graceful_exit() is None


def test_claude_answer_keystrokes_single_select():
    q = [{"question": "Which DB?", "multiSelect": False,
          "options": [{"label": "Postgres"}, {"label": "SQLite"}, {"label": "Mongo"}]}]
    claude = ClaudeProvider()
    assert claude.answer_keystrokes(q, 0) == "\r"               # first option: just submit
    assert claude.answer_keystrokes(q, 2) == "\x1b[B\x1b[B\r"   # down twice, submit
    assert claude.answer_keystrokes(q, 9) is None               # out of range


def test_answer_keystrokes_fallback_cases():
    claude = ClaudeProvider()
    multi = [{"question": "Pick", "multiSelect": True, "options": [{"label": "a"}]}]
    two = [{"question": "1", "options": [{"label": "a"}]}, {"question": "2", "options": [{"label": "b"}]}]
    assert claude.answer_keystrokes(multi, 0) is None  # multi-select → terminal
    assert claude.answer_keystrokes(two, 0) is None    # multiple questions → terminal
    # Cursor can't auto-answer at all
    assert CursorProvider().answer_keystrokes(
        [{"question": "x", "options": [{"label": "a"}]}], 0) is None
