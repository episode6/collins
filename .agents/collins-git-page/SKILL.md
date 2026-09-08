---
name: collins-git-page
description: >-
  How Collins' git page works: the native diff view beside the session
  (gitpage.py holding diffview.py under a one-row header — one
  GtkSource.View per hunk, split or stacked, gaps, images, the find bar,
  the page-local git.* chords, the file monitors' watch, the line selection
  and the headers' stage / unstage / discard / revert buttons with the
  page's plan-confirm-run path, the note cards and highlights over the
  GTK-free diffnotes.py store), the native commits and files sidebar with
  its action row and files filter (gitsidebar.py over the GTK-free
  gitmodel.py and gitops.py), the GTK-free Loaded vocabulary in gitloads.py
  (modes, commit and range loads, safe refs, breadcrumb and tab titles, the
  chords, the layout slot, show_diff's reading of its arguments), the
  parser and staging arithmetic (diffmodel.py, gitpatch.py), Preferences →
  Git, the parent-branch rule, freshness reloads, and gitinfo.py's cheap
  .git reads for the footer branch. Use when changing the git page, the
  sidebar, the diff view, the show_diff tool's page-driving half,
  git-branch detection, or debugging "clicking a commit does nothing" / a
  diff key reaching the terminal / a reload that dropped a widget.
---

# The git page

## Shape

`GitPage` (`gitpage.py`) is a `PanelPage` (`page_kind="git"`; opened by F6,
the footer's git button, a click on the footer's ⎇ branch label, or the
`show_diff` tool; the button fires `win.toggle-git`, so it closes the page
too when the cursor is in it and its tooltip carries the F6 hint, while the
label only ever opens, via `TerminalTab.open_git_page`). It is an `Adw.Bin`
holding a `Gtk.Stack` of two pages: `_VIEW` (the header, the find bar and
the sidebar + `diffview.DiffView` in a `Gtk.Paned` inside an
`Adw.BreakpointBin`) and `_CARD` (one card, `_NOT_A_REPO`: a directory that
stops being a repository; `_show_card` takes the id alone). There is no
terminal in it and no external program: every load is one
`gitops.read_diff` and the view draws it. The header is one row: the branch
(`_BRANCH_MAX_CHARS`), a breadcrumb of what is loaded, the find toggle, the
sidebar toggle, the view's menu (layout, line numbers, wrap, agent notes,
reload, keyboard shortcuts) and refresh; the tab's X closes. The
`BreakpointBin` (`max-width: 679px` → the sidebar hides and the toggle goes
insensitive; the bin's 460 px request is the page's real minimum,
`column_floor` / `column_seed` 680 and ~700 are what the dock opens it at).
The whole page sits in an `Adw.ToastOverlay` for its own toasts. The tab's
glyph is `gitpage.ICON` (`git-merge-symbolic`), public because the footer
button wears it too. The diff needs `git` alone — a machine without it
never gets past `gitinfo.repo_root`.

**Loads.** The five things the page shows are the `Loaded` vocabulary
(`gitloads.py`, below): `unstaged`, `staged`, `branch`, `{"show": ref}`
and `{"range": "a...b"}`, picked by Ctrl+1/2/3 (a CAPTURE key controller
on the page, `gitloads.load_for_key`), the sidebar's rows, the host's
`open_git_page(mode)` or the agent's `show_diff`. `load(loaded)` →
`_read_diff(loaded)`: one `gitops.read_diff(cwd, load, parent_target,
untracked)` on a daemon thread behind `_gen`, plus `gitloads.commit_message`
for a commit (one `git log -1`: sha, author, date, subject, body — the
breadcrumb's subject, the sidebar's `▸` sha and the commit card's words
come from the same read) with `gitinfo.github_url` beside it, the
`merge_base` a branch / range reads its old side at, and
`tree_state_signature` for a working-tree load (sampled **before** the
read, see Watch mode) — landing in `_diff_read` at `PRIORITY_DEFAULT`, one
read at a time with a newer ask parked in `_pending_load` — **an ask for
the same load that arrives while a read is out re-reads after the landed
read is drawn** (the tick's moved index, a mutation landing, the untracked
switch: the tick already advanced its signature past the move, so nothing
else would reload, and the stale read stood in for it until PR 4's review
— the e2e gates `gitops.read_diff`'s landing to prove it). The breadcrumb
and `set_context` come from the `Loaded` + that sha (`_resolved_sha`); the
files list from the read (`_file_summaries` builds `gitmodel.FileSummary`s
so `gitmodel.files_sections` is fed the same shape; `GitSidebar.
refresh_files(..., status=)` takes the read's own `git status` and draws at
once); the context reader handed to the view is `gitops.side_bytes(cwd,
load, side, path, previous_path, parent_target, merge_base)` (None for a
side that names nothing — a branch with no parent — *except* the unstaged
new side, which is the disk). A restored page is built unselected, maybe in
a hidden strip, and opens on its first "map" (`_ensure_open` → `_open_view`:
a thread reads the stack git shows under HEAD and, for a saved commit,
whether it still exists — else the default mode — then `_view_opened`
shows the view and loads); `_close_view` orphans the read, drops the
monitors and empties the view; `_reopen` is the moved-repo-root path.
`GitPage.opened`, `.opening` (the open's thread is out: a card still up
is not the page's last word), `.loaded`, `.diff_view`, `.settled()` (no
read or navigate in flight), `.shows(loaded)`, `.card`, `.breadcrumb_text`,
`.reveal(path, hunk, side, line, focus=True)` and the marks' doors
(`notes()`, `highlights()`, `add_notes()`, `add_highlights()`,
`clear_marks()` — `DiffView`'s, refused with a reason while the view isn't
up) are the public face — `app._ShowDiff` opens the page with
`focus=False`, polls `settled()` up to `gitloads.SHOW_DIFF_DEADLINE_S`
(12 s, `SHOW_DIFF_POLL_MS` 250; the not-a-repo card ends the poll only
while `opening` is False) and reveals with `focus=False`, so the tool
never takes the keyboard. `load()` on a mapped page standing on the card
opens the view (the host's `open_git_page(mode)` whose own repo check just
passed: the tree turned up) rather than waiting for the tick, and so does
`recheck_tree()`, which `open_git_page` with no mode (the footer's button,
F6) calls on a page it fronts. A reveal of
a file the files filter hides clears the filter first (the sidebar's entry
and `DiffView.filter("")` at once; `DiffView.hidden_by_filter` says so) —
only the tool reaches a hidden section, and True over one nobody can see
was wrong. A
`line` no hunk carries (an unchanged stretch; `diffmodel.locate` misses)
lands on the file's nearest hunk (`diffmodel.nearest_hunk`) and reveal
still answers True — the file *is* in the diff — and `DiffView.holds_line`
lets the tool's reply say the line itself is not in a changed region.

**Freshness.** Working-tree edits are the file monitors' (Watch mode,
below); commits and staging done from a shell or by the agent are caught
by the tab footer's 2 s tick forwarding `poll_tick` while the page is
mapped (`_tick`): `gitinfo.tree_signature` (index mtime, HEAD sha, parent
ref) changed → reload what is shown, and either that or a
`refs_signature` move (`refs/heads` and `refs/remotes` directory mtimes,
`packed-refs`: a branch made, deleted or committed to in another worktree,
a push) → `_refresh_branch_stack` (below), which re-reads the commits list
when it lands. A native mutation from the sidebar or the view (`mutated`)
re-seeds the signatures, re-reads the stack and reloads at once.

