#!/usr/bin/env python3
"""End-to-end check for what archiving does with a session's worktree.

MainWindow._settle_archived_worktree runs after a single archive lands on a
stopped session: it reads the worktree the transcript records and, as the
archive_worktree setting says, leaves it, deletes it, or asks. None of that
is reachable from pytest — tests/conftest.py blocks the GTK stack, and the
ask is a real dialog on a real window — so it is checked here against a
real App and a real git repository:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \
        python3 scripts/check_archive_worktree.py

One session, never opened in a tab (so the archive lands on the tabless
path, with no CLI to spawn), filed the way the CLI files a session that
lives in a worktree, and the worktree itself cut for real under the staged
repository. The setting is walked through never, always and ask, with the
worktree put back between rounds; a last round makes the session a
background agent, which must keep its worktree whatever the setting.

Run it behind the headless wrapper, or a window opens on the user's screen.
"""

import json
import os
import subprocess
import sys
import tempfile

E2E = tempfile.mkdtemp(prefix="collins-archivewt-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

# Isolation first: every one of these is read at import time somewhere below.
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"

REPO = f"{E2E}/repo"
WORKTREE = f"{REPO}/.claude/worktrees/oasis"
BRANCH = "worktree-oasis"
SESSION = "aaaaaaaa-1111-4222-8333-444444444444"
# The CLI moves a transcript under the worktree's own project key the moment
# a session enters one.
_PROJECT = f"{E2E}/projects/" + "".join(c if c.isalnum() else "-" for c in WORKTREE)

SHIM = f"{E2E}/bin/claude"

for path in (f"{E2E}/chats", f"{E2E}/config/collins", f"{E2E}/bin", _PROJECT, REPO):
    os.makedirs(path, exist_ok=True)
# Discovery only lists sessions of an agent whose CLI is on PATH, and CI has
# no claude: a shim that is never run stands in for it (no tab opens here).
with open(SHIM, "w", encoding="utf-8") as fh:
    fh.write("#!/bin/sh\nexit 0\n")
os.chmod(SHIM, 0o755)
os.environ["PATH"] = f"{E2E}/bin:{os.environ['PATH']}"
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
with open(f"{E2E}/config/collins/state.json", "w", encoding="utf-8") as fh:
    # Titles on None so nothing runs a `-p`; the first-launch welcome
    # answered already; the gh notice waved off (CI has no gh, and its card
    # would be the visible dialog every "no dialog" check looks at); no
    # claude.ai mirroring to shrug at.
    fh.write(
        '{"settings": {"title_model": "none", "welcome_seen": true, '
        '"gh_welcome_dismissed": true, "archive_on_claude_ai": false}}'
    )


def git(cwd: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t.t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


git(REPO, "init", "-q", "-b", "main")
with open(f"{REPO}/readme.md", "w", encoding="utf-8") as fh:
    fh.write("hi\n")
git(REPO, "add", "-A")
git(REPO, "commit", "-qm", "init")
HEAD = git(REPO, "rev-parse", "HEAD")
STATE = {
    "worktreePath": WORKTREE,
    "worktreeName": "oasis",
    "worktreeBranch": BRANCH,
    "originalHeadCommit": HEAD,
}

_LINES = [
    {
        "type": "user",
        "uuid": "u1",
        "timestamp": "2026-09-05T09:00:00Z",
        "cwd": WORKTREE,
        "sessionId": SESSION,
        "message": {"role": "user", "content": "Do a thing in the worktree"},
    },
    {"type": "worktree-state", "worktreeSession": STATE},
]
with open(f"{_PROJECT}/{SESSION}.jsonl", "w", encoding="utf-8") as fh:
    for line in _LINES:
        fh.write(json.dumps(line) + "\n")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, GLib  # noqa: E402

from collins import i18n  # noqa: E402
from collins.app import App  # noqa: E402
from collins.sessions import recreate_worktree  # noqa: E402
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


def worktree_exists() -> bool:
    return os.path.isdir(WORKTREE)


def branch_exists() -> bool:
    return bool(git(REPO, "branch", "--list", BRANCH))


def put_back() -> None:
    if not worktree_exists():
        assert recreate_worktree(STATE), "could not recreate the staged worktree"


put_back()

i18n.init(AppState().get_setting("language"))
app = App()
exit_code = 1
tries = 0
state: dict = {}


def later(fn, ms: int = 2500) -> bool:
    GLib.timeout_add(ms, fn)
    return GLib.SOURCE_REMOVE


def archive() -> None:
    """The sidebar's archive action, as the row button fires it."""
    state["win"].archive_session(SESSION)


def restore() -> None:
    state["win"].store.set_archived(SESSION, False)


# -- the steps ---------------------------------------------------------------


def stage() -> bool:
    global tries
    tries += 1
    win = app.get_active_window()
    session = app.store.get_session(SESSION)
    if win is None or session is None:
        if tries > 40:  # ~10s: the store scan should long since have landed
            print("timed out waiting for the window/session", file=sys.stderr)
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE
    state["win"] = win
    check("the session is filed under its worktree", session.cwd == WORKTREE, session.cwd)
    win.state.set_setting("archive_worktree", "never")
    archive()
    return later(step_never)


def step_never() -> bool:
    win = state["win"]
    check("never: the session was archived", win.state.is_archived(SESSION))
    check("never: the worktree stays", worktree_exists())
    check("never: no dialog", win.get_visible_dialog() is None)
    restore()
    win.state.set_setting("archive_worktree", "always")
    archive()
    return later(step_always)


def step_always() -> bool:
    win = state["win"]
    check("always: the session was archived", win.state.is_archived(SESSION))
    check("always: the worktree is gone", not worktree_exists())
    check("always: the untouched branch went with it", not branch_exists())
    check("always: no dialog", win.get_visible_dialog() is None)
    put_back()
    restore()
    win.state.set_setting("archive_worktree", "ask")
    archive()
    return later(step_ask)


def step_ask() -> bool:
    win = state["win"]
    dialog = win.get_visible_dialog()
    check("ask: the session was archived", win.state.is_archived(SESSION))
    check("ask: the worktree waits on the answer", worktree_exists())
    check(
        "ask: the dialog is up",
        isinstance(dialog, Adw.AlertDialog) and WORKTREE in dialog.get_body(),
        type(dialog).__name__ if dialog else None,
    )
    if not isinstance(dialog, Adw.AlertDialog):
        return done()
    state["dialog"] = dialog
    # Keep: the dialog's suggested answer.
    dialog.set_close_response("extra")
    dialog.close()
    return later(step_kept, 800)


def step_kept() -> bool:
    win = state["win"]
    check("keep: the worktree stays", worktree_exists())
    check("keep: the dialog is gone", win.get_visible_dialog() is None)
    restore()
    archive()
    return later(step_ask_delete)


def step_ask_delete() -> bool:
    win = state["win"]
    dialog = win.get_visible_dialog()
    check("ask again: the dialog is up", isinstance(dialog, Adw.AlertDialog))
    if not isinstance(dialog, Adw.AlertDialog):
        return done()
    dialog.set_close_response("confirm")
    dialog.close()
    return later(step_deleted)


def step_deleted() -> bool:
    win = state["win"]
    check("delete: the worktree is gone", not worktree_exists())
    check("delete: the dialog is gone", win.get_visible_dialog() is None)
    # A session running on as a background agent keeps its worktree whatever
    # the setting says: it is still working in there.
    put_back()
    restore()
    win.state.set_setting("archive_worktree", "always")
    win._bg_status.background_ids.add(SESSION)
    archive()
    return later(step_background)


def step_background() -> bool:
    win = state["win"]
    check("background: the session was archived", win.state.is_archived(SESSION))
    check("background: the worktree stays", worktree_exists())
    check("background: no dialog", win.get_visible_dialog() is None)
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
