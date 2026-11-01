<!--
SPDX-License-Identifier: MIT
Copyright (c) 2026 episode6, Inc.

This document is licensed MIT, not GPL-3.0 like the rest of Collins: see the
License section of index.md. It is written from the behaviour the Collins
service shows on the wire and from Claude Code as its users observe it, not
from Collins' source: nothing here is a copy or a paraphrase of code in
collins/service/ or of any other GPL-licensed file, in words or in
structure (an informative paragraph states the constraints a reading must
honour and why, never the steps of Collins' own reading).

Record of sources. Read for this page: the server-side API spec's design
sections (its required properties, its brief for this page and its
decisions) and its private reading of the service, used as a source of
facts only; the repository's agent guide and the skills on sessions and
the sidebar, the terminal tab, testing, the GPL notices and pull requests;
the author's memory notes on the CLI; real transcripts, read only; the
strings of the installed CLI and its --help; the recorded 2.1.288 terminal
streams under tests/fixtures/streams/; and a headless probe of 2.1.316 on
a bare pty in a throwaway home. Not read: any module of the service or of
the data layer. The text was compared by word n-grams against the spec's
reading of the service and against those modules' docstrings and comments
and reworded wherever a run of words was shared.
-->

# Session facts

A Level 1 server of [the service API](./index#implementing-a-server) runs a
Claude Code session on a pty and tells its clients what is going on in it:
the `session` event's fields, and the `item` fields of the session's list
row. This page is the contract behind those fields. The
[message reference](./messages#session) gives each field's type and bounds;
this page gives its **meaning** (what a client may assume), the **required
properties** every server must satisfy, and, informatively, **what a
reading contends with**: the constraints Claude Code and the pty put on
any reading of the fact, with their reasons and the numbers Collins
measured, so that an implementer can build a reading of their own without
reading Collins' code. The
[appendix](#appendix-claude-code-as-observed) collects the facts about
Claude Code itself that such a reading rides on.

The companion page, [the terminal contract](./terminal), says what a server does to
a pty's byte stream; this one says what it reads off the session. The split
is deliberate: a client sees the stream through its own terminal, and reads
the facts off these events, never the other way round.

::: warning Stability
What [the specification](./index) says of stability holds here. The facts
below are what the Collins service does on `main` on the day; none of them
is declared stable yet, and the ones that lean on Claude Code's own
behaviour lean on behaviour nobody has promised to keep (the appendix says
so of each). This page lives in the Collins repository for now and may move
to a shared repository once it settles; every link between the MIT pages is
relative, so a move is a copy.
:::

## What a session is

A **session** is one Claude Code conversation: a process the server started
on a pty it owns, and the transcript file that process writes. The server
starts it in answer to a `spawn {kind: "agent"}` request and assembles the
command itself: no client sends a command line. Collins runs the user's
login shell on the pty and types the `claude` command there, newline and
all, as a person would; a server may do that or run the CLI
directly, and the facts below are defined so that either works. Everything
the server knows about the session comes from three places: the pty's
screen (what the CLI has drawn), the server's process table (what runs on
the pty) and the transcript (what the CLI has recorded). Each fact says
which.

The session's **id** is the one Claude Code mints: the file name of its
transcript, less the `.jsonl` suffix. Until the CLI writes that file, which
it does once the first prompt is submitted, the session has no id, so a
fresh spawn is known by the `handle` the spawn reply names until the server
binds it to an id (`session` below). A resumed session has its id from the
start.

## Reading the facts

The `session` event is sent whole to a client when it attaches to the pty,
and after that with only the fields that moved. A field a client has never
received is unknown to it, not false; the message reference marks which
fields a server may leave out, and the Level 1
[conformance](./messages#level-1-sessions-and-terminals) list says which
ones every server sends. A nullable field is null while the fact is not
known yet (`model` before the first reply, `session` before the id is
bound), and absent when the server does not read it at all.

One property is **required of every fact**: an attach's snapshot and the
`session` events that follow are **one series**. A client that attaches
holds, after its snapshot, exactly what a client attached all along holds
after the events, and every later event is a change from that. So a
server must not read a fact once to fill the snapshot and again, apart,
for its change detection: the two readings can differ, and a client told
the odd one out is never corrected, because the change detection sees no
change. What this contends with is that most facts are polled or
re-read, and a polled fact can be misread for a moment (the process
facts below give a case measured in fractions of a millisecond); the
property makes such a misreading cost one poll period at most, where a
snapshot read apart from the series could make it last until the fact
really moves. A server that reads a fact once per period, keeps what it
last sent, sends the difference, and fills the snapshot from what it
last sent, has it.

Four fields are **one-shots**: `landed`, `reset`, `finished` and `forked`
are sent as `true` each time the thing happens and never as `false`. A
client acts on each arrival and keeps no state for them. `forked` belongs
to the `internal` family and is not described here.

Fields outside the Level 1 core belong to families and are described with
them in the reference: the sandbox chip's five fields to `sandbox`, `prs`
to `prs`, `attachments` to `tools`, `touched_files` to `files`, the
composer's `entered`, `foreign_paste` and `prompt_block` to `composer`,
and the rest to `internal`. A server that lists none of those families
sends none of those fields.

## The facts

### `takes_prompt`

**Meaning.** Text typed into the pty now goes into the CLI's input box,
which is empty, and a carriage return at the end of it submits exactly that
text: nothing is appended to text already in the box, and no menu, dialog
or permission prompt takes the keys instead. It is what the `prompt`
request checks before it types, and what a client reads before it offers a
"send" of its own.

It is **not** "the agent is idle". Claude Code empties its box as soon as a
prompt is submitted and keeps it empty, and typeable, for the whole of the
reply that follows: a prompt typed then is queued by the CLI behind the
turn. A client that wants "working or not" reads `busy`.

**Required.**

- `false` while no agent runs on the pty (`agent_running` false, or no
  child at all).
- never `true` while the box holds text, a stand-in for a paste included.
- may be `true` while the agent is streaming a reply, since the box is
  empty then; a server is not required to read it `false` mid-turn.
- read from the server's own model of the screen, never from a client's
  terminal (the rule of [the terminal contract](./terminal#the-screen-model-of-record-normative)), so that every client
  of one pty reads the same answer.

**What a reading contends with** (informative). The CLI draws its box
with cursor movement and partial repaints, so the byte stream never says
"the box is empty"; the answer is only on the screen, which is why the
required properties put it on the server's screen model. Three facts of
the appendix's [input box](#c-the-input-box) entry pin an empty box:
the prompt row opens with `❯` (U+276F) and a no-break space (U+00A0),
the cursor rests right after them, and the rest of the row holds nothing
but the CLI's hint. The no-break space is what tells the box from the
CLI's menus and dialogs, which use `❯` with an ordinary space, so a
reading that ignores the space answers `true` under a permission prompt.
The hint is text, so a reading needs a way to tell it from typed text:
it is drawn dim, which means the screen model has to keep each cell's
rendering and not only its character, and what "dim" looks like depends
on the colours the active client sent, which is why its fixed opening
`Try "` is the sturdier tell and Collins accepts either. Typed text
reaches the cursor's row (the box wraps onto continuation rows and the
cursor stays at the end of the text), so the cursor's row is enough to
read. Collins re-reads it whenever the pty's output settles, and the
field follows the screen within about 50 ms.

### `busy`

**Meaning.** The server's reading that the agent is working: a turn is in
progress, a tool is running, a sub-process of the agent's is alive. A client
shows it (Collins paints a moving guide line on the row) and does not act on
it beyond that; the edge that matters for notifications is `finished`.

**Required.**

- `true` within 3 seconds of the agent beginning a turn after a `prompt`
  request, or after a carriage return typed into the pty.
- `false` within 10 seconds of the agent's turn ending with no further
  activity: no output, no progress report, no live sub-process of the
  agent's.
- never flaps more than once per second: a reading that changes is held
  for at least a second before it changes back.
- sent on the `session` event for the pty, and mirrored as the `item`
  field `busy` on every row that stands for the session.

**What a reading contends with** (informative). No single sign of work
meets the three bounds, which is why Collins reads several and lets each
hold for a while of its own, busy ending only when every one has lapsed.
What each sign can and cannot say:

- **The CLI's progress report** (the appendix's
  [progress](#f-progress-reports-and-the-two-environment-variables)
  entry) is the earliest sign and the only one the CLI gives on purpose:
  it starts within half a second of the submit and clears at the turn's
  end. But it also clears briefly between tool calls and while a
  permission prompt waits, so a clear cannot end busy at once or the
  field flaps on every tool call, past the once-a-second bound; a clear
  needs a grace (Collins waits 3 seconds for the next report). And it is
  absent for a person who turned it off and for a background agent,
  whose environment the daemon scrubs.
- **Output on the pty** is a sign of a reply, but a weak one: the CLI
  repaints while idle, and what a server types echoes back, so output
  has to be held for a short window only (Collins: 2 seconds, and the
  server's own writes discounted for a quarter of a second after each
  key) and cannot be the sole sign, since a long tool run prints nothing
  for minutes. Collins stretches the window to a minute while a progress
  report stands, for exactly that case.
- **The spinner column** (the appendix's [spinner](#e-the-spinner-column)
  entry) moves through every tool loop, but one repaint moves it too, so
  it counts only as a streak (Collins samples it every 350 ms and counts
  1.4 seconds of motion). A still column says nothing.
- **The process table**: a running tool is a live descendant of the
  agent. The CLI keeps long-lived children from its start (its MCP
  servers), so only descendants beyond that set are a sign, and the set
  has to be learnt after the spawn (Collins reads it 5 seconds in, and
  polls every 2 seconds).
- **The submit itself**: nothing is drawn for up to a second after a
  carriage return, so the 3-second bound needs the submit to count as a
  sign on its own (Collins: 2 seconds).
- **A background agent** gives none of the above; the daemon's own
  listing (the appendix's [agents](#k-claude-agents-json) entry) is the
  only reading, polled every 3 seconds.

Two consequences hold any such combination together: a sign that lapses
never ends busy while another still stands, so the readings cannot
contradict each other; and a session going busy again withdraws a finish
not yet counted, since the pause was a tool call's.

### `finished`

**Meaning.** A turn ended that a person has not seen yet: the moment to
notify. One-shot, sent `true` once per turn.

**Required.**

- sent at most once per turn. A repaint, a resize, a scroll or an
  interrupted turn does not produce a second one; a turn that produced no
  reply produces none.
- sent only after `busy` has fallen for the session.
- accompanied by `unread: true` on the `item` of every row that stands for
  the session, unless the row is being looked at: a client says so with
  `store.flags {unread: false}`, and a server that has heard that for the
  row since the turn began leaves `unread` alone. A row whose chain is
  still busy, or whose status is `background`, is left alone too.
- accompanied by a `notify {kind: "finished"}` event where the server
  serves Level 2.

**What a reading contends with** (informative). Every fall of `busy` is
not a finish: the CLI repaints while idle, and a repaint is a short burst
of output, so a server that notified on the busy edge alone would flag
rows unread for nothing. The transcript is the ground truth, and the
appendix's [records](#b-the-records) entry gives the marks a turn leaves
in it: a `turn_duration` record and at least one assistant record on the
main chain. So a finish is a fall of `busy` that the transcript confirms
with a turn it has not confirmed before, which is also what makes the
once-per-turn property hold through repaints, resizes and scrolls. Two
timing facts shape the confirmation: the CLI writes those records as the
turn closes, a moment after the screen goes quiet, so a fall of `busy`
has to wait for the transcript's next landing rather than be judged
against a stale file (Collins gives it 4 seconds); and only the CLI
writes to the file, so a file that grew while the fall waited is a turn
even when its records could not be reread in time. A fall the transcript
never confirms is dropped, and the person is not told. The finish, once
confirmed, is what sets `unread` and sends the notification.

### `model`, `effort`, `permission_mode`

**Meaning.** The model the agent is answering with, the effort level it is
running at and the permission mode in force, as the CLI reports them in its
own records. `null` until known. A client shows them (the footer's chips)
and offers to change them: the `switch` request types the CLI's own
`/model` or `/effort` command into the box.

**Required.**

- `null` until the transcript says, never a guess from the server's
  configuration (the defaults a client needs for a *new* session come from
  `models.defaults`, a `usage` family request, not from here).
- read from the transcript, so they are the CLI's word and survive a resume.
- a `switch` request is reflected within one turn: the CLI confirms a
  `/model` or `/effort` it accepted with a line of its own (the appendix's
  [switches](#i-model-and-effort) entry), and a server that reads the
  confirmation reports the change then; one that does not reads the next
  reply's model and effort instead, within the turn after the switch.
- a switch the CLI refused changes nothing.
- the strings are the CLI's own: a model id as the record carries it
  (`claude-opus-5-5`), an effort level as the CLI names it (`high`), a
  permission mode as the CLI names it (`auto`, `plan`, …). A client does
  not match them against a list of its own.

**What a reading contends with** (informative). The three values are in
the records the appendix's [records](#b-the-records) entry names: the
model and the effort on an assistant record (`message.model`, and the
record's own `effort`), the permission mode on a typed prompt's record
(`permissionMode`); the latest of each on the main chain is the current
one, since the CLI writes them with every record. Three things trip a
naive reading: the CLI writes a placeholder model into some synthetic
assistant records, which are not the agent's model; a sub-agent's records
sit in the same file with `isSidechain` true and carry the sub-agent's
model, not the session's; and a switch shows in no assistant record until
the next reply. For the within-one-turn property the appendix's
[switches](#i-model-and-effort) entry gives the two records a local
command leaves: the confirmation line names the effort level, so an
effort picked in the CLI's own menu is readable from it too, while the
model is named only in the command's argument, so a bare `/model` picked
from the menu is known at the next reply. The tail is where all of this
is, so a reading needs only the end of the file on each change.

### `agent_cwd`, `agent_running`, `running_command`, `pid`

**Meaning.** The server's reading of its process table for the pty.
`pid` is the process the server started on the pty (in Collins the shell;
a server that runs the CLI directly reports the CLI). `running_command`
says the pty's foreground is something other than that process: a command
runs in it. `agent_running` says that something is a Claude Code agent.
`agent_cwd` is the agent's current directory, which moves when the CLI
enters a worktree, and is what a client's footer and editor follow.

**Required.**

- read by whatever means the server's platform gives (`/proc` on Linux,
  libproc on macOS, a `ps` as a last resort); a server that cannot read
  one of them sends `null` for it or leaves it out, and a client then falls
  back: `agent_cwd` unknown reads as `cwd`; `agent_running` unknown reads
  as `running_command`; `running_command` unknown means a client cannot
  tell whether a graceful close worked and will force the close with
  `close {mode: "kill"}` once its budget runs out.
- `running_command` is the field that decides whether a session is
  **running** on the server: the `item`'s `running`, the pty table's rows
  and the refusal of a second `spawn` for the same session all follow it.
  A server must not report it `true` for a pty whose agent has exited and
  left only the shell.
- `agent_cwd` is an absolute path on the server's machine, refreshed often
  enough that a client polling every 2 seconds sees a worktree change
  within a few seconds.
- `agent_running` is re-read at least every 2 seconds while the pty
  lives, so that a wrong reading stands for at most one period; and the
  value a snapshot carries is the value the events last carried (the
  one-series property of [Reading the facts](#reading-the-facts)), so
  that a client attaching mid-run and a client attached all along hold
  the same word.

**What a reading contends with** (informative). When the command is
typed into a shell the agent is not the pty's child but a descendant of
it, and the only mark it carries is `claude` in its command line (a
wrapper that starts it, such as a sandbox launcher, names it too), so
the agent is found by descent from the pty's child and by name. The CLI
changes its own directory when it enters a worktree, while the shell's
stays put, so `agent_cwd` is the agent process's directory and not the
pty's; and the CLI's own children (its MCP servers) may name `claude` as
well, so the deepest such process is not necessarily the agent: Collins
takes the deepest one's directory, which is the agent's in practice,
since its children start where it is. Whether a command runs is readable
from the terminal itself: the pty's foreground process group is the
shell's own while nothing runs, so `running_command` needs no process
walk. Collins polls every 2 seconds, which is what the refresh property
asks, and reports a change on the next `session` event.

The process table can be misread for an instant, which is why the last
two properties exist. A process that execs on its way up shows an empty
command line while the new image loads: measured at about 0.3 ms and
never over 1 ms, but a reading that lands in that window sees no agent
at all, and a zombie shows the same empty line, so the command line
alone cannot tell the two apart. The re-read bounds the damage to one
period. The one-series property is what makes the bound hold for a
client that attaches: Collins once filled an attach's snapshot with a
fresh read of the process facts while its change detection kept its own
last reading, and an attach landing in that window told the client
`false` while the detection held `true`, which no later poll corrected
(one attach in fifteen hundred, with the attach's timing randomised);
that client refused to offer a prompt until the fact really moved. A
server fills the snapshot from what it last sent, and a misread lasts a
poll.

### `session`, `transcript_path`, `landed`, `reset`

**Meaning.** `session` is the session's id, the transcript's file-name
stem; `transcript_path` is the file. Both are `null` for a fresh spawn
until the server has found the transcript the CLI started writing (the
**resolver**), and from then on name it; a `resumed` session has both from
the spawn. `landed` says the server has just read the transcript and the
facts read from it are fresh; it comes with every read, so expect it often,
even from an idle session. `reset` says the server started over on the
transcript (the file was replaced, truncated or moved) and a client drops
what it derived from the old one.

**Required.**

- the id is the file-name stem of the transcript and nothing else; the
  transcript's own `sessionId` field is not consulted (the appendix's
  [files](#a-the-transcript-directory-and-its-files) entry says why).
- for a fresh spawn, bound within 10 seconds of the CLI writing the
  transcript's first record, while a client is attached. A server may slow
  or stop the search while no client is attached, and a client re-arms it
  with `resolver.arm` when it shows the tab again; a server with no such
  search answers `resolver.arm` `ok` and ignores it.
- never bound to a transcript another live pty of the server's already
  holds, nor to one that existed before the spawn (a directory often holds
  older sessions).
- once bound, the `item` for that id appears in the list (a Level 1
  server scans the transcript directory, or at least the one it just
  bound), and the pty table's row names the session.
- `transcript_path` follows the file if the CLI moves it (Claude Code
  moves a transcript into the worktree's directory when a session enters
  one); a move sends the new path with `reset`.

**What a reading contends with** (informative). The CLI mints the id and
writes the file only after the first submit (the appendix's
[files](#a-the-transcript-directory-and-its-files) entry), so no server
knows it at spawn; what it knows is the directory, from the working
directory and the encoding rule, and that is where the file will appear.
The directory usually holds older sessions, so a file that was there
before the spawn is never the new one, and two fresh spawns in one
directory would claim the same file, so a file another live pty is bound
to is excluded as well (the required properties say both). A `claude -w`
launch moves the session into the worktree's own directory, and a fast
`-w` writes the transcript within a second of making the worktree, so in
that directory "was there before" has to be judged by the file's age
against the launch, not by its presence. Polling at 1.5 seconds meets the
10-second bound with margin; Collins stops after 120 rounds when no
client is attached, which is why `resolver.arm` exists, and a submit
re-arms it since a submit is what makes the file appear. A pty a server
adopts after its own restart with no id recorded has only time to go on:
a transcript whose first record postdates the child's start is its own.
Once bound, the file is watched (a monitor and a poll, since a monitor
alone misses writes on some filesystems) and every read is a `landed`.

### `cwd`, `handle`, `pty`

`pty` and `handle` are the event's keys: the pty the session runs on and
the server's name for the session, minted at spawn and returned in the
spawn reply, the name a client uses for the session before (and beside)
its id. `cwd` is the directory the pty started in, as the spawn reply
reported it, which may differ from the one the client asked for when that
one does not exist on the server's machine.

## The `item`

An `item` is one row of the session list: a session the server found on
disk, with what a client needs to draw it. The snapshot sends one per
session; after that an `item` carries only the fields that moved, and
`removed` set `true` when the row goes away (the transcript was deleted,
or the session was forgotten). The list's order and grouping is the `rows`
event's.

**Meanings.** `session` is the id. `path` is the transcript file. `cwd` is
where the session began, read from the transcript; `resume_cwd` is where a
resume should start, the last directory the transcript records, which
differs from `cwd` for a session that entered a worktree. `project` names
the group the row sits in, the project's root directory (a worktree's row
sits under its repository). `display_name` is the name to show: a person's
own name for the row if they gave one, else the CLI's title, else a title
the server made from the first prompt. `cli_title` is the CLI's own title
as its records carry it. `subtitle` is a one-line note under the name (in
Collins, how long ago the session was active). `preview` is the first
prompt's text, cut short. `mtime`, `created` and `size` describe the
transcript: its modification time, when the session began and its size in
bytes. `state` is `""` or `interrupted`, the latter when the transcript's
last turn is one the person cut short (the appendix's
[interruption](#b-the-records) entry). `busy` is the busy tracker's word
for the row. `running` says a live agent pty of the server's runs this
session. `provider` is `claude`. `syncing` marks a row that a newer
transcript is about to replace (a server with no such notion never sends
it). `favorite`, a Level 2 field, is the person's star on the row, written
with `store.favorite`.

**Required.**

- `path` is always sent: a client builds no row without it.
- `status` and `unread` record something a person did in a client, and
  a client writes them with `store.flags`: `status` is the client's word
  (`open` while a tab shows the session, `attention`, `""`; the
  `background` family adds `background`), stored in memory and echoed back
  to every client; `unread` is set `true` by the server on a counted
  finish and cleared by a client's `unread: false` (a client may set it
  `true` too). `busy`, `backgrounding` and `can_background` from a client
  are refused: those are the server's own verdicts.
- `busy` on a row is `busy` of the session that stands for it (or of the
  pty's handle while the session has no row yet), and follows the same
  bounds.
- `running` is `true` while the session has a live agent pty whose
  `running_command` is `true`, and `false` once the CLI has exited, even
  while the shell lives on.
- a row for an archived session is not in the snapshot and is not sent
  until a client asks for the archived rows (`store.page-archived`) or
  looks a session up (`store.lookup`), both Level 2; archiving a listed
  row sends `put-away`.
- the optional fields (`project`, `cli_title`, `provider`, `state`,
  `syncing`) read as absent when a server never sends them.

**What a reading contends with** (informative). A transcript can run to
many megabytes, and a list needs only what the appendix's
[records](#b-the-records) entry places at the file's ends: the first
`cwd`, the first prompt and the first timestamp are in the opening
records, and the title records, the interruption marker and the last
`cwd` are the latest of their kind, so a bounded read of each end (Collins
reads a small prefix and the last 64 KiB) builds the row without parsing
the file. The CLI writes a stub file for a session that never got a
prompt, which is no row. The person's names, favorites, archives and
order are the server's own state, not the transcript's; `busy` and
`unread` are the session's; `running` is the pty's. The directory is
watched, so a row appears when the CLI writes the file, and a client can
ask for a rescan.

## A checklist for a server

The required properties above, gathered for a test to tick off:

| Fact | Property |
| --- | --- |
| `takes_prompt` | false with no agent; never true while the box holds text; read from the server's screen model |
| `busy` | true within 3 s of a prompt; false within 10 s of a turn's end; changes at most once a second |
| `finished` | once per turn, after `busy` fell, with `item.unread` unless the row was looked at; `notify {kind: "finished"}` at Level 2 |
| `model`, `effort`, `permission_mode` | null until the transcript says; a `switch` reflected within one turn |
| every fact | an attach's snapshot and the events are one series: the snapshot carries what the events last carried |
| `agent_cwd`, `agent_running`, `running_command`, `pid` | by the platform's means; null or absent when unreadable; `running_command` false once the agent has exited; `agent_running` re-read at least every 2 s |
| `session`, `transcript_path` | the file-name stem; bound within 10 s of the first record while a client is attached; never another live pty's file |
| `landed`, `reset` | one-shots, `true` only |
| `item` | `path` always; `status` and `unread` the client's; `running` is a live pty's `running_command`; archived rows on request |

## Appendix: Claude Code as observed

Everything above that the server reads off the CLI rests on how Claude Code
happens to behave: where it writes, what it draws, what it answers. **None
of it is promised by Anthropic.** It is what the program does as its users
see it on their own screens and in their own files, confirmed on the
versions each entry names, and any release may change any of it without
notice. A server must therefore **fail soft** on every entry: when one of
them stops holding, the fact that rode on it goes null, absent or false and
the session carries on; a server never crashes on it, never writes anything
to the CLI's files because of it, and never reports a fact it cannot read as
if it could.

Each entry is self-contained and names the version it was last confirmed
on. 2.1.316 is the CLI installed when this page was written (re-checked on
2026-11-01, headlessly: a real `claude` on a bare pty in a throwaway home,
with no prompt sent except where an entry says so); 2.1.288 is the version
the recorded terminal streams in Collins' test fixtures were made with. An
entry can be removed without disturbing the others.

### A. The transcript directory and its files

*Confirmed on 2.1.316.*

Under `~/.claude/projects/` there is a directory for every working
directory the CLI has been run in. The directory's name is the working directory's
absolute path with every character that is not an ASCII letter or digit
replaced by `-`: `/home/me/src/app` becomes `-home-me-src-app`. The encoding
is not reversible (a `-` and a `.` in the path both become `-`), which is
why a session's own directory is read from its transcript rather than from
the directory's name.

Inside, each session is one file named by a UUID with the suffix `.jsonl`,
one JSON object per line, appended to as the session goes. The UUID is the
session id: it is what `claude --resume` takes, and every record of the
file carries it again as `sessionId`. The file-name stem is the id a
server uses, because the file is the thing that exists before anything
else is known of the session, and because a copy of a transcript made for
a fork carries the original's ids inside while its name is the fork's.

No file exists before the first prompt is submitted: a CLI started and left
at its empty box writes nothing. A file holding metadata records only, which
some launches leave without a prompt (a `-w` launch's worktree agent among
them), is not a conversation and a list should skip it. A session that enters a worktree (`claude -w`, or a worktree the CLI
re-enters on resume) has its transcript moved to the worktree's own
directory, `<repo>/.claude/worktrees/<name>` encoded as above, at that
moment.

### B. The records

*Confirmed on 2.1.316.*

A transcript's records are typed by a top-level `type`. The ones a server
reads:

- **The first records are metadata.** A file written by 2.1.316 opens with
  records such as `last-prompt`, `bridge-session`, `mode` and
  `permission-mode`, which carry a `sessionId` and no `timestamp` and no
  `cwd`. The session's start time and directory are read from the first
  record that carries `timestamp` and `cwd`, not from line one.
- **`user`** records carry the person's prompts and the tools' results.
  Each has `cwd`, `timestamp`, `uuid`, `parentUuid`, `isSidechain`,
  `gitBranch` and `version` (the CLI's version), and `message.content`,
  which is a string or a list of parts. A typed prompt's record also
  carries `permissionMode` (`auto`, `plan`, …), the mode in force when it
  was typed; a tool result's record does not.
- **`assistant`** records carry a reply, or one block of one. Each has
  `message.model` (the model id, `claude-opus-5-5`) and a top-level
  `effort` (`low`, `medium`, `high`, `xhigh`, …). A record the CLI makes
  up locally carries the placeholder model `<synthetic>` and is skipped.
- **The main chain** is the records with `isSidechain` false; sub-agent
  transcripts (sidechains) live in the same file with `isSidechain` true
  and are ignored for the session's model, effort and finishes.
- **A turn's end** is recorded as a `system` record whose `subtype` is
  `turn_duration` (it carries a `durationMs`). Counting them counts turns.
- **An interrupted turn** leaves a `user` record whose text begins with
  `[Request interrupted by user` (as a string, or as the text of the first
  part of a list). A transcript whose last conversational record is one of
  these is in the `interrupted` state until the session moves on.
- **A local slash command** the CLI ran itself (`/model`, `/effort`,
  `/login`, …) is two `user` records in a row. The first's text is
  `<command-name>`, the command, `</command-name>`, then
  `<command-message>…</command-message>` and
  `<command-args>…</command-args>` (the args element empty when there were
  none); the second's text begins with `<local-command-stdout>` and holds
  what the command printed. The content may be a string or a one-part list.
- **Titles and names.** An `ai-title` record (`aiTitle`) holds the title
  the CLI generated; a `custom-title` record holds one the person set; an
  `agent-name` record holds the name a background agent was given. The
  last of any of them wins. (`custom-title` and `agent-name` were last seen
  in a transcript on 2.1.2xx; the 2.1.316 program still carries both
  record names.)
- **`worktree-state`** records (`worktreeSession`) say which worktree a
  session lived in; **`pr-link`** records (`prNumber`, `prRepository`,
  `prUrl`) name a pull request the session opened or was pointed at;
  a **`relocated`** record (`relocatedCwd`) notes a directory move. None of
  these is needed for the Level 1 facts; Collins reads them for its own
  rows.

The `sessionId` inside the records and the `gitBranch` on them are not
read for any fact on this page.

### C. The input box

*Confirmed on 2.1.316 (classic mode) and 2.1.288 (both modes, from the
recorded streams).*

At rest the input box is three rows on the screen: a full-width rule of `─` (U+2500),
a row that opens with `❯` (U+276F) followed by a **no-break space**
(U+00A0), and another full-width rule. The cursor rests in the third
column of the prompt row, right after the no-break space. The CLI's other
prompts and menus use `❯` with an ordinary space, which is what tells the
input box from them.

While the box is empty the CLI draws a hint in the rest of the prompt row,
rendered **dim** (SGR 2): one of several sentences, on 2.1.316
`Try "edit <filepath> to..."` or `Try "fix typecheck errors"`, every one of
them opening with `Try "`. The hint is drawn in pieces with cursor movement between
words, so a server reads it from its screen model, not from the byte
stream.

The box is `columns − 4` cells wide: two for the prompt mark and the space,
two of right margin. Text that does not fit wraps onto rows that open with
two ordinary spaces; a word is moved down whole, and a word longer than the
row fills it before breaking. A row that keeps trailing space cells was
ended by a typed line break rather than a wrap (the CLI erases to the end
of a wrapped row), though a repaint sometimes removes that cell. When the
text outgrows the screen the box scrolls within its own rows, and nothing
on the screen says that it has.

After a prompt is submitted the box is redrawn empty within about a second
and stays empty, and typeable, while the reply streams above it.

Under the lower rule the CLI prints the permission mode, the effort level
and hints; that row is not part of the box.

### D. Editing the box, and pastes

*Confirmed on 2.1.316.*

- **Keys.** Down moves to the next row of the box and is a no-op on the
  last; Ctrl+E moves to the end of the row; Backspace deletes one character,
  and at the box's start is a no-op. So whatever the box holds, one write
  of Down for each row below the cursor, then Ctrl+E, then as many
  Backspaces as there are characters, empties it. Escape is not used for
  this: pressed twice it interrupts a running turn.
- **A carriage return submits**, provided it arrives on its own after the
  text (a few milliseconds later): a carriage return inside the same chunk
  as the text is read as part of a paste and becomes a line break.
- **Pastes.** A bracketed paste (`ESC [ 200 ~` … `ESC [ 201 ~`) of any
  size is a paste event, and so is a plain chunk of more than 800
  characters. A paste of **more than 800 characters, or more than two line
  breaks** (counting `\r\n`, `\r` and `\n` each as one), is not put into
  the box as text: the box shows a stand-in, `[Pasted text #1]`, or
  `[Pasted text #1 +3 lines]` when the paste had line breaks, numbered per
  CLI process; the text is kept by the CLI and sent with the prompt. A
  paste of exactly 800 characters, or of two line breaks, lands as text.
  (The two-break allowance is the smaller of two and the terminal's rows
  minus ten, so a terminal under 12 rows folds sooner.) Several bracketed
  pastes in one write are separate events, each judged alone. One
  Backspace at the end of a stand-in deletes it whole.
- **Typing speed.** Keys written to the pty faster than a few milliseconds
  apart are read as one chunk, and a chunk over the limit above is a paste;
  a server that types on a person's behalf spaces its keys or uses a
  bracketed paste.

### E. The spinner column

*Confirmed on 2.1.316 (one prompt sent) and 2.1.288 (the recorded streams).*

While the CLI works it animates a spinner at the left edge of the screen,
below the reply so far and above the box: a glyph from a small set
(`✢`, `✶`, `✻`, `✽`, `✳`) redrawn several times a second, beside a
word and the elapsed time. The terminal's title animates with it (OSC 0,
alternating `◐` and `◑` ahead of the title's text). Motion in that column
over a second or so is a sign of work; a still column is not a sign of
idleness.

### F. Progress reports, and the two environment variables

*Confirmed on 2.1.316 (two prompts sent: one answered in a second, one
with a 12-second tool run) and 2.1.288 (the recorded streams).*

The CLI can report its progress to the terminal with the OSC 9;4 sequence
(`ESC ] 9 ; 4 ; <state> ; <percent> <terminator>`): state 0 clears the
report, 1 to 4 are the terminal-progress states (1 a definite percentage,
2 an error, 3 indeterminate, 4 paused). The CLI uses state 3 while it
works and 0 when it stops, and on both versions it writes the percent
field **empty**: `ESC ] 9 ; 4 ; 3 ; ESC \` and `ESC ] 9 ; 4 ; 0 ; ESC \`,
so a parser must accept an empty field. Every turn reports 3 once, within
half a second of the submitting carriage return, and 0 once as the turn
ends (measured on a turn of one second and one of fifteen); the report is
also cleared briefly between tool calls and while a permission prompt
waits, and once at startup. It is the earliest and the most
reliable sign of a turn's start and end, and the only one that comes from
the CLI rather than from reading its screen. The setting that turns it off
is `terminalProgressBarEnabled` in `~/.claude.json` (on by default), so a
server still needs the other readings for a person who turned it off.

The CLI emits it only for a terminal it believes can show it, and ends it
with BEL unless the terminal is the one it knows wants ST. Two environment
variables, set for the CLI's process, make it emit the sequence and end it
with ST, which is the form a VTE-based terminal parses:

| Variable | Value | Effect |
| --- | --- | --- |
| `ConEmuANSI` | `ON` | The CLI emits OSC 9;4 at all. |
| `TERM_PROGRAM` | `kitty` | The CLI terminates it with ST (`ESC \`) instead of BEL. |

One report is an exception on both versions: the clear the CLI writes as
it exits ends with BEL even under the two variables, which a server that
parses only ST-terminated sequences misses harmlessly (the process is
gone). A server that runs the CLI directly sets them in its environment;
one that types the command into a shell exports them first. They are the
[spawn environment](./terminal#the-spawn-environment-normative) of the terminal contract. They fail soft: a release that
ignores them simply emits nothing, and the busy reading falls back to the
screen and the process table.

Two side effects of the `kitty` name, both harmless to a VTE-based client,
which ignores them: the CLI terminates its title sequences with ST as well,
and it sends its desktop notifications as kitty's OSC 99 (on 2.1.316, "Claude
is waiting for your input" a minute after a turn ends unattended). The CLI
also sets the terminal's title (OSC 0 and 2) as it works and rings the bell
(BEL) when it wants attention; a server forwards all of these to its clients
unchanged.

### G. Leaving a session

*Confirmed on 2.1.316 (Ctrl+C Ctrl+C) and 2.1.2xx (`/bg`).*

- **Ctrl+C twice**, within a second or two, exits the CLI cleanly: it
  writes its last records and returns to the shell. A first Ctrl+C
  during a turn interrupts the turn instead, and one while the box holds
  text clears it, so a server that wants the exit sends the pair, waits,
  and sends it again if the CLI is still there.
- **`/bg`**, typed as a prompt, hands the conversation to the CLI's
  background daemon and exits the foreground process. The daemon
  continues it as a **new session**: a second transcript, forked from the
  first, whose first record carries the same `uuid` as the original's,
  which is how the two are paired (Collins keeps a chain from the old id to
  the new one and shows the pair as one row). The daemon's own id for the
  agent, the one `claude attach <id>` takes, is read off `claude agents
  --json` (entry K). The daemon is a separate process tree that no server
  owns.
- A session that was started with `-w` asks a question on exit (entry H).

### H. Worktrees: the exit dialog and the failure line

*Confirmed on 2.1.288 (the recorded streams) and, the failure line, on
2.1.316 (the program's text).*

- **The exit dialog.** When a session started with `claude -w` is exited
  with changes in its worktree, the CLI draws a menu headed `Exiting
  worktree session`, with a line counting the uncommitted files, two
  options, `1. Keep worktree` and `2. Remove worktree`, and the footer
  `Enter to confirm · Esc to cancel`. Enter takes the highlighted option
  (`❯` with an ordinary space marks it); `1` then Enter keeps the worktree.
  A server driving a graceful close answers it, with keep, before it can
  expect the shell back. A worktree with nothing to keep is removed, with
  its branch, without asking.
- **The failure line.** A `-w` launch that cannot create its worktree
  prints `Error creating worktree:` and the reason to stderr and exits
  with status 1 at once; nothing else is drawn. A server that launched
  with `-w` watches for that line and may retype the command without the
  flag.
- The CLI re-enters an existing worktree whenever its directory exists on
  resume, and recycles an unchanged worktree for a new `-w` session.

### I. `/model` and `/effort`

*Confirmed on 2.1.316 (the records and the program's text).*

Typing `/model <id>` or `/effort <level>` into the box and submitting it
changes the session's model or effort without a reply from the model. Each
leaves the two user records of entry B: the command, then its output. The
output line for the model is
`Set model to <display name> and saved as your default for new sessions`
(the display name, such as `Opus 5.5`, not the id; the id is read from the
command's argument, or from the next reply's record), and for the effort
`Set effort level to <level>`, possibly followed by more. A refused
switch prints something else and changes nothing.

Both commands also **write the person's defaults** into
`~/.claude/settings.json` (entry M). A server that posts them does so on a
person's request, never on its own, since it is changing their
configuration.

### J. The command line

*Confirmed on 2.1.316 (`claude --help`).*

The forms a server uses, all documented by the CLI's own help:

| Purpose | Command |
| --- | --- |
| a new session | `claude` |
| resume a session | `claude --resume <id>` |
| resume as a new session (a fork) | `claude --resume <id> --fork-session` |
| pick up the directory's latest session | `claude --continue` |
| a new session in a fresh worktree | `claude -w` or `claude --worktree <name>` |
| choose the session's model, effort level and permission mode | `--model <model>`, `--effort <level>`, `--permission-mode <mode>` |
| the server's MCP server, and nothing else | `--mcp-config <file>` and `--strict-mcp-config` |
| open a background agent | `claude attach <id>` |
| a headless run | `claude -p <prompt>` with `--tools ""` to run no tools |

A headless `-p` run does not ask for folder trust; Collins runs its title
and icon generation that way, from a scratch directory of its own, so that
those runs never list as sessions. An id the background daemon still lists
(a finished agent's included) cannot be opened with `--resume`, which the
daemon refuses: such a session is opened with `attach`.

### K. `claude agents --json`

*Confirmed on 2.1.316.*

`claude agents --json` prints a JSON array of the sessions the CLI's
daemon knows and exits; it needs no terminal, but it puts a terminal it is
given into raw mode, so a server runs it with its standard input closed.
Each element has `kind`, `sessionId`, `cwd`, `pid`, `startedAt` (epoch
milliseconds), `name` and `status` (`idle` or `busy`). On 2.1.316 the
array lists interactive sessions too (`kind: "interactive"`); a background
agent's element (`kind: "background"`) also carries an `id`, which is what
`attach` takes, and a `state` (last seen on 2.1.2xx). `--all` includes
completed ones. The listing is how a server tells a session that has
become a background agent from one that has not, and the only busy
reading it has for one, since the
daemon scrubs its agents' environment and they report no progress (entry
F).

### L. Folder trust

*Confirmed on 2.1.316.*

The CLI asks "Do you trust the files in this folder?" before its first
interactive session in a directory, and records the answer in
`~/.claude.json` under `projects`, keyed by the directory's absolute path,
as `hasTrustDialogAccepted: true`. The answer is inherited by
subdirectories: a session in a directory with a trusted ancestor is not
asked. A `-w` launch checks the worktree's exact directory. Collins writes
that one key, in the same form, before a launch the person confirmed, and
nothing else into the file; a server either does the same or lets the CLI
ask (the dialog is on the pty, and `takes_prompt` is false while it
shows).

### M. The model and effort defaults in `settings.json`

*Confirmed on 2.1.316.*

`~/.claude/settings.json` holds the person's default model as the top-level
`model` key and the default effort per model under
`modelSettings.<model id>.effortLevel`; a top-level `effortLevel` key also
exists in older files and is not what the CLI reads for a model that has
an entry. A `managed-settings.json` under the system's configuration
directory, when present, overrides them. These are the defaults a client
shows for a *new* session (`models.defaults`); they are never a session's
`model` or `effort`, which come from its transcript.
