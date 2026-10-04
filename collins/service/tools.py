# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""The session tools, served by the service (split-service spec §3.7).

Every launched session's MCP shim relays its tool calls over the service's
Unix socket (`mcpserver.SessionToolService`, `mcptools`' framing and
schemas); this module is what that socket service dispatches to. It used to
be `App._mcp_*`, where dispatch needed every window and every tab. It needs
neither now: a call binds to a **Session** (`service.session.Session`, by
the shim's kernel-verified pid walked up `/proc`: `find`), and what a
session is offered, refused and told is decided here, from the service's
own records.

**What does not move** (§5's third rule: nothing a sandboxed session is
offered or refused changes as a side effect of the move). The skeleton is
`mcptools.run_tool_call` (validate, the Preferences switch, identity, the
caller's own list, the handler), unchanged; `tool_offered` is
`App._mcp_tool_offered` word for word over the Session instead of its tab
(`Session.sandboxed`, `Session.sandbox_box`, what the tab forwarded to);
`list_tools` is `App._mcp_list_tools`; the switch is read off the service's
settings, which are the ones Preferences writes; and every handler gets the
same `sandboxed` reading of its caller, which the halves that reach the
host apply the policy on (`mcptools.tool_shells`, `sibling_sandboxed`, the
derived plan).

**Where each tool runs.** With a client attached, a tool that needs a
person's screen is a `tool` event to the session's **active client** (D20:
that one only; `ServiceCore._tool_client` picks it) and a
`mcptools.DeferredResult` that resolves on the client's `tool-reply` or at
`TOOL_BOUND_S`, under the shim's own 15 s call timeout. On the loopback the
client answers inside the event, so a call that finishes at once still
returns its ``(ok, text)`` at once (`_settled`). The table, as implemented:

====================  ====================================  ===========================================
Tool                  With a client attached                With no client attached
====================  ====================================  ===========================================
set_session_title     the service's store renames; the      the same
                      client's tab title follows its mirror
attach_pr             the session's own PR list             the same
                      (`Session.attach_pr`)
start_session         the active client's window spawns     refused: in Phase 1 a session's logic
                      the sibling (a new tab)               runs in its client's tab (escalated)
read_terminal,        the active client (Phase 1: the       the session's shell ptys on the pty server,
run_in_terminal       vte backend's shells are widgets)     read through the model; a new one spawned
show_image            the lightbox, recorded too            recorded as an attachment; the reply says
                                                            nobody is looking
notify_user           the client's delivery table; the      recorded in the history, the row unread and
                      row is the service's (notify.post)    the session flagged (the sink is PR-3.4's)
open_in_editor        the editor                            "no client attached"
show_diff             the git page                          recorded as the session's pending load
                                                            (``pending_diffs``), applied when a client
                                                            subscribes; the reply waits up to
                                                            `PENDING_DIFF_WAIT_S`, then "queued"
diff_context,         the git page (its loaded diff), the   the service's `diffnotes` store over its
annotate_diff, ...    marks written through to the          own read of the session's diff
                      service's store
====================  ====================================  ===========================================

Error strings and replies are agent-facing English, as they always were
(§3.14: tool replies to the agent stay English). GLib only; nothing here
imports GTK.
"""

from __future__ import annotations

import itertools
import logging
import os
from collections.abc import Callable, Iterable
from typing import Any

from .. import attachrecords, editorfiles, mcptools, notifycenter, proctree, remoteimages
from ..prstatus import parse_pr_url
from ..shellinput import shell_command

log = logging.getLogger(__name__)

# How long a UI-bound call waits for its client's reply: under the shim's
# own call timeout (mcp_shim._CALL_TIMEOUT, 15 s), so the agent hears
# Collins' words rather than a transport timeout. The client's own
# deadlines (a show_diff's load, a start_session's spawn) are shorter.
TOOL_BOUND_S = 14.0
# How long a show_diff with no client attached waits for one before
# answering "queued" (§3.7: up to 10 s).
PENDING_DIFF_WAIT_S = 10.0

# The tools a session's own data serves, whoever is attached.
SERVICE_TOOLS = frozenset({"set_session_title", "attach_pr"})

NOT_RESOLVED = "The session isn't resolved in Collins yet — try again in a moment"
NO_CLIENT = "No Collins window is open to show that in"
NO_ANSWER = "Collins didn't answer in time; the request may still land"
SHOW_IMAGE_RECORDED = (
    "No Collins window is open: the image is recorded in the session's attachments for when one is."
)
NOTIFY_RECORDED = (
    "No Collins window is open: the message is in the user's notification history, unread."
)
START_NEEDS_CLIENT = "No Collins window is open to start a session in"


def _settled(result: mcptools.ToolResult) -> mcptools.ToolResult:
    """A DeferredResult that is already resolved, as its ``(ok, text)``: the
    shape a handler that finished at once always answered with."""
    if isinstance(result, mcptools.DeferredResult) and result.resolved:
        return result._result  # noqa: SLF001 - the one reader of a settled promise
    return result


class _Call:
    __slots__ = ("id", "deferred", "client", "timer", "name")

    def __init__(self, call_id: str, deferred: mcptools.DeferredResult, client, name: str):
        self.id = call_id
        self.deferred = deferred
        self.client = client
        self.timer = 0
        self.name = name


class SessionTools:
    """The dispatcher the socket service is handed. See the module docstring.

    *get_setting* reads the service's settings; *sessions* lists every
    session the service holds (`service.session.Session`s; through Phase 1
    the tabs' own, the app's lookup); *sandbox_host* is the SandboxHost or
    None; *send_tool(session, event)* hands a `tool` event to the session's
    active client and answers which client took it (None: no client is
    attached); *store* is the service's SessionStore, *state* its AppState,
    *ptys* its PtyServer, *notifications* its `notifications.
    ServiceNotifications` and *diffs* its `diffs.DiffNotes` (each optional:
    a tool that needs a missing one answers as it would with no client).
    *timeout_add* / *source_remove* are GLib's (a test's fake clock)."""

    def __init__(
        self,
        *,
        get_setting: Callable[[str], Any],
        sessions: Callable[[], Iterable[Any]],
        sandbox_host: Callable[[], Any] = lambda: None,
        send_tool: Callable[[Any, dict], Any] = lambda session, event: None,
        store=None,
        state=None,
        ptys=None,
        notifications=None,
        diffs=None,
        shell_spawner: Callable[[Any, bool], int | None] | None = None,
        timeout_add: Callable[..., int] | None = None,
        source_remove: Callable[[int], Any] | None = None,
    ) -> None:
        self._get_setting = get_setting
        self._sessions = sessions
        self._host = sandbox_host
        self._send_tool = send_tool
        self.store = store
        self.state = state
        self.ptys = ptys
        self.notifications = notifications
        self.diffs = diffs
        self._spawn_shell = shell_spawner
        if timeout_add is None or source_remove is None:
            from gi.repository import GLib

            timeout_add = timeout_add or GLib.timeout_add
            source_remove = source_remove or GLib.source_remove
        self._timeout_add = timeout_add
        self._source_remove = source_remove
        self._ids = itertools.count(1)
        self._calls: dict[str, _Call] = {}
        # show_diff calls made with no client attached, waiting for one:
        # session key -> the deferred replies to settle when it comes.
        self._waiting_diffs: dict[str, list[tuple[mcptools.DeferredResult, int]]] = {}

    # -- who is calling, and what it is offered ---------------------------------

    def find(self, shim_pid: int):
        """The Session whose terminal the calling shim descends from, or
        None (a daemon-hosted background job, a chat session, a session
        since closed). Resolved fresh per call: pids recycle."""
        ancestors = proctree.ancestor_pids(shim_pid)
        for session in list(self._sessions()):
            if session.owns_pid_ancestors(ancestors):
                return session
        return None

    def tool_enabled(self, name: str) -> bool:
        """Whether the user leaves this tool switched on (Preferences →
        Session tools). Read per list and per call, never cached: a session
        keeps the tool list it was handed at startup, so the switch reaching
        a running session at all depends on this being asked again."""
        return bool(self._get_setting(mcptools.tool_setting_key(name)))

    def tool_offered(self, session, name: str) -> bool:
        """Whether the calling session is offered the tool: always, for an
        unsandboxed one; for a sandboxed one, what its box's switches and
        the defaults for sandboxed sessions say (SandboxHost.tool_enabled).
        Whether the session is sandboxed, and which box is its, are the
        service's own records of the launch — the caller is only ever the
        pid the kernel named."""
        if not session.sandboxed:
            return True
        host = self._host()
        if host is None:
            return mcptools.sandbox_tool_enabled(
                name, self._get_setting(mcptools.sandbox_tool_setting_key(name)), {}
            )
        return host.tool_enabled(session.sandbox_box, name)

    def list_tools(self, pid: int) -> list[dict]:
        """The tools the session whose shim is *pid* is told about: the ones
        switched on, less — for a sandboxed session — the ones it isn't
        offered. A caller no session owns gets the first list; it can call
        none of them."""
        found = self.find(pid)
        return mcptools.enabled_tools(
            lambda name: self.tool_enabled(name) and (found is None or self.tool_offered(found, name))
        )

    def dispatch(self, pid: int, tool: str, args: object) -> mcptools.ToolResult:
        """Run one tool call from the shim at *pid*: (ok, message-or-error),
        or a DeferredResult (see the module docstring)."""
        return _settled(
            mcptools.run_tool_call(
                tool,
                args,
                find_tab=lambda: self.find(pid),
                handlers={name: self._handler(name) for name in mcptools.tool_names()},
                is_enabled=self.tool_enabled,
                is_sandboxed=lambda session: session.sandboxed,
                is_offered=self.tool_offered,
            )
        )

    def _handler(self, name: str):
        own = getattr(self, "_tool_" + name, None)
        if name in SERVICE_TOOLS and own is not None:
            return own

        def ui(session, args: dict, sandboxed: bool = False) -> mcptools.ToolResult:
            forwarded = self._forward(session, name, args, sandboxed)
            if forwarded is not None:
                return forwarded
            headless = getattr(self, "_headless_" + name, None)
            if headless is None:
                return False, NO_CLIENT
            return headless(session, args, sandboxed)

        return ui

    # -- the active client ----------------------------------------------------

    def _forward(self, session, name: str, args: dict, sandboxed: bool) -> mcptools.ToolResult | None:
        """Hand the call to the session's active client as a `tool` event;
        None when no client is attached."""
        call_id = f"call-{next(self._ids)}"
        deferred = mcptools.DeferredResult()
        event = {
            "t": "tool",
            "call": call_id,
            "session": session.session_id or "",
            "handle": session.handle,
            "name": name,
            "arguments": dict(args),
            "sandboxed": bool(sandboxed),
        }
        call = _Call(call_id, deferred, None, name)
        self._calls[call_id] = call
        client = self._send_tool(session, event)
        if client is None:
            self._calls.pop(call_id, None)
            return None
        if deferred.resolved:
            return _settled(deferred)
        call.client = client
        call.timer = self._timeout_add(int(TOOL_BOUND_S * 1000), self._on_bound, call_id)
        return deferred

    def reply(self, call_id: str, ok: bool, text: str, client=None) -> bool:
        """A client's `tool-reply`: resolve the call it answers. A reply
        from a client the call was not sent to, or for a call already
        settled, is dropped (False)."""
        call = self._calls.get(call_id)
        if call is None or (call.client is not None and client is not None and call.client is not client):
            return False
        del self._calls[call_id]
        if call.timer:
            self._source_remove(call.timer)
            call.timer = 0
        call.deferred.resolve(bool(ok), str(text))
        return True

    def _on_bound(self, call_id: str) -> bool:
        call = self._calls.pop(call_id, None)
        if call is not None:
            call.timer = 0
            call.deferred.resolve(False, NO_ANSWER)
        return False

    def client_gone(self, client) -> None:
        """A client went away with calls of its unanswered: they answer now
        rather than at the bound."""
        for call_id, call in list(self._calls.items()):
            if call.client is client:
                self._calls.pop(call_id, None)
                if call.timer:
                    self._source_remove(call.timer)
                call.deferred.resolve(False, "The Collins window answering this went away")

    # -- the session's own data -------------------------------------------------

    def _tool_set_session_title(self, session, args: dict, sandboxed: bool = False):
        if not session.session_id:
            return False, NOT_RESOLVED
        if self.store is None:
            return False, NO_CLIENT
        self.store.rename(session.session_id, args["title"])
        return True, "Session renamed."

    def _tool_attach_pr(self, session, args: dict, sandboxed: bool = False):
        """Put a PR on the calling session's row without a gh call: the
        number and repository are read off the URL here and the session's
        own update thread fetches title and status right after (the PR hub
        persists it, keyed by session id — hence the resolution guard)."""
        if not session.session_id:
            return False, NOT_RESOLVED
        pr = parse_pr_url(args["url"])
        if pr is None:
            return False, f"Not a GitHub pull request URL: {args['url']}"
        if not session.attach_pr(pr):
            return True, f"{pr.slug} is already attached to this session."
        return True, f"Attached {pr.slug} to this session."

    # -- with no client attached (§3.7's last column) ----------------------------

    def _headless_open_in_editor(self, session, args: dict, sandboxed: bool = False):
        return False, NO_CLIENT

    def _headless_start_session(self, session, args: dict, sandboxed: bool = False):
        return False, START_NEEDS_CLIENT

    def _headless_show_image(self, session, args: dict, sandboxed: bool = False):
        """Recorded as an attachment (the record a lightbox sighting writes:
        a local image by its path, a remote one by its URL), so the gallery
        has it when a client opens the session."""
        raw = args["path"]
        if remoteimages.looks_remote(raw):
            error = remoteimages.url_error(raw)
            if error is not None:
                return False, error
            key = raw
        else:
            key = resolve_file(session, raw)
            if key is None:
                return False, f"No such file: {raw}"
            if not editorfiles.is_image_path(key):
                return False, f"Not an image Collins can display: {raw}"
        if not session.session_id or self.state is None:
            return False, NO_CLIENT
        one = attachrecords.sighting(
            key, source=attachrecords.LIGHTBOX, caption=args.get("caption"), origin=raw
        )
        if one is None:
            return False, f"Not an image Collins can display: {raw}"
        saved = attachrecords.from_records(self.state.get_session_attachments(session.session_id))
        everything = attachrecords.fold({a.key: a for a in saved}, one)
        self.state.set_session_attachments(session.session_id, attachrecords.to_records(everything.values()))
        self.state.save()
        return True, SHOW_IMAGE_RECORDED

    def _headless_notify_user(self, session, args: dict, sandboxed: bool = False):
        """Recorded in the history, unread, and the session's row flagged,
        as the delivery table does for a user away from Collins (the desktop
        notification and the sound have nobody to reach; the command sink
        of §3.13 is PR-3.4's)."""
        if self.notifications is None:
            return False, "Collins couldn't post a notification"
        title, project = self._identity(session)
        self.notifications.post(
            notifycenter.KIND_MESSAGE,
            session.session_id or "",
            title,
            project,
            args["message"],
            None,
        )
        if session.session_id and self.store is not None:
            self.store.set_unread(session.session_id, True)
        return True, NOTIFY_RECORDED

    def _identity(self, session) -> tuple[str, str]:
        item = None
        if self.store is not None and session.session_id:
            item = self.store.get_item(session.session_id)
        if item is not None:
            return item.display_name, item.session.project_name
        from ..sessions import project_name_for_cwd

        cwd = session.current_agent_cwd()
        return "", project_name_for_cwd(cwd) if cwd else ""

    def _headless_show_diff(self, session, args: dict, sandboxed: bool = False):
        """The session's pending load (``pending_diffs``), applied when a
        client subscribes (`apply_pending_diffs`); the reply waits up to
        PENDING_DIFF_WAIT_S for that, then says it is queued."""
        key = session.session_id
        if not key or self.state is None:
            return False, NO_CLIENT
        self.state.set_pending_diff(key, dict(args))
        self.state.save()
        deferred = mcptools.DeferredResult()
        timer = self._timeout_add(int(PENDING_DIFF_WAIT_S * 1000), self._on_diff_wait_over, key, deferred)
        self._waiting_diffs.setdefault(key, []).append((deferred, timer))
        return deferred

    def _on_diff_wait_over(self, key: str, deferred: mcptools.DeferredResult) -> bool:
        waiting = self._waiting_diffs.get(key, [])
        self._waiting_diffs[key] = [(d, t) for d, t in waiting if d is not deferred]
        if not self._waiting_diffs[key]:
            del self._waiting_diffs[key]
        deferred.resolve(
            True,
            "Queued: no Collins window is open; the git page opens on that diff when one does.",
        )
        return False

    def apply_pending_diffs(self, client) -> int:
        """A client subscribed: hand it every session's pending show_diff as
        a `tool` event (its own reply settles a call still waiting), and
        drop the record once it is handed. Returns how many went."""
        if self.state is None:
            return 0
        pending = self.state.get_pending_diffs()
        sent = 0
        for key, args in list(pending.items()):
            session = next((s for s in self._sessions() if s.session_id == key), None)
            if session is None:
                continue
            result = self._forward(session, "show_diff", dict(args), bool(session.sandboxed))
            if result is None:
                continue
            sent += 1
            self.state.set_pending_diff(key, None)
            waiting = self._waiting_diffs.pop(key, [])

            def settle(ok: bool, text: str, waiting=waiting) -> None:
                for deferred, timer in waiting:
                    self._source_remove(timer)
                    deferred.resolve(ok, text)

            if isinstance(result, mcptools.DeferredResult):
                result.watch(settle)
            else:
                settle(*result)
        if sent:
            self.state.save()
        return sent

    def _headless_diff_context(self, session, args: dict, sandboxed: bool = False):
        if self.diffs is None:
            return False, NO_CLIENT
        return self.diffs.context(session, args)

    def _headless_annotate_diff(self, session, args: dict, sandboxed: bool = False):
        if self.diffs is None:
            return False, NO_CLIENT
        return self.diffs.annotate(session, args)

    def _headless_highlight_diff(self, session, args: dict, sandboxed: bool = False):
        if self.diffs is None:
            return False, NO_CLIENT
        return self.diffs.highlight(session, args)

    def _headless_clear_diff_marks(self, session, args: dict, sandboxed: bool = False):
        if self.diffs is None:
            return False, NO_CLIENT
        return self.diffs.clear(session, args)

    # -- the terminal tools, headless: the session's shell ptys ------------------

    def _shell_ptys(self, session, sandboxed: bool) -> list:
        """The session's panel shells on the pty server, oldest first: the
        ptys of kind shell filed under its history key. A sandboxed session
        reaches only the shells of its own box, launched on the plan it
        runs on (mcptools.tool_shells' rule, by the pty's records)."""
        if self.ptys is None or not session.session_id:
            return []
        shells = [
            pty
            for pty_id, pty in sorted(self.ptys.ptys.items())
            if pty.kind == "shell" and pty.history == session.session_id
        ]
        if sandboxed:
            shells = [pty for pty in shells if pty.box and pty.plan and pty.plan == session.sandbox_plan_path]
        return shells

    def _headless_read_terminal(self, session, args: dict, sandboxed: bool = False):
        shells = self._shell_ptys(session, sandboxed)
        if not shells:
            if sandboxed:
                return True, "No sandboxed shells are open in this session."
            return True, "No terminal-panel tabs are open in this session."
        numbered = list(enumerate(shells, start=1))
        wanted = args.get("terminal")
        if wanted is not None:
            chosen = [(n, pty) for n, pty in numbered if n == wanted]
            if not chosen:
                numbers = ", ".join(str(n) for n, _pty in numbered)
                kind = "sandboxed shell" if sandboxed else "terminal"
                return False, f"No {kind} numbered {wanted} — open: {numbers}"
            numbered = chosen
        sections = [
            (
                number,
                pty.has_running_command(),
                pty.screen.capture_contents(),
                "Sandboxed shell" if pty.box else "Terminal",
            )
            for number, pty in numbered
        ]
        return True, mcptools.terminal_reply(sections, args.get("lines", mcptools.TERMINAL_DEFAULT_LINES))

    def _headless_run_in_terminal(self, session, args: dict, sandboxed: bool = False):
        shells = self._shell_ptys(session, sandboxed)
        kind = "Sandboxed shell" if sandboxed else "Terminal"
        wanted = args.get("terminal")
        numbered = list(enumerate(shells, start=1))
        opened = False
        if wanted is not None:
            target = next(((n, pty) for n, pty in numbered if n == wanted), None)
            if target is None:
                numbers = ", ".join(str(n) for n, _pty in numbered)
                return False, f"No {kind.lower()} numbered {wanted} — open: {numbers or 'none'}"
            if target[1].has_running_command():
                return False, (
                    f"{kind} {wanted} is busy running a command — pick an "
                    "idle one, or omit 'terminal' to open a new tab"
                )
        else:
            target = next(((n, pty) for n, pty in numbered if not pty.has_running_command()), None)
            if target is None:
                pty_id = self._spawn_shell(session, sandboxed) if self._spawn_shell is not None else None
                if pty_id is None:
                    return False, "Collins couldn't open a terminal in this session"
                target = (len(numbered) + 1, self.ptys.get(pty_id))
                opened = True
        self.ptys.write(target[1].id, shell_command(args["command"].rstrip("\n") + "\n").encode())
        prefix = "Running in new" if opened else "Running in"
        return True, f"{prefix} {kind} {target[0]}."


def resolve_file(session, raw: str) -> str | None:
    """The existing file a tool's path argument names, or None. Relative
    paths try the running agent's cwd first (it may have cd'd into a
    worktree), then the session's launch directory — the order clickable
    file references resolve in."""
    expanded = os.path.expanduser(raw)
    if os.path.isabs(expanded):
        trials = [expanded]
    else:
        roots = [session.current_agent_cwd(), session.cwd]
        trials = [os.path.join(root, expanded) for root in roots if root]
    for trial in trials:
        if os.path.isfile(trial):
            return os.path.normpath(trial)
    return None
