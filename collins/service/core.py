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

**The sessions are the service's** (§3.19, PR-1.12a). `sessions` holds a
`hosting.SessionRecord` per live agent pty, built by `spawn` for kind
``agent`` from the request's fields (`SessionOptions`' and the rest of
what `session.Session` takes) and dropped once the pty's exit has been
reported. The record is the session's host and ports; the facts a client
reads off its tab arrive as `session` events (whole on `attach`, changed
fields after); the requests a tab makes of its session (`prompt`, `write`,
`switch`, `send`, `cut`, `close`, ...) are routed to it by its pty. The
MCP identity walk and the Sandboxed chip's requests read `sessions`, and a
box's plan is the session's that runs in it (`hosting.sandbox_plan_of`).

**Busy and the finish verdict are the service's** (§3.6, D29). The
tracker (`tracking.ServiceActivity`, `start_activity`) is fed by the stream
filter's `Progress` events, every pty's output after the filter, the input
frames and the ``/proc`` poll, and sets `busy` and a counted finish's
`unread` on the store's items itself. `store.flags` keeps what a person did
at a client's screen — `status`, `unread` — and refuses `busy`.

**The background agents are the service's** (§3.22, PR-1.12d,
`bgagents.BackgroundAgents`, `start_background`): the agent list's poller
and the rows' yellow-line fact (`background`), the /bg handoff a session's
``close {mode: background}`` starts (the fork watch, the pending detach,
the rows' `backgrounding`), the gate (`can_background`), the replay at
start, the repair (the ``session.repair`` job) and the busy feed into the
tracker. `store.flags` refuses `backgrounding` and `can_background`. The
rest of what the window did with the service's files moved with them: a
transcript that moved under a session on worktree entry is followed here
(`sync_transcript_paths`, on every refresh of the store), the archive's
worktree ask reads the transcript here (the ``worktree.check`` job), a
forgotten transcript's files and records go here (`store.forget`), the
archive sweep runs on the service's own timer (`start_housekeeping`, over
`autodelete`), a chat's throwaway folder is made again before a spawn in
it, and `service.status` says whether the service's `gh` is ready.

**The sandbox host is the service's** (`start_sandbox_host`): the
`sandboxplan.SandboxHost` over the service's state, the live grants, the
sweeps and the probe, whose verdict reaches clients as a `sandbox` event
(``what: "probe"``).

**The probe** (D27). With ``COLLINS_DEBUG_API=1`` in the service's
environment the ``debug.*`` requests are served — a session attribute read,
written or called by name, a pty's screen and process facts, a call on the
sandbox host or the live grants — so the e2e checks reach past the protocol
through one door; a service started otherwise refuses them as `unknown`.

A client is anything the transport represents as a `Client`: it has a
`device` name, a sink per pty (`sink_for(pty)`: the object the pty server
hands output and events to), `forget(pty)` for when a pty is gone, and
`deliver(event)` for the store and state events of its subscription. The
socket server (`api.server.SocketClient`, PR-1.12b) is the transport, and
the unit suite's `tests/inproc.py` stands in for it; both give the core
the same dicts, already validated by `api.protocol`, and get the same
replies back.

Spawning is the core's: a client asks for an agent's or a shell's pty in a
directory at a grid, and the core runs the user's `$SHELL` there with the
service's own environment (the login-shell capture, `service.main`) plus the two declarations
that coax the CLI's progress announcements out (`session.agent_environment`,
decided by the `progress_termprop` setting at spawn, as the VTE path did).
For an agent the `Session` is built first and `Session.spawn` settles the
directory, the sandbox plan and the command; its `spawn_shell` is the pty
server's spawn (`spawn_agent_pty`) and `shell_spawned` types the command,
the `sandboxrun.py` wrapper included. A `SpawnError` is refused with the
child's errno in the message, so the tab shows it where it used to show
VTE's spawn error.

`close` with mode ``exit`` or ``background`` and the provider's keystrokes
is the session's graceful close (`Session.begin_close`: the keystrokes,
the nudges, the worktree dialog, the shell's exit, the budgets; a budget
running out is a `close` event the window answers); ``kill``, or `force`,
is the pty's child sent SIGHUP and the master closed (`PtyServer.close`),
with SIGKILL after the grace.

