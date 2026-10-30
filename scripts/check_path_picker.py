#!/usr/bin/env python3
"""End-to-end check for the path picker (split-service spec §3.11, §3.24;
PR-3.2, D60): a client that is not on the service's machine picks a folder
for a new session.

One `App()` against a service of its own, started with `HOME` at the
scratch tree (so the picker's home shortcut is the scratch home). The
link is made what a client over ssh is, at both ends
(`e2e_service.drop_local`: the service confines it, the client offers no
local extra). Then a new session's folder chooser opens the path picker,
not the desktop's `Gtk.FileDialog` (`choosers.choose_folder` branches on
`apilink.is_local()`), and the check walks it the way a person would:

- the shortcuts are the service's home and the store's project root;
- the home lists its subfolders, a link to a folder among them marked,
  the dotted ones only with *Show hidden*;
- a click moves into `work`, a folder in the scratch tree outside every
  root the service knows (no session, no project there);
- *New folder* makes `made` there and enters it;
- a typed path is filtered after the debounce and Tab completes it;
- *Open* hands the folder on, the picker closes, and the session spawns
  there (the pty's row says where it started, D39).

And it asserts the requests were the right ones: over the same link
`fs.list` of `work` is refused `outside` while `fs.dirs` of it answers,
so a picker over the editor's confined requests would fail here.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_path_picker.py
"""

import json
import os
import re
import shutil
import sys
import tempfile
import uuid

