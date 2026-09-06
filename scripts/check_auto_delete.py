#!/usr/bin/env python3
# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""End-to-end check for the automatic delete of archived sessions.

Preferences → Session behavior → *Delete archived sessions after* is a
number with a unit drop-down as its suffix; each writes its setting the
moment it changes. Once a day the app trashes every session archived at
least that long ago (autodelete.py): a session archived forty days ago goes
under "1 month", one archived yesterday stays, one never archived is not
looked at, a project emptied out is kept as a header, and a sweep that ran
within the day is not run again. The row is a widget and the trash is
Gio's, so none of it is reachable from pytest — checked here against a
real App:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_auto_delete.py

The tree is staged on the home filesystem (under ~/.cache), not /tmp: GLib
refuses to trash from a tmpfs. The CLI is a shim that is never run.

Run it behind the headless wrapper, or a window opens on the user's screen.
"""

import json
import os
import shutil
import sys
import tempfile
import time

_HOME_TMP = os.path.join(os.path.expanduser("~"), ".cache", "collins-e2e")
os.makedirs(_HOME_TMP, exist_ok=True)
E2E = tempfile.mkdtemp(prefix="collins-autodelete-", dir=_HOME_TMP)
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

# Isolation first: every one of these is read at import time somewhere below.
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["COLLINS_USAGE_FIXTURE"] = f"{E2E}/usage-fixture.json"  # no usage poll, no token repair
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"
os.environ["XDG_CACHE_HOME"] = f"{E2E}/cache"
os.environ["LANG"] = "C"  # the check reads English titles

DAY = 24 * 3600
NOW = time.time()
ALPHA = f"{E2E}/alpha"  # two sessions: one archived long ago, one yesterday
BETA = f"{E2E}/beta"  # one session, archived long ago: the sweep empties it
OLD = "aaaaaaaa-1111-4222-8333-444444444444"
FRESH = "bbbbbbbb-1111-4222-8333-444444444444"
LIVE = "cccccccc-1111-4222-8333-444444444444"
LONE = "dddddddd-1111-4222-8333-444444444444"
SHIM = f"{E2E}/bin/claude"


def _project_dir(cwd: str) -> str:
    return f"{E2E}/projects/" + "".join(c if c.isalnum() else "-" for c in cwd)


for path in (
    f"{E2E}/chats",
    f"{E2E}/bin",
    f"{E2E}/config/collins",
    f"{E2E}/cache/collins",
    ALPHA,
    BETA,
    _project_dir(ALPHA),
    _project_dir(BETA),
):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
with open(f"{E2E}/usage-fixture.json", "w", encoding="utf-8") as fh:
    json.dump({"limits": [], "extra_usage": {"is_enabled": False}}, fh)
with open(SHIM, "w", encoding="utf-8") as fh:
    fh.write("#!/bin/sh\nexit 0\n")
os.chmod(SHIM, 0o755)
os.environ["PATH"] = f"{E2E}/bin:{os.environ['PATH']}"


def transcript(cwd: str, session_id: str) -> str:
    path = f"{_project_dir(cwd)}/{session_id}.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "type": "user",
                    "uuid": "u1",
                    "timestamp": "2026-07-01T09:00:00Z",
                    "cwd": cwd,
                    "sessionId": session_id,
                    "message": {"role": "user", "content": f"Prompt for {session_id[:8]}"},
                }
            )
            + "\n"
        )
    return path


PATHS = {
    OLD: transcript(ALPHA, OLD),
    FRESH: transcript(ALPHA, FRESH),
    LIVE: transcript(ALPHA, LIVE),
    LONE: transcript(BETA, LONE),
}

# Titles on None so nothing runs a `-p`; the first-launch welcome answered
# already; the gh notice waved off (CI has no gh); no claude.ai mirroring.
# The setting is on at one month, and the sweep's day already stamped two
# hours ago, so the launch's own sweep does nothing and the check drives
# the one that counts.
with open(f"{E2E}/config/collins/state.json", "w", encoding="utf-8") as fh:
    json.dump(
        {
            "archived": [OLD, FRESH, LONE],
            "archived_at": {OLD: NOW - 40 * DAY, FRESH: NOW - DAY, LONE: NOW - 40 * DAY},
            "settings": {
                "title_model": "none",
                "show_usage_panel": False,
                "welcome_seen": True,
                "gh_welcome_dismissed": True,
                "archive_on_claude_ai": False,
                "restore_last_session": False,
                "auto_delete_archived_after": 1,
                "auto_delete_archived_unit": "months",
            },
        },
        fh,
    )
with open(f"{E2E}/cache/collins/archive-sweep.json", "w", encoding="utf-8") as fh:
    json.dump({"version": 1, "swept_at": NOW - 2 * 3600}, fh)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, GLib  # noqa: E402

from collins import autodelete, i18n, prefslayout  # noqa: E402
from collins.app import App  # noqa: E402
from collins.prefs import PreferencesDialog  # noqa: E402
from collins.state import AppState  # noqa: E402

PASSED = 0
FAILED = 0
ROW_TITLE = "Delete archived sessions after"


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


def setting(key: str):
    # A fresh AppState reads the file back: what the dialog saved.
    return AppState().get_setting(key)


def settle() -> None:
    context = GLib.MainContext.default()
    for _ in range(50):
        if not context.pending():
            break
        context.iteration(False)


i18n.init(AppState().get_setting("language"))
app = App()

exit_code = 1
tries = 0
state: dict = {}


# -- the steps ---------------------------------------------------------------


def stage() -> bool:
    """Wait for the window and its scan, then open preferences."""
    global tries
    tries += 1
    win = app.get_active_window()
    if win is None or len(win.store.sessions) < 4:
        if tries > 40:  # ~10s
            print("timed out waiting for the window and its sessions", file=sys.stderr)
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE
    other = win.get_visible_dialog()
    if other is not None:
        print(f"closing a dialog presented at launch: {other}", file=sys.stderr)
        other.force_close()
    check(
        "the launch's sweep left everything alone (the day was stamped)",
        all(os.path.exists(p) for p in PATHS.values()),
    )
    dialog = PreferencesDialog(win.state, lambda: None)
    dialog.present(win)
    state.update(win=win, dialog=dialog)
    return later(step_row)


def step_row() -> bool:
    dialog = state["dialog"]
    page = dialog._page
    sessions = page.groups[prefslayout.GROUPS.index("sessions")]
    row = next((r for r in sessions.rows if r.get_title() == ROW_TITLE), None)
    check("the Session behavior group holds the row", row is not None, [r.get_title() for r in sessions.rows])
    if row is None:
        return done()
    check("it is a number row", isinstance(row, Adw.SpinRow), type(row).__name__)
    check("opening on the staged count", row.get_value() == 1, row.get_value())
    unit = dialog._auto_delete_unit
    model = unit.get_model()
    labels = [model.get_string(i) for i in range(model.get_n_items())]
    check(
        "the unit drop-down lists days, weeks, months, years",
        labels == ["Days", "Weeks", "Months", "Years"],
        labels,
    )
    check("and opens on the staged unit", unit.get_selected() == 2, unit.get_selected())
    check("the drop-down sits on the row", unit.get_ancestor(Adw.SpinRow) is row)

    row.set_value(3)
    settle()
    count = setting(autodelete.SETTING_COUNT)
    check("changing the number writes it", count == 3, count)
    unit.set_selected(1)
    settle()
    word = setting(autodelete.SETTING_UNIT)
    check("picking Weeks writes its word", word == "weeks", word)
    row.set_value(0)
    settle()
    count = setting(autodelete.SETTING_COUNT)
    check("zero is a plain zero (never)", count == 0, count)
    # Back to one month for the sweep, and the row saved it into the app's
    # own state, which the sweep reads.
    row.set_value(1)
    unit.set_selected(2)
    settle()
    check(
        "the app's state carries the pair",
        state["win"].state.get_setting(autodelete.SETTING_COUNT) == 1
        and state["win"].state.get_setting(autodelete.SETTING_UNIT) == "months",
    )
    entry = dialog._search_entry
    entry.set_text("retention")
    dialog._apply_filter()
    visible = [r.get_title() for g in page.groups if g.get_visible() for r in g.rows if r.get_visible()]
    check("the search finds the row by its words", visible == [ROW_TITLE], visible)
    entry.set_text("")
    dialog._apply_filter()
    dialog.force_close()
    return later(step_sweep, 300)


def step_sweep() -> bool:
    win = state["win"]
    # Within the day: nothing, whatever has expired.
    app._sweep_archived()
    settle()
    check("a sweep within the day trashes nothing", all(os.path.exists(p) for p in PATHS.values()))
    # A day on: the two old ones go, the fresh one and the live one stay.
    autodelete.write_record({"swept_at": NOW - 25 * 3600})
    app._sweep_archived()
    settle()
    check("the session archived forty days ago is trashed", not os.path.exists(PATHS[OLD]))
    check("and so is the lone one in the other project", not os.path.exists(PATHS[LONE]))
    check("the one archived yesterday stays", os.path.exists(PATHS[FRESH]))
    check("the one never archived stays", os.path.exists(PATHS[LIVE]))
    fresh = AppState()
    check("the trashed ids leave the archive set", not fresh.is_archived(OLD) and not fresh.is_archived(LONE))
    check("and their stamps", fresh.archived_since(OLD) is None and fresh.archived_since(LONE) is None)
    check("the fresh one keeps its stamp", fresh.archived_since(FRESH) is not None)
    record = autodelete.read_record()
    check("the day is stamped", record.get("swept_at", 0) > NOW - 60, record)
    check(
        "the emptied project is kept as a header",
        "beta" in fresh.get_virtual_projects() and "beta" in win.store.resolved_project_order,
        (fresh.get_virtual_projects(), win.store.resolved_project_order),
    )
    check("alpha is not (it still has sessions)", "alpha" not in fresh.get_virtual_projects())
    check(
        "the store no longer lists the trashed sessions",
        OLD not in win.store.sessions and LONE not in win.store.sessions,
    )
    return done()


def done() -> bool:
    global exit_code
    print(f"\n{PASSED} passed, {FAILED} failed")
    exit_code = 0 if FAILED == 0 else 1
    app.quit()
    return GLib.SOURCE_REMOVE


GLib.timeout_add(250, stage)
try:
    app.run([])
finally:
    shutil.rmtree(E2E, ignore_errors=True)
sys.exit(exit_code)
