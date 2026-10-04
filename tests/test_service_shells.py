# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Panel shells on the service (PR-1.8, spec §3.15): ptys of kind ``shell``
through the loopback and `ServiceCore`, against real children. The spawn
(the user's own environment, a sandboxed one on the plan the service's
records hold for its box, refused with none, its ``cd`` queued), the pty
server's reads a panel shell asks for (the shell's cwd from ``/proc``, its
foreground, the shell inside a box), ``clear`` on a shell, and the panel
history written from the models (`ServiceCore.write_panel_history`)."""

import os
import time

import pytest
from gi.repository import GLib

import collins.panelhistory as panelhistory
from collins import providers
from collins.api import loopback, protocol
from collins.service import core as core_mod
from collins.service import ptyserver
from collins.service.core import ServiceCore

CAT = "/bin/cat"
SH = "/bin/sh"


def pump(seconds=0.5, until=None):
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ctx.iteration(False)
        if until is not None and until():
            return True
        time.sleep(0.005)
    return until() if until is not None else True


class Ends:
    """A client's two callbacks, recorded."""

    def __init__(self):
        self.output = []
        self.events = []

    def on_output(self, pty, data, flags):
        self.output.append((pty, bytes(data), flags))

    def on_event(self, event):
        self.events.append(event)

    def live(self, pty):
        return b"".join(d for p, d, f in self.output if p == pty and f == 0)


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setattr(panelhistory, "_HISTORY_DIR", tmp_path / "panel_history")
    return tmp_path / "panel_history"


def make(tmp_path, shell=CAT, plans=None, environment=None):
    """A loopback over a fresh core, its client and recorded ends."""
    os.environ["SHELL"] = shell
    plans = plans or {}
    core = ServiceCore(
        state_dir=tmp_path / "pty",
        get_setting=lambda key: True,
        environment=environment,
        sandbox_plan=plans.get,
    )
    srv = loopback.LoopbackServer(core)
    ends = Ends()
    client = srv.connect(ends.on_output, ends.on_event, device="laptop")
    return srv, client, ends


@pytest.fixture
def restore_shell():
    saved = os.environ.get("SHELL")
    servers = []
    yield servers
    for srv in servers:
        srv.shutdown()
    pump(0.3)
    if saved is None:
        os.environ.pop("SHELL", None)
    else:
        os.environ["SHELL"] = saved


def spawn_shell(client, cwd, **extra):
    reply = client.request({"t": "spawn", "kind": "shell", "cwd": str(cwd), "cols": 80, "rows": 24, **extra})
    pty = reply["pty"]
    client.request({"t": "attach", "pty": pty, "cols": 80, "rows": 24})
    return pty


def child_environ(pid):
    with open(f"/proc/{pid}/environ", "rb") as f:
        return dict(item.split(b"=", 1) for item in f.read().split(b"\0") if b"=" in item)


# -- the spawn


def test_a_panel_shell_has_vtes_variables_and_no_progress_declarations(tmp_path, restore_shell):
    """The VTE path never gave a panel shell the agent's two progress
    declarations; the service does not either, whatever the setting."""
    bare = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}
    srv, client, _ends = make(tmp_path, environment=lambda: bare)
    restore_shell.append(srv)
    pty = spawn_shell(client, tmp_path)
    env = child_environ(client.pty_of(pty).child_pid())
    assert env[b"TERM"] == b"xterm-256color" and env[b"COLORTERM"] == b"truecolor"
    assert b"ConEmuANSI" not in env and b"TERM_PROGRAM" not in env
    assert client.pty_of(pty).kind == "shell"


def test_a_sandboxed_shell_with_no_plan_is_refused(tmp_path, restore_shell):
    srv, client, _ends = make(tmp_path)
    restore_shell.append(srv)
    for extra in ({"sandbox": True}, {"sandbox": True, "sandbox_box": "box-1"}):
        with pytest.raises(loopback.RequestRefused) as refused:
            client.request({"t": "spawn", "kind": "shell", "cwd": str(tmp_path), **extra})
        assert refused.value.error == protocol.ERROR_REFUSED
        assert "no sandbox plan" in refused.value.msgid
    assert srv.core.ptys.ptys == {}


def test_a_sandboxed_shell_runs_the_launcher_on_its_boxs_plan(tmp_path, restore_shell, monkeypatch):
    """The argv is `providers.sandboxed_shell_argv(plan, $SHELL)` on the
    plan the service's records hold for the box the client names (never a
    plan the client hands over), the pty keeps both, and a cwd inside the
    workspace that is not where bwrap lands the shell is typed as a `cd`
    ahead of anything else."""
    workspace = tmp_path / "ws"
    inside = workspace / "sub dir"
    inside.mkdir(parents=True)
    plan = str(tmp_path / "plan.json")
    seen = []

    def argv(plan_path, shell):
        seen.append((plan_path, shell))
        return [CAT]  # stands in for the launcher: echoes what is typed

    monkeypatch.setattr(providers, "sandboxed_shell_argv", argv)
    monkeypatch.setattr(core_mod.sandboxplan, "load_plan", lambda path: {"workspace": str(workspace)})
    monkeypatch.setattr(core_mod.sandboxplan, "plan_start_dir", lambda loaded: loaded["workspace"])
    srv, client, ends = make(tmp_path, shell="/bin/the-users-shell", plans={"box-1": plan})
    restore_shell.append(srv)
    pty = spawn_shell(client, inside, sandbox=True, sandbox_box="box-1")
    assert seen == [(plan, "/bin/the-users-shell")]
    found = client.pty_of(pty)
    assert (found.box, found.plan) == ("box-1", plan)
    assert pump(2, lambda: b"cd '" + str(inside).encode() + b"'" in ends.live(pty))

    # Where bwrap lands it already: nothing typed.
    other = spawn_shell(client, workspace, sandbox=True, sandbox_box="box-1")
    pump(0.3)
    assert b"cd " not in ends.live(other)


