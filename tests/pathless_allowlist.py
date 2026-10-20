# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The pathless allowlist (split-service spec §3.23, Phase 2's rule): the
final list, as PR-2.8 leaves it.

Every filesystem and subprocess site `tests/pathless.py` finds in the
client's modules, named ``module:qualname:call``. Created in PR-2.1 with
every site the client had then; each chunk of Phase 2 took off the sites
it moved to the service (git in PR-2.1 and PR-2.2, the editor's files in
PR-2.3, the tree, quick open and roots in PR-2.4, the file operations in
PR-2.5, the links and the worktree checks in PR-2.6, the pictures in
PR-2.7, the tab's and the shells' cwd checks in PR-2.8, D39). What is
left is in four groups, and `tests/test_client_is_pathless.py` holds
each to its rule:

- `DEVICE`: this device's own files and programs. Nothing here names a
  path of the service's.
- `LOCAL_EXTRAS`: the sites that hand a path of the service's to
  something on this device (another app, the file manager, a native
  chooser's starting folder). They exist only for a client on the
  service's machine: in the function each sits in, every call of the
  site runs only after `apilink.is_local()` (which is `app.local`, the
  `local` proof of D11) answered yes, and the test reads the function to
  see that it does (`pathless.function_asks_local` has the shapes it
  accepts and what it does not prove). **Ruled D49** (2026-10-05): the
  group is part of the final list, and the acceptance's "only device
  files" reads "device files and gated local extras".
- `UNRULED`: three sites that are neither. **Ruled D50** (2026-10-05):
  recorded, not moved; Phase 2 closes with them. **D54** (2026-10-12)
  rules what Phase 3 does with the list. The test pins the group so it
  can only shrink.
- `RECORDED`: four more that are neither, in `gitloads`, which the walker
  could not see until it learned to read a function handed on without
  being called (the 2026-10-13 round, below). Under D50 they are
  recorded, not moved, and no ruling names them yet. Pinned the same way.

**What the tests enforce** (`tests/test_client_is_pathless.py`). A new
site in a GTK module or a client helper fails the suite until it is
filed here, and so does an entry nothing matches any more (a site that
moved comes off the list in the same PR). Filing is a deliberate,
reviewed edit, not a way round the rule: each group is pinned by its
exact size (`DEVICE` 67 and per module, `LOCAL_EXTRAS` 16, `UNRULED` at
most its three, `RECORDED` at most its four), so a site cannot be added
to any of them without that test changing in the same diff, where a
reviewer sees it. And a match that is **no read at all** — the walker's
any-receiver words landing on an object that is no path and no file (a
module's own `load_bytes(data)`, a service fact with an `is_dir()` of
its own) — goes in none of the groups, whose meanings it cannot
satisfy: it is answered in `NOT_A_SITE`, one line saying what the
receiver is, pinned by its exact contents the same way. The list is not frozen in number: it went from 45 to
69 in PR-2.8 when the walker took in four more modules (28 sites they
already had) and the four `terminal` cwd sites came off, to 70 with the
cache folder's `chmod`, to 72 with the two `connection.unit_state` sites
PR 604's fix round added (the `systemctl show` of a timed-out wait: this
device's unit), and to 88 on 2026-10-13 when the walker's vocabulary
grew (the Fable review of PR 615, should-fix 1): twelve `DEVICE` sites
it had been blind to (a `tmp = path.with_suffix(...)` receiver, a
generator's `p.is_dir()`, `realpath`, `mkstemp`) and the four of
`RECORDED`; and to 90 in round two of the same day (the verification's
S2) with `remoteuploads._read`'s two reads of the dropped file it is
uploading, which the walker could not see while a `Gio.File` method
needed a receiver it had watched being made. No code moved for any of
the eighteen. What does not change
is what may be on the list: this device's own files and programs, and
local extras behind the proof.
"""

DEVICE = frozenset(
    {
        # -- the app's own icons: the bundled path beside the package ----------------
        "app:<module>:Path.resolve",
        "app:_icon_root:Path.is_dir",  # which of the three places the icons are installed in
        # -- ~/.cache/collins/blobs: the blob cache (PR-2.2, PR-2.7) -----------------
        "blobcache:fetch:Path.exists",
        "blobcache:fetch:Path.mkdir",
        "blobcache:fetch:os.chmod",  # the cache folder kept 0700 (review of PR 615, N2)
        "blobcache:fetch:Path.read_text",  # a blob's saved tag
        "blobcache:fetch:Path.unlink",
        "blobcache:fetch:Path.write_text",
        "blobcache:fetch:os.replace",  # the fetch's own temporary into place
        "blobcache:fetch:os.unlink",
        "blobcache:fetch:os.utime",  # a 304 marks the blob used: the prune clock
        "blobcache:fetch:tempfile.mkstemp",  # that temporary, made in the cache folder
        # The one read of a fetched blob's bytes, refused outside the cache
        # (the `realpath` is that refusal): every decoder (animatedimage,
        # pictures, imagediff, remoteicons) reads through it.
        "blobcache:read:Path.read_bytes",
        "blobcache:read:os.path.realpath",
        # The cache's prune (`blobcache` calls it on its own folder).
        "remoteimages:prune_stale:Path.is_file",
        "remoteimages:prune_stale:Path.iterdir",
        "remoteimages:prune_stale:Path.stat",
        "remoteimages:prune_stale:Path.unlink",
        # -- a dropped or picked file of this device's, read to be uploaded to the
        # service (D37): the `Gio.File` the drop handed over, its type and size
        # checked and its bytes read on a worker ---------------------------------------
        "remoteuploads:_read:Gio.File.query_info",
        "remoteuploads:_read:Gio.File.load_bytes",
        # -- ui-state.json: this device's half of the state (§3.8) -------------------
        "uistate:UiState._load:Path.read_text",
        "uistate:UiState.save:Path.exists",
        "uistate:UiState.save:shutil.copy2",  # a `ui-state.json` that would not parse, kept as `.corrupt`
        "uistate:write_json_atomic:Path.mkdir",
        "uistate:write_json_atomic:Path.write_text",  # the temporary beside it
        "uistate:write_json_atomic:Path.replace",  # into place
        # -- the Markdown export's destination: the file the chooser named. The
        # transcript is the service's (`store.transcript-export`, PR-2.8) ------------
        "window:MainWindow._on_export_save.work:Path.write_text",
        # -- the desktop entry: `collins --install-desktop` and the sidebar's
        # *Add to applications* write this device's launcher, icon and units ----------
        "desktopentry:<module>:Path.resolve",
        "desktopentry:_refresh:shutil.which",
        "desktopentry:_refresh:subprocess.run",
        "desktopentry:_reload_units:shutil.which",
        "desktopentry:_reload_units:subprocess.run",
        "desktopentry:install:Path.is_file",  # the package's own unit and icon files
        "desktopentry:install:Path.mkdir",
        "desktopentry:install:Path.read_text",
        "desktopentry:install:Path.write_text",
        "desktopentry:install:shutil.copyfile",
        "desktopentry:is_installed:Path.is_file",
        "desktopentry:launcher_path:Path.is_file",
        "desktopentry:launcher_path:Path.resolve",
        "desktopentry:launcher_path:shutil.which",
        "desktopentry:service_launcher_path:Path.is_file",
        "desktopentry:service_launcher_path:Path.resolve",
        "desktopentry:service_launcher_path:shutil.which",
        # -- the update check: its stamp under ~/.cache/collins, and whether this
        # device has a `gh` to ask with ------------------------------------------------
        "updatecheck:gh_usable:shutil.which",
        "updatecheck:read_record:Path.read_text",
        "updatecheck:write_record:Path.mkdir",
        "updatecheck:write_record:Path.write_text",  # the temporary beside the stamp
        "updatecheck:write_record:Path.replace",  # into place
        # -- buildinfo: which build this is, read off the package's own checkout ------
        "buildinfo:<module>:Path.resolve",
        "buildinfo:_read:shutil.which",
        "buildinfo:_read:subprocess.run",
        # -- the service's process, which this device finds and starts (§3.10,
        # §3.20): its pid, its launcher, the user unit ----------------------------------
        "connection:pid_is_alive:open",
        "connection:service_argv:shutil.which",
        "connection:spawn_service:subprocess.Popen",
        "connection:systemctl_start:shutil.which",
        "connection:systemctl_start:subprocess.run",
        "connection:unit_state:shutil.which",  # the systemctl show of a timed-out wait (604)
        "connection:unit_state:subprocess.run",
        "prefs:PreferencesDialog._on_restart:subprocess.Popen",
        # -- which programs this device has, for the local extras' offers: Ghostty
        # on its PATH (the offer and the launch are LOCAL_EXTRAS' below), the
        # desktop's terminal preference (`xdg-terminals.list`, the alternatives
        # link, `$TERMINAL`) -----------------------------------------------------------
        "sidebar:<module>:shutil.which",
        "window:<module>:shutil.which",
        "openwith:_alternatives_terminal:os.access",
        "openwith:_alternatives_terminal:os.path.realpath",  # /etc/alternatives/… to the terminal
        "openwith:_alternatives_terminal:shutil.which",
        "openwith:_configured_terminal_ids:Path.read_text",
        "openwith:_from_command:shutil.which",
    }
)

# In the function each of these sits in, every call of the site runs only
# after `apilink.is_local()` answered yes: behind an earlier `if not
# apilink.is_local(): return` in the function's own body, inside `if
# apilink.is_local():`, or after the ask in the same `and`. The test reads
# the function for that (`pathless.function_asks_local`).
LOCAL_EXTRAS = frozenset(
    {
        # -- another app, the file manager, a terminal: a folder or a file of the
        # service's handed to a program of this device's ------------------------------
        "footerapps:launch_app:Path.is_dir",
        "footerapps:launch_app:subprocess.Popen",
        "footerapps:launch_app_file:Path.is_file",
        "openwith:launch_terminal:Path.is_dir",
        "openwith:launch_terminal:subprocess.Popen",
        "openwith:open_file_default:Path.is_file",
        "openwith:open_file_default:shutil.which",  # xdg-open
        "openwith:open_file_default:subprocess.Popen",
        # -- the attachments panel's Open With… and Show in Folder ----------------------
        "attachpanel:AttachmentsView._on_show_folder:os.path.exists",
        "attachpanel:AttachmentsView._with_local_file:os.path.isfile",
        # -- Open in Ghostty ---------------------------------------------------------
        "window:MainWindow._on_open_ghostty:Path.is_dir",
        "window:MainWindow._on_open_ghostty:shutil.which",
        "window:MainWindow._on_open_ghostty:subprocess.Popen",
        # -- a native chooser's starting folder (§3.11: the native choosers are a
        # `local` client's; PR-3.2 gives the others the path picker) --------------------
        "clonedialog:CloneDialog._browse_target:os.path.isdir",
        "prefs:PreferencesDialog._browse_clone_directory:os.path.isdir",
        "window:MainWindow._open_session_file:Path.is_dir",
    }
)

# Not this device's files and not local extras: a path of the service's
# read on this device, in code no chunk of Phase 2 was given.
#
# D50 (split-service spec §6; the PR-2.8 entry's "Rulings of 2026-10-05 on
# the implementer's questions" has the full text): recorded, not moved.
# Phase 2 closes with this list. D54 (2026-10-12) rules what Phase 3 does
# with it, in order: `command_override` leaves the client's resume path
# (item 2 below; PR-3.1), `resume_cwd` becomes a field of the `item` the
# service computes (item 1, and `sidebar._abbreviate_path` with it;
# PR-3.2), native chat is not ported (item 3), and a short allow-list of
# document suffixes decides what a client that is not local hands to a
# desktop app. Nothing may be added to `UNRULED`. The same record covers
# the reads the walker cannot see, because they sit in modules it does
# not walk or go through a helper the service runs too; they are NOT on
# any list and no test pins them, so this comment is where they are
# written down.
#
# Line numbers are as of 2026-10-13 (the round after the Fable review of
# PR 615, on PR 614's `3aaed7b`; the thirteen fix rounds below this PR
# moved most of them, and a later rebase may move them again by a few
# lines). Record only: under
# D50 nothing here was moved or gated.
#
# From the first report:
# - `toolclient.resolve_file` (`os.path.isfile`, toolclient.py:514) and
#   `toolclient.start_session` (`os.path.isdir`, `expanduser`,
#   toolclient.py:878-879)
# - `dialogs.details_dialog` through `provider.parse_details(jsonl_path)`
#   (dialogs.py:846)
# - the replay (`replaymodel.read_session_turns`, replayview.py:32) and
#   `window._open_session_file` through `sessions.session_from_file`
#   (window.py:7072)
# - `clonerepo.destination_status` (disk reads per keystroke in the clone
#   dialog, clonedialog.py:337)
# - `panelhistory.load_all` (terminal.py:5629) and `panelhistory.delete`
#   (terminal.py:5709, window.py:2761, 2871, 2952)
# - `pkgrepos` (sidebar.py:2049, window.py:6275); `clisetup.validate`
#   was here until it moved to the service (item 7 below)
#
# From the Opus review of PR 615:
# 1. `sessions.resume_cwd(session)` (sessions.py: the transcript's tail,
#    then `Path(cwd).is_dir()`), five call sites: window.py:2124 (every
#    session open), window.py:3631 (native chat), window.py:6014 (Open in
#    Ghostty, behind the proof), sidebar.py:1990 (the PR sweep's worker),
#    and sidebar.py:2979 (`_session_cwd`). **The gate order in
#    `show_row_menu`**: it reads `local = apilink.is_local()` at
#    sidebar.py:2673 and then calls `_session_cwd` at :2695 for every
#    client, local or not, before the `if local and cwd` at :2696 that is
#    the only use of the answer: the read (the transcript's tail and an
#    `is_dir`, on every row right-click) is ahead of its gate. Asking it
#    only when `local` would be a gate, not a move; D54 (2) takes the read
#    off the client instead, so it is recorded and left.
# 2. This device's `shutil.which("claude")` standing in for the service's
#    CLI (providers.py:359 `Provider.available()`): through
#    `available_providers()` (window.py:501, 517, 522) and
#    `default_provider()` (window.py:2215, sidebar.py:633, 2279),
#    `chat_variants()` (window.py:523, 3609, 3626; sidebar.py:2678),
#    `new_command()` (toolclient.py:999), and `continue_command()` at
#    window.py:3006, whose result goes to the service as the spawn's
#    `command_override`: this device's `claude` path typed into the
#    service's shell.
# 3. Native chat runs the agent in the client process:
#    `chatsession.py:193` (`subprocess.Popen(argv, cwd=self.cwd)`, with
#    `chats.chat_cwd_or_fallback`), built from window.py:3613, 3630.
# 4. `~/.claude.json` read by the client: `sessions.read_mcp_config()`
#    (dialogs.py:712) and `sessions.configured_mcp_servers(session.cwd)`
#    (dialogs.py:848).
# 5. `state.json` read directly, past the mirror: `AppState(migrate=True)`
#    for the language (app.py:3030), `AppState().get_setting("icon_model")`
#    (dialogs.py:891, 1002), `updatecheck.py:308`.
# 6. `chats.is_chat_cwd`: a service cwd compared with a chats root computed
#    from this device's environment and `realpath`; about twenty call
#    sites (window.py, sidebar.py, dialogs.py:770, newchatview.py:131,
#    switcher.py:94, remotestore.py:551).
# 7. Moved: `clisetup` in the client (`on_path`, `found_at`, `detect`,
#    `validate`, and `apply`, which changed the client process's `PATH`)
#    is the service's word now: `service.status`'s `cli` and `cli.check`,
#    read through `remotecli` by the welcome gate, the welcome dialog and
#    Preferences, and the service applies a written `claude_cli_path` to
#    its own `PATH`. (A window whose PATH lacked the CLI's folder showed
#    the not-found welcome on every launch.) `tokenrefresh`'s `on_path()`
#    always ran on the service.
# 8. `toolclient`'s `os.path.realpath` on service paths: toolclient.py:372
#    and :1006.
# 9. Six native choosers that start at a path of the service's with no
#    gate (`Gio.File.new_for_path` with no read, so the walker sees
#    nothing): window.py:2309, 2324, 2984, 3583 (from
#    `_visible_project_dir`), terminal.py:5011 (the composer's attach),
#    sandboxchip.py:449; and the composer's attach mentions the picked
#    path of this device to the service's CLI, where a drop of the same
#    file uploads. (The three whose starting folder IS read are gated and
#    on `LOCAL_EXTRAS`: prefs.py:480, clonedialog.py:389, window.py:7064.)
#    All of it is PR-3.2's path picker by the spec's 3.11.
# Lesser: this device's `Path.home()` sent as a service cwd
#    (terminal.py:1210, 1991, 2058, 2441; sidebar.py:2414; window.py:2006;
#    D39's fallback absorbs it) and used to abbreviate service paths:
#    `formatting.display_path` (formatting.py:405, some twenty call
#    sites), clonerepo.py:309, and `sidebar._abbreviate_path`
#    (sidebar.py:158-168: the "New session in {path}" labels at :301,
#    :315 and :652, and every session row's path label at :878, the one
#    every row shows; missed until the Fable review of PR 615, and D54
#    sends it with item 1).
# For the ruling, not a read: `footer_apps` is a `SERVICE_SETTINGS` key
#    (state.py:330, :517) whose values are desktop-entry ids of the apps
#    of the device that set them. Preferences hides the group from a
#    client that is not local, which reads the setting as "apps on the
#    service's machine"; the setting's comment does not say which.
#
# Added by the implementer's sweep (scratch script: every call from a
# walked module into a function of an unwalked one that touches the disk
# or a process, plus a grep of the walked modules for `realpath`,
# `expanduser`, `abspath`, `tempfile`, `os.kill`; method calls on objects
# were matched by name only, so this is not exhaustive either; re-run on
# 2026-10-13 against the tree the fix rounds left: the same calls, no new
# one, and the two new unwalked modules, `gitfresh` and `quitrules`, read
# nothing):
# - `os.path.expanduser` with this device's home on a path of the
#   service's: `linkpatterns.resolve_path` (linkpatterns.py:142: a `~/x`
#   reference printed in the terminal), `gitloads.py:457` (show_diff's
#   path argument), and `clonerepo.parent_directory` / `destination`
#   (clonedialog.py:118, 331, 336, 338, 385; prefs.py:464, 477: the
#   `clone_directory` setting, a service setting holding `~/…`).
# - `gitinfo`'s `.git` reads (`gitfiles.*`, some thirty call sites in
#   gitinfo.py) and every `gitops.*` call of `gitpage`, `gitsidebar`,
#   `gitinfo` and `window` (some fifty: `read_diff`, `side_bytes` and
#   `tree_state_signature` at gitpage.py:1315, 1316, 1389, `read_status`,
#   `run_plan`, `commit`, …; each takes the transport when one is
#   installed) run on this
#   device when the service's hello lacks the `git` capability
#   (`App` logs "git runs locally" and installs no transport). Every
#   service since PR-2.1 has the capability, so this is a fallback for an
#   older service, which the protocol window does not admit today. The
#   three `subprocess.run` sites of `RECORDED` below are the same
#   fallback, seen by the walker.
# - Seen and judged the device's own (not service facts): the update
#   check's `prstatus.gh_succeeds` / `gh_json` (updatecheck.py:169, 191:
#   this device's `gh` asking GitHub about Collins' releases),
#   `licenses.legal_sections`, `notifycenter.sound_subtitle`
#   (prefs.py:983) and `sound_file` (notifysound.py: the notification
#   sound's file), `connection.py:144`
#   (`os.kill` of the service's pid).
#
# **What the walker cannot see** (`tests/pathless.py`; every form its
# author and three reviews have found, as of the Fable pass of
# 2026-10-18: not a proof that there is no other. The rounds closed the
# cheap ones: an
# `import … as`
# alias, `from os import path as P`, `io.open` / `builtins.open`,
# `glob.*`, `tempfile.*`, `os.path.realpath`, a chain through a path's
# own methods (`Path(p).expanduser().resolve().is_dir()`), `Path.home()`,
# a `Path`-annotated or `Path`-defaulted parameter, a tuple assignment, a
# loop or comprehension over `map(Path, …)` / `.iterdir()`, a name bound
# to a bound name, the method names only a path has on any receiver at
# all (`is_dir`, `is_file`, `read_text`, `read_bytes`, `write_bytes`,
# `iterdir`, `rglob`, `is_symlink`, …), `Gio.File`'s `query_exists` and
# the rest of `query_*`, `Gio.File.new_for_uri` and
# `new_for_commandline_arg`, `GLib.file_*` / `spawn_*` /
# `find_program_in_path`, `Gio.Subprocess`, a site's function handed on
# or bound without being called (`run=subprocess.run`, `map(os.path.isdir,
# ps)`, `fn = os.path.isdir`), a path's method handed on or bound uncalled
# (`filter(Path.is_dir, ps)`, `key=Path.stat`, `fn = Path.read_text`;
# 2026-10-18), `getattr(os.path, …)`, the GTK
# constructors and setters that open or write a file by path or by
# `Gio.File` in their `.new(...)` and `set_*` spellings
# (`Picture.new_for_file`, `set_file`, `load_from_file`,
# `savev`, `save_to_png`, `Builder.new_from_file`,
# `Pixbuf.get_file_info`, `GtkSource.FileLoader.new` / `FileSaver.new`,
# `GLib.IOChannel.new_file`) and, since 2026-10-18, the keyword-property
# constructor (`Gtk.Picture(file=g)`, `Gtk.Image(filename=p)`,
# `GtkSource.FileLoader(buffer=b, location=f)`, `Gtk.MediaFile` /
# `Video` the same) and `set_property("file" | "filename", …)` on any
# receiver; `io.FileIO`, `io.open_code` and `posixpath.*` (read as
# `os.path`); `spawn_with_fds_async` (Vte's third spawn); a `Gio.File`
# renamed or re-moded (`set_display_name`, `set_attribute*`) and
# `Gio.File.new_tmp`; the method names only a `Gio.File` has on
# any receiver (`load_contents`, `load_bytes`, `query_info`,
# `query_exists`, `enumerate_children`, `monitor_file`,
# `replace_contents`, …: a parameter, an attribute), `Gio.File.parse_name`
# / `new_build_filenamev` / the `Gio.file_new_for_path` form,
# `spawn_async` / `spawn_sync` / `spawnv` on any receiver (Vte's
# terminal, a `Gio.SubprocessLauncher`), `Gio.Subprocess(argv=…)`,
# `Gtk.show_uri`, `webbrowser.*`, `codecs.open`, `os.fwalk` / `statvfs` /
# `chown` / `listxattr` / `getxattr`, and `Path.readlink` / `owner` /
# `is_fifo` / `is_socket`). Still unseen, and a reviewer's to catch:
# - **A helper's read.** A call into a function of a module the walker
#   does not walk (`sessions`, `providers`, `chats`, `clisetup`,
#   `clonerepo`, `toolclient`, `panelhistory`, `gitops`, …) or into one
#   of the walked module's own functions: the site is named by the
#   function the read is written in, never by its callers. Everything
#   above this paragraph is of this kind.
# - **A receiver with no hint**, for the method names other objects use
#   too: `exists`, `stat`, `rename`, `replace`, `resolve`, `unlink`,
#   `mkdir`, `rmdir`, `open`, `glob`, `touch`, `chmod`, `walk`, `lstat`,
#   `samefile` and `write_text` (which is also `Session.write_text`) on a
#   bare parameter (`p.exists()`), an attribute (`self._thing.unlink()`),
#   a call's result (`somewhere(p).exists()`, `(cache_dir() / k)
#   .exists()`), a subscript (`paths[0].exists()`), or a name bound
#   through a container or another function. Names carrying `path`,
#   `file`, `dir`, `target`, `folder`, `root`, `dest` or `source` and
#   names bound to a path in the same function are seen; a path kept on
#   `self` in one method and read in another is seen only when the
#   attribute's name carries a hint. The same for a `Gio.File`'s shared
#   words (`trash`, `delete`, `copy`, `move`, `read`, `replace`,
#   `create`) on a receiver the walker did not watch being made.
# - **A process started some other way**: `asyncio.create_subprocess_*`,
#   `pty.spawn` / `pty.fork`, `multiprocessing`, `concurrent.futures`'
#   process pool, a bare `.spawn(...)` on an object (a word the client's
#   own `spawn` request uses), `Gio.AppInfo.create_from_commandline`,
#   D-Bus activation.
# - **A star import** (`from os.path import *`, then a bare `isdir(p)`),
#   and a module or `os.path` itself bound to a name (`sp = subprocess;
#   sp.run(a)`).
# - **The launchers**: `Gtk.FileLauncher`, `Gtk.UriLauncher`,
#   `Gio.AppInfo.launch` / `launch_uris` / `launch_default_for_uri`,
#   `open_containing_folder`. They open nothing themselves; they hand a
#   path to a program of this device's, which is what a local extra is.
#   Every one today is gated by hand or works on this device's cached
#   copy (D51): attachpanel.py:507-524, lightbox.py:339, 776,
#   terminal.py:319-322, 813, window.py:6098, 6398, footerapps.py:92,
#   120, openwith.py:163, copylabel.py:59 (GitHub, releases, the `gh`
#   install page).
# - **A property store**: `picture.props.file = g` / `.props.filename =`
#   (an `ast.Attribute` store, not a call); the constructor and
#   `set_property` forms are seen.
# - **Not reads, but this device's facts applied to a service path**:
#   `os.path.expanduser`, `os.path.abspath`, `os.getcwd`-relative joins,
#   `os.environ`, `Path.home()` used as a value.
# - `os.kill` and the other signals; `Gdk.FileList` and the clipboard's
#   file formats (behind `clipboard_scope`, read by hand).
# - A read through another library: `json.load` and the like on a handle
#   are seen at the `open`; `zipfile`, `tarfile`, `sqlite3`,
#   `configparser`, `mimetypes`, `importlib.resources` and the rest of
#   the standard library's openers by name (`tokenize.open`, `gzip` /
#   `bz2` / `lzma` `.open`, `xml.etree.ElementTree.parse`, `ctypes.CDLL`,
#   `linecache`, `fileinput`),
#   `Gio.DesktopAppInfo.new_from_filename`, `Gio.Settings`, `GLib.Dir`,
#   `GLib.MappedFile`, `Gio.Resource.load`, `Gio.FileEnumerator`'s own
#   children are not (`GLib.KeyFile.load_from_file` is, by its method's
#   name).
# - A function fetched some other way: out of a dict or a list, through
#   `functools.partial` of a bound name, `eval` / `exec` / `__import__`;
#   a bare `open` handed on (`map(open, …)`); a name the module rebinds
#   after importing it.
# - The modules themselves: only the GTK modules and `CLIENT_HELPERS` are
#   walked. A new GTK-free module the client calls for files is unseen
#   until it is added to `CLIENT_HELPERS`.
# And about `LOCAL_EXTRAS`' rule (`pathless.function_asks_local`): it
# proves that every call of the site in its function runs after
# `apilink.is_local()` answered yes, in three shapes (an earlier
# returning refusal in the function's own body; inside `if <ask>:`; after
# the ask in the same `and`). An ask after the read, in another branch,
# inside a lambda that is never called, bound to a name first, the bare
# attribute, or another object's `is_local()` does not count; every
# function a qualname names is held to it (a property's getter and its
# setter, a function defined twice); and a function that binds `apilink`
# itself (a parameter, an assignment, a loop variable), or a module that
# redefines an imported `is_local`, has no ask, and nor does one that
# stores to `<module>.is_local` as an attribute or `setattr`s it with a
# constant name (2026-10-18). It does not prove
# anything about a helper the function calls before it asks, about a
# rebinding the AST does not show (`globals()`, a `global` statement in
# another function, `setattr` with a name it cannot read),
# nor about a read of the kinds above. All sixteen were read by hand as
# well.
#
# Whether the walker should cover the unwalked modules is part of the
# same ruling.
UNRULED = frozenset(
    {
        # The clone dialog: whether the clone's folder is there once the job
        # says it is done, and whether `gh` (the service's, which does the
        # clone) is installed, for the destination note's "with gh / git".
        "clonedialog:CloneDialog._clone_done:os.path.isdir",
        "clonedialog:CloneDialog._refresh_destination:shutil.which",
        # "Is the visible session's project directory still there", asked
        # synchronously by the five ways a new session picks its folder.
        "window:MainWindow._visible_project_dir:Path.is_dir",
    }
)

# Neither this device's files nor local extras, and seen by the walker
# only since 2026-10-13: each is a site's function named as a default
# argument, not called where it is written (`run=subprocess.run`,
# `exists=os.path.exists`), which the walker did not read before. D50's
# rule for what is neither: recorded, not moved. No ruling names these
# four (D54 was made on the three above); they go with the next one.
RECORDED = frozenset(
    {
        # The local runner `gitops.run_git` falls back to when no transport
        # is installed (a service whose hello lacks the `git` capability:
        # the fallback recorded above for `gitinfo` and `gitpage`). With the
        # transport, which every service since PR-2.1 has, `run` is unused
        # and git runs on the service. Callers: gitpage.py:1210, 1322
        # (the breadcrumb's subject, the commit card), toolclient.py:160.
        "gitloads:commit_message:subprocess.run",
        "gitloads:commit_subject:subprocess.run",
        "gitloads:resolve_commit:subprocess.run",
        # show_diff's `file` argument: a relative path is taken against the
        # agent's cwd when it is only there, and "there" is asked of this
        # device's disk when the client's half runs it (toolclient.py:566,
        # :620); the service's half asks its own (service/diffs.py:200).
        # **What goes wrong for a client on another machine**: an agent that
        # `cd`ed into `sub/` and names `foo.py` (meaning `sub/foo.py`) gets
        # `foo.py`, which is not in the diff, so show_diff reveals nothing
        # or refuses and annotate_diff / highlight_diff add nothing, where
        # the same call with no client attached resolves right. If this
        # device has a checkout at the same path, its disk decides instead.
        # And `gitloads.py:457`'s `expanduser` puts this device's home into
        # a `~/…` argument on the same path. It fails soft (a refusal, a
        # mark not placed); read, not driven.
        "gitloads:diff_file_path:os.path.exists",
    }
)

ALLOWLIST = DEVICE | LOCAL_EXTRAS | UNRULED | RECORDED

# What the walker names a site and is none: no file of anyone's is opened
# and no program started, because the receiver is not a path, not a
# `Gio.File` and not a widget loading a file. The walker's any-receiver
# words (`is_dir`, `read_text`, `load_bytes`, `set_filename`, …) and its
# name hints (`root`, `target`, `profile`, …) match by name, and a name
# is sometimes another object's.
#
# **Not a way round the rule.** An entry says what the receiver *is*, in
# words a reviewer can check against the line; the table is pinned by its
# exact contents in `tests/test_client_is_pathless.py`, so adding one is
# an edit of that test in the same diff; an entry nothing matches any
# more fails the suite. Each entry carries the number of calls it
# answers for, so another call of the same name in the same function is
# not covered by it. If the receiver is a path or a file, of this
# device or of the service, the site belongs in a group above or behind
# the API, never here. Prefer renaming a local variable whose name only
# looks like a path (`redirect`, `profile`) to adding an entry for it.
NOT_A_SITE = {
    # `animatedimage.load_bytes(data)`: a module function that decodes
    # bytes already in memory (they came through `blobcache.read`, which is
    # the site). Not `Gio.File.load_bytes`.
    # (Two calls, both that function: the count is pinned with the entry,
    # so a third `load_bytes` written into `thumbnail` is a new site.)
    "pictures:thumbnail:Gio.File.load_bytes": (2, "animatedimage.load_bytes(data) decodes bytes in memory"),
}
