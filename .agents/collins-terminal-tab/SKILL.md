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

## `Session` and its ports (where the logic lives now)

Since PR-1.6 of the split (`~/specs/collins/split-service-and-client.md`
§3.5) the tab is a widget around a **`Session`**
(`collins/service/session.py`, GTK-free: GLib/Gio only), one per tab as
`tab.session`. Read the rest of this skill with that in mind — the
mechanisms are unchanged, but most of the methods it names are the
session's now:

| Lives in `Session` | Stays in `TerminalTab` / `MainWindow` |
| --- | --- |
| identity: `session_id`, `fork`, `provider`, `options`, `command_override`, `cwd` | widgets, overlays, dialogs, the footer, the dock, the editor |
| launch: `spawn`, `_finish_spawn`, `_launch_command`, `agent_environment` (was `_agent_tab_environment`), the sandbox plan (`_sandbox_options`, `_reserve_worktree`, `_register/_unregister_sandbox_box`, `_release_sandbox_plan`, `shell_exited`), `_check_worktree_launch` / `relaunch_without_worktree`, `restart_sandboxed` and its poll, the new-chat prompt poll | `spawn_async` on the tab's own VTE (`_TabHost.spawn_shell`), `_on_spawned` handing the pid to the pty port |
| reads: `takes_prompt`, `entered_prompt`, `prompt_block`, `unstarted_thread` (minus the new-chat screen), `visible_screen_text`, `worktree_exit_prompt_keystrokes`, `screen_first_column` | `_mention_leading_space` / `_row_text` (Add to chat) |
| writes: `inject_prompt(_unfocused)`, `switch_model/effort`, the open-cut (`begin_cut` → settle → apply → verify), `send_composed`, `restore_draft` (paste-back), `foreign_paste_in_box` | the composer widget, its open/close/dock, the stash (`_stash_draft`) |
| `/proc`: `candidate_pids`, `agent_is_running`, `agent_pid`, `has_running_command`, `current_agent_cwd`, `has_background_descendant`, `owns_pid_ancestors` | — |
| transcript: the `TranscriptModel`, its monitor/poll/debounce, `request_update` and its landing, the PRs it tracks (`tracked_prs`, `restored_prs`, `attached_prs`), the resolver (`start_resolver`, `arm_resolver`), `current_model/effort/permission_mode`, `finish_witness` | chips, labels, attachments, the editor's agent files (`_on_transcript_landed`, `_on_transcript_reset`) |
| activity: `echo_gate`, `spinner`, `progress`, `finish_ledger`, `redraw_counts` | the `ActivityTracker`, ids, unread — `MainWindow` reads the watches through `_session_of(page)` (its per-page watch dicts are gone) |
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
`first_column`, `capture_contents`, plus three a moved reader needed
(`row_count` for the echo gate's grid, `row_text` for `takes_prompt`'s one
line read on every key press, `visible_text` for the worktree matchers that
read soft-wrapped lines whole). Row indices are relative to the screen's
cursor-anchored first row. Today they are `terminal.VtePtyPort` and
`terminal.VteScreenPort`, adapters over the tab's own `Vte.Terminal` making
exactly the reads the tab always made; later swaps put `PtyServer` and
`termscreen` behind the same ports.

**How the tab talks to it.** Down: every old name on the tab is a forwarder
(`tab.takes_prompt()`, `tab.session_id` — settable —, `tab._options`,
`tab._child_pid`, `tab._initial_command`, `tab.finish_ledger`, ...), so the
window, the app and the e2e checks didn't change. Up: the session reports
through a `SessionHost` listener (`alive`, `mapped`, `paint`,
`focus_terminal`, `spawn_shell`, `session_resolved`, `transcript_landed`,
`process_exited`, ...) which `terminal._TabHost` implements, mostly by
emitting the tab's existing signals. A composer's open-cut reaches the
composer through a `CutSink` (`terminal._ComposerCut`: `alive`, `seed`,
`refuse`).

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

