---
name: collins-terminal-tab
description: >-
  How a Collins session tab works: TerminalTab in terminal.py (the VTE running
  the user's shell with claude typed in), spawn/resume/attach, the transcript
  resolver that binds a fresh tab to its session id, the close flows in
  window.py (graceful Ctrl+C Ctrl+C, /bg handoff, hide, force-close budgets),
  reading and writing the CLI's input box (takes_prompt, entered_prompt,
  inject_prompt, model/effort switching), the footer (cwd, branch, model, PR
  chips), clickable links and wrapped-link stitching, easy copy & paste, the
  env spoof for progress reports, and tab ordering. Use when changing anything
  about the agent terminal, tab open/close/quit behaviour, typing into or
  reading the CLI, or a footer element.
---

# The session tab

`TerminalTab(Gtk.Box)` in `collins/terminal.py` is the biggest widget in the
app (~6k lines with its helpers); `MainWindow` in `window.py` owns tab
lifecycle. `PanelTerminal` (same file) is the plain-shell page used in the
panel dock. GTK-free helpers around them: `shellinput.py` (commands typed into
a shell), `linkpatterns.py` / `transcriptlinks.py` (what counts as a link),
`transcript.py` (tailing the JSONL for touched files, PRs, model, permission
mode, attachments), `vtehtml.py` (reading dim text back out of VTE),
`proctree.py` (`/proc` walks), `taborder.py` (tabs follow the sidebar order).

## `Session` and its ports (where the logic lives)

Collins is two processes (spec `~/specs/collins/split-service-and-client.md`,
§3.1, §3.5, §3.19): `collins-service` owns the ptys and every session, the
window is a client of it. The tab is a widget around a **`Session`**
(`collins/service/session.py`, GTK-free: GLib/Gio only) that lives **on the
service**, one per live agent pty in `ServiceCore.sessions`; `tab.session` is
its mirror (`clientsession.ClientSession`, see "The session on the service").
Most of the methods this skill names are the session's, not the tab's:

