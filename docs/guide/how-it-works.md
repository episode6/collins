<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-08-30. Full change history: git log for this file.
-->
# How It Works

## Reading sessions

Claude Code stores its sessions as JSONL transcripts under
`~/.claude/projects/<encoded-path>/<uuid>.jsonl`. Collins scans
that directory, reads a small prefix of each transcript to extract the project
directory and a message preview, and watches the folder with a
`Gio.FileMonitor` so the list stays live. Metadata-only stub transcripts (for
example from worktree agent runs) are filtered out so they don't create
phantom projects.

It **only reads** these files. The only actions that write to a transcript are
"Move to trash" (sends the file to your system trash, recoverable) and "Delete
permanently" — both behind a confirmation.

## Resuming, attaching & backgrounding

Opening a session spawns your `$SHELL` in the directory the transcript **last
recorded as its working directory** (so worktree-hopping sessions resume where
they left off) and types `claude --resume <id>` into it. If the session is
still running detached — say you backgrounded it with `/bg` — Collins finds
the live process via `claude agents --json` and types `claude attach <job-id>`
instead, reconnecting rather than resuming a copy.

On current Claude Code versions, backgrounding (`/bg`) detaches the session
in place — the same session id keeps running as a background agent, so the
sidebar row simply stays put. Older CLI versions instead forked the
conversation to a new session id; when that happens, Collins tracks the
forwarding so the old sidebar row is replaced by the live one and names,
favorites, emoji, and panel state carry over.

## Session titles

Pre-existing sessions are titled **locally** on launch (first words of the
initial prompt) — no model call, so your backlog is never sent anywhere. Only
sessions created while the app runs get a headless `claude -p` summarization
(the model is the *Session title model* preference, defaulting to the newest
Haiku; its **None** option keeps the local title for new sessions too),
pinned to the lowest effort level so a saved `/effort xhigh` is never spent
on a five-word summary,
executed in a scratch directory, with none of your skills, MCP servers, or
the CLI's tools loaded (see [What spends tokens](#what-spends-tokens)), so
the title runs don't appear as sessions themselves and cost little more
than the prompt.

## Model list

Every model picker — the session footer, the composer, Preferences, Generate
Icon — offers what your login can actually use, which Collins asks the Models
API for once and then leans on hard. The list is **cached for a day**, and
saved to `~/.cache/collins/models.json` so a restart doesn't ask again: the
catalog changes a few times a year, and the pickers should not cost a network
round trip every time one opens.

The list is grouped by tier family so like sits with like: any unrecognized
(so, newer) family first, then Mythos, Fable, Opus, Sonnet, Haiku, each family
ordered by version number with the newest at the top. In the footer and
composer model menus a divider separates one family from the next.

One answer is trusted less than that: a query that comes back with a single
model is not a catalog you can pick from — a page cut short, or a login that
momentarily sees one model — so Collins shows it (it still beats the aliases)
but gives it no lifetime at all. The next picker you open, and the next launch
of the app, ask again instead of sitting on it for the day.

A failed query never clears that cache. Offline, logged out, or with the API
refusing, the pickers keep offering the last list Collins got, however old,
and only fall back to the CLI's aliases (`opus`, `sonnet`, `haiku`) if no
query has ever succeeded on this machine. A run of failures also backs off
for five minutes rather than making every picker wait out the network timeout
again.

The cost of caching that hard is a model released this morning not appearing
until tomorrow, so **Preferences → Token use → Model list** dates the list
("12 models, updated 3h ago") and its **Refresh** button asks Anthropic
outright, ignoring both the day and the backoff.

That row never goes quiet about a failure, whoever caused it. Whenever the
list on screen is one Collins fell back to — a Refresh that couldn't reach
Anthropic, or simply opening the page after a background query failed — it
says so and keeps naming how old the list it's showing is ("Couldn't reach
Anthropic — still showing the list fetched 2d ago"). Opening Preferences onto
a lapsed day with the network down reads exactly the same as pressing Refresh
with the network down, because it is exactly as broken.

Every query is logged too: run Collins with `COLLINS_LOG=INFO` to see what
came back, and anything that failed — an unreachable API, a missing token —
is logged at `WARNING`, which the default level already prints.

## Claude usage

The sidebar's usage panel reads the OAuth token the `claude` CLI already
stores in `~/.claude/.credentials.json` (read-only — Collins never writes
it) and queries Anthropic's usage endpoint every 5 minutes, pausing
while the window is minimized or the screen is locked.

