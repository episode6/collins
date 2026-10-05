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
  `x-special/gnome-copied-files` for cut) are `fileclipboard.py`'s. A file
  row (and an Agent files row) also gets the git page's *Open In…* submenu
  (`openwithrows.file_open_with_menu` over the `footer_apps` setting the
  pane relays through `set_footer_apps`, plus *Default app* via xdg-open);
  the tree emits `open-with-request(path, app_id)` and
  `EditorPane._open_file_with` launches through `openwith.open_file_with`,
  a failure landing in the banner. Probes: `FileTree.open_with_labels`,
  `activate_open_with`, `EditorPane.agent_file_open_with_labels`.
- `quickopen.QuickOpen`: type-ahead over `editorfiles.walk_files` (background
  thread, cached per root, cache dropped by a `Gio.FileMonitor` on the root,
  re-walked on every open anyway), scored by `fuzzy.py` (subsequence; basename
  and segment-start hits win).

## Behaviours

**Opening** (`open_file(path, restore_cursor)`): guarded by `is_inside(root)`
and `editorfiles.is_image_path`; everything else about the file is the
service's (below). Language from `guess_language_id` (extension, then the
first line's shebang — `editorfiles.first_line` of the text the service
read; its sibling `fence_language_id` maps a markdown fence's info word —
`python` → `python3`, `bash` → `sh` — for the PR page's code blocks in
`mdwidgets`), switched off above 512 KiB (`should_highlight(size)`); style
scheme from `editor.style_scheme(setting, dark)` (a bare `GtkSource.Buffer`
defaults to the light `classic` scheme — never leave it unset). Cursor
placement on a fresh buffer must re-issue `scroll_to_mark` from a
`PRIORITY_LOW` idle (`_apply_cursor`): line heights are estimates until
validation idles run, so an immediate scroll lands ~line 44 for a target of
602. `open_in_editor` (the MCP tool, the terminal's Ctrl+click on a path,
"Add to chat" in reverse) all land in `MainWindow.open_in_tab_editor`.

**The files are the service's** (split-service spec §3.23, PR-2.3). The
pane opens no file and writes none: `GtkSource.File`, `FileLoader` and
`FileSaver` are gone, and every path is a path on the service's machine.
`collins/remotefiles.py` (GTK-free) is the client's end, `collins/service/
files.py` the service's:

- `_start_load` asks `fs.read` from a worker thread (`_off_main`: a daemon
  thread, the answer landed at `GLib.PRIORITY_DEFAULT`) and `_on_loaded`
  fills the buffer outside the undo history (`_fill`), keeping the reply's
  `mtime` and `encoding` on the `_OpenFile`. The guards moved with the
  read: the service refuses a path that is not a regular file, one over
  5 MiB (`protocol.FILE_TEXT_MAX`) and one outside every root it knows
  (`files.allowed`: a live session's cwd, a store session's cwd or project
  root, anything for a `local` client), and flags `binary` (a NUL in the
  first 8 KiB) and `latin-1` (bytes that are not UTF-8, written back the
  same way). A refusal's words come translated through
  `remotefiles.refusal_words` into the banner; a fresh open closes its tab,
  a reload keeps it. `load_id` still makes a superseded read (a rename's)
  a no-op.
- `_fill` strips the file's final newline when the buffer's implicit
  trailing newline is on (what `FileLoader` did; `opened.newline` keeps
  `\n` or `\r\n`) and `_do_save` puts it back, so a file shows no empty
  last line and a save adds a missing final newline. `_fill` also marks
  the buffer `filled` and makes the view editable: until then a save is
  refused ("still loading") and nothing can be typed, so a Ctrl+S or a
  close-flow Save during the first read never writes an empty buffer over
  the file.
- `_do_save` sends `fs.write` with the buffer's text and `expect_mtime`:
  Ctrl+S (`_save`) expects the mtime of the last read or write, and a file
  that moved underneath is refused `stale` by the service with **nothing
  written** — `_on_saved` raises the "changed on disk" dialog, whose
  Overwrite saves again with `expect_mtime` null. `save_all` and the close
  flows' Save pass null from the start (the user's explicit consent, as
  before). Saves are serialized: one asked for while one is in flight
  waits (`save_again`) and goes from `_on_saved` expecting the mtime that
  save answered, so two quick Ctrl+S never raise the dialog. The service
  writes as `FileSaver` did (`service/files.write_file`): in place for a
  hard link, a read-only directory or another owner's file, else by a temp
  file beside it with the old mode and one `os.replace`, a new file under
  the umask; the compare runs right before the write, and the reply's
  mtime is the written descriptor's. A save whose encoding differs from
  the read's (latin-1 that could not carry the text) is told in the banner.
- `_watch_external_changes` installs `fs.watch kind: file` under a handle
  the client mints (`remotefiles.Watcher`), seeded with `opened.mtime`:
  the service's first stat (on a thread) is compared against the seed, so
  a write between the read and the watch is a `file-changed` at once;
  `watcher.update(handle, mtime)` after each fill and save keeps the seed
  a reconnect's `reset` re-sends. The listener is the bound method
  `_on_file_changed` (held weakly by the watcher; `_watched` maps handle →
  `_OpenFile`), so a pane dropped without `shutdown` is not kept alive by
  its watches. `_teardown_page` unwatches one file; `EditorPane.shutdown`
  every one (`TerminalTab.release_editor`, called where the window closes
  a tab for good and when the window itself is destroyed) — without it
  every watch, and through it the pane, outlived its tab. The service's
  `Gio.FileMonitor` debounces 300 ms, stats on a thread and pushes
  `file-changed {handle, path, mtime, size, gone}` once per burst whose
  stat moved. `_check_external` judges it against `opened.mtime` and
  `size`: a clean buffer reloads silently (cursor kept), a dirty one is
  told (Reload), `gone` marks the buffer dirty and says so. An event that
  arrives while a save or load is in flight waits (`pending_change`) for
  the reply's mtime, so the editor's own write never reads as a change.
  The link is installed behind the `files` capability of the service's
  hello (`App._install_file_transport`, decided on every connect like
  git's); against a service without it nothing is sent and every open
  lands in the banner. A reconnect (`remotefiles.reset()` in
  `App._on_connected`) re-sends every live watch. The agent rewrites these files constantly, so this path is
  exercised more than manual saves are.
- A text over the 1 MiB frame cap crosses as `TAG_BLOB` chunks either way
  (`protocol.split_request` / `split_reply`, the `text_chunked` /
  `text_bytes` fields), so a 5 MiB file saves.
- `check_editor_save.py` drives all of it against a scratch service through
  `e2e_service.harness_link()` plus `remotefiles.install(link)`; a widget
  check that opens files needs the same two lines, or every open lands in
  the banner with "Not connected to the service".

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
`collins-gtk-sharp-edges`, `collins-testing` (`check_editor_narrow.py`,
`check_editor_save.py`), `collins-session-mcp-tools` (the API's message
table in `api/protocol.py`: the `fs.*` types).
