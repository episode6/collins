# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Connections: the services this device knows (split-service spec §3.24,
D2, D10). GTK-free.

`ui-state.json` keeps a `connections` list and a `last_connection`
(`uistate.UiState`); this module is the one reader and validator of the
list, and the shape a connection has in the client:

    {"id": "<32 hex>", "name": "build box", "kind": "ssh",
     "target": "collins@build.example.net:2222", "remote_app_id": ""}

`kind` is ``local`` (the service on this machine, found or started by
the connection manager: there is exactly one such connection, `LOCAL`,
never written to the file) or ``ssh`` (a service on another machine,
reached through `sshforward`: `ssh TARGET collins-service --print-socket`
once, then the `-L` forward of the Unix socket, §3.2). `target` is what
ssh is handed as its destination, ``[user@]host[:port]``; `remote_app_id`
is the app id the remote service runs under when it is not the default.

Everything read off the file is foreign content (CLAUDE.md rule 5):
shapes are checked, sizes bounded, a row that does not fit is dropped,
and nothing here is ever formatted into a shell line (`sshremote` builds
an argv; a target that would read as an ssh option is refused by its
`parse_target`, the one grammar of a target). The
`--connect NAME` main option and the connect dialog resolve a connection
by name through `find`; ``local`` always names `LOCAL`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace

from .sshremote import Target, parse_target
from .uistate import UiState, mint_id

KIND_LOCAL = "local"
KIND_SSH = "ssh"
KINDS = (KIND_LOCAL, KIND_SSH)

# The id `LOCAL` carries and the one name that always resolves to it.
LOCAL_ID = "local"
LOCAL_NAME = "local"

# Bounds on what the file may hold (rule 5).
CONNECTIONS_MAX = 64
NAME_MAX = 64
APP_ID_MAX = 255

# A connection id: uuid4().hex, as the other ids of ui-state.json.
_ID_RE = re.compile(r"^[0-9a-f]{32}$")
# An app id as GApplication accepts one, narrowed to what a path takes.
_APP_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+)+$")


def valid_name(name: object) -> bool:
    """A name a person typed: non-empty, at most `NAME_MAX` characters, no
    control characters, not blank."""
    if not isinstance(name, str) or not name.strip() or len(name) > NAME_MAX:
        return False
    # No C0 control, and no Unicode format character either (a
    # right-to-left override, a zero-width joiner): the name is shown in
    # the banner, the dialog and `--connect` as it is (PR 623's review).
    return not any(ord(c) < 32 or c == "\x7f" or unicodedata.category(c) == "Cf" for c in name)


def valid_app_id(app_id: object) -> bool:
    return isinstance(app_id, str) and len(app_id) <= APP_ID_MAX and bool(_APP_ID_RE.fullmatch(app_id))


@dataclass(frozen=True)
class Connection:
    id: str
    name: str
    kind: str
    target: str = ""
    remote_app_id: str = ""
    # Not stored: whether this is the local connection.
    extra: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def is_local(self) -> bool:
        return self.kind == KIND_LOCAL

    def parsed_target(self) -> Target | None:
        return parse_target(self.target)

    def to_record(self) -> dict:
        """The row as the file holds it."""
        record = {"id": self.id, "name": self.name, "kind": self.kind, "target": self.target}
        if self.remote_app_id:
            record["remote_app_id"] = self.remote_app_id
        return record


# The one local connection: the service on this machine. Never in the file.
LOCAL = Connection(id=LOCAL_ID, name=LOCAL_NAME, kind=KIND_LOCAL)


def from_record(raw: object) -> Connection | None:
    """One row of the file as a `Connection`, or None when it does not fit
    (rule 5). Only ``ssh`` rows live in the file: a ``local`` row, or an
    unknown kind, is dropped."""
    if not isinstance(raw, dict):
        return None
    connection_id = raw.get("id")
    if not isinstance(connection_id, str) or not _ID_RE.fullmatch(connection_id):
        return None
    name = raw.get("name")
    if not valid_name(name):
        return None
    if raw.get("kind") != KIND_SSH:
        return None
    target = raw.get("target")
    if parse_target(target) is None:
        return None
    app_id = raw.get("remote_app_id", "")
    if app_id in (None, ""):
        app_id = ""
    elif not valid_app_id(app_id):
        return None
    return Connection(id=connection_id, name=name.strip(), kind=KIND_SSH, target=target,
                      remote_app_id=app_id)


