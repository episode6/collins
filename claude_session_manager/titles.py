"""Auto-generated session titles.

When a session is first discovered without a user-assigned name, its first
prompt is summarized to five words or fewer by a cheap Claude model
(claude-haiku-4-5) and persisted by the store, so each title is generated
exactly once per session.

The ``anthropic`` SDK is an optional dependency (``pip install
agent-session-manager-gtk[titles]``); when it is missing, or no API
credentials are configured, the feature quietly stays off and the sidebar
keeps showing the raw prompt preview.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)

TITLE_MODEL = "claude-haiku-4-5"

# The preview passed in is already short; keep a hard cap anyway so a future
# caller can't accidentally ship a whole transcript to the API.
_MAX_PROMPT_CHARS = 1000
_MAX_TITLE_CHARS = 60

_SYSTEM_PROMPT = (
    "You generate titles for coding-agent sessions. Summarize the user's "
    "prompt as a title of five words or fewer. Reply with the title only: "
    "no quotes, no trailing punctuation, no explanation."
)


def sanitize_title(text: str) -> str:
    """Normalize a model reply into a short single-line title."""
    title = " ".join(text.split())
    title = title.strip("\"'` ").rstrip(".")
    return title[:_MAX_TITLE_CHARS].strip()


def _default_client():
    import anthropic  # optional dependency; ImportError disables the feature

    # Resolves credentials from the environment (ANTHROPIC_API_KEY or an
    # `ant auth login` profile). Raises when none are available.
    return anthropic.Anthropic()


class TitleGenerator:
    """Serial background worker that titles sessions via the Claude API.

    ``submit()`` must be called from a single thread (the GLib main loop);
    ``callback(session_id, title)`` fires on the worker thread, so the caller
    is responsible for marshalling back to the main loop.
    """

    def __init__(
        self,
        callback: Callable[[str, str], None],
        client_factory: Callable[[], object] = _default_client,
    ) -> None:
        self._callback = callback
        self._client_factory = client_factory
        self._queue: queue.SimpleQueue[tuple[str, str]] = queue.SimpleQueue()
        self._seen: set[str] = set()  # queued or attempted during this run
        self._client: object | None = None
        self._disabled = False
        self._thread: threading.Thread | None = None

    def submit(self, session_id: str, prompt: str) -> None:
        """Queue a session's first prompt for titling. Duplicate ids are
        ignored, so this is safe to call on every refresh."""
        prompt = prompt.strip()
        if self._disabled or not prompt or session_id in self._seen:
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
        if self._client is None:
            try:
                self._client = self._client_factory()
            except Exception as err:
                self._disabled = True
                log.info("session title generation disabled: %s", err)
                return None
        try:
            response = self._client.messages.create(
                model=TITLE_MODEL,
                max_tokens=30,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt[:_MAX_PROMPT_CHARS]}],
            )
        except Exception as err:
            self._handle_api_error(err)
            return None
        if getattr(response, "stop_reason", None) == "refusal":
            return None
        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        return sanitize_title(text) or None

    def _handle_api_error(self, err: Exception) -> None:
        """Bad credentials won't fix themselves mid-run: stop the worker.
        Anything else (rate limit after the SDK's retries, network, 5xx)
        skips just this session; it is retried on the next app run."""
        try:
            import anthropic
        except ImportError:  # injected client without the SDK: treat as transient
            log.warning("session title generation failed: %s", err)
            return
        if isinstance(err, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
            self._disabled = True
            log.info("session title generation disabled: %s", err)
        else:
            log.warning("session title generation failed: %s", err)
