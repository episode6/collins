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
restart, and removed when the session's transcript is forgotten. A launch
that asks the CLI for a worktree reserves it first and types its name: the
box holds that worktree read-write and the checkout read-only, the chip
says so, the restart binds the same worktree again — and says so when the
worktree was reaped and couldn't be put back, in a box no wider for it — a
sibling in the checkout is refused, and a worktree the CLI couldn't cut
puts the session in a box rebuilt around the checkout.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \
        python3 scripts/check_sandbox_policy.py

Staged under ~/.cache rather than /tmp: /tmp is shared read-write into every
box, and a scratch tree there would carry Collins' own state inside — the
plan's protect-check refuses exactly that. HOME is moved into the scratch
tree too, so the launch's RW_HOME_ALWAYS directories and the seeded sandbox
home never touch the real one.
"""

import atexit
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import types

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
os.environ["COLLINS_DEBUG_API"] = "1"  # the e2e probe (debug.*): served only with this set
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
EXTRA = f"{E2E}/dev/extra"  # allowed to one session, then pinned as a project default
PINNED = f"{E2E}/dev/pinned"  # a project default the first session never held
# A repository's main checkout, for the launch that asks for a worktree.
CHECKOUT = f"{E2E}/dev/gamma"
NARROWED = "22222222-3333-4444-5555-666666666666"
# A project pinned unsandboxed: its sessions are offered every tool.
PLAIN = f"{E2E}/dev/plain"
SHIM = f"{E2E}/bin/claude"
FAKE_BWRAP = os.environ["COLLINS_BWRAP"]
FAKE_LOG = os.environ["SANDBOX_FAKE_LOG"]

# A session from an earlier run, sandboxed, with a box and a directory of
# its own: what a fork is taken from.
ORIGIN = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
ORIGIN_BOX = "0123456789abcdef0123456789abcdef"
# What a build from before grants were a session's wrote, keyed by a path:
# dropped on load, and nobody's default.
STALE_KEY = TRUSTED
_PROJECT = f"{E2E}/projects/" + "".join(c if c.isalnum() else "-" for c in TRUSTED)

for path in (
    f"{E2E}/projects", f"{E2E}/chats", f"{E2E}/bin", HOME, SUB, OTHER, LIVE, EXTRA, PINNED,
    f"{HOME}/.ssh", _PROJECT, f"{CHECKOUT}/.git", PLAIN,
):
    os.makedirs(path, exist_ok=True)
# A dotfiles setup: the user's instructions are a symlink, which no bind can
# hold read-only. The chip says so.
os.makedirs(f"{HOME}/.claude", exist_ok=True)
os.symlink(f"{E2E}/dotfiles/AGENTS.md", f"{HOME}/.claude/CLAUDE.md")
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
with open(f"{_PROJECT}/{ORIGIN}.jsonl", "w", encoding="utf-8") as fh:
    fh.write(
        json.dumps(
            {
                "type": "user",
                "uuid": "u1",
                "timestamp": "2026-09-05T09:00:00Z",
                "cwd": TRUSTED,
                "sessionId": ORIGIN,
                "message": {"role": "user", "content": "An earlier sandboxed session"},
            }
        )
        + "\n"
    )
os.makedirs(f"{E2E}/config/collins", exist_ok=True)
with open(f"{E2E}/config/collins/state.json", "w", encoding="utf-8") as fh:
    json.dump(
        {
            "settings": {
                "welcome_seen": True,
                "gh_welcome_dismissed": True,
                "title_model": "none",
                "sandbox_new_sessions": True,
                # Off by default; on here, since the mode a box hands its
                # session and its sibling is what the checks below read.
                "sandbox_bypass_permissions": True,
            },
            "project_sandbox": {"plain": False},
            "sandboxed_sessions": {ORIGIN: ORIGIN_BOX},
            # The earlier session was let into the terminal panel, and
            # kept from showing images: what a fork of it starts with.
            "sandbox_tools": {ORIGIN_BOX: {"read_terminal": True, "show_image": False}},
            "sandbox_grants": {ORIGIN_BOX: [OTHER], STALE_KEY: [PINNED]},
        },
        fh,
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
import e2e_service  # noqa: E402
from gi.repository import GLib, Gtk  # noqa: E402

from collins import (  # noqa: E402
    apilink,
    i18n,
    mcptools,
    panellayout,
    sandboxgrants,
    sandboxplan,
    sandboxstatus,
    terminal,
    trust,
)
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
trust.trust_dir(CHECKOUT)
trust.trust_dir(PLAIN)
# The service is its own process (PR-1.12b): started here, with this
# check's environment, before the app connects to it.
e2e_service.start_service()
app = App()


def _service_call(target: str, name: str, *args, **kwargs):
    """A method of the service's sandbox host, its live grants or the core,
    through the probe (debug.sandbox, D27): the one door a check has past
    the protocol."""
    message: dict = {"t": "debug.sandbox", "target": target, "name": name, "args": list(args)}
    if kwargs:
        message["kwargs"] = dict(kwargs)
    return app._service_link.call(message).get("value")


class _Probe:
    """The service's sandbox host (or its live grants) as the check reads
    them: every method call crosses as a debug.sandbox request, a
    `Delivery` coming back as a record with the same attributes."""

    def __init__(self, target: str) -> None:
        self._target = target

    def __getattr__(self, name: str):
        def call(*args, **kwargs):
            value = _service_call(self._target, name, *args, **kwargs)
            if name == "delivery" and isinstance(value, dict):
                return types.SimpleNamespace(**value)
            return value

        return call


def probe_host():
    """The service's sandbox host, None where it runs none (asked once the
    app is up: the link exists from its startup on)."""
    return _Probe("host") if _service_call("core", "sandbox_hosted") else None


def probe_grants():
    return _Probe("grants") if _service_call("core", "grants_live") else None

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
    # The probe is the service's; the client hears its verdict (sandboxstatus).
    if win is None or sandboxstatus.probe_reason() is None:
        if tries > 60:
            return bail("timed out waiting for the window / the sandbox probe")
        return GLib.SOURCE_CONTINUE
    check("the fake bwrap passes the probe", sandboxstatus.probe_reason() == "")
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
        # The lease is the service's (its process forks the box, spec §3.20):
        # the pid service.status answers, not this window's.
        service_pid = apilink.call({"t": "service.status"}).get("pid")
        check("…leased to this instance", bool(lease) and lease["pid"] == service_pid, (lease, service_pid))
        check(
            "no session names the box before the id resolves",
            box not in AppState().sandbox_boxes(),
            AppState().sandboxed_sessions,
        )

    # The tool policy: a sandboxed session reaches only sandboxed shells.
    got = app.tool_client.read_terminal(found(), {}, True)
    check(
        "read from the box with no sandboxed shell says so",
        got == (True, "No sandboxed shells are open in this session."),
        got,
    )
    got = app.tool_client.run_in_terminal(found(), {"command": "echo boxed-here"}, True)
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
    ok, text = app.tool_client.read_terminal(found(), {}, True)
    check("read from the box is a success", ok, text)
    check("…names the sandboxed shell", "── Sandboxed shell 1 (idle) ──" in text, text)
    check("…sees its output", "boxed-here" in text, text)
    check("…and never the user's shell", f"Terminal {plain.number}" not in text, text)
    got = app.tool_client.read_terminal(found(), {"terminal": plain.number}, True)
    check(
        "the user's shell can't be named from the box",
        got == (False, f"No sandboxed shell numbered {plain.number} — open: 1"),
        got,
    )
    got = app.tool_client.run_in_terminal(found(), {"command": "echo nope", "terminal": plain.number}, True)
    check("…nor typed into", got == (False, f"No sandboxed shell numbered {plain.number} — open: 1"), got)
    got = app.tool_client.run_in_terminal(found(), {"command": "echo again"}, True)
    check("a second run reuses the idle sandboxed shell", got == (True, "Running in Sandboxed shell 1."), got)
    ok, text = app.tool_client.read_terminal(found(), {}, False)
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
    return tools()


OFFERED = ["set_session_title", "open_in_editor", "show_diff", "show_image", "notify_user", "attach_pr"]


def _core_call(name: str, *args):
    """A method of the service's core by name (the probe, D27): the tools'
    dispatcher is the service's since PR-1.12b."""
    return apilink.call(
        {"t": "debug.sandbox", "target": "core", "name": name, "args": list(args)}
    )["value"]


