# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""What the service tells its clients about the store and the state.

The store and state half of `ServiceCore` (spec §3.8 "The mirror", §3.15
"Sidebar and store", PR-1.10). The service owns `SessionStore` and
`AppState`; every client holds a mirror of both (`remotestore.RemoteStore`,
`remotestate.RemoteState`), filled by the snapshot `subscribe` sends and
kept by events. `StoreFeed` is the service's end of that: it hears every
change and turns it into the events of `api.protocol`, per subscriber.

**State.** `AppState.on_saved` fires after every write of `state.json`. The
feed diffs each of `state.SHARED_KEYS` against what it last published and
sends a `state.set` event per change: a map key per entry, the rest whole.
A write a client asked for is no exception: the requester gets the echo
too, which is how its optimistic copy learns the value the service kept.

**Items.** A subscriber is sent the sessions with rows, an `item` event
each, every field the first time and only the fields that moved after
that (`known` is what it was last sent). On every `refreshed` of the store
each known session is rebuilt and diffed; one that is gone from the scan is
sent `removed`. The sessions kept out of sight are not sent until the
client asks: `page()` (the `store.page-archived` request) marks the
subscriber and sends them all, `send_one()` (`store.lookup`) one. A session
once sent stays known until it is removed, so the client's copy never goes
stale. Busy, unread, status and the background agents' three facts
(`backgrounding`, `can_background` and `background`, PR-1.12d) are
properties of the store's `SessionItem`s set outside a refresh: the feed
watches each row item's `notify` for them and sends the one field at once
(rule 3 of §3.1: the service decides, the client shows). `running` (an
agent pty on the service names the session, PR-1.12c) is the pty table's
fact: worked out on every rebuild, and sent when a pty spawns, resolves or
exits (`refresh_running`, from `ServiceCore`).

**Rows.** After the items of a refresh, one `rows` event carries the
projection: the row order, the groups with their counts, the empty
project headers, the resolved project order and the counts the sidebar's
footer and the archived actions read. It is the client's `refreshed`, so it
is sent last; a client's listeners that make requests while handling it
find every item already current.

GLib only; nothing here imports GTK.
"""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import Callable
from typing import Any

from .. import chats
from ..api import protocol
from ..i18n import _
from ..state import MAP, SHARED_KEYS, diff_shared
from ..store import _relative_time, display_name_for

log = logging.getLogger(__name__)

# The SessionItem properties set outside a refresh (the tracker's verdicts,
# the background agents' facts), sent the moment they move.
FLAGS = ("status", "busy", "unread", "backgrounding", "can_background", "background")
_FLAG_DEFAULTS: dict[str, Any] = {
    "status": "",
    "busy": False,
    "unread": False,
    "backgrounding": False,
    "can_background": False,
    "background": "",
}
# The flags that are words, not switches.
_TEXT_FLAGS = frozenset({"status", "background"})

# A map key larger than this (as JSON) goes out entry by entry in a
# snapshot, so no frame nears the cap and no value nears the node bound.
_WHOLE_MAX = 128 * 1024


def _clamp(text: object, high: int) -> str:
    return text[:high] if isinstance(text, str) else ""


def _path_or_none(path: object) -> str | None:
    """A path as the protocol carries one, or None: absolute, no NUL,
    bounded. A transcript's recorded cwd is foreign content."""
    if not isinstance(path, str) or not path.startswith("/") or "\0" in path:
        return None
    if len(path) > protocol.PATH_MAX:
        return None
    return path


class Subscriber:
    """One client's subscription: how to reach it, and what it was told."""

    __slots__ = ("client", "deliver", "known", "paged")

    def __init__(self, client, deliver: Callable[[dict], None]) -> None:
        self.client = client
        self.deliver = deliver
        # session id -> the item fields this client was last sent.
        self.known: dict[str, dict] = {}
        # Whether it asked for the sessions kept out of sight.
        self.paged = False


