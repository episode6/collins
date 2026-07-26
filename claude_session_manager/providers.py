"""Agent providers: each adapts one AI coding-agent CLI to the app's Session model.

A provider knows how to discover its sessions on disk, how to resume/start them
in a terminal, and how to close them cleanly. The Claude adapter wraps the
original discovery logic in sessions.py; the Cursor adapter reads cursor-agent's
transcript format (`~/.cursor/projects/<enc-cwd>/agent-transcripts/<uuid>/<uuid>.jsonl`,
top-level `role`, user text wrapped in <user_query> tags, no cwd/usage/timestamps).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from . import sessions
from .sessions import (
    _MAX_SCAN_BYTES,
    _MAX_SCAN_LINES,
    _UUID_RE,
    PEEK_MESSAGES,
    Session,
    SessionDetails,
    _extract_text,
    _scan_transcript,
    _tail_state,
)
from .sessions import parse_details as _claude_parse_details

# Cursor transcripts live under ~/.cursor/projects; override for demos/tests.
CURSOR_PROJECTS_DIR = Path(
    os.environ.get("CSM_CURSOR_PROJECTS_DIR") or Path.home() / ".cursor" / "projects"
)

_CURSOR_TAIL_BYTES = 64 * 1024
_USER_QUERY_RE = re.compile(r"</?user_query>")


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

    `transport` picks the driver: "stdin_stream" = one long-lived process fed
    user turns over stdin (Claude); "spawn_resume" = a fresh process per turn,
    resuming the same session id (Cursor). `writeable` is whether tools may edit;
    `gated` is whether each tool use can be individually approved (Claude only —
    Cursor's headless mode auto-runs tools, so its variants are ungated).
    `label` is a short mode descriptor for the menu ("" for a sole variant).
    """

    key: str
    transport: str
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
        """argv for a long-lived ("stdin_stream") chat process, or None. Used by
        agents whose chat is a single process fed user turns over stdin. A
        non-empty `session_id` resumes that existing session."""
        return None

    def chat_turn_command(
        self, variant_key: str, prompt: str, session_id: str
    ) -> list[str] | None:
        """argv for ONE turn of a "spawn_resume" chat (a fresh process per turn,
        resuming `session_id` if set). None for agents that don't use it."""
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
        return [ChatVariant(key="default", transport="stdin_stream", writeable=True, gated=True)]

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
        for project_dir in base.iterdir():
            if not project_dir.is_dir():
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
                cwd, preview = _scan_transcript(jsonl)
                found.append(
                    Session(
                        session_id=jsonl.stem,
                        jsonl_path=jsonl,
                        cwd=cwd,
                        preview=preview,
                        mtime=stat.st_mtime,
                        size=stat.st_size,
                        state=_tail_state(jsonl),
                        provider=self.id,
                    )
                )
        return found

    def parse_details(self, path: Path) -> SessionDetails:
        return _claude_parse_details(path)


class CursorProvider(Provider):
    id = "cursor"
    name = "Cursor"
    cli = "cursor-agent"
    icon_name = "agent-cursor-symbolic"
    supports_fork = False

    @property
    def projects_dir(self) -> Path:
        return CURSOR_PROJECTS_DIR

    def session_models(self) -> list[tuple[str, str]]:
        return [
            ("sonnet-4", "Sonnet 4"),
            ("sonnet-4-thinking", "Sonnet 4 (thinking)"),
            ("gpt-5", "GPT-5"),
        ]

    def permission_modes(self) -> list[tuple[str, str]]:
        # Cursor exposes read-only execution modes via --mode.
        return [("plan", "Plan (read-only)"), ("ask", "Ask (read-only)")]

    def _option_flags(self, options) -> list[str]:
        if not options:
            return []
        out: list[str] = []
        if options.model:
            out += ["--model", shlex.quote(options.model)]
        if options.permission_mode in ("plan", "ask"):
            out += ["--mode", options.permission_mode]
        return out

    def chat_variants(self) -> list[ChatVariant]:
        if shutil.which(self.cli) is None:
            return []
        # cursor-agent's headless mode can't gate tools (it auto-runs them), so
        # offer two explicit levels: a read-only Q&A chat, and a trusted chat
        # that runs edits/commands automatically.
        return [
            ChatVariant(key="ask", transport="spawn_resume", label="ask, read-only"),
            ChatVariant(
                key="trusted", transport="spawn_resume", writeable=True, label="trusted, auto-run"
            ),
        ]

    def chat_turn_command(
        self, variant_key: str, prompt: str, session_id: str
    ) -> list[str] | None:
        # cursor-agent takes the prompt as an arg; multi-turn = --resume the same
        # session id. --trust skips the workspace-trust prompt (required headless).
        cli = shutil.which(self.cli)
        if cli is None:
            return None
        argv = [cli, "-p", "--output-format", "stream-json", "--stream-partial-output", "--trust"]
        if session_id:
            argv += ["--resume", session_id]
        if variant_key == "trusted":
            argv.append("--force")  # auto-run edits/commands (no per-tool approval exists)
        else:
            argv += ["--mode", "ask"]  # read-only Q&A
        argv.append(prompt)
        return argv

    def watch_dirs(self) -> list[Path]:
        # Cursor nests transcripts one level deeper, under agent-transcripts/,
        # so watch each project's agent-transcripts dir too.
        base = self.projects_dir
        dirs = [base]
        try:
            for project_dir in base.iterdir():
                if not project_dir.is_dir():
                    continue
                dirs.append(project_dir)
                transcripts = project_dir / "agent-transcripts"
                if transcripts.is_dir():
                    dirs.append(transcripts)
        except OSError:
            pass
        return dirs

    def transcripts_for_cwd(self, cwd: str) -> list[Path]:
        if not cwd:
            return []
        directory = (
            self.projects_dir / re.sub(r"[^A-Za-z0-9]", "-", cwd).lstrip("-") / "agent-transcripts"
        )
        if not directory.is_dir():
            return []
        return list(directory.glob("*/*.jsonl"))

    def session_id_for_transcript(self, path: Path) -> str:
        # Transcripts live in a per-session dir named by the session id; the
        # file usually shares that name but may not (see discover()).
        return path.parent.name

    def discover(self) -> list[Session]:
        found: list[Session] = []
        base = self.projects_dir
        if not base.is_dir():
            return found
        for project_dir in base.iterdir():
            transcripts = project_dir / "agent-transcripts"
            if not transcripts.is_dir():
                continue
            cwd = _decode_cursor_cwd(project_dir.name)
            for sess_dir in transcripts.iterdir():
                if not sess_dir.is_dir():
                    continue
                jsonl = sess_dir / f"{sess_dir.name}.jsonl"
                if not jsonl.exists():
                    matches = list(sess_dir.glob("*.jsonl"))
                    if not matches:
                        continue
                    jsonl = matches[0]
                try:
                    stat = jsonl.stat()
                except OSError:
                    continue
                if stat.st_size == 0:
                    continue
                found.append(
                    Session(
                        session_id=sess_dir.name,
                        jsonl_path=jsonl,
                        cwd=cwd,
                        preview=_cursor_preview(jsonl),
                        mtime=stat.st_mtime,
                        size=stat.st_size,
                        state=_cursor_state(jsonl),
                        provider=self.id,
                    )
                )
        return found

    def parse_details(self, path: Path) -> SessionDetails:
        return _cursor_parse_details(path)


