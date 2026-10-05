# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-10-05. Full change history: git log for this file.

"""GObject view-models. UI widgets bind to SessionItem properties, so
renames, favorites and status changes propagate without list rebuilds."""

from __future__ import annotations

from gi.repository import GObject

from .providers import get_provider
from .sessions import Session

FAV_GROUP = ("fav", "")
# The virtual "Chats" project: sessions living in throwaway directories
# under chats.CHATS_DIR, pinned right after Favorites.
CHATS_GROUP = ("chats", "")


class SessionItem(GObject.Object):
    """Bindable wrapper around a discovered Session."""

    __gtype_name__ = "CsmSessionItem"

    display_name = GObject.Property(type=str, default="")
    subtitle = GObject.Property(type=str, default="")  # relative "time ago" of last activity
    preview = GObject.Property(type=str, default="")
    favorite = GObject.Property(type=bool, default=False)
    # "" | "open" | "attention" (tab state) | "background" (running detached)
    status = GObject.Property(type=str, default="")
    state = GObject.Property(type=str, default="")  # "", "waiting", "interrupted" (transcript)
    # Whether the agent is producing output right now (see activity.py). Rides
    # on top of `status`, which says *where* a session runs but never whether
    # anything is happening: the guide line only moves while this is on.
    busy = GObject.Property(type=bool, default=False)
    # A run finished and nobody has looked yet: set when the agent's output
    # runs out on its own (never when a tab closes or a detach is torn down),
    # cleared by returning to the session's tab — or, for the tab already on
    # screen, by the next keystroke into its terminal. Sets the row's guide
    # line pulsing green (see row.session-child.unread in app.py).
    unread = GObject.Property(type=bool, default=False)
    # Conversation moved to a fork the store hasn't discovered yet (row is
    # kept visible but disabled until the fork's row can take its place).
    syncing = GObject.Property(type=bool, default=False)
    # A /bg detach fed but not yet confirmed: the guide line shows yellow
    # pre-emptively and the row is disabled until the agent CLI lists the
    # session as a background agent (or confirmation times out).
    backgrounding = GObject.Property(type=bool, default=False)
    # Whether this row's background button can be pressed right now: it needs
    # a running session whose id is registered, and no other handoff still
    # waiting for its new id (see bgblock.background_blocker). The service's
    # word (service.bgagents), as `backgrounding` is.
    can_background = GObject.Property(type=bool, default=False)
    # Whether the row's conversation runs as a background agent (/bg):
    # "running" when the agent CLI lists it (under any id of its forward
    # chain), "pending" while a /bg fed for it waits for the list to say so,
    # "" otherwise. The service's word (service.bgagents, split-service spec
    # §3.22); a client reads it for the yellow line of a row with no tab
    # (its `status` "background").
    background = GObject.Property(type=str, default="")
    # An agent pty on the Collins service runs this session right now (the
    # service's word, an `item` field and the pty table's rows; split-service
    # spec §3.21): with no tab on this device the row is a running row —
    # the yellow line, the pole while busy — and opening it attaches to the
    # pty instead of resuming the session in a second one.
    running = GObject.Property(type=bool, default=False)

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session
        self.group_key: tuple = FAV_GROUP
        self.group_label: str = ""

    @property
    def session_id(self) -> str:
        return self.session.session_id

    @property
    def provider_icon(self) -> str:
        return get_provider(self.session.provider).icon_name

    @property
    def provider_label(self) -> str:
        return get_provider(self.session.provider).name

    @property
    def search_text(self) -> str:
        return " ".join(
            (self.display_name, self.session.project_name, self.session.preview, self.session_id)
        ).lower()
