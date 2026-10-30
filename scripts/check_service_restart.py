#!/usr/bin/env python3
# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""End-to-end check that a service restart keeps every session (spec §3.10
item 2, PR-3.6: the keeper and the adoption).

A service with its keeper, one Collins on it with two sessions against a
`claude` stub: a plain one, typed into, and one in a sandbox (the fake
bubblewrap of check_sandbox_policy) with a directory allowed to it. The
service is stopped with SIGTERM, as *Restart service*, an upgrade and
`systemctl --user restart` stop it: it must exit 0 and leave both agents
alive under the keeper, their rows in the pty table and their saved
models on disk. A second service on the same state must adopt both: the
window reconnects by itself and re-attaches each tab, whose screen must
match the service's model of record (`debug.screen`) text for text with
the first service's typing on it, typing must reach the same process (the
pid the pty table named before), and the sandboxed session's box must be
held again under the new service's pid (its lease), its plan file kept
and its live grants registered again, the allowed directory in the same
state as before the restart (live where the machine delivers grants live,
pending where it cannot: either way, what the first service had).

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_service_restart.py
"""

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

# Under ~/.cache, not /tmp: a box carries /tmp, so a plan whose state file
# lives there is refused (check_sandbox_policy stages the same way).
STAGE = os.path.join(os.path.expanduser("~"), ".cache", "collins-e2e")
os.makedirs(STAGE, exist_ok=True)
E2E = tempfile.mkdtemp(prefix="service-restart-", dir=STAGE)
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())
HOME = f"{E2E}/home"


def mounts_under(tree: str) -> list[str]:
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
    """Remove the scratch tree, never through a mount (a live grant's
    bindfs mount *is* the granted directory): unmount first."""
    for point in mounts_under(E2E):
        subprocess.run(["fusermount3", "-u", "-z", point], check=False, capture_output=True)
    if mounts_under(E2E):
        print(f"not removing {E2E}: still mounted", file=sys.stderr)
        return
    shutil.rmtree(E2E, ignore_errors=True)


atexit.register(clear_tree)

os.environ["HOME"] = HOME
os.environ["COLLINS_DEBUG_API"] = "1"
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["COLLINS_SANDBOX_ROOT"] = f"{E2E}/sbx"
os.environ["COLLINS_PTY_STATE_DIR"] = f"{E2E}/pty"
os.environ["COLLINS_UPLOADS_DIR"] = f"{E2E}/uploads"
os.environ["COLLINS_BWRAP"] = f"{E2E}/bin/bwrap"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"
os.environ["XDG_CACHE_HOME"] = f"{E2E}/cache"
os.environ["XDG_DATA_HOME"] = f"{HOME}/.local/share"
os.environ["SANDBOX_FAKE_LOG"] = f"{E2E}/bwrap.log"
os.environ["RESTART_WINCH_LOG"] = f"{E2E}/winch.log"
os.environ.pop("COLLINS_KEEPER", None)  # the keeper is the point
os.environ.pop("TMPDIR", None)
for var in list(os.environ):
    if var.startswith("CLAUDE_"):
        del os.environ[var]

PLAIN = f"{E2E}/dev/plain"  # pinned unsandboxed: the typed-into session
BOXED = f"{E2E}/dev/alpha"  # sandboxed by the project default
GRANT = f"{HOME}/dev/granted"  # allowed to the boxed session while it runs
SHIM = f"{E2E}/bin/claude"
STATE_FILE = f"{E2E}/config/collins/state.json"
MODELS = f"{E2E}/pty/{os.environ['COLLINS_APP_ID']}"  # the saved models, by app id
SCAN_GATE = f"{E2E}/scan-gate"  # the restarted service's scans wait for this file (V5-S1)
WINCH_LOG = f"{E2E}/winch.log"  # the stubs' SIGWINCH sizes (S8: the repaint proven)
TYPED = "typed before the restart"
AFTER = "typed after the restart"

for path in (f"{E2E}/projects", f"{E2E}/chats", f"{E2E}/bin", PLAIN, BOXED, GRANT, f"{HOME}/.claude"):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
os.makedirs(f"{E2E}/config/collins", exist_ok=True)
with open(STATE_FILE, "w", encoding="utf-8") as fh:
    json.dump(
        {
            "settings": {
                "welcome_seen": True,
                "gh_welcome_dismissed": True,
                "title_model": "none",
                "sandbox_new_sessions": True,
            },
            "project_sandbox": {"plain": False},
        },
        fh,
    )

# The stub: the CLI's box (❯ + U+00A0), a transcript so the row resolves,
# and a line editor that echoes what it is typed, so the screen holds the
# first service's typing for the second one to show.
_SHIM = r"""#!/usr/bin/env python3
import json, os, signal, sys, time, tty, uuid
projects = os.environ["COLLINS_PROJECTS_DIR"]
cwd = os.getcwd()
encoded = "".join(c if c.isalnum() else "-" for c in cwd)
os.makedirs(os.path.join(projects, encoded), exist_ok=True)
sid = str(uuid.uuid4())
now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
with open(os.path.join(projects, encoded, sid + ".jsonl"), "w") as fh:
    fh.write(json.dumps({"type": "user", "cwd": cwd, "sessionId": sid, "timestamp": now,
                         "message": {"role": "user", "content": "survive the restart"}}) + "\n")
