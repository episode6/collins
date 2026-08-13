import json
import os
import re
import uuid

import pytest

from collins import chats as chats_mod
from collins import store as store_mod
from collins.models import CHATS_GROUP
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


def test_restore_many_undoes_an_archive(store):
    # What the archive snackbar's Undo (and Ctrl+Shift+Z) calls: the whole
    # last-archived batch comes back at once.
    ids = [s.session_id for s in store._last_sessions[:2]]
    store.archive_many(ids)
    assert sorted(s.session_id for s in store.archived_sessions()) == sorted(ids)

    store.restore_many(ids)
    assert store.archived_sessions() == []


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


def test_rows_representing_is_the_session_itself_without_a_forward(store):
    session_id = store._last_sessions[0].session_id
    assert store.rows_representing(session_id) == [session_id]
    assert store.rows_representing(str(uuid.uuid4())) == []  # no row anywhere


def test_rows_representing_covers_a_fork_with_no_row(store):
    original = store._last_sessions[0].session_id
    fork = str(uuid.uuid4())  # /bg fork still holding only a metadata stub

    store.record_forward(original, fork)

    # Nothing discoverable to scan, so the fork has no row of its own and the
    # forward reads as stale — the row it forked from goes on standing in for
    # it, and has to carry its status (the yellow "running detached" line).
    assert store.forward_state(store.sessions[original]) == ""
    assert store.rows_representing(fork) == [original]
    assert store.rows_representing(original) == [original]


def test_rows_representing_follows_a_chain_of_forks(store):
    original = store._last_sessions[0].session_id
    middle, latest = str(uuid.uuid4()), str(uuid.uuid4())

    store.record_forward(original, middle)
    store.record_forward(middle, latest)

    # Backgrounded repeatedly without the agent ever doing any work: only the
    # very first row exists, and it stands for every fork in the chain — the
    # middle one included. A second /bg runs its handoff under `middle`, and
    # `original` is the only row there is to disable while it's in flight and
    # to re-enable once `latest` is recorded.
    assert store.rows_representing(latest) == [original]
    assert store.rows_representing(middle) == [original]


def test_rows_representing_is_only_the_fork_once_it_has_a_row(store):
    original, fork = (s.session_id for s in store._last_sessions[:2])

    store.record_forward(original, fork)

    # The fork's own row took over and the original went out of sight, so the
    # fork stands for itself alone.
    assert store.forward_state(store.sessions[original]) == "moved"
    assert store.rows_representing(fork) == [fork]
    assert store.rows_representing(original) == []


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


def test_add_project_before_any_session(store, app_state):
    # The sidebar's "Add project" button: a directory no session has ever
    # run in lands as a persisted empty header with its folder attached.
    store.add_project("/home/user/gamma")

    assert _empty_group_names(store) == ["gamma"]
    assert store.empty_groups[0][2] == "/home/user/gamma"
    assert "gamma" in store.resolved_project_order
    assert app_state.AppState().get_virtual_projects() == {"gamma": "/home/user/gamma"}


def test_add_project_with_live_sessions_is_a_noop(store):
    # The folder chooser defaults to the visible project's own directory, so
    # "Add project" on a live project must not mark it virtual — the sidebar
    # reads is_virtual_project as "no sessions left", which is what puts
    # "Remove project from sidebar" in the project menu.
    store.add_project("/home/user/alpha")
    assert not store.state.is_virtual_project("alpha")
    assert _empty_group_names(store) == []


def test_add_project_worktree_maps_to_repository(store):
    # Adding a Claude-managed worktree is really adding its repository —
    # matching how sessions in one are grouped.
    store.add_project("/home/user/gamma/.claude/worktrees/wt")
    assert store.state.get_virtual_projects() == {"gamma": "/home/user/gamma"}


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


# -- the virtual Chats project ------------------------------------------------


