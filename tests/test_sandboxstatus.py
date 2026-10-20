# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The client's copy of the sandbox probe's verdict (collins.sandboxstatus):
set from the service's word, read by the widgets, told to the listeners
when it moves (PR-1.12a)."""

import pytest

from collins import sandboxstatus


@pytest.fixture(autouse=True)
def fresh():
    sandboxstatus.reset()
    yield
    sandboxstatus.reset()


def test_unknown_until_the_service_says():
    assert sandboxstatus.probe_reason() is None
    sandboxstatus.set_probe_reason("")
    assert sandboxstatus.probe_reason() == ""
    sandboxstatus.set_probe_reason("bubblewrap is not installed")
    assert sandboxstatus.probe_reason() == "bubblewrap is not installed"


def test_a_non_string_is_no_verdict():
    sandboxstatus.set_probe_reason("")
    sandboxstatus.set_probe_reason(None)
    assert sandboxstatus.probe_reason() is None
    sandboxstatus.set_probe_reason(7)
    assert sandboxstatus.probe_reason() is None


def test_listeners_hear_a_move_and_only_a_move():
    heard = []
    stop = sandboxstatus.listen(heard.append)
    sandboxstatus.set_probe_reason("")
    sandboxstatus.set_probe_reason("")  # the same word again: nothing
    sandboxstatus.set_probe_reason("no user namespaces")
    assert heard == ["", "no user namespaces"]
    stop()
    sandboxstatus.set_probe_reason("")
    assert heard == ["", "no user namespaces"]


def test_a_failing_listener_does_not_stop_the_others():
    heard = []

    def broken(_reason):
        raise RuntimeError("boom")

    sandboxstatus.listen(broken)
    sandboxstatus.listen(heard.append)
    sandboxstatus.set_probe_reason("")
    assert heard == [""]
