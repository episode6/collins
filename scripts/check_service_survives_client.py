#!/usr/bin/env python3
"""End-to-end check that the service outlives its client (spec §3.20).

The service is its own process since PR-1.12b: a session's agent runs on a
pty the service holds, and the app is one client of it. This check starts
a service, runs a first Collins in a child process that starts a session
against a `claude` stub and types into it, kills that Collins with SIGKILL
(no shutdown of any kind), and then builds a second Collins in this
process on the same service: the sidebar must list the session, the
service's `ptys` table must still hold its pty with the agent alive, and
the relaunch must reopen its tab attached to that pty (open_tabs, PR-1.12c),
the VTE's screen matching
the service's screen model (`debug.screen`) text for text, the first
Collins' typing included.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_service_survives_client.py
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

E2E = tempfile.mkdtemp(prefix="collins-survive-")
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
TYPED = "typed before the kill"

for path in (f"{E2E}/projects", f"{E2E}/chats", f"{E2E}/bin", TRUSTED):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
os.makedirs(f"{E2E}/config/collins", exist_ok=True)
with open(STATE_FILE, "w", encoding="utf-8") as fh:
    fh.write('{"settings": {"welcome_seen": true, "gh_welcome_dismissed": true, "title_model": "none"}}')

# The stub: the CLI's box (❯ + U+00A0), a transcript so the row resolves,
# and a line editor that echoes what it is typed, so the screen holds the
# first Collins' typing for the second one to find.
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
                         "message": {"role": "user", "content": "survive the kill"}}) + "\n")
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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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
    time.sleep(180)
    print("timed out", file=sys.stderr)
    os._exit(3)


threading.Thread(target=watchdog, daemon=True).start()


def pty_rows() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh).get("ptys") or {}
    except (OSError, ValueError):
        return {}


# -- the first Collins, in a child, killed ------------------------------------------

FIRST = r"""
import os, sys, time, threading
sys.path.insert(0, %(repo)r)
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); gi.require_version("Vte", "3.91")
from gi.repository import GLib
from collins import i18n, trust
from collins.app import App
from collins.state import AppState
i18n.init(AppState().get_setting("language"))
trust.trust_dir(%(trusted)r)
app = App()

def steps():
    win = app.get_active_window()
    while win is None:
        yield 100
        win = app.get_active_window()
    tab = win.start_background_session(%(trusted)r)
    for _ in range(100):
        if tab.takes_prompt():
            break
        yield 200
    for _ in range(100):
        if tab.session_id:
            break
        yield 200
    tab.feed_child_text(%(typed)r + "\r")
    yield 800
    print("FIRST session", tab.session_id, "pty", tab._view.pty, flush=True)
    # Say we are ready to be killed, then idle: the kill is the parent's.
    print("FIRST ready", flush=True)
    while True:
        yield 1000

gen = steps()
def tick():
    try:
        delay = next(gen)
    except StopIteration:
        return GLib.SOURCE_REMOVE
    GLib.timeout_add(delay, tick)
    return GLib.SOURCE_REMOVE
GLib.timeout_add(250, tick)
app.run([])
"""

repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
service = e2e_service.start_service()
first = subprocess.Popen(
    [sys.executable, "-c", FIRST % {"repo": repo, "trusted": TRUSTED, "typed": TYPED}],
    stdout=subprocess.PIPE,
    text=True,
    env=dict(os.environ, PYTHONPATH=repo),
)
session_id = None
pty_id = None
deadline = time.monotonic() + 90
while time.monotonic() < deadline:
    line = first.stdout.readline()
    if not line:
        break
    sys.stdout.write("    first> " + line)
    if line.startswith("FIRST session"):
        parts = line.split()
        session_id, pty_id = parts[2], int(parts[4])
    if line.startswith("FIRST ready"):
        break
check("the first Collins started a session on the service", session_id is not None and pty_id is not None)
rows_before = pty_rows()
check("the ptys table holds its pty", str(pty_id) in rows_before, sorted(rows_before))
agent_pid = (rows_before.get(str(pty_id)) or {}).get("pid")

os.kill(first.pid, signal.SIGKILL)
first.wait()
check("the first Collins is dead (SIGKILL)", first.returncode == -signal.SIGKILL, first.returncode)
# A SIGKILLed process's sockets close at once, so the service sees the
# client go without waiting for a pong timeout: wait until it counts no
# clients (a probe link of our own asks, then goes), bounded.
from collins.api import server as api_server  # noqa: E402
from collins.api.client import SocketLink  # noqa: E402

probe = SocketLink(api_server.socket_path(os.environ["COLLINS_APP_ID"]), device="probe")
probe.connect()
deadline = time.monotonic() + 10
while time.monotonic() < deadline:
    if probe.call({"t": "service.status"}).get("clients") == 1:  # the probe itself
        break
    time.sleep(0.1)
check("the service dropped the killed client", probe.call({"t": "service.status"}).get("clients") == 1)
probe.shutdown()
check("the service is alive", service.poll() is None)
rows_after = pty_rows()
check("the pty is still in the table", str(pty_id) in rows_after, sorted(rows_after))
child_alive = bool(agent_pid) and os.path.exists(f"/proc/{agent_pid}")
check("the agent's shell lives on", child_alive, agent_pid)

# -- the second Collins, here -------------------------------------------------------

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import GLib  # noqa: E402

from collins import i18n, trust  # noqa: E402
from collins import terminal as terminal_mod  # noqa: E402
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
    for _ in range(50):
        if app.store.get_session(session_id) is not None:
            break
        yield 200
    found = app.store.get_session(session_id)
    check("the second Collins finds the session in its sidebar", found is not None, session_id)
    status = app._service_link.call({"t": "service.status"})
    check(
        "the service counts the pty and this client",
        status.get("ptys", 0) >= 1 and status.get("clients") == 1,
        status,
    )
    # Open it: since PR-1.12c the relaunch reopens the tabs the first
    # Collins had open (open_tabs, §3.21), attaching the one whose pty the
    # service still runs (a tab is a view over a pty, D31); the tab is
    # attached once more here for the reply's grid.
    page = None
    for _ in range(30):
        page = win._page_for_pty(pty_id)
        if page is not None:
            break
        yield 100
    if page is None:
        check("the relaunch reopened the tab over the running pty", False, win.open_tab_entries())
        app.quit()
        return
    tab = page.get_child()
    win.tab_view.set_selected_page(page)
    yield 500
    tab._client.claim(pty_id)
    reply = tab._view.attach(pty_id)
    check("attaching to the running pty answers its grid", bool(reply.get("cols")), reply)
    for _ in range(40):
        if not tab._view.guarded:
            break
        yield 100
    yield 500
    screen = tab._client.request({"t": "debug.screen", "pty": pty_id})
    vte_text = terminal_mod._capture_contents(tab.terminal)
    model_lines = [line.rstrip() for line in screen["capture"].splitlines() if line.strip()]
    vte_lines = [line.rstrip() for line in vte_text.splitlines() if line.strip()]
    check(
        "the VTE shows what the first Collins typed", any(TYPED in line for line in vte_lines), vte_lines[-4:]
    )
    check(
        "the VTE's screen matches the service's model",
        vte_lines == model_lines,
        (vte_lines[-3:], model_lines[-3:]),
    )
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