| Lives in `Session` | Stays in `TerminalTab` / `MainWindow` |
| --- | --- |
| identity: `session_id`, `fork`, `provider`, `options`, `continue_recent`, `cwd` | widgets, overlays, dialogs, the footer, the dock, the editor |
| launch: `spawn`, `_finish_spawn`, `_launch_command`, `agent_environment` (was `_agent_tab_environment`), the sandbox plan (`_sandbox_options`, `_reserve_worktree`, `_register/_unregister_sandbox_box`, `_release_sandbox_plan`, `shell_exited`), `_check_worktree_launch` / `relaunch_without_worktree`, `restart_sandboxed` and its poll, the new-chat prompt poll, a reaped worktree put back (`_recreated`) | the `spawn` request that builds the session (`TerminalTab._service_spawn`) and the attach |
| reads: `takes_prompt`, `entered_prompt`, `prompt_block`, `unstarted_thread` (minus the new-chat screen), `visible_screen_text`, `worktree_exit_prompt_keystrokes`, `screen_first_column`, `mention_leading_space` (Add to chat and a drop's tokens: `mention`, and `write` with `mention: true`) | — |
| writes: `inject_prompt(_unfocused)`, `switch_model/effort`, the open-cut (`begin_cut` → settle → apply → verify), `send_composed`, `restore_draft` (paste-back), `foreign_paste_in_box` | the composer widget, its open/close/dock, the stash (`_stash_draft`) |
| `/proc`: `candidate_pids`, `agent_is_running`, `agent_pid`, `has_running_command`, `current_agent_cwd`, `has_background_descendant`, `owns_pid_ancestors` | — |
| transcript: the `TranscriptModel`, its monitor/poll/debounce, `request_update` and its landing, the PRs it tracks (`tracked_prs`, `restored_prs`, `attached_prs`), the resolver (`start_resolver`, `arm_resolver`), `current_model/effort/permission_mode`, `finish_witness` | chips, labels, attachments, the editor's agent files (`_on_transcript_landed`, `_on_transcript_reset`) |
| activity: `echo_gate`, `spinner`, `progress`, `finish_ledger`, `redraw_counts` | the tracker itself is the service's (`service/tracking.py`); the window keeps "the person typed here" (`unread: false`) and attention |
| the cwd poll (`start_cwd_poll`, mapped-only) and `settle_cwd` (the follow debounce) | what following means (`_maybe_follow_editor`, the shells' offer) |
| the close's keystrokes and polls: `begin_close`, the nudges, the worktree "keep", the shell's exit, the force budgets (`CLOSE_POLL_MS`, `EXIT_*`, `BG_*`, `SHELL_EXIT_TICKS`), `end_close`, `nudge_exit` | every decision about a close (`_graceful_close`, the asks, `_close_confirmed` passed in as the force) |

Constants moved with their code and lost their underscore
(`PROMPT_SUBMIT_MS`, `CUT_*`, `RESTART_*`, `WORKTREE_LAUNCH_*`,
`NEW_CHAT_*`, `CWD_POLL_MS`, `EDITOR_FOLLOW_TICKS`, `MAX_PR_CHIPS`, ...);
`terminal._bracketed_paste` and `terminal._agent_tab_environment` stay as
aliases.

**The ports** (`collins/service/ports.py`, `typing.Protocol`s):
`PtyPort` — `write`, `resize`, `child_pid`, `foreground_pgrp`;
`ScreenPort` — `rows`, `cursor`, `columns`, `tail_is_faint`,
`first_column`, `capture_contents`, plus `row_count` (the echo gate's grid),
`row_text` (`takes_prompt`'s one line read on every key press) and
`visible_text` (the worktree matchers that read soft-wrapped lines whole).
Row indices are relative to the screen's cursor-anchored first row. The
implementations are `hosting.RecordPtyPort` / `RecordScreenPort`, over the
service's `PtyServer` pty and `termscreen.Screen`; the unit tests'
`FakeTerminal` is the third.

**How the tab talks to it.** Down: every old name on the tab is a forwarder
(`tab.takes_prompt()`, `tab.session_id`, `tab._options`, `tab.sandboxed`,
`tab.current_agent_cwd()`, ...) to the mirror, which answers from the last
`session` event (no round trip: D28) or makes a request. The privates the
e2e checks read (`_initial_command`, `_visible_screen_text()`,
`_pasted_back`, `_child_pid`, `finish_ledger`, `_transcript.set_path`) are
the **probe**: `tab.probe("initial_command")`, `tab.probe_call(
"visible_screen_text")`, `tab.probe_set("session_id", x)`,
`tab.probe_call("transcript.set_path", p)`, which are `debug.*` requests the
service serves **only when it runs with `COLLINS_DEBUG_API=1`** (otherwise
`unknown`). Never use it in product code. Up: the session reports through a
`SessionHost` listener (`alive`, `mapped`, `paint`, `focus_terminal`,
`spawn_shell`, `session_resolved`, `transcript_landed`, `process_exited`,
...) which `hosting.SessionRecord` implements as `session`, `cut`,
`composer`, `focus.terminal`, `shells` and `close` events to the pty's
attached clients; the tab turns them into its signals (`focus` stays the
client's own event, `focused` required). A composer's open-cut reaches the
composer through a `CutSink` (`hosting.CutSink`: `alive` is "the client is
still attached and hasn't called the cut off"; `seed`, `refuse` and, for a
cut that ends with nothing to seed, `ended` are `cut` events by handle,
`seeded` / `refused` / `cancelled`; a seeded cut's handle is let go of at
once, and `cut.cancel` reaches only the asking client's cuts). The tab's
decisions on those events are `ClientSession.cut_action` (seed an open
composer, `_restore_or_stash` for one that closed meanwhile, lower one whose
box can't be read) and `ClientSession.close_action` (`budget` → the forced
close), GTK-free and tested in `tests/test_clientsession.py`. `input_sent`
is the one host call that never reaches a client: every write the session
makes (`Session.write_text`: an injected prompt, a switch, a close flow's
keys) is announced *before* the bytes go to the pty and never passes a
client's VTE, so it never arrives as an input frame. The session pokes and
arms its own `echo_gate` in `write_text`, and `SessionRecord.input_sent`
hands the text to `ServiceActivity.input_sent`, which takes the process
baseline's last pristine snapshot on a "\r", as `on_input` does for a typed
Enter. Without it a fresh spawn whose turns were only ever sent that way
sits in its startup hold for good (no pole, no unread flag, no finished
notification); `scripts/check_injected_prompt_pole.py` drives the road end
to end through the probe (`activity.startup_held_for`, `echo_gate.armed`).

**Testing a state machine.** `tests/test_session.py` builds a `Session`
over fakes: a `FakeTerminal` that is both ports and models just enough of
the CLI's input box (`❯`+NBSP, typed text, DEL, bracketed pastes and their
fold into `[Pasted text #N]`, Enter, echo lag), a `FakeHost` recorder, and
a `FakeScheduler` whose clock `advance(ms)` winds by hand (GLib's
repeat-while-True contract, ties in insertion order, background work run
inline, a `monitor_file` the test can fire). `/proc` is faked by
monkeypatching `session.proctree.agent_descendant_cwd` (agent running or
not) and `os.getpgid`. New launch, cut, switch, close or resolver behaviour
gets its test there — fast, no display — and its e2e check only when
something about VTE or a real CLI is the point.

## Spawn, resume, attach

**Where a spawn lands is the service's to say (D39, PR-2.8).** The tab
and its panel shells never ask this device's disk whether a cwd is a
directory: `TerminalTab.__init__` roots the editor at the cwd it was
handed, `_service_spawn` sends that cwd, and the service applies the
fallback: `Session._finish_spawn` for an agent (the repository of a
worktree that is gone, else the service's `$HOME`, with its painted
warning), `core.spawn_cwd` for a shell (a directory, else `$HOME`). The
`spawn` reply's `cwd`, the pty's row and `pty` event, and `pty.info`'s
`cwd` all say where the pty started. `_land_where_the_service_says`
re-roots the editor there when it differs from what was asked (the
editor's `root-changed` moves `link_root` and the bare-name links);
`_land_where_the_pty_started` does the same for an attach, after an
`fs.stat` says the tab's own root is no directory. `tab._cwd` is the
session mirror's `cwd`, which follows the service's `session` event. A
new path check in the tab is an `fs.stat` off the main loop
(`remotefiles.off_main`), never `Path.is_dir`: `terminal.py` has no site
left in the pathless walker, and `tests/test_client_is_pathless.py` pins
that.

A **sandboxed** launch (`SessionOptions.sandbox`, all of it on the service) types
`python3 <…>/collins/sandboxrun.py <plan> -- claude …` instead:
`_launch_command` (from `_finish_spawn`, and again from the chip's
*Restart to apply*, `restart_sandboxed`) writes the plan through
the service host's `prepare_launch(cwd, box)` at the last moment (the
workspace is the settled cwd, a recreated worktree included; the box is
the one the options name — a resumed session's, a fork's — else the one
this tab already launched in, else minted by the host's `mint_box(cwd)`,
seeded with the project's default grants unless this is a `--continue`
launch, whose box is settled when it resolves) — or adopts the plan and box
the options already carry (a sibling's derived ones) — `Provider.
sandbox_prefix` prepends the wrapper (and the session appends
`provider.session_flags` — the permission mode — behind a `--continue`),
`_register_sandbox_box` tells the live grants
(the service's, `core.sandbox_grants`) the box is up once the plan is settled,
`_unregister_sandbox_box(then)` has what was mounted into it unmounted on
the grants' worker and calls `then` when it is (the restart's relaunch,
`_relaunch_sandboxed`, and the exit's clean-up both hang off it — the
main loop never waits for an unmount), `_release_sandbox_plan`
unlinks the plan and releases the
box's lease (before a restart's rebuild and in `_on_child_exited`, which
also asks for the box to be forgotten, on the main loop — a no-op for
one a session names, and otherwise the end of the box and of the grants
recorded for it), `tab.sandboxed` is what
the `/bg` and attach guards read, `tab.sandbox_plan_path` what the
footer's `sandboxchip.SandboxChip` and a sandboxed `PanelTerminal` read,
and `tab.sandbox_box` what the window records against the session id on
`session-resolved` / `fork-resolved`;
a sandboxed fork tab runs the resolver in `_fork_resolve` mode and reports
the forked id on `fork-resolved`. No box possible → an unsandboxed launch
that says so, with a bypass mode dropped. A `"toast"` signal carries a
tab's short message to the window's toast overlay. See
`collins-sandboxed-sessions`.

A tab asks the service to spawn the user's `$SHELL` (a `spawn` request, see
"The tab on the pty server") in the session's resume cwd — the **last** cwd its transcript recorded, mapped back
through worktree recovery; the service's scan works it out and the row
carries it (`Session.resume_at`, the `item`'s `resume_cwd`: PR-3.0 item 3,
the client reads no transcript for it) — with the environment from
`agent_environment()` (`service/session.py`; the app's env plus `ConEmuANSI=ON` and
`TERM_PROGRAM=kitty`, which are the two spoofs that make the CLI emit
ST-terminated OSC 9;4 progress that VTE parses; they fail soft). Then the
session on the service types the provider's command, **assembled there**
(PR-3.0 item 1, D54 (1): no client sends a command; `command_override` is
gone from the `spawn` request and the identity, and the window's
*Continue last session* asks for `continue_recent`): `claude --resume <id>`
(or `--fork-session`, `--continue`, or a fresh `claude` with
`--mcp-config`, `--model`, `--effort`, `-w`), or `claude attach <job-id>`
when `background_agents(include_finished=True)` lists the session — the
daemon refuses a plain resume for any id it still lists. The `claude` it
types is the service's own lookup on its session environment's PATH
(`Provider.cli_path` → `clisetup.which`; `collins-service` installs that
PATH as `clisetup`'s search base at start, PR-3.0 item 10), never this
device's. Tests stage a `claude` on the PATH instead of typing a command
(`tests/test_core.py`'s `stage_cli`). `attach` is not exclusive: a second client can attach to the same
job, which is how live sessions are probed without typing into them.

The VTE's `set_size(cols, rows)` before the `spawn` request is the grid the
pty starts at, even for a never-shown tab (background spawns mirror the
visible terminal or use 120x40). A never-selected tab never realizes; VTE still emits `bell` for it
but rings no audible bell.

**The transcript resolver.** A fresh tab has no session id until the CLI
writes the transcript after the first prompt. `_start_transcript_resolver`
polls every 1.5 s for a **new** transcript in the launch cwd, baselining the
ones that already exist (a `--continue` tab instead adopts the newest). It
follows the agent into a `claude -w` worktree when both sides share a project
root (`sessions.worktree_shares_project`) and baselines that worktree's
transcripts **only if they predate the first arm time** — a fast `-w` writes
the session's own transcript within a second of creating the worktree, so a
mtime-less baseline swallowed it. Unmapped tabs pause after ~120 attempts and
resume on `map`. Resolution fires `session-resolved`, which the window uses to
replace the sidebar placeholder, re-key notifications, adopt saved PRs and
attachments. Two unresolved tabs in one cwd race for the same transcript;
callers spawning several sessions serialize per project root. The path is
resolved once; the CLI moving the transcript on worktree entry is a known,
unfixed staleness.

## Reading and writing the CLI's box

Everything here is probed CLI behaviour (2.1.2xx), encoded in
`ClaudeProvider`:

- The cursor line at rest is `❯` + **U+00A0** with the cursor at column 2;
  `takes_prompt()` means "a keystroke would land in an empty input box" and is
  **True for the whole of a turn** while the agent streams above it — it never
  means idle. Use `_agent_is_running()` / `has_running_command()` for busy.
- Ghost text (the CLI's suggestion, `Try "…"`) is drawn **dim** (SGR 2). VTE
  exposes dim only through `get_text_range_format(HTML)` and only for the run
  the range *starts* on; `vtehtml.py` reads it starting at the cursor and
  judges dim against `themes.terminal_foreground` (fg × 2/3 per channel).
- `entered_prompt()` reads the box by its grammar (`─` rules above and below,
  `❯`+NBSP first row, two-space continuations, width `columns - 4`); wraps vs
  typed breaks are told apart by trailing space cells and row fullness. A box
  taller than the screen scrolls internally with no on-screen tell.
- VTE range reads: the end column is exclusive, columns are cells (CJK = 2,
  combining = 0; `dropimages.cell_width`), trailing typed spaces are kept,
  reads stop at the last written cell, and a `feed()` is not parsed until the
  main loop runs.
- Clearing the box: Down × rows-below, Ctrl+E, one backspace per character,
  in one write (`clear_prompt_keys`) — never Esc Esc, which interrupts a turn.
- `inject_prompt(text)` types the text and sends the `\r` in a **second
  write a beat later** (`PROMPT_SUBMIT_MS`): a Return inside a chunk the CLI
  reads as a paste is a newline, not a submit. `inject_prompt_unfocused`
  wraps multi-line text in a bracketed paste (`_bracketed_paste`, stripping
  `\r` and any paste-end marker). A paste over 800 chars or more than two line
  breaks becomes a `[Pasted text #N +M lines]` stand-in in the box.
- `switch_model` / `switch_effort` post `/model <id>` / `/effort <level>`
  through `_post_switch`; the typed `/model` form **also saves the user's
  default** to `~/.claude/settings.json`. The footer's model and effort come
  from the transcript (`transcript.TranscriptModel.model()` / `.effort()`),
  never from settings. A switch shows up as soon as the CLI confirms it, not
  at the next reply: the CLI writes a local slash command as a user line
  wrapped in `<command-name>` / `<command-args>` and its output as a second
  user line starting `<local-command-stdout>`; `_record_switch` arms on the
  first and commits on the second only when it reads as the confirmation
  (`Set effort level to <level>` — the level comes off the text, so the
  CLI's own picker counts too; `Set model to <display name>` — the model
  comes off the command's args, so a bare `/model` picked in the CLI waits
  for the next reply's `message.model`, which also replaces an alias with
  the resolved id). A refused switch prints something else and moves
  nothing.
- `feed_message()` paints into VTE directly (not into the pty): the CLI's box
  reads as unreadable until it redraws.
- `add_file_to_chat` types `@path#L2-4` (`Provider.file_reference`; whole lines
  only) with `dropimages.leading_space` deciding whether a space is needed.
  It refuses without an agent in the terminal, except on the new-chat
  screen, where the mention lands in the screen's composer instead.

## Closing a tab

`_on_close_page` is the single entry: pages in `_close_ok` are blanket consent
(the header Exit/Background buttons and sidebar row buttons pre-add there — the
click is the confirmation), everything else asks via `_ask_tab_close` (busy
agent → confirm / exit / background, honouring `archive_running_session`),
then `_ask_editor_then_tab_close` for dirty editor buffers (Save / Don't
Save / Cancel; Cancel aborts the whole action), plus the busy-panel-shell ask.
An **unstarted thread** (resolver armed, no id, no `--continue`, empty box)
closes without asking. Any new discard-on-close state must be gated at all
entry points: `_close_tab_direct`, `_ask_editor_then_tab_close`,
`_begin_quit_flow`.

`_graceful_close` decides on the provider's exit (`\x03\x03`) or `/bg\r`,
hands the screen to a neighbouring tab, and starts
`tab.session.begin_close`, which feeds it and polls (`_poll_close`, until
the window's final close calls `end_close`): it re-nudges at
`EXIT_NUDGE_TICKS`/`BG_NUDGE_TICKS` (a mid-turn agent spends the first
Ctrl+C Ctrl+C interrupting itself), answers the CLI's "keep or remove this
worktree?" dialog with keep (`worktree_exit_prompt_keystrokes`), and
force-closes at the tick budget (`SessionRecord.close_budget`: a `close`
event, `state: budget`, to the attached clients, which the window answers
with `_close_confirmed`; with no client attached — the tab detached
mid-drain, a quit insisted on, a client crash — the service forces it
itself, `ServiceCore.force_close`, and a last detach after the event went
out does the same (`client_detached`), so a session the user asked to
stop ends whether anyone watches or not). Once the CLI is gone `_poll_shell_exit` feeds `shellinput.shell_command("exit\r")` — the `" \x15"` line reset first,
because a shell inherits input the CLI never read (VTE mouse reports), and a
bare kill-line at column 0 rings the bell — and force-closes if the shell
ignores it. Keys fed to the *CLI* get no reset (raw-mode TUI). A `/bg` close
(`close {mode: background}`) has the service mark the row `backgrounding`
(yellow, disabled) until the daemon lists the job or a timeout, and its fork
watch (`service/bgagents.py`) handles older CLIs that fork.

**Detach** (spec §3.21, D12): `win.detach-session` (the tab
menu's item targets "" = the menu's page; the row menu's names the
session, routed to its owner window) → `MainWindow.detach_page` → the
editor's Save Changes? only → the page goes into `_detached_pages` and
`close_page` → `_on_close_page` skips every ask and the graceful close and
calls `TerminalTab.detach_pty()` (a `detach`, the client closed) instead of
`release_pty()` (`detach_page` refuses a page mid-close, `_page_settling`,
and closes a tab whose CLI has exited instead; the row menu asks
`can_detach_session`); the panel shells still end (their history written by the
service). On the service, a shell alone nobody shows is closed
(`SessionRecord.close_if_orphan_shell`, from `client_detached` on the last
`detach` / `client_gone` and from `cli_changed(False)`): the client's
`cli_exited()` cannot tell a CLI that never came up (no `claude`, a bad
`claude_cli_path`, a failed sandbox launch) from one still starting, so a
plain Quit detaches such a tab, and a session detached while its CLI runs
is left a bare shell when the CLI quits by itself; neither has a running
row or anything that would attach to it, so the service ends it rather
than keep it for its life. A pty mid-close is left to its close flow.
**The startup hold:** `has_running_command` is the pty's foreground group,
the shell's own until its rc has run and it forks the typed command (bash
or zsh with a heavy rc: a second or more), so a pty whose CLI has never
been seen running (`SessionRecord.cli_ever_ran`, set by `cli_changed(True)`)
is never closed for "no CLI" inside `hosting.ORPHAN_HOLD_S` (30 s) of its
`spawned_at`: one re-check is scheduled for the hold's end with the same
guards (nobody shows it, CLI not running, not closing, not exited), an
attach in the meantime cancels it, and a CLI seen running and then gone
closes at once. A pty whose master is already gone (`pty.master < 0`) is
not forced twice. The one window left: an attach aimed at a pty being
closed this way is refused and falls back to a resume. The tab's `destroy` also detaches (no longer `release_pty`): a
window that closes for real leaves its sessions running. **Opening a
running session attaches** (D31): `open_session` asks `store.pty_for(id)`
and, while that pty's CLI still runs (`pty.info`'s `running_command`; a shell-only pty is resumed over, which the service takes by closing it), builds `TerminalTab(attach_pty=…)`, which reads the pty's grid
(`pty.info` `cols`/`rows`, so the attach paints at the pty's own size and
the model, which never reflows, cuts nothing), claims the pty and attaches;
the `session` snapshot fills the mirror. `attach_refused` (the pty ended
meanwhile) falls back to a resume. `open_running_pty(pty)` does the same
for a session with no id yet (a running "New session" row, a `pty:<id>`
entry of `open_tabs`), with a placeholder row like a fresh tab's.

Quitting: `_on_close_request` → `_begin_quit_flow` → `_confirm_quit`, by
`quit_with_running_sessions`: **detach** (the default since PR-1.12c, D30:
`do_detach` records `open_tabs` with `_close_for_good`, marks every tab
`quitrules.tab_fate` says `DETACH` as `_detached_pages`, the ones it says
`RELEASE` — a shell left after its CLI, an unstarted New Thread, which has
nothing to keep — as `_confirmed_closes`, and closes all pages with
`_close_ok`, so a page mid-close drains and the rest detach or end, and
the last page's close reissues the window's; a non-last window's tabs are
kept by `App.keep_open_tabs`; the nothing-busy fast path of
`_on_close_request` releases the `RELEASE` tabs the same way; `quitrules`
is GTK-free and reads `TerminalTab.cli_exited()` — the CLI once seen up
and gone, or the pty exited — not `has_running_command()`, which is False
for the first second after a spawn or an attach),
**exit** (*Stop Sessions and Quit*: close all with `_close_ok`, as quitting
always did; with nothing busy the fast path releases each pty), background
(the /bg queue), **hide** (the window hides, sessions keep running, the
status icon brings it back; `request_quit` bypasses hide) or **ask**: the
dialog (`dialogs.answer_dialog`) "N sessions keep running in the Collins
service" with *Quit*, *Stop Sessions and Quit*, *Keep Running (Hide
Window)*; its first showing adds the status-icon sentence
(`quit_notice_shown`). The words are `quitrules.quit_heading` /
`quit_body` (GTK-free, tested): N is `_agent_busy_count` (agents owning
their terminals; `_busy_tab_count`, which gates the dialog, also counts a
tab busy only in its panel), and with a command running in a terminal
panel (`_panel_busy`) the body says Quit and Stop Sessions both end it
with its tab, only Keep Running keeps it — the panel shells go through
`release_panel_ptys` on every close but a hide. An install whose stored value was `ask` moves to
`detach` once (`state.migrate_device_settings`, marked by
`quit_detach_migrated`). `_close_for_good` writes `open_tabs` one last time
(with this window's tabs when it is the last window) and freezes it.
`_close_ok` also covers the no-dialog paths. A hidden window keeps every
page alive with no `Gio.Application.hold()`.

**open_tabs** (§3.21; this device's, in `ui-state.json`):
`App.persist_open_tabs` (debounced 250 ms) writes every open window's
`open_tab_entries()` — a tab with a live pty whose CLI is up or still
coming up (`quitrules.records_as_open`: not a shell left after its CLI,
not an unstarted New Thread, not a page mid-close), as its
session id or `pty:<id>` for an unresolved one or a fork — through
`AppState.set_open_tabs` into the per-service block of `ui-state.json`, on
every page attached, detached, reordered and resolved. It is frozen while
the link is down (tabs whose ptys went with the service close as their
reattach fails; that must not shrink the record) and after the last
window's quit. `MainWindow.reopen_tabs()` (the launch, from
`do_activate`) and `App._on_connected` after a reconnect (after
`reattach_tabs`: every tab re-`attach`es its pty for a redraw, and one whose
pty is gone is marked ended and offers resume) reopen each entry not shown: attached when the service
runs it, resumed when not (a `pty:` entry that no longer runs is dropped);
a session the store has not scanned yet waits for its first refresh. With
`open_tabs` empty the launch falls back to `restore_last_session`.

**Restart service** (`win.restart-service`, the main menu):
`MainWindow._ask_restart_service` reads `service.status` (`busy`, `pid`),
the mirror's agent ptys and the rows whose `background` is set for the
blast radius (`quitrules.restart_body`: the foreground sessions end, the
`/bg` agents survive — the CLI's daemon is not the service's, D52's
`KillMode=process`), then `App.restart_service("now" | "idle", pid)`. *Now* flushes and freezes
`open_tabs` and tells the connection manager to wait for that pid to be
gone (`ConnectionManager.expect_restart`) before it starts another
service. *Idle* is `ServiceCore.restart_when_idle` (polls
`activity.busy_count()` every `RESTART_POLL_MS`, 2 s; `service.restart
{when: cancel}` → `cancel_restart`), shown as "Restarting when idle" with
Cancel in the reconnect banner (`MainWindow.set_restart_pending`). The
service's stop now ends its clients **before** its sessions
(`Service.stop`), so a client sees the link go, not one `pty-exited` per
session. *Restart Service* is offered only for an older service
(`mismatch.service < mismatch.client`). A `hello` refused `protocol`
raises `api.client.ProtocolMismatch`:
at startup `App._mismatch_at_startup` asks over a bare window on a nested
loop (*Restart Service*: `connection.stop_service` SIGTERMs the socket's
peer, found by `SO_PEERCRED`, then `start_local` again; *Quit*); on a
reconnect the manager goes to `mismatch` and `App._on_protocol_mismatch`
asks over the window.

## Footer and chrome

**The footer's ways out of Collins are local extras** (§3.12, PR-2.8):
the file manager button (`_files_btn`), the app launchers
(`_set_footer_apps`) and the panel toggle's right-click with its tooltip
line are built only when `apilink.is_local()` (which is `app.local`: the
link's `local` proof, D11), and their handlers return early too. The
rule for a new one is in Footguns.

`_build_footer`: the live cwd (2 s poll of `/proc/<pid>/cwd` down the
`_candidate_pids` chain, worktree-aware; click copies), the git branch
(`gitinfo.current_branch`, no subprocess; click opens the git page), model
and effort chips (`modelmenu` MenuButtons), PR chips (`PrChipRow` measures
overflow into an ellipsis menu), the composer/attachments/terminal/git/editor
buttons and footer apps. The git button (`_git_toggle_btn`, `gitpage.ICON`,
`win.toggle-git`) sits between the terminal and editor toggles and is
**greyed outside a repository rather than hidden**, re-checked with the
branch on the 2 s tick — a button that comes and goes is one nobody learns
the place of. The cwd tick also drives `_maybe_follow_editor` (the editor
and panel shells follow a worktree hop) and the git page's freshness check.

Links (`_setup_links`): VTE regex matches for URLs and path-shaped text
(`linkpatterns`; bare filenames only via a per-root alternation of names that
exist, `_RootNameLinks`). A path hit is a *candidate* resolved against the
filesystem at click time. Hard-wrapped links are stitched from screen
geometry (`_resolve_wrapped_at`, gated on row fullness; the two directions
have different guards) and, when geometry declines, from the transcript
(`transcriptlinks`, finished turns only, never producing text the screen
doesn't show). Read the **visible screen** (`get_text_format`) indexed by
screen row — the CLI's repaint renderer leaves VTE's ring a page away from
the adjustment, so adjustment-derived rows read empty.

**Links over the API (PR-2.6; every path is the service's, the click opens
no file).** `on_pressed` reads what needs the widget on the main loop (the
match under the pointer, `_screen_at`'s snapshot of the rows, the roots, the
tab's session id), claims the gesture when there is a candidate under the
pointer, and hands the rest to `remotefiles.off_main(_click_target ...)`: the
resolution is a pure function over the snapshot (`_click_target`, with
`_resolve_wrapped_at` / `_resolve_wrapped_url_at` / `_resolve_from_transcript_at`
taking the snapshot, never the terminal), whose only side reads are an
`exists` predicate (`linkpatterns.resolve_path` / `resolve_file_reference` /
`resolve_wrapped_reference` take it as a required argument and return the
path it answers with; the click passes `remotefiles.locate`, an `fs.stat`
remembered for the click, and the attachment log passes
`attachrecords.here`, its own disk. **A `~` path is handed to it as
written** (`linkpatterns.home_led`): the service's `fs.stat` expands it
with the service's home and answers the absolute path, PR-3.0 item 4; no
client expands a `~`) and `links_of` (the transcript's
links, asked once and only when a click gets that far). The answer lands at
`PRIORITY_DEFAULT` in `_on_click_resolved`, which opens a URL or
`_open_file_reference`: one `fs.stat` of the path with the tab's editor root
(its `kind` says file or not, its `inside` says editor or default app, and an
image goes to the lightbox with that `inside` as its Open-in-Editor
decision). **Because the answer is asynchronous the gesture is claimed on a
candidate, resolved or not**: a click on a match that resolves nowhere is
swallowed where it used to fall through to the terminal; a click on no
candidate (the token under the pointer stands in) is not claimed and opens
on the reply. **The default app is a local extra** (§3.12, PR-2.8):
`_launch_default` (a directory, a file outside the project) and
`_launch_local_uri` return at once unless `apilink.is_local()`.
`_launch_uri` sends an http, https or mailto link to `_launch_web` (any
client: a web or mail address names nothing of the service's machine)
and **every other scheme** to `_launch_local_uri` (a `file:` URI with no
path the click could open, `ssh:`, an editor's `vscode:`, a desktop's
own handlers: PR-3.0 item 8), by one GTK-free rule,
`linkpatterns.scheme_is_remote_safe` (the scheme lower-cased, never a
prefix test: `FILE:///x` is a file URI to GIO). The editor and the
lightbox are not local extras and open for every client. The transcript is the
service's file:
`transcriptlinks.fetch(session)` is `store.transcript-tail`, answered by
`service/transcripttail.py` (the last 2 MiB parsed once per size and mtime,
bounded by `protocol.TRANSCRIPT_LINKS_*` in `bound`, the bytes measured as
the frame writes them with `protocol.json_bytes`, the path off the store's
row and checked by `transcript_path_allowed`); the client never opens it.
**`bound` drops every link UTF-8 cannot encode**: the reply is validated
whole, so one lone surrogate (the CLI cuts tool output by UTF-16 unit, and
both link patterns take a surrogate as part of a token) would otherwise
cost the session every link of its tail until the tail moved past it; a
reply's list is fitted per item on the service, never left to the
validator. `fs.names` holds the same rule for a name that is not UTF-8 on
disk (`names_reply`, as `projectfiles.list_entries` for `fs.list`), and
`transcriptlinks.completions` (pure) still decides what the screen
corroborates. `_RootNameLinks` asks `fs.names {root}` (non-directory names,
5000 at most, `names` a `protocol.CHUNKED_JSON_FIELDS` so a full bound of long
names travels as blob frames, confined on the worker like `fs.walk`, off the
main loop; the answer is dropped when the root moved or
a newer ask overtook it, kept when the service cannot answer) and holds a
`fs.watch {kind: dir}` on the root (`self._monitor` is its handle) whose
`dir-changed` re-asks through the same 500 ms leading-edge throttle. A root
the service refused (`refused`: outside every root it knows, a remote
client's tab whose session has no row yet) is asked about again, names and
watch (`_rebuild`), when a `rows` event lists a session there: the store's
`refreshed` heard while `_refused`, `remotefiles.covers(root, cwds)` over
the listed rows' cwds (a cwd at or above the root, or a worktree's
project; a cue only, the service decides), disconnected once it fires or
the instance dies (PR-2.8's nit 2, PR-3.2). A link
check that needs the files transport must `remotefiles.install(link)` first
(the app does on every connect; a widget check on a harness link does it
itself, as `check_root_name_links.py` does). "Is this directory a git
checkout" (the worktree box, the sidebar project menu, the header's one-off
entry, a sibling's worktree launch) is `checkouts` — an `fs.stat` of
`.git` off the main loop, in two forms because a failure means two things.
`checkouts.ask` is for an *offer* (the menus, a sibling's launch): False on
any failure, so a worktree is simply not offered. `checkouts.ask_answer` is
for the new-chat screen, whose Send may already be waiting on it: True,
False, or **None for "could not ask"** (a refusal, no link, nothing within
`ANSWER_TIMEOUT_S`, 10 s, not the files' 60; since the 2026-10-18 review's
nit 5 `ask` waits the same 10 s, since a person waits on an offer too: at a
right-click whose menu is built on it, at a `start_session` tool call whose
tab opens on it), and None is never remembered
(the next caller asks again). A screen whose own first ask failed, built
while the link was down or the service stalled, arms one re-ask on the
next reconnect (`_arm_checkout_reask`, `remotefiles.Watcher.on_reset`, the
`_RootNameLinks` idiom; disarmed when the answer lands, when the screen
goes at Send and on destroy): without it a project whose default is off
never showed its box for the screen's life, since no Send there asks
(the review's should-fix 1; `check_new_chat.py` drives it with a
`watcher().reset()`). The new-chat screen builds its
checkbox hidden and shows it when the answer lands (`NewChatView.set_is_git`).
A Send that beats the answer (nothing a person does against a local
service, a line's typing against a remote one) must not read the hidden box
as unticked, which would launch a default-on project plain: until the
answer the screen sends the box's *wish* (`newchat.worktree_wish`, `is_git`
None), and `MainWindow._on_new_chat_send` holds the launch on
`TerminalTab.when_checkout_known`. While it is held the whole screen is
insensitive (`TerminalTab.set_new_chat_held`: what is typed or picked after
the Send would be thrown away by the launch), a second Send starts nothing
(`_new_chat_sends_held`), and a page that leaves the window (closed, handed
over) has its hold called off (`_call_off_new_chat_send`). What the answer
does is `newchat.send_outcome`: a worktree in a checkout, plain in what is
known to be none, and **no launch at all on None** — the screen comes back
with its text, the draft stays, a toast says the folder could not be
checked. Never launch outside the worktree the user asked for: the session
would edit the checkout the worktree was to protect. A draft's restored
choice is kept through the wait too (`newchat.worktree_draft_choice`).
`check_new_chat.py` drives all of it with `checkouts.ask_answer` held back.
Two Ctrl+clicks on images are ordered by the press, not the landing: the
press takes a number (`tab.link_clicks`, `editorfiles.ClickOrder`), carried
through `_on_click_resolved` and `_open_file_reference` to `_present_image`,
which presents nothing for a click older than one already shown but still
records it in the attachments gallery (the gallery is what the user
clicked, not which lightbox won). The hold is a token per page
(`_new_chat_sends_held` is a dict), so a page handed to another window and
back inside one round trip launches with its current Send, and the screen
is released in a `finally` around the launch. The menus
build when it lands (an ask a newer one overtook adds nothing: the header
entry's `_alt_new_asked`, the project header's `_group_menu_asked`), and
`start_background_session(..., is_git=)` is handed the answer
(`_BackgroundSpawn.begin` asks only when the launch would use a worktree).

Easy copy & paste (`easy_copy_paste`): Ctrl+C copies when there is a
selection else SIGINT, Ctrl+V pastes; these live in `_on_key_pressed`
consulting `keymap.KeyMatcher`, not in `Gtk.Shortcut`s (a shortcut can't be
conditional on a selection). The window's `ShortcutController` runs in the
**CAPTURE** phase, so every chord it claims never reaches the CLI; a disabled
`NamedAction` lets its key fall through into the terminal.

Tabs follow the sidebar's order (`taborder`, re-sorted on `refreshed` and
`page-reordered`); tabs with no row collect at the end. The tab bar is hidden
by default. `_tab_widget(page)` finds a tab's private `AdwTab` by its `page`
property (creation order diverges from position).

## The service's stream filter (termstream)

The split (`~/specs/collins/split-service-and-client.md`) moves the pty out
of the tab into a headless service; `collins/service/termstream.py` (GTK-free,
stdlib only) is what every pty's output on the service goes through. One
`StreamFilter` per pty; `feed(bytes)` returns `Filtered(forward, replies,
tokens, events)`:

- **What it strips.** Only the terminal queries in its table (F8 plus what
  CLI 2.1.285 asks): DA1/DA2/DA3, XTVERSION, DSR 5, CPR, DECXCPR, the colour
  scheme report, DECRQM (private and ANSI), OSC 10/11/12/4 colour queries,
  XTGETTCAP `TN`, DECRQSS SGR, `CSI 18/14/21 t`, and the known-silent ones
  (`CSI ? u`, `CSI 16 t`, `CSI ? 4 m`, ENQ, the APC kitty graphics query).
  Everything else is forwarded byte for byte, OSC 9;4 progress and BEL
  included (they also come out as `Progress` and `Bell` events, so the
  client's VTE still rings and shows progress). A query in any other shape
  (`CSI 23 t`, `DCS $q r ST`, an OSC 4 that also sets a colour) passes
  through and the client's VTE answers it late, the user's decision.
- **What it answers, and from what.** VTE 0.84's bytes, from a
  `TerminalState` (grid, cell pixels, the active client's colours and
  scheme, VTE version) and the `ModeTracker`; DECRQM uses VTE's own mode
  table (a sweep of every mode 0 to 65535: which are permanent, which
  default set). The OSC terminator is mirrored. A colour the program set
  itself (OSC 4/10/11/12) is left to the client until it is reset. With a
  `ScreenHook` attached the cursor is read from the screen as it stands at
  the query.
- **The order rule.** `replies` are in the order the queries appeared: the
  CLI closes a round with DA1 and takes what arrived before its answer as
  the round's answers (F11). Never answer out of order or batch by kind.
- **The preamble.** `preamble(screen=True)` re-asserts what differs from a
  fresh VTE: the alternate screen, the modes, the scroll region, the kitty
  flag stack, modifyOtherKeys, the character sets, then cursor visibility.
  Pass `screen=False` when the redraw switches screens itself:
  `?1049h` on a VTE already on the alternate screen clears it (measured).
  RIS resets the tracker; DECSTR resets every mode bit VTE can set, the
  alternate-screen bits too, without leaving the alternate screen.
- **The tokenizer** carries a sequence cut by a read, and a UTF-8 character
  cut at the end of a text run, to the next `feed`; flushes a string open
  at 64 KiB and passes the rest of it on; follows VTE's parser on CAN/SUB,
  an ESC inside a sequence and C0 inside a CSI (hoisted ahead of it).
  UTF-8-encoded C1 controls are text here although VTE executes them (the
  CLI never sends one).

`scripts/check_termstream_answers.py` compares every answer with a real
childless VTE's; when VTE is upgraded and it fails, the responder follows
VTE (update the tables and the module docstring's measurements).

## The service's screen model (termscreen)

`collins/service/termscreen.py` (GTK-free, stdlib only) is the terminal of
record: one `Screen` per pty,
fed the tokens `termstream`'s tokenizer cuts (`apply(token)`; as the
`ScreenHook` of a `StreamFilter` it also answers CPR and DECRQSS from the
screen as it stands at the query). Every automated read of a terminal
(`takes_prompt`, the echo gate, the spinner column, `capture_contents`) is
made of it, never of a client's VTE:

- **Reads** (the `ScreenPort`): `rows()`, `cursor()` (the
  column is `cols` while a wrap is pending, as VTE reports it),
  `row_text(row)`, `tail_is_faint(row, column, foreground, background,
  palette)` (the dim-tail question `takes_prompt` asks, answered as
  today's read answers it: `vtehtml.is_dim_run` over the drawn colour,
  with the active client's colours; one run, no bold / italic / underline
  / strike / blink / overline / background, the colour a scaled-down
  foreground, so faint on a light palette colour or a plain mid grey
  reads dim and faint on a saturated colour does not), `first_column()`
  (the spinner), `capture_contents()` (scrollback plus screen as text,
  the alternate screen alone while it is up), `screen_text()` (one read
  with the soft wraps joined, what `split_screen_rows` undoes),
  `cells(row)`, `sgr()` (the DECRQSS answer in VTE's form).
- **What it keeps.** Two screens of `(text, width, pen)` cells, each with
  its cursor, deferred wrap and saved cursor; one scroll region shared by
  both screens (VTE's, measured); tab stops as tab cells; the character
  sets; 10 000 rows of scrollback kept as runs with their pens (interned,
  so a diff-like screen with a full scrollback is about 30 MiB). Origin,
  autowrap and insert mode are its own; every other mode is the
  tracker's, whose `preamble(screen=False)` leaves both the screen switch
  and the region to the redraw. Every parameter saturates at 65535 as
  VTE's parser does and the grid is clamped to the protocol's bounds.
- **The VTE rules** it follows (F13, measured, listed in the module
  docstring): the pending-wrap table (which operations clear the wrap and
  act at the last column, which leave it), ED 2 moving the rows in use
  into scrollback, tab cells, never-written cells trimmed at a row's end,
  erased-with-background cells trimmed too, the per-screen saved cursor,
  IL/DL to column 0, a private-prefix CSI never read as its twin, width
  by `dropimages.cell_width`'s rule (`WIDTH_SKEW` is the full table of
  the 473 code points VTE draws otherwise, re-measured with the spike's
  `widths` mode). ED 2 moves the rows VTE's buffer holds into the
  scrollback, blank ones included (`Grid.used`), so a panel shell's
  Ctrl+L leaves the same history VTE would. No reflow on `resize` (D19):
  rows are truncated or padded, the region cleared, rows above the cursor
  scrolled into history only as far as keeping it on screen needs.
- **The redraw.** `snapshot(preamble)` is the whole of attach: the
  scrollback rows as text with their pens (pushed above the screen), the
  main screen with absolute addressing and a full SGR per pen change
  (erased cells erased again so a row's text ends where it did), the
  alternate screen on top when it is up, the tracker's
  `preamble(screen=False)`, the tab stops and charsets, then the cursor
  (a pending wrap re-made by writing the last column last) and the pen.
  Not carried (F14): a tab that no longer ends on a stop, a wrap flag
  ahead of an empty row, OSC 8 links.
- **Fidelity is pinned two ways.** The goldens in `tests/fixtures/streams/`
  are what VTE 0.84 showed for every recorded scenario (two real CLI
  sessions in each mode, the worktree dialog in each) and every synthetic
  one (`scenarios.py`: the spike's tables plus the pending-wrap table);
  `tests/test_termscreen.py` holds the model to them (rows, cursor, every
  drawn cell's colours and attributes, the soft wraps, the scrollback, the
  dim tail and the grammar's reads), and `scripts/check_termscreen_parity.py`
  holds a real VTE to the same goldens under the headless display, feeds
  every scenario's `snapshot()` to a fresh VTE and holds it to the same
  (three named exceptions, all F14: `tabs-overwrite`, a tab that lost its
  stop, and `pending-su` / `pending-sd`, a wrap flag on an empty row that
  no write re-makes), and compares DECRQSS answers, the tab stops after a
  resize, and the resize scenarios (`scenarios.RESIZE`, VTE resized
  between two feeds). Never special-case the CLI in the
  model to make a fixture match (spec §5): escalate with the differing
  rows instead.
- **Re-recording.** `python3 scripts/spike_split_3_screen_model.py record
  DIR classic fullscreen worktree` from the checkout, DIR a neutral
  directory outside every checkout and outside the scratchpad (the path
  shows up in the stream; `/tmp/<something>` is fine, a path with the
  user's name is not). It runs a real `claude` in an isolated `$HOME`
  (`spike_split_common`) and spends four real turns. Copy the `.bin` and
  `.marks.json` into the fixtures directory, `grep -a '/home/'` them, then
  `check_termscreen_parity.py --write` under the headless display to
  regenerate the goldens from VTE, and run the unit suite.

## The tab on the pty server (ptyclient, the socket, core)

**The wiring.** The app owns one `api.client.SocketLink` (`apilink.current()`)
to the service and a `connection.ConnectionManager` that finds or starts
`collins-service` (the unit via `systemctl --user start` for the default app
id, else a detached spawn), connects both channels, subscribes, and on a
loss backs off (1, 2, 5, 10, then every 30 s) and reconnects. **A remote
service** (spec §3.24, PR-3.1) puts a `sshforward.RemoteTransport` where
find and start were (`ConnectionManager.start_remote` at launch,
`switch` while running): *find* probes the forward's local socket
(`<runtime dir>/collins/remote/<connection id>.sock`, `none` while no
forward runs), *start* runs `ssh TARGET collins-service --print-socket`
(10 s, bounded; exit 255 is ssh's "could not connect" with stderr's last
line, any other exit `collins-service`'s own) and then the `-N -L` child of
§3.2 (`BatchMode=yes`, `ServerAliveInterval=15`, `ControlMaster=no` so the
forward is a connection of its own), unlinking the local socket first and
only ever a socket. The child's exit is the manager's `lost` (reported from
its stderr-reader thread, landed on the main loop), its last stderr line
the banner's detail: `MainWindow.set_reconnecting(True, name, detail)`
reads "Reconnecting to <name>: <detail>". A hello that fails over a live
forward (ssh accepts, the remote service is dead) tells the transport
(`connect_failed`), whose next find says `none` so the next start runs
`--print-socket` again, which restarts it; the local service's loop never
loops on a socket nobody answers. On a reconnect the remote start runs on
the owner's thread (`_start_async`) so the banner paints; at launch it
blocks like `start_local`. Footguns: the manager sets `link.path` itself
before every connect (the forward's socket or the local one); a `switch`
closes the link deliberately (no loss reported) and takes the lost road
with no backoff; the `local` proof is read from *this* device's proof file
and refused by the remote service (D11), so a remote window is never
`local`. `connections.py` (GTK-free) is the list in `ui-state.json`
(`Connections` over the device's one `UiState`, which the app shares with
`RemoteState` so the file has one writer; rule 5 on every row; a target
ssh could read as an option cannot be spelled), `connectdialog.py` the
*Connect to…* dialog (Test runs `print_socket` on a thread), and
`App.switch_connection` the switch: `open_tabs` flushed and frozen, every
window's `detach_all_tabs()` (the quit's detach loop without the quit),
`jobclient.reset`, then `ConnectionManager.switch`; `_on_connected(False)`
resets the mirrors and reopens the new service's `open_tabs`. **From the
review of PR 623 (ruled 2026-10-29):** the remote command runs through the
box's login shell (`exec "$SHELL" -lc 'exec collins-service
--print-socket'`, one fixed string, the app id validated inside it), since
`ssh host cmd` gets a non-login PATH without `~/.local/bin`; the printed
path is taken only when it matches `sshforward.SOCKET_PATH_RE` (ssh
expands `${VAR}` and `%u` of a `-L` path on the client, so an attacker's
line would pick this device's variables); `RemoteCommand=none` and
`RequestTTY=no` join `ControlMaster=no`; each forward writes
`<id>.pid` beside its socket (ssh pid, client pid, socket) and
`Forward.start` ends a recorded forward whose cmdline is still that `-L`
before unlinking, while `sweep_orphans` at app start ends those whose
client pid no longer lives (no `PR_SET_PDEATHSIG`: `preexec_fn` in a
threaded client, and the signal is per parent thread); `RemoteTransport`
takes a lock so a `stop` landing during `--print-socket` or right after
the spawn leaves no child (`start` raises `ForwardError("stopped")`), and
`self.forward` is set before `start()` so a death in the first instant is
reported; a protocol mismatch over a forward never offers *Restart
Service* (`quitrules.mismatch_words`: *Connect to This Computer*, the body
names the side to upgrade; the forward is stopped before any exit); the
`local` proof is not sent over a forward at all; `_on_link_lost` cancels
a WAITING round's poll chain and `_poll` stops when its generation moved
(two chains once shook hands twice); `detach_all_tabs` drops a page's
settling state (`_closing_pages`, `_bg_closing`, `_close_asking`,
`_bg_queue`) so every page leaves the old service instead of re-attaching
its old pty id on the new one; `expect_restart` is skipped for a remote
restart (the pid is the box's).
**From its verification (ruled 2026-10-29):** the socket and pid file are
`<runtime dir>/collins/remote/<app id>/<id>.{sock,pid}` — `ui-state.json`
and so the connection ids are shared by the real and the debug instance,
and two app ids on one path took over each other's forward on every
reconnect; `end_stale_forward` leaves a record whose client lives
(another process) alone, file included, and signals through
`pidfd_open` + `pidfd_send_signal` after the cmdline check; the sweep
ignores a record naming a socket that is not its own `<name>.sock`; the
remote command is a POSIX `sh -c '…'` wrapper (single quotes only, so
tcsh/nushell hand it on) running `$SHELL -lc` for the POSIX family and
fish and, only on exit 127, `$SHELL -lic` (the interactive rc, where
pipx's `ensurepath` writes bash's PATH behind Debian's guard) — never
`-lic` first, since an rc that hands the shell on (`exec zsh`, auto-tmux)
would run instead of the command (the second verification, V1; a `-lic`
round that prints nothing is reported as that hand-over,
`MSG_HANDOVER`); `sh -lc` for any other shell
(`sshforward.remote_command`; the tests run the wrapper under a real
bash with both rc shapes). The service's own capture (`$SHELL -lic 'env
-0'`, `service/main.py`) has the same hand-over hazard and fails soft
(an empty overlay); it is §3.10's and not this chunk's, reported rather
than changed. `_signal_forward` falls through to check-then-`os.kill`
when `pidfd_open` is refused (ENOSYS/EPERM/EINVAL; V2); a switch away
from a dead link with dirty buffers asks Discard / Cancel and says why
(`App._ask_discard_editors`, V3). `switch_connection`
asks about unsaved editor buffers per window (`_ask_save_editors`; Cancel
aborts the switch and stops the prepared transport) before `_switch_now`;
a remote mismatch answered Cancel shows "{name} speaks another protocol;
nothing is connected" with a *Connect to…* button (`set_mismatch`); the
path grammar admits `@ + , =`. A tab's (and a
panel shell's) client is an `api.client.PtyClient` from
`terminal._pty_client`: `request`, `send_input`, `send_event`, `close`, fed the
output frames and events of the ptys it spawned or attached. Tests and
harness checks reach the link through `scripts/e2e_service.py`.

**Two connections, one rule** (D26). The link has a **primary** WebSocket
(the subscription, every event, output and input frames, `send`) and a
**sync channel** (`call`, which blocks its calling thread, the main thread
included, on a `threading.Event` for up to 10 s, then `gone`). Both are
driven by one daemon thread running its own `GLib.MainContext`; what
arrives lands on the main loop through one ordered queue at
`PRIORITY_DEFAULT`. Footguns: **never `call` from that I/O thread** (it
would wait on itself), and never expect an event to have landed when a
`call` returns: the events a reply implies (`session`, `pty`) land after it,
so read the fact the reply carries, not a mirror. `subscribe` is the one
call sent on the primary, and the main thread drains the landing queue while
it waits, so the mirrors are full when it returns. A lost link fails every
pending call `gone` and calls `on_lost` once.

`TerminalTab.reattach()` and `PanelTerminal.reattach()` are what the
manager's reconnect calls (a redraw from the service's model; a pty the
service lost reads as the shell's exit, and the tab shows "ended" and offers
resume). `save_panel_history` is the `panel.history` request.
`App.do_shutdown` closes the link and ends nothing on the service.

**One backend.** A session tab has the service's pty server only, spec §3.4:

- **The terminal has no child.** `TerminalTab.terminal` is a
  `ptyclient.ClientVte` (a `Vte.Terminal` that reports its own allocation,
  since GTK4 has no size-allocate signal) glued to a pty of the service by
  `ptyclient.ClientTerminal` (`tab._view`): output frames are `feed()`,
  every `commit` goes back as an input frame, the grid goes as a `resize`
  event on every allocation change while this client owns the size (below),
  `notify::has-focus` as a `focus` event, `pty-exited` is what `child-exited` was (`tab._on_pty_exited` →
  `Session.shell_exited`; a status the service does not know is -1). The
  ports are `hosting.RecordPtyPort` and `RecordScreenPort` on the service over its
  pty and `termscreen.Screen` (the dim judgement with the
  active client's `term`, palette included): the model is the screen as
  anchored to the cursor already, so row indices are the model's own;
  `row_text` cuts a row's cells, `visible_text` joins on the wrap flags.
- **The spawn.** `TerminalTab._service_spawn`: one `spawn` request (kind
  `agent`, the cwd, the VTE's grid, and what the `Session` is built from:
  `session`, `fork`, `continue_recent`, `jsonl_path`, the options'
  fields, `sandbox_plan`, `fork_resolve`, a new chat's `prompt`); the
  service builds the session, runs `Session.spawn` (its `spawn_shell` is
  `PtyServer.spawn`, then `shell_spawned` types the command), answers
  with the pty and the session's `handle`, and the tab attaches. The
  **service** builds the shell's environment (its own, plus the two
  progress declarations when `progress_termprop` is on); the session
  types the agent command exactly as before, the `sandboxrun.py` wrapper
  included. A refusal (`SpawnError`'s errno) is painted where VTE's spawn
  error was. A resume whose reaped worktree is being put back spawns the
  shell in the directory at once, made empty, and types the command once
  the checkout has landed (`Session._recreated`; a `cd` ahead of it when
  the worktree couldn't be put back).
- **The redraw guard** (`redrawguard.RedrawGuard`, GTK-free,
  `tests/test_redrawguard.py`). Raised by `attach` (the VTE reset first)
  and by the first `REDRAW` frame of a redraw the service sends on its
  own (flow control, a resize for another client; the VTE is reset then
  too). Every commit is dropped until VTE answers the sentinel fed after
  the frame flagged `REDRAW_END`: a generation-tagged `OSC 4;<index>;?`
  (DSR 5 answers the same whatever was asked, so two sentinels could not
  be told apart), and only the latest sentinel's answer lowers it, so
  two attaches back to back or a redraw landing mid-redraw stay guarded
  until the last repaint is in. A 2 s watchdog lowers a guard nothing
  answered (an attach that failed, a sink cut mid-redraw) and logs. The
  `attach` reply's `modes` lists what the preamble re-asserted; if
  `?1004h` is among them the real focus state is sent when the guard
  comes down. The attach is also where the session's facts arrive whole
  (the `session` event). `ClientTerminal.dropped_commits` counts what the guard
  swallowed (`check_attach_redraw.py` asserts it and that no sentinel
  answer reached the pty). A commit carrying NUL (Ctrl+Space, Ctrl+@)
  reaches `commit` as an empty C string of size 1 and is sent as `\0`
  (`ptyclient.commit_bytes`).
- **The mouse.** `mouserate.MotionCoalescer` (GTK-free,
  `tests/test_mouserate.py`): a plain motion report naming the cell of the
  one before is dropped, the rest coalesced to the latest per 30 ms on a
  timer in `ClientTerminal`; presses, releases, wheel and drags go at once
  and flush what was pending ahead of them.
- **Two clients on one pty** (§3.24, PR-3.5). The service decides who
  owns the size (the active client, below in the pty server); the client
  shows. `sizerole.SizeRole` (GTK-free, `tests/test_sizerole.py`) reads the
  `attach` reply and every `pty` event (`active`, `cols`, `rows`,
  `sized_for`) into `PINNED` / `MOVED` / `RELEASED`, and
  `ClientTerminal._apply_role` acts: while not active, `send_grid` and the
  allocation's send are suppressed (`grids_withheld` counts them), the VTE
  is `set_size` to the pty's grid (before the redraw is fed, since the
  reply is applied in `attach`), and the tab's and each panel shell's
  scrolled window is a `ptyclient.TerminalFrame`: an `Adw.Banner` "Sized
  for <device>" (`sizerole.banner_title`: the device is foreign, bounded
  and stripped of control and format characters, shown with
  `use_markup` off) with *Use this size*, over a `PinBox` that allocates
  the scrolled window the grid's cells (`cols × char width`) plus
  everything around the cells as the last unpinned allocation measured it
  (the scrolled window's width less the cells the terminal fitted: its
  frame, the terminal's margins, border and padding, the remainder short
  of a cell), top left and clipped; never the terminal's `measure()` at
  that point, whose cache still holds the grid from before the allocation
  that regridded it, and never against `get_width()`, the content box,
  which counted margins and padding twice (the Fable review of PR 627,
  S1: a 60x15 pin with 12 px margins came out 62x16). `halign START`
  alone is not enough (F2 holds only while the pane is larger: a
  `Gtk.ScrolledWindow` allocates a scrollable child its viewport, so a
  smaller pane regrids the VTE; measured). *Use this size* is
  `take_size()`: a `focus {focused: true}` event and `grab_focus`; a click
  (a capture-phase `GestureClick` on the terminal, `view.click`, never
  claimed) does the same while pinned, and a key is input, which the
  service counts as taking the role (only typing: a frame of nothing but
  focus and mouse reports takes nothing, `ptyserver.takes_role`, so the
  focus state sent when a re-attach's guard drops, a focus-out, a hover
  or a wheel leave the size where it is). On `RELEASED` the box lets go,
  `_last_grid` is cleared and the VTE re-allocated, so the grid it lands
  on is sent even when it equals the one sent before the pin.
  `ClientTerminal.active` is the role's, read-only. When the active
  client detaches the service hands the role on at once (below), so a
  lone window is never left pinned. `scripts/check_two_clients.py` is the
  e2e (the app's tab with 12 px margins, a stub that turns `?1004h` and
  SGR mouse reports on, and a bare `SocketLink` on another device: attach,
  pinned, take the size, the tab pinned through window resizes and a grid
  larger than its pane, the pinned tab re-attached and still pinned after
  its focus report, the banner's button, a key, a click, the second's
  detach handing the size back).
- **`feed_message` is `paint`** (rule 2 of §3.1) once the pty is attached:
  the line goes into the stream on the service, so the model and every
  client show it; before the spawn or after the exit it is fed to the
  widget alone.
- **The tab's end.** `TerminalTab.release_pty()` (from
  `MainWindow._on_close_page`, the final close) sends `close` (mode
  `kill`: SIGHUP plus the master closed, with SIGKILL after the grace;
  `exit` and `background` are the session's graceful close on the service
  — `Session.begin_close` — and a budget that runs out comes back as a
  `close` event the window answers), detaches and closes its `PtyClient`:
  closing a tab ends the session (D12). `TerminalTab.detach_pty()` is
  *Detach*'s and a destroyed tab's: the pty runs on.
  `App.do_shutdown` closes the panel shells' ptys and the link; the
  agents' ptys live on (§3.10) and the next client attaches.
- **The service's side** is `ServiceCore` (`ServiceCore.with_state` over the
  service's own `AppState`): the pty half serves `spawn`, `attach`,
  `detach`, `paint`, `close` and takes the `resize` / `focus` / `theme` /
  `ack` events; the store and state half is in
  `collins-sessions-and-sidebar`.
- **What differs, by design.** A row written before a resize is kept by
  the model as it was (no reflow, D19) where VTE re-wraps it: a shell's
  echo from before the tab's first allocation reads differently in
  `capture_contents` between the model's read and the VTE's until the program repaints
  (`check_attach_redraw.py` compares from the first line written at the
  settled grid for that reason). `capture_contents` is held to VTE's
  `write_contents_sync` by the goldens' `capture` read (three wrap
  scenarios in `scenarios.SYNTHETIC`, every scenario compared after
  `scenarios.normalise_capture`): soft-wrapped rows joined, and the rows
  `ED 3` blanked left out (VTE's text reads show them, measured;
  its capture does not:
  `Screen.scrollback_erased` counts them, carried in the model file).
- **The first keystroke after focus** (F11's unexplained loss) is the
  harness, not the client. `scripts/probe_first_keystroke.py` drives the
  glue with real input through the headless shell's own bus: with `cat`
  behind the pty, under every mode the CLI sets (focus reporting, mouse
  tracking, the kitty flags, modifyOtherKeys, bracketed paste) and with
  the guard freshly raised and lowered, the first key after a click
  arrived in every round (20 to 30 a condition; the one miss was a click
  that did not focus, not a key). `scripts/spike_split_7_server_drive.py`
  (a real `claude` in a real tab) reproduced the loss once per run: the
  key after the **first** click into the window never reached GTK at all
  (a capture-phase key controller on the window saw nothing, no commit,
  the guard down), while the key after a second click arrived in every
  run (the same on VTE's own child in the old control run). The key never reached GTK;
  the compositor's keyboard focus not having moved yet to the window the
  click activated is the likely cause (the toplevel reports
  `is_active()` False throughout under the headless shell). A harness
  that clicks a window for the first time and types in the same breath
  loses that key.
- **Running the suite on it:** `python3 scripts/run_e2e.py`; a single
  check: `python3 scripts/check_x.py`.
- **The panel shells** are the same, each a `ClientTerminal` over
  a `shell` pty with its own `PtyClient`; see `collins-panel-dock`,
  "Panel shells on the pty server".

## The session on the service (hosting, tracking, finish, clientsession)

The `Session` is the service's (spec §3.19). The pieces:

- **`service/hosting.py`.** `SessionRecord`, one per live agent pty
  (`ServiceCore.sessions`, by pty id, built by `spawn` for kind `agent`
  and dropped once the pty's exit has been reported): the `Session`, its
  `SessionHost` (this object), its ports (`RecordPtyPort`,
  `RecordScreenPort`), the `CutSink`s of the composers that asked for a
  cut, and the **facts** a client reads synchronously off its tab, sent
  as a `session` event: whole on `attach` (`snapshot`) and as the fields
  that changed after (`refresh_facts`, on the output settle — 50 ms after
  a burst, `output_arrived` — and the 2 s process poll; D28). The
  identity (`session`, `fork`, `options`, `cwd`, `agent_cwd`, `pid`), the
  box reads (`takes_prompt`, `entered`, `prompt_block` as a msgid,
  `unstarted`, `foreign_paste`, `agent_running`, `running_command`), the
  launch state (`initial_command`, `worktree_launch`, `new_chat_prompt` as
  a `PREVIEW_MAX` preview, `shells_follow_armed`, `closing`; the
  paste-back's `pasted_back` / `paste_back_pending` are not sent — no
  client reads them, the checks probe them),
  the sandbox (`sandboxed`, `sandbox_box`, `sandbox_plan_path`,
  `defaults_owed`, `can_restart`), the transcript tail (`model`, `effort`,
  `permission_mode` — the transcript's own, None while unknown —
  `transcript_path`, `prs`, `touched_files`, `attachments` — the three
  lists cut to the byte budgets of `protocol.SESSION_LIST_BYTES` with
  `fit_list_bytes`, so an event with every field at its bound still
  encodes under `MAX_FRAME` — `ledger_armed`, with `landed` and `reset`
  one-shots), and the activity verdicts by handle (`busy`, `finished`).
  The `composer` event's stash is fitted by bytes too (`fit_text_bytes` to
  `EVENT_TEXT_BYTES`: a TEXT_MAX string escaped and encoded can pass the
  frame). The `cut` event's seed is **never cut back** (D55): a box whose
  text, stand-ins expanded, would not frame is refused before the erase —
  `Session.cut_fits_now` on the `cut` request, answered `refused` with
  `CUT_TOO_LARGE_MSGID`, which the tab paints (`ClientSession.cut_refusal`)
  while its composer stays empty over the intact box; the settle's own
  check (`_apply_cut` → `ended`) is the belt. `composer_open` — the client
  fact `_post_switch` reads — arrives on the `switch`, `send`, `cut` and
  `draft.restore` requests and is remembered. `paint` formats the
  "[session manager]" line and inserts it into the stream (a line painted
  before the spawn waits for the pty). `focus_terminal`,
  `refocus_composer`, `resend_composed`, `stash_draft` and
  `mark_stale_shells` are `focus.terminal`, `composer` and `shells`
  events; a close budget running out is a `close` event (`state: budget`)
  the window answers by forcing the close — sent only while a client is
  attached; with none the service forces the close itself
  (`ServiceCore.force_close`).
- **The requests** (`ServiceCore._req_*`, each routed to the session by
  its pty): `prompt {text, focus, when_empty}` (with `when_empty` the
  service re-reads `takes_prompt` off the live screen and refuses with
  `PROMPT_BLOCK_MSGID`: the PR menu's send and `start_session`'s poll use
  it), `write {text, mention}`, `switch {model|effort,
  composer_open}`, `send {text, composer_open}` (reply `sent`: now, or
  waiting on a cut, which comes back as `composer resend`), `cut` (reply
  the cut's `handle`), `cut.cancel`, `draft.restore` (reply `restored`),
  `mention` (the service builds the reference and the leading space),
  `clear` (served for agents now: the provider's clear keys), `close
  {mode, text, force}`, `close.nudge`, `close.end`, `resolver.arm` (the
  tab's map), `transcript.update/set/relocate`, `prs.restore`,
  `cwd.settle {cwd, root}` (reply the `FollowScope` name: the editor's
  root is the client's), `shells.follow`, `finish.witness`,
  `baseline.absorb`, `baseline.cmdlines`, `restart.worktreeless`,
  `sandbox.restart` (by box). A panel shell's reads are `pty.info` and
  `pty.capture`. The sandbox host and the live grants are the core's
  (`start_sandbox_host`: `App._start_sandbox_support` moved whole; the
  probe's verdict is a `sandbox` event `what: "probe"`, and
  `service.status` carries it with the grants' `live` reason); a session
  resolving settles its box there (`ServiceCore.session_resolved`) and
  re-files its panel shells' history (`rekey_shells`, by the `handle` a
  shell's `spawn` names); a resume or fork with no box is minted one
  there (`_box_for_launch`).
- **`service/tracking.py`.** `ServiceActivity` (`start_activity`): the
  one activity tracker, fed by `termstream.Progress`
  events in place of the termprop (`on_progress`, through the
  `progress_termprop` setting), the output settle in place of
  `contents-changed` (`on_settled`: `Session.redraw_counts`), the input
  frames in place of `commit` (`on_input`: the echo gate's poke, a
  carriage return's arm, the baseline's last snapshot and the pre-emptive
  mark), the `/proc` poll every 2 s for every live agent
  (`has_background_descendant` behind the plumbing baseline, absorbed
  through a fresh spawn's pre-submit life by handle and persisted under
  the id on resolve), and the background-busy poll (`claude agents
  --json`, 3 s, while a session is attached to a background agent:
  `Session.attached_background`, off the provider's
  `is_attach_command`). Verdicts: `store.set_busy` on the rows a session
  stands for (chain-aware; re-synced on the store's refresh for a session
  that got its row mid-turn), a counted finish's `set_unread(True)` with
  the window's old exemptions (a chain still busy, a row `backgrounding`,
  a row with status `background`), and a `session {finished}` to the
  pty's clients, which refresh the pull requests and mark the greens as
  owed an announcement; a session with no row hears `busy` / `finished`
  by its handle (the placeholder row). `service/finish.py` is the judge
  (`FinishJudge`; `held`
  is what `check_pr_refresh_on_finish` reads by probe).
- **`clientsession.py`.** `ClientSession`, the tab's mirror: the fields
  under the attribute names the forwarders read, the reads that answer
  from them (`takes_prompt_now`, `prompt_block_text` — translated —,
  `entered_prompt`, `has_running_command`, `agent_is_running`,
  `current_agent_cwd`, `current_model` with the options as the fallback,
  ...), `apply(event)` (the names that moved; the one-shots every time
  they are sent), `exit()` on `pty-exited`, and each request as a method.
  The tab's `_on_session_event` turns the moved fields into
  `_on_transcript_landed` / `_on_transcript_reset`, `_on_cwd_polled`,
  `_sync_sandbox_chip`, and the signals `session-resolved`,
  `fork-resolved`, `activity-changed`, `run-finished`; `close-budget` is
  the close event. The window keeps `_clear_unread` (a keystroke is
  presence), the attention mark off `contents-changed`, the /bg
  orchestration and the notification delivery; `store.flags` keeps
  `status` and `unread: false` and refuses `busy`, `backgrounding` and
  `can_background` (the service's, `service/bgagents.py`).
- **The cwd tick** is a client 2 s tick while mapped, over the mirror's
  `agent_cwd`, which the service's own poll publishes on change: the
  footer's branch and the git page's root check ride it (`poll_tick`;
  the page's signatures' compare is the service's `git-changed` since
  PR-2.2, and the finish edge calls `check_now`).
- **Running means the CLI runs.** A session counts as running (the row's
  `running`, `open_tabs`, attach-on-activate) only while its CLI does:
  `ServiceCore.cli_changed` follows the `running_command` fact, publishing
  the pty's table row when the CLI comes up and a table `pty-exited` with
  status null when it leaves while the shell lives. A shell left after the
  CLI exited is resumed over (the service closes that pty).
- **The spawn refusal.** An agent `spawn` for a session that already has a
  live agent pty whose CLI runs is refused `{"error": "refused", "msgid":
  "This session is already running in the Collins service", "args": {"pty":
  <id>}}`: two CLIs must never write one transcript. The client attaches
  to the named pty instead. Fork spawns and fork records are exempt (a
  fork's tab must not land on its parent's pty).
- **The debug API** (D27): `debug.*` requests (`debug_patch`,
  `debug_find_handle`, `debug_spy_writes`, `debug_tools_*`, a session
  attribute read, written or called by name, a pty's screen and process
  facts), served only with `COLLINS_DEBUG_API=1` in the service's
  environment, which `scripts/e2e_service.py` sets for a check's service and
  which is stripped from spawned shells. A monkeypatch in a check's own
  process no longer reaches the service: stubs go in
  `scripts/e2e_stubs.py` or `debug_patch`.
- **Testing.** `tests/test_core.py` (a spawn builds a session, the attach's
  snapshot, the facts moving with the screen, the requests, a client's
  departure cancelling its cut, the probe gated on the flag, `busy`
  refused), `tests/test_clientsession.py` (every field on its attribute,
  the requests' shapes through a fake client), `tests/test_finish.py`
  (the judge), `tests/test_tracking.py` (the tracker's two roads for a
  verdict, a counted finish's flags and exemptions, the background-busy
  poll, through fakes), and `tests/test_session.py` as before.

## The service's pty server (ptyserver)

`collins/service/ptyserver.py` (GLib only, nothing from GTK; served by
`service.core.ServiceCore`) is the table of terminals the service owns, one
`Pty` per agent session and per panel shell (spec §3.3).

- **Lifetime.** `PtyServer.spawn(kind, argv, cwd, env, cols, rows, …)`
  does `os.openpty()`, sets the window size on the slave, forks, and in the
  child `os.login_tty` (a new session, the slave its controlling terminal),
  resets SIGPIPE and SIGXFSZ to their defaults and clears the signal mask
  (Python's inheritance), then `execvpe` with the caller's environment plus
  `TERM`, `COLORTERM`, `VTE_VERSION` and the two progress spoofs
  (`ConEmuANSI=ON`, `TERM_PROGRAM=kitty`). A failed exec comes back through
  a close-on-exec pipe and `spawn` raises `SpawnError` (an `OSError` with
  the child's errno), nothing left in the table. The master is non-blocking
  and read on a `GLib.io_add_watch` at `PRIORITY_DEFAULT`; each read goes
  through the pty's `StreamFilter` (which feeds the `Screen`), the replies
  are written back, the forward bytes go to every sink, the stream events
  to the server's `on_event` listener. A `GLib.child_watch_add` reaps the
  child; the master is drained to EIO, the model saved, every sink sent
  `{"t": "pty-exited", "pty", "status"}` (the exit code, or minus the
  signal), the row removed. `close()` is SIGHUP to the process group plus
  the master closed, and SIGKILL after `CLOSE_GRACE_MS` (5 s) for a child
  that ignored it; `shutdown()` closes every pty, waits a bounded time
  for the saves in flight, then finishes each on the spot (status
  unknown: the reap no longer lands on a stopping service), its row
  recorded gone and its model file removed with it (stopping the
  service ends every foreground agent, and so does a crash of it; the
  CLI's `/bg` daemon and its agents hold no pty of the service's and run
  on, D52's `KillMode=process`). A `Pty` implements the `PtyPort` (`write`,
  `resize`, `child_pid`, `foreground_pgrp`) for its `Session`,
  and for a panel shell `shell_pid` (a sandboxed one's shell inside the
  box), `has_running_command` and `process_cwd`; `clear(pty)` swaps in a
  fresh model and `capture(pty)` reads a live pty's text ("" once gone:
  the model file goes with the row).
- **Attach and the redraw.** `attach(pty, sink, cols, rows)` sends
  `Screen.snapshot()` with the tracker's `preamble(screen=False)` in
  frames of at most `protocol.MAX_PAYLOAD`, every one flagged `REDRAW`,
  the last `REDRAW_END`, then live output with flags 0. The reply is
  `attach`'s: the grid the redraw was painted at, `active`, `sized_for`.
  A sink is any object with `send_output(bytes, flags)` and
  `send_event(dict)`; `drop_queued()` and a `device` attribute are
  optional; sinks are keyed by identity.
- **The active client's colours answer the queries** (§3.3): a client's
  `theme` event sets its own term (the client's `term`, which its sinks
  read), and a pty's responder takes its active sink's
  (`PtyServer.pty_term`, re-applied whenever the active client or a
  term changes), the last term any client sent while none is active.
- **The active client owns the size** (D1, §3.3 as ruled on the Fable
  review of PR 627): the last sink to type (`write(…, sink=)` with a frame
  that is not only focus or mouse reports: `takes_role`; a focus report,
  a motion or wheel report or a click's press and release take nothing)
  or report focus (`focus(…, True)`, the client's event), the first to
  attach when none (a newcomer takes the role when nobody holds it, other
  sinks or not). When the active sink detaches with others attached the
  role passes at once (`_detach` → `_take_role`) to the sink that most
  recently typed or reported focus (`_Attachment.acted`), else the most
  recently attached (`attached`), its remembered size applied as
  `_activate` would; with none left the size is left alone. Its resize applies (`TIOCSWINSZ`, the model, the filter's
  grid); another sink's is remembered and applied when it becomes active;
  `resize(pty, cols, rows)` with no sink is the service's own. An attaching
  sink that is active and differs in grid is applied before its redraw, so
  the redraw is painted at its size. Every attached sink gets the
  protocol's `pty` event (`cols`, `rows`, `active`, `sized_for`) when the
  size or the active client changes, and the attacher gets one after its
  redraw.
- **Three queues per pty on the way in.** Every write is queued behind a
  writability watch and drained in this order: the entry a write already
  started (never split further), the filter's replies in the order their
  queries came (F11's rule: a round closed by DA1 is answered in order),
  then typing in arrival order. Replies are never dropped. Typing is
  bounded at `INPUT_QUEUE_BYTES` (4 MiB): past it a client's input is
  dropped whole and everything after it until the queue has drained
  (`Pty.dropped_input` counts the bytes), so a paste is never cut leaving
  the program in paste mode; the loop is never blocked on a write (F11's
  deadlock). A reply can land between two frames of a split paste.
- **Flow control on the way out.** Live output per sink is counted
  (`protocol.QUEUE_BYTES`, 4 MiB) and the transport reports what of it it
  wrote out with `drained(pty, sink, n)` (live bytes only, never a
  redraw's frames, which are not counted); a sink that would pass the bound is
  not sent the backlog: its `drop_queued()` (required) is called, the
  count reset, and a fresh redraw follows. A redraw's own frames are
  never counted against the bound, and a redraw is bounded at attach time
  (`REDRAW_MAX`, half the queue): the oldest scrollback rows are left out
  of the redraw, not the model, until it fits. A sink that never reports
  draining is redrawn every 4 MiB, logged once. A re-attach of an attached
  sink keeps its count until `_redraw` drops it, so what the sink still
  holds of the old stream (the socket sink's pending frames) goes with the
  redraw instead of being released after it.
- **`paint(pty, text)`** is rule 2's inserted text: fed to the model and
  sent to the sinks as if the child had written it, not through the
  responder.
- **The saved model.** `Screen.dump()` as JSON (`MODEL_FORMAT` 1: a pen
  table, cells and scrollback runs naming pens by index; `Screen.load`
  validates every field and bound, clamps the file's caps to the model's
  constants, and raises on anything off) is written to
  `$XDG_STATE_HOME/collins/pty/<id>.model` (0600 in a 0700 directory) at
  most every `SAVE_INTERVAL_MS` while output arrives and while the pty
  lives (see the retention rule below): the dump on the loop, the encoding and the write on a worker
  thread (one in flight per pty, a save asked for meanwhile following it,
  the landing at `PRIORITY_DEFAULT`); `COLLINS_PTY_STATE_DIR` overrides the
  directory (tests, captures). `PtyServer.load_model(path)` gives the
  `Screen` or None, and refuses a file over `MODEL_FILE_MAX` unread. The
  scrollback has a cost budget as well as its row count
  (`termscreen.SCROLLBACK_COST`, 8 Mi units, a row costing its text plus
  `RUN_COST` = 100 per run, the measured memory of a run): it bounds a
  diff-like scrollback at about 4 000 rows and a pen-per-cell one at a few
  hundred, and leaves plain text to the 10 000-row cap. Pty ids are never
  reused: the next id is persisted (`AppState.pty_next_id`, through the
  `record_next_id` callable), so no two ptys share a model file. **A
  model file lives exactly as long as its pty's row**: removed when the
  pty exits (`_finish`) and at shutdown (which finishes every pty on the
  spot, status unknown, since the reap no longer lands on a stopping
  service), and every `*.model` whose id is not in the table is pruned at
  service start (`prune_models`, from `ServiceCore`). The panel history
  and the transcript carry what a person needs after the exit; the file
  exists for a live pty's re-adoption by a restarted service (not built yet).
- **The `ptys` table.** The server's `record(pty_id, row | None)` callable
  (`AppState.set_pty`) keeps a row per live pty in
  `state.json` (§3.8): kind, session, cwd, pid, cols, rows, box, plan,
  options. A spawn and an exit are written at once; size changes are
  coalesced to one write a second.
- **Testing.** `tests/test_ptyserver.py` runs real children (`cat` put in
  raw mode from the master side, `sh -c`, `true`) under the default GLib
  main context iterated by hand (`pump`), GTK-free; a 100 KB write against
  an echoing child, the overflow redraw, the fd count after 200 spawns and
  the ten-thousand-row redraw (measured, in the module docstring) are all
  there.

## Footguns

- **A site that hands a path of the service's to something on this
  device is a local extra**: another app, the file manager, a native
  chooser's starting folder, a `file:` URI. It is offered only when
  `apilink.is_local()` (hidden, never greyed out: §3.12), and the
  function that touches the disk or starts the program asks
  `apilink.is_local()` itself and first, because
  `tests/test_client_is_pathless.py` reads the function and wants every
  such call behind the ask (`if not apilink.is_local(): return` at the
  top of the function is the shape to write;
  `pathless_allowlist.LOCAL_EXTRAS`). The launchers
  (`footerapps.launch_app` / `launch_app_file`, `openwith.
  open_file_default` / `launch_terminal`, `openwith.
  file_open_with_entries`) refuse by themselves, so a menu built on them
  comes out empty: append an "Open In…" submenu only when it has items.
- Redraws the app causes (typing a command, `feed_message`) look like agent
  output; `EchoGate` discounts them, and ungated sources are held on fresh
  spawns until the gate arms.
- A `/bg` agent's environment is scrubbed by the daemon: no progress
  termprop, no echo gate — an attached tab's pole comes from `SpinnerWatch`
  and the `claude agents --json` busy poll only.
- The plumbing baseline (`state.process_baselines` ∪
  `mcptools.infrastructure_cmdlines()`) is what keeps the CLI's permanent MCP
  server children from reading as work; it is captured on fresh spawns only.
- Scripts probing the CLI in a bare VTE: drip keys ~8 ms apart (a burst reads
  as a paste), wait a frame after `feed()`, scrub `CLAUDE_*` from the env,
  `killpg` on exit.
- Closing a window with a live session from a script hangs on the confirm;
  `killpg` the tab's child first and keep an `os._exit` watchdog.

Related: `collins-composer-and-new-chat`, `collins-panel-dock`,
`collins-sessions-and-sidebar`, `collins-session-mcp-tools`.
