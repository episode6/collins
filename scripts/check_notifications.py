#!/usr/bin/env python3
"""Headless check of the header bell and the notification history sheet — run
on a dev machine, never by pytest.

Drives a real App against staged data and pushes notifications through its
center, asserting what the window does with them: the bell's badge and
tooltip, the sheet's rows and their sections, the Ctrl+Shift+B action and
the bell ↔ sheet binding, a row click landing on the right tab (a real
session's, a placeholder's — in this window or another) with the keyboard
staying in the sheet, Mark all read, Clear and a row's Remove, the
Preferences link's open-on-group, and a second window wearing the same
number and letting go of the center when it closes.

Stages its own throwaway scratch tree and app id (two sessions in a project
named alpha-widgets, named in state.json), so it runs anywhere the e2e suite
does:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_notifications.py

The steps run as a chain of short timeouts rather than one function: the
sheet rebuilds its rows on an idle after every change (so the placeholder →
real-row handoff costs one rebuild, not two), and each step reads what the
one before it caused. None of it is reachable from pytest: the widgets need
a display, and tests/conftest.py blocks the GTK stack to match CI. What can
be decided without a toolkit — the tooltip's words, a row's age, the
unread/earlier split — is tests/test_notifycenter.py's.

Run it behind the headless wrapper, or a window opens on the user's screen.
"""

import json
import os
import re
import shutil
import sys
import tempfile

E2E = tempfile.mkdtemp(prefix="collins-notify-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

# Isolation first: every one of these is read at import time somewhere below.
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"

SESSION_A = "11111111-1111-4111-8111-111111111111"
SESSION_B = "22222222-2222-4222-8222-222222222222"
PLACEHOLDER = "placeholder-77"
PLACEHOLDER_2 = "placeholder-78"  # a second window's
PLACEHOLDER_3 = "placeholder-79"  # a sidebar row with no tab behind it
PROJECT_DIR = f"{E2E}/dev/alpha-widgets"
STATE_FILE = f"{E2E}/config/collins/state.json"

_encoded = re.sub(r"[^A-Za-z0-9]", "-", PROJECT_DIR)
for path in (
    f"{E2E}/projects/{_encoded}",
    f"{E2E}/chats",
    f"{E2E}/config/collins",
    f"{E2E}/bin",
    PROJECT_DIR,
):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
# A `claude` on PATH, never run: the store only lists sessions for providers
# whose CLI it can find, and CI has none.
with open(f"{E2E}/bin/claude", "w", encoding="utf-8") as fh:
    fh.write("#!/bin/sh\nexit 0\n")
os.chmod(f"{E2E}/bin/claude", 0o755)
os.environ["PATH"] = f"{E2E}/bin:{os.environ['PATH']}"
# The project ships an icon, so a row can be seen wearing it.
with open(f"{PROJECT_DIR}/project-icon.svg", "w", encoding="utf-8") as fh:
    fh.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
             '<circle cx="8" cy="8" r="7" fill="#e66100"/></svg>')
for session, prompt, stamp in (
    (SESSION_A, "Fix the flaky spinner animation", "2026-07-25T14:12:03Z"),
    (SESSION_B, "Profile the router", "2026-07-25T15:12:03Z"),
):
    with open(f"{E2E}/projects/{_encoded}/{session}.jsonl", "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "type": "user", "timestamp": stamp, "cwd": PROJECT_DIR,
            "message": {"role": "user", "content": prompt},
        }) + "\n")
with open(STATE_FILE, "w", encoding="utf-8") as fh:
    json.dump({
        "names": {SESSION_A: "Fix spinner animation", SESSION_B: "Router profiling"},
        "settings": {"welcome_seen": True},
    }, fh)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk  # noqa: E402

from collins import i18n, notifycenter  # noqa: E402
from collins.app import App  # noqa: E402
from collins.window import MainWindow  # noqa: E402

failures = []
passed = [0]


def check(label, ok, detail=""):
    print(f"{'  ok' if ok else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")
    if ok:
        passed[0] += 1
    else:
        failures.append(label)


def row_titles(win) -> list[str]:
    return [r.notification.title for r in win.notify_sheet.rows()]


def section_titles(win) -> list[str]:
    """The heading rows' text, in order, with the count pill's number after
    a heading that has one."""
    found = []
    index = 0
    lb = win.notify_sheet.list
    while (row := lb.get_row_at_index(index)) is not None:
        if row.get_css_classes() and "notification-section" in row.get_css_classes():
            box = row.get_child()
            label = box.get_first_child()
            text = label.get_label()
            pill = label.get_next_sibling()
            if pill is not None:
                text += f" {pill.get_label()}"
            found.append(text)
        index += 1
    return found


