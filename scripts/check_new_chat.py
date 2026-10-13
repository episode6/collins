#!/usr/bin/env python3
"""End-to-end check for the new-chat screen and its drafts.

A session started by hand opens onto the new-chat screen (newchatview.py)
rather than the agent's console; the first prompt is written there, and its
Send is what launches the CLI. Between those two moments the tab is a draft
(newchat.py): text on the screen, or a terminal opened beside it, is written
to state.json under a draft id, listed in the sidebar, survives the tab
closing, and comes back — text, checkbox, model and effort picks, dock and
all — when the row is clicked. Send spends the draft, launches the CLI with
the picked model as its --model and the picked effort as its --effort, and
types the prompt in once it is at its input box;
with nothing in the box it reads Empty Session and launches the CLI with no
prompt at all.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \
        python3 scripts/check_new_chat.py

`claude` is a stub that draws an idle prompt, logs its arguments and the
first line it is sent, writes a transcript so the session resolves, and
holds the terminal.
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
os.environ["COLLINS_DEBUG_API"] = "1"  # the e2e probe (debug.*): served only with this set
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
# The first-launch welcome (collins.welcome) is answered already: it would
# otherwise sit over the window under test.
os.makedirs(f"{E2E}/config/collins", exist_ok=True)
with open(f"{E2E}/config/collins/state.json", "w", encoding="utf-8") as fh:
    fh.write('{"settings": {"welcome_seen": true}}')
# A git checkout, so the screen offers its worktree checkbox.
subprocess.run(["git", "init", "-q", PROJECT], check=True)

_SHIM = r'''#!/usr/bin/env python3
import os, re, sys, time, tty, uuid, pathlib
tty.setraw(0)  # as the CLI does: the submitting Return arrives as a bare \r
log = os.environ.get("SPAWN_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("<<<ARGV>>>\n" + " ".join(sys.argv[1:]) + "\n<<<END>>>\n")
sys.stdout.write("❯ ")  # ❯ + no-break space; cursor parks right after
sys.stdout.flush()
buf = b""
while b"\r" not in buf:
    ch = os.read(0, 1)
    if not ch:
        buf += b"<EOF>"
        break
    buf += ch
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
import e2e_service  # noqa: E402
from gi.repository import GLib  # noqa: E402

from collins import checkouts, i18n, newchat, proctree, trust  # noqa: E402
from collins.app import App  # noqa: E402
from collins.newchatview import NewChatView  # noqa: E402
from collins.state import AppState  # noqa: E402

PASSED = 0
FAILED = 0
PROMPT = "first prompt, from the screen"
MODEL = "sonnet"  # the pick: an alias the CLI's --model takes
EFFORT = "low"  # the effort pick: a level the CLI's --effort takes


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
# The service is its own process (PR-1.12b): started here, with this
# check's environment, before the app connects to it.
e2e_service.start_service()
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


def a_hidden_sandbox_box_keeps_the_draft_choice() -> None:
    """A draft reopened before the bwrap probe's verdict lands has no
    Sandboxed box to show yet: its choice must survive the wait, and the
    verdict landing must not put the project's default over it. Standalone
    screens, because whether this machine can build a box decides what the
    tab under test shows."""

    def screen(default: bool) -> NewChatView:
        return NewChatView(
            PROJECT,
            lambda: None,
            lambda _path: None,
            lambda _message: None,
            worktree_default=False,
            is_git=True,
            pick_model=False,
            pick_effort=False,
            sandbox_default=default,
            sandbox_available=False,
        )

    view = screen(default=False)
    view.set_sandbox_choice(True)
    check("a draft's Sandboxed choice is kept while the box is hidden", view.sandbox_choice() is True)
    check("…without launching boxed off a hidden box", view.sandbox() is False)
    view.set_sandbox_available(True, False)
    check(
        "…and the verdict landing late doesn't put the project's default over it",
        (view.sandbox(), view.sandbox_choice()) == (True, True),
        (view.sandbox(), view.sandbox_choice()),
    )
    view = screen(default=True)
    view.set_sandbox_choice(False)
    view.set_sandbox_available(True, True)
    check(
        "…nor tick the box a draft left clear in a sandboxed project",
        (view.sandbox(), view.sandbox_choice()) == (False, False),
        (view.sandbox(), view.sandbox_choice()),
    )


def a_send_before_the_checkout_answer_keeps_the_worktree_wish() -> None:
    """The worktree box is hidden until the service says the directory is
    a checkout (`checkouts.ask`). A Send that beats the answer carries the
    hidden box's wish, which the window holds to the answer; a known
    non-checkout has no wish at all."""

    def screen(default: bool) -> NewChatView:
        return NewChatView(
            PROJECT,
            lambda: None,
            lambda _path: None,
            lambda _message: None,
            worktree_default=default,
            is_git=None,
            pick_model=False,
            pick_effort=False,
            sandbox_default=False,
            sandbox_available=False,
        )

    view = screen(default=True)
    check("no worktree box before the checkout answer", not view._worktree.get_visible())
    check("…but a Send then carries the project's default-on wish", view.worktree() is True)
    check("…and the draft keeps no choice the user didn't make", view.worktree_choice() is None)
    view.set_is_git(False)
    check("…no wish once the directory is known to be no checkout", view.worktree() is False)
    view = screen(default=False)
    check("a default-off project has no wish before the answer", view.worktree() is False)
    view.set_worktree_choice(True)
    check("a draft's worktree choice is kept through the wait", view.worktree_choice() is True)
    view.set_is_git(True)
    check(
        "…and shows ticked when the answer lands",
        (view._worktree.get_visible(), view.worktree(), view.worktree_choice()) == (True, True, True),
    )


def on_the_screen() -> bool:
    a_hidden_sandbox_box_keeps_the_draft_choice()
    a_send_before_the_checkout_answer_keeps_the_worktree_wish()
    win = state["win"]
    page = win.tab_view.get_selected_page()
    tab = page.get_child()
    state["page"], state["tab"] = page, tab
    check("a session started by hand opens onto the new-chat screen", tab.is_new_chat)
    check("…with nothing spawned behind it", tab.probe_call("child_pid") is None, tab.probe_call("child_pid"))
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
    check("the model picker is in the composer's send row", tab._new_chat.composer._model_btn is not None)
    check("…standing on the CLI's default", tab.new_chat_model() == "", tab.new_chat_model())
    check("the effort picker is beside it", tab._new_chat.composer._effort_btn is not None)
    check("…standing on the CLI's default too", tab.new_chat_effort() == "", tab.new_chat_effort())
    check(
        "the worktree box wears its short label",
        tab._new_chat._worktree.get_label() == i18n._("New git worktree"),
        tab._new_chat._worktree.get_label(),
    )
    check("the screen is an unstarted thread", tab.unstarted_thread())

    # The editor's "Add to chat" has no agent to type at here: the mention
    # lands in the screen's own composer, relative to the project.
    tab.add_file_to_chat(os.path.join(PROJECT, "notes.md"), 2, 4)
    check(
        "add to chat lands a mention in the screen's composer",
        tab.new_chat_text() == "@notes.md#L2-4 ",
        repr(tab.new_chat_text()),
    )

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
        check("…nor a model while the picker stands on the default", "model" not in record, record)
        check("…nor an effort, likewise", "effort" not in record, record)
    row = win.sidebar._placeholder_rows.get(draft_id)
    check(
        "the sidebar row shows the draft's first line",
        row is not None and row.get_tooltip_text() == PROMPT,
        row.get_tooltip_text() if row else None,
    )
    check("…as a live row (a tab is open on it)", row is not None and row.has_css_class("running"))

    # The worktree box, the model pick, and a terminal beside the screen are
    # parts of the draft too.
    tab._new_chat._worktree.set_active(True)
    tab._new_chat.set_model(MODEL)
    check("a pick names the model on the button", tab._new_chat.composer._model_btn.get_label() != "Model")
    tab._new_chat.set_effort(EFFORT)
    check(
        "a pick names the effort on its button",
        tab._new_chat.composer._effort_btn.get_label() == i18n._("Low"),
        tab._new_chat.composer._effort_btn.get_label(),
    )
    tab.show_panel(focus=False)
    check("a terminal opened beside the screen starts in the project", tab.panel_shells() != [])
    GLib.timeout_add(1500, after_the_panel)
    return GLib.SOURCE_REMOVE


def after_the_panel() -> bool:
    win, page, draft_id = state["win"], state["page"], state["draft_id"]
    record = saved_drafts().get(draft_id) or {}
    check("the ticked box is kept", record.get("worktree") is True, record)
    check("the model pick is kept", record.get("model") == MODEL, record)
    check("the effort pick is kept", record.get("effort") == EFFORT, record)
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
    check("…the model pick back", tab.new_chat_model() == MODEL, tab.new_chat_model())
    check("…the effort pick back", tab.new_chat_effort() == EFFORT, tab.new_chat_effort())
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
    tab._new_chat.emit("send-requested", PROMPT, False, False, MODEL, EFFORT)
    check("Send leaves the screen", not tab.is_new_chat)
    check(
        "…launching on the picked model",
        tab.launch_options is not None and tab.launch_options.model == MODEL,
        tab.launch_options,
    )
    check(
        "…at the picked effort",
        tab.launch_options is not None and tab.launch_options.effort == EFFORT,
        tab.launch_options,
    )
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
    check("the CLI was passed the picked model", f"--model {MODEL}" in log, repr(log[:300]))
    check("…and the picked effort", f"--effort {EFFORT}" in log, repr(log[:300]))
    check("the CLI received the prompt", PROMPT in log, repr(log[-300:]))
    check("the tab is a session now", tab.session_id is not None, tab.session_id)
    check("no draft row lingers", draft_id not in win.sidebar._placeholder_rows)

    # An empty box's Send is an empty session.
    win._start_new_session(PROJECT)
    GLib.timeout_add(800, send_an_empty_screen)
    return GLib.SOURCE_REMOVE


def send_an_empty_screen() -> bool:
    win = state["win"]
    tab = win.tab_view.get_selected_page().get_child()
    view = tab._new_chat
    label = view.composer.send_label
    empty, full = i18n._("Empty Session"), i18n._("Send")
    check("an empty box's Send reads Empty Session", label() == empty, label())
    view.set_text("a prompt")
    check("…and Send once there is text", label() == full, label())
    view.set_text("   ")
    check("…whitespace counting as empty", label() == empty, label())
    view.composer._send.emit("clicked")  # the button itself: the real path
    check("Empty Session leaves the screen", not tab.is_new_chat)
    check("…for the console", tab._stage.get_visible_child_name() == "terminal")
    check("…with no prompt waiting to be typed", tab.probe("new_chat_prompt") is None)
    GLib.timeout_add(3000, after_the_empty_send)
    return GLib.SOURCE_REMOVE


def after_the_empty_send() -> bool:
    win = state["win"]
    log = ""
    if os.path.exists(os.environ["SPAWN_LOG"]):
        with open(os.environ["SPAWN_LOG"], encoding="utf-8") as fh:
            log = fh.read()
    check("the CLI was launched a second time", log.count("<<<ARGV>>>") == 2, repr(log[-300:]))
    check("…and sent no prompt", log.count("<<<PROMPT>>>") == 1, repr(log[-300:]))

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
        hold_the_answer(),
    )[-1])
    return GLib.SOURCE_REMOVE


# -- a Send that beats the checkout answer --------------------------------------------------
#
# Whether the folder is a checkout is the service's answer (`checkouts.
# ask_answer`), and against a local service it lands long before anyone can
# press Send. Here the answer is held back, so the window's half of the rule
# is driven: the Send waits, the screen waits with it, and what happens then
# is the wish and the answer — a worktree, a plain launch, or no launch at
# all when the service could not be asked.

_pending_asks: list = []
_real_ask_answer = checkouts.ask_answer


def _held_ask_answer(cwd, then, timeout=checkouts.ANSWER_TIMEOUT_S):
    _pending_asks.append(then)


def answer_the_asks(answer) -> int:
    waiting, _pending_asks[:] = list(_pending_asks), []
    for then in waiting:
        then(answer)
    return len(waiting)


def spawn_log() -> str:
    try:
        with open(os.environ["SPAWN_LOG"], encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def launches() -> int:
    return spawn_log().count("<<<ARGV>>>")


def worktree_launches() -> int:
    argvs = [block.split("\n<<<END>>>")[0] for block in spawn_log().split("<<<ARGV>>>\n")[1:]]
    return sum(1 for argv in argvs if "-w" in argv.split())


def draft_texts() -> list:
    return [record.get("text") for record in saved_drafts().values()]


def selected():
    win = state["win"]
    page = win.tab_view.get_selected_page()
    return win, page, page.get_child()


def hold_the_answer() -> bool:
    checkouts.ask_answer = _held_ask_answer
    state["launches"] = launches()
    # A project whose new sessions use a worktree: the box starts ticked,
    # hidden until the answer.
    state["win"]._start_new_session(PROJECT, worktree=True)
    GLib.timeout_add(800, send_before_the_answer)
    return GLib.SOURCE_REMOVE


def send_before_the_answer() -> bool:
    win, page, tab = selected()
    view = tab._new_chat
    check("the checkout question is out and unanswered", len(_pending_asks) == 1, len(_pending_asks))
    check("…so there is no worktree box yet", not view._worktree.get_visible())
    check("…and the screen's wish is the project's default (on)", view.worktree() is True)
    view.set_text("FIRST prompt, at the Send")
    view.composer._send.emit("clicked")
    check("a Send before the answer is held: still the new-chat screen", tab.is_new_chat)
    check("…the page in the window's held set", page in win._new_chat_sends_held)
    check("…nothing spawned", tab.probe_call("child_pid") is None and launches() == state["launches"])
    check("…and the screen waits with it, insensitive", tab.new_chat_held and not view.get_sensitive())
    check("…down to the Send button", not view.composer._send.is_sensitive())
    # Enter again (the key path emits the same signal as the button).
    view.composer.emit("send-requested", "SECOND prompt, pressed in the wait")
    check(
        "a second Send in the wait launches nothing and waits on nothing more",
        tab.is_new_chat and len(tab._checkout_waiters) == 1 and len(_pending_asks) == 1,
        (len(tab._checkout_waiters), len(_pending_asks)),
    )
    answer_the_asks(True)
    check("the answer launches: off the screen", not tab.is_new_chat)
    check(
        "…in a worktree, as the hidden box wished",
        tab.launch_options is not None and tab.launch_options.worktree is True,
        tab.launch_options,
    )
    check("…and the held set is empty", not win._new_chat_sends_held)
    GLib.timeout_add(4000, after_the_held_send)
    return GLib.SOURCE_REMOVE


def after_the_held_send() -> bool:
    log = spawn_log()
    check("the held Send launched once", launches() == state["launches"] + 1, launches())
    check("…with the worktree flag", worktree_launches() == 1, worktree_launches())
    check("…and the first prompt, the one Send was pressed on", "FIRST prompt" in log, repr(log[-300:]))
    check("…never the one pressed in the wait", "SECOND prompt" not in log)
    state["launches"] = launches()

    # The tab closed while its Send is held: the answer launches nothing.
    state["win"]._start_new_session(PROJECT, worktree=True)
    GLib.timeout_add(800, close_a_held_send)
    return GLib.SOURCE_REMOVE


def close_a_held_send() -> bool:
    win, page, tab = selected()
    state["closed"] = tab
    tab._new_chat.set_text("closed in the wait")
    tab._new_chat.composer._send.emit("clicked")
    check("a second held Send", tab.is_new_chat and page in win._new_chat_sends_held)
    win.tab_view.close_page(page)
    GLib.timeout_add(1200, after_the_held_close)
    return GLib.SOURCE_REMOVE


def after_the_held_close() -> bool:
    win = state["win"]
    check("closing the page calls the hold off", not win._new_chat_sends_held)
    check("the answer was still owed", answer_the_asks(True) == 1)
    check("…and a closed tab launches nothing on it", launches() == state["launches"], launches())
    check("…its draft kept", "closed in the wait" in draft_texts(), draft_texts())

    # The service cannot be asked: no launch, the screen and its text back.
    win._start_new_session(PROJECT, worktree=True)
    GLib.timeout_add(800, send_and_fail_the_answer)
    return GLib.SOURCE_REMOVE


def send_and_fail_the_answer() -> bool:
    win, page, tab = selected()
    view = tab._new_chat
    view.set_text("asked for a worktree")
    view.composer._send.emit("clicked")
    check("a third held Send", tab.is_new_chat and tab.new_chat_held)
    answer_the_asks(None)  # a refusal, no link, or nothing in ANSWER_TIMEOUT_S
    stayed = tab.is_new_chat and launches() == state["launches"]
    check("an answer that could not be had launches nothing", stayed)
    check("…never plain, outside the worktree that was asked for", tab.probe_call("child_pid") is None)
    check("…the hold is over", page not in win._new_chat_sends_held)
    check("…the screen is usable again", not tab.new_chat_held and view.composer._send.is_sensitive())
    check("…with its text", view.text() == "asked for a worktree", repr(view.text()))
    still_open = tab._is_git_checkout is None and view.worktree() is True
    check("…and the question still open, the wish kept", still_open)
    # Send again: a fresh question, and this time the folder is no checkout.
    view.composer._send.emit("clicked")
    check("the next Send asks again", len(_pending_asks) == 1 and tab.new_chat_held, len(_pending_asks))
    answer_the_asks(False)
    check("no checkout: the Send launches", not tab.is_new_chat)
    check(
        "…plain (the flag means nothing there)",
        tab.launch_options is not None and tab.launch_options.worktree is False,
        tab.launch_options,
    )
    GLib.timeout_add(4000, after_the_plain_send)
    return GLib.SOURCE_REMOVE


def after_the_plain_send() -> bool:
    check("that was one more launch", launches() == state["launches"] + 1, launches())
    check("…without the worktree flag", worktree_launches() == 1, worktree_launches())
    checkouts.ask_answer = _real_ask_answer
    return finish()


def finish() -> bool:
    win = state["win"]
    # Take the shim's process group out before quitting; it never exits on
    # its own. The panel shells die with their pty.
    for i in range(win.tab_view.get_n_pages()):
        page_tab = win.tab_view.get_nth_page(i).get_child()
        probe = getattr(page_tab, "probe_call", None)
        pid = probe("child_pid") if probe is not None else None
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
