---
name: collins-sandboxed-sessions
description: >-
  How Collins runs a Claude Code session inside a bubblewrap filesystem
  sandbox: the GTK-free mount plan (sandboxplan.py, a port of aibox's
  plan.rs with Collins' additions), the stdlib-only host launcher the typed
  command starts with (sandboxrun.py), SessionOptions.sandbox and the
  provider wrapper, the sticky per-session flag and per-project override in
  state.json, the new-chat Sandboxed checkbox, trust mirroring into the
  sandbox home, the /bg and attach refusals, the Preferences group and the
  bwrap probe, and what the box does and does not contain. Use when changing
  anything about sandboxed launches, the plan's tables or ordering, grants,
  the two shares, or debugging a session that came up unsandboxed, a shim
  that can't reach Collins from inside, or a settings.json write that failed
  with EBUSY.
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
makes it editable. **Ordering rule**: the overlay home first,
Collins' own read-only binds *before* the workspace (a workspace that
overlaps them — a Collins checkout under `./start-debug` — must land on
top and stay writable), masks last. `carried()` decides which masks to
emit: bwrap would *create* a destination it was told to cover, so only
secrets that exist and that a shared mount actually reaches are masked.
The workspace is held to the grant rule (`guard_path`: never a secret,
inside one, an ancestor of one, `$HOME` or above it — checked as written
*and* resolved, against the home as written and resolved, and against the
target of any secret that is itself a symlink, so a `~/.ssh` that points
into a dotfiles checkout refuses the checkout too); grants go through the
same guard and are kept as written, not `realpath`'d. The protect-check
refuses a plan that would carry `~/.config/collins`,
`~/.local/state/collins`, `~/.cache/collins` or the plan directory
(`PlanRefused`). Every path is validated (`valid_path`: absolute, bounded,
no control characters, no `.`/`..`). The output is a JSON document
(`bwrap_args`, `unsetenv`, `setenv`, `gh_token`, `workspace`, `inputs`,
`notes`), written by `prepare_launch` to
`$XDG_RUNTIME_DIR/collins/<app id>/sandbox/<uuid>.json` mode 0600 and
regenerated at every launch — a cache of state, never state.

`prepare_launch(workspace, app_id, state)` is the host side of a launch: it
creates the `RW_HOME_ALWAYS` directories (a bind needs a source), seeds the
sandbox home once with `~/.claude.json` (`seed_home`; mode 0700, the copy
diverges from then on), mirrors folder trust (`mirror_trust`:
`hasTrustDialogAccepted` for the workspace *and its trusted ancestors*, and
nothing else — the one write Collins makes to a file the CLI would not
have written itself, into a file Collins owns), builds and writes the plan.
Returns None when no box can be built; the tab then launches unsandboxed
and says so, dropping a bypass mode the box justified. **Every path under
the sandbox home is attacker-controlled** (it is `$HOME` inside, shared by
every box): the two writes there go through `_replace_private` —
`lstat` the destination and refuse anything but a regular file or
nothing, write a fresh `O_EXCL | O_NOFOLLOW` temp under a random name,
rename — so a planted `.claude.json` or `.claude.json.tmp` symlink never
becomes a host write. `sweep_plans(app_id)` at startup clears plan files
a killed tab never released (bwrap reads its plan once at exec).

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

**The tab.** `SessionOptions.sandbox` is the decision, `sandbox_plan` the
file. `TerminalTab._finish_spawn` writes the plan at the last moment
through `terminal.SANDBOX_PLANNER` (set by `App._start_sandbox_support`,
like `providers.MCP_CONFIG_PATH`) because the workspace is the *settled*
cwd — a recreated worktree included — and `Provider.sandbox_prefix`
prepends the wrapper in `new_command` / `resume_command` and, for the
`--continue` override, in the tab itself — which also appends
`Provider.session_flags` (the permission mode) there, after the options
settled, so a box that couldn't be built never leaves a bypass flag typed.
`--die-with-parent` ties the box to the tab's shell, so every close flow
holds; the plan is unlinked in `_on_child_exited`. `tab.sandboxed` is the
launch record (as launched, not as the settings now say). A sandboxed
*fork* tab keeps its origin's id but runs the resolver in `_fork_resolve`
mode: the forked conversation's id lands on `fork-resolved` and the window
adds it to the sticky set, so the fork's own row resumes boxed too.

**State.** `sandbox_new_sessions` + `project_sandbox` overrides
(`sandbox_for_project`, the sidebar project menu's *New sessions are
sandboxed* check), `sandbox_bypass_permissions`, `sandbox_share_gh`,
`sandbox_share_ssh`, `sandbox_settings_editable`; the sticky
`sandboxed_sessions` set (written when a sandboxed launch resolves its id,
or a sandboxed fork reports its new one, carried by `forward_session`,
read by `open_session` for a resume); `sandbox_grants` per real workspace
path (no UI yet — the footer chip is the next PR). The draft record's
`sandbox` slot mirrors the worktree checkbox exactly, and
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

**What a sandboxed session may ask Collins to do.** Until the policy PR
(a sandboxed `ShellPage`, a sibling that inherits the box and stays inside
the workspace), `run_in_terminal`, `read_terminal` and `start_session` are
**refused from a sandboxed tab** (`mcptools.SANDBOX_HOST_TOOLS`, checked in
`run_tool_call` after identity, `is_sandboxed=lambda found:
found[1].sandboxed` from app.py): each reaches the host — the user's own
unconfined Ctrl+J shell, a sibling in an agent-chosen cwd — and under
bypassPermissions no prompt stands in between. The display-only tools are
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
  into the mount namespace. Live grants (aibox's broker/launcher) are
  impossible here; a grant applies at the next launch. Phase 2 is FUSE
  (`bindfs` mounted by the host under sandbox-home propagates in).
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
- bwrap creates every mount point inside sandbox-home and the skeleton
  persists (empty dirs, 0-byte file stubs for `RO_HOME` files). Derive
  "what is inside" from the plan, never by listing sandbox-home; a later
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
that dumps the fd. E2E checks (`check_sandbox_launch.py` with
`COLLINS_BWRAP`, a real-box check that skips) are the packaging PR's. Any
probe or e2e run needs a fresh `COLLINS_APP_ID` and `COLLINS_SANDBOX_HOME`
beside the usual scratch tree.

Related: `collins-terminal-tab`, `collins-sessions-and-sidebar`,
`collins-session-mcp-tools`, `collins-preferences-keybindings-i18n`.
