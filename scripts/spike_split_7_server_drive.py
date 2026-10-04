#!/usr/bin/env python3
"""PR-1.7's by-hand acceptance, scripted: a real `claude` in a real tab on
the server backend, driven with real input, captured as PNGs.

One session per CLI mode (`--mode classic` sets CLAUDE_CODE_NO_FLICKER=0,
`--mode fullscreen` sets it to 1), in an isolated $HOME holding a copy of
the login (scripts/spike_split_common.make_home: never the real one). In
each: a click into the terminal and one typed character (the first key
after focus, F11's loose end), typing, a paste, the composer's cut, a
Shift+drag selection, a wheel scroll, **one real turn** on the cheapest
model, and the link match under the pointer at the URL that turn prints
(the launcher itself is not fired: a browser on a headless shell is not a
thing to want). Every step is asserted the way the e2e checks assert, and
the window is rendered to a PNG after each (the capture-screenshots skill's
in-process render), into `--out`.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/spike_split_7_server_drive.py --mode classic --out /tmp/drive

Input goes through org.gnome.Mutter.RemoteDesktop on the headless shell's
own bus and only that bus. Costs one real turn per run; the CLI is ended
with Ctrl+C Ctrl+C before the home is removed. Exit 0 when every step
passed.
"""

import argparse
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))

import spike_split_common as common  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--mode", choices=("classic", "fullscreen"), required=True)
parser.add_argument("--out", required=True, help="directory the PNGs go to")
parser.add_argument("--model", default="claude-haiku-4-5-20251001", help="the cheapest model")
parser.add_argument("--keep-home", action="store_true")
parser.add_argument("--no-turn", action="store_true", help="type the prompt but never send it (no quota)")
ARGS = parser.parse_args()
os.makedirs(ARGS.out, exist_ok=True)

if not common.have_cli():
    print("no claude or no login; nothing to drive", file=sys.stderr)
    sys.exit(77)

HOME = common.make_home()
WORK = common.workdir(HOME)
SCRATCH = tempfile.mkdtemp(prefix="collins-drive-")
RUN = "r" + "".join(c for c in os.path.basename(SCRATCH) if c.isalnum())

# The app's own environment is what the service spawns the shell with: the
# isolated home, the CLI mode, no user MCP servers (the copy has none).
os.environ["HOME"] = HOME
os.environ["CLAUDE_CODE_NO_FLICKER"] = "1" if ARGS.mode == "fullscreen" else "0"
for key in list(os.environ):
    if key.startswith(common.SCRUBBED_PREFIXES):
        del os.environ[key]
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.Drive.{RUN}"
os.environ["COLLINS_CHATS_DIR"] = f"{SCRATCH}/chats"
os.environ["COLLINS_PTY_STATE_DIR"] = f"{SCRATCH}/pty"
os.environ["XDG_CONFIG_HOME"] = f"{SCRATCH}/config"
os.environ["XDG_STATE_HOME"] = f"{SCRATCH}/state"
os.environ["XDG_CACHE_HOME"] = f"{SCRATCH}/cache"
os.makedirs(f"{SCRATCH}/chats", exist_ok=True)
os.makedirs(f"{SCRATCH}/config/collins", exist_ok=True)
with open(f"{SCRATCH}/config/collins/state.json", "w", encoding="utf-8") as fh:
    fh.write(
        '{"settings": {"welcome_seen": true, "gh_welcome_dismissed": true, '
        '"composer_on_typing": false}}'
    )

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
import spike_split_1_end_to_end as spike  # noqa: E402
from gi.repository import Gio, GLib, Graphene, Gtk, Vte  # noqa: E402

from collins import i18n, trust  # noqa: E402
from collins import terminal as terminal_mod  # noqa: E402
from collins.app import App  # noqa: E402
from collins.providers import SessionOptions  # noqa: E402
from collins.state import AppState  # noqa: E402

PASSED = 0
FAILED = 0
SHOTS = []


