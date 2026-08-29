# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Every notification Collins has raised, and where each one goes — worked
out without a toolkit.

The way traymodel.py owns what the status icon shows, this module owns what
the notification history holds and what the badges count: pure Python, unit-
tested in CI (which has no GTK typelibs; see tests/conftest.py), imported by
the widgets rather than the other way round. Everything decidable lives here
— routing, coalescing, counting, persistence — and the widgets that show it
(the header bell, the history sheet, the in-app cards, the tray and dock
badges) hang off one `changed()` callback.

**The badge counts unread notifications**: every unlooked-at finished run is
one (a *synthetic* row, see set_green), plus every message and bell nobody
has gone to. The synthetic row tracks the sidebar's green pulse one-for-one —
it appears when a session's unread flag comes on and leaves, rather than
being marked read, when the flag goes off — so for a user who never receives
a `notify_user` message or a bell the badge is the sidebar's pulses, counted.
That is what the tray showed before there was a history at all, with one
difference: the old number was counted per open *tab* and this one per green
*row*, and the two part company where a row stands in for a tab it doesn't
own — a tab attached to a /bg fork the store hasn't discovered pulses the
row it forked from, and the badge now counts that pulse where the tab list
skipped it (see App._sync_green).

**What survives a restart**: message and bell rows, read flags included — a
notification you missed before quitting is the kind of thing the history
exists for. Synthetic rows are never persisted: the unread flag they stand
for is in-memory only, so green doesn't survive a restart and neither does
its row. The persisted list is newest first, capped at ROW_CAP rows and
pruned of anything older than KEEP_DAYS on load (see clean_records).

**Where a notification goes** is `delivery()`: a pure function of the kind
of notification and where the user is, returning the set of things to do.
The table is the spec's; the names it returns are the vocabulary the window
wires to widgets (see the DELIVER_* constants).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .i18n import _

# What raised the notification. A `finished` row is synthetic — the green
# flag's row, owned by set_green — and never persisted.
KIND_MESSAGE = "message"  # the notify_user tool
KIND_BELL = "bell"  # a terminal's BEL
KIND_FINISHED = "finished"  # a run finished and nobody has looked
KINDS = frozenset({KIND_MESSAGE, KIND_BELL, KIND_FINISHED})

# Where the user is, relative to the session that spoke up. "Active" is
# Gtk.Window.is_active(); the app-level answer is "any main window is active".
FOCUS_SELECTED = "selected"  # a Collins window is active and the tab is its selected page
FOCUS_ELSEWHERE = "elsewhere"  # a Collins window is active; the tab is another tab, anywhere
FOCUS_UNFOCUSED = "unfocused"  # no Collins window is active: hidden, or behind another app
FOCUSES = frozenset({FOCUS_SELECTED, FOCUS_ELSEWHERE, FOCUS_UNFOCUSED})

# The things delivery() can ask for. Each is one widget-side act; the window
# does the ones it is handed and nothing else.
DELIVER_CARD = "card"  # the in-app card, in the active window
DELIVER_SOUND = "sound"  # the notification sound (only ever beside a card)
DELIVER_ROW = "row"  # a history row, unread (for `finished`: the synthetic row set_green owns)
DELIVER_ROW_READ = "row-read"  # a history row that is already read
DELIVER_FLAG = "flag"  # flag the session's sidebar row unread (MainWindow._flag_unread)
DELIVER_FLASH = "flash"  # flash the header bar, tab and row (MainWindow._flash_session)
DELIVER_DESKTOP = "desktop"  # a desktop notification, id = session id (replaces)
DELIVER_BEEP = "beep"  # the compositor's beep (Gdk.Display.beep)
DELIVERIES = frozenset(
    {
        DELIVER_CARD,
        DELIVER_SOUND,
        DELIVER_ROW,
        DELIVER_ROW_READ,
        DELIVER_FLAG,
        DELIVER_FLASH,
        DELIVER_DESKTOP,
        DELIVER_BEEP,
    }
)

# A synthetic row's id is its session's, prefixed, so set_green can find the
# row it owns without a search and nothing else can collide with it.
GREEN_PREFIX = "green:"

# The persisted list's bounds. Two hundred rows is weeks of a busy fleet;
# older than a fortnight, a notification is no longer news to anyone.
ROW_CAP = 200
KEEP_DAYS = 14
KEEP_SECONDS = KEEP_DAYS * 24 * 60 * 60

