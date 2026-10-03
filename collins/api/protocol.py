# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The message table of the service/client API, kept free of GTK.

What `mcptools` is for the session MCP tools, this module is for the API
between `collins-service` and its clients (spec §3.2,
~/specs/collins/split-service-and-client.md): the table of message types
with the fields each carries and their bounds, `validate` and
`validate_response`, the JSON framing, the 16-byte binary header, the
protocol version window, the capability names, the error codes and the
transport's numbers (frame and queue sizes, keepalive). It imports nothing
but the standard library, so the unit suite tests all of it, and so do both
halves: the service validates what clients send and every client validates
what the service sends. Rule 5 holds in both directions: every frame is
foreign content.

Frames
------

Text frames are JSON objects::

    request   {"t": "<type>", "id": <int>, ...}            client -> service
    response  {"re": <id>, "ok": true, ...}                 service -> client
              {"re": <id>, "ok": false, "error": "<code>", "msgid": "...", "args": {...}}
    event     {"t": "<type>", ...}                          either way, no id

A request always goes client -> service and is answered by exactly one
response; an event is never answered. Which peer may send each type, and in
which form, is in `TYPES`: a type arriving from the wrong peer is refused
with `direction`, a type nobody knows with `unknown`. Fields a type does not
list are dropped (versioning is additive), at every depth of a closed shape.
A refusal's `msgid` is an English source string with `{name}` placeholders
and `args` fills them; the client translates it with `i18n._()` and
`str.format_map` (§3.14).

Binary frames carry bytes behind a 16-byte big-endian header,
``tag:u8 flags:u8 reserved:u16 stream:u32 offset:u64``: tag 0x01 is pty
output (service -> client, flags REDRAW 0x01 and REDRAW_END 0x02), 0x02 pty
input (client -> service), 0x03 a blob chunk (either way). For output and
input `stream` is the pty's number; for a blob it names the transfer.

Versioning
----------

`PROTOCOL` is bumped only for a change no capability can express. New types
and fields arrive behind a name in the hello reply's `caps`. A peer speaks
every protocol from its `MIN_PROTOCOL` to its `PROTOCOL`, and the window is
at most two wide (a client speaks the service's protocol or the one before
it, D13), so an upgraded client keeps talking to a service with running
agents. Both sides call `negotiate` with their own and the peer's numbers
and arrive at the same answer; None is the mismatch dialog of §3.10.

Error codes
-----------

The codes this module and the service send (`ERRORS`); a receiver accepts
any code shaped like one, since a newer peer may add codes behind a cap:

- ``unknown``: a message type this peer does not know.
- ``invalid``: the type is known and the message is not a valid one of it
  (a missing or ill-typed field, a bound exceeded, a request without an id).
- ``direction``: a known type from the peer that may not send it.
- ``protocol``: hello's protocol window does not meet this peer's.
- ``sequence``: a request this connection may not make yet (anything
  before hello, a write to a pty before attach).
- ``gone``: what the request names does not exist (any more): a pty, a
  session, a tool call.
- ``refused``: the service understood and declined (a prompt that would not
  land, a path a sandbox will not take); `msgid` says why.
- ``failed``: the service tried and it went wrong; `msgid` says how.

Shapes the spec left to this module
-----------------------------------

The spec names the types and their gist; these choices are this module's,
each taken from the code the message replaces:

- Request ids are integers 0..2**53-1, so a future JavaScript client reads
  them exactly.
- A pty is named by an integer 1..2**32-1, the same number the binary
  frames carry as `stream` (output and input frames name their pty by it).
- Session, client, service, box, notification and tool-call ids share one
  shape: 1..128 of ``[A-Za-z0-9._:-]``, starting alphanumeric (uuids, box
  ids, ``green:<id>``, ``update:<version>``, ``placeholder-3`` all fit).
- Every path is absolute, at most 4096 characters, with no NUL (§3.11:
  every path is the service machine's).
- Text payloads (a prompt, a paint, a cut's text, a tool reply) are at most
  1 MiB of characters; `encode` refuses a frame over 1 MiB of bytes, which
  is the tighter limit in practice and the sender's to respect.
- Colours are ``#rrggbb``, the form the spec's hello shows.
- `hello` also carries an optional `min_protocol` (default: its
  `protocol`), so a service can agree on an older protocol with a newer
  client instead of only refusing it.
- `subscribe`'s snapshot arrives as ordinary events (`item`, `pty`, `pr`,
  `state.set`, `notify`) sent before the reply, and the reply, carrying the
  counts of `item` and `pty` events sent, marks the snapshot complete. That
  keeps every frame under the 1 MiB send cap with no paging of its own.
- `attach`'s reply carries the grid the redraw is painted at, whether this
  client is the pty's active client and, when not, `sized_for`, the active
  client's device for the "Sized for <device>" bar (§3.3).
- `resize`, `focus` and `theme` are events: fire and forget, and frequent.
  `focus` carries `focused`; `theme` carries `term`, hello's shape.
- `spawn` carries `SessionOptions`' fields (bar `sandbox_plan`, which the
  service writes), `kind` ("agent" or "shell"), `session` (the session an
  agent resumes, the session a shell belongs to), the prompt, and the
  client's grid; its reply names the new pty and its grid.
- `mention` carries `TerminalTab.add_file_to_chat`'s path and line range;
  `cut` is answered with the box's text (a paste Collins can't read is a
  `refused`); `close` carries a mode: ``exit`` (the graceful exit),
  ``background`` (the /bg handoff) or ``kill`` (the force close). Hide is a
  client gesture and never reaches the service.
- `paint` carries the text to insert into the pty's output stream, as
  `feed_message` takes it.
- `state.set` and `state.get` name a top-level key (``settings``, ``names``,
  ...) and optionally an `entry` inside a dict-valued key (a setting's name,
  a session id); `state.set`'s `value` is any bounded JSON value, null
  removing an entry. The service decides which keys a client may write.
  `state.set` is also the service's event for a change, in the same shape.
