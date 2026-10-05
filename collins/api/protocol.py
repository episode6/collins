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
  service writes), `kind` (``agent`` or ``shell``, required), `session` (the session an
  agent resumes, the session a shell belongs to), the prompt, and the
  client's grid; its reply names the new pty and its grid.
- `mention` carries `TerminalTab.add_file_to_chat`'s path and line range;
  `cut` is answered with the box's text (a paste Collins can't read is a
  `refused`); `close` carries a required mode: ``exit`` (the graceful exit),
  ``background`` (the /bg handoff) or ``kill`` (the force close). Hide is a
  client gesture and never reaches the service.
- `paint` carries the text to insert into the pty's output stream, as
  `feed_message` takes it.
- `state.set` and `state.get` name a top-level key (``settings``, ``names``,
  ...) and optionally an `entry` inside a dict-valued key (a setting's name,
  a session id); `state.set`'s `value` is any bounded JSON value, null
  removing an entry. The service decides which keys a client may write.
  `state.set` is also the service's event for a change, in the same shape.
  The keys are `state.SHARED_KEYS`: a map key changes entry by entry, the
  rest whole, and the snapshot sends a large map entry by entry too, so no
  value nears the frame cap. A client may write every key but
  ``service_id``, ``ptys`` and ``pty_next_id``, and ``settings`` only one
  service setting at a time: a device setting is ``refused``.
- `item` carries a `SessionItem`'s bindable properties and the `Session`
  facts the sidebar reads, every one optional but `session`, plus
  `removed`. `pty` is a row of the pty table (§3.8) as a client needs it.
