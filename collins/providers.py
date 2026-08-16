# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-16. Full change history: git log for this file.

"""Agent providers: each adapts one AI coding-agent CLI to the app's Session model.

A provider knows how to discover its sessions on disk, how to resume/start them
in a terminal, and how to close them cleanly. This fork supports Claude Code
only; the Claude adapter wraps the original discovery logic in sessions.py.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import sessions
from .dropimages import cell_width
from .sessions import (
    _UUID_RE,
    Session,
    SessionDetails,
    _scan_tail,
    _scan_transcript,
    transcript_is_stub,
)
from .sessions import parse_details as _claude_parse_details
from .titles import is_scratch_project

# How Claude Code opens the line it takes a prompt on: a caret and a no-break
# space (an ordinary space is what its *other* prompts use), followed by the
# suggestion it shows while nothing has been typed. See takes_prompt.
_PROMPT_MARK = "❯ "
# The one suggestion whose wording is fixed, kept as a second way to recognise
# ghost text for a terminal that can't report how the line was drawn. Every
# other suggestion is written for the session at hand and can say anything.
_PROMPT_HINT = 'Try "'

# Claude's own confirmation before it will leave a worktree session (with or
# without a paired tmux session): "Keep worktree[...]" is always the first
# item and the dialog's default selection, "Remove worktree[...]" the last.
# The graceful-close flow that reads this never sends an arrow key, so the
# selection marker (the same ❯ the trust and permission dialogs draw) sitting
# right before "Keep worktree" is what's actually being detected — plain
# substrings would also match a "Keep worktree" mentioned in scrollback
# further up the screen, from an earlier turn that happened to discuss the
# dialog. Requiring both close together is what `takes_prompt` does for the
# input prompt's own marker. See ClaudeProvider.worktree_exit_prompt.
_WORKTREE_EXIT_SELECTED_RE = re.compile(r"❯\s*Keep worktree\b")
_WORKTREE_EXIT_OTHER_OPTION = "Remove worktree"

# How Claude Code frames its input box (probed live against 2.1.226 — the
# captured screens are in test_providers): a full-width rule row above and
# below, the prompt-mark row, and continuation rows opening with two plain
# spaces. Each box row leaves _BOX_MARGIN cells unused: two for the
# "❯ "/"  " prefix and a two-cell right margin — a row only ever reaches
# `columns - _BOX_MARGIN` cells of text, and does reach it whenever the CLI
# splits a too-long token mid-word.
_BOX_RULE_CHAR = "─"
_BOX_PREFIX_CELLS = 2
_BOX_MARGIN = 4


def split_screen_rows(text: str, columns: int) -> list[str]:
    """A terminal's joined plain-text screen read, split back into its rows.

    VTE returns soft-wrapped screen rows joined into one logical line, and
    entered_prompt's row indices (the cursor's above all) only line up when
    every such line is put back on its row boundaries. The split counts
    cells, never characters — a wide character fills two, so a brim-full
    row of CJK is *fewer* characters than columns, and fixed-size character
    chunks would drift every row after it. The cut falls exactly where the
    terminal wrapped: a character that would overflow the row starts the
    next one (which is also how a wide character leaves a straddled last
    cell empty), and a zero-width mark stays with the character it
    decorates.
    """
    rows: list[str] = []
    for line in text.split("\n"):
        if cell_width(line) <= columns:
            rows.append(line)
            continue
        row = ""
        cells = 0
        for char in line:
            width = cell_width(char)
            if cells + width > columns:
                rows.append(row)
                row, cells = "", 0
            row += char
            cells += width
        rows.append(row)
    return rows


def _box_rule(row: str) -> bool:
    """A full-width horizontal rule — the frame above and below the box."""
    return bool(row) and set(row) == {_BOX_RULE_CHAR}


def _box_body_row(row: str) -> bool:
    """A row that can sit inside the input box below the mark row: a
    two-space-prefixed continuation, or a blank (an empty typed line)."""
    return not row.strip() or row.startswith("  ")


def _wrap_join(c1: str, c2: str, width: int) -> str:
    """What stood between two adjacent box rows in the typed text: nothing
    (one token split mid-word at the edge), a space (a word wrap), or a
    newline the user actually typed.

    The CLI's renderer doesn't say which, so this reads it off the shapes
    the probes established: a wrap only ever leaves a row short when moving
    a whole word down would overflow it, and only fills a row to the brim
    when splitting a token longer than the row. Everything else — a short
    row before a word that had room to spare, a next row opening with a
    blank or an indent, a next row opening with a token too long to have
    ever shared a row — is a break the user typed.

    Callers rule on the stronger evidence first: a row keeping trailing
    space cells is a typed break (see entered_prompt), never asked here.
    What stays unknowable is a break right after a row those cells were
    repainted away from — with the row nearly full, or a word ending flush
    at the margin, it reads as a wrap and the copied text gains a space
    where the newline was.

    Miscounting the *characters* costs more than miscounting the shape:
    the composer's open-cut erases one backspace per character read, so a
    join that drops a space the user typed leaves a character behind in
    the box (terminal._begin_cut).
    """
    run2 = c2.split(" ", 1)[0]
    if not run2:
        return "\n"  # blank or indented next row: only a typed break does that
    # Full: some wrap. One cell short counts as full only when the next row
    # opens wide — that cell is the one the wide character couldn't straddle
    # — and not when an ordinary word simply ended there, which is a wrap
    # that ate a space.
    if cell_width(c1) >= width or (
        cell_width(c1) == width - 1 and cell_width(run2[0]) == 2
    ):
        run1 = c1.rsplit(" ", 1)[-1]
        if run1 and cell_width(run1) + cell_width(run2) > width:
            return ""  # the runs can't be two words that ever shared a row
        return " "
    if cell_width(run2) < width and cell_width(c1) + 1 + cell_width(run2) > width:
        return " "  # the word was moved down whole; the break ate its space
    return "\n"


@dataclass(frozen=True)
class EnteredPrompt:
    """The prompt sitting unsent in an agent's input box, read off the screen.

    `text` is the logical text the user typed: rows the CLI wrapped are
    stitched back together, intentional line breaks stay newlines — so its
    length is also how many characters the input buffer holds (a break
    deletes as one character, like any other). `rows_below` is how many
    screen rows of the box sit below the cursor — what a caller needs to
    walk the cursor to the end before clearing. A box taller than the
    terminal shows (and yields) only its visible tail; the CLI scrolls it
    with no on-screen tell.
    """

    text: str
    rows_below: int

# `claude agents --json` job-lifecycle values that mean a background agent is
# no longer running, undocumented like the rest of that field. `state` is the
# lifecycle (only background jobs carry it); `status` is a busy/idle activity
# indicator — a live job merely waiting on input is idle, so it can't gate
# this. "error" is defensive; the other three are what the CLI's internals
# (2.1.220) test terminality as, exactly: there is no "cancelled"
# (cancellation lands as "stopped"), and "crashed" is transient — respawned
# or settled to "failed". An unknown value counts as still running, which
# errs toward a stale detached marker rather than attaching to (or
# double-resuming) a live job. See ClaudeProvider.background_agents.
_BACKGROUND_TERMINAL_STATES = frozenset({"done", "error", "failed", "stopped"})

# The `--mcp-config` file that gives launched sessions the app's own MCP tools
# (see mcptools/mcpserver). Set by app.py only once the whole chain — runtime
# dir, socket service, config file — is actually up; None means commands go
# out exactly as they did before the feature existed. Read-live module state,
# like sessions.CLAUDE_PROJECTS_DIR: the provider registry is module-level
# singletons built at import time, so constructor arguments can't carry this.
MCP_CONFIG_PATH: str | None = None


@dataclass(frozen=True)
class SessionOptions:
    """Optional CLI flags for a new session — chosen in the advanced new-session
    dialog, plus the worktree launch decision the window resolves per project.
    Each provider translates these into the flags it actually supports (unknowns
    are dropped)."""

    model: str = ""
    permission_mode: str = ""
    add_dirs: tuple[str, ...] = ()
    worktree: bool = False  # start the session in a fresh git worktree


@dataclass(frozen=True)
class BackgroundAgent:
    """One detached agent, as reported by the agent CLI (e.g. `claude agents`).

    `job_id` is the short id the CLI's attach/logs subcommands expect —
    distinct from the session id, which they do not accept.

    `busy` is the job's activity right now, from the same `status` field that
    can't gate terminality (a job waiting on input is idle but very much
    alive). As an activity signal it is exactly right, and it is the only one
    a background agent has: nothing it prints announces its turns, because the
    daemon spawns it without the terminal declarations the CLI's progress
    emission is gated on. See activity.BackgroundBusyWatch.
    """

    session_id: str
    job_id: str
    cwd: str
    busy: bool = False


@dataclass(frozen=True)
class ChatVariant:
    """One way to start a native chat with an agent.

    The chat is one long-lived process fed user turns over stdin. `writeable` is
    whether tools may edit; `gated` is whether each tool use can be individually
    approved. `label` is a short mode descriptor for the menu ("" for a sole
    variant).
    """

    key: str
    writeable: bool = False
    gated: bool = False
    label: str = ""


class Provider:
    """Base class. Subclasses set the class attributes and implement discover()."""

    id: str = ""
    name: str = ""
    cli: str = ""  # executable name looked up on PATH
    icon_name: str = ""  # bundled symbolic icon for sidebar rows
    supports_fork: bool = False
    # Whether this agent's CLI accepts `--mcp-config`, i.e. whether launched
    # sessions can be handed the Collins tool server. Mirrors supports_fork.
    supports_mcp_config: bool = False

    @property
    def projects_dir(self) -> Path:
        raise NotImplementedError

    def available(self) -> bool:
        return shutil.which(self.cli) is not None

    def watch_dirs(self) -> list[Path]:
        """Directories to file-monitor so the session list stays live.

        Default: the projects dir plus its immediate subdirs (where Claude writes
        its <uuid>.jsonl transcripts).
        """
        base = self.projects_dir
        dirs = [base]
        try:
            dirs += [p for p in base.iterdir() if p.is_dir()]
        except OSError:
            pass
        return dirs

    def transcripts_for_cwd(self, cwd: str) -> list[Path]:
        """All transcript files for a cwd. Empty if unsupported."""
        return []

    def latest_transcript_for_cwd(self, cwd: str) -> Path | None:
        """Newest transcript for a cwd — used to attach a freshly-started
        session's prompt detection once the agent writes its transcript. None if
        unsupported."""
        cands = self.transcripts_for_cwd(cwd)
        try:
            return max(cands, key=lambda p: p.stat().st_mtime, default=None)
        except OSError:
            return None

    def session_id_for_transcript(self, path: Path) -> str:
        """Session id a transcript file belongs to."""
        return path.stem

    def discover(self) -> list[Session]:
        raise NotImplementedError

    def _mcp_config_flag(self) -> str:
        """The ` --mcp-config <path>` suffix for launched commands, or "".

        Every command that starts or resumes a session through this provider
        carries it (the CLI registers the config on --resume/--continue too,
        verified 2026-08-08) — except `attach`, which accepts no flags at all
        and doesn't need any: it joins a process that already has its servers.
        """
        if self.supports_mcp_config and MCP_CONFIG_PATH:
            return f" --mcp-config {shlex.quote(MCP_CONFIG_PATH)}"
        return ""

    def resume_command(self, session_id: str, fork: bool = False) -> str | None:
        """Shell command to type into the terminal to resume a session."""
        cli = shutil.which(self.cli)
        if cli is None:
            return None
        cmd = f"{shlex.quote(cli)} --resume {shlex.quote(session_id)}"
        if fork and self.supports_fork:
            cmd += " --fork-session"
        return cmd + self._mcp_config_flag()

    def new_command(self, options=None) -> str | None:
        """Shell command to start a fresh session, optionally with advanced
        CLI flags (model / permission-mode / extra dirs)."""
        cli = shutil.which(self.cli)
        if cli is None:
            return None
        return " ".join([shlex.quote(cli), *self._option_flags(options)]) + self._mcp_config_flag()

    def _option_flags(self, options) -> list[str]:
        """Translate SessionOptions into this agent's CLI flags. Base: none."""
        return []

    def continue_command(self) -> str | None:
        """Shell command to continue the most recent session in the cwd."""
        cli = shutil.which(self.cli)
        return f"{shlex.quote(cli)} --continue{self._mcp_config_flag()}" if cli else None

    def session_models(self) -> list[tuple[str, str]]:
        """(flag value, label) model choices for the advanced dialog; the first
        entry's empty value means 'don't pass --model'. Empty list = no picker."""
        return []

    def model_switch_command(self, model_id: str) -> str | None:
        """The line typed into a *running* session to switch it to *model_id*
        — posted like any prompt — or None when the CLI has no mid-session
        switch (which hides the model menus entirely)."""
        return None

    def permission_modes(self) -> list[tuple[str, str]]:
        """(flag value, label) permission-mode choices; first empty = default."""
        return []

    supports_add_dir: bool = False

    def chat_variants(self) -> list[ChatVariant]:
        """The native-chat options this agent offers (empty = no chat)."""
        return []

    def chat_variant(self, key: str) -> ChatVariant | None:
        return next((v for v in self.chat_variants() if v.key == key), None)

    def chat_command(self, session_id: str = "") -> list[str] | None:
        """argv for a long-lived chat process, or None. Used by agents whose
        chat is a single process fed user turns over stdin. A non-empty
        `session_id` resumes that existing session."""
        return None

    def graceful_exit(self) -> str | None:
        """Keystrokes to feed the agent to make it exit cleanly, or None to
        force-close. Fed repeatedly while a close is pending, so whatever this
        returns has to be safe to send more than once."""
        return None

    def background_exit(self) -> str | None:
        """Text to feed the agent to detach it and keep it running in the
        background, or None if this agent can't be backgrounded."""
        return None

    def background_agents(self, include_finished: bool = False) -> list[BackgroundAgent]:
        """The agent CLI's currently-running detached sessions. Base: none.

        `include_finished` adds jobs the CLI still lists but considers over.
        Liveness callers (the sidebar's detached marker, attach-vs-resume)
        want the default; /bg-handoff pairing wants them included — pairing
        is about which transcript a job forked from, not whether it is still
        running, and a fork that finished before the pairing caught up is
        still the fork."""
        return []

    def background_watch_dir(self) -> Path | None:
        """Directory whose changes hint that background-agent state changed —
        strictly a wake-up signal for re-polling background_agents(), never a
        data source. Base: none."""
        return None

    def takes_prompt(
        self, cursor_line: str, cursor_column: int, tail_is_dim: bool = False
    ) -> bool:
        """Whether typing a prompt into the terminal right now would only ever
        land in an empty input box — evidence being the CLI's own screen, as the
        terminal has it: the line the cursor is on, where in it it sits, and
        whether everything from there to the end of the line is drawn dim (see
        vtehtml.is_dim_run), which is how a CLI marks text as its own ghost
        suggestion rather than the user's.

        Collins types into a terminal it doesn't otherwise speak for (the PR
        menu's "Address the CI errors"), so this is the check that keeps a
        prompt out of a half-written line, and out of whatever *else* an agent
        might be waiting on — a permission dialog answers Enter too.

        Base agents can't tell, so they say no rather than guess: an unknown
        screen is exactly the one worth not typing into."""
        return False

    def worktree_exit_prompt(self, screen_text: str) -> str | None:
        """Keystrokes that accept this screen's "leaving a worktree" dialog
        if it's showing right now, or None if it isn't.

        Only meant for the graceful-close flow, which never touches the
        arrow keys — so whatever this returns has to assume the dialog's own
        default is still selected, not pick an option itself. Base agents
        have no such dialog to detect."""
        return None

    def entered_prompt(
        self, rows: list[str], cursor_index: int, columns: int
    ) -> EnteredPrompt | None:
        """The prompt typed (and not yet sent) into this agent's input box,
        reconstructed from the visible screen — *rows* are the screen's
        rows, *cursor_index* the one the cursor is on. None when the screen
        doesn't show an input box with the cursor in it. Base agents have no
        input box Collins knows how to read."""
        return None

    def clear_prompt_keys(self, prompt: EnteredPrompt) -> str | None:
        """Keystrokes that erase *prompt* — the entered_prompt read of this
        same screen — from the input box, or None if this agent's box can't
        be cleared safely. Base agents have no box to clear."""
        return None

    def file_reference(
        self, path: str, cwd: str | None, start_line: int = 0, end_line: int = 0
    ) -> str | None:
        """The token that, typed into this agent's input box, makes it read
        *path* — narrowed to `start_line`..`end_line` (1-based, inclusive;
        0 = the whole file) when the agent's syntax can carry a range.
        None when there is no such syntax: base agents have no input box
        Collins knows how to write a file mention into."""
        return None

    def parse_details(self, path: Path) -> SessionDetails:
        raise NotImplementedError


