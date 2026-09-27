---
name: collins-sandboxed-sessions
description: >-
  How Collins runs a Claude Code session inside a bubblewrap filesystem
  sandbox: the GTK-free mount plan (sandboxplan.py, a port of aibox's
  plan.rs with Collins' additions), the stdlib-only host launcher the typed
  command starts with (sandboxrun.py), SessionOptions.sandbox and the
  provider wrapper, the box every sandboxed session has for itself (its own
  sandbox home, a carrier and anchors, a lease; made, scrubbed, swept and
  removed by file descriptor), grants that are one session's (keyed by its
  box) and a project's defaults that seed new sessions, live grants
  (sandboxgrants.py: a directory
  allowed while a session runs, mounted into the running box with bindfs
  and fusermount3 on one worker thread), the sticky session-to-box map and
  per-project override in state.json, the new-chat Sandboxed checkbox,
  trust mirroring into the sandbox home, the /bg and attach refusals, the
  Preferences group and the bwrap probe, and what the box does and does not
  contain. Use when changing anything about sandboxed launches, the plan's
  tables or ordering, the boxes on disk, grants and how they reach a
  running session, the two shares, or debugging a session that came up
  unsandboxed, a shim that can't reach Collins from inside, a grant stuck
  on "after restart", a mount left behind, a box that wasn't removed, or a
  settings.json write that failed with EBUSY.
---

# Sandboxed sessions

