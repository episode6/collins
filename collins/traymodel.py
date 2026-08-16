# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""What the status icon shows, worked out without a toolkit.

The item itself is D-Bus plumbing (statusicon.py); everything it *decides* is
here, so it can be unit-tested in CI, which has no GTK typelibs at all (see
tests/conftest.py). Given the session tabs open right now — across every
window — this returns the four things the StatusNotifierItem exports:

- **`Status`**: `Passive` with nothing open (a host may hide the item),
  `Active` with tabs open, `NeedsAttention` once something is unread.
- **The badge text** composited onto the icon: `""`, `"1"`…`"9"`, `"9+"`.
- **The tooltip**, the same state in words.
- **The menu layout** as plain data — labels, markers, actions and the
  separators that survive — which the DBusMenu export walks.

**The badge counts unread sessions only** — runs that finished and nobody has
looked at yet, the sidebar's green pulse. Never unread + working: a number
that climbs because an agent *started* tells the user something they cannot
act on and that will resolve itself. The badge means "n things are waiting for
you", so a badge reading `0` while three agents work is correct, and the
tooltip carries both numbers for the curious.

Only sessions with an open tab are passed in, which is not a narrowing: an
unread flag never outlives the tab it spoke for (MainWindow._sync_status
takes it off a row whose tab goes away), so every unread session has a tab,
and every menu row leads somewhere — `app.focus-session` can only raise a tab
that exists. Tabs whose session id hasn't resolved yet have no id to jump to,
so they arrive as bare counts (`placeholders`) instead: they hold the item
`Active` and can carry unread, but get no row.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .i18n import _

# org.kde.StatusNotifierItem.Status values.
STATUS_PASSIVE = "Passive"
STATUS_ACTIVE = "Active"
STATUS_ATTENTION = "NeedsAttention"

# What a menu row does when clicked. Session rows carry the session id as
# their target; the rest take none. "show" is the item's own present-the-
# window behavior (what Activate does), not an app action.
ACTION_SHOW = "show"
ACTION_FOCUS = "focus-session"
ACTION_NEW_WINDOW = "new-window"
ACTION_QUIT = "quit"

# What a session row has to say about itself, if anything.
MARKER_WORKING = "working"
MARKER_UNREAD = "unread"

# A jump list, not a second sidebar: the most recently active handful, and
# the window for anything else.
SESSION_ROW_CAP = 8
# Past this the badge would need a second digit at 22px. "9+" is enough.
BADGE_MAX = 9
# Session titles run to a whole first message; a shell menu sized to one is
# unusable.
LABEL_MAX = 60

APP_NAME = "Collins"


@dataclass(frozen=True)
class TraySession:
    """One open session tab, as much of it as the icon cares about."""

    session_id: str
    project: str = ""
    title: str = ""
    busy: bool = False
    unread: bool = False
    last_active: float = 0.0


@dataclass(frozen=True)
class MenuEntry:
    """One row of the DBusMenu layout. `id` is its item id (1-based; 0 is the
    root the export owns). A separator carries nothing else."""

    id: int = 0
    label: str = ""
    action: str = ""
    target: str = ""
    marker: str = ""
    separator: bool = False


@dataclass(frozen=True)
class TrayView:
    """Everything the item exports, for one moment of app state."""

    status: str = STATUS_PASSIVE
    badge: str = ""
    tooltip: str = ""
    menu: list[MenuEntry] = field(default_factory=list)
    sessions: int = 0
    working: int = 0
    unread: int = 0


def badge_text(unread: int) -> str:
    """The number drawn on the icon. Empty means "draw no badge"."""
    if unread <= 0:
        return ""
    if unread > BADGE_MAX:
        return f"{BADGE_MAX}+"
    return str(unread)


def status_for(sessions: int, unread: int) -> str:
    """The Status value for a session count and an unread count.

    Unread wins outright, including over a session count of zero it should
    never be seen with: a host that has hidden a Passive item defers every
    property change until the item goes Active again, so a badge landing on a
    Passive item would be invisible until something else moved.
    """
    if unread > 0:
        return STATUS_ATTENTION
    return STATUS_ACTIVE if sessions > 0 else STATUS_PASSIVE


def _count_phrase(n: int, one: str, many: str) -> str:
    """One tooltip clause. Both forms are separate msgids rather than an
    ngettext pair: the translations are a flat msgid→msgstr dict, so a plural
    entry would go untranslated in every language (see po/generate.py)."""
    return _(one) if n == 1 else _(many).format(n=n)


