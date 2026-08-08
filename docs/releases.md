<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-08-08. Full change history: git log for this file.
-->

# Releases & Roadmap

A running overview of what's shipped and what's planned. For the full notes and
downloads of each version, see the
[GitHub releases](https://github.com/episode6/collins/releases).

## Roadmap

### Shipped milestones

- ✅ **Core** — sidebar of all sessions, embedded terminal tabs, resume/fork
- ✅ **Organization** — favorites, custom names, auto-generated titles, project groups (reorderable, hideable), search, quick switcher
- ✅ **Insight** — session details, transcript peek, MCP servers & usage, detached / busy / interrupted guide lines, Claude subscription usage panel
- ✅ **Workflow** — terminal panel per tab, session backgrounding (`/bg`) & re-attach, graceful `Ctrl+C` close, per-tab cwd + git-branch footer, idle notifications, Export as Markdown, tab emoji
- ✅ **Theming** — light/dark plus selectable terminal color palettes
- ✅ **Localization** — English, Hungarian, German, Spanish, French
- ✅ **Multi-window**
- ✅ **Distribution** — `.deb`, one-step tag-driven releases

### Exploring next

- 🔭 **Flathub** distribution

## Changelog

### v0.1.0 — Collins

The first release under the **Collins** name, and the fork's version reset —
Collins forked from
[agent-session-manager](https://github.com/r4nd3l/agent-session-manager)
([original project website](https://r4nd3l.github.io/agent-session-manager/))
and restarted its version numbering at 0.1.0.

- **Rebrand to Collins** — app name, command (`collins`), Python package, app
  id (`com.episode6.Collins`), docs, and an original **Tom Collins glass**
  app icon. Existing settings, names, and favorites migrate automatically from
  the old `~/.config/agent-session-manager/` (or older
  `~/.config/claude-session-manager/`) location on first run.
- **Claude Code only** — the upstream Cursor provider was removed; this fork
  focuses on Claude Code.
- **Terminal panel** — a second plain-shell terminal per tab (`Ctrl+J`),
  bottom or right, with persisted per-session layout and scrollback history.
- **Claude usage panel** — subscription limits with reset countdowns under the
  session list, polled every 5 minutes (paused while minimized/locked).
- **Session backgrounding & re-attach** — close dialogs and header buttons can
  background a session (`/bg`) instead of exiting it; opening a
  still-running session attaches to the live process instead of resuming a
  copy.
- **Auto-generated session titles** — pre-existing sessions titled locally,
  new ones summarized by a headless `claude -p --model haiku` run.
- **Sidebar upgrades** — compact single-line rows, drag-to-reorder projects,
  per-project `+` buttons, empty projects with hide/unhide, a "New Thread"
  placeholder for just-started sessions, active-tab highlight, and
  creation-time sorting.
- **Per-tab footer** — the agent's live working directory (click to copy) and
  the current git branch.
- **Quality of life** — reopen the last active session on launch, remembered
  window size, tab-bar hide toggle, `Ctrl+W` to close a tab, and retuned
  defaults (easy copy & paste on, idle notifications off).

---

## Upstream history

Everything below is the changelog of the upstream project,
[agent-session-manager](https://github.com/r4nd3l/agent-session-manager) by
Máté Molnár, as it stood at the fork point. Version numbers are upstream's
(unrelated to Collins's own 0.1.0). Note that Cursor support (added upstream
in v0.10.0) and the native chat / session replay features (added in v1.0.0)
are not currently part of Collins.

### v1.0.0 — Native chat, replay & richer sessions

- **Native streaming chat** (Claude): token-by-token output with **interactive
  permission cards** — Allow / Always allow / Deny every Edit, Write, and Bash.
- **Cursor chat** in read-only (`ask`) and trusted (auto-run) modes.
- **Resume into chat**: reopen any session from the sidebar as a live chat tab.
- **Session replay**: step through any transcript as native chat bubbles.
- **Interactive prompt cards** over the terminal answer the agent's questions
  with a click.
- **Advanced new-session options**: pick a model, permission mode, extra
  directories, or continue the last session in a folder.
- **Open session from file**, **permanent delete** (alongside trash), a
  **folder-path** toggle in the sidebar, and a quick **tab emoji** shortcut
  (`Ctrl+Shift+E`).
- A new **generic agent icon and banner**, replacing the Claude-specific logo.

### v0.10.0 — Multi-agent (Cursor)

- **Provider framework**: session handling is now generalized behind per-agent
  providers, so the app manages more than one AI coding agent.
- **Cursor support**: Cursor sessions are discovered and resumed (`cursor-agent`)
  right alongside Claude Code. One sidebar lists both, each row badged with its
  agent icon.
- **Per-agent New Session**: the New Session menu offers one entry per installed
  agent. Resume, graceful close, and fork all route through each session's own
  agent (Cursor force-closes; fork stays Claude-only).

### v0.9.0 — Rebrand to Agent Session Manager

- Renamed from **Claude Session Manager** to **Agent Session Manager** — the
  project, repository, app icon, and docs — to reflect upcoming support for
  more AI coding agents beyond Claude Code.
- Existing settings, names, and favorites migrate automatically from the old
  `~/.config/claude-session-manager/` location on first run.
- Installed command became `agent-session-manager`; PyPI package
  `agent-session-manager-gtk`; AUR/PPA package `agent-session-manager`.

### v0.8.0 — Distribution & localization

- **Localization**: full UI translations for Hungarian, German, Spanish, and French, with a language picker
- **Terminal color themes**: Dracula, Solarized, Gruvbox, Nord, Catppuccin, Tokyo Night, Monokai, One Dark…
- **Multi-window** support
- **Interrupted badge** for sessions you stopped mid-task
- **AUR** and **Ubuntu PPA** packages; one-step tag-driven release pipeline

### v0.7.0 — Insight, export & PyPI

- Waiting badge for sessions where Claude asked a question
- Export a session transcript as Markdown
- Published to PyPI; app ID moved to `io.github.r4nd3l.ClaudeSessionManager`

### v0.6.0 — Navigation & config

- Quick switcher (`Ctrl+Shift+K`)
- New Session remembers your last folder; resizable, persisted sidebar
- Drag to reorder tabs; read-only MCP servers browser

### v0.5.0 — Session insight

- Transcript peek in the details dialog
- Desktop notifications when a background session goes idle
- MCP servers and per-session usage; online documentation

### v0.4.0 — Tab usability

- Per-tab emoji, clearer active-tab styling, close-all-tabs button
- Shift+Enter inserts a newline in Claude's prompt

### v0.3.0 — Search & graceful close

- In-terminal find bar; closing a tab asks Claude to exit cleanly (`/exit`)
- Copy session ID; groups collapsed by default; card-style rows

### v0.2.0 — Tabs & workflow

- Renameable tabs, per-tab status dots, toggleable sidebar
- Installable `.deb`

### v0.1.0 — Initial release

- Sidebar of Claude Code sessions, embedded terminal tabs, custom names, favorites
