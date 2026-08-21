<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-08-21. Full change history: git log for this file.
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
- ✅ **Workflow** — terminal panel per tab, session backgrounding (`/bg`) & re-attach, graceful `Ctrl+C` close, per-tab cwd + git-branch footer, Export as Markdown, tab emoji
- ✅ **Pull requests** — every session's PRs tracked through `gh` (checks, conflicts, unanswered comments) as sidebar marks, footer chips, and an in-app PR page; merge / auto-merge / ready-for-review / request-a-review actions, with red CI, conflicts, and unanswered comments sendable back to the agent as prompts
- ✅ **Editor** — a per-tab code editor beside the terminal: file tree, quick open, an agent-files list of what the session just wrote, pop-out to a second monitor
- ✅ **Composer** — a spell-checked, multi-line prompt box that opens the moment you start typing, floating or docked, with drag-and-drop image attachments
- ✅ **Session tools** — an in-app MCP server every launched session can call: rename itself, open a file or an image on your screen, notify you when it needs you, attach a pull request to its own row, spawn a sibling session, and read or drive the terminal panel
- ✅ **Desktop presence** — a status icon with an unread badge, close-to-hide (sessions keep running without a window), notifications wearing each project's own icon, Caffeine Mode
- ✅ **Theming** — light/dark plus selectable terminal color palettes
- ✅ **Localization** — English, Hungarian, German, Spanish, French
- ✅ **Multi-window**
- ✅ **Distribution** — Ubuntu PPA (`ppa:episode6/stable`), `.deb`, PyPI (`pipx install collins`), one-step tag-driven releases

### Exploring next

- 🔭 **Flathub** distribution
- 🔭 **AUR** package
- 🔭 **Fedora COPR** repository
- 🔭 **A better native chat** — a first-class chat experience for an agent session, beyond the terminal

## Changelog

### v0.1.1 — UNRELEASED

Collins is now installable from an apt repository:

```bash
sudo add-apt-repository ppa:episode6/stable
sudo apt install collins
```

The PPA covers **Ubuntu 24.04 (noble)** and **26.04 (resolute)**, and the
Ubuntu derivatives that share them — Linux Mint, Pop!_OS, elementary OS,
Zorin. Ubuntu 22.04 (jammy) is out of scope: it ships libadwaita 1.1 and GTK
4.6, and Collins uses APIs from libadwaita 1.5 and GTK 4.10.

**On Debian, keep using the `.deb`** from the releases page. A Launchpad PPA
can only ever serve Ubuntu, so the `.deb` is not a lesser fallback — it is the
channel for Debian and the rest of its family, and it stays. It adds no
apt source, so it does not update itself; watch the releases page. Debian 13
(trixie) and newer have everything Collins needs.

- **`Ctrl+Shift+F` no longer opens the sidebar search.** `Ctrl+K` (the
  quick switcher) is the keyboard way to reach a session; the sidebar's
  search button still works, and the chord is held back for a future
  session-content search. The `win.focus-search` action remains for custom
  keybindings.
- **Installs alongside agent-session-manager.** Collins' action icons are
  app-private artwork on generic names, and they were being written into the
  shared `hicolor` icon theme, where they collided with the copies upstream's
  package owns. They now live in `/usr/share/collins/icons`, which also stops
  them outranking the system's own icons for every other application.
- **The `.deb` declares what it actually needs.** It was missing
  `gir1.2-gtksource-5` and `gir1.2-spelling-1` entirely, so it could install on
  a machine without them and then exit on launch asking you to install them by
  hand. Both packages now also require libadwaita 1.5 and GTK 4.10, turning a
  crash on a too-old distribution into an apt refusal that says why.
- **Collins is on PyPI** as plain `collins` (`pipx install
  --system-site-packages collins`). The wheel carries the app's icons,
  `.desktop` launcher, and metainfo, and `collins --install-desktop` puts the
  launcher in the app grid.
