# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-10-05. Full change history: git log for this file.

"""Persistent app state: custom names, favorites, archived sessions, settings.

Everything lives in our own config files — the agents' session data is never
modified. There are two files since the state split (the service-and-client
spec, §3.8): `state.json`, what the Collins service keeps about sessions,
projects and what it does for them (`AppState` is its one writer), and
`ui-state.json`, what this device keeps for itself and per service it talks
to (`uistate.UiState`, owned by the AppState and written through it).
`DEVICE_SETTINGS` and `SERVICE_SETTINGS` say which side each setting lives
on; every call site reads and writes through `AppState.get_setting` /
`set_setting` and the `get_*` / `set_*` methods below, which route, so none
of them learns the difference. `AppState.settings` stays the one merged
dict every reader sees.

The migration (AppState._load): the first start after the split copies
`state.json` to `state.json.pre-split`, moves the device keys into
`ui-state.json` under the local service's id, mints the service id into
`state.json` and rewrites it without them — `ui-state.json` first, so a
crash between the two writes leaves a `state.json` that still migrates on
the next start (and the merge below makes that retry harmless). The
service id's presence is the marker, so a second start migrates nothing.
Only the app's own instance migrates (`AppState(migrate=True)`: `main()`
and `App.__init__`); the throwaway readers on worker threads see the
merged view of an unsplit file and write nothing — unless one of them
saves, in which case the migration is committed first, so a service-side
write never strips device keys that have not reached `ui-state.json`.

A build from before the split, started on a split `state.json`, runs on
default device settings and empty layouts and saves them back without the
id; the following start of this build migrates again, **merging**: it
adopts the one service `ui-state.json` already knows, takes a record of
the old file only where the file's entry is non-empty, takes a device
setting from the file only where it differs from its default or
`ui-state.json` never held the key, and never overwrites an existing
`state.json.pre-split` (a timestamped sibling is written instead). What
the old build could not have changed is therefore what `ui-state.json`
kept. `./start-debug` shares the real config dir (it sets only the app
id), so a debug launch of a split build migrates the real `state.json`.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from . import autodelete, mcptools, newchat, notifycenter, panelhistory, panellayout, uistate
from .claudemodels import NO_MODEL
from .uistate import SERVICE_SCOPED_SETTINGS

log = logging.getLogger(__name__)

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
    # Read each new session's first prompt for pull request URLs and attach
    # every PR it links to the session's row (see prattach.py). URLs only:
    # "PR 1" is as often a PR the session is about to open as one that exists.
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
    # prefers them; a manual rename in Collins still wins. On by default:
    # the CLI names every session it runs, at no cost to Collins.
    "cli_title_sessions": True,
    # Run the sidebar's PR sweep once, shortly after launch, so the marks
    # restored from the last run are replaced by current ones without the
    # refresh button being clicked (see MainWindow._schedule_launch_sweep).
    "refresh_prs_on_launch": True,
    # Launch new sessions with the agent CLI's worktree flag (claude -w) in
    # git projects, isolating their edits from the live checkout. Per-project
    # overrides live in AppState.project_worktree.
    "worktree_new_sessions": False,
    # Where Add project → Clone Repository puts a new checkout: the dialog's
    # "Clone into" field starts here (clonedialog.CloneDialog, expanded by
    # clonerepo.parent_directory), and the clone lands in a folder named
    # after the repository inside it. "~" is the home folder; Preferences →
    # General stores a picked folder as a "~/…" path when it's under home.
    "clone_directory": "~",
    # Launch new sessions inside a bubblewrap filesystem sandbox (see
    # sandboxplan): the workspace read-write, ~/.claude and the toolchain
    # caches shared, the system read-only, credentials and the rest of the
    # disk absent. Per-project overrides live in AppState.project_sandbox;
    # the new-chat screen's Sandboxed box starts on the effective value
    # (MainWindow._sandbox_for_new_session). Only ever effective where
    # sandboxplan.available() says a box can be built.
    "sandbox_new_sessions": False,
    # Whether a sandboxed session's permission mode defaults to
    # bypassPermissions: a yolo session that can't reach ~/.ssh. Off by
    # default — with no prompt the box is the only barrier, and it bounds
    # the filesystem, not everything the host later runs (the repository's
    # .git/config, the toolchain directories, the workspace itself). Read
    # by MainWindow._sandboxed_options for every sandboxed launch: the
    # new-chat Send, a resume or --continue of a sticky-sandboxed session,
    # and the start_session tool's sibling.
    "sandbox_bypass_permissions": False,
    # Hand the host's GitHub CLI login into sandboxed sessions: ~/.config/gh
    # bound read-only and the token (`gh auth token`) passed in as GH_TOKEN
    # by sandboxrun. Off, gh inside is logged out and HTTPS pushes through
    # gh's credential helper fail. Read by sandboxplan.gather_inputs.
    "sandbox_share_gh": False,
    # Share the SSH agent with sandboxed sessions: the SSH_AUTH_SOCK socket
    # file is bound in and the variable survives the scrub, so ssh inside
    # signs with keys the box never sees. Read by sandboxplan.gather_inputs.
    "sandbox_share_ssh": False,
    # Let sandboxed sessions write what the host's other sessions and
    # commands run: ~/.claude/settings.json (and settings.local.json, the
    # plugins, skills, commands and agents directories, CLAUDE.md, a
    # real-file claude launcher), the repository's .git/hooks and
    # ~/.local/bin. Needed for /model and /effort to persist their
    # defaults from inside and for installing a hook or a tool from there,
    # at the cost that what is written runs in every later session,
    # sandboxed or not. Off, they are bound read-only over themselves, and
    # a symlinked settings.json refuses the box. Read by
    # sandboxplan.gather_inputs.
    "sandbox_settings_editable": False,
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
    "archive_running_session": "ask",  # archiving a session whose tab is busy
    # Closing a window (quitting) while sessions run (MainWindow._confirm_quit,
    # split-service spec §3.21, D30): detach (the default: plain Quit, every
    # tab detached and its session left running in the Collins service) |
    # ask (the quit dialog: Quit, Stop sessions and quit, Keep running) |
    # exit (Stop sessions and quit: each agent asked to exit, as quitting
    # always did before the service) | background (each handed to /bg, the
    # rest exited) | hide (the window, not its sessions, goes away,
    # recoverable from the status icon — MainWindow._hide_window). An
    # install that stored "ask" before detach existed is moved to detach
    # once, by migrate_device_settings (quit_detach_migrated records it, so
    # an "ask" chosen afterwards stays).
    "quit_with_running_sessions": "detach",
    # Whether the one-shot ask -> detach move of quit_with_running_sessions
    # has run on this device (migrate_device_settings).
    "quit_detach_migrated": False,
    # Whether the quit dialog has said, once, that the status icon leaves
    # with the window while the sessions keep running (MainWindow.
    # _confirm_quit; set when the dialog first shows, like hide_notice_shown).
    "quit_notice_shown": False,
    # What becomes of a session's git worktree when the session is archived:
    # ask (a dialog before the archive — Keep, Trash, or Cancel the archive)
    # | always (move it to the trash, no dialog) | never (leave it). The move
    # waits until the session has stopped, if a tab was open, and the
    # archive's Undo brings the worktree back. Only a worktree
    # the session still occupies (sessions.removable_worktree), and never
    # while the session runs on as a background agent — see
    # MainWindow._ask_worktree_then_archive and _settle_archived_worktree.
    "archive_worktree": "ask",
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
    # Trash archived sessions automatically once they have sat archived this
    # long: a count and a unit (autodelete.UNITS: days | weeks | months |
    # years). 0 is never, the default. Measured from the archive stamp in
    # archived_at; swept at most once a day by the app (see autodelete.py).
    "auto_delete_archived_after": 0,
    "auto_delete_archived_unit": "months",
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
    # "none" is silence, "theme:<event>" another of the desktop theme's
    # sounds, "bundled:<name>" one of the sounds Collins ships, and anything
    # else an absolute path to a sound file.
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
    # Off by default: an unattended agent needs the computer, not the room lit.
    "caffeine_keep_screen_on": False,
    # Start with Caffeine Mode on (see app.py's inhibitor), on the timer
    # below. On by default with the Until-idle timer: the machine stays up
    # only while a session works, and dozes five minutes after the last stops.
    "caffeine_on_launch": True,
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
    # _on_hub_pr_attached). On by default: a session that just opened a PR
    # is about to watch its checks. Once per PR per session — the saved list
    # is what remembers, so a page closed again stays closed.
    "open_pr_panel_on_attach": True,
    # Whether a session's gallery of images docks itself beside that session
    # the first time it shows one (see TerminalTab._consider_attachments_dock).
    # On by default, and cheaper than the PR switch above: it only ever
    # spends room the terminal wasn't using, waiting for a tab wide enough
    # that a column comes free of the terminal's maximum width
    # (panelsizing.room_for_a_split), and once per tab, so a panel closed
    # again stays closed.
    "dock_attachments_when_room": True,
    # Whether merging a pull request asks first (see practions.confirmation).
    # On: Merge, Merge when checks pass and Merge and archive each put up
    # their dialog, as they always have. Off: the click merges. Only the
    # merges — closing a pull request unmerged still asks, since that is the
    # one PR action that throws the work away rather than landing it.
    "confirm_merges": True,
    # The git page (gitpage.py), Preferences → Git, read through
    # gitloads.Options.from_settings: the diff view's layout (the header
    # menu and the `0` / `1` / `2` keys flip it too), whether working-tree
    # reviews list untracked files, and the commits panel's page size.
    "git_layout": "auto",  # auto / split / stack (diffview.set_options)
    "git_untracked": True,  # off: working-tree reviews hide untracked files
    "git_log_page": 20,  # commits per group page in the commits panel ("load more…" step)
    # The diff view's own knobs (DiffView.set_options, fed from
    # gitpage.apply_settings; the header menu and the `l` / `w` keys flip
    # the first two): the old and new line-number columns, wrapping long
    # lines instead of scrolling each hunk sideways, the word-level
    # emphasis pass within a changed line, and whether a changed line that
    # differs from its partner in whitespace alone is drawn as context
    # (GitHub's "Hide whitespace"; the header menu's check flips it, and
    # the patch underneath is untouched).
    "git_line_numbers": True,
    "git_wrap_lines": False,
    "git_word_diff": True,
    "git_hide_whitespace": False,
    # The branch a session's git page measures its branch against when git
    # shows no local branch under HEAD (the stack, gitops.stack_branches,
    # names the parent first) and no attached pull request names one (see
    # TerminalTab._git_parent_branch): "" = automatic (the PR's base, else
    # the repository's default branch). A branch name; "origin/x" is taken
    # as x when origin has it (gitinfo.parent_branch); skipped in a
    # repository that lacks it.
    "git_parent_branch": "",
    "editor_window_width": 1000,  # last popped-out editor window size (floating, unmaximized)
    "editor_window_height": 700,
    "editor_window_maximized": False,
    # One switch per tool Collins offers the sessions it starts (see
    # mcptools.TOOLS), all on: "mcp_tool_<name>". Keyed off the tool table so
    # a new tool can't ship without its switch — off means the tool is left
    # out of what a session is offered, and refused if an older session calls
    # it anyway.
    **mcptools.default_tool_settings(),
    # And one per tool for sessions that run in a sandbox:
    # "sandbox_tool_<name>", on for mcptools.SANDBOX_DEFAULT_TOOLS and off
    # for the rest. What a sandboxed session is offered unless its own box
    # says otherwise (AppState.sandbox_tools, edited in the Sandboxed
    # chip); read by sandboxplan.SandboxHost.tool_enabled for every list
    # and every call. A tool off above is off inside a box too.
    **mcptools.default_sandbox_tool_settings(),
}

# Which side of the split each setting lives on (the spec's §3.8). A
# setting that describes what the service does — which claude it runs, how
# sessions are launched, archived and titled, the sandbox, the tools a
# session is offered, the git page's reading of the repository — is the
# service's and stays in state.json. Everything that describes this
# device — appearance, geometry, keybindings, sounds, the tray, Caffeine,
# the composer's and editor's and git page's look, which notifications
# this screen shows — is the device's and lives in ui-state.json. Both
# lists are explicit, and tests/test_state_split.py holds them to
# DEFAULT_SETTINGS: a new setting has to be put on one side here.
SERVICE_SETTINGS: frozenset[str] = frozenset({
    "claude_cli_path",
    "title_model",
    "icon_model",
    "auto_renew_login",
    "worktree_new_sessions",
    "clone_directory",
    "sandbox_new_sessions",
    "sandbox_bypass_permissions",
    "sandbox_share_gh",
    "sandbox_share_ssh",
    "sandbox_settings_editable",
    *mcptools.default_sandbox_tool_settings(),
    *mcptools.default_tool_settings(),
    "attach_prompt_prs",
    "pr_title_sessions",
    "cli_title_sessions",
    "refresh_prs_on_launch",
    "archive_running_session",
    "archive_worktree",
    "archive_on_claude_ai",
    "auto_delete_archived_after",
    "auto_delete_archived_unit",
    "background_status_poll",
    "progress_termprop",
    "git_parent_branch",
    "git_untracked",
    "git_log_page",
    "welcome_seen",
    "gh_welcome_dismissed",
})
DEVICE_SETTINGS: frozenset[str] = frozenset({
    "font",
    "keybindings",
    "scrollback",
    "color_scheme",
    "terminal_theme",
    "terminal_max_width",
    "easy_copy_paste",
    "language",
    "quit_with_running_sessions",
    "quit_detach_migrated",
    "quit_notice_shown",
    "hide_notice_shown",
    "show_tab_bar",
    "status_icon",
    "inapp_notifications",
    "notification_sound",
    "bell_notifications",
    "announce_finished_runs",
    "check_for_updates",
    "notification_color_scheme",
    "attach_overlay_button",
    "composer_enter_sends",
    "composer_on_typing",
    "composer_spell_click",
    "show_folder_path",
    "project_icon_size",
    "show_usage_panel",
    "usage_panel_collapsed",
    "footer_apps",
    "caffeine_keep_screen_on",
    "caffeine_on_launch",
    "caffeine_launch_timer",
    "caffeine_idle_grace_minutes",
    "sidebar_width",
    "panel_position",
    "panel_tab_drag_handles",
    "panel_size_bottom",
    "panel_size_right",
    "page_panel_size_bottom",
    "page_panel_size_right",
    "window_width",
    "window_height",
    "window_maximized",
    "last_active_session",  # stored per service (uistate.SERVICE_SCOPED_SETTINGS)
    "restore_last_session",
    "editor_width",
    "editor_style_scheme",
    "editor_font",
    "editor_show_line_numbers",
    "editor_show_hidden_files",
    "editor_pop_out_screen_width",
    "editor_narrow_width",
    "pr_font_scale",
    "pr_inline_images",
    "open_pr_panel_on_attach",
    "dock_attachments_when_room",
    "confirm_merges",
    "git_layout",
    "git_line_numbers",
    "git_wrap_lines",
    "git_word_diff",
    "git_hide_whitespace",
    "editor_window_width",
    "editor_window_height",
    "editor_window_maximized",
})


def _ui_state_file() -> Path:
    """ui-state.json beside state.json (so a test's redirect of the config
    dir carries it)."""
    return _CONFIG_DIR / "ui-state.json"


def _pre_split_backup() -> Path:
    return _STATE_FILE.with_name("state.json.pre-split")


def migrate_device_settings(settings: dict) -> dict:
    """The one-shot moves of this device's settings, applied to the device
    settings as read (in place, and returned). Today one: the quit setting's
    old default, "ask", becomes "detach" (D30: quitting leaves the sessions
    running in the service), once per device; quit_detach_migrated marks it
    done, so an "ask" chosen in Preferences afterwards stays."""
    if not settings.get("quit_detach_migrated"):
        if settings.get("quit_with_running_sessions", "ask") == "ask":
            settings["quit_with_running_sessions"] = "detach"
        settings["quit_detach_migrated"] = True
    return settings


def device_defaults() -> dict:
    """DEFAULT_SETTINGS narrowed to what ui-state.json's "device" holds."""
    return {
        k: v for k, v in DEFAULT_SETTINGS.items()
        if k in DEVICE_SETTINGS and k not in SERVICE_SCOPED_SETTINGS
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


def _valid_box_id(box: object) -> bool:
    """sandboxplan.valid_box_id, which this module can't import (the plan
    sits above the state): 32 lowercase hex characters."""
    return isinstance(box, str) and len(box) == 32 and all(c in "0123456789abcdef" for c in box)


def _sandboxed_sessions(raw: object) -> dict[str, str]:
    """The session → box map out of state.json: an object of string keys,
    each value kept when it is "" or a box id and read as "" otherwise —
    or a list of ids (what a build from before the boxes wrote), each with
    no box yet. Anything else is no sessions at all."""
    if isinstance(raw, dict):
        return {
            key: (box if _valid_box_id(box) else "")
            for key, box in raw.items()
            if isinstance(key, str) and key
        }
    if isinstance(raw, list):
        return {key: "" for key in raw if isinstance(key, str) and key}
    return {}


def _sandbox_grants(raw: object) -> dict[str, list[str]]:
    """The box → grants map out of state.json: an object whose keys are box
    ids and whose values are lists of absolute paths. An entry keyed by
    anything else is dropped — a path, which is what a build from before
    grants were a session's own wrote, is nobody's default — and so is one
    left with no path in it."""
    if not isinstance(raw, dict):
        return {}
    grants = {
        box: [path for path in paths if isinstance(path, str) and path.startswith("/")]
        for box, paths in raw.items()
        if _valid_box_id(box) and isinstance(paths, list)
    }
    return {box: paths for box, paths in grants.items() if paths}


def _sandbox_tools(raw: object) -> dict[str, dict[str, bool]]:
    """The box → tool switches map out of state.json: an object whose keys
    are box ids and whose values map a tool's name to on or off
    (mcptools.tool_overrides: names the table has, booleans). An entry
    keyed by anything else is dropped, and so is one left with nothing."""
    if not isinstance(raw, dict):
        return {}
    tools = {
        box: mcptools.tool_overrides(switches)
        for box, switches in raw.items()
        if _valid_box_id(box)
    }
    return {box: switches for box, switches in tools.items() if switches}


def _sandbox_project_grants(raw: object) -> dict[str, list[str]]:
    """The project → default grants map out of state.json: keys that are
    absolute paths, values that are lists of absolute paths. Anything else
    is dropped, and so is an entry left with nothing in it."""
    if not isinstance(raw, dict):
        return {}
    grants = {
        key: [path for path in paths if isinstance(path, str) and path.startswith("/")]
        for key, paths in raw.items()
        if isinstance(key, str) and key.startswith("/") and isinstance(paths, list)
    }
    return {key: paths for key, paths in grants.items() if paths}


# ---- the shared keys (the mirror, split-service spec §3.8) ------------------
#
# The top-level keys of state.json as the API carries them: what the
# service's `ServiceCore` publishes to its clients as `state.set` events, and
# what a client's `remotestate.RemoteState` mirrors and writes back. Each
# names the AppState attribute holding it, its form on the wire, the cleaner
# that reads a value of it as `_load` would (garbage dropped: rule 5, in
# both directions), and whether a client may write it. A MAP key travels
# entry by entry (`state.set`'s `entry`: a session id, a setting's name);
# the rest travel whole. `settings` carries the service's settings only
# (`DEVICE_SETTINGS` never leave the device), and a client writes them one
# entry at a time.

MAP = "map"
SET = "set"
LIST = "list"
SCALAR = "scalar"


def _map_of(check: Callable[[object], bool]) -> Callable[[object], dict]:
    """A cleaner for a map of non-empty string keys whose values pass
    *check*."""

    def clean(raw: object) -> dict:
        if not isinstance(raw, dict):
            return {}
        return {k: v for k, v in raw.items() if isinstance(k, str) and k and check(v)}

    return clean


def _is_str(value: object) -> bool:
    return isinstance(value, str)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _clean_set(raw: object) -> list[str]:
    return [v for v in raw if isinstance(v, str)] if isinstance(raw, list) else []


def _clean_drafts(raw: object) -> dict[str, str]:
    return _map_of(lambda v: isinstance(v, str) and bool(v))(raw)


def _clean_new_chat_drafts(raw: object) -> dict[str, dict]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict] = {}
    for key, value in raw.items():
        clean = newchat.valid_draft(value)
        if newchat.is_draft_id(key) and clean is not None:
            out[key] = clean
    return out


