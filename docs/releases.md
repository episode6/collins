<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-08-27. Full change history: git log for this file.
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

### v0.1.2 — UNRELEASED

- **A Token use group in Preferences, with a login-renew switch.** The
  settings that run Claude on your behalf now sit together, directly
  under General: the *Session title model* and *Icon generation model*
  pickers (moved from Session behavior and General), a new **Auto-renew
  the Claude login** switch (on by default) for the throwaway `claude -p`
  that repairs an expired login at launch or after a refused usage poll —
  off, the usage panel says the login expired and leaves running
  `claude` to you — and the *Model list* row, whose subtitle now says it
  is free. The group's description says what the rows have in common:
  each runs a headless `claude -p` from a scratch directory, against your
  subscription's limits, without a prompt from you. *Built-in MCP tools*
  moves up from the bottom of the page to follow it, and its description
  now discloses that every enabled tool's definition rides in each
  session's context, that `read_terminal` sends the panel's text into the
  conversation, and that a session `start_session` starts is titled like
  any other. Search finds every moved row where it was ("oauth",
  "auto-generate", "quota"…).
- **The model pickers gain a None option.** *Session title model* and *Icon
  generation model* both list **None** first, ahead of the automatic default
  and the catalog. For titles it replaces the *Auto-generate session titles*
  switch (an install that had it off comes up with None): under None new
  sessions keep the free local title — the first words of their prompt —
  and nothing goes to a model, while right-click → *Regenerate name* stays
  available and runs on the automatic default; the menu item now names the
  model it will run, *Regenerate name (Haiku 4.5)*. For icons None is the
  new default: the Generate Icon dialog no longer runs the moment it opens
  but waits for a model to be picked from its drop-down (*Choose a model…*)
  and **Generate** clicked; with a model set it generates on open, as
  before.
- **A big draft survives the composer's round trip.** Closing the floating
  composer typed its draft back into the agent's input box as one chunk,
  and past a few lines the CLI folded that into a `[Pasted text #1 +12
  lines]` stand-in — which the next open then cut into the composer in
  place of the draft, gone for good once the stand-in was erased. The
  draft now goes back as a series of pastes each small enough to show in
  full, so the box holds every line of it; should the CLI fold one anyway,
  Collins notes which stand-in holds which piece and puts the piece itself
  back in the composer. A stand-in Collins didn't make — a paste of your
  own, an image — is one it can't read, so the composer refuses to open
  over it rather than cut it.
- **A folded PR description shows the start of its first paragraph.** The
  collapsed description keeps whole lines until its character budget runs
  out, and the line that would overrun the budget used to be dropped
  entirely — so a description in the usual `## Why` shape, a heading and
  then one long paragraph, folded to nothing but the heading and an
  ellipsis. That line is now cut to the room left instead, at a word
  boundary and never through a link, code span or URL, so the preview reads
  as the opening of the description it stands for.
- **The tab bar is hidden by default.** The sidebar is where sessions are
  switched, and the window title names the active one, so the row of tabs
  under the header was chrome most windows never needed; the header's
  toggle shows it again, and a window that had it showing keeps it. The
  docs' screenshots are recaptured against the app as it is now — the
  new-chat screen, project icons, PR marks — and the new-chat screen gets
  a picture of its own.
- **A PR page refresh only touches what changed.** Every refresh — the
  Refresh button, the page coming back into view, a check finishing, a
  run ending — used to rebuild the whole page from the new reply, which
  reset the scroll, closed the description's and the checks list's *Show
  more*, collapsed every diff you had expanded and dropped the keyboard
  wherever GTK saw fit, usually while you were reading. The page now
  patches itself instead: cards, check rows, file sections and list rows
  are keyed, and one the reply describes as it did last time is left
  exactly as you had it; a check going green swaps that one row, a new
  comment lands at the end of the timeline, a reply on a file lands under
  that file's diff without rebuilding the diff, and the scroll stays
  anchored on whatever was in view. An edited description or a checks
  list grown past the fold does get rebuilt, open if you had it open.
- **An expired Claude login repairs itself.** The usage panel and the
  model pickers ride the OAuth token the `claude` CLI stores, and only the
  CLI can refresh it — so when the token is found dead, at launch or when
  a usage poll under a running app comes back refused (the app outlived
  its token, or the token was revoked server-side), Collins now runs one
  throwaway headless `claude -p` prompt in the background, then re-fetches
  the usage bars and, when a models query had already failed, the model
  catalog. Attempts are single-flight and cooled down — an hour, doubling
  with every consecutive failure up to a day — so a login no run can fix
  never turns into a subprocess-per-poll. Before, the panel
  sat on *Claude login expired — run claude to refresh* until you ran the
  CLI yourself.
- **New sessions open onto a new-chat screen.** The project's icon and
  name over the composer, with a *Start in a new git worktree* checkbox
  (seeded from the project's setting) at the left of the composer's Send
  row and a model picker at its right; the agent starts
  when the first prompt is sent, with that prompt as its first turn and
  the picked model as its `--model`. The picker opens on *Default*, named
  after the CLI's own default as its settings files resolve it (the
  `model` that `/model` saves to `~/.claude/settings.json`, a project's
  `.claude/settings.json`, `ANTHROPIC_MODEL`), and a pick is for that
  session only — the default is left alone. `Ctrl+J`'s terminal
  opens beside the screen as it would beside the console. A screen with
  text on it, or a terminal open beside it, is kept as a **draft**: closing
  the tab or quitting leaves a Draft row under the project in the sidebar,
  and clicking it brings the screen back — text, checkbox, model pick and
  terminal panel included; the row's trash button discards it, closing the open screen
  with it. When terminals were open beside a worktree launch, Collins
  offers to `cd` them into the worktree once it exists.
