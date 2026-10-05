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
  (guarded by `editorfiles.image_stat_guard` over the service's `fs.stat`,
  shown through `animatedimage.load` of the blob `pictures.fetch` lands:
  `kind=file`, PR-2.7; the page opens on the landing).
- `filetree.FileTree`: a `Gtk.ListView` over a `Gtk.TreeListModel`, lazily
  populated by the service's `fs.list` on first expansion (honours
  `editor_show_hidden_files`; the ignored names come in the same answer;
  see "The tree, quick open and roots" below). Icons and
  colors per extension from `filetypes.py` (bundled `ft-*-symbolic` Octicons;
  color classes defined in `app.py`'s scheme provider, Seti-inspired). Context
  menus (Add to chat, Copy, Cut, Paste, Rename…, Open In…) act through the
  service's `fs.rename` / `fs.paste` (`remotefiles.rename_path` /
  `paste_files`; the rules `projectfiles.rename_target` / `paste_target` /
  `unique_target` / `paste_entries` run there); the clipboard payloads
  (Collins' own `x-collins/copied-files` of `collins://` URIs, and for a
  `local` client `Gdk.FileList`, `text/uri-list`,
  `x-special/gnome-copied-files` for cut) are `fileclipboard.py`'s (see
  "File operations and the clipboard" below). A file
  row (and an Agent files row) also gets the git page's *Open In…* submenu
  (`openwithrows.file_open_with_menu` over the `footer_apps` setting the
  pane relays through `set_footer_apps`, plus *Default app* via xdg-open);
  the tree emits `open-with-request(path, app_id)` and
  `EditorPane._open_file_with` launches through `openwith.open_file_with`,
  a failure landing in the banner. Probes: `FileTree.open_with_labels`,
  `activate_open_with`, `EditorPane.agent_file_open_with_labels`.
- `quickopen.QuickOpenDialog`: type-ahead over the service's `fs.walk`
  (asked from a background thread, cached per root, the cache dropped by
  the root's `fs.watch kind: dir`, re-walked on every open anyway), scored
  by `fuzzy.py` (subsequence; basename and segment-start hits win).

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
  the umask; a read-only file of the user's own is refused Permission
  denied, never swapped out; the compare runs right before the write, and
  the reply's mtime is the written descriptor's. A save whose encoding differs from
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
  every watch, and through it the pane, outlived its tab; `shutdown` sets
  `_shut`, so a read or write that lands after it fills nothing and
  installs no watch (a restored tab closed soon after its files reopened). The service's
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

**The tree, quick open and roots are the service's** (split-service spec
§3.23, PR-2.4). The directory reads moved out of `editorfiles` into
`collins/projectfiles.py` (`list_entries`, `list_dir`, `walk_files`,
`repository_root`, `is_inside`, `follow_scope`, `FollowScope`), which the
service runs; the client reaches them only through requests
(`remotefiles.stat_path` / `list_dir` / `walk_root`, blocking, called from a
worker through `remotefiles.off_main`, which lands at
`GLib.PRIORITY_DEFAULT`; the names avoid `stat` / `walk`, which the
pathless walker reads as `Path` methods):

- `fs.list {path, hidden, root}` answers the entries in the tree's order
  (`kind` `file`, `dir`, or `symlink` — a link to a folder inside the root,
  shown as a folder and never expanded; a link out of the root is not
  listed), each marked `ignored` by one check-ignore the service runs in
  the folder (`gitinfo.ignored_names`, which used to run on the main loop
  per listing), at most 5000 and `truncated`. Confined to a root the
  service knows (`files.allowed`) and to the root it names.
- **`Gtk.TreeListModel` calls the children function for every directory row
  it binds**, to draw the expander, and drops the model it gets (measured:
  one call per bound row, two more per expansion). So `_create_children`
  returns the node's own store (`_Node.children`, made once) and costs
  nothing; the listing and the watch start in `_open_dir`, from the row's
  `notify::expanded` (hooked in `_on_bind`) or from `reveal`. The old code
  listed every visible folder on bind and paired its monitor with a store
  the model then dropped, so a subfolder's live refresh never landed.