- `item` carries a `SessionItem`'s bindable properties and the `Session`
  facts the sidebar reads, every one optional but `session`, plus
  `removed`. `pty` is a row of the pty table (§3.8) as a client needs it.
- `pr` carries a session's PR records in `prstatus.to_record`'s shape, as
  bounded JSON objects: `prstatus.from_record` re-validates them on arrival.
- `notify` carries a `notifycenter.Notification` record with the body as
  `msgid` and `args` (§3.13, §3.14); the record's id rides as
  `notification`, since `id` on any frame makes it a request. `seen` carries notification ids and/or
  a session, from a client as a request and from the service to every other
  client as an event, so unread is one number everywhere.
- `tool` is a UI-bound tool call the service hands the active client:
  `call` (its id), the session, the tool's name and its arguments, a JSON
  object bounded here and re-validated against `mcptools.validate_args` by
  the client. `tool-reply` is the client's event back, `call`, `ok` and
  `text`, `mcptools.run_tool_call`'s ``(ok, text)``.
- The `sandbox.*` requests name the session's pty (the chip lives on a
  running tab). `sandbox.grants` answers with what the chip draws;
  `sandbox.allow` and `sandbox.revoke` take a `scope`, ``session`` (this
  session's box) or ``project`` (the project's defaults for new sessions);
  `sandbox.tools` sets switches (`tools`, name -> on) or `reset`s them. The
  `sandbox` event says something changed, and carries a grant's `delivery`
  (``sandboxgrants.Delivery``'s path, status, inside, linked) for the toast.
- `service.restart` takes `when`, ``now`` or ``idle`` (§3.10's *Restart
  when idle*); `service.status` answers with the version, the protocol and
  the counts of ptys, busy sessions and clients.
- Enumerations a client sends are closed (`choices`); strings the service
  sends that a later service may extend (a status, a notification kind, a
  grant status) are bounded strings, mapped by the receiver.
"""

from __future__ import annotations

import json
import math
import re
import struct
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

# ---- versions and capabilities ----------------------------------------------

# The protocol this build speaks, and the oldest it still speaks. Bumped only
# for a change no capability can express; pinned by tests/test_protocol.py.
PROTOCOL = 1
MIN_PROTOCOL = 1

# Capability names a service may list in its hello reply. A new type or field
# ships behind a new name here; a peer uses it only when the other lists it.
CAP_LOCAL = "local"  # the service offers the local-extras proof (§3.2)
CAPABILITIES = frozenset({CAP_LOCAL})

# The local-extras proof: random bytes in a 0600 file the service names.
LOCAL_PROOF_BYTES = 32
LOCAL_PROOF_MAX = 4096

# ---- the transport's numbers -------------------------------------------------

MAX_FRAME = 1024 * 1024  # never send a frame over this; larger transfers are chunked
MAX_INCOMING = 16 * 1024 * 1024  # libsoup's max-incoming-payload-size, both ends
QUEUE_BYTES = 4 * 1024 * 1024  # one client's send queue per pty; overflow means a redraw
KEEPALIVE_INTERVAL_S = 10  # libsoup keepalive-interval, both ends (F15)
KEEPALIVE_PONG_TIMEOUT_S = 10  # libsoup keepalive-pong-timeout, both ends

# ---- the binary header -------------------------------------------------------

HEADER = struct.Struct(">BBHIQ")  # tag flags reserved stream offset, big endian
HEADER_SIZE = HEADER.size  # 16
MAX_PAYLOAD = MAX_FRAME - HEADER_SIZE

TAG_OUTPUT = 0x01  # pty output, filtered, live: service -> client
TAG_INPUT = 0x02  # what the client's VTE committed: client -> service
TAG_BLOB = 0x03  # part of a transfer named by `stream`: either way

FLAG_REDRAW = 0x01  # output: part of a redraw (§3.3 "Attach")
FLAG_REDRAW_END = 0x02  # output: the redraw's last frame

U8_MAX = 0xFF
U32_MAX = 0xFFFF_FFFF
U64_MAX = 0xFFFF_FFFF_FFFF_FFFF

# ---- peers, forms, errors ----------------------------------------------------

CLIENT = "client"
SERVICE = "service"
PEERS = frozenset({CLIENT, SERVICE})

REQUEST = "request"
EVENT = "event"

ERROR_UNKNOWN = "unknown"
ERROR_INVALID = "invalid"
ERROR_DIRECTION = "direction"
ERROR_PROTOCOL = "protocol"
ERROR_SEQUENCE = "sequence"
ERROR_GONE = "gone"
ERROR_REFUSED = "refused"
ERROR_FAILED = "failed"
ERRORS = frozenset(
    {
        ERROR_UNKNOWN,
        ERROR_INVALID,
        ERROR_DIRECTION,
        ERROR_PROTOCOL,
        ERROR_SEQUENCE,
        ERROR_GONE,
        ERROR_REFUSED,
        ERROR_FAILED,
    }
)

# Which peer may send each binary tag.
FRAME_SENDERS: Mapping[int, frozenset[str]] = {
    TAG_OUTPUT: frozenset({SERVICE}),
    TAG_INPUT: frozenset({CLIENT}),
    TAG_BLOB: frozenset({CLIENT, SERVICE}),
}

# ---- bounds ------------------------------------------------------------------

REQUEST_ID_MAX = 2**53 - 1
TYPE_MAX = 64
ID_MAX = 128
PATH_MAX = 4096
TEXT_MAX = 1024 * 1024  # characters: prompt, paint, cut, tool reply
NAME_MAX = 1024  # a title, a project's name, a display name
HOST_MAX = 255  # a hostname, a device's name
VERSION_MAX = 64
SHORT_MAX = 32  # a status, a kind, a mode, a permission mode, an effort
MODEL_MAX = 128
PREVIEW_MAX = 4096
MSGID_MAX = 4096
ARGS_MAX = 32  # entries in a msgid's args
ARG_TEXT_MAX = 4096
KEY_MAX = 64  # a state key
CAPS_MAX = 64
ADD_DIRS_MAX = 64
PRS_MAX = 100
SEEN_MAX = 200  # notifycenter.ROW_CAP: the most rows a history holds
GRANTS_MAX = 256
TOOLS_MAX = 64
MAX_COLS = 2000
MAX_ROWS = 1000
VTE_VERSION_MAX = 99_999_999
LINE_MAX = 2**31 - 1
PID_MAX = 2**31 - 1
COUNT_MAX = 2**31 - 1
SIZE_MAX = 2**53 - 1
JSON_MAX_DEPTH = 32  # a free JSON value's nesting
JSON_MAX_NODES = 100_000  # a free JSON value's values, all depths together

_TYPE_RE = re.compile(r"[a-z][a-z0-9.-]{0,63}")
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}")
_ARG_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_CAP_RE = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
_CODE_RE = re.compile(r"[a-z][a-z-]{0,31}")
_COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}")
_HEX_RE = re.compile(rf"(?:[0-9a-f]{{2}}){{1,{LOCAL_PROOF_MAX}}}")
_LOCALE_RE = re.compile(r"[A-Za-z0-9_.@-]{1,64}")
_TOOL_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")

# ---- field specs -------------------------------------------------------------

# The kinds a field can be. "json" is any JSON value within the depth and
# node bounds; "object" is a closed shape whose unknown fields are dropped;
# "map" is a dict of free keys to one kind of value; "scalar" is a string,
# number, bool or null (a msgid's arg).
K_STR = "string"
K_INT = "integer"
K_NUM = "number"
K_BOOL = "boolean"
K_LIST = "list"
K_OBJ = "object"
K_MAP = "map"
K_JSON = "json"
K_JSON_OBJECT = "json-object"
K_PATH = "path"
K_URL = "url"
K_SCALAR = "scalar"


@dataclass(frozen=True)
class Field:
    """One field's kind and bounds.

    `low` / `high` bound a string's length, a number's value, or a list's or
    map's size. `pattern` must match a whole string; `choices` closes it.
    `item` is a list's items or a map's values, `fields` an object's shape
    (with `one_of`: at least one of those must be present). `key` bounds a
    map's keys.
    """

    kind: str
    required: bool = False
    nullable: bool = False
    low: int | float | None = None
    high: int | float | None = None
    pattern: re.Pattern | None = None
    choices: frozenset[str] | None = None
    item: Field | None = None
    fields: Mapping[str, Field] | None = None
    one_of: tuple[str, ...] = ()
    key: re.Pattern | None = None


def _s(high: int, low: int = 0, **kw) -> Field:
    return Field(K_STR, low=low, high=high, **kw)


def _i(low: int, high: int, **kw) -> Field:
    return Field(K_INT, low=low, high=high, **kw)


def _req(spec: Field) -> Field:
    """*spec*, required."""
    return replace(spec, required=True)


def _null(spec: Field) -> Field:
    """*spec*, which may also be null."""
    return replace(spec, nullable=True)


_ID = Field(K_STR, low=1, high=ID_MAX, pattern=_ID_RE)
_PTY = _i(1, U32_MAX)
_COLS = _i(1, MAX_COLS)
_ROWS = _i(1, MAX_ROWS)
_PATH = Field(K_PATH)
_TEXT = _s(TEXT_MAX)
_NAME = _s(NAME_MAX)
_HOST = _s(HOST_MAX)
_SHORT = _s(SHORT_MAX)
_BOOL = Field(K_BOOL)
_NUM = Field(K_NUM)
_COUNT = _i(0, COUNT_MAX)
_MSGID = _s(MSGID_MAX, low=1)
_ARGS = Field(K_MAP, high=ARGS_MAX, key=_ARG_KEY_RE, item=Field(K_SCALAR))
_JSON = Field(K_JSON)
_TOOL_NAME = Field(K_STR, low=1, high=64, pattern=_TOOL_RE)
_CODE = Field(K_STR, low=1, high=32, pattern=_CODE_RE)
# A session field the service fills before the id has resolved: may be "".
_SESSION_OR_EMPTY = Field(K_STR, high=ID_MAX, pattern=re.compile(f"(?:{_ID_RE.pattern})?"))

_TERM = Field(
    K_OBJ,
    fields={
        "vte": _i(0, VTE_VERSION_MAX),
        "fg": Field(K_STR, pattern=_COLOR_RE, high=7),
        "bg": Field(K_STR, pattern=_COLOR_RE, high=7),
        "scheme": Field(K_STR, choices=frozenset({"light", "dark"}), high=8),
    },
)

_DELIVERY = Field(
    K_OBJ,
    fields={
        "path": _req(_PATH),
        "status": _req(_SHORT),
        "inside": _PATH,
        "linked": _BOOL,
    },
)

_SEEN = {
    "ids": Field(K_LIST, high=SEEN_MAX, item=_ID),
    "session": _ID,
}

_STATE_FIELDS = {
    "key": Field(K_STR, required=True, low=1, high=KEY_MAX, pattern=_KEY_RE),
    "entry": _s(PATH_MAX, low=1),
}

# ---- the message table -------------------------------------------------------


@dataclass(frozen=True)
class Shape:
    """One form of a type: who sends it, its fields, and for a request the
    fields of its ok reply. `one_of`: at least one of these fields must be
    present."""

    sender: str
    fields: Mapping[str, Field] = field(default_factory=dict)
    one_of: tuple[str, ...] = ()
    reply: Mapping[str, Field] | None = None


@dataclass(frozen=True)
class MessageType:
    """A message type: as a request (always client -> service), as an event
    (either way), or both (`state.set`, `seen`)."""

    name: str
    summary: str
    request: Shape | None = None
    event: Shape | None = None


def _request(fields=None, reply=None, one_of=()) -> Shape:
    return Shape(CLIENT, dict(fields or {}), tuple(one_of), dict(reply or {}))


def _event(sender: str, fields=None, one_of=()) -> Shape:
    return Shape(sender, dict(fields or {}), tuple(one_of))


_TABLE: tuple[MessageType, ...] = (
    # -- connecting (§3.2)
    MessageType(
        "hello",
        "The client introduces itself; the service answers with its own facts.",
        request=_request(
            {
                "protocol": _req(_i(0, 65535)),
                "min_protocol": _i(0, 65535),
                "version": _req(_s(VERSION_MAX, low=1)),
                "client_id": _req(_ID),
                "device": _HOST,
                "locale": Field(K_STR, high=64, pattern=_LOCALE_RE),
                "term": _TERM,
            },
            reply={
                "protocol": _req(_i(0, 65535)),
                "min_protocol": _req(_i(0, 65535)),
                "version": _req(_s(VERSION_MAX, low=1)),
                "service_id": _req(_ID),
                "host": _HOST,
                "caps": Field(K_LIST, high=CAPS_MAX, item=Field(K_STR, high=64, pattern=_CAP_RE)),
                "local_proof": Field(
                    K_OBJ,
                    fields={
                        "path": _req(_PATH),
                        "length": _req(_i(1, LOCAL_PROOF_MAX)),
                    },
                ),
            },
        ),
    ),
    MessageType(
        "local",
        "The client proves it shares the service's filesystem and uid.",
        request=_request({"proof": _req(Field(K_STR, high=2 * LOCAL_PROOF_MAX, pattern=_HEX_RE))}),
    ),
    MessageType(
        "subscribe",
        "Send the snapshot as events, then answer, then keep sending events.",
        request=_request(reply={"items": _COUNT, "ptys": _COUNT}),
    ),
    # -- terminals (§3.3)
    MessageType(
        "attach",
        "Show a pty: a redraw, then live output.",
        request=_request(
            {"pty": _req(_PTY), "cols": _req(_COLS), "rows": _req(_ROWS)},
            reply={
                "cols": _req(_COLS),
                "rows": _req(_ROWS),
                "active": _req(_BOOL),
                "sized_for": _HOST,
            },
        ),
    ),
    MessageType(
        "detach",
        "Stop showing a pty; it keeps running.",
        request=_request({"pty": _req(_PTY)}),
    ),
    MessageType(
        "resize",
        "The client's grid for a pty changed.",
        event=_event(CLIENT, {"pty": _req(_PTY), "cols": _req(_COLS), "rows": _req(_ROWS)}),
    ),
    MessageType(
        "focus",
        "The client's view of a pty gained or lost focus.",
        event=_event(CLIENT, {"pty": _req(_PTY), "focused": _req(_BOOL)}),
    ),
    MessageType(
        "theme",
        "The client's terminal colours or scheme changed.",
        event=_event(CLIENT, {"term": _req(_TERM)}),
    ),
    MessageType(
        "spawn",
        "Start a session or a panel shell in a new pty.",
        request=_request(
            {
                "kind": Field(K_STR, choices=frozenset({"agent", "shell"}), high=8),
                "cwd": _req(_PATH),
                "session": _ID,
                "prompt": _TEXT,
                "model": _s(MODEL_MAX),
                "effort": _SHORT,
                "permission_mode": _SHORT,
                "add_dirs": Field(K_LIST, high=ADD_DIRS_MAX, item=_PATH),
                "worktree": _BOOL,
                "worktree_name": _s(ID_MAX),
                "sandbox": _BOOL,
                "sandbox_box": _ID,
                "cols": _COLS,
                "rows": _ROWS,
            },
            reply={"pty": _req(_PTY), "cols": _req(_COLS), "rows": _req(_ROWS)},
        ),
    ),
    MessageType(
        "prompt",
        "Type text into the agent's box and submit it.",
        request=_request({"pty": _req(_PTY), "text": _req(_s(TEXT_MAX, low=1))}),
    ),
    MessageType(
        "switch",
        "Post a model and/or effort switch to the agent.",
        request=_request(
            {"pty": _req(_PTY), "model": _s(MODEL_MAX, low=1), "effort": _s(SHORT_MAX, low=1)},
            one_of=("model", "effort"),
        ),
    ),
    MessageType(
        "mention",
        "Type a file's mention token into the agent's box.",
        request=_request(
            {
                "pty": _req(_PTY),
                "path": _req(_PATH),
                "start_line": _i(0, LINE_MAX),
                "end_line": _i(0, LINE_MAX),
            }
        ),
    ),
    MessageType(
        "cut",
        "Lift the text out of the agent's box (the composer's open-cut).",
        request=_request({"pty": _req(_PTY)}, reply={"text": _req(_TEXT)}),
    ),
    MessageType(
        "clear",
        "Erase the agent's box.",
        request=_request({"pty": _req(_PTY)}),
    ),
    MessageType(
        "paint",
        "Insert text into a pty's output stream (rule 2).",
        request=_request({"pty": _req(_PTY), "text": _req(_s(TEXT_MAX, low=1))}),
    ),
    MessageType(
        "close",
        "End a pty's session: the graceful exit, the /bg handoff, or force.",
        request=_request(
            {
                "pty": _req(_PTY),
                "mode": Field(K_STR, choices=frozenset({"exit", "background", "kill"}), high=16),
            }
        ),
    ),
    # -- state (§3.8)
    MessageType(
        "state.set",
        "Write a shared state key (request), or a key changed (event).",
        request=_request({**_STATE_FIELDS, "value": _req(_null(_JSON))}),
        event=_event(SERVICE, {**_STATE_FIELDS, "value": _req(_null(_JSON))}),
    ),
    MessageType(
        "state.get",
        "Read a shared state key.",
        request=_request(_STATE_FIELDS, reply={"value": _req(_null(_JSON))}),
    ),
    # -- the store (§3.15)
    MessageType(
        "item",
        "A session row appeared, changed or went away.",
        event=_event(
            SERVICE,
            {
                "session": _req(_ID),
                "removed": _BOOL,
                "project": _s(PATH_MAX),
                "cwd": _null(_PATH),
                "display_name": _NAME,
                "cli_title": _NAME,
                "subtitle": _s(256),
                "preview": _s(PREVIEW_MAX),
                "provider": _SHORT,
                "favorite": _BOOL,
                "status": _SHORT,
                "state": _SHORT,
                "busy": _BOOL,
                "unread": _BOOL,
                "syncing": _BOOL,
                "backgrounding": _BOOL,
                "can_background": _BOOL,
                "mtime": _NUM,
                "created": _NUM,
                "size": _i(0, SIZE_MAX),
            },
        ),
    ),
    MessageType(
        "pty",
        "A pty appeared or changed (a row of the pty table).",
        event=_event(
            SERVICE,
            {
                "pty": _req(_PTY),
                "kind": _req(_s(SHORT_MAX, low=1)),
                "session": _ID,
                "cwd": _PATH,
                "pid": _i(1, PID_MAX),
                "cols": _COLS,
                "rows": _ROWS,
                "sandboxed": _BOOL,
                "box": _ID,
                "active": _BOOL,
                "sized_for": _HOST,
            },
        ),
    ),
    MessageType(
        "pty-exited",
        "A pty's child exited; status is null when it is unknown (a keeper crash).",
        event=_event(
            SERVICE,
            {"pty": _req(_PTY), "status": _req(_null(_i(-(2**31), 2**31 - 1)))},
        ),
    ),
    MessageType(
        "pr",
        "A session's linked pull requests, as prstatus records.",
        event=_event(
            SERVICE,
            {
                "session": _req(_ID),
                "prs": _req(Field(K_LIST, high=PRS_MAX, item=Field(K_JSON_OBJECT))),
            },
        ),
    ),
    # -- notifications (§3.13)
    MessageType(
        "notify",
        "A notification record, its body as msgid and args.",
        event=_event(
            SERVICE,
            {
                "notification": _req(_ID),
                "kind": _req(_s(SHORT_MAX, low=1)),
                "session": _SESSION_OR_EMPTY,
                "title": _NAME,
                "project": _NAME,
                "msgid": _MSGID,
                "args": _ARGS,
                "when": _req(_NUM),
                "read": _BOOL,
                "count": _i(1, COUNT_MAX),
                "url": Field(K_URL),
            },
        ),
    ),
    MessageType(
        "seen",
        "Notifications or a session were seen: a client says so, the service tells the rest.",
        request=_request(_SEEN, one_of=("ids", "session")),
        event=_event(SERVICE, _SEEN, one_of=("ids", "session")),
    ),
    # -- session tools (§3.7)
    MessageType(
        "tool",
        "A UI-bound tool call for the session's active client.",
        event=_event(
            SERVICE,
            {
                "call": _req(_ID),
                "session": _SESSION_OR_EMPTY,
                "name": _req(_TOOL_NAME),
                "arguments": _req(Field(K_JSON_OBJECT)),
            },
        ),
    ),
    MessageType(
        "tool-reply",
        "The active client's answer to a tool call.",
        event=_event(CLIENT, {"call": _req(_ID), "ok": _req(_BOOL), "text": _req(_TEXT)}),
    ),
    # -- sandboxed sessions (§3.9)
    MessageType(
        "sandbox.plan",
        "The plan a sandboxed session's box was launched with.",
        request=_request({"pty": _req(_PTY)}, reply={"plan": _req(Field(K_JSON_OBJECT))}),
    ),
    MessageType(
        "sandbox.grants",
        "What the Sandboxed chip draws: grants, defaults, tools, staleness.",
        request=_request(
            {"pty": _req(_PTY)},
            reply={
                "grants": Field(K_LIST, high=GRANTS_MAX, item=_DELIVERY),
                "defaults": Field(K_LIST, high=GRANTS_MAX, item=_PATH),
                "tools": Field(K_MAP, high=TOOLS_MAX, key=_TOOL_RE, item=_BOOL),
                "stale": _BOOL,
                "can_restart": _BOOL,
            },
        ),
    ),
    MessageType(
        "sandbox.allow",
        "Allow a directory to a session's box, or make it a project default.",
        request=_request(
            {
                "pty": _req(_PTY),
                "path": _req(_PATH),
                "scope": Field(K_STR, choices=frozenset({"session", "project"}), high=8),
            }
        ),
    ),
    MessageType(
        "sandbox.revoke",
        "Take a directory back from a session's box, or from the project defaults.",
        request=_request(
            {
                "pty": _req(_PTY),
                "path": _req(_PATH),
                "scope": Field(K_STR, choices=frozenset({"session", "project"}), high=8),
            }
        ),
    ),
    MessageType(
        "sandbox.tools",
        "Set which session tools a sandboxed session is offered, or reset them.",
        request=_request(
            {
                "pty": _req(_PTY),
                "tools": Field(K_MAP, low=1, high=TOOLS_MAX, key=_TOOL_RE, item=_BOOL),
                "reset": _BOOL,
            },
            reply={"tools": Field(K_MAP, high=TOOLS_MAX, key=_TOOL_RE, item=_BOOL)},
            one_of=("tools", "reset"),
        ),
    ),
    MessageType(
        "sandbox.restart",
        "Exit a sandboxed session and resume it with the current plan.",
        request=_request({"pty": _req(_PTY)}),
    ),
    MessageType(
        "sandbox",
        "A sandboxed session's grants or plan changed; a delivery for the toast.",
        event=_event(
            SERVICE,
            {"pty": _req(_PTY), "session": _ID, "delivery": _DELIVERY},
        ),
    ),
    # -- the service itself (§3.10)
    MessageType(
        "service.restart",
        "Restart the service: now, or once no session is busy.",
        request=_request(
            {"when": Field(K_STR, choices=frozenset({"now", "idle"}), high=8)},
        ),
    ),
    MessageType(
        "service.status",
        "What the service is running.",
        request=_request(
            reply={
                "version": _req(_s(VERSION_MAX, low=1)),
                "protocol": _req(_i(0, 65535)),
                "ptys": _COUNT,
                "busy": _COUNT,
                "clients": _COUNT,
                "started": _NUM,
            }
        ),
    ),
)

TYPES: Mapping[str, MessageType] = {entry.name: entry for entry in _TABLE}

# Names a message's own fields can't take: the envelope's. A response is told
# apart by `re` and no `t`, so `ok`, `msgid` and `args` are a response's only
# there, and a type may carry them as fields (`tool-reply`, `notify`).
ENVELOPE = frozenset({"t", "id", "re"})


def type_names() -> tuple[str, ...]:
    """Every message type, in the table's order."""
    return tuple(TYPES)


def message_type(name: str) -> MessageType | None:
    return TYPES.get(name)


# ---- validated messages and refusals ----------------------------------------


@dataclass(frozen=True)
class Message:
    """A request or event that passed `validate`: its fields bounded and
    shaped, the ones its type doesn't list dropped. `id` is None for an
    event."""

    type: str
    kind: str  # REQUEST | EVENT
    id: int | None
    fields: dict

    def get(self, name: str, default=None):
        return self.fields.get(name, default)


@dataclass(frozen=True)
class Response:
    """A response that passed `validate_response`. On ok, `fields` holds the
    reply's fields; on a refusal, `error`, `msgid` and `args`."""

    re: int
    ok: bool
    fields: dict
    error: str = ""
    msgid: str = ""
    args: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Refusal:
    """Why a message was not accepted. `re` is the id to answer with
    `to_message()`, or None when there is nobody to answer (an event, a
    response, a message with no usable id): the receiver drops it then."""

    re: int | None
    error: str
    msgid: str
    args: dict = field(default_factory=dict)

    def to_message(self) -> dict | None:
        if self.re is None:
            return None
        return refuse(self.re, self.error, self.msgid, self.args)


class _Invalid(Exception):
    def __init__(self, msgid: str, **args) -> None:
        super().__init__(msgid)
        self.msgid = msgid
        self.args_ = args


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _check_text(name: str, value: str) -> None:
    """A string must survive being encoded: json.loads takes lone
    surrogates that UTF-8 can't carry on."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise _Invalid("{field} is not valid text", field=name) from None


def _check_string(name: str, value, spec: Field) -> str:
    if not isinstance(value, str):
        raise _Invalid("{field} must be a string", field=name)
    low = spec.low or 0
    if len(value) < low:
        if low == 1:
            raise _Invalid("{field} must not be empty", field=name)
        raise _Invalid("{field} must be at least {min} characters", field=name, min=low)
    if spec.high is not None and len(value) > spec.high:
        raise _Invalid("{field} must be at most {max} characters", field=name, max=spec.high)
    if spec.choices is not None and value not in spec.choices:
        raise _Invalid(
            "{field} must be one of: {choices}",
            field=name,
            choices=", ".join(sorted(spec.choices)),
        )
    if spec.pattern is not None and not spec.pattern.fullmatch(value):
        raise _Invalid("{field} is not well formed", field=name)
    _check_text(name, value)
    return value


def _check_json(name: str, value, depth: int, budget: list[int]):
    budget[0] -= 1
    if budget[0] < 0:
        raise _Invalid("{field} holds too much", field=name)
    if depth > JSON_MAX_DEPTH:
        raise _Invalid("{field} is nested too deeply", field=name)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if not _is_number(value):
            raise _Invalid("{field} must be a finite number", field=name)
        return value
    if isinstance(value, str):
        if len(value) > TEXT_MAX:
            raise _Invalid("{field} must be at most {max} characters", field=name, max=TEXT_MAX)
        _check_text(name, value)
        return value
    if isinstance(value, list):
        return [_check_json(name, item, depth + 1, budget) for item in value]
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise _Invalid("{field} must have text keys", field=name)
            if len(key) > PATH_MAX:
                raise _Invalid("{field} has a key that is too long", field=name)
            _check_text(name, key)
            out[key] = _check_json(name, item, depth + 1, budget)
        return out
    raise _Invalid("{field} must be a JSON value", field=name)


def _check(name: str, value, spec: Field):
    """*value* checked against *spec*, as it is kept: a closed object's
    unknown fields dropped. Raises _Invalid."""
    if value is None:
        if spec.nullable or spec.kind == K_SCALAR:
            return None
        raise _Invalid("{field} must not be null", field=name)
    kind = spec.kind
    if kind == K_STR:
        return _check_string(name, value, spec)
    if kind == K_PATH:
        if not isinstance(value, str):
            raise _Invalid("{field} must be a string", field=name)
        if len(value) > PATH_MAX:
            raise _Invalid("{field} must be at most {max} characters", field=name, max=PATH_MAX)
        if not value.startswith("/") or "\0" in value:
            raise _Invalid("{field} must be an absolute path", field=name)
        _check_text(name, value)
        return value
    if kind == K_URL:
        if not isinstance(value, str):
            raise _Invalid("{field} must be a string", field=name)
        if len(value) > PATH_MAX:
            raise _Invalid("{field} must be at most {max} characters", field=name, max=PATH_MAX)
        if value and not value.lower().startswith(("https://", "http://")):
            raise _Invalid("{field} must be an http(s) URL", field=name)
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
            raise _Invalid("{field} must be an http(s) URL", field=name)
        _check_text(name, value)
        return value
    if kind == K_INT:
        if not _is_int(value):
            raise _Invalid("{field} must be an integer", field=name)
        if (spec.low is not None and value < spec.low) or (
            spec.high is not None and value > spec.high
        ):
            raise _Invalid(
                "{field} must be between {min} and {max}", field=name, min=spec.low, max=spec.high
            )
        return value
    if kind == K_NUM:
        if not _is_number(value):
            raise _Invalid("{field} must be a finite number", field=name)
        return value
    if kind == K_BOOL:
        if not isinstance(value, bool):
            raise _Invalid("{field} must be true or false", field=name)
        return value
    if kind == K_SCALAR:
        if value is None or isinstance(value, bool) or _is_number(value):
            return value
        if isinstance(value, str):
            if len(value) > ARG_TEXT_MAX:
                raise _Invalid(
                    "{field} must be at most {max} characters", field=name, max=ARG_TEXT_MAX
                )
            _check_text(name, value)
            return value
        raise _Invalid("{field} must be text, a number, true, false or null", field=name)
    if kind == K_LIST:
        if not isinstance(value, list):
            raise _Invalid("{field} must be a list", field=name)
        if spec.high is not None and len(value) > spec.high:
            raise _Invalid("{field} must hold at most {max} items", field=name, max=spec.high)
        if spec.low is not None and len(value) < spec.low:
            raise _Invalid("{field} must hold at least {min} items", field=name, min=spec.low)
        return [_check(f"{name}[{index}]", item, spec.item) for index, item in enumerate(value)]
    if kind == K_MAP:
        if not isinstance(value, dict):
            raise _Invalid("{field} must be an object", field=name)
        if spec.high is not None and len(value) > spec.high:
            raise _Invalid("{field} must hold at most {max} items", field=name, max=spec.high)
        if spec.low is not None and len(value) < spec.low:
            raise _Invalid("{field} must hold at least {min} items", field=name, min=spec.low)
        out = {}
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > PATH_MAX:
                raise _Invalid("{field} has a key that is not well formed", field=name)
            if spec.key is not None and not spec.key.fullmatch(key):
                raise _Invalid("{field} has a key that is not well formed", field=name)
            _check_text(name, key)
            out[key] = _check(f"{name}.{key}", item, spec.item)
        return out
    if kind == K_OBJ:
        if not isinstance(value, dict):
            raise _Invalid("{field} must be an object", field=name)
        return _check_fields(value, spec.fields or {}, spec.one_of, prefix=f"{name}.")
    if kind == K_JSON:
        return _check_json(name, value, 0, [JSON_MAX_NODES])
    if kind == K_JSON_OBJECT:
        if not isinstance(value, dict):
            raise _Invalid("{field} must be an object", field=name)
        return _check_json(name, value, 0, [JSON_MAX_NODES])
    raise AssertionError(f"unknown field kind {kind!r}")  # the table's bug


def _check_fields(
    message: dict, fields: Mapping[str, Field], one_of: tuple[str, ...], prefix: str = ""
) -> dict:
    out = {}
    for name, spec in fields.items():
        if name not in message:
            if spec.required:
                raise _Invalid("{field} is missing", field=prefix + name)
            continue
        out[name] = _check(prefix + name, message[name], spec)
    if one_of and not any(name in out for name in one_of):
        raise _Invalid(
            "One of {fields} is required", fields=", ".join(prefix + name for name in one_of)
        )
    return out


def _peer_name(sender: str) -> str:
    return "client" if sender == CLIENT else "service"


def validate(message: object, sender: str) -> Message | Refusal:
    """*message*, a decoded text frame from *sender* (CLIENT or SERVICE), as
    a `Message`, or the `Refusal` to send back (or, with no id, to drop).

    A request is a frame with an `id`, an event one without; each type
    lists the forms it takes and which peer may send each. Fields the form
    does not list are dropped; responses go through `validate_response`.
    """
    if sender not in PEERS:
        raise ValueError(f"unknown sender {sender!r}")
    if not isinstance(message, dict):
        return Refusal(None, ERROR_INVALID, "A message must be a JSON object")
    request_id = None
    if "id" in message:
        request_id = message["id"]
        if not _is_int(request_id) or not 0 <= request_id <= REQUEST_ID_MAX:
            return Refusal(
                None,
                ERROR_INVALID,
                "The id must be an integer between 0 and {max}",
                {"max": REQUEST_ID_MAX},
            )
    name = message.get("t")
    if not isinstance(name, str) or not _TYPE_RE.fullmatch(name):
        if "re" in message and name is None:
            return Refusal(None, ERROR_INVALID, "A response needs the request it answers")
        return Refusal(request_id, ERROR_INVALID, "A message needs a type")
    entry = TYPES.get(name)
    if entry is None:
        return Refusal(request_id, ERROR_UNKNOWN, "Unknown message type: {type}", {"type": name})
    kind = REQUEST if request_id is not None else EVENT
    shape = entry.request if kind == REQUEST else entry.event
    if shape is None:
        if kind == EVENT:
            return Refusal(None, ERROR_INVALID, "{type} is a request and needs an id", {"type": name})
        return Refusal(
            request_id, ERROR_INVALID, "{type} is an event and takes no id", {"type": name}
        )
    if shape.sender != sender:
        return Refusal(
            request_id,
            ERROR_DIRECTION,
            "{type} can't come from the {peer}",
            {"type": name, "peer": _peer_name(sender)},
        )
    try:
        fields = _check_fields(message, shape.fields, shape.one_of)
    except _Invalid as invalid:
        return Refusal(request_id, ERROR_INVALID, invalid.msgid, invalid.args_)
    return Message(name, kind, request_id, fields)


def response_id(message: object) -> int | None:
    """The request id a response answers, or None when *message* is not
    shaped like a response: what a receiver looks its pending request up by
    before calling `validate_response`."""
    if not isinstance(message, dict) or "t" in message:
        return None
    re_id = message.get("re")
    if _is_int(re_id) and 0 <= re_id <= REQUEST_ID_MAX:
        return re_id
    return None


def validate_response(message: object, request_type: str) -> Response | Refusal:
    """*message*, a response from the service to a *request_type* request,
    as a `Response`, or a `Refusal` (never answered: the receiver fails the
    pending request instead). An ok reply is checked against the type's
    reply fields; a refusal against the envelope (`error` any code shaped
    like one, since a newer service may add codes)."""
    entry = TYPES.get(request_type)
    if entry is None or entry.request is None:
        raise ValueError(f"{request_type!r} is not a request type")
    re_id = response_id(message)
    if re_id is None:
        return Refusal(None, ERROR_INVALID, "A response needs the request it answers")
    ok = message.get("ok")
    if not isinstance(ok, bool):
        return Refusal(None, ERROR_INVALID, "{field} must be true or false", {"field": "ok"})
    try:
        if ok:
            fields = _check_fields(message, entry.request.reply or {}, ())
            return Response(re_id, True, fields)
        envelope = _check_fields(
            message,
            {"error": _req(_CODE), "msgid": _req(_MSGID), "args": _ARGS},
            (),
        )
    except _Invalid as invalid:
        return Refusal(None, ERROR_INVALID, invalid.msgid, invalid.args_)
    return Response(
        re_id,
        False,
        {},
        error=envelope["error"],
        msgid=envelope["msgid"],
        args=envelope.get("args", {}),
    )


# ---- building messages -------------------------------------------------------
#
# The frames as dicts, for `encode`. The leading parameters are positional
# only, so a type's own fields may share their names (`tool` has a `name`).


def request(name: str, request_id: int, /, **fields) -> dict:
    return {"t": name, "id": request_id, **fields}


def event(name: str, /, **fields) -> dict:
    return {"t": name, **fields}


def reply(re_id: int, /, **fields) -> dict:
    return {"re": re_id, "ok": True, **fields}


def refuse(re_id: int, error: str, msgid: str, args: Mapping | None = None) -> dict:
    return {"re": re_id, "ok": False, "error": error, "msgid": msgid, "args": dict(args or {})}


# ---- versions ----------------------------------------------------------------


def negotiate(
    own: int, own_min: int, peer: int, peer_min: int | None = None
) -> int | None:
    """The newest protocol both peers speak, or None when their windows
    don't meet. Each peer speaks every protocol from its minimum to its
    own; a peer that names no minimum speaks only its own. Both sides
    compute the same answer from the same four numbers."""
    if peer_min is None:
        peer_min = peer
    agreed = min(own, peer)
    if agreed < max(own_min, peer_min) or peer_min > peer or own_min > own:
        return None
    return agreed


# ---- framing -----------------------------------------------------------------


def _reject_constant(name: str):
    raise ValueError(f"{name} is not JSON")


def encode(message: dict) -> str:
    """One text frame: compact JSON. Raises ValueError when it would be
    over MAX_FRAME bytes, or holds something JSON can't (NaN, a lone
    surrogate): the sender's bug to surface, never the peer's to choke on."""
    text = json.dumps(message, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ValueError("message holds text UTF-8 can't carry") from error
    if size > MAX_FRAME:
        raise ValueError("message exceeds the frame limit")
    return text


def decode(frame: str | bytes) -> dict:
    """The JSON object one incoming text frame holds. Raises ValueError for
    a frame over MAX_INCOMING, malformed JSON (NaN and Infinity included),
    nesting Python can't parse, or a value that isn't an object; the
    receiver treats any of those as a broken peer."""
    if isinstance(frame, bytes):
        if len(frame) > MAX_INCOMING:
            raise ValueError("message exceeds the incoming limit")
        try:
            frame = frame.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("message is not UTF-8") from error
    elif len(frame) > MAX_INCOMING:
        raise ValueError("message exceeds the incoming limit")
    try:
        message = json.loads(frame, parse_constant=_reject_constant)
    except RecursionError as error:
        raise ValueError("message is nested too deeply") from error
    if not isinstance(message, dict):
        raise ValueError("message is not a JSON object")
    return message


@dataclass(frozen=True)
class Header:
    tag: int
    flags: int
    stream: int
    offset: int
    reserved: int = 0


def pack_header(tag: int, flags: int, stream: int, offset: int) -> bytes:
    """The 16-byte header; `reserved` is always sent as 0. Raises
    ValueError for a field out of its range."""
    for name, value, high in (
        ("tag", tag, U8_MAX),
        ("flags", flags, U8_MAX),
        ("stream", stream, U32_MAX),
        ("offset", offset, U64_MAX),
    ):
        if not _is_int(value) or not 0 <= value <= high:
            raise ValueError(f"{name} out of range: {value!r}")
    return HEADER.pack(tag, flags, 0, stream, offset)


def unpack_header(data: bytes) -> Header:
    """The header at the start of *data*. Raises ValueError when *data* is
    shorter than a header. `reserved` is reported, never refused: a newer
    peer may give it a meaning behind a cap."""
    if len(data) < HEADER_SIZE:
        raise ValueError(f"binary frame shorter than its {HEADER_SIZE}-byte header")
    tag, flags, reserved, stream, offset = HEADER.unpack_from(data)
    return Header(tag, flags, stream, offset, reserved)


def pack_frame(tag: int, flags: int, stream: int, offset: int, payload: bytes) -> bytes:
    """A whole binary frame. Raises ValueError for a payload over
    MAX_PAYLOAD: larger transfers are the sender's to chunk."""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload exceeds the frame limit")
    return pack_header(tag, flags, stream, offset) + bytes(payload)


def unpack_frame(data: bytes) -> tuple[Header, bytes]:
    """The header and payload of one incoming binary frame. Raises
    ValueError for a short frame or one over MAX_INCOMING."""
    if len(data) > MAX_INCOMING:
        raise ValueError("binary frame exceeds the incoming limit")
    header = unpack_header(data)
    return header, bytes(data[HEADER_SIZE:])


def check_frame(header: Header, sender: str) -> Refusal | None:
    """None when *sender* may send a frame with *header*'s tag, else the
    refusal to log (a binary frame is never answered)."""
    if sender not in PEERS:
        raise ValueError(f"unknown sender {sender!r}")
    senders = FRAME_SENDERS.get(header.tag)
    if senders is None:
        return Refusal(None, ERROR_UNKNOWN, "Unknown frame tag: {tag}", {"tag": header.tag})
    if sender not in senders:
        return Refusal(
            None,
            ERROR_DIRECTION,
            "Frame tag {tag} can't come from the {peer}",
            {"tag": header.tag, "peer": _peer_name(sender)},
        )
    if header.stream == 0 and header.tag in (TAG_OUTPUT, TAG_INPUT):
        return Refusal(None, ERROR_INVALID, "Frame tag {tag} needs a pty", {"tag": header.tag})
    return None
