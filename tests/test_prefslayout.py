# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The preferences page's shape (collins.prefslayout): the settings that
spend the user's Claude quota sit together, directly under General."""

from collins import prefslayout, tokenrefresh
from collins.state import DEFAULT_SETTINGS


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


def test_notifications_sit_between_sessions_and_composer():
    groups = prefslayout.GROUPS
    assert groups.index("notifications") == groups.index("sessions") + 1
    assert groups.index("composer") == groups.index("notifications") + 1


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
    # hunk's own defaults for what reaches hunk, twenty commits a page, and
    # the parent branch left to the page to work out.
    assert DEFAULT_SETTINGS["git_layout"] == "auto"
    assert DEFAULT_SETTINGS["git_theme"] == ""
    assert DEFAULT_SETTINGS["git_untracked"] is True
    assert DEFAULT_SETTINGS["git_log_page"] == 20
    assert DEFAULT_SETTINGS["git_parent_branch"] == ""


def test_git_layouts_are_hunks_mode_words():
    # The values go to hunk's --mode verbatim, and the default is first so
    # an unknown stored value falls back to it.
    assert [value for value, _label in prefslayout.GIT_LAYOUTS] == ["auto", "split", "stack"]
    assert DEFAULT_SETTINGS["git_layout"] == prefslayout.GIT_LAYOUTS[0][0]
