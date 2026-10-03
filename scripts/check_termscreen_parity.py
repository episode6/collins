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

Besides the goldens: every scenario's `snapshot()` (the redraw) is fed to
a fresh VTE and must show what the original bytes showed (the named
exceptions in `SNAPSHOT_EXCEPTIONS`, and the alternate screen's own
scrolled-off rows, which the model does not keep); `Screen.sgr()` is
compared with VTE's
DECRQSS answer for a few pens; and tab stops after a resize from a
multiple of eight columns are compared with VTE's.

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
from collins.service import termscreen, termstream  # noqa: E402

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
    # One read per history row: a read of a range joins the rows that
    # wrapped, and a blank row is a row the buffer holds all the same.
    history = [read_range(term, Vte.Format.TEXT, y, 0, y, COLS).rstrip("\n") for y in range(top)]
    return {
        "rows": rows,
        "cursor": [column, abs_row - top],
        "cells": cells,
        "screen_text": read_range(term, Vte.Format.TEXT, top, 0, top + ROWS - 1, COLS).rstrip("\n"),
        "history": history,
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


def model_of(data: bytes) -> termscreen.Screen:
    screen = termscreen.Screen(COLS, ROWS)
    tokenizer = termstream.Tokenizer()
    screen.feed(tokenizer.feed(data))
    screen.feed(tokenizer.flush())
    return screen


# What a redraw cannot carry, by measurement (F14), by scenario name: a tab
# cell that no longer ends on a tab stop comes back as spaces (VTE's text
# read then shows spaces where the golden shows a tab), and a soft-wrap
# flag on a row that holds nothing cannot be re-made by writing (the
# screen's one text read joins one row fewer). The only allowed
# differences between a snapshot fed to VTE and the original; everything
# else (rows, cursor, cells, history, the reads) must match.
BLANKS = re.compile(r"[ \t]+")
SNAPSHOT_EXCEPTIONS = {
    "tabs-overwrite": "a tab that lost its stop is spaces after a redraw (F14)",
    "pending-su": "a wrap flag on an empty row is not re-made (F14)",
    "pending-sd": "a wrap flag on an empty row is not re-made (F14)",
}

# DECRQSS SGR, for a few pens: `Screen.sgr()` against VTE's answer.
DECRQSS_PENS = (
    b"0", b"1;31", b"2", b"1;2", b"4", b"4:3", b"21", b"7", b"9", b"53", b"38;5;100",
    b"38;2;10;20;30", b"48;2;1;2;3", b"58;5;3;4", b"58:2::7:8:9;4", b"91;104", b"3;5;8",
    b"38;5;99999", b"38;2;300;1;1",
)  # fmt: skip


def vte_decrqss(term, sgr: bytes) -> bytes:
    commits.clear()
    term.feed(b"\x1b[" + sgr + b"m" + b"\x1bP$qm\x1b\\" + SENTINEL)
    pump_until(lambda: any(SENTINEL_ANSWER in c for c in commits))
    return b"".join(commits).replace(SENTINEL_ANSWER, b"")


def grown_terminal(before: bytes, after: bytes):
    """A 16x4 VTE fed `before`, grown to 40x4, fed `after`."""
    term = Vte.Terminal()
    term.set_size(16, 4)
    term.set_halign(Gtk.Align.START)
    term.set_valign(Gtk.Align.START)
    term.connect("commit", lambda _t, text, _size: commits.append(text.encode()))
    window.set_child(term)
    pump_until(lambda: term.get_mapped() and (term.get_column_count(), term.get_row_count()) == (16, 4))
    feed_parsed(term, before)
    term.set_size(40, 4)
    pump_until(lambda: (term.get_column_count(), term.get_row_count()) == (40, 4))
    pump_until(lambda: False, timeout=0.2)
    feed_parsed(term, after)
    return term


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
    elif name not in goldens:
        check(name, False, "no golden; run with --write")
        continue
    else:
        compare(name, got, goldens[name])
    # The redraw: the model's snapshot fed to a fresh VTE shows what the
    # original bytes showed (the goldens, or what was just read).
    again = observe(model_of(data).snapshot())
    if again is None:
        check(f"{name}: snapshot", False, "VTE never answered the sentinel")
    elif name in SNAPSHOT_EXCEPTIONS:
        # A run of tabs and spaces reads as one space on both sides (only
        # the tab that lost its stop became spaces, and a tab cell is one
        # character for several cells), and the soft wraps are not compared.
        lenient, loose = dict(got), dict(again)
        for side in (lenient, loose):
            side["rows"] = [BLANKS.sub(" ", row) for row in side["rows"]]
            side["cells"] = [[c for c in row if c[1].strip()] for row in side["cells"]]
        lenient["screen_text"] = again["screen_text"]
        compare(f"{name}: snapshot (allowed: {SNAPSHOT_EXCEPTIONS[name]})", loose, lenient)
    else:
        expected = got
        if model_of(data).on_alt:
            # The alternate screen's own scrolled-off rows are VTE's
            # buffer's, not the model's: a redraw does not carry them and
            # nothing in the app reads them.
            expected = dict(got, history=again["history"])
        compare(f"{name}: snapshot", again, expected)

if not only:
    term = fresh_terminal()
    for sgr in DECRQSS_PENS:
        feed_parsed(term, b"\x1bc")
        want = vte_decrqss(term, sgr)
        mine = b"\x1bP1$r" + model_of(b"\x1b[" + sgr + b"m").sgr().encode() + b"m\x1b\\"
        check(f"DECRQSS SGR {sgr.decode()}", mine == want, f"model {mine!r}, VTE {want!r}")
    before, after = scenarios.tab_grow_scenario()
    term = grown_terminal(before, after)
    vte_cursor = tuple(term.get_cursor_position())
    vte_row = read_range(term, Vte.Format.TEXT, 1, 0, 1, 40).rstrip("\n")
    screen = termscreen.Screen(16, 4)
    tokenizer = termstream.Tokenizer()
    screen.feed(tokenizer.feed(before))
    screen.resize(40, 4)
    screen.feed(tokenizer.feed(after))
    check(
        "tab stops after growing 16 to 40 columns",
        screen.cursor() == vte_cursor and screen.rows()[1] == vte_row,
        f"model {screen.cursor()} {screen.rows()[1]!r}, VTE {vte_cursor} {vte_row!r}",
    )

if write:
    for path, entries in written.items():
        existing = {}
        if os.path.exists(path) and only:
            with open(path) as f:
                existing = json.load(f)
        existing.update(entries)
        scenarios.dump_goldens(path, existing)
        print(f"wrote {len(entries)} goldens to {os.path.relpath(path, ROOT)}")
check("at least one scenario ran", ran > 0)
verdict = "FAIL" if failures else "PASS"
print(f"{verdict} termscreen parity: {ran} scenarios, {len(failures)} failures", flush=True)
os._exit(1 if failures else 0)
