# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-19. Full change history: git log for this file.

"""Read what a session is doing by tailing its JSONL transcript.

Five things come out of the same pass, which is what makes it cheap: the files
the agent has written (see ``_TOUCH_TOOLS``), the ``pr-link`` records Claude
Code writes when a session opens or touches a pull request (see prstatus), the
model that answered the last turn (see ``model``), the session's current
permission mode (see ``permission_mode``), and the images the conversation
named (see ``attachments``).
Every distinct PR is kept, not just the last one — a session that opens three of
them has three to show — in the order they first appear, which is the order they
were opened.

Tailing is incremental (byte offset) so it stays cheap on large, actively-written
transcripts. It is also retroactive for free: a transcript is always read from
byte 0, so a session opened today gives up every image it has ever mentioned on
the first poll and only the new ones after that.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import attachrecords
from .attachrecords import Attachment
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

# The harness tool that hands the user files directly. Its input is the only
# tool input the image pass reads — see `_deliveries`.
_SEND_FILE_TOOL = "SendUserFile"

# The CLI stamps its own interjections — API errors, interrupted turns — as
# assistant messages from this "model". No model answered them, so they must
# not retire the one that did.
_SYNTHETIC_MODEL = "<synthetic>"


class TranscriptModel:
    def __init__(self, jsonl_path: str | Path | None) -> None:
        self.path = Path(jsonl_path) if jsonl_path else None
        self._prs: dict[str, PullRequest] = {}  # url -> PR, first-seen order
        self._touched: list[str] = []  # files written by the agent, newest first
        self._model: str | None = None  # model id of the most recent reply
        self._permission_mode: str | None = None  # last mode the CLI recorded
        self._images: dict[str, Attachment] = {}  # images named in the messages
        self._offset = 0
        self._buf = b""

    def set_path(self, jsonl_path: str | Path | None) -> None:
        """(Re)point at a transcript — used once a new session's file appears."""
        self.path = Path(jsonl_path) if jsonl_path else None
        self._prs = {}
        self._touched = []
        self._model = None
        self._permission_mode = None
        self._images = {}
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
            self._model = None
            self._permission_mode = None
            self._images = {}
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
        # The CLI stamps the session's permission mode onto every user turn
        # and writes a bare `permission-mode` record each time it changes
        # (shift+tab), so the last one seen is the mode right now. Sidechain
        # lines never carry the field (verified 2026-08-19), so no filter is
        # needed. Not "changed": nothing drawn reads it — start_session does.
        mode = entry.get("permissionMode")
        if isinstance(mode, str) and mode:
            self._permission_mode = mode
        if entry.get("type") == "pr-link":  # bare metadata record, no message
            pr = parse_pr_link(entry)
            if pr is None or pr.url in self._prs:
                return False  # re-emitted on resume/compact; not news, and not a reorder
            self._prs[pr.url] = pr
            return True
        message = entry.get("message") or {}
        changed = self._record_model(entry, message)
        if self._record_images(entry, message):
            changed = True
        content = message.get("content")
        if not isinstance(content, list):
            return changed
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") in _TOUCH_TOOLS:
                key = _TOUCH_TOOLS[block.get("name")]
                path = (block.get("input") or {}).get(key)
                if isinstance(path, str) and path.strip() and self._record_touch(path.strip()):
                    changed = True
        return changed

    def _record_model(self, entry: dict, message: dict) -> bool:
        """Remember which model wrote this reply. False when it isn't one, or
        when it is the model already recorded.

        Only the session's own replies count. A subagent's turns are written
        into the same transcript (``isSidechain``) and routinely run on another
        model — a Haiku search agent must not be mistaken for the session
        switching to Haiku.
        """
        if entry.get("type") != "assistant" or entry.get("isSidechain"):
            return False
        model = message.get("model")
        if not isinstance(model, str) or not model or model == _SYNTHETIC_MODEL:
            return False
        if model == self._model:
            return False
        self._model = model
        return True

    def _record_images(self, entry: dict, message: dict) -> bool:
        """Fold the images this message names into the session's list. False
        when it named none, or none the list didn't already have.

        Only what was actually *said* is read — the text blocks of the two
        sides' messages. Tool results are where a session's bulk lives, and
        they are mostly file listings and diffs: scanning them would turn a
        gallery of the pictures a conversation was about into every png
        under the project root. Sidechain records are skipped for the reason
        `_record_model` gives — a subagent's turns are somebody else's
        conversation that happens to share the file. ``isMeta`` records are
        skipped too: those are text the harness injected (a skill's SKILL.md,
        a slash command's expansion), which is neither side speaking, and
        instructions name paths for a hundred reasons — a repo-shipped
        example image mentioned in a skill would land in the gallery of
        every session that ever loaded it.

        One tool call *is* read: SendUserFile (see ``_deliveries``), because
        handing the user a file is saying it as deliberately as prose can,
        and the call's arguments are the only place its paths and caption
        ever appear — an agent that sends a file without also naming it in
        text would otherwise leave no trace here at all. It is also the one
        feed that records more than pictures: a delivered report or archive
        lands as a ``file`` row (attachrecords.delivered sorts the kinds),
        where the text scan stays images-only.

        The message's own cwd resolves its relative paths, and its own
        timestamp dates the sighting: read from byte 0, a morning's
        transcript arrives all at once, and stamping it `now` would file
        every image in it under this minute.
        """
        if entry.get("type") not in ("user", "assistant") or entry.get("isSidechain"):
            return False
        if entry.get("isMeta"):
            return False
        content = message.get("content")
        cwd = entry.get("cwd")
        roots = [cwd] if isinstance(cwd, str) and cwd else []
        at = _stamp(entry.get("timestamp"))
        seen: list[Attachment] = []
        for text in _texts(content):
            seen.extend(attachrecords.scan(text, roots=roots, now=at))
        for files, caption in _deliveries(content):
            for path in files:
                one = attachrecords.delivered(path, roots=roots, caption=caption, now=at)
                if one is not None:
                    seen.append(one)
        if not seen:
            return False
        before = self._images
        self._images = attachrecords.fold(before, *seen)
        return self._images != before

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

    def model(self) -> str | None:
        """The model that answered the session's most recent turn, as the CLI
        recorded it (``claude-opus-5``), or None until one has.

        The most recent rather than the first: a session can change model
        mid-run — ``/model``, a fast-mode toggle — and what it is answering
        with now is the only interesting answer.
        """
        return self._model

    def permission_mode(self) -> str | None:
        """The session's permission mode as of its last transcript line —
        the CLI's own value ("default", "acceptEdits", "plan", "auto", …) —
        or None until one has been recorded (an empty transcript, or a CLI
        old enough not to stamp it)."""
        return self._permission_mode

    def attachments(self) -> list[Attachment]:
        """Every image the conversation has named — plus every file it
        delivered outright — newest sighting first.

        Transcript-sourced, so each carries the line it was mentioned on as
        its context and none of them carries a caption: the tab merges these
        with what the lightbox recorded, where the captions come from (see
        attachrecords).
        """
        return list(self._images.values())

    def pull_requests(self) -> list[PullRequest]:
        """Every pull request this session has linked, oldest first.

        Unenriched — call ``prstatus.enrich()`` (which touches the filesystem)
        off the main loop to add CI status.
        """
        return list(self._prs.values())


