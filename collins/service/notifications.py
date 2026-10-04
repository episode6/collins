# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""The notification history, kept by the service (split-service spec §3.13).

The service produces the notification record (its kind, its session, its
text as a msgid and args, §3.14) and owns the history and the unread set;
each client applies `notifycenter`'s delivery table with its own focus
state and does the card, the sound, the desktop notification, the flash
and the tray, and reports what was seen, so unread is one number
everywhere. `ServiceNotifications` is the service's end of that: a
`notifycenter.NotificationCenter` over state.json's records (the center's
whole behaviour, coalescing and capping included, unchanged), persisted
after every change of what is persisted, and published to every
subscriber as `notify` events: the snapshot at `subscribe` (every row,
oldest first, so a mirror that puts each new row at the top ends up in the
center's order), then each row that changed or appeared, and each that
left (``removed``). A `seen` request reads rows (by id, by session, or
all) and is told to every other subscriber as a `seen` event.

What a client asks for (Phase 1: the app's window and bell): a row
(`notify.post`, minted here: its id, its time; a bell coalesced into the
unread bell of its session; an update replacing every other), a row's
removal, the history cleared, a finished run's synthetic row raised or
taken down (`notify.green`: the window decides green in Phase 1, where the
activity tracker runs, as `store.flags` carries its verdicts), a
placeholder's rows filed under the session it resolved to
(`notify.rekey`). A producer on this side (`post`) is the `notify_user`
tool with no client attached. The command sink for when no client is
attached (`notify_command`) is PR-3.4's.

GLib only; nothing here imports GTK.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from .. import notifycenter
from ..api import protocol
from ..i18n import english
from ..notifycenter import Notification, NotificationCenter

log = logging.getLogger(__name__)


def event_for(row: Notification) -> dict:
    """A row as the `notify` event carries it, bounded."""
    event = {
        "t": "notify",
        "notification": row.id,
        "kind": row.kind[: protocol.SHORT_MAX],
        "session": row.session_id,
        "title": row.title[: protocol.NAME_MAX],
        "project": row.project[: protocol.NAME_MAX],
        "msgid": (row.text_id or " ")[: protocol.MSGID_MAX],
        "when": float(row.when),
        "read": bool(row.read),
        "count": max(1, int(row.count)),
    }
    if row.args:
        event["args"] = dict(list(row.args.items())[: protocol.ARGS_MAX])
    if row.url:
        event["url"] = row.url
    return event


def _key(event: dict) -> tuple:
    return tuple(sorted((k, repr(v)) for k, v in event.items()))


class ServiceNotifications:
    """See the module docstring. *state* is the service's AppState."""

    def __init__(self, state) -> None:
        self.state = state
        self.center = NotificationCenter(records=state.get_notifications())
        self._subscribers: list[tuple[object, Callable[[dict], None]]] = []
        self._published: dict[str, dict] = {row.id: event_for(row) for row in self.center.rows()}
        self.center.connect(self._on_changed)

    # -- subscribers ---------------------------------------------------------------

    def subscribe(self, client, deliver: Callable[[dict], None]) -> int:
        """The snapshot: every row, oldest first. Returns how many."""
        self._subscribers = [(c, d) for c, d in self._subscribers if c is not client]
        self._subscribers.append((client, deliver))
        rows = self.center.rows()
        for row in reversed(rows):
            self._send(deliver, event_for(row))
        return len(rows)

    def unsubscribe(self, client) -> None:
        self._subscribers = [(c, d) for c, d in self._subscribers if c is not client]

    def _send(self, deliver: Callable[[dict], None], event: dict) -> None:
        try:
            deliver(event)
        except Exception:
            log.exception("notifications: a subscriber's delivery failed")

    def _broadcast(self, event: dict, skip=None) -> None:
        for client, deliver in list(self._subscribers):
            if client is not skip:
                self._send(deliver, event)

    def _on_changed(self) -> None:
        """Persist what is persisted, then tell every subscriber what moved:
        rows that appeared or changed (oldest first, so each lands on top in
        order), then rows that left."""
        self.state.set_notifications(self.center.to_records())
        rows = self.center.rows()
        current = {row.id: event_for(row) for row in rows}
        for row in reversed(rows):
            event = current[row.id]
            old = self._published.get(row.id)
            if old is None or _key(old) != _key(event):
                self._broadcast(event)
        for row_id, old in list(self._published.items()):
            if row_id not in current:
                gone = dict(old)
                gone["removed"] = True
                self._broadcast(gone)
        self._published = current

    # -- what the service produces ---------------------------------------------

    def post(
        self,
        kind: str,
        session_id: str,
        title: str,
        project: str,
        msgid: str,
        args: dict | None,
        *,
        read: bool = False,
        url: str = "",
        key: str = "",
    ) -> Notification:
        """Mint a row and post it (the center coalesces a bell, replaces an
        update). Its body here is the English source (clients translate)."""
        row = self.center.make(kind, session_id, title, project, english(msgid, args), msgid=msgid, args=args)
        row.read = bool(read)
        row.url = url or ""
        if key:
            row.id = key
        return self.center.post(row)

    # -- requests -----------------------------------------------------------------

    def handle(self, message: protocol.Message, client) -> dict:
        kind = message.type
        if kind == "notify.post":
            post_kind = message.get("kind")
            key = message.get("key") or ""
            fixed = post_kind == notifycenter.KIND_UPDATE and key.startswith(notifycenter.UPDATE_PREFIX)
            if key and not fixed:
                return protocol.refuse(
                    message.id, protocol.ERROR_REFUSED, "Only an update row has a fixed id"
                )
            row = self.post(
                post_kind,
                message.get("session") or "",
                message.get("title") or "",
                message.get("project") or "",
                message.get("msgid"),
                message.get("args"),
                read=bool(message.get("read")),
                url=message.get("url") or "",
                key=key,
            )
            return protocol.reply(message.id, notification=row.id)
        if kind == "notify.remove":
            removed = sum(1 for row_id in message.get("ids") if self.center.remove(row_id))
            return protocol.reply(message.id, removed=removed)
        if kind == "notify.clear":
            return protocol.reply(message.id, removed=self.center.clear())
        if kind == "notify.green":
            changed = self.center.set_green(
                message.get("session"),
                bool(message.get("on")),
                title=message.get("title") or "",
                project=message.get("project") or "",
            )
            return protocol.reply(message.id, changed=bool(changed))
        if kind == "notify.rekey":
            return protocol.reply(
                message.id, moved=self.center.rekey_session(message.get("session"), message.get("to"))
            )
        if kind == "seen":
            return self._seen(message, client)
        return protocol.refuse(message.id, protocol.ERROR_UNKNOWN, "{type}: not served here", {"type": kind})

    def _seen(self, message: protocol.Message, client) -> dict:
        """Rows read: by id, by session, or all; the other subscribers hear
        it as a `seen` event (each row's `notify` says so too)."""
        if message.get("all"):
            self.center.mark_all_read()
        for row_id in message.get("ids") or []:
            self.center.mark_read(row_id)
        if message.get("session"):
            self.center.mark_session_read(message.get("session"))
        event = {"t": "seen"}
        for name in ("ids", "session", "all"):
            if message.get(name) is not None:
                event[name] = message.get(name)
        self._broadcast(event, skip=client)
        return protocol.reply(message.id)