Refreshing that token is the CLI's job, done at the start of any `claude`
run — so when Collins finds the CLI installed but the token dead, it runs
one throwaway headless `claude -p` prompt in the background (in the same
scratch directory the title runs use, so it never appears as a session),
then re-asks the usage endpoint and, if a query had already failed, the
model catalog. That happens at launch, when the stored token is already
past its expiry, and mid-run, when a usage poll comes back refused — an app
left running outlives its token, and a token can also be revoked
server-side while the file still looks fine. Repair attempts are
single-flight and cooled down, and every consecutive failure doubles the
cooldown — an hour, then two, four, up to a day — so a login no run can
fix costs a few background subprocesses a day, not one per poll; a success
sets the cooldown back to an hour. No credentials
file at all means not logged in, which no background run can fix; the
panel just says so. The run spends tokens without a prompt from you, so it
has a switch — **Auto-renew the Claude login**, in Preferences → Token use,
on by default — and with it off the panel says the login expired and leaves
running `claude` to you. Nor does any repair run before the first-launch
welcome dialog that discloses the switch has been answered: not the launch
check, and not one the usage panel asks for from under the open dialog
(its first fetch is refused the same way); until Continue, the panel says
the login expired, exactly as it does with the switch off.

## What spends tokens

Collins runs Claude on your behalf — against your subscription's usage
limits — in four places, each with its setting in **Preferences → Token
use** (directly under General) or the **Built-in MCP tools** group right
below it. Every headless run is a `claude -p` from a scratch directory, so
none of them ever appears as a session, and each passes
`--strict-mcp-config --tools ""`, so none carries your MCP servers, the
CLI's built-in tools, or your skills (they ride in the Skill tool) — what
an interactive session loads, and what a prompt that wants five words back
has no use for. On CLI 2.1.251 that took a one-line prompt on Haiku from
about 23k input tokens to 8k. Your global `~/.claude/CLAUDE.md` still
loads: the one flag that drops it, `--bare`, also drops the OAuth login the
repair run exists to renew. A project's `CLAUDE.md` and
`.claude/settings.json` are read from the working directory and its
parents, which the scratch directory under `~/.config/collins` keeps out
of reach. A fresh install is shown these
settings once, in the **Before you start** dialog on its first launch,
before the first of the runs happens — and the login repair waits until
that dialog has been answered.

- **Session titling** — a `claude -p` on the *Session title model* for
  every unnamed transcript that appears under `~/.claude/projects` while
  the app runs: sessions Collins launched, sessions an agent spawned through
  `start_session`, background jobs, and a `claude -p` you ran from a
  terminal in some project. *None* turns the model runs off; the free local
  title (the first words of the prompt) always runs.
- **Project icon generation** — a `claude -p` on the *Icon generation
  model*, only on **Generate Icon** in a project header's menu and then per
  click of Regenerate. Under *None*, the default, the dialog waits for a
  model pick and a click.
- **Login repair** — one throwaway `claude -p` (a one-word prompt on Haiku)
  when the CLI's login is found expired at launch or a usage poll is
  refused mid-run, backing off from an hour to a day on repeated failure;
  *Auto-renew the Claude login* turns it off.
- **Built-in MCP tools** — no run of their own, but every enabled tool's
  definition rides in each session's context, `read_terminal` sends the
  panel's text into the conversation, and a session `start_session` starts
  is titled like any other. One switch per tool.

The **Model list** row beside the pickers is the odd one out: a Models API
query that spends no tokens, which its subtitle says.

## Archiving on claude.ai

A session that was remote-controlled from claude.ai, or teleported into from
there, has a counterpart on the web's session list, and the transcript
records which: a `bridge-session` line naming the remote id. When you archive
or restore such a session, Collins mirrors the toggle with the CLI's own
session API — `POST /v1/code/sessions/<id>/archive` (or `/unarchive`) on the
same stored OAuth token — from a background thread, after the local archive
has already landed. Every failure (no counterpart, no token, no network, an
HTTP error) is logged and swallowed; nothing blocks or reverts the local
toggle. *Archive on claude.ai too* in Preferences turns it off.

## App state

Custom names, generated titles, emoji, favorites, archived sessions, project
order, panel layouts, unsent composer drafts, window geometry, and preferences
are stored separately in `~/.config/collins/state.json`. The terminal panel's per-session scrollback
lives in `~/.local/state/collins/panel_history/` (one file per panel tab).
This keeps the app's data fully decoupled from the agents' own — you can
delete both at any time without affecting a single session.

## Terminals

