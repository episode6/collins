# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Project icons over the API (collins.remoteicons, PR-2.7; should-fix 2
of the PR 612 review): `icon_bytes`' answer-now-fetch-later shape, the
`REFRESH_S` window, the in-flight coalescing, listeners called only on a
changed answer, the gate applied to what the service sends, `forget`.
GLib only: the fetch thread and the idle landing are run inline here."""

import types

import pytest

from collins import blobcache, remoteicons

ROOT = "/srv/projects/alpha"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128"><rect/></svg>'
SVG2 = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle/></svg>'


class Service:
    """What the service answers for an icon fetch: bytes, a ValueError
    (no icon, a refused root) or any other exception; `fetch` records the
    URLs asked and `read` hands the bytes back."""

    def __init__(self, answer=SVG):
        self.answer = answer
        self.fetches = []

    def fetch(self, url, suffix, link=None):
        self.fetches.append((url, suffix))
        if isinstance(self.answer, Exception):
            raise self.answer
        return f"/cache/{len(self.fetches)}.svg"

    def read(self, path):
        return self.answer


@pytest.fixture
def inline(monkeypatch):
    """`icon_bytes` with its thread and its idle landing run inline, or
    held in *pending* while a test wants the fetch in flight."""
    service = Service()
    pending = []
    clock = [1000.0]

    class Thread:
        def __init__(self, target, args=(), name="", daemon=True):
            self.target, self.args = target, args

        def start(self):
            pending.append(self)

    def idle_add(fn, *args, priority=None):
        assert priority == remoteicons.GLib.PRIORITY_DEFAULT  # never idle: CI's Xvfb starves it
        fn(*args)

    monkeypatch.setattr(remoteicons, "threading", types.SimpleNamespace(Thread=Thread))
    monkeypatch.setattr(remoteicons, "GLib", types.SimpleNamespace(
        idle_add=idle_add, PRIORITY_DEFAULT=remoteicons.GLib.PRIORITY_DEFAULT, SOURCE_REMOVE=False))
    monkeypatch.setattr(remoteicons.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(blobcache, "fetch", service.fetch)
    monkeypatch.setattr(blobcache, "read", service.read)
    monkeypatch.setattr(remoteicons, "_held", {})
    monkeypatch.setattr(remoteicons, "_waiting", {})

    def land():
        while pending:
            thread = pending.pop(0)
            thread.target(*thread.args)

    return types.SimpleNamespace(service=service, land=land, clock=clock, pending=pending)


def test_the_first_ask_answers_none_and_fetches_then_the_listener_hears_the_icon(inline):
    heard = []
    assert remoteicons.icon_bytes(ROOT, heard.append) is None
    assert len(inline.pending) == 1 and heard == []
    inline.land()
    assert heard == [SVG]
    assert inline.service.fetches == [(blobcache.icon_url(ROOT), ".svg")]
    assert remoteicons.icon_bytes(ROOT) == SVG  # held now, answered at once
    assert inline.pending == []  # inside the window: no second fetch


def test_an_ask_past_the_window_refetches_and_tells_only_of_a_change(inline):
    remoteicons.icon_bytes(ROOT)
    inline.land()
    heard = []
    inline.clock[0] += remoteicons.REFRESH_S - 0.5
    assert remoteicons.icon_bytes(ROOT, heard.append) == SVG and inline.pending == []
    inline.clock[0] += 1.0
    assert remoteicons.icon_bytes(ROOT, heard.append) == SVG  # the held one, while the re-check runs
    assert len(inline.pending) == 1
    inline.land()
    assert heard == []  # a 304's worth of nothing: the same bytes are no news
    inline.clock[0] += remoteicons.REFRESH_S + 1
    inline.service.answer = SVG2
    assert remoteicons.icon_bytes(ROOT, heard.append) == SVG
    inline.land()
    assert heard == [SVG2] and remoteicons.icon_bytes(ROOT) == SVG2


def test_asks_while_a_fetch_is_in_flight_join_it(inline):
    heard = []
    remoteicons.icon_bytes(ROOT, lambda data: heard.append(("a", data)))
    remoteicons.icon_bytes(ROOT, lambda data: heard.append(("b", data)))
    remoteicons.icon_bytes(ROOT)  # no listener: still no second fetch
    assert len(inline.pending) == 1
    inline.land()
    assert heard == [("a", SVG), ("b", SVG)] and len(inline.service.fetches) == 1


def test_no_icon_and_a_failed_fetch_are_none_and_a_change_to_none_is_told(inline):
    remoteicons.icon_bytes(ROOT)
    inline.land()
    heard = []
    inline.clock[0] += remoteicons.REFRESH_S + 1
    inline.service.answer = ValueError("404")  # no icon, a refused root, no service
    remoteicons.icon_bytes(ROOT, heard.append)
    inline.land()
    assert heard == [None] and remoteicons.icon_bytes(ROOT) is None
    inline.clock[0] += remoteicons.REFRESH_S + 1
    inline.service.answer = OSError("boom")
    remoteicons.icon_bytes(ROOT, heard.append)
    inline.land()
    assert heard == [None]  # None to None: nothing new to say


def test_what_the_service_sends_passes_the_icon_gate_again(inline):
    inline.service.answer = b"<svg><script>alert(1)</script></svg>"
    heard = []
    remoteicons.icon_bytes(ROOT, heard.append)
    inline.land()
    assert heard == [] and remoteicons.icon_bytes(ROOT) is None


def test_forget_drops_what_is_held_so_the_next_ask_fetches(inline):
    other = "/srv/projects/beta"
    remoteicons.icon_bytes(ROOT)
    remoteicons.icon_bytes(other)
    inline.land()
    remoteicons.forget(ROOT)
    heard = []
    assert remoteicons.icon_bytes(ROOT, heard.append) is None  # nothing held: answered as unknown
    assert remoteicons.icon_bytes(other) == SVG and len(inline.pending) == 1
    inline.service.answer = SVG2
    inline.land()
    assert heard == [SVG2]
    remoteicons.forget()
    assert remoteicons.icon_bytes(other) is None and len(inline.pending) == 1


def test_a_listener_that_fails_does_not_take_the_others_with_it(inline):
    heard = []

    def bad(_data):
        raise RuntimeError("a sidebar row went away")

    remoteicons.icon_bytes(ROOT, bad)
    remoteicons.icon_bytes(ROOT, heard.append)
    inline.land()
    assert heard == [SVG] and remoteicons._waiting == {}


def test_no_root_is_no_icon_and_no_fetch(inline):
    assert remoteicons.icon_bytes(None) is None and remoteicons.icon_bytes("") is None
    assert inline.pending == []
