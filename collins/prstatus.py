"""The pull request a Claude Code session is working on, read off disk.

Claude Code links a session to a PR the moment one shows up in tool output:
it appends a ``pr-link`` record to the session's JSONL transcript ::

    {"type":"pr-link","sessionId":"…","prNumber":55,
     "prUrl":"https://github.com/episode6/collins/pull/55",
     "prRepository":"episode6/collins","timestamp":"…"}

and re-emits it on resume/compact, so the *last* such record wins. Reading the
number back is therefore a plain filesystem read that can't fail loudly.

CI status is the harder half. Claude Code keeps a URL-keyed cache of PR state
and check counts at ``~/.claude/gh-pr-status-cache.json``, but as of CLI 2.1.220
only FleetView refreshes it — an ordinary session never does, so the file can
sit untouched for days while its entries rot. Trusting it wholesale means
showing a red ✗ on a PR that has long since gone green. So the cache is used
only while the file itself is recent (a free warm start for the first seconds
after launch), and Collins otherwise refreshes status itself with a short
``gh pr view`` per linked PR, at most once a minute.

A transcript is not the only way a session gets a PR, though: one opened by
hand never shows up in it. So the footer's refresh button can also ask gh which
PR belongs to the checked-out branch (`discover_pr`), which fills the chip in
for a session whose transcript will never mention one.

Those gh calls are the only subprocesses here; everything else is a filesystem
read, they always happen off the main thread, and every failure degrades to "no
status" (or "no PR") rather than raising.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

from .i18n import _

log = logging.getLogger(__name__)

# Override with COLLINS_PR_STATUS_CACHE for demos and development.
PR_STATUS_CACHE = Path(
    os.environ.get("COLLINS_PR_STATUS_CACHE")
    or Path.home() / ".claude" / "gh-pr-status-cache.json"
)

# Guards against reading something that isn't the cache we expect.
_MAX_CACHE_BYTES = 1024 * 1024

# How long the CLI's cache is worth reading after it was last written. Only
# FleetView refreshes it, so beyond this it is a fossil, not a cache.
CACHE_MAX_AGE_S = 300

# Our own fetched status: how long it stays fresh, and how long a *failed*
# fetch is remembered before that PR is tried again. The footer polls every
# second, so both intervals are what keeps `gh` off the CPU.
_TTL_S = 60
_ERROR_TTL_S = 300
_GH_TIMEOUT_S = 10
# The stamp of a status that is due no matter which TTL applies to it (see
# invalidate) — every interval measured against it is already over.
_DUE = float("-inf")
_GH_FIELDS = "state,isDraft,statusCheckRollup"
# A branch lookup needs to learn which PR it found, and when it was opened, on
# top of that PR's status.
_GH_DISCOVER_FIELDS = "number,url,createdAt," + _GH_FIELDS
# A branch with more PRs than this behind it has no plausible "current" one.
_DISCOVER_LIMIT = 20

# Only fetch for URLs shaped like a PR page. The URL comes out of a transcript
# — repo content, i.e. untrusted — and lands in an argv, so this also keeps a
# value like "--version" from ever reaching `gh`. The group is the repository.
_FETCHABLE = re.compile(r"https://[\w.-]+/([\w.-]+/[\w.-]+)/pull/\d+$")

# Branch names reach argv the same way, from a `git branch --show-current` in a
# directory we didn't choose. git forbids a leading "-", but this is the code
# that hands the name to a subprocess, so it says so itself.
_BRANCH = re.compile(r"^\w[\w./+-]*$")

# gh's rollup verdicts, mapped the way the CLI maps them.
_PASSED = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})
_FAILED = frozenset({"FAILURE", "ERROR"})
_PENDING = frozenset({"ACTION_REQUIRED", "PENDING", "EXPECTED"})

_lock = threading.Lock()
# url -> (fetched-at, cache-shaped entry or None for a failed fetch)
_statuses: dict[str, tuple[float, dict | None]] = {}
_inflight: set[str] = set()
_gh_missing = False  # gh isn't on PATH; nothing to retry against this run

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
    def merged(self) -> bool:
        """Merged PRs get GitHub's purple git-merge mark in place of a glyph."""
        return self.state == "MERGED"

    @property
    def glyph(self) -> str | None:
        """The CI mark the chip shows beside the number, if any.

        A merged PR shows none — the merge mark beside it says all there is to
        say, and whether CI passed on the way in is history.
        """
        return None if self.merged else self.checks_glyph


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