def found_for_pid(shim_pid: int):
    """The window and tab of the session the service binds a call from
    *shim_pid* to (`SessionTools.find`, through the probe: the walk is the
    service's /proc and sessions), as `ToolClient.found_for_handle` finds
    them. A check's helper: no product code asks a debug request."""
    handle = _core_call("debug_find_handle", int(shim_pid))
    return app.tool_client.found_for_handle(handle or "")


def offered_to(tab) -> list[str]:
    """What the session in *tab* is told it may call: the list the socket
    serves the pid of a process under the tab's shell."""
    return list(_core_call("debug_tools_list", tab.probe_call("child_pid")))


def call_from(tab, tool: str, args: dict):
    """One tool call as the dispatcher sees it arrive from *tab*: by pid,
    through every gate — not the handler alone. ``(ok, text)``, or the
    word "deferred" for a reply that waits on a client."""
    got = _core_call("debug_tools_dispatch", tab.probe_call("child_pid"), tool, args)
    if isinstance(got, dict) and got.get("deferred"):
        # A UI-bound tool: its reply waits on this very client (the tool
        # event lands on this loop), so pump until it has settled.
        call_id = got["deferred"]
        box = {}

        def settled() -> bool:
            box["result"] = _core_call("debug_tools_result", call_id)
            return box["result"] is not None

        e2e_service.wait_until(settled, timeout_s=15)
        got = box.get("result")
        if got is None:
            return "deferred"
    return tuple(got) if isinstance(got, list) else got


def tools() -> bool:
    """What a sandboxed session may call is a list of its own, kept by
    Collins against the session's box and asked at the dispatcher."""
    caller = state["caller"]
    host = probe_host()
    box = caller.sandbox_box
    check(
        "the pid of the session's shell resolves to its tab",
        found_for_pid(caller.probe_call("child_pid")) == found(),
    )
    check("a sandboxed session is offered six tools", offered_to(caller) == OFFERED, offered_to(caller))
    shells = len(caller.panel_shells())
    refused = {
        "run_in_terminal": {"command": "echo out-of-the-box"},
        "read_terminal": {},
        "start_session": {"prompt": "hi"},
        "diff_context": {},
        "annotate_diff": {"notes": [{"file": "a", "line": 1, "summary": "s"}]},
        "clear_diff_marks": {},
    }
    for tool, args in refused.items():
        got = call_from(caller, tool, args)
        check(
            f"{tool} from the box is refused at the dispatcher",
            got == (False, mcptools.sandbox_disabled_error(tool)),
            got,
        )
    check("…and nothing was opened for it", len(caller.panel_shells()) == shells, caller.panel_shells())
    got = call_from(caller, "set_session_title", {"title": "hi"})
    check(
        "a tool it is offered reaches its handler",
        isinstance(got, tuple) and "turned off" not in got[1],
        got,
    )
    # A call says nothing about what its caller is offered.
    got = call_from(caller, "run_in_terminal", {"command": "echo x", "sandboxed": False})
    check("an argument that claims otherwise is a schema error", got[0] is False and "turned off" not in got[1], got)
    chip = caller._sandbox_chip
    chip._rebuild()
    texts = labels(chip._content)
    check("the chip counts the tools, folded away", "Session tools: 6 of 13 on" in texts, texts)
    check("…with no check on show until it is unfolded", buttons_of(chip._content, Gtk.CheckButton) == [])
    # An expander holds its child only while it is open.
    chip._tools_expanded = True
    chip._rebuild()
    boxes = {
        b.get_label(): b for b in buttons_of(chip._content, Gtk.CheckButton)
    }
    check("the chip has a check per tool", len(boxes) == len(mcptools.TOOLS), sorted(boxes))
    on = sorted(label for label, b in boxes.items() if b.get_active())
    check("…six of them set", len(on) == 6, on)
    run = boxes.get("Run commands in the terminal panel")
    check("…and the terminal's is not", run is not None and not run.get_active())
    # Switched on in the chip: this session's box, and at once for a call.
    if run is not None:
        run.set_active(True)
    check("switched on in the chip, it is the box's own switch", host.tool_overrides(box) == {"run_in_terminal": True}, host.tool_overrides(box))
    check("…in state.json, under the box", AppState().get_sandbox_tools(box) == {"run_in_terminal": True})
    check("…and offered", offered_to(caller) == [*OFFERED, "run_in_terminal"], offered_to(caller))
    got = call_from(caller, "run_in_terminal", {"command": "echo offered-now"})
    check("the call goes through, into a sandboxed shell", got == (True, "Running in Sandboxed shell 1."), got)
    got = call_from(caller, "read_terminal", {})
    check("the tool beside it is still refused", got == (False, mcptools.sandbox_disabled_error("read_terminal")), got)
    # Switched off for every session, it is off in the box whatever the box says.
    win_state().set_setting("mcp_tool_run_in_terminal", False)
    e2e_service.settle()
    got = call_from(caller, "run_in_terminal", {"command": "echo nope"})
    check("a tool off in Preferences is off in the box too", got == (False, mcptools.disabled_error("run_in_terminal")), got)
    check("…and not offered", "run_in_terminal" not in offered_to(caller))
    chip._rebuild()
    run = {b.get_label(): b for b in buttons_of(chip._content, Gtk.CheckButton)}.get(
        "Run commands in the terminal panel"
    )
    check("…its check greyed", run is not None and not run.get_sensitive() and not run.get_active())
    win_state().set_setting("mcp_tool_run_in_terminal", True)
    e2e_service.settle()
    # The default for sandboxed sessions moves the boxes with no switch of
    # their own for the tool, and only those.
    win_state().set_setting("sandbox_tool_show_image", False)
    e2e_service.settle()
    check("a default switched off reaches the session", "show_image" not in offered_to(caller), offered_to(caller))
    win_state().set_setting("sandbox_tool_run_in_terminal", False)
    e2e_service.settle()
    check("…and leaves its own switch alone", "run_in_terminal" in offered_to(caller))
    win_state().set_setting("sandbox_tool_show_image", True)
    e2e_service.settle()
    chip._rebuild()
    names = [b.get_label() for b in buttons(chip._content) if b.get_label()]
    check("the chip offers the defaults back", "Use the defaults" in names, names)
    chip.reset_tools()
    check("back on the defaults", host.tool_overrides(box) == {} and offered_to(caller) == OFFERED, offered_to(caller))
    # What the session is denied, a sibling it spawns is denied (siblings()).
    check("the session is denied a tool it had", chip.set_tool("attach_pr", False))
    check("…which it is no longer offered", "attach_pr" not in offered_to(caller))
    launch_unsandboxed()
    return GLib.SOURCE_REMOVE


