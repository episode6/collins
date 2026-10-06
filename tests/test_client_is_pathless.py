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
PR-2.1 added for it.

The final list (PR-2.8) is three groups with a rule each: `DEVICE` (this
device's own files and programs), `LOCAL_EXTRAS` (each in a function that
asks `apilink.is_local()`; kept on the list by ruling D49) and `UNRULED`
(pinned, so it only shrinks; recorded and not moved by ruling D50, with
the reads the walker cannot see listed beside the group in
`tests/pathless_allowlist.py`).
This file runs in CI's `test` job with the rest of the unit suite and
skips nothing: it needs `ast` and the source tree, no GTK."""

import pathless
from pathless_allowlist import ALLOWLIST, DEVICE, LOCAL_EXTRAS, UNRULED


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


# -- the final list (PR-2.8) ------------------------------------------------------------------


def test_the_list_is_three_groups_that_do_not_overlap():
    assert ALLOWLIST == DEVICE | LOCAL_EXTRAS | UNRULED
    assert not DEVICE & LOCAL_EXTRAS and not DEVICE & UNRULED and not LOCAL_EXTRAS & UNRULED


def test_every_local_extra_sits_behind_the_local_proof():
    """§3.12: a site that hands a path of the service's to something on
    this device exists only for a `local` client. Each one's function asks
    `apilink.is_local()` (which is `app.local`); a new launcher that
    forgets the ask fails here, whatever the menus hide."""
    trees = pathless.modules()
    unasked = sorted(site for site in LOCAL_EXTRAS if not pathless.function_asks_local(site, trees))
    assert not unasked, f"local extras whose function never asks apilink.is_local(): {unasked}"


def test_the_walker_tells_a_function_that_asks_from_one_that_does_not(monkeypatch):
    import ast

    source = (
        "import os\n"
        "from . import apilink\n"
        "class C:\n"
        "    def asked(self, p):\n"
        "        if not apilink.is_local():\n"
        "            return\n"
        "        os.path.isdir(p)\n"
        "    def unasked(self, p):\n"
        "        os.path.isdir(p)\n"
        "    def nested(self, p):\n"
        "        if not apilink.is_local():\n"
        "            return\n"
        "        def work():\n"
        "            os.path.isdir(p)\n"
        "os.path.isdir('.')\n"
    )
    tree = ast.parse(source)
    monkeypatch.setattr(pathless, "modules", lambda: {"m": tree})
    assert pathless.sites_of("m", tree) == {
        "m:C.asked:os.path.isdir",
        "m:C.unasked:os.path.isdir",
        "m:C.nested.work:os.path.isdir",
        "m:<module>:os.path.isdir",
    }
    assert pathless.function_asks_local("m:C.asked:os.path.isdir")
    # What does not count: a function with no ask at all, a site at module
    # level, a closure that does not ask itself (the ask has to be in the
    # function the site is named by), a function or a module that is not
    # there.
    assert not pathless.function_asks_local("m:C.unasked:os.path.isdir")
    assert not pathless.function_asks_local("m:C.nested.work:os.path.isdir")
    assert not pathless.function_asks_local("m:<module>:os.path.isdir")
    assert not pathless.function_asks_local("m:C.missing:os.path.isdir")
    assert not pathless.function_asks_local("nope:f:os.path.isdir")


def test_the_device_group_names_no_module_that_takes_a_path_of_the_services():
    """`DEVICE` is the device's own: the blob cache, `ui-state.json`, the
    export's destination, the desktop entry, the update check, `buildinfo`,
    the service's process, and which programs this device has. A site in
    any other module belongs in another group, or behind the API."""
    modules = {site.split(":", 1)[0] for site in DEVICE}
    assert modules == {
        "app", "blobcache", "remoteimages", "uistate", "window", "desktopentry", "updatecheck",
        "buildinfo", "connection", "prefs", "sidebar", "openwith",
    }
    # The four GTK modules in the group are there for exactly these sites.
    assert {s for s in DEVICE if s.startswith(("app:", "window:", "prefs:", "sidebar:"))} == {
        "app:<module>:Path.resolve",
        "window:MainWindow._on_export_save.work:Path.write_text",
        "window:<module>:shutil.which",
        "prefs:PreferencesDialog._on_restart:subprocess.Popen",
        "sidebar:<module>:shutil.which",
    }


def test_the_unruled_group_only_shrinks():
    """Three sites PR-2.8 reported for a ruling (neither device files nor
    local extras, and no chunk's by the spec). Pinned: ruling one takes it
    off here and off the list; nothing joins it."""
    assert UNRULED <= {
        "clonedialog:CloneDialog._clone_done:os.path.isdir",
        "clonedialog:CloneDialog._refresh_destination:shutil.which",
        "window:MainWindow._visible_project_dir:Path.is_dir",
    }


def test_the_cwd_checks_and_the_terminals_sites_are_gone():
    """D39 (PR-2.8): the tab and its panel shells do not ask this device's
    disk whether a cwd is a directory; the service's `spawn` falls back and
    says where the pty started. `terminal` has no site left at all."""
    found = pathless.walk()
    assert not sorted(s for s in found if s.startswith(("terminal:", "lightbox:")))
