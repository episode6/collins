#!/usr/bin/env python3
# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Wiring check for the editor's files over the API (collins/editor.py over
collins/remotefiles.py and the service's collins/service/files.py; the
split-service spec §3.23, PR-2.3) — run on a dev machine.

Exercises the GTK side that tests/test_files.py (the service's reads,
writes and watches) and tests/test_remotefiles.py (the client's calls)
can't reach: a real EditorPane in a real window against a service of this
check's own. A file opens through `fs.read` (the text in the buffer, the
mtime kept, a watch installed on the service); an edit dirties the tab; a
write from outside (what the agent does all day) reaches the pane as a
`file-changed` and raises the Reload banner over the dirty buffer, which
keeps the edit until the banner is clicked; the reload brings the disk's
text and the new mtime; a save after it writes through `fs.write` and the
file carries the edit; a clean buffer reloads silently, cursor kept; a
save over a file that moved underneath is refused `stale` and asks before
overwriting (the "changed on disk" dialog, stubbed), the file untouched
until Overwrite; a deleted file is told and marked dirty; a change that
waited for a reload is judged even when the reload fails; a closed tab's
watch is gone. The pane never opens a file itself.

This is a script, not a pytest test, on purpose: tests/conftest.py blocks
the GTK-stack namespaces for the whole suite so local runs reproduce CI.
Testing widgets for real means running this by hand, behind a display
nobody is looking at:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_editor_save.py

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
_SCRATCH = tempfile.mkdtemp(prefix="collins-es-")
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

from collins import dialogs, remotefiles  # noqa: E402
from collins.api import protocol  # noqa: E402
from collins.api.protocol import RequestRefused  # noqa: E402
from collins.editor import EditorPane  # noqa: E402

PASSED = 0
FAILED = 0
# How long each asynchronous step gets: a read or write's round trip, or
# the service's 300 ms debounce plus a stat and the event's landing.
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
    """Spin the main loop for *seconds*: long enough for the service's
    debounce and a `file-changed` that should (or should not) arrive."""
    wait_for(lambda: False, seconds)


def buffer_text(opened) -> str:
    start, end = opened.buffer.get_bounds()
    return opened.buffer.get_text(start, end, True)


def cursor_of(opened) -> tuple[int, int]:
    it = opened.buffer.get_iter_at_mark(opened.buffer.get_insert())
    return it.get_line(), it.get_line_offset()


def write_outside(path: str, text: str) -> None:
    """What a shell or the agent does: a plain write, mtime moved on from
    the editor's by a second so a coarse filesystem clock never hides it."""
    before = os.stat(path).st_mtime_ns if os.path.exists(path) else 0
    with open(path, "w") as fh:
        fh.write(text)
    after = os.stat(path).st_mtime_ns
    if after <= before:
        os.utime(path, ns=(before + 1_000_000_000, before + 1_000_000_000))