def sheet_page(win) -> str:
    return win.notify_sheet._stack.get_visible_child_name()


def steps(app: App):
    """Each step runs after the one before had an idle to settle in."""
    center = app.notification_center
    store = app.store
    win = app.get_active_window()
    bell = win.notify_bell
    sheet = win.notify_sheet
    split = win.notify_split
    page_a = win.tab_view.append(Gtk.Label(label="tab A"))
    page_b = win.tab_view.append(Gtk.Label(label="tab B"))
    win._pages[SESSION_A] = page_a
    win._pages[SESSION_B] = page_b
    win.tab_view.set_selected_page(page_a)
    shared = {}

    # -- quiet ------------------------------------------------------------------
    def quiet():
        check("the bell starts with no badge", bell.badge_text() == "", repr(bell.badge_text()))
        check("its tooltip names the sheet", bell.button.get_tooltip_text() == "Notifications")
        check("the sheet shows its empty state", sheet_page(win) == "empty")
        check("the sheet is closed and the bell is off",
              not split.get_show_sidebar() and not bell.button.get_active())
        check("the split holds the content stack",
              split.get_content() is win.content_stack and split.get_collapsed())
        check("the sheet is 380 wide both ways",
              split.get_min_sidebar_width() == 380 and split.get_max_sidebar_width() == 380)
        # pack_end order is right to left, so the first packed lands nearest
        # the window controls; read the geometry rather than the sibling chain.
        _ok, bell_bounds = bell.compute_bounds(win._content_header)
        _ok, cup_bounds = win.caffeine_btn.compute_bounds(win._content_header)
        check("the bell sits right of the caffeine button",
              bell_bounds.get_x() > cup_bounds.get_x(),
              f"bell x={bell_bounds.get_x()}, cup x={cup_bounds.get_x()}")
        check("the footer names the default sound",
              sheet._sound_label.get_label() == "Sound: Default", sheet._sound_label.get_label())
    yield quiet

    # -- a message row ----------------------------------------------------------
    def post_message():
        shared["msg"] = center.post(center.make(
            notifycenter.KIND_MESSAGE, SESSION_B, "Router profiling", "alpha-widgets",
            "Profiling done: p95 latency is down 31%."))
        check("a message badges the bell at once", bell.badge_text() == "1", bell.badge_text())
        check("and the tooltip counts it", bell.button.get_tooltip_text() == "1 unread notification")
    yield post_message

    def message_row():
        check("the sheet shows the row under Unread",
              sheet_page(win) == "list" and section_titles(win) == ["Unread 1"], str(section_titles(win)))
        rows = sheet.rows()
        check("the row is the message, unread",
              len(rows) == 1 and rows[0].notification is shared["msg"]
              and "unread" in rows[0].get_css_classes())
        icon = rows[0].get_child().get_first_child()
        check("the row wears the project's icon", icon.get_paintable() is not None)
    yield message_row

    # -- a synthetic row --------------------------------------------------------
    def go_green():
        store.set_unread(SESSION_A, True)
        check("a finished run counts beside the message", bell.badge_text() == "2")
    yield go_green

    def green_row():
        check("the newest row is first: the finished run above the message",
              row_titles(win) == ["Fix spinner animation", "Router profiling"], str(row_titles(win)))
        check("the Unread pill counts two", section_titles(win) == ["Unread 2"])
    yield green_row

    # -- the action and the binding ----------------------------------------------
    def open_by_action():
        win.activate_action("win.toggle-notifications")
        check("Ctrl+Shift+B's action opens the sheet", split.get_show_sidebar())
        check("and lights the bell", bell.button.get_active())
    yield open_by_action

    def focus_in_sheet():
        focus = win.get_focus()
        check("the sheet took the keyboard", focus is not None and focus.is_ancestor(sheet),
              type(focus).__name__ if focus else "None")
    yield focus_in_sheet

    # -- a row click ------------------------------------------------------------
    def click_message():
        row = next(r for r in sheet.rows() if r.notification is shared["msg"])
        row.grab_focus()  # a real click puts the keyboard on the row it lands on
        sheet.list.emit("row-activated", row)
        check("clicking the message row selects its session's tab",
              win.tab_view.get_selected_page() is page_b)
        check("the row is read", shared["msg"].read)
        check("the badge drops to the finished run", bell.badge_text() == "1")
        check("the sheet stays open", split.get_show_sidebar())
    yield click_message

    def after_click():
        check("the message moved under Earlier",
              section_titles(win) == ["Unread 1", "Earlier"], str(section_titles(win)))
        rows = sheet.rows()
        check("the read row lost its guide line",
              "unread" not in rows[1].get_css_classes() and rows[1].notification is shared["msg"])
        focus = win.get_focus()
        check("the keyboard is still in the sheet, on the row that was clicked",
              focus is not None and focus.is_ancestor(sheet) and focus is rows[1],
              type(focus).__name__ if focus else "None")
    yield after_click

    def close_by_split():
        split.set_show_sidebar(False)  # what Escape and the scrim do
        check("closing the sheet turns the bell off", not bell.button.get_active())
    yield close_by_split

    # -- a placeholder's row ----------------------------------------------------
    def placeholder():
        win.sidebar.add_placeholder(PLACEHOLDER, PROJECT_DIR, "agent-claude-symbolic")
        page = win.tab_view.append(Gtk.Label(label="new thread"))
        win._placeholder_pages[page] = PLACEHOLDER
        shared["placeholder_page"] = page
        win.tab_view.set_selected_page(page_a)
        win._on_session_finished(PLACEHOLDER)
        check("a placeholder's finish is a row under its key",
              center.get(notifycenter.green_id(PLACEHOLDER)) is not None and bell.badge_text() == "2")
    yield placeholder

    def click_placeholder():
        row = next(r for r in sheet.rows() if r.notification.session_id == PLACEHOLDER)
        sheet.list.emit("row-activated", row)
        check("clicking a placeholder's row selects its tab",
              win.tab_view.get_selected_page() is shared["placeholder_page"])
        check("which clears its flag and drops the row",
              center.get(notifycenter.green_id(PLACEHOLDER)) is None and bell.badge_text() == "1")
    yield click_placeholder

    # -- mark all read, clear, remove -------------------------------------------
    def mark_all():
        sheet.mark_all_button.emit("clicked")
        check("Mark all read zeroes the badge", bell.badge_text() == "")
        check("the finished row is read but still there",
              center.is_green(SESSION_A) and center.get(notifycenter.green_id(SESSION_A)).read)
    yield mark_all

    def after_mark_all():
        check("everything is under Earlier", section_titles(win) == ["Earlier"], str(section_titles(win)))
        check("Mark all read is greyed with nothing unread", not sheet.mark_all_button.get_sensitive())
        store.set_unread(SESSION_A, False)
        check("the finished row leaves with its flag", not center.is_green(SESSION_A))
    yield after_mark_all

    def clear():
        center.post(center.make(notifycenter.KIND_BELL, SESSION_A, "Fix spinner animation",
                                "alpha-widgets", "Rang the bell"))
        center.post(center.make(notifycenter.KIND_BELL, SESSION_A, "Fix spinner animation",
                                "alpha-widgets", "Rang the bell"))
        check("two bells from one session are one row", len(center.rows()) == 2 and bell.badge_text() == "1")
    yield clear

    def bell_row():
        row = sheet.rows()[0]
        body = row.get_child().get_first_child().get_next_sibling().get_last_child()
        check("the coalesced bell row counts itself",
              body.get_label() == "Rang the bell ×2", body.get_label())
        sheet.clear_button.emit("clicked")
        check("Clear empties the list", center.rows() == [] and bell.badge_text() == "")
    yield bell_row

    def after_clear():
        check("the sheet is back to its empty state", sheet_page(win) == "empty")
        shared["gone"] = center.post(center.make(
            notifycenter.KIND_MESSAGE, SESSION_B, "Router profiling", "alpha-widgets", "Removable"))
        sheet.activate_action("notify.remove", GLib.Variant("s", shared["gone"].id))
        check("a row's Remove drops it", center.get(shared["gone"].id) is None)
        shared["kept"] = center.post(center.make(
            notifycenter.KIND_MESSAGE, SESSION_B, "Router profiling", "alpha-widgets", "Readable"))
        sheet.activate_action("notify.mark-read", GLib.Variant("s", shared["kept"].id))
        check("a row's Mark read reads it", shared["kept"].read and bell.badge_text() == "")
    yield after_clear

    # -- preferences on a group -------------------------------------------------
    def preferences():
        win._show_preferences("notifications")
        dialog = win.get_visible_dialog()
        check("the footer's link opens Preferences", dialog is not None)
        if dialog is not None:
            check("on the whole page while the Notifications group isn't built",
                  dialog._search_entry.get_text() == "" and not dialog.show_group("notifications"))
            check("and can open on a group that exists",
                  dialog.show_group("terminal") and dialog._search_entry.get_text() == "Terminal")
            dialog.force_close()
    yield preferences

    # -- a second window --------------------------------------------------------
    def second_window():
        win2 = app._new_window()
        shared["win2"] = win2
        center.post(center.make(notifycenter.KIND_MESSAGE, SESSION_A, "Fix spinner animation",
                                "alpha-widgets", "Seen twice"))
        check("every window's bell shows the same number",
              bell.badge_text() == "1" and win2.notify_bell.badge_text() == "1",
              f"{bell.badge_text()!r} / {win2.notify_bell.badge_text()!r}")
        check("the second window is a MainWindow with its own sheet",
              isinstance(win2, MainWindow) and win2.notify_sheet is not sheet)
    yield second_window

    def second_window_rows():
        win2 = shared["win2"]
        check("its sheet shows the same rows", row_titles(win2) == row_titles(win), str(row_titles(win2)))
        # A placeholder of the second window's, finished: its synthetic row
        # is in every sheet, this window's included.
        win2.sidebar.add_placeholder(PLACEHOLDER_2, PROJECT_DIR, "agent-claude-symbolic")
        page = win2.tab_view.append(Gtk.Label(label="new thread, elsewhere"))
        win2._placeholder_pages[page] = PLACEHOLDER_2
        shared["placeholder_page_2"] = page
        win2.tab_view.append(Gtk.Label(label="tab C"))
        win2.tab_view.set_selected_page(win2.tab_view.get_nth_page(1))
        win2._on_session_finished(PLACEHOLDER_2)
        check("another window's placeholder finishing is a row here",
              center.get(notifycenter.green_id(PLACEHOLDER_2)) is not None and bell.badge_text() == "2")
    yield second_window_rows

    def click_other_windows_placeholder():
        win2 = shared["win2"]
        row = next(r for r in sheet.rows() if r.notification.session_id == PLACEHOLDER_2)
        sheet.list.emit("row-activated", row)
        check("clicking it from this window's sheet selects the tab in the window that has it",
              win2.tab_view.get_selected_page() is shared["placeholder_page_2"])
        check("which clears that window's flag and drops the row",
              center.get(notifycenter.green_id(PLACEHOLDER_2)) is None and bell.badge_text() == "1")
    yield click_other_windows_placeholder

    def dead_placeholder_click():
        # A synthetic row whose tab no window has any more: the click goes
        # nowhere, and the row is left standing rather than read by hand.
        win2 = shared["win2"]
        win2.sidebar.add_placeholder(PLACEHOLDER_3, PROJECT_DIR, "agent-claude-symbolic")
        win2._on_session_finished(PLACEHOLDER_3)
        check("a placeholder with no page still counts", bell.badge_text() == "2")
    yield dead_placeholder_click

    def dead_placeholder_click_lands_nowhere():
        row = next(r for r in sheet.rows() if r.notification.session_id == PLACEHOLDER_3)
        sheet.list.emit("row-activated", row)
        check("a synthetic row whose click goes nowhere stays unread",
              not row.notification.read and bell.badge_text() == "2")
        center.set_green(PLACEHOLDER_3, False)
        shared["win2"].destroy()
    yield dead_placeholder_click_lands_nowhere

    def after_destroy():
        center.mark_all_read()
        check("a closed window let go of the center", bell.badge_text() == "")
    yield after_destroy


