# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The whole of one pull request, fetched on demand for the native PR view.

prstatus keeps the running summary — the handful of fields a footer chip or a
sidebar mark can show, polled on a TTL and persisted whole in state.json. This
module is the other altitude: everything the PR *page* shows — description,
timeline, checks, per-file diffs — fetched only when a view asks for it, never
polled, and never persisted (a diff must not end up in state.json).

A load is two `gh` calls, both through prstatus's transport so the URL gate,
timeouts, argv-only policy and missing-gh latch stay in one place: one
``gh pr view --json`` with the full field list, and one ``gh pr diff`` for the
patch. The diff is the unbounded half, so it is the capped one, and over the
cap — or on any diff-only failure — the load degrades to stat-only files
rather than failing: the conversation half of the view doesn't owe its life to
the patch. The view reply is also folded back into the summary layer
(`prstatus.absorb`), so opening a PR updates its chip and mark for free.

Everything parsed here is repository content — bodies, titles, branch names,
file paths, check names, URLs — i.e. untrusted. Parsing is therefore tolerant
(a malformed entry is dropped, never fatal), every string is bounded, and a
URL is only kept when it is http(s): nothing else may ever reach a browser.

GTK-free on purpose, like prstatus and practions: CI runs with no GTK
installed, so everything fixture-testable lives here and the widgets (prview,
a later PR) stay a thin shell beside it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from . import prstatus
from .prstatus import PullRequest

log = logging.getLogger(__name__)

# Everything the view renders, in one reply. A superset of prstatus._GH_FIELDS,
# which is what lets `absorb` treat the reply as a status fetch of our own.
_GH_DETAIL_FIELDS = (
    "number,url,title,state,isDraft,body,author,createdAt,baseRefName,"
    "headRefName,additions,deletions,changedFiles,labels,comments,reviews,"
    "statusCheckRollup,mergeable,files"
)

# The diff cap. A patch this size isn't going to be read in a panel, and the
# per-file stats from the view reply still give the Files list something to
# show; see `fetch`.
MAX_DIFF_BYTES = 5 * 1024 * 1024

# Bounds on what a repository can put in memory (and later on screen). Bodies
# stay generous — the view adds its own "Show more" fold well below this — and
# everything one-line gets the title treatment (see prstatus._MAX_TITLE).
_MAX_BODY = 100_000
_MAX_LINE = 200
_MAX_PATH = 500

# The one scheme family a stored URL may have: these are opened in a browser
# later, and a repository must not get to hand Collins a javascript:/file: URI.
_HTTP_URL = re.compile(r"^https?://", re.IGNORECASE)

# git's c-quoted path escapes, the non-octal half.
_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}
_OCTAL = "01234567"


@dataclass(frozen=True)
class PrComment:
    """One issue comment, as the timeline shows it."""

    author: str  # the login; "" when gh didn't say
    created_at: str  # ISO-8601 UTC as gh sent it; "" when it didn't
    body: str
    url: str  # http(s) link to the comment on GitHub, or ""


@dataclass(frozen=True)
class PrReview:
    """One submitted review — a verdict, with or without words."""

    author: str
    created_at: str
    state: str  # APPROVED / CHANGES_REQUESTED / COMMENTED / DISMISSED
    body: str


@dataclass(frozen=True)
class PrCheck:
    """One CI check row. *state* is a prstatus badge name (check_verdict)."""

    name: str
    state: str  # prstatus.BADGE_PASSED / BADGE_FAILED / BADGE_PENDING
    url: str  # the check's details page, http(s) or ""


@dataclass(frozen=True)
class PrFile:
    """One changed file: its stats always, its patch when there is one.

    A None patch is a binary file, a diff over the cap, or a diff call that
    failed — all rendered as a stat-only row with a placeholder.
    """

    path: str
    additions: int
    deletions: int
    patch: str | None


