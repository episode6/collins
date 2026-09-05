<!-- New in the ghackett fork of agent-session-manager (GPL-3.0). -->

# Collins — guide for agents working in this repo

Collins is a native GTK4/libadwaita desktop app (Linux only, pure Python via
PyGObject, no build step) that manages, orchestrates and complements Claude
Code sessions. It reads the CLI's own session transcripts under
`~/.claude/projects/`, lists them in a sidebar, and opens each one in an
embedded VTE terminal running `claude --resume <id>`. Around that terminal it
grows a workbench: a prompt composer, a terminal panel, a code editor, a git
page (hunk in a VTE), native pull-request pages, notifications, a status icon,
and a small MCP server every launched session can call back into.

It is a GPL-3.0 fork of r4nd3l/agent-session-manager, positioned as an
opinionated **Agent-First IDE / Agent Orchestrator for Claude Code only** — do
not frame the provider abstraction as multi-agent-ready in docs or copy. The
audience is Linux developers: terse titles and one-line subtitles, no
hand-holding. The app is written by Claude Code; that fact is stated as a
matter-of-fact disclosure ("vibecoded"), never celebrated.

This file covers the architecture and the rules that hold everywhere. Each
feature has a skill under `.agents/` with the deep dive and its footguns; the
map at the end says which to load.

## Non-negotiable rules

1. **GPL modification notices.** Almost every file that existed before the fork
   (commit `a3a5a77`) carries a "Modified from the original
   agent-session-manager … Last modified: YYYY-MM-DD" header. Any edit to such
   a file must bump that date in the same commit. Files created in this fork
   need no notice. Renamed pre-fork files (`collins/app.py`, `window.py`,
   `terminal.py`, …) look fork-new to a path check but still carry headers —
   `head -4` each edited file and bump what you find. Load the
   `gpl-modified-file-notices` skill before committing.
2. **Never modify the agent's own data.** `~/.claude/` (transcripts,
   `.credentials.json`, `~/.claude.json`, `settings.json`) is read-only, with
   two exceptions behind confirmations (move a transcript to trash / delete it)
   and one deliberate write (folder trust, `trust.py`, which mirrors what the
   CLI itself would write). Everything Collins owns lives in its own XDG paths
   (see "Where state lives").
3. **Unit tests are GTK-free.** `tests/conftest.py` blocks `gi.repository.{Gtk,
   Adw, Gdk, Gsk, Graphene, Vte}`. Anything worth testing lives in a module
   with no GTK import; widgets import the pure module, never the reverse. A
   test that needs a widget is an e2e check (`scripts/check_*.py`), not a
   pytest.
4. **Fail soft on undocumented surfaces.** Collins rides CLI internals nobody
   promised to keep (transcript fields, `claude agents --json`, the input-box
   grammar, OSC 9;4, the OAuth usage and models endpoints). When one moves the
   feature must go blank or skip a step — never crash, never write anything
   wrong.
5. **Treat foreign content as untrusted.** Repo files (`project-icon.svg`),
   transcript text, PR bodies/titles/branch names, GitHub logins, MCP socket
   frames, `state.json` itself: bound sizes, escape before Pango markup, gate
   URLs to http(s), validate shapes, drop what doesn't fit. A project row
   appears the moment a session is started there — before Claude has ever run
   in that directory — so "the user already trusted this repo" is not an
   argument.
6. **Every headless `claude` run goes through `titles.headless_argv`** and runs
   from the scratch dir `~/.config/collins/title-scratch/<uuid>` with
   `stdin=DEVNULL`. Never hand-roll a `claude -p` argv, and never spend the
   user's quota from a path that isn't disclosed in Preferences → Token use.

## Architecture in one page

**Entry and composition.** `collins/app.py` (`App(Adw.Application)`) loads the
CSS, prepends the bundled icon path, starts the MCP socket service, owns
Caffeine Mode, the status icon, the notification center's app-level fan-out,
and the MCP tool *handlers* (`App._mcp_*`). `collins/window.py` (`MainWindow`)
composes the sidebar with the tab view, installs every `win.*` action, and owns
the tab lifecycle: open, resolve, close (graceful exit / `/bg` / hide), archive,
quit flows. There can be several windows; a tab can move between them.

