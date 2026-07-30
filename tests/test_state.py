# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-30. Full change history: git log for this file.

import json

from collins.state import clamp_window_size, merge_project_order, move_in_order


def test_roundtrip(app_state):
    state = app_state.AppState()
    state.set_name("sid-1", "My session")
    state.toggle_favorite("sid-1")
    state.set_archived("sid-2", True)
    state.set_setting("scrollback", 5000)

    fresh = app_state.AppState()
    assert fresh.get_name("sid-1") == "My session"
    assert fresh.is_favorite("sid-1")
    assert fresh.is_archived("sid-2")
    assert fresh.get_setting("scrollback") == 5000


def test_archived_carries_over_from_pre_rename_hidden_keys(app_state):
    # A state.json written before the archive rename used the "hidden"
    # spellings: they load into the archived sets, and the next save rewrites
    # them under the new keys.
    app_state._CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    app_state._STATE_FILE.write_text(
        json.dumps({"hidden": ["sid-1"], "hidden_projects": ["alpha"]}),
        encoding="utf-8",
    )

    state = app_state.AppState()
    assert state.is_archived("sid-1")
    assert state.is_project_archived("alpha")

    state.save()
    data = json.loads(app_state._STATE_FILE.read_text(encoding="utf-8"))
    assert data["archived"] == ["sid-1"]
    assert data["archived_projects"] == ["alpha"]
    assert "hidden" not in data
    assert "hidden_projects" not in data


def test_virtual_projects_roundtrip(app_state):
    state = app_state.AppState()
    state.keep_virtual_projects({"alpha": "/home/user/alpha", "beta": ""})
    fresh = app_state.AppState()
    assert fresh.get_virtual_projects() == {"alpha": "/home/user/alpha", "beta": ""}
    assert fresh.is_virtual_project("beta")

    fresh.forget_virtual_project("beta")
    assert app_state.AppState().get_virtual_projects() == {"alpha": "/home/user/alpha"}


def test_generated_names_roundtrip(app_state):
    state = app_state.AppState()
    state.set_generated_name("sid", "Fix login bug")
    assert app_state.AppState().get_generated_name("sid") == "Fix login bug"
    state.set_generated_name("sid", "  ")
    assert app_state.AppState().get_generated_name("sid") is None


def test_set_generated_names_bulk(app_state):
    state = app_state.AppState()
    state.set_generated_names({"sid-1": "One", "sid-2": "Two", "sid-3": "  "})
    fresh = app_state.AppState()
    assert fresh.get_generated_name("sid-1") == "One"
    assert fresh.get_generated_name("sid-2") == "Two"
    assert fresh.get_generated_name("sid-3") is None


def test_clearing_name_removes_entry(app_state):
    state = app_state.AppState()
    state.set_name("sid-1", "Name")
    state.set_name("sid-1", "   ")
    assert app_state.AppState().get_name("sid-1") is None


def test_toggle_favorite_returns_new_state(app_state):
    state = app_state.AppState()
    assert state.toggle_favorite("sid") is True
    assert state.toggle_favorite("sid") is False


def test_defaults_for_unknown_settings(app_state):
    state = app_state.AppState()
    assert state.get_setting("color_scheme") == "system"
    assert state.get_setting("font") == ""


def test_caffeine_on_launch_setting(app_state):
    state = app_state.AppState()
    assert state.get_setting("caffeine_on_launch") is False  # opt-in only
    state.set_setting("caffeine_on_launch", True)
    assert app_state.AppState().get_setting("caffeine_on_launch") is True


def test_footer_apps_setting_roundtrip(app_state):
    state = app_state.AppState()
    assert state.get_setting("footer_apps") == []  # default: no extra buttons
    state.set_setting("footer_apps", ["org.gnome.Nautilus.desktop", "code.desktop"])
    fresh = app_state.AppState()
    assert fresh.get_setting("footer_apps") == ["org.gnome.Nautilus.desktop", "code.desktop"]
    # The default list object must not have been mutated by the roundtrip.
    assert app_state.DEFAULT_SETTINGS["footer_apps"] == []


def test_corrupt_state_file_recovers(app_state):
    state = app_state.AppState()
    state.set_name("sid", "x")  # creates the file
    app_state._STATE_FILE.write_text("{corrupt", encoding="utf-8")
    fresh = app_state.AppState()  # must not raise
    assert fresh.get_name("sid") is None


