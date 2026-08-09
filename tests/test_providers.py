# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-09. Full change history: git log for this file.

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
    split_screen_rows,
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


def test_background_agents_never_hands_the_cli_our_tty(monkeypatch):
    # The CLI puts a tty stdin into raw mode for its whole run and restores
    # it only on a clean exit — an inherited stdin is the terminal the app
    # was launched from, and one timeout-killed poll left that terminal
    # raw/no-echo. stdin must be /dev/null so the CLI never touches it.
    monkeypatch.setattr(shutil, "which", lambda cli: f"/usr/bin/{cli}")
    seen = {}

    class Result:
        stdout = "[]"

    def fake_run(*args, **kwargs):
        seen.update(kwargs)
        return Result()

    monkeypatch.setattr(providers.subprocess, "run", fake_run)
    ClaudeProvider().background_agents()
    assert seen["stdin"] == providers.subprocess.DEVNULL


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


def test_background_watch_dir():
    assert ClaudeProvider().background_watch_dir() == Path.home() / ".claude" / "jobs"
    # Base providers have no background agents → nothing to watch.
    assert Provider().background_watch_dir() is None


# -- is the agent's prompt free to type into? ---------------------------------
#
# The lines below are real Claude Code screens (v2.1.220, and v2.1.223 for the
# suggestions), read back out of a VTE terminal exactly as
# TerminalTab.takes_prompt reads them: the line the cursor is on, the column it
# sits at, and whether the rest of that line was drawn dim. The marker is a
# caret and U+00A0.


def test_claude_prompt_is_free_when_it_is_empty():
    claude = ClaudeProvider()
    # Freshly drawn: a dim suggestion sits in the empty input.
    assert claude.takes_prompt('❯\xa0Try "create a util logging.py that..."', 2, True) is True
    # Just after a prompt was sent: no suggestion at all.
    assert claude.takes_prompt("❯\xa0", 2) is True


def test_the_opening_suggestion_is_recognised_by_its_words_alone():
    """The `Try "…"` opener is the one suggestion with fixed wording, and it
    stays readable without the terminal reporting how the line was drawn — the
    fallback for a terminal that can't say (an old VTE, an unknown foreground
    behind an unusual theme)."""
    claude = ClaudeProvider()
    assert claude.takes_prompt('❯\xa0Try "create a util logging.py that..."', 2) is True


def test_a_suggestion_written_for_the_session_is_still_an_empty_prompt():
    """Once a session has some history the CLI stops offering `Try "…"` and
    suggests something about the work at hand instead. It is ghost text just
    the same — dim, and gone the moment anything is typed — and reading it as
    the user's own is what greyed a resting session's prompt actions out."""
    claude = ClaudeProvider()
    assert claude.takes_prompt("❯\xa0close both PRs and delete the branches", 2, True) is True


def test_claude_prompt_is_taken_once_anything_is_typed():
    claude = ClaudeProvider()
    assert claude.takes_prompt("❯\xa0fix the flaky test", 20) is False
    assert claude.takes_prompt("❯\xa0/he", 5) is False


def test_a_cursor_sent_home_over_typed_text_is_not_an_empty_prompt():
    """Ctrl+A puts the cursor back at column 2 with the line still written.
    Typed text is drawn in the plain foreground, so what saves this is the same
    thing that lets a suggestion through: how the line was drawn, not where the
    cursor ended up."""
    assert ClaudeProvider().takes_prompt("❯\xa0fix the flaky test", 2) is False


def test_dim_text_somewhere_other_than_the_input_is_not_an_empty_prompt():
    """The dimness only ever excuses a line that already opens like the input
    box — a dim line under a permission prompt stays untypeable."""
    claude = ClaudeProvider()
    assert claude.takes_prompt("❯ 1. Yes, proceed", 2, True) is False
    assert claude.takes_prompt("❯\xa0fix the flaky test", 20, True) is False


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


# -- reading the typed prompt back out of the input box -----------------------
#
# The screens below are real Claude Code renderings (v2.1.226), captured by
# spawning the CLI in a headless VTE and typing into it — the same rows
# TerminalTab.entered_prompt hands over. The box frames itself with
# full-width rules; text rows keep two cells of frame on the left and two
# clear on the right, so at 80 columns a row holds at most 76 cells of text
# and only a mid-word token split fills them.


