# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The review of PR 629 (PR-3.6, the keeper) as tests: the must-fixes M1
to M4 each reproduced the way the reviewer's probes did and pinned, the
should-fixes that have a GTK-free seam (S1, S2, S4, S5), and the §5
rulings on an older keeper (Q2) and a kept pty with no row (Q3). GTK-free:
keeper subprocesses, real ptys, the GLib main context by hand."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time

import pytest
from test_keeper import (
    ENV,
    REPO,
    KeeperProcess,
    Recorder,
    Sink,
    kill_child,
    make_server,
    pump,
    settle,
    wait_for,
)

from collins.service import keeper
from collins.service import main as service_main


@pytest.fixture
def runtime():
    directory = tempfile.mkdtemp(prefix="ck-")
    yield directory
    import shutil

    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def kept(runtime):
    proc = KeeperProcess(runtime)
    yield proc
    proc.stop()


def open_fds(pid: int) -> list[str]:
    """What *pid* has open, as the links of ``/proc/<pid>/fd`` read."""
    out = []
    try:
        for name in os.listdir(f"/proc/{pid}/fd"):
            try:
                out.append(os.readlink(f"/proc/{pid}/fd/{name}"))
            except OSError:
                pass
    except OSError:
        pass
    return out


# -- M1: received fds are not inheritable ------------------------------------------------------


def test_a_child_spawned_after_an_adopt_holds_no_other_master(kept):
    """M1: the master a keeper takes with `adopt` (and every fd a service
    receives) is close-on-exec, so the next child the keeper forks (a
    sandboxed session's bwrap included) holds no other session's pty."""
    client = kept.client()
    own_pid, own_master = keeper.spawn_on_pty(["sleep", "30"], "/tmp", ENV, 80, 24)
    try:
        client.adopt(7, own_pid, own_master)
        pid, master = client.spawn(8, ["sleep", "30"], "/tmp", ENV, 80, 24)
        try:
            links = open_fds(pid)
            assert links, "the child's fd table could not be read"
            assert not any(link.startswith("/dev/ptmx") for link in links), links
            # The service's own received master is non-inheritable too.
            assert os.get_inheritable(master) is False
        finally:
            kill_child(pid)
            os.close(master)
    finally:
        kill_child(own_pid)
        os.close(own_master)


# -- M2: one keeper per socket -------------------------------------------------------------------


def test_a_second_keeper_for_the_same_socket_exits_zero_and_changes_nothing(kept):
    """M2: the keeper holds `keeper.lock` for its life; a second one for the
    path exits 0 at once, and the first keeps its socket, its pid file and
    every child."""
    client = kept.client()
    pid, master = client.spawn(1, ["sleep", "30"], "/tmp", ENV, 80, 24)
    try:
        second = subprocess.run(
            [sys.executable, "-m", "collins.service.keeper", "--socket", kept.socket],
            cwd=REPO, env={**ENV, "PYTHONPATH": REPO}, stdin=subprocess.DEVNULL, timeout=20,
        )  # fmt: skip
        assert second.returncode == 0
        assert keeper.read_pid(kept.socket) == kept.pid and os.path.exists(kept.socket)
        again = kept.client()
        listed = again.list(with_fds=False)
        assert [e["id"] for e in listed] == [1] and listed[0]["pid"] == pid and listed[0]["alive"]
        # The socket of a keeper that answers is never unlinked by another.
        assert keeper.socket_answers(kept.socket)
    finally:
        kill_child(pid)
        os.close(master)


def test_a_keepers_exit_removes_only_its_own_files(runtime):
    """M2's other half: a keeper that lost its socket to something else
    (here: the file replaced under it) removes neither the socket nor a pid
    file that is not its own at exit."""
    proc = KeeperProcess(runtime)
    try:
        os.unlink(proc.socket)
        os.symlink("/nonexistent", proc.socket)  # not its socket any more
        with open(os.path.join(runtime, keeper.PID_NAME), "w") as fh:
            fh.write("1")  # not its pid
    finally:
        assert proc.stop() == 0
    assert os.path.islink(proc.socket)
    with open(os.path.join(runtime, keeper.PID_NAME)) as fh:
        assert fh.read() == "1"
    os.unlink(proc.socket)


# -- M3: more ptys than a control message carries -----------------------------------------------


def test_the_list_pages_so_seventy_ptys_come_back_whole(kept):
    """M3: a `list` with fds is paged (`LIST_PAGE` a reply): every one of
    seventy ptys comes back with its master, where one message's control
    buffer lost everything past 64 (the reviewer's probe)."""
    client = kept.client()
    pids = []
    try:
        for n in range(1, 71):
            pid, master = client.spawn(n, ["sleep", "60"], "/tmp", ENV, 80, 24)
            pids.append(pid)
            os.close(master)
        listed = client.list(with_fds=True)
        try:
            assert [e["id"] for e in listed] == list(range(1, 71))
            assert all(e["fd"] > 2 and os.get_inheritable(e["fd"]) is False for e in listed)
            assert all(e["alive"] for e in listed)
        finally:
            for e in listed:
                os.close(e["fd"])
        assert len(client.list(with_fds=False)) == 70
    finally:
        for pid in pids:
            kill_child(pid)


