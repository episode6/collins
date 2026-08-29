#!/usr/bin/env python3
"""End-to-end check for background session launches — run on a dev machine.

Exercises Window.start_background_session against a real App: a real window,
a real AdwTabView, a real VTE spawning a real child. None of it is reachable
from pytest (tests/conftest.py blocks the GTK stack so local runs reproduce
CI), and the two claims that matter are exactly the ones a headless unit test
can't make — that the launch never moves what the user is looking at, and
that a page which is never selected (so never allocated) still hands its
child a usable terminal size.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \
        python3 scripts/check_background_session.py

It stages its own throwaway scratch tree and app id, per the
capture-screenshots skill's isolation rules, so it neither reads nor writes
the user's real state and can run alongside other agent sessions. `claude` is
a shim that prints the terminal size its stdin reports and then sleeps, which
is how the geometry claim is checked at the child rather than at the widget.

Run it behind the headless wrapper, or a window opens on the user's screen —
and this check is precisely about selection and focus, which a stray click
would falsify.
"""

import os
import shutil
import signal
import sys
import tempfile

E2E = tempfile.mkdtemp(prefix="collins-bg-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

# Isolation first: every one of these is read at import time somewhere below.
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"

TRUSTED = f"{E2E}/dev/alpha"
UNTRUSTED = f"{E2E}/dev/stranger"
SHIM = f"{E2E}/bin/claude"

for path in (f"{E2E}/projects", f"{E2E}/chats", f"{E2E}/bin", TRUSTED, UNTRUSTED):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
# The first-launch welcome (collins.welcome) is answered already: it would
# otherwise sit over the window under test.
os.makedirs(f"{E2E}/config/collins", exist_ok=True)
with open(f"{E2E}/config/collins/state.json", "w", encoding="utf-8") as fh:
    fh.write('{"settings": {"welcome_seen": true}}')
# The child under test: report the winsize this pty gave us, then hold the
# terminal open the way a real CLI would. `stty size` prints "rows cols".
with open(SHIM, "w", encoding="utf-8") as fh:
    fh.write('#!/bin/bash\necho "CHILD_SIZE=$(stty size)"\nsleep infinity\n')
os.chmod(SHIM, 0o755)
os.environ["PATH"] = f"{E2E}/bin:{os.environ['PATH']}"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import GLib, Vte  # noqa: E402

from collins import i18n, trust  # noqa: E402
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
        print(f"FAIL  {label}  {detail}")


def screen_text(tab) -> str:
    """Everything the terminal has drawn, unmapped or not."""
    columns = tab.terminal.get_column_count()
    _, cursor_row = tab.terminal.get_cursor_position()
    screen = tab.terminal.get_text_range_format(
        Vte.Format.TEXT, 0, 0, cursor_row + tab.terminal.get_row_count(), columns
    )
    text = screen[0] if isinstance(screen, tuple) else screen
    return text or ""


i18n.init(AppState().get_setting("language"))
trust.trust_dir(TRUSTED)  # the launch refuses without this; that's checked too
app = App()
tries = 0
state: dict = {}


def stage() -> bool:
    """Wait for the window, then open the tab everything else happens
    behind."""
    global tries
    tries += 1
    win = app.get_active_window()
    if win is None:
        if tries > 40:  # ~10s
            print("timed out waiting for the window", file=sys.stderr)
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    # -- an untrusted folder is refused, not asked about -------------------
    before = win.tab_view.get_n_pages()
    check("untrusted folder returns None", win.start_background_session(UNTRUSTED) is None)
    check(
        "untrusted folder opens no tab",
        win.tab_view.get_n_pages() == before,
        win.tab_view.get_n_pages(),
    )

    # -- with nothing open, the launch falls through to the foreground -----
    first = win.start_background_session(TRUSTED)
    check("empty tab view still launches", first is not None)
    page_a = win.tab_view.get_selected_page()
    check("empty tab view selects the new page", page_a is not None and page_a.get_child() is first)
    check(
        "empty tab view shows the tab area",
        win.content_stack.get_visible_child_name() == "tabs",
        win.content_stack.get_visible_child_name(),
    )
    state["first"] = first
    state["page_a"] = page_a
    # Let that tab reach the screen before the background one goes behind it:
    # the size it is *allocated* is what the hidden terminal has to match, and
    # a terminal that hasn't been laid out yet is still carrying whatever it
    # was constructed with — which would let a launch that sets no size at all
    # look like a launch that mirrored one.
    GLib.timeout_add(2500, launch_background)
    return GLib.SOURCE_REMOVE


def launch_background() -> bool:
    """The claims about a launch that must not interrupt the tab now on
    screen."""
    win = app.get_active_window()
    first, page_a = state["first"], state["page_a"]
    check("the visible tab is allocated", first.terminal.get_mapped())
    # The size the launch is about to ask for, read before it runs so every
    # geometry claim below is against a number of its own — comparing the
    # hidden child to the hidden widget would pass just as well at VTE's
    # default, which is the failure this check exists to catch.
    wanted = win._background_terminal_size()
    check("mirrored size isn't VTE's default", wanted != (80, 24), wanted)
    second = win.start_background_session(TRUSTED)
    check("second launch returns its tab", second is not None)
    check("selection never moved", win.tab_view.get_selected_page() is page_a)
    check("both tabs are open", win.tab_view.get_n_pages() == 2, win.tab_view.get_n_pages())
    check(
        "background page is not selected",
        win.tab_view.get_selected_page().get_child() is not second,
    )
    check(
        "focus stayed out of the background tab",
        second is not None and win.get_focus() is not second.terminal,
        win.get_focus(),
    )
    check(
        "background tab is never allocated",
        second is not None and not second.terminal.get_mapped(),
    )
    check(
        "background session gets its sidebar row",
        len(win._placeholder_pages) == 2,
        list(win._placeholder_pages.values()),
    )
    check(
        "background terminal mirrors the visible one",
        second is not None
        and (second.terminal.get_column_count(), second.terminal.get_row_count()) == wanted,
        (second.terminal.get_column_count(), second.terminal.get_row_count()),
    )
    state["second"] = second
    state["wanted"] = wanted
    GLib.timeout_add(4000, verify_child)  # let the shell spawn and type the command
    return GLib.SOURCE_REMOVE


def verify_child() -> bool:
    """The claim that only a real child can settle: the size VTE handed a pty
    behind a page that was never on screen."""
    win = app.get_active_window()
    second = state["second"]
    columns, rows = state["wanted"]
    text = screen_text(second)
    check(
        "child came up at the requested size",
        f"CHILD_SIZE={rows} {columns}" in text,
        repr(text[-200:]),
    )
    check("selection still never moved", win.tab_view.get_selected_page() is state["page_a"])
    # The shim never exits on its own; take its whole process group out before
    # quitting, so no `sleep infinity` outlives the check.
    for tab in (state["first"], state["second"]):
        pid = tab._child_pid
        if pid:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except OSError:
                pass
    app.quit()
    return GLib.SOURCE_REMOVE


GLib.timeout_add(250, stage)
app.run([])
shutil.rmtree(E2E, ignore_errors=True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED or not PASSED else 0)
