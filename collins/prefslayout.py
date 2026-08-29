# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The shape of the preferences page, as data: which groups, in what order,
and which rows the Token use group holds.

Kept free of GTK (like prefssearch) so the unit suite can hold the layout
to its promises — prefs.py builds its groups by walking GROUPS, and
tokensettings.build_token_rows returns its rows in TOKEN_USE_ROWS order, so
neither can drift from this file without failing tests/test_prefslayout.py.

The one promise worth a test: the settings that spend the user's Claude
quota sit together, directly under General, where a first look at
Preferences finds them — not scattered across three groups with one of
them at the bottom of the page, which is where they used to live.
"""

from __future__ import annotations

# The page's groups, top to bottom. "cli" is the untitled pair of rows above
# everything (which claude Collins runs); the rest are titled groups.
GROUPS: tuple[str, ...] = (
    "cli",
    "general",
    "token_use",
    "mcp_tools",
    "sessions",
    "composer",
    "terminal",
    "footer_apps",
    "pull_requests",
    "caffeine",
    "editor",
)

# The Token use group's rows, top to bottom, by the setting each one writes.
# "model_list" is the exception: the status row under the two pickers writes
# nothing (it dates the cached catalog and carries the Refresh button), and
# is listed under that name so the row order is whole.
TOKEN_USE_ROWS: tuple[str, ...] = (
    "title_model",
    "icon_model",
    "auto_renew_login",
    "model_list",
)
