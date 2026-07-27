# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-27. Full change history: git log for this file.

"""GObject view-models. UI widgets bind to SessionItem properties, so
renames, favorites and status changes propagate without list rebuilds."""

from __future__ import annotations

from gi.repository import GObject

from .providers import get_provider
from .sessions import Session

FAV_GROUP = ("fav", "")


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
    # Conversation moved to a fork the store hasn't discovered yet (row is
    # kept visible but disabled until the fork's row can take its place).
    syncing = GObject.Property(type=bool, default=False)

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