A session can run inside a bubblewrap box (spec:
`~/specs/collins/sandboxed-sessions.md`, a port of EricKuck's `aibox`, MIT):
the workspace read-write, `~/.claude` and the toolchain caches shared, the
system read-only, credentials and every other checkout absent. It is a
**filesystem** sandbox with a PID namespace; IP networking is not isolated
(the CLI needs the API) and unix sockets are absent only because the
mounts carrying them are. The point is a yolo session — permission prompts
off by default inside — that cannot reach `~/.ssh`, `~/.config/gh`, the
keyring, or the other checkouts.

## The pieces

**`sandboxplan.py` (GTK-free, unit-tested).** `build_plan(Inputs)` is pure
path arithmetic over aibox's tables (`RO_HOME`, `RW_HOME`,
`RW_HOME_ALWAYS`, `SENSITIVE_HOME`, `RO_ABSOLUTE`, `RW_ABSOLUTE`) plus the
Collins additions: the resolved `claude` binary's directory
(`resolved_claude`; the native installer's whole `~/.local/share/claude`),
`sys.prefix` when not under `/usr`, the shim's package parent
(`mcptools.package_parent()`), the `mcp.json` *file* read-only (never its
directory: for a generated app id that is the runtime dir, where the plan
files live — every e2e instance runs on such an id), the socket *file*
read-write (connect needs write on the inode; only the file, never its
directory), the workspace and the enclosing repository's `.git` and
`.claude` (a linked worktree's common git dir too), grants, the two shares
(the SSH share binds the `SSH_AUTH_SOCK` *file*, never its directory — the
keyring's control socket sits beside it), then the masks, then the hook
surface pins: `settings.json` / `settings.local.json`, `~/.claude/plugins`
and the `claude` launcher on PATH when it is a real file in a shared tree
(the native installer's is a symlink in `~/.local/bin`, which no bind can
pin — noted in the plan and stated in the docs). A *symlinked*
`settings.json` refuses the plan (`PlanRefused`: bwrap can't create the
destination through it, and the link stays replaceable) unless the switch
makes it editable. **Ordering rule**: the overlay home first, then the
system tables, the runtime dir's tmpfs and `TMPDIR`, then the box's
**anchors** and its **carrier** (below),
Collins' own read-only binds *before* the workspace (a workspace that
overlaps them — a Collins checkout under `./start-debug` — must land on
top and stay writable), masks last, then the protect-check, then
`--remount-ro` for the carrier and every emitted anchor, then `--proc`.
`carried()` decides which masks to
emit: bwrap would *create* a destination it was told to cover, so only
secrets that exist and that a shared mount actually reaches are masked.
The workspace is held to the grant rule (`guard_path`: never a secret,
inside one, an ancestor of one, `$HOME` or above it, and never anything
that is, holds or lies inside the **sandbox root** — "reaches the sandbox
homes": no box is built on, or granted, another box's home — checked as
written
*and* resolved, against the home as written and resolved, and against the
target of any secret that is itself a symlink, so a `~/.ssh` that points
into a dotfiles checkout refuses the checkout too); grants go through the
same guard and are kept as written, not `realpath`'d. The protect-check
refuses a plan that would carry `~/.config/collins`,
`~/.local/state/collins`, `~/.cache/collins`, the plan directory or the
sandbox root
(`PlanRefused`). Every path is validated (`valid_path`: absolute, bounded,
no control characters, no `.`/`..`). The output is a JSON document
(`version` 2, `bwrap_args`, `unsetenv`, `setenv`, `gh_token`, `workspace`,
`inputs` — the policy slice plus `box`, `sandbox_home`, `carrier` and the
emitted `anchors` — and
`notes`), written by `prepare_launch` to
`$XDG_RUNTIME_DIR/collins/<app id>/sandbox/<uuid>.json` mode 0600 and
regenerated at every launch — a cache of state, never state. With no
`XDG_RUNTIME_DIR` (a Collins started outside a desktop login session, and
CI) `mcptools.runtime_dir` falls back to the temp directory, which every
box shares read-write, so `plan_dir` falls back to
`$XDG_STATE_HOME/collins/sandbox/<app id>` instead — a plan the box could
reach is exactly what the protect-check refuses, and it would refuse every
launch.

**The box.** Every sandboxed session has a directory of its own under the
sandbox root (`sandbox_root()`: `COLLINS_SANDBOX_ROOT`, else
`$XDG_DATA_HOME/collins/sandbox`, one root whatever the app id), named by
a **box id** — `uuid4().hex`, 32 lowercase hex characters, and
`valid_box_id` accepts nothing else, so `box_dir` / `box_home` /
`box_carrier` / `box_anchor` raise on anything that could be a path:

```
<root>/owner            which state file's boxes these are (the sweep)
<root>/<box id>/home/   $HOME inside the box            (agent-written)
               grants/  the carrier, bound at /run/collins/grants
               anchors/<top>/   one per anchored top-level directory
               lease    {"pid", "app_id"} while an instance holds the box
```

The id is minted by the tab at the first launch — before the CLI has
minted a session id — and recorded against the session id when the
resolver binds the tab. The **carrier** and the **anchors** are empty
host directories bound into the box and remounted read-only there
(`Plan.remount_ro`), which is what lets a mount the *host* makes in one
propagate into the running box while the box itself can write none of
them: where a live grant is mounted (below). `anchor_roots()` is every
real directory in `/` not in
`NEVER_ANCHORED` (`/mnt`, `/media`, `/srv`, a machine's own `/data`);
`_kept_anchors` drops one whose destination the plan binds itself (the
workspace, a grant, the repository's `.git` / `.claude`, one of Collins'
own pieces — the remount acts on the topmost mount at a path and must
never be the workspace) or that would cover something bound before it (a
home outside `/home`, the runtime dir, `TMPDIR`).

`prepare_launch(workspace, app_id, state, box)` is the host side of a
launch: it validates the box id, creates the `RW_HOME_ALWAYS` directories
(a bind needs a source) and the box's own (`make_box`, each 0700, the
lease written the moment the box's directory exists — and the host
counts the hold *before* that, so the startup sweep's thread never finds
a box that is still being built unheld), seeds
the
sandbox home once with `~/.claude.json` (`seed_home`; mode 0700, the copy
diverges from then on), mirrors folder trust (`mirror_trust`:
`hasTrustDialogAccepted` for the workspace *and its trusted ancestors*, and
nothing else — the one write Collins makes to a file the CLI would not
have written itself, into a file Collins owns), builds the plan, **scrubs
the home** (`scrub_home`) and writes the plan.
Returns None when no box can be built; the tab then launches unsandboxed
and says so, dropping a bypass mode the box justified, and discards what
the attempt left. **Every path under
a sandbox home is attacker-controlled** (it is `$HOME` inside):
the two writes there go through `_replace_private` —
`lstat` the destination and refuse anything but a regular file or
nothing, write a fresh `O_EXCL | O_NOFOLLOW` temp under a random name,
rename — so a planted `.claude.json` or `.claude.json.tmp` symlink never
becomes a host write. `sweep_plans(app_id)` at startup clears plan files
a killed tab never released (bwrap reads its plan once at exec).

`scrub_home(home_dir, plan, home)` removes what stands in a mount's way
in this box's own home: for every destination of the plan strictly under
`$HOME` (a bind whose source exists, a tmpfs) that no earlier mount
already covers, it walks the path one component at a time, each opened
relative to the last with `O_NOFOLLOW`. A symlink is unlinked, a stray
file where a directory goes is unlinked, an empty directory where a file
goes is removed; bwrap's own stubs and a non-empty directory where a
file goes are left. It never follows a link, never leaves the home, and
touches nothing off a mount's path. The notes go into the plan.

`remove_box(box)` is **the one place the feature deletes a tree**, and
the tree was written by the agent: only `<root>/<32 hex>` (a bad id
raises); refused while `/proc/self/mountinfo` shows a mount point at or
under the box (as written and resolved) — never delete through a mount;
walked by file descriptor (`os.scandir(fd)`, `stat`/`unlink`/`rmdir`
with `dir_fd`), a symlink unlinked and never followed; an entry on
another device stops the whole removal before anything is touched (one
walk to look, one to remove, both checking). These three are not to be
loosened to make something work.

**`sandboxrun.py` (stdlib only, nothing from `collins`).** The typed line
is `python3 <…>/collins/sandboxrun.py <plan.json> -- claude …` — the
launcher by file (`providers.sandboxrun_path`), never `-m
collins.sandboxrun`: the tab's shell has no PYTHONPATH for a checkout and
an installed collins would shadow it. It reads and
validates the plan, execs `bwrap --args <fd> -- claude …` with the
arguments NUL-separated over a pipe (a memfd past 60 KB), applies the
scrub with `--unsetenv` (`SSH_AUTH_SOCK`, `SSH_AGENT_PID`,
`GPG_AGENT_INFO`, a unix `DOCKER_HOST`) and `--setenv HOME`, and — when
the plan says `gh_token` — reads `gh auth token` on the host and hands it
in as `GH_TOKEN` the same way (never on disk, never on the command line).
A plan it can't read or a missing bwrap is exit 2 with the reason: it never
runs the command unsandboxed. `COLLINS_BWRAP` names a fake for tests.

**The host object.** `sandboxplan.SandboxHost(app_id, state, state_file)`
is the host
side of everything a running instance asks: `prepare_launch(cwd, box)`,
the boxes (`hold` / `release` — counted in memory and written as the
box's lease; `discard_box`, which removes a box only when no session of
`state.sandbox_boxes()` names it, no *live* lease of another process
holds it — live while `/proc/<pid>` exists — and this process doesn't;
`discard_box_async` on a daemon thread; `sweep_boxes` at startup), the
session's grants (`grants(box)` / `allow(box, workspace, path)` /
`revoke(box, path)` — every one is handed the box it is about; `allow`
runs
`grant_reason`: the `guard_path` rule first, so a secret is refused whether
or not it exists, then "already inside the workspace" and "not a
directory"), the project's defaults (below), `plan_stale(plan_path,
workspace)` (the box is read off the launched plan; the launched plan's
`inputs` against what `build_plan(gather_inputs(...))` would say now, on
the `POLICY_INPUTS` keys — grants, the two shares, settings protection),
and `derive(plan_path, cwd)` (a sibling's plan file and box, see the
policy below). `load_plan` reads a launched plan back through `sandboxrun.
read_plan` plus an `inputs` check (version 2, a box id, the box's paths);
`plan_reaches(plan, path)` is the
"inside the workspace or a grant" rule; `derive_plan(plan, cwd, box)`
re-issues a plan with `--chdir` changed (`cwd` recorded beside
`workspace`) and every path under the parent's box moved to the
sibling's.
The app sets `terminal.SANDBOX_HOST` to one at startup.

**Grants are a session's.** A sandboxed session's box is that session's
alone: its home, *and the directories it is allowed*. `state.
sandbox_grants` is box id → list — the box is the one identity a session
has before the CLI has minted its id — and an entry keyed by anything
else is dropped on load. A directory allowed in one session's chip
reaches that session and no other, running or future. **Nothing
delivers, lists or removes a grant for any box but the one it was asked
about, and there is no lookup from a workspace, a project or a
repository to a set of boxes anywhere in the feature.**
`state.sandbox_grants` is written on the main loop only.

**A project's defaults** are what a *new* session of it starts allowed:
`state.sandbox_project_grants`, keyed by `project_key(workspace)` (the
repository a Claude-managed worktree belongs to, else the real
workspace). `host.project_grants` / `is_project_default` /
`set_project_default(workspace, path, on)` — the guard a grant is held
to, without "already inside the workspace" and "not a directory", which
are about one session on one day. They are **a template, never a live
link**: `mint_box(workspace, seed=True)` — the one place a session's box
id is made; `new_box_id()` is not called outside `sandboxplan.py` —
copies them into the new box's list once (`seed_box`: each kept only
when `grant_reason` passes for this session; one that doesn't is
skipped with a log line and stays a default). From then on the two lists
have nothing to do with each other: marking or removing a default
changes no session that exists.

| The session is | Its box | Its grants at launch |
| --- | --- | --- |
| new (Send, the header button, a project row) | `mint_box(cwd)` by its tab | the project's defaults |
| resumed | the one the state maps it to | that box's |
| resumed with no box (`""`) | `mint_box(cwd)` by `open_session` | the project's defaults |
| a fork | `mint_box(cwd, seed=False)` by `open_session` | a copy of its origin's, taken once |
| a `--continue` tab | `mint_box(cwd, seed=False)` by its tab | none until it resolves |
| a sibling of a sandboxed parent | `mint_box(cwd, seed=False)` by `derive` | its parent's static grants as launched |
| a sibling of an unsandboxed parent | `mint_box(cwd)` by its tab | the project's defaults |
| the same conversation under a forwarded id | the same box | the same |

`host.settle_box(session_id, box, workspace, owed)` is what
`_on_session_resolved` calls (through `MainWindow._settle_sandbox_box`):
for a `--continue` tab (`tab.take_sandbox_defaults_owed()`) whose session
already had a box, the tab's box takes over that box's grants and the
old box is forgotten; one whose session had none is seeded with the
defaults then. It answers whether `GrantMounts.sync(box)` is owed.

**Forgetting.** Grants leave the state with their box.
`host.forget_box(box)` (main loop) drops the grants of a box no session
names and then asks `discard_box_async`; **every caller outside
`sandboxplan.py` uses it, never `discard_box_async`** — the tab at its
shell's exit (landed on the main loop first), a launch that couldn't be
prepared, the `--continue` takeover, `_forget_transcript`, every
`start_session` refusal that drops a sibling's box.
`host.prune_grants()` at startup drops the entry of every box no session
names and whose directory is gone.

**The sweep belongs to one state file.** `sweep_boxes` removes every box
the state doesn't name, so it must be the state that names them — and
every e2e check and capture runs on a scratch `state.json` beside the
user's real sandbox root. `<root>/owner` names the owning state file
(`owns_root`): no owner, this instance's, or one whose file is gone →
this instance claims the root and sweeps; anyone else's → it sweeps
nothing. `discard_box` has no such check: it is only called for a box
this instance's own state named or its own tab minted.

**The tab.** `SessionOptions.sandbox` is the decision, `sandbox_plan` the
file, `sandbox_box` the box. `TerminalTab._launch_command` (from
`_finish_spawn`, and again from a
restart) writes the plan at the last moment through
`terminal.SANDBOX_HOST.prepare_launch` because the workspace is the
*settled* cwd — a recreated worktree included — in the box the options
name (a resumed session's), else the one the tab already launched in (a
restart keeps the home), else a fresh one — unless the options already
carry a plan the tab hasn't seen (a sibling's derived plan, with its
box), which
`_sandbox_options` *adopts* instead; and `Provider.sandbox_prefix`
prepends the wrapper in `new_command` / `resume_command` and, for the
`--continue` override, in the tab itself — which also appends
`Provider.session_flags` (the permission mode) there, after the options
settled, so a box that couldn't be built never leaves a bypass flag typed.
`--die-with-parent` ties the box to the tab's shell, so every close flow
holds; `_release_sandbox_plan` unlinks the plan and releases the lease
(before a restart's rebuild, and in `_on_child_exited`, which then asks
`forget_box` — a no-op for a box a session names, and what removes
the box of a launch that never produced a transcript, its grants with
it). `tab.sandboxed`
is the
launch record (as launched, not as the settings now say). A sandboxed
*fork* tab keeps its origin's id but runs the resolver in `_fork_resolve`
mode: the forked conversation's id lands on `fork-resolved` and the window
records it with the fork tab's own box, so the fork's row resumes boxed
too, in its own home.

**Also.** A restart keeps the tab's box. A sandboxed panel shell runs in
the session's, since it runs the session's plan file. A trashed or
deleted transcript (`_forget_transcript`): the entry's box is cleared and
the box forgotten — unlinked, not trashed, its grants gone with it —
while the sticky flag stays, so a transcript restored from the trash
resumes boxed in a fresh home, as a new session would.

**State.** `sandbox_new_sessions` + `project_sandbox` overrides
(`sandbox_for_project`, the sidebar project menu's *New sessions are
sandboxed* check), `sandbox_bypass_permissions`, `sandbox_share_gh`,
`sandbox_share_ssh`, `sandbox_settings_editable`; the sticky
`sandboxed_sessions` map, session id → box id or `""` (written when a
sandboxed launch resolves its id,
or a sandboxed fork reports its new one, carried by `forward_session`,
read by `open_session` for a resume; `set_sandboxed(id, True, box=None)`
keeps the box, a string replaces it; saved as an object with sorted keys,
and loaded from an object or from the list an older build wrote — shape
validation, not a migration); `sandbox_grants` per session, by box id,
and `sandbox_project_grants`, the defaults (both edited from the footer
chip, see below). The draft record's
`sandbox` slot mirrors the worktree checkbox, except that a restored
choice sticks while the box is hidden (a draft reopened before the probe's
verdict, or on a machine with no box — the late verdict keeps it rather
than the project's default), and
`newchat.effective_sandbox` is the rule the window applies
(`_sandbox_for_new_session(cwd, choice)`) for the checkbox's start state,
the Send, and a sibling's default alike.

**Trust and permission mode.** A sandboxed launch defaults the mode to
`bypassPermissions` (`MainWindow._sandboxed_options`) — a resume and a
`--continue` of a sandboxed session too (`Provider.session_flags` types
`--permission-mode` after `--resume`; the CLI restores no mode by itself).
`start_session` grants bypass to a sibling only when the sibling is
sandboxed (`mcptools.inherited_permission_mode(..., sandboxed=True)`). The
trust dialog is mirrored, the bypass-acceptance dialog is not persisted by
the CLI anywhere, so an interactive sandboxed launch still shows it.

**The footer chip (`sandboxchip.SandboxChip`).** A `Gtk.MenuButton` with
a TOP popover, built like the model chip and placed first in the footer's
left run (`_model_sep` follows it), shown by `_sync_sandbox_chip` exactly
when `tab.sandboxed and tab.sandbox_plan_path` — never on an unsandboxed
tab. Filled on every `show` from the *launched* plan (`load_plan`, never
re-derived): the workspace, the two shares' state, settings protection;
then two lists. **Allowed directories**, captioned *For this session
only*: the host's grants for this session's box (`box()`), one row each
by `GrantMounts.status`, each but a `LEAVING` one with a pin before its
remove button (a flat `Gtk.ToggleButton`, `view-pin-symbolic`, active
when `host.is_project_default`; `chip.set_project_default` toggles it,
and a refusal is a toast and the pin goes back):

| The grant is | Row | Remove button |
| --- | --- | --- |
| `STATIC` | the path | yes |
| `LIVE` | the path, *live* (tooltip: mounted, a restart makes it a plain bind; and where it answers when it isn't linked) | yes |
| `PENDING` | the path, *after restart* (tooltip: the reason) | yes |
| `LEAVING` | the path, dimmed, *until restart* | no |

— the state's grants, then the `LEAVING` ones from the launched plan.
With no `GrantMounts`, one that isn't `capable()`, or a box it doesn't
know, the rows are drawn from the launched plan alone (nothing, or *after
restart*). Then **New sessions of this project**: the project's defaults
(`host.project_grants(workspace)`), each with a remove button, *None*
when there are none. Removing one there changes nothing in the list
above but that row's pin; removing a row above leaves the defaults
alone; nothing in the second list affects *Restart to apply*. Every call
that means *whose grants* passes the box; the workspace is passed only
where `grant_reason` and the defaults need it and as the folder
chooser's starting point. *Allow a directory…*
(`Gtk.FileDialog.select_folder`, modal, closes the popover — so the verdict
goes out as a toast either way) is `allow_directory`: `host.allow`, whose
refusal is a toast and ends there; then `grants.allow(workspace, path,
done)`, and one toast when `done` lands, by this tab's own `Delivery` —
"Allowed {path}" (live and linked), "… — inside the sandbox at {inside}"
(live, not linked), "… — restart the session to apply" (pending). The
remove button is `host.revoke` then `grants.revoke`, and the popover is
drawn again when that lands. `done` runs on the grants' worker thread:
`_land` puts it on the main loop with `GLib.idle_add(...,
priority=GLib.PRIORITY_DEFAULT)` — an idle-priority landing starves under
Xvfb, and a chip stuck on *after restart* after a delivery that worked is
that. *Restart to apply* is offered when `can_restart_
sandboxed()` and `host.plan_stale(plan, workspace,
live=grants.live_paths(box))`, and *Sandboxed shell* closes the list. The
chip knows
no tab or app: it takes callables (`plan_path`, `host`, `grants`, `box`,
`can_restart`,
`on_restart`, `on_open_shell`, `on_toast`); the tab's `"toast"` signal
reaches `MainWindow._on_tab_toast` (markup-escaped, over the sidebar's
toast overlay).

**Live grants (`sandboxgrants.py`, GTK-free).** One `GrantMounts(host)`
per app instance, handed to the tabs as `terminal.SANDBOX_GRANTS`; it owns
every mount this instance made.

- **One worker thread**, `sandbox-grants`, fed by a queue and alive until
  `shutdown()`. Every spawn, mount and unmount runs on it: that serialises
  them, and it is the thread `PR_SET_PDEATHSIG` ties the servers to —
  **never spawn `bindfs` from anywhere else**, a short-lived thread's
  exit would take the mount with it. `register(plan)` / `unregister(box,
  wait=)` follow a box's life, `allow` / `revoke(box, path, done)` a
  grant's and `sync(box, done)` delivers what the state holds for a box
  that it lacks; each acts on the one box it is handed (`allow` mounts
  nothing the state doesn't grant that box, and nothing for a box that
  isn't registered), queues and returns. `done(list[Delivery])` is called on
  the worker thread. `status`, `delivery`, `live_paths`, `registered` read
  a table under a lock and never block.
- **`capable()`** is `""` or the reason, settled once: `bindfs not
  installed` (`COLLINS_BINDFS`, else PATH), `fusermount3 not installed`
  (`COLLINS_FUSERMOUNT`, else PATH), `FUSE is not available` (no
  `/dev/fuse`), `mounts don't propagate from the sandbox directory` (the
  mount holding the sandbox root — the longest mount point in
  `/proc/self/mountinfo` that is a prefix of it — has no `shared:`
  field). Nothing short of mounting knows whether a mount will be
  *permitted*; a refused one is a `PENDING` delivery with bindfs's own
  first line of stderr.