def test_migrates_old_config_dir(app_state):
    # state.json in the pre-rebrand dir is carried over to the new dir.
    old_dir = app_state._OLD_CONFIG_DIRS[0]
    old_dir.mkdir(parents=True, exist_ok=True)
    (old_dir / "state.json").write_text(
        json.dumps({"names": {"sid": "Carried over"}, "favorites": ["sid"]}),
        encoding="utf-8",
    )
    state = app_state.AppState()
    assert state.get_name("sid") == "Carried over"
    assert state.is_favorite("sid")
    assert app_state._STATE_FILE.exists()  # copied into the new location


def test_migration_leaves_old_config_untouched(app_state):
    # Copy-only migration: the old app must keep its state so both apps can
    # run side by side.
    old_dir = app_state._OLD_CONFIG_DIRS[0]
    old_dir.mkdir(parents=True, exist_ok=True)
    old_file = old_dir / "state.json"
    original = json.dumps({"names": {"sid": "Carried over"}})
    old_file.write_text(original, encoding="utf-8")
    state = app_state.AppState()
    state.set_name("sid", "Renamed in Collins")  # persists via save()
    assert old_file.read_text(encoding="utf-8") == original  # never modified
    assert app_state.AppState().get_name("sid") == "Renamed in Collins"


def test_migrates_oldest_config_dir(app_state):
    # A state.json two renames back still migrates, and the newer dir wins.
    oldest_dir = app_state._OLD_CONFIG_DIRS[1]
    oldest_dir.mkdir(parents=True, exist_ok=True)
    (oldest_dir / "state.json").write_text(
        json.dumps({"names": {"sid": "From oldest"}}), encoding="utf-8"
    )
    state = app_state.AppState()
    assert state.get_name("sid") == "From oldest"


def test_panel_size_settings_roundtrip(app_state):
    state = app_state.AppState()
    assert state.get_setting("panel_size_bottom") == 0  # unset → fraction default
    assert state.get_setting("panel_size_right") == 0
    state.set_setting("panel_size_bottom", 420)
    state.set_setting("panel_size_right", 512)
    fresh = app_state.AppState()
    assert fresh.get_setting("panel_size_bottom") == 420
    assert fresh.get_setting("panel_size_right") == 512


def test_panel_state_roundtrip(app_state):
    state = app_state.AppState()
    snap = {"open": True, "mode": "right", "sizes": {"right": 300, "bottom": 240}}
    state.set_panel_state("sid", snap)
    fresh = app_state.AppState()
    assert fresh.get_panel_state("sid") == snap
    assert fresh.get_panel_state("other") is None


def test_panel_state_none_removes_entry(app_state):
    state = app_state.AppState()
    state.set_panel_state("sid", {"open": False, "mode": "bottom"})
    state.set_panel_state("sid", None)
    assert app_state.AppState().get_panel_state("sid") is None


def test_panel_state_unchanged_is_not_rewritten(app_state):
    state = app_state.AppState()
    snap = {"open": True, "mode": "bottom", "sizes": {"bottom": 200}}
    state.set_panel_state("sid", snap)
    app_state._STATE_FILE.unlink()  # a redundant save would recreate the file
    state.set_panel_state("sid", dict(snap))  # identical snapshot
    state.set_panel_state("absent", None)  # removing a missing entry
    assert not app_state._STATE_FILE.exists()


def test_panel_state_ignores_corrupt_entries(app_state):
    state = app_state.AppState()
    state.set_panel_state("good", {"open": True, "mode": "bottom"})
    data = json.loads(app_state._STATE_FILE.read_text(encoding="utf-8"))
    data["panel_states"]["bad"] = "not-a-dict"
    app_state._STATE_FILE.write_text(json.dumps(data), encoding="utf-8")
    fresh = app_state.AppState()
    assert fresh.get_panel_state("good") == {"open": True, "mode": "bottom"}
    assert fresh.get_panel_state("bad") is None


def _pr(number):
    return {"number": number, "url": f"https://github.com/episode6/collins/pull/{number}"}


def test_session_prs_roundtrip(app_state):
    state = app_state.AppState()
    state.set_session_prs("sid", [_pr(40), _pr(55)])
    fresh = app_state.AppState()
    assert fresh.get_session_prs("sid") == [_pr(40), _pr(55)]  # oldest first, never sorted
    assert fresh.get_session_prs("other") == []