- An expanded folder holds `fs.watch kind: dir` (handle → path in
  `_watched`; the bound method `_on_dir_changed` is the listener, held
  weakly by the watcher): the service's `monitor_directory`, debounced
  300 ms into one `dir-changed {handle, path}` per burst, and the tree lists
  again (`_list`: one listing per folder in flight, a change meanwhile
  re-lists once it lands; `_epoch` turns over with the root). The root is
  watched too (it is the folder always open). Only what is on screen holds
  a watch: a collapse drops the folder's and every folder's under it
  (`_close_dir`, parked in `_parked`, the rows kept; GTK collapses the rows
  under a collapsed one, so they come back closed), and a re-expansion
  watches and lists it again (`_open_dir`). The service bounds watches per
  client (`files.MAX_WATCHES_PER_CLIENT`, 4096: every tab's open files,
  expanded folders and quick-open roots share it). `_splice` keeps the node
  of every entry still there (a dimming change is `_Node.set_dim`, in
  place), so a folder expanded inside a refreshed one stays expanded; a
  folder that left the listing is forgotten (its store and watch). A
  listing refused `gone` with `protocol.FOLDER_GONE_MSGID` empties the
  folder's rows and forgets it (the root keeps its store and watch); one
  refused any other way (no service, no `files` cap) leaves the rows, and
  the reconnect lists every folder shown again (`_on_reconnect`, through
  `remotefiles.Watcher.on_reset`), so a pane made while the service was
  unreachable fills in. `forget_dir`, `set_root` and `shutdown` (from
  `EditorPane.shutdown`) unwatch.
- On the service, `fs.list`, `fs.walk` and every `fs.watch` are confined
  on the worker (`files._confine` against `_roots(client)`, computed on the
  main loop, as `fs.read` / `fs.write` do); `fs.list`'s client-named
  `root` is held to the known roots too. A watch is installed when its
  confinement lands (`Files._pending`: an `fs.unwatch` first wins), and one
  Gio cannot make a monitor for is refused `failed`. A walk also stops
  queueing folders at `projectfiles.WALK_DIRS_CAP` (50 000).
- `reveal(path)` is a walk that waits: it goes as far as the rows already
  listed, expands and opens the next folder, and resumes from each
  listing's landing (`_continue_reveal`); a newer reveal or a re-root
  replaces it.
- `fs.walk {root, hidden}` answers at most 20 000 relative paths; the walk's
  `paths` and the listing's `entries` are `protocol.CHUNKED_JSON_FIELDS`
  (past a frame they travel as their JSON in `TAG_BLOB` frames).
- `fs.stat {path, root}` answers the kind a symlink names (`symlink` only
  for a dangling one, `missing`), a file's size, the mtime and `inside`
  (of *root*, or of every root the service knows); it is unconfined.
  `TerminalTab.ask_can_open_in_editor(path, then)` is `can_open_in_editor`
  over it (the window's `_open_in_editor` asks each tab's root in turn on
  one worker; `open_in_editor` and `show_image` defer their tool reply;
  a clicked path or image and an attachment wait for the answer). The
  Agent files list (`set_agent_files`: one pass of stats in flight, the
  latest paths kept), a fresh open (the load's worker refuses one outside
  the root; a reload is not confined, so a file a re-root left open
  outside goes on reloading), an image page (`editorfiles.
  image_stat_guard`; the on-disk `image_guard` went when PR-2.7 put the
  lightbox on the blob GET) and
  the git page's file-row menu (`GitSidebar._file_on_disk`; the
  right-click asks off the main loop, the e2e probes block) all ask it.

**File operations and the clipboard are the service's** (split-service
spec §3.23, PR-2.5, D35). The rename and paste rules moved out of
`editorfiles` into `projectfiles.py` with the directory reads
(`RenameError` / `PasteError` are re-exported for the editor's messages,
and `rename_name_error`, the pure name check, for the client to run
first); nothing in `editor.py`, `filetree.py` or `fileclipboard.py`
touches the disk.

