# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The client's mirror of the service's state (collins.remotestate):
optimistic writes, the refusal's revert, events that land while a write is
unanswered, the draft debounce and the routing of get_setting between this
device's file and the mirror. Driven through a fake link that holds every
reply until the test gives it."""

import json

import pytest

from collins import uistate
from collins.api.protocol import RequestRefused
from collins.apilink import Link
from collins.remotestate import DRAFT_DEBOUNCE_MS, RemoteState
from collins.state import device_defaults

SERVICE = "0123456789abcdef0123456789abcdef"


class FakeLink(Link):
    """Every request is held until `reply` or `refuse` answers it."""

    def __init__(self):
        super().__init__()
        self.sent = []  # [message, on_reply, on_refused]

    def send(self, message, on_reply=None, on_refused=None):
        self.sent.append([message, on_reply, on_refused])

    def call(self, message):
        self.sent.append([message, None, None])
        return {}

    def reply(self, index=0, **fields):
        message, on_reply, _on_refused = self.sent.pop(index)
        if on_reply is not None:
            on_reply(fields)
        return message

    def refuse(self, index=0, msgid="{key} does not take that value", **details):
        message, _on_reply, on_refused = self.sent.pop(index)
        on_refused(RequestRefused("refused", msgid, details or {"key": message.get("key", "")}))
        return message

    def event(self, key, value, entry=None):
        event = {"t": "state.set", "key": key, "value": value}
        if entry is not None:
            event["entry"] = entry
        self.dispatch(event)


class FakeTimers:
    def __init__(self):
        self.due = {}
        self.next = 1

    def schedule(self, ms, fn):
        assert ms == DRAFT_DEBOUNCE_MS
        handle = self.next
        self.next += 1
        self.due[handle] = fn
        return handle

    def cancel(self, handle):
        self.due.pop(handle, None)

    def fire_all(self):
        for handle in list(self.due):
            self.due.pop(handle)()


@pytest.fixture
def mirror(tmp_path):
    link = FakeLink()
    timers = FakeTimers()
    toasts = []
    ui = uistate.UiState(tmp_path / "ui-state.json", device_defaults())
    state = RemoteState(
        link, ui=ui, schedule=timers.schedule, cancel=timers.cancel, on_refused=toasts.append
    )
    changes = []
    state.connect_changed(lambda key, entry, reverted: changes.append((key, entry, reverted)))
    # The snapshot.
    link.event("service_id", SERVICE)
    link.event("names", {"a": "Old"})
    link.event("favorites", ["b"])
    link.event("archived", [])
    link.event("session_drafts", {})
    link.event("settings", {"title_model": "", "archive_worktree": "ask"})
    changes.clear()
    return state, link, timers, toasts, changes


def test_the_snapshot_fills_the_mirror(mirror):
    state, _link, _timers, _toasts, _changes = mirror
    assert state.service_id == SERVICE
    assert state.get_name("a") == "Old"
    assert state.is_favorite("b")
    assert state.get_setting("archive_worktree") == "ask"


def test_a_write_is_optimistic_and_sent_as_one_entry(mirror):
    state, link, _timers, _toasts, _changes = mirror
    state.set_name("a", "New")
    assert state.get_name("a") == "New"  # at once, before any reply
    [(message, _ok, _refused)] = link.sent
    assert message == {"t": "state.set", "key": "names", "entry": "a", "value": "New"}
    assert state.is_pending("names", "a")
    link.reply()
    assert not state.is_pending("names", "a")
    assert state.get_name("a") == "New"


def test_a_refused_write_reverts_notifies_and_toasts(mirror):
    state, link, _timers, toasts, changes = mirror
    state.set_name("a", "New")
    link.refuse(msgid="{key} does not take that value")
    assert state.get_name("a") == "Old"
    assert ("names", "a", True) in changes
    assert toasts == ["Not saved: names does not take that value"]


def test_a_refused_whole_key_write_reverts(mirror):
    state, link, _timers, _toasts, changes = mirror
    state.toggle_favorite("a")
    assert state.is_favorite("a")
    [(message, _ok, _refused)] = link.sent
    assert message == {"t": "state.set", "key": "favorites", "value": ["a", "b"]}
    link.refuse()
    assert not state.is_favorite("a") and state.is_favorite("b")
    assert ("favorites", None, True) in changes


def test_an_event_during_an_unanswered_write_does_not_clobber_it(mirror):
    state, link, _timers, _toasts, changes = mirror
    state.set_name("a", "Mine")
    assert changes == [("names", "a", False)]  # the write itself is announced
    changes.clear()
    # Another client's write lands first.
    link.event("names", "Theirs", entry="a")
    assert state.get_name("a") == "Mine"  # the optimistic value holds
    assert changes == []  # and nothing moved in the mirror
    # The service's answer to ours is the last word, and its echo with it.
    link.event("names", "Mine", entry="a")
    link.reply()
    assert state.get_name("a") == "Mine"


def test_after_the_reply_the_services_value_wins(mirror):
    state, link, _timers, _toasts, changes = mirror
    state.set_name("a", "Mine")
    link.event("names", "Theirs", entry="a")
    assert state.get_name("a") == "Mine"
    link.reply()  # ours was taken, but the service kept the later write
    assert state.get_name("a") == "Theirs"
    assert ("names", "a", False) in changes


def test_a_refusal_reverts_to_what_arrived_meanwhile(mirror):
    state, link, _timers, _toasts, _changes = mirror
    state.set_name("a", "Mine")
    link.event("names", "Theirs", entry="a")
    link.refuse()
    assert state.get_name("a") == "Theirs"


def test_two_writes_in_flight_and_an_event_between(mirror):
    """The first write's late reply must not confirm its value when an
    event landed after it went: what was heard is per send, not per mark."""
    state, link, _timers, toasts, _changes = mirror
    state.set_name("a", "first")
    link.event("names", "other", entry="a")  # another client's, after ours
    state.set_name("a", "second")
    assert len(link.sent) == 2  # both unanswered
    link.reply(0)  # the first is taken: but "other" came after it
    assert state.get_name("a") == "second"  # the second still holds
    link.refuse(0)  # the second is refused
    assert state.get_name("a") == "other"  # back to the service's last word
    assert len(toasts) == 1


def test_two_writes_in_flight_settle_on_the_last(mirror):
    state, link, _timers, _toasts, _changes = mirror
    state.set_name("a", "first")
    state.set_name("a", "second")
    link.reply(0)
    assert state.get_name("a") == "second"
    link.reply(0)
    assert state.get_name("a") == "second"
    assert not state.is_pending("names", "a")


def test_a_whole_map_event_keeps_the_pending_entries(mirror):
    state, link, _timers, _toasts, _changes = mirror
    state.set_name("a", "Mine")
    link.event("names", {"a": "Theirs", "c": "Other"})
    assert state.get_name("a") == "Mine"
    assert state.get_name("c") == "Other"


def test_a_whole_key_event_waits_for_the_pending_write(mirror):
    state, link, _timers, _toasts, _changes = mirror
    state.toggle_favorite("a")
    link.event("favorites", ["b", "c"])
    assert state.is_favorite("a") and not state.is_favorite("c")
    link.reply()
    assert state.is_favorite("c") and not state.is_favorite("a")


def test_a_store_request_carries_the_state_change(mirror):
    state, link, _timers, _toasts, changes = mirror
    applied = []
    state.request(
        {"t": "store.archive", "sessions": ["a"], "archived": True},
        mutate=lambda: state.set_archived("a", True),
        applied=lambda: applied.append(state.is_archived("a")),
    )
    assert applied == [True]
    [(message, _ok, _refused)] = link.sent
    assert message["t"] == "store.archive"  # no state.set of its own
    assert state.is_pending("archived")
    link.refuse(msgid="no")
    assert not state.is_archived("a")
    assert ("archived", None, True) in changes


def test_drafts_are_debounced_per_draft(mirror):
    state, link, timers, _toasts, _changes = mirror
    state.set_session_draft("s1", "a")
    state.set_session_draft("s1", "ab")
    state.set_session_draft("s2", "x")
    assert link.sent == []
    assert len(timers.due) == 2  # one timer per draft
    assert state.get_session_draft("s1") == "ab"
    timers.fire_all()
    sent = sorted((m["entry"], m["value"]) for m, _ok, _refused in link.sent)
    assert sent == [("s1", "ab"), ("s2", "x")]


def test_a_flush_sends_a_draft_at_once(mirror):
    state, link, timers, _toasts, _changes = mirror
    state.set_session_draft("s1", "half a prompt")
    state.flush_drafts("s1")
    assert timers.due == {}
    [(message, _ok, _refused)] = link.sent
    assert message == {"t": "state.set", "key": "session_drafts", "entry": "s1", "value": "half a prompt"}
    # Emptying it (a send) goes out as a removal.
    link.reply()
    state.set_session_draft("s1", "")
    state.flush_drafts()
    [(message, _ok, _refused)] = link.sent
    assert message["value"] is None


def test_a_draft_held_by_the_debounce_is_not_clobbered(mirror):
    state, link, timers, _toasts, _changes = mirror
    state.set_session_draft("s1", "mine")
    link.event("session_drafts", "older", entry="s1")
    assert state.get_session_draft("s1") == "mine"
    timers.fire_all()
    # Ours went out after theirs landed, so the service holds ours.
    [(message, _ok, _refused)] = link.sent
    assert message["value"] == "mine"
    link.reply()
    assert state.get_session_draft("s1") == "mine"


def test_get_setting_reads_the_device_first(mirror, tmp_path):
    state, link, _timers, _toasts, _changes = mirror
    state.set_setting("font", "Mono 13")
    assert link.sent == []  # a device setting never reaches the service
    assert state.get_setting("font") == "Mono 13"
    saved = json.loads((tmp_path / "ui-state.json").read_text())
    assert saved["device"]["settings"]["font"] == "Mono 13"
    # The service sending a device key changes nothing here.
    link.event("settings", "Comic 99", entry="font")
    assert state.get_setting("font") == "Mono 13"


def test_get_setting_reads_the_service_second(mirror):
    state, link, _timers, _toasts, _changes = mirror
    link.event("settings", "always", entry="archive_worktree")
    assert state.get_setting("archive_worktree") == "always"
    state.set_setting("title_model", "none")
    [(message, _ok, _refused)] = link.sent
    assert message == {"t": "state.set", "key": "settings", "entry": "title_model", "value": "none"}
    assert state.get_setting("title_model") == "none"
    assert state.settings["title_model"] == "none"  # the merged dict tabs hold


def test_update_settings_splits_by_side(mirror):
    state, link, _timers, _toasts, _changes = mirror
    state.update_settings({"scrollback": 500, "archive_on_claude_ai": False})
    assert [m["entry"] for m, _ok, _refused in link.sent] == ["archive_on_claude_ai"]
    assert state.get_setting("scrollback") == 500


def test_every_shared_key_survives_the_trip(app_state, tmp_path):
    """What the service exports, a mirror imports to the same value: each
    key's cleaner keeps everything a real state holds."""
    from collins.state import SHARED_KEYS

    service = app_state.AppState(migrate=True, device=False)
    box = "a" * 32
    service.set_name("s1", "Named")
    service.set_generated_name("s2", "Generated")
    service.set_cli_titles({"s3": "CLI"})
    service.set_emoji("s1", "🦊")
    service.toggle_favorite("s1")
    service.set_archived("s2", True)
    service.set_project_archived("old", True)
    service.set_project_worktree("alpha", True)
    service.set_project_sandbox("alpha", False)
    service.set_sandboxed("s1", True, box=box)
    service.set_sandbox_grants(box, ["/data"])
    service.set_sandbox_project_grants("/home/u/alpha", ["/data"])
    service.set_project_order(["beta", "alpha"])
    service.keep_virtual_projects({"kept": "/home/u/kept"})
    service.set_groups_expanded(["proj:alpha"], True)
    service.set_session_prs("s1", [{"number": 1, "url": "https://github.com/o/r/pull/1"}])
    service.set_session_draft("s1", "half")
    service.set_process_baseline("s1", ["node mcp"])
    service.forward_session("s1", "s4")
    service.set_pending_detach("s5", provider="claude", cwd="/home/u", uuid="u")
    service.set_pty(3, {"kind": "agent", "cwd": "/home/u", "cols": 80, "rows": 24})
    service.set_setting("title_model", "none")
    link = FakeLink()
    mirror = RemoteState(link, ui=uistate.UiState(tmp_path / "ui.json", device_defaults()))
    for name in SHARED_KEYS:
        link.event(name, service.export_key(name))
    for name in SHARED_KEYS:
        assert mirror.export_key(name) == service.export_key(name), name
    assert mirror.resolve_forward("s1") == "s4"
    assert mirror.sandbox_box("s4") == box
    assert mirror.get_setting("title_model") == "none"


def test_service_scoped_settings_wait_for_the_service_id(tmp_path):
    link = FakeLink()
    ui = uistate.UiState(tmp_path / "ui-state.json", device_defaults())
    ui.set_scoped(SERVICE, "last_active_session", "sid-7")
    state = RemoteState(link, ui=ui)
    assert state.get_setting("last_active_session") == ""
    link.event("service_id", SERVICE)
    assert state.get_setting("last_active_session") == "sid-7"
    assert state.settings["last_active_session"] == "sid-7"