**Data layer (GTK-free, unit-tested).** `sessions.py` discovers and parses
transcripts; `providers.py` wraps the `claude` CLI (commands, prompt-line
grammar, background agents); `state.py` is `AppState` — the one writer of
`~/.config/collins/state.json`, with `DEFAULT_SETTINGS` as the settings
catalogue; `store.py` is `SessionStore`, the single source of truth between
disk and UI (threaded scans, `Gio.FileMonitor`s, grouping, every state
mutation), which the sidebar and windows observe via `refreshed`, `busy-changed`
and per-item `SessionItem` property notifications (`models.py`).

**Hubs, not wires.** The app repeatedly replaced lattices of hand-run signals
with one owner that everybody subscribes to: `SessionStore` (sessions),
`prstore.PrStore` (all pull-request state, reachable as `store.pr_store`),
`notifycenter.NotificationCenter` (every notification, the badge's number,
the delivery table), `traymodel` (what the status icon shows), `keybindings`
(every shortcut). New surfaces read from the hub and subscribe to its
signals; new writes go through the hub. Do not reintroduce per-surface relays.

**The tab.** `terminal.py`'s `TerminalTab` is the session tab: a VTE running
the user's `$SHELL` with the agent command typed in, a footer (cwd, branch,
model, effort, PR chips), a `PanelDock` around the terminal (`paneldock.py`
realizing a GTK-free `docktree.DockTree` of `Gtk.Paned`s whose leaves are
`PanelStrip`s of duck-typed `PanelPage`s — shells, PR pages, the composer, the
attachments gallery, the git page), an `EditorPane` in its own end slot, and
overlays (composer, attachments, lightbox). It also runs the transcript
resolver that binds a freshly spawned tab to the session id the CLI mints.

**What the session can call.** `mcp_shim.py` (stdlib-only, spawned by the CLI
via `--mcp-config`) relays MCP over a Unix socket to `mcpserver.py`
(Gio-only) inside the app; `mcptools.py` (GTK-free) holds the tool table,
schemas, framing and runtime paths; the handlers live in `app.py`. Session
identity is the shim's kernel-verified pid walked up `/proc` to a tab.

**Everything Claude-shaped runs on the CLI's own login.** Titles
(`titles.py`), project icons (`icongen.py`) and login repair
(`tokenrefresh.py`) are headless `claude -p` runs. The usage panel
(`usage.py`), the model catalog (`claudemodels.py`) and claude.ai archive
mirroring (`remotearchive.py`) read the OAuth token from
`~/.claude/.credentials.json` and call Anthropic's undocumented endpoints
directly. No separate API key exists anywhere.

**GitHub goes through `gh`.** Every PR read and action is a `gh` call
(`prstatus.py` transport); without `gh` a PR is a number and an empty menu.

## Where state lives

| What | Where |
| --- | --- |
| Names, favorites, archived, project order, settings, panel layouts, editor state, PR records, attachments, drafts, notifications | `~/.config/collins/state.json` (`AppState`, synchronous atomic writes) |
| Headless-run scratch cwd | `~/.config/collins/title-scratch/<uuid>` |
| Panel shell scrollback | `~/.local/state/collins/panel_history/<session>[.<ordinal>].txt` |
| Chats virtual project | `~/.local/share/collins/chats/` |
| MCP config file | `~/.local/share/collins/<app id>/` |
| MCP socket | `$XDG_RUNTIME_DIR/collins/<app id>/mcp.sock` |
| Model catalog, update-check stamp, fetched images | `~/.cache/collins/` |
| Everything of the CLI's | `~/.claude/` — read only |

Every one of these has an environment override used by tests, captures and
e2e checks: `COLLINS_APP_ID`, `COLLINS_PROJECTS_DIR`, `COLLINS_CLAUDE_CONFIG`,
`COLLINS_CHATS_DIR`, `COLLINS_CLAUDE_CREDENTIALS`, `COLLINS_USAGE_FIXTURE`,
`COLLINS_PR_STATUS_CACHE`, plus `XDG_CONFIG_HOME` / `XDG_STATE_HOME`.
Diagnostics: `COLLINS_LOG=INFO`, `COLLINS_SHIM_LOG=<file>`,
`COLLINS_GIT_DEBUG_LOG=<file>`.

## Conventions that hold everywhere

