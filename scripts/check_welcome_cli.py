#!/usr/bin/env python3
"""End-to-end check for the welcome's CLI ask, answered by the service.

Whether there is a `claude`, where, and what a typed path is worth are the
service's to say (`service.status`'s ``cli``, `cli.check`; collins/remotecli),
because the CLI runs where the service runs. Two machines' worth of
difference is staged on one: the service's PATH has no claude and its HOME
is a scratch one whose ``~/.local/bin/claude`` is a shim, while this
window's PATH has none either and its HOME is the real one. So:

- with ``welcome_seen`` already true, the launch still opens on the
  not-found ask, because the *service* has no CLI on its PATH;
- the box is pre-filled with the scratch HOME's launcher: the service's
  known locations, not this process's;
- a typed path the service can't find wears the red cross and the button
  is Use This CLI, insensitive; the shim's path brings Continue back;
- Continue stores the path and the service puts it on its own PATH: its
  next `service.status` names the shim, the dialog is gone, and the window's
  PATH is exactly what it was (the client changes nothing of its own);
- Preferences' CLI row shows the service's verdict on the stored path, and
  clearing it takes the entry back off the service's PATH, which the row
  then reports.

The inverse case, a window that can't find claude while the service can
(the bug this replaced: the not-found ask on every launch), is
check_welcome.py's found state, whose window PATH has no claude.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_welcome_cli.py
"""

import json
import os
import sys
import tempfile