class ClaudeProvider(Provider):
    id = "claude"
    name = "Claude Code"
    cli = "claude"
    icon_name = "agent-claude-symbolic"
    supports_fork = True
    supports_mcp_config = True

    @property
    def projects_dir(self) -> Path:
        # Read live so tests/demos can override sessions.CLAUDE_PROJECTS_DIR.
        return sessions.CLAUDE_PROJECTS_DIR

    def graceful_exit(self) -> str | None:
        # Ctrl+C twice — the CLI's own quit keystroke, and the one exit that
        # doesn't depend on which screen it is showing. A typed /exit only
        # means anything at the prompt: on the trust dialog, a permission
        # prompt or the session list it is just text, either garbling an
        # answer or leaving the CLI parked there until the force-close.
        #
        # Both bytes go in one write on purpose. The "Press Ctrl-C again to
        # exit" window shuts after ~2s, and a lone Ctrl+C only re-arms it, so
        # a pair split across two feeds would never land the exit.
        #
        # Mid-turn the first pair spends itself interrupting the agent and
        # clearing its input box rather than exiting; the caller's nudges
        # re-feed the pair, and the next one exits (measured: ~2.5s, well
        # inside _poll_graceful's window).
        return "\x03\x03"

    def background_exit(self) -> str | None:
        return "/bg\r"

    def background_watch_dir(self) -> Path | None:
        # The CLI keeps one directory per background job here. Undocumented —
        # never parse its contents; changes just wake up the status poller.
        return Path.home() / ".claude" / "jobs"

    def takes_prompt(
        self, cursor_line: str, cursor_column: int, tail_is_dim: bool = False
    ) -> bool:
        """Claude Code's prompt, read off the screen.

        The CLI draws its input as ``❯`` and a no-break space, then whatever
        has been typed; empty, it shows a suggestion in that space, or nothing
        at all just after a prompt was sent. Every other thing it waits at —
        the trust dialog, a permission prompt — draws its own marker (an
        indented ``❯ 1. Yes, …``, with an ordinary space), so requiring this
        exact opening is what keeps a typed prompt from answering a question
        nobody read.

        The cursor is the second half of the test, and the half that catches a
        line still being written: on an empty input it sits immediately after
        the marker. That alone would be fooled by a cursor sent home over
        typed text (Ctrl+A), so what follows the marker has to look untyped
        as well.

        "Untyped" is a question about how the line was *drawn*, not what it
        says. The suggestion is ghost text — Tab fills it in, Enter sends
        nothing — and the CLI prints it dim (``ESC[2m``) precisely to say so,
        which the terminal reports as *tail_is_dim*. Only the oldest of these,
        the ``Try "…"`` opener, is recognisable by its words; the rest are
        written for the session at hand ("close both PRs and delete the
        branches"), and reading those as typed text is what greyed a resting
        session's prompt actions out.
        """
        if not cursor_line.startswith(_PROMPT_MARK):
            return False
        if cursor_column != len(_PROMPT_MARK):
            return False
        rest = cursor_line[len(_PROMPT_MARK) :].strip()
        return not rest or tail_is_dim or rest.startswith(_PROMPT_HINT)

    def entered_prompt(
        self, rows: list[str], cursor_index: int, columns: int
    ) -> EnteredPrompt | None:
        """Claude Code's input box, read back into the text the user typed.

        The box is the rows from the one opening with the prompt mark down
        to the rule row under it; the cursor sits inside it whenever it
        exists (a permission dialog or a menu draws no mark row, and the
        walk up from the cursor hits the box's own rule first — so both
        come back None rather than someone else's text). Each row's first
        two cells are frame, not text; what stood between two rows is
        _wrap_join's call — except that a mid-box row keeping trailing
        space cells settles it first: a wrap erases to the row's end (its
        break space is consumed, cells past it never written), so trailing
        cells only survive where a typed break's backslash was drawn.
        They're stripped from the text either way — the backslash was
        never in the buffer, and only the last row can carry trailing
        spaces the user really typed.

        Callers gate on takes_prompt first: an *empty* box showing a dim
        ghost suggestion reads exactly like typed text from here.
        """
        if not 0 <= cursor_index < len(rows):
            return None
        mark = None
        for i in range(cursor_index, -1, -1):
            if rows[i].startswith(_PROMPT_MARK):
                mark = i
                break
            if not _box_body_row(rows[i]):
                return None
        if mark is None:
            return None
        end = len(rows) - 1
        for i in range(mark + 1, len(rows)):
            if _box_rule(rows[i]) or not _box_body_row(rows[i]):
                end = i - 1
                break
        if cursor_index > end:
            return None
        raw = [row[_BOX_PREFIX_CELLS:] for row in rows[mark : end + 1]]
        contents = [*(c.rstrip(" ") for c in raw[:-1]), raw[-1]]
        width = columns - _BOX_MARGIN
        text = contents[0]
        for i, c2 in enumerate(contents[1:]):
            c1 = contents[i]
            broke = raw[i] != c1  # trailing cells survived: a typed break
            text += ("\n" if broke else _wrap_join(c1, c2, width)) + c2
        return EnteredPrompt(text=text, rows_below=end - cursor_index)

    def clear_prompt_keys(self, prompt: EnteredPrompt) -> str | None:
        """Erase the box: walk the cursor to the end (Down per row below it —
        extras are no-ops with text in the box, probed — then Ctrl+E to the
        line's end), and one backspace per character; a line break deletes
        as one. All in one write: the pty stream keeps the order.

        Backspaces rather than the Esc-Esc clear on purpose: Esc interrupts
        a running turn, and the box takes typed text mid-turn — exactly when
        cutting it must not cost the user their agent's work. A backspace
        never means anything but the box, and one extra at an emptied box is
        a no-op — though none are sent: a real trailing space dropped by the
        stray-cell strip stays behind rather than risk eating hidden rows of
        a box too tall for the screen (see EnteredPrompt)."""
        return "\x1b[B" * prompt.rows_below + "\x05" + "\x7f" * len(prompt.text)

    def file_reference(
        self, path: str, cwd: str | None, start_line: int = 0, end_line: int = 0
    ) -> str | None:
        """Claude Code's @-mention, with its native line-range suffix.

        Verified against the CLI (2026-08-02): `@path#L2-4` and `@path#L5`
        attach exactly those lines, but a column suffix (`#L2C3-L4C6`) is
        not parsed — the whole file gets attached and the range is silently
        lost — so callers must round a partial-line selection outward to
        whole lines before calling. A path containing whitespace only
        survives the mention tokenizer quoted (`@"my file.txt"#L2-4`);
        backslash-escaping the space does not work.

        Relative paths resolve against the CLI's cwd *now*, not where the
        session started, so the path is shown relative to *cwd* when it sits
        inside it and absolute otherwise — an agent that has cd'd into a
        worktree still gets a working reference to a file outside it.

        A name carrying control characters gets None instead of a token:
        the tty acts on those bytes before any tokenizer sees them — a
        CR/LF would submit whatever is sitting in the input box, an ESC
        could open a terminal control sequence — and no quoting defuses
        that. Repo content is untrusted at first contact (the standing
        rule), and file names are repo content.
        """
        p = Path(path)
        if cwd:
            try:
                p = p.relative_to(cwd)
            except ValueError:
                pass
        token = str(p)
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in token):
            return None
        if any(ch.isspace() for ch in token):
            token = f'"{token}"'
        reference = f"@{token}"
        if start_line > 0:
            reference += f"#L{start_line}"
            if end_line > start_line:
                reference += f"-{end_line}"
        return reference

    def worktree_exit_prompt(self, screen_text: str) -> str | None:
        """Claude's confirmation before leaving a worktree session, read off
        the same live screen `takes_prompt` reads — this dialog, like the
        trust and permission dialogs, owns the terminal without writing
        anything to the transcript, so the screen is the only place to see
        it. Enter accepts whichever item is highlighted; the graceful-close
        flow that calls this never sends an arrow key, so that's always
        "Keep worktree", the dialog's own default and first item — the
        selection marker sitting right before it is what confirms this is
        the dialog actually showing, not just its text mentioned somewhere
        higher up the screen. "Remove worktree" is always the dialog's other
        option, so requiring it too costs nothing and rules out an unrelated
        screen that merely puts something else after a bare ❯."""
        if not _WORKTREE_EXIT_SELECTED_RE.search(screen_text):
            return None
        if _WORKTREE_EXIT_OTHER_OPTION not in screen_text:
            return None
        return "\r"

    def resume_command(self, session_id: str, fork: bool = False) -> str | None:
        # Attach-first: if the session is still running detached (e.g. after
        # /bg), `claude attach` reconnects to the live process instead of
        # starting a new foreground turn over the transcript. Attach only
        # accepts the daemon's short job id — the full session id gets
        # "No job matching". Forks always resume: attach can't create a
        # new session.
        #
        # Finished jobs count too: the daemon refuses `--resume` for ANY
        # session id it still lists ("Session … is currently running as a
        # background agent"), and a finished job stays listed — process
        # resident, state "done" — indefinitely (observed on 2.1.220). Until
        # the daemon lets go of the id, attach is the only door back into the
        # conversation, and it does work on finished jobs (the CLI's own
        # error message recommends it).
        cmd = super().resume_command(session_id, fork=fork)
        if cmd is None or fork:
            return cmd
        agent = next(
            (
                a
                for a in self.background_agents(include_finished=True)
                if a.session_id == session_id
            ),
            None,
        )
        if agent is not None:
            cli = shutil.which(self.cli)
            return f"{shlex.quote(cli)} attach {shlex.quote(agent.job_id)}"
        return cmd

    def background_agents(self, include_finished: bool = False) -> list[BackgroundAgent]:
        """Detached sessions, per `claude agents --json`.

        Only `"kind": "background"` entries count — `"interactive"` ones are
        sessions open in a foreground TUI somewhere (including our own tabs),
        which attach doesn't target. Any failure (old CLI without the
        subcommand, timeout, bad JSON) means "none running" → plain resume.

        A background job the CLI itself considers finished (its own agent
        view buckets these as "Completed") keeps its process resident and
        keeps showing up here with `"kind": "background"` regardless of
        `--all` — only `state` says it is actually done. Left uncounted,
        such a job never leaves `background_agents()`: the sidebar's yellow
        "running detached" guide line has no other source and no exit path
        ever clears it (see BackgroundStatusPoller). `include_finished`
        keeps them in, for the callers where finished-but-listed still
        matters: /bg-handoff pairing (see the base docstring) and
        resume_command()'s attach-vs-resume choice, since the daemon refuses
        a plain `--resume` for as long as it lists the job, done or not.
        """
        cli = shutil.which(self.cli)
        if cli is None:
            return []
        try:
            # stdin must NOT be inherited: it is the terminal the app was
            # launched from, and the CLI puts any tty stdin into raw mode
            # for its whole run — restored only on a clean exit, so a
            # timeout kill here left that terminal raw/no-echo (unreadable
            # after quit). With /dev/null it never touches the tty.
            out = subprocess.run(
                [cli, "agents", "--json"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout
            agents = json.loads(out)
        except (OSError, subprocess.SubprocessError, ValueError):
            return []
        found: list[BackgroundAgent] = []
        for a in agents if isinstance(agents, list) else []:
            if not isinstance(a, dict) or a.get("kind") != "background":
                continue
            if not include_finished and a.get("state") in _BACKGROUND_TERMINAL_STATES:
                continue
            session_id = a.get("sessionId")
            job_id = a.get("id")
            if isinstance(session_id, str) and isinstance(job_id, str) and job_id:
                cwd = a.get("cwd")
                found.append(
                    BackgroundAgent(
                        session_id=session_id,
                        job_id=job_id,
                        cwd=cwd if isinstance(cwd, str) else "",
                        busy=a.get("status") == "busy",
                    )
                )
        return found

    supports_add_dir = True

    def session_models(self) -> list[tuple[str, str]]:
        # CLI aliases (version-agnostic; resolve to the current model of each tier).
        return [("opus", "Opus"), ("sonnet", "Sonnet"), ("haiku", "Haiku")]

    def model_switch_command(self, model_id: str) -> str | None:
        # The CLI's own slash command, which takes anything --model does:
        # an alias or a full model id.
        return f"/model {model_id}"

    def permission_modes(self) -> list[tuple[str, str]]:
        return [
            ("plan", "Plan (read-only)"),
            ("acceptEdits", "Accept edits"),
            ("bypassPermissions", "Bypass permissions"),
        ]

    def _option_flags(self, options) -> list[str]:
        if not options:
            return []
        out: list[str] = []
        if options.model:
            out += ["--model", shlex.quote(options.model)]
        if options.permission_mode:
            out += ["--permission-mode", shlex.quote(options.permission_mode)]
        for d in options.add_dirs:
            out += ["--add-dir", shlex.quote(d)]
        if options.worktree:
            out.append("-w")
        return out

    def chat_variants(self) -> list[ChatVariant]:
        if shutil.which(self.cli) is None:
            return []
        # One variant: writeable, with per-tool approval cards (the control
        # protocol gates every Edit/Write/Bash).
        return [ChatVariant(key="default", writeable=True, gated=True)]

    def chat_command(self, session_id: str = "") -> list[str] | None:
        # Headless stream-json chat over stdio. --verbose is required by the CLI
        # alongside --output-format stream-json. `--permission-prompt-tool stdio`
        # routes every tool-use permission through the stdio control channel
        # (control_request / control_response) so the GUI can show approve/deny
        # cards — without it `default` mode silently auto-denies all tool use.
        cli = shutil.which(self.cli)
        if cli is None:
            return None
        argv = [
            cli, "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--include-partial-messages",
            "--verbose",
            "--permission-mode", "default",
            "--permission-prompt-tool", "stdio",
        ]
        if self.supports_mcp_config and MCP_CONFIG_PATH:
            argv += ["--mcp-config", MCP_CONFIG_PATH]
        if session_id:
            argv += ["--resume", session_id]
        return argv

    def transcripts_for_cwd(self, cwd: str) -> list[Path]:
        if not cwd:
            return []
        # Claude encodes the cwd into the project dir by replacing every
        # non-alphanumeric char with '-' (e.g. /a/b_c -> -a-b-c).
        directory = self.projects_dir / re.sub(r"[^A-Za-z0-9]", "-", cwd)
        if not directory.is_dir():
            return []
        return [p for p in directory.glob("*.jsonl") if _UUID_RE.match(p.stem)]

    def discover(self) -> list[Session]:
        found: list[Session] = []
        base = self.projects_dir
        if not base.is_dir():
            return found
        # Headless title and icon-generation runs (titles.py, icongen.py)
        # write transcripts under per-run scratch projects; surfacing them
        # would re-trigger titling forever.
        for project_dir in base.iterdir():
            if not project_dir.is_dir() or is_scratch_project(project_dir.name):
                continue
            for jsonl in project_dir.glob("*.jsonl"):
                if not _UUID_RE.match(jsonl.stem):
                    continue
                try:
                    stat = jsonl.stat()
                except OSError:
                    continue
                if stat.st_size == 0:
                    continue
                cwd, preview, created = _scan_transcript(jsonl)
                # Metadata-only stubs (worktree agent runs, dead /bg forks)
                # can't be resumed and would surface phantom projects named
                # after the munged worktree path.
                if transcript_is_stub(cwd, preview):
                    continue
                state, cli_title = _scan_tail(jsonl)
                found.append(
                    Session(
                        session_id=jsonl.stem,
                        jsonl_path=jsonl,
                        cwd=cwd,
                        preview=preview,
                        mtime=stat.st_mtime,
                        created=created if created is not None else stat.st_mtime,
                        size=stat.st_size,
                        state=state,
                        provider=self.id,
                        cli_title=cli_title,
                    )
                )
        return found

    def parse_details(self, path: Path) -> SessionDetails:
        return _claude_parse_details(path)


# -- registry -----------------------------------------------------------------

ALL_PROVIDERS: list[Provider] = [ClaudeProvider()]
_BY_ID: dict[str, Provider] = {p.id: p for p in ALL_PROVIDERS}


def get_provider(provider_id: str) -> Provider:
    """Provider for an id, defaulting to Claude for unknown/legacy ids."""
    return _BY_ID.get(provider_id) or _BY_ID["claude"]


def available_providers() -> list[Provider]:
    """Providers whose CLI is installed on PATH."""
    return [p for p in ALL_PROVIDERS if p.available()]


def default_provider() -> Provider:
    """The agent a plain "new session" starts: the first installed one, which
    is Claude wherever it is on PATH. Anything that says which agent that will
    be before it runs -- the offer row's icon (see NewThreadRow) -- reads it
    from here, so the promise and the launch can't drift apart."""
    installed = available_providers()
    return installed[0] if installed else get_provider("claude")