def buttons_of(widget, kind) -> list:
    found_ = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, kind):
            found_.append(child)
        found_.extend(buttons_of(child, kind))
        child = child.get_next_sibling()
    return found_


def launch_unsandboxed() -> None:
    """A session outside any box, in a project pinned that way: it is
    offered every tool, as it always was."""
    win = state["win"]
    before = {win.tab_view.get_nth_page(i).get_child() for i in range(win.tab_view.get_n_pages())}
    win.start_background_session(PLAIN)
    ticks = {"n": 0}

    def up() -> bool:
        ticks["n"] += 1
        for i in range(win.tab_view.get_n_pages()):
            tab = win.tab_view.get_nth_page(i).get_child()
            if tab not in before and getattr(tab, "probe_call", None) and tab.probe_call("child_pid"):
                check("a session of a project pinned unsandboxed has no box", not tab.sandboxed)
                every = [tool["name"] for tool in mcptools.TOOLS]
                check("…and is offered every tool", offered_to(tab) == every, offered_to(tab))
                got = call_from(tab, "read_terminal", {})
                check("…the terminal's included", isinstance(got, tuple) and got[0] is True, got)
                grants()
                return GLib.SOURCE_REMOVE
        if ticks["n"] > 100:
            check("an unsandboxed session launched", False, "no tab spawned a shell")
            grants()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    GLib.timeout_add(100, up)


def grants() -> bool:
    caller = state["caller"]
    host = probe_host()
    check("the app installed a sandbox host", host is not None)
    mounts = probe_grants()
    check("…and the live grants", mounts is not None)
    check(
        "which can't deliver one here, and say why",
        mounts is not None and mounts.capable() == "bindfs not installed",
        mounts and mounts.capable(),
    )
    check("the session's box is registered with them", mounts.registered(caller.sandbox_box))
    plan_path = caller.sandbox_plan_path
    box = caller.sandbox_box
    check("the launched plan is not stale yet", not host.plan_stale(plan_path, TRUSTED))
    # What a build wrote while grants were keyed by a path is nobody's.
    check(
        "a grants entry keyed by a path was dropped on load",
        win_state().sandbox_grants == {ORIGIN_BOX: [OTHER]},
        win_state().sandbox_grants,
    )
    check("…and is no project's default", host.project_grants(TRUSTED) == [], host.project_grants(TRUSTED))
    check("a new session starts with no grants", host.grants(box) == [], host.grants(box))
    reason = host.allow(box, TRUSTED, f"{HOME}/.ssh")
    check("a secret is refused by the guard", "reaches" in reason and ".ssh" in reason, reason)
    check("the home itself is refused", host.allow(box, TRUSTED, HOME) == "the home directory itself")
    reason = host.allow(box, TRUSTED, SUB)
    check("a subdirectory of the workspace is redundant", reason == "already inside the workspace", reason)
    check("a plain directory is allowed", host.allow(box, TRUSTED, OTHER) == "")
    check("…recorded for the session's box", host.grants(box) == [OTHER], host.grants(box))
    check("…and in state.json", AppState().get_sandbox_grants(box) == [OTHER])
    check("the launched plan is stale now", host.plan_stale(plan_path, TRUSTED))
    chip = caller._sandbox_chip
    chip._rebuild()
    texts = labels(chip._content)
    check("the chip lists the workspace", any(TRUSTED.split("/")[-1] in t for t in texts), texts)
    listed = any("lib" in t for t in texts) and "after restart" in texts
    check("the chip lists the grant, not yet applied", listed, texts)
    check("…under a caption saying whose it is", "For this session only" in texts, texts)
    check("…and the project's defaults, none yet", texts.count("None") == 1, texts)
    check("…under their own heading", "New sessions of this project" in texts, texts)
    shares_off = "GitHub CLI login: not shared" in texts and "SSH agent: not shared" in texts
    check("the chip says the shares are off", shares_off, texts)
    check("the chip says settings and hooks are read-only", "Settings and hooks: read-only" in texts, texts)
    unpinned = [t for t in texts if t.startswith("Writable, a symlink: ")]
    check(
        "…and names the one that couldn't be: the symlinked CLAUDE.md",
        len(unpinned) == 1 and unpinned[0].endswith("/.claude/CLAUDE.md"),
        unpinned,
    )
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
    # What the resolver does when the id lands: binds the session, and
    # tells the service's host, which records the session as sandboxed —
    # in this box — and the tab, which hears "session-resolved".
    caller.probe_set("session_id", RESUMED)
    caller.probe_call("host.session_resolved", RESUMED)
    check("the resolved session is sticky-sandboxed", AppState().is_sandboxed(RESUMED))
    check(
        "…in the tab's box",
        AppState().sandbox_box(RESUMED) == caller.sandbox_box == state["box1"],
        (AppState().sandboxed_sessions, caller.sandbox_box),
    )
    e2e_service.settle()  # the session event carrying can_restart lands a moment later
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
    host = probe_host()
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
        f"--resume {RESUMED}" in (caller.probe("initial_command") or ""),
        caller.probe("initial_command"),
    )
    # The shell opened before the restart still runs in the box it spawned
    # in, which may hold a directory the revoke has since taken away: it
    # stays on screen for the user, and leaves the agent's reach.
    got = app.tool_client.read_terminal(found(), {}, True)
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
    pins = [b for b in buttons(chip._content) if b.get_icon_name() == "view-pin-symbolic"]
    check("…and a pin, not set", len(pins) == 1 and not pins[0].get_active(), len(pins))
    if remove:
        remove[0].emit("clicked")
        check("…which revokes it", host.grants(state["box1"]) == [], host.grants(state["box1"]))
        check("…making the plan stale again", host.plan_stale(plan2, TRUSTED))
    return siblings()


