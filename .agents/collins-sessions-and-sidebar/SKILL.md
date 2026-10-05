---
name: collins-sessions-and-sidebar
description: >-
  How Collins finds, models, persists and lists Claude Code sessions: transcript
  discovery and parsing (sessions.py, providers.py), the SessionStore hub and
  SessionItem view-models, AppState and state.json, the sidebar's rows, groups,
  guide lines and status/busy/unread flags, session titles, worktrees and
  worktree recovery, /bg forward chains and background-agent status, folder
  trust, the Chats virtual project, project icons, and adding or cloning a
  project (clonerepo.py, clonedialog.py). Use when touching
  anything the sidebar shows, how a session is discovered or grouped, what is
  saved in state.json, archive/favorite/trash semantics, or busy/idle
  detection for a row.
---

# Sessions, the store and the sidebar

## Discovery (`sessions.py`, `providers.py`)

Claude Code writes one JSONL per session at
`~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`, where the directory name is
the cwd with every non-alphanumeric character replaced by `-`.
`ClaudeProvider.discover()` walks that tree (overridable with
`COLLINS_PROJECTS_DIR`), skips the headless-run scratch projects
(`titles.is_scratch_project`), empty files, non-UUID names and metadata-only
stubs (`transcript_is_stub` — e.g. worktree agent runs that never got a
prompt), and reads a **small prefix** for `cwd`, the first user prompt and the
creation time plus a **64 KB tail** for the interrupted marker and the CLI's
own title records (`_scan_tail`). Nothing else is read at scan time; the
store does this off the main thread and lands on `PRIORITY_DEFAULT`.

`Provider` is the abstraction over the CLI: resume/new/continue command
strings (with the `--mcp-config` flag), graceful exit (`Ctrl+C Ctrl+C`),
background exit (`/bg`), `background_agents()` (`claude agents --json`, with
`stdin=DEVNULL` — the CLI raw-modes any tty stdin even for that subcommand),
the prompt-line grammar (`takes_prompt`, `entered_prompt`,
`clear_prompt_keys`), worktree-failure detection and `file_reference`
(`@path#L2-4`; column syntax is not parsed by the CLI). Only
`ClaudeProvider` exists and only ever will — keep the abstraction, don't
advertise it.

Transcript facts the data layer relies on (all undocumented, verified against
real files): the first `cwd` is **not** where a session began (a `/bg` fork
copy rewrites every `cwd` to the worktree); `resume_cwd` uses the last
recorded cwd; a `worktree-state` record says which worktree a session lived
in; `bridge-session` names a claude.ai counterpart; `ai-title` /
`custom-title` / `agent-name` records carry the CLI's names, the last of any
type winning; `pr-link` records name PRs; every user turn carries
`permissionMode`; every assistant line carries `message.model` (skip
`<synthetic>` and `isSidechain` lines) and a top-level `effort`; a slash
command the CLI ran locally is a user line whose content is
`<command-name>/x</command-name>…<command-args>…</command-args>` followed by
a user line starting `<local-command-stdout>` with what it printed.

**Worktrees.** `worktree_project_root(cwd)` maps `<repo>/.claude/worktrees/x`
back to `<repo>` — every "which project is this" question goes through it,
or the sidebar grows phantom projects named after worktree directories and
"New session" cascades into a worktree. When a `-w` session exits clean with
nothing to keep, the CLI deletes the worktree **and its branch**; on resume
the CLI re-enters the worktree whenever the directory exists, so
`sessions.recreate_worktree` puts it back from the last live `worktree-state`
before resuming (`git worktree add -f -f`: the CLI's lock outlives the
session, and a single `-f` fails silently). The CLI also *moves* a transcript
to the worktree's project directory the moment a session enters one; the
service follows it (`ServiceCore.sync_transcript_paths` on every refresh of
its store: a live session whose transcript path is gone is re-aimed at the
path the scan found, PR-1.12d), and the client hears the session's
`transcript_path` and the row's `path`, never stat-ing a transcript.

