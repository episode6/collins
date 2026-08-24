#!/usr/bin/env python3
"""End-to-end check for what a click on a sidebar project header does.

A project header is two targets (sidebar.GroupHeaderRow): its icon folds
the group, and the rest of the row starts a session there — onto the
new-chat screen, exactly as its + button does. This check stages one
project with a session under it, activates its header row and expects a
new-chat tab filed against the project, then fires the icon's own click
gesture and expects the group folded with no further tab opened. The
Favorites header, which has nowhere to start a session, must keep folding
on activation.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \
        python3 scripts/check_project_row_click.py

Nothing is spawned: the new-chat screen starts no CLI until its first
prompt is sent, and this check never sends one. A `claude` stub still has
to be on PATH — a provider whose CLI is missing has no sessions discovered
at all, so without it the project never gets a header.
"""

import json
import os
import re
import shutil
import sys
import tempfile
import uuid

E2E = tempfile.mkdtemp(prefix="collins-rowclick-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

# Isolation first: every one of these is read at import time somewhere below.
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"

PROJECT = f"{E2E}/dev/alpha"
SHIM = f"{E2E}/bin/claude"

for path in (f"{E2E}/projects", f"{E2E}/chats", f"{E2E}/bin", PROJECT):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
with open(SHIM, "w", encoding="utf-8") as fh:
    fh.write("#!/bin/sh\nexit 0\n")  # never run; only found
os.chmod(SHIM, 0o755)
os.environ["PATH"] = f"{E2E}/bin:{os.environ['PATH']}"

# One session on disk, so the project has a header row (and a session row
# under it to fold away).
enc = re.sub(r"[^A-Za-z0-9]", "-", PROJECT)
SID = str(uuid.uuid4())
os.makedirs(f"{E2E}/projects/{enc}", exist_ok=True)
with open(f"{E2E}/projects/{enc}/{SID}.jsonl", "w", encoding="utf-8") as fh:
    fh.write(json.dumps({"type": "summary", "cwd": PROJECT, "summary": "an old thread"}) + "\n")
    fh.write(
        json.dumps(
            {
                "type": "user",
                "cwd": PROJECT,
                "sessionId": "x",
                "timestamp": "2026-08-01T00:00:00Z",
                "message": {"role": "user", "content": "hello"},
            }
        )
        + "\n"
    )

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import GLib, Gtk  # noqa: E402

from collins import i18n, trust  # noqa: E402
from collins.app import App  # noqa: E402
from collins.sidebar import FAV_GROUP, GroupHeaderRow  # noqa: E402
from collins.state import AppState  # noqa: E402

PASSED = 0
FAILED = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  ok  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}  {detail}")


def project_header(win) -> GroupHeaderRow | None:
    for row in win.sidebar._header_rows.values():
        if row.cwd == PROJECT:
            return row
    return None


def icon_click_gesture(row: GroupHeaderRow) -> Gtk.GestureClick | None:
    """The fold gesture, which lives on the icon and nowhere else."""
    controllers = row._icon.observe_controllers()
    for i in range(controllers.get_n_items()):
        controller = controllers.get_item(i)
        if isinstance(controller, Gtk.GestureClick) and controller.get_button() == 1:
            return controller
    return None


def new_chat_tabs(win) -> list:
    tabs = []
    for i in range(win.tab_view.get_n_pages()):
        tab = win.tab_view.get_nth_page(i).get_child()
        if getattr(tab, "is_new_chat", False):
            tabs.append(tab)
    return tabs


seed = AppState()
i18n.init(seed.get_setting("language"))
# A group the sidebar has never shown starts collapsed; this one starts open,
# so the icon's first click has something to fold. The session is starred so
# Favorites has a header to check too.
seed.set_group_expanded("proj:alpha", True)
seed.toggle_favorite(SID)
trust.trust_dir(PROJECT)
app = App()

tries = 0
state: dict = {}


