# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-27. Full change history: git log for this file.

import json

from collins.claudemodels import NO_MODEL
from collins.state import (
    clamp_window_size,
    editor_pops_out,
    merge_project_order,
    move_in_order,
    panel_size_key,
)


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


def test_set_cli_titles_roundtrip(app_state):
    state = app_state.AppState()
    state.set_cli_titles({"sid-1": "composer entry", "sid-2": "  ", "sid-3": "fix login"})
    fresh = app_state.AppState()
    assert fresh.get_cli_title("sid-1") == "composer entry"
    assert fresh.get_cli_title("sid-2") is None
    assert fresh.get_cli_title("sid-3") == "fix login"


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


def test_pr_title_sessions_setting(app_state):
    state = app_state.AppState()
    assert state.get_setting("pr_title_sessions") is False  # opt-in only
    state.set_setting("pr_title_sessions", True)
    assert app_state.AppState().get_setting("pr_title_sessions") is True


def test_caffeine_keep_screen_on_setting(app_state):
    state = app_state.AppState()
    # Default matches how Caffeine Mode behaved before the setting existed:
    # the screen stays lit along with the computer.
    assert state.get_setting("caffeine_keep_screen_on") is True
    state.set_setting("caffeine_keep_screen_on", False)
    assert app_state.AppState().get_setting("caffeine_keep_screen_on") is False


def test_caffeine_on_launch_setting(app_state):
    state = app_state.AppState()
    assert state.get_setting("caffeine_on_launch") is False  # opt-in only
    state.set_setting("caffeine_on_launch", True)
    assert app_state.AppState().get_setting("caffeine_on_launch") is True


def test_caffeine_launch_timer_setting(app_state):
    from collins.caffeine import DURATION_KEYS, duration_seconds, follows_activity

    state = app_state.AppState()
    # Default follows the sessions ("Until idle"): on at launch means on while
    # there's work, not on until told otherwise.
    assert follows_activity(state.get_setting("caffeine_launch_timer"))
    assert duration_seconds(state.get_setting("caffeine_launch_timer")) is None
    state.set_setting("caffeine_launch_timer", "3h")
    saved = app_state.AppState().get_setting("caffeine_launch_timer")
    assert saved in DURATION_KEYS
    assert duration_seconds(saved) == 10800


def test_caffeine_launch_timer_can_follow_the_sessions(app_state):
    from collins.caffeine import WHILE_ACTIVE, follows_activity

    state = app_state.AppState()
    state.set_setting("caffeine_launch_timer", WHILE_ACTIVE)
    # It has to survive the round trip as the key itself: the app reads the
    # saved string back to decide between a clock and the sessions.
    assert follows_activity(app_state.AppState().get_setting("caffeine_launch_timer"))


def test_caffeine_idle_grace_setting(app_state):
    from collins.caffeine import ACTIVE_GRACE_S, grace_seconds

    state = app_state.AppState()
    # The default reproduces the grace as it was before it became a setting.
    assert grace_seconds(state.get_setting("caffeine_idle_grace_minutes")) == ACTIVE_GRACE_S
    state.set_setting("caffeine_idle_grace_minutes", 15)
    # The app reads the saved minutes back through the same sanitizer.
    assert grace_seconds(app_state.AppState().get_setting("caffeine_idle_grace_minutes")) == 900


def test_running_session_behavior_settings(app_state):
    state = app_state.AppState()
    # Both default to today's behaviour: the confirmation dialog asks.
    assert state.get_setting("archive_running_session") == "ask"
    assert state.get_setting("quit_with_running_sessions") == "ask"
    state.set_setting("archive_running_session", "background")
    state.set_setting("quit_with_running_sessions", "exit")
    fresh = app_state.AppState()
    assert fresh.get_setting("archive_running_session") == "background"
    assert fresh.get_setting("quit_with_running_sessions") == "exit"


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