- **The sidebar menu offers the PPA.** On Ubuntu (and its derivatives), a
  Collins that didn't come from `ppa:episode6/stable` — the GitHub `.deb`,
  PyPI, a checkout — shows *Add the Ubuntu PPA…* in the ☰ menu until the
  repository is configured. It shows the commands and runs them in a
  terminal of the current session, where `sudo` can ask for a password.
- **Close hides the window; sessions keep running.** The close dialog's
  third answer, *Keep Running (Hide Window)*, leaves every session exactly
  as it is with no window on screen; the status icon's *Show Collins
  (Hidden)*, a notification, or relaunching brings it back, and the *When
  quitting with running sessions* preference can make hiding the default.
  A first-time notice says where Collins went.
- **Session tools grow up** — `start_session` takes a `model`, and a spawned
  sibling inherits the caller's current **model and permission mode** by
  default (bypass mode never inherits — it caps to acceptEdits).
- **Generate Icon picks its model per run** — a drop-down in the dialog, so
  a more capable model can be tried for one project without changing the
  Preferences default.
- **Git pull from the project row** — a project's right-click menu pulls its
  checked-out branch, named on the item; git's summary lands as a toast.
- **`Ctrl+K` is the quick switcher** and `Ctrl+Shift+K` clears the terminal
  panel — the two swapped, so the jump-anywhere key is the easier chord.
- **The composer opens on typing by default** (still an opt-out), and
  **libspelling is optional** — without it the composer is a plain text box,
  everything else intact.
- **A composer draft is never lost.** Closing the composer types the draft
  back into the agent's input box, but when the agent has left the terminal
  there is nowhere safe to put it — that draft is now kept for the session
  and comes back the next time you open its composer, as long as the box is
  empty. Drafts are saved to disk with the rest of the session's state, so
  closing the tab or quitting Collins keeps them too — including the one
  still in an open composer when the window goes away.
- **A PR page opens wider when the room is free** — twice as wide as it
  can be squeezed (640px) whenever the terminal is already past its maximum
  width and the spare gutter covers it, or as much of that gutter as covers
  the page at all; never a pixel out of the terminal, and decided before the
  page's first fetch lands, so it doesn't resize under its own data.
