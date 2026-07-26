# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-26. Full change history: git log for this file.

import os
import shutil
import time
from pathlib import Path

from claude_session_manager import providers
from claude_session_manager.providers import (
    ClaudeProvider,
    available_providers,
    get_provider,
)

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


def test_session_id_for_transcript():
    assert ClaudeProvider().session_id_for_transcript(Path("/p/abc-123.jsonl")) == "abc-123"


# -- registry + commands ------------------------------------------------------


def test_available_providers_gating(monkeypatch):
    monkeypatch.setattr(providers.ClaudeProvider, "available", lambda self: True)
    assert [p.id for p in available_providers()] == ["claude"]
    monkeypatch.setattr(providers.ClaudeProvider, "available", lambda self: False)
    assert available_providers() == []


def test_resume_and_new_commands(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cli: f"/usr/bin/{cli}")
    claude = ClaudeProvider()
    assert claude.resume_command("abc") == "/usr/bin/claude --resume abc"
    assert claude.resume_command("abc", fork=True) == "/usr/bin/claude --resume abc --fork-session"


def test_commands_none_when_cli_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cli: None)
    assert ClaudeProvider().resume_command("abc") is None
    assert ClaudeProvider().new_command() is None


def test_chat_variants(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cli: f"/usr/bin/{cli}")
    claude = [v for v in ClaudeProvider().chat_variants()]
    assert [(v.key, v.writeable, v.gated) for v in claude] == [("default", True, True)]


def test_chat_variants_empty_when_cli_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cli: None)
    assert ClaudeProvider().chat_variants() == []


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


def test_provider_option_lists():
    assert ClaudeProvider().supports_add_dir is True
    assert ("opus", "Opus") in ClaudeProvider().session_models()
    assert any(v == "plan" for v, _l in ClaudeProvider().permission_modes())


def test_claude_chat_command_resume(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cli: f"/usr/bin/{cli}")
    cmd = ClaudeProvider().chat_command("sess-42")
    assert cmd[-2:] == ["--resume", "sess-42"]
    # no resume id → no --resume flag
    assert "--resume" not in ClaudeProvider().chat_command()


def test_get_provider_default():
    assert get_provider("claude").id == "claude"
    assert get_provider("unknown-agent").id == "claude"  # legacy/unknown -> Claude


def test_graceful_exit_text():
    assert ClaudeProvider().graceful_exit() == "/exit\r"


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
