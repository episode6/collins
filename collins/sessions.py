# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-10. Full change history: git log for this file.

"""Session model + Claude Code transcript parsing.

Discovery is delegated to per-agent providers (see providers.py);
discover_sessions() aggregates every installed agent's sessions.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# How many recent messages the details dialog peeks at.
PEEK_MESSAGES = 12

# Override with COLLINS_PROJECTS_DIR for demos and development.
CLAUDE_PROJECTS_DIR = Path(
    os.environ.get("COLLINS_PROJECTS_DIR") or Path.home() / ".claude" / "projects"
)
CLAUDE_CONFIG = Path(os.environ.get("COLLINS_CLAUDE_CONFIG") or Path.home() / ".claude.json")

# Session transcripts are named <uuid>.jsonl; skip anything else (agent files, etc.)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

# How much of a transcript to scan for cwd/preview before giving up.
_MAX_SCAN_LINES = 50
_MAX_SCAN_BYTES = 256 * 1024


def worktree_project_root(cwd: str | None) -> str | None:
    """The repository a Claude-managed worktree belongs to, or None.

    Claude Code creates session worktrees under <repo>/.claude/worktrees/<name>,
    and its /bg fork copies re-record the worktree as the conversation's cwd. A
    session working in one still belongs to <repo>'s project — grouping it by
    its cwd's basename would surface a phantom project named after the worktree
    directory."""
    if not cwd:
        return None
    parts = Path(cwd).parts
    for i in range(1, len(parts) - 1):
        if parts[i] == ".claude" and parts[i + 1] == "worktrees":
            root = Path(*parts[:i])
            if root.name:
                return str(root)
            break
    return None


def project_name_for_cwd(cwd: str) -> str:
    """The project a working directory belongs to: the repository for
    Claude-managed worktrees, the directory's own name otherwise."""
    root = worktree_project_root(cwd)
    if root:
        return Path(root).name
    return Path(cwd).name or cwd


@dataclass
class Session:
    session_id: str
    jsonl_path: Path
    cwd: str | None  # project directory recorded in the transcript
    preview: str  # first user message, truncated
    mtime: float  # last activity (file mtime)
    created: float = 0.0  # session start (first transcript timestamp; mtime fallback)
    size: int = 0  # transcript size in bytes
    state: str = ""  # "" or "interrupted" (see _tail_state)
    provider: str = "claude"  # provider id (see providers.py)

    @property
    def project_name(self) -> str:
        if self.cwd:
            return project_name_for_cwd(self.cwd)
        return self.jsonl_path.parent.name

    @property
    def last_active(self) -> datetime:
        return datetime.fromtimestamp(self.mtime)


def session_from_file(path: Path) -> Session | None:
    """Build a Session from an arbitrary transcript .jsonl on disk (for the
    'open session from file' action). Returns None if it can't be read."""
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return None
    if not path.is_file():
        return None
    cwd, preview, created = _scan_transcript(path)
    return Session(
        session_id=path.stem,
        jsonl_path=path,
        cwd=cwd,
        preview=preview,
        mtime=stat.st_mtime,
        created=created if created is not None else stat.st_mtime,
        size=stat.st_size,
        state="",
        provider="claude",
    )


def _extract_text(content) -> str:
    """Message content is either a plain string or a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return " ".join(p for p in parts if p)
    return ""


def _parse_timestamp(value) -> float | None:
    """Epoch seconds from a transcript entry's ISO-8601 timestamp, or None."""
    if not isinstance(value, str):
        return None
    try:
        # fromisoformat() can't parse a trailing "Z" until Python 3.11.
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _scan_transcript(path: Path) -> tuple[str | None, str, float | None]:
    """Return (cwd, preview, created) from the first lines of a transcript."""
    cwd: str | None = None
    preview = ""
    created: float | None = None
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
                if not isinstance(entry, dict):
                    continue
                if created is None:
                    created = _parse_timestamp(entry.get("timestamp"))
                if cwd is None and isinstance(entry.get("cwd"), str):
                    cwd = entry["cwd"]
                if not preview and entry.get("type") == "user":
                    message = entry.get("message") or {}
                    text = _extract_text(message.get("content")).strip()
                    # Skip harness-injected content (commands, reminders)
                    if text and not text.startswith("<"):
                        preview = " ".join(text.split())[:120]
                if cwd and preview and created is not None:
                    break
    except OSError:
        pass
    return cwd, preview, created


