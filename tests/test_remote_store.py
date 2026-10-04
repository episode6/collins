# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The client's mirror of the service's store (collins.remotestore) over
the real service core and the loopback (collins.service.core,
collins.service.storefeed, collins.api.loopback): the snapshot fills the
rows, an `item` event moves one property and announces it, a mutation is a
request the service carries out, the archived sessions are paged in only
when asked for, and the service refuses a device setting written through
the API."""

import json

import pytest

from collins import uistate
from collins.api import loopback
from collins.api.loopback import RequestRefused
from collins.apilink import LoopbackLink
from collins.remotestate import RemoteState
from collins.remotestore import RemoteStore
from collins.service.core import ServiceCore
from collins.sessions import discover_sessions
from collins.store import SessionStore


@pytest.fixture
def world(app_state, projects_dir, tmp_path):
    """The service (its own AppState and a store holding the fixture's three
    sessions, one of them archived) and a client's two mirrors on it."""
    _root, ids = projects_dir
    service_state = app_state.AppState(migrate=True, device=False)
    service_state.set_name(ids["alpha1"], "Named alpha")
    service_state.set_archived(ids["beta1"], True)
    core = ServiceCore(state=service_state, state_dir=tmp_path / "pty")
    store = SessionStore(service_state)
    core.start_store(store)
    store._last_sessions = discover_sessions()
    store._apply()
    server = loopback.LoopbackServer(core)
    link = LoopbackLink()
    client = server.connect(lambda *_frame: None, link.dispatch, device="laptop")
    link.bind(client)
    ui = uistate.UiState(tmp_path / "client-ui.json", app_state.device_defaults())
    state = RemoteState(link, ui=ui)
    remote = RemoteStore(link, state, pr_store=store.pr_store)
    remote.subscribe()
    yield store, remote, state, ids, link
    server.shutdown()


def _row_ids(model):
    return [model.get_item(i).session_id for i in range(model.get_n_items())]


def test_the_snapshot_fills_the_rows(world):
    store, remote, state, ids, _link = world
    assert _row_ids(remote.model) == _row_ids(store.model)
    assert ids["beta1"] not in _row_ids(remote.model)
    item = remote.get_item(ids["alpha1"])
    assert item.display_name == "Named alpha"
    assert item.session.project_name == "alpha"
    assert item.group_key == ("proj", "alpha")
    assert remote.group_counts == store.group_counts
    assert remote.empty_groups == store.empty_groups
    assert remote.resolved_project_order == store.resolved_project_order
    # The state's mirror came with it.
    assert state.get_name(ids["alpha1"]) == "Named alpha"
    assert state.is_archived(ids["beta1"])
    assert state.service_id == store.state.service_id


def test_the_snapshot_leaves_the_archive_out(world):
    _store, remote, _state, ids, _link = world
    assert ids["beta1"] not in remote.known_sessions
    summary = remote.summary()
    assert summary["total"] == 3 and summary["hidden"] == 1
    assert remote.has_archived()
    assert remote.session_count() == 3


def test_archived_sessions_are_paged_in_when_asked_for(world):
    _store, remote, _state, ids, _link = world
    assert [s.session_id for s in remote.archived_sessions()] == [ids["beta1"]]
    assert ids["beta1"] in remote.known_sessions
    assert len(remote.sessions) == 3
    assert remote.archived_breakdown() == [("beta", 1, 1)]


def test_showing_the_archive_pages_it_in(world):
    store, remote, _state, ids, _link = world
    remote.set_show_archived(True)
    assert store.show_archived
    assert ids["beta1"] in _row_ids(remote.model)
    assert remote.get_item(ids["beta1"]).session.project_name == "beta"


def test_a_lookup_fetches_one_out_of_sight_session(world):
    _store, remote, _state, ids, _link = world
    session = remote.get_session(ids["beta1"])
    assert session is not None and session.project_name == "beta"
    assert remote.get_session("no-such-session") is None


def test_an_item_event_moves_one_property_and_announces_it(world):
    store, remote, _state, ids, _link = world
    seen = []
    remote.connect("busy-changed", lambda _s, sid, flag: seen.append(("busy", sid, flag)))
    remote.connect("unread-changed", lambda _s, sid, flag: seen.append(("unread", sid, flag)))
    store.set_busy(ids["alpha2"], True)  # the service decides
    assert remote.get_item(ids["alpha2"]).busy
    assert seen == [("busy", ids["alpha2"], True)]
    # The tracker's verdict from the client goes through the service.
    remote.set_unread(ids["alpha2"], True)
    assert store.get_item(ids["alpha2"]).unread
    assert remote.get_item(ids["alpha2"]).unread
    assert seen[-1] == ("unread", ids["alpha2"], True)
    remote.set_status(ids["alpha2"], "open")
    assert remote.get_item(ids["alpha2"]).status == "open"


def test_a_rename_is_a_request_the_service_carries_out(world):
    store, remote, state, ids, _link = world
    refreshed = []
    remote.connect("refreshed", lambda _s, changed: refreshed.append(changed))
    remote.rename(ids["alpha2"], "Second")
    assert store.state.get_name(ids["alpha2"]) == "Second"
    assert remote.get_item(ids["alpha2"]).display_name == "Second"
    assert state.get_name(ids["alpha2"]) == "Second"
    assert refreshed == [False]


def test_archiving_puts_the_row_away(world):
    store, remote, state, ids, _link = world
    archived = []
    remote.connect("archived", lambda _s, sid: archived.append(sid))
    remote.set_unread(ids["alpha2"], True)
    remote.archive_many([ids["alpha2"]])
    assert store.state.is_archived(ids["alpha2"])
    assert state.is_archived(ids["alpha2"])
    assert archived == [ids["alpha2"]]
    assert remote.get_item(ids["alpha2"]) is None
    assert ids["alpha2"] in remote.known_sessions  # still held, now out of sight
    remote.restore_many([ids["alpha2"]])
    assert remote.get_item(ids["alpha2"]) is not None


