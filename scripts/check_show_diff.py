#!/usr/bin/env python3
# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""End-to-end check for the diff tools: show_diff, diff_context,
annotate_diff, highlight_diff and clear_diff_marks, through the socket.

Exercises the five tools against a real App the way a session does: a
real window, a real session tab whose `claude` spawns the real stdio shim
(`collins.mcp_shim`, from the `--mcp-config` file the tab was launched
with) and speaks MCP to it; the shim relays each call over the Unix socket
to the running app, whose dispatch traces the shim's pid back to the tab
and drives the tab's real GitPage over a real repository. The claims that
matter are the ones tests/test_gitloads.py and tests/test_mcptools.py
(the `what` and path rules, the schemas, the reply shapes) can't make:
that a call opens the page and the page reads the diff, that the reply
waits for the load to land and names it, that the file, line, side or
hunk the agent named is revealed in the view, that a second call reloads
the open page rather than opening a twin, that a note batch lands whole
or not at all and its cards come up under the hunk, that diff_context
reads back what the page shows, that a highlight is painted and a clear
takes both away, that a switched-off tool is refused at the door — and
that none of it ever hands the page the keyboard.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_show_diff.py

Run it behind the headless wrapper, or a window opens on the user's screen.

`claude` is a stub that draws an idle prompt, spawns the shim exactly as
the CLI would and holds the terminal open; it takes each tool call from a
request file this script drops, sends it to the shim as a JSON-RPC
`tools/call`, and writes the shim's answer back — so every reply read
here crossed stdio, the socket and the dispatch, and the tab lookup
found the caller by the shim's ancestry.
"""

import json
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
RPC = f"{E2E}/rpc"  # the request / reply files between this script and the stub

for path in (f"{E2E}/projects", f"{E2E}/chats", BIN, RPC):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
# The first-launch welcome (collins.welcome) and the gh card are answered
# already: either would otherwise sit over the window under test.
os.makedirs(f"{E2E}/config/collins", exist_ok=True)
with open(f"{E2E}/config/collins/state.json", "w", encoding="utf-8") as fh:
    fh.write('{"settings": {"welcome_seen": true, "gh_welcome_dismissed": true}}')

# The stub CLI. It draws the idle prompt (❯ + no-break space, what
# takes_prompt keys on), spawns the `collins` server named in its
# --mcp-config file with that server's command, args and env — the shim
# is then a child of the stub, a grandchild of the tab's shell, which is
# the ancestry the app's tab lookup walks — runs the MCP handshake, and
# then relays: every `<n>.req` file in RPC (a JSON-RPC method + params)
# goes to the shim's stdin as one request, and the shim's reply line comes
# back as `<n>.res`. Stdout is the terminal's: nothing but the prompt is
# written to it.
_SHIM = r"""#!/usr/bin/env python3
import json, os, subprocess, sys, time

RPC = "@RPC@"
sys.stdout.write("❯ ")
sys.stdout.flush()
argv = sys.argv[1:]
with open(argv[argv.index("--mcp-config") + 1], encoding="utf-8") as fh:
    server = json.load(fh)["mcpServers"]["collins"]
env = dict(os.environ)
env.update(server["env"])
shim = subprocess.Popen(
    [server["command"], *server["args"]],
    env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8",
)
serial = 0


def rpc(message):
    global serial
    if "id" in message:
        serial += 1
        message["id"] = serial
    shim.stdin.write(json.dumps(message) + "\n")
    shim.stdin.flush()
    if "id" not in message:
        return None
    return json.loads(shim.stdout.readline())


