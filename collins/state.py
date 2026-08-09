# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-08. Full change history: git log for this file.

"""Persistent app state: custom names, favorites, archived sessions, settings.

Everything lives in our own config file — the agents' session data is never
modified.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterable
from pathlib import Path

from . import mcptools

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
    "auto_title_sessions": True,  # summarize each new session's first prompt into a short title
    # Claude models for the app's own headless runs, as --model values.
    # "" = automatic: the newest model of the setting's preferred tier, or
    # the weakest model offered should that tier ever be dropped (see
    # claudemodels.resolve_model).
    "title_model": "",  # session title generation ("" = newest Haiku)
    "icon_model": "",  # the sidebar's Generate Icon ("" = newest Sonnet)
    # Retitle a session to its newest pull request's title as PRs are
    # detected (see SessionStore.apply_pr_title). Fills the generated-name
    # slot, so a manual rename always wins.
    "pr_title_sessions": False,
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
    # What to do instead of the confirmation dialog when a running session's
    # tab has to close: ask (the dialog, as before) | exit | background.
    "archive_running_session": "ask",  # archiving a session whose tab is busy
    "quit_with_running_sessions": "ask",  # closing a window while sessions run
    "show_tab_bar": True,  # tab bar visibility (tabs keep working underneath)
    # The floating attach-file button over each agent terminal's bottom-left
    # corner (see terminal.py). Off hides it everywhere; the header's attach
    # button keeps working either way.
    "attach_overlay_button": True,
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
    "sidebar_width": 300,  # persisted sidebar pane width in px
    "panel_position": "bottom",  # secondary terminal panel placement: bottom | right
    "panel_size_bottom": 0,  # last-set panel height in px (0 = default fraction)
    "panel_size_right": 0,  # last-set panel width in px (0 = default fraction)
    "window_width": 1280,  # last window size (floating, unmaximized)
    "window_height": 800,
    "window_maximized": False,
    "last_active_session": "",  # session in the active tab when the last window closed
    "editor_width": 0,  # last-set editor panel width in px (0 = default fraction)
    "editor_style_scheme": "",  # GtkSource style scheme id; "" = follow the app's light/dark scheme
    "editor_font": "",  # empty = system monospace
    "editor_show_line_numbers": True,
    "editor_show_hidden_files": True,
    "editor_pop_out_screen_width": 1600,  # scaled px; this wide or narrower opens popped out (0 = never)
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
        self.panel_states: dict[str, dict] = {}  # per-session panel open/mode/sizes
        self.editor_states: dict[str, dict] = {}  # per-session editor open/width/files/cursors
        # session id -> the PRs it has opened, oldest first, as prstatus
        # records ({number, url, repository?, title?, state?, checks?,
        # mergeable?, unresolved? — see to_record). The status in one is the
        # last that was fetched, not the current one.
        self.session_prs: dict[str, list] = {}
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
        self.panel_states = {
            k: v for k, v in (data.get("panel_states") or {}).items() if isinstance(v, dict)
        }
        self.editor_states = {
            k: v for k, v in (data.get("editor_states") or {}).items() if isinstance(v, dict)
        }
        self.session_prs = {
            k: v for k, v in (data.get("session_prs") or {}).items() if isinstance(v, list)
        }
        self.process_baselines = {
            k: v for k, v in (data.get("process_baselines") or {}).items() if isinstance(v, list)
        }
        self.session_forwards = {
            k: v for k, v in (data.get("session_forwards") or {}).items() if isinstance(v, str)
        }
        self.pending_detaches = {
            k: v for k, v in (data.get("pending_detaches") or {}).items() if isinstance(v, dict)
        }
        self.settings = {**DEFAULT_SETTINGS, **(data.get("settings") or {})}

    def save(self) -> None:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "names": self.names,
            "generated_names": self.generated_names,
            "emojis": self.emojis,
            "favorites": sorted(self.favorites),
            "archived": sorted(self.archived),
            "archived_projects": sorted(self.archived_projects),
            "project_worktree": self.project_worktree,
            "project_order": self.project_order,  # order is the payload — never sort
            "virtual_projects": self.virtual_projects,
            "expanded_groups": sorted(self.expanded_groups),
            "panel_states": self.panel_states,
            "editor_states": self.editor_states,
            "session_prs": self.session_prs,  # order is the payload — never sort
            "process_baselines": self.process_baselines,
            "session_forwards": self.session_forwards,
            "pending_detaches": self.pending_detaches,
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
        if old_id in self.panel_states and new_id not in self.panel_states:
            self.panel_states[new_id] = dict(self.panel_states[old_id])
        if old_id in self.editor_states and new_id not in self.editor_states:
            self.editor_states[new_id] = dict(self.editor_states[old_id])
        if old_id in self.session_prs and new_id not in self.session_prs:
            # The same conversation under a new id: the PRs it opened are the
            # fork's too, and the fork's transcript doesn't repeat them.
            self.session_prs[new_id] = list(self.session_prs[old_id])
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

    # -- per-session panel state -------------------------------------------

    def get_panel_state(self, session_id: str) -> dict | None:
        return self.panel_states.get(session_id)

    def set_panel_state(self, session_id: str, state: dict | None) -> None:
        """Persist a session's panel snapshot ({"open", "mode", "sizes"});
        None or empty removes the entry. Tabs snapshot on every close, so an
        unchanged snapshot is deliberately not rewritten to disk."""
        if state:
            if self.panel_states.get(session_id) == state:
                return
            self.panel_states[session_id] = state
        else:
            if session_id not in self.panel_states:
                return
            del self.panel_states[session_id]
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

        Only their identity is saved — CI status is refetched on every run, so
        a chip never shows a check that went stale overnight. A tab re-derives
        this list on every transcript poll, so an unchanged one is deliberately
        not rewritten to disk.
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
