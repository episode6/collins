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

The final list (PR-2.8, closed by PR-3.0: D59) is two groups with a
rule each: `DEVICE` (this device's own files and programs, the launchers
of a web address or of a file this device has among them) and
`LOCAL_EXTRAS` (each behind `apilink.is_local()` in its own function;
kept on the list by ruling D49), and `NOT_A_SITE`. The reads the walker
cannot see are recorded in `tests/pathless_allowlist.py` (the D50
record). `UNRULED` (three sites) and `RECORDED` (four `gitloads`
default arguments) are gone since PR-3.0 (D54).

The walker's own reach is pinned at the end of this file: a table of
spellings of a read it must catch, and a table of the ones it is known
to miss (the D50 record names each form), so the vocabulary cannot lose
a word, or be believed to have one it lacks, without a test changing.
This file runs in CI's `test` job with the rest of the unit suite and
skips nothing: it needs `ast` and the source tree, no GTK."""

import ast

import pathless
import pytest
from pathless_allowlist import ALLOWLIST, DEVICE, LOCAL_EXTRAS, NOT_A_SITE

WHAT_TO_DO = """
Each is a call the walker reads as the client opening a file or starting a
program (`module:function:call`). The client opens no file of the service's,
so one of these is the answer, in this order:

1. It reads a path of the service's (a project file, a cwd, a transcript):
   ask the service instead (`remotefiles.stat_path` and friends, off the main
   loop), and the site goes away.
2. It hands a path of the service's to something on this device (an app, the
   file manager, a native chooser's starting folder): put it behind
   `if not apilink.is_local(): return` in the same function and file it in
   `LOCAL_EXTRAS`.
3. It is this device's own file or program (ui-state.json, the caches, the
   desktop entry, a dropped file being uploaded): file it in `DEVICE`.
4. It is no read at all: the name matched an object that is no path and no
   file (a service fact with an `is_dir()` of its own, a module's
   `load_bytes(data)`). Rename a local variable that only looks like a path;
   otherwise add it to `NOT_A_SITE` with a line saying what the receiver is.

