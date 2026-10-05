# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""`RemoteStore`: the client's mirror of the service's `SessionStore`.

Split-service spec §3.15 "Sidebar and store". The service owns the store
(`service.core.ServiceCore`): the scan, the file monitors, the grouping and
every mutation. The client holds this, which offers the store's four
signals (`refreshed`, `unread-changed`, `busy-changed`, `archived`), its
`Gio.ListStore` of `models.SessionItem`s and the same lookups and mutators,
so the sidebar, the window, the switcher and the app use it unchanged.

**How it is filled.** The subscribe snapshot sends an `item` per row, then
`rows`; after that, every refresh of the service's store sends the items
that moved (only their changed fields) and one `rows`. An `item` rebuilds
the `sessions.Session` the client holds for that id when a field of it
moved, and sets the row's properties on its `SessionItem` (busy and unread
announcing their flips, as the store's setters do). `rows` reconciles the
list: items are made for new rows and dropped for rows that left, the
groups, empty headers, project order and counts are taken, the model is
spliced when the order changed, and `refreshed` is emitted: the client's
refresh is the service's, one for one. `put-away` is `archived`. No
transcript is read here: the per-refresh I/O stays on the service.

**Busy is the service's** (rule 3 of §3.1, D29): `set_busy` only sends
`store.flags`, and the row changes when the service's `item` event comes
back. The flags this client decides — `unread`, `status`, the /bg
orchestration's `backgrounding` and `can_background` — are applied to the
row at once (D16) and sent; a refusal puts the old value back. The window's
handlers on the store's signals (the green row's rise, `_reraise_green`)
count on that edge being synchronous, as it was on the loopback.

**Archived sessions are paged.** The snapshot carries the sessions with
rows. The ones kept out of sight are fetched when they are first needed:
`set_show_archived(True)` (the archive drawn in the sidebar),
`archived_sessions()` and `archived_breakdown()` (what a bulk delete
confirms), and the `sessions` attribute, which keeps its old meaning of
*every* session (`store.page-archived`, once; the service keeps paged
sessions current from then on). `known_sessions` is the cheap view: what
the client has been told about (the rows, plus anything paged or looked
up), which is what the sidebar's own refresh reads. `get_session` of an id
the client has not seen asks the service for it (`store.lookup`) and
remembers a miss until the next `rows`. The counts the footer and the
archived actions read (`summary()`, `has_archived()`) come with every
`rows`, so nothing pages just to draw the sidebar.

**Mutations are requests**, each named after the store's method
(`store.rename`, `store.archive`, ...), and optimistic where the client
can know the outcome: the state change goes through `RemoteState.request`
(the mirror first, pending until the reply, reverted on a refusal) and the
row's name and star are re-projected at once (as they are for any change
of a name, a title switch or the favorites in the mirror, a write made
straight on the state included). The new row order of a
favorite or an archive waits for the service's `rows`: one round trip, and
none on the loopback. `trash_many`, `delete` and the lookups wait for their
reply (`Link.call`), which on the loopback is immediate; the socket client
of PR-1.12 makes their callers wait the same way they wait on a dialog.

