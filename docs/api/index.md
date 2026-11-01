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
This specification, the [message reference](./messages), [the terminal
contract](./terminal), the [session facts](./sessions) page, the script that
generates the reference, the Python module that defines the protocol
(`collins/api/protocol.py`), the one that connects over ssh
(`collins/sshremote.py`) and the terminal stream fixtures the contract is
measured against are licensed [MIT](#license). Collins itself, the
service included, stays GPL-3.0-or-later. A program that talks to
`collins-service` only through this API is, in the view of Collins'
authors, an independent work: see [Interoperability](#interoperability).
:::

::: warning Stability
The protocol is at version 1 and the service has not shipped in a stable
release. Until it has, a message's fields can change without the protocol
number moving. Read the version and the capabilities off the `hello` reply
and treat anything unexpected as absent, as the Collins client does. A second
implementation of the service changes nothing about this: nothing is stable
yet, and both projects are still testing. These pages, the reference and,
once it is published, the conformance kit are kept current with the service
by the unit suite's gates, so what they say is what `main` does on the day.
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
oldest it speaks, its package version, its id, its hostname, its product
name (`implementation`, optional: for a log line or a settings row, and never
a reason to behave differently), the capabilities it offers (`caps`) and where
its local proof is.

```json
{"re": 1, "ok": true, "protocol": 1, "min_protocol": 1,
 "version": "0.1.5", "service_id": "a1b2…", "host": "laptop",
 "implementation": "collins-service",
 "caps": ["background", "composer", "files", "git", "internal", "jobs", "local",
          "prs", "sandbox", "shells", "tools", "usage"],
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
`protocol` (naming both numbers in the refusal's `args`, and the `caps` its
hello would have listed) and closes the connection. Each side's window is at
most two wide. New message types and
fields do not bump the protocol: they ship behind a name in `caps`, and a
client uses one only when the service lists it. The names today: `local`
(the service offers a local proof), `git` (the `git.*` requests,
`git-changed` and the `git` blob kind), `files` (every `fs.*` request and
their events), `prs` (pull requests), `sandbox` (sandboxed sessions), `tools`
(the session tools that need a screen, a diff's marks, files handed between a
client and a session), `jobs` (long-running operations), `usage` (token use,
the model catalog and project icons), `background` (background agents),
`shells` (panel shells), `composer` (reading and editing the CLI's input box),
`internal` (what the Collins service keeps for its own window and tests) and
`debug` (the service was started with its debug API on and serves `debug.*`,
which no client outside Collins' own test suite should rely on). Every name
but `local` is also a family of message types: see
[Conformance](./messages#conformance). The list is also in the
[enumerations](./messages#enumerations). `collins-service` lists every family,
`debug` only when its debug API is on, and `local`, which a server lists only
with a proof.

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
| `0x01` | pty output, service → client. `stream` is the pty, `offset` the count of live output bytes sent to this client for this pty before this frame. Flags: `0x01` part of a redraw, `0x02` the redraw's last frame. |
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
whichever comes first, with `offset` the count of live bytes fed so far: a
frame's `offset` plus its payload's length. The service hands a client at most 1 MiB of unacked live bytes and holds the
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
   `0x01`, the last also `0x02`, then live output. [The terminal
   contract](./terminal) says what the redraw is, what your terminal must
   not send back in answer to it, and what the service has done to the
   stream before you see it. A second client may
   attach to the same pty; both see the output, and the active one's
   `resize` events size the pty. Send `resize` when your grid changes,
   `focus` when your view of the pty gains or loses focus, `theme` when
   your colours change, and `0x02` frames for what the user types. An
   input frame is written to its pty whether or not you have attached;
   `attach` is about output. `detach` stops the output; the pty keeps
   running.
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
   `forked` each mark something happening: sent true every time it does, and
   never false. `landed` comes with every read of the transcript, so expect
   it often, even from an idle session. What each fact means, what every
   server must guarantee of it and what any reading of it off Claude Code
   contends with is the [session facts](./sessions) page. Requests a
   client makes of a session name its pty: `prompt` to type a prompt in
   (refused when the box is not empty, so nothing lands mid-sentence),
   `switch` for the model or effort, `write` and `send` for raw text,
   `cut` to lift the box's text out, `close` with a mode (`exit` for the
   graceful exit, `background` for the `/bg` handoff, `kill` to force it).
7. **`state.get` / `state.set`** read and write the shared state by key
   and, for map-valued keys, entry: names, favorites, archives, project
   order, the service-side settings. The reference lists the
   [keys](./messages#state-keys) and
   [settings](./messages#service-settings). The service decides what a client
   may write and refuses the rest. The `store.*` requests are the higher-level
   mutations the sidebar makes (rename, favorite, archive, trash, …) and
   `store.flags` carries what a person did at a client's screen; the
   service's own verdicts (`busy`, `unread`, `background`) come back as
   `item` fields and are refused when a client sends them.
8. **Everything else** is one of the [families](./messages#conformance) in the reference: the PR hub
   and every `gh` call (`pr.*`), notifications (`notify.*`, `seen`), jobs
   (`job.start` and `job` events for a clone, a login repair, an icon
   generation), token use (`usage.get`, `models.get`), the session tools a
   running agent calls that need a screen (`tool` events and `tool-reply`),
   the sandbox (`sandbox.*`), git (`git.*`, with `git.run` taking one of the
   [git builders](./messages#git-builders)) and files (`fs.*`). The
   service's own requests sit beside them: `service.status`, which every
   server answers, and `service.restart`.

An event a client does not understand is dropped. A request the service
refuses leaves nothing changed.

## What a client can leave out

The Collins window is one client with many surfaces, and the reference lists
everything it sends. A client that shows sessions and terminals needs very
little of that: `hello`, `subscribe` if it lists what is running, `spawn`,
`attach`, `resize`, `ack`, input frames, and `prompt` and `close` for an agent.
The other families are features to add when you want them, and the `caps` in
the hello reply say which of them a service offers.

The reference marks a message *Optional for another client* when the window
sends it only for one of its own surfaces or for its own timing, or when it is
an event a client can ignore. A session works without them.

**One event needs an answer.** A running agent can call session tools that need
a screen, such as `show_diff`. Those reach the session's active client, the one
whose size the pty takes, as `tool` events. If no `tool-reply` comes back, the
agent waits 14 seconds for that call to fail. A client that doesn't implement a
tool should answer at once with `ok: false` and a `text` saying so.

## A minimal client

This starts an agent, waits until its input box will take a prompt, sends one,
and prints what comes back. It uses the [`websockets`](https://websockets.readthedocs.io/)
package for the WebSocket and the MIT-licensed `collins/api/protocol.py` for
the binary frames. Pass it the socket's path: the one
[`--print-socket`](#finding-the-service) prints, or a forward's
[local socket](#connecting-over-ssh).

```python
import itertools
import json
import sys
import time
import uuid

from websockets.sync.client import unix_connect

import protocol as p  # a vendored copy of collins/api/protocol.py

path, cwd = sys.argv[1], sys.argv[2]
next_id = itertools.count(1)
facts = {}  # what the session events have said, by pty


def handle(frame):
    """A frame that is not a reply: pty output or an event."""
    if isinstance(frame, bytes):
        header, payload = p.unpack_frame(frame)
        if header.tag == p.TAG_OUTPUT:
            sys.stdout.buffer.write(payload)
            sys.stdout.buffer.flush()
            if not header.flags & p.FLAG_REDRAW:
                fed = header.offset + len(payload)
                ws.send(json.dumps({"t": "ack", "pty": header.stream, "offset": fed}))
        return
    event = json.loads(frame)
    if event.get("t") == "session":
        facts.setdefault(event["pty"], {}).update(event)
    elif event.get("t") == "tool":
        reply = {"t": "tool-reply", "call": event["call"], "ok": False, "text": "Not supported here"}
        ws.send(json.dumps(reply))


def call(message):
    """Send a request and answer what arrives until its reply does."""
    request_id = next(next_id)
    ws.send(json.dumps({**message, "id": request_id}))
    while True:
        frame = ws.recv()
        reply = json.loads(frame) if isinstance(frame, str) else {}
        if reply.get("re") == request_id:
            if not reply["ok"]:
                raise RuntimeError(reply["msgid"].format(**reply.get("args", {})))
            return reply
        handle(frame)


with unix_connect(path, "ws://localhost/api/ws", max_size=p.MAX_INCOMING) as ws:
    call({"t": "hello", "protocol": p.PROTOCOL, "version": "0.1", "client_id": uuid.uuid4().hex, "device": "example"})
    call({"t": "subscribe"})

    pty = call({"t": "spawn", "kind": "agent", "cwd": cwd, "cols": 100, "rows": 30})["pty"]
    call({"t": "attach", "pty": pty, "cols": 100, "rows": 30})

    while not facts.get(pty, {}).get("takes_prompt"):
        handle(ws.recv())
    call({"t": "prompt", "pty": pty, "text": "Say hello."})

    stop = time.monotonic() + 60
    while time.monotonic() < stop:  # a real client keeps going until it is closed
        try:
            handle(ws.recv(timeout=1))
        except TimeoutError:
            pass
# The connection is gone and the session keeps running on the service.
```

Run it again with the same `cwd` and it starts a second agent. To come back to
this one, find its pty in the `pty` events `subscribe` sends and `attach` to it.
A real client also watches for `pty-exited`, sends `resize` when its grid
changes, and feeds the output to a terminal emulator instead of printing it.

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
as they do over the socket itself. It takes two ssh commands. A Python
client can vendor `collins/sshremote.py` instead of writing them: see
[Writing a client](#writing-a-client).

**1. Start the service and learn its path.** Run `--print-socket` on the
remote machine, adding `--app-id ID` for a service that does not run under
the default app id:

```bash
opts=(-o BatchMode=yes -o ControlMaster=no -o ControlPath=none
      -o RemoteCommand=none -o RequestTTY=no
      -o ServerAliveInterval=15 -o ServerAliveCountMax=3)
output=$(ssh "${opts[@]}" user@host \
    '"$SHELL" -lc "collins-service --print-socket"')
code=$?
remote=$(printf '%s\n' "$output" | tail -n 1)
```

Always ask for the path. The remote runtime directory is not yours to
guess, and a long path is shortened to fit a `sockaddr_un`. The `$SHELL -lc`
is there because the plain command `ssh host cmd` runs with a non-login
`PATH`, which misses `~/.local/bin`, where a pipx install puts
`collins-service`. Some profiles need more than this; the
[remote guide](../guide/remote#what-ssh-does-for-you) describes the shell
wrapper Collins uses, and `remote_command` in `collins/sshremote.py` is that
wrapper. Put a time limit on this run (Collins gives it 10
seconds), and read its exit status, `$code` above:

| Exit | Meaning |
| --- | --- |
| 0 | The last non-empty line of stdout is the path. A login banner may come before it. |
| 255 | ssh itself failed: no route, a refused key, an unknown host key. The last line of stderr says which. |
| 127 | The remote shell did not find the discovery command's program (`collins-service` by default), or `$SHELL` names a shell that is not installed. |
| 1 | `collins-service` could not start a service, or none answered in time. |

A server that is not `collins-service` documents its own discovery command,
which must behave as the table says: print the socket path as the last line
of stdout and exit 0, or exit non-zero. It is one program and its arguments,
because a client may run it under `exec` through the remote login shell, as
Collins does: for a variable write `env VAR=value program`, and use no shell
builtin, no pipeline and no leading `-`. A client that lets a connection name
one runs it verbatim in place of `collins-service --print-socket`, adds no
`--app-id` to it, and keeps the rest of this section as it is.

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
ssh "${opts[@]}" -o ExitOnForwardFailure=yes \
    -o StreamLocalBindUnlink=yes \
    -N -L "$local:$remote" user@host
```

Then connect to `$local` and say `hello` as usual. Keep that path short: a
Unix socket's path fits in about 100 bytes, and past that ssh says only `Bad
local forwarding specification`. Both commands share `opts`, as Collins' two
do. What the options are for:

- `BatchMode=yes` stops ssh from prompting when nobody can answer. The key
  must be in the agent or have no passphrase, and the host's key must
  already be in `known_hosts`.
- `ControlMaster=no` and `ControlPath=none` give each command a connection
  of its own. Under a shared control master the forward would live in the
  master, so ending this child would end nothing.
- `RemoteCommand=none` and `RequestTTY=no` keep the user's `~/.ssh/config`
  from changing what the commands are. A `RemoteCommand` there makes
  `ssh host cmd` fail with exit 255.
- The `ServerAlive` pair is ssh's own liveness check, which gives up after
  45 seconds.
- `ExitOnForwardFailure=yes`, on the forward, makes ssh exit when it cannot
  set up the forward, instead of staying up without one.
- `StreamLocalBindUnlink=yes`, on the forward, removes a stale socket left
  at `$local`, which would otherwise stop ssh from binding the path.

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

`collins/sshremote.py` is [Connecting over ssh](#connecting-over-ssh) as code,
and is MIT-licensed too. It imports nothing but the standard library, so a
Python client may vendor it as one file. It gives `parse_target` for a
`[user@]host[:port]` destination, `print_socket` for the first command (a
failure comes back as a code, and the words are yours to choose; `command=`
names a server's own discovery command), `Forward` for the child that holds
the forward (its `on_exit` is the signal to reconnect) and `sweep_orphans`
for a forward a crashed client left behind.

The GPL-licensed parts of Collins are the worked examples for a client:
`collins/api/client.py` is the window's end, `collins/connection.py` the
find-start-connect-reconnect loop and `collins/sshforward.py` the transport
that drives `sshremote` for it. Read them for how, not to copy from: their
license is Collins'. A server author who wants the
[Interoperability](#interoperability) section's reading writes from the MIT
pages alone and reads neither `collins/api/server.py` nor `collins/service/`.

## Implementing a server

A client is not the only program this specification allows: a program may
serve the same API in place of `collins-service`. The Collins window is one
client such a server would meet, and the other clients are the ones these pages
describe. A server author who wants the [Interoperability](#interoperability)
section's reading writes from the MIT pages alone and reads neither
`collins/api/server.py` nor `collins/service/`.

**Levels and families.** Every message type has a family and a level, printed
under its summary in the reference and gathered in
[Conformance](./messages#conformance).

| Level | Name | What a client can do with it |
| --- | --- | --- |
| 0 | The wire | Connect and show an empty list. |
| 1 | Sessions and terminals | Run a Claude Code session in a terminal, with its list row, whether it is busy or unread, its model and effort, a typed prompt, and closing it. |
| 2 | The store | Organise the list: names, favorites, archives, projects, trash, folder trust, notifications. |

Levels 0 to 2 are the `core` family and build on each other, and Levels 1 and 2
together are the *sessions* profile to aim for first. Everything else is a
family behind a `caps` name, optional at any level: `git`, `files`, `prs`,
`sandbox`, `tools`, `jobs`, `usage`, `background`, `shells`, `composer` and
`internal`, and `debug`, Collins' own test probe, which no other server serves.
A family may also cover part of a core message, such as `spawn`'s
`kind: "shell"` or `close`'s `background` mode; Conformance lists those parts.
A `spawn` field whose value asks for nothing is accepted and ignored by every
server, and only a value that asks for a feature the server lacks is refused.

**What you say you serve.** List the families you serve in your hello's `caps`
and your product's name in `implementation`. A family you do not list is one
you do not serve. You should send `implementation` even if you list no family:
a hello with neither is the shape of a Collins service from before families,
and the Collins window answers it by offering to restart that service. Leave
`local` out and omit `local_proof` if you offer no proof.

**How a client reaches you.** Over ssh a client runs a discovery command to
learn your socket's path, so document yours.
[Connecting over ssh](#connecting-over-ssh) says how it must behave.

**State.** A Level 1 server answers `state.get` for every key it emits
`state.set` for, and refuses any other `gone`. A state key, or a field of
`service.status`, `session` or `item`, that belongs to a family, such as
`session_forwards` to `background`, is emitted, read and written only by a
server that lists the family. A client reads a key it never received as empty
and a field it never received as unknown.
[Conformance](./messages#conformance) says where each one belongs.

**Answer every request.** A server must answer every request it receives, and
a type outside its families gets `unknown` with nothing changed. Answer within
the client's call timeout (10 seconds for most of the Collins window's calls),
and a refusal promptly: a few hundred milliseconds at most. A client depends on
this to carry on without a feature, and the window blocks on some of its calls
while it waits, so a request left unanswered freezes it where a refusal costs a
round trip.

**What the Collins window does without a family.** It reads `caps` from every
hello, hides what belongs to a family you do not list, and sends none of that
family's requests, blob fetches or uploads. A Level 0 server shows it a
connected, empty sidebar with no dialog. Without a family:

| Family | What the window does without it |
| --- | --- |
| `git` | No branch, git toggle, git page or git menu items. |
| `files` | The editor opens nothing, and its banner says the service has no files. The file tree and quick open stay empty, and projects show the generic icon. A client that is not local gets one box to type a folder's path in, not the picker, and you judge the path it hands you. Whether a folder is a checkout can't be asked, so the worktree box and *Git pull* go too and a launch is plain. |
| `prs` | No pull request chips, pages or menus, and no gh notice. |
| `sandbox` | No Sandboxed checkbox, pin, chip or Preferences group. |
| `tools` | No attachments gallery or session-tool settings. A drop is refused with a toast. |
| `jobs` | No clone, generated icon, new scratch chat or link repair, and no login repair at launch. |
| `usage` | No usage panel and no icon generation. The model menus fall back to a built-in list. |
| `background` | No background item or button: closing a tab exits the agent. |
| `shells` | No terminal panel, and Ctrl+J does nothing. |
| `composer` | Typing goes to the agent. The composer sends through `prompt`, into an empty box only, as the model and effort menus do with `/model` and `/effort`. A closed draft is kept and never typed back. |
| `internal` | No *Restart service* or CLI path row. The editor follows `agent_cwd` itself, a close is timed by the window and ends in `kill`, and a protocol mismatch offers only Quit. |

Three more things matter for a server. A `subscribe` you refuse, or a first one
you do not answer in time, is a failed connect, not a failed startup: on a
reconnect as at launch, the window shows its Reconnecting banner and tries again.
An `item` with no `path` is a row with
no transcript, so the row's transcript actions are hidden. And a few refusals
are matched by their exact sentence, which
[Conformance](./messages#refusal-sentences-a-client-matches) lists.

**The terminal.** What a Level 1 server has to do to a terminal stream is
[The terminal contract](./terminal): the queries it answers in the child's
place and strips, with VTE 0.84.0's reply to each, the redraw it paints an
attaching client with, the screen model its session facts are read from, the
active client, and the environment an agent starts in.

**The session facts.** How a server reads a session's facts from Claude Code,
and what every server must guarantee of each fact, is the
[session facts](./sessions) page, with an appendix of the Claude Code
behaviour the reading rides on.

**What is still to be written.** These pages specify the messages, the
terminal and the session facts, and one thing a server needs is not published
yet. A conformance kit will test a server against these pages.

**Where this lives.** This documentation is kept in the Collins repository for
now, and may move to a repository both projects share once it settles. Every
link between the MIT pages is relative, so a move is a copy.

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

The same holds, in the authors' view, for a program that implements the
*service's* side of this API, written from this specification: it is an
independent work, whatever its license, and the GPL continues to cover
Collins' own implementation. This specification describes behaviour;
it does not grant a license to Collins' code, and a server that copies
from `collins/service/` is a derivative of Collins.

## License

This specification (`docs/api/index.md`), the message reference
(`docs/api/messages.md`), the terminal contract (`docs/api/terminal.md`),
the session facts page and its appendix on Claude Code
(`docs/api/sessions.md`), the reference's generator
(`scripts/gen_api_reference.py`), the protocol module
(`collins/api/protocol.py`), the ssh module (`collins/sshremote.py`)
and every file of the terminal stream fixtures (`tests/fixtures/streams/`:
the synthetic scenarios and their golden, the recorded streams of a Claude
Code session with their marks and goldens, measurements the terminal
contract rests on) are:

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
