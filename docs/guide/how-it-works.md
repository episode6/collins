<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-07-26. Full change history: git log for this file.
-->
# How It Works

## Reading sessions

Each supported agent stores its sessions as JSONL transcripts — Claude Code,
for example, writes them under
`~/.claude/projects/<encoded-path>/<uuid>.jsonl`. Collins scans
that directory, reads a small prefix of each transcript to extract the project
directory and a message preview, and watches the folder with a
`Gio.FileMonitor` so the list stays live.

It **only reads** these files. The single action that writes to a transcript is
"Move to trash", which sends the file to your system trash (recoverable) behind
a confirmation.

## App state

Custom names, emoji, favorites, hidden sessions, and preferences are stored
separately in `~/.config/collins/state.json`. This keeps the
app's data fully decoupled from the agents' own — you can delete the config at
any time without affecting a single session.

## Terminals

Each tab embeds a [VTE](https://gitlab.gnome.org/GNOME/vte) terminal — the same
widget behind GNOME Terminal and Ptyxis. The app spawns your `$SHELL` and types
the agent's resume command (e.g. `claude --resume <id>`) into it, so your
aliases and environment apply and you drop back to a prompt when the agent
exits.

## The stack

Collins is built with **GTK4**, **libadwaita**, **VTE**, and
**PyGObject** — pure Python, no build step. VTE is the deciding factor: it's the
only production-grade embeddable terminal on Linux, which is why the app is
Linux-native. The data layer (session discovery, parsing, state) is GTK-free
and unit-tested.

## Architecture

```
collins/
├── app.py        # Adw.Application entry point + CSS
├── window.py     # main window: tabs, actions, dialogs wiring
├── sidebar.py    # the session list widget
├── store.py      # single source of truth: threaded scans, file monitors
├── models.py     # SessionItem GObject with bindable properties
├── sessions.py   # transcript discovery & parsing (pure Python)
├── state.py      # app-side persistence
├── terminal.py   # VTE terminal tab
├── dialogs.py    # rename / emoji / confirm / details dialogs
└── prefs.py      # preferences dialog
```

The source lives on
[GitHub](https://github.com/episode6/collins) under GPL-3.0 —
contributions welcome.
