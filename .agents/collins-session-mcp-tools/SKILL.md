---
name: collins-session-mcp-tools
description: >-
  How the Collins MCP server works — the tools every launched Claude Code
  session can call back into the app with (set_session_title, open_in_editor,
  show_diff, diff_context, annotate_diff, highlight_diff, clear_diff_marks,
  show_image, notify_user, attach_pr, start_session, read_terminal,
  run_in_terminal): the stdlib-only stdio shim (mcp_shim.py), the GTK-free
  tool table, validation, framing and runtime paths (mcptools.py), the Gio
  socket service (mcpserver.py), the dispatcher and handlers on the service
  (service/tools.py) with their client halves (toolclient.py), session
  identity via the shim pid, deferred replies and the active-client tool
  events, the per-tool Preferences switches, plus the
  lightbox and the attachments gallery that show_image feeds. Use when adding
  or changing a session tool, debugging "Collins is not running" from an
  agent, or touching the lightbox/attachments panel.
---

# Session MCP tools

Every session Collins starts gets `--mcp-config <file>` naming one stdio
server, `python3 -m collins.mcp_shim`, so the agent sees a `collins` server in
`/mcp` with the tools the user left on. Thirteen tools today; each has an
on/off switch in Preferences → Built-in MCP tools (`mcp_tool_<name>`, derived
from the tool table by `mcptools.default_tool_settings()` so a new tool can't
ship without a switch). **A sandboxed session is offered six of them by
default** (`mcptools.SANDBOX_DEFAULT_TOOLS`), by a second set of switches
(`sandbox_tool_<name>`, Preferences → Sandbox) and its own box's
(`state.sandbox_tools`, the Sandboxed chip): a new tool is **off** inside
a box until it is put on that list — see `collins-sandboxed-sessions`.
The config file itself is per app id under
`~/.local/share/collins/<app id>/`; the socket is
`$XDG_RUNTIME_DIR/collins/<app id>/mcp.sock`. Every tool's definition rides in
each session's context, which is why the Token use disclosure lists them.

## The three modules

**`mcp_shim.py` — stdlib only, imports nothing from `collins`.** It is spawned
by the CLI, not by Collins, and must never break a session: Collins gone
(quit, crashed, stale config) degrades to an empty tool list and clean
"Collins is not running" errors; the MCP handshake always succeeds. It
relays `tools/list` and `tools/call` over the Unix socket named by
`COLLINS_MCP_SOCKET`, one newline-delimited JSON frame each way; a failed
round trip marks the connection dead and the next request reconnects (that is
what heals a Collins restart — no retry loops inside a request). Wire
constants (`_MAX_LINE` = 1 MiB, `_CALL_TIMEOUT` = 15 s) are mirrored by hand.
Debug output goes to `COLLINS_SHIM_LOG` — stdout is protocol bytes only.
Error strings are agent-facing English, untranslated.

**`mcptools.py` — GTK-free.** `TOOLS` is the table served verbatim to
`tools/list`, in MCP's own shape with JSON schemas; `validate_args` checks
calls against them (strings with min/max length, integers with `maximum`,
booleans, enums; `additionalProperties: False`). `run_tool_call(tool, args,
find_tab, handlers, is_enabled, is_sandboxed, is_offered)` is the validate →
switch → identity → offered → handler
skeleton, so its branching is unit-tested (`is_offered(found, tool)` is the
caller's own list: a sandboxed session's, by its box). Also here: `encode_message` /
`decode_message` (framing shared with the shim), `runtime_dir` / `socket_path`
/ `config_path` / `write_config`, `infrastructure_cmdlines()` (the shim's
cmdline for the process-baseline that keeps it from reading as work),
`inherited_permission_mode` (a caller's transcript mode passes through;
`bypassPermissions` caps to `acceptEdits`), `terminal_reply` (shrinks
`read_terminal` output to fit one frame), and `DeferredResult`.