def test_page_panel_size_settings_roundtrip(app_state):
    state = app_state.AppState()
    assert state.get_setting("page_panel_size_bottom") == 0
    assert state.get_setting("page_panel_size_right") == 0
    state.set_setting("page_panel_size_right", 640)
    fresh = app_state.AppState()
    assert fresh.get_setting("page_panel_size_right") == 640
    # Sizing the docked-page strip leaves the shells' panel alone.
    assert fresh.get_setting("panel_size_right") == 0


def test_panel_size_key_scopes(app_state):
    # The shells' panel keeps the original key names, so a size saved
    # before docked pages had their own still lands.
    assert panel_size_key("home", "bottom") == "panel_size_bottom"
    assert panel_size_key("home", "right") == "panel_size_right"
    assert panel_size_key("page", "bottom") == "page_panel_size_bottom"
    assert panel_size_key("page", "right") == "page_panel_size_right"
    # Every key it can name has a default, so an unset one reads as 0.
    for scope in ("home", "page"):
        for mode in ("bottom", "right"):
            assert panel_size_key(scope, mode) in app_state.DEFAULT_SETTINGS


_LAYOUT = {
    "mode": "right",
    "sizes": {"right": 300, "bottom": 240},
    "tree": {
        "split": "h",
        "size": 300,
        "managed": "b",
        "a": {"terminal": True},
        "b": {
            "strip": {
                "open": True,
                "home": True,
                "selected": 0,
                "pages": [{"kind": "shell", "hist": 0}],
            }
        },
    },
}


def test_panel_layout_roundtrip(app_state):
    state = app_state.AppState()
    state.set_panel_layout("sid", _LAYOUT)
    fresh = app_state.AppState()
    assert fresh.get_panel_layout("sid") == _LAYOUT
    assert fresh.get_panel_layout("other") is None


def test_panel_layout_none_removes_entry(app_state):
    state = app_state.AppState()
    state.set_panel_layout("sid", {"mode": "bottom"})
    state.set_panel_layout("sid", None)
    assert app_state.AppState().get_panel_layout("sid") is None


def test_panel_layout_unchanged_is_not_rewritten(app_state):
    state = app_state.AppState()
    state.set_panel_layout("sid", _LAYOUT)
    app_state._STATE_FILE.unlink()  # a redundant save would recreate the file
    state.set_panel_layout("sid", json.loads(json.dumps(_LAYOUT)))  # identical layout
    state.set_panel_layout("absent", None)  # removing a missing entry
    assert not app_state._STATE_FILE.exists()


def test_panel_layout_ignores_corrupt_entries(app_state):
    state = app_state.AppState()
    state.set_panel_layout("good", {"mode": "bottom"})
    data = json.loads(app_state._STATE_FILE.read_text(encoding="utf-8"))
    data["panel_layout"]["bad"] = "not-a-dict"
    app_state._STATE_FILE.write_text(json.dumps(data), encoding="utf-8")
    fresh = app_state.AppState()
    assert fresh.get_panel_layout("good") == {"mode": "bottom"}
    assert fresh.get_panel_layout("bad") is None


def test_panel_states_migrate_to_layouts(app_state):
    # A pre-tree state.json: the open panel becomes the two-node tree for
    # its mode with one shell page per history file on disk, and the old
    # key is dropped on the next save.
    import collins.panelhistory as panelhistory

    panelhistory.save_all("open-sid", {0: "one", 2: "three"})
    app_state._CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    app_state._STATE_FILE.write_text(
        json.dumps(
            {
                "panel_states": {
                    "open-sid": {"open": True, "mode": "bottom", "sizes": {"bottom": 300}},
                    "closed-sid": {"open": False, "mode": "right", "sizes": {"right": 512}},
                    "bad-sid": "not-a-dict",
                }
            }
        ),
        encoding="utf-8",
    )
    state = app_state.AppState()
    migrated = state.get_panel_layout("open-sid")
    assert migrated["mode"] == "bottom"
    assert migrated["tree"]["split"] == "v"
    assert migrated["tree"]["b"]["strip"]["pages"] == [
        {"kind": "shell", "hist": 0},
        {"kind": "shell", "hist": 2},
    ]
    # A closed panel keeps only its mode/size memory: nothing spawns until
    # Ctrl+J, exactly the old behavior.
    assert state.get_panel_layout("closed-sid") == {"mode": "right", "sizes": {"right": 512}}
    assert state.get_panel_layout("bad-sid") is None
    state.save()
    data = json.loads(app_state._STATE_FILE.read_text(encoding="utf-8"))
    assert "panel_states" not in data
    assert set(data["panel_layout"]) == {"open-sid", "closed-sid"}


