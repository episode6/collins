#!/usr/bin/env python3
"""End-to-end check for the composer's paste-back and the CLI's stand-ins.

Closing the floating composer types its draft back into the agent's input
box, and the CLI folds a paste of more than two line breaks (or more than
800 characters) into a "[Pasted text #N +M lines]" stand-in. This check
drives a real TerminalTab against a `claude` stub that draws the CLI's box
and folds pastes by that rule, and verifies the three things
tests/test_composerkeys.py can only see the pure halves of:

* a close pastes a long draft back in pieces the stub shows in full, and
  the next open cuts every line of it back into the composer;
* a paste-back the stub folds anyway is read back — the verify read
  records which stand-in holds which piece, and the next open seeds the
  composer with the piece in the stand-in's place;
* a stand-in Collins didn't make keeps the composer from opening — refused
  at the open, and lowered again when one slips in under the open-cut.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_composer_paste_back.py

The stub is a small line editor: it keeps the box's text, folds bracketed
pastes as the CLI does (verified against Claude Code 2.1.247), deletes a
stand-in whole on the backspace that reaches it, and redraws the box the
way `Provider.entered_prompt` reads it — a mark row, two-space continuation
rows padded to the width (a typed break leaves its row's trailing cells
written), a rule underneath.
"""

import os
import shutil
import signal
import sys
import tempfile
import threading
import time

