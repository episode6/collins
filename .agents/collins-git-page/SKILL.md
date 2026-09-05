---
name: collins-git-page
description: >-
  How Collins' git page works: hunk (hunk.dev, the terminal diff viewer)
  running in a VTE beside the session and driven over its session API
  (gitpage.py), the native commits and files sidebar with its action row
  (gitsidebar.py over the GTK-free gitmodel.py and gitops.py), the GTK-free
  decisions in hunkctl.py (argv, version gate, session lookup by pid, titles,
  the sidecar, show_diff), the slim collins-git hunk extension shipped as
  package data (TypeScript in collins/hunkext/collins-git: the five keys at
  hunk's cursor, line ranges, the sidecar v2 contract), Preferences → Git,
  the parent-branch rule, freshness reloads, and gitinfo.py's cheap .git
  reads for the footer branch. Use when changing the git page, the sidebar,
  the extension, the show_diff tool's page-driving half, git-branch
  detection, or debugging "clicking a commit does nothing" / a stranded hunk
  viewer.
---

# The git page

## Shape

`GitPage` (`gitpage.py`) is a `PanelPage` (`page_kind="git"`; opened by F6,
the footer's git button, a click on the footer's ⎇ branch label, or the
`show_diff` tool; the button fires `win.toggle-git`, so it closes the page
too when the cursor is in it and its tooltip carries the F6 hint, while the
label only ever opens, via `TerminalTab.open_git_page`) holding a VTE that runs
`hunk diff --watch --transparent-bg --no-sidebar --extension <collins-git>
[--mode …] [--theme …] [--exclude-untracked]` in the agent's working tree,
under a one-row header: the branch, a breadcrumb of what is loaded, the
sidebar toggle, and refresh. `--no-sidebar` (hunk 0.21, hence `MIN_VERSION`)
hides hunk's own files pane: the review stream is all hunk draws. Beside the
VTE, to its left, sits the native sidebar (`gitsidebar.GitSidebar`, see
below) in a `Gtk.Paned` inside an `Adw.BreakpointBin` (`max-width: 679px`
→ the sidebar hides and the toggle goes insensitive; the bin's 460 px
request is the page's real minimum, `column_floor` / `column_seed` 680 and
~700 are what the dock opens it at). The whole page sits in an
`Adw.ToastOverlay` for its own toasts. A machine without hunk (or with one
older than the version gate) gets an install card linking to hunk.dev with a
"Check again" button — never an error. `hunkctl.ProbeCache` probes `hunk
--version` at most every 30 s; `App._refresh_hunk_probe` also gates the
`show_diff` tool's presence in the MCP list. The tab's glyph is
`gitpage.ICON` (`git-merge-symbolic`), public because the footer button wears
it too.

**Loads go through hunk's session API, not respawns.** After spawning, the
page resolves the viewer's session id from `hunk session list --json`,
matching `pid in proctree.process_children(child_pid)` — `/usr/local/bin/hunk`
is an npm wrapper (`bin/hunk.cjs`) that `spawnSync`s the real viewer, so VTE's
child pid is the wrapper and hunk reports the child. Then Ctrl+1/2/3 (unstaged
/ staged / branch) and any host request run `hunk session reload <id> --json
-- diff …` or `-- show <ref>`, swapping the contents in place. Only a reply
whose stderr says the session is gone (`hunkctl.session_gone`: "No active
session(s)…") triggers a respawn; a refused load ("could not resolve Git
revision") leaves the healthy viewer alone. When `_resolved` gives up on
`session list` (retries at `RESOLVE_DELAYS_MS`), every switch is a respawn
and an `Adw.Banner` over the viewer says the viewer never registered with
the daemon, names `hunkctl.DAEMON_DIAGNOSTIC` (`hunk daemon serve`) as the
run that prints why, and offers **Retry** (`_respawn`); the banner hides on a
successful resolve, a card, or the child exiting. The three modes plus
`{"show": ref}` and `{"range": "a...b"}` are the `Loaded` value;
`hunkctl.breadcrumb` / `tab_title` / `loaded_from_title` map between them
and hunk's own session titles (a two-dot range or a pathspec stays
foreign: shown by title, never reloaded). `_apply_title` also hands the
sidebar its context and, for a `show`, the full sha the worker read
(`_title_subject_and_sha`) so the ▸ row matches `show HEAD`.

**Closing** must kill the process **group** VTE started
(`hunkctl.terminate_tree`, `gitpage._shutdown`; `app.do_shutdown` calls
`gitpage.shutdown_all()`): SIGTERM to the wrapper leaves the viewer alive, and
a viewer whose pty is gone ignores SIGTERM/SIGINT. The auto-spawned `hunk
daemon serve` is in its own group and rightly survives.

**Freshness.** hunk's `--watch` covers file edits; commits and staging done
from a shell or by the agent are caught by the tab footer's 2 s tick
forwarding `poll_tick` while the page is mapped: `gitinfo.tree_signature`
(index mtime, HEAD sha, parent ref) changed → reload what is shown, and a
`remote_refs_signature` move refreshes the `↑` marks. A move the extension
made itself (`x`, `X`, `D`, reloaded through hunk's own `hunk.app.refresh`)
is recorded in the sidecar (`refreshed`: index mtime + HEAD) and skipped, or
the reload would land on the dialog the user opened next (hunk cancels
dialogs on any reload). A native mutation from the sidebar (`mutated`)
re-seeds the signatures and reloads at once.

**Parent branch.** `TerminalTab._git_parent_branch`: an attached open PR's
base, else the `git_parent_branch` setting (`origin/x` read as `x`), else the
repository's default branch (`gitinfo.default_branch`: `refs/remotes/*/HEAD`,
then local `main`/`master`, loose or packed — no subprocess; a `git init`
repo has no remote HEAD). The user overrides with the sidebar's ⎇ button
(`parent-picked` → `GitPage._on_parent_picked`, which re-seeds the tree
signature so the tick reads no move in the new base); "Automatic" hands the
choice back. The pick persists in the page's layout slot
(`hunkctl.encode_state` / `decode_parent`), beside `"sidebar": false` when
the sidebar is folded (`decode_sidebar`). The extension never sees the
parent.

## The native sidebar (`gitsidebar.py`)

`GitSidebar(Gtk.Box)`: a vertical `Gtk.Paned` of two `Gtk.ListBox`es
(commits over files, `navigation-sidebar` style, section headings as
non-selectable `caption-heading` rows) and a wrapping `Gtk.FlowBox` action
row. It never imports `gitpage`; the page feeds it and listens:

- Feed: `set_context(branch, parent, default, loaded, resolved_sha,
  live_side, hunk_alive, extension_loaded, auto_parent)` after every
  `_apply_title` / probe / exit (a changed branch, parent or default
  re-reads the commits; returns whether it did), `refresh_commits()`
  (threads: `gitops.read_page` per group + `unpushed_shas`, landed behind
  a generation), `refresh_files(files, loaded, untracked)` (the page calls
  it only when `(files, loaded, untracked)` changed or the tree moved —
  `_files_shown` / `_files_stale` — since a working-tree load costs a `git
  status`), `set_selection(path, hunk, source)` (the sidecar's word beats
  the `session get` snapshot within a tick, `_sidecar_selection_seen`),
  `set_anchor`, `set_options` (page size → re-page; untracked → redraw).
- Signals: `load-requested(Loaded)` → `load()`; `navigate-requested(path,
  side)` → `_navigate` on the live side (or the flat list), else
  `_pending_navigate = (path, side)` + `load(side)`, run when the reload
  lands with that side (`settled()` waits for both); `key-requested(bytes)`
  → `feed_child` + `terminal.grab_focus()` (hunk's `D` confirm answers to
  Enter); `mutated` → re-seed the signatures, refresh, `_reload` now;
  `parent-picked(name | None)`.
- Native mutations (`commit`, `fixup`, `stage_all`, `unstage_all` — public
  so the e2e drives them without dialogs) run on a thread behind `busy`
  (the Commit button spins, the other mutations go insensitive) and toast
  through the nearest `Adw.ToastOverlay` (`set_use_markup(False)`: titles
  carry commit summaries). The button handlers gate first
  (`in_progress_operation`, `staged_paths`) and ask through
  `dialogs.commit_dialog` / `choice_dialog` / `confirm_dialog(destructive=
  False)`. The e2e reaches rows with `click_commit_row(id)`,
  `click_file_row(path, side)`, `click_section(side)` and reads
  `commit_rows()`, `file_rows()`, `loaded_row_id()`, `selected_path`,
  `anchor_button_label()` / `stage_button_label()`.
- The cursor buttons (`x`, `v`/escape, `D`) are hidden — not merely
  insensitive — when hunk runs without the extension
  (`hunkctl.extension_dir()` None: a broken install), and insensitive off
  a live working tree or a dead hunk.

The GTK-free halves: `gitmodel.py` (ports of the old extension's
`model.ts` / parsers: `parse_log` over `LOG_FORMAT`, `parse_status_v2`,
`build_rows`, `loaded_row_id`, `files_sections`, the confirm/toast words;
every subject, path and list bounded) and `gitops.py` (argv builders and
runners that take `run=subprocess.run` and never raise: `read_page` with
the limit+1 trick, `unpushed_shas` — `HEAD --not --remotes`, empty without
a remote-tracking ref — `read_status`, `local_branches`, `staged_paths`,
`in_progress_operation` on `gitinfo.git_dir`, `commit`, `commit_fixup`,
`stage_all`, `unstage_all`, `unpushed_in_group`, `resolve_group_branches`).
`hunkctl` carries what they lean on: the fifth `Loaded`, `{"range":
"a...b"}` (`is_range`, `range_halves`; three dots between two safe refs —
`loaded_from_title` names it, two-dot ranges stay foreign),
`Session.files` / `selected_path` / `selected_hunk` parsed off `session
get`'s `files[]` (hunk 0.21.1's `fileSummarySchema`: id, path,
previousPath?, additions, deletions, hunkCount — a binary change lists
0/0/0, there is no binary flag; capped at `MAX_SESSION_FILES`) and
`snapshot.state`, and `commit_subject_and_sha` (one `git log -1
--format=%s%x00%H`).