- **Running checks are followed, not waited for.** While a PR you're
  looking at has checks in progress — or none yet, for the first minute
  after it was opened fresh off a push — Collins asks GitHub every ten
  seconds whether its head commit's check-runs changed — a conditional
  request that comes back as an empty, rate-limit-free `304` until one does
  — and fetches the full status the moment they do, instead of at the next
  minute mark.
  Marks, chips and the PR page itself all update on the spot (the page
  re-reads when it's on screen). Merged and closed PRs are now refetched
  every ten minutes rather than every one.
- **Preferences regrouped** — eighteen groups become ten, the CLI path
  alone under no heading at the top. **General** follows with language,
  Dark / Light Mode, status icon, tab drag handles, the sidebar's switches
  and the icon model; **Session
  behavior** gathers everything about how sessions start, get named, are
  archived and quit (with the title model beside the auto-title switch, and
  the polling and busy-tracking fallbacks at its tail); the **composer's**
  switches leave Terminal for a **Composer** group of their own; the three
  pull-request switches that lived under Session list join **Pull
  requests**; and **Session tools** is now **Built-in MCP tools**. Row
  names and settings are unchanged (two renames: the app's **Color scheme**
  is **Dark / Light Mode**, and **Show folder path** is **Show folder paths
  in sidebar**), so search finds everything where it was.
- **Fixes** — archiving a session clears its notification and unread count;
  the status icon's menu no longer draws a stray "Quit" on a separator; a
  click in the empty gutter beside a width-limited terminal clears the
  session's unread flag and focuses the terminal, as a click into the
  terminal does.

### v0.1.0 — Collins (2026-08-17)

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
- **Session tools** — an in-app MCP server offered to every session Collins
  starts, so the agent can drive the window it runs in: `notify_user`,
  `set_session_title`, `open_in_editor`, `show_image` (local or URL, with a
  caption), `attach_pr`, `start_session` (spawn a sibling session in the
  background), and `read_terminal` / `run_in_terminal` for the terminal
  panel. Each tool has its own switch in Preferences.
- **Pull requests** — every PR a session opens is tracked through `gh`:
  checks, conflicts, and unanswered comments roll up into one mark on the
  sidebar row and chips on the footer, and a native **PR page** beside the
  session shows the description, checks, timeline, threads and diff. Merge,
  auto-merge, ready-for-review, ask-Claude-for-a-review, comment, approve,
  and reply/resolve from the app; red CI, conflicts, and unanswered comments
  can be sent back to the agent as prompts. `attach_pr` and PRs named in a
  first prompt attach to the session too.
- **Editor panel** — a GtkSourceView editor beside the terminal (`F8`): file
  tree, quick open, an agent-files list of what the session just wrote,
  clickable file references in the terminal that open at the line, "Add to
  chat" for a selection, and pop-out to its own window on small screens.
- **Prompt composer** — a spell-checked, multi-line prompt box over the
  terminal that opens the moment you type at an empty prompt (`Ctrl+.`),
  floating or docked, with drag-and-drop files and image previews.
- **Attachments panel** — a per-session gallery of every image and file the
  session has shown or handed over (`Ctrl+'`), with a lightbox whose arrows
  walk the gallery; it docks itself beside the terminal when there's room.
- **Terminal panel** — a second plain-shell terminal per session (`Ctrl+J`),
  grown into a **dock of splittable strips**: tabs of its own, split and move
  by drag or right-click, rotate (`Ctrl+;`), take the whole session tab as an
  overlay, with layout and scrollback persisted per session.
- **Status icon** — Collins in the top bar with an unread-count badge, and
  notifications wearing each project's own icon.
- **Model switcher** — the footer names the model a session is answering
  with; click it (or the composer's model button) to switch, from a live
  list of the models your login can use.
- **Claude usage panel** — subscription limits with reset countdowns under the
  session list, polled every 5 minutes (paused while minimized/locked).
- **Session backgrounding & re-attach** — close dialogs and header buttons can
  background a session (`/bg`) instead of exiting it; opening a
  still-running session attaches to the live process instead of resuming a
  copy. Closing asks the agent to exit cleanly (`Ctrl+C` `Ctrl+C`).
- **Auto-generated session titles** — pre-existing sessions titled locally,
  new ones summarized by a headless `claude -p` run; a session can also take
  its name from its pull request, or follow Claude's own session names.
- **Sidebar upgrades** — compact single-line rows with a guide line for every
  status (working, unread, detached, interrupted, waiting on you), per-project
  icons from `project-icon.svg` (or one Claude designs for you), drag-to-reorder
  projects, per-project `+` buttons, a virtual Chats project, archive with
  undo, Open In… submenus, a "New Thread" placeholder for just-started
  sessions, and creation-time sorting.
- **Starting sessions** — new sessions in a git worktree (`claude -w`,
  opt-in), folder trust asked once up front, a setting for where the `claude`
  CLI lives, and advanced launch options (model, permission mode, extra
  directories, continue).
- **Caffeine Mode** — keep the machine awake while sessions work ("Until
  idle"), for a set time, or until turned off; screen may still go dark.
- **Per-session footer** — the agent's live working directory (click to copy),
  the current git branch, and configurable app-launcher buttons.
- **Quality of life** — reopen the last active session on launch (opt-in),
  remembered window size, a searchable Preferences window, `Ctrl+W` to close
  a tab, terminal font zoom and smooth scrolling, drop images and files into
  the chat as `@`-mentions, move a session to its own window, and retuned
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
