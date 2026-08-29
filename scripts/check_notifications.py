#!/usr/bin/env python3
"""Headless check of the header bell, the notification history sheet, the
in-app cards and the delivery routing — run on a dev machine, never by
pytest.

Drives a real App against staged data and pushes notifications through its
center, asserting what the window does with them: the bell's badge and
tooltip, the sheet's rows and their sections, the Ctrl+Shift+B action and
the bell ↔ sheet binding, a row click landing on the right tab (a real
session's, a placeholder's — in this window or another) with the keyboard
staying in the sheet, Mark all read, Clear and a row's Remove, the
Preferences link's open-on-group, and a second window wearing the same
number and letting go of the center when it closes.

Then the delivery table, through the window's own entry points
(notify_session and _on_bell) with a real TerminalTab: a message from a tab
that isn't selected is a card in the overlay (under the header bar, the
sound asked for, the row unread and the sidebar flagged), clicking the card
selects the tab and reads the row, a message to the selected tab is a read
row and no card, an unfocused window gets the desktop notification, a bell
from another tab is a card and a coalesced row while the selected tab's bell
is a beep and no row, the three switches (in-app, bells, announce finished
runs) do what their subtitles say, a placeholder tab's card still finds its
page and the handoff re-files its rows under the session id (re-sending the
desktop notification with the tab behind it), a bell read from the sheet
takes its desktop notification down, a fourth card evicts the oldest, the ×
leaves the row unread, the
notify_user tool's reply names where the message went, and the Preferences
group's sound picker writes the setting the sheet's footer reads. Focus is
the harness's to declare — is_active is read per window instance — so
nothing here depends on what the headless compositor decides to focus.

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

from collins import i18n, notifycenter, notifysound  # noqa: E402
from collins.app import App  # noqa: E402
from collins.terminal import TerminalTab  # noqa: E402
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
            check("on the Notifications group, by its title in the search box",
                  dialog._search_entry.get_text() == "Notifications")
            check("and can open on another group that exists",
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

    # -- the delivery table ------------------------------------------------------
    cards = win.notify_cards
    played = []
    withdrawn = []

    def wait():
        pass

    def deliveries_setup():
        # The sound, for real, once: whatever this machine has (GStreamer and
        # a theme, or neither), play() answers and never raises.
        first = notifysound.play("default", force=True)
        check("play() answers for the default sound",
              first in (notifysound.PLAYED, notifysound.BEEPED, notifysound.MUTED), first)
        second = notifysound.play("default")
        check("a second play right after is debounced or busy",
              second in (notifysound.DEBOUNCED, notifysound.BUSY), second)
        check("silence is silence", notifysound.play("none", force=True) == notifysound.SILENT)
        check("GStreamer's absence is a fact, not an error", isinstance(notifysound.available(), bool))
        # From here the sound is recorded, not played, and every withdraw
        # of a desktop notification is noted on its way to the bus.
        notifysound.play = lambda value, **_kw: played.append(value) or notifysound.PLAYED
        real_withdraw = app.withdraw_notification
        app.withdraw_notification = lambda key: withdrawn.append(key) or real_withdraw(key)
        # Focus is declared: this window is the active one.
        win.is_active = lambda: True
        center.clear()
        center.mark_all_read()
        store.set_unread(SESSION_A, False)
        store.set_unread(SESSION_B, False)
        # A real terminal tab for session B: notify_session and _on_bell
        # take a TerminalTab, and the tab's session id is what a row is
        # filed under. The shell runs `sleep`, never the CLI.
        tab_b = TerminalTab(cwd=PROJECT_DIR, session_id=SESSION_B, command_override="sleep 300")
        page = win.tab_view.append(tab_b)
        page.set_title("Router profiling")
        win._pages[SESSION_B] = page
        shared["tab_b"], shared["page_b"] = tab_b, page
        win.tab_view.set_selected_page(page_a)
        check("no cards stand before anything is delivered", cards.cards() == [])
    yield deliveries_setup

    def message_elsewhere():
        result = win.notify_session(shared["tab_b"], "Profiling done: p95 latency is down 31%.")
        check("a message from another tab is a card, the sound, a row, a flag and the flash",
              result == {notifycenter.DELIVER_CARD, notifycenter.DELIVER_SOUND, notifycenter.DELIVER_ROW,
                         notifycenter.DELIVER_FLAG, notifycenter.DELIVER_FLASH}, str(result))
        check("the sound was asked for, with the setting", played == ["default"], str(played))
        rows = [r for r in center.rows() if r.kind != notifycenter.KIND_FINISHED]
        check("the row is unread, under the session, with its name and project",
              len(rows) == 1 and not rows[0].read and rows[0].session_id == SESSION_B
              and rows[0].title == "Router profiling" and rows[0].project == "alpha-widgets",
              str([(r.kind, r.session_id, r.body, r.read) for r in rows]))
        check("the sidebar row is flagged", store.get_item(SESSION_B).unread)
        # The flag's own synthetic row rides along (set_green tracks every
        # flag edge, a message's included): the badge reads two until the
        # tab is visited, which takes both down.
        check("and the flag's synthetic row stands beside the message", center.is_green(SESSION_B))
        check("the tool reply says in Collins", notifycenter.tool_reply(result) == notifycenter.REPLY_IN_APP)
        shared["msg_row"] = rows[0]
    yield message_elsewhere

    def card_up():
        up = cards.cards()
        check("one card stands", len(up) == 1, str(len(up)))
        if not up:
            return
        card = up[0]
        check("it is revealed", card.get_reveal_child())
        check("it holds the row", card.notification is shared["msg_row"])
        check("it holds the page it came from", card.page is shared["page_b"])
        header_h = win._content_header.get_height()
        check("the stack sits 12px under the header bar",
              header_h > 0 and cards.get_margin_top() == header_h + 12,
              f"header {header_h}, margin {cards.get_margin_top()}")
        check("and 14px from the right edge", cards.get_margin_end() == 14)
        card.activate()
        check("clicking the card selects its tab", win.tab_view.get_selected_page() is shared["page_b"])
        check("which reads the row, clears the flag and drops the flag's row",
              shared["msg_row"].read and not store.get_item(SESSION_B).unread
              and not center.is_green(SESSION_B))
        check("and the card is on its way out", not card.get_reveal_child())
    yield card_up
    yield wait
    yield wait

    def card_gone():
        check("the card is gone once the slide ends", cards.cards() == [], str(len(cards.cards())))
        result = win.notify_session(shared["tab_b"], "Still here?")
        check("a message to the selected tab is a read row and the flash, no card",
              result == {notifycenter.DELIVER_ROW_READ, notifycenter.DELIVER_FLASH}, str(result))
        check("the row is read on arrival", center.rows()[0].read and center.rows()[0].body == "Still here?")
        check("no card came up", cards.cards() == [])
        check("the tool reply says the user is looking",
              notifycenter.tool_reply(result) == notifycenter.REPLY_SELECTED)
        check("the sound was not asked for", played == ["default"])
        win.tab_view.set_selected_page(page_a)
    yield card_gone

    def message_unfocused():
        win.is_active = lambda: False
        result = win.notify_session(shared["tab_b"], "Away message")
        check("with no window active the message is a desktop notification, a row and a flag",
              result == {notifycenter.DELIVER_DESKTOP, notifycenter.DELIVER_ROW, notifycenter.DELIVER_FLAG},
              str(result))
        check("the tool reply says the desktop",
              notifycenter.tool_reply(result) == notifycenter.REPLY_DESKTOP)
        check("the sidebar row is flagged again", store.get_item(SESSION_B).unread)
        win.is_active = lambda: True
        # The app's tool dispatch, end to end: the reply is one of the three.
        ok, reply = app._mcp_notify_user((win, shared["tab_b"]), {"message": "Through the tool"})
        check("the notify_user tool replies 'in Collins' for a card",
              ok and reply == "The user was notified in Collins.", reply)
    yield message_unfocused

    def visit_reads():
        unread_before = center.unread_count()
        check("three rows wait (the desktop one, the tool's, and the flag's)",
              unread_before == 3, str(unread_before))
        win.tab_view.set_selected_page(shared["page_b"])
        check("selecting the tab reads every row the session posted", center.unread_count() == 0)
        check("and takes its card down", all(not c.get_reveal_child() for c in cards.cards()))
        win.tab_view.set_selected_page(page_a)
    yield visit_reads
    yield wait
    yield wait

    def bell_elsewhere():
        played.clear()
        before = len(center.rows())
        win._on_bell(shared["tab_b"])
        win._on_bell(shared["tab_b"])
        rows = center.rows()
        check("two bells from another tab are one unread bell row",
              len(rows) == before + 1 and rows[0].kind == notifycenter.KIND_BELL
              and rows[0].count == 2 and not rows[0].read and rows[0].body == "Rang the bell",
              f"{len(rows) - before} new, count {rows[0].count}")
        check("the sound was asked for each time (notifysound debounces)", played == ["default", "default"])
        check("a bell flags nothing", not store.get_item(SESSION_B).unread)
        shared["bell_row"] = rows[0]
    yield bell_elsewhere

    def bell_card():
        up = cards.cards()
        check("the bell's card stands, once, wearing the count",
              len(up) == 1 and up[0].notification is shared["bell_row"], str(len(up)))
        win.tab_view.set_selected_page(shared["page_b"])
        before = len(center.rows())
        win._on_bell(shared["tab_b"])
        check("a bell from the selected tab posts no row", len(center.rows()) == before)
        win.tab_view.set_selected_page(page_a)
        win.state.set_setting("bell_notifications", False)
        win._on_bell(shared["tab_b"])
        check("nor does any bell with 'Bells from other sessions' off",
              len(center.rows()) == before and shared["bell_row"].count == 2)
        win.state.set_setting("bell_notifications", True)
    yield bell_card

    def bell_read_from_sheet():
        # An unfocused bell is a desktop notification with no flag beside
        # it; reading its row from the sheet is what takes the banner down.
        win.is_active = lambda: False
        win._on_bell(shared["tab_b"])
        win.is_active = lambda: True
        row = center.rows()[0]  # a fresh row: the visit above read the coalesced one
        check("an unfocused bell is a desktop notification and an unread bell row",
              row.kind == notifycenter.KIND_BELL and not row.read and row.session_id == SESSION_B
              and SESSION_B in win._desktop_keys, f"{row.kind} read={row.read}")
        before = len(withdrawn)
        center.mark_read(row.id)
        check("marking the bell row read withdraws the session's desktop notification",
              withdrawn[before:] == [SESSION_B], str(withdrawn[before:]))
    yield bell_read_from_sheet

    def inapp_off():
        cards.dismiss_all()
        win.state.set_setting("inapp_notifications", False)
        result = win.notify_session(shared["tab_b"], "Cards off")
        check("with in-app notifications off the card becomes a desktop notification",
              notifycenter.DELIVER_DESKTOP in result and notifycenter.DELIVER_CARD not in result
              and notifycenter.DELIVER_SOUND not in result, str(result))
        check("the row and the flag still land",
              notifycenter.DELIVER_ROW in result and store.get_item(SESSION_B).unread)
        win.state.set_setting("inapp_notifications", True)
        win.tab_view.set_selected_page(shared["page_b"])  # reads it all
        win.tab_view.set_selected_page(page_a)
    yield inapp_off
    yield wait
    yield wait

    def announce_off():
        cards.dismiss_all()
        check("announce finished runs is off by default", not win.state.get_setting("announce_finished_runs"))
        win._on_session_finished(SESSION_B)
        check("a finish flags the row and puts the synthetic row up",
              store.get_item(SESSION_B).unread and center.is_green(SESSION_B))
    yield announce_off

    def announce_off_no_card():
        check("but shows no card while the setting is off", cards.cards() == [], str(len(cards.cards())))
        store.set_unread(SESSION_B, False)
        win.state.set_setting("announce_finished_runs", True)
        played.clear()
        win._on_session_finished(SESSION_B)
        check("with the setting on a finish plays the sound", played == ["default"], str(played))
    yield announce_off_no_card

    def announce_card():
        up = cards.cards()
        check("and shows a finished-run card for the synthetic row",
              len(up) == 1 and up[0].notification.kind == notifycenter.KIND_FINISHED
              and up[0].notification.id == notifycenter.green_id(SESSION_B), str(len(up)))
        if up:
            up[0].activate()
            check("clicking it selects the tab, which clears the flag and drops the row",
                  win.tab_view.get_selected_page() is shared["page_b"]
                  and not store.get_item(SESSION_B).unread and not center.is_green(SESSION_B))
        win.state.set_setting("announce_finished_runs", False)
        win.tab_view.set_selected_page(page_a)
    yield announce_card
    yield wait
    yield wait

    def placeholder_card():
        # A tab with no session id yet: its message is filed under the
        # placeholder id, and its card holds the page.
        tab_p = TerminalTab(cwd=PROJECT_DIR, command_override="sleep 300")
        page = win.tab_view.append(tab_p)
        page.set_title("New Thread")
        win._placeholder_pages[page] = "placeholder-90"
        win.sidebar.add_placeholder("placeholder-90", PROJECT_DIR, "agent-claude-symbolic")
        shared["page_p"] = page
        win.tab_view.set_selected_page(page_a)
        result = win.notify_session(tab_p, "From a tab with no id yet")
        row = center.rows()[0]
        check("a placeholder tab's message is a card and a row under the placeholder id",
              notifycenter.DELIVER_CARD in result and row.session_id == "placeholder-90"
              and row.project == "alpha-widgets", f"{row.session_id!r} {row.project!r}")
        check("and flags the placeholder row", win.sidebar.placeholder_unread("placeholder-90"))
        shared["p_row"] = row
    yield placeholder_card

    def placeholder_card_click():
        up = cards.cards()
        check("its card holds the page", len(up) == 1 and up[0].page is shared["page_p"], str(len(up)))
        if up:
            up[0].activate()
            check("clicking it selects the placeholder's tab and reads the row",
                  win.tab_view.get_selected_page() is shared["page_p"] and shared["p_row"].read
                  and not win.sidebar.placeholder_unread("placeholder-90"))
        win.tab_view.set_selected_page(page_a)
    yield placeholder_card_click
    yield wait
    yield wait

    def placeholder_handoff():
        # The tab spoke again while no window was active, then the store
        # discovered its session: the rows move to the session id, and the
        # banner sent under the placeholder is replaced by one with a tab
        # behind it.
        page = shared["page_p"]
        win.is_active = lambda: False
        win.notify_session(page.get_child(), "Away, from a tab with no id yet")
        win.is_active = lambda: True
        row = center.rows()[0]
        check("an unfocused placeholder message is a desktop notification under the placeholder id",
              "placeholder-90" in win._desktop_keys and row.session_id == "placeholder-90" and not row.read)
        win._set_placeholder_unread("placeholder-90", False)  # the handoff takes the green row down first
        before = len(withdrawn)
        win._rekey_notifications("placeholder-90", "session-resolved-90", page)
        check("the handoff re-files the rows under the session id",
              row.session_id == "session-resolved-90"
              and not any(r.session_id == "placeholder-90" for r in center.rows()))
        check("withdraws the placeholder's desktop notification",
              "placeholder-90" in withdrawn[before:] and "placeholder-90" not in win._desktop_keys,
              str(withdrawn[before:]))
        check("and sends it again under the session id while the row is unread",
              "session-resolved-90" in win._desktop_keys)
        before = len(withdrawn)
        center.mark_session_read("session-resolved-90")
        check("which the session's read then withdraws",
              withdrawn[before:] == ["session-resolved-90"], str(withdrawn[before:]))
    yield placeholder_handoff

    def four_cards():
        for n in range(4):
            win.notify_session(shared["tab_b"], f"Message {n}")
    yield four_cards

    def three_stand():
        up = cards.cards()
        check("a fourth card pushes the oldest out: three stand, newest on top",
              [c.notification.body for c in up if c.get_reveal_child()]
              == ["Message 3", "Message 2", "Message 1"],
              str([c.notification.body for c in up]))
        if up:
            top = up[0]
            top.close_button.emit("clicked")
            check("the × takes the card down", not top.get_reveal_child())
            check("and leaves the row unread", not top.notification.read and center.unread_count() >= 4)
        cards.dismiss_all()
        center.clear()
        store.set_unread(SESSION_B, False)
    yield three_stand

    # -- the Notifications group in Preferences -------------------------------------
    def preferences_group():
        win._show_preferences("notifications")
        dialog = win.get_visible_dialog()
        check("the sheet's link opens Preferences on the Notifications group",
              dialog is not None and dialog._search_entry.get_text() == "Notifications")
        if dialog is None:
            return
        check("the group is visible and holds the four rows",
              dialog._inapp_row.get_visible() and dialog._sound_row.get_visible()
              and dialog._bell_row.get_visible() and dialog._announce_row.get_visible())
        check("the sound row says what Default means",
              dialog._sound_row.get_subtitle() == (
                  "Default: the desktop's message sound" if notifysound.available()
                  else "Sound needs GStreamer (gir1.2-gstreamer-1.0); the desktop's beep is used instead"),
              dialog._sound_row.get_subtitle())
        check("and is greyed exactly when GStreamer is missing",
              dialog._sound_row.get_sensitive() == notifysound.available())
        if notifysound.available():
            dialog._sound_row.set_selected(1)
            check("picking None writes the setting", win.state.get_setting("notification_sound") == "none")
            check("and the subtitle says Silent", dialog._sound_row.get_subtitle() == "Silent")
            check("the ▶ is greyed for silence", not dialog._sound_play.get_sensitive())
        dialog._announce_row.set_active(True)
        check("the announce switch writes its setting",
              win.state.get_setting("announce_finished_runs") is True)
        dialog._announce_row.set_active(False)
        dialog.force_close()
    yield preferences_group

    def footer_after_prefs():
        expected = "Sound: None" if notifysound.available() else "Sound: Default"
        check("the sheet's footer follows the sound setting",
              sheet._sound_label.get_label() == expected, sheet._sound_label.get_label())
        win.state.set_setting("notification_sound", "default")
    yield footer_after_prefs


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
