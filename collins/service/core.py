# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""`ServiceCore`: the service's request router.

The service of the split (spec §3.1) is one object the transport hands
validated messages to and takes events back from. It has two halves so
far. The pty half: the pty table (`ptyserver.PtyServer`) and the requests
that reach it (`spawn`, `attach`, `detach`, `paint`, `close`) and the
events that come from a client about it (`resize`, `focus`, `theme`). The
store and state half (PR-1.10): the core owns the service's `AppState`
(built with ``migrate=True``: the service's instance is the one that
writes the state split, and with ``device=False``: ui-state.json is the
client's from then on) and `SessionStore`, answers `subscribe` with the
snapshot and keeps every subscriber current through `storefeed.StoreFeed`,
and serves `state.get`, `state.set` and every mutation the sidebar, the
window and Preferences make as a `store.*` request, plus the CLI's folder
trust (`trust.*`). PRs, notifications and the tools join in PR-1.11.
GLib only; nothing here imports GTK.

**The service decides which keys a client writes** (`state.set`): every
key of `state.SHARED_KEYS` but the three only the service writes
(``service_id``, ``ptys``, ``pty_next_id``), and `settings` one service
setting at a time; a device setting, which lives in the client's
ui-state.json and never reaches the service, is `refused`, and so is a
value the key's cleaner would drop, a setting the catalogue
(`state.DEFAULT_SETTINGS`) does not name, and a setting of a type other
than its default's (rule 5: a bool is no int, an int stands in for a
float). A settings write is followed by the
store's two reactions to its title switches (`apply_pr_titles`,
`apply_cli_titles`): the service acting on its own settings.

**The activity tracker's boundary** (§3.6). Busy, unread and status are
service facts that arrive at the client as `item` fields. In Phase 1 the
tracker that decides them still runs in the client's process, beside the
tab whose `Session` it watches (`window.MainWindow`'s `ActivityTracker`);
its verdicts come here as `store.flags` requests, the service's store sets
them on its `SessionItem`s, and the client's `RemoteStore` shows them when
the `item` events come back. When the session moves into the service the
tracker comes with it and `store.flags` goes.

A client is anything the transport represents as a `Client`: it has a
`device` name, a sink per pty (`sink_for(pty)`: the object the pty server
hands output and events to), `forget(pty)` for when a pty is gone, and
`deliver(event)` for the store and state events of its subscription. The
loopback of PR-1.7 (`api.loopback`) is the one transport until PR-1.12
brings the socket; both give the core the same dicts, already validated
by `api.protocol`, and get the same replies back.

Spawning is the core's: a client asks for an agent's or a shell's pty in a
directory at a grid, and the core runs the user's `$SHELL` there with the
service's own environment (the app's, in Phase 1) plus the two declarations
that coax the CLI's progress announcements out (`session.agent_environment`,
decided by the `progress_termprop` setting at spawn, as the VTE path did).
The agent's command line is typed in by its `Session` (still on the client
through Phase 1), exactly as before the split; a sandboxed launch types the
`sandboxrun.py` wrapper the same way. A `SpawnError` is refused with the
child's errno in the message, so the tab shows it where it used to show
VTE's spawn error.

`close` has one meaning in Phase 1 whatever its mode: the pty's child is
sent SIGHUP and the master closed (`PtyServer.close`), which is what the
tab's widget did to its VTE child. The graceful keystrokes of mode ``exit``
and the /bg handoff of ``background`` are the `Session`'s until PR-1.10
moves it into the service; ``kill`` is the same close with no grace to
give, because `PtyServer.close` already escalates on its own.

