#!/usr/bin/env python3
# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""End-to-end check for two clients on one session (split-service spec
§3.3, §3.24 "Two clients"; PR-3.5).

One service, two clients: the app's window (a real TerminalTab on a
`claude` stub) and a second client driven from the check, a bare
`SocketLink` on another device (no second `App()` in one process). The
active client owns the pty's size; the other is pinned to it:

* the tab owns the size at first: no banner, the service's grid is the
  tab's;
* the second attaches at another grid: the reply says it is not the
  active one and whom the pty is sized for, and the grid stays the tab's;
* the second takes the size (a ``focus {focused: true}`` event): the
  service's grid becomes the second's, the tab is told it is not active,
  pins its VTE to that grid, shows "Sized for <device>", and sends no
  ``resize`` however its allocation moves, a pane smaller than the grid
  included (the fixed-size box: an allocation would regrid a VTE that
  fills it, F2);
* *Use this size* (the banner's button) takes it back: the banner goes,
  the box lets go, the tab's grid is the service's again, and the second
  is told it is pinned;
* a key typed into the pinned tab and a click on it take the size too.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_two_clients.py
"""

import os
import shutil
import sys
import tempfile
import threading
import time

E2E = tempfile.mkdtemp(prefix="collins-two-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

os.environ["COLLINS_DEBUG_API"] = "1"  # the e2e probe (debug.*): served only with this set
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["COLLINS_UPLOADS_DIR"] = f"{E2E}/uploads"
os.environ["COLLINS_PTY_STATE_DIR"] = f"{E2E}/pty"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"

TRUSTED = f"{E2E}/dev/alpha"
SHIM = f"{E2E}/bin/claude"

for path in (f"{E2E}/projects", f"{E2E}/chats", f"{E2E}/bin", TRUSTED):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
os.makedirs(f"{E2E}/config/collins", exist_ok=True)
with open(f"{E2E}/config/collins/state.json", "w", encoding="utf-8") as fh:
    fh.write('{"settings": {"welcome_seen": true, "gh_welcome_dismissed": true, "title_model": "none"}}')

# The stub: the CLI's box (❯ + U+00A0, what takes_prompt keys on), then it
# echoes what it is typed into the box, as a line editor would.
_SHIM = r"""#!/usr/bin/env python3
import os, sys, tty
tty.setraw(0)
text = ""
def draw():
    sys.stdout.write("\r\x1b[K❯\u00a0" + text)
    sys.stdout.flush()
draw()
while True:
    chunk = os.read(0, 4096)
    if not chunk:
        break
    for b in chunk:
        if b == 0x7F:
            text = text[:-1]
        elif 0x20 <= b < 0x7F:
            text += chr(b)
    draw()
"""
with open(SHIM, "w", encoding="utf-8") as fh:
    fh.write(_SHIM)
os.chmod(SHIM, 0o755)
os.environ["PATH"] = f"{E2E}/bin:{os.environ['PATH']}"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
import e2e_service  # noqa: E402
from gi.repository import GLib  # noqa: E402

from collins import i18n, ptyclient, sizerole, trust  # noqa: E402
from collins.api import server as api_server  # noqa: E402
from collins.api.client import SocketLink  # noqa: E402
from collins.app import App  # noqa: E402

PASSED = 0
FAILED = 0
SECOND_DEVICE = "phone"


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  ok  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}  {detail!r}")


def watchdog() -> None:
    time.sleep(110)
    print("timed out", file=sys.stderr)
    os._exit(3)


threading.Thread(target=watchdog, daemon=True).start()

i18n.init("en")  # the banner's words are compared in English
trust.trust_dir(TRUSTED)
e2e_service.start_service()
app = App()
state: dict = {}


def steps():
    win = app.get_active_window()
    while win is None:
        yield 100
        win = app.get_active_window()
    win.set_default_size(1400, 900)
    tab = win.start_background_session(TRUSTED)
    for _ in range(100):
        if tab.takes_prompt():
            break
        yield 200
    check("the stub is up at an empty box", tab.takes_prompt())
    win.tab_view.set_selected_page(win.tab_view.get_page(tab))
    view, first = tab._view, tab.terminal
    frame = view.frame
    check("the tab's terminal sits in the frame that pins it", isinstance(frame, ptyclient.TerminalFrame))

    def vte_grid() -> tuple[int, int]:
        return (first.get_column_count(), first.get_row_count())

    for _ in range(30):
        if first.get_width() > 0 and view._last_grid == vte_grid():
            break
        yield 100
    yield 300
    pty = view.pty

    def service_grid() -> tuple[int, int]:
        screen = tab._client.request({"t": "debug.screen", "pty": pty})
        return (screen["columns"], screen["row_count"])

    def wait(predicate, tries: int = 50):
        for _ in range(tries):
            if predicate():
                return True
            yield 100
        return predicate()

    own = vte_grid()
    check("the tab owns the size at first", view.active and not frame.banner.get_revealed())
    check("…and the service's grid is the tab's", service_grid() == own, (service_grid(), own))
    check("…with nothing pinned", not frame.pinbox.pinned)

    # Every resize the tab sends from here on.
    resizes: list[dict] = []
    real_send_event = tab._client.send_event

    def recording_send_event(message):
        if message.get("t") == "resize":
            resizes.append(dict(message))
        return real_send_event(message)

    tab._client.send_event = recording_send_event

    # -- the second client: a bare link on another device ------------------------------
    app_id = os.environ["COLLINS_APP_ID"]
    second_link = SocketLink(api_server.socket_path(app_id), app_id=app_id, device=SECOND_DEVICE)
    second_link.connect()
    events: list[dict] = []
    second = second_link.pty_client(lambda p, d, f: None, events.append, device=SECOND_DEVICE)
    theirs = (100, 30) if own != (100, 30) else (90, 28)
    reply = second.request({"t": "attach", "pty": pty, "cols": theirs[0], "rows": theirs[1]})
    check("the second attaches and is told it is not the active one", reply.get("active") is False, reply)
    check(
        "…the pty sized for the tab's device",
        reply.get("sized_for") == ptyclient.device_name(),
        (reply.get("sized_for"), ptyclient.device_name()),
    )
    check("…at the tab's grid (the second is pinned)", (reply.get("cols"), reply.get("rows")) == own, reply)

    def second_role() -> dict | None:
        found = [e for e in events if e.get("t") == "pty" and e.get("pty") == pty]
        return found[-1] if found else None

    yield from wait(lambda: second_role() is not None)
    check("…and its pty event says so", (second_role() or {}).get("active") is False, second_role())
    yield 300
    check("the grid is still the tab's", service_grid() == own, service_grid())
    check("the tab is still active", view.active and not frame.banner.get_revealed())

    # -- the second takes the size -----------------------------------------------------
    second.send_event({"t": "focus", "pty": pty, "focused": True})
    yield from wait(lambda: service_grid() == theirs)
    check("a focus event from the second takes the size", service_grid() == theirs, service_grid())
    yield from wait(lambda: (second_role() or {}).get("active") is True)
    check("…and the second is told it is active", (second_role() or {}).get("active") is True, second_role())
    yield from wait(lambda: not view.active)
    check("the tab is told it is not active", not view.active)
    yield from wait(lambda: vte_grid() == theirs)
    check("…pins its VTE to the second's grid", vte_grid() == theirs, (vte_grid(), theirs))
    check("…in the fixed-size box", frame.pinbox.pinned)
    check("…and shows the banner", frame.banner.get_revealed())
    check(
        "…naming the second's device",
        frame.banner.get_title() == sizerole.banner_title(SECOND_DEVICE) == f"Sized for {SECOND_DEVICE}",
        frame.banner.get_title(),
    )
    check("…as plain text", not frame.banner.get_use_markup())
    sent_before = len(resizes)
    # The allocation moves (the window grows, then shrinks): nothing regrids
    # the pinned VTE and nothing is sent.
    win.set_default_size(1500, 950)
    yield 600
    win.set_default_size(1300, 850)
    yield 600
    first.queue_resize()
    yield 400
    check("an allocation that moves leaves the pinned grid alone", vte_grid() == theirs, vte_grid())
    check("…and the tab sends no resize", len(resizes) == sent_before, resizes[sent_before:])
    check("…so the service's grid is still the second's", service_grid() == theirs, service_grid())

    # A grid larger than the tab's pane: the box holds it all the same.
    big = (min(400, own[0] + 120), min(200, own[1] + 60))
    second.send_event({"t": "resize", "pty": pty, "cols": big[0], "rows": big[1]})
    yield from wait(lambda: vte_grid() == big)
    check("the second's larger grid pins the tab too", vte_grid() == big, (vte_grid(), big))
    pane = frame.pinbox.get_width(), frame.pinbox.get_height()
    cell = first.get_char_width(), first.get_char_height()
    check(
        "…though the tab's pane is smaller than that grid",
        pane[0] < big[0] * cell[0] and pane[1] < big[1] * cell[1],
        (pane, big, cell),
    )
    yield 400
    check("…and still nothing is sent", len(resizes) == sent_before, resizes[sent_before:])

    # -- Use this size --------------------------------------------------------------
    frame.banner.emit("button-clicked")
    yield from wait(lambda: view.active)
    check("Use this size takes the size back", view.active)
    yield from wait(lambda: not frame.banner.get_revealed() and not frame.pinbox.pinned)
    check("…the banner goes and the box lets go", not frame.banner.get_revealed() and not frame.pinbox.pinned)
    yield from wait(lambda: service_grid() == vte_grid() and vte_grid() != big)
    check(
        "…and the tab's own grid is the service's again",
        service_grid() == vte_grid() and vte_grid() != big,
        (service_grid(), vte_grid()),
    )
    check("…sent as a resize", len(resizes) > sent_before, resizes[sent_before:])
    yield from wait(lambda: (second_role() or {}).get("active") is False)
    check(
        "the second is told it is pinned, at the tab's grid",
        (second_role() or {}).get("active") is False
        and ((second_role() or {}).get("cols"), (second_role() or {}).get("rows")) == vte_grid(),
        second_role(),
    )

    # -- a key and a click take it too -------------------------------------------------
    second.send_input(pty, b"x")
    yield from wait(lambda: not view.active)
    check("the second typing takes the size from the tab", not view.active and frame.banner.get_revealed())
    yield from wait(lambda: not view.guarded, 20)
    first.feed_child(b"y")  # what a key press commits
    yield from wait(lambda: view.active)
    check("a key typed into the pinned tab takes it back", view.active and not frame.banner.get_revealed())
    second.send_input(pty, b"z")
    yield from wait(lambda: not view.active)
    check("…the second again", not view.active)
    view.click.emit("pressed", 1, 10.0, 10.0)
    yield from wait(lambda: view.active)
    check("a click on the pinned tab takes it back", view.active and not frame.banner.get_revealed())
    yield from wait(lambda: service_grid() == vte_grid())
    check("…at the tab's grid", service_grid() == vte_grid(), (service_grid(), vte_grid()))

    second.request({"t": "detach", "pty": pty})
    second.close()
    second_link.shutdown()
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
shutil.rmtree(E2E, ignore_errors=True)
sys.exit(1 if FAILED else 0)
