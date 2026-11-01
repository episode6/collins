<!--
SPDX-License-Identifier: MIT
Copyright (c) 2026 episode6, Inc.

This document is licensed MIT, not GPL-3.0 like the rest of Collins: see the
License section of index.md.
-->

# The terminal contract

A Level 1 server of [the service API](./) holds every pty: it spawns the
child, reads what the child writes, and shows it to the clients that attach.
This page is what the server has to do to that stream, and what a client
may rely on: the terminal queries it answers in the child's place, the redraw
it paints an attaching client with, the screen model its session facts are
read from, the client whose size the pty takes, and the environment the child
starts in. The [message reference](./messages) has the fields of every
message named here; [index.md](./) has the frames, the flow control and a
client's life. The session facts themselves (`takes_prompt`, `busy`, the
model and effort read off a transcript) are the sessions page's, which
follows this one.

**Normative and informative.** The sections marked *normative* are what a
client, or Claude Code inside the pty, can observe: a server that differs
there will see the window or the CLI behave differently. The sections marked
*informative* describe how Collins' own service does it; they are published so
an implementer can match them, and are not a promise. Where this page says
MUST, SHOULD or MAY, it means them as RFC 2119 does.

**Where the numbers come from.** Every reply on this page was measured against
a real `Vte.Terminal` of VTE 0.84.0, the terminal widget the Collins window
draws with, at 120 columns by 40 rows with VTE's own colours. Collins' own CI
re-measures the table on every run, so what this page says is what VTE
answered on the day. The page quotes no code, VTE's or Collins': it is written
from a specification of the behaviour and from measurement.

## Notation

An escape sequence is written as a row of tokens separated by spaces. A token
is one of the names below, or a run of ASCII characters taken literally and
joined to its neighbours with nothing in between. A token never contains a
space; the name `SP` is a space.