**Forwarders kept for the e2e checks and the panel code**: `sessions`
(the dict of every session, paging), `pr_store` (the mirror of the
service store's PR hub, `remoteprs.RemotePrStore`), `start()` and the two title
re-projections (`apply_pr_titles`, `apply_cli_titles`), which are no-ops
here because the service runs them when it takes a write of their
settings.

GTK-free: GLib, GObject and Gio only.
"""

from __future__ import annotations

import logging
from pathlib import Path

from gi.repository import Gio, GObject

from . import chats
from .api.protocol import RequestRefused
from .models import SessionItem
from .sessions import Session, worktree_project_root
from .state import merge_project_order, move_in_order
from .store import display_name_for

log = logging.getLogger(__name__)

# The `item` fields a `sessions.Session` is rebuilt from.
_SESSION_FIELDS = frozenset(
    {"path", "cwd", "preview", "mtime", "created", "size", "state", "provider", "cli_title"}
)
# The `item` fields that are SessionItem properties.
_PROPS = (
    "display_name",
    "subtitle",
    "preview",
    "favorite",
    "status",
    "state",
    "busy",
    "unread",
    "syncing",
    "backgrounding",
    "can_background",
    "running",
)
_SIGNALLED = {"busy": "busy-changed", "unread": "unread-changed"}
# The settings whose flip moves what a row's name is: whether the CLI's
# own titles show, and whether PR titles name sessions (the latter writes
# generated names on the service, which then arrive as their own change;
# watched so a flip is never missed).
_TITLE_SWITCHES = ("cli_title_sessions", "pr_title_sessions")
# State keys whose change moves what a row shows (settings: the title
# switches above, and only on a flip).
_NAME_KEYS = frozenset({"names", "generated_names", "cli_titles", "settings"})


class RemoteStore(GObject.Object):
    """See the module docstring."""

    __gsignals__ = {
        # The service's store refreshed (order_changed: rows were re-spliced).
        "refreshed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        # A row's unread flag flipped (session id, new value).
        "unread-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, bool)),
        # A row's busy flag flipped (session id, new value).
        "busy-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, bool)),
        # A session was put away (archived).
        "archived": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # The service's agent ptys moved: one spawned, resolved or exited,
        # or a row's `running` flipped (PR-1.12c, §3.21). The session id
        # whose row it moves, "" for a fresh spawn not yet resolved.
        "running-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, link, state, pr_store=None) -> None:
        """*link* is an `apilink.Link`, *state* the `RemoteState` on the same
        link; *pr_store* the mirror of the service store's PR hub
        (`remoteprs.RemotePrStore`, on the same link)."""
        super().__init__()
        self._link = link
        self.state = state
        self.pr_store = pr_store
        self.model = Gio.ListStore(item_type=SessionItem)
        self.known_sessions: dict[str, Session] = {}
        self.group_counts: dict[tuple, int] = {}
        self.empty_groups: list[tuple[tuple, str, str | None]] = []
        self.resolved_project_order: list[str] = []
        self.show_archived = False
        self._fields: dict[str, dict] = {}  # session id -> its item fields, as last sent
        self._items: dict[str, SessionItem] = {}
        self._paged = False
        self._missing: set[str] = set()  # lookups that found nothing, until the next rows
        self._summary = {"total": 0, "hidden": 0, "size": 0, "projects": (), "chats": 0}
        self._projects: frozenset[str] = frozenset()
        self._title_switches = {name: bool(state.get_setting(name)) for name in _TITLE_SWITCHES}
        # The service's agent ptys, as the subscription's rows of the pty
        # table tell them (PR-1.12c): pty id -> {"session": id or None,
        # "cwd": str}. What a running row attaches to, and the unresolved
        # spawns' "New session" rows.
        self._agent_ptys: dict[int, dict] = {}
        link.on("item", self._on_item)
        link.on("pty", self._on_pty)
        link.on("pty-exited", self._on_pty_exited)
        link.on("rows", self._on_rows)
        link.on("put-away", self._on_put_away)
        state.connect_changed(self._on_state_changed)

    # -- the subscription -----------------------------------------------------------

    def subscribe(self) -> None:
        """Ask for the snapshot and the events after it. The state's mirror
        listens on the same link, so both are filled by this one request."""
        self._link.call({"t": "subscribe"})

    def start(self) -> None:
        """The service's store scans and watches by itself."""

    def reset(self) -> None:
        """The link was lost and is back (spec §3.20): forget what the
        service last said so the next snapshot is taken whole. The items
        and the model stay until the snapshot's `rows` reconciles them,
        so the sidebar redraws once, not twice."""
        self.known_sessions.clear()
        self._fields.clear()
        self._paged = False
        self._missing.clear()
        # The new service's table comes whole with the snapshot.
        gone = [row.get("session") or "" for row in self._agent_ptys.values()]
        self._agent_ptys.clear()
        for session_id in gone:
            self._sync_running(session_id)

    # -- events ---------------------------------------------------------------------

    def _on_item(self, event: dict) -> None:
        session_id = event["session"]
        if event.get("removed"):
            self.known_sessions.pop(session_id, None)
            self._fields.pop(session_id, None)
            return
        changed = {k: v for k, v in event.items() if k not in ("t", "session", "removed")}
        fields = self._fields.setdefault(session_id, {})
        fields.update(changed)
        self._missing.discard(session_id)
        session = self.known_sessions.get(session_id)
        if session is None or _SESSION_FIELDS & changed.keys():
            session = self._session_from(session_id, fields)
            if session is not None:
                self.known_sessions[session_id] = session
        item = self._items.get(session_id)
        if item is not None:
            if session is not None and item.session is not session:
                item.session = session
            self._set_props(item, changed, announce=True)

    def _on_rows(self, event: dict) -> None:
        rows = list(event.get("rows") or [])
        groups = list(event.get("groups") or [])
        keys: list[tuple[tuple, str]] = []
        for group in groups:
            key = (group.get("kind", ""), group.get("name", ""))
            keys.extend([(key, group.get("label", ""))] * int(group.get("count", 0)))
        items: list[SessionItem] = []
        group_counts: dict[tuple, int] = {}
        for index, session_id in enumerate(rows):
            session = self.known_sessions.get(session_id)
            if session is None:
                log.warning("store: a row for %s, which no item named", session_id)
                continue
            key, label = keys[index] if index < len(keys) else (("proj", session.project_name), "")
            item = self._items.get(session_id)
            if item is None:
                item = SessionItem(session)
                self._items[session_id] = item
                self._set_props(item, self._fields.get(session_id, {}), announce=False)
            elif item.session is not session:
                item.session = session
            item.group_key = key
            item.group_label = label
            items.append(item)
            group_counts[key] = group_counts.get(key, 0) + 1
        wanted = {item.session_id for item in items}
        for session_id in list(self._items):
            if session_id not in wanted:
                del self._items[session_id]
        self.group_counts = group_counts
        self.empty_groups = [
            ((entry.get("kind", ""), entry.get("name", "")), entry.get("label", ""), entry.get("cwd"))
            for entry in event.get("empty") or []
        ]
        self.resolved_project_order = list(event.get("order") or [])
        self.show_archived = bool(event.get("show_archived"))
        self._summary = {
            "total": int(event.get("total", 0)),
            "hidden": int(event.get("hidden", 0)),
            "size": int(event.get("size", 0)),
            "projects": tuple(event.get("projects") or ()),
            "chats": int(event.get("chats", 0)),
        }
        self._projects = frozenset(self._summary["projects"])
        self._missing.clear()
        current = [self.model.get_item(i).session_id for i in range(self.model.get_n_items())]
        order_changed = bool(event.get("order_changed")) or current != [i.session_id for i in items]
        if order_changed:
            self.model.splice(0, self.model.get_n_items(), items)
        self.emit("refreshed", order_changed)

    def _on_pty(self, event: dict) -> None:
        """A row of the pty table (the subscription's, ``table: true``): an
        agent pty spawned, or resolved to a session. A view's `pty` event
        (a size, the active client) moves nothing here."""
        if not event.get("table") or event.get("kind") != "agent":
            return
        pty = int(event["pty"])
        session_id = event.get("session") or None
        old = self._agent_ptys.get(pty)
        self._agent_ptys[pty] = {"session": session_id, "cwd": event.get("cwd") or ""}
        if old is not None and old.get("session") and old["session"] != session_id:
            self._sync_running(old["session"])
        self._sync_running(session_id or "")

    def _on_pty_exited(self, event: dict) -> None:
        """An agent pty's child exited: its row leaves the table (the
        subscription's word and a view's alike; the second is a no-op)."""
        row = self._agent_ptys.pop(event.get("pty"), None)
        if row is not None:
            self._sync_running(row.get("session") or "")

    def _sync_running(self, session_id: str) -> None:
        """Put a row's `running` in line with what the service said of it
        (its `item` field, or an agent pty naming it) and tell the window."""
        item = self._items.get(session_id) if session_id else None
        if item is not None:
            running = self.is_running(session_id)
            if item.running != running:
                item.running = running
        self.emit("running-changed", session_id)

    def _on_put_away(self, event: dict) -> None:
        self.emit("archived", event["session"])

    def _on_state_changed(self, key: str, entry, _reverted: bool) -> None:
        """A name or a star moved in the mirror (a write made here, the
        service's event, a refusal's revert): show the rows as the mirror
        has them now. A write made straight on the state (not through a
        store method) re-projects too, so a row never waits for the next
        refresh to show a name the mirror already holds; the service's own
        `item` event, when its refresh comes, carries the same value."""
        if key not in _NAME_KEYS and key != "favorites":
            return
        if key == "settings":
            # Only the title switches move a name, and only when they flip
            # (what the store's apply_cli_titles / apply_pr_titles waited
            # for): any other setting written re-projects nothing.
            switches = {name: bool(self.state.get_setting(name)) for name in _TITLE_SWITCHES}
            if switches == self._title_switches:
                return
            self._title_switches = switches
            self._reproject(list(self._items))
        elif entry is not None:
            self._reproject([entry])
        else:
            self._reproject(list(self._items))

    def _session_from(self, session_id: str, fields: dict) -> Session | None:
        path = fields.get("path")
        if not path:
            return None
        return Session(
            session_id=session_id,
            jsonl_path=Path(path),
            cwd=fields.get("cwd"),
            preview=fields.get("preview", ""),
            mtime=float(fields.get("mtime", 0.0)),
            created=float(fields.get("created", 0.0)),
            size=int(fields.get("size", 0)),
            state=fields.get("state", ""),
            provider=fields.get("provider", "claude"),
            cli_title=fields.get("cli_title", ""),
        )

    def _set_props(self, item: SessionItem, fields: dict, announce: bool) -> None:
        for prop in _PROPS:
            if prop not in fields:
                continue
            value = fields[prop]
            if prop == "running":
                # The field, or a row of the pty table naming the session.
                value = bool(value) or self._table_runs(item.session_id)
            if item.get_property(prop) == value:
                continue
            item.set_property(prop, value)
            if announce and prop in _SIGNALLED:
                self.emit(_SIGNALLED[prop], item.session_id, bool(value))
            if announce and prop == "running":
                self.emit("running-changed", item.session_id)

    def _reproject(self, session_ids) -> None:
        """Put a row's name and star back in line with the mirror (after an
        optimistic write, or its revert). The name and the star only: a
        star's flip does not move the row into Favorites here (its
        `group_key` and the list's order wait for the service's `rows`, one
        round trip; none on the loopback). PR-1.12 revisits that once the
        socket makes the round trip visible."""
        for session_id in session_ids:
            item = self._items.get(session_id)
            if item is None:
                continue
            name = display_name_for(self.state, item.session)
            if item.display_name != name:
                item.display_name = name
            favorite = self.state.is_favorite(session_id)
            if item.favorite != favorite:
                item.favorite = favorite

    # -- the service's agent ptys (PR-1.12c, §3.21) ----------------------------------

    def _table_runs(self, session_id: str) -> bool:
        return any(row.get("session") == session_id for row in self._agent_ptys.values())

    def is_running(self, session_id: str) -> bool:
        """Whether an agent pty on the service runs *session_id* (its item
        field, or a row of the pty table)."""
        if not session_id:
            return False
        return bool(self._fields.get(session_id, {}).get("running")) or self._table_runs(session_id)

    def pty_for(self, session_id: str) -> int | None:
        """The agent pty running *session_id*, or the session its forward
        chain continued under (newest first); None when none runs it."""
        if not session_id:
            return None
        chain = list(reversed(self.state.forward_chain(session_id))) or [session_id]
        for sid in chain:
            for pty, row in sorted(self._agent_ptys.items()):
                if row.get("session") == sid:
                    return pty
        return None

    def agent_ptys(self) -> dict[int, dict]:
        """Every agent pty the service runs: pty id -> ``{"session": id or
        None, "cwd": str}`` (a copy)."""
        return {pty: dict(row) for pty, row in self._agent_ptys.items()}

    def pty_running(self, pty: int) -> bool:
        """Whether the service still runs agent pty *pty*."""
        return pty in self._agent_ptys

    def unresolved_ptys(self) -> dict[int, str]:
        """The agent ptys whose session is not known yet (a fresh spawn
        the resolver has not bound, an unsandboxed fork): pty id -> cwd."""
        return {
            pty: row.get("cwd") or ""
            for pty, row in self._agent_ptys.items()
            if not row.get("session")
        }

    # -- lookups ----------------------------------------------------------------------

    def get_item(self, session_id: str) -> SessionItem | None:
        return self._items.get(session_id)

    def get_session(self, session_id: str) -> Session | None:
        """The session, from what the client holds or, for one it was never
        sent (out of sight), from the service. A miss is remembered until
        the next `rows` (the next refresh of the service's store): a
        session that appears on disk in between is found by asking again
        after that refresh, which is when the store itself would first
        have known it."""
        session = self.known_sessions.get(session_id)
        if session is not None or not session_id or session_id in self._missing:
            return session
        try:
            found = bool(self._link.call({"t": "store.lookup", "session": session_id}).get("found"))
        except RequestRefused:
            found = False
        if not found:
            self._missing.add(session_id)
            return None
        return self.known_sessions.get(session_id)

    @property
    def sessions(self) -> dict[str, Session]:
        """Every session, out of sight or not (the archived ones paged in on
        first use), as `SessionStore.sessions` was."""
        self._page_archived()
        return self.known_sessions

    def row_ids(self) -> list[str]:
        return list(self._items)

    def rows_representing(self, session_id: str) -> list[str]:
        """The rows that stand for this session (SessionStore's, over the
        mirrored forward chains)."""
        rows = [session_id] if session_id in self._items else []
        rows += [
            row_id
            for row_id in self._items
            if row_id != session_id and session_id in self.state.forward_chain(row_id)
        ]
        return rows

    def display_name(self, session: Session) -> str:
        return display_name_for(self.state, session)

    def forward_state(self, session: Session) -> str:
        """"moved", "syncing" or "": the service's answer (it reads the
        fork's transcript off its disk), as the session's item last said."""
        return str((self._fields.get(session.session_id) or {}).get("forward", ""))

    def is_out_of_sight(self, session: Session) -> bool:
        return (
            self.state.is_archived(session.session_id)
            or self.state.is_project_archived(session.project_name)
            or self.forward_state(session) == "moved"
        )

    def archived_sessions(self) -> list[Session]:
        return [s for s in self.sessions.values() if self.is_out_of_sight(s)]

    def archived_breakdown(self) -> list[tuple[str, int, int]]:
        def display_project(session: Session) -> str:
            return "Chats" if chats.is_chat_cwd(session.cwd) else session.project_name

        sessions = list(self.sessions.values())
        totals: dict[str, int] = {}
        for session in sessions:
            totals[display_project(session)] = totals.get(display_project(session), 0) + 1
        archived: dict[str, int] = {}
        for session in sessions:
            if self.is_out_of_sight(session):
                archived[display_project(session)] = archived.get(display_project(session), 0) + 1
        return sorted(
            ((name, count, totals[name]) for name, count in archived.items()),
            key=lambda row: (-row[1], row[0]),
        )

    def project_cwd(self, project_name: str) -> str | None:
        return next(
            (
                worktree_project_root(s.cwd) or s.cwd
                for s in self.sessions.values()
                if s.project_name == project_name and s.cwd
            ),
            None,
        )

    def is_virtual_project(self, project_name: str) -> bool:
        """A kept header with no sessions of its own (SessionStore's
        question), over the project names the last `rows` listed."""
        return self.state.is_virtual_project(project_name) and project_name not in self._projects

    def summary(self) -> dict:
        """What the sidebar's footer counts, as of the last `rows`: every
        session (``total``), their bytes (``size``), the projects with any
        (``projects``, chats aside) and the chat sessions (``chats``)."""
        return dict(self._summary)

    def has_archived(self) -> bool:
        """Whether any session is kept out of sight (the trash-archived
        action's sensitivity), without paging them in."""
        return self._summary["hidden"] > 0

    def session_count(self) -> int:
        """Every session the service's last scan found (0 before one)."""
        return self._summary["total"]

    def _page_archived(self) -> None:
        if self._paged:
            return
        try:
            self._link.call({"t": "store.page-archived"})
        except RequestRefused as refusal:
            log.warning("store: the archived sessions could not be paged in: %s", refusal.msgid)
            return
        self._paged = True

    # -- mutations (each a request; see the module docstring) -------------------------

    def refresh(self, force_rebuild: bool = False) -> None:
        self._link.send({"t": "store.refresh", "force": bool(force_rebuild)})

    def rename(self, session_id: str, name: str) -> None:
        self.state.request(
            {"t": "store.rename", "session": session_id, "name": name},
            mutate=lambda: self.state.set_name(session_id, name),
            applied=lambda: self._reproject([session_id]),
        )

    def regenerate_name(self, session_id: str) -> None:
        self._link.send({"t": "store.regenerate-name", "session": session_id})

    def toggle_favorite(self, session_id: str) -> None:
        self.set_favorites([session_id], not self.state.is_favorite(session_id))

    def set_favorites(self, session_ids: list[str], favorite: bool) -> None:
        if not session_ids:
            return

        def mutate() -> None:
            for session_id in session_ids:
                if self.state.is_favorite(session_id) != favorite:
                    self.state.toggle_favorite(session_id)

        self.state.request(
            {"t": "store.favorite", "sessions": list(session_ids), "favorite": bool(favorite)},
            mutate=mutate,
            applied=lambda: self._reproject(session_ids),
        )

    def set_archived(self, session_id: str, archived: bool) -> None:
        self._archive([session_id], archived)

    def archive_many(self, session_ids: list[str]) -> None:
        self._archive(session_ids, True)

    def restore_many(self, session_ids: list[str]) -> None:
        self._archive(session_ids, False)

    def _archive(self, session_ids: list[str], archived: bool) -> None:
        if not session_ids:
            return

        def mutate() -> None:
            for session_id in session_ids:
                self.state.set_archived(session_id, archived)

        self.state.request(
            {"t": "store.archive", "sessions": list(session_ids), "archived": bool(archived)},
            mutate=mutate,
        )

    def set_project_archived(self, project_name: str, archived: bool) -> None:
        self.state.request(
            {"t": "store.archive-project", "project": project_name, "archived": bool(archived)},
            mutate=lambda: self.state.set_project_archived(project_name, archived),
        )

    def trash(self, session_id: str) -> str | None:
        """Move the transcript to trash. Returns an error message or None."""
        return self.trash_many([session_id]).get(session_id)

    def trash_many(self, session_ids: list[str]) -> dict[str, str]:
        if not session_ids:
            return {}
        try:
            reply = self._link.call({"t": "store.trash", "sessions": list(session_ids)})
        except RequestRefused as refusal:
            return {session_id: refusal.msgid for session_id in session_ids}
        return dict(reply.get("errors") or {})

    def delete(self, session_id: str) -> str | None:
        try:
            reply = self._link.call({"t": "store.delete", "session": session_id})
        except RequestRefused as refusal:
            return refusal.msgid
        return reply.get("error") or None

    def record_forward(self, old_id: str, new_id: str) -> None:
        self.state.request(
            {"t": "store.forward", "session": old_id, "to": new_id},
            mutate=lambda: self.state.forward_session(old_id, new_id),
        )

    def keep_projects(self, project_names: list[str]) -> None:
        if project_names:
            self.state.request({"t": "store.keep-projects", "projects": list(project_names)})

    def add_project(self, cwd: str) -> None:
        self.state.request({"t": "store.add-project", "cwd": cwd})

    def forget_project(self, project_name: str) -> None:
        self.state.request(
            {"t": "store.forget-project", "project": project_name},
            mutate=lambda: self.state.forget_virtual_project(project_name),
        )

    def move_project(self, name: str, before: str | None) -> None:
        def mutate() -> None:
            order = merge_project_order(
                self.state.get_project_order(), set(self.resolved_project_order) | {name}
            )
            self.state.set_project_order(move_in_order(order, name, before))

        self.state.request(
            {"t": "store.move-project", "project": name, "before": before},
            mutate=mutate,
        )

    def set_show_archived(self, show: bool) -> None:
        if show:
            self._page_archived()
        self.show_archived = show
        self._link.send({"t": "store.show-archived", "show": bool(show)})

    def apply_pr_titles(self) -> None:
        """The service re-titles when it takes a write of the setting."""

    def apply_cli_titles(self) -> None:
        """The service re-projects when it takes a write of the setting."""

    # -- the tracker's verdicts (store.flags) -------------------------------------

    def set_status(self, session_id: str, status: str) -> None:
        self._flag(session_id, "status", status)

    def set_busy(self, session_id: str, flag: bool) -> None:
        self._flag(session_id, "busy", bool(flag))

    def set_unread(self, session_id: str, flag: bool) -> None:
        self._flag(session_id, "unread", bool(flag))

    def set_backgrounding(self, session_id: str, flag: bool) -> None:
        self._flag(session_id, "backgrounding", bool(flag))

    def set_can_background(self, session_id: str, flag: bool) -> None:
        self._flag(session_id, "can_background", bool(flag))

    def _flag(self, session_id: str, prop: str, value) -> None:
        """A verdict for a row: sent only when it would change the row (the
        store's setters are no-ops otherwise, and a row the client lacks is
        one the service lacks). A flag this client decides (`unread`,
        `status`, the /bg orchestration's two: D29) is applied to the row
        at once, so what listens on the store's signals (the green row's
        rise in `App._sync_green`, the window's `_reraise_green`, which
        counts on the edge being synchronous) sees it inside the call, as
        on the loopback; the service's `item` echo confirms it (D16).
        `busy` is the service's verdict and is only ever sent."""
        item = self._items.get(session_id)
        if item is None or item.get_property(prop) == value:
            return
        if prop == "busy":
            self._link.send({"t": "store.flags", "session": session_id, prop: value})
            return
        before = item.get_property(prop)

        def refused(refusal) -> None:
            log.warning("store: %s of %s refused: %s", prop, session_id, refusal.msgid)
            current = self._items.get(session_id)
            if current is not None and current.get_property(prop) == value:
                self._set_props(current, {prop: before}, announce=True)

        self._set_props(item, {prop: value}, announce=True)
        self._link.send({"t": "store.flags", "session": session_id, prop: value}, None, refused)

    # -- the CLI's folder trust (trust.*) -------------------------------------------

    def folder_trust(self, path: str) -> tuple[bool, str]:
        """Whether the CLI trusts *path* already, and the folder a trust
        would be recorded on (`trust.trust_root`)."""
        try:
            reply = self._link.call({"t": "trust.check", "path": path})
        except RequestRefused:
            return False, path
        return bool(reply.get("trusted")), str(reply.get("root") or path)

    def trust_folder(self, path: str, launch: bool = False) -> bool:
        """Record the CLI's trust: on *path*'s root, or (*launch*) on a
        worktree launch's directory and its repository."""
        try:
            reply = self._link.call(
                {"t": "trust.grant", "path": path, "scope": "launch" if launch else "root"}
            )
        except RequestRefused:
            return False
        return bool(reply.get("written"))
