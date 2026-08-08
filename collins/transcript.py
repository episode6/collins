# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-08. Full change history: git log for this file.

"""Read what a session is doing by tailing its JSONL transcript.

Two things come out of the same pass, which is what makes it cheap: the files
the agent has written (see ``_TOUCH_TOOLS``), and the ``pr-link`` records Claude
Code writes when a session opens or touches a pull request (see prstatus).
Every distinct PR is kept, not just the last one — a session that opens three of
them has three to show — in the order they first appear, which is the order they
were opened.

Tailing is incremental (byte offset) so it stays cheap on large, actively-written
transcripts.
"""

from __future__ import annotations

import json
from pathlib import Path

from .prstatus import PullRequest, parse_pr_link

# Write-tools whose input names the file they touch, and the input key that
# carries it. This is what feeds the editor panel's "agent files" list — reads
# are deliberately absent (a session Reads far more than it changes, and the
# list is for "look at what the agent just wrote").
_TOUCH_TOOLS = {
    "Edit": "file_path",
    "MultiEdit": "file_path",
    "Write": "file_path",
    "NotebookEdit": "notebook_path",
}
_MAX_TOUCHED = 30  # most-recent-first; plenty for a list that shows a handful


class TranscriptModel:
    def __init__(self, jsonl_path: str | Path | None) -> None:
        self.path = Path(jsonl_path) if jsonl_path else None
        self._prs: dict[str, PullRequest] = {}  # url -> PR, first-seen order
        self._touched: list[str] = []  # files written by the agent, newest first
        self._offset = 0
        self._buf = b""

    def set_path(self, jsonl_path: str | Path | None) -> None:
        """(Re)point at a transcript — used once a new session's file appears."""
        self.path = Path(jsonl_path) if jsonl_path else None
        self._prs = {}
        self._touched = []
        self._offset = 0
        self._buf = b""

    def relocate(self, jsonl_path: str | Path) -> None:
        """Follow the *same* transcript to a new path, keeping what's parsed.

        The CLI re-keys a session's transcript under a project directory named
        for its working directory, so entering a git worktree moves the file
        out from under whoever is reading it. It is the same file with the same
        contents, so everything already ingested still stands and the read
        offset still points at the same place — unlike `set_path`, which starts
        a different session from scratch.

        A file that turns out to be shorter than the offset is picked up by
        `update`'s truncation path on the next read.
        """
        self.path = Path(jsonl_path)

    def update(self) -> bool:
        """Read newly-appended bytes and ingest them. Returns True if changed."""
        if not self.path or not self.path.exists():
            return False
        try:
            size = self.path.stat().st_size
        except OSError:
            return False
        if size < self._offset:  # rewritten/truncated → start over
            self._prs = {}
            self._touched = []
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
            if block.get("type") == "tool_use" and block.get("name") in _TOUCH_TOOLS:
                key = _TOUCH_TOOLS[block.get("name")]
                path = (block.get("input") or {}).get(key)
                if isinstance(path, str) and path.strip() and self._record_touch(path.strip()):
                    changed = True
        return changed

    def _record_touch(self, path: str) -> bool:
        """Move *path* to the front of the touched list. False when it was
        already the most recent one — no reorder, nothing to redraw."""
        if self._touched and self._touched[0] == path:
            return False
        try:
            self._touched.remove(path)
        except ValueError:
            pass
        self._touched.insert(0, path)
        del self._touched[_MAX_TOUCHED:]
        return True

    def touched_files(self) -> list[str]:
        """Files the agent has written (Edit/Write/NotebookEdit), most recent
        first, as the transcript recorded them — absolute paths, unchecked
        against disk or project root; the editor pane does that filtering."""
        return list(self._touched)

    def pull_requests(self) -> list[PullRequest]:
        """Every pull request this session has linked, oldest first.

        Unenriched — call ``prstatus.enrich()`` (which touches the filesystem)
        off the main loop to add CI status.
        """
        return list(self._prs.values())