| Token | Bytes |
| --- | --- |
| `ESC` | `0x1B` |
| `CSI` | `ESC [` |
| `OSC` | `ESC ]` |
| `DCS` | `ESC P` |
| `APC` | `ESC _` |
| `ST` | `ESC \` |
| `BEL` | `0x07` |
| `ENQ` | `0x05` |
| `SP` | `0x20` |

So `CSI ?61;1;21;22;28c` is the seventeen bytes `ESC [ ? 6 1 ; 1 ; 2 1 ; 2 2
; 2 8 c`, and `DCS !|7E565445 ST` is `ESC P ! | 7 E 5 6 5 4 4 5 ESC \` (a
pipe is escaped with a backslash inside a table cell, as Markdown wants it;
read it as a pipe). A placeholder in
angle brackets inside a token, such as `<row>`, stands for a decimal number
or a hex string the row explains. Collins' conformance check parses the
tables of this page in exactly this notation and feeds each row to VTE, so
the notation is also a file format.

<!-- Maintainers: scripts/termcontract.py reads the three tables below by
their headings. Keep each query and reply in a single code span of its own
cell, keep the headings as they are, and start every table line with a
pipe; the notes column is not checked. -->

## The filtered stream (normative)

A pty's output does not reach a client raw. The server reads it, **answers
the queries in the table below on the child's behalf and does not forward
them**, strips the queries in the silent list without answering, and forwards
everything else to every attached client **unchanged**, byte for byte. A
client's terminal therefore never sees a query the server answered, and the
child is never left waiting for a client: a program that asks the terminal
who it is gets an answer whether or not anyone is attached, and so a session
can start and run unwatched.

**Replies** go to the pty in query order, each ahead of any typing the
server has queued for that pty: a program
that asks three questions and then reads reads the three answers first, and
in order.

**The pass-through rule.** A sequence whose parameters are not those of a row
in the two tables is forwarded, unanswered, and the attached client's terminal
answers it if it wants to. With nobody attached it goes unanswered, and with
two clients attached it is answered twice, so a program should not rely on
such an answer. Passed through in particular: any query with a parameter the tables
do not show (`CSI 1 c`, `CSI ? 15 n`, `CSI 19 t`); a DECRQM whose parameter is
absent or above 65535; a colour query (`OSC 4`, `OSC 10`, `OSC 11`,
`OSC 12`) for a colour the program itself has set, and an `OSC 12` query
while the program has set the foreground with `OSC 10`, since the cursor
colour is read from the foreground (the client's terminal knows that colour,
the server does not; the server answers again once the matching `OSC 104`,
`OSC 110`, `OSC 111` or `OSC 112`, or `RIS`, has reset it); an `OSC 4` query for an index over 255; a DECRQSS for anything but the
SGR state; an XTGETTCAP for any name but `TN`; a string cut short or
oversized (below). The server does **not** rewrite, reorder or coalesce what
it forwards.

**Forwarded and also read.** Some of what passes through unchanged the server
reads for itself: OSC 9;4 progress (`OSC 9 ; 4 ; <state> ; <percent>`
ended by `BEL` or `ST`; states 0 to 4, the percentage clamped to 0–100, a
bare `9 ; 4` read as state 0 and a state over 4 not read at all; for a state
with no percentage Claude Code writes the percent field present and empty,
`9;4;3;` and `9;4;0;`, in the 2.1.288 recordings published with these pages
and on 2.1.316 alike, and a reader accepts a missing field too),
`BEL`, the `OSC 0` and `OSC 2` titles,
every mode set and reset and the alternate-screen switch. They are how the
server knows the agent is working and keeps its mode tracker current, and the
client sees them too. None of them is stripped, OSC 9;4 included.

### Queries the server answers

Each reply is what VTE 0.84.0 answers to the query on a fresh terminal of
120×40. A query is matched on its parameters' values, not their spelling:
`CSI c`, `CSI 0 c` and `CSI 00 c` are the same query, and `CSI 1 c` is not a
query in this table.

| Query | Reply | Notes |
| --- | --- | --- |
| `CSI c` | `CSI ?61;1;21;22;28c` | DA1, primary device attributes. |
| `CSI 0 c` | `CSI ?61;1;21;22;28c` | The same with an explicit 0. |
| `CSI > c` | `CSI >61;<vte>;1c` | DA2. `<vte>` is the pty's version (VTE's as one number: 0.84.0 is `8400`): the most recent `vte` any `term` the colour queries follow has brought to this pty; before any did, the `vte` the stand-in had when the pty was spawned, or `8400` (below). |
| `CSI > 0 c` | `CSI >61;<vte>;1c` | The same with an explicit 0. |
| `CSI = c` | `DCS !\|7E565445 ST` | DA3, the unit id: the hex of the ASCII `~VTE`. |
| `CSI = 0 c` | `DCS !\|7E565445 ST` | The same with an explicit 0. |
| `CSI > q` | `DCS >\|VTE(<vte>) ST` | XTVERSION. `<vte>` as for DA2. |
| `CSI > 0 q` | `DCS >\|VTE(<vte>) ST` | The same with an explicit 0. |
| `CSI 5 n` | `CSI 0n` | DSR 5, device status: always OK. |
| `CSI 6 n` | `CSI <row>;<col>R` | CPR. 1-based, from the server's screen model and clamped to the grid; measured from the scroll region's top row when origin mode is on; and the last column rather than the one beyond it when a wrap is pending. |
| `CSI ? 6 n` | `CSI ?<row>;<col>;1R` | DECXCPR: the same position, page 1. |
| `CSI ? 996 n` | `CSI ?997;<scheme>n` | The colour-scheme report: `<scheme>` is `1` for dark, `2` for light, from the `term.scheme` of the client the colour queries follow; with no `scheme`, from that client's `term.bg`: `2` when the weighted sum 0.2126 R + 0.7152 G + 0.0722 B of its channels, each as a fraction of full and taken as sent (not gamma-decoded), is one half or more, else `1` (`#808080` answers `2`); with neither, `1`. |
| `CSI ? <n> $ p` | `CSI ?<n>;<s>$y` | DECRQM, a DEC private mode. `<s>` is `1` set, `2` reset, `3` permanently set, `4` permanently reset, `0` for a mode VTE does not know; from the server's mode tracker over VTE's mode table. `<n>` is 0 to 65535. |
| `CSI <n> $ p` | `CSI <n>;<s>$y` | DECRQM, an ANSI mode, the same states. |
| `CSI 18 t` | `CSI 8;<rows>;<cols>t` | The grid in cells, the pty's size. |
| `CSI 14 t` | `CSI 4;<height>;<width>t` | The grid in pixels. VTE's own answer follows its font's cell; a server has no font and reports a stand-in cell of its own, Collins' being 9 wide by 18 high: `<height>` is rows × the cell height, `<width>` cols × the cell width. |
| `CSI 21 t` | `OSC l ST` | The title report: always an empty title. |
| `OSC 10 ; ? ST` | `OSC 10;rgb:<rrrr>/<gggg>/<bbbb> ST` | The foreground, as 16-bit hex per channel (`c0c0` for `#c0`: the channel times 257), from the `term.fg` of the client the colour queries follow (below), else VTE's default. A query ended by `BEL` instead is answered ended by `BEL`. |
| `OSC 11 ; ? ST` | `OSC 11;rgb:<rrrr>/<gggg>/<bbbb> ST` | The background, from `term.bg` likewise; a query ended by `BEL` is answered ended by `BEL`, as for `OSC 10`. |
| `OSC 12 ; ? ST` | `OSC 12;rgb:<rrrr>/<gggg>/<bbbb> ST` | The cursor colour. The API carries no cursor colour, so it is the foreground, as VTE answers with no cursor colour set. The query's terminator is echoed, as for `OSC 10`. |
| `OSC 4 ; <i> ; ? ST` | `OSC 4;<i>;rgb:<rrrr>/<gggg>/<bbbb> ST` | Palette entry `<i>`, 0 to 255, always VTE 0.84's default palette: a client's own palette is not consulted. Several `<i> ; ?` pairs in one query get one reply each, in order; the query's own terminator is used. |
| `DCS +q544e ST` | `DCS 1+r544e=787465726D2D323536636F6C6F72 ST` | XTGETTCAP for `TN`: the terminal name, `xterm-256color` as hex. The name's hex is echoed in the case it was asked (`544E` answered `544E`). |
| `DCS $qm ST` | `DCS 1$r<sgr>m ST` | DECRQSS for the SGR state: the server's screen model's current pen. `<sgr>` is `0` followed by one `;`-separated parameter per attribute in VTE's order: `1` bold, `2` faint, `3` italic, `4` underline (`4:3` curly, `4:4` dotted, `4:5` dashed, `21` double), `5` blink, `7` inverse, `8` hidden, `9` strikethrough, `53` overline; then the foreground as it was set: `30` to `37` or `90` to `97` for a named colour, `38:5:` and the index for a palette index (an index under 16 set as `38;5;<i>` is reported as `38:5:<i>`, not as the named colour), or `38:2::` and three channels; the background likewise with `40` to `47`, `100` to `107`, `48:5:`, `48:2::`; then an underline colour set from the palette, as `58:5:` and the index, reported even with no underline in force; a direct underline colour (`58:2::`) is not reported. A plain pen is `DCS 1$r0m ST`. Collins' own answer differs from VTE's in one case, named under [How Collins does it](#how-collins-does-it-informative). |

### Queries stripped without an answer

VTE answers nothing to these. The server strips them so that a client's
terminal, which may answer, does not, and answers nothing itself.

| Query | What it is |
| --- | --- |
| `CSI ? u` | The kitty keyboard protocol's flags query. |
| `CSI 16 t` | The cell size in pixels. |
| `CSI ? 4 m` | XTQMODKEYS, the modifyOtherKeys query. |
| `ENQ` | The answerback request. |
| `APC Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA ST` | A kitty graphics command whose action is a query (`a=q`): this one and every other `APC G` with `a=q`. A graphics command with another action is forwarded. |

### Passed through, for example

Each of these reaches the client unchanged and is answered by nobody on the
server. VTE answers several of them itself; the server does not, because they
are not of the table's exact shape or ask about something only a client
knows.

| Sequence | Why it passes |
| --- | --- |
| `CSI 1 c` | DA1 with a parameter other than 0. |
| `CSI ? 15 n` | A DSR the table does not list. |
| `CSI 11 t` | A window operation the table does not list; so `CSI 13 t`, `CSI 19 t`, `CSI 20 t`. |
| `CSI ? $ p` | DECRQM with an empty parameter. |
| `CSI ? 65536 $ p` | DECRQM with a mode over 65535. |
| `OSC 17 ; ? ST` | A colour the table does not list (the selection), so `OSC 5`, `OSC 13`, `OSC 19`. |
| `OSC 4 ; 256 ; ? ST` | A palette index over 255. |
| `OSC 10 ; #ff0000 ST OSC 10 ; ? ST` | The program set this colour, so its query passes along with the set. |
| `DCS $qr ST` | DECRQSS for anything but `m`. |
| `DCS +q436f ST` | XTGETTCAP for a name other than `TN`. |
| `OSC 9 ; 4 ; 1 ; 50 ST` | Progress: forwarded unchanged, and read by the server. |
| `OSC 9 ; 4 ; 3 ; ST` | Progress with an empty percent field, as Claude Code writes a state with no percentage: forwarded unchanged, and read as state 3. |

### Tokenizing the stream (normative where it meets the tables)

A query is recognised only when the server's tokenizer cuts it as one
sequence. These rules are normative where they decide whether a query of the
tables is recognised: a server that cuts the stream otherwise answers a query
VTE would not, or misses one VTE would answer. What becomes of the bytes of a
sequence that is no query is not a rule of its own: they are forwarded
unchanged, like everything else.

- A sequence or a UTF-8 character that a read cuts in two is completed by
  the next read and recognised whole.
- An OSC ends at `BEL` or `ST`; the other strings (DCS, APC, PM, SOS) end
  at `ST` alone.
- An `ESC` arriving inside a CSI sequence or a string ends that sequence
  unfinished and starts the next one: in `OSC 10 ; ? ESC [ 6 n` the OSC is
  abandoned, its bytes forwarded like any other non-query, and the CPR query
  that follows is answered.
- A CSI sequence in which a parameter byte follows an intermediate one is
  malformed: `CSI $ 6 n` is no query.
- A string that has reached 64 KiB without its terminator is abandoned at
  that point: what has arrived and what follows up to the terminator are
  forwarded in pieces, and the server makes nothing of any of it, neither a
  query nor text for the screen model.
- `CAN` (`0x18`) and `SUB` (`0x1A`) abort the sequence they arrive in.
- A C0 control arriving inside a CSI or ESC sequence takes effect where it
  arrives, and the sequence resumes after it.
- A C1 control begins no sequence here, whether it arrives as a raw byte
  (`0x80`–`0x9F`) or as the UTF-8 encoding of `U+0080`–`U+009F`: the server
  treats both as text. VTE differs on the UTF-8 encodings, which it executes
  (`U+009B 6 n` is a CPR query to VTE and not to the server), and agrees on
  the raw bytes, which it executes no more than the server does. None of the
  recorded Claude Code streams published with these pages contains one.
- A CSI or ESC sequence longer than 4096 bytes is malformed and is never a
  query.

## The reply bytes are VTE's (normative)

The replies in the table SHOULD be exactly those bytes. A server that
answers in another terminal's voice (another DA1, another XTVERSION, a
different DECRQM table) is conformant at the wire, since no client sees the
replies, but Claude Code will behave differently inside it: its colour-scheme
detection reads `CSI ? 996 n`, its keyboard handling probes the kitty
protocol, and whether it emits OSC 9;4 progress at all depends on the
terminal it believes it is in (see [the spawn
environment](#the-spawn-environment-normative)). The session facts of the
sessions page assume the CLI behaved as it does under VTE. The DECRQM mode
table, VTE 0.84's answer for every private and ANSI mode in its default
state, ships as data with the conformance kit; until then the check that
re-measures it is Collins' own.

## An attach's redraw (normative)

`attach {pty, cols, rows}` is answered with the grid the redraw is painted at
(`cols`, `rows`), whether this client is the pty's active client (`active`),
the device of the client the pty is sized for (`sized_for`: its own when it
is active), and `modes`: the modes the redraw sets away from a fresh
terminal's defaults, on or off, each as the bytes between `CSI` and the end
of its sequence as the redraw asserts it, such as `"?1004h"`, `"?2004h"`, `"?7l"`,
`">5u"`. Four are left out of the list because the redraw handles them
itself: `?3`, `?25`, `?1048` and the alternate-screen modes. The redraw
follows as output frames flagged `0x01` (redraw), the last also `0x02`
(redraw end), and live output follows that.

**What the redraw is.** A complete repaint of a *fresh* terminal of the
reply's `cols` × `rows`, made of ordinary escape sequences. A client MUST
reset its terminal, scrollback included, before feeding the first redraw
frame, and feed the frames through the one flagged redraw end: the redraw
carries the scrollback the server has, and a client that kept its own would
show it twice. When the last frame has been fed, the
client's terminal shows what the server's screen model shows: every cell of
the screen, where the cursor is and whether it shows, the pen in force, the
scroll region, the tab stops, which character sets are designated and
shifted in, the origin, autowrap and insert settings, the saved cursor, and
which screen (main or alternate) is showing. Apart from the four it leaves out, `modes` is exactly the set of modes
the redraw moves off a fresh terminal's defaults, so a client that keeps its
own idea of a mode (focus reporting, bracketed paste, the kitty keyboard
flags) reads the list rather than the frames, and reads whether the alternate
screen is up from its terminal. When the list holds `?1004h` the client tells
the pty its real focus state after the redraw, as an input frame of `CSI I`
(focused) or `CSI O` (not), since the pty has been told to expect focus
reports and the redraw cannot know whether the client is focused. It does
not send `focus` for this: that message takes the active role (below), and
an attach is not a claim to it.

**A redraw can arrive unasked.** A client the server had to cut off, with
4 MiB of output unacked, is sent a fresh redraw with no reply and no `modes`
list, under the same rules: the same reset first, the same guard on what the
terminal says back, and live output after the frame flagged redraw end. A
client keeps the last `modes` it was given.

**Scrollback MAY be partial; the screen MUST be whole.** A server with more
scrollback than it will send sends the newest rows and drops the oldest, and
the screen is always complete. Collins bounds the redraw at 2 MiB.

**Nothing the client's terminal says in answer to the redraw may reach the
pty.** A redraw is fed to a real terminal, and a real terminal answers what
looks like a query (a `CSI 6 n` the server used to place something, a mode
report). A client MUST drop what its terminal emits in reaction to the redraw
rather than send it as input. The Collins window does it with a sentinel: it
feeds `OSC 4 ; <n> ; ? ST` to its own terminal right after the frame flagged
redraw end and drops every commit until the terminal's answer to that
sentinel arrives, or 2 seconds pass. Any method that holds the input back
until the redraw has been digested is fine.

**Size.** The redraw is painted at the pty's grid, which the attaching client
asked for only if it is the active client: an attaching client whose
`cols` × `rows` differ from the pty's is resized to them *before* the redraw
when it is active, and redrawn at the pty's grid when it is not, with
`sized_for` naming whose grid that is. See [the active
client](#the-active-client-normative).

## The screen model of record (normative)

The server keeps a model of each pty's terminal, and that model, not any
client's terminal, is what every automated reading is made of:
`session.takes_prompt` (whether the CLI's input box is empty), the
`read_terminal` session tool a running agent calls, and `pty.capture`. Two
clients attached to one pty, or none, read the same facts.

**`pty.capture`** answers `text`: the scrollback followed by the screen, as
plain text with no escape sequences, under these rules:

- rows are joined with `\n`; a row that soft-wrapped at the margin is joined
  to its continuation with no `\n` between them, so a wrapped line reads as
  one line;
- a never-written cell reads as a space, a space the program wrote stays a
  space, and a tab the program wrote is a tab character; at a row's end the
  never-written cells and the cells a background erase left are dropped,
  while spaces the program wrote stay (the comparison in the fixtures
  normalises further, trimming every row's trailing blanks: that is how two
  models are compared, not what either answers);
- the trailing empty rows are dropped;
- rows that `CSI 3 J` blanked are skipped, as the terminal would skip them
  (VTE's own save-contents does);
- the alternate screen is captured only while it is up, with no scrollback;
- a pty that is gone answers `""`;
- the text is bounded at the protocol's text bound (1 MiB of characters);
  which end of a longer text is kept is the server's choice (Collins keeps
  the start: see below).

**Painted text.** `paint {pty, text}` inserts text into the output stream of
the pty on the far side of the filter, where the child's own output lands
once the server has looked at it: the model takes it in, and every attached
client gets it as output frames. Being past the filter, painted text is
never inspected: a query in it is answered by nobody and goes out to the
clients untouched, and a mode set in it reaches neither the mode tracker nor
`modes`. The pty itself is not written, so the
child never sees it. It is how a client puts a line of its
own in front of everyone watching the pty (Collins' session-manager notes in
an agent tab, a panel shell's saved scrollback painted back on reopen), and a
server serves it at Level 1.

## The active client (normative)

Several clients may attach to one pty. One of them is **active**: the pty is
sized to it, and the `tool` events a running agent raises go to it.

- The role belongs to whichever client **typed** into the pty most recently
  (an input
  frame with anything in it but pure focus reports `CSI I` / `CSI O` and
  mouse reports) or that sent `focus {focused: true}`. A client attaching
  while no attached client holds the role takes it, and its attach reply says
  `active: true`; after that, a client that only watches never becomes
  active by watching. A client that is not attached takes no role by typing:
  its input is written and the active client stays.
- On the active client's `detach`, the role passes to the attached client
  that acted most recently, by the order of its last action and attach.
- Only the active client's `resize` changes the pty's grid at once. Another
  client's `resize`, like the grid its `attach` asked for, is accepted and
  remembered: meanwhile it sees the pty at the active client's grid and is
  told whose in the attach reply's `sized_for`, and its grid is applied the
  moment it becomes active. A server that forgets it leaves that client at
  the other's grid when the role passes, and the client has no reason to
  resend a grid it already sent.
- Every attached client is sent a `pty` event with `cols`, `rows`, `active`
  and `sized_for` whenever the pty's grid or the role changes. It is how a
  client learns that it lost or gained the role or that the grid moved; the
  attach reply speaks only at attach.
- An attaching client that is active and asks for a different grid has the
  pty resized to it before its redraw.
- **The colour queries follow the active client.** `OSC 10`, `OSC 11`,
  `OSC 12`, `CSI ? 996 n`, DA2 and XTVERSION are answered from one `term`
  (`fg`, `bg`, `scheme`, `vte`). With an active client on the pty whose
  `term` is not empty, that `term` is the active client's own: the one in
  its hello, superseded by every `theme` it sends afterwards. With no active
  client, or an active one whose `term` is empty, a **stand-in** `term`
  answers: the most recent `theme` that reached the server from any client
  on any pty, or, before any has, what VTE itself would say (VTE's default
  foreground and background, and dark). Only a `theme` updates the
  stand-in, never a hello, so until some client has sent one, a pty with no
  active client answers with VTE's defaults; the Collins window therefore
  sends `theme` with every attach. **The version sticks where the colours
  do not.** A `term` without a `vte` field changes nothing about the
  version a pty reports, which therefore stays whatever the most recent
  `term` with one set, or what the stand-in held when the pty was spawned,
  or `8400`: after the client that brought `7000` detaches, DA2 still
  says `7000` while the colour queries go back to the stand-in's. A pty's
  colours are answered for its active client, not for a client that merely
  watches it, and attaching to a pty does not change the colours it is
  answered with until the attaching client becomes active.
- Two views of one pty in one client share one attachment on the server:
  one ack counter, one role. A second `attach` from the same client is
  answered with another redraw and takes the new grid as a first attach
  would (applied at once when the client is active, remembered otherwise);
  it also makes that client the most recent in the order the role passes
  by, and the output queued for it before the redraw is dropped.
- **Input frames are written whether or not the sender is attached.** An
  input frame for a pty the client has not attached, or has detached from,
  is written to the pty all the same; `attach` is about output, not input.
- Flow control is as [the frames section](./#flow-control) says: an attached
  client acks the live bytes it has fed, the server holds output at 1 MiB
  unacked and redraws a client it had to cut off at 4 MiB, and redraw frames
  are neither counted nor acked.

## The spawn environment (normative)

The child of an agent pty sees these variables set by the server, over
whatever else the server gives it:

| Variable | Value |
| --- | --- |
| `TERM` | `xterm-256color` |
| `COLORTERM` | `truecolor` |
| `VTE_VERSION` | The `vte` of the stand-in `term` (above) as it stood when the pty was spawned, else `8400`. The client that asked for the spawn is not consulted, and a `theme` sent later alters DA2's answer while the child's environment keeps the value it started with. |
| `ConEmuANSI` | `ON`, for an agent pty while the `progress_termprop` service setting is on (its default). |
| `TERM_PROGRAM` | `kitty`, for an agent pty, under the same setting. |

The last two are spoofs: Claude Code emits OSC 9;4 progress only in a
terminal it believes shows it, and terminates it with `ST` rather than `BEL`
only when it believes the terminal is kitty. Together with the query replies
above they are what makes the CLI report its progress, which the busy and
finished facts of the sessions page are read from. They are normative for
that reason, and for that reason only: a server with another way to know the
agent is working MAY leave them out and SHOULD say so in its own
documentation, since a client's `busy` and `finished` are its reading. A
server that does set them honours the setting: a client that turns
`progress_termprop` off asks for a session without the spoofs, and the
change reaches the sessions spawned after it.

A shell pty (`spawn {kind: "shell"}`) gets the first three and not the
spoofs.

## How Collins does it (informative)

What follows is Collins' own implementation, described so a server can match
it where matching is cheaper than deciding. None of it is a promise, and a
server that differs here is conformant.

**Starting the agent.** Collins starts the user's login shell (`$SHELL`,
else `/bin/bash`) on the pty and *types* the `claude …` command into it,
newline included, so the first thing a client sees is a shell prompt and the
echoed command. The server assembles that line itself from its
own `claude` installation and the request's fields; no client sends a
command. Once the CLI has exited, the shell is still there, but the row
stops being a running session (`item.running` turns off); the pty lives on
until the shell exits or `close {mode: "kill"}`. The child's environment
is the login shell's laid over the server's own, with a few of the server's
private variables removed, the table above added, and the `--mcp-config`
the session tools ride on.

**What a repaint must get right, and why.** Any server has to turn its
model back into a stream that a real terminal will read into the same
state, and the terminal's own rules decide much of what that stream can
look like. The constraints below are the terminal's, each with the fact
behind it; how a server orders its work within them is its own affair, and
the kit's scenarios judge the result, not the method.

- **History is filled from the screen.** A terminal's scrollback holds the
  rows that left its screen, scrolled off the top while text was being
  written or pushed there by `CSI 2 J`; nothing can be placed into history
  directly. So the scrollback has to be written
  as ordinary rows and scrolled off before the screen is painted, and a
  repaint that wants an exact screen afterwards has to leave the cursor
  somewhere it knows.
- **A soft wrap is made by writing through the margin.** A terminal marks a
  row as continued only when text runs past its right edge into the next
  row. A row that wrapped in the model must therefore be written without a
  line ending and allowed to wrap, and a row that did not must be ended
  before anything runs past the margin (a full row that stops at it and
  then ends is not a wrap); otherwise `pty.capture`'s joins and a later resize
  differ between the two sides. For the same reason a pending wrap (the
  cursor held on the last column with that cell written) exists only once a
  character has landed in the last column, so a model that holds one
  reproduces it by writing that one cell last, and once.
- **Erased is not never-written, and neither is a space.** A terminal
  remembers whether a cell was touched: a cell erased under a background
  colour carries that colour and counts towards the row's length, a cell
  nothing ever wrote does not, and a written space is text. The two
  readings treat them differently: VTE's own text extraction counts the
  erased cells in a row's text, while `pty.capture` drops them at a row's
  end as the normative half says, and written spaces stay in both. So a
  repaint reproduces erased cells by erasing (`CSI <n> X`, `CSI K`) with
  the background set, not by writing spaces, which would turn them into
  text in both readings, and not by skipping them, and leaves never-written
  cells alone.
- **A tab is a tab only while its stop exists.** The cells a tab skipped can
  be reproduced only by writing a tab with the same stops in place, so
  stops that differ from the default go in before any content that relies
  on them; a terminal's capture then reads the cells as a tab (`d\te`).
- **Modes that change how writing lands stay off while painting.** Insert
  mode, autowrap off, origin mode and a scroll region all alter where
  written cells go (a region scrolls content written inside it; origin mode
  changes what row 1 means; a region or origin change also homes the
  cursor). They are asserted after the cells are in place, and the final
  cursor position is placed after them.
- **The alternate screen saves the cursor it finds.** `CSI ? 1049 h` saves
  the cursor's position as it stands at the switch, and that saved position
  is the one `CSI ? 1049 l` restores (it replaces what an earlier `ESC 7`
  saved, as `ESC 8` then shows). So the main screen has to be complete
  before the switch, the cursor must be standing where the program will
  expect it back when it leaves the alternate screen, and what is written
  after the switch is the alternate screen's content.
- **What the last cell left behind is not the state.** Writing the cells
  leaves the terminal with the last run's pen and the cursor after the last
  cell, so the model's current pen and cursor are set after all content. A
  hidden cursor is not such a case: `CSI ? 25 l` stays in force through
  text, SGR, cursor moves and mode changes, so it may be asserted at any
  point.
- **The kitty keyboard flags are a stack.** `CSI > <flags> u` pushes and
  `CSI = <flags> u` sets the base, so the same two sequences in the other
  order leave a different stack (`>5u` then `=3u` reads back as `>3u`,
  the reverse as `=3u`, `>5u`). A repaint asserts the base first and the
  pushed entries in stack order; the other modes may be asserted in any
  order, since each sets its own state.

Collins bounds a redraw at 2 MiB. When the scrollback does not fit, it
leaves out the oldest rows: the screen is what the person is looking at,
and the newest history is nearest to it. Three things Collins' redraw does
not carry: a tab whose stop has since moved, a soft wrap on a row with no
character to write through the margin, and OSC 8 hyperlinks, which its
model does not keep.

**The state a model has to hold.** The normative half and the kit's
scenarios together say what a server's model must be able to answer, and the
published scenarios are the test of it. Grouped by what each decides, a
model that passes them holds, for each pty: what `pty.capture` and the box
readings are made of (the cells of the main and the alternate screen, each
with its text, its width and the pen it was written with, the touched,
erased and tab-skipped distinctions above among them; the main screen's
scrollback, the alternate screen having none); what DECRQSS and the cursor
reports answer from (the current pen: bold, faint, italic, underline and
its style, blink, inverse, hidden, strikethrough, overline, foreground,
background, underline colour; the cursor and its pending wrap; a saved
cursor); and what decides where the next write lands (the origin, autowrap
and insert settings; the tab stops; the designated character sets and the
shift; **a scroll region that is the same on either screen**, measured on
VTE: set on one screen, it is found set on the other). VTE's reading of the
operations that move rows between screen and history is recorded in the
scenarios (`CSI 2 J` pushes the screen's used rows into history, `CSI 3 J`
blanks the history's rows but keeps their count; which rows count after a
cursor move, an erase, an insert, a resize or a trip through the alternate
screen), as is its verdict on the pending wrap for every operation that can
touch it (the `pending-*` cases: the erase, insert and delete sequences,
cursor movement, `SGR`, `DECSC`/`DECRC`, the C0 controls, the
alternate-screen switch, `REP`), with `CSI 6 n` reporting the last column
for as long as the wrap is pending. Collins' own choices, which a server
need not share: the main screen's history capped at 10 000 rows; no reflow on resize
(a row is cut or padded to the new width, never re-wrapped); character
widths that follow VTE 0.84 where it disagrees with Unicode's East Asian
Width tables for a few hundred code points, carried as a table the kit's
screens data exercises where the scenarios touch it.

**What DECRQM and a redraw need to know.** Beyond the cells, a server has
to answer DECRQM for any mode and tell an attaching client the modes in
force, so it keeps the state of every private (DEC) and ANSI mode a program
has set or reset since the last reset, the kitty keyboard flags (the base
value and the pushed entries, which Collins caps at 32), the modifyOtherKeys
level, and whether the cursor is hidden; a redraw also needs the scroll
region, the character-set designations and shift, and whether the
alternate screen is up (Collins also records which of modes 47, 1047 and
1049 entered it). `RIS` clears all of this. `DECSTR` on VTE resets the
modes to their defaults and forgets the region and the designations while
leaving the cells alone, and a server's tracker has to agree, since DECRQM
answers afterwards are measured against VTE's. The attach reply's `modes`
is the list of modes the redraw asserts from this state, as described under
[the redraw](#an-attach-s-redraw-normative); in what order a redraw asserts
them does not matter to the client's terminal, which ends in the same state
either way, except for the kitty keyboard flags, whose base must come
before the pushed entries, as noted above.

**What `pty.capture` and the facts share.** The capture is the model's
text as above; `takes_prompt` and the other box readings are made of the
same cells, so a server that passes the kit's screen comparisons gives the
same facts for the same bytes.

**Which end of a long capture Collins keeps.** When the text exceeds 1 MiB
of characters, Collins keeps its beginning and leaves out the newest rows, so a
very long scrollback reads from its beginning. Only the bound is normative;
a server that keeps the tail is conformant.

**One known divergence from VTE, in DECRQSS.** In Collins' pen, a
foreground or background chosen as a palette index `<i>` below 16
(`38;5;<i>`, `48;5;<i>`) is stored no differently from the same colour
chosen by name (`30`–`37` and `90`–`97`; `40`–`47` and `100`–`107`), so
Collins reports the name where VTE reports `38:5:<i>` / `48:5:<i>`. Indexes 16 and up agree, as does every
other attribute. The answers check sweeps the pens and lists this as its
one exception; Claude Code is not known to read this reply.

**Measurements, published.** The fixtures Collins' own screen model is
held to are MIT and travel with these pages: four streams recorded from
Claude Code 2.1.288 sessions, the byte offsets at which their scenarios end,
and for each offset a golden of what VTE 0.84 displayed there (row text,
cursor, the colour and attributes of every drawn cell, which rows
soft-wrapped, the scrollback, and the input-box readings), and the generator of 135 synthetic scenarios with its
golden. The conformance kit, when it ships, carries the same scenarios as
expected capture text and cursor per scenario, so an implementer compares
a model against VTE's reading without VTE.
