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
    status = GObject.Property(type=str, default="")  # "", "open", "attention" (tab state)
    state = GObject.Property(type=str, default="")  # "", "waiting", "interrupted" (transcript)

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
