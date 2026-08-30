# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-30. Full change history: git log for this file.

"""Persistent app state: custom names, favorites, archived sessions, settings.

Everything lives in our own config file — the agents' session data is never
modified.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
from collections.abc import Iterable
from pathlib import Path

from . import mcptools, newchat, notifycenter, panelhistory, panellayout
from .claudemodels import NO_MODEL

_CONFIG_BASE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
_CONFIG_DIR = _CONFIG_BASE / "collins"
# Pre-rebrand locations, newest first (collins ← agent-session-manager ← claude-session-manager).
_OLD_CONFIG_DIRS = [
    _CONFIG_BASE / "agent-session-manager",
    _CONFIG_BASE / "claude-session-manager",
]
_STATE_FILE = _CONFIG_DIR / "state.json"
_LEGACY_NAMES_FILE = _CONFIG_BASE / "claude-session-manager" / "names.json"


def _migrate_old_config() -> None:
    """One-time: carry settings/names over from the old config dir names.

    Copy only — the old dirs are never modified or removed, so the pre-rebrand
    apps keep working side by side with their own (from then on independent)
    state.
    """
    if _STATE_FILE.exists():
        return
    for old_dir in _OLD_CONFIG_DIRS:
        old_state = old_dir / "state.json"
        if old_state.exists():
            _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old_state, _STATE_FILE)
            return

DEFAULT_SETTINGS = {
    "font": "",  # empty = VTE default
    # Rebound shortcuts: action name → list of GTK accelerator strings
    # (empty = unbound); an action not listed keeps its default. The
    # catalogue of actions and defaults is keybindings.BINDINGS.
    "keybindings": {},
    "scrollback": 10_000,
    "color_scheme": "system",  # system | light | dark
    "terminal_theme": "Default",  # VTE color palette (see themes.py)
    "terminal_max_width": 1200,  # px; terminal stops growing past this and centers (0 = no limit)
    "easy_copy_paste": True,  # Ctrl+C copies the selection (else SIGINT), Ctrl+V pastes, right-click menu
    "language": "",  # UI language code; "" = follow the system locale
    "background_status_poll": False,  # timed-poll fallback for the yellow "running detached" lines
    # Experimental: drive the sidebar's busy pole from the agent CLI's own
    # OSC 9;4 progress announcements (see activity.ProgressWatch), coaxed out
    # of it with two env vars on each agent tab's shell (see terminal.py's
    # _agent_tab_environment). Off = the inferred sources alone, as before;
    # the env half only takes effect for tabs opened after a change.
    "progress_termprop": True,
    # Read each new session's first prompt for pull request references and
    # attach every PR it names to the session's row (see prattach.py).
    "attach_prompt_prs": True,
    # Claude models for the app's own headless runs, as --model values.
    # "" = automatic: the newest model of the setting's preferred tier, or
    # the weakest model offered should that tier ever be dropped (see
    # claudemodels.resolve_model). NO_MODEL = the picker's None: the feature
    # runs nothing by itself — new sessions keep the free local title (the
    # first words of their prompt), and the Generate Icon dialog waits for a
    # model to be picked. It replaced the auto_title_sessions switch, which
    # _load migrates: false became title_model = NO_MODEL.
    "title_model": "",  # session title generation ("" = newest Haiku)
    "icon_model": NO_MODEL,  # the sidebar's Generate Icon ("" = newest Sonnet)
    # Repair an expired CLI login with one throwaway headless run — at
    # launch, or when a usage fetch is refused mid-run (see tokenrefresh).
    # Off, the usage panel just says the login expired and leaves running
    # `claude` to the user. The third of the Token use rows in Preferences.
    "auto_renew_login": True,
    # Retitle a session to its newest pull request's title as PRs are
    # detected (see SessionStore.apply_pr_title). Fills the generated-name
    # slot, so a manual rename always wins.
    "pr_title_sessions": False,
    # Follow the agent CLI's own session names: the titles it generates for
    # itself and /rename renames, read off each transcript's title records
    # as they land. Display-only — the names are recorded either way (see
    # AppState.cli_titles) and this switch decides whether display_name
    # prefers them; a manual rename in Collins still wins.
    "cli_title_sessions": False,
    # Run the sidebar's PR sweep once, shortly after launch, so the marks
    # restored from the last run are replaced by current ones without the
    # refresh button being clicked (see MainWindow._schedule_launch_sweep).
    "refresh_prs_on_launch": True,
    # Launch new sessions with the agent CLI's worktree flag (claude -w) in
    # git projects, isolating their edits from the live checkout. Per-project
    # overrides live in AppState.project_worktree.
    "worktree_new_sessions": False,
    # Where the Claude Code CLI lives when PATH doesn't say — desktop
    # launches don't get the folders a shell adds (see clisetup). Stored
    # exactly as picked, symlinks unexpanded, so the installer's stable
    # launcher survives the CLI's self-updates. "" = rely on PATH alone.
    "claude_cli_path": "",
    # Whether the "better with the GitHub CLI" notice has been waved off for
    # good. Set only by its own "Don't show this again" box — until that is
    # ticked it appears on every launch that finds gh missing or signed out,
    # and it never appears on a launch that doesn't (see ghwelcome).
    "gh_welcome_dismissed": False,
    # Whether the first-launch welcome — what runs Claude on the user's
    # behalf, with the switch for each, and the CLI's location when that
    # needs asking — has been answered (see welcome). Set by every answer
    # but Quit. An install that predates the key sees the dialog once too:
    # the disclosure is as new to it as to a fresh one, by design.
    "welcome_seen": False,
    # What to do instead of the confirmation dialog when a running session's
    # tab has to close: ask (the dialog, as before) | exit | background.
    # quit_with_running_sessions takes a fourth value, hide: the window (not
    # its sessions) goes away, recoverable from the status icon — see
    # MainWindow._hide_window.
    "archive_running_session": "ask",  # archiving a session whose tab is busy
    "quit_with_running_sessions": "ask",  # closing a window while sessions run
    # Whether the one-time first-hide notice has gone out: the desktop
    # notification saying "Collins is still running" the first time a window
    # hides instead of closing, so nobody mistakes the hide for a quit. Set
    # when the notice is sent — once per install, like gh_welcome_dismissed
    # (see MainWindow._maybe_show_hide_notice).
    "hide_notice_shown": False,
    # Mirror the archive toggle to claude.ai: a session that has a page there
    # (it was remote-controlled, so its transcript names a remote session id)
    # is archived and restored there too, best-effort on a background thread —
    # the local toggle never waits on it and never fails with it (see
    # remotearchive.py). On by default; sessions with no remote page cost one
    # transcript scan and no network.
    "archive_on_claude_ai": True,
    # The session tab bar under the header. Hidden by default: the sidebar is
    # the intended way to move between sessions (the window title names the
    # active one), and the tabs keep working underneath; the header's own
    # toggle shows the bar for anyone who wants it.
    "show_tab_bar": False,
    # A StatusNotifierItem in the top bar: presence, and a menu that jumps to
    # any open session (see statusicon.py). On by default — an icon nobody
    # knows to turn on isn't presence — and free on a desktop with no host for
    # one, where it never registers at all.
    "status_icon": True,
    # The in-app notification card (see notifyoverlay.py): a message from a
    # session that isn't the one on screen, shown inside the window while
    # Collins is focused. Off sends every notification to the desktop, as
    # before there were cards; the history and the badge are unaffected.
    "inapp_notifications": True,
    # What the card plays (see notifysound.py): "default" is the desktop's
    # own message sound, resolved at play time (notifycenter.sound_file),
    # "none" is silence, anything else an absolute path to a sound file.
    "notification_sound": "default",
    # A terminal bell from a session the user isn't looking at posts a
    # notification (card or desktop, by focus) and plays the sound; off
    # keeps the compositor's beep for every bell. The selected tab's bell
    # is the beep either way — a bell you were there for is not history.
    "bell_notifications": True,
    # Also notify when a session's run finishes, not only when it asks:
    # the finished run's synthetic row goes out as a message would (a card
    # when elsewhere in Collins, a desktop notification when unfocused).
    # Off by default: the docs promise nothing is guessed from a quiet
    # terminal, and this is the switch for whoever wants a chime anyway.
    "announce_finished_runs": False,
    # Once a day, ask GitHub's public releases API (anonymously — no token,
    # no gh login) whether a newer Collins is out, and notify about it once:
    # a card in Collins, a desktop notification away from it, and a history
    # row whose click opens the release page (see updatecheck.py). On by
    # default; off asks nothing.
    "check_for_updates": True,
    # The in-app card's own light/dark (notifycenter.CARD_SCHEMES): "app"
    # paints it in whatever the app is, "light" and "dark" pin it — a dark
    # card over a light window reads the way a desktop notification does,
    # and the other way round. Only the card; the desktop's are its own.
    "notification_color_scheme": "app",  # app | light | dark
    # The floating composer button over each agent terminal's bottom-left
    # corner (see terminal.py; it was the attach-file button once, and keeps
    # that key so saved preferences carry over). Off hides it everywhere;
    # drag-and-drop onto the terminal and the editor's "Add to chat" keep
    # working either way.
    "attach_overlay_button": True,
    # Whether Enter sends the composer's text (Shift+Enter for a newline);
    # off swaps the pair: Enter is a newline and Ctrl+Enter sends.
    "composer_enter_sends": True,
    # What a session Collins starts fresh opens its composer as, off by
    # default: "off" | "float" (raised over the terminal, as Ctrl+. does) |
    # "dock" (a panel page below it, which joins the session's saved layout
    # and so comes back on later resumes). Only new sessions — a resumed one
    # is left to the layout it was closed with (see composerkeys.autoshow_mode
    # and TerminalTab.autoshow_composer).
    "composer_new_sessions": "off",
    # Whether typing at an agent's empty input box raises the composer and
    # takes the character with it, so a prompt is written in the composer by
    # default and in the CLI's box only on purpose. On by default. Only an
    # *empty* box is typed away from — a permission dialog, a menu and a
    # half-written line all keep the keys, and an agent mid-turn doesn't
    # (its box is empty, and composing over a working agent is the point)
    # — see composerkeys.typing_opens_composer and
    # TerminalTab._typing_opens_composer.
    "composer_on_typing": True,
    # Whether right-clicking a misspelled word in the composer offers
    # corrections for *that* word. libspelling reads them from the text
    # cursor, which a right-click doesn't move, so left alone the menu
    # answers about wherever the caret sat -- usually nothing. On by
    # default; off restores that, for anyone who would rather the caret
    # never moved under a right-click. Ignored where libspelling is older
    # than 0.4 and can't rebuild the menu in time -- see
    # ComposerView.__init__.
    "composer_spell_click": True,
    "show_folder_path": False,  # show each session's project folder path in the sidebar
    "project_icon_size": 16,  # px size of the sidebar's project/folder (and group) icons
    "show_usage_panel": True,  # Claude subscription usage bars under the session list
    "usage_panel_collapsed": False,  # usage panel folded down to its heading line
    "footer_apps": [],  # desktop-file IDs of apps launchable from each tab's footer
    # Whether Caffeine Mode holds the screen on too (idle inhibit) or only
    # keeps the computer from suspending, leaving the screen free to blank.
    "caffeine_keep_screen_on": True,
    "caffeine_on_launch": False,  # start with Caffeine Mode on (see app.py's inhibitor)
    "caffeine_launch_timer": "active",  # shut-off timer armed at launch (see caffeine.py)
    # Minutes the Until-idle mode keeps holding the machine awake after the
    # last session stops working, before it dozes (see caffeine.grace_seconds).
    "caffeine_idle_grace_minutes": 5,
    "sidebar_width": 300,  # persisted sidebar pane width in px
    "panel_position": "bottom",  # secondary terminal panel placement: bottom | right
    # Panel tabs drag by their own handle (join/reorder/split via the drop
    # zones). Rides private libadwaita widget internals, so off falls back
    # to native tab dragging plus each strip's drag grip (see paneldnd).
    "panel_tab_drag_handles": True,
    "panel_size_bottom": 0,  # last-set panel height in px (0 = default fraction)
    "panel_size_right": 0,  # last-set panel width in px (0 = default fraction)
    # The same, for the strip docked *pages* open into — PR views, the
    # attachments list, a docked composer — which is a different strip from
    # the shells' Ctrl+J panel above and remembers its own size, so sizing a
    # PR page doesn't move the shell panel (or the other way around). One
    # size per axis, not per kind: those pages share a strip (see
    # paneldock.open_page's join-don't-split rule), so they share its size.
    "page_panel_size_bottom": 0,  # last-set docked-page strip height in px
    "page_panel_size_right": 0,  # last-set docked-page strip width in px
    "window_width": 1280,  # last window size (floating, unmaximized)
    "window_height": 800,
    "window_maximized": False,
    "last_active_session": "",  # session in the active tab when the last window closed
    # Whether a launch reopens last_active_session. Off by default: launching
    # into an old session resumes it, and a resume the user didn't ask for is
    # a surprise. The id above is recorded either way, so switching this on
    # works from the very next launch.
    "restore_last_session": False,
    "editor_width": 0,  # last-set editor panel width in px (0 = default fraction)
    "editor_style_scheme": "",  # GtkSource style scheme id; "" = follow the app's light/dark scheme
    "editor_font": "",  # empty = system monospace
    "editor_show_line_numbers": True,
    "editor_show_hidden_files": True,
    "editor_pop_out_screen_width": 1600,  # scaled px; this wide or narrower opens popped out (0 = never)
    "editor_narrow_width": 500,  # px; a column this wide or narrower shows one column at a time (0 = never)
    # The native PR page's reading-text size, % of the app font. Buttons and
    # menus keep the app size (see prview._apply_font_scale).
    "pr_font_scale": 120,
    # Whether PR bodies render the images they embed (bodyimages.py). On:
    # opening a PR fetches the pictures its description and comments name.
    # Off: they stay alt-text links, and nothing is fetched.
    "pr_inline_images": True,
    # Whether a pull request joining a session opens its page beside that
    # session on its own (see PrStore's pr-attached and TerminalTab's
    # _on_hub_pr_attached). Off by default: it spends the session's panel
    # room without being asked. Once per PR per session — the saved list is
    # what remembers, so a page closed again stays closed.
    "open_pr_panel_on_attach": False,
    # Whether a session's gallery of images docks itself beside that session
    # the first time it shows one (see TerminalTab._consider_attachments_dock).
    # On by default, unlike the PR switch above, because it only ever spends
    # room the terminal wasn't using: it waits for a tab wide enough that a
    # column comes free of the terminal's maximum width
    # (panelsizing.room_for_a_split), and once per tab, so a panel closed
    # again stays closed.
    "dock_attachments_when_room": True,
    # Whether merging a pull request asks first (see practions.confirmation).
    # On: Merge, Merge when checks pass and Merge and archive each put up
    # their dialog, as they always have. Off: the click merges. Only the
    # merges — closing a pull request unmerged still asks, since that is the
    # one PR action that throws the work away rather than landing it.
    "confirm_merges": True,
    "editor_window_width": 1000,  # last popped-out editor window size (floating, unmaximized)
    "editor_window_height": 700,
    "editor_window_maximized": False,
    # One switch per tool Collins offers the sessions it starts (see
    # mcptools.TOOLS), all on: "mcp_tool_<name>". Keyed off the tool table so
    # a new tool can't ship without its switch — off means the tool is left
    # out of what a session is offered, and refused if an older session calls
    # it anyway.
    **mcptools.default_tool_settings(),
}

# Floor for a restored window, so a corrupt/absurd saved value can't produce
# an unusably tiny window.
_MIN_WINDOW_SIZE = (640, 480)


def merge_project_order(saved: list[str], names: Iterable[str]) -> list[str]:
    """Resolve the sidebar display order for `names` against the saved order.

    Names present in `saved` keep their saved relative order; names not yet
    ranked are prepended alphabetically, so a project seen for the first time
    surfaces at the top of the sidebar instead of sinking to the bottom. Saved
    entries for projects that no longer exist are dropped from the result (but
    not from the saved list).
    """
    present = set(names)
    ranked = set(saved)
    ordered = sorted((n for n in present if n not in ranked), key=str.casefold)
    ordered += [n for n in saved if n in present]
    return ordered


def move_in_order(order: list[str], name: str, before: str | None) -> list[str]:
    """Return `order` with `name` moved before `before` (or to the end)."""
    result = [n for n in order if n != name]
    index = result.index(before) if before is not None and before in result else len(result)
    result.insert(index, name)
    return result


def clamp_window_size(width: int, height: int, monitor_sizes: list[tuple[int, int]]) -> tuple[int, int]:
    """Clamp a remembered window size so it fits the available monitors.

    The compositor decides which monitor the window opens on, so each
    dimension is clamped to the largest extent across all monitors.
    """
    if monitor_sizes:
        width = min(width, max(w for w, _h in monitor_sizes))
        height = min(height, max(h for _w, h in monitor_sizes))
    return max(width, _MIN_WINDOW_SIZE[0]), max(height, _MIN_WINDOW_SIZE[1])


def panel_size_key(scope: str, mode: str) -> str:
    """The setting holding one dock strip's app-wide last-set size.

    *scope* is which strip the divider speaks for — "home" for the shells'
    panel, "page" for the strip docked pages (PR views, attachments, the
    docked composer) open into — and *mode* the axis it sits on
    ("bottom" | "right"). The home strip keeps the original key names, so
    a panel size saved before docked pages had their own survives.
    """
    prefix = "page_panel_size" if scope == "page" else "panel_size"
    return f"{prefix}_{mode}"


def editor_pops_out(monitor_width: int, limit: int) -> bool:
    """Whether the editor should open popped out rather than docked: true on
    monitors at most `limit` scaled px wide (the pop-out threshold setting;
    scaled because that's the space windows are actually laid out in — a
    3072-px panel at 2× display scale only has 1536 px for a split).

    A `limit` of 0 means always dock; a `monitor_width` of 0 means the
    monitor couldn't be determined, which also docks — the docked panel is
    the recoverable default (its pop-out button is one click away).
    """
    return 0 < monitor_width <= limit


class AppState:
    def __init__(self) -> None:
        self.names: dict[str, str] = {}
        self.generated_names: dict[str, str] = {}  # auto-generated titles (user names win)
        # The agent CLI's own name per session, as last seen in its
        # transcript. Kept separately from generated_names so the
        # cli_title_sessions switch flips display both ways without
        # touching what either side wrote (see SessionStore.display_name).
        self.cli_titles: dict[str, str] = {}
        self.emojis: dict[str, str] = {}
        self.favorites: set[str] = set()
        self.archived: set[str] = set()
        self.archived_projects: set[str] = set()  # by project name (the group identity)
        # Per-project "new sessions use a worktree" choices, by project name.
        # Absent key = follow the worktree_new_sessions setting.
        self.project_worktree: dict[str, bool] = {}
        self.project_order: list[str] = []  # user-arranged sidebar order, by project name
        # Projects kept in the sidebar after their last session went away
        # (project name -> working directory, "" when it was never known), so
        # deleting sessions doesn't cost you the folder.
        self.virtual_projects: dict[str, str] = {}
        self.expanded_groups: set[str] = set()  # sidebar groups the user expanded
        self.panel_layouts: dict[str, dict] = {}  # per-session dock layout (see panellayout)
        self.editor_states: dict[str, dict] = {}  # per-session editor open/width/files/cursors
        # session id -> the PRs it has opened, oldest first, as prstatus
        # records ({number, url, repository?, title?, state?, checks?,
        # mergeable?, unresolved? — see to_record). The status in one is the
        # last that was fetched, not the current one.
        self.session_prs: dict[str, list] = {}
        # session id -> the images it has seen, newest first, as attachrecords
        # records ({key, kind, source, at, last, remote?, caption?, context?,
        # origin? — see to_record). A log of what the session showed, not a
        # claim the files are still there.
        self.session_attachments: dict[str, list] = {}
        # session id -> the prompt draft that session's composer was holding
        # when nothing could be done with it — a close the agent had already
        # left, or the window going away with the box still full (see
        # TerminalTab._stash_draft). Persisted because a draft is the user's
        # own writing: it outlives the tab, the app, and the machine going
        # to sleep, and comes back the next time that session's composer
        # opens on an empty box.
        self.session_drafts: dict[str, str] = {}
        # draft id -> a new-chat screen the user walked away from with
        # something in it: the prompt being written for a session that hasn't
        # been started, the worktree choice, and the dock layout (see
        # newchat.draft_record). Listed in the sidebar under the draft's
        # project, and opened back onto the same screen; consumed by the Send
        # that starts the session.
        self.new_chat_drafts: dict[str, dict] = {}
        # session id -> cmdlines of the processes the CLI had spawned under
        # itself before anything was ever submitted to it — its MCP servers,
        # plumbing that must not read as "the agent left something running".
        # Captured on tabs Collins spawns fresh, applied by the busy poll on
        # every tab (see MainWindow._poll_process_activity). Persisted because
        # the set is fixed at CLI startup: a tab re-attaching to that same
        # process later can trust it verbatim.
        self.process_baselines: dict[str, list[str]] = {}
        # old session id -> the id its conversation continued under (Claude's
        # /bg has been observed forking a backgrounded session to a fresh
        # background session id; in-place detaches add no entries here).
        self.session_forwards: dict[str, str] = {}
        # /bg detaches whose fork hasn't been identified yet: session id -> the
        # evidence needed to finish the pairing after a restart (see
        # MainWindow._replay_pending_detaches).
        self.pending_detaches: dict[str, dict] = {}
        # The notification history, newest first, as notifycenter records
        # ({id, session_id, title, project, kind, body, when, read, count}).
        # Messages and bells only — a finished run's synthetic row stands for
        # an in-memory flag and never lands here. Cleaned on load (garbage,
        # rows past their fortnight and rows past the cap all go; see
        # notifycenter.clean_records) and written back whole by the center.
        self.notifications: list[dict] = []
        self.settings: dict = dict(DEFAULT_SETTINGS)
        self._load()

    # -- persistence ---------------------------------------------------

    def _load(self) -> None:
        _migrate_old_config()
        data: dict = {}
        try:
            data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # one-time migration from the old names-only store
            try:
                data = {"names": json.loads(_LEGACY_NAMES_FILE.read_text(encoding="utf-8"))}
            except (OSError, json.JSONDecodeError):
                data = {}
        self.names = dict(data.get("names") or {})
        self.generated_names = dict(data.get("generated_names") or {})
        self.cli_titles = dict(data.get("cli_titles") or {})
        self.emojis = dict(data.get("emojis") or {})
        self.favorites = set(data.get("favorites") or [])
        # "hidden"/"hidden_projects" are the pre-rename spellings of the
        # archive keys — read them as a fallback so an existing archive
        # carries over; save() only ever writes the new keys.
        self.archived = set(data.get("archived") or data.get("hidden") or [])
        self.archived_projects = set(
            data.get("archived_projects") or data.get("hidden_projects") or []
        )
        self.project_worktree = {
            k: v for k, v in (data.get("project_worktree") or {}).items() if isinstance(v, bool)
        }
        self.project_order = list(data.get("project_order") or [])
        self.virtual_projects = {
            k: v for k, v in (data.get("virtual_projects") or {}).items() if isinstance(v, str)
        }
        self.expanded_groups = set(data.get("expanded_groups") or [])
        self.panel_layouts = {
            k: v for k, v in (data.get("panel_layout") or {}).items() if isinstance(v, dict)
        }
        # Read-time, one-way migration of the pre-tree "panel_states" shape
        # ({"open", "mode", "sizes"}): each entry becomes the two-node tree
        # for its mode, sized by the shell history files on disk. The old
        # key is dropped on the next save.
        for sid, old in (data.get("panel_states") or {}).items():
            if sid in self.panel_layouts or not isinstance(old, dict):
                continue
            entry = panellayout.from_legacy(old, panelhistory.ordinals(sid))
            if entry:
                self.panel_layouts[sid] = entry
        self.editor_states = {
            k: v for k, v in (data.get("editor_states") or {}).items() if isinstance(v, dict)
        }
        self.session_prs = {
            k: v for k, v in (data.get("session_prs") or {}).items() if isinstance(v, list)
        }
        self.session_attachments = {
            k: v for k, v in (data.get("session_attachments") or {}).items() if isinstance(v, list)
        }
        self.session_drafts = {
            k: v for k, v in (data.get("session_drafts") or {}).items() if isinstance(v, str) and v
        }
        self.new_chat_drafts = {}
        for k, v in (data.get("new_chat_drafts") or {}).items():
            clean = newchat.valid_draft(v)
            if newchat.is_draft_id(k) and clean is not None:
                self.new_chat_drafts[k] = clean
        self.process_baselines = {
            k: v for k, v in (data.get("process_baselines") or {}).items() if isinstance(v, list)
        }
        self.session_forwards = {
            k: v for k, v in (data.get("session_forwards") or {}).items() if isinstance(v, str)
        }
        self.pending_detaches = {
            k: v for k, v in (data.get("pending_detaches") or {}).items() if isinstance(v, dict)
        }
        self.notifications = notifycenter.clean_records(data.get("notifications"))
        settings = dict(data.get("settings") or {})
        # Read-time, one-way migration of the auto_title_sessions switch the
        # title model's None item replaced: off becomes None, on is just the
        # default. Either way the old key goes, dropped on the next save.
        if settings.pop("auto_title_sessions", None) is False:
            settings["title_model"] = NO_MODEL
        self.settings = {**DEFAULT_SETTINGS, **settings}

    def save(self) -> None:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "names": self.names,
            "generated_names": self.generated_names,
            "cli_titles": self.cli_titles,
            "emojis": self.emojis,
            "favorites": sorted(self.favorites),
            "archived": sorted(self.archived),
            "archived_projects": sorted(self.archived_projects),
            "project_worktree": self.project_worktree,
            "project_order": self.project_order,  # order is the payload — never sort
            "virtual_projects": self.virtual_projects,
            "expanded_groups": sorted(self.expanded_groups),
            "panel_layout": self.panel_layouts,
            "editor_states": self.editor_states,
            "session_prs": self.session_prs,  # order is the payload — never sort
            "session_attachments": self.session_attachments,  # newest first; never sort
            "session_drafts": self.session_drafts,
            "new_chat_drafts": self.new_chat_drafts,
            "process_baselines": self.process_baselines,
            "session_forwards": self.session_forwards,
            "pending_detaches": self.pending_detaches,
            "notifications": self.notifications,  # newest first; never sort
            "settings": self.settings,
        }
        tmp = _STATE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_STATE_FILE)

    # -- names -----------------------------------------------------------

    def get_name(self, session_id: str) -> str | None:
        return self.names.get(session_id)

    def set_name(self, session_id: str, name: str) -> None:
        name = name.strip()
        if name:
            self.names[session_id] = name
        else:
            self.names.pop(session_id, None)
        self.save()

    # -- generated names ---------------------------------------------------

    def get_generated_name(self, session_id: str) -> str | None:
        return self.generated_names.get(session_id)

    def set_generated_name(self, session_id: str, name: str) -> None:
        name = name.strip()
        if name:
            self.generated_names[session_id] = name
        else:
            self.generated_names.pop(session_id, None)
        self.save()

    def set_generated_names(self, names: dict[str, str]) -> None:
        """Set several generated names with a single write to disk."""
        for session_id, name in names.items():
            name = name.strip()
            if name:
                self.generated_names[session_id] = name
        self.save()

    # -- agent-CLI titles --------------------------------------------------

    def get_cli_title(self, session_id: str) -> str | None:
        return self.cli_titles.get(session_id)

    def set_cli_titles(self, titles: dict[str, str]) -> None:
        """Record several sessions' CLI titles with a single write to disk."""
        for session_id, title in titles.items():
            title = title.strip()
            if title:
                self.cli_titles[session_id] = title
        self.save()

    # -- emojis ------------------------------------------------------------

    def get_emoji(self, session_id: str) -> str | None:
        return self.emojis.get(session_id)

    def set_emoji(self, session_id: str, emoji: str) -> None:
        emoji = emoji.strip()
        if emoji:
            self.emojis[session_id] = emoji
        else:
            self.emojis.pop(session_id, None)
        self.save()

    # -- favorites ---------------------------------------------------------

    def is_favorite(self, session_id: str) -> bool:
        return session_id in self.favorites

    def toggle_favorite(self, session_id: str) -> bool:
        if session_id in self.favorites:
            self.favorites.discard(session_id)
        else:
            self.favorites.add(session_id)
        self.save()
        return session_id in self.favorites

    # -- archived ----------------------------------------------------------

    def is_archived(self, session_id: str) -> bool:
        return session_id in self.archived

    def set_archived(self, session_id: str, archived: bool) -> None:
        if archived:
            self.archived.add(session_id)
        else:
            self.archived.discard(session_id)
        self.save()

    def is_project_archived(self, project_name: str) -> bool:
        return project_name in self.archived_projects

    def set_project_archived(self, project_name: str, archived: bool) -> None:
        if archived:
            self.archived_projects.add(project_name)
        else:
            self.archived_projects.discard(project_name)
        self.save()

    # -- per-project worktree launches --------------------------------------

    def project_worktree_override(self, project_name: str) -> bool | None:
        """The project's own "new sessions use a worktree" choice, or None to
        follow the app-wide setting."""
        return self.project_worktree.get(project_name)

    def set_project_worktree(self, project_name: str, use_worktree: bool) -> None:
        """Pin a project's choice. Deliberately kept even when it matches the
        app setting, so a project pinned "off" stays off if the app default is
        later flipped on."""
        self.project_worktree[project_name] = use_worktree
        self.save()

    def worktree_for_project(self, project_name: str) -> bool:
        """Effective "new sessions use a worktree" value for a project."""
        override = self.project_worktree.get(project_name)
        if override is not None:
            return override
        return bool(self.get_setting("worktree_new_sessions"))

    # -- virtual projects --------------------------------------------------

    def get_virtual_projects(self) -> dict[str, str]:
        return dict(self.virtual_projects)

    def is_virtual_project(self, project_name: str) -> bool:
        return project_name in self.virtual_projects

    def keep_virtual_projects(self, projects: dict[str, str]) -> None:
        """Remember projects whose sessions are about to go, so their sidebar
        group survives. One write for the whole batch."""
        self.virtual_projects.update(projects)
        self.save()

    def forget_virtual_project(self, project_name: str) -> None:
        self.virtual_projects.pop(project_name, None)
        self.save()

    # -- project order -----------------------------------------------------

    def get_project_order(self) -> list[str]:
        return list(self.project_order)

    def set_project_order(self, order: list[str]) -> None:
        self.project_order = list(order)
        self.save()

    # -- expanded groups ---------------------------------------------------

    def is_group_expanded(self, group: str) -> bool:
        return group in self.expanded_groups

    def set_group_expanded(self, group: str, expanded: bool) -> None:
        if expanded:
            self.expanded_groups.add(group)
        else:
            self.expanded_groups.discard(group)
        self.save()

    def set_groups_expanded(self, groups: Iterable[str], expanded: bool) -> None:
        """Expand/collapse several groups with a single write to disk."""
        if expanded:
            self.expanded_groups.update(groups)
        else:
            self.expanded_groups.difference_update(groups)
        self.save()

    # -- session forwards --------------------------------------------------

    def forward_session(self, old_id: str, new_id: str) -> None:
        """Record that a session's conversation continued under a new id
        (Claude's /bg forking a backgrounded session to a fresh background
        session — still observed on current CLIs, despite docs suggesting
        in-place detaches). Carries the user's metadata over — without clobbering
        anything already set on the new id. One write to disk.

        The stale original row is *not* archived here: visibility is derived
        from the forward at display time (see SessionStore), so the original
        stays in the sidebar — disabled — until the fork's row can take its
        place, instead of vanishing for the scan-lag gap.

        A session that already has a forward is appended to, never overwritten:
        the existing target may be an agent that is still running, and dropping
        the only record of it would leave it with no row to reach it from. The
        new fork goes on the end of the chain instead, so resolve_forward()
        still lands on the newest id and nothing is orphaned."""
        if not old_id or not new_id or old_id == new_id:
            return
        tail = self.resolve_forward(old_id)
        if tail == new_id:
            return  # already the end of this chain
        self.session_forwards[tail] = new_id  # tail == old_id when unforwarded
        if old_id in self.names and new_id not in self.names:
            self.names[new_id] = self.names[old_id]
        if old_id in self.generated_names and new_id not in self.generated_names:
            self.generated_names[new_id] = self.generated_names[old_id]
        if old_id in self.emojis and new_id not in self.emojis:
            self.emojis[new_id] = self.emojis[old_id]
        if old_id in self.favorites:
            self.favorites.add(new_id)
        if old_id in self.panel_layouts and new_id not in self.panel_layouts:
            # Deep copy: a layout entry nests its whole strip tree, and the
            # two sessions' layouts must diverge independently from here.
            self.panel_layouts[new_id] = copy.deepcopy(self.panel_layouts[old_id])
        if old_id in self.editor_states and new_id not in self.editor_states:
            self.editor_states[new_id] = dict(self.editor_states[old_id])
        if old_id in self.session_prs and new_id not in self.session_prs:
            # The same conversation under a new id: the PRs it opened are the
            # fork's too, and the fork's transcript doesn't repeat them.
            self.session_prs[new_id] = list(self.session_prs[old_id])
        if old_id in self.session_attachments and new_id not in self.session_attachments:
            # Same reasoning as the PRs: the fork is the same conversation
            # under a new id, so the images it has already been shown are its
            # own — and its transcript, which starts at the fork, won't
            # mention them again.
            self.session_attachments[new_id] = list(self.session_attachments[old_id])
        if old_id in self.session_drafts and new_id not in self.session_drafts:
            # The fork is the same conversation under a new id, and the draft
            # was written *to that conversation* — it belongs to whichever id
            # the user reaches it by now.
            self.session_drafts[new_id] = self.session_drafts[old_id]
        if old_id in self.process_baselines and new_id not in self.process_baselines:
            # A fork is a fresh CLI process with no pristine capture window of
            # its own (it resumes a conversation already underway), so the
            # original's plumbing baseline is the best available. A fork
            # spawned with *fewer* servers is harmless — extra entries just
            # never match anything.
            self.process_baselines[new_id] = list(self.process_baselines[old_id])
        self.save()

    # -- pending /bg detaches ----------------------------------------------

    def set_pending_detach(
        self, session_id: str, provider: str = "", cwd: str = "", uuid: str = ""
    ) -> None:
        """Remember that a /bg was fed for this session but its background
        agent hasn't been identified yet, together with what identifying it
        needs. Persisted so closing the app mid-handoff doesn't strand a live
        agent with no row pointing at it."""
        if not session_id:
            return
        self.pending_detaches[session_id] = {
            "provider": provider,
            "cwd": cwd,
            "uuid": uuid,
        }
        self.save()

    def clear_pending_detach(self, session_id: str) -> None:
        if self.pending_detaches.pop(session_id, None) is not None:
            self.save()

    def get_pending_detaches(self) -> dict[str, dict]:
        return dict(self.pending_detaches)

    def get_process_baseline(self, session_id: str) -> set[str]:
        """The plumbing cmdlines captured for this session, empty when none
        were (a session attached from outside, or from before capture)."""
        return set(self.process_baselines.get(session_id) or [])

    def set_process_baseline(self, session_id: str, cmdlines: Iterable[str]) -> None:
        """Record the plumbing baseline for a session. Sorted so repeat
        captures of the same set are recognized without a write; called from
        a 2-second poll, so the no-change case must not touch the disk."""
        if not session_id:
            return
        value = sorted(set(cmdlines))
        if self.process_baselines.get(session_id) == value:
            return
        self.process_baselines[session_id] = value
        self.save()

    def forward_chain(self, session_id: str) -> list[str]:
        """Every id this conversation has run under from `session_id` onwards,
        oldest first and always including `session_id` itself. Cycle-safe.

        The middle of the chain matters, not just its ends: a session
        backgrounded twice is mid-handoff under its *previous* fork's id while
        the newest one is being recorded, and callers that only knew the head
        and the tail lost track of it exactly then."""
        chain = [session_id]
        seen = {session_id}
        while (nxt := self.session_forwards.get(session_id)) and nxt not in seen:
            session_id = nxt
            seen.add(nxt)
            chain.append(nxt)
        return chain

    def resolve_forward(self, session_id: str) -> str:
        """Follow the forward chain (a session may be backgrounded repeatedly)
        to the latest id. Cycle-safe; returns the input when unforwarded."""
        return self.forward_chain(session_id)[-1]

    # -- per-session panel layout ------------------------------------------

    def get_panel_layout(self, session_id: str) -> dict | None:
        return self.panel_layouts.get(session_id)

    def set_panel_layout(self, session_id: str, layout: dict | None) -> None:
        """Persist a session's dock layout (a panellayout entry: mode,
        sizes, split tree); None or empty removes the entry. Tabs snapshot
        on every close, so an unchanged layout is deliberately not
        rewritten to disk."""
        if layout:
            if self.panel_layouts.get(session_id) == layout:
                return
            self.panel_layouts[session_id] = layout
        else:
            if session_id not in self.panel_layouts:
                return
            del self.panel_layouts[session_id]
        self.save()

    # -- per-session editor state --------------------------------------------

    def get_editor_state(self, session_id: str) -> dict | None:
        return self.editor_states.get(session_id)

    def set_editor_state(self, session_id: str, state: dict | None) -> None:
        """Persist a session's editor snapshot ({"open", "width", "files",
        "active", "cursors"}); None or empty removes the entry. Tabs
        snapshot on every close, so an unchanged snapshot is deliberately
        not rewritten to disk."""
        if state:
            if self.editor_states.get(session_id) == state:
                return
            self.editor_states[session_id] = state
        else:
            if session_id not in self.editor_states:
                return
            del self.editor_states[session_id]
        self.save()

    # -- per-session pull requests -----------------------------------------

    def get_session_prs(self, session_id: str) -> list:
        """The PR records saved for a session, oldest first."""
        return list(self.session_prs.get(session_id) or [])

    def set_session_prs(self, session_id: str, prs: list) -> None:
        """Persist a session's PRs (prstatus records); an empty list drops it.

        Status is saved with them (see prstatus.to_record), so a restored mark
        reads as the last thing gh said until this run's first fetch replaces
        it. A tab re-derives this list on every transcript poll, so an
        unchanged one is deliberately not rewritten to disk.

        This pair is the persistence only: everything above it reads and
        writes through the PR hub (see prstore.PrStore), whose signals are
        how every surface showing the session hears about a write.
        """
        if not session_id:
            return
        if prs:
            if self.session_prs.get(session_id) == prs:
                return
            self.session_prs[session_id] = list(prs)
        else:
            if session_id not in self.session_prs:
                return
            del self.session_prs[session_id]
        self.save()

    # -- per-session attachments ---------------------------------------------

    def get_session_attachments(self, session_id: str) -> list:
        """The image records saved for a session, newest first."""
        return list(self.session_attachments.get(session_id) or [])

    def set_session_attachments(self, session_id: str, attachments: list) -> None:
        """Persist a session's images (attachrecords records); empty drops it.

        A tab re-folds this list on every sighting and hands the whole thing
        back, so the unchanged case — which is nearly all of them — is
        deliberately not rewritten to disk.
        """
        if not session_id:
            return
        if attachments:
            if self.session_attachments.get(session_id) == attachments:
                return
            self.session_attachments[session_id] = list(attachments)
        else:
            if session_id not in self.session_attachments:
                return
            del self.session_attachments[session_id]
        self.save()

    # -- per-session composer draft ------------------------------------------

    def get_session_draft(self, session_id: str) -> str:
        """The unsent prompt saved for a session, "" when there is none."""
        return self.session_drafts.get(session_id) or ""

    def set_session_draft(self, session_id: str, draft: str) -> None:
        """Persist a session's unsent composer draft; "" drops the entry.

        Dropping is as much the point as keeping: a draft that has made it
        back into a composer, or been sent, must not come back a second time
        (see TerminalTab._restore_stashed_draft). Tabs hand their draft over
        on every stash and again when the window closes, so the unchanged
        case is deliberately not rewritten to disk.
        """
        if not session_id:
            return
        if draft:
            if self.session_drafts.get(session_id) == draft:
                return
            self.session_drafts[session_id] = draft
        else:
            if session_id not in self.session_drafts:
                return
            del self.session_drafts[session_id]
        self.save()

    # -- new-chat drafts -----------------------------------------------------

    def get_new_chat_drafts(self) -> dict[str, dict]:
        """Every kept new-chat screen, by draft id, oldest first (the order
        the sidebar lists them in under a project)."""
        return dict(sorted(self.new_chat_drafts.items(), key=lambda kv: kv[1].get("created", 0.0)))

    def get_new_chat_draft(self, draft_id: str) -> dict | None:
        return self.new_chat_drafts.get(draft_id)

    def set_new_chat_draft(self, draft_id: str, record: dict) -> None:
        """Keep a new-chat screen's state (see newchat.draft_record). Written
        on every change the tab reports, so the unchanged case — the debounce
        firing on a box that was retyped back to what it was — is deliberately
        not rewritten to disk."""
        if not newchat.is_draft_id(draft_id):
            return
        if self.new_chat_drafts.get(draft_id) == record:
            return
        self.new_chat_drafts[draft_id] = record
        self.save()

    def remove_new_chat_draft(self, draft_id: str) -> None:
        """Forget a draft: its Send started the session, it was emptied, or
        the user discarded it from the sidebar."""
        if draft_id not in self.new_chat_drafts:
            return
        del self.new_chat_drafts[draft_id]
        self.save()

    # -- notification history ------------------------------------------------

    def get_notifications(self) -> list[dict]:
        """The saved notification rows, newest first (see notifycenter)."""
        return list(self.notifications)

    def set_notifications(self, records: list[dict]) -> None:
        """Persist the notification history whole — what the center's
        to_records() says it is now.

        The center announces every change it makes, and most of those don't
        touch the list on disk at all: a finished run's synthetic row coming
        and going is the commonest change of all and is never persisted. So
        the unchanged case — which save() would otherwise rewrite in full,
        synchronously, on every green edge — is deliberately not written.
        """
        if self.notifications == records:
            return
        self.notifications = list(records)
        self.save()

    # -- settings ------------------------------------------------------------

    def get_setting(self, key: str):
        return self.settings.get(key, DEFAULT_SETTINGS.get(key))

    def set_setting(self, key: str, value) -> None:
        self.settings[key] = value
        self.save()

    def update_settings(self, values: dict) -> None:
        """Set several settings with a single write to disk."""
        self.settings.update(values)
        self.save()
