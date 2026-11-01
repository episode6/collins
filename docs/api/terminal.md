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
answered on the day. Nothing here is taken from VTE's source, and nothing is
taken from Collins' GPL code: the page is written from the behaviour.

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

So `CSI ?61;1;21;22;28c` is the sixteen bytes `ESC [ ? 6 1 ; 1 ; 2 1 ; 2 2
; 2 8 c`, and `DCS !|7E565445 ST` is `ESC P ! | 7 E 5 6 5 4 4 5 ESC \` (a
pipe is escaped with a backslash inside a table cell, as Markdown wants it;
read it as a pipe). A placeholder in
angle brackets inside a token, such as `<row>`, stands for a decimal number
or a hex string the row explains. Collins' conformance check parses the
tables of this page in exactly this notation and feeds each row to VTE, so
the notation is also a file format: keep each query and reply in a single
code span of its own cell, and keep the three table headings as they are.

## The filtered stream (normative)

A pty's output does not reach a client raw. The server reads it, **answers
the queries in the table below on the child's behalf and does not forward
them**, strips the queries in the silent list without answering, and forwards
everything else to every attached client **unchanged**, byte for byte. A
client's terminal therefore never sees a query the server answered, and the
child never waits on a client: a program that asks the terminal who it is
gets its answer whether a client is attached or not, which is what lets a
session start and run with nobody watching.

**Replies** are written to the pty in the order the queries appeared in the
stream, ahead of any typing the server has queued for that pty: a program
that asks three questions and then reads reads the three answers first, and
in order.

**The pass-through rule.** A sequence that is not *exactly* of a shape in the
two tables is forwarded, unanswered, and the attached client's terminal
answers it if it wants to: nobody answers it when no client is attached, and
two attached clients both answer, so a program should not rely on such an
answer. Passed through in particular: any query with a parameter the tables
do not show (`CSI 1 c`, `CSI ? 15 n`, `CSI 19 t`); a DECRQM whose parameter is
empty or over 65535; an OSC colour query for a colour the program has set
itself with `OSC 4`, `OSC 10`, `OSC 11` or `OSC 12` (the client's terminal
knows that colour, the server does not; the server answers again once the
matching `OSC 104`, `OSC 110`, `OSC 111` or `OSC 112`, or `RIS`, has reset
it); an `OSC 4` query for an index over 255; a DECRQSS for anything but the
SGR state; an XTGETTCAP for any name but `TN`; a string cut short or
oversized (below). The server does **not** rewrite, reorder or coalesce what
it forwards.

**Forwarded and also read.** Some of what passes through unchanged the server
reads for itself: OSC 9;4 progress (ended by `BEL` or `ST`; states 0 to 4,
the percentage clamped to 0–100), `BEL`, the `OSC 0` and `OSC 2` titles,
every mode set and reset and the alternate-screen switch. They are how the
server knows the agent is working and keeps its mode tracker current, and the
client sees them too. OSC 9;4 is not stripped.

### Queries the server answers

Each reply is what VTE 0.84.0 answers to the query on a fresh terminal of
120×40. A query is matched on its exact shape: `CSI c` and `CSI 0 c` are the
same query, and `CSI 1 c` is not a query in this table.

| Query | Reply | Notes |
| --- | --- | --- |
| `CSI c` | `CSI ?61;1;21;22;28c` | DA1, primary device attributes. |
| `CSI 0 c` | `CSI ?61;1;21;22;28c` | The same with an explicit 0. |
| `CSI > c` | `CSI >61;<vte>;1c` | DA2. `<vte>` is the `term.vte` of the client the colour queries follow (below; VTE's version as one number: 0.84.0 is `8400`), or `8400` when no client has said. |
| `CSI > 0 c` | `CSI >61;<vte>;1c` | The same with an explicit 0. |
| `CSI = c` | `DCS !\|7E565445 ST` | DA3, the unit id: the hex of the ASCII `~VTE`. |
| `CSI = 0 c` | `DCS !\|7E565445 ST` | The same with an explicit 0. |
| `CSI > q` | `DCS >\|VTE(<vte>) ST` | XTVERSION. `<vte>` as for DA2. |
| `CSI > 0 q` | `DCS >\|VTE(<vte>) ST` | The same with an explicit 0. |
| `CSI 5 n` | `CSI 0n` | DSR 5, device status: always OK. |
| `CSI 6 n` | `CSI <row>;<col>R` | CPR. 1-based, from the server's screen model, clamped to the grid; relative to the scroll region's top in origin mode; the last column, not one past it, while a wrap is pending. |
| `CSI ? 6 n` | `CSI ?<row>;<col>;1R` | DECXCPR: the same position, page 1. |
| `CSI ? 996 n` | `CSI ?997;<scheme>n` | The colour-scheme report: `<scheme>` is `1` for dark, `2` for light, from the `term.scheme` of the client the colour queries follow (dark when none has said). |
| `CSI ? <n> $ p` | `CSI ?<n>;<s>$y` | DECRQM, a DEC private mode. `<s>` is `1` set, `2` reset, `3` permanently set, `4` permanently reset, `0` for a mode VTE does not know; from the server's mode tracker over VTE's mode table. `<n>` is 0 to 65535. |
| `CSI <n> $ p` | `CSI <n>;<s>$y` | DECRQM, an ANSI mode, the same states. |
| `CSI 18 t` | `CSI 8;<rows>;<cols>t` | The grid in cells, the pty's size. |
| `CSI 14 t` | `CSI 4;<height>;<width>t` | The grid in pixels at a fixed cell of 9×18: `<height>` is rows × 18, `<width>` cols × 9. |
| `CSI 21 t` | `OSC l ST` | The title report: always an empty title. |
| `OSC 10 ; ? ST` | `OSC 10;rgb:<rrrr>/<gggg>/<bbbb> ST` | The foreground, as 16-bit hex per channel (`c000` for `#c0`), from the `term.fg` of the client the colour queries follow (below), else VTE's default. A query ended by `BEL` instead is answered ended by `BEL`. |
| `OSC 11 ; ? ST` | `OSC 11;rgb:<rrrr>/<gggg>/<bbbb> ST` | The background, from `term.bg` likewise. |
| `OSC 12 ; ? ST` | `OSC 12;rgb:<rrrr>/<gggg>/<bbbb> ST` | The cursor colour. The API carries no cursor colour, so it is the foreground, as VTE answers with no cursor colour set. |
| `OSC 4 ; <i> ; ? ST` | `OSC 4;<i>;rgb:<rrrr>/<gggg>/<bbbb> ST` | Palette entry `<i>`, 0 to 255, from VTE 0.84's built-in palette, never the client's. Several `<i> ; ?` pairs in one query get one reply each, in order; the query's own terminator is used. |
| `DCS +q544e ST` | `DCS 1+r544e=787465726D2D323536636F6C6F72 ST` | XTGETTCAP for `TN`: the terminal name, `xterm-256color` as hex. The name's hex is echoed in the case it was asked (`544E` answered `544E`). |
| `DCS $qm ST` | `DCS 1$r<sgr>m ST` | DECRQSS for the SGR state: the server's screen model's current pen. `<sgr>` is `0` followed by one `;`-separated parameter per attribute in VTE's order: `1` bold, `2` faint, `3` italic, `4` underline (`4:3` curly, `4:4` dotted, `4:5` dashed, `21` double), `5` blink, `7` inverse, `8` hidden, `9` strikethrough, `53` overline; then the foreground as `30` to `37`, `90` to `97`, `38:5:` and an index, or `38:2::` and three channels; the background likewise with `40` to `47`, `100` to `107`, `48:5:`, `48:2::`; then the underline colour as `58:5:` or `58:2::`, only while an underline is on. A plain pen is `DCS 1$r0m ST`. |

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
| `OSC 10 ; #ff0000 ST OSC 10 ; ? ST` | A query for a colour the program set itself; the set and the query both pass. |
| `DCS $qr ST` | DECRQSS for anything but `m`. |
| `DCS +q436f ST` | XTGETTCAP for a name other than `TN`. |
| `OSC 9 ; 4 ; 1 ; 50 ST` | Progress: forwarded unchanged, and read by the server. |