The lists are in tests/pathless_allowlist.py. Each is pinned in this file
(`test_each_group_is_pinned…`, `test_what_is_no_site_is_pinned…`), so filing
means editing the pin in the same diff."""


def test_no_site_outside_the_allowlist():
    found = pathless.walk()
    new = sorted(found - ALLOWLIST - set(NOT_A_SITE))
    assert not new, f"new filesystem or subprocess sites in the client: {new}\n{WHAT_TO_DO}"


def test_no_stale_entry_in_the_allowlist():
    found = pathless.walk()
    stale = sorted((ALLOWLIST | set(NOT_A_SITE)) - found)
    assert not stale, f"entries nothing matches any more (remove them): {stale}"


def test_what_is_no_site_is_pinned_with_its_reason_and_its_count():
    """The verification of PR 615, S3: the any-receiver words match by
    name, and a match on an object that is no path has no truthful group
    (`DEVICE` is this device's files, `LOCAL_EXTRAS` needs the ask, the
    other two are closed). `NOT_A_SITE` answers it where a reviewer sees
    it: pinned here by its exact entries, each with a reason and with the
    number of calls it covers, and never also on the allowlist."""
    assert set(NOT_A_SITE) == {"pictures:thumbnail:Gio.File.load_bytes"}
    assert not set(NOT_A_SITE) & ALLOWLIST
    found = pathless.counts()
    for site, (count, reason) in NOT_A_SITE.items():
        assert found.get(site) == count, (site, found.get(site))
        assert len(reason) > 20, site  # says what the receiver is


def test_start_session_asks_the_service_for_the_cli():
    """The `start_session` tool's "is there a claude" check is the service's
    word (`remotecli.missing()`: `service.status`'s ``cli``), never a
    command built from this process's PATH (`provider.new_command()`):
    the walker cannot see that read (it is `shutil.which` inside
    providers.py, D50's record item 2), and it refused the tool from a
    window started without `~/.local/bin` on its PATH while the service,
    with the login shell's PATH and `claude_cli_path` appended, launched
    sessions fine. The service assembles the command at spawn
    (`service/session.py`, `_launch_command`), so the client has no
    command to build here at all."""
    source = (pathless.ROOT / "toolclient.py").read_text(encoding="utf-8")
    calls = {
        node.func.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "missing" in calls  # remotecli.missing()
    assert not calls & {"new_command", "resume_command", "continue_command"}, calls


def test_the_git_pages_sites_are_gone():
    found = pathless.walk()
    for prefix in ("gitpage:", "gitinfo:", "remotegit:", "gitpatch:"):
        left = sorted(s for s in found if s.startswith(prefix))
        assert not left, left
    # `gitloads` calls nothing itself, and since PR-3.0 (D54 (6)) names
    # no site's function as a default argument either: no local runner,
    # no `exists` of this device's.
    assert {s for s in found if s.startswith("gitloads:")} == set()
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


def test_the_list_is_two_groups_that_do_not_overlap():
    """D59: Phase 3's first chunk ends the list with `DEVICE`,
    `LOCAL_EXTRAS` and `NOT_A_SITE` only; nothing else is importable."""
    import pathless_allowlist

    groups = (DEVICE, LOCAL_EXTRAS)
    assert ALLOWLIST == DEVICE | LOCAL_EXTRAS
    assert not hasattr(pathless_allowlist, "UNRULED")
    assert not hasattr(pathless_allowlist, "RECORDED")
    assert len(ALLOWLIST) == sum(len(group) for group in groups)


def test_each_group_is_pinned_so_a_site_is_never_filed_silently():
    """Review of PR 615 (N5): the walker's "no site outside the list" is
    satisfied by adding a line to any group, and `openwith` sits in two of
    them. So each group's size is pinned, and `DEVICE`'s per module: a
    new site means editing these numbers in the same diff, where the
    question "is this really the device's own file" gets asked."""
    # 16, eleven launchers since PR-3.0 (items 7, 8), and the two folder
    # choosers' one site in `choosers` since PR-3.2.
    assert len(LOCAL_EXTRAS) == 26
    per_module: dict[str, int] = {}
    for site in DEVICE:
        module = site.split(":", 1)[0]
        per_module[module] = per_module.get(module, 0) + 1
    assert per_module == {
        "app": 2,
        "blobcache": 12,
        "remoteimages": 4,
        "remoteuploads": 2,
        "uistate": 6,
        "window": 2,
        "desktopentry": 17,
        "updatecheck": 5,
        "buildinfo": 3,
        "connection": 7,
        "sshremote": 12,  # the ssh forward: this device's ssh, its local socket and pid file (PR-3.1)
        "prefs": 1,
        "sidebar": 1,
        "openwith": 5,
        # The launchers handing a URL or a file this device has (PR-3.0 item 7).
        "copylabel": 1,
        "terminal": 1,
        "attachpanel": 2,
        "lightbox": 2,
    }
    assert len(DEVICE) == 85
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
    """`DEVICE` is the device's own: the blob cache, a dropped file being
    uploaded, `ui-state.json`, the export's destination, the desktop entry, the update check, `buildinfo`,
    the service's process, the ssh forward (this device's ssh, its local
    socket), and which programs this device has. A site in
    any other module belongs in another group, or behind the API."""
    modules = {site.split(":", 1)[0] for site in DEVICE}
    assert modules == {
        "app", "blobcache", "remoteimages", "remoteuploads", "uistate", "window", "desktopentry",
        "updatecheck", "buildinfo", "connection", "prefs", "sidebar", "openwith",
        "copylabel", "terminal", "attachpanel", "lightbox", "sshremote",
    }
    # The four modules the launchers brought (PR-3.0 item 7) are there for
    # a launcher each and nothing else: a web or mail address, or a file
    # this device has (a cached copy, or the service's own file handed
    # only to a `local` client by the callers' ask).
    assert {s for s in DEVICE if s.startswith(("copylabel:", "terminal:", "attachpanel:", "lightbox:"))} == {
        "copylabel:open_uri:UriLauncher.new",
        "terminal:_launch_web:UriLauncher.new",
        "attachpanel:AttachmentsView._launch:FileLauncher.new",
        "attachpanel:AttachmentsView._launch_default:FileLauncher.new",
        "lightbox:ImageLightbox._on_open_with_clicked:FileLauncher.new",
        "lightbox:ImageLightbox.present_over:FileLauncher.new",
    }
    # `remoteuploads` is there for the dropped file it reads to upload, and nothing else.
    assert {s for s in DEVICE if s.startswith("remoteuploads:")} == {
        "remoteuploads:_read:Gio.File.query_info",
        "remoteuploads:_read:Gio.File.load_bytes",
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


def test_the_three_unruled_sites_are_gone():
    """PR-3.0 item 9 (D54, D59): the clone dialog's folder check and its
    `gh` lookup, and the visible project's directory, are the service's
    answers (`fs.stat`, `service.status`'s `gh`)."""
    found = pathless.walk()
    for site in (
        "clonedialog:CloneDialog._clone_done:os.path.isdir",
        "clonedialog:CloneDialog._refresh_destination:shutil.which",
        "window:MainWindow._visible_project_dir:Path.is_dir",
    ):
        assert site not in found, site


def test_the_cwd_checks_and_the_terminals_sites_are_gone():
    """D39 (PR-2.8): the tab and its panel shells do not ask this device's
    disk whether a cwd is a directory; the service's `spawn` falls back and
    says where the pty started. `terminal` and `lightbox` have no site left
    but their launchers (PR-3.0 item 7), the ones a click opens with."""
    found = pathless.walk()
    assert sorted(s for s in found if s.startswith(("terminal:", "lightbox:"))) == [
        "lightbox:ImageLightbox._on_open_with_clicked:FileLauncher.new",
        "lightbox:ImageLightbox.present_over:FileLauncher.new",
        "terminal:_launch_default:FileLauncher.new",
        "terminal:_launch_local_uri:FileLauncher.new",
        "terminal:_launch_local_uri:UriLauncher.new",
        "terminal:_launch_web:UriLauncher.new",
    ]


def test_a_clicked_link_of_any_scheme_but_the_webs_is_a_local_extra():
    """PR-3.0 item 8: `_launch_web` is the one launch a client that is not
    local makes for a clicked link (a web or mail address); every other
    scheme goes through `_launch_local_uri`, which asks first."""
    assert "terminal:_launch_web:UriLauncher.new" in DEVICE
    assert {s for s in LOCAL_EXTRAS if s.startswith("terminal:_launch_local_uri:")} == {
        "terminal:_launch_local_uri:FileLauncher.new",
        "terminal:_launch_local_uri:UriLauncher.new",
    }


# -- the walker's reach (the Fable review of PR 615, should-fix 1 and 2) -----------------------
#
# The reviewer's probe wrote one read 48 ways and the walker caught 17.
# These tables are that probe, kept: every spelling the walker catches now,
# and every one it is known to miss (each a form the D50 record in
# `tests/pathless_allowlist.py` names). A real site written any of the first ways is found; one
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
    # -- the launchers (PR-3.0 item 7; on the "unseen" record until then) -------------------
    "Gtk.FileLauncher": (
        "def f(p):\n"
        "    Gtk.FileLauncher.new(Gio.File.new_for_path(p)).launch(None, None, None)\n",
        "FileLauncher.new",
    ),
    "Gtk.UriLauncher": (
        "def f(u):\n    Gtk.UriLauncher.new(u).launch(None, None, None)\n",
        "UriLauncher.new",
    ),
    "Gio.AppInfo.launch_default_for_uri": (
        "def f(u):\n    return Gio.AppInfo.launch_default_for_uri(u, None)\n",
        "AppInfo.launch_default_for_uri",
    ),
    "Gio.AppInfo.launch_default_for_uri_async": (
        "def f(u):\n    Gio.AppInfo.launch_default_for_uri_async(u, None, None, None)\n",
        "AppInfo.launch_default_for_uri_async",
    ),
    "an app's launch": (
        "def f(info, p):\n    info.launch([Gio.File.new_for_path(p)], None)\n",
        "Launcher.launch",
    ),
    "an app's launch on an attribute": (
        "def f(self, p):\n    self.app_info.launch([Gio.File.new_for_path(p)], None)\n",
        "Launcher.launch",
    ),
    "an app's launch_uris": ("def f(app, u):\n    app.launch_uris([u], None)\n", "Launcher.launch_uris"),
    "a launcher's open_containing_folder": (
        "def f(launcher):\n    launcher.open_containing_folder(None, None, None)\n",
        "Launcher.open_containing_folder",
    ),
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
    "getattr with a constant name": ("def f(p):\n    return getattr(os.path, 'isdir')(p)\n", "os.path.isdir"),
    "getattr with a name it cannot read": (
        "def f(p, n):\n    return getattr(os.path, n)(p)\n",
        "getattr.os.path",
    ),
    # -- round two (the verification of PR 615, S2) -----------------------------------------
    "a Gio.File parameter": (
        "def f(gfile):\n    return gfile.load_contents(None)\n",
        "Gio.File.load_contents",
    ),
    "a Gio.File kept on self": (
        "class C:\n    def f(self):\n        return self._gfile.query_info('*', 0, None)\n",
        "C.f:Gio.File.query_info",
    ),
    "a dropped file's bytes": (
        "def f(gfile):\n    return gfile.load_bytes(None)[0]\n",
        "Gio.File.load_bytes",
    ),
    "Gio.File.parse_name": (
        "def f(p):\n    return Gio.File.parse_name(p).load_contents(None)\n",
        "Gio.File.parse_name.load_contents",
    ),
    "Gio.File.new_build_filenamev": (
        "def f(p):\n    return Gio.File.new_build_filenamev([p, 'x']).trash(None)\n",
        "Gio.File.new_build_filenamev.trash",
    ),
    "Gio.file_new_for_path": (
        "def f(p):\n    return Gio.file_new_for_path(p).delete(None)\n",
        "Gio.File.new_for_path.delete",
    ),
    "Vte's spawn_async": (
        "def f(term, argv, cwd):\n    term.spawn_async(0, cwd, argv, None, 0, None, None, -1, None, None)\n",
        "Process.spawn_async",
    ),
    "a SubprocessLauncher's spawnv": (
        "def f(argv):\n    launcher = Gio.SubprocessLauncher.new(0)\n    return launcher.spawnv(argv)\n",
        "Process.spawnv",
    ),
    "Gio.Subprocess(argv=...)": (
        "def f(argv):\n    return Gio.Subprocess(argv=argv, flags=0)\n",
        "Gio.Subprocess",
    ),
    "Gtk.show_uri": ("def f(win, p):\n    Gtk.show_uri(win, 'file://' + p, 0)\n", "Gtk.show_uri"),
    "webbrowser.open": (
        "import webbrowser\ndef f(p):\n    webbrowser.open('file://' + p)\n",
        "webbrowser.open",
    ),
    "codecs.open": ("import codecs\ndef f(p):\n    return codecs.open(p).read()\n", "codecs.open"),
    "os.fwalk": ("def f(p):\n    return list(os.fwalk(p))\n", "os.fwalk"),
    "os.statvfs": ("def f(p):\n    return os.statvfs(p)\n", "os.statvfs"),
    "os.chown": ("def f(p):\n    os.chown(p, 0, 0)\n", "os.chown"),
    "os.listxattr": ("def f(p):\n    return os.listxattr(p)\n", "os.listxattr"),
    "Path.readlink": ("def f(p):\n    return Path(p).readlink()\n", "Path.readlink"),
    "readlink on anything": ("def f(p):\n    return p.readlink()\n", "Path.readlink"),
    "Path.owner": ("def f(p):\n    return Path(p).owner()\n", "Path.owner"),
    "is_fifo on anything": ("def f(p):\n    return p.is_fifo()\n", "Path.is_fifo"),
    "is_socket on anything": ("def f(p):\n    return p.is_socket()\n", "Path.is_socket"),
    "Gtk.Picture.new_for_file": (
        "def f(g):\n    return Gtk.Picture.new_for_file(g)\n",
        "Picture.new_for_file",
    ),
    "set_file": ("def f(picture, g):\n    picture.set_file(g)\n", "Widget.set_file"),
    "load_from_file": ("def f(css, g):\n    css.load_from_file(g)\n", "Widget.load_from_file"),
    "Gtk.Builder.new_from_file": (
        "def f(p):\n    return Gtk.Builder.new_from_file(p)\n",
        "Builder.new_from_file",
    ),
    "Pixbuf.get_file_info": (
        "from gi.repository import GdkPixbuf\ndef f(p):\n    return GdkPixbuf.Pixbuf.get_file_info(p)\n",
        "Pixbuf.get_file_info",
    ),
    "savev": ("def f(pixbuf, p):\n    pixbuf.savev(p, 'png', [], [])\n", "Widget.savev"),
    "save_to_png": ("def f(texture, p):\n    texture.save_to_png(p)\n", "Widget.save_to_png"),
    "GtkSource.FileLoader": (
        "from gi.repository import GtkSource\ndef f(b, sf):\n    return GtkSource.FileLoader.new(b, sf)\n",
        "FileLoader.new",
    ),
    "GLib.IOChannel.new_file": (
        "def f(p):\n    return GLib.IOChannel.new_file(p, 'r')\n",
        "IOChannel.new_file",
    ),
    # -- the Fable pass of 2026-10-18 (S1): five families the record did not name ------------
    # 1. A path's method handed on or bound without being called.
    "Path.is_dir handed to map": ("def f(ps):\n    return list(map(Path.is_dir, ps))\n", "Path.is_dir"),
    "Path.exists handed to filter": ("def f(ps):\n    return list(filter(Path.exists, ps))\n", "Path.exists"),
    "Path.stat as a key": ("def f(ps):\n    return max(ps, key=Path.stat)\n", "Path.stat"),
    "Path.read_text bound and called later": (
        "def f(p):\n    fn = Path.read_text\n    return fn(p)\n",
        "Path.read_text",
    ),
    "pathlib.Path.is_file handed on": (
        "def f(ps):\n    return any(map(pathlib.Path.is_file, map(Path, ps)))\n",
        "Path.is_file",
    ),
    # 2. The keyword-property constructor of a GTK file opener, and set_property.
    "Gtk.Picture(file=)": ("def f(g):\n    return Gtk.Picture(file=g)\n", "Widget.Picture(file=)"),
    "Gtk.Image(file=)": ("def f(p):\n    return Gtk.Image(file=p)\n", "Widget.Image(file=)"),
    "Gtk.MediaFile(file=)": ("def f(g):\n    return Gtk.MediaFile(file=g)\n", "Widget.MediaFile(file=)"),
    "Gtk.Video(filename=)": ("def f(p):\n    return Gtk.Video(filename=p)\n", "Widget.Video(filename=)"),
    "GtkSource.FileLoader(buffer=, file=)": (
        "from gi.repository import GtkSource\n"
        "def f(b, sf):\n    return GtkSource.FileLoader(buffer=b, file=sf)\n",
        "Widget.FileLoader(file=)",
    ),
    "GtkSource.FileSaver(buffer=, location=)": (
        "from gi.repository import GtkSource\n"
        "def f(b, g):\n    return GtkSource.FileSaver(buffer=b, location=g)\n",
        "Widget.FileSaver(location=)",
    ),
    "set_property('file', g)": (
        "def f(picture, g):\n    picture.set_property('file', g)\n",
        "Widget.set_property.file",
    ),
    # 3. io's other openers, and posixpath.
    "io.FileIO": ("def f(p):\n    return io.FileIO(p).read()\n", "io.FileIO"),
    "io.open_code": ("def f(p):\n    return io.open_code(p)\n", "io.open_code"),
    "from io import FileIO": ("from io import FileIO\ndef f(p):\n    return FileIO(p)\n", "io.FileIO"),
    "posixpath.isdir": ("import posixpath\ndef f(p):\n    return posixpath.isdir(p)\n", "os.path.isdir"),
    # 4. Vte's third spawn.
    "a terminal's spawn_with_fds_async": (
        "def f(term, argv):\n    term.spawn_with_fds_async(0, None, argv, None, [], [], 0, None, None, -1)\n",
        "Process.spawn_with_fds_async",
    ),
    "a pty's spawn_with_fds_async": (
        "def f(pty, argv):\n    pty.spawn_with_fds_async(None, argv, None, [], [], 0, None, None, -1)\n",
        "Process.spawn_with_fds_async",
    ),
    # 5. A Gio.File renamed, re-moded or made.
    "Gio.File renamed": (
        "def f(p):\n    return Gio.File.new_for_path(p).set_display_name('x', None)\n",
        "Gio.File.new_for_path.set_display_name",
    ),
    "Gio.File re-moded": (
        "def f(p):\n    Gio.File.new_for_path(p).set_attribute_uint32('unix::mode', 0o700, 0, None)\n",
        "Gio.File.new_for_path.set_attribute_uint32",
    ),
    "Gio.File.new_tmp": ("def f():\n    return Gio.File.new_tmp('x-XXXXXX', None)\n", "Gio.File.new_tmp"),
}


@pytest.mark.parametrize("spelling", sorted(CAUGHT))
def test_the_walker_catches(spelling):
    body, expected = CAUGHT[spelling]
    sites = pathless.sites_of("m", ast.parse(_HEADER + body))
    if spelling == "a loop over iterdir":
        assert sites == {"m:f:Path.iterdir", "m:f:Path.unlink"}
    elif expected.startswith("C.f:"):
        assert sites == {f"m:{expected}"}
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
    "an app's launch on a receiver with no hint": (
        "def f(app, p):\n    app.launch([Gio.File.new_for_path(p)], None)\n"
    ),
    "os.kill": "def f(pid):\n    os.kill(pid, 15)\n",
    "a function out of a dict": "def f(p, table):\n    return table['isdir'](p)\n",
    "a bare open handed on": "def f(ps):\n    return list(map(open, ps))\n",
    "another library's reader": "def f(p):\n    import zipfile\n    return zipfile.ZipFile(p)\n",
    "a Gio.File with no read": "def f(dialog, p):\n    dialog.set_initial_folder(Gio.File.new_for_path(p))\n",
    # -- round two: the verifier's spellings that stay unseen (each on the record) ---------
    "a star import": "from os.path import *\ndef f(p):\n    return isdir(p)\n",
    "a module bound to a name": "def f(a):\n    mod = subprocess\n    return mod.run(a)\n",
    "os.path bound to a name": "def f(p):\n    op = os.path\n    return op.getsize(p)\n",
    "__import__": "def f(p):\n    return __import__('os').path.isdir(p)\n",
    "asyncio's subprocess": (
        "import asyncio\n"
        "async def f(a):\n"
        "    return await asyncio.create_subprocess_exec(*a)\n"
    ),
    "pty.spawn": "import pty\ndef f(a):\n    return pty.spawn(a)\n",
    "multiprocessing": "import multiprocessing\ndef f(fn):\n    multiprocessing.Process(target=fn).start()\n",
    "a bare .spawn on an object": "def f(launcher, a):\n    return launcher.spawn(a)\n",
    "sqlite3": "import sqlite3\ndef f(p):\n    return sqlite3.connect(p)\n",
    "Gio.DesktopAppInfo.new_from_filename": "def f(p):\n    return Gio.DesktopAppInfo.new_from_filename(p)\n",
    "a call's result and a slash": "def f(k):\n    return (cache_place() / k).exists()\n",
    "a subscript": "def f(found):\n    return found[0].exists()\n",
    "a path kept on self under a plain name": (
        "class C:\n    def __init__(self, x):\n        self._p = Path(x)\n"
        "    def f(self):\n        return self._p.exists()\n"
    ),
    "open() on a hintless parameter": "def f(p):\n    with p.open() as fh:\n        return fh.read()\n",
    "a Gio.File's shared word on a parameter": "def f(gfile):\n    gfile.trash(None)\n",
    # The Fable pass of 2026-10-18: the one spelling of a GTK file property the
    # walker still does not see (an attribute store, not a call).
    "a props store": "def f(picture, g):\n    picture.props.file = g\n",
}


