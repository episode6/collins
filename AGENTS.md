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

**Two processes.** Collins is a headless **service** (`collins-service`,
`collins/service/main.py`) and a GTK **client** (the window, `collins`),
joined by a Unix socket (`~/specs/collins/split-service-and-client.md` is
the design). The service is a plain Python program on a GLib loop and
imports no GTK (`tests/test_service_imports.py` pins every module of
`collins/service/` and `collins/api/`); it owns `~/.claude`, `state.json`,
every agent's pty, `gh` and `git`, the tokens and the sandboxes. The client
owns widgets, `ui-state.json` and what a person did at its screen. **The
service decides, the client shows**: busy, status, unread, which session a
pty belongs to, whether a prompt would land are service facts delivered as
events, and the client never infers one from its own VTE. A client that
goes away, however it goes, changes nothing on the service except "no
client is attached": quitting the window or crashing it ends no session.
Several windows, and several clients, can share one service.

**The service process.** `service/main.py` takes the `flock` on
`service.lock` first (a second service for the app id exits 0), captures the
login shell's environment once (`$SHELL -lic 'env -0'`, fail-soft) and
**overlays** it on the service's own (the user's variables and `PATH` order
win; `COLLINS_*`, `XDG_*`, `PYTHONPATH`, `HOME`, `USER` and the systemd
variables are protected, and the service's own leading `PATH` entries stay
first), builds `ServiceCore`, starts
the store, tracker, background agents, sandbox host, session tools and MCP
socket, listens, then `sd_notify`s ready. `SIGTERM` / `SIGINT` / a client's
`service.restart` run the stop sequence: clients disconnected first, every
session ended as *Stop Sessions and Quit* does with its id recorded as
`resume_on_start`, exit 0 (so a unit's `Restart=on-failure` leaves it
down). `collins-service --check` and `--print-socket` are the headless
box's helpers. `data/collins-service.service` is its systemd user unit, never
enabled by a package. `service/core.py` is `ServiceCore`, the request
router, with one module per concern it routes to: the pty half owns the
`PtyServer` (`service/ptyserver.py`: spawns each child on a pty it holds the
master of, runs output through the stream filter into the screen model,
answers an attach with a redraw from the model, lets the active client own
the size, queues writes, reaps the child, keeps the `ptys` row in
`state.json` and a saved model on disk); `service/termstream.py` (stdlib
only) is the filter, query responder and mode tracker that answers the
terminal's queries as VTE 0.84 would and strips them from what clients see;
`service/termscreen.py` (stdlib only) is the screen model of record that
every automated read is made of (`takes_prompt`, the spinner column,
`capture_contents`) and an attaching client is redrawn from, its fidelity to
VTE pinned by `tests/fixtures/streams/` and
`scripts/check_termscreen_parity.py`. The store and state half owns
`AppState` and `SessionStore` and serves `subscribe`, `state.get` /
`state.set` (the service decides which keys a client writes: a device
setting, an unknown setting or one of the wrong type is `refused`), every
`store.*` mutation and `trust.*`; `service/storefeed.py` turns each save and
refresh into events per subscriber. The rest, each a module of its own:
the PR hub and every `gh` call (`prfeed`), the notification history
(`notifications`: rows carry msgid and args, each client translating with
`i18n.translate`), the session tools (`tools`), the Sandboxed chip's requests
(`sandbox`), the diffs' marks (`diffs`), token use (`tokenuse`) and the
long-running operations as jobs (`jobs`: `job.start` and `job` events;
`jobclient.py` is the client's end).

**A session lives on the service.** The `Session` (`service/session.py`,
GTK-free, fake-port tested in `tests/test_session.py`) is the logic behind a
tab: the launch (command, sandbox plan, worktree checks, restart), reading
and writing the CLI's input box (`takes_prompt`, prompts, switches, the
composer's cut), the transcript tail and the resolver that binds a freshly
spawned session to the id the CLI mints, the cwd poll, and the close flows'
keystrokes and polls. `ServiceCore.sessions` holds one per live agent pty,
built by `spawn` and hosted by `service/hosting.py`'s `SessionRecord` (its
host, its two ports `service/ports.py` over the pty and screen model, the
cut sinks, and the facts it pushes as `session` events: whole on `attach`,
changed fields after). `service/tracking.py`'s `ServiceActivity` is the
busy / finish tracker (fed by the filter's `Progress` events, every pty's
output, the input frames and a `/proc` poll; `service/finish.py` judges a
finish edge against the transcript); it sets `busy` and a counted finish's
`unread` on the store's items. `service/bgagents.py` is the background
agents (the `claude agents` poller, the /bg handoff and its fork watch, the
pending-detach replay, the link repair, the busy feed), and `bgblock.py`
(GTK-free) is the /bg gate both halves read. Their verdicts reach a client as `item`
fields (`busy`, `unread`, `background`, `backgrounding`, `can_background`,
`running`); `store.flags` carries only what a person did at a client's
screen (`status`, `unread: false`). `running` is "a live agent pty whose
CLI runs" and follows the CLI's life (`ServiceCore.cli_changed`), so a shell
left behind after the agent exits is not a running row. The service
refuses to spawn a second CLI on a session already running (`refused`,
"This session is already running in the Collins service", `args.pty`; fork
spawns are exempt), and the client attaches instead.