## The sidecar contract (`COLLINS_GIT_STATE`, version 2)

A JSON file under `$XDG_RUNTIME_DIR` (`hunkctl.sidecar_path`), created before
spawn and deleted on close, read-merge-written by both sides (temp file +
rename, unknown keys pass through, garbled file tolerated), stamped
`"version": 2` (`hunkctl.SIDECAR_VERSION`) by whoever writes:

- Collins writes `untracked` (from `git_untracked`) — `hunkctl.
  sidecar_payload(untracked)`, all of it. Informational to the extension,
  which reloads through hunk's own refresh; it says what Collins' loads
  hold.
- The extension writes `selection` (`{"path", "hunkIndex": n | null}` or
  `null`) on every `selection_changed` and changeset event when it
  changed, `anchor` (`{"path", "side": "old"|"new", "line"}` or `null`) on
  set / clear / a reload that lost it, and `refreshed` (`{"index": "<mtime
  ns as a string>", "head": "<sha>"}`) after its own reloads. Readers:
  `hunkctl.read_sidecar_selection` / `read_sidecar_anchor` /
  `read_sidecar_refreshed`, every field shape-gated.
- Dropped with version 1: `parent`, `parentSource`, `default`, `logPage`,
  `level` (they fed the panes the extension no longer draws). A v1 file's
  keys are ignored by both sides.