@pytest.mark.parametrize("spelling", sorted(MISSED))
def test_the_walker_is_known_to_miss(spelling):
    assert pathless.sites_of("m", ast.parse(_HEADER + MISSED[spelling])) == set()


# Honest code: nothing of the service's is read and nothing is started.
# The verifier's 28 spellings, each with what the walker says of it: None
# (no site), or the site it is still named. A flagged one is what
# `NOT_A_SITE` is for when it turns up in the tree (or a renamed variable,
# for the name hints); the table pins which is which, so narrowing or
# widening a word shows here.
HONEST = {
    "getattr of a flag": ("def f():\n    return getattr(os, 'O_NOFOLLOW', 0)\n", None),
    "getattr of a function that reads nothing": ("def f():\n    return getattr(os, 'getuid', None)\n", None),
    "a str named path, replace": ("def f(path):\n    return path.replace('/', '-')\n", None),
    "a str named filename, replace": ("def f(filename):\n    return filename.replace('.png', '')\n", None),
    "glob.escape": ("def f(p):\n    return glob.escape(p)\n", None),
    "shutil.get_terminal_size": ("def f():\n    return shutil.get_terminal_size()\n", None),
    "subprocess.list2cmdline": ("def f(a):\n    return subprocess.list2cmdline(a)\n", None),
    "except subprocess.TimeoutExpired": (
        "def f(g):\n    try:\n        g()\n    except subprocess.TimeoutExpired:\n        pass\n",
        None,
    ),
    "a Gio input stream's read_bytes": ("def f(stream):\n    return stream.read_bytes(4096, None)\n", None),
    "GLib.spawn_check_wait_status": ("def f(s):\n    return GLib.spawn_check_wait_status(s)\n", None),
    "GLib.filename_to_uri": ("def f(p):\n    return GLib.filename_to_uri(p, None)\n", None),
    "a buffer named source, replace": ("def f(source):\n    return source.replace('a', 'b')\n", None),
    "tempfile.gettempdir": ("def f():\n    return tempfile.gettempdir()\n", None),
    "a resource:// Gio.File": (
        "def f():\n    return Gio.File.new_for_uri('resource:///x/y.css').load_contents(None)\n",
        None,
    ),
    "a resource:// Gio.File bound to a name": (
        "def f():\n    g = Gio.File.new_for_uri('resource:///x/y.css')\n    return g.load_bytes(None)\n",
        None,
    ),
    "a dict named roots, get": ("def f(roots, k):\n    return roots.get(k)\n", None),
    "a class of subprocess as a value": (
        "def f():\n    kind = subprocess.CompletedProcess\n    return kind\n",
        None,
    ),
    "a CompletedProcess constructed": (
        "def f(a):\n    return subprocess.CompletedProcess(a, 0, b'', b'')\n",
        None,
    ),
    # -- still named a site: NOT_A_SITE's to answer, or a rename's -----------------------
    "a service entry's own is_dir()": ("def f(entry):\n    return entry.is_dir()\n", "Path.is_dir"),
    "a clipboard's read_text": ("def f(clip):\n    return clip.read_text()\n", "Path.read_text"),
    "a module's own load_bytes(data)": (
        "def f(decoder, data):\n    return decoder.load_bytes(data)\n",
        "Gio.File.load_bytes",
    ),
    "a Gtk.Root named root, resolve": ("def f(root):\n    return root.resolve()\n", "Path.resolve"),
    "a drop target named target, open": ("def f(drop_target):\n    return drop_target.open()\n", "Path.open"),
    "a redirect (contains dir)": ("def f(redirect):\n    return redirect.exists()\n", "Path.exists"),
    "a profile (contains file)": ("def f(profile):\n    return profile.stat()\n", "Path.stat"),
    "a print setting's set_filename": (
        "def f(thing, n):\n    thing.set_filename(n)\n",
        "Widget.set_filename",
    ),
    "any .parent": ("def f(row):\n    return row.parent.exists()\n", "Path.exists"),
    "a service reply named file_info": ("def f(file_info):\n    return file_info.exists()\n", "Path.exists"),
    "a remote entry named remote_file": ("def f(remote_file):\n    return remote_file.stat()\n", "Path.stat"),
    "a one-argument rename on a name with a hint": (
        "def f(target):\n    return target.rename('x')\n",
        "Path.rename",
    ),
    # Honest, and a site by design: this device's own, filed in DEVICE.
    "realpath of this device's own folder": (
        "def f():\n    return os.path.realpath(os.environ.get('XDG_CACHE_HOME', '~'))\n",
        "os.path.realpath",
    ),
}


