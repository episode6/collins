# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The client opens no project file and runs no program of the project's
(split-service spec §3.23 "The pathless test"; created in PR-2.1 with the
walker and the allowlist, made the final list in PR-2.8).

Every filesystem and subprocess site `tests/pathless.py` finds in the
GTK modules and the client helpers must be in `tests/pathless_allowlist.
ALLOWLIST`, and every entry there must still match a site: nothing
joins the list unseen, and a site that moved to the service comes off it in the
same PR (each group is pinned by size, so filing one is an edit a
reviewer sees). The git page's, gitinfo's and window._run_git's sites are gone
since PR-2.1 (git goes over the API), and the walker covers the modules
PR-2.1 added for it.

The final list (PR-2.8) is four groups with a rule each: `DEVICE` (this
device's own files and programs), `LOCAL_EXTRAS` (each behind
`apilink.is_local()` in its own function; kept on the list by ruling
D49), `UNRULED` (pinned, so it only shrinks; recorded and not moved by
ruling D50, with the reads the walker cannot see listed beside the group
in `tests/pathless_allowlist.py`) and `RECORDED` (four `gitloads` sites
the walker has seen only since it reads a function handed on without
being called; pinned the same way).

The walker's own reach is pinned at the end of this file: a table of
spellings of a read it must catch, and a table of the ones it is known
to miss (the D50 record names each form), so the vocabulary cannot lose
a word, or be believed to have one it lacks, without a test changing.
This file runs in CI's `test` job with the rest of the unit suite and
skips nothing: it needs `ast` and the source tree, no GTK."""

import ast

import pathless
import pytest
from pathless_allowlist import ALLOWLIST, DEVICE, LOCAL_EXTRAS, RECORDED, UNRULED


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
    for prefix in ("gitpage:", "gitinfo:", "remotegit:", "gitpatch:"):
        left = sorted(s for s in found if s.startswith(prefix))
        assert not left, left
    # `gitloads` calls nothing itself; what the walker finds there are its
    # default arguments (the local runner behind `gitops`' transport, and
    # show_diff's `exists`), which D50 records: `RECORDED`, and no other.
    assert {s for s in found if s.startswith("gitloads:")} == RECORDED
    # The sidebar's last one, the file row's "is there a file to open"
    # check, is `fs.stat`'s since PR-2.4.
    assert sorted(s for s in found if s.startswith("gitsidebar:")) == []
    assert not any(site.startswith("window:MainWindow._run_git") for site in found)
    assert not any(site.startswith("window:MainWindow._on_git_") for site in found)


def test_the_walker_follows_a_name_bound_to_a_path_and_the_image_constructors():
    """A `candidate = Path(root, p); candidate.is_file()` is a site (the
    hint rule alone missed it), a name bound to `Gio.File.new_for_path`
    too, and the texture / pixbuf constructors that open a file."""
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


def test_the_list_is_four_groups_that_do_not_overlap():
    groups = (DEVICE, LOCAL_EXTRAS, UNRULED, RECORDED)
    assert ALLOWLIST == DEVICE | LOCAL_EXTRAS | UNRULED | RECORDED
    assert len(ALLOWLIST) == sum(len(group) for group in groups)


def test_each_group_is_pinned_so_a_site_is_never_filed_silently():
    """Review of PR 615 (N5): the walker's "no site outside the list" is
    satisfied by adding a line to any group, and `openwith` sits in two of
    them. So each group's size is pinned, and `DEVICE`'s per module: a
    new site means editing these numbers in the same diff, where the
    question "is this really the device's own file" gets asked."""
    assert len(LOCAL_EXTRAS) == 16
    assert len(UNRULED) <= 3
    assert len(RECORDED) <= 4
    per_module: dict[str, int] = {}
    for site in DEVICE:
        module = site.split(":", 1)[0]
        per_module[module] = per_module.get(module, 0) + 1
    assert per_module == {
        "app": 2,
        "blobcache": 12,
        "remoteimages": 4,
        "uistate": 6,
        "window": 2,
        "desktopentry": 17,
        "updatecheck": 5,
        "buildinfo": 3,
        "connection": 7,
        "prefs": 1,
        "sidebar": 1,
        "openwith": 5,
    }
    assert len(DEVICE) == 65
    # `openwith`'s five device sites are lookups of this device's terminal
    # preference; its launchers are local extras and nowhere else.
    assert {s for s in DEVICE if s.startswith("openwith:")} == {
        "openwith:_alternatives_terminal:os.access",
        "openwith:_alternatives_terminal:os.path.realpath",
        "openwith:_alternatives_terminal:shutil.which",
        "openwith:_configured_terminal_ids:Path.read_text",
        "openwith:_from_command:shutil.which",
    }


def test_every_local_extra_sits_behind_the_local_proof():
    """§3.12: a site that hands a path of the service's to something on
    this device exists only for a `local` client. In each one's function
    every call of the site runs only after `apilink.is_local()` (which is
    `app.local`) answered yes; a new launcher that forgets the ask, or
    reads before it, fails here, whatever the menus hide."""
    trees = pathless.modules()
    unasked = sorted(site for site in LOCAL_EXTRAS if not pathless.function_asks_local(site, trees))
    assert not unasked, f"local extras not behind apilink.is_local() in their own function: {unasked}"


def test_the_walker_tells_a_function_that_asks_from_one_that_does_not(monkeypatch):
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
        "app:_icon_root:Path.is_dir",
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


def test_the_recorded_group_only_shrinks():
    """Four `gitloads` sites the walker has seen since 2026-10-13 (a
    site's function as a default argument). Neither device files nor local
    extras; D50's rule is to record them. Pinned like `UNRULED`."""
    assert RECORDED <= {
        "gitloads:commit_message:subprocess.run",
        "gitloads:commit_subject:subprocess.run",
        "gitloads:resolve_commit:subprocess.run",
        "gitloads:diff_file_path:os.path.exists",
    }


def test_the_cwd_checks_and_the_terminals_sites_are_gone():
    """D39 (PR-2.8): the tab and its panel shells do not ask this device's
    disk whether a cwd is a directory; the service's `spawn` falls back and
    says where the pty started. `terminal` has no site left at all."""
    found = pathless.walk()
    assert not sorted(s for s in found if s.startswith(("terminal:", "lightbox:")))


# -- the walker's reach (the Fable review of PR 615, should-fix 1 and 2) -----------------------
#
# The reviewer's probe wrote one read 48 ways and the walker caught 17.
# These tables are that probe, kept: every spelling the walker catches now,
# and every one it is known to miss (each a form the D50 record beside
# `UNRULED` names). A real site written any of the first ways is found; one
# written any of the second ways is a reviewer's to find.

_HEADER = (
    "import builtins, glob, io, os, pathlib, shutil, subprocess, tempfile\n"
    "import os.path as osp, shutil as sh, subprocess as sp\n"
    "from os import path as P\n"
    "from os.path import isdir as there\n"
    "from pathlib import Path\n"
    "from shutil import which\n"
    "from gi.repository import Gio, GLib, Gtk\n"
    "from gi.repository import Gio as G\n"
)

CAUGHT = {
    # -- import aliases ---------------------------------------------------------------------
    "from os.path import isdir as there": ("def f(p):\n    return there(p)\n", "os.path.isdir"),
    "import os.path as osp": ("def f(p):\n    return osp.isdir(p)\n", "os.path.isdir"),
    "from os import path as P": ("def f(p):\n    return P.exists(p)\n", "os.path.exists"),
    "import shutil as sh": ("def f(n):\n    return sh.which(n)\n", "shutil.which"),
    "from shutil import which": ("def f(n):\n    return which(n)\n", "shutil.which"),
    "import subprocess as sp": ("def f(a):\n    return sp.run(a)\n", "subprocess.run"),
    "from gi.repository import Gio as G": (
        "def f(p):\n    return G.File.new_for_path(p).load_contents(None)\n",
        "Gio.File.new_for_path.load_contents",
    ),
    "an import inside the function": (
        "def f(p):\n    import shutil as mine\n    return mine.rmtree(p)\n",
        "shutil.rmtree",
    ),
    # -- open, glob, tempfile, realpath, the os calls ---------------------------------------
    "open for writing": ("def f(p):\n    with open(p, 'w') as fh:\n        fh.write('x')\n", "open"),
    "io.open": ("def f(p):\n    return io.open(p).read()\n", "io.open"),
    "builtins.open": ("def f(p):\n    return builtins.open(p).read()\n", "builtins.open"),
    "glob.glob": ("def f(p):\n    return glob.glob(p + '/*')\n", "glob.glob"),
    "tempfile.mkstemp": ("def f():\n    return tempfile.mkstemp()\n", "tempfile.mkstemp"),
    "os.path.realpath": ("def f(p):\n    return os.path.realpath(p)\n", "os.path.realpath"),
    "os.walk": ("def f(p):\n    return list(os.walk(p))\n", "os.walk"),
    "os.stat": ("def f(p):\n    return os.stat(p)\n", "os.stat"),
    "os.access": ("def f(p):\n    return os.access(p, os.R_OK)\n", "os.access"),
    "os.scandir": ("def f(p):\n    with os.scandir(p) as it:\n        return list(it)\n", "os.scandir"),
    "os.posix_spawn": ("def f(a):\n    return os.posix_spawn(a[0], a, {})\n", "os.posix_spawn"),
    # -- a path, however the receiver is spelled --------------------------------------------
    "pathlib.Path(p).is_dir()": ("def f(p):\n    return pathlib.Path(p).is_dir()\n", "Path.is_dir"),
    "a name bound to Path(p)": ("def f(p):\n    q = Path(p)\n    return q.exists()\n", "Path.exists"),
    "a chain through expanduser": ("def f(p):\n    return Path(p).expanduser().exists()\n", "Path.exists"),
    "a chain through resolve and absolute": (
        "def f(p):\n    return Path(p).absolute().joinpath('x').stat()\n",
        "Path.stat",
    ),
    "a name bound to a chain": (
        "def f(p):\n    q = Path(p).expanduser()\n    return q.unlink()\n",
        "Path.unlink",
    ),
    "Path.home()": ("def f():\n    return (Path.home() / '.x').exists()\n", "Path.exists"),
    "Path(p).glob": ("def f(p):\n    return list(Path(p).glob('*'))\n", "Path.glob"),
    "an unhinted parameter": ("def f(p):\n    return p.is_dir()\n", "Path.is_dir"),
    "an attribute": ("def f(self):\n    return self._cwd_thing.is_dir()\n", "Path.is_dir"),
    "a call's result": ("def f(p):\n    return somewhere(p).is_file()\n", "Path.is_file"),
    "read_text on anything": ("def f(thing):\n    return thing.read_text()\n", "Path.read_text"),
    "a tuple assignment": ("def f(p):\n    a, b = Path(p), Path(p)\n    return a.exists()\n", "Path.exists"),
    "a Path default": ("def f(p=Path('/x')):\n    return p.exists()\n", "Path.exists"),
    "a Path annotation": ("def f(p: Path | None):\n    return p.exists()\n", "Path.exists"),
    "a comprehension over map(Path, ...)": (
        "def f(ps):\n    return [x.exists() for x in map(Path, ps)]\n",
        "Path.exists",
    ),
    "a loop over iterdir": (
        "def f(p):\n    for child in sorted(Path(p).iterdir()):\n        child.unlink()\n",
        None,  # two sites: see the test
    ),
    "an augmented assignment": (
        "def f(p):\n    q = Path(p)\n    q /= 'sub'\n    return q.exists()\n",
        "Path.exists",
    ),
    "a name bound to a bound name": (
        "def f(p):\n    q = Path(p)\n    r = q\n    return r.exists()\n",
        "Path.exists",
    ),
    "a walrus": ("def f(p):\n    if (q := Path(p)).exists():\n        return q\n", "Path.exists"),
    # -- Gio.File ---------------------------------------------------------------------------
    "Gio.File bound to a name": (
        "def f(p):\n    g = Gio.File.new_for_path(p)\n    return g.load_contents(None)\n",
        "Gio.File.new_for_path.load_contents",
    ),
    "Gio.File through a second name": (
        "def f(p):\n    g = Gio.File.new_for_path(p)\n    h = g\n    return h.load_contents(None)\n",
        "Gio.File.new_for_path.load_contents",
    ),
    "Gio.File chained through get_child": (
        "def f(p):\n    return Gio.File.new_for_path(p).get_child('x').trash(None)\n",
        "Gio.File.new_for_path.trash",
    ),
    "query_exists": (
        "def f(p):\n    return Gio.File.new_for_path(p).query_exists(None)\n",
        "Gio.File.new_for_path.query_exists",
    ),
    "new_for_uri": (
        "def f(p):\n    return Gio.File.new_for_uri('file://' + p).load_contents(None)\n",
        "Gio.File.new_for_uri.load_contents",
    ),
    "new_for_commandline_arg": (
        "def f(p):\n    return Gio.File.new_for_commandline_arg(p).query_info('*', 0, None)\n",
        "Gio.File.new_for_commandline_arg.query_info",
    ),
    # -- GLib, Gio's processes, the widgets that open a file --------------------------------
    "GLib.file_get_contents": ("def f(p):\n    return GLib.file_get_contents(p)\n", "GLib.file_get_contents"),
    "GLib.file_test": ("def f(p):\n    return GLib.file_test(p, GLib.FileTest.IS_DIR)\n", "GLib.file_test"),
    "GLib.spawn_async": ("def f(a):\n    return GLib.spawn_async(a)\n", "GLib.spawn_async"),
    "GLib.find_program_in_path": (
        "def f(n):\n    return GLib.find_program_in_path(n)\n",
        "GLib.find_program_in_path",
    ),
    "Gio.Subprocess.new": ("def f(a):\n    return Gio.Subprocess.new(a, 0)\n", "Subprocess.new"),
    "Gtk.Picture.new_for_filename": (
        "def f(p):\n    return Gtk.Picture.new_for_filename(p)\n",
        "Picture.new_for_filename",
    ),
    "set_filename": ("def f(picture, p):\n    picture.set_filename(p)\n", "Widget.set_filename"),
    # -- a site's function named, not called ------------------------------------------------
    "bound to a name and called later": (
        "def f(p):\n    fn = os.path.isdir\n    return fn(p)\n",
        "os.path.isdir",
    ),
    "handed to map": ("def f(ps):\n    return list(map(os.path.isfile, ps))\n", "os.path.isfile"),
    "a default argument": ("def f(a, run=subprocess.run):\n    return run(a)\n", "subprocess.run"),
    "a default through an alias": ("def f(n, find=sh.which):\n    return find(n)\n", "shutil.which"),
    "getattr on the module": ("def f(p):\n    return getattr(os.path, 'isdir')(p)\n", "getattr.os.path"),
}


@pytest.mark.parametrize("spelling", sorted(CAUGHT))
def test_the_walker_catches(spelling):
    body, expected = CAUGHT[spelling]
    sites = pathless.sites_of("m", ast.parse(_HEADER + body))
    if spelling == "a loop over iterdir":
        assert sites == {"m:f:Path.iterdir", "m:f:Path.unlink"}
    else:
        assert sites == {f"m:f:{expected}"}


# What the walker does not see: each a form the D50 record names. A case
# that starts being caught moves to the table above, and its form comes off
# the record in the same change.
MISSED = {
    "a hintless receiver and a shared method name": "def f(p):\n    return p.exists()\n",
    "an attribute and a shared method name": "def f(self):\n    return self._thing.unlink()\n",
    "a call's result and a shared method name": "def f(p):\n    return somewhere(p).stat()\n",
    "write_text with no hint (the session's method too)": "def f(self, t):\n    self.session.write_text(t)\n",
    "a name bound through a container": (
        "def f(ps):\n"
        "    q = [Path(p) for p in ps][0]\n"
        "    return q.exists()\n"
    ),
    "a helper's read": "def f(p):\n    return helper(p)\n\ndef helper(p):\n    pass\n",
    "expanduser (this device's home, no read)": "def f(p):\n    return os.path.expanduser(p)\n",
    "abspath (this device's cwd, no read)": "def f(p):\n    return os.path.abspath(p)\n",
    "Gtk.FileLauncher": (
        "def f(p):\n"
        "    Gtk.FileLauncher.new(Gio.File.new_for_path(p)).launch(None, None, None)\n"
    ),
    "Gtk.UriLauncher": "def f(u):\n    Gtk.UriLauncher.new(u).launch(None, None, None)\n",
    "Gio.AppInfo.launch_default_for_uri": (
        "def f(u):\n"
        "    return Gio.AppInfo.launch_default_for_uri(u, None)\n"
    ),
    "an app's launch": "def f(info, p):\n    info.launch([Gio.File.new_for_path(p)], None)\n",
    "os.kill": "def f(pid):\n    os.kill(pid, 15)\n",
    "a function out of a dict": "def f(p, table):\n    return table['isdir'](p)\n",
    "a bare open handed on": "def f(ps):\n    return list(map(open, ps))\n",
    "another library's reader": "def f(p):\n    import zipfile\n    return zipfile.ZipFile(p)\n",
    "a Gio.File with no read": "def f(dialog, p):\n    dialog.set_initial_folder(Gio.File.new_for_path(p))\n",
}


@pytest.mark.parametrize("spelling", sorted(MISSED))
def test_the_walker_is_known_to_miss(spelling):
    assert pathless.sites_of("m", ast.parse(_HEADER + MISSED[spelling])) == set()


def test_what_is_no_site_stays_none():
    """The new words must not turn ordinary code into sites: a constant or
    a class of a walked module (an annotation, `stdin=subprocess.DEVNULL`),
    a string's own methods, a hinted name bound to no path."""
    source = _HEADER + (
        "def f(text, argv) -> subprocess.CompletedProcess | None:\n"
        "    kind: type[subprocess.Popen] | None = None\n"
        "    flags = (subprocess.DEVNULL, subprocess.PIPE, os.sep, os.path.sep)\n"
        "    name = os.path.basename(text) + os.path.join('a', 'b')\n"
        "    label = text.replace('a', 'b').strip()\n"
        "    words = text.split()\n"
        "    return None\n"
    )
    assert pathless.sites_of("m", ast.parse(source)) == set()


# `function_asks_local`: what counts as "behind `apilink.is_local()`".
_ASK_HEADER = "import os\nfrom pathlib import Path\nfrom . import apilink\n"

BEHIND_THE_ASK = {
    "an earlier refusal that returns": (
        "def f(p):\n    if not apilink.is_local():\n        return None\n    return os.path.isdir(p)\n"
    ),
    "a refusal among other refusals": (
        "def f(one, p):\n"
        "    if one is None or one.remote or not apilink.is_local():\n"
        "        return\n"
        "    os.path.isdir(p)\n"
    ),
    "a refusal that raises": (
        "def f(p):\n"
        "    if not apilink.is_local():\n"
        "        raise ValueError(p)\n"
        "    return os.path.isdir(p)\n"
    ),
    "a refusal whose body ends in the return": (
        "def f(p):\n"
        "    if not apilink.is_local():\n"
        "        print('no')\n"
        "        return\n"
        "    os.path.isdir(p)\n"
    ),
    "after the ask in the same and": (
        "def f(p, picker):\n    if apilink.is_local() and os.path.isdir(p):\n        picker.go(p)\n"
    ),
    "inside if the ask": ("def f(p):\n    if apilink.is_local():\n        return os.path.isdir(p)\n"),
    "inside if the ask and more": (
        "def f(p):\n    if p and apilink.is_local():\n        return os.path.isdir(p)\n"
    ),
    "in the else of a refusal": (
        "def f(p):\n"
        "    if not apilink.is_local():\n"
        "        r = None\n"
        "    else:\n"
        "        r = os.path.isdir(p)\n"
        "    return r\n"
    ),
    "a conditional expression": ("def f(p):\n    return os.path.isdir(p) if apilink.is_local() else None\n"),
    "after not-ask in the same or": ("def f(p):\n    return not apilink.is_local() or os.path.isdir(p)\n"),
    "a lambda made after the refusal": (
        "def f(p, later):\n"
        "    if not apilink.is_local():\n"
        "        return\n"
        "    later(lambda: os.path.isdir(p))\n"
    ),
    "the function imported by name": (
        "from .apilink import is_local as local\n"
        "def f(p):\n    if not local():\n        return\n    os.path.isdir(p)\n"
    ),
    "the module under another name": (
        "from collins import apilink as link\n"
        "def f(p):\n    if not link.is_local():\n        return\n    os.path.isdir(p)\n"
    ),
}

NOT_BEHIND_THE_ASK = {
    "no ask at all": "def f(p):\n    return os.path.isdir(p)\n",
    # The reviewer's three (CONFIRMED by probe at 6c2427a, all three passed then).
    "the read first, the ask afterwards": (
        "def f(p):\n"
        "    r = os.path.isdir(p)\n"
        "    if not apilink.is_local():\n"
        "        return None\n"
        "    return r\n"
    ),
    "another object's is_local()": (
        "def f(p, obj):\n    if not obj.is_local():\n        return None\n    return os.path.isdir(p)\n"
    ),
    "an ask inside a lambda nobody calls": (
        "def f(p):\n    ask = lambda: apilink.is_local()\n    return os.path.isdir(p)\n"
    ),
    # The bug that would open every gate: the attribute is always true.
    "the attribute, not the call": (
        "def f(p):\n    if not apilink.is_local:\n        return None\n    return os.path.isdir(p)\n"
    ),
    "self.is_local()": "def f(self, p):\n    if not self.is_local():\n        return\n    os.path.isdir(p)\n",
    "a clipboard's is_local()": (
        "def f(clipboard, p):\n    if not clipboard.is_local():\n        return\n    os.path.isdir(p)\n"
    ),
    "a refusal that does not return": (
        "def f(p):\n    if not apilink.is_local():\n        print('no')\n    return os.path.isdir(p)\n"
    ),
    "a refusal inside a loop": (
        "def f(ps):\n"
        "    for p in ps:\n"
        "        if not apilink.is_local():\n"
        "            return\n"
        "    return os.path.isdir(ps[0])\n"
    ),
    "a refusal inside another if": (
        "def f(p, x):\n"
        "    if x:\n"
        "        if not apilink.is_local():\n"
        "            return\n"
        "    return os.path.isdir(p)\n"
    ),
    "the ask in another branch": (
        "def f(p):\n    if apilink.is_local():\n        pass\n    return os.path.isdir(p)\n"
    ),
    "the read in the else of the ask": (
        "def f(p):\n"
        "    if apilink.is_local():\n"
        "        return None\n"
        "    else:\n"
        "        return os.path.isdir(p)\n"
    ),
    "the read before the ask in the same and": (
        "def f(p):\n    return os.path.isdir(p) and apilink.is_local()\n"
    ),
    "the ask in an or": ("def f(p, x):\n    if x or apilink.is_local():\n        return os.path.isdir(p)\n"),
    "a refusal in an and": (
        "def f(p, x):\n    if x and not apilink.is_local():\n        return\n    return os.path.isdir(p)\n"
    ),
    "the answer bound to a name first": (
        "def f(p):\n"
        "    local = apilink.is_local()\n"
        "    if not local:\n"
        "        return\n"
        "    return os.path.isdir(p)\n"
    ),
    "one read behind the ask, one not": (
        "def f(p, q):\n"
        "    os.path.isdir(q)\n"
        "    if not apilink.is_local():\n"
        "        return\n"
        "    return os.path.isdir(p)\n"
    ),
    "the read as a default argument": (
        "def f(p, there=os.path.isdir('/')):\n"
        "    if not apilink.is_local():\n"
        "        return\n"
        "    return there\n"
    ),
    "a module that never imported apilink": (
        "import os\ndef f(p):\n    if not apilink.is_local():\n        return\n    return os.path.isdir(p)\n"
    ),
    "is_local imported from elsewhere": (
        "import os\nfrom .remoteuploads import is_local\n"
        "def f(p):\n    if not is_local():\n        return\n    return os.path.isdir(p)\n"
    ),
}


def _asks(source: str) -> bool:
    # A case that brings its own imports starts with them; the rest get
    # the usual `from . import apilink`.
    tree = ast.parse(source if source.startswith("import os\n") else _ASK_HEADER + source)
    assert "m:f:os.path.isdir" in pathless.sites_of("m", tree)
    return pathless.function_asks_local("m:f:os.path.isdir", {"m": tree})


@pytest.mark.parametrize("shape", sorted(BEHIND_THE_ASK))
def test_a_site_is_behind_the_local_proof(shape):
    source = BEHIND_THE_ASK[shape]
    if source.startswith("from "):
        source = "import os\n" + source
    assert _asks(source)


@pytest.mark.parametrize("shape", sorted(NOT_BEHIND_THE_ASK))
def test_a_site_is_not_behind_the_local_proof(shape):
    assert not _asks(NOT_BEHIND_THE_ASK[shape])
