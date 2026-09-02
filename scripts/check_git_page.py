#!/usr/bin/env python3
# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Wiring check for the git page (collins/gitpage.py) — run on a dev machine.

Exercises the GTK side that tests/test_hunkctl.py and tests/test_gitinfo.py
can't reach: a real GitPage in a real window, spawning a stand-in `hunk`
into its VTE with the bundled collins-git extension on its argv and the
sidecar's path in its environment, resolving the session id by pid off the
shim's `session list --json`, switching modes through `session reload`,
reloading on a freshness tick after a commit (and not for a move the
extension recorded as already shown), keeping the viewer through a
reload hunk refuses, picking up a parent branch the extension set through
the sidecar (and publishing the automatic one back), following a reload
made behind Collins' back (the poll's `session get`) — a commit becoming
the page's own load (its breadcrumb naming it, `<ref> <subject>`, off a
real git), a range between two branches staying hunk's — taking the child
down on close, restoring into `hunk show <sha>` with the user's parent
(carried in page_state before the page is ever shown), opening the default
mode for a saved commit git no longer has, and reopening a dead viewer
from Ctrl+1/2/3. A second pass with an empty PATH checks the install card
comes up instead.

The shim is a small Python script staged on a scratch PATH: `--version`
answers 0.20.1, `diff …` and `show …` spawn a child "viewer" (the
two-process shape of the real npm wrapper) and record both pids, the
arguments, the `--extension` directory and the sidecar path in a state
file, and the `session` subcommands answer out of that file the way hunk
0.20 does (shapes probed on 2026-09-01; the refusal of a bad range, which
leaves the viewer as it was, on 2026-09-02). FAKE_HUNK_REFUSE names a diff
target the shim's `session reload` refuses. A third pass checks hunk also
goes down — viewer included — when the page is unparented (a tab close), on
gitpage.shutdown_all (the app's shutdown), and when the page closes while
its spawn is still in flight, not only through page_closed.

This is a script, not a pytest test, on purpose: tests/conftest.py blocks
the GTK-stack namespaces for the whole suite so local runs reproduce CI.
Testing widgets for real means running this by hand, behind a display
nobody is looking at:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_git_page.py

(CI runs it under Xvfb through scripts/run_e2e.py.) Skips, exiting 0 with a
message, when git isn't installed — the temp repository needs it.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

from collins import gitinfo, gitpage, hunkctl  # noqa: E402
from collins.gitpage import GitPage  # noqa: E402

PASSED = 0
FAILED = 0

# How long the page gets for each asynchronous step (a spawn plus the first
# session-list poll, a reload round trip, a child's exit).
STEP_TIMEOUT_S = 8.0

# The shim's shebang is the running interpreter, absolute: the page is
# probed with a PATH holding nothing but the shim's directory.
FAKE_HUNK = f"#!{sys.executable}\n" + r'''
"""A stand-in for hunk 0.20: enough of the CLI for the git page's plumbing."""
import json, os, signal, subprocess, sys, time

STATE = os.environ["FAKE_HUNK_STATE"]
# The repository the check stages, named "repo" — what the titles below
# start with, and what repoRoot reports (the page strips it off a title it
# can't otherwise read).
REPO = os.path.join(os.path.dirname(STATE), "repo")


def read_state():
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def write_state(state):
    with open(STATE, "w") as fh:
        json.dump(state, fh)


def title_for(args):
    if args and args[0] == "show":
        return "repo show " + (args[1] if len(args) > 1 else "HEAD")
    if "--staged" in args:
        return "repo staged changes"
    for arg in args:
        if ".." in arg:
            return "repo " + arg
    return "repo working tree"


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


args = sys.argv[1:]
if args == ["--version"]:
    print("0.20.1")
    sys.exit(0)
if args and args[0] in ("diff", "show"):
    # The real hunk on PATH is an npm wrapper that spawnSyncs the viewer
    # binary and never forwards a signal to it — so the shim is two
    # processes too: this one is the wrapper, its child the viewer, and it
    # is the *child's* pid the session list reports, as hunk's does.
    if os.environ.get("FAKE_HUNK_ROLE") == "viewer":
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        while True:
            time.sleep(0.1)
    tail = args[1:]
    extension = None
    if "--extension" in tail:
        at = tail.index("--extension")
        extension = tail[at + 1]
        del tail[at:at + 2]
    diff_args = [a for a in tail if a not in ("--watch", "--transparent-bg")]
    if args[0] == "show":
        diff_args = ["show", *diff_args]  # the same shape a `session reload -- show` records
    state = read_state()
    env = {**os.environ, "FAKE_HUNK_ROLE": "viewer"}
    viewer = subprocess.Popen([sys.executable, __file__, *args], env=env)
    write_state({
        "pid": viewer.pid,
        "wrapper": os.getpid(),
        "args": diff_args,
        "extension": extension,
        "sidecar": os.environ.get("COLLINS_GIT_STATE"),
        "reloads": state.get("reloads", 0),
    })
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    viewer.wait()
    sys.exit(0)
if args and args[0] == "session":
    state = read_state()
    pid = state.get("pid")
    live = pid is not None and alive(pid)
    session = {
        "sessionId": "fake-session-1",
        "pid": pid,
        "cwd": REPO,
        "repoRoot": REPO,
        "title": title_for(state.get("args", [])),
        "fileCount": 0,
        "files": [],
    }
    if args[1] == "list":
        print(json.dumps({"sessions": [session] if live else []}))
        sys.exit(0)
    session_id = args[2] if len(args) > 2 else None
    if not live or session_id != "fake-session-1":
        print(f"hunk: No active session matches sessionId {session_id}.", file=sys.stderr)
        sys.exit(1)
    if args[1] == "get":
        print(json.dumps({"session": session}))
        sys.exit(0)
    if args[1] == "reload":
        tail = args[args.index("--") + 1:] if "--" in args else []
        diff_args = tail[1:] if tail and tail[0] == "diff" else tail
        refused = os.environ.get("FAKE_HUNK_REFUSE")
        if refused and refused in diff_args:
            # hunk 0.20.1's answer to a range git can't resolve: exit 1, the
            # viewer untouched.
            print(
                f"hunk: `hunk diff {refused}` could not resolve Git revision or range `{refused}`.",
                file=sys.stderr,
            )
            sys.exit(1)
        state["args"] = diff_args
        state["reloads"] = state.get("reloads", 0) + 1
        write_state(state)
        session["title"] = title_for(diff_args)
        print(json.dumps({"result": session}))
        sys.exit(0)
print("unknown command", args, file=sys.stderr)
sys.exit(2)
'''


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  ok  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}  {detail}")