@pytest.fixture
def chats_dir(tmp_path, monkeypatch):
    """Chats root isolated to the temp dir."""
    root = tmp_path / "chats"
    monkeypatch.setattr(chats_mod, "CHATS_DIR", root)
    return root


def _write_chat_session(projects_root, text="Quick chat", cwd=None):
    """A discoverable session whose transcript records a cwd inside the
    (monkeypatched) chats root."""
    from conftest import make_transcript_lines

    cwd = cwd or chats_mod.create_chat_dir()
    project_dir = projects_root / re.sub(r"[^A-Za-z0-9]", "-", cwd)
    project_dir.mkdir(parents=True, exist_ok=True)
    session_id = str(uuid.uuid4())
    lines = make_transcript_lines(cwd, text)
    (project_dir / f"{session_id}.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines), encoding="utf-8"
    )
    return session_id, cwd


@pytest.fixture
def store_with_chat(app_state, projects_dir, chats_dir):
    """The regular three-session store plus one chat session."""
    root, _ids = projects_dir
    session_id, cwd = _write_chat_session(root)
    store = store_mod.SessionStore(app_state.AppState())
    store._last_sessions = discover_sessions()
    store._apply()
    return store, session_id, cwd


def _items(store):
    return [store.model.get_item(i) for i in range(store.model.get_n_items())]


def test_chat_sessions_group_under_chats_key(store_with_chat):
    store, session_id, _cwd = store_with_chat
    item = next(i for i in _items(store) if i.session_id == session_id)
    assert item.group_key == CHATS_GROUP
    assert item.group_label == "Chats"
    assert store.group_counts[CHATS_GROUP] == 1


def test_chat_rows_ordered_after_favorites_before_projects(store_with_chat):
    store, session_id, _cwd = store_with_chat
    favorite = next(s for s in store._last_sessions if not chats_mod.is_chat_cwd(s.cwd))
    store.toggle_favorite(favorite.session_id)

    kinds = [item.group_key[0] for item in _items(store)]
    assert kinds == ["fav", "chats", "proj", "proj"]


def test_chat_sessions_excluded_from_resolved_project_order(store_with_chat):
    store, _session_id, cwd = store_with_chat
    assert os.path.basename(cwd) not in store.resolved_project_order
    assert sorted(store.resolved_project_order) == ["alpha", "beta"]


def test_move_project_does_not_persist_chat_names(store_with_chat):
    store, _session_id, cwd = store_with_chat
    store.move_project("beta", "alpha")
    order = store.state.get_project_order()
    assert os.path.basename(cwd) not in order
    assert order == ["beta", "alpha"]


def test_archived_chat_session_creates_no_empty_group(store_with_chat):
    store, session_id, cwd = store_with_chat
    store.set_archived(session_id, True)
    labels = [label for _key, label, _cwd in store.empty_groups]
    assert os.path.basename(cwd) not in labels
    assert "Chats" not in labels
    assert store.group_counts.get(CHATS_GROUP, 0) == 0


def test_archived_breakdown_labels_chats(store_with_chat):
    store, session_id, _cwd = store_with_chat
    store.set_archived(session_id, True)
    assert store.archived_breakdown() == [("Chats", 1, 1)]


def test_delete_chat_session_removes_its_dir(store_with_chat):
    store, session_id, cwd = store_with_chat
    assert store.delete(session_id) is None
    assert not os.path.exists(cwd)


def test_delete_chat_session_keeps_dir_shared_with_fork(app_state, projects_dir, chats_dir):
    root, _ids = projects_dir
    original, cwd = _write_chat_session(root)
    fork, _ = _write_chat_session(root, text="Fork of the chat", cwd=cwd)
    store = store_mod.SessionStore(app_state.AppState())
    store._last_sessions = discover_sessions()
    store._apply()

    assert store.delete(original) is None
    assert os.path.isdir(cwd)  # the fork still lives there
    assert store.delete(fork) is None
    assert not os.path.exists(cwd)  # last one out cleans up


