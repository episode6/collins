# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The service's claude CLI as the client knows it (collins.remotecli),
through a fake link: the connect's word is what `missing` and `found_at`
read (never this process's PATH), a service that never said is not
missing a CLI, `detect` and `check` are `cli.check` asks whose `found`
keeps the word current, and a refusal or no link at all is a `missing`
verdict, never a crash."""

from __future__ import annotations

import pytest

from collins import apilink, clisetup, remotecli
from collins.api import protocol
from collins.api.protocol import RequestRefused


class FakeLink(apilink.Link):
    def __init__(self) -> None:
        super().__init__()
        self.asked: list[dict] = []
        self.answer: object = {}

    def _request(self, message: dict, timeout: float | None = None) -> dict:
        self.asked.append(dict(message))
        if isinstance(self.answer, Exception):
            raise self.answer
        return dict(self.answer(message) if callable(self.answer) else self.answer)


@pytest.fixture
def link(monkeypatch):
    fake = FakeLink()
    monkeypatch.setattr(apilink, "_current", fake)
    monkeypatch.setattr(remotecli, "_found", None)
    return fake


def test_the_word_at_connect_is_what_the_gate_reads(link, monkeypatch):
    monkeypatch.setenv("PATH", "/nowhere")  # this process's PATH is never asked
    remotecli.set_found("/home/u/.local/bin/claude")
    assert not remotecli.missing()
    assert remotecli.found_at() == "/home/u/.local/bin/claude"
    remotecli.set_found("")
    assert remotecli.missing() and remotecli.found_at() is None


def test_a_service_that_never_said_is_not_missing_a_cli(link):
    remotecli.set_found(None)  # an older service: no `cli` in its status
    assert not remotecli.missing() and remotecli.found_at() is None
    remotecli.set_found(17)  # nothing but text is a word
    assert not remotecli.missing()


def test_detect_is_a_cli_check_with_no_path(link):
    link.answer = {"found": "", "detected": "/home/u/.local/bin/claude"}
    assert remotecli.detect() == "/home/u/.local/bin/claude"
    assert link.asked == [{"t": "cli.check"}]
    assert remotecli.missing()  # its `found` is the word now


def test_detect_refused_prefills_nothing(link):
    link.answer = RequestRefused(protocol.ERROR_UNKNOWN, "Unknown request {type}", {"type": "cli.check"})
    assert remotecli.detect() == ""


def test_check_hands_on_the_services_verdict(link):
    link.answer = {"found": "/usr/bin/claude", "status": "versioned"}
    verdicts = []
    remotecli.check("~/.local/share/claude/versions/2.1.226", verdicts.append)
    assert verdicts == ["versioned"]
    assert link.asked == [{"t": "cli.check", "path": "~/.local/share/claude/versions/2.1.226"}]
    assert remotecli.found_at() == "/usr/bin/claude"


def test_check_refused_or_unlinked_is_missing(link, monkeypatch):
    link.answer = RequestRefused(protocol.ERROR_GONE, "Not connected to the service", {})
    verdicts = []
    remotecli.check("/usr/bin/claude", verdicts.append)
    monkeypatch.setattr(apilink, "_current", None)
    remotecli.check("/usr/bin/claude", verdicts.append)
    assert verdicts == [clisetup.MISSING, clisetup.MISSING]


def test_refresh_updates_the_word_then_says_so(link):
    link.answer = {"found": "/home/u/tools/claude"}
    done = []
    remotecli.refresh(lambda: done.append(remotecli.found_at()))
    assert done == ["/home/u/tools/claude"]
    link.answer = RequestRefused(protocol.ERROR_GONE, "Not connected to the service", {})
    remotecli.refresh(lambda: done.append("refused"))
    assert done[-1] == "refused" and remotecli.found_at() == "/home/u/tools/claude"


def test_the_offered_agents_are_the_services_word_not_this_path(link, monkeypatch):
    """PR-3.0 item 1: the client offers an agent unless the service said it
    has no CLI; this process's PATH (which `Provider.available` reads) is
    never asked."""
    from collins import providers

    monkeypatch.setenv("PATH", "/nowhere")
    monkeypatch.setattr(providers.Provider, "available", lambda self: pytest.fail("asked this PATH"))
    remotecli.set_found(None)  # not said yet: offered
    assert [p.id for p in remotecli.available_providers()] == ["claude"]
    remotecli.set_found("/home/u/.local/bin/claude")
    assert [p.id for p in remotecli.available_providers()] == ["claude"]
    assert remotecli.default_provider().id == "claude"
    remotecli.set_found("")  # the service has none: nothing offered
    assert remotecli.available_providers() == []
    assert remotecli.default_provider().id == "claude"
