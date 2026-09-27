#!/usr/bin/env python3
"""End-to-end check for the sandboxed session's chip, shell and tool policy.

A real App with a real window, a session launched sandboxed through a
*fake* bubblewrap (COLLINS_BWRAP: it records the NUL-separated argument
payload sandboxrun feeds it over the --args fd, then execs the command),
so the seams are proven without a user namespace: the footer chip appears
on the sandboxed tab and reads the launched plan back; run_in_terminal /
read_terminal from the sandboxed tab reach only a *Sandboxed shell* (a
PanelTerminal spawned through the same launcher), never the user's own
shell, which Ctrl+J keeps to itself; a grant is refused by the guard or
recorded, makes the launched plan stale so the chip offers *Restart to
apply*, and the restart resumes the session in the same shell with a plan
carrying the grant; a sibling from the sandboxed tab inherits the parent's
plan derived for its directory and a box of its own, and one outside the
box is refused. Every session has a box (its own `$HOME`): minted at the
launch, recorded against the session id when it resolves, kept across the
restart, and removed when the session's transcript is forgotten.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \
        python3 scripts/check_sandbox_policy.py

Staged under ~/.cache rather than /tmp: /tmp is shared read-write into every
box, and a scratch tree there would carry Collins' own state inside — the
plan's protect-check refuses exactly that. HOME is moved into the scratch
tree too, so the launch's RW_HOME_ALWAYS directories and the seeded sandbox
home never touch the real one.
"""

import atexit
import os
import shutil
import signal
import subprocess
import sys
import tempfile

REAL_HOME = os.path.expanduser("~")
STAGE = os.path.join(REAL_HOME, ".cache", "collins-e2e")
os.makedirs(STAGE, exist_ok=True)
E2E = tempfile.mkdtemp(prefix="sandbox-policy-", dir=STAGE)


def mounts_under(tree: str) -> list[str]:
    """Every mount point at or under *tree*, deepest first."""
    found = []
    with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            fields = line.split(" ")
            if len(fields) > 4:
                point = fields[4].replace("\\040", " ")
                if point == tree or point.startswith(tree + "/"):
                    found.append(point)
    return sorted(found, key=len, reverse=True)


def clear_tree() -> None:
    """Remove the scratch tree — never through a mount: a bindfs mount in
    it *is* the granted directory. Whatever is still mounted is unmounted
    first, and a tree that still holds a mount is left where it is."""
    for point in mounts_under(E2E):
        subprocess.run(["fusermount3", "-u", "-z", point], check=False, capture_output=True)
    left = mounts_under(E2E)
    if left:
        print(f"not removing {E2E}: still mounted: {left}", file=sys.stderr)
        return
    shutil.rmtree(E2E, True)


# ~/.cache outlives the run, so the tree goes however the check ends: an
# exception while staging, a failed check, a clean finish. The watchdog at
# the bottom leaves through os._exit, which skips this, and clears it first.
atexit.register(clear_tree)
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())
HOME = f"{E2E}/home"

os.environ["HOME"] = HOME
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["COLLINS_SANDBOX_ROOT"] = f"{E2E}/sbx"
os.environ["COLLINS_BWRAP"] = f"{E2E}/bin/bwrap"
# No bindfs for the first pass: a grant waits for the restart, as it does on
# a machine that can't deliver one live. The last stage takes this away.
os.environ["COLLINS_BINDFS"] = "/nonexistent"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"
os.environ["XDG_CACHE_HOME"] = f"{E2E}/cache"
os.environ["XDG_DATA_HOME"] = f"{HOME}/.local/share"
os.environ["SANDBOX_FAKE_LOG"] = f"{E2E}/bwrap.log"
os.environ.pop("TMPDIR", None)
os.environ.pop("SSH_AUTH_SOCK", None)
os.environ.pop("DOCKER_HOST", None)
for var in list(os.environ):
    if var.startswith("CLAUDE_"):
        del os.environ[var]

# The session id the transcript resolver would bind to the tab once the CLI
# mints one: the restart resumes *this* conversation rather than starting a
# new one in the same tab.
RESUMED = "11111111-2222-3333-4444-555555555555"
TRUSTED = f"{E2E}/dev/alpha"
SUB = f"{TRUSTED}/sub"
OTHER = f"{E2E}/dev/lib"
LIVE = f"{HOME}/dev/live"  # under $HOME: what a live grant can link at its real path
SHIM = f"{E2E}/bin/claude"
FAKE_BWRAP = os.environ["COLLINS_BWRAP"]
FAKE_LOG = os.environ["SANDBOX_FAKE_LOG"]