- **Delivering one grant to one box**: `capable()`, then `guard_path`
  again (as `build_plan` does for a static grant), then `place()`. Under
  `$HOME`: the mount point is `<carrier>/<slot>` (`slot_name`: the last
  component made safe, cut to 40, then 8 hex of the path's SHA-256),
  `inside` is `/run/collins/grants/<slot>`, and a link is wanted at
  `<box home>/<path relative to $HOME>` — unless a mount of the box's
  plan covers that spot, where a link would sit unseen. Under an anchor
  the box emitted: `<anchor's host dir>/<rest>`, `inside` is the path
  itself, no link. Anything else: `PENDING`, "can't be added to a running
  sandbox". Then the mount point is made by file descriptor
  (`_make_point`), a stale mount there unmounted, and `bindfs -f
  --no-allow-other -o fsname=collins:<pid> <path> <mount point>` spawned;
  `mountinfo` is polled every 10 ms for the point with type
  `fuse.bindfs`, 5 s at most. The server exiting, or the timeout, is
  `PENDING` with its reason, the child stopped and anything that
  half-appeared unmounted.
- **The link** (`_make_link`) is walked from the box's home down by file
  descriptor, each component opened relative to the last with
  `O_NOFOLLOW | O_DIRECTORY`, what is missing created. At the last
  component a symlink is replaced, an empty directory removed and
  replaced, a missing name created. Anything else in the way, or a
  component that is not a directory, leaves the grant `LIVE` with
  `linked` False — it answers at `inside` only.
- **Revoking**: the link goes (only while it still is a symlink whose
  target is this grant's `inside`), `fusermount3 -u -z`, the server's own
  exit for 1 s, `SIGTERM`, 2 s, `SIGKILL`, then `rmdir` of the mount
  point. Where the grant is static nothing can be done to the running
  box: `LEAVING`.
- **`register`** ends as `sync` does: every grant the state holds for
  this box that its plan lacks is delivered — nothing for an ordinary
  launch, whose plan was built from that very list; a box
  registered again has what was live in it unmounted first.
  **`unregister(box, done=)`** unmounts everything live in the box; the
  restart needs the home clear before the next plan is prepared, and a
  box can't be removed before its mounts are gone, but **the main loop
  waits for neither**: a server a process inside still held a file of
  takes seconds to end, several of them several times that. The tab
  unregisters through `_unregister_sandbox_box(then)` and goes on from
  `done` — the restart lands `_relaunch_sandboxed` with `GLib.idle_add`
  at default priority, and so does the shell's exit, which releases the
  lease and forgets the box there. `unregister(box, wait=True)` still exists and
  says whether the unmounts finished in time; `_release_sandbox_plan`
  calls it for a box that is, by then, already unknown.
  **`sweep_mounts()`** at startup, before
  `sweep_boxes`, unmounts every `fuse.bindfs` mount under the sandbox
  root whose source is `collins:<pid>` with no such process.
  **`shutdown()`** from `App.do_shutdown`.
- **A sibling can't start inside a directory its parent holds only
  live** (`SandboxHost.derive(..., live=grants.live_paths(parent box))`):
  its plan is the parent's as launched, and `derive` records the
  parent's static grants as the sibling's own list. Nothing the parent
  holds live, and nothing it is allowed later, reaches the sibling: a
  directory is allowed in the sibling's own chip, and delivered live
  there like any grant.

**The invariants** — the first three are never loosened to make
something work:

1. A mount Collins makes lands only in a box's `grants/` or `anchors/`.
   Never under a `home/`, never on a path any box can write.
2. Every write and every removal under a `home/` goes by file descriptor
   with `O_NOFOLLOW`; a symlink there is unlinked or replaced, never
   followed.
3. Nothing is deleted through a mount: a box with a mount under it is
   not removed, and a removal that meets another device stops.
4. Only `<root>/<32 hex>` is ever removed.
5. A grant that can't be delivered live is `PENDING`, never an error and
   never a guess: the restart applies it.
6. `bindfs` is spawned only by the `sandbox-grants` thread.
7. A sandboxed session never launches unsandboxed because of anything
   here.
8. Nothing delivers, lists or removes a grant for any box but the one it
   was asked about. There is no lookup from a workspace, a project or a
   repository to a set of boxes anywhere in the feature — a project's
   defaults are copied into a box when it is minted, and never linked.
9. `state.sandbox_grants` is written on the main loop only.

**Restart to apply** (`TerminalTab.restart_sandboxed`): the CLI's exit
keystroke, a `_RESTART_POLL_MS` poll that answers the worktree keep/remove
dialog and re-nudges at `_RESTART_NUDGE_TICKS` (a mid-turn agent spends the
first Ctrl+C Ctrl+C on itself), and — once the shell has the terminal
back — `_launch_command(self._cwd, self.session_id)` typed again: a fresh
plan from the state now, the old one released, the same shell, tab and
row. Gives up with a message at `_RESTART_GIVE_UP_TICKS`. `can_restart_
sandboxed` also refuses three tabs that would get something other than a
restart: a fork (the tab holds its origin's id, and a resume would fork it
a second time), a tab whose id the resolver hasn't bound yet (a
`new_command` would *replace* the conversation, not restart it), and one
running a plan adopted from another session (`_sandbox_plan_adopted`: a
sibling's box was built for its parent's workspace, and a rebuild here
takes this tab's own cwd, silently narrowing it). The chip says so in a
caption where the row would be. `_launch_command(cwd, id, restart=True)`
is what turns the id ahead of a `--continue` override — an initial spawn
honours the override it was handed. The launch cwd, not the agent's last
one: a resume re-enters the worktree the transcript records by itself,
and the worktree lies under the launch workspace's mount.

**The sandboxed shell.** `PanelTerminal(number, plan_lookup=...)` is the
same page class with `sandboxed` True: `page_kind` stays `"shell"` (so
history, busy checks, close confirmations and layout persistence all hold;
`page_state` adds `"sandboxed": True`, which `panellayout._valid_page`
keeps and `PanelDock._restore_node` hands back to `strip.new_shell(
sandboxed=True)`), titled *Sandboxed shell N* on the dock-wide numbering,
wearing `sandboxchip.ICON`. It spawns `providers.sandboxed_shell_argv(
plan, $SHELL)` — `python3 sandboxrun.py <plan> -- $SHELL`, so it runs in
exactly the session's box — with a queued `cd` to the agent's directory
when that lies inside the workspace (bwrap's `--chdir` lands it in the
workspace). No plan at spawn (a layout restored before the launch settled,
or into an unsandboxed tab) → a message and no shell; the next show
retries. **Busy detection**: bash inside the box takes the pty's
foreground for itself and the kernel reports its process group in host pid
numbers, so `has_running_command` compares against the shell *inside*
(`proctree.inner_shell_pid`: the first descendant that isn't the
launcher or a `bwrap`), cached once found; measured under a real box
(idle at the prompt, busy during `sleep`, idle after Ctrl+C). A shell keeps the box it spawned in
(`PanelTerminal.sandbox_plan`, the plan file `_spawn_plan` recorded):
`--die-with-parent` ties that box to the shell's own pty, not to the
session, so a *Restart to apply* leaves it running in the old box, which
may still hold a directory the user has since revoked — the tab notes that
in its scrollback (`_mark_stale_sandboxed_shells`) and
`mcptools.tool_shells(shells, sandboxed, plan)` stops handing it to the
agent (a shell that hasn't spawned yet takes the current plan when it
does, so it counts). Ctrl+J
never binds to one (`PanelDock._on_page_touched` skips `sandboxed`
pages), and `open_shell_page(sandboxed=True)` / `PanelStrip.new_shell(
sandboxed=True)` / the strip menu's *New sandboxed shell* (offered while
`set_sandboxed_shell_offer` says there is a plan) are the ways in;
`TerminalTab.open_sandboxed_shell(focus)` opens one beside the last shell
or in a strip of its own on the home edge.

**What a sandboxed session may ask Collins to do.** `run_tool_call` hands
every handler a third argument, `sandboxed` — Collins' own reading of the
calling tab (`is_sandboxed=lambda found: found[1].sandboxed`), never the
caller's word — and the three host-reaching handlers apply the policy:
`_mcp_read_terminal` / `_mcp_run_in_terminal` filter through
`mcptools.tool_shells(shells, sandboxed)` (a sandboxed session sees only
`sandboxed` shells; the reply names them *Sandboxed shell N* via
`terminal_reply`'s fourth tuple slot) and open one with
`tab.open_panel_shell(sandboxed=True)` when none is idle — the user's own
Ctrl+J shell is never typed into or read from inside a box.
`_mcp_start_session`: `mcptools.sibling_sandboxed(parent, project_default)`
(a sandboxed parent's sibling is always sandboxed — the unit test says so
in those words), and for a sandboxed parent the sibling gets
`host.derive(parent's launch plan, cwd)`: the parent's exact mounts
(grants, shares, settings protection *as launched*) with `--chdir` moved,
around **a box of the sibling's own** — `derive` mints it, makes it,
seeds its home, mirrors trust for the cwd, scrubs, writes the plan and
holds the lease, returning (plan file, box id, reason) — refused
through `mcptools.sibling_cwd_refusal` when the cwd lies outside the
plan's workspace or grants (`plan_reaches`); the derived plan and box
ride in `options.sandbox_plan` / `sandbox_box`, the sibling tab adopts
and releases them, and a refusal past the derive, or a
spawn that never made a tab, drops both (`app._drop_sibling_box`).
bypass is granted (explicit or inherited) only when the sibling is
sandboxed. A parent in a linked worktree gets its sibling refused: the
sibling collapses to the repo root (the resolver's rule), which the
worktree's workspace mount doesn't reach. The display-only tools are
unchanged.

**Refusals.** `/bg` and `claude attach` are never used for a sandboxed
session (`bgstatus.BLOCK_SANDBOXED`, `_quit_backgroundable`,
`open_session`'s attach path, `ClaudeProvider.resume_command` with
`options.sandbox`): the daemon respawns jobs on the host, and its job
record has no wrapper seam (`respawnFlags` allowlist, `bgIsolation`
none|worktree, measured on 2.1.268). The header's background button is
*hidden* on a sandboxed tab (the refusal never clears, unlike the
registration window the greyed-with-tooltip rule is for); the sidebar
row's button stays greyed through the blocker like every other reason.

**The probe.** `sandboxplan.probe()` runs `bwrap --unshare-user
--unshare-pid … -- /bin/true` once per launch on a thread
(`probe_async` from `App._start_sandbox_support`) and caches
`probe_reason()`: `""`, `REASON_NO_BWRAP`, `REASON_NO_USERNS`.
`available()` probes synchronously (5 s cap) if asked before the thread
landed — only `prepare_launch` calls it; every UI path reads
`probe_reason() == ""` and treats None as "not yet", and
`App._on_sandbox_probe_landed` → `MainWindow.refresh_sandbox_availability`
→ `NewChatView.set_sandbox_available` puts the checkbox on screens built
before the verdict. The new-chat checkbox and the project-menu item are
shown only when it passes; the Preferences group is always built, its
switches go insensitive and its status row says why.

## Facts that shape it (measured on Ubuntu 26.04, bwrap 0.11.1, CLI 2.1.268)

- Unprivileged userns is AppArmor-restricted: bwrap works through Ubuntu's
  shipped profile, but nothing inside can mount and the host can't `setns`
  into the mount namespace. aibox's way of granting live (its broker and
  launcher, mounting from inside) is impossible here; Collins mounts on
  the host instead.
- **The mount point must not be agent-writable, and `/proc/self/fd`
  does not fix that.** libfuse's `fusermount.c` runs `realpath()` on the
  mount point's parent — which turns a magic link back into a path
  string — and then `lstat`s and `chdir`s to that *string* before
  mounting on `.`. An agent that swaps a component for a symlink between
  the two lands the mount on a host directory of its choosing, and the
  mount's content is the granted directory, which the agent writes:
  `<some repo>/.git/hooks` shadowed by a granted directory called `hooks`
  is code execution outside the box. Hence the carrier and the anchors,
  and a symlink — whose creation *is* race-free — for the real path.
- **Revoke has to end the server.** `fusermount3 -u -z` took 2.7 ms and
  the mount was gone inside 20 ms later, `bindfs -f` exiting by itself;
  with a process inside holding a file open the lazy unmount still
  removed the mount at once, but `bindfs` stayed up serving that file
  until `SIGTERM` (the holder's next read: `ENOTCONN`).
- **`PR_SET_PDEATHSIG` cleans up after a crash**, and is tied to the
  *thread* that forked: after `SIGKILL` to the spawning process the
  mount was gone within 20 ms.
- **Ubuntu confines `fusermount3` to a few places**
  (`/etc/apparmor.d/fusermount3`): under `@{HOME}/**/`, `/mnt/`,
  `/run/user/<uid>/**/`, `/media/**/` and `/tmp/**/`. The default sandbox
  root is under `$HOME`; an `XDG_DATA_HOME` outside it makes every live
  mount fail, which is `PENDING` with bindfs's message.
- Measured in the checks on this machine: a grant mounted in about
  17 ms, readable inside the running box 20 to 40 ms later, gone 20 ms
  after a revoke that took 5 ms.
- **A home per box isolates a mount the host makes** (measured
  2026-09-27, scripts in `~/specs/collins/assets/sandbox-live-grants/`).
  Two boxes, each with its own home and carrier: a `bindfs` mount made on
  the host in box A's carrier was in A's `/proc/self/mountinfo` 21 ms
  later, readable and writable there, and box B never saw it. With the
  one shared sandbox home the stack began with, the same mount landed in
  **every** open box — which is why a grant was restart-only, and why
  every session now has a box of its own.
- **`--ro-bind` is the wrong spelling for the carrier; `--bind` then
  `--remount-ro` is the right one.** bubblewrap's `--ro-bind` is
  recursive and remounts every submount it finds read-only: a box
  launched while a mount already sat in the carrier (a sandboxed panel
  shell opened later) got it read-only. With `--bind` early and
  `--remount-ro` at the end of the plan the directory is read-only
  (`mkdir`, `ln -s`, `mv`, `rmdir` inside: `EROFS`), a mount in it stays
  `rw`, and a static bind *under* such a directory needs no pre-created
  mount point (bwrap makes it before the remount).
- **A leftover symlink where a static bind goes stops the launch.** With
  a symlink at `<box home>/<rel>` and `--bind <dir> <dir>` for the same
  path, bwrap exits 1 ("Can't bind mount … No such file or directory").
  An agent can plant one; with one shared home it bricked every
  sandboxed session. `scrub_home` clears the destinations before every
  launch.
- **State is shared across app ids, not across `XDG_CONFIG_HOME`.**
  `state.json` is `~/.config/collins/state.json` whatever
  `COLLINS_APP_ID` says, so a debug instance beside the real one reads
  the same session → box map: hence one root and the lease. A test
  instance moves `XDG_CONFIG_HOME` and usually nothing else: hence the
  root's `owner`.
- The two `bwrap` processes carry the CLI's argv in their own command
  lines, so `proctree._deepest_agent_pid` walks through them and the CLI
  itself is the deepest agent: they are ancestors, not descendants, and
  nothing joins `infrastructure_cmdlines()` for them (test in
  `test_proctree`).
- `mcpserver._greet` trusts `SO_PEERCRED`: the shim's declared pid is
  namespace-local and differs by construction.
- `~/.claude/settings.json` bound read-only over itself: an append fails
  EROFS, a rename-over EBUSY; `/model` inside says "for this session only
  · couldn't save it as your default" and carries on. `settings.local.json`
  is protected only when it exists (bwrap would create it) — an agent can
  create one; documented, not fixed. The project's own `.claude/settings*`
  sit in the workspace and can't be protected at all, and the native
  installer's `~/.local/bin/claude` symlink sits in a shared tree and can
  be repointed. The box bounds the filesystem, not the hook surface; the
  docs say so in those words.
- `gh` keeps its token in the Secret Service keyring on a desktop, which the
  box can't reach, so a bind of `~/.config/gh` alone yields "token invalid";
  hence `GH_TOKEN` via `gh auth token` in sandboxrun. `git_protocol: ssh`
  users push over SSH, which the SSH-agent share covers.
- bwrap creates every mount point inside the box's home and the skeleton
  persists (empty dirs, 0-byte file stubs for `RO_HOME` files). Derive
  "what is inside" from the plan, never by listing the home; a later
  launch that no longer binds a `RO_HOME` file exposes the stub.
- The seeded `~/.claude.json` carries the user's global `mcpServers` and
  Remote Control: both run inside the box, and the file grows with the
  CLI's per-project stats (160 KB is normal).
- The CLI's own sandbox setting self-disables inside the box (socat
  missing, or "failed to initialize"); the two don't stack.
- The bypass-permissions acceptance dialog is not persisted in
  `~/.claude.json`; only the trust dialog can be pre-answered.

## Testing

`tests/test_sandboxplan.py` holds aibox's cases and the additions (the
fixture monkeypatches `RW_ABSOLUTE` to exclude `/tmp`, where pytest's
fake home lives, or the protect-check refuses every plan); one test runs a
real box over a real plan and skips where the probe says no.
`tests/test_sandboxrun.py` runs the module end to end against a fake bwrap
that dumps the fd; `load_plan` / `plan_reaches` / `derive_plan` and the
`SandboxHost` (allow, revoke, stale, derive) have their own cases there,
and so do the boxes: ids, anchors and their two drop rules, `scrub_home`
on real trees (the planted link's target must survive), `remove_box`
with the mount table and `stat` injected (`mounts=`, `stat_fn=`),
`discard_box`, the lease, the sweep and its owner;
`tests/test_sandboxgrants.py` holds the live grants over a fake spawn, a
fake mount table and a fake clock (`GrantMounts(host, spawn=, mountinfo=,
run=, clock=, sleep=, pid_alive=, bindfs=, fusermount=, fuse_device=)`),
the link on real trees, and one test over **real** bindfs and two real
boxes that skips where `capable()` or the probe says no — it prints what
it measured, and cleans every mount before pytest's own cleanup could
delete through one;
the session → box map in `tests/test_state.py`,
the policy helpers (`tool_shells`, `sibling_sandboxed`, the handler flag)
in `tests/test_mcptools.py`, `inner_shell_pid` in `tests/test_proctree.py`
over a real process tree, the layout flag in `tests/test_panellayout.py`.
`scripts/check_sandbox_policy.py` is the e2e check: a real App, a session
launched sandboxed through a **fake** `COLLINS_BWRAP` (records the
`--args` payload, execs the command; answers the probe with exit 0),
driving the chip, the sandboxed shell, both terminal tools from the box,
a grant → stale → restart → relaunch with the grant, and a sibling
derived / refused — all with `COLLINS_BINDFS=/nonexistent`, so a grant
waits for the restart; then a last pass with the override gone and a
fresh `GrantMounts` in the app's place, where a directory allowed through
`chip.allow_directory` is tagged *live*, asks for no restart, and leaves
through its remove button (the fake bwrap builds no box, but the mount
and the link are made on the host all the same). Staged under `~/.cache/collins-e2e` with `HOME` moved
into the scratch tree — `/tmp` is shared into every box, so a scratch
tree there trips the protect-check, and a real home would get the
`RW_HOME_ALWAYS` directories. `scripts/check_sandbox_launch.py` is the
other half and has no GTK in it: a plan `prepare_launch` wrote, run under
the **real** bwrap (`sandboxrun.py <plan> -- /bin/sh -c …`), reporting
from inside — the workspace and the grant writable, the un-granted
sibling, `~/.ssh`, Collins' own state and the plan file itself absent,
`settings.json` read-only, `/usr` read-only, its own pid namespace, the
carrier and `/mnt` there and unwritable, the sandbox root absent — and a
second box for a second workspace, blind to the first's home, workspace
and grant — and the **live section**: both boxes kept running on a
command loop, a directory allowed, readable at its real path in the
first within 2 s, the second blind to it, revoked, gone. It is
the only proof that bwrap *accepts* a generated plan, and it exits 77
(`run_e2e`'s skip) with a printed reason where no box can be built — a CI
container may have no user namespace to give. Both checks print their
live part as *SKIP* and still pass where `capable()` says no, or where
the first delivery comes back `PENDING` (a container with the tools and
nothing to mount with); on a development machine they must run. Any
probe or e2e run needs a fresh `COLLINS_APP_ID` and
`COLLINS_SANDBOX_ROOT` beside the usual scratch tree, staged under
`~/.cache/collins-e2e` — under `$HOME`, where AppArmor lets `fusermount3`
mount. **After anything that mounts, look at `/proc/self/mountinfo`
before deleting a scratch tree, and never delete through a mount**: both
checks' `clear_tree` unmounts what is left and leaves the tree alone if
anything still is.

Related: `collins-terminal-tab`, `collins-sessions-and-sidebar`,
`collins-session-mcp-tools`, `collins-preferences-keybindings-i18n`.