def _texts(content: object) -> list[str]:
    """What a message actually said, as plain strings.

    A user turn is either the text itself or a list of blocks; an assistant
    turn is always blocks. Only ``text`` ones are taken: a ``tool_use``
    input is arguments, a ``tool_result`` is output, and ``thinking`` is a
    draft of what was later said properly.
    """
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    return [
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]


def _deliveries(content: object) -> list[tuple[list, str | None]]:
    """Each SendUserFile call in a message, as ``(files, caption)``.

    The one tool whose *input* the image pass reads (see ``_record_images``
    for why). Shapes are taken on trust no further than typing: the files
    list is whatever the call carried — each entry is vetted individually by
    ``attachrecords.delivered`` — and a caption that isn't a string is no
    caption. The list is also cut to `scan`'s stat budget: a real call sends
    a handful of files, so anything longer is a doctored transcript, and
    every entry kept may cost a disk check on the update thread.
    """
    if not isinstance(content, list):
        return []
    calls = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        if block.get("name") != _SEND_FILE_TOOL:
            continue
        arguments = block.get("input") or {}
        if not isinstance(arguments, dict):
            continue
        files = arguments.get("files")
        caption = arguments.get("caption")
        calls.append((
            files[: attachrecords.MAX_SCAN_CANDIDATES] if isinstance(files, list) else [],
            caption if isinstance(caption, str) else None,
        ))
    return calls


def _stamp(value: object) -> float | None:
    """A transcript timestamp as unix seconds, or None when it can't be read
    — in which case the sighting is dated when it was noticed instead."""
    if not isinstance(value, str) or not value:
        return None
    try:
        # fromisoformat() can't parse a trailing "Z" until Python 3.11.
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