E2E = tempfile.mkdtemp(prefix="collins-pasteback-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"

TRUSTED = f"{E2E}/dev/alpha"
SHIM = f"{E2E}/bin/claude"

for path in (f"{E2E}/projects", f"{E2E}/chats", f"{E2E}/bin", TRUSTED):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")

# The prompt mark is ❯ + U+00A0, exactly as the CLI draws it (takes_prompt
# keys on the no-break space); copied from check_new_chat.py's shim.
_SHIM = r'''#!/usr/bin/env python3
import os, re, sys, tty
tty.setraw(0)
PS, PE = b"\x1b[200~", b"\x1b[201~"
STAND_IN = re.compile(r"\[Pasted text #\d+(?: \+\d+ lines)?\]$")
text, pastes = "", 0

def draw():
    cols = os.get_terminal_size(1).columns
    rule = "─" * cols
    rows = text.split("\n")
    out = "\x1b[H\x1b[2J" + rule + "\r\n"
    for i, row in enumerate(rows):
        line = ("❯ " if i == 0 else "  ") + row
        if i < len(rows) - 1:
            line = line.ljust(cols)  # a typed break leaves its row's tail written
        out += line + ("\r\n" if i < len(rows) - 1 else "")
    out += "\x1b7\r\n" + rule + "\x1b8"  # cursor back to the end of the text
    sys.stdout.write(out)
    sys.stdout.flush()

def paste(body):
    global text, pastes
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    lines = body.count("\n")
    if len(body) > 800 or lines > 2:
        pastes += 1
        text += "[Pasted text #%d%s]" % (pastes, " +%d lines" % lines if lines else "")
    else:
        text += body

draw()
buf = b""
while True:
    chunk = os.read(0, 4096)
    if not chunk:
        break
    buf += chunk
    while buf:
        if buf.startswith(PS):
            end = buf.find(PE)
            if end < 0:
                break  # the rest of the paste is still coming
            paste(buf[len(PS):end].decode("utf-8", "replace"))
            buf = buf[end + len(PE):]
        elif buf.startswith(b"\x1b[B"):
            buf = buf[3:]  # Down: the cursor is always at the end here
        elif buf[:1] == b"\x1b":
            buf = buf[1:]
        elif buf[:1] == b"\x7f":
            m = STAND_IN.search(text)
            text = text[: m.start()] if m else text[:-1]
            buf = buf[1:]
        elif buf[:1] in (b"\x05", b"\r"):
            buf = buf[1:]  # Ctrl+E is a no-op at the end; nothing here submits
        else:
            text += buf[:1].decode("utf-8", "replace") if buf[0] < 0x80 else ""
            buf = buf[1:]
    draw()
'''
with open(SHIM, "w", encoding="utf-8") as fh:
    fh.write(_SHIM)
os.chmod(SHIM, 0o755)
os.environ["PATH"] = f"{E2E}/bin:{os.environ['PATH']}"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import GLib  # noqa: E402

from collins import composerkeys, i18n, trust  # noqa: E402
from collins.app import App  # noqa: E402
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
        print(f"FAIL  {label}  {detail!r}")


def watchdog() -> None:
    time.sleep(120)
    print("timed out", file=sys.stderr)
    os._exit(3)


threading.Thread(target=watchdog, daemon=True).start()

i18n.init(AppState().get_setting("language"))
trust.trust_dir(TRUSTED)
app = App()
state: dict = {}

PASTE = "\x1b[200~", "\x1b[201~"
DRAFT = "\n".join(f"line {i:02d}: " + "word " * 6 for i in range(12))  # 12 lines, ~500 chars


def box() -> str | None:
    prompt = state["tab"].entered_prompt()
    return None if prompt is None else prompt.text


def read_back(text: str) -> str:
    """What a screen read makes of *text*: the spaces a broken row ends in
    are gone (only the last row keeps the ones really typed)."""
    lines = text.split("\n")
    return "\n".join([*(line.rstrip(" ") for line in lines[:-1]), lines[-1]])


def redraw():
    """feed_message paints over the box in VTE; a typed-and-deleted space
    has the stub draw it again."""
    state["tab"].feed_child_text(" ")
    yield 150
    state["tab"].feed_child_text("\x7f")
    yield 300


def clear_box():
    tab = state["tab"]
    for _ in range(4):
        prompt = tab.entered_prompt()
        if prompt is None:
            break
        tab.feed_child_text(tab.provider.clear_prompt_keys(prompt))
        yield 400


def steps():
    win = app.get_active_window()
    while win is None:
        yield 100
        win = app.get_active_window()
    state["tab"] = tab = win.start_background_session(TRUSTED)
    for _ in range(100):
        if tab.takes_prompt():
            break
        yield 200
    check("the stub is up at an empty box", tab.takes_prompt())

    # 1. A long draft goes back in full and comes back in full.
    tab.open_composer()
    yield 1200
    check("the composer opens over the stub", tab.composer_open())
    tab._composer.set_text(DRAFT)
    tab.close_composer()
    yield 1200
    check("closing puts the draft in the box, unfolded", box() == read_back(DRAFT), box())
    check(
        "…and records nothing as folded",
        tab._pasted_back == {} and tab._paste_back_pending is None,
        (tab._pasted_back, tab._paste_back_pending),
    )
    tab.open_composer()
    yield 2000
    got = tab._composer.peek_text()
    check("reopening cuts every line back into the composer", got == read_back(DRAFT), got)
    check("…and empties the box", tab.takes_prompt(), box())
    tab._composer.set_text("")
    tab.close_composer()
    yield 600

    # 2. A paste-back the CLI folds anyway: force whole-draft pieces.
    limits = composerkeys._PIECE_NEWLINES, composerkeys._PIECE_CHARS
    composerkeys._PIECE_NEWLINES = composerkeys._PIECE_CHARS = 10_000
    tab.open_composer()
    yield 1200
    tab._composer.set_text(DRAFT)
    tab.close_composer()
    yield 1200
    composerkeys._PIECE_NEWLINES, composerkeys._PIECE_CHARS = limits
    check("a whole-draft paste folds into a stand-in", box() == "[Pasted text #1 +11 lines]", box())
    check(
        "…which the verify read records against the draft",
        tab._pasted_back == {"[Pasted text #1 +11 lines]": DRAFT},
        tab._pasted_back,
    )
    tab.feed_child_text(" and more")
    yield 400
    tab.open_composer()
    yield 2000
    got = tab._composer.peek_text()
    check("reopening puts the draft back in the stand-in's place", got == DRAFT + " and more", got)
    check("…and empties the box", tab.takes_prompt(), box())
    check("…and spends the record", tab._pasted_back == {}, tab._pasted_back)
    tab._composer.set_text("")
    tab.close_composer()
    yield 600

    # 3. A paste of the user's own is refused at the open.
    tab.feed_child_text(PASTE[0] + "their\nown\nfour\nlines" + PASTE[1])
    yield 800
    check("a foreign paste folds into a stand-in", box() == "[Pasted text #2 +3 lines]", box())
    tab.open_composer()
    yield 1200
    check("the composer refuses to open over it", not tab.composer_open() and not tab._composer_opening)
    yield from redraw()
    check("…and the box keeps it", box() == "[Pasted text #2 +3 lines]", box())
    yield from clear_box()
    check("the box is cleared for the next round", tab.takes_prompt(), box())

    # 4. One that slips in under the open-cut lowers the composer again.
    tab._stash_draft("")
    tab.open_composer()
    tab.feed_child_text(PASTE[0] + "late\nfour\nline\npaste" + PASTE[1])
    yield 2000
    check("a stand-in arriving under the cut lowers the composer", not tab.composer_open())
    yield from redraw()
    check("…and leaves it in the box", box() == "[Pasted text #3 +3 lines]", box())

    try:
        os.killpg(os.getpgid(tab._child_pid), signal.SIGKILL)
    except OSError:
        pass
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
shutil.rmtree(E2E, ignore_errors=True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED or not PASSED else 0)
