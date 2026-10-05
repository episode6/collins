#!/usr/bin/env python3
"""Headless check that a prompt Collins types itself starts the sidebar's
pole — run on a dev machine, never by pytest.

Spawns a real TerminalTab on a `claude` shim that waits at a prompt box,
then — on the submit — announces itself busy with an ST-terminated OSC 9;4
progress hint for a few seconds and clears it. The prompt is sent through
`tab.inject_prompt`, the road the new-chat screen, the composer and the
start_session tool all take: through the session on the service to its
pty, never through the client VTE's "commit". That road has to count as
the tab's first submit: it releases the fresh spawn's startup hold
(ServiceActivity.startup_held, read through the debug probe), the agent's
own progress hint (the stream filter's Progress event on the service) then
marks the row busy, and the hint's clear brings the pole down through the
grace.

Through PR-1.8 every app write was VTE's `feed_child`, whose `commit`
told the echo gate; PR-1.9's service pty bypassed it, and every tab whose
turns were only ever sent this way sat in the startup hold for good — no
pole, no unread flag, no finished notification. This is the check that
would have caught it. The shim detail matters: the stream filter (as VTE)
parses only the ST-terminated OSC 9;4, so the shim ends its hints with
ESC \\.

Stages its own throwaway scratch tree and app id, so it runs anywhere the
e2e suite does:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_injected_prompt_pole.py

Run it behind the headless wrapper, or a window opens on the user's screen.
"""

import os
import shutil
import signal
import sys
import tempfile
import time

