<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-08-24. Full change history: git log for this file.
-->

# Features

::: warning Three features use undocumented Anthropic APIs
The **usage panel**, the **model pickers**, and **archiving on claude.ai**
call endpoints the `claude` CLI uses for itself and doesn't document, on the
CLI's own login. They can change or break with any CLI release; each is built
to fail quietly when that happens. Details, and the CLI internals the rest of
the app reads, are in
[How It Works](/guide/how-it-works#undocumented-apis-and-cli-internals).
:::

## Sidebar

- **Every session** under `~/.claude/projects/`, **grouped by project**.
  **Click a project header** to start a new session there; the strip left
  of its title — icon included, shown as a caret under the pointer — folds
  and unfolds the group, as do ←/→ on a focused header (plus a
  collapse-all/expand-all toggle). Which projects are expanded is
  remembered across restarts, and **dragging a project header** reorders
  the projects — the order is yours and persists.
- A **Favorites** section pinned on top — star any session to move it there.
- **Compact single-line rows**: name, relative time, status — with an
  optional second line for the project folder path. Within a project,
  sessions sort by creation time, newest first, so rows don't jump around.
  Pointing at a row swaps its timestamp for its actions: **archive** on
  every row, plus **stop** and **background** on sessions with a tab open.
- **Aggregated pull request status on every session**: a mark ahead of the
  title reads a session's PRs into one glyph — their state, plus whatever
  needs attention most (failed checks, conflicts, unanswered comments,
  all green). Clicking jumps to the session's tab and its newest PR's page;
  right-clicking lists every PR with its actions.
- **Custom project icons**: a project that ships a `project-icon.svg` in its
  root directory wears it in the sidebar in place of the generic folder
  icon — commit one and everyone who opens the project in Collins gets it.
  **Generate Icon**, in a project header's right-click menu, has Claude
  design one from what's in the folder — previewed at full size and at the
  16px the sidebar actually uses, regenerable with an adjustment ("make it
  blue") until it's right; *Save* writes the file to the project root.
- **Live updates** — new sessions appear the moment they're created, via a
  filesystem watch, and existing rows update in place without jumping
  around. A just-started session shows a **"New Thread"** placeholder row
  until the agent writes its transcript.
- A **Claude usage panel** under the session list: your subscription
  limits — the 5-hour session window, weekly limits, extra-usage credits —
  as progress bars with reset countdowns, read from the `claude` CLI's own
  login and refreshed every 5 minutes. Toggle it in Preferences (*Show
  Claude usage*).

![The sidebar: each project wearing its own icon, sessions carrying pull request marks](/img/sidebar.png)

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

Press `Ctrl+K` anywhere to fuzzy-jump to a session — arrow keys move,
Enter opens, Esc closes.

![Quick switcher](/img/quick-switcher.png)

## Sessions & terminals

- Clicking a session opens it in an embedded **VTE terminal** running your
  `$SHELL` with the agent's resume command (`claude --resume <id>`) — in
  the directory the session **last worked in** (worktree-aware), not just
  where it started. A session still **running detached** is **re-attached**
  (`claude attach`) instead of resumed as a copy. Open sessions stay open
  as you switch between them; the sidebar marks the ones with unread
  output.
- A slim **session footer** shows the **model** the session last answered
  with, the agent's live working directory (click to copy), and the current
  **git branch** (⎇), plus the terminal-panel buttons.
- **The model is one click from switching.** The footer's model name is a
  menu: every model your login can use, fetched from the Models API with
  the CLI's own token, the current one marked. Pick another and the
  session gets the CLI's `/model` command — the footer follows once the
  agent answers with it. The same menu sits in the composer's chrome, so
  you can change model halfway through writing a prompt without losing the
  draft. (Copying the full model id lives in the menu too.)
- **Pull request chips** trail the branch: one per PR the session has
  opened, each with its CI or merge mark, and each opening that PR's **page
  beside the session** on click — a native view of the description, checks,
  timeline, and diff, whose *Conversation* and *Files* tabs each open their
  github.com counterpart on a right-click. The caret beside them lists every
  PR with its title,
  `F7` opens the newest one's page, and *Open new pull requests
  automatically* (Preferences) opens the page by itself — once per PR — the
  moment a session picks one up. On a screen where the terminal has already
  hit its *maximum width* (Preferences), the page opens in a column of its
  own, twice as wide as it can be squeezed when the spare room covers that —
  paid out of the gutter the terminal wasn't using, never out of the
  terminal itself.
- **A changed image is shown, not diffed.** In the PR page's *Files* view, a
  file whose name says image — `.png`, `.svg`, `.gif`, `.jpg`, `.webp`… —
  renders as the picture itself, on a transparency checkerboard: **before
  beside after** for one the PR changed, a single picture for one it adds or
  deletes, and a click opens either full size. An SVG keeps its patch under
  the preview (a small one is drawn scaled up, since vector artwork loses
  nothing by it); a binary image, whose diff only ever said the two files
  differ, shows the picture alone. The blobs come through `gh`, so private
  repositories and Enterprise hosts work — and *Show embedded images*
  (Preferences) turns the whole thing off.
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

- **Rename** a session, **copy its ID**, or **fork** it (`--fork-session`)
  from the right-click menu. **Shift+Enter** inserts a newline in the
  agent's prompt, and **in-terminal search** (`Ctrl+Shift+G`) covers the
  scrollback.
- **Easy copy & paste** (on by default): plain `Ctrl+C` **copies whenever
  text is selected** — otherwise it interrupts the agent as usual — plain
  `Ctrl+V` pastes, and right-click opens a Copy / Paste / Select All menu.
  No `Ctrl+Shift` finger-twisting just because it's a terminal; the classic
  `Ctrl+Shift+C` / `Ctrl+Shift+V` always work.
- Closing a session asks the agent to **exit cleanly** (Claude Code's
  `Ctrl+C` `Ctrl+C`, which works from whatever the agent happens to be
  showing) in the background first — or the dialog offers to **background
  the session** instead (`/bg`), leaving it running detached to re-attach
  to later. While a session is focused, two header buttons do the same
  directly, skipping the dialog. Backgrounding is greyed out for the second
  or two before a brand-new session's id is known — the tooltip says so.
- **Closing the window doesn't have to end anything.** The close-window
  dialog's third answer, **Keep Running (Hide Window)**, hides the window
  and leaves every session exactly as it is — panels, scrollback and all.
  The status icon's *Show Collins*, a session's notification, or simply
  launching Collins again brings it back. Where a status icon is present
  it's the dialog's default answer; the **When quitting with running
  sessions** preference can skip the dialog entirely (always ask, exit,
  background, or hide), and the menu's explicit Quit always really quits.
- The window title names the focused session, and the sidebar is
  **resizable**, its width remembered. On the next launch the app opens
  with no session — or, with **Reopen the last session** on (Preferences →
  Session behavior), with the one you had focused — and the window comes back at
  its last size.

## Prompt composer

A real text box for writing prompts — multi-line, spell-checked, floating
over the agent's terminal — for every prompt that outgrows the CLI's
one-line input:

- **Start typing and it's there** (on by default): type at an agent's empty
  prompt and the composer opens with what you typed already in it. The CLI's
  own `/`, `!`, `#` and `@` keep their keys, and so do dialogs and menus.
  `Ctrl+.` opens it deliberately; pressed again it closes the composer and
  puts the draft back in the agent's own input box, so nothing you wrote is
  ever stranded. If the agent has since left the terminal — a bare shell,
  where pasting a draft would run it — the draft is kept instead, and comes
  back the next time you open that session's composer. Kept on disk, so it
  survives closing the tab and quitting the app — as does a draft still in
  an open composer when Collins goes away. A semi-transparent
  **composer button** on the corner of each agent terminal opens it by mouse.
- **Send on Enter** — or flip *Enter sends composer text* off to make Enter
  a newline and `Ctrl+Enter` the send. `Shift+Enter` is always a newline.
  The box is drawn in the terminal's own font on purpose: the text is about
  to *be* terminal text.
- **Right-click a misspelling for corrections.** The menu offers
  alternatives for the word you clicked, not for wherever the cursor
  happens to be — turn *Right-click aims spell-check* off to leave the
  cursor untouched by a right-click. Spell-check needs libspelling
  installed (see [Getting started](/guide/getting-started)); without it the
  composer is a plain text box.
- **Drop images and files straight in.** Files land in the prompt as
  mentions; images get a strip of preview thumbnails above the text (click
  one to inspect it full-size) and go to the agent with the prompt.
- **Floating or docked.** The composer floats translucent over the
  terminal; its dock button turns it into a panel below the terminal
  instead, where it stays for that session's later visits. The *Composer in
  new sessions* preference can open it by itself the moment a session
  starts.
- **Model button**: the composer names the model the session is answering
  with, and clicking it opens the same switch menu as the footer's — pick a
  different model mid-draft and carry on writing.
- **It's also where a session begins.** A new session's first prompt is
  written in this same composer, on the [new-chat screen](#starting-sessions)
  that stands in for the console until Send.

![The composer floating over an agent terminal](/img/composer.png)

## Terminal panel

Every session has a second, plain-shell terminal area — no agent
auto-launched — below or beside the agent terminal, with **tabs of its own**:

- Toggle it with `Ctrl+J` or the buttons in the session footer; `Ctrl+Shift+K`
  clears it (screen and saved history). Shells open in the agent's **current
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
- **Right-click the footer's terminal button** to open the agent's live
  directory in your desktop's own terminal instead, for the times a window
  of its own beats a panel.

![The terminal panel below an agent session](/img/terminal-panel.png)

## Attachments panel

Pictures flow through a session constantly — screenshots the agent takes,
images it shows you, files you drag in — and the moment they scroll off the
terminal they're gone. The **attachments panel** keeps them: a per-session
gallery of everything the session has shared, on `Ctrl+'` or the slim
handle at the terminal's right edge.

- **Every picture, from every source**: images the agent shows with
  `show_image`, the ones it mentions by path or URL in its replies, and the
  files it hands over with its "send file" tool — those can be any kind of
  file, shown as a typed icon with the filename. Captions ride along where
  there was one; a picture found in prose carries a snippet of the text
  around it instead.
- **Reads like a chat**: oldest at the top, newest at the bottom. The handle
  wears a badge when something new has arrived that you haven't looked at.
- **Click a picture** to open it full-size in the lightbox, where the arrow
  keys walk the gallery; a file opens in your desktop's default app.
  **Right-click** a row for *Open With…*, *Show in Folder*, *Copy Path*,
  and *Remove From List*. A file that's no longer on disk stays listed, and
  says so.
- **Floating or docked.** The panel slides in over the terminal; its dock
  button makes it a panel tab beside the terminal instead, where it can be
  split and moved like any other. It **docks itself** the first time a
  session shows a picture, when the window is wide enough to spare the
  column (*Show the attachments panel automatically*, in Preferences).
- The list **persists per session** — reopen a session weeks later and its
  gallery is exactly as you left it. Forked sessions inherit the original's.

![The attachments panel docked beside a session: three cat photos with their captions, two files between them](/img/attachments-panel.png)

## Editor panel

A syntax-highlighted code editor lives beside the agent terminal — the
"read and fix what the agent just did" surface, not a general-purpose IDE:

- Toggle it with `F8` or the footer icon — one editor per session, full-height
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

- **Keyboard Bindings** (menu → *Keyboard Bindings*): every shortcut Collins
  has, rebindable — click a row, press the new chord. See
  [Keyboard Shortcuts](/guide/keyboard-shortcuts#customizing).

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
  it needs you, and unless told otherwise runs on the **model and
  permission mode its caller is using right now** (one exception: a
  bypass-permissions caller's siblings come up in acceptEdits, so an
  unattended session can't mint more of itself). Spawned sessions get
  these same tools, so they can spawn siblings of their own.
- **`read_terminal(terminal?, lines?)`** — read the terminal panel's tabs,
  text and scrollback, exactly as you see it — so "the error over there" is
  something the agent can just look at instead of asking you to paste it.
- **`run_in_terminal(command, terminal?)`** — type a command into an idle
  panel shell and run it, visibly, where you can watch it, interact with
  it, and keep the shell afterwards — a dev server, a REPL, a long build.

Each tool asks for permission the first time a session calls it, like any
other MCP tool, and **each has its own switch** in Preferences → *Built-in
MCP tools* (all on by default): a tool switched off isn't offered to the
sessions Collins starts from then on, and a session already running when you
flipped the switch is refused if it calls it anyway.

## Starting sessions

- **New session** (tab icon in the header, or `Ctrl+Shift+T`) starts a
  fresh agent session in the **visible session's project** — no dialog
  needed; with no session visible, it asks for a folder. The **Advanced**
  entry picks a model, a permission mode, or an extra directory
  (`--add-dir`), and **Continue** resumes the most recent session in a
  folder (`claude --continue`).
- **The first prompt is written on a new-chat screen**, not in the agent's
  console: the project's icon and name over the [composer](#prompt-composer),
  with a *Start in a new git worktree* checkbox under it (ticked or not as
  the project's setting says — see below). Nothing runs until you press
  Send; then the agent starts with your prompt as its first turn, and the
  tab is an ordinary session tab from there. `Ctrl+J` opens a terminal
  beside the screen just as it would beside the console.
- **Unsent screens are drafts.** As soon as there is text on the screen, or
  a terminal open beside it, it is kept: closing the tab or quitting Collins
  leaves a **Draft** row under the project in the sidebar (named after the
  prompt's first line, with a pencil mark; a screen kept only for its
  terminal is called *Draft* and keeps the agent's mark), and clicking that
  row brings the screen back with the text, the checkbox, and the terminal
  panel as you left them. Send spends the draft; the row's trash button
  discards it.
- With *Start new sessions in a git worktree* on, each new session works in
  a fresh worktree of its project, so it won't see uncommitted local
  changes; a launch that can't cut one (a repository with nothing committed
  yet, say) says so and starts in the project directory instead. If
  terminals were open beside the new-chat screen when a worktree launch was
  sent, Collins offers to `cd` them into the worktree once it exists — a
  terminal running a command is left alone.
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
  *Archive on claude.ai too*): a session you've remote-controlled from
  claude.ai, or teleported in from it, has a sibling on the web's session
  list, and archiving (or restoring) it here archives (or restores) that
  sibling as well, so the two lists stay in step. Best-effort: archiving
  locally never waits on the network, and a failure over there is logged,
  not surfaced. (Uses an undocumented CLI endpoint — see
  [How It Works](/guide/how-it-works#archiving-on-claude-ai).)
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
files** toggles, the app's **Dark / Light Mode** (system / light / dark), the
**language** (English, Magyar, Deutsch, Español, Français), the sidebar's
**Show folder paths in sidebar**, **Show Claude usage**, and **Auto-generate session
titles** toggles, the **status icon**, **Reopen the last session**, what to
do **when quitting with running sessions** (ask / exit / background / hide),
**Archive on claude.ai too**, and a switch for each of the **built-in MCP tools**
the agent can call — reachable from the sidebar menu or `Ctrl+,`.

A **search bar across the top** filters the whole screen as you type, and it
has the focus the moment preferences opens, so the way to a setting is to
type a word from it — a section heading, a word from a description
(*Ctrl+C*), or an option folded away inside a row (*Dracula*, *Magyar*).

![Preferences dialog](/img/preferences.png)
