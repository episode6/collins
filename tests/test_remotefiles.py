# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The client's end of files over the API (collins.remotefiles, split-service
spec §3.23, PR-2.3), through a fake link: `read` and `write` are `fs.read`
/ `fs.write` calls and build their records, a `stale` refusal is told
apart, the watcher sends `fs.watch` under a handle of its own and hands
each `file-changed` for that handle to its listener, `unwatch` sends
`fs.unwatch` once, a reconnect's `reset` sends every live watch again, and
a refusal's words are translated and filled. PR-2.5: `rename_path`,
`paste_files` and `make_dir` are `fs.rename` / `fs.paste` / `fs.mkdir`
with their outcomes, and `clipboard_scope` reads the link's service id
and local proof (D35)."""

from __future__ import annotations

import pytest

from collins import apilink, remotefiles
from collins.api import protocol
from collins.api.protocol import RequestRefused
from collins.projectfiles import PasteError, RenameError


class FakeLink(apilink.Link):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[dict, float | None]] = []
        self.sent: list[dict] = []
        self.answers: dict[str, object] = {}

    def _request(self, message: dict, timeout: float | None = None) -> dict:
        self.calls.append((dict(message), timeout))
        answer = self.answers.get(message["t"])
        if isinstance(answer, Exception):
            raise answer
        return dict(answer(message) if callable(answer) else (answer or {}))

    def send(self, message: dict, on_reply=None, on_refused=None) -> None:
        self.sent.append(dict(message))
        answer = self.answers.get(message["t"])
        if isinstance(answer, Exception):
            if on_refused is not None:
                on_refused(answer)
            return
        if on_reply is not None:
            on_reply({})


@pytest.fixture
def link(monkeypatch):
    fake = FakeLink()
    monkeypatch.setattr(apilink, "_current", fake)
    watcher = remotefiles.Watcher()
    monkeypatch.setattr(remotefiles, "_WATCHER", watcher)
    remotefiles.install(fake)
    yield fake
    remotefiles.uninstall()


def test_read_is_an_fs_read_call(link):
    link.answers["fs.read"] = {"text": "hi\n", "encoding": "latin-1", "mtime": 5, "size": 3, "binary": False}
    read = remotefiles.read("/srv/p/a.txt")
    assert read == remotefiles.FileText("hi\n", "latin-1", 5, 3, False)
    assert link.calls[-1][0] == {"t": "fs.read", "path": "/srv/p/a.txt"}
    remotefiles.read("/srv/p/a.txt", max_bytes=10)
    assert link.calls[-1][0]["max"] == 10


def test_write_is_an_fs_write_call_and_stale_is_told_apart(link):
    link.answers["fs.write"] = {"mtime": 9, "size": 2, "encoding": "utf-8"}
    written = remotefiles.write("/srv/p/a.txt", "x\n", 5, "utf-8")
    assert written == remotefiles.Written(9, 2, "utf-8")
    assert link.calls[-1][0] == {
        "t": "fs.write", "path": "/srv/p/a.txt", "text": "x\n", "expect_mtime": 5, "encoding": "utf-8"
    }
    remotefiles.write("/srv/p/a.txt", "x\n", None, "weird")
    assert link.calls[-1][0]["expect_mtime"] is None and link.calls[-1][0]["encoding"] == "utf-8"
    stale = RequestRefused(protocol.ERROR_STALE, "{name} changed on disk", {"name": "a.txt"})
    link.answers["fs.write"] = stale
    with pytest.raises(RequestRefused) as refused:
        remotefiles.write("/srv/p/a.txt", "x\n", 5)
    assert remotefiles.is_stale(refused.value)
    assert remotefiles.refusal_words(refused.value) == "a.txt changed on disk"
    assert not remotefiles.is_stale(RequestRefused(protocol.ERROR_FAILED, "", {}))
    assert remotefiles.refusal_words(RequestRefused(protocol.ERROR_FAILED, "", {})) == "failed"


def test_the_watcher_sends_watches_under_handles_and_routes_events(link):
    heard: list[dict] = []
    other: list[dict] = []
    watcher = remotefiles.watcher()
    first = watcher.watch("/srv/p/a.txt", heard.append, mtime=7)
    second = watcher.watch("/srv/p/a.txt", other.append)
    assert first != second and watcher.watching(first) and watcher.watching(second)
    assert link.sent[-2] == {
        "t": "fs.watch", "path": "/srv/p/a.txt", "kind": "file", "handle": first, "mtime": 7,
    }
    assert link.sent[-1] == {"t": "fs.watch", "path": "/srv/p/a.txt", "kind": "file", "handle": second}
    event = {"t": "file-changed", "handle": first, "path": "/srv/p/a.txt", "mtime": 1, "size": 2}
    event["gone"] = False
    link.dispatch(event)
    assert heard == [event] and other == []
    link.dispatch({**event, "handle": "nobody"})
    assert len(heard) == 1
    watcher.unwatch(first)
    assert link.sent[-1] == {"t": "fs.unwatch", "handle": first}
    watcher.unwatch(first)
    watcher.unwatch(None)
    assert link.sent[-1] == {"t": "fs.unwatch", "handle": first} and len(link.sent) == 3
    link.dispatch(event)
    assert len(heard) == 1  # unwatched: nothing more lands


def test_reset_sends_every_live_watch_again(link):
    watcher = remotefiles.watcher()
    a = watcher.watch("/srv/p/a.txt", lambda e: None)
    b = watcher.watch("/srv/p/b.txt", lambda e: None, mtime=1)
    watcher.unwatch(a)
    watcher.update(b, 9)  # a later read or write: the seed a reconnect sends
    watcher.update(None, 3)
    del link.sent[:]
    remotefiles.reset()
    assert link.sent == [{"t": "fs.watch", "path": "/srv/p/b.txt", "kind": "file", "handle": b, "mtime": 9}]


def test_a_refused_watch_is_logged_and_kept_for_the_next_reset(link, caplog):
    link.answers["fs.watch"] = RequestRefused(protocol.ERROR_REFUSED, "{path} is outside", {"path": "/x"})
    watcher = remotefiles.watcher()
    handle = watcher.watch("/x", lambda e: None)
    assert watcher.watching(handle)
    assert any("refused" in record.getMessage() for record in caplog.records)


def test_a_listener_that_raises_does_not_break_the_link(link, caplog):
    watcher = remotefiles.watcher()

    def boom(event):
        raise RuntimeError("listener")

    handle = watcher.watch("/srv/p/a.txt", boom)
    link.dispatch(
        {"t": "file-changed", "handle": handle, "path": "/srv/p/a.txt", "mtime": 1, "size": 2, "gone": False}
    )
    assert any("listener failed" in record.getMessage() for record in caplog.records)


def test_no_installed_link_means_no_watch_is_sent(link):
    """A service with no `files` capability (the app never installed the
    link): the watch is kept for a later install's reset, nothing sent."""
    remotefiles.uninstall()
    watcher = remotefiles.watcher()
    handle = watcher.watch("/srv/p/a.txt", lambda e: None)
    watcher.unwatch(handle)
    assert link.sent == [] and not watcher.installed
    handle = watcher.watch("/srv/p/b.txt", lambda e: None)
    remotefiles.install(link)
    remotefiles.reset()
    assert link.sent == [{"t": "fs.watch", "path": "/srv/p/b.txt", "kind": "file", "handle": handle}]