- **Main-loop discipline.** Blocking work (gh, git, HTTP, transcript scans)
  runs on daemon threads and lands with `GLib.idle_add`. Any landing that
  resets a gate or advances a pipeline must pass
  `priority=GLib.PRIORITY_DEFAULT`: under CI's Xvfb the frame clock never goes
  quiet and default-idle callbacks starve forever. Purely cosmetic settles may
  stay at idle priority but e2e checks must not assert on them.
- **Unbidden dock surgery waits a beat.** Opening/splitting a panel page from
  inside the idle cascade that announced the trigger has segfaulted GTK's
  Wayland backend; schedule such opens on a `timeout_add`, and open them with
  `focus=False` so the keyboard stays where it was.
- **Settings.** A new setting is one entry in `state.DEFAULT_SETTINGS` (with a
  comment saying what it does and where it is read), a row in `prefs.py`
  placed per `prefslayout.GROUPS`, search words if its row's text doesn't
  carry them, and a mention in `docs/guide/features.md`. Read settings via
  `state.get_setting`; the file writes every default back, so a new key exists
  in every install after its first save.
- **Shortcuts** come from `keybindings.BINDINGS` (GTK-free catalogue) via
  `keymap.py`; never hard-code a chord in a controller. Ctrl+letter chords are
  nearly all taken by the CLI's readline; the free space is punctuation (now
  exhausted for single-modifier) and function keys.
- **Strings** go through `i18n._()`; translations are hand-written dicts in
  `po/generate.py` (no plural support — avoid `ngettext`). New strings may fall
  back to English until the release-cut translation refresh, which is expected.
- **Icons** are filled paths only (GTK's symbolic parser drops strokes and
  transforms), live in `data/icons/hicolor/scalable/actions/`, and ship inside
  the wheel via the `collins/icons` symlink into `data/`.
- **CSS** in `app.py` is a bytes literal — ASCII only, no em dashes in its
  comments. Theme-following colors live in `themes.py`'s dynamic provider.
- **Copy and confirmation.** Bulk destructive actions state their blast radius
  (counts, projects, side effects) and prefer the system trash over unlink.
  Archiving is the user's "done with this" gesture and holds most of their
  data — treat "all archived" as "most of everything".
- **Sizing rules of thumb.** `Adw.ComboRow` values get ~130px — short labels,
  explanation in the subtitle. `Adw.AlertDialog` sizes to its text, not its
  extra child. `Gtk.Picture` has no height-for-width. See the GTK skill.

## Working in the repo

```bash
python3 -m pytest tests/ -q                 # unit suite (GTK-free, ~seconds)
ruff check collins/ tests/                  # CI pins ruff 0.16.4; rules E F W I UP B
bash .agents/capture-screenshots/scripts/with-headless-display.sh \
    python3 scripts/run_e2e.py [--only NAME] # e2e checks behind a headless compositor
(cd collins/hunkext/collins-git && bun test) # the hunk extension's tests
python3 scripts/verify_versions.py          # every version copy agrees
./start-debug                               # a debug instance (COLLINS_APP_ID=com.episode6.Collins.Debug)
```

GTK apps are single-instance per application id: launching with an id that is
already running only activates the existing window. Never test against the
user's live instance; mint a fresh `COLLINS_APP_ID` and a per-run scratch tree
(the `capture-screenshots` skill has the exact recipe, and
`COLLINS_CHATS_DIR` there is not optional — omitting it reaps the user's chat
directories). Scripts run from outside the repo import the system-installed
`collins` — set `PYTHONPATH=<worktree>` or `sys.path.insert(0, repo_root)`.

CI (`.github/workflows/ci.yml`) runs lint, the unit suite, the e2e suite under
Xvfb, wheel + `.deb` packaging with `scripts/verify_wheel_data.py`, PPA source
builds for noble and resolute, an RPM build + `dnf install`, version
verification, and `bun test`. The e2e job appears late in `gh pr checks`
output — a run is green only when `e2e` is listed and passed. When you change a
signal signature or a method the e2e scripts poke, grep `scripts/check_*.py`.