def siblings() -> bool:
    caller = state["caller"]
    win = state["win"]
    host = probe_host()
    plan = caller.sandbox_plan_path
    # A default of the project that the parent never held: a sibling holds
    # nothing its parent wasn't launched with, defaults included.
    check("a default is made", host.set_project_default(TRUSTED, PINNED, True) == "")
    derived, box, reason = host.derive(plan, SUB)
    check("a sibling inside the workspace gets a derived plan", derived is not None and reason == "", reason)
    if derived:
        doc = sandboxplan.load_plan(derived)
        check("…chdir'd into its directory", doc and doc["cwd"] == SUB, doc and doc.get("cwd"))
        check("…with the parent's grants", doc and doc["inputs"]["grants"] == [OTHER], doc and doc["inputs"])
        check("…recorded as its own list", host.grants(box) == [OTHER], host.grants(box))
        check("…and a box of its own", sandboxplan.valid_box_id(box) and box != caller.sandbox_box, box)
        check("…made and held", os.path.isdir(f"{E2E}/sbx/{box}/home") and host.held(box))
        sandboxplan.release_plan(derived)
        # Nothing launched from it: let go, it is nobody's, and goes, its
        # grants with it.
        host.release(box)
        host.forget_box(box)
        check("an unused sibling box's grants are forgotten", host.grants(box) == [], host.grants(box))
    refused, no_box, reason = host.derive(plan, f"{HOME}/.ssh")
    check("a sibling in ~/.ssh is refused", refused is None and "outside the sandbox" in reason, reason)
    check("…and gets no box", no_box == "", no_box)
    got = app.tool_client.start_session(found(), {"prompt": "hi", "cwd": f"{HOME}/.ssh"}, True)
    check(
        "start_session from the box refuses a cwd outside it",
        isinstance(got, tuple)
        and got[0] is False
        and got[1].startswith("start_session from a sandboxed session:"),
        got,
    )
    check("…naming the rule", isinstance(got, tuple) and "allowed directories" in got[1], got)
    got = app.tool_client.start_session(
        found(), {"prompt": "hi", "cwd": SUB, "model": "not a model; rm -rf"}, True
    )
    check("a refusal past the derive", isinstance(got, tuple) and got[0] is False, got)
    before = win.tab_view.get_n_pages()
    got = app.tool_client.start_session(found(), {"prompt": "hi", "cwd": SUB}, True)
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
        host = probe_host()
        # Its parent was launched with the first grant, and has lost it
        # since; the sibling holds what the parent was launched with.
        # What its parent was denied, the sibling is denied; the rest are
        # the defaults.
        check("the sibling is denied the tool its parent was", host.tool_overrides(own) == {"attach_pr": False}, host.tool_overrides(own))
        check("…and offered the other five", offered_to(sibling) == [n for n in OFFERED if n != "attach_pr"], offered_to(sibling))
        check("the parent's own list is empty by now", host.grants(caller.sandbox_box) == [])
        check("the sibling's list is its parent's launch-time grants", host.grants(own) == [OTHER], host.grants(own))
        chip = sibling._sandbox_chip
        chip._rebuild()
        texts = labels(chip._content)
        check("the sibling's chip lists the grant", any(t.endswith("/lib") for t in texts), texts)
        tags = [t for t in texts if t in ("after restart", "until restart", "live")]
        check("…untagged", tags == [], tags)
        check(
            "…and not a default its parent lacks",
            PINNED not in host.grants(own) and not any(PINNED in a for a in doc["bwrap_args"]),
            host.grants(own),
        )
        check("…though the project has it", host.project_grants(TRUSTED) == [PINNED])
        check("the sibling is not stale", not host.plan_stale(sibling.sandbox_plan_path, TRUSTED))
        check("the default is taken back", host.set_project_default(TRUSTED, PINNED, False) == "")
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
    box = probe_host().mint_box(TRUSTED, seed=False)
    win.state.set_sandbox_grants(box, [OTHER])
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
    check("…and its grants went with its box", AppState().get_sandbox_grants(box) == [])
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
    return sessions()


def win_state():
    return state["win"].state if "win" in state else AppState()


def launch(then, cwd: str = TRUSTED, **how) -> None:
    """Another sandboxed session of the same project — or one in *cwd*,
    launched *how* — and *then(tab)* once its launch has settled on a plan."""
    win = state["win"]
    before = {win.tab_view.get_nth_page(i).get_child() for i in range(win.tab_view.get_n_pages())}
    win.start_background_session(cwd, **how)
    ticks = {"n": 0}

    def settled() -> bool:
        ticks["n"] += 1
        for i in range(win.tab_view.get_n_pages()):
            tab = win.tab_view.get_nth_page(i).get_child()
            if tab not in before and getattr(tab, "sandbox_plan_path", None):
                then(tab)
                return GLib.SOURCE_REMOVE
        if ticks["n"] > 100:
            check("another session launched", False, "no tab settled on a plan")
            finish()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    GLib.timeout_add(100, settled)