def test_a_bound_method_listener_is_held_weakly_and_its_watch_dropped_when_it_dies(link):
    """A pane dropped without `shutdown` must not live on through its
    watches: the listener is a weak method, and the next event for a dead
    one drops the watch (and sends the unwatch)."""
    import gc

    class Pane:
        def __init__(self) -> None:
            self.heard: list[dict] = []

        def on_event(self, event: dict) -> None:
            self.heard.append(event)

    pane = Pane()
    watcher = remotefiles.watcher()
    handle = watcher.watch("/srv/p/a.txt", pane.on_event, mtime=1)
    event = {"t": "file-changed", "handle": handle, "path": "/srv/p/a.txt", "mtime": 2, "size": 1}
    event["gone"] = False
    link.dispatch(event)
    assert pane.heard == [event]
    del pane
    gc.collect()
    link.dispatch(event)
    assert not watcher.watching(handle)
    assert link.sent[-1] == {"t": "fs.unwatch", "handle": handle}


# -- the tree, quick open and roots (PR-2.4) ------------------------------------------


def test_stat_list_and_walk_are_their_requests(link):
    link.answers["fs.stat"] = {"kind": "file", "size": 3, "mtime": 7, "inside": True}
    found = remotefiles.stat_path("/srv/p/a.txt", "/srv/p")
    assert found == remotefiles.Stat("file", 3, 7, True) and found.is_file and not found.is_dir
    assert link.calls[-1][0] == {"t": "fs.stat", "path": "/srv/p/a.txt", "root": "/srv/p"}
    link.answers["fs.stat"] = {"kind": "missing", "size": None, "mtime": None, "inside": False}
    assert remotefiles.stat_path("/srv/p/nope") == remotefiles.Stat("missing", None, None, False)
    assert "root" not in link.calls[-1][0]
    link.answers["fs.list"] = {
        "entries": [
            {"name": "alias", "kind": "symlink", "ignored": False},
            {"name": "src", "kind": "dir", "ignored": False},
            {"name": "a.log", "kind": "file", "ignored": True},
        ],
        "truncated": True,
    }
    entries, truncated = remotefiles.list_dir("/srv/p", "/srv/p", hidden=True)
    assert truncated is True
    assert [(e.name, e.is_dir, e.expandable, e.ignored) for e in entries] == [
        ("alias", True, False, False), ("src", True, True, False), ("a.log", False, False, True),
    ]
    assert link.calls[-1][0] == {"t": "fs.list", "path": "/srv/p", "hidden": True, "root": "/srv/p"}
    link.answers["fs.walk"] = {"paths": ["a.txt", "src/b.py"], "truncated": False}
    assert remotefiles.walk_root("/srv/p") == (["a.txt", "src/b.py"], False)
    assert link.calls[-1][0] == {"t": "fs.walk", "root": "/srv/p", "hidden": False}