def test_panel_layout_wins_over_stale_panel_states(app_state):
    # Both keys present (a downgrade wrote the old shape after an upgrade
    # wrote the new): the tree entry is the newer record and keeps ruling.
    app_state._CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    app_state._STATE_FILE.write_text(
        json.dumps(
            {
                "panel_layout": {"sid": {"mode": "right"}},
                "panel_states": {"sid": {"open": True, "mode": "bottom"}},
            }
        ),
        encoding="utf-8",
    )
    assert app_state.AppState().get_panel_layout("sid") == {"mode": "right"}


def test_editor_state_roundtrip(app_state):
    state = app_state.AppState()
    snap = {
        "open": True,
        "width": 320,
        "files": ["/proj/a.py", "/proj/b.py"],
        "active": "/proj/b.py",
        "cursors": {"/proj/a.py": [4, 2]},
    }
    state.set_editor_state("sid", snap)
    fresh = app_state.AppState()
    assert fresh.get_editor_state("sid") == snap
    assert fresh.get_editor_state("other") is None


def test_editor_state_none_removes_entry(app_state):
    state = app_state.AppState()
    state.set_editor_state("sid", {"open": False, "files": []})
    state.set_editor_state("sid", None)
    assert app_state.AppState().get_editor_state("sid") is None


def test_editor_state_unchanged_is_not_rewritten(app_state):
    state = app_state.AppState()
    snap = {"open": True, "files": ["/proj/a.py"]}
    state.set_editor_state("sid", snap)
    app_state._STATE_FILE.unlink()  # a redundant save would recreate the file
    state.set_editor_state("sid", dict(snap))  # identical snapshot
    state.set_editor_state("absent", None)  # removing a missing entry
    assert not app_state._STATE_FILE.exists()


def test_editor_state_ignores_corrupt_entries(app_state):
    state = app_state.AppState()
    state.set_editor_state("good", {"open": True, "files": []})
    data = json.loads(app_state._STATE_FILE.read_text(encoding="utf-8"))
    data["editor_states"]["bad"] = "not-a-dict"
    app_state._STATE_FILE.write_text(json.dumps(data), encoding="utf-8")
    fresh = app_state.AppState()
    assert fresh.get_editor_state("good") == {"open": True, "files": []}
    assert fresh.get_editor_state("bad") is None


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


def _shot(name, last=1.0):
    return {"key": f"/tmp/{name}.png", "kind": "image", "source": "lightbox",
            "at": last, "last": last}


def test_session_attachments_roundtrip(app_state):
    state = app_state.AppState()
    state.set_session_attachments("sid", [_shot("b", 9), _shot("a", 1)])
    fresh = app_state.AppState()
    assert fresh.get_session_attachments("sid") == [_shot("b", 9), _shot("a", 1)]
    assert fresh.get_session_attachments("other") == []


def test_session_attachments_empty_removes_entry(app_state):
    state = app_state.AppState()
    state.set_session_attachments("sid", [_shot("a")])
    state.set_session_attachments("sid", [])
    assert app_state.AppState().get_session_attachments("sid") == []


def test_session_attachments_unchanged_are_not_rewritten(app_state):
    """A tab hands its whole list back on every sighting; only what reads
    differently is worth a write."""
    state = app_state.AppState()
    state.set_session_attachments("sid", [_shot("a")])
    app_state._STATE_FILE.unlink()  # a redundant save would recreate the file
    state.set_session_attachments("sid", [_shot("a")])  # identical list
    state.set_session_attachments("absent", [])  # clearing a session that has none
    state.set_session_attachments("", [_shot("b")])  # a tab with no session id yet
    assert not app_state._STATE_FILE.exists()


