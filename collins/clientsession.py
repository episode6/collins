# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""The client's mirror of one service session (spec §3.19, PR-1.12a).

Since PR-1.12a a session tab's `Session` lives on the service
(`service.session`, hosted by `service.hosting.SessionRecord`); the tab
holds a `ClientSession`: the last `session` event's fields, under the
attribute names `terminal.TerminalTab`'s forwarders always read
(`session_id`, `fork`, `provider`, `options`, `cwd`, `sandboxed`,
`sandbox_box`, `sandbox_plan_path`, ...), the reads that answer from them
with no round trip (`takes_prompt`, `prompt_block`, `entered_prompt`,
`unstarted_thread`, `has_running_command`, `agent_is_running`,
`current_agent_cwd`, `current_model`, ...: D28), and the requests the tab
makes of its session (`inject_prompt`, `switch_model`, `send_composed`,
`begin_cut`, `restore_draft`, `begin_close`, ...), each a message of
`api.protocol` through the tab's client. **A client never derives one of
these from its own VTE.**

`apply(event)` takes a `session` event and answers the names of the fields
that moved; the tab turns those into its GObject signals
(``session-resolved``, ``fork-resolved``, ``transcript-updated``,
``process-exited``), so `window.py`'s handlers stay. The one-shots
(`landed`, `reset`, `finished`, `forked`) come back in the set each time
they are sent.

`probe`, `probe_set` and `probe_call` are the e2e checks' door past the
protocol (D27): the ``debug.*`` requests, served only by a service running
with ``COLLINS_DEBUG_API=1``.

GTK-free (the unit suite drives it through a fake client).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from . import editorfiles
from .api.loopback import RequestRefused
from .i18n import _
from .providers import EnteredPrompt, Provider, SessionOptions, options_from_record

log = logging.getLogger(__name__)

# The fields of the `session` event a mirror holds, with what each is before
# the first event lands (the identity fields are seeded from the tab's own
# arguments by `__init__`).
_FIELD_DEFAULTS: dict[str, Any] = {
    "agent_cwd": None,
    "pid": None,
    "resolver_cwd": None,
    "initial_command": None,
    "worktree_launch": False,
    "new_chat_prompt": None,
    "shells_follow_armed": False,
    "closing": False,
    "sandboxed": False,
    "sandbox_box": "",
    "sandbox_plan_path": None,
    "defaults_owed": False,
    "can_restart": False,
    "takes_prompt": False,
    "entered": None,
    "prompt_block": "",
    "unstarted": False,
    "foreign_paste": False,
    "agent_running": False,
    "running_command": False,
    "pasted_back": {},
    "paste_back_pending": None,
    "model": None,
    "effort": None,
    "permission_mode": None,
    "transcript_path": None,
    "prs": [],
    "lookup_empty": False,
    "touched_files": [],
    "attachments": [],
    "ledger_armed": False,
    "busy": False,
}
_ONE_SHOTS = ("landed", "reset", "finished", "forked")