for path in (f"{E2E}/projects", f"{E2E}/chats", f"{E2E}/bin", HOME, SUB, OTHER, LIVE, f"{HOME}/.ssh"):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
os.makedirs(f"{E2E}/config/collins", exist_ok=True)
with open(f"{E2E}/config/collins/state.json", "w", encoding="utf-8") as fh:
    fh.write(
        '{"settings": {"welcome_seen": true, "gh_welcome_dismissed": true, '
        '"title_model": "none", "sandbox_new_sessions": true}}'
    )

# The CLI stand-in: draws the idle prompt, holds the terminal, and leaves on
# Ctrl+C — the restart's graceful exit — so the shell gets the terminal back.
_SHIM = r"""#!/usr/bin/env python3
import os, sys, tty
sys.stdout.write("❯ ")  # the CLI's idle prompt: ❯ + no-break space
sys.stdout.flush()
tty.setraw(0)
while True:
    data = os.read(0, 64)
    if not data or b"\x03" in data:
        break
"""
with open(SHIM, "w", encoding="utf-8") as fh:
    fh.write(_SHIM)
os.chmod(SHIM, 0o755)

# The fake bubblewrap: `bwrap --args <fd> -- cmd…` — dump the payload the
# launcher fed over the fd, one launch per blank-line-separated block, then
# run the command in place.
_FAKE = r"""#!/usr/bin/env python3
import os, sys
if len(sys.argv) < 3 or sys.argv[1] != "--args":
    sys.exit(0)  # the launch probe: `bwrap --unshare-user … -- /bin/true`
fd = int(sys.argv[2])
chunks = []
while True:
    chunk = os.read(fd, 65536)
    if not chunk:
        break
    chunks.append(chunk)
with open(os.environ["SANDBOX_FAKE_LOG"], "ab") as fh:
    fh.write(b"".join(chunks) + b"\n\n")
os.execvp(sys.argv[4], sys.argv[4:])
"""
with open(FAKE_BWRAP, "w", encoding="utf-8") as fh:
    fh.write(_FAKE)
os.chmod(FAKE_BWRAP, 0o755)
os.environ["PATH"] = f"{E2E}/bin:{os.environ['PATH']}"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import GLib, Gtk  # noqa: E402

from collins import i18n, panellayout, sandboxgrants, sandboxplan, terminal, trust  # noqa: E402
from collins.app import App  # noqa: E402
from collins.state import AppState  # noqa: E402

PASSED = 0
FAILED = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  ok  {label}", flush=True)
    else:
        FAILED += 1
        print(f"FAIL  {label}  {detail}", flush=True)


def launches() -> list[list[str]]:
    """Every launch the fake bwrap saw, as its argument list."""
    try:
        with open(FAKE_LOG, "rb") as fh:
            raw = fh.read()
    except OSError:
        return []
    return [
        [a.decode() for a in block.split(b"\0") if a]
        for block in raw.split(b"\n\n")
        if block.strip()
    ]


def after(args: list[str], flag: str) -> list[tuple[str, str]]:
    return [(args[i + 1], args[i + 2]) for i, a in enumerate(args) if a == flag]


def buttons(widget) -> list[Gtk.Button]:
    """Every button under *widget*, depth first."""
    found = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Button):
            found.append(child)
        found.extend(buttons(child))
        child = child.get_next_sibling()
    return found


def labels(widget) -> list[str]:
    found = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            found.append(child.get_text())
        found.extend(labels(child))
        child = child.get_next_sibling()
    return found


i18n.init(AppState().get_setting("language"))
trust.trust_dir(TRUSTED)
app = App()

tries = 0
state: dict = {}


def found():
    return state["win"], state["caller"]


def bail(reason: str) -> bool:
    print(reason, file=sys.stderr)
    app.quit()
    return GLib.SOURCE_REMOVE