def wait_for(condition, timeout: float = STEP_TIMEOUT_S) -> bool:
    """Spin the main loop until *condition()* holds or *timeout* passes."""
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if condition():
            return True
        time.sleep(0.02)
    return condition()


# Resolved before PATH is swapped out from under the page.
GIT = shutil.which("git")


def git(repo: str, *args: str) -> None:
    subprocess.run(
        [GIT, "-c", "user.email=t@example.com", "-c", "user.name=Test", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def make_repo(root: str) -> str:
    """main and feat at one commit, plus `base`: another branch the user can
    pick as the parent through the sidecar."""
    repo = os.path.join(root, "repo")
    os.mkdir(repo)
    git(repo, "init", "-q", "-b", "main")
    with open(os.path.join(repo, "a.txt"), "w") as fh:
        fh.write("one\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-qm", "first")
    git(repo, "branch", "base")
    git(repo, "checkout", "-qb", "feat")
    return repo


def head_sha(repo: str) -> str:
    return subprocess.run(
        [GIT, "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def read_sidecar(path: str | None) -> dict:
    if not path:
        return {}
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def write_sidecar(path: str, **fields) -> None:
    """What the extension writes: the whole file, read-merge-write, keeping
    what Collins put there."""
    data = read_sidecar(path)
    data.update(fields)
    with open(path, "w") as fh:
        json.dump(data, fh)


def read_state(path: str) -> dict:
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def pid_alive(pid: int | None) -> bool:
    """Is *pid* still running — a zombie counts as gone.

    A viewer orphaned by its wrapper's death is re-parented to pid 1, and
    CI's job container has no init that reaps: the dead viewer lingers as
    a zombie there, which `os.kill(pid, 0)` still answers for. Read the
    state off /proc instead, so "went down" means the same thing on a
    desktop (systemd reaps at once) and in the container.
    """
    if not pid:
        return False
    try:
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                if line.startswith("State:"):
                    return not line.split()[1].startswith("Z")
    except OSError:
        return False
    return False


def card_title(page: GitPage) -> str:
    card = page._card_slot.get_child()
    return card.get_title() if card is not None else ""


def shim_processes(shim: str) -> list[int]:
    """Every live process running the shim (wrapper or viewer), off /proc."""
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as fh:
                cmdline = fh.read()
        except OSError:
            continue
        if shim.encode() in cmdline:
            found.append(int(entry))
    return found


def settled(page: GitPage) -> bool:
    """No reload or session get in flight."""
    return not page._reloading and not page._syncing_session


def check_with_hunk(repo: str, state_path: str, shim: str) -> None:
    print("-- with a hunk on PATH")
    closed = []
    page = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
        on_close=lambda p: p.page_closed(),
        on_closed=closed.append,
    )
    window = Gtk.Window(title="check_git_page", default_width=900, default_height=600)
    window.set_child(page)
    window.present()

    check("page_state before the spawn", page.page_state() == {"kind": "git", "loaded": "unstaged"})
    check("page title before the spawn", page.page_title() == "Git · unstaged", page.page_title())

    resolved = wait_for(lambda: page._session_id is not None)
    check("session id resolved by pid", resolved, page._session_id)
    state = read_state(state_path)
    check("hunk spawned into the unstaged working tree", state.get("args") == [], state)
    check("hunk is alive", page.hunk_alive and pid_alive(state.get("pid")), state)
    check("the child VTE spawned is the wrapper", page._child_pid == state.get("wrapper"), state)
    check("the stack shows hunk", page._stack.get_visible_child_name() == "hunk")
    check("holds_escape while hunk runs", page.holds_escape())
    check("page title after the spawn", page.page_title() == "Git · unstaged", page.page_title())
    check(
        "breadcrumb reads working tree · unstaged",
        page._breadcrumb.get_text() == "working tree · unstaged",
        page._breadcrumb.get_text(),
    )
    check("branch label reads the checked-out branch", page._branch_label.get_text() == "⎇ feat")
    check("the parent resolved to main", page._parent_name == "main" and page._parent_target == "main")

    # -- the extension and the sidecar reached the child --------------------------------
    check(
        "hunk spawned with --extension pointing at the bundled collins-git",
        state.get("extension") == hunkctl.extension_dir() == hunkctl.EXTENSION_DIR,
        (state.get("extension"), hunkctl.EXTENSION_DIR),
    )
    check(
        "the extension directory has its package.json",
        os.path.isfile(os.path.join(hunkctl.EXTENSION_DIR, "package.json")),
    )
    sidecar = state.get("sidecar")
    check("COLLINS_GIT_STATE names the page's sidecar", sidecar == page._sidecar, (sidecar, page._sidecar))
    check(
        "the sidecar sits under the runtime dir's collins/",
        bool(sidecar) and os.path.dirname(sidecar) == os.path.join(GLib.get_user_runtime_dir(), "collins"),
        sidecar,
    )
    check(
        "the sidecar carries the automatic parent, the default branch and the page size",
        read_sidecar(sidecar)
        == {"version": 1, "parent": "main", "parentSource": "auto", "default": "main", "logPage": 20},
        read_sidecar(sidecar),
    )
    check("the check's own environment was not touched", hunkctl.SIDECAR_ENV not in os.environ)
    check(
        "page_state carries no parent while the automatic one is in force",
        "parent" not in page.page_state(),
        page.page_state(),
    )

    # -- switch to staged through session reload --------------------------------
    titles = []
    page.connect("title-changed", lambda *_a: titles.append(page.page_title()))
    page.load("staged")
    check("the tab title follows the load at once", page.page_title() == "Git · staged", titles)
    check("page_state follows the load", page.page_state() == {"kind": "git", "loaded": "staged"})
    landed = wait_for(lambda: read_state(state_path).get("args") == ["--staged"] and not page._reloading)
    check("session reload asked for --staged", landed, read_state(state_path))
    check(
        "the same child is still running (no respawn)", read_state(state_path).get("pid") == state.get("pid")
    )
    check(
        "breadcrumb reads working tree · staged",
        page._breadcrumb.get_text() == "working tree · staged",
        page._breadcrumb.get_text(),
    )

    # -- branch mode names the parent ------------------------------------------
    page.load("branch")
    landed = wait_for(lambda: read_state(state_path).get("args") == ["main...HEAD"] and not page._reloading)
    check("branch mode reloads main...HEAD", landed, read_state(state_path))
    check("tab title names the parent", page.page_title() == "Git · vs main", page.page_title())
    check(
        "breadcrumb reads feat vs main",
        page._breadcrumb.get_text() == "feat vs main",
        page._breadcrumb.get_text(),
    )

    # -- load() is what Ctrl+1 and the host's open_git_page(mode) do --------------
    page.load("unstaged")
    landed = wait_for(lambda: read_state(state_path).get("args") == [] and not page._reloading)
    check("load(\"unstaged\") reloads the working tree", landed, read_state(state_path))
    check("loaded follows the load", page.loaded == "unstaged")

    # -- freshness: a commit moves HEAD, the tick reloads ----------------------
    reloads_before = read_state(state_path).get("reloads", 0)
    page.poll_tick()  # nothing moved: no reload
    with open(os.path.join(repo, "a.txt"), "w") as fh:
        fh.write("two\n")
    git(repo, "commit", "-qam", "second")
    page.poll_tick()
    landed = wait_for(
        lambda: read_state(state_path).get("reloads", 0) == reloads_before + 1 and not page._reloading
    )
    check("a commit seen by poll_tick reloads the diff once", landed, read_state(state_path))
    page.poll_tick()
    wait_for(lambda: not page._reloading, timeout=1.0)
    check(
        "a tick with nothing moved doesn't reload again",
        read_state(state_path).get("reloads", 0) == reloads_before + 1,
        read_state(state_path),
    )

    # -- freshness: a move the extension made and recorded is not reloaded for ---
    with open(os.path.join(repo, "a.txt"), "w") as fh:
        fh.write("two b\n")
    git(repo, "add", "a.txt")  # what the extension's `x` does to the index …
    write_sidecar(  # … and what it writes right after its own hunk.app.refresh
        sidecar, refreshed={"index": str(gitinfo.index_mtime(repo)), "head": head_sha(repo)}
    )
    page.poll_tick()
    wait_for(lambda: not page._reloading, timeout=1.0)
    check(
        "a stage the extension recorded in the sidecar does not reload the page",
        read_state(state_path).get("reloads", 0) == reloads_before + 1 and page._ext_refreshed is not None,
        (read_state(state_path), page._ext_refreshed),
    )
    git(repo, "commit", "-qm", "second-b")  # a shell's move after it: the record is stale
    page.poll_tick()
    landed = wait_for(
        lambda: read_state(state_path).get("reloads", 0) == reloads_before + 2 and not page._reloading
    )
    check("a later move from a shell reloads the diff again", landed, read_state(state_path))

    # -- a reload hunk refuses keeps the viewer ------------------------------------
    pid_before = read_state(state_path).get("pid")
    reloads_before = read_state(state_path).get("reloads", 0)
    os.environ["FAKE_HUNK_REFUSE"] = "main...HEAD"
    try:
        page.load("branch")
        check("the header follows the ask at once", page.loaded == "branch")
        back = wait_for(lambda: settled(page) and page.loaded == "unstaged")
        check(
            "a refused reload puts the header back to what hunk shows",
            back and page._breadcrumb.get_text() == "working tree · unstaged",
            (page.loaded, page._breadcrumb.get_text()),
        )
        check(
            "the same child is still running (a refusal is no respawn)",
            read_state(state_path).get("pid") == pid_before and pid_alive(pid_before),
            (read_state(state_path).get("pid"), pid_before),
        )
        check(
            "the refused reload changed nothing",
            read_state(state_path).get("reloads", 0) == reloads_before,
        )
        check("the parent target is put down after the refusal", page._parent_target is None)
    finally:
        del os.environ["FAKE_HUNK_REFUSE"]
    page.load("branch")
    landed = wait_for(lambda: read_state(state_path).get("args") == ["main...HEAD"] and settled(page))
    check("the vs load works again once the target resolves", landed, read_state(state_path))
    check("the parent target is back", page._parent_target == "main")

    # -- the extension sets the parent through the sidecar ----------------------------
    reloads_before = read_state(state_path).get("reloads", 0)
    write_sidecar(sidecar, parent="base", parentSource="user")
    page.poll_tick()
    check("a tick picks the user's parent up off the sidecar", page._parent_name == "base", page._parent_name)
    check(
        "page_state carries the user-set parent", page.page_state().get("parent") == "base", page.page_state()
    )
    landed = wait_for(lambda: read_state(state_path).get("args") == ["base...HEAD"] and settled(page))
    check("the branch diff reloads against the new parent", landed, read_state(state_path))
    check(
        "one reload for the parent change, not a freshness one on top",
        read_state(state_path).get("reloads", 0) == reloads_before + 1,
        read_state(state_path),
    )
    check(
        "breadcrumb reads feat vs base",
        page._breadcrumb.get_text() == "feat vs base",
        page._breadcrumb.get_text(),
    )
    check("tab title names the new parent", page.page_title() == "Git · vs base", page.page_title())
    check(
        "Collins wrote the pick back as it stands",
        read_sidecar(sidecar).get("parent") == "base" and read_sidecar(sidecar).get("parentSource") == "user",
        read_sidecar(sidecar),
    )
    page.poll_tick()
    wait_for(lambda: settled(page))
    check("a second tick changes nothing", read_state(state_path).get("reloads", 0) == reloads_before + 1)
    page.load("unstaged")
    wait_for(lambda: read_state(state_path).get("args") == [] and settled(page))
    reloads_before = read_state(state_path).get("reloads", 0)
    write_sidecar(sidecar, parentSource="auto")  # "Automatic": the extension leaves the name alone
    page.poll_tick()
    check("back to the automatic parent", page._parent_name == "main", page._parent_name)
    check("page_state drops the parent", "parent" not in page.page_state(), page.page_state())
    check(
        "Collins published the automatic name into the sidecar (keeping the extension's record)",
        read_sidecar(sidecar) == {
            "version": 1,
            "parent": "main",
            "parentSource": "auto",
            "default": "main",
            "logPage": 20,
            "refreshed": read_sidecar(sidecar).get("refreshed"),  # the extension's record, kept
        }
        and read_sidecar(sidecar).get("refreshed") is not None,
        read_sidecar(sidecar),
    )
    wait_for(lambda: settled(page))
    wait_for(lambda: False, timeout=0.3)
    check(
        "a parent change under a working-tree load reloads nothing",
        read_state(state_path).get("reloads", 0) == reloads_before,
        read_state(state_path),
    )

    # -- the poll follows a reload made behind Collins' back -----------------------
    subprocess.run(
        [shim, "session", "reload", "fake-session-1", "--json", "--", "diff", "--staged"],
        check=True,
        capture_output=True,
    )
    check("(the header still says working tree · unstaged before the tick)", page.loaded == "unstaged")
    page.poll_tick()
    landed = wait_for(lambda: settled(page) and page.loaded == "staged")
    check("the tick reads the title back from session get", landed, page._breadcrumb.get_text())
    check("the tab title follows hunk", page.page_title() == "Git · staged", page.page_title())

    # -- a commit loaded by the extension (or anyone) becomes the page's own load ----
    subprocess.run(
        [shim, "session", "reload", "fake-session-1", "--json", "--", "show", "HEAD"],
        check=True,
        capture_output=True,
    )
    page.poll_tick()
    landed = wait_for(lambda: settled(page) and page.loaded == {"show": "HEAD"})
    check("a `show` title becomes a commit load", landed, page.loaded)
    check("it isn't foreign", page._foreign is None)
    check(
        "breadcrumb names the commit: ref and subject",
        page._breadcrumb.get_text() == "HEAD second-b",
        page._breadcrumb.get_text(),
    )
    check("the tab title names the commit", page.page_title() == "Git · HEAD", page.page_title())
    check(
        "page_state persists the commit",
        page.page_state() == {"kind": "git", "loaded": {"show": "HEAD"}},
        page.page_state(),
    )
    reloads_before = read_state(state_path).get("reloads", 0)
    with open(os.path.join(repo, "a.txt"), "w") as fh:
        fh.write("three\n")
    git(repo, "commit", "-qam", "third")
    page.poll_tick()
    landed = wait_for(
        lambda: read_state(state_path).get("reloads", 0) == reloads_before + 1 and settled(page)
    )
    check("a freshness tick reloads the commit", landed, read_state(state_path))
    check("as `show HEAD`", read_state(state_path).get("args") == ["show", "HEAD"], read_state(state_path))
    check(
        "the breadcrumb's subject follows the reload",
        page._breadcrumb.get_text() == "HEAD third",
        page._breadcrumb.get_text(),
    )
    page.refresh()
    landed = wait_for(
        lambda: read_state(state_path).get("reloads", 0) == reloads_before + 2 and settled(page)
    )
    check("refresh reloads it too", landed, read_state(state_path))

    # -- a range between two branches is hunk's: shown, left alone -------------------
    subprocess.run(
        [shim, "session", "reload", "fake-session-1", "--json", "--", "diff", "main..feat"],
        check=True,
        capture_output=True,
    )
    page.poll_tick()
    landed = wait_for(lambda: settled(page) and page._foreign is not None)
    check(
        "a load Collins can't name shows hunk's title, repo name stripped",
        landed and page._breadcrumb.get_text() == "main..feat",
        page._breadcrumb.get_text(),
    )
    check("the tab title shows it too", page.page_title() == "Git · main..feat", page.page_title())
    check("page_state keeps the last load Collins knew", page.page_state()["loaded"] == {"show": "HEAD"})
    reloads_before = read_state(state_path).get("reloads", 0)
    with open(os.path.join(repo, "a.txt"), "w") as fh:
        fh.write("four\n")
    git(repo, "commit", "-qam", "fourth")
    page.poll_tick()
    wait_for(lambda: settled(page))
    wait_for(lambda: False, timeout=0.5)
    check(
        "a freshness tick leaves a load Collins can't name alone",
        read_state(state_path).get("reloads", 0) == reloads_before and page._foreign is not None,
        read_state(state_path),
    )
    page.refresh()
    wait_for(lambda: settled(page))
    check("refresh is a no-op on it", read_state(state_path).get("reloads", 0) == reloads_before)
    page.load("unstaged")
    landed = wait_for(lambda: read_state(state_path).get("args") == [] and settled(page))
    check(
        "load() reclaims the page",
        landed and page._foreign is None and page.loaded == "unstaged",
    )

    # -- close ---------------------------------------------------------------------
    pid = read_state(state_path).get("pid")
    wrapper = read_state(state_path).get("wrapper")
    check(
        "the session names the viewer child, not the wrapper",
        pid != wrapper and pid_alive(pid),
        (pid, wrapper),
    )
    page.page_closed()
    check("on_closed fired with the page", closed == [page])
    gone = wait_for(lambda: not pid_alive(wrapper))
    check("the wrapper was signalled on close", gone, wrapper)
    gone = wait_for(lambda: not pid_alive(pid))
    check("the viewer went down with it (no orphan)", gone, pid)
    wait_for(lambda: not page.hunk_alive, timeout=2.0)
    check("no exited card after a deliberate close", page._stack.get_visible_child_name() == "hunk")
    check("the sidecar is removed on close", not os.path.exists(sidecar), sidecar)
    window.destroy()


def check_restore(repo: str, state_path: str, shim: str) -> None:
    """A page rebuilt from a saved layout — a commit, and the user's parent —
    spawns `hunk show <sha>` and reports the parent it was given; a saved
    commit that no longer exists opens the default mode instead of a dead
    viewer; a dead viewer's card reopens on Ctrl+1/2/3 (load())."""
    print("-- restored from a layout")
    sha = head_sha(repo)
    page = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
        on_close=lambda p: None,
        on_closed=lambda p: None,
        loaded=hunkctl.decode_state({"kind": "git", "loaded": {"show": sha}, "parent": "base"}),
        parent=hunkctl.decode_parent({"kind": "git", "loaded": {"show": sha}, "parent": "base"}),
    )
    check(
        "page_state before the spawn keeps the restored parent (a hidden page saves it again)",
        page.page_state() == {"kind": "git", "loaded": {"show": sha}, "parent": "base"},
        page.page_state(),
    )
    check("breadcrumb reads the short sha", page.page_title() == f"Git · {sha[:7]}", page.page_title())
    check(
        "no subject before the spawn",
        page._breadcrumb.get_text() == f"commit {sha[:7]}",
        page._breadcrumb.get_text(),
    )
    window = Gtk.Window(title="restore", default_width=900, default_height=600)
    window.set_child(page)
    window.present()
    check("session id resolved", wait_for(lambda: page._session_id is not None))
    state = read_state(state_path)
    check("hunk spawned as `show <sha>`", state.get("args") == ["show", sha], state)
    check("the user's parent is in force", page._parent_name == "base" and page._parent_target == "base")
    check(
        "page_state carries it",
        page.page_state() == {"kind": "git", "loaded": {"show": sha}, "parent": "base"},
        page.page_state(),
    )
    check(
        "the sidecar says so",
        read_sidecar(state.get("sidecar")).get("parent") == "base"
        and read_sidecar(state.get("sidecar")).get("parentSource") == "user",
        read_sidecar(state.get("sidecar")),
    )
    check(
        "breadcrumb reads <sha7> <subject>",
        page._breadcrumb.get_text() == f"{sha[:7]} fourth",
        page._breadcrumb.get_text(),
    )
    check("the tab title stays short", page.page_title() == f"Git · {sha[:7]}", page.page_title())

    # -- the viewer dies: the card, and Ctrl+1/2/3 as its Reopen -----------------------
    hunkctl.terminate_tree(state["wrapper"], [state["pid"]])
    shown = wait_for(lambda: page._stack.get_visible_child_name() == "card")
    check("a dead viewer shows the exited card", shown and card_title(page) == "hunk exited")
    page.load("staged")
    landed = wait_for(
        lambda: page._session_id is not None and read_state(state_path).get("args") == ["--staged"]
    )
    check("load() on the card reopens hunk, into that load", landed, read_state(state_path))
    check("the card is gone", page._stack.get_visible_child_name() == "hunk")
    page.page_closed()
    wait_for(lambda: not page.hunk_alive, timeout=2.0)
    window.destroy()

    # -- a saved commit that no longer exists (rebased away, another clone) -------------
    gone = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    page = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
        on_close=lambda p: None,
        on_closed=lambda p: None,
        loaded=hunkctl.decode_state({"kind": "git", "loaded": {"show": gone}}),
    )
    check("the page asks for the saved commit", page.loaded == {"show": gone})
    window = Gtk.Window(title="restore (gone commit)", default_width=900, default_height=600)
    window.set_child(page)
    window.present()
    check("session id resolved (gone commit)", wait_for(lambda: page._session_id is not None))
    check(
        "a commit git doesn't know opens the default mode, not a dead `show`",
        read_state(state_path).get("args") == [] and page.loaded == "unstaged",
        (read_state(state_path).get("args"), page.loaded),
    )
    check("no card", page._stack.get_visible_child_name() == "hunk")
    page.page_closed()
    wait_for(lambda: not page.hunk_alive, timeout=2.0)
    window.destroy()

    # A saved parent that no longer exists falls back to the automatic rung.
    page = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
        on_close=lambda p: None,
        on_closed=lambda p: None,
        loaded="branch",
        parent="nosuch",
    )
    window = Gtk.Window(title="restore (stale parent)", default_width=900, default_height=600)
    window.set_child(page)
    window.present()
    check("session id resolved (stale parent)", wait_for(lambda: page._session_id is not None))
    check("a parent that doesn't resolve yields to the automatic one", page._parent_name == "main")
    check("and isn't persisted once found missing", "parent" not in page.page_state(), page.page_state())
    check("the branch diff is against main", read_state(state_path).get("args") == ["main...HEAD"])
    page.page_closed()
    wait_for(lambda: not page.hunk_alive, timeout=2.0)
    window.destroy()


