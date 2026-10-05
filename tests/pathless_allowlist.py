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
        "diffview:_blob_to_file:Path.exists",  # the git-blobs cache; the blobcache in PR-2.2
        "diffview:_blob_to_file:Path.mkdir",
        "imagediff:_paintable:Path.read_bytes",  # a fetched blob, decoded (PR-2.2: the blobcache's file)
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
        "terminal:_open_file_reference:os.path.isfile",
        "window:<module>:shutil.which",
        "window:MainWindow._on_open_ghostty:Path.is_dir",
        "window:MainWindow._on_open_ghostty:shutil.which",
        "window:MainWindow._on_open_ghostty:subprocess.Popen",
        "window:MainWindow._open_session_file:Path.is_dir",
        "window:MainWindow._visible_project_dir:Path.is_dir",
        # -- the editor's files (PR-2.3) -------------------------------------------
        "editor:EditorPane._watch_external_changes:Gio.File.new_for_path.monitor_file",
        "editor:EditorPane.request_root:Path.is_dir",
        "editorfiles:read_first_line:open",
        "editorfiles:should_highlight:Path.stat",
        "editor:EditorPane.set_agent_files:Path.is_file",
        "editorfiles:image_guard:Path.is_file",
        "editorfiles:image_guard:Path.open",
        "editorfiles:image_guard:Path.stat",
        "editorfiles:load_guard:Path.is_file",
        "editorfiles:load_guard:Path.open",
        "editorfiles:load_guard:Path.stat",
        # The file row's "is there a file to open" check (the editor and the
        # Open In… apps): `fs.stat` once PR-2.3 brings it; the one git-page
        # site still on this machine's disk.
        "gitsidebar:GitSidebar._file_menu_items:Path.is_file",
        # -- the tree, quick open and roots (PR-2.4) -----------------------------------
        "editorfiles:_exists:Path.exists",
        "editorfiles:_exists:Path.is_symlink",
        "editorfiles:_is_file:Path.is_file",
        "editorfiles:follow_scope:Path.is_dir",
        "editorfiles:is_inside:Path.resolve",
        "editorfiles:list_dir:Path.iterdir",
        "filetree:FileTree._create_children:Path.is_symlink",
        "filetree:FileTree._watch:Gio.File.new_for_path.monitor_directory",
        "quickopen:_watch_root:Gio.File.new_for_path.monitor_directory",
        "terminal:PanelTerminal._spawn:Path.is_dir",
        "terminal:PanelTerminal._sync_cwd:Path.is_dir",
        "terminal:PanelTerminal.follow_cwd:Path.is_dir",
        "terminal:TerminalTab.__init__:Path.is_dir",
        "editorfiles:repository_root:Path.exists",
        "editorfiles:walk_files:Path.is_symlink",
        # -- file operations and the clipboard (PR-2.5) --------------------------------
        "editor:EditorPane._rename:Path.rename",
        "editorfiles:paste_entries:shutil.copy2",
        "editorfiles:paste_entries:shutil.copytree",
        "editorfiles:paste_entries:shutil.move",
        "editorfiles:paste_target:Path.is_dir",
        "editorfiles:rename_target:Path.exists",
        "editorfiles:rename_target:Path.is_symlink",
        "editorfiles:paste_entries:Path.is_dir",
        "editorfiles:paste_entries:Path.is_symlink",
        # -- links and root names (PR-2.6) ----------------------------------------------
        "linkpatterns:resolve_path:os.path.exists",
        "terminal:_RootNameLinks._file_names:os.scandir",
        "terminal:_RootNameLinks._rebuild:Gio.File.new_for_path.monitor_directory",
        "transcriptlinks:transcript_links:open",
        "transcriptlinks:transcript_links:os.stat",
        # The new-chat screen's "is this a git checkout / does the worktree
        # exist" checks: `fs.stat` reads, which PR-2.6 brings for the links.
        "newchatview:is_git_checkout:Path.exists",
        "sidebar:SessionSidebar.show_group_menu:Path.exists",
        "window:MainWindow._launch_new_session:Path.exists",
        "window:MainWindow._on_new_chat_send:Path.exists",
        "window:MainWindow._refresh_alt_new_session_item:Path.exists",
        "window:MainWindow._worktree_for_new_session:Path.exists",
        # -- uploads, attachments, lightbox, icons (PR-2.7) ------------------------------
        "projecticons:project_icon_data:Path.read_bytes",
        "projecticons:project_icon_path:Path.is_file",
        "projecticons:project_icon_path:Path.stat",
        "animatedimage:_animation:PixbufAnimation.new_from_file",
        "animatedimage:load:Texture.new_from_filename",
        "composer:ComposerView._add_preview:Texture.new_from_filename",
        "pictures:thumbnail:Pixbuf.new_from_file_at_scale",
    }
)
