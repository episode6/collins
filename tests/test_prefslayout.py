# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The preferences page's shape (collins.prefslayout): the settings that
spend the user's Claude quota sit together, directly under General."""

from collins import prefslayout, tokenrefresh
from collins.state import DEFAULT_SETTINGS, SERVICE_SETTINGS


def test_token_use_sits_directly_under_general():
    groups = prefslayout.GROUPS
    assert groups.index("token_use") == groups.index("general") + 1


def test_mcp_tools_follow_token_use():
    # The tools' definitions ride in every session's context; the group
    # belongs beside the spenders, not at the bottom of the page.
    groups = prefslayout.GROUPS
    assert groups.index("mcp_tools") == groups.index("token_use") + 1


def test_the_page_starts_with_the_cli_rows():
    assert prefslayout.GROUPS[:2] == ("cli", "general")


def test_every_group_has_its_settings_and_each_setting_one_group():
    assert set(prefslayout.GROUP_SETTINGS) == set(prefslayout.GROUPS)
    seen: dict[str, str] = {}
    for group, keys in prefslayout.GROUP_SETTINGS.items():
        for key in keys:
            assert key in DEFAULT_SETTINGS, (group, key)
            assert key not in seen, (key, seen.get(key), group)
            seen[key] = group


def test_a_mixed_group_has_rows_on_both_sides_and_the_others_on_one():
    for group, side in prefslayout.GROUP_SIDES.items():
        halves = {
            "service" if key in SERVICE_SETTINGS else "device"
            for key in prefslayout.GROUP_SETTINGS[group]
        }
        assert halves == ({"service", "device"} if side == "mixed" else {side}), group


def test_the_two_pages_cover_the_groups_and_share_the_mixed_ones():
    device = set(prefslayout.page_groups("device"))
    service = set(prefslayout.page_groups("service"))
    assert device | service == set(prefslayout.GROUPS)
    assert device & service == {
        g for g, side in prefslayout.GROUP_SIDES.items() if side == "mixed"
    }
    assert prefslayout.page_groups("device") == (
        "general", "sessions", "notifications", "composer", "terminal", "footer_apps",
        "pull_requests", "git", "caffeine", "editor",
    )
    assert prefslayout.page_groups("service") == (
        "cli", "general", "token_use", "mcp_tools", "sessions", "sandbox", "pull_requests", "git",
    )


def test_the_token_use_promise_holds_on_the_service_page():
    assert prefslayout.page_groups("service")[:3] == ("cli", "general", "token_use")


def test_git_directly_follows_pull_requests_on_both_pages():
    for side in ("device", "service"):
        groups = prefslayout.page_groups(side)
        assert groups.index("git") == groups.index("pull_requests") + 1, side


def test_no_such_page():
    import pytest

    with pytest.raises(ValueError):
        prefslayout.page_groups("both")


def test_pr_and_session_rows_are_where_the_dialog_has_them():
    # Part 1 had these four wrong (D61): the dialog's rows decide.
    assert "pr_title_sessions" in prefslayout.GROUP_SETTINGS["pull_requests"]
    assert "attach_prompt_prs" in prefslayout.GROUP_SETTINGS["pull_requests"]
    assert "worktree_new_sessions" in prefslayout.GROUP_SETTINGS["sessions"]
    assert "dock_attachments_when_room" in prefslayout.GROUP_SETTINGS["sessions"]


def test_group_names_are_unique():
    assert len(set(prefslayout.GROUPS)) == len(prefslayout.GROUPS)


def test_token_use_holds_the_four_rows_in_order():
    assert prefslayout.TOKEN_USE_ROWS == (
        "title_model",
        "icon_model",
        "auto_renew_login",
        "model_list",
    )


def test_every_token_use_setting_has_a_default():
    # All but the status row write a setting; each has to exist so a fresh
    # install's rows open on a value.
    for key in prefslayout.TOKEN_USE_ROWS:
        if key != "model_list":
            assert key in DEFAULT_SETTINGS, key


def test_the_renew_row_writes_tokenrefreshs_setting():
    assert tokenrefresh.SETTING in prefslayout.TOKEN_USE_ROWS


def test_sandbox_follows_sessions_then_notifications_then_composer():
    # The sandbox is a way of starting sessions, so its switches sit right
    # under the session behaviour they qualify.
    groups = prefslayout.GROUPS
    assert groups.index("sandbox") == groups.index("sessions") + 1
    assert groups.index("notifications") == groups.index("sandbox") + 1
    assert groups.index("composer") == groups.index("notifications") + 1


