# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""`ServiceCore`: the service's request router, the pty half.

The service of the split (spec §3.1) is one object the transport hands
validated messages to and takes events back from. This is its first half:
the pty table (`ptyserver.PtyServer`) and the requests that reach it
(`spawn`, `attach`, `detach`, `paint`, `close`) and the events that come
from a client about it (`resize`, `focus`, `theme`). The store, the state,
PRs, notifications and the tools join in PR-1.10 and PR-1.11. GLib only;
nothing here imports GTK.

A client is anything the transport represents as a `Client`: it has a
`device` name, a sink per pty (`sink_for(pty)`: the object the pty server
hands output and events to) and `forget(pty)` for when a pty is gone. The
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
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from ..api import protocol
from . import ptyserver, termstream

log = logging.getLogger(__name__)


class Client(Protocol):
    """What the core needs of a connected client (the transport's object)."""

    device: str

    def sink_for(self, pty: int):
        """The sink the pty server hands this client's output and events
        for *pty* (one object per client and pty, kept for the attachment's
        life)."""
        ...

    def forget(self, pty: int) -> None:
        """The pty is gone (exited or closed): drop its sink."""
        ...


class ServiceCore:
    """The router. See the module docstring."""

    def __init__(
        self,
        *,
        state_dir: Path | None = None,
        record: Callable[[int, dict | None], None] | None = None,
        record_next_id: Callable[[int], None] | None = None,
        next_id: int = 1,
        get_setting: Callable[[str], Any] = lambda key: None,
        environment: Callable[[], dict[str, str]] | None = None,
        on_stream_event: Callable[[int, object], None] | None = None,
    ) -> None:
        """*record*, *record_next_id* and *next_id* are `AppState.set_pty`,
        `AppState.set_pty_next_id` and `AppState.pty_next_id` (the pty table
        of §3.8); *get_setting* reads the service's settings (only
        ``progress_termprop`` so far); *environment* is the session
        environment of §3.10 (the process's own by default); *on_stream_event*
        hears every stream event (progress, bell, title, modes) off every
        pty, for the service's own detection (§3.6, PR-1.10)."""
        self._get_setting = get_setting
        self._environment = environment or (lambda: dict(os.environ))
        self.ptys = ptyserver.PtyServer(
            state_dir=state_dir,
            record=record,
            record_next_id=record_next_id,
            next_id=next_id,
            on_event=on_stream_event,
        )
        self._clients: set[int] = set()  # id(client)

    # -- clients

    def client_connected(self, client: Client) -> None:
        self._clients.add(id(client))

    def client_gone(self, client: Client) -> None:
        """A client went away however it went: nothing changes on the
        service but "no client is attached" (§3.1 rule 5)."""
        self._clients.discard(id(client))
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
        # The two declarations that coax the CLI's progress announcements
        # out (session.agent_environment says why), unless the experimental
        # setting is off: decided at spawn, as the VTE path decided it.
        progress = self._get_setting("progress_termprop") in (None, True)
        env = dict(self._environment())
        cols = message.get("cols") or ptyserver.termscreen.DEFAULT_COLS
        rows = message.get("rows") or ptyserver.termscreen.DEFAULT_ROWS
        try:
            pty_id = self.ptys.spawn(
                message.get("kind"),
                [shell],
                message.get("cwd"),
                env,
                cols,
                rows,
                session=message.get("session"),
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
        pty = self.ptys.get(pty_id)
        return protocol.reply(message.id, pty=pty_id, cols=pty.cols, rows=pty.rows)

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

    # -- client events

    def _ev_resize(self, event: protocol.Message, client: Client) -> None:
        pty_id = event.get("pty")
        self.ptys.resize(pty_id, event.get("cols"), event.get("rows"), sink=client.sink_for(pty_id))

    def _ev_focus(self, event: protocol.Message, client: Client) -> None:
        pty_id = event.get("pty")
        self.ptys.focus(pty_id, client.sink_for(pty_id), bool(event.get("focused")))

    def _ev_theme(self, event: protocol.Message, client: Client) -> None:
        term = event.get("term") or {}
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

    def shutdown(self) -> None:
        self.ptys.shutdown()