def chip_rows(tab) -> tuple[list[str], list[str]]:
    """(the session's rows, the project's defaults) as the tab's chip
    draws them: the path labels under each of the two headings."""
    chip = tab._sandbox_chip
    chip._rebuild()
    texts = labels(chip._content)
    start = texts.index("Allowed directories")
    split = texts.index("New sessions of this project")
    mine = [t for t in texts[start + 1 : split] if t.startswith(("/", "~"))]
    defaults = []
    for text in texts[split + 1 :]:
        if text == "None":
            continue
        if not text.startswith(("/", "~")):
            break  # the restart row, the shell button: past the list
        defaults.append(text)
    return mine, defaults


def sessions() -> bool:
    """Grants are a session's: two sessions of one project, side by side,
    and what a project's defaults do and don't do to them."""
    caller = state["caller"]
    host = probe_host()
    first = caller.sandbox_box
    state["toasts"] = []
    caller.connect("toast", lambda _tab, text: state["toasts"].append(text))
    caller._sandbox_chip.allow_directory(TRUSTED, EXTRA)
    check("the first session is allowed a directory", host.grants(first) == [EXTRA], host.grants(first))
    launch(second_up)
    return GLib.SOURCE_REMOVE


def second_up(second) -> None:
    caller = state["caller"]
    host = probe_host()
    first = caller.sandbox_box
    state["second"] = second
    box = second.sandbox_box
    check("a second session of the project has a box of its own", sandboxplan.valid_box_id(box) and box != first)
    doc = sandboxplan.load_plan(second.sandbox_plan_path)
    check("…in the same workspace", bool(doc) and doc["inputs"]["workspace"] == TRUSTED)
    check("a session launched afterwards starts with none", host.grants(box) == [], host.grants(box))
    check("…and with the six tools, its neighbour's switches being its neighbour's", offered_to(second) == OFFERED and host.tool_overrides(box) == {}, offered_to(second))
    check("…its plan binds nothing of the first's", bool(doc) and doc["inputs"]["grants"] == [])
    check("…not by any other name either", bool(doc) and not any(EXTRA in a for a in doc["bwrap_args"]))
    mine, defaults = chip_rows(second)
    check("the second's chip lists nothing", mine == [] and defaults == [], (mine, defaults))
    mine, defaults = chip_rows(caller)
    check("the first's chip lists its own", len(mine) == 1 and mine[0].endswith("/extra"), mine)
    # Pinned in the first session's chip: a default of the project.
    pins = [b for b in buttons(caller._sandbox_chip._content) if b.get_icon_name() == "view-pin-symbolic"]
    check("the first's row has a pin", len(pins) == 1 and not pins[0].get_active(), len(pins))
    if pins:
        pins[0].set_active(True)
    check("pinned, it is a default of the project", host.project_grants(TRUSTED) == [EXTRA], host.project_grants(TRUSTED))
    check("…in state.json, under the project", AppState().sandbox_project_grants == {os.path.realpath(TRUSTED): [EXTRA]})
    mine, defaults = chip_rows(caller)
    check("the first's chip lists it as one", len(defaults) == 1 and defaults[0].endswith("/extra"), defaults)
    pins = [b for b in buttons(caller._sandbox_chip._content) if b.get_icon_name() == "view-pin-symbolic"]
    check("…its pin set", len(pins) == 1 and pins[0].get_active())
    check("the first's own list is as it was", host.grants(first) == [EXTRA], host.grants(first))
    # The second session, already running, is none the wiser.
    check("the second session's list is unchanged", host.grants(box) == [], host.grants(box))
    mine, defaults = chip_rows(second)
    check("…its chip shows the default, and none of its own", mine == [] and len(defaults) == 1, (mine, defaults))
    check("…and it has nothing to restart for", not host.plan_stale(second.sandbox_plan_path, TRUSTED))
    # A secret is no default either, and says so.
    state["toasts"].clear()
    check("a secret can't be made a default", not caller._sandbox_chip.set_project_default(TRUSTED, f"{HOME}/.ssh", True))
    check("…with the reason in a toast", any("Can't make" in t and ".ssh" in t for t in state["toasts"]), state["toasts"])
    launch(third_up)


def third_up(third) -> None:
    host = probe_host()
    state["third"] = third
    box = third.sandbox_box
    check("a session launched after the pin starts with the default", host.grants(box) == [EXTRA], host.grants(box))
    doc = sandboxplan.load_plan(third.sandbox_plan_path)
    check("…and its plan binds it", bool(doc) and doc["inputs"]["grants"] == [EXTRA], doc and doc["inputs"]["grants"])
    check("…as a plain bind", bool(doc) and (EXTRA, EXTRA) in after(doc["bwrap_args"], "--bind-try"))
    chip = third._sandbox_chip
    chip._rebuild()
    texts = labels(chip._content)
    tags = [t for t in texts if t in ("after restart", "until restart", "live")]
    mine, _defaults = chip_rows(third)
    check("its chip lists it untagged", len(mine) == 1 and tags == [], (mine, tags))
    names = [b.get_label() for b in buttons(chip._content) if b.get_label()]
    check("…with nothing to restart for", "Restart to apply" not in names, names)
    # Unpinned — from the defaults list of another session's chip.
    second = state["second"]
    chip_rows(second)
    remove = [b for b in buttons(second._sandbox_chip._content) if b.get_icon_name() == "list-remove-symbolic"]
    check("the second's chip can take the default back", len(remove) == 1, len(remove))
    if remove:
        remove[0].emit("clicked")
    check("unpinned, the project has no defaults", host.project_grants(TRUSTED) == [], host.project_grants(TRUSTED))
    check("the third keeps what it was seeded with", host.grants(box) == [EXTRA], host.grants(box))
    check("…and so does the first", host.grants(state["caller"].sandbox_box) == [EXTRA])
    # A session can drop what it was seeded with without touching the defaults.
    host.set_project_default(TRUSTED, EXTRA, True)
    chip_rows(third)
    remove = [b for b in buttons(third._sandbox_chip._content) if b.get_icon_name() == "list-remove-symbolic"]
    check("the third's chip has its row's button and the default's", len(remove) == 2, len(remove))
    if remove:
        remove[0].emit("clicked")
    check("the third drops it", host.grants(box) == [], host.grants(box))
    check("…and the default stays", host.project_grants(TRUSTED) == [EXTRA], host.project_grants(TRUSTED))
    host.set_project_default(TRUSTED, EXTRA, False)
    launch(fourth_up)


