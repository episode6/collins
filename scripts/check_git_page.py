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
reload hunk refuses, git naming the stack (a local branch created under
HEAD becomes the parent on the next tick, the branch diff reloads against
it and the commits list grows its group; deleted, the parent falls back to
the host's rung — the sidecar untouched: it carries no parent since
contract version 2), following a reload
made behind Collins' back (the poll's `session get`) — a commit becoming
the page's own load (its breadcrumb naming it, `<ref> <subject>`, off a
real git), a range between two branches staying hunk's — taking the child
down on close, restoring into `hunk show <sha>` (a layout's stale
"parent" key ignored), opening the default
mode for a saved commit git no longer has, and reopening a dead viewer
from Ctrl+1/2/3. A pass through apply_settings checks Preferences → Git
reaching an open page: a layout or theme change respawns hunk with
--mode/--theme (the same settings again don't), the untracked switch
reloads the current load with --exclude-untracked and rides every later
diff load and the sidecar (never a `show`), the page size reaches the
native commits list with neither. A pass over the native
sidebar (collins/gitsidebar.py) checks its commits list off the real
repository (the branch header, the working tree row, the `main..HEAD`
commits, the default branch's group; no stack group while the parent is
the default, no `↑` without a remote), the header toggle and its
persistence (page_state's "sidebar", a restore with it off), the collapse
under the breakpoint on a 500 px window and the return on a 900 px one, a
commit row's click reloading `show <sha>` (and the default header's doing
nothing), a staged-side file click reloading `--staged` then navigating to
the file, the sidecar's `selection` / `anchor` driving the highlight and
the anchor button's label, the four cursor buttons feeding hunk their
bytes, stage_all and commit moving the repository with exactly one reload
each (and none on the following tick), and `git_log_page` paging the
list — and the same pass runs again beside the **native viewer**
(Preferences → Git → Diff viewer → Native), reading the page's own
`loaded` / `shows` instead of the shim's state file and counting its
reloads through `_native_load`. A native-only pass then stages every kind
of change in the repository (two unstaged hunks with gaps around them, a
staged edit and a staged rename, a modified binary, an image before and
after, an untracked text file and an untracked picture, a deletion, a
mode change) and checks each section kind renders through the view's
probes (`file_rows`, `hunk_rows`, `gap_rows`, `badge_rows`,
`hunk_serials`), a gap expands, a files-list click reveals, an external
edit that keeps the line counts reloads through the file monitors within
2 s keeping the untouched hunk's widget and the keyboard, the `git.*`
actions are routed, the highlight follows a scroll to the end (and the
pinned header), the files filter hides a section, Ctrl+F counts across
hunks, the staged and commit loads land, settings and the keys reach the
view with `page_state` untouched by a layout change, the switch flips
live both ways, and a page restores into a commit. A pass on a PATH
holding git alone checks the install card comes up for hunk and the diff
for the native viewer.

The shim is a small Python script staged on a scratch PATH: `--version`
answers 0.21.1, `diff …` and `show …` spawn a child "viewer" (the
two-process shape of the real npm wrapper) and record both pids, the
arguments, the `--extension` directory, whether `--no-sidebar` was on
the argv, and the sidecar path in a state file, and the `session`
subcommands answer out of that file the way hunk 0.21 does (shapes probed
on 2026-09-01; the refusal of a bad range, which leaves the viewer as it
was, on 2026-09-02; `files[]` and the `snapshot` on 2026-09-05): the
session records list the files FAKE_HUNK_FILES names (comma-separated;
`a.txt` when unset) with hunk 0.21.1's counts, and a snapshot whose
`selectedFilePath` is the last `session navigate`'s file. FAKE_HUNK_REFUSE
names a diff target the shim's `session reload` refuses; `session
navigate` records its target in the file and refuses a file outside
FAKE_HUNK_FILES (the shim is shared with check_show_diff.py, which drives
the tool against it); the viewer records every byte fed to its pty in
`keys`. A third
pass checks hunk also goes down — viewer included — when the page is
unparented (a tab close), on gitpage.shutdown_all (the app's shutdown), and
when the page closes while its spawn is still in flight, not only through
page_closed.

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
"""A stand-in for hunk 0.21: enough of the CLI for the git page's plumbing."""
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


def known_files():
    """The files the loaded diff "has": FAKE_HUNK_FILES, comma-separated,
    `a.txt` when unset (check_show_diff.py sets it for a refusal)."""
    known = os.environ.get("FAKE_HUNK_FILES")
    names = [name for name in (known.split(",") if known is not None else ["a.txt"]) if name]
    return names


def session_files():
    """hunk 0.21.1's fileSummarySchema: id, path, additions, deletions,
    hunkCount, hunks (the record lists them; the page ignores them)."""
    return [
        {"id": f"f{index + 1}", "path": path, "additions": 1, "deletions": 0, "hunkCount": 1, "hunks": []}
        for index, path in enumerate(known_files())
    ]


def snapshot(state):
    """hunk 0.21.1's snapshotSchema, as far as the page reads it: the
    cursor's file is the last navigate's, else the first file."""
    files = known_files()
    navigate = state.get("navigate") or {}
    selected = navigate.get("file") or (files[0] if files else None)
    return {
        "updatedAt": "2026-09-05T00:00:00.000Z",
        "state": {
            "selectedFilePath": selected,
            "selectedHunkIndex": 0,
            "liveComments": [],
            "showAgentNotes": False,
        },
    }


args = sys.argv[1:]
if args == ["--version"]:
    print("0.21.1")
    sys.exit(0)
if args and args[0] in ("diff", "show"):
    # The real hunk on PATH is an npm wrapper that spawnSyncs the viewer
    # binary and never forwards a signal to it — so the shim is two
    # processes too: this one is the wrapper, its child the viewer, and it
    # is the *child's* pid the session list reports, as hunk's does.
    if os.environ.get("FAKE_HUNK_ROLE") == "viewer":
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        # What Collins feeds the pty (the sidebar's cursor buttons) is
        # recorded in the state file, raw, so the check can read it back.
        import select, termios, tty
        try:
            tty.setraw(0)
        except (OSError, termios.error, ValueError):
            pass
        while True:
            ready, _, _ = select.select([0], [], [], 0.1)
            if not ready:
                continue
            try:
                data = os.read(0, 64)
            except OSError:
                data = b""
            if data:
                state = read_state()
                state["keys"] = state.get("keys", "") + data.decode("utf-8", "replace")
                write_state(state)
    tail = args[1:]
    # The valued flags are lifted out and recorded on their own; what is
    # left after the bare ones go is the load's tail (`--exclude-untracked`
    # included: it is part of the tail a reload records too).
    valued = {}
    for flag in ("--extension", "--mode", "--theme"):
        if flag in tail:
            at = tail.index(flag)
            valued[flag] = tail[at + 1]
            del tail[at:at + 2]
    # `--no-sidebar` (hunk 0.21's "hide files pane") is a spawn flag too:
    # tolerated here and recorded, so a check can say whether it was on.
    diff_args = [a for a in tail if a not in ("--watch", "--transparent-bg", "--no-sidebar")]
    if args[0] == "show":
        diff_args = ["show", *diff_args]  # the same shape a `session reload -- show` records
    state = read_state()
    env = {**os.environ, "FAKE_HUNK_ROLE": "viewer"}
    viewer = subprocess.Popen([sys.executable, __file__, *args], env=env)
    write_state({
        "pid": viewer.pid,
        "wrapper": os.getpid(),
        "args": diff_args,
        "extension": valued.get("--extension"),
        "mode": valued.get("--mode"),
        "theme": valued.get("--theme"),
        "no_sidebar": "--no-sidebar" in tail,
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
    files = session_files()
    session = {
        "sessionId": "fake-session-1",
        "pid": pid,
        "cwd": REPO,
        "repoRoot": REPO,
        "title": title_for(state.get("args", [])),
        "sourceLabel": "working tree",
        "fileCount": len(files),
        "files": files,
        "snapshot": snapshot(state),
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
    if args[1] == "navigate":
        # hunk 0.20.1's shape: exactly one target beside --file; a file the
        # loaded diff doesn't have is refused by name; a landed navigate
        # answers with the spot. FAKE_HUNK_FILES (comma-separated) names
        # the files the diff "has"; unset, every file is in it.
        flags = {args[i]: args[i + 1] for i in range(3, len(args) - 1) if args[i].startswith("--")}
        path = flags.get("--file")
        targets = [k for k in ("--hunk", "--old-line", "--new-line") if k in flags]
        if len(targets) != 1:
            print(
                "hunk: Specify exactly one navigation target: --hunk <n>, --old-line <n>, or --new-line <n>.",
                file=sys.stderr,
            )
            sys.exit(1)
        known = os.environ.get("FAKE_HUNK_FILES")
        if known is not None and path not in known.split(","):
            print(f"hunk: No diff file matches {path}.", file=sys.stderr)
            sys.exit(1)
        state["navigate"] = {"file": path, "target": targets[0], "value": flags[targets[0]]}
        state["navigates"] = state.get("navigates", 0) + 1
        write_state(state)
        print(json.dumps({"result": {"filePath": path, "hunkIndex": 0, "revealed": "line"}}))
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
    """main and feat at one commit, plus `base`: a second branch at that
    commit (on the trunk, so never in feat's stack). The identity is set
    in the repository's own config:
    the sidebar's native commit runs a plain `git commit`, which needs one
    (CI's container has no global identity)."""
    repo = os.path.join(root, "repo")
    os.mkdir(repo)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "Test")
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


def log_shas(repo: str, *range_args: str) -> list[str]:
    """`git log --format=%H <range>`, newest first — what the commits list
    is expected to show for a group."""
    return subprocess.run(
        [GIT, "log", "--format=%H", *range_args, "--"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.split()


def git_out(repo: str, *args: str) -> str:
    return subprocess.run([GIT, *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def patch_state(path: str, **fields) -> None:
    """Edit the shim's state file (drop a key by passing None)."""
    state = read_state(path)
    for key, value in fields.items():
        if value is None:
            state.pop(key, None)
        else:
            state[key] = value
    with open(path, "w") as fh:
        json.dump(state, fh)


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
    """No reload, session get or navigate in flight, none queued."""
    return (
        not page._reloading
        and not page._syncing_session
        and not page._navigating
        and page._pending_navigate is None
        and page._pending_reload is None
    )


def check_with_hunk(repo: str, state_path: str, shim: str) -> None:
    print("-- with a hunk on PATH")
    closed = []
    page = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
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
        "the sidecar carries the contract version and the untracked switch, nothing else",
        read_sidecar(sidecar) == {"version": 2, "untracked": True},
        read_sidecar(sidecar),
    )
    check("hunk spawned with --no-sidebar (its files pane hidden)", state.get("no_sidebar") is True, state)
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

    # -- git names the stack: a local branch under HEAD becomes the parent ----------------
    reloads_before = read_state(state_path).get("reloads", 0)
    git(repo, "branch", "step", "HEAD~1")  # on feat's own history: feat stacks on it now
    page.poll_tick()  # the refs signature moved: the stack is re-read
    landed = wait_for(lambda: page._parent_name == "step" and settled(page))
    check("a local branch under HEAD is the parent git names", landed, page._parent_name)
    check("page_state carries no parent (git names it, nothing is set)", "parent" not in page.page_state())
    landed = wait_for(lambda: read_state(state_path).get("args") == ["step...HEAD"] and settled(page))
    check("the branch diff reloads against the branch it stacks on", landed, read_state(state_path))
    check(
        "one reload for the parent change, not a freshness one on top",
        read_state(state_path).get("reloads", 0) == reloads_before + 1,
        read_state(state_path),
    )
    check(
        "breadcrumb reads feat vs step",
        page._breadcrumb.get_text() == "feat vs step",
        page._breadcrumb.get_text(),
    )
    check("tab title names the new parent", page.page_title() == "Git · vs step", page.page_title())
    landed = wait_for(
        lambda: [r.sha for r in page.sidebar.commit_rows() if r.kind == "commit" and r.group == "stack:step"]
        == log_shas(repo, "main..step")
        and [r.sha for r in page.sidebar.commit_rows() if r.kind == "commit" and r.group == "current"]
        == log_shas(repo, "step..HEAD")
    )
    check(
        "the commits list groups by the stack: feat's commits since step, step's since main",
        landed,
        [(r.group, r.label) for r in page.sidebar.commit_rows()],
    )
    step_header = next((r for r in page.sidebar.commit_rows() if r.id == "header:stack:step"), None)
    check(
        "the stack branch's header loads it against the branch below (main...step)",
        step_header is not None and step_header.load == {"range": "main...step"},
        step_header,
    )
    check(
        "the stack never reaches the sidecar (contract version 2 carries no parent)",
        read_sidecar(sidecar).get("parent") is None and "parentSource" not in read_sidecar(sidecar),
        read_sidecar(sidecar),
    )
    page.poll_tick()
    wait_for(lambda: settled(page))
    check("a second tick changes nothing", read_state(state_path).get("reloads", 0) == reloads_before + 1)
    page.load("unstaged")
    wait_for(lambda: read_state(state_path).get("args") == [] and settled(page))
    reloads_before = read_state(state_path).get("reloads", 0)
    git(repo, "branch", "-D", "step")  # the stack is gone: the host's rung names the parent again
    page.poll_tick()
    landed = wait_for(lambda: page._parent_name == "main" and settled(page))
    check("back to the host's parent once the stack is gone", landed, page._parent_name)
    landed = wait_for(lambda: not any(r.group == "stack:step" for r in page.sidebar.commit_rows()))
    check("and the stack group is gone from the commits list", landed)
    check(
        "the sidecar still holds Collins' two keys and the extension's record",
        read_sidecar(sidecar) == {
            "version": 2,
            "untracked": True,
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

    # -- a three-dot range between two branches is the page's own load ------------------
    subprocess.run(
        [shim, "session", "reload", "fake-session-1", "--json", "--", "diff", "main...feat"],
        check=True,
        capture_output=True,
    )
    page.poll_tick()
    landed = wait_for(lambda: settled(page) and page.loaded == {"range": "main...feat"})
    check("a `main...feat` title becomes a range load", landed and page._foreign is None, page.loaded)
    check(
        "its breadcrumb reads the right half against the left",
        page._breadcrumb.get_text() == "feat vs main",
        page._breadcrumb.get_text(),
    )
    check("the tab title names the branch under review", page.page_title() == "Git · feat", page.page_title())
    check(
        "page_state persists the range",
        page.page_state() == {"kind": "git", "loaded": {"range": "main...feat"}},
        page.page_state(),
    )
    reloads_before = read_state(state_path).get("reloads", 0)
    with open(os.path.join(repo, "a.txt"), "w") as fh:
        fh.write("five\n")
    git(repo, "commit", "-qam", "fifth")
    page.poll_tick()
    landed = wait_for(
        lambda: read_state(state_path).get("reloads", 0) == reloads_before + 1 and settled(page)
    )
    check("a freshness tick reloads the range", landed, read_state(state_path))
    check("as `diff main...feat`", read_state(state_path).get("args") == ["main...feat"], read_state(state_path))
    page.refresh()
    landed = wait_for(
        lambda: read_state(state_path).get("reloads", 0) == reloads_before + 2 and settled(page)
    )
    check("refresh reloads the range too", landed, read_state(state_path))
    page.load("unstaged")
    wait_for(lambda: read_state(state_path).get("args") == [] and settled(page))

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
    """A page rebuilt from a saved layout — a commit — spawns `hunk show
    <sha>` (a "parent" key from an older layout is ignored: git names the
    stack); a saved commit that no longer exists opens the default mode
    instead of a dead viewer; a dead viewer's card reopens on Ctrl+1/2/3
    (load())."""
    print("-- restored from a layout")
    sha = head_sha(repo)
    page = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
        on_closed=lambda p: None,
        loaded=hunkctl.decode_state({"kind": "git", "loaded": {"show": sha}, "parent": "base"}),
    )
    check(
        "page_state before the spawn: the load, and no parent (an older layout's is dropped)",
        page.page_state() == {"kind": "git", "loaded": {"show": sha}},
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
    check("the parent is the host's (no stack under HEAD)", page._parent_name == "main" and page._parent_target == "main")
    check(
        "page_state carries the load alone",
        page.page_state() == {"kind": "git", "loaded": {"show": sha}},
        page.page_state(),
    )
    check(
        "breadcrumb reads <sha7> <subject>",
        page._breadcrumb.get_text() == f"{sha[:7]} fifth",
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


def spawn_page(repo: str, title: str) -> tuple[GitPage, Gtk.Window]:
    """A page in a window over *repo*, with its session id resolved."""
    page = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
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


# What a real settings dict carries besides the git_* keys the page reads
# (apply_settings sets the font, the terminal theme and the key chords off
# the same dict; the keybinding key is absent, as it is until the user
# rebinds something, and KeyMatcher tolerates that).
SETTINGS = {"font": "", "terminal_theme": "Default"}


def check_settings(repo: str, state_path: str) -> None:
    """Preferences → Git reaching an open page through apply_settings: the
    defaults spawn today's argv; a layout or theme change respawns hunk
    with --mode/--theme (and the same dict again doesn't); the untracked
    switch is a reload of the current load with --exclude-untracked (no
    respawn), rides every later diff load and the sidecar, and never
    touches a `show`; the page size reaches the native list with neither."""
    print("-- Preferences → Git")
    page, window = spawn_page(repo, "settings")
    state = read_state(state_path)
    check(
        "default options spawn hunk without --mode/--theme/--exclude-untracked",
        state.get("args") == [] and state.get("mode") is None and state.get("theme") is None,
        state,
    )
    sidecar = state.get("sidecar")
    check(
        "the sidecar carries the untracked switch (and no page size: the native list pages)",
        read_sidecar(sidecar) == {"version": 2, "untracked": True},
        read_sidecar(sidecar),
    )

    # -- layout and theme: a respawn, once ---------------------------------------------
    settings = {**SETTINGS, "git_layout": "split", "git_theme": "nord", "git_untracked": True}
    settings["git_log_page"] = 20
    pid_before = state.get("pid")
    page.apply_settings(settings)
    respawned = wait_for(
        lambda: read_state(state_path).get("pid") not in (None, pid_before)
        and page._session_id is not None
        and not page._resolving
    )
    check("a layout/theme change respawns hunk", respawned, read_state(state_path))
    check("the old viewer went down", not pid_alive(pid_before), pid_before)
    state = read_state(state_path)
    check(
        "the new hunk runs with --mode split --theme nord, the tail unchanged",
        state.get("mode") == "split" and state.get("theme") == "nord" and state.get("args") == [],
        state,
    )
    check("the page still shows the working tree", page.loaded == "unstaged" and page._foreign is None)
    pid_before = state.get("pid")
    reloads_before = state.get("reloads", 0)
    page.apply_settings(settings)  # the same dict again: every other preference change looks like this
    wait_for(lambda: False, timeout=0.6)
    check(
        "the same settings again respawn nothing and reload nothing",
        read_state(state_path).get("pid") == pid_before
        and pid_alive(pid_before)
        and read_state(state_path).get("reloads", 0) == reloads_before,
        read_state(state_path),
    )

    # -- the untracked switch: a reload of the current load ---------------------------
    settings["git_untracked"] = False
    page.apply_settings(settings)
    landed = wait_for(
        lambda: read_state(state_path).get("reloads", 0) == reloads_before + 1 and settled(page)
    )
    check("turning untracked files off reloads the current load once", landed, read_state(state_path))
    check(
        "as `diff --exclude-untracked`, on the same viewer",
        read_state(state_path).get("args") == ["--exclude-untracked"]
        and read_state(state_path).get("pid") == pid_before,
        read_state(state_path),
    )
    check(
        "the sidecar says untracked: false at once",
        read_sidecar(sidecar).get("untracked") is False,
        read_sidecar(sidecar),
    )
    check(
        "the breadcrumb still reads the working tree",
        page._breadcrumb.get_text() == "working tree · unstaged",
        page._breadcrumb.get_text(),
    )
    page.load("staged")
    landed = wait_for(
        lambda: read_state(state_path).get("args") == ["--exclude-untracked", "--staged"] and settled(page)
    )
    check("a later diff load carries the switch too", landed, read_state(state_path))
    page.load({"show": "HEAD"})
    landed = wait_for(lambda: read_state(state_path).get("args") == ["show", "HEAD"] and settled(page))
    check("a commit load never carries it (hunk show refuses the flag)", landed, read_state(state_path))
    reloads_before = read_state(state_path).get("reloads", 0)
    settings["git_untracked"] = True
    page.apply_settings(settings)
    wait_for(lambda: settled(page))
    wait_for(lambda: False, timeout=0.5)
    check(
        "flipping the switch under a commit load reloads nothing",
        read_state(state_path).get("reloads", 0) == reloads_before
        and read_state(state_path).get("pid") == pid_before,
        read_state(state_path),
    )
    check("but the sidecar follows it", read_sidecar(sidecar).get("untracked") is True, read_sidecar(sidecar))
    page.load("unstaged")
    landed = wait_for(lambda: read_state(state_path).get("args") == [] and settled(page))
    check("the next diff load runs without the flag", landed, read_state(state_path))

    # -- the switch flipped while a respawn is still resolving its session id ---------
    settings["git_untracked"] = True
    page.apply_settings(settings)
    wait_for(lambda: read_state(state_path).get("args") == [] and settled(page))
    settings["git_layout"] = "stack"
    pid_before = read_state(state_path).get("pid")
    page.apply_settings(settings)
    # The new child is up, its `session list` not yet answered (the first
    # resolve step waits RESOLVE_DELAYS_MS[0]): the window the flip must
    # survive.
    in_window = wait_for(
        lambda: read_state(state_path).get("pid") not in (None, pid_before)
        and page.hunk_alive
        and page._resolving
    )
    check("the respawn into stack is up and resolving", in_window, read_state(state_path))
    settings["git_untracked"] = False
    page.apply_settings(settings)
    check("the flip is noted for the resolution", page._options_stale is True)
    check(
        "and the sidecar says so already",
        read_sidecar(sidecar).get("untracked") is False,
        read_sidecar(sidecar),
    )
    landed = wait_for(
        lambda: not page._resolving
        and read_state(state_path).get("args") == ["--exclude-untracked"]
        and settled(page)
    )
    check("once the id lands the current load is reloaded with the switch", landed, read_state(state_path))
    pid_before = read_state(state_path).get("pid")
    check(
        "on the viewer the respawn started (no second respawn)",
        pid_alive(pid_before) and read_state(state_path).get("mode") == "stack" and not page._options_stale,
        read_state(state_path),
    )
    # A flip while the respawn is still on its way — the old child going
    # down, the probe not yet run — rides the new argv instead: the spawn
    # reads the options as they are then, and nothing is reloaded after.
    settings["git_layout"] = "split"
    page.apply_settings(settings)
    settings["git_untracked"] = True
    page.apply_settings(settings)  # the old child is going down; the probe hasn't run
    check("a flip before the probe leaves the respawn to carry it", page._respawn_wanted is True)
    respawned = wait_for(
        lambda: read_state(state_path).get("pid") not in (None, pid_before)
        and page._session_id is not None
        and not page._resolving
        and settled(page)
    )
    state = read_state(state_path)
    check(
        "and the spawn's own argv carries it, with no reload after",
        respawned and state.get("args") == [] and state.get("mode") == "split" and not page._options_stale,
        state,
    )
    reloads_before = state.get("reloads", 0)
    wait_for(lambda: False, timeout=0.5)
    check(
        "really no reload",
        read_state(state_path).get("reloads", 0) == reloads_before,
        read_state(state_path),
    )
    pid_before = state.get("pid")
    settings["git_untracked"] = False
    page.apply_settings(settings)
    wait_for(lambda: read_state(state_path).get("args") == ["--exclude-untracked"] and settled(page))

    # -- the page size: the sidecar alone ---------------------------------------------
    reloads_before = read_state(state_path).get("reloads", 0)
    settings["git_log_page"] = 50
    page.apply_settings(settings)
    wait_for(lambda: False, timeout=0.5)
    check(
        "a page size change is neither a respawn nor a reload",
        read_state(state_path).get("pid") == pid_before
        and read_state(state_path).get("reloads", 0) == reloads_before,
        read_state(state_path),
    )
    check("the page size reaches the native commits list", page.sidebar.options.log_page == 50)
    check("and never the sidecar", "logPage" not in read_sidecar(sidecar), read_sidecar(sidecar))

    # -- back to the defaults: a respawn into today's argv ----------------------------
    page.apply_settings(SETTINGS)
    respawned = wait_for(
        lambda: read_state(state_path).get("pid") not in (None, pid_before)
        and page._session_id is not None
        and not page._resolving
    )
    state = read_state(state_path)
    check(
        "the defaults respawn hunk without --mode/--theme",
        respawned and state.get("mode") is None and state.get("theme") is None and state.get("args") == [],
        state,
    )
    page.page_closed()
    wait_for(lambda: not page.hunk_alive, timeout=2.0)
    window.destroy()


NATIVE_SETTINGS = {**SETTINGS, "git_viewer": "native", "git_layout": "stack"}


def viewer_up(page: GitPage, native: bool) -> bool:
    """The viewer is on screen with its first load landed: hunk's session
    id resolved, or the native view settled."""
    return page.settled() if native else page._session_id is not None


def check_sidebar(repo: str, state_path: str, native: bool = False) -> None:
    """The native sidebar (collins/gitsidebar.py) beside either viewer: its
    lists off the real repository, the header toggle and its persistence,
    the collapse under the breakpoint, clicks that load and navigate, the
    native mutations, and the page size. Beside hunk (*native* False) the
    loads are read off the shim's state file and the sidecar's selection
    and anchor and the cursor buttons' bytes are checked too; beside the
    native view the page's own `loaded` / `shows` say what landed and the
    view's current file is what the highlight follows."""
    viewer = "the native view" if native else "hunk"
    print(f"-- the native sidebar beside {viewer}")
    page = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
        on_closed=lambda p: None,
    )
    if native:
        page.apply_settings(NATIVE_SETTINGS)
    sidebar = page.sidebar
    # A 500 px window first: under the breakpoint the sidebar hides
    # whatever the toggle says, and the toggle goes insensitive.
    window = Gtk.Window(title="sidebar", default_width=500, default_height=600)
    window.set_child(page)
    window.present()
    check(f"sidebar: {viewer} is up", wait_for(lambda: viewer_up(page, native)))
    narrow = wait_for(lambda: page._narrow)
    check("a 500 px window is under the breakpoint", narrow)
    check("the sidebar is hidden there", not page.sidebar_shown)
    check("the toggle is insensitive and its box says why",
          not page._sidebar_toggle.get_sensitive()
          and page._sidebar_toggle_box.get_tooltip_text() == "Widen the page to show the panels",
          (page._sidebar_toggle.get_sensitive(), page._sidebar_toggle_box.get_tooltip_text()))
    check("the toggle still reads shown (its word persists)", page.sidebar_wanted and "sidebar" not in page.page_state())
    page.set_size_request(900, -1)  # the toplevel grows to its child's minimum
    wide = wait_for(lambda: not page._narrow and page.sidebar_shown)
    check("a 900 px page shows the sidebar again", wide, (page._narrow, page.sidebar_shown))
    check("the toggle is sensitive again", page._sidebar_toggle.get_sensitive())
    if native:
        check(
            "the view keeps its width beside the sidebar",
            wait_for(lambda: page.diff_view.get_width() >= 400),
            page.diff_view.get_width(),
        )
    else:
        check(
            "the VTE keeps its columns beside the sidebar",
            wait_for(lambda: page.terminal.get_column_count() >= 48),
            page.terminal.get_column_count(),
        )

    # -- the toggle and its persistence -------------------------------------------------
    page.set_sidebar_wanted(False)
    check("the toggle hides the sidebar", not page.sidebar_shown)
    check("page_state says sidebar: false", page.page_state().get("sidebar") is False, page.page_state())
    page.set_sidebar_wanted(True)
    check("and shows it again, dropping the key", page.sidebar_shown and "sidebar" not in page.page_state())
    restored = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
        on_closed=lambda p: None,
        sidebar=hunkctl.decode_sidebar({"kind": "git", "loaded": "unstaged", "sidebar": False}),
    )
    check(
        "a page restored with sidebar: false keeps it hidden and says so before it is shown",
        not restored.sidebar_wanted and restored.page_state() == {"kind": "git", "loaded": "unstaged", "sidebar": False},
        restored.page_state(),
    )

    # -- the commits list off the real repository ---------------------------------------
    current = log_shas(repo, "main..HEAD")
    trunk = log_shas(repo, "main")
    landed = wait_for(lambda: [r.sha for r in sidebar.commit_rows() if r.kind == "commit" and r.group == "current"] == current)
    rows = sidebar.commit_rows()
    check("the current group lists main..HEAD, newest first", landed, [r.label for r in rows])
    check(
        "header feat, then the working tree row",
        len(rows) > 1 and rows[0].kind == "header" and rows[0].label == "feat" and rows[1].kind == "worktree",
        [(r.kind, r.label) for r in rows[:2]],
    )
    check(
        "the default group: header main and its commits",
        any(r.kind == "header" and r.group == "default" and r.label == "main" for r in rows)
        and [r.sha for r in rows if r.kind == "commit" and r.group == "default"] == trunk,
        [(r.kind, r.group, r.label) for r in rows],
    )
    check("no stack group while the parent is the default", not any(r.group.startswith("stack:") for r in rows))
    check("no ↑ without a remote", not any(r.unpushed for r in rows))
    check("no load more… under a page of 20", not any(r.kind == "more" for r in rows))
    check("the working tree row is the loaded one", sidebar.loaded_row_id() == "worktree", sidebar.loaded_row_id())
    check(
        "the loaded row wears the mark, its group's header the highlight",
        sidebar._commit_widgets["worktree"].has_css_class("git-row-loaded")
        and sidebar._commit_widgets["header:current"].has_css_class("git-group-loaded"),
    )

    # -- a commit row click loads it; the default header loads nothing --------------------
    def shows(loaded, args: list) -> bool:
        """The page shows *loaded*: hunk's state file records *args*, or
        the native page says so itself."""
        if native:
            return page.shows(loaded) and page.settled()
        return read_state(state_path).get("args") == args and settled(page)

    sha = current[0]
    subject = git_out(repo, "log", "-1", "--format=%s", sha).strip()
    check("the commit row is drawn", sidebar.click_commit_row(f"commit:{sha}"))
    landed = wait_for(lambda: shows({"show": sha}, ["show", sha]))
    check("a commit row click loads `show <sha>`", landed, page.loaded)
    check("the ▸ row follows the load", wait_for(lambda: sidebar.loaded_row_id() == f"commit:{sha}"), sidebar.loaded_row_id())
    check(
        "the breadcrumb names the commit, <sha7> <subject>",
        wait_for(lambda: page.breadcrumb_text() == f"{sha[:7]} {subject}"),
        page.breadcrumb_text(),
    )
    landed = wait_for(lambda: sidebar.file_rows().mode == "flat" and [f.path for f in sidebar.file_rows().flat] == ["a.txt"])
    check("a commit load lists its files flat, with counts", landed and sidebar.file_rows().flat[0].additions == 1, sidebar.file_rows())
    reloads_before = read_state(state_path).get("reloads", 0)
    sidebar.click_commit_row("header:default")
    wait_for(lambda: False, timeout=0.3)
    check(
        "the default header loads nothing",
        shows({"show": sha}, ["show", sha]) and read_state(state_path).get("reloads", 0) == reloads_before,
        page.loaded,
    )
    sidebar.click_commit_row("header:current")
    landed = wait_for(lambda: shows("branch", ["main...HEAD"]))
    check("the current header loads the branch diff", landed, page.loaded)
    check("the header row is the loaded one", wait_for(lambda: sidebar.loaded_row_id() == "header:current"))

    # -- the files list on the working tree: the other side's click loads it ------------
    with open(os.path.join(repo, "a.txt"), "w") as fh:
        fh.write("staged\n")
    git(repo, "add", "a.txt")  # the index differs from HEAD: a.txt is on the staged side
    with open(os.path.join(repo, "a.txt"), "w") as fh:
        fh.write("staged\nand more\n")  # and the tree from the index: on the unstaged side too
    sidebar.click_commit_row("worktree")
    landed = wait_for(lambda: shows("unstaged", []))
    check("the working tree row loads the unstaged changes", landed, page.loaded)
    landed = wait_for(
        lambda: sidebar.file_rows().mode == "split"
        and sidebar.file_rows().live == "unstaged"
        and [f.path for f in sidebar.file_rows().staged] == ["a.txt"]
        and [f.path for f in sidebar.file_rows().unstaged] == ["a.txt"]
    )
    check("the working tree splits: the viewer's files live, the other side off git status", landed, sidebar.file_rows())
    check(
        "the live row carries counts and the status letter, the other side its letter alone",
        sidebar.file_rows().unstaged[0].live
        and sidebar.file_rows().unstaged[0].code == "M"
        and not sidebar.file_rows().staged[0].live
        and sidebar.file_rows().staged[0].code == "M",
        sidebar.file_rows(),
    )
    check(
        "the viewer's cursor puts the highlight on its file",
        wait_for(lambda: sidebar.selected_path == "a.txt"),
        sidebar.selected_path,
    )
    check("the live row is highlighted", sidebar._file_widgets[("unstaged", "a.txt")].has_css_class("git-file-selected"))
    patch_state(state_path, navigate=None, navigates=0)
    reloads_before = read_state(state_path).get("reloads", 0)
    check("the staged-side row is drawn", sidebar.click_file_row("a.txt", "staged"))
    if native:
        landed = wait_for(lambda: shows("staged", []) and page.diff_view.current()[0] == "a.txt")
        check("a staged-side click loads --staged, then reveals the file", landed, (page.loaded, page.diff_view.current()))
        focus = window.get_focus()
        check("the keyboard landed in the view", focus is not None and focus.is_ancestor(page.diff_view), focus)
    else:
        landed = wait_for(
            lambda: read_state(state_path).get("args") == ["--staged"]
            and read_state(state_path).get("navigate", {}).get("file") == "a.txt"
            and settled(page)
        )
        check("a staged-side click reloads --staged, then navigates to the file", landed, read_state(state_path))
        check("one reload, one navigate", read_state(state_path).get("reloads", 0) == reloads_before + 1 and read_state(state_path).get("navigates") == 1, read_state(state_path))
        check("the navigate asked for the file's first hunk", read_state(state_path).get("navigate", {}).get("target") == "--hunk")
    check("the staged side is live now", wait_for(lambda: sidebar.file_rows().live == "staged"), sidebar.file_rows())
    reloads_before = read_state(state_path).get("reloads", 0)
    sidebar.click_file_row("a.txt", "staged")
    if native:
        wait_for(lambda: False, timeout=0.3)
        check("a live-side click reveals without a reload", shows("staged", []) and page.diff_view.current()[0] == "a.txt", page.loaded)
    else:
        landed = wait_for(lambda: read_state(state_path).get("navigates") == 2 and settled(page))
        check("a live-side click navigates without a reload", landed and read_state(state_path).get("reloads", 0) == reloads_before, read_state(state_path))
    sidebar.click_section("unstaged")
    landed = wait_for(lambda: shows("unstaged", []))
    check("the other side's heading loads that side", landed, page.loaded)
    if not native:
        check("with no navigate", read_state(state_path).get("navigates") == 2)

    if native:
        # -- no hunk: the cursor buttons (hunk's keys) are drawn but off ------------------
        check(
            "the cursor buttons are insensitive with no hunk to feed",
            not sidebar._stage_button.get_sensitive()
            and not sidebar._anchor_button.get_sensitive()
            and not sidebar._discard_button.get_sensitive(),
        )
        check(
            "stage all, unstage all and commit are live on the working tree",
            sidebar._stage_all_button.get_sensitive() and sidebar._commit_button.get_sensitive(),
        )
    else:
        # -- the session get snapshot moves the highlight within a tick ---------------------
        os.environ["FAKE_HUNK_FILES"] = "a.txt,b.txt"
        try:
            patch_state(state_path, navigate={"file": "b.txt", "target": "--hunk", "value": "1"})
            page.poll_tick()
            check("a tick reads hunk's cursor off the session get", wait_for(lambda: sidebar.selected_path == "b.txt"), sidebar.selected_path)
            check("the files list follows hunk's files", wait_for(lambda: [f.path for f in sidebar.file_rows().unstaged] == ["a.txt", "b.txt"]), sidebar.file_rows())
        finally:
            del os.environ["FAKE_HUNK_FILES"]
        patch_state(state_path, navigate=None)
        page.poll_tick()
        wait_for(lambda: sidebar.selected_path == "a.txt" and [f.path for f in sidebar.file_rows().unstaged] == ["a.txt"])

        # -- the sidecar's selection and anchor: the highlight and the button labels -----------
        sidecar = read_state(state_path).get("sidecar")
        check("the cursor buttons are drawn (the extension is there)", sidebar._action_children[sidebar._stage_button].get_visible())
        check(
            "the cursor buttons are sensitive on a live working tree",
            sidebar._stage_button.get_sensitive() and sidebar._anchor_button.get_sensitive() and sidebar._discard_button.get_sensitive(),
        )
        check("no anchor: Anchor line / Stage hunk", sidebar.anchor_button_label() == "Anchor line" and sidebar.stage_button_label() == "Stage hunk")
        write_sidecar(sidecar, selection={"path": "b.txt", "hunkIndex": 2}, anchor={"path": "a.txt", "side": "new", "line": 1})
        page.poll_tick()
        check("the sidecar's selection moves the highlight at once", sidebar.selected_path == "b.txt", sidebar.selected_path)
        check("the sidecar's anchor relabels the buttons", sidebar.anchor_button_label() == "Clear anchor" and sidebar.stage_button_label() == "Stage lines", (sidebar.anchor_button_label(), sidebar.stage_button_label()))
        wait_for(lambda: settled(page))
        check("the session get this tick didn't overwrite the sidecar's selection", sidebar.selected_path == "b.txt", sidebar.selected_path)

        # -- the four buttons feed their bytes --------------------------------------------------
        sidebar._stage_button.emit("clicked")
        landed = wait_for(lambda: read_state(state_path).get("keys") == "x")
        check("Stage lines fed hunk `x`", landed, read_state(state_path).get("keys"))
        check("the VTE took the keyboard", window.get_focus() is page.terminal, window.get_focus())
        sidebar._anchor_button.emit("clicked")
        landed = wait_for(lambda: read_state(state_path).get("keys") == "x\x1b")
        check("Clear anchor fed escape", landed, read_state(state_path).get("keys"))
        write_sidecar(sidecar, selection={"path": "a.txt", "hunkIndex": 0}, anchor=None)
        page.poll_tick()
        check("the anchor cleared: Anchor line / Stage hunk again", sidebar.anchor_button_label() == "Anchor line" and sidebar.stage_button_label() == "Stage hunk")
        sidebar._anchor_button.emit("clicked")
        landed = wait_for(lambda: read_state(state_path).get("keys") == "x\x1bv")
        check("Anchor line fed `v`", landed, read_state(state_path).get("keys"))
        sidebar._discard_button.emit("clicked")
        landed = wait_for(lambda: read_state(state_path).get("keys") == "x\x1bvD")
        check("Discard fed `D`", landed, read_state(state_path).get("keys"))
        wait_for(lambda: settled(page))

    # -- native mutations: stage all, commit --------------------------------------------------
    # Each reloads the viewer exactly once: hunk's state file counts its
    # reloads; the native page's read is counted through its own method.
    native_loads: list[object] = []
    if native:
        original_load = page._native_load

        def counted_load(loaded) -> None:
            native_loads.append(loaded)
            original_load(loaded)

        page._native_load = counted_load

    def reloads() -> int:
        return len(native_loads) if native else read_state(state_path).get("reloads", 0)

    reloads_before = reloads()
    sidebar.stage_all()
    landed = wait_for(lambda: not sidebar.busy and reloads() == reloads_before + 1 and (page.settled() if native else settled(page)))
    check("stage_all reloads the viewer once", landed, reloads())
    check("and staged the tree", git_out(repo, "diff", "--name-only") == "" and git_out(repo, "diff", "--cached", "--name-only").split() == ["a.txt"])
    if native:
        check("the unstaged view emptied", wait_for(lambda: page.diff_view.file_rows() == []), page.diff_view.file_rows())
        check(
            "the files list moved a.txt to the staged side",
            wait_for(lambda: [f.path for f in sidebar.file_rows().staged] == ["a.txt"] and not sidebar.file_rows().unstaged),
            sidebar.file_rows(),
        )
    page.poll_tick()
    wait_for(lambda: page.settled() if native else settled(page))
    wait_for(lambda: False, timeout=0.3)
    check("the following tick reloads nothing more", reloads() == reloads_before + 1, reloads())
    reloads_before = reloads()
    sidebar.commit("native commit", None)
    landed = wait_for(lambda: not sidebar.busy and reloads() == reloads_before + 1 and (page.settled() if native else settled(page)))
    check("commit reloads the viewer once", landed, reloads())
    check("and made the commit", git_out(repo, "log", "-1", "--format=%s").strip() == "native commit", git_out(repo, "log", "-1", "--format=%s"))
    page.poll_tick()
    wait_for(lambda: page.settled() if native else settled(page))
    wait_for(lambda: False, timeout=0.3)
    check("the following tick reloads nothing more", reloads() == reloads_before + 1, reloads())
    landed = wait_for(lambda: [r.sha for r in sidebar.commit_rows() if r.kind == "commit" and r.group == "current"] == log_shas(repo, "main..HEAD"))
    check("the commits list gained the commit", landed, [r.label for r in sidebar.commit_rows()])

    # -- the page size pages the current group ------------------------------------------------
    while len(log_shas(repo, "main..HEAD")) < 7:
        with open(os.path.join(repo, "a.txt"), "a") as fh:
            fh.write("more\n")
        git(repo, "commit", "-qam", f"filler {len(log_shas(repo, 'main..HEAD'))}")
    page.apply_settings({**(NATIVE_SETTINGS if native else SETTINGS), "git_log_page": 5})
    landed = wait_for(lambda: any(r.kind == "more" and r.group == "current" for r in sidebar.commit_rows()))
    check("git_log_page 5 over 7 commits shows load more…", landed, [(r.kind, r.label) for r in sidebar.commit_rows()])
    check("five commits listed", len([r for r in sidebar.commit_rows() if r.kind == "commit" and r.group == "current"]) == 5)
    sidebar.load_more("current")
    landed = wait_for(
        lambda: [r.sha for r in sidebar.commit_rows() if r.kind == "commit" and r.group == "current"] == log_shas(repo, "main..HEAD")
        and not any(r.kind == "more" and r.group == "current" for r in sidebar.commit_rows())
    )
    check("load more… lists them all and goes away", landed, [(r.kind, r.label) for r in sidebar.commit_rows()])
    wait_for(lambda: page.settled() if native else settled(page))
    page.page_closed()
    wait_for(lambda: not page.hunk_alive, timeout=2.0)
    window.destroy()


def range_settled(view, hold_s: float = 0.3):
    """A wait_for condition: the view's scroll range has held still for
    *hold_s* — the expanded context views have validated their heights."""
    seen = {"upper": None, "since": 0.0}

    def settled() -> bool:
        upper = view._scroller.get_vadjustment().get_upper()
        now = time.monotonic()
        if upper != seen["upper"]:
            seen["upper"], seen["since"] = upper, now
            return False
        return now - seen["since"] >= hold_s

    return settled


def png_bytes(rgb: tuple[int, int, int]) -> bytes:
    """A 4×4 PNG of one colour — a picture git calls binary and the view
    calls an image."""
    import struct
    import zlib

    raw = b"".join(b"\x00" + bytes(rgb) * 4 for _ in range(4))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def write_file(repo: str, name: str, data: bytes | str) -> None:
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(os.path.join(repo, name), mode) as fh:
        fh.write(data)


def stage_native_fixture(repo: str) -> list[str]:
    """Every kind of change the view draws, on a fresh commit: unstaged
    edits (two hunks with gaps around them), a staged edit, an untracked
    file, a staged rename, a modified binary, an image before/after and an
    untracked image, a deleted file and a mode change. Returns text.txt's
    lines (the check edits them again)."""
    lines = [f"line {n}\n" for n in range(1, 61)]
    write_file(repo, "text.txt", "".join(lines))
    write_file(repo, "staged.txt", "".join(f"staged {n}\n" for n in range(1, 11)))
    write_file(repo, "old.txt", "".join(f"old {n}\n" for n in range(1, 6)))
    write_file(repo, "blob.bin", bytes(range(256)) * 4)
    write_file(repo, "pic.png", png_bytes((200, 30, 30)))
    write_file(repo, "gone.txt", "".join(f"gone {n}\n" for n in range(1, 4)))
    write_file(repo, "run.sh", "#!/bin/sh\necho run\n")
    os.chmod(os.path.join(repo, "run.sh"), 0o644)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "native fixture")
    # unstaged: two hunks in text.txt (a 1-line gap before, 33 between, 12 after)
    lines[4] = "line 5 changed\n"
    lines[44] = "line 45 changed\n"
    write_file(repo, "text.txt", "".join(lines))
    # staged: an edit, and a rename
    write_file(repo, "staged.txt", "".join(f"staged {n}\n" for n in range(1, 11)).replace("staged 3", "staged three"))
    git(repo, "add", "staged.txt")
    git(repo, "mv", "old.txt", "renamed.txt")
    # unstaged: a binary, an image, a deletion, a mode change
    write_file(repo, "blob.bin", bytes(range(255, -1, -1)) * 4)
    write_file(repo, "pic.png", png_bytes((30, 200, 30)))
    os.remove(os.path.join(repo, "gone.txt"))
    os.chmod(os.path.join(repo, "run.sh"), 0o755)
    # untracked: a text file and a picture
    write_file(repo, "untracked.txt", "nothing tracked here\n")
    write_file(repo, "new.png", png_bytes((30, 30, 200)))
    return lines


def clear_native_fixture(repo: str) -> None:
    git(repo, "reset", "-q", "--hard")
    git(repo, "clean", "-qfd")


def check_native(repo: str, state_path: str) -> None:
    """The native viewer behind the git_viewer switch (PR 2 of the
    native-diff stack) over a repository holding every kind of change: the
    view opens with no hunk spawned and draws each section kind (badges,
    pictures, a rename on the staged side); a gap expands; the watch
    reloads an external edit within two seconds keeping an untouched
    hunk's widget and the keyboard; the files list follows the scroll and
    a click reveals; the page-local keys are routed; the filter hides
    sections; the find bar counts across hunks; loads switch (staged, a
    commit); settings and the keys reach the view and page_state
    round-trips a layout change; a page restores into a commit (and into
    the default mode for a commit git no longer has); the switch flips
    live both ways."""
    print("-- the native viewer")
    lines = stage_native_fixture(repo)
    fixture_sha = head_sha(repo)
    page = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
        on_closed=lambda p: None,
    )
    page.apply_settings(NATIVE_SETTINGS)
    check("the page reads the switch before it maps", page.native and not page.hunk_alive)
    opened: list[tuple[str, int]] = []
    page.diff_view.connect("open-requested", lambda _v, path, line: opened.append((path, line)))
    window = Gtk.Window(title="native", default_width=900, default_height=600)
    window.set_child(page)
    window.present()
    check("the view opens and the first read lands", wait_for(page.settled))
    check(
        "the stack shows the native view; no hunk was spawned",
        page._stack.get_visible_child_name() == "native" and page._child_pid is None and page.card is None,
        (page._stack.get_visible_child_name(), page._child_pid, page.card),
    )
    check(
        "the header's find and menu show, and the sidebar's filter",
        page._find_toggle.get_visible()
        and page._menu_button.get_visible()
        and page.sidebar._filter_entry.get_visible(),
    )
    check("the breadcrumb reads the working tree", page.breadcrumb_text() == "working tree · unstaged")
    check("nothing to reveal for a file that isn't loaded", not page.reveal("nowhere.txt"))

    # -- every section kind renders -----------------------------------------------------
    view = page.diff_view
    kinds = {path: kind for path, kind, _shown in view.file_rows()}
    check(
        "the unstaged load draws a change, a binary, an image, a deletion, a mode change and the untracked files (a picture reads binary)",
        kinds == {
            "text.txt": "change",
            "blob.bin": "binary",
            "pic.png": "binary",
            "gone.txt": "deleted",
            "run.sh": "mode",
            "untracked.txt": "new",
            "new.png": "binary",
        },
        kinds,
    )
    badges = {label: (badge, picture) for label, badge, picture in view.badge_rows()}
    check(
        "the headers wear their badges; the images their pictures",
        badges.get("blob.bin") == ("binary", False)
        and badges.get("pic.png") == ("binary", True)
        and badges.get("gone.txt") == ("deleted", False)
        and badges.get("run.sh") == ("mode 100644 → 100755", False)
        and badges.get("text.txt") == ("", False)
        and badges.get("untracked.txt") == ("untracked", False)
        and badges.get("new.png") == ("untracked · binary", True),
        badges,
    )
    check("text.txt draws its two hunks", len(view.hunk_rows("text.txt")) == 2, view.hunk_rows("text.txt"))
    check(
        "the deleted file's one hunk holds its lines, the untracked file's its own",
        len(view.hunk_rows("gone.txt")) == 1 and len(view.hunk_rows("untracked.txt")) == 1,
        (view.hunk_rows("gone.txt"), view.hunk_rows("untracked.txt")),
    )
    check("a binary and a mode change have no hunk", view.hunk_rows("blob.bin") == [] and view.hunk_rows("run.sh") == [])
    check(
        "the files list lists the unstaged side from the read's own status, the staged from git status",
        wait_for(
            lambda: {r.path for r in page.sidebar.file_rows().unstaged} >= {"text.txt", "blob.bin", "gone.txt", "untracked.txt"}
            and {r.path for r in page.sidebar.file_rows().staged} == {"staged.txt", "renamed.txt"}
        ),
        page.sidebar.file_rows(),
    )
    first = view.file_rows()[0][0]
    check(
        "the sidebar highlights the file at the top of the view",
        wait_for(lambda: page.sidebar.selected_path == first),
        (page.sidebar.selected_path, first),
    )

    # -- a gap expands -----------------------------------------------------------------
    gaps = dict((address, (remaining, shown)) for address, remaining, shown in view.gap_rows("text.txt"))
    check(
        "the gaps before, between and after the hunks are measured",
        gaps.get("before:0") == (1, 0) and gaps.get("before:1") == (33, 0) and "trailing:1" in gaps,
        gaps,
    )
    check("▼ 20 on the gap between the hunks", view.expand_gap("text.txt", "before:1", "down", 20))
    check(
        "twenty lines of context are drawn, thirteen remain",
        wait_for(lambda: dict((a, (r, s)) for a, r, s in view.gap_rows("text.txt")).get("before:1") == (13, 20)),
        view.gap_rows("text.txt"),
    )
    check("`all` on the trailing gap", view.expand_gap("text.txt", "trailing:1", "all", 0))
    check(
        "the trailing gap is measured off the file and drawn whole",
        wait_for(lambda: dict((a, (r, s)) for a, r, s in view.gap_rows("text.txt")).get("trailing:1") == (0, 12)),
        view.gap_rows("text.txt"),
    )

    # -- a files-list click reveals and focuses ------------------------------------------
    page.sidebar.click_file_row("text.txt", "unstaged")
    check(
        "a files-list click focuses the file's first hunk",
        wait_for(lambda: view.current()[:2] == ("text.txt", 0)),
        view.current(),
    )
    focus = window.get_focus()
    check("the keyboard is in the view", focus is not None and focus.is_ancestor(view), focus)
    check("the highlight followed the click", page.sidebar.selected_path == "text.txt", page.sidebar.selected_path)

    # -- the watch: an external edit reloads within 2 s, an untouched hunk keeps its widget --
    ids_before = view.hunk_serials("text.txt")
    focus_before = window.get_focus()
    lines[44] = "line 45 changed again\n"
    write_file(repo, "text.txt", "".join(lines))
    started = time.monotonic()
    landed = wait_for(lambda: view.hunk_serials("text.txt")[1:] != ids_before[1:] and page.settled(), timeout=2.0)
    elapsed = time.monotonic() - started
    check(f"an edit reloads the view through the watch within 2 s ({elapsed:.1f} s)", landed)
    ids_after = view.hunk_serials("text.txt")
    check("the edited hunk was rebuilt, the untouched one kept its widget", len(ids_after) == 2 and ids_after[0] == ids_before[0] and ids_after[1] != ids_before[1], (ids_before, ids_after))
    check("the keyboard stayed in the untouched hunk", window.get_focus() is focus_before and view.current()[:2] == ("text.txt", 0), (window.get_focus(), view.current()))
    check("the file's badge is still none and its hunks two", len(view.hunk_rows("text.txt")) == 2)

    # -- the page-local keys, through their actions ----------------------------------------
    check("git.next-hunk is routed to the view", view.activate_action("git.next-hunk", None))
    check("and moved the current hunk", wait_for(lambda: view.current()[:2] == ("text.txt", 1)), view.current())
    view.activate_action("git.open-editor", None)
    check(
        "`e` asks for the file at the cursor's line",
        bool(opened) and opened[-1][0] == "text.txt" and opened[-1][1] >= 1,
        opened,
    )
    check("git.expand-gap draws the gap above the focused hunk", view.activate_action("git.expand-gap", None))
    check(
        "the gap between the hunks is spent",
        wait_for(lambda: any(a == "before:1" and remaining == 0 for a, remaining, _s in view.gap_rows("text.txt"))),
        view.gap_rows("text.txt"),
    )
    check("git.prev-file moves to the file before", view.activate_action("git.prev-file", None) and view.current()[0] != "text.txt", view.current())

    # -- the sidebar highlight follows the scroll ----------------------------------------------
    order = [path for path, _k, _s in view.file_rows()]
    window.set_focus(None)  # a focused hunk still in view would hold the highlight
    # The context views just drawn validate their heights a beat after
    # allocation (the column's range grows then): scroll once the range
    # has held still, or "the end" is the end of a shorter column.
    check("the column's range settles", wait_for(range_settled(view)))
    view.set_scroll(1.0)
    check(
        "scrolled to the end, the highlight moves to a later file",
        wait_for(lambda: page.sidebar.selected_path is not None and order.index(page.sidebar.selected_path) > 0),
        (page.sidebar.selected_path, order),
    )
    check(
        "the pinned header names the file scrolled into",
        wait_for(lambda: view.pinned_header_text() == "text.txt"),
        view.pinned_header_text(),
    )
    view.set_scroll(0.0)
    check("scrolled back, the first file again", wait_for(lambda: page.sidebar.selected_path == order[0]), page.sidebar.selected_path)

    # -- the files filter ------------------------------------------------------------------
    # (A GtkSearchEntry's search-changed is debounced: the words land a
    # beat after the text does, so every read below waits for them.)
    page.sidebar.set_filter_text("zzz")
    check(
        "the filter hides the sections and rows that don't match",
        wait_for(
            lambda: all(not shown for _p, _k, shown in view.file_rows())
            and all(not w.get_visible() for w in page.sidebar._file_widgets.values())
        ),
        view.file_rows(),
    )
    page.sidebar.set_filter_text("text")
    check(
        "and shows what does",
        wait_for(lambda: [p for p, _k, shown in view.file_rows() if shown] == ["text.txt"]),
        view.file_rows(),
    )
    page.sidebar.set_filter_text("")
    check("clearing shows every section", wait_for(lambda: all(shown for _p, _k, shown in view.file_rows())))

    # -- the find bar -------------------------------------------------------------------------
    page._search_bar.set_search_mode(True)
    page._search_entry.set_text("changed")
    check(
        "the find bar counts the matches across both hunks",
        wait_for(lambda: page._search_label.get_text() == "1 of 2"),
        page._search_label.get_text(),
    )
    check("the first match put the current hunk on the first", view.current()[:2] == ("text.txt", 0), view.current())
    page._search_entry.emit("next-match")
    check("Enter steps to the next hunk's match", page._search_label.get_text() == "2 of 2", page._search_label.get_text())
    check("the current hunk followed the match", view.current()[:2] == ("text.txt", 1), view.current())
    page._search_entry.emit("previous-match")
    check("Shift+Enter steps back", page._search_label.get_text() == "1 of 2", page._search_label.get_text())
    page._search_entry.set_text("nowhere")
    check("no match says so", wait_for(lambda: page._search_label.get_text() == "No matches"), page._search_label.get_text())
    page._search_bar.set_search_mode(False)
    check("closing the bar clears the label", page._search_label.get_text() == "")
    check("Escape is not held with the bar closed", not page.holds_escape())

    # -- other loads: the index (a rename), a commit -------------------------------------------
    page.load("staged")
    check("Ctrl+2 loads the index", wait_for(lambda: page.settled() and page.loaded == "staged"))
    check("the breadcrumb says staged", page.breadcrumb_text() == "working tree · staged", page.breadcrumb_text())
    check(
        "the files list shows the staged side live",
        wait_for(lambda: any(r.path == "staged.txt" and r.live for r in page.sidebar.file_rows().staged)),
        page.sidebar.file_rows(),
    )
    kinds = {path: kind for path, kind, _shown in view.file_rows()}
    check("the index shows the edit and the rename", kinds == {"staged.txt": "change", "renamed.txt": "rename"}, kinds)
    badges = {label: badge for label, badge, _p in view.badge_rows()}
    check("the rename reads `old → new` and its similarity", badges.get("old.txt → renamed.txt") == "renamed 100%", badges)
    check("a pure rename has no hunk", view.hunk_rows("renamed.txt") == [])
    check("no matches remain from the other side", page._search_label.get_text() == "")
    page.load({"show": fixture_sha})
    check("a commit load lands", wait_for(lambda: page.settled() and page.shows({"show": fixture_sha})))
    check(
        "the breadcrumb names the commit off the read's own git log",
        page.breadcrumb_text() == f"{fixture_sha[:7]} native fixture" and page._resolved_sha == fixture_sha,
        (page.breadcrumb_text(), page._resolved_sha),
    )
    kinds = {path: kind for path, kind, _shown in view.file_rows()}
    check(
        "the commit's files are drawn, its new files as new",
        kinds.get("text.txt") == "new" and kinds.get("pic.png") == "binary" and kinds.get("old.txt") == "new",
        kinds,
    )
    check("a picture added by the commit shows its one side", dict((label, p) for label, _b, p in view.badge_rows()).get("pic.png") is True)
    check("no monitors on a commit load", page._monitors == [])
    page.load("unstaged")
    check("back to the working tree", wait_for(lambda: page.settled() and page.loaded == "unstaged"))
    check("monitors are back", page._monitors != [])
    check("page_state carries the load", page.page_state() == {"kind": "git", "loaded": "unstaged"}, page.page_state())

    # -- settings and the keys that write them; page_state round-trips a layout change ----------
    page.apply_settings({**NATIVE_SETTINGS, "git_wrap_lines": True, "git_layout": "split", "git_line_numbers": False})
    check(
        "wrap, layout and line numbers reach the view",
        view.options.wrap and view.is_split() and not view.options.line_numbers,
        view.options,
    )
    check("the menu's states follow", page._wrap_action.get_state().get_boolean() and page._layout_action.get_state().get_string() == "split")
    check("a layout change leaves page_state alone (the layout is a preference)", page.page_state() == {"kind": "git", "loaded": "unstaged"}, page.page_state())
    view.activate_action("git.layout-stack", None)
    check("the `2` key stacks the layout (applied to the page with no window action)", not view.is_split())
    view.activate_action("git.line-numbers", None)
    check("`l` toggles the line numbers back on", view.options.line_numbers, view.options)
    restored = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
        on_closed=lambda p: None,
        loaded=hunkctl.decode_state(page.page_state()),
    )
    restored.apply_settings({**NATIVE_SETTINGS, "git_layout": "split"})
    check("a page restored from page_state reads the same state back", restored.page_state() == page.page_state(), restored.page_state())
    check("and takes its layout from the setting before it maps", restored.diff_view.options.split)
    page.apply_settings(NATIVE_SETTINGS)

    # -- the switch flips live, both ways ----------------------------------------------------------
    page.apply_settings({**NATIVE_SETTINGS, "git_viewer": "hunk"})
    check("flipping to hunk spawns the viewer", wait_for(lambda: page._session_id is not None))
    check(
        "the stack shows hunk and the native chrome hides",
        page._stack.get_visible_child_name() == "hunk"
        and not page._find_toggle.get_visible()
        and not page.sidebar._filter_entry.get_visible()
        and not page.native,
    )
    pid = read_state(state_path).get("pid")
    page.apply_settings(NATIVE_SETTINGS)
    check("flipping back takes hunk down and opens the view", wait_for(lambda: page.native and page.settled() and page._child_pid is None))
    check("the old viewer went down", wait_for(lambda: not pid_alive(pid)), pid)
    check("the view shows the working tree again", page._stack.get_visible_child_name() == "native" and page.loaded == "unstaged")

    # -- unparented for good, the view closes; re-parented, it stays ---------------------------------
    window.set_child(None)  # a drag to another strip: unrealized, then realized again
    window.set_child(page)
    wait_for(lambda: False, timeout=0.3)
    check("a re-parented page keeps its view and monitors", page._native_opened and page._monitors != [])
    page.page_closed()
    window.destroy()
    check("closing dropped the monitors", page._monitors == [])

    # -- restored from a layout: a commit, and a commit git no longer has -----------------------
    print("-- the native viewer restored from a layout")
    page = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
        on_closed=lambda p: None,
        loaded=hunkctl.decode_state({"kind": "git", "loaded": {"show": fixture_sha}, "parent": "base"}),
    )
    page.apply_settings(NATIVE_SETTINGS)
    check(
        "page_state before the open: the load, and no parent",
        page.page_state() == {"kind": "git", "loaded": {"show": fixture_sha}},
        page.page_state(),
    )
    check("no subject before the open", page.breadcrumb_text() == f"commit {fixture_sha[:7]}", page.breadcrumb_text())
    window = Gtk.Window(title="restore (native)", default_width=900, default_height=600)
    window.set_child(page)
    window.present()
    check("the view opens into the saved commit", wait_for(lambda: page.settled() and page.shows({"show": fixture_sha})))
    check("no hunk was spawned for it", page._child_pid is None and page.card is None)
    check("breadcrumb reads <sha7> <subject>", page.breadcrumb_text() == f"{fixture_sha[:7]} native fixture", page.breadcrumb_text())
    check("the tab title stays short", page.page_title() == f"Git · {fixture_sha[:7]}", page.page_title())
    check("the ▸ row is the commit's", wait_for(lambda: page.sidebar.loaded_row_id() == f"commit:{fixture_sha}"), page.sidebar.loaded_row_id())
    page.page_closed()
    window.destroy()
    gone = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    page = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
        on_closed=lambda p: None,
        loaded=hunkctl.decode_state({"kind": "git", "loaded": {"show": gone}}),
    )
    page.apply_settings(NATIVE_SETTINGS)
    window = Gtk.Window(title="restore (native, gone commit)", default_width=900, default_height=600)
    window.set_child(page)
    window.present()
    check("a commit git doesn't know opens the default mode", wait_for(lambda: page.settled() and page.loaded == "unstaged"), page.loaded)
    check("no card", page._stack.get_visible_child_name() == "native" and page.card is None)
    page.page_closed()
    window.destroy()
    clear_native_fixture(repo)


def check_without_hunk(repo: str) -> None:
    """With no hunk on PATH: the install card for the hunk viewer, and the
    native viewer drawing regardless (it needs git alone)."""
    print("-- with no hunk on PATH")
    page = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
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

    page = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
        on_closed=lambda p: None,
    )
    page.apply_settings(NATIVE_SETTINGS)
    window = Gtk.Window(title="check_git_page (no hunk, native)", default_width=900, default_height=600)
    window.set_child(page)
    window.present()
    check("the native viewer opens with no hunk anywhere", wait_for(page.settled))
    check("no card stands in for it", page._stack.get_visible_child_name() == "native" and page.card is None, (page._stack.get_visible_child_name(), page.card))
    check("the breadcrumb reads the working tree", page.breadcrumb_text() == "working tree · unstaged", page.breadcrumb_text())
    page.page_closed()
    window.destroy()


def check_outside_a_repo(scratch: str) -> None:
    print("-- outside a repository")
    nowhere = os.path.join(scratch, "nowhere")
    os.mkdir(nowhere)
    for native in (False, True):
        page = GitPage(
            cwd_provider=lambda: nowhere,
            parent_provider=lambda _cwd: None,
            on_closed=lambda p: None,
        )
        if native:
            page.apply_settings(NATIVE_SETTINGS)
        window = Gtk.Window(title="check_git_page (no repo)", default_width=900, default_height=600)
        window.set_child(page)
        window.present()
        shown = wait_for(lambda p=page: p._stack.get_visible_child_name() == "card")
        viewer = "native" if native else "hunk"
        check(
            f"the not-a-repository card comes up ({viewer})",
            shown and card_title(page) == "Not a git repository",
            card_title(page),
        )
        check(f"no parent to name ({viewer})", page._parent_target is None)
        check(f"no sidecar was written ({viewer})", not os.path.exists(page._sidecar))
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
            check_settings(repo, state_path)
            check_sidebar(repo, state_path)
            check_sidebar(repo, state_path, native=True)
            check_native(repo, state_path)
            check_teardown_paths(repo, state_path, shim)
        finally:
            os.environ["PATH"] = real_path

        # git alone: the hunk viewer's install card, the native viewer's diff
        gitonly = os.path.join(scratch, "gitonly")
        os.mkdir(gitonly)
        os.symlink(GIT, os.path.join(gitonly, "git"))
        os.environ["PATH"] = gitonly
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
