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

`connections` and `last_connection` are written with their defaults and
read by nobody yet: PR-3.1 fills them in. `open_tabs` is the tabs this
device had open on that service, in tab order (session ids, and
``pty:<id>`` for a tab whose session had not resolved): written by the
window on every tab open, close, detach and reorder, read at launch and
after a reconnect to reattach or resume each (PR-1.12c, §3.21; see
`AppState.get_open_tabs`). What this module decides on its own, where the
spec left room:

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
import logging
import shutil
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

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


# How many open tabs a block keeps, and how long an entry may be: a session
# id is a uuid, ``pty:<id>`` a few digits more.
OPEN_TABS_MAX = 256
_OPEN_TAB_MAX_LEN = 128


def clean_open_tabs(raw: object) -> list[str]:
    """An `open_tabs` list as the file (or a caller) gave it, cut to what
    fits: strings with no control characters, at most `_OPEN_TAB_MAX_LEN`
    long, a ``pty:`` entry naming a positive integer, no duplicates, at
    most `OPEN_TABS_MAX` (rule 5: the file is foreign content)."""
    if not isinstance(raw, list):
        return []
    clean: list[str] = []
    for entry in raw:
        if not isinstance(entry, str) or not entry or len(entry) > _OPEN_TAB_MAX_LEN:
            continue
        if any(ord(c) < 32 or c == "\x7f" for c in entry) or entry in clean:
            continue
        if entry.startswith("pty:") and not (entry[4:].isdigit() and int(entry[4:]) > 0):
            continue
        clean.append(entry)
        if len(clean) >= OPEN_TABS_MAX:
            break
    return clean


def open_tab_pty(entry: str) -> int | None:
    """The pty id an ``open_tabs`` entry names (``pty:<id>``), else None."""
    if isinstance(entry, str) and entry.startswith("pty:") and entry[4:].isdigit():
        return int(entry[4:])
    return None


def _block(raw: object) -> dict:
    """One per-service block read off the file, every key present and of
    the right shape."""
    data = raw if isinstance(raw, dict) else {}
    block: dict = {
        "panel_layout": _record_map(data.get("panel_layout")),
        "editor_states": _record_map(data.get("editor_states")),
    }
    block["open_tabs"] = clean_open_tabs(data.get("open_tabs"))
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
        # The setting keys the file actually held (as opposed to defaults
        # filled in): what "ui-state.json lacks the key" means to the
        # migration's merge (AppState._load).
        self.present_keys: frozenset[str] = frozenset()
        # Whether the file was there and could not be read as a dict: it is
        # copied aside as <name>.corrupt before the first save overwrites it.
        self._corrupt = False
        self._load()

    def _load(self) -> None:
        data: dict = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
            else:
                self._corrupt = True
        except json.JSONDecodeError:
            self._corrupt = True
        except OSError:
            data = {}
        device = data.get("device")
        settings = device.get("settings") if isinstance(device, dict) else None
        if isinstance(settings, dict):
            self.settings = {**self._defaults, **settings}
            self.present_keys = frozenset(k for k in settings if isinstance(k, str))
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

    def save(self) -> None:
        if self._corrupt and self.path.exists():
            aside = self.path.with_name(self.path.name + ".corrupt")
            try:
                shutil.copy2(self.path, aside)
                log.warning("ui-state.json could not be read; the old file is kept as %s", aside)
            except OSError as exc:
                log.warning("ui-state.json could not be read, and copying it aside failed: %s", exc)
            self._corrupt = False
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

    def has_service(self, service_id: str) -> bool:
        """Whether the file held a block for *service_id* (as opposed to
        one service() would make on first access)."""
        return service_id in self.services

    def service(self, service_id: str) -> dict:
        """The block this device keeps for *service_id*, created empty on
        first access. The dicts inside are the live ones: a caller that
        mutates them saves through the owner (AppState)."""
        block = self.services.get(service_id)
        if block is None:
            block = {
                k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
                for k, v in _BLOCK_DEFAULTS.items()
            }
            self.services[service_id] = block
        return block

    def get_scoped(self, service_id: str, key: str):
        """A service-scoped setting (SERVICE_SCOPED_SETTINGS) for one
        service; the block's default when it was never written."""
        return self.service(service_id).get(key, _BLOCK_DEFAULTS.get(key))

    def set_scoped(self, service_id: str, key: str, value) -> None:
        self.service(service_id)[key] = value