### Tokenizing the stream (normative where it meets the tables)

A query is recognised only when the server's tokenizer cuts it as one
sequence, so a replacement tokenizer must cut the stream the same way where
it matters:

- A sequence or a UTF-8 character split by a read is carried to the next read
  and recognised whole.
- `BEL` or `ST` ends an OSC; only `ST` ends a DCS, APC, PM or SOS string.
- A string still open at 64 KiB is flushed as text, and the rest of it is
  forwarded as it arrives: it is never a query.
- `CAN` (`0x18`) and `SUB` (`0x1A`) abort the sequence they arrive in.
- A C0 control inside a CSI or ESC sequence executes in place; the sequence
  continues after it.
- 8-bit C1 controls and their UTF-8 encodings (`U+0080`–`U+009F`) are text
  here, never the start of a sequence. This is a deliberate divergence from
  VTE, which executes them; Claude Code never sends one.
- A CSI or ESC sequence over 4096 bytes is bad: it is never recognised as a
  query. What becomes of its bytes is the server's business.

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
the active client's device when it is not (`sized_for`), and `modes`: the
mode re-assertions the redraw turns on, each as the bytes between `CSI` and
the end of its sequence, such as `"?1004h"`, `"?2004h"`, `">5u"`. The redraw
follows as `0x01` output frames flagged `0x01` (redraw), the last also `0x02`
(redraw end), and live output follows that.