def _range_text(terminal, fmt, start_row, start_col, end_row, end_col):
    """One `get_text_range_format` read, unwrapped from the tuple some VTE
    bindings return it in; "" for nothing."""
    text = terminal.get_text_range_format(fmt, start_row, start_col, end_row, end_col)
    if isinstance(text, tuple):
        text = text[0]
    return text or ""


def check(label, ok, detail=""):
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  ok  {label}", flush=True)
    else:
        FAILED += 1
        print(f"FAIL  {label}  {detail!r}", flush=True)


def shot(win, name):
    path = os.path.join(ARGS.out, f"{ARGS.mode}-{name}.png")
    try:
        w, h = win.get_width(), win.get_height()
        paintable = Gtk.WidgetPaintable.new(win)
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, w, h)
        node = snapshot.to_node()
        renderer = win.get_native().get_renderer()
        texture = renderer.render_texture(node, Graphene.Rect().init(0, 0, w, h))
        texture.save_to_png(path)
        SHOTS.append(path)
    except Exception as exc:  # noqa: BLE001
        print(f"  (no shot {name}: {exc})")


def watchdog():
    time.sleep(420)
    print("timed out", file=sys.stderr)
    cleanup()
    os._exit(3)


def cleanup():
    if ARGS.keep_home:
        print("home kept:", HOME)
    else:
        common.remove_home(HOME)
    shutil.rmtree(SCRATCH, ignore_errors=True)


threading.Thread(target=watchdog, daemon=True).start()
i18n.init(AppState().get_setting("language"))
trust.trust_dir(WORK)
app = App()
state = {"written": []}


def until(pred, ms):
    deadline = time.monotonic() + ms / 1000
    while time.monotonic() < deadline:
        if pred():
            return True
        yield 100
    return pred()


