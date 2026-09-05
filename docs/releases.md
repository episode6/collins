<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-09-04. Full change history: git log for this file.
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
- ✅ **Composer** — a spell-checked, multi-line prompt box that opens the moment you start typing, floating or docked, with dropped or pasted image attachments; an unsent prompt is a **draft** kept with its session across tab close and quit — or a sidebar Draft row, for a session not started yet
- ✅ **Session tools** — an in-app MCP server every launched session can call: rename itself, open a file, a diff or an image on your screen, notify you when it needs you, attach a pull request to its own row, spawn a sibling session, and read or drive the terminal panel
- ✅ **Desktop presence** — a status icon with an unread badge, close-to-hide (sessions keep running without a window), notifications wearing each project's own icon, an in-app notification center (cards, a header bell, a history sheet, a choice of sounds), a daily update check, Caffeine Mode
- ✅ **Theming** — light/dark plus selectable terminal color palettes
- ✅ **Localization** — English, Hungarian, German, Spanish, French
- ✅ **Multi-window**
- ✅ **Distribution** — Ubuntu PPA (`ppa:episode6/stable`), Fedora COPR (`episode6/stable`, RHEL 10 included), `.deb` + `.rpm` downloads, PyPI (`pipx install collins`), one-step tag-driven releases

### Exploring next

- 🔭 **Flathub** distribution
- 🔭 **AUR** package
- 🔭 **A better native chat** — a first-class chat experience for an agent session, beyond the terminal

## Changelog

### v0.1.3 — UNRELEASED

