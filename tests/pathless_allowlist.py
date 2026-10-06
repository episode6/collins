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

**The list never grows** (`tests/test_client_is_pathless.py`): a new
site in a GTK module or a client helper is a failing test, and so is an
entry nothing matches any more (a site that moved comes off the list in
the same PR).
"""

DEVICE = frozenset(
    {
        # -- the app's own icons: the bundled path beside the package ----------------
        "app:<module>:Path.resolve",
        # -- ~/.cache/collins/blobs: the blob cache (PR-2.2, PR-2.7) -----------------
        "blobcache:fetch:Path.exists",
        "blobcache:fetch:Path.mkdir",
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
        "uistate:UiState.save:shutil.copy2",  # the one-time `state.json.pre-split` backup
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
# - `toolclient.resolve_file` (`os.path.isfile`) and
#   `toolclient.start_session` (`os.path.isdir`, `expanduser`)
# - `dialogs.details_dialog` through `provider.parse_details(jsonl_path)`
# - the replay (`replaymodel.read_session_turns`) and
#   `window._open_session_file` through `sessions.session_from_file`
# - `clonerepo.destination_status` (disk reads per keystroke in the clone
#   dialog)
# - `panelhistory.load_all` / `delete`, called from `terminal` and `window`
# - `clisetup.validate`; `pkgrepos`
#
# Whether the walker should cover such modules is part of the same ruling.
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
