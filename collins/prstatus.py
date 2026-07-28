"""The pull request a Claude Code session is working on, read off disk.

Claude Code links a session to a PR the moment one shows up in tool output:
it appends a ``pr-link`` record to the session's JSONL transcript ::

    {"type":"pr-link","sessionId":"…","prNumber":55,
     "prUrl":"https://github.com/episode6/collins/pull/55",
     "prRepository":"episode6/collins","timestamp":"…"}

and re-emits it on resume/compact, so the *last* such record wins. It also
keeps ``~/.claude/gh-pr-status-cache.json`` — a URL-keyed cache it refreshes
from ``gh pr view`` every ~30s — holding the PR's state and check counts.

Together those give the footer a live PR chip for free. Like gitinfo, this is
all plain filesystem reads with no subprocesses, and every failure degrades to
"no PR" rather than raising.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

from .i18n import _

# Override with COLLINS_PR_STATUS_CACHE for demos and development.
PR_STATUS_CACHE = Path(
    os.environ.get("COLLINS_PR_STATUS_CACHE")
    or Path.home() / ".claude" / "gh-pr-status-cache.json"
)

# Guards against reading something that isn't the cache we expect.
_MAX_CACHE_BYTES = 1024 * 1024

CHECKS_PASSED = "✓"
CHECKS_FAILED = "✗"
CHECKS_PENDING = "●"


@dataclass(frozen=True)
class PullRequest:
    """A session's linked PR, optionally enriched with cached CI status."""

    number: int
    url: str
    repository: str | None = None
    state: str | None = None  # OPEN / DRAFT / MERGED / CLOSED
    passed: int | None = None
    failed: int | None = None
    pending: int | None = None

    @property
    def slug(self) -> str:
        """``episode6/collins#55``, or just ``#55`` without a repository."""
        return f"{self.repository}#{self.number}" if self.repository else f"#{self.number}"

    @property
    def checks_glyph(self) -> str | None:
        """One character summarizing CI, or None when no status is cached.

        Failures outrank pending runs, which outrank a clean sweep — the chip
        has room for one glyph, so it shows the one worth acting on.
        """
        if self.passed is None and self.failed is None and self.pending is None:
            return None
        if self.failed:
            return CHECKS_FAILED
        if self.pending:
            return CHECKS_PENDING
        if self.passed:
            return CHECKS_PASSED
        return None  # a PR with zero checks configured

    @property
    def label(self) -> str:
        """The footer chip's text: ``#55`` or ``#55 ✗``."""
        glyph = self.checks_glyph
        return f"#{self.number} {glyph}" if glyph else f"#{self.number}"


def state_text(state: str) -> str:
    """Translate a gh PR state, falling back to the raw value for new ones.

    Spelled out rather than a bare "Open"/"Draft": the sidebar already
    translates "Open" as a verb, and one msgid can't be both.
    """
    known = {
        "OPEN": _("Open pull request"),
        "DRAFT": _("Draft pull request"),
        "MERGED": _("Merged pull request"),
        "CLOSED": _("Closed pull request"),
    }
    return known.get(state, state)


def describe(pr: PullRequest) -> str:
    """The chip's long form: what the PR is and how its checks are doing.

    e.g. ``episode6/collins#55 · Draft pull request · 1 passed, 1 failed``.
    Lives here rather than beside the widget so it stays testable without a
    Gtk namespace — CI installs PyGObject but no GTK.
    """
    parts = [pr.slug]
    if pr.state:
        parts.append(state_text(pr.state))
    checks = [
        _("{n} passed").format(n=pr.passed) if pr.passed else None,
        _("{n} failed").format(n=pr.failed) if pr.failed else None,
        _("{n} pending").format(n=pr.pending) if pr.pending else None,
    ]
    running = ", ".join(part for part in checks if part)
    if running:
        parts.append(running)
    return " · ".join(parts)


def parse_pr_link(entry: dict) -> PullRequest | None:
    """Build a PullRequest from a decoded ``pr-link`` transcript record.

    Returns None for any other record type or a malformed one.
    """
    if entry.get("type") != "pr-link":
        return None
    number = entry.get("prNumber")
    url = entry.get("prUrl")
    if not isinstance(number, int) or isinstance(number, bool) or not isinstance(url, str) or not url:
        return None
    repository = entry.get("prRepository")
    return PullRequest(
        number=number,
        url=url,
        repository=repository if isinstance(repository, str) and repository else None,
    )


def _load_cache() -> dict:
    """The whole gh PR status cache, or {} when it is missing or unusable."""
    try:
        if PR_STATUS_CACHE.stat().st_size > _MAX_CACHE_BYTES:
            return {}
        raw = PR_STATUS_CACHE.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _count(checks: object, key: str) -> int | None:
    if not isinstance(checks, dict):
        return None
    value = checks.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def enrich(pr: PullRequest | None) -> PullRequest | None:
    """Fill in *pr*'s state and check counts from the CLI's status cache.

    Returns *pr* unchanged when there's nothing cached for its URL — the chip
    still shows the number, just without a CI glyph.
    """
    if pr is None:
        return None
    entry = _load_cache().get(pr.url)
    if not isinstance(entry, dict):
        return pr
    state = entry.get("state")
    checks = entry.get("checks")
    return replace(
        pr,
        state=state if isinstance(state, str) and state else None,
        passed=_count(checks, "passed"),
        failed=_count(checks, "failed"),
        pending=_count(checks, "pending"),
    )
