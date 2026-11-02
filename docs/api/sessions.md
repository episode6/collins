<!--
SPDX-License-Identifier: MIT
Copyright (c) 2026 episode6, Inc.

This document is licensed MIT, not GPL-3.0 like the rest of Collins: see the
License section of index.md. It is written from Claude Code as its users
observe it (its files, its screen, its strings) and from the design
sections of the server-side API spec, not from Collins' source: nothing
here is a copy or a paraphrase of code in collins/service/ or of any other
GPL-licensed file, in words or in structure (an informative paragraph
states the constraints a reading must honour and why, never the steps of
Collins' own reading).

Record of sources. Read for this page: the server-side API spec's design
sections (its required properties, its brief for this page and its
decisions) and its private reading of the service, used as a source of
facts only; the repository's agent guide and the skills on sessions and
the sidebar, the terminal tab, testing, the GPL notices and pull requests;
the author's memory notes on the CLI; real transcripts, read only; the
strings of the installed CLI and its --help; the recorded 2.1.288 terminal
streams under tests/fixtures/streams/; and a headless probe of 2.1.316 on
a bare pty in a throwaway home (three short prompts in all); and the
reviews of the page on its pull request (Claude's, and the Opus 5.5 review
and its verification), which quote and cite the service's code and
measured the service against the page: every number this page gives for
Collins' own reading that is not the spec's comes from them and is
labelled as Collins'. Not read for the page: any module of the service or
of the data layer. After the page's text and its first corrections were
committed, its author read six modules of the service (tracking,
activity, core, session, hosting, finish) and the process-tree helper to
make the four service fixes the spec's decisions ordered; the corrections
made after that (the verification's items and the decisions on them) were
written from those decisions and the reviews, and compared by word runs
against those modules as well. Not run: the Collins service. The text was
compared by runs of five or more words against the spec's reading of the
service, the GPL modules' docstrings and comments, the author's memory
notes and the repository's skills, and the runs that were prose were
reworded (2026-11-01); the runs that remain are field names, glyph lists
and commonplace phrases.
-->

# Session facts

A Level 1 server of [the service API](./index#implementing-a-server) hosts a
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
below are what the Collins service on `main` does on the day; none of them
is declared stable yet, and the ones that lean on Claude Code's own
behaviour lean on behaviour nobody has promised to keep (the appendix says
so of each). This page lives in the Collins repository for now and may move
to a shared repository once it settles; every link between the MIT pages is
relative, so moving them is copying them.
:::

## What a session is

A **session** is one Claude Code conversation: a process the server started
on a pty it owns, and the transcript file that process writes. The server
starts it in answer to a `spawn {kind: "agent"}` request and assembles the
command itself; a client never sends one. Collins runs the user's
login shell on the pty and types the `claude` command there, newline and
all, as a person would; a server may do that or run the CLI
directly, and the facts below are defined so that either works. Everything
the server knows about the session comes from three places: the pty's
screen (what the CLI has drawn), the server's process table (what runs on
the pty) and the transcript (what the CLI has recorded). Each fact says
which.

The session's **id** is the one Claude Code mints: the file name of its
transcript, less the `.jsonl` suffix. Until the CLI writes that file, which
happens at the first submit, the session has no id, so a
fresh spawn is known by the `handle` the spawn reply names until the server
binds it to an id (`session` below). A resumed session has its id from the
start.

## Reading the facts

The `session` event is sent whole to a client when it attaches to the pty,
and after that with only the fields that changed. A field a client has never
received is unknown to it, not false; the message reference marks which
fields a server may leave out, and the Level 1
[conformance](./messages#level-1-sessions-and-terminals) list says which
ones every server sends. A nullable field is null while the fact is not
known yet (`model` before the first reply, `session` before the id is
bound), and absent when the server does not read it at all.

One property is **required of every fact**: an attach's snapshot and every
`session` event after it form **a single series**. A client that attaches
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

Three fields are **one-shots**: `landed`, `reset` and `finished` are sent
as `true` each time the thing happens and never as `false`. A client acts
on each arrival and keeps no state for them. Two arrive without a new
event: an attach's snapshot is itself a read of the transcript, so it can
carry `landed: true`, and the first bind of a fresh spawn sends `reset`
(below). `forked`, of the `internal` family and not described here, is of
the same kind but carries an id rather than `true`.

Fields outside the Level 1 core belong to families and are described with
them in the reference: the sandbox chip's five fields to `sandbox`, `prs`
to `prs`, `attachments` to `tools`, `touched_files` to `files`, the
composer's `entered`, `foreign_paste` and `prompt_block` to `composer`,
and the rest to `internal`. A server that lists none of those families
sends none of those fields.

## The facts

### `takes_prompt`

**Meaning.** Text typed into the pty now lands in the CLI's box, which is
empty, and a carriage return after it submits exactly that text: nothing is
appended to text already in the box, and no menu, dialog or permission
prompt takes the keys instead. It is what a `prompt` request sent with
`when_empty` re-checks before it types (and is refused on when the box
holds text), and what a client reads before it offers a "send" of its own.

It is **not** "the agent is idle". Claude Code empties its box as soon as a
prompt is submitted and keeps it empty, and typeable, throughout the
reply that follows: a prompt typed then is queued by the CLI behind the
turn. A client that wants "working or not" reads `busy`.

**Required.**

- `false` while no agent runs on the pty, by the best reading the server
  has: its process table (`agent_running`, the last reading it sent, so
  that the two facts agree) where it reads one, else whether the pty's
  foreground is a program other than the shell (`running_command`), else
  whether the pty has a child at all. A server that cannot read its
  process table sends `agent_running: null` and is held to the reading it
  has. Where a server reads `agent_running` or `running_command`, a shell
  the agent left behind is not the agent, whatever its prompt draws: a
  prompt typed then lands in the shell.
- the other box facts follow it: `entered` is null and `foreign_paste`
  false while the agent is not running, since a screen with no agent has
  no box to read typed text from (the CLI's hint, drawn before the agent
  is recorded, is not typed text).
- never `true` while the box holds text, a stand-in for a paste included.
- may be `true` while the agent is streaming a reply, since the box is
  empty then; a server is not required to read it `false` mid-turn.
- re-read when `agent_running` flips, not only when the screen settles,
  so the two never disagree for longer than a poll.
- read from the server's own model of the screen, never from a client's
  terminal (the rule of [the terminal contract](./terminal#the-screen-model-of-record-normative)), so that every client
  of one pty reads the same answer.

**What a reading contends with** (informative). The CLI draws its box
with cursor movement and partial repaints, so the byte stream never says
"the box is empty"; the answer is only on the screen, which is why the
required properties put it on the server's screen model. The appendix's
[input box](#c-the-input-box) entry gives the marks of an empty box at
rest. Four other things the screen can show share some of those marks,
and a reading has to tell each from an empty box:

| What the screen can show | What it is | What tells it from an empty box |
| --- | --- | --- |
| the prompt mark with an ordinary space after it | a menu or a permission prompt, which takes the keys | the no-break space, which the box alone has |
| the box's marks and the cursor right after them, with text further along the row | a box holding typed text whose cursor was sent to its start (Ctrl+A) | what the row holds past the cursor, not where the cursor is |
| the box's marks with dim text after them | the CLI's hint, of two kinds (the entry says which) | the dim rendering, which the screen model has to keep per cell and judge against the colours the active client sent; the kind shown at startup also opens with fixed words, the other kind does not, so a reading that matches the words alone answers `false` on every resting session showing the other kind |
| every mark of an empty box, with no agent on the pty | a shell's own prompt (a shell left behind by an exited agent can draw the same mark and space) | the process table, not the screen: the reason the first required property is what it is |

Typed text reaches the cursor's row (the box wraps onto continuation rows
and the cursor stays at the end of the text), so the cursor's row is
enough to read. Collins re-reads it whenever the pty's output settles, and
the field follows the screen within about 50 ms (Collins' settle).

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
- `false` within 10 seconds of the agent's process leaving the pty
  (`running_command` falling), whatever its last progress report said: a
  CLI killed mid-turn, or one that exited on its own, ends busy.
- no fall from a quiet under a second: a fall is reported only after
  the reading has stood false for one second, and a sign of work inside
  that second cancels the fall unreported, so every fall a client hears
  of stood a second of quiet and no one-shot (`finished`, `unread`, a
  notification) ever follows a shorter one. A rise is never held, so a
  reported fall may be followed by a rise sooner than a second (a process
  the agent left running is sighted again; output arrives as the quiet's
  second runs out), and a client that wants an indicator free of that
  flicker holds rises itself, which costs it nothing: a rise carries no
  one-shot.
- sent on the `session` event for the pty, and mirrored as the `item`
  field `busy` on every row that stands for the session.

**What a reading contends with** (informative). No single sign of work
meets the three bounds, which is why a reading takes several, each held
for a while of its own. What each sign can and cannot say, from the CLI's
side:

- **The CLI's progress report** (the appendix's
  [progress](#f-progress-reports-and-the-two-environment-variables)
  entry) is the earliest sign and the only one the CLI gives on purpose:
  it starts within half a second of the submit and clears at the turn's
  end. What it does between the two is the version's: the 2.1.288
  recordings hold it through a permission prompt and two tool calls,
  seven turns of 2.1.261 held it through tool loops and permission
  prompts with a flutter of clears in each turn's first seconds, and
  2.1.220 cleared it while a permission prompt waited. So a clear cannot
  end busy at once, or the field flaps on a version that beats, past the
  once-a-second bound; a clear needs a grace (Collins waits 3 seconds for
  the next report). And it is absent for a person who turned it off and
  for a background agent, whose environment the daemon scrubs.
- **Output on the pty** is a sign of a reply, but a weak one: the CLI
  repaints while idle, a resize or a focus report makes it repaint, a
  clear is followed by trailing repaints, and what a server types echoes
  back; so output has to be held for a short window only (Collins: 2 seconds, and the
  server's own writes discounted for a quarter of a second after each
  key) and cannot be the sole sign, since a long tool run prints nothing
  for minutes. Collins stretches the window to a minute while a progress
  report stands, for exactly that case, and ends it when the CLI leaves
  the pty.
- **The spinner column** (the appendix's [spinner](#e-the-spinner-column)
  entry) moves through every tool loop, but one repaint moves it too, so
  it counts only as a streak (Collins samples it every 350 ms and counts
  two changes at most 1.4 seconds apart). A still column says nothing.
- **The process table**: a running tool is a live descendant of the
  agent. The CLI keeps long-lived children from its start (its MCP
  servers), so only descendants beyond that set are a sign, and the set
  has to be learnt before the first turn (Collins gathers it over the
  session's life before the first submit and freezes it there, polls every
  2 seconds, and holds a sighting for 5 seconds).
- **The submit itself**: nothing need be drawn for a moment after a
  carriage return, so a typed carriage return is taken as a sign on its
  own (Collins: 2 seconds). Measured on the Collins service, `busy` rose
  2 ms after a typed carriage return and within 0.4 seconds of a `prompt`
  request, which makes no such mark.
- **A background agent** reports no progress (its environment is
  scrubbed) and runs under the daemon rather than on the pty, so the
  process table says nothing; its spinner is still painted into a pty
  attached to it, and the daemon's own listing (the appendix's
  [agents](#k-claude-agents-json) entry) carries a busy word of its own,
  which Collins polls every 3 seconds.
- **A fresh spawn** paints a welcome that animates, and the progress
  report can blip during it, before any prompt; a reading holds the
  signs off until the first submit, or it reads busy with no turn.

Two things hold any such combination together. A sign that lapses should
not end busy while another still stands, or the readings contradict each
other; Collins keeps one deadline, set by the latest sign, so a lapsed
clear can end the signs' hold while a live child still stands. The hold
on a fall keeps such a quiet from counting as a finish, and the rise that
follows the next sighting is not held, so a client of Collins can see
`busy` dip after a turn that left a child running (dips of 37 to 883 ms
were measured on the service under the hold; before it, falls of 22 to
300 ms, each then a false finish). And a session
going busy again withdraws a finish not yet counted, since the pause was
a tool call's.

### `finished`

**Meaning.** A turn ended that a person has not seen yet: the moment to
notify. One-shot.

**Required.**

- sent at most once per turn for a session bound to its transcript,
  deduplicated against the transcript: a repaint, a resize, a scroll or an
  interrupted turn does not count twice. What counts is a turn the
  transcript confirms, by its stamp moving or, when the confirmation's
  wait runs out, by the file having grown; so a submit the model did not
  answer and a local slash command count.
- before the session is bound there is no transcript to judge by, and a
  fall of `busy` is reported as a finish.
- sent only after `busy` has fallen for the session.
- accompanied by `unread: true` on the `item` of every row that stands for
  the session, except a row whose chain is still busy, one that is
  backgrounding, and one whose status is `background`. Whether a row is
  being looked at is not the server's to know: it sets the flag on every
  such row, and the client that is showing the session takes it off
  (`store.flags {unread: false}`) once it arrives, so a client may see its
  own row's `unread` go `true` and then `false`.
- accompanied by a `notify {kind: "finished"}` event where the server
  serves Level 2.

**What a reading contends with** (informative). Every fall of `busy` is
not a finish, and the CLI's side says why. It repaints while idle, on a
resize and on a focus report, and a repaint is a short burst of output,
so a server that notified on the busy edge alone would flag rows unread
for nothing. Only the CLI writes the transcript, and it writes a turn's
records as the turn closes, a moment after the screen goes quiet, so the
file is the ground truth and a fall of `busy` is judged against what the
file says after that moment, not before it (Collins allows 4 seconds for
the records to land). The appendix's [records](#b-the-records) entry
gives the marks a turn leaves: a turn-duration record, and assistant
records on the main chain. A turn interrupted from the keyboard leaves no
duration record, one prompt can leave several, and one stretch of busy
can cover two of the file's turns, so a reading compares what the file
holds now with what it held at the last counted finish and never counts
marks. A file that grew while the fall was being judged is a turn even
when its new records could not be read in time, since nothing else writes
it. A fall the file never confirms is dropped, and the person is not told.
A session with no transcript bound yet (a fresh spawn before its first
submit) has nothing to judge a finish by, which is why the required
properties have a fall of `busy` reported as a finish until the bind.
The finish, once confirmed, is what sets `unread` and sends the
notification.

### `model`, `effort`, `permission_mode`

**Meaning.** The model the agent is answering with, the effort level it is
running at and the permission mode in force, as the CLI's records report
them. `null` until known. A client shows them (the footer's chips) and
offers to change them: the `switch` request, where the `composer` family
is served, types the CLI's own `/model` or `/effort` command into the box.

**Required.**

- `null` until the transcript says, never a guess from the server's
  configuration (the defaults a client needs for a *new* session come from
  `models.defaults`, a `usage` family request, not from here).
- read from the transcript, so they are the CLI's word and survive a resume.
- a `switch` request (where served) is reflected within one turn: the CLI confirms a
  `/model` or `/effort` it accepted with a line of its own (the appendix's
  [switches](#i-model-and-effort) entry), and a server that reads the
  confirmation reports the change then; one that does not reads the next
  reply's model and effort instead, within the turn after the switch.
- a switch the CLI refused changes nothing.
- the strings are as the CLI writes them: a model id as the record
  carries it (`claude-opus-5-5`), or, between a `/model` command and the
  next reply, the name the command was given (an alias such as `opus`);
  an effort level as the CLI names it (`high`); a permission mode as the
  CLI names it (`auto`, `plan`, …). A client checks them against no list
  of its own.

**What a reading contends with** (informative). The three values are in
the records the appendix's [records](#b-the-records) entry names: the
model and the effort on an assistant record (`message.model`, and the
record's own `effort`), the permission mode on a typed prompt's record
(`permissionMode`) and on the standalone `permission-mode` records the
CLI writes whenever it saves its state (with each request and reply, and
as it exits); the current one is the latest of each, since the CLI
writes them over and over, and a standalone record carries no timestamp,
so latest means last in file order. A mode cycled at the
keyboard between prompts reaches the file at the CLI's next save, which
is the next prompt's at the latest, so a reading that takes the later of
the two records is right from then on. Three things trip a
naive reading: the CLI writes a placeholder model into some synthetic
assistant records, which are not the agent's model; a sub-agent's records
sit in the same file with `isSidechain` true and carry the sub-agent's
model, not the session's; and a switch shows in no assistant record until
the next reply. For the within-one-turn property the appendix's
[switches](#i-model-and-effort) entry gives the two records a local
command leaves: the confirmation line names the effort level, so an
effort picked in the CLI's own menu is readable from it too, while the
model is named only in the command's argument, so a `/model` chosen from
the CLI's own menu is known only when the next reply names it. The tail is
where all of this is, so a reading needs only the end of the file on each
change.

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
  enough that a client hears of a worktree change within a few seconds.
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
pty's. Nor is the CLI always the process a pty's foreground group is
headed by: `claude attach` and the daemon's `claude bg-pty-host` run the
CLI under a wrapper of their own, and the wrapper stays where it started
while the CLI moves, so the directory a reading wants is the CLI
process's own, wherever in the group it sits. Whether a command runs is readable from the
terminal itself: while nothing runs, the terminal's foreground group is
the shell itself, so `running_command` needs no process walk. Collins
polls every 2 seconds, which is what the refresh property asks, and
reports a change on the next `session` event.

The process table can be misread for an instant, which is why the last
two properties exist. Between the moment a process replaces its program
and the moment the new program's arguments are in place, its command line
reads as empty: Collins measured the gap at about 0.3 ms and never over
1 ms over three hundred launches of the CLI, when it fixed its own
snapshot, and a reading taken inside it finds no
process named `claude` on the pty. A finished process not yet reaped
reads the same way, so the command line alone does not tell a starting
agent from a dead one. The re-read bounds the damage to one
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
even from an idle session. `reset` says the server has just pointed the
session at a transcript (the first bind of a fresh spawn, or a new file
in place of the old) and a client drops what it derived from any earlier
one. A move is not a reset: the path changes and the derived facts stand.

**Required.**

- the id is the file-name stem of the transcript and nothing else; the
  transcript's own `sessionId` field is not consulted (the appendix's
  [files](#a-the-transcript-directory-and-its-files) entry says why).
- for a fresh spawn, bound within 10 seconds of the CLI writing the
  transcript's first record, with a client attached. A server may slow
  or stop the search while no client is attached, and a client re-arms it
  with `resolver.arm` when it shows the tab again; a server with no such
  search answers `resolver.arm` `ok` and ignores it.
- never bound to a transcript another live pty of the server's already
  holds, nor, for a new session, to one that existed before the spawn (a
  directory often holds older sessions); a `continue_recent` spawn binds
  the directory's latest existing transcript, which is its purpose.
- once bound, the `item` for that id appears in the list (a Level 1
  server scans the transcript directory, or at least the one it just
  bound), and the pty table's row names the session.
- `transcript_path` follows the file if the CLI moves it (Claude Code
  moves a transcript into the worktree's directory when a session enters
  one); a move sends the new path and no `reset`, and a file truncated in
  place is re-read, its facts sent as ordinary changes.

**What a reading contends with** (informative). The CLI mints the id and
writes the file only after the first submit (the appendix's
[files](#a-the-transcript-directory-and-its-files) entry), so no server
knows it at spawn; what it knows is the directory, from the working
directory and the encoding rule, and that is where the file will appear.
That directory usually holds older sessions, and two fresh spawns in one
directory would claim the same file: the two exclusions in the required
properties exist for those two facts. A `claude -w` launch moves the
session into the worktree's own directory, and the file can appear as
soon as the worktree does, so in that directory a file's age against the
launch, not its presence at spawn, says whether it is
this session's. A `--continue` launch is the exception the properties
carve out: its file is the directory's newest existing one. The 10-second
bound leaves a poll of a second or two ample margin. A search costs a
directory listing per round, which is why a server may rest it while no
client is attached and `resolver.arm` exists; and a submit is what makes
the file appear, so a submit is a reason to search again. A pty a server
adopts after its own restart with no id recorded has only time to go on:
a transcript whose first record postdates the child's start is its own.
Once bound, the file is watched and every read is a `landed`; a monitor
alone misses writes on some filesystems, so a poll backs it.

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
session; after that an `item` carries only the fields that changed, and
`removed` set `true` when the row goes away (the transcript was deleted,
or the session was forgotten). The list's order and grouping is the `rows`
event's.

**Meanings.** `session` is the id. `path` is the transcript file. `cwd` is
where the session began, read from the transcript; `resume_cwd` is where a
resume should start, the last directory the transcript records, which
differs from `cwd` for a session that entered a worktree. `project` names
the group the row sits in: the project directory's name, its last path
component (a worktree's row sits under its repository's). `display_name`
is the name to show: a name the person gave the row if they gave one, else
the title the CLI gave, else a title the server made from the first
prompt, else the preview, else the head of the id. `cli_title` is the
CLI's title as its records carry it. `subtitle` is a one-line note under the name (in
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

- `path` is in the first `item` for a session: a client builds no row
  without it, and the `item`s that follow carry only what changed.
- `status` and `unread` record something a person did in a client, and
  a client writes them with `store.flags`: `status` is what the client
  says (`open` while a tab shows the session, `attention`, `""`; the
  `background` family adds `background`), stored in memory and echoed back
  to every client; `unread` is set `true` by the server on a counted
  finish and cleared by a client's `unread: false` (a client may set it
  `true` too). `busy`, `backgrounding` and `can_background` are refused
  when a client sends them: those are the server's own verdicts.
- `busy` on a row is `busy` of the session that stands for it (or of the
  pty's handle while no row stands for the session yet), and follows the
  same bounds.
- `running` is `true` while a live agent pty of the server's runs the
  session and its `running_command` is `true`, and `false` after the CLI
  exits, even while the shell lives on.
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
the file. A launch can leave a file of metadata records and no prompt
(the appendix's [files](#a-the-transcript-directory-and-its-files) entry),
which is no row. The person's names, favorites, archives and
order are the server's own state, not the transcript's; `busy` and
`unread` are the session's; `running` is the pty's. The directory is
watched, so a row appears when the CLI writes the file, and a client can
ask for a rescan.

## A checklist for a server

The required properties above, gathered for a test to tick off:

| Fact | Property |
| --- | --- |
| `takes_prompt` | false unless the agent runs, by the best reading the server has; `entered` null and `foreign_paste` false then; never true while the box holds text; read from the server's screen model; re-read when `agent_running` flips |
| `busy` | true within 3 s of a prompt; false within 10 s of a turn's end and of the CLI leaving the pty; no fall from a quiet under a second, a rise never held |
| `finished` | once per confirmed turn of a bound session (a fall of `busy` before the bind), after `busy` fell, with `item.unread` on every row bar a busy chain, a backgrounding row and status `background`; `notify {kind: "finished"}` at Level 2 |
| `model`, `effort`, `permission_mode` | null until the transcript says; a `switch` reflected within one turn |
| every fact | the snapshot and the events form a single series: the snapshot carries what the events last carried |
| `agent_cwd`, `agent_running`, `running_command`, `pid` | by the platform's means; null or absent when unreadable; `running_command` false once the agent has exited; `agent_running` re-read at least every 2 s |
| `session`, `transcript_path` | the file-name stem; bound within 10 s of the first record with a client attached; never another live pty's file |
| `landed`, `reset` | one-shots, `true` only |
| `item` | `path` in the first `item`; `status` and `unread` the client's; `running` follows a live pty's `running_command`; archived rows on request |

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

Each entry stands on its own, refers to no other entry, and names the
version it was last confirmed on; the body above links entries and names
the CLI's glyphs and messages only by linking one, so an entry can be
removed without disturbing the rest. 2.1.316 is the CLI installed when
this page was written (re-checked on 2026-11-01, headlessly: a real
`claude` on a bare pty in a throwaway home, with three short prompts sent
in all, where an entry says so); 2.1.288 is the version the recorded
terminal streams in Collins' test fixtures were made with; an earlier
version is named where a fact was last seen on it.

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
a JSON object on each line, appended to as the session goes. The UUID is the
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

- **The first records are metadata, and they recur.** A file written by
  2.1.316 opens with records such as `last-prompt`, `bridge-session`,
  `mode` and `permission-mode`, which carry a `sessionId` and no
  `timestamp` and no `cwd`. The session's start time and directory are
  read from the first record that carries `timestamp` and `cwd`, not from
  line one. The same group (`last-prompt`, `ai-title`, `mode`,
  `permission-mode`, `atis-latch`, …) is written again whenever the CLI
  saves its state: before each request, after each reply and as it exits,
  so these records are found through the whole file, and the last
  `permission-mode` record is the mode at the last save. A mode cycled at
  the keyboard (shift+tab) writes no record at the keystroke (none within
  5 s on 2.1.316): the next save carries it, and the next typed prompt's
  `user` record carries it too.
- **`user`** records carry the person's prompts and the tools' results.
  Each has `cwd`, `timestamp`, `uuid`, `parentUuid`, `isSidechain`,
  `gitBranch` and `version` (the CLI's version), and `message.content`,
  a string or else a list of parts. A typed prompt's record also
  carries `permissionMode` (`auto`, `plan`, …), the mode in force when it
  was typed; a tool result's record does not.
- **`assistant`** records carry a reply, or one block of one. Each has
  `message.model` (the model id, `claude-opus-5-5`) and, at the record's
  top level, `effort` (`low`, `medium`, `high`, `xhigh`, …). A record the CLI makes
  up locally carries the placeholder model `<synthetic>` and is skipped.
- **The main chain** is the records with `isSidechain` false; sub-agent
  transcripts (sidechains) live in the same file with `isSidechain` true
  and are ignored for the session's model, effort and finishes.
- **A turn's end** is recorded as a `system` record whose `subtype` is
  `turn_duration` (it carries a `durationMs`). One typed prompt can leave
  several (a real transcript holds 14 for one prompt) and an interrupted
  turn leaves none, so they are compared with what was seen before, never
  counted. A `user` record with `isMeta` true is text the CLI injected on
  its own (a message from another session, a caveat, a reminder), not a
  prompt the person typed, though it may carry `permissionMode`.
- **An interrupted turn** leaves a `user` record whose text begins with
  `[Request interrupted by user` (as a string, or as the text of the first
  part of a list). A transcript whose last conversational record is one of
  these is in the `interrupted` state until the next conversational record.
- **A local slash command**, one the CLI handled itself (`/model`, `/effort`,
  `/login`, …) is two `user` records in a row. The first's text is
  `<command-name>`, the command, `</command-name>`, then
  `<command-message>…</command-message>` and
  `<command-args>…</command-args>` (the args element empty when there were
  none); the second's text begins with `<local-command-stdout>` and holds
  what the command printed. The content may be a string or a one-part list.
- **Titles and names.** An `ai-title` record (`aiTitle`) holds the title
  the CLI generated; a `custom-title` record holds one the person set; an
  `agent-name` record holds the name a background agent was given. The
  last of any of them wins. (No transcript on hand, and they span 2.1.285
  to 2.1.316, holds a `custom-title` or an `agent-name` record; the
  2.1.316 program carries both record names.)
- **`worktree-state`** records (`worktreeSession`) name the worktree a
  session ran in; **`pr-link`** records (`prNumber`, `prRepository`,
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
column of the prompt row, right after the no-break space. Menus and
permission prompts use the same `❯` followed by an ordinary space, which
is what tells the input box from them.

While the box is empty the CLI draws a hint in the rest of the prompt row,
rendered **dim** (SGR 2). It is of two kinds, alike only in the dim
rendering: at startup, one of several fixed sentences, on 2.1.316
`Try "edit <filepath> to..."` or `Try "fix typecheck errors"`, each
opening with `Try "`; and, on a session at rest, a suggestion the CLI
writes for that session in words of its own (`promptSuggestionEnabled`
in `settings.json` turns that kind off when `false`). The 2.1.288
recordings show the startup kind only. Either is drawn in pieces with
cursor movement between words, so a server reads it from its screen
model, not from the byte stream.

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

- **Keys.** Down moves the cursor to the next row of the box and does
  nothing on the last; Ctrl+E moves it to the row's end and Ctrl+A to the
  box's start; Backspace deletes the character before the cursor and does
  nothing at the box's start. A single Escape interrupts a running turn,
  so it is no key for editing the box.
- **A carriage return submits**, provided it arrives in a write of its own
  after the text (Collins waits 250 ms between the two): a carriage return
  inside the same chunk as the text is read as part of a paste and becomes
  a line break.
- **Pastes.** Text wrapped in the bracketed-paste markers (`ESC [ 200 ~`
  before, `ESC [ 201 ~` after) is a paste event whatever its size, and so
  is a plain chunk of more than 800 characters. A paste **longer than 800
  characters, or holding three or more line breaks** (counting `\r\n`, `\r` and `\n` each as one), is not put into
  the box as text: the box shows a stand-in, `[Pasted text #1]`, or
  `[Pasted text #1 +3 lines]` when the paste had line breaks, numbered per
  CLI process; the text is kept by the CLI and sent with the prompt. A
  paste of exactly 800 characters, or of two line breaks, lands as text.
  (The allowance of two breaks is the lesser of two and the terminal's
  row count less ten; a terminal of fewer than 12 rows therefore folds at
  fewer breaks.) Two or more bracketed pastes arriving in one write count
  as that many events, each judged on its own. A Backspace right after a
  stand-in removes the whole stand-in.
- **Typing speed.** Keys written to the pty faster than a few milliseconds
  apart are read as one chunk, and a chunk over the limit above is a paste;
  a server that types on a person's behalf spaces its keys or uses a
  bracketed paste.

### E. The spinner column

*Confirmed on 2.1.316 (one prompt sent) and 2.1.288 (the recorded streams).*

While the CLI works it animates a spinner at the left edge of the screen,
below the reply so far and above the box: a glyph from a small set that
varies by version (`✢`, `✶`, `✻`, `✽`, and the plain `·` and `*`, have all
been seen in it) redrawn several times a second, beside a word and the
elapsed time. A reading watches the column for change and matches no
glyph. The terminal's title animates with it (OSC 0,
alternating `◐` and `◑` ahead of the title's text). Motion in that column
over a second or so is a sign of work; a still column is not a sign of
idleness.

### F. Progress reports, and the two environment variables

*Confirmed on 2.1.316 (two prompts sent: one answered in a second, one
with a 12-second tool run) and 2.1.288 (the recorded streams).*

The CLI can report its progress to the terminal with the OSC 9;4 sequence
(`ESC ] 9 ; 4 ; <state> ; <percent> <terminator>`): state 0 clears the
report, 1 to 4 are the terminal-progress states (1 a definite percentage,
2 an error state, 3 indeterminate progress, 4 a pause). The CLI uses state 3 while it
works and 0 when it stops, and on both versions it writes the percent
field **empty**: `ESC ] 9 ; 4 ; 3 ; ESC \` and `ESC ] 9 ; 4 ; 0 ; ESC \`,
so a parser must accept an empty field. Every turn reports 3 once, within
half a second of the submitting carriage return, and 0 once as the turn
ends (measured on 2.1.316 on a turn of one second and one of fifteen),
and the CLI clears the report once at startup. What it does between the
two has varied by version: the 2.1.288 recordings hold state 3 straight
through a permission prompt and two tool calls; on 2.1.261 seven turns
held it through tool loops and permission prompts, with 0 and 3
alternating about ten times a second in a turn's first two or three
seconds; on 2.1.220 a permission prompt cleared it. A reading therefore
gives a clear a grace rather than ending busy on it. It is the earliest
and the most reliable sign of a turn's start and end, and the only one
that comes from the CLI rather than from reading its screen. The setting that turns it off
is `terminalProgressBarEnabled` in `~/.claude.json` (on by default), so a
server still needs the other readings for a person who turned it off.

The CLI emits it only for a terminal it believes can show it, and ends it
with BEL unless the terminal is the one it knows wants ST. Two environment
variables, set for the CLI's process, make it emit the sequence and end it
with ST, which is the form a VTE-based terminal parses:

| Variable | Value | Effect |
| --- | --- | --- |
| `ConEmuANSI` | `ON` | Turns the OSC 9;4 reports on. |
| `TERM_PROGRAM` | `kitty` | The CLI terminates it with ST (`ESC \`) instead of BEL. |

The clears the CLI writes as it exits are an exception on both versions:
they end with BEL even under the two variables (the 2.1.288 recording of
the fullscreen mode ends with two of them around one ST-terminated
clear). A server that parses only ST-terminated sequences does not see
them, so a CLI that leaves mid-turn leaves its last report standing, and
the reading has to end busy from the process table instead (the
`running_command` bound of `busy`). A server that runs the CLI directly
sets the two variables in its environment; one that types the command
into a shell exports them first. They are the
[spawn environment](./terminal#the-spawn-environment-normative) of the
terminal contract. A release that ignores them emits no report, and the
other signs of the busy reading stand alone, which is this appendix's
rule for every entry.

Two side effects of the `kitty` name, both harmless to a VTE-based client,
which ignores them: the CLI terminates its title sequences with ST as well,
and it sends its desktop notifications as kitty's OSC 99 (on 2.1.316, "Claude
is waiting for your input" a minute after a turn ends unattended). The CLI
also sets the terminal's title (OSC 0, OSC 2) as it works and rings the bell
(BEL) when it wants attention; a server forwards all of these to its clients
unchanged.

### G. Leaving a session

*Confirmed on 2.1.316 (the Ctrl+C pair); the `/bg` handoff last observed on
2.1.226, and the 2.1.316 program still carries the command.*

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
  agent, the one `claude attach <id>` takes, is in the daemon's listing
  (`claude agents --json`). The daemon is a separate process tree that no
  server owns.
- A session started with `-w` that exits with changes in its worktree
  asks whether to keep the worktree before the shell returns.

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
  with `-w` watches for that line and may retype the command with the
  flag removed.
- The CLI re-enters an existing worktree whenever its directory exists on
  resume, and hands a new `-w` session an unchanged worktree left by an
  earlier one rather than making another.

### I. `/model` and `/effort`

*Confirmed on 2.1.316 (the records and the program's text).*

Typing `/model <id>` or `/effort <level>` into the box and submitting it
changes the session's model or effort without a reply from the model. Each
leaves two `user` records: the command, then its output. The output for
the model opens `Set model to <display name>` (the display name, such as
`Opus 5.5`, not the id, so the id is read from the command's argument
until the next reply's record names it) and continues either
`and saved as your default for new sessions` or `for this session only`;
a `/model` that changes nothing opens `Kept model as`. The output for the
effort opens `Set effort level to <level>` and continues
`(saved as your default for new sessions)` or `(this session only)`,
possibly with more after it. A switch the CLI did not accept prints
another message and leaves both unchanged.

In the saved form the command also **writes the person's defaults** into
`~/.claude/settings.json` (the top-level `model`; the effort under
`modelSettings`); which form a session gets is the CLI's choice. A server
that posts the commands does so on a person's request, never on its own,
since it may be changing their configuration.

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
`attach` takes (an element on earlier versions carried a `state` too, not
seen on 2.1.316). `--all` includes completed ones. The listing is how a
server tells a session that has become a background agent from one that
has not, and, apart from the agent's spinner, the only busy reading a
server has for one, since the daemon scrubs its agents' environment and
they report no progress.

### L. Folder trust

*Confirmed on 2.1.316.*

The CLI asks `Quick safety check: Is this a project you created or one you trust?`
before its first interactive session in a directory, and records the answer in
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
