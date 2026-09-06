# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Once a day, trash the archived sessions that have sat archived long
enough.

**The setting** is a pair in Preferences → Session behavior: a number
(``auto_delete_archived_after``) and a unit (``auto_delete_archived_unit``:
days, weeks, months, years). The number defaults to 0, which means never —
nothing here runs until the user picks a span. A month counts as thirty
days and a year as 365: the span is a rule of thumb for "old enough", not
a calendar promise.

**What "archived long enough" is measured from** is the moment the session
was archived, which AppState stamps into ``archived_at`` beside the
``archived`` set (an unarchive drops the stamp, so a restored-then-archived
session starts its clock over). Sessions archived before the stamp existed
are stamped when the newer state.json is first read: the clock starts at
the upgrade, never earlier — deleting on a date the app can only guess is
the wrong direction to be wrong in. Only sessions archived one by one (or
in a bulk archive) count; a session that is out of sight because its whole
*project* is archived carries no stamp and is left alone, since archiving
a project is a statement about the folder, not each conversation in it.

**What deletion is** is the sidebar's *Move transcript to trash*: the
transcript goes to the system trash (recoverable), and everything the app
kept for it (panel layout, scrollback, PRs, attachments, draft) goes with
it, through the same window path the manual bulk delete uses
(`MainWindow.trash_expired_archives`). A session that is still running — a
tab open in any window, or a background agent — is skipped until it isn't.

**Once a day** is kept the way updatecheck keeps its own: a small cache
file under the app's cache directory (`cache_path`) records when the last
sweep ran. Losing it costs one early sweep, never a setting. The app calls
`maybe_sweep` at launch and then every hour (the same timer the update
check rides); `due` says whether a day has passed. A sweep that finds
nothing to delete still counts as done for the day.

GTK-free: the span arithmetic, the expiry test, the cache and the
once-a-day rule are unit-tested headless.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterable
from pathlib import Path

log = logging.getLogger(__name__)

# The two settings (see state.DEFAULT_SETTINGS).
SETTING_COUNT = "auto_delete_archived_after"
SETTING_UNIT = "auto_delete_archived_unit"

_DAY_S = 24 * 3600

# The unit drop-down, in order: the setting value, its label (N_-style —
# prefs translates at use) and how many seconds one of it is.
UNITS: tuple[tuple[str, str, int], ...] = (
    ("days", "Days", _DAY_S),
    ("weeks", "Weeks", 7 * _DAY_S),
    ("months", "Months", 30 * _DAY_S),
    ("years", "Years", 365 * _DAY_S),
)
DEFAULT_UNIT = "months"
# The spin row's ceiling; anything past it is a typo.
MAX_COUNT = 999

# A day between sweeps. The app's timer fires every POLL_S (updatecheck's
# hour, which the sweep shares) and `due` decides whether that is a sweep.
INTERVAL_S = _DAY_S
_CACHE_VERSION = 1  # bumped if the file's shape changes; another version's file is ignored


# -- the span ------------------------------------------------------------------


def unit_seconds(unit: str | None) -> int | None:
    """How long one *unit* is, or None for a unit that isn't one of ours
    (a hand-edited state.json): an unknown unit is a span of nothing, and
    a span of nothing never deletes."""
    for value, _label, seconds in UNITS:
        if value == unit:
            return seconds
    return None


def retention_seconds(count, unit: str | None) -> float | None:
    """The span a session must sit archived before it goes, in seconds, or
    None for "never": a count of 0 (the default), a count that isn't a
    whole number, a negative one, or a unit that isn't ours."""
    if isinstance(count, bool) or not isinstance(count, (int, float)):
        return None
    if count != int(count) or count <= 0 or count > MAX_COUNT:
        return None
    seconds = unit_seconds(unit)
    if seconds is None:
        return None
    return float(int(count) * seconds)


def expired(
    archived_at: dict[str, float], retention_s: float | None, now: float | None = None
) -> list[str]:
    """The session ids whose archive stamp is at least *retention_s* old.
    Nothing for a retention of None (never). A stamp that reads later than
    *now* (a clock that went back) has not expired: it is not old, however
    the arithmetic works out."""
    if retention_s is None:
        return []
    if now is None:
        now = time.time()
    out: list[str] = []
    for session_id, stamp in archived_at.items():
        if not isinstance(stamp, (int, float)) or isinstance(stamp, bool):
            continue
        age = now - stamp
        if 0 <= age and age >= retention_s:
            out.append(session_id)
    return out