- `fs.rename {path, target, root}` (`EditorPane._rename` →
  `remotefiles.rename_path` on a worker through `_off_main`): the name is
  checked on the client (`editorfiles.rename_name_error`: empty or
  path-shaped → the banner, unchanged → nothing, no round trip), the rest
  on the service (`projectfiles.rename_entry`: the entry is there, the
  target is the same directory's — a rename never moves things elsewhere,
  a paste of a cut does — the name is free, a broken symlink counts as
  taken, both resolve inside *root*: a rename across roots or through a
  link out is `outside`). A refusal carries the rule as its `reason`
  (`protocol.FS_RENAME_REASONS`; `remotefiles.rename_reason` turns it
  back into a `RenameError` for `_rename_error_message`); a failure
  (permissions) comes with the OS's words. The reply's `mtime` is the
  renamed file's (null for a folder): `_retarget_open(old, new, mtime)`
  re-keys the open tabs as before and takes that mtime for the file at
  `new` when it differs, then re-watches, so the next save expects the
  file as it is. Then `forget_dir` / `refresh_dir` / `reveal` in that
  order (the tree's listings are asynchronous).
- `fs.paste {entries, target, cut, root}` (`_paste` → `remotefiles.
  paste_files`, landing in `_pasted`): `projectfiles.paste_entries` on the
  service, a copy or a move for a cut, never over anything (a taken name
  lands as "name (copy)", `unique_target`), a folder never into itself.
  Each source is confined on the worker: a client that is not `local`
  may name a source only inside a root the service knows
  (`PasteError.SOURCE_OUTSIDE`, per entry — the rest of the clipboard
  still lands; a source inside *another* known root is fine: the one
  clipboard moves a cut from project B's tree into project A's, D43); a
  `local` client may paste anything, and the service does the copy. The
  reply's `results` are one `remotefiles.PasteOutcome` per entry (source,
  target, error, message, and the landed file's `mtime`; paths as
  strings; a `CHUNKED_JSON_FIELD`, since a thousand deep paths echoed
  twice pass a frame; there is no `placed` list, D42); `_pasted`
  refreshes the tree, re-keys an open file a cut moved
  (`_retarget_open(old, new, mtime)`, which also takes back the "was
  deleted" mark a `file-changed {gone}` set when the service's watch saw
  the file leave its old path before the paste answered — for the moved
  entry itself with the reply's mtime, and for an open file *inside* a
  moved or renamed folder with the mtime it expected before the `gone`,
  `_OpenFile.gone_mtime`, since the move kept it; the re-watch seeds the
  service with it, so a file that really differs is told at once), spends
  the cut
  (`_spend_cut`: what failed stays on the clipboard, still cut) and names
  a failure in the banner. `paste_files` sends the clipboard in slices of
  `protocol.FS_PASTE_MAX` (1000), one request each, and joins the
  outcomes (a slice refused before anything landed raises as one
  request's would; one refused after something landed ends the batching
  with its entries and the unsent ones as `FAILED` outcomes carrying the
  refusal's words), and waits `PASTE_TIMEOUT_S` (a day: a guard, not a
  deadline; the link's death ends the wait sooner) rather than
  `CALL_TIMEOUT_S`, since a folder copy takes as long as it takes and the
  sync channel pipelines the other calls (D41).
- **Placement is exclusive (D44)**: "never over anything" holds against
  every writer, not only Collins' own requests — the agent writes in the
  same folders, and a planted symlink at the chosen name must not be
  written through. `projectfiles` lands a file copy by opening the target
  `O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW` (`_copy_file_exclusive`: the
  bytes, then `copystat`; the source is read only when it is a regular
  file, so a FIFO or a device is `failed`), a tree by `shutil.copytree`
  with that copy function and `dirs_exist_ok` False (`_copy_tree_
  exclusive`: `makedirs` at the top and every subdirectory is the
  exclusive step, links stay links), a same-filesystem move by `os.link`
  + `unlink` for a file and `os.mkdir` + `os.rename` for a directory
  (`_move_exclusive`; across filesystems, `EXDEV` / `EPERM` / `EMLINK`,
  the exclusive copy then the source removed, as `shutil.move` does).
  `EEXIST` at a paste's target is the next name of `unique_target`'s
  sequence (`paste_entries`' loop; `no_room` after the hundredth), at a
  rename's it is `exists`. **No lock** serializes the operations: two
  requests cannot land one name, and a lock would hold every client's
  rename behind a long copy. The tests in `test_projectfiles.py` make
  each race deterministic by patching `unique_target` / `rename_target`
  to plant something at the name they answer. Still as before: a
  cross-filesystem cut that fails mid-tree leaves the partial copy at the
  destination (a retry lands as "tree (copy)") and its `message` is
  `shutil.Error`'s list.
- `fs.rename` is `_move_exclusive` too (the inode kept; `exists` when
  the name was taken between the check and the link), the request's
  `root` is confined on the worker like `fs.list`'s (never `allowed` on
  the main loop), a target name with surrounding whitespace is refused
  `not_a_name` (the dialog's trimming is the client's, before the
  request), and `fs.mkdir` takes a folder called `~` (`name_error`, the
  pure name check `rename_name_error` and `make_directory` share).
- `fs.mkdir {path, root}` (`remotefiles.make_dir`; `projectfiles.
  make_directory`): one folder inside the root, never over anything; the
  refusal's `reason` is one of `protocol.FS_MKDIR_REASONS`. Served for
  Phase 3's path picker (*New folder*); the tree has no caller yet.
- **The clipboard** (`fileclipboard.set_files` / `has_files` /
  `read_files`, each taking a `remotefiles.ClipboardScope` or reading the
  link's: its hello's `service_id` and its `local` proof): Copy and Cut
  always put Collins' own `x-collins/copied-files` payload on the
  clipboard (the GNOME payload's shape — the operation, then one
  `collins://<service id>/<path>` URI per line, `editorfiles.collins_uri`)
  and the paths as plain text; only a `local` client adds the `file:`
  payloads (`Gdk.FileList`, `text/uri-list`, `x-special/gnome-copied-files`),
  since a `file:` URI names a file on the client's machine. Reading
  prefers Collins' own payload (`editorfiles.parse_copied_files(text,
  service_id, local)`: another service's URIs are dropped; `file:` URIs
  only when local), then — local only — the GNOME payload and GDK's file
  list; a payload that yields no path falls through to the next format
  (`read_files`' attempts chain), so a local client beside another local
  Collins still reads the `file:` formats the same clipboard carries.
  `has_files` judges the formats alone, so another service's payload does
  not grey Paste out; the read yields nothing (when not local). A hostile
  line `urlsplit` refuses is dropped, not raised, and a name that is not
  UTF-8 round-trips byte for byte (`collins_uri` / `path_from_collins_
  uri`). PR-2.8's `app.local` gate takes `local` over from the link's
  flag.
- `check_filetree_ops.py` drives all of it against a scratch service:
  the rename with the file open (key, mtime, watch), the renamed folder,
  the refusals, Copy / Paste / Cut through the service, the spent cut,
  the empty clipboard, the non-local scope's formats and reads, `fs.mkdir`.

**Following the session** (`request_root` / `offer_root`): the tab's cwd tick
calls `_maybe_follow_editor`, whose scope is the service's (`cwd.settle`:
`projectfiles.follow_scope` after the settling); `plan_reroot` decides which
open files map to the new root (`renamed_path`), given the counterparts the
service says are files (`reroot_counterparts`, stat'ed with the new root's
`is_dir` on a worker: `request_root` → `_reroot_checked`, superseded by a
newer request). A move queued while the follow dialog was up is judged
again by `cwd.settle` with `judge: true` (`set_follow_judge`, the tab's
`session.judge_cwd`; a pane with no session only offers). Panel shells get
the same offer to `cd` (`_maybe_offer_shells_follow`).

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
`check_editor_save.py`, `check_filetree.py`, `check_filetree_ops.py`), `collins-session-mcp-tools` (the API's message
table in `api/protocol.py`: the `fs.*` types).
