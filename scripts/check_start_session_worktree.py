#!/usr/bin/env python3
"""End-to-end check of what `start_session` says about a worktree — dev machine.

The reply used to infer the outcome by comparing the launch directory with the
client's mirror of the agent's cwd, which only the service's 2 s poll moved.
The resolver can bind a `-w` session between two polls (the CLI cuts its
worktree and writes its transcript well inside one), so a session that got its
worktree was reported as "this isn't a git checkout". Here the poll is held off
altogether, so only what the resolving event itself carries can reach the
reply:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \
        python3 scripts/check_start_session_worktree.py

The `claude` shim draws the idle prompt, on `-w` moves into a directory under
`.claude/worktrees/` as the CLI does (or, in one project, refuses as the CLI
does), and writes its transcript under the cwd it ended up in once the prompt
lands. Every scene asserts both the argv that really ran and the reply, so a
request that is ignored can't pass on its wording alone.

Run it behind the headless wrapper, or a window opens on the user's screen.
"""

import os
import shutil
import signal
import sys
import tempfile

E2E = os.path.realpath(tempfile.mkdtemp(prefix="collins-sswt-"))
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

os.environ["COLLINS_DEBUG_API"] = "1"  # the e2e probe (debug.*): served only with this set
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["COLLINS_UPLOADS_DIR"] = f"{E2E}/uploads"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"

CALLER = f"{E2E}/caller"  # the session that calls the tool
REPO = f"{E2E}/repo"  # a checkout, its project's default off
DEFAULTED = f"{E2E}/defaulted"  # a checkout whose project starts sessions in a worktree
PLAIN = f"{E2E}/plain"  # no checkout at all
REFUSES = f"{E2E}/refuses"  # a checkout the CLI won't cut a worktree in
UNANSWERED = f"{E2E}/unanswered"  # a checkout the service can't be asked about
LOG = f"{E2E}/launches.log"  # one line per shim run: cwd, then its argv
SHIM = f"{E2E}/bin/claude"

for path in (f"{E2E}/projects", f"{E2E}/chats", f"{E2E}/bin", f"{E2E}/config/collins", CALLER, PLAIN):
    os.makedirs(path, exist_ok=True)
