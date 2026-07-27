import threading

import pytest

from collins import bgstatus
from collins.bgstatus import BackgroundStatusPoller, fetch_background_ids
from collins.providers import BackgroundAgent, ClaudeProvider


class FakeThread:
    """Captures thread targets instead of running them, so tests drive the
    fetch → _apply flow synchronously."""

    started: list["FakeThread"] = []

    def __init__(self, target=None, daemon=None) -> None:
        self.target = target

    def start(self) -> None:
        FakeThread.started.append(self)


@pytest.fixture
def fake_threads(monkeypatch):
    FakeThread.started = []
    monkeypatch.setattr(threading, "Thread", FakeThread)
    return FakeThread.started


def test_apply_diffs_sets_and_notifies():
    changes = []
    poller = BackgroundStatusPoller(fetch=set, on_change=changes.append)
    poller._apply({"a", "b"})
    assert poller.background_ids == {"a", "b"}
    assert changes == [{"a", "b"}]
    # Symmetric difference: departed ids re-sync too, not just arrivals.
    poller._apply({"b", "c"})
    assert poller.background_ids == {"b", "c"}
    assert changes[-1] == {"a", "c"}


def test_apply_unchanged_set_does_not_notify():
    changes = []
    poller = BackgroundStatusPoller(fetch=set, on_change=changes.append)
    poller._apply({"a"})
    poller._apply({"a"})
    assert changes == [{"a"}]


def test_apply_none_keeps_last_known_ids():
    # A fetch that raised reports None: keep the last-known set rather than
    # clearing every dot on a transient failure.
    changes = []
    poller = BackgroundStatusPoller(fetch=set, on_change=changes.append)
    poller._apply({"a"})
    poller._apply(None)
    assert poller.background_ids == {"a"}
    assert changes == [{"a"}]


def test_refresh_coalesces_concurrent_requests(fake_threads):
    poller = BackgroundStatusPoller(fetch=lambda: {"a"})
    poller.refresh()
    poller.refresh()  # lands while the first fetch is "in flight"
    poller.refresh()
    assert len(fake_threads) == 1
    # Completing the in-flight fetch runs exactly one queued follow-up.
    poller._apply({"a"})
    assert len(fake_threads) == 2
    poller._apply({"a"})
    assert len(fake_threads) == 2


def test_refresh_after_stop_is_a_noop(fake_threads):
    poller = BackgroundStatusPoller(fetch=set)
    poller.stop()
    poller.refresh()
    assert fake_threads == []


def test_set_polling_starts_once_and_stops(fake_threads):
    poller = BackgroundStatusPoller(fetch=set)
    poller.set_polling(True)
    poller.set_polling(True)  # idempotent: one timer, one refresh
    assert len(fake_threads) == 1
    assert poller._poll_source is not None
    poller.set_polling(False)
    assert poller._poll_source is None
    poller.stop()


def test_fetch_background_ids_unions_providers(monkeypatch):
    monkeypatch.setattr(
        bgstatus,
        "available_providers",
        lambda: [ClaudeProvider()],
    )
    monkeypatch.setattr(
        ClaudeProvider,
        "background_agents",
        lambda self: [
            BackgroundAgent(session_id="s1", job_id="j1", cwd="/p"),
            BackgroundAgent(session_id="s2", job_id="j2", cwd="/q"),
        ],
    )
    assert fetch_background_ids() == {"s1", "s2"}