# The delivery table, kind × focus (see the module docstring and the spec).
# `finished` has no `selected` cell: a selected tab never goes green.
_TABLE: dict[tuple[str, str], frozenset[str]] = {
    (KIND_MESSAGE, FOCUS_SELECTED): frozenset({DELIVER_ROW_READ, DELIVER_FLASH}),
    (KIND_MESSAGE, FOCUS_ELSEWHERE): frozenset(
        {DELIVER_CARD, DELIVER_SOUND, DELIVER_ROW, DELIVER_FLAG, DELIVER_FLASH}
    ),
    (KIND_MESSAGE, FOCUS_UNFOCUSED): frozenset({DELIVER_DESKTOP, DELIVER_ROW, DELIVER_FLAG}),
    (KIND_BELL, FOCUS_SELECTED): frozenset({DELIVER_BEEP, DELIVER_FLASH}),
    (KIND_BELL, FOCUS_ELSEWHERE): frozenset({DELIVER_CARD, DELIVER_SOUND, DELIVER_ROW, DELIVER_FLASH}),
    (KIND_BELL, FOCUS_UNFOCUSED): frozenset({DELIVER_DESKTOP, DELIVER_ROW, DELIVER_FLASH}),
    (KIND_FINISHED, FOCUS_SELECTED): frozenset(),
    (KIND_FINISHED, FOCUS_ELSEWHERE): frozenset({DELIVER_ROW}),
    (KIND_FINISHED, FOCUS_UNFOCUSED): frozenset({DELIVER_ROW}),
}


def delivery(kind: str, focus: str, announce_finished_runs: bool = False) -> frozenset[str]:
    """What to do for a notification of *kind* when the user is at *focus*.

    Three things the table settles. A message to the tab the user is looking
    at is not lost: it lands in the history as a read row, and the tool reply
    can say so — nothing silently does nothing, and nobody is banner-ed about
    the terminal in front of them. A bell from an unfocused Collins becomes a
    desktop notification, not just a beep, because the beep never said which
    of six sessions rang; a bell from the *selected* tab keeps the compositor
    beep and gets no row, since a bell you were there for is not history.
    And the sound plays only beside an in-app card: the desktop sounds its
    own notifications, and ours on top would ring twice.

    A `finished` run is a row and nothing more — the synthetic row set_green
    already put there — unless *announce_finished_runs* is on, in which case
    it goes out exactly as a message would: a card and the sound when the
    user is elsewhere in Collins, a desktop notification when they are not.
    A selected tab never goes green, so that cell stays empty either way.

    Unknown kinds and focuses raise rather than deliver nothing: a typo in a
    caller must not read as "the table said to stay quiet".
    """
    if kind not in KINDS:
        raise ValueError(f"unknown notification kind: {kind!r}")
    if focus not in FOCUSES:
        raise ValueError(f"unknown focus: {focus!r}")
    if kind == KIND_FINISHED and announce_finished_runs and focus != FOCUS_SELECTED:
        return _TABLE[(KIND_MESSAGE, focus)]
    return _TABLE[(kind, focus)]


def green_id(session_id: str) -> str:
    """The id of the synthetic row standing for *session_id*'s green flag."""
    return GREEN_PREFIX + session_id


def is_green_id(notification_id: str) -> bool:
    return notification_id.startswith(GREEN_PREFIX)