def fourth_up(fourth) -> None:
    host = probe_host()
    box = fourth.sandbox_box
    check("a session launched after the unpin starts without it", host.grants(box) == [], host.grants(box))
    doc = sandboxplan.load_plan(fourth.sandbox_plan_path)
    check("…in its plan too", bool(doc) and doc["inputs"]["grants"] == [])
    boxes = {t.sandbox_box for t in (state["caller"], state["second"], state["third"], fourth)}
    check("four sessions of one project, four boxes", len(boxes) == 4, boxes)
    forked()


def forked() -> None:
    """A fork starts with a copy of its origin's grants, taken once."""
    win = state["win"]
    host = probe_host()
    origin = win.store.get_session(ORIGIN)
    check("the earlier session is in the store", origin is not None)
    if origin is None:
        narrowed()
        return
    check("it holds a directory of its own", host.grants(ORIGIN_BOX) == [OTHER], host.grants(ORIGIN_BOX))
    host.set_project_default(TRUSTED, PINNED, True)  # a fork takes no defaults
    before = {win.tab_view.get_nth_page(i).get_child() for i in range(win.tab_view.get_n_pages())}
    win.open_session(origin, fork=True)
    host.set_project_default(TRUSTED, PINNED, False)
    fork = next(
        (
            win.tab_view.get_nth_page(i).get_child()
            for i in range(win.tab_view.get_n_pages())
            if win.tab_view.get_nth_page(i).get_child() not in before
        ),
        None,
    )
    check("the fork opened", fork is not None and fork.fork)
    if fork is None:
        narrowed()
        return
    e2e_service.settle()  # the session event carrying the options lands a moment later
    box = fork.launch_options.sandbox_box
    check("the window minted the fork's box", sandboxplan.valid_box_id(box) and box != ORIGIN_BOX, box)
    check("the fork's list equals its origin's at the fork", host.grants(box) == [OTHER], host.grants(box))
    check(
        "…and so do the tools it is offered",
        host.tool_overrides(box) == {"read_terminal": True, "show_image": False},
        host.tool_overrides(box),
    )
    host.set_tool(ORIGIN_BOX, "start_session", True)
    check("…a copy: the origin's next switch is the origin's", "start_session" not in host.tool_overrides(box))
    check("the origin still names its own box", AppState().sandbox_box(ORIGIN) == ORIGIN_BOX)
    # The origin is allowed another: the fork's list stays put.
    check("the origin is allowed another", host.allow(ORIGIN_BOX, TRUSTED, EXTRA) == "")
    check("the fork's list stays put", host.grants(box) == [OTHER], host.grants(box))
    host.revoke(box, OTHER)
    check("…and the origin's, when the fork drops one", host.grants(ORIGIN_BOX) == [OTHER, EXTRA])
    narrowed()


def narrowed() -> None:
    """A launch that asks the CLI for a worktree: narrowed to it."""
    launch(narrowed_typed, CHECKOUT, worktree=True)


def in_checkout() -> list[list[str]]:
    """The launches that started in the checkout, in order. By where they
    start, not by count: the fork before them reaches the fake bwrap when
    its own shell gets to it."""
    return [
        args
        for args in launches()
        if "--chdir" in args and args[args.index("--chdir") + 1] == CHECKOUT
    ]


def narrowed_typed(tab) -> None:
    """The plan is settled before the shell has run the command typed into
    it: wait for the launch itself to reach the fake bwrap."""
    ticks = {"n": 0}

    def seen() -> bool:
        ticks["n"] += 1
        if not in_checkout() and ticks["n"] < 100:
            return GLib.SOURCE_CONTINUE
        narrowed_up(tab)
        return GLib.SOURCE_REMOVE

    GLib.timeout_add(100, seen)


def narrowed_up(tab) -> None:
    host = probe_host()
    state["narrow"] = tab
    options = tab.launch_options
    name = options.worktree_name
    own = f"{CHECKOUT}/.claude/worktrees/{name}"
    state["own"] = own
    check("a worktree launch is sandboxed", tab.sandboxed and options.worktree, options)
    check("the tab named the worktree", sandboxplan.valid_worktree_name(name), name)
    check("…and made its directory, empty", os.path.isdir(own) and os.listdir(own) == [], own)
    typed = tab.probe("initial_command") or ""
    check("the name is typed after the flag", f" -w {name} " in f"{typed} ", typed)
    doc = sandboxplan.load_plan(tab.sandbox_plan_path)
    check("the plan's workspace is the worktree", bool(doc) and doc["workspace"] == own, doc and doc["workspace"])
    check("…started in the checkout", bool(doc) and sandboxplan.plan_start_dir(doc) == CHECKOUT)
    seen = in_checkout()
    check("the launch starts in the checkout", len(seen) == 1, len(seen))
    args = seen[-1] if seen else []
    check("the checkout is bound read-only", (CHECKOUT, CHECKOUT) in after(args, "--ro-bind"), args)
    check(
        "…and never read-write",
        (CHECKOUT, CHECKOUT) not in after(args, "--bind") + after(args, "--bind-try"),
        args,
    )
    claude_dir = f"{CHECKOUT}/.claude"
    check("so is the directory that holds every worktree", (claude_dir, claude_dir) in after(args, "--ro-bind-try"), args)
    check("the worktree is bound read-write", (own, own) in after(args, "--bind"), args)
    check("the git directory stays shared", (f"{CHECKOUT}/.git", f"{CHECKOUT}/.git") in after(args, "--bind-try"), args)
    chip = tab._sandbox_chip
    chip._rebuild()
    texts = labels(chip._content)
    check("the chip names the worktree", any(t.endswith(f"/worktrees/{name}") for t in texts), texts)
    read_only = [t for t in texts if t.startswith("Read-only: ")]
    check("…and the checkout, read-only", len(read_only) == 1 and read_only[0].endswith("/dev/gamma"), texts)
    check("the launched plan is not stale", not host.plan_stale(tab.sandbox_plan_path, own))
    # The checkout is not "already inside": the user can allow it.
    check("the guard would allow the checkout", host.grant_reason(own, CHECKOUT) == "", host.grant_reason(own, CHECKOUT))
    # A sibling collapses to the repository, which the box can't write.
    got = app.tool_client.start_session((state["win"], tab), {"prompt": "hi", "cwd": own}, True)
    check(
        "a sibling of a session in its worktree is refused",
        isinstance(got, tuple) and got[0] is False and "outside the sandbox's workspace" in got[1],
        got,
    )
    tab.probe_set("session_id", NARROWED)
    tab.probe_call("host.session_resolved", NARROWED)
    check("the resolved session is sticky-sandboxed", AppState().is_sandboxed(NARROWED))
    check("the restart starts", tab.restart_sandboxed())
    GLib.timeout_add(5000, narrowed_restarted)