def _screen(*box_rows: str, cols: int = 80) -> list[str]:
    """A visible screen: header junk, the framed input box, the hint line."""
    return [
        "",
        " ▐▛███▜▌   Claude Code v2.1.226",
        "",
        "─" * cols,
        *box_rows,
        "─" * cols,
        "  ⏵⏵ auto mode on (shift+tab to cycle)",
        "",
    ]


def _box_row(index: int) -> int:
    """Screen index of the box's *index*-th row, as _screen laid it out."""
    return 4 + index


def test_a_single_line_prompt_reads_back_verbatim():
    claude = ClaudeProvider()
    prompt = claude.entered_prompt(
        _screen("❯\xa0fix the flaky test"), _box_row(0), 80
    )
    assert prompt.text == "fix the flaky test"
    assert prompt.rows_below == 0


def test_a_word_wrap_stitches_back_into_the_typed_space():
    """The CLI moved "invalidated" down whole because it had no room left —
    the break *is* the typed space, so the copy gets the space back."""
    claude = ClaudeProvider()
    prompt = claude.entered_prompt(
        _screen(
            "❯\xa0please refactor the frobnicator module so that the widget cache is",
            "  invalidated whenever the upstream configuration changes and add tests",
        ),
        _box_row(1),
        80,
    )
    assert prompt.text == (
        "please refactor the frobnicator module so that the widget cache is "
        "invalidated whenever the upstream configuration changes and add tests"
    )


def test_a_typed_line_break_stays_a_line_break():
    """"also" had plenty of room after "tests" — only a typed break puts it
    on its own row."""
    claude = ClaudeProvider()
    prompt = claude.entered_prompt(
        _screen(
            "❯\xa0please refactor the frobnicator module so that the widget cache is",
            "  invalidated whenever the upstream configuration changes and add tests",
            "  also update the docs",
        ),
        _box_row(2),
        80,
    )
    assert prompt.text.endswith("add tests\nalso update the docs")


def test_a_split_token_rejoins_without_a_space():
    """A token longer than a row fills rows to the brim; the brim-full row is
    the tell that nothing stood between the halves. The captured screen also
    keeps the stray trailing cell the CLI left on the row above when the
    typed break was converted (the drawn backslash's cell) — stripped, it
    was never in the buffer."""
    claude = ClaudeProvider()
    prompt = claude.entered_prompt(
        _screen(
            "❯\xa0first part",
            "  also update the docs ",
            "  " + "a" * 76,
            "  " + "a" * 34,
        ),
        _box_row(3),
        80,
    )
    assert prompt.text == "first part\nalso update the docs\n" + "a" * 110


def test_surviving_trailing_cells_mark_a_typed_break():
    """A wrap erases to the row's end, so trailing space cells only survive
    where a typed break's backslash was drawn — and they outrank the fit
    heuristic, which would read this nearly-full row before a short word
    as a wrap."""
    claude = ClaudeProvider()
    prompt = claude.entered_prompt(
        _screen(
            "❯\xa0" + "x" * 74 + " ",
            "  and more",
        ),
        _box_row(1),
        80,
    )
    assert prompt.text == "x" * 74 + "\nand more"


def test_a_word_ending_flush_at_the_margin_is_still_a_wrap():
    """Captured at 100 columns: "abcde" ends exactly at the 96-cell margin.
    The row is brim-full, but the runs either side of the break are two
    little words that could have shared a row — a split token couldn't."""
    claude = ClaudeProvider()
    prompt = claude.entered_prompt(
        _screen(
            "❯\xa0" + "x" * 90 + " abcde",
            "  next words",
            cols=100,
        ),
        _box_row(1),
        100,
    )
    assert prompt.text == "x" * 90 + " abcde next words"


def test_a_break_before_a_row_filling_token_is_kept():
    """A wrap would have filled the short row before the long token (the CLI
    fills mid-token rather than leave a gap — probed), so a short row before
    one reads as the typed break it was."""
    claude = ClaudeProvider()
    prompt = claude.entered_prompt(
        _screen(
            "❯\xa0short line",
            "  " + "b" * 76,
            "  bb",
        ),
        _box_row(2),
        80,
    )
    assert prompt.text == "short line\n" + "b" * 78


def test_blank_and_indented_lines_are_typed_breaks():
    claude = ClaudeProvider()
    prompt = claude.entered_prompt(
        _screen(
            "❯\xa0def f():",
            "      return 1",
            "",
            "  call it twice",
        ),
        _box_row(3),
        80,
    )
    assert prompt.text == "def f():\n    return 1\n\ncall it twice"