def _now() -> float:
    return time.monotonic()


def _cached_status(url: str) -> dict | None:
    """The CLI's cached entry for *url*, but only while the cache is recent.

    The whole file shares one mtime, so a refresh for any PR vouches for every
    entry in it. That's as fine-grained as the file allows, and it errs the
    right way: our own fetch overrides this within the minute anyway.
    """
    try:
        age = time.time() - PR_STATUS_CACHE.stat().st_mtime
    except OSError:
        return None
    if age > CACHE_MAX_AGE_S:
        return None
    entry = _load_cache().get(url)
    return entry if isinstance(entry, dict) else None


def _counts(rollup: object) -> dict:
    """Tally gh's statusCheckRollup contexts into passed/failed/pending.

    A context with no verdict yet, or one whose run hasn't completed, is
    pending; anything unrecognized counts as a failure, so a state we've never
    heard of surfaces as something to look at rather than silently passing.
    """
    passed = failed = pending = 0
    for check in rollup if isinstance(rollup, list) else []:
        if not isinstance(check, dict):
            continue
        verdict = str(check.get("conclusion") or check.get("state") or "").upper()
        status = str(check.get("status") or "").upper()
        if verdict in _PASSED:
            passed += 1
        elif verdict in _FAILED:
            failed += 1
        elif not verdict or verdict in _PENDING or status != "COMPLETED":
            pending += 1
        else:
            failed += 1
    return {"passed": passed, "failed": failed, "pending": pending}


def _state(data: dict) -> str | None:
    """gh reports draft-ness separately; the chip wants it as a state."""
    state = data.get("state")
    if not isinstance(state, str) or not state:
        return None
    if state in ("MERGED", "CLOSED"):
        return state
    return "DRAFT" if data.get("isDraft") is True else state


def _gh_json(args: list[str], cwd: str | None = None) -> object | None:
    """One `gh` call, returning its parsed --json output. None on any failure.

    An object or a list, depending on the subcommand, so callers check the shape
    they asked for.

    Never a shell, and never a caller-built string: *args* trails the gh binary
    as argv, so nothing in it can become a second command.
    """
    global _gh_missing
    gh = shutil.which("gh")
    if gh is None:
        _gh_missing = True
        log.info("prstatus: gh not on PATH; PR chips will show the number only")
        return None
    try:
        result = subprocess.run(
            [gh, *args],
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT_S,
            cwd=cwd,
        )
    except (OSError, subprocess.SubprocessError) as err:
        log.debug("prstatus: gh %s failed: %s", " ".join(args), err)
        return None
    if result.returncode != 0:
        log.debug(
            "prstatus: gh %s exited %s: %s",
            " ".join(args),
            result.returncode,
            (result.stderr or result.stdout).strip()[:200],
        )
        return None
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return None


def _entry(data: dict) -> dict:
    """A gh reply reduced to the CLI cache's `{state, checks}` shape."""
    return {"state": _state(data), "checks": _counts(data.get("statusCheckRollup"))}


def _run_gh(url: str) -> dict | None:
    """One `gh pr view`, shaped like a CLI cache entry. None on any failure.

    A URL argument means this works from anywhere — no repository cwd needed —
    and covers GitHub Enterprise hosts the user is logged in to.
    """
    data = _gh_json(["pr", "view", url, "--json", _GH_FIELDS])
    return _entry(data) if isinstance(data, dict) else None


def refresh(url: str) -> None:
    """Fetch *url*'s status now and remember it. Never call on the main thread.

    A failure is remembered too (as "no status"), so an unauthenticated or
    offline `gh` is retried once every few minutes instead of every poll.
    """
    try:
        entry = _run_gh(url)
    except Exception:  # never leave a PR wedged as in-flight
        log.debug("prstatus: refreshing %s failed", url, exc_info=True)
        entry = None
    with _lock:
        _statuses[url] = (_now(), entry)
        _inflight.discard(url)


