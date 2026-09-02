<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-09-02. Full change history: git log for this file.
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
  blue") until it's right; *Save* writes the file to the project root. The
  dialog starts on the *Icon generation model* preference, which is
  **None** by default: it opens waiting for you to pick a model from its
  drop-down and click **Generate**. With a model set, it generates the
  moment it opens.
- **Repository upkeep from the project header**: the right-click menu's
  **Git pull** pulls the project folder's checkout, and **Checkout main**
  (or whatever the remote calls its trunk) switches it back to the default
  branch — offered only while some other branch is checked out, with git's
  own refusal (local changes, a branch held by a worktree) shown in an
  error dialog.
- **Live updates** — new sessions appear the moment they're created, via a
  filesystem watch, and existing rows update in place without jumping
  around. A just-started session shows a **"New Thread"** placeholder row
  until the agent writes its transcript. The header's refresh button
  re-reads the list and every pull request on demand, and wears the same
  barber pole as the rows while any session works.
- **Search the list** from the header's magnifier: type, and only the
  matching sessions — and the projects holding them — stay on screen. It
  ships unbound; give it a chord in *Keyboard Bindings* if you want one.
  `Ctrl+K`'s switcher is the one-jump version of the same thing.
- **Add a project** with the sidebar's **+**: pick a folder, answer the
  trust prompt once, and it gets a header of its own with no sessions in
  it yet. A project emptied by archiving can be kept the same way; *Remove
  project from sidebar* in the header's menu is what finally drops one.
