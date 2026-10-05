#!/usr/bin/env python3
# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Wiring check for the editor's tree, quick open and roots over the API
(collins/filetree.py, collins/quickopen.py and collins/editor.py over
collins/remotefiles.py and the service's collins/service/files.py; the
split-service spec §3.23, PR-2.4) — run on a dev machine.

Exercises the GTK side that tests/test_files.py (the service's listings,
walks, stats and directory watches) and tests/test_remotefiles.py (the
client's calls) can't reach: a real EditorPane in a real window against a
service of this check's own. The root's rows arrive from `fs.list`, the
ignored names dimmed and a symlinked folder with no expander; a folder
row the tree model binds is not listed until it is expanded; an expansion
lists it and watches it on the service; a file a shell makes in it
appears (`dir-changed`); a folder open inside a refreshed one stays open;
`reveal` walks down through listings still to come; quick open's walk is
`fs.walk` and finds the new file, and the root's watch drops its cache;
the Agent files list keeps the files inside the project only; a re-root
waits for the service's `fs.stat`; the pane's shutdown drops every
directory watch.

This is a script, not a pytest test, on purpose: tests/conftest.py blocks
the GTK-stack namespaces for the whole suite so local runs reproduce CI.
Testing widgets for real means running this by hand, behind a display
nobody is looking at:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_filetree.py

(CI runs it under Xvfb through scripts/run_e2e.py.)
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# A service of this check's own, on a scratch tree and a fresh app id, so
# nothing of the user's is read or reaped. Set before anything of collins
# is imported.
_SCRATCH = tempfile.mkdtemp(prefix="collins-ft-")
_RUN = "r" + "".join(c for c in os.path.basename(_SCRATCH) if c.isalnum())
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{_RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = os.path.join(_SCRATCH, "projects")
os.environ["COLLINS_CLAUDE_CONFIG"] = os.path.join(_SCRATCH, "claude.json")
os.environ["COLLINS_CHATS_DIR"] = os.path.join(_SCRATCH, "chats")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_SCRATCH, "config")
os.environ["XDG_STATE_HOME"] = os.path.join(_SCRATCH, "state")
os.environ["XDG_CACHE_HOME"] = os.path.join(_SCRATCH, "cache")
os.makedirs(os.environ["COLLINS_PROJECTS_DIR"])
with open(os.environ["COLLINS_CLAUDE_CONFIG"], "w") as _fh:
    _fh.write("{}")

import e2e_service  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

from collins import apilink, quickopen, remotefiles  # noqa: E402
from collins.editor import EditorPane  # noqa: E402

PASSED = 0
FAILED = 0
# How long each asynchronous step gets: a listing's round trip, or the
# service's 300 ms debounce plus the listing that follows it.
STEP_TIMEOUT_S = 8.0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  ok  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}  {detail}")


def wait_for(condition, timeout: float = STEP_TIMEOUT_S) -> bool:
    """Spin the main loop until *condition()* holds or *timeout* passes."""
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if condition():
            return True
        time.sleep(0.02)
    return condition()


def settle(seconds: float = 0.6) -> None:
    wait_for(lambda: False, seconds)


def rows(tree) -> list[tuple[int, str]]:
    """The tree's rows as (depth, name), top to bottom."""
    model = tree._tree_model
    return [(model.get_item(i).get_depth(), model.get_item(i).get_item().name) for i in range(model.get_n_items())]


def row_of(tree, name: str, depth: int = 0):
    model = tree._tree_model
    for i in range(model.get_n_items()):
        row = model.get_item(i)
        if row.get_depth() == depth and row.get_item().name == name:
            return row
    return None


def node_of(tree, name: str, depth: int = 0):
    row = row_of(tree, name, depth)
    return row.get_item() if row is not None else None


