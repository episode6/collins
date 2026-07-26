"""Persistent app state: custom names, favorites, hidden sessions, settings.

Everything lives in our own config file — the agents' session data is never
modified.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

_CONFIG_BASE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
_CONFIG_DIR = _CONFIG_BASE / "agent-session-manager"
_OLD_CONFIG_DIR = _CONFIG_BASE / "claude-session-manager"  # pre-rebrand location
_STATE_FILE = _CONFIG_DIR / "state.json"
_LEGACY_NAMES_FILE = _OLD_CONFIG_DIR / "names.json"


def _migrate_old_config() -> None:
    """One-time: carry settings/names over from the old config dir name."""
    if _STATE_FILE.exists() or not _OLD_CONFIG_DIR.is_dir():
        return
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    old_state = _OLD_CONFIG_DIR / "state.json"
    if old_state.exists():
        shutil.copy2(old_state, _STATE_FILE)

DEFAULT_SETTINGS = {
    "font": "",  # empty = VTE default
    "scrollback": 10_000,
    "color_scheme": "system",  # system | light | dark
    "terminal_theme": "Default",  # VTE color palette (see themes.py)
    "easy_copy_paste": False,  # Ctrl+C copies the selection (else SIGINT), Ctrl+V pastes, right-click menu
    "language": "",  # UI language code; "" = follow the system locale
    "notify_idle": True,  # notify when a background session goes quiet
    "new_session_dir": "",  # remembered folder for new sessions (empty = ask)
    "sidebar_width": 300,  # persisted sidebar pane width in px
}


class AppState:
    def __init__(self) -> None:
        self.names: dict[str, str] = {}
        self.emojis: dict[str, str] = {}
        self.favorites: set[str] = set()
        self.hidden: set[str] = set()
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
        self.emojis = dict(data.get("emojis") or {})
        self.favorites = set(data.get("favorites") or [])
        self.hidden = set(data.get("hidden") or [])
        self.settings = {**DEFAULT_SETTINGS, **(data.get("settings") or {})}

    def save(self) -> None:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "names": self.names,
            "emojis": self.emojis,
            "favorites": sorted(self.favorites),
            "hidden": sorted(self.hidden),
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

    # -- settings ------------------------------------------------------------

    def get_setting(self, key: str):
        return self.settings.get(key, DEFAULT_SETTINGS.get(key))

    def set_setting(self, key: str, value) -> None:
        self.settings[key] = value
        self.save()
