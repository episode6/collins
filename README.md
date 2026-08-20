<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-08-20. Full change history: git log for this file.
-->

# Collins

[![CI](https://github.com/episode6/collins/actions/workflows/ci.yml/badge.svg)](https://github.com/episode6/collins/actions/workflows/ci.yml)

A vibecoded, native GTK4/libadwaita agentic development environment to manage, orchestrate and compliment all your [Claude Code](https://claude.com/claude-code) sessions.

> My wife keeps referring to Claude as Collins by mistake. So now when she asks me if I'm talking to Collins, I can say yes.

Collins is a fork of [agent-session-manager](https://github.com/r4nd3l/agent-session-manager) by Máté Molnár ([original project website](https://r4nd3l.github.io/agent-session-manager/)) — all credit for the original app goes there. This fork is GPL-3.0 like the original.

📖 **[Documentation](https://episode6.github.io/collins/)**

> **Unofficial community tool.** An independent community project, not affiliated with or endorsed by any agent vendor (including Anthropic).
> It never modifies your agents' own session data — all app state lives in its own config file.
> Fair warning: this app is entirely vibecoded — the code is written by Claude Code.

![Collins](data/screenshot.png)

Features:

- **Sidebar** lists every session found on disk (for Claude Code, under `~/.claude/projects/`), grouped by project (collapsible headers, with a collapse-all/expand-all toggle in the sidebar header), with a **Favorites** section pinned on top — star a session to move it there. **Drag a project header** to rearrange projects — the order and each project's expanded state persist across restarts. The **search button** (or `Ctrl+Shift+F`) opens a search box across the sidebar header that filters by name, project, preview, or session id, and the list **updates live** as sessions are created or written to.
- Sessions can be given **custom names** (right-click → Rename…, or rename the session's tab), and unnamed sessions get an **auto-generated title**: pre-existing sessions are titled locally on launch (first 10 words of the initial prompt), while sessions created during an app run have their first prompt summarized to ≤5 words by a headless `claude -p` run (the model is a Preferences setting, defaulting to the newest Haiku) — the same CLI and login the whole app is based on, no extra credentials needed. A prompt that only points at a pull request ("review PR 183") would summarize to a number nobody can read at a glance, so that PR's title is fetched with `gh` and handed to the model as quoted context — flagged as untrusted data, with any instruction inside it to be ignored. Titles are persisted so each is generated only once; right-click → **Regenerate name** re-runs the model for one session, and a Preferences toggle turns auto-titling off (a manual rename always wins). Names, favorites, and archived sessions persist in `~/.config/collins/state.json` — your agents' own session files are never modified.
- **Clicking a session** opens a tab in the main area; each tab is an embedded **VTE terminal** running your `$SHELL` with the agent's resume command (`claude --resume <session-id>` for Claude Code) typed into it, in the directory the session last worked in (worktree-aware). If the session is still running detached, Collins **re-attaches** to the live process (`claude attach`) instead of resuming a copy. When the agent exits you drop to a shell prompt; the tab closes when the shell exits. Closing a tab asks the agent to exit cleanly (Claude Code's `Ctrl+C` `Ctrl+C`, which works from whatever screen the agent is on) in the background first — or, for agents that support it, the close dialog offers to **background the session** instead (Claude Code's `/bg`), leaving it running detached to re-attach to later. On the next launch the window comes back at its last size — and with **Reopen the last session** on (Preferences → Startup), with the session you had focused when you closed it.
- **In-terminal search** with a find bar (`Ctrl+Shift+G`) over the tab's scrollback.
- **Terminal panel** (`Ctrl+J`, or the small buttons in the window's bottom-right corner): every tab has a second plain-shell terminal — no agent auto-launched — below or beside the agent terminal. It opens in the agent's *current* working directory (worktree-aware), and the rotate button in the panel's tab row — or **`Ctrl+;`** — moves the tab you're in bottom↔right without restarting its shell. The **overlay button** beside it floats that tab over the whole session tab — agent terminal, other panels and editor alike — until the restore button in its top-left puts it back where it was. Each layout's panel size is remembered per tab while the app runs, and the last-set size per layout is saved app-wide, so every panel opened from then on defaults to it. **Right-clicking** the terminal button opens that same directory in your desktop's own terminal instead, in a window of its own. Typing `exit` in it hides the panel; the last-used position (bottom/right) is remembered app-wide, and whichever panel you open next defaults to it. Closing a tab while a command is running in its panel — even a hidden one — asks for confirmation before the command is killed. Closing the whole window with busy sessions shows a **single confirmation** — close (every agent is asked to exit cleanly), background the sessions (`/bg`), or **keep running (hide the window)** — before the window goes away.
- **Pull requests, tracked and acted on**: every PR a session opens shows up as a **mark on its sidebar row** and **chips in its tab footer** — GitHub's own iconography, carrying the least settled state (draft/open/merged/closed) and the loudest thing left to do (red CI or conflicts, comments waiting on a reply, checks still running, all green). Click through to an **in-app PR page** beside the terminal, or right-click for the actions: mark ready for review, **merge** (or arm auto-merge), ask Claude for a review — and send red CI, conflicts, or unanswered comments **back to the session as a prompt**. Status refreshes on demand, at launch, and the moment a session's run finishes. All of it runs on the GitHub CLI (`gh`).
- **Editor panel** (`F8`): a syntax-highlighted code editor beside the agent terminal — file tree, quick open (`Ctrl+Shift+O`), and an **Agent files** list of what the session most recently wrote, one click from the change it just made. External changes reload cleanly (the agent is rewriting these files, after all), the editor follows the session into worktrees, and it can pop out to a window on a second monitor.
- **Prompt composer**: a multi-line, spell-checked text box floating over the agent terminal for prompts that outgrow one line — **start typing at an empty prompt and it opens with what you typed** (the CLI's `/`, `!`, `#` and `@` keep their keys), `Ctrl+.` toggles it, closing puts the draft back in the agent's input box, and dropped images ride along as attachments. Floating or docked below the terminal.
- **Status icon & close-to-hide**: a status icon in the top bar whose menu jumps to any open session, wearing an **unread badge** counting the runs nobody has looked at yet. Closing the window can **hide it instead** — every session keeps running, and the icon, a notification, or a relaunch brings the window back. (StatusNotifierItem; GNOME needs an AppIndicator extension.)
- **Caffeine Mode**: the header's coffee cup keeps the machine awake while agents work — by timer, indefinitely, or **until idle**: as long as any open tab is working the machine stays up, and five minutes after the last one stops it dozes, re-arming when work resumes.
- **Easy copy & paste** (on by default): plain `Ctrl+C` **copies whenever text is selected** — otherwise it interrupts the agent as usual — plain `Ctrl+V` pastes, and right-click opens a Copy / Paste / Select All menu. No `Ctrl+Shift` finger-twisting just because it's a terminal (`Ctrl+Shift+C` / `Ctrl+Shift+V` still work, and the mode can be turned off in Preferences).
- **Live sessions stand out**: a session with a tab open gets a fill in the sidebar, whether or not you're caught up on its output (a tab with unread output is marked by the tab bar itself). A session **running detached** (`/bg`) gets a **yellow guide line** and no fill — there's no tab to return to until you reopen it. While an agent is **actually working in a tab**, its row's guide line becomes a **moving blue barber pole** that stops soon after the agent does, so the sidebar says which sessions are thinking and which are waiting on you. A **red guide line** marks a session you stopped mid-task, and stays red whether or not a tab is open.
- A **Claude usage panel** at the bottom of the sidebar shows your subscription limits (session, weekly, extra usage) as progress bars with reset countdowns — read straight from the `claude` CLI's own login, refreshed every 5 minutes (paused while the window is minimized or the screen is locked). Toggle it in Preferences.
- **Tabs follow the sidebar's order**: left to right is the session list read top to bottom, whatever order you opened them in, and they re-arrange when the list does (drag a project to a new spot and its tabs move with it). Tabs with no row in the list — chats, replays — collect at the right-hand end; the tab bar itself isn't drag-reorderable, since the sidebar is where the order is set.
- **Tabs** can be renamed, given an emoji prefix, or have their session ID copied (right-click → Rename… / Set emoji… / Copy session ID); renaming a session's tab updates its name everywhere. While a session tab is focused, header buttons **exit** or **background** it and close the tab immediately — no confirmation dialog — the **tab bar can be hidden** with its own header toggle (the window title then names the active tab), and the **sidebar toggles** with the header button or `F9`. **Shift+Enter** inserts a newline in the agent's prompt.
- Each tab has a slim **footer** showing the agent's live working directory (click to copy; worktree-aware) and the current **git branch** (⎇), plus the terminal-panel buttons.
- **Right-click a session** for the full action set: open, open in [Ghostty](https://ghostty.org) (external window — Ghostty can't be embedded), fork (`--fork-session`), rename, regenerate name, favorite, **details** (messages/models/tokens, a peek at recent messages, and MCP servers/usage), copy session id, export as Markdown, reveal transcript, archive, move the transcript to trash, or delete permanently. **Right-click a project header** to start a new session there, **open its folder in another app**, or archive the whole project — projects with no visible sessions still show their header (with a `+` button) so a folder stays reachable. The open-in list carries each app's own icon: one row per app added under Preferences → *Footer apps*, plus **Open in File Manager** and **Open in Terminal** using whatever your desktop nominates for those (`$TERMINAL`, `xdg-terminals.list`, and the system's `x-terminal-emulator` are honoured, in that order).
- **Tools the session itself can call**: every session Collins starts is offered a small MCP server of Collins' own (`collins` in its `/mcp` list), so the agent can drive the window it runs in — `notify_user` (a **desktop notification** titled with the session; it flashes the session's tab and row, flags the row until you come back, and clicking it raises that tab), `set_session_title` (the session names itself in the tab and sidebar), `open_in_editor` (put a file on your screen at a line, in the session's editor pane), `show_image` (a screenshot or render in the in-app lightbox — a file, or an `http(s)` URL Collins fetches for it), `attach_pr` (put a pull request on the session's footer and sidebar row — for one Collins can't spot on its own, like a PR opened by a subagent), `start_session` (spawn a **sibling session** in a background tab, handed a prompt and inheriting the caller's permission mode — it never steals your focus), `read_terminal` (read the terminal panel's tabs, scrollback and all), and `run_in_terminal` (type a command into an idle panel shell and run it where you can watch). Each asks permission on first use, like any MCP tool, and each has its own on/off switch in Preferences → *Session tools* (all on by default).
- **Select mode** (menu → *Select multiple sessions*) for bulk actions: open, star, archive, or trash many sessions at once.
- **New session** (tab icon in the header) starts a fresh agent session (`claude`) in the **visible session's project** — no dialog needed; with no session visible it asks for a folder. Every project header also has a **`+` button** to start a session right there, and a just-started session shows a **"New Thread"** placeholder row until the agent writes its transcript.
- **Quick switcher** (`Ctrl+K`) jumps to any session by type-ahead; the sidebar is **resizable** and its width is remembered.
- **MCP servers browser** (menu → MCP servers): a read-only view of every MCP server configured in `~/.claude.json`, global and per-project.
- **Preferences** (menu → Preferences, or `Ctrl+,`): terminal font, scrollback, **terminal color theme** (Dracula, Solarized, Gruvbox, Nord, Catppuccin, Tokyo Night, Monokai, One Dark…), color scheme, **language** (English, Magyar, Deutsch, Español, Français), **easy copy & paste**, plus **Show folder path**, **Show Claude usage**, and **Auto-generate session titles** toggles for the sidebar, and a switch per **session tool**.
- A sidebar status footer shows session, project, transcript-size, and open-tab counts.

- **Advanced new session** (New Session menu): choose a **model**, **permission mode**, **extra directories** (`--add-dir`), or **continue** the last session in a folder.
- **Permanent delete** sits alongside *Move to trash*; `Ctrl+Shift+E` toggles a 😊 marker on the current tab.

### Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+Shift+F` | Focus search |
| `Ctrl+Shift+T` | New session |
| `Ctrl+Shift+N` | New window |
| `Ctrl+W` | Close the last-focused panel tab, then the session tab once none are left |
| `Ctrl+PgUp` / `Ctrl+PgDn` | Previous / next tab |
| `Ctrl+C` / `Ctrl+V` | Copy selection / paste (easy copy & paste, on by default) |
| `Ctrl+Shift+C` / `Ctrl+Shift+V` | Copy / paste in terminal (always available) |
| `Ctrl+Shift+G` | Find in terminal |
| `Ctrl+K` | Quick switcher (jump to any session) |
| `Ctrl+Shift+E` | Toggle 😊 marker on the current tab |
| `Ctrl+J` | Show/hide the terminal panel |
| `Ctrl+Shift+K` | Clear the terminal panel (screen and saved history) |
| `Ctrl+;` | Move the current panel tab to the panel's other side (bottom ↔ right) |
| `Ctrl+.` | Show/hide the composer (pressed while composing, it closes and puts the draft back in the agent's box) |
| `Ctrl+'` | Show/hide the attachments gallery (docked as a panel tab, it comes to the front instead) |
| `F7` | Open the page for the session's most recently linked pull request |
| `F8` | Show/hide the editor panel |
| `Ctrl+S` (in the editor) | Save the current file |
| `Ctrl+F` (in the editor) | Find in file |
| `F9` | Toggle sidebar |
| `Ctrl+,` | Preferences |

## Requirements

Python ≥ 3.10, GTK 4, libadwaita ≥ 1.5, VTE (GTK 4 build), GtkSourceView 5, PyGObject — from your distro's packages. libspelling is optional: with it the prompt composer gets spell-check, without it the composer is a plain text box.

```bash
# Ubuntu / Debian
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-vte-3.91 gir1.2-gtksource-5 gir1.2-spelling-1

# Fedora
sudo dnf install python3-gobject gtk4 libadwaita vte291-gtk4 gtksourceview5 libspelling

# Arch
sudo pacman -S python-gobject gtk4 libadwaita vte4 gtksourceview5 libspelling
```

Plus the [`claude` CLI](https://claude.com/claude-code) on your `PATH` — Collins is a tool for Claude specifically.

Optional: the [GitHub CLI](https://cli.github.com/) (`gh`), signed in. It is what every pull request mark and every pull request action above runs on; without it a PR is a number and nothing else, so a launch that finds `gh` missing or signed out says so — showing what it's holding back, and pointing at the install or the one command that signs you in — until you set it up or tick *Don't show this again*.

## Install

**From source:**

```bash
git clone https://github.com/episode6/collins.git
cd collins
python3 -m collins
```

Or install the desktop launcher + icon (shows up in the app grid as "Collins"):

```bash
./data/install.sh
```

**PyPI — pipx or pip.** Available everywhere, and the way in on a distro with no package of its own.

```bash
pipx install --system-site-packages collins   # or: pip install --user collins
collins --install-desktop                     # optional: add it to the app grid
```

`--system-site-packages` is not optional: Collins' `dependencies` list is deliberately empty because PyGObject, GTK, VTE and GtkSourceView come from your distro's packages (see [Requirements](#requirements)), not from PyPI. A plain `pipx install collins` or a venv without that flag builds an environment that cannot see them, and the app exits on `import gi` the first time you run it.

`collins --install-desktop` is what `data/install.sh` is for a checkout and what the packages do system-wide: it writes the launcher, app icon and metainfo under `~/.local/share` for your user only. The app offers the same thing from its sidebar menu — **Install desktop icon**, shown only when nothing has put Collins in your app grid yet. Nothing else needs installing — the toolbar and sidebar artwork ships inside the wheel.

**Ubuntu — the episode6 PPA.** The maintained channel on Ubuntu 24.04 (noble) and 26.04 (resolute), and on the derivatives that share them: Linux Mint, Pop!_OS, elementary OS, Zorin. Collins upgrades with the rest of your system from here.

```bash
sudo add-apt-repository ppa:episode6/stable
sudo apt install collins
```

Ubuntu 22.04 (jammy) is not supported — it ships libadwaita 1.1 and GTK 4.6, and Collins uses APIs from libadwaita 1.5 and GTK 4.10.

**Debian — .deb package.** A Launchpad PPA can only serve Ubuntu, so on Debian — and the Debian-family distros that don't build on Ubuntu — this is the way in (outside the Debian family, PyPI above is). Grab one from [the releases page](https://github.com/episode6/collins/releases), or build it with `./scripts/build_deb.sh`:

```bash
sudo apt install ./collins_*_all.deb
```

Dependencies are pulled in automatically and the app appears in your app grid as "Collins". Note that a `.deb` installed this way **does not update itself** — it adds no apt source, deliberately, so nothing is subscribed to a third-party archive behind your back. Watch the releases page, or use the PPA if you are on Ubuntu.

**Updating.** PPA: `sudo apt upgrade` picks it up with everything else. `.deb`: install the new one over the old with the same `sudo apt install ./collins_*_all.deb`. pipx: `pipx upgrade collins` (the `--system-site-packages` flag is remembered). pip: `pip install --user --upgrade collins`. Source: `git pull`. Relaunch Collins afterwards — a running instance keeps the old code, including one hidden with *Keep Running*; use **Quit** for a real restart.

Debian 13 (trixie) and newer have everything Collins needs. Debian 12 (bookworm) does not — libadwaita 1.2 against the 1.5 APIs.

Terminal copy & paste works the way you'd expect out of the box: plain `Ctrl+C` (when text is selected) and `Ctrl+V` — see **easy copy & paste** above. `Ctrl+Shift+C` / `Ctrl+Shift+V` always work too.

## Layout

```
collins/
├── app.py            # Adw.Application entry point + CSS
├── window.py         # main window: tabs, actions, dialogs wiring
├── sidebar.py        # session list: search, groups, badges, select mode
├── store.py          # single source of truth: threaded scans, file monitors
├── models.py         # SessionItem GObject with bindable properties
├── sessions.py       # transcript discovery & parsing (pure Python)
├── providers.py      # agent CLI abstraction (currently Claude Code)
├── state.py          # persistent app state (names, favorites, settings)
├── terminal.py       # VTE terminal tab + secondary shell panel
├── titles.py         # auto-generated session titles (local + claude)
├── usage.py          # Claude subscription usage fetch/parse
├── usagepanel.py     # the sidebar usage panel widget
├── gitinfo.py        # git branch detection for the tab footer
├── dialogs.py        # rename / emoji / confirm / details / MCP dialogs
├── prefs.py          # preferences dialog
└── themes.py, i18n.py, switcher.py, panelhistory.py, copylabel.py, …
data/                                    # symlinked into collins/, so it ships in the wheel
├── com.episode6.Collins.desktop         # launcher template
├── com.episode6.Collins.metainfo.xml    # AppStream metadata
├── icons/                               # app icon + the app-private action icons
└── install.sh                           # install launcher + icon for current user
scripts/
├── build_deb.sh                         # build the .deb package into dist/
├── install-debug-launcher.sh            # app-grid entry: pull this checkout, run debug
├── make_demo_data.py                    # fake sessions for screenshots/demos
├── ship-release.py                      # publish a release branch as a GitHub release
├── verify_versions.py                   # CI check: all version copies agree
└── verify_wheel_data.py                 # CI check: the wheel carries icons + launcher
```

## Publishing (maintainers)

Releases follow the episode6 release-branch flow: cut `release/v<VERSION>`,
harden it, then ship from it. The whole process — versioning scheme,
version-bump PRs, hardening, hotfixes — lives in
[RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md), and the `release-branch-skill`
and `ship-release-skill` agent skills in [.agents/](.agents) automate most of
it.

Shipping runs `./scripts/ship-release.py` from the release branch: it creates
the GitHub Release (tag `v<VERSION>`, notes extracted from
[docs/releases.md](docs/releases.md)), and the tag push runs
`.github/workflows/release.yml` — build the wheel/sdist and the `.deb`,
attach the `.deb` to the release, publish to PyPI (trusted publishing /
OIDC), and upload a source package per Ubuntu series to
`ppa:episode6/stable`.

## Credits & license

Collins is a rebranded fork of
[**agent-session-manager**](https://github.com/r4nd3l/agent-session-manager)
by Máté Molnár, which did all the heavy lifting — see the
[original project's website](https://r4nd3l.github.io/agent-session-manager/)
for the app Collins grew out of. Released under
[GPL-3.0-or-later](LICENSE), same as the original.

Everything Collins is built on is disclosed in
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) — the same document the app
shows on the Legal page of its About dialog.
