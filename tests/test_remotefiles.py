# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The client's end of files over the API (collins.remotefiles, split-service
spec §3.23, PR-2.3), through a fake link: `read` and `write` are `fs.read`
/ `fs.write` calls and build their records, a `stale` refusal is told
apart, the watcher sends `fs.watch` under a handle of its own and hands
each `file-changed` for that handle to its listener, `unwatch` sends
`fs.unwatch` once, a reconnect's `reset` sends every live watch again, and
a refusal's words are translated and filled."""

from __future__ import annotations

import pytest

from collins import apilink, remotefiles
from collins.api import protocol
from collins.api.protocol import RequestRefused


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
    first = watcher.watch("/srv/p/a.txt", heard.append)
    second = watcher.watch("/srv/p/a.txt", other.append)
    assert first != second and watcher.watching(first) and watcher.watching(second)
    assert link.sent[-2] == {"t": "fs.watch", "path": "/srv/p/a.txt", "kind": "file", "handle": first}
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
    b = watcher.watch("/srv/p/b.txt", lambda e: None)
    watcher.unwatch(a)
    del link.sent[:]
    remotefiles.reset()
    assert link.sent == [{"t": "fs.watch", "path": "/srv/p/b.txt", "kind": "file", "handle": b}]


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
