# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The Sandboxed chip's requests on the service (collins.service.sandbox,
spec §3.9, PR-1.11): every answer worked out from the service's own
records (the host, the live grants, the plan of the box), the chip's rules
unchanged, a refusal carrying the host's own reason, and the `sandbox`
event that says what became of a grant in the running box."""

import pytest

from collins import mcptools, sandboxgrants, sandboxplan
from collins.api import protocol
from collins.service.sandbox import SandboxRequests

BOX = "0123456789abcdef0123456789abcdef"
WORKSPACE = "/home/u/project"
PLAN = {"inputs": {"workspace": WORKSPACE, "grants": ["/home/u/old", "/home/u/kept"], "box": BOX}}


class Host:
    def __init__(self):
        self.granted = ["/home/u/kept", "/home/u/new"]
        self.defaults = ["/home/u/kept"]
        self.overrides = {}
        self.calls = []

    def grants(self, box):
        return list(self.granted)

    def tool_available(self, name):
        return name != "start_session"

    def tool_enabled(self, box, name):
        return mcptools.sandbox_tool_enabled(name, name in mcptools.SANDBOX_DEFAULT_TOOLS, self.overrides)

    def tool_overrides(self, box):
        return dict(self.overrides)

    def project_grants(self, workspace):
        return list(self.defaults)

    def plan_stale(self, plan_path, workspace, live=()):
        return True

    def allow(self, box, workspace, path):
        self.calls.append(("allow", box, workspace, path))
        return "a secret" if path.endswith(".ssh") else ""

    def revoke(self, box, path):
        self.calls.append(("revoke", box, path))

    def set_project_default(self, workspace, path, on):
        self.calls.append(("default", workspace, path, on))
        return "not a directory" if path == "/nope" else ""

    def set_tool(self, box, name, on):
        self.overrides[name] = on
        return ""

    def reset_tools(self, box):
        self.overrides.clear()


class Mounts:
    def __init__(self):
        self.done = []

    def registered(self, box):
        return True

    def capable(self):
        return ""

    def status(self, box, path):
        return sandboxgrants.LIVE if path == "/home/u/new" else sandboxgrants.STATIC

    def delivery(self, box, path):
        if path != "/home/u/new":
            return None
        return sandboxgrants.Delivery(box, path, sandboxgrants.LIVE, "/inside/new", False, "")

    def live_paths(self, box):
        return ["/home/u/new"]

    def allow(self, box, path, done):
        self.done.append(done)

    def revoke(self, box, path, done):
        self.done.append(done)


class Session:
    def __init__(self, handle, can):
        self.handle = handle
        self.sandbox_box = BOX
        self._can = can
        self.restarted = False

    def can_restart_sandboxed(self):
        return self._can

    def restart_sandboxed(self):
        self.restarted = self._can
        return self._can


@pytest.fixture
def requests(monkeypatch):
    monkeypatch.setattr(sandboxplan, "load_plan", lambda path: PLAN if path == "/plans/p.json" else None)
    host, mounts, events = Host(), Mounts(), []
    sessions = [Session("s-fork", False), Session("s-own", True)]
    served = SandboxRequests(
        host=lambda: host,
        grants=lambda: mounts,
        plan_of=lambda box: "/plans/p.json" if box == BOX else None,
        sessions=lambda: sessions,
        broadcast=events.append,
        dispatch=lambda fn, *args: fn(*args),
    )
    return {"served": served, "host": host, "mounts": mounts, "events": events, "sessions": sessions}


def ask(requests, kind, **fields):
    message = protocol.validate(protocol.request(kind, 1, box=BOX, **fields), protocol.CLIENT)
    assert isinstance(message, protocol.Message), message
    raw = requests["served"].handle(message)
    answer = protocol.validate_response(raw, kind)
    assert not isinstance(answer, protocol.Refusal), answer
    return answer


def test_the_plan_is_the_boxs_own(requests):
    assert ask(requests, "sandbox.plan").fields["plan"] == PLAN


def test_the_grants_are_drawn_as_the_chip_drew_them(requests):
    fields = ask(requests, "sandbox.grants", handle="s-own").fields
    rows = {row["path"]: row for row in fields["grants"]}
    assert rows["/home/u/kept"]["status"] == sandboxgrants.STATIC
    assert rows["/home/u/new"] == {
        "path": "/home/u/new",
        "status": sandboxgrants.LIVE,
        "inside": "/inside/new",
        "linked": False,
    }
    # Bound by the launched plan and taken back since: until restart.
    assert rows["/home/u/old"]["status"] == sandboxgrants.LEAVING
    assert fields["launched"] == PLAN["inputs"]["grants"] and fields["defaults"] == ["/home/u/kept"]
    assert fields["tools"] == {
        name: name in mcptools.SANDBOX_DEFAULT_TOOLS and name != "start_session"
        for name in mcptools.tool_names()
    }
    assert fields["available"]["start_session"] is False
    assert fields["stale"] is True and fields["hosted"] is True and fields["overridden"] is False


def test_can_restart_is_the_asking_sessions(requests):
    assert ask(requests, "sandbox.grants", handle="s-own").fields["can_restart"] is True
    assert ask(requests, "sandbox.grants", handle="s-fork").fields["can_restart"] is False
    assert not ask(requests, "sandbox.restart", handle="s-fork").ok
    assert ask(requests, "sandbox.restart", handle="s-own").ok
    assert requests["sessions"][1].restarted


def test_without_live_grants_the_rows_are_the_launched_plans(requests):
    requests["served"]._grants = lambda: None
    rows = {r["path"]: r["status"] for r in ask(requests, "sandbox.grants").fields["grants"]}
    assert rows == {"/home/u/kept": sandboxgrants.STATIC, "/home/u/new": sandboxgrants.PENDING}


def test_an_allow_is_decided_by_the_host_and_delivered_live(requests):
    answer = ask(requests, "sandbox.allow", path="/home/u/data", scope="session")
    assert answer.fields == {"live": True}
    assert requests["host"].calls[-1] == ("allow", BOX, WORKSPACE, "/home/u/data")
    delivered = sandboxgrants.Delivery(BOX, "/home/u/data", sandboxgrants.LIVE, "/home/u/data", True, "")
    requests["mounts"].done[-1]([delivered])
    (event,) = requests["events"]
    assert isinstance(protocol.validate(event, protocol.SERVICE), protocol.Message)
    assert event["path"] == "/home/u/data" and event["delivery"]["status"] == sandboxgrants.LIVE


def test_a_refused_allow_carries_the_hosts_reason(requests):
    answer = ask(requests, "sandbox.allow", path="/home/u/.ssh", scope="session")
    assert not answer.ok and answer.msgid == "a secret" and answer.error == protocol.ERROR_REFUSED


def test_a_project_default_is_the_hosts_and_never_the_session_list(requests):
    assert ask(requests, "sandbox.allow", path="/home/u/x", scope="project").fields == {"live": False}
    assert requests["host"].calls[-1] == ("default", WORKSPACE, "/home/u/x", True)
    assert not ask(requests, "sandbox.allow", path="/nope", scope="project").ok
    ask(requests, "sandbox.revoke", path="/home/u/x", scope="project")
    assert requests["host"].calls[-1] == ("default", WORKSPACE, "/home/u/x", False)


def test_a_revoke_leaves_the_running_box_and_says_so(requests):
    ask(requests, "sandbox.revoke", path="/home/u/new", scope="session")
    assert requests["host"].calls[-1] == ("revoke", BOX, "/home/u/new")
    requests["mounts"].done[-1]([])
    assert requests["events"][-1]["revoked"] is True


def test_the_tools_switches_are_the_boxs(requests):
    assert ask(requests, "sandbox.tools", tools={"run_in_terminal": True}).fields == {
        "tools": {"run_in_terminal": True}
    }
    assert ask(requests, "sandbox.grants").fields["overridden"] is True
    assert ask(requests, "sandbox.tools", reset=True).fields == {"tools": {}}


def test_a_box_it_cannot_read_is_gone(requests):
    message = protocol.validate(protocol.request("sandbox.plan", 1, box="f" * 32), protocol.CLIENT)
    raw = requests["served"].handle(message)
    assert raw["ok"] is False and raw["error"] == protocol.ERROR_GONE


# -- drop and forget (rule 5) -------------------------------------------------------------


def _drop_requests(monkeypatch, tmp_path, live=None):
    """A SandboxRequests whose host derives into *tmp_path*'s plan dir."""
    plan_dir = tmp_path / "plans"
    plan_dir.mkdir()
    monkeypatch.setattr(sandboxplan, "plan_dir", lambda app_id: str(plan_dir))
    monkeypatch.setattr(sandboxplan, "load_plan", lambda path: PLAN if path == "/plans/p.json" else None)
    released = []
    monkeypatch.setattr(sandboxplan, "release_plan", released.append)
    derived = str(plan_dir / "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0.json")
    sibling = "b" * 32
    host = Host()
    host.derive = lambda plan_path, cwd, live=(): (derived, sibling, "")
    host.released, host.forgotten = [], []
    host.release = host.released.append
    host.forget_box = host.forgotten.append
    served = SandboxRequests(
        host=lambda: host,
        grants=lambda: None,
        plan_of=lambda box: "/plans/p.json" if box == BOX else None,
        sessions=lambda: [],
        broadcast=lambda e: None,
        dispatch=lambda fn, *args: fn(*args),
        app_id=lambda: "com.example.Test",
        session_for_box=lambda box: live if box == live else None,
    )
    return served, host, released, derived, sibling


