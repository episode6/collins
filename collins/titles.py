"""Auto-generated session titles.

Sessions that already exist when the app launches get a cheap local title
(the first words of their prompt, via ``fallback_title``). Sessions created
while the app runs get their first prompt summarized to five words or fewer
by a headless ``claude -p --model haiku`` run — the same CLI and login the
whole app is built on, so no separate API credentials are needed. The store
persists each result, so a title is generated exactly once per session.
"Regenerate name" in the sidebar re-runs the model for one session on demand.

Headless runs write their own transcripts under ``~/.claude/projects``, so
they execute from a dedicated scratch directory: discovery skips that
project (otherwise every title run would appear as a session and itself get
queued for titling), and its transcripts are removed after each call.
"""

from __future__ import annotations

import logging
import queue
import re
import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from . import sessions, state

log = logging.getLogger(__name__)

# CLI model alias: version-agnostic, resolves to the current Haiku tier.
TITLE_MODEL = "haiku"

_TIMEOUT_S = 120
_MAX_PROMPT_CHARS = 1000
_MAX_TITLE_CHARS = 60
_FALLBACK_WORDS = 10
# Consecutive failures (e.g. CLI not logged in) before the worker gives up
# for the rest of the run.
_MAX_CONSECUTIVE_FAILURES = 3

# `claude -p` keeps Claude Code's own system prompt, so the instructions ride
# in the user message instead.
_PROMPT_TEMPLATE = (
    "Summarize the following coding-agent prompt as a session title of five "
    "words or fewer. Reply with the title only - no quotes, no punctuation, "
    "no explanation.\n\nPrompt:\n{prompt}"
)


def scratch_dir() -> Path:
    """Working directory for headless title runs. Session discovery excludes
    the ~/.claude/projects entry this maps to."""
    return state._CONFIG_DIR / "title-scratch"


def scratch_project_dirname() -> str:
    """The ~/.claude/projects directory name Claude Code derives from the
    scratch dir (every non-alphanumeric char becomes '-')."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(scratch_dir()))


def sanitize_title(text: str) -> str:
    """Normalize a model reply into a short single-line title."""
    title = " ".join(text.split())
    title = title.strip("\"'` ").rstrip(".")
    return title[:_MAX_TITLE_CHARS].strip()


def fallback_title(prompt: str) -> str:
    """A no-model title: the first few words of the prompt, tidied up. Used to
    backfill pre-existing sessions on launch so only sessions created while
    the app runs are ever sent to the model."""
    words = prompt.split()[:_FALLBACK_WORDS]
    return " ".join(words).strip("\"'` ").rstrip(".,;:")


class TitleError(Exception):
    """A failed title run. ``fatal`` means retrying can't help this run
    (e.g. the CLI is missing entirely)."""

    def __init__(self, message: str, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal


def _run_claude(prompt: str) -> str:
    """One headless CLI call; returns the model's reply text."""
    cli = shutil.which("claude")
    if cli is None:
        raise TitleError("claude CLI not found on PATH", fatal=True)
    workdir = scratch_dir()
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [cli, "-p", "--model", TITLE_MODEL],
            input=prompt,
            capture_output=True,
            text=True,
            cwd=workdir,
            timeout=_TIMEOUT_S,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise TitleError(f"claude exited {result.returncode}: {detail[:200]}")
        return result.stdout
    finally:
        # Drop the transcript the headless run just wrote.
        shutil.rmtree(sessions.CLAUDE_PROJECTS_DIR / scratch_project_dirname(), ignore_errors=True)


class TitleGenerator:
    """Serial background worker that titles sessions via the claude CLI.

    ``submit()`` must be called from a single thread (the GLib main loop);
    ``callback(session_id, title)`` fires on the worker thread, so the caller
    is responsible for marshalling back to the main loop.
    """

    def __init__(
        self,
        callback: Callable[[str, str], None],
        runner: Callable[[str], str] = _run_claude,
    ) -> None:
        self._callback = callback
        self._runner = runner
        self._queue: queue.SimpleQueue[tuple[str, str]] = queue.SimpleQueue()
        self._seen: set[str] = set()  # queued or attempted during this run
        self._failures = 0  # consecutive; reset on success
        self._disabled = False
        self._thread: threading.Thread | None = None

    def submit(self, session_id: str, prompt: str, force: bool = False) -> None:
        """Queue a session's first prompt for titling. Duplicate ids are
        ignored (so this is safe to call on every refresh) unless ``force``
        re-queues an id that already ran — used by "Regenerate name"."""
        prompt = prompt.strip()
        if self._disabled or not prompt:
            return
        if not force and session_id in self._seen:
            return
        self._seen.add(session_id)
        self._queue.put((session_id, prompt))
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._work, name="session-titles", daemon=True
            )
            self._thread.start()

    # -- worker thread -------------------------------------------------------

    def _work(self) -> None:
        while True:
            session_id, prompt = self._queue.get()
            if self._disabled:
                continue
            title = self._generate(prompt)
            if title:
                self._callback(session_id, title)

    def _generate(self, prompt: str) -> str | None:
        try:
            reply = self._runner(_PROMPT_TEMPLATE.format(prompt=prompt[:_MAX_PROMPT_CHARS]))
        except Exception as err:  # TitleError, TimeoutExpired, OSError, ...
            self._handle_error(err)
            return None
        self._failures = 0
        return sanitize_title(reply) or None

    def _handle_error(self, err: Exception) -> None:
        """A missing CLI won't fix itself mid-run, and neither will a setup
        that fails every call (e.g. not logged in) — stop the worker after a
        few strikes. One-off failures skip just this session; it is retried
        on the next app run."""
        self._failures += 1
        fatal = isinstance(err, TitleError) and err.fatal
        if fatal or self._failures >= _MAX_CONSECUTIVE_FAILURES:
            self._disabled = True
            log.info("session title generation disabled: %s", err)
        else:
            log.warning("session title generation failed: %s", err)