def test_an_unreadable_keeper_list_leaves_every_row_and_model(tmp_path, app_state):
    """M3: a keeper whose list cannot be read is not an empty keeper. The
    core adopts nothing, drops no row, prunes no model and says so
    (`keeper_unread`), so the next service, or a fixed one, finds them."""
    from collins.service.core import ServiceCore

    state = app_state.AppState()
    app = "com.example.Unread"
    state.set_pty(app, 5, {"kind": "agent", "cwd": "/tmp", "cols": 80, "rows": 24, "session": "s5"})
    models = tmp_path / "pty" / app
    models.mkdir(parents=True)
    (models / "5.model").write_text("{}")

    class Unreadable:
        connected = True
        pid = 4242
        socket_path = "/nonexistent"

        def list(self, with_fds=True):
            raise keeper.KeeperError(74, "the keeper's list and its fds differ")

        def close(self):
            pass

    core = ServiceCore(state=state, state_dir=tmp_path / "pty", app_id=app)
    core.attach_keeper(Unreadable())
    assert core.adopt_ptys() == []
    assert core.keeper_unread is True
    row = {"kind": "agent", "cwd": "/tmp", "cols": 80, "rows": 24, "session": "s5"}
    assert state.get_ptys(app) == {5: row}
    assert (models / "5.model").exists()


# -- M4: two app ids on one state file --------------------------------------------------------


def test_two_app_ids_on_one_state_keep_each_others_rows_and_models(runtime, tmp_path, app_state):
    """M4 (the reviewer's probe, in one process): services A and B share
    `state.json` and the pty root; B's adoption from its own keeper drops
    none of A's rows and prunes none of A's models, and A's next
    adoption finds its pty where it left it."""
    from collins.service.core import ServiceCore

    state = app_state.AppState()
    keeper_a = KeeperProcess(runtime)
    keeper_b = KeeperProcess(tempfile.mkdtemp(prefix="ck-", dir=runtime))
    try:
        a = ServiceCore(state=state, state_dir=tmp_path / "pty", app_id="com.example.A")
        a.attach_keeper(keeper_a.client())
        pty_id = a.ptys.spawn("shell", ["cat"], "/tmp", ENV, 80, 24, session="sA")
        pty = a.ptys.get(pty_id)
        child = pty.pid
        a.ptys.attach(pty_id, Sink(), 80, 24)
        a.ptys.write(pty_id, b"hello A\r")
        pump(lambda: pty.screen.rows()[:2] == ["hello A"] * 2, what="A's echo")
        a.ptys.suspend()
        assert (tmp_path / "pty" / "com.example.A" / f"{pty_id}.model").exists()
        assert list(state.get_ptys("com.example.A")) == [pty_id]
        # B starts on the same state and pty root, with a keeper of its own
        # that holds nothing.
        b = ServiceCore(state=state, state_dir=tmp_path / "pty", app_id="com.example.B")
        b.attach_keeper(keeper_b.client())
        assert b.adopt_ptys() == []
        assert list(state.get_ptys("com.example.A")) == [pty_id]
        assert (tmp_path / "pty" / "com.example.A" / f"{pty_id}.model").exists()
        assert keeper.pid_alive(child)
        # B's own spawn takes B's ids, from B's counter, and B's model dir.
        b_id = b.ptys.spawn("shell", ["cat"], "/tmp", ENV, 80, 24)
        assert b_id == 1 and state.get_pty_next_id("com.example.B") == 2
        assert state.get_pty_next_id("com.example.A") == pty_id + 1
        b.ptys.get(b_id)._dirty = True
        b.ptys.save_model(b_id)
        b.ptys.wait_for_saves()
        assert (tmp_path / "pty" / "com.example.B" / "1.model").exists()
        b.shutdown()
        pump(lambda: not b.ptys.ptys, what="B's ptys to end")
        # A again: its pty is still the keeper's and the row's.
        a2 = ServiceCore(state=state, state_dir=tmp_path / "pty", app_id="com.example.A")
        a2.attach_keeper(keeper_a.client())
        assert a2.adopt_ptys() == [pty_id]
        adopted = a2.ptys.get(pty_id)
        assert adopted.pid == child and adopted.screen.rows()[:2] == ["hello A"] * 2
        assert adopted.session == "sA"  # its row was found under A's app id, not lost
        a2.ptys.write(pty_id, b"\x04")
        pump(lambda: not a2.ptys.ptys, what="A's pty to end")
        assert state.get_ptys("com.example.A") == {}
    finally:
        kill_child(child)
        keeper_a.stop()
        keeper_b.stop()
        settle(0.1)