Panel shells (PR-1.8, spec §3.15) are ptys of kind ``shell``: the user's
`$SHELL` with the service's environment and none of the agent's progress
declarations (a panel shell never had them). A sandboxed one (``sandbox``
with the session's ``sandbox_box``) runs `providers.sandboxed_shell_argv`
around the shell, the launcher a sandboxed session's typed line starts
with, on the plan the service's own records hold for that box (the
*sandbox_plan* lookup: in Phase 1 the `Session` the box belongs to, still
held by its tab), and is refused when there is none; the plan is the pty's
(its row's ``plan``, read back as the shell's `sandbox_plan`), and a cwd
inside the box's workspace other than where bwrap lands the shell is one
queued ``cd`` away, typed before anything else. ``clear`` on a shell wipes
its model (`PtyServer.clear`); on an agent it is the composer's erase of
the box, PR-1.10's. The panel history is written here from the models
(`write_panel_history`), and the cwd, foreground and inner-shell reads a
panel shell asks of its pty are the pty server's (`Pty.process_cwd`,
`Pty.has_running_command`, `Pty.shell_pid`).
"""

from __future__ import annotations

import logging
import os
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from .. import panelhistory, providers, sandboxplan, trust
from ..api import protocol
from ..shellinput import shell_command
from ..state import MAP, SCALAR, SHARED_KEYS
from . import ptyserver, storefeed, termstream

log = logging.getLogger(__name__)

# The requests of the store and state half, besides every ``store.*`` type:
# refused as `unknown` by a core with no store. ``trust.*`` is not gated: the
# CLI's folder trust is a file of the service's machine, not the store's.
_STORE_REQUESTS = frozenset({"subscribe", "state.get", "state.set"})


class Client(Protocol):
    """What the core needs of a connected client (the transport's object)."""

    device: str
    term: dict  # its terminal (the hello's ``term``, the ``theme`` event)

    def sink_for(self, pty: int):
        """The sink the pty server hands this client's output and events
        for *pty* (one object per client and pty, kept for the attachment's
        life)."""
        ...

    def forget(self, pty: int) -> None:
        """The pty is gone (exited or closed): drop its sink."""
        ...

    def deliver(self, event: dict) -> None:
        """An event of this client's store and state subscription."""
        ...


class ServiceCore:
    """The router. See the module docstring."""

    def __init__(
        self,
        *,
        state_dir: Path | None = None,
        record: Callable[[int, dict | None], None] | None = None,
        record_next_id: Callable[[int], None] | None = None,
        next_id: int | None = None,
        get_setting: Callable[[str], Any] = lambda key: None,
        environment: Callable[[], dict[str, str]] | None = None,
        on_stream_event: Callable[[int, object], None] | None = None,
        sandbox_plan: Callable[[str], str | None] | None = None,
        state=None,
    ) -> None:
        """*record*, *record_next_id* and *next_id* are `AppState.set_pty`,
        `AppState.set_pty_next_id` and `AppState.pty_next_id` (the pty table
        of §3.8); *get_setting* reads the service's settings (only
        ``progress_termprop`` so far); *environment* is the session
        environment of §3.10 (the process's own by default); *on_stream_event*
        hears every stream event (progress, bell, title, modes) off every
        pty, for the service's own detection (§3.6); *sandbox_plan*
        is the plan file the session running in a box launched from, by the
        box's id (None when there is none): what a sandboxed panel shell is
        spawned on.

        *state* is the service's `AppState`; given one, the pty table is
        written to it and the settings are read off it (the callables above
        default to its), and `start_store` builds the store over it.
        Without one (a check or a test driving ptys alone) the core is the
        pty half only, and the store's requests are `unknown`."""
        self.state = state
        self.store = None
        self.feed: storefeed.StoreFeed | None = None
        if state is not None:
            record = record or state.set_pty
            record_next_id = record_next_id or state.set_pty_next_id
            if next_id is None:
                next_id = state.pty_next_id
            get_setting = state.get_setting
        self._get_setting = get_setting
        self._sandbox_plan = sandbox_plan or (lambda box: None)
        self._environment = environment or (lambda: dict(os.environ))
        self.ptys = ptyserver.PtyServer(
            state_dir=state_dir,
            record=record,
            record_next_id=record_next_id,
            next_id=1 if next_id is None else next_id,
            on_event=on_stream_event,
        )
        self._clients: set[int] = set()  # id(client)
        pruned = self.ptys.prune_models()
        if pruned:
            log.info("pruned %d model file(s) of ptys no longer in the table", pruned)

    @classmethod
    def with_state(cls, **kwargs) -> ServiceCore:
        """The service's core over its own `AppState`: the instance that
        migrates the state split and never writes the device's half (see
        the module docstring). The store is started apart (`start_store`),
        so what must happen between the state and the first scan can (the
        CLI's saved path going on PATH)."""
        from ..state import AppState

        return cls(state=AppState(migrate=True, device=False), **kwargs)

    def start_store(self, store=None) -> None:
        """Build the `SessionStore` over the core's state, publish it and
        start its first scan; or publish *store*, one a test built."""
        if self.state is None:
            raise RuntimeError("the core has no state to build a store over")
        start = store is None
        if store is None:
            from ..store import SessionStore

            store = SessionStore(self.state)
        self.store = store
        self.feed = storefeed.StoreFeed(store, self.state)
        if start:
            store.start()

    # -- clients

    def client_connected(self, client: Client) -> None:
        self._clients.add(id(client))

    def client_gone(self, client: Client) -> None:
        """A client went away however it went: nothing changes on the
        service but "no client is attached" (§3.1 rule 5)."""
        self._clients.discard(id(client))
        if self.feed is not None:
            self.feed.unsubscribe(client)
        for pty_id in list(self.ptys.ptys):
            self.ptys.detach(pty_id, client.sink_for(pty_id))

    # -- requests

    def handle(self, message: protocol.Message, client: Client) -> dict:
        """Answer a validated request: the reply dict (`protocol.reply`) or a
        refusal (`protocol.refuse`)."""
        handler = getattr(self, "_req_" + message.type.replace(".", "_").replace("-", "_"), None)
        if handler is None:
            return protocol.refuse(
                message.id, protocol.ERROR_UNKNOWN, "{type}: not served here", {"type": message.type}
            )
        if message.type in _STORE_REQUESTS or message.type.startswith("store."):
            if self.feed is None:
                return protocol.refuse(
                    message.id, protocol.ERROR_UNKNOWN, "{type}: not served here", {"type": message.type}
                )
            return handler(message, client)
        try:
            return handler(message, client)
        except KeyError:
            return protocol.refuse(
                message.id, protocol.ERROR_GONE, "no such pty: {pty}", {"pty": message.get("pty")}
            )

    def deliver(self, event: protocol.Message, client: Client) -> None:
        """A validated client event (`resize`, `focus`, `theme`)."""
        handler = getattr(self, "_ev_" + event.type.replace(".", "_"), None)
        if handler is None:
            return
        try:
            handler(event, client)
        except KeyError:
            pass

    def _req_spawn(self, message: protocol.Message, client: Client) -> dict:
        shell = os.environ.get("SHELL") or "/bin/bash"
        kind = message.get("kind")
        cwd = message.get("cwd")
        env = dict(self._environment())
        cols = message.get("cols") or ptyserver.termscreen.DEFAULT_COLS
        rows = message.get("rows") or ptyserver.termscreen.DEFAULT_ROWS
        argv = [shell]
        box = plan = None
        if kind == "shell":
            # A panel shell is the user's own: no progress declarations,
            # which the VTE path never gave one either.
            progress = False
            if message.get("sandbox"):
                box = message.get("sandbox_box") or ""
                plan = self._sandbox_plan(box) if box else None
                if not plan:
                    # No box to run in: nothing is spawned rather than a
                    # shell that would run unconfined under that title.
                    return protocol.refuse(
                        message.id,
                        protocol.ERROR_REFUSED,
                        "no sandbox plan for this session — the shell was not started",
                    )
                argv = providers.sandboxed_shell_argv(plan, shell)
        else:
            # The two declarations that coax the CLI's progress announcements
            # out (session.agent_environment says why), unless the
            # experimental setting is off: decided at spawn, as the VTE path
            # decided it.
            progress = self._get_setting("progress_termprop") in (None, True)
        try:
            pty_id = self.ptys.spawn(
                kind,
                argv,
                cwd,
                env,
                cols,
                rows,
                session=message.get("session"),
                box=box,
                plan=plan,
                progress=progress,
            )
        except ptyserver.SpawnError as exc:
            return protocol.refuse(
                message.id,
                protocol.ERROR_FAILED,
                "failed to start shell: {msg}",
                {"msg": f"{exc.strerror}: {exc.filename}"},
            )
        except (OSError, ValueError) as exc:
            return protocol.refuse(
                message.id, protocol.ERROR_FAILED, "failed to start shell: {msg}", {"msg": str(exc)}
            )
        if plan:
            self._enter_cwd_in_box(pty_id, plan, cwd)
        pty = self.ptys.get(pty_id)
        return protocol.reply(message.id, pty=pty_id, cols=pty.cols, rows=pty.rows)

    def _enter_cwd_in_box(self, pty_id: int, plan: str, cwd: str) -> None:
        """A sandboxed shell starts where bwrap's ``--chdir`` lands it (the
        box's workspace, or the checkout of a launch narrowed to its
        worktree); a *cwd* inside the workspace other than that is one
        ``cd`` away, queued for the shell to read the moment it is up,
        behind the line reset everything typed into a shell gets."""
        loaded = sandboxplan.load_plan(plan)
        if not loaded:
            return
        workspace, start = loaded["workspace"], sandboxplan.plan_start_dir(loaded)
        if workspace and cwd != start and _within(workspace, cwd):
            self.ptys.write(pty_id, shell_command(f"cd {shlex.quote(cwd)}\n").encode())

    def _req_attach(self, message: protocol.Message, client: Client) -> dict:
        pty_id = message.get("pty")
        answer = self.ptys.attach(pty_id, client.sink_for(pty_id), message.get("cols"), message.get("rows"))
        return protocol.reply(message.id, **answer)

    def _req_detach(self, message: protocol.Message, client: Client) -> dict:
        pty_id = message.get("pty")
        self.ptys.get(pty_id)
        self.ptys.detach(pty_id, client.sink_for(pty_id))
        client.forget(pty_id)
        return protocol.reply(message.id)

    def _req_paint(self, message: protocol.Message, client: Client) -> dict:
        self.ptys.paint(message.get("pty"), message.get("text"))
        return protocol.reply(message.id)

    def _req_close(self, message: protocol.Message, client: Client) -> dict:
        self.ptys.close(message.get("pty"))
        return protocol.reply(message.id)

    def _req_clear(self, message: protocol.Message, client: Client) -> dict:
        """A shell's *Clear*: its screen and scrollback wiped from the model
        (the client attaches again to be redrawn from it). An agent's
        ``clear`` is the composer's erase of the CLI's box, PR-1.10's."""
        pty_id = message.get("pty")
        if self.ptys.get(pty_id).kind != "shell":
            # Refused, not unknown: the type is served, an agent's case is
            # not yet, and a client can tell the two apart.
            return protocol.refuse(message.id, protocol.ERROR_REFUSED, "clear of an agent's box is PR-1.10's")
        self.ptys.clear(pty_id)
        return protocol.reply(message.id)

    # -- the store and the state (PR-1.10)

    def _req_subscribe(self, message: protocol.Message, client: Client) -> dict:
        items = self.feed.subscribe(client, client.deliver)
        ptys = 0
        for pty_id, pty in list(self.ptys.ptys.items()):
            event = {
                "t": "pty",
                "pty": pty_id,
                "kind": pty.kind or "agent",
                "cols": pty.cols,
                "rows": pty.rows,
            }
            if pty.cwd:
                event["cwd"] = pty.cwd
            if pty.session:
                event["session"] = pty.session
            pid = pty.child_pid()
            if pid:
                event["pid"] = pid
            client.deliver(event)
            ptys += 1
        return protocol.reply(message.id, items=items, ptys=ptys)

    def _req_state_get(self, message: protocol.Message, client: Client) -> dict:
        name, entry = message.get("key"), message.get("entry")
        key = SHARED_KEYS.get(name)
        if key is None:
            return protocol.refuse(message.id, protocol.ERROR_GONE, "No such state key: {key}", {"key": name})
        if name == "settings" and entry is not None and self.state.is_device_setting(entry):
            return protocol.refuse(
                message.id,
                protocol.ERROR_REFUSED,
                "{setting} is a setting of this device, not of the service",
                {"setting": entry},
            )
        value = self.state.export_key(name)
        if entry is not None:
            if key.form != MAP:
                return protocol.refuse(
                    message.id, protocol.ERROR_INVALID, "{key} has no entries", {"key": name}
                )
            value = value.get(entry)
        return protocol.reply(message.id, value=value)

    def _req_state_set(self, message: protocol.Message, client: Client) -> dict:
        """A client's write of a shared key; the module docstring says
        which the service takes. Saved (and so published) only when the
        value moved."""
        name, entry, value = message.get("key"), message.get("entry"), message.get("value")
        key = SHARED_KEYS.get(name)
        if key is None:
            return protocol.refuse(message.id, protocol.ERROR_GONE, "No such state key: {key}", {"key": name})
        if not key.writable:
            return protocol.refuse(
                message.id, protocol.ERROR_REFUSED, "{key} is written by the service alone", {"key": name}
            )
        state = self.state
        before = state.export_key(name)
        if entry is None:
            if name == "settings":
                return protocol.refuse(
                    message.id, protocol.ERROR_REFUSED, "Settings are written one at a time", {}
                )
            shape = dict if key.form == MAP else list
            if key.form != SCALAR and not isinstance(value, shape):
                return protocol.refuse(
                    message.id, protocol.ERROR_REFUSED, "{key} does not take that value", {"key": name}
                )
            state.import_key(name, value)
        else:
            if key.form != MAP:
                return protocol.refuse(
                    message.id, protocol.ERROR_INVALID, "{key} has no entries", {"key": name}
                )
            if name == "settings" and state.is_device_setting(entry):
                return protocol.refuse(
                    message.id,
                    protocol.ERROR_REFUSED,
                    "{setting} is a setting of this device, not of the service",
                    {"setting": entry},
                )
            if not state.import_entry(name, entry, value):
                if name == "settings":
                    return protocol.refuse(
                        message.id,
                        protocol.ERROR_REFUSED,
                        "{setting} does not take that value",
                        {"setting": entry},
                    )
                return protocol.refuse(
                    message.id, protocol.ERROR_REFUSED, "{key} does not take that value", {"key": name}
                )
        if state.export_key(name) != before:
            state.save()
            if name == "settings":
                self.store.apply_pr_titles()
                self.store.apply_cli_titles()
        return protocol.reply(message.id)

    def _req_store_lookup(self, message: protocol.Message, client: Client) -> dict:
        return protocol.reply(message.id, found=self.feed.send_one(client, message.get("session")))

    def _req_store_page_archived(self, message: protocol.Message, client: Client) -> dict:
        return protocol.reply(message.id, items=self.feed.page(client))

    def _req_store_refresh(self, message: protocol.Message, client: Client) -> dict:
        self.store.refresh(force_rebuild=bool(message.get("force")))
        return protocol.reply(message.id)

    def _req_store_show_archived(self, message: protocol.Message, client: Client) -> dict:
        self.store.set_show_archived(bool(message.get("show")))
        return protocol.reply(message.id)

    def _req_store_rename(self, message: protocol.Message, client: Client) -> dict:
        self.store.rename(message.get("session"), message.get("name"))
        return protocol.reply(message.id)

    def _req_store_regenerate_name(self, message: protocol.Message, client: Client) -> dict:
        self.store.regenerate_name(message.get("session"))
        return protocol.reply(message.id)

    def _req_store_favorite(self, message: protocol.Message, client: Client) -> dict:
        self.store.set_favorites(list(message.get("sessions")), bool(message.get("favorite")))
        return protocol.reply(message.id)

    def _req_store_archive(self, message: protocol.Message, client: Client) -> dict:
        sessions = list(message.get("sessions"))
        if message.get("archived"):
            self.store.archive_many(sessions)
        else:
            self.store.restore_many(sessions)
        return protocol.reply(message.id)

    def _req_store_archive_project(self, message: protocol.Message, client: Client) -> dict:
        self.store.set_project_archived(message.get("project"), bool(message.get("archived")))
        return protocol.reply(message.id)

    def _req_store_trash(self, message: protocol.Message, client: Client) -> dict:
        errors = self.store.trash_many(list(message.get("sessions")))
        return protocol.reply(
            message.id,
            errors={sid: str(error)[: protocol.ARG_TEXT_MAX] for sid, error in errors.items()},
        )

    def _req_store_delete(self, message: protocol.Message, client: Client) -> dict:
        error = self.store.delete(message.get("session"))
        if error:
            return protocol.reply(message.id, error=str(error)[: protocol.ARG_TEXT_MAX])
        return protocol.reply(message.id)

    def _req_store_forward(self, message: protocol.Message, client: Client) -> dict:
        self.store.record_forward(message.get("session"), message.get("to"))
        return protocol.reply(message.id)

    def _req_store_add_project(self, message: protocol.Message, client: Client) -> dict:
        self.store.add_project(message.get("cwd"))
        return protocol.reply(message.id)

    def _req_store_keep_projects(self, message: protocol.Message, client: Client) -> dict:
        self.store.keep_projects(list(message.get("projects")))
        return protocol.reply(message.id)

    def _req_store_forget_project(self, message: protocol.Message, client: Client) -> dict:
        self.store.forget_project(message.get("project"))
        return protocol.reply(message.id)

    def _req_store_move_project(self, message: protocol.Message, client: Client) -> dict:
        self.store.move_project(message.get("project"), message.get("before"))
        return protocol.reply(message.id)

    def _req_store_flags(self, message: protocol.Message, client: Client) -> dict:
        """The tracker's verdicts (see the module docstring), set on the
        service's items; each lands back as an `item` field. A session with
        no row is a silent no-op, as the store's setters are: the reply is
        ok and no event follows (the client sends none for a row it lacks)."""
        session_id = message.get("session")
        store = self.store
        if (status := message.get("status")) is not None:
            store.set_status(session_id, status)
        if (busy := message.get("busy")) is not None:
            store.set_busy(session_id, busy)
        if (unread := message.get("unread")) is not None:
            store.set_unread(session_id, unread)
        if (backgrounding := message.get("backgrounding")) is not None:
            store.set_backgrounding(session_id, backgrounding)
        if (can_background := message.get("can_background")) is not None:
            store.set_can_background(session_id, can_background)
        return protocol.reply(message.id)

    def _req_trust_check(self, message: protocol.Message, client: Client) -> dict:
        root = trust.trust_root(message.get("path"))
        fields: dict = {"trusted": trust.is_trusted(root)}
        if root.startswith("/"):
            fields["root"] = root
        return protocol.reply(message.id, **fields)

    def _req_trust_grant(self, message: protocol.Message, client: Client) -> dict:
        path = message.get("path")
        if message.get("scope") == "launch":
            written = trust.trust_launch_dir(path)
        else:
            written = trust.trust_dir(trust.trust_root(path))
        return protocol.reply(message.id, written=bool(written))

    # -- client events

    def _ev_resize(self, event: protocol.Message, client: Client) -> None:
        pty_id = event.get("pty")
        self.ptys.resize(pty_id, event.get("cols"), event.get("rows"), sink=client.sink_for(pty_id))

    def _ev_focus(self, event: protocol.Message, client: Client) -> None:
        pty_id = event.get("pty")
        self.ptys.focus(pty_id, client.sink_for(pty_id), bool(event.get("focused")))

    def _ev_theme(self, event: protocol.Message, client: Client) -> None:
        """A client's terminal: its own (its sinks answer `term` from it),
        so a pty's queries are answered with its active client's colours
        (§3.3), the last one seen standing in while none is active."""
        term = dict(event.get("term") or {})
        client.term = term
        self.ptys.set_term(term)

    # -- input

    def input(self, pty_id: int, data: bytes, client: Client) -> None:
        """A `0x02` frame: what the client's VTE committed."""
        try:
            self.ptys.write(pty_id, data, sink=client.sink_for(pty_id))
        except KeyError:
            pass

    # -- the loopback's shortcuts (deleted with it, D21)

    def screen_of(self, pty_id: int) -> termstream.ScreenHook:
        """The pty's screen model: what a client-side `Session` reads through
        its `ScreenPort` while it still runs in the client's process
        (PR-1.7 to PR-1.9). From PR-1.10 the session reads it on the
        service, and this goes with the loopback."""
        return self.ptys.get(pty_id).screen

    def pty_of(self, pty_id: int) -> ptyserver.Pty:
        """The pty object, for the client-side `Session`'s `PtyPort`
        (`child_pid`, `foreground_pgrp`: service facts the socket will
        carry as `pty` event fields and a request). Same lifetime as
        `screen_of`."""
        return self.ptys.get(pty_id)

    def write_panel_history(self, key: str, shells: dict[int, int | str]) -> None:
        """Write a session's panel history (spec §3.15): each shell under
        its ordinal, from its pty's live model (`PtyServer.capture`; a pty
        already gone writes nothing, its file cleared) when *shells* names
        a pty id, else the text given (a shell with no pty: what its widget
        shows).
        The mapping is the keep-set: files under ordinals it does not name
        are dropped, as `panelhistory.save_all` does; the path and the cap
        are panelhistory's. In Phase 1 the tab decides the key (the session
        id, or the draft id of a new-chat screen) and the moment (a draft
        save, the tab's close, the app's quit, each before its shells end)
        and asks through the loopback (a shortcut, D21); PR-1.10 moves
        those decisions into the service with the session."""
        texts: dict[int, str] = {}
        for ordinal, source in shells.items():
            if isinstance(source, str):
                texts[ordinal] = source
            else:
                texts[ordinal] = self.ptys.capture(int(source))
        panelhistory.save_all(key, texts)

    def shutdown(self) -> None:
        self.ptys.shutdown()


def _within(root: str, path: str) -> bool:
    """Whether *path* is *root* itself or something under it. Purely lexical."""
    root, path = os.path.normpath(root), os.path.normpath(path)
    return path == root or path.startswith(root + os.sep)
