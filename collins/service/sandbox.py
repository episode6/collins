# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""The Sandboxed chip's requests, served by the service (split-service spec
§3.9, PR-1.11).

Everything but the chip was GTK-free already and is the service's: the
`sandboxplan.SandboxHost` (the boxes, the grants of each session's box, the
project defaults, the tool switches), the live grants
(`sandboxgrants.GrantMounts`), the plan files. The chip (`sandboxchip`)
becomes a client of `sandbox.*` requests and `sandbox` events, and **every
decision is made here, from the service's own records** (the user's rule
from PR 582): a client asks, the service decides, by the box the request
names, against the plan the service's records hold for that box (through
Phase 1 the plan of the session that runs in it, the app's lookup, as for a
sandboxed panel shell).

- `sandbox.plan`: the plan the box was launched with (`load_plan`).
- `sandbox.grants`: what the chip draws, worked out exactly as the chip
  worked it out: the box's grants with their status (live, after restart,
  until restart; the live grants' statuses where a grant can arrive live,
  the launched plan's otherwise) and each one's delivery, the plan's
  grants, the project's defaults, which tools the box is offered and which
  exist at all, whether the box overrides the defaults, whether the plan
  is stale, and whether its session can restart.
- `sandbox.allow` / `sandbox.revoke`, scope ``session`` (the box's own
  list; the running box gets it live where it can, and a `sandbox` event
  says what became of it) or ``project`` (the defaults for new sessions).
  A refusal is the host's own reason, crossing as its own msgid.
- `sandbox.tools`: a box's switches set or reset; the host's reason
  refuses. What is offered at a call is still `service.tools`' question,
  asked of the same host: nothing here changes the policy.
- `sandbox.restart`: the session in the box exits and resumes on a plan
  rebuilt from the state (through Phase 1 its `Session`, held by its tab).

GLib only; nothing here imports GTK.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

from .. import mcptools, sandboxgrants, sandboxplan
from ..api import protocol

log = logging.getLogger(__name__)

_NOT_SET_UP = "Sandboxed sessions aren't set up here"


def delivery_record(delivery) -> dict:
    """A `sandboxgrants.Delivery` (or a row's status alone) as the protocol
    carries it: `inside` left out where it is "" (a pending grant)."""
    record = {"path": delivery.path, "status": delivery.status[: protocol.SHORT_MAX]}
    if delivery.inside:
        record["inside"] = delivery.inside
    record["linked"] = bool(delivery.linked)
    if delivery.reason:
        record["reason"] = delivery.reason[: protocol.NAME_MAX]
    return record


