---
name: collins-git-page
description: >-
  How Collins' git page works: hunk (hunk.dev, the terminal diff viewer)
  running in a VTE beside the session and driven over its session API
  (gitpage.py), the native commits and files sidebar with its action row
  (gitsidebar.py over the GTK-free gitmodel.py and gitops.py), the GTK-free
  Loaded vocabulary in gitloads.py (modes, commit and range loads, safe
  refs, breadcrumb and tab titles, the chords, the layout slot) and the
  hunk-side decisions in hunkctl.py (argv, version gate, session lookup by
  pid, session titles, the sidecar, show_diff), the slim collins-git hunk
  extension shipped as
  package data (TypeScript in collins/hunkext/collins-git: the five keys at
  hunk's cursor, line ranges, the sidecar v2 contract), the experimental
  native diff view behind the temporary git_viewer switch (diffview.py over
  diffmodel.py: the page's native load path, the sidebar following the view,
  the files filter, the find bar, the page-local git.* chords, the file
  monitors' watch), Preferences → Git,
  the parent-branch rule, freshness reloads, and gitinfo.py's cheap .git
  reads for the footer branch. Use when changing the git page, the sidebar,
  the diff view, the extension, the show_diff tool's page-driving half,
  git-branch detection, or debugging "clicking a commit does nothing" / a
  stranded hunk viewer / a diff key reaching the terminal.
---

# The git page

## Shape

The page has two faces behind the temporary `git_viewer` setting: hunk in a
VTE (this section, today's default) or the native `DiffView` (the section
"The native viewer behind `git_viewer`" below — the same header, sidebar,
loads, breadcrumb and persistence, a different review stream). What
follows describes the hunk face; every hunk-only path is gated on
`GitPage._native`.

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
`{"show": ref}` and `{"range": "a...b"}` are the `Loaded` value
(`gitloads.py`, see below); `gitloads.breadcrumb` / `tab_title` and
`hunkctl.loaded_from_title` map between them
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
(index mtime, HEAD sha, parent ref) changed → reload what is shown, and
either that or a `refs_signature` move (`refs/heads` and `refs/remotes`
directory mtimes, `packed-refs`: a branch made, deleted or committed to in
another worktree, a push) → `_refresh_branch_stack` (below), which re-reads the
commits list when it lands. A move the extension made itself (`x`, `X`,
`D`, reloaded through hunk's own `hunk.app.refresh`) is recorded in the
sidecar (`refreshed`: index mtime + HEAD) and skipped, or the reload would
land on the dialog the user opened next (hunk cancels dialogs on any
reload). A native mutation from the sidebar (`mutated`) re-seeds the
signatures, re-reads the stack and reloads at once.

**Parent branch and the stack.** Git is the source of truth: `gitops.
stack_branches(cwd, trunk)` — `for-each-ref refs/heads` tips intersected
with `rev-list --topo-order <trunk>..HEAD` (capped at `MAX_STACK_WALK`),
HEAD's own commit dropped — lists the local branches on the current
branch's history since the default branch, nearest first. The page reads
it on the spawn's probe thread and on a thread after every move
(`_refresh_branch_stack` → `_branch_stack_read`, generation-guarded; `_branch_stack` is cleared
on a branch change), keeps it in `_branch_stack`, and `_resolve_parent` takes the
first entry that still resolves as the parent; `_branches_below_parent` is
what the sidebar groups. Only with no stack does the host's rung name the
parent — `TerminalTab._git_parent_branch`: an attached open PR's base, else
the `git_parent_branch` setting (`origin/x` read as `x`), else the
repository's default branch (`gitinfo.default_branch`: `refs/remotes/*/HEAD`,
then local `main`/`master`, loose or packed — no subprocess; a `git init`
repo has no remote HEAD, and without a default there is no trunk to walk
from, so no stack). A stack read that moves the parent re-seeds the tree
signature (the base changed, not the tree) and reloads a branch diff. There
is no picker and no persisted parent: the layout slot is `gitloads.
encode_state(loaded, sidebar)` (`"sidebar": false` when folded,
`decode_sidebar`); a `"parent"` key from older layouts is ignored. The
extension never sees the parent.

## The native sidebar (`gitsidebar.py`)

`GitSidebar(Gtk.Box)`: a vertical `Gtk.Paned` of two `Gtk.ListBox`es
(commits over files, `navigation-sidebar` style, section headings as
non-selectable `caption-heading` rows) and a wrapping `Gtk.FlowBox` action
row. It never imports `gitpage`; the page feeds it and listens:

- Feed: `set_context(branch, parent, default, stack, loaded, resolved_sha,
  live_side, hunk_alive, extension_loaded)` after every `_apply_title` /
  probe / exit / stack read (a changed branch, parent, stack or default
  re-reads the commits; returns whether it did), `refresh_commits()`
  (threads: `gitops.read_page` per group — the current `<parent>..HEAD`,
  then the parent and each stack branch as `<below>..<branch>`
  (`gitmodel.stack_ranges`), the default — plus `unpushed_shas`, landed
  behind a generation; group ids are `current`, `stack:<name>`, `default`,
  and `_pages` is keyed by them), `refresh_files(files, loaded, untracked)` (the page calls
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
  Enter); `mutated` → re-seed the signatures, re-read the stack, `_reload`
  now.
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
a remote-tracking ref — `stack_branches`, `read_status`, `staged_paths`,
`in_progress_operation` on `gitinfo.git_dir`, `commit`, `commit_fixup`,
`stage_all`, `unstage_all`, `unpushed_in_group`, `resolve_group_branches`).

**The native diff view's GTK-free half** (landed ahead of the view, spec
`~/specs/collins/native-diff-panel.md`): `diffmodel.py` parses a whole
`git diff` / `show` stream into `File`s / `Hunk`s / `Line`s (every header
form, binary and too-large placeholders, gaps, split rows, word emphasis,
the palette blends, `stable_key` for reload matching); `gitpatch.py` is the
extension's staging arithmetic ported (partial-patch writers, `plan_file` /
`plan_hunk` / `plan_lines` → a `Plan` or a `Refusal`, the confirm and toast
words; `parse_file_patch` answers only the stanza whose path was asked
for and `_same_file` re-checks it before a patch is written, so a re-read
can never plan another file's change under this file's confirm, and
`_became_rename` refuses stale — the view reloads — when the fresh stanza
is a rename the shown file was not, in `plan_hunk` as in the shared
`_guard_partial`, since a partial patch keeps the `rename from` / `rename
to` lines and `git apply --cached` would move the file whole; the
planners take `dirty` — the page must pass whether `DiffRead.status`
lists the path under unstaged — and a revert's confirm then opens with
`revert_warning`, the spec's "may conflict" sentence); and `gitops.py`
grew the reads and runs they need — `read_diff(cwd,
load, parent_target, untracked, pathspecs)` → `DiffRead(files, status, ok,
error)`: hunk's own argv (`diff_argv` / `show_argv` behind
`DIFF_PREFIX_ARGS`, the `-c` options pinning `a/` `b/` so the patch applies
back under any `diff.noprefix`), a `numstat_argv` pre-pass whose over-cap
paths are excluded (`:(exclude,literal)`) and stood in as `KIND_TOO_LARGE`
placeholders, `git status` for the working-tree loads, and the untracked
files synthesized one `untracked_diff_argv` (`diff --no-index -- /dev/null
path`, exit 1 is the answer) at a time, at most `MAX_UNTRACKED_DIFFS`;
`file_patch` (the re-read every mutation starts from), `file_at(cwd, ref,
path)` → bytes (`side_ref` names the ref per load and side: `INDEX_REF`
`""` is `:path`, `None` is the disk), `merge_base`, `apply_patch(cwd, patch,
cached, reverse, three_way)` → `ApplyResult` (the `--3way` retry only when
asked, flagged, `conflicts` when it left markers), `stage_paths` /
`unstage_paths` / `checkout_paths` behind `safe_path`, and
`tree_state_signature` (status + numstat + the size and mtime of every
working-tree path status lists, `MAX_STAT_PATHS`, hashed) for the watch.
Footguns:
every one of these runs from `gitinfo.repo_root` (`_root`) — from a
subdirectory git reads pathspecs against the cwd and `apply` silently
skips paths outside it; every path after `--` goes on as
`:(literal)path` (`literal_pathspec`) because git reads a bare pathspec
as a glob — `foo[1].txt` names foo1.txt too, and a confirmed
`checkout -- foo[1].txt` discarded the twin's changes (the `--no-index`
untracked read takes filesystem paths and stays bare); patch reads are
binary (`run_git_bytes`) because
`text=True` folds CRLF and the patch then matches nothing; `--3way` implies
`--index`, so a three-way revert also stages, and a conflicting one exits 1
having changed the tree.
**`gitloads.py` is the `Loaded` vocabulary**, split out of `hunkctl` so
the native view can load the same things without the viewer's module:
`MODES` / `DEFAULT_MODE`, `SHOW_KEY` / `RANGE_KEY`, `safe_ref` (the one
rule for a ref that goes on an argv or into a title), `is_show` /
`show_ref`, the fifth `Loaded` `{"range": "a...b"}` (`is_range`,
`range_halves`, `range_of`; three dots between two safe refs, two-dot
ranges stay foreign), `loaded_ok`, `short_ref`, `breadcrumb` /
`tab_title`, `load_for_key` (Ctrl+1/2/3 as integers), `initial_mode`,
`encode_state` / `decode_state` / `decode_sidebar`, the `show_diff` tool's
`show_diff_load` and `diff_file_path`, the git calls behind a commit's
name (`commit_subject`, `commit_subject_and_sha` — one `git log -1
--format=%s%x00%H` — and `resolve_commit`, `GIT_TIMEOUT_S`), `Options.
from_settings` with `LAYOUTS`, the `LOG_PAGE` bounds, `safe_theme` /
`MAX_THEME_LEN` (the theme goes with decision 3 of the native-diff spec,
not before), and `MAX_PATH_CHARS`. **`hunkctl` re-exports every one of
those names** (redundant `X as X` aliases, so ruff reads them as
re-exports; `tests/test_hunkctl.py` pins the identity), so the widgets
have not moved yet — the GTK-free half (`gitops`, `gitmodel`, `gitpatch`)
and any new code import `gitloads` directly; `gitmodel` still takes
`hunkctl.SessionFile`, which is hunk's. `hunkctl` itself keeps what is
hunk's: `loaded_from_title` / `title_tail` / `foreign_tab_title`,
`Session.files` / `selected_path` / `selected_hunk` parsed off `session
get`'s `files[]` (hunk 0.21.1's `fileSummarySchema`: id, path,
previousPath?, additions, deletions, hunkCount — a binary change lists
0/0/0, there is no binary flag; capped at `MAX_SESSION_FILES`) and
`snapshot.state`, the argv, the probe, the sidecar and `terminate_tree`.