def run(root: str) -> int:
    os.makedirs(os.path.join(root, "src", "pkg"))
    os.makedirs(os.path.join(root, "docs"))
    with open(os.path.join(root, "README.md"), "w") as fh:
        fh.write("# hi\n")
    with open(os.path.join(root, "src", "main.py"), "w") as fh:
        fh.write("print('hi')\n")
    with open(os.path.join(root, "src", "pkg", "mod.py"), "w") as fh:
        fh.write("x = 1\n")
    with open(os.path.join(root, ".gitignore"), "w") as fh:
        fh.write("build.log\n")
    with open(os.path.join(root, "build.log"), "w") as fh:
        fh.write("noise\n")
    os.symlink(os.path.join(root, "docs"), os.path.join(root, "docs-link"))
    have_git = shutil.which("git") is not None
    if have_git:
        subprocess.run(["git", "init", "-q", root], check=True)

    pane = EditorPane(root)
    pane.apply_settings({"editor_show_hidden_files": True})
    tree = pane._tree
    window = Gtk.Window(default_width=1000, default_height=760)
    window.set_child(pane)
    pane.set_visible(True)
    window.present()
    if not wait_for(lambda: pane.get_width() > 0):
        print("FAIL  the pane never allocated")
        return 1
    watcher = remotefiles.watcher()

    # -- the root's listing ------------------------------------------------------------
    check("the root's rows arrive from the service", wait_for(lambda: ("README.md" in [n for _d, n in rows(tree)])), rows(tree))
    names = [n for d, n in rows(tree) if d == 0]
    check("folders (and the link to one) first, then files, by name", names == ["docs", "docs-link", "src", ".gitignore", "build.log", "README.md"], names)
    if have_git:
        check("the ignored name is dimmed", wait_for(lambda: node_of(tree, "build.log") is not None and node_of(tree, "build.log").dim), names)
        check("a tracked-looking file is not", not node_of(tree, "README.md").dim)
    link_row = row_of(tree, "docs-link")
    check("the symlinked folder shows as a folder with no expander", link_row is not None and link_row.get_item().is_dir and not link_row.is_expandable(), link_row)
    settle(0.4)
    check("a folder row the model bound is not listed until it is expanded", list(tree._stores) == [root], list(tree._stores))
    check("and only the root is watched on the service yet", list(tree._watches) == [root], tree._watches)

    # -- expansion, its watch, a file from a shell ---------------------------------------------
    src = row_of(tree, "src")
    src.set_expanded(True)
    check("expanding src lists its children", wait_for(lambda: (1, "main.py") in rows(tree)), rows(tree))
    src_path = os.path.join(root, "src")
    check("and watches it on the service", src_path in tree._watches and watcher.watching(tree._watches[src_path]), tree._watches)
    pkg = row_of(tree, "pkg", 1)
    pkg.set_expanded(True)
    check("a folder inside it expands too", wait_for(lambda: (2, "mod.py") in rows(tree)), rows(tree))
    settle(0.5)  # the monitors are in before the shell writes
    with open(os.path.join(src_path, "from_shell.py"), "w") as fh:
        fh.write("made by a shell\n")
    check("a file a shell makes in src appears", wait_for(lambda: (1, "from_shell.py") in rows(tree)), rows(tree))
    check("and the folder open inside src stayed open through the refresh", row_of(tree, "pkg", 1) is not None and row_of(tree, "pkg", 1).get_expanded() and (2, "mod.py") in rows(tree), rows(tree))
    os.remove(os.path.join(src_path, "from_shell.py"))
    check("a file a shell removes goes", wait_for(lambda: (1, "from_shell.py") not in rows(tree)), rows(tree))

    # -- the root is watched too; a folder that goes takes its rows ---------------------------
    check("the root is watched on the service", root in tree._watches and watcher.watching(tree._watches[root]), tree._watches)
    with open(os.path.join(root, "AT_ROOT.md"), "w") as fh:
        fh.write("written at the root by a shell\n")
    check("a file a shell writes at the root appears", wait_for(lambda: (0, "AT_ROOT.md") in rows(tree)), rows(tree))
    os.makedirs(os.path.join(root, "doomed", "inner"))
    check("a folder a shell makes at the root appears", wait_for(lambda: (0, "doomed") in rows(tree)), rows(tree))
    row_of(tree, "doomed").set_expanded(True)
    check("expanding it lists its child", wait_for(lambda: (1, "inner") in rows(tree)), rows(tree))
    shutil.rmtree(os.path.join(root, "doomed"))
    check("removing it takes its row and its children's", wait_for(lambda: (0, "doomed") not in rows(tree) and (1, "inner") not in rows(tree)), rows(tree))
    check("and its watch", wait_for(lambda: os.path.join(root, "doomed") not in tree._watches), tree._watches)

    # -- a collapse drops the folder's watches; a re-expansion lists and watches again ----------
    pkg_path = os.path.join(src_path, "pkg")
    src.set_expanded(False)
    check("collapsing src drops its watch and pkg's", src_path not in tree._watches and pkg_path not in tree._watches, tree._watches)
    with open(os.path.join(src_path, "while_collapsed.py"), "w") as fh:
        fh.write("x\n")
    src.set_expanded(True)
    check("re-expanding src watches it again", wait_for(lambda: src_path in tree._watches), tree._watches)
    check("and the file written while it was collapsed shows", wait_for(lambda: (1, "while_collapsed.py") in rows(tree)), rows(tree))
    # GTK collapses the rows under a collapsed one: pkg comes back closed.
    check("pkg came back closed, and unwatched", not row_of(tree, "pkg", 1).get_expanded() and pkg_path not in tree._watches, tree._watches)
    os.remove(os.path.join(src_path, "while_collapsed.py"))

    # -- reveal through listings still to come --------------------------------------------
    src.set_expanded(False)
    tree.forget_dir(src_path)  # as a rename would: src's rows are listed afresh
    tree.reveal(os.path.join(src_path, "pkg", "mod.py"))

    def selected_name():
        item = tree._selection.get_selected_item()
        return item.get_item().name if item is not None else None

    check("reveal walks down through src and pkg to the file", wait_for(lambda: selected_name() == "mod.py"), (selected_name(), rows(tree)))

    # -- quick open over fs.walk -----------------------------------------------------------
    with open(os.path.join(src_path, "pkg", "quick_target.py"), "w") as fh:
        fh.write("found\n")
    chosen: list[str] = []
    dialog = quickopen.QuickOpenDialog(root, chosen.append, show_hidden=False)
    check("quick open's walk lands", wait_for(lambda: not dialog._indexing), dialog._status.get_text())
    check("and finds the new file", "src/pkg/quick_target.py" in dialog._paths, dialog._paths)
    check("hidden and skipped files are not in it", ".gitignore" not in dialog._paths, dialog._paths)
    dialog._entry.set_text("quick_target")
    check("typing ranks it first", wait_for(lambda: dialog._list.get_row_at_index(0) is not None and dialog._list.get_row_at_index(0).rel_path == "src/pkg/quick_target.py"))
    dialog._activate_selected()
    check("choosing it hands back the absolute path", chosen == [os.path.join(root, "src", "pkg", "quick_target.py")], chosen)
    key = (root, False)
    check("the walk is cached for the root", key in quickopen._index_cache)
    check("and the root is watched on the service", root in quickopen._root_watches and watcher.watching(quickopen._root_watches[root]))
    with open(os.path.join(root, "TOP.md"), "w") as fh:
        fh.write("new at the top\n")
    check("a change at the root drops the cache", wait_for(lambda: key not in quickopen._index_cache), list(quickopen._index_cache))

    # -- the Agent files list ------------------------------------------------------------
    outside = os.path.join(_SCRATCH, "outside.py")
    with open(outside, "w") as fh:
        fh.write("not the project's\n")
    pane.set_agent_files([os.path.join(src_path, "main.py"), outside, os.path.join(root, "gone.py"), os.path.join(root, "README.md")])
    check("the Agent files keep the project's files only", wait_for(lambda: pane._agent_paths == [os.path.join(src_path, "main.py"), os.path.join(root, "README.md")]), pane._agent_paths)
    check("and the list shows", pane._agent_box.get_visible())

    # -- a fresh open of a missing file is "not there", not "outside" ----------------------------
    missing = os.path.join(root, "never-was.py")
    pane.open_file(missing)
    check("a missing file is not opened", wait_for(lambda: missing not in pane._pages and pane._banner.get_revealed()), list(pane._pages))
    check("and the banner does not call it outside the project", "outside this project" not in pane._banner.get_title(), pane._banner.get_title())
    pane._banner.set_revealed(False)

    # -- a pane made while the service is unreachable fills in on the reconnect -------------------
    link = apilink.current()
    apilink.set_current(None)
    try:
        orphan = EditorPane(root)
        settle(0.5)
        check("a pane with no service has an empty tree", rows(orphan._tree) == [], rows(orphan._tree))
    finally:
        apilink.set_current(link)
    remotefiles.reset()  # what App._on_connected does
    check("the reconnect lists its root", wait_for(lambda: (0, "README.md") in rows(orphan._tree)), rows(orphan._tree))
    orphan.shutdown()

    # -- an open outside the root is refused on the load's worker --------------------------------
    pane.open_file(outside)
    check("a file outside the project is not opened", wait_for(lambda: outside not in pane._pages and pane._banner.get_revealed()), list(pane._pages))
    check("and the banner says so", "outside this project" in pane._banner.get_title(), pane._banner.get_title())

    # -- a re-root waits for the service's stat --------------------------------------------
    other = os.path.join(root, "src")
    pane.request_root(os.path.join(root, "no-such-dir"))
    settle(0.6)
    check("a re-root to a missing folder is no move", str(pane.root) == root, pane.root)
    pane.request_root(other)
    check("a re-root to src moves the pane and the tree", wait_for(lambda: str(pane.root) == other and str(tree.root) == other), (pane.root, tree.root))
    check("the tree lists the new root", wait_for(lambda: (0, "main.py") in rows(tree)), rows(tree))
    check("the old root's watches went with it, the new root watched", list(tree._watches) == [other], tree._watches)

    # -- shutdown -------------------------------------------------------------------------
    row_of(tree, "pkg").set_expanded(True)
    check("pkg is watched under the new root", wait_for(lambda: os.path.join(other, "pkg") in tree._watches), tree._watches)
    handles = list(tree._watches.values())
    pane.shutdown()
    check("the pane's shutdown drops the tree's watches", not tree._watches and not any(watcher.watching(h) for h in handles), tree._watches)

    window.destroy()
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


def main() -> int:
    root = tempfile.mkdtemp(prefix="collins-filetree-")
    try:
        link = e2e_service.harness_link()
        remotefiles.install(link)
        return run(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(_SCRATCH, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