def test_trash_chat_session_trashes_its_dir(store_with_chat, monkeypatch):
    store, session_id, cwd = store_with_chat
    monkeypatch.setattr(store_mod, "_trash_file", lambda path: None)
    trashed_dirs = []
    monkeypatch.setattr(chats_mod, "trash_chat_dir", lambda path: trashed_dirs.append(path))

    assert store.trash(session_id) is None
    assert trashed_dirs == [cwd]


def test_trash_chat_session_keeps_dir_shared_with_fork(app_state, projects_dir, chats_dir, monkeypatch):
    root, _ids = projects_dir
    original, cwd = _write_chat_session(root)
    _fork, _ = _write_chat_session(root, text="Fork of the chat", cwd=cwd)
    store = store_mod.SessionStore(app_state.AppState())
    store._last_sessions = discover_sessions()
    store._apply()

    monkeypatch.setattr(store_mod, "_trash_file", lambda path: None)
    trashed_dirs = []
    monkeypatch.setattr(chats_mod, "trash_chat_dir", lambda path: trashed_dirs.append(path))

    assert store.trash(original) is None
    assert trashed_dirs == []  # the fork still lives there


def test_emptied_projects_excludes_chats(store_with_chat):
    store, chat_id, _cwd = store_with_chat
    beta_ids = {s.session_id for s in store._last_sessions if s.project_name == "beta"}
    doomed = beta_ids | {chat_id}
    assert store_mod.emptied_projects(store.sessions.values(), doomed) == ["beta"]


def test_first_scan_sweeps_orphan_chat_dirs(app_state, projects_dir, chats_dir, monkeypatch):
    swept = []
    monkeypatch.setattr(chats_mod, "sweep_orphan_chat_dirs", lambda refs: swept.append(refs))
    monkeypatch.setattr(store_mod.SessionStore, "_setup_monitors", lambda self: None)
    monkeypatch.setattr(store_mod.SessionStore, "_request_titles", lambda self, sessions: None)

    store = store_mod.SessionStore(app_state.AppState())
    sessions = discover_sessions()
    store._on_scanned(sessions)
    assert swept == [{s.cwd for s in sessions if s.cwd}]

    store._on_scanned(sessions)  # later rescans don't sweep again
    assert len(swept) == 1


def test_manual_refresh_forces_a_rebuild_report(app_state, projects_dir, monkeypatch):
    """refresh(force_rebuild=True) must report an order change even when
    nothing moved: sidebar rows bake in on-disk state at construction time
    (project icons), and the refresh button is how users re-read it."""
    monkeypatch.setattr(chats_mod, "sweep_orphan_chat_dirs", lambda refs: None)
    monkeypatch.setattr(store_mod.SessionStore, "_setup_monitors", lambda self: None)
    monkeypatch.setattr(store_mod.SessionStore, "_request_titles", lambda self, sessions: None)

    # Run the scan inline: the threaded hand-off is not what's under test.
    class _InlineThread:
        def __init__(self, target, daemon=None):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(store_mod.threading, "Thread", _InlineThread)
    monkeypatch.setattr(store_mod.GLib, "idle_add", lambda fn, *a: fn(*a))

    store = store_mod.SessionStore(app_state.AppState())
    changes = []
    store.connect("refreshed", lambda _s, changed: changes.append(changed))

    store.refresh()  # first scan populates the model
    store.refresh()  # nothing changed
    store.refresh(force_rebuild=True)  # forced: reported anyway
    store.refresh()  # the force flag is one-shot
    assert changes == [True, False, True, False]


def _write_worktree_session(projects_root, text="Continue in the worktree"):
    """A discoverable session whose transcript records a Claude-managed
    worktree of the alpha project as its cwd — the shape /bg fork copies
    take after the agent moved into a worktree."""
    from conftest import make_transcript_lines

    cwd = "/home/user/alpha/.claude/worktrees/some-branch"
    project_dir = projects_root / re.sub(r"[^A-Za-z0-9]", "-", cwd)
    project_dir.mkdir(parents=True, exist_ok=True)
    session_id = str(uuid.uuid4())
    lines = make_transcript_lines(cwd, text)
    (project_dir / f"{session_id}.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines), encoding="utf-8"
    )
    return session_id, cwd