# -- S1, S2, S4, S5 -------------------------------------------------------------------------------


def test_a_pty_spawned_while_the_keeper_is_lost_is_handed_to_the_next_one(kept, runtime, tmp_path):
    """S1: a pty forked by the server while the keeper was gone is adopted
    into the replacement when it lands (kept from then on), so a stop
    leaves it running too; its exit is still the child watch's, with its
    status."""
    recorder = Recorder()
    second: list[KeeperProcess] = []

    def restart():
        proc = KeeperProcess(runtime)
        second.append(proc)
        return proc.client()

    server = make_server(tmp_path, kept.client(), recorder, keeper_restart=restart)
    sink = Sink()
    first = server.spawn("shell", ["cat"], "/tmp", ENV, 80, 24)
    try:
        kept.stop(signal.SIGKILL)
        # The next spawn finds the keeper gone: forked here, unkept.
        unkept = server.spawn("shell", ["cat"], "/tmp", ENV, 80, 24)
        assert server.get(unkept).kept is False and server.get(unkept)._child_watch
        pump(lambda: bool(second) and server.keeper is not None and server.keeper.pid == second[0].pid,
             timeout=8, what="the replacement keeper")  # fmt: skip
        assert server.get(unkept).kept is True and server.get(first).kept is True
        listed = {e["id"]: e for e in server.keeper.list(with_fds=False)}
        assert set(listed) == {first, unkept} and all(e["adopted"] for e in listed.values())
        assert server.unkept_ptys() == []
        server.attach(unkept, sink, 80, 24)
        server.write(unkept, b"\x04", sink)
        pump(lambda: any(e["t"] == "pty-exited" for e in sink.events), timeout=6, what="the exit")
        assert next(e["status"] for e in sink.events if e["t"] == "pty-exited") == 0
    finally:
        # The server first (it drops its restart callable and poll), then
        # the keepers: a leftover poll firing under a later test's pump
        # would otherwise bring yet another keeper nobody stops.
        server.shutdown()
        pump(lambda: not server.ptys, timeout=8, what="the ptys to end")
        for proc in second:
            proc.stop()
        settle(0.1)


def test_an_adopted_pty_takes_the_keepers_size_not_the_rows(kept, tmp_path):
    """S2: the row's size is a second behind (its write is debounced); the
    keeper reads the master's `TIOCGWINSZ`, which the adoption takes."""
    recorder = Recorder()
    server = make_server(tmp_path, kept.client(), recorder)
    pty_id = server.spawn("shell", ["cat"], "/tmp", ENV, 80, 24)
    child = server.get(pty_id).pid
    try:
        server.resize(pty_id, 132, 40)
        assert recorder.rows[pty_id]["cols"] == 80  # the debounce has not written
        server.suspend()
        assert recorder.rows[pty_id]["cols"] == 132  # suspend flushes it (S2)
        again = make_server(tmp_path, kept.client(), recorder)
        entry = again.keeper.list(with_fds=True)[0]
        assert (entry["cols"], entry["rows"]) == (132, 40)
        stale = dict(recorder.rows[pty_id], cols=80, rows=24)
        adopted = again.adopt(pty_id, entry["fd"], entry["pid"], stale, grid=(entry["cols"], entry["rows"]))
        assert (adopted.cols, adopted.rows) == (132, 40)
        assert keeper.get_window_size(adopted.master) == (132, 40)
        assert adopted.screen.cols == 132
    finally:
        kill_child(child)
        settle(0.1)


def test_a_partial_line_from_one_client_stalls_no_other(kept):
    """S4: a client that sends half a request (or none) does not hold the
    keeper's loop; another client's call is answered meanwhile."""
    stuck = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stuck.connect(kept.socket)
    stuck.sendall(b'{"t": "list"')  # no newline, ever
    time.sleep(0.2)
    client = kept.client()
    started = time.monotonic()
    assert client.list(with_fds=False) == []
    assert time.monotonic() - started < 1.0
    # Two requests in one write are both answered (N3).
    two = b'{"t": "list", "fds": false, "v": 1}\n{"t": "list", "fds": false, "v": 1}\n'
    client.sock.sendall(two)
    line1, _ = keeper._recv_line(client.sock, client._buffer)
    line2, _ = keeper._recv_line(client.sock, client._buffer)
    assert json.loads(line1)["ok"] and json.loads(line2)["ok"]
    # A line past the bound drops that client alone.
    stuck.sendall(b"x" * (keeper.LINE_MAX + 10))
    wait_for(lambda: _closed(stuck), 5, "the stuck client to be dropped")
    assert client.list(with_fds=False) == []
    stuck.close()


