#!/usr/bin/env python3
# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""End-to-end check for the show_diff MCP tool.

Exercises App._mcp_show_diff against a real App: a real window, a real
session tab, a real GitPage in the tab's dock over a real repository. The
claims that matter are the ones tests/test_gitloads.py (the `what` and
path rules) can't make: that a call opens the page and the page reads the
diff, that the deferred reply waits for the load to land and names it,
that the file and line the agent named are revealed in the view, that a
second call reloads the open page rather than opening a twin, that a
commit ref is resolved before anything is opened, that a file the diff
doesn't hold is refused — and that none of it ever hands the page the
keyboard.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_show_diff.py

Run it behind the headless wrapper, or a window opens on the user's screen.

`claude` is a stub that draws an idle prompt and holds the terminal open,
so the caller tab looks like any session.
"""

import os
import shutil
import signal
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
REPO = f"{E2E}/repo"

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
import check_git_page as gp  # noqa: E402  (the repo builder)
from gi.repository import GLib  # noqa: E402

from collins import i18n, mcptools, trust  # noqa: E402
from collins.app import App  # noqa: E402
from collins.state import AppState  # noqa: E402

# main / feat / base at one commit ("first"); feat then gets a commit of
# its own ("second", a.txt grows a line) so the branch diff holds a.txt;
# then a.txt is edited in the tree so the unstaged diff has a file in it,
# and b.txt staged so the index holds a file the working tree's diff
# doesn't.
gp.make_repo(E2E)
with open(os.path.join(REPO, "a.txt"), "w", encoding="utf-8") as fh:
    fh.write("one\ntwo\n")
gp.git(REPO, "commit", "-qam", "second")
SHA = gp.head_sha(REPO)
with open(os.path.join(REPO, "a.txt"), "w", encoding="utf-8") as fh:
    fh.write("one\ntwo\nthree\n")
with open(os.path.join(REPO, "b.txt"), "w", encoding="utf-8") as fh:
    fh.write("bee\n")
gp.git(REPO, "add", "b.txt")

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
    call({"what": "unstaged", "file": "a.txt", "line": 3}, unstaged_landed)


def unstaged_landed(ok: bool, text: str) -> None:
    caller = state["caller"]
    page = caller.git_page
    check("the first call opened the git page", page is not None)
    check("the call succeeded", ok, text)
    lines = text.split("\n")
    check(
        "the reply names the load",
        lines[0] == "Loaded working tree · unstaged in the session's git page.",
        lines[0],
    )
    check(
        "the reply names the spot, the side and the hunk landed on",
        len(lines) > 1 and lines[1] == "Revealed a.txt, line 3 (new side): hunk 1 of 1.",
        lines,
    )
    check("line 3 is a changed line: no nearest-hunk note", len(lines) == 2, lines)
    check("the page shows the unstaged working tree", page is not None and page.shows("unstaged"), page and page.loaded)
    check(
        "the view revealed a.txt",
        page is not None and page.diff_view.current()[0] == "a.txt",
        page and page.diff_view.current(),
    )
    check("the page is mapped (revealed)", page is not None and page.get_mapped())
    check("the page never took the keyboard", page is not None and not page.has_page_focus())
    check("the page is settled", page is not None and page.settled())
    state["page"] = page
    call({"what": "staged", "file": "a.txt"}, staged_refused)


def staged_refused(ok: bool, text: str) -> None:
    caller = state["caller"]
    page = caller.git_page
    check("the second call reused the page", page is state["page"] and page is not None)
    check(
        "a file the diff doesn't hold is refused, naming the load",
        (ok, text) == (False, "The git page loaded the staged diff, but a.txt isn't in that diff"),
        (ok, text),
    )
    check(
        "the page reloaded into the index first and shows staged",
        page.shows("staged") and page.breadcrumb_text() == "working tree · staged",
        (page.loaded, page.breadcrumb_text()),
    )
    call({"what": "staged", "file": "b.txt", "line": 40}, staged_landed)


def staged_landed(ok: bool, text: str) -> None:
    page = state["caller"].git_page
    check("a file in the index reveals", ok, text)
    lines = text.split("\n")
    check(
        "the reply names the file and line",
        len(lines) > 1 and lines[1] == "Revealed b.txt, line 40 (new side): hunk 1 of 1.",
        lines,
    )
    check(
        "a line no hunk holds lands on the nearest hunk, and the reply says so",
        len(lines) == 3 and lines[2].startswith("Line 40 (new side) isn't in a changed region"),
        lines,
    )
    check("the view is on b.txt", page.diff_view.current()[0] == "b.txt", page.diff_view.current())
    call({"what": "HEAD"}, commit_landed)


def commit_landed(ok: bool, text: str) -> None:
    page = state["caller"].git_page
    check("a commit ref loads", ok, text)
    first = text.split("\n")[0]
    check(
        "the reply names the commit by sha and subject",
        first == f"Loaded {SHA[:7]} second in the session's git page.",
        first,
    )
    check(
        "the page shows `show <sha>` (resolved, not the ref as written)",
        page.shows({"show": SHA}),
        page.loaded,
    )
    check("no reveal line without a file", "\n" not in text, text)
    check("the page still hasn't the keyboard", not page.has_page_focus())
    # The files filter hides a.txt before the next call names it: a reveal
    # of a hidden file must clear the filter, not answer True over a
    # section nobody can see. (The entry's search-changed is debounced:
    # the call waits a beat for the words to land.)
    page.sidebar.set_filter_text("zzz")
    GLib.timeout_add(400, filtered_then_branch)


def filtered_then_branch() -> bool:
    page = state["caller"].git_page
    check("the filter hid a.txt", page.diff_view.hidden_by_filter("a.txt"), page.diff_view.file_rows())
    call({"what": "branch", "file": "/" + os.path.relpath(REPO, "/") + "/a.txt"}, branch_landed)
    return GLib.SOURCE_REMOVE


def branch_landed(ok: bool, text: str) -> None:
    page = state["caller"].git_page
    check("the branch diff loads", ok, text)
    check(
        "revealing a file the filter hid cleared the filter",
        page.sidebar.filter_text == "" and not page.diff_view.hidden_by_filter("a.txt")
        and any(p == "a.txt" and shown for p, _k, shown in page.diff_view.file_rows()),
        (page.sidebar.filter_text, page.diff_view.file_rows()),
    )
    first = text.split("\n")[0]
    check(
        "the reply names the branch against its parent",
        first.startswith("Loaded feat vs main in the session's git page"),
        first,
    )
    check("the page shows the branch diff", page.shows("branch"), page.loaded)
    check(
        "an absolute path inside the repository reveals by its repo-relative name",
        "Revealed a.txt: hunk 1 of 1." in text and page.diff_view.current()[0] == "a.txt",
        (text, page.diff_view.current()),
    )
    check("the page still hasn't the keyboard", not page.has_page_focus())
    call({"what": "branch", "file": "a.txt", "hunk": 2}, hunk_refused)


def hunk_refused(ok: bool, text: str) -> None:
    check(
        "a hunk the file doesn't have is refused by count",
        (ok, text) == (False, "The git page loaded the branch diff, but a.txt has 1 hunk in that diff, not 2"),
        (ok, text),
    )
    got = app._mcp_show_diff(found(), {"what": "branch", "file": "a.txt", "hunk": 1, "line": 2})
    check("line and hunk together are refused", got == (False, "'line' and 'hunk' are exclusive: give one"), got)
    got = app._mcp_show_diff(found(), {"what": "branch", "side": "old"})
    check("a side without a file is refused", got == (False, "'side' needs 'file'"), got)
    call({"what": "branch", "file": "a.txt", "hunk": 1}, hunk_landed)


def hunk_landed(ok: bool, text: str) -> None:
    page = state["caller"].git_page
    check("a hunk address reveals", ok and text.endswith("Revealed a.txt, hunk 1 of 1."), text)
    check("the view is on a.txt's hunk 1", page.diff_view.current()[:2] == ("a.txt", 0), page.diff_view.current())
    call({"what": "branch", "file": "a.txt", "side": "old", "line": 1}, old_side_landed)


def old_side_landed(ok: bool, text: str) -> None:
    check(
        "an old-side line reveals and the reply says which side",
        ok and text.endswith("Revealed a.txt, line 1 (old side): hunk 1 of 1."),
        text,
    )
    tools()


def tools() -> None:
    """The other four tools on the open page (the branch diff: a.txt with
    one hunk, `+two` at new line 2): annotate, read back, highlight, clear
    — a bad batch landing nothing — and every one of them refused, naming
    show_diff, when no page is open."""
    import json
    from types import SimpleNamespace

    page = state["caller"].git_page
    nowhere = (None, SimpleNamespace(git_page=None))
    for name in ("diff_context", "annotate_diff", "highlight_diff", "clear_diff_marks"):
        args = {
            "annotate_diff": {"notes": [{"file": "a.txt", "line": 2, "summary": "x"}]},
            "highlight_diff": {"marks": [{"file": "a.txt", "line": 2, "start": 0, "end": 1}]},
        }.get(name, {})
        got = getattr(app, f"_mcp_{name}")(nowhere, args)
        check(f"{name} without a page names show_diff", got == (False, mcptools.PAGE_NOT_OPEN), got)

    got = app._mcp_diff_context(found(), {})
    check("diff_context answers at once on a settled page", isinstance(got, tuple) and got[0], got)
    context = json.loads(got[1])
    check("…naming the load", context["loaded"] == "branch" and context["breadcrumb"].startswith("feat vs main"), context)
    check("…the current file and hunk", context["current"]["file"] == "a.txt" and context["current"]["hunk"] == 1, context)
    check(
        "…and the files with their hunks",
        [(f["path"], [h["hunk"] for h in f["hunks"]], f["hunks"][0]["new"]) for f in context["files"]]
        == [("a.txt", [1], [1, 2])],
        context["files"],
    )
    check("no patch or notes unless asked", "patch" not in context["files"][0] and "notes" not in context)

    got = app._mcp_annotate_diff(
        found(),
        {
            "notes": [
                {"file": "a.txt", "line": 2, "summary": "fine"},
                {"file": "a.txt", "line": 99, "summary": "not in a hunk"},
            ]
        },
    )
    check(
        "a batch with one bad address lands nothing and names it",
        got == (False, "No notes added: line 99 (new) of a.txt is not in a hunk of the loaded diff"),
        got,
    )
    check("…nothing landed", page.notes() == [] and page.diff_view.note_rows("a.txt", 0) == [], page.notes())
    got = app._mcp_annotate_diff(
        found(),
        {
            "notes": [
                {"file": "a.txt", "line": 2, "summary": "Note one", "rationale": "because"},
                {"file": "a.txt", "hunk": 1, "side": "old", "summary": "Note two", "author": "reviewer"},
            ],
            "focus": True,
        },
    )
    check("a good batch lands and the reply lists the ids", got == (True, "Added 2 notes: n1, n2."), got)
    rows = page.diff_view.note_rows("a.txt", 0)
    check(
        "the cards are under the hunk, as the agent's",
        [(r[0], r[1], r[2], r[3], r[4]) for r in rows]
        == [("n1", "agent", "new", 2, "Note one"), ("n2", "agent", "old", 1, "Note two")],
        rows,
    )
    check("focus never took the keyboard", not page.has_page_focus())

    got = app._mcp_diff_context(found(), {"notes": True, "patch": True, "files": True})
    context = json.loads(got[1])
    check(
        "diff_context lists the notes",
        [(n["id"], n["source"], n["line"], n.get("rationale"), n.get("author")) for n in context["notes"]]
        == [("n1", "agent", 2, "because", None), ("n2", "agent", 1, None, "reviewer")],
        context.get("notes"),
    )
    check("…and the patch when asked", "+two\n" in context["files"][0].get("patch", ""), context["files"][0])

    got = app._mcp_highlight_diff(
        found(), {"marks": [{"file": "a.txt", "line": 2, "start": 0, "end": 3, "tone": "warning"}]}
    )
    check("a highlight lands", got == (True, "Added 1 highlight."), got)
    check(
        "…painted on the hunk's view",
        page.diff_view.highlight_rows("a.txt", 0) == [(1, 0, 3, "warning")],
        page.diff_view.highlight_rows("a.txt", 0),
    )
    got = app._mcp_highlight_diff(found(), {"marks": [{"file": "a.txt", "line": 2, "start": 0, "end": 30}]})
    check("a range past the line is refused", got[0] is False and "range [0, 30)" in got[1], got)
    check("…and lands nothing", len(page.highlights()) == 1, page.highlights())
    got = app._mcp_diff_context(found(), {"notes": True, "files": False})
    context = json.loads(got[1])
    check(
        "diff_context lists the highlight",
        [(h["id"], h["line"], h["start"], h["end"], h["tone"]) for h in context["highlights"]]
        == [("h3", 2, 0, 3, "warning")],  # the store's serial is shared: n1, n2, h3
        context.get("highlights"),
    )
    check("…without the files when told not to", "files" not in context, list(context))

    got = app._mcp_clear_diff_marks(found(), {"file": "a.txt", "notes": True})
    check("clearing the notes of a file counts them", got == (True, "Cleared 2 notes from a.txt."), got)
    check("…the highlight stays", len(page.highlights()) == 1 and page.notes() == [], page.highlights())
    got = app._mcp_clear_diff_marks(found(), {})
    check("clearing everything counts both", got == (True, "Cleared 0 notes and 1 highlight."), got)
    check("…the page is bare", page.highlights() == [] and page.diff_view.highlight_rows("a.txt", 0) == [])
    check("the page still hasn't the keyboard", not page.has_page_focus())
    finish()


def finish() -> None:
    # Take the shim's process group out before quitting; it never exits on
    # its own.
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
shutil.rmtree(E2E, ignore_errors=True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED or not PASSED else 0)