def test_a_dir_watch_sends_its_kind_and_hears_dir_changed(link):
    heard: list[dict] = []
    handle = remotefiles.watcher().watch("/srv/p/src", heard.append, kind=protocol.WATCH_DIR)
    assert link.sent[-1] == {"t": "fs.watch", "path": "/srv/p/src", "kind": "dir", "handle": handle}
    link.dispatch({"t": "dir-changed", "handle": handle, "path": "/srv/p/src"})
    link.dispatch({"t": "dir-changed", "handle": "other", "path": "/srv/p/src"})
    assert heard == [{"t": "dir-changed", "handle": handle, "path": "/srv/p/src"}]
    remotefiles.reset()
    assert link.sent[-1] == {"t": "fs.watch", "path": "/srv/p/src", "kind": "dir", "handle": handle}
    remotefiles.watcher().unwatch(handle)
    assert link.sent[-1] == {"t": "fs.unwatch", "handle": handle}


def test_reset_tells_its_listeners_after_resending(link):
    heard: list[str] = []

    class Tree:
        def on_reconnect(self) -> None:
            heard.append("tree")

    tree = Tree()
    watcher = remotefiles.watcher()
    watcher.on_reset(tree.on_reconnect)
    handle = watcher.watch("/srv/p", lambda _e: None, kind=protocol.WATCH_DIR)
    link.sent.clear()
    remotefiles.reset()
    assert link.sent[-1]["handle"] == handle and heard == ["tree"]
    watcher.off_reset(tree.on_reconnect)
    remotefiles.reset()
    assert heard == ["tree"]
    watcher.on_reset(tree.on_reconnect)
    del tree  # held weakly: a tree that is gone is not called
    remotefiles.reset()
    assert heard == ["tree"]


# -- file operations and the clipboard (PR-2.5) -----------------------------------------------


def test_rename_path_is_an_fs_rename_call_and_its_reason_is_told(link):
    link.answers["fs.rename"] = {"mtime": 42}
    assert remotefiles.rename_path("/srv/p/a.txt", "/srv/p/b.txt", "/srv/p") == 42
    assert link.calls[-1][0] == {
        "t": "fs.rename", "path": "/srv/p/a.txt", "target": "/srv/p/b.txt", "root": "/srv/p"
    }
    link.answers["fs.rename"] = {"mtime": None}
    assert remotefiles.rename_path("/srv/p/pkg", "/srv/p/package", "/srv/p") is None
    taken = RequestRefused(protocol.ERROR_REFUSED, "The name is taken", {"reason": "exists"})
    link.answers["fs.rename"] = taken
    with pytest.raises(RequestRefused) as refused:
        remotefiles.rename_path("/srv/p/a.txt", "/srv/p/b.txt", "/srv/p")
    assert remotefiles.rename_reason(refused.value) is RenameError.EXISTS
    assert remotefiles.rename_reason(RequestRefused(protocol.ERROR_FAILED, "boom", {})) is None
    assert remotefiles.rename_reason(RequestRefused(protocol.ERROR_REFUSED, "", {"reason": "odd"})) is None


