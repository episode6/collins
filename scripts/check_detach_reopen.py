#!/usr/bin/env python3
"""End-to-end check of what detaching, quitting and reopening mean (spec
§3.21, PR-1.12c).

A first Collins, in a child process, starts a session against a `claude`
stub and types into it, then *Detaches* it: the tab closes, the service
still runs its pty, and the sidebar row is a running row (the item's
`running`, the yellow `detached` class). Activating the row attaches a tab
to the same pty (D31) — the screen still holds what was typed — and the
quit dialog's *Quit* closes the window while the session keeps running.
`open_tabs` names the session. A second Collins, in this process, on the
same service and scratch dirs, reopens the tab on launch, attached to the
same pty, the typing still on its screen.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_detach_reopen.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

E2E = tempfile.mkdtemp(prefix="collins-detach-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

os.environ["COLLINS_DEBUG_API"] = "1"
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["COLLINS_PTY_STATE_DIR"] = f"{E2E}/pty"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"

TRUSTED = f"{E2E}/dev/alpha"
SHIM = f"{E2E}/bin/claude"
STATE_FILE = f"{E2E}/config/collins/state.json"
UI_FILE = f"{E2E}/config/collins/ui-state.json"
TYPED = "typed before the detach"

for path in (f"{E2E}/projects", f"{E2E}/chats", f"{E2E}/bin", TRUSTED):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
os.makedirs(f"{E2E}/config/collins", exist_ok=True)
with open(STATE_FILE, "w", encoding="utf-8") as fh:
    # "ask": the quit dialog shows, so its *Quit* is what this check presses.
    fh.write(json.dumps({"settings": {
        "welcome_seen": True, "gh_welcome_dismissed": True, "title_model": "none",
        "quit_with_running_sessions": "ask", "quit_detach_migrated": True,
    }}))

# The stub: the CLI's box (❯ + U+00A0), a transcript so the row resolves,
# and a line editor that echoes what it is typed.
_SHIM = r"""#!/usr/bin/env python3
import json, os, sys, time, tty, uuid
projects = os.environ["COLLINS_PROJECTS_DIR"]
cwd = os.getcwd()
encoded = "".join(c if c.isalnum() else "-" for c in cwd)
os.makedirs(os.path.join(projects, encoded), exist_ok=True)
sid = str(uuid.uuid4())
now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
with open(os.path.join(projects, encoded, sid + ".jsonl"), "w") as fh:
    fh.write(json.dumps({"type": "user", "cwd": cwd, "sessionId": sid, "timestamp": now,
                         "message": {"role": "user", "content": "detach and come back"}}) + "\n")
tty.setraw(0)
text = ""
def draw():
    sys.stdout.write("\r\x1b[K❯ " + text)
    sys.stdout.flush()
sys.stdout.write("session " + sid + "\r\n")
draw()
while True:
    chunk = os.read(0, 4096)
    if not chunk:
        break
    for b in chunk:
        if b == 0x7F:
            text = text[:-1]
        elif b == 0x0D:
            sys.stdout.write("\r\n" + "you said: " + text + "\r\n")
            text = ""
        elif 0x20 <= b < 0x7F:
            text += chr(b)
    draw()
"""
with open(SHIM, "w", encoding="utf-8") as fh:
    fh.write(_SHIM)
os.chmod(SHIM, 0o755)
os.environ["PATH"] = f"{E2E}/bin:{os.environ['PATH']}"

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, REPO)

import e2e_service  # noqa: E402

PASSED = 0
FAILED = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  ok  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}  {detail!r}")


def watchdog() -> None:
    time.sleep(240)
    print("timed out", file=sys.stderr)
    os._exit(3)


threading.Thread(target=watchdog, daemon=True).start()


def read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


# -- the first Collins, in a child: detach, reattach, Quit ---------------------------

FIRST = r"""
import os, sys, time, threading
sys.path.insert(0, %(repo)r)
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); gi.require_version("Vte", "3.91")
from gi.repository import Adw, GLib
from collins import i18n, trust
from collins.app import App
from collins.state import AppState
i18n.init(AppState().get_setting("language"))
trust.trust_dir(%(trusted)r)
app = App()