@dataclass(frozen=True)
class PullRequestDetail:
    """Everything the PR view renders, one load's worth.

    *summary* is the same record the chips read, freshly enriched — the header
    reuses its state icon and title. *changed_files* is gh's own count rather
    than ``len(files)``: the files field is a list a big PR can outgrow, and
    the header should keep counting what the list can't hold.
    """

    summary: PullRequest
    body: str
    author: str
    created_at: str
    base_ref: str
    head_ref: str
    additions: int
    deletions: int
    changed_files: int
    labels: tuple[str, ...]
    checks: tuple[PrCheck, ...]
    timeline: tuple[PrComment | PrReview, ...]
    files: tuple[PrFile, ...]


def fetch(url: str) -> PullRequestDetail | None:
    """Everything the PR view shows about *url*, fetched right now.

    None when the view reply can't be had — a URL that isn't a PR page, no
    gh, offline — and the caller keeps showing what it has (stale beats blank
    here too). A failed or over-cap diff is *not* a failure: the files arrive
    stat-only, patches None. The reply is folded into the summary cache on
    the way through (`prstatus.absorb`), so the chip and mark update with the
    view. Never call on the main thread — this waits on gh twice.
    """
    if prstatus.repository_for(url) is None:
        return None
    # The action timeout, not the poll's: this is one on-demand call the user
    # is waiting for, and a 19-field reply is the heavier half of the load.
    data = prstatus.gh_json(
        ["pr", "view", url, "--json", _GH_DETAIL_FIELDS],
        timeout=prstatus._GH_ACTION_TIMEOUT_S,
    )
    if not isinstance(data, dict):
        return None
    prstatus.absorb(url, data)
    diff = prstatus.gh_text(["pr", "diff", url], max_bytes=MAX_DIFF_BYTES)
    return parse_detail(url, data, diff)


def parse_detail(url: str, data: dict, diff: str | None) -> PullRequestDetail | None:
    """One gh view reply (and its diff, if any) as the record the view renders.

    Pure — module state is never touched — so recorded gh output drives it
    straight in tests. None only when *url*/*data* can't even identify a PR
    (`prstatus.summarize`'s answer).
    """
    summary = prstatus.summarize(url, data)
    if summary is None:
        return None
    patches = dict(split_unified_diff(diff)) if diff else {}
    return PullRequestDetail(
        summary=summary,
        body=_text(data.get("body")),
        author=_author(data.get("author")),
        created_at=_line(data.get("createdAt")),
        base_ref=_line(data.get("baseRefName")),
        head_ref=_line(data.get("headRefName")),
        additions=_int(data.get("additions")),
        deletions=_int(data.get("deletions")),
        changed_files=_int(data.get("changedFiles")),
        labels=_labels(data.get("labels")),
        checks=_checks(data.get("statusCheckRollup")),
        timeline=_timeline(data.get("comments"), data.get("reviews")),
        files=_files(data.get("files"), patches),
    )


# -- shaping the view reply ---------------------------------------------------


def _timeline(comments: object, reviews: object) -> tuple[PrComment | PrReview, ...]:
    """Comments and reviews as one column, oldest first.

    Minimized comments are dropped — GitHub collapses those as spam or
    off-topic, so they demand nothing (the same stance prstatus._unresolved
    takes) — and so is anything with nothing to show: an empty comment, an
    unsubmitted PENDING review, and the bodiless COMMENTED shell gh leaves
    where a review's inline comments hang (those threads are v2's GraphQL
    work; the shell alone would render as an empty card). gh's stamps are
    ISO-8601 UTC, so sorting the strings is sorting the times, and the sort
    is stable: entries one second can't split keep gh's own order.
    """
    entries: list[PrComment | PrReview] = []
    for comment in comments if isinstance(comments, list) else []:
        if not isinstance(comment, dict) or comment.get("isMinimized") is True:
            continue
        body = _text(comment.get("body"))
        if not body:
            continue
        entries.append(
            PrComment(
                author=_author(comment.get("author")),
                created_at=_line(comment.get("createdAt")),
                body=body,
                url=_http_url(comment.get("url")),
            )
        )
    for review in reviews if isinstance(reviews, list) else []:
        if not isinstance(review, dict):
            continue
        state = _line(review.get("state")).upper()
        body = _text(review.get("body"))
        if not state or state == "PENDING" or (state == "COMMENTED" and not body):
            continue
        entries.append(
            PrReview(
                author=_author(review.get("author")),
                created_at=_line(review.get("submittedAt")),
                state=state,
                body=body,
            )
        )
    entries.sort(key=lambda entry: entry.created_at)
    return tuple(entries)


