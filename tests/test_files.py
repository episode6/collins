# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Files over the API, the service's end (collins.service.files; split-service
spec §3.23, PR-2.3): `fs.read` answers a file's text with its encoding,
mtime and size, flags latin-1 bytes and a binary file, refuses a read over
5 MiB (and over the request's own `max`), a directory and a missing file,
and confines the path; `fs.write` writes through a temp file and a
replace, keeps the mode, follows a symlink to the file, writes latin-1
back and falls back to UTF-8 when latin-1 can't carry the text, is refused
`stale` with the file untouched when the mtime moved, and writes
regardless with `expect_mtime` null; `fs.watch` reports a change once per
debounce with the file's stat, a deletion as `gone`, replaces a handle's
earlier watch, is dropped by `fs.unwatch` and by the client going away,
and refuses a directory kind; every read and write is a `Deferred`
settled off the main loop, and a worker that raises settles `failed`.

The core over the in-process harness (tests/inproc.py) with the worker
and the landing made inline, so a reply settles before `request` returns;
the watch tests pump the default main context for the debounce."""

from __future__ import annotations

import os
import stat
import time

import inproc
import pytest
from gi.repository import GLib

from collins.api import protocol
from collins.service import files
from collins.service.core import ServiceCore


@pytest.fixture
def served(tmp_path, monkeypatch):
    """A core whose files answer inline, a local loopback client and a
    project directory with one file."""
    monkeypatch.setenv("SHELL", "/bin/cat")
    core = ServiceCore(state_dir=tmp_path / "pty", get_setting=lambda key: True)
    core.files = files.Files(core, dispatch=lambda fn: fn(), spawn=lambda fn, name: fn())
    server = inproc.LoopbackServer(core)
    events: list[dict] = []
    client = server.connect(lambda *a: None, events.append, device="laptop")
    client.local = True
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.txt").write_text("one\ntwo\n")
    yield core, client, project, events
    server.shutdown()


def pump(seconds: float, until=None) -> bool:
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ctx.iteration(False)
        if until is not None and until():
            return True
        time.sleep(0.005)
    return until() if until is not None else True


def mtime_of(path) -> int:
    return os.stat(path).st_mtime_ns // 1000


# -- fs.read ---------------------------------------------------------------------------


def test_read_answers_text_encoding_mtime_and_size(served):
    core, client, project, _events = served
    reply = client.request({"t": "fs.read", "path": str(project / "a.txt")})
    assert reply["text"] == "one\ntwo\n" and reply["encoding"] == "utf-8"
    assert reply["mtime"] == mtime_of(project / "a.txt") and reply["size"] == 8
    assert reply["binary"] is False


def test_read_flags_latin_1(served):
    core, client, project, _events = served
    (project / "l1.txt").write_bytes(b"caf\xe9\n")
    reply = client.request({"t": "fs.read", "path": str(project / "l1.txt")})
    assert reply["encoding"] == "latin-1" and reply["text"] == "café\n"


def test_read_flags_a_binary_file_with_no_text(served):
    core, client, project, _events = served
    (project / "bin").write_bytes(b"\x89PNG\x00\x00abc")
    reply = client.request({"t": "fs.read", "path": str(project / "bin")})
    assert reply["binary"] is True and reply["text"] == "" and reply["size"] == 9


def test_read_over_five_mib_is_refused(served):
    core, client, project, _events = served
    big = project / "big.txt"
    with open(big, "wb") as fh:
        fh.truncate(protocol.FILE_TEXT_MAX + 1)
    with pytest.raises(inproc.RequestRefused) as refused:
        client.request({"t": "fs.read", "path": str(big)})
    assert refused.value.error == protocol.ERROR_REFUSED and "too large" in refused.value.msgid
    # The request's own cap, below the service's.
    with pytest.raises(inproc.RequestRefused) as refused:
        client.request({"t": "fs.read", "path": str(project / "a.txt"), "max": 4})
    assert refused.value.error == protocol.ERROR_REFUSED
    # At the cap exactly, it is read.
    with open(big, "wb") as fh:
        fh.truncate(protocol.FILE_TEXT_MAX)
    reply = client.request({"t": "fs.read", "path": str(big)})
    assert reply["size"] == protocol.FILE_TEXT_MAX and reply["binary"] is True


def test_read_refuses_a_directory_and_a_missing_file(served):
    core, client, project, _events = served
    with pytest.raises(inproc.RequestRefused) as refused:
        client.request({"t": "fs.read", "path": str(project)})
    assert refused.value.error == protocol.ERROR_REFUSED and "not a file" in refused.value.msgid
    with pytest.raises(inproc.RequestRefused) as refused:
        client.request({"t": "fs.read", "path": str(project / "nope.txt")})
    assert refused.value.error == protocol.ERROR_GONE


def test_read_write_and_watch_are_confined(served, tmp_path):
    """A client that is not local may name a path under a root the service
    knows and nothing else; a symlink out of the root is outside it."""
    core, client, project, _events = served
    client.local = False
    for message in (
        {"t": "fs.read", "path": str(project / "a.txt")},
        {"t": "fs.write", "path": str(project / "a.txt"), "text": "x", "expect_mtime": None},
        {"t": "fs.watch", "path": str(project / "a.txt"), "kind": "file", "handle": "w1"},
    ):
        with pytest.raises(inproc.RequestRefused) as refused:
            client.request(message)
        assert refused.value.error == protocol.ERROR_REFUSED
    core.store = type("Store", (), {"all_sessions": lambda self: [type("S", (), {"cwd": str(project)})()]})()
    assert client.request({"t": "fs.read", "path": str(project / "a.txt")})["text"] == "one\ntwo\n"
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (project / "link.txt").symlink_to(outside)
    with pytest.raises(inproc.RequestRefused):
        client.request({"t": "fs.read", "path": str(project / "link.txt")})


# -- fs.write --------------------------------------------------------------------------


def test_write_round_trips_and_answers_the_mtime_the_next_read_has(served):
    core, client, project, _events = served
    path = project / "a.txt"
    read = client.request({"t": "fs.read", "path": str(path)})
    message = {"t": "fs.write", "path": str(path), "text": "ONE\n", "encoding": "utf-8"}
    reply = client.request({**message, "expect_mtime": read["mtime"]})
    assert path.read_text() == "ONE\n" and reply["size"] == 4 and reply["encoding"] == "utf-8"
    assert reply["mtime"] == mtime_of(path) == client.request({"t": "fs.read", "path": str(path)})["mtime"]
    assert not [name for name in os.listdir(project) if name.startswith(".collins-")]  # no temp left


def test_write_with_a_moved_mtime_is_refused_stale_and_the_file_untouched(served):
    core, client, project, _events = served
    path = project / "a.txt"
    read = client.request({"t": "fs.read", "path": str(path)})
    path.write_text("edited elsewhere\n")
    os.utime(path, ns=(0, (read["mtime"] + 5_000_000) * 1000))  # 5 s later, whatever the clock did
    with pytest.raises(inproc.RequestRefused) as refused:
        client.request({"t": "fs.write", "path": str(path), "text": "mine\n", "expect_mtime": read["mtime"]})
    assert refused.value.error == protocol.ERROR_STALE
    assert path.read_text() == "edited elsewhere\n"
    # The Overwrite the user confirmed: null writes regardless.
    reply = client.request({"t": "fs.write", "path": str(path), "text": "mine\n", "expect_mtime": None})
    assert path.read_text() == "mine\n" and reply["mtime"] == mtime_of(path)


def test_write_of_a_vanished_file_with_an_expectation_is_stale(served):
    core, client, project, _events = served
    path = project / "a.txt"
    read = client.request({"t": "fs.read", "path": str(path)})
    path.unlink()
    with pytest.raises(inproc.RequestRefused) as refused:
        client.request({"t": "fs.write", "path": str(path), "text": "x", "expect_mtime": read["mtime"]})
    assert refused.value.error == protocol.ERROR_STALE and not path.exists()
    client.request({"t": "fs.write", "path": str(path), "text": "new\n", "expect_mtime": None})
    assert path.read_text() == "new\n"


def test_write_keeps_the_mode_and_follows_a_symlink(served):
    core, client, project, _events = served
    script = project / "run.sh"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    link = project / "link.sh"
    link.symlink_to(script)
    client.request({"t": "fs.write", "path": str(link), "text": "#!/bin/sh\necho\n", "expect_mtime": None})
    assert link.is_symlink() and script.read_text() == "#!/bin/sh\necho\n"
    assert stat.S_IMODE(script.stat().st_mode) == 0o755


def test_write_latin_1_back_and_utf_8_when_it_cannot_carry_the_text(served):
    core, client, project, _events = served
    path = project / "l1.txt"
    path.write_bytes(b"caf\xe9\n")
    read = client.request({"t": "fs.read", "path": str(path)})
    message = {"t": "fs.write", "path": str(path), "text": "café!\n", "encoding": "latin-1"}
    reply = client.request({**message, "expect_mtime": read["mtime"]})
    assert path.read_bytes() == b"caf\xe9!\n" and reply["encoding"] == "latin-1"
    reply = client.request({**message, "text": "café €\n", "expect_mtime": reply["mtime"]})
    assert path.read_bytes() == "café €\n".encode() and reply["encoding"] == "utf-8"


def test_write_failure_is_failed_with_the_reason(served):
    core, client, project, _events = served
    with pytest.raises(inproc.RequestRefused) as refused:
        client.request(
            {"t": "fs.write", "path": str(project / "no" / "dir.txt"), "text": "x", "expect_mtime": None}
        )
    assert refused.value.error == protocol.ERROR_FAILED and "Couldn't save" in refused.value.msgid


# -- off the main loop -----------------------------------------------------------------


def test_read_and_write_are_deferreds_settled_later(served):
    """With a real thread the reply settles on the dispatcher, never in
    the handler."""
    core, client, project, _events = served
    landed: list = []
    core.files = files.Files(core, dispatch=landed.append)  # the real thread, a captured landing
    frame = {"t": "fs.read", "id": 7, "path": str(project / "a.txt")}
    raw = core.handle(protocol.validate(frame, protocol.CLIENT), client)
    assert isinstance(raw, protocol.Deferred) and not raw.settled
    deadline = time.monotonic() + 5
    while not landed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert landed
    landed[0]()
    assert raw.settled and raw.reply["text"] == "one\ntwo\n"


def test_a_worker_that_raises_settles_failed(served, monkeypatch):
    core, client, project, _events = served

    def boom(path, max_bytes):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(files, "read_file", boom)
    with pytest.raises(inproc.RequestRefused) as refused:
        client.request({"t": "fs.read", "path": str(project / "a.txt")})
    assert refused.value.error == protocol.ERROR_FAILED and refused.value.details["error"] == "disk on fire"


# -- the watch -------------------------------------------------------------------------


def changes(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("t") == "file-changed"]


def test_watch_reports_a_change_once_per_debounce(served):
    core, client, project, events = served
    path = project / "a.txt"
    client.request({"t": "fs.watch", "path": str(path), "kind": "file", "handle": "w1"})
    assert core.files.watching(client, "w1")
    watch = core.files._watches[(id(client), "w1")]
    # Two writes in one burst: one check, one event, the file's stat now.
    path.write_text("one\ntwo\nthree\n")
    watch._on_event()
    path.write_text("one\ntwo\nthree\nfour\n")
    watch._on_event()
    assert watch._debounce != 0
    assert pump(2.0, lambda: changes(events))
    pump(0.5)
    assert len(changes(events)) == 1
    event = changes(events)[0]
    assert event["handle"] == "w1" and event["path"] == str(path) and event["gone"] is False
    assert event["mtime"] == mtime_of(path) and event["size"] == 19
    # A burst that changed nothing is no event; the next real change is one.
    watch._on_event()
    pump(0.5)
    assert len(changes(events)) == 1
    path.write_text("five\n")
    os.utime(path, ns=(0, (event["mtime"] + 1_000_000) * 1000))
    watch._on_event()
    assert pump(2.0, lambda: len(changes(events)) == 2)
    assert changes(events)[1]["size"] == 5


def test_the_monitor_itself_fires_the_watch(served):
    """The Gio monitor is real: a write with no hand-fired event lands."""
    core, client, project, events = served
    path = project / "a.txt"
    client.request({"t": "fs.watch", "path": str(path), "kind": "file", "handle": "w1"})
    pump(0.2)
    path.write_text("changed by a shell\n")
    assert pump(3.0, lambda: changes(events)), "the monitor never fired"


def test_watch_reports_a_deletion_as_gone(served):
    core, client, project, events = served
    path = project / "a.txt"
    client.request({"t": "fs.watch", "path": str(path), "kind": "file", "handle": "w1"})
    watch = core.files._watches[(id(client), "w1")]
    path.unlink()
    watch._on_event()
    assert pump(2.0, lambda: changes(events))
    assert changes(events)[0] == {
        "t": "file-changed", "handle": "w1", "path": str(path), "mtime": None, "size": None, "gone": True
    }


def test_watch_handles_unwatch_and_a_client_going_away(served):
    core, client, project, _events = served
    path = str(project / "a.txt")
    client.request({"t": "fs.watch", "path": path, "kind": "file", "handle": "w1"})
    first = core.files._watches[(id(client), "w1")]
    client.request({"t": "fs.watch", "path": path, "kind": "file", "handle": "w1"})
    assert first._stopped and core.files._watches[(id(client), "w1")] is not first
    client.request({"t": "fs.watch", "path": path, "kind": "file", "handle": "w2"})
    client.request({"t": "fs.unwatch", "handle": "w1"})
    assert not core.files.watching(client, "w1") and core.files.watching(client, "w2")
    client.request({"t": "fs.unwatch", "handle": "w1"})  # twice is fine
    core.client_gone(client)
    assert not core.files.watching(client, "w2")


def test_a_directory_watch_is_not_served_yet(served):
    core, client, project, _events = served
    with pytest.raises(inproc.RequestRefused) as refused:
        client.request({"t": "fs.watch", "path": str(project), "kind": "dir", "handle": "d1"})
    assert refused.value.error == protocol.ERROR_REFUSED


# -- the helpers -----------------------------------------------------------------------


def test_file_stat_of_a_directory_is_gone(tmp_path):
    assert files.file_stat(str(tmp_path)) == (None, None, True)
    assert files.file_stat(str(tmp_path / "nope")) == (None, None, True)
    (tmp_path / "f").write_text("x")
    mtime, size, gone = files.file_stat(str(tmp_path / "f"))
    assert (size, gone) == (1, False) and mtime == mtime_of(tmp_path / "f")