def test_the_cursor_arrowed_back_up_still_reads_the_whole_box():
    """rows_below is what clear_prompt_keys walks back down before erasing."""
    claude = ClaudeProvider()
    prompt = claude.entered_prompt(
        _screen(
            "❯\xa0first line",
            "  second line",
            "  third line",
        ),
        _box_row(0),
        80,
    )
    assert prompt.text == "first line\nsecond line\nthird line"
    assert prompt.rows_below == 2


def test_a_scrolled_box_yields_its_visible_tail():
    """A box taller than the terminal scrolls with no on-screen tell — the
    mark row simply shows the first visible line. What's visible is what
    there is to copy."""
    claude = ClaudeProvider()
    prompt = claude.entered_prompt(
        _screen("❯\xa0line25", "  line26", "  tail"), _box_row(2), 80
    )
    assert prompt.text == "line25\nline26\ntail"


def test_no_box_on_screen_reads_as_nothing():
    claude = ClaudeProvider()
    # A permission dialog: its caret is not the input's (ordinary space).
    dialog = ["  Do you want to proceed?", "  ❯ 1. Yes", "    2. No", ""]
    assert claude.entered_prompt(dialog, 1, 80) is None
    # A cursor below the box (in the hint rows) walks up into the box's own
    # rule before any mark row.
    screen = _screen("❯\xa0typed text")
    assert claude.entered_prompt(screen, _box_row(2), 80) is None
    # A cursor index outside the screen reads as nothing at all.
    assert claude.entered_prompt(screen, 99, 80) is None


def test_clear_keys_walk_to_the_end_and_erase_every_character():
    """Down per row below the cursor (no-ops once at the bottom — probed),
    Ctrl+E to the line's end, then one backspace per character: a break
    deletes as one, and — unlike Esc Esc — none of it can interrupt a
    running turn."""
    claude = ClaudeProvider()
    prompt = claude.entered_prompt(
        _screen("❯\xa0first line", "  second"), _box_row(0), 80
    )
    keys = claude.clear_prompt_keys(prompt)
    assert keys == "\x1b[B" + "\x05" + "\x7f" * len("first line\nsecond")


def test_base_providers_have_no_box_to_read_or_clear():
    assert Provider().entered_prompt(["❯\xa0typed"], 0, 80) is None


def test_screen_rows_split_where_the_terminal_wrapped():
    """VTE hands soft-wrapped rows back joined; the split must land on the
    same boundaries the terminal wrapped at, counted in cells."""
    # ASCII: a brim-full row is exactly `columns` characters.
    assert split_screen_rows("x" * 200, 80) == ["x" * 80, "x" * 80, "x" * 40]
    # Wide characters fill two cells each: 50 CJK chars are 100 cells, so
    # the first row holds 40 characters, not 80 — character-count chunks
    # would swallow the boundary and shift every row after it.
    assert split_screen_rows("字" * 50, 80) == ["字" * 40, "字" * 10]
    # A wide character that would straddle the last cell starts the next
    # row, leaving that cell empty — the row before it is 79 cells.
    assert split_screen_rows("x" * 79 + "字字", 80) == ["x" * 79, "字字"]
    # A zero-width mark stays with the character it decorates, even when
    # that character sits in the row's last cell.
    assert split_screen_rows("x" * 80 + "́y", 80) == ["x" * 80 + "́", "y"]
    # Lines that fit stay whole; blanks stay rows.
    assert split_screen_rows("short\n\nnext", 80) == ["short", "", "next"]


def test_wide_scrollback_above_the_box_does_not_shift_the_read():
    """A soft-wrapped CJK line in the shell scrollback above the box comes
    back joined; only a cell-counted split keeps the cursor index pointing
    into the box."""
    screen_text = "\n".join(
        [
            "字" * 50,  # two screen rows once split (100 cells at 80 columns)
            "─" * 80,
            "❯\xa0fix the flaky test",
            "─" * 80,
            "  ⏵⏵ auto mode on",
        ]
    )
    rows = split_screen_rows(screen_text, 80)
    assert rows[3] == "❯\xa0fix the flaky test"
    prompt = ClaudeProvider().entered_prompt(rows, 3, 80)
    assert prompt.text == "fix the flaky test"


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