def stage() -> bool:
    """Wait for the window and the project's header row."""
    global tries
    tries += 1
    win = app.get_active_window()
    header = project_header(win) if win is not None else None
    if header is None:
        if tries > 120:  # the scan lands off a worker thread; CI is slow
            print("timed out waiting for the window and the project header", file=sys.stderr)
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE
    state["win"] = win
    sidebar = win.sidebar
    check("the project is the group seeded open", header.group_key == ("proj", "alpha"), header.group_key)
    check("…and starts expanded", header.group_key not in sidebar._collapsed)
    check("the header row offers a session", header.starts_session)
    check(
        "…and says so",
        header.get_tooltip_text() == f"New session in {PROJECT}",
        header.get_tooltip_text(),
    )
    check("the icon carries the fold gesture", icon_click_gesture(header) is not None)
    check(
        "…and names the fold",
        header._icon.get_tooltip_text() == "Collapse",
        header._icon.get_tooltip_text(),
    )
    state["before"] = win.tab_view.get_n_pages()

    # A click on the row (off the icon) reaches the list as a row activation.
    sidebar.list.emit("row-activated", header)
    GLib.timeout_add(800, after_the_row_click)
    return GLib.SOURCE_REMOVE


def after_the_row_click() -> bool:
    win = state["win"]
    header = project_header(win)
    tabs = new_chat_tabs(win)
    check(
        "activating the row opens one new tab",
        win.tab_view.get_n_pages() == state["before"] + 1,
        win.tab_view.get_n_pages(),
    )
    check("…onto the new-chat screen", len(tabs) == 1, len(tabs))
    check(
        "…filed against the project",
        bool(tabs) and tabs[0].start_cwd == PROJECT,
        [t.start_cwd for t in tabs],
    )
    check("…with nothing spawned behind it", bool(tabs) and tabs[0]._child_pid is None)
    check("…and the group left as it was", header.group_key not in win.sidebar._collapsed)
    state["after_row"] = win.tab_view.get_n_pages()

    # A click on the icon: the gesture fires and claims the press, so the row
    # activation the list would have made never comes.
    gesture = icon_click_gesture(header)
    gesture.emit("pressed", 1, 8.0, 8.0)
    GLib.timeout_add(800, after_the_icon_click)
    return GLib.SOURCE_REMOVE


def after_the_icon_click() -> bool:
    win = state["win"]
    header = project_header(win)
    check("the icon click folds the group", header.group_key in win.sidebar._collapsed)
    check("…and the row knows", header._collapsed)
    check(
        "…naming the unfold now",
        header._icon.get_tooltip_text() == "Expand",
        header._icon.get_tooltip_text(),
    )
    check(
        "…without opening a tab",
        win.tab_view.get_n_pages() == state["after_row"],
        win.tab_view.get_n_pages(),
    )
    icon_click_gesture(header).emit("pressed", 1, 8.0, 8.0)
    GLib.timeout_add(300, after_the_unfold)
    return GLib.SOURCE_REMOVE


def after_the_unfold() -> bool:
    win = state["win"]
    header = project_header(win)
    check("a second icon click unfolds it", header.group_key not in win.sidebar._collapsed)

    fav = win.sidebar._header_rows.get(FAV_GROUP)
    check("Favorites has a header", fav is not None)
    if fav is not None:
        check("…that offers no session", not fav.starts_session)
        check("…and no fold gesture on its icon", icon_click_gesture(fav) is None)
        before = win.tab_view.get_n_pages()
        was_folded = FAV_GROUP in win.sidebar._collapsed
        win.sidebar.list.emit("row-activated", fav)
        check("activating it toggles the fold", (FAV_GROUP in win.sidebar._collapsed) != was_folded)
        check("…and opens nothing", win.tab_view.get_n_pages() == before)
    app.quit()
    return GLib.SOURCE_REMOVE


GLib.timeout_add(250, stage)
app.run([])
shutil.rmtree(E2E, ignore_errors=True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED or not PASSED else 0)