def tooltip_for(sessions: int, working: int, unread: int, name: str = APP_NAME) -> str:
    """The item's tooltip: the app's name, then whatever is worth saying.

    Working and unread clauses appear only when they are non-zero, so an idle
    app reads "Collins — 2 sessions" rather than trailing two zeroes.
    """
    if sessions <= 0:
        return f"{name} — {_('no sessions open')}"
    clauses = [_count_phrase(sessions, "1 session", "{n} sessions")]
    if working > 0:
        clauses.append(_count_phrase(working, "1 working", "{n} working"))
    if unread > 0:
        clauses.append(_count_phrase(unread, "1 unread", "{n} unread"))
    return f"{name} — {', '.join(clauses)}"


def session_label(session: TraySession) -> str:
    """A session row's text: "project — title", truncated to fit a menu."""
    parts = [part for part in (session.project.strip(), session.title.strip()) if part]
    label = " — ".join(parts) or session.session_id
    if len(label) > LABEL_MAX:
        label = label[: LABEL_MAX - 1].rstrip() + "…"
    return label


def session_marker(session: TraySession) -> str:
    """Working outranks unread on a row that is somehow both: the run the flag
    was raised for has been overtaken by another that is still going."""
    if session.busy:
        return MARKER_WORKING
    return MARKER_UNREAD if session.unread else ""


def session_rows(sessions: list[TraySession]) -> list[TraySession]:
    """The sessions that get a row: most recently active first, capped.

    Ties break on the session id so the order is stable — several tabs opened
    in the same second must not shuffle between two rebuilds of the menu.
    """
    ordered = sorted(sessions, key=lambda s: (-s.last_active, s.session_id))
    return ordered[:SESSION_ROW_CAP]


def _numbered(entries: list[MenuEntry]) -> list[MenuEntry]:
    """Drop the separators with nothing above them, then hand out item ids.
    Separators are written unconditionally around the session rows and
    collapse here, so an empty jump list leaves one divider, not two."""
    kept: list[MenuEntry] = []
    for entry in entries:
        if entry.separator and (not kept or kept[-1].separator):
            continue
        kept.append(entry)
    return [
        MenuEntry(
            id=index,
            label=entry.label,
            action=entry.action,
            target=entry.target,
            marker=entry.marker,
            separator=entry.separator,
        )
        for index, entry in enumerate(kept, start=1)
    ]


def menu_entries(sessions: list[TraySession]) -> list[MenuEntry]:
    """The whole menu, top to bottom.

    Kept flat on purpose: a submenu in a tray menu is a usability tax, and
    shell menus cap their height the way the app's own popovers do.
    """
    entries = [
        MenuEntry(label=_("Show Collins"), action=ACTION_SHOW),
        MenuEntry(separator=True),
    ]
    for session in session_rows(sessions):
        entries.append(
            MenuEntry(
                label=session_label(session),
                action=ACTION_FOCUS,
                target=session.session_id,
                marker=session_marker(session),
            )
        )
    entries.extend(
        [
            MenuEntry(separator=True),
            MenuEntry(label=_("New window"), action=ACTION_NEW_WINDOW),
            MenuEntry(label=_("Quit"), action=ACTION_QUIT),
        ]
    )
    return _numbered(entries)


def tray_view(
    sessions: list[TraySession],
    placeholders: int = 0,
    placeholder_unread: int = 0,
    name: str = APP_NAME,
) -> TrayView:
    """Everything the item exports, from the open session tabs.

    `placeholders` is how many open tabs have no session id yet (their unread
    flags live in their window's sidebar, hence `placeholder_unread`): they
    count towards the totals but cannot be jumped to, so they get no row.

    The unread half is clamped to the tabs it counts rather than trusted. The
    aggregate reads the two numbers from each window in turn, and a
    placeholder that resolves between the reads leaves the pair briefly
    disagreeing — a badge counting more sessions than the tooltip beside it
    admits to having is worse than a badge one short for one repaint.
    """
    placeholders = max(placeholders, 0)
    placeholder_unread = min(max(placeholder_unread, 0), placeholders)
    open_count = len(sessions) + placeholders
    working = sum(1 for session in sessions if session.busy)
    unread = sum(1 for session in sessions if session.unread) + placeholder_unread
    return TrayView(
        status=status_for(open_count, unread),
        badge=badge_text(unread),
        tooltip=tooltip_for(open_count, working, unread, name=name),
        menu=menu_entries(sessions),
        sessions=open_count,
        working=working,
        unread=unread,
    )