**`mcpserver.py` — Gio only.** `SessionToolService` listens on the socket;
protocol is `hello` (carrying the shim's pid), then `list` and `call`
correlated by id. Everything runs on the GLib main loop with Gio async
sockets (no threads); per connection strictly read → reply → read, so a peer
that stops reading stalls only itself. Every frame is untrusted: a malformed
hello or broken framing disconnects the peer, and the pid that authorizes
the connection is **`SO_PEERCRED`, never the declared one** — the declared
pid only has to be shaped like a pid. It used to have to match; a shim
inside a PID namespace (a bubblewrap sandbox, see
`~/specs/collins/sandboxed-sessions.md` and `scripts/spike_sandbox.py`) can
only report its namespace-local pid while the kernel translates
`SO_PEERCRED` into ours, so the two disagree by construction and the
kernel's answer is the one the `/proc` walk needs. A peer with no
credentials is still dropped. `start()` refuses socket paths over 107 bytes: Gio
silently truncates longer ones and listens on the wrong path (bit a scratch
tree with a long tmpdir prefix).

## The twin: `collins/api/protocol.py` (the service/client API)

The split into `collins-service` and a GTK client
(`~/specs/collins/split-service-and-client.md`, §3.2) gets a table of its
own, modelled on `mcptools` and kept here until the split has a skill.
**GTK-free and stdlib only** (`json`, `math`, `re`, `struct`,
`dataclasses`), pinned by `tests/test_protocol.py`, since both halves
import it. It holds:

- `TYPES`: every message type, each with a request form (always client →
  service, with the fields of its ok reply) and/or an event form (either
  way, the sender recorded). `state.set` and `seen` have both. The set of
  names is pinned against the spec's list; a new type is a
  deliberate edit there, and ships behind a name in `CAPABILITIES`.
- Field specs (`Field`: kind, bounds, pattern, choices, nested shapes).
  Every string is bounded, every list and map capped, free JSON (state
  values, PR records, tool arguments) bounded by depth and node count.
  Unknown fields are dropped at every depth of a closed shape; free JSON
  is kept whole for its receiver to re-validate.
- **The contract**: `validate(message, sender)` returns a `Message` (type,
  kind, id, the kept fields) or a `Refusal` (`re`, `error`, `msgid`,
  `args`). A refusal with an `re` is answered with `to_message()`; one
  without (an event, a frame with no usable id) is dropped. Responses go
  through `response_id` then `validate_response(message, request_type)`.
  Error codes are `ERRORS` (`unknown` for a type nobody knows, `invalid`,
  `direction` for a type from the wrong peer, `protocol`, `sequence`,
  `gone`, `refused`, `failed`); a receiver accepts any code-shaped string.
  `msgid` is an English source string with `{name}` placeholders, for the
  client's `i18n._()` and `format_map`.
- Files over the API (§3.23, PR-2.3; the `files` cap): `fs.read {path,
  max}` answers a file's `text`, `encoding` (`utf-8` or `latin-1`),
  `mtime` (microseconds), `size` and `binary`; `fs.write {path, text,
  expect_mtime, encoding}` is refused `stale` with nothing written when
  the file's mtime moved from `expect_mtime` (null writes regardless);
  `fs.watch {path, kind, handle, mtime}` / `fs.unwatch {handle}` are
  per-client `Gio.FileMonitor`s on the service (at most 512 a client)
  pushing `file-changed {handle, path, mtime, size, gone}`, debounced
  300 ms; the first stat is compared against the client's `mtime` seed,
  so a change between the client's read and its watch is an event at once. The handlers are
  `service/files.py`'s `Files`, the client's `remotefiles.py`. A message
  over the frame cap is chunked **either way**: `split_message` /
  `join_message` on the first of `CHUNKED_FIELDS` (`stdout`, `text`) a
  message holds, as `TAG_BLOB` frames under the request id ahead of a slim
  message saying `<field>_chunked` / `<field>_bytes`; the server joins a
  chunked request (`join_request`) before validating it, the client's link
  a chunked reply (`join_reply`).
- Framing: `encode` (refuses over `MAX_FRAME`, 1 MiB, and NaN / lone
  surrogates) and `decode` (refuses over `MAX_INCOMING`, 16 MiB, NaN,
  non-objects, nesting Python can't parse). The binary header is
  `tag:u8 flags:u8 reserved:u16 stream:u32 offset:u64`, big endian, 16
  bytes: `pack_header` / `unpack_header` / `pack_frame` / `unpack_frame`
  and `check_frame` (tag 0x01 output from the service with REDRAW 0x01 /
  REDRAW_END 0x02, 0x02 input from the client, 0x03 blob chunks either
  way; `reserved` is reported, never refused).
- **Versioning is additive.** `PROTOCOL` and `MIN_PROTOCOL` (both 1,
  pinned) bound what a peer speaks; the window is at most two wide, so an
  upgraded client still talks to a service with running agents.
  `negotiate(own, own_min, peer, peer_min)` gives both sides the same
  answer, None being the mismatch dialog. Also here: `QUEUE_BYTES` (4 MiB),
  the keepalive pair (10 s, 10 s), `LOCAL_PROOF_BYTES`.

The module docstring's "Shapes the spec left to this module" records each
field shape chosen beyond the spec's text; read it before adding a field.

PR-2.8's additions: `store.transcript-export {session}` (reply `text`,
at most `TRANSCRIPT_EXPORT_MAX` characters, which is `CHUNKED_MAX // 4`
so the text always fits a chunked field in UTF-8; `text_chunked` /
`text_bytes` set by the transport), and `cwd` in the replies of `spawn`
and `pty.info`: where the service started the pty (D39), the request's
cwd or the service's fallback. Both are optional reply fields, so an
older service's reply still validates.

## Identity and dispatch (`service/tools.py`, `toolclient.py`)

The dispatcher is the service's: `SessionTools` (built by
`ServiceCore.start_tools`; the core also runs the MCP socket, `start_mcp`,
so `mcp.sock` belongs to the `collins-service` process and an agent keeps its
tools with no window open; the window's half is `app.tool_client`). There is no session id in an MCP server's
environment. `SessionTools.find(pid)` walks the shim's `/proc` ancestry
(`proctree.ancestor_pids`) and asks every `Session` the service holds
`owns_pid_ancestors` (`ServiceCore.sessions`: a `hosting.SessionRecord`
per live agent pty) — so a tool acts on the session whose shell the
CLI descends from, the pid being the kernel's (`SO_PEERCRED`), never the
shim's word. Anything not launched from a session's pty (a daemon-hosted
`/bg` job, whose ancestry tops out at systemd; an ended session) gets a
clean "not from a Collins session" error. `list_tools`, `tool_enabled` and
`tool_offered` are over the Session (§5 of the
split spec: nothing a sandboxed session is offered or refused changes;
`tests/test_service_tools.py` pins the parity), reading the service's
settings.

**Where a tool runs** (the split spec's §3.7 table, in the module
docstring): `set_session_title` (the service store renames; the tab's
title follows the name through the state mirror) and `attach_pr`
(`Session.attach_pr`) run on the service whoever is attached. Every other
tool is **UI-bound**: a `tool` event (`call`, `session`, `handle`, `name`,
`arguments`, and `sandboxed`: the service's reading of the caller) to the
session's **active client** (`ServiceCore._tool_client`, D20) and a
`DeferredResult` settled by the client's `tool-reply` or at
`TOOL_BOUND_S` (14 s, under the shim's 15 s). Over the socket the client
answers inside the event, so a call that finishes at once still returns
`(ok, text)` at once (`_settled`) — the e2e checks rely on it. The client
half is `toolclient.ToolClient` (`app.tool_client`): it finds the tab by
the Session's `handle` (an unresolved session is still found) and runs
`<tool>(found, args, sandboxed)` with `found = (window, tab)` —
`_ShowDiff`, `_BackgroundSpawn` and the per-root spawn queue
(`app._start_session_chains`, shared). **With no
client attached** each tool does what §3.7 says (`_headless_<tool>`):
`open_in_editor` replies that no window is open; `show_image` records the
attachment; `notify_user` records an unread row in the service's history
and flags the session; `show_diff` records the session's pending load
(`state.pending_diffs`), waits up to 10 s for a client to subscribe
(`apply_pending_diffs`), then answers "queued"; the diff tools work on the
service's `diffnotes` store over its own read of the diff (`service/
diffs.py`); `read_terminal` / `run_in_terminal` reach the session's shell
ptys on the pty server (`run_in_terminal` opens at most one shell of its
own per session, reused and refused when busy, closed when the session's
agent pty exits); `start_session` is refused with no client
(`START_NEEDS_CLIENT`: its client half spawns a tab). With a
client, the tab is found by the event's `handle`
(`ToolClient.found_for_handle`, `found_for_pid` through
`SessionTools.find`); the sibling's derived plan and box come from the
service (`sandbox.derive`, `sandbox.drop` when the sibling will not
launch after all).

**Deferred replies.** The whole dispatch runs on the main loop, so a handler
that blocks freezes the window. Return `mcptools.DeferredResult` and resolve
it later from the main loop; `mcpserver` holds that connection's reply
(read → reply → read, stretched). Budget well under the shim's 15 s
(`remoteimages` caps at 10 s / 25 MB, now on the service), **always** resolve — even on an
unexpected exception — or the connection goes silent forever, and expect the
late reply to land on a gone connection (`_send` no-ops). A reply larger than
`MAX_LINE` doesn't degrade, it closes the connection; `terminal_reply` halves
tails until the JSON-encoded size fits with a 16 KiB margin.

## The tools

- `set_session_title` — writes Collins' manual name slot (beats generated and
  CLI titles).
- `open_in_editor` — resolves a path against the tab's live cwd and editor
  root (`ToolClient.resolve_file`), opens it at a line (`window.open_in_tab_editor`,
  which honours the pop-out rule).
- `show_diff` — opens the git page quietly on the working tree / index /
  branch / a commit (`open_git_page(focus=False)`), waits for the read to
  settle (`toolclient._ShowDiff` polls `page.settled()` up to
  `gitloads.SHOW_DIFF_DEADLINE_S`), then `GitPage.reveal(path, hunk, side,
  line, focus=False)`; the reply names what loaded and what was revealed
  (a line no hunk holds lands on the nearest hunk and the reply says so;
  a file the files filter hides is revealed with the filter cleared). The
  not-a-repo card ends the poll only while `page.opening` is False — a
  page that stood on the card when the tree turned up shows it until its
  open lands. Decisions in `gitloads.show_diff_load` / `diff_file_path`.
  `side` (`old`/`new`) and `hunk` (1-based, exclusive with `line`; past
  the file's count → `mcptools.hunk_refusal`, never a silent hunk 1)
  joined the schema in PR 5; `line`, `hunk` and `side` all need `file`.
  The reply is `mcptools.reveal_reply` (the load, then the file, the
  spot, the hunk count and the 1-based hunk landed on).
- `diff_context`, `annotate_diff`, `highlight_diff`, `clear_diff_marks` —
  the page-reading and marking half of the diff tools, all behind
  `ToolClient._on_diff_page(tab, act)`: refused with `mcptools.PAGE_NOT_OPEN`
  (which names `show_diff`) when the tab has no page or one that is
  neither `opened` nor `opening`; run at once on a settled page; and
  otherwise waited for behind a `DeferredResult` through
  `toolclient._await_page_settled` (the one settle poll `_ShowDiff` uses too —
  the watch or the footer's tick may have a reload out), so an agent
  calling right after an edit isn't flaky. `diff_context` is
  `mcptools.diff_context_reply(page.context(), files, patch, notes)`:
  **one JSON object** (hunks 1-based with header / old / new ranges — a
  side the hunk has no lines on, a new file's old side, is `null`, not
  the view's padded `[0, 0]` — the current file + hunk, the selection's
  spans and text, the files, the patches under
  `DIFF_CONTEXT_PATCH_BYTES` (200 kB, a file that doesn't fit is
  `patch_omitted` and so are the later ones), the notes and highlights),
  shrunk stepwise — patches, then the hunk lists (`hunks_omitted` +
  `hunk_count` per file), then notes, then the file list, then the
  selection's text (`text_omitted`) — with a `truncated` key so it always
  fits one frame. **The measure is the framed reply**, `json.dumps(text)`
  as `encode_message` will escape it (indent-1 JSON doubles every newline
  and quote on the wire; a raw-bytes measure passed replies that closed
  the connection) with the same 16 KiB margin as `terminal_reply`.
  `mcptools.NOTE_MAX_CHARS` (diffnotes') is the schema's `maxLength` for
  a summary and a rationale. `annotate_diff` /
  `highlight_diff` shape their batches with `mcptools.note_specs` /
  `highlight_specs` (each refuses a non-repo path by index, `notes[1]
  (x.py)`, and a note with both or neither of `line` / `hunk`; the path
  resolver, `ToolClient._diff_path_resolver(tab, page)`, works against
  `page.repo_root` — the diff the agent sees — not the tab's live cwd,
  which the agent may have `cd`ed out of since `show_diff`; the cwd only
  breaks a relative path's tie, and a page on its card answers
  `PAGE_NOT_OVER_A_REPO`) and hand
  them to `page.add_notes(specs, focus, source=diffnotes.AGENT)` /
  `add_highlights(specs, focus)` — the `diffnotes.MarkStore` lands the
  batch whole or not at all and its reason names the first bad address
  (`line 99 (new) of a.txt is not in a hunk of the loaded diff`); the
  replies are prefixed `No notes added: ` / `No highlights added: `.
  Highlight offsets are code points (diffnotes' rule; the schema says
  so). `clear_diff_marks`' flags are read by `mcptools.clear_targets`:
  neither given clears both, one alone names the kind (`notes: true` the
  notes only, `notes: false` the highlights only), both false is refused
  (`CLEAR_NOTHING`) — an explicit false beside an omitted key once
  cleared nothing and answered `Cleared .`; `user` only widens the notes;
  two `clear_marks` calls so the reply (`mcptools.clear_reply`) counts
  each. The switch labels are in
  `tokensettings._MCP_TOOL_LABELS`.
- `show_image` — a local path or an `http(s)` URL, **prepared on the
  service** (PR-2.7, `SessionTools._ui_show_image`): a path is resolved
  there (`resolve_file`, the agent's cwd then the launch dir), refused
  when it is no image, and **admitted for the session** in
  `service.blobs.ImageRegistry` (by id and by handle, 512 per session,
  oldest dropped; D38) before the `tool` event carries it absolute; a URL
  is downloaded there first (`RemoteBlobs.download_then`: `remoteimages.
  fetch`, stdlib urllib, redirects to http(s) only, size and content-type
  gated, 25 MiB, into the service's `remote-images` cache; localhost is
  deliberately allowed) behind a `DeferredResult`, its failure the agent's
  words, the download's time off the forwarded call's bound. The client
  half (`ToolClient.show_image`) resolves nothing: for a path it asks the
  service whether the file is inside the tab's project
  (`ask_can_open_in_editor`, PR-2.4: only the lightbox's "Open in Editor"
  button hangs on it; a URL is never asked), then records the
  attachment and calls `lightbox.show_image(tab, key, session=…)`, which
  fetches the blob (`kind=file` / `kind=remote`) and answers "Image shown."
  once the lightbox decoded it, a failure when it didn't. With no client the
  path is resolved and admitted the same way and recorded.
- `notify_user` — routes through `MainWindow.notify_session` → the
  notification center's delivery table (card + sound in Collins, desktop
  notification away; a message to the selected tab is a read history row);
  flashes the tab and row, flags unread, and the reply tells the agent where
  it went (`notifycenter.tool_reply`).
- `attach_pr` — puts a PR the transcript never mentioned (one a subagent
  opened) on the session via `pr_store.attach`; a `/pull/` URL attaches bare
  when `gh` can't answer.
- `start_session` — spawns a sibling session into a **background tab**
  (`MainWindow.start_background_session`: no selection, focus or view change;
  terminal sized like the visible one else 120x40), from the caller's
  **project root** (`worktree_project_root(cwd) or cwd` — launching inside the
  caller's worktree broke the resolver's follow), inheriting the caller's
  transcript permission mode, model and effort (`inherited_effort` passes
  only one of `claudemodels.EFFORT_LEVELS`; the schema's enum gates an
  explicit pick), injecting the prompt unfocused, and
  answering with the new session id once `session-resolved` fires (12 s
  deadline, `process-exited` fail-fast, tab kept on failure). Spawns
  **serialize per project root** (`_start_session_chains`) so two siblings
  can't claim each other's transcript. The sibling is sandboxed whenever
  its parent is (`mcptools.sibling_sandboxed` — never an unsandboxed
  sibling from a sandboxed parent), else per the project's default
  (`window._sandbox_for_new_session`); a sandboxed parent's sibling runs
  on the parent's *launched* plan re-issued for its directory and for a
  box of its own — the service host's `derive` (the `sandbox.derive`
  request) returns (plan file, box id, reason), records the parent's launch-time grants as the sibling's
  own list (grants are a session's: nothing the parent holds live or is
  allowed later reaches the sibling, and it takes no project defaults),
  and both ride in the options (`sandbox_plan`,
  `sandbox_box`) — and a cwd
  outside that plan's workspace or grants is refused
  (`mcptools.sibling_cwd_refusal`) — one inside a directory the parent
  holds only *live* included (`derive(..., live=grants.live_paths(parent
  box))` on the service: "restart the parent session to start a sibling
  there"). Every refusal past the derive drops
  both (`toolclient._drop_sibling_box`). `bypassPermissions` is granted —
  explicit or inherited — only to a sandboxed sibling
  (`inherited_permission_mode(..., sandboxed=True)`); otherwise it is
  refused; the trust dialog becomes a refusal. See
  `collins-sandboxed-sessions`.
- Every client half is `ToolClient.<name>(found, args, sandboxed)`: the
  third argument is the service's reading of the calling session
  (`run_tool_call`'s `is_sandboxed=lambda session: session.sandboxed`,
  carried on the `tool` event), and the three
  host-reaching tools apply the sandbox policy on it — `read_terminal` and
  `run_in_terminal` through `mcptools.tool_shells` (a sandboxed session
  reaches only *Sandboxed shell* pages, opening one via
  `tab.open_panel_shell(sandboxed=True)`, never the user's own Ctrl+J
  shell), `start_session` as above.
- `read_terminal` — dumps the Ctrl+J panel shells' scrollback
  (`capture_contents`, tailed to `lines`, max 2000).
- `run_in_terminal` — types a command into an idle panel shell behind
  `shellinput.shell_command`'s line reset, opening one (quietly, `focus=False`)
  when none exists or all are busy; refuses a busy shell. `PanelTerminal.run_
  command` queues input until the pty exists. Multi-line input feeds each
  newline as Enter — `sudo` then eats the next line as its password, so
  privileged sequences must be one `a && b` line.
- The panel shell is a client view of a `shell` pty on the service:
  the handlers route through it. `capture_contents()` is
  the service's screen model of the shell's pty, `has_running_command()`
  the pty server's foreground read, `run_command` input frames to the
  service, and a shell opened for the call is spawned there (a sandboxed
  one on its box's plan).

A tool that ends or hands off its own session (an `archive_session` was
prototyped) can't land inside its own call — the reply would never reach the
shim — so it should arm and ride the busy→idle finish edge
(`service.tracking.ServiceActivity._on_finished`, judged by `service/finish.py`).

## Adding a tool

1. Append to `mcptools.TOOLS` with a tight schema and an agent-facing
   description that says when to call it. The setting key follows — and
   so does `sandbox_tool_<name>`, which is **off**: decide whether a
   sandboxed session should be offered the tool (it runs on the host,
   outside the box) and add it to `SANDBOX_DEFAULT_TOOLS` only if so.
2. Decide where it runs (§3.7): a tool a session's own data serves goes in
   `service.tools.SERVICE_TOOLS` with a `SessionTools._tool_<name>`; a
   UI-bound one gets a `ToolClient.<name>(found, args, sandboxed)` half
   and a `SessionTools._headless_<name>` for no client attached. Keep
   decisions in a GTK-free module (as `gitloads` does for `show_diff`).
3. If it opens or changes panels: `focus=False`, and a beat's delay if it runs
   from inside another cascade.
4. Add the tool to `prefslayout` if the switch group's order is pinned, to
   the README's "Tools the session itself can call" bullet, `docs/guide`, and
   the `docs/guide/how-it-works.md` token-use list.
5. An e2e check with a real `App`: either call the client half directly
   (`app.tool_client.<name>(found, args)`: `scripts/check_terminal_tools.py`,
   `check_start_session.py`) or the service's dispatcher through the probe door
   (`debug_tools_list` / `debug_tools_dispatch` / `debug_tools_result` on
   the core, served only to a service started with `COLLINS_DEBUG_API=1`,
   which every check's service is: `check_sandbox_policy.py`; a UI-bound
   tool answers a deferred id the check polls, since the check is the
   client). A tool's canned data inside the service (gh, models) is
   `scripts/e2e_stubs.py`, never a monkeypatch in the check's process: the
   service is another process. Or go
   the whole way through the socket as `check_show_diff.py` does — its
   `claude` stub spawns the real `collins.mcp_shim` from the tab's
   `--mcp-config` file (so the shim's ancestry reaches the tab and the
   pid lookup finds the caller), speaks JSON-RPC to it, and relays
   `tools/list` / `tools/call` requests the script drops as files; that
   is how a `tools/list` reflecting a switch, a schema refusal at the
   door and a `DeferredResult` crossing the socket are proven. The
   protocol itself is unit-tested with a fake service
   (`tests/test_mcpserver.py`, `test_mcp_shim.py`).
6. The acceptance pass with the real CLI (the spec's "a real session
   calling each tool"): a throwaway `App` behind the headless display
   with `HOME` moved to a scratch dir carrying *copies* of `~/.claude.json`
   and `~/.claude/.credentials.json` (delete the dir afterwards — it holds
   a token) and a scratch `~/.claude/settings.json` of `{"permissions":
   {"allow": ["mcp__collins"]}}` so no permission prompt blocks the turn;
   `trust.trust_dir(repo)` against `COLLINS_CLAUDE_CONFIG` (the copy) —
   a nested temp repository is its own project and inherits nothing;
   `win.start_background_session(repo, options=SessionOptions(model=
   "haiku"))`, `inject_prompt_unfocused` once `takes_prompt()`, then read
   the `tool_use` / `tool_result` blocks off the scratch transcript. The
   diff tools passed it on 2026-09-06 (CLI 2.1.261): seven calls, 28 s.

## The lightbox and attachments

`lightbox.py`: a singleton shade over the window's full-window overlay
(`MainWindow.lightbox_overlay`); a second `present_over` closes the first. It
takes focus onto itself and a CAPTURE-phase controller claims Esc and arrows
(gallery navigation via an injected callback, rules in
`editorfiles.gallery_step`) and swallows other keys except Tab/Enter/Space so
its buttons stay keyboardable. Zoom/pan math is `editorfiles.lightbox_zoom_
slot`; window resizes are followed via the surface's `notify::width/height`
(a `do_size_allocate` on a `Gtk.Box` is never called). Every image loads
through `animatedimage.load` (GIFs animate via `GdkPixbuf.PixbufAnimation`,
the only decoder in the stack; the frame clock stops itself when nothing
draws the paintable).

**Every picture is a blob** (PR-2.7, split-service spec §3.23). The client
opens no image file of the project's: `lightbox.show_image(parent, key,
session=…, on_shown=…)` takes a path on the service's machine or a URL,
drops `pictures`' run cache for it (a rewritten file shows anew), fetches it
(`pictures.fetch`, whose default fetcher is `blobcache.fetch_image`: `GET
/api/blob?kind=file&path=&session=` or `kind=remote&url=`) and floats the
lightbox on the landing — the newest call wins, an older landing is
dropped — or its "Couldn't display image" page with the fetch's reason.
`present_image_lightbox(parent, file)` floats a file already in the blob
cache (a PR body's picture, a diff's side). The decoders read bytes through
`blobcache.read`, which refuses any path outside the cache
(`animatedimage.load_bytes`: `Gdk.Texture.new_from_bytes`, a GIF by its
signature through a `PixbufLoader`; `pictures.thumbnail` sizes the decode
in the loader's `size-prepared`). "Open With…" hands another app the
cache's copy — the service's own file when the link is `local`.

**`kind=file`'s confinement** (`service.blobs.FileBlobs`, D38): a path
inside a root the service knows (`files.roots`), inside the asking
session's upload directory or `_pending/` (`uploads.inside`), or one the
**session's own agent** named — a show_image path in the registry, or an
image the service's transcript scan of that live session found
(`Session.transcript.attachments`) — or anything for a `local` client. The
asking session is the URL's `session` (the tab's `image_session()`: its id,
or its handle while unresolved). A client therefore reads only what a root
or that session's agent exposed, never an arbitrary path: a remote client's
show_image of a `/tmp` screenshot works because the call admitted it, while
a clicked `/tmp` reference no tool call named is refused (403, the
stand-in) — the click gate is PR-2.6's. The registry is the service's run;
after a restart an old session's `/tmp` attachments are readable again once
its transcript is scanned. Only image suffixes are served (400 otherwise),
at most 50 MiB (413), tagged `"<mtime µs>-<size>"` (304 free).

**`kind=remote`** (`RemoteBlobs`): the service's cache
(`~/.cache/collins/remote-images/<sha1 of url><suffix>`, a day) answers a
PR body's image or a gallery row, downloading on a miss (502 when the fetch
fails); show_image's `download` replaces the copy first. The client's copy
is named by the answer's content type (`blobcache.fetch(url, None)`). Any attached client can therefore have the service GET any http(s) URL,
localhost included (a body image is enough): deliberate for show_image and
acceptable for one user's service whose clients already have a shell
(§3.16); only image content types are answered. A show_image download is
bounded from the call's start (`TOOL_BOUND_S` armed before the fetch: a
dripping server answers "Timed out…", its late landing dropped), and
`remoteimages` reads with `read1` so its 10 s deadline is checked between
drips. The registry holds resolved paths only, and the GET compares the
resolved path: a link swapped in at an admitted path is refused.

`attachrecords.py` (GTK-free) is the per-session log of every image the
session put on screen — lightbox showings (with captions, which always win),
transcript mentions (`scan`: text blocks of non-sidechain, non-`isMeta`
user/assistant messages only — skill text injected as `isMeta` once produced
phantom rows), and `SendUserFile` deliveries (the one tool input scanned;
files of any kind) — persisted in `state.json` like `session_prs`, capped at
100 with **tombstones** (`hidden=True`) because a removed entry would come
back from the transcript otherwise. Sightings are dated by the message
timestamp, not the poll. `attachpanel.py` shows them oldest-top in a column
that can float over the terminal or dock as `page_kind="attachments"`;
every row's picture — a path or a URL alike — is a blob (`pictures.fetch`
with the view's `session_key()`, `kind=file` / `kind=remote`, PR-2.7), its
thumbnail decoded at display size via `pictures.thumbnail` (a
`PixbufLoader` sized on `size-prepared`, never upscaling) one row per idle
turn; activating a picture hands the record's key to the host's lightbox
(`TerminalTab._show_attachment` → `lightbox.show_image`). The file rows'
Open and Open With are for every client (D51, PR-2.8): `_with_file` hands
the launcher the file itself on the service's machine (`_with_local_file`,
a local extra, with Show in Folder, which is hidden elsewhere) and this
device's cached copy anywhere else (`_with_cached_copy`: a picture
through `fetch_image`, any other file through `blobcache.fetch_file`,
i.e. `GET /api/blob?kind=file&as=file`). The service answers `as=file`
only for a client that is not `local` and only for a file the session's
agent **named**: inside its uploads, a path a tool call registered
(`ImageRegistry`), or a delivered file's record of its live transcript
(`DeliveredFiles`, noted by `ServiceCore.session_transcript_landed` and
resolved on a thread), each by the path it resolved to **when the service
first saw it**, so a link swapped in afterwards is refused on both roads
(a request that beats the resolve is refused, and the panel asks afresh
on every open: `pictures.forget` before the fetch); a file merely inside
a root is `400` (`fs.read`
is the reader of project text), and without `as=file` the GET serves
pictures only, as before, so no decoder is ever handed anything else. A
session with no live record on the service (its tab closed) has no
transcript scan there, so its file rows answer "couldn't fetch that
file", as its pictures outside a root already did. The cached copy is
`<sha1 of the url><suffix>`, 0600, never executable; the suffix is the
only part taken from the service's path and `blobcache.file_suffix`
replaces a missing, odd or runnable one (`.desktop`, `.sh`, `.AppImage`,
`.py`, `.exe`, …) with `.bin`, and then `opens_by_default` is False and
the row's activation shows the app chooser (`set_always_ask`) instead of
a default app. The
"new images" handle badge needs both an announced-set and a moving timestamp
baseline; a lightbox showing suppresses its own echo by key
(`_attachments_beheld`). The panel docks itself once per tab when a column is
free (`dock_attachments_when_room`).

Related: `collins-terminal-tab`, `collins-panel-dock`, `collins-git-page`,
`collins-notifications-and-tray`, `collins-testing`.
