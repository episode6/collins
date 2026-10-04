# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""`RemoteNotifications`: the client's mirror of the service's history.

Split-service spec §3.13: the service owns the notification history and
the unread set (`service.notifications.ServiceNotifications`); the client
holds this, a `notifycenter.NotificationCenter` in every way its users can
tell (the bell, the sheet, the cards, the badge, the status icon, the app's
withdraws, `updatecheck`), over a copy kept by the service's `notify`
events: the subscribe snapshot, then every row that appeared, changed or
left. A row's `body` is its msgid and args translated here (§3.14,
`i18n.translate`): the service never knows the language of whoever is
looking.

**Writes are requests.** `post` (`notify.post`: the service mints the id,
the time, coalesces a bell, replaces an update; the row handed back is the
mirror's own, filled by the event that came first), `remove`, `clear`,
`set_green` (`notify.green`), `rekey_session` (`notify.rekey`), and reads
(`mark_read`, `mark_session_read`, `mark_all_read`: `seen`). The reads
are applied here first and then told (a badge drops at the click, on any
link); the service's echo carries the same flags. Listeners hear one
`changed` per write, however many rows its events moved, as they heard one
from the center. With no link (a refused request), nothing changes.

GTK-free.
"""

from __future__ import annotations

import logging
import time

from .api.loopback import RequestRefused
from .i18n import translate
from .notifycenter import (
    KIND_FINISHED,
    KIND_UPDATE,
    Notification,
    NotificationCenter,
    clean_args,
    green_id,
)

log = logging.getLogger(__name__)

_FIELDS = ("session_id", "title", "project", "kind", "body", "when", "read", "count", "url", "msgid", "args")


class RemoteNotifications(NotificationCenter):
    """See the module docstring. *link* is an `apilink.Link`."""

    def __init__(self, link, *, clock=time.time) -> None:
        super().__init__(records=None, clock=clock)
        self._link = link
        self._holding = 0  # writes in flight: their events change, one `changed` at the end
        self._moved = False
        link.on("notify", self._on_notify)
        link.on("seen", self._on_seen)

    # -- events ---------------------------------------------------------------------

    def _on_notify(self, event: dict) -> None:
        row_id = event.get("notification", "")
        existing = self.get(row_id)
        if event.get("removed"):
            if existing is not None:
                self._rows.remove(existing)
                self._moved_now()
            return
        msgid = event.get("msgid") or ""
        args = clean_args(event.get("args"))
        row = Notification(
            id=row_id,
            session_id=event.get("session") or "",
            title=event.get("title") or "",
            project=event.get("project") or "",
            kind=event.get("kind") or "",
            body=translate(msgid, args),
            when=float(event.get("when") or 0.0),
            read=bool(event.get("read")),
            count=int(event.get("count") or 1),
            url=event.get("url") or "",
            msgid=msgid,
            args=args,
        )
        if existing is None:
            self._rows.insert(0, row)
        elif existing.when != row.when:
            # A bell coalesced into it: back to the top, as the center does.
            self._rows.remove(existing)
            self._copy(row, existing)
            self._rows.insert(0, existing)
        else:
            self._copy(row, existing)
        self._moved_now()

    @staticmethod
    def _copy(source: Notification, target: Notification) -> None:
        """Into the row object the widgets hold (a card keeps its row)."""
        for name in _FIELDS:
            setattr(target, name, getattr(source, name))

    def _on_seen(self, event: dict) -> None:
        """Another client read rows: the `notify` events that follow say so
        row by row; this applies it at once."""
        self._hold()
        try:
            if event.get("all"):
                self._read_where(lambda row: True)
            ids = set(event.get("ids") or ())
            session = event.get("session")
            self._read_where(lambda row: row.id in ids or (bool(session) and row.session_id == session))
        finally:
            self._release()

    def _read_where(self, matches) -> int:
        moved = 0
        for row in self._rows:
            if not row.read and matches(row):
                row.read = True
                moved += 1
        if moved:
            self._moved_now()
        return moved

    def _moved_now(self) -> None:
        if self._holding:
            self._moved = True
        else:
            self._changed()

    def _hold(self) -> None:
        self._holding += 1

    def _release(self) -> None:
        self._holding -= 1
        if not self._holding and self._moved:
            self._moved = False
            self._changed()

    def _request(self, message: dict) -> dict | None:
        self._hold()
        try:
            return self._link.call(message)
        except RequestRefused as refusal:
            log.warning("notifications: %s refused: %s", message.get("t"), refusal.msgid)
            return None
        finally:
            self._release()

    # -- writes (the center's API) --------------------------------------------------

    def post(self, notification: Notification) -> Notification:
        if notification.kind == KIND_FINISHED:
            raise ValueError("finished rows are set_green's to add")
        message = {
            "t": "notify.post",
            "kind": notification.kind,
            "session": notification.session_id,
            "title": notification.title,
            "project": notification.project,
            "msgid": notification.text_id or " ",
            "read": bool(notification.read),
        }
        if notification.args:
            message["args"] = dict(notification.args)
        if notification.url:
            message["url"] = notification.url
        if notification.kind == KIND_UPDATE and notification.id.startswith("update:"):
            message["key"] = notification.id
        reply = self._request(message)
        if reply is None:
            return notification
        return self.get(reply.get("notification", "")) or notification

    def mark_read(self, notification_id: str) -> bool:
        row = self.get(notification_id)
        if row is None or row.read:
            return False
        self._hold()
        try:
            row.read = True
            self._moved_now()
            self._send_seen({"ids": [notification_id]})
        finally:
            self._release()
        return True

    def mark_session_read(self, session_id: str) -> int:
        if not session_id:
            return 0
        self._hold()
        try:
            moved = self._read_where(lambda row: row.session_id == session_id)
            if moved:
                self._send_seen({"session": session_id})
        finally:
            self._release()
        return moved

    def mark_all_read(self) -> int:
        self._hold()
        try:
            moved = self._read_where(lambda row: True)
            if moved:
                self._send_seen({"all": True})
        finally:
            self._release()
        return moved

    def _send_seen(self, fields: dict) -> None:
        self._request({"t": "seen", **fields})

    def rekey_session(self, old: str, new: str) -> int:
        if not old or not new or old == new:
            return 0
        reply = self._request({"t": "notify.rekey", "session": old, "to": new})
        return int(reply.get("moved", 0)) if reply else 0

    def clear(self) -> int:
        reply = self._request({"t": "notify.clear"})
        return int(reply.get("removed", 0)) if reply else 0

    def remove(self, notification_id: str) -> bool:
        row = self.get(notification_id)
        if row is None or row.kind == KIND_FINISHED:
            return False
        reply = self._request({"t": "notify.remove", "ids": [notification_id]})
        return bool(reply and reply.get("removed"))

    def set_green(self, session_id: str, on: bool, *, title: str = "", project: str = "") -> bool:
        if not session_id:
            return False
        # A no-op asks nothing (callers re-assert on every edge they see).
        if (self.get(green_id(session_id)) is not None) == bool(on):
            return False
        message = {"t": "notify.green", "session": session_id, "on": bool(on)}
        if on:
            message["title"] = title
            message["project"] = project
        reply = self._request(message)
        return bool(reply and reply.get("changed"))
