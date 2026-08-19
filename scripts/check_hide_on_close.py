#!/usr/bin/env python3
"""End-to-end check for hide-on-close — run on a dev machine.

Exercises the quit_with_running_sessions = "hide" branch against a real App:
a real window, a real VTE with a real child, a real close-request. The two
claims that matter are exactly the ones that risk killing sessions and that
no GTK-free unit test can make:

- **Hiding keeps the session running.** The close hides the window instead
  of destroying it; the child stays alive and un-reaped, VTE keeps parsing
  its output with nothing on screen, and present() brings the window back
  mapped with the same tab. The tab is held by a weakref as well as a pid
  check — a widget that hasn't finalized keeps its child alive, so a pid
  check alone can pass for the wrong reason.
- **Hiding saves what a close would have saved.** A crash or power cut while
  hidden must not lose state, so the moment the window hides, a *fresh*
  AppState (what a relaunch would read from disk) must already see
  last_active_session, the panel layout, and the window geometry.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \
        python3 scripts/check_hide_on_close.py

`claude` is a shim that writes a uuid.jsonl transcript under its cwd — which
is how Collins binds the session id that last_active_session and the panel
layout are keyed by — and then prints a tick counter forever, so "VTE kept
reading while hidden" is a number that has to advance, not a guess.

Run it behind the headless wrapper, or a window opens on the user's screen.
"""

import gc
import os
import re
import shutil
import signal
import sys
import tempfile
import weakref

E2E = tempfile.mkdtemp(prefix="collins-hide-")
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