E2E = tempfile.mkdtemp(prefix="collins-picker-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

# Isolation first: every one of these is read at import time somewhere below.
os.environ["COLLINS_DEBUG_API"] = "1"  # the e2e probe (debug.*): served only with this set
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"
os.environ["XDG_CACHE_HOME"] = f"{E2E}/cache"
os.environ["XDG_DATA_HOME"] = f"{E2E}/data"
os.environ.setdefault(
    "MESA_SHADER_CACHE_DIR", os.path.join(os.path.expanduser("~"), ".cache", "mesa_shader_cache")
)

SERVICE_HOME = f"{E2E}/home"
PROJECT = f"{E2E}/dev/alpha"  # the store's one project: a shortcut, and a root
WORK = f"{SERVICE_HOME}/work"  # outside every root the service knows
MADE = f"{WORK}/made"  # what New folder makes
LINK = f"{SERVICE_HOME}/linked"  # a link to a folder
HIDDEN = f"{SERVICE_HOME}/.secret"

for path in (f"{E2E}/projects", f"{E2E}/chats", f"{E2E}/bin", f"{E2E}/cache", PROJECT, WORK, HIDDEN):
    os.makedirs(path, exist_ok=True)
os.symlink(PROJECT, LINK)
with open(f"{SERVICE_HOME}/a-file.txt", "w", encoding="utf-8") as fh:
    fh.write("not a folder\n")
# The scratch tree is trusted as the CLI records it (a folder's trust covers
# everything under it), so the launch asks no question.
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    json.dump({"projects": {E2E: {"hasTrustDialogAccepted": True}}}, fh)
os.makedirs(f"{E2E}/config/collins", exist_ok=True)
with open(f"{E2E}/config/collins/state.json", "w", encoding="utf-8") as fh:
    fh.write('{"settings": {"welcome_seen": true, "gh_welcome_dismissed": true}}')
with open(f"{E2E}/bin/claude", "w", encoding="utf-8") as fh:
    fh.write("#!/bin/sh\nexec sleep 60\n")
os.chmod(f"{E2E}/bin/claude", 0o755)
os.environ["PATH"] = f"{E2E}/bin:{os.environ['PATH']}"


def _transcript(cwd: str) -> str:
    enc = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    sid = str(uuid.uuid4())
    os.makedirs(f"{E2E}/projects/{enc}", exist_ok=True)
    with open(f"{E2E}/projects/{enc}/{sid}.jsonl", "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "summary", "cwd": cwd, "summary": "an old thread"}) + "\n")
        fh.write(json.dumps({
            "type": "user", "cwd": cwd, "sessionId": sid, "timestamp": "2026-08-01T00:00:00Z",
            "message": {"role": "user", "content": "hello"},
        }) + "\n")
    return sid


_transcript(PROJECT)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
import e2e_service  # noqa: E402
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

# The desktop's folder chooser is never shown: a call to it is recorded.
NATIVE: list[str] = []
Gtk.FileDialog.select_folder = lambda self, *_a: NATIVE.append(self.get_title() or "")

from collins import apilink  # noqa: E402
from collins.api import protocol  # noqa: E402
from collins.api.protocol import RequestRefused  # noqa: E402
from collins.app import App  # noqa: E402
from collins.pathpicker import LINK_ICON, PathPicker  # noqa: E402
from collins.pathpickermodel import Folder  # noqa: E402
from collins.service import files as service_files  # noqa: E402

PASSED = 0
FAILED = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  ok    {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label}  {detail!r}")


e2e_service.start_service({**os.environ, "HOME": SERVICE_HOME})
app = App()
state: dict = {}
tries = 0


def wait(predicate, seconds: float = 5.0) -> bool:
    return e2e_service.wait_until(predicate, timeout_s=seconds)


def row_names(picker: PathPicker) -> list[str]:
    names = []
    child = picker.rows.get_first_child()
    while child is not None:
        if getattr(child, "folder", None):
            names.append(child.folder)
        child = child.get_next_sibling()
    return names


def row_named(picker: PathPicker, name: str):
    child = picker.rows.get_first_child()
    while child is not None:
        if getattr(child, "folder", None) == name:
            return child
        child = child.get_next_sibling()
    return None


def row_icons(row) -> list[str]:
    """The icon names a listing row's box shows, left to right."""
    names = []
    child = row.get_child().get_first_child() if row is not None else None
    while child is not None:
        if isinstance(child, Gtk.Image):
            names.append(child.get_icon_name())
        child = child.get_next_sibling()
    return names


def place_paths(picker: PathPicker) -> list[str]:
    paths = []
    child = picker.places.get_first_child()
    while child is not None:
        paths.append(getattr(child, "path", None))
        child = child.get_next_sibling()
    return paths


def stage() -> bool:
    """Wait for the window and the project's row, then drop the proof."""
    global tries
    tries += 1
    win = app.get_active_window()
    if win is None or win.store.model.get_n_items() < 1:
        if tries > 80:
            check("the window and its project came up", False)
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE
    state["win"] = win
    link = apilink.current()
    e2e_service.drop_local(link)
    check("the client is not local, to itself", app.local is False and apilink.is_local() is False)
    # The service agrees: the editor's confined listing refuses `work` now.
    try:
        apilink.call({"t": "fs.list", "path": WORK, "hidden": False, "root": WORK})
        refused = None
    except RequestRefused as refusal:
        refused = refusal
    check("…and to the service (fs.list outside every root is refused)",
          refused is not None and refused.error == protocol.ERROR_REFUSED
          and refused.msgid == service_files.OUTSIDE_MSGID, refused)
    win._choose_new_session_folder()
    GLib.timeout_add(300, picker_opened)
    return GLib.SOURCE_REMOVE


def picker_opened() -> bool:
    win = state["win"]
    picker = win.get_visible_dialog()
    check("the new session's chooser is the path picker", isinstance(picker, PathPicker), picker)
    check("…and never the desktop's folder chooser", NATIVE == [], NATIVE)
    if not isinstance(picker, PathPicker):
        app.quit()
        return GLib.SOURCE_REMOVE
    state["picker"] = picker
    places = place_paths(picker)
    check("the first shortcut is the service's home", places[:1] == [SERVICE_HOME], places)
    check("…and the store's project is one", PROJECT in places, places)
    listed = wait(lambda: picker._listing.path == SERVICE_HOME and bool(row_names(picker)))
    check("it opens on the service's home, listed", listed, (picker._listing.path, picker._listing.error))
    text = picker.entry.get_text()
    check("…the entry saying where it is", text == SERVICE_HOME + "/", text)
    names = row_names(picker)
    check("the home's subfolders, a link to one among them, no file and no dotted one",
          names == ["linked", "work"], names)
    rows = picker._listing.rows
    check("…the link marked as one", Folder("linked", link=True) in rows, rows)
    # The mark is the icon Collins ships, and the theme finds it (no theme
    # has the name: a missing one would draw the broken-image glyph).
    marks = row_icons(row_named(picker, "linked"))
    theme = Gtk.IconTheme.get_for_display(picker.get_display())
    check("…with Collins' own link icon, which the theme resolves",
          marks[-1:] == [LINK_ICON] and theme.has_icon(LINK_ICON), marks)
    check("…and a plain folder has no mark", row_icons(row_named(picker, "work")) == ["folder-symbolic"],
          row_icons(row_named(picker, "work")))
    picker.hidden_btn.set_active(True)
    shown = wait(lambda: ".secret" in row_names(picker))
    check("Show hidden lists the dotted folder", shown, row_names(picker))
    picker.hidden_btn.set_active(False)
    wait(lambda: ".secret" not in row_names(picker))
    # Into `work`, by a click.
    picker.rows.emit("row-activated", row_named(picker, "work"))
    entered = wait(lambda: picker._listing.path == WORK and picker._listing.error == "")
    where = (picker._listing.path, picker._listing.error)
    check("a click moves into work, outside every root", entered, where)
    check("…which has no subfolder yet", row_names(picker) == [], row_names(picker))
    # New folder.
    picker.new_btn.popup()
    GLib.timeout_add(200, make_folder)
    return GLib.SOURCE_REMOVE


def make_folder() -> bool:
    picker = state["picker"]
    picker.new_entry.set_text("made")
    picker._make_folder()
    made = wait(lambda: picker._listing.path == MADE)
    check("New folder makes it on the service and enters it", made and os.path.isdir(MADE),
          (picker._listing.path, picker.new_note.get_text()))
    check("…the entry saying so", picker.entry.get_text() == MADE + "/", picker.entry.get_text())
    # The requests were the picker's own: over the same link, the editor's
    # confined listing of `work` is refused while the picker's answers.
    try:
        apilink.call({"t": "fs.list", "path": WORK, "hidden": False, "root": WORK})
        refused = None
    except RequestRefused as refusal:
        refused = refusal
    outside = refused is not None and refused.msgid == service_files.OUTSIDE_MSGID
    check("fs.list of work is refused outside", outside, refused)
    try:
        dirs = apilink.call({"t": "fs.dirs", "path": WORK, "hidden": False})
    except RequestRefused as refusal:
        dirs = {"refused": refusal.msgid}
    check("…while fs.dirs of it answers, with the folder made",
          dirs.get("path") == WORK and dirs.get("entries") == [{"name": "made", "symlink": False}], dirs)
    # A typed folder is asked for only once the debounce has run out.
    asked = picker._listing.asked
    picker.entry.set_text(SERVICE_HOME + "/")
    check("typing arms the debounce and asks nothing yet",
          picker._debounce is not None and picker._listing.asked == asked, picker._listing.asked - asked)
    relisted = wait(lambda: picker._listing.path == SERVICE_HOME and picker._listing.asked == asked + 1)
    count = picker._listing.asked - asked
    check("…then lists the folder typed, once", relisted, (picker._listing.path, count))
    # A typed name: filtered after the debounce, completed by Tab.
    picker.go(WORK)
    wait(lambda: picker._listing.path == WORK)
    picker.entry.set_text(WORK + "/ma")
    filtered = wait(lambda: picker._listing.folder == WORK and row_names(picker) == ["made"], seconds=3.0)
    check("a typed name filters the rows after the debounce", filtered, row_names(picker))
    picker._on_key(None, Gdk.KEY_Tab, 0, Gdk.ModifierType(0))
    check("Tab completes it", picker.entry.get_text() == MADE + "/", picker.entry.get_text())
    GLib.timeout_add(400, open_it)
    return GLib.SOURCE_REMOVE


def open_it() -> bool:
    picker, win = state["picker"], state["win"]
    picker._open()
    closed = wait(lambda: win.get_visible_dialog() is None)
    check("Open closes the picker", closed, win.get_visible_dialog())
    GLib.timeout_add(300, spawned)
    return GLib.SOURCE_REMOVE


def made_tab(win):
    for index in range(win.tab_view.get_n_pages()):
        tab = win.tab_view.get_nth_page(index).get_child()
        if getattr(tab, "_cwd", None) == MADE:
            return tab
    return None


def spawned() -> bool:
    """The new session's tab opens onto its new-chat screen in the folder
    picked; Send is what launches the agent (`_on_new_chat_send`)."""
    win = state["win"]
    opened = wait(lambda: made_tab(win) is not None, seconds=5.0)
    tab = made_tab(win)
    pages = range(win.tab_view.get_n_pages())
    cwds = [getattr(win.tab_view.get_nth_page(i).get_child(), "_cwd", None) for i in pages]
    check("a new session's screen opened in the folder picked", opened, cwds)
    if tab is None:
        app.quit()
        return GLib.SOURCE_REMOVE
    tab._new_chat.emit("send-requested", "hello from the picked folder", False, False, "sonnet", "low")
    found = wait(lambda: tab.session.pty is not None, seconds=8.0)
    check("Send spawns the session", found)
    if tab.session.pty is not None:
        info = apilink.call({"t": "pty.info", "pty": tab.session.pty})
        check("…and the pty's row says it started there", info.get("cwd") == MADE, info.get("cwd"))
    check("the desktop's folder chooser never opened", NATIVE == [], NATIVE)
    app.quit()
    return GLib.SOURCE_REMOVE


def deadline() -> bool:
    print("timed out", file=sys.stderr)
    print(f"\n{PASSED} passed, {FAILED} failed (and the check timed out)")
    os._exit(1)


GLib.timeout_add_seconds(60, deadline)
GLib.timeout_add(250, stage)
app.run([])
shutil.rmtree(E2E, ignore_errors=True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED or not PASSED else 0)
