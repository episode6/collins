import pytest

from collins import store as store_mod
from collins.sessions import discover_sessions


@pytest.fixture
def store(app_state, projects_dir):
    """A store holding the fixture's three sessions, without the background
    scan: feed the discovered sessions in and project them directly."""
    store = store_mod.SessionStore(app_state.AppState())
    store._last_sessions = discover_sessions()
    store._apply()
    return store


def _by_project(store, project: str) -> list[str]:
    return [s.session_id for s in store._last_sessions if s.project_name == project]


def test_hidden_sessions_individually_hidden(store):
    assert store.hidden_sessions() == []

    session_id = store._last_sessions[0].session_id
    store.set_hidden(session_id, True)
    assert [s.session_id for s in store.hidden_sessions()] == [session_id]

    # Independent of the "Show hidden sessions" toggle: the rows come back
    # into view but the sessions are still hidden ones.
    store.set_show_hidden(True)
    assert [s.session_id for s in store.hidden_sessions()] == [session_id]


def test_hidden_sessions_include_hidden_projects(store):
    project = store._last_sessions[0].project_name
    store.set_project_hidden(project, True)
    assert sorted(s.session_id for s in store.hidden_sessions()) == sorted(
        _by_project(store, project)
    )


def test_trash_many_drops_rows(store, monkeypatch):
    trashed = []
    monkeypatch.setattr(store_mod, "_trash_file", lambda path: trashed.append(path))

    doomed = [s.session_id for s in store._last_sessions[:2]]
    survivor = store._last_sessions[2].session_id

    assert store.trash_many(doomed) == {}
    assert len(trashed) == 2
    assert list(store.sessions) == [survivor]
    assert store.model.get_n_items() == 1


def test_trash_many_reports_failures(store, monkeypatch):
    doomed, survivor = (s.session_id for s in store._last_sessions[:2])
    monkeypatch.setattr(
        store_mod,
        "_trash_file",
        lambda path: None if path.stem == doomed else "Permission denied",
    )

    errors = store.trash_many([doomed, survivor, "no-such-session"])
    assert errors == {survivor: "Permission denied", "no-such-session": "session not found"}
    # The one that failed keeps its row; only the trashed session goes.
    assert doomed not in store.sessions
    assert survivor in store.sessions
