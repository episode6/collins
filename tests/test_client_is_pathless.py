# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The client opens no project file and runs no program of the project's
(split-service spec §3.23 "The pathless test"; created in PR-2.1 with the
walker and the allowlist, made the final list in PR-2.8).

Every filesystem and subprocess site `tests/pathless.py` finds in the
GTK modules and the client helpers must be in `tests/pathless_allowlist.
ALLOWLIST`, and every entry there must still match a site: the list
never grows, and a site that moved to the service comes off it in the
same PR. The git page's, gitinfo's and window._run_git's sites are gone
since PR-2.1 (git goes over the API), and the walker covers the modules
PR-2.1 added for it."""

import pathless
from pathless_allowlist import ALLOWLIST


def test_no_site_outside_the_allowlist():
    found = pathless.walk()
    new = sorted(found - ALLOWLIST)
    assert not new, f"new filesystem or subprocess sites in the client: {new}"


def test_no_stale_entry_in_the_allowlist():
    found = pathless.walk()
    stale = sorted(ALLOWLIST - found)
    assert not stale, f"allowlist entries nothing matches any more (remove them): {stale}"


def test_the_git_pages_sites_are_gone():
    found = pathless.walk()
    for prefix in ("gitpage:", "gitinfo:", "remotegit:", "gitloads:", "gitpatch:"):
        left = sorted(s for s in found if s.startswith(prefix))
        assert not left, left
    # The sidebar's last one, the file row's "is there a file to open"
    # check, is `fs.stat`'s since PR-2.4.
    assert sorted(s for s in found if s.startswith("gitsidebar:")) == []
    assert not any(site.startswith("window:MainWindow._run_git") for site in found)
    assert not any(site.startswith("window:MainWindow._on_git_") for site in found)


def test_the_walker_follows_a_name_bound_to_a_path_and_the_image_constructors():
    """A `candidate = Path(root, p); candidate.is_file()` is a site (the
    hint rule alone missed it), a name bound to `Gio.File.new_for_path`
    too, and the texture / pixbuf constructors that open a file."""
    import ast

    source = (
        "from pathlib import Path\n"
        "from gi.repository import Gdk, GdkPixbuf, Gio\n"
        "def f(root, p):\n"
        "    candidate = Path(root, p)\n"
        "    if candidate.is_file():\n"
        "        pass\n"
        "    other = root / p\n"
        "    other.exists()\n"
        "    gfile = Gio.File.new_for_path(p)\n"
        "    gfile.load_contents(None)\n"
        "    Gdk.Texture.new_from_filename(p)\n"
        "    GdkPixbuf.Pixbuf.new_from_file_at_scale(p, 1, 1, True)\n"
        "    plain = 'x'\n"
        "    plain.exists()\n"
    )
    sites = pathless.sites_of("m", ast.parse(source))
    assert sites == {
        "m:f:Path.is_file",
        "m:f:Path.exists",
        "m:f:Gio.File.new_for_path.load_contents",
        "m:f:Texture.new_from_filename",
        "m:f:Pixbuf.new_from_file_at_scale",
    }


def test_the_trees_and_quick_opens_sites_are_gone():
    """PR-2.4: the tree, quick open, the editor's roots and the follow
    scope read no disk on the client (the service's `fs.list`, `fs.walk`,
    `fs.stat`, `cwd.settle`)."""
    found = pathless.walk()
    for prefix in ("filetree:", "quickopen:"):
        left = sorted(s for s in found if s.startswith(prefix))
        assert not left, left
    assert not sorted(s for s in found if s.startswith("editor:"))
    for name in ("list_dir", "walk_files", "repository_root", "follow_scope", "is_inside", "_is_file"):
        left = sorted(s for s in found if s.startswith(f"editorfiles:{name}:"))
        assert not left, left


def test_the_file_operations_sites_are_gone():
    """PR-2.5: the rename, the paste and the clipboard's reads touch no
    disk on the client (the service's `fs.rename`, `fs.paste`, `fs.mkdir`;
    the rules in `projectfiles`, which the service runs)."""
    found = pathless.walk()
    assert not sorted(s for s in found if s.startswith(("editor:", "fileclipboard:", "filetree:")))
    for name in ("rename_target", "paste_target", "paste_entries", "unique_target", "_exists"):
        left = sorted(s for s in found if s.startswith(f"editorfiles:{name}:"))
        assert not left, left


def test_the_walker_covers_the_gtk_modules_and_the_helpers():
    walked = set(pathless.modules())
    for name in ("window", "app", "terminal", "gitpage", "diffview", "editor", "sidebar", "prefs"):
        assert name in walked, name
    for name in pathless.CLIENT_HELPERS:
        assert name in walked, name
    for name in ("gitops", "sessions", "store", "state", "chats", "trust", "sandboxplan"):
        assert name not in walked, name  # shared with the service: the service's reads