def test_worktree_sessions_group_under_their_repository(store, projects_dir):
    root, _ids = projects_dir
    session_id, _cwd = _write_worktree_session(root)
    store._last_sessions = discover_sessions()
    store._apply()

    item = next(i for i in _items(store) if i.session_id == session_id)
    assert item.group_key == ("proj", "alpha")
    assert "some-branch" not in store.resolved_project_order


def test_bg_fork_into_a_worktree_stays_in_the_original_project(store, projects_dir):
    root, ids = projects_dir
    fork_id, _cwd = _write_worktree_session(root)
    store._last_sessions = discover_sessions()
    store._apply()
    original = ids["alpha1"]
    store.record_forward(original, fork_id)

    # The fork's row takes the original's place *in the same group* — the
    # session must not vanish from the project and resurface under a phantom
    # project named after the worktree directory.
    assert store.forward_state(store.sessions[original]) == "moved"
    assert store.get_item(original) is None
    item = store.get_item(fork_id)
    assert item is not None
    assert item.group_key == ("proj", "alpha")


def test_project_cwd_answers_with_the_repository_not_the_worktree(store, projects_dir):
    root, _ids = projects_dir
    _write_worktree_session(root)
    store._last_sessions = discover_sessions()
    store._apply()

    assert store.project_cwd("alpha") == "/home/user/alpha"


def test_adopt_worktree_archives_converts_phantom_project_archives(store, projects_dir):
    root, _ids = projects_dir
    session_id, _cwd = _write_worktree_session(root)
    # Pre-fix state: worktree sessions grouped under a phantom project named
    # after the worktree directory, and the user archived that whole project
    # to get its rows out of the sidebar.
    store.state.set_project_archived("some-branch", True)

    store._last_sessions = discover_sessions()
    store._adopt_worktree_archives()
    store._apply()

    # The session stays hidden — as its own archive now — and the phantom
    # project entry, which no longer matches any group, is dropped.
    assert store.state.is_archived(session_id)
    assert not store.state.is_project_archived("some-branch")
    assert store.get_item(session_id) is None


def test_replaced_rows_are_labeled_replaced_in_the_archive_view(store):
    original, fork = (s.session_id for s in store._last_sessions[:2])
    store.record_forward(original, fork)
    store.set_show_archived(True)

    item = store.get_item(original)
    assert item is not None
    assert item.subtitle == "replaced"


def test_can_background_is_off_until_a_row_is_told_otherwise(store):
    session_id = store._last_sessions[0].session_id
    item = store.get_item(session_id)
    # Rows start closed: a session with no open tab has nothing to hand over.
    assert item.can_background is False

    store.set_can_background(session_id, True)
    assert item.can_background is True

    store.set_can_background(session_id, False)
    assert item.can_background is False


def test_set_can_background_ignores_a_session_with_no_row(store):
    store.set_can_background(str(uuid.uuid4()), True)  # must not raise


def test_unread_survives_a_store_refresh(store):
    session_id = store._last_sessions[0].session_id
    item = store.get_item(session_id)
    assert item.unread is False

    store.set_unread(session_id, True)
    assert item.unread is True

    # A rescan updates items in place rather than rebuilding them, so the
    # flag (and the blue line it paints) rides out unrelated refreshes.
    store._apply()
    assert store.get_item(session_id) is item
    assert item.unread is True

    store.set_unread(session_id, False)
    assert item.unread is False


def test_set_unread_ignores_a_session_with_no_row(store):
    store.set_unread(str(uuid.uuid4()), True)  # must not raise