E2E = tempfile.mkdtemp(prefix="collins-welcome-cli-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

os.environ["COLLINS_DEBUG_API"] = "1"
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["COLLINS_CLAUDE_CREDENTIALS"] = f"{E2E}/credentials.json"
os.environ["COLLINS_SANDBOX_ROOT"] = f"{E2E}/sandbox"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"
os.environ["XDG_CACHE_HOME"] = f"{E2E}/cache"
os.environ["LANG"] = "C"

SERVICE_HOME = f"{E2E}/home"
SHIM = f"{SERVICE_HOME}/.local/bin/claude"
STATE_FILE = f"{E2E}/config/collins/state.json"

for path in (f"{E2E}/chats", f"{E2E}/projects", f"{E2E}/config/collins", os.path.dirname(SHIM)):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
with open(SHIM, "w", encoding="utf-8") as fh:
    fh.write("#!/bin/sh\nexit 0\n")
os.chmod(SHIM, 0o755)
# Seen already: the ask must come back anyway, as the service has no CLI.
# Nothing else here spends a token or reaches the network.
with open(STATE_FILE, "w", encoding="utf-8") as fh:
    json.dump(
        {
            "settings": {
                "welcome_seen": True,
                "gh_welcome_dismissed": True,
                "title_model": "none",
                "auto_renew_login": False,
                "refresh_prs_on_launch": False,
                "check_for_updates": False,
                "show_usage_panel": False,
            }
        },
        fh,
    )

# No claude on either PATH (a real one on the dev machine included).
os.environ["PATH"] = os.pathsep.join(
    d for d in os.environ["PATH"].split(os.pathsep) if d and not os.path.exists(os.path.join(d, "claude"))
)
WINDOW_PATH = os.environ["PATH"]

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
import e2e_service  # noqa: E402
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from collins import apilink, i18n, remotecli, welcome  # noqa: E402
from collins.app import App  # noqa: E402
from collins.prefs import PreferencesDialog  # noqa: E402

PASSED = 0
FAILED = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  ok   {label}")
    else:
        FAILED += 1
        print(f"  FAIL {label}  ({detail!r})")


def walk(widget: Gtk.Widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from walk(child)
        child = child.get_next_sibling()


def find(widget: Gtk.Widget, pred) -> Gtk.Widget | None:
    return next((w for w in walk(widget) if pred(w)), None)


def button(dialog, label: str) -> Gtk.Button | None:
    return find(dialog, lambda w: isinstance(w, Gtk.Button) and w.get_label() == label)


def saved_settings() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh).get("settings") or {}
    except OSError:
        return {}


def later(fn, ms: int = 1000) -> bool:
    GLib.timeout_add(ms, fn)
    return GLib.SOURCE_REMOVE


i18n.init("en")
# The service's HOME is the scratch one (its known locations), its login
# shell captures nothing (SHELL=/bin/false), and its PATH is this one: no
# claude anywhere it looks but the scratch ~/.local/bin, which is no PATH's.
e2e_service.start_service(env={**os.environ, "HOME": SERVICE_HOME, "SHELL": "/bin/false"})
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
    state["win"] = win
    return later(step_asked, 1500)


def step_asked() -> bool:
    dialog = state["win"].get_visible_dialog()
    check("the service said it has no claude", remotecli.missing(), remotecli.found_at())
    check(
        "welcome_seen or not, the launch opens on the CLI ask",
        isinstance(dialog, welcome.WelcomeDialog),
        type(dialog).__name__ if dialog else None,
    )
    if not isinstance(dialog, welcome.WelcomeDialog):
        return done()
    state["dialog"] = dialog
    entry = find(dialog, lambda w: isinstance(w, Adw.EntryRow))
    check("the not-found state has the path box", entry is not None)
    check("and a Quit", button(dialog, "Quit") is not None)
    if entry is None:
        return done()
    state["entry"] = entry
    check("pre-filled from the service's known locations", entry.get_text() == SHIM, entry.get_text())
    check("the Browse button is there for a local client", button(dialog, "Browse…") is not None,
          apilink.is_local())
    check("the service's verdict on it is Continue", button(dialog, "Continue") is not None
          and button(dialog, "Continue").get_sensitive())
    entry.set_text(f"{E2E}/nowhere/claude")
    return later(step_refused, 500)


def step_refused() -> bool:
    dialog = state["dialog"]
    use = button(dialog, "Use This CLI")
    check(
        "a path the service can't find is Use This CLI, insensitive",
        use is not None and not use.get_sensitive(),
        use and use.get_sensitive(),
    )
    check("no Continue for it", button(dialog, "Continue") is None)
    state["entry"].set_text(SHIM)
    return later(step_accepted, 500)


def step_accepted() -> bool:
    dialog = state["dialog"]
    cont = button(dialog, "Continue")
    check("the shim's path brings Continue back", cont is not None and cont.get_sensitive())
    if cont is None:
        return done()
    cont.emit("clicked")
    return later(step_continued, 800)


def step_continued() -> bool:
    win = state["win"]
    check("the dialog is gone", not isinstance(win.get_visible_dialog(), welcome.WelcomeDialog))
    settings = saved_settings()
    stored = settings.get("claude_cli_path")
    check("the path is stored as given", stored == SHIM, stored)
    status = apilink.call({"t": "service.status"})
    check("the service put it on its own PATH", status.get("cli") == SHIM, status.get("cli"))
    check("and the window heard so", remotecli.found_at() == SHIM, remotecli.found_at())
    check("the window's PATH is untouched", os.environ["PATH"] == WINDOW_PATH)
    prefs = PreferencesDialog(win.state, lambda: None)
    prefs.present(win)
    state["prefs"] = prefs
    return later(step_prefs, 800)


def step_prefs() -> bool:
    """Preferences' CLI row asks the same service: the stored path's
    verdict, then the box cleared, which takes it off the service's PATH."""
    prefs = state["prefs"]
    check("Preferences shows the stored path", prefs._cli_row.get_text() == SHIM, prefs._cli_row.get_text())
    reason = prefs._cli_reason.get_subtitle() or ""
    check("with the service's verdict on it", reason.startswith("Found it"), reason)
    prefs._cli_row.set_text("")
    return later(step_cleared, 800)


def step_cleared() -> bool:
    prefs = state["prefs"]
    stored = saved_settings().get("claude_cli_path")
    check("clearing the box stores the empty answer", stored == "", stored)
    status = apilink.call({"t": "service.status"})
    check("and the service took it off its PATH", status.get("cli") == "", status.get("cli"))
    reason = prefs._cli_reason.get_subtitle() or ""
    check("the row says the service's PATH has none", reason.startswith("claude isn't on PATH"), reason)
    prefs.force_close()
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
