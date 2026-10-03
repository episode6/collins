#!/usr/bin/env python3
"""Headless check that the termscreen goldens are what a real VTE shows —
run on a dev machine or by the e2e suite, never by pytest.

`collins.service.termscreen` is the terminal of record for every automated
read the service makes (split-service spec §3.3), so it has to read a pty's
output exactly as VTE does. Its fidelity is pinned two ways: the goldens in
`tests/fixtures/streams/` (what VTE 0.84 showed for every recorded and
synthetic scenario: rows, cursor, the drawn cells, the dim tail and the
grammar's reads), which `tests/test_termscreen.py` holds the model to, and
this check, which feeds the same scenarios to a real childless
`Vte.Terminal` (120x40, VTE's default colours) and holds VTE to the same
goldens. A VTE upgrade that changes behaviour shows up here; a model that
drifts shows up in the unit suite; together they say the model and VTE
agree on every scenario.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_termscreen_parity.py [--write] [--only NAME]

`--write` (re)writes the goldens from this VTE instead of comparing: for a
fixture newly recorded (see the testing skill's recording rule), or after a
VTE upgrade whose change was judged right. `--only NAME` runs the scenarios
whose name contains NAME.

Run it behind the headless wrapper, or a window opens on the user's screen.
VTE's `get_cursor_position()` counts rows from the top of its buffer, not
the screen (memory: vte-cursor-position-counts-from-buffer-top); the
screen's first row is read off VTE itself, by homing the cursor with origin
mode off.
"""

import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "fixtures", "streams"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Vte", "3.91")
import scenarios  # noqa: E402
from gi.repository import Gdk, GLib, Gtk, Vte  # noqa: E402

from collins import providers, vtehtml  # noqa: E402
from collins.service import termscreen  # noqa: E402

DEADLINE_S = 600
GLib.timeout_add_seconds(DEADLINE_S, lambda: (print("FAIL deadline", flush=True), os._exit(2)))

COLS, ROWS = scenarios.COLS, scenarios.ROWS
SENTINEL = b"\x1b[5n"
SENTINEL_ANSWER = b"\x1b[0n"
HOME_AND_SENTINEL = b"\x1b[?6l\x1b[H\x1b[5n"

CELL_FONT = re.compile(r'<font color="#([0-9A-Fa-f]{6})"')
CELL_BACK = re.compile(r"background-color:#([0-9A-Fa-f]{6})")
CELL_STYLE = re.compile(r"text-decoration-style:(\w+)")
CELL_LINE = re.compile(r"text-decoration-color:#([0-9A-Fa-f]{6})")
CELL_TAGS = (
    ("b", "<b>"),
    ("i", "<i>"),
    ("u", "<u "),
    ("s", "<strike>"),
    ("o", "text-decoration-line:overline"),
    ("k", "<blink>"),
)

write = "--write" in sys.argv
only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else ""

failures = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": {detail}"), flush=True)
    if not ok:
        failures.append(name)


window = Gtk.Window()
window.set_default_size(1600, 1000)
window.present()
commits: list[bytes] = []
context = GLib.MainContext.default()


def rgba(rgb):
    colour = Gdk.RGBA()
    colour.parse("#" + bytes(rgb).hex())
    return colour


def fresh_terminal():
    """A new childless VTE at 120x40. VTE resizes its grid to its
    allocation, and a resize resets the scroll region: under CI's Xvfb a
    terminal swapped into the window came up at its allocation. Aligned to
    the start of a larger window it keeps the grid it was given (F2), and
    the grid is waited for before anything is fed."""
    terminal = Vte.Terminal()
    terminal.set_size(COLS, ROWS)
    terminal.set_halign(Gtk.Align.START)
    terminal.set_valign(Gtk.Align.START)
    terminal.set_scrollback_lines(termscreen.SCROLLBACK_ROWS)
    terminal.set_colors(
        rgba(termscreen.FOREGROUND),
        rgba(termscreen.BACKGROUND),
        [rgba(c) for c in termscreen.PALETTE16],
    )
    terminal.connect("commit", lambda _t, text, _size: commits.append(text.encode()))
    window.set_child(terminal)
    pump_until(
        lambda: terminal.get_mapped()
        and terminal.get_char_width() > 0
        and (terminal.get_column_count(), terminal.get_row_count()) == (COLS, ROWS)
    )
    pump_until(lambda: False, timeout=0.1)
    return terminal


def pump_until(predicate, timeout=10.0):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end:
            return False
        if not context.iteration(False):
            time.sleep(0.002)
    return True


def feed_parsed(terminal, data: bytes) -> bool:
    """Feed and wait until VTE has parsed it all (the sentinel, F3)."""
    commits.clear()
    terminal.feed(data + SENTINEL)
    return pump_until(lambda: any(SENTINEL_ANSWER in c for c in commits))


def read_range(term, fmt, row0, col0, row1, col1) -> str:
    got = term.get_text_range_format(fmt, row0, col0, row1, col1)
    return (got[0] if isinstance(got, tuple) else got) or ""