## The native viewer behind `git_viewer` (experimental; PR 2 of the stack)

`git_viewer` (`state.DEFAULT_SETTINGS`, `"hunk"` | `"native"`, Preferences
→ Git → *Diff viewer*, `gitloads.Options.viewer` / `.native`) is a
**temporary** switch: PR 4 of `~/specs/collins/native-diff-panel.md`
deletes it with every hunk path. While it lives, `GitPage` has two faces
and every hunk-only method is gated on `self._native`; the native half is
what survives, the gates are what goes.

**The page's native branch (`gitpage.py`).** The stack has a third child,
`_NATIVE`, holding one `diffview.DiffView`. `_ensure_spawned` / `_spawn` /
`_respawn` dispatch to `_native_open` (a thread reads the stack and a saved
commit's subject, like `_probed`, then `_native_opened_cb` shows the view
and loads), `_native_close` (orphan the read, drop the monitors, empty the
view) and `_native_load(loaded)`: one `gitops.read_diff` on a daemon
thread behind `_gen`, plus `commit_subject_and_sha` for a commit, the
`merge_base` a branch / range reads its old side at, and
`tree_state_signature` for a working-tree load — landing in
`_native_loaded` at `PRIORITY_DEFAULT`, one read at a time with a newer
ask parked in `_native_pending`. The breadcrumb and `set_context` come
from the `Loaded` + that sha (no title parsing); the files list from the
read (`_session_files` builds `hunkctl.SessionFile`s so `gitmodel.
files_sections` is unchanged; `GitSidebar.refresh_files(..., status=)`
takes the read's own `git status` and draws at once); the context reader
handed to the view is `gitops.side_bytes(cwd, load, side, path,
previous_path, parent_target, merge_base)` (None for a side that names
nothing — a branch with no parent — *except* the unstaged new side, which
is the disk). `settled()`, `load()`, `refresh()`, `poll_tick`
(`_native_tick`), `_on_mutated`, `_navigate` (→ `DiffView.reveal`,
synchronous; a miss toasts), `apply_settings`, `_after_unrealize`,
`_on_child_exited` and `holds_escape` all have the native branch.
`_set_native` flips live: to native, a running hunk is terminated and its
exit opens the view (`_on_child_exited`), a spawn in flight is orphaned;
to hunk, the view closes and hunk spawns if mapped. `GitPage.native`,
`.diff_view` and `.reveal(path, hunk, side, line)` are the public face
(`app._ShowDiff` reveals through it and replies without a session id). A
`line` no hunk carries (an unchanged stretch; `diffmodel.locate` misses)
lands on the file's nearest hunk (`diffmodel.nearest_hunk`) and reveal
still answers True — the file *is* in the diff, which is what the hunk
path always did — and `DiffView.holds_line` lets the tool's reply say
the line itself is not in a changed region.

**The sidebar follows the view.** `DiffView`'s `current-changed(path,
hunk)` — the file at the top of the viewport (60 ms after the scroll
settles) or the hunk the keyboard moved into — → `GitSidebar.
set_selection(path, hunk, "view")`. A files-list click → `reveal(path)`;
the working tree's other side loads first with `_pending_navigate`, as
with hunk. The **files filter** is a `Gtk.SearchEntry` above the files
list (`set_filter_shown` — native only; hunk's own `/` filters in its
terminal), `filter-changed(str)` → `DiffView.filter` hides sections
(`set_visible`, not destroyed) and the rows hide too; Escape clears and
`filter-escaped` puts the keyboard back in the view. A `GtkSearchEntry`'s
`search-changed` is **debounced** (~150 ms): a probe that sets the text
must `wait_for` the words to land.

**Keys.** `keybindings.GROUP_GIT` (`git.*`): `]` `[` next/prev hunk, `.`
`,` file, `}` `{` annotated hunk (a view with marks — `_HunkView.marks`,
the notes/highlights hook), `z` expand the gap above the focused hunk
(all of it; one way), `0` `1` `2` layout, `l` line numbers, `w` wrap, `a`
notes shown (a flag until the cards land), `r` reload, `/` the filter,
`Ctrl+F` find, `?` Keyboard Bindings, `e` open in the editor at the cursor
line (`win.open-in-editor (sii)`, 1-based line), `q` close (`win.
toggle-git`). The mechanism copies the editor's `editor.*` row:
`keymap.shortcut_controller(custom, "git", …)` in `DiffView.
apply_keybindings`, scoped `LOCAL` **on the view** (spec) and in the
**CAPTURE** phase — a bare letter must beat the `GtkSource.View` under it
(the editor uses BUBBLE because its chords are Ctrl chords); the
`NamedAction`s resolve to a `Gio.SimpleActionGroup` inserted on the *page*
under `git` (an ancestor's groups are found from the view). Nothing
editable lives inside the view today; the note editor PR 3 adds must
disable the letter actions while it has focus, or `e` types nothing.
`keybindings.LOCAL_PREFIXES` / `may_overlap`: `editor.*` and `git.*` are
page-local scopes that never see one press, so `Ctrl+F` in both is not a
conflict (`conflicts` / `holders` skip such pairs; the dialog too). The
stateful actions `git.layout` (`s`), `git.line-numbers` / `git.wrap` (`b`;
a parameterless activate toggles a boolean-stateful `GSimpleAction`, so
one action serves the key and the menu check) write their setting through
**`win.git-option (sv)`** (window.py: an allowlist of the three keys →
`state.set_setting` + `apply_preferences`, so every page follows); with no
window action to reach — a page in a bare test window — `_write_option`
applies the dict to the page alone. `_sync_action_states` mirrors
`apply_settings` back into the menu's checks. `Ctrl+1/2/3` stay on the
page's raw capture controller.

**Find.** `Gtk.SearchBar` under the header (no `key_capture_widget`: typing
in the page must not open it — the letters are the view's), a toggle in
the header bound to `search-mode-enabled`. `DiffView.search(text)` /
`search_step(forward)` / `search_position()` / `search_clear()`: matches
are counted **in Python** over the hunk views' `rows` (`re.escape`,
IGNORECASE; split reads the old view for deletions + context and the new
for additions, so a context line counts once; capped at
`MAX_SEARCH_MATCHES`), selected with `select_range` and scrolled to
(`keyedslots.scroll_to` the hunk, `scroll_to_iter` within it), the
sidebar following through `_set_current`. A `GtkSource.SearchContext` per
buffer (weakly keyed by `_HunkView`) paints the highlights only — the
editor found the sync `forward()` misses matches before the index is
built, and async across dozens of buffers can't give "3 of 12". Matches
are re-counted (position kept) on every `load`, `filter` and layout
change; the page re-reads the label (`_sync_search_label`). Closing the
bar focuses the current match's view (`focus_search_match`).

**Watch mode.** After a working-tree load lands, `_install_monitors`
puts a `Gio.FileMonitor` (`monitor_directory`) on each distinct directory
of the loaded files (old paths of renames too) — over `MAX_DIR_MONITORS`
(64), or with no file at all, the repository root alone; a commit / range
load gets none. Events (not for the `.git` entry itself) debounce
`_WATCH_DEBOUNCE_MS` (300) into `_watch_check`: `gitops.
tree_state_signature` on a thread, compared against the state the load's
own worker seeded (`_tree_state`); a move is a `_native_load` by key (the
view keeps the scroll and an untouched hunk's widget and focus). One
compare at a time, and none beside a read in flight: either marks the
event `_watch_stale`, and the compare (`_watch_checked`) or the read
(`_native_loaded`, after re-making the monitors, which clears the mark)
re-runs `_watch_check` when it lands — before that, an edit written while
`read_diff` ran was never drawn: the event was dropped and the worker
sampled the signature *after* the read, so it already covered the edit.
The worker now samples it **before** `read_diff` (an edit between the two
costs one reload by key on the next compare, harmless).
`_native_tick` re-compares every `_WATCH_SLOW_TICKS` (5) ticks regardless.
The 2 s tick's `tree_signature` still covers index / HEAD / refs moves.
The signature hashes `git status` **and** `numstat` **and** the size +
mtime of every path on the working-tree side: an edit that rewrites an
already-changed line moves neither the letter nor the counts and went
unnoticed until the stats were added — a `touch` now costs one reload by
key, which keeps every widget. Measured: the monitor fires at once, the
compare lands ~20 ms after the debounce, the reload ~40 ms later (0.38 s
edit-to-view on the e2e's fixture).

**Measured (PR 2, this machine, headless).** `read_diff` + `DiffView.load`
of PR 500's squash (`git show 449fc98`: 16 files, 76 hunks, 3118 patch
lines): `read_diff` 23 ms; `load()` 335 ms ≈ **108 ms per 1000 patch
lines**; first paint 1146 ms ≈ **370 ms per 1000 patch lines** (the rest
is GTK's first allocation and GtkSourceView's highlighting); a reload
keeps 76/76 hunk widgets, a changed hunk rebuilds only itself; split under
wrap: 0 of 2920 rows misaligned. Re-measure with `scripts/probe_diffview.py
--ref 449fc98` after touching `_HunkView` or the section builders — a
second per thousand lines is the point to start deferring hunk builds
below the fold. Keyboard selection with `cursor_visible=False`
selects nothing on GtkSourceView 5.18 — the cursor shows while a view has
focus. Tab width is the constant `diffview.TAB_WIDTH = 4` (the editor has
no tab-width setting). Facts the view codes around: `GutterRendererText`
bolds the cursor line in every view regardless of focus (a
`weight="normal"` span outranks it); a one-line hunk's validated height
may not reach its `propagate_natural_height` scroller until the next
resize (the vadjustment's `changed` queues one from an idle); under wrap
the split alignment must re-fire on height changes too.

**E2E.** `scripts/check_git_page.py` runs its viewer-agnostic passes with
**both** viewers: `check_sidebar(repo, state_path, native=)` (the 500 /
900 px collapse, the toggle and its persistence, the commits list, a
commit / header / working-tree row loading, the split files list and the
other side's click, stage_all and commit reloading exactly once — hunk's
count off the shim's state file, the native page's by wrapping
`page._native_load` — and the page size), `check_without_hunk` (the
install card for hunk, the diff for native, on a PATH holding git alone)
and `check_outside_a_repo`. `check_native` stages every section kind in
the tmp repository (`stage_native_fixture`: two unstaged hunks with gaps
around them, a staged edit, a staged rename, a modified binary, an image
before/after, an untracked text file and an untracked picture, a
deletion, a mode change) and reads them back through the view's probes —
`file_rows()` (path, kind, shown), `hunk_rows(path)`, `gap_rows(path)`,
`badge_rows()` (path label, badge, has picture), `hunk_serials(path)`
(`_HunkSection.serial`, minted once per widget: an `id()` can be reused
after the dropped widget is freed), `set_scroll(fraction)`,
`pinned_header_text()` — then a gap expanding, a files-list click
revealing, the watch reloading an edit that keeps the line counts within
2 s (0.4 s measured) with the untouched hunk's widget and the keyboard
kept, the `git.*` actions, the highlight following a scroll to the end
(and the pinned header), the filter, the find bar's counts across hunks,
the staged (rename) and commit loads, settings and a layout change leaving
`page_state` alone while a page restored from it takes the layout off the
setting, the switch flipping both ways with the shim hunk going down,
re-parent vs. unparent, and a page restored into a commit / a commit git
no longer has. Kinds to expect: an untracked *picture* reads `binary`
with the badge `untracked · binary` (the `--no-index` diff says "Binary
files differ"; `KIND_BINARY` wins over `KIND_NEW`), a pure rename has no
hunk, a mode change no hunk and no counts. `scripts/probe_diffview.py`
draws a real repository's diff to a PNG and prints the timings above.

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
`gitloads.Options.from_settings` normalises the settings dict; `safe_theme`
gates the theme string (both re-exported from `hunkctl`).

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
`refs_signature` (mtimes of `packed-refs` and every directory under
`refs/heads` and `refs/remotes`, so a branch written anywhere or a push
moves it), `git_dir`, `parent_branch`. Anything
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
- **The native view's scroll range settles late.** A hunk or context
  `GtkSource.View` validates its height a beat after allocation, so the
  column's `upper` grows after the widgets are in the tree: a
  `set_value(upper - page_size)` issued right after a load or a gap
  expansion lands short of the real end (the e2e waits for the range to
  hold still, `range_settled`, before `set_scroll(1.0)`), and a one-line
  hunk can sit at 0 px until the vadjustment's `changed` queues a resize.
- **The split padding pass is a convergence, not a one-shot.** Under wrap
  the two views' rows are aligned with `pixels-below-lines` tags after
  every allocation whose width *or* height changed (`_SplitPane`, 40 ms
  settle); a pad changes a height, which re-fires the pass, which
  converges once nothing changes. Don't assert on row alignment before it
  has settled, and don't add a `queue_resize` inside the pass.
- **Every hunk scrolls on its own.** With wrap off each `_HunkView` sits
  in a `ScrolledWindow` with horizontal AUTOMATIC / vertical NEVER: a long
  line scrolls within its hunk, never the column. Anything that measures
  the column's height must ask the hunk scrollers
  (`propagate_natural_height`), and a wheel over a hunk with a horizontal
  bar is the hunk's, not the stream's.
- **A files-list highlight that "flickers" between two files at a scroll
  end** is the top-of-viewport rule: the first section whose bottom edge
  is still below the viewport's top is current, so a section 8 px in
  counts. The pinned header has its own rule (the top section's header
  fully off) and so can name the next file down.

Related: `collins-session-mcp-tools` (`show_diff`), `collins-panel-dock`,
`collins-terminal-tab`.
