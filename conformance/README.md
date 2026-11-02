<!--
SPDX-License-Identifier: MIT
Copyright (c) 2026 episode6, Inc.
-->

# The conformance kit

A client that holds a server of the Collins service API to its published
specification: [the service API](../docs/api/index.md), its
[message reference](../docs/api/messages.md), [the terminal
contract](../docs/api/terminal.md) and the [session facts](../docs/api/sessions.md)
page. Run it against `collins-service` or against a server written from those
pages, and it says, check by check, what the server does as the pages say and
what it does not.

Everything here is MIT (see [LICENSE](LICENSE)), like the pages and unlike the
rest of Collins. It is Python 3.12 or newer and the standard library alone; it
imports `collins/api/protocol.py` (the MIT message table) by path and nothing
else of Collins, so it can be copied out of the repository with that one file
beside it as `conformance/protocol.py` (or named by `CONFORMANCE_PROTOCOL` in
the environment). Nothing in it reads Collins' service.

## Running it

```bash
python3 -m conformance <socket path> [options]
```

The socket is the one `collins-service --print-socket` prints, or a server's
own. **Run it against a server that holds nothing of value**: the kit spawns
sessions and shells of its own, writes files in its scratch directory and
under its sessions' directories, posts and removes a notification, uploads
one file, and reads and writes state (`names`, `favorites`, `archived` for
its own session; a `settings` entry written back to its own value). It names
no pty but its own and sends a server that lists `internal` or `debug`
nothing of theirs; still, a person's sessions on the same server see its
rows come and go, so a scratch service is the place for it
(`scripts/run_conformance.py` in the Collins repository makes one). The kit runs on the server's machine: the fake claude it points the
server at is a file the server runs, the scratch directory it makes is one the
server writes in, and the repository it makes for the `git` family is read by
the server. Over an ssh forward, pass `--fake-claude` and `--scratch` as paths
on the server's machine and copy the fake there first.

| Option | What it does |
| --- | --- |
| `--level N` | The highest level to check, 0 to 2 (default 2). |
| `--family F` | Check only this family (repeatable); the default is every family the server lists in its hello. |
| `--skip-family F` | Leave a family out (repeatable). `prs` needs the GitHub CLI and the network, `sandbox` bubblewrap. |
| `--fake-claude PATH` | The fake claude, as a path on the server's machine (default: `conformance/fake_claude.py`). |
| `--scratch DIR` | A directory the kit may write in (default: a temporary one, removed at the end). |
| `--replay` | Also write every recorded Claude Code stream under `data/streams/` into a fake session, mark by mark, and compare the server's capture and `takes_prompt` with VTE's reading. |
| `--vte-bytes` | Fail a query the server answers in another terminal's voice. Without it such an answer is a note: the terminal contract says a server should answer with VTE 0.84.0's bytes and is conformant at the wire otherwise; Collins' own CI passes it, since Collins promises those bytes. |
| `--spawn-budget SECONDS` | How long the fake's box may take to take a prompt after the spawn (default 5). The fake's own budget, not a property of the pages. |
| `--with-gh` | Run the `prs` checks that call `gh`: a fetch and a detail of a real pull request of the Collins repository, over the network, holding only that they are answered. |
| `--only TEXT` | Run only the groups whose name holds TEXT: `level 0`, `terminal`, `sessions`, `level 2`, `families`. |
| `--json FILE` | Write the results as JSON too. |
| `-v` | Group headings and a traceback on a crash. |

Every check ends `pass`, `fail` (with what was seen against what was expected)
or `skip` (with the reason). A table closes the run, and the exit status is 1
when any check failed. A `pass` may carry a note: a SHOULD the server did not
meet, a measurement, a word the kit wants on the record.

