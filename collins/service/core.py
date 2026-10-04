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
trust (`trust.*`). PR-1.11 adds the rest, each in a module of its own
the core routes to: the PR hub's events and every gh call
(`prfeed`), the notification history (`notifications`), the session
tools and the MCP socket (`tools`, `start_tools`, `start_mcp`; a
UI-bound call is a `tool` event to the session's active client,
`_tool_client`, answered by `tool-reply`), the Sandboxed chip's
requests (`sandbox`, `start_sandbox`), the diffs' marks (`diffs`),
token use (`tokenuse`) and the jobs (`jobs`). GLib only; nothing here
imports GTK.

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
the box, which is the `Session`'s and so still the client's through Phase
1 (the session moves into the service in PR-1.12): the core refuses it
rather than erase a box it cannot read. The panel history is written here
from the models: by the tab's three saves (`write_panel_history`), and
(PR-1.11, §3.15) by the core itself when a shell's child exits, from its
model before the model is dropped, under the key and ordinal the shell was
spawned with (`spawn`'s ``history`` and ``ordinal``) or re-filed under
since (`panel.key`: the resolver bound the tab; none once the shell's page
closed for good, so a closed shell's history goes with it). The cwd,
foreground and inner-shell reads a panel shell asks of its pty are the pty
server's (`Pty.process_cwd`, `Pty.has_running_command`, `Pty.shell_pid`).

**Jobs** (PR-1.11): `job.start` and `job.cancel` are `jobs.JobRunner`'s,
whose `job` events go to the client that started each one.
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
from . import diffs as diffs_mod
from . import jobs, prfeed, ptyserver, storefeed, termstream, tokenuse
from . import notifications as notifications_mod
from . import sandbox as sandbox_mod
from . import tools as tools_mod

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
        self.notifications: notifications_mod.ServiceNotifications | None = None
        self.prs: prfeed.PrFeed | None = None
        self.diffs: diffs_mod.DiffNotes | None = None
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
            on_exit=self._on_pty_exit,
        )
        self.jobs = jobs.JobRunner()
        self.tools: tools_mod.SessionTools | None = None
        self.sandbox = None  # service.sandbox.SandboxRequests, once start_sandbox ran
        self._clients: set[int] = set()  # id(client)
        # The subscribed clients, in the order they subscribed: who a
        # UI-bound tool call can go to (_tool_client).
        self._subscribers: list[Client] = []
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
        # The notification history (PR-1.11): the service's, over the same
        # state, published to the same subscribers.
        self.notifications = notifications_mod.ServiceNotifications(self.state)
        # The PR hub is the store's own; its events go to the same
        # subscribers (PR-1.11).
        self.prs = prfeed.PrFeed(store.pr_store)
        # The marks on each session's diff (PR-1.11, service.diffs).
        self.diffs = diffs_mod.DiffNotes(self.state, self._broadcast, self._get_setting)
        if start:
            store.start()

    # -- clients

    def client_connected(self, client: Client) -> None:
        self._clients.add(id(client))

    def client_gone(self, client: Client) -> None:
        """A client went away however it went: nothing changes on the
        service but "no client is attached" (§3.1 rule 5)."""
        self._clients.discard(id(client))
        self.jobs.forget(client.deliver)
        self._subscribers = [c for c in self._subscribers if c is not client]
        if self.tools is not None:
            self.tools.client_gone(client)
        if self.feed is not None:
            self.feed.unsubscribe(client)
        if self.notifications is not None:
            self.notifications.unsubscribe(client)
        if self.prs is not None:
            self.prs.unsubscribe(client)
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
        handler = getattr(self, "_ev_" + event.type.replace(".", "_").replace("-", "_"), None)
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
                history=message.get("history") if kind == "shell" else None,
                ordinal=int(message.get("ordinal") or 0),
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
        ``clear`` is the composer's erase of the CLI's box: the `Session`'s,
        which runs in the client through Phase 1 (see the module
        docstring)."""
        pty_id = message.get("pty")
        if self.ptys.get(pty_id).kind != "shell":
            # Refused, not unknown: the type is served, an agent's case is
            # not here yet, and a client can tell the two apart.
            return protocol.refuse(
                message.id,
                protocol.ERROR_REFUSED,
                "An agent's box is erased by its session, which runs in the client"
                " until the service holds it",
            )
        self.ptys.clear(pty_id)
        return protocol.reply(message.id)

    def _req_panel_key(self, message: protocol.Message, client: Client) -> dict:
        """File a shell's history under a new key (the resolver bound its
        tab to a session id), or under none (its page closed for good)."""
        pty = self.ptys.get(message.get("pty"))
        if pty.kind != "shell":
            return protocol.refuse(message.id, protocol.ERROR_REFUSED, "Only a shell has a panel history")
        pty.history = message.get("history")
        return protocol.reply(message.id)

    def _on_pty_exit(self, pty: ptyserver.Pty) -> None:
        """A shell's child exited: its history, from its model while the
        model is still whole (§3.15). A shell filed under no key (a fork's,
        a page closed for good, a tab with no session yet) writes nothing."""
        if pty.kind != "shell" or not pty.history:
            return
        panelhistory.save(pty.history, pty.screen.capture_contents(), pty.ordinal)

    # -- jobs (PR-1.11)

    def _req_job_start(self, message: protocol.Message, client: Client) -> dict:
        job_id = self.jobs.start(message.get("kind"), message.get("args") or {}, client.deliver)
        return protocol.reply(message.id, job=job_id)

    # -- notifications (PR-1.11; service.notifications)

    def _notify(self, message: protocol.Message, client: Client) -> dict:
        if self.notifications is None:
            return protocol.refuse(
                message.id, protocol.ERROR_UNKNOWN, "{type}: not served here", {"type": message.type}
            )
        return self.notifications.handle(message, client)

    _req_notify_post = _notify
    _req_notify_remove = _notify
    _req_notify_clear = _notify
    _req_notify_green = _notify
    _req_notify_rekey = _notify
    _req_seen = _notify

    # -- pull requests (PR-1.11; service.prfeed)

    def _req_pr_set(self, message: protocol.Message, client: Client) -> dict:
        if self.prs is None:
            return protocol.refuse(
                message.id, protocol.ERROR_UNKNOWN, "{type}: not served here", {"type": message.type}
            )
        return self.prs.set_records(message)

    def _gh(self, message: protocol.Message, client: Client) -> dict:
        return prfeed.handle_gh(message)

    _req_pr_fetch = _gh
    _req_pr_sweep = _gh
    _req_pr_detail = _gh
    _req_pr_threads = _gh
    _req_pr_blob = _gh
    _req_pr_action = _gh
    _req_pr_comment = _gh
    _req_pr_review = _gh
    _req_pr_thread = _gh

    # -- the diffs' marks (PR-1.11; service.diffs)

    def _req_diff_set_notes(self, message: protocol.Message, client: Client) -> dict:
        if self.diffs is None:
            return protocol.refuse(
                message.id, protocol.ERROR_UNKNOWN, "{type}: not served here", {"type": message.type}
            )
        return self.diffs.set_notes(message)

    # -- token use (PR-1.11; service.tokenuse)

    def _req_usage_get(self, message: protocol.Message, client: Client) -> dict:
        return tokenuse.usage_get(message)

    def _req_models_get(self, message: protocol.Message, client: Client) -> dict:
        return tokenuse.models_get(message)

    def _req_models_defaults(self, message: protocol.Message, client: Client) -> dict:
        return tokenuse.models_defaults(message)

    def _req_icon_save(self, message: protocol.Message, client: Client) -> dict:
        return tokenuse.icon_save(message)

    def _req_job_cancel(self, message: protocol.Message, client: Client) -> dict:
        if not self.jobs.cancel(message.get("job")):
            return protocol.refuse(
                message.id, protocol.ERROR_GONE, "No such job: {job}", {"job": message.get("job")}
            )
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
        if self.notifications is not None:
            self.notifications.subscribe(client, client.deliver)
        if self.prs is not None:
            self.prs.subscribe(client, client.deliver)
        if self.diffs is not None:
            self.diffs.subscribe(client.deliver)
        if all(c is not client for c in self._subscribers):
            self._subscribers.append(client)
        if self.tools is not None:
            # A show_diff asked for while nobody was attached opens now.
            self.tools.apply_pending_diffs(client)
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

    # -- the session tools (PR-1.11, service.tools)

    def start_tools(self, sessions, sandbox_host=lambda: None, notifications=None, diffs=None):
        """The session tools' dispatcher over the core's records: *sessions*
        lists the `service.session.Session`s the service holds (through
        Phase 1 the tabs' own; the app's lookup), *sandbox_host* the
        SandboxHost. Returns it: what the MCP socket service dispatches to."""
        self.tools = tools_mod.SessionTools(
            get_setting=self._get_setting,
            sessions=sessions,
            sandbox_host=sandbox_host,
            send_tool=self._send_tool,
            store=self.store,
            state=self.state,
            ptys=self.ptys,
            notifications=notifications or self.notifications,
            diffs=diffs or self.diffs,
            shell_spawner=self._spawn_tool_shell,
        )
        return self.tools

    def start_mcp(self, app_id: str) -> str | None:
        """The socket every launched session's MCP shim relays its calls
        through (`mcpserver.SessionToolService`, dispatching to the tools of
        `start_tools`) and the `--mcp-config` file naming it. The path of
        that file, or None when either could not be made (logged): the
        tools are conveniences, never load-bearing."""
        from .. import mcpserver, mcptools

        self.mcp_service = None
        if self.tools is None:
            return None
        service = mcpserver.SessionToolService(
            mcptools.socket_path(app_id),
            list_tools=self.tools.list_tools,
            dispatch=self.tools.dispatch,
        )
        try:
            service.start()
        except Exception:  # GLib.Error, OSError
            log.exception("session MCP socket unavailable")
            return None
        config = mcptools.write_config(app_id)
        if config is None:
            log.error("session MCP config not writable")
            service.stop()
            return None
        self.mcp_service = service
        return config

    def stop_mcp(self) -> None:
        """Stop accepting and unlink the socket; the config file stays
        behind on purpose: the app-id-keyed path is stable across restarts,
        so a session that outlives this run reconnects to the next one, and
        until then its shim degrades to clean "Collins is not running"
        errors rather than breaking the session."""
        service = getattr(self, "mcp_service", None)
        if service is not None:
            service.stop()
            self.mcp_service = None

    def start_sandbox(self, host, grants, sessions) -> None:
        """The Sandboxed chip's requests (service.sandbox): *host* and
        *grants* answer the SandboxHost and the GrantMounts, *sessions* the
        service's sessions; the plan of a box is the core's own lookup."""
        self.sandbox = sandbox_mod.SandboxRequests(
            host=host,
            grants=grants,
            plan_of=self._sandbox_plan,
            sessions=sessions,
            broadcast=self._broadcast,
        )

    def _broadcast(self, event: dict) -> None:
        """An event for every subscribed client."""
        for client in list(self._subscribers):
            try:
                client.deliver(dict(event))
            except Exception:
                log.exception("a client's delivery failed")

    def _req_sandbox(self, message: protocol.Message, client: Client) -> dict:
        if getattr(self, "sandbox", None) is None:
            return protocol.refuse(
                message.id, protocol.ERROR_UNKNOWN, "{type}: not served here", {"type": message.type}
            )
        return self.sandbox.handle(message)

    _req_sandbox_plan = _req_sandbox
    _req_sandbox_grants = _req_sandbox
    _req_sandbox_allow = _req_sandbox
    _req_sandbox_revoke = _req_sandbox
    _req_sandbox_tools = _req_sandbox
    _req_sandbox_restart = _req_sandbox

    def _tool_client(self, session) -> Client | None:
        """The session's active client (D20): of the subscribed clients,
        the one whose device is the active client of the session's agent
        pty, else the one that subscribed last. Through Phase 1 there is
        one subscriber, the app's own connection."""
        if not self._subscribers:
            return None
        device = None
        for pty in self.ptys.ptys.values():
            if pty.kind != "shell" and session.session_id and pty.session == session.session_id:
                device = pty.sized_for() or None
                break
        if device:
            for client in reversed(self._subscribers):
                if client.device == device:
                    return client
        return self._subscribers[-1]

    def _send_tool(self, session, event: dict) -> Client | None:
        client = self._tool_client(session)
        if client is None:
            return None
        checked = protocol.validate(dict(event), protocol.SERVICE)
        if isinstance(checked, protocol.Refusal):
            log.error("tools: a tool event the protocol refuses: %s", checked.msgid)
            return None
        client.deliver(event)
        return client

    def _ev_tool_reply(self, event: protocol.Message, client: Client) -> None:
        if self.tools is not None:
            self.tools.reply(event.get("call"), event.get("ok"), event.get("text"), client)

    def _spawn_tool_shell(self, session, sandboxed: bool) -> int | None:
        """A panel shell for run_in_terminal with no client attached: the
        user's shell in the session's directory, filed under its history; a
        sandboxed session's in its own box, on the plan its records hold."""
        shell = os.environ.get("SHELL") or "/bin/bash"
        cwd = session.current_agent_cwd() or session.cwd
        if not cwd:
            return None
        argv, box, plan = [shell], None, None
        if sandboxed:
            box = session.sandbox_box
            plan = self._sandbox_plan(box) if box else None
            if not plan:
                return None
            argv = providers.sandboxed_shell_argv(plan, shell)
        try:
            pty_id = self.ptys.spawn(
                "shell",
                argv,
                cwd,
                dict(self._environment()),
                session=session.session_id,
                box=box,
                plan=plan,
                progress=False,
                history=session.session_id,
            )
        except (OSError, ValueError):
            log.exception("tools: a shell for run_in_terminal failed to start")
            return None
        if plan:
            self._enter_cwd_in_box(pty_id, plan, cwd)
        return pty_id

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