def stage() -> bool:
    """Wait for the window and the probe's verdict, then launch the session."""
    global tries
    tries += 1
    win = app.get_active_window()
    if win is None or sandboxplan.probe_reason() is None:
        if tries > 60:
            return bail("timed out waiting for the window / the sandbox probe")
        return GLib.SOURCE_CONTINUE
    check("the fake bwrap passes the probe", sandboxplan.probe_reason() == "")
    state["win"] = win
    # An empty tab view makes the background launch fall through to the
    # foreground; the project default (sandbox_new_sessions) boxes it.
    state["caller"] = win.start_background_session(TRUSTED)
    GLib.timeout_add(3000, launched)
    return GLib.SOURCE_REMOVE


def launched() -> bool:
    caller = state["caller"]
    check("the session launched sandboxed", caller.sandboxed)
    plan_path = caller.sandbox_plan_path
    check("…with a plan file", bool(plan_path) and os.path.isfile(plan_path), plan_path)
    state["plan1"] = plan_path
    check("the chip shows on the sandboxed tab", caller._sandbox_chip.get_visible())
    check("the launch options carry the plan", caller.launch_options.sandbox_plan == plan_path)
    check(
        "…and the box's permission mode",
        caller.launch_options.permission_mode == "bypassPermissions",
        caller.launch_options,
    )
    seen = launches()
    check("the fake bwrap saw the launch", len(seen) == 1, len(seen))
    if seen:
        args = seen[0]
        check("the box chdirs into the workspace", args[args.index("--chdir") + 1] == TRUSTED, args)
        check("the workspace is bound read-write", (TRUSTED, TRUSTED) in after(args, "--bind"), args)
        check("HOME inside is the sandbox home", ("HOME", HOME) in after(args, "--setenv"), args)
        box = caller.sandbox_box
        state["box1"] = box
        check("the tab minted a box for the session", sandboxplan.valid_box_id(box), box)
        check("the launch options carry it", caller.launch_options.sandbox_box == box)
        check(
            "the overlay home comes first, and is the box's own",
            after(args, "--bind")[0] == (f"{E2E}/sbx/{box}/home", HOME),
            after(args, "--bind")[:1],
        )
        check(
            "the box's carrier is bound where live grants arrive",
            (f"{E2E}/sbx/{box}/grants", sandboxplan.CARRIER_DEST) in after(args, "--bind"),
            args,
        )
        remounts = [args[i + 1] for i, a in enumerate(args) if a == "--remount-ro"]
        check("…and remounted read-only", sandboxplan.CARRIER_DEST in remounts, remounts)
        check("the box is on disk", os.path.isdir(f"{E2E}/sbx/{box}/home"))
        lease = sandboxplan.read_lease(box)
        check("…leased to this instance", bool(lease) and lease["pid"] == os.getpid(), lease)
        check(
            "no session names the box before the id resolves",
            box not in AppState().sandbox_boxes(),
            AppState().sandboxed_sessions,
        )

    # The tool policy: a sandboxed session reaches only sandboxed shells.
    got = app._mcp_read_terminal(found(), {}, True)
    check(
        "read from the box with no sandboxed shell says so",
        got == (True, "No sandboxed shells are open in this session."),
        got,
    )
    got = app._mcp_run_in_terminal(found(), {"command": "echo boxed-here"}, True)
    check("run from the box opens a sandboxed shell", got == (True, "Running in new Sandboxed shell 1."), got)
    shells = caller.panel_shells()
    check("the shell page is the sandboxed kind", len(shells) == 1 and shells[0].sandboxed, shells)
    if shells:
        check("…titled as one", shells[0].page_title() == "Sandboxed shell 1", shells[0].page_title())
        check("…wearing the shield", shells[0].page_icon() == "sandbox-shield-symbolic")
        check("…and never focused", not shells[0].has_page_focus())
        saved = {"kind": "shell", "hist": shells[0].hist, "sandboxed": True}
        check("…saved as one", shells[0].page_state() == saved, shells[0].page_state())
    check("Ctrl+J is not bound to the sandboxed shell", caller._dock.panel_terminal is None)
    # The user's own shell, beside it.
    caller.show_panel(focus=False)
    plain = caller._dock.panel_terminal
    check("Ctrl+J opens the user's own shell", plain is not None and not plain.sandboxed)
    GLib.timeout_add(4000, shells_up)
    return GLib.SOURCE_REMOVE