class StoreFeed:
    """The store and state half of the service's events. See the module
    docstring."""

    def __init__(self, store, state, running: Callable[[], set[str]] | None = None) -> None:
        """*running* answers the session ids an agent pty on the service
        names right now (`ServiceCore.running_sessions`): each item's
        `running` field (PR-1.12c, §3.21)."""
        self.store = store
        self.state = state
        self._running = running or (lambda: set())
        self._subscribers: dict[int, Subscriber] = {}
        self._published: dict[str, Any] = {
            name: copy.deepcopy(state.export_key(name)) for name in SHARED_KEYS
        }
        # session id -> (the row's SessionItem, its notify handler id).
        self._watched: dict[str, tuple[Any, int]] = {}
        state.on_saved = self._on_saved
        store.connect("refreshed", self._on_refreshed)
        store.connect("archived", self._on_archived)

    # -- subscribers -------------------------------------------------------------

    def subscribe(self, client, deliver: Callable[[dict], None]) -> int:
        """The snapshot: every shared key as `state.set` events, then an
        `item` per row and, once the store has projected a scan, the
        `rows`. Returns the number of `item` events sent."""
        sub = Subscriber(client, deliver)
        self._subscribers[id(client)] = sub
        for name, key in SHARED_KEYS.items():
            value = self.state.export_key(name)
            if key.form == MAP and isinstance(value, dict) and _too_big(value):
                for entry, entry_value in value.items():
                    self._send(sub, protocol.event("state.set", key=name, entry=entry, value=entry_value))
            else:
                self._send(sub, protocol.event("state.set", key=name, value=value))
        if not self.store.applied:
            return 0
        sent = 0
        for item in self.store.row_items():
            session = self.store.get_session(item.session_id)
            if session is not None and self._sync_item(sub, session):
                sent += 1
        self._watch_items()
        self._send(sub, self._rows_event(order_changed=True))
        return sent

    def unsubscribe(self, client) -> None:
        self._subscribers.pop(id(client), None)

    def is_subscribed(self, client) -> bool:
        return id(client) in self._subscribers

    def page(self, client) -> int:
        """Send *client* every session kept out of sight, and keep them
        current from now on. Returns how many were sent."""
        sub = self._subscribers.get(id(client))
        if sub is None:
            return 0
        sub.paged = True
        sent = 0
        for session in self.store.all_sessions():
            if session.session_id not in sub.known and self._sync_item(sub, session):
                sent += 1
        return sent

    def send_one(self, client, session_id: str) -> bool:
        """Send *client* one session, row or not, and keep it current.
        False when the service has no such session."""
        sub = self._subscribers.get(id(client))
        session = self.store.get_session(session_id)
        if sub is None or session is None:
            return False
        self._sync_item(sub, session, force=True)
        return True

    # -- state ------------------------------------------------------------------

    def _on_saved(self) -> None:
        """Publish what the save changed. A map entry is sent whole, and
        an entry near the protocol's TEXT_MAX (a long draft, a large
        attachments list) plus its framing would pass MAX_FRAME once the
        socket encodes it, so the socket's sender has to chunk such an entry
        or refuse it."""
        for name, key in SHARED_KEYS.items():
            current = self.state.export_key(name)
            old = self._published.get(name)
            changes = diff_shared(key, old, current)
            if not changes:
                continue
            for entry, value in changes:
                if entry is None:
                    event = protocol.event("state.set", key=name, value=value)
                elif not entry:
                    continue  # an entry the protocol can't name; nobody writes one
                else:
                    event = protocol.event("state.set", key=name, entry=entry, value=value)
                for sub in list(self._subscribers.values()):
                    self._send(sub, event)
            if key.form == MAP and isinstance(old, dict) and all(e is not None for e, _v in changes):
                for entry, value in changes:
                    if value is None:
                        old.pop(entry, None)
                    else:
                        old[entry] = copy.deepcopy(value)
            else:
                self._published[name] = copy.deepcopy(current)

    # -- the store ------------------------------------------------------------------

    def _on_refreshed(self, _store, order_changed: bool) -> None:
        self._watch_items()
        if not self._subscribers:
            return
        rows = [item.session_id for item in self.store.row_items()]
        for sub in list(self._subscribers.values()):
            wanted = list(rows)
            seen = set(rows)
            extra = (
                [s.session_id for s in self.store.all_sessions()] if sub.paged else []
            ) + list(sub.known)
            for session_id in extra:
                if session_id not in seen:
                    seen.add(session_id)
                    wanted.append(session_id)
            for session_id in wanted:
                session = self.store.get_session(session_id)
                if session is None:
                    if sub.known.pop(session_id, None) is not None:
                        self._send(sub, protocol.event("item", session=session_id, removed=True))
                    continue
                self._sync_item(sub, session)
        event = self._rows_event(order_changed)
        for sub in list(self._subscribers.values()):
            self._send(sub, event)

    def refresh_running(self, session_ids) -> None:
        """An agent pty appeared, resolved or exited (PR-1.12c): every
        subscriber that knows one of *session_ids* is sent what moved in
        it (its `running`, the one field such a change moves)."""
        for session_id in {s for s in session_ids if s}:
            session = self.store.get_session(session_id)
            if session is None:
                continue
            for sub in list(self._subscribers.values()):
                if session_id in sub.known:
                    self._sync_item(sub, session)

    def _on_archived(self, _store, session_id: str) -> None:
        event = protocol.event("put-away", session=session_id)
        for sub in list(self._subscribers.values()):
            self._send(sub, event)

    def _watch_items(self) -> None:
        """Follow the flags of every row's item, and only those: an item
        that left the list (or was replaced) is let go."""
        current = {item.session_id: item for item in self.store.row_items()}
        for session_id, (item, handler) in list(self._watched.items()):
            if current.get(session_id) is not item:
                try:
                    item.disconnect(handler)
                except TypeError:
                    pass
                del self._watched[session_id]
        for session_id, item in current.items():
            if session_id not in self._watched:
                handler = item.connect("notify", self._on_item_notify)
                self._watched[session_id] = (item, handler)

    def _on_item_notify(self, item, pspec) -> None:
        name = pspec.name.replace("-", "_")
        if name not in _FLAG_DEFAULTS:
            return
        value = item.get_property(name)
        if name in _TEXT_FLAGS:
            value = _clamp(value, protocol.SHORT_MAX)
        session_id = item.session_id
        for sub in list(self._subscribers.values()):
            known = sub.known.get(session_id)
            if known is None or known.get(name) == value:
                continue
            known[name] = value
            self._send(sub, protocol.event("item", session=session_id, **{name: value}))

    # -- items ------------------------------------------------------------------

    def item_fields(self, session) -> dict:
        """Everything a client holds for one session: the `Session` it is
        rebuilt as, the row's bindable properties (off the store's item
        when the session has a row, worked out the same way when not), the
        forward state that needs this machine's disk, and the flags."""
        store = self.store
        session_id = session.session_id
        item = store.get_item(session_id)
        forward = store.forward_state(session)
        if item is not None:
            display_name = item.display_name
            subtitle = item.subtitle
            favorite = item.favorite
            syncing = item.syncing
            flags = {name: item.get_property(name) for name in FLAGS}
        else:
            display_name = display_name_for(self.state, session)
            subtitle = _("replaced") if forward == "moved" else _relative_time(session.last_active)
            favorite = self.state.is_favorite(session_id)
            syncing = forward == "syncing"
            flags = dict(_FLAG_DEFAULTS)
        fields = {
            "path": _path_or_none(str(session.jsonl_path)) or "/",
            "cwd": _path_or_none(session.cwd),
            "project": _clamp(session.project_name, protocol.PATH_MAX),
            "display_name": _clamp(display_name, protocol.NAME_MAX),
            "cli_title": _clamp(session.cli_title, protocol.NAME_MAX),
            "subtitle": _clamp(subtitle, 256),
            "preview": _clamp(session.preview, protocol.PREVIEW_MAX),
            "provider": _clamp(session.provider, protocol.SHORT_MAX),
            "favorite": bool(favorite),
            "state": _clamp(session.state, protocol.SHORT_MAX),
            "syncing": bool(syncing),
            "forward": _clamp(forward, protocol.SHORT_MAX),
            "mtime": float(session.mtime),
            "created": float(session.created),
            "size": max(0, int(session.size)),
        }
        for name, value in flags.items():
            fields[name] = _clamp(value, protocol.SHORT_MAX) if name in _TEXT_FLAGS else bool(value)
        # Whether an agent pty on the service runs this session: a fact of
        # the pty table, not of the item, so worked out on every rebuild.
        try:
            fields["running"] = session_id in self._running()
        except Exception:
            log.exception("store: the running sessions could not be read")
            fields["running"] = False
        return fields

    def _sync_item(self, sub: Subscriber, session, force: bool = False) -> bool:
        """Send *sub* what moved in one session since it was last told
        (everything, the first time or with *force*). True when an event
        went out."""
        fields = self.item_fields(session)
        session_id = session.session_id
        known = sub.known.get(session_id)
        if known is None or force:
            changed = fields
        else:
            changed = {k: v for k, v in fields.items() if known.get(k, _MISSING) != v}
        sub.known[session_id] = fields
        if not changed:
            return False
        self._send(sub, protocol.event("item", session=session_id, **changed))
        return True

    # -- rows ------------------------------------------------------------------

    def _rows_event(self, order_changed: bool) -> dict:
        store = self.store
        items = store.row_items()
        if len(items) > protocol.ROWS_MAX:
            log.warning("store: %d rows, more than a rows event carries", len(items))
            items = items[: protocol.ROWS_MAX]
        groups: list[dict] = []
        for item in items:
            key = tuple(item.group_key)
            if groups and groups[-1]["_key"] == key:
                groups[-1]["count"] += 1
                continue
            groups.append(
                {
                    "_key": key,
                    "kind": _clamp(key[0], protocol.SHORT_MAX),
                    "name": _clamp(key[1] if len(key) > 1 else "", protocol.NAME_MAX),
                    "label": _clamp(item.group_label, protocol.NAME_MAX),
                    "count": 1,
                }
            )
        for group in groups:
            del group["_key"]
        empty = []
        for key, label, cwd in store.empty_groups[: protocol.ROWS_MAX]:
            entry = {
                "kind": _clamp(key[0], protocol.SHORT_MAX),
                "name": _clamp(key[1], protocol.NAME_MAX),
                "label": _clamp(label, protocol.NAME_MAX),
            }
            path = _path_or_none(cwd)
            if path is not None:
                entry["cwd"] = path
            empty.append(entry)
        sessions = store.all_sessions()
        projects = sorted(
            {s.project_name for s in sessions if not chats.is_chat_cwd(s.cwd)}
        )[: protocol.ROWS_MAX]
        return protocol.event(
            "rows",
            rows=[item.session_id for item in items],
            groups=groups,
            empty=empty,
            order=[_clamp(n, protocol.NAME_MAX) for n in store.resolved_project_order[: protocol.ROWS_MAX]],
            order_changed=bool(order_changed),
            show_archived=bool(store.show_archived),
            total=len(sessions),
            hidden=sum(1 for s in sessions if store.is_out_of_sight(s)),
            size=sum(max(0, int(s.size)) for s in sessions),
            projects=[_clamp(n, protocol.NAME_MAX) for n in projects],
            chats=sum(1 for s in sessions if chats.is_chat_cwd(s.cwd)),
        )

    # -- delivery ------------------------------------------------------------------

    def _send(self, sub: Subscriber, event: dict) -> None:
        try:
            sub.deliver(event)
        except Exception:
            log.exception("store: a subscriber's delivery failed")


_MISSING = object()


def _too_big(value: dict) -> bool:
    try:
        return len(json.dumps(value, ensure_ascii=False)) > _WHOLE_MAX
    except (TypeError, ValueError):
        return True