def test_a_favorite_moves_the_row(world):
    store, remote, _state, ids, _link = world
    remote.toggle_favorite(ids["alpha2"])
    assert store.state.is_favorite(ids["alpha2"])
    item = remote.get_item(ids["alpha2"])
    assert item.favorite and item.group_key == ("fav", "")
    assert _row_ids(remote.model)[0] == ids["alpha2"]


def test_trashing_removes_the_session(world, monkeypatch):
    import collins.store as store_mod

    # The temp dir's filesystem has no trash; unlinking stands in for it.
    monkeypatch.setattr(store_mod, "_trash_file", lambda path: path.unlink())
    store, remote, _state, ids, _link = world
    errors = remote.trash_many([ids["alpha1"], "missing-id"])
    assert errors == {"missing-id": "session not found"}
    assert ids["alpha1"] not in remote.known_sessions
    assert ids["alpha1"] not in _row_ids(remote.model)
    assert ids["alpha1"] not in store.sessions


def test_project_order_is_optimistic_and_agrees(world):
    store, remote, state, _ids, _link = world
    remote.move_project("beta", "alpha")
    assert state.get_project_order() == store.state.get_project_order()
    assert remote.resolved_project_order == store.resolved_project_order


def test_a_setting_write_reaches_the_service_file(world, app_state):
    store, _remote, state, _ids, _link = world
    state.set_setting("archive_worktree", "always")
    assert store.state.get_setting("archive_worktree") == "always"
    saved = json.loads(app_state._STATE_FILE.read_text())
    assert saved["settings"]["archive_worktree"] == "always"
    # A device setting stays on the device.
    state.set_setting("font", "Mono 9")
    saved = json.loads(app_state._STATE_FILE.read_text())
    assert "font" not in saved["settings"]


def test_the_service_refuses_a_device_setting(world):
    _store, _remote, _state, _ids, link = world
    with pytest.raises(RequestRefused) as refused:
        link.call({"t": "state.set", "key": "settings", "entry": "font", "value": "x"})
    assert refused.value.error == "refused"
    with pytest.raises(RequestRefused) as refused:
        link.call({"t": "state.set", "key": "service_id", "value": "f" * 32})
    assert refused.value.error == "refused"
    with pytest.raises(RequestRefused) as refused:
        link.call({"t": "state.set", "key": "favorites", "value": {"not": "a list"}})
    assert refused.value.error == "refused"


def test_a_setting_of_the_wrong_type_is_refused_and_reverted(world, app_state):
    store, _remote, state, _ids, _link = world
    toasts = []
    state.on_refused = toasts.append
    before = store.state.get_setting("git_log_page")
    state.set_setting("git_log_page", "abc")
    assert store.state.get_setting("git_log_page") == before
    assert state.get_setting("git_log_page") == before  # reverted
    assert toasts == ["Not saved: git_log_page does not take that value"]
    saved = json.loads(app_state._STATE_FILE.read_text())
    assert saved["settings"].get("git_log_page") != "abc"
    # A bool is no int, an exact type is.
    state.set_setting("git_log_page", True)
    assert store.state.get_setting("git_log_page") == before
    state.set_setting("git_log_page", 250)
    assert store.state.get_setting("git_log_page") == 250


def test_a_setting_the_catalogue_does_not_name_is_refused(world, app_state):
    store, _remote, state, _ids, _link = world
    toasts = []
    state.on_refused = toasts.append
    state.set_setting("totally_unknown_key", {"nested": [1]})
    assert "totally_unknown_key" not in store.state.settings
    assert state.get_setting("totally_unknown_key") is None  # reverted
    assert toasts == ["Not saved: totally_unknown_key does not take that value"]
    saved = json.loads(app_state._STATE_FILE.read_text())
    assert "totally_unknown_key" not in saved["settings"]


def test_a_name_written_on_the_state_reaches_the_row(world):
    """Not through a store method (the PR-title and title paths write the
    state directly): the row shows it at once, and the service holds it."""
    store, remote, state, ids, _link = world
    state.set_name(ids["alpha2"], "Written on the state")
    assert remote.get_item(ids["alpha2"]).display_name == "Written on the state"
    assert store.state.get_name(ids["alpha2"]) == "Written on the state"
    state.set_generated_name(ids["alpha1"], "Generated")  # a manual name still wins
    assert remote.get_item(ids["alpha1"]).display_name == "Named alpha"
    state.toggle_favorite(ids["alpha2"])
    assert remote.get_item(ids["alpha2"]).favorite


def test_a_write_from_elsewhere_reaches_the_mirror(world):
    store, remote, state, ids, _link = world
    store.state.set_emoji(ids["alpha1"], "🦊")  # the service's own write
    assert state.get_emoji(ids["alpha1"]) == "🦊"


def test_state_get_answers_from_the_service(world):
    _store, _remote, _state, ids, link = world
    reply = link.call({"t": "state.get", "key": "names", "entry": ids["alpha1"]})
    assert reply["value"] == "Named alpha"


def test_a_core_without_state_refuses_the_store(tmp_path):
    core = ServiceCore(state_dir=tmp_path / "pty")
    server = loopback.LoopbackServer(core)
    client = server.connect(lambda *_a: None, lambda _e: None)
    with pytest.raises(RequestRefused) as refused:
        client.request({"t": "subscribe"})
    assert refused.value.error == "unknown"
    server.shutdown()