def say(*words):
    print("FIRST", *words, flush=True)

def screen(tab, pty):
    try:
        return tab._client.request({"t": "debug.screen", "pty": pty}).get("capture") or ""
    except Exception as exc:
        return "error: %%s" %% exc

def steps():
    win = app.get_active_window()
    while win is None:
        yield 100
        win = app.get_active_window()
    tab = win.start_background_session(%(trusted)r)
    for _ in range(100):
        if tab.takes_prompt() and tab.session_id:
            break
        yield 200
    sid = tab.session_id
    pty = tab.pty_id
    say("session", sid, "pty", pty)
    tab.feed_child_text(%(typed)r)
    yield 800
    for _ in range(50):
        if app.store.get_item(sid) is not None:
            break
        yield 200
    # Detach, from the row menu's action.
    win.activate_action("win.detach-session", GLib.Variant("s", sid))
    yield 800
    say("detached-tab-gone", win._page_for(sid) is None)
    say("detached-pty-runs", app.store.pty_running(pty), app.store.pty_for(sid) == pty)
    item = app.store.get_item(sid)
    say("detached-item-running", bool(item and item.running))
    row = win.sidebar._rows.get(sid)
    say("detached-row-yellow", bool(row and row.has_css_class("detached")))
    status = app._service_link.call({"t": "service.status"})
    say("detached-service-ptys", status.get("ptys"))
    # Activate the row: a tab over the same pty, not a second one.
    win.sidebar._on_row_activated(win.sidebar.list, row)
    for _ in range(30):
        if win._page_for(sid) is not None:
            break
        yield 100
    page = win._page_for(sid)
    again = page.get_child() if page is not None else None
    say("reattached-same-pty", again is not None and again.pty_id == pty)
    say("reattached-ptys", app._service_link.call({"t": "service.status"}).get("ptys"))
    yield 600
    say("reattached-screen-typed", %(typed)r in screen(again, pty) if again else False)
    # Quit, through the dialog's Quit.
    app.quit_all_windows()
    dialog = None
    for _ in range(30):
        dialog = win.get_visible_dialog()
        if dialog is not None:
            break
        yield 100
    heading = dialog.get_heading() if isinstance(dialog, Adw.AlertDialog) else ""
    say("quit-dialog", "running in the Collins service" in heading, repr(heading))
    say("quit-dialog-notice", "status icon leaves with the window" in (dialog.get_body() if dialog else ""))
    if dialog is not None:
        dialog.set_close_response("quit")
        dialog.close()
    yield 2000
    say("still-running-after-quit?")

gen = steps()
def tick():
    try:
        delay = next(gen)
    except StopIteration:
        return GLib.SOURCE_REMOVE
    GLib.timeout_add(delay, tick)
    return GLib.SOURCE_REMOVE