## The store (`store.py`) and the view-model (`models.py`)

`SessionStore` is the single source of truth between disk and UI: it owns the
threaded scan, `Gio.FileMonitor`s on each provider's projects dir (debounced),
grouping and ordering, and **every** state mutation (rename, favorite,
archive, trash, project order, forward chains). It reuses `SessionItem`s
across refreshes so property bindings survive; `refreshed(order_changed)` says
whether rows must be rebuilt. `SessionItem` properties the sidebar binds to:
`display_name`, `subtitle`, `preview`, `favorite`, `status` (`""` | `open` |
`attention` | `background`), `state` (`""` | `interrupted`), `busy`, `unread`,
`syncing`, `backgrounding`, `can_background`, `background` (`""` |
`running` | `pending`: the service's word that the row's conversation runs
as a /bg agent, PR-1.12d), `running`. `unread-changed`,
`busy-changed` and `archived` signals fire from the setters so the badge and
notification center follow without callers remembering to announce.

Two traps in `_apply`: it **deletes the item of any out-of-sight session**, so
`set_unread(id, False)` after an archive is a silent no-op — anything that
takes a row out of sight must clear what it carried first (`_put_away` is
that hook). And `display_name` is a precedence chain: manual name > CLI title
(only when `cli_title_sessions` is on) > generated title / PR title > local
first-words title. A manual rename always wins.

`store.pr_store` is the PR hub (see `collins-pull-requests`). `titles` are
requested for sessions that appear while the app runs (the backlog at launch
gets the free local title only) and persisted so each is generated once;
`prattach` reads each new session's first prompt for PR URLs the same way
(URLs only — a bare "PR 12" is not attached). `store.is_virtual_project(name)` (flagged **and** no sessions) is the
question "does this header stand for a folder with nothing in it";
`state.is_virtual_project` alone is stale data.

## The store and the state over the API (`remotestore.py`, `remotestate.py`)

Since PR-1.10 (split-service spec §3.8, §3.15) the `SessionStore` and the
`AppState` above are **the service's**: `service.core.ServiceCore` owns
them (`ServiceCore.with_state()` builds the state with `migrate=True,
device=False`; `start_store()` the store) and `service/storefeed.py`
publishes them. Everything in the UI — `app.state`, `app.store`,
`window.state`, `window.store`, the sidebar, Preferences, the tabs' settings
dict — holds the client's **mirrors** on one `api.client.SocketLink`
(`App._start_service_client`):

- `RemoteState` *is* an `AppState` (subclass): the same methods and
  attributes, every read local. Its `_load` reads only `ui-state.json`;
  `save()` diffs `state.SHARED_KEYS` against what was shown and sends each
  change as `state.set` (map keys per entry, the rest whole). Writes are
  **optimistic**: the mirror holds the new value at once, the key or entry
  is *pending* until the reply, and a `state.set` event for a pending
  mark only updates the confirmed copy (the optimistic value holds). On
  the reply the service's last word wins; on a refusal the mirror reverts,
  `connect_changed` listeners hear it with `reverted`, and the window's
  toast says "Not saved: <reason>". `get_setting` reads this device's
  `UiState` first for a device key. `session_drafts` is debounced 500 ms
  per draft; `flush_drafts` sends what waits (the window flushes on every
  stash, its own close, blur; the app on shutdown).