def _msg(t, **fields):
    checked = protocol.validate({"t": t, "id": 1, **fields}, protocol.CLIENT)
    assert isinstance(checked, protocol.Message), checked
    return checked


def test_drop_releases_only_a_plan_this_service_derived_for_the_box(monkeypatch, tmp_path):
    served, host, released, derived, sibling = _drop_requests(monkeypatch, tmp_path)
    reply = served.handle(_msg("sandbox.derive", box=BOX, cwd="/home/u/project/sub"))
    assert reply["plan"] == derived and reply["box"] == sibling
    # Another path, even under the plan dir, is nobody's to unlink.
    other = str(tmp_path / "plans" / "1f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0.json")
    refused = served.handle(_msg("sandbox.drop", box=sibling, plan=other))
    assert refused.get("error") == protocol.ERROR_REFUSED and released == []
    # The derived plan named against another box is refused too.
    refused = served.handle(_msg("sandbox.drop", box=BOX, plan=derived))
    assert refused.get("error") == protocol.ERROR_REFUSED and released == []
    served.handle(_msg("sandbox.drop", box=sibling, plan=derived))
    assert released == [derived] and host.released == [sibling] and host.forgotten == [sibling]
    # Once dropped, a second drop of the same plan has nothing to release.
    refused = served.handle(_msg("sandbox.drop", box=sibling, plan=derived))
    assert refused.get("error") == protocol.ERROR_REFUSED and released == [derived]