def run(root: str) -> int:
    first = os.path.join(root, "first.py")
    second = os.path.join(root, "second.txt")
    with open(first, "w") as fh:
        fh.write("print('hello')\n")
    with open(second, "w") as fh:
        fh.write("plain text\n")

    pane = EditorPane(root)
    pane.apply_settings({})
    window = Gtk.Window(default_width=900, default_height=700)
    window.set_child(pane)
    window.present()
    if not wait_for(lambda: pane.get_width() > 0):
        print("FAIL  the pane never allocated")
        return 1
    watcher = remotefiles.watcher()

    # -- open: the text arrives from the service, with its mtime and a watch --
    pane.open_file(first)
    opened = pane._open.get(first)
    check("open_file makes a buffer at once", opened is not None)
    if opened is None:
        return 1
    check("the buffer is loading until the read lands", opened.loading)
    if not wait_for(lambda: not opened.loading):
        print("FAIL  the read never landed")
        return 1
    check("the read filled the buffer", buffer_text(opened) == "print('hello')\n", buffer_text(opened))
    check("the buffer is clean after the load", not opened.buffer.get_modified())
    check("the file's mtime is kept for the save", opened.mtime == os.stat(first).st_mtime_ns // 1000, opened.mtime)
    check("the encoding is flagged", opened.encoding == "utf-8", opened.encoding)
    check("the cursor opens at the top", cursor_of(opened) == (0, 0), cursor_of(opened))
    check("a watch is installed under a handle", opened.watch_handle and watcher.watching(opened.watch_handle))
    check("the language came from the name", opened.buffer.get_language() is not None)
    page = pane._pages[first]
    check("the tab is titled after the file", page.get_title() == "first.py", page.get_title())

    # -- edit: the tab is dirty -----------------------------------------------
    opened.buffer.insert(opened.buffer.get_end_iter(), "x = 1\n")
    check("an edit dirties the buffer", opened.buffer.get_modified())
    check("the tab shows the dot", page.get_title() == "• first.py", page.get_title())

    # -- an external write over a dirty buffer: the Reload banner -------------
    write_outside(first, "print('changed')\n")
    if not wait_for(lambda: pane._banner.get_revealed()):
        print("FAIL  the external write raised no banner")
        return 1
    check(
        "the banner says the file changed on disk",
        pane._banner.get_title() == "first.py changed on disk.",
        pane._banner.get_title(),
    )
    check("the banner offers Reload", pane._banner.get_button_label() == "Reload", pane._banner.get_button_label())
    check("the dirty buffer kept the edit", buffer_text(opened) == "print('hello')\nx = 1\n", buffer_text(opened))
    check("the buffer is still dirty", opened.buffer.get_modified())

    # -- Reload: the disk's text, the new mtime, a clean buffer ------------------
    pane._banner.emit("button-clicked")
    if not wait_for(lambda: buffer_text(opened) == "print('changed')\n"):
        print("FAIL  the reload never brought the disk's text:", repr(buffer_text(opened)))
        return 1
    check("the banner came down", not pane._banner.get_revealed())
    check("the reloaded buffer is clean", not opened.buffer.get_modified())
    check("the reload took the new mtime", opened.mtime == os.stat(first).st_mtime_ns // 1000)
    check("the tab's dot is gone", page.get_title() == "first.py", page.get_title())

    # -- save after reload: fs.write lands the edit on disk --------------------
    opened.buffer.insert(opened.buffer.get_end_iter(), "y = 2\n")
    pane._select_page(page)
    pane.save_current()
    check("a save is in flight", opened.saving)
    if not wait_for(lambda: not opened.saving):
        print("FAIL  the save never landed")
        return 1
    with open(first) as fh:
        on_disk = fh.read()
    check("the file carries the edit", on_disk == "print('changed')\ny = 2\n", on_disk)
    check("the saved buffer is clean", not opened.buffer.get_modified())
    check("the save took the new mtime", opened.mtime == os.stat(first).st_mtime_ns // 1000)
    settle()
    check("the save's own change raised no banner", not pane._banner.get_revealed(), pane._banner.get_title())
    check("and reloaded nothing", buffer_text(opened) == "print('changed')\ny = 2\n" and not opened.loading)

    # -- a clean buffer follows the disk silently, cursor kept --------------------
    _found, it = opened.buffer.get_iter_at_line(1)
    it.set_line_offset(2)
    opened.buffer.place_cursor(it)
    write_outside(first, "print('again')\ny = 2\nz = 3\n")
    if not wait_for(lambda: buffer_text(opened) == "print('again')\ny = 2\nz = 3\n"):
        print("FAIL  the clean buffer never followed the disk:", repr(buffer_text(opened)))
        return 1
    check("a clean buffer reloads without a banner", not pane._banner.get_revealed(), pane._banner.get_title())
    check("the silent reload kept the cursor", cursor_of(opened) == (1, 2), cursor_of(opened))
    check("and the buffer is clean", not opened.buffer.get_modified())

    # -- a save over a file that moved: refused stale, asked before overwriting --
    asked: list[dict] = []

    def fake_confirm(parent, heading, body, confirm_label, on_confirm, on_dismiss=None, **kw):
        asked.append({"heading": heading, "label": confirm_label, "confirm": on_confirm, "dismiss": on_dismiss})

    real_confirm = dialogs.confirm_dialog
    dialogs.confirm_dialog = fake_confirm
    try:
        opened.buffer.insert(opened.buffer.get_end_iter(), "mine = 1\n")
        write_outside(first, "theirs = 1\n")
        if not wait_for(lambda: pane._banner.get_revealed()):
            print("FAIL  the second external write raised no banner")
            return 1
        pane.save_current()
        if not wait_for(lambda: asked):
            print("FAIL  the stale save never asked")
            return 1
        check("a stale save asks before overwriting", asked[0]["heading"] == "first.py changed on disk", asked[0])
        check("the ask offers Overwrite", asked[0]["label"] == "Overwrite", asked[0]["label"])
        with open(first) as fh:
            on_disk = fh.read()
        check("nothing was written before the answer", on_disk == "theirs = 1\n", on_disk)
        check("the buffer kept its edit", buffer_text(opened).endswith("mine = 1\n"))
        asked[0]["confirm"]()
        if not wait_for(lambda: not opened.buffer.get_modified()):
            print("FAIL  the overwrite never landed")
            return 1
        with open(first) as fh:
            on_disk = fh.read()
        check("Overwrite writes the buffer over the file", on_disk == buffer_text(opened), on_disk)
        check("and takes the new mtime", opened.mtime == os.stat(first).st_mtime_ns // 1000)
    finally:
        dialogs.confirm_dialog = real_confirm

    # -- a deleted file is told, and the buffer marked dirty ------------------------
    pane.open_file(second)
    other = pane._open[second]
    if not wait_for(lambda: not other.loading):
        print("FAIL  the second read never landed")
        return 1
    pane._banner.set_revealed(False)
    os.unlink(second)
    if not wait_for(lambda: pane._banner.get_revealed()):
        print("FAIL  the deletion raised no banner")
        return 1
    check("a deleted file is told", pane._banner.get_title() == "second.txt was deleted.", pane._banner.get_title())
    check("and its buffer is dirty, so nothing saves over nothing silently", other.buffer.get_modified())
    check("its mtime is forgotten", other.mtime is None)

    # -- a change that waited for a reload is judged even when the reload fails ------
    # Two external changes racing one reload: the second is queued while the
    # read is in flight, and the read then fails (the file went away between
    # the monitor and the read). The queued `gone` is still judged, so the
    # buffer is told and marked dirty (the review of PR 609).
    third = os.path.join(root, "third.txt")
    with open(third, "w") as fh:
        fh.write("third\n")
    pane.open_file(third)
    racing = pane._open[third]
    if not wait_for(lambda: not racing.loading):
        print("FAIL  the third read never landed")
        return 1
    pane._banner.set_revealed(False)
    racing.loading = True  # a reload in flight...
    racing.reloading = True
    pane._check_external(
        racing, {"handle": racing.watch_handle, "path": third, "mtime": None, "size": None, "gone": True}
    )
    check("a change during a reload waits for it", racing.pending_change is not None and not racing.buffer.get_modified())
    pane._on_loaded(
        racing, racing.load_id, "refused", RequestRefused(protocol.ERROR_GONE, "{name} is not there", {"name": "third.txt"})
    )
    check("the failed reload is told", pane._banner.get_revealed())
    check(
        "and the waiting change is judged after it",
        racing.pending_change is None and racing.mtime is None and racing.buffer.get_modified(),
        (racing.pending_change, racing.mtime, racing.buffer.get_modified()),
    )

    # -- closing a tab drops its watch -----------------------------------------------
    handle = other.watch_handle
    pane._close_confirmed.add(pane._pages[second])
    pane._tab_view.close_page(pane._pages[second])
    if not wait_for(lambda: second not in pane._open):
        print("FAIL  the tab never closed")
        return 1
    check("a closed tab's watch is gone", handle and not watcher.watching(handle))
    check("the first file's watch stays", watcher.watching(opened.watch_handle))

    # -- a file the service refuses: no tab, a banner --------------------------------
    binary = os.path.join(root, "blob.bin")
    with open(binary, "wb") as fh:
        fh.write(b"\x00\x01\x02binary")
    pane._banner.set_revealed(False)
    pane.open_file(binary)
    if not wait_for(lambda: binary not in pane._open and pane._banner.get_revealed()):
        print("FAIL  the binary file's refusal never landed")
        return 1
    check(
        "a binary file is refused with a banner and no tab",
        "binary" in pane._banner.get_title() and binary not in pane._pages,
        pane._banner.get_title(),
    )

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


def main() -> int:
    root = tempfile.mkdtemp(prefix="collins-editor-save-")
    try:
        link = e2e_service.harness_link()
        remotefiles.install(link)
        return run(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(_SCRATCH, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
