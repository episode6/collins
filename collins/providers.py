# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-26. Full change history: git log for this file.

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
from .sessions import (
    _UUID_RE,
    Session,
    SessionDetails,
    _scan_transcript,
    _tail_state,
)
from .sessions import parse_details as _claude_parse_details
from .titles import scratch_project_dirname


@dataclass(frozen=True)
class SessionOptions:
    """Optional CLI flags chosen in the advanced new-session dialog. Each provider
    translates these into the flags it actually supports (unknowns are dropped)."""

    model: str = ""
    permission_mode: str = ""
    add_dirs: tuple[str, ...] = ()


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

    def resume_command(self, session_id: str, fork: bool = False) -> str | None:
        """Shell command to type into the terminal to resume a session."""
        cli = shutil.which(self.cli)
        if cli is None:
            return None
        cmd = f"{shlex.quote(cli)} --resume {shlex.quote(session_id)}"
        if fork and self.supports_fork:
            cmd += " --fork-session"
        return cmd

    def new_command(self, options=None) -> str | None:
        """Shell command to start a fresh session, optionally with advanced
        CLI flags (model / permission-mode / extra dirs)."""
        cli = shutil.which(self.cli)
        if cli is None:
            return None
        return " ".join([shlex.quote(cli), *self._option_flags(options)])

    def _option_flags(self, options) -> list[str]:
        """Translate SessionOptions into this agent's CLI flags. Base: none."""
        return []

    def continue_command(self) -> str | None:
        """Shell command to continue the most recent session in the cwd."""
        cli = shutil.which(self.cli)
        return f"{shlex.quote(cli)} --continue" if cli else None

    def session_models(self) -> list[tuple[str, str]]:
        """(flag value, label) model choices for the advanced dialog; the first
        entry's empty value means 'don't pass --model'. Empty list = no picker."""
        return []

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
        """Text to feed the agent to make it exit cleanly, or None to force-close."""
        return None

    def answer_keystrokes(self, questions: list, option_index: int) -> str | None:
        """Keystrokes that select option `option_index` of a structured prompt,
        or None if this agent/shape can't be auto-answered (→ fall back to the
        terminal). Base agents can't auto-answer."""
        return None

    def parse_details(self, path: Path) -> SessionDetails:
        raise NotImplementedError


class ClaudeProvider(Provider):
    id = "claude"
    name = "Claude Code"
    cli = "claude"
    icon_name = "agent-claude-symbolic"
    supports_fork = True

    @property
    def projects_dir(self) -> Path:
        # Read live so tests/demos can override sessions.CLAUDE_PROJECTS_DIR.
        return sessions.CLAUDE_PROJECTS_DIR

    def graceful_exit(self) -> str | None:
        return "/exit\r"

    def resume_command(self, session_id: str, fork: bool = False) -> str | None:
        # Attach-first: if the session is still running detached (e.g. after
        # /bg), `claude attach` reconnects to the live process instead of
        # starting a new foreground turn over the transcript. Forks always
        # resume: attach can't create a new session.
        cmd = super().resume_command(session_id, fork=fork)
        if cmd is None or fork:
            return cmd
        if self._running_in_background(session_id):
            cli = shutil.which(self.cli)
            return f"{shlex.quote(cli)} attach {shlex.quote(session_id)}"
        return cmd

    def _running_in_background(self, session_id: str) -> bool:
        """Whether the session is running detached, per `claude agents --json`.

        Only `"kind": "background"` entries count — `"interactive"` ones are
        sessions open in a foreground TUI somewhere (including our own tabs),
        which attach doesn't target. Any failure (old CLI without the
        subcommand, timeout, bad JSON) means "not running" → plain resume.
        """
        cli = shutil.which(self.cli)
        if cli is None:
            return False
        try:
            out = subprocess.run(
                [cli, "agents", "--json"],
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout
            agents = json.loads(out)
        except (OSError, subprocess.SubprocessError, ValueError):
            return False
        return any(
            a.get("sessionId") == session_id and a.get("kind") == "background"
            for a in agents
            if isinstance(a, dict)
        )

    supports_add_dir = True

    def session_models(self) -> list[tuple[str, str]]:
        # CLI aliases (version-agnostic; resolve to the current model of each tier).
        return [("opus", "Opus"), ("sonnet", "Sonnet"), ("haiku", "Haiku")]

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

    def answer_keystrokes(self, questions: list, option_index: int) -> str | None:
        # Reliable only for a single-question, single-select prompt: the first
        # option starts highlighted in Claude's TUI, so arrow-down to the target
        # and submit. Multi-select / multi-question fall back to the terminal.
        if not questions or len(questions) != 1:
            return None
        q = questions[0]
        if q.get("multiSelect"):
            return None
        options = q.get("options") or []
        if not 0 <= option_index < len(options):
            return None
        return "\x1b[B" * option_index + "\r"

    def discover(self) -> list[Session]:
        found: list[Session] = []
        base = self.projects_dir
        if not base.is_dir():
            return found
        # Headless title-generation runs (titles.py) write transcripts under
        # this project; surfacing them would re-trigger titling forever.
        scratch = scratch_project_dirname()
        for project_dir in base.iterdir():
            if not project_dir.is_dir() or project_dir.name == scratch:
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
                found.append(
                    Session(
                        session_id=jsonl.stem,
                        jsonl_path=jsonl,
                        cwd=cwd,
                        preview=preview,
                        mtime=stat.st_mtime,
                        created=created if created is not None else stat.st_mtime,
                        size=stat.st_size,
                        state=_tail_state(jsonl),
                        provider=self.id,
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
