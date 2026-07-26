# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-26. Full change history: git log for this file.

"""Persistent app state: custom names, favorites, hidden sessions, settings.

Everything lives in our own config file — the agents' session data is never
modified.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterable
from pathlib import Path

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
    "easy_copy_paste": False,  # Ctrl+C copies the selection (else SIGINT), Ctrl+V pastes, right-click menu
    "language": "",  # UI language code; "" = follow the system locale
    "notify_idle": True,  # notify when a background session goes quiet
    "auto_title_sessions": True,  # summarize each new session's first prompt into a short title
    "new_session_dir": "",  # remembered folder for new sessions (empty = ask)
    "show_tab_bar": True,  # tab bar visibility (tabs keep working underneath)
    "sidebar_width": 300,  # persisted sidebar pane width in px
    "panel_position": "bottom",  # secondary terminal panel placement: bottom | right
    "panel_size_bottom": 0,  # last-set panel height in px (0 = default fraction)
    "panel_size_right": 0,  # last-set panel width in px (0 = default fraction)
    "window_width": 1280,  # last window size (floating, unmaximized)
    "window_height": 800,
    "window_maximized": False,
    "last_active_session": "",  # session in the active tab when the last window closed
}

# Floor for a restored window, so a corrupt/absurd saved value can't produce
# an unusably tiny window.
_MIN_WINDOW_SIZE = (640, 480)


def merge_project_order(saved: list[str], names: Iterable[str]) -> list[str]:
    """Resolve the sidebar display order for `names` against the saved order.

    Names present in `saved` keep their saved relative order; names not yet
    ranked are appended alphabetically. Saved entries for projects that no
    longer exist are dropped from the result (but not from the saved list).
    """
    present = set(names)
    ranked = set(saved)
    ordered = [n for n in saved if n in present]
    ordered += sorted((n for n in present if n not in ranked), key=str.casefold)
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


class AppState:
    def __init__(self) -> None:
        self.names: dict[str, str] = {}
        self.generated_names: dict[str, str] = {}  # auto-generated titles (user names win)
        self.emojis: dict[str, str] = {}
        self.favorites: set[str] = set()
        self.hidden: set[str] = set()
        self.hidden_projects: set[str] = set()  # by project name (the group identity)
        self.project_order: list[str] = []  # user-arranged sidebar order, by project name
        self.expanded_groups: set[str] = set()  # sidebar groups the user expanded
        self.panel_states: dict[str, dict] = {}  # per-session panel open/mode/sizes
        # old session id -> the id its conversation continued under (Claude's
        # /bg forks a backgrounded session to a fresh background session).
        self.session_forwards: dict[str, str] = {}
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
        self.hidden = set(data.get("hidden") or [])
        self.hidden_projects = set(data.get("hidden_projects") or [])
        self.project_order = list(data.get("project_order") or [])
        self.expanded_groups = set(data.get("expanded_groups") or [])
        self.panel_states = {
            k: v for k, v in (data.get("panel_states") or {}).items() if isinstance(v, dict)
        }
        self.session_forwards = {
            k: v for k, v in (data.get("session_forwards") or {}).items() if isinstance(v, str)
        }
        self.settings = {**DEFAULT_SETTINGS, **(data.get("settings") or {})}

    def save(self) -> None:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "names": self.names,
            "generated_names": self.generated_names,
            "emojis": self.emojis,
            "favorites": sorted(self.favorites),
            "hidden": sorted(self.hidden),
            "hidden_projects": sorted(self.hidden_projects),
            "project_order": self.project_order,  # order is the payload — never sort
            "expanded_groups": sorted(self.expanded_groups),
            "panel_states": self.panel_states,
            "session_forwards": self.session_forwards,
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

    # -- hidden ------------------------------------------------------------

    def is_hidden(self, session_id: str) -> bool:
        return session_id in self.hidden

    def set_hidden(self, session_id: str, hidden: bool) -> None:
        if hidden:
            self.hidden.add(session_id)
        else:
            self.hidden.discard(session_id)
        self.save()

    def is_project_hidden(self, project_name: str) -> bool:
        return project_name in self.hidden_projects

    def set_project_hidden(self, project_name: str, hidden: bool) -> None:
        if hidden:
            self.hidden_projects.add(project_name)
        else:
            self.hidden_projects.discard(project_name)
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
        (Claude's /bg forks a backgrounded session to a fresh background
        session). Carries the user's metadata over — without clobbering
        anything already set on the new id — and hides the stale original
        row. One write to disk."""
        if not old_id or not new_id or old_id == new_id:
            return
        self.session_forwards[old_id] = new_id
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
        self.hidden.add(old_id)
        self.save()

    def resolve_forward(self, session_id: str) -> str:
        """Follow the forward chain (a session may be backgrounded repeatedly)
        to the latest id. Cycle-safe; returns the input when unforwarded."""
        seen = {session_id}
        while (nxt := self.session_forwards.get(session_id)) and nxt not in seen:
            session_id = nxt
            seen.add(nxt)
        return session_id

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
