# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-30. Full change history: git log for this file.

"""Drive a headless `claude -p` session over stream-json stdio.

This is the *correct* plumbing for a native chat (vs. tailing the transcript): a
long-lived `claude -p --input-format stream-json --output-format stream-json
--include-partial-messages --verbose` child. We write one user-message JSON line
per turn to its stdin (the process stays alive and retains context across turns)
and parse its stdout stream into normalized `Event`s — live text deltas, tool
calls, quota status, and turn completion.

`StreamParser` is pure (bytes in → Events out) and unit-tested without a process.
`ChatSession` owns the subprocess + reader thread and calls `on_event` from that
thread — the GTK view wraps the callback with `GLib.idle_add` to hop to the main
loop, so this module stays GTK-free.
"""

from __future__ import annotations

import json
import signal
import subprocess
import threading
from dataclasses import dataclass

from . import chats


@dataclass
class Event:
    kind: str  # init | text | thinking | tool | permission | turn_end | rate_limit | error | exit
    text: str = ""
    session_id: str = ""
    tool_name: str = ""
    cost_usd: float = 0.0
    resets_at: int = 0
    rate_status: str = ""
    # permission events only:
    request_id: str = ""
    tool_input: dict | None = None
    tool_use_id: str = ""


class _LineParser:
    """Buffers raw stdout bytes and dispatches one decoded JSON object per line.

    Handles partial-line buffering across feeds; subclasses implement `_dispatch`
    to turn each decoded object into normalized Events.
    """

    def __init__(self) -> None:
        self._buf = b""

    def feed(self, data: bytes) -> list[Event]:
        self._buf += data
        parts = self._buf.split(b"\n")
        self._buf = parts.pop()  # trailing partial line
        out: list[Event] = []
        for raw in parts:
            out.extend(self._line(raw))
        return out

    def _line(self, raw: bytes) -> list[Event]:
        text = raw.strip()
        if not text:
            return []
        try:
            entry = json.loads(text.decode("utf-8", "replace"))
        except (json.JSONDecodeError, ValueError):
            return []
        if not isinstance(entry, dict):
            return []
        return self._dispatch(entry)

    def _dispatch(self, e: dict) -> list[Event]:
        raise NotImplementedError


class StreamParser(_LineParser):
    """Parse Claude's `claude -p` stream-json output into normalized Events.

    Tracks each content block's type so a `content_block_delta` can be routed to
    text vs. thinking vs. tool input.
    """

    def __init__(self) -> None:
        super().__init__()
        self._block_types: dict[int, str] = {}

    def _dispatch(self, e: dict) -> list[Event]:
        etype = e.get("type")
        if etype == "system" and e.get("subtype") == "init":
            return [Event("init", session_id=e.get("session_id", "") or "")]
        if etype == "stream_event":
            return self._stream(e.get("event") or {})
        if etype == "control_request":
            req = e.get("request") or {}
            if req.get("subtype") == "can_use_tool":
                return [
                    Event(
                        "permission",
                        request_id=e.get("request_id", "") or "",
                        tool_name=req.get("display_name") or req.get("tool_name", "") or "",
                        tool_input=req.get("input") or {},
                        text=req.get("description", "") or "",
                        tool_use_id=req.get("tool_use_id", "") or "",
                    )
                ]
            return []
        if etype == "rate_limit_event":
            info = e.get("rate_limit_info") or {}
            return [
                Event(
                    "rate_limit",
                    rate_status=info.get("status", "") or "",
                    resets_at=int(info.get("resetsAt", 0) or 0),
                )
            ]
        if etype == "result":
            if e.get("is_error"):
                msg = e.get("result") or e.get("api_error_status") or "error"
                return [Event("error", text=str(msg))]
            return [
                Event(
                    "turn_end",
                    text=e.get("result", "") or "",
                    cost_usd=float(e.get("total_cost_usd", 0.0) or 0.0),
                )
            ]
        return []

    def _stream(self, ev: dict) -> list[Event]:
        etype = ev.get("type")
        if etype == "content_block_start":
            idx = ev.get("index", 0)
            block = ev.get("content_block") or {}
            self._block_types[idx] = block.get("type", "text")
            if block.get("type") == "tool_use":
                return [Event("tool", tool_name=block.get("name", "") or "")]
            return []
        if etype == "content_block_delta":
            delta = ev.get("delta") or {}
            dtype = delta.get("type")
            if dtype == "text_delta":
                return [Event("text", text=delta.get("text", "") or "")]
            if dtype == "thinking_delta":
                return [Event("thinking", text=delta.get("thinking", "") or "")]
            return []
        return []


class ChatSession:
    """A long-lived headless agent process driven over stream-json stdio."""

    def __init__(self, provider, cwd: str | None, on_event, resume_session_id: str = "") -> None:
        self.provider = provider
        # A chat's throwaway directory may be gone (swept, trashed, removed);
        # recreate it, and land in a disposable scratch directory rather than
        # $HOME when even that fails — see chats.fallback_chat_dir().
        self.cwd = chats.chat_cwd_or_fallback(cwd)
        self._on_event = on_event  # called from the reader thread
        self._proc: subprocess.Popen | None = None
        self._parser = StreamParser()
        self.session_id = resume_session_id

    def start(self) -> None:
        argv = self.provider.chat_command(self.session_id)
        if not argv:
            self._on_event(Event("error", text=f"`{self.provider.cli}` not found in PATH"))
            return
        try:
            self._proc = subprocess.Popen(
                argv,
                cwd=self.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
        except OSError as exc:
            self._on_event(Event("error", text=str(exc)))
            return
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for chunk in iter(lambda: self._proc.stdout.read(4096), b""):
            for event in self._parser.feed(chunk):
                if event.kind == "init" and event.session_id:
                    self.session_id = event.session_id
                self._on_event(event)
        code = self._proc.wait()
        self._on_event(Event("exit", text=str(code)))

    def send(self, text: str) -> None:
        if not self._proc or self._proc.poll() is not None or self._proc.stdin is None:
            return
        message = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
        try:
            self._proc.stdin.write((json.dumps(message) + "\n").encode())
            self._proc.stdin.flush()
        except OSError:
            pass

    def respond_permission(
        self,
        request_id: str,
        allow: bool,
        updated_input: dict | None = None,
        message: str = "",
    ) -> None:
        """Answer a `can_use_tool` control_request. `allow` runs the tool (with
        `updated_input` as its — possibly edited — arguments); otherwise it's
        denied and `message` is shown to the agent."""
        if not self._proc or self._proc.poll() is not None or self._proc.stdin is None:
            return
        if allow:
            inner = {"behavior": "allow", "updatedInput": updated_input or {}}
        else:
            inner = {"behavior": "deny", "message": message or "Denied by the user."}
        envelope = {
            "type": "control_response",
            "response": {"subtype": "success", "request_id": request_id, "response": inner},
        }
        try:
            self._proc.stdin.write((json.dumps(envelope) + "\n").encode())
            self._proc.stdin.flush()
        except OSError:
            pass

    def interrupt(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.send_signal(signal.SIGINT)
            except OSError:
                pass

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                if self._proc.stdin is not None:
                    self._proc.stdin.close()
            except OSError:
                pass
            self._proc.terminate()


def make_chat_session(provider, variant, cwd: str | None, on_event, resume_session_id: str = ""):
    """Build the chat session for a provider's chat variant, optionally
    resuming an existing session id."""
    return ChatSession(provider, cwd, on_event, resume_session_id)