def test_paste_files_is_an_fs_paste_call_with_an_outcome_per_entry(link):
    def result(source, target=None, mtime=None, error=None, message=""):
        return {"source": source, "target": target, "mtime": mtime, "error": error, "message": message}

    link.answers["fs.paste"] = {
        "results": [
            result("/srv/p/a.txt", "/srv/p/pkg/a (copy).txt", 42),
            result("/tmp/x.txt", error="source_outside"),
            result("/srv/p/b.txt", error="failed", message="Permission denied"),
            result("/srv/p/c.txt", error="newer_rule"),
            result("/srv/p/d", "/srv/p/pkg/d"),
        ],
    }
    entries = ["/srv/p/a.txt", "/tmp/x.txt", "/srv/p/b.txt", "/srv/p/c.txt", "/srv/p/d"]
    outcomes = remotefiles.paste_files(entries, "/srv/p/pkg", True, "/srv/p")
    assert link.calls[-1][0] == {
        "t": "fs.paste",
        "entries": ["/srv/p/a.txt", "/tmp/x.txt", "/srv/p/b.txt", "/srv/p/c.txt", "/srv/p/d"],
        "target": "/srv/p/pkg",
        "cut": True,
        "root": "/srv/p",
    }
    # The wait is as long as the move takes (D41), never CALL_TIMEOUT_S.
    assert link.calls[-1][1] == remotefiles.PASTE_TIMEOUT_S > remotefiles.CALL_TIMEOUT_S
    assert outcomes == [
        remotefiles.PasteOutcome("/srv/p/a.txt", "/srv/p/pkg/a (copy).txt", None, "", 42),
        remotefiles.PasteOutcome("/tmp/x.txt", None, PasteError.SOURCE_OUTSIDE, ""),
        remotefiles.PasteOutcome("/srv/p/b.txt", None, PasteError.FAILED, "Permission denied"),
        # A rule this client does not know reads as a failure, never as a landing.
        remotefiles.PasteOutcome("/srv/p/c.txt", None, PasteError.FAILED, ""),
        remotefiles.PasteOutcome("/srv/p/d", "/srv/p/pkg/d", None, "", None),  # a folder: no mtime
    ]


def _result(entry: str, landed: bool = True) -> dict:
    return {
        "source": entry,
        "target": entry.replace("/src/", "/dst/") if landed else None,
        "mtime": 7 if landed else None,
        "error": None if landed else "missing",
        "message": "",
    }


def test_paste_files_sends_a_long_clipboard_in_slices_and_joins_the_outcomes(link):
    """D42: at most FS_PASTE_MAX entries per request, one request per
    slice, the outcomes one per entry in order."""
    entries = [f"/srv/p/src/f{index:04d}" for index in range(2 * protocol.FS_PASTE_MAX + 5)]
    link.answers["fs.paste"] = lambda message: {"results": [_result(e) for e in message["entries"]]}
    outcomes = remotefiles.paste_files(entries, "/srv/p/dst", False, "/srv/p")
    sent = [call[0] for call in link.calls if call[0]["t"] == "fs.paste"]
    assert [len(call["entries"]) for call in sent] == [protocol.FS_PASTE_MAX, protocol.FS_PASTE_MAX, 5]
    assert [e for call in sent for e in call["entries"]] == entries
    assert all(call["target"] == "/srv/p/dst" and call["root"] == "/srv/p" for call in sent)
    assert all(call["cut"] is False for call in sent)
    assert [o.source for o in outcomes] == entries
    assert [o.target for o in outcomes] == [e.replace("/src/", "/dst/") for e in entries]
    assert all(o.mtime == 7 for o in outcomes)