def test_drop_refuses_a_plan_outside_the_plan_dir_even_when_derived(monkeypatch, tmp_path):
    served, _host, released, _derived, sibling = _drop_requests(monkeypatch, tmp_path)
    outside = str(tmp_path / "elsewhere" / "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0.json")
    served._derived[sibling] = {outside}  # even a derived path has to sit under the plan dir
    refused = served.handle(_msg("sandbox.drop", box=sibling, plan=outside))
    assert refused.get("error") == protocol.ERROR_REFUSED and released == []
    named = str(tmp_path / "plans" / "probe.json")
    served._derived[sibling] = {named}
    refused = served.handle(_msg("sandbox.drop", box=sibling, plan=named))
    assert refused.get("error") == protocol.ERROR_REFUSED and released == []


def test_a_box_with_a_live_session_is_neither_dropped_nor_forgotten(monkeypatch, tmp_path):
    live = "c" * 32
    served, host, _released, _derived, _sibling = _drop_requests(monkeypatch, tmp_path, live=live)
    refused = served.handle(_msg("sandbox.drop", box=live))
    assert refused.get("error") == protocol.ERROR_REFUSED
    refused = served.handle(_msg("sandbox.forget", box=live))
    assert refused.get("error") == protocol.ERROR_REFUSED
    assert host.forgotten == []
    served.handle(_msg("sandbox.forget", box="d" * 32))
    assert host.forgotten == ["d" * 32]