def _checks(rollup: object) -> tuple[PrCheck, ...]:
    """The rollup as rows, one per context, in gh's order.

    Both of gh's shapes: a CheckRun has name/detailsUrl, a StatusContext has
    context/targetUrl. A context with no name at all has nothing to put in a
    row and is dropped — the summary's counts still include it.
    """
    checks = []
    for check in rollup if isinstance(rollup, list) else []:
        if not isinstance(check, dict):
            continue
        name = _line(check.get("name")) or _line(check.get("context"))
        if not name:
            continue
        checks.append(
            PrCheck(
                name=name,
                state=prstatus.check_verdict(check),
                url=_http_url(check.get("detailsUrl") or check.get("targetUrl")),
            )
        )
    return tuple(checks)


def _files(value: object, patches: dict[str, str]) -> tuple[PrFile, ...]:
    """gh's per-file stats joined with the split diff by path."""
    files = []
    for entry in value if isinstance(value, list) else []:
        if not isinstance(entry, dict):
            continue
        path = _path(entry.get("path"))
        if not path:
            continue
        files.append(
            PrFile(
                path=path,
                additions=_int(entry.get("additions")),
                deletions=_int(entry.get("deletions")),
                patch=patches.get(path),
            )
        )
    return tuple(files)


def _labels(value: object) -> tuple[str, ...]:
    """The label names, bounded like every other one-liner here."""
    if not isinstance(value, list):
        return ()
    names = [_line(label.get("name")) for label in value if isinstance(label, dict)]
    return tuple(name for name in names if name)


def _author(value: object) -> str:
    """The login behind gh's author object — '' when there isn't one."""
    return _line(value.get("login")) if isinstance(value, dict) else ""


def _line(value: object) -> str:
    """A one-line bounded string out of untrusted *value*, or ''."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:_MAX_LINE]


def _text(value: object) -> str:
    """A body: newlines kept, length bounded, anything non-string ''."""
    return value[:_MAX_BODY] if isinstance(value, str) else ""


def _path(value: object) -> str:
    """A file path: kept as git spelled it (spaces and all), length bounded."""
    return value[:_MAX_PATH] if isinstance(value, str) else ""


def _int(value: object) -> int:
    """A non-negative count, or 0 for anything a reply shouldn't have said."""
    ok = isinstance(value, int) and not isinstance(value, bool) and value >= 0
    return value if ok else 0


def _http_url(value: object) -> str:
    """*value* when it is a link a browser may be handed, else ''."""
    return value if isinstance(value, str) and _HTTP_URL.match(value) else ""


# -- splitting the unified diff -----------------------------------------------


def split_unified_diff(text: str) -> list[tuple[str, str]]:
    """*text* split at its ``diff --git`` boundaries: ``(path, patch)`` per file.

    The path is the file's *current* name — the b-side — read from the
    stanza's ``+++`` line when it has one, from ``--- a/…`` when the file was
    deleted (``+++`` says /dev/null), from its ``rename to`` line for a pure
    rename, and as a last resort off the ``diff --git`` header itself (binary
    stanzas have none of the above). git's c-quoted spellings are unquoted.
    Each patch keeps its whole stanza, header lines included — that is what a
    diff widget renders. A stanza whose path can't be worked out is dropped
    rather than guessed at; anything before the first boundary is preamble
    git never writes, and is ignored.
    """
    stanzas: list[list[str]] = []
    for line in text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            stanzas.append([line])
        elif stanzas:
            stanzas[-1].append(line)
    split = []
    for lines in stanzas:
        path = _stanza_path(lines)
        if path:
            split.append((path, "".join(lines)))
    return split


