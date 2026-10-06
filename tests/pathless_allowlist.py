# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The pathless allowlist (split-service spec §3.23, Phase 2's rule).

Every filesystem and subprocess site `tests/pathless.py` finds in the
client's modules, named ``module:qualname:call``. Created in PR-2.1 with
every site the client had then, minus the git page's, gitinfo's and
window._run_git's (git goes over the API); each later chunk of Phase 2
removes the sites it moves to the service, and PR-2.8 leaves the final
list: the device's own files (`ui-state.json`, `~/.cache/collins/blobs`
and the other caches, the notification sound, the Markdown export's
destination, the desktop entry, the app's icons, the update check,
`buildinfo`) and the local extras a `local` client lights up.

**The list never grows** (`tests/test_client_is_pathless.py`): a new
site in a GTK module or a client helper is a failing test, and so is an
entry nothing matches any more (a site that moved comes off the list in
the same PR). The groups below say which chunk takes each site.
"""

ALLOWLIST = frozenset(
    {
        # -- this device's own files: stay (PR-2.8 keeps them) --------------------
        "app:<module>:Path.resolve",  # the bundled icons' path
        "blobcache:fetch:Path.exists",
        "blobcache:fetch:Path.mkdir",
        "blobcache:fetch:Path.read_text",
        "blobcache:fetch:Path.unlink",
        "blobcache:fetch:Path.write_text",
        "blobcache:fetch:os.replace",  # the fetch's own temporary into place
        "blobcache:fetch:os.unlink",
        # The one read of a fetched blob's bytes, refused outside the cache:
        # every decoder (animatedimage, pictures, imagediff, remoteicons)
        # reads through it (PR-2.7, in place of imagediff's own read_bytes).
        "blobcache:read:Path.read_bytes",
        "blobcache:fetch:os.utime",  # a 304 marks the blob used: the prune clock (PR-2.2)
        "remoteimages:prune_stale:Path.is_file",
        "remoteimages:prune_stale:Path.iterdir",
        "remoteimages:prune_stale:Path.stat",
        "remoteimages:prune_stale:Path.unlink",
        "uistate:UiState._load:Path.read_text",
        "uistate:UiState.save:Path.exists",
        "uistate:UiState.save:shutil.copy2",
        "uistate:write_json_atomic:Path.mkdir",
        "window:MainWindow._on_export_save.work:Path.write_text",  # the Markdown export's destination
        # -- the service's process, which the client finds and starts (stay) --------
        "connection:pid_is_alive:open",
        "connection:service_argv:shutil.which",
        "connection:spawn_service:subprocess.Popen",
        "connection:systemctl_start:shutil.which",
        "connection:systemctl_start:subprocess.run",
        "prefs:PreferencesDialog._on_restart:subprocess.Popen",
        # -- the local extras (PR-2.8 gates them on the `local` capability) ---------
        "attachpanel:AttachmentsView._on_show_folder:os.path.exists",
        "attachpanel:AttachmentsView._with_local_file:os.path.isfile",
        "clonedialog:CloneDialog._browse_target:os.path.isdir",
        "clonedialog:CloneDialog._clone_done:os.path.isdir",
        "clonedialog:CloneDialog._refresh_destination:shutil.which",
        "footerapps:launch_app:Path.is_dir",
        "footerapps:launch_app:subprocess.Popen",
        "footerapps:launch_app_file:Path.is_file",
        "prefs:PreferencesDialog._browse_clone_directory:os.path.isdir",
        "sidebar:<module>:shutil.which",
        "window:<module>:shutil.which",
        "window:MainWindow._on_open_ghostty:Path.is_dir",
        "window:MainWindow._on_open_ghostty:shutil.which",
        "window:MainWindow._on_open_ghostty:subprocess.Popen",
        "window:MainWindow._open_session_file:Path.is_dir",
        "window:MainWindow._visible_project_dir:Path.is_dir",
        # -- the editor's files (PR-2.3) and the tree, quick open and roots (PR-2.4):
        # the monitors, the first-line read, the highlight stat, the load guard,
        # the listing, the walk, the follow scope, the reroot's and the Agent
        # files' checks and the git page's file-row check went to the service
        # (`fs.read` / `fs.stat` / `fs.list` / `fs.walk` / `cwd.settle`; the
        # directory reads into `projectfiles.py`, which the service runs).
        # Left: the tab's and the panel shells' cwd checks, which fall back to
        # this device's home — the service's home is no request's answer yet
        # (named in PR-2.4's report; PR-2.6 or PR-2.8 settles them) --------------
        "terminal:PanelTerminal._spawn:Path.is_dir",
        "terminal:PanelTerminal._sync_cwd:Path.is_dir",
        "terminal:PanelTerminal.follow_cwd:Path.is_dir",
        "terminal:TerminalTab.__init__:Path.is_dir",
        # -- file operations and the clipboard (PR-2.5): the rename, the paste
        # (and the "is the name taken" check) went to the service (`fs.rename`
        # / `fs.paste` / `fs.mkdir`; the rules into `projectfiles.py`). Nothing
        # left. ------------------------------------------------------------------------
        # -- uploads, attachments, lightbox, icons (PR-2.7): none left. The
        # project icon is the service's `kind=icon` blob, every picture a blob
        # decoded from its bytes through `blobcache.read`; `image_guard` went
        # with its last caller (the editor's image pages guard over `fs.stat`
        # since PR-2.4, the lightbox is on the blob GET) ------------------------
    }
)
