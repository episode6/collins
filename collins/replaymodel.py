# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-26. Full change history: git log for this file.

"""Read a whole session transcript into ordered turns for replay.

Unlike `transcript.py` (which tails a *live* session only to detect a pending
prompt), this does a one-shot full read and normalizes every conversational turn
— text messages, tool-call chips, and AskUserQuestion prompts — into a list the
replay view renders. GTK-free (unit-tested).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_NOISE_TEXTS = {"No response requested.", "(no content)"}


@dataclass
class Turn:
    role: str  # "user" | "assistant"
    kind: str  # "message" | "tool" | "question"
    text: str = ""
    tool_name: str = ""
    questions: list = field(default_factory=list)


def _clean(text: str) -> str:
    return (text or "").strip()


def _tool_summary(name: str, inp: dict) -> str:
    if not isinstance(inp, dict):
        return name
    if name in ("Edit", "Write", "Read", "NotebookEdit"):
        target = inp.get("file_path") or inp.get("notebook_path") or ""
        return f"{name} {Path(target).name}" if target else name
    if name == "Bash":
        cmd = " ".join((inp.get("command") or "").split())
        return f"Bash: {cmd[:60]}" if cmd else "Bash"
    if name in ("Grep", "Glob"):
        return f"{name} {inp.get('pattern', '')}".strip()
    if name == "Task":
        return f"Task: {inp.get('description', '')}".strip()
    return name


def _parse_claude(entry: dict) -> list[Turn]:
    typ = entry.get("type")
    content = (entry.get("message") or {}).get("content")
    turns: list[Turn] = []

    if typ == "user":
        if isinstance(content, str):
            text = _clean(content)
            if text and not text.startswith("<"):
                turns.append(Turn("user", "message", text=text))
        elif isinstance(content, list):
            texts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            text = _clean(" ".join(t for t in texts if t))
            if text and not text.startswith("<"):
                turns.append(Turn("user", "message", text=text))

    elif typ == "assistant" and isinstance(content, list):
        pending: list[str] = []

        def flush() -> None:
            text = _clean(" ".join(p for p in pending if p))
            if text and text not in _NOISE_TEXTS:
                turns.append(Turn("assistant", "message", text=text))
            pending.clear()

        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                pending.append(block.get("text", ""))
            elif btype == "tool_use":
                flush()
                name = block.get("name", "")
                if name == "AskUserQuestion":
                    turns.append(Turn("assistant", "question", tool_name=name,
                                      questions=(block.get("input") or {}).get("questions", [])))
                else:
                    turns.append(Turn("assistant", "tool", tool_name=name,
                                      text=_tool_summary(name, block.get("input") or {})))
            # "thinking" / other blocks are not replayed
        flush()

    return turns


def read_session_turns(jsonl_path: str | Path | None) -> list[Turn]:
    """Parse a whole transcript into ordered renderable turns."""
    if not jsonl_path:
        return []
    path = Path(jsonl_path)
    if not path.exists():
        return []
    turns: list[Turn] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(entry, dict):
                    turns.extend(_parse_claude(entry))
    except OSError:
        pass
    return turns
