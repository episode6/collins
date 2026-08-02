# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-02. Full change history: git log for this file.

import os
import shutil
import time
from pathlib import Path

import pytest

from collins import providers
from collins.providers import (
    BackgroundAgent,
    ClaudeProvider,
    Provider,
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
    monkeypatch.setattr(
        ClaudeProvider, "background_agents", lambda self, include_finished=False: []
    )
    claude = ClaudeProvider()
    assert claude.resume_command("abc") == "/usr/bin/claude --resume abc"
    assert claude.resume_command("abc", fork=True) == "/usr/bin/claude --resume abc --fork-session"


def test_resume_attaches_to_running_background_session(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cli: f"/usr/bin/{cli}")
    monkeypatch.setattr(
        ClaudeProvider,
        "background_agents",
        lambda self, include_finished=False: [
            BackgroundAgent(session_id="abc", job_id="abc12345", cwd="/p")
        ],
    )
    claude = ClaudeProvider()
    # Attach takes the daemon's short job id — the session id gets "No job matching".
    assert claude.resume_command("abc") == "/usr/bin/claude attach abc12345"
    # Forks always resume: attach can't create a new session.
    assert claude.resume_command("abc", fork=True) == "/usr/bin/claude --resume abc --fork-session"
    # A session with no live background agent falls back to a plain resume.
    assert claude.resume_command("other") == "/usr/bin/claude --resume other"


def test_resume_attaches_to_finished_resident_background_job(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cli: f"/usr/bin/{cli}")

    class Result:
        stdout = _agents_json_payload("done")

    monkeypatch.setattr(providers.subprocess, "run", lambda *a, **k: Result())
    # The daemon refuses a plain --resume for any session id it still lists,
    # and a finished job stays listed (process resident, state "done") — so
    # opening one has to attach, exactly like a live job.
    assert (
        ClaudeProvider().resume_command("finished-id")
        == "/usr/bin/claude attach finished-job"
    )


def test_background_agents_parses_agents_json(monkeypatch):
    import json

    monkeypatch.setattr(shutil, "which", lambda cli: f"/usr/bin/{cli}")
    payload = json.dumps(
        [
            {"sessionId": "fg-id", "kind": "interactive"},  # no job id: not detached
            # No `state` field, as older CLIs emit: treated as still running.
            {"sessionId": "bg-id", "id": "bg-job", "kind": "background", "cwd": "/p"},
            {"sessionId": "no-job-id", "kind": "background"},
            "not-a-dict",
        ]
    )

    class Result:
        stdout = payload

    monkeypatch.setattr(providers.subprocess, "run", lambda *a, **k: Result())
    agents = ClaudeProvider().background_agents()
    # Interactive = open in a foreground TUI somewhere; attach doesn't target
    # it. A background entry without a job id can't be attached either.
    assert agents == [BackgroundAgent(session_id="bg-id", job_id="bg-job", cwd="/p")]


def _agents_json_payload(finished_state: str) -> str:
    import json

    return json.dumps(
        [
            # Finished but still resident (Completed in `claude`'s own agent
            # view) — kind alone can't tell it apart from a live job.
            {
                "sessionId": "finished-id",
                "id": "finished-job",
                "kind": "background",
                "cwd": "/p",
                "state": finished_state,
            },
            {
                "sessionId": "live-id",
                "id": "live-job",
                "kind": "background",
                "cwd": "/p",
                "state": "working",
            },
        ]
    )


@pytest.mark.parametrize("state", sorted(providers._BACKGROUND_TERMINAL_STATES))
def test_background_agents_excludes_finished_jobs(monkeypatch, state):
    monkeypatch.setattr(shutil, "which", lambda cli: f"/usr/bin/{cli}")

    class Result:
        stdout = _agents_json_payload(state)

    monkeypatch.setattr(providers.subprocess, "run", lambda *a, **k: Result())
    agents = ClaudeProvider().background_agents()
    assert agents == [BackgroundAgent(session_id="live-id", job_id="live-job", cwd="/p")]


def test_background_agents_include_finished_keeps_finished_jobs(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cli: f"/usr/bin/{cli}")

    class Result:
        stdout = _agents_json_payload("done")

    monkeypatch.setattr(providers.subprocess, "run", lambda *a, **k: Result())
    # Pairing callers (match_background_fork) ask for finished jobs too: the
    # fork a /bg created is still the fork after it runs to completion.
    agents = ClaudeProvider().background_agents(include_finished=True)
    assert [a.session_id for a in agents] == ["finished-id", "live-id"]


def test_background_agents_empty_on_error(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cli: f"/usr/bin/{cli}")

    def boom(*a, **k):
        raise OSError("exec failed")

    monkeypatch.setattr(providers.subprocess, "run", boom)
    assert ClaudeProvider().background_agents() == []


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
    from collins.providers import SessionOptions
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
    # Ctrl+C twice, in a single feed: the CLI's "Press Ctrl-C again to exit"
    # window closes after ~2s, so the pair must not be split across writes.
    assert ClaudeProvider().graceful_exit() == "\x03\x03"
    assert Provider().graceful_exit() is None  # no clean exit → force-close


def test_background_exit_text():
    assert ClaudeProvider().background_exit() == "/bg\r"
    # Base providers can't be backgrounded → no Background option in dialogs.
    assert Provider().background_exit() is None


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


def test_background_watch_dir():
    assert ClaudeProvider().background_watch_dir() == Path.home() / ".claude" / "jobs"
    # Base providers have no background agents → nothing to watch.
    assert Provider().background_watch_dir() is None


# -- is the agent's prompt free to type into? ---------------------------------
#
# The lines below are real Claude Code v2.1.220 screens, read back out of a VTE
# terminal exactly as TerminalTab.takes_prompt reads them: the line the cursor
# is on, and the column it sits at. The marker is a caret and U+00A0.


def test_claude_prompt_is_free_when_it_is_empty():
    claude = ClaudeProvider()
    # Freshly drawn: a dim suggestion sits in the empty input.
    assert claude.takes_prompt('❯\xa0Try "create a util logging.py that..."', 2) is True
    # Just after a prompt was sent: no suggestion at all.
    assert claude.takes_prompt("❯\xa0", 2) is True


def test_claude_prompt_is_taken_once_anything_is_typed():
    claude = ClaudeProvider()
    assert claude.takes_prompt("❯\xa0fix the flaky test", 20) is False
    assert claude.takes_prompt("❯\xa0/he", 5) is False


def test_a_cursor_sent_home_over_typed_text_is_not_an_empty_prompt():
    """Ctrl+A puts the cursor back at column 2 with the line still written;
    what follows the marker is what gives it away."""
    assert ClaudeProvider().takes_prompt("❯\xa0fix the flaky test", 2) is False


def test_claudes_other_prompts_are_not_the_input():
    """The trust dialog and permission prompts draw their own caret — indented,
    and with an ordinary space. They take Enter too, which is exactly why a
    prompt must never be sent into one."""
    claude = ClaudeProvider()
    assert claude.takes_prompt(" ❯ 1. Yes, I trust this folder", 1) is False
    assert claude.takes_prompt("❯ 1. Yes, proceed", 2) is False  # ordinary space
    assert claude.takes_prompt("", 0) is False
    assert claude.takes_prompt("$ ", 2) is False  # a shell, the agent gone


def test_base_providers_never_claim_a_free_prompt():
    """An agent whose screen we can't read is the one worth not typing into."""
    assert Provider().takes_prompt("❯\xa0", 2) is False


# -- the "leaving a worktree" dialog, at graceful-close time ------------------


def test_claude_worktree_exit_dialog_is_answered_with_enter():
    claude = ClaudeProvider()
    screen = (
        "You are working in a worktree. Keep it to continue working there,\n"
        "or remove it to clean up.\n"
        "\n"
        "❯ Keep worktree\n"
        "  Remove worktree\n"
    )
    assert claude.worktree_exit_prompt(screen) == "\r"


def test_claude_worktree_exit_dialog_with_tmux_variant_is_still_answered():
    """The tmux-paired dialog swaps in three longer labels, but the first is
    still a "Keep worktree..." default — the anchored marker still matches."""
    claude = ClaudeProvider()
    screen = (
        'This session was named "refactor-auth". Keep the worktree to resume\n'
        'it later, or remove it to clean up.\n'
        '\n'
        '❯ Keep worktree and tmux session\n'
        '    Stays at /repo/.claude/worktrees/refactor-auth. '
        'Reattach with: tmux attach -t refactor-auth\n'
        '  Keep worktree, end tmux session\n'
        '    Keeps worktree at /repo/.claude/worktrees/refactor-auth, '
        'terminates tmux session.\n'
        '  Remove worktree and tmux session\n'
        '    All changes and commits will be lost.\n'
    )
    assert claude.worktree_exit_prompt(screen) == "\r"


def test_claude_other_screens_are_not_the_worktree_dialog():
    claude = ClaudeProvider()
    assert claude.worktree_exit_prompt("❯\xa0fix the flaky test") is None
    assert claude.worktree_exit_prompt("") is None
    # Only "Keep worktree" on screen (e.g. the confirmation after answering)
    # isn't the dialog itself — Enter there would just hit whatever's next.
    assert claude.worktree_exit_prompt("Worktree kept. Goodbye!") is None


def test_claude_worktree_mentions_in_scrollback_are_not_the_dialog():
    """Both labels appearing on screen isn't enough on its own — an earlier
    turn that happened to discuss "Keep worktree" and "Remove worktree" (e.g.
    this very PR's own diff) must not be mistaken for the dialog actually
    showing. Only the ❯ selection marker sitting right before the label,
    exactly as `takes_prompt` requires for the input prompt's own marker,
    tells the two apart."""
    claude = ClaudeProvider()
    screen = (
        "❯\xa0explain what \"Keep worktree\" and \"Remove worktree\" do\n"
        "  in the exit dialog\n"
    )
    assert claude.worktree_exit_prompt(screen) is None


def test_base_providers_have_no_worktree_exit_dialog():
    assert Provider().worktree_exit_prompt("Keep worktree\nRemove worktree") is None


# -- file references (the editor's "Add to chat") -----------------------------


def test_claude_file_reference_whole_file_is_a_bare_mention():
    assert ClaudeProvider().file_reference("/home/user/proj/app.py", "/home/user/proj") == "@app.py"


def test_claude_file_reference_line_ranges():
    claude = ClaudeProvider()
    assert claude.file_reference("/p/a.py", "/p", 2, 4) == "@a.py#L2-4"
    assert claude.file_reference("/p/a.py", "/p", 5, 5) == "@a.py#L5"


def test_claude_file_reference_outside_cwd_falls_back_to_absolute():
    """The agent cd'd into a worktree; the editor's file lives outside it."""
    worktree = "/home/user/proj/.claude/worktrees/wt"
    ref = ClaudeProvider().file_reference("/home/user/proj/app.py", worktree, 1, 3)
    assert ref == "@/home/user/proj/app.py#L1-3"
    assert ClaudeProvider().file_reference("/p/a.py", None) == "@/p/a.py"


def test_claude_file_reference_quotes_a_path_with_spaces():
    """The CLI's mention tokenizer stops at whitespace unless quoted."""
    assert ClaudeProvider().file_reference("/p/my file.txt", "/p", 2, 4) == '@"my file.txt"#L2-4'


def test_base_providers_have_no_file_reference():
    assert Provider().file_reference("/p/a.py", "/p", 1, 2) is None