def main() -> int:
    i18n.init("")
    app = App()
    tries = 0
    exit_code = [1]

    def run_chain(chain) -> None:
        try:
            step = next(chain)
        except StopIteration:
            if not failures:
                exit_code[0] = 0
            app.quit()
            return
        try:
            step()
        except Exception as exc:  # noqa: BLE001
            failures.append(f"exception in {step.__name__}: {exc!r}")
            import traceback

            traceback.print_exc()
            app.quit()
            return
        GLib.timeout_add(120, lambda: (run_chain(chain), GLib.SOURCE_REMOVE)[1])

    def prepare() -> bool:
        nonlocal tries
        tries += 1
        win = app.get_active_window()
        ready = (
            win is not None
            and app.store.get_session(SESSION_A) is not None
            and app.store.get_session(SESSION_B) is not None
        )
        if not ready:
            if tries > 40:
                print("timed out waiting for the window/sessions", file=sys.stderr)
                app.quit()
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE
        run_chain(steps(app))
        return GLib.SOURCE_REMOVE

    GLib.timeout_add(250, prepare)
    try:
        app.run([])
    finally:
        shutil.rmtree(E2E, ignore_errors=True)
    print(f"{passed[0]} passed, {len(failures)} failed")
    return exit_code[0]


if __name__ == "__main__":
    sys.exit(main())