tty.setraw(0)
text = ""
def draw():
    sys.stdout.write("\r\x1b[K❯ " + text)
    sys.stdout.flush()
# The CLI repaints on SIGWINCH (F6/F12); so does this stub, logging each
# size it saw (S8: the adoption's repaint, a resize of one row and back,
# is proven by the sizes the stub was shown, in order).
def winch(*_a):
    size = os.get_terminal_size(0)
    with open(os.environ["RESTART_WINCH_LOG"], "a") as fh:
        fh.write(f"{os.getcwd()} {size.columns} {size.lines}\n")
    draw()
signal.signal(signal.SIGWINCH, winch)
sys.stdout.write("session " + sid + "\r\n")
draw()
while True:
    try:
        chunk = os.read(0, 4096)
    except InterruptedError:
        continue
    if not chunk:
        break
    for b in chunk:
        if b == 0x7F:
            text = text[:-1]
        elif b == 0x0D:
            sys.stdout.write("\r\n" + "you said: " + text + "\r\n")
            text = ""
        elif 0x20 <= b < 0x7F:
            text += chr(b)
    draw()
"""
with open(SHIM, "w", encoding="utf-8") as fh:
    fh.write(_SHIM)
os.chmod(SHIM, 0o755)
# The fake bubblewrap (check_sandbox_policy's): dump the plan, run the
# command in place.
_FAKE = r"""#!/usr/bin/env python3
import os, sys
if len(sys.argv) < 3 or sys.argv[1] != "--args":
    sys.exit(0)
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
with open(os.environ["COLLINS_BWRAP"], "w", encoding="utf-8") as fh:
    fh.write(_FAKE)
os.chmod(os.environ["COLLINS_BWRAP"], 0o755)
os.environ["PATH"] = f"{E2E}/bin:{os.environ['PATH']}"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import e2e_service  # noqa: E402

PASSED = 0
FAILED = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASSED, FAILED
    # Flushed: a check that dies on its deadline (os._exit) would otherwise
    # take every buffered result line with it.
    if ok:
        PASSED += 1
        print(f"  ok  {label}", flush=True)
    else:
        FAILED += 1
        print(f"FAIL  {label}  {detail!r}", flush=True)


def watchdog() -> None:
    time.sleep(240)
    print("timed out", file=sys.stderr)
    # os._exit skips the atexit handlers: the services, the keeper (and
    # its children, the check's shims) and the tree are ended here first.
    e2e_service._stop_all()
    clear_tree()
    os._exit(3)


threading.Thread(target=watchdog, daemon=True).start()


def state_json() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def alive(pid: int | None) -> bool:
    return bool(pid) and os.path.exists(f"/proc/{pid}")


