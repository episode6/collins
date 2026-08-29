#!/usr/bin/env python3
"""End-to-end check for the composer's draft stash and its persistence.

Exercises the whole loop the GTK-free unit tests can only see the ends of
(tests/test_composerkeys.py for the two rules, tests/test_state.py for the
store): a real TerminalTab stashes a draft, the window it lives in files it
under the tab's session in a real state.json, and the next composer opened
on that session takes it back out and empties the entry behind it.

Both of the tab's two-jobs calls are real ones here: a tab that stashes
before its session id lands (the ordinary case for a fresh session) has the
draft written down the moment `session-resolved` fires, and a tab whose
session already has one adopts it on the same signal.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \
        python3 scripts/check_composer_draft.py

`claude` is a stub that draws an idle prompt and holds the terminal open:
the tabs need a live child to be ordinary session tabs, and nothing here
reads the agent's screen — the composer is driven directly.
"""

import json
import os
import shutil
import signal
import sys
import tempfile

E2E = tempfile.mkdtemp(prefix="collins-draft-")
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
STATE_FILE = f"{E2E}/config/collins/state.json"

for path in (f"{E2E}/projects", f"{E2E}/chats", f"{E2E}/bin", TRUSTED):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
# The first-launch welcome (collins.welcome) is answered already: it would
# otherwise sit over the window under test.
os.makedirs(f"{E2E}/config/collins", exist_ok=True)
with open(f"{E2E}/config/collins/state.json", "w", encoding="utf-8") as fh:
    fh.write('{"settings": {"welcome_seen": true}}')

_SHIM = r"""#!/usr/bin/env python3
import sys, time
sys.stdout.write("❯ ")  # the CLI's idle prompt: ❯ + no-break space
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


def saved_drafts() -> dict:
    """The drafts as they are on disk, read back from state.json — the point
    of the exercise is that they outlive this process, so the in-memory
    AppState is not evidence."""
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh).get("session_drafts") or {}
    except (OSError, json.JSONDecodeError):
        return {}


i18n.init(AppState().get_setting("language"))
trust.trust_dir(TRUSTED)
app = App()

tries = 0
state: dict = {}


def stage() -> bool:
    """Wait for the window, then open the two session tabs the check drives."""
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
    # Two ordinary session tabs, neither of which will ever resolve a session
    # id on its own: the shim writes no transcript, so the resolve below is
    # ours to fire — which is exactly the signal the window listens on.
    state["late"] = win.start_background_session(TRUSTED)
    state["saved"] = win.start_background_session(TRUSTED)
    GLib.timeout_add(2500, stash_before_the_id)  # let the tabs settle
    return GLib.SOURCE_REMOVE


def stash_before_the_id() -> bool:
    """A draft stashed by a tab that doesn't know its session yet."""
    tab = state["late"]
    tab._stash_draft("a draft written before the id landed")
    check("an unresolved tab holds the draft", tab._composer_stash, tab._composer_stash)
    check("…and nothing is on disk for it yet", saved_drafts() == {}, saved_drafts())

    # The resolver's own two lines, in the order it runs them.
    tab.session_id = "sid-late"
    tab.emit("session-resolved", "sid-late")
    check(
        "resolving files the draft under the session",
        saved_drafts().get("sid-late") == "a draft written before the id landed",
        saved_drafts(),
    )

    # And a plain stash, now that the tab has an id to file it under.
    tab._stash_draft("a second draft, this one after")
    check(
        "a later stash replaces it rather than queueing",
        saved_drafts().get("sid-late") == "a second draft, this one after",
        saved_drafts(),
    )
    GLib.timeout_add(50, adopt_a_saved_draft)
    return GLib.SOURCE_REMOVE


def adopt_a_saved_draft() -> bool:
    """The other direction: a session whose draft was saved by an earlier
    run, taken back into the composer that opens on it."""
    win, tab = state["win"], state["saved"]
    win.state.set_session_draft("sid-saved", "what the last run was writing")
    tab.session_id = "sid-saved"
    tab.emit("session-resolved", "sid-saved")
    check("the tab adopts the saved draft", tab._composer_stash == "what the last run was writing")

    composer = tab._ensure_composer()
    tab._restore_stashed_draft(composer)
    check(
        "opening a composer puts it in the box",
        composer.peek_text() == "what the last run was writing",
        composer.peek_text(),
    )
    check("…and the stash is spent", not tab._composer_stash, tab._composer_stash)
    check("…and so is the entry on disk", "sid-saved" not in saved_drafts(), saved_drafts())

    # A box someone has already typed in is never written over, and the
    # draft waiting for an empty one stays where it is.
    tab._stash_draft("waiting for an empty box")
    composer.set_text("typed since")
    tab._restore_stashed_draft(composer)
    check("a written box is left alone", composer.peek_text() == "typed since", composer.peek_text())
    check(
        "…and the draft keeps waiting on disk",
        saved_drafts().get("sid-saved") == "waiting for an empty box",
        saved_drafts(),
    )
    GLib.timeout_add(50, save_on_the_way_out)
    return GLib.SOURCE_REMOVE


def save_on_the_way_out() -> bool:
    """The window's close-time save: the text in an open composer has never
    been through a stash, and is the draft nothing else would catch."""
    win, tab = state["win"], state["saved"]
    tab._composer_stash = ""  # the box is the only draft now
    tab._composer.set_text("half a prompt, still on screen")
    win._save_composer_draft(tab)
    check(
        "an open composer's text is saved for the session",
        saved_drafts().get("sid-saved") == "half a prompt, still on screen",
        saved_drafts(),
    )

    tab._composer.set_text("")  # as a send leaves it
    win._save_composer_draft(tab)
    check("an emptied composer clears the entry", "sid-saved" not in saved_drafts(), saved_drafts())
    check("…and leaves the other session's alone", "sid-late" in saved_drafts(), saved_drafts())

    # Deleting a session's transcript takes its draft with it.
    win._forget_transcript("sid-late")
    check("forgetting a session drops its draft", saved_drafts() == {}, saved_drafts())

    # Take the shim's process group out before quitting; it never exits on
    # its own. The panel shells die with their pty.
    for i in range(win.tab_view.get_n_pages()):
        page_tab = win.tab_view.get_nth_page(i).get_child()
        pid = getattr(page_tab, "_child_pid", None)
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