rpc({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {},
    "clientInfo": {"name": "check_show_diff", "version": "0"},
}})
rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
while True:
    for name in sorted(os.listdir(RPC)):
        if not name.endswith(".req"):
            continue
        path = os.path.join(RPC, name)
        with open(path, encoding="utf-8") as fh:
            request = json.load(fh)
        os.unlink(path)
        reply = rpc({"jsonrpc": "2.0", "id": 0, **request})
        tmp = os.path.join(RPC, name[:-4] + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(reply, fh)
        os.replace(tmp, os.path.join(RPC, name[:-4] + ".res"))
    time.sleep(0.05)
"""
with open(f"{BIN}/claude", "w", encoding="utf-8") as fh:
    fh.write(_SHIM.replace("@RPC@", RPC))
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
DEADLINE_S = 110  # run_e2e's per-check budget is 120 s
CALL_DEADLINE_S = 20  # a call's reply: the shim gives the app 15 s


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
serial = 0


def request(message: dict, then) -> None:
    """Hand one JSON-RPC request to the stub and run *then(reply)* with
    the shim's answer once the reply file turns up."""
    global serial
    serial += 1
    name = f"{serial:04d}"
    tmp = os.path.join(RPC, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(message, fh)
    os.replace(tmp, os.path.join(RPC, name + ".req"))
    waited = [0]

    def poll() -> bool:
        path = os.path.join(RPC, name + ".res")
        if not os.path.exists(path):
            waited[0] += 50
            if waited[0] > CALL_DEADLINE_S * 1000:
                then(None)
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE
        with open(path, encoding="utf-8") as fh:
            reply = json.load(fh)
        os.unlink(path)
        then(reply)
        return GLib.SOURCE_REMOVE

    GLib.timeout_add(50, poll)


def tool_result(reply: dict | None) -> tuple[bool, str]:
    """A tools/call reply as the (ok, text) the app's handler answered:
    the shim renders a refusal as an isError text block."""
    if reply is None:
        return False, "<no reply from the shim>"
    result = reply.get("result")
    if not isinstance(result, dict):
        return False, f"<not a tool result: {reply!r}>"
    content = result.get("content") or [{}]
    return not result.get("isError"), str(content[0].get("text", ""))


def resume(value) -> None:
    """Drive the script generator one step: it yields (tool, args) for a
    call — resumed with (ok, text) — "<list>" for tools/list (resumed with
    the tool names) or ("<wait>", ms)."""
    try:
        step = SCRIPT.send(value)
    except StopIteration:
        finish()
        return
    kind, payload = step
    if kind == "<wait>":
        GLib.timeout_add(payload, lambda: (resume(None), GLib.SOURCE_REMOVE)[1])
    elif kind == "<list>":
        request(
            {"method": "tools/list", "params": {}},
            lambda reply: resume(
                [t.get("name") for t in ((reply or {}).get("result") or {}).get("tools") or []]
            ),
        )
    else:
        request(
            {"method": "tools/call", "params": {"name": kind, "arguments": payload}},
            lambda reply: resume(tool_result(reply)),
        )


def stage() -> bool:
    """Wait for the window, open the session tab whose page the tools drive."""
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
    GLib.timeout_add(2500, lambda: (resume(None), GLib.SOURCE_REMOVE)[1])  # let the caller settle
    return GLib.SOURCE_REMOVE


def script():
    caller = state["caller"]
    check("the caller sees the repository", caller.current_agent_cwd() == REPO, caller.current_agent_cwd())
    check("no git page before the first call", caller.git_page is None)

    # -- the session's tool list, through the shim --
    names = yield "<list>", None
    check(
        "the session lists the five diff tools",
        {"show_diff", "diff_context", "annotate_diff", "highlight_diff", "clear_diff_marks"} <= set(names),
        names,
    )
    check("…among every tool in the table", set(names) == {t["name"] for t in mcptools.TOOLS}, names)

    # -- nothing but show_diff before a page is open --
    for name, args in (
        ("diff_context", {}),
        ("annotate_diff", {"notes": [{"file": "a.txt", "line": 2, "summary": "x"}]}),
        ("highlight_diff", {"marks": [{"file": "a.txt", "line": 2, "start": 0, "end": 1}]}),
        ("clear_diff_marks", {}),
    ):
        got = yield name, args
        check(f"{name} without a page names show_diff", got == (False, mcptools.PAGE_NOT_OPEN), got)
    check("still no git page", caller.git_page is None)

    # -- show_diff's refusals: the schema's, the handler's, git's --
    got = yield "show_diff", {"what": "main..feat"}
    check(
        "a range is refused before anything opens",
        got == (False, "'what' must be unstaged, staged, branch, or a commit ref: 'main..feat'"),
        got,
    )
    got = yield "show_diff", {"what": "staged", "line": 3}
    check("a line without a file is refused", got == (False, "'line' needs 'file'"), got)
    got = yield "show_diff", {"what": "staged", "file": "../outside.txt"}
    check(
        "a file outside the repository is refused",
        got == (False, "'file' must be a path inside the repository: '../outside.txt'"),
        got,
    )
    got = yield "show_diff", {"what": "staged", "hunk": 0, "file": "a.txt"}
    check("the schema refuses hunk 0 at the door", got[0] is False and "hunk" in got[1], got)
    check("still no git page", caller.git_page is None)
    got = yield "show_diff", {"what": "nope-such-ref"}
    check("an unknown ref is refused by git", got == (False, f"No commit named nope-such-ref in {REPO}"), got)
    check("…without opening the page", caller.git_page is None)

    # -- the first open --
    ok, text = yield "show_diff", {"what": "unstaged", "file": "a.txt", "line": 3}
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
    check(
        "the page shows the unstaged working tree",
        page is not None and page.shows("unstaged"),
        page and page.loaded,
    )
    check(
        "the view revealed a.txt",
        page is not None and page.diff_view.current()[0] == "a.txt",
        page and page.diff_view.current(),
    )
    check("the page is mapped (revealed)", page is not None and page.get_mapped())
    check("the page never took the keyboard", page is not None and not page.has_page_focus())
    check("the page is settled", page is not None and page.settled())
    state["page"] = page

    # -- the index --
    got = yield "show_diff", {"what": "staged", "file": "a.txt"}
    page = caller.git_page
    check("the second call reused the page", page is state["page"] and page is not None)
    check(
        "a file the diff doesn't hold is refused, naming the load",
        got == (False, "The git page loaded the staged diff, but a.txt isn't in that diff"),
        got,
    )
    check(
        "the page reloaded into the index first and shows staged",
        page.shows("staged") and page.breadcrumb_text() == "working tree · staged",
        (page.loaded, page.breadcrumb_text()),
    )
    ok, text = yield "show_diff", {"what": "staged", "file": "b.txt", "line": 40}
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

    # -- a commit --
    ok, text = yield "show_diff", {"what": "HEAD"}
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

    # -- the branch, with the files filter hiding the file first --
    # A reveal of a hidden file must clear the filter, not answer True
    # over a section nobody can see. (The entry's search-changed is
    # debounced: the call waits a beat for the words to land.)
    page.sidebar.set_filter_text("zzz")
    yield "<wait>", 400
    check("the filter hid a.txt", page.diff_view.hidden_by_filter("a.txt"), page.diff_view.file_rows())
    ok, text = yield "show_diff", {"what": "branch", "file": "/" + os.path.relpath(REPO, "/") + "/a.txt"}
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

    # -- hunk and side --
    got = yield "show_diff", {"what": "branch", "file": "a.txt", "hunk": 2}
    check(
        "a hunk the file doesn't have is refused by count",
        got == (False, "The git page loaded the branch diff, but a.txt has 1 hunk in that diff, not 2"),
        got,
    )
    got = yield "show_diff", {"what": "branch", "file": "a.txt", "hunk": 1, "line": 2}
    check(
        "line and hunk together are refused",
        got == (False, "'line' and 'hunk' are exclusive: give one"),
        got,
    )
    got = yield "show_diff", {"what": "branch", "side": "old"}
    check("a side without a file is refused", got == (False, "'side' needs 'file'"), got)
    got = yield "show_diff", {"what": "branch", "file": "a.txt", "hunk": 1}
    check("a hunk address reveals", got[0] and got[1].endswith("Revealed a.txt, hunk 1 of 1."), got)
    check(
        "the view is on a.txt's hunk 1",
        page.diff_view.current()[:2] == ("a.txt", 0),
        page.diff_view.current(),
    )
    got = yield "show_diff", {"what": "branch", "file": "a.txt", "side": "old", "line": 1}
    check(
        "an old-side line reveals and the reply says which side",
        got[0] and got[1].endswith("Revealed a.txt, line 1 (old side): hunk 1 of 1."),
        got,
    )

    # -- the switch: a tool the user turned off is refused at the door --
    app.state.set_setting(mcptools.tool_setting_key("annotate_diff"), False)
    got = yield "annotate_diff", {"notes": [{"file": "a.txt", "line": 2, "summary": "x"}]}
    check("a switched-off tool is refused", got == (False, mcptools.disabled_error("annotate_diff")), got)
    names = yield "<list>", None
    check("…and leaves the session's list", "annotate_diff" not in names and "highlight_diff" in names, names)
    app.state.set_setting(mcptools.tool_setting_key("annotate_diff"), True)
    check("…nothing landed", page.notes() == [], page.notes())

    # -- the other four tools on the open page (the branch diff: a.txt
    # with one hunk, `+two` at new line 2) --
    ok, text = yield "diff_context", {}
    check("diff_context answers on a settled page", ok, text)
    context = json.loads(text)
    check(
        "…naming the load",
        context["loaded"] == "branch" and context["breadcrumb"].startswith("feat vs main"),
        context,
    )
    check(
        "…the current file and hunk",
        context["current"]["file"] == "a.txt" and context["current"]["hunk"] == 1,
        context,
    )
    check(
        "…and the files with their hunks",
        [(f["path"], [h["hunk"] for h in f["hunks"]], f["hunks"][0]["new"]) for f in context["files"]]
        == [("a.txt", [1], [1, 2])],
        context["files"],
    )
    check("no patch or notes unless asked", "patch" not in context["files"][0] and "notes" not in context)

    got = yield "annotate_diff", {"notes": []}
    check("the schema refuses an empty batch", got[0] is False and "notes" in got[1], got)
    got = yield "annotate_diff", {
        "notes": [
            {"file": "a.txt", "line": 2, "summary": "fine"},
            {"file": "a.txt", "line": 99, "summary": "not in a hunk"},
        ]
    }
    check(
        "a batch with one bad address lands nothing and names it",
        got == (False, "No notes added: line 99 (new) of a.txt is not in a hunk of the loaded diff"),
        got,
    )
    check("…nothing landed", page.notes() == [] and page.diff_view.note_rows("a.txt", 0) == [], page.notes())
    got = yield "annotate_diff", {
        "notes": [
            {"file": "a.txt", "line": 2, "summary": "Note one", "rationale": "because"},
            {"file": "a.txt", "hunk": 1, "side": "old", "summary": "Note two", "author": "reviewer"},
        ],
        "focus": True,
    }
    check("a good batch lands and the reply lists the ids", got == (True, "Added 2 notes: n1, n2."), got)
    rows = page.diff_view.note_rows("a.txt", 0)
    check(
        "the cards are under the hunk, as the agent's",
        [(r[0], r[1], r[2], r[3], r[4]) for r in rows]
        == [("n1", "agent", "new", 2, "Note one"), ("n2", "agent", "old", 1, "Note two")],
        rows,
    )
    check("focus never took the keyboard", not page.has_page_focus())

    # The agent's shell has left the repository since show_diff: a file
    # still resolves against the repository the page shows, not the cwd.
    real_cwd = caller.current_agent_cwd
    caller.current_agent_cwd = lambda: E2E
    try:
        got = yield "annotate_diff", {"notes": [{"file": "a.txt", "line": 2, "summary": "From elsewhere"}]}
    finally:
        caller.current_agent_cwd = real_cwd
    check("a note from a shell outside the repository lands against the page's root", got == (True, "Added 1 note: n3."), got)
    got = yield "clear_diff_marks", {"notes": True, "file": "a.txt"}
    check("…and clears the same way", got == (True, "Cleared 3 notes from a.txt."), got)
    got = yield "annotate_diff", {
        "notes": [
            {"file": "a.txt", "line": 2, "summary": "Note one", "rationale": "because"},
            {"file": "a.txt", "hunk": 1, "side": "old", "summary": "Note two", "author": "reviewer"},
        ],
    }
    check("the two notes are back for the reads below", got == (True, "Added 2 notes: n4, n5."), got)

    ok, text = yield "diff_context", {"notes": True, "patch": True, "files": True}
    context = json.loads(text)
    check(
        "diff_context lists the notes",
        [(n["id"], n["source"], n["line"], n.get("rationale"), n.get("author")) for n in context["notes"]]
        == [("n4", "agent", 2, "because", None), ("n5", "agent", 1, None, "reviewer")],
        context.get("notes"),
    )
    check("…and the patch when asked", "+two\n" in context["files"][0].get("patch", ""), context["files"][0])

    got = yield "highlight_diff", {
        "marks": [{"file": "a.txt", "line": 2, "start": 0, "end": 3, "tone": "warning"}]
    }
    check("a highlight lands", got == (True, "Added 1 highlight."), got)
    check(
        "…painted on the hunk's view",
        page.diff_view.highlight_rows("a.txt", 0) == [(1, 0, 3, "warning")],
        page.diff_view.highlight_rows("a.txt", 0),
    )
    got = yield "highlight_diff", {"marks": [{"file": "a.txt", "line": 2, "start": 0, "end": 30}]}
    check("a range past the line is refused", got[0] is False and "range [0, 30)" in got[1], got)
    check("…and lands nothing", len(page.highlights()) == 1, page.highlights())
    ok, text = yield "diff_context", {"notes": True, "files": False}
    context = json.loads(text)
    check(
        "diff_context lists the highlight",
        [(h["id"], h["line"], h["start"], h["end"], h["tone"]) for h in context["highlights"]]
        == [("h6", 2, 0, 3, "warning")],  # the store's serial is shared: n1..n5, h6
        context.get("highlights"),
    )
    check("…without the files when told not to", "files" not in context, list(context))

    got = yield "clear_diff_marks", {"file": "a.txt", "notes": True}
    check("clearing the notes of a file counts them", got == (True, "Cleared 2 notes from a.txt."), got)
    check("…the highlight stays", len(page.highlights()) == 1 and page.notes() == [], page.highlights())
    got = yield "clear_diff_marks", {"notes": False, "highlights": False}
    check("both flags false is refused, not 'Cleared .'", got == (False, mcptools.CLEAR_NOTHING), got)
    check("…and clears nothing", len(page.highlights()) == 1, page.highlights())
    got = yield "clear_diff_marks", {"notes": False}
    check("'notes': false alone clears the highlights", got == (True, "Cleared 1 highlight."), got)
    check("…the page is bare", page.highlights() == [] and page.diff_view.highlight_rows("a.txt", 0) == [])
    got = yield "clear_diff_marks", {}
    check("clearing everything counts both", got == (True, "Cleared 0 notes and 0 highlights."), got)
    check("the page still hasn't the keyboard", not page.has_page_focus())


SCRIPT = script()


def finish() -> None:
    # Take the stub's process group (the stub and its shim) out before
    # quitting; it never exits on its own.
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


def deadline() -> bool:
    check("the check finished within its deadline", False, f"{DEADLINE_S} s")
    print(f"\n{PASSED} passed, {FAILED} failed")
    os._exit(1)


GLib.timeout_add(250, stage)
GLib.timeout_add_seconds(DEADLINE_S, deadline)
app.run([])
shutil.rmtree(E2E, ignore_errors=True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED or not PASSED else 0)
