#!/usr/bin/env python3
"""End-to-end check for the start_session MCP tool — run on a dev machine.

Exercises App._mcp_start_session against a real App: a real window, a real
AdwTabView, real VTEs spawning real children. It builds on
check_background_session.py (the launch path PR) and adds the tool half — the
deferred spawn → inject → resolve dance, and the per-project-root serialization
that keeps two back-to-back spawns from racing each other's transcript.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \
        python3 scripts/check_start_session.py

None of this is reachable from pytest (tests/conftest.py blocks the GTK stack),
and the claims that matter are exactly the ones a headless unit test can't
make: that the tool never moves what the user is looking at, that it resolves
with a real session id, and that a second spawn waits for the first.

`claude` is a Python shim that draws Claude Code's idle prompt (❯ + NBSP, cursor
parked so takes_prompt says yes), captures the bracketed-paste prompt injected
into it, and then "resolves" by writing a uuid.jsonl transcript under its cwd —
which is how Collins binds a session id. The shim can't emulate the real CLI's
paste heuristic, so the multi-line-stays-literal question is the one thing here
that isn't a live proof; _bracketed_paste's own sanitizing is asserted directly
instead (the wrapper is what makes the CLI keep the newlines).

Run it behind the headless wrapper, or a window opens on the user's screen —
and this check is precisely about selection and focus, which a stray click
would falsify.
"""

import os
import re
import shutil
import signal
import sys
import tempfile