**The wire.** `api/protocol.py` (stdlib-only) is the message table (types,
direction, fields and bounds), `validate` / `validate_response`, JSON
framing, the 16-byte binary header and the `PROTOCOL` / `MIN_PROTOCOL`
window (a client speaks the service's protocol or the one before it).
`api/server.py` (Gio and libsoup) is the service's `Soup.Server` on
`api.sock`: a `SocketClient` per `client_id`, grouping its two WebSockets
(the **primary** carries the subscription, every event and every output and
input frame; the **sync channel** answers `call`), the sink per pty with
its ack window, and `hello`, `local` and `service.restart`. `api/client.py`
is the client's `SocketLink` (an `apilink.Link`): a daemon thread with its
own `GLib.MainContext` drives both sockets, `call` blocks the calling thread
on the sync channel (**never call from the I/O thread**), `send` and every
event ride the primary and land on the main loop through one ordered queue
at `PRIORITY_DEFAULT`; `subscribe` is the one call on the primary, the main
thread draining the queue until it returns so the mirrors are filled. A
`PtyClient` per tab or panel shell routes the output frames. The `local`
capability is proven, not inferred: the client computes `proof_path(app_id)`
itself (never the path a hello names), opens it `O_NOFOLLOW`, requires a
regular 0600 file of its own uid of exactly 32 bytes, and otherwise sends no
proof. Client modules reach the service through `apilink.current()`.

**The client.** `collins/app.py` (`App(Adw.Application)`) loads the CSS,
prepends the bundled icon path, owns Caffeine Mode, the status icon and the
notification center's app-level fan-out (the delivery, the withdraws, the
badge) and builds the connection. `collins/connection.py` is the GTK-free
`ConnectionManager`: find → start (`systemctl --user start
collins-service.service` for the default app id, else a detached spawn of
`collins-service`) → wait → connect → connected; on lost, backoff 1, 2, 5,
10, 30 s, the mirrors `reset()` and resubscribed, every tab re-attached, the
window's "Reconnecting" banner meanwhile; a protocol mismatch is its own
state. `collins/window.py` (`MainWindow`) composes the sidebar with the tab
view, installs every `win.*` action and owns the tab lifecycle: open,
resolve, close (graceful exit / `/bg` / hide), **detach**, archive, quit and
**restart service** flows. A tab opened on a running session attaches (`attach`
plus the `session` snapshot) instead of spawning; `open_tabs` in
`ui-state.json` is written on every tab change and reopened at launch and
after a reconnect. The quit dialog's *Quit* detaches every tab;
`quit_with_running_sessions` defaults to `detach`. There can be several
windows; a tab can move between them.