def test_session_attachments_ignore_corrupt_entries(app_state):
    state = app_state.AppState()
    state.set_session_attachments("good", [_shot("a")])
    data = json.loads(app_state._STATE_FILE.read_text(encoding="utf-8"))
    data["session_attachments"]["bad"] = "not-a-list"
    app_state._STATE_FILE.write_text(json.dumps(data), encoding="utf-8")
    fresh = app_state.AppState()
    assert fresh.get_session_attachments("good") == [_shot("a")]
    assert fresh.get_session_attachments("bad") == []


def test_forwarding_a_session_carries_its_attachments(app_state):
    """A fork is the same conversation: the images it was already shown are
    its own, and its transcript starts after them."""
    state = app_state.AppState()
    state.set_session_attachments("old", [_shot("a")])
    state.forward_session("old", "new")
    assert app_state.AppState().get_session_attachments("new") == [_shot("a")]


def test_forwarding_does_not_clobber_the_forks_own_attachments(app_state):
    state = app_state.AppState()
    state.set_session_attachments("old", [_shot("a")])
    state.set_session_attachments("new", [_shot("b")])
    state.forward_session("old", "new")
    assert app_state.AppState().get_session_attachments("new") == [_shot("b")]


def test_session_draft_roundtrip(app_state):
    state = app_state.AppState()
    state.set_session_draft("sid", "half a prompt\nand a second line")
    fresh = app_state.AppState()
    assert fresh.get_session_draft("sid") == "half a prompt\nand a second line"
    assert fresh.get_session_draft("other") == ""


def test_session_draft_empty_removes_entry(app_state):
    """A draft that went back into a composer, or was sent, must not wait
    on disk for the next launch."""
    state = app_state.AppState()
    state.set_session_draft("sid", "a draft")
    state.set_session_draft("sid", "")
    assert app_state.AppState().get_session_draft("sid") == ""
    data = json.loads(app_state._STATE_FILE.read_text(encoding="utf-8"))
    assert data["session_drafts"] == {}


def test_session_draft_unchanged_is_not_rewritten(app_state):
    state = app_state.AppState()
    state.set_session_draft("sid", "a draft")
    app_state._STATE_FILE.unlink()  # a redundant save would recreate the file
    state.set_session_draft("sid", "a draft")  # the same draft again
    state.set_session_draft("absent", "")  # clearing a session that has none
    state.set_session_draft("", "orphan")  # a tab with no session id yet
    assert not app_state._STATE_FILE.exists()


def test_session_draft_ignores_corrupt_entries(app_state):
    state = app_state.AppState()
    state.set_session_draft("good", "a draft")
    data = json.loads(app_state._STATE_FILE.read_text(encoding="utf-8"))
    data["session_drafts"]["bad"] = ["not", "a", "string"]
    data["session_drafts"]["blank"] = ""
    app_state._STATE_FILE.write_text(json.dumps(data), encoding="utf-8")
    fresh = app_state.AppState()
    assert fresh.get_session_draft("good") == "a draft"
    assert fresh.get_session_draft("bad") == ""
    assert "blank" not in fresh.session_drafts


def test_forwarding_a_session_carries_its_draft(app_state):
    """The draft was written to the conversation, not to the id: a fork
    reaches the same one."""
    state = app_state.AppState()
    state.set_session_draft("old", "a draft")
    state.forward_session("old", "new")
    assert app_state.AppState().get_session_draft("new") == "a draft"


def test_forwarding_does_not_clobber_the_forks_own_draft(app_state):
    state = app_state.AppState()
    state.set_session_draft("old", "a draft")
    state.set_session_draft("new", "the fork's own")
    state.forward_session("old", "new")
    assert app_state.AppState().get_session_draft("new") == "the fork's own"


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