Collins reads the file on the tick (one `os.stat`, a read when the mtime
moved past its own last write) **and** from a `Gio.FileMonitor` on the path
(`GitPage._on_sidecar_changed`: the extension renames a temp file over it,
which the monitor reports as `CREATED`), so hunk's cursor moves the files
list's highlight as it lands. The extension reads `untracked` only. Without
the env var it runs standalone: the same keys, nothing written.

**Pinned keys.** `hunkctl.STAGE_KEY` `x`, `STAGE_FILE_KEY` `X`,
`ANCHOR_KEY` `v`, `CLEAR_ANCHOR_KEY` escape, `DISCARD_KEY` `D` — what the
sidebar's buttons feed the pty. hunk's CLI can't run an extension command
by name, so a user who rebinds one under `[keybindings]` finds the buttons
still pressing these bytes; `tests/test_hunkctl.py` greps `index.ts`'s
`registerCommand` keys for exactly them.

Preferences → Git: layout (`--mode` auto/split/stack) and theme go on the
spawn argv (a change restarts hunk in place — nothing in the session API
changes them); untracked goes on every diff tail and into the sidecar; page
size pages the native commits list (`GitSidebar.set_options`). `prefslayout`
pins hunk's own `--mode` words so the two sides can't drift.
`hunkctl.Options.from_settings` normalises the settings dict; `safe_theme`
gates the theme string.

## The extension (`collins/hunkext/collins-git`)