def spawn_page(repo: str, title: str) -> tuple[GitPage, Gtk.Window]:
    """A page in a window over *repo*, with its session id resolved."""
    page = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
        on_close=lambda p: None,
        on_closed=lambda p: None,
    )
    window = Gtk.Window(title=title, default_width=900, default_height=600)
    window.set_child(page)
    window.present()
    check(f"{title}: session id resolved", wait_for(lambda: page._session_id is not None))
    return page, window


def check_teardown_paths(repo: str, state_path: str, shim: str) -> None:
    """hunk goes down with the page on every path that isn't the strip's
    close funnel: the page unparented for good (a tab close) and the app's
    shutdown hook — and *not* on a re-parent, which unrealizes it too — and
    when the page closes while its spawn is still in flight."""
    print("-- teardown without page_closed")
    page, window = spawn_page(repo, "reparent")
    pid, wrapper = read_state(state_path).get("pid"), read_state(state_path).get("wrapper")
    window.set_child(None)  # a drag to another strip: unrealized, then realized again
    window.set_child(page)
    wait_for(lambda: False, timeout=0.6)
    check("a re-parented page keeps its hunk", page.hunk_alive and pid_alive(pid) and pid_alive(wrapper))
    check("the same child, not a respawn", read_state(state_path).get("pid") == pid)
    window.set_child(None)  # the tab closed: unrealized for good
    gone = wait_for(lambda: not pid_alive(pid) and not pid_alive(wrapper))
    check("an unparented page stops hunk, viewer included", gone, (pid, wrapper))
    check("an unparented page isn't closing (a later map may spawn again)", not page._closing)
    window.destroy()

    page, window = spawn_page(repo, "shutdown_all")
    pid, wrapper = read_state(state_path).get("pid"), read_state(state_path).get("wrapper")
    gitpage.shutdown_all()
    gone = wait_for(lambda: not pid_alive(pid) and not pid_alive(wrapper))
    check("shutdown_all stops hunk, viewer included", gone, (pid, wrapper))
    check("shutdown_all marks the page closing", page._closing)
    window.destroy()
    wait_for(lambda: not shim_processes(shim), timeout=2.0)

    # A close within the spawn window: the probe has answered and VTE's
    # spawn is out, but its callback hasn't landed. The child it announces
    # must still go down, viewer included.
    try:
        os.remove(state_path)
    except OSError:
        pass
    page = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
        on_close=lambda p: None,
        on_closed=lambda p: None,
    )
    window = Gtk.Window(title="close mid-spawn", default_width=900, default_height=600)
    window.set_child(page)
    window.present()
    fired: list[bool] = []
    original = page._on_spawned
    page._on_spawned = lambda terminal, pid, error, gen: (
        fired.append(page._closing),
        original(terminal, pid, error, gen),
    )
    probed = wait_for(lambda: page._hunk_path is not None)
    check("the probe answered (spawn_async is out)", probed)
    page.page_closed()
    if fired:
        print("  --  the spawn landed before the close; the mid-spawn path wasn't exercised this run")
    else:
        landed = wait_for(lambda: bool(fired))
        check("the spawn callback landed after the close", landed and fired == [True], fired)
        check("the page took no child on", page._child_pid is None)
    gone = wait_for(lambda: not shim_processes(shim))
    check("a hunk spawned for a closed page goes down, viewer included", gone, shim_processes(shim))
    window.destroy()