def _clean_ptys(raw: object) -> dict[str, dict]:
    if not isinstance(raw, dict):
        return {}
    return {
        k: v for k, v in raw.items() if isinstance(k, str) and k.isdigit() and isinstance(v, dict)
    }


def _clean_settings(raw: object) -> dict:
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and k}


def _clean_service_id(raw: object) -> str:
    return raw if uistate._is_id(raw) else ""


def _clean_next_id(raw: object) -> int:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and 1 <= raw < 2**32 else 1


def _clean_notifications(raw: object) -> list[dict]:
    return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []


class SharedKey:
    """One top-level key of state.json as the API carries it."""

    __slots__ = ("name", "attr", "form", "clean", "writable")

    def __init__(self, name: str, form: str, clean, attr: str | None = None, writable: bool = True):
        self.name = name
        self.attr = attr or name
        self.form = form
        self.clean = clean
        self.writable = writable


SHARED_KEYS: dict[str, SharedKey] = {
    key.name: key
    for key in (
        SharedKey("service_id", SCALAR, _clean_service_id, writable=False),
        SharedKey("names", MAP, _map_of(_is_str)),
        SharedKey("generated_names", MAP, _map_of(_is_str)),
        SharedKey("cli_titles", MAP, _map_of(_is_str)),
        SharedKey("emojis", MAP, _map_of(_is_str)),
        SharedKey("favorites", SET, _clean_set),
        SharedKey("archived", SET, _clean_set),
        SharedKey("archived_at", MAP, _map_of(_is_number)),
        SharedKey("archived_projects", SET, _clean_set),
        SharedKey("project_worktree", MAP, _map_of(lambda v: isinstance(v, bool))),
        SharedKey("project_sandbox", MAP, _map_of(lambda v: isinstance(v, bool))),
        SharedKey("sandboxed_sessions", MAP, lambda raw: _sandboxed_sessions(raw)),
        SharedKey("sandbox_grants", MAP, lambda raw: _sandbox_grants(raw)),
        SharedKey("sandbox_project_grants", MAP, lambda raw: _sandbox_project_grants(raw)),
        SharedKey("sandbox_tools", MAP, lambda raw: _sandbox_tools(raw)),
        SharedKey("project_order", LIST, _clean_set),
        SharedKey("virtual_projects", MAP, _map_of(_is_str)),
        SharedKey("expanded_groups", SET, _clean_set),
        SharedKey("session_prs", MAP, _map_of(lambda v: isinstance(v, list))),
        SharedKey("session_attachments", MAP, _map_of(lambda v: isinstance(v, list))),
        SharedKey("session_drafts", MAP, _clean_drafts),
        SharedKey("new_chat_drafts", MAP, _clean_new_chat_drafts),
        SharedKey("process_baselines", MAP, _map_of(lambda v: isinstance(v, list))),
        SharedKey("session_forwards", MAP, _map_of(_is_str)),
        SharedKey("pending_detaches", MAP, _map_of(lambda v: isinstance(v, dict))),
        # The sessions a stopping service ended (§3.10 item 1, PR-1.12b):
        # written by the service alone when it restarts or is stopped; a
        # client reopening its tabs finds them resumable.
        SharedKey("resume_on_start", LIST, _clean_set, writable=False),
        SharedKey("ptys", MAP, _clean_ptys, writable=False),
        SharedKey("pty_next_id", SCALAR, _clean_next_id, writable=False),
        # The service's own since PR-1.11: the history is written through
        # its notification center (notify.* and seen), the diffs' marks
        # through diff.set-notes, the pending show_diffs by the tools.
        SharedKey("notifications", LIST, _clean_notifications, writable=False),
        SharedKey("diff_notes", MAP, _map_of(lambda v: isinstance(v, dict)), writable=False),
        SharedKey("pending_diffs", MAP, _map_of(lambda v: isinstance(v, dict)), writable=False),
        SharedKey("settings", MAP, _clean_settings),
    )
}


