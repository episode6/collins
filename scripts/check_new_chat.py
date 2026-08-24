#!/usr/bin/env python3
"""End-to-end check for the new-chat screen and its drafts.

A session started by hand opens onto the new-chat screen (newchatview.py)
rather than the agent's console; the first prompt is written there, and its
Send is what launches the CLI. Between those two moments the tab is a draft
(newchat.py): text on the screen, or a terminal opened beside it, is written
to state.json under a draft id, listed in the sidebar, survives the tab
closing, and comes back — text, checkbox, dock and all — when the row is
clicked. Send spends the draft and types the prompt into the CLI once it is
at its input box.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \
        python3 scripts/check_new_chat.py

`claude` is a stub that draws an idle prompt, logs the first line it is
sent, writes a transcript so the session resolves, and holds the terminal.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile

E2E = tempfile.mkdtemp(prefix="collins-newchat-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

# Isolation first: every one of these is read at import time somewhere below.
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"
os.environ["SPAWN_LOG"] = f"{E2E}/spawn.log"

PROJECT = f"{E2E}/dev/alpha"
ELSEWHERE = f"{E2E}/dev/elsewhere"
SHIM = f"{E2E}/bin/claude"
STATE_FILE = f"{E2E}/config/collins/state.json"
HISTORY_DIR = f"{E2E}/state/collins/panel_history"

for path in (f"{E2E}/projects", f"{E2E}/chats", f"{E2E}/bin", PROJECT, ELSEWHERE):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
# A git checkout, so the screen offers its worktree checkbox.
subprocess.run(["git", "init", "-q", PROJECT], check=True)

_SHIM = r'''#!/usr/bin/env python3
import os, re, sys, time, tty, uuid, pathlib
tty.setraw(0)  # as the CLI does: the submitting Return arrives as a bare \r
sys.stdout.write("❯ ")  # ❯ + no-break space; cursor parks right after
sys.stdout.flush()
buf = b""
while b"\r" not in buf:
    ch = os.read(0, 1)
    if not ch:
        buf += b"<EOF>"
        break
    buf += ch
log = os.environ.get("SPAWN_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("<<<PROMPT>>>\n" + buf.decode("utf-8", "replace") + "\n<<<END>>>\n")
cwd = os.getcwd()
enc = re.sub(r"[^A-Za-z0-9]", "-", cwd)
proj = pathlib.Path(os.environ["COLLINS_PROJECTS_DIR"]) / enc
proj.mkdir(parents=True, exist_ok=True)
sid = str(uuid.uuid4())
(proj / (sid + ".jsonl")).write_text('{"type":"summary","cwd":"%s"}\n' % cwd)
sys.stdout.write("\nworking\n")
sys.stdout.flush()
while True:
    time.sleep(3600)
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

from collins import i18n, newchat, proctree, trust  # noqa: E402
from collins.app import App  # noqa: E402
from collins.state import AppState  # noqa: E402

PASSED = 0
FAILED = 0
PROMPT = "first prompt, from the screen"


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  ok  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}  {detail}")


def saved_drafts() -> dict:
    """The drafts as they are on disk — the point is that they outlive this
    process, so the in-memory AppState is not evidence."""
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh).get("new_chat_drafts") or {}
    except (OSError, json.JSONDecodeError):
        return {}


def history_files(draft_id: str) -> list[str]:
    try:
        return sorted(name for name in os.listdir(HISTORY_DIR) if name.startswith(draft_id))
    except OSError:
        return []


i18n.init(AppState().get_setting("language"))
trust.trust_dir(PROJECT)
app = App()

tries = 0
state: dict = {}


def stage() -> bool:
    """Wait for the window, then start a session by hand — the new-chat path."""
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
    win._start_new_session(PROJECT)
    GLib.timeout_add(800, on_the_screen)
    return GLib.SOURCE_REMOVE


def on_the_screen() -> bool:
    win = state["win"]
    page = win.tab_view.get_selected_page()
    tab = page.get_child()
    state["page"], state["tab"] = page, tab
    check("a session started by hand opens onto the new-chat screen", tab.is_new_chat)
    check("…with nothing spawned behind it", tab._child_pid is None, tab._child_pid)
    check(
        "…showing the screen, not the console",
        tab._stage.get_visible_child_name() == "new-chat",
        tab._stage.get_visible_child_name(),
    )
    draft_id = win._placeholder_pages.get(page)
    state["draft_id"] = draft_id
    check("its sidebar row is keyed by a draft id from the start", newchat.is_draft_id(draft_id), draft_id)
    check("an empty screen is not a draft", not tab.new_chat_worthy() and saved_drafts() == {})
    check("the screen's composer holds the keyboard", tab._new_chat.has_focus_within())
    check("a git project offers the worktree box", tab._new_chat._worktree.get_visible())
    check("…following the project's default (off)", tab.new_chat_worktree_choice() is None)
    check("the screen is an unstarted thread", tab.unstarted_thread())

    # Typing makes it a draft; the write is debounced.
    tab._new_chat.set_text(PROMPT + "\nand a second line")
    check("text makes the screen worth keeping", tab.new_chat_worthy())
    check("…but the write waits for the debounce", saved_drafts() == {}, saved_drafts())
    GLib.timeout_add(1000, after_the_write)
    return GLib.SOURCE_REMOVE


def after_the_write() -> bool:
    win, tab, draft_id = state["win"], state["tab"], state["draft_id"]
    record = saved_drafts().get(draft_id)
    check("the draft is on disk under its id", record is not None, saved_drafts())
    if record:
        check("…with the project directory", record.get("cwd") == PROJECT, record)
        check("…and the text", record.get("text", "").startswith(PROMPT), record)
        check("…and no worktree choice while the box is untouched", "worktree" not in record, record)
    row = win.sidebar._placeholder_rows.get(draft_id)
    check(
        "the sidebar row shows the draft's first line",
        row is not None and row.get_tooltip_text() == PROMPT,
        row.get_tooltip_text() if row else None,
    )
    check("…as a live row (a tab is open on it)", row is not None and row.has_css_class("running"))

    # The worktree box, and a terminal beside the screen, are parts of the draft too.
    tab._new_chat._worktree.set_active(True)
    tab.show_panel(focus=False)
    check("a terminal opened beside the screen starts in the project", tab.panel_shells() != [])
    GLib.timeout_add(1500, after_the_panel)
    return GLib.SOURCE_REMOVE


def after_the_panel() -> bool:
    win, page, draft_id = state["win"], state["page"], state["draft_id"]
    record = saved_drafts().get(draft_id) or {}
    check("the ticked box is kept", record.get("worktree") is True, record)
    layout = record.get("layout")
    check("the dock layout is kept", isinstance(layout, dict) and layout.get("tree"), record)
    check(
        "the shell's scrollback is filed under the draft id",
        history_files(draft_id) != [],
        history_files(draft_id),
    )

    # Closing the tab keeps the draft: the row stays, without a tab.
    win.tab_view.close_page(page)
    GLib.timeout_add(500, after_the_close)
    return GLib.SOURCE_REMOVE


def after_the_close() -> bool:
    win, draft_id = state["win"], state["draft_id"]
    check("the tab is gone", win._placeholder_page(draft_id) is None)
    check("…and the draft is still on disk", draft_id in saved_drafts(), list(saved_drafts()))
    row = win.sidebar._placeholder_rows.get(draft_id)
    check("the sidebar keeps a row for it", row is not None)
    check("…dimmed, as a row with no tab", row is not None and not row.has_css_class("running"))

    # Clicking the row brings the screen back, with everything on it.
    win._on_sidebar_open_placeholder(None, draft_id)
    GLib.timeout_add(1500, after_the_reopen)
    return GLib.SOURCE_REMOVE


def after_the_reopen() -> bool:
    win, draft_id = state["win"], state["draft_id"]
    page = win._placeholder_page(draft_id)
    check("the draft reopens as a tab on the same row", page is not None)
    if page is None:
        return finish()
    tab = page.get_child()
    state["page"], state["tab"] = page, tab
    check("…on the new-chat screen", tab.is_new_chat)
    check("…with the text back", tab.new_chat_text().startswith(PROMPT), tab.new_chat_text())
    check("…the worktree box ticked", tab.new_chat_worktree_choice() is True)
    check("…and the terminal beside it again", tab.panel_shells() != [], tab.panel_shells())

    # A shell can be told to follow the session into a worktree (the offer's
    # accept), and only when idle.
    shell = tab.panel_shells()[0]
    state["shell"] = shell
    check("an idle shell follows", shell.follow_cwd(ELSEWHERE))
    GLib.timeout_add(1200, after_the_follow)
    return GLib.SOURCE_REMOVE


def after_the_follow() -> bool:
    shell = state["shell"]
    check(
        "…and is in the new directory",
        proctree.process_cwd(shell._child_pid) == ELSEWHERE,
        proctree.process_cwd(shell._child_pid),
    )
    check("a shell already there is left alone", not shell.follow_cwd(ELSEWHERE))

    # Send: the draft is spent, the console appears, the prompt is typed in.
    tab, draft_id = state["tab"], state["draft_id"]
    tab._new_chat.emit("send-requested", PROMPT, False)
    check("Send leaves the screen", not tab.is_new_chat)
    check("…for the console", tab._stage.get_visible_child_name() == "terminal")
    check("…and spends the draft", draft_id not in saved_drafts(), list(saved_drafts()))
    check("…and its panel history files", history_files(draft_id) == [], history_files(draft_id))
    check("the terminal beside it survives the flip", tab.panel_shells() != [])
    GLib.timeout_add(4000, after_the_send)
    return GLib.SOURCE_REMOVE


def after_the_send() -> bool:
    win, tab, draft_id = state["win"], state["tab"], state["draft_id"]
    log = ""
    if os.path.exists(os.environ["SPAWN_LOG"]):
        with open(os.environ["SPAWN_LOG"], encoding="utf-8") as fh:
            log = fh.read()
    check("the CLI received the prompt", PROMPT in log, repr(log[-300:]))
    check("the tab is a session now", tab.session_id is not None, tab.session_id)
    check("no draft row lingers", draft_id not in win.sidebar._placeholder_rows)

    # An empty screen closed leaves nothing behind.
    win._start_new_session(PROJECT)
    GLib.timeout_add(800, close_an_empty_screen)
    return GLib.SOURCE_REMOVE


def close_an_empty_screen() -> bool:
    win = state["win"]
    page = win.tab_view.get_selected_page()
    empty_id = win._placeholder_pages.get(page)
    check(
        "a second screen has an id of its own",
        newchat.is_draft_id(empty_id) and empty_id != state["draft_id"],
    )
    win.tab_view.close_page(page)
    GLib.timeout_add(500, lambda: (
        check("closing an empty screen writes nothing", empty_id not in saved_drafts(), saved_drafts()),
        check("…and leaves no row", empty_id not in win.sidebar._placeholder_rows),
        finish(),
    )[-1])
    return GLib.SOURCE_REMOVE


def finish() -> bool:
    win = state["win"]
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