- A **Claude usage panel** under the session list: your subscription
  limits — the 5-hour session window, weekly limits, extra-usage credits —
  as progress bars with reset countdowns, read from the `claude` CLI's own
  login and refreshed every 5 minutes. Its heading folds the bars away,
  its refresh button asks again on the spot, and Preferences (*Show
  Claude usage*) removes it.

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
**Regenerate name** re-runs the model for one session, and the menu item
names the model it will run (*Regenerate name (Haiku 4.5)*). The **Session
title model** preference has a **None** option that turns the model runs
off (it replaced the *Auto-generate session titles* switch): under None
sessions keep the free local title, and *Regenerate name* still works, on
the automatic default. A manual rename always wins. Claude names sessions
for itself too — the **Follow Claude's own session names** preference (off
by default) makes the sidebar adopt those names as they land in the
transcript.

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
  with and the **effort level** it answered at, the agent's live working
  directory (click to copy), and the current **git branch** (⎇ — click to
  open the [git page](#git-page), right-click to copy), plus the
  terminal-panel buttons.
- **Footer apps**: name any installed application in Preferences →
  *Footer apps* and every session's footer grows a button that opens the
  session's live directory in it — your editor, a file manager, a git GUI.
  They're stored as desktop-file IDs, so names and icons follow the app.
- A **tab bar** under the header is there for anyone who wants it — the
  header's pages button toggles it, off by default, since the sidebar and
  the window title are how Collins expects you to move between sessions.
- **The model is one click from switching.** The footer's model name is a
  menu: every model your login can use, fetched from the Models API with
  the CLI's own token, the current one marked. Pick another and the
  session gets the CLI's `/model` command — the footer follows once the
  agent answers with it. The same menu sits in the composer's chrome, so
  you can change model halfway through writing a prompt without losing the
  draft. (Copying the full model id lives in the menu too.)
- **So is the effort level.** The level beside the model name is a menu of
  the CLI's effort levels (*Low*, *Medium*, *High*, *Extra high*, *Max*),
  the current one marked; a pick sends `/effort`, and the footer follows
  the agent's next answer. Levels the current model can't take — the
  Models API says which — are greyed out. The same menu sits beside the
  composer's model button.
- **Pull request chips** trail the branch: one per PR the session has
  opened, each with its CI or merge mark, and each opening that PR's **page
  beside the session** on click — a native view of the description, checks,
  timeline, and diff, whose *Conversation* and *Files* tabs each open their
  github.com counterpart on a right-click. The description and the checks
  list each fold behind **Show more** — the description showing the start
  of its first paragraph, the checks keeping failing and pending rows on
  top — and the page carries its own action button: **Ready** on a draft,
  **Merge** or **Auto-Merge** on an open PR (**Disable Auto-Merge** once
  GitHub has it armed). Right-clicking a draft's
  *Ready* offers the stops past it: *Mark ready & merge* (or *…when checks
  pass*), *Ready & Auto-Merge*, and *Mark ready, merge & archive session*. The caret beside them lists every
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
one-line input. And unlike that input box, which dies with the process, it
keeps what you haven't sent: a half-written prompt is a **draft** that
belongs to its session, and it is there when you come back to it.

- **Start typing and it's there** (on by default): type at an agent's empty
  prompt and the composer opens with what you typed already in it. The CLI's
  own `/`, `!`, `#` and `@` keep their keys, and so do dialogs and menus.
  `Ctrl+.` opens it deliberately; pressed again it closes the composer and
  puts the draft back in the agent's own input box, so nothing you wrote is
  ever stranded. A semi-transparent **composer button** on the corner of
  each agent terminal opens it by mouse.
- **Drafts wait for you.** Whatever is in the composer when you leave stays
  with the session: close the tab, quit Collins, come back a day later, and
  the draft is back in the box the next time you open that session's
  composer — by `Ctrl+.`, by the button, or by starting to type. Whatever
  arrives with the open — the keystroke that raised it, text already
  typed-but-unsent in the CLI's own box — goes in after the draft, and the
  cursor sits at the end, so you carry on from where the box now ends
  rather than from where it began. The same holds when the agent has left
  the terminal under an open composer — a bare shell, where pasting a draft
  would run it as commands — so closing the composer keeps the draft instead
  of typing it back. Drafts are saved to disk with the rest of the session's
  state (`~/.config/collins/state.json`), never to the agent's transcript,
  and a draft that has been sent or taken back into the agent's input box is
  spent — it won't turn up a second time. A **new session's** first prompt
  works the same way from the other end: it is written in this composer on
  the [new-chat screen](#starting-sessions), and until you send it the
  screen is a **Draft** row in the sidebar, ready to reopen.
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
- **Drop or paste images and files straight in.** Files land in the
  prompt as mentions; images get a strip of preview thumbnails above the
  text (click one to inspect it full-size) and go to the agent with the
  prompt. Pasting works the same way: an image on the clipboard (a
  screenshot tool's copy, a browser's *Copy image*) is saved as a PNG under
  `~/.cache/collins/dropped-images/` — where dropped images go too, pruned
  after a week — and that copy is what the prompt mentions; files copied in
  a file manager are mentioned in place.
- **Floating or docked.** The composer floats translucent over the
  terminal; its dock button turns it into a panel below the terminal
  instead, where it stays for that session's later visits. The *Composer in
  new sessions* preference can open it by itself the moment a session
  starts.
- **Model and effort buttons**: the composer names the model the session
  is answering with and the effort level it answers at, and clicking either
  opens the same switch menu as the footer's — pick a different model or
  level mid-draft and carry on writing.
- **It's also where a session begins.** A new session's first prompt is
  written in this same composer, on the [new-chat screen](#starting-sessions)
  that stands in for the console until Send — worktree checkbox, model
  picker and effort picker on its Send row.

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
  into (a PR view, the git page, the attachments gallery, a docked
  composer) remembers a size of its own, kept apart from the shells'.
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
  files it hands over with the CLI's own `SendUserFile` tool — those can be any kind of
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
- **One column when narrow.** Drag the editor column to 500 px or narrower (the
  *Single column when narrow* preference) and it shows the file tree and the
  open file one at a time: pick a file to see it, with a back button beside
  the tabs to return to the tree; widen it again and both come back side by
  side, the tree at the width you left it.

![The editor panel beside an agent session](/img/editor-panel.png)

## Git page

What the agent has changed, beside the terminal it is changing it in. The
page is [hunk](https://hunk.dev) — the terminal diff viewer — running in a
terminal of its own under a one-row header, one page per session:

- **Three loads.** The **unstaged** changes (the working tree against the
  index), the **staged** changes (the index against `HEAD`), or the whole
  **branch against its parent** (`main...HEAD`). The parent is the base
  branch of the session's newest pull request once its PR page has been
  opened — a stacked PR is measured against the branch it stacks on — and
  otherwise the repository's default branch; either way the local branch
  when there is one, else the remote's. `Ctrl+1` / `Ctrl+2` / `Ctrl+3`
  switch between them from anywhere in the page, and so do the header's
  three buttons.
- **The header says what you're looking at**: the branch, then a
  breadcrumb — *working tree · unstaged*, *working tree · staged*,
  *feature vs main* — that the page's tab title follows (*Git · staged*).
  It reports what hunk has loaded, not what was last clicked: a `hunk
  session reload` run from a shell or by the agent shows up in it within a
  couple of seconds, and a load that isn't one of the three (`show HEAD`)
  is named as hunk names it, with none of the buttons down, until a click
  on one takes the page back. A refresh button reloads the same diff; the
  ✕ closes the page.
- **It keeps itself fresh.** Every two seconds the page compares the
  index, `HEAD` and the parent branch against what it last loaded, and
  reloads when any of them moved — an agent staging, committing or
  rebasing shows up without a keypress, and a session that finishes a
  turn is checked on the spot. A session that steps into a worktree takes
  the page with it.
- **Two ways in**: `F6` (pressed while the cursor is in the page, it
  closes; from anywhere else it opens or fronts it), or a click on the
  footer's **⎇ branch** label. A fresh page opens on the unstaged changes
  while anything in the tree is dirty, and on the staged ones when only
  the index is. Outside a git repository there is nothing to open, and
  the terminal says so.
- **Hunk's own keys still work** — it is the real program, in a real
  terminal, so its navigation, search and `r` reload are all there. The
  page holds `Esc` for it, and the terminal zoom chords apply.
- **No hunk, no error.** A machine without hunk (or with one older than
  0.20) gets a card in the page's place: the three install lines,
  click-to-copy, and a *Check again* button. Hunk exiting gets a *Reopen*
  card; a directory that stops being a repository, a card saying so.
- Each session remembers whether its git page was open, where it sat, and
  which of the three loads it showed — restored on the next launch, hunk
  starting the moment the page is first shown.

## Knowing what's happening

- **Notifications the session raises itself** — the agent calls Collins'
  `notify_user` tool when it wants you back (see [Tools a session can
  call](#tools-a-session-can-call)), and where the notification lands
  depends on where you are. In Collins but in another session, a **card
  slides in** at the top-right of the window, under the header bar: the
  project's icon, the session's name, two lines of the message, and the
  **notification sound** (the desktop's own message sound by default —
  Preferences → *Notifications* offers the desktop's other sounds too,
  its *Bell*, *Complete*, *Message* and *Information*; five short chimes
  Collins ships, all public domain; any file of yours; or none). The card
  follows the app's light or dark unless *Card theme* pins it — a dark
  card over a light window reads the way a desktop notification does.
  Click anywhere on it to go there; the × dismisses the card and leaves the row
  waiting in the history. Away from Collins, it is a **desktop
  notification** titled with the session, so clicking it jumps straight to
  that tab. Looking at that very session already, nothing pops up at all —
  the message goes straight into the history, and the tool tells the agent
  so. The card and the desktop notification both flag the session's sidebar
  row until you visit it, and both wear the project's own `project-icon.svg`
  where the project ships one. Nothing is
  guessed from a quiet terminal: a notification means the agent asked for
  you — unless you turn on *Announce finished runs*, which notifies on
  every finish too.
- **Bells from other sessions** ring the same way: a terminal bell (`\a`,
  from the agent or from a `make` in a session's panel shell) in a session
  you aren't looking at is a card and the sound in Collins, a desktop
  notification saying *Rang the bell* when Collins isn't focused, and one
  coalesced row in the history however many times it rings. The selected
  session's bell stays the desktop's beep — a bell you were there for is
  not history — and *Bells from other sessions* in Preferences turns the
  rest back into beeps.
- **A newer Collins** is announced the same way. Once a day the app asks
  GitHub for the latest release — through your `gh` login when it has one,
  anonymously over the public API otherwise (no token, no account) — and
  when that release is newer than the one running, says so once: a card
  and the sound in Collins, a desktop notification away from it, and a row
  in the history either way, titled with the version. Clicking any of them
  opens the release page in your browser. The same release is never
  announced twice, a launch that has caught up retires the row, and
  *Check for updates* in Preferences → *General* turns the whole check off.

![A notification card over a session](/img/notification-card.png)
- **The bell in the header** wears the unread count — the same number the
  status icon and the dock badge show — and opens the **notification
  history**: a sheet that slides in over the session from the right edge
  (`Ctrl+Shift+B`, or the bell). Every finished run nobody has looked at is
  a row there, wearing the sidebar's green pulse, and it leaves when you
  visit the tab, exactly as the pulse does; *Unread* rows sit above
  *Earlier*. Clicking a row goes to its session and marks it read — the
  sheet stays open, so you can work through a morning's worth — and
  right-click offers *Mark read* and *Remove*. *Mark all read* and *Clear*
  do what they say, except that a finished run's row is the green flag's to
  remove, not yours. Opening the sheet reads nothing on your behalf.

![The notification history sheet, open over a session](/img/notifications.png)
- **Session details** (right-click → *Details…*): message and tool-call
  counts, models used, token totals, timestamps, transcript size — plus a
  **recent activity** peek of the last messages, so you can identify a
  session without resuming it, and the MCP servers it used.

![Session details dialog](/img/session-details.png)

- **Replay…** (right-click) opens a past session's transcript as chat
  bubbles in a tab of its own — step through it turn by turn, or let it
  play — so an old session can be read end to end without resuming it.

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
- The icon **wears an unread badge**: it counts unread notifications — the
  same number as the bell in the header bar. Every session that finished a
  run nobody has looked at yet is one (the sidebar's green pulse, counted),
  and so is every message or bell in the history nobody has gone to — so a
  session that called for you from another tab counts twice until you visit
  it, once for the message and once for the flag it put on the row. A
  flagged session that goes back to work drops out of the count while the
  run lasts (it isn't waiting on you) and comes back the moment the turn
  ends. Sessions that are merely *working* never light the badge, but the
  tooltip carries both counts for the curious. The dock badge, where the
  desktop has one, shows the same number and lives with the status icon:
  off when the icon is off. The bell is the one place the number is always
  on.
- The glass says at a glance what the badge says in numbers. While any
  session is **working**, the drink turns into the sidebar's blue **barber
  pole** — the same stripes as a busy row's guide line, standing still. With
  nothing working, the glass holds the **coral drink** while anything is
  unread, and stands **empty** when nothing is running and nothing is
  waiting — open-but-idle sessions don't fill it. It's a change of picture,
  not an animation: the tray protocol has none, and re-sending frames would
  cost the desktop a round trip per frame.
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

- **`notify_user(message)`** — a notification titled with the session:
  a card inside the window while you're in Collins looking at another
  session, a desktop notification while you're away, and straight into the
  notification history when you're looking at that session already —
  the reply tells the agent which of the three happened ("The user was
  notified in Collins.", "…on their desktop.", or "The user is looking at
  this session; the message is in their notification history."). Clicking
  either raises the tab, and the sidebar row stays flagged so a
  notification you missed is still waiting when you get back.
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

- **New session** (tab icon in the header, or `Ctrl+Shift+T`) opens a
  new-chat screen in the **visible session's project** — no dialog
  needed; with no session visible, it asks for a folder. The button's
  menu also holds **Continue last Claude Code session…**, which resumes
  the most recent session in a folder (`claude --continue`), a one-off
  launch of the visible project with its worktree setting inverted, and
  **New chat (scratch folder)**.
- **Chats** is a pinned virtual project for work that has no repository:
  *New chat* on its header — or *New chat (scratch folder)* in the New
  Session menu — starts a session in a throwaway directory under
  `~/.local/share/collins/chats/`, pre-trusted, so a one-off question
  doesn't need a project to live in.
- **The first prompt is written on a new-chat screen**, not in the agent's
  console: the project's icon and name over the [composer](#prompt-composer),
  with a *New git worktree* checkbox at the left of its Send
  row (in a git project; ticked or not as the project's setting says —
  see below) and a
  **model picker** and an **effort picker** at its right, where a running
  session's model and effort menus sit. The model
  picker lists the same catalog the session's model menu does, and opens
  pre-selected on the CLI's own default — the model its settings resolve
  it to (`~/.claude/settings.json`'s `model`, the key `/model` writes,
  with a project's `.claude/settings.json` or `ANTHROPIC_MODEL` taking
  precedence the way the CLI has it), marked in the list and named on the
  button; an alias like `opus` marks the newest Opus. When nothing sets
  one, the button reads a bare *Default* and no row is marked. A pick is
  for this session alone: it is passed as `--model` on launch and the
  default is left as it was; with nothing picked nothing is passed, so
  the session runs on whatever the CLI resolves at that moment. The effort
  picker works the same way for `--effort`: it opens on the level the CLI
  keeps for the model the launch will run on — the `modelSettings` entry
  `/effort` saves, else a top-level `effortLevel`, or
  `CLAUDE_CODE_EFFORT_LEVEL` over both — and the levels on offer — *Low*
  to *Max* — are the ones that model takes, as the Models API reports
  them; a level the model can't take is greyed out. The effort follows
  the model: picking a different model lets any effort pick go, and the
  picker reads the new model's own default. Nothing runs
  until you press Send; then the agent starts with your prompt as its
  first turn, and the tab is an ordinary session tab from there. With
  nothing written, the button reads **Empty Session** instead — press it
  (or Enter) and the agent starts with no prompt, waiting at its own input
  box, the way a session used to open. `Ctrl+J`
  opens a terminal beside the screen just as it would beside the console.
- **Unsent screens are drafts.** As soon as there is text on the screen, or
  a terminal open beside it, it is kept: closing the tab or quitting Collins
  leaves a **Draft** row under the project in the sidebar (named after the
  prompt's first line, with a pencil mark; a screen kept only for its
  terminal is called *Draft* and keeps the agent's mark), and clicking that
  row brings the screen back with the text, the checkbox, the model and
  effort picks, and the terminal panel as you left them. Send spends the draft; the row's
  trash button discards it. While the screen is still open, that same button
  closes the tab too — it is a close cross until something is written, and
  turns into the trash can with the pencil, since the click then throws the
  draft away along with the tab.
- With *Start new sessions in a git worktree* on, each new session works in
  a fresh worktree of its project, so it won't see uncommitted local
  changes; a launch that can't cut one (a repository with nothing committed
  yet, say) says so and starts in the project directory instead. If
  terminals were open beside the new-chat screen when a worktree launch was
  sent, Collins offers to `cd` them into the worktree once it exists — a
  terminal running a command is left alone. The choice is per project as
  well as global: *New sessions use a worktree* in a project header's
  right-click menu pins it for that project over the preference. Either
  way, one launch can go the other way without changing anything — *New
  session here (in a worktree)* / *(no worktree)* in the same menu, and
  the matching entry the New Session dropdown grows for the visible
  project.
- **Folder trust is asked once, up front**: the first launch in a project
  the agent doesn't trust yet asks *Do you trust this folder?* before
  anything starts, and records the answer where the agent reads it, so the
  question isn't asked a second time inside the terminal. Trust covers
  everything under the folder, worktrees included.

![The new-chat screen: the project over its composer, a worktree checkbox and a model picker on the Send row, the unsent prompt already a Draft row in the sidebar](/img/new-chat.png)

## Bulk actions & housekeeping

- **Select mode** (sidebar menu → *Select multiple sessions*) to open, star,
  archive, or trash many sessions at once.
- **Archive** sessions you're done with (kept on disk; toggle *Show
  archived sessions* to see and restore them). Archiving a session with an open tab
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
- **Export as Markdown…** (right-click) writes a session transcript to a
  readable Markdown file. **Move transcript to trash…** (recoverable) and
  **Delete permanently…** are the only actions that touch a transcript
  file, and always sit behind a confirmation.
- The row's menu also carries the small stuff: **Reveal transcript** (the
  `.jsonl` in your file manager), **Open In…** (the session's directory,
  in any app the desktop offers), **Open in new window**, **Rename to
  match PR**, and **Repair session link** for a row whose detached agent
  the app lost track of. Project headers get **Open on GitHub** where the
  checkout has a github.com remote.
- **Open in [Ghostty](https://ghostty.org)** resumes a session in an
  external Ghostty window instead of an embedded tab (shown when `ghostty`
  is on your `PATH`).

## Caffeine Mode

The coffee cup at the right of the header keeps the computer awake and the
screen on while an agent works unattended — click it to toggle; the cup
fills while it's on.

- **Right-click it for a timer**: *Until idle*, 1, 2, 3, 6 or 12 hours,
  or *Indefinitely* — so a long build can't leave the machine awake
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
the editor's **color scheme**, **font**, **line numbers**/**hidden
files** toggles and the width below which it shows a **single column**, the
app's **Dark / Light Mode** (system / light / dark), the **Notifications**
group — the **In-app notifications** switch, the **Sound** picker (the
desktop theme's sounds, five bundled chimes, or a file of your own), the
card's **Card theme** pin (follow app / light / dark), **Bells from other
sessions**, and **Announce finished runs** — the
**language** (English, Magyar, Deutsch, Español, Français), the sidebar's
**Show folder paths in sidebar** and **Show Claude usage** toggles, a
**Token use** group directly under General that gathers everything that runs
Claude on your behalf — the **Session title model** and **Icon generation
model** pickers (each with a **None** option — it replaced the
*Auto-generate session titles* switch, and is the icon picker's default), an
**Auto-renew the Claude login** switch (on) for the throwaway run that repairs
an expired login — off, the usage panel just says to run `claude` yourself — and
the **Model list** row, which is free — followed by a switch for each of the
**built-in MCP tools** the agent can call, the **status icon**, **Reopen the
last session**, what to do **when quitting with running sessions** (ask /
exit / background / hide), **Archive on claude.ai too**, **Check for
updates** (the once-a-day look at GitHub's latest release, through `gh` or
anonymously), a **Pull requests** group — the PR page's **Text size**,
whether a first prompt's "review PR 183" **attaches that PR to the
session** (on), whether sessions are **renamed after their pull
requests** (off), and whether the marks are **refreshed at launch**
(on) — and the **Footer apps** list — reachable from the sidebar menu or
`Ctrl+,`.

A **search bar across the top** filters the whole screen as you type, and it
has the focus the moment preferences opens, so the way to a setting is to
type a word from it — a section heading, a word from a description
(*Ctrl+C*), or an option folded away inside a row (*Dracula*, *Magyar*).

The *Token use* rows and the tool switches are also the first thing a fresh
install sees: a **Before you start** dialog shows them once, before any of
those runs happens, along with the `claude` CLI in use — or, on a launch
that can't find one, the path box that asks where it is (see
[Getting Started](/guide/getting-started#first-run)).

![Preferences dialog](/img/preferences.png)
