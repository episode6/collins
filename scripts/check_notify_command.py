#!/usr/bin/env python3
# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""End-to-end check for the Notifications group's Service half — dev machine.

The Notifications group is mixed (D62): the in-app card, sound and bell
rows are this device's, and the Service page holds the *Notification
command* row (the program the service runs for a message or a finished run
when no window is attached), a caption under it and a *Send test* button.
The box saves like the Git group's entry (a text `shlex` cannot split wears
the error style and leaves the setting alone; empty saves ""), and *Send
test* commits the box and sends its text to the service, which runs it
once with test values in the environment and answers with the exit status
or the error, shown in the caption. Checked against a real App and a real
service:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_notify_command.py

The command is a scratch script that records its environment; nothing is
sent anywhere.

Run it behind the headless wrapper, or a window opens on the user's screen.
"""

import json
import os
import sys
import tempfile

E2E = tempfile.mkdtemp(prefix="collins-notifycmd-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

# Isolation first: every one of these is read at import time somewhere below.
os.environ["COLLINS_DEBUG_API"] = "1"  # the e2e probe (debug.*): served only with this set
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["COLLINS_USAGE_FIXTURE"] = f"{E2E}/usage-fixture.json"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"
os.environ["XDG_CACHE_HOME"] = f"{E2E}/cache"
os.environ["LANG"] = "C"  # the check reads English titles

SHIM = f"{E2E}/bin/claude"
RECORD = f"{E2E}/record.sh"
OUT = f"{E2E}/out.txt"
os.environ["OUT"] = OUT  # the script the service runs writes here

for path in (f"{E2E}/chats", f"{E2E}/bin", f"{E2E}/projects", f"{E2E}/config/collins"):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
with open(f"{E2E}/usage-fixture.json", "w", encoding="utf-8") as fh:
    json.dump({"limits": [], "extra_usage": {"is_enabled": False}}, fh)
with open(f"{E2E}/config/collins/state.json", "w", encoding="utf-8") as fh:
    fh.write(
        '{"settings": {"title_model": "none", "show_usage_panel": false, "language": "", '
        '"welcome_seen": true, "gh_welcome_dismissed": true}}'
    )
with open(SHIM, "w", encoding="utf-8") as fh:
    fh.write(
        "#!/usr/bin/env python3\n"
        "import sys, time\n"
        "sys.stdout.write('\\u276f  ')\n"
        "sys.stdout.flush()\n"
        "while True:\n"
        "    time.sleep(3600)\n"
    )
os.chmod(SHIM, 0o755)
with open(RECORD, "w", encoding="utf-8") as fh:
    fh.write(
        "#!/bin/sh\n"
        "{\n"
        '  for a in "$@"; do printf \'arg=%s\\n\' "$a"; done\n'
        '  printf \'KIND=%s\\n\' "$COLLINS_NOTIFY_KIND"\n'
        '  printf \'TITLE=%s\\n\' "$COLLINS_NOTIFY_TITLE"\n'
        '  printf \'BODY=%s\\n\' "$COLLINS_NOTIFY_BODY"\n'
        '  printf \'SESSION=%s\\n\' "$COLLINS_NOTIFY_SESSION"\n'
        '  printf \'PROJECT=%s\\n\' "$COLLINS_NOTIFY_PROJECT"\n'
        '} > "$OUT"\n'
        'exit "${EXIT:-0}"\n'
    )
os.chmod(RECORD, 0o755)
os.environ["PATH"] = f"{E2E}/bin:{os.environ['PATH']}"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
import e2e_service  # noqa: E402
from gi.repository import GLib  # noqa: E402

from collins import i18n, prefslayout  # noqa: E402
from collins.app import App  # noqa: E402
from collins.prefs import PreferencesDialog  # noqa: E402
from collins.state import AppState  # noqa: E402

PASSED = 0
FAILED = 0

DEVICE_TITLES = [
    "In-app notifications",
    "Card theme",
    "Sound",
    "Bells from other sessions",
    "Announce finished runs",
]


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  ok  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}  {detail}")


def later(fn, ms: int = 1000) -> bool:
    GLib.timeout_add(ms, fn)
    return GLib.SOURCE_REMOVE


def titles(group) -> list[str]:
    return [row.get_title() for row in group.rows]


def visible_rows(dialog) -> list[str]:
    return [
        row.get_title()
        for page in dialog._pages
        for group in page.groups
        if group.get_visible()
        for row in group.rows
        if row.get_visible()
    ]


def setting(key: str):
    return AppState().get_setting(key)


def settle() -> None:
    e2e_service.settle()


def typed(row, text: str) -> None:
    row.set_text(text)
    settle()
    row.emit("entry-activated")
    settle()


def recorded() -> dict:
    with open(OUT, encoding="utf-8") as fh:
        return dict(line.rstrip("\n").split("=", 1) for line in fh if "=" in line)


def send_test(button, caption, help_text: str) -> str:
    """Click *button*; wait for the caption to leave the help; its subtitle."""
    button.emit("clicked")
    check("Send test is insensitive until the reply", not button.get_sensitive())
    e2e_service.wait_until(lambda: caption.get_subtitle() != help_text, timeout_s=15)
    settle()
    return caption.get_subtitle()


i18n.init(AppState().get_setting("language"))
e2e_service.start_service()
app = App()

exit_code = 1
tries = 0
state: dict = {}


def stage() -> bool:
    global tries
    tries += 1
    win = app.get_active_window()
    if win is None:
        if tries > 40:
            print("timed out waiting for the window", file=sys.stderr)
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE
    other = win.get_visible_dialog()
    if other is not None:
        other.force_close()
    dialog = PreferencesDialog(win.state, lambda: state.setdefault("changes", []).append(1))
    dialog.present(win)
    state.update(win=win, dialog=dialog)
    return later(step_layout)


def step_layout() -> bool:
    dialog = state["dialog"]
    check(
        "the Notifications group is on both pages",
        "notifications" in prefslayout.page_groups("device")
        and "notifications" in prefslayout.page_groups("service"),
    )
    device = dialog._group("notifications", "device")
    service = dialog._group("notifications", "service")
    check("This device keeps the five rows it had", titles(device) == DEVICE_TITLES, titles(device))
    check(
        "the Service half is the command and its caption",
        titles(service) == ["Notification command", ""],
        titles(service),
    )
    entry, caption = service.rows
    state.update(entry=entry, caption=caption, service=service)
    # The Send test button is the entry row's suffix.
    found = []

    def walk(widget):
        child = widget.get_first_child()
        while child is not None:
            if type(child).__name__ == "Button" and child.get_label() == "Send test":
                found.append(child)
            walk(child)
            child = child.get_next_sibling()

    walk(entry)
    check("the row has a Send test button", len(found) == 1, found)
    state["button"] = found[0]
    state["help"] = caption.get_subtitle()
    check("the box opens empty", entry.get_text() == "", entry.get_text())
    check("and Send test is insensitive with nothing to send", not found[0].get_sensitive())
    check(
        "the caption explains the environment",
        "COLLINS_NOTIFY_KIND" in state["help"] and "without one" in state["help"],
        state["help"],
    )
    return later(step_save, 200)


def step_save() -> bool:
    entry, button = state["entry"], state["button"]
    entry.set_text("tpush 'unterminated")
    settle()
    check("a text shlex cannot split wears the error style", entry.has_css_class("error"))
    check("and Send test is insensitive", not button.get_sensitive())
    entry.emit("entry-activated")
    settle()
    check("and leaves the setting alone", setting("notify_command") == "", setting("notify_command"))
    typed(entry, f"{RECORD} one 'two words'")
    check(
        "a command that splits is kept on Enter",
        setting("notify_command") == f"{RECORD} one 'two words'",
        setting("notify_command"),
    )
    check("and drops the error style", not entry.has_css_class("error"))
    check("and Send test is sensitive", button.get_sensitive())
    typed(entry, "")
    check("clearing the box saves the empty command (off)", setting("notify_command") == "")
    return later(step_test, 100)


def step_test() -> bool:
    entry, button, caption = state["entry"], state["button"], state["caption"]
    # Unsaved text: Send test commits the box, then tests what the box shows.
    entry.set_text(f"{RECORD} hello")
    settle()
    check("typing alone has not saved it yet", setting("notify_command") == "", setting("notify_command"))
    shown = send_test(button, caption, state["help"])
    saved = setting("notify_command")
    check("Send test saved the box", saved == f"{RECORD} hello", saved)
    check("the caption shows the exit status", shown == "Exit status 0", shown)
    check("and is not an error", not caption.has_css_class("error"))
    got = recorded()
    check("the service ran the box's text", got.get("arg") == "hello", got)
    check(
        "with the test values in the five variables",
        (got["KIND"], got["TITLE"], got["BODY"], got["SESSION"], got["PROJECT"])
        == ("test", "Collins test notification", "Sent from Preferences", "", ""),
        got,
    )
    check("and the button is back", button.get_sensitive())
    entry.set_text(f"{RECORD} again")
    back = caption.get_subtitle()
    check("typing in the box puts the help back", back == state["help"], back)
    settle()
    # A refused or failing run is an error caption.
    typed(entry, "/nonexistent/collins-notify-test")
    shown = send_test(button, caption, state["help"])
    check("a program that is missing shows the OS's words", shown == "No such file or directory", shown)
    check("in the error style", caption.has_css_class("error"))
    # Text the service sent is never markup.
    state["dialog"]._on_notify_test_result(None, "<b>&oops</b>")
    check("the caption does not parse markup", not caption.get_use_markup())
    check("and shows the text as sent", caption.get_subtitle() == "<b>&oops</b>", caption.get_subtitle())
    return later(step_exit_status, 100)


def step_exit_status() -> bool:
    entry, button, caption = state["entry"], state["button"], state["caption"]
    typed(entry, f"env EXIT=3 {RECORD}")
    shown = send_test(button, caption, state["help"])
    check("a non-zero exit shows its status", shown == "Exit status 3", shown)
    check("in the error style", caption.has_css_class("error"))
    typed(entry, "")
    return later(step_search, 100)


def step_search() -> bool:
    dialog = state["dialog"]
    entry = dialog._search_entry
    for query, expected in (
        ("ntfy", ["Notification command", ""]),
        ("pushover", ["Notification command", ""]),
    ):
        entry.set_text(query)
        dialog._apply_filter()
        rows = visible_rows(dialog)
        check(f"searching {query!r} finds {expected}", rows == expected, rows)
    entry.set_text("")
    dialog._apply_filter()
    check(
        "show_group opens on the group's title, This device first",
        dialog.show_group("notifications") and dialog._view_stack.get_visible_child() is dialog._device_page,
    )
    dialog._apply_filter()
    rows = visible_rows(dialog)
    check(
        "and shows every row of both halves",
        all(t in rows for t in [*DEVICE_TITLES, "Notification command"]),
        rows,
    )
    dialog.force_close()
    return done()


def done() -> bool:
    global exit_code
    print(f"\n{PASSED} passed, {FAILED} failed")
    exit_code = 0 if FAILED == 0 else 1
    app.quit()
    return GLib.SOURCE_REMOVE


GLib.timeout_add(250, stage)
app.run([])
sys.exit(exit_code)