TypeScript hunk transpiles itself — no build step, no `node_modules` shipped;
package data is `package.json`, `README.md`, `*.ts` (pyproject;
`scripts/verify_wheel_data.py` derives the list from the directory). Its
README is the authoritative key and contract reference; the short version:
`index.ts` registers five commands — `x` stage/unstage the hunk or the
anchored range, `X` the file, `v` anchor, `escape` clear, `D` discard (or
restore a deleted file) after hunk's own confirm — and the `range-anchor`
line highlighter, keeps the changeset's files and `decodeTitle`'s side on
`startup` / `changeset_loaded` / `session_reload`, writes `selection` on
`selection_changed` (by path, since ids renumber on every reload) and
`anchor` on set / clear / rebind, and after a mutation runs
`ctx.commands.execute("hunk.app.refresh")` then `recordRefreshed()` — with
no refresh available it writes nothing and Collins' tick reloads. No panes,
no store, no `SIGWINCH` or width poll, no `session reload` of its own, no
commit / fixup / stage-all / parent keys. `staging.ts` / `patch.ts` /
`range.ts` / `anchor.ts` are the staging arithmetic (partial patches applied
with `--unidiff-zero`, mode changes left to `X`, ranges refused across
files or when the file changed since the anchor); `git.ts` the runner and
the handful of git calls (`readFilePatch`, the applies, `stageFiles` /
`unstageFiles`, `restoreFile`, `treeMark`); `model.ts` `decodeTitle` (the
two working-tree sides, everything else read-only); `sidecar.ts` the v2
file. It never writes to the terminal; `COLLINS_GIT_DEBUG_LOG=<file>` logs
what it does.

Tests: `bun test` in that directory (no `bun install` needed — modules import
only `node:*`, each other and erased `import type`s; CI installs bun 1.4.0;
`test/support/status.ts` is the status parser the integration tests read the
index back with). `index.ts` is not imported by any test: typecheck it
(`bun install && bun run typecheck`, or a scratch tsconfig with a `paths`
mapping of `hunkdiff/extension` onto the installed hunk's `.d.ts`) after
touching it. `bun.lock` is gitignored.

## gitinfo (`gitinfo.py`, GTK-free)

Cheap reads straight from `.git` for the footer's 2 s poll and every
right-click: `current_branch`, `default_branch`, `github_url`, `repo_root`,
`index_mtime`, `head_sha`, `resolve_branch`, `base_ref`, `tree_signature`,
`remote_refs_signature` (mtimes of `packed-refs` and every directory under
`refs/remotes`, so a push moves it), `git_dir`, `parent_branch`. Anything
that needs `git` (`has_changes`, `change_summary`, `ignored_names`) shells
out and is asked on demand only.

## Footguns

- **hunk 0.21 refuses its daemon when `$XDG_RUNTIME_DIR/hunk-mcp` is not
  owner-only** (0.20 created it with the umask; 0.21 creates it 0700 itself).
  Symptom: `hunk session list --json` answers `{"sessions": []}`, a commit
  click toasts "cannot find this hunk window in the session daemon", the
  page stays on the working tree, and the auto-spawned daemon's stderr is
  swallowed. `hunkctl.repair_daemon_dir(runtime_dir)` runs on the probe
  thread before every spawn and chmods the directory (`DAEMON_DIR`,
  `DAEMON_DIR_MODE`) when group/other bits are set — it never raises and
  returns `absent` / `ok` / `repaired` / `failed`; the page only logs the
  outcome. It cannot revive a viewer launched while the daemon was dead
  (that viewer never reconnects), which is what the banner's Retry is for;
  kill daemons born before the repair if the banner persists.
- Matching the session by `pid == child_pid` finds nothing (wrapper vs
  viewer); use the children set.
- **Feeding `D` needs the VTE focused.** hunk's confirm answers to Enter in
  the terminal; `_on_key_requested` grabs the VTE's focus after every fed
  key, and a probe that feeds bytes without it leaves the dialog waiting.
- **hunk's `s` re-opens its own pane.** `--no-sidebar` is a startup flag:
  the user's `s` pops hunk's files pane inside the VTE beside the native
  one, and a `session reload` keeps whatever `s` did. Not blocked, only
  documented.
- Two tabs on one worktree make `--repo` ambiguous; pid is the only key.
- Hunk cancels any open dialog on reload; a shell-side commit while a `D`
  confirmation is up closes it — by design, press the key again.

Related: `collins-session-mcp-tools` (`show_diff`), `collins-panel-dock`,
`collins-terminal-tab`.