E2E = tempfile.mkdtemp(prefix="collins-pole-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

# Isolation first: every one of these is read at import time somewhere below.
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"
os.environ["COLLINS_DEBUG_API"] = "1"  # the e2e probe (debug.*): served only with this set

TRUSTED = f"{E2E}/dev/alpha"
SHIM = f"{E2E}/bin/claude"
WORK_S = 6  # how long the shim holds its busy hint

for path in (f"{E2E}/projects", f"{E2E}/chats", f"{E2E}/bin", TRUSTED):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")

# The child under test: mint a transcript the way the CLI does, draw the
# CLI's prompt marker (so takes_prompt reads an empty box), wait for the
# submit, then hold an OSC 9;4 busy hint for WORK_S seconds and clear it.
_SHIM = r'''#!/usr/bin/env python3
import os, pathlib, re, sys, time, tty, uuid
cwd = os.getcwd()
enc = re.sub(r"[^A-Za-z0-9]", "-", cwd)
proj = pathlib.Path(os.environ["COLLINS_PROJECTS_DIR"]) / enc
proj.mkdir(parents=True, exist_ok=True)
sid = str(uuid.uuid4())
(proj / (sid + ".jsonl")).write_text('{"type":"summary","cwd":"%s"}\n' % cwd)
tty.setraw(0)
sys.stdout.write("Welcome\r\n❯ ")  # the CLI's idle prompt: marker + no-break space, cursor after it
sys.stdout.flush()
while b"\r" not in os.read(0, 1024):
    pass
sys.stdout.write("\r\n\x1b]9;4;3;0\x1b\\")
sys.stdout.flush()
for i in range(WORK_TICKS):
    sys.stdout.write("working %d\r\n" % i)
    sys.stdout.flush()
    time.sleep(0.5)
sys.stdout.write("\x1b]9;4;0;0\x1b\\done\r\n❯ ")
sys.stdout.flush()
time.sleep(600)
'''.replace("WORK_TICKS", str(WORK_S * 2))
with open(SHIM, "w", encoding="utf-8") as fh:
    fh.write(_SHIM)
os.chmod(SHIM, 0o755)
os.environ["PATH"] = f"{E2E}/bin:{os.environ['PATH']}"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
import e2e_service  # noqa: E402
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


def cleanup(win) -> None:
    """Take the shim's process group out before quitting; its sleep would
    otherwise be reaped only by its pty closing."""
    for i in range(win.tab_view.get_n_pages()):
        tab = win.tab_view.get_nth_page(i).get_child()
        pid = tab.probe_call("child_pid") if hasattr(tab, "probe_call") else None
        if pid:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except OSError:
                pass


def bail(win, why: str) -> bool:
    global FAILED
    FAILED += 1
    print(f"FAIL  {why}", file=sys.stderr)
    if win is not None:
        cleanup(win)
    app.quit()
    return GLib.SOURCE_REMOVE


seed = AppState()
i18n.init(seed.get_setting("language"))
seed.update_settings({"gh_welcome_dismissed": True, "welcome_seen": True})
trust.trust_dir(TRUSTED)
# The service is its own process (PR-1.12b): started here, with this
# check's environment, before the app connects to it.
e2e_service.start_service()
app = App()
tries = 0
state: dict = {}


def row_busy() -> bool | None:
    item = state["win"].store.get_item(state["tab"].session_id)
    return None if item is None else bool(item.busy)


def startup_held(tab) -> bool:
    """The service's word on the fresh spawn's hold (ServiceActivity.
    startup_held, reached through the probe where the window's
    `_startup_held(page)` was read)."""
    return bool(tab.probe_call("activity.startup_held_for", tab.probe("handle")))


def stage() -> bool:
    """Wait for the window, then open the tab under test."""
    global tries
    tries += 1
    win = app.get_active_window()
    if win is None:
        if tries > 40:  # ~10s
            return bail(None, "timed out waiting for the window")
        return GLib.SOURCE_CONTINUE
    tab = win.start_background_session(TRUSTED)  # nothing open: lands foreground
    check("launch returns its tab", tab is not None)
    if tab is None:
        return bail(win, "no tab to test against")
    state["win"], state["tab"] = win, tab
    tries = 0
    GLib.timeout_add(250, wait_prompt)
    return GLib.SOURCE_REMOVE


def wait_prompt() -> bool:
    """Wait for the session id to bind, the row to exist and the shim's box
    to read as taking a prompt — the fresh spawn still in its startup hold."""
    global tries
    tries += 1
    win, tab = state["win"], state["tab"]
    if not (tab.session_id and row_busy() is not None and tab.takes_prompt()):
        if tries > 80:  # ~20s
            return bail(win, f"tab never settled: id={tab.session_id!r} row={row_busy()} "
                             f"takes_prompt={tab.takes_prompt()}")
        return GLib.SOURCE_CONTINUE
    page = win.tab_view.get_page(tab)
    state["page"] = page
    check("a fresh spawn starts in the startup hold", startup_held(tab))
    check("the row is idle before the prompt", row_busy() is False, row_busy())
    tab.inject_prompt("hello")  # the new-chat screen's, the composer's, start_session's road
    state["t0"] = time.monotonic()
    tries = 0
    GLib.timeout_add(250, wait_busy)
    return GLib.SOURCE_REMOVE


def wait_busy() -> bool:
    """The injected submit must release the hold and the shim's busy hint
    must put the pole on the row."""
    global tries
    tries += 1
    win, tab = state["win"], state["tab"]
    if not (row_busy() and not startup_held(tab)):
        if tries > 40:  # ~10s
            check("the injected prompt released the startup hold", not startup_held(tab))
            check("the row went busy on the injected prompt", bool(row_busy()), row_busy())
            cleanup(win)
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE
    check("the injected prompt released the startup hold", True)
    check("the row went busy on the injected prompt", True)
    check("the session's echo gate is armed", tab.probe("echo_gate.armed") is True)
    tries = 0
    GLib.timeout_add(500, wait_idle)
    return GLib.SOURCE_REMOVE


def wait_idle() -> bool:
    """The hint's clear brings the pole down (through the finish grace)."""
    global tries
    tries += 1
    win = state["win"]
    if row_busy():
        if tries > 2 * (WORK_S + 12):
            check("the row went idle after the hint cleared", False, "still busy")
            cleanup(win)
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE
    elapsed = time.monotonic() - state["t0"]
    check("the row went idle after the hint cleared", True)
    check("not before the shim was done", elapsed >= WORK_S, f"{elapsed:.1f}s")
    cleanup(win)
    app.quit()
    return GLib.SOURCE_REMOVE


GLib.timeout_add(250, stage)
app.run([])
shutil.rmtree(E2E, ignore_errors=True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED or not PASSED else 0)
