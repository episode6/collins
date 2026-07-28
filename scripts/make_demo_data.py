#!/usr/bin/env python3
# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-28. Full change history: git log for this file.
"""Generate fake Claude Code sessions for screenshots and demos.

Usage:
    python3 scripts/make_demo_data.py
    XDG_CONFIG_HOME=/tmp/collins-demo/config \\
        COLLINS_PROJECTS_DIR=/tmp/collins-demo/projects \\
        COLLINS_CLAUDE_CONFIG=/tmp/collins-demo/claude.json \\
        python3 -m collins
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

ROOT = Path("/tmp/collins-demo")

# (project, preview prompt, age, custom name|None, favorite, emoji|None, mcp server used|None)
SESSIONS = [
    ("portfolio-website", "Redesign the hero section with a dark theme and subtle animations",
     300, "Homepage redesign", True, "🎨", None),
    ("portfolio-website", "Fix the contact form validation, emails with a plus sign are rejected",
     7200, None, False, None, None),
    ("todo-api", "Add JWT authentication to the API with refresh tokens",
     5400, "JWT auth", True, "🔐", "postgres"),
    ("todo-api", "Write integration tests for the todo endpoints", 86400, None, False, None, None),
    ("dotfiles", "Migrate my neovim configuration from vimscript to lua",
     172800, None, False, None, None),
    ("snake-game", "Build a snake game in pygame with a persistent high score list",
     259200, "Snake", False, "🐍", None),
    ("data-pipeline", "Optimize the CSV import, it is too slow on files over 1GB",
     518400, None, False, None, "linear"),
]

# Fake MCP servers, so the MCP browser and details show realistic-but-fake data.
MCP_GLOBAL = {
    "github": {"type": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]},
    "linear": {"type": "http", "url": "https://mcp.linear.app/sse"},
    "filesystem": {"type": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem"]},
}
MCP_PER_PROJECT = {
    "/home/demo/projects/todo-api": {
        "postgres": {"type": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-postgres"]},
    },
}


def make_transcript(cwd: str, prompt: str, timestamp: float, mcp_server: str | None) -> tuple[str, str]:
    iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(timestamp))
    session_id = str(uuid.uuid4())
    content = [{"type": "text", "text": "On it — here's the plan, then I'll start."}]
    if mcp_server:
        content.append(
            {"type": "tool_use", "id": "t1", "name": f"mcp__{mcp_server}__query", "input": {}}
        )
    lines = [
        {"type": "mode", "sessionId": session_id},
        {
            "type": "user",
            "cwd": cwd,
            "sessionId": session_id,
            "timestamp": iso,
            "message": {"role": "user", "content": prompt},
        },
        {
            "type": "assistant",
            "cwd": cwd,
            "sessionId": session_id,
            "timestamp": iso,
            "message": {
                "role": "assistant",
                "model": "claude-opus-4-8",
                "content": content,
                "usage": {"input_tokens": 1200, "output_tokens": 400, "cache_read_input_tokens": 9000},
            },
        },
    ]
    return session_id, "\n".join(json.dumps(line) for line in lines)


def main() -> None:
    projects_dir = ROOT / "projects"
    config_dir = ROOT / "config" / "collins"
    projects_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)

    names: dict[str, str] = {}
    emojis: dict[str, str] = {}
    favorites: list[str] = []
    now = time.time()

    for project, prompt, age, custom_name, favorite, emoji, mcp_server in SESSIONS:
        cwd = f"/home/demo/projects/{project}"
        project_dir = projects_dir / cwd.replace("/", "-")
        project_dir.mkdir(exist_ok=True)
        mtime = now - age
        session_id, content = make_transcript(cwd, prompt, mtime, mcp_server)
        path = project_dir / f"{session_id}.jsonl"
        path.write_text(content, encoding="utf-8")
        os.utime(path, (mtime, mtime))
        if custom_name:
            names[session_id] = custom_name
        if emoji:
            emojis[session_id] = emoji
        if favorite:
            favorites.append(session_id)

    (config_dir / "state.json").write_text(
        json.dumps(
            {"names": names, "emojis": emojis, "favorites": favorites, "archived": [], "settings": {}},
            indent=2,
        ),
        encoding="utf-8",
    )

    (ROOT / "claude.json").write_text(
        json.dumps({"mcpServers": MCP_GLOBAL, "projects": MCP_PER_PROJECT}, indent=2),
        encoding="utf-8",
    )

    print(f"Demo data written to {ROOT}")
    print("Launch with:")
    print(
        f"  XDG_CONFIG_HOME={ROOT}/config COLLINS_PROJECTS_DIR={ROOT}/projects "
        f"COLLINS_CLAUDE_CONFIG={ROOT}/claude.json python3 -m collins"
    )


if __name__ == "__main__":
    main()
