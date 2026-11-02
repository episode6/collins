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

The Notifications group's search words live here for the same reason: the
words someone types for a notification setting ("chime", "badge") are not
in any row's own text, and the unit suite can hold the list to the spec's
without a dialog. The Git group's layouts and search words likewise: the
layout values are gitloads.LAYOUTS' words, and the two must not drift
apart.
"""

from __future__ import annotations

from . import mcptools
from .api import protocol

# The page's groups, top to bottom. "cli" is the untitled pair of rows above
# everything (which claude Collins runs); the rest are titled groups.
GROUPS: tuple[str, ...] = (
    "cli",
    "general",
    "token_use",
    "mcp_tools",
    "sessions",
    "sandbox",
    "notifications",
    "composer",
    "terminal",
    "footer_apps",
    "pull_requests",
    "git",
    "caffeine",
    "editor",
)

# The family a whole group's rows belong to (the server-side spec's D5): a
# service that does not list it has no such group, on either page. The rest are
# always there, with the odd row of theirs asking for itself (prefs.py).
GROUP_FAMILIES: dict[str, str] = {
    "cli": protocol.CAP_INTERNAL,
    "token_use": protocol.CAP_USAGE,
    "mcp_tools": protocol.CAP_TOOLS,
    "sandbox": protocol.CAP_SANDBOX,
    "pull_requests": protocol.CAP_PRS,
    "git": protocol.CAP_GIT,
}

# Which side of the state split each group's settings live on (the
# service-and-client spec, §3.8; state.SERVICE_SETTINGS and DEVICE_SETTINGS
# are the per-key lists): "service" for a group whose every row writes a
# setting the Collins service reads, "device" for one whose rows are all
# this device's, and "mixed" for the groups that hold both. The dialog is two
# pages, This device and Service (PR-3.3, D61): a group is on the page of
# its side, and a mixed group is two groups of one title, one per page
# (page_groups says which). Pinned by tests/test_state_split.py.
GROUP_SIDES: dict[str, str] = {
    "cli": "service",
    "general": "mixed",
    "token_use": "service",
    "mcp_tools": "service",
    "sessions": "mixed",
    "sandbox": "service",
    "notifications": "mixed",
    "composer": "device",
    "terminal": "device",
    "footer_apps": "device",
    "pull_requests": "mixed",
    "git": "mixed",
    "caffeine": "device",
    "editor": "device",
}

# The settings each group's rows write (the service-and-client spec, §3.24;
# PR-3.3), by group, in no particular order. Prefs builds a group from the
# rows whose setting is listed here, and a "mixed" group (GROUP_SIDES) is
# two groups of one title, one per page: the rows whose setting is in
# state.SERVICE_SETTINGS on the Service page, the rest on This device.
# tests/test_state_split.py pins every group's side against
# state.SERVICE_SETTINGS / DEVICE_SETTINGS, so a setting cannot sit in a
# group on the wrong side of the split, and check_token_use_prefs.py holds
# the dialog's rows to it (every row names its settings: prefs.py's
# _SearchableGroup.add).
GROUP_SETTINGS: dict[str, tuple[str, ...]] = {
    "cli": ("claude_cli_path",),
    "general": ("language", "color_scheme", "status_icon", "show_folder_path",
                "project_icon_size", "panel_tab_drag_handles", "clone_directory",
                "check_for_updates", "show_usage_panel"),
    "token_use": ("title_model", "icon_model", "auto_renew_login"),
    "mcp_tools": tuple(mcptools.default_tool_settings()),
    "sessions": ("archive_running_session", "quit_with_running_sessions", "restore_last_session",
                 "archive_worktree", "archive_on_claude_ai", "auto_delete_archived_after",
                 "auto_delete_archived_unit", "cli_title_sessions",
                 "background_status_poll", "progress_termprop", "worktree_new_sessions",
                 "dock_attachments_when_room"),
    "sandbox": ("sandbox_new_sessions", "sandbox_bypass_permissions", "sandbox_share_gh",
                "sandbox_share_ssh", "sandbox_settings_editable",
                *mcptools.default_sandbox_tool_settings()),
    "notifications": ("inapp_notifications", "notification_color_scheme", "notification_sound",
                      "bell_notifications", "announce_finished_runs", "notify_command"),
    "composer": ("composer_enter_sends", "composer_on_typing", "composer_spell_click",
                 "attach_overlay_button"),
    "terminal": ("font", "scrollback", "terminal_theme", "terminal_max_width", "easy_copy_paste"),
    "footer_apps": ("footer_apps",),
    "pull_requests": ("pr_font_scale", "pr_inline_images", "open_pr_panel_on_attach",
                      "confirm_merges", "refresh_prs_on_launch", "attach_prompt_prs",
                      "pr_title_sessions"),
    "git": ("git_layout", "git_line_numbers", "git_wrap_lines", "git_word_diff",
            "git_hide_whitespace", "git_untracked", "git_log_page", "git_parent_branch"),
    "caffeine": ("caffeine_on_launch", "caffeine_launch_timer", "caffeine_idle_grace_minutes",
                 "caffeine_keep_screen_on"),
    "editor": ("editor_style_scheme", "editor_font", "editor_show_line_numbers",
               "editor_show_hidden_files", "editor_narrow_width", "editor_pop_out_screen_width"),
}


def page_groups(side: str) -> tuple[str, ...]:
    """The groups of one page of the dialog, in GROUPS order: "device" (This
    device) or "service" (Service). A side's own groups and every mixed one,
    which are on both pages."""
    if side not in ("device", "service"):
        raise ValueError(f"no such page: {side!r}")
    return tuple(g for g in GROUPS if GROUP_SIDES[g] in (side, "mixed"))


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

# What the search bar matches the Notifications group on, beyond its rows'
# own titles and subtitles: the names people have for the thing.
NOTIFICATION_SEARCH_TERMS: tuple[str, ...] = (
    "notification",
    "notify",
    "bell",
    "sound",
    "chime",
    "badge",
    "unread",
)

# What the search bar matches the Sandbox group on, beyond its rows' own
# titles and subtitles: the tool underneath, and the words people have for
# what the box is for.
SANDBOX_SEARCH_TERMS: tuple[str, ...] = (
    "sandbox",
    "bubblewrap",
    "bwrap",
    "isolation",
    "yolo",
    "permissions",
    "gh",
    "ssh",
)

# The Sandbox group's rows, top to bottom, by the setting each one writes;
# "sandbox_tools" is the expander holding one switch per session tool
# ("sandbox_tool_<name>", what a sandboxed session is offered by default),
# and "status" the row under them that writes nothing (whether a box can
# be built here, and why not).
SANDBOX_ROWS: tuple[str, ...] = (
    "sandbox_new_sessions",
    "sandbox_bypass_permissions",
    "sandbox_share_gh",
    "sandbox_share_ssh",
    "sandbox_settings_editable",
    "sandbox_tools",
    "status",
)

# The Git group's Layout row, in the drop-down's order: the diff view's
# layouts and their labels (N_-style — prefs translates them at use).
# gitloads.LAYOUTS holds the same three, and a fourth would have to land in
# both.
GIT_LAYOUTS: tuple[tuple[str, str], ...] = (
    ("auto", "Automatic"),
    ("split", "Split"),
    ("stack", "Stacked"),
)

# What the search bar matches the Git group on, beyond its rows' own titles
# and subtitles: the tool's name, and the words people have for its parts.
GIT_SEARCH_TERMS: tuple[str, ...] = (
    "git",
    "diff",
    "branch",
    "parent",
    "untracked",
    "commits",
    "layout",
    "line numbers",
    "wrap",
    "word",
    "whitespace",
)
