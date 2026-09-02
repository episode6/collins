#!/usr/bin/env python3
# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""End-to-end check for the show_diff MCP tool.

Exercises App._mcp_show_diff against a real App: a real window, a real
session tab, a real GitPage in the tab's dock, and the stand-in `hunk` from
check_git_page.py on PATH (its `session navigate` records the target it was
given and refuses a file outside FAKE_HUNK_FILES). The claims that matter
are the ones tests/test_hunkctl.py (argv, the reply, the path rules) can't
make: that a call opens the page and the page spawns hunk, that the deferred
reply waits for the load to land and carries hunk's own word for it, that
the navigate goes out with the file and line the agent named, that a second
call reloads the open page rather than opening a twin, that a commit ref is
resolved before anything is opened, that hunk's refusal of a file comes back
verbatim — and that none of it ever hands the page the keyboard.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_show_diff.py

Run it behind the headless wrapper, or a window opens on the user's screen.

`claude` is a stub that draws an idle prompt and holds the terminal open,
so the caller tab looks like any session.
"""

import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile

E2E = tempfile.mkdtemp(prefix="collins-showdiff-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

# Isolation first: every one of these is read at import time somewhere below.
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"

BIN = f"{E2E}/bin"
# The shim names the repository it "shows" as <dirname of its state file>/repo.
STATE = f"{E2E}/hunk-state.json"
REPO = f"{E2E}/repo"
os.environ["FAKE_HUNK_STATE"] = STATE

for path in (f"{E2E}/projects", f"{E2E}/chats", BIN):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
# The first-launch welcome (collins.welcome) and the gh card are answered
# already: either would otherwise sit over the window under test.
os.makedirs(f"{E2E}/config/collins", exist_ok=True)
with open(f"{E2E}/config/collins/state.json", "w", encoding="utf-8") as fh:
    fh.write('{"settings": {"welcome_seen": true, "gh_welcome_dismissed": true}}')

_SHIM = r"""#!/usr/bin/env python3
import sys, time
sys.stdout.write("❯ ")  # the CLI's idle prompt: ❯ + no-break space
sys.stdout.flush()
while True:
    time.sleep(3600)
