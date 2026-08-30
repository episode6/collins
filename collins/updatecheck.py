# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Once a day, ask GitHub whether a newer Collins is out.

The question is one GET of ``/repos/episode6/collins/releases/latest``,
asked through the user's own `gh` when it is installed and signed in — the
same CLI and login the PR features ride (see prstatus), whose rate limit is
five thousand an hour — and, when `gh` can't be used (not on PATH, no
stored credentials, or a call that didn't answer), anonymously over the
public API, which allows sixty an hour and asks no login of anyone. Either
way the answer is compared with the running `__version__`. A newer release
becomes one notification (see
notifycenter.KIND_UPDATE): an in-app card while the user is in Collins, a
desktop notification while they are not, and a row in the history either
way, whose click opens the release's page in the browser. The same release
is announced once, ever: the version told about is remembered beside the
check's timestamp, so a card dismissed on Monday does not come back on
Tuesday, and a row read from the sheet is not re-raised by the next check.

**Once a day** is kept in a small cache file under the app's cache
directory (`cache_path`), not in state.json: losing it costs one query,
never a setting. The app asks at launch and then every hour (`maybe_start`,
which is what the hourly timer calls too), and the file says whether a day
has passed since the last answer. A failed query — offline, GitHub down —
is retried after an hour rather than a day, and never becomes a
notification. On the anonymous path the API's ETag is kept and sent back as
If-None-Match, so the usual answer, "nothing changed", is a 304 that costs
none of the sixty at all (see github-304s-are-free); `gh` has the budget
to spare, so it asks plainly.

**The switch** is *Check for updates* in the General preferences
(``check_for_updates``, on by default): off, `maybe_start` does nothing and
the file is left alone. The harness gates that keep the token refresh off
a screenshot or e2e instance keep this off too (`harnessed`): a canned
usage fixture, or an app id that is neither the release's nor the debug
build's — a capture's throwaway instance must never pop a card over the
window being photographed, and the e2e suite's card counts must not
depend on what GitHub answered today.

GTK-free, like tokenrefresh and claudemodels, so the version comparison,
the cache and the once-a-day rule are unit-tested headless.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import APP_ID, __version__, is_debug_app_id, notifycenter, state
from .i18n import _
from .prstatus import gh_json, gh_succeeds

log = logging.getLogger(__name__)

# The preference that allows a check at all (see `enabled`).
SETTING = "check_for_updates"

REPO = "episode6/collins"
# Where a click lands when the answer named no page of its own.
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
_API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
_TIMEOUT_S = 10
# A day between answers; an hour between attempts after a failed one. The
# app's timer fires every POLL_S and `due` decides whether that is a query.
INTERVAL_S = 24 * 3600
RETRY_AFTER_FAILURE_S = 3600
POLL_S = 3600
_CACHE_VERSION = 1  # bumped if the file's shape changes; another version's file is ignored

# The desktop notification's id, and the key the app withdraws it under
# once no unread update row is left (App._on_notifications_changed).
DESKTOP_KEY = "collins-update"


@dataclass(frozen=True)
class Release:
    """One GitHub release, as much of it as the notification needs."""

    version: str  # "0.1.3" — the tag with its leading v stripped
    tag: str  # "v0.1.3"
    url: str  # the release's page, for the click


# -- versions ------------------------------------------------------------------

_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)((?:[.\-]?(?:dev|a|b|rc|alpha|beta|post)\d*)?)$", re.I)


def parse_version(text: str | None) -> tuple[tuple[int, ...], int] | None:
    """A version as something comparable, or None for anything that isn't
    one: the numeric release segment with trailing zeros dropped (1.2 is
    1.2.0), and a rank that puts a pre-release (0.1.2.dev0, the snapshot
    versions main runs as; 0.1.2rc1) below the release it precedes. A
    post-release ranks above. Tags wear a leading v; it is not part of
    the version."""
    if not isinstance(text, str):
        return None
    match = _VERSION_RE.match(text.strip())
    if match is None:
        return None
    numbers = [int(part) for part in match.group(1).split(".")]
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers.pop()
    suffix = match.group(2).lower()
    rank = 1
    if suffix:
        rank = 2 if "post" in suffix else 0
    return tuple(numbers), rank


def is_newer(candidate: str | None, running: str | None) -> bool:
    """Whether *candidate* is a later version than *running*. False when
    either doesn't parse: a tag that isn't a version is not an update."""
    a = parse_version(candidate)
    b = parse_version(running)
    if a is None or b is None:
        return False
    return a > b


def running_version() -> str:
    return __version__


# -- the API -------------------------------------------------------------------