- **A git page beside the session.** `F6`, or a click on the footer's ⎇
  branch label, opens [hunk](https://hunk.dev) in a terminal of its own
  next to the agent's, with an extension Collins ships drawing two panels
  inside it: **commits** — the current branch with a *working tree* row,
  its parent, the default branch — where a click loads that commit or
  branch into the same window, and **files**, split into Unstaged and
  Staged on the working tree. `x` / `X` stage or unstage the hunk or file
  under the cursor, `A` / `U` all of them, `n` / `p` walk the commits, `P`
  (or a right-click) picks the parent branch; `Ctrl+1` / `Ctrl+2` /
  `Ctrl+3` jump to the unstaged, staged and whole-branch diffs. **Lines,
  too**: `v` anchors the cursor line in amber, hunk's `j` / `k` move, and
  `x` stages (or unstages) exactly the lines in between, across hunks of
  one file. `D` discards a hunk or range from the working tree after a
  confirmation (and restores a deleted file), `C` / `B` commit the index
  with a summary (and a body), and `F` commits it as a `fixup!` for an
  unpushed commit picked from a list, naming the `git rebase -i
  --autosquash` that folds it in. The page
  reloads by itself when the index or `HEAD` moves, and each session
  remembers whether it was open, what it showed and the parent you set. A
  machine without hunk gets an install card in the page's place; the
  branch label's copy moved to a right-click.
- **The git page repairs hunk's daemon directory, and says when the
  daemon is missing.** hunk 0.21 refuses to start its session daemon while
  `$XDG_RUNTIME_DIR/hunk-mcp` is readable by anyone but its owner, and 0.20
  created that directory at the umask — so an upgrade could leave every
  commit click landing nowhere, with no word why. Before each spawn the
  page now makes the directory owner-only when it isn't, and when the
  viewer still never registers with the daemon, a banner over it says so,
  names `hunk daemon serve` as the run that prints the reason, and offers
  a Retry.
- **A narrow git page walks its panels one at a time.** hunk fits both of
  the extension's panels beside the diff only from about 100 columns, and
  one from 73; below that the page used to show the diff alone with no
  way to the panels. Now the page opens one panel wide at its narrowest,
  and while it is narrower than both, the header grows a **back** and a
  **forward** button that step through three levels — the diff, the files
  panel, the commits panel — with `<` / `>` doing the same from the
  keyboard, and a click drilling down by itself: a commit clicked loads
  it and shows the files, a file clicked selects it and shows the diff.
  Wide enough for both, the buttons go and both panels show as before.
- **`show_diff`, a session tool for the git page.** The agent can put a
  change on your screen: `show_diff("unstaged" | "staged" | "branch" |
  <commit ref>, file?, line?)` opens the session's git page on that diff
  (revealed, never focused) and moves hunk to the file and line. The reply
  names what loaded and the hunk session id, and points the agent at
  `hunk session …` for everything else the viewer can do. Its switch sits
  with the other tools' under Preferences → *Built-in MCP tools*.
- **Preferences → Git.** A group for the git page: hunk's layout
  (automatic / split / stacked) and theme, whether working-tree reviews
  show untracked files, how many commits each group of the commits panel
  shows per *load more…*, and a default parent branch — the branch a
  session's diffs are measured against when no attached pull request names
  one; *Set parent branch…* in the page still overrides it. Every one of
  them reaches a page already open.
- **The status icon's glass empties when there's nothing to do.** The drink
  now stands for something waiting: the glass holds the coral pour while
  anything is unread, pours the barber pole while any session works, and
  stands empty when nothing is running and nothing is waiting — so a glance
  at the top bar says "all clear" without a badge to read. Open sessions
  that are simply idle don't fill it; the tooltip and the menu still count
  them.
- **Releases now include an `.rpm` download.** Each GitHub release attaches
  a binary noarch RPM next to the `.deb` — built from the same spec the
  COPR publishes, for installing directly on an RPM-based distro without
  enabling the `episode6/stable` COPR (which remains the maintained,
  self-updating channel on Fedora and RHEL 10).
- **Collins has a proper app page in GNOME Software.** The AppStream
  metadata Collins ships now carries a screenshot with a caption and the
  app icon's own light and dark accent colors, so software centers render
  a real page for the installed app instead of a bare name and summary —
  and every package build validates it. The summary, and the launcher's
  description and keywords, are translated into every language Collins
  speaks.
- **The new-chat effort picker reads the model's own default.** The CLI
  keeps an effort level per model (`/effort` saves it under
  `modelSettings` in `settings.json`, leaving any older top-level
  `effortLevel` standing), and the new-chat screen's effort picker was
  reading only the top-level key — so a default of *medium* set on the
  current model still showed as *High*. It now opens on the level the CLI
  would run the launch's model at, and picking a different model swaps
  the effort along with it: an effort chosen for the last model is let
  go, and the picker reads the new model's default.
- **A model or effort switch shows up as soon as it takes.** Picking a
  model or an effort level from the footer or the composer's pickers —
  floating over the terminal or docked below it — posted the right
  `/model` or `/effort`, but the button and the mark in its menu kept
  the old value until the session's next reply, which could be a long
  wait for a switch made between prompts. Collins now reads the CLI's
  own confirmation of the switch off the transcript, so the footer chip
  and both pickers move within a poll of the command running. A model
  chosen in the CLI's own `/model` picker still waits for the next
  reply, since the CLI prints its name rather than its id.

### v0.1.2 — 2026-08-30

- **The editor shows one column when it's narrow.** An editor column
  dragged to 500 px or narrower shows the file tree and the open file one
  at a time instead of squeezing both: picking a file shows it, a back
  button beside the tabs returns to the tree, and closing the last tab
  returns there on its own. Widen the column and both come back side by
  side, the tree at the width you left it. The threshold is *Single column
  when narrow* in Preferences → *Editor* (0 keeps the two columns at any
  width).
- **More notification sounds to choose from.** The *Sound* picker in
  Preferences → *Notifications* names the desktop theme's other sounds —
  *Bell*, *Complete*, *Message*, *Information*, resolved from the desktop's
  own theme the way *Default* is — and five short chimes Collins now
  ships, so the choice is the same on every desktop: *Zen*, *Soft* and
  *Glass* from UI SFX, *Confirmation* and *Pluck* from Kenney's Interface
  Sounds, all public domain (CC0). *Default*, *None* and *Custom…* are as
  before.
- **The notification card can be pinned light or dark.** A *Card theme*
  row in Preferences → *Notifications* — *Follow app*, *Light*, *Dark* —
  pins the in-app card's colors whatever theme the app wears, so a dark
  card over a light window can read the way a desktop notification does.
  Only the in-app card changes; desktop notifications stay the desktop's.
- **Collins tells you when a newer Collins is out.** Once a day the app
  asks GitHub for the latest release — through your `gh` login when it
  has one, anonymously over the public API otherwise — and when that
  release is newer than the one running, says so once: a card and the
  sound in Collins, a desktop notification away from it, and a row in the
  notification history either way, titled with the version. Clicking any
  of them opens the release page in your browser. The same release is
  never announced twice, and the launch that installed it retires the
  row. *Check for updates* in Preferences → *General* (on by default)
  turns the check off.
- **The new-chat pickers pre-select the CLI's default.** The model and
  effort menus on the new-chat screen no longer head their lists with a
  *Default (…)* row: the model the CLI's settings resolve to (`model` in
  `~/.claude/settings.json`, `ANTHROPIC_MODEL`; an `opus` alias marks the
  newest Opus in the catalog) and the `effortLevel` they name are marked
  on the lists themselves, and the buttons read their names. Nothing is
  passed on launch until something is picked, as before; with no default
  set, the button reads a bare *Default* and no row is marked.