def _closed(sock: socket.socket) -> bool:
    sock.settimeout(0.2)
    try:
        return sock.recv(1) == b""
    except TimeoutError:
        return False
    except OSError:
        return True


def test_fds_on_requests_that_take_none_are_closed(kept):
    """S5: an fd that rides on a `list`, a `resize`, an unknown id or a
    duplicate `adopt` is closed by the keeper on every path, so its fd
    table does not grow and no child inherits it."""
    client = kept.client()
    before = len(open_fds(kept.pid))
    pid, master = client.spawn(1, ["sleep", "30"], "/tmp", ENV, 80, 24)
    try:
        for _ in range(20):
            r, w = os.pipe()
            client.call({"t": "list", "fds": False}, [r])
            client.call({"t": "resize", "id": 1, "cols": 80, "rows": 24}, [w])
            os.close(r)
            os.close(w)
            r, w = os.pipe()
            with pytest.raises(keeper.KeeperError):
                client.call({"t": "signal", "id": 99, "signal": 0}, [r])
            with pytest.raises(keeper.KeeperError):
                client.call({"t": "adopt", "id": 1, "pid": pid}, [w])  # duplicate
            os.close(r)
            os.close(w)
        after = len(open_fds(kept.pid))
        assert after <= before + 2, (before, after)
    finally:
        kill_child(pid)
        os.close(master)


# -- the rulings: Q2 and Q3 ------------------------------------------------------------------------


def test_a_keeper_of_another_protocol_holding_ptys_is_left_alone(kept, monkeypatch):
    """Q2 (PR-3.6's ruling): a service whose protocol the keeper does not
    speak neither replaces it nor adopts from it while it holds ptys: no
    keeper, every row untouched, `KeeperMismatch` with how many it holds.
    One holding nothing is stopped and replaced."""
    client = kept.client()
    pid, master = client.spawn(1, ["sleep", "30"], "/tmp", ENV, 80, 24)
    os.close(master)
    monkeypatch.setattr(keeper, "KEEPER_PROTOCOL", 2)  # this "service" speaks 2
    try:
        with pytest.raises(service_main.KeeperMismatch) as caught:
            service_main.connect_keeper(kept.socket)
        assert caught.value.held == 1 and caught.value.protocol == 1
        assert keeper.pid_alive(kept.pid) and keeper.pid_alive(pid)
        # Through `_probe_keeper`'s caller, with no replacement started.
        monkeypatch.setattr(service_main, "start_keeper_process", lambda *a: pytest.fail("started one"))
        monkeypatch.setattr(service_main.api_server, "keeper_socket_path", lambda app_id: kept.socket)
        with pytest.raises(service_main.KeeperMismatch):
            service_main.find_or_start_keeper("com.example.Mismatch")
    finally:
        kill_child(pid)
    # Now it holds nothing: the probe stops it (SIGTERM) and answers None,
    # so the caller starts one that speaks its protocol.
    monkeypatch.setattr(keeper, "KEEPER_PROTOCOL", 1)
    assert client.reap(1, wait=2.0)[1] is False
    monkeypatch.setattr(keeper, "KEEPER_PROTOCOL", 2)
    assert service_main.connect_keeper(kept.socket) is None
    wait_for(lambda: not keeper.pid_alive(kept.pid), 10, "the empty old keeper to stop")


def test_a_kept_pty_with_no_row_is_adopted_as_a_shell_and_never_signalled(kept, tmp_path, app_state):
    """Q3 (PR-3.6's ruling): a pty the keeper holds whose row the state
    lost is adopted as an anonymous shell (its cwd off /proc, no session),
    recorded, and never signalled for the missing row: the child lives."""
    from collins.service.core import ServiceCore

    state = app_state.AppState()
    app = "com.example.NoRow"
    core = ServiceCore(state=state, state_dir=tmp_path / "pty", app_id=app)
    core.attach_keeper(kept.client())
    pty_id = core.ptys.spawn("agent", ["sleep", "60"], str(tmp_path), ENV, 80, 24, session="lost")
    child = core.ptys.get(pty_id).pid
    try:
        core.ptys.suspend()
        state.remove_pty(app, pty_id)  # the row write that was lost
        again = ServiceCore(state=state, state_dir=tmp_path / "pty", app_id=app)
        again.attach_keeper(kept.client())
        assert again.adopt_ptys() == [pty_id]
        adopted = again.ptys.get(pty_id)
        assert adopted.kind == "shell" and adopted.session is None and adopted.pid == child
        assert adopted.cwd == str(tmp_path)
        assert keeper.pid_alive(child)
        assert state.get_ptys(app)[pty_id]["kind"] == "shell"
        assert pty_id not in again.sessions
    finally:
        kill_child(child)
        settle(0.1)
