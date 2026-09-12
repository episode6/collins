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
plan derived for its directory, and one outside the box is refused.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \
        python3 scripts/check_sandbox_policy.py

Staged under ~/.cache rather than /tmp: /tmp is shared read-write into every
box, and a scratch tree there would carry Collins' own state inside — the
plan's protect-check refuses exactly that. HOME is moved into the scratch
tree too, so the launch's RW_HOME_ALWAYS directories and the seeded sandbox
home never touch the real one.
"""

import os
import shutil
import signal
import sys
import tempfile

REAL_HOME = os.path.expanduser("~")
STAGE = os.path.join(REAL_HOME, ".cache", "collins-e2e")
os.makedirs(STAGE, exist_ok=True)
E2E = tempfile.mkdtemp(prefix="sandbox-policy-", dir=STAGE)
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())
HOME = f"{E2E}/home"

os.environ["HOME"] = HOME
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["COLLINS_SANDBOX_HOME"] = f"{E2E}/sbx"
os.environ["COLLINS_BWRAP"] = f"{E2E}/bin/bwrap"
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
SHIM = f"{E2E}/bin/claude"
FAKE_BWRAP = os.environ["COLLINS_BWRAP"]
FAKE_LOG = os.environ["SANDBOX_FAKE_LOG"]

for path in (f"{E2E}/projects", f"{E2E}/chats", f"{E2E}/bin", HOME, SUB, OTHER, f"{HOME}/.ssh"):
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

from collins import i18n, panellayout, sandboxplan, terminal, trust  # noqa: E402
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
        check("the overlay home comes first", after(args, "--bind")[0] == (f"{E2E}/sbx", HOME), args)

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
    caller.session_id = RESUMED  # what the resolver does when the id lands
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
    derived, reason = host.derive(plan, SUB)
    check("a sibling inside the workspace gets a derived plan", derived is not None and reason == "", reason)
    if derived:
        doc = sandboxplan.load_plan(derived)
        check("…chdir'd into its directory", doc and doc["cwd"] == SUB, doc and doc.get("cwd"))
        check("…with the parent's grants", doc and doc["inputs"]["grants"] == [OTHER], doc and doc["inputs"])
        sandboxplan.release_plan(derived)
    refused, reason = host.derive(plan, f"{HOME}/.ssh")
    check("a sibling in ~/.ssh is refused", refused is None and "outside the sandbox" in reason, reason)
    got = app._mcp_start_session(found(), {"prompt": "hi", "cwd": f"{HOME}/.ssh"}, True)
    check(
        "start_session from the box refuses a cwd outside it",
        isinstance(got, tuple)
        and got[0] is False
        and got[1].startswith("start_session from a sandboxed session:"),
        got,
    )
    check("…naming the rule", isinstance(got, tuple) and "allowed directories" in got[1], got)
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
        seen = launches()
        check("the sibling's launch went through the box", len(seen) == 4, len(seen))
        if len(seen) == 4:
            check("…chdir'd into its directory", seen[3][seen[3].index("--chdir") + 1] == SUB, seen[3])
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


GLib.timeout_add(250, stage)
GLib.timeout_add(90_000, lambda: os._exit(3))
app.run([])
shutil.rmtree(E2E, ignore_errors=True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED or not PASSED else 0)