def test_row_ids_covers_every_row_so_the_gate_can_sweep(store):
    # The background gate is app-wide — one handoff at a time — so closing it
    # means re-evaluating every row, not just the one being handed over.
    assert sorted(store.row_ids()) == sorted(s.session_id for s in store._last_sessions)


def test_rows_representing_holds_a_mid_chain_fork_during_a_second_handoff(store):
    original = store._last_sessions[0].session_id
    first_fork, second_fork = str(uuid.uuid4()), str(uuid.uuid4())
    store.record_forward(original, first_fork)

    # The tab attached to first_fork is backgrounded again: the /bg is fed and
    # marked under first_fork, then second_fork's id lands and is recorded.
    # first_fork must keep resolving to a row across that, or the handoff has
    # nothing to disable while it runs and nothing to re-enable afterwards.
    assert store.rows_representing(first_fork) == [original]
    store.record_forward(first_fork, second_fork)
    assert store.rows_representing(first_fork) == [original]
    assert store.rows_representing(second_fork) == [original]


# -- apply_pr_title (the pr_title_sessions setting) -------------------------


def _titled_pr(number, title=None):
    record = {"number": number, "url": f"https://github.com/episode6/collins/pull/{number}"}
    if title:
        record["title"] = title
    return record


def test_apply_pr_title_is_off_by_default(store):
    session_id = store._last_sessions[0].session_id
    store.state.set_session_prs(session_id, [_titled_pr(55, "Add the thing")])
    store.apply_pr_title(session_id)
    assert store.state.get_generated_name(session_id) is None


def test_apply_pr_title_renames_to_the_newest_titled_pr(store):
    store.state.set_setting("pr_title_sessions", True)
    session = store._last_sessions[0]
    store.state.set_session_prs(
        session.session_id, [_titled_pr(40, "Old work"), _titled_pr(55, "Add the thing")]
    )
    store.apply_pr_title(session.session_id)
    assert store.state.get_generated_name(session.session_id) == "Add the thing"
    assert store.display_name(session) == "Add the thing"


def test_apply_pr_title_waits_for_a_title(store):
    """A bare pr-link record has no title until gh answers; nothing to do yet."""
    store.state.set_setting("pr_title_sessions", True)
    session_id = store._last_sessions[0].session_id
    store.state.set_generated_name(session_id, "Auto title")
    store.state.set_session_prs(session_id, [_titled_pr(55)])
    store.apply_pr_title(session_id)
    assert store.state.get_generated_name(session_id) == "Auto title"


def test_apply_pr_title_loses_to_a_manual_rename(store):
    store.state.set_setting("pr_title_sessions", True)
    session = store._last_sessions[0]
    store.rename(session.session_id, "My name")
    store.state.set_session_prs(session.session_id, [_titled_pr(55, "Add the thing")])
    store.apply_pr_title(session.session_id)
    # The generated slot is written, but the manual name keeps winning.
    assert store.state.get_generated_name(session.session_id) == "Add the thing"
    assert store.display_name(session) == "My name"


def test_apply_pr_titles_sweeps_saved_prs_when_switched_on(store):
    """Turning the setting on retitles sessions whose PRs are already saved."""
    first, second = store._last_sessions[0], store._last_sessions[1]
    store.state.set_session_prs(first.session_id, [_titled_pr(55, "Add the thing")])
    store.state.set_session_prs(second.session_id, [_titled_pr(56)])  # no title yet
    store.apply_pr_titles()  # setting still off: a no-op
    assert store.state.get_generated_name(first.session_id) is None

    store.state.set_setting("pr_title_sessions", True)
    store.apply_pr_titles()
    assert store.state.get_generated_name(first.session_id) == "Add the thing"
    assert store.state.get_generated_name(second.session_id) is None


