<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-08-19. Full change history: git log for this file.
-->

# Features

## Sidebar

- **Every session** under `~/.claude/projects/`, **grouped by project** with
  collapsible headers (plus a collapse-all/expand-all toggle). Which projects
  are expanded is remembered across restarts, and **dragging a project
  header** reorders the projects — the order is yours and persists.
- A **Favorites** section pinned on top — star any session to move it there.
- **Compact single-line rows**: name, relative time, status — with an
  optional second line for the project folder path. Within a project,
  sessions sort by creation time, newest first, so rows don't jump around.
  Pointing at a row swaps its timestamp for its actions: **archive** on
  every row, plus **stop** and **background** on sessions with a tab open.
- **A pull request mark ahead of the title**, on every session that has
  opened one: GitHub's own iconography, with everything the session's PRs
  amount to read into a single mark. The base says the least settled state
  among them (draft, open, merged, closed); the badge on its corner says the
  loudest thing left to do — a red **✗** for a failed check or a conflicting
  branch, an amber **⚠** for comments waiting on a reply, an amber **●** for
  checks still running, a green **✓** when every live PR has passed
  everything. Hovering names each PR, **clicking jumps to the session's tab**
  and opens its newest PR's page there, and **right-clicking** lists every
  PR with its actions.
- **Refresh** (the header's ↻) re-reads the session list *and* every listed
  session's pull requests — checks, conflicts, unanswered comments, plus a
  branch lookup that picks up PRs opened by hand. Collins runs the same
  sweep by itself a few seconds after launch (toggle in Preferences), and
  **a finished run re-asks GitHub about its own PRs** the moment its agent
  stops — that is when the answer changes, so fresh checks and review
  replies land on the mark without a click.
- A **`+` button on every project header** starts a new session in that
  project. **Right-click a header** for *New session here*, *Archive
  project*, **opening the folder in another app** (each app you added under
  Preferences → *Footer apps*, plus *Open in File Manager* and *Open in
  Terminal* for whatever your desktop nominates), **Open on GitHub** (read
  from the checkout's own remotes), and **Git pull** — labeled with the
  checked-out branch, so it's clear which branch the click acts on; success
  lands as a toast carrying git's own summary line.
- **Generate Icon**, in that same menu, asks Claude to design the project a
  sidebar icon from what's in the folder. The result is previewed at full
  size and at the 16px the sidebar actually uses; type an adjustment ("make
  it blue") and *Regenerate* until it's right. *Save* writes a
  `project-icon.svg` to the project root — the same file a project can ship
  by hand — so commit it and everyone gets it.
- A **search button** (or `Ctrl+Shift+F`) filters the whole list by name,
  project, message preview, or session ID, plus a footer showing session,
  project, transcript-size, and open-tab counts.
- **Live updates** — sessions appear and reorder as they're created or
  written to, via a filesystem watch. A just-started session shows a **"New
  Thread"** placeholder row until the agent writes its transcript.
- **Guide lines say what each session is doing**: a session with a tab open
  gets a fill (the selected tab's session takes the peach highlight); a
  **yellow line** marks a session running detached in the background
  (`/bg`); a **moving blue barber pole** marks a tab whose agent is working
  right now, going still a couple of seconds after it stops; a **red line**
  marks a session you stopped mid-task. The sidebar header runs the same
  pole while *any* session in the window is working, so the panel says so
  even when the busy row is scrolled away, collapsed, or filtered out.

![Sidebar with the Favorites section expanded](/img/sidebar-favorites.png)

The search button (or `Ctrl+Shift+F`) opens the search box across the sidebar
header, and it filters the whole list as you type:

![Filtering sessions with the search box](/img/sidebar-search.png)

### Auto-generated titles

Unnamed sessions get a **title generated for them**: sessions that already
existed when the app launched are titled **locally** (the first words of the
initial prompt — the model never sees your backlog), while sessions created
while the app runs have their first prompt summarized to **five words or
fewer** by a headless `claude -p` run — the same CLI and login the whole app
is based on, no extra credentials needed. A prompt that only points at a
pull request ("review PR 183") gets that PR's title fetched with `gh` and
handed to the model as quoted, untrusted context, so the title reads as
words instead of a number.

Titles are persisted so each is generated only once; right-click →
**Regenerate name** re-runs the model for one session, and a Preferences
toggle turns auto-titling off. A manual rename always wins. Claude names
sessions for itself too — the **Follow Claude's own session names**
preference (off by default) makes the sidebar adopt those names as they
land in the transcript.

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

- Clicking a session opens a tab with an embedded **VTE terminal** running
  your `$SHELL` with the agent's resume command (`claude --resume <id>`) —
  in the directory the session **last worked in** (worktree-aware), not just
  where it started. A session still **running detached** is **re-attached**
  (`claude attach`) instead of resumed as a copy.
- **Tabs sit in the sidebar's order** — left to right is the session list
  read top to bottom, and they re-arrange when the list does. A tab with
  unread output is marked by the tab bar itself.
- A slim **tab footer** shows the **model** the session last answered with
  (click to copy the full id — it follows a `/model` switch mid-session),
  the agent's live working directory (click to copy), and the current **git
  branch** (⎇), plus the terminal-panel buttons.
- **Pull request chips** trail the branch: one per PR the session has
  opened, each with its CI or merge mark, and each opening that PR's **page
  beside the session** on click — a native view of the description, checks,
  timeline, and diff. The caret beside them lists every PR with its title,
  `F7` opens the newest one's page, and *Open new pull requests
  automatically* (Preferences) opens the page by itself — once per PR — the
  moment a session picks one up.
- **Right-click a chip** for what to *do* with it: mark a draft **ready for
  review**, **merge** it — or arm **auto-merge** while its checks are still
  running (merging asks first, until you turn *Confirm before merging*
  off) — **ask Claude for a review**, or open it on GitHub. Four items are
  **sent to the session as a prompt** instead: *address the CI errors* when
  its CI is red, *resolve conflicts* when the branch no longer merges,
  *address unresolved comments* when someone else has had the last word, and
  *open a pull request* when a merged PR leaves uncommitted work in the
  tree. Those need the session sitting at an empty prompt; where one can't
  be sent it's greyed out, saying why.
- All of it runs on the [**GitHub CLI**](https://cli.github.com/) (`gh`) —
  every question asked with it, every action carried out by it. Without it
  the chips still appear (the numbers come off the transcript) but stay
  blank, and a launch that finds `gh` missing or signed out says so —
  pointing at the install, or at the one login command that fixes it — until
  you set it up or tick *Don't show this again*.

![A pull request page open beside its session](/img/pr-page.png)

- **Rename** tabs, **copy the session ID**, or **fork** a session
  (`--fork-session`) from the right-click menu. **Shift+Enter** inserts a
  newline in the agent's prompt, and **in-terminal search**
  (`Ctrl+Shift+G`) covers the scrollback.
- **Easy copy & paste** (on by default): plain `Ctrl+C` **copies whenever
  text is selected** — otherwise it interrupts the agent as usual — plain
  `Ctrl+V` pastes, and right-click opens a Copy / Paste / Select All menu.
  No `Ctrl+Shift` finger-twisting just because it's a terminal; the classic
  `Ctrl+Shift+C` / `Ctrl+Shift+V` always work.
- Closing a tab asks the agent to **exit cleanly** (Claude Code's
  `Ctrl+C` `Ctrl+C`, which works from whatever the agent happens to be
  showing) in the background first — or the dialog offers to **background
  the session** instead (`/bg`), leaving it running detached to re-attach
  to later. While a session tab is focused, two header buttons do the same
  directly, skipping the dialog. Backgrounding is greyed out for the second
  or two before a brand-new session's id is known — the tooltip says so.
- **Closing the window doesn't have to end anything.** The close-window
  dialog's third answer, **Keep Running (Hide Window)**, hides the window
  and leaves every session exactly as it is — tabs, panels, scrollback and
  all. The status icon's *Show Collins*, a session's notification, or
  simply launching Collins again brings it back. Where a status icon is
  present it's the dialog's default answer; the **When quitting with
  running sessions** preference can skip the dialog entirely (always ask,
  exit, background, or hide), and the menu's explicit Quit always really
  quits.
- The **tab bar can be hidden** with a header toggle — the window title then
  names the active tab — and the sidebar is **resizable**, its width
  remembered. On the next launch the app opens with no session — or, with
  **Reopen the last session** on (Preferences → Startup), with the one you
  had focused — and the window comes back at its last size.

## Prompt composer

A real text box for writing prompts — multi-line, spell-checked, floating
over the agent's terminal — for every prompt that outgrows the CLI's
one-line input:

- **Start typing and it's there** (on by default): type at an agent's empty
  prompt and the composer opens with what you typed already in it. The CLI's
  own `/`, `!`, `#` and `@` keep their keys, and so do dialogs and menus.
  `Ctrl+.` opens it deliberately; pressed again it closes the composer and
  puts the draft back in the agent's own input box, so nothing you wrote is
  ever stranded. A semi-transparent **composer button** on the corner of
  each agent terminal opens it by mouse.
- **Send on Enter** — or flip *Enter sends composer text* off to make Enter
  a newline and `Ctrl+Enter` the send. `Shift+Enter` is always a newline.
  The box is drawn in the terminal's own font on purpose: the text is about
  to *be* terminal text.
- **Drop images and files straight in.** Files land in the prompt as
  mentions; images get a strip of preview thumbnails above the text (click
  one to inspect it full-size) and go to the agent with the prompt.
- **Floating or docked.** The composer floats translucent over the
  terminal; its dock button turns it into a panel below the terminal
  instead, where it stays for that session's later visits. The *Composer in
  new sessions* preference can open it by itself the moment a session
  starts.

![The composer floating over an agent terminal](/img/composer.png)

## Terminal panel

Every tab has a second, plain-shell terminal area — no agent auto-launched —
below or beside the agent terminal, with **tabs of its own**:

- Toggle it with `Ctrl+J` or the buttons in the tab footer; `Ctrl+K` clears
  it (screen and saved history). Shells open in the agent's **current
  working directory** (worktree-aware).
- The tab row's **+** opens another shell tab; each tab's **✕** closes it,
  asking first if a command is still running. Typing `exit` closes a tab
  too, and closing the last one hides the panel.
- The **rotate button** (or `Ctrl+;`) sends the tab you're looking at to
  the panel's other side — below the terminal to beside it, and back.
  **Right-click a panel tab to split** (Left / Right / Up / Down) or **move
  it** to another strip, so you can keep shells below the terminal *and*
  beside it at once. Shells keep running through every move.
- The **overlay button** gives the tab you're looking at the *whole* tab: it
  floats over the agent terminal, the other strips and the editor — a shell
  to read a long build log, a PR page to read a diff — with a restore
  button (or `Esc`) that drops it back where it came from. While it's up it
  owns the keyboard, so nothing you type lands in the agent's terminal by
  mistake.
- Scrollback **persists across restarts, per panel tab** — reopen a session
  and the panel picks up where it left off. Each session remembers its
  panel's open state, position, and size; the strip that **pages** dock
  into (a PR view, the attachments gallery, a docked composer) remembers a
  size of its own, kept apart from the shells'.
- The **attachments panel** — the gallery of pictures a session has been
  shown, on `Ctrl+'` or the handle at the terminal's right edge — docks
  itself beside the terminal the first time a session shows a picture, when
  the window is wide enough to spare the column (toggle in Preferences →
  Panels).
- **Right-click the footer's terminal button** to open the agent's live
  directory in your desktop's own terminal instead, for the times a window
  of its own beats a panel.

![The terminal panel below an agent session](/img/terminal-panel.png)

## Editor panel

A syntax-highlighted code editor lives beside the agent terminal — the
"read and fix what the agent just did" surface, not a general-purpose IDE:

- Toggle it with `F8` or the footer icon — one editor per tab, full-height
  in a right-hand column, with a **project file tree** rooted where the
  session is working and **quick open** (`Ctrl+Shift+O`) to fuzzy-find any
  file in the project.
- An **Agent files** list pinned above the tree: the files this session's
  agent has most recently written or edited, newest first — one click from
  the change it just made.
- **It follows the session.** When the agent steps into a worktree, the
  tree, quick open and the open tabs all move with it — clean buffers
  silently, anything with unsaved changes only after asking, per file.
- Real editing: line numbers, bracket matching, undo/redo, find in file
  (`Ctrl+F`), save (`Ctrl+S`), and **180 languages'** worth of syntax
  highlighting via GtkSourceView — the engine behind GNOME Text Editor. The
  tree's right-click menu covers rename, copy, cut and paste, through the
  system clipboard, so files round-trip with your file manager.
- **External changes are the normal case** — the agent is rewriting these
  files while you look at them. A clean buffer reloads silently, cursor and
  scroll preserved; a buffer with your own edits gets a banner instead, so
  nothing is overwritten without asking.
- Each session remembers which files were open, the cursor in each, and the
  panel's width — and the whole editor can **pop out** into a window of its
  own on a second monitor, then dock back with one click.

![The editor panel beside an agent session](/img/editor-panel.png)

## Claude usage panel

Below the session list, a **Claude usage** panel shows your subscription
limits — the 5-hour session window, weekly limits, and any extra-usage
credits — as progress bars with "resets in…" countdowns. It reads the OAuth
token the `claude` CLI already stores (never modifying it), refreshes every 5
minutes, and pauses while the window is minimized or the screen is locked.
Toggle it in Preferences (*Show Claude usage*).

## Knowing what's happening

- **Desktop notifications the session raises itself** — the agent calls
  Collins' `notify_user` tool when it wants you back (see [Tools a session
  can call](#tools-a-session-can-call)), and the notification is titled with
  the session, so clicking it jumps straight to that tab. It wears the
  project's own `project-icon.svg` where the project ships one, and it flags
  the session's sidebar row too, so a popup you miss is still waiting in the
  list. Nothing is guessed from a quiet terminal: a notification means the
  agent asked for you.
- **Session details** (right-click → *Details…*): message and tool-call
  counts, models used, token totals, timestamps, transcript size — plus a
  **recent activity** peek of the last messages, so you can identify a
  session without resuming it, and the MCP servers it used.

![Session details dialog](/img/session-details.png)

- **MCP servers browser** (menu → *MCP servers*): a read-only view of every
  MCP server configured in `~/.claude.json`, global and per-project.

![MCP servers browser](/img/mcp-servers.png)

### Status icon

Collins puts a **status icon** in the top bar, so the sessions can be
watched — and reached — without the window:

- Its menu **jumps to any open session** by name, brings a hidden window
  back (*Show Collins*), opens a new window, or quits — and Quit from here
  really quits, hidden windows and all.
- The icon **wears an unread badge**: the number of sessions that finished a
  run nobody has looked at yet — the sidebar's green pulse, counted. A
  flagged session that goes back to work drops out of the count while the
  run lasts (it isn't waiting on you) and comes back the moment the turn
  ends. Sessions that are merely *working* never light the badge, but the
  tooltip carries both counts for the curious.
- With no session tabs open anywhere the icon goes passive, and the desktop
  may hide it entirely.
- It's a StatusNotifierItem — the modern tray protocol — so on GNOME it
  needs an AppIndicator extension (Ubuntu ships one enabled). Preferences →
  *Show status icon* is the switch, and it says so when nothing on the
  desktop can show one.

## Tools a session can call

Every session Collins starts is offered a small MCP server of Collins' own —
`collins` in the session's `/mcp` list — so the agent can drive the window it
is running in:

- **`notify_user(message)`** — a desktop notification titled with the
  session; clicking it raises the tab, and the sidebar row stays flagged so
  a notification you missed is still waiting when you get back.
- **`set_session_title(title)`** — the session names itself, in the tab and
  the sidebar, and renames itself again when the work pivots.
- **`open_in_editor(path, line?)`** — put a file on your screen in the
  session's own editor pane, instead of hoping you click a path in the
  terminal.
- **`show_image(path)`** — show a screenshot, plot, or render in the in-app
  lightbox. An `http(s)` URL works too: Collins downloads it and shows the
  copy.
- **`attach_pr(url)`** — put a pull request on the session's footer and
  sidebar row, live status and all — for a PR Collins can't spot on its
  own, like one opened by a subagent, or one the session is reviewing
  rather than authoring.
- **`start_session(prompt, …)`** — spawn a **sibling session**: a new agent
  in a background tab, handed a prompt to begin on, working in parallel
  while the caller keeps going. It never takes your tab selection or
  keyboard — it turns up as a new row in the sidebar, rings and flashes if
  it needs you, and inherits the permission mode its caller was started
  with. Spawned sessions get these same tools, so they can spawn siblings
  of their own.
- **`read_terminal(terminal?, lines?)`** — read the terminal panel's tabs,
  text and scrollback, exactly as you see it — so "the error over there" is
  something the agent can just look at instead of asking you to paste it.
- **`run_in_terminal(command, terminal?)`** — type a command into an idle
  panel shell and run it, visibly, where you can watch it, interact with
  it, and keep the shell afterwards — a dev server, a REPL, a long build.

Each tool asks for permission the first time a session calls it, like any
other MCP tool, and **each has its own switch** in Preferences → *Session
tools* (all on by default): a tool switched off isn't offered to the
sessions Collins starts from then on, and a session already running when you
flipped the switch is refused if it calls it anyway.

## Starting sessions

- **New session** (tab icon in the header, or `Ctrl+Shift+T`) starts a
  fresh agent session in the **visible session's project** — no dialog
  needed; with no session visible, it asks for a folder. The **Advanced**
  entry picks a model, a permission mode, or an extra directory
  (`--add-dir`), and **Continue** resumes the most recent session in a
  folder (`claude --continue`).
- With *Start new sessions in a git worktree* on, each new session works in
  a fresh worktree of its project, so it won't see uncommitted local
  changes; a launch that can't cut one (a repository with nothing committed
  yet, say) says so and starts in the project directory instead.
- **Folder trust is asked once, up front**: the first launch in a project
  the agent doesn't trust yet asks *Do you trust this folder?* before
  anything starts, and records the answer where the agent reads it, so the
  question isn't asked a second time inside the terminal. Trust covers
  everything under the folder, worktrees included.

## Bulk actions & housekeeping

- **Select mode** (sidebar menu → *Select multiple sessions*) to open, star,
  archive, or trash many sessions at once.
- **Archive** sessions you're done with (kept on disk; toggle "Show
  archived" to see and restore them). Archiving a session with an open tab
  closes the tab too, and whole **projects** can be archived from their
  header's right-click menu.
- **Archiving reaches claude.ai too** (on by default — Preferences →
  *Archive on claude.ai too*): a session that also appears on claude.ai is
  archived and restored there along with the toggle here. Best-effort:
  archiving locally never waits on the network.
- **Delete archived sessions…** (sidebar menu) clears the lot in one go,
  and the confirmation spells out the damage first: how many transcripts,
  in which projects. Any dialog that would empty a project out offers to
  keep it in the sidebar as an empty header, so *New session here* still
  works.
- **Export as Markdown** (right-click) writes a session transcript to a
  readable Markdown file. **Move to trash** (recoverable) and **delete
  permanently** are the only actions that touch a transcript file, and
  always sit behind a confirmation.
- **Open in [Ghostty](https://ghostty.org)** resumes a session in an
  external Ghostty window instead of an embedded tab (shown when `ghostty`
  is on your `PATH`).

## Caffeine Mode

The coffee cup at the right of the header keeps the computer awake and the
screen on while an agent works unattended — click it to toggle; the cup
fills while it's on.

- **Right-click it for a timer**: *Until idle*, a duration from 1 to 12
  hours, or *Indefinitely* — so a long build can't leave the machine awake
  all week because you forgot. The time left counts down beside the cup.
- **Until idle**, the default, hands the deadline to the sessions instead
  of the clock: as long as at least one open tab is working — the same
  barber pole the session list shows — the machine stays awake. Five
  minutes after the last one stops, Caffeine Mode dozes but stays armed: a
  session picking work back up — tomorrow morning included — takes hold of
  the machine again, until you click the cup off.
- **Keep screen on** decides how far "awake" goes: on (the default) holds
  the screen too; off lets the screen blank while the computer still can't
  sleep — for an overnight run you don't want lighting up the room.
  Preferences can also arm Caffeine Mode at every launch, with a duration
  of your choice.

## Multiple windows

Open additional windows from the New Session button's menu or with
`Ctrl+Shift+N`. Windows share one session list and state, and a session
only ever runs in one tab: clicking a session another window already has
open raises that window instead of resuming a copy.

**Move a running session to a window of its own** from its sidebar row's
right-click menu: the tab is lifted out and dropped into a fresh window
live — the agent keeps running, and its scrollback, panel, and editor come
along.

## Preferences

Terminal **font**, **scrollback** size, **easy copy & paste** (on by default),
a **terminal color theme** (Dracula, Solarized, Gruvbox, Nord, Catppuccin,
Tokyo Night, Monokai, One Dark…), the **composer's** switches (the typing
trigger, Enter behavior, the floating button, auto-open in new sessions),
the editor's **color scheme**, **font**, and **line numbers**/**hidden
files** toggles, the app's **color scheme** (system / light / dark), the
**language** (English, Magyar, Deutsch, Español, Français), the sidebar's
**Show folder path**, **Show Claude usage**, and **Auto-generate session
titles** toggles, the **status icon**, **Reopen the last session**, what to
do **when quitting with running sessions** (ask / exit / background / hide),
**Archive on claude.ai too**, and a switch for each of the **session tools**
the agent can call — reachable from the sidebar menu or `Ctrl+,`.

A **search bar across the top** filters the whole screen as you type, and it
has the focus the moment preferences opens, so the way to a setting is to
type a word from it — a section heading, a word from a description
(*Ctrl+C*), or an option folded away inside a row (*Dracula*, *Magyar*).

![Preferences dialog](/img/preferences.png)
