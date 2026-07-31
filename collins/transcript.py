# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-30. Full change history: git log for this file.

"""Detect an agent's pending structured prompt by tailing its JSONL transcript.

Backs the question card: an ``AskUserQuestion`` tool_use whose id has no matching
``tool_result`` yet is a live, unanswered prompt. Tailing is incremental (byte
offset) so it stays cheap on large, actively-written transcripts.

The same pass also picks up the ``pr-link`` records Claude Code writes when a
session opens or touches a pull request (see prstatus), which costs nothing
extra: the bytes are already being read and decoded here. Every distinct PR is
kept, not just the last one — a session that opens three of them has three to
show — in the order they first appear, which is the order they were opened.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .prstatus import PullRequest, parse_pr_link


@dataclass
class Question:
    tool_use_id: str
    questions: list = field(default_factory=list)  # AskUserQuestion `questions` payload


class TranscriptModel:
    def __init__(self, jsonl_path: str | Path | None) -> None:
        self.path = Path(jsonl_path) if jsonl_path else None
        self._questions: dict[str, list] = {}  # tool_use_id -> questions payload
        self._order: list[str] = []  # question ids, arrival order
        self._resolved: set[str] = set()  # tool_use_ids that have a tool_result
        self._prs: dict[str, PullRequest] = {}  # url -> PR, first-seen order
        self._offset = 0
        self._buf = b""

    def set_path(self, jsonl_path: str | Path | None) -> None:
        """(Re)point at a transcript — used once a new session's file appears."""
        self.path = Path(jsonl_path) if jsonl_path else None
        self._questions = {}
        self._order = []
        self._resolved = set()
        self._prs = {}
        self._offset = 0
        self._buf = b""

    def update(self) -> bool:
        """Read newly-appended bytes and ingest them. Returns True if changed."""
        if not self.path or not self.path.exists():
            return False
        try:
            size = self.path.stat().st_size
        except OSError:
            return False
        if size < self._offset:  # rewritten/truncated → start over
            self._questions, self._order, self._resolved = {}, [], set()
            self._prs = {}
            self._offset, self._buf = 0, b""
        if size <= self._offset:
            return False
        try:
            with self.path.open("rb") as fh:
                fh.seek(self._offset)
                data = fh.read()
                self._offset = fh.tell()
        except OSError:
            return False

        self._buf += data
        parts = self._buf.split(b"\n")
        self._buf = parts.pop()  # trailing partial line
        changed = False
        for raw in parts:
            if not raw.strip():
                continue
            try:
                entry = json.loads(raw.decode("utf-8", "replace"))
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(entry, dict) and self._ingest(entry):
                changed = True
        return changed

    def _ingest(self, entry: dict) -> bool:
        if entry.get("type") == "pr-link":  # bare metadata record, no message
            pr = parse_pr_link(entry)
            if pr is None or pr.url in self._prs:
                return False  # re-emitted on resume/compact; not news, and not a reorder
            self._prs[pr.url] = pr
            return True
        content = (entry.get("message") or {}).get("content")
        if not isinstance(content, list):
            return False
        changed = False
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_use" and block.get("name") == "AskUserQuestion":
                qid = block.get("id", "")
                if qid and qid not in self._questions:
                    self._questions[qid] = (block.get("input") or {}).get("questions", [])
                    self._order.append(qid)
                    changed = True
            elif btype == "tool_result":
                rid = block.get("tool_use_id")
                if rid and rid not in self._resolved:
                    self._resolved.add(rid)
                    changed = True
        return changed

    def pull_requests(self) -> list[PullRequest]:
        """Every pull request this session has linked, oldest first.

        Unenriched — call ``prstatus.enrich()`` (which touches the filesystem)
        off the main loop to add CI status.
        """
        return list(self._prs.values())

    def pending_question(self) -> Question | None:
        """The most recent AskUserQuestion still awaiting an answer, or None."""
        for qid in reversed(self._order):
            if qid not in self._resolved:
                return Question(qid, self._questions[qid])
        return None
