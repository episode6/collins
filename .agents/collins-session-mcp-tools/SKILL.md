---
name: collins-session-mcp-tools
description: >-
  How the Collins MCP server works — the tools every launched Claude Code
  session can call back into the app with (set_session_title, open_in_editor,
  show_diff, show_image, notify_user, attach_pr, start_session, read_terminal,
  run_in_terminal): the stdlib-only stdio shim (mcp_shim.py), the GTK-free
  tool table, validation, framing and runtime paths (mcptools.py), the Gio
  socket service (mcpserver.py), the handlers in app.py, session identity via
  the shim pid, deferred replies, the per-tool Preferences switches, plus the
  lightbox and the attachments gallery that show_image feeds. Use when adding
  or changing a session tool, debugging "Collins is not running" from an
  agent, or touching the lightbox/attachments panel.
---

# Session MCP tools

Every session Collins starts gets `--mcp-config <file>` naming one stdio
server, `python3 -m collins.mcp_shim`, so the agent sees a `collins` server in
`/mcp` with the tools the user left on. Nine tools today; each has an
on/off switch in Preferences → Built-in MCP tools (`mcp_tool_<name>`, derived
from the tool table by `mcptools.default_tool_settings()` so a new tool can't
ship without a switch). The config file itself is per app id under
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
find_tab, handlers, is_enabled)` is the validate → switch → identity → handler
skeleton, so its branching is unit-tested. Also here: `encode_message` /
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
hello or broken framing disconnects the peer, and the hello's pid **must
match `SO_PEERCRED`** or the connection is dropped — the pid is load-bearing
for authorization. `start()` refuses socket paths over 107 bytes: Gio
silently truncates longer ones and listens on the wrong path (bit a scratch
tree with a long tmpdir prefix).

## Identity and dispatch (`app.py`)

There is no session id in an MCP server's environment. `App._mcp_tab_for_pid`
walks the shim's `/proc` ancestry (`proctree.ancestor_pids`) and asks every
open tab `owns_pid_ancestors` — so a tool acts on the tab whose shell the CLI
descends from. Anything not launched from a tab (a daemon-hosted `/bg` job,
whose ancestry tops out at systemd; a closed tab) gets a clean "not from a
Collins session" error. Handlers are `App._mcp_<tool>(found, args)` returning
`(ok, text)` or a `DeferredResult`.

**Deferred replies.** The whole dispatch runs on the main loop, so a handler
that blocks freezes the window. Return `mcptools.DeferredResult` and resolve
it later from the main loop; `mcpserver` holds that connection's reply
(read → reply → read, stretched). Budget well under the shim's 15 s
(`remoteimages` caps at 10 s / 25 MB), **always** resolve — even on an
unexpected exception — or the connection goes silent forever, and expect the
late reply to land on a gone connection (`_send` no-ops). A reply larger than
`MAX_LINE` doesn't degrade, it closes the connection; `terminal_reply` halves
tails until the JSON-encoded size fits with a 16 KiB margin.

## The tools

- `set_session_title` — writes Collins' manual name slot (beats generated and
  CLI titles).
- `open_in_editor` — resolves a path against the tab's live cwd and editor
  root (`_mcp_resolve_file`), opens it at a line (`window.open_in_tab_editor`,
  which honours the pop-out rule).
- `show_diff` — offered only while hunk is installed (probed at most every
  30 s, `App._refresh_hunk_probe`); opens the git page quietly on the working
  tree / index / branch / a commit and navigates to a file and line via
  `hunk session navigate` (`app._ShowDiff` polls up to 12 s for the session
  id). Decisions in `hunkctl.show_diff_load` / `diff_file_path` /
  `show_diff_reply`.
- `show_image` — a local path or an `http(s)` URL: URLs are fetched on a
  worker thread (`remoteimages.py`, stdlib urllib, redirects to http(s) only,
  size and content-type gated, into the pruned cache dir; localhost is
  deliberately allowed) behind a `DeferredResult`, then shown in the lightbox
  with an optional caption, and recorded as an attachment.
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
  transcript permission mode and model, injecting the prompt unfocused, and
  answering with the new session id once `session-resolved` fires (12 s
  deadline, `process-exited` fail-fast, tab kept on failure). Spawns
  **serialize per project root** (`_start_session_chains`) so two siblings
  can't claim each other's transcript. `bypassPermissions` is refused; the
  trust dialog becomes a refusal.
- `read_terminal` — dumps the Ctrl+J panel shells' scrollback
  (`capture_contents`, tailed to `lines`, max 2000).
- `run_in_terminal` — types a command into an idle panel shell behind
  `shellinput.shell_command`'s line reset, opening one (quietly, `focus=False`)
  when none exists or all are busy; refuses a busy shell. `PanelTerminal.run_
  command` queues input until the pty exists. Multi-line input feeds each
  newline as Enter — `sudo` then eats the next line as its password, so
  privileged sequences must be one `a && b` line.

A tool that ends or hands off its own session (an `archive_session` was
prototyped) can't land inside its own call — the reply would never reach the
shim — so it should arm and ride the busy→idle finish edge
(`MainWindow._on_session_finished`).

## Adding a tool

1. Append to `mcptools.TOOLS` with a tight schema and an agent-facing
   description that says when to call it. The setting key follows.
2. Add `App._mcp_<name>` and register it in `_mcp_dispatch`'s handler map;
   keep decisions in a GTK-free module (as `hunkctl` does for `show_diff`).
3. If it opens or changes panels: `focus=False`, and a beat's delay if it runs
   from inside another cascade.
4. Add the tool to `prefslayout` if the switch group's order is pinned, to
   the README's "Tools the session itself can call" bullet, `docs/guide`, and
   the `docs/guide/how-it-works.md` token-use list.
5. An e2e check with a real `App` and a fake shim connection (see
   `scripts/check_terminal_tools.py`, `check_start_session.py`,
   `check_show_diff.py`); the protocol itself is unit-tested with a fake
   service (`tests/test_mcpserver.py`, `test_mcp_shim.py`).

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
thumbnails decode at display size via `pictures.thumbnail`
(`Pixbuf.new_from_file_at_scale`, never upscaling) one row per idle turn. The
"new images" handle badge needs both an announced-set and a moving timestamp
baseline; a lightbox showing suppresses its own echo by key
(`_attachments_beheld`). The panel docks itself once per tab when a column is
free (`dock_attachments_when_room`).

Related: `collins-terminal-tab`, `collins-panel-dock`, `collins-git-page`,
`collins-notifications-and-tray`, `collins-testing`.