def test_apply_pr_titles_sweeps_only_on_the_off_to_on_flip(store):
    """Preferences apply calls the sweep on every save; only the save that
    flips the setting on walks the sessions — steady-state names are the
    per-detection apply_pr_title's job."""
    first = store._last_sessions[0]
    store.state.set_setting("pr_title_sessions", True)
    store.apply_pr_titles()  # off at construction, on now: the flip

    # A list saved behind the sweep's back: a later steady-state apply (the
    # user changed the font, say) must not pick it up.
    store.state.set_session_prs(first.session_id, [_titled_pr(55, "Add the thing")])
    store.apply_pr_titles()
    assert store.state.get_generated_name(first.session_id) is None

    # Off and back on is a fresh flip, and a fresh catch-up.
    store.state.set_setting("pr_title_sessions", False)
    store.apply_pr_titles()
    store.state.set_setting("pr_title_sessions", True)
    store.apply_pr_titles()
    assert store.state.get_generated_name(first.session_id) == "Add the thing"


def test_cli_title_display_precedence(store):
    """The CLI's own name shows only while cli_title_sessions is on, beats
    the generated slot, and loses to a manual rename."""
    session = store._last_sessions[0]
    sid = session.session_id
    store.state.set_cli_titles({sid: "cli name"})

    assert store.display_name(session) != "cli name"  # setting off: ignored
    store.state.set_setting("cli_title_sessions", True)
    assert store.display_name(session) == "cli name"

    store.state.set_generated_name(sid, "generated name")
    assert store.display_name(session) == "cli name"
    store.rename(sid, "manual name")
    assert store.display_name(session) == "manual name"

    store.state.set_setting("cli_title_sessions", False)
    store.state.set_name(sid, "")
    assert store.display_name(session) == "generated name"


def test_record_cli_titles_survives_the_tail_window(store):
    """A title observed once sticks: a scan whose tail window no longer holds
    the title records (cli_title == "") must not clear the recorded name."""
    session = store._last_sessions[0]
    sid = session.session_id

    session.cli_title = "seen once"
    store._record_cli_titles(store._last_sessions)
    assert store.state.get_cli_title(sid) == "seen once"

    session.cli_title = ""  # scrolled out of the 64KB tail
    store._record_cli_titles(store._last_sessions)
    assert store.state.get_cli_title(sid) == "seen once"

    session.cli_title = "renamed later"
    store._record_cli_titles(store._last_sessions)
    assert store.state.get_cli_title(sid) == "renamed later"


def test_apply_cli_titles_reprojects_only_on_a_flip(store, monkeypatch):
    """Preferences apply calls this on every save; only a save that moved the
    cli_title_sessions switch (either direction — off reverts the adopted
    names) re-projects the session list."""
    applies = []
    orig = store._apply
    monkeypatch.setattr(store, "_apply", lambda: (applies.append(1), orig())[1])

    store.apply_cli_titles()  # nothing moved: no work
    assert not applies

    store.state.set_setting("cli_title_sessions", True)
    store.apply_cli_titles()
    assert len(applies) == 1
    store.apply_cli_titles()  # steady state: no work
    assert len(applies) == 1

    store.state.set_setting("cli_title_sessions", False)
    store.apply_cli_titles()  # switching off is a change too
    assert len(applies) == 2


def test_cli_titled_sessions_skip_auto_titling(store, monkeypatch):
    """No claude -p run for a session whose display name already comes from
    the CLI's own title — the generated name would be shadowed anyway."""
    submitted = []
    monkeypatch.setattr(
        store._titles, "submit", lambda sid, *a, **k: submitted.append(sid)
    )
    store.state.set_setting("auto_title_sessions", True)
    store.state.set_setting("cli_title_sessions", True)
    titled = store._last_sessions[0].session_id
    store.state.set_cli_titles({titled: "cli name"})

    store._request_titles(store._last_sessions)
    assert titled not in submitted
    assert len(submitted) == len(store._last_sessions) - 1

    # With the switch off the CLI title no longer shows, so titling resumes.
    store.state.set_setting("cli_title_sessions", False)
    submitted.clear()
    store._request_titles(store._last_sessions)
    assert titled in submitted
