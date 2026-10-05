<!-- New in the ghackett fork of agent-session-manager (GPL-3.0). -->

# Collins — guide for agents working in this repo

Collins is a native GTK4/libadwaita desktop app (Linux only, pure Python via
PyGObject, no build step) that manages, orchestrates and complements Claude
Code sessions. It reads the CLI's own session transcripts under
`~/.claude/projects/`, lists them in a sidebar, and opens each one in an
embedded VTE terminal running `claude --resume <id>`. Around that terminal it
grows a workbench: a prompt composer, a terminal panel, a code editor, a git
page (a native diff view), native pull-request pages, notifications, a status icon,
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
CSS, prepends the bundled icon path, starts the in-process service and its
MCP socket service, owns Caffeine Mode, the status icon and the
notification center's app-level fan-out (the delivery, the withdraws, the
badge). The session tools are the service's (`service/tools.py`); the app
holds their client half (`toolclient.ToolClient`, as `app.tool_client`;
the dispatcher is `app.session_tools`). `collins/window.py` (`MainWindow`)
composes the sidebar with the tab view, installs every `win.*` action, and owns
the tab lifecycle: open, resolve, close (graceful exit / `/bg` / hide), archive,
quit flows. There can be several windows; a tab can move between them.

**Data layer (GTK-free, unit-tested).** `sessions.py` discovers and parses
transcripts; `providers.py` wraps the `claude` CLI (commands, prompt-line
grammar, background agents); `state.py` is `AppState` — the one writer of
`~/.config/collins/state.json`, with `DEFAULT_SETTINGS` as the settings
catalogue, and the owner of `uistate.UiState`, the one writer of
`ui-state.json` beside it (the state split of the service-and-client spec:
`state.json` is what the service keeps, `ui-state.json` what this device
keeps; `SERVICE_SETTINGS` / `DEVICE_SETTINGS` say which side a setting
lives on, and every read and write goes through `AppState`'s one API, which
routes); `store.py` is `SessionStore`, the single source of truth between
disk and UI (threaded scans, `Gio.FileMonitor`s, grouping, every state
mutation). **Both are the service's** since PR-1.10: `ServiceCore` owns the
`AppState` (built `migrate=True, device=False`: it writes the split, and
never `ui-state.json`) and the `SessionStore`, and publishes them
(`service/storefeed.py`). The UI holds the **mirrors**: `remotestate.
RemoteState` (an `AppState` subclass over a copy the subscribe snapshot
fills and `state.set` events keep; writes optimistic, reverted with a toast
on a refusal; `get_setting` reads this device's `UiState` first) and
`remotestore.RemoteStore` (the store's four signals, `Gio.ListStore` of
`SessionItem`s and methods, filled by `item` / `rows` / `put-away` events,
every mutation a `store.*` request). `app.state`, `app.store`,
`window.state` and `window.store` are the mirrors; the sidebar and windows
observe `refreshed`, `busy-changed`, `unread-changed`, `archived` and
per-item `SessionItem` property notifications (`models.py`) on them.

**Hubs, not wires.** The app repeatedly replaced lattices of hand-run signals
with one owner that everybody subscribes to: `SessionStore` (sessions, seen
by the UI through its mirror `RemoteStore`),
`prstore.PrStore` (all pull-request state: the service store's own, seen
by the UI as `store.pr_store`, the mirror `remoteprs.RemotePrStore` with the
same three signals and equality guard),
`notifycenter.NotificationCenter` (every notification, the badge's number,
the delivery table: the history and the unread set are the service's,
`service/notifications.py`, and `app.notification_center` is the mirror
`remotenotify.RemoteNotifications`, which applies the delivery table with
this client's focus), `traymodel` (what the status icon shows), `keybindings`
(every shortcut). New surfaces read from the hub and subscribe to its
signals; new writes go through the hub. Do not reintroduce per-surface relays.

**The tab.** `terminal.py`'s `TerminalTab` is the session tab: a VTE running
the user's `$SHELL` with the agent command typed in, a footer (cwd, branch,
model, effort, PR chips), a `PanelDock` around the terminal (`paneldock.py`
realizing a GTK-free `docktree.DockTree` of `Gtk.Paned`s whose leaves are
`PanelStrip`s of duck-typed `PanelPage`s — shells, PR pages, the composer, the
attachments gallery, the git page), an `EditorPane` in its own end slot, and
overlays (composer, attachments, lightbox). The logic behind the widget is
the session's `Session` (`service/session.py`, GTK-free), **the service's
since PR-1.12a**: `ServiceCore.sessions` holds one per live agent pty,
built by `spawn` for kind `agent` and hosted by `service/hosting.py`'s
`SessionRecord` (its host, its two ports over the service's pty and screen
model, the cut sinks, and the facts it pushes) — the launch (command,
sandbox plan, worktree checks, restart), reading and writing the CLI's
input box (`takes_prompt`, prompts, switches, the composer's cut), the
transcript tail and the resolver that binds a freshly spawned session to
the id the CLI mints, the cwd poll, and the close flows' keystrokes and
polls; `tests/test_session.py` drives its state machines through fake
ports. The tab holds the **mirror**, `clientsession.ClientSession`
(`tab.session`, GTK-free): the last `session` event's facts (sent whole on
`attach`, as changed fields after, debounced to the screen's 50 ms settle:
D28), read with no round trip, and the requests the tab makes (`prompt`,
`write`, `switch`, `send`, `cut`, `draft.restore`, `close`, ...); every
old name on the tab forwards to it, and `apply(event)` turns the fields
that moved into the tab's GObject signals so `window.py`'s handlers stay.
A client never derives one of these facts from its own VTE. Busy and the
finish verdict are the service's too (D29): `service/tracking.py`'s
`ServiceActivity` is the window's `ActivityTracker` moved whole, fed by the
stream filter's `Progress` events, every pty's output after the filter,
the input frames and the `/proc` poll, its finish edges judged by
`service/finish.py` against the transcript; it sets `busy` and a counted
finish's `unread` on the store's items, and a session with no row yet
hears them as `busy` / `finished` fields of its `session` event. The tab
has **one backend** (PR-1.9): the terminal has no child and shows a pty of
the service's pty server over the socket (`ptyclient.ClientTerminal`:
output frames are `feed()`, the VTE's commits go back as input, the redraw
guard, the mouse coalescing). The panel shells beside it are the same: each
`PanelTerminal` is a `ClientTerminal` over a service pty of kind `shell` (a
sandboxed one spawned by the service on its box's plan), its text,
foreground and cwd read on the service (`pty.info`, `pty.capture`), its
history written by the service from the screen model and painted back
into the new pty's stream on reopen. The e2e checks reach past the
protocol only through `tab.probe` / `probe_set` / `probe_call` (the
`debug.*` requests, served only with `COLLINS_DEBUG_API=1` in the service's
environment: D27).

**What the session can call.** `mcp_shim.py` (stdlib-only, spawned by the CLI
via `--mcp-config`) relays MCP over a Unix socket to `mcpserver.py`
(Gio-only), which the service starts (`ServiceCore.start_mcp`);
`mcptools.py` (GTK-free) holds the tool table, schemas, framing, runtime
paths and the sandbox policy; the dispatcher and the handlers live in
`service/tools.py` (§3.7's table: the tools a session's own data serves
run there, a UI-bound one is a `tool` event to the session's active client
and a deferred reply settled by its `tool-reply` or at a 14 s bound, and
each has its no-client behaviour), and the client halves of the UI-bound
ones in `toolclient.py`. Session identity is the shim's kernel-verified pid
walked up `/proc` to a `Session` (through Phase 1 the tabs' own).

**The service and its API, in progress.** The split into a headless
`collins-service` and a GTK client (`~/specs/collins/split-service-and-client.md`)
lands a module per PR, GTK-free; `Session`, the pty server and the store and state are wired into the app.
`collins/api/protocol.py` (stdlib-only) is the API's message table (types,
direction, fields and bounds), `validate` / `validate_response`, JSON
framing, the 16-byte binary header and the `PROTOCOL` / `MIN_PROTOCOL`
window, modelled on `mcptools`. `collins/service/termstream.py` (stdlib
only; nothing in `collins/service/` imports GTK) is the stream filter, query
responder and mode tracker the service runs every pty's output through: it
answers the terminal's queries as VTE 0.84 would, strips them from what
clients see, and tracks the modes an attach has to re-assert.
`collins/service/termscreen.py` (stdlib only) is the screen model of
record: it consumes the filter's tokens and is what every automated read
(`takes_prompt`, `entered_prompt`, the spinner column, `capture_contents`)
will be made of, and what an attaching client is redrawn from
(`snapshot()`); its fidelity to VTE is pinned by the goldens in
`tests/fixtures/streams/` and `scripts/check_termscreen_parity.py`.
`collins/service/core.py` (GLib only) is `ServiceCore`, the request router:
its pty half owns the `PtyServer` and serves `spawn` (an agent's pty or a
panel shell's, a sandboxed shell on the plan of its box), `attach`,
`detach`, `paint`, `close`, `clear` (a shell's) and the `resize` / `focus`
/ `theme` events, and writes the panel history from the screen models
(`write_panel_history`); its store and state half owns `AppState` and
`SessionStore` and serves `subscribe`, `state.get` / `state.set` (the
service decides which keys a client writes: a device setting, an unknown
setting or one of the wrong type is `refused`), every `store.*` mutation
and `trust.*`, with `collins/service/storefeed.py` turning each save and
refresh into events per subscriber (archived sessions only once paged
in). The activity tracker still runs in the client (the window's), and
its verdicts go to the service as `store.flags` and come back as `item`
fields. PR-1.11 put the rest behind requests and events: the PR hub and
every `gh` call (`service/prfeed.py`), the notification history
(`service/notifications.py`; rows carry their text as msgid and args,
each client translating with `i18n.translate`), the session tools
(`service/tools.py`), the Sandboxed chip's requests (`service/sandbox.py`),
the diffs' marks (`service/diffs.py`), token use (`service/tokenuse.py`:
usage, the model catalog, the CLI's defaults, an icon's save) and the
long-running operations as jobs (`service/jobs.py`: `job.start` and `job`
events for the clone, the repository list, worktree trash and restore, a
chat folder's trust, icon generation, the login repair; `jobclient.py` is
the client's end). Client modules reach the service through
`apilink.current()` (the app's link, or the one an e2e check driving bare
widgets built through `scripts/e2e_service.py`). The service also writes
a panel shell's history from its model when the shell exits, under the
key `spawn`'s `history` and `panel.key` give it, and from the tab's three
saves (`panel.history`).
**The service is its own process** (PR-1.12b, §3.20): `collins-service`
(`collins/service/main.py`, the entry point in `pyproject.toml`; the
login-shell capture into the core's environment, `sd_notify`, SIGTERM /
SIGINT ending every session as *Stop sessions and quit* does with the ids
recorded as `resume_on_start`, exit 0). `collins/api/server.py` (Gio and
libsoup) is its `Soup.Server` on `api.sock` (`ApiServer`: a `SocketClient`
per `client_id` grouping that client's two WebSockets, the `_Sink` per
pty with the ack window; `hello`, `local` and `service.restart` served
there, everything else through `ServiceCore.handle`). `collins/api/
client.py` is the client's `SocketLink` (an `apilink.Link`): a daemon
thread with its own `GLib.MainContext` drives both WebSockets, `call`
blocks the calling thread on the **sync channel** (D26), `send` and every
event ride the **primary** and land on the main loop through one ordered
queue at `PRIORITY_DEFAULT`; `subscribe` is the one call drained inside
the call so the mirrors are filled when it returns; a `PtyClient` per tab
or panel shell is what the Phase 1 loopback client was. `collins/
connection.py` is the GTK-free `ConnectionManager` (find → start through
`systemctl --user start collins-service.service` for the default id, else
a detached spawn → wait → connect → connected; lost → backoff 1, 2, 5, 10,
30 s → reconnect, the mirrors `reset()` and resubscribed, every tab
`reattach()`ed, the window's "Reconnecting" banner meanwhile). The in-
process loopback is gone (D21); `tests/inproc.py` is the unit suite's
harness over a core, and every e2e check starts a `collins-service` of its
own (`scripts/e2e_service.py`). What the loopback answered inside a call
the socket answers a moment later on the main loop: a mirror write is
applied optimistically (D16) and a check that reads a write's outcome
waits for it (`e2e_service.settle`), never the other way round. **Until
PR-1.12c lands, a session with a live agent pty cannot be opened**: the
service refuses a second `spawn` of it (`refused`, "This session is
already running in the Collins service", `args.pty`), which the tab
paints as a spawn error; the panel shells a client spawned end with it
(`App.do_shutdown` → `close_panel_ptys`), the agents do not. The local
proof is read from **the client's own** `proof_path(app_id)` (never the
path the hello names: a remote service could name any file), a regular
0600 file of exactly 32 bytes owned by the user.
`collins/service/session.py` is a tab's `Session` (see "The tab"), the
first module the app did use: the tab's logic carved out of the widget,
reaching its terminal through the two ports in `collins/service/ports.py`
(`PtyPort`, `ScreenPort`): adapters over the service's pty and
`termscreen`.
`collins/service/ptyserver.py` (GLib only) is the pty table: it spawns each
child on a pty it holds the master of, runs the output through the filter
into the screen model, hands what is left to every attached sink, answers an
attach with a redraw from the model, lets the active client own the size,
queues every write behind a writability watch, reaps the child, keeps the
`ptys` row in `state.json` and saves the model to a file for a restart.

**Everything Claude-shaped runs on the CLI's own login.** Titles
(`titles.py`), project icons (`icongen.py`) and login repair
(`tokenrefresh.py`) are headless `claude -p` runs. The usage panel
(`usage.py`), the model catalog (`claudemodels.py`) and claude.ai archive
mirroring (`remotearchive.py`) read the OAuth token from
`~/.claude/.credentials.json` and call Anthropic's undocumented endpoints
directly. No separate API key exists anywhere. All of it runs on the
service, against its machine's login: the UI asks (`usage.get`,
`models.get` through `modelcatalog.py`, `models.defaults`, the `icon` and
`login.repair` jobs, `icon.save`).

**GitHub goes through `gh`.** Every PR read and action is a `gh` call
(`prstatus.py` transport) on the service's machine, asked through the
`pr.*` requests (`remoteprs.py`'s functions keep the old names); without
`gh` a PR is a number and an empty menu.

## Where state lives

| What | Where |
| --- | --- |
| The service's half: names, favorites, archived, project order, PR records, attachments, drafts, notifications (each row's text as `msgid` and `args`, plus its English `body` for an older build; a row written before PR-1.11 reads its body as its msgid), the service id, and the settings in `state.SERVICE_SETTINGS` | `~/.config/collins/state.json` (`AppState`, synchronous atomic writes) |
| The marks on each session's diff (`diff_notes`: session id → notes and highlights, `diffnotes.mark_record` each; kept for the git page's life, so only a crash leaves any) and the show_diffs asked for with no client attached (`pending_diffs`: session id → the tool's arguments) | `state.json` (the service's alone: `service/diffs.py`, `service/tools.py`) |
| This device's half: the settings in `state.DEVICE_SETTINGS` (appearance, geometry, keybindings, sounds, tray, Caffeine, composer, editor and git-page looks), and per service: panel layouts, editor states, the last active session | `~/.config/collins/ui-state.json` (`uistate.UiState`, written through `AppState`; `state.json.pre-split` is the one-time backup the first start after the split leaves) |
| Headless-run scratch cwd | `~/.config/collins/title-scratch/<uuid>` |
| Panel shell scrollback (the tab's saves, and the service's own write when a shell exits) | `~/.local/state/collins/panel_history/<session>[.<ordinal>].txt` |
| Chats virtual project | `~/.local/share/collins/chats/` |
| MCP config file | `~/.local/share/collins/<app id>/` |
| MCP socket | `$XDG_RUNTIME_DIR/collins/<app id>/mcp.sock` |
| The API socket and the local proof (32 random bytes, 0600, minted at service start; a client that can read it from its own filesystem is `local`) | `$XDG_RUNTIME_DIR/collins/<app id>/api.sock` and `local-proof` (0700 directory; with no runtime dir, `~/.local/state/collins/<app id>/`) |
| The sessions a stopping service ended, for a client to reopen | `state.json` (`resume_on_start`, the service's alone) |
| The service's single-instance lock (an `flock`, taken as its first step and held for its life; a second service for the id exits 0 on it) | `<runtime dir>/collins/<app id>/service.lock` |
| The service's systemd user unit (never enabled by a package; started on demand by the client) | `data/collins-service.service`, installed to `/usr/lib/systemd/user/` by the packages and `~/.local/share/systemd/user/` by `collins --install-desktop` |
| A sandboxed session's box: its `$HOME` (`home/`), the carrier its live grants mount in (`grants/`), its anchors | `~/.local/share/collins/sandbox/<box id>/` |
| Per-launch sandbox plan | `$XDG_RUNTIME_DIR/collins/<app id>/sandbox/<uuid>.json` (mode 0600, unlinked when the tab's shell exits; with no runtime dir, `~/.local/state/collins/sandbox/<app id>/` — never the temp dir, which every box shares) |
| Sandbox grants per session, keyed by box id; per-project defaults for new sessions; the session tools each sandboxed session is offered, by box id, over the defaults in the settings; the session → box map; the sandbox switches | `state.json` |
| The pty table (a row per live pty: kind, session, cwd, pid, size, box, plan, options) and the next pty id | `state.json` (`AppState.set_pty` / `remove_pty` / `set_pty_next_id`, written by the service's `PtyServer`: an agent's pty and a panel shell's) |
| A live pty's saved screen model (`termscreen.Screen.dump()` as JSON, for a restarted service's re-adoption, PR-3.6's keeper); removed with the pty's row, pruned at service start | `~/.local/state/collins/pty/<pty id>.model` |
| Model catalog, update-check stamp, fetched images | `~/.cache/collins/` |
| Everything of the CLI's | `~/.claude/` — read only |

Every one of these has an environment override used by tests, captures and
e2e checks: `COLLINS_APP_ID`, `COLLINS_PROJECTS_DIR`, `COLLINS_CLAUDE_CONFIG`,
`COLLINS_CHATS_DIR`, `COLLINS_CLAUDE_CREDENTIALS`, `COLLINS_USAGE_FIXTURE`,
`COLLINS_PR_STATUS_CACHE`, `COLLINS_SANDBOX_ROOT`, `COLLINS_BWRAP` (a fake
bubblewrap, like the fake `claude`), `COLLINS_BINDFS` and
`COLLINS_FUSERMOUNT` (the two tools a live grant is mounted with; a path
that doesn't exist says "not installed"), `COLLINS_PTY_STATE_DIR` (where
the service saves pty models), plus `XDG_CONFIG_HOME` / `XDG_STATE_HOME` /
`XDG_RUNTIME_DIR`.
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
  comment saying what it does and where it is read), its name in
  `state.SERVICE_SETTINGS` or `state.DEVICE_SETTINGS` (which file it lives
  in; `tests/test_state_split.py` fails until it is on a side), a row in `prefs.py`
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
    python3 scripts/run_e2e.py [--only NAME] [--shard N/5] # e2e checks behind a headless compositor
python3 scripts/verify_versions.py          # every version copy agrees
python3 -m collins.service.main --app-id X  # a service by hand (the checks start their own: scripts/e2e_service.py)
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
Xvfb as five time-balanced shards (`e2e-shard (N)`, fanned into the required
`e2e` check; weights live in `scripts/run_e2e.py`), wheel + `.deb` packaging
with `scripts/verify_wheel_data.py`, PPA source builds for noble and resolute,
an RPM build + `dnf install`, version verification, and a VitePress build of
the docs site (`docs`; a `<word>` outside a one-line code span breaks it). The
e2e jobs appear late in `gh pr checks` output — a run is green only when
`e2e` is listed and passed. When you change a signal signature or a method
the e2e scripts poke, grep `scripts/check_*.py`.

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
| Session discovery, the store and its mirror, sidebar, state.json and its mirror, titles, worktrees, background agents, busy detection, adding and cloning projects | `sessions` `providers` `store` `remotestore` `models` `state` `remotestate` `sidebar` `titles` `bgstatus` `activity` `trust` `chats` `projecticons` `clonerepo` `clonedialog` | `collins-sessions-and-sidebar` |
| The session tab: VTE, spawn/resume/attach, close flows, prompt-line reading, links, footer, transcript resolver | `terminal` `window` `shellinput` `linkpatterns` `transcriptlinks` `transcript` `vtehtml` `proctree` `taborder` | `collins-terminal-tab` |
| Service (the split): the pty stream filter, query responder and mode tracker; the screen model of record with its goldens, parity check and redraw; the pty server (spawn, the read loop and write queue, attach by redraw, the active client's size, sinks and their flow control, exit, the saved model, the `ptys` table); the request router and the in-process loopback; a tab's `Session` behind its pty and screen ports; the client side of the pty server (the childless VTE, the redraw guard, the mouse coalescing, the ports over the service); the API's message table, validation, framing, binary header, protocol version | `service/termstream` `service/termscreen` `service/ptyserver` `service/core` `service/storefeed` `service/jobs` `service/diffs` `jobclient` `remotediffs` `service/session` `service/ports` `api/server` `api/client` `connection` `service/main` `ptyclient` `api/protocol` `apilink` `remotestore` `remotestate` | `collins-terminal-tab` (the stream, the screen model, the pty server, Session, the client side), `collins-panel-dock` (panel shells on the pty server, the history the service writes), `collins-session-mcp-tools` (the protocol), `collins-sessions-and-sidebar` (the store and state over the API, the mirrors) |
| Sandboxed sessions: the bubblewrap mount plan, the host launcher, the sticky flag and per-project override, the new-chat checkbox, trust mirroring, the /bg and attach refusals, the probe and the Preferences group, the footer chip with its grants and restart, live grants (a directory allowed while a session runs, mounted into the running box), the sandboxed panel shell, the session tools a sandboxed session is offered and the policy for the ones that reach the host, a worktree launch narrowed to its worktree | `sandboxplan` `sandboxrun` `sandboxgrants` `sandboxchip` `sandboxstatus` `service/sandbox` | `collins-sandboxed-sessions` |
| Panel docking: strips, splits, DnD, layout persistence, sizes | `docktree` `dockzones` `paneldock` `panelstrip` `paneldnd` `tabguard` `panellayout` `panelhistory` `panedsizer` `panelsizing` `panelkeys` | `collins-panel-dock` |
| Composer, drafts, the new-chat screen, model/effort pickers, drops and pastes | `composer` `composerkeys` `newchat` `newchatview` `modelmenu` `dropimages` | `collins-composer-and-new-chat` |
| Session MCP tools, the shim, the socket service, lightbox and attachments | `mcp_shim` `mcptools` `mcpserver` `service/tools` `toolclient` `remoteimages` `lightbox` `attachrecords` `attachpanel` `pictures` `animatedimage` | `collins-session-mcp-tools` |
| Pull requests: status, hub, detail page, body markdown, actions, menus, gh setup | `prstatus` `prstore` `service/prfeed` `remoteprs` `prdetail` `practions` `prmenu` `prview` `mdblocks` `mdwidgets` `prattach` `prblobs` `prfileimages` `avatars` `bodyimages` `ghsetup` `ghwelcome` | `collins-pull-requests` |
| The git page: the native diff view, its GTK-free model and staging arithmetic, the commits and files sidebar, the loads vocabulary, git info, the panels' model and git runners | `gitpage` `gitsidebar` `diffview` `diffmodel` `diffnotes` `gitpatch` `gitloads` `gitinfo` `gitmodel` `gitops` `commitcard` `gitoperation` `keyedslots` `imagediff` | `collins-git-page` |
| Editor panel: file tree, quick open, pop-out, narrow mode | `editor` `editorfiles` `filetree` `quickopen` `fuzzy` `fileclipboard` `editorwindow` `filetypes` | `collins-editor-panel` |
| Notifications, bell, cards, sounds, status icon, dock badge, update check, Caffeine | `notifycenter` `service/notifications` `remotenotify` `notifyoverlay` `notifypanel` `notifysound` `statusicon` `traymodel` `flash` `updatecheck` `caffeine` | `collins-notifications-and-tray` |
| Everything that spends tokens or calls Anthropic: titles, models, usage, login repair, welcome, icon generation, claude.ai archive | `titles` `claudemodels` `modelcatalog` `usage` `usagepanel` `tokenrefresh` `tokensettings` `service/tokenuse` `service/jobs` `jobclient` `welcome` `welcomegate` `clisetup` `icongen` `remotearchive` | `collins-token-use-and-claude-api` |
| Preferences dialog, keybindings, themes, translations | `prefs` `prefslayout` `prefssearch` `keybindings` `keymap` `keybindingsdialog` `themes` `i18n` `po/` | `collins-preferences-keybindings-i18n` |
| Testing: unit suite rules, e2e checks, shims, headless probes | `tests/` `scripts/check_*.py` `scripts/run_e2e.py` | `collins-testing` |
| Packaging and CI: wheel, deb, rpm, PPA, COPR, AUR, the CI image, versions | `pyproject.toml` `debian/` `packaging/` `.github/` `scripts/` | `collins-packaging-and-ci` |
| GTK / libadwaita / VTE / CSS traps that are not feature-specific | — | `collins-gtk-sharp-edges` |

Also in `.agents/`: `capture-screenshots` (headless captures of a throwaway
instance), `gpl-modified-file-notices` (mandatory before committing),
`balance-e2e-shards` (refresh the e2e timing table, change the shard count),
`release-branch-skill`, `ship-release-skill`.
