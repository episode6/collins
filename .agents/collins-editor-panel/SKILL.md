---
name: collins-editor-panel
description: >-
  How Collins' built-in editor panel works: EditorPane (editor.py, GtkSourceView
  5) beside the agent terminal with its file tree (filetree.py), Agent files
  list, quick open (quickopen.py + fuzzy.py), file clipboard (fileclipboard.py),
  the popped-out editor window (editorwindow.py), narrow single-column mode,
  following the session into worktrees, external-change reloading, and the
  GTK-free rules in editorfiles.py and filetypes.py. Use when changing the
  editor, its tree, how files open from the terminal or from the
  open_in_editor tool, cursor placement, pop-out behaviour, or the editor's
  persistence in state.json.
---

# The editor panel

`EditorPane(Gtk.Box)` in `editor.py` is one per `TerminalTab`, living in the
tab's own end slot (it is deliberately **not** a `PanelDock` page). F8 toggles
it; `Ctrl+Shift+O` quick-opens; `Ctrl+S` / `Ctrl+F` inside it save / find via
the `editor.*` bindings in `keybindings`. GtkSourceView 5 is a hard
dependency (the PR page's diffs build on it too); a missing typelib exits with
an install hint (`editor.py` import guard) — `prview` imports GtkSource
*through* `editor` to share that path.

## Anatomy

- **Left column** (`self._left`): the **Agent files** list — the paths the
  session most recently wrote, from `transcript.TranscriptModel.touched_files`'s
  pass (`set_agent_files`) — above the `FileTree`.
- **File column** (`self._editors`): an `Adw.TabBar` + `Adw.TabView` of open
  files (a `GtkSource.View` per file, `_OpenFile` bookkeeping: buffer, monitor,
  dirty state), a search bar, and an image page for pictures
  (`editorfiles.image_guard`, shown through `animatedimage.load`).
- `filetree.FileTree`: a `Gtk.ListView` over a `Gtk.TreeListModel`, lazily
  populated by `editorfiles.list_dir` on first expansion (honours
  `editor_show_hidden_files`; `ignored_names` from git on demand). Icons and
  colors per extension from `filetypes.py` (bundled `ft-*-symbolic` Octicons;
  color classes defined in `app.py`'s scheme provider, Seti-inspired). Context
  menus (new file/folder, rename, copy/cut/paste, trash, reveal) act through
  `editorfiles.rename_target` / `paste_target` / `unique_target` /
  `paste_entries`; the clipboard payloads (`Gdk.FileList`, `text/uri-list`,
  `x-special/gnome-copied-files` for cut) are `fileclipboard.py`'s.
- `quickopen.QuickOpen`: type-ahead over `editorfiles.walk_files` (background
  thread, cached per root, cache dropped by a `Gio.FileMonitor` on the root,
  re-walked on every open anyway), scored by `fuzzy.py` (subsequence; basename
  and segment-start hits win).

## Behaviours

**Opening** (`open_file(path, restore_cursor)`): guarded by
`editorfiles.load_guard` (size, binary, image) and `is_inside(root)`;
language from `guess_language_id` (extension, then the first line's shebang);
style scheme from `editor.style_scheme(setting, dark)` (a bare
`GtkSource.Buffer` defaults to the light `classic` scheme — never leave it
unset). Cursor placement on a fresh buffer must re-issue `scroll_to_mark` from
a `PRIORITY_LOW` idle (`_apply_cursor`): line heights are estimates until
validation idles run, so an immediate scroll lands ~line 44 for a target of
602. `open_in_editor` (the MCP tool, the terminal's Ctrl+click on a path,
"Add to chat" in reverse) all land in `MainWindow.open_in_tab_editor`.

**External changes**: each open file has a `Gio.FileMonitor`; a clean buffer
reloads silently, a dirty one is told. The agent rewrites these files
constantly, so this path is exercised more than manual saves are.

**Following the session** (`request_root` / `offer_root`): the tab's cwd tick
calls `_maybe_follow_editor`; `editorfiles.follow_scope(root, cwd)` and
`plan_reroot` decide whether a cwd move (into a `.claude/worktrees/<x>`
worktree, back out, into an unrelated dir) re-roots the tree and which open
files map to the new root (`renamed_path`). Panel shells get the same offer to
`cd` (`_maybe_offer_shells_follow`).

**Narrow mode** (`editor_narrow_width`, default 500, 0 = never): an
`Adw.BreakpointBin` around the paned with one `Adw.Breakpoint`
(`max-width: Npx`) flips `_narrow`; `editorfiles.pane_layout(narrow,
n_pages, picker_requested)` says which single column shows, with a back button
in `Adw.TabBar.set_start_action_widget`. The switch is `set_visible` on one
paned child (a `Gtk.Paned` gives its whole allocation to its only visible
child and keeps `position`). The bin needs `set_size_request` on **both**
axes or it warns per allocation; `queue_resize()` after the setting changes,
since a changed condition isn't re-judged on its own. `notify::n-pages` (not
`close-page`, which fires before the page is gone) returns to the picker on
the last close.

**Pop-out** (`editorwindow.EditorWindow`): the live pane is **reparented**
into an `Adw.ApplicationWindow` — buffers, cursors, dirty state and monitors
all survive; one editor per tab, in one place at a time. `state.editor_pops_
out(monitor_width, limit)` decides at every path that *newly* opens the editor
(`MainWindow._editor_opens_popped_out`): monitors at most
`editor_pop_out_screen_width` **logical** px wide (default 1600; geometry ×
scale is the spec-sheet number nobody thinks in) open popped out. The
headerbar dock-back button and the footer icon bring the panel back open; the
WM close button docks it back closed. Hidden main windows hide their pop-outs
and restore them on `notify::visible`.

**Persistence**: `capture_editor_state` / `restore_editor_state` →
`AppState.set_editor_state` (open paths, active path, cursors; a popped-out
pane counts as open). `editor_width` is a `PanedSizer` seed — the first-show
race that poisoned it (`notify::position` from the reveal's relayout before
the apply idle) is why every sized paned uses the hardened sizer.

## Close and quit

Dirty buffers are part of every close gate: `_ask_editor_then_tab_close`
(Save / Don't Save / Cancel — Cancel aborts the whole action) on tab close,
`_ask_save_editors` on quit, and `_close_tab_direct` for the no-dialog paths
(`_close_ok` is blanket consent for the *session* only). Any new discard-on-
close state joins all three.

## Footguns

- `grab_focus()` on a `Gtk.Box` subclass stops at a `Gtk.ScrolledWindow`
  child (not focusable, no `grab_focus_child`), so `FileTree.grab_focus()` did
  nothing for a long time; composite widgets need `do_grab_focus` naming the
  focusable leaf. Probe with `window.get_focus()`, not the return value.
- `Gtk.Widget.pick()` at a tree row's indent or trailing space returns the
  ListView, not the row's `TreeExpander`; one gesture on the ListView and
  `FileTree._row_at` (compare `compute_bounds` against y, with the 2 px seam
  below each row) is the hit-test.
- A synchronous `grab_focus()` inside a context-menu action doesn't stick
  (the closing popover restores focus); defer with `GLib.idle_add`, and for a
  target in another window re-select the tab and `present()` the root first.
- The `@path#L2-4` mention: the CLI parses line ranges but not columns; round
  partial selections outward to whole lines. Paths with spaces need quotes,
  not backslashes.
- `GtkSource.View` widget-level CSS styles only that widget; page-wide font
  changes need a display-level provider keyed on an ancestor class.
- The `.deb`/RPM/AUR pull `gtksourceview5` in; only a source checkout can hit
  the missing-typelib exit.

Related: `collins-terminal-tab`, `collins-panel-dock`,
`collins-gtk-sharp-edges`, `collins-testing` (`check_editor_narrow.py`).