**What the redraw is.** A complete repaint of a *fresh* terminal of the
reply's `cols` × `rows`, made of ordinary escape sequences. A client MUST
reset its terminal before feeding the first redraw frame and feed the frames
through the one flagged redraw end. When the last frame has been fed, the
client's terminal shows what the server's screen model shows: the screen's
cells, the cursor's position and visibility, the current pen, the scroll
region, the tab stops, the character-set designations and shift, origin,
autowrap and insert mode, the saved cursor, and whether the alternate screen
is up. The redraw turns on exactly the modes `modes` lists and no other
mode a fresh terminal has off; a client that keeps its own idea of a mode
(focus reporting, bracketed paste, the kitty keyboard flags) reads the list
rather than the frames. When `?1004h` is among them the client re-sends its
real focus state to the pty after the redraw, since the pty has been told to
expect focus reports and the redraw cannot know whether the client is
focused.

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
client's terminal, is what every automated reading is made of: `session.
takes_prompt` (whether the CLI's input box is empty), the `read_terminal`
session tool a running agent calls, and `pty.capture`. Two clients attached
to one pty, or none, read the same facts.

**`pty.capture`** answers `text`: the scrollback followed by the screen, as
plain text with no escape sequences, under these rules:

- rows are joined with `\n`; a row that soft-wrapped at the margin is joined
  to its continuation with no `\n` between them, so a wrapped line reads as
  one line;
- a never-written cell reads as a space, and each row's trailing blanks are
  dropped;
- the trailing empty rows are dropped;
- rows that `CSI 3 J` blanked are skipped, as the terminal would skip them
  (VTE's own save-contents does);
- the alternate screen is captured only while it is up, with no scrollback;
- a pty that is gone answers `""`;
- the text is truncated to the protocol's text bound (1 MiB of characters),
  oldest rows first.

## The active client (normative)

Several clients may attach to one pty. One of them is **active**: the pty is
sized to it, and the `tool` events a running agent raises go to it.

- The active client is the last one that **typed** into the pty (an input
  frame with anything in it but pure focus reports `CSI I` / `CSI O` and
  mouse reports) or that sent `focus {focused: true}`. A client that only
  watches never becomes active by watching.
- On the active client's `detach`, the role passes to the attached client
  that acted most recently, by the order of its last action and attach.
- Only the active client's `resize` is applied to the pty. Another client's
  `resize` is accepted and ignored; it sees the pty at the active client's
  grid and is told whose in the attach reply's `sized_for`.
- An attaching client that is active and asks for a different grid has the
  pty resized to it before its redraw.
- **The colour queries follow the active client.** `OSC 10`, `OSC 11`,
  `OSC 12`, `CSI ? 996 n`, DA2 and XTVERSION are answered from the active
  client's `term` (`fg`, `bg`, `scheme`, `vte`: the hello's, replaced by
  each `theme` it sends). While the active client has said nothing of its
  theme, the last `term` any client sent stands in. One client's theme never
  answers another client's pty. Attaching to a pty does not change the
  colours it is answered with until the attaching client becomes active.
- Two views of one pty in one client share one attachment on the server:
  one ack counter, one role, and a second `attach` from the same client is
  answered with another redraw and leaves the attachment as it was.
- **Input frames are written whether or not the sender is attached.** An
  input frame for a pty the client has not attached, or has detached from,
  is written to the pty all the same; `attach` is about output, not input.
  (This corrects what an earlier edition of these pages said, that such a
  frame is refused `sequence`: it never was.)
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
| `VTE_VERSION` | The spawning client's `term.vte` from its hello, else `8400`. |
| `ConEmuANSI` | `ON`, for an agent pty while the `progress_termprop` service setting is on (its default). |
| `TERM_PROGRAM` | `kitty`, for an agent pty, under the same setting. |

The last two are spoofs: Claude Code emits OSC 9;4 progress only in a
terminal it believes shows it, and terminates it with `ST` rather than `BEL`
only when it believes the terminal is kitty. Together with the query replies
above they are what makes the CLI report its progress, which the busy and
finished facts of the sessions page are read from. They are normative for
that reason, and for that reason only: a server with another way to know the
agent is working MAY leave them out and MUST then say so in its own
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
else `/bin/bash`) on the pty and *types* the `claude …` command line into it
with a trailing newline, so what a client sees first is a shell prompt and
the command being echoed. The server assembles that line itself from its
own `claude` installation and the request's fields; no client sends a
command. When the CLI exits the shell remains, and the row is no longer a
running session (`item.running` turns off) though the pty lives on until
the shell exits or `close {mode: "kill"}`. The child's environment is the
server's own overlaid with the login shell's, less a few of the server's
private variables, plus the table above and the `--mcp-config` the session
tools ride on.

**The mode tracker.** Collins keeps, per pty: every DEC private and ANSI
mode set or reset since the last reset; the kitty keyboard protocol's base
flags and a stack of at most 32 pushed entries; modifyOtherKeys; the scroll
region; the G0 to G3 designations and the shift in force; which of modes
47, 1047 and 1049 entered the alternate screen, if any; cursor visibility.
`RIS` resets all of it. `DECSTR` does what VTE does: modes back to their
defaults, the screen kept, the region and the charsets cleared. The
tracker exists for three things: the DECRQM answers; the **preamble** of a
redraw (the private modes off their defaults, then the ANSI modes, except
`3`, `25`, `1048` and the alternate-screen modes; then `CSI = <base> u`
and one `CSI > <flags> u` per stack entry; then `CSI > 4 ; <n> m`; then the
charset designations and the shift; then `CSI ? 25 l` last if the cursor
is hidden); and the attach reply's `modes` list, which is the preamble's
mode re-assertions by name.

**The screen model.** A main and an alternate grid of cells (text, width,
pen), a pen of attribute bits, foreground, background and underline colour,
**one scroll region shared by both grids** (measured on VTE: a region set on
either screen applies on the other), tab stops, charsets, origin, autowrap
and insert. 10 000 rows of main-screen scrollback under an 8 Mi cost
budget; no alternate-screen scrollback; **no reflow on resize**: rows are
cut or padded, never re-wrapped. A never-written cell is distinct from a
written space, and the cells a tab skipped are a kind of their own, so that
a redraw can write the tab again rather than spaces. `CSI 2 J` pushes the screen's used rows into scrollback as VTE does
(the synthetic scenarios record which rows count after a cursor move, an
erase, an insert, a resize or a round trip through the alternate screen);
`CSI 3 J` blanks the scrollback rows but keeps their count. A pending wrap (the cursor on the last column with the character
written) is kept or cleared per operation exactly as VTE keeps or clears
it; the operations that matter are the erase, insert and delete sequences,
cursor movement, `SGR`, `DECSC`/`DECRC`, the C0 controls, the alternate-
screen switch and `REP`, and VTE's answer for each is recorded in the
synthetic scenarios the conformance kit ships (the `pending-*` cases), with
`CSI 6 n` answering the last column while the wrap is pending. Character
widths follow VTE 0.84, which disagrees with Unicode's East Asian Width
tables for a few hundred code points; Collins carries that skew as a table
and the kit's screens data exercises the ones the scenarios touch.

**The redraw, in order.** `CSI 0 m`; the scrollback rows, oldest first,
each ended with `\r\n`, a soft-wrapped row left to wrap again at the margin
rather than ended; then `\r` and rows − 1 line feeds, which push the
scrollback above the screen; the tab stops where they differ from the
default every eighth column; the main screen, cell runs placed by absolute
`CSI <row> ; <col> H` with a full SGR at each change of pen, never-written
cells skipped, cells erased with a background colour erased again with
`CSI <n> X` so a row's text ends where it did; the saved cursor with
`ESC 7`; if the alternate screen is up, `CSI ? 1049 h` and then the
alternate screen the same way; the scroll region; the preamble; origin
(`CSI ? 6 h`), autowrap off (`CSI ? 7 l`) and insert (`CSI 4 h`) when in
force; the charset designations; the cursor, a pending wrap re-made by
writing the last column's character last; the current pen. The whole is
bounded at 2 MiB: the newest scrollback rows that fit, the screen always.
What a redraw cannot carry: a tab whose run no longer ends on a stop, a
wrap flag ahead of an empty row, and OSC 8 hyperlinks.

**What `pty.capture` and the facts share.** The capture is the model's
text as above; `takes_prompt` and the other box readings are made of the
same cells, so a server that passes the kit's screen comparisons gives the
same facts for the same bytes.

**Measurements, published.** The fixtures Collins' own screen model is
held to are MIT and travel with these pages: four recorded streams of a
real Claude Code 2.1.288 session with the offsets their scenarios end at,
their goldens (what VTE 0.84 showed at each offset: the rows, the cursor,
every drawn cell's colours and attributes, the soft wraps, the scrollback,
the box readings), and the generator of 135 synthetic scenarios with its
golden. The conformance kit, when it ships, carries the same scenarios as
expected capture text and cursor per scenario, so an implementer compares
a model against VTE's reading without VTE.