A **sandboxed** launch (`SessionOptions.sandbox`) types
`python3 <…>/collins/sandboxrun.py <plan> -- claude …` instead:
`_launch_command` (from `_finish_spawn`, and again from the chip's
*Restart to apply*, `restart_sandboxed`) writes the plan through
`terminal.SANDBOX_HOST.prepare_launch(cwd, box)` at the last moment (the
workspace is the settled cwd, a recreated worktree included; the box is
the one the options name — a resumed session's, a fork's — else the one
this tab already launched in, else minted by `SANDBOX_HOST.mint_box(cwd)`,
seeded with the project's default grants unless this is a `--continue`
launch, whose box is settled when it resolves) — or adopts the plan and box
the options already carry (a sibling's derived ones) — `Provider.
sandbox_prefix` prepends the wrapper (and the tab appends
`provider.session_flags` — the permission mode — behind a `--continue`
override), `_register_sandbox_box` tells the live grants
(`terminal.SANDBOX_GRANTS`) the box is up once the plan is settled,
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

A tab spawns the user's `$SHELL` (via `Vte.Terminal.spawn_async`) in the
session's resume cwd — the **last** cwd its transcript recorded, mapped back
through worktree recovery — with the environment from
`agent_environment()` (`service/session.py`; the app's env plus `ConEmuANSI=ON` and
`TERM_PROGRAM=kitty`, which are the two spoofs that make the CLI emit
ST-terminated OSC 9;4 progress that VTE parses; they fail soft). Then it
types the provider's command: `claude --resume <id>` (or `--fork-session`,
`--continue`, or a fresh `claude` with `--mcp-config`, `--model`, `--effort`,
`-w`), or `claude attach <job-id>` when `background_agents(include_finished=
True)` lists the session — the daemon refuses a plain resume for any id it
still lists. `attach` is not exclusive: a second client can attach to the same
job, which is how live sessions are probed without typing into them.

`Vte.Terminal.set_size(cols, rows)` before spawn reaches the child's winsize
even for a never-shown tab (background spawns mirror the visible terminal or
use 120x40). A never-selected tab never realizes; VTE still emits `bell` for it
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
force-closes at the tick budget (the window's `_close_confirmed`, handed
in). Once the CLI is gone `_poll_shell_exit` feeds `shellinput.shell_command("exit\r")` — the `" \x15"` line reset first,
because a shell inherits input the CLI never read (VTE mouse reports), and a
bare kill-line at column 0 rings the bell — and force-closes if the shell
ignores it. Keys fed to the *CLI* get no reset (raw-mode TUI). A `/bg` close
marks the row `backgrounding` (yellow, disabled) until the daemon lists the
job or a timeout, and `_watch_background_fork` handles older CLIs that fork.

Quitting: `_on_close_request` → `_begin_quit_flow` → `_confirm_quit` (close
all / background all in a queue / **hide** — the window hides, sessions keep
running, the status icon brings it back; `request_quit` bypasses hide).
`_close_ok` also covers the no-dialog paths. Finalizing a `Vte.Terminal`
SIGHUPs its child; a hidden window keeps every page alive with no
`Gio.Application.hold()`.

## Footer and chrome

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
stdlib only, not yet wired into the app) is the first piece. One
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

`collins/service/termscreen.py` (GTK-free, stdlib only, not yet wired into
the app) is the terminal of record of the split: one `Screen` per pty,
fed the tokens `termstream`'s tokenizer cuts (`apply(token)`; as the
`ScreenHook` of a `StreamFilter` it also answers CPR and DECRQSS from the
screen as it stands at the query). Every automated read the tab makes
today off VTE will be made of it once PR-1.7 swaps the backend:

- **Reads** (the `ScreenPort` of spec §3.5): `rows()`, `cursor()` (the
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

## The server backend: the tab on the pty server (ptyclient, loopback, core)

Since PR-1.7 of the split a session tab runs on one of two backends,
`ptyclient.PTY_BACKEND`, read once from `COLLINS_PTY_BACKEND` (`vte`, the
default; `server`, opt-in until PR-1.9 makes it the only one). On `vte`
nothing changed. On `server` (spec §3.4, swap 2 of §3.5):

- **The terminal has no child.** `TerminalTab.terminal` is a
  `ptyclient.ClientVte` (a `Vte.Terminal` that reports its own allocation,
  since GTK4 has no size-allocate signal) glued to a pty of the service by
  `ptyclient.ClientTerminal` (`tab._view`): output frames are `feed()`,
  every `commit` goes back as an input frame, the grid goes as a `resize`
  event on every allocation change, `notify::has-focus` as a `focus`
  event, `pty-exited` is what `child-exited` was (`tab._on_pty_exited` →
  `Session.shell_exited`; a status the service does not know is -1). The
  ports are `ptyclient.ServicePtyPort` (a write is an input frame, a
  resize `set_size` plus the event; the pid and foreground group read off
  the loopback's pty object) and `ServiceScreenPort` over the service's
  `termscreen.Screen` (`client.screen_of(pty)`): the model is the screen
  as anchored to the cursor already, so row indices are the model's own;
  `row_text` cuts a row's cells, `visible_text` joins on the wrap flags.
- **The spawn.** `_TabHost.spawn_shell` → `TerminalTab._service_spawn`: a
  `spawn` request (kind `agent`, the cwd, the VTE's grid), then
  `ClientTerminal.attach(pty)`, and `Session.shell_spawned` an idle later
  at `PRIORITY_DEFAULT` (VTE's spawn was asynchronous; the session's
  recreated-worktree landing now runs at `PRIORITY_DEFAULT` too). The
  **service** builds the shell's environment (its own, plus the two
  progress declarations when `progress_termprop` is on: `ServiceCore`'s
  `get_setting`); the session types the agent command exactly as before,
  the `sandboxrun.py` wrapper included. A refusal (`SpawnError`'s errno)
  is painted where VTE's spawn error was.
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
  comes down. `ClientTerminal.dropped_commits` counts what the guard
  swallowed (`check_attach_redraw.py` asserts it and that no sentinel
  answer reached the pty). A commit carrying NUL (Ctrl+Space, Ctrl+@)
  reaches `commit` as an empty C string of size 1 and is sent as `\0`
  (`ptyclient.commit_bytes`).
- **The mouse.** `mouserate.MotionCoalescer` (GTK-free,
  `tests/test_mouserate.py`): a plain motion report naming the cell of the
  one before is dropped, the rest coalesced to the latest per 30 ms on a
  timer in `ClientTerminal`; presses, releases, wheel and drags go at once
  and flush what was pending ahead of them.
- **`feed_message` is `paint`** (rule 2 of §3.1) once the pty is attached:
  the line goes into the stream on the service, so the model and every
  client show it; before the spawn or after the exit it is fed to the
  widget alone.
- **The tab's end.** `TerminalTab.release_pty()` (from
  `MainWindow._on_close_page`, the final close) sends `close` (mode
  `kill`: in Phase 1 every mode is SIGHUP plus the master closed, with
  SIGKILL after the grace), detaches and closes the loopback client:
  closing a tab ends the session (D12) until *Detach* in PR-1.12.
  `App.do_shutdown` shuts the loopback down, which saves every model and
  ends every pty (Phase 1: quitting ends the sessions).
- **The wiring.** `App._start_service_loopback` builds one
  `service.core.ServiceCore` (the pty half: `spawn`, `attach`, `detach`,
  `paint`, `close`; the `resize`/`focus`/`theme` events; the state's
  `set_pty` / `set_pty_next_id` as the pty table's writers) behind an
  `api.loopback.LoopbackServer` in `terminal.SERVICE_LOOPBACK`; a tab
  built without an app gets one made on the spot. The loopback passes
  the same dicts and bytes the socket will, every message through
  `protocol.validate` both ways, with no framing and no queue (a request
  is answered before it returns; every live frame is reported drained
  as the callback takes it). Two shortcuts the socket will not have
  (`screen_of`, `pty_of`) exist because the `Session` still runs in the
  client's process; they go with the loopback (D21, PR-1.12).
- **What differs, by design.** A row written before a resize is kept by
  the model as it was (no reflow, D19) where VTE re-wraps it: a shell's
  echo from before the tab's first allocation reads differently in
  `capture_contents` on the two backends until the program repaints
  (`check_attach_redraw.py` compares from the first line written at the
  settled grid for that reason). `capture_contents` is held to VTE's
  `write_contents_sync` by the goldens' `capture` read (three wrap
  scenarios in `scenarios.SYNTHETIC`, every scenario compared after
  `scenarios.normalise_capture`): soft-wrapped rows joined, and the rows
  `ED 3` blanked left out (VTE's text reads show them, measured in
  PR-1.2; its capture does not, measured in PR-1.7:
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
  run (server: lost in 7 of 9 runs; the same step on the `vte` backend,
  the drive's `--backend vte` control run: lost in 2 of 4, the key never
  seen by GTK either, delivered when it was). The key never reached GTK;
  the compositor's keyboard focus not having moved yet to the window the
  click activated is the likely cause (the toplevel reports
  `is_active()` False throughout under the headless shell). A harness
  that clicks a window for the first time and types in the same breath
  loses that key, on either backend.
- **Running the suite on it:** `python3 scripts/run_e2e.py --pty-backend
  server` (CI runs both backends as `e2e-shard (<backend>, N)`); a single
  check: `COLLINS_PTY_BACKEND=server … python3 scripts/check_x.py`.

## The service's pty server (ptyserver)

`collins/service/ptyserver.py` (GLib only, nothing from GTK; wired into the
app through `service.core.ServiceCore` and the loopback, used by every tab
on the server backend) is the table of terminals the service owns, one
`Pty` per agent session and, from PR-1.8, per panel shell (spec §3.3,
PR-1.5).

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
  that ignored it; `shutdown()` saves and closes everything, waits a
  bounded time for the saves in flight and writes any model still owed
  synchronously (a stopping service runs no more idle callbacks) (Phase 1: stopping the service ends
  every agent). A `Pty` implements the `PtyPort` of §3.5 (`write`,
  `resize`, `child_pid`, `foreground_pgrp`) for the `Session` of PR-1.7.
- **Attach and the redraw.** `attach(pty, sink, cols, rows)` sends
  `Screen.snapshot()` with the tracker's `preamble(screen=False)` in
  frames of at most `protocol.MAX_PAYLOAD`, every one flagged `REDRAW`,
  the last `REDRAW_END`, then live output with flags 0. The reply is
  `attach`'s: the grid the redraw was painted at, `active`, `sized_for`.
  A sink is any object with `send_output(bytes, flags)` and
  `send_event(dict)`; `drop_queued()` and a `device` attribute are
  optional; sinks are keyed by identity.
- **The active client owns the size** (D1): the last sink to type
  (`write(…, sink=)`) or take focus (`focus(…, True)`), the first to
  attach when none (a newcomer takes the role when nobody holds it, other
  sinks or not). Its resize applies (`TIOCSWINSZ`, the model, the filter's
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
  draining is redrawn every 4 MiB, logged once.
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
  exists for a live pty's re-adoption (PR-3.6).
- **The `ptys` table.** The server's `record(pty_id, row | None)` callable
  (`AppState.set_pty`, wired in PR-1.7) keeps a row per live pty in
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
