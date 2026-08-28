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
