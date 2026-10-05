#!/usr/bin/env python3
"""End-to-end check for attaching a fresh terminal to a running pty.

The split's pty server (spec §3.4) feeds a
childless VTE from the service's pty server, and a terminal that attaches
later is painted from the service's screen model, not from any bytes the
first terminal saw. This check drives a real TerminalTab on that backend
against a `claude` stub that prints coloured output past the screen's
height and then draws the CLI's input box, and verifies what the unit
suite cannot (a real VTE on each side):

* a second client attaching to the same pty gets a redraw that reproduces
  the first VTE's screen *and scrollback* as text, and its colours (the
  HTML read of a coloured row is the same on both);
* the redraw guard holds: whatever the fresh VTE says while it is being
  repainted (its answer to the sentinel) never reaches the pty;
* both terminals then follow live output, and the first one detaching
  changes nothing for the second (§3.1 rule 5).

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_attach_redraw.py

The check is about the tab's client terminal on the service's pty. With
COLLINS_ATTACH_RECORD=<file> it also writes
every live output byte the first client was fed to that file, so a
difference between the model and VTE can be replayed through both
(scripts/check_termscreen_parity.py's harness) outside the app.
"""

import os
import shutil
import signal
import sys
import tempfile
import threading
import time

E2E = tempfile.mkdtemp(prefix="collins-attach-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

os.environ["COLLINS_DEBUG_API"] = "1"  # the e2e probe (debug.*): served only with this set
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
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
    fh.write('{"settings": {"welcome_seen": true}}')

# The stub: the CLI's box (❯ + U+00A0, what takes_prompt keys on) at once;
# on the first key typed, 60 numbered lines, every fifth one red and every
# seventh bold green (colour that has to survive the redraw, in the
# scrollback and on the screen), then the box again; from then on it echoes
# what it is typed into the box, as a line editor would. The lines wait for
# a key so that they are written at the grid the tab settles on: what the
# shell echoed before the tab was first allocated was written at another
# width, and the model keeps it as it was (no reflow, D19) where VTE
# re-wraps it, which §3.3 names as the one place the two differ.
_SHIM = r"""#!/usr/bin/env python3
import os, sys, tty
tty.setraw(0)
text = ""
def draw():
    sys.stdout.write("\r\x1b[K❯ " + text)
    sys.stdout.flush()
def lines():
    out = []
    for i in range(1, 61):
        if i % 5 == 0:
            out.append("\x1b[31mline %02d is red\x1b[0m\r\n" % i)
        elif i % 7 == 0:
            out.append("\x1b[1;32mline %02d is bold green\x1b[0m\r\n" % i)
        else:
            out.append("line %02d\r\n" % i)
    sys.stdout.write("\r\x1b[K" + "".join(out))
draw()
printed = False
while True:
    chunk = os.read(0, 4096)
    if not chunk:
        break
    if not printed:
        printed = True
        lines()
        draw()
        continue
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
from gi.repository import GLib, Gtk, Vte  # noqa: E402

from collins import apilink, i18n, ptyclient, trust  # noqa: E402
from collins import terminal as terminal_mod  # noqa: E402
from collins.api import server as api_server  # noqa: E402
from collins.api.client import SocketLink  # noqa: E402
from collins.app import App  # noqa: E402
from collins.state import AppState  # noqa: E402

PASSED = 0
FAILED = 0


def _range_text(terminal, fmt, start_row, start_col, end_row, end_col):
    """One `get_text_range_format` read, unwrapped from the tuple some VTE
    bindings return it in; "" for nothing."""
    text = terminal.get_text_range_format(fmt, start_row, start_col, end_row, end_col)
    if isinstance(text, tuple):
        text = text[0]
    return text or ""


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  ok  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}  {detail!r}")


def watchdog() -> None:
    time.sleep(120)
    print("timed out", file=sys.stderr)
    os._exit(3)


threading.Thread(target=watchdog, daemon=True).start()

i18n.init(AppState().get_setting("language"))
trust.trust_dir(TRUSTED)
# The service is its own process (PR-1.12b): started here, with this
# check's environment, before the app connects to it.
e2e_service.start_service()
app = App()
state: dict = {}

RECORD = os.environ.get("COLLINS_ATTACH_RECORD")
if RECORD:
    _real_on_output = ptyclient.ClientTerminal.on_output

    def _recording_on_output(self, pty, data, flags):
        if flags == 0 and self is state.get("first_view"):
            with open(RECORD, "ab") as rec:
                rec.write(data)
        return _real_on_output(self, pty, data, flags)

    ptyclient.ClientTerminal.on_output = _recording_on_output


def row_html(terminal: Vte.Terminal, text: str) -> str:
    """The HTML VTE draws the row holding *text* with, "" when no row does.
    Rows are read one at a time by VTE's own row numbers (the capture's
    lines do not map onto them one to one)."""
    columns = terminal.get_column_count()
    _, cursor_row = terminal.get_cursor_position()
    for index in range(cursor_row + 1):
        row = _range_text(terminal, Vte.Format.TEXT, index, 0, index, columns)
        if text in row:
            return _range_text(terminal, Vte.Format.HTML, index, 0, index, columns)
    return ""


def lines(text: str) -> list[str]:
    """A capture as comparable lines: trailing blanks and empty tail rows
    dropped, the way the model's capture gives them."""
    out = [line.rstrip(" ") for line in text.split("\n")]
    while out and "line 01" not in out[0]:
        out.pop(0)  # what came before was written at another grid
    while out and not out[-1]:
        out.pop()
    return out


