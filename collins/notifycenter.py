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

**What survives a restart**: message, bell and update rows, read flags
included — a notification you missed before quitting is the kind of thing
the history exists for. (An update row — a newer Collins is out, see
updatecheck — is the one kind with no session behind it: its click opens
the release's page, and the launch that installed it retires the row.)
Synthetic rows are never persisted: the unread flag they stand
for is in-memory only, so green doesn't survive a restart and neither does
its row. The persisted list is newest first, capped at ROW_CAP rows and
pruned of anything older than KEEP_DAYS on load (see clean_records).

**Where a notification goes** is `delivery()`: a pure function of the kind
of notification and where the user is, returning the set of things to do.
The table is the spec's; the names it returns are the vocabulary the window
wires to widgets (see the DELIVER_* constants). Where the user *is* —
`focus_state()` — and what the two switches do to the table (`without_cards`
for a user who turned the in-app cards off) are decided here too, as is what
the `notify_user` tool is told about it all (`tool_reply`).

**What the sound is** is also worked out here, short of playing it: the
notification_sound setting's three shapes (SOUND_DEFAULT, SOUND_NONE, a
path), which file "default" means on this desktop (`sound_file`, walking the
desktop's sound theme for its message sound), and what the preferences row
and the sheet's footer call the choice. notifysound.py plays whatever this
resolves to.

**What the widgets say** is decided here too, where it is string work: the
bell's tooltip, a row's relative time, a coalesced bell's "×3", the split of
the list into its unread and earlier halves (see the helpers after the
class). notifypanel.py lays those out; it never works the words out itself.
"""

from __future__ import annotations

import os
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
KIND_UPDATE = "update"  # a newer Collins is out (see updatecheck)
KINDS = frozenset({KIND_MESSAGE, KIND_BELL, KIND_FINISHED, KIND_UPDATE})

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

# The notification_sound setting: the desktop's own message sound, silence,
# or an absolute path to a file of the user's. Anything else is a path.
SOUND_DEFAULT = "default"
SOUND_NONE = "none"

# Where "default" looks: the freedesktop sound-theme layout, under the
# desktop's chosen theme first (GNOME's org.gnome.desktop.sound theme-name —
# Yaru on Ubuntu, whose message sound is its own) and the freedesktop theme
# second, which sound-theme-freedesktop installs on Ubuntu and Fedora desktops
# alike. The event is the one a desktop plays for a new message. Collins
# ships no sound of its own: nothing to license, and the theme's sound is
# what the desktop already plays for exactly this.
SOUND_THEME_ROOT = "/usr/share/sounds"
SOUND_FALLBACK_THEME = "freedesktop"
SOUND_EVENT = "stereo/message-new-instant.oga"

# The in-app card's own light/dark (the notification_color_scheme setting,
# and the Card theme row's three choices, in its order): CARD_SCHEME_APP
# paints the card in whatever the app is; the other two pin it. Each pinned
# one is a CSS class on the card's body, under which app.py's _CSS re-pins
# Adwaita's card colors (--card-bg-color and friends) — the card is the one
# widget under the class, so nothing else in the window changes.
CARD_SCHEME_APP = "app"
CARD_SCHEME_LIGHT = "light"
CARD_SCHEME_DARK = "dark"
CARD_SCHEMES = (CARD_SCHEME_APP, CARD_SCHEME_LIGHT, CARD_SCHEME_DARK)
CARD_SCHEME_CLASSES = {
    CARD_SCHEME_LIGHT: "notification-card-light",
    CARD_SCHEME_DARK: "notification-card-dark",
}

# The `notify_user` tool's replies: what happened, since the model can only
# know by asking. Plain English, not translated — they go to the agent.
REPLY_IN_APP = "The user was notified in Collins."
REPLY_DESKTOP = "The user was notified on their desktop."
REPLY_SELECTED = (
    "The user is looking at this session; the message is in their notification history."
)

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
    # An update has no session: nothing to flag or flash, no tab to be
    # looking at — in Collins it is a card and the sound, away from it the
    # desktop notification, and a row either way.
    (KIND_UPDATE, FOCUS_SELECTED): frozenset({DELIVER_CARD, DELIVER_SOUND, DELIVER_ROW}),
    (KIND_UPDATE, FOCUS_ELSEWHERE): frozenset({DELIVER_CARD, DELIVER_SOUND, DELIVER_ROW}),
    (KIND_UPDATE, FOCUS_UNFOCUSED): frozenset({DELIVER_DESKTOP, DELIVER_ROW}),
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

    An `update` (a newer Collins, see updatecheck) has no session, so
    nothing is flagged or flashed and there is no tab to be looking at:
    a card and the sound while any window is active, the desktop
    notification while none is, and a row either way.

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


def focus_state(any_window_active: bool, tab_window_active: bool, tab_selected: bool) -> str:
    """Where the user is, relative to a tab: one of the FOCUS_* values the
    delivery table is indexed by.

    `selected` needs both halves — the tab's own window is the active one
    *and* the tab is its selected page. A selected tab in a window that
    isn't active (a second window, a hidden one) is `elsewhere` while some
    other Collins window is active, because a card in that window can
    reach it, and `unfocused` when none is: nothing on screen is Collins,
    so the desktop carries the notification. "Active" is
    Gtk.Window.is_active, which a hidden window never is.
    """
    if not any_window_active:
        return FOCUS_UNFOCUSED
    if tab_window_active and tab_selected:
        return FOCUS_SELECTED
    return FOCUS_ELSEWHERE


def without_cards(deliveries: Iterable[str]) -> frozenset[str]:
    """The same delivery with the in-app card turned off (the
    inapp_notifications switch): the card and the sound that only ever
    plays beside it become a desktop notification — the desktop is where
    every notification went before there were cards, and it sounds its own.
    Everything else (the row, the flag, the flash) stays."""
    deliveries = frozenset(deliveries)
    if DELIVER_CARD not in deliveries:
        return deliveries
    return (deliveries - {DELIVER_CARD, DELIVER_SOUND}) | {DELIVER_DESKTOP}


def tool_reply(deliveries: Iterable[str]) -> str:
    """What `notify_user` is told: where the message went. A card is "in
    Collins", a desktop notification is "on their desktop", and neither —
    the user is looking at the very tab — says so and where the message
    can still be found. Nothing silently did nothing."""
    deliveries = frozenset(deliveries)
    if DELIVER_CARD in deliveries:
        return REPLY_IN_APP
    if DELIVER_DESKTOP in deliveries:
        return REPLY_DESKTOP
    return REPLY_SELECTED


def green_id(session_id: str) -> str:
    """The id of the synthetic row standing for *session_id*'s green flag."""
    return GREEN_PREFIX + session_id


def is_green_id(notification_id: str) -> bool:
    return notification_id.startswith(GREEN_PREFIX)


# An update row's id is its version, prefixed, the way a synthetic row's is
# its session's: the check can tell whether the row it is about to post is
# already there, and the launch-time retire can read the version back off
# a row that has no other place for it (see updatecheck).
UPDATE_PREFIX = "update:"


def update_id(version: str) -> str:
    """The id of the row announcing Collins *version*."""
    return UPDATE_PREFIX + version


def update_version(notification_id: str) -> str:
    """The version an update row's id names, "" for any other id."""
    if notification_id.startswith(UPDATE_PREFIX):
        return notification_id[len(UPDATE_PREFIX) :]
    return ""


@dataclass
class Notification:
    """One thing Collins told the user about.

    `session_id` is what a click on the row jumps to. For a message or a
    bell it is the session's id, or "" for a tab whose id hadn't resolved
    when it spoke (the card then routes to the tab's window — see the spec).
    For a synthetic row it is whatever key the green was set under: the
    session id, or, for a tab still waiting on its id, the placeholder id
    the sidebar knows the row by (`placeholder-N`, or a new-chat draft id),
    which the owning window can select a tab by. An update row has no
    session at all: its `session_id` is "" and a click opens its `url`
    (the release's page on GitHub) instead.
    """

    id: str  # uuid4; synthetic rows use "green:" + session_id, update rows "update:" + version
    session_id: str
    title: str  # the session title at raise time
    project: str  # project name, for the row's footer / eyebrow
    kind: str  # KIND_MESSAGE | KIND_BELL | KIND_FINISHED | KIND_UPDATE
    body: str  # the message; bell_body() / "Finished a run"
    when: float  # time.time()
    read: bool = False
    count: int = 1  # bells coalesce: "Rang the bell ×3"
    url: str = ""  # where an update row's click goes; "" for every other kind

    def to_record(self) -> dict:
        """The row as state.json holds it. The url rides only on a row that
        has one, so every other kind's record is what it always was."""
        record = {
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
        if self.url:
            record["url"] = self.url
        return record

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
            url=text("url"),
        )


def clean_notifications(raw, now: float | None = None) -> list[Notification]:
    """The persisted list, made safe to load: garbage dropped, synthetic rows
    dropped (they never belong on disk, whatever wrote them), rows older than
    KEEP_DAYS pruned, newest first, at most ROW_CAP of them.

    Two rows under one id — only a hand-edited or half-written file has them
    — keep the newer one, whichever came first in the file: the file's order
    is a claim, the timestamp is the row's own.

    Idempotent, and used on both sides of the file — AppState runs it (as
    clean_records) on load, and the center runs it again on whatever it is
    handed — so neither has to trust the other to have done it.
    """
    if now is None:
        now = time.time()
    if not isinstance(raw, list):
        return []
    candidates: list[Notification] = []
    for entry in raw:
        row = Notification.from_record(entry)
        if row is None or row.kind == KIND_FINISHED or is_green_id(row.id):
            continue
        if row.when < now - KEEP_SECONDS:
            continue
        candidates.append(row)
    candidates.sort(key=lambda row: -row.when)  # stable: ties keep file order
    rows: list[Notification] = []
    seen: set[str] = set()
    for row in candidates:
        if row.id in seen:
            continue
        seen.add(row.id)
        rows.append(row)
    return rows[:ROW_CAP]


def clean_records(raw, now: float | None = None) -> list[dict]:
    """clean_notifications, as the records AppState holds and writes back."""
    return [row.to_record() for row in clean_notifications(raw, now=now)]


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
        self._rows.extend(clean_notifications(list(records or []), now=clock()))

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

    def unread_sessions(self) -> frozenset[str]:
        """The keys with an unread row of any kind standing — what a desktop
        notification is still speaking for (see App._on_notifications_changed,
        which withdraws one the moment its key drops out of this set). A ""
        key is nobody's and never listed."""
        return frozenset(row.session_id for row in self._rows if not row.read and row.session_id)

    def has_unread(self, session_id: str) -> bool:
        """Whether *session_id* has any unread row standing."""
        return bool(session_id) and any(
            row.session_id == session_id and not row.read for row in self._rows
        )

    def has_unread_kind(self, kind: str) -> bool:
        """Whether any unread row of *kind* is standing — what the update's
        desktop notification, which no session key speaks for, is withdrawn
        on (see App._on_notifications_changed)."""
        return any(row.kind == kind and not row.read for row in self._rows)

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

        An update row replaces every update row standing, read or not: the
        newest release is the only one worth a row, and a row for a version
        that is no longer the latest would be a click to the wrong page.

        Synthetic rows are set_green's to add, never post's.
        """
        if notification.kind not in KINDS:
            raise ValueError(f"unknown notification kind: {notification.kind!r}")
        if notification.kind == KIND_FINISHED or is_green_id(notification.id):
            raise ValueError("finished rows are set_green's to add")
        if notification.kind == KIND_UPDATE:
            self._rows = [row for row in self._rows if row.kind != KIND_UPDATE]
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

    def rekey_session(self, old: str, new: str) -> int:
        """Move every message and bell row filed under *old* to *new*: the
        placeholder → real-row handoff, for the rows a tab posted before the
        store discovered its session (MainWindow._apply_resolved_sessions).
        Without it the rows keep a key nothing answers to any more — the
        sheet's click would go nowhere and visiting the tab would read
        nothing. Synthetic rows are left alone: the handoff takes the
        placeholder's down and raises the session's through set_green, whose
        ids the keys are part of. Returns how many rows moved; a rekey to
        the same key, or from or to "", moves none."""
        if not old or not new or old == new:
            return 0
        moved = 0
        for row in self._rows:
            if row.session_id == old and row.kind != KIND_FINISHED:
                row.session_id = new
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


# -- what the bell and the sheet say -------------------------------------------


def bell_body() -> str:
    """A bell row's body. Translated when the row is posted and persisted as
    such — a bell rung under one UI language is shown in it after a switch,
    the way the row's title (the session's name at the time) is a record of
    the moment rather than a live lookup."""
    return _("Rang the bell")


def bell_tooltip(unread: int) -> str:
    """The header bell's tooltip: what it is when quiet, how many are waiting
    otherwise. Two plain strings rather than a plural form — po/generate.py
    has no plurals (see po-generate-has-no-plurals)."""
    if unread <= 0:
        return _("Notifications")
    if unread == 1:
        return _("1 unread notification")
    return _("{n} unread notifications").format(n=unread)


def relative_time(when: float, now: float | None = None) -> str:
    """How long ago a row happened, as the row's corner says it: "just now",
    "12s ago", "6m ago", "2h ago", "yesterday", "3d ago", then the date. A
    clock that reads earlier than the row (a machine whose time went back)
    is "just now" rather than a negative age."""
    if now is None:
        now = time.time()
    seconds = int(now - when)
    if seconds < 10:
        return _("just now")
    if seconds < 60:
        return _("{n}s ago").format(n=seconds)
    if seconds < 3600:
        return _("{n}m ago").format(n=seconds // 60)
    if seconds < 86400:
        return _("{n}h ago").format(n=seconds // 3600)
    if seconds < 2 * 86400:
        return _("yesterday")
    if seconds < 7 * 86400:
        return _("{n}d ago").format(n=seconds // 86400)
    return time.strftime("%Y-%m-%d", time.localtime(when))


def row_body(notification: Notification) -> str:
    """The row's body line: the text, with a coalesced bell's count after it
    ("Rang the bell ×3"). A count of one says nothing about itself."""
    if notification.count > 1:
        return _("{body} ×{n}").format(body=notification.body, n=notification.count)
    return notification.body


def split_rows(rows: Iterable[Notification]) -> tuple[list[Notification], list[Notification]]:
    """The sheet's two sections, each in the order given (newest first):
    the unread rows, then everything already read."""
    unread: list[Notification] = []
    earlier: list[Notification] = []
    for row in rows:
        (earlier if row.read else unread).append(row)
    return unread, earlier


def sound_display_name(value: str | None) -> str:
    """What the sheet's footer calls the notification_sound setting: "Default"
    for an unset or default value, "None" for silence, else the chosen file's
    name. The sound's own module reads the same setting for playback; the
    two must agree on the words, so the words live here."""
    if not value or value == SOUND_DEFAULT:
        return _("Default")
    if value == SOUND_NONE:
        return _("None")
    return os.path.basename(str(value))


def card_scheme_class(value) -> str:
    """The CSS class the setting's *value* puts on a card's body — "" for
    a card that follows the app, which is what any value the setting does
    not take (an old file, a hand edit) comes to as well."""
    return CARD_SCHEME_CLASSES.get(str(value or ""), "")


def sound_subtitle(value, home: str | None = None) -> str:
    """The preferences row's line under "Sound": what the choice means.
    "Default" is described rather than named (the file it resolves to is
    the desktop's business, and differs per theme), silence says so, and a
    file of the user's shows its path with the home directory as ~."""
    if not value or value == SOUND_DEFAULT:
        return _("Default: the desktop's message sound")
    if value == SOUND_NONE:
        return _("Silent")
    path = str(value)
    home = home if home is not None else os.path.expanduser("~")
    if home and path.startswith(home.rstrip("/") + "/"):
        path = "~" + path[len(home.rstrip("/")):]
    return path


def sound_is_silent(value) -> bool:
    """Whether the setting asks for no sound at all — not even the beep."""
    return value == SOUND_NONE


def sound_candidates(theme_name: str = "") -> list[str]:
    """Where "default" looks, in order: the desktop's theme (when it names
    one that isn't the fallback itself), then the freedesktop theme."""
    themes: list[str] = []
    theme_name = (theme_name or "").strip()
    if theme_name and theme_name != SOUND_FALLBACK_THEME and "/" not in theme_name:
        themes.append(theme_name)
    themes.append(SOUND_FALLBACK_THEME)
    return [os.path.join(SOUND_THEME_ROOT, theme, SOUND_EVENT) for theme in themes]


def sound_file(value, theme_name: str = "", exists: Callable[[str], bool] = os.path.isfile) -> str:
    """The file to play for the notification_sound setting, resolved now
    rather than when it was chosen: "default" is the first of the theme
    candidates that exists on this machine today, a path is itself if it
    still exists, and "" means there is nothing to play and the beep is the
    fallback. Silence (SOUND_NONE) is sound_is_silent's to notice first;
    here it resolves like an absent file.

    `exists` is os.path.isfile unless a test says otherwise."""
    if not value or value == SOUND_DEFAULT:
        for candidate in sound_candidates(theme_name):
            if exists(candidate):
                return candidate
        return ""
    if value == SOUND_NONE:
        return ""
    path = str(value)
    return path if os.path.isabs(path) and exists(path) else ""