def check_without_hunk(repo: str) -> None:
    print("-- with no hunk on PATH")
    page = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
        on_close=lambda p: None,
        on_closed=lambda p: None,
    )
    window = Gtk.Window(title="check_git_page (no hunk)", default_width=900, default_height=600)
    window.set_child(page)
    window.present()
    shown = wait_for(lambda: page._stack.get_visible_child_name() == "card")
    check("the card is the visible stack child", shown, page._stack.get_visible_child_name())
    check("the card says hunk isn't installed", card_title(page) == "hunk isn't installed", card_title(page))
    check("hunk is not alive", not page.hunk_alive)
    check("Escape is not held by a card", not page.holds_escape())
    page.page_closed()
    window.destroy()


def check_outside_a_repo(scratch: str) -> None:
    print("-- outside a repository")
    nowhere = os.path.join(scratch, "nowhere")
    os.mkdir(nowhere)
    page = GitPage(
        cwd_provider=lambda: nowhere,
        parent_provider=lambda _cwd: None,
        on_close=lambda p: None,
        on_closed=lambda p: None,
    )
    window = Gtk.Window(title="check_git_page (no repo)", default_width=900, default_height=600)
    window.set_child(page)
    window.present()
    shown = wait_for(lambda: page._stack.get_visible_child_name() == "card")
    check(
        "the not-a-repository card comes up",
        shown and card_title(page) == "Not a git repository",
        card_title(page),
    )
    check("no parent to name", page._parent_target is None)
    check("no sidecar was written", not os.path.exists(page._sidecar))
    page.page_closed()
    window.destroy()