def narrowed_restarted() -> bool:
    tab = state["narrow"]
    own = state["own"]
    seen = in_checkout()
    check("the fake bwrap saw the relaunch, in the checkout", len(seen) == 2, len(seen))
    args = seen[-1] if seen else []
    check("the restart binds the same worktree", (own, own) in after(args, "--bind"), args)
    check("…and the checkout read-only still", (CHECKOUT, CHECKOUT) in after(args, "--ro-bind"), args)
    typed = tab.probe("initial_command") or ""
    check("the session is resumed", f"--resume {NARROWED}" in typed and " -w" not in typed, typed)
    check("…and up again", tab.has_running_command())
    # The CLI reaped the worktree on its way out, and it can't be put back:
    # the transcript records a branch the repository doesn't have and no
    # commit to cut one from.
    lost = f"{E2E}/narrowed-lost.jsonl"
    record = {
        "type": "worktree-state",
        "worktreeSession": {
            "worktreePath": own,
            "worktreeBranch": sandboxplan.worktree_branch(os.path.basename(own)),
        },
    }
    with open(lost, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    tab.probe_call("transcript.set_path", lost)
    check("the worktree is there, and empty", os.path.isdir(own) and os.listdir(own) == [], own)
    check("the second restart starts", tab.restart_sandboxed())
    GLib.timeout_add(5000, narrowed_lost)
    return GLib.SOURCE_REMOVE


def narrowed_lost() -> bool:
    tab = state["narrow"]
    own = state["own"]
    text = " ".join(tab.probe_call("visible_screen_text").split())
    check("a worktree that couldn't be put back is said", "couldn't be recreated" in text, text[-400:])
    seen = in_checkout()
    check("the fake bwrap saw the relaunch all the same", len(seen) == 3, len(seen))
    args = seen[-1] if seen else []
    check("…in a box no wider than it was", (CHECKOUT, CHECKOUT) in after(args, "--ro-bind"), args)
    check(
        "…the checkout never read-write",
        (CHECKOUT, CHECKOUT) not in after(args, "--bind") + after(args, "--bind-try"),
        args,
    )
    check("…around the worktree's directory", (own, own) in after(args, "--bind"), args)
    typed = tab.probe("initial_command") or ""
    check("the session is resumed", f"--resume {NARROWED}" in typed and " -w" not in typed, typed)
    check("…and up again", tab.has_running_command())
    tab.probe_call("transcript.set_path", None)
    # The CLI leaves, as it does when it can't cut the worktree.
    tab.feed_child_text("\x03")
    GLib.timeout_add(1500, narrowed_fallback)
    return GLib.SOURCE_REMOVE


def narrowed_fallback() -> bool:
    tab = state["narrow"]
    check("the CLI is gone", not tab.has_running_command())
    state["narrow_plan"] = tab.sandbox_plan_path
    tab.probe_call("relaunch_without_worktree")
    GLib.timeout_add(3000, narrowed_fell_back)
    return GLib.SOURCE_REMOVE


def narrowed_fell_back() -> bool:
    tab = state["narrow"]
    own = state["own"]
    options = tab.launch_options
    check("without its worktree, the launch drops the flag", not options.worktree and not options.worktree_name, options)
    check("…and stays sandboxed", tab.sandboxed and bool(tab.sandbox_plan_path))
    check("…on a plan of its own", tab.sandbox_plan_path != state["narrow_plan"], tab.sandbox_plan_path)
    check("the plan before it is released", not os.path.exists(state["narrow_plan"]))
    doc = sandboxplan.load_plan(tab.sandbox_plan_path)
    check("whose workspace is the checkout", bool(doc) and doc["workspace"] == CHECKOUT, doc and doc["workspace"])
    check("…not narrowed", bool(doc) and sandboxplan.plan_launch_dir(doc) is None)
    seen = in_checkout()
    check("the fake bwrap saw the launch", len(seen) == 4, len(seen))
    args = seen[-1] if seen else []
    check("the checkout is bound read-write", (CHECKOUT, CHECKOUT) in after(args, "--bind"), args)
    check("…and the worktree not at all", not any(own == a for a in args), args)
    typed = tab.probe("initial_command") or ""
    check("the command is typed without the flag", " -w" not in typed and "--resume" not in typed, typed)
    check("the session is up", tab.has_running_command())
    for _tick in range(100):
        if not os.path.exists(own):
            break
        GLib.usleep(20_000)
    check("the reserved directory is tidied away", not os.path.exists(own), own)
    live()
    return GLib.SOURCE_REMOVE


def live() -> bool:
    """The second pass, where the machine can: bindfs back, a directory
    allowed through the chip while the session runs is tagged *live* and
    nothing asks for a restart."""
    caller = state["caller"]
    host = probe_host()
    del os.environ["COLLINS_BINDFS"]
    # The service's own live grants, built again in place of the ones that
    # had no bindfs: what a launch on this machine gets.
    _service_call("core", "restart_grants")
    mounts = probe_grants()
    reason = mounts.capable()
    if reason:
        print(f"SKIP  the live pass: {reason}", flush=True)
        finish()
        return GLib.SOURCE_REMOVE
    # The grant taken back in the first pass, allowed again, and the one
    # that waited for a restart dropped: the box holds the first
    # statically, and the state agrees with the box from here.
    box = caller.sandbox_box
    host.revoke(box, EXTRA)
    host.allow(box, TRUSTED, OTHER)
    mounts.register(sandboxplan.load_plan(caller.sandbox_plan_path))
    # The second session of the project, running beside it.
    mounts.register(sandboxplan.load_plan(state["second"].sandbox_plan_path))
    check("the box holds the first grant statically", mounts.status(box, OTHER) == "static")
    check("…and is not stale", not host.plan_stale(caller.sandbox_plan_path, TRUSTED))
    state["toasts"].clear()
    with open(f"{LIVE}/hello.txt", "w", encoding="utf-8") as fh:
        fh.write("from the host\n")
    # The chip's own flow, minus the folder chooser.
    caller._sandbox_chip.allow_directory(TRUSTED, LIVE)
    state["polls"] = 0
    GLib.timeout_add(50, delivered)
    return GLib.SOURCE_REMOVE


def delivered() -> bool:
    caller = state["caller"]
    host = probe_host()
    mounts = probe_grants()
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
    check("the grant is recorded, for this session", host.grants(box) == [OTHER, LIVE], host.grants(box))
    # The second session of the same project, running beside it.
    second = state["second"]
    beside = second.sandbox_box
    check("the second session was allowed nothing", host.grants(beside) == [], host.grants(beside))
    check("…holds nothing live", mounts.live_paths(beside) == [] and mounts.delivery(beside, LIVE) is None)
    check("…has an empty carrier", os.listdir(sandboxplan.box_carrier(beside)) == [])
    check("…and no link in its home", not os.path.lexists(f"{sandboxplan.box_home(beside)}/dev/live"))
    mine, _defaults = chip_rows(second)
    check("…and its chip lists nothing", mine == [], mine)
    chip = caller._sandbox_chip
    chip._rebuild()
    texts = labels(chip._content)
    check("the chip tags the row live", "live" in texts, texts)
    check("…and nothing after restart", "after restart" not in texts, texts)
    names = [b.get_label() for b in buttons(chip._content) if b.get_label()]
    check("no Restart to apply: the box holds what the state grants", "Restart to apply" not in names, names)
    check("both rows can be removed", len([b for b in buttons(chip._content) if b.get_icon_name() == "list-remove-symbolic"]) == 2)
    # A sibling can't start inside a directory its parent holds only live.
    got = app.tool_client.start_session(found(), {"prompt": "hi", "cwd": LIVE}, True)
    check(
        "a sibling inside the live grant is refused, and told why",
        isinstance(got, tuple) and got[0] is False and "was allowed while the parent session was running" in got[1],
        got,
    )
    # Taken back through its remove button: out of the running box.
    remove = [b for b in buttons(chip._content) if b.get_icon_name() == "list-remove-symbolic"]
    remove[-1].emit("clicked")
    check("the remove button revokes it", host.grants(box) == [OTHER], host.grants(box))
    state["polls"] = 0
    GLib.timeout_add(50, revoked)
    return GLib.SOURCE_REMOVE


def revoked() -> bool:
    caller = state["caller"]
    mounts = probe_grants()
    state["polls"] += 1
    if mounts_under(E2E) and state["polls"] < 200:
        return GLib.SOURCE_CONTINUE
    check("revoked, nothing is left mounted", mounts_under(E2E) == [], mounts_under(E2E))
    link = f"{sandboxplan.box_home(caller.sandbox_box)}/dev/live"
    check("…and the link is gone", not os.path.lexists(link))
    check("the directory itself is untouched", os.path.exists(f"{LIVE}/hello.txt"))
    check("the live grants hold nothing", mounts.live_paths(caller.sandbox_box) == [])
    # Allowed again, and then the session restarted over it: what is
    # mounted goes first, off the main loop, and the relaunch binds the
    # directory itself.
    state["toasts"].clear()
    caller._sandbox_chip.allow_directory(TRUSTED, LIVE)
    state["polls"] = 0
    GLib.timeout_add(50, live_again)
    return GLib.SOURCE_REMOVE


def live_again() -> bool:
    caller = state["caller"]
    state["polls"] += 1
    if not state["toasts"] and state["polls"] < 200:
        return GLib.SOURCE_CONTINUE
    check("allowed again, it is mounted again", len(mounts_under(E2E)) == 1, mounts_under(E2E))
    state["plan_live"] = caller.sandbox_plan_path
    state["launches"] = len(launches())
    check("the restart starts over a live grant", caller.restart_sandboxed())
    GLib.timeout_add(5000, restarted_live)
    return GLib.SOURCE_REMOVE


def restarted_live() -> bool:
    caller = state["caller"]
    host = probe_host()
    mounts = probe_grants()
    plan = caller.sandbox_plan_path
    check("the restart wrote a fresh plan", bool(plan) and plan != state["plan_live"], plan)
    check("nothing is mounted any more", mounts_under(E2E) == [], mounts_under(E2E))
    link = f"{sandboxplan.box_home(caller.sandbox_box)}/dev/live"
    check("the link is out of the way of the bind", not os.path.lexists(link))
    doc = sandboxplan.load_plan(plan)
    check(
        "the relaunch binds what was live itself",
        bool(doc) and doc["inputs"]["grants"] == [OTHER, LIVE],
        doc and doc["inputs"]["grants"],
    )
    seen = launches()
    check("the fake bwrap saw the relaunch", len(seen) == state["launches"] + 1, len(seen))
    if seen:
        check("…binding the directory at its real path", (LIVE, LIVE) in after(seen[-1], "--bind-try"))
    check("the box is registered again, in the same home", mounts.registered(caller.sandbox_box))
    check("…holding the grant statically", mounts.status(caller.sandbox_box, LIVE) == "static")
    check("…with nothing live", mounts.live_paths(caller.sandbox_box) == [])
    check("and is not stale", not host.plan_stale(plan, TRUSTED, live=mounts.live_paths(caller.sandbox_box)))
    check("the session is up again", caller.has_running_command())
    # A static grant taken back stays in the running box until the restart.
    probe_host().revoke(caller.sandbox_box, OTHER)
    chip = caller._sandbox_chip
    chip._rebuild()
    texts = labels(chip._content)
    check("a static grant taken back is tagged until restart", "until restart" in texts, texts)
    remove = [b for b in buttons(chip._content) if b.get_icon_name() == "list-remove-symbolic"]
    check("…with no remove button: only the grant still held has one", len(remove) == 1, len(remove))
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
        probe = getattr(tab, "probe_call", None)
        pid = probe("child_pid") if probe is not None else None
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
