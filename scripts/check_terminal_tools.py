#!/usr/bin/env python3
"""End-to-end check for the read_terminal / run_in_terminal MCP tools.

Exercises App._mcp_read_terminal and App._mcp_run_in_terminal against a real
App: a real window, a real PanelDock, real VTE shells running real commands.
The claims that matter are exactly the ones the GTK-free unit tests
(tests/test_mcptools.py — schemas, tailing, framing) can't make: that a run
with no panel opens one and the command actually executes in it, that a read
comes back with what the shell really printed, that a busy shell is refused
while an idle one is reused, that "all busy" opens a second tab — and that
none of it ever hands a panel shell the keyboard.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \
        python3 scripts/check_terminal_tools.py

Run it behind the headless wrapper, or a window opens on the user's screen —
and the focus claims are exactly what a stray click would falsify.

`claude` is a stub that draws an idle prompt and holds the terminal open, so
the caller tab looks like any session; the tools under test only ever touch
the panel shells beside it, which are real `$SHELL` processes.
"""

import os
import shutil
import signal
import sys
import tempfile

E2E = tempfile.mkdtemp(prefix="collins-terminaltools-")
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

_SHIM = r"""#!/usr/bin/env python3
import sys, time
sys.stdout.write("❯ ")  # the CLI's idle prompt: ❯ + no-break space
sys.stdout.flush()
while True:
    time.sleep(3600)
"""
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

tries = 0
state: dict = {}


def found():
    return state["win"], state["caller"]


def stage() -> bool:
    """Wait for the window, open the session tab whose panel the tools drive."""
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
    # foreground: an ordinary session tab on screen, panel closed.
    state["win"] = win
    state["caller"] = win.start_background_session(TRUSTED)
    GLib.timeout_add(2500, first_run)  # let the caller settle on screen
    return GLib.SOURCE_REMOVE


def first_run() -> bool:
    check(
        "read with no panel says so",
        app._mcp_read_terminal(found(), {})
        == (True, "No terminal-panel tabs are open in this session."),
        app._mcp_read_terminal(found(), {}),
    )
    got = app._mcp_run_in_terminal(found(), {"command": "echo collins-agent-was-here"})
    check("run with no panel opens one", got == (True, "Running in new Terminal 1."), got)
    caller = state["caller"]
    check("the panel came on screen", caller.panel_visible)
    GLib.timeout_add(4000, first_read)  # the shell spawns and runs the echo
    return GLib.SOURCE_REMOVE


def first_read() -> bool:
    ok, text = app._mcp_read_terminal(found(), {})
    check("read is a success", ok, text)
    check("read names the terminal, idle again", "── Terminal 1 (idle) ──" in text, text)
    check("read sees the command's output", "collins-agent-was-here" in text, text)
    check(
        "a missing number is an error naming the open ones",
        app._mcp_read_terminal(found(), {"terminal": 9})
        == (False, "No terminal numbered 9 — open: 1"),
    )
    shells = state["caller"].panel_shells()
    check("the revealed shell never took the keyboard", not shells[0].has_page_focus())
    got = app._mcp_run_in_terminal(found(), {"command": "sleep 60", "terminal": 1})
    check("an idle terminal named outright is reused", got == (True, "Running in Terminal 1."), got)
    GLib.timeout_add(2500, second_run)  # the sleep takes the foreground
    return GLib.SOURCE_REMOVE


def second_run() -> bool:
    got = app._mcp_run_in_terminal(found(), {"command": "echo nope", "terminal": 1})
    check(
        "a busy terminal is refused, not typed into",
        got
        == (
            False,
            "Terminal 1 is busy running a command — pick an idle one, or "
            "omit 'terminal' to open a new tab",
        ),
        got,
    )
    got = app._mcp_run_in_terminal(found(), {"command": "echo second-terminal"})
    check("all-busy opens a second tab", got == (True, "Running in new Terminal 2."), got)
    GLib.timeout_add(4000, verify)
    return GLib.SOURCE_REMOVE


def verify() -> bool:
    ok, text = app._mcp_read_terminal(found(), {})
    check("the busy terminal reads as running", "── Terminal 1 (command running) ──" in text, text)
    check("the new terminal reads as idle", "── Terminal 2 (idle) ──" in text, text)
    check("the second command ran in it", "second-terminal" in text, text)
    ok, text = app._mcp_read_terminal(found(), {"terminal": 2, "lines": 5})
    check("a single-terminal read filters", ok and "Terminal 1" not in text, text)
    check("…to the terminal asked for", "── Terminal 2 (idle) ──" in text, text)
    for shell in state["caller"].panel_shells():
        check(
            f"Terminal {shell.number} never holds the keyboard",
            not shell.has_page_focus(),
        )

    # Take the shim's process group out before quitting; it never exits on
    # its own. The panel shells die with their pty.
    win = state["win"]
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