def test_every_sandbox_setting_has_its_default():
    for key in prefslayout.SANDBOX_ROWS:
        # Two rows write no setting of their own name: the status row, and
        # the expander of per-tool switches ("sandbox_tool_<name>").
        if key not in ("status", "sandbox_tools"):
            assert key in DEFAULT_SETTINGS, key
    assert prefslayout.SANDBOX_ROWS[-2:] == ("sandbox_tools", "status")
    # The expander's rows follow the switches of the Built-in MCP tools
    # group (a tool off for every session is off inside a box, and its row
    # says so), which the dialog therefore has to have built first.
    assert prefslayout.GROUPS.index("mcp_tools") < prefslayout.GROUPS.index("sandbox")
    offered = sorted(
        key[len("sandbox_tool_"):]
        for key, on in DEFAULT_SETTINGS.items()
        if key.startswith("sandbox_tool_") and on is True
    )
    assert offered == [
        "attach_pr", "notify_user", "open_in_editor", "set_session_title", "show_diff",
        "show_image",
    ]
    assert DEFAULT_SETTINGS["sandbox_new_sessions"] is False
    # Off until asked for: with no prompt the box is the only barrier.
    assert DEFAULT_SETTINGS["sandbox_bypass_permissions"] is False
    assert DEFAULT_SETTINGS["sandbox_share_gh"] is False
    assert DEFAULT_SETTINGS["sandbox_share_ssh"] is False
    assert DEFAULT_SETTINGS["sandbox_settings_editable"] is False
    for word in ("sandbox", "bubblewrap", "bwrap", "isolation", "yolo", "permissions", "gh", "ssh"):
        assert word in prefslayout.SANDBOX_SEARCH_TERMS


def test_every_notification_setting_has_a_default():
    for key in (
        "inapp_notifications",
        "notification_color_scheme",
        "notification_sound",
        "bell_notifications",
        "announce_finished_runs",
    ):
        assert key in DEFAULT_SETTINGS, key


def test_the_update_check_setting_has_a_default():
    from collins import updatecheck

    assert updatecheck.SETTING == "check_for_updates"
    assert DEFAULT_SETTINGS[updatecheck.SETTING] is True


def test_git_follows_pull_requests():
    # The git page's knobs sit beside the other page's, above the two
    # groups nobody adjusts twice.
    groups = prefslayout.GROUPS
    assert groups.index("git") == groups.index("pull_requests") + 1


def test_every_git_setting_has_its_default():
    # The diff view's layout and three knobs, untracked files shown, twenty
    # commits a page, and the parent branch left to the page to work out.
    assert DEFAULT_SETTINGS["git_layout"] == "auto"
    assert DEFAULT_SETTINGS["git_untracked"] is True
    assert DEFAULT_SETTINGS["git_log_page"] == 20
    assert DEFAULT_SETTINGS["git_parent_branch"] == ""
    assert DEFAULT_SETTINGS["git_line_numbers"] is True
    assert DEFAULT_SETTINGS["git_wrap_lines"] is False
    assert DEFAULT_SETTINGS["git_word_diff"] is True
    assert DEFAULT_SETTINGS["git_hide_whitespace"] is False
    # The terminal viewer's theme and the viewer switch went with it
    # (decisions 1 and 3 of the native-diff spec).
    assert "git_theme" not in DEFAULT_SETTINGS
    assert "git_viewer" not in DEFAULT_SETTINGS
    assert not hasattr(prefslayout, "GIT_VIEWERS")
    for word in ("wrap", "line numbers", "word", "layout", "whitespace"):
        assert word in prefslayout.GIT_SEARCH_TERMS
    for word in ("hunk", "theme", "viewer", "experimental"):
        assert word not in prefslayout.GIT_SEARCH_TERMS


def test_git_layouts_are_gitloads_words():
    # The values are gitloads.LAYOUTS verbatim, and the default is first so
    # an unknown stored value falls back to it.
    assert [value for value, _label in prefslayout.GIT_LAYOUTS] == ["auto", "split", "stack"]
    assert DEFAULT_SETTINGS["git_layout"] == prefslayout.GIT_LAYOUTS[0][0]