@dataclass
class Notification:
    """One thing Collins told the user about.

    `session_id` is what a click on the row jumps to. For a message or a
    bell it is the session's id, or "" for a tab whose id hadn't resolved
    when it spoke (the card then routes to the tab's window — see the spec).
    For a synthetic row it is whatever key the green was set under: the
    session id, or, for a tab still waiting on its id, the placeholder id
    the sidebar knows the row by (`placeholder-N`, or a new-chat draft id),
    which the owning window can select a tab by.
    """

    id: str  # uuid4; synthetic rows use "green:" + session_id
    session_id: str
    title: str  # the session title at raise time
    project: str  # project name, for the row's footer / eyebrow
    kind: str  # KIND_MESSAGE | KIND_BELL | KIND_FINISHED
    body: str  # the message; "Rang the bell" / "Finished a run"
    when: float  # time.time()
    read: bool = False
    count: int = 1  # bells coalesce: "Rang the bell ×3"

    def to_record(self) -> dict:
        """The row as state.json holds it."""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "title": self.title,
            "project": self.project,
            "kind": self.kind,
            "body": self.body,
            "when": self.when,
            "read": self.read,
            "count": self.count,
        }

    @classmethod
    def from_record(cls, raw) -> Notification | None:
        """A row read back from state.json, or None for anything that isn't
        one. The file is ours but not sacred: a hand edit, a crash mid-write
        of an older build, or a newer build's shape must cost at most the row
        it broke, never the list."""
        if not isinstance(raw, dict):
            return None
        notification_id = raw.get("id")
        kind = raw.get("kind")
        when = raw.get("when")
        if not isinstance(notification_id, str) or not notification_id:
            return None
        if kind not in KINDS:
            return None
        if isinstance(when, bool) or not isinstance(when, (int, float)):
            return None
        count = raw.get("count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            count = 1

        def text(key: str) -> str:
            value = raw.get(key, "")
            return value if isinstance(value, str) else ""

        return cls(
            id=notification_id,
            session_id=text("session_id"),
            title=text("title"),
            project=text("project"),
            kind=kind,
            body=text("body"),
            when=float(when),
            read=bool(raw.get("read", False)),
            count=count,
        )


def clean_records(raw, now: float | None = None) -> list[dict]:
    """The persisted list, made safe to load: garbage dropped, synthetic rows
    dropped (they never belong on disk, whatever wrote them), rows older than
    KEEP_DAYS pruned, newest first, at most ROW_CAP of them.

    Idempotent, and used on both sides of the file — AppState runs it on
    load, and the center runs it again on whatever it is handed — so neither
    has to trust the other to have done it.
    """
    if now is None:
        now = time.time()
    if not isinstance(raw, list):
        return []
    rows: list[Notification] = []
    seen: set[str] = set()
    for entry in raw:
        row = Notification.from_record(entry)
        if row is None or row.kind == KIND_FINISHED or is_green_id(row.id):
            continue
        if row.when < now - KEEP_SECONDS or row.id in seen:
            continue
        seen.add(row.id)
        rows.append(row)
    rows.sort(key=lambda row: -row.when)
    return [row.to_record() for row in rows[:ROW_CAP]]


class NotificationCenter:
    """The list, newest first, and the one event everything hangs off.

    Listeners are called synchronously, in the order they connected, after
    every change to the list — including a change that only touched a row's
    fields. Consumers that paint should coalesce on an idle (the status icon
    already does): two back-to-back calls, as the placeholder → real-row
    handoff makes, must not flicker a badge.

    `clock` is time.time unless a test says otherwise.
    """

    def __init__(
        self, records: Iterable[dict] | None = None, *, clock: Callable[[], float] = time.time
    ) -> None:
        self._clock = clock
        self._rows: list[Notification] = []
        self._listeners: list[Callable[[], None]] = []
        for record in clean_records(list(records or []), now=clock()):
            row = Notification.from_record(record)
            if row is not None:
                self._rows.append(row)

    # -- listeners ------------------------------------------------------------

    def connect(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Hear about every change. Returns the callback, for disconnect."""
        if callback not in self._listeners:
            self._listeners.append(callback)
        return callback

    def disconnect(self, callback: Callable[[], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _changed(self) -> None:
        for callback in list(self._listeners):
            callback()

    # -- reading --------------------------------------------------------------

    def rows(self) -> list[Notification]:
        """Every row, newest first. The rows are the center's own objects —
        read them, don't write them; every change goes through a method so
        the listeners hear it."""
        return list(self._rows)

    def get(self, notification_id: str) -> Notification | None:
        for row in self._rows:
            if row.id == notification_id:
                return row
        return None

    def unread_count(self) -> int:
        """The badge: how many rows nobody has gone to or waved off."""
        return sum(1 for row in self._rows if not row.read)

    def is_green(self, session_id: str) -> bool:
        return self.get(green_id(session_id)) is not None

    def to_records(self) -> list[dict]:
        """What state.json should hold right now: the persistable rows, newest
        first, within the cap. Synthetic rows are left out (see the module
        docstring)."""
        return [row.to_record() for row in self._rows if row.kind != KIND_FINISHED][:ROW_CAP]

    # -- writing --------------------------------------------------------------

    def post(self, notification: Notification) -> Notification:
        """Add a row at the top, and say so.

        A bell for a session that already has an *unread* bell row bumps that
        row's count and time and brings it back to the top instead of adding
        another — the desktop-notification "replace by session id" rule,
        kept: five bells from one shell are one thing to look at, "Rang the
        bell ×5". A read bell row is not bumped; the user dealt with it, and
        this is a new one. A message never coalesces: two messages are two
        things to read. Rows with no session id (a tab whose id hadn't
        resolved) never coalesce either — there is no session to say they
        are the same.

        Synthetic rows are set_green's to add, never post's.
        """
        if notification.kind not in KINDS:
            raise ValueError(f"unknown notification kind: {notification.kind!r}")
        if notification.kind == KIND_FINISHED or is_green_id(notification.id):
            raise ValueError("finished rows are set_green's to add")
        if notification.kind == KIND_BELL and notification.session_id:
            for row in self._rows:
                if row.kind == KIND_BELL and not row.read and row.session_id == notification.session_id:
                    row.count += notification.count
                    row.when = notification.when
                    row.title = notification.title or row.title
                    row.project = notification.project or row.project
                    self._rows.remove(row)
                    self._rows.insert(0, row)
                    self._changed()
                    return row
        self._rows.insert(0, notification)
        self._trim()
        self._changed()
        return notification

    def make(
        self,
        kind: str,
        session_id: str,
        title: str,
        project: str,
        body: str,
    ) -> Notification:
        """A fresh row for post(): a new id and the clock's time."""
        return Notification(
            id=uuid.uuid4().hex,
            session_id=session_id,
            title=title,
            project=project,
            kind=kind,
            body=body,
            when=self._clock(),
        )

    def _trim(self) -> None:
        """Keep the persistable rows within the cap by dropping the oldest.
        Synthetic rows don't count and are never dropped here: they leave
        with their green flag, and a badge that lost a finished run to a
        flood of bells would be lying about the sidebar."""
        kept = 0
        trimmed: list[Notification] = []
        for row in self._rows:
            if row.kind == KIND_FINISHED:
                trimmed.append(row)
                continue
            if kept < ROW_CAP:
                trimmed.append(row)
                kept += 1
        self._rows = trimmed

    def mark_read(self, notification_id: str) -> bool:
        """One row read: the user clicked it, or said so. False if there was
        no such row or it was read already."""
        row = self.get(notification_id)
        if row is None or row.read:
            return False
        row.read = True
        self._changed()
        return True

    def mark_session_read(self, session_id: str) -> int:
        """Every row of a session read — what focusing the session calls,
        mirroring the desktop notification's withdraw-on-unread-off: visiting
        the session is reading everything it said. Returns how many moved.
        A "" session id matches nothing: rows with no session can't be
        visited by id."""
        if not session_id:
            return 0
        moved = 0
        for row in self._rows:
            if row.session_id == session_id and not row.read:
                row.read = True
                moved += 1
        if moved:
            self._changed()
        return moved

    def mark_all_read(self) -> int:
        """The sheet's button: everything read, synthetic rows included — the
        badge goes to zero because the user said so. A synthetic row still
        leaves when its green does, not before."""
        moved = 0
        for row in self._rows:
            if not row.read:
                row.read = True
                moved += 1
        if moved:
            self._changed()
        return moved

    def clear(self) -> int:
        """The sheet's other button: drop read and unread rows alike — except
        synthetic rows, which are the green flag's to remove. Returns how
        many went."""
        kept = [row for row in self._rows if row.kind == KIND_FINISHED]
        gone = len(self._rows) - len(kept)
        if gone:
            self._rows = kept
            self._changed()
        return gone

    def remove(self, notification_id: str) -> bool:
        """Drop one row, read or not (the row's own Remove). Synthetic rows
        are refused for the same reason clear() skips them."""
        row = self.get(notification_id)
        if row is None or row.kind == KIND_FINISHED:
            return False
        self._rows.remove(row)
        self._changed()
        return True

    def set_green(self, session_id: str, on: bool, *, title: str = "", project: str = "") -> bool:
        """The synthetic row: *on* inserts `finished` row `green:<session_id>`
        if absent (unread, at the top); *off* removes it. Both are no-ops when
        there is nothing to do, so callers can re-assert the state on every
        edge they see without the listeners hearing about non-events.

        Called from wherever the sidebar's green pulse is decided — the
        store's unread-changed edge for real rows, the window's placeholder
        path for tabs still waiting on an id — so the row tracks the pulse
        exactly. What "green" means (unread and not working) is the caller's
        rule to apply; this only records the answer. The placeholder → real
        row handoff calls it twice, off for the placeholder's key and on for
        the session's: the count dips for the space between two synchronous
        calls, which no idle-coalesced consumer can see.

        `title` and `project` name the row on the way in and are ignored on
        the way out. Returns whether anything changed.
        """
        if not session_id:
            return False
        row = self.get(green_id(session_id))
        if on:
            if row is not None:
                return False
            self._rows.insert(
                0,
                Notification(
                    id=green_id(session_id),
                    session_id=session_id,
                    title=title,
                    project=project,
                    kind=KIND_FINISHED,
                    body=_("Finished a run"),
                    when=self._clock(),
                ),
            )
        else:
            if row is None:
                return False
            self._rows.remove(row)
        self._changed()
        return True

    def green_sessions(self) -> list[str]:
        """The keys every synthetic row currently stands under, for a caller
        reconciling them against what still exists (see App)."""
        return [row.session_id for row in self._rows if row.kind == KIND_FINISHED]
