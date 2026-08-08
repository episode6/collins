<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-08-07. Full change history: git log for this file.
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
Haiku), executed in a scratch directory so the title runs don't appear as
sessions themselves.

## Claude usage

The sidebar's usage panel reads the OAuth token the `claude` CLI already
stores in `~/.claude/.credentials.json` (read-only — Collins never refreshes
or writes it) and queries Anthropic's usage endpoint every 5 minutes, pausing
while the window is minimized or the screen is locked.

## App state

Custom names, generated titles, emoji, favorites, archived sessions, project
order, panel layouts, window geometry, and preferences are stored separately
in `~/.config/collins/state.json`. The terminal panel's per-session scrollback
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

## Architecture

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
├── terminal.py       # VTE terminal tab + secondary shell panel
├── titles.py         # auto-generated session titles (local + claude)
├── usage.py          # Claude subscription usage fetch/parse
├── usagepanel.py     # the sidebar usage panel widget
├── gitinfo.py        # git branch for the tab footer; is the tree dirty?
├── prstatus.py       # a session's pull requests and their CI status (gh)
├── prmenu.py         # the PR list popover and its per-PR actions submenu
├── practions.py      # what a PR offers (merge, review, …) and the gh calls
├── panelhistory.py   # persisted panel scrollback
├── promptcard.py     # native option cards over the terminal
├── transcript.py     # tail transcripts for pending structured prompts
├── switcher.py       # quick-switcher dialog
├── dialogs.py        # rename / emoji / confirm / details / MCP dialogs
├── prefs.py          # preferences dialog
├── themes.py         # terminal color palettes
├── i18n.py           # gettext setup + languages
├── copylabel.py      # click-to-copy footer labels
└── formatting.py     # size / timestamp / token / path formatting
```

The source lives on
[GitHub](https://github.com/episode6/collins) under GPL-3.0 —
contributions welcome. Collins is a fork of
[agent-session-manager](https://github.com/r4nd3l/agent-session-manager)
by Máté Molnár ([original project website](https://r4nd3l.github.io/agent-session-manager/)).