E2E = tempfile.mkdtemp(prefix="collins-startsession-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

# Isolation first: every one of these is read at import time somewhere below.
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"
# Where the shim records the prompt it was handed, so the test can read it back.
os.environ["SPAWN_LOG"] = f"{E2E}/injected.log"

TRUSTED = f"{E2E}/dev/alpha"
SHIM = f"{E2E}/bin/claude"

for path in (f"{E2E}/projects", f"{E2E}/chats", f"{E2E}/bin", TRUSTED):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")

# The child under test. It draws the idle prompt, reads the injected bracketed
# paste (capturing its body so the test can confirm the prompt survived the
# wrapper), writes a transcript keyed by its cwd — the file whose stem Collins
# reads as the session id — and then holds the terminal open like a real CLI.
_SHIM = r'''#!/usr/bin/env python3
import os, re, sys, time, uuid, pathlib
sys.stdout.write("❯ ")  # ❯ + no-break space; cursor parks right after
sys.stdout.flush()
buf = b""
while b"\x1b[201~" not in buf:
    ch = os.read(0, 1)
    if not ch:
        break
    buf += ch
m = re.search(rb"\x1b\[200~(.*)\x1b\[201~", buf, re.S)
body = (m.group(1) if m else b"").decode("utf-8", "replace")
log = os.environ.get("SPAWN_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("<<<PROMPT>>>\n" + body + "\n<<<END>>>\n")
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

from collins import i18n, mcptools, terminal, trust  # noqa: E402
from collins.app import App  # noqa: E402
from collins.sessions import worktree_project_root  # noqa: E402
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


# -- the pure sanitizing _bracketed_paste does, tested directly ---------------
# This is the half a bash/python shim can't prove live: that the wrapper keeps
# a prompt's newlines literal and can't be closed early by the prompt's text.
_wrapped = terminal._bracketed_paste("one\r\ntwo\rthree\x1b[201~four")
check("bracketed paste opens and closes once", _wrapped.count("\x1b[200~") == 1
      and _wrapped.endswith("\x1b[201~"))
check(
    "carriage returns become newlines (no mid-prompt submit)",
    "\r" not in _wrapped and "one\ntwo\nthree" in _wrapped,
    repr(_wrapped),
)
check(
    "a paste-end marker in the text can't close the wrapper early",
    _wrapped.count("\x1b[201~") == 1 and "threefour" in _wrapped,
    repr(_wrapped),
)

i18n.init(AppState().get_setting("language"))
trust.trust_dir(TRUSTED)
app = App()

PROMPT = "Do the first thing.\nThen the second thing.\nReport back when done."
ROOT = os.path.realpath(worktree_project_root(TRUSTED) or TRUSTED)
tries = 0
state: dict = {}


def stage() -> bool:
    """Wait for the window, open the caller tab everything spawns behind."""
    global tries
    tries += 1
    win = app.get_active_window()
    if win is None:
        if tries > 40:
            print("timed out waiting for the window", file=sys.stderr)
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE
    # The caller: an ordinary foreground session (an empty tab view makes the
    # background launch fall through to the foreground, which is what we want
    # here — a tab on screen for the spawn not to disturb).
    caller = win.start_background_session(TRUSTED)
    state["win"] = win
    state["caller"] = caller
    state["caller_page"] = win.tab_view.get_selected_page()
    check("caller tab is the selected one", state["caller_page"].get_child() is caller)
    GLib.timeout_add(2500, fire_tool)  # let the caller settle on screen
    return GLib.SOURCE_REMOVE


def fire_tool() -> bool:
    """Call the tool twice, same cwd, back to back — the case the per-root
    queue exists for — and pin that the second waits behind the first."""
    win = app.get_active_window()
    caller = state["caller"]
    before = win.tab_view.get_n_pages()

    first = app._mcp_start_session((win, caller), {"prompt": PROMPT, "cwd": TRUSTED})
    second = app._mcp_start_session(
        (win, caller), {"prompt": "Second sibling, go.", "cwd": TRUSTED}
    )
    check("the tool returns a deferred answer", isinstance(first, mcptools.DeferredResult))
    check("both calls deferred", isinstance(second, mcptools.DeferredResult))

    # Two enqueued for the root; only the first has spawned a tab.
    queue = app._start_session_chains.get(ROOT)
    check("both spawns queued under one root", queue is not None and len(queue) == 2,
          None if queue is None else len(queue))
    check(
        "only the first spawn opened a tab (the second waits)",
        win.tab_view.get_n_pages() == before + 1,
        win.tab_view.get_n_pages(),
    )
    check("selection didn't move to spawn", win.tab_view.get_selected_page() is state["caller_page"])

    results: dict = {}
    first.watch(lambda ok, text: results.__setitem__("first", (ok, text)))
    second.watch(lambda ok, text: results.__setitem__("second", (ok, text)))
    state["results"] = results
    state["before"] = before
    GLib.timeout_add(9000, verify)  # both spawn, inject, resolve well within this
    return GLib.SOURCE_REMOVE


def verify() -> bool:
    win = app.get_active_window()
    results = state["results"]
    check("the first call resolved", "first" in results, results)
    check("the second call resolved", "second" in results, results)

    first = results.get("first", (False, ""))
    second = results.get("second", (False, ""))
    check("first reported success", first[0], first)
    check("second reported success", second[0], second)

    ids = re.findall(r"session ([0-9a-f-]{36})", first[1] + " " + second[1])
    check("each reply carries a session id", len(ids) == 2, first[1] + " | " + second[1])
    check("the two sessions are distinct", len(set(ids)) == 2, ids)
    check(
        "the reply names the directory it started in",
        TRUSTED in first[1] and TRUSTED in second[1],
        first[1] + " | " + second[1],
    )

    # Both siblings ended up open, and the user was never moved off the caller.
    check(
        "both spawned tabs are open beside the caller",
        win.tab_view.get_n_pages() == state["before"] + 2,
        win.tab_view.get_n_pages(),
    )
    check(
        "selection never moved through the whole run",
        win.tab_view.get_selected_page() is state["caller_page"],
    )
    check("the root's queue drained", ROOT not in app._start_session_chains)

    # The prompt reached the child intact, newlines and all — the paste wrapper
    # carried it without a line submitting early.
    log = ""
    if os.path.exists(os.environ["SPAWN_LOG"]):
        with open(os.environ["SPAWN_LOG"], encoding="utf-8") as fh:
            log = fh.read()
    for line in PROMPT.split("\n"):
        check(f"the child received the line {line!r}", line in log, repr(log[-400:]))

    # Take every shim's process group out before quitting; none exit on their own.
    for i in range(win.tab_view.get_n_pages()):
        tab = win.tab_view.get_nth_page(i).get_child()
        pid = getattr(tab, "_child_pid", None)
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