- **The Advanced new-session dialog is gone.** Its *New … session
  (advanced)…* entry left the New Session menu along with the dialog it
  opened: the model and effort it offered are picked on the new-chat
  screen now, and a permission mode or an extra directory (`--add-dir`)
  is a `claude` flag to type in the tab's terminal. *Continue last …
  session…* stays.
- **An effort picker beside the model picker.** The composer's Send row
  and the new-chat screen's both carry a second menu for the CLI's effort
  level (*Low* to *Max*, the `--effort` / `/effort` dial); the footer's
  model chip has the level beside it, and a click on either opens the same
  menu. On a running session the chip and the button read the level the
  transcript last answered at, and a pick posts `/effort` the way the model
  menu posts `/model`; on the new-chat screen the pick is the launch's
  `--effort`, opening on the `effortLevel` that `/effort` saves to the
  CLI's settings (`CLAUDE_CODE_EFFORT_LEVEL` outranking it), and it is
  kept with the draft like the model pick. The levels on offer
  are the ones the model in question takes, as the Models API reports
  them, so a level the CLI would refuse (*Extra high* on Opus 4.6, any on
  Haiku 4.5) is greyed out rather than sent. The screen's worktree
  checkbox reads *New git worktree* now, to make room.
- **Session titles are generated at low effort.** The headless `claude -p`
  that names a new session passes `--effort low`: a five-word summary
  gains nothing from thinking, and the pin keeps an `/effort xhigh` saved
  in your settings from being spent on every title.
- **In-app notifications: a card, a bell, and a history.** A session that
  speaks up from a tab you aren't looking at — a `notify_user` message or
  a terminal bell — now shows a **card** at the window's top right and
  plays the notification sound while Collins is focused; the desktop
  notification of old now covers the times no Collins window is active,
  and the tab on screen still gets nothing but its flash. Cards stack
  three deep, wait while the pointer is over them, and click through to
  their session; the × takes down just the card. A **bell** in the header
  bar wears the same unread count as the tray icon and the dock badge,
  and clicking it — or `Ctrl+Shift+B` — opens the **notification
  history**: a sheet sliding in from the right edge, every notification
  newest first under *Unread* and *Earlier*, each row wearing its
  project's icon, its kind's mark and its age, with a bell rung again
  coalescing onto its row. A click goes to the session and marks the row
  read (clearing the matching desktop notification with it), right-click
  offers *Mark read* and *Remove*, and *Mark all read* / *Clear* sit in
  the sheet's header; the history survives restarts, capped at 200 rows
  and two weeks. Finished runs land there as rows too — the sidebar's
  green pulses, counted — and **Announce finished runs** (off by default)
  sends them out as cards, the sound, and desktop notifications like the
  rest. Preferences gains a **Notifications** group for all of it: the
  in-app switch, the *Sound* picker, *Bells from other sessions*, and the
  announce switch. The `notify_user` tool's reply now tells the agent
  which of the three the user got — a card, a desktop notification, or
  nothing because they were already looking. The sound plays through
  GStreamer (`gir1.2-gstreamer-1.0`, a Recommends on every package);
  without it, the desktop's beep stands in.
- **The status icon shows when agents are working.** While any session is
  busy, the drink in the panel icon's glass turns into the sidebar's blue
  barber pole — the busy row's guide line, standing still — and the coral
  pour comes back the moment the last run stops. One change of picture per
  transition, not an animation: the tray protocol has none, and each frame
  would cost the desktop a D-Bus round trip.