def test_restore_last_session_defaults_off(app_state):
    state = app_state.AppState()
    assert state.get_setting("restore_last_session") is False  # launch opens nothing
    state.set_setting("restore_last_session", True)
    assert app_state.AppState().get_setting("restore_last_session") is True


def test_archive_on_claude_ai_defaults_on(app_state):
    state = app_state.AppState()
    assert state.get_setting("archive_on_claude_ai") is True  # opt-out, not opt-in
    state.set_setting("archive_on_claude_ai", False)
    assert app_state.AppState().get_setting("archive_on_claude_ai") is False


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


def test_editor_window_geometry_roundtrip(app_state):
    state = app_state.AppState()
    state.update_settings(
        {"editor_window_width": 1400, "editor_window_height": 900, "editor_window_maximized": True}
    )
    fresh = app_state.AppState()
    assert fresh.get_setting("editor_window_width") == 1400
    assert fresh.get_setting("editor_window_height") == 900
    assert fresh.get_setting("editor_window_maximized") is True


def test_editor_window_geometry_defaults(app_state):
    state = app_state.AppState()
    assert state.get_setting("editor_window_width") == 1000
    assert state.get_setting("editor_window_height") == 700
    assert state.get_setting("editor_window_maximized") is False


def test_editor_pop_out_threshold_default(app_state):
    state = app_state.AppState()
    assert state.get_setting("editor_pop_out_screen_width") == 1600


def test_editor_pops_out_at_or_below_threshold():
    assert editor_pops_out(1280, 1536)
    assert editor_pops_out(1536, 1536)


def test_editor_pops_out_not_above_threshold():
    assert not editor_pops_out(1920, 1536)


def test_editor_pops_out_zero_threshold_always_docks():
    assert not editor_pops_out(1280, 0)


def test_editor_pops_out_unknown_monitor_docks():
    assert not editor_pops_out(0, 1536)


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