@pytest.mark.parametrize("spelling", sorted(HONEST))
def test_what_the_walker_says_of_honest_code(spelling):
    body, expected = HONEST[spelling]
    sites = pathless.sites_of("m", ast.parse(_HEADER + body))
    assert sites == (set() if expected is None else {f"m:f:{expected}"})


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
    # The Fable pass of 2026-10-18 (N1): the module's function reassigned
    # as an attribute, or by setattr with a constant name.
    "the module's is_local reassigned as an attribute": (
        "def f(p):\n"
        "    apilink.is_local = lambda: True\n"
        "    if not apilink.is_local():\n"
        "        return\n"
        "    return os.path.isdir(p)\n"
    ),
    "setattr on the module": (
        "def f(p):\n"
        "    setattr(apilink, 'is_local', lambda: True)\n"
        "    if not apilink.is_local():\n"
        "        return\n"
        "    return os.path.isdir(p)\n"
    ),
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
    # The verification of PR 615, S4: every function a qualname names.
    "a second def of the same name that does not ask": (
        "def f(p):\n    if not apilink.is_local():\n        return\n    return os.path.isdir(p)\n"
        "def f(p):\n    return os.path.isdir(p)\n"
    ),
    "a property's getter asks, its setter does not": (
        "class C:\n"
        "    @property\n"
        "    def f(self):\n"
        "        if not apilink.is_local():\n"
        "            return\n"
        "        return os.path.isdir('.')\n"
        "    @f.setter\n"
        "    def f(self, p):\n"
        "        os.path.isdir(p)\n"
    ),
    # A name the function or the module binds itself is not the import.
    "apilink assigned in the function": (
        "def f(p):\n"
        "    apilink = type('X', (), {'is_local': staticmethod(lambda: True)})\n"
        "    if not apilink.is_local():\n"
        "        return\n"
        "    return os.path.isdir(p)\n"
    ),
    "apilink as a parameter": (
        "def f(p, apilink):\n    if not apilink.is_local():\n        return\n    return os.path.isdir(p)\n"
    ),
    "an imported is_local redefined in the module": (
        "import os\nfrom .apilink import is_local\n"
        "def is_local():\n    return True\n"
        "def f(p):\n    if not is_local():\n        return\n    return os.path.isdir(p)\n"
    ),
    "an imported alias shadowed by a loop variable": (
        "import os\nfrom collins.apilink import is_local as ok\n"
        "def f(p, fs):\n"
        "    for ok in fs:\n"
        "        pass\n"
        "    if not ok():\n"
        "        return\n"
        "    return os.path.isdir(p)\n"
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
    found = pathless.sites_of("m", tree)
    site = "m:f:os.path.isdir" if "m:f:os.path.isdir" in found else "m:C.f:os.path.isdir"
    assert site in found
    return pathless.function_asks_local(site, {"m": tree})


def test_both_functions_of_a_name_pass_when_both_ask():
    source = _ASK_HEADER + (
        "class C:\n"
        "    @property\n"
        "    def f(self):\n"
        "        if not apilink.is_local():\n"
        "            return\n"
        "        return os.path.isdir('.')\n"
        "    @f.setter\n"
        "    def f(self, p):\n"
        "        if apilink.is_local() and os.path.isdir(p):\n"
        "            pass\n"
    )
    assert pathless.function_asks_local("m:C.f:os.path.isdir", {"m": ast.parse(source)})
    # A read at a class's own level has no function to ask in.
    tree = ast.parse(_ASK_HEADER + "class C:\n    there = apilink.is_local() and os.path.isdir('.')\n")
    assert pathless.sites_of("m", tree) == {"m:C:os.path.isdir"}
    assert not pathless.function_asks_local("m:C:os.path.isdir", {"m": tree})


@pytest.mark.parametrize("shape", sorted(BEHIND_THE_ASK))
def test_a_site_is_behind_the_local_proof(shape):
    source = BEHIND_THE_ASK[shape]
    if source.startswith("from "):
        source = "import os\n" + source
    assert _asks(source)


@pytest.mark.parametrize("shape", sorted(NOT_BEHIND_THE_ASK))
def test_a_site_is_not_behind_the_local_proof(shape):
    assert not _asks(NOT_BEHIND_THE_ASK[shape])