def shells_up() -> bool:
    caller = state["caller"]
    seen = launches()
    check("the sandboxed shell went through the launcher", len(seen) == 2, len(seen))
    boxed = next((s for s in caller.panel_shells() if s.sandboxed), None)
    plain = next((s for s in caller.panel_shells() if not s.sandboxed), None)
    check("both kinds are open", boxed is not None and plain is not None)
    if boxed is None or plain is None:
        return bail("no shells")
    check("the sandboxed shell reads idle at its prompt", not boxed.has_running_command())
    ok, text = app._mcp_read_terminal(found(), {}, True)
    check("read from the box is a success", ok, text)
    check("…names the sandboxed shell", "── Sandboxed shell 1 (idle) ──" in text, text)
    check("…sees its output", "boxed-here" in text, text)
    check("…and never the user's shell", f"Terminal {plain.number}" not in text, text)
    got = app._mcp_read_terminal(found(), {"terminal": plain.number}, True)
    check(
        "the user's shell can't be named from the box",
        got == (False, f"No sandboxed shell numbered {plain.number} — open: 1"),
        got,
    )
    got = app._mcp_run_in_terminal(found(), {"command": "echo nope", "terminal": plain.number}, True)
    check("…nor typed into", got == (False, f"No sandboxed shell numbered {plain.number} — open: 1"), got)
    got = app._mcp_run_in_terminal(found(), {"command": "echo again"}, True)
    check("a second run reuses the idle sandboxed shell", got == (True, "Running in Sandboxed shell 1."), got)
    ok, text = app._mcp_read_terminal(found(), {}, False)
    check("an unsandboxed reading sees every shell", ok and f"Terminal {plain.number}" in text, text)
    # The layout round-trips the kind.
    layout = caller.capture_panel_layout()
    clean = panellayout.validate(layout) if layout else None
    kinds = []

    def walk(node):
        if "strip" in node:
            kinds.extend(node["strip"]["pages"])
        elif "split" in node:
            walk(node["a"])
            walk(node["b"])

    if clean and clean.get("tree"):
        walk(clean["tree"])
    check(
        "the layout keeps the sandboxed shell as one",
        {"kind": "shell", "hist": boxed.hist, "sandboxed": True} in kinds,
        kinds,
    )
    return grants()


def grants() -> bool:
    caller = state["caller"]
    host = terminal.SANDBOX_HOST
    check("the app installed a sandbox host", host is not None)
    mounts = terminal.SANDBOX_GRANTS
    check("…and the live grants", mounts is not None)
    check(
        "which can't deliver one here, and say why",
        mounts is not None and mounts.capable() == "bindfs not installed",
        mounts and mounts.capable(),
    )
    check("the session's box is registered with them", mounts.registered(caller.sandbox_box))
    plan_path = caller.sandbox_plan_path
    check("the launched plan is not stale yet", not host.plan_stale(plan_path, TRUSTED))
    reason = host.allow(TRUSTED, f"{HOME}/.ssh")
    check("a secret is refused by the guard", "reaches" in reason and ".ssh" in reason, reason)
    check("the home itself is refused", host.allow(TRUSTED, HOME) == "the home directory itself")
    reason = host.allow(TRUSTED, SUB)
    check("a subdirectory of the workspace is redundant", reason == "already inside the workspace", reason)
    check("a plain directory is allowed", host.allow(TRUSTED, OTHER) == "")
    check("…recorded for the workspace", host.grants(TRUSTED) == [OTHER], host.grants(TRUSTED))
    check("…and in state.json", AppState().get_sandbox_grants(os.path.realpath(TRUSTED)) == [OTHER])
    check("the launched plan is stale now", host.plan_stale(plan_path, TRUSTED))
    chip = caller._sandbox_chip
    chip._rebuild()
    texts = labels(chip._content)
    check("the chip lists the workspace", any(TRUSTED.split("/")[-1] in t for t in texts), texts)
    listed = any("lib" in t for t in texts) and "after restart" in texts
    check("the chip lists the grant, not yet applied", listed, texts)
    shares_off = "GitHub CLI login: not shared" in texts and "SSH agent: not shared" in texts
    check("the chip says the shares are off", shares_off, texts)
    check("the chip says settings.json is protected", "~/.claude/settings.json: protected" in texts, texts)
    names = [b.get_label() for b in buttons(chip._content) if b.get_label()]
    check("…Allow a directory…", "Allow a directory…" in names, names)
    check("…and a sandboxed shell", "Sandboxed shell" in names, names)
    # Nothing to resume yet: the CLI stand-in writes no transcript, so the
    # tab's resolver never bound an id. A restart here would *replace* the
    # conversation with a fresh session, so it isn't offered — and the chip
    # says why the grant above is still tagged "after restart".
    check("no restart before the session resolves", not caller.can_restart_sandboxed())
    check("…so the chip doesn't offer one", "Restart to apply" not in names, names)
    check(
        "…and says why the grant can't be applied",
        any("can't apply it from here" in t for t in texts),
        texts,
    )
    # What the resolver does when the id lands: binds the tab, and tells
    # the window, which records the session as sandboxed — in this box.
    caller.session_id = RESUMED
    caller.emit("session-resolved", RESUMED)
    check("the resolved session is sticky-sandboxed", AppState().is_sandboxed(RESUMED))
    check(
        "…in the tab's box",
        AppState().sandbox_box(RESUMED) == caller.sandbox_box == state["box1"],
        (AppState().sandboxed_sessions, caller.sandbox_box),
    )
    check("restart is on offer once it has", caller.can_restart_sandboxed())
    chip._rebuild()
    names = [b.get_label() for b in buttons(chip._content) if b.get_label()]
    check("the chip offers Restart to apply", "Restart to apply" in names, names)
    # A toast from the tab reaches the window without incident.
    caller.emit("toast", "hello & goodbye")
    check("the restart starts", caller.restart_sandboxed())
    check("…once", not caller.restart_sandboxed())
    GLib.timeout_add(5000, restarted)
    return GLib.SOURCE_REMOVE