def read_lease(box: str) -> dict:
    try:
        with open(f"{E2E}/sbx/{box}/lease", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import GLib  # noqa: E402

from collins import i18n, sandboxstatus, trust  # noqa: E402
from collins import terminal as terminal_mod  # noqa: E402
from collins.app import App  # noqa: E402
from collins.connection import CONNECTED  # noqa: E402
from collins.state import AppState  # noqa: E402

i18n.init(AppState().get_setting("language"))
trust.trust_dir(PLAIN)
trust.trust_dir(BOXED)
service = e2e_service.start_service()
app = App()


def probe(target: str, name: str, *args):
    reply = app._service_link.call({"t": "debug.sandbox", "target": target, "name": name, "args": list(args)})
    return reply.get("value")


def vte_lines(tab) -> list[str]:
    text = terminal_mod._capture_contents(tab.terminal)
    return [line.rstrip() for line in text.splitlines() if line.strip()]


def model_lines(tab, pty: int) -> list[str]:
    screen = tab._client.request({"t": "debug.screen", "pty": pty})
    return [line.rstrip() for line in screen["capture"].splitlines() if line.strip()]


IN_TAB = ("open", "attention")  # sidebar._IN_TAB_STATUSES: a tab shows the row, selected or not


def row_status(session_id: str | None) -> str | None:
    item = app.store.get_item(session_id) if session_id else None
    return None if item is None else item.status


def footer_text(win) -> str:
    return win.sidebar.footer.get_label() or ""


def steps():
    global service
    win = app.get_active_window()
    while win is None or sandboxstatus.probe_reason() is None:
        yield 100
        win = app.get_active_window()
    check("the fake bwrap passes the probe", sandboxstatus.probe_reason() == "")
    status = app._service_link.call({"t": "service.status"})
    keeper_pid = status.get("keeper")
    check("the service runs with a keeper", bool(keeper_pid) and alive(keeper_pid), status)
    first_pid = status.get("pid")

    plain = win.start_background_session(PLAIN)
    boxed = win.start_background_session(BOXED)
    check("two sessions started", plain is not None and boxed is not None)
    for _ in range(150):
        if plain.takes_prompt() and boxed.takes_prompt() and plain.session_id and boxed.session_id:
            break
        yield 200
    ids = (plain.session_id, boxed.session_id)
    check("both sessions resolved", all(ids), ids)
    check("the second session launched sandboxed", boxed.sandboxed and bool(boxed.sandbox_box), ids)
    # The rows' in-tab status (D29: the client's word) and the footer's count
    # of them, to survive the restart below.
    for _ in range(50):
        if all(row_status(sid) in IN_TAB for sid in ids) and "2 open" in footer_text(win):
            break
        yield 100
    check("both rows are open rows before the restart", all(row_status(sid) in IN_TAB for sid in ids),
          [row_status(sid) for sid in ids])
    check("…and the footer counts the two open tabs", "2 open" in footer_text(win), footer_text(win))
    box = boxed.sandbox_box
    plan_path = boxed.sandbox_plan_path
    check("…with a plan file", bool(plan_path) and os.path.isfile(plan_path), plan_path)
    plain.feed_child_text(TYPED + "\r")
    for _ in range(50):
        if any(TYPED in line for line in vte_lines(plain)):
            break
        yield 100
    check("the typing shows on the plain tab", any(TYPED in line for line in vte_lines(plain)))

    # A directory allowed while the boxed session runs: live where the
    # machine can (a real bindfs and FUSE), pending where it cannot.
    if not box:
        check("the boxed session has a box to allow a directory to", False, boxed.sandbox_plan_path)
        app.quit()
        return
    try:
        app._service_link.call({"t": "sandbox.allow", "box": box, "path": GRANT, "scope": "session"})
        check("the directory is allowed to the boxed session", True)
    except Exception as exc:  # noqa: BLE001 - the check reports, never dies
        check("the directory is allowed to the boxed session", False, exc)
    yield 1500
    grants = app._service_link.call({"t": "sandbox.grants", "box": box})
    rows = {row["path"]: row for row in grants.get("grants", [])}
    check("…and listed for its box", GRANT in rows, grants)
    status_before = rows.get(GRANT, {}).get("status")
    print(f"      the grant stands {status_before!r} before the restart")
    check("the box is held by the first service", probe("host", "held", box) is True)
    check("…and registered with the live grants", probe("grants", "registered", box) is True)
    check("…its lease is the first service's pid", read_lease(box).get("pid") == first_pid, read_lease(box))

    rows_before = (state_json().get("ptys") or {}).get(os.environ["COLLINS_APP_ID"]) or {}
    plain_pty, boxed_pty = plain._view.pty, boxed._view.pty
    both = {str(plain_pty), str(boxed_pty)}
    check("the pty table holds both", both <= set(rows_before), sorted(rows_before))
    pids = {pty: (rows_before.get(str(pty)) or {}).get("pid") for pty in (plain_pty, boxed_pty)}
    check("…with their pids", all(alive(pid) for pid in pids.values()), pids)

    # -- the restart: SIGTERM to the service, as every restart is ----------------------
    e2e_service.stop_service(service)
    check("the service exited 0 on SIGTERM", service.returncode == 0, service.returncode)
    check("the keeper lives on", alive(keeper_pid))
    check("both agents live on under it", all(alive(pid) for pid in pids.values()), pids)
    after = state_json()
    kept_rows = (after.get("ptys") or {}).get(os.environ["COLLINS_APP_ID"]) or {}
    check("the pty table kept both rows", both <= set(kept_rows), sorted(kept_rows))
    check("nothing is recorded to resume", after.get("resume_on_start", []) == [])
    check(
        "both saved models are on disk",
        all(os.path.isfile(f"{MODELS}/{pty}.model") for pty in (plain_pty, boxed_pty)),
        sorted(os.listdir(MODELS)) if os.path.isdir(MODELS) else None,
    )
    check("the plan file survived the stop", bool(plan_path) and os.path.isfile(plan_path), plan_path)

    # -- the next service: the window reconnects, the tabs attach again --------------
    # Its scans wait for SCAN_GATE (a large projects directory): the window
    # reconnects and re-attaches its tabs before any row has landed, the
    # order V5-S1 is about, and the rows land when the check opens the gate.
    service = e2e_service.start_service(stubs={"scan_gate": SCAN_GATE})
    for _ in range(300):
        if app._connection.state == CONNECTED and app._service_link is not None:
            try:
                status = app._service_link.call({"t": "service.status"})
                break
            except Exception:
                pass
        yield 100
    else:
        check("the window reconnected to the new service", False, app._connection.state)
        app.quit()
        return
    check("the window reconnected to the new service", status.get("pid") != first_pid, status)
    # The mirror keeps the old items until the new snapshot's rows reconcile
    # them (RemoteStore.reset); `known_sessions` is what the new service said.
    check("…before any row of its had landed (the scan waits at the gate)", not app.store.known_sessions,
          sorted(app.store.known_sessions))
    check("the new service adopted both ptys", status.get("ptys") == 2, status)
    check("…and found the same keeper", status.get("keeper") == keeper_pid, status)
    second_pid = status.get("pid")
    for tab in (plain, boxed):
        for _ in range(100):
            if not tab._view.guarded and tab._view.pty:
                break
            yield 100
    yield 1000  # the repaint's two steps, and the tabs' redraws
    for name, tab, pty in (("plain", plain, plain_pty), ("boxed", boxed, boxed_pty)):
        info = tab._client.request({"t": "pty.info", "pty": pty})
        same = info.get("child_pid") == pids[pty]
        check(f"the {name} tab is attached to the same process", same, (info, pids))
        check(
            f"the {name} tab's screen matches the model of record",
            vte_lines(tab) == model_lines(tab, pty),
            (vte_lines(tab)[-3:], model_lines(tab, pty)[-3:]),
        )
    check("the first service's typing is on the plain tab", any(TYPED in line for line in vte_lines(plain)))
    # The repaint (S8): each stub saw a resize of one row and back, in that
    # order, and the pty's size now equals the tab's grid.
    try:
        with open(WINCH_LOG, encoding="utf-8") as fh:
            winches = [line.split() for line in fh if line.strip()]
    except OSError:
        winches = []
    for name, tab, pty, cwd in (("plain", plain, plain_pty, PLAIN), ("boxed", boxed, boxed_pty, BOXED)):
        info = tab._client.request({"t": "pty.info", "pty": pty})
        seen = [(int(c), int(r)) for where, c, r in winches if where == cwd]  # the stub logs its cwd
        cols, rows = info.get("cols"), info.get("rows")
        step = (cols, rows + 1) if rows < 500 else (cols, rows - 1)
        repainted = any(seen[i] == step and seen[i + 1] == (cols, rows) for i in range(len(seen) - 1))
        check(f"the {name} agent was shown one row more, then its size again (the repaint)", repainted, seen)
        grid = (tab.terminal.get_column_count(), tab.terminal.get_row_count())
        check(f"the {name} pty's size is the tab's grid", (cols, rows) == grid, ((cols, rows), grid))
    plain.feed_child_text(AFTER + "\r")
    for _ in range(50):
        if any(("you said: " + AFTER) in line for line in vte_lines(plain)):
            break
        yield 100
    answered = any(("you said: " + AFTER) in line for line in vte_lines(plain))
    check("typing reaches the adopted agent", answered)
    # The tabs are attached and repainted with no row in the mirror: now the
    # scan may read, and the rows land on a window whose reattach is over.
    check("no row had landed before the gate opened", not app.store.known_sessions,
          sorted(app.store.known_sessions))
    with open(SCAN_GATE, "w", encoding="utf-8") as fh:
        fh.write("go\n")
    for _ in range(100):
        if app.store.known_sessions:
            break
        yield 100
    check("the rows landed after the tabs had re-attached", bool(app.store.known_sessions))
    model = app.store.model
    items = [model.get_item(i) for i in range(model.get_n_items())]
    item = next((i for i in items if getattr(i, "session_id", None) == plain.session_id), None)
    check("the plain session is a running row", item is not None and bool(item.running), item)
    # The restarted service started every row blank, and its rows landed
    # after the reattach; the window said again what it shows
    # (MainWindow._resync_statuses, from the reattach and from every store
    # refresh), so the rows are open rows and the footer counts them as before.
    for _ in range(50):
        if all(row_status(sid) in IN_TAB for sid in ids) and "2 open" in footer_text(win):
            break
        yield 100
    check("both rows are open rows after the restart", all(row_status(sid) in IN_TAB for sid in ids),
          [row_status(sid) for sid in ids])
    check("…and the footer counts the two open tabs again", "2 open" in footer_text(win), footer_text(win))

    # -- the sandbox: re-held, re-mounted, in §3.9's order ------------------------------
    check("the box is held by the new service", probe("host", "held", box) is True)
    check("…its lease rewritten under its pid", read_lease(box).get("pid") == second_pid, read_lease(box))
    check("…and registered with the live grants again", probe("grants", "registered", box) is True)
    check("the plan file was kept from the sweep", os.path.isfile(plan_path), plan_path)
    rows = {}
    for _ in range(50):
        try:
            grants = app._service_link.call({"t": "sandbox.grants", "box": box})
        except Exception as exc:  # noqa: BLE001 - reported below, never raised through the tick
            grants = {"error": repr(exc)}
        rows = {row["path"]: row for row in grants.get("grants", [])}
        if rows.get(GRANT, {}).get("status") == status_before:
            break
        yield 200
    check(
        "the allowed directory stands as it did before the restart",
        rows.get(GRANT, {}).get("status") == status_before,
        (rows.get(GRANT), status_before),
    )
    # Where this machine delivers grants live (bindfs, fusermount3, FUSE,
    # a shared mount holding the sandbox root) the grant must be live again
    # (mounted again by the new service); where it cannot (CI) both readings
    # are "pending", and the lines above prove the registration alone.
    capable = probe("grants", "capable")
    if capable == "":
        live_again = rows.get(GRANT, {}).get("status") == "live"
        check("…live again where the machine delivers grants live", live_again, rows.get(GRANT))
    else:
        print(f"      (grants are not delivered live here: {capable!r}; registration proven, not the mount)")
    check("the boxed tab's mirror still names its box", boxed.sandbox_box == box, boxed.sandbox_box)
    app.quit()


generator = steps()


def tick() -> bool:
    try:
        delay = next(generator)
    except StopIteration:
        return GLib.SOURCE_REMOVE
    GLib.timeout_add(delay, tick)
    return GLib.SOURCE_REMOVE


GLib.timeout_add(250, tick)
app.run([])
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