def parse_release(payload) -> Release | None:
    """A Release out of the API's JSON, or None for anything that isn't
    one: no tag, a tag that isn't a version, a draft or a pre-release (the
    `latest` endpoint lists neither, but a hand-fed payload might)."""
    if not isinstance(payload, dict):
        return None
    if payload.get("draft") or payload.get("prerelease"):
        return None
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or parse_version(tag) is None:
        return None
    url = payload.get("html_url")
    if not isinstance(url, str) or not url.startswith("https://"):
        url = RELEASES_URL
    version = tag.strip()
    if version[:1] in ("v", "V"):
        version = version[1:]
    return Release(version=version, tag=tag.strip(), url=url)


# What a query came back with, besides a Release: GitHub said "unchanged"
# (the anonymous path, answering an ETag), or nothing usable came back.
NOT_MODIFIED = "not-modified"
FAILED = "failed"


def gh_usable() -> bool:
    """Whether the user's `gh` can ask for us: on PATH, with credentials
    stored — asked locally with `gh auth token`, the way ghsetup asks, so a
    laptop on a train isn't told it is signed out. Spawns a subprocess:
    never on the main thread."""
    return shutil.which("gh") is not None and gh_succeeds(["auth", "token"])


def fetch_latest(etag: str | None = None, timeout: float = _TIMEOUT_S) -> tuple[Release | str, str | None]:
    """One query, through `gh` when it can be used and anonymously
    otherwise. Returns the Release and the ETag to send next time (the
    anonymous path's; `gh` hands back none), NOT_MODIFIED with the ETag
    that matched when *etag* still stands, or FAILED with no ETag for every
    way of not getting an answer — which is logged first, since offline and
    rate-limited are the same "no" to the caller and different things to
    fix. A `gh` that is set up but didn't answer counts as one that can't
    be used: the anonymous path is tried before giving up."""
    if gh_usable():
        release = fetch_via_gh(timeout)
        if release is not None:
            return release, None
    return fetch_anonymous(etag, timeout)


def fetch_via_gh(timeout: float = _TIMEOUT_S) -> Release | None:
    """The query through the user's `gh` (see prstatus.gh_json). None for
    any way of not getting a release out of it."""
    payload = gh_json(
        ["api", f"repos/{REPO}/releases/latest", "-H", "X-GitHub-Api-Version: 2022-11-28"],
        timeout=timeout,
    )
    release = parse_release(payload)
    if release is None:
        log.info("update check: gh api gave no usable release; asking anonymously")
    return release


def fetch_anonymous(etag: str | None = None, timeout: float = _TIMEOUT_S) -> tuple[Release | str, str | None]:
    """The query over the public API with no token: fetch_latest's
    contract, for the machine with no `gh` to ask."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"collins/{__version__} (+https://github.com/{REPO})",
    }
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(_API_URL, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
            new_etag = response.headers.get("ETag")
    except urllib.error.HTTPError as err:
        if err.code == 304:
            return NOT_MODIFIED, etag
        log.warning("update check: GET %s answered %s", _API_URL, err.code)
        return FAILED, None
    except Exception as err:  # URLError, timeout, bad JSON, ...
        log.warning("update check: GET %s failed: %r", _API_URL, err)
        return FAILED, None
    release = parse_release(payload)
    if release is None:
        log.warning("update check: %s answered with no usable release", _API_URL)
        return FAILED, None
    return release, new_etag


# -- the cache -----------------------------------------------------------------


def cache_path() -> Path:
    """Where the last answer lives: under the app's cache directory, honoring
    XDG_CACHE_HOME the way claudemodels.cache_path does, so tests and the
    screenshot harness relocate it with the rest of the app's state."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "collins" / "update-check.json"


def read_record() -> dict:
    """The saved answer: `checked_at` (the last successful query's wall-clock
    time), `failed_at` (the last failed one's, cleared by a success),
    `latest` and `url` (what GitHub last named), `etag`, and `notified` (the
    version last announced). {} for every way of not having one — no file
    yet, another version's shape, unreadable — all of them ordinary."""
    path = cache_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as err:
        log.debug("update check: cannot read %s: %r", path, err)
        return {}
    if not isinstance(payload, dict) or payload.get("version") != _CACHE_VERSION:
        return {}
    record = {}
    for key in ("checked_at", "failed_at"):
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            record[key] = float(value)
    for key in ("latest", "url", "etag", "notified"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            record[key] = value
    return record


def write_record(record: dict) -> None:
    """Save the answer for the next check. Best effort: a cache that can't
    be written costs a query next time, not anything anyone need act on."""
    path = cache_path()
    payload = {"version": _CACHE_VERSION, **record}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)  # atomic: a half-written file would poison the next read
    except OSError as err:
        log.warning("update check: cannot save %s: %r", path, err)