for path in (REPO, DEFAULTED, REFUSES, UNANSWERED):
    # A .git is all the launch asks of a checkout; the shim runs no git.
    os.makedirs(f"{path}/.git", exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
with open(f"{E2E}/config/collins/state.json", "w", encoding="utf-8") as fh:
    fh.write(
        '{"settings": {"title_model": "none", "welcome_seen": true,'
        ' "gh_welcome_dismissed": true, "worktree_new_sessions": false}}'
    )

_SHIM = r'''#!/usr/bin/env python3
import os, re, sys, time, tty, uuid, pathlib
LOG, REFUSES = @LOG@, @REFUSES@
start = os.getcwd()
with open(LOG, "a", encoding="utf-8") as fh:
    fh.write(start + "\t" + " ".join(sys.argv[1:]) + "\n")
if "-w" in sys.argv[1:]:
    if start == REFUSES:
        sys.stderr.write("Error creating worktree: Workspace trust not yet accepted.\n")
        sys.exit(1)
    worktree = f"{start}/.claude/worktrees/wt{os.getpid()}"
    os.makedirs(worktree)
    os.chdir(worktree)
tty.setraw(0)
sys.stdout.write("❯ ")
sys.stdout.flush()
buf = b""
while b"\x1b[201~" not in buf:
    ch = os.read(0, 1)
    if not ch:
        break
    buf += ch
cwd = os.getcwd()
proj = pathlib.Path(os.environ["COLLINS_PROJECTS_DIR"]) / re.sub(r"[^A-Za-z0-9]", "-", cwd)
proj.mkdir(parents=True, exist_ok=True)
(proj / (str(uuid.uuid4()) + ".jsonl")).write_text('{"type":"summary","cwd":"' + cwd + '"}\n')
sys.stdout.write("\r\nworking\r\n")
sys.stdout.flush()
while True:
    time.sleep(3600)
'''
with open(SHIM, "w", encoding="utf-8") as fh:
    fh.write(_SHIM.replace("@LOG@", repr(LOG)).replace("@REFUSES@", repr(REFUSES)))
os.chmod(SHIM, 0o755)
os.environ["PATH"] = f"{E2E}/bin:{os.environ['PATH']}"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
import e2e_service  # noqa: E402
from gi.repository import GLib  # noqa: E402

from collins import apilink, i18n, mcptools, remotefiles, trust  # noqa: E402
from collins.api.protocol import RequestRefused  # noqa: E402
from collins.app import App  # noqa: E402
from collins.sessions import project_name_for_cwd  # noqa: E402
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


def launches(cwd: str) -> list[list[str]]:
    """The argv of every shim run started in *cwd*, oldest first."""
    try:
        with open(LOG, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    return [line.split("\t", 1)[1].split() for line in lines if line.split("\t", 1)[0] == cwd]


def flags(cwd: str) -> list[bool]:
    """Whether each run in *cwd* carried the worktree flag."""
    return ["-w" in argv for argv in launches(cwd)]


i18n.init(AppState().get_setting("language"))
trust.trust_dir(E2E)
for path in (CALLER, REPO, DEFAULTED, PLAIN, REFUSES, UNANSWERED):
    trust.trust_dir(path)
AppState().set_project_worktree(project_name_for_cwd(DEFAULTED), True)

e2e_service.start_service()
app = App()
exit_code = 1
tries = 0
state: dict = {}

# One way to not be able to ask: the service refuses the `.git` stat. It is
# the client's own function, and the only directory whose marker it refuses.
_real_stat = remotefiles.stat_path


def _stat(path, *args, **kwargs):
    if path == f"{UNANSWERED}/.git":
        raise RequestRefused("failed", "stubbed: the service could not be asked")
    return _real_stat(path, *args, **kwargs)


remotefiles.stat_path = _stat

STARTED = "Started session "
SCENES = [
    # (name, cwd, the tool's `worktree` argument or absent, the flags that ran)
    ("worktree=true in a checkout", REPO, True, [True]),
    ("worktree=false over a project default that is on", DEFAULTED, False, [False]),
    ("no argument under a project default that is on", DEFAULTED, None, [False, True]),
    ("worktree=true outside a checkout", PLAIN, True, [False]),
    ("no argument outside a checkout", PLAIN, None, [False, False]),
    ("worktree=true where the CLI refuses", REFUSES, True, [True, False]),
]


def stage() -> bool:
    global tries
    tries += 1
    win = app.get_active_window()
    if win is None:
        if tries > 40:
            print("timed out waiting for the window", file=sys.stderr)
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE
    # Hold the service's cwd poll off: after its first read (the launch
    # directory) nothing but the resolving event can move the mirror.
    apilink.call(
        {"t": "debug.sandbox", "target": "core", "name": "debug_patch",
         "args": ["collins.service.session", "CWD_POLL_MS", 3_600_000]}
    )
    state["win"] = win
    state["caller"] = win.start_background_session(CALLER)
    GLib.timeout_add(2500, next_scene)
    return GLib.SOURCE_REMOVE


def next_scene() -> bool:
    if not SCENES:
        return refused_scene()
    name, cwd, worktree, _expected = SCENES[0]
    args = {"prompt": "go", "cwd": cwd}
    if worktree is not None:
        args["worktree"] = worktree
    answer = app.tool_client.start_session((state["win"], state["caller"]), args)
    check(f"{name}: the tool defers", isinstance(answer, mcptools.DeferredResult), answer)
    if not isinstance(answer, mcptools.DeferredResult):
        return done()
    answer.watch(lambda ok, text: GLib.timeout_add(100, scene_landed, ok, text))
    return GLib.SOURCE_REMOVE


def scene_landed(ok: bool, text: str) -> bool:
    name, cwd, worktree, expected = SCENES.pop(0)
    print(f"      {name}: {text}")
    check(f"{name}: succeeded", ok, text)
    check(f"{name}: the flags that really ran", flags(cwd) == expected, launches(cwd))
    if cwd == REPO or (cwd == DEFAULTED and worktree is None):
        check(
            f"{name}: says it is in the worktree",
            text.startswith(STARTED) and f" in a fresh worktree at {cwd}/.claude/worktrees/wt" in text,
            text,
        )
    elif cwd == PLAIN and worktree:
        check(f"{name}: says why there is none", "isn't a git checkout" in text and cwd in text, text)
    elif cwd == REFUSES:
        check(
            f"{name}: says the worktree failed, not that it is no checkout",
            "couldn't be created" in text and "isn't a git checkout" not in text and cwd in text,
            text,
        )
    else:
        check(f"{name}: names the directory and nothing else", text.endswith(f" in {cwd}."), text)
    next_scene()
    return GLib.SOURCE_REMOVE


def refused_scene() -> bool:
    """The service can't say whether it is a checkout: nothing starts, since a
    session started anyway would edit the tree the worktree was to protect."""
    win = state["win"]
    state["pages"] = win.tab_view.get_n_pages()
    answer = app.tool_client.start_session(
        (win, state["caller"]), {"prompt": "go", "cwd": UNANSWERED, "worktree": True}
    )
    check("an unanswered ask defers", isinstance(answer, mcptools.DeferredResult), answer)
    if not isinstance(answer, mcptools.DeferredResult):
        return done()
    answer.watch(lambda ok, text: GLib.timeout_add(100, refused_landed, ok, text))
    return GLib.SOURCE_REMOVE


def refused_landed(ok: bool, text: str) -> bool:
    print(f"      an unanswered ask: {text}")
    check("an unanswered ask is a refusal", not ok and UNANSWERED in text, text)
    check("an unanswered ask ran nothing", launches(UNANSWERED) == [], launches(UNANSWERED))
    check("an unanswered ask opened no tab", state["win"].tab_view.get_n_pages() == state["pages"])
    done()
    return GLib.SOURCE_REMOVE


def done() -> bool:
    global exit_code
    win = state["win"]
    for i in range(win.tab_view.get_n_pages()):
        tab = win.tab_view.get_nth_page(i).get_child()
        probe = getattr(tab, "probe_call", None)
        pid = probe("child_pid") if probe is not None else None
        if pid:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except OSError:
                pass
    print(f"\n{PASSED} passed, {FAILED} failed")
    exit_code = 0 if FAILED == 0 and PASSED else 1
    app.quit()
    return GLib.SOURCE_REMOVE


def _deadline() -> bool:
    print("FAIL  the check's own deadline")
    e2e_service._stop_all()
    os._exit(1)


GLib.timeout_add(250, stage)
GLib.timeout_add_seconds(100, _deadline)
app.run([])
shutil.rmtree(E2E, ignore_errors=True)
sys.exit(exit_code)