**The tab.** `terminal.py`'s `TerminalTab` is a childless VTE
(`ptyclient.ClientTerminal`: output frames are `feed()`, the VTE's commits go
back as input, the redraw guard, the mouse coalescing) showing a pty of the
service, a footer (cwd, branch, model, effort, PR chips), a `PanelDock`
around the terminal (`paneldock.py` realizing a GTK-free `docktree.DockTree`
of `Gtk.Paned`s whose leaves are `PanelStrip`s of duck-typed `PanelPage`s:
shells, PR pages, the composer, the attachments gallery, the git page), an
`EditorPane` in its own end slot, and overlays (composer, attachments,
lightbox). Its session is the mirror `clientsession.ClientSession`
(`tab.session`, GTK-free): the last `session` event's facts, read with no
round trip, and the requests the tab makes (`prompt`, `write`, `switch`,
`send`, `cut`, `draft.restore`, `close`, ...); `apply(event)` turns the
fields that moved into the tab's GObject signals, and every old name on the
tab forwards to it. Each panel shell is a `PanelTerminal`, a `ClientTerminal`
over a service pty of kind `shell` (a sandboxed one spawned by the service
on its box's plan), its text, foreground and cwd read on the service
(`pty.info`, `pty.capture`), its history written by the service from the
screen model and painted back into the new pty's stream on reopen. The
service reads the CLI's OSC 9;4 progress off the stream filter; the
client's VTE only draws.

**Data layer (GTK-free, unit-tested).** `sessions.py` discovers and parses
transcripts; `providers.py` wraps the `claude` CLI (commands, prompt-line
grammar, background agents); `state.py` is `AppState`, the one writer of
`~/.config/collins/state.json` with `DEFAULT_SETTINGS` as the settings
catalogue, and the owner of `uistate.UiState`, the one writer of
`ui-state.json` (`state.json` is what the service keeps, `ui-state.json`
what this device keeps; `SERVICE_SETTINGS` / `DEVICE_SETTINGS` say which
side a setting lives on, and every read and write goes through `AppState`'s
one API, which routes); `store.py` is `SessionStore`, the single source of
truth between disk and UI (threaded scans, `Gio.FileMonitor`s, grouping,
every state mutation). **Both are the service's**: `ServiceCore` owns the
`AppState` (`migrate=True, device=False`: it writes the split, never
`ui-state.json`) and the `SessionStore`. The UI holds the **mirrors**:
`remotestate.RemoteState` (an `AppState` subclass over a copy the subscribe
snapshot fills and `state.set` events keep; writes optimistic, reverted with
a toast on a refusal; `get_setting` reads this device's `UiState` first) and
`remotestore.RemoteStore` (the store's four signals, a `Gio.ListStore` of
`SessionItem`s and methods, filled by `item` / `rows` / `put-away` events,
every mutation a `store.*` request). `app.state`, `app.store`,
`window.state` and `window.store` are the mirrors; the sidebar and windows
observe `refreshed`, `busy-changed`, `unread-changed`, `archived` and
per-item `SessionItem` property notifications (`models.py`) on them.
`sandboxstatus.py` holds the sandbox probe's verdict as the service reports it
(the probe runs only on the service).

**Hubs, not wires.** The app repeatedly replaced lattices of hand-run signals
with one owner that everybody subscribes to: `SessionStore` (sessions, seen
by the UI through its mirror `RemoteStore`), `prstore.PrStore` (all
pull-request state: the service store's own, seen by the UI as
`store.pr_store`, the mirror `remoteprs.RemotePrStore` with the same three
signals and equality guard), `notifycenter.NotificationCenter` (every
notification, the badge's number, the delivery table: the history and the
unread set are the service's, `service/notifications.py`, and
`app.notification_center` is the mirror `remotenotify.RemoteNotifications`,
which applies the delivery table with this client's focus), `traymodel`
(what the status icon shows), `keybindings` (every shortcut). New surfaces
read from the hub and subscribe to its signals; new writes go through the
hub. Do not reintroduce per-surface relays.

**What the session can call.** `mcp_shim.py` (stdlib-only, spawned by the CLI
via `--mcp-config`) relays MCP over a Unix socket to `mcpserver.py`
(Gio-only), which the service starts (`ServiceCore.start_mcp`);
`mcptools.py` (GTK-free) holds the tool table, schemas, framing, runtime
paths and the sandbox policy; the dispatcher and the handlers live in
`service/tools.py` (the tools a session's own data serves run there, a
UI-bound one is a `tool` event to the session's active client and a deferred
reply settled by its `tool-reply` or at a 14 s bound, and each has its
no-client behaviour), and the client halves of the UI-bound ones in
`toolclient.py`. Session identity is the shim's kernel-verified pid walked up
`/proc` to a service `Session`.

**The debug API.** With `COLLINS_DEBUG_API=1` in the service's environment the
`debug.*` requests are served (an attribute of a session read, written or
called by name, a pty's screen and process facts, a call on the sandbox host
or the live grants, the stubs of `scripts/e2e_stubs.py`); a service started
otherwise refuses them as `unknown`, and the flag is stripped from the shells
it spawns. The e2e checks reach past the protocol only through it
(`tab.probe` / `probe_set` / `probe_call`).

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

**Git goes over the API** (§3.23, D33). The git page, the sidebar's
menus, the footer's branch and the project row's pull and checkout keep
calling `gitinfo` and `gitops`; in the client those reach the service:
every runner maps its argv back to the **builder** that makes it
(`gitops.BUILDERS`: every `*_argv` plus the nine ad-hoc argv promoted to
builders) and sends `git.run {cwd, builder, args}` — the wire never
carries an argv, and the service runs only what its own builder makes —
`gitinfo`'s `.git` reads are a per-cwd mirror of `git.info`
(`remotegit.py`; the readers themselves are `gitfiles.py`, the service's),
a blob is `GET /api/blob?kind=git` (`blobcache.py` caches it), the page's
watch is `git.watch` / `git-changed` on the service (`service/gitfeed.py`)
and a plan is `git.plan`, refused `stale` when a stable key moved;
`service/files.py` is the trash and the rule confining every path to a
root the service knows (or anything for a `local` client). The client
opens no project file and runs no git: `tests/test_client_is_pathless.py`
walks the GTK modules and the client helpers for filesystem and
subprocess sites and holds them to `tests/pathless_allowlist.py`, which
shrinks per Phase 2 chunk and never grows.

## Where state lives

| What | Where |
| --- | --- |
| The service's half: names, favorites, archived, project order, PR records, attachments, drafts, notifications (each row's text as `msgid` and `args`, plus its English `body` for an older build; a row written before msgids reads its body as its msgid), the service id, and the settings in `state.SERVICE_SETTINGS` | `~/.config/collins/state.json` (`AppState`, synchronous atomic writes) |
| The marks on each session's diff (`diff_notes`: session id → notes and highlights, `diffnotes.mark_record` each; kept for the git page's life, so only a crash leaves any) and the show_diffs asked for with no client attached (`pending_diffs`: session id → the tool's arguments) | `state.json` (the service's alone: `service/diffs.py`, `service/tools.py`) |
| This device's half: the settings in `state.DEVICE_SETTINGS` (appearance, geometry, keybindings, sounds, tray, Caffeine, composer, editor and git-page looks), and per service: panel layouts, editor states, the last active session, the open tabs (`open_tabs`: session ids and `pty:<id>`, reopened at launch and after a reconnect) | `~/.config/collins/ui-state.json` (`uistate.UiState`, written through `AppState`; `state.json.pre-split` is the one-time backup the first start after the split leaves) |
| Headless-run scratch cwd | `~/.config/collins/title-scratch/<uuid>` |
| Panel shell scrollback (the tab's saves, and the service's own write when a shell exits) | `~/.local/state/collins/panel_history/<session>[.<ordinal>].txt` |
| Chats virtual project | `~/.local/share/collins/chats/` |
| MCP config file | `~/.local/share/collins/<app id>/` |
| MCP socket | `$XDG_RUNTIME_DIR/collins/<app id>/mcp.sock` |
| The API socket and the local proof (`local-proof`: 32 random bytes, 0600, minted at each service start; the client reads it from its *own* filesystem at a path it computes itself, and sends no proof if it is not a regular 0600 file of its uid) | `$XDG_RUNTIME_DIR/collins/<app id>/api.sock` and `local-proof` (0700 directory; with no runtime dir, `~/.local/state/collins/<app id>/`) |
| The sessions a stopping service ended, resumable when a client reopens its `open_tabs` | `state.json` (`resume_on_start`, the service's alone) |
| The service's single-instance lock (an `flock`, taken as its first step and held for its life; a second service for the id exits 0 on it) | `<runtime dir>/collins/<app id>/service.lock` |
| The service's systemd user unit (never enabled by a package; started on demand by the client) | `data/collins-service.service`, installed to `/usr/lib/systemd/user/` by the packages and `~/.local/share/systemd/user/` by `collins --install-desktop` |
| A sandboxed session's box: its `$HOME` (`home/`), the carrier its live grants mount in (`grants/`), its anchors | `~/.local/share/collins/sandbox/<box id>/` |
| Per-launch sandbox plan | `$XDG_RUNTIME_DIR/collins/<app id>/sandbox/<uuid>.json` (mode 0600, unlinked when the tab's shell exits; with no runtime dir, `~/.local/state/collins/sandbox/<app id>/` — never the temp dir, which every box shares) |
| Sandbox grants per session, keyed by box id; per-project defaults for new sessions; the session tools each sandboxed session is offered, by box id, over the defaults in the settings; the session → box map; the sandbox switches | `state.json` |
| The pty table (a row per live pty: kind, session, cwd, pid, size, box, plan, options) and the next pty id | `state.json` (`AppState.set_pty` / `remove_pty` / `set_pty_next_id`, written by the service's `PtyServer`: an agent's pty and a panel shell's) |
| A live pty's saved screen model (`termscreen.Screen.dump()` as JSON, for a restarted service's re-adoption, a later phase's keeper); removed with the pty's row, pruned at service start | `~/.local/state/collins/pty/<pty id>.model` |
| Model catalog, update-check stamp, fetched images | `~/.cache/collins/` |
| Blobs fetched over the API (`GET /api/blob`), with the ETag kept beside each (`blobcache.py`) | `~/.cache/collins/blobs/<service id>/<sha1 of the url>` |
| Everything of the CLI's | `~/.claude/` — read only |

Every one of these has an environment override used by tests, captures and
e2e checks: `COLLINS_APP_ID`, `COLLINS_PROJECTS_DIR`, `COLLINS_CLAUDE_CONFIG`,
`COLLINS_CHATS_DIR`, `COLLINS_CLAUDE_CREDENTIALS`, `COLLINS_USAGE_FIXTURE`,
`COLLINS_PR_STATUS_CACHE`, `COLLINS_SANDBOX_ROOT`, `COLLINS_BWRAP` (a fake
bubblewrap, like the fake `claude`), `COLLINS_BINDFS` and
`COLLINS_FUSERMOUNT` (the two tools a live grant is mounted with; a path
that doesn't exist says "not installed"), `COLLINS_PTY_STATE_DIR` (where
the service saves pty models), `COLLINS_DEBUG_API=1` (the service serves the
`debug.*` probe and applies `scripts/e2e_stubs.py`; never set it outside a
check), plus `XDG_CONFIG_HOME` / `XDG_STATE_HOME` /
`XDG_RUNTIME_DIR`.
Diagnostics: `COLLINS_LOG=INFO` (the service's too: a window-spawned service
goes to `/dev/null` unless it is set; under the unit it is the journal), `COLLINS_SHIM_LOG=<file>`,
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
python3 -m collins.service.main --app-id X  # a service by hand (--check, --print-socket too)
./start-debug                               # a debug instance (COLLINS_APP_ID=com.episode6.Collins.Debug)
```

GTK apps are single-instance per application id: launching with an id that is
already running only activates the existing window. Never test against the
user's live instance; mint a fresh `COLLINS_APP_ID` and a per-run scratch tree
(the `capture-screenshots` skill has the exact recipe, and
`COLLINS_CHATS_DIR` there is not optional — omitting it reaps the user's chat
directories). Scripts run from outside the repo import the system-installed
`collins` — set `PYTHONPATH=<worktree>` or `sys.path.insert(0, repo_root)`.

The app is two processes, so a check or probe that builds an `App()` first
starts a service of its own: `e2e_service.start_service()` (in
`scripts/e2e_service.py`) right before `App()`, after the scratch tree, the
`COLLINS_*` / `XDG_*` overrides (`XDG_RUNTIME_DIR` included) and the `claude`
shim are in the environment, because the service spawns the shells that run
it; it runs out of the checkout with `COLLINS_DEBUG_API=1` and dies with the
check. A monkeypatch in the check's own process never reaches the service:
reach session logic through `tab.probe` / `probe_set` / `probe_call` and
stub service modules with `e2e_service.start_service(stubs=...)`. A write's
outcome lands a moment later on the main loop (`e2e_service.settle`).

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
| Session discovery, the store and its mirror, sidebar, state.json and its mirror, titles, worktrees, background agents, busy detection, adding and cloning projects | `sessions` `providers` `store` `remotestore` `models` `state` `remotestate` `sidebar` `titles` `bgstatus` `bgblock` `service/bgagents` `activity` `trust` `chats` `projecticons` `clonerepo` `clonedialog` | `collins-sessions-and-sidebar` |
| The session tab: VTE, spawn/resume/attach, close flows, prompt-line reading, links, footer, transcript resolver | `terminal` `window` `shellinput` `linkpatterns` `transcriptlinks` `transcript` `vtehtml` `proctree` `taborder` | `collins-terminal-tab` |
| Service (the split): `collins-service`'s process (lock, login-shell capture, stop sequence, `--check`); the pty stream filter, query responder and mode tracker; the screen model of record with its goldens, parity check and redraw; the pty server (spawn, the read loop and write queue, attach by redraw, the active client's size, sinks and their flow control, exit, the saved model, the `ptys` table); the request router; a session on the service (`Session`, its hosted record and pushed facts, the busy and finish tracker, background agents); the socket (server, client with its sync channel, the connection manager and its reconnect); the client's mirror of a session, the childless VTE and its ports; the API's message table, validation, framing, binary header, protocol version; the debug API | `service/main` `service/core` `service/termstream` `service/termscreen` `service/ptyserver` `service/storefeed` `service/jobs` `service/diffs` `service/gitfeed` `service/files` `service/session` `service/hosting` `service/ports` `service/tracking` `service/finish` `service/bgagents` `api/protocol` `api/server` `api/client` `apilink` `connection` `clientsession` `ptyclient` `jobclient` `remotediffs` `remotegit` `blobcache` `remotestore` `remotestate` `sandboxstatus` `bgblock` | `collins-terminal-tab` (the stream, the screen model, the pty server, Session, the client side, detach and reopen), `collins-panel-dock` (panel shells on the pty server, the history the service writes), `collins-session-mcp-tools` (the protocol), `collins-sessions-and-sidebar` (the store and state over the API, the mirrors, running rows, background agents), `collins-sandboxed-sessions` (the sandbox host on the service), `collins-testing` (the per-check service, the debug API) |
| Sandboxed sessions: the bubblewrap mount plan, the host launcher, the sticky flag and per-project override, the new-chat checkbox, trust mirroring, the /bg and attach refusals, the probe and the Preferences group, the footer chip with its grants and restart, live grants (a directory allowed while a session runs, mounted into the running box), the sandboxed panel shell, the session tools a sandboxed session is offered and the policy for the ones that reach the host, a worktree launch narrowed to its worktree | `sandboxplan` `sandboxrun` `sandboxgrants` `sandboxchip` `sandboxstatus` `service/sandbox` | `collins-sandboxed-sessions` |
| Panel docking: strips, splits, DnD, layout persistence, sizes | `docktree` `dockzones` `paneldock` `panelstrip` `paneldnd` `tabguard` `panellayout` `panelhistory` `panedsizer` `panelsizing` `panelkeys` | `collins-panel-dock` |
| Composer, drafts, the new-chat screen, model/effort pickers, drops and pastes | `composer` `composerkeys` `newchat` `newchatview` `modelmenu` `dropimages` | `collins-composer-and-new-chat` |
| Session MCP tools, the shim, the socket service, lightbox and attachments | `mcp_shim` `mcptools` `mcpserver` `service/tools` `toolclient` `remoteimages` `lightbox` `attachrecords` `attachpanel` `pictures` `animatedimage` | `collins-session-mcp-tools` |
| Pull requests: status, hub, detail page, body markdown, actions, menus, gh setup | `prstatus` `prstore` `service/prfeed` `remoteprs` `prdetail` `practions` `prmenu` `prview` `mdblocks` `mdwidgets` `prattach` `prblobs` `prfileimages` `avatars` `bodyimages` `ghsetup` `ghwelcome` | `collins-pull-requests` |
| The git page: the native diff view, its GTK-free model and staging arithmetic, the commits and files sidebar, the loads vocabulary, git info and its mirror, the builders and runners over the API, the panels' model | `gitpage` `gitsidebar` `diffview` `diffmodel` `diffnotes` `gitpatch` `gitloads` `gitinfo` `gitfiles` `remotegit` `gitmodel` `gitops` `service/gitfeed` `service/files` `blobcache` `commitcard` `gitoperation` `keyedslots` `imagediff` | `collins-git-page` |
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
