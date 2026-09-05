---
name: collins-sessions-and-sidebar
description: >-
  How Collins finds, models, persists and lists Claude Code sessions: transcript
  discovery and parsing (sessions.py, providers.py), the SessionStore hub and
  SessionItem view-models, AppState and state.json, the sidebar's rows, groups,
  guide lines and status/busy/unread flags, session titles, worktrees and
  worktree recovery, /bg forward chains and background-agent status, folder
  trust, the Chats virtual project and project icons. Use when touching
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
`<synthetic>` and `isSidechain` lines) and a top-level `effort`.

**Worktrees.** `worktree_project_root(cwd)` maps `<repo>/.claude/worktrees/x`
back to `<repo>` — every "which project is this" question goes through it,
or the sidebar grows phantom projects named after worktree directories and
"New session" cascades into a worktree. When a `-w` session exits clean with
nothing to keep, the CLI deletes the worktree **and its branch**; on resume
the CLI re-enters the worktree whenever the directory exists, so
`sessions.recreate_worktree` puts it back from the last live `worktree-state`
before resuming (`git worktree add -f -f`: the CLI's lock outlives the
session, and a single `-f` fails silently). The CLI also *moves* a transcript
to the worktree's project directory the moment a session enters one.

## The store (`store.py`) and the view-model (`models.py`)

`SessionStore` is the single source of truth between disk and UI: it owns the
threaded scan, `Gio.FileMonitor`s on each provider's projects dir (debounced),
grouping and ordering, and **every** state mutation (rename, favorite,
archive, trash, project order, forward chains). It reuses `SessionItem`s
across refreshes so property bindings survive; `refreshed(order_changed)` says
whether rows must be rebuilt. `SessionItem` properties the sidebar binds to:
`display_name`, `subtitle`, `preview`, `favorite`, `status` (`""` | `open` |
`attention` | `background`), `state` (`""` | `interrupted`), `busy`, `unread`,
`syncing`, `backgrounding`, `can_background`. `unread-changed`,
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
`prattach` reads each new session's first prompt for PR references the same
way. `store.is_virtual_project(name)` (flagged **and** no sessions) is the
question "does this header stand for a folder with nothing in it";
`state.is_virtual_project` alone is stale data.

## AppState (`state.py`)

`~/.config/collins/state.json`, written synchronously and atomically on every
mutation; `DEFAULT_SETTINGS` is the settings catalogue, each key with a
comment saying what it does and where it is read. `_load` migrates old keys
(`hidden`→`archived`, `auto_title_sessions`→`title_model`, the old
`panel_states` shape). Save writes every default back, so a new key exists in
every install after its first save. Beyond settings it holds names,
generated names, CLI titles, emoji, favorites, archived sessions and
projects, project order and expansion, per-project worktree overrides,
virtual projects, forward chains and pending detaches, process baselines,
panel layouts, editor states, `session_prs`, `session_attachments`, composer
drafts, new-chat drafts and notifications. Persisted state is untrusted
input: every reader validates shape and drops what doesn't fit.

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

**Which sessions are working** is `activity.py`, GTK-free: `ActivityTracker`
is marked by (in order of trust) the CLI's own OSC 9;4 progress termprop
(`ProgressWatch`, coaxed out of the CLI by the env spoof in
`terminal._agent_tab_environment` — `ConEmuANSI=ON`, `TERM_PROGRAM=kitty`),
first-column screen motion (`SpinnerWatch`), `contents-changed` redraws
filtered by `EchoGate` (drops redraws the app itself caused), a `/proc` poll
for live descendants below the agent (`proctree.has_live_descendant`, minus
the persisted plumbing baseline so MCP servers don't read as work), and for
tabs attached to a background agent the `claude agents --json` busy status
(`bgstatus.BackgroundBusyWatch`, since a `/bg` agent's env is scrubbed and
speaks no progress). Ungated sources are held on fresh spawns until the gate
arms (`MainWindow._startup_held`). The busy→idle edge is
`MainWindow._on_session_finished`: it flags unread, refreshes PRs, and is the
edge any "do this when the session is done" feature should ride.

**Background agents.** `bgstatus.py` polls `background_agents()` on a file
monitor over `~/.claude/jobs/` (used only as a wake-up, never parsed) plus
app events; the `background_status_poll` setting is a 20 s timed fallback.
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
claude.ai on a background thread (`remotearchive.py`), best-effort.

**Trust.** `trust.py` walks `~/.claude.json`'s `hasTrustDialogAccepted`
entries up the ancestor chain (the CLI honours ancestors), and asks the
"Do you trust this folder?" dialog before a first launch. `claude -w` checks
the **exact** directory, so `trust.trust_launch_dir` writes it (and the repo
root) before a worktree launch.

**Chats** (`chats.py`) are ordinary sessions whose cwd is a throwaway dir under
`~/.local/share/collins/chats/`, shown as a pinned virtual project. On its
first scan the app **reaps chat dirs no discovered session points at** — which
is why every throwaway instance must set `COLLINS_CHATS_DIR`.

**Project icons** (`projecticons.py`): a `project-icon.svg` at a project's
root replaces the folder icon, gated by `usable_icon_bytes` (SVG only;
`data:image/png` hrefs allowed, nothing else). Generated icons pass the
stricter `usable_generated_icon_bytes`. Rasterized via `svgtexture.py`.

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