def test_a_sandboxed_shells_shell_is_the_one_inside_the_box(tmp_path, restore_shell, monkeypatch):
    """Busy and cwd are read against the shell inside the box (found once,
    then remembered); a plain shell's is the child itself."""
    asked = []

    def inner(pid):
        asked.append(pid)
        return 4242

    monkeypatch.setattr(ptyserver.proctree, "inner_shell_pid", inner)
    monkeypatch.setattr(providers, "sandboxed_shell_argv", lambda plan, shell: [CAT])
    monkeypatch.setattr(core_mod.sandboxplan, "load_plan", lambda path: None)
    srv, client, _ends = make(tmp_path, plans={"box-1": "/plan"})
    restore_shell.append(srv)
    boxed = client.pty_of(spawn_shell(client, tmp_path, sandbox=True, sandbox_box="box-1"))
    assert boxed.shell_pid() == 4242 and boxed.shell_pid() == 4242
    assert asked == [boxed.child_pid()]
    plain = client.pty_of(spawn_shell(client, tmp_path))
    assert plain.shell_pid() == plain.child_pid()


# -- the pty server's reads


def test_the_shells_cwd_and_foreground_are_read_on_the_service(tmp_path, restore_shell):
    sub = tmp_path / "elsewhere"
    sub.mkdir()
    bare = {"PATH": os.environ["PATH"], "PS1": "$ "}
    srv, client, _ends = make(tmp_path, shell=SH, environment=lambda: bare)
    restore_shell.append(srv)
    pty = spawn_shell(client, tmp_path)
    found = client.pty_of(pty)
    assert pump(3, lambda: found.process_cwd() == str(tmp_path))
    assert pump(3, lambda: not found.has_running_command())
    client.send_input(pty, f"cd '{sub}'\n".encode())
    assert pump(3, lambda: found.process_cwd() == str(sub))
    client.send_input(pty, b"sleep 30\n")
    assert pump(3, found.has_running_command)
    client.send_input(pty, b"\x03")
    assert pump(3, lambda: not found.has_running_command())


def test_the_reads_go_quiet_once_the_shell_is_gone(tmp_path, restore_shell):
    srv, client, _ends = make(tmp_path, shell="/bin/true")
    restore_shell.append(srv)
    reply = client.request({"t": "spawn", "kind": "shell", "cwd": str(tmp_path)})
    found = client.pty_of(reply["pty"])
    assert pump(3, lambda: found.child_pid() is None)
    assert found.shell_pid() is None
    assert found.process_cwd() is None
    assert found.has_running_command() is False


# -- clear


def test_clear_wipes_a_shells_model_and_refuses_an_agent(tmp_path, restore_shell):
    srv, client, ends = make(tmp_path)
    restore_shell.append(srv)
    pty = spawn_shell(client, tmp_path)
    client.send_input(pty, b"hello\r")
    # The tty's echo, then cat's own line: both in before the wipe.
    assert pump(2, lambda: "hello\nhello" in client.screen_of(pty).capture_contents())
    client.request({"t": "clear", "pty": pty})
    assert client.screen_of(pty).capture_contents() == ""
    # The model is live again: what the shell says next lands in it.
    client.send_input(pty, b"again\r")
    assert pump(2, lambda: "again" in client.screen_of(pty).capture_contents())
    assert "hello" not in client.screen_of(pty).capture_contents()

    agent = client.request({"t": "spawn", "kind": "agent", "cwd": str(tmp_path)})["pty"]
    with pytest.raises(loopback.RequestRefused) as refused:
        client.request({"t": "clear", "pty": agent})
    assert refused.value.error == protocol.ERROR_REFUSED


# -- the panel history


def test_the_panel_history_is_written_from_the_models(tmp_path, restore_shell, history):
    """Each shell under its ordinal from its pty's model, a shell with no
    pty from the text given, and the mapping is the keep-set."""
    srv, client, _ends = make(tmp_path)
    restore_shell.append(srv)
    pty = spawn_shell(client, tmp_path)
    client.send_input(pty, b"echoed by cat\r")
    assert pump(2, lambda: "echoed by cat\nechoed by cat" in client.screen_of(pty).capture_contents())
    panelhistory.save("sess", "a closed shell's", 5)
    srv.write_panel_history("sess", {0: pty, 2: "restored text\nkept"})
    assert panelhistory.load("sess", 0) == client.screen_of(pty).capture_contents().rstrip()
    assert "echoed by cat" in panelhistory.load("sess", 0)
    assert panelhistory.load("sess", 2) == "restored text\nkept"
    assert panelhistory.ordinals("sess") == [0, 2]


def test_a_shell_already_gone_writes_nothing(tmp_path, restore_shell, history):
    """The model file goes with the pty's row, so the history is written
    while the shell lives; one already gone clears its file, as a blank
    capture does."""
    srv, client, _ends = make(tmp_path)
    restore_shell.append(srv)
    pty = spawn_shell(client, tmp_path)
    client.send_input(pty, b"before the end\r")
    assert pump(2, lambda: "before the end\nbefore the end" in client.screen_of(pty).capture_contents())
    srv.write_panel_history("sess", {0: pty})
    assert "before the end" in (panelhistory.load("sess", 0) or "")
    client.request({"t": "close", "pty": pty, "mode": "kill"})
    assert pump(3, lambda: pty not in srv.core.ptys.ptys)
    srv.write_panel_history("sess", {0: pty})
    assert panelhistory.load("sess", 0) is None
