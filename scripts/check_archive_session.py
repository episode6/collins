#!/usr/bin/env python3
"""End-to-end check for the archive_session MCP tool — run on a dev machine.

Exercises App._mcp_archive_session against a real App: a real window, a real
AdwTabView, a real VTE running a real child. The claims that matter are the
ones tests/test_mcptools.py (schema, switch, identity) can't make, because
they are about *when* the archive lands: never inside the call that asked —
the reply has to reach the agent first — but on the session's finish edge,
the same output-stopped moment that flags a row unread; and then the way the
user's own Archive does, closing the tab through the running-session close
flow rather than around it.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_archive_session.py

Run it behind the headless wrapper, or a window opens on the user's screen.

`claude` is a stub that draws the CLI's idle prompt, writes a uuid.jsonl
transcript under its cwd — the file whose stem Collins reads as the session
id, so the tab resolves and the tool has a session to archive — and then holds
the terminal open like a real CLI until the close flow's Ctrl+C reaches it.
The Archiving a running session setting is "exit" here, so the close needs
no dialog answered: the tab gets its clean exit and the archive lands when
the shell returns.
"""

import os
import shutil
import signal
import sys
import tempfile

E2E = tempfile.mkdtemp(prefix="collins-archivesession-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

# Isolation first: every one of these is read at import time somewhere below.
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"

TRUSTED = f"{E2E}/dev/alpha"
SHIM = f"{E2E}/bin/claude"

for path in (f"{E2E}/projects", f"{E2E}/chats", f"{E2E}/bin", TRUSTED):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
# The first-launch welcome (collins.welcome) is answered already: it would
# otherwise sit over the window under test. And a running session archives
# by exiting, so no dialog interposes between the finish edge and the close.
os.makedirs(f"{E2E}/config/collins", exist_ok=True)
with open(f"{E2E}/config/collins/state.json", "w", encoding="utf-8") as fh:
    fh.write('{"settings": {"welcome_seen": true, "archive_running_session": "exit"}}')

# The child under test: the idle prompt, a transcript keyed by its cwd so the
# tab resolves a session id, then a hold that a Ctrl+C (the close flow's clean
# exit) ends the way the real CLI's would.
_SHIM = r'''#!/usr/bin/env python3
import os, re, sys, time, uuid, pathlib
sys.stdout.write("❯ ")  # ❯ + no-break space; cursor parks right after
sys.stdout.flush()
cwd = os.getcwd()
enc = re.sub(r"[^A-Za-z0-9]", "-", cwd)
proj = pathlib.Path(os.environ["COLLINS_PROJECTS_DIR"]) / enc
proj.mkdir(parents=True, exist_ok=True)
sid = str(uuid.uuid4())
(proj / (sid + ".jsonl")).write_text('{"type":"summary","cwd":"%s"}\n' % cwd)
try:
    while True:
        time.sleep(3600)
except KeyboardInterrupt:
    sys.exit(0)
'''
with open(SHIM, "w", encoding="utf-8") as fh:
    fh.write(_SHIM)
os.chmod(SHIM, 0o755)
os.environ["PATH"] = f"{E2E}/bin:{os.environ['PATH']}"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import GLib  # noqa: E402

from collins import i18n, trust  # noqa: E402
from collins.app import App  # noqa: E402
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


i18n.init(AppState().get_setting("language"))
trust.trust_dir(TRUSTED)
app = App()

ARMED_REPLY = (
    True,
    "Collins will archive this session once this turn ends — finish your "
    "reply; don't start anything else.",
)
tries = 0
state: dict = {}


def found():
    return state["win"], state["caller"]


def stage() -> bool:
    """Wait for the window, open the session tab that will archive itself."""
    global tries
    tries += 1
    win = app.get_active_window()
    if win is None:
        if tries > 40:
            print("timed out waiting for the window", file=sys.stderr)
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE
    # An empty tab view makes the background launch fall through to the
    # foreground: an ordinary session tab on screen.
    state["win"] = win
    state["caller"] = win.start_background_session(TRUSTED)
    # The resolver hasn't had a chance to run yet: the tab has no session id,
    # and the tool says so instead of arming an archive for nobody.
    got = app._mcp_archive_session(found(), {})
    check(
        "an unresolved tab is told to try again",
        got == (False, "The session isn't resolved in Collins yet — try again in a moment"),
        got,
    )
    tries = 0
    GLib.timeout_add(500, wait_resolved)
    return GLib.SOURCE_REMOVE


def wait_resolved() -> bool:
    """The shim's transcript lands and the resolver binds the id."""
    global tries
    tries += 1
    sid = state["caller"].session_id
    if not sid:
        if tries > 40:
            check("the caller tab resolved a session id", False, "timed out")
            return finish()
        return GLib.SOURCE_CONTINUE
    check("the caller tab resolved a session id", True)
    state["sid"] = sid
    GLib.timeout_add(1000, fire_tool)  # let the tab settle on screen
    return GLib.SOURCE_REMOVE


def fire_tool() -> bool:
    """Call the tool from inside a 'turn' — the tracker reads the session
    busy, as it does while a real agent is mid-call — and pin that nothing
    happens until that turn ends."""
    win, caller = found()
    sid = state["sid"]
    # The shim prints nothing after its prompt, so this mark is the whole
    # turn: the tracker's idle window (activity.IDLE_S) runs out on its own
    # and the sweep reports the finish — the real mechanism, unhurried.
    win._activity.mark(sid)
    check("the tracker reads the session busy", win._activity.is_busy(sid))
    before = win.tab_view.get_n_pages()
    got = app._mcp_archive_session(found(), {})
    check("the tool replies that the archive waits for the turn", got == ARMED_REPLY, got)
    check("the archive is armed on the window", sid in win._archive_when_finished)
    check("nothing is archived yet", not win.state.is_archived(sid))
    check("the tab is still open", win.tab_view.get_n_pages() == before, win.tab_view.get_n_pages())
    check("the tracker still reads the session busy", win._activity.is_busy(sid))
    state["before"] = before
    global tries
    tries = 0
    GLib.timeout_add(500, wait_archived)
    return GLib.SOURCE_REMOVE


def wait_archived() -> bool:
    """The idle window runs out (~2s), the finish edge fires, the archive
    lands through the running-session close flow: a clean exit fed to the
    shim, the tab closing once the shell returns, then the archive itself."""
    global tries
    tries += 1
    win = state["win"]
    sid = state["sid"]
    if not win.state.is_archived(sid):
        if tries > 60:
            check("the session was archived once the turn ended", False, "timed out")
            return finish()
        return GLib.SOURCE_CONTINUE
    check("the session was archived once the turn ended", True)
    check("the arm was spent", sid not in win._archive_when_finished)
    # A close through the flow finishes asynchronously from the archive's
    # own landing (they are the same _on_close_page pass), but a page still
    # counted here would mean the archive went around the tab.
    GLib.timeout_add(500, verify_closed)
    return GLib.SOURCE_REMOVE


def verify_closed() -> bool:
    win = state["win"]
    check(
        "the tab closed with the archive",
        win.tab_view.get_n_pages() == state["before"] - 1,
        win.tab_view.get_n_pages(),
    )
    check("the tab's session id has no page any more", win._page_for(state["sid"]) is None)
    # The other way in: a session the tracker already reads as idle has no
    # finish edge coming, so the archive lands after a beat instead.
    state["caller"] = win.start_background_session(TRUSTED)
    global tries
    tries = 0
    GLib.timeout_add(500, wait_second_resolved)
    return GLib.SOURCE_REMOVE


def wait_second_resolved() -> bool:
    global tries
    tries += 1
    sid = state["caller"].session_id
    if not sid:
        if tries > 40:
            check("the second tab resolved a session id", False, "timed out")
            return finish()
        return GLib.SOURCE_CONTINUE
    check("the second tab resolved a session id", True)
    state["sid2"] = sid
    GLib.timeout_add(2500, fire_idle)  # past the launch paint's hold and idle window
    return GLib.SOURCE_REMOVE


def fire_idle() -> bool:
    win = state["win"]
    sid = state["sid2"]
    win._activity.clear(sid)  # whatever the launch paint left, this is an idle session
    check("the tracker reads the second session idle", not win._activity.is_busy(sid))
    state["before"] = win.tab_view.get_n_pages()
    got = app._mcp_archive_session(found(), {})
    check("an idle session gets the same reply", got == ARMED_REPLY, got)
    check("…and is not archived inside the call", not win.state.is_archived(sid))
    check("…its tab still open", win.tab_view.get_n_pages() == state["before"])
    global tries
    tries = 0
    GLib.timeout_add(500, wait_second_archived)
    return GLib.SOURCE_REMOVE


def wait_second_archived() -> bool:
    global tries
    tries += 1
    win = state["win"]
    sid = state["sid2"]
    if not win.state.is_archived(sid):
        if tries > 60:
            check("the idle session was archived after the beat", False, "timed out")
            return finish()
        return GLib.SOURCE_CONTINUE
    check("the idle session was archived after the beat", True)
    GLib.timeout_add(500, verify_second_closed)
    return GLib.SOURCE_REMOVE


def verify_second_closed() -> bool:
    win = state["win"]
    check(
        "the second tab closed with its archive",
        win.tab_view.get_n_pages() == state["before"] - 1,
        win.tab_view.get_n_pages(),
    )
    check("no arm is left behind", not win._archive_when_finished, win._archive_when_finished)
    return finish()


def finish() -> bool:
    # Take any shim still holding a terminal out before quitting.
    win = state.get("win")
    if win is not None:
        for i in range(win.tab_view.get_n_pages()):
            tab = win.tab_view.get_nth_page(i).get_child()
            pid = getattr(tab, "_child_pid", None)
            if pid:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except OSError:
                    pass
    app.quit()
    return GLib.SOURCE_REMOVE


GLib.timeout_add(250, stage)
app.run([])
shutil.rmtree(E2E, ignore_errors=True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED or not PASSED else 0)