def clean(rows: object) -> list[Connection]:
    """The file's list, cut to what fits: valid rows, distinct ids, the
    first `CONNECTIONS_MAX`."""
    if not isinstance(rows, list):
        return []
    seen: set[str] = set()
    out: list[Connection] = []
    for raw in rows:
        connection = from_record(raw)
        if connection is None or connection.id in seen:
            continue
        seen.add(connection.id)
        out.append(connection)
        if len(out) >= CONNECTIONS_MAX:
            break
    return out


def new_ssh(name: str, target: str, remote_app_id: str = "") -> Connection:
    """A connection a person just added. Raises ValueError on a bad name,
    target or app id, with a word for the dialog."""
    if not valid_name(name):
        raise ValueError("name")
    if parse_target(target) is None:
        raise ValueError("target")
    if remote_app_id and not valid_app_id(remote_app_id):
        raise ValueError("remote_app_id")
    return Connection(id=mint_id(), name=name.strip(), kind=KIND_SSH, target=target.strip(),
                      remote_app_id=remote_app_id)


def find(connections: list[Connection], name_or_id: str) -> Connection | None:
    """The connection *name_or_id* names: ``local`` (any case) is `LOCAL`;
    else the first whose name matches exactly, then case-insensitively,
    then by id."""
    if not isinstance(name_or_id, str) or not name_or_id:
        return None
    if name_or_id.lower() == LOCAL_NAME or name_or_id == LOCAL_ID:
        return LOCAL
    for connection in connections:
        if connection.name == name_or_id:
            return connection
    lowered = name_or_id.lower()
    for connection in connections:
        if connection.name.lower() == lowered:
            return connection
    for connection in connections:
        if connection.id == name_or_id:
            return connection
    return None


class Connections:
    """The list and `last_connection` as `ui-state.json` holds them, over
    one `UiState` (the device's own, the one the mirror writes through, so
    the file has one writer). Reads validate, writes save."""

    def __init__(self, ui: UiState) -> None:
        self._ui = ui

    def all(self) -> list[Connection]:
        return clean(self._ui.connections)

    def get(self, connection_id: str) -> Connection | None:
        for connection in self.all():
            if connection.id == connection_id:
                return connection
        return LOCAL if connection_id == LOCAL_ID else None

    def find(self, name_or_id: str) -> Connection | None:
        return find(self.all(), name_or_id)

    def add(self, connection: Connection) -> Connection:
        """Add (or, by id, replace) a connection; returns what was stored.
        A `LOCAL` is never stored. Raises ValueError when the list is full."""
        if connection.is_local or from_record(connection.to_record()) is None:
            raise ValueError("connection")
        rows = [c for c in self.all() if c.id != connection.id]
        if len(rows) >= CONNECTIONS_MAX:
            raise ValueError("full")
        rows.append(connection)
        self._write(rows)
        return connection

    def remove(self, connection_id: str) -> bool:
        rows = self.all()
        kept = [c for c in rows if c.id != connection_id]
        if len(kept) == len(rows):
            return False
        self._write(kept)
        if self._ui.last_connection == connection_id:
            self._ui.last_connection = ""
            self._ui.save()
        return True

    def rename(self, connection_id: str, name: str) -> Connection | None:
        current = self.get(connection_id)
        if current is None or current.is_local or not valid_name(name):
            return None
        return self.add(replace(current, name=name.strip()))

    @property
    def last(self) -> Connection | None:
        """The connection used last, when the file names one that still
        exists; None for the local one or nothing."""
        last = self._ui.last_connection
        if not last or last == LOCAL_ID:
            return None
        return self.get(last)

    def set_last(self, connection: Connection | None) -> None:
        """Record *connection* as the one in use (`LOCAL` or None clears it,
        so the next launch connects locally)."""
        value = "" if connection is None or connection.is_local else connection.id
        if self._ui.last_connection == value:
            return
        self._ui.last_connection = value
        self._ui.save()

    def _write(self, rows: list[Connection]) -> None:
        self._ui.connections = [c.to_record() for c in rows]
        self._ui.save()
