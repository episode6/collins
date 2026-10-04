# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The notification history on the service and its client mirror (spec
§3.13, §3.14, PR-1.11): the service mints and keeps every row and tells
every subscriber; a client's `RemoteNotifications` is a NotificationCenter
over that, its rows' text translated from msgid and args, its reads told
back as `seen`, which every other client hears."""

import pytest

from collins import notifycenter
from collins.api import protocol
from collins.api.loopback import RequestRefused
from collins.apilink import Link
from collins.notifycenter import KIND_BELL, KIND_MESSAGE, KIND_UPDATE
from collins.remotenotify import RemoteNotifications
from collins.service.notifications import ServiceNotifications


class State:
    def __init__(self, records=None):
        self.records = list(records or [])
        self.writes = 0

    def get_notifications(self):
        return list(self.records)

    def set_notifications(self, records):
        if records != self.records:
            self.records = list(records)
            self.writes += 1


class Link_(Link):
    """A link straight to a ServiceNotifications: every request and event
    validated as the loopback validates them."""

    def __init__(self, service):
        super().__init__()
        self.service = service
        self.sent = []
        self._next = 1

    def deliver(self, event):
        checked = protocol.validate(dict(event), protocol.SERVICE)
        assert isinstance(checked, protocol.Message), checked
        self.dispatch(event)

    def _request(self, message):
        message = {**message, "id": self._next}
        self._next += 1
        self.sent.append(message)
        checked = protocol.validate(message, protocol.CLIENT)
        assert isinstance(checked, protocol.Message), checked
        answer = protocol.validate_response(self.service.handle(checked, self), checked.type)
        if not answer.ok:
            raise RequestRefused(answer.error, answer.msgid, answer.args)
        return dict(answer.fields)


def connect(service):
    link = Link_(service)
    mirror = RemoteNotifications(link)
    service.subscribe(link, link.deliver)
    return link, mirror


@pytest.fixture
def service():
    return ServiceNotifications(State())


def test_a_row_is_minted_on_the_service_and_mirrored(service):
    link, mirror = connect(service)
    calls = []
    mirror.connect(lambda: calls.append(1))
    row = mirror.post(mirror.make(KIND_MESSAGE, "s1", "Fix it", "proj", "Done!"))
    assert row is mirror.get(row.id) and row.id == service.center.rows()[0].id
    assert row.msgid == "Done!" and row.body == "Done!" and not row.read
    assert calls == [1]  # one change per write, however many events
    assert service.state.records[0]["msgid"] == "Done!"


def test_a_bell_coalesces_on_the_service(service):
    _link, mirror = connect(service)
    first = mirror.post(
        mirror.make(KIND_BELL, "s1", "t", "p", "Rang the bell", msgid=notifycenter.BELL_MSGID)
    )
    again = mirror.post(
        mirror.make(KIND_BELL, "s1", "t", "p", "Rang the bell", msgid=notifycenter.BELL_MSGID)
    )
    assert again.id == first.id and again.count == 2 and len(mirror.rows()) == 1


def test_the_body_is_translated_from_its_msgid_and_args(service, monkeypatch):
    from collins import i18n

    monkeypatch.setattr(i18n, "_", lambda text: {"Hi {who}": "Hallo {who}"}.get(text, text))
    _link, mirror = connect(service)
    service.post(KIND_MESSAGE, "s1", "t", "p", "Hi {who}", {"who": "Ada"})
    assert mirror.rows()[0].body == "Hallo Ada"
    assert service.center.rows()[0].body == "Hi Ada"  # the service writes English


def test_seen_reads_rows_everywhere(service):
    link_a, a = connect(service)
    link_b, b = connect(service)
    row = a.post(a.make(KIND_MESSAGE, "s1", "t", "p", "one"))
    a.post(a.make(KIND_MESSAGE, "s2", "t", "p", "two"))
    assert b.unread_count() == 2
    assert a.mark_read(row.id)
    assert a.unread_count() == 1 and b.unread_count() == 1 and service.center.unread_count() == 1
    assert any(m["t"] == "seen" and m["ids"] == [row.id] for m in link_a.sent)
    b.mark_session_read("s2")
    assert a.unread_count() == 0 and service.center.unread_count() == 0


def test_mark_all_read_and_clear_and_remove(service):
    _link, mirror = connect(service)
    gone = mirror.post(mirror.make(KIND_MESSAGE, "s1", "t", "p", "gone"))
    mirror.post(mirror.make(KIND_MESSAGE, "s1", "t", "p", "kept"))
    assert mirror.remove(gone.id) and mirror.get(gone.id) is None
    assert service.center.get(gone.id) is None
    mirror.set_green("s3", True, title="t", project="p")
    assert mirror.mark_all_read() == 2
    assert mirror.clear() == 1
    # The finished run's row stays: it is the flag's to take down.
    assert [r.kind for r in mirror.rows()] == [notifycenter.KIND_FINISHED]
    assert mirror.rows()[0].body == "Finished a run"


def test_green_is_raised_and_lowered_through_the_service(service):
    _link, mirror = connect(service)
    assert mirror.set_green("s1", True, title="t", project="p")
    assert mirror.is_green("s1") and service.center.is_green("s1")
    assert not mirror.set_green("s1", True)  # a no-op asks nothing
    assert mirror.set_green("s1", False) and not mirror.is_green("s1")


def test_a_placeholders_rows_are_rekeyed(service):
    _link, mirror = connect(service)
    mirror.post(mirror.make(KIND_MESSAGE, "placeholder-3", "t", "p", "early"))
    assert mirror.rekey_session("placeholder-3", "sid") == 1
    assert mirror.rows()[0].session_id == "sid"


def test_an_update_keeps_its_fixed_id_and_replaces_the_last(service):
    _link, mirror = connect(service)
    for version in ("0.2.0", "0.2.1"):
        row = mirror.make(
            KIND_UPDATE, "", f"Collins {version}", "", "x", msgid="Running {version}", args={"version": "0.1"}
        )
        row.id = notifycenter.update_id(version)
        row.url = "https://example.com/r"
        mirror.post(row)
    assert [r.id for r in mirror.rows()] == ["update:0.2.1"]
    assert mirror.rows()[0].body == "Running 0.1" and mirror.rows()[0].url == "https://example.com/r"


def test_a_fixed_id_is_refused_for_anything_but_an_update(service):
    link, _mirror = connect(service)
    with pytest.raises(RequestRefused):
        link.call({"t": "notify.post", "kind": "message", "msgid": "x", "key": "update:1"})


def test_the_snapshot_keeps_the_order_and_existing_history(service):
    old = [
        {
            "id": "b",
            "session_id": "s",
            "title": "t",
            "project": "p",
            "kind": KIND_BELL,
            "body": "Hat geklingelt",
            "when": 2e9,
            "read": False,
            "count": 2,
        },
        {
            "id": "a",
            "session_id": "s",
            "title": "t",
            "project": "p",
            "kind": KIND_MESSAGE,
            "body": "older",
            "when": 2e9 - 5,
            "read": True,
            "count": 1,
        },
    ]
    svc = ServiceNotifications(State(old))
    _link, mirror = connect(svc)
    assert [r.id for r in mirror.rows()] == ["b", "a"]
    # A record from before msgids: its body is its msgid, shown as it was.
    assert mirror.rows()[0].body == "Hat geklingelt" and mirror.rows()[0].count == 2


def test_a_refused_write_changes_nothing(service):
    _link, mirror = connect(service)
    mirror._link = type(
        "Dead",
        (Link,),
        {
            "_request": lambda self, m: (_ for _ in ()).throw(
                RequestRefused(protocol.ERROR_GONE, "Not connected to the service", {})
            )
        },
    )()
    row = mirror.make(KIND_MESSAGE, "s1", "t", "p", "lost")
    assert mirror.post(row) is row and mirror.rows() == []
