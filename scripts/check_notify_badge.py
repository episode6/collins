#!/usr/bin/env python3
"""Headless check that the badge's number is the notification center's — run
on a dev machine, never by pytest.

Drives a real App against staged data and pushes the store's edges through
it: a session's unread flag going on and off, the barber pole covering and
uncovering that flag, a placeholder tab's flag and pole (which live in the
sidebar, not the store), the placeholder → real-row handoff, and a message
row landing in state.json while the synthetic rows stay off disk. Every step
asserts the center's rows and the number App.tray_view() would badge.

Stages its own throwaway scratch tree and app id (one session's transcript
under a project named alpha-widgets, and a name for it in state.json), so it
runs anywhere the e2e suite does:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_notify_badge.py

None of it is reachable from pytest: the store's signals need a GObject main
loop and the window's placeholder path needs a sidebar, and tests/conftest.py
blocks the GTK stack to match CI. What can be decided without a toolkit —
coalescing, counts, the delivery table, the persisted shape — is
tests/test_notifycenter.py's.

Run it behind the headless wrapper, or a window opens on the user's screen.
"""

import json
import os
import re
import shutil
import sys
import tempfile

E2E = tempfile.mkdtemp(prefix="collins-badge-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

# Isolation first: every one of these is read at import time somewhere below.
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"

SESSION = "11111111-1111-4111-8111-111111111111"
PLACEHOLDER = "placeholder-77"
PROJECT_DIR = f"{E2E}/dev/alpha-widgets"
STATE_FILE = f"{E2E}/config/collins/state.json"

# One session in one project, named so the row's title is a known string.
_encoded = re.sub(r"[^A-Za-z0-9]", "-", PROJECT_DIR)
for path in (f"{E2E}/projects/{_encoded}", f"{E2E}/chats", f"{E2E}/config/collins", PROJECT_DIR):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
with open(f"{E2E}/projects/{_encoded}/{SESSION}.jsonl", "w", encoding="utf-8") as fh:
    fh.write(
        json.dumps(
            {
                "type": "user",
                "timestamp": "2026-07-25T14:12:03Z",
                "cwd": PROJECT_DIR,
                "message": {"role": "user", "content": "Fix the flaky spinner animation"},
            }
        )
        + "\n"
    )
with open(STATE_FILE, "w", encoding="utf-8") as fh:
    json.dump({"names": {SESSION: "Fix spinner animation"}, "settings": {"welcome_seen": True}}, fh)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib  # noqa: E402

from collins import i18n, notifycenter  # noqa: E402
from collins.app import App  # noqa: E402
from collins.state import AppState  # noqa: E402

failures = []
passed = [0]


def check(label, ok, detail=""):
    print(f"{'  ok' if ok else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")
    if ok:
        passed[0] += 1
    else:
        failures.append(label)


def saved_notifications() -> list:
    """What state.json holds right now, read back off the disk."""
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh).get("notifications", [])
    except (OSError, json.JSONDecodeError):
        return []


def run_checks(app: App) -> None:
    center = app.notification_center
    store = app.store
    win = app.get_active_window()

    def badge() -> int:
        return app.tray_view().unread

    check("the center starts empty", center.rows() == [] and badge() == 0)
    check("nothing is on disk", saved_notifications() == [])

    # -- a real row's flag, and the pole over it ----------------------------
    store.set_unread(SESSION, True)
    row = center.get(notifycenter.green_id(SESSION))
    check("a finished run is one synthetic row", row is not None and badge() == 1)
    check("the row is named after the session",
          row is not None and row.title == "Fix spinner animation" and row.project == "alpha-widgets",
          f"{row.title!r} / {row.project!r}" if row else "no row")
    check("the row's body is the finished-run text",
          row is not None and row.body == "Finished a run", row.body if row else "")
    store.set_busy(SESSION, True)
    check("a flagged session back at work leaves the badge",
          not center.is_green(SESSION) and badge() == 0)
    check("its flag is still up", store.get_item(SESSION).unread)
    store.set_busy(SESSION, False)
    check("the badge comes back when the turn ends", center.is_green(SESSION) and badge() == 1)
    store.set_unread(SESSION, False)
    check("the flag coming off removes the row", center.rows() == [] and badge() == 0)
    check("synthetic rows never touched the disk", saved_notifications() == [])

    # -- a placeholder's flag lives in the sidebar --------------------------
    win.sidebar.add_placeholder(PLACEHOLDER, PROJECT_DIR, "agent-claude-symbolic")
    win._on_session_finished(PLACEHOLDER)
    row = center.get(notifycenter.green_id(PLACEHOLDER))
    check("a placeholder's finish is a row under its own key",
          row is not None and row.session_id == PLACEHOLDER and badge() == 1)
    check("the placeholder row names its project",
          row is not None and row.project == "alpha-widgets", row.project if row else "")
    view = app.tray_view()
    check("the placeholder is counted as a session, not a row",
          view.sessions == 1 and not any(e.action == "focus-session" for e in view.menu))
    win._on_activity_changed(PLACEHOLDER, True)
    check("a working placeholder leaves the badge",
          not center.is_green(PLACEHOLDER) and badge() == 0)
    win._on_activity_changed(PLACEHOLDER, False)
    check("and comes back when its turn ends", center.is_green(PLACEHOLDER) and badge() == 1)

    # -- the handoff: placeholder down, session up ---------------------------
    win.sidebar.remove_placeholder(PLACEHOLDER)
    win._sync_placeholder_green(PLACEHOLDER)
    store.set_unread(SESSION, True)
    check("the handoff leaves one row, under the session's key",
          center.green_sessions() == [SESSION] and badge() == 1, str(center.green_sessions()))

    # -- what reaches the disk ------------------------------------------------
    center.post(center.make(notifycenter.KIND_MESSAGE, SESSION, "Fix spinner animation",
                            "alpha-widgets", "Need a decision on the easing"))
    check("a message counts beside the green", badge() == 2)
    saved = saved_notifications()
    check("the message is on disk, the synthetic row is not",
          [r["kind"] for r in saved] == ["message"], str([r["kind"] for r in saved]))
    center.mark_session_read(SESSION)
    check("visiting the session reads everything it said; the green row stays until its flag drops",
          badge() == 0 and center.is_green(SESSION))
    check("the read flag is on disk", saved_notifications()[0]["read"] is True)
    store.set_unread(SESSION, False)
    check("the synthetic row leaves with the flag", not center.is_green(SESSION) and badge() == 0)

    # -- a row whose session went away --------------------------------------
    center.set_green("ghost-session", True)
    app._on_store_refreshed(store, False)
    check("a rescan drops a synthetic row with no item and no placeholder",
          not center.is_green("ghost-session"))

    # A fresh AppState reads the same list back.
    check("a fresh load sees the message", [r["kind"] for r in AppState().get_notifications()] == ["message"])


def main() -> int:
    i18n.init("")
    app = App()
    tries = 0
    exit_code = [1]

    def prepare() -> bool:
        nonlocal tries
        tries += 1
        win = app.get_active_window()
        if win is None or app.store.get_session(SESSION) is None:
            if tries > 40:
                print("timed out waiting for the window/session", file=sys.stderr)
                app.quit()
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE
        try:
            run_checks(app)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"exception: {exc!r}")
            import traceback

            traceback.print_exc()
        if not failures:
            exit_code[0] = 0
        app.quit()
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
