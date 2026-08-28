#!/usr/bin/env python3
"""End-to-end check for what a click on a sidebar project header does.

A project header is two targets (sidebar.GroupHeaderRow): the fold zone —
everything before the title, full row height — folds the group, and the
rest of the row starts a session there, onto the new-chat screen, exactly
as its + button does. This check stages one project with a session under
it, activates its header row and expects a new-chat tab filed against the
project, then presses inside and outside the fold zone and expects only
the in-zone press to fold, with no further tab opened. Left/Right on the
row fold and unfold from the keyboard. The Favorites header, which has
nowhere to start a session, must keep folding on activation.

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
# The first-launch welcome (collins.welcome) is answered already: it would
# otherwise sit over the window under test.
os.makedirs(f"{E2E}/config/collins", exist_ok=True)
with open(f"{E2E}/config/collins/state.json", "w", encoding="utf-8") as fh:
    fh.write('{"settings": {"welcome_seen": true}}')
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

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

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


def _controller(widget: Gtk.Widget, kind: type, button: int | None = None):
    controllers = widget.observe_controllers()
    for i in range(controllers.get_n_items()):
        controller = controllers.get_item(i)
        if isinstance(controller, kind) and (button is None or controller.get_button() == button):
            return controller
    return None


def fold_gesture(row: GroupHeaderRow) -> Gtk.GestureClick | None:
    """The fold gesture on the row (the menu's is button 3, so distinct)."""
    return _controller(row, Gtk.GestureClick, button=1)


def zone_edge(row: GroupHeaderRow) -> float:
    """Row-x where the title starts — the fold zone's right edge."""
    ok, bounds = row._label.compute_bounds(row)
    assert ok
    return bounds.origin.x


def press(row: GroupHeaderRow, x: float) -> None:
    fold_gesture(row).emit("pressed", 1, x, 4.0)


def press_key(row: GroupHeaderRow, keyval: int) -> None:
    """Fire the row's key controller as a key on the focused row would."""
    _controller(row, Gtk.EventControllerKey).emit("key-pressed", keyval, 0, Gdk.ModifierType(0))


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
    edge = zone_edge(header)
    check("the title leaves room for the fold zone", edge > 4, edge)
    check(
        "…and says so past the zone",
        header._tooltip_text_at(edge + 4) == f"New session in {PROJECT}",
        header._tooltip_text_at(edge + 4),
    )
    check("the row carries the fold gesture", fold_gesture(header) is not None)
    check("the fold zone runs to the title", header._in_fold_zone(edge - 1))
    check("…and no further", not header._in_fold_zone(edge + 1))
    check("…including left of the icon", header._in_fold_zone(1.0))
    check(
        "…naming the fold there",
        header._tooltip_text_at(edge - 4) == "Collapse",
        header._tooltip_text_at(edge - 4),
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

    # A press on the title's side of the zone edge is not the fold's: the
    # handler leaves it unclaimed for the list, which would activate the row
    # (that path is the emit above; here only "no fold" can be asserted).
    press(header, zone_edge(header) + 4)
    check("a press past the zone never folds", header.group_key not in win.sidebar._collapsed)

    # A press in the fold zone: the gesture fires and claims the press, so
    # the row activation the list would have made never comes.
    press(header, zone_edge(header) - 4)
    GLib.timeout_add(800, after_the_zone_click)
    return GLib.SOURCE_REMOVE


def after_the_zone_click() -> bool:
    win = state["win"]
    header = project_header(win)
    check("the fold-zone press folds the group", header.group_key in win.sidebar._collapsed)
    check("…and the row knows", header._collapsed)
    check(
        "…naming the unfold now",
        header._tooltip_text_at(4.0) == "Expand",
        header._tooltip_text_at(4.0),
    )
    check(
        "…without opening a tab",
        win.tab_view.get_n_pages() == state["after_row"],
        win.tab_view.get_n_pages(),
    )
    press(header, zone_edge(header) - 4)
    GLib.timeout_add(300, after_the_unfold)
    return GLib.SOURCE_REMOVE


def after_the_unfold() -> bool:
    win = state["win"]
    header = project_header(win)
    check("a second fold-zone press unfolds it", header.group_key not in win.sidebar._collapsed)

    # The keyboard's fold: Left folds, Right unfolds, on the focused row.
    press_key(header, Gdk.KEY_Left)
    check("Left on the row folds the group", header.group_key in win.sidebar._collapsed)
    press_key(header, Gdk.KEY_Left)
    check("…and a second Left is a no-op", header.group_key in win.sidebar._collapsed)
    press_key(header, Gdk.KEY_Right)
    check("Right unfolds it", header.group_key not in win.sidebar._collapsed)

    fav = win.sidebar._header_rows.get(FAV_GROUP)
    check("Favorites has a header", fav is not None)
    if fav is not None:
        check("…that offers no session", not fav.starts_session)
        check("…and no zone-gated fold gesture", fold_gesture(fav) is None)
        check(
            "…with the hover caret on the whole row, its fold target",
            _controller(fav, Gtk.EventControllerMotion) is not None,
        )
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