- **A click on a project header starts a session there.** From the title
  on, a click opens the new-chat screen for that project, the same as its
  + button; everything before the title — the strip holding the icon, full
  row height, the caret under the pointer — is what folds and unfolds the
  group now, and ←/→ on a focused header do the same from the keyboard.
  Favorites, with nowhere to start a session, still folds on a click
  anywhere.
- **The PR page's tabs link back to GitHub.** A right-click on
  *Conversation* or *Files* opens that view of the pull request on
  github.com in your browser — the escape hatch for whatever the native
  page doesn't render.
- **The PR page's Checks list folds instead of scrolling.** Past four
  checks the rest wait behind a "Show more" button, the same step the
  description takes, with the failing and pending rows kept on top. The
  little in-place scroller is gone.
- **No more busy pointer after bringing Collins back from the tray.** On
  GNOME, double-clicking the status icon (or picking Show Collins, a
  session, or New Window from its menu) left the shell's busy cursor up for
  fifteen seconds: the host hands over an activation token that doubles as a
  startup-notification sequence, and Collins was dropping it instead of
  raising the window with it. The window now comes up on that token.
- **The PR page no longer selects its description by itself.** A click on
  "Show more", in the description or on a thread's Reply, followed by any
  background re-read of the page, left the whole description highlighted —
  GTK re-placing the keyboard focus onto the first label of the rebuilt
  page, which selects itself on focus. A rebuild now parks the focus on the
  page's scroller first, and gives it back to the comment box or the reply
  editor being typed in, whose cursor a background refresh used to throw out.
  The other way in — switching to another tab and back after a click in the
  description — is closed too: GTK's select-a-label-on-focus is off across
  the app, so text is only ever selected by dragging over it.
- **A PR page opened behind another tab gets its full width.** A panel
  page that opens itself in a background tab — an agent attaching its
  pull request while you read another session — used to come up squeezed
  to its minimum width when that tab was next selected, however much room
  the terminal had to spare. The column now takes its proper size the
  moment the tab is first shown.
- **Project headers are centred.** The icon, title, count and + button of
  a sidebar group header sat a few pixels below the middle of the row —
  plainest under the pointer, where the row's highlight framed a + that
  rode low in it. The space that separates one group from the last row
  of the one above now sits outside the row instead of padding the top of
  it, so the row's contents are centred in its highlight.

### v0.1.1 — 2026-08-22

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

- **Every keyboard shortcut is rebindable.** The sidebar menu's new
  *Keyboard Bindings* dialog lists each of them, grouped; click a row and
  press the new chord and it is live in every window and tab at once.
  Backspace unbinds, a chord another action holds can be moved over, and
  each changed row — or all of them — resets to its default. The terminal's
  copy/paste/find/zoom keys and Shift+Enter are included, as are a few
  actions that ship without a key (search sessions, swap the panel's sides,
  focus the editor). Stored under `settings.keybindings` in `state.json`.
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
- **Ctrl+click finishes a wrapped link off the transcript.** A URL or file
  path the CLI broke across rows is stitched back together from the screen
  when the geometry is unambiguous; when it isn't — the CLI wrapped narrower
  than the terminal, or a link fills a row exactly — the click now asks the
  session's transcript, which has the link unwrapped, and opens it when the
  rows around the click spell that link out. Finished turns only; a link
  still streaming in keeps the screen-only behaviour.
- **The Files view shows a changed image as an image.** A PR page's file
  list used to say `Binary files differ` for a picture and print path data
  for an SVG; now `.png`, `.svg`, `.gif`, `.jpg`, `.webp` and friends render
  on a transparency checkerboard — before beside after for a modified file,
  a click opening the lightbox at full size — with an SVG's real patch kept
  under its preview. *Show embedded images* off restores the plain patch
  and downloads nothing.
- **The model list is cached for a day, and across restarts** — saved to
  `~/.cache/collins/models.json`, so the first picker of a run opens on real
  models instead of the CLI's aliases and a network wait. A failed query
  never evicts the last good list, and a *Model list* row in Preferences
  dates it and refreshes it on demand. A one-model answer is served but
  expires at once, so a cut-short page never sits in the pickers all day.
  The pickers also **group the catalog by family** — any newer,
  unrecognized tier first, then Fable, Opus, Sonnet, Haiku — so like sits
  with like.
- **The composer's spell-check menu aims at the word you clicked.**
  libspelling builds its corrections from the insertion cursor and GTK4's
  right-click never moved it, so the menu offered fixes for wherever the
  caret was parked — usually the end of what you just typed. The click now
  moves the cursor first, the way gspell did.
- **A notification comes down when what it says stops being true.** The
  first-hide notice is withdrawn when Collins is brought back by any route,
  and a `notify_user` banner goes away with its session's unread flag —
  so Ubuntu Dock's notification counter stops badging the launcher with a
  `1` nobody can find.
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