GLib.timeout_add(250, tick)
threading.Thread(target=lambda: (time.sleep(150), os._exit(4)), daemon=True).start()
app.run([])
say("quit")
"""

service = e2e_service.start_service()
first = subprocess.Popen(
    [sys.executable, "-c", FIRST % {"repo": REPO, "trusted": TRUSTED, "typed": TYPED}],
    stdout=subprocess.PIPE,
    text=True,
    env=dict(os.environ, PYTHONPATH=REPO),
)
said: dict[str, list[str]] = {}
deadline = time.monotonic() + 170
while time.monotonic() < deadline:
    line = first.stdout.readline()
    if not line:
        break
    sys.stdout.write("    first> " + line)
    parts = line.split()
    if parts and parts[0] == "FIRST" and len(parts) > 1:
        said[parts[1]] = parts[2:]
first.wait(30)

session_id = (said.get("session") or [None])[0]
pty_id = int(said["session"][2]) if said.get("session") and said["session"][2].isdigit() else None
check("the first Collins started a session", bool(session_id) and pty_id is not None, said.get("session"))
check("Detach closed the tab", said.get("detached-tab-gone") == ["True"], said.get("detached-tab-gone"))
check("the service still runs the pty", said.get("detached-pty-runs") == ["True", "True"],
      said.get("detached-pty-runs"))
check("the row is a running row", said.get("detached-item-running") == ["True"],
      said.get("detached-item-running"))
check("the running row wears the yellow line", said.get("detached-row-yellow") == ["True"],
      said.get("detached-row-yellow"))
check("activating the row attaches to the same pty", said.get("reattached-same-pty") == ["True"],
      said.get("reattached-same-pty"))
check("no second pty was spawned", said.get("reattached-ptys") == said.get("detached-service-ptys"),
      (said.get("detached-service-ptys"), said.get("reattached-ptys")))
check("the reattached screen holds the typing", said.get("reattached-screen-typed") == ["True"],
      said.get("reattached-screen-typed"))
check("the quit dialog says the sessions keep running", (said.get("quit-dialog") or [""])[0] == "True",
      said.get("quit-dialog"))
check("the first quit dialog carries the status-icon notice", said.get("quit-dialog-notice") == ["True"],
      said.get("quit-dialog-notice"))
check("Quit quit the first Collins", "quit" in said and first.returncode == 0, first.returncode)
check("the service is alive", service.poll() is None)
rows = read_json(STATE_FILE).get("ptys") or {}
check("the pty outlived the quit", str(pty_id) in rows, sorted(rows))
blocks = read_json(UI_FILE).get("services") or {}
open_tabs = [tab for block in blocks.values() for tab in block.get("open_tabs") or []]
check("open_tabs names the session", open_tabs == [session_id], open_tabs)
settings = (read_json(UI_FILE).get("device") or {}).get("settings") or {}
check("the status-icon notice is shown once", settings.get("quit_notice_shown") is True,
      settings.get("quit_notice_shown"))

# -- the second Collins, here: the tab is back on the same pty -------------------------

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import GLib  # noqa: E402

from collins import i18n, trust  # noqa: E402
from collins.app import App  # noqa: E402
from collins.state import AppState  # noqa: E402

i18n.init(AppState().get_setting("language"))
trust.trust_dir(TRUSTED)
app = App()


def steps():
    win = app.get_active_window()
    while win is None:
        yield 100
        win = app.get_active_window()
    page = None
    for _ in range(50):
        page = win._page_for_pty(pty_id) if pty_id is not None else None
        if page is not None:
            break
        yield 200
    check("the relaunch reopened the tab on the same pty", page is not None, win.open_tab_entries())
    tab = page.get_child() if page is not None else None
    check("the reopened tab is the session's", tab is not None and tab.session_id == session_id,
          tab.session_id if tab else None)
    status = app._service_link.call({"t": "service.status"})
    check("the relaunch spawned nothing", status.get("ptys") == len(rows), (status.get("ptys"), rows))
    yield 800
    if tab is not None:
        capture = tab._client.request({"t": "debug.screen", "pty": pty_id}).get("capture") or ""
        check("the reopened screen holds the typing", TYPED in capture, capture[-200:])
    app.quit()


generator = steps()


def tick() -> bool:
    try:
        delay = next(generator)
    except StopIteration:
        return GLib.SOURCE_REMOVE
    GLib.timeout_add(delay, tick)
    return GLib.SOURCE_REMOVE


GLib.timeout_add(250, tick)
app.run([])
print(f"\n{PASSED} passed, {FAILED} failed")
shutil.rmtree(E2E, ignore_errors=True)
sys.exit(1 if FAILED else 0)