def steps():
    win = app.get_active_window()
    while win is None:
        yield 100
        win = app.get_active_window()
    yield 800
    win.fullscreen()
    ok = yield from until(lambda: win.is_fullscreen() and win.is_active(), 8000)
    if not ok:
        # The shell did not give the window the keyboard yet: a click into
        # it does. Keys tapped before that go to no window at all (what
        # F11's harness lost: the first key after a click that activates
        # the window is delivered before the shell's focus moves).
        inp_pre = spike.Input(Gio, GLib)
        inp_pre.motion(-5000, -5000)
        yield 50
        inp_pre.motion(960, 540)
        yield 50
        inp_pre.button(True)
        yield 30
        inp_pre.button(False)
        inp_pre.stop()
        ok = yield from until(lambda: win.is_active(), 5000)
    check(f"the window is active (fullscreen {win.is_fullscreen()})", ok, win.is_active())
    yield 500
    try:
        inp = spike.Input(Gio, GLib)
    except GLib.Error as err:
        check("RemoteDesktop on the headless bus", False, err.message)
        app.quit()
        return
    def focused(widget):
        focus = win.get_focus()
        return focus is not None and (focus is widget or focus.is_ancestor(widget))

    tab = win.start_background_session(WORK, options=SessionOptions(model=ARGS.model))
    check("a tab on the service's pty server", tab is not None and tab._view is not None)
    win.tab_view.set_selected_page(win.tab_view.get_page(tab))
    term = tab.terminal
    loopback = terminal_mod.SERVICE_LOOPBACK
    real_write = loopback.core.ptys.write

    def spy(pty_id, data, sink=None):
        state["written"].append(bytes(data))
        return real_write(pty_id, data, sink=sink)

    loopback.core.ptys.write = spy
    commits = []
    term.connect("commit", lambda _t, text, size: commits.append(text[:size] if text else ""))
    keys = []
    key_spy = Gtk.EventControllerKey()
    key_spy.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    key_spy.connect("key-pressed", lambda c, keyval, code, state: keys.append(keyval) and False)
    win.add_controller(key_spy)

    ok = yield from until(tab.takes_prompt, 40000)
    check("the CLI is up at its box", ok)
    yield 800
    shot(win, "1-startup")
    check("the tab shows a pty of the service", tab._view.pty is not None)

    # A click into the terminal, then one character: the first key after
    # focus must arrive.
    def pointer_to(widget):
        inp.motion(-5000, -5000)
        yield 40
        ok, rect = widget.compute_bounds(win)
        inp.motion(rect.origin.x + rect.size.width / 2, rect.origin.y + rect.size.height / 2)
        yield 40

    # Focus elsewhere first (the sidebar's search), then a real click into
    # the terminal.
    win.get_focus().get_root().set_focus(None) if win.get_focus() else None
    yield 200
    yield from pointer_to(term)
    inp.button(True)
    yield 30
    inp.button(False)
    ok = yield from until(lambda: focused(term), 2000)
    check("the click put the keyboard in the terminal", ok, win.get_focus())
    state["written"].clear()
    commits.clear()
    keys.clear()
    before = tab._view.dropped_commits if tab._view is not None else 0
    at_key = {
        "is_active": win.is_active(),
        "focus": type(win.get_focus()).__name__,
        "term_has_focus": term.has_focus(),
    }
    inp.tap(ord("h"))
    ok = yield from until(lambda: any(b"h" in w for w in state["written"]), 2000)
    detail = {
        "at_key": at_key,
        "written": state["written"][-4:],
        "vte_commits": commits[-4:],
        "window_saw_keyvals": keys[-4:],
    }
    if tab._view is not None:
        detail["guard_dropped"] = tab._view.dropped_commits - before
        detail["guard_up"] = tab._view.guarded
    print("first key:", detail)
    check("the first keystroke after the click reached the pty", ok, detail)
    for ch in "ello":
        inp.tap(ord(ch))
        yield 40
    ok = yield from until(lambda: (p := tab.entered_prompt()) is not None and p.text.endswith("ello"), 4000)
    check("typing shows in the box", ok, tab.entered_prompt())
    # A second click and a second first key, now that the window has had
    # the keyboard once: tells a first-focus race in the harness from a
    # loss in the client.
    tab.feed_child_text(" ")
    yield 200
    yield from pointer_to(term)
    inp.button(True)
    yield 30
    inp.button(False)
    yield 150
    state["written"].clear()
    keys.clear()
    inp.tap(ord("w"))
    ok = yield from until(lambda: any(b"w" in w for w in state["written"]), 2000)
    check(
        "the first keystroke after a second click reached the pty",
        ok,
        {"written": state["written"][-4:], "window_saw_keyvals": keys[-4:]},
    )
    yield 300
    shot(win, "2-typed")

    # The composer's cut lifts the typed text out of the box.
    tab.open_composer()
    ok = yield from until(lambda: tab.composer_open() and tab._composer.peek_text().endswith("ello w"), 5000)
    check(
        "the composer cut the text out of the box", ok, tab._composer.peek_text() if tab._composer else None
    )
    check("…and the box is empty", tab.takes_prompt())
    shot(win, "3-composer")
    tab._composer.set_text("")
    tab.close_composer()
    yield 800

    # A paste (VTE's own paste path: a bracketed commit through the guard
    # and the input frames).
    term.paste_text("pasted line one\npasted line two")
    ok = yield from until(
        lambda: (p := tab.entered_prompt()) is not None and "pasted line" in p.text, 4000
    )
    check("a paste lands in the box", ok, tab.entered_prompt())
    check(
        "…as one bracketed commit",
        any(w.startswith(b"\x1b[200~") and w.endswith(b"\x1b[201~") for w in state["written"]),
    )
    shot(win, "4-pasted")
    prompt = tab.entered_prompt()
    if prompt is not None:
        tab.feed_child_text(tab.provider.clear_prompt_keys(prompt))
    ok = yield from until(tab.takes_prompt, 4000)
    check("the box is cleared again", ok)

    # A Shift+drag selects locally (nothing reaches the pty).
    yield from pointer_to(term)
    state["written"].clear()
    inp.key(spike.KEYSYM["Shift"], True)
    yield 120
    inp.button(True)
    yield 120
    for _ in range(30):  # a drag is motion while pressed, as the spike did it
        inp.motion(-term.get_char_width(), 0)
        yield 20
    yield 150
    inp.button(False)
    yield 120
    inp.key(spike.KEYSYM["Shift"], False)
    yield 400
    check("a Shift+drag selects in the VTE", term.get_has_selection())
    check("…and sends nothing to the pty", not any(b"\x1b[<" in w for w in state["written"]))
    shot(win, "5-selection")
    term.unselect_all()

    # One real turn, asking for a URL so the link match can be checked.
    state["written"].clear()
    for ch in "Reply with exactly this and nothing else: https://example.com/collins":
        inp.tap(ord(ch) if ch != " " else spike.KEYSYM["space"])
        yield 25
    ok = yield from until(
        lambda: (p := tab.entered_prompt()) is not None and "example.com" in p.text, 5000
    )
    check("the prompt is in the box", ok)
    if ARGS.no_turn:
        shot(win, "6-prompt")
        tab.feed_child_text("\x03")
        yield 300
        tab.feed_child_text("\x03")
        ok = yield from until(lambda: not tab.has_running_command(), 20000)
        check("Ctrl+C Ctrl+C ended the CLI", ok)
        tab.release_pty()
        yield 500
        app.quit()
        return
    inp.tap(spike.KEYSYM["Return"])

    def answered():
        text = terminal_mod._capture_contents(term)
        after = text.split("nothing else: https://example.com/collins", 1)[-1]
        return "https://example.com/collins" in after

    ok = yield from until(answered, 120000)
    check("the turn answered with the URL", ok)
    ok = yield from until(tab.takes_prompt, 30000)
    check("…and the CLI is back at its box", ok)
    yield 1000
    shot(win, "6-turn")

    # The link under the pointer: what a Ctrl+click would open.
    columns = term.get_column_count()
    _, cursor_row = term.get_cursor_position()
    found = None
    top = max(0, cursor_row - term.get_row_count() + 1)
    for row in range(top, cursor_row + 1):  # the screen only: the link is matched where it is drawn
        text = _range_text(term, Vte.Format.TEXT, row, 0, row, columns)
        if "https://example.com/collins" in text:
            found = (row, text.index("https://example.com/collins"))
    check("the URL is on the screen", found is not None, terminal_mod._capture_contents(term)[-300:])
    if found is not None:
        row, col = found
        x = (col + 3) * term.get_char_width()
        top = max(0, cursor_row - term.get_row_count() + 1)
        y = (row - top) * term.get_char_height() + term.get_char_height() / 2
        match, _tag = term.check_match_at(x, y)
        check("the link matches under the pointer", match == "https://example.com/collins", match)

    # The wheel: in fullscreen mode the CLI gets SGR wheel reports; in
    # classic mode VTE scrolls its own scrollback.
    yield from pointer_to(term)
    state["written"].clear()
    for _ in range(3):
        inp.wheel(1)
        yield 60
    yield 400
    if ARGS.mode == "fullscreen":
        check("wheel steps reach the CLI as SGR reports", any(b"\x1b[<6" in w for w in state["written"]))
    else:
        check("wheel steps stay local in classic mode", not any(b"\x1b[<6" in w for w in state["written"]))
    shot(win, "7-wheel")

    # Leave: Ctrl+C Ctrl+C ends the CLI, the shell is then closed with the tab.
    tab.feed_child_text("\x03")
    yield 300
    tab.feed_child_text("\x03")
    ok = yield from until(lambda: not tab.has_running_command(), 20000)
    check("Ctrl+C Ctrl+C ended the CLI", ok)
    tab.release_pty()
    yield 500
    app.quit()


generator = steps()


def tick():
    try:
        delay = next(generator)
    except StopIteration:
        return GLib.SOURCE_REMOVE
    except Exception as exc:  # noqa: BLE001
        check("the drive ran to its end", False, repr(exc))
        app.quit()
        return GLib.SOURCE_REMOVE
    GLib.timeout_add(delay, tick)
    return GLib.SOURCE_REMOVE


GLib.timeout_add(500, tick)
app.run([])
print(f"\n{PASSED} passed, {FAILED} failed")
for path in SHOTS:
    print("shot", path)
cleanup()
sys.exit(1 if FAILED else 0)