Docs: the README's feature list and `docs/guide/*.md` (VitePress; built by
`docs.yml`) describe every user-visible feature, and `docs/releases.md` has an
UNRELEASED section for the next version. A PR that adds or changes behaviour
updates them in the same PR. Screenshots for PRs: the `capture-screenshots`
skill, then the global `publish-screenshots` skill; include a full-window shot
beside any crop.

Releases follow `RELEASE_CHECKLIST.md` (release branch → harden → ship) with
the `release-branch-skill` and `ship-release-skill`. Four changelogs must
describe every release: `docs/releases.md`, `debian/changelog` (always
`UNRELEASED` in git), the AppStream metainfo `<release>`, and the Fedora
spec's `%changelog`.

## Feature map → skill to load

| Area | Modules | Skill |
| --- | --- | --- |
| Session discovery, the store, sidebar, state.json, titles, worktrees, background agents, busy detection | `sessions` `providers` `store` `models` `state` `sidebar` `titles` `bgstatus` `activity` `trust` `chats` `projecticons` | `collins-sessions-and-sidebar` |
| The session tab: VTE, spawn/resume/attach, close flows, prompt-line reading, links, footer, transcript resolver | `terminal` `window` `shellinput` `linkpatterns` `transcriptlinks` `transcript` `vtehtml` `proctree` `taborder` | `collins-terminal-tab` |
| Panel docking: strips, splits, DnD, layout persistence, sizes | `docktree` `dockzones` `paneldock` `panelstrip` `paneldnd` `tabguard` `panellayout` `panelhistory` `panedsizer` `panelsizing` `panelkeys` | `collins-panel-dock` |
| Composer, drafts, the new-chat screen, model/effort pickers, drops and pastes | `composer` `composerkeys` `newchat` `newchatview` `modelmenu` `dropimages` | `collins-composer-and-new-chat` |
| Session MCP tools, the shim, the socket service, lightbox and attachments | `mcp_shim` `mcptools` `mcpserver` `remoteimages` `lightbox` `attachrecords` `attachpanel` `pictures` `animatedimage` | `collins-session-mcp-tools` |
| Pull requests: status, hub, detail page, actions, menus, gh setup | `prstatus` `prstore` `prdetail` `practions` `prmenu` `prview` `prattach` `prblobs` `prfileimages` `avatars` `bodyimages` `ghsetup` `ghwelcome` | `collins-pull-requests` |
| The git page: hunk in a VTE, the native commits and files sidebar, the collins-git extension, git info, the panels' model and git runners | `gitpage` `gitsidebar` `hunkctl` `gitinfo` `gitmodel` `gitops` `hunkext/collins-git` | `collins-git-page` |
| Editor panel: file tree, quick open, pop-out, narrow mode | `editor` `editorfiles` `filetree` `quickopen` `fuzzy` `fileclipboard` `editorwindow` `filetypes` | `collins-editor-panel` |
| Notifications, bell, cards, sounds, status icon, dock badge, update check, Caffeine | `notifycenter` `notifyoverlay` `notifypanel` `notifysound` `statusicon` `traymodel` `flash` `updatecheck` `caffeine` | `collins-notifications-and-tray` |
| Everything that spends tokens or calls Anthropic: titles, models, usage, login repair, welcome, icon generation, claude.ai archive | `titles` `claudemodels` `usage` `usagepanel` `tokenrefresh` `tokensettings` `welcome` `welcomegate` `clisetup` `icongen` `remotearchive` | `collins-token-use-and-claude-api` |
| Preferences dialog, keybindings, themes, translations | `prefs` `prefslayout` `prefssearch` `keybindings` `keymap` `keybindingsdialog` `themes` `i18n` `po/` | `collins-preferences-keybindings-i18n` |
| Testing: unit suite rules, e2e checks, shims, headless probes | `tests/` `scripts/check_*.py` `scripts/run_e2e.py` | `collins-testing` |
| Packaging and CI: wheel, deb, rpm, PPA, COPR, AUR, the CI image, versions | `pyproject.toml` `debian/` `packaging/` `.github/` `scripts/` | `collins-packaging-and-ci` |
| GTK / libadwaita / VTE / CSS traps that are not feature-specific | — | `collins-gtk-sharp-edges` |

Also in `.agents/`: `capture-screenshots` (headless captures of a throwaway
instance), `gpl-modified-file-notices` (mandatory before committing),
`release-branch-skill`, `ship-release-skill`.