- **The new-chat screen starts empty sessions again.** With nothing in
  its box, the screen's Send button reads *Empty Session*; pressing it — or
  Enter (`Ctrl+Enter`, if that is your send key) — launches the agent with
  no prompt, waiting at its own input box the way a session opened before
  the screen arrived. An empty Send used to do nothing.
- **The composer always picks up at the end.** Every way into the composer
  now appends and leaves the cursor at the end of the box: the prompt cut
  out of the CLI's input box on open lands after a restored draft instead
  of above it, and a keystroke typed at the terminal continues the box
  wherever a docked composer's cursor was left. Before, the cut went in at
  the top with the cursor between it and the draft.
- **Paste an image into the composer.** An image on the clipboard — a
  screenshot tool's copy, a browser's *Copy image* — pasted into the
  composer (floating, docked, or on the new-chat screen) lands the way a
  dropped one does: saved as a PNG under `~/.cache/collins/dropped-images/`
  (a `paste-…` file beside the `drop-…` ones, pruned after a week), its
  mention typed at the cursor, a thumbnail in the strip. GTK's text box
  pastes text and nothing else, so an image used to paste nothing at all.
  Files copied in a file manager paste as their mentions too; plain text
  pastes as it always did.
- **Collins is on Fedora.** `sudo dnf copr enable episode6/stable && sudo dnf
  install collins` — the `episode6/stable` COPR builds every release for
  every current Fedora (new ones join as Fedora branches them) and for RHEL
  10 and its rebuilds, and upgrades with the rest of the system, the way the
  PPA does on Ubuntu. The RPM (`packaging/fedora/collins.spec`) carries the
  same GTK 4.10 / libadwaita 1.5 floors as the `.deb`, with libspelling as a
  weak dependency; every PR builds and installs it in a Fedora container,
  and a tag uploads the SRPM and waits for every chroot to go green. A
  Fedora install that came from PyPI or a checkout sees *Add the Fedora
  COPR…* in the sidebar's ☰ menu until the repository is configured — the
  Ubuntu PPA item's twin, with the same run-it-here dialog (not on the
  image-based variants, Silverblue and friends, where the command it
  offers wouldn't install anything).
- **Checkout the default branch from the project row** — next to *Git
  pull* in a project's right-click menu, *Checkout main* (or whatever the
  remote calls its trunk) switches the project folder back to it. Live
  only while some other branch is checked out; git's own refusal (local
  changes, the branch in another worktree) lands in an error dialog.
- **The headless runs carry none of your skills, MCP servers, or tools.**
  Session titling, icon generation, and the login repair each run a
  `claude -p`, and each used to load what an interactive session does — the
  built-in tool set, your skills, every MCP server in `~/.claude.json` —
  into a prompt that wants five words back. They now pass
  `--strict-mcp-config --tools ""`: on CLI 2.1.251 a one-line prompt on
  Haiku went from about 23k input tokens to 8k. The *Token use* description
  (in Preferences and the welcome dialog) and the guide say so. `--bare`
  would trim the global `CLAUDE.md` too, but drops the OAuth login the
  repair exists to renew, so it stays off.
- **A welcome dialog that says what spends tokens.** The first launch of a
  fresh install — and, once, of every install that predates this release —
  opens on a **Before you start** dialog: the *Token use* rows and the
  *Built-in MCP tools* switches, the same ones Preferences shows, so what
  runs Claude on your behalf is disclosed before the first of those runs
  happens, with the switch for each right there. Toggles write at once;
  Continue (or Escape) records the dialog as seen, and the expired-login
  repair now waits until it has been — the launch check and the usage
  panel's own ask alike. Its first group is the CLI: a row
  naming the `claude` in use, or — on a launch that can't find one — the
  path ask that used to be a dialog of its own, prefilled, with its live
  verdict, Browse and Quit, and no way past it until the path validates.
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
  name over the composer, with a *New git worktree* checkbox
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
- **Mark ready & merge, one right-click away.** A right-click on a draft
  PR's *Ready* button offers the stops past it: *Mark ready & merge* when
  the checks are green, *Mark ready & merge when checks pass* while they
  are pending or red, and — beside the immediate merge, when there is a
  session to put away — *Mark ready, merge & archive session*. A
  conflicting draft is offered no shortcut at all, exactly as an open
  conflicting PR is offered no merge.
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