def expired_for_settings(
    settings: dict, archived_at: dict[str, float], now: float | None = None
) -> list[str]:
    """`expired` read straight off a settings dict — what the app hands the
    sweep."""
    retention = retention_seconds(settings.get(SETTING_COUNT), settings.get(SETTING_UNIT))
    return expired(archived_at, retention, now)


def describe(count, unit: str | None) -> str | None:
    """A log-friendly "3 months", or None for never."""
    if retention_seconds(count, unit) is None:
        return None
    return f"{int(count)} {unit}"


# -- the cache -----------------------------------------------------------------


def cache_path() -> Path:
    """Where the last sweep's time lives: under the app's cache directory,
    honoring XDG_CACHE_HOME the way updatecheck.cache_path does, so tests
    and the screenshot harness relocate it with the rest of the app's
    state."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "collins" / "archive-sweep.json"


def read_record() -> dict:
    """The saved record: `swept_at` (the last sweep's wall-clock time). {}
    for every way of not having one — no file yet, another version's
    shape, unreadable — all of them ordinary."""
    path = cache_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as err:
        log.debug("archive sweep: cannot read %s: %r", path, err)
        return {}
    if not isinstance(payload, dict) or payload.get("version") != _CACHE_VERSION:
        return {}
    record = {}
    value = payload.get("swept_at")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        record["swept_at"] = float(value)
    return record


def write_record(record: dict) -> None:
    """Save the record for the next check. Best effort: a cache that can't
    be written costs an early sweep next launch, not anything anyone need
    act on."""
    path = cache_path()
    payload = {"version": _CACHE_VERSION, **record}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)  # atomic: a half-written file would poison the next read
    except OSError as err:
        log.warning("archive sweep: cannot save %s: %r", path, err)


def due(record: dict, now: float | None = None) -> bool:
    """Whether a sweep is called for: none yet, or the last one a day old.
    A clock that reads earlier than the file (a machine whose time went
    back) is due: waiting out a day that has already passed would be the
    wrong direction to be wrong in."""
    if now is None:
        now = time.time()
    swept_at = record.get("swept_at")
    if swept_at is not None and 0 <= now - swept_at < INTERVAL_S:
        return False
    return True


# -- the sweep -----------------------------------------------------------------


def maybe_sweep(
    settings: dict, archived_at: dict[str, float], trash, now: float | None = None
) -> list[str] | None:
    """One sweep when the setting is on and a day has passed: works out
    which sessions have expired, hands them to *trash* (which takes the
    list of ids and returns the ids it could not trash — the window's
    `trash_expired_archives`), and stamps the day as done. Returns the ids
    trashed, or None when nothing ran (the setting is off, or the day isn't
    up). The stamp is written whether or not anything expired, and even
    when *trash* refused some: a session the window skipped (still
    running) is picked up tomorrow, and one it couldn't trash (a file that
    won't move) is not something to retry every hour.

    Call from the main loop: *trash* touches the window."""
    if now is None:
        now = time.time()
    span = describe(settings.get(SETTING_COUNT), settings.get(SETTING_UNIT))
    if span is None:
        return None
    if not due(read_record(), now):
        return None
    doomed = expired_for_settings(settings, archived_at, now)
    trashed: list[str] = []
    if doomed:
        failed = set(trash(list(doomed)) or ())
        trashed = [sid for sid in doomed if sid not in failed]
        log.info(
            "archive sweep: %d session(s) archived over %s trashed, %d skipped",
            len(trashed),
            span,
            len(doomed) - len(trashed),
        )
    else:
        log.debug("archive sweep: nothing archived over %s", span)
    write_record({"swept_at": now})
    return trashed


def stamp_missing(
    archived: Iterable[str], archived_at: dict[str, float], now: float | None = None
) -> dict[str, float]:
    """`archived_at` with a stamp for every archived id that has none (an
    archive from before the stamp existed: the clock starts now) and none
    for ids that are no longer archived (a hand-edited file). What
    AppState reads state.json through."""
    if now is None:
        now = time.time()
    archived = set(archived)
    out = {
        sid: float(stamp)
        for sid, stamp in archived_at.items()
        if sid in archived and isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
    }
    for sid in archived:
        out.setdefault(sid, now)
    return out
