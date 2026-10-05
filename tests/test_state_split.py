# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The state split (the service-and-client spec, §3.8): state.json keeps
the service's keys, ui-state.json this device's, AppState routes between
them, and the first start after the split migrates once, with a backup —
and survives a downgrade round trip, a crash between its two writes and
an unwritable config dir."""

import json
import os
import stat

import pytest

from collins import mcptools, prefslayout, uistate
from collins.state import DEFAULT_SETTINGS, DEVICE_SETTINGS, SERVICE_SETTINGS

# -- the key lists, pinned ------------------------------------------------

# The spec's §3.8 service settings, by name. mcp_tool_* and sandbox_tool_*
# come from the tool table and are listed as patterns below.
SPEC_SERVICE_SETTINGS = {
    "claude_cli_path", "title_model", "icon_model", "auto_renew_login",
    "worktree_new_sessions", "clone_directory",
    "sandbox_new_sessions", "sandbox_bypass_permissions", "sandbox_share_gh",
    "sandbox_share_ssh", "sandbox_settings_editable",
    "attach_prompt_prs", "pr_title_sessions", "cli_title_sessions",
    "refresh_prs_on_launch", "archive_running_session", "archive_worktree",
    "archive_on_claude_ai", "auto_delete_archived_after",
    "auto_delete_archived_unit", "background_status_poll", "progress_termprop",
    "git_parent_branch", "git_untracked", "git_log_page", "welcome_seen",
    "gh_welcome_dismissed",
}

# The spec's §3.8 service top-level keys that the code writes. The spec also
# names "hidden" and "hidden_projects" (pre-rename spellings AppState only
# ever reads). "ptys" is the pty table PR-1.5 added, "diff_notes" and
# "pending_diffs" PR-1.11's; a migrated file gains each with its empty
# default.
SERVICE_RECORDS = {
    "service_id", "names", "generated_names", "cli_titles", "emojis", "favorites",
    "archived", "archived_at", "archived_projects", "project_worktree",
    "project_sandbox", "sandboxed_sessions", "sandbox_grants",
    "sandbox_project_grants", "sandbox_tools", "project_order",
    "virtual_projects", "expanded_groups", "session_prs", "session_attachments",
    "session_drafts", "new_chat_drafts", "process_baselines", "session_forwards",
    "pending_detaches", "ptys", "pty_next_id", "notifications", "settings",
    "diff_notes", "pending_diffs", "resume_on_start",
}
# Not in a v0.1.4 file.
NEW_SERVICE_RECORDS = {
    "service_id", "ptys", "pty_next_id", "diff_notes", "pending_diffs", "resume_on_start",
}

# Device settings added since v0.1.4 (not in its file): the quit dialog's
# one-time notice and the ask -> detach move's marker (PR-1.12c).
NEW_DEVICE_SETTINGS = {"quit_detach_migrated", "quit_notice_shown"}

# Every settings key v0.1.4 wrote, frozen as a literal: the migration
# fixture is built from this list, not from DEFAULT_SETTINGS, so a key the
# catalogue gains or loses later cannot change what the fixture stands for.
V014_SETTINGS_KEYS = (
    "announce_finished_runs", "archive_on_claude_ai", "archive_running_session",
    "archive_worktree", "attach_overlay_button", "attach_prompt_prs",
    "auto_delete_archived_after", "auto_delete_archived_unit", "auto_renew_login",
    "background_status_poll", "bell_notifications", "caffeine_idle_grace_minutes",
    "caffeine_keep_screen_on", "caffeine_launch_timer", "caffeine_on_launch",
    "check_for_updates", "claude_cli_path", "cli_title_sessions", "clone_directory",
    "color_scheme", "composer_enter_sends", "composer_on_typing",
    "composer_spell_click", "confirm_merges", "dock_attachments_when_room",
    "easy_copy_paste", "editor_font", "editor_narrow_width",
    "editor_pop_out_screen_width", "editor_show_hidden_files",
    "editor_show_line_numbers", "editor_style_scheme", "editor_width",
    "editor_window_height", "editor_window_maximized", "editor_window_width",
    "font", "footer_apps", "gh_welcome_dismissed", "git_hide_whitespace",
    "git_layout", "git_line_numbers", "git_log_page", "git_parent_branch",
    "git_untracked", "git_word_diff", "git_wrap_lines", "hide_notice_shown",
    "icon_model", "inapp_notifications", "keybindings", "language",
    "last_active_session", "mcp_tool_annotate_diff", "mcp_tool_attach_pr",
    "mcp_tool_clear_diff_marks", "mcp_tool_diff_context", "mcp_tool_highlight_diff",
    "mcp_tool_notify_user", "mcp_tool_open_in_editor", "mcp_tool_read_terminal",
    "mcp_tool_run_in_terminal", "mcp_tool_set_session_title", "mcp_tool_show_diff",
    "mcp_tool_show_image", "mcp_tool_start_session", "notification_color_scheme",
    "notification_sound", "open_pr_panel_on_attach", "page_panel_size_bottom",
    "page_panel_size_right", "panel_position", "panel_size_bottom",
    "panel_size_right", "panel_tab_drag_handles", "pr_font_scale",
    "pr_inline_images", "pr_title_sessions", "progress_termprop",
    "project_icon_size", "quit_with_running_sessions", "refresh_prs_on_launch",
    "restore_last_session", "sandbox_bypass_permissions", "sandbox_new_sessions",
    "sandbox_settings_editable", "sandbox_share_gh", "sandbox_share_ssh",
    "sandbox_tool_annotate_diff", "sandbox_tool_attach_pr",
    "sandbox_tool_clear_diff_marks", "sandbox_tool_diff_context",
    "sandbox_tool_highlight_diff", "sandbox_tool_notify_user",
    "sandbox_tool_open_in_editor", "sandbox_tool_read_terminal",
    "sandbox_tool_run_in_terminal", "sandbox_tool_set_session_title",
    "sandbox_tool_show_diff", "sandbox_tool_show_image", "sandbox_tool_start_session",
    "scrollback", "show_folder_path", "show_tab_bar", "show_usage_panel",
    "sidebar_width", "status_icon", "terminal_max_width", "terminal_theme",
    "title_model", "usage_panel_collapsed", "welcome_seen", "window_height",
    "window_maximized", "window_width", "worktree_new_sessions",
)


def test_every_setting_is_on_exactly_one_side():
    assert SERVICE_SETTINGS | DEVICE_SETTINGS == set(DEFAULT_SETTINGS)
    assert not (SERVICE_SETTINGS & DEVICE_SETTINGS)


def test_the_service_settings_are_the_specs():
    tools = set(mcptools.default_tool_settings()) | set(mcptools.default_sandbox_tool_settings())
    assert tools  # the patterns stand for something
    assert SERVICE_SETTINGS == SPEC_SERVICE_SETTINGS | tools


def test_the_device_settings_are_everything_else():
    # Spelled out, so a new setting lands on a side on purpose and this
    # test names it.
    assert DEVICE_SETTINGS == {
        "font", "keybindings", "scrollback", "color_scheme", "terminal_theme",
        "terminal_max_width", "easy_copy_paste", "language",
        "quit_with_running_sessions", "quit_detach_migrated", "quit_notice_shown",
        "hide_notice_shown", "show_tab_bar", "status_icon", "inapp_notifications", "notification_sound",
        "bell_notifications", "announce_finished_runs", "check_for_updates",
        "notification_color_scheme", "attach_overlay_button",
        "composer_enter_sends", "composer_on_typing", "composer_spell_click",
        "show_folder_path", "project_icon_size", "show_usage_panel",
        "usage_panel_collapsed", "footer_apps", "caffeine_keep_screen_on",
        "caffeine_on_launch", "caffeine_launch_timer",
        "caffeine_idle_grace_minutes", "sidebar_width", "panel_position",
        "panel_tab_drag_handles", "panel_size_bottom", "panel_size_right",
        "page_panel_size_bottom", "page_panel_size_right", "window_width",
        "window_height", "window_maximized", "last_active_session",
        "restore_last_session", "editor_width", "editor_style_scheme",
        "editor_font", "editor_show_line_numbers", "editor_show_hidden_files",
        "editor_pop_out_screen_width", "editor_narrow_width", "pr_font_scale",
        "pr_inline_images", "open_pr_panel_on_attach", "dock_attachments_when_room",
        "confirm_merges", "git_layout", "git_line_numbers", "git_wrap_lines",
        "git_word_diff", "git_hide_whitespace", "editor_window_width",
        "editor_window_height", "editor_window_maximized",
    }


def test_the_v014_key_list_is_the_catalogue_of_its_day():
    # The literal list above is what v0.1.4 wrote; today's catalogue has
    # not dropped any of it (a dropped key would be a migration of its own).
    assert set(V014_SETTINGS_KEYS) <= set(DEFAULT_SETTINGS)


def test_the_service_scoped_settings_are_device_settings():
    assert uistate.SERVICE_SCOPED_SETTINGS == {"last_active_session"}
    assert uistate.SERVICE_SCOPED_SETTINGS <= DEVICE_SETTINGS
    assert uistate.DEVICE_RECORDS == ("panel_layout", "editor_states")


# Each Preferences group's side, with the settings that decide it: every
# group is pinned, and a group's side has to agree with the lists for
# every setting it builds (prefs.py's _build_*_group).
GROUP_SETTINGS = {
    "cli": ("claude_cli_path",),
    "general": ("language", "color_scheme", "status_icon", "show_tab_bar", "show_folder_path",
                "project_icon_size", "panel_tab_drag_handles", "clone_directory",
                "check_for_updates", "show_usage_panel"),
    "token_use": ("title_model", "icon_model", "auto_renew_login"),
    "mcp_tools": tuple(mcptools.default_tool_settings()),
    "sessions": ("archive_running_session", "quit_with_running_sessions", "restore_last_session",
                 "archive_worktree", "archive_on_claude_ai", "auto_delete_archived_after",
                 "auto_delete_archived_unit", "cli_title_sessions", "pr_title_sessions",
                 "attach_prompt_prs", "background_status_poll", "progress_termprop"),
    "sandbox": ("sandbox_new_sessions", "sandbox_bypass_permissions", "sandbox_share_gh",
                "sandbox_share_ssh", "sandbox_settings_editable",
                *mcptools.default_sandbox_tool_settings()),
    "notifications": ("inapp_notifications", "notification_color_scheme", "notification_sound",
                      "bell_notifications", "announce_finished_runs"),
    "composer": ("composer_enter_sends", "composer_on_typing", "composer_spell_click",
                 "attach_overlay_button"),
    "terminal": ("font", "scrollback", "terminal_theme", "terminal_max_width", "easy_copy_paste"),
    "footer_apps": ("footer_apps",),
    "pull_requests": ("pr_font_scale", "pr_inline_images", "open_pr_panel_on_attach",
                      "confirm_merges", "refresh_prs_on_launch"),
    "git": ("git_layout", "git_line_numbers", "git_wrap_lines", "git_word_diff",
            "git_hide_whitespace", "git_untracked", "git_log_page", "git_parent_branch"),
    "caffeine": ("caffeine_on_launch", "caffeine_launch_timer", "caffeine_idle_grace_minutes",
                 "caffeine_keep_screen_on"),
    "editor": ("editor_style_scheme", "editor_font", "editor_show_line_numbers",
               "editor_show_hidden_files", "editor_narrow_width", "editor_pop_out_screen_width"),
}


def test_the_prefslayout_sides_agree_with_the_split():
    assert set(prefslayout.GROUP_SIDES) == set(prefslayout.GROUPS) == set(GROUP_SETTINGS)
    assert prefslayout.GROUP_SIDES == {
        "cli": "service",
        "general": "mixed",
        "token_use": "service",
        "mcp_tools": "service",
        "sessions": "mixed",
        "sandbox": "service",
        "notifications": "device",
        "composer": "device",
        "terminal": "device",
        "footer_apps": "device",
        "pull_requests": "mixed",
        "git": "mixed",
        "caffeine": "device",
        "editor": "device",
    }
    for group, keys in GROUP_SETTINGS.items():
        for key in keys:
            assert key in DEFAULT_SETTINGS, (group, key)
        sides = {"service" if key in SERVICE_SETTINGS else "device" for key in keys}
        expected = sides.pop() if len(sides) == 1 else "mixed"
        assert prefslayout.GROUP_SIDES[group] == expected, group
    for key in prefslayout.TOKEN_USE_ROWS:
        if key != "model_list":
            assert key in GROUP_SETTINGS["token_use"]
    for key in prefslayout.SANDBOX_ROWS:
        if key not in ("status", "sandbox_tools"):
            assert key in GROUP_SETTINGS["sandbox"]


# -- a v0.1.4 state.json, migrated ----------------------------------------


def _v014_state() -> dict:
    """A state.json as v0.1.4 wrote it: every key of its day, a few of them
    changed, and representative records of both sides."""
    settings = {key: DEFAULT_SETTINGS[key] for key in V014_SETTINGS_KEYS}
    settings.update({
        "scrollback": 5000,
        "terminal_theme": "Nord",
        "window_width": 1500,
        "last_active_session": "sid-active",
        "keybindings": {"win.close-tab": ["<Control>F4"]},
        "title_model": "claude-haiku-4-5-20251001",
        "welcome_seen": True,
        "git_layout": "stack",
        "git_log_page": 50,
        # What v0.1.4 wrote back for the quit setting: its default then.
        "quit_with_running_sessions": "ask",
    })
    return {
        "names": {"sid-1": "My session"},
        "generated_names": {"sid-2": "A generated one"},
        "cli_titles": {},
        "emojis": {"sid-1": "🚀"},
        "favorites": ["sid-1"],
        "archived": ["sid-3"],
        "archived_at": {"sid-3": 1700000000.0},
        "archived_projects": [],
        "project_worktree": {"proj": True},
        "project_sandbox": {},
        "sandboxed_sessions": {},
        "sandbox_grants": {},
        "sandbox_project_grants": {},
        "sandbox_tools": {},
        "project_order": ["proj", "other"],
        "virtual_projects": {"other": "/home/u/other"},
        "expanded_groups": ["proj:proj"],
        "panel_layout": {"sid-1": {"mode": "bottom", "sizes": {"bottom": 300}}},
        "editor_states": {"sid-1": {"open": True, "files": ["/proj/a.py"]}},
        "session_prs": {"sid-1": [{"number": 7, "url": "https://github.com/episode6/collins/pull/7"}]},
        "session_attachments": {},
        "session_drafts": {"sid-2": "an unsent prompt"},
        "new_chat_drafts": {},
        "process_baselines": {},
        "session_forwards": {},
        "pending_detaches": {},
        "notifications": [],
        "settings": settings,
    }


def _write_state(app_state, data: dict) -> None:
    app_state._CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    app_state._STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


@pytest.fixture
def v014(app_state):
    original = _v014_state()
    _write_state(app_state, original)
    return original


def _files(app_state):
    service = json.loads(app_state._STATE_FILE.read_text(encoding="utf-8"))
    ui = json.loads(app_state._ui_state_file().read_text(encoding="utf-8"))
    return service, ui


def _backups(app_state) -> list:
    return sorted(app_state._CONFIG_DIR.glob("state.json.pre-split*"))


def test_first_start_migrates_with_every_key_accounted_for(app_state, v014):
    state = app_state.AppState(migrate=True)
    backup = app_state._pre_split_backup()
    assert _backups(app_state) == [backup]
    assert json.loads(backup.read_text(encoding="utf-8")) == v014

    service, ui = _files(app_state)
    block = ui["services"][state.service_id]
    # The service file: the original's top-level keys less the device's,
    # plus the id.
    assert set(service) == (set(v014) - set(uistate.DEVICE_RECORDS)) | NEW_SERVICE_RECORDS
    assert set(service) == SERVICE_RECORDS
    assert service["service_id"] == state.service_id
    # Every setting of the original is in exactly one file.
    assert set(service["settings"]) == SERVICE_SETTINGS
    device_in_ui = set(ui["device"]["settings"]) | uistate.SERVICE_SCOPED_SETTINGS
    assert device_in_ui == DEVICE_SETTINGS
    assert set(service["settings"]) | device_in_ui == set(v014["settings"]) | NEW_DEVICE_SETTINGS
    assert not (set(service["settings"]) & set(ui["device"]["settings"]))
    # With their values.
    assert service["settings"]["title_model"] == "claude-haiku-4-5-20251001"
    assert service["settings"]["git_log_page"] == 50
    assert ui["device"]["settings"]["scrollback"] == 5000
    assert ui["device"]["settings"]["terminal_theme"] == "Nord"
    assert ui["device"]["settings"]["keybindings"] == {"win.close-tab": ["<Control>F4"]}
    assert ui["device"]["settings"]["git_layout"] == "stack"
    assert block["last_active_session"] == "sid-active"
    assert block["panel_layout"] == v014["panel_layout"]
    assert block["editor_states"] == v014["editor_states"]
    assert block["open_tabs"] == []
    assert uistate._is_id(ui["client_id"])
    assert ui["connections"] == []
    assert ui["last_connection"] == ""
    # The records that stay are untouched.
    for key in ("names", "favorites", "archived", "project_order", "session_prs", "session_drafts"):
        assert service[key] == v014[key], key


def test_the_merged_view_reads_both_sides(app_state, v014):
    state = app_state.AppState(migrate=True)
    assert state.get_setting("scrollback") == 5000  # device
    assert state.get_setting("title_model") == "claude-haiku-4-5-20251001"  # service
    assert state.get_setting("last_active_session") == "sid-active"  # per service
    assert state.get_panel_layout("sid-1") == {"mode": "bottom", "sizes": {"bottom": 300}}
    assert state.get_editor_state("sid-1") == {"open": True, "files": ["/proj/a.py"]}
    assert state.get_name("sid-1") == "My session"
    # The settings attribute other modules are handed holds everything.
    assert set(state.settings) == set(DEFAULT_SETTINGS)


def test_a_second_start_migrates_nothing(app_state, v014):
    app_state.AppState(migrate=True)
    backup = app_state._pre_split_backup()
    before = (
        app_state._STATE_FILE.read_bytes(),
        app_state._ui_state_file().read_bytes(),
        backup.read_bytes(),
    )
    backup.unlink()
    second = app_state.AppState(migrate=True)
    assert _backups(app_state) == []
    assert app_state._STATE_FILE.read_bytes() == before[0]
    assert app_state._ui_state_file().read_bytes() == before[1]
    assert second.get_setting("scrollback") == 5000
    assert second.get_panel_layout("sid-1") is not None


def test_a_fresh_install_gets_an_id_and_no_backup(app_state):
    state = app_state.AppState(migrate=True)
    assert uistate._is_id(state.service_id)
    assert _backups(app_state) == []
    assert not app_state._STATE_FILE.exists()  # nothing was written yet
    state.set_setting("welcome_seen", True)
    service = json.loads(app_state._STATE_FILE.read_text(encoding="utf-8"))
    assert service["service_id"] == state.service_id
    assert "window_width" not in service["settings"]
    assert app_state.AppState().service_id == state.service_id


def test_an_unparseable_file_is_left_alone(app_state):
    app_state._CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    app_state._STATE_FILE.write_text("{not json", encoding="utf-8")
    state = app_state.AppState(migrate=True)
    assert app_state._STATE_FILE.read_text(encoding="utf-8") == "{not json"
    assert _backups(app_state) == []
    assert not app_state._ui_state_file().exists()
    assert state.get_setting("scrollback") == DEFAULT_SETTINGS["scrollback"]


def test_an_unknown_settings_key_stays_in_state_json(app_state, v014):
    v014["settings"]["notify_idle"] = True  # a key no build of this catalogue knows
    _write_state(app_state, v014)
    app_state.AppState(migrate=True)
    service, ui = _files(app_state)
    assert service["settings"]["notify_idle"] is True
    assert "notify_idle" not in ui["device"]["settings"]


def test_legacy_panel_states_migrate_with_the_split(app_state, v014):
    import collins.panelhistory as panelhistory

    panelhistory._HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    v014["panel_states"] = {
        "closed-sid": {"open": False, "mode": "right", "sizes": {"right": 512}},
        "sid-1": {"open": False, "mode": "right", "sizes": {"right": 1}},  # a tree entry already rules
    }
    _write_state(app_state, v014)
    state = app_state.AppState(migrate=True)
    assert state.get_panel_layout("closed-sid") == {"mode": "right", "sizes": {"right": 512}}
    assert state.get_panel_layout("sid-1") == v014["panel_layout"]["sid-1"]
    service, ui = _files(app_state)
    assert "panel_states" not in service
    assert set(ui["services"][state.service_id]["panel_layout"]) == {"sid-1", "closed-sid"}


def test_a_downgrade_and_upgrade_keeps_what_the_old_build_could_not_know(app_state, v014):
    first = app_state.AppState(migrate=True)
    first.set_panel_layout("sid-9", {"mode": "right"})
    first.set_editor_state("sid-9", {"open": True, "files": ["/x"]})
    first.set_setting("sidebar_width", 444)  # differs from the default
    first.set_setting("last_active_session", "sid-9")
    backup_bytes = app_state._pre_split_backup().read_bytes()

    # A build from before the split, started on the split file, ran on
    # DEFAULT_SETTINGS and empty layouts and saved them back without the
    # id — and changed one layout and one setting on purpose meanwhile.
    data = json.loads(app_state._STATE_FILE.read_text(encoding="utf-8"))
    del data["service_id"]
    data["settings"] = {key: DEFAULT_SETTINGS[key] for key in V014_SETTINGS_KEYS}
    data["settings"]["welcome_seen"] = True
    data["settings"]["scrollback"] = 777  # chosen on the old build
    data["panel_layout"] = {"sid-1": {"mode": "bottom"}}  # changed on the old build
    data["editor_states"] = {}
    app_state._STATE_FILE.write_text(json.dumps(data), encoding="utf-8")

    again = app_state.AppState(migrate=True)
    assert again.service_id == first.service_id  # the one block is adopted
    # What ui-state.json kept survives where the old build wrote defaults
    # or nothing; what the old build changed is taken.
    assert again.get_setting("sidebar_width") == 444
    assert again.get_setting("scrollback") == 777
    assert again.get_setting("terminal_theme") == "Nord"
    assert again.get_setting("last_active_session") == "sid-9"
    assert again.get_panel_layout("sid-9") == {"mode": "right"}
    assert again.get_panel_layout("sid-1") == {"mode": "bottom"}
    assert again.get_editor_state("sid-9") == {"open": True, "files": ["/x"]}
    assert again.get_editor_state("sid-1") == v014["editor_states"]["sid-1"]
    # The first backup is untouched and a timestamped sibling appeared.
    backups = _backups(app_state)
    assert len(backups) == 2
    assert app_state._pre_split_backup().read_bytes() == backup_bytes
    assert backups[1].name.startswith("state.json.pre-split.")
    assert json.loads(backups[1].read_text(encoding="utf-8")) == data
    _service, ui = _files(app_state)
    assert list(ui["services"]) == [first.service_id]


def test_a_crash_between_the_two_writes_loses_nothing(app_state, v014, monkeypatch):
    real = uistate.write_json_atomic

    def fail_on_state(path, payload):
        if path == app_state._STATE_FILE:
            raise OSError("disk full")
        real(path, payload)

    monkeypatch.setattr(uistate, "write_json_atomic", fail_on_state)
    crashed = app_state.AppState(migrate=True)
    assert crashed.get_setting("scrollback") == 5000  # the merged view stands
    # ui-state.json was written first; state.json is still the unsplit one.
    assert app_state._ui_state_file().exists()
    assert "service_id" not in json.loads(app_state._STATE_FILE.read_text(encoding="utf-8"))
    monkeypatch.setattr(uistate, "write_json_atomic", real)

    state = app_state.AppState(migrate=True)
    service, ui = _files(app_state)
    assert service["service_id"] == state.service_id
    assert "panel_layout" not in service
    assert ui["device"]["settings"]["scrollback"] == 5000
    assert ui["services"][state.service_id]["panel_layout"] == v014["panel_layout"]
    assert ui["services"][state.service_id]["last_active_session"] == "sid-active"
    assert state.get_setting("title_model") == "claude-haiku-4-5-20251001"
    assert len(_backups(app_state)) == 2  # one per attempt; nothing overwritten


def test_an_unwritable_config_dir_does_not_stop_the_app(app_state, v014):
    if os.geteuid() == 0:
        pytest.skip("root writes anywhere")
    before = app_state._STATE_FILE.read_bytes()
    app_state._CONFIG_DIR.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        state = app_state.AppState(migrate=True)
        assert state.get_setting("scrollback") == 5000
        assert state.get_panel_layout("sid-1") == v014["panel_layout"]["sid-1"]
        assert state.get_name("sid-1") == "My session"
        assert app_state._STATE_FILE.read_bytes() == before
        assert _backups(app_state) == []
        assert not app_state._ui_state_file().exists()
    finally:
        app_state._CONFIG_DIR.chmod(stat.S_IRWXU)
    # Writable again: the next start migrates.
    app_state.AppState(migrate=True)
    service, _ui = _files(app_state)
    assert "service_id" in service


def test_a_reader_that_does_not_migrate_writes_nothing(app_state, v014):
    before = app_state._STATE_FILE.read_bytes()
    reader = app_state.AppState()  # migrate=False, as every worker-thread reader
    assert reader.get_setting("scrollback") == 5000
    assert reader.get_setting("last_active_session") == "sid-active"
    assert reader.get_panel_layout("sid-1") == v014["panel_layout"]["sid-1"]
    assert app_state._STATE_FILE.read_bytes() == before
    assert not app_state._ui_state_file().exists()
    assert _backups(app_state) == []


def test_a_reader_that_saves_commits_the_migration_first(app_state, v014):
    reader = app_state.AppState()
    reader.set_generated_names({"sid-2": "Titled by a worker"})
    service, ui = _files(app_state)
    assert service["service_id"] == reader.service_id
    assert service["generated_names"]["sid-2"] == "Titled by a worker"
    assert "panel_layout" not in service and "window_width" not in service["settings"]
    assert ui["device"]["settings"]["scrollback"] == 5000
    assert ui["services"][reader.service_id]["panel_layout"] == v014["panel_layout"]
    assert len(_backups(app_state)) == 1


# -- routing of writes ----------------------------------------------------


def test_a_device_setting_lands_in_ui_state_only(app_state):
    state = app_state.AppState()
    state.set_setting("sidebar_width", 420)
    assert not app_state._STATE_FILE.exists()
    ui = json.loads(app_state._ui_state_file().read_text(encoding="utf-8"))
    assert ui["device"]["settings"]["sidebar_width"] == 420
    # Every device default is written back, as state.json's are.
    assert set(ui["device"]["settings"]) == DEVICE_SETTINGS - uistate.SERVICE_SCOPED_SETTINGS
    assert app_state.AppState().get_setting("sidebar_width") == 420


def test_a_service_setting_lands_in_state_json_only(app_state):
    state = app_state.AppState()
    state.set_setting("git_log_page", 40)
    assert not app_state._ui_state_file().exists()
    service = json.loads(app_state._STATE_FILE.read_text(encoding="utf-8"))
    assert service["settings"]["git_log_page"] == 40
    assert "sidebar_width" not in service["settings"]
    assert app_state.AppState().get_setting("git_log_page") == 40


def test_last_active_session_is_kept_per_service(app_state):
    state = app_state.AppState()
    state.set_setting("last_active_session", "sid-x")
    assert not app_state._STATE_FILE.exists()
    ui = json.loads(app_state._ui_state_file().read_text(encoding="utf-8"))
    assert ui["services"][state.service_id]["last_active_session"] == "sid-x"
    assert "last_active_session" not in ui["device"]["settings"]
    assert app_state.AppState().get_setting("last_active_session") == "sid-x"


def test_update_settings_writes_each_side_it_touched(app_state):
    state = app_state.AppState()
    state.update_settings({"window_width": 900, "window_height": 600})
    assert app_state._ui_state_file().exists()
    assert not app_state._STATE_FILE.exists()
    state.update_settings({"welcome_seen": True, "font": "Mono 11"})
    service, ui = _files(app_state)
    assert service["settings"]["welcome_seen"] is True
    assert ui["device"]["settings"]["font"] == "Mono 11"
    assert ui["device"]["settings"]["window_width"] == 900


def test_a_forward_carries_the_device_records_too(app_state):
    state = app_state.AppState()
    state.set_panel_layout("old", {"mode": "bottom"})
    state.set_editor_state("old", {"open": True, "files": []})
    state.forward_session("old", "new")
    fresh = app_state.AppState()
    assert fresh.get_panel_layout("new") == {"mode": "bottom"}
    assert fresh.get_editor_state("new") == {"open": True, "files": []}
    assert fresh.resolve_forward("old") == "new"


# -- what ui-state.json may hold ------------------------------------------


def _write_ui(app_state, data) -> None:
    app_state._CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    app_state._ui_state_file().write_text(json.dumps(data), encoding="utf-8")


def test_ui_state_drops_what_does_not_fit(app_state):
    _write_ui(app_state, {
        "device": {"settings": {"sidebar_width": 333}},
        "connections": "nope",
        "client_id": "short",
        "services": {
            "not-an-id": {"panel_layout": {"sid": {"mode": "bottom"}}},
            "0" * 32: {"panel_layout": {"sid": "bad", "ok": {"mode": "right"}}, "open_tabs": "x"},
        },
    })
    ui = uistate.UiState(app_state._ui_state_file(), app_state.device_defaults())
    assert ui.settings["sidebar_width"] == 333
    assert ui.present_keys == {"sidebar_width"}
    assert ui.connections == []
    assert uistate._is_id(ui.client_id) and ui.client_id != "short"
    assert ui.known_services() == ["0" * 32]
    assert ui.has_service("0" * 32) and not ui.has_service("1" * 32)
    assert ui.service("0" * 32)["panel_layout"] == {"ok": {"mode": "right"}}
    assert ui.service("0" * 32)["open_tabs"] == []


def test_an_unknown_ui_key_stays_on_the_ui_side(app_state):
    _write_ui(app_state, {"device": {"settings": {"future_device_knob": 7}}})
    state = app_state.AppState()
    assert state.get_setting("future_device_knob") == 7
    state.set_setting("welcome_seen", True)
    state.set_setting("sidebar_width", 500)
    service, ui = _files(app_state)
    assert "future_device_knob" not in service["settings"]
    assert ui["device"]["settings"]["future_device_knob"] == 7


def test_a_service_key_in_ui_state_does_not_override_the_service(app_state):
    _write_ui(app_state, {"device": {"settings": {"title_model": "smuggled"}}})
    state = app_state.AppState()
    state.set_setting("git_log_page", 30)
    assert state.get_setting("title_model") == DEFAULT_SETTINGS["title_model"]
    state.set_setting("sidebar_width", 500)
    service, ui = _files(app_state)
    assert service["settings"]["title_model"] == DEFAULT_SETTINGS["title_model"]
    assert "title_model" not in ui["device"]["settings"]


def test_a_corrupt_ui_state_is_copied_aside_before_it_is_overwritten(app_state):
    app_state._CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    app_state._ui_state_file().write_text("{broken", encoding="utf-8")
    state = app_state.AppState()
    assert state.get_setting("sidebar_width") == DEFAULT_SETTINGS["sidebar_width"]
    state.set_setting("sidebar_width", 510)
    aside = app_state._CONFIG_DIR / "ui-state.json.corrupt"
    assert aside.read_text(encoding="utf-8") == "{broken"
    ui = json.loads(app_state._ui_state_file().read_text(encoding="utf-8"))
    assert ui["device"]["settings"]["sidebar_width"] == 510
    state.set_setting("sidebar_width", 520)  # a second save does not copy again
    assert aside.read_text(encoding="utf-8") == "{broken"


# -- the quit setting's ask -> detach move (PR-1.12c, D30) ----------------------


def _write_ui_settings(app_state, settings: dict) -> None:
    _write_ui(app_state, {"device": {"settings": settings}})


def test_the_new_quit_settings_are_this_devices():
    assert {"quit_notice_shown", "quit_detach_migrated"} <= DEVICE_SETTINGS
    assert DEFAULT_SETTINGS["quit_with_running_sessions"] == "detach"
    assert DEFAULT_SETTINGS["quit_notice_shown"] is False
    assert DEFAULT_SETTINGS["quit_detach_migrated"] is False


def test_a_stored_ask_moves_to_detach_once(app_state):
    _write_ui_settings(app_state, {"quit_with_running_sessions": "ask"})
    state = app_state.AppState()
    assert state.get_setting("quit_with_running_sessions") == "detach"
    state.set_setting("sidebar_width", 300)  # any device write saves the move
    ui = json.loads(app_state._ui_state_file().read_text(encoding="utf-8"))
    assert ui["device"]["settings"]["quit_with_running_sessions"] == "detach"
    assert ui["device"]["settings"]["quit_detach_migrated"] is True
    # Once: an "ask" chosen afterwards stays.
    state.set_setting("quit_with_running_sessions", "ask")
    assert app_state.AppState().get_setting("quit_with_running_sessions") == "ask"


def test_the_other_quit_values_are_not_moved(app_state):
    for value in ("exit", "background", "hide"):
        _write_ui_settings(app_state, {"quit_with_running_sessions": value})
        assert app_state.AppState().get_setting("quit_with_running_sessions") == value


def test_a_v014_file_migrates_its_ask_to_detach(app_state, v014):
    state = app_state.AppState(migrate=True)
    assert state.get_setting("quit_with_running_sessions") == "detach"
    _service, ui = _files(app_state)
    assert ui["device"]["settings"]["quit_with_running_sessions"] == "detach"
    assert ui["device"]["settings"]["quit_detach_migrated"] is True


def test_the_mirror_moves_a_stored_ask_too(app_state, tmp_path):
    from collins.remotestate import RemoteState

    class _Link:
        def on(self, *_args):
            pass

    ui_path = tmp_path / "client-ui.json"
    ui_path.write_text(
        json.dumps({"device": {"settings": {"quit_with_running_sessions": "ask"}}}),
        encoding="utf-8",
    )
    ui = uistate.UiState(ui_path, app_state.device_defaults())
    state = RemoteState(_Link(), ui=ui)
    assert state.get_setting("quit_with_running_sessions") == "detach"
    assert state.get_setting("quit_detach_migrated") is True