# -- Cursor parsing helpers ---------------------------------------------------


def _strip_user_query(text: str) -> str:
    """Cursor wraps the human turn in <user_query>…</user_query>."""
    return _USER_QUERY_RE.sub("", text).strip()


def _decode_cursor_cwd(encoded: str) -> str:
    """Reconstruct a cwd from Cursor's dash-encoded project dir name.

    'home-matt-Documents-Git-Foo' -> '/home/matt/Documents/Git/Foo'. Dashes are
    ambiguous (real directories may contain '-'), so walk the real filesystem
    greedily, preferring the longest segment that names an existing directory;
    fall back to a naive '/'-join for the part that no longer exists on disk.
    """
    parts = encoded.split("-")
    path = Path("/")
    i, n = 0, len(parts)
    while i < n:
        best_k: int | None = None
        best_acc: str | None = None
        acc = parts[i]
        k = i
        while k < n:
            if (path / acc).is_dir():
                best_k, best_acc = k, acc
            k += 1
            if k < n:
                acc = acc + "-" + parts[k]
        if best_k is None or best_acc is None:
            return str(path / "/".join(parts[i:]))
        path = path / best_acc
        i = best_k + 1
    return str(path)


def _cursor_preview(path: Path) -> str:
    """First real user message, with <user_query> tags stripped."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            read = 0
            for i, line in enumerate(fh):
                read += len(line)
                if i >= _MAX_SCAN_LINES or read > _MAX_SCAN_BYTES:
                    break
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(entry, dict) or entry.get("role") != "user":
                    continue
                text = _strip_user_query(_extract_text((entry.get("message") or {}).get("content")))
                text = " ".join(text.split())
                if text and not text.startswith("<"):
                    return text[:120]
    except OSError:
        pass
    return ""


def _cursor_state(path: Path) -> str:
    """"waiting" if the agent's last message was a question; else ""."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > _CURSOR_TAIL_BYTES:
                fh.seek(-_CURSOR_TAIL_BYTES, os.SEEK_END)
            blob = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""

    latest: str | None = None
    latest_assistant_text = ""
    for line in blob.splitlines():
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        role = entry.get("role")
        text = _extract_text((entry.get("message") or {}).get("content")).strip()
        if not text:
            continue
        if role == "assistant":
            latest = "assistant"
            latest_assistant_text = text
        elif role == "user":
            stripped = _strip_user_query(text)
            if stripped and not stripped.startswith("<"):
                latest = "user"

    if latest == "assistant" and latest_assistant_text.rstrip().endswith("?"):
        return "waiting"
    return ""


def _cursor_parse_details(path: Path) -> SessionDetails:
    """Partial details: message counts + recent peek (no tokens/models/timestamps)."""
    details = SessionDetails()
    recent: deque[tuple[str, str]] = deque(maxlen=PEEK_MESSAGES)
    try:
        details.file_size = path.stat().st_size
    except OSError:
        pass
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(entry, dict):
                    continue
                role = entry.get("role")
                text = _extract_text((entry.get("message") or {}).get("content")).strip()
                if role == "user":
                    details.user_messages += 1
                    text = _strip_user_query(text)
                elif role == "assistant":
                    details.assistant_messages += 1
                else:
                    continue
                if text:
                    recent.append((role, " ".join(text.split())))
    except OSError:
        pass
    details.messages = list(recent)
    return details


# -- registry -----------------------------------------------------------------

ALL_PROVIDERS: list[Provider] = [ClaudeProvider(), CursorProvider()]
_BY_ID: dict[str, Provider] = {p.id: p for p in ALL_PROVIDERS}


def get_provider(provider_id: str) -> Provider:
    """Provider for an id, defaulting to Claude for unknown/legacy ids."""
    return _BY_ID.get(provider_id) or _BY_ID["claude"]


def available_providers() -> list[Provider]:
    """Providers whose CLI is installed on PATH."""
    return [p for p in ALL_PROVIDERS if p.available()]