def dump(term, cursor, top) -> dict:
    """What VTE shows: rows, cursor, the drawn cells as runs, the reads the
    grammar makes. `cursor` is where the stream left it and `top` the
    screen's first row, both by VTE's buffer count."""
    column, abs_row = cursor
    rows = []
    cells = []
    for y in range(ROWS):
        text = read_range(term, Vte.Format.TEXT, top + y, 0, top + y, COLS).rstrip("\n")
        rows.append(text)
        runs = []
        if not text:
            cells.append(runs)
            continue
        for x in range(COLS):
            # One cell at a time: VTE's HTML folds faint into a colour only
            # for the run a range starts on (vtehtml.py).
            char = read_range(term, Vte.Format.TEXT, top + y, x, top + y, x + 1).rstrip("\n")
            if not char:
                continue
            html = read_range(term, Vte.Format.HTML, top + y, x, top + y, x + 1)
            fg = CELL_FONT.search(html)
            bg = CELL_BACK.search(html)
            style = CELL_STYLE.search(html)
            line = CELL_LINE.search(html)
            cell = [
                x,
                char,
                fg.group(1).upper() if fg else None,
                bg.group(1).upper() if bg else None,
                "".join(flag for flag, tag in CELL_TAGS if tag in html),
                style.group(1) if style else "",
                line.group(1).upper() if line else None,
            ]
            runs.append(cell)
        cells.append(scenarios.merge_runs(runs))
    # The grammar's reads over the screen's rows (the model's `rows()`), the
    # cursor row by the screen's count.
    screen = read_range(term, Vte.Format.TEXT, top, 0, top + ROWS - 1, COLS)
    lines = providers.split_screen_rows(screen, COLS)
    line = read_range(term, Vte.Format.TEXT, abs_row, 0, abs_row, COLS).rstrip("\n")
    tail = read_range(term, Vte.Format.HTML, abs_row, column, abs_row, COLS)
    provider = providers.get_provider("claude")
    dim_tail = vtehtml.is_dim_run(tail, termscreen.FOREGROUND)
    entered = provider.entered_prompt(lines, abs_row - top, COLS)
    history = (read_range(term, Vte.Format.TEXT, 0, 0, top - 1, COLS) if top > 0 else "").rstrip("\n")
    return {
        "rows": rows,
        "cursor": [column, abs_row - top],
        "cells": cells,
        "screen_text": read_range(term, Vte.Format.TEXT, top, 0, top + ROWS - 1, COLS).rstrip("\n"),
        "history": history.split("\n") if history else [],
        "tail_is_dim": dim_tail,
        "takes_prompt": provider.takes_prompt(line, column, dim_tail),
        "entered": None if entered is None else entered.text,
    }


def observe(data: bytes) -> dict | None:
    term = fresh_terminal()
    if not feed_parsed(term, data):
        return None
    cursor = tuple(term.get_cursor_position())
    # The screen's first row, from VTE alone: with origin mode off, home is
    # the screen's first cell wherever the scroll region is.
    if not feed_parsed(term, HOME_AND_SENTINEL):
        return None
    top = term.get_cursor_position()[1]
    if (term.get_column_count(), term.get_row_count()) != (COLS, ROWS):
        return None
    return dump(term, cursor, top)


def compare(name: str, got: dict, want: dict) -> None:
    keys = ("rows", "cursor", "cells", "screen_text", "history", "tail_is_dim", "takes_prompt", "entered")
    for key in keys:
        if got.get(key) != want.get(key):
            detail = ""
            if key == "rows":
                for y, (a, b) in enumerate(zip(got["rows"], want["rows"], strict=True)):
                    if a != b:
                        detail = f"row {y}: VTE {a!r}, golden {b!r}"
                        break
            elif key == "cells":
                for y, (a, b) in enumerate(zip(got["cells"], want["cells"], strict=True)):
                    if a != b:
                        pairs = zip(a, b, strict=False)
                        diff = next((pair for pair in pairs if pair[0] != pair[1]), (a[:1], b[:1]))
                        detail = f"row {y}: VTE {diff[0]!r}, golden {diff[1]!r}"
                        break
            else:
                detail = f"VTE {got.get(key)!r}, golden {want.get(key)!r}"
            check(f"{name}: {key}", False, detail)
            return
    check(name, True)


directory = os.path.join(ROOT, "tests", "fixtures", "streams")
goldens = {} if write else scenarios.load_goldens(directory)
written: dict[str, dict] = {}
ran = 0
print(f"VTE {Vte.get_major_version()}.{Vte.get_minor_version()}.{Vte.get_micro_version()}", flush=True)
for name, data in scenarios.all_scenarios(directory):
    if only and only not in name:
        continue
    ran += 1
    got = observe(data)
    if got is None:
        check(name, False, "VTE never answered the sentinel, or the grid is wrong")
        continue
    if write:
        written.setdefault(scenarios.golden_path(name, directory), {})[name] = got
        print(f"read {name}", flush=True)
        continue
    if name not in goldens:
        check(name, False, "no golden; run with --write")
        continue
    compare(name, got, goldens[name])

if write:
    for path, entries in written.items():
        existing = {}
        if os.path.exists(path) and only:
            with open(path) as f:
                existing = json.load(f)
        existing.update(entries)
        with open(path, "w") as f:
            json.dump(existing, f, ensure_ascii=False, indent=0, sort_keys=True)
            f.write("\n")
        print(f"wrote {len(entries)} goldens to {os.path.relpath(path, ROOT)}")
check("at least one scenario ran", ran > 0)
verdict = "FAIL" if failures else "PASS"
print(f"{verdict} termscreen parity: {ran} scenarios, {len(failures)} failures", flush=True)
os._exit(1 if failures else 0)