def test_merge_project_order_keeps_saved_order_and_prepends_new_alphabetically():
    assert merge_project_order(["zeta", "alpha"], ["alpha", "Beta", "gamma", "zeta"]) == [
        "Beta",
        "gamma",
        "zeta",
        "alpha",
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
    state.set_panel_layout("old", {"mode": "bottom", "sizes": {"bottom": 300}})
    state.set_editor_state("old", {"open": True, "files": ["/proj/a.py"]})
    state.forward_session("old", "new")

    fresh = app_state.AppState()
    assert fresh.resolve_forward("old") == "new"
    assert fresh.get_name("new") == "My task"
    assert fresh.get_emoji("new") == "🚀"
    assert fresh.is_favorite("new")
    assert fresh.get_panel_layout("new") == {"mode": "bottom", "sizes": {"bottom": 300}}
    assert fresh.get_editor_state("new") == {"open": True, "files": ["/proj/a.py"]}
    # The stale original is NOT flagged archived here: the store derives its
    # row's fate from the forward (visible-but-disabled until the fork is
    # discovered, replaced afterwards).
    assert not fresh.is_archived("old")
    assert not fresh.is_archived("new")


def test_process_baseline_roundtrip(app_state):
    state = app_state.AppState()
    state.set_process_baseline("sid", {"python3 -m collins.mcp_shim", "a-server --stdio"})

    fresh = app_state.AppState()
    assert fresh.get_process_baseline("sid") == {"python3 -m collins.mcp_shim", "a-server --stdio"}
    assert fresh.get_process_baseline("unknown") == set()
    assert fresh.get_process_baseline("") == set()


def test_process_baseline_unchanged_set_never_touches_the_disk(app_state, monkeypatch):
    # The setter runs from a 2-second poll; re-recording the same set (in any
    # iteration order) must not rewrite state.json every tick.
    state = app_state.AppState()
    state.set_process_baseline("sid", ["b", "a"])
    saves = []
    monkeypatch.setattr(state, "save", lambda: saves.append(1))
    state.set_process_baseline("sid", {"a", "b"})
    state.set_process_baseline("", ["ignored entirely"])
    assert saves == []
    state.set_process_baseline("sid", {"a", "b", "c"})
    assert saves == [1]


def test_forward_session_carries_the_process_baseline(app_state):
    # A fork is the same CLI plumbing under a new id; losing the baseline
    # there would resurrect the busy pole the moment the fork re-attaches.
    state = app_state.AppState()
    state.set_process_baseline("old", ["plumbing --stdio"])
    state.forward_session("old", "new")

    fresh = app_state.AppState()
    assert fresh.get_process_baseline("new") == {"plumbing --stdio"}


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


def test_forward_chain_covers_every_id_including_the_middle(app_state):
    state = app_state.AppState()
    state.forward_session("a", "b")
    state.forward_session("b", "c")

    # The middle is what a second /bg runs its handoff under: resolving to the
    # tail alone loses the session exactly while it's being handed over.
    assert state.forward_chain("a") == ["a", "b", "c"]
    assert state.forward_chain("b") == ["b", "c"]
    assert state.forward_chain("c") == ["c"]
    assert state.resolve_forward("a") == "c"


def test_forward_chain_of_an_unforwarded_session_is_just_itself(app_state):
    assert app_state.AppState().forward_chain("lonely") == ["lonely"]


def test_forward_chain_survives_a_cycle(app_state):
    state = app_state.AppState()
    state.session_forwards = {"a": "b", "b": "a"}  # corrupt state on disk

    assert state.forward_chain("a") == ["a", "b"]
    assert state.resolve_forward("a") == "b"


def test_project_worktree_override_roundtrip(app_state):
    state = app_state.AppState()
    assert state.project_worktree_override("alpha") is None
    assert state.worktree_for_project("alpha") is False  # app default: off

    state.set_setting("worktree_new_sessions", True)
    assert state.worktree_for_project("alpha") is True

    state.set_project_worktree("alpha", False)
    state.set_project_worktree("beta", True)
    fresh = app_state.AppState()
    assert fresh.project_worktree_override("alpha") is False
    assert fresh.project_worktree_override("beta") is True
    assert fresh.project_worktree_override("gamma") is None
    assert fresh.worktree_for_project("alpha") is False
    assert fresh.worktree_for_project("beta") is True


def test_project_worktree_pin_survives_app_default_flip(app_state):
    state = app_state.AppState()
    # Pinning a project to the value the app setting already has still records
    # the override: a project pinned "off" stays off when the app default is
    # later flipped on.
    state.set_project_worktree("alpha", False)
    state.set_setting("worktree_new_sessions", True)
    assert state.worktree_for_project("alpha") is False


def test_new_chat_drafts_roundtrip_oldest_first(app_state):
    state = app_state.AppState()
    later = {"cwd": "/p", "provider": "claude", "text": "second", "created": 20.0}
    earlier = {"cwd": "/p", "provider": "claude", "text": "first", "created": 10.0, "worktree": True}
    state.set_new_chat_draft("draft-b", later)
    state.set_new_chat_draft("draft-a", earlier)

    fresh = app_state.AppState()
    assert list(fresh.get_new_chat_drafts()) == ["draft-a", "draft-b"]
    assert fresh.get_new_chat_draft("draft-a") == earlier
    assert fresh.get_new_chat_draft("draft-zzz") is None

    fresh.remove_new_chat_draft("draft-a")
    fresh.remove_new_chat_draft("draft-never")  # nothing to forget, no error
    assert list(app_state.AppState().get_new_chat_drafts()) == ["draft-b"]


def test_new_chat_drafts_refuse_bad_ids_and_records(app_state):
    state = app_state.AppState()
    state.set_new_chat_draft("placeholder-1", {"cwd": "/p"})  # not a draft id
    assert state.get_new_chat_drafts() == {}

    app_state._CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    app_state._STATE_FILE.write_text(
        json.dumps(
            {
                "new_chat_drafts": {
                    "draft-ok": {"cwd": "/p", "text": "hi", "created": 1},
                    "draft-bad": {"text": "no cwd"},
                    "not-a-draft": {"cwd": "/q"},
                }
            }
        ),
        encoding="utf-8",
    )
    fresh = app_state.AppState()
    assert list(fresh.get_new_chat_drafts()) == ["draft-ok"]
    assert fresh.get_new_chat_draft("draft-ok") == {
        "cwd": "/p",
        "provider": "claude",
        "text": "hi",
        "created": 1.0,
    }


def test_new_chat_draft_unchanged_write_is_skipped(app_state):
    state = app_state.AppState()
    record = {"cwd": "/p", "provider": "claude", "text": "hi", "created": 1.0}
    state.set_new_chat_draft("draft-a", record)
    app_state._STATE_FILE.unlink()  # a rewrite would recreate it
    state.set_new_chat_draft("draft-a", dict(record))
    assert not app_state._STATE_FILE.exists()
    state.remove_new_chat_draft("draft-a")
    assert app_state._STATE_FILE.exists()


# -- the model pickers' None -----------------------------------------------------


def test_model_setting_defaults(app_state):
    # Titles default to the automatic model; icons to None, so Generate Icon
    # waits for a pick instead of running on open.
    state = app_state.AppState()
    assert state.get_setting("title_model") == ""
    assert state.get_setting("icon_model") == NO_MODEL
    assert "auto_title_sessions" not in app_state.DEFAULT_SETTINGS


def test_auto_renew_login_defaults_on(app_state):
    # The login repair ran with no switch before it had one; the switch
    # arriving must not turn it off under anyone.
    assert app_state.AppState().get_setting("auto_renew_login") is True
    assert app_state.DEFAULT_SETTINGS["auto_renew_login"] is True


def test_old_state_without_the_model_keys_loads_the_new_defaults(app_state):
    app_state._CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    app_state._STATE_FILE.write_text(
        json.dumps({"settings": {"scrollback": 5000}}), encoding="utf-8"
    )
    state = app_state.AppState()
    assert state.get_setting("scrollback") == 5000
    assert state.get_setting("title_model") == ""
    assert state.get_setting("icon_model") == NO_MODEL


def test_every_default_is_persisted(app_state):
    # Every default is written out on save, not only the keys that were set:
    # an install that saved under the old default carries an explicit
    # icon_model of "" forward, keeping its auto-start.
    app_state.AppState().set_setting("scrollback", 5000)
    data = json.loads(app_state._STATE_FILE.read_text(encoding="utf-8"))
    assert data["settings"]["icon_model"] == NO_MODEL
    assert data["settings"]["title_model"] == ""

    data["settings"]["icon_model"] = ""
    app_state._STATE_FILE.write_text(json.dumps(data), encoding="utf-8")
    assert app_state.AppState().get_setting("icon_model") == ""


def test_auto_title_off_migrates_to_a_none_title_model(app_state):
    app_state._CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    app_state._STATE_FILE.write_text(
        json.dumps({"settings": {"auto_title_sessions": False, "title_model": "claude-haiku-4-5"}}),
        encoding="utf-8",
    )
    state = app_state.AppState()
    assert state.get_setting("title_model") == NO_MODEL
    assert "auto_title_sessions" not in state.settings

    state.save()
    data = json.loads(app_state._STATE_FILE.read_text(encoding="utf-8"))
    assert data["settings"]["title_model"] == NO_MODEL
    assert "auto_title_sessions" not in data["settings"]


def test_auto_title_on_just_drops_the_key(app_state):
    app_state._CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    app_state._STATE_FILE.write_text(
        json.dumps({"settings": {"auto_title_sessions": True, "title_model": "claude-haiku-4-5"}}),
        encoding="utf-8",
    )
    state = app_state.AppState()
    assert state.get_setting("title_model") == "claude-haiku-4-5"
    assert "auto_title_sessions" not in state.settings

    state.save()
    data = json.loads(app_state._STATE_FILE.read_text(encoding="utf-8"))
    assert "auto_title_sessions" not in data["settings"]