- The store (§3.15, PR-1.10). `item` also carries `path` (the transcript)
  and `forward` (the row's forward state: "", "moved" or "syncing", which
  reads the service's disk), so a client holds the session as a
  `sessions.Session`; after the first, an `item` carries only the fields
  that changed. `rows` is the projection after each refresh (the row
  order, the groups with their counts, the empty project headers, the
  resolved project order, and the counts the sidebar's footer and the
  archived actions read) and is `SessionStore`'s `refreshed`; `put-away` is
  its `archived`. A subscription carries the sessions with rows; the ones
  kept out of sight are paged in by `store.page-archived`, or one at a time
  by `store.lookup`, and a session once sent is kept current until it is
  `removed`. The store's mutations are `store.*` requests named after
  `SessionStore`'s methods, bulk ones carrying up to `ROWS_MAX` sessions;
  `store.flags` carries what the person did at a client's screen (a row's
  `status`, `unread`), and the service sets it and sends it back as an
  `item` field; `busy`, `backgrounding` and `can_background` stay in its
  shape for an older client and are refused (the tracker and the
  background agents are the service's, PR-1.12a and PR-1.12d, whose
  verdicts arrive as `item` fields, `background` among them).
  The ``worktree.check`` job reads the worktree a session's transcript
  still records (the archive's ask) and `store.forget` lets go of what the
  service kept for a session whose transcript went; `forgotten` tells the
  clients the service did that by itself (the archive sweep).
  `trust.check` and `trust.grant` are the CLI's folder trust
  (`trust.py`), which only the service's machine can read and write.
- `pr` carries a session's PR records in `prstatus.to_record`'s shape, as
  bounded JSON objects: `prstatus.from_record` re-validates them on arrival.
  It is the service's `PrStore` seen from a client (§3.15, PR-1.11): sent
  for every session with records in the subscribe snapshot and after every
  change of a session's list, with `attached` naming the URLs that joined
  the list for the first time (the hub's ``pr-attached``). `pr-status` is
  its ``status-changed``: a PR's fetched status by URL, as the fetch
  cache's entry. Every write and every `gh` call is a request on the
  service's machine: `pr.set` (a session's list, wholesale), `pr.fetch`
  (re-read the statuses of some URLs), `pr.sweep` (the sidebar's sweep),
  `pr.detail` and `pr.threads` (the PR page's data, `prdetail`'s, as JSON
  objects), `pr.blob` (an image in the Files view: the gates checked and
  the URL of its blob GET answered, ``GET /api/blob?kind=pr``, PR-2.2: its
  reply's `file` became `url`, the one non-additive change of Phase 2 so
  far, made without a `PROTOCOL` bump because no release has shipped the
  split — no service or client of another build speaks `pr.blob`) and
  the actions:
  `pr.action` (`practions.perform`'s keys), `pr.comment`, `pr.review` and
  `pr.thread` (reply in a review thread, or resolve it). Each names the PR
  by its record, from which the service rebuilds it with
  `prstatus.from_record`; a refusal's or a failure's words come back as
  the reply's `error`, `practions`' own.
- `notify` carries a `notifycenter.Notification` record with the body as
  `msgid` and `args` (§3.13, §3.14); the record's id rides as
  `notification`, since `id` on any frame makes it a request. The
  producers send msgid and args (the client translates with `i18n._()`
  and `str.format_map`), and text an agent supplied (`notify_user`)
  crosses as its own `msgid` with no `args`, which `_()` returns
  unchanged and which is never formatted. `removed` says a row left the
  history. The service owns the history and the unread set; a client asks
  for a row with `notify.post` (the service mints its id and time,
  coalesces a bell, and answers with the id), and for the rest with
  `notify.remove`, `notify.clear`, `notify.green` (the synthetic row of a
  finished run, on or off) and `notify.rekey` (a placeholder's rows moving
  to the session that resolved). `seen` carries notification ids and/or a
  session, or `all`, from a client as a request and from the service to
  every other client as an event, so unread is one number everywhere.
- `job.start` starts a long-running operation on the service (a clone, the
  repository list a clone picks from, a worktree's trash or restore, a
  chat folder's trust, an icon's generation, a login repair) and answers
  with its id at once; `job` events then report it to the client that
  started it: `state` (``running``, then one of ``done``, ``failed``,
  ``refused`` or ``cancelled``), the progress or the failure as `msgid`
  and `args`, and the `result` (a JSON object; a ``running`` event may
  carry a partial one, such as the repositories listed so far).
  `job.cancel` ends one early. The kinds are closed (`JOB_KINDS`).
- `usage.get`, `models.get` and `models.defaults` are the token-use reads
  of §3.15: the usage panel's snapshot (or why there is none), the model
  catalog as the service's cache has it (`refresh` fetches it again), and
  the CLI's default model and effort for a directory. `icon.save` writes a
  generated project icon. They are requests a client makes from a worker
  thread, as the git transport of §3.15 will be: blocking that thread on
  the reply is the shape the code had when it called the module itself.
- `spawn`'s `history` and `ordinal` name the panel-history file a shell's
  scrollback is written to (`panelhistory`'s key, the session id or a
  new-chat draft id, and the shell's ordinal); `panel.key` re-files a
  shell's pty under a new key (the resolver bound the tab) or none (the
  shell's page closed for good, so its history goes with it). The service
  writes a shell's history from its model when its child exits, before the
  model is dropped (§3.15).
- `tool` is a UI-bound tool call the service hands the active client:
  `call` (its id), the session, the tool's name and its arguments, a JSON
  object bounded here and re-validated against `mcptools.validate_args` by
  the client, and `handle`: the service's name for the calling session,
  which a session has before its id resolves (`service.session.Session.
  handle`), and `sandboxed`, the service's own reading of whether the
  caller runs in a box, which the client's half applies the policy on.
  `tool-reply` is the client's event back, `call`, `ok` and
  `text`, `mcptools.run_tool_call`'s ``(ok, text)``.
- The `sandbox.*` requests name the session's box (`box`, required: what
  the service's records are keyed by) and its pty.
  `sandbox.grants` answers with what the chip draws, each
  grant in ``sandboxgrants.Delivery``'s shape (path, status, inside,
  linked, reason: why a grant is pending, the grant tag's tooltip),
  the grants the launched plan binds (`launched`), the project's defaults,
  the tools the box is offered with which of them exist on this machine
  (`available`) and whether the box overrides the defaults (`overridden`).
  `inside` must be absolute, so the service omits it where `Delivery`
  holds ``""`` (a pending grant) rather than sending the empty string.
  `sandbox.allow` and `sandbox.revoke` take a required `scope`,
  ``session`` (this session's box) or ``project`` (the project's defaults
  for new sessions); `sandbox.tools` sets switches (`tools`, name -> on)
  or `reset`s them. The `sandbox` event says something changed, and
  carries a grant's `delivery`, the same shape, for the toast.
- `diff.notes` is a session's notes and highlights on its diff (the
  service's `diffnotes.MarkStore`, §3.7, §3.8): sent whole whenever they
  change (and in the subscribe snapshot, every session's that has any),
  each mark as a JSON object (`diffnotes.mark_record`), keyed by the
  session's id, or by its `handle` before the id resolves. `diff.set-notes` is a client's write of them,
  in the same shape and whole: the page's own store is the mirror, every
  change (a note typed into the view, an edit, a remove, a clear, the
  prune a reload makes, the marks an agent's tool call landed while the
  page was open) is applied there first against the diff the page has
  loaded, and the result sent; the service's echo is the event.
- `service.restart` takes a required `when`, ``now`` or ``idle`` (§3.10's
  *Restart when idle*); `service.status` answers with the version, the protocol and
  the counts of ptys, busy sessions and clients.
- The session on the service (PR-1.12a, §3.19). `spawn` for an agent
  carries what `service.session.Session` is built from (`fork`,
  `command_override`, `jsonl_path`, `sandbox_plan`, `fork_resolve`, the
  new chat's `prompt`) and answers with the session's `handle`; a shell's
  `spawn` names its agent's `handle` so the service re-files its history
  when the session resolves. The `session` event is the facts a client
  reads off its tab, every field optional but `pty` and `handle`, sent
  whole on `attach` and as changed fields after (`_SESSION_FIELDS`; the
  transcript's `model`, `effort` and `permission_mode` are null while
  unknown; `landed`, `reset`, `finished` and `forked` are one-shots).
  `cut` is a request answered with the cut's `handle` and an event by that
  handle (`seeded` with the text, `refused`, `cancelled`); `cut.cancel`,
  `draft.restore`, `write`, `send` (reply `sent`), `close` with the exit
  keystrokes (`text`) or `force`, `close.nudge`, `close.end`,
  `resolver.arm`, `transcript.update` / `set` / `relocate`,
  `prs.restore`, `cwd.settle` (reply the follow scope's name),
  `shells.follow`, `finish.witness`, `baseline.absorb`,
  `baseline.cmdlines` and `restart.worktreeless` are the requests a tab
  makes of its session (`prompt` with `when_empty` is re-checked against
  the live box and refused when it isn't empty); `composer`, `shells`,
  `focus.terminal` (the session asks the active client to put the
  keyboard in the terminal; `focus` stays the client's own event) and
  `close` (`budget` or `exited`) are the events the session sends. `pty.info` and
  `pty.capture` are a panel shell's reads. `sandbox.derive`,
  `sandbox.drop` and `sandbox.forget` are the sandbox host's three calls
  the client still makes; the `sandbox` event's `box` is optional for the
  probe's verdict (`what: "probe"`, `reason`). The `term` carries the
  sixteen-colour `palette`, for the dim judgement the service makes. The
  `debug.*` family (D27) is served only with `COLLINS_DEBUG_API=1`.
- Git over the API (§3.23, PR-2.1, D33; behind the ``git`` cap). `git.run`
  names one of gitops' argv builders (its registry's Python name) and the
  builder's keyword *args* as a bounded JSON object: the wire never carries
  an argv, and the service runs what its own builder makes of the args. The
  reply is `GitResult`'s shape with git's exit *status* (null: unreachable),
  the client's runner deciding which statuses are ok. A reply the frame cap
  can't hold travels as `split_reply` says: the stdout as TAG_BLOB frames on
  the asking connection ahead of a slim reply (``stdout_chunked``,
  ``stdout_bytes``), joined back by the receiver (`join_reply`). `git.info`
  answers every `.git` read gitinfo makes at once (the root, the branch,
  the trunk, the GitHub page, HEAD, the index's mtime, the in-progress
  markers and operation, the refs digest, and the heads when the client's
  ``known_refs`` digest differs; with ``changes`` the `git status` of
  has_changes / change_summary; with ``state`` the watch's tree-state
  digest), `git.sizes` the on-disk size of repository paths, `git.watch` /
  `git.unwatch` install the page's directory monitors on the service (one
  watch per client and ``handle``, the page's own name for it) and
  `git-changed` is their event, `git.plan` carries a gitpatch plan out on
  the service, refused ``stale`` when a stable key it names is no longer in
  the fresh patch, and `fs.trash` is §3.23's trash. A handler that must
  not block the main loop answers with a `Deferred` the transport settles
  later: exactly one response per request, on the connection that asked,
  which may take longer; `tests/inproc.py` pumps the loop until it does.
  ``GET /api/blob?kind=git`` on the same socket is a blob's bytes
  (`api.server`), never a path on the wire; ``kind=pr`` is a PR file's
  (PR-2.2), at the URL `pr.blob` answers.
- Files over the API (§3.23, PR-2.3; behind the ``files`` cap). `fs.read`
  answers a file's text with the encoding it was decoded as (UTF-8, or
  latin-1 for bytes that are not UTF-8, so the save writes them back the
  same way), its mtime in microseconds and its size, or ``binary``;
  `fs.write` takes the text and ``expect_mtime``, the mtime the client's
  last read or write answered, and is refused ``stale`` with nothing
  written when the file's mtime moved (null writes regardless: the
  Overwrite the user confirmed); `fs.watch` / `fs.unwatch` name a watch by
  the client's ``handle`` (one client may watch a path twice) and
  `file-changed` is its event, the file's stat after the service's 300 ms
  debounce, ``gone`` when it is no more. A file is at most FILE_TEXT_MAX
  (5 MiB), so its text may exceed a frame: `split_request` / `join_request`
  chunk a request the way `split_reply` chunks a reply, on the first of
  CHUNKED_FIELDS the message holds (``stdout``, ``text``), the server
  joining a request's chunks before it validates the request.
- Enumerations a client sends are closed (`choices`) and, where a request
  carries one, required: no choice has an unstated default. Strings the service
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
CAP_DEBUG = "debug"  # the service runs with COLLINS_DEBUG_API=1 and serves debug.* (D27)
CAP_GIT = "git"  # git over the API: git.*, fs.trash, git-changed, GET /api/blob?kind=git (PR-2.1)
CAP_FILES = "files"  # files over the API: fs.read, fs.write, fs.watch / fs.unwatch, file-changed (PR-2.3)
CAPABILITIES = frozenset({CAP_LOCAL, CAP_DEBUG, CAP_GIT, CAP_FILES})

# The client's second connection (D26): a hello carrying ``channel: "sync"``
# opens the channel `Link.call` blocks on; the primary (the default) carries
# the subscription, the attaches and every event.
CHANNEL_PRIMARY = "primary"
CHANNEL_SYNC = "sync"
CHANNELS = frozenset({CHANNEL_PRIMARY, CHANNEL_SYNC})

# The client acks live output every ACK_BYTES fed to its VTE, or ACK_MS after
# the last unacked frame, whichever first (§3.20); the service hands a sink
# at most ACK_WINDOW unacked bytes before holding the rest in the sink's own
# pending list.
ACK_BYTES = 64 * 1024
ACK_MS = 100
ACK_WINDOW = 1024 * 1024

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
# An event form either peer may send (`focus`: the client's focus report,
# and the service asking the active client to focus a terminal, PR-1.12a).

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
# What the request was planned against has moved since (PR-2.1): a git
# plan whose stable keys the fresh patch no longer holds, later a write
# whose expected mtime moved (§3.23). The client reloads and asks again.
ERROR_STALE = "stale"
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
        ERROR_STALE,
    }
)

# The fields the transport may carry outside the text frame
# (`split_reply` / `join_reply`, `split_request` / `join_request`):
# `git.run`'s stdout, which a whole diff can take past MAX_FRAME (PR-2.1),
# and `fs.read`'s / `fs.write`'s text, a file of up to FILE_TEXT_MAX
# (PR-2.3). The field travels as TAG_BLOB frames whose `stream` is the
# request's id (masked to 32 bits, STREAM_MASK) ahead of the message,
# which then says `<field>_chunked` and `<field>_bytes`; a request's
# chunks go ahead of the request on the connection that carries it and
# the service joins them before it validates. One field per message is
# ever chunked: the first of CHUNKED_FIELDS the message holds as text.
CHUNKED_FIELD = "stdout"
CHUNKED_FIELDS = ("stdout", "text")
CHUNKED_MAX = 64 * 1024 * 1024  # the most bytes a chunked field runs to, either way
STREAM_MASK = 0xFFFF_FFFF

# A `spawn` refused because the service runs that session already (its
# args name the running `pty`): the client attaches to it instead (D31).
ALREADY_RUNNING_MSGID = "This session is already running in the Collins service"

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
# A blob URL (`/api/blob?kind=…`): a path and a ref percent-encoded (PR-2.2).
BLOB_URL_MAX = 4 * PATH_MAX
TEXT_MAX = 1024 * 1024  # characters: prompt, paint, cut, tool reply
NAME_MAX = 1024  # a title, a project's name, a display name
HOST_MAX = 255  # a hostname, a device's name
VERSION_MAX = 64
SHORT_MAX = 32  # a status, a kind, a mode, a permission mode, an effort
# The mode re-assertions an attach reply lists: what termstream's tracker
# can hold at most (every known private and ANSI mode off its default, the
# unknown-mode allowance, the kitty base and stack, modifyOtherKeys), with
# room; tests/test_termstream.py pins the worst case under it.
MODES_MAX = 512
MODE_MAX = 16  # one of them, as the preamble sends it (CSI stripped): "?1004h"
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
ROWS_MAX = 10_000  # sessions a store message names: the sidebar's rows, a bulk mutation
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
# A review thread's id (prdetail.THREAD_ID's alphabet).
_THREAD_RE = re.compile(r"[A-Za-z0-9+/=_-]{1,200}")

# The long-running operations a client starts with `job.start` (see the
# module docstring), and the states a `job` event reports.
JOB_KINDS = frozenset(
    {
        "clone",
        "clone.repos",
        "worktree.trash",
        "worktree.restore",
        "chats.trust",
        "icon",
        "login.repair",
        # A row's link to its background agent, repaired, and the worktree
        # an archive would trash, read off the transcript (PR-1.12d).
        "session.repair",
        "worktree.check",
    }
)
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_FAILED = "failed"
JOB_REFUSED = "refused"
JOB_CANCELLED = "cancelled"
JOB_STATES = frozenset({JOB_RUNNING, JOB_DONE, JOB_FAILED, JOB_REFUSED, JOB_CANCELLED})

# The notification kinds a client may post (a finished run's row is
# `notify.green`'s).
POST_KINDS = frozenset({"message", "bell", "update"})

# The most marks a session's diff holds (diffnotes.MAX_NOTES and
# MAX_HIGHLIGHTS, pinned by tests/test_protocol.py).
NOTES_MAX = 1000
HIGHLIGHTS_MAX = 5000

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

# Git over the API (§3.23, D33). A builder is named by its Python name in
# gitops' registry; its args are a bounded JSON object the service hands
# the builder as keyword arguments (a builder that refuses them is
# `invalid`). A whole diff's stdout may run to diffmodel.MAX_PATCH_CHARS;
# stderr is cut at GIT_STDERR_MAX. The ops are gitpatch.OPS, pinned by
# tests/test_protocol.py; MAX_PATH is diffmodel.MAX_PATH_CHARS.
_BUILDER = Field(K_STR, low=1, high=64, pattern=re.compile(r"[a-z][a-z0-9_]{0,63}"))
GIT_TIMEOUT_MAX = 3600.0
GIT_ENV_DEFAULT = "default"
GIT_ENV_NO_EDITOR = "no_editor"
GIT_ENV_NO_PROMPT = "no_prompt"
GIT_ENVS = frozenset({GIT_ENV_DEFAULT, GIT_ENV_NO_EDITOR, GIT_ENV_NO_PROMPT})
GIT_OUTPUT_MAX = 64 * 1024 * 1024
GIT_STDERR_MAX = 64 * 1024
REFS_MAX = 4096
MAX_PATH = 512
REPO_PATHS_MAX = 4000
_SHA = Field(K_STR, high=40, pattern=re.compile(r"[0-9a-f]{40}"))
_REFS_MAP = Field(K_MAP, high=REFS_MAX, item=_SHA)
_REPO_PATHS = Field(K_LIST, high=REPO_PATHS_MAX, item=_s(MAX_PATH, low=1))
PLAN_OPS = frozenset(
    {"add", "reset", "apply-cached", "apply-cached-reverse", "apply-worktree-reverse", "checkout", "trash"}
)
# Files over the API (§3.23, PR-2.3). A file the editor opens is at most
# FILE_TEXT_MAX bytes (editorfiles' open cap); its text crosses chunked
# (CHUNKED_FIELDS) when it does not fit a frame. An mtime is microseconds
# since the epoch, as git.info's index_mtime is. A watch is named by the
# client's handle, so one client may watch the same path twice (two
# editors on it). The encodings are the two a read answers: UTF-8, or
# latin-1 for bytes that are not UTF-8 (flagged, so the save writes them
# back the same way).
FILE_TEXT_MAX = 5 * 1024 * 1024
FILE_ENCODING_UTF8 = "utf-8"
FILE_ENCODING_LATIN1 = "latin-1"
FILE_ENCODINGS = frozenset({FILE_ENCODING_UTF8, FILE_ENCODING_LATIN1})
WATCH_FILE = "file"
WATCH_DIR = "dir"
WATCH_KINDS = frozenset({WATCH_FILE, WATCH_DIR})
_HANDLE = Field(K_STR, low=1, high=64, pattern=_ID_RE)
_FILE_TEXT = _s(FILE_TEXT_MAX)
_MTIME = _i(0, SIZE_MAX)
_ENCODING = Field(K_STR, choices=FILE_ENCODINGS, high=SHORT_MAX)

_TERM = Field(
    K_OBJ,
    fields={
        "vte": _i(0, VTE_VERSION_MAX),
        "fg": Field(K_STR, pattern=_COLOR_RE, high=7),
        "bg": Field(K_STR, pattern=_COLOR_RE, high=7),
        "scheme": Field(K_STR, choices=frozenset({"light", "dark"}), high=8),
        # The sixteen-colour palette the client draws in (PR-1.12a): what
        # the screen model's dim-tail read judges a faint run against.
        "palette": Field(K_LIST, low=16, high=16, item=Field(K_STR, pattern=_COLOR_RE, high=7)),
    },
)

_DELIVERY = Field(
    K_OBJ,
    fields={
        "path": _req(_PATH),
        "status": _req(_SHORT),
        "inside": _PATH,  # omitted, never "", while the grant is pending
        "linked": _BOOL,
        "reason": _NAME,  # why the grant is pending; "" or absent otherwise
    },
)

_SEEN = {
    "ids": Field(K_LIST, high=SEEN_MAX, item=_ID),
    "session": _ID,
    "all": _BOOL,
}

# A PR as a request names it: its record (prstatus.to_record's shape).
_PR_RECORD = Field(K_JSON_OBJECT)
_PR_RECORDS = Field(K_LIST, high=PRS_MAX, item=Field(K_JSON_OBJECT))
# What a gh call that may fail answers: its words, "" or absent on success.
_PR_ERROR = {"error": _s(ARG_TEXT_MAX)}
# One session of the sidebar's sweep: its PRs and the directory a PR is
# discovered from.
_SWEEP_TARGET = Field(
    K_OBJ,
    fields={"session": _req(_ID), "prs": _PR_RECORDS, "cwd": _PATH},
)
# A session's marks on its diff, as `diff.notes` and `diff.set-notes` carry
# them (diffnotes.mark_record).
_MARKS = {
    # Whose: the session's id once it resolved (what the marks are kept
    # under), its handle before.
    "handle": _ID,
    "session": _ID,
    "notes": _req(Field(K_LIST, high=NOTES_MAX, item=Field(K_JSON_OBJECT))),
    "highlights": _req(Field(K_LIST, high=HIGHLIGHTS_MAX, item=Field(K_JSON_OBJECT))),
}
# The box a sandbox request is about, and the session asking (its handle:
# boxes are per session, but a restart is the asking session's own).
_SANDBOX_TARGET = {"box": _req(_ID), "pty": _PTY, "handle": _ID}

_SESSIONS = Field(K_LIST, low=1, high=ROWS_MAX, item=_ID)
_PROJECT = _s(NAME_MAX, low=1)  # a project's name: the group's identity in the sidebar

# A sidebar group, as the `rows` event lists them: its kind (``fav``,
# ``chats`` or ``proj``, a string a later service may extend), the
# project's name for ``proj``, the label it is shown under, and how many of
# the rows (in order) are its.
_GROUP = Field(
    K_OBJ,
    fields={
        "kind": _req(_s(SHORT_MAX, low=1)),
        "name": _NAME,
        "label": _NAME,
        "count": _req(_i(1, ROWS_MAX)),
    },
)
# A project with no rows under its header (all archived or favorited, or a
# kept folder): the same identity, and its directory when one is known.
_EMPTY_GROUP = Field(
    K_OBJ,
    fields={
        "kind": _req(_s(SHORT_MAX, low=1)),
        "name": _NAME,
        "label": _NAME,
        "cwd": _PATH,
    },
)

_DEBUG_NAME = Field(K_STR, low=1, high=256, pattern=re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,255}"))
_DEBUG_ARGS = Field(K_LIST, high=16, item=_null(_JSON))
_DEBUG_KWARGS = Field(K_JSON_OBJECT)

# The `session` event (§3.19): the facts a client reads synchronously off
# its tab's session, sent whole on attach and as single changed fields after.
# Every field but `pty` and `handle` is optional; the one-shots (`landed`,
# `reset`, `finished`, `forked`) are sent true once and never false.
# The box's text is a preview (§3.19): what the composer's cut seeds is the
# `cut` event's, not this.
_ENTERED = Field(K_OBJ, fields={"text": _req(_s(PREVIEW_MAX)), "rows_below": _req(_i(0, MAX_ROWS))})
_SESSION_FIELDS = {
    "pty": _req(_PTY),
    "handle": _req(_ID),
    # identity and the launch
    "session": _null(_ID),
    "fork": _BOOL,
    "provider": _SHORT,
    "options": _null(Field(K_JSON_OBJECT)),
    "command_override": _null(_s(PATH_MAX)),
    "cwd": _null(_PATH),
    "agent_cwd": _null(_PATH),
    "pid": _null(_i(1, PID_MAX)),
    "resolver_cwd": _null(_PATH),
    "initial_command": _null(_s(PATH_MAX)),
    "worktree_launch": _BOOL,
    "new_chat_prompt": _null(_TEXT),
    "shells_follow_armed": _BOOL,
    "closing": _BOOL,
    # the sandbox
    "sandboxed": _BOOL,
    "sandbox_box": _s(ID_MAX),
    "sandbox_plan_path": _null(_PATH),
    "defaults_owed": _BOOL,
    "can_restart": _BOOL,
    # the box reads and the process questions
    "takes_prompt": _BOOL,
    "entered": _null(_ENTERED),
    "prompt_block": _s(MSGID_MAX),
    "unstarted": _BOOL,
    "foreign_paste": _BOOL,
    "agent_running": _BOOL,
    "running_command": _BOOL,
    "pasted_back": Field(K_MAP, high=256, item=_TEXT),
    "paste_back_pending": _null(Field(K_LIST, high=256, item=_TEXT)),
    # the transcript tail
    "model": _null(_s(MODEL_MAX)),
    "effort": _null(_SHORT),
    "permission_mode": _null(_SHORT),
    "transcript_path": _null(_PATH),
    "prs": _PR_RECORDS,
    "lookup_empty": _BOOL,
    "touched_files": Field(K_LIST, high=64, item=_PATH),
    "attachments": Field(K_LIST, high=1000, item=Field(K_JSON_OBJECT)),
    "ledger_armed": _BOOL,
    "landed": _BOOL,
    "reset": _BOOL,
    # activity, keyed by the session's handle (the placeholder row's)
    "busy": _BOOL,
    "finished": _BOOL,
    # a sandboxed fork's resolver found the forked conversation's id
    "forked": _ID,
}

SESSION_FIELDS = _SESSION_FIELDS  # the `session` event's fields, for the service's fitter

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
                "channel": Field(K_STR, choices=CHANNELS, high=8),
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
                # The mode tracker's re-assertions, as the redraw's preamble
                # sends them: "?1004h" and the like, so a client knows what
                # the redraw turned on without reading its frames.
                "modes": Field(K_LIST, high=MODES_MAX, item=_s(MODE_MAX, low=1)),
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
        "A client's view of a pty gained or lost focus.",
        event=_event(CLIENT, {"pty": _req(_PTY), "focused": _req(_BOOL)}),
    ),
    MessageType(
        "focus.terminal",
        "The session asks its active client to put the keyboard in the terminal.",
        event=_event(SERVICE, {"pty": _req(_PTY)}),
    ),
    MessageType(
        "theme",
        "The client's terminal colours or scheme changed.",
        event=_event(CLIENT, {"term": _req(_TERM)}),
    ),
    MessageType(
        "ack",
        "The client has fed this much of a pty's live output to its terminal (flow control).",
        event=_event(CLIENT, {"pty": _req(_PTY), "offset": _req(_i(0, U64_MAX))}),
    ),
    MessageType(
        "spawn",
        "Start a session or a panel shell in a new pty.",
        request=_request(
            {
                "kind": _req(Field(K_STR, choices=frozenset({"agent", "shell"}), high=8)),
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
                # The rest of what an agent's `Session` is built from
                # (PR-1.12a, §3.19): the session forked, a command override
                # (a --continue, a check's stand-in), the transcript to tail,
                # a sibling's derived plan, a sandboxed fork's resolver mode.
                "fork": _BOOL,
                "command_override": _s(PATH_MAX),
                "jsonl_path": _PATH,
                "sandbox_plan": _PATH,
                "fork_resolve": _BOOL,
                # A shell's: the agent session (its handle) it is a panel of.
                "handle": _ID,
                "cols": _COLS,
                "rows": _ROWS,
                # A shell's panel history: the key its file is under and
                # its ordinal (panelhistory's).
                "history": _ID,
                "ordinal": _i(0, 65535),
            },
            # `handle` is the service's name for an agent's session (`s-N`).
            reply={"pty": _req(_PTY), "cols": _req(_COLS), "rows": _req(_ROWS), "handle": _ID},
        ),
    ),
    MessageType(
        "panel.key",
        "File a shell's panel history under a new key, or under none; name the session it belongs to.",
        # `handle`: the session the shell is a panel of, for one opened
        # before that session was spawned (a new-chat screen's shell), so
        # its history is re-filed when the session resolves.
        request=_request({"pty": _req(_PTY), "history": _req(_null(_ID)), "handle": _ID}),
    ),
    MessageType(
        "panel.history",
        "Write a session's panel history: each shell's scrollback, from its pty's model or as text.",
        request=_request(
            {
                "key": _req(_ID),
                "shells": _req(
                    Field(
                        K_LIST,
                        high=64,
                        item=Field(
                            K_OBJ,
                            # `keep`: the ordinal is named (kept) and nothing is
                            # written for it, so a text too large for this frame
                            # can follow in a `partial` request of its own.
                            fields={
                                "ordinal": _req(_i(0, 10_000)),
                                "pty": _PTY,
                                "text": _TEXT,
                                "keep": _BOOL,
                            },
                        ),
                    )
                ),
                # `partial`: write only the shells named; an ordinal absent is
                # left alone (the keep-set was sent whole in an earlier request).
                "partial": _BOOL,
            }
        ),
    ),
    MessageType(
        "pty.info",
        "A pty's process facts: its child, its shell, the foreground, the shell's cwd, its plan.",
        request=_request(
            {"pty": _req(_PTY)},
            reply={
                "kind": _req(_s(SHORT_MAX, low=1)),
                "child_pid": _null(_i(1, PID_MAX)),
                "shell_pid": _null(_i(1, PID_MAX)),
                "foreground_pgrp": _null(_i(-1, PID_MAX)),
                "running_command": _req(_BOOL),
                "process_cwd": _null(_PATH),
                "plan": _null(_PATH),
                # Its grid now: what a tab that attaches to a running pty
                # starts its terminal at, so the attach paints the screen
                # as it stands and no row is cut short (PR-1.12c, D19).
                "cols": _COLS,
                "rows": _ROWS,
            },
        ),
    ),
    MessageType(
        "pty.capture",
        "A pty's text, scrollback and screen, from the service's model.",
        request=_request({"pty": _req(_PTY)}, reply={"text": _req(_TEXT)}),
    ),
    MessageType(
        "prompt",
        "Type text into the agent's box and submit it.",
        request=_request(
            {
                "pty": _req(_PTY),
                "text": _req(_s(TEXT_MAX, low=1)),
                # Whether to put the keyboard in the terminal too (default
                # True: `inject_prompt`; False is the unfocused, bracketed
                # paste of a background spawn's prompt).
                "focus": _BOOL,
                # Only into an empty box: the service re-reads `takes_prompt`
                # off the live screen and refuses (PROMPT_BLOCK_MSGID) when
                # it isn't, rather than trusting the client's mirror.
                "when_empty": _BOOL,
            }
        ),
    ),
    MessageType(
        "switch",
        "Post a model and/or effort switch to the agent.",
        request=_request(
            {
                "pty": _req(_PTY),
                "model": _s(MODEL_MAX, low=1),
                "effort": _s(SHORT_MAX, low=1),
                # The client fact the switch's road depends on (§3.19).
                "composer_open": _BOOL,
            },
            one_of=("model", "effort"),
        ),
    ),
    MessageType(
        "write",
        "Type raw keystrokes into the agent's pty.",
        request=_request(
            {
                "pty": _req(_PTY),
                "text": _req(_s(TEXT_MAX, low=1)),
                # The text is a mention token (a drop's): the service puts
                # the space in front of it that a half-written sentence in
                # the box wants, as `mention` does (dropimages.leading_space).
                "mention": _BOOL,
            }
        ),
    ),
    MessageType(
        "send",
        "The composer's send: submit its draft, now or once a cut has settled.",
        request=_request(
            {"pty": _req(_PTY), "text": _req(_s(TEXT_MAX, low=1)), "composer_open": _BOOL},
            # Whether it went now (the composer empties) or waits on a cut
            # still settling (a `composer resend` event follows).
            reply={"sent": _req(_BOOL)},
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
        "Begin the composer's open-cut; `cut` events say what it found.",
        request=_request({"pty": _req(_PTY)}, reply={"handle": _req(_ID)}),
        event=_event(
            SERVICE,
            {
                "pty": _req(_PTY),
                "handle": _req(_ID),
                "state": _req(
                    Field(K_STR, choices=frozenset({"seeded", "refused", "cancelled"}), high=16)
                ),
                "text": _TEXT,
            },
        ),
    ),
    MessageType(
        "cut.cancel",
        "Call a cut off: the composer closed, or the box holds its text again.",
        request=_request({"pty": _req(_PTY), "handle": _ID}),
    ),
    MessageType(
        "draft.restore",
        "A closing composer's draft goes back into the agent's box, in pieces.",
        request=_request(
            {"pty": _req(_PTY), "text": _req(_s(TEXT_MAX, low=1))},
            reply={"restored": _req(_BOOL)},
        ),
    ),
    MessageType(
        "clear",
        "Erase the agent's box; wipe a shell's screen and scrollback.",
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
                "mode": _req(Field(K_STR, choices=frozenset({"exit", "background", "kill"}), high=16)),
                # The keystrokes of an exit or a handoff, as the provider
                # spells them; `force` is the window's answer to a budget
                # event (the same as mode ``kill``).
                "text": _s(256),
                "force": _BOOL,
            }
        ),
        # The close poll's word: a budget ran out (the window answers with
        # a forced close or a dialog), or the shell exited.
        event=_event(
            SERVICE,
            {
                "pty": _req(_PTY),
                "state": _req(Field(K_STR, choices=frozenset({"budget", "exited"}), high=16)),
                "phase": _SHORT,
            },
        ),
    ),
    MessageType(
        "close.nudge",
        "Feed the exit keystroke again to a CLI parked on a screen.",
        request=_request({"pty": _req(_PTY)}),
    ),
    MessageType(
        "close.end",
        "The page is gone: the close poll stops.",
        request=_request({"pty": _req(_PTY)}),
    ),
    # -- the session on the service (§3.19, PR-1.12a)
    MessageType(
        "session",
        "An agent session's facts, whole on attach and as changed fields after.",
        event=_event(SERVICE, _SESSION_FIELDS),
    ),
    MessageType(
        "composer",
        "The session asks the active client's composer for something.",
        event=_event(
            SERVICE,
            {
                "pty": _req(_PTY),
                "what": _req(Field(K_STR, choices=frozenset({"refocus", "resend", "stash"}), high=16)),
                "text": _TEXT,
            },
        ),
    ),
    MessageType(
        "shells",
        "The session says something about the panel shells beside it.",
        event=_event(
            SERVICE,
            {"pty": _req(_PTY), "what": _req(Field(K_STR, choices=frozenset({"stale"}), high=16))},
        ),
    ),
    MessageType(
        "resolver.arm",
        "A client mapped the tab: the transcript resolver's background budget starts over.",
        request=_request({"pty": _req(_PTY)}),
    ),
    MessageType(
        "transcript.update",
        "Re-read the transcript now; with `discover`, ask the branch which PR it has.",
        request=_request({"pty": _req(_PTY), "discover": _BOOL}),
    ),
    MessageType(
        "transcript.set",
        "Tail another transcript (or none).",
        request=_request({"pty": _req(_PTY), "path": _req(_null(_PATH))}),
    ),
    MessageType(
        "transcript.relocate",
        "Follow the session's transcript to where the CLI moved it.",
        request=_request({"pty": _req(_PTY), "path": _req(_PATH)}),
    ),
    MessageType(
        "prs.restore",
        "Re-adopt the pull requests saved for the session.",
        request=_request({"pty": _req(_PTY), "records": _req(_PR_RECORDS)}),
    ),
    MessageType(
        "cwd.settle",
        "Whether the agent has moved, as the editor rooted at `root` should see it.",
        request=_request(
            {"pty": _req(_PTY), "cwd": _null(_PATH), "root": _req(_PATH)},
            reply={"scope": _SHORT},
        ),
    ),
    MessageType(
        "shells.follow",
        "Whether the shells beside a new chat are owed the offer to follow its worktree.",
        request=_request({"pty": _req(_PTY), "armed": _req(_BOOL)}),
    ),
    MessageType(
        "finish.witness",
        "What the transcript says right now, for the finish ledger.",
        request=_request(
            {"pty": _req(_PTY)},
            reply={
                "stamp": _req(Field(K_LIST, low=2, high=2, item=_i(0, COUNT_MAX))),
                "size": _req(_null(_i(0, SIZE_MAX))),
            },
        ),
    ),
    MessageType(
        "baseline.absorb",
        "Fold what runs under a pristine fresh spawn into its plumbing baseline.",
        request=_request({"pty": _req(_PTY)}, reply={"capturing": _req(_BOOL)}),
    ),
    MessageType(
        "baseline.cmdlines",
        "The cmdlines running directly below the session's agent right now.",
        request=_request(
            {"pty": _req(_PTY)},
            reply={"cmdlines": _req(Field(K_LIST, high=1000, item=_s(ARG_TEXT_MAX)))},
        ),
    ),
    MessageType(
        "restart.worktreeless",
        "Type the new-session command again with the worktree dropped.",
        request=_request({"pty": _req(_PTY)}),
    ),
    # -- the e2e probe (D27): served only with COLLINS_DEBUG_API=1
    MessageType(
        "debug.session.get",
        "An attribute of a session, JSON-encoded (a method answers `callable`).",
        request=_request(
            {"pty": _req(_PTY), "name": _req(_DEBUG_NAME)},
            reply={"value": _null(_JSON), "callable": _BOOL},
        ),
    ),
    MessageType(
        "debug.session.set",
        "Set an attribute of a session.",
        request=_request({"pty": _req(_PTY), "name": _req(_DEBUG_NAME), "value": _req(_null(_JSON))}),
    ),
    MessageType(
        "debug.session.call",
        "Call a method of a session with JSON arguments; its result JSON-encoded.",
        request=_request(
            {"pty": _req(_PTY), "name": _req(_DEBUG_NAME), "args": _DEBUG_ARGS, "kwargs": _DEBUG_KWARGS},
            reply={"value": _null(_JSON)},
        ),
    ),
    MessageType(
        "debug.screen",
        "A pty's screen model, as the checks read it.",
        request=_request(
            {"pty": _req(_PTY)},
            reply={
                "rows": _req(Field(K_LIST, high=MAX_ROWS, item=_s(MAX_COLS * 4))),
                "cursor": _req(Field(K_LIST, low=2, high=2, item=_i(0, MAX_COLS))),
                "columns": _req(_COLS),
                "row_count": _req(_ROWS),
                "capture": _req(_TEXT),
            },
        ),
    ),
    MessageType(
        "debug.pty",
        "A pty's process facts, as the checks read them.",
        request=_request(
            {"pty": _req(_PTY)},
            reply={
                "child_pid": _req(_null(_i(1, PID_MAX))),
                "foreground_pgrp": _req(_null(_i(-1, PID_MAX))),
                "shell_pid": _req(_null(_i(1, PID_MAX))),
                "process_cwd": _req(_null(_PATH)),
            },
        ),
    ),
    MessageType(
        "debug.sandbox",
        "Call a method of the service's sandbox host, its live grants, or the core.",
        request=_request(
            {
                "target": _req(Field(K_STR, choices=frozenset({"host", "grants", "core"}), high=8)),
                "name": _req(_DEBUG_NAME),
                "args": _DEBUG_ARGS,
                "kwargs": _DEBUG_KWARGS,
            },
            reply={"value": _null(_JSON)},
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
                # The row's conversation runs as a background agent (PR-1.12d,
                # §3.22): "running" when the agent list says so, "pending"
                # while a /bg waits for it to, "" otherwise.
                "background": _SHORT,
                # An agent pty on the service names this session (PR-1.12c,
                # §3.21): the row of a session nobody here shows is a
                # running row, and opening it attaches.
                "running": _BOOL,
                "mtime": _NUM,
                "created": _NUM,
                "size": _i(0, SIZE_MAX),
                "path": _PATH,
                "forward": _SHORT,
            },
        ),
    ),
    MessageType(
        "rows",
        "The sidebar's rows after a refresh: their order, groups and counts.",
        event=_event(
            SERVICE,
            {
                "rows": _req(Field(K_LIST, high=ROWS_MAX, item=_ID)),
                "groups": Field(K_LIST, high=ROWS_MAX, item=_GROUP),
                "empty": Field(K_LIST, high=ROWS_MAX, item=_EMPTY_GROUP),
                "order": Field(K_LIST, high=ROWS_MAX, item=_NAME),
                "order_changed": _BOOL,
                "show_archived": _BOOL,
                "total": _COUNT,
                "hidden": _COUNT,
                "size": _i(0, SIZE_MAX),
                "projects": Field(K_LIST, high=ROWS_MAX, item=_NAME),
                "chats": _COUNT,
            },
        ),
    ),
    MessageType(
        "put-away",
        "A session was archived: whatever it still asked of the user goes with it.",
        event=_event(SERVICE, {"session": _req(_ID)}),
    ),
    MessageType(
        "forgotten",
        "A session's transcript went (the archive sweep): what a client kept for it goes too.",
        event=_event(SERVICE, {"session": _req(_ID)}),
    ),
    MessageType(
        "store.lookup",
        "Send one session's item, archived or not (a session the client was never sent).",
        request=_request({"session": _req(_ID)}, reply={"found": _req(_BOOL)}),
    ),
    MessageType(
        "store.page-archived",
        "Send the sessions kept out of sight as items, and keep them current.",
        request=_request(reply={"items": _COUNT}),
    ),
    MessageType(
        "store.refresh",
        "Rescan the transcripts.",
        request=_request({"force": _BOOL}),
    ),
    MessageType(
        "store.show-archived",
        "Draw the rows kept out of sight (Show archived sessions).",
        request=_request({"show": _req(_BOOL)}),
    ),
    MessageType(
        "store.rename",
        "Name a session; an empty name clears the manual one.",
        request=_request({"session": _req(_ID), "name": _req(_NAME)}),
    ),
    MessageType(
        "store.regenerate-name",
        "Title a session again with the title model, over a manual name.",
        request=_request({"session": _req(_ID)}),
    ),
    MessageType(
        "store.favorite",
        "Add sessions to the favorites, or take them out.",
        request=_request({"sessions": _req(_SESSIONS), "favorite": _req(_BOOL)}),
    ),
    MessageType(
        "store.archive",
        "Archive sessions, or restore them.",
        request=_request({"sessions": _req(_SESSIONS), "archived": _req(_BOOL)}),
    ),
    MessageType(
        "store.archive-project",
        "Archive a whole project, or restore it.",
        request=_request({"project": _req(_PROJECT), "archived": _req(_BOOL)}),
    ),
    MessageType(
        "store.trash",
        "Move sessions' transcripts to the trash; the ones that failed, with why.",
        request=_request(
            {"sessions": _req(_SESSIONS)},
            reply={"errors": Field(K_MAP, high=ROWS_MAX, key=_ID_RE, item=_s(ARG_TEXT_MAX))},
        ),
    ),
    MessageType(
        "store.delete",
        "Delete a session's transcript for good; why it failed, when it did.",
        request=_request({"session": _req(_ID)}, reply={"error": _s(ARG_TEXT_MAX)}),
    ),
    MessageType(
        "store.forward",
        "A session continued under a new id (a /bg fork): carry its records over.",
        request=_request({"session": _req(_ID), "to": _req(_ID)}),
    ),
    MessageType(
        "store.add-project",
        "Put a folder in the sidebar before a session has run there.",
        request=_request({"cwd": _req(_PATH)}),
    ),
    MessageType(
        "store.keep-projects",
        "Keep projects in the sidebar after their sessions go.",
        request=_request({"projects": _req(Field(K_LIST, low=1, high=ROWS_MAX, item=_PROJECT))}),
    ),
    MessageType(
        "store.forget-project",
        "Drop a kept project from the sidebar.",
        request=_request({"project": _req(_PROJECT)}),
    ),
    MessageType(
        "store.move-project",
        "Move a project in the sidebar order, before another or to the end.",
        request=_request({"project": _req(_PROJECT), "before": _null(_PROJECT)}),
    ),
    MessageType(
        "store.forget",
        "A session's transcript went: its panel history, records and box go too.",
        request=_request({"session": _req(_ID)}),
    ),
    MessageType(
        "store.flags",
        "What the person did at a client's screen, for a session's row: its status, unread off.",
        request=_request(
            {
                "session": _req(_ID),
                "status": _SHORT,
                "busy": _BOOL,
                "unread": _BOOL,
                "backgrounding": _BOOL,
                "can_background": _BOOL,
            },
            one_of=("status", "busy", "unread", "backgrounding", "can_background"),
        ),
    ),
    MessageType(
        "trust.check",
        "Whether the CLI trusts a folder already, and the folder a trust is recorded on.",
        request=_request({"path": _req(_PATH)}, reply={"trusted": _req(_BOOL), "root": _PATH}),
    ),
    MessageType(
        "trust.grant",
        "Record the CLI's folder trust: on the folder's root, or on a launch directory.",
        request=_request(
            {
                "path": _req(_PATH),
                "scope": _req(Field(K_STR, choices=frozenset({"root", "launch"}), high=8)),
            },
            reply={"written": _req(_BOOL)},
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
                # A row of the pty table as the subscription carries it
                # (the snapshot, an agent's spawn, its resolve): for the
                # sidebar's running rows, never for a view of the pty
                # (PR-1.12c, §3.21).
                "table": _BOOL,
            },
        ),
    ),
    MessageType(
        "pty-exited",
        "A pty's child exited; status is null when it is unknown (a keeper crash).",
        event=_event(
            SERVICE,
            {
                "pty": _req(_PTY),
                "status": _req(_null(_i(-(2**31), 2**31 - 1))),
                # The subscription's word that an agent's row left the pty
                # table (PR-1.12c), beside the one its views are sent.
                "table": _BOOL,
            },
        ),
    ),
    MessageType(
        "pr",
        "A session's linked pull requests, as prstatus records.",
        event=_event(
            SERVICE,
            {
                "session": _req(_ID),
                "prs": _req(_PR_RECORDS),
                # The URLs that joined the session's list for the first time
                # (the hub's pr-attached), in the list's order.
                "attached": Field(K_LIST, high=PRS_MAX, item=Field(K_URL)),
            },
        ),
    ),
    MessageType(
        "pr-status",
        "A pull request's fetched status changed (the fetch cache's entry, by URL).",
        event=_event(SERVICE, {"url": _req(Field(K_URL)), "status": _req(Field(K_JSON_OBJECT))}),
    ),
    MessageType(
        "pr.set",
        "Replace a session's saved pull requests.",
        request=_request({"session": _req(_ID), "prs": _req(_PR_RECORDS)}),
    ),
    MessageType(
        "pr.fetch",
        "Fetch pull requests' statuses again (dropping what is cached, when asked).",
        request=_request(
            {
                "urls": _req(Field(K_LIST, low=1, high=PRS_MAX, item=Field(K_URL))),
                "invalidate": _BOOL,
            }
        ),
    ),
    MessageType(
        "pr.sweep",
        "The sidebar's sweep: fetch and discover the pull requests of sessions.",
        request=_request(
            {"targets": _req(Field(K_LIST, low=1, high=ROWS_MAX, item=_SWEEP_TARGET))},
            reply={"results": Field(K_MAP, high=ROWS_MAX, key=_ID_RE, item=_PR_RECORDS)},
        ),
    ),
    MessageType(
        "pr.detail",
        "A pull request's page: everything gh says about it.",
        request=_request(
            {"url": _req(Field(K_URL))},
            reply={"detail": Field(K_JSON_OBJECT)},
        ),
    ),
    MessageType(
        "pr.threads",
        "A pull request's review threads.",
        request=_request(
            {"url": _req(Field(K_URL))},
            reply={"threads": Field(K_LIST, high=1000, item=Field(K_JSON_OBJECT))},
        ),
    ),
    MessageType(
        "pr.blob",
        "Name the blob URL of one file of a repository at a commit (an image the Files view shows).",
        request=_request(
            {
                "repository": _req(_s(NAME_MAX, low=1)),
                "ref": _req(_s(256, low=1)),
                "path": _req(_s(PATH_MAX, low=1)),
            },
            # The blob GET's URL (`GET /api/blob?kind=pr&…`, PR-2.2), never
            # a path: the client's blobcache fetches it.
            reply={"url": _s(BLOB_URL_MAX), **_PR_ERROR},
        ),
    ),
    MessageType(
        "pr.action",
        "Run one of a pull request's actions through gh (practions.perform).",
        request=_request(
            {"pr": _req(_PR_RECORD), "key": _req(_s(SHORT_MAX, low=1))},
            reply=_PR_ERROR,
        ),
    ),
    MessageType(
        "pr.comment",
        "Comment on a pull request.",
        request=_request(
            {"pr": _req(_PR_RECORD), "body": _req(_s(TEXT_MAX, low=1))}, reply=_PR_ERROR
        ),
    ),
    MessageType(
        "pr.review",
        "Review a pull request: approve, or request changes.",
        request=_request(
            {"pr": _req(_PR_RECORD), "verdict": _req(_s(SHORT_MAX, low=1)), "body": _TEXT},
            reply=_PR_ERROR,
        ),
    ),
    MessageType(
        "pr.thread",
        "Reply in a pull request's review thread, or resolve it.",
        request=_request(
            {
                "pr": _req(_PR_RECORD),
                "thread": _req(Field(K_STR, low=1, high=200, pattern=_THREAD_RE)),
                "body": _s(TEXT_MAX, low=1),
                "resolved": _BOOL,
            },
            reply=_PR_ERROR,
            one_of=("body", "resolved"),
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
                "removed": _BOOL,
            },
        ),
    ),
    MessageType(
        "notify.post",
        "Add a row to the notification history (the service mints it).",
        request=_request(
            {
                "kind": _req(Field(K_STR, choices=POST_KINDS, high=SHORT_MAX)),
                "session": _SESSION_OR_EMPTY,
                "title": _NAME,
                "project": _NAME,
                "msgid": _req(_MSGID),
                "args": _ARGS,
                "read": _BOOL,
                "url": Field(K_URL),
                # The row's id, for a kind whose id is fixed (an update's).
                "key": _ID,
            },
            reply={"notification": _req(_ID)},
        ),
    ),
    MessageType(
        "notify.remove",
        "Drop rows from the notification history.",
        request=_request(
            {"ids": _req(Field(K_LIST, low=1, high=SEEN_MAX, item=_ID))},
            reply={"removed": _COUNT},
        ),
    ),
    MessageType(
        "notify.clear",
        "Drop every row of the notification history but the finished runs'.",
        request=_request(reply={"removed": _COUNT}),
    ),
    MessageType(
        "notify.green",
        "A finished run's row: raise it (unread) or take it down.",
        request=_request(
            {"session": _req(_ID), "on": _req(_BOOL), "title": _NAME, "project": _NAME},
            reply={"changed": _req(_BOOL)},
        ),
    ),
    MessageType(
        "notify.rekey",
        "File a placeholder's rows under the session it resolved to.",
        request=_request({"session": _req(_ID), "to": _req(_ID)}, reply={"moved": _COUNT}),
    ),
    MessageType(
        "seen",
        "Notifications or a session were seen: a client says so, the service tells the rest.",
        request=_request(_SEEN, one_of=("ids", "session", "all")),
        event=_event(SERVICE, _SEEN, one_of=("ids", "session", "all")),
    ),
    # -- long-running operations
    MessageType(
        "job.start",
        "Start a long-running operation; its progress and outcome arrive as job events.",
        request=_request(
            {
                "kind": _req(Field(K_STR, choices=JOB_KINDS, high=SHORT_MAX)),
                "args": Field(K_JSON_OBJECT),
            },
            reply={"job": _req(_ID)},
        ),
    ),
    MessageType(
        "job.cancel",
        "End a job early.",
        request=_request({"job": _req(_ID)}),
    ),
    MessageType(
        "job",
        "A job's progress, or how it ended.",
        event=_event(
            SERVICE,
            {
                "job": _req(_ID),
                "kind": _req(_s(SHORT_MAX, low=1)),
                "state": _req(_s(SHORT_MAX, low=1)),
                "msgid": _MSGID,
                "args": _ARGS,
                "result": Field(K_JSON_OBJECT),
            },
        ),
    ),
    # -- token use (§3.15)
    MessageType(
        "usage.get",
        "The plan's usage, as the usage panel shows it, or why there is none.",
        request=_request(
            reply={
                "snapshot": Field(K_JSON_OBJECT),
                "kind": _SHORT,
                "error": _s(ARG_TEXT_MAX),
            }
        ),
    ),
    MessageType(
        "models.get",
        "The model catalog: the service's cache, fetched again when asked.",
        request=_request(
            {"fetch": _BOOL, "refresh": _BOOL},
            reply={
                "models": Field(K_LIST, high=256, item=Field(K_JSON_OBJECT)),
                "cached": _BOOL,
                "fetched_at": _NUM,
                "failed": _BOOL,
            },
        ),
    ),
    MessageType(
        "models.defaults",
        "The CLI's default model for a directory, and its effort for a model.",
        request=_request(
            {"cwd": _PATH, "model": _s(MODEL_MAX)},
            reply={"model": _s(MODEL_MAX), "effort": _SHORT},
        ),
    ),
    MessageType(
        "icon.save",
        "Write a generated project icon into the project.",
        request=_request(
            {"cwd": _req(_PATH), "svg": _req(_s(TEXT_MAX, low=1))},
            reply={"path": _PATH},
        ),
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
                "handle": _ID,
                "name": _req(_TOOL_NAME),
                "arguments": _req(Field(K_JSON_OBJECT)),
                # The service's reading of the caller: whether it runs in a
                # box (the sandbox policy is applied on it, never the
                # caller's word).
                "sandboxed": _BOOL,
            },
        ),
    ),
    MessageType(
        "tool-reply",
        "The active client's answer to a tool call.",
        event=_event(CLIENT, {"call": _req(_ID), "ok": _req(_BOOL), "text": _req(_TEXT)}),
    ),
    MessageType(
        "diff.notes",
        "A session's notes and highlights on its diff, whole.",
        event=_event(SERVICE, _MARKS, one_of=("handle", "session")),
    ),
    MessageType(
        "diff.set-notes",
        "Write a session's notes and highlights on its diff, whole.",
        request=_request(_MARKS, one_of=("handle", "session")),
    ),
    # -- sandboxed sessions (§3.9)
    MessageType(
        "sandbox.plan",
        "The plan a sandboxed session's box was launched with.",
        request=_request(_SANDBOX_TARGET, reply={"plan": _req(Field(K_JSON_OBJECT))}),
    ),
    MessageType(
        "sandbox.grants",
        "What the Sandboxed chip draws: grants, defaults, tools, staleness.",
        request=_request(
            _SANDBOX_TARGET,
            reply={
                "grants": Field(K_LIST, high=GRANTS_MAX, item=_DELIVERY),
                "launched": Field(K_LIST, high=GRANTS_MAX, item=_PATH),
                "defaults": Field(K_LIST, high=GRANTS_MAX, item=_PATH),
                "tools": Field(K_MAP, high=TOOLS_MAX, key=_TOOL_RE, item=_BOOL),
                "available": Field(K_MAP, high=TOOLS_MAX, key=_TOOL_RE, item=_BOOL),
                "overridden": _BOOL,
                # Whether this machine runs sandboxed sessions at all (a
                # host): what the chip's allow and pins are offered on.
                "hosted": _BOOL,
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
                **_SANDBOX_TARGET,
                "path": _req(_PATH),
                "scope": _req(Field(K_STR, choices=frozenset({"session", "project"}), high=8)),
            },
            # Whether the running box is being told (a `sandbox` event
            # follows with what became of it), or the grant waits for the
            # session's restart.
            reply={"live": _BOOL},
        ),
    ),
    MessageType(
        "sandbox.revoke",
        "Take a directory back from a session's box, or from the project defaults.",
        request=_request(
            {
                **_SANDBOX_TARGET,
                "path": _req(_PATH),
                "scope": _req(Field(K_STR, choices=frozenset({"session", "project"}), high=8)),
            },
            reply={"live": _BOOL},
        ),
    ),
    MessageType(
        "sandbox.tools",
        "Set which session tools a sandboxed session is offered, or reset them.",
        request=_request(
            {
                **_SANDBOX_TARGET,
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
        request=_request(_SANDBOX_TARGET),
    ),
    MessageType(
        "sandbox.derive",
        "A sibling's plan, derived from a box's launched one for its directory.",
        request=_request(
            {**_SANDBOX_TARGET, "cwd": _req(_PATH)},
            reply={"plan": _PATH, "box": _s(ID_MAX), "reason": _s(ARG_TEXT_MAX)},
        ),
    ),
    MessageType(
        "sandbox.drop",
        "A derived plan and its box, let go of: nothing will launch from them.",
        request=_request({**_SANDBOX_TARGET, "plan": _PATH}),
    ),
    MessageType(
        "sandbox.forget",
        "A session's transcript went: its box, and what it was allowed, go too.",
        request=_request(_SANDBOX_TARGET),
    ),
    MessageType(
        "sandbox",
        "A sandboxed session's grants or plan changed; a delivery for the toast; the probe's verdict.",
        event=_event(
            SERVICE,
            {
                # Absent for the probe's verdict (`what`: "probe"), which is
                # about this machine, not a box.
                "box": _ID,
                "what": _SHORT,
                "reason": _s(ARG_TEXT_MAX),
                "pty": _PTY,
                "session": _ID,
                # The grant the event is about, what became of it in the
                # running box, and whether it was taken back.
                "path": _PATH,
                "delivery": _DELIVERY,
                "revoked": _BOOL,
            },
        ),
    ),
    # -- git over the API (§3.23, PR-2.1, D33)
    MessageType(
        "git.run",
        "Run one of gitops' argv builders, by name and keyword args, on the service's git.",
        request=_request(
            {
                "cwd": _req(_PATH),
                "builder": _req(_BUILDER),
                "args": Field(K_JSON_OBJECT),
                # The bytes on git's stdin, as text (check-ignore's names);
                # a plan's patch travels in `git.plan`.
                "stdin": _TEXT,
                "timeout": Field(K_NUM, low=0.1, high=GIT_TIMEOUT_MAX),
                # Which environment the run gets: the service's own, one
                # with no editor (the continue runners'), or one that
                # also answers no prompt (the sidebar's pull).
                "env": Field(K_STR, choices=GIT_ENVS, high=SHORT_MAX),
            },
            reply={
                # git's exit status; null when git couldn't be run at all
                # (`unreachable`, with the reason as `stderr`). The client's
                # runner decides which statuses are ok.
                "status": _req(_null(_i(-1, COUNT_MAX))),
                "stdout": _s(GIT_OUTPUT_MAX),
                "stderr": _s(GIT_STDERR_MAX),
                "unreachable": _BOOL,
                # Set by the transport when stdout went ahead as TAG_BLOB
                # frames (CHUNKED_FIELD): the client joins them back.
                "stdout_chunked": _BOOL,
                "stdout_bytes": _i(0, SIZE_MAX),
            },
        ),
    ),
    MessageType(
        "git.info",
        "Everything gitinfo reads off a repository's .git, at once (the client's per-cwd mirror).",
        request=_request(
            {
                "cwd": _req(_PATH),
                # With `changes`, the service runs `git status` too (the
                # on-demand has_changes / change_summary; never on a tick).
                "changes": _BOOL,
                # With `state`, gitops.tree_state_signature's digest too (the
                # watch's seed, sampled by the page's read worker).
                "state": _BOOL,
                # The refs digest the client holds: the heads are sent
                # only when the service's differs.
                "known_refs": _s(64),
            },
            reply={
                # Absent fields mean "not a repository" (root null).
                "root": _req(_null(_PATH)),
                "git_dir": _null(_PATH),
                "branch": _null(_NAME),
                "default_branch": _null(_NAME),
                "github_url": _null(Field(K_URL, high=2048)),
                "index_mtime": _null(_i(0, SIZE_MAX)),
                "head": _null(_s(64)),
                "markers": Field(K_LIST, high=8, item=_s(32)),
                "operation": _null(_s(32)),
                "refs": _null(_s(64)),
                "remotes": Field(K_LIST, high=REFS_MAX, item=_NAME),
                "heads": _REFS_MAP,
                "remote_heads": _REFS_MAP,
                "changes": _null(Field(K_OBJ, fields={"staged": _req(_BOOL), "unstaged": _req(_BOOL)})),
                "state": _null(_s(64)),
            },
        ),
    ),
    MessageType(
        "git.sizes",
        "The size on disk of repository paths (the read's too-large gate for untracked and unmerged files).",
        request=_request(
            {"cwd": _req(_PATH), "paths": _req(_REPO_PATHS)},
            reply={"sizes": _req(Field(K_MAP, high=REPO_PATHS_MAX, item=_null(_i(0, SIZE_MAX))))},
        ),
    ),
    MessageType(
        "git.watch",
        "Watch a tree for this client: the page's directory monitors and its 2 s tick, on the service.",
        request=_request(
            {
                "cwd": _req(_PATH),
                # The client's name for this watch (a page's): two pages
                # on one tree are two watches, and an unwatch names one.
                # The same handle again replaces that watch.
                "handle": _req(_s(64, low=1)),
                "files": Field(K_LIST, high=REPO_PATHS_MAX, item=_s(MAX_PATH)),
                # The tree-state digest the page's read sampled: the
                # watch's first compare is against it.
                "state": _null(_s(64)),
                # False (PR-2.2): a commit, range or branch load's watch,
                # the index, HEAD and the refs on the 2 s tick alone — no
                # monitors, no state digest. Absent is true.
                "working_tree": _BOOL,
            }
        ),
    ),
    MessageType(
        "git.unwatch",
        "Stop one watch of this client's, by its handle.",
        request=_request({"handle": _req(_s(64, low=1))}),
    ),
    MessageType(
        "git-changed",
        "A watched working tree moved: the three signatures, each a short digest.",
        event=_event(
            SERVICE,
            {"cwd": _req(_PATH), "tree": _req(_s(64)), "refs": _req(_s(64)), "state": _req(_null(_s(64)))},
        ),
    ),
    MessageType(
        "git.plan",
        "Carry a gitpatch plan out on the service, refused stale when its keys moved.",
        request=_request(
            {
                "cwd": _req(_PATH),
                "load": _req(Field(K_JSON)),
                "path": _req(_s(MAX_PATH, low=1)),
                "previous_path": _null(_s(MAX_PATH)),
                "parent_target": _null(_s(256)),
                "op": _req(Field(K_STR, choices=PLAN_OPS, high=SHORT_MAX)),
                "paths": _req(_REPO_PATHS),
                "patch": _null(_TEXT),
                "three_way": _BOOL,
                # The stable keys (diffmodel.stable_key) the plan was made
                # against: every one must still be in the fresh patch.
                "keys": Field(K_LIST, high=REPO_PATHS_MAX, item=_s(1024)),
            },
            reply={
                # `ok` is the envelope's: the plan's outcome is `applied`.
                "applied": _req(_BOOL),
                "stdout": _s(GIT_STDERR_MAX),
                "stderr": _s(GIT_STDERR_MAX),
                "unreachable": _BOOL,
                "three_way": _BOOL,
                "conflicts": _BOOL,
            },
        ),
    ),
    MessageType(
        "fs.trash",
        "Move files to the trash on the service's machine (§3.23).",
        request=_request(
            {"paths": _req(Field(K_LIST, low=1, high=REPO_PATHS_MAX, item=_PATH))},
            reply={
                "trashed": Field(K_LIST, high=REPO_PATHS_MAX, item=_PATH),
                "removed": Field(K_LIST, high=REPO_PATHS_MAX, item=_PATH),
            },
        ),
    ),
    # -- files over the API (§3.23, PR-2.3): the editor's files
    MessageType(
        "fs.read",
        "A file's text off the service's machine, with the mtime the save that follows expects.",
        request=_request(
            {
                "path": _req(_PATH),
                # Refused `refused` over this many bytes (FILE_TEXT_MAX when
                # left out).
                "max": _i(1, FILE_TEXT_MAX),
            },
            reply={
                # Empty for a binary file (`binary`: a NUL in its first 8 KiB).
                "text": _req(_FILE_TEXT),
                "encoding": _req(_ENCODING),
                "mtime": _req(_MTIME),
                "size": _req(_i(0, SIZE_MAX)),
                "binary": _req(_BOOL),
                # Set by the transport when the text went ahead as TAG_BLOB
                # frames (CHUNKED_FIELDS): the client joins them back.
                "text_chunked": _BOOL,
                "text_bytes": _i(0, SIZE_MAX),
            },
        ),
    ),
    MessageType(
        "fs.write",
        "Write a file's text on the service's machine, refused stale when its mtime moved.",
        request=_request(
            {
                "path": _req(_PATH),
                "text": _req(_FILE_TEXT),
                # The mtime the client's last read or write of the file
                # answered: a file whose mtime differs now is refused
                # `stale` and left as it is. Null writes regardless (the
                # Overwrite the user confirmed, or a file that is new).
                "expect_mtime": _req(_null(_MTIME)),
                # How the text is written back: the read's encoding; UTF-8
                # when left out, and when latin-1 cannot carry the text.
                "encoding": _ENCODING,
                # Set by the transport when the text went ahead as TAG_BLOB
                # frames (CHUNKED_FIELDS): the service joins them back.
                "text_chunked": _BOOL,
                "text_bytes": _i(0, SIZE_MAX),
            },
            reply={"mtime": _req(_MTIME), "size": _req(_i(0, SIZE_MAX)), "encoding": _req(_ENCODING)},
        ),
    ),
    MessageType(
        "fs.watch",
        "Watch a file for this client: a Gio.FileMonitor on the service (a directory's is PR-2.4's).",
        request=_request(
            {
                "path": _req(_PATH),
                "kind": _req(Field(K_STR, choices=WATCH_KINDS, high=SHORT_MAX)),
                # The client's name for the watch: what `file-changed`
                # carries back and what `fs.unwatch` names. A handle
                # watched again replaces its earlier watch.
                "handle": _req(_HANDLE),
            }
        ),
    ),
    MessageType(
        "fs.unwatch",
        "Stop a watch of this client's, by its handle.",
        request=_request({"handle": _req(_HANDLE)}),
    ),
    MessageType(
        "file-changed",
        "A watched file moved: its stat now (null when gone), debounced 300 ms on the service.",
        event=_event(
            SERVICE,
            {
                "handle": _req(_HANDLE),
                "path": _req(_PATH),
                "mtime": _req(_null(_MTIME)),
                "size": _req(_null(_i(0, SIZE_MAX))),
                "gone": _req(_BOOL),
            },
        ),
    ),
    # -- the service itself (§3.10)
    MessageType(
        "service.restart",
        "Restart the service: now, or once no session is busy; or call a waiting restart off.",
        request=_request(
            {"when": _req(Field(K_STR, choices=frozenset({"now", "idle", "cancel"}), high=8))},
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
                # The sandbox probe's verdict: "" when a box can be built here
                # (absent until the probe has run), and the live grants'
                # (sandboxgrants.GrantMounts.capable: "" when a directory
                # allowed while a session runs reaches it at once, else why
                # not; absent where the service runs no live grants).
                "sandbox": _s(ARG_TEXT_MAX),
                "live": _s(ARG_TEXT_MAX),
                "pid": _i(1, PID_MAX),
                # Whether the service's gh is there to be used (ghsetup's
                # word: "ready", "missing", "logged-out"); absent until the
                # service has asked (PR-1.12d).
                "gh": _SHORT,
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


class RequestRefused(Exception):
    """A request the service refused, as a client sees it: `error` (one of
    `ERRORS`, or a newer code shaped like one), the `msgid` and its
    `details` (the msgid's args; not `args`, which is `BaseException`'s
    tuple), for the client to translate."""

    def __init__(self, error: str, msgid: str, details: dict | None = None) -> None:
        super().__init__(error, msgid)
        self.error = error
        self.msgid = msgid
        self.details = dict(details or {})


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


def fit_field(name: str, value, spec: Field) -> tuple[bool, object]:
    """*value* as *spec* would keep it (what `validate` does per field),
    for a sender fitting its own facts before they go out: a string over
    its bound is cut to it, an integer clamped to its range, a list's
    items fitted one by one (the unfit dropped, never the list), an
    object's fields each fitted; what still doesn't fit is reported.
    Returns (True, the kept value) or (False, why it doesn't fit)."""
    try:
        return True, _check(name, value, spec)
    except _Invalid as first:
        closer = _fit_closer(name, value, spec)
        if closer is _UNFIT:
            return False, first.msgid.format_map(first.args_)
        try:
            return True, _check(name, closer, spec)
        except _Invalid as again:
            return False, again.msgid.format_map(again.args_)


_UNFIT = object()


def _fit_closer(name: str, value, spec: Field):
    """*value* brought toward *spec* (see fit_field), or _UNFIT when there
    is no bringing it closer."""
    kind = spec.kind
    if kind == K_STR and isinstance(value, str) and spec.high is not None and len(value) > spec.high:
        return value[: int(spec.high)]
    if kind == K_INT and _is_int(value):
        if spec.high is not None and value > spec.high:
            return int(spec.high)
        if spec.low is not None and value < spec.low:
            return int(spec.low)
    if kind == K_LIST and isinstance(value, list) and spec.item is not None:
        kept = []
        for index, item in enumerate(value):
            ok, fitted = fit_field(f"{name}[{index}]", item, spec.item)
            if ok:
                kept.append(fitted)
        if spec.high is not None:
            kept = kept[: int(spec.high)]
        return kept
    if kind == K_OBJ and isinstance(value, dict) and spec.fields:
        out = {}
        for field_name, field_spec in spec.fields.items():
            if field_name in value:
                ok, fitted = fit_field(f"{name}.{field_name}", value[field_name], field_spec)
                if ok:
                    out[field_name] = fitted
        return out
    return _UNFIT


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


class Deferred:
    """A reply that arrives later (PR-2.1). A request handler that must
    not block the service's main loop (one that runs git) returns one
    instead of a reply dict; the transport answers the request when
    `settle` is called with the reply (`reply` / `refuse`'s dict), on the
    main loop. The contract is unchanged for the peer: exactly one
    response per request, on the connection that asked, a reply that
    may simply take longer. `then` registers the transport's callback;
    one registered after the settle runs at once."""

    __slots__ = ("reply", "_callbacks", "settled")

    def __init__(self) -> None:
        self.reply: dict | None = None
        self.settled = False
        self._callbacks: list = []

    def then(self, callback) -> None:
        if self.settled:
            callback(self.reply)
            return
        self._callbacks.append(callback)

    def settle(self, reply: dict) -> None:
        if self.settled:
            return
        self.settled = True
        self.reply = reply
        callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            callback(reply)


def chunked_field(message: dict) -> str | None:
    """The one field of *message* that may travel in TAG_BLOB frames: the
    first of CHUNKED_FIELDS it holds as text."""
    for name in CHUNKED_FIELDS:
        if isinstance(message.get(name), str):
            return name
    return None


def chunk_stream(message_id) -> int | None:
    """The TAG_BLOB stream a chunked message's frames travel under: its
    id (a request's ``id``, a reply's ``re``) masked to 32 bits."""
    return (message_id & STREAM_MASK) if _is_int(message_id) else None


def split_message(message: dict, id_key: str) -> tuple[list[bytes], dict]:
    """*message* as the transport sends it: unchanged when it fits a
    frame; else, when it holds one of CHUNKED_FIELDS as text and an
    integer under *id_key* (``re`` for a reply, ``id`` for a request),
    the TAG_BLOB frames that carry that field's UTF-8 (stream: the id
    masked to 32 bits, offset: the byte offset) and the message with the
    field replaced by ``<field>_chunked`` and ``<field>_bytes``. Raises
    ValueError when the message fits no frame even so, or the field runs
    past CHUNKED_MAX."""
    text = json.dumps(message, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    if len(text.encode("utf-8", "surrogatepass")) <= MAX_FRAME:
        return [], message
    field = chunked_field(message)
    stream = chunk_stream(message.get(id_key))
    if field is None or stream is None:
        raise ValueError("message exceeds the frame limit")
    data = message[field].encode("utf-8", "replace")
    if len(data) > CHUNKED_MAX:
        raise ValueError("message exceeds the chunked limit")
    frames = [
        pack_frame(TAG_BLOB, 0, stream, offset, data[offset : offset + MAX_PAYLOAD])
        for offset in range(0, len(data), MAX_PAYLOAD)
    ]
    slim = {key: value for key, value in message.items() if key != field}
    slim[field + "_chunked"] = True
    slim[field + "_bytes"] = len(data)
    encode(slim)  # raises when the rest of the message is itself too large
    return frames, slim


def join_message(message: dict, chunks: bytes | None) -> dict | None:
    """The receiver's half of `split_message`: a message saying
    ``<field>_chunked`` gets its field back from the *chunks* collected
    for its stream (decoded with replacement); one short of
    ``<field>_bytes`` is refused as ``invalid`` by the caller (None)."""
    field = next((f for f in CHUNKED_FIELDS if message.get(f + "_chunked")), None)
    if field is None:
        return message
    joined = dict(message)
    joined.pop(field + "_chunked", None)
    wanted = joined.pop(field + "_bytes", None)
    data = chunks or b""
    if wanted is not None and len(data) != wanted:
        return None
    joined[field] = data.decode("utf-8", "replace")
    return joined


def split_reply(reply: dict) -> tuple[list[bytes], dict]:
    """`split_message` for a reply (its id is ``re``): the service's half."""
    return split_message(reply, "re")


def join_reply(reply: dict, chunks: bytes | None) -> dict | None:
    """`join_message` for a reply: the client's half."""
    return join_message(reply, chunks)


def split_request(request: dict) -> tuple[list[bytes], dict]:
    """`split_message` for a request (its id is ``id``): the client's
    half, for a `fs.write` whose text does not fit a frame (PR-2.3)."""
    return split_message(request, "id")


def join_request(request: dict, chunks: bytes | None) -> dict | None:
    """`join_message` for a request: the service's half, before `validate`."""
    return join_message(request, chunks)


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
