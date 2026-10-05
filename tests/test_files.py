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
and (PR-2.4) watches a directory too, one `dir-changed` per debounce;
`fs.stat` answers the kind a symlink names, a file's size, the mtime and
whether the path is inside a root; `fs.list` answers a directory's entries
in the tree's order with the ignored names marked, cut at 5000 and saying
so, confined to a known root and to the root it names; `fs.walk` answers a
root's files, capped at 20 000; `fs.names` (PR-2.6) a root's non-directory
names for the bare root-name links, bounded at 5000, a root outside every
root refused unless the client is `local`; every read and write is a `Deferred`
settled off the main loop, and a worker that raises settles `failed`.

The core over the in-process harness (tests/inproc.py) with the worker
and the landing made inline, so a reply settles before `request` returns;
the watch tests pump the default main context for the debounce."""

from __future__ import annotations

import os
import stat
import threading
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

    def boom(path, max_bytes, roots=None):
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


def dir_changes(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("t") == "dir-changed"]


def test_a_dir_watch_reports_once_per_debounce(served):
    """PR-2.4: a burst of changes in a watched directory is one
    `dir-changed {handle, path}`; the next burst is another; the watch
    goes with `fs.unwatch`."""
    core, client, project, events = served
    client.request({"t": "fs.watch", "path": str(project), "kind": "dir", "handle": "d1"})
    watch = core.files._watches[(id(client), "d1")]
    assert isinstance(watch, files._DirWatch)
    for name in ("b.txt", "c.txt", "d.txt"):
        (project / name).write_text("x")
        watch._on_event()
    assert pump(2.0, lambda: dir_changes(events))
    pump(0.6)
    assert dir_changes(events) == [{"t": "dir-changed", "handle": "d1", "path": str(project)}]
    (project / "e.txt").write_text("x")
    watch._on_event()
    assert pump(2.0, lambda: len(dir_changes(events)) == 2)
    client.request({"t": "fs.unwatch", "handle": "d1"})
    assert watch._stopped and not core.files.watching(client, "d1")


def test_a_dir_watchs_monitor_is_real(served):
    """A file a shell makes in the directory lands with no hand-fired
    event (what the tree's live refresh rides)."""
    core, client, project, events = served
    client.request({"t": "fs.watch", "path": str(project), "kind": "dir", "handle": "d1"})
    pump(0.2)
    (project / "from-a-shell.txt").write_text("hello\n")
    assert pump(3.0, lambda: dir_changes(events)), "the directory monitor never fired"


def test_a_dir_watch_is_confined_and_bounded_like_a_files(served):
    core, client, project, _events = served
    client.local = False
    with pytest.raises(inproc.RequestRefused) as refused:
        client.request({"t": "fs.watch", "path": str(project), "kind": "dir", "handle": "d1"})
    assert refused.value.error == protocol.ERROR_REFUSED


# -- fs.stat, fs.list, fs.walk (PR-2.4) ------------------------------------------------


def test_stat_answers_kind_size_mtime_and_inside(served, tmp_path):
    core, client, project, _events = served
    path = project / "a.txt"
    reply = client.request({"t": "fs.stat", "path": str(path), "root": str(project)})
    assert reply["kind"] == "file" and reply["size"] == 8 and reply["mtime"] == mtime_of(path)
    assert reply["inside"] is True
    reply = client.request({"t": "fs.stat", "path": str(project), "root": str(project)})
    assert reply["kind"] == "dir" and reply["size"] is None and reply["inside"] is True
    reply = client.request({"t": "fs.stat", "path": str(project / "nope"), "root": str(project)})
    assert reply == {"kind": "missing", "size": None, "mtime": None, "inside": False}
    (project / "dangling").symlink_to(project / "nowhere")
    assert client.request({"t": "fs.stat", "path": str(project / "dangling")})["kind"] == "symlink"
    # A link to a file is the file; one out of the root is not inside it.
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (project / "out.txt").symlink_to(outside)
    reply = client.request({"t": "fs.stat", "path": str(project / "out.txt"), "root": str(project)})
    assert reply["kind"] == "file" and reply["inside"] is False


def test_stat_is_unconfined_and_inside_of_every_root_without_one(served, tmp_path):
    """A stat leaks nothing a shell could not: a client that is not local
    may stat anything; with no root named, `inside` is of every root the
    service knows."""
    core, client, project, _events = served
    client.local = False
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_text("x")
    reply = client.request({"t": "fs.stat", "path": str(elsewhere)})
    assert reply["kind"] == "file" and reply["inside"] is False
    assert client.request({"t": "fs.stat", "path": str(project / "a.txt")})["inside"] is False
    core.store = type("Store", (), {"all_sessions": lambda self: [type("S", (), {"cwd": str(project)})()]})()
    assert client.request({"t": "fs.stat", "path": str(project / "a.txt")})["inside"] is True


def test_list_answers_the_trees_order_and_kinds(served):
    core, client, project, _events = served
    (project / "src").mkdir()
    (project / "Zeta.md").write_text("z")
    (project / ".hidden").write_text("h")
    (project / "node_modules").mkdir()
    (project / "link").symlink_to(project / "src")
    message = {"t": "fs.list", "path": str(project), "hidden": False, "root": str(project)}
    reply = client.request(message)
    assert [(e["name"], e["kind"]) for e in reply["entries"]] == [
        ("link", "symlink"), ("src", "dir"), ("a.txt", "file"), ("Zeta.md", "file"),
    ]
    assert reply["truncated"] is False
    names = [e["name"] for e in client.request({**message, "hidden": True})["entries"]]
    assert ".hidden" in names and "node_modules" not in names


def test_list_marks_the_ignored_names(served):
    """The check-ignore the tree ran on the main loop runs on the service,
    in the listing's directory, and marks the entries."""
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("no git")
    core, client, project, _events = served
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    (project / ".gitignore").write_text("build.log\nout/\n")
    (project / "build.log").write_text("x")
    (project / "out").mkdir()
    reply = client.request({"t": "fs.list", "path": str(project), "hidden": True, "root": str(project)})
    ignored = {e["name"]: e["ignored"] for e in reply["entries"]}
    assert ignored == {"out": True, ".gitignore": False, "a.txt": False, "build.log": True}


def test_list_of_more_than_5000_entries_is_truncated(served):
    core, client, project, _events = served
    big = project / "big"
    big.mkdir()
    for index in range(5003):
        (big / f"f{index:05d}").touch()
    reply = client.request({"t": "fs.list", "path": str(big), "hidden": False, "root": str(project)})
    assert len(reply["entries"]) == protocol.FS_LIST_MAX == 5000
    assert reply["truncated"] is True and reply["entries"][-1]["name"] == "f04999"


def test_list_is_confined_to_a_known_root_and_to_its_own(served, tmp_path):
    core, client, project, _events = served
    other = tmp_path / "other"
    other.mkdir()
    # The root it names: the directory must resolve inside it.
    with pytest.raises(inproc.RequestRefused) as refused:
        client.request({"t": "fs.list", "path": str(other), "hidden": False, "root": str(project)})
    assert refused.value.error == protocol.ERROR_REFUSED
    (project / "escape").symlink_to(other)
    with pytest.raises(inproc.RequestRefused):
        client.request(
            {"t": "fs.list", "path": str(project / "escape"), "hidden": False, "root": str(project)}
        )
    # A client that is not local: only under a root the service knows,
    # whatever root it names.
    client.local = False
    with pytest.raises(inproc.RequestRefused):
        client.request({"t": "fs.list", "path": str(other), "hidden": False, "root": str(other)})
    core.store = type("Store", (), {"all_sessions": lambda self: [type("S", (), {"cwd": str(project)})()]})()
    # Nor may it name a looser root than the ones the service knows: an
    # ancestor would let the link out of the project be listed.
    with pytest.raises(inproc.RequestRefused):
        client.request({"t": "fs.list", "path": str(project), "hidden": False, "root": str(tmp_path)})
    reply = client.request({"t": "fs.list", "path": str(project), "hidden": False, "root": str(project)})
    assert [e["name"] for e in reply["entries"]] == ["a.txt"]  # the link out of the root is not listed
    with pytest.raises(inproc.RequestRefused) as refused:
        client.request({"t": "fs.list", "path": str(project / "gone"), "hidden": False, "root": str(project)})
    assert refused.value.error == protocol.ERROR_GONE


def test_walk_answers_relative_paths_breadth_first(served):
    core, client, project, _events = served
    (project / "src" / "deep").mkdir(parents=True)
    (project / "src" / "b.py").write_text("b")
    (project / "src" / "deep" / "c.py").write_text("c")
    (project / ".env").write_text("e")
    reply = client.request({"t": "fs.walk", "root": str(project), "hidden": False})
    assert reply == {"paths": ["a.txt", "src/b.py", "src/deep/c.py"], "truncated": False}
    hidden = client.request({"t": "fs.walk", "root": str(project), "hidden": True})["paths"]
    assert ".env" in hidden


def test_a_walk_is_capped_at_20000(served):
    core, client, project, _events = served
    for part in range(5):  # under the listing's 5000 each, 22 501 in all
        directory = project / f"d{part}"
        directory.mkdir()
        for index in range(4500):
            (directory / f"f{index}").touch()
    reply = client.request({"t": "fs.walk", "root": str(project), "hidden": False})
    assert len(reply["paths"]) == protocol.FS_WALK_MAX == 20_000 and reply["truncated"] is True
    assert reply["paths"][0] == "a.txt"


def test_walk_is_confined_to_a_known_root(served, tmp_path):
    core, client, project, _events = served
    client.local = False
    with pytest.raises(inproc.RequestRefused) as refused:
        client.request({"t": "fs.walk", "root": str(project), "hidden": False})
    assert refused.value.error == protocol.ERROR_REFUSED
    core.store = type("Store", (), {"all_sessions": lambda self: [type("S", (), {"cwd": str(project)})()]})()
    assert client.request({"t": "fs.walk", "root": str(project), "hidden": False})["paths"] == ["a.txt"]


def test_names_answers_the_non_directory_names_of_a_root(served, tmp_path):
    core, client, project, _events = served
    (project / "README.md").write_text("r")
    (project / ".env").write_text("e")
    (project / "src").mkdir()
    (project / "elsewhere").symlink_to(tmp_path)  # a link to a directory is a directory
    (project / "alias.txt").symlink_to(project / "a.txt")  # a link to a file is a file
    (project / "dangling").symlink_to(project / "nope")  # a link naming nothing is not a directory
    reply = client.request({"t": "fs.names", "root": str(project)})
    assert reply == {"names": [".env", "README.md", "a.txt", "alias.txt", "dangling"], "truncated": False}


def test_names_is_bounded_and_leaves_out_a_name_over_the_bound(served, monkeypatch):
    core, client, project, _events = served
    (project / "a-name-over-the-bound").write_text("long")
    monkeypatch.setattr(protocol, "FS_NAME_MAX", 12)  # the filesystem's own is 255
    assert "a-name-over-the-bound" not in files.names_reply(str(project))["names"]
    monkeypatch.setattr(protocol, "FS_NAME_MAX", 1024)
    for index in range(protocol.FS_NAMES_MAX + 2):
        (project / f"f{index:05d}").touch()
    reply = client.request({"t": "fs.names", "root": str(project)})
    assert len(reply["names"]) == protocol.FS_NAMES_MAX == 5000 and reply["truncated"] is True
    assert all(len(name) <= protocol.FS_NAME_MAX for name in reply["names"])


def test_a_full_bound_of_long_names_chunks_through_the_real_framing(tmp_path):
    """Review of PR 614: 5000 names of 250 characters validate but are
    past a frame as one message; `names` travels as TAG_BLOB frames like a
    walk's paths, and the client's join gives the list back."""
    for index in range(protocol.FS_NAMES_MAX + 1):
        (tmp_path / f"{index:05d}-{'n' * 244}.txt").touch()
    fields = files.names_reply(str(tmp_path))
    assert len(fields["names"]) == protocol.FS_NAMES_MAX and fields["truncated"] is True
    reply = protocol.reply(77, **fields)
    assert not isinstance(protocol.validate_response(reply, "fs.names"), protocol.Refusal)
    with pytest.raises(ValueError, match="frame limit"):
        protocol.encode(reply)  # unchunked, it would not fit
    frames, slim = protocol.split_reply(reply)
    assert frames and "names" not in slim and slim["names_chunked"] is True
    data = b"".join(protocol.unpack_frame(frame)[1] for frame in frames)
    joined = protocol.join_reply(slim, data)
    assert joined["names"] == fields["names"] and "names_chunked" not in joined
    assert not isinstance(protocol.validate_response(joined, "fs.names"), protocol.Refusal)


def test_names_skips_a_name_that_is_not_text(tmp_path):
    (tmp_path / "ok.txt").write_text("x")
    try:
        (tmp_path / os.fsdecode(b"bad-\xff.txt")).write_text("x")
    except OSError:
        pytest.skip("this filesystem refuses a non-UTF-8 name")
    assert files.names_reply(str(tmp_path)) == {"names": ["ok.txt"], "truncated": False}


def test_names_of_a_missing_root_is_gone(served):
    core, client, project, _events = served
    with pytest.raises(inproc.RequestRefused) as refused:
        client.request({"t": "fs.names", "root": str(project / "gone")})
    assert refused.value.error == protocol.ERROR_GONE


def test_names_is_confined_to_a_known_root_unless_local(served, tmp_path):
    core, client, project, _events = served
    other = tmp_path / "other"
    other.mkdir()
    (other / "secret.txt").write_text("s")
    # A local client may name any root...
    assert client.request({"t": "fs.names", "root": str(other)})["names"] == ["secret.txt"]
    # ...a client that is not local, only a root the service knows.
    client.local = False
    with pytest.raises(inproc.RequestRefused) as refused:
        client.request({"t": "fs.names", "root": str(other)})
    assert refused.value.error == protocol.ERROR_REFUSED
    core.store = type("Store", (), {"all_sessions": lambda self: [type("S", (), {"cwd": str(project)})()]})()
    assert client.request({"t": "fs.names", "root": str(project)})["names"] == ["a.txt"]
    with pytest.raises(inproc.RequestRefused):
        client.request({"t": "fs.names", "root": str(other)})
    # A link out of a known root is outside it.
    (project / "escape").symlink_to(other)
    with pytest.raises(inproc.RequestRefused):
        client.request({"t": "fs.names", "root": str(project / "escape")})


def test_stat_list_walk_and_names_are_deferreds_settled_later(served):
    core, client, project, _events = served
    landed: list = []
    core.files = files.Files(core, dispatch=landed.append)
    for frame in (
        {"t": "fs.stat", "id": 1, "path": str(project / "a.txt")},
        {"t": "fs.list", "id": 2, "path": str(project), "hidden": False, "root": str(project)},
        {"t": "fs.walk", "id": 3, "root": str(project), "hidden": False},
        {"t": "fs.names", "id": 4, "root": str(project)},
    ):
        raw = core.handle(protocol.validate(frame, protocol.CLIENT), client)
        assert isinstance(raw, protocol.Deferred) and not raw.settled


# -- the helpers -----------------------------------------------------------------------


def test_file_stat_of_a_directory_is_gone(tmp_path):
    assert files.file_stat(str(tmp_path)) == (None, None, True)
    assert files.file_stat(str(tmp_path / "nope")) == (None, None, True)
    (tmp_path / "f").write_text("x")
    mtime, size, gone = files.file_stat(str(tmp_path / "f"))
    assert (size, gone) == (1, False) and mtime == mtime_of(tmp_path / "f")


# -- the seeded watch and the saver's cases (the review of PR 609) -----------------------------


def test_a_watch_seeded_with_an_older_mtime_reports_the_change_at_once(served):
    """A change between the client's read and its watch (or while it was
    away): the first stat differs from the seed, so one `file-changed`
    arrives without any monitor event."""
    core, client, project, events = served
    path = project / "a.txt"
    was = mtime_of(path)
    path.write_text("changed before the watch\n")
    os.utime(path, ns=(0, (was + 5_000_000) * 1000))
    client.request({"t": "fs.watch", "path": str(path), "kind": "file", "handle": "w1", "mtime": was})
    assert pump(1.0, lambda: changes(events))
    event = changes(events)[0]
    assert event["mtime"] == mtime_of(path) and event["gone"] is False
    # A seed that matches the file is no event; a null seed never is.
    same = {"t": "fs.watch", "path": str(path), "kind": "file", "handle": "w2", "mtime": mtime_of(path)}
    client.request(same)
    client.request({"t": "fs.watch", "path": str(path), "kind": "file", "handle": "w3"})
    pump(0.3)
    assert len(changes(events)) == 1
    # A seed for a file that is gone is `gone` at once.
    path.unlink()
    client.request({"t": "fs.watch", "path": str(path), "kind": "file", "handle": "w4", "mtime": was})
    assert pump(1.0, lambda: any(e["gone"] for e in changes(events)))


def test_watches_per_client_are_bounded(served, monkeypatch):
    core, client, project, _events = served
    monkeypatch.setattr(files, "MAX_WATCHES_PER_CLIENT", 2)
    path = str(project / "a.txt")
    client.request({"t": "fs.watch", "path": path, "kind": "file", "handle": "w1"})
    client.request({"t": "fs.watch", "path": path, "kind": "file", "handle": "w2"})
    client.request({"t": "fs.watch", "path": path, "kind": "file", "handle": "w1"})  # a replace is fine
    with pytest.raises(inproc.RequestRefused) as refused:
        client.request({"t": "fs.watch", "path": path, "kind": "file", "handle": "w3"})
    assert refused.value.error == protocol.ERROR_REFUSED


def test_write_through_a_hard_link_updates_both_names(served):
    core, client, project, _events = served
    a, b = project / "a.txt", project / "b.txt"
    os.link(a, b)
    client.request({"t": "fs.write", "path": str(a), "text": "linked\n", "expect_mtime": None})
    assert a.read_text() == b.read_text() == "linked\n" and a.stat().st_nlink == 2


def test_write_into_a_read_only_directory_writes_the_writable_file_in_place(served):
    core, client, project, _events = served
    if os.geteuid() == 0:
        pytest.skip("root writes anywhere")
    sub = project / "ro"
    sub.mkdir()
    target = sub / "f.txt"
    target.write_text("old\n")
    sub.chmod(0o555)
    try:
        reply = client.request({"t": "fs.write", "path": str(target), "text": "new\n", "expect_mtime": None})
        assert target.read_text() == "new\n" and reply["size"] == 4
        # A stale compare still holds in place: nothing written.
        target.write_text("theirs\n")
        os.utime(target, ns=(0, (reply["mtime"] + 5_000_000) * 1000))
        with pytest.raises(inproc.RequestRefused) as refused:
            client.request(
                {"t": "fs.write", "path": str(target), "text": "mine\n", "expect_mtime": reply["mtime"]}
            )
        assert refused.value.error == protocol.ERROR_STALE and target.read_text() == "theirs\n"
    finally:
        sub.chmod(0o755)


def test_a_recreated_file_gets_the_umask_mode_not_the_temp_files(served):
    core, client, project, _events = served
    path = project / "fresh.txt"
    client.request({"t": "fs.write", "path": str(path), "text": "x\n", "expect_mtime": None})
    umask = os.umask(0)
    os.umask(umask)
    assert stat.S_IMODE(path.stat().st_mode) == (0o666 & ~umask)


def test_the_reply_mtime_is_the_written_files_own(served):
    """The mtime in the reply is read off the written descriptor, so it is
    exactly what the file carries after the replace and what the next
    read answers (a write right after by someone else is not mistaken for
    ours)."""
    core, client, project, _events = served
    path = project / "a.txt"
    reply = client.request({"t": "fs.write", "path": str(path), "text": "ours\n", "expect_mtime": None})
    assert reply["mtime"] == mtime_of(path) == client.request({"t": "fs.read", "path": str(path)})["mtime"]


def test_confinement_is_checked_on_the_worker_against_the_resolved_file(served, tmp_path):
    """The roots are computed on the main loop and the check runs on the
    worker right before the open, on the file the path resolves to."""
    core, client, project, _events = served
    client.local = False
    core.store = type("Store", (), {"all_sessions": lambda self: [type("S", (), {"cwd": str(project)})()]})()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    link = project / "link.txt"
    link.symlink_to(outside)
    for message in (
        {"t": "fs.read", "path": str(link)},
        {"t": "fs.write", "path": str(link), "text": "x", "expect_mtime": None},
    ):
        with pytest.raises(inproc.RequestRefused) as refused:
            client.request(message)
        assert refused.value.error == protocol.ERROR_REFUSED and refused.value.msgid == files.OUTSIDE_MSGID
    assert outside.read_text() == "secret"
    inside = project / "inside.txt"
    inside.write_text("fine")
    (project / "link2.txt").symlink_to(inside)
    assert client.request({"t": "fs.read", "path": str(project / "link2.txt")})["text"] == "fine"


def test_read_of_a_fifo_is_refused_not_waited_on(served):
    core, client, project, _events = served
    fifo = project / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(inproc.RequestRefused) as refused:
        client.request({"t": "fs.read", "path": str(fifo)})
    assert refused.value.error == protocol.ERROR_REFUSED and "not a file" in refused.value.msgid


def test_a_read_only_file_of_ones_own_is_refused_not_swapped_out(served):
    """0444 in a writable directory: the replace branch could swap the file
    out, but the saver this replaces refused with Permission denied, and
    so does the service — the content and the mode are untouched."""
    core, client, project, _events = served
    if os.geteuid() == 0:
        pytest.skip("root writes anywhere")
    path = project / "ro.txt"
    path.write_text("keep\n")
    path.chmod(0o444)
    try:
        with pytest.raises(inproc.RequestRefused) as refused:
            client.request({"t": "fs.write", "path": str(path), "text": "new\n", "expect_mtime": None})
        assert refused.value.error == protocol.ERROR_FAILED
        assert "Couldn't save" in refused.value.msgid and "denied" in refused.value.details["error"]
        assert path.read_text() == "keep\n" and stat.S_IMODE(path.stat().st_mode) == 0o444
        assert not [name for name in os.listdir(project) if name.startswith(".collins-")]
    finally:
        path.chmod(0o644)


# -- the Fable review of PR 611 ------------------------------------------------------------


def test_a_watch_unwatched_while_it_is_confined_installs_nothing(served):
    """The watch is confined on a thread and installed when that lands: an
    `fs.unwatch` of the handle first makes the landing install nothing."""
    core, client, project, _events = served
    landings: list = []
    core.files = files.Files(core, dispatch=landings.append, spawn=lambda fn, name: fn())
    frame = {"t": "fs.watch", "id": 3, "path": str(project), "kind": "dir", "handle": "d1"}
    raw = core.handle(protocol.validate(frame, protocol.CLIENT), client)
    assert isinstance(raw, protocol.Deferred) and not raw.settled and landings
    unwatch = protocol.validate({"t": "fs.unwatch", "id": 4, "handle": "d1"}, protocol.CLIENT)
    core.files.unwatch(unwatch, client)
    landings.pop()()
    assert raw.settled and raw.reply["ok"] and not core.files.watching(client, "d1")


def test_a_watch_gio_cannot_monitor_is_refused_failed(served, monkeypatch):
    core, client, project, _events = served
    monkeypatch.setattr(files._DirWatch, "start", lambda self: "No space left on device")
    with pytest.raises(inproc.RequestRefused) as refused:
        client.request({"t": "fs.watch", "path": str(project), "kind": "dir", "handle": "d1"})
    assert refused.value.error == protocol.ERROR_FAILED
    assert refused.value.details["error"] == "No space left on device"
    assert not core.files.watching(client, "d1")


def test_the_watch_bound_holds_a_tree_of_folders(served):
    assert files.MAX_WATCHES_PER_CLIENT == 4096


def test_list_and_walk_confine_on_the_worker(served, monkeypatch):
    """`allowed` (which resolves every root) is not called on the main
    loop for a listing or a walk: the roots are, and `_confine` runs on the
    worker."""
    core, client, project, _events = served
    client.local = False
    core.store = type("Store", (), {"all_sessions": lambda self: [type("S", (), {"cwd": str(project)})()]})()
    monkeypatch.setattr(files, "allowed", lambda *a: pytest.fail("allowed() on the main loop"))
    reply = client.request({"t": "fs.list", "path": str(project), "hidden": False, "root": str(project)})
    assert [e["name"] for e in reply["entries"]] == ["a.txt"]
    assert client.request({"t": "fs.walk", "root": str(project), "hidden": False})["paths"] == ["a.txt"]
    assert client.request({"t": "fs.names", "root": str(project)})["names"] == ["a.txt"]
    client.request({"t": "fs.watch", "path": str(project), "kind": "dir", "handle": "d1"})
    assert core.files.watching(client, "d1")


# -- fs.rename, fs.paste, fs.mkdir (PR-2.5) --------------------------------------------------


def _knows(core, *dirs) -> None:
    """The store knows a session in each of *dirs*: they are roots."""
    sessions = [type("S", (), {"cwd": str(d)})() for d in dirs]
    core.store = type("Store", (), {"all_sessions": lambda self: list(sessions)})()


def _rename(client, project, path, target, root=None) -> dict:
    return client.request(
        {"t": "fs.rename", "path": str(path), "target": str(target), "root": str(root or project)}
    )


def _paste(client, project, entries, target, cut=False, root=None) -> dict:
    return client.request(
        {
            "t": "fs.paste",
            "entries": [str(e) for e in entries],
            "target": str(target),
            "cut": cut,
            "root": str(root or project),
        }
    )


def _placed(reply: dict) -> list[str]:
    """The paths a paste landed at, in order: what the reply's `placed`
    said before D42 dropped it (derivable from `results`)."""
    return [r["target"] for r in reply["results"] if r["target"] is not None]


def test_rename_renames_in_place_and_answers_the_files_mtime(served):
    core, client, project, _events = served
    before = mtime_of(project / "a.txt")
    reply = _rename(client, project, project / "a.txt", project / "b.txt")
    assert not (project / "a.txt").exists() and (project / "b.txt").read_text() == "one\ntwo\n"
    assert reply["mtime"] == mtime_of(project / "b.txt") == before
    (project / "pkg").mkdir()
    reply = _rename(client, project, project / "pkg", project / "package")
    assert (project / "package").is_dir() and reply["mtime"] is None
    # The name unchanged: nothing to do, nothing to complain about.
    reply = _rename(client, project, project / "b.txt", project / "b.txt")
    assert reply["mtime"] is None and (project / "b.txt").exists()


def test_rename_never_lands_on_something_that_is_there(served):
    core, client, project, _events = served
    (project / "b.txt").write_text("mine")
    with pytest.raises(inproc.RequestRefused) as refused:
        _rename(client, project, project / "a.txt", project / "b.txt")
    assert refused.value.error == protocol.ERROR_REFUSED and refused.value.details["reason"] == "exists"
    assert (project / "b.txt").read_text() == "mine" and (project / "a.txt").exists()
    # A broken symlink is taken too.
    (project / "c.txt").symlink_to(project / "gone.txt")
    with pytest.raises(inproc.RequestRefused) as refused:
        _rename(client, project, project / "a.txt", project / "c.txt")
    assert refused.value.details["reason"] == "exists"


def test_rename_refuses_a_move_a_missing_entry_and_a_bad_name(served):
    core, client, project, _events = served
    (project / "sub").mkdir()
    # Another directory's: a rename keeps its folder (a paste of a cut moves).
    with pytest.raises(inproc.RequestRefused) as refused:
        _rename(client, project, project / "a.txt", project / "sub" / "a.txt")
    assert refused.value.details["reason"] == "not_a_name" and (project / "a.txt").exists()
    with pytest.raises(inproc.RequestRefused) as refused:
        _rename(client, project, project / "gone.txt", project / "b.txt")
    assert refused.value.error == protocol.ERROR_GONE and refused.value.details["reason"] == "missing"
    with pytest.raises(inproc.RequestRefused) as refused:
        _rename(client, project, project / "a.txt", project / "..")
    assert refused.value.details["reason"] == "not_a_name"


def test_rename_across_roots_is_refused(served, tmp_path):
    """The root the request names confines both paths: an entry of another
    root (or a target through a link out of this one) is `outside`, and a
    client that is not local may name only a root the service knows."""
    core, client, project, _events = served
    other = tmp_path / "other"
    other.mkdir()
    (other / "x.txt").write_text("x")
    with pytest.raises(inproc.RequestRefused) as refused:
        _rename(client, project, other / "x.txt", other / "y.txt")
    assert refused.value.error == protocol.ERROR_REFUSED and refused.value.details["reason"] == "outside"
    assert (other / "x.txt").exists()
    # A rename inside a directory that is itself a symlink out of the root
    # keeps the bare name and still lands outside it.
    (project / "link").symlink_to(other)
    with pytest.raises(inproc.RequestRefused) as refused:
        _rename(client, project, project / "link" / "x.txt", project / "link" / "y.txt")
    assert refused.value.details["reason"] == "outside" and (other / "x.txt").exists()
    # Not local: the root itself must be one the service knows.
    client.local = False
    with pytest.raises(inproc.RequestRefused) as refused:
        _rename(client, project, other / "x.txt", other / "y.txt", root=other)
    assert refused.value.error == protocol.ERROR_REFUSED and "reason" not in refused.value.details
    _knows(core, project)
    reply = _rename(client, project, project / "a.txt", project / "b.txt")
    assert reply["mtime"] == mtime_of(project / "b.txt")


def test_rename_reports_the_os_error(served):
    core, client, project, _events = served
    if os.geteuid() == 0:
        pytest.skip("root renames anywhere")
    locked = project / "locked"
    locked.mkdir()
    (locked / "a.txt").write_text("x")
    locked.chmod(0o500)
    try:
        with pytest.raises(inproc.RequestRefused) as refused:
            _rename(client, project, locked / "a.txt", locked / "b.txt")
    finally:
        locked.chmod(0o700)
    assert refused.value.error == protocol.ERROR_FAILED and "denied" in refused.value.details["error"]
    assert (locked / "a.txt").exists()


def test_paste_copies_and_never_overwrites(served):
    core, client, project, _events = served
    (project / "pkg").mkdir()
    (project / "pkg" / "a.txt").write_text("mine")
    reply = _paste(client, project, [project / "a.txt"], project / "pkg")
    copy = project / "pkg" / "a (copy).txt"
    assert _placed(reply) == [str(copy)]
    assert reply["results"] == [
        {
            "source": str(project / "a.txt"),
            "target": str(copy),
            "mtime": mtime_of(copy),
            "error": None,
            "message": "",
        }
    ]
    assert "placed" not in reply
    assert (project / "pkg" / "a.txt").read_text() == "mine"
    assert copy.read_text() == "one\ntwo\n"
    assert (project / "a.txt").exists()  # a copy leaves the original


def test_paste_of_a_cut_moves(served):
    core, client, project, _events = served
    (project / "pkg").mkdir()
    (project / "folder" / "sub").mkdir(parents=True)
    (project / "folder" / "sub" / "deep.txt").write_text("deep")
    reply = _paste(client, project, [project / "a.txt", project / "folder"], project / "pkg", cut=True)
    assert _placed(reply) == [str(project / "pkg" / "a.txt"), str(project / "pkg" / "folder")]
    assert not (project / "a.txt").exists() and not (project / "folder").exists()
    assert (project / "pkg" / "a.txt").read_text() == "one\ntwo\n"
    assert (project / "pkg" / "folder" / "sub" / "deep.txt").read_text() == "deep"
    # A cut pasted back where it came from: nothing to do, no error.
    reply = _paste(client, project, [project / "pkg" / "a.txt"], project / "pkg", cut=True)
    assert _placed(reply) == [] and reply["results"][0]["target"] is None
    assert reply["results"][0]["error"] is None


def test_paste_source_outside_every_root_is_refused_unless_local(served, tmp_path):
    """Per entry: the rest of the clipboard still lands."""
    core, client, project, _events = served
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (project / "pkg").mkdir()
    client.local = False
    _knows(core, project)
    reply = _paste(client, project, [outside, project / "a.txt"], project / "pkg")
    assert _placed(reply) == [str(project / "pkg" / "a.txt")]
    assert [r["error"] for r in reply["results"]] == ["source_outside", None]
    assert not (project / "pkg" / "outside.txt").exists()
    # A link inside to a file outside: the source is confined resolved.
    (project / "link.txt").symlink_to(outside)
    reply = _paste(client, project, [project / "link.txt"], project / "pkg")
    assert reply["results"][0]["error"] == "source_outside"
    # A local client (the same machine): a copy taken in a file manager is
    # exactly what paste is for, and the service does the copy.
    client.local = True
    reply = _paste(client, project, [outside], project / "pkg")
    assert _placed(reply) == [str(project / "pkg" / "outside.txt")]
    assert (project / "pkg" / "outside.txt").read_text() == "secret"


def test_paste_destination_is_confined_to_the_root(served, tmp_path):
    core, client, project, _events = served
    other = tmp_path / "other"
    other.mkdir()
    reply = _paste(client, project, [project / "a.txt"], other)
    assert _placed(reply) == [] and reply["results"][0]["error"] == "outside"
    assert not (other / "a.txt").exists()
    # Not local: the root must be one the service knows.
    client.local = False
    with pytest.raises(inproc.RequestRefused) as refused:
        _paste(client, project, [other / "x"], other, root=other)
    assert refused.value.error == protocol.ERROR_REFUSED


def test_paste_reports_each_entrys_rule(served):
    core, client, project, _events = served
    (project / "pkg").mkdir()
    sources = [project / "gone.txt", project / "pkg", project / "a.txt"]
    reply = _paste(client, project, sources, project / "pkg")
    assert [r["error"] for r in reply["results"]] == ["missing", "into_itself", None]
    assert _placed(reply) == [str(project / "pkg" / "a.txt")]
    reply = _paste(client, project, [project / "a.txt"], project / "nowhere")
    assert reply["results"][0]["error"] == "not_a_dir"
    if os.geteuid() != 0:
        locked = project / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        try:
            reply = _paste(client, project, [project / "a.txt"], locked)
        finally:
            locked.chmod(0o700)
        assert reply["results"][0]["error"] == "failed" and "denied" in reply["results"][0]["message"]


def test_mkdir_makes_one_folder_inside_the_root(served, tmp_path):
    core, client, project, _events = served

    def mkdir(path, root=project):
        return client.request({"t": "fs.mkdir", "path": str(path), "root": str(root)})

    assert mkdir(project / "new") == {}
    assert (project / "new").is_dir()
    with pytest.raises(inproc.RequestRefused) as refused:
        mkdir(project / "new")
    assert refused.value.details["reason"] == "exists"
    with pytest.raises(inproc.RequestRefused) as refused:
        mkdir(project / "a.txt")
    assert refused.value.details["reason"] == "exists"  # never over a file either
    with pytest.raises(inproc.RequestRefused) as refused:
        mkdir(project / "no" / "parent")
    assert refused.value.details["reason"] == "no_parent"
    with pytest.raises(inproc.RequestRefused) as refused:
        mkdir(tmp_path / "elsewhere")
    assert refused.value.details["reason"] == "outside" and not (tmp_path / "elsewhere").exists()
    other = tmp_path / "other"
    other.mkdir()
    (project / "link").symlink_to(other)
    with pytest.raises(inproc.RequestRefused) as refused:
        mkdir(project / "link" / "made")
    assert refused.value.details["reason"] == "outside" and not (other / "made").exists()
    client.local = False
    with pytest.raises(inproc.RequestRefused) as refused:
        mkdir(other / "made", root=other)
    assert "reason" not in refused.value.details


def test_rename_paste_and_mkdir_are_deferreds_settled_later(served):
    core, client, project, _events = served
    (project / "pkg").mkdir()
    root = str(project)
    for frame in (
        {
            "t": "fs.rename",
            "id": 11,
            "path": str(project / "a.txt"),
            "target": str(project / "b.txt"),
            "root": root,
        },
        {
            "t": "fs.paste",
            "id": 12,
            "entries": [str(project / "b.txt")],
            "target": str(project / "pkg"),
            "cut": False,
            "root": root,
        },
        {"t": "fs.mkdir", "id": 13, "path": str(project / "made"), "root": root},
    ):
        landed: list = []
        core.files = files.Files(core, dispatch=landed.append)
        raw = core.handle(protocol.validate(frame, protocol.CLIENT), client)
        assert isinstance(raw, protocol.Deferred) and not raw.settled
        deadline = time.monotonic() + 5
        while not landed and time.monotonic() < deadline:
            time.sleep(0.01)
        assert landed
        landed[0]()
        assert raw.settled and raw.reply["ok"], raw.reply
    assert (project / "pkg" / "b.txt").exists() and (project / "made").is_dir()


def test_a_paste_worker_that_raises_settles_failed(served, monkeypatch):
    core, client, project, _events = served

    def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(files, "paste_entries", boom)
    with pytest.raises(inproc.RequestRefused) as refused:
        _paste(client, project, [project / "a.txt"], project)
    assert refused.value.error == protocol.ERROR_FAILED and refused.value.details["error"] == "disk on fire"


def test_paste_results_carry_the_landed_files_mtime(served):
    """Each result's `mtime` is the landed file's (null for a folder, or
    nothing landed): what `_retarget_open` takes for a moved open file,
    so a `gone` that beat the reply is taken back (D41, S1)."""
    core, client, project, _events = served
    (project / "pkg").mkdir()
    (project / "folder").mkdir()
    before = mtime_of(project / "a.txt")
    entries = [project / "a.txt", project / "folder", project / "gone"]
    reply = _paste(client, project, entries, project / "pkg", cut=True)
    assert [r["mtime"] for r in reply["results"]] == [mtime_of(project / "pkg" / "a.txt"), None, None]
    assert reply["results"][0]["mtime"] == before  # a move keeps the inode's


def test_a_cut_from_another_known_root_lands_and_one_from_outside_every_root_is_refused(served, tmp_path):
    """D43: the roots are what a client may reach, not walls between
    projects: the one clipboard moves a cut from project B's tree into
    project A's, for a client that is not local too; a source inside no
    root the service knows is `source_outside`."""
    core, client, project, _events = served
    other = tmp_path / "other"
    other.mkdir()
    (other / "b.txt").write_text("from b")
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    (nowhere / "n.txt").write_text("n")
    client.local = False
    _knows(core, project, other)
    reply = _paste(client, project, [other / "b.txt", nowhere / "n.txt"], project, cut=True)
    assert [r["error"] for r in reply["results"]] == [None, "source_outside"]
    assert (project / "b.txt").read_text() == "from b" and not (other / "b.txt").exists()
    assert (nowhere / "n.txt").exists() and not (project / "n.txt").exists()


def test_the_root_is_confined_on_the_worker_not_the_handler(served, monkeypatch):
    """S3 of PR 613's review: `allowed` resolved every root on the main
    loop for the three operations; now the handler computes the roots
    and `_confine` runs on the thread, as `fs.list`'s does."""
    core, client, project, _events = served
    client.local = False
    _knows(core, project)
    resolved: list[str] = []
    real_is_inside = files.is_inside

    def spy(root, path):
        resolved.append(threading.current_thread().name)
        return real_is_inside(root, path)

    monkeypatch.setattr(files, "is_inside", spy)
    (project / "pkg").mkdir()
    root = str(project)
    frames = (
        {
            "t": "fs.rename",
            "id": 21,
            "path": str(project / "a.txt"),
            "target": str(project / "b.txt"),
            "root": root,
        },
        {
            "t": "fs.paste",
            "id": 22,
            "entries": [str(project / "b.txt")],
            "target": str(project / "pkg"),
            "cut": False,
            "root": root,
        },
        {"t": "fs.mkdir", "id": 23, "path": str(project / "made"), "root": root},
    )
    inline = core.files
    for frame in frames:
        work: list = []
        resolved.clear()
        collect = lambda fn, name, work=work: work.append(fn)  # noqa: E731
        core.files = files.Files(core, dispatch=lambda fn: fn(), spawn=collect)
        raw = core.handle(protocol.validate(frame, protocol.CLIENT), client)
        assert isinstance(raw, protocol.Deferred) and not raw.settled
        assert resolved == [], f"{frame['t']} resolved a path on the handler"
        work[0]()
        assert raw.settled and raw.reply["ok"], raw.reply
        assert resolved and all(name == threading.current_thread().name for name in resolved)
    assert (project / "pkg" / "b.txt").exists() and (project / "made").is_dir()
    # And a root the client may not name is refused by that worker check.
    core.files = inline
    with pytest.raises(inproc.RequestRefused) as refused:
        client.request({"t": "fs.mkdir", "path": str(project.parent / "x"), "root": str(project.parent)})
    assert refused.value.error == protocol.ERROR_REFUSED and not (project.parent / "x").exists()


def test_source_confinement_never_resolves_the_held_path_again(tmp_path, monkeypatch):
    """The third verification's probe (H1, D48). A paste source's parent is
    swapped for a link to a directory outside every root after the first
    question; the paste holds that outside directory; then the outside
    path the kernel printed for it is replaced by a link into the root
    before the second question. Resolved again (`is_inside`), that path
    reads as inside and the outside file lands in the root; compared as
    it stands, it is `source_outside` and nothing lands."""
    import shutil

    from collins import projectfiles

    root = tmp_path / "project"
    (root / "d").mkdir(parents=True)
    (root / "p").mkdir()
    (root / "p" / "secret").write_text("inside, harmless")
    outside = tmp_path / "outside"
    (outside / "dir").mkdir(parents=True)
    (outside / "dir" / "secret").write_text("OUTSIDE SECRET")
    real_target, real_held = projectfiles.paste_target, projectfiles._held_path

    def check_then_swap_the_parent(*args, **kwargs):
        answer = real_target(*args, **kwargs)
        if not (root / "p").is_symlink():
            shutil.rmtree(root / "p")
            os.symlink(outside / "dir", root / "p")
        return answer

    def read_then_swap_the_outside_path(fd):
        held = real_held(fd)
        if held == str(outside.resolve() / "dir") and not (outside / "dir").is_symlink():
            os.rename(outside / "dir", outside / "dir-moved")
            os.symlink(root / "d", outside / "dir")  # the kernel's path now resolves inside the root
        return held

    monkeypatch.setattr(projectfiles, "paste_target", check_then_swap_the_parent)
    monkeypatch.setattr(projectfiles, "_held_path", read_then_swap_the_outside_path)
    asked: list[str] = []
    confine = files.source_confinement([str(root)])

    def source_allowed(path: str) -> bool:
        asked.append(path)
        return confine(path)

    sources = [str(root / "p" / "secret")]
    (outcome,) = projectfiles.paste_entries(root, root / "d", sources, False, source_allowed)
    assert asked == [str(root.resolve() / "p" / "secret"), str(outside.resolve() / "dir")]
    assert files.is_inside(root, asked[1])  # the premise: resolving it again says inside
    assert outcome.error is projectfiles.PasteError.SOURCE_OUTSIDE
    assert os.listdir(root / "d") == []
    assert (outside / "dir-moved" / "secret").read_text() == "OUTSIDE SECRET"


def test_source_confinement_answers_the_roots_and_what_is_under_them(tmp_path):
    root, other = tmp_path / "alpha", tmp_path / "beta"
    (root / "sub").mkdir(parents=True)
    other.mkdir()
    (tmp_path / "alpha-evil").mkdir()
    (tmp_path / "way-in").symlink_to(root)
    confine = files.source_confinement([str(tmp_path / "way-in"), str(other)])  # a root named by a link
    real = str(root.resolve())
    assert confine(real) and confine(real + "/sub") and confine(str(other.resolve()) + "/x")
    assert not confine(str(tmp_path.resolve())) and not confine(real + "-evil") and not confine("/")
    assert files.source_confinement(None)("/anything/at/all")  # a `local` client
    assert not files.source_confinement([])("/anything/at/all")
