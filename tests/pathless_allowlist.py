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
left is in three groups, and `tests/test_client_is_pathless.py` holds
each to its rule:

- `DEVICE`: this device's own files and programs. Nothing here names a
  path of the service's.
- `LOCAL_EXTRAS`: the sites that hand a path of the service's to
  something on this device (another app, the file manager, a native
  chooser's starting folder). They exist only for a client on the
  service's machine: the function each sits in asks `apilink.is_local()`
  (which is `app.local`, the `local` proof of D11), and the test reads
  the function to see that it does. **Ruled D49** (2026-10-05): the
  group is part of the final list, and the acceptance's "only device
  files" reads "device files and gated local extras".
- `UNRULED`: three sites that are neither. **Ruled D50** (2026-10-05):
  recorded, not moved; Phase 2 closes with them and each needs a ruling
  before Phase 3 starts. The test pins the group so it can only shrink.

**What the tests enforce** (`tests/test_client_is_pathless.py`). A new
site in a GTK module or a client helper fails the suite until it is
filed here, and so does an entry nothing matches any more (a site that
moved comes off the list in the same PR). Filing is a deliberate,
reviewed edit, not a way round the rule: each group is pinned by its
exact size (`DEVICE` 51, `LOCAL_EXTRAS` 16, `UNRULED` at most its three),
so a site cannot be added to any of them without that test changing in
the same diff, where a reviewer sees it. The list is not frozen in
number: it went from 45 to 69 in PR-2.8 when the walker took in four
more modules (28 sites they already had) and the four `terminal` cwd
sites came off, and to 70 with the cache folder's `chmod`. What does not
change is what may be on it: this device's own files and programs, and
local extras behind the proof.
"""

DEVICE = frozenset(
    {
        # -- the app's own icons: the bundled path beside the package ----------------
        "app:<module>:Path.resolve",
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
        # The one read of a fetched blob's bytes, refused outside the cache:
        # every decoder (animatedimage, pictures, imagediff, remoteicons)
        # reads through it.
        "blobcache:read:Path.read_bytes",
        # The cache's prune (`blobcache` calls it on its own folder).
        "remoteimages:prune_stale:Path.is_file",
        "remoteimages:prune_stale:Path.iterdir",
        "remoteimages:prune_stale:Path.stat",
        "remoteimages:prune_stale:Path.unlink",
        # -- ui-state.json: this device's half of the state (§3.8) -------------------
        "uistate:UiState._load:Path.read_text",
        "uistate:UiState.save:Path.exists",
        "uistate:UiState.save:shutil.copy2",  # a `ui-state.json` that would not parse, kept as `.corrupt`
        "uistate:write_json_atomic:Path.mkdir",
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
        "desktopentry:install:Path.mkdir",
        "desktopentry:install:Path.write_text",
        "desktopentry:install:shutil.copyfile",
        "desktopentry:is_installed:Path.is_file",
        "desktopentry:launcher_path:Path.resolve",
        "desktopentry:launcher_path:shutil.which",
        "desktopentry:service_launcher_path:Path.resolve",
        "desktopentry:service_launcher_path:shutil.which",
        # -- the update check: its stamp under ~/.cache/collins, and whether this
        # device has a `gh` to ask with ------------------------------------------------
        "updatecheck:gh_usable:shutil.which",
        "updatecheck:read_record:Path.read_text",
        "updatecheck:write_record:Path.mkdir",
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
        "prefs:PreferencesDialog._on_restart:subprocess.Popen",
        # -- which programs this device has, for the local extras' offers: Ghostty
        # on its PATH (the offer and the launch are LOCAL_EXTRAS' below), the
        # desktop's terminal preference (`xdg-terminals.list`, the alternatives
        # link, `$TERMINAL`) -----------------------------------------------------------
        "sidebar:<module>:shutil.which",
        "window:<module>:shutil.which",
        "openwith:_alternatives_terminal:os.access",
        "openwith:_alternatives_terminal:shutil.which",
        "openwith:_configured_terminal_ids:Path.read_text",
        "openwith:_from_command:shutil.which",
    }
)

# Each of these sits in a function that returns before the site unless
# `apilink.is_local()` (the test reads the function's source for the ask).
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
# Phase 2 closes with this list, and PR-3.1 must not start until each is
# ruled. Nothing may be added here. The same ruling covers reads the
# walker cannot see, because they sit in modules with no GTK import or go
# through a helper the service runs too; they are NOT on this list and no
# test pins them, so this comment is where they are written down:
#
# Line numbers are as of the commit that wrote this (the fix round of PR
# 615's Opus review, which completed the record: its nine families and
# "lesser" ones, plus what the implementer's own sweep added, marked so).
# Record only: under D50 nothing here was moved or gated.
#
# From the first report:
# - `toolclient.resolve_file` (`os.path.isfile`, toolclient.py:514) and
#   `toolclient.start_session` (`os.path.isdir`, `expanduser`,
#   toolclient.py:868-869)
# - `dialogs.details_dialog` through `provider.parse_details(jsonl_path)`
#   (dialogs.py:846)
# - the replay (`replaymodel.read_session_turns`, replayview.py:32) and
#   `window._open_session_file` through `sessions.session_from_file`
#   (window.py:6975)
# - `clonerepo.destination_status` (disk reads per keystroke in the clone
#   dialog, clonedialog.py:337)
# - `panelhistory.load_all` (terminal.py) and `panelhistory.delete`
#   (terminal.py, window.py three sites)
# - `clisetup.validate` (prefs.py:1560, welcome.py:283, 325); `pkgrepos`
#
# From the review of PR 615:
# 1. `sessions.resume_cwd(session)` (sessions.py: the transcript's tail,
#    then `Path(cwd).is_dir()`): window.py:2084 (every session open),
#    window.py:3546, sidebar.py:1990, and sidebar.py:2979 (`_session_cwd`,
#    called by `show_row_menu` before its `local` test, so on every row
#    right-click). It decides the cwd every resume sends.
# 2. This device's `shutil.which("claude")` standing in for the service's
#    CLI (providers.py): `Provider.available()` (window.py, sidebar.py),
#    `chat_variants()` (window.py:520, 3524, 3541; sidebar.py:2678),
#    `new_command()` (toolclient.py:989), and `continue_command()` at
#    window.py:2921, whose result goes to the service as the spawn's
#    `command_override`: this device's `claude` path typed into the
#    service's shell.
# 3. Native chat runs the agent in the client process:
#    `chatsession.py:193` (`subprocess.Popen(argv, cwd=self.cwd)`, with
#    `chats.chat_cwd_or_fallback`), built from window.py:3527, 3545.
# 4. `~/.claude.json` read by the client: `sessions.read_mcp_config()`
#    (dialogs.py:712) and `sessions.configured_mcp_servers(session.cwd)`
#    (dialogs.py:848).
# 5. `state.json` read directly, past the mirror: `AppState(migrate=True)`
#    for the language (app.py:3002), `AppState().get_setting("icon_model")`
#    (dialogs.py:891, 1002), `updatecheck.py:308`.
# 6. `chats.is_chat_cwd`: a service cwd compared with a chats root computed
#    from this device's environment and `realpath`; about twenty call
#    sites (window.py, sidebar.py, dialogs.py:770, newchatview.py:129,
#    switcher.py:94, remotestore.py:551).
# 7. `clisetup` beyond `validate`: `on_path()` (welcome.py:102 on every
#    launch, welcomegate.py:37, prefs.py:1572, tokenrefresh.py:285, 308),
#    `found_at()` (prefs.py:1575), `detect()` (welcome.py:248) and
#    `apply()` (prefs.py:1567, welcome.py:336), which changes the client
#    process's `PATH`.
# 8. `toolclient`'s `os.path.realpath` on service paths: toolclient.py:372
#    and :996.
# 9. Seven native choosers that start at a path of the service's with no
#    gate (`Gio.File.new_for_path` with no read, so the walker sees
#    nothing): window.py:2269, 2284, 2899, 3498 (from
#    `_visible_project_dir`), terminal.py:4938 (the composer's attach),
#    sandboxchip.py:449; and the composer's attach mentions the picked
#    path of this device to the service's CLI, where a drop of the same
#    file uploads. (The three whose starting folder IS read are gated and
#    on `LOCAL_EXTRAS`: prefs.py:480, clonedialog.py:389, window.py:6967.)
#    All of it is PR-3.2's path picker by the spec's 3.11.
# Lesser: this device's `Path.home()` sent as a service cwd
#    (terminal.py:1192, 1967, 2033, 2376; sidebar.py:2414; window.py:1966;
#    D39's fallback absorbs it) and used to abbreviate service paths
#    (`formatting.display_path`, formatting.py:405, twenty call sites;
#    clonerepo.py:309).
#
# Added by the implementer's sweep (scratch script: every call from a
# walked module into a function of an unwalked one that touches the disk
# or a process, plus a grep of the walked modules for `realpath`,
# `expanduser`, `abspath`, `tempfile`, `os.kill`; method calls on objects
# were matched by name only, so this is not exhaustive either):
# - `os.path.expanduser` with this device's home on a path of the
#   service's: `linkpatterns.resolve_path` (linkpatterns.py:142: a `~/x`
#   reference printed in the terminal), `gitloads.py:457` (show_diff's
#   path argument), and `clonerepo.parent_directory` / `destination`
#   (clonedialog.py:118, 331, 336, 338, 385; prefs.py:464, 477: the
#   `clone_directory` setting, a service setting holding `~/…`).
# - `gitinfo`'s `.git` reads (`gitfiles.*`, some twenty call sites in
#   gitinfo.py) and `gitpage`'s `gitops.read_diff` / `side_bytes` /
#   `tree_state_signature` (gitpage.py:1285, 1286, 1359) run on this
#   device when the service's hello lacks the `git` capability
#   (`App` logs "git runs locally" and installs no transport). Every
#   service since PR-2.1 has the capability, so this is a fallback for an
#   older service, which the protocol window does not admit today.
# - Seen and judged the device's own (not service facts): the update
#   check's `prstatus.gh_succeeds` / `gh_json` (updatecheck.py:169, 191:
#   this device's `gh` asking GitHub about Collins' releases),
#   `licenses.legal_sections`, `notifycenter.sound_subtitle`
#   (prefs.py:983: the notification sound's file), `connection.py:120`
#   (`os.kill` of the service's pid), `blobcache`'s `realpath` and
#   `mkstemp` (the cache), `desktopentry` / `buildinfo` / `app`
#   `.resolve()` of the package's own path.
# - The walker's vocabulary has no `realpath`, `expanduser`, `abspath`,
#   `tempfile`, `os.kill` or launcher (`Gtk.FileLauncher`), and misses a
#   receiver it has no hint for (`tmp.write_text` after `.with_suffix()`
#   in uistate.py, `p.is_dir()` in app.py): all on device files today.
#
# Whether the walker should cover such modules, and learn those calls, is
# part of the same ruling.
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

ALLOWLIST = DEVICE | LOCAL_EXTRAS | UNRULED
