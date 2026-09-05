"""Attach the pull requests a session's first prompt links to its row.

A session launched with "address the comments on
https://github.com/owner/repo/pull/271" is about that PR from its first
breath, but nothing shows it: a transcript only grows a pr-link record when
the agent's own tool output touches the PR, and plenty of prompts get
answered without that ever happening. The attach_pr session tool exists for
exactly this gap — a PR named outside the transcript — but it relies on the
agent thinking to call it. So each new session's first prompt is read here
instead, and every PR URL in it goes on the session's row as if the tool
had been called.

Only the URL form counts. The grammar session titling parses
(titles.pr_references_all) also reads "PR 271" and "owner/repo#12", and
attaching once did too — until prompts like "open PR 0 of the port" and "PR
1 (base: main), PR 2 (base: PR 1's branch)" stuck pull requests 0, 1 and 2
of the repository, real and unrelated, onto sessions that were about to
*open* those PRs. A title that names the wrong PR is a wrong title; a chip
that names the wrong PR is a wrong mark, a wrong footer, and a wrong prompt
target for every PR action. The URL is the one form that cannot mean
anything but the PR it names, so it is the one form read here
(titles.pr_url_references).

Resolution happens off the main thread, one ``gh pr view`` per reference,
which fills in the status. When gh can't answer (offline, not logged in)
the URL is attached bare — the attach_pr tool does the same — and the next
status fetch fills it in.

Only sessions that appear while the app runs are read. The backlog on disk
at launch predates its prompts being read (and mostly carries its PRs
already, via pr-links); a launch that re-read it would be a burst of gh
calls about ancient history. The store marks the backlog seen (`skip`) and
submits the rest once each; results return on the worker thread via the
callback, and the store hops them back to the main loop (see
SessionStore._on_prompt_prs).
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

from . import prstatus, sessions
from .prstatus import PullRequest
from .titles import PRReference, pr_url_references

log = logging.getLogger(__name__)

# References resolved per prompt. Each one is a `gh` call; a prompt that
# links more PRs than this is a changelog, not a work order.
_MAX_REFERENCES = 8


def resolve(ref: PRReference, cwd: str | None) -> PullRequest | None:
    """The PR *ref* stands for, status included, or None to drop it.

    Written for every form the grammar knows, though the attacher only
    hands it URLs now: a bare number with no directory to resolve it against
    has no repository to be a number in, and a slug or number gh answers
    nothing for is as likely an issue as a pull request — both dropped. Only
    a URL survives an unanswered lookup, and gh's canonical answer outranks
    it when there is one. Never call on the main thread.
    """
    if ref.needs_cwd and not cwd:
        return None
    # The URL form is the single-argument one that parses as a PR page; a
    # bare number is also a single argument, but parses as nothing.
    literal = prstatus.parse_pr_url(ref.args[0]) if len(ref.args) == 1 else None
    found = prstatus.lookup_pr(ref.args, cwd=cwd if ref.needs_cwd else None)
    return found if found is not None else literal


class PromptAttacher:
    """Serial background worker that reads first prompts for PR references.

    ``submit()`` and ``skip()`` must be called from a single thread (the
    GLib main loop); ``callback(session_id, prs)`` fires on the worker
    thread, so the caller is responsible for marshalling back to the main
    loop. Walks the same beat as titles.TitleGenerator, which reads the same
    prompts for names.
    """

    def __init__(
        self,
        callback: Callable[[str, list[PullRequest]], None],
        resolver: Callable[[PRReference, str | None], PullRequest | None] = resolve,
    ) -> None:
        self._callback = callback
        self._resolve = resolver
        self._queue: queue.SimpleQueue[tuple[str, Path, str | None]] = queue.SimpleQueue()
        self._seen: set[str] = set()  # queued or skipped during this run
        self._thread: threading.Thread | None = None

    def skip(self, session_ids: Iterable[str]) -> None:
        """Mark sessions as not worth reading — the launch backlog."""
        self._seen.update(session_ids)

    def submit(self, session_id: str, jsonl_path: Path, cwd: str | None) -> None:
        """Queue one session's transcript for a first-prompt read. Duplicate
        ids are ignored, so this is safe to call on every refresh."""
        if session_id in self._seen:
            return
        self._seen.add(session_id)
        self._queue.put((session_id, Path(jsonl_path), cwd))
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._work, name="prompt-prs", daemon=True
            )
            self._thread.start()

    # -- worker thread -------------------------------------------------------

    def _work(self) -> None:
        while True:
            session_id, path, cwd = self._queue.get()
            try:
                prs = self._find(path, cwd)
            except Exception:  # noqa: BLE001 - one bad transcript mustn't stop the rest
                log.debug("prompt PRs: reading %s failed", path, exc_info=True)
                continue
            if prs:
                self._callback(session_id, prs)

    def _find(self, path: Path, cwd: str | None) -> list[PullRequest]:
        """Every PR the transcript's first prompt links, resolved and deduped.

        The dedup is by resolved URL, gh's canonical spelling, so two ways
        of writing one page meet even when the prompt spelled them apart.
        """
        prompt = sessions.first_user_prompt(path)
        if not prompt:
            return []
        found: dict[str, PullRequest] = {}
        for ref in pr_url_references(prompt)[:_MAX_REFERENCES]:
            pr = self._resolve(ref, cwd)
            if pr is not None and pr.url not in found:
                found[pr.url] = pr
        return list(found.values())
