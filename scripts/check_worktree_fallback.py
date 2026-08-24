#!/usr/bin/env python3
"""End-to-end check for the worktree-launch fallback — dev machine.

`claude -w` cuts its worktree before it starts a session, and when it can't
(workspace trust it has no record of, a repository it can't resolve a base
branch in) it prints one line and exits, leaving the tab at a shell prompt
with nothing in it. TerminalTab is supposed to notice that and start the
session again without the worktree, and Window is supposed to make the trust
half of it not happen in the first place. None of that is reachable from
pytest — tests/conftest.py blocks the GTK stack, and the whole thing is a
real shell, a real spawn and a real screen — so it is checked here, against a
real App:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \
        python3 scripts/check_worktree_fallback.py

Two projects, and a `claude` shim standing in for the CLI: in one it refuses
the worktree the way the real CLI does, in the other it starts and then prints
the same error text as ordinary output — an agent talking about this very
fallback, which must not restart a session that is running fine.

Run it behind the headless wrapper, or a window opens on the user's screen.
"""

import json
import os
import sys
import tempfile

E2E = tempfile.mkdtemp(prefix="collins-wtfallback-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

# Isolation first: every one of these is read at import time somewhere below.
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"

FAIL_CWD = f"{E2E}/refuses"  # the shim won't cut a worktree here
OK_CWD = f"{E2E}/works"  # …and here it will
LOG = f"{E2E}/launches.log"  # one line per shim run: cwd, then its argv
SHIM = f"{E2E}/bin/claude"

for path in (f"{E2E}/chats", f"{E2E}/bin", f"{E2E}/config/collins"):
    os.makedirs(path, exist_ok=True)
# A .git is all the launch path looks for before it will pass the worktree
# flag on; the shim never runs git, so an empty directory is enough.
for path in (FAIL_CWD, OK_CWD):
    os.makedirs(f"{path}/.git", exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
with open(f"{E2E}/config/collins/state.json", "w", encoding="utf-8") as fh:
    fh.write('{"settings": {"auto_title_sessions": false}}')

# The CLI the tab spawns. Every run is logged, so the check can see exactly
# which command lines the tab typed; what it does then is the scene:
#   -w in the refusing project -> the CLI's own error, and exit
#   -w anywhere else           -> an idle prompt, plus the error text as
#                                 output (the false positive to survive)
#   no -w                      -> an idle prompt
with open(SHIM, "w", encoding="utf-8") as fh:
    fh.write(
        "#!/usr/bin/env python3\n"
        "import os, sys, time\n"
        f"log, refuses = {LOG!r}, {FAIL_CWD!r}\n"
        "cwd = os.getcwd()\n"
        "with open(log, 'a') as fh:\n"
        "    fh.write(cwd + '\\t' + ' '.join(sys.argv[1:]) + '\\n')\n"
        "worktree = '-w' in sys.argv[1:]\n"
        "if worktree and cwd == refuses:\n"
        "    sys.stderr.write('Error creating worktree: Workspace trust not yet accepted. '\n"
        "                     'Run `claude` once in this directory and accept the trust '\n"
        "                     'dialog, then retry with --worktree.\\n')\n"
        "    sys.exit(1)\n"
        "if worktree:\n"
        "    sys.stdout.write('Error creating worktree: is what the CLI would say.\\n')\n"
        "sys.stdout.write('\\u276f  ')\n"
        "sys.stdout.flush()\n"
        "while True:\n"
        "    time.sleep(3600)\n"
    )
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


def launches(cwd: str) -> list[str]:
    """The argv the shim was started with in *cwd*, oldest first."""
    try:
        with open(LOG, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    return [line.split("\t", 1)[1] for line in lines if line.split("\t", 1)[0] == cwd]


def trusted(cwd: str) -> object:
    with open(f"{E2E}/claude.json", encoding="utf-8") as fh:
        projects = json.load(fh).get("projects", {})
    return projects.get(cwd, {}).get("hasTrustDialogAccepted")


# The trust Collins is launching on: the tree above both projects, never the
# projects themselves. That is the state that breaks `-w` — a session inherits
# the answer, cutting a worktree doesn't.
i18n.init(AppState().get_setting("language"))
trust.trust_dir(E2E)

app = App()
exit_code = 1
tries = 0
state: dict = {}


def later(fn, ms: int = 3000) -> bool:
    GLib.timeout_add(ms, fn)
    return GLib.SOURCE_REMOVE


def start(cwd: str):
    """A session started by hand opens onto the new-chat screen; its Send,
    with the worktree box ticked, is the launch this check watches (the
    window settles the flag and the trust exactly as it does for the screen,
    see MainWindow._on_new_chat_send)."""
    win = state["win"]
    tab = win._launch_new_session(cwd, win._default_provider(), None, True)
    tab._new_chat.emit("send-requested", "hello", True)
    return tab


# -- the steps ---------------------------------------------------------------


def stage() -> bool:
    """Wait for the window, then start a session that asks for a worktree in
    the project where the CLI won't cut one."""
    global tries
    tries += 1
    win = app.get_active_window()
    if win is None:
        if tries > 40:  # ~10s
            print("timed out waiting for the window", file=sys.stderr)
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE
    state["win"] = win
    state["failing"] = start(FAIL_CWD)
    return later(step_fallback)


def step_fallback() -> bool:
    tab = state["failing"]
    typed = launches(FAIL_CWD)
    check("the worktree launch went first", bool(typed) and "-w" in typed[0].split(), typed)
    check("it was started again without the worktree",
          len(typed) == 2 and "-w" not in typed[1].split(), typed)
    check("the tab's options dropped the flag", tab._options is not None
          and not tab._options.worktree, tab._options)
    check("the tab stopped watching for a failure", tab._worktree_launch is False)
    check("the terminal says what happened",
          "couldn't create a worktree" in tab._visible_screen_text(),
          tab._visible_screen_text()[-400:])
    check("the launch directory's own trust was recorded", trusted(FAIL_CWD) is True,
          trusted(FAIL_CWD))
    state["working"] = start(OK_CWD)
    return later(step_no_false_positive)


def step_no_false_positive() -> bool:
    tab = state["working"]
    typed = launches(OK_CWD)
    check("a launch that worked is left alone",
          len(typed) == 1 and "-w" in typed[0].split(), typed)
    check("its options keep the worktree flag", tab._options is not None and tab._options.worktree,
          tab._options)
    check("the error text in its output changed nothing",
          "Error creating worktree" in tab._visible_screen_text(),
          tab._visible_screen_text()[-400:])
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