def restarted() -> bool:
    caller = state["caller"]
    host = terminal.SANDBOX_HOST
    plan2 = caller.sandbox_plan_path
    check("the restart wrote a fresh plan", plan2 and plan2 != state["plan1"], (state["plan1"], plan2))
    check("…and released the old one", not os.path.exists(state["plan1"]))
    check("the restart kept the session's box", caller.sandbox_box == state["box1"], caller.sandbox_box)
    doc = sandboxplan.load_plan(plan2)
    check("…and its plan names it", bool(doc) and doc["inputs"]["box"] == state["box1"])
    check("…still leased", host.held(state["box1"]) and bool(sandboxplan.read_lease(state["box1"])))
    check("the restarted plan carries the grant", plan2 and not host.plan_stale(plan2, TRUSTED))
    seen = launches()
    check("the fake bwrap saw the relaunch", len(seen) == 3, len(seen))
    if len(seen) == 3:
        args = seen[2]
        check("the relaunch binds the grant", (OTHER, OTHER) in after(args, "--bind-try"), args)
        check("…in the same workspace", args[args.index("--chdir") + 1] == TRUSTED, args)
    check("the session is up again", caller.has_running_command())
    check(
        "…resumed, not started fresh",
        f"--resume {RESUMED}" in (caller._initial_command or ""),
        caller._initial_command,
    )
    # The shell opened before the restart still runs in the box it spawned
    # in, which may hold a directory the revoke has since taken away: it
    # stays on screen for the user, and leaves the agent's reach.
    got = app._mcp_read_terminal(found(), {}, True)
    check(
        "the shell left in the old box is out of the agent's reach",
        got == (True, "No sandboxed shells are open in this session."),
        got,
    )
    check(
        "…but the user still has it",
        any(getattr(s, "sandboxed", False) for s in caller.panel_shells()),
        caller.panel_shells(),
    )
    check("the chip is still on", caller._sandbox_chip.get_visible())
    chip = caller._sandbox_chip
    chip._rebuild()
    names = [b.get_label() for b in buttons(chip._content) if b.get_label()]
    check("Restart to apply is gone once applied", "Restart to apply" not in names, names)
    # Revoke through the chip's remove button.
    remove = [b for b in buttons(chip._content) if b.get_icon_name() == "list-remove-symbolic"]
    check("the grant row has a remove button", len(remove) == 1, len(remove))
    if remove:
        remove[0].emit("clicked")
        check("…which revokes it", host.grants(TRUSTED) == [], host.grants(TRUSTED))
        check("…making the plan stale again", host.plan_stale(plan2, TRUSTED))
    return siblings()


