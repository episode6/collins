# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""`apilink.service_host()` (PR-3.3): the hostname the last hello named, which
the Preferences dialog's Service page is headed with. Foreign content (rule
5): only a non-empty string within the protocol's bound is taken."""

from collins import apilink
from collins.api import protocol


class _Link(apilink.Link):
    def _request(self, message, timeout=None):
        return {}


def test_service_host_is_the_hellos_host(monkeypatch):
    monkeypatch.setattr(apilink, "_current", None)
    assert apilink.service_host() is None  # no link
    link = _Link()
    monkeypatch.setattr(apilink, "_current", link)
    assert apilink.service_host() is None  # no hello yet
    link.hello = {"host": "box.example"}
    assert apilink.service_host() == "box.example"


def test_service_host_drops_what_does_not_fit(monkeypatch):
    link = _Link()
    monkeypatch.setattr(apilink, "_current", link)
    for bad in ("", "x" * (protocol.HOST_MAX + 1), 7, None, ["a"]):
        link.hello = {"host": bad}
        assert apilink.service_host() is None, bad
    link.hello = "not a dict"
    assert apilink.service_host() is None
    link.hello = {"host": "x" * protocol.HOST_MAX}
    assert apilink.service_host() == "x" * protocol.HOST_MAX