def transcript_is_stub(cwd: str | None, preview: str) -> bool:
    """Whether a transcript's scan results mark it as a metadata-only stub.

    Claude leaves such transcripts (ai-title / agent-name lines only — no
    cwd, no user message) behind for worktree agent runs, and for /bg
    forks whose background agent exited before the conversation copy was
    written. They can't be resumed, so discovery skips them.
    """
    return cwd is None and not preview


def is_discoverable_transcript(path: Path) -> bool:
    """Whether a scan would surface `path` as a session row: a non-empty
    transcript file that isn't a metadata-only stub. Mirrors the filters in
    ClaudeProvider.discover()."""
    try:
        if path.stat().st_size == 0:
            return False
    except OSError:
        return False
    cwd, preview, _created = _scan_transcript(path)
    return not transcript_is_stub(cwd, preview)


def first_message_uuid(path: Path) -> str | None:
    """The uuid of a transcript's first user/assistant message, or None.

    Claude's /bg copies the conversation into a new session id verbatim —
    message uuids included — so a matching first uuid identifies two
    transcripts as the same conversation (used to pair a backgrounded
    session with its fork).
    """
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
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") in ("user", "assistant") and isinstance(
                    entry.get("uuid"), str
                ):
                    return entry["uuid"]
    except OSError:
        pass
    return None


_TAIL_BYTES = 64 * 1024