def test_session_prs_empty_removes_entry(app_state):
    state = app_state.AppState()
    state.set_session_prs("sid", [_pr(55)])
    state.set_session_prs("sid", [])
    assert app_state.AppState().get_session_prs("sid") == []


def test_session_prs_unchanged_are_not_rewritten(app_state):
    """Every tab re-derives its list on each transcript poll; a redundant write
    per poll would be a state.json rewrite per second, per tab."""
    state = app_state.AppState()
    prs = [_pr(55)]
    state.set_session_prs("sid", prs)
    app_state._STATE_FILE.unlink()  # a redundant save would recreate the file
    state.set_session_prs("sid", [dict(_pr(55))])  # identical list
    state.set_session_prs("absent", [])  # clearing a session that has none
    state.set_session_prs("", [_pr(61)])  # a tab whose session isn't resolved yet
    assert not app_state._STATE_FILE.exists()


def test_session_prs_ignore_corrupt_entries(app_state):
    state = app_state.AppState()
    state.set_session_prs("good", [_pr(55)])
    data = json.loads(app_state._STATE_FILE.read_text(encoding="utf-8"))
    data["session_prs"]["bad"] = "not-a-list"
    app_state._STATE_FILE.write_text(json.dumps(data), encoding="utf-8")
    fresh = app_state.AppState()
    assert fresh.get_session_prs("good") == [_pr(55)]
    assert fresh.get_session_prs("bad") == []


def test_forwarding_a_session_carries_its_prs(app_state):
    """A /bg fork continues the same conversation: the PRs it opened are its
    own, and the fork's transcript never repeats their pr-links."""
    state = app_state.AppState()
    state.set_session_prs("old", [_pr(55)])
    state.forward_session("old", "new")
    assert app_state.AppState().get_session_prs("new") == [_pr(55)]


def test_forwarding_does_not_clobber_the_forks_own_prs(app_state):
    state = app_state.AppState()
    state.set_session_prs("old", [_pr(55)])
    state.set_session_prs("new", [_pr(61)])
    state.forward_session("old", "new")
    assert app_state.AppState().get_session_prs("new") == [_pr(61)]


def test_last_active_session_roundtrip(app_state):
    state = app_state.AppState()
    assert state.get_setting("last_active_session") == ""  # default: nothing to reopen
    state.set_setting("last_active_session", "sid-42")
    assert app_state.AppState().get_setting("last_active_session") == "sid-42"


def test_window_geometry_roundtrip(app_state):
    state = app_state.AppState()
    state.update_settings({"window_width": 1600, "window_height": 900, "window_maximized": True})
    fresh = app_state.AppState()
    assert fresh.get_setting("window_width") == 1600
    assert fresh.get_setting("window_height") == 900
    assert fresh.get_setting("window_maximized") is True


def test_window_geometry_defaults(app_state):
    state = app_state.AppState()
    assert state.get_setting("window_width") == 1280
    assert state.get_setting("window_height") == 800
    assert state.get_setting("window_maximized") is False


def test_clamp_window_size_fits_unchanged():
    assert clamp_window_size(1280, 800, [(1920, 1080)]) == (1280, 800)


def test_clamp_window_size_shrinks_to_largest_monitor():
    assert clamp_window_size(3000, 2000, [(1280, 720), (1920, 1080)]) == (1920, 1080)


def test_clamp_window_size_clamps_each_dimension_independently():
    assert clamp_window_size(2500, 900, [(1920, 1080)]) == (1920, 900)


def test_clamp_window_size_without_monitors_leaves_size_alone():
    assert clamp_window_size(3000, 2000, []) == (3000, 2000)


def test_clamp_window_size_enforces_minimum():
    assert clamp_window_size(10, 10, [(1920, 1080)]) == (640, 480)


def test_project_order_roundtrip(app_state):
    state = app_state.AppState()
    state.set_project_order(["zeta", "alpha", "mid"])
    fresh = app_state.AppState()
    assert fresh.get_project_order() == ["zeta", "alpha", "mid"]  # order preserved, not sorted


def test_expanded_groups_roundtrip(app_state):
    state = app_state.AppState()
    state.set_group_expanded("proj:alpha", True)
    state.set_groups_expanded(["proj:beta", "fav:"], True)
    state.set_group_expanded("proj:alpha", False)
    fresh = app_state.AppState()
    assert not fresh.is_group_expanded("proj:alpha")
    assert fresh.is_group_expanded("proj:beta")
    assert fresh.is_group_expanded("fav:")


