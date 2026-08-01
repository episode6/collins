<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-08-01. Full change history: git log for this file.
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
- **Open a project's folder anywhere else** from that same right-click menu:
  one row per app you added under Preferences → *Footer apps*, each with its
  own icon, plus *Open in File Manager* and *Open in Terminal* for whichever
  apps your desktop nominates for those jobs (`$TERMINAL`,
  `xdg-terminals.list`, and the system's own `x-terminal-emulator` are
  honoured, in that order). The Chats group has no folder of its own, so its
  menu stays as it was.
- A **search button** that opens a search box across the sidebar header,
  filtering by name, project, message preview, or session ID, plus a footer
  showing session, project, transcript-size, and open-tab counts.
- **Live updates** — sessions appear and reorder as they're created or written
  to, via a filesystem watch. A just-started session shows a **"New Thread"**
  placeholder row until the agent writes its transcript.
- **Quick switcher** (`Ctrl+Shift+K`) — a type-ahead dialog to jump to any
  session by name, project, preview, or ID.
- **Running sessions are filled in**: a session with a tab open gets a fill, so
  live work stands out from the archive of past sessions — read or unread, the
  fill is the same. The session shown in the selected tab takes the peach
  highlight.
- **A yellow guide line** marks a session **running detached** in the
  background (`/bg`) — no fill, because there is no tab to return to; reopen
  the row to re-attach.
- **A moving barber pole** marks a session whose agent is **working right
  now**: the row's guide line turns into blue stripes climbing while output is
  flowing, and goes still a couple of seconds after the agent stops — so a
  glance down the sidebar says which sessions are thinking and which are
  waiting on you. A detached session poles in **yellow** instead of blue, since
  its line is what says it has no tab. Collins reads a tab's terminal for this;
  for a detached session, its transcript — which only grows in bursts, so the
  yellow pole rides out longer quiet spells (about ten seconds) before going
  still, instead of flickering off mid-turn. The pole follows the desktop's
  animation setting: with animations off, the line simply stays put.
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
- A tab with **unread output** is marked by the tab bar itself; tabs carry no
  status dot of their own.
- **Tabs sit in the sidebar's order** — left to right is exactly the session
  list read top to bottom, whatever order you opened them in, and they
  re-arrange when the list does (a project dragged to a new spot takes its
  tabs with it). Chats and replays, which have no row in the list, collect at
  the right-hand end. The tab bar can't be dragged into a different order for
  that reason: reorder the projects in the sidebar instead.
- A slim **tab footer** shows the agent's live working directory (click to
  copy) and the current **git branch** (⎇), plus the terminal-panel buttons.
- **Pull request chips** trail the branch: one per PR the session has opened,
  each with its **CI mark** (✓ / ✗ / ●) or GitHub's merge mark, and each
  opening that PR on click. The caret beside them lists every one with its
  title — the same list a sidebar row's GitHub button opens.
- **Right-click a chip** (or a PR in either list) for what to *do* with it:
  mark a draft **ready for review**, **merge** it — or turn on **auto-merge**
  while its checks are still running — or **ask Claude for a review** (a
  `@claude review` comment, for repositories running the Claude Code GitHub
  action). Left-clicking still opens the page, which is why nothing in the
  menu does; a PR with nothing left to do says so rather than opening empty.
