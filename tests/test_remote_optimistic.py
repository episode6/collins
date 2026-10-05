# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""What a mirror applies before the service answers (D16, PR-1.12b). Over
the socket a write's reply and the event it implies land on the main loop
after the call returns; on the loopback they landed inside it, and the
window counts on some of those edges being synchronous (`_reraise_green`
on the store's `unread-changed`, the handoff reading the rows it just
rekeyed). So a flag this client decides is set on the row at once, and a
notification posted, raised, removed, cleared or rekeyed shows at once;
the service's echo confirms, or a refusal reverts (the state mirror's
case, tested in test_remote_state)."""

from collins import notifycenter, uistate
from collins.apilink import Link
from collins.remotenotify import RemoteNotifications
from collins.remotestate import RemoteState
from collins.remotestore import RemoteStore


class HeldLink(Link):
    """`send` holds its reply; `call` answers from a table and never sends
    an event: what the socket looks like before the primary catches up."""

    def __init__(self, answers=None):
        super().__init__()
        self.sent = []
        self.calls = []
        self.answers = dict(answers or {})

    def send(self, message, on_reply=None, on_refused=None):
        self.sent.append(message)
        self.handlers = (on_reply, on_refused)

    def call(self, message):
        self.calls.append(message)
        return dict(self.answers.get(message["t"], {}))

    def send_event(self, message):
        pass


def _store(tmp_path, app_state):
    link = HeldLink()
    ui = uistate.UiState(tmp_path / "ui.json", app_state.device_defaults())
    state = RemoteState(link, ui=ui)
    store = RemoteStore(link, state)
    link.dispatch({
        "t": "item", "session": "s1", "path": str(tmp_path / "s1.jsonl"), "cwd": str(tmp_path),
        "project_name": "alpha", "title": "One", "name": "One", "busy": False, "unread": False,
        "status": "", "backgrounding": False, "can_background": True,
    })
    link.dispatch({
        "t": "rows", "rows": ["s1"],
        "groups": [{"kind": "proj", "name": "alpha", "label": "alpha", "count": 1}],
        "empty": [], "order": ["alpha"], "show_archived": False, "total": 1, "hidden": 0, "size": 0,
        "projects": ["alpha"], "chats": 0, "order_changed": True,
    })
    return link, store


def test_a_client_flag_is_set_on_the_row_before_the_echo(tmp_path, app_state):
    link, store = _store(tmp_path, app_state)
    item = store.get_item("s1")
    assert item is not None and item.unread is False
    heard = []
    store.connect("unread-changed", lambda _s, sid, flag: heard.append((sid, flag)))
    store.set_unread("s1", True)
    assert item.unread is True
    assert heard == [("s1", True)]
    assert link.sent[-1] == {"t": "store.flags", "session": "s1", "unread": True}
    # The service's echo changes nothing a second time.
    link.dispatch({"t": "item", "session": "s1", "unread": True})
    assert heard == [("s1", True)]


def test_busy_is_the_services_and_only_sent(tmp_path, app_state):
    link, store = _store(tmp_path, app_state)
    item = store.get_item("s1")
    store.set_busy("s1", True)
    assert item.busy is False
    assert link.sent[-1] == {"t": "store.flags", "session": "s1", "busy": True}
    link.dispatch({"t": "item", "session": "s1", "busy": True})
    assert item.busy is True


def test_a_post_shows_at_once_and_the_echo_confirms():
    link = HeldLink({"notify.post": {"notification": "n-1"}})
    center = RemoteNotifications(link)
    changes = []
    center.connect(lambda: changes.append(1))
    row = center.post(center.make(notifycenter.KIND_MESSAGE, "s1", "One", "alpha", "hello"))
    assert row.id == "n-1" and center.get("n-1") is row and not row.read
    assert len(changes) == 1
    link.dispatch({
        "t": "notify", "notification": "n-1", "kind": notifycenter.KIND_MESSAGE, "session": "s1",
        "title": "One", "project": "alpha", "msgid": "hello", "when": row.when, "read": False, "count": 1,
    })
    assert [r.id for r in center.rows()] == ["n-1"]


def test_green_remove_clear_and_rekey_apply_at_once():
    link = HeldLink({
        "notify.green": {"changed": True},
        "notify.post": {"notification": "n-2"},
        "notify.remove": {"removed": 1},
        "notify.clear": {"removed": 1},
        "notify.rekey": {"moved": 1},
    })
    center = RemoteNotifications(link)
    assert center.set_green("s1", True, title="One", project="alpha") is True
    assert center.is_green("s1")
    center.post(center.make(notifycenter.KIND_MESSAGE, "placeholder-1", "P", "alpha", "hi"))
    assert center.rekey_session("placeholder-1", "s9") == 1
    assert center.get("n-2").session_id == "s9"
    assert center.remove("n-2") is True and center.get("n-2") is None
    center.post(center.make(notifycenter.KIND_MESSAGE, "s9", "P", "alpha", "again"))
    assert center.clear() == 1
    assert [r.id for r in center.rows()] == [notifycenter.green_id("s1")]  # the synthetic row stays
    assert center.set_green("s1", False) is True and not center.is_green("s1")


def test_a_refused_client_flag_is_put_back(tmp_path, app_state):
    from collins.api.protocol import RequestRefused

    link, store = _store(tmp_path, app_state)
    item = store.get_item("s1")
    heard = []
    store.connect("unread-changed", lambda _s, sid, flag: heard.append((sid, flag)))
    store.set_unread("s1", True)
    assert item.unread is True
    _reply, refused = link.handlers
    refused(RequestRefused("refused", "no", {}))
    assert item.unread is False
    assert heard == [("s1", True), ("s1", False)]