def siblings() -> bool:
    caller = state["caller"]
    win = state["win"]
    host = terminal.SANDBOX_HOST
    plan = caller.sandbox_plan_path
    derived, box, reason = host.derive(plan, SUB)
    check("a sibling inside the workspace gets a derived plan", derived is not None and reason == "", reason)
    if derived:
        doc = sandboxplan.load_plan(derived)
        check("…chdir'd into its directory", doc and doc["cwd"] == SUB, doc and doc.get("cwd"))
        check("…with the parent's grants", doc and doc["inputs"]["grants"] == [OTHER], doc and doc["inputs"])
        check("…and a box of its own", sandboxplan.valid_box_id(box) and box != caller.sandbox_box, box)
        check("…made and held", os.path.isdir(f"{E2E}/sbx/{box}/home") and host.held(box))
        sandboxplan.release_plan(derived)
        # Nothing launched from it: let go, it is nobody's, and goes.
        host.release(box)
        check("an unused sibling box is removed", host.discard_box(box) and not os.path.exists(f"{E2E}/sbx/{box}"))
    refused, no_box, reason = host.derive(plan, f"{HOME}/.ssh")
    check("a sibling in ~/.ssh is refused", refused is None and "outside the sandbox" in reason, reason)
    check("…and gets no box", no_box == "", no_box)
    got = app._mcp_start_session(found(), {"prompt": "hi", "cwd": f"{HOME}/.ssh"}, True)
    check(
        "start_session from the box refuses a cwd outside it",
        isinstance(got, tuple)
        and got[0] is False
        and got[1].startswith("start_session from a sandboxed session:"),
        got,
    )
    check("…naming the rule", isinstance(got, tuple) and "allowed directories" in got[1], got)
    got = app._mcp_start_session(
        found(), {"prompt": "hi", "cwd": SUB, "model": "not a model; rm -rf"}, True
    )
    check("a refusal past the derive", isinstance(got, tuple) and got[0] is False, got)
    before = win.tab_view.get_n_pages()
    got = app._mcp_start_session(found(), {"prompt": "hi", "cwd": SUB}, True)
    check("start_session from the box spawns a sibling", not isinstance(got, tuple), got)
    GLib.timeout_add(3000, sibling_up, before)
    return GLib.SOURCE_REMOVE


def sibling_up(before: int) -> bool:
    win = state["win"]
    caller = state["caller"]
    check("a sibling tab opened", win.tab_view.get_n_pages() == before + 1, win.tab_view.get_n_pages())
    sibling = None
    for i in range(win.tab_view.get_n_pages()):
        tab = win.tab_view.get_nth_page(i).get_child()
        if tab is not caller and isinstance(tab, terminal.TerminalTab):
            sibling = tab
    check("the sibling is sandboxed", sibling is not None and sibling.sandboxed)
    if sibling is not None:
        doc = sandboxplan.load_plan(sibling.sandbox_plan_path)
        check("…on a plan derived from the parent's", doc and doc.get("cwd") == SUB, doc and doc.get("cwd"))
        check(
            "…with the parent's workspace, not its own",
            doc and doc["inputs"]["workspace"] == TRUSTED,
            doc and doc["inputs"],
        )
        check("…in bypass mode", sibling.launch_options.permission_mode == "bypassPermissions")
        check("…wearing the chip", sibling._sandbox_chip.get_visible())
        own = sibling.sandbox_box
        check(
            "the sibling's box differs from its parent's",
            sandboxplan.valid_box_id(own) and own != caller.sandbox_box,
            (own, caller.sandbox_box),
        )
        check("…and its plan names it", doc and doc["inputs"]["box"] == own, doc and doc["inputs"])
        seen = launches()
        check("the sibling's launch went through the box", len(seen) == 4, len(seen))
        if len(seen) == 4:
            check("…chdir'd into its directory", seen[3][seen[3].index("--chdir") + 1] == SUB, seen[3])
            check(
                "…with a home of its own",
                after(seen[3], "--bind")[0] == (f"{E2E}/sbx/{own}/home", HOME),
                after(seen[3], "--bind")[:1],
            )
            check(
                "…and nothing of its parent's box",
                not any(caller.sandbox_box in arg for arg in seen[3]),
                seen[3],
            )
        # The refused start_session's box went on its thread: what is left
        # is the parent's, the sibling's, and the owner's mark.
        left = sorted(n for n in os.listdir(f"{E2E}/sbx") if sandboxplan.valid_box_id(n))
        check("a refused sibling leaves no box", left == sorted([caller.sandbox_box, own]), left)
    return forgotten()


