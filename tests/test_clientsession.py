# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The client's mirror of a service session (collins.clientsession): every
`session` field lands on the attribute the tab's forwarder reads, the reads
answer from the mirror with no round trip, and each request the tab makes
is the message of api.protocol it should be, through a fake client that
records them and answers as the test says (PR-1.12a, spec §3.19)."""

import pytest

from collins import editorfiles
from collins.api import protocol
from collins.api.loopback import RequestRefused
from collins.clientsession import ClientSession
from collins.providers import ClaudeProvider, EnteredPrompt, SessionOptions

ID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"


class FakeClient:
    """Records every request; answers each with what `replies` holds for
    its type (a dict), or refuses it (a RequestRefused)."""

    def __init__(self):
        self.sent = []
        self.replies = {}
        self.refuse = set()

    def request(self, message):
        checked = protocol.validate(dict(message, id=1), protocol.CLIENT)
        assert isinstance(checked, protocol.Message), checked
        self.sent.append(message)
        if message["t"] in self.refuse:
            raise RequestRefused(protocol.ERROR_REFUSED, "no", {})
        return dict(self.replies.get(message["t"], {}))


@pytest.fixture
def mirror():
    client = FakeClient()
    session = ClientSession(
        request=client.request,
        provider=ClaudeProvider(),
        cwd="/home/u/project",
        options=SessionOptions(model="opus"),
    )
    session.pty, session.handle = 7, "s-4"
    return session, client


def event(**fields):
    return {"t": "session", "pty": 7, "handle": "s-4", **fields}


# -- the fields --------------------------------------------------------------------


def test_the_identity_is_seeded_from_the_tabs_arguments(mirror):
    session, _client = mirror
    assert session.session_id is None and session.cwd == "/home/u/project"
    assert session.options == SessionOptions(model="opus")
    assert session.current_model() == "opus"  # the options, until the transcript says


def test_every_field_lands_on_the_attribute_the_forwarder_reads(mirror):
    session, _client = mirror
    changed = session.apply(
        event(
            session=ID,
            fork=True,
            options={"model": "sonnet", "effort": "high", "add_dirs": ["/x"], "sandbox": True},
            command_override="claude --continue",
            cwd="/home/u/project",
            agent_cwd="/home/u/project/.claude/worktrees/x",
            pid=4242,
            resolver_cwd="/home/u/project",
            initial_command="claude",
            worktree_launch=True,
            new_chat_prompt="hi",
            shells_follow_armed=True,
            closing=False,
            sandboxed=True,
            sandbox_box="b" * 32,
            sandbox_plan_path="/run/plan.json",
            defaults_owed=True,
            can_restart=True,
            takes_prompt=False,
            entered={"text": "half", "rows_below": 1},
            prompt_block="This session isn't at an empty prompt.",
            unstarted=False,
            foreign_paste=False,
            agent_running=True,
            running_command=True,
            pasted_back={"[Pasted text #1 +2 lines]": "a\nb\nc"},
            paste_back_pending=["a"],
            model="claude-opus-5-5",
            effort="max",
            permission_mode="acceptEdits",
            transcript_path="/home/u/.claude/projects/p/" + ID + ".jsonl",
            prs=[{"url": "https://github.com/o/r/pull/1", "number": 1}],
            touched_files=["/home/u/project/a.py"],
            attachments=[{"key": "/x.png"}],
            ledger_armed=True,
            busy=True,
        )
    )
    assert session.session_id == ID and session.fork is True
    assert session.options == SessionOptions(model="sonnet", effort="high", add_dirs=("/x",), sandbox=True)
    assert session.command_override == "claude --continue"
    assert session.current_agent_cwd() == "/home/u/project/.claude/worktrees/x"
    assert session.child_pid() == 4242
    assert session.initial_command == "claude" and session.worktree_launch is True
    assert session.new_chat_prompt == "hi" and session.shells_follow_armed is True
    assert session.sandboxed and session.sandbox_box == "b" * 32
    assert session.sandbox_plan_path == "/run/plan.json" and session.can_restart_sandboxed()
    assert session.takes_prompt_now() is False
    assert session.entered_prompt() == EnteredPrompt(text="half", rows_below=1)
    assert session.prompt_block_text() == "This session isn't at an empty prompt."
    assert session.has_running_command() and session.agent_is_running()
    assert session.pasted_back == {"[Pasted text #1 +2 lines]": "a\nb\nc"}
    assert session.paste_back_pending == ["a"]
    assert session.model == "claude-opus-5-5" and session.current_model() == "claude-opus-5-5"
    assert session.current_effort() == "max" and session.current_permission_mode() == "acceptEdits"
    assert session.transcript_path.endswith(".jsonl")
    assert session.prs[0]["number"] == 1 and session.touched_files == ["/home/u/project/a.py"]
    assert session.attachments == [{"key": "/x.png"}] and session.ledger_armed and session.busy
    assert "session_id" in changed and "options" in changed and "busy" in changed


def test_a_partial_event_leaves_the_other_fields_as_they_were(mirror):
    """The snapshot comes whole on attach and changed fields after (D28):
    a field an event leaves out keeps its value."""
    session, _client = mirror
    session.apply(
        event(
            session=ID, agent_cwd="/a", takes_prompt=True, model="claude-opus-5-5",
            prs=[{"url": "https://github.com/o/r/pull/1", "number": 1}], busy=True,
        )
    )
    assert session.apply(event(takes_prompt=False)) == {"takes_prompt"}
    assert session.session_id == ID and session.current_agent_cwd() == "/a"
    assert session.current_model() == "claude-opus-5-5" and session.busy is True
    assert session.prs == [{"url": "https://github.com/o/r/pull/1", "number": 1}]
    assert session.takes_prompt_now() is False


def test_only_what_moved_is_reported(mirror):
    session, _client = mirror
    session.apply(event(takes_prompt=True, agent_cwd="/a"))
    assert session.apply(event(takes_prompt=True)) == set()
    assert session.apply(event(takes_prompt=False)) == {"takes_prompt"}
    assert session.takes_prompt_now() is False


def test_the_one_shots_are_reported_whenever_sent(mirror):
    session, _client = mirror
    assert session.apply(event(landed=True)) == {"landed"}
    assert session.apply(event(landed=True)) == {"landed"}
    assert session.apply(event(reset=True, finished=True)) == {"reset", "finished"}
    assert session.apply(event(forked="1f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")) == {"forked"}
    assert session.forked == "1f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
    assert session.apply(event(landed=False)) == set()


def test_an_event_for_another_pty_is_ignored(mirror):
    session, _client = mirror
    assert session.apply({"t": "session", "pty": 8, "handle": "s-9", "takes_prompt": True}) == set()
    assert session.takes_prompt_now() is False


def test_the_exit_blanks_the_reads(mirror):
    session, _client = mirror
    session.apply(event(takes_prompt=True, running_command=True, agent_running=True, pid=1, unstarted=True))
    session.exit()
    assert not session.takes_prompt_now() and not session.has_running_command()
    assert not session.agent_is_running() and session.child_pid() is None
    assert not session.unstarted_thread()
    assert session.prompt_block_text()  # a gone session takes no prompt


def test_the_model_and_effort_fall_back_to_the_options_while_the_transcript_is_silent(mirror):
    session, _client = mirror
    assert session.current_model() == "opus" and session.current_effort() == ""
    session.apply(event(model=None, effort=None))
    assert session.current_model() == "opus"
    assert session.model is None  # the footer hides the chip on None


# -- the requests --------------------------------------------------------------------


def test_prompts_switches_and_writes_are_the_protocols_requests(mirror):
    session, client = mirror
    session.inject_prompt("hello")
    session.inject_prompt_unfocused("hi\nthere")
    session.switch_model("sonnet", composer_open=True)
    session.switch_effort("max")
    session.write_text(" @a.py ")
    assert client.sent == [
        {"t": "prompt", "pty": 7, "text": "hello", "focus": True},
        {"t": "prompt", "pty": 7, "text": "hi\nthere", "focus": False},
        {"t": "switch", "pty": 7, "model": "sonnet", "composer_open": True},
        {"t": "switch", "pty": 7, "effort": "max", "composer_open": False},
        {"t": "write", "pty": 7, "text": " @a.py "},
    ]
    # A drop's tokens: the service puts the leading space in front.
    session.write_mention("@b.py @c.py ")
    assert client.sent[-1] == {"t": "write", "pty": 7, "text": "@b.py @c.py ", "mention": True}


def test_a_send_clears_the_composer_only_when_it_went(mirror):
    session, client = mirror
    cleared = []
    client.replies["send"] = {"sent": False}
    session.send_composed("draft", lambda: cleared.append(1))
    assert cleared == []
    assert client.sent[-1] == {"t": "send", "pty": 7, "text": "draft", "composer_open": True}
    client.replies["send"] = {"sent": True}
    session.send_composed("draft", lambda: cleared.append(2))
    assert cleared == [2]


def test_the_cut_is_begun_and_called_off_by_handle(mirror):
    session, client = mirror
    client.replies["cut"] = {"handle": "cut-3"}
    assert session.begin_cut() == "cut-3"
    session.cancel_cut("cut-3")
    session.cancel_cut()
    assert client.sent == [
        {"t": "cut", "pty": 7},
        {"t": "cut.cancel", "pty": 7, "handle": "cut-3"},
        {"t": "cut.cancel", "pty": 7},
    ]
    client.refuse.add("cut")
    assert session.begin_cut() is None


def test_a_draft_goes_back_through_the_service(mirror):
    session, client = mirror
    client.replies["draft.restore"] = {"restored": True}
    assert session.restore_draft("kept") is True
    client.replies["draft.restore"] = {"restored": False}
    assert session.restore_draft("kept") is False
    assert session.restore_draft("") is True  # nothing to put back


def test_the_mention_answers_the_refusals_reason(mirror):
    session, client = mirror
    assert session.mention("/home/u/project/a.py", 2, 4) is None
    assert client.sent[-1] == {
        "t": "mention", "pty": 7, "path": "/home/u/project/a.py", "start_line": 2, "end_line": 4,
    }
    client.refuse.add("mention")
    assert session.mention("/home/u/project/a.py") == "no"


def test_the_transcript_and_the_resolver_requests(mirror):
    session, client = mirror
    session.set_transcript_path("/t.jsonl")
    session.set_transcript_path(None)
    session.relocate_transcript("/w.jsonl")
    session.request_update(discover=True)
    session.restore_prs([{"url": "https://github.com/o/r/pull/1"}, "junk"])
    session.arm_resolver()
    assert [m["t"] for m in client.sent] == [
        "transcript.set", "transcript.set", "transcript.relocate", "transcript.update",
        "prs.restore", "resolver.arm",
    ]
    assert client.sent[0]["path"] == "/t.jsonl" and client.sent[1]["path"] is None
    assert client.sent[3]["discover"] is True
    assert client.sent[4]["records"] == [{"url": "https://github.com/o/r/pull/1"}]


def test_the_cwd_settles_as_a_follow_scope(mirror):
    session, client = mirror
    client.replies["cwd.settle"] = {"scope": "auto"}
    scope = session.settle_cwd("/home/u/project/.claude/worktrees/x", "/home/u/project")
    assert scope is editorfiles.FollowScope.AUTO
    client.replies["cwd.settle"] = {"scope": "offer"}
    assert session.settle_cwd("/elsewhere", "/home/u/project") is editorfiles.FollowScope.OFFER
    client.replies["cwd.settle"] = {"scope": ""}
    assert session.settle_cwd(None, "/home/u/project") is None
    session.set_shells_follow_armed(False)
    assert client.sent[-1] == {"t": "shells.follow", "pty": 7, "armed": False}
    assert session.shells_follow_armed is False


def test_the_close_flows_are_requests(mirror):
    session, client = mirror
    assert session.begin_close("\x03\x03", False) is True
    assert session.begin_close("/bg\r", True) is True
    session.nudge_exit()
    session.end_close()
    session.relaunch_without_worktree()
    assert client.sent == [
        {"t": "close", "pty": 7, "mode": "exit", "text": "\x03\x03"},
        {"t": "close", "pty": 7, "mode": "background", "text": "/bg\r"},
        {"t": "close.nudge", "pty": 7},
        {"t": "close.end", "pty": 7},
        {"t": "restart.worktreeless", "pty": 7},
    ]
    client.refuse.add("close")
    assert session.begin_close("\x03\x03", False) is False  # the window forces it


def test_the_restart_goes_by_the_box(mirror):
    session, client = mirror
    assert session.restart_sandboxed() is False  # no box: nothing to restart
    session.apply(event(sandbox_box="b" * 32))
    assert session.restart_sandboxed() is True
    assert client.sent[-1] == {"t": "sandbox.restart", "box": "b" * 32, "pty": 7, "handle": "s-4"}
    client.refuse.add("sandbox.restart")
    assert session.restart_sandboxed() is False


def test_nothing_is_asked_before_the_spawn_or_after_the_exit():
    client = FakeClient()
    session = ClientSession(request=client.request, provider=ClaudeProvider())
    session.inject_prompt("x")
    session.begin_cut()
    assert client.sent == []
    session.pty = 7
    session.exit()
    session.write_text("x")
    assert client.sent == []


def test_a_refusal_fails_soft(mirror):
    session, client = mirror
    client.refuse.add("prompt")
    session.inject_prompt("x")  # logged, not raised


# -- the probe (D27) ----------------------------------------------------------------------


def test_the_probe_reads_sets_and_calls_by_name(mirror):
    session, client = mirror
    client.replies["debug.session.get"] = {"value": {"a": 1}}
    assert session.probe("finish_ledger.armed") == {"a": 1}
    session.probe_set("session_id", ID)
    client.replies["debug.session.call"] = {"value": "ok"}
    assert session.probe_call("transcript.set_path", "/t.jsonl", discover=True) == "ok"
    assert client.sent[-2:] == [
        {"t": "debug.session.set", "pty": 7, "name": "session_id", "value": ID},
        {
            "t": "debug.session.call",
            "pty": 7,
            "name": "transcript.set_path",
            "args": ["/t.jsonl"],
            "kwargs": {"discover": True},
        },
    ]


def test_a_refused_probe_is_an_error_and_a_gone_pty_is_none(mirror):
    session, client = mirror
    client.refuse.add("debug.session.get")
    with pytest.raises(RuntimeError):
        session.probe("session_id")
    session.exit()
    assert session.probe("session_id") is None and session.probe_call("child_pid") is None