def _stanza_path(lines: list[str]) -> str:
    """One stanza's b-side path, or '' when it can't be worked out."""
    minus = plus = None
    for line in lines[1:]:
        line = line.rstrip("\r\n")
        if line.startswith("+++ ") and plus is None:
            plus = _target(line[4:], "b/")
        elif line.startswith("--- ") and minus is None:
            minus = _target(line[4:], "a/")
        elif line.startswith("rename to ") and plus is None:
            # A pure rename has no ---/+++ pair; git spells the new name out.
            plus = _unquote(line[len("rename to "):])
        elif line.startswith("@@"):
            break  # hunks: the headers are over
    if plus:
        return _path(plus)
    if minus:  # +++ said /dev/null: a deletion keeps its old name
        return _path(minus)
    return _path(_header_path(lines[0].rstrip("\r\n")))


def _target(name: str, prefix: str) -> str | None:
    """A ---/+++ line's path with git's a/ or b/ shed; None for /dev/null."""
    name = _unquote(name.split("\t")[0])  # git may trail a tab + timestamp
    if name == "/dev/null":
        return None
    return name[len(prefix):] if name.startswith(prefix) else (name or None)


def _header_path(header: str) -> str:
    """The b-side name off a ``diff --git a/X b/Y`` line — the last resort.

    Only binary stanzas land here (everything else answered from ---/+++ or
    ``rename to``), and a binary stanza is never a rename — so both halves
    name the same file, which is what disambiguates an unquoted name with
    spaces in it: ``a/x y b/x y`` splits where the halves agree.
    """
    rest = header[len("diff --git "):]
    cut = rest.rfind(' "b/')
    if cut != -1 and rest.endswith('"'):
        return _unquote(rest[cut + 1:])[len("b/"):]
    positions = [i for i in range(len(rest)) if rest.startswith(" b/", i)]
    if not positions:
        return ""
    if not rest.startswith("a/"):
        # The a-side was quoted; the b-side is whatever follows the last " b/".
        return rest[positions[-1] + 3:]
    for pos in positions:
        if rest[len("a/"):pos] == rest[pos + 3:]:
            return rest[pos + 3:]
    return rest[positions[-1] + 3:]


def _unquote(name: str) -> str:
    """git's c-quoted path spelling undone; a bare name comes back as it is.

    Octal escapes are UTF-8 *bytes* — git writes ``\\303\\251`` for é — so
    they collect as bytes and decode at the end, with replacement: a path is
    untrusted input, and a bad sequence is the repository's problem to have,
    not ours to crash on.
    """
    if len(name) < 2 or not (name.startswith('"') and name.endswith('"')):
        return name
    body = name[1:-1]
    out = bytearray()
    at = 0
    while at < len(body):
        char = body[at]
        if char == "\\" and at + 1 < len(body):
            escape = body[at + 1]
            if escape in _ESCAPES:
                out += _ESCAPES[escape].encode()
                at += 2
                continue
            octal = ""
            while len(octal) < 3 and at + 1 + len(octal) < len(body) \
                    and body[at + 1 + len(octal)] in _OCTAL:
                octal += body[at + 1 + len(octal)]
            if octal and int(octal, 8) < 256:  # \777 isn't a byte; keep it literal
                out.append(int(octal, 8))
                at += 1 + len(octal)
                continue
        out += char.encode("utf-8")
        at += 1
    return out.decode("utf-8", "replace")
