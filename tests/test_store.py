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


def test_archived_sessions_individually_archived(store):
    assert store.archived_sessions() == []

    session_id = store._last_sessions[0].session_id
    store.set_archived(session_id, True)
    assert [s.session_id for s in store.archived_sessions()] == [session_id]

    # Independent of the "Show archived sessions" toggle: the rows come back
    # into view but the sessions are still archived ones.
    store.set_show_archived(True)
    assert [s.session_id for s in store.archived_sessions()] == [session_id]


def test_archived_sessions_include_archived_projects(store):
    project = store._last_sessions[0].project_name
    store.set_project_archived(project, True)
    assert sorted(s.session_id for s in store.archived_sessions()) == sorted(
        _by_project(store, project)
    )


def test_archived_sessions_include_sessions_a_fork_replaced(store):
    original, fork = (s.session_id for s in store._last_sessions[:2])
    store.record_forward(original, fork)

    # The original's row is gone from the sidebar even though nobody archived
    # it — the fork stands in for it — so the bulk delete has to see it as
    # archived.
    assert store.forward_state(store.sessions[original]) == "moved"
    assert [s.session_id for s in store.archived_sessions()] == [original]


def test_archived_breakdown_counts_per_project(store):
    alpha = [s for s in store._last_sessions if s.project_name == "alpha"]
    beta = [s for s in store._last_sessions if s.project_name == "beta"]
    store.set_archived(alpha[0].session_id, True)
    store.set_project_archived("beta", True)

    # (project, archived count, total sessions in the project) — beta loses
    # every session it has, which is what the confirmation warns about.
    assert store.archived_breakdown() == [
        ("alpha", 1, len(alpha)),
        ("beta", len(beta), len(beta)),
    ]


def _empty_group_names(store) -> list[str]:
    return [label for _key, label, _cwd in store.empty_groups]


def test_kept_project_outlives_its_sessions(store, monkeypatch):
    monkeypatch.setattr(store_mod, "_trash_file", lambda path: None)
    beta = [s for s in store._last_sessions if s.project_name == "beta"]

    store.keep_projects(["beta"])
    # Still has sessions, so it's a real group — not an empty header as well.
    assert _empty_group_names(store) == []

    store.trash_many([s.session_id for s in beta])
    assert _by_project(store, "beta") == []  # every session gone
    assert _empty_group_names(store) == ["beta"]
    # The header keeps the folder, so "new session here" still works.
    assert store.empty_groups[0][2] == "/home/user/beta"
    assert "beta" in store.resolved_project_order

    store.forget_project("beta")
    assert _empty_group_names(store) == []
    assert not store.state.is_virtual_project("beta")


def test_kept_project_persists_and_respects_archiving(store, app_state, monkeypatch):
    monkeypatch.setattr(store_mod, "_trash_file", lambda path: None)
    store.keep_projects(["beta"])
    store.trash_many([s.session_id for s in store._last_sessions if s.project_name == "beta"])

    assert app_state.AppState().get_virtual_projects() == {"beta": "/home/user/beta"}

    store.set_project_archived("beta", True)
    assert _empty_group_names(store) == []
    store.set_show_archived(True)
    assert _empty_group_names(store) == ["beta"]


def test_trash_many_keeps_orphaned_forwards_archived(store, monkeypatch):
    monkeypatch.setattr(store_mod, "_trash_file", lambda path: None)
    original, fork = (s.session_id for s in store._last_sessions[:2])
    store.record_forward(original, fork)
    assert store.forward_state(store.sessions[original]) == "moved"  # row suppressed

    store.trash_many([fork])

    # The forward is stale now, so the original's row would pop back into view
    # after having been invisible all along; it stays archived instead.
    assert store.state.is_archived(original)


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
