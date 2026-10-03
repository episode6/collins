# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""This device's half of the app state: `~/.config/collins/ui-state.json`.

The split (the service-and-client spec, §3.8): `state.json` is what the
Collins *service* keeps about sessions and projects — names, favorites,
archives, drafts, PR records, the sandbox, the settings that describe
what the service does. Everything that describes *this device* — the
window's geometry, the sidebar's width, fonts, themes, keybindings,
sounds, the tray, Caffeine, the composer's and editor's and git page's
appearance — lives here, per device, and so does what this device keeps
*per service* it talks to: each session's dock layout and editor state,
the tabs that were open and the session that was active. In Phase 1 the
one service is the local one, and this file sits beside `state.json` on
the same machine; nothing of it ever crosses the API.

Shape:

    {"device": {"settings": {...}},
     "connections": [],            # Phase 3: the services this device knows
     "last_connection": "",        # Phase 3
     "client_id": "<uuid hex>",    # minted once, this device's name on the wire
     "services": {"<service id>": {"panel_layout": {}, "editor_states": {},
                                   "open_tabs": [], "last_active_session": ""}}}

`connections`, `last_connection` and `open_tabs` are written with their
defaults and read by nobody yet: PR-3.1 and PR-1.12 fill them in. What
this module decides on its own, where the spec left room:

- The spec's per-service block names a `panel_states` key. There is no
  such thing in the code any more: it was the pre-tree panel shape, which
  `AppState._load` migrates into `panel_layout` on read and drops. It is
  not written here either.
- `last_active_session` keeps its place in `state.DEFAULT_SETTINGS` (so
  `get_setting` keeps answering it) but is stored in the per-service
  block: a session id names a session *of a service*, and the spec's
  shape puts it there.

`UiState` is the one writer of its file, with the same atomic write
`AppState` uses (`write_json_atomic`, which lives here so `state.py` can
import it without a cycle). `AppState` owns the instance and routes every
read and write to the right side, so no call site learns which file a
key lives in — see `AppState.get_setting` and `set_setting`. Everything
read off the file is untrusted input (CLAUDE.md rule 5): shapes are
checked and what does not fit is dropped.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

# The top-level keys of state.json that are this device's, moved into the
# per-service block by the migration (AppState._load) and read and written
# there from then on. Each is a map of session id to a dict.
DEVICE_RECORDS: tuple[str, ...] = ("panel_layout", "editor_states")

# The settings that are stored in the per-service block rather than under
# "device": they name something of one service's.
SERVICE_SCOPED_SETTINGS: frozenset[str] = frozenset({"last_active_session"})

# What a per-service block holds, with its empty value.
_BLOCK_DEFAULTS: dict[str, object] = {
    "panel_layout": {},
    "editor_states": {},
    "open_tabs": [],
    "last_active_session": "",
}


def write_json_atomic(path: Path, payload: object) -> None:
    """Write *payload* as JSON to *path* through a sibling temp file and a
    rename, so a crash mid-write leaves the old file whole. The parent
    directory is created."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _is_id(value: object) -> bool:
    """A service or client id: 32 lowercase hex characters (uuid4().hex)."""
    return isinstance(value, str) and len(value) == 32 and all(c in "0123456789abcdef" for c in value)


def mint_id() -> str:
    return uuid.uuid4().hex


def _record_map(raw: object) -> dict[str, dict]:
    """A session id → dict map, anything else dropped."""
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and k and isinstance(v, dict)}


def _block(raw: object) -> dict:
    """One per-service block read off the file, every key present and of
    the right shape."""
    data = raw if isinstance(raw, dict) else {}
    block: dict = {
        "panel_layout": _record_map(data.get("panel_layout")),
        "editor_states": _record_map(data.get("editor_states")),
    }
    tabs = data.get("open_tabs")
    block["open_tabs"] = [t for t in tabs if isinstance(t, str)] if isinstance(tabs, list) else []
    last = data.get("last_active_session")
    block["last_active_session"] = last if isinstance(last, str) else ""
    return block


class UiState:
    """This device's state file. *defaults* is the catalogue of device
    settings (`state.DEFAULT_SETTINGS` narrowed to `state.DEVICE_SETTINGS`,
    less the service-scoped ones): save() writes every default back, as
    `AppState.save` does, so a new key exists in every install after its
    first save."""

    def __init__(self, path: Path, defaults: dict) -> None:
        self.path = Path(path)
        self._defaults = dict(defaults)
        self.settings: dict = dict(defaults)
        self.connections: list = []
        self.last_connection: str = ""
        self.client_id: str = ""
        self.services: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        data: dict = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except (OSError, json.JSONDecodeError):
            data = {}
        device = data.get("device")
        settings = device.get("settings") if isinstance(device, dict) else None
        if isinstance(settings, dict):
            self.settings = {**self._defaults, **settings}
        connections = data.get("connections")
        self.connections = (
            [c for c in connections if isinstance(c, dict)] if isinstance(connections, list) else []
        )
        last = data.get("last_connection")
        self.last_connection = last if isinstance(last, str) else ""
        client_id = data.get("client_id")
        self.client_id = client_id if _is_id(client_id) else mint_id()
        services = data.get("services")
        self.services = (
            {sid: _block(block) for sid, block in services.items() if _is_id(sid)}
            if isinstance(services, dict)
            else {}
        )

    def exists(self) -> bool:
        return self.path.exists()

    def save(self) -> None:
        payload = {
            "device": {"settings": {**self._defaults, **self.settings}},
            "connections": self.connections,
            "last_connection": self.last_connection,
            "client_id": self.client_id,
            "services": self.services,
        }
        write_json_atomic(self.path, payload)

    # -- per-service blocks ------------------------------------------------

    def known_services(self) -> list[str]:
        return list(self.services)

    def service(self, service_id: str) -> dict:
        """The block this device keeps for *service_id*, created empty on
        first access. The dicts inside are the live ones: a caller that
        mutates them saves through the owner (AppState)."""
        block = self.services.get(service_id)
        if block is None:
            block = {k: (dict(v) if isinstance(v, dict) else list(v)) for k, v in _BLOCK_DEFAULTS.items()}
            block["last_active_session"] = ""
            self.services[service_id] = block
        return block

    def get_scoped(self, service_id: str, key: str):
        """A service-scoped setting (SERVICE_SCOPED_SETTINGS) for one
        service; the block's default when it was never written."""
        return self.service(service_id).get(key, _BLOCK_DEFAULTS.get(key))

    def set_scoped(self, service_id: str, key: str, value) -> None:
        self.service(service_id)[key] = value