**Parent branch and the stack.** Git is the source of truth: `gitops.
read_stack(cwd, trunk)` — `for-each-ref refs/heads` tips intersected
with `rev-list --topo-order <trunk>..HEAD` (capped at `MAX_STACK_WALK`) —
lists the local branches on the current branch's history since the
default branch, nearest first (`stack_branches` is its first half), and
the tips at HEAD's own commit as its second half. **Branches at one
commit are one `BranchRef`** (the first by name, the rest in `.twins`;
`.label` / `gitmodel.branch_label` is `a / b`), so they share a header
row — the group id, the range and the parent name are the first's. The
page keeps HEAD's tips as `_head_twins` and hands them to `set_context
(twins=)` for the current header, and grafts the parent's twins back
onto the `BranchRef` that `resolve_group_branches` re-resolves from
`.git` (which knows no twins). The page reads
it on the open's thread and on a thread after every move
(`_refresh_branch_stack` → `_branch_stack_read`, generation-guarded;
`_branch_stack` is cleared on a branch change), keeps it in
`_branch_stack`, and `_resolve_parent` takes the first entry that still
resolves as the parent; `_branches_below_parent` is what the sidebar
groups. Only with no stack does the host's rung name the parent —
`TerminalTab._git_parent_branch`: an attached open PR's base, else the
`git_parent_branch` setting (`origin/x` read as `x`), else the
repository's default branch (`gitinfo.default_branch`: `refs/remotes/*/HEAD`,
then local `main`/`master`, loose or packed — no subprocess; a `git init`
repo has no remote HEAD, and without a default there is no trunk to walk
from, so no stack). A stack read that moves the parent re-seeds the tree
signature (the base changed, not the tree) and reloads a branch diff. There
is no picker and no persisted parent: the layout slot is `gitloads.
encode_state(loaded, sidebar)` (`"sidebar": false` when folded,
`decode_sidebar`); a `"parent"` key from older layouts is ignored.

## The commit card (`commitcard.py`)

`GitPage.commit_card` is a `CommitCard` — a vertical `Gtk.Box` wearing
`.pr-card.git-commit-card` — at the top of the `_VIEW` column, above the
`DiffView`. It is hidden for every load but a commit: `_diff_read` calls
`show(message, github_url, scheme)` when the read's
`gitloads.CommitMessage` landed on a `{"show": ref}` load and `clear()`
otherwise; `load()` clears it the moment the load changes (a stale
message over a new diff), `_close_view` too. The card's fixed part is
the subject (`heading`), a byline (author, `format_relative` age with
the stamp in the tooltip, the short sha — a `<a>` to
`<github_url>/commit/<sha>` when `gitinfo.github_url` knows one) and,
for a commit with a body, the handle: one flat button, "Show more" /
"Show less" with a caret, the PR fold's look built here. Under the
handle sits a `Gtk.ScrolledWindow` (vertical only, natural height up to
`MAX_HEIGHT` 360, then it scrolls alone) holding the **whole** body
through `prview.body_label` (the public name of `_body_label`: markdown
blocks via `mdblocks` / `mdwidgets`, code fences in the page's scheme —
`set_scheme` restyles them on a scheme or light/dark change — and
reference links from `mdblocks.repo_context(owner/name, host, sha)` off
the GitHub URL, relative links at the commit's own sha). There is no
preview: folded, the scroller is hidden and the card is the subject and
the byline; the handle is the card's child, never the scroller's, so
"Show less" stays put however far the body scrolls. `show` with the
message already shown (a tick's reload of the same commit) is a no-op,
so a body brought out stays out; a different message carries the fold's
state across the rebuild like `PrViewPage._description_card`. Probes:
`subject_text()`, `byline_text()`, `folded()` (None without a body),
`set_folded()`, `toggle_text()`, `handle_is_sticky()`, `body_labels()`
(empty while folded), `message`. `check_git_page.py`'s sidebar check
gives its `second` commit `COMMIT_BODY` for this.

## The in-progress bar (`gitoperation.py`)

`GitPage.operation_bar` is an `OperationBar` — a horizontal `Gtk.Box`
wearing `.pr-card.git-operation-bar` — at the very top of the `_VIEW`
column, above the commit card. It names a half-finished rebase / `git
am` / merge / cherry-pick / revert over a **working-tree load only**
(unstaged, staged): the read worker asks `gitops.in_progress(gitinfo.
git_dir(cwd))` beside `read_diff` and `_diff_read` calls `show(operation,
gitmodel.unmerged_count(read.status))` or `clear()`; `load()` clears it
the moment the load leaves the working tree, `_close_view` too. The
words are `gitmodel`'s: `operation_title(kind)` ("Rebase in progress"),
`operation_hint(kind, unmerged)` (the count of `U` rows and the two
commands), `abort_heading` / `abort_body`, `abort_done`, `continue_done
(label, still)` — *still* is what `in_progress` finds after a landed
continue, a rebase's next conflicting commit — and `operation_failed`
(git's first stderr line: "You must edit all merge conflicts…").

**Detection is GTK-free and file-only.** `gitops.in_progress(git_dir)`
→ `InProgress(kind, label)` walks `_IN_PROGRESS_MARKERS` in order
(rebase beats merge beats cherry-pick beats revert; `sequencer` last)
with two refinements: `rebase-apply/applying` is `git am` (kind `am`,
label "git am" — `git rebase --continue` refuses while one is in
progress, so it is named apart), and a lone `sequencer` (a
multi-commit cherry-pick or revert whose stopped step was committed by
hand) reads its `todo`'s first word — `revert` or `pick` — at most
`_SEQUENCER_TODO_BYTES`. `in_progress_operation` (the commit and revert
gates) is `in_progress(...).label`. **The markers are part of
`gitinfo.tree_signature`** (`operation_markers`, its fourth element):
an operation started or finished from a shell moves the index or HEAD
anyway, but `git merge --quit` and its kin forget one without touching
either, and the tick has to take the bar down for those too.

**The runs.** `gitops.continue_operation(cwd, kind)` is `git <kind>
--continue` under `no_editor_env()` — `GIT_EDITOR=true`, so the message
git prepared for the step stands; without it git waits on `vi` against
a pipe until `COMMIT_TIMEOUT_S` — and `abort_operation` is `git <kind>
--abort` (the argv from `continue_argv` / `abort_argv`, which refuse a
kind outside `OPERATION_KINDS`). `run_git` grew an `env=` for this. The
page runs both through `_run_operation` behind the sidebar's
`run_mutation` (the bar's `set_busy` greys its buttons meanwhile), gated
on the bar still naming the same operation, and emits `mutated` either
way; Continue goes straight in (a refused one changes nothing), Abort…
asks through `dialogs.confirm_dialog` first — the resolutions made since
the stop are lost. Probes: `operation`, `title_text()`, `hint_text()`,
`buttons_sensitive()`, `click_continue()`, `click_abort()`.

## The native sidebar (`gitsidebar.py`)

`GitSidebar(Gtk.Box)`: a vertical `Gtk.Paned` of two `Gtk.ListBox`es
(commits over files, `navigation-sidebar` style, section headings as
non-selectable `caption-heading` rows), a `Gtk.SearchEntry` files filter
above the files list, and a wrapping `Gtk.FlowBox` action row. It never
imports `gitpage`; the page feeds it and listens:

- Feed: `set_context(branch=, twins=, parent=, default=, loaded=,
  resolved_sha=, live=, stack=)` after every load / open / stack read
  (`_sync_context`; a changed branch, twins, parent, stack or default
  re-reads the commits; header and commit rows carry their full label as
  a tooltip, since both ellipsize;
  returns whether it did), `refresh_commits()` (threads: `gitops.read_page`
  per group — the current `<parent>..HEAD` (skipped, and its group not
  drawn, when the default branch is checked out: `gitmodel.build_rows`
  leaves the current group out rather than name the branch twice; the
  `working tree` row tops the list in `WORKTREE_GROUP`, above every header
  and outside every fold), then the parent and each stack
  branch as `<below>..<branch>` (`gitmodel.stack_ranges`), the default —
  plus `unpushed_shas`, landed behind a generation; group ids are
  `current`, `stack:<name>`, `default`, and `_pages` is keyed by them),
  `refresh_files(files, loaded, untracked, status=)` (the page calls it on
  every read: the status comes with the read, so no extra `git status`;
  `gitmodel.files_sections` lifts the status's `U` rows out of the
  unstaged side into `FileSections.conflicts`, drawn as a **CONFLICTS**
  heading — `.git-section-conflicts`, warning-coloured — above UNSTAGED
  whenever it is non-empty: on the unstaged load the diff's own live row
  per clash, a status row standing in for one the read didn't carry, on
  the staged load status rows; all are unstaged-side rows, keyed
  `("unstaged", path)`, so `click_section("unstaged")` finds the CONFLICTS
  heading first while one is up),
  `set_selection(path, hunk)` (the view's `current-changed`),
  `set_options` (page size → re-page; untracked → redraw),
  `set_filter_text` / `focus_filter` / `filter_text`.
- Signals: `load-requested(Loaded)` → `load()` (which clears the view's
  solo: a load is the whole stream); `show-all-requested` (the live
  side's or the flat list's section heading clicked) → `_show_all` →
  `DiffView.solo(None)`; `revert-requested(path)`
  (the files list's right-click *Revert file*, offered only on a
  read-only load — `gitpatch.working_side` is None — through the
  `gitsb.revert-file (s)` action) → the view's file button;
  `navigate-requested(path, side)` → `_navigate` (→ `DiffView.solo(path)`
  — the file's section shown alone, the others hidden by the same
  `set_visible` the filter uses, `soloed` says which; a reload keeps it
  until the file leaves the load, and a filter word that leaves the
  soloed file out drops it, else the two would show nothing; `]` `[`
  `.` `,` `}` `{` and `j` `k`
  walk the sections the *filter* admits and re-solo the file they land
  in — then `DiffView.reveal`, synchronous; a miss toasts)
  on the live side (or the flat list), else `_pending_navigate = (path,
  side)` + `load(side)`, run when the reload lands with that side
  (`settled()` waits for both) — and when that landing re-reads at once
  (an equal ask parked behind it, `_diff_read`'s `reread`), the navigate
  stays queued for the re-read rather than scrolling a view about to be
  redrawn; `filter-changed(str)` → `DiffView.filter`
  hides sections (`set_visible`, not destroyed) and the rows hide too;
  `filter-escaped` puts the keyboard back in the view (Escape in the entry
  clears it first); `mutated` → re-seed the signatures, re-read the
  stack, reload now. A `GtkSearchEntry`'s `search-changed` is **debounced**
  (~150 ms): a probe that sets the text must `wait_for` the words to land.
- Native mutations (`commit`, `fixup`, `revert(sha, commit)`, `stage_all`,
  `unstage_all` — public so the e2e drives them without dialogs) run on a thread behind `busy`
  (the Commit button spins, the other mutations go insensitive) and toast
  through the nearest `Adw.ToastOverlay` (`set_use_markup(False)`: titles
  carry commit summaries). The button handlers gate first
  (`in_progress_operation`, `staged_paths`) and ask through
  `dialogs.commit_dialog` / `choice_dialog` / `confirm_dialog(destructive=
  False)`. `run_mutation(work, done)` is public (it returns False, with a
  toast, while one runs): the page runs the diff view's plans behind the
  same `busy`. The e2e reaches rows with `click_commit_row(id)`,
  `click_file_row(path, side)`, `click_section(side)` and reads
  `commit_rows()`, `file_rows()`, `loaded_row_id()`, `selected_path`,
  `commit_menu_labels(id)` (a commit row's right-click: *Copy sha*,
  *Revert…* — only while `live` — and *Reload*; `_on_revert_clicked`
  gates on `in_progress_operation` on a thread, then a three-way
  `confirm_dialog`: *Commit revert* → `revert(sha, True)` = `revert
  --no-edit`, *Revert in working tree* → `revert(sha, False)` =
  `revert --no-commit` **followed by `revert --quit`**, since a clean
  `--no-commit` leaves REVERT_HEAD and the Commit gate would refuse;
  a failed quit comes back not ok with gitops's own words; a stopped
  revert's toast names `--continue` / `--abort` off the unmerged paths
  `read_status` lists, `gitmodel.revert_done` / `revert_failed`),
  and folds groups with `collapse_group(group, collapsed=None)` /
  `collapsed_groups()` — the caret on a header row (`_CommitRow`, a
  focusable=False flat button, so its press never activates the row);
  `gitmodel.row_folded` says which rows hide, the header never, and the
  fold set lives on the widget for the page's life (not in `page_state`).
- The per-hunk and per-line buttons are the diff view's own, on its
  headers (decision 7 of the spec): the sidebar has no cursor buttons.

The GTK-free halves: `gitmodel.py` (ports of the former extension's
`model.ts` / parsers: `parse_log` over `LOG_FORMAT`, `parse_status_v2`,
`build_rows`, `loaded_row_id`, `files_sections`, `FileSummary(id, path,
previous_path, additions, deletions, hunk_count)`, the confirm/toast
words; every subject, path and list bounded) and `gitops.py` (argv
builders and runners that take `run=subprocess.run` and never raise:
`read_page` with the limit+1 trick, `unpushed_shas` — `HEAD --not
--remotes`, empty without a remote-tracking ref — `stack_branches`,
`read_status`, `staged_paths`, `in_progress_operation` on
`gitinfo.git_dir`, `commit`, `commit_fixup`, `stage_all`, `unstage_all`,
`unpushed_in_group`, `resolve_group_branches`).

## The GTK-free half of the diff (`diffmodel`, `gitpatch`, `gitops`, `gitloads`)

Spec: `~/specs/collins/native-diff-panel.md`. `diffmodel.py` parses a
whole `git diff` / `show` stream into `File`s / `Hunk`s / `Line`s (every
header form, binary and too-large placeholders — `TOO_LARGE_LINES` /
`TOO_LARGE_BYTES` — gaps, split rows, word emphasis, the palette blends,
`locate` / `nearest_hunk`, `stable_key` for reload matching, `display_text`
for a row's buffer paragraph); `gitpatch.py` is the staging arithmetic
(partial-patch writers, `plan_file` / `plan_hunk` / `plan_lines` → a `Plan`
or a `Refusal`, `MutationRequest`, `action_labels`, the confirm and toast
words; `parse_file_patch` answers only the stanza whose path was asked
for and `_same_file` re-checks it before a patch is written, so a re-read
can never plan another file's change under this file's confirm, and
`_became_rename` refuses stale — the view reloads — when the fresh stanza
is a rename the shown file was not, in `plan_hunk` as in the shared
`_guard_partial`, since a partial patch keeps the `rename from` / `rename
to` lines and `git apply --cached` would move the file whole; **a revert
plan carries no `confirm`** — its result is unstaged, in the diff, a
discard away — so the spec's "may conflict" warning and the `dirty`
flag the planners once took are gone, and a discard is the only plan
that asks); and `gitops.py`
holds the reads and runs they need — `read_diff(cwd, load, parent_target,
untracked, pathspecs)` → `DiffRead(files, status, ok, error)`: `diff_argv`
/ `show_argv` behind `DIFF_PREFIX_ARGS` (the `-c` options pinning `a/`
`b/` so the patch applies back under any `diff.noprefix`) and `DIFF_ARGS`,
a `numstat_argv` pre-pass whose over-cap paths are excluded
(`:(exclude,literal)`) and stood in as `KIND_TOO_LARGE` placeholders,
`git status` for the working-tree loads, and the untracked files
synthesized one `untracked_diff_argv` (`diff --no-index -- /dev/null
path`, exit 1 is the answer) at a time, at most `MAX_UNTRACKED_DIFFS`,
**and on the unstaged load the status's unmerged (`U`) paths read again
as one `conflict_diff_argv` (`diff --ours -- paths`, at most
`MAX_CONFLICT_DIFFS`) with `diffmodel.parse(..., conflict=True)`** — a
bare `git diff` writes an unmerged path as a `diff --cc` stanza, which
the parser leaves out, and `--staged` lists it as `* Unmerged path` with
no patch, so without this read a stopped rebase's clashes had no diff at
all; `File.conflict` is what the view and the planners key on;
`file_patch` (the re-read every mutation starts from), `file_at(cwd, ref,
path)` → bytes (`side_ref` names the ref per load and side: `INDEX_REF`
`""` is `:path`, `None` is the disk), `merge_base`, `apply_patch(cwd,
patch, cached, reverse, three_way)` → `ApplyResult` (the `--3way` retry
only when asked, flagged, `conflicts` when it left markers), `stage_paths`
/ `unstage_paths` / `checkout_paths` behind `safe_path`, `run_plan`, and
`tree_state_signature` (status + numstat + the size and mtime of every
working-tree path status lists, `MAX_STAT_PATHS`, hashed) for the watch.
Footguns: every one of these runs from `gitinfo.repo_root` (`_root`) —
from a subdirectory git reads pathspecs against the cwd and `apply`
silently skips paths outside it; every path after `--` goes on as
`:(literal)path` (`literal_pathspec`) because git reads a bare pathspec as
a glob — `foo[1].txt` names foo1.txt too, and a confirmed `checkout --
foo[1].txt` discarded the twin's changes (the `--no-index` untracked read
takes filesystem paths and stays bare); patch reads are binary
(`run_git_bytes`) because `text=True` folds CRLF and the patch then matches
nothing; `--3way` implies `--index`, so a three-way revert also stages, and
a conflicting one exits 1 having changed the tree.

**`gitloads.py` is the `Loaded` vocabulary**: `MODES` / `DEFAULT_MODE`,
`SHOW_KEY` / `RANGE_KEY`, `safe_ref` (the one rule for a ref that goes on
an argv or into a title), `is_show` / `show_ref`, the fifth `Loaded`
`{"range": "a...b"}` (`is_range`, `range_halves`, `range_of`; three dots
between two safe refs, two-dot ranges are refused), `loaded_ok`,
`short_ref`, `breadcrumb` / `tab_title`, `load_for_key` (Ctrl+1/2/3 as
integers), `initial_mode`, `encode_state` / `decode_state` /
`decode_sidebar`, the `show_diff` tool's `show_diff_load` and
`diff_file_path` plus `SHOW_DIFF_DEADLINE_S` / `SHOW_DIFF_POLL_MS`, the
git calls behind a commit's name (`commit_subject`, `commit_message` —
one `git log -1 --format=%H%x00%an%x00%aI%x00%s%x00%b` into a
`CommitMessage`, every field bounded, the body at
`COMMIT_BODY_MAX_CHARS` — and `resolve_commit`, `GIT_TIMEOUT_S`),
`Options.from_settings` →
`Options(layout, untracked, log_page, line_numbers, wrap, word_diff)` with
`LAYOUTS`, the `LOG_PAGE` bounds, and `MAX_PATH_CHARS`. The widgets and
the GTK-free half alike import it directly.

## The view (`diffview.py`)

`DiffView` is a `Gtk.ScrolledWindow` over a column of `_FileSection`s
(header: path, `old → new`, counts, kind badge, the file buttons; a
picture for an image; the gap rows and `_HunkSection`s), the pinned file
header over the top. Every hunk is its own `GtkSource.View` (decision 2:
the header is a plain `Gtk.Box` carrying the buttons, the selection is
hunk-scoped, gaps expand between them); split is two views per hunk
(`_SplitPane`) over `diffmodel.split_rows` with a padding pass; the
language is the file's, the scheme and font the editor's
(`editor.style_scheme`, `editor_font`, decision 3 — there is no diff
theme). `load(files, reader)` patches by `diffmodel.stable_key` through
`keyedslots` — an untouched hunk keeps its widget, its selection, its
notes and the keyboard; a changed one rebuilds itself. **A kept hunk
section must be re-pointed at the read's Hunk**: `stable_key` leaves the
index out on purpose (a hunk above going away must not rebuild the ones
below), so after `plan.commit()` `_FileSection.update` re-assigns
`section.hunk` from `file.hunks` in order (one section per hunk,
`zip(strict=True)`), and a kept `_GapRow`'s `file` likewise; before that
the survivors kept the old `hunk.index` and reveal named the wrong hunk to
the sidebar (`hunk_indexes` probes it).

**The sidebar follows the view.** `current-changed(path, hunk)` — the
file at the top of the viewport (60 ms after the scroll settles) or the
hunk the keyboard moved into — → `GitSidebar.set_selection`. A files-list
highlight that "flickers" between two files at a scroll end is the
top-of-viewport rule: the first section whose bottom edge is still below
the viewport's top is current, so a section 8 px in counts; the pinned
header has its own rule (the top section's header fully off) and so can
name the next file down.

**Keys.** `keybindings.GROUP_GIT` (`git.*`): `]` `[` next/prev hunk, `.`
`,` file, `j` `k` the cursor a row down/up in the focused hunk's view
(`DiffView.step_cursor`: past the hunk's edge it enters the neighbouring
hunk on the same side, first/last row; `_show_cursor` scrolls the
*column* to the line — each hunk's scroller never scrolls vertically —
from the column's own coordinates, since bounds against the scroller are
stale until the layout after a `set_value`), `}` `{` annotated hunk (a
view with marks — `_HunkView.marks`), `z` expand the gap above the focused
hunk (all of it; one way), `0` `1` `2` layout, `l` line numbers, `w` wrap,
`c` add a note / `E` (`<Shift>e`) edit the hunk's first user note / `a`
show or fold the agent's notes (`git.agent-notes`, a boolean-stateful
action on the page with no setting behind it — the page's for the tab's
life), `r` reload, `/` the filter, `Ctrl+F` find, `?` Keyboard Bindings
opened **on the Git page group** (`win.keyboard-bindings-group (s)` →
`KeyboardBindingsDialog(group=)`: it scrolls after the first layout past
`map`, and takes the viewport's `scroll-to-focus` off around the first
row's grab — that scroll reads pre-`set_value` bounds and animates the
page 1 100 px past the group), `e` open in the editor at the cursor line
(`win.open-in-editor (sii)`, 1-based line), `q` close (`win.toggle-git`).
The mechanism copies the editor's `editor.*` row: `keymap.
shortcut_controller(custom, "git", …)` in `DiffView.apply_keybindings`,
scoped `LOCAL` **on the view** and in the **CAPTURE** phase — a bare
letter must beat the `GtkSource.View` under it (the editor uses BUBBLE
because its chords are Ctrl chords); the `NamedAction`s resolve to a
`Gio.SimpleActionGroup` inserted on the *page* under `git`. The staging
keys: `x` `git.stage` (`DiffView.request_stage`: the selected lines when
the selection is in the focused hunk, else the focused hunk — the current
one when none has the keyboard), `X` `git.stage-file` (`<Shift>x` in the
catalogue: GTK lowercases the event's keyval and compares the Shift bit,
so a bare `X` would never match — `braceright` and `question` work
because Shift is consumed producing them) and `D` `git.discard`
(`<Shift>d`); `Esc` is not a binding — a capture key controller on the
view clears the selection and swallows the press only while one exists,
so the dock's restore-from-maximized still gets it otherwise
(`GitPage.holds_escape` says so — also while the find bar or a note
editor is open). `DiffView`'s `editing-changed(bool)` →
`GitPage._on_note_editing_changed` sets every `git.*` action insensitive
while a note editor is open — a disabled named action lets its chord fall
through, so `e` types an e instead of closing the page — and enables them
again when it closes. `keybindings.LOCAL_PREFIXES` / `may_overlap`:
`editor.*` and `git.*` are page-local scopes that never see one press, so
`Ctrl+F` in both is not a conflict. The stateful actions `git.layout`
(`s`), `git.line-numbers` / `git.wrap` (`b`) write their setting through
**`win.git-option (sv)`** (window.py: an allowlist of `_KEY_SETTINGS` →
`state.set_setting` + `apply_preferences`, so every page follows); with no
window action to reach — a page in a bare test window — `_write_option`
applies the dict to the page alone. `_sync_action_states` mirrors
`apply_settings` back into the menu's checks. `Ctrl+1/2/3` stay on the
page's raw capture controller.

**The selection and the buttons.** Decision 4: the line selection *is*
the buffer's own selection, snapped to whole paragraphs. `_HunkView`
connects `mark-set` (only for the `insert` / `selection_bound` marks) and
`_snap`s inside it — the start of the first line to the start of the line
after the last, the insert mark kept on the end it was on so Shift+arrows
keep extending; `_snapping` guards the re-entry `select_range` causes. The
line numbers get a `Gtk.GestureDrag` on the gutter widget
(`view.get_gutter(LEFT)`): press selects the row under it (Shift extends
from the far end of what is selected), drag extends, `_gutter_row` mapping
gutter y through `window_to_buffer_coords(LEFT)` + `get_line_at_y`.
`_owned` says the selection is the model's: the find bar's current match
goes through `select_match` (no snap, `_owned` False), so
`selected_rows()` is None for it and the buttons stay unselected.
`_Row.line` is the index into `hunk.lines` (None for a padding cell and
for expanded context); `_HunkSection.selection()` is `(min, max)` of the
selected rows' line indexes — a span in **patch order** (in split, a
selection from a context line through additions takes the deletions
between them too), None when only pads are selected. One selection in the
stream: `DiffView.on_hunk_selection` clears every other hunk's and the
hunk's other side; `clear_selection` (Esc), `has_selection`, `selection()`
→ `(path, hunk, first, last)`. PyGObject returns `get_selection_bounds()`
as an empty tuple with no selection — never unpack three values
(`_selection_bounds`).

The buttons are `_ActionButton`s (a label / spinner `Gtk.Stack`, so the
width holds) on every `_FileSection` header (`primary_button`,
`discard_button`), every `_HunkSection` header, and the pinned header
(copies acting on `_pinned_section`, re-worded in `_sync_pinned`; the
pinned box forwards the wheel to the column's adjustment through an
`EventControllerScroll`). `gitpatch.action_labels(load, grain, selected)`
words them (the spec's table; the discard button only on the unstaged
load) and `sync_actions` re-reads them on every load and selection
change; the CSS lifts their opacity on `:hover` / focus
(`.git-hunk-actions button`, `.git-file-actions button`). A right-click on
a hunk view (a CAPTURE-phase `GestureClick(button=3)`, claimed ahead of
the text view's own Cut/Paste menu) pops a `Gtk.PopoverMenu` built fresh
from a `Gio.Menu` — the two actions, then Copy / Open in editor / Add note
/ Expand context — over the `hunk.*` `SimpleActionGroup` each section
inserts on itself. Every button, menu item and key ends in
`request_hunk(section, discard)` / `request_file(section, discard)`,
which build a **`gitpatch.MutationRequest`** and emit
`mutation-requested(request)` — not a Plan: the planners take the file's
patch re-read from git, which is the page's thread read. `set_busy(busy)`
makes every button insensitive and spins `_acting`, the one pressed — and
**`set_busy(False)` forgets `_acting`**, so the page calls it only where
the request ends. **`Gtk.TextView.get_line_at_y` returns `(target_iter,
line_top)`** — the two C out parameters, no boolean first; unpacked as
`(ok, iter)` the "iter" is an int and the gutter press and the right-click
both raised (swallowed by the signal dispatch: a dead gesture, no
traceback). Probes: `select_lines(path, hunk, first, last)`,
`gutter_press(path, hunk, row, shift)` / `gutter_drag(path, hunk, row)`,
`context_menu_labels(path, hunk, row)`, `hunk_action_labels` /
`file_action_labels`, `click_hunk_action` / `click_file_action`, `busy()`,
`acting_button_spinning()`.

**Notes and highlights (decisions 5 and 8).** `diffnotes.py` is the
GTK-free half: `Note` (id, source USER / AGENT, path, side, 1-based line,
summary, rationale, author, `hunk_key`) and `Highlight` (id, path, side,
line, `[start, end)` in code points, tone of `TONES`, `hunk_key`),
`NoteSpec` / `HighlightSpec`, `resolve_anchor(files, path, side, line,
hunk)` → an `Anchor` or the reason, `bound_text` (CRLF, CR, NEL and the
Unicode line and paragraph separators folded to newlines, C0 and C1
controls dropped, cut at `NOTE_MAX_CHARS` 4000; authors at 80),
`summary_text` (a summary is one line: its newlines read as spaces, so
a card's heading and the editor's split can't disagree),
`split_note_text` / `join_note_text`, and `MarkStore`: `add_notes(files, specs, source)` /
`add_highlights(files, specs)` land a batch **whole or not at all**
(`MAX_NOTES` / `MAX_HIGHLIGHTS` per page, `MAX_*_PER_BATCH`), `edit`,
`remove`, `clear(path, notes, highlights, include_user)`, and the reload
rule: **a mark survives by the stable key of the hunk it was placed in
and its line's index into that hunk** (`line_index`) — `prune(files)`
drops a mark whose file the load shows without that hunk, renumbers one
whose hunk is there at other numbers (`mark.line` follows the line at its
index), and parks one whose file the load doesn't show at all (Ctrl+2 and
back keeps them); `placed_notes` / `placed_highlights(files)` is what the
view draws. **The key holds no line numbers**: `diffmodel.stable_key(file,
hunk)` is the file's path, a digest of the hunk's body (every line's kind
and text, context lines included — the buffer *is* the body, so a changed
context line is a changed hunk) and, among the file's hunks with that
same body, its ordinal in patch order and their count (`0/1`, `1/3`;
`stable_keys(file)` is the one-pass form the loops use). So staging a
range out of hunk 0, or an edit above that adds or removes lines, moves
every later hunk's numbers and none of their keys: `_FileSection.update`
keeps the widget and calls `_HunkSection.rebase(file, hunk)`, which
re-words the header and renumbers the gutters in place
(`_HunkView.renumber`, the buffer and so the selection, cursor and tags
untouched; a row mismatch draws afresh), and `gitpatch.rebind_selection`
follows the hunk by the same key. The count in the key is deliberate:
when one of two identical hunks changes or leaves, the bodies cannot say
which survived, so both are matched afresh and their marks dropped rather
than landed on the wrong twin. The e2e's mutation check stages hunk 0
out from under a selection and a note in hunk 1 and reads the gutters
back through `gutter_numbers(path, hunk)`.

The widgets: `DiffView` owns one `MarkStore` (`_store`), `_apply_marks`
hands each `_HunkSection` its share (`set_marks(notes, highlights)`:
`_NoteCard`s in a `.git-hunk-notes` box under the views, kept by note id
so one being edited keeps its editor, drafts last; `_apply_view_marks`
puts the glyphs in the marker column and the tone tags on the side's
view). A `_NoteCard` is the header (*You* / *Agent · author*, `new line
12`, *Edit* for user notes, *Delete* for any) over a `Gtk.Stack` of the
words and the editor (a `Gtk.TextView` in a scroller capped at
`_NOTE_EDITOR_MAX_HEIGHT`, a CAPTURE key controller: Escape cancels,
Ctrl+Enter commits). `start_edit` grabs the keyboard **before** telling the
view (`on_note_editing`), which closes any other editor unsaved. `c` is
`add_note_at_cursor` (`_HunkSection.anchor_at_cursor`: the cursor row's
new number, else its old, a pad taking the next numbered row — and with a
line selection the cursor row is the selection's *last* row whichever
end the insert mark is on: the snap parks a downward drag's at the start
of the line after, an upward one's on the first row, and
`_HunkView.cursor_row` folds both back), `E`
`edit_first_note`, `on_note_saved` refuses an empty text, lands a draft as
a USER note or re-words the note, then `section.grab()` puts the keyboard
back in the hunk. **`_HunkSection._remove_card` hides a dropped card at
once and unparents it `_CARD_REAP_MS` (500) later**: a `Gtk.TextView`
unrealized within a few milliseconds of its focus leaving segfaults GTK's
Wayland input method (the compositor's text-input reply lands after the
widget is gone; `gtk_widget_get_display: assertion 'GTK_IS_WIDGET'` then
`wl_proxy_get_version` in the trace); measured with
`scripts/probe_diffview.py --notes --layout split`. The agent's tools
(`annotate_diff`, `highlight_diff`, `clear_diff_marks`, `diff_context` —
`collins-session-mcp-tools`) land on `add_notes(specs, focus, source)` →
ids or the reason, `add_highlights(specs, focus)`, `clear_marks(...)`,
`notes()` / `highlights()`, and read `GitPage.context()` → a
`mcptools.DiffContext` (the `Loaded`, the breadcrumb, `DiffView.files`,
`current()`, the selection, the marks; empty files while the view isn't
up); `set_agent_notes_shown` folds the agent's cards. The ids are one
serial across notes and highlights (`n1`, `n2`, `h3`). Probes: `note_rows(path,
hunk)`, `note_marks`, `highlight_rows`, `editing()`, `note_editor_text` /
`set_note_editor_text`, `commit_note` / `cancel_note`.

**The page runs the request (`GitPage._on_mutation_requested`).** Gated
on the sidebar's and the view's busy and on `request.load == self.
_loaded`; a thread reads `gitops.file_patch` when `request.needs_patch`;
then `_mutation_planned` calls `request.plan(fresh)`. The sidebar's
*Revert file* (a file row's right-click on a read-only load,
`revert-requested(path)` → `_on_revert_requested` → `DiffView.
request_file_at(path)`) presses the view's file button for the path, so
it is the same request from here on — the page meets it with the same
busy gate and toast first, since nothing greys a menu item while a
mutation runs and `request_file` drops a press made while busy. A `Refusal` is a toast and, when
`stale`, a `_read_diff` (the reload the words promise). A plan with
`confirm` (a discard's; a revert never asks) asks
through `dialogs.confirm_dialog` (heading and button from `request.
confirm_words(plan)`: *Move to the trash?* / *Restore the file?* /
*Discard the changes?* / *Revert into the working tree?*; the spinner
stays on while it is up, `on_dismiss` clears it), then `_run_plan`:
the request's own `gen` and `cwd` ride along, and a confirm answered
after the page moved on (the generation bumped, `request.load` no
longer `_loaded`) runs nothing — a toast, the buttons freed — since the
plan described a tree the page no longer shows; else
`gitops.run_plan(cwd, plan, three_way=request.three_way(plan), trash=
_trash_paths)` on the sidebar's `run_mutation` thread — OP_TRASH through
`gitpage._trash_paths` (`Gio.File.trash`, never an unlink; the e2e stubs
it — **Gio refuses to trash on "system internal" mounts**, a tmpfs `/tmp`
included, and the toast then says so) — the toast from `gitpatch.
outcome_words`, and `sidebar.emit("mutated")` when it landed or left
conflicts → `_on_mutated` re-seeds the signatures, re-reads the stack and
reloads by key. `three_way` is only a revert's: **`--3way` implies
`--index`, so it needs the file clean against the index** — a dirty file
is refused with "does not match index" (what the warning anticipates),
the case it helps is a *committed* move of the context, and it stages the
merged result.

**Find.** `Gtk.SearchBar` under the header (no `key_capture_widget`:
typing in the page must not open it — the letters are the view's), a
toggle in the header bound to `search-mode-enabled`. `DiffView.
search(text)` / `search_step(forward)` / `search_position()` /
`search_clear()`: matches are counted **in Python** over the hunk views'
`rows` (`re.escape`, IGNORECASE; split reads the old view for deletions +
context and the new for additions, so a context line counts once; capped
at `MAX_SEARCH_MATCHES`), selected with `select_range` and scrolled to,
the sidebar following through `_set_current`. A `GtkSource.SearchContext`
per buffer paints the highlights only — the sync `forward()` misses
matches before the index is built, and async across dozens of buffers
can't give "3 of 12". Matches are re-counted (position kept) on every
`load`, `filter` and layout change; the page re-reads the label
(`_sync_search_label`). Closing the bar focuses the current match's view.

**Watch mode.** After a working-tree load lands, `_install_monitors` puts
a `Gio.FileMonitor` (`monitor_directory`) on each distinct directory of
the loaded files (old paths of renames too) — over `MAX_DIR_MONITORS`
(64), or with no file at all, the repository root alone; a commit / range
load gets none. Events (not for the `.git` entry itself) debounce
`_WATCH_DEBOUNCE_MS` (300) into `_watch_check`: `gitops.
tree_state_signature` on a thread, compared against the state the load's
own worker seeded (`_tree_state`); a move is a `_read_diff` by key. One
compare at a time, and none beside a read in flight: either marks the
event `_watch_stale`, and the compare (`_watch_checked`) or the read
(`_diff_read`, after re-making the monitors) re-runs `_watch_check` when
it lands. The worker samples the signature **before** `read_diff` (an
edit between the two costs one reload by key on the next compare,
harmless). `_tick` re-compares every `_WATCH_SLOW_TICKS` (5) ticks
regardless. The signature hashes `git status` **and** `numstat` **and**
the size + mtime of every path on the working-tree side: an edit that
rewrites an already-changed line moves neither the letter nor the counts
and went unnoticed until the stats were added. Measured: the monitor fires
at once, the compare lands ~20 ms after the debounce, the reload ~40 ms
later (0.38 s edit-to-view on the e2e's fixture).

**Measured (this machine, headless).** `read_diff` + `DiffView.load` of
PR 500's squash (`git show 449fc98`: 16 files, 76 hunks, 3118 patch
lines): `read_diff` 23 ms; `load()` 335 ms ≈ **108 ms per 1000 patch
lines**; first paint 1146 ms ≈ **370 ms per 1000 patch lines** (the rest
is GTK's first allocation and GtkSourceView's highlighting); a reload
keeps 76/76 hunk widgets; split under wrap: 0 of 2920 rows misaligned.
Re-measure with `scripts/probe_diffview.py --ref 449fc98` after touching
`_HunkView` or the section builders — a second per thousand lines is the
point to start deferring hunk builds below the fold. Keyboard selection
with `cursor_visible=False` selects nothing on GtkSourceView 5.18 — the
cursor shows while a view has focus. Tab width is the constant
`diffview.TAB_WIDTH = 4`. `GutterRendererText` bolds the cursor line in
every view regardless of focus (a `weight="normal"` span outranks it); a
one-line hunk's validated height may not reach its
`propagate_natural_height` scroller until the next resize; under wrap the
split alignment must re-fire on height changes too.

Preferences → Git (`gitloads.Options.from_settings`, `GitPage.
apply_settings`): `git_layout` (auto / split / stack, `prefslayout.
GIT_LAYOUTS`), `git_line_numbers`, `git_wrap_lines`, `git_word_diff` reach
the view at once (`DiffView.set_options`); `git_untracked` re-reads a
working-tree load; `git_log_page` re-pages the commits list;
`git_parent_branch` is the host's rung. The scheme and font are the
editor's settings. `git_theme` and `git_viewer` (the retired terminal
viewer's) are popped from a loaded `state.json` by `AppState._load`.

## E2E

`scripts/check_git_page.py` runs on a PATH holding `git` alone:
`check_sidebar(repo)` (the 500 / 900 px collapse, the toggle and its
persistence, the commits list, a commit / header / working-tree row
loading, the split files list and the other side's click, stage_all and
commit reloading exactly once — counted by wrapping `page._read_diff` —
the in-progress bar (a native revert stopped on a clash brings it up,
Abort… asks and takes it down; a cherry-pick stopped from a shell comes
up on the tick, the resolution re-words the hint, Continue finishes it)
and the page size), `check_native(repo)` (stages every section kind in
the tmp repository with `stage_native_fixture` — two unstaged hunks with
gaps around them, a staged edit, a staged rename, a modified binary, an
image before/after, an untracked text file and an untracked picture, a
deletion, a mode change — and reads them back through the view's probes:
`file_rows()`, `hunk_rows(path)`, `gap_rows(path)`, `badge_rows()`,
`hunk_serials(path)` (`_HunkSection.serial`, minted once per widget: an
`id()` can be reused after the dropped widget is freed), `set_scroll
(fraction)`, `pinned_header_text()` — then a gap expanding, a files-list
click revealing, the watch reloading an edit within 2 s with the
untouched hunk's widget, the keyboard and its line selection kept, the
`git.*` actions, the highlight following a scroll to the end, the filter,
the find bar's counts, the staged and commit loads, settings and a layout
change leaving `page_state` alone, re-parent vs. unparent, and a page
restored into a commit / a commit git no longer has), and
`check_outside_a_repo`. Kinds to expect: an untracked file's badge is
`new`, an untracked *picture* reads `binary` with the badge `new ·
binary`, a pure rename has no hunk, a mode change no hunk and no counts;
an unmerged file (`File.conflict`) is badged `conflict`, its marker rows
wear the `git-conflict` tag (`conflict_rows(path, hunk)` probes them —
the in-progress part of `check_sidebar` reads the three markers off the
stopped revert), its hunk `actions` box is hidden and its file header
offers *Stage file* alone (`gitpatch` refuses every partial plan and the
discard on it with "is unmerged", and `MutationRequest.needs_patch` is
False, since the file's bare `git diff` is a `diff --cc`).
`check_native`'s window is an **`Adw.Window`** (`set_content`), so the
page's real `Adw.AlertDialog` is reachable through
`window.get_visible_dialog()` — presented over a bare `Gtk.Window`, an
Adw dialog opens in a window of its own and nothing finds it.
`check_native_mutations` records the page's toasts (`page._toast`
wrapped), stubs `gitpage._trash_paths`, and walks the staging interface:
the words per load, `select_lines` and the one-hunk rule, Esc, the gutter
gesture, the right-click menu, stage lines / hunk with the index read back
through **`gitops.read_status`**, *Stage file* on the binary, unstage hunk
/ file, the **real confirm dialog** (`get_visible_dialog()`; `dialog.
close()` cancels — Escape's path; `set_close_response("confirm")` +
`close()` runs it; never `emit("response")` and then `close()`, which
fires a second `cancel`), then with `dialogs.confirm_dialog` stubbed the
untracked trash, the deleted restore, a revert hunk from a commit with
no question asked, the sidebar's *Revert file* (`file_menu_labels`,
`activate_file_menu`; an empty menu on the working tree), the three-way
retry over a committed context move, a revert from `{"show": "HEAD"}`,
and a binary's *Revert file* refused.
`check_native_notes` walks the notes: `c` → a draft with the chords off
and the page holding Escape, an empty save refused, Ctrl+Enter splitting
summary and rationale, `}` / `{`, `E` and Esc, the menu's *Add note*,
`add_notes` / `add_highlights`, `a`, delete, the clears, and an edit to
hunk 1 reloading with hunk 0's note kept and hunk 1's dropped.
`scripts/check_show_diff.py` drives the five diff tools end to end
through the MCP socket on a temp repository (a `second` commit on `feat`
so the branch diff holds `a.txt`, `b.txt` staged): its `claude` stub
spawns the real shim from the tab's `--mcp-config` and relays JSON-RPC
requests the script drops as files, so every reply crossed stdio, the
socket, the pid lookup and the dispatch. It checks the tool list, the
four page tools refused with `PAGE_NOT_OPEN` before any page, show_diff's
reply lines (the load, the spot, the side, the hunk landed on), a missing
file refused by name, a line no hunk holds noted, a hunk past the count
refused, the old side, a switched-off tool refused and gone from the
list, `diff_context`'s JSON (load, current, files / hunks, patch and
notes only when asked), a note batch with one bad address landing
nothing, a good one's cards under the hunk, a highlight painted
(`highlight_rows`) and a bad range refused, the clears' counts (a lone
`notes: false` clearing the highlights, both false refused) — and the
keyboard never taken, `focus: true` included.
`scripts/check_git_prefs.py`: the seven Git rows, the settle-timer rules
on the parent-branch entry. `scripts/probe_diffview.py` draws a real
repository's diff to a PNG and prints the timings above; `--notes`
renders the note cards.

## gitinfo (`gitinfo.py`, GTK-free)

Cheap reads straight from `.git` for the footer's 2 s poll and every
right-click: `current_branch`, `default_branch`, `github_url`, `repo_root`,
`index_mtime`, `head_sha`, `resolve_branch`, `base_ref`, `tree_signature`,
`refs_signature` (mtimes of `packed-refs` and every directory under
`refs/heads` and `refs/remotes`, so a branch written anywhere or a push
moves it), `git_dir`, `parent_branch`. Anything that needs `git`
(`has_changes`, `change_summary`, `ignored_names`) shells out and is asked
on demand only.

## Footguns

- **Buffer paragraphs must equal patch rows.** `GtkTextBuffer` splits a
  paragraph on a lone `\r` and U+2029 as well as `\n`, so a row holding
  one shifted every later tag, number, emphasis span, cursor and search
  offset by a line. `_HunkView.set_rows` keeps `diffmodel.display_text`
  of each row (a trailing CR dropped, the separators shown as `␍` `¶`
  `␤`) and the search counts over those rows, so offsets agree; the
  patch text gitpatch writes back is the model's, untouched.
- **The view's scroll range settles late.** A hunk or context
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
- **Reveal takes the keyboard unless told not to.** `DiffView.reveal
  (..., focus=True)` grabs the target hunk's view; the `show_diff` tool
  and the page's quiet open pass `focus=False`, and `check_show_diff.py`
  asserts the focus never moved.
- **A quiet open is `open_git_page(mode, focus=False)`.** `app._ShowDiff`
  calls it from the MCP dispatch (or the `PRIORITY_DEFAULT` idle its
  `rev-parse` thread lands on), then polls `settled()` on a `timeout_add`;
  an open scheduled from inside the footer's chip cascade must still wait
  a beat (the Wayland backend segfault, see `collins-panel-dock`).
- **Two tabs on one worktree are two pages**, each with its own reads and
  monitors; nothing is shared through the filesystem.

Related: `collins-session-mcp-tools` (`show_diff`), `collins-panel-dock`,
`collins-terminal-tab`, `collins-editor-panel` (the scheme and font the
diff follows).
