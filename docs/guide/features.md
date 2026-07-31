<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-07-30. Full change history: git log for this file.
-->

# Features

## Sidebar

- **Every session** under `~/.claude/projects/`, **grouped by project** with
  collapsible headers (and one header-bar toggle that collapses every group, or
  opens them all again). Which projects are expanded is remembered across
  restarts.
- **Drag a project header** up or down to rearrange projects; the order is
  yours and persists (new projects join the list alphabetically).
- A **Favorites** section pinned on top — star any session to move it there.
- **Compact single-line rows**: name, relative time, status — with an optional
  second line showing the project folder path (Preferences → *Show folder
  path*). Within a project, sessions sort by creation time, newest first, so
  rows don't jump around as sessions produce output.
- **Row actions on hover** — pointing at a row swaps its timestamp for its
  buttons, so a row shows either when it last ran or what you can do with it:
  **archive** on every row, preceded by **stop** (exit the agent and close its
  tab) and **background** (detach with `/bg`, leaving it running) on the
  sessions that have a tab open.
- A **`+` button on every project header** starts a new session in that
  project; right-click a header for *New session here* and *Archive project*.
  Projects whose sessions are all archived or favorited still show their header,
  so the folder stays reachable.
- A **search button** that opens a search box across the sidebar header,
  filtering by name, project, message preview, or session ID, plus a footer
  showing session, project, transcript-size, and open-tab counts.
- **Live updates** — sessions appear and reorder as they're created or written
  to, via a filesystem watch. A just-started session shows a **"New Thread"**
  placeholder row until the agent writes its transcript.
- **Quick switcher** (`Ctrl+Shift+K`) — a type-ahead dialog to jump to any
  session by name, project, preview, or ID.
- **Running sessions are filled in**: a session with a tab open gets a fill and
  a green left guide line, so live work stands out from the archive of past
  sessions. A tab with output you haven't looked at yet keeps the fill and
  turns its guide line neutral grey. The session shown in the selected tab
  takes the peach highlight.
- **A yellow guide line** marks a session **running detached** in the
  background (`/bg`) — no fill, because there is no tab to return to; reopen
  the row to re-attach.
- **Waiting badge** — an amber **?** marks sessions where the agent's last
  message was a question awaiting your reply, so you can spot what needs you at
  a glance.
- **Interrupted badge** — a red stop icon marks sessions you stopped mid-task.

![Sidebar with the Favorites section expanded](/img/sidebar-favorites.png)

The search button (or `Ctrl+Shift+F`) opens the search box across the sidebar
header, and it filters the whole list as you type — the **X** closes it again
and restores the unfiltered list:

![Filtering sessions with the search box](/img/sidebar-search.png)

### Auto-generated titles

Unnamed sessions get a **title generated for them**:

- Sessions that already existed when the app launched are titled **locally**
  (the first 10 words of the initial prompt) — the model never sees your
  backlog.
- Sessions created while the app is running have their first prompt summarized
  to **five words or fewer** by a headless `claude -p --model haiku` run — the
  same CLI and login the whole app is based on, no extra credentials needed.

Titles are persisted so each is generated only once; right-click →
**Regenerate name** re-runs the model for one session, and a Preferences
toggle turns auto-titling off. A manual rename always wins.

### Quick switcher

Press `Ctrl+Shift+K` anywhere to fuzzy-jump to a session — arrow keys move,
Enter opens, Esc closes.

![Quick switcher](/img/quick-switcher.png)

## Custom names, favorites & emoji

- Give any session a **custom name** (right-click → *Rename…*, or rename its
  tab — the name syncs everywhere).
- **Star** sessions to pin them to Favorites.
- Add an **emoji** prefix to a tab (right-click a tab → *Set emoji…*, or
  `Ctrl+Shift+E` for a quick 😊 marker).

![Setting a tab emoji, with two sessions open as tabs](/img/tab-emoji.png)

All of this is stored in `~/.config/collins/state.json`. Your
agents' own session files are never modified.

## Tabs & terminals

- Clicking a session opens a tab with an embedded **VTE terminal** running your
  `$SHELL` with the agent's resume command (`claude --resume <id>` for Claude
  Code) — in the directory the session **last worked in** (worktree-aware),
  not just where it started.
- If the session is still **running detached** (e.g. after backgrounding it),
  Collins **re-attaches** to the live process (`claude attach`) instead of
  resuming a copy.
- A **green dot** marks a tab whose output you're caught up on, matching the
  sidebar's guide line; a tab with unread output drops the dot.
- A slim **tab footer** shows the agent's live working directory (click to
  copy) and the current **git branch** (⎇), plus the terminal-panel buttons.
- **Rename** tabs, **copy the session ID**, or **fork** a session
  (`--fork-session`) from the right-click menu.
- **Shift+Enter** inserts a newline in the agent's prompt.
- **In-terminal search** (`Ctrl+Shift+G`) over the scrollback.
- **Easy copy & paste** (on by default): plain `Ctrl+C` **copies whenever
  text is selected** — otherwise it interrupts the agent as usual — plain
  `Ctrl+V` pastes, and right-click opens a Copy / Paste / Select All menu.
  No `Ctrl+Shift` finger-twisting just because it's a terminal; the classic
  `Ctrl+Shift+C` / `Ctrl+Shift+V` always work, and the mode can be toggled
  in Preferences.