def main() -> int:
    if GIT is None:
        print("check_git_page: git isn't installed; skipping")
        return 0
    scratch = tempfile.mkdtemp(prefix="collins-git-page-")
    try:
        bindir = os.path.join(scratch, "bin")
        os.mkdir(bindir)
        shim = os.path.join(bindir, "hunk")
        with open(shim, "w") as fh:
            fh.write(FAKE_HUNK)
        os.chmod(shim, os.stat(shim).st_mode | stat.S_IXUSR)
        # The page's own git (a commit's subject, a saved commit's existence)
        # comes off the same PATH, which otherwise holds nothing but the shim.
        os.symlink(GIT, os.path.join(bindir, "git"))
        state_path = os.path.join(scratch, "hunk-state.json")
        os.environ["FAKE_HUNK_STATE"] = state_path
        repo = make_repo(scratch)

        real_path = os.environ.get("PATH", "")
        os.environ["PATH"] = bindir
        try:
            check_with_hunk(repo, state_path, shim)
            check_restore(repo, state_path, shim)
            check_teardown_paths(repo, state_path, shim)
        finally:
            os.environ["PATH"] = real_path

        empty = os.path.join(scratch, "empty")
        os.mkdir(empty)
        os.environ["PATH"] = empty
        try:
            check_without_hunk(repo)
        finally:
            os.environ["PATH"] = real_path

        os.environ["PATH"] = bindir
        try:
            check_outside_a_repo(scratch)
        finally:
            os.environ["PATH"] = real_path
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