def test_claude_file_reference_refuses_control_characters():
    """The tty acts on control bytes before any tokenizer sees them — a CR
    would submit the input box — so names carrying them get no token at all."""
    claude = ClaudeProvider()
    assert claude.file_reference("/p/evil\nname.py", "/p") is None
    assert claude.file_reference("/p/evil\rname.py", "/p", 1, 2) is None
    assert claude.file_reference("/p/evil\x1b]0;x\x07.py", "/p") is None


# -- the session MCP tools' --mcp-config flag ---------------------------------
#
# providers.MCP_CONFIG_PATH is set by app.py only once the socket service and
# config file are actually up; until then (and on any failure) it stays None
# and every command below must come out exactly as it did before the feature.


@pytest.fixture
def mcp_config_set(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cli: f"/usr/bin/{cli}")
    monkeypatch.setattr(providers, "MCP_CONFIG_PATH", "/run/user/1/collins/id/mcp.json")
    monkeypatch.setattr(
        ClaudeProvider, "background_agents", lambda self, include_finished=False: []
    )


def test_launch_commands_carry_the_mcp_config(mcp_config_set):
    claude = ClaudeProvider()
    suffix = " --mcp-config /run/user/1/collins/id/mcp.json"
    assert claude.new_command() == "/usr/bin/claude" + suffix
    assert claude.resume_command("abc") == "/usr/bin/claude --resume abc" + suffix
    assert (
        claude.resume_command("abc", fork=True)
        == "/usr/bin/claude --resume abc --fork-session" + suffix
    )
    assert claude.continue_command() == "/usr/bin/claude --continue" + suffix


def test_chat_command_carries_the_mcp_config(mcp_config_set):
    argv = ClaudeProvider().chat_command("sess-42")
    index = argv.index("--mcp-config")
    assert argv[index + 1] == "/run/user/1/collins/id/mcp.json"
    assert argv[-2:] == ["--resume", "sess-42"]  # resume args stay terminal


def test_a_spaced_config_path_is_quoted(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cli: f"/usr/bin/{cli}")
    monkeypatch.setattr(providers, "MCP_CONFIG_PATH", "/tmp/my dir/mcp.json")
    assert ClaudeProvider().new_command().endswith(" --mcp-config '/tmp/my dir/mcp.json'")


def test_no_mcp_config_flag_while_unset(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cli: f"/usr/bin/{cli}")
    monkeypatch.setattr(
        ClaudeProvider, "background_agents", lambda self, include_finished=False: []
    )
    assert providers.MCP_CONFIG_PATH is None  # the module default
    claude = ClaudeProvider()
    assert "--mcp-config" not in claude.new_command()
    assert "--mcp-config" not in claude.resume_command("abc")
    assert "--mcp-config" not in claude.continue_command()
    assert "--mcp-config" not in claude.chat_command()


def test_base_providers_never_carry_the_mcp_config(monkeypatch):
    """The capability flag gates the append, so a provider whose CLI has no
    --mcp-config flag is untouched even while the path is set."""

    class OtherAgent(Provider):
        cli = "other"

    monkeypatch.setattr(shutil, "which", lambda cli: f"/usr/bin/{cli}")
    monkeypatch.setattr(providers, "MCP_CONFIG_PATH", "/run/collins/mcp.json")
    assert OtherAgent().new_command() == "/usr/bin/other"
    assert OtherAgent().resume_command("abc") == "/usr/bin/other --resume abc"


def test_attach_carries_no_mcp_config(monkeypatch):
    """`claude attach` accepts no flags at all — and needs none: it joins a
    daemon process that already has the servers from its own launch."""
    monkeypatch.setattr(shutil, "which", lambda cli: f"/usr/bin/{cli}")
    monkeypatch.setattr(providers, "MCP_CONFIG_PATH", "/run/collins/mcp.json")
    monkeypatch.setattr(
        ClaudeProvider,
        "background_agents",
        lambda self, include_finished=False: [
            BackgroundAgent(session_id="abc", job_id="abc12345", cwd="/p")
        ],
    )
    assert ClaudeProvider().resume_command("abc") == "/usr/bin/claude attach abc12345"


def test_new_command_worktree_flag(monkeypatch):
    from collins.providers import SessionOptions
    monkeypatch.setattr(shutil, "which", lambda cli: f"/usr/bin/{cli}")
    claude = ClaudeProvider()
    assert claude.new_command(SessionOptions(worktree=True)) == "/usr/bin/claude -w"
    assert claude.new_command(SessionOptions()) == "/usr/bin/claude"
