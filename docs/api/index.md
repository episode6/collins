<!--
SPDX-License-Identifier: MIT
Copyright (c) 2026 episode6, Inc.

This document and docs/api/messages.md are licensed MIT, not GPL-3.0 like
the rest of Collins: see the License section at the end.
-->

# The service API

`collins-service` runs Claude Code sessions: every agent's pty, the shared
state, the git and GitHub calls, the sandboxes. The Collins window is one
client of it, talking over a Unix socket. This document is the protocol that
socket speaks, published so that another development environment, editor or
script can be a client too. The [message reference](./messages) lists every
message type with its fields and bounds; this page is everything else.

::: tip License
This specification, the [message reference](./messages), the script that
generates it and the Python module that defines the protocol
(`collins/api/protocol.py`) are licensed [MIT](#license). Collins itself,
the service included, stays GPL-3.0-or-later. A program that talks to
`collins-service` only through this API is, in the view of Collins'
authors, an independent work: see [Interoperability](#interoperability).
:::

::: warning Stability
The protocol is at version 1 and the service has not shipped in a stable
release. Until it has, a message's fields can change without the protocol
number moving. Read the version and the capabilities off the `hello` reply
and treat anything unexpected as absent, as the Collins client does.
:::

## Finding the service

The service listens on a Unix socket at

```
$XDG_RUNTIME_DIR/collins/<app id>/api.sock
```

where the app id is `com.episode6.Collins` for a normal install
(`com.episode6.Collins.Debug` for a debug instance started from a
checkout). The directory is mode 0700 and the socket 0600: whoever can
open the socket is the service's own user, and that is the whole
credential. There is no token and no password. Without `$XDG_RUNTIME_DIR`
the directory is under the state directory,
`~/.local/state/collins/<app id>/`, shortened when the path would not fit
a `sockaddr_un`.

To start a service when none is running and learn its socket path in one
step, run the service's own helper. It prints the path and exits 0, or
exits 1 when no service came up:

```bash
collins-service --print-socket
```

Beside the socket the service writes `local-proof`, 32 random bytes in a
0600 file minted at each start. It is what a client reads to prove it
shares the service's filesystem; see [The local proof](#the-local-proof).

The socket is the API's only listener. A service on another machine is
reached by forwarding it: see [Connecting over ssh](#connecting-over-ssh).

## Connections

The socket speaks plain HTTP. A client opens a WebSocket at `/api/ws` and
says `hello` before anything else: a request of any other type before it
is refused `sequence`, an event before it is dropped, and a connection
that has said nothing after 5 seconds is closed.

```json
{"t": "hello", "id": 1, "protocol": 1, "version": "0.1.5",
 "client_id": "c0ffee", "device": "laptop", "locale": "en_US"}
```

The service answers with its own facts: the protocol it agreed on, the
oldest it speaks, its package version, its id, its hostname, the
capabilities it offers (`caps`) and where its local proof is.

```json
{"re": 1, "ok": true, "protocol": 1, "min_protocol": 1,
 "version": "0.1.5", "service_id": "a1b2…", "host": "laptop",
 "caps": ["local", "git", "files"],
 "local_proof": {"path": "/run/user/1000/collins/com.episode6.Collins/local-proof", "length": 32}}
```

**Two channels.** The Collins client opens two WebSockets with the same
`client_id`. The first, the **primary**, carries the subscription, every
event the service sends, every pty output frame and every input frame. The
second says `channel: "sync"` in its hello and is the **sync channel**: the
service answers requests on it and sends nothing down it unasked, so a
client can block a thread on it without ever having to drain events. A
sync channel's hello before its primary's is refused `sequence`. One
connection is enough for a client that is happy to multiplex: the sync
channel is a convenience, not a requirement. A request is always answered
on the connection that asked. When the primary closes the client is gone:
its sync channel is closed and the service forgets it, and nothing else
changes on the service. Sessions keep running whether anyone is attached
or not.

**Reconnects.** A hello with a `client_id` the service already holds takes
it over; the old connections are ended. Both ends set WebSocket keepalives
at the interval the [numbers table](./messages#numbers) gives.

**Versions.** `protocol` is the protocol the client speaks and
`min_protocol`, optional, the oldest it also speaks. The service computes
the newest protocol both windows share and answers with it, or refuses
`protocol` (naming both numbers in the refusal's `args`) and closes the
connection. Each side's window is at most two wide. New message types and
fields do not bump the protocol: they ship behind a name in `caps`, and a
client uses one only when the service lists it. The names today: `local`
(the service offers a local proof), `git` (the `git.*` requests, `fs.trash`,
`git-changed` and the `git` blob kind), `files` (the `fs.*` requests and
their events) and `debug` (the service was started with its debug API on
and serves `debug.*`, which no client outside Collins' own test suite
should rely on). The list is also in the
[enumerations](./messages#enumerations).

## Frames

Text frames are JSON objects in one of three shapes:

| Shape | Form | Direction |
| --- | --- | --- |
| request | `{"t": "<type>", "id": <int>, …fields}` | client → service |
| response | `{"re": <id>, "ok": true, …fields}` or `{"re": <id>, "ok": false, "error": "<code>", "msgid": "…", "args": {…}}` | service → client |
| event | `{"t": "<type>", …fields}` (no `id`) | either way, per type |

A request always goes from client to service and is answered by exactly
one response, which may take a while (the service defers the ones that
would block it). An event is never answered. Which peer may send each type,
in which form, is the [message reference](./messages): a known type from
the wrong peer is refused `direction`, an unknown one `unknown`, a request
without an `id` or an event with one `invalid`. Request ids are integers
from 0 to 2^53 − 1, so a JavaScript client reads them exactly; the client
picks them, and nothing requires them to be sequential.

**Unknown fields are dropped**, at every depth of a closed shape, and
every string, list and map is bounded. Versioning is additive because of
this: a newer peer's extra fields cost an older one nothing. Both ends
validate what they receive. Every frame is foreign content.

**Refusals.** A response with `ok: false` carries an `error` code from the
list below, a `msgid` and `args`. The `msgid` is an English sentence with
`{name}` placeholders and `args` fills them (`"{type} before hello"`,
`{"type": "attach"}`), so a client can translate the sentence as a whole
and format it afterwards. A code the client does not know should be read
as `failed`: a newer service may add one.

| Code | Meaning |
| --- | --- |
| `unknown` | A message type this peer does not know. |
| `invalid` | The type is known and the message is not a valid one of it: a missing or ill-typed field, a bound exceeded, a request without an id. |
| `direction` | A known type from the peer that may not send it. |
| `protocol` | The hello's protocol window does not meet this peer's. |
| `sequence` | A request this connection may not make yet: anything before hello, a second hello, a sync channel before its primary. |
| `gone` | What the request names does not exist, or not any more: a pty, a session, a tool call. |
| `refused` | The service understood and declined: a prompt that would not land, a path a sandbox will not take, a write of a key the client may not write. `msgid` says why. |
| `failed` | The service tried and it went wrong; `msgid` says how. |
| `stale` | What the request was built against has moved: a file's mtime, a git plan's stable keys. Nothing was changed. |

**Ids, paths and text.** A session, client, service, box, notification or
tool-call id is 1 to 128 characters of `[A-Za-z0-9._:-]` starting
alphanumeric. Every path is the service machine's, absolute, at most 4096
characters and free of NUL; a client never resolves a path against its own
filesystem. Text payloads are at most 1 MiB of characters, and no frame may
exceed 1 MiB of bytes once encoded, which is the tighter bound in practice.
Colours are `#rrggbb`. Enumerations a client sends are closed; strings the
service sends that a later service may extend (a status, a kind, a reason)
are bounded strings the receiver maps, with a fallback for the ones it does
not know.

### Free JSON

A few fields carry JSON the table does not shape: a state value, a PR
record, a tool call's arguments, a job's result. Such a value is kept whole
for its receiver to re-validate, within these bounds: nesting at most 32
deep, at most 100,000 values in all, every string at most 1 MiB of
characters, every object key at most 4096 characters, and no NaN or
infinity.

### Binary frames

Binary frames carry bytes behind a 16-byte big-endian header:

```
tag:u8  flags:u8  reserved:u16  stream:u32  offset:u64
```

| Tag | Meaning |
| --- | --- |
| `0x01` | pty output, service → client. `stream` is the pty, `offset` the count of live output bytes sent to this client for this pty so far. Flags: `0x01` part of a redraw, `0x02` the redraw's last frame. |
| `0x02` | pty input, client → service. `stream` is the pty; the payload is what the user typed, bytes for the pty. |
| `0x03` | a chunk of a chunked message, either way. `stream` is the message id masked to 32 bits, `offset` the chunk's byte offset. |

`reserved` is reported and never refused. A frame with an unknown tag, or
a tag from the wrong peer, is dropped and logged, never answered: binary
frames have no id to answer with. An output or input frame with `stream`
0 is invalid.

### Chunked messages

A message that would not fit a frame once encoded may carry one of its
large fields in `0x03` frames instead, when that field is one of
`CHUNKED_FIELDS` (`stdout`, `text`, `paths`, `entries`, `results`,
`names`) and the message has an integer id (a request's `id`, a reply's
`re`). The sender emits the field's UTF-8 (for the list-valued fields, the
JSON of the list) as `0x03` frames under the id masked to 32 bits, at most
`MAX_PAYLOAD` bytes each, followed by the message with the field replaced
by two: `<field>_chunked: true` and `<field>_bytes: <n>`. The receiver
collects the stream's chunks and puts the field back; a join short of
`<field>_bytes` is `invalid`. A chunked field runs to at most 64 MiB. It
works in both directions: a reply's `stdout` from the service, a request's
`text` from a client saving a large file.

### Flow control

WebSockets offer no backpressure, so the client acks. For every pty it is
attached to, the client sends an `ack {pty, offset}` event every 64 KiB of
live output fed to its terminal or 100 ms after the last unacked frame,
whichever comes first, with `offset` the output frame's counter. The
service hands a client at most 1 MiB of unacked live bytes and holds the
rest; a client that never acks is cut off at 4 MiB and redrawn. Redraw
frames carry the same counter but are neither counted nor acked.

## The local proof

A client on the service's own machine may use *local extras*: open a
path of the service's with a program of its own, show a folder in a file
manager, and the like. The service never infers that from the socket. The
client proves it with the `local` request: it computes the proof file's
path itself from the app id (never the path the hello named), opens it
with `O_NOFOLLOW`, requires a regular file of its own uid, mode 0600 and
exactly 32 bytes, and sends the bytes as lowercase hex. The service
compares them against what it wrote at start and marks the client `local`.
A client that cannot read the file sends no proof and is treated as remote,
which is the right answer for a client reaching the socket over an
[ssh forward](#connecting-over-ssh) from another machine. Bytes that do not
match are refused `refused`, and that client is remote too: it carries on
without the extras. Nothing a remote client can do depends on
the proof, except the service's confinement of a few reads to directories
it knows: see `fs.stat`, `fs.paste` and `fs.names` in the reference.

## A client's life

The sequence the Collins window follows, which any client can:

1. **`hello`** on each connection, as above.
2. **`local`**, when the proof is readable.
3. **`subscribe`**. The service sends its snapshot as ordinary events on
   the same connection, *before* the reply: an `item` per session with a
   sidebar row, a `pty` per live pty, a `pr` per session with PR records,
   a `state.set` per shared state key (a large map entry by entry), a
   `notify` per notification row and a `diff.notes` per session with marks
   on its diff, then `rows` (the sidebar's projection). The reply, carrying
   the counts of `item` and `pty` events sent, marks the snapshot complete.
   From then on every change arrives as the same events: an `item` carries
   only the fields that moved, a `state.set` one key or entry.
4. **`attach {pty, cols, rows}`** to show a pty. The reply names the grid
   the redraw is painted at, whether this client is the pty's *active*
   client (the one whose size the pty takes) and, when it is not, the
   active client's `device` for a "Sized for …" notice, and the terminal
   modes the redraw turns on. The redraw follows as `0x01` frames flagged
   `0x01`, the last also `0x02`, then live output. A second client may
   attach to the same pty; both see the output, and the active one's
   `resize` events size the pty. Send `resize` when your grid changes,
   `focus` when your view of the pty gains or loses focus, `theme` when
   your colours change, and `0x02` frames for what the user types.
   `detach` stops the output; the pty keeps running.
5. **`spawn`** to start something new: `kind: "agent"` with a `cwd` and,
   optionally, the session to resume, a `prompt` to hand the CLI, `model`,
   `effort`, `permission_mode`, `worktree`, `sandbox` and the rest of the
   table; `kind: "shell"` for a plain shell beside an agent. The reply
   names the pty, its grid, the service's `handle` for the session (its
   name before the CLI mints a session id) and the directory the pty
   actually started in, which may differ from the one asked for when it
   does not exist on the service's machine. The service assembles the
   `claude` command itself from its own installation; no client sends a
   command line. A session already running is refused `refused`: attach to
   its pty instead (the `pty` rows name the session each belongs to).
6. **`session` events** are the facts about an agent pty a client reads
   without a round trip: sent whole on attach, then as changed fields.
   `takes_prompt` says the CLI's input box is empty and would take a
   prompt; `busy` and `finished` are the service's reading of the agent's
   activity; `model`, `effort` and `permission_mode` are read off the
   transcript, null while unknown; `landed`, `reset`, `finished` and
   `forked` are one-shots, sent true once and never false. Requests a
   client makes of a session name its pty: `prompt` to type a prompt in
   (refused when the box is not empty, so nothing lands mid-sentence),
   `switch` for the model or effort, `write` and `send` for raw text,
   `cut` to lift the box's text out, `close` with a mode (`exit` for the
   graceful exit, `background` for the `/bg` handoff, `kill` to force it).
7. **`state.get` / `state.set`** read and write the shared state by key
   and, for map-valued keys, entry: names, favorites, archives, project
   order, the service-side settings. The service decides what a client may
   write and refuses the rest. The `store.*` requests are the higher-level
   mutations the sidebar makes (rename, favorite, archive, trash, …) and
   `store.flags` carries what a person did at a client's screen; the
   service's own verdicts (`busy`, `unread`, `background`) come back as
   `item` fields and are refused when a client sends them.
8. **Everything else** is one of the families in the reference: the PR hub
   and every `gh` call (`pr.*`), notifications (`notify.*`, `seen`), jobs
   (`job.start` and `job` events for a clone, a login repair, an icon
   generation), token use (`usage.get`, `models.get`), the session tools a
   running agent calls that need a screen (`tool` events and `tool-reply`),
   the sandbox (`sandbox.*`), git (`git.*`), files (`fs.*`) and the
   service itself (`service.status`, `service.restart`).

An event a client does not understand is dropped. A request the service
refuses leaves nothing changed.

## Plain HTTP on the same socket

Two things are too big or too binary for frames and go over HTTP on the
same listener:

- **`GET /api/blob?kind=<kind>&…`** answers a blob's bytes with an `ETag`
  and honours `If-None-Match`. The kinds are `git` (a file at a ref, the
  query naming the repository, the ref and the path), `pr` (a file of a
  pull request, at the URL `pr.blob` answers), `file` (an image a session's
  agent named, by `path` and `session`), `remote` (an http(s) image the
  service fetches, by `url`) and `icon` (a project's icon, by `root`). The
  URL of a `git` or `pr` blob is what the corresponding request answers:
  a client never builds a path into one. An unknown kind is 404.
- **`PUT /api/upload?session=<id>&name=<basename>`** stores a dropped or
  pasted file for a session and answers its path on the service's machine
  as JSON, for a prompt to mention. The body needs a `Content-Length`
  (411 without one) and is at most 64 MiB (413 past it).

Both read the `Collins-Client` header, the hello's `client_id`, to apply
that client's `local` standing. It is a grouping key, not a credential:
whoever reaches the socket is already the service's user.

## Connecting over ssh

The service opens no network port and has no login of its own. To reach one
on another machine, forward its Unix socket with ssh, which supplies the
authentication, the encryption and the host verification. The protocol does
not change: the WebSockets and the plain HTTP above all run over the forward
as they do over the socket itself. It takes two ssh commands.

**1. Start the service and learn its path.** Run `--print-socket` on the
remote machine, adding `--app-id ID` for a service that does not run under
the default app id:

```bash
remote=$(ssh -o BatchMode=yes user@host \
    '"$SHELL" -lc "collins-service --print-socket"' | tail -n 1)
```

Always ask for the path. The remote runtime directory is not yours to
guess, and a long path is shortened to fit a `sockaddr_un`. The `$SHELL -lc`
is there because the plain command `ssh host cmd` runs with a non-login
`PATH`, which misses `~/.local/bin`, where a pipx install puts
`collins-service`. Some profiles need more than this; the
[remote guide](../guide/remote#what-ssh-does-for-you) describes the shell
wrapper Collins uses. Put a time limit on this run (Collins gives it 10
seconds), and read its exit status:

| Exit | Meaning |
| --- | --- |
| 0 | The last non-empty line of stdout is the path. A login banner may come before it. |
| 255 | ssh itself failed: no route, a refused key, an unknown host key. The last line of stderr says which. |
| 127 | The remote shell did not find `collins-service`, or `$SHELL` names a shell that is not installed. |
| 1 | `collins-service` could not start a service, or none answered in time. |

**Check the path before using it.** ssh expands `${VAR}` and `%` tokens in
both halves of `-L` on your side, so a machine that prints such a path
chooses which of your environment variables reach it. Accept only an
absolute path of letters, digits and `.` `_` `-` `/` `@` `+` `,` `=`, at
most 108 bytes long.

**2. Hold the forward.** Keep this child running for as long as the
connection lasts:

```bash
mkdir -p -m 700 "$XDG_RUNTIME_DIR/my-client"
local="$XDG_RUNTIME_DIR/my-client/host.sock"
ssh -o BatchMode=yes -o ExitOnForwardFailure=yes \
    -o StreamLocalBindUnlink=yes \
    -o ControlMaster=no -o ControlPath=none \
    -o RemoteCommand=none -o RequestTTY=no \
    -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
    -N -L "$local:$remote" user@host
```

Then connect to `$local` and say `hello` as usual. What the options are for:

- `BatchMode=yes` stops ssh from prompting when nobody can answer. The key
  must be in the agent or have no passphrase, and the host's key must
  already be in `known_hosts`. Use it on the first command too.
- `ExitOnForwardFailure=yes` makes ssh exit when it cannot set up the
  forward, instead of staying up without one.
- `StreamLocalBindUnlink=yes` removes a stale socket left at `$local`, which
  would otherwise stop ssh from binding the path.
- `ControlMaster=no` and `ControlPath=none` give the forward a connection of
  its own. Under a shared control master the forward would live in the
  master, so ending this child would end nothing.
- `RemoteCommand=none` and `RequestTTY=no` keep the user's `~/.ssh/config`
  from changing what the command is. A `RemoteCommand` there makes
  `ssh host cmd` fail with exit 255, so set these on the first command too.
- The `ServerAlive` pair is ssh's own liveness check, which gives up after
  45 seconds.

**Guard the local socket.** Whoever can open it acts as the service's user
on the remote machine, with no further credential. Keep it in a 0700
directory of your own. ssh creates it 0600 by default, so leave
`StreamLocalBindMask` alone, and never forward to a TCP port, which any
local user could open.

**What changes for the client:**

- The `local` proof cannot succeed, by design. The hello's `local_proof.path`
  names a file on the remote machine, so never open it. A proof file you
  find at your own computed path belongs to a service on your own machine,
  and the remote service refuses its bytes. Stay
  [remote](#the-local-proof) and carry on.
- The hello's `host` and every path are the remote machine's. Move bytes
  through the API (`GET /api/blob`, `PUT /api/upload`, the `fs.*` requests)
  and don't look for those paths on your own disk.
- A session's `spawn` runs on the remote machine, on its `claude` login.

**Losing the connection.** The forward's exit is the signal to reconnect,
and the WebSocket keepalives notice a dead link in about 20 seconds. Sessions
keep running on the remote machine. To come back, run both commands again,
since the service may have stopped and `--print-socket` starts it. A hello
that fails while the forward is still up means the service has stopped, so
end that forward and run both again then too. Say `hello` with the same
`client_id`, then `subscribe` and `attach` again. Each attach redraws its
terminal from the service's screen. Collins waits 1, 2, 5 and 10 seconds
between attempts, then every 30.

**Ending it.** Terminate the ssh child and remove the local socket. A child
left behind by a client that crashed keeps running and holds its connection
open, so record its pid and end it at your next start, checking first that
the pid is still that forward.

## Writing a client

`collins/api/protocol.py` is the protocol's one definition and is itself
MIT-licensed: it imports nothing but the Python standard library, and a
Python client may vendor it whole. It gives `validate(message, sender)` and
`validate_response(message, request_type)` for the message table, `encode`
and `decode` for the text frames, `pack_frame` / `unpack_frame` /
`check_frame` for the binary header, `split_message` / `join_message` for
chunking and `negotiate` for the version window. A client in another
language reads the [message reference](./messages) instead, which is
generated from that module by `scripts/gen_api_reference.py` and pinned by
the unit suite, so the two never disagree.

The GPL-licensed parts of Collins are the worked examples: `collins/api/server.py` is the
service's end, `collins/api/client.py` the window's, `collins/connection.py`
the find-start-connect-reconnect loop and `collins/sshforward.py` the ssh
forward that reaches a service on another machine. Read them for how, not
to copy from: their license is Collins'.

## Interoperability

In the view of Collins' authors, a program that communicates with
`collins-service` only through this documented API, over its socket, is a
separate and independent work and not a derivative of Collins, whatever
the program's own license. The GPL continues to cover Collins itself: the
service, the window, and any copy or modification of their code other than
the files this page names as MIT. This is a statement of how the copyright
holder reads the license it chose, offered so that nobody has to guess; it
is not legal advice, and it does not grant permissions the GPL withholds
for the GPL-licensed code.

## License

This specification (`docs/api/index.md`), the message reference
(`docs/api/messages.md`), its generator (`scripts/gen_api_reference.py`)
and the protocol module (`collins/api/protocol.py`) are:

```
MIT License

Copyright (c) 2026 episode6, Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

The same text is in `docs/api/LICENSE`. Everything else in Collins is
[GPL-3.0-or-later](https://github.com/episode6/collins/blob/main/LICENSE).