def forgotten() -> bool:
    """A session whose transcript goes takes its box with it; the sticky
    flag stays, so a transcript restored from the trash resumes boxed."""
    win = state["win"]
    gone = "99999999-8888-7777-6666-555555555555"
    box = sandboxplan.new_box_id()
    sandboxplan.make_box(box)
    sandboxplan.seed_home(sandboxplan.box_home(box))
    os.makedirs(f"{sandboxplan.box_home(box)}/dev/project")
    with open(f"{sandboxplan.box_home(box)}/dev/project/notes.txt", "w", encoding="utf-8") as fh:
        fh.write("the agent's own\n")
    os.symlink(OTHER, f"{sandboxplan.box_home(box)}/dev/lib")  # a live grant's link
    win.state.set_sandboxed(gone, True, box=box)
    state["gone"] = (gone, box)
    win._forget_transcript(gone)
    check("a forgotten session keeps its sticky flag", AppState().is_sandboxed(gone))
    check("…and names no box", AppState().sandbox_box(gone) == "", AppState().sandboxed_sessions)
    # An unsandboxed session's transcript going marks nothing.
    win._forget_transcript("00000000-aaaa-bbbb-cccc-000000000000")
    check(
        "forgetting an unsandboxed session marks nothing",
        not AppState().is_sandboxed("00000000-aaaa-bbbb-cccc-000000000000"),
    )
    # The running session's box is held by its tab: forgetting its id
    # alone would not remove it.
    state["polls"] = 0
    GLib.timeout_add(100, box_gone)
    return GLib.SOURCE_REMOVE


def box_gone() -> bool:
    _gone, box = state["gone"]
    state["polls"] += 1
    if os.path.exists(sandboxplan.box_dir(box)) and state["polls"] < 50:
        return GLib.SOURCE_CONTINUE
    check("a trashed session leaves no box directory", not os.path.exists(sandboxplan.box_dir(box)))
    check("…and what its link pointed at is untouched", os.path.isdir(OTHER))
    check("the running session's box is still there", os.path.isdir(sandboxplan.box_home(state["box1"])))
    return live()


def live() -> bool:
    """The second pass, where the machine can: bindfs back, a directory
    allowed through the chip while the session runs is tagged *live* and
    nothing asks for a restart."""
    caller = state["caller"]
    host = terminal.SANDBOX_HOST
    del os.environ["COLLINS_BINDFS"]
    mounts = sandboxgrants.GrantMounts(host)
    reason = mounts.capable()
    if reason:
        print(f"SKIP  the live pass: {reason}", flush=True)
        mounts.shutdown()
        finish()
        return GLib.SOURCE_REMOVE
    # The app's own, in place of the one that had no bindfs: what a launch
    # on this machine gets.
    terminal.SANDBOX_GRANTS.shutdown()
    terminal.SANDBOX_GRANTS = mounts
    app._sandbox_grants = mounts
    # The grant taken back in the first pass, allowed again: the box holds
    # it statically, and the state agrees with the box from here.
    host.allow(TRUSTED, OTHER)
    mounts.register(sandboxplan.load_plan(caller.sandbox_plan_path))
    check("the box holds the first grant statically", mounts.status(caller.sandbox_box, OTHER) == "static")
    check("…and is not stale", not host.plan_stale(caller.sandbox_plan_path, TRUSTED))
    state["toasts"] = []
    caller.connect("toast", lambda _tab, text: state["toasts"].append(text))
    with open(f"{LIVE}/hello.txt", "w", encoding="utf-8") as fh:
        fh.write("from the host\n")
    # The chip's own flow, minus the folder chooser.
    caller._sandbox_chip.allow_directory(TRUSTED, LIVE)
    state["polls"] = 0
    GLib.timeout_add(50, delivered)
    return GLib.SOURCE_REMOVE