# The child under test: resolve a session id the way the real CLI does (a
# transcript keyed by cwd appears under the projects dir), then tick forever.
_SHIM = r'''#!/usr/bin/env python3
import os, pathlib, re, sys, time, uuid
cwd = os.getcwd()
enc = re.sub(r"[^A-Za-z0-9]", "-", cwd)
proj = pathlib.Path(os.environ["COLLINS_PROJECTS_DIR"]) / enc
proj.mkdir(parents=True, exist_ok=True)
sid = str(uuid.uuid4())
(proj / (sid + ".jsonl")).write_text('{"type":"summary","cwd":"%s"}\n' % cwd)
i = 0
while True:
    sys.stdout.write("TICK %d\n" % i)
    sys.stdout.flush()
    i += 1
    time.sleep(0.25)
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
from gi.repository import GLib, Vte  # noqa: E402

from collins import i18n, trust  # noqa: E402
from collins.app import App  # noqa: E402
from collins.state import AppState  # noqa: E402
from collins.window import MainWindow  # noqa: E402

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


def screen_text(tab) -> str:
    """Everything the terminal has drawn, unmapped or not."""
    columns = tab.terminal.get_column_count()
    _, cursor_row = tab.terminal.get_cursor_position()
    screen = tab.terminal.get_text_range_format(
        Vte.Format.TEXT, 0, 0, cursor_row + tab.terminal.get_row_count(), columns
    )
    text = screen[0] if isinstance(screen, tuple) else screen
    return text or ""


def last_tick(tab) -> int:
    ticks = re.findall(r"TICK (\d+)", screen_text(tab))
    return int(ticks[-1]) if ticks else -1


def cleanup(win) -> None:
    """Take every shim's process group out before quitting; the tick loop
    would otherwise be reaped only by its pty closing."""
    for i in range(win.tab_view.get_n_pages()):
        tab = win.tab_view.get_nth_page(i).get_child()
        pid = getattr(tab, "_child_pid", None)
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
# Seeded before the App reads the same file: the behavior under test, and no
# gh-welcome notice — on a runner with gh signed out that dialog lands over
# the window, and a window close then dismisses the dialog instead of
# reaching close-request at all (Adw closes the topmost dialog first).
seed.update_settings(
    {"quit_with_running_sessions": "hide", "gh_welcome_dismissed": True}
)
trust.trust_dir(TRUSTED)
app = App()
tries = 0
state: dict = {}


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
    state["win"] = win
    state["tab"] = tab
    state["tab_ref"] = weakref.ref(tab)
    tries = 0
    GLib.timeout_add(250, settle)
    return GLib.SOURCE_REMOVE


def settle() -> bool:
    """Wait for the session id to bind and the tab to count as busy — the
    hide branch is gated on both being true for real."""
    global tries
    tries += 1
    win, tab = state["win"], state["tab"]
    if not (tab.session_id and win._busy_tab_count() > 0):
        if tries > 60:  # ~15s
            return bail(
                win,
                f"session never became busy (sid={tab.session_id!r}, "
                f"busy={win._busy_tab_count()})",
            )
        return GLib.SOURCE_CONTINUE
    # A panel shell gives the crash-safety half a panel layout to persist.
    tab.open_panel_shell()
    GLib.timeout_add(1500, hide)
    return GLib.SOURCE_REMOVE


def hide() -> bool:
    """The close that must not close."""
    win, tab = state["win"], state["tab"]
    state["sid"] = tab.session_id
    state["shell_pid"] = tab._child_pid
    state["tick_before"] = last_tick(tab)
    check("shim ticked before the hide", state["tick_before"] >= 0)
    win.close()
    check("window is hidden, not destroyed", not win.get_visible())
    check("window object survives in get_windows", win in app.get_windows())
    check("tab count unchanged", win.tab_view.get_n_pages() == 1)
    check("session still counts busy while hidden", win._busy_tab_count() > 0)

    # -- crash safety: a relaunch right now would find everything saved -----
    fresh = AppState()
    check(
        "last_active_session persisted on hide",
        fresh.get_setting("last_active_session") == state["sid"],
        fresh.get_setting("last_active_session"),
    )
    check(
        "panel layout persisted on hide",
        fresh.get_panel_layout(state["sid"]) is not None,
    )
    # Presence in the persisted dict, not get_setting — the default answers
    # that even when nothing was ever written.
    check(
        "window geometry persisted on hide",
        "window_maximized" in fresh.settings,
        sorted(k for k in fresh.settings if k.startswith("window_")),
    )
    GLib.timeout_add(2000, verify_hidden)
    return GLib.SOURCE_REMOVE


def verify_hidden() -> bool:
    """Two seconds hidden: the child lives, un-reaped, and VTE kept reading."""
    win, tab = state["win"], state["tab"]
    pid = state["shell_pid"]
    alive = False
    procstate = "?"
    try:
        os.kill(pid, 0)
        with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
            procstate = fh.read().rsplit(")", 1)[1].split()[0]
        alive = procstate != "Z"
    except (OSError, IndexError):
        pass
    check("child is alive and un-reaped after 2s hidden", alive, procstate)
    gc.collect()
    check("tab widget never finalized", state["tab_ref"]() is not None)
    tick_now = last_tick(tab)
    check(
        "VTE kept parsing output while hidden",
        tick_now > state["tick_before"],
        (state["tick_before"], tick_now),
    )
    state["tick_hidden"] = tick_now
    win.present()
    GLib.timeout_add(1000, verify_presented)
    return GLib.SOURCE_REMOVE


def verify_presented() -> bool:
    """present() is the whole restore path: mapped, same tab, still ticking."""
    win, tab = state["win"], state["tab"]
    check("window is visible again", win.get_visible())
    check("terminal is mapped again", tab.terminal.get_mapped())
    check("same tab is still selected", win.tab_view.get_selected_page().get_child() is tab)
    check(
        "output still flowing after the restore",
        last_tick(tab) >= state["tick_hidden"],
        (state["tick_hidden"], last_tick(tab)),
    )
    cleanup(win)
    app.quit()
    return GLib.SOURCE_REMOVE


GLib.timeout_add(250, stage)
app.run([])
shutil.rmtree(E2E, ignore_errors=True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED or not PASSED else 0)
