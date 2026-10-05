#!/usr/bin/env python3
# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Wiring check for the editor tree's file operations and the file clipboard
over the API (collins/editor.py, collins/filetree.py and
collins/fileclipboard.py over collins/remotefiles.py and the service's
collins/service/files.py; the split-service spec §3.23, PR-2.5) — run on a
dev machine.

Exercises the GTK side that tests/test_files.py (the service's rename,
paste and mkdir), tests/test_projectfiles.py (the rules) and
tests/test_remotefiles.py (the client's calls) can't reach: a real
EditorPane in a real window against a service of this check's own. A
rename lands on disk through the service and the open buffer follows it
(its key, its mtime, its watch); a renamed folder takes the open file
under it along; a taken name and a path-shaped name end in the banner
with the file untouched; Copy puts Collins' own payload and, for a local
client, the file: payloads on the clipboard; Paste copies through the
service, never over anything; a Cut's paste moves, the open buffer
follows and the cut is spent; an empty clipboard is told; a client that
is not local puts no file: URI on the clipboard and reads none back
(D35); a new folder is `fs.mkdir`.

This is a script, not a pytest test, on purpose: tests/conftest.py blocks
the GTK-stack namespaces for the whole suite so local runs reproduce CI.
Testing widgets for real means running this by hand, behind a display
nobody is looking at:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_filetree_ops.py

(CI runs it under Xvfb through scripts/run_e2e.py.)
"""

import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# A service of this check's own, on a scratch tree and a fresh app id, so
# nothing of the user's is read or reaped. Set before anything of collins
# is imported.
_SCRATCH = tempfile.mkdtemp(prefix="collins-fto-")
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
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

Adw.init()

from collins import fileclipboard, remotefiles  # noqa: E402
from collins.editor import EditorPane  # noqa: E402

PASSED = 0
FAILED = 0
# How long each asynchronous step gets: a request's round trip and the
# listing that follows it.
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


def settle(seconds: float = 0.5) -> None:
    wait_for(lambda: False, seconds)


def rows(tree) -> list[tuple[int, str]]:
    model = tree._tree_model
    return [(model.get_item(i).get_depth(), model.get_item(i).get_item().name) for i in range(model.get_n_items())]


def row_of(tree, name: str, depth: int = 0):
    model = tree._tree_model
    for i in range(model.get_n_items()):
        row = model.get_item(i)
        if row.get_depth() == depth and row.get_item().name == name:
            return row
    return None


def selected_name(tree):
    item = tree._selection.get_selected_item()
    return item.get_item().name if item is not None else None


def banner(pane) -> str:
    return pane._banner.get_title() if pane._banner.get_revealed() else ""


def clear_banner(pane) -> None:
    pane._banner.set_revealed(False)
    pane._banner.set_title("")


def mimes(clipboard) -> set[str]:
    return set(clipboard.get_formats().to_string().split())


def read_clipboard(clipboard, scope=None):
    got: list = []
    fileclipboard.read_files(clipboard, lambda paths, cut: got.append((paths, cut)), scope=scope)
    wait_for(lambda: bool(got))
    return got[0] if got else None


def run(root: str) -> int:
    os.makedirs(os.path.join(root, "src", "pkg"))
    os.makedirs(os.path.join(root, "docs"))
    with open(os.path.join(root, "README.md"), "w") as fh:
        fh.write("# hi\n")
    with open(os.path.join(root, "src", "main.py"), "w") as fh:
        fh.write("print('hi')\n")
    with open(os.path.join(root, "src", "pkg", "mod.py"), "w") as fh:
        fh.write("x = 1\n")

    pane = EditorPane(root)
    tree = pane._tree
    window = Gtk.Window(default_width=1000, default_height=760)
    window.set_child(pane)
    pane.set_visible(True)
    window.present()
    if not wait_for(lambda: pane.get_width() > 0):
        print("FAIL  the pane never allocated")
        return 1
    watcher = remotefiles.watcher()
    clipboard = Gdk.Display.get_default().get_clipboard()
    scope = remotefiles.clipboard_scope()
    check("the harness link is local and names its service", scope.local and scope.service_id, scope)
    check("the root's rows arrive", wait_for(lambda: (0, "README.md") in rows(tree)), rows(tree))

    # -- a rename, with the file open ---------------------------------------------------
    main_py = os.path.join(root, "src", "main.py")
    pane.open_file(main_py)
    check("main.py opens", wait_for(lambda: main_py in pane._open and pane._open[main_py].filled), list(pane._open))
    opened = pane._open[main_py]
    mtime, handle = opened.mtime, opened.watch_handle
    check("and is watched", handle is not None and watcher.watching(handle), handle)
    opened.buffer.set_text("print('edited')\n")
    main2 = os.path.join(root, "src", "main2.py")
    pane._rename(main_py, "main2.py")
    check("the rename lands on disk through the service", wait_for(lambda: os.path.exists(main2) and not os.path.exists(main_py)), os.listdir(os.path.join(root, "src")))
    check("the open buffer follows to the new path", wait_for(lambda: main2 in pane._open and main_py not in pane._open), list(pane._open))
    moved = pane._open[main2]
    check("with its edits kept", moved.buffer.get_text(moved.buffer.get_start_iter(), moved.buffer.get_end_iter(), True) == "print('edited')\n")
    check("the mtime it expects is the renamed file's", moved.mtime == mtime == os.stat(main2).st_mtime_ns // 1000, (moved.mtime, mtime))
    check("the new path is watched and the old one is not", wait_for(lambda: moved.watch_handle is not None and watcher.watching(moved.watch_handle) and not watcher.watching(handle)), (moved.watch_handle, handle))
    check("the tree reveals the renamed file", wait_for(lambda: selected_name(tree) == "main2.py"), (selected_name(tree), rows(tree)))
    check("no banner", not banner(pane), banner(pane))

    # -- a renamed folder takes the open file along --------------------------------------
    source = os.path.join(root, "source")
    pane._rename(os.path.join(root, "src"), "source")
    check("the folder is renamed on disk", wait_for(lambda: os.path.isdir(source) and not os.path.exists(os.path.join(root, "src"))), os.listdir(root))
    under = os.path.join(source, "main2.py")
    check("the open file under it is re-keyed", wait_for(lambda: under in pane._open and main2 not in pane._open), list(pane._open))
    check("and still watched", wait_for(lambda: pane._open[under].watch_handle is not None and watcher.watching(pane._open[under].watch_handle)))
    check("the tree lists the renamed folder", wait_for(lambda: (0, "source") in rows(tree) and (0, "src") not in rows(tree)), rows(tree))

    # -- refusals: a taken name, a path-shaped name ---------------------------------------
    readme = os.path.join(root, "README.md")
    pane._rename(readme, "docs")
    check("a taken name is refused in the banner", wait_for(lambda: "already exists" in banner(pane)), banner(pane))
    check("and nothing moved", os.path.exists(readme) and os.path.isdir(os.path.join(root, "docs")))
    clear_banner(pane)
    pane._rename(readme, "a/b")
    check("a path-shaped name is refused before any request", "isn't a name" in banner(pane), banner(pane))
    clear_banner(pane)
    pane._rename(readme, "README.md")
    settle(0.3)
    check("the same name again is nothing to do", not banner(pane) and os.path.exists(readme), banner(pane))

    # -- Copy and Paste through the service ------------------------------------------------
    tree._menu_path, tree._menu_is_dir = readme, False
    tree._on_copy(None, None)
    check("a copy claims the clipboard", wait_for(lambda: clipboard.is_local()))
    have = mimes(clipboard)
    check("with Collins' own payload", fileclipboard.COLLINS_COPIED_FILES in have, have)
    check("and, local, the file: payloads", fileclipboard.GNOME_COPIED_FILES in have and fileclipboard.URI_LIST in have, have)
    check("has_files says so", fileclipboard.has_files(clipboard))
    got = read_clipboard(clipboard)
    check("read back as the path, a copy", got == ([readme], False), got)
    docs = os.path.join(root, "docs")
    pane._paste_into(docs)
    pasted = os.path.join(docs, "README.md")
    check("the paste lands a copy through the service", wait_for(lambda: os.path.exists(pasted)), os.listdir(docs))
    check("and leaves the original", os.path.exists(readme))
    check("the tree shows it", wait_for(lambda: (1, "README.md") in rows(tree)), rows(tree))
    pane._paste_into(docs)
    check("a second paste never overwrites", wait_for(lambda: os.path.exists(os.path.join(docs, "README (copy).md"))), os.listdir(docs))
    with open(pasted) as fh:
        check("the first copy is untouched", fh.read() == "# hi\n")
    check("no banner", not banner(pane), banner(pane))

    # -- a Cut's paste moves, the open buffer follows, the cut is spent ------------------------
    tree._menu_path, tree._menu_is_dir = under, False
    tree._on_cut(None, None)
    got = read_clipboard(clipboard)
    check("a cut reads back as a cut", got == ([under], True), got)
    check("nothing moved yet", os.path.exists(under))
    pane._paste_into(docs)
    moved_to = os.path.join(docs, "main2.py")
    check("the paste moves the file", wait_for(lambda: os.path.exists(moved_to) and not os.path.exists(under)), (os.listdir(docs), os.listdir(source)))
    check("the open buffer follows the move", wait_for(lambda: moved_to in pane._open and under not in pane._open), list(pane._open))
    followed = pane._open[moved_to]
    check("with its edits", followed.buffer.get_text(followed.buffer.get_start_iter(), followed.buffer.get_end_iter(), True) == "print('edited')\n")
    check("the cut is spent", wait_for(lambda: not fileclipboard.has_files(clipboard)), mimes(clipboard))
    check("the tree reveals it under docs", wait_for(lambda: selected_name(tree) == "main2.py" and (1, "main2.py") in rows(tree)), rows(tree))

    # -- an empty clipboard --------------------------------------------------------------
    clipboard.set(GLib.Variant("s", "just text"))
    check("plain text is not files", not fileclipboard.has_files(clipboard), mimes(clipboard))
    pane._paste_into(docs)
    check("a paste of nothing is told", wait_for(lambda: "nothing on the clipboard" in banner(pane)), banner(pane))
    clear_banner(pane)

    # -- a client that is not local (D35) -------------------------------------------------
    remote = remotefiles.ClipboardScope("svc-remote", False)
    fileclipboard.set_files(clipboard, [readme], cut=True, scope=remote)
    have = mimes(clipboard)
    check("not local: Collins' own payload only, no file: URI", fileclipboard.COLLINS_COPIED_FILES in have and fileclipboard.GNOME_COPIED_FILES not in have and fileclipboard.URI_LIST not in have, have)
    check("the plain text is still there", "text/plain" in have or "text/plain;charset=utf-8" in have, have)
    check("has_files for its own service", fileclipboard.has_files(clipboard, scope=remote))
    # The formats alone cannot say whose service the URIs name: another
    # service's client sees a Paste it can try, and the read drops them.
    check("and (formats only) for another service's", fileclipboard.has_files(clipboard, scope=remotefiles.ClipboardScope("svc-other", False)))
    got = read_clipboard(clipboard, scope=remote)
    check("read back for its own service, still a cut", got == ([readme], True), got)
    got = read_clipboard(clipboard, scope=remotefiles.ClipboardScope("svc-other", False))
    check("and as nothing for another's", got == ([], True), got)
    # A file manager's copy (file: URIs) on a client that is not local.
    fileclipboard.set_files(clipboard, [readme], scope=remotefiles.ClipboardScope(None, True))
    have = mimes(clipboard)
    check("a file: clipboard with no service is the file manager's shape", fileclipboard.GNOME_COPIED_FILES in have and fileclipboard.COLLINS_COPIED_FILES not in have, have)
    check("which a client that is not local cannot paste", not fileclipboard.has_files(clipboard, scope=remote))
    check("and a local one can", fileclipboard.has_files(clipboard, scope=remotefiles.ClipboardScope("svc1", True)))
    got = read_clipboard(clipboard, scope=remote)
    check("read back as nothing when not local", got == ([], False), got)

    # -- a new folder is fs.mkdir ------------------------------------------------------------
    made: list = []
    new_dir = os.path.join(root, "made")
    remotefiles.off_main(lambda: remotefiles.make_dir(new_dir, root), lambda kind, value: made.append(kind))
    check("fs.mkdir makes the folder", wait_for(lambda: made == ["ok"]) and os.path.isdir(new_dir), (made, os.listdir(root)))
    remotefiles.off_main(lambda: remotefiles.make_dir(new_dir, root), lambda kind, value: made.append((kind, value)))
    check("and refuses it twice", wait_for(lambda: len(made) == 2) and made[1][0] == "refused" and made[1][1].details.get("reason") == "exists", made)

    pane.shutdown()
    window.destroy()
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


def main() -> int:
    root = tempfile.mkdtemp(prefix="collins-filetree-ops-")
    try:
        link = e2e_service.harness_link()
        remotefiles.install(link)
        return run(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(_SCRATCH, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
