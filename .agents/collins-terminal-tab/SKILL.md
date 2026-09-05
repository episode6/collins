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

## Spawn, resume, attach

A tab spawns the user's `$SHELL` (via `Vte.Terminal.spawn_async`) in the
session's resume cwd — the **last** cwd its transcript recorded, mapped back
through worktree recovery — with the environment from
`_agent_tab_environment()` (the app's env plus `ConEmuANSI=ON` and
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
  write a beat later** (`_PROMPT_SUBMIT_MS`): a Return inside a chunk the CLI
  reads as a paste is a newline, not a submit. `inject_prompt_unfocused`
  wraps multi-line text in a bracketed paste (`_bracketed_paste`, stripping
  `\r` and any paste-end marker). A paste over 800 chars or more than two line
  breaks becomes a `[Pasted text #N +M lines]` stand-in in the box.
- `switch_model` / `switch_effort` post `/model <id>` / `/effort <level>`
  through `_post_switch`; the typed `/model` form **also saves the user's
  default** to `~/.claude/settings.json`. The footer's model and effort come
  from the transcript (`transcript.TranscriptModel.model()` / `.effort()`),
  never from settings.
- `feed_message()` paints into VTE directly (not into the pty): the CLI's box
  reads as unreadable until it redraws.
- `add_file_to_chat` types `@path#L2-4` (`Provider.file_reference`; whole lines
  only) with `dropimages.leading_space` deciding whether a space is needed.

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

`_graceful_close` feeds the provider's exit (`\x03\x03`) or `/bg\r`, hands the
screen to a neighbouring tab, and polls: `_poll_graceful` re-nudges at
`_EXIT_NUDGE_TICKS`/`_BG_NUDGE_TICKS` (a mid-turn agent spends the first
Ctrl+C Ctrl+C interrupting itself), answers the CLI's "keep or remove this
worktree?" dialog with keep (`worktree_exit_prompt_keystrokes`), and
force-closes at the tick budget. Once the CLI is gone `_poll_shell_exit`
feeds `shellinput.shell_command("exit\r")` — the `" \x15"` line reset first,
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
overflow into an ellipsis menu), the composer/attachments/git/editor buttons
and footer apps. The cwd tick also drives `_maybe_follow_editor` (the editor
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