- Two of those items are **sent to the session as a prompt** instead of run
  against GitHub: **address the CI errors**, when that PR's CI is red, and
  **open pull request**, once it has **merged** and the terminal's working
  directory has **uncommitted changes** again — your work landed, and what is
  in the tree now wants a PR of its own. Both are only offered while the
  session is open **and sitting at an empty prompt**, so a half-written line
  of yours is never sent along with it (and a permission dialog, which takes
  Enter too, is never answered by it).
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
- Closing a tab asks the agent to **exit cleanly** (Claude Code's
  `Ctrl+C` `Ctrl+C`) in the background first, rather than terminating it.
  The quit keystroke rather than a typed `/exit`, because it works from
  whatever the agent happens to be showing — a permission prompt, the trust
  dialog, its session list — and not just from an empty prompt. For agents that support
  it, the close dialog also offers to **background the session** instead
  (Claude Code's `/bg`) — the agent keeps running detached, and reopening the
  session re-attaches to it. The same option appears when closing the whole
  window with active sessions, which hands the sessions over **one at a time**
  so each is correctly paired with the background agent it becomes.
- While a session tab is focused, two header buttons act on it directly,
  skipping the confirmation dialog: one **exits** the session and closes the
  tab, the other **backgrounds** it (shown only for agents that support
  detaching) and closes the tab.
- Backgrounding is **greyed out until it is safe**: a brand-new thread can't be
  backgrounded until Collins knows its session id, and no session can be
  backgrounded while another handoff is still waiting for the id its agent
  moved to. Both windows last a second or two; the tooltip says which one you
  are in. Backgrounding before then would detach an agent that nothing could
  find its way back to.
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
- **Right-click the footer's terminal button** to open that same directory —
  the agent's live one, not the project root — in your desktop's own terminal
  instead, for the times a window of its own beats a panel. The terminal is
  whatever `$TERMINAL`, `xdg-terminals.list` or the system's
  `x-terminal-emulator` nominate (the same pick the sidebar's *Open in
  Terminal* uses), and the directory is handed to it on its own command line,
  so an already-running terminal opens where you asked rather than wherever it
  last was.
- Its scrollback **persists across restarts** — reopen a session and the
  panel picks up where it left off, with a "restored panel history" marker.
- Each session remembers its panel's open state, position, and size; the
  last-used position and size also become the default for new panels.
- Typing `exit` in the panel hides it.
- Closing a tab while a command is running in its panel — even a hidden one —
  asks for confirmation before the command is killed.

![The terminal panel below an agent session](/img/terminal-panel.png)

## Editor panel

A syntax-highlighted code editor lives beside the agent terminal — the
"read and fix what the agent just did" surface, not a general-purpose IDE:

- Toggle it with `F8` or the footer icon (a page with a folded corner,
  between the footer apps and the terminal-panel button) — one editor per
  tab, full-height in a right-hand column.
- A **project file tree** rooted at the tab's directory; click a file to
  open it in a tab strip of its own, with a dot marking unsaved changes.
- Real editing: line numbers, current-line highlight, bracket matching,
  undo/redo, and **180 languages'** worth of syntax highlighting via
  GtkSourceView, the same engine behind GNOME Text Editor and Builder.
- **Save** with `Ctrl+S` or the status row's save button; **find** in the
  current file with `Ctrl+F`.
- **External changes are the normal case**, not the edge case — the agent is
  rewriting these files while you look at them. A clean buffer reloads
  silently, cursor and scroll preserved; a buffer with your own edits gets a
  banner instead, so nothing is overwritten without asking.
- The color scheme **follows the app's light/dark setting** by default, or
  pick one of GtkSourceView's bundled schemes in Preferences, along with the
  editor's font and whether the file tree shows hidden files.
- Each session remembers which files were open, the cursor in each, and the
  panel's width — restored the next time you reopen it.
- **Pop it out** to a second monitor with the status row's detach button
  (rightmost): the whole editor — open files, cursors, unsaved changes —
  moves into a window of its own, and the in-tab panel disappears until it
  comes back. Dock it back with the window's headerbar button, by closing
  the window, or with the tab's footer icon (while the editor is popped
  out, one click means "bring it back"). One editor per tab, in one place
  at a time; the popped-out window remembers its own size.

If the footer icon is greyed out, the GtkSourceView 5 library the editor is
built on isn't installed — the `.deb` pulls it in automatically, but a source
checkout needs it installed by hand (Debian/Ubuntu: `gir1.2-gtksource-5`,
Fedora: `gtksourceview5`, Arch: `gtksourceview5`). Install it and restart
Collins; the button's tooltip says the same.

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

## Caffeine Mode

The coffee cup at the right of the header keeps the computer awake and the
screen on while an agent works unattended — click it to toggle, and the cup
fills while it's on.

- **Right-click it for a timer**: *1 hour*, *2 hours*, *3 hours*, *6 hours*,
  *12 hours* or *Indefinitely*. Picking a duration turns Caffeine Mode on for
  that long and turns it off again when the time runs out — so a long build
  can't leave the machine awake all week because you forgot.
- The **time left counts down** just left of the cup while a timer is running.
  Picking another duration restarts the clock, *Indefinitely* clears it, and
  turning Caffeine Mode off cancels it.
- Preferences → *Turn on at launch* starts every launch with Caffeine Mode on,
  and *Turn off after* arms one of the same durations at startup.

## Multiple windows

Open additional windows from the New Session button's menu or with
`Ctrl+Shift+N`. Windows share one session list and state, so favorites, names,
and live updates stay consistent across them.

## Preferences

Terminal **font**, **scrollback** size, **easy copy & paste** (on by default),
a **terminal color theme** (Dracula, Solarized, Gruvbox, Nord, Catppuccin,
Tokyo Night, Monokai, One Dark…), the editor's **color scheme**, **font**,
and **line numbers**/**hidden files** toggles, the app's **color scheme**
(system / light / dark), the **language** (English, Magyar, Deutsch,
Español, Français), the sidebar's **Show folder path**, **Show Claude
usage**, and **Auto-generate session titles** toggles, and the
idle-notification toggle — reachable from the sidebar menu or `Ctrl+,`.

![Preferences dialog](/img/preferences.png)