def test_merge_project_order_keeps_saved_order_and_appends_new_alphabetically():
    assert merge_project_order(["zeta", "alpha"], ["alpha", "Beta", "gamma", "zeta"]) == [
        "zeta",
        "alpha",
        "Beta",
        "gamma",
    ]


def test_merge_project_order_drops_stale_names():
    assert merge_project_order(["gone", "kept"], ["kept"]) == ["kept"]


def test_merge_project_order_empty_saved_is_alphabetical():
    assert merge_project_order([], ["b", "A", "c"]) == ["A", "b", "c"]


def test_move_in_order_before_and_to_end():
    assert move_in_order(["a", "b", "c"], "c", "a") == ["c", "a", "b"]
    assert move_in_order(["a", "b", "c"], "a", None) == ["b", "c", "a"]
    assert move_in_order(["a", "b"], "new", "b") == ["a", "new", "b"]
    assert move_in_order(["a", "b"], "a", "gone") == ["b", "a"]  # unknown anchor → end


def test_forward_session_carries_metadata_without_archiving_original(app_state):
    state = app_state.AppState()
    state.set_name("old", "My task")
    state.set_emoji("old", "🚀")
    state.toggle_favorite("old")
    state.set_panel_state("old", {"open": True, "mode": "bottom"})
    state.forward_session("old", "new")

    fresh = app_state.AppState()
    assert fresh.resolve_forward("old") == "new"
    assert fresh.get_name("new") == "My task"
    assert fresh.get_emoji("new") == "🚀"
    assert fresh.is_favorite("new")
    assert fresh.get_panel_state("new") == {"open": True, "mode": "bottom"}
    # The stale original is NOT flagged archived here: the store derives its
    # row's fate from the forward (visible-but-disabled until the fork is
    # discovered, replaced afterwards).
    assert not fresh.is_archived("old")
    assert not fresh.is_archived("new")


def test_forward_session_never_clobbers_target_metadata(app_state):
    state = app_state.AppState()
    state.set_name("old", "Old name")
    state.set_name("new", "New name")
    state.forward_session("old", "new")
    assert state.get_name("new") == "New name"


def test_forward_session_ignores_degenerate_ids(app_state):
    state = app_state.AppState()
    state.forward_session("x", "x")
    state.forward_session("", "y")
    state.forward_session("x", "")
    assert state.session_forwards == {}


def test_forward_session_appends_instead_of_dropping_a_live_fork(app_state):
    state = app_state.AppState()
    state.forward_session("old", "first")
    # Backgrounding "old" again (its row still stands for the conversation)
    # must not overwrite the first forward: that agent may still be running,
    # and the forward is the only record of which row belongs to it.
    state.forward_session("old", "second")

    assert state.session_forwards == {"old": "first", "first": "second"}
    assert state.resolve_forward("old") == "second"
    assert state.resolve_forward("first") == "second"

    # Re-recording the id already at the end of the chain is a no-op, so a
    # replayed confirmation can't build a cycle out of it.
    state.forward_session("old", "second")
    assert state.session_forwards == {"old": "first", "first": "second"}


def test_pending_detaches_survive_a_restart(app_state):
    state = app_state.AppState()
    state.set_pending_detach("old", provider="claude", cwd="/proj", uuid="u-1")

    # The app can close between feeding /bg and the CLI listing the agent; the
    # evidence for the pairing has to outlive the process that gathered it.
    fresh = app_state.AppState()
    assert fresh.get_pending_detaches() == {
        "old": {"provider": "claude", "cwd": "/proj", "uuid": "u-1"}
    }

    fresh.clear_pending_detach("old")
    assert app_state.AppState().get_pending_detaches() == {}


def test_resolve_forward_follows_chains_and_survives_cycles(app_state):
    state = app_state.AppState()
    assert state.resolve_forward("solo") == "solo"
    state.forward_session("a", "b")
    state.forward_session("b", "c")
    assert state.resolve_forward("a") == "c"
    # A cycle (corrupt state) must terminate, not hang.
    state.session_forwards["c"] = "a"
    assert state.resolve_forward("a") in {"a", "b", "c"}


def test_migrates_legacy_names_file(app_state):
    app_state._LEGACY_NAMES_FILE.parent.mkdir(parents=True, exist_ok=True)
    app_state._LEGACY_NAMES_FILE.write_text(json.dumps({"old-sid": "Old name"}), encoding="utf-8")
    state = app_state.AppState()
    assert state.get_name("old-sid") == "Old name"