def _setting_type_ok(key: str, value: object) -> bool:
    """Whether *value* has the type of the setting's default (rule 5: a
    setting written through the API is foreign content). Exact types, so a
    bool is no int; an int stands in for a float."""
    default = DEFAULT_SETTINGS[key]
    if type(value) is type(default):
        return True
    return type(default) is float and type(value) is int


def diff_shared(key: SharedKey, old, new) -> list[tuple[str | None, object]]:
    """What changed in *key* between two exported values (`export_key`'s
    form), as ``(entry, value)`` pairs: a MAP key entry by entry (value
    None for an entry that went), anything else whole (entry None)."""
    if old == new:
        return []
    if key.form != MAP or not isinstance(old, dict) or not isinstance(new, dict):
        return [(None, new)]
    changes: list[tuple[str | None, object]] = [
        (entry, value) for entry, value in new.items() if old.get(entry, _MISSING) != value
    ]
    changes.extend((entry, None) for entry in old if entry not in new)
    return changes


_MISSING = object()


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
    def __init__(
        self,
        migrate: bool = False,
        device: bool = True,
        ui: uistate.UiState | None = None,
    ) -> None:
        """*migrate*: whether this instance may perform the state split's
        one-time migration at load (the app's own instance; see the module
        docstring). A reader that does not migrate still sees an unsplit
        file's device keys in its merged view, and commits the migration
        before its first save, if it ever saves.

        *device*: whether this instance writes this device's half,
        ui-state.json. The service's own instance (`service.core.ServiceCore`)
        passes False: once the migration is written, the device's half is
        the client's (`remotestate.RemoteState`, which owns its own
        `UiState`), and a second writer in the same process would clobber
        it. *ui* is the `UiState` to use, one read off ui-state.json by
        default."""
        self._migrate = migrate
        self._device = device
        # Called after every write of state.json (the service publishes the
        # change to its clients from here; see service.core).
        self.on_saved = None
        # Set while an unsplit state.json has been read and its migration
        # not yet written: the first write of either half commits it
        # (ui-state.json first), whichever instance writes.
        self._migration_pending = False
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
        # When each session was archived (wall-clock seconds; see autodelete).
        self.archived_at: dict[str, float] = {}
        # Per-project "new sessions use a worktree" choices, by project name.
        # Absent key = follow the worktree_new_sessions setting.
        self.project_worktree: dict[str, bool] = {}
        # Per-project "new sessions are sandboxed" choices, by project name,
        # on the same terms (absent = follow sandbox_new_sessions).
        self.project_sandbox: dict[str, bool] = {}
        # The sessions that run inside a sandbox, each with the id of its
        # box (sandboxplan.box_dir: its own $HOME, kept across resumes), or
        # "" while it has none yet: sticky per session, so a resume builds
        # the same box the session was started in (see
        # MainWindow.open_session). Recorded when a sandboxed launch resolves
        # its id; a forward (/bg fork) carries it to the new id.
        self.sandboxed_sessions: dict[str, str] = {}
        # box id -> the directories that box's session may also reach,
        # read-write, beyond its workspace. A grant is one session's: it is
        # keyed by the session's box (the one identity a session has before
        # the CLI has minted its id), never by a workspace or a project, and
        # it leaves the state with its box (SandboxHost.forget_box).
        # Written on the main loop only. Every entry is re-checked against
        # sandboxplan.guard_sensitive when a plan is built: state.json is a
        # file on disk like any other.
        self.sandbox_grants: dict[str, list[str]] = {}
        # project (sandboxplan.project_key: a real path, a worktree's
        # repository) -> the directories a *new* session of it starts
        # allowed. A template, copied into a box's list once when the box
        # is minted (SandboxHost.mint_box) and never a live link: changing
        # it changes no session that exists.
        self.sandbox_project_grants: dict[str, list[str]] = {}
        # box id -> the session tools that box's session is, or is not,
        # offered where that differs from the default for sandboxed
        # sessions (the "sandbox_tool_<name>" settings): tool name -> on.
        # A tool with no entry follows the default. Keyed by the box and
        # gone with it, like the grants; written on the main loop only.
        self.sandbox_tools: dict[str, dict[str, bool]] = {}
        self.project_order: list[str] = []  # user-arranged sidebar order, by project name
        # Projects kept in the sidebar after their last session went away
        # (project name -> working directory, "" when it was never known), so
        # deleting sessions doesn't cost you the folder.
        self.virtual_projects: dict[str, str] = {}
        self.expanded_groups: set[str] = set()  # sidebar groups the user expanded
        # This device's file (ui-state.json), and which service's block of it
        # this state is: per-session dock layouts and editor states live
        # there (the panel_layouts and editor_states properties below), as
        # do the device settings. The service id is minted once into
        # state.json; _load reads it or makes it.
        self.ui = ui if ui is not None else uistate.UiState(_ui_state_file(), device_defaults())
        self.service_id: str = ""
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
        # The session ids the service ended on its last stop (resumable by
        # a client reopening them; split-service spec §3.10, PR-1.12b).
        self.resume_on_start: list[str] = []
        # The marks on each session's diff (split-service spec §3.8,
        # PR-1.11): session id -> {"notes": [...], "highlights": [...]},
        # diffnotes.mark_record each; written by the service's
        # diffs.DiffNotes, mirrored to the git page's store.
        self.diff_notes: dict[str, dict] = {}
        # A show_diff a session asked for while no client was attached:
        # session id -> the tool's arguments, handed to the first client
        # that subscribes (service.tools, §3.7).
        self.pending_diffs: dict[str, dict] = {}
        # The pty table (split-service spec §3.8): pty id (as a string, JSON
        # keys) -> the row the service's PtyServer keeps for a live pty
        # (kind, session, cwd, pid, cols, rows, box, plan, options). Written
        # by the server through set_pty / remove_pty (wired in PR-1.7). A
        # row left by a service that died stays until the service that
        # starts next clears it (PR-1.12; before the keeper, PR-3.6, no pty
        # survives a restart). pty_next_id is the id the next spawn takes,
        # persisted so a saved model file never names two ptys.
        self.ptys: dict[str, dict] = {}
        self.pty_next_id: int = 1
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

    # -- the device's half -------------------------------------------------

    @property
    def panel_layouts(self) -> dict[str, dict]:
        """Per-session dock layout (see panellayout), this device's."""
        return self.ui.service(self.service_id)["panel_layout"]

    @property
    def editor_states(self) -> dict[str, dict]:
        """Per-session editor open/width/files/cursors, this device's."""
        return self.ui.service(self.service_id)["editor_states"]

    def _load(self) -> None:
        _migrate_old_config()
        data: dict = {}
        parsed = False
        try:
            data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            parsed = isinstance(data, dict)
            if not parsed:
                data = {}
        except (OSError, json.JSONDecodeError):
            # one-time migration from the old names-only store
            try:
                data = {"names": json.loads(_LEGACY_NAMES_FILE.read_text(encoding="utf-8"))}
            except (OSError, json.JSONDecodeError):
                data = {}
        # The split: a parsed state.json with no service id is one a build
        # from before the split wrote (or the e2e scripts seeded). It is
        # backed up whole, then its device keys are moved into ui-state.json
        # and it is rewritten without them, once. A fresh install (no file)
        # just gets its id. An unparseable file is left exactly as it is:
        # nothing is backed up, nothing rewritten, the state starts on
        # defaults as it always did.
        service_id = data.get("service_id")
        migrating = parsed and not uistate._is_id(service_id)
        if uistate._is_id(service_id):
            self.service_id = service_id
        else:
            known = self.ui.known_services()
            self.service_id = known[0] if len(known) == 1 else uistate.mint_id()
        # Whether ui-state.json already holds a block for this service: a
        # re-migration after a downgrade, where the file's device keys are
        # merged into what the block kept rather than replacing it.
        adopted = migrating and self.ui.has_service(self.service_id)
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
        # When each session was archived, for the automatic delete
        # (autodelete.py). An archive from before the stamp existed starts
        # its clock at this read; a stamp for something no longer archived
        # is dropped.
        raw_archived_at = data.get("archived_at")
        self.archived_at = autodelete.stamp_missing(
            self.archived, raw_archived_at if isinstance(raw_archived_at, dict) else {}
        )
        self.project_worktree = {
            k: v for k, v in (data.get("project_worktree") or {}).items() if isinstance(v, bool)
        }
        self.project_sandbox = {
            k: v for k, v in (data.get("project_sandbox") or {}).items() if isinstance(v, bool)
        }
        self.sandboxed_sessions = _sandboxed_sessions(data.get("sandboxed_sessions"))
        self.sandbox_grants = _sandbox_grants(data.get("sandbox_grants"))
        self.sandbox_tools = _sandbox_tools(data.get("sandbox_tools"))
        self.sandbox_project_grants = _sandbox_project_grants(
            data.get("sandbox_project_grants")
        )
        self.project_order = list(data.get("project_order") or [])
        self.virtual_projects = {
            k: v for k, v in (data.get("virtual_projects") or {}).items() if isinstance(v, str)
        }
        self.expanded_groups = set(data.get("expanded_groups") or [])
        block = self.ui.service(self.service_id)
        if migrating:
            # The device records of the old file: on a first migration the
            # block is empty and takes them whole; on a re-migration (a
            # downgrade wrote the file last, on empty layouts it then saved
            # back) an entry of the file wins only where it is non-empty,
            # and the block keeps the rest.
            layouts = {
                k: v for k, v in (data.get("panel_layout") or {}).items() if isinstance(v, dict)
            }
            # Read-time, one-way migration of the pre-tree "panel_states"
            # shape ({"open", "mode", "sizes"}): each entry becomes the
            # two-node tree for its mode, sized by the shell history files
            # on disk. The old key is dropped on the next save.
            for sid, old in (data.get("panel_states") or {}).items():
                if sid in layouts or not isinstance(old, dict):
                    continue
                entry = panellayout.from_legacy(old, panelhistory.ordinals(sid))
                if entry:
                    layouts[sid] = entry
            editors = {
                k: v for k, v in (data.get("editor_states") or {}).items() if isinstance(v, dict)
            }
            block["panel_layout"].update({k: v for k, v in layouts.items() if v})
            block["editor_states"].update({k: v for k, v in editors.items() if v})
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
        self.resume_on_start = _clean_set(data.get("resume_on_start"))
        self.diff_notes = {
            k: v for k, v in (data.get("diff_notes") or {}).items() if isinstance(v, dict)
        }
        self.pending_diffs = {
            k: v for k, v in (data.get("pending_diffs") or {}).items() if isinstance(v, dict)
        }
        self.ptys = {
            k: v for k, v in (data.get("ptys") or {}).items()
            if isinstance(k, str) and k.isdigit() and isinstance(v, dict)
        }
        next_id = data.get("pty_next_id")
        self.pty_next_id = next_id if isinstance(next_id, int) and 1 <= next_id < 2**32 else 1
        self.notifications = notifycenter.clean_records(data.get("notifications"))
        settings = dict(data.get("settings") or {})
        # Read-time, one-way migration of the auto_title_sessions switch the
        # title model's None item replaced: off becomes None, on is just the
        # default. Either way the old key goes, dropped on the next save.
        if settings.pop("auto_title_sessions", None) is False:
            settings["title_model"] = NO_MODEL
        # composer_new_sessions (auto-open the composer on a fresh session)
        # went with the new-chat screen, which writes every first prompt in
        # its own box; the key is dropped on the next save.
        settings.pop("composer_new_sessions", None)
        # git_theme and git_viewer went with the terminal diff viewer the
        # git page used to run (the diff view has no theme of its own: it
        # follows the editor's scheme); a stale key would otherwise ride
        # along in self.settings and be written back by every save.
        settings.pop("git_theme", None)
        settings.pop("git_viewer", None)
        service_settings = {k: v for k, v in settings.items() if k not in DEVICE_SETTINGS}
        # What ui-state.json contributes: its device keys, and any key the
        # catalogue does not know (kept on that side, never written to
        # state.json); a service key that found its way there is ignored,
        # so it cannot override the service's value.
        ui_settings = {k: v for k, v in self.ui.settings.items() if k not in SERVICE_SETTINGS}
        self._ui_only_keys = frozenset(k for k in ui_settings if k not in DEFAULT_SETTINGS)
        if migrating:
            # The device settings of the old file move to this device's. On
            # a re-migration a value is taken from the file only where it
            # differs from the default or ui-state.json never held the key:
            # the downgrade ran on defaults, and a default it wrote back
            # says nothing about what this device had chosen.
            for key, value in settings.items():
                if key not in DEVICE_SETTINGS:
                    continue
                if key in SERVICE_SCOPED_SETTINGS:
                    if value != DEFAULT_SETTINGS.get(key) or not adopted:
                        self.ui.set_scoped(self.service_id, key, value)
                elif value != DEFAULT_SETTINGS.get(key) or key not in self.ui.present_keys:
                    ui_settings[key] = value
        before = dict(ui_settings)
        migrate_device_settings(ui_settings)
        moved = {k: v for k, v in ui_settings.items() if before.get(k) != v}
        if moved:
            # The device's file holds the move from here on: written now
            # by the instance that writes it (the app's), so the one-shot
            # is committed with the read; a throwaway reader (migrate
            # False, or the service's device=False) keeps it in memory and
            # writes nothing, as it writes nothing else. Only a value that
            # moved is written (a fresh device sets the marker alone).
            self.ui.settings.update(moved)
            if "quit_with_running_sessions" in moved and self._device and self._migrate and not migrating:
                try:
                    self.ui.save()
                except OSError as exc:
                    log.warning("ui-state.json not written (%s); the move is redone next start", exc)
        # The merged view every reader sees: the service's settings and
        # this device's, the latter read from ui-state.json.
        self.settings = {**DEFAULT_SETTINGS, **service_settings, **ui_settings}
        for key in SERVICE_SCOPED_SETTINGS:
            self.settings[key] = self.ui.get_scoped(self.service_id, key)
        if migrating:
            self._migration_pending = True
            if self._migrate:
                self._commit_migration()

    def _commit_migration(self) -> None:
        """Write the split: the backup, ui-state.json, then state.json. A
        failure (an unwritable config dir) is logged and leaves every file
        as it was and the merged view in memory; the next start retries."""
        if not self._migration_pending:
            return
        try:
            if _STATE_FILE.exists():
                backup = _pre_split_backup()
                if backup.exists():
                    stamp = time.strftime("%Y%m%d-%H%M%S")
                    backup = backup.with_name(f"{backup.name}.{stamp}")
                shutil.copy2(_STATE_FILE, backup)
            self._migration_pending = False
            self._write_ui()
            self.save()
        except OSError as exc:
            self._migration_pending = True
            log.warning("state split not written (%s); will retry at the next start", exc)

    def state_file(self) -> str:
        """The file this state is read from and saved to. What the sandbox
        root's ownership is settled by (sandboxplan.SandboxHost.owns_root):
        an instance on another state.json names none of this one's boxes."""
        return str(_STATE_FILE)

    def save(self) -> None:
        """Write the service's half, state.json. Every mutator of a service
        key calls this; the device's half is _save_ui, and a mutator that
        touched both calls both."""
        if self._migration_pending:
            self._commit_migration()
            return
        payload = {
            "service_id": self.service_id,
            "names": self.names,
            "generated_names": self.generated_names,
            "cli_titles": self.cli_titles,
            "emojis": self.emojis,
            "favorites": sorted(self.favorites),
            "archived": sorted(self.archived),
            "archived_at": self.archived_at,
            "archived_projects": sorted(self.archived_projects),
            "project_worktree": self.project_worktree,
            "project_sandbox": self.project_sandbox,
            "sandboxed_sessions": dict(sorted(self.sandboxed_sessions.items())),
            "sandbox_grants": self.sandbox_grants,  # order is the payload — never sort
            "sandbox_project_grants": self.sandbox_project_grants,  # likewise
            "sandbox_tools": self.sandbox_tools,
            "project_order": self.project_order,  # order is the payload — never sort
            "virtual_projects": self.virtual_projects,
            "expanded_groups": sorted(self.expanded_groups),
            "session_prs": self.session_prs,  # order is the payload — never sort
            "session_attachments": self.session_attachments,  # newest first; never sort
            "session_drafts": self.session_drafts,
            "new_chat_drafts": self.new_chat_drafts,
            "process_baselines": self.process_baselines,
            "session_forwards": self.session_forwards,
            "pending_detaches": self.pending_detaches,
            "resume_on_start": self.resume_on_start,
            "diff_notes": self.diff_notes,
            "pending_diffs": self.pending_diffs,
            "ptys": self.ptys,
            "pty_next_id": self.pty_next_id,
            "notifications": self.notifications,  # newest first; never sort
            "settings": {
                k: v for k, v in self.settings.items()
                if k not in DEVICE_SETTINGS and k not in self._ui_only_keys
            },
        }
        uistate.write_json_atomic(_STATE_FILE, payload)
        if self.on_saved is not None:
            self.on_saved()

    def _save_ui(self) -> None:
        """Write this device's half, ui-state.json, from the merged view —
        unless this is the service's own instance (*device* False), whose
        device half is the client's to write once the migration is."""
        if self._migration_pending:
            self._commit_migration()
            return
        if not self._device:
            return
        self._write_ui()

    def _write_ui(self) -> None:
        self.ui.settings = {
            k: v for k, v in self.settings.items()
            if (k in DEVICE_SETTINGS and k not in SERVICE_SCOPED_SETTINGS) or k in self._ui_only_keys
        }
        for key in SERVICE_SCOPED_SETTINGS:
            self.ui.set_scoped(self.service_id, key, self.settings.get(key, DEFAULT_SETTINGS.get(key)))
        self.ui.save()

    def ui_state_file(self) -> str:
        """The device's file, beside state_file()."""
        return str(self.ui.path)

    # -- the shared keys, as the API carries them (SHARED_KEYS) ----------------

    def is_device_setting(self, key: str) -> bool:
        """Whether a setting is this device's (ui-state.json's): one of
        DEVICE_SETTINGS, or a key the catalogue doesn't know that the
        device's file holds."""
        return key in DEVICE_SETTINGS or key in self._ui_only_keys

    def export_key(self, name: str):
        """A shared key's value in its wire form: a set as a sorted list, a
        map as a fresh dict (its values the live ones), `settings` as the
        service's settings alone."""
        key = SHARED_KEYS[name]
        value = getattr(self, key.attr)
        if name == "settings":
            return {k: v for k, v in value.items() if not self.is_device_setting(k)}
        if key.form == SET:
            return sorted(value)
        if key.form == MAP:
            if name == "sandboxed_sessions":
                return dict(sorted(value.items()))
            return dict(value)
        if key.form == LIST:
            return list(value)
        return value

    def import_key(self, name: str, value) -> None:
        """Replace a shared key's value with *value* (wire form), cleaned as
        `_load` reads it. `settings` replaces the service's settings only:
        a key it no longer names falls back to its default, and a device
        setting in it is ignored. Falling back is right for the one place a
        whole `settings` arrives, a mirror's snapshot: the service exports
        every service setting (its merged view holds each default), so an
        omitted key is one the service does not have, and its default is
        what `get_setting` there would answer too. (A client never writes
        `settings` whole: the service refuses it.)"""
        key = SHARED_KEYS[name]
        clean = key.clean(value)
        if name == "settings":
            for k in [k for k in self.settings if not self.is_device_setting(k)]:
                if k not in clean:
                    if k in DEFAULT_SETTINGS:
                        self.settings[k] = DEFAULT_SETTINGS[k]
                    else:
                        del self.settings[k]
            for k, v in clean.items():
                if not self.is_device_setting(k):
                    self.settings[k] = v
            return
        if key.form == SET:
            clean = set(clean)
        if name == "service_id" and not clean:
            return
        setattr(self, key.attr, clean)

    def import_entry(self, name: str, entry: str, value) -> bool:
        """Set one entry of a MAP key (None removes it; for `settings`, back
        to the default). False, changing nothing, when the value is not one
        the key takes (its cleaner drops it), or for `settings` when the
        setting is a device one, one the catalogue does not name, or of a
        type other than its default's (`_setting_type_ok`)."""
        key = SHARED_KEYS[name]
        if key.form != MAP or not isinstance(entry, str) or not entry:
            return False
        target = getattr(self, key.attr)
        if name == "settings":
            if self.is_device_setting(entry):
                return False
            if value is not None and (
                entry not in DEFAULT_SETTINGS or not _setting_type_ok(entry, value)
            ):
                return False
            if value is None:
                if entry in DEFAULT_SETTINGS:
                    target[entry] = DEFAULT_SETTINGS[entry]
                else:
                    target.pop(entry, None)
            else:
                target[entry] = value
            return True
        if value is None:
            target.pop(entry, None)
            return True
        clean = key.clean({entry: value})
        if entry not in clean:
            return False
        target[entry] = clean[entry]
        return True

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
        """Archive or restore a session. An archive stamps the moment (kept
        across repeated archives of the same session — the first archive is
        the one that counts); a restore drops the stamp, so archiving again
        later starts the clock over."""
        if archived:
            self.archived.add(session_id)
            self.archived_at.setdefault(session_id, time.time())
        else:
            self.archived.discard(session_id)
            self.archived_at.pop(session_id, None)
        self.save()

    def archived_since(self, session_id: str) -> float | None:
        """When the session was archived (wall-clock seconds), or None for
        one that isn't."""
        return self.archived_at.get(session_id)

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

    # -- per-project sandboxed launches ---------------------------------------

    def project_sandbox_override(self, project_name: str) -> bool | None:
        """The project's own "new sessions are sandboxed" choice, or None to
        follow the app-wide setting."""
        return self.project_sandbox.get(project_name)

    def set_project_sandbox(self, project_name: str, sandboxed: bool) -> None:
        """Pin a project's choice; kept even when it matches the app setting,
        like the worktree pin, so a project pinned "off" stays off."""
        self.project_sandbox[project_name] = sandboxed
        self.save()

    def sandbox_for_project(self, project_name: str) -> bool:
        """Effective "new sessions are sandboxed" value for a project — the
        pin, else the setting. Whether a box can be built at all is the
        caller's question (sandboxplan.available)."""
        override = self.project_sandbox.get(project_name)
        if override is not None:
            return override
        return bool(self.get_setting("sandbox_new_sessions"))

    # -- sandboxed sessions ------------------------------------------------------

    def is_sandboxed(self, session_id: str) -> bool:
        """Whether *session_id* was launched inside a sandbox — what a
        resume rebuilds. Follows the forward chain, so a /bg fork of a
        sandboxed session answers as its origin did."""
        if not session_id:
            return False
        return (
            session_id in self.sandboxed_sessions
            or self.resolve_forward(session_id) in self.sandboxed_sessions
        )

    def set_sandboxed(self, session_id: str, sandboxed: bool, box: str | None = None) -> None:
        """Record (or clear) a session's sandbox membership, and its box:
        *box* None keeps the one the session has, a string replaces it
        ("" for none yet; anything that is not a box id is read as that).
        One write, none when nothing changes."""
        if not session_id:
            return
        if sandboxed:
            current = self.sandboxed_sessions.get(session_id)
            if box is None:
                wanted = current or ""
            else:
                wanted = box if _valid_box_id(box) else ""
            if current == wanted:
                return
            self.sandboxed_sessions[session_id] = wanted
        else:
            if session_id not in self.sandboxed_sessions:
                return
            del self.sandboxed_sessions[session_id]
        self.save()

    def sandbox_box(self, session_id: str) -> str:
        """The id of the box *session_id* runs in, or "" with none: its own,
        or — like is_sandboxed — the one at the end of its forward chain."""
        if not session_id:
            return ""
        return (
            self.sandboxed_sessions.get(session_id)
            or self.sandboxed_sessions.get(self.resolve_forward(session_id))
            or ""
        )

    def sandbox_boxes(self) -> set[str]:
        """Every box a session of the map names: what must not be removed.
        Read from the sweep's thread too, so off a snapshot."""
        return {box for box in list(self.sandboxed_sessions.values()) if box}

    def get_sandbox_grants(self, box: str) -> list[str]:
        """The directories granted to the session whose box is *box*, in
        the order they were allowed. Read from the live grants' thread too,
        so off a copy."""
        return list(self.sandbox_grants.get(box) or [])

    def set_sandbox_grants(self, box: str, grants: list[str]) -> None:
        """Persist a box's grants; an empty list drops the key, and
        anything that is not a box id is no key at all. The guard against
        granting a secret is sandboxplan.guard_sensitive, applied by the
        surface that asks — and again when a plan is built. Main loop
        only."""
        if not _valid_box_id(box):
            return
        clean = [g for g in grants if isinstance(g, str) and g.startswith("/")]
        if clean:
            if self.sandbox_grants.get(box) == clean:
                return
            self.sandbox_grants[box] = clean
        else:
            if box not in self.sandbox_grants:
                return
            del self.sandbox_grants[box]
        self.save()

    def sandbox_grant_boxes(self) -> set[str]:
        """Every box that has grants recorded."""
        return set(self.sandbox_grants)

    def get_sandbox_tools(self, box: str) -> dict[str, bool]:
        """The tool switches of the session whose box is *box*: tool name
        → on, for the tools it differs from the default on. A copy."""
        return dict(self.sandbox_tools.get(box) or {})

    def set_sandbox_tools(self, box: str, switches: dict[str, bool]) -> None:
        """Persist a box's tool switches whole; an empty map drops the key,
        and anything that is not a box id is no key at all. One write,
        none when nothing changes. Main loop only."""
        if not _valid_box_id(box):
            return
        clean = mcptools.tool_overrides(switches)
        if clean:
            if self.sandbox_tools.get(box) == clean:
                return
            self.sandbox_tools[box] = clean
        else:
            if box not in self.sandbox_tools:
                return
            del self.sandbox_tools[box]
        self.save()

    def sandbox_tool_boxes(self) -> set[str]:
        """Every box that has tool switches recorded."""
        return set(self.sandbox_tools)

    def get_sandbox_project_grants(self, key: str) -> list[str]:
        """The default grants of the project *key* (sandboxplan.
        project_key), in the order they were made defaults."""
        return list(self.sandbox_project_grants.get(key) or [])

    def set_sandbox_project_grants(self, key: str, grants: list[str]) -> None:
        """Persist a project's default grants; an empty list drops the
        key. One write, none when nothing changes. Main loop only."""
        if not isinstance(key, str) or not key.startswith("/"):
            return
        clean = [g for g in grants if isinstance(g, str) and g.startswith("/")]
        if clean:
            if self.sandbox_project_grants.get(key) == clean:
                return
            self.sandbox_project_grants[key] = clean
        else:
            if key not in self.sandbox_project_grants:
                return
            del self.sandbox_project_grants[key]
        self.save()

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
        if old_id in self.sandboxed_sessions and new_id not in self.sandboxed_sessions:
            # The same conversation, the same box.
            self.sandboxed_sessions[new_id] = self.sandboxed_sessions[old_id]
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
        self._save_ui()  # the layout and editor state copied above

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

    # -- what a stopping service leaves behind (§3.10, PR-1.12b)

    def set_resume_on_start(self, session_ids: list[str]) -> None:
        """The sessions the service ended on its way out, for a client to
        reopen: written by the service alone."""
        ids = [s for s in dict.fromkeys(session_ids) if isinstance(s, str) and s]
        if ids == self.resume_on_start:
            return
        self.resume_on_start = ids
        self.save()

    def get_resume_on_start(self) -> list[str]:
        return list(self.resume_on_start)

    # -- the diffs' marks and pending loads (§3.8, PR-1.11)

    def get_diff_notes(self, session_id: str) -> dict:
        return dict(self.diff_notes.get(session_id) or {})

    def set_diff_notes(self, session_id: str, marks: dict | None) -> None:
        """A session's marks (diffnotes records); None or empty drops
        them. Unsaved: the caller saves."""
        if not session_id:
            return
        if marks and (marks.get("notes") or marks.get("highlights")):
            self.diff_notes[session_id] = {
                "notes": list(marks.get("notes") or []),
                "highlights": list(marks.get("highlights") or []),
            }
        else:
            self.diff_notes.pop(session_id, None)

    def get_pending_diffs(self) -> dict[str, dict]:
        return {k: dict(v) for k, v in self.pending_diffs.items()}

    def set_pending_diff(self, session_id: str, args: dict | None) -> None:
        """A session's pending show_diff (the tool's arguments); None
        drops it. Unsaved: the caller saves."""
        if not session_id:
            return
        if args:
            self.pending_diffs[session_id] = dict(args)
        else:
            self.pending_diffs.pop(session_id, None)

    # -- the pty table (§3.8)

    def set_pty(self, pty_id: int, row: dict | None) -> None:
        """A pty's row as the service's PtyServer reports it; None removes
        it (the pty exited)."""
        key = str(int(pty_id))
        if row is None:
            if self.ptys.pop(key, None) is None:
                return
        else:
            if self.ptys.get(key) == row:
                return
            self.ptys[key] = dict(row)
        self.save()

    def remove_pty(self, pty_id: int) -> None:
        self.set_pty(pty_id, None)

    def get_ptys(self) -> dict[int, dict]:
        return {int(k): dict(v) for k, v in self.ptys.items()}

    def set_pty_next_id(self, next_id: int) -> None:
        """The id the service's next pty takes (PtyServer's counter)."""
        next_id = int(next_id)
        if not 1 <= next_id < 2**32 or next_id == self.pty_next_id:
            return
        self.pty_next_id = next_id
        self.save()

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

    # -- the tabs this device had open on this service (§3.21) ---------------

    def get_open_tabs(self) -> list[str]:
        """The tabs this device had open on this service, in tab order:
        session ids, and ``pty:<id>`` for a tab whose session had not
        resolved (uistate's per-service block). Empty until the service
        has named itself."""
        if not self.service_id:
            return []
        return list(self.ui.service(self.service_id).get("open_tabs") or [])

    def set_open_tabs(self, entries: list[str]) -> None:
        """Record the open tabs (see get_open_tabs); an unchanged list is
        not rewritten."""
        if not self.service_id:
            return
        clean = uistate.clean_open_tabs(entries)
        block = self.ui.service(self.service_id)
        if block.get("open_tabs") == clean:
            return
        block["open_tabs"] = clean
        self._save_ui()

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
        self._save_ui()

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
        self._save_ui()

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
        """A setting from either side: self.settings is the merged view, the
        device's keys (DEVICE_SETTINGS) read off ui-state.json at load and
        the service's off state.json."""
        return self.settings.get(key, DEFAULT_SETTINGS.get(key))

    def set_setting(self, key: str, value) -> None:
        """Write a setting to the file its side lives in: one write, to one
        file. A key no side claims (not in DEFAULT_SETTINGS) is the
        service's, as every unknown key was before the split."""
        self.settings[key] = value
        if key in DEVICE_SETTINGS:
            self._save_ui()
        else:
            self.save()

    def update_settings(self, values: dict) -> None:
        """Set several settings with a single write to disk per side touched."""
        self.settings.update(values)
        if any(k in DEVICE_SETTINGS for k in values):
            self._save_ui()
        if any(k not in DEVICE_SETTINGS for k in values):
            self.save()