class ClientSession:
    """See the module docstring. *request* sends a request through the
    tab's client and answers the reply's fields (raising `RequestRefused`);
    the identity arguments are what the tab was opened with, shown until
    the service's first `session` event replaces them."""

    def __init__(
        self,
        *,
        request: Callable[[dict], dict],
        provider: Provider,
        session_id: str | None = None,
        fork: bool = False,
        options: SessionOptions | None = None,
        command_override: str | None = None,
        cwd: str | None = None,
        jsonl_path: str | None = None,
    ) -> None:
        self._request = request
        self.provider = provider
        self.pty: int | None = None
        self.handle: str | None = None
        self.session_id = session_id
        self.fork = bool(fork)
        self.options = options
        self.command_override = command_override
        self.cwd = cwd
        self.transcript_path: str | None = str(jsonl_path) if jsonl_path else None
        self.exited = False
        for name, value in _FIELD_DEFAULTS.items():
            if name == "transcript_path":
                continue
            if isinstance(value, dict):
                value = dict(value)
            elif isinstance(value, list):
                value = list(value)
            setattr(self, name, value)
        self.forked: str | None = None

    # -- the event -------------------------------------------------------------------

    def apply(self, event: dict) -> set[str]:
        """Take a `session` event's fields; the names of the ones that moved
        (the one-shots whenever they are sent)."""
        if self.pty is not None and event.get("pty") not in (None, self.pty):
            return set()
        changed: set[str] = set()
        if event.get("handle") and event.get("handle") != self.handle:
            self.handle = event["handle"]
            changed.add("handle")
        if event.get("pty") and self.pty is None:
            self.pty = int(event["pty"])
        for name, value in event.items():
            if name in ("t", "pty", "handle"):
                continue
            if name in _ONE_SHOTS:
                if value:
                    changed.add(name)
                    if name == "forked":
                        self.forked = str(value)
                continue
            if name == "session":
                value = str(value) if value else None
                attr = "session_id"
            elif name == "options":
                value = options_from_record(value)
                attr = "options"
            elif name == "provider":
                continue  # the provider object is the tab's, built from the id
            else:
                attr = name
            if getattr(self, attr, _MISSING) != value:
                setattr(self, attr, value)
                changed.add(attr)
        return changed

    def exit(self) -> None:
        """The pty exited (`pty-exited`): every read answers as with no child."""
        self.exited = True
        self.pid = None
        self.takes_prompt = False
        self.entered = None
        self.running_command = False
        self.agent_running = False
        self.unstarted = False

    # -- the reads (no round trip) ------------------------------------------------------

    def takes_prompt_now(self) -> bool:
        """Whether a prompt sent right now would land in an empty input box."""
        return bool(self.takes_prompt) and not self.exited

    def prompt_block_text(self) -> str:
        """Why a prompt sent to this session wouldn't land, translated, or ""."""
        if self.exited:
            return _("This session isn't at an empty prompt.")
        return _(self.prompt_block) if self.prompt_block else ""

    def entered_prompt(self) -> EnteredPrompt | None:
        entered = self.entered
        if self.exited or not isinstance(entered, dict):
            return None
        return EnteredPrompt(text=str(entered.get("text", "")), rows_below=int(entered.get("rows_below", 0)))

    def unstarted_thread(self) -> bool:
        return bool(self.unstarted) and not self.exited

    def has_running_command(self) -> bool:
        return bool(self.running_command) and not self.exited

    def agent_is_running(self) -> bool:
        return bool(self.agent_running) and not self.exited

    def foreign_paste_in_box(self) -> bool:
        return bool(self.foreign_paste) and not self.exited

    def current_agent_cwd(self) -> str | None:
        return self.agent_cwd or self.cwd

    def current_model(self) -> str:
        return self.model or (self.options.model if self.options else "")

    def current_effort(self) -> str:
        return self.effort or (self.options.effort if self.options else "")

    def current_permission_mode(self) -> str:
        return self.permission_mode or (self.options.permission_mode if self.options else "")

    def can_restart_sandboxed(self) -> bool:
        return bool(self.can_restart)

    def child_pid(self) -> int | None:
        return None if self.exited else self.pid

    # -- the requests ----------------------------------------------------------------------

    def _ask(self, message: dict, **fields) -> dict | None:
        """A request about this session's pty; its reply, or None when
        there is no pty yet or the service refused (logged)."""
        if self.pty is None or self.exited:
            return None
        message = {**message, "pty": self.pty, **fields}
        try:
            return self._request(message)
        except RequestRefused as refusal:
            log.info("session %s: %s refused: %s", self.handle, message.get("t"), refusal.msgid)
            return None
        except ValueError as error:
            log.error("session %s: %s is not a valid request: %s", self.handle, message.get("t"), error)
            return None

    def write_text(self, text: str) -> None:
        if text:
            self._ask({"t": "write"}, text=text)

    def inject_prompt(self, text: str) -> None:
        if text:
            self._ask({"t": "prompt"}, text=text, focus=True)

    def inject_prompt_unfocused(self, text: str) -> None:
        if text:
            self._ask({"t": "prompt"}, text=text, focus=False)

    def switch_model(self, model_id: str, composer_open: bool = False) -> None:
        if model_id:
            self._ask({"t": "switch"}, model=model_id, composer_open=bool(composer_open))

    def switch_effort(self, effort: str, composer_open: bool = False) -> None:
        if effort:
            self._ask({"t": "switch"}, effort=effort, composer_open=bool(composer_open))

    def send_composed(self, text: str, clear: Callable[[], None], composer_open: bool = True) -> None:
        """The composer's send; *clear* runs when the service says it went
        now (a send waiting on a cut comes back as a `composer resend`)."""
        if not text:
            return
        reply = self._ask({"t": "send"}, text=text, composer_open=bool(composer_open))
        if reply is not None and reply.get("sent"):
            clear()

    def begin_cut(self) -> str | None:
        """Start the open-cut; its handle, which the `cut` events carry."""
        reply = self._ask({"t": "cut"})
        return str(reply["handle"]) if reply and reply.get("handle") else None

    def cancel_cut(self, handle: str | None = None) -> None:
        fields = {"handle": handle} if handle else {}
        self._ask({"t": "cut.cancel"}, **fields)

    def restore_draft(self, text: str) -> bool:
        if not text:
            return True
        reply = self._ask({"t": "draft.restore"}, text=text)
        return bool(reply and reply.get("restored"))

    def mention(self, path: str, start_line: int = 0, end_line: int = 0) -> str | None:
        """Type a file's mention into the box; the refusal's reason (a
        msgid, translated) when it didn't go, else None."""
        if self.pty is None or self.exited:
            return _("Add to chat: the agent isn't running in this tab")
        try:
            self._request(
                {
                    "t": "mention",
                    "pty": self.pty,
                    "path": path,
                    "start_line": start_line,
                    "end_line": end_line,
                }
            )
        except RequestRefused as refusal:
            return _(refusal.msgid).format_map(refusal.details) if refusal.msgid else _("Add to chat failed")
        except ValueError as error:
            return str(error)
        return None

    def set_transcript_path(self, path: str | None) -> None:
        self._ask({"t": "transcript.set"}, path=str(path) if path else None)

    def relocate_transcript(self, path: str) -> None:
        self._ask({"t": "transcript.relocate"}, path=str(path))

    def request_update(self, discover: bool = False) -> None:
        self._ask({"t": "transcript.update"}, discover=bool(discover))

    def restore_prs(self, records: object) -> None:
        if isinstance(records, list) and records:
            self._ask({"t": "prs.restore"}, records=[r for r in records if isinstance(r, dict)])

    def settle_cwd(self, cwd: str | None, root: str) -> editorfiles.FollowScope | None:
        reply = self._ask({"t": "cwd.settle"}, cwd=cwd, root=root)
        scope = (reply or {}).get("scope") or ""
        for member in editorfiles.FollowScope:
            if member.value == scope and member is not editorfiles.FollowScope.NONE:
                return member
        return None

    def set_shells_follow_armed(self, armed: bool) -> None:
        self.shells_follow_armed = bool(armed)
        self._ask({"t": "shells.follow"}, armed=bool(armed))

    def arm_resolver(self) -> None:
        self._ask({"t": "resolver.arm"})

    def begin_close(self, exit_text: str, backgrounding: bool) -> bool:
        """The graceful close; False when the service refused it (the
        window then forces the close)."""
        reply = self._ask(
            {"t": "close"}, mode="background" if backgrounding else "exit", text=exit_text
        )
        return reply is not None

    def end_close(self) -> None:
        self._ask({"t": "close.end"})

    def nudge_exit(self) -> None:
        self._ask({"t": "close.nudge"})

    def relaunch_without_worktree(self) -> None:
        self._ask({"t": "restart.worktreeless"})

    def restart_sandboxed(self) -> bool:
        """The chip's *Restart to apply* (`sandbox.restart`), by the box."""
        if self.pty is None or self.exited or not self.sandbox_box:
            return False
        try:
            self._request(
                {"t": "sandbox.restart", "box": self.sandbox_box, "pty": self.pty, "handle": self.handle}
            )
        except (RequestRefused, ValueError):
            return False
        return True

    # -- the probe (D27) ----------------------------------------------------------------------

    def _probe_ask(self, message: dict) -> dict | None:
        """A probe's request: None when the session has no pty (before the
        spawn, after the exit: nothing is held for it, every read answers
        as with no child), a `RuntimeError` on a refusal — a misnamed probe
        must never pass quietly."""
        if self.pty is None or self.exited:
            return None
        try:
            return self._request({**message, "pty": self.pty})
        except (RequestRefused, ValueError) as error:
            raise RuntimeError(f"{message.get('t')} {message.get('name', '')}: {error}") from error

    def probe(self, name: str):
        """A session attribute by (dotted) name, as the service holds it
        (None with no pty)."""
        reply = self._probe_ask({"t": "debug.session.get", "name": name})
        return reply.get("value") if reply is not None else None

    def probe_set(self, name: str, value) -> None:
        self._probe_ask({"t": "debug.session.set", "name": name, "value": value})

    def probe_call(self, name: str, *args, **kwargs):
        """A session method by (dotted) name with JSON arguments; its result
        JSON-encoded (None for what is not, and with no pty)."""
        message: dict = {"t": "debug.session.call", "name": name, "args": list(args)}
        if kwargs:
            message["kwargs"] = dict(kwargs)
        reply = self._probe_ask(message)
        return reply.get("value") if reply is not None else None


_MISSING = object()