def _read_tail(path: Path) -> str:
    """The last _TAIL_BYTES of a transcript, decoded; "" if unreadable."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > _TAIL_BYTES:
                fh.seek(-_TAIL_BYTES, os.SEEK_END)
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def resume_cwd(session: Session) -> str | None:
    """Where to start the CLI when resuming: the last cwd recorded in the
    transcript, since the agent may have left the project directory
    mid-session (e.g. moved into a git worktree). Falls back to the
    session's starting directory when the tail records no cwd or the
    directory no longer exists."""
    # Local import breaks the sessions<->chats cycle (chats imports this module
    # at load time; this runs only at call time).
    from .chats import is_degraded_chat_cwd

    cwd: str | None = None
    for line in _read_tail(session.jsonl_path).splitlines():
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(entry, dict) and isinstance(entry.get("cwd"), str):
            cwd = entry["cwd"]
    if is_degraded_chat_cwd(session.cwd, cwd):
        # The tail records where this chat was pushed when its own directory
        # went missing, not a move it chose. Resuming there would pin it to
        # the fallback (or $HOME) for good; go back to its own directory,
        # which the caller recreates.
        cwd = None
    if cwd and Path(cwd).is_dir():
        return cwd
    return session.cwd


def last_worktree_state(path: Path) -> dict | None:
    """The live worktree session the transcript currently records, or None.

    The CLI appends a `worktree-state` record on every launch and resume; a
    session that left its worktree — or was relocated out of it because the
    directory went missing — gets a final record with `worktreeSession: null`.
    Only the last record counts, so a relocated session reads as having no
    worktree rather than the one it lost.
    """
    state: dict | None = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                # Cheap prefilter: the records are rare and small, the
                # transcript can be megabytes.
                if '"worktree-state"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(entry, dict) and entry.get("type") == "worktree-state":
                    session = entry.get("worktreeSession")
                    state = session if isinstance(session, dict) else None
    except OSError:
        return None
    return state


def recreatable_worktree(jsonl_path: str | Path | None, cwd: str) -> dict | None:
    """The worktree state a missing session cwd can be recreated from, or None.

    Exiting the CLI reaps a session worktree it considers untouched — no
    changes, no commits — deleting both the directory and its `worktree-*`
    branch, so resuming later silently relocates the session to the repository
    root. When the missing cwd is exactly the worktree the transcript still
    records as current and the repository is still there, the worktree can be
    put back before relaunching: a recreated worktree at the same path, branch
    and base commit resumes seamlessly (verified against CLI 2.1.226).
    """
    if not jsonl_path:
        return None
    root = worktree_project_root(cwd)
    if root is None or not Path(root, ".git").exists():
        return None
    state = last_worktree_state(Path(jsonl_path))
    if not state:
        return None
    path = state.get("worktreePath")
    if not isinstance(path, str) or os.path.normpath(path) != os.path.normpath(cwd):
        return None
    if not (state.get("worktreeBranch") and state.get("originalHeadCommit")):
        return None
    return state


def recreate_worktree(state: dict) -> bool:
    """Re-create a reaped session worktree at its recorded path, branch and
    base commit. True when the directory exists afterwards. Runs git
    subprocesses — call off the main loop."""
    path = str(state["worktreePath"])
    root = worktree_project_root(path)
    if root is None:
        return False

    def add(*args: str) -> bool:
        try:
            return (
                subprocess.run(
                    ["git", "-C", root, "worktree", "add", *args],
                    capture_output=True,
                    timeout=60,
                ).returncode
                == 0
            )
        except (OSError, subprocess.TimeoutExpired):
            return False

    branch = str(state["worktreeBranch"])
    # -f, because git refuses a path it still has registered — a worktree
    # deleted behind git's back rather than reaped by the CLI.
    if not add("-f", "-b", branch, path, str(state["originalHeadCommit"])):
        # The branch survived (the -b form refuses to clobber one), so it may
        # hold commits the base doesn't: check it back out instead.
        if not add("-f", path, branch):
            return False
    return Path(path).is_dir()


def _tail_state(path: Path) -> str:
    """Cheaply read the transcript's tail to classify its state.

    - "interrupted": the last event was the user stopping Claude mid-task.
    - "" otherwise.

    Anything after the interruption — the agent picking back up, or the user
    saying something else — means the session moved on, so the marker only
    stands when it is the last thing in the transcript.
    """
    blob = _read_tail(path)

    latest: str | None = None  # "other" or "interrupted"
    for line in blob.splitlines():
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue  # likely a partial first line from the tail window
        if not isinstance(entry, dict):
            continue
        text = _extract_text((entry.get("message") or {}).get("content")).strip()
        if not text:
            continue
        if "[Request interrupted by user" in text:
            latest = "interrupted"
        elif entry.get("type") == "assistant":
            latest = "other"
        elif entry.get("type") == "user" and not text.startswith("<"):
            latest = "other"  # a real user reply, not a tool result / command

    return "interrupted" if latest == "interrupted" else ""


def discover_sessions() -> list[Session]:
    """All sessions from every installed agent, newest activity first."""
    # Local import breaks the sessions<->providers cycle (providers imports the
    # parsing helpers above at module load; this runs only at call time).
    from .providers import available_providers

    sessions: list[Session] = []
    for provider in available_providers():
        sessions.extend(provider.discover())
    sessions.sort(key=lambda s: s.mtime, reverse=True)
    return sessions


@dataclass
class SessionDetails:
    """Full-transcript statistics for the details dialog."""

    user_messages: int = 0
    assistant_messages: int = 0
    tool_calls: int = 0
    models: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    first_timestamp: str | None = None
    last_timestamp: str | None = None
    file_size: int = 0
    # Recent (role, text) messages, oldest first, for the transcript peek.
    messages: list[tuple[str, str]] = field(default_factory=list)
    # MCP server name -> number of tool calls in this session.
    mcp_tools: dict[str, int] = field(default_factory=dict)


def parse_details(path: Path) -> SessionDetails:
    """Scan the whole transcript. Run off the main thread for big files."""
    details = SessionDetails()
    models: set[str] = set()
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
                ts = entry.get("timestamp")
                if isinstance(ts, str):
                    details.first_timestamp = details.first_timestamp or ts
                    details.last_timestamp = ts
                etype = entry.get("type")
                message = entry.get("message") or {}
                content = message.get("content")
                if etype == "user":
                    text = _extract_text(content).strip()
                    # Skip tool results and harness-injected content
                    if text and not text.startswith("<"):
                        details.user_messages += 1
                        recent.append(("user", " ".join(text.split())[:500]))
                elif etype == "assistant":
                    details.assistant_messages += 1
                    model = message.get("model")
                    if isinstance(model, str) and not model.startswith("<"):
                        models.add(model)
                    usage = message.get("usage") or {}
                    details.input_tokens += usage.get("input_tokens") or 0
                    details.output_tokens += usage.get("output_tokens") or 0
                    details.cache_read_tokens += usage.get("cache_read_input_tokens") or 0
                    if isinstance(content, list):
                        for b in content:
                            if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                                continue
                            details.tool_calls += 1
                            name = b.get("name")
                            # MCP tools are named mcp__<server>__<tool>
                            if isinstance(name, str) and name.startswith("mcp__"):
                                parts = name.split("__")
                                if len(parts) >= 2 and parts[1]:
                                    server = parts[1]
                                    details.mcp_tools[server] = details.mcp_tools.get(server, 0) + 1
                    text = _extract_text(content).strip()
                    if text:
                        recent.append(("assistant", " ".join(text.split())[:500]))
    except OSError:
        pass
    details.models = sorted(models)
    details.messages = list(recent)
    return details


def export_markdown(path: Path, title: str, session_id: str, cwd: str | None) -> str:
    """Render a whole transcript to Markdown. Run off the main thread."""
    out: list[str] = [f"# {title}", ""]
    meta = [f"- **Session:** `{session_id}`"]
    if cwd:
        meta.append(f"- **Project:** `{cwd}`")
    first_ts: str | None = None
    last_ts: str | None = None
    turns: list[str] = []

    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(entry, dict):
                    continue
                ts = entry.get("timestamp")
                if isinstance(ts, str):
                    first_ts = first_ts or ts
                    last_ts = ts
                etype = entry.get("type")
                message = entry.get("message") or {}
                content = message.get("content")
                if etype == "user":
                    text = _extract_text(content).strip()
                    if text and not text.startswith("<"):
                        turns.append(f"### You\n\n{text}")
                elif etype == "assistant":
                    text = _extract_text(content).strip()
                    tools = []
                    if isinstance(content, list):
                        tools = [
                            b["name"]
                            for b in content
                            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name")
                        ]
                    if not text and not tools:
                        continue
                    block = "### Claude"
                    if text:
                        block += f"\n\n{text}"
                    if tools:
                        used = ", ".join(f"`{name}`" for name in tools)
                        block += f"\n\n*Used {used}*"
                    turns.append(block)
    except OSError:
        pass

    if first_ts:
        meta.append(f"- **Created:** {first_ts}")
    if last_ts:
        meta.append(f"- **Last activity:** {last_ts}")
    out.extend(meta)
    out.append("\n---\n")
    out.append("\n\n".join(turns) if turns else "_No messages._")
    out.append("")
    return "\n".join(out)


def configured_mcp_servers(cwd: str | None) -> list[str]:
    """MCP servers available to a session: global servers from ~/.claude.json
    plus any configured for the session's project directory. Read-only."""
    try:
        data = json.loads(CLAUDE_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    servers: set[str] = set()
    global_servers = data.get("mcpServers")
    if isinstance(global_servers, dict):
        servers.update(global_servers)
    projects = data.get("projects")
    if cwd and isinstance(projects, dict):
        project = projects.get(cwd)
        if isinstance(project, dict) and isinstance(project.get("mcpServers"), dict):
            servers.update(project["mcpServers"])
    return sorted(servers)


@dataclass
class McpServer:
    name: str
    summary: str  # short description: transport + command/url


@dataclass
class McpConfig:
    """Read-only snapshot of MCP servers configured in ~/.claude.json."""

    global_servers: list[McpServer] = field(default_factory=list)
    # (project_path, servers) for projects that define their own servers
    project_servers: list[tuple[str, list[McpServer]]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.global_servers and not self.project_servers


def _summarize_mcp(config: object) -> str:
    if not isinstance(config, dict):
        return ""
    transport = config.get("type")
    url = config.get("url")
    if url:
        return f"{transport or 'http'} · {url}"
    command = config.get("command")
    if command:
        args = config.get("args") or []
        joined = " ".join(str(a) for a in args) if isinstance(args, list) else ""
        return f"{transport or 'stdio'} · {command} {joined}".strip()
    return transport or "—"


def _servers_from(mapping: object) -> list[McpServer]:
    if not isinstance(mapping, dict):
        return []
    return [McpServer(name, _summarize_mcp(cfg)) for name, cfg in sorted(mapping.items())]


def read_mcp_config() -> McpConfig:
    """All configured MCP servers — global and per-project. Read-only."""
    try:
        data = json.loads(CLAUDE_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return McpConfig()
    if not isinstance(data, dict):
        return McpConfig()
    config = McpConfig(global_servers=_servers_from(data.get("mcpServers")))
    projects = data.get("projects")
    if isinstance(projects, dict):
        for path, project in sorted(projects.items()):
            if isinstance(project, dict):
                servers = _servers_from(project.get("mcpServers"))
                if servers:
                    config.project_servers.append((path, servers))
    return config