def first_difference(a: list[str], b: list[str]) -> object:
    for index, (x, y) in enumerate(zip(a, b, strict=False)):
        if x != y:
            return (index, x, y)
    return (len(a), len(b))


def steps():
    win = app.get_active_window()
    while win is None:
        yield 100
        win = app.get_active_window()
    state["tab"] = tab = win.start_background_session(TRUSTED)
    state["first_view"] = tab._view
    check("the tab runs on the service's pty", tab._view is not None and tab._client is not None)
    for _ in range(100):
        if tab.takes_prompt():
            break
        yield 200
    check("the stub is up at an empty box", tab.takes_prompt())
    # On screen, so the tab is allocated and its grid settles before the
    # stub writes the lines under test (see the stub's comment).
    win.tab_view.set_selected_page(win.tab_view.get_page(tab))
    first = tab.terminal
    for _ in range(30):
        grid = (first.get_column_count(), first.get_row_count())
        if first.get_width() > 0 and tab._view._last_grid == grid:
            break
        yield 100
    yield 300
    pty = tab._view.pty
    link = apilink.current()
    check("the tab shows a pty of the app's service", pty is not None and link is not None)
    screen = tab._client.request({"t": "debug.screen", "pty": pty})
    grid = (first.get_column_count(), first.get_row_count())
    check(
        "the service's pty has the tab's grid",
        (screen["columns"], screen["row_count"]) == grid,
        ((screen["columns"], screen["row_count"]), grid),
    )
    tab.feed_child_text("g")
    for _ in range(50):
        if "line 60 is red" in terminal_mod._capture_contents(first):
            break
        yield 100
    yield 400
    first_text = lines(terminal_mod._capture_contents(first))
    check(
        "the first VTE holds the lines in its scrollback",
        any("line 01" in line for line in first_text)
        and any("line 60 is red" in line for line in first_text),
        first_text[:3],
    )
    model_text = lines(tab._client.request({"t": "debug.screen", "pty": pty})["capture"])
    check(
        "the model and the first VTE agree on the text",
        model_text == first_text,
        first_difference(model_text, first_text),
    )
    red_html = row_html(first, "line 60 is red")
    check("the first VTE draws line 60 in a colour", "color" in red_html, red_html[:120])

    # Everything the pty is written from now on, as the service sees it
    # (the probe's write spy on the service's core, D27).
    tab._client.request({"t": "debug.sandbox", "target": "core", "name": "debug_spy_writes", "args": [pty]})

    def written() -> list[bytes]:
        reply = tab._client.request(
            {"t": "debug.sandbox", "target": "core", "name": "debug_written", "args": [pty]}
        )
        return [bytes.fromhex(entry) for entry in reply["value"]]

    # A second client: a fresh VTE in its own window, pinned to the first
    # one's grid, attached to the same pty.
    events: list[dict] = []
    second = Vte.Terminal()
    second.set_scrollback_lines(10_000)
    # Pinned to the first one's grid: aligned to the start of a window big
    # enough that the allocation never resizes it (a terminal that fills
    # its window takes the window's grid, and a redraw painted for another
    # width re-wraps every long row).
    second.set_halign(Gtk.Align.START)
    second.set_valign(Gtk.Align.START)
    second.set_size(first.get_column_count(), first.get_row_count())
    window = Gtk.Window(title="attach check")
    window.set_default_size(1600, 1000)
    window.set_child(second)
    window.present()
    for _ in range(30):
        grid = (first.get_column_count(), first.get_row_count())
        if (second.get_column_count(), second.get_row_count()) == grid:
            break
        second.set_size(first.get_column_count(), first.get_row_count())
        yield 100
    grid = (first.get_column_count(), first.get_row_count())
    check(
        "the fresh VTE holds the first one's grid",
        (second.get_column_count(), second.get_row_count()) == grid,
        ((second.get_column_count(), second.get_row_count()), grid),
    )
    # A second client of the service: its own link (its own client id),
    # as another Collins on this machine would be.
    second_link = SocketLink(api_server.socket_path(os.environ["COLLINS_APP_ID"]), device="second")
    second_link.connect()
    client = second_link.pty_client(
        lambda p, d, f: state["view"].on_output(p, d, f),
        lambda e: (events.append(e), state["view"].on_event(e)),
        device="second",
    )
    state["view"] = view = ptyclient.ClientTerminal(
        second, client, on_exited=lambda status: events.append({"exit": status})
    )
    reply = view.attach(pty)
    check("the attach reply names the grid", reply.get("cols") == first.get_column_count(), reply)
    for _ in range(40):
        if not view.guarded:
            break
        yield 100
    check("the guard came down on the sentinel's answer", not view.guarded)
    check("the guard swallowed what the fresh VTE said", view.dropped_commits >= 1, view.dropped_commits)
    check(
        "nothing the fresh VTE said reached the pty", not any(b"\x1b[0n" in w for w in written()), written()
    )
    yield 300
    second_text = lines(terminal_mod._capture_contents(second))
    check(
        "the redraw reproduced the screen and the scrollback as text",
        second_text == first_text,
        first_difference(second_text, first_text),
    )
    green = "line 56 is bold green"
    check(
        "…in the same colours",
        row_html(second, "line 60 is red") == red_html and row_html(second, green) == row_html(first, green),
        (row_html(second, "line 60 is red")[:120], red_html[:120]),
    )
    check(
        "the second client is told it is not the active one",
        any(e.get("t") == "pty" and e.get("active") is False for e in events),
        events[-1:],
    )

    # Live output follows on both: the first client types, both see it.
    tab.feed_child_text("abc")
    yield 600
    check(
        "typing on the first shows on both",
        "abc" in terminal_mod._capture_contents(first) and "abc" in terminal_mod._capture_contents(second),
    )
    # The second types too (it becomes the active client), and the first
    # client detaching changes nothing for it.
    client.send_input(pty, b"d")
    yield 600
    check("the second's typing reaches the pty", any(w == b"d" for w in written()), written()[-3:])
    check(
        "…and shows on both",
        "abcd" in terminal_mod._capture_contents(second) and "abcd" in terminal_mod._capture_contents(first),
    )
    tab._view.detach()
    yield 200
    client.send_input(pty, b"e")
    yield 600
    check(
        "the first detaching leaves the second live",
        "abcde" in terminal_mod._capture_contents(second),
        terminal_mod._capture_contents(second)[-60:],
    )
    child_pid = client.request({"t": "debug.pty", "pty": pty})["child_pid"]
    check("…and the pty running", child_pid is not None)

    try:
        os.killpg(os.getpgid(child_pid), signal.SIGKILL)
    except (OSError, TypeError):
        pass
    for _ in range(30):
        if any("exit" in e for e in events):
            break
        yield 100
    check("the second hears the exit", any("exit" in e for e in events), events[-1:])
    client.close()
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