def test_a_slice_refused_before_anything_landed_raises_and_after_ends_the_batching_as_failures(link):
    entries = [f"/srv/p/src/f{index:04d}" for index in range(3 * protocol.FS_PASTE_MAX)]
    refusal = RequestRefused(protocol.ERROR_REFUSED, "The path is outside every root the service knows", {})
    # The first slice refused: the whole paste raises, as one request's would.
    link.answers["fs.paste"] = refusal
    with pytest.raises(RequestRefused):
        remotefiles.paste_files(entries, "/srv/p/dst", True, "/srv/p")
    # A first slice that landed nothing (every entry missing), then a
    # refusal: still nothing landed, it raises.
    calls = [0]

    def missing_then_refused(message):
        calls[0] += 1
        if calls[0] == 1:
            return {"results": [_result(e, landed=False) for e in message["entries"]]}
        raise refusal

    link.answers["fs.paste"] = missing_then_refused
    with pytest.raises(RequestRefused):
        remotefiles.paste_files(entries, "/srv/p/dst", True, "/srv/p")
    # A first slice that landed, then a refusal: the batching ends, its
    # entries and the unsent ones are failures carrying the refusal's
    # words, so the caller sees one outcome per entry and spends the cut
    # for what landed.
    calls[0] = 0

    def landed_then_refused(message):
        calls[0] += 1
        if calls[0] == 1:
            return {"results": [_result(e) for e in message["entries"]]}
        raise refusal

    link.answers["fs.paste"] = landed_then_refused
    outcomes = remotefiles.paste_files(entries, "/srv/p/dst", True, "/srv/p")
    assert calls[0] == 2  # the third slice was never sent
    assert [o.source for o in outcomes] == entries
    landed = outcomes[: protocol.FS_PASTE_MAX]
    assert all(o.target is not None and o.error is None for o in landed)
    rest = outcomes[protocol.FS_PASTE_MAX :]
    assert all(o.target is None and o.error is PasteError.FAILED for o in rest)
    assert all(o.message == "The path is outside every root the service knows" for o in rest)


def test_make_dir_is_an_fs_mkdir_call(link):
    link.answers["fs.mkdir"] = {}
    assert remotefiles.make_dir("/srv/p/new", "/srv/p") is None
    assert link.calls[-1][0] == {"t": "fs.mkdir", "path": "/srv/p/new", "root": "/srv/p"}
    taken = RequestRefused(protocol.ERROR_REFUSED, "The name is taken", {"reason": "exists"})
    link.answers["fs.mkdir"] = taken
    with pytest.raises(RequestRefused) as refused:
        remotefiles.make_dir("/srv/p/new", "/srv/p")
    assert refused.value.details["reason"] == "exists"


def test_clipboard_scope_is_the_links_service_and_local_proof(link, monkeypatch):
    """D35: `collins://<service id>/<path>` URIs for the link's service,
    `file:` URIs only when the link proved `local`."""
    assert remotefiles.clipboard_scope() == remotefiles.ClipboardScope(None, False)
    link.hello = {"service_id": "svc1", "caps": ["files"]}
    link.local = True
    assert remotefiles.clipboard_scope() == remotefiles.ClipboardScope("svc1", True)
    link.local = False
    assert remotefiles.clipboard_scope() == remotefiles.ClipboardScope("svc1", False)
    link.hello = {"service_id": ""}
    assert remotefiles.clipboard_scope().service_id is None
    monkeypatch.setattr(apilink, "_current", None)
    assert remotefiles.clipboard_scope() == remotefiles.ClipboardScope(None, False)


def test_present_is_what_the_click_gate_asks_and_fails_soft(link):
    for kind, expected in (
        ("file", True), ("dir", True), ("other", True), ("missing", False), ("symlink", False),
    ):
        link.answers["fs.stat"] = {"kind": kind, "size": None, "mtime": None, "inside": False}
        assert remotefiles.present("/srv/p/x") is expected
    assert link.calls[-1][0] == {"t": "fs.stat", "path": "/srv/p/x"}  # no root: any path
    link.answers["fs.stat"] = RequestRefused(protocol.ERROR_FAILED, "boom", {})
    assert remotefiles.present("/srv/p/x") is False  # a service that cannot say: nothing opens


def test_root_names_is_fs_names(link):
    link.answers["fs.names"] = {"names": ["README.md", "notes.txt"], "truncated": True}
    assert remotefiles.root_names("/srv/p") == (["README.md", "notes.txt"], True)
    assert link.calls[-1][0] == {"t": "fs.names", "root": "/srv/p"}
    link.answers["fs.names"] = RequestRefused(protocol.ERROR_GONE, "The folder is not there", {})
    with pytest.raises(RequestRefused) as refused:
        remotefiles.root_names("/srv/p")
    assert refused.value.error == protocol.ERROR_GONE
