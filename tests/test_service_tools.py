# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The session tools on the service (collins.service.tools, spec §3.7,
PR-1.11): identity by pid, the switches and what a sandboxed session is
offered (unchanged from App._mcp_tool_offered: §5's third rule), each tool
with a client attached (a `tool` event to the active client and a reply
that settles it, at once or at the bound) and with none (§3.7's last
column)."""

import pytest

from collins import mcptools, notifycenter, proctree
from collins.api import protocol
from collins.service import tools as tools_mod
from collins.service.notifications import ServiceNotifications
from collins.service.tools import SessionTools


class Timers:
    def __init__(self):
        self.due = {}
        self.next = 1

    def add(self, ms, fn, *args):
        source = self.next
        self.next += 1
        self.due[source] = (ms, fn, args)
        return source

    def remove(self, source):
        self.due.pop(source, None)

    def fire_all(self):
        for source, (_ms, fn, args) in list(self.due.items()):
            self.due.pop(source, None)
            fn(*args)


class FakeSession:
    def __init__(self, session_id="sid-1", sandboxed=False, box="", pids=(4242,), cwd="/tmp"):
        self.session_id = session_id
        self.handle = f"h-{session_id}"
        self.sandboxed = sandboxed
        self.sandbox_box = box
        self.sandbox_plan_path = "/plans/p.json" if sandboxed else None
        self._pids = set(pids)
        self.cwd = cwd
        self.attached = []

    def owns_pid_ancestors(self, ancestors):
        return bool(self._pids & set(ancestors))

    def current_agent_cwd(self):
        return self.cwd

    def attach_pr(self, pr):
        if pr.url in self.attached:
            return False
        self.attached.append(pr.url)
        return True


class FakeHost:
    """A SandboxHost's tool switches: the box's own over the defaults."""

    def __init__(self, overrides=None, defaults=None):
        self.overrides = overrides or {}
        self.defaults = defaults or {}

    def tool_enabled(self, box, name):
        return mcptools.sandbox_tool_enabled(
            name, self.defaults.get(name, name in mcptools.SANDBOX_DEFAULT_TOOLS), self.overrides.get(box, {})
        )


class FakeStore:
    def __init__(self):
        self.renamed = []
        self.unread = []

    def rename(self, session_id, name):
        self.renamed.append((session_id, name))

    def set_unread(self, session_id, flag):
        self.unread.append((session_id, flag))

    def get_item(self, session_id):
        return None


class FakeState:
    def __init__(self):
        self.settings = {}
        self.attachments = {}
        self.pending = {}
        self.notifications = []
        self.saves = 0

    def get_setting(self, key):
        return self.settings.get(key, True if key.startswith("mcp_tool_") else None)

    def get_session_attachments(self, sid):
        return list(self.attachments.get(sid, []))

    def set_session_attachments(self, sid, records):
        self.attachments[sid] = list(records)

    def get_pending_diffs(self):
        return {k: dict(v) for k, v in self.pending.items()}

    def set_pending_diff(self, sid, args):
        if args:
            self.pending[sid] = dict(args)
        else:
            self.pending.pop(sid, None)

    def get_notifications(self):
        return list(self.notifications)

    def set_notifications(self, records):
        self.notifications = list(records)

    def save(self):
        self.saves += 1


class Client:
    """A client that answers each tool event with what *answer* says
    (None: it holds the call unanswered)."""

    def __init__(self, tools=None, answer=lambda event: (True, "done")):
        self.tools = tools
        self.answer = answer
        self.events = []

    def take(self, event):
        assert isinstance(protocol.validate(dict(event), protocol.SERVICE), protocol.Message)
        self.events.append(event)
        result = self.answer(event)
        if result is not None:
            self.tools.reply(event["call"], *result, client=self)


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(proctree, "ancestor_pids", lambda pid: {pid, 1})
    state, store, timers = FakeState(), FakeStore(), Timers()
    sessions = [FakeSession()]
    holder = {"client": None, "host": None}

    def send_tool(session, event):
        client = holder["client"]
        if client is None:
            return None
        client.take(event)
        return client

    tools = SessionTools(
        get_setting=state.get_setting,
        sessions=lambda: list(sessions),
        sandbox_host=lambda: holder["host"],
        send_tool=send_tool,
        store=store,
        state=state,
        notifications=ServiceNotifications(state),
        timeout_add=timers.add,
        source_remove=timers.remove,
    )
    return {
        "tools": tools,
        "state": state,
        "store": store,
        "timers": timers,
        "sessions": sessions,
        **{"holder": holder},
    }


def attach(world, answer=lambda event: (True, "done")):
    client = Client(world["tools"], answer)
    world["holder"]["client"] = client
    return client


# -- identity and the policy (unchanged) -------------------------------------------


def test_a_call_binds_to_the_session_whose_terminal_spawned_the_shim(world):
    assert world["tools"].find(4242) is world["sessions"][0]
    assert world["tools"].find(99) is None
    assert world["tools"].dispatch(99, "set_session_title", {"title": "x"}) == (
        False,
        mcptools.NOT_FROM_TAB_ERROR,
    )


def _old_offered(session, name, host, get_setting):
    """App._mcp_tool_offered as it read before the move, over a tab."""
    if not session.sandboxed:
        return True
    if host is None:
        return mcptools.sandbox_tool_enabled(name, get_setting(mcptools.sandbox_tool_setting_key(name)), {})
    return host.tool_enabled(session.sandbox_box, name)


@pytest.mark.parametrize("hosted", [False, True])
@pytest.mark.parametrize("sandboxed", [False, True])
def test_what_a_session_is_offered_is_what_it_was_before_the_move(world, hosted, sandboxed):
    """§5: nothing a sandboxed session is offered or refused changes."""
    session = FakeSession(sandboxed=sandboxed, box="box-1")
    host = FakeHost(overrides={"box-1": {"run_in_terminal": True, "attach_pr": False}}) if hosted else None
    world["holder"]["host"] = host
    world["state"].settings["sandbox_tool_show_image"] = False
    for name in mcptools.tool_names():
        assert world["tools"].tool_offered(session, name) == _old_offered(
            session, name, host, world["state"].get_setting
        ), name


def test_a_sandboxed_session_is_listed_and_refused_by_its_box(world):
    world["sessions"][0] = FakeSession(sandboxed=True, box="box-1")
    world["holder"]["host"] = FakeHost()
    listed = [tool["name"] for tool in world["tools"].list_tools(4242)]
    assert listed == list(mcptools.SANDBOX_DEFAULT_TOOLS)
    assert world["tools"].dispatch(4242, "run_in_terminal", {"command": "ls"}) == (
        False,
        mcptools.sandbox_disabled_error("run_in_terminal"),
    )


def test_a_switched_off_tool_is_refused_before_identity(world):
    world["state"].settings["mcp_tool_notify_user"] = False
    assert world["tools"].dispatch(99, "notify_user", {"message": "hi"}) == (
        False,
        mcptools.disabled_error("notify_user"),
    )
    assert "notify_user" not in [t["name"] for t in world["tools"].list_tools(4242)]


# -- the session's own data, with or without a client -----------------------------


@pytest.mark.parametrize("client", [False, True])
def test_set_session_title_renames_on_the_service(world, client):
    if client:
        attached = attach(world)
    assert world["tools"].dispatch(4242, "set_session_title", {"title": "New"}) == (True, "Session renamed.")
    assert world["store"].renamed == [("sid-1", "New")]
    if client:
        assert attached.events == []  # nothing for the client to do


def test_an_unresolved_session_cannot_be_renamed(world):
    world["sessions"][0].session_id = None
    assert world["tools"].dispatch(4242, "set_session_title", {"title": "x"}) == (
        False,
        tools_mod.NOT_RESOLVED,
    )


@pytest.mark.parametrize("client", [False, True])
def test_attach_pr_lands_on_the_session(world, client):
    if client:
        attach(world)
    url = "https://github.com/o/r/pull/7"
    assert world["tools"].dispatch(4242, "attach_pr", {"url": url}) == (
        True,
        "Attached o/r#7 to this session.",
    )
    assert (
        world["tools"]
        .dispatch(4242, "attach_pr", {"url": url})[1]
        .endswith("already attached to this session.")
    )
    assert world["sessions"][0].attached == [url]


# -- a UI-bound tool with a client attached --------------------------------------


def test_a_ui_tool_is_an_event_to_the_active_client_settled_by_its_reply(world):
    client = attach(world, answer=lambda event: (True, "Opened in the editor."))
    got = world["tools"].dispatch(4242, "open_in_editor", {"path": "a.py"})
    assert got == (True, "Opened in the editor.")  # answered inside the event: settled at once
    (event,) = client.events
    assert event["name"] == "open_in_editor" and event["handle"] == "h-sid-1"
    assert event["session"] == "sid-1" and event["sandboxed"] is False
    assert world["timers"].due == {}


def test_a_held_call_resolves_on_the_late_reply(world):
    client = attach(world, answer=lambda event: None)
    got = world["tools"].dispatch(4242, "show_diff", {"what": "staged"})
    assert isinstance(got, mcptools.DeferredResult) and not got.resolved
    seen = []
    got.watch(lambda ok, text: seen.append((ok, text)))
    other = Client(world["tools"])
    assert not world["tools"].reply(client.events[0]["call"], True, "spoof", client=other)
    assert world["tools"].reply(client.events[0]["call"], True, "Shown.", client=client)
    assert seen == [(True, "Shown.")] and world["timers"].due == {}


def test_a_call_nobody_answers_ends_at_the_bound(world):
    attach(world, answer=lambda event: None)
    got = world["tools"].dispatch(4242, "start_session", {"prompt": "go"})
    ((ms, _fn, _args),) = world["timers"].due.values()
    assert ms == int(tools_mod.TOOL_BOUND_S * 1000) < 15_000  # under the shim's call timeout
    world["timers"].fire_all()
    assert got.resolved and got.result() == (False, tools_mod.NO_ANSWER)


def test_a_client_that_goes_away_answers_its_calls(world):
    client = attach(world, answer=lambda event: None)
    got = world["tools"].dispatch(4242, "read_terminal", {})
    world["tools"].client_gone(client)
    assert got.resolved and got.result()[0] is False and world["timers"].due == {}


def test_the_sandbox_reading_travels_with_the_event(world):
    world["sessions"][0] = FakeSession(sandboxed=True, box="box-1")
    world["holder"]["host"] = FakeHost(overrides={"box-1": {"run_in_terminal": True}})
    client = attach(world, answer=lambda event: (True, "Running in Sandboxed shell 1."))
    assert world["tools"].dispatch(4242, "run_in_terminal", {"command": "ls"}) == (
        True,
        "Running in Sandboxed shell 1.",
    )
    assert client.events[0]["sandboxed"] is True


# -- with no client attached (§3.7's table) ---------------------------------------


def test_open_in_editor_says_no_client(world):
    assert world["tools"].dispatch(4242, "open_in_editor", {"path": "a.py"}) == (False, tools_mod.NO_CLIENT)


def test_start_session_needs_a_client_in_phase_one(world):
    assert world["tools"].dispatch(4242, "start_session", {"prompt": "go"}) == (
        False,
        tools_mod.START_NEEDS_CLIENT,
    )


def test_notify_user_is_recorded_unread_and_flags_the_row(world):
    got = world["tools"].dispatch(4242, "notify_user", {"message": "Done {soon}"})
    assert got == (True, tools_mod.NOTIFY_RECORDED)
    (record,) = world["state"].notifications
    assert record["kind"] == notifycenter.KIND_MESSAGE and record["msgid"] == "Done {soon}"
    assert record["read"] is False and "args" not in record
    assert world["store"].unread == [("sid-1", True)]


def test_show_image_is_recorded_as_an_attachment(world, tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    got = world["tools"].dispatch(4242, "show_image", {"path": str(image), "caption": "Look"})
    assert got == (True, tools_mod.SHOW_IMAGE_RECORDED)
    (record,) = world["state"].attachments["sid-1"]
    assert record["key"] == str(image)
    assert world["tools"].dispatch(4242, "show_image", {"path": str(tmp_path / "nope.png")})[0] is False


def test_show_diff_is_queued_then_applied_when_a_client_subscribes(world):
    got = world["tools"].dispatch(4242, "show_diff", {"what": "staged"})
    assert isinstance(got, mcptools.DeferredResult) and not got.resolved
    assert world["state"].pending == {"sid-1": {"what": "staged"}}
    ((ms, _fn, _a),) = world["timers"].due.values()
    assert ms == int(tools_mod.PENDING_DIFF_WAIT_S * 1000)
    client = attach(world, answer=lambda event: (True, "Shows the staged diff."))
    assert world["tools"].apply_pending_diffs(client) == 1
    assert client.events[0]["name"] == "show_diff" and client.events[0]["arguments"] == {"what": "staged"}
    assert got.resolved and got.result() == (True, "Shows the staged diff.")
    assert world["state"].pending == {} and world["timers"].due == {}


def test_show_diff_with_nobody_coming_answers_queued(world):
    got = world["tools"].dispatch(4242, "show_diff", {"what": "branch"})
    world["timers"].fire_all()
    assert got.resolved and got.result()[0] is True and got.result()[1].startswith("Queued")
    assert world["state"].pending == {"sid-1": {"what": "branch"}}  # still applied on attach


def test_the_diff_tools_without_a_store_say_no_client(world):
    for name, args in (
        ("diff_context", {}),
        ("annotate_diff", {"notes": [{"file": "a", "line": 1, "summary": "s"}]}),
        ("clear_diff_marks", {}),
    ):
        assert world["tools"].dispatch(4242, name, args) == (False, tools_mod.NO_CLIENT), name


class FakePty:
    def __init__(self, pty_id, history, text="", busy=False, box=None, plan=None):
        self.id = pty_id
        self.kind = "shell"
        self.history = history
        self.box = box
        self.plan = plan
        self.busy = busy
        self.screen = type("S", (), {"capture_contents": lambda _self: text})()
        self.written = []

    def has_running_command(self):
        return self.busy


class FakePtys:
    def __init__(self, ptys):
        self.ptys = {p.id: p for p in ptys}

    def get(self, pty_id):
        return self.ptys[pty_id]

    def write(self, pty_id, data):
        self.ptys[pty_id].written.append(data)


def test_the_terminal_tools_reach_the_sessions_service_ptys(world):
    shells = FakePtys(
        [FakePty(3, "sid-1", "one$ make"), FakePty(5, "other"), FakePty(8, "sid-1", "two$", busy=True)]
    )
    world["tools"].ptys = shells
    ok, text = world["tools"].dispatch(4242, "read_terminal", {})
    assert ok and "one$ make" in text and "two$" in text
    got = world["tools"].dispatch(4242, "run_in_terminal", {"command": "ls"})
    assert got == (True, "Running in Terminal 1.")
    assert shells.ptys[3].written and b"ls" in shells.ptys[3].written[0]
    got = world["tools"].dispatch(4242, "run_in_terminal", {"command": "ls", "terminal": 2})
    assert got[0] is False and "busy" in got[1]


def test_run_in_terminal_opens_a_shell_on_the_service_when_none_is_idle(world):
    shells = FakePtys([FakePty(3, "sid-1", busy=True)])
    world["tools"].ptys = shells

    def spawn(session, sandboxed):
        shells.ptys[9] = FakePty(9, session.session_id)
        return 9

    world["tools"]._spawn_shell = spawn
    assert world["tools"].dispatch(4242, "run_in_terminal", {"command": "ls"}) == (
        True,
        "Running in new Terminal 2.",
    )


def test_a_sandboxed_session_reads_only_its_boxs_shells(world):
    world["sessions"][0] = FakeSession(sandboxed=True, box="box-1")
    world["holder"]["host"] = FakeHost(overrides={"box-1": {"read_terminal": True}})
    world["tools"].ptys = FakePtys(
        [FakePty(3, "sid-1", "host$"), FakePty(4, "sid-1", "boxed$", box="box-1", plan="/plans/p.json")]
    )
    ok, text = world["tools"].dispatch(4242, "read_terminal", {})
    assert ok and "boxed$" in text and "host$" not in text


# -- through the core and the loopback ------------------------------------------------


def test_a_tool_event_crosses_the_loopback_and_its_reply_settles_the_call(tmp_path, monkeypatch):
    """The whole path: the core picks the active client, the event is
    validated on its way out, the client's tool-reply comes back as an
    event and settles the call the dispatcher returned."""
    import inproc as loopback

    from collins.service.core import ServiceCore

    monkeypatch.setattr(proctree, "ancestor_pids", lambda pid: {pid, 1})
    core = ServiceCore(state_dir=tmp_path / "pty", get_setting=lambda key: True)
    session = FakeSession()
    tools = core.start_tools(sessions=lambda: [session])
    server = loopback.LoopbackServer(core)
    seen = []

    def on_event(event):
        seen.append(event)
        if event["t"] == "tool":
            client.send_event({"t": "tool-reply", "call": event["call"], "ok": True, "text": "Opened."})

    client = server.connect(lambda *_a: None, on_event, device="laptop")
    try:
        assert tools.dispatch(4242, "open_in_editor", {"path": "a.py"}) == (False, tools_mod.NO_CLIENT)
        core._subscribers.append(client)  # what a subscribe does
        assert tools.dispatch(4242, "open_in_editor", {"path": "a.py"}) == (True, "Opened.")
        assert [e["t"] for e in seen] == ["tool"] and seen[0]["handle"] == session.handle
    finally:
        server.shutdown()


# -- the review's fixes ------------------------------------------------------------


@pytest.mark.parametrize("client", [False, True])
def test_a_call_the_protocol_cannot_carry_is_refused_not_answered_headless(world, client):
    """An argument past a protocol bound is refused, attached or not: never
    taken for "no client" (notify_user recorded unread, show_diff queued)."""
    if client:
        attached = attach(world)
    session = world["sessions"][0]
    huge = "x" * (protocol.TEXT_MAX + 1)
    for name, args in (("notify_user", {"message": huge}), ("show_diff", {"what": huge})):
        got = world["tools"]._handler(name)(session, args, False)
        assert got == (False, tools_mod.ARGS_DONT_FIT), name
    assert world["state"].notifications == [] and world["state"].pending == {}
    assert world["store"].unread == []
    if client:
        assert attached.events == []


class ClosingPtys(FakePtys):
    def __init__(self, ptys):
        super().__init__(ptys)
        self.closed = []

    def close(self, pty_id):
        self.closed.append(pty_id)
        self.ptys.pop(pty_id, None)


def test_one_headless_shell_per_session_closed_with_its_agent(world):
    shells = ClosingPtys([])
    world["tools"].ptys = shells
    spawned = []

    def spawn(session, sandboxed):
        pty_id = 20 + len(spawned)
        spawned.append(pty_id)
        shells.ptys[pty_id] = FakePty(pty_id, session.session_id)
        return pty_id

    world["tools"]._spawn_shell = spawn
    assert world["tools"].dispatch(4242, "run_in_terminal", {"command": "make"}) == (
        True,
        "Running in new Terminal 1.",
    )
    shells.ptys[20].busy = True
    got = world["tools"].dispatch(4242, "run_in_terminal", {"command": "ls"})
    assert got[0] is False and "busy" in got[1] and spawned == [20]  # reused, never a second
    shells.ptys[20].busy = False
    assert world["tools"].dispatch(4242, "run_in_terminal", {"command": "ls"}) == (
        True,
        "Running in Terminal 1.",
    )
    world["tools"].agent_exited("sid-1")
    assert shells.closed == [20] and world["tools"]._headless_shells == {}
    assert world["tools"].dispatch(4242, "run_in_terminal", {"command": "ls"})[0] is True
    assert spawned == [20, 21]


def test_a_headless_shell_of_a_session_no_longer_held_goes_too(world):
    shells = ClosingPtys([FakePty(30, "gone")])
    world["tools"].ptys = shells
    world["tools"]._headless_shells["gone"] = 30
    world["tools"].agent_exited("sid-1")  # another session's exit
    assert shells.closed == [30]


def test_with_no_link_every_call_is_gone(monkeypatch):
    """A widget with no app behind it and no harness link (PR-1.12b: the
    Phase 1 fallback loopback is gone, D21) is refused ``gone``, visibly."""
    from collins import apilink
    from collins.api.protocol import RequestRefused

    monkeypatch.setattr(apilink, "_current", None)
    assert apilink.current() is None
    with pytest.raises(RequestRefused) as refused:
        apilink.call({"t": "pr.detail", "url": "https://github.com/o/r/pull/1"})
    assert refused.value.error == protocol.ERROR_GONE


# -- show_image on the service (PR-2.7) -----------------------------------------------------


def test_show_image_resolves_and_registers_the_path_before_the_client_sees_it(world, tmp_path):
    """The event carries the absolute path the service resolved (a
    relative one against the agent's cwd), and the path is admitted for
    the session (D38) so the client's `kind=file` GET may read it."""
    from collins.service.blobs import ImageRegistry

    world["tools"].registry = registry = ImageRegistry()
    world["sessions"][0].cwd = str(tmp_path)
    (tmp_path / "shot.png").write_bytes(b"\x89PNG")
    client = attach(world)
    got = world["tools"].dispatch(4242, "show_image", {"path": "shot.png"})
    assert got == (True, "done")
    (event,) = client.events
    assert event["arguments"]["path"] == str(tmp_path / "shot.png")
    assert str(tmp_path / "shot.png") in registry.paths("sid-1")
    assert str(tmp_path / "shot.png") in registry.paths("h-sid-1")
    gone = world["tools"].dispatch(4242, "show_image", {"path": "gone.png"})
    assert gone == (False, "No such file: gone.png")


def test_show_image_of_a_url_is_downloaded_by_the_service_before_it_is_forwarded(world):
    fetched = []

    def fetch_remote(url, done):
        fetched.append(url)
        world["holder"]["done"] = done

    world["tools"]._fetch_remote = fetch_remote
    client = attach(world)
    url = "https://example.com/plot.png"
    got = world["tools"].dispatch(4242, "show_image", {"path": url})
    assert isinstance(got, mcptools.DeferredResult) and not got.resolved
    assert fetched == [url] and client.events == []  # nothing forwarded before the bytes are here
    world["holder"]["done"](None)
    assert got.resolved and got.result() == (True, "done")
    assert client.events[0]["arguments"]["path"] == url


def test_show_image_of_a_url_the_service_cannot_fetch_says_why(world):
    world["tools"]._fetch_remote = lambda url, done: done("The server answered 404 for " + url)
    client = attach(world)
    got = world["tools"].dispatch(4242, "show_image", {"path": "https://example.com/x.png"})
    assert got == (False, "The server answered 404 for https://example.com/x.png")
    assert client.events == []
    assert world["tools"].dispatch(4242, "show_image", {"path": "ftp://example.com/x.png"})[0] is False


def test_show_image_of_a_url_with_no_client_is_recorded_after_the_download(world):
    world["tools"]._fetch_remote = lambda url, done: done(None)
    got = world["tools"].dispatch(4242, "show_image", {"path": "https://example.com/x.png"})
    assert got == (True, tools_mod.SHOW_IMAGE_RECORDED)
    (record,) = world["state"].attachments["sid-1"]
    assert record["key"] == "https://example.com/x.png"


def test_show_image_of_a_dripping_url_answers_at_the_bound(world):
    """The deferred is bounded from the start (PR 612's review): a download
    that never lands answers the agent at TOOL_BOUND_S, and a late landing
    forwards nothing."""
    world["tools"]._fetch_remote = lambda url, done: world["holder"].update(done=done)
    client = attach(world)
    got = world["tools"].dispatch(4242, "show_image", {"path": "https://example.com/drip.png"})
    assert isinstance(got, mcptools.DeferredResult) and not got.resolved
    (bound,) = [ms for ms, _fn, _args in world["timers"].due.values()]
    assert bound == int(tools_mod.TOOL_BOUND_S * 1000)
    world["timers"].fire_all()
    assert got.result() == (False, "Timed out after 14s fetching https://example.com/drip.png")
    world["holder"]["done"](None)
    assert client.events == []


def test_show_image_of_a_url_that_lands_cancels_its_bound(world):
    world["tools"]._fetch_remote = lambda url, done: done(None)
    attach(world)
    got = world["tools"].dispatch(4242, "show_image", {"path": "https://example.com/x.png"})
    assert got == (True, "done") and world["timers"].due == {}
