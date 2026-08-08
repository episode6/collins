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
badging a PR as failed when it has long since gone green. So the cache is used
only while the file itself is recent (a free warm start for the first seconds
after launch), and Collins otherwise refreshes status itself with a short
``gh pr view`` per linked PR, at most once a minute.

A transcript is not the only way a session gets a PR, though: one opened by
hand never shows up in it. So the footer's refresh button can also ask gh which
PR belongs to the checked-out branch (`discover_pr`), which fills the chip in
for a session whose transcript will never mention one. The sidebar's refresh
button does both halves for every session it lists at once (`sweep`), so the
marks down the panel are current without every row being visited.

A session accumulates PRs — the footer shows every one of them — so its list
outlives the app run: `to_record`/`from_record` write a PR out whole, status
included (see AppState.set_session_prs). What was true when the app last looked
is not what is true now, but it is the closest thing to it that costs nothing:
the alternative is a panel of grey "nothing known" marks on every launch, which
reads as a verdict of its own. So a restored mark is last week's answer until a
fetch replaces it, and every path that shows one is already asking for that
fetch (an open tab's poll, a menu opening, the refresh sweep).

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
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

from .gitinfo import current_branch
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
# A status fetch is one of many and nobody is waiting on any single one; an
# action the user picked from a menu is one call they are waiting for, and
# GitHub takes its time over a merge.
_GH_ACTION_TIMEOUT_S = 30
# How much of gh's complaint about a failed action is worth putting in a
# dialog. It comes from GitHub, so it is capped like every other bit of
# untrusted text here.
_MAX_GH_ERROR = 500
# The stamp of a status that is due no matter which TTL applies to it (see
# invalidate) — every interval measured against it is already over.
_DUE = float("-inf")
# ``comments`` rides along for one bit: whether the newest comment is someone
# else's. Each comment carries ``viewerDidAuthor`` — GitHub answering "did the
# logged-in user write this?" — which spares a separate call to learn who that
# user is.
_GH_FIELDS = "title,state,isDraft,statusCheckRollup,mergeable,comments"
# What gh may say in the mergeable field. GitHub works the answer out lazily —
# the first fetch after a push routinely says UNKNOWN while a background job
# recomputes — so only a definite verdict is kept and UNKNOWN is stored as "no
# answer", to be corrected by the refresh after the next TTL.
_MERGEABLE_STATES = frozenset({"MERGEABLE", "CONFLICTING"})
# The states a record may carry, in and out (see `to_record`). A state gh has
# never reported isn't written, and one nothing here recognizes isn't read: a
# saved list is a file on disk, and a mark is built out of whatever it says.
_SAVED_STATES = frozenset({"OPEN", "DRAFT", "MERGED", "CLOSED"})
# Check counts are small integers about a single PR. A saved list that claims
# otherwise is not describing a PR, so it doesn't get to describe a mark.
_MAX_CHECK_COUNT = 9999
# Long PR titles are a thing; the menu ellipsizes them anyway, and this keeps
# what a repository can put on screen (and on disk) bounded.
_MAX_TITLE = 200
# A branch lookup needs to learn which PR it found, and when it was opened, on
# top of that PR's status.
_GH_DISCOVER_FIELDS = "number,url,createdAt," + _GH_FIELDS
# A branch with more PRs than this behind it has no plausible "current" one.
_DISCOVER_LIMIT = 20
# How many of a session's PRs `resync` fetches at once. Each one is a `gh`
# subprocess and someone is watching a spinner, so a session with twenty of
# them doesn't wait twenty round trips — but it isn't a fan-out either.
_RESYNC_WORKERS = 4
# The same, for a `sweep` across every session in the sidebar: a wider pool,
# because that is a whole panel's worth of directories and PRs behind one
# click, and still narrow enough that Collins doesn't look like a load test to
# the machine or to GitHub.
_SWEEP_WORKERS = 8
# How many PRs a row's tooltip spells out before it starts counting them.
_MAX_TOOLTIP_PRS = 8

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

# What a PR's badge — the small status mark riding its base icon — can say.
# Pure names rather than icon names: which icon and color each one gets is the
# chips' business (see prmenu), and this module stays importable without Gtk.
BADGE_FAILED = "failed"
BADGE_CONFLICT = "conflict"
BADGE_PENDING = "pending"
BADGE_UNRESOLVED = "unresolved"
BADGE_PASSED = "passed"


@dataclass(frozen=True)
class PullRequest:
    """A session's linked PR, optionally enriched with cached CI status."""

    number: int
    url: str
    repository: str | None = None
    # What the PR is called. A transcript never says, so this arrives with the
    # first `gh` reply — and stays with the PR from then on, saved list
    # included: it is what the chips' menu reads.
    title: str | None = None
    # Everything below is status: what `gh` last said. All of it is saved with
    # the PR (see `to_record`) and all of it is what the next fetch replaces —
    # a restored value is the last answer, not the current one.
    state: str | None = None  # OPEN / DRAFT / MERGED / CLOSED
    passed: int | None = None
    failed: int | None = None
    pending: int | None = None
    # MERGEABLE / CONFLICTING, or None while GitHub hasn't said (an unfetched
    # PR, gh's transient UNKNOWN, or a warm start from the CLI cache — which
    # has no such field).
    mergeable: str | None = None
    # Whether the PR's newest comment is someone else's — the conversation is
    # waiting on us.
    unresolved: bool = False

    @property
    def slug(self) -> str:
        """``episode6/collins#55``, or just ``#55`` without a repository."""
        return f"{self.repository}#{self.number}" if self.repository else f"#{self.number}"

    @property
    def merged(self) -> bool:
        """Merged PRs get GitHub's purple git-merge mark as their base icon."""
        return self.state == "MERGED"

    @property
    def closed(self) -> bool:
        """Closed-without-merging PRs get GitHub's red closed-PR mark."""
        return self.state == "CLOSED"

    @property
    def settled(self) -> bool:
        """Whether the PR is over — merged or closed — so nothing about it can change.

        The line every status mark stops at: a settled PR's base icon already
        says everything there is to say, and whatever its checks read at the
        end is history rather than something to act on. A PR nothing has been
        fetched for is *not* settled: unknown is not the same as finished.
        """
        return self.merged or self.closed

    @property
    def conflicting(self) -> bool:
        """Whether GitHub has said this PR can't merge as it stands.

        Only a live PR can conflict in any sense worth showing: a merged or
        closed one isn't going anywhere, whatever gh reports for it.
        """
        return self.state in ("OPEN", "DRAFT") and self.mergeable == "CONFLICTING"

    @property
    def awaiting_reply(self) -> bool:
        """Whether someone else has the last word on a PR that is still live.

        Only live PRs count, as with `conflicting`: a comment on a merged or
        closed PR isn't waiting on anyone.
        """
        return self.state in ("OPEN", "DRAFT") and self.unresolved

    @property
    def badge(self) -> str | None:
        """The one status worth acting on, or None when there is nothing to do.

        The chips show it as a small badge over the PR's base icon, and the
        slot holds one mark, so these outrank each other: a failed check needs
        fixing whatever else is true; a conflict blocks the merge even when
        every check is green; pending runs beat the all-clear that some checks
        already gave; a conversation waiting on a reply only surfaces once
        nothing above blocks the merge — checks passed (or none exist) and the
        branch merges clean; and a clean sweep gets GitHub's green check.

        A settled PR carries none: its base — purple merged, red closed — says
        all there is to say, and whether CI passed on the way in or out is
        history. A PR with no checks at all earns no green check either — one
        it never ran would be a lie — though unanswered comments still show on
        it.
        """
        if self.settled:
            return None
        if self.failed:
            return BADGE_FAILED
        if self.conflicting:
            return BADGE_CONFLICT
        if self.pending:
            return BADGE_PENDING
        if self.awaiting_reply:
            return BADGE_UNRESOLVED
        if self.passed:
            return BADGE_PASSED
        return None


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


def menu_name(pr: PullRequest) -> str:
    """What a PR is called in the chips' menu — the middle of the line there.

    The status mark that precedes it is a widget (colored, or the merge icon)
    and the number that follows it is its own label, so that an over-long title
    ellipsizes without taking the number with it. A PR whose title hasn't
    arrived yet falls back to the repository it is in, which offline is still
    better than a bare number.
    """
    return pr.title or pr.repository or _("Pull request")


def describe(pr: PullRequest) -> str:
    """The chip's long form: what the PR is and how its checks are doing.

    e.g. ``Add the thing · episode6/collins#55 · Draft pull request · 1 passed``.
    Lives here rather than beside the widget so it stays testable without a
    Gtk namespace — CI installs PyGObject but no GTK.
    """
    parts = [pr.title] if pr.title else []
    parts.append(pr.slug)
    if pr.state:
        parts.append(state_text(pr.state))
    if pr.conflicting:
        parts.append(_("Has merge conflicts"))
    if pr.awaiting_reply:
        parts.append(_("Has unresolved comments"))
    checks = [
        _("{n} passed").format(n=pr.passed) if pr.passed else None,
        _("{n} failed").format(n=pr.failed) if pr.failed else None,
        _("{n} pending").format(n=pr.pending) if pr.pending else None,
    ]
    running = ", ".join(part for part in checks if part)
    if running:
        parts.append(running)
    return " · ".join(parts)


def repository_for(url: str) -> str | None:
    """The ``owner/name`` behind a PR URL, or None when it isn't a PR URL.

    The gate every `gh` call goes through before a URL becomes an argv entry
    (see `_FETCHABLE`), and the one way to learn which repository a PR is in
    without trusting the ``prRepository`` a transcript claimed.
    """
    match = _FETCHABLE.match(url) if isinstance(url, str) else None
    return match.group(1) if match else None


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


def merge_ordered(
    saved: Iterable[PullRequest], links: Iterable[PullRequest]
) -> list[PullRequest]:
    """*saved* and *links* as one list, in an order that respects both.

    A transcript's pr-links are chronological and a row's saved list was built
    the same way, but neither is the whole story: a PR found by a branch lookup
    was never in a transcript, and a transcript can hold links that aged out of
    what was saved. Appending one to the other therefore gets the order wrong
    in one direction or the other.

    So this walks *saved* and, for each entry the transcript also knows, first
    emits every link that comes before it; an entry the transcript has never
    heard of keeps the slot it had. Both orders survive, and a PR either side
    knows about survives with them.

    When both sides have a PR, *saved*'s copy is the one returned — it is the
    one carrying whatever has been learned about it since.
    """
    saved, links = list(saved), list(links)
    by_url = {pr.url: pr for pr in links}
    by_url.update({pr.url: pr for pr in saved})
    at = {pr.url: index for index, pr in enumerate(links)}
    order: list[str] = []
    seen: set[str] = set()
    cursor = 0

    def emit(url: str) -> None:
        if url not in seen:
            seen.add(url)
            order.append(url)

    for pr in saved:
        index = at.get(pr.url)
        if index is not None:
            for link in links[cursor : index + 1]:
                emit(link.url)
            cursor = max(cursor, index + 1)
        emit(pr.url)
    for link in links[cursor:]:
        emit(link.url)
    return [by_url[url] for url in order]


def to_record(pr: PullRequest) -> dict | None:
    """*pr* as a JSON-safe record for AppState, or None when it isn't one.

    A URL that doesn't look like a PR page is dropped rather than written: it
    can't be refreshed (see `_FETCHABLE`), so the only thing persisting it
    would achieve is putting an unvalidated URL on disk.

    Status goes out with the PR — state, check counts, mergeability, whether
    someone is waiting on a reply — so a mark reads as the last thing gh said
    rather than as "nothing known" until the run's first fetch. Stale beats
    blank: grey says a PR nothing is known about, and saying that about a PR
    the app has watched all week is the more wrong of the two answers. A field
    gh never answered is left out of the record rather than written as null, so
    a record only ever says what was actually known.
    """
    if not _FETCHABLE.match(pr.url):
        return None
    record: dict = {"number": pr.number, "url": pr.url}
    if pr.repository:
        record["repository"] = pr.repository
    if pr.title:
        record["title"] = pr.title
    if pr.state in _SAVED_STATES:
        record["state"] = pr.state
    checks = {
        name: count
        for name, count in (("passed", pr.passed), ("failed", pr.failed),
                            ("pending", pr.pending))
        if count is not None
    }
    if checks:
        record["checks"] = checks
    if pr.mergeable in _MERGEABLE_STATES:
        record["mergeable"] = pr.mergeable
    if pr.unresolved:
        record["unresolved"] = True
    return record


def to_records(prs: Iterable[PullRequest]) -> list[dict]:
    """The persistable records for *prs*, in order, skipping any that aren't."""
    return [record for pr in prs if (record := to_record(pr)) is not None]


def from_record(record: object) -> PullRequest | None:
    """A PullRequest read back from `to_record`, or None if it can't be used.

    Everything is re-validated on the way in. These records started life in a
    transcript — repo content, i.e. untrusted — and a restored PR's URL is
    handed to a browser and to `gh`, so being the one that wrote the file
    earns no shortcut here. Status is read back the same way `gh`'s own reply
    is: a state nothing recognizes, a count that isn't a small integer or a
    mergeability GitHub never uses is dropped, and that field reads as "not
    known" — which is what it is.
    """
    if not isinstance(record, dict):
        return None
    number = record.get("number")
    url = record.get("url")
    if not isinstance(number, int) or isinstance(number, bool):
        return None
    if not isinstance(url, str) or not _FETCHABLE.match(url):
        return None
    repository = record.get("repository")
    checks = record.get("checks")
    mergeable = record.get("mergeable")
    return PullRequest(
        number=number,
        url=url,
        repository=repository if isinstance(repository, str) and repository else None,
        title=_title(record.get("title")),
        state=record["state"] if record.get("state") in _SAVED_STATES else None,
        passed=_saved_count(checks, "passed"),
        failed=_saved_count(checks, "failed"),
        pending=_saved_count(checks, "pending"),
        mergeable=mergeable if mergeable in _MERGEABLE_STATES else None,
        unresolved=record.get("unresolved") is True,
    )


def _saved_count(checks: object, key: str) -> int | None:
    """One saved check count, or None when the file doesn't have a usable one."""
    count = _count(checks, key)
    return count if count is not None and 0 <= count <= _MAX_CHECK_COUNT else None


def from_records(records: object) -> list[PullRequest]:
    """Every usable PullRequest in a saved list, in the order it was saved."""
    if not isinstance(records, list):
        return []
    return [pr for record in records if (pr := from_record(record)) is not None]


def newest_title(records: object) -> str | None:
    """The newest saved PR's title, or None while no saved PR has one.

    What the pr_title_sessions setting renames a session to (see
    SessionStore.apply_pr_title). A saved list is oldest-first, so the last
    titled entry is the PR the session opened most recently; a PR whose title
    hasn't arrived from `gh` yet (a bare pr-link, say) contributes nothing
    until a refresh lands one.
    """
    titles = [pr.title for pr in from_records(records) if pr.title]
    return titles[-1] if titles else None


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


def _gh(
    args: list[str], cwd: str | None = None, timeout: float = _GH_TIMEOUT_S
) -> subprocess.CompletedProcess | None:
    """One `gh` call, run to completion. None when it couldn't be run at all.

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
        return subprocess.run(
            [gh, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    except (OSError, subprocess.SubprocessError) as err:
        log.debug("prstatus: gh %s failed: %s", " ".join(args), err)
        return None


def gh_json(args: list[str], cwd: str | None = None) -> object | None:
    """One `gh` call, returning its parsed --json output. None on any failure.

    An object or a list, depending on the subcommand, so callers check the shape
    they asked for.
    """
    result = _gh(args, cwd)
    if result is None:
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


def gh_succeeds(args: list[str]) -> bool:
    """Whether one `gh` call exited 0 — its output never read, never logged.

    For a question whose whole answer is the exit code, and whose output is
    better not carried around: `gh auth token` says whether this machine has
    GitHub credentials at all (see ghsetup) and prints one to do it. False
    when gh isn't installed either, so a caller that needs to tell the two
    apart asks `shutil.which` first.
    """
    result = _gh(args)
    return result is not None and result.returncode == 0


def gh_run(args: list[str]) -> tuple[bool, str]:
    """One `gh` call run for what it *does*: ``(it worked, what to say if not)``.

    The reading half of this module can shrug a failure off — a chip without a
    status is a chip — but a menu item the user picked has to say why nothing
    happened, so this hands back gh's own complaint rather than logging it.
    That text comes from GitHub and from a repository, i.e. is untrusted, so it
    is capped before it is ever put in a dialog.

    Given longer than a status fetch gets: a merge waits on GitHub doing the
    merge. Never call on the main thread.
    """
    if shutil.which("gh") is None:
        return False, _("The GitHub CLI (gh) isn't installed, or isn't on PATH.")
    result = _gh(args, timeout=_GH_ACTION_TIMEOUT_S)
    if result is None:
        return False, _("Collins couldn't run gh.")
    if result.returncode == 0:
        return True, ""
    complaint = (result.stderr or result.stdout or "").strip()[:_MAX_GH_ERROR]
    log.info("prstatus: gh %s exited %s", " ".join(args), result.returncode)
    return False, complaint or _("gh exited with status {code}.").format(code=result.returncode)


def _entry(data: dict) -> dict:
    """A gh reply reduced to the CLI cache's `{state, checks}` shape, plus the
    title and mergeability — which that cache has no room for and the chips'
    menu needs."""
    mergeable = data.get("mergeable")
    return {
        "state": _state(data),
        "checks": _counts(data.get("statusCheckRollup")),
        "title": _title(data.get("title")),
        "mergeable": mergeable if mergeable in _MERGEABLE_STATES else None,
        "unresolved": _unresolved(data.get("comments")),
    }


def _unresolved(comments: object) -> bool:
    """Whether the newest comment on the PR is someone else's.

    gh hands the PR's comments back oldest-first, each stamped with
    ``viewerDidAuthor``; the last word being anyone else's means there is
    plausibly something to answer. Minimized comments are skipped — GitHub
    collapses those as spam or off-topic, so they demand nothing — and so is
    anything that isn't a comment-shaped dict at all, like the whole field
    when it isn't a list: no comments, nothing to answer. A comment that *is*
    one but is missing its authorship stamp reads as someone else's, though —
    erring toward "look at it" beats silently swallowing a reply.
    """
    if not isinstance(comments, list):
        return False
    for comment in reversed(comments):
        if isinstance(comment, dict) and comment.get("isMinimized") is not True:
            return comment.get("viewerDidAuthor") is not True
    return False


def _title(value: object) -> str | None:
    """A PR title as it is worth keeping: non-empty, one line, bounded.

    Comes from a repository, so it is treated like any other repo content:
    newlines would break the menu row it is put in, and length is capped.
    """
    if not isinstance(value, str):
        return None
    title = " ".join(value.split())
    return title[:_MAX_TITLE] or None


def _run_gh(url: str) -> dict | None:
    """One `gh pr view`, shaped like a CLI cache entry. None on any failure.

    A URL argument means this works from anywhere — no repository cwd needed —
    and covers GitHub Enterprise hosts the user is logged in to.
    """
    data = gh_json(["pr", "view", url, "--json", _GH_FIELDS])
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

    Returns what we have even when it's past its TTL: a minute-old status beats
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
    dropped, so `enrich` keeps handing back the status it has while the refetch
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
        gh_json(
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
    """Fill in *pr*'s title, state and check counts, refreshing them when due.

    Touches the filesystem and may spawn `gh` off a worker thread, so keep this
    off the main loop. Returns *pr* unchanged when no status is known yet — the
    chip still shows the number, just with nothing on its mark.

    A title the entry doesn't carry leaves the one *pr* already has: the CLI's
    own cache has no title field, so a warm start from it must not blank the
    title a `gh` reply (or the saved list) supplied.
    """
    if pr is None:
        return None
    return _applied(pr, _own_status(pr.url) or _cached_status(pr.url))


def known(pr: PullRequest) -> PullRequest:
    """*pr* with whatever status has already been fetched this run, if any.

    The main loop's own version of `enrich`: a dictionary lookup and nothing
    else — no file is read, no TTL is consulted and no `gh` is scheduled — so a
    widget can be rebuilt from it without either blocking or quietly starting
    subprocesses. What it hands back is therefore only ever as good as the last
    fetch someone else made (a tab's poll, a menu opening, a refresh sweep),
    which is exactly what the sidebar's marks want: the status the app already
    knows, shown wherever that session appears.
    """
    with _lock:
        stamped = _statuses.get(pr.url)
    return _applied(pr, stamped[1] if stamped else None)


def _applied(pr: PullRequest, entry: object) -> PullRequest:
    """*pr* with a fetched status entry folded into it, or unchanged without one."""
    if not isinstance(entry, dict):
        return pr
    state = entry.get("state")
    checks = entry.get("checks")
    mergeable = entry.get("mergeable")
    return replace(
        pr,
        title=_title(entry.get("title")) or pr.title,
        state=state if isinstance(state, str) and state else None,
        passed=_count(checks, "passed"),
        failed=_count(checks, "failed"),
        pending=_count(checks, "pending"),
        mergeable=mergeable if mergeable in _MERGEABLE_STATES else None,
        unresolved=entry.get("unresolved") is True,
    )


def resync(prs: Iterable[PullRequest]) -> list[PullRequest]:
    """*prs* with every title and check count fetched right now, in order.

    `enrich` is the polling path: it hands back what it has and only goes out
    to `gh` once a TTL is up. This is the on-demand one, for a list that is
    being opened rather than watched — a sidebar row's PR menu has no poll
    behind it, and what it saved last run may be days old. So each PR is
    fetched before the list comes back, and a fetch that fails leaves that PR
    exactly as it arrived (`enrich` reads the "no status" the failure recorded,
    and keeps what the saved record supplied).

    A merged PR is skipped unless its title is missing: nothing about one can
    change, so it isn't worth a subprocess. Merged, not settled — a closed PR
    is asked about like any other, because closing one is reversible and
    reopening it must not need a restart to show.

    Never call on the main thread — this waits on `gh`, several at a time.
    """
    prs = list(prs)
    due = [pr.url for pr in prs if _worth_fetching(pr)]
    due = [url for url in due if _FETCHABLE.match(url)]
    if due and not _gh_missing:
        with ThreadPoolExecutor(max_workers=min(_RESYNC_WORKERS, len(due))) as pool:
            list(pool.map(refresh, due))
    return [_after_fetching(pr) for pr in prs]


# -- a session's whole list as one mark --------------------------------------
#
# A footer has room for a chip per PR; a sidebar row has room for one mark, and
# it stands for everything the session has open. So the list is reduced the way
# a person reading the row would: the worst thing among them is what the mark
# says, and the all-clear has to be earned by every one of them.


def combined_state(prs: Iterable[PullRequest]) -> str | None:
    """The state a whole session's PRs read as, for one base icon.

    Least-settled first, because that is what the session still has to do: a
    draft anywhere means work in progress, an open PR anywhere means something
    is up for review, and a set with nothing live left reads as however it
    ended — merged if every one of them landed, closed if every one of them was
    abandoned. A mix of the two ended both ways and claims neither, and so does
    a list with a PR nothing has been fetched for: the caller's fallback (grey,
    i.e. "nothing known") is the honest answer to both.
    """
    states = {pr.state for pr in prs}
    if not states:
        return None
    if "DRAFT" in states:
        return "DRAFT"
    if "OPEN" in states:
        return "OPEN"
    if states == {"MERGED"}:
        return "MERGED"
    return "CLOSED" if states == {"CLOSED"} else None


def combined_badge(prs: Iterable[PullRequest]) -> str | None:
    """The one status worth acting on across a session's PRs, or None.

    Same slot and same marks as a single PR's `badge`, ranked by how loudly the
    session is asking for attention rather than by what blocks a given merge:
    anything broken (a failed check or a conflicting branch) outranks a
    conversation waiting on a reply, which outranks checks still running. That
    puts unanswered comments above pending runs — the opposite of the per-PR
    order, where a merge blocker is the question being asked; here the question
    is "does this session need me?", and a comment does while a running check
    does not.

    Settled PRs abstain entirely: nothing about a merged or closed one can
    change, and its base already says how it ended — a red build it carried on
    the way out is not something the row can ask for. The green check is the
    only mark that has to be earned by all of them — one live PR still running,
    still conflicting or with no checks at all is enough to withhold it,
    because a green mark on a row means "there is nothing here to do".
    """
    live = [pr for pr in prs if not pr.settled]
    if any(pr.failed for pr in live):
        return BADGE_FAILED
    if any(pr.conflicting for pr in live):
        return BADGE_CONFLICT
    if any(pr.awaiting_reply for pr in live):
        return BADGE_UNRESOLVED
    if any(pr.pending for pr in live):
        return BADGE_PENDING
    if live and all(pr.passed for pr in live):
        return BADGE_PASSED
    return None


def describe_all(prs: Iterable[PullRequest]) -> str:
    """Every PR behind one mark, a `describe` line each.

    What the row's tooltip says, since the mark itself has collapsed them all
    into a single verdict: the line count is the PR count, and the one that
    earned the badge is in there with the rest. Bounded, like everything else
    built out of repository text — a session that has opened dozens of PRs
    still gets a tooltip rather than a wall.

    A list too long to print keeps both of its ends: the oldest fill the top,
    as ever, and the newest gets the last line whatever the count, because it
    is the one the mark's right-click opens and the one the hint under this
    list names (see SessionRow._sync_pr_mark). The tally in between says how
    many were passed over on the way to it.
    """
    prs = list(prs)
    if len(prs) <= _MAX_TOOLTIP_PRS:
        return "\n".join(describe(pr) for pr in prs)
    lines = [describe(pr) for pr in prs[: _MAX_TOOLTIP_PRS - 1]]
    lines.append(_("and {n} more").format(n=len(prs) - _MAX_TOOLTIP_PRS))
    lines.append(describe(prs[-1]))
    return "\n".join(lines)


def sweep(targets: Iterable[tuple[str, list[PullRequest], str | None]]) -> dict[str, list[PullRequest]]:
    """Re-read every listed session's pull requests, branch lookup included.

    What the sidebar's refresh button runs over the whole list, so a panel full
    of rows tells the truth about CI and comments without each row's menu being
    opened one at a time. Each *target* is a session's ``(id, saved PRs, cwd)``,
    and each comes back with the same PRs enriched, plus any the branch lookup
    turned up — the same two halves the footer's own refresh button does for one
    tab, and in the same order: find first, then fetch, so a PR discovered here
    lands with its status already on it.

    The lookups are deduplicated by directory: sessions that share a worktree
    share a branch, and asking gh the same question once per row would be the
    expensive half of this. Status fetches are deduplicated by URL for the same
    reason — several sessions on one PR cost one call between them.

    Never call on the main thread: this is a `gh` call per directory and per
    unsettled PR, a few at a time. Every failure degrades to "nothing found"
    and leaves that session's list exactly as it arrived.
    """
    targets = [(session_id, list(prs), cwd) for session_id, prs, cwd in targets]

    # Which branch each distinct directory is on. Cheap enough to do serially:
    # current_branch is a couple of stat calls and a small read, no subprocess.
    branches: dict[str, str] = {}
    for _session_id, _prs, cwd in targets:
        if not cwd or cwd in branches:
            continue
        branch = current_branch(cwd)
        if branch:
            branches[cwd] = branch

    found: dict[str, PullRequest] = {}
    if branches and not _gh_missing:
        heads = list(branches.items())
        with ThreadPoolExecutor(max_workers=min(_SWEEP_WORKERS, len(heads))) as pool:
            for (cwd, _branch), pr in zip(heads, pool.map(_discover, heads), strict=True):
                if pr is not None:
                    found[cwd] = pr
        log.info("prstatus: swept %s branch(es), %s with a PR", len(branches), len(found))

    # A discovered PR is the newest thing that session knows about, exactly as
    # it is for a tab (see TerminalTab._collect_prs) — appended, never
    # replacing, so a session keeps the PRs it opened earlier.
    collected = {
        session_id: (
            [*prs, discovered]
            if (discovered := found.get(cwd or "")) is not None
            and all(pr.url != discovered.url for pr in prs)
            else prs
        )
        for session_id, prs, cwd in targets
    }

    # One fetch per URL, whatever it is worth to. A PR the lookup just found
    # came back with its status attached, so it is already current. Only a
    # merged one is skipped outright, as in `resync`: a closed PR still costs a
    # call, since it is the click that would show it reopened.
    due = {
        pr.url
        for prs in collected.values()
        for pr in prs
        if _worth_fetching(pr) and _FETCHABLE.match(pr.url)
    } - {pr.url for pr in found.values()}
    if due and not _gh_missing:
        with ThreadPoolExecutor(max_workers=min(_SWEEP_WORKERS, len(due))) as pool:
            list(pool.map(refresh, sorted(due)))
    return {
        session_id: [_after_fetching(pr) for pr in prs]
        for session_id, prs in collected.items()
    }


def _worth_fetching(pr: PullRequest) -> bool:
    """Whether asking `gh` about *pr* could tell us anything new.

    A merged PR that knows its title is the one thing nothing can change; a
    closed one is still asked about, because closing is reversible.
    """
    return not pr.merged or pr.title is None


def _after_fetching(pr: PullRequest) -> PullRequest:
    """*pr* with the status a just-finished round of fetching left for it.

    `enrich` for anything that was worth fetching — it reads what came back,
    and falls back to the CLI's cache when the fetch failed. `known` for the
    rest, which is a dictionary lookup and, unlike `enrich`, schedules nothing:
    a sweep that deliberately spent no call on a merged PR must not then start
    one in the background for it.
    """
    return (enrich(pr) or pr) if _worth_fetching(pr) else known(pr)


def _discover(target: tuple[str, str]) -> PullRequest | None:
    """One directory's branch lookup, for the sweep's pool. Never raises."""
    cwd, branch = target
    try:
        return discover_pr(cwd, branch)
    except Exception:
        log.debug("prstatus: branch lookup in %s failed", cwd, exc_info=True)
        return None