"""
with open(f"{BIN}/claude", "w", encoding="utf-8") as fh:
    fh.write(_SHIM)
os.chmod(f"{BIN}/claude", 0o755)
os.environ["PATH"] = f"{BIN}:{os.environ['PATH']}"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
import check_git_page as gp  # noqa: E402  (the fake hunk, the repo builder)
from gi.repository import GLib  # noqa: E402

from collins import i18n, mcptools, trust  # noqa: E402
from collins.app import App  # noqa: E402
from collins.state import AppState  # noqa: E402

HUNK = f"{BIN}/hunk"
with open(HUNK, "w", encoding="utf-8") as fh:
    fh.write(gp.FAKE_HUNK)
os.chmod(HUNK, os.stat(HUNK).st_mode | stat.S_IXUSR)

# main / feat / base at one commit ("first"), then a.txt edited in the tree
# so the unstaged diff has a file in it.
gp.make_repo(E2E)
with open(os.path.join(REPO, "a.txt"), "w", encoding="utf-8") as fh:
    fh.write("one\ntwo\n")
SHA = gp.head_sha(REPO)

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


def read_state() -> dict:
    return gp.read_state(STATE)


i18n.init(AppState().get_setting("language"))
trust.trust_dir(REPO)
app = App()

tries = 0
state: dict = {}


def found():
    return state["win"], state["caller"]


def call(args: dict, then) -> None:
    """One tool call; *then(ok, text)* runs when the reply is in — at once
    for a synchronous refusal, later for a deferred one."""
    result = app._mcp_show_diff(found(), args)
    if isinstance(result, mcptools.DeferredResult):
        result.watch(then)
    else:
        then(*result)


def stage() -> bool:
    """Wait for the window, open the session tab whose page the tool drives."""
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
    state["caller"] = win.start_background_session(REPO)
    GLib.timeout_add(2500, refusals)  # let the caller settle on screen
    return GLib.SOURCE_REMOVE


def refusals() -> bool:
    caller = state["caller"]
    check("the caller sees the repository", caller.current_agent_cwd() == REPO, caller.current_agent_cwd())
    check("no git page before the first call", caller.git_page is None)
    got = app._mcp_show_diff(found(), {"what": "main..feat"})
    check("a range is refused before anything opens", got == (
        False, "'what' must be unstaged, staged, branch, or a commit ref: 'main..feat'"
    ), got)
    got = app._mcp_show_diff(found(), {"what": "staged", "line": 3})
    check("a line without a file is refused", got == (False, "'line' needs 'file'"), got)
    got = app._mcp_show_diff(found(), {"what": "staged", "file": "../outside.txt"})
    check(
        "a file outside the repository is refused",
        got == (False, "'file' must be a path inside the repository: '../outside.txt'"),
        got,
    )
    check("still no git page", caller.git_page is None)
    call({"what": "nope-such-ref"}, no_such_commit)
    return GLib.SOURCE_REMOVE


def no_such_commit(ok: bool, text: str) -> None:
    check(
        "an unknown ref is refused by git",
        (ok, text) == (False, f"No commit named nope-such-ref in {REPO}"),
        text,
    )
    check("…without opening the page", state["caller"].git_page is None)
    call({"what": "unstaged", "file": "a.txt", "line": 2}, unstaged_landed)


def unstaged_landed(ok: bool, text: str) -> None:
    caller = state["caller"]
    page = caller.git_page
    check("the first call opened the git page", page is not None)
    check("the call succeeded", ok, text)
    lines = text.split("\n")
    check(
        "the reply names the load and the session",
        lines[0] == "Loaded working tree · unstaged in the session's git page (hunk session fake-session-1).",
        lines[0],
    )
    check("the reply names the spot", lines[1] == "Navigated the viewer to a.txt, line 2.", lines[1])
    check(
        "the reply points at hunk session from the shell",
        "`hunk session <command> fake-session-1 …`" in lines[2],
        lines[2],
    )
    hunk_state = read_state()
    check("hunk spawned into the unstaged working tree", hunk_state.get("args") == [], hunk_state)
    check(
        "the navigate went out with the file and new-side line",
        hunk_state.get("navigate") == {"file": "a.txt", "target": "--new-line", "value": "2"},
        hunk_state.get("navigate"),
    )
    check("the page is mapped (revealed)", page is not None and page.get_mapped())
    check("the page never took the keyboard", page is not None and not page.has_page_focus())
    check("the page is settled", page is not None and page.settled())
    state["page"] = page
    os.environ["FAKE_HUNK_FILES"] = "a.txt"
    call({"what": "staged", "file": "b.txt"}, staged_refused)


def staged_refused(ok: bool, text: str) -> None:
    del os.environ["FAKE_HUNK_FILES"]
    caller = state["caller"]
    page = caller.git_page
    check("the second call reused the page", page is state["page"] and page is not None)
    check(
        "a file hunk has no diff for comes back as hunk's own refusal",
        (ok, text) == (
            False,
            "The git page loaded the staged diff, but hunk couldn't move to b.txt: "
            "No diff file matches b.txt.",
        ),
        (ok, text),
    )
    hunk_state = read_state()
    check("the page reloaded into the index first", hunk_state.get("args") == ["--staged"], hunk_state)
    check(
        "the page shows staged",
        page.shows("staged") and page.breadcrumb_text() == "working tree · staged",
        page.breadcrumb_text(),
    )
    call({"what": "HEAD"}, commit_landed)


def commit_landed(ok: bool, text: str) -> None:
    page = state["caller"].git_page
    check("a commit ref loads", ok, text)
    first = text.split("\n")[0]
    check(
        "the reply names the commit by sha and subject",
        first == f"Loaded {SHA[:7]} first in the session's git page (hunk session fake-session-1).",
        first,
    )
    hunk_state = read_state()
    check(
        "hunk was reloaded into `show <sha>` (resolved, not the ref as written)",
        hunk_state.get("args") == ["show", SHA],
        hunk_state,
    )
    check("no navigate without a file", hunk_state.get("navigate", {}).get("file") == "a.txt")
    check("the page shows the commit", page.shows({"show": SHA}), page.loaded)
    check("the page still hasn't the keyboard", not page.has_page_focus())
    call({"what": "branch", "file": "/" + os.path.relpath(REPO, "/") + "/a.txt"}, branch_landed)


def branch_landed(ok: bool, text: str) -> None:
    page = state["caller"].git_page
    check("the branch diff loads", ok, text)
    first = text.split("\n")[0]
    check(
        "the reply names the branch against its parent",
        first.startswith("Loaded feat vs main in the session's git page"),
        first,
    )
    hunk_state = read_state()
    check("hunk was reloaded into main...HEAD", hunk_state.get("args") == ["main...HEAD"], hunk_state)
    check(
        "an absolute path inside the repository navigates by its repo-relative name, first hunk",
        hunk_state.get("navigate") == {"file": "a.txt", "target": "--hunk", "value": "1"},
        hunk_state.get("navigate"),
    )
    check("the page still hasn't the keyboard", not page.has_page_focus())
    finish()


def finish() -> None:
    # Take the shim's process group out before quitting; it never exits on
    # its own. hunk goes down with the app's shutdown (gitpage.shutdown_all).
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


GLib.timeout_add(250, stage)
app.run([])
leftover = subprocess.run(["pgrep", "-f", HUNK], capture_output=True, text=True).stdout.split()
check("no fake hunk outlived the app", not leftover, leftover)
for pid in leftover:
    try:
        os.kill(int(pid), signal.SIGKILL)
    except OSError:
        pass
shutil.rmtree(E2E, ignore_errors=True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED or not PASSED else 0)