Panel shells (PR-1.8, spec §3.15) are ptys of kind ``shell``: the user's
`$SHELL` with the service's environment and none of the agent's progress
declarations (a panel shell never had them). A sandboxed one (``sandbox``
with the session's ``sandbox_box``) runs `providers.sandboxed_shell_argv`
around the shell, the launcher a sandboxed session's typed line starts
with, on the plan the service's own records hold for that box (the
*sandbox_plan* lookup: the `Session` the box belongs to), and is refused when there is none;
the plan is the pty's
(its row's ``plan``, read back as the shell's `sandbox_plan`), and a cwd
inside the box's workspace other than where bwrap lands the shell is one
queued ``cd`` away, typed before anything else. ``clear`` on a shell wipes
its model (`PtyServer.clear`); on an agent it is the composer's erase of
the box (the provider's clear keys for what the box holds). The panel history is written here
from the models: by the tab's three saves (`write_panel_history`), and
(PR-1.11, §3.15) by the core itself when a shell's child exits, from its
model before the model is dropped, under the key and ordinal the shell was
spawned with (`spawn`'s ``history`` and ``ordinal``) or re-filed under
since (`panel.key`, or `rekey_shells` when the session its ``handle``
names resolves; none once the shell's page closed for good, so a closed
shell's history goes with it). The cwd,
foreground and inner-shell reads a panel shell asks of its pty are the pty
server's (`Pty.process_cwd`, `Pty.has_running_command`, `Pty.shell_pid`).

**Jobs** (PR-1.11): `job.start` and `job.cancel` are `jobs.JobRunner`'s,
whose `job` events go to the client that started each one.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from gi.repository import GLib

from .. import autodelete, chats, panelhistory, providers, sandboxgrants, sandboxplan, sessions, trust
from ..api import protocol
from ..shellinput import shell_command
from ..state import MAP, SCALAR, SHARED_KEYS
from . import bgagents, gitfeed, hosting, jobs, prfeed, ptyserver, storefeed, termstream, tokenuse, tracking
from . import diffs as diffs_mod
from . import files as files_mod
from . import notifications as notifications_mod
from . import sandbox as sandbox_mod
from . import tools as tools_mod
from .session import PROMPT_BLOCK_MSGID

log = logging.getLogger(__name__)

# The requests of the store and state half, besides every ``store.*`` type:
# refused as `unknown` by a core with no store. ``trust.*`` is not gated: the
# CLI's folder trust is a file of the service's machine, not the store's.
_STORE_REQUESTS = frozenset({"subscribe", "state.get", "state.set"})

# What a spawned shell never inherits from the service: the e2e probe's
# flag and stubs (D27: a child cannot tell it runs under a probing
# service), and systemd's per-invocation variables.
SPAWN_ENV_STRIPPED = (
    "COLLINS_DEBUG_API",
    "COLLINS_E2E_STUBS",
    "COLLINS_E2E_STUBS_DATA",
    "NOTIFY_SOCKET",
    "INVOCATION_ID",
    "JOURNAL_STREAM",
)
ALREADY_RUNNING_MSGID = protocol.ALREADY_RUNNING_MSGID
# How long stop_sessions waits for every close flow (the agent's exit
# budget, the shell's, the SIGKILL grace) before shutting what is left.
STOP_BOUND_S = 30.0

# How often the archive sweep asks autodelete whether a day has passed
# (the hour the client's update check rode before PR-1.12d).
SWEEP_POLL_S = 3600


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
        # The sessions (§3.19): one record per live agent pty, by pty id.
        self.sessions: dict[int, hosting.SessionRecord] = {}
        self._sandbox_plan = sandbox_plan or (lambda box: hosting.sandbox_plan_of(self._records, box))
        self._environment = environment or (lambda: dict(os.environ))
        self._stream_listener = on_stream_event
        self.ptys = ptyserver.PtyServer(
            state_dir=state_dir,
            record=record,
            record_next_id=record_next_id,
            next_id=1 if next_id is None else next_id,
            on_event=self._on_stream_event,
            on_exit=self._on_pty_exit,
            on_output=self._on_pty_output,
        )
        self.jobs = jobs.JobRunner()
        # Git over the API (PR-2.1): every git.* request, the blob GET and
        # the watches, answered off the main loop.
        self.git = gitfeed.GitFeed(self)
        # A PR file's blob GET (PR-2.2, `kind=pr`): gh on a thread, no store.
        self.pr_blobs = prfeed.PrBlobs()
        # Files over the API (PR-2.3): the editor's reads, writes and
        # file watches, answered off the main loop.
        self.files = files_mod.Files(self)
        self.tools: tools_mod.SessionTools | None = None
        self.sandbox = None  # service.sandbox.SandboxRequests, once start_sandbox ran
        # The sandbox host and the live grants (start_sandbox_host), the
        # tracker (start_activity): None until started (a core that only
        # serves ptys, as a test's).
        self.sandbox_host: sandboxplan.SandboxHost | None = None
        self.sandbox_grants: sandboxgrants.GrantMounts | None = None
        self.activity: tracking.ServiceActivity | None = None
        # The background agents (start_background, PR-1.12d).
        self.background: bgagents.BackgroundAgents | None = None
        # The service's gh, as ghsetup last found it (None: not asked yet),
        # and the archive sweep's timer (start_housekeeping).
        self.gh_status: str | None = None
        self._gh_checking = False
        self._gh_wanted = 0  # bumped by each drop of the answer (check_gh)
        self._sweep_source = 0
        self.app_id = ""
        # The e2e probe (D27): served only when the service runs with the
        # flag in its environment.
        self.debug = os.environ.get("COLLINS_DEBUG_API") == "1"
        self._started = time.time()
        self._clients: set[int] = set()  # id(client)
        # The subscribed clients, in the order they subscribed: who a
        # UI-bound tool call can go to (_tool_client).
        self._subscribers: list[Client] = []
        # A restart waiting for no session to be busy (restart_when_idle):
        # what to call, and the poll's source.
        self._restart_waiting: Callable[[], None] | None = None
        self._restart_source = 0
        # Agent pty id -> whether its CLI runs (the facts' running_command,
        # hosting.SessionRecord._send_changed): a session counts as running
        # only while it does (cli_changed).
        self._cli_running: dict[int, bool] = {}
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
        self.feed = storefeed.StoreFeed(store, self.state, running=self.running_sessions)
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
        self.git.client_gone(client)
        self.files.client_gone(client)
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
            sink = client.sink_for(pty_id)
            record = self.sessions.get(pty_id)
            if record is not None:
                # A cut whose composer went with the client is called off;
                # nothing else about the session changes.
                record.client_gone(sink)
            self.ptys.detach(pty_id, sink)

    # -- requests

    def handle(self, message: protocol.Message, client: Client) -> dict:
        """Answer a validated request: the reply dict (`protocol.reply`) or a
        refusal (`protocol.refuse`)."""
        handler = getattr(self, "_req_" + message.type.replace(".", "_").replace("-", "_"), None)
        if handler is None or (message.type.startswith("debug.") and not self.debug):
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
        except Exception:
            # One request's failure is that request's (rule 4): the service
            # hosts every session, so a read of a CLI internal that moved
            # (the box, the transcript, the process tree) is logged and
            # refused, never raised through the router.
            log.exception("%s: the handler failed", message.type)
            return protocol.refuse(
                message.id, protocol.ERROR_FAILED, "{type}: failed on the service", {"type": message.type}
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
        if message.get("kind") != "shell":
            return self._spawn_agent(message, client)
        shell = os.environ.get("SHELL") or "/bin/bash"
        cwd = message.get("cwd")
        # A chat's throwaway folder may have been swept since (PR-1.12d: the
        # client never makes it).
        chats.ensure_chat_dir(cwd)
        env = self._spawn_env()
        cols = message.get("cols") or ptyserver.termscreen.DEFAULT_COLS
        rows = message.get("rows") or ptyserver.termscreen.DEFAULT_ROWS
        argv = [shell]
        box = plan = None
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
        try:
            # A panel shell is the user's own: no progress declarations,
            # which the VTE path never gave one either.
            pty_id = self.ptys.spawn(
                "shell",
                argv,
                cwd,
                env,
                cols,
                rows,
                session=message.get("session"),
                box=box,
                plan=plan,
                progress=False,
                history=message.get("history"),
                ordinal=int(message.get("ordinal") or 0),
            )
        except ptyserver.SpawnError as exc:
            return _spawn_refusal(message, exc)
        except (OSError, ValueError) as exc:
            return protocol.refuse(
                message.id, protocol.ERROR_FAILED, "failed to start shell: {msg}", {"msg": str(exc)}
            )
        pty = self.ptys.get(pty_id)
        pty.handle = message.get("handle")
        if plan:
            self._enter_cwd_in_box(pty_id, plan, cwd)
        return protocol.reply(message.id, pty=pty_id, cols=pty.cols, rows=pty.rows)

    # -- the sessions (§3.19)

    def _records(self) -> list[hosting.SessionRecord]:
        return list(self.sessions.values())

    def _sessions_list(self) -> list:
        """Every live session (`SessionTools`' and `SandboxRequests`' lookup)."""
        return [record.session for record in self.sessions.values()]

    def _record(self, message: protocol.Message) -> hosting.SessionRecord:
        """The session record the request's pty names (KeyError: gone)."""
        pty_id = message.get("pty")
        record = self.sessions.get(pty_id)
        if record is None:
            raise KeyError(pty_id)
        return record

    def session_for_box(self, box: str) -> hosting.SessionRecord | None:
        for record in self.sessions.values():
            if record.session.sandbox_box == box:
                return record
        return None

    def _spawn_agent(self, message: protocol.Message, client: Client) -> dict:
        """An agent's pty: the `Session` built from the request's fields,
        then `Session.spawn`, whose `spawn_shell` is `spawn_agent_pty`
        below; the reply is the pty and the session's handle, or the
        spawn's refusal."""
        provider = providers.get_provider(message.get("provider") or "claude")
        options = self._launch_options(message)
        if options is not None and message.get("sandbox") and not options.sandbox_plan:
            options = self._box_for_launch(message, options)
        wanted = message.get("session")
        # A fork starts its own conversation: no running pty of its origin
        # stands in for it, and none is closed for it (PR-1.12c review).
        if wanted and not message.get("fork"):
            for live in self._records():
                if live.pty_id not in self.ptys.ptys or live.session.session_id != wanted:
                    continue
                if live.session.fork:
                    # A fork's pty is no view of the session it forked
                    # from: neither attached to nor closed for it.
                    continue
                if live.session.has_running_command():
                    # Two CLIs on one transcript would both write it; the
                    # client (PR-1.12c) attaches to the running pty it names;
                    # a client that cannot paints it where a spawn error goes.
                    return protocol.refuse(
                        message.id, protocol.ERROR_REFUSED, ALREADY_RUNNING_MSGID, {"pty": live.pty_id}
                    )
                # The CLI left and only the shell sits on the pty (a quit
                # before 1.12c's attach): that pty is ended, its row and
                # history kept, and the resume takes a fresh one.
                log.info("spawn: session %s has a shell-only pty %d; closing it", wanted, live.pty_id)
                live.session.end_close()
                self.ptys.close(live.pty_id)
        record = hosting.SessionRecord(
            self,
            provider=provider,
            session_id=message.get("session"),
            fork=bool(message.get("fork")),
            options=options,
            command_override=message.get("command_override"),
            cwd=message.get("cwd"),
            jsonl_path=message.get("jsonl_path"),
            # The service's ProgressWatch reads the stream's Progress events,
            # so no VTE version is asked (§3.19); the declarations that coax
            # them out of the CLI follow the setting at spawn, as before.
            progress=True,
            progress_env=self._get_setting("progress_termprop") in (None, True),
        )
        record.grid = (
            message.get("cols") or ptyserver.termscreen.DEFAULT_COLS,
            message.get("rows") or ptyserver.termscreen.DEFAULT_ROWS,
        )
        session = record.session
        session.fork_resolve = bool(message.get("fork_resolve"))
        prompt = message.get("prompt")
        if prompt:
            session.hold_new_chat_prompt(prompt)
        session.set_transcript_path(message.get("jsonl_path"))
        # A chat's throwaway folder may have been swept or trashed since:
        # made again rather than the spawn falling back to $HOME (what the
        # window did before it opened the tab, PR-1.12d).
        chats.ensure_chat_dir(message.get("cwd"))
        session.spawn(message.get("cwd"), message.get("session"))
        if record.spawn_error is not None:
            return _spawn_refusal(message, record.spawn_error)
        if record.pty_id is None:
            return protocol.refuse(
                message.id, protocol.ERROR_FAILED, "failed to start shell: {msg}", {"msg": "no pty"}
            )
        fresh = message.get("session") is None
        if message.get("jsonl_path") is None and fresh:
            session.start_resolver(session.cwd or message.get("cwd"))
        elif session.fork_resolve:
            session.start_resolver(session.cwd or message.get("cwd"))
        if prompt:
            session.start_new_chat_prompt_poll()
        if self.activity is not None:
            self.activity.started(record, fresh=fresh)
        pty = self.ptys.get(record.pty_id)
        # Every subscriber's sidebar hears of the new agent pty (PR-1.12c).
        # The broadcast is queued before the reply is returned, but the
        # requester's client lands the reply first (a `call` returns before
        # the events the reply implies are drained, api.client), so its tab
        # knows its own pty by the time the table names it.
        self.publish_pty(record.pty_id)
        if self.background is not None:
            self.background.sessions_changed()
        return protocol.reply(
            message.id, pty=record.pty_id, cols=pty.cols, rows=pty.rows, handle=record.handle
        )

    @staticmethod
    def _launch_options(message: protocol.Message):
        """The `SessionOptions` a spawn request carries, None when it
        carries none of them (a plain resume)."""
        names = ("model", "effort", "permission_mode", "add_dirs", "worktree", "worktree_name",
                 "sandbox", "sandbox_box", "sandbox_plan")
        if all(message.get(name) is None for name in names):
            return None
        return providers.SessionOptions(
            model=message.get("model") or "",
            effort=message.get("effort") or "",
            permission_mode=message.get("permission_mode") or "",
            add_dirs=tuple(message.get("add_dirs") or ()),
            worktree=bool(message.get("worktree")),
            worktree_name=message.get("worktree_name") or "",
            sandbox=bool(message.get("sandbox")),
            sandbox_plan=message.get("sandbox_plan") or "",
            sandbox_box=message.get("sandbox_box") or "",
        )

    def _box_for_launch(self, message: protocol.Message, options):
        """The box a sandboxed resume or fork runs in (what MainWindow.
        open_session minted before the sessions were the service's): a
        fork gets a box of its own seeded with a copy of its origin's
        grants and tool switches, taken once; a resumed session with no
        box yet (recorded before the boxes, or its transcript back from the
        trash) gets one here, recorded against its id, starting with its
        project's defaults. A fresh session's box is the launch's own."""
        session_id, host, state = message.get("session"), self.sandbox_host, self.state
        if not session_id or host is None or state is None:
            return options
        cwd = message.get("cwd") or ""
        box = options.sandbox_box or state.sandbox_box(session_id)
        if message.get("fork"):
            origin = box
            box = host.mint_box(cwd, seed=False)
            grants = state.get_sandbox_grants(origin) if origin else []
            if grants:
                state.set_sandbox_grants(box, grants)
            host.copy_tools(origin, box)
        elif not box:
            box = host.mint_box(cwd)
            state.set_sandboxed(session_id, True, box=box)
        return providers.replace_options(options, sandbox_box=box)

    def _spawn_env(self) -> dict[str, str]:
        """The environment a shell spawns with: the service's own, less the
        e2e probe's flag (D27: a child never sees it, so a check's agent
        shell cannot tell it runs under a probing service)."""
        env = dict(self._environment())
        for name in SPAWN_ENV_STRIPPED:
            env.pop(name, None)
        return env

    def spawn_agent_pty(self, record: hosting.SessionRecord, cwd: str) -> None:
        """The session's `spawn_shell`: the user's shell on a new pty in
        *cwd*, the record bound to it. A refusal is kept on the record for
        the `spawn` reply (`SpawnError`); nothing is spawned."""
        shell = os.environ.get("SHELL") or "/bin/bash"
        session = record.session
        cols, rows = record.grid
        try:
            pty_id = self.ptys.spawn(
                "agent",
                [shell],
                cwd,
                self._spawn_env(),
                cols,
                rows,
                session=session.session_id,
                box=session.sandbox_box or None,
                plan=session.sandbox_plan_path,
                options=providers.options_record(session.options),
                progress=session.progress_env,
            )
        except ptyserver.SpawnError as exc:
            record.spawn_error = exc
            return
        except (OSError, ValueError) as exc:
            record.spawn_error = ptyserver.SpawnError(0, str(exc), cwd)
            return
        record.pty_id = pty_id
        pty = self.ptys.get(pty_id)
        pty.handle = record.handle
        # Registered the moment the pty is live, so an exception later in
        # the spawn (the session's own set-up) leaves no pty without its
        # record: the exit still reaches the session's clean-up.
        self.sessions[pty_id] = record

    def _on_pty_output(self, pty: ptyserver.Pty) -> None:
        record = self.sessions.get(pty.id)
        if record is not None:
            record.output_arrived()

    def _on_stream_event(self, pty_id: int, event: object) -> None:
        if isinstance(event, termstream.Progress):
            record = self.sessions.get(pty_id)
            if record is not None and self.activity is not None:
                self.activity.on_progress(record, event)
        if self._stream_listener is not None:
            self._stream_listener(pty_id, event)

    def session_resolved(self, record: hosting.SessionRecord, session_id: str) -> None:
        """The resolver bound a session: its pty's row names it, its box is
        settled and recorded (what MainWindow._settle_sandbox_box did), its
        shells' history re-filed, and the tracker told."""
        pty = record.pty()
        if pty is not None:
            pty.session = session_id
            self.ptys._record_row(pty)
        session = record.session
        if session.sandboxed and self.state is not None:
            box = session.sandbox_box
            owed = session.take_sandbox_defaults_owed()
            host = self.sandbox_host
            if host is None:
                self.state.set_sandboxed(session_id, True, box=box)
            elif host.settle_box(session_id, box, session.cwd, owed) and self.sandbox_grants is not None:
                self.sandbox_grants.sync(box)
        self.rekey_shells(record, session_id)
        if self.activity is not None:
            self.activity.resolved(record)
        if pty is not None:
            # The row of the pty table names the session now (PR-1.12c).
            self.publish_pty(pty.id)
        if self.background is not None:
            # Half of registering: the id is known; the row is the other half.
            self.background.sessions_changed()

    def session_forked(self, record: hosting.SessionRecord, session_id: str) -> None:
        """A sandboxed fork's resolver found the forked conversation: its
        pty runs that session from now on, as the pty table tells it."""
        record.forked = session_id
        if record.pty_id is not None:
            self.publish_pty(record.pty_id)

    def rekey_shells(self, record: hosting.SessionRecord, session_id: str) -> None:
        """File the history of every panel shell of *record*'s session (the
        shells spawned with its handle) under the id it resolved to."""
        for pty in self.ptys.ptys.values():
            if pty.kind == "shell" and getattr(pty, "handle", None) == record.handle:
                pty.history = session_id

    def session_transcript_landed(self, record: hosting.SessionRecord) -> None:
        if self.activity is not None:
            self.activity.transcript_landed(record)

    # -- the requests a tab makes of its session (§3.19)

    def _req_prompt(self, message: protocol.Message, client: Client) -> dict:
        session = self._record(message).session
        if message.get("when_empty") and not session.takes_prompt():
            # The client's mirror is a settle behind the screen: the live
            # model decides whether the box is empty.
            return protocol.refuse(message.id, protocol.ERROR_REFUSED, PROMPT_BLOCK_MSGID)
        if message.get("focus", True):
            session.inject_prompt(message.get("text"))
        else:
            session.inject_prompt_unfocused(message.get("text"))
        return protocol.reply(message.id)

    def _req_write(self, message: protocol.Message, client: Client) -> dict:
        """Raw keystrokes for the pty; a mention (a drop's tokens, built on
        the client) gets the leading space the box wants in front of it."""
        session = self._record(message).session
        text = message.get("text")
        if message.get("mention"):
            text = session.mention_leading_space() + text
        session.write_text(text)
        return protocol.reply(message.id)

    def _req_switch(self, message: protocol.Message, client: Client) -> dict:
        record = self._record(message)
        record.set_composer_open(message.get("composer_open"))
        if message.get("model"):
            record.session.switch_model(message.get("model"))
        if message.get("effort"):
            record.session.switch_effort(message.get("effort"))
        return protocol.reply(message.id)

    def _req_send(self, message: protocol.Message, client: Client) -> dict:
        record = self._record(message)
        record.set_composer_open(message.get("composer_open"))
        sent = []
        record.session.send_composed(message.get("text"), lambda: sent.append(True))
        return protocol.reply(message.id, sent=bool(sent))

    def _req_cut(self, message: protocol.Message, client: Client) -> dict:
        record = self._record(message)
        record.set_composer_open(True)
        handle = record.begin_cut(client.sink_for(record.pty_id))
        return protocol.reply(message.id, handle=handle)

    def _req_cut_cancel(self, message: protocol.Message, client: Client) -> dict:
        """A client calls its cuts off: one by handle, else every one of its
        own (never another client's)."""
        record = self._record(message)
        record.cancel_cuts(sink=client.sink_for(record.pty_id), handle=message.get("handle"))
        record.set_composer_open(False)
        return protocol.reply(message.id)

    def _req_draft_restore(self, message: protocol.Message, client: Client) -> dict:
        record = self._record(message)
        record.set_composer_open(False)
        return protocol.reply(message.id, restored=bool(record.session.restore_draft(message.get("text"))))

    def _req_mention(self, message: protocol.Message, client: Client) -> dict:
        """The editor's "Add to chat" typed into the box: the mention token
        for the path, a leading space when the box has a sentence in it
        already (Session.mention_leading_space)."""
        session = self._record(message).session
        if not session.agent_is_running():
            return protocol.refuse(
                message.id, protocol.ERROR_REFUSED, "Add to chat: the agent isn't running in this tab"
            )
        reference = session.provider.file_reference(
            message.get("path"),
            session.current_agent_cwd(),
            int(message.get("start_line") or 0),
            int(message.get("end_line") or 0),
        )
        if reference is None:
            return protocol.refuse(
                message.id, protocol.ERROR_REFUSED, "Add to chat isn't available for this file"
            )
        session.write_text(session.mention_leading_space() + reference + " ")
        return protocol.reply(message.id)

    def _req_close_nudge(self, message: protocol.Message, client: Client) -> dict:
        self._record(message).session.nudge_exit()
        return protocol.reply(message.id)

    def _req_close_end(self, message: protocol.Message, client: Client) -> dict:
        record = self.sessions.get(message.get("pty"))
        if record is not None:
            record.session.end_close()
        return protocol.reply(message.id)

    def _req_resolver_arm(self, message: protocol.Message, client: Client) -> dict:
        self._record(message).session.arm_resolver()
        return protocol.reply(message.id)

    def _req_transcript_update(self, message: protocol.Message, client: Client) -> dict:
        self._record(message).session.request_update(bool(message.get("discover")))
        return protocol.reply(message.id)

    def _req_transcript_set(self, message: protocol.Message, client: Client) -> dict:
        path = message.get("path")
        if path is not None and not transcript_path_allowed(path):
            return protocol.refuse(message.id, protocol.ERROR_REFUSED, "Not a transcript Collins reads")
        self._record(message).session.set_transcript_path(path)
        return protocol.reply(message.id)

    def _req_transcript_relocate(self, message: protocol.Message, client: Client) -> dict:
        path = message.get("path")
        if not transcript_path_allowed(path):
            return protocol.refuse(message.id, protocol.ERROR_REFUSED, "Not a transcript Collins reads")
        self._record(message).session.relocate_transcript(path)
        return protocol.reply(message.id)

    def _req_prs_restore(self, message: protocol.Message, client: Client) -> dict:
        self._record(message).session.restore_prs(message.get("records"))
        return protocol.reply(message.id)

    def _req_cwd_settle(self, message: protocol.Message, client: Client) -> dict:
        session = self._record(message).session
        judge = session.judge_cwd if message.get("judge") else session.settle_cwd
        scope = judge(message.get("cwd"), message.get("root"))
        return protocol.reply(message.id, scope=scope.name.lower() if scope is not None else "")

    def _req_shells_follow(self, message: protocol.Message, client: Client) -> dict:
        record = self._record(message)
        record.session.shells_follow_armed = bool(message.get("armed"))
        record.refresh_facts()
        return protocol.reply(message.id)

    def _req_finish_witness(self, message: protocol.Message, client: Client) -> dict:
        stamp, size = self._record(message).session.finish_witness()
        return protocol.reply(message.id, stamp=[int(stamp[0]), int(stamp[1])], size=size)

    def _req_baseline_absorb(self, message: protocol.Message, client: Client) -> dict:
        record = self._record(message)
        capturing = self.activity.absorb_baseline(record.session) if self.activity is not None else False
        return protocol.reply(message.id, capturing=bool(capturing))

    def _req_baseline_cmdlines(self, message: protocol.Message, client: Client) -> dict:
        cmdlines = sorted(self._record(message).session.background_descendant_cmdlines())
        return protocol.reply(message.id, cmdlines=[c[: protocol.ARG_TEXT_MAX] for c in cmdlines[:1000]])

    def _req_restart_worktreeless(self, message: protocol.Message, client: Client) -> dict:
        self._record(message).session.relaunch_without_worktree()
        return protocol.reply(message.id)

    # -- the e2e probe (D27)

    def _req_debug_session_get(self, message: protocol.Message, client: Client) -> dict:
        target, name = self._debug_target(self._record(message).session, message.get("name"))
        value = getattr(target, name)
        if callable(value):
            return protocol.reply(message.id, callable=True)
        return protocol.reply(message.id, value=_jsonable(value))

    def _req_debug_session_set(self, message: protocol.Message, client: Client) -> dict:
        record = self._record(message)
        target, name = self._debug_target(record.session, message.get("name"))
        setattr(target, name, message.get("value"))
        record.refresh_facts()
        return protocol.reply(message.id)

    def _req_debug_session_call(self, message: protocol.Message, client: Client) -> dict:
        record = self._record(message)
        target, name = self._debug_target(record.session, message.get("name"))
        result = getattr(target, name)(*(message.get("args") or []), **(message.get("kwargs") or {}))
        record.refresh_facts()
        return protocol.reply(message.id, value=_jsonable(result))

    def _debug_target(self, session, name: str):
        """A dotted name walked from the session (`finish_ledger.armed`,
        `transcript.set_path`, `activity.judge.held`): the owner and the
        last part. `activity` and `core` reach the service's tracker and
        the core itself."""
        parts = name.split(".")
        target: Any = session
        for part in parts[:-1]:
            if target is session and part == "activity":
                target = self.activity
            elif target is session and part == "core":
                target = self
            else:
                target = getattr(target, part)
        return target, parts[-1]

    def _req_debug_screen(self, message: protocol.Message, client: Client) -> dict:
        screen = self.ptys.get(message.get("pty")).screen
        column, row = screen.cursor()
        return protocol.reply(
            message.id,
            rows=screen.rows(),
            cursor=[int(column), int(row)],
            columns=screen.columns(),
            row_count=screen.row_count(),
            capture=screen.capture_contents()[: protocol.TEXT_MAX],
        )

    def _req_debug_pty(self, message: protocol.Message, client: Client) -> dict:
        pty = self.ptys.get(message.get("pty"))
        return protocol.reply(
            message.id,
            child_pid=pty.child_pid(),
            foreground_pgrp=pty.foreground_pgrp(),
            shell_pid=pty.shell_pid(),
            process_cwd=pty.process_cwd(),
        )

    def _req_debug_sandbox(self, message: protocol.Message, client: Client) -> dict:
        targets = {"host": self.sandbox_host, "grants": self.sandbox_grants, "core": self}
        target = targets[message.get("target")]
        if target is None:
            return protocol.refuse(message.id, protocol.ERROR_GONE, "Sandboxed sessions aren't set up here")
        method = getattr(target, message.get("name"))
        result = method(*(message.get("args") or []), **(message.get("kwargs") or {}))
        return protocol.reply(message.id, value=_jsonable(result))

    def _req_service_status(self, message: protocol.Message, client: Client) -> dict:
        """What the service is running (§3.10): its version and protocol,
        the counts, when it started, the sandbox probe's verdict and the
        live grants' word."""
        from .. import __version__

        busy = self.activity.busy_count() if self.activity is not None else 0
        fields: dict = {
            "version": __version__,
            "protocol": protocol.PROTOCOL,
            "ptys": len(self.ptys.ptys),
            "busy": busy,
            "clients": len(self._clients),
            "started": self._started,
            "pid": os.getpid(),
        }
        reason = sandboxplan.probe_reason()
        if reason is not None:
            fields["sandbox"] = reason[: protocol.ARG_TEXT_MAX]
        if self.sandbox_grants is not None:
            fields["live"] = (self.sandbox_grants.capable() or "")[: protocol.ARG_TEXT_MAX]
        if self.gh_status is not None:
            fields["gh"] = self.gh_status[: protocol.SHORT_MAX]
        return protocol.reply(message.id, **fields)

    def activity_call(self, name: str, *args, **kwargs):
        """A method of the service's tracker by dotted name (`tracker.mark`,
        `judge.drop`): the probe's door for a check with no tab to reach
        it through (`debug.sandbox` with target ``core``)."""
        target: Any = self.activity
        parts = name.split(".")
        for part in parts[:-1]:
            target = getattr(target, part)
        return getattr(target, parts[-1])(*args, **kwargs)

    def sandbox_hosted(self) -> bool:
        """Whether this service runs sandboxed sessions (a host was started)."""
        return self.sandbox_host is not None

    def grants_live(self) -> bool:
        """Whether the live grants exist (with the host)."""
        return self.sandbox_grants is not None

    def restart_grants(self) -> None:
        """The e2e checks' last pass: the live grants built again (the
        bindfs override gone from the environment), in the service's place."""
        if self.sandbox_grants is not None:
            self.sandbox_grants.shutdown()
        self.sandbox_grants = sandboxgrants.GrantMounts(self.sandbox_host)

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
        sink = client.sink_for(pty_id)
        answer = self.ptys.attach(pty_id, sink, message.get("cols"), message.get("rows"))
        record = self.sessions.get(pty_id)
        if record is not None:
            # The session's facts, whole, before any live word (§3.19).
            record.client_attached(sink)
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
        pty_id = message.get("pty")
        mode = message.get("mode")
        record = self.sessions.get(pty_id)
        if mode in ("exit", "background") and not message.get("force") and record is not None:
            text = message.get("text") or ""
            if not text:
                return protocol.refuse(
                    message.id, protocol.ERROR_REFUSED, "A graceful close needs the exit keystrokes"
                )
            if mode == "background" and self.background is not None:
                # The gate, on the service (§5: a sandboxed session is never
                # handed to the CLI's daemon, which would respawn it outside
                # its box): a /bg that could not be tracked is refused, and
                # the window falls back to the graceful exit.
                blocker = self.background.blocker(record.session)
                if blocker:
                    return protocol.refuse(message.id, protocol.ERROR_REFUSED, blocker)
                # The fork watch and the pre-emptive "detached" (PR-1.12d),
                # before the /bg is typed: the agents listed so far are noted
                # first.
                self.background.handoff(record)
            record.session.begin_close(text, mode == "background", record.close_budget)
            return protocol.reply(message.id)
        if record is not None:
            record.session.end_close()
        self.ptys.close(pty_id)
        return protocol.reply(message.id)

    def _req_clear(self, message: protocol.Message, client: Client) -> dict:
        """A shell's *Clear*: its screen and scrollback wiped from the model
        (the client attaches again to be redrawn from it). An agent's
        ``clear`` is the composer's erase of the CLI's box: the provider's
        clear keys for what the box holds now (nothing for an empty one)."""
        pty_id = message.get("pty")
        if self.ptys.get(pty_id).kind != "shell":
            record = self._record(message)
            entered = record.session.entered_prompt()
            keys = record.session.provider.clear_prompt_keys(entered) if entered is not None else None
            if keys:
                record.session.write_text(keys)
            return protocol.reply(message.id)
        self.ptys.clear(pty_id)
        return protocol.reply(message.id)

    def _req_pty_info(self, message: protocol.Message, client: Client) -> dict:
        """A pty's process facts (a panel shell's reads: its shell, whether
        a command runs, where the shell is), from the master the service
        holds and its /proc."""
        pty = self.ptys.get(message.get("pty"))
        return protocol.reply(
            message.id,
            kind=pty.kind,
            child_pid=pty.child_pid(),
            shell_pid=pty.shell_pid(),
            foreground_pgrp=pty.foreground_pgrp(),
            running_command=bool(pty.has_running_command()),
            process_cwd=pty.process_cwd(),
            plan=pty.plan,
            cols=pty.cols,
            rows=pty.rows,
        )

    def _req_pty_capture(self, message: protocol.Message, client: Client) -> dict:
        """A pty's text from the model of record (what the panel history
        is written from and the terminal tools read)."""
        return protocol.reply(message.id, text=self.ptys.capture(message.get("pty"))[: protocol.TEXT_MAX])

    def _req_panel_key(self, message: protocol.Message, client: Client) -> dict:
        """File a shell's history under a new key (the resolver bound its
        tab to a session id), or under none (its page closed for good)."""
        pty = self.ptys.get(message.get("pty"))
        if pty.kind != "shell":
            return protocol.refuse(message.id, protocol.ERROR_REFUSED, "Only a shell has a panel history")
        pty.history = message.get("history")
        if message.get("handle"):
            # A shell opened before its session was spawned (the new-chat
            # screen's) learns the session it belongs to: `rekey_shells`
            # re-files it when the session resolves.
            pty.handle = message.get("handle")
        return protocol.reply(message.id)

    def _on_pty_exit(self, pty: ptyserver.Pty) -> None:
        """A shell's child exited: its history, from its model while the
        model is still whole (§3.15). A shell filed under no key (a fork's,
        a page closed for good, a tab with no session yet) writes nothing.
        An agent's exit is its session's (`Session.shell_exited`: the box
        released, the plan unlinked), closes the shell the tools opened for
        its session with no client attached (SessionTools.agent_exited) and
        drops the session once the exit is reported."""
        if pty.kind != "shell":
            ran = self.agent_pty_sessions(include_finished=True).get(pty.id)
            self._cli_running.pop(pty.id, None)
            record = self.sessions.pop(pty.id, None)
            # The subscribers' word that the agent's row left the table
            # (PR-1.12c): its session runs no more.
            self._broadcast(
                {"t": "pty-exited", "pty": pty.id, "status": pty.exit_status, "table": True}
            )
            if self.feed is not None and ran:
                self.feed.refresh_running({ran})
            if record is not None:
                status = -1 if pty.exit_status is None else int(pty.exit_status)
                try:
                    record.session.shell_exited(status)
                except Exception:
                    log.exception("session %s: the exit's clean-up failed", record.handle)
                if self.activity is not None:
                    self.activity.ended(record)
            if self.background is not None:
                self.background.session_ended(ran or pty.session)
            if self.tools is not None and pty.session:
                self.tools.agent_exited(pty.session)
            return
        if not pty.history:
            return
        panelhistory.save(pty.history, pty.screen.capture_contents(), pty.ordinal)

    # -- jobs (PR-1.11)

    def _req_job_start(self, message: protocol.Message, client: Client) -> dict:
        args = message.get("args") or {}
        if message.get("kind") == "session.repair":
            # What the match reads off the store and the state, here on the
            # main loop; the job's thread only asks the agent CLI.
            session_id = args.get("session")
            inputs = None
            if self.background is not None and isinstance(session_id, str):
                inputs = self.background.repair_inputs(session_id)
            args = {"inputs": inputs}
        elif message.get("kind") == "worktree.check":
            # The session's transcript and directory, looked up here on the
            # main loop; the job's thread reads the transcript.
            session_id = args.get("session")
            session = self.store.get_session(session_id) if isinstance(session_id, str) else None
            args = {"session": session_id if session is not None else None}
            if session is not None:
                args.update(path=str(session.jsonl_path), cwd=session.cwd or "")
        job_id = self.jobs.start(message.get("kind"), args, client.deliver)
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

    # -- git over the API (PR-2.1; service.gitfeed, service.files)

    def _git(self, message: protocol.Message, client: Client) -> dict | protocol.Deferred:
        return self.git.handle(message, client)

    _req_git_run = _git
    _req_git_info = _git
    _req_git_sizes = _git
    _req_git_watch = _git
    _req_git_unwatch = _git
    _req_git_plan = _git

    def _req_fs_trash(self, message: protocol.Message, client: Client) -> dict | protocol.Deferred:
        return files_mod.handle_trash(self, client, message, later=self.git._later)

    # -- files over the API (PR-2.3; service.files): the editor's files

    def _files(self, message: protocol.Message, client: Client) -> dict | protocol.Deferred:
        return self.files.handle(message, client)

    _req_fs_read = _files
    _req_fs_write = _files
    _req_fs_watch = _files
    _req_fs_unwatch = _files
    _req_fs_stat = _files  # the tree, quick open and roots (PR-2.4)
    _req_fs_list = _files
    _req_fs_walk = _files
    _req_fs_rename = _files  # file operations (PR-2.5)
    _req_fs_paste = _files
    _req_fs_mkdir = _files

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
        for pty_id in list(self.ptys.ptys):
            event = self.table_row(pty_id)
            if event is None:
                continue
            client.deliver(event)
            ptys += 1
        for record in self._records():
            if record.pty_id is not None:
                client.deliver(
                    {"t": "session", "pty": record.pty_id, "handle": record.handle, **record.snapshot()}
                )
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
        # A client's launch is when gh is asked about (ghsetup, the notice
        # ghwelcome shows): the last answer may predate an install or a
        # login, so it is dropped and asked again.
        self.check_gh(fresh=True)
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
                if self.background is not None:
                    self.background.settings_changed()
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
        if message.get("busy") is not None:
            # D29: busy is the service's own verdict (tracking.ServiceActivity);
            # a client says what the person did at its screen: the status,
            # unread off (the person looked) or on (a notification its
            # delivery table flagged, a placeholder's flag handed to the row).
            return protocol.refuse(
                message.id, protocol.ERROR_REFUSED, "Busy is decided by the service, not a client"
            )
        if message.get("backgrounding") is not None or message.get("can_background") is not None:
            # §3.22: the /bg handoff and its gate are the service's own
            # (bgagents), sent to every client as `item` fields.
            return protocol.refuse(
                message.id,
                protocol.ERROR_REFUSED,
                "The background handoff is decided by the service, not a client",
            )
        if (status := message.get("status")) is not None:
            store.set_status(session_id, status)
        if (unread := message.get("unread")) is not None:
            store.set_unread(session_id, unread)
        return protocol.reply(message.id)

    def _req_store_forget(self, message: protocol.Message, client: Client) -> dict:
        session_id = message.get("session")
        if self.store.get_session(session_id) is not None:
            # Its transcript is still there (a forget is what follows a trash
            # or a delete, which take the session out of the store first):
            # its records are not a client's to drop.
            return protocol.refuse(
                message.id, protocol.ERROR_REFUSED, "This session's transcript is still there"
            )
        self.forget_session(session_id)
        return protocol.reply(message.id)

    def forget_session(self, session_id: str, announce: bool = False) -> None:
        """Let go of what the service kept for a session whose transcript
        went (what MainWindow._forget_transcript did with the service's
        files): its panel history, its PRs, its images, its draft, and its
        box with what the box was allowed — unlinked, not trashed (Collins'
        own derived data). The sticky flag stays, so a transcript restored
        from the trash resumes boxed, in a fresh home. A box another id of
        the conversation still names keeps its grants and stays; one a
        live session runs in goes when that lets go. *announce* tells the
        clients (`forgotten`): the sweep's forget, which no client asked
        for."""
        if not session_id:
            return
        panelhistory.delete(session_id)
        state = self.state
        state.set_session_draft(session_id, "")
        state.set_session_attachments(session_id, [])
        self.store.pr_store.set_records(session_id, [])
        if session_id in state.sandboxed_sessions:
            box = state.sandboxed_sessions[session_id]
            state.set_sandboxed(session_id, True, box="")
            if box and self.session_for_box(box) is not None:
                log.info("forget %s: a session runs in its box %s; kept", session_id, box)
            elif box and self.sandbox_host is not None:
                self.sandbox_host.forget_box(box)
        if announce:
            self._broadcast({"t": "forgotten", "session": session_id})

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

    def start_tools(self, sessions=None, sandbox_host=None, notifications=None, diffs=None):
        """The session tools' dispatcher over the core's records: the
        service's sessions (`sessions`, a test's own lookup given one) and
        its sandbox host. Returns it: what the MCP socket service
        dispatches to."""
        self.tools = tools_mod.SessionTools(
            get_setting=self._get_setting,
            sessions=sessions or self._sessions_list,
            sandbox_host=sandbox_host or (lambda: self.sandbox_host),
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

    def start_sandbox(self, host=None, grants=None, sessions=None) -> None:
        """The Sandboxed chip's requests (service.sandbox) over the core's
        sandbox host, live grants and sessions (each a test's own lookup
        when given); the plan of a box is the core's own lookup."""
        self.sandbox = sandbox_mod.SandboxRequests(
            host=host or (lambda: self.sandbox_host),
            grants=grants or (lambda: self.sandbox_grants),
            plan_of=self._sandbox_plan,
            sessions=sessions or self._sessions_list,
            broadcast=self._broadcast,
            app_id=lambda: self.app_id,
            session_for_box=self.session_for_box,
        )

    def start_sandbox_host(self, app_id: str) -> None:
        """The sandbox host (§3.9, PR-1.12a: what App._start_sandbox_support
        did): the `SandboxHost` over the service's own state, the plans a
        previous run never released swept, the grants of boxes that are
        gone pruned, the live grants and their worker, the mounts a dead
        instance left and the boxes nobody names swept off the main loop,
        and the probe, whose verdict goes to every subscriber as a `sandbox`
        event (``what: "probe"``)."""
        self.app_id = app_id
        state = self.state
        if state is None:
            return
        host = sandboxplan.SandboxHost(app_id, state, state.state_file())
        self.sandbox_host = host
        swept = sandboxplan.sweep_plans(app_id)
        if swept:
            log.info("sandbox: swept %d stale plan file(s)", swept)
        # Here, on the main loop, before the sweep's thread: it writes the state.
        pruned = host.prune_grants()
        if pruned:
            log.info("sandbox: dropped the grants of %d box(es) that are gone", pruned)
        grants = sandboxgrants.GrantMounts(host)
        self.sandbox_grants = grants

        def sweep() -> None:
            # A box with a mount under it is never removed: the mounts first.
            unmounted = grants.sweep_mounts()
            if unmounted:
                log.info("sandbox: unmounted %d grant(s) a dead instance left", unmounted)
            gone = host.sweep_boxes()
            if gone:
                log.info("sandbox: removed %d unused box(es)", gone)

        threading.Thread(target=sweep, name="sandbox-sweep", daemon=True).start()
        sandboxplan.probe_async(self._on_probe)

    def _on_probe(self, reason: str) -> None:
        """The probe's verdict, on its thread: told to every subscriber on
        the main loop."""
        from gi.repository import GLib

        def landed() -> bool:
            self._broadcast({"t": "sandbox", "what": "probe", "reason": reason[: protocol.ARG_TEXT_MAX]})
            return False

        GLib.idle_add(landed, priority=GLib.PRIORITY_DEFAULT)

    def start_activity(self) -> tracking.ServiceActivity:
        """The tracker (§3.6, D29): built over the core's sessions, store
        and state, fed by the pty server's output and stream events and the
        input frames from here on."""
        self.activity = tracking.ServiceActivity(
            records=self._records,
            store=self.store,
            state=self.state,
            get_setting=lambda key: self._get_setting(key) in (None, True),
            announce=self._announce_finished,
        )
        return self.activity

    def start_background(self, **kwargs) -> bgagents.BackgroundAgents:
        """The background agents (§3.22, PR-1.12d): built over the core's
        store, state, sessions and tracker, the repair's job registered, and
        started (the watch, the first reading, the replay). *kwargs* are a
        test's own fetches, timers and poller."""
        if self.store is None or self.state is None:
            raise RuntimeError("the core has no store to keep the background agents in")
        background = bgagents.BackgroundAgents(
            store=self.store,
            state=self.state,
            records=self._records,
            activity=self.activity,
            running=lambda: {sid for sid in self.agent_pty_sessions().values() if sid},
            get_setting=self._get_setting,
            **kwargs,
        )
        self.background = background
        if self.activity is not None:
            self.activity.on_sessions_changed = background.sync_busy_poll
        else:
            # The busy feed has nobody to tell: start_activity first.
            log.warning("background agents started with no tracker: the busy feed is off")
        self.jobs.register("session.repair", self._repair_job)
        self.jobs.register("worktree.check", self._worktree_check_job)
        self.store.connect("refreshed", lambda *_a: self.sync_transcript_paths())
        background.start()
        return background

    def _repair_job(self, job: jobs.Job, args: dict) -> dict:
        """The ``session.repair`` job's worker, on its thread: the match
        (the agent CLI, the transcripts), then the forward recorded back on
        the main loop before the job's outcome is reported. The result's
        ``found`` is the agent's id, "" when the session is itself the
        listed agent, None when nothing (or more than one) matches."""
        inputs = args.get("inputs")
        background = self.background
        if background is None or not isinstance(inputs, dict):
            raise jobs.JobRefused("No such session")
        found = background.repair_match(inputs)
        landed = threading.Event()

        def land() -> None:
            try:
                background.repair_landed(inputs, found)
            finally:
                landed.set()

        background._land(land)
        landed.wait(10)
        return {"found": found}

    def _worktree_check_job(self, job: jobs.Job, args: dict) -> dict:
        """The ``worktree.check`` job's worker (the archive's worktree ask,
        §3.22): on its thread, the worktree the session's transcript still
        records on disk (`sessions.removable_worktree`, a read of the whole
        transcript, never on the main loop); then, back on the main loop,
        whether it is not the client's to take — the session runs on as a
        background agent, or one works in there. The result is
        ``removable`` (the worktree state, or None) and ``shares``. The tabs
        that may work in it are the client's to count."""
        session_id = args.get("session")
        if not session_id:
            return {"removable": None, "shares": False}
        state = sessions.removable_worktree(args.get("path") or None, args.get("cwd") or "")
        shares = [False]
        background = self.background
        if background is not None:
            landed = threading.Event()

            def land() -> None:
                try:
                    if state is not None:
                        shares[0] = background.worktree_shared(session_id, str(state["worktreePath"]))
                    else:
                        shares[0] = background.is_detached(session_id)
                finally:
                    landed.set()

            background._land(land)
            landed.wait(10)
        return {"removable": _jsonable(state), "shares": bool(shares[0])}

    def sync_transcript_paths(self) -> None:
        """Follow a transcript that moved out from under a live session (what
        MainWindow._sync_transcript_paths did, §3.22). The CLI keys a
        session's transcript by its working directory, so a session that
        enters a git worktree has its file re-homed under a new project
        directory; the session's tail would otherwise follow a path that
        no longer exists for the rest of the run. Only a session whose own
        path has gone missing is touched, and only when the store found
        that same session somewhere that exists, so a session deliberately
        pointed at another file (an attached fork tails the fork's
        transcript) is never dragged off it. The client hears the new path
        as the session's `transcript_path` and the row's `path`."""
        if self.store is None:
            return
        for record in self._records():
            session = record.session
            if record.exited or not session.session_id or session.fork:
                continue
            current = session.transcript_path
            if not current or Path(current).exists():
                continue
            found = self.store.get_session(session.session_id)
            if found is None:
                continue
            moved = str(found.jsonl_path)
            if moved == current or not Path(moved).exists():
                continue
            log.info("transcript moved: %s -> %s", current, moved)
            try:
                session.relocate_transcript(moved)
            except Exception:
                log.exception("session %s: following the moved transcript failed", record.handle)

    def start_housekeeping(self) -> None:
        """The service's own timers (PR-1.12d): the archive sweep at start
        and every `SWEEP_POLL_S` (autodelete makes it one sweep a day, none
        until the setting is on), and gh asked about once. The first sweep
        waits for the store's first scan: before it the store knows no
        session, and a sweep then would stamp the day having trashed
        nothing."""
        if self.store is not None and not self.store.applied:
            handler = None

            def first(*_args) -> None:
                self.store.disconnect(handler)
                self.sweep_archived()

            handler = self.store.connect("refreshed", first)
        else:
            self.sweep_archived()
        if not self._sweep_source:
            self._sweep_source = GLib.timeout_add_seconds(SWEEP_POLL_S, self._sweep_tick)
        self.check_gh()

    def _sweep_tick(self) -> bool:
        self.sweep_archived()
        return True

    def sweep_archived(self) -> list[str] | None:
        """Ask autodelete whether a sweep is due (the setting and the
        once-a-day file are its to weigh) and trash what has expired."""
        if self.store is None or self.state is None:
            return None
        try:
            return autodelete.maybe_sweep(
                self.state.settings, dict(self.state.archived_at), self.trash_expired_archives
            )
        except Exception:  # never let housekeeping take the service down
            log.warning("archive sweep failed", exc_info=True)
            return None

    def trash_expired_archives(self, session_ids: list[str]) -> list[str]:
        """The automatic delete (autodelete.maybe_sweep's *trash*): the path
        of *Delete archived sessions…* without the dialog. A session still
        running (an agent pty on the service, or a background agent) is
        skipped — it comes round again tomorrow — and a project this
        empties is kept as an empty header: an automatic delete has nobody
        to ask. Returns the ids that were not trashed."""
        from ..store import emptied_projects

        store, state = self.store, self.state
        sessions_by_id = {s.session_id: s for s in store.all_sessions()}
        running = self.background.session_is_running if self.background is not None else (lambda _s: False)
        wanted = [
            sid
            for sid in session_ids
            if sid in sessions_by_id and state.is_archived(sid) and not running(sid)
        ]
        skipped = [sid for sid in session_ids if sid not in wanted]
        if not wanted:
            return skipped
        emptied = emptied_projects(list(sessions_by_id.values()), set(wanted))
        if emptied:
            store.keep_projects(emptied)
        errors = store.trash_many(wanted)
        for session_id in wanted:
            if session_id in errors:
                continue
            self.forget_session(session_id, announce=True)
            # The row is gone for good: its id (and its archive stamp) go
            # from the state too.
            state.set_archived(session_id, False)
        for session_id, error in errors.items():
            log.warning("archive sweep: could not trash %s: %s", session_id, error)
        return skipped + list(errors)

    def check_gh(self, fresh: bool = False) -> None:
        """Ask ghsetup, off the main loop, whether the service's gh is there
        to be used; `service.status` carries the answer once it has landed.
        *fresh* drops the last answer first (a client's launch: the user
        may have installed gh or logged in since): a check already running
        then started before the drop, so its answer is not kept and one
        more check follows it."""
        if fresh:
            self.gh_status = None
            self._gh_wanted += 1
        if self._gh_checking:
            return
        self._gh_checking = True
        asked = self._gh_wanted
        from gi.repository import GLib

        def landed(status: str | None) -> bool:
            self._gh_checking = False
            if asked == self._gh_wanted:
                self.gh_status = status
            else:
                self.check_gh()  # dropped while it ran: ask again
            return False

        def work() -> None:
            from .. import ghsetup

            try:
                status = ghsetup.check()
            except Exception:
                log.exception("gh: the check failed")
                status = None
            GLib.idle_add(landed, status, priority=GLib.PRIORITY_DEFAULT)

        threading.Thread(target=work, name="gh-check", daemon=True).start()

    def background_call(self, name: str, *args, **kwargs):
        """A method of the background agents by dotted name
        (`mark_backgrounding`, `poller.background_ids.add`): the probe's door
        (`debug.sandbox` with target ``core``), as `activity_call` is the
        tracker's."""
        target: Any = self.background
        parts = name.split(".")
        for part in parts[:-1]:
            target = getattr(target, part)
        return getattr(target, parts[-1])(*args, **kwargs)

    def _announce_finished(self, session_id: str) -> None:
        """A counted finish, to every subscriber (§3.19): a `notify` event of
        kind ``finished`` (never persisted, as today), which a client's
        delivery runs on — the pull requests re-read, the green announced —
        before the row's unread flag follows it."""
        self._broadcast(
            {
                "t": "notify",
                "notification": f"finished:{session_id}",
                "kind": "finished",
                "session": session_id,
                "when": time.time(),
            }
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
    _req_sandbox_derive = _req_sandbox
    _req_sandbox_drop = _req_sandbox
    _req_sandbox_forget = _req_sandbox

    def _tool_client(self, session) -> Client | None:
        """The session's active client (D20): of the subscribed clients,
        the one whose device is the active client of the session's agent
        pty, else the one that subscribed last."""
        if not self._subscribers:
            return None
        device = None
        for record in self.sessions.values():
            if record.session is session:
                pty = record.pty()
                device = (pty.sized_for or None) if pty is not None else None
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
                self._spawn_env(),
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
        """A `0x02` frame: what the client's VTE committed — typed into the
        pty, and on its way to the tracker's echo gate for an agent's."""
        record = self.sessions.get(pty_id)
        if record is not None and self.activity is not None:
            # Told before the write: a "\r" re-arms the resolver and takes
            # the baseline's snapshot while the pty is still pristine.
            try:
                self.activity.on_input(record, data)
            except Exception:  # noqa: BLE001 - the tracker's failure is not the keystroke's
                log.exception("pty %d: the tracker failed on an input frame", pty_id)
        try:
            self.ptys.write(pty_id, data, sink=client.sink_for(pty_id))
        except KeyError:
            return

    # -- the panel history (§3.15)

    def _req_panel_history(self, message: protocol.Message, client: Client) -> dict:
        """The tab's three saves (a draft save, the tab's close, the quit):
        `write_panel_history` as a request (PR-1.12b)."""
        shells: dict[int, int | str] = {}
        keep: set[int] = set()
        for entry in message.get("shells") or []:
            if entry.get("keep"):
                keep.add(int(entry["ordinal"]))
            elif "pty" in entry:
                shells[int(entry["ordinal"])] = int(entry["pty"])
            else:
                shells[int(entry["ordinal"])] = str(entry.get("text") or "")
        self.write_panel_history(
            str(message.get("key")), shells, keep=keep, partial=bool(message.get("partial"))
        )
        return protocol.reply(message.id)

    def write_panel_history(
        self, key: str, shells: dict[int, int | str], keep: set[int] = frozenset(), partial: bool = False
    ) -> None:
        """Write a session's panel history (spec §3.15): each shell under
        its ordinal, from its pty's live model (`PtyServer.capture`; a pty
        already gone writes nothing, its file cleared) when *shells* names
        a pty id, else the text given (a shell with no pty: what its widget
        shows).
        The mapping is the keep-set: files under ordinals it does not name
        are dropped, as `panelhistory.save_all` does; the path and the cap
        are panelhistory's. The tab decides the key (the session id, or
        the draft id of a new-chat screen) and the moment (a draft save,
        the tab's close, the app's quit, each before its shells end) and
        asks with `panel.history`."""
        texts: dict[int, str] = {}
        for ordinal, source in shells.items():
            if isinstance(source, str):
                texts[ordinal] = source
            else:
                texts[ordinal] = self.ptys.capture(int(source))
        if partial:
            # Only what is named is written; the keep-set went before.
            for ordinal, text in texts.items():
                panelhistory.save(key, text, ordinal)
            return
        panelhistory.save_all(key, texts, keep=keep)

    # -- the e2e probe's write spy (D27; served through debug.sandbox target core)

    def debug_spy_writes(self, pty_id: int) -> None:
        """Record every byte written to *pty_id* from now on
        (`debug_written` reads them): what a check cannot read by
        wrapping `PtyServer.write` in its own process."""
        if getattr(self, "_write_spy", None) is None:
            self._write_spy: dict[int, list[str]] = {}
            real_write = self.ptys.write

            def spy(pty_id, data, sink=None):
                written = self._write_spy.get(pty_id)
                if written is not None:
                    written.append(bytes(data).hex())
                return real_write(pty_id, data, sink=sink)

            self.ptys.write = spy
        self._write_spy[pty_id] = []

    def debug_patch(self, module: str, attr: str, value) -> object:
        """Set *attr* of the service's *module* to *value* (a JSON value:
        a limit a check lowers, a flag) and return the old one, JSON-shaped
        or None: what a check patched in its own process while the service
        ran there (D27, debug only)."""
        import importlib

        target = importlib.import_module(module)
        old = getattr(target, attr, None)
        setattr(target, attr, value)
        return _jsonable(old)

    def debug_find_handle(self, pid: int) -> str | None:
        """The handle of the session the shim at *pid* descends from
        (`SessionTools.find`; D27), or None."""
        if self.tools is None:
            return None
        session = self.tools.find(int(pid))
        return getattr(session, "handle", None) if session is not None else None

    def debug_tools_list(self, pid: int) -> list[str]:
        """The names of the tools the session at *pid* is offered, as the
        MCP socket serves them (`SessionTools.list_tools`; D27)."""
        if self.tools is None:
            return []
        return [tool["name"] for tool in self.tools.list_tools(int(pid))]

    def debug_tools_dispatch(self, pid: int, tool: str, args: dict):
        """One tool call as the dispatcher sees it arrive from *pid*, through
        every gate (`SessionTools.dispatch`; D27): ``[ok, text]``, or
        ``{"deferred": id}`` for a reply that waits on a client (a UI-bound
        tool: the asking check is that client, so it polls
        `debug_tools_result` from its own loop rather than blocking here)."""
        if self.tools is None:
            return [False, "no tools"]
        result = self.tools.dispatch(int(pid), str(tool), dict(args or {}))
        if isinstance(result, tuple):
            return [bool(result[0]), str(result[1])]
        deferred = getattr(self, "_debug_deferred", None)
        if deferred is None:
            deferred = self._debug_deferred = {}
        call_id = f"d-{len(deferred) + 1}"
        deferred[call_id] = result
        return {"deferred": call_id}

    def debug_tools_result(self, call_id: str):
        """A deferred tool call's settled ``[ok, text]``, or None while it
        waits."""
        result = getattr(self, "_debug_deferred", {}).get(str(call_id))
        if result is None or not result.resolved:
            return None
        ok, text = result.result()
        return [bool(ok), str(text)]

    def debug_written(self, pty_id: int) -> list[str]:
        """The hex of every write recorded for *pty_id* since the spy."""
        return list(getattr(self, "_write_spy", {}).get(pty_id, []))

    # -- the pty table as the subscription carries it (PR-1.12c, §3.21)

    def agent_pty_sessions(self, include_finished: bool = False) -> dict[int, str | None]:
        """Every live agent pty whose CLI runs, and the session it runs: the
        id a resume or a resolved fresh spawn runs under; for a fork, the
        forked conversation's id once its resolver found it, else None (a
        fork's pty is no view of the session it forked from); None for a
        fresh spawn that has not resolved yet. A pty whose CLI has exited
        (the shell alone, or the CLI not up yet) is no running session and
        is left out (`cli_changed`, the /proc poll's word); *include_finished*
        (the exit's own read) takes every agent pty, finished or not."""
        table: dict[int, str | None] = {}
        for pty_id, pty in self.ptys.ptys.items():
            if pty.kind == "shell" or (getattr(pty, "_finished", False) and not include_finished):
                continue
            record = self.sessions.get(pty_id)
            if not include_finished and record is not None and not self._cli_running.get(pty_id):
                continue
            if record is None:
                table[pty_id] = pty.session or None
            elif record.session.fork:
                table[pty_id] = getattr(record, "forked", None)
            else:
                table[pty_id] = record.session.session_id or None
        return table

    def running_sessions(self) -> set[str]:
        """The session ids an agent pty on the service runs right now
        (each item's `running` field)."""
        return {sid for sid in self.agent_pty_sessions().values() if sid}

    def table_row(self, pty_id: int) -> dict | None:
        """The `pty` event a subscriber is sent for one row of the pty
        table (``table: true``: the sidebar's, never a view's); None for a
        pty that is gone."""
        pty = self.ptys.ptys.get(pty_id)
        if pty is None or getattr(pty, "_finished", False):
            return None
        if pty.kind != "shell" and pty_id in self.sessions and not self._cli_running.get(pty_id):
            return None  # its CLI is not running: no row (cli_changed)
        event: dict = {
            "t": "pty",
            "pty": pty_id,
            "kind": pty.kind or "agent",
            "cols": pty.cols,
            "rows": pty.rows,
            "table": True,
        }
        if pty.cwd:
            event["cwd"] = pty.cwd
        session = pty.session if pty.kind == "shell" else self.agent_pty_sessions().get(pty_id)
        if session:
            event["session"] = session
        pid = pty.child_pid()
        if pid:
            event["pid"] = pid
        return event

    def cli_changed(self, record: hosting.SessionRecord, running: bool) -> None:
        """The session's CLI came up or went (the facts' `running_command`,
        re-read by the /proc poll and on a foreground flip): its pty joins
        the table, or leaves it with the table's word (a `pty-exited` with
        ``table: true``; the pty itself lives on, the shell alone), and the
        item's `running` follows (PR-1.12c review)."""
        pty_id = record.pty_id
        if pty_id is None or pty_id not in self.ptys.ptys:
            return
        running = bool(running)
        if self._cli_running.get(pty_id, False) == running:
            return
        if not running:
            ran = self.agent_pty_sessions().get(pty_id)
        self._cli_running[pty_id] = running
        if running:
            self.publish_pty(pty_id)
            return
        self._broadcast({"t": "pty-exited", "pty": pty_id, "status": None, "table": True})
        if self.feed is not None and ran:
            self.feed.refresh_running({ran})

    def publish_pty(self, pty_id: int) -> None:
        """An agent pty spawned or resolved: every subscriber is sent its
        row, and the item of the session it runs its `running`."""
        event = self.table_row(pty_id)
        if event is None:
            return
        self._broadcast(event)
        if self.feed is not None and event.get("session"):
            self.feed.refresh_running({event["session"]})

    # -- restart when idle (§3.10 item 1, PR-1.12c)

    # How often a restart that waits for every session to be idle looks.
    RESTART_POLL_MS = 2000

    def restart_when_idle(self, restart: Callable[[], None]) -> None:
        """Call *restart* once no session is busy (the tracker's
        `busy_count`): looked at now, then every `RESTART_POLL_MS`. A
        second ask replaces the first; `cancel_restart` calls it off."""
        from gi.repository import GLib

        self.cancel_restart()
        self._restart_waiting = restart
        if self._restart_tick():
            self._restart_source = GLib.timeout_add(self.RESTART_POLL_MS, self._restart_tick)

    def _restart_tick(self) -> bool:
        busy = self.activity.busy_count() if self.activity is not None else 0
        if busy:
            return True
        restart, self._restart_waiting = self._restart_waiting, None
        self._restart_source = 0
        if restart is not None:
            log.info("restart when idle: no session is busy; restarting")
            try:
                restart()
            except Exception:
                log.exception("restart when idle: the restart failed")
        return False

    def cancel_restart(self) -> bool:
        """Call off a restart waiting for the sessions to be idle; True
        when one was waiting."""
        from gi.repository import GLib

        waiting = self._restart_waiting is not None
        self._restart_waiting = None
        if self._restart_source:
            GLib.source_remove(self._restart_source)
            self._restart_source = 0
        return waiting

    @property
    def restart_pending(self) -> bool:
        """Whether a restart waits for the sessions to be idle."""
        return self._restart_waiting is not None

    # -- stopping (§3.10 item 1)

    def stop_sessions(self, done: Callable[[], None] | None = None) -> list[str]:
        """End every session the way *Stop sessions and quit* does (§3.10
        item 1): the ids of the live agent sessions are recorded as
        `resume_on_start` (a client reopening its tabs finds them resumable;
        the next service resumes nothing by itself); each agent session
        runs its graceful close flow (the provider's exit keystrokes, the
        nudges, the worktree dialog answered, its budget, then the pty
        closed: SIGHUP, and SIGKILL after `CLOSE_GRACE_MS`); once every
        agent pty has exited, or `STOP_BOUND_S` has passed, `shutdown()`
        closes what is left and *done* is called. The main loop keeps
        running meanwhile (the flows and the grace are its timers). Returns
        the ids recorded."""
        ids: list[str] = []
        for record in self._records():
            session_id = getattr(record.session, "session_id", None)
            if session_id:
                ids.append(str(session_id))
        if self.state is not None:
            try:
                self.state.set_resume_on_start(ids)
            except Exception:
                log.exception("could not record the sessions to resume")
        for record in self._records():
            pty_id = record.pty_id
            if pty_id is None or pty_id not in self.ptys.ptys:
                continue
            session = record.session
            exit_text = None
            try:
                exit_text = session.provider.graceful_exit()
            except Exception:  # noqa: BLE001 - a provider's failure is not the stop's
                log.exception("stop: no exit keystrokes for %s", record.handle)
            force = self._force_close_for(pty_id)
            if exit_text and session.has_running_command():
                try:
                    session.begin_close(exit_text, False, force)
                    continue
                except Exception:  # noqa: BLE001
                    log.exception("stop: the close flow of %s failed", record.handle)
            force()
        deadline = time.monotonic() + STOP_BOUND_S

        def settled() -> bool:
            live = [r for r in self._records() if r.pty_id in self.ptys.ptys]
            if live and time.monotonic() < deadline:
                return GLib.SOURCE_CONTINUE
            if live:
                log.warning(
                    "stop: %d session(s) still up after %.0f s; closing them", len(live), STOP_BOUND_S
                )
            self.shutdown()
            if done is not None:
                done()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(100, settled)
        return ids

    def _force_close_for(self, pty_id: int) -> Callable[[], None]:
        def force() -> None:
            record = self.sessions.get(pty_id)
            if record is not None:
                record.session.end_close()
            try:
                self.ptys.close(pty_id)
            except KeyError:
                pass

        return force

    def shutdown(self) -> None:
        self.cancel_restart()
        self.git.shutdown()
        self.files.shutdown()
        if self.background is not None:
            self.background.stop()
        if self._sweep_source:
            from gi.repository import GLib

            GLib.source_remove(self._sweep_source)
            self._sweep_source = 0
        if self.activity is not None:
            self.activity.stop()
        self.ptys.shutdown()
        if self.sandbox_grants is not None:
            # Every directory mounted into a running box, unmounted; the
            # bindfs servers would go with the process anyway
            # (PR_SET_PDEATHSIG), this leaves nothing to chance.
            self.sandbox_grants.shutdown()
            self.sandbox_grants = None


def _spawn_refusal(message: protocol.Message, exc: ptyserver.SpawnError) -> dict:
    return protocol.refuse(
        message.id,
        protocol.ERROR_FAILED,
        "failed to start shell: {msg}",
        {"msg": f"{exc.strerror}: {exc.filename}"},
    )


def transcript_path_allowed(path: object) -> bool:
    """Whether *path* is a transcript the service may tail for a client
    (rule 5): a `.jsonl` under the CLI's projects directory
    (`COLLINS_PROJECTS_DIR`) or the chats directory, after symlinks."""
    if not isinstance(path, str) or not path.endswith(".jsonl"):
        return False
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError):
        return False
    for root in (sessions.CLAUDE_PROJECTS_DIR, chats.CHATS_DIR):
        try:
            base = os.path.realpath(str(root))
        except (OSError, ValueError):
            continue
        if real.startswith(base.rstrip(os.sep) + os.sep):
            return True
    return False


def _jsonable(value):
    """*value* as the probe's reply carries it: JSON as it is, a set or a
    tuple as a list, a path as text, a dataclass as its fields, anything
    else None."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (set, frozenset)):
        return [_jsonable(v) for v in sorted(value, key=str)]
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        import dataclasses

        return {k: _jsonable(v) for k, v in dataclasses.asdict(value).items()}
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return None
    return value


def _within(root: str, path: str) -> bool:
    """Whether *path* is *root* itself or something under it. Purely lexical."""
    root, path = os.path.normpath(root), os.path.normpath(path)
    return path == root or path.startswith(root + os.sep)