- `RemoteStore` has the store's four signals (`refreshed`,
  `unread-changed`, `busy-changed`, `archived`), a `Gio.ListStore` of
  `SessionItem`s and the store's lookups and mutators. The snapshot sends
  an `item` per row and one `rows`; each service refresh sends the items
  that moved (changed fields only) then `rows`, which is the client's
  `refreshed`. **Busy, unread and status are the service's**: since
  PR-1.12a the tracker runs there (`service/tracking.py`, D29) and sets
  `busy` and a counted finish's `unread` on the items itself; the client
  sends `store.flags` only for what the person did at its screen —
  `status`, `unread: false` (and a notification's flag by focus, the
  placeholder handoff) — and `busy`, `backgrounding` and `can_background`
  from a client are refused (the last two are the service's background
  agents' since PR-1.12d, below). The property
  moves when the `item` comes back (a moment later, over the socket). The
  sandbox host and the live grants are the core's too
  (`ServiceCore.start_sandbox_host`); the box a resumed or forked session
  runs in is minted on the service at its spawn, and settled against the
  id when the session resolves. **Archived sessions
  are paged**: not in the snapshot; `set_show_archived(True)`,
  `archived_sessions()`, `archived_breakdown()` and the `sessions`
  attribute (every session, as before) page them in once
  (`store.page-archived`); `get_session` of an unseen id asks
  (`store.lookup`). Per-refresh readers use `known_sessions`,
  `summary()` (the footer's counts), `has_archived()` and
  `session_count()`, which never page.

**How a mutation flows.** `remotestore.RemoteStore.rename(sid, name)` →
`RemoteState.request({"t": "store.rename", ...}, mutate=lambda:
state.set_name(sid, name), applied=re-project the row)` → the mirror
changes, the mark is pending, the row's name shows → the service's
`_req_store_rename` runs `SessionStore.rename` → its save publishes
`state.set names/<sid>`, its refresh an `item` and `rows` → the reply
settles the mark. **To add one**: a `store.<verb>` type in
`api/protocol.py` (bounded fields, a sample and reply in
`tests/test_protocol.py`, the type list), a `_req_store_<verb>` in
`service/core.py` calling the store's method, and the mirror method in
`remotestore.py` — optimistic through `state.request(..., mutate=...)`
where the client can compute the state change, a plain
`state.request(msg)` or `link.call` (when the caller needs the reply)
where only the service can. A new **state key** goes in
`state.SHARED_KEYS` (attribute, form, cleaner, writable) and travels by
itself. Folder trust is `store.folder_trust` / `trust_folder`
(`trust.check` / `trust.grant`), the CLI's config being the service
machine's. `store.pr_store` is the mirror of the service store's
`PrStore` (`remoteprs.RemotePrStore`, PR-1.11), and the sandbox host keeps
the service's own `AppState`. `notifications`, `diff_notes` and
`pending_diffs` are written by the service alone (not `writable`): the
history through its notification center, the marks through
`service/diffs.py`, the pending show_diffs by the tools.

**Long-running operations are jobs** (PR-1.11): a `job.start` request
(kind and arguments, answered with an id) and `job` events (`running`
with progress or a partial result, then `done`, `failed`, `refused` or
`cancelled`), run by `service/jobs.py`'s `JobRunner` on daemon threads and
read through `jobclient.start(kind, args, on_event)`. Worktree recovery
(`worktree.trash`, `worktree.restore`: `sessions.trash_worktree` /
`restore_worktree` on the service's machine), the chat folder made and
trusted before its session starts (`chats.trust`: `chats.create_chat_dir`
then `trust_chat_dir`), the clone and the repository list it picks from
(`clone`, `clone.repos`) all go this way; the dialogs keep their
confirmations and read the events.

## AppState (`state.py`)

Sandboxing mirrors the worktree pair exactly: `sandbox_new_sessions` +
`project_sandbox` overrides (`sandbox_for_project`, the project menu's
*New sessions are sandboxed* check, shown only when `sandboxplan.
probe_reason() == ""`), plus the sticky `sandboxed_sessions` map a resume
reads — session id → the id of its box, its own `$HOME` (`is_sandboxed`
and `sandbox_box` follow the forward chain; `forward_session` carries the
entry; saved as an object, and a list from an older build loads as ids
with no box) — `sandbox_grants` per **session**, keyed by box id (an
entry keyed by anything else is dropped on load; written on the main loop
only), and `sandbox_project_grants`, the defaults a *new* session of a
project starts with (keyed by `sandboxplan.project_key`: a worktree's
repository; copied into a box's list once, when the box is minted).
`_forget_transcript` clears a trashed session's box and has it forgotten
— its grants with it; the flag stays. See `collins-sandboxed-sessions`.

`~/.config/collins/state.json`, written synchronously and atomically on every
mutation; `DEFAULT_SETTINGS` is the settings catalogue, each key with a
comment saying what it does and where it is read. `_load` migrates old keys
(`hidden`→`archived`, `auto_title_sessions`→`title_model`, the old
`panel_states` shape). Save writes every default back, so a new key exists in
every install after its first save. Beyond settings it holds names,
generated names, CLI titles, emoji, favorites, archived sessions and
projects, project order and expansion, per-project worktree overrides,
virtual projects, forward chains and pending detaches, process baselines,
`session_prs`, `session_attachments`, composer drafts, new-chat drafts and
notifications, and `service_id`. Persisted state is untrusted input: every
reader validates shape and drops what doesn't fit.

**The state split** (the service-and-client spec, §3.8; PR-1.4). `state.json`
is the *service's* half. This device's half is `ui-state.json` beside it,
written by `uistate.UiState`, which `AppState` owns: the settings in
`state.DEVICE_SETTINGS` (appearance, geometry, keybindings, sounds, tray,
Caffeine, the composer's, editor's and git page's looks; `SERVICE_SETTINGS`
is the rest, and the two must cover `DEFAULT_SETTINGS` exactly —
`tests/test_state_split.py`), and under `services.<service id>` the
per-session `panel_layout` and `editor_states` (the `AppState.panel_layouts`
/ `editor_states` properties read that block), `last_active_session` (a
setting in the catalogue, stored per service: `uistate.SERVICE_SCOPED_
SETTINGS`) and `open_tabs`, the tabs this device had open on that
service (`AppState.get_open_tabs` / `set_open_tabs`; written and read by
the window, see `collins-terminal-tab`). **Nothing outside `state.py`
knows the side**: `get_setting`, `set_setting`, `update_settings` and the
`get_*` / `set_*` pairs route, `AppState.settings` stays the merged dict
the window hands to `SessionOptions`, and `save()` writes `state.json`
only — a mutator of a device record calls `_save_ui()`, and
`forward_session` calls both. The first start after the split (a parsed
`state.json` with no `service_id`) copies it to `state.json.pre-split`,
moves the device keys over, mints the id and rewrites — **`ui-state.json`
first, then `state.json`**, so a crash between the two leaves a file that
migrates again; the id is the marker, so the second start does nothing;
an unparseable file is left untouched (no backup, no rewrite); an
unwritable config dir is logged, nothing is written, the merged view
stands in memory and the next start retries. **Only the app's own
instance migrates** (`AppState(migrate=True)`, in `main()` and
`App.__init__`); the throwaway readers on worker threads (titles,
tokenrefresh, updatecheck, icongen, the dialogs) read the merged view of
an unsplit file and write nothing — and if one of them does save, the
migration is committed first, so a service-side write never strips
device keys that have not reached `ui-state.json`. **A downgrade** (an
old build run on the split file) saves `state.json` back without the id,
on default device settings and empty layouts; the next start of this
build re-migrates by *merging*: it adopts the one service `ui-state.json`
knows, takes a record from the file only where the file's entry is
non-empty, takes a device setting only where it differs from its default
or `ui-state.json` never held the key (`UiState.present_keys`), and never
overwrites `state.json.pre-split` (a `.<YYYYMMDD-HHMMSS>` sibling is
written instead). Mind that `./start-debug` sets only the app id and
shares the real config dir: a debug launch of a split build migrates the
user's real `state.json`, and the installed pre-split build then runs on
default device settings until the merge above brings them back. A corrupt
`ui-state.json` is copied to `ui-state.json.corrupt` before the first save
overwrites it. An unknown key in `ui-state.json` stays on that side
(`_ui_only_keys`), and a service key found there is ignored. The e2e
scripts seed `state.json` without an id and are migrated on launch,
which is fine: a seeded device key reaches `get_setting` the same way.
The `app_state` fixture isolates both files (`_ui_state_file()` follows
`_CONFIG_DIR`); a test of the migration constructs `AppState(migrate=True)`.

## The sidebar (`sidebar.py`)

`SessionSidebar` is a `Gtk.ListBox` of hand-rolled two-level rows:
`GroupHeaderRow` (a project; click on the title starts a session there, the
fold zone left of the title folds — hit-tested with a claiming
`GestureClick`), `SessionRow`, `PlaceholderRow` ("New Thread" / a Draft, for
a tab whose session has no transcript yet), `NewThreadRow` (an empty group's
offer). Rows bind to `SessionItem`; the list rebuilds only on
`order_changed`. Rows arrive with a `Gtk.Revealer` slide (placeholders, Undo
restores, New Thread offers) and leave with a CSS `transform: translateX`
ghost (`.archiving`).

**Guide lines** are the 2px left border, ranked by CSS source order: idle
rows draw none; `.running` (a tab is open) fills the row; `.detached` is
yellow (running under `/bg`, no tab); `.interrupted` is red (the last
transcript event was the user stopping Claude — it stands until the session
moves on); `.running.busy` is the moving blue barber pole; `.unread` pulses
green. An animated property outranks later plain rules, so the unread
animation's selector excludes every status that outranks it.

**Running rows** (PR-1.12c, spec §3.21, D31). `SessionItem.running` is
the service's word that an agent pty runs the session: an `item` field
(`StoreFeed.item_fields` asks `ServiceCore.running_sessions`, and
`refresh_running` sends it when a pty spawns, resolves or exits) and the
rows of the pty table the subscription carries (`pty` events with
`table: true` — the snapshot's, an agent's spawn and resolve — and
`pty-exited` with `table: true`; `api.client` routes a table event to the
mirrors only, never to a view). `RemoteStore` keeps the table
(`agent_ptys`, `pty_for(session)` through the forward chain,
`unresolved_ptys`, `pty_running`) and emits `running-changed`. A fork's
pty names the forked id once a sandboxed fork's resolver found it, else
nothing (`ServiceCore.agent_pty_sessions`), and a fork's spawn is never
refused for its origin's pty, nor the origin's for a fork's. Only a pty
whose CLI runs is in the table: the facts' `running_command`
(`SessionRecord._send_changed` → `ServiceCore.cli_changed`) adds the row
when the CLI comes up and sends a table `pty-exited` (status null, the
pty alive) when it leaves, so a shell alone is no running row. A table
exit also clears the session's `running` field in the mirror. A `SessionRow` whose session
runs with no tab here takes the `detached` class (yellow), and
`row.session-child.detached.busy` poles in yellow (the service's busy
verdict reaches such a row too); an unresolved pty with no tab on this
device is a running `PlaceholderRow` "New session" keyed `pty:<id>`
(`SessionSidebar._unshown_ptys`, `pty_shown` from the window), opened by
attaching. The row menu's *Detach* is offered while the session has a
tab.

**Which sessions are working** is `activity.py`, GTK-free: `ActivityTracker`
is marked by (in order of trust) the CLI's own OSC 9;4 progress termprop
(`ProgressWatch`, coaxed out of the CLI by the env spoof in
`service.session.agent_environment` — `ConEmuANSI=ON`, `TERM_PROGRAM=kitty`
— and read off the stream filter's `Progress` events on the service),
first-column screen motion (`SpinnerWatch`), the pty's output after the
filter, coalesced to the screen's 50 ms settle and filtered by `EchoGate`
(drops what the app itself caused), a `/proc` poll for live descendants
below the agent (`proctree.has_live_descendant`, minus the persisted
plumbing baseline so MCP servers don't read as work), and for sessions
attached to a background agent the `claude agents --json` busy status
(`bgstatus.BackgroundBusyWatch`, since a `/bg` agent's env is scrubbed and
speaks no progress). All of it runs on the service since PR-1.12a
(`service/tracking.py` `ServiceActivity`, `tests/test_tracking.py`).
Ungated sources are held on fresh spawns until the gate arms
(`ServiceActivity.startup_held`): on a "\r" in a client's input frame (an
Enter typed into the VTE, `ServiceActivity.on_input`; the pole starts
pre-emptively on a bare Return alone — Alt+Enter and a pasted newline arm
the gate but start no pole, as the window's Enter key was the one starter)
*or* on a "\r" the
session writes itself (`Session.write_text` pokes its own gate and tells its
host `input_sent`, which the service's `SessionRecord` hands to
`ServiceActivity.input_sent` for the baseline's last pristine snapshot; the
app's writes take the service's pty, never the VTE, so a new-chat send, a
composer send or `start_session` would otherwise never arm it — the
regression that left every such tab without a pole after PR-1.9, fixed in
PR 602 and carried into the service here). The busy→idle edge is
`ServiceActivity._on_finished`: a counted one flags unread, tells the
session's clients (who refresh PRs: `MainWindow.run_finished`), and is the
edge any "do this when the session is done" feature should ride — but it is
judged against the session's transcript first (`service/finish.py`
`FinishJudge`: `activity.FinishLedger` over `TranscriptModel.stamp`; the
CLI's idle repaints land redraw-inferred edges with the transcript
unchanged, and those are held, then dropped — see
`collins-notifications-and-tray`), so ride `_land_finish`'s verdict, never
the raw tracker edge. A progress
termprop clear (and a background agent's idle reading) does not land that
edge at once: the CLI (2.1.261) also clears the hint for a beat between tool
calls, so `finish(grace_s=PROGRESS_FINISH_GRACE_S)` arms the finish for 3 s
and the next busy hint `resume`s it; the pole stays up through the wait, and
the sweep leaves an armed session to its grace (a redraw mark on `IDLE_S`
inside it can't time it out early). The latest `mark` decides the deadline,
so while the hint reads busy (`ProgressWatch.busy`) the service's redraw marks
carry `PROGRESS_IDLE_S` too — on the default `IDLE_S` they cut the agent's
word down to 2 s of screen silence, and a main loop stalled that long landed
graceless finishes (unread flag, notification) mid-turn. Probing the CLI
(2.1.261) from a bare pty showed the hint held busy straight through tool
loops, hooks, the auto-mode classifier, thinking and 40 s tool runs, with
0/3 flapping only in the first seconds of a turn's stream; `COLLINS_LOG=DEBUG`
logs each hint reading and each finish the tracker lands or disarms.

**Background agents** run on the service since PR-1.12d (split-service
spec §3.22): `service/bgagents.py`'s `BackgroundAgents`, built by
`ServiceCore.start_background()`, owns `bgstatus.BackgroundStatusPoller`
(which polls `background_agents()` on a file monitor over `~/.claude/jobs/`
— used only as a wake-up, never parsed — plus explicit events; the
`background_status_poll` setting, read on the service and re-read after
every settings write, is a 20 s timed fallback), the /bg handoff (a
session's `close {mode: background}` runs `handoff`: the pending detach
written, the agents listed so far noted, the fork watch's 30 × 1 s thread,
every row standing for the session `backgrounding` until the match, the
watch's give-up or the 45 s safety timer; `record_forward` on a fork; the
CLI nudged 700 ms after a confirmation), the replay of pending detaches
at service start, *Repair session link* (the `session.repair` job: the
inputs read on the main loop in `_req_job_start`, the match on the job's
thread, the forward recorded back on the main loop before the outcome),
each row's `can_background` (`bgblock.background_blocker` over the
service's live sessions; one handoff app-wide), and the busy feed into the
tracker (`ServiceActivity.background_busy`) while a session is attached
to a background agent. Its outputs are item fields: `background`,
`backgrounding`, `can_background`. The window is a reader: the mirror's
`background-changed` runs `MainWindow._on_backgrounded`, which re-syncs
the row's status (`_row_status`: a row with no tab is `background` when its
item says so) and, when no row is `backgrounding` any more, advances the
quit-time /bg queue (`_on_detach_settled`); `_background_blocker` reads
the same gate over the tab and the mirror for its reasons (the header
button's tooltip, the close dialog's sentence). `window.py` imports
nothing of `bgstatus` (`tests/test_client_boundary.py`).
Current CLIs detach in place (same id keeps running); older ones forked to a
new id, which `AppState.forward_session` tracks so the old row is replaced and
names/favorites/panels carry over. Anything mapping session→row must go
through the forward chain (`_rows_by_session`), never the raw id. The daemon
lists a finished job indefinitely and refuses a plain `--resume` for any
listed id, so resume checks use `include_finished=True`; a job left listed
also means `_is_detached()` stays true for an attached tab — pair "detached"
checks with "has no tab". Stopping stray duplicate `/bg` jobs can delete a
worktree other jobs share.

**Archive** is the user's "done with this" gesture and holds most of their
data (100+ sessions is normal). `win.archive-session` is a **toggle** (the
row button); `win.archive-session-now` / `MainWindow.archive_session` always
archives. Either rides the normal tab-close flow when a tab is open, so an
archive can be declined — never assume it landed. A fully-archived project
keeps its header only while sessions still exist on disk. Bulk deletes
confirm with the blast radius (counts, projects that vanish) and use the
system trash. With `archive_on_claude_ai` on, user-driven archives mirror to
claude.ai on a background thread (`remotearchive.py`), best-effort. A single
archive also settles the session's worktree (the `archive_worktree` setting:
ask | always | never). With "ask", `MainWindow._set_archived` asks *before*
anything is archived (`_ask_worktree_then_archive`: the dialog has Cancel,
and Cancel must leave the tab and row untouched); a Trash answer is parked
in `_worktree_deletions` and acted on by `_settle_archived_worktree` once
the archive has landed on a stopped session — the move always comes last.
"always" probes and trashes at that same landing. `sessions.
removable_worktree` finds the worktree the transcript still records on
disk, `sessions.trash_worktree` (the service's `worktree.trash` job)
moves it to the system trash (`worktree
unlock`, then `Gio.File.trash`; git's registration and the branch stay,
so the entry lists as prunable until gc forgets it), never while the
session is detached or another tab / background agent works in it, and
never for bulk archives. The transcript read is the service's
(`store.worktree-check {session}` → `removable`, the worktree state or
null, and `shares`, whether the session runs on as a background agent or
one works in there), asked from a worker thread; the open tabs that may
work in it are the window's to count (`_worktree_in_use`). The archive's
Undo (`_undo_archive_now`) restores
the worktree with the session: `_trashed_worktrees` holds the records while
the undo is armed, `sessions.restore_worktree` reads the freedesktop trash
(home trash and the mount's `.Trash-<uid>`) back by `Path=` and re-registers
the worktree by hand if git pruned it meanwhile. GLib refuses to trash on a
"system internal" mount (tmpfs) unless it shares the home filesystem — an
error dialog then, the worktree stays; `scripts/check_archive_worktree.py`
stages under `~/.cache/collins-e2e` for that reason and drives all of it.

**Automatic delete** (`autodelete.py`, GTK-free): `AppState.set_archived`
stamps `archived_at` (first archive wins; a restore drops it; archives from
before the stamp are stamped at first read, never earlier). The
`auto_delete_archived_after` / `auto_delete_archived_unit` pair (0 = never,
the default; month = 30 d, year = 365 d) is read by `autodelete.maybe_sweep`,
which the service runs on its own timer since PR-1.12d
(`ServiceCore.start_housekeeping`: after the store's first scan, then
hourly; the e2e probe's door is `debug.sandbox` → `sweep_archived`); a
cache file (`archive-sweep.json`, the service machine's) holds it to one
sweep a day. The trash goes through `ServiceCore.trash_expired_archives` —
the manual bulk delete's path minus the dialog: running sessions (an agent
pty on the service, or a background agent) are skipped, an emptied project
is kept as a header, and each trashed session is forgotten on the service
(`forget_session`) with a `forgotten` event so every window drops its tab,
layout and Undo for it. A forgotten transcript's files and records
(panel history, PRs, images, draft, box) go through `store.forget` from
the window's `_forget_transcript` too. Sessions out of sight only because their
*project* is archived carry no stamp and are never swept.
`scripts/check_auto_delete.py` drives the row and the sweep.

**Trust.** `trust.py` walks `~/.claude.json`'s `hasTrustDialogAccepted`
entries up the ancestor chain (the CLI honours ancestors), and asks the
"Do you trust this folder?" dialog before a first launch. `claude -w` checks
the **exact** directory, so `trust.trust_launch_dir` writes it (and the repo
root) before a worktree launch.

**Chats** (`chats.py`) are ordinary sessions whose cwd is a throwaway dir under
`~/.local/share/collins/chats/`, shown as a pinned virtual project. On its
first scan the app **reaps chat dirs no discovered session points at** — which
is why every throwaway instance must set `COLLINS_CHATS_DIR`. A swept or
trashed chat folder is made again by the service before a spawn in it (an
agent's or a panel shell's, PR-1.12d) and by the `chats.trust` job given a
`cwd`; the client never creates one.

**Project icons** (`projecticons.py`): a `project-icon.svg` at a project's
root replaces the folder icon, gated by `usable_icon_bytes` (SVG only;
`data:image/png` hrefs allowed, nothing else). Generated icons pass the
stricter `usable_generated_icon_bytes`. Rasterized via `svgtexture.py`.

**Adding and cloning projects.** The sidebar header's folder button is a
`Gtk.MenuButton`: *Open folder…* (`win.add-project`, a `Gtk.FileDialog`)
and *Clone repository…* (`win.clone-project`, `clonedialog.CloneDialog`,
whose repository list and clone are the service's `clone.repos` and
`clone` jobs: gh and git run on its machine).
Both end in `MainWindow._with_folder_trust` → `_add_project` →
`store.add_project`, which records a virtual project (a no-op for a
project that already has sessions). The clone dialog's GTK-free half is
`clonerepo.py`: `parse_source` decides whether the box holds a filter word,
an `owner/repo` or a clone address (never anything starting with `-`,
never git's `<transport>::` remote-helper syntax); `fetch_repos` pages `gh
api user/repos?affiliation=owner,collaborator,organization_member`
(bounded to `MAX_PAGES`, through `prstatus.gh_json`); `destination` /
`destination_status` are the path the dialog prints and whether git will
take it; `clone_argv` is `gh repo clone` for GitHub (gh's protocol
preference and credentials) and `git clone --` otherwise. The clone runs
with `GIT_TERMINAL_PROMPT=0`, `stdin=DEVNULL`, in its own session (no
controlling tty, so ssh can't prompt either). Cancel or closing the
dialog `killpg`s it, and git removes its half-made folder. The list is
cached module-wide for the app's lifetime. The dialog's starting folder
is the `clone_directory` setting (`"~"` by default; Preferences stores a
picked folder as `~/…` under home). `scripts/check_clone_repo.py` drives
all of it with a `gh` shim.

## Footguns

- Two unresolved fresh tabs in one cwd race for the same new transcript;
  serialize spawn→inject→resolve per project root (the `start_session` tool
  does).
- A `claude -w` refusal ("Error creating worktree:") is fatal and silent to
  everything downstream; the tab watches the screen and retypes without `-w`.
- The CLI recycles an unchanged existing worktree for a new `-w` session, so
  a leftover transcript can sit under its key before the new session writes.
- Sidebar groups start **collapsed** unless `expanded_groups` lists them;
  a placeholder in a group persists its expansion so discovery doesn't snap it
  shut.
- `chats.py` once claimed trust was not inherited; it is. Ignore stale
  comments to the contrary.
- Screenshots of guide lines: append a `[Request interrupted by user` message
  as the last transcript line for a red line with no live agent; stage
  `session_prs` records for PR marks without `gh`.

Related: `collins-terminal-tab`, `collins-token-use-and-claude-api`,
`collins-pull-requests`, `collins-testing`.