- Closing a tab asks the agent to **exit cleanly** (Claude Code's `/exit`) in
  the background first, rather than terminating it. For agents that support
  it, the close dialog also offers to **background the session** instead
  (Claude Code's `/bg`) — the agent keeps running detached, and reopening the
  session re-attaches to it. The same option appears when closing the whole
  window with active sessions.
- While a session tab is focused, two header buttons act on it directly,
  skipping the confirmation dialog: one **exits** the session and closes the
  tab, the other **backgrounds** it (shown only for agents that support
  detaching) and closes the tab.
- The **tab bar can be hidden** with a header toggle — tabs and sessions keep
  running. While it is hidden the **window title becomes the active tab's
  title**, so the header (and alt-tab, and the dock) still names the session
  you are looking at; showing the tab bar again restores "Collins".
- On the next launch the app **reopens the session you had focused** when you
  closed the window, and the window comes back at its last size.
- The sidebar is **resizable** (drag the divider) and its width is remembered.

## Terminal panel

Every tab has a second, plain-shell terminal — no agent auto-launched — that
lives below or beside the agent terminal:

- Toggle it with `Ctrl+J` or the buttons in the tab footer; `Ctrl+K` clears it
  (screen and saved history).
- It opens in the agent's **current working directory** (worktree-aware), and
  the swap button moves it bottom ↔ right without restarting its shell.
- Its scrollback **persists across restarts** — reopen a session and the
  panel picks up where it left off, with a "restored panel history" marker.
- Each session remembers its panel's open state, position, and size; the
  last-used position and size also become the default for new panels.
- Typing `exit` in the panel hides it.
- Closing a tab while a command is running in its panel — even a hidden one —
  asks for confirmation before the command is killed.

![The terminal panel below an agent session](/img/terminal-panel.png)

## Claude usage panel

Below the session list, a **Claude usage** panel shows your subscription
limits — the 5-hour session window, weekly limits, and any extra-usage
credits — as progress bars with "resets in…" countdowns. It reads the OAuth
token the `claude` CLI already stores (never modifying it), refreshes every 5
minutes, and pauses while the window is minimized or the screen is locked.
Toggle it in Preferences (*Show Claude usage*).

## Knowing what's happening

- **Desktop notifications** when a background session goes quiet after
  producing output — click to jump straight to that tab. (Off by default;
  toggle in Preferences.)
- **Session details** (right-click → *Details…*): message and tool-call counts,
  models used, token totals, timestamps, transcript size — plus a **recent
  activity** peek of the last messages, so you can identify a session without
  resuming it. It also lists the **MCP servers** available to the project and
  which ones the session actually used.

![Session details dialog](/img/session-details.png)

- **MCP servers browser** (menu → *MCP servers*): a read-only view of every MCP
  server configured in `~/.claude.json`, global and per-project.

![MCP servers browser](/img/mcp-servers.png)

## Prompt cards

When the agent asks a structured question in the terminal, a native option
card overlays it — answer with a click instead of typing a number.

## Starting sessions

- **New session** (tab icon in the header, or `Ctrl+Shift+T`) starts a fresh
  agent session in the **visible session's project** — no dialog needed. With
  no session visible, it asks for a folder.
- **Advanced new session** (New Session menu): choose a **model**, a
  **permission mode**, or an **extra directory** (`--add-dir`).
- **Continue** the most recent session in a folder (`claude --continue`).

## Bulk actions & housekeeping

- **Select mode** (sidebar menu → *Select multiple sessions*) to open, star,
  archive, or trash many sessions at once.
- **Archive** sessions you're done with (kept on disk, toggle "Show archived"
  to see them and restore any of them); archiving a session with an open tab
  closes the tab too. Whole **projects** can be archived from their header's
  right-click menu.
- **Delete archived sessions…** (sidebar menu) clears the lot in one go: every
  session the sidebar keeps out of sight — archived by hand, archived with its
  whole project, or replaced by a backgrounded fork — has its transcript moved
  to the trash. Archiving is cheap and the pile grows quietly,
  so the confirmation spells out the damage first: how many transcripts, in
  which projects, and how many of those projects lose *every* session they
  have. Greyed out when nothing is archived.
- **Keeping a project after its sessions go**: any dialog that would empty a
  project out offers *"Keep the N emptied project(s) in the sidebar"* (checked
  by default). Kept projects stay as empty headers with their folder, so
  **New session here** still works — they're remembered across restarts, and
  the header's right-click menu can **Remove project from sidebar** again. A
  project that gets real sessions back simply keeps its place.
- **Export as Markdown** (right-click) writes a session transcript to a
  readable Markdown file.
- **Move a transcript to trash** (recoverable) or **delete it permanently** —
  the only actions that touch a transcript file, and always behind a
  confirmation.
- **Open in [Ghostty](https://ghostty.org)** to resume a session in an external
  Ghostty window instead of an embedded tab (shown when `ghostty` is on your
  `PATH`).

## Multiple windows

Open additional windows from the New Session button's menu or with
`Ctrl+Shift+N`. Windows share one session list and state, so favorites, names,
and live updates stay consistent across them.

## Preferences

Terminal **font**, **scrollback** size, **easy copy & paste** (on by default),
a **terminal color theme** (Dracula, Solarized, Gruvbox, Nord, Catppuccin,
Tokyo Night, Monokai, One Dark…), the **color scheme** (system / light /
dark), the **language** (English, Magyar, Deutsch, Español, Français), the
sidebar's **Show folder path**, **Show Claude usage**, and **Auto-generate
session titles** toggles, and the idle-notification toggle — reachable from
the sidebar menu or `Ctrl+,`.

![Preferences dialog](/img/preferences.png)