Each tab embeds a [VTE](https://gitlab.gnome.org/GNOME/vte) terminal — the same
widget behind GNOME Terminal and Ptyxis. The app spawns your `$SHELL` and types
the agent's resume command (e.g. `claude --resume <id>`) into it, so your
aliases and environment apply and you drop back to a prompt when the agent
exits. The secondary panel terminal is another VTE running a plain shell —
the same widget, minus the agent.

## The stack

Collins is built with **GTK4**, **libadwaita**, **VTE**, and
**PyGObject** — pure Python, no build step. VTE is the deciding factor: it's the
only production-grade embeddable terminal on Linux, which is why the app is
Linux-native. The data layer (session discovery, parsing, state, titles,
usage, git info) is GTK-free and unit-tested.

## Undocumented APIs and CLI internals

Collins has no SDK to lean on: it reads what the `claude` CLI reads and calls
what the CLI calls. Some of that is public — the `claude` command line,
`--resume`, `-p`, `/mcp` and `--mcp-config` — and everything built on those
(terminals, the editor, panels, the MCP session tools) is on solid ground, as
are the pull request features, which go through `gh`. The rest is the CLI's
private surface, in two kinds. Anthropic can change either without notice;
when something moves, the feature built on it stops working until Collins
catches up, and the app is written so that's a blank panel or a skipped step,
never a crash.

### Undocumented APIs

Three features call Anthropic directly, on the OAuth token the CLI stores in
`~/.claude/.credentials.json` (read, never written — an expired token is
refreshed by the CLI itself, via a throwaway headless run at launch or when
a poll comes back refused; see [Claude usage](#claude-usage) for the backoff
and the switch that turns it off) and the
same beta header the CLI sends:

| Feature | Endpoint | When it breaks |
| --- | --- | --- |
| Usage panel | `/api/oauth/usage` — what feeds the CLI's `/usage` screen | The panel reports an error and stays empty |
| Model pickers (footer, composer, Preferences, Generate Icon) | the Models API, which answers to the CLI's token only with its beta header | Keeps serving the last list it got (see [Model list](#model-list)); with none ever fetched, falls back to the CLI's built-in aliases (`opus`, `sonnet`, `haiku`) |
| Archive on claude.ai | `POST /v1/code/sessions/<id>/archive` and `/unarchive` | The local archive still happens; the remote one silently doesn't |

### CLI internals

The larger dependency is on files and commands the CLI keeps for itself —
formats nobody promised would stay put:

| Feature | Leans on | When it breaks |
| --- | --- | --- |
| Session list, titles, status, the footer's model, PR detection, the attachments scan, a spawned sibling's inherited model and permission mode | The JSONL transcript format under `~/.claude/projects/` and its fields (`cwd`, `permissionMode`, `message.model`, `bridge-session`, …) | Rows go blank or misreport; nothing is written, so nothing is lost |
| Re-attaching to backgrounded sessions | `claude agents --json` and `claude attach` | Opening a detached session resumes a copy instead of reconnecting |
| Folder trust asked up front | The trust entries the CLI keeps in `~/.claude.json` | The CLI asks its own question at launch, as it would without Collins |
| Busy / idle detection | The CLI's OSC 9;4 progress reports and the on-screen shape of its prompt | The sidebar's working indicator and the composer's "empty prompt" gate misjudge |
| Model switching, prompts sent from PR chips | The CLI's `/model` command and the layout of its input box | A switch or a sent prompt lands as typed text instead of taking effect |

## Architecture

The package is some 100 modules by now; these are the load-bearing ones:

```
collins/
├── app.py            # Adw.Application entry point + CSS
├── window.py         # main window: tabs, actions, dialogs wiring
├── sidebar.py        # the session list widget
├── store.py          # single source of truth: threaded scans, file monitors
├── models.py         # SessionItem GObject with bindable properties
├── sessions.py       # transcript discovery & parsing (pure Python)
├── providers.py      # agent CLI abstraction (currently Claude Code)
├── state.py          # app-side persistence
├── terminal.py       # VTE terminal tab + its panels' wiring
├── composer.py       # the prompt composer text box
├── newchat.py        # the new-chat screen's state: drafts, picks, launch
├── newchatview.py    # the new-chat screen itself
├── editor.py         # the editor panel (GtkSourceView)
├── docktree.py       # the panel docking tree: strips, splits, moves
├── mcpserver.py      # the in-app MCP server sessions can call
├── mcptools.py       # the tools it offers (notify, spawn, show_image, …)
├── prstore.py        # single source of truth for pull request state (gh)
├── prview.py         # the in-app pull request page
├── practions.py      # what a PR offers (merge, review, …) and the gh calls
├── statusicon.py     # the status icon: a StatusNotifierItem over D-Bus
├── traymodel.py      # what the icon shows (badge, menu) — toolkit-free
├── notifycenter.py   # the notification history, the badge's number, the delivery table — toolkit-free
├── notifypanel.py    # the header bell and the history sheet it opens
├── notifyoverlay.py  # the in-app notification card
├── notifysound.py    # the notification sound (GStreamer, with the beep as fallback)
├── caffeine.py       # Caffeine Mode: inhibit sleep while agents work
├── titles.py         # auto-generated session titles (local + claude)
├── usage.py          # Claude subscription usage fetch/parse
├── updatecheck.py    # the once-a-day look at GitHub's latest release
├── tokensettings.py  # the Token use rows: what runs Claude for you
├── welcome.py        # the first-launch dialog: those rows, and where claude is
├── gitinfo.py        # git branch for the tab footer; is the tree dirty?
├── transcript.py     # tail transcripts for touched files and PR links
├── dialogs.py        # rename / emoji / confirm / details / MCP dialogs
├── prefs.py          # preferences dialog
└── …                 # panels, docking, theming, i18n, and the rest
```

The source lives on
[GitHub](https://github.com/episode6/collins) under GPL-3.0 —
contributions welcome. Collins is a fork of
[agent-session-manager](https://github.com/r4nd3l/agent-session-manager)
by Máté Molnár ([original project website](https://r4nd3l.github.io/agent-session-manager/)).