class SandboxRequests:
    """See the module docstring. *host* and *grants* answer the SandboxHost
    and the GrantMounts (None when the app has none); *plan_of(box)* the
    plan file the box's session launched from; *sessions* the service's
    sessions (for the restart); *broadcast(event)* tells every subscriber;
    *dispatch* lands a worker thread's answer on the main loop."""

    def __init__(
        self,
        host: Callable,
        grants: Callable,
        plan_of: Callable[[str], str | None],
        sessions: Callable,
        broadcast: Callable[[dict], None],
        dispatch: Callable | None = None,
    ) -> None:
        self._host = host
        self._grants = grants
        self._plan_of = plan_of
        self._sessions = sessions
        self._broadcast = broadcast
        if dispatch is None:
            from gi.repository import GLib

            def dispatch(fn, *args):
                def landed() -> bool:
                    fn(*args)
                    return GLib.SOURCE_REMOVE

                GLib.idle_add(landed, priority=GLib.PRIORITY_DEFAULT)

        self._dispatch = dispatch

    def handle(self, message: protocol.Message) -> dict:
        box = message.get("box")
        if not sandboxplan.valid_box_id(box):
            return protocol.refuse(message.id, protocol.ERROR_REFUSED, "Not a sandbox: {box}", {"box": box})
        handler = {
            "sandbox.plan": self._plan_reply,
            "sandbox.grants": self._grants_reply,
            "sandbox.allow": self._allow,
            "sandbox.revoke": self._revoke,
            "sandbox.tools": self._tools,
            "sandbox.restart": self._restart,
        }[message.type]
        return handler(message, box)

    def _plan(self, box: str) -> tuple[str | None, dict | None]:
        path = self._plan_of(box)
        return path, sandboxplan.load_plan(path)

    def _session(self, box: str, handle: str | None = None):
        """The session the request is about: the one asking (its handle),
        when it runs in the box; else the box's first."""
        sessions = [s for s in self._sessions() if s.sandbox_box == box]
        if handle:
            return next((s for s in sessions if s.handle == handle), None)
        return sessions[0] if sessions else None

    # -- reads

    def _plan_reply(self, message: protocol.Message, box: str) -> dict:
        _path, plan = self._plan(box)
        if plan is None:
            return protocol.refuse(
                message.id, protocol.ERROR_GONE, "The sandbox plan for this session can't be read"
            )
        return protocol.reply(message.id, plan=plan)

    def _live_grants(self, box: str):
        """The live grants, when a grant can arrive live here and the box is
        one they know (the chip's rule); None otherwise."""
        mounts = self._grants()
        if mounts is None or not mounts.registered(box) or mounts.capable():
            return None
        return mounts

    def _grants_reply(self, message: protocol.Message, box: str) -> dict:
        plan_path, plan = self._plan(box)
        if plan is None:
            return protocol.refuse(
                message.id, protocol.ERROR_GONE, "The sandbox plan for this session can't be read"
            )
        host = self._host()
        mounts = self._live_grants(box)
        inputs = plan["inputs"]
        workspace = inputs["workspace"]
        launched = list(inputs.get("grants", []))
        grants = host.grants(box) if host is not None else list(launched)
        rows = []
        for grant in grants:
            if mounts is not None:
                status = mounts.status(box, grant)
                delivery = mounts.delivery(box, grant)
            else:
                status = sandboxgrants.STATIC if grant in launched else sandboxgrants.PENDING
                delivery = None
            row = delivery_record(delivery) if delivery is not None else {"path": grant, "status": status}
            row["status"] = status
            row["path"] = grant
            rows.append(row)
        if mounts is not None:
            rows += [
                {"path": grant, "status": sandboxgrants.LEAVING} for grant in launched if grant not in grants
            ]
        names = mcptools.tool_names()
        available = {name: bool(host is not None and host.tool_available(name)) for name in names}
        tools = {name: bool(available[name] and host.tool_enabled(box, name)) for name in names}
        every = self._grants()
        live = every.live_paths(box) if every is not None else ()
        session = self._session(box, message.get("handle"))
        return protocol.reply(
            message.id,
            grants=rows[: protocol.GRANTS_MAX],
            launched=launched[: protocol.GRANTS_MAX],
            defaults=(host.project_grants(workspace) if host is not None else [])[: protocol.GRANTS_MAX],
            tools=tools,
            available=available,
            overridden=bool(host is not None and host.tool_overrides(box)),
            hosted=host is not None,
            stale=bool(host is not None and host.plan_stale(plan_path, workspace, live=live)),
            can_restart=bool(session is not None and session.can_restart_sandboxed()),
        )

    # -- writes

    def _allow(self, message: protocol.Message, box: str) -> dict:
        return self._change(message, box, on=True)

    def _revoke(self, message: protocol.Message, box: str) -> dict:
        return self._change(message, box, on=False)

    def _change(self, message: protocol.Message, box: str, on: bool) -> dict:
        host = self._host()
        if host is None:
            return protocol.refuse(message.id, protocol.ERROR_REFUSED, _NOT_SET_UP)
        _plan_path, plan = self._plan(box)
        if plan is None:
            return protocol.refuse(
                message.id, protocol.ERROR_GONE, "The sandbox plan for this session can't be read"
            )
        workspace = plan["inputs"]["workspace"]
        path = message.get("path")
        if message.get("scope") == "project":
            reason = host.set_project_default(workspace, path, on)
            if reason:
                return protocol.refuse(message.id, protocol.ERROR_REFUSED, reason)
            return protocol.reply(message.id, live=False)
        if not on:
            host.revoke(box, path)
            mounts = self._grants()
            if mounts is not None:
                mounts.revoke(box, path, lambda _gone: self._dispatch(self._told, box, path, None, True))
            return protocol.reply(message.id, live=mounts is not None)
        reason = host.allow(box, workspace, path)
        if reason:
            return protocol.refuse(message.id, protocol.ERROR_REFUSED, reason)
        mounts = self._grants()
        if mounts is None:
            return protocol.reply(message.id, live=False)
        path = os.path.normpath(path)
        mounts.allow(box, path, lambda delivered: self._dispatch(self._delivered, box, path, delivered))
        return protocol.reply(message.id, live=True)

    def _delivered(self, box: str, path: str, delivered: list) -> None:
        """The live grants tried *path* in the box: what became of it, as
        the chip worked it out (its own delivery, else the record's)."""
        mounts = self._grants()
        own = next((d for d in delivered if d.box == box), None)
        if own is None and mounts is not None:
            own = mounts.delivery(box, path)
        self._told(box, path, own, False)

    def _told(self, box: str, path: str, delivery, revoked: bool) -> None:
        event = {"t": "sandbox", "box": box, "path": path}
        if delivery is not None:
            event["delivery"] = delivery_record(delivery)
        if revoked:
            event["revoked"] = True
        self._broadcast(event)

    def _tools(self, message: protocol.Message, box: str) -> dict:
        host = self._host()
        if host is None:
            return protocol.refuse(message.id, protocol.ERROR_REFUSED, _NOT_SET_UP)
        if message.get("reset"):
            host.reset_tools(box)
        for name, on in (message.get("tools") or {}).items():
            reason = host.set_tool(box, name, bool(on))
            if reason:
                return protocol.refuse(
                    message.id,
                    protocol.ERROR_REFUSED,
                    "Can't change {name}: {reason}",
                    {"name": name, "reason": reason},
                )
        return protocol.reply(message.id, tools=dict(host.tool_overrides(box)))

    def _restart(self, message: protocol.Message, box: str) -> dict:
        session = self._session(box, message.get("handle"))
        if session is None or not session.restart_sandboxed():
            return protocol.refuse(message.id, protocol.ERROR_REFUSED, "This session can't restart from here")
        return protocol.reply(message.id)