def due(record: dict, now: float | None = None) -> bool:
    """Whether a query is called for: none yet, or the last answer is a day
    old — and the last failure, if it is more recent than the last answer,
    an hour old. A clock that reads earlier than the file (a machine whose
    time went back) is due: waiting out a day that has already passed
    would be the wrong direction to be wrong in."""
    if now is None:
        now = time.time()
    checked_at = record.get("checked_at")
    failed_at = record.get("failed_at")
    if checked_at is not None and 0 <= now - checked_at < INTERVAL_S:
        return False
    if failed_at is not None and 0 <= now - failed_at < RETRY_AFTER_FAILURE_S:
        return False
    return True


# -- the gates -----------------------------------------------------------------


def enabled() -> bool:
    """Whether the switch is on. Read off a fresh AppState rather than one
    handed in, the way tokenrefresh.enabled does: a preference flipped a
    moment ago has to govern the very next attempt."""
    return bool(state.AppState().get_setting(SETTING))


def harnessed() -> bool:
    """Whether this is a screenshot or e2e instance, which never checks:
    the canned usage fixture, or an app id that is neither the release's
    nor a debug build's (a capture's com.episode6.Collins.E2E.<run>)."""
    if os.environ.get("COLLINS_USAGE_FIXTURE"):
        return True
    app_id = os.environ.get("COLLINS_APP_ID")
    return bool(app_id) and app_id != APP_ID and not is_debug_app_id(app_id)


# -- the check -----------------------------------------------------------------

_lock = threading.Lock()
_running = False


def check(now: float | None = None) -> Release | None:
    """One check, blocking (up to the network timeout) — call from a worker
    thread. Reads the record, queries when `due`, saves what came back, and
    returns the Release to announce: newer than the running version and
    not the one already announced. None otherwise, which covers the common
    day — not due, unchanged, up to date, told already, or failed."""
    if now is None:
        now = time.time()
    record = read_record()
    if not due(record, now):
        return None
    answer, etag = fetch_latest(record.get("etag"))
    if answer == FAILED:
        record["failed_at"] = now
        write_record(record)
        return None
    record.pop("failed_at", None)
    record["checked_at"] = now
    if isinstance(answer, Release):
        record["latest"] = answer.version
        record["url"] = answer.url
        if etag:
            record["etag"] = etag
        else:
            record.pop("etag", None)
        latest = answer
    else:  # NOT_MODIFIED: what the file already names still stands
        version, url = record.get("latest"), record.get("url")
        if not version:
            # An ETag with nothing behind it (a hand-edited file): ask
            # again next time without it.
            record.pop("etag", None)
            write_record(record)
            return None
        latest = Release(version=version, tag=f"v{version}", url=url or RELEASES_URL)
    announce = is_newer(latest.version, running_version()) and record.get("notified") != latest.version
    if announce:
        record["notified"] = latest.version
    write_record(record)
    log.debug(
        "update check: latest %s, running %s%s",
        latest.version,
        running_version(),
        " — announcing" if announce else "",
    )
    return latest if announce else None


def maybe_start(on_update: Callable[[Release], None]) -> threading.Thread | None:
    """The app's entry, at launch and from the hourly timer: when the switch
    is on, this isn't a harness, and no check is already running, run
    `check` on a daemon thread and hand what it returns — a release worth
    announcing — to *on_update*, on that thread (the caller marshals to its
    main loop). Returns the thread, or None when nothing was started; the
    caller needs neither, tests join. The record is read on the thread —
    it is file I/O — so a launch never waits on it."""
    global _running
    if harnessed() or not enabled():
        return None
    with _lock:
        if _running:
            return None
        _running = True

    def work() -> None:
        global _running
        try:
            release = check()
        finally:
            with _lock:
                _running = False
        if release is not None:
            on_update(release)

    thread = threading.Thread(target=work, name="update-check", daemon=True)
    thread.start()
    return thread


# -- the notification ----------------------------------------------------------


def notification(center: notifycenter.NotificationCenter, release: Release) -> notifycenter.Notification:
    """The row for *release*, ready to post: titled with the version, its
    body naming the one running, keyed by the version (notifycenter.update_id)
    so the center replaces any older update row with it, and carrying the
    release page's URL for the click. Translated when made and persisted as
    such, like a bell's body."""
    row = center.make(
        notifycenter.KIND_UPDATE,
        "",
        _("Collins {version} is available").format(version=release.version),
        "",
        _("You're running {version}. Click to open the release on GitHub").format(
            version=running_version()
        ),
    )
    row.id = notifycenter.update_id(release.version)
    row.url = release.url or RELEASES_URL
    return row


def retire(center: notifycenter.NotificationCenter, running: str | None = None) -> int:
    """Drop every update row that no longer names an update — the user
    installed it (or something newer) since the row was posted. Called at
    launch, before anything paints the history. Returns how many went."""
    if running is None:
        running = running_version()
    gone = 0
    for row in center.rows():
        if row.kind != notifycenter.KIND_UPDATE:
            continue
        if not is_newer(notifycenter.update_version(row.id), running):
            center.remove(row.id)
            gone += 1
    return gone