**The fake claude.** The Level 1 checks need the server to run a Claude Code
session, so they point it at `fake_claude.py`: a stand-in that draws the input
box and writes the transcript as the appendix of the sessions page describes,
reports progress with OSC 9;4 under the two environment variables, and takes
the kit's prompts as the scenarios to play (a quiet mid-turn, a clear with
output going on, leaving mid-turn). Install it as the `claude` the server
finds: for `collins-service`, a copy named `claude` in a directory the
`claude_cli_path` setting names, first on the service's `PATH`
(`scripts/run_conformance.py` in the Collins repository does this for a
checkout's service). The real Claude Code is never started, and no token is
spent.

## What each level proves

**Level 0, the wire.** The hello and the protocol window; `sequence` before
hello; `unknown` for a made-up type; `invalid` on a bound and on an event sent
with an id; the two channels and a reconnect taking a `client_id` over; the
snapshot's events before the `subscribe` reply with the counts matching; the
required fields of `service.status`; the local proof refused with wrong bytes
and accepted with the right ones; every request type answered, and a type of
an unlisted family answered `unknown`; the WebSocket keepalives across 25 s of
idleness; a 1.5 MiB `fs.write` chunked out and the `fs.read` chunked back,
where `files` is claimed. A server that passes Level 0 shows the Collins
window a connected, empty sidebar.

What it does not prove: that the server does anything with a session.

**Level 1, the terminal.** With a probe inside a pty of the server's (a
`shells` pty, or the fake's `probe` prompt): the spawn environment; the grid;
every query of the terminal contract's tables, answered with VTE 0.84.0's
bytes (the DECRQM table and the palette in `data/`), stripped, or passed
through to the attached client unchanged; the tokenizer's cuts; an attach's
redraw frames and `modes`; the active client, resize and focus; `paint`; an
input frame written without an attach; `pty.info`; and the screen model of
record, every published scenario painted into the pty with the server's
`pty.capture` and cursor report held to what VTE showed for the same bytes.

What it does not prove: that the redraw reproduces the screen on a real
terminal. The kit has no terminal; it compares the model's text, which is what
the session facts are read from, and leaves the byte-level comparison to the
implementer's own terminal.

**Level 1, the sessions.** Against the fake: `takes_prompt` within 5 s of the
spawn and false while the box holds text; a prompt that lands; `busy` within
3 s and down within 10 s of the turn's end; one `finished` per turn, through
the idle repaints after it; `unread` on the row and `store.flags` clearing it;
the transcript's model, effort and permission mode; the id bound within 10 s of
the first record; a second attach's snapshot holding the series' word; a
`switch` reflected within a turn; the two roads a fall of `busy` takes (a quiet
under a second ends nothing; the agent's clear ends the turn while its output
goes on, and a rise after that fall is no failure); `busy` down within 10 s of
the agent leaving mid-turn; `close {exit}` and `pty-exited`. With `--replay`,
the recorded streams of Claude Code 2.1.288 written into a fake session, the
capture and `takes_prompt` against VTE's reading.

What it does not prove: anything about the real Claude Code. The fake draws
what the appendix describes; a release of the CLI that draws otherwise is the
appendix's to catch, not the kit's.

**Level 2, the store.** A rename and its `names` entry, a favorite, a lookup, an
archive with `put-away` and `store.page-archived`, a posted notification and
`seen`, folder trust, the transcript's tail and export, a refresh.

**The families.** One short check each: `git` (a scratch repository, `git.info`,
a builder, a builder the server lacks refused `unknown`, an argument of the
wrong type `invalid`), `files` (read, write, `stale`,
list, the chunked round trip under a session's directory), `prs` (with
`--with-gh` only), `sandbox` (a clean refusal of what does not exist), `tools`
(the blob and upload endpoints), `jobs` (an unknown kind is `invalid`), `usage`
(`models.defaults`), `background` (answered), `shells` (a shell pty, `clear`,
`panel.key`), `composer` (a cut, a draft restored, a send). `internal` and
`debug` are Collins' own and are skipped.

## The data

| File | What it is | Where it comes from |
| --- | --- | --- |
| `data/queries.json` | The three tables of the terminal contract in the page's notation. | Written from the page; `tests/test_conformance_data.py` in the Collins repository holds it to the page. |
| `data/decrqm-modes.json` | VTE 0.84.0's DECRQM answer for every mode in its default state. | Measured by `scripts/check_conformance_data.py --write` in the Collins repository (and re-checked by it). |
| `data/vte-palette.json` | VTE's default 256-colour palette as OSC 4 reports it. | The same script. |
| `data/screens/*.json` | Each published synthetic scenario: its bytes, and the capture text and cursor VTE showed. | `scripts/check_termscreen_parity.py --write`, from the goldens under `tests/fixtures/streams/`. |
| `data/streams/` | The recorded Claude Code streams with their marks and goldens, for `--replay`. | Copied by the same script. |

The kit's own tests: `python3 -m unittest discover -s conformance/tests -t .`
from the repository root.

## Reading a failure

A fail names what the kit saw and what the page it tests says, so the first
question is which of the two is wrong. A server that differs from the page
where the page says MUST has a bug; where the page says SHOULD, the kit notes
it and passes; where the page is silent, the kit does not check. A check the
kit cannot make from outside (a `direction` refusal, which no frame a client
sends can draw; a redraw fed to a terminal) is recorded in the check's own
words rather than tested. Collins runs the kit against its own service in CI
(the `conformance` job), so a fail against `collins-service` on `main` is a
finding about the service or the pages, and worth reporting.