def _schedule(url: str) -> None:
    threading.Thread(target=refresh, args=(url,), name="pr-status", daemon=True).start()


def _own_status(url: str) -> dict | None:
    """Our last fetched status for *url*, kicking off a refresh when it's due.

    Returns what we have even when it's past its TTL: a minute-old glyph beats
    a blank one, and the refresh lands before the next poll or two.
    """
    if not _FETCHABLE.match(url):
        return None
    with _lock:
        stamped = _statuses.get(url)
        entry = stamped[1] if stamped else None
        ttl = _TTL_S if entry else _ERROR_TTL_S
        due = stamped is None or _now() - stamped[0] >= ttl
        fetch = due and not _gh_missing and url not in _inflight
        if fetch:
            _inflight.add(url)
    if fetch:
        _schedule(url)
    return entry


def invalidate(url: str) -> None:
    """Mark *url*'s status due, so the next `enrich` refetches it.

    The chip's refresh button, when a PR is already showing. The click only
    invalidates — the fetch belongs to the poll that follows, off the main
    thread like every other one — and the entry stays put rather than being
    dropped, so `enrich` keeps handing back the glyph it has while the refetch
    runs. Clicking refresh must not blank the chip on its way to updating it.
    """
    with _lock:
        stamped = _statuses.get(url)
        if stamped is not None:
            _statuses[url] = (_DUE, stamped[1])


def _newest(entries: object) -> dict | None:
    """The most recently opened of gh's PRs for a branch, or None if none are
    usable. Ties (gh has second resolution) go to the higher number."""
    if not isinstance(entries, list):
        return None
    usable = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        number, url = item.get("number"), item.get("url")
        if not isinstance(number, int) or isinstance(number, bool):
            continue
        if not isinstance(url, str) or _FETCHABLE.match(url) is None:
            continue
        created = item.get("createdAt")
        usable.append(((created if isinstance(created, str) else ""), number, item))
    if not usable:
        return None
    usable.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return usable[0][2]


def discover_pr(cwd: str | None, branch: str | None) -> PullRequest | None:
    """The PR gh reports for *branch*, asked from inside *cwd*, status included.

    What the footer's refresh button runs, every time and whatever the chip is
    showing: it asks the branch rather than the transcript, so it finds a PR
    nobody told Collins about and notices when the branch has moved on to a
    newer one. One call answers "is there one?", "which?" and "how is its CI
    doing?", and the status is remembered as a fetch of our own — which it is.

    A branch can carry several PRs (a merged one, then fresh work on the same
    name), so the most recently opened wins: that's the one still live.

    Never call on the main thread, and expect None often: no branch, no PR for
    it, or no gh to ask are all ordinary.
    """
    if not cwd or not branch or not _BRANCH.match(branch) or _gh_missing:
        return None
    found = _newest(
        _gh_json(
            [
                "pr", "list",
                "--head", branch,
                "--state", "all",  # a merged PR still answers "which branch is this?"
                "--json", _GH_DISCOVER_FIELDS,
                "--limit", str(_DISCOVER_LIMIT),
            ],
            cwd=cwd,
        )
    )
    if found is None:
        return None
    number, url = found["number"], found["url"]
    with _lock:
        _statuses[url] = (_now(), _entry(found))
    log.info("prstatus: branch %s -> #%s", branch, number)
    repository = _FETCHABLE.match(url).group(1)  # _newest only keeps matching URLs
    return enrich(PullRequest(number=number, url=url, repository=repository))


def enrich(pr: PullRequest | None) -> PullRequest | None:
    """Fill in *pr*'s state and check counts, refreshing them when they're due.

    Touches the filesystem and may spawn `gh` off a worker thread, so keep this
    off the main loop. Returns *pr* unchanged when no status is known yet — the
    chip still shows the number, just without a CI glyph.
    """
    if pr is None:
        return None
    entry = _own_status(pr.url) or _cached_status(pr.url)
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