def delivered() -> bool:
    caller = state["caller"]
    host = terminal.SANDBOX_HOST
    mounts = terminal.SANDBOX_GRANTS
    state["polls"] += 1
    if not state["toasts"] and state["polls"] < 200:
        return GLib.SOURCE_CONTINUE
    box = caller.sandbox_box
    own = mounts.delivery(box, LIVE)
    if own is None or own.status != sandboxgrants.LIVE:
        why = own.reason if own is not None else "nothing was delivered"
        print(f"SKIP  the live pass: the grant couldn't be mounted here: {why}", flush=True)
        finish()
        return GLib.SOURCE_REMOVE
    print(f"  --  live: {LIVE} allowed through the chip while the session runs", flush=True)
    check("the verdict lands as one toast", state["toasts"] == [f"Allowed {LIVE.replace(HOME, '~')}"], state["toasts"])
    point = f"{sandboxplan.box_carrier(box)}/{sandboxgrants.slot_name(LIVE)}"
    check("the mount is in the box's carrier, and nowhere else", mounts_under(E2E) == [point], mounts_under(E2E))
    check("…and holds the directory", os.path.exists(f"{point}/hello.txt"))
    link = f"{sandboxplan.box_home(box)}/dev/live"
    check("the real path is a link in the box's home", os.path.islink(link) and os.readlink(link) == own.inside)
    check("the grant is recorded", host.grants(TRUSTED) == [OTHER, LIVE], host.grants(TRUSTED))
    chip = caller._sandbox_chip
    chip._rebuild()
    texts = labels(chip._content)
    check("the chip tags the row live", "live" in texts, texts)
    check("…and nothing after restart", "after restart" not in texts, texts)
    names = [b.get_label() for b in buttons(chip._content) if b.get_label()]
    check("no Restart to apply: the box holds what the state grants", "Restart to apply" not in names, names)
    check("both rows can be removed", len([b for b in buttons(chip._content) if b.get_icon_name() == "list-remove-symbolic"]) == 2)
    # A sibling can't start inside a directory its parent holds only live.
    got = app._mcp_start_session(found(), {"prompt": "hi", "cwd": LIVE}, True)
    check(
        "a sibling inside the live grant is refused, and told why",
        isinstance(got, tuple) and got[0] is False and "was allowed while the parent session was running" in got[1],
        got,
    )
    # Taken back through its remove button: out of the running box.
    remove = [b for b in buttons(chip._content) if b.get_icon_name() == "list-remove-symbolic"]
    remove[-1].emit("clicked")
    check("the remove button revokes it", host.grants(TRUSTED) == [OTHER], host.grants(TRUSTED))
    state["polls"] = 0
    GLib.timeout_add(50, revoked)
    return GLib.SOURCE_REMOVE


def revoked() -> bool:
    caller = state["caller"]
    mounts = terminal.SANDBOX_GRANTS
    state["polls"] += 1
    if mounts_under(E2E) and state["polls"] < 200:
        return GLib.SOURCE_CONTINUE
    check("revoked, nothing is left mounted", mounts_under(E2E) == [], mounts_under(E2E))
    link = f"{sandboxplan.box_home(caller.sandbox_box)}/dev/live"
    check("…and the link is gone", not os.path.lexists(link))
    check("the directory itself is untouched", os.path.exists(f"{LIVE}/hello.txt"))
    check("the live grants hold nothing", mounts.live_paths(caller.sandbox_box) == [])
    # A static grant taken back stays in the running box until the restart.
    terminal.SANDBOX_HOST.revoke(TRUSTED, OTHER)
    chip = caller._sandbox_chip
    chip._rebuild()
    texts = labels(chip._content)
    check("a static grant taken back is tagged until restart", "until restart" in texts, texts)
    remove = [b for b in buttons(chip._content) if b.get_icon_name() == "list-remove-symbolic"]
    check("…with no remove button", remove == [], len(remove))
    names = [b.get_label() for b in buttons(chip._content) if b.get_label()]
    check("…and the restart on offer", "Restart to apply" in names, names)
    finish()
    return GLib.SOURCE_REMOVE


def finish() -> None:
    win = state["win"]
    for i in range(win.tab_view.get_n_pages()):
        tab = win.tab_view.get_nth_page(i).get_child()
        for shell in getattr(tab, "panel_shells", list)():
            pid = getattr(shell, "_child_pid", None)
            if pid:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except OSError:
                    pass
        pid = getattr(tab, "_child_pid", None)
        if pid:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except OSError:
                pass
    app.quit()


def watchdog() -> bool:
    clear_tree()  # os._exit skips the atexit hook
    os._exit(3)


GLib.timeout_add(250, stage)
GLib.timeout_add(90_000, watchdog)
app.run([])
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED or not PASSED else 0)
