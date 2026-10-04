"""termscreen: the screen model of record, against the goldens and the
rules of split-service spec §3.3 (F13, F14)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from collins import providers
from collins.service import termscreen, termstream
from collins.service.termscreen import (
    BACKGROUND,
    DEFAULT_PEN,
    FAINT,
    FOREGROUND,
    PALETTE16,
    Screen,
    drawn_colours,
)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "streams")
sys.path.insert(0, FIXTURES)
import scenarios  # noqa: E402

E = "\x1b"
COLS, ROWS = scenarios.COLS, scenarios.ROWS


def feed(data: bytes | str, size: int = 4096, cols: int = COLS, rows: int = ROWS) -> Screen:
    if isinstance(data, str):
        data = data.encode()
    screen = Screen(cols, rows)
    tokenizer = termstream.Tokenizer()
    for start in range(0, len(data), size):
        screen.feed(tokenizer.feed(data[start : start + size]))
    screen.feed(tokenizer.flush())
    return screen


def hex_rgb(rgb) -> str:
    return bytes(rgb).hex().upper()


# ----------------------------------------------------------------- goldens

GOLDENS = scenarios.load_goldens(FIXTURES)
ALL = scenarios.all_scenarios(FIXTURES)
NAMES = [name for name, _ in ALL]
STREAMS = dict(ALL)

FLAG_BITS = (
    ("b", termscreen.BOLD),
    ("i", termscreen.ITALIC),
    ("u", termscreen.UNDERLINE_MASK),
    ("s", termscreen.STRIKE),
    ("o", termscreen.OVERLINE),
    ("k", termscreen.BLINK),
)
UNDERLINES = ("", "solid", "double", "wavy", "dotted", "dashed")
# VTE keeps an underline's colour in fewer bits than it was given in
# (measured: 7;8;9 comes back as 08 0C 08, 200;100;50 within a step of 16).
UNDERLINE_COLOUR_STEP = 16


def model_cells(screen: Screen, row: int) -> list[list]:
    """The row's drawn cells in the goldens' shape: up to the last cell
    written, the second halves of wide characters left out."""
    out = []
    line = screen.cells(row)
    end = len(line)
    while end and termscreen.blank(line[end - 1]):
        end -= 1
    for x, cell in enumerate(line[:end]):
        if cell is not None and cell[1] == 0:
            continue
        if termscreen.blank(cell):
            text, pen = " ", (DEFAULT_PEN if cell is None else cell[2])
        else:
            text, _, pen = cell
        fg, bg = drawn_colours(pen)
        attrs = pen[0]
        flags = "".join(flag for flag, bit in FLAG_BITS if attrs & bit)
        style = UNDERLINES[(attrs & termscreen.UNDERLINE_MASK) >> termscreen.UNDERLINE_SHIFT]
        line_colour = None
        if attrs & termscreen.UNDERLINE_MASK and pen[3] is not None:
            ul = pen[3]
            line_colour = hex_rgb(termscreen.palette_rgb(ul) if isinstance(ul, int) else ul)
        out.append([x, text, hex_rgb(fg), hex_rgb(bg), flags, style, line_colour])
    return scenarios.merge_runs(out)


def near(a: str, b: str, step: int) -> bool:
    return all(abs(int(a[i : i + 2], 16) - int(b[i : i + 2], 16)) <= step for i in (0, 2, 4))


def cells_agree(mine: list, theirs: list) -> str:
    """Why a row's drawn cells differ from the golden's, or "". The golden
    reports the default colours as None and a shown cell's attributes; a
    blank cell's attributes are not drawn."""
    if len(mine) != len(theirs):
        return f"{len(mine)} runs against {len(theirs)}: {mine[:3]!r} / {theirs[:3]!r}"
    for a, b in zip(mine, theirs, strict=True):
        x, text, fg, bg, flags, style, line = a
        bx, btext, bfg, bbg, bflags, bstyle, bline = b
        bfg = bfg or hex_rgb(FOREGROUND)
        bbg = bbg or hex_rgb(BACKGROUND)
        if (x, text) != (bx, btext):
            return f"run at {x}: {text!r} against {btext!r} at {bx}"
        shown = bool(text.strip())
        if (shown and fg != bfg) or bg != bbg:
            return f"cell {x} {text!r}: {fg} on {bg} against {bfg} on {bbg}"
        if shown and (flags, style) != (bflags, bstyle):
            return f"cell {x} {text!r}: {flags!r} {style!r} against {bflags!r} {bstyle!r}"
        line_differs = (line is None) != (bline is None) or (
            line and not near(line, bline, UNDERLINE_COLOUR_STEP)
        )
        if shown and line_differs:
            return f"cell {x} {text!r}: underline colour {line} against {bline}"
    return ""


def history(screen: Screen) -> list[str]:
    """The scrollback as text, row by row, as the goldens hold VTE's."""
    return [termscreen._runs_text(runs) for runs in screen.scrollback]


@pytest.mark.parametrize("name", NAMES)
def test_golden(name):
    """Rows, cursor, every drawn cell, the soft wraps, the scrollback, the
    dim tail and the grammar's reads: the model says what VTE showed."""
    golden = GOLDENS.get(name)
    assert golden is not None, f"no golden for {name}: run check_termscreen_parity.py --write"
    data = STREAMS[name]
    screen = feed(data)
    assert screen.rows() == golden["rows"]
    assert list(screen.cursor()) == golden["cursor"]
    for y in range(ROWS):
        why = cells_agree(model_cells(screen, y), golden["cells"][y])
        assert not why, f"row {y}: {why}"
    assert screen.screen_text().rstrip("\n") == golden["screen_text"]
    if not screen.on_alt:
        assert history(screen) == golden["history"]
    x, y = screen.cursor()
    faint = screen.tail_is_faint(y, x)
    assert faint == golden["tail_is_dim"]
    provider = providers.get_provider("claude")
    assert provider.takes_prompt(screen.row_text(y), x, faint) == golden["takes_prompt"]
    entered = provider.entered_prompt(screen.rows(), y, COLS)
    assert (None if entered is None else entered.text) == golden["entered"]


@pytest.mark.parametrize("name", sorted(scenarios.SYNTHETIC_MARKED))
def test_snapshot_then_the_rest_matches_the_after_golden(name):
    """A redraw taken at the first mark, with the session's next bytes fed
    on top, shows what the whole stream shows (what an attach mid-session
    has to carry into the output that follows it)."""
    first, then = scenarios.SYNTHETIC_MARKED[name]
    at = feed(first)
    again = feed(at.snapshot())
    tokenizer = termstream.Tokenizer()
    again.feed(tokenizer.feed(then.encode()))
    again.feed(tokenizer.flush())
    golden = GOLDENS[name + "@after"]
    assert again.rows() == golden["rows"]
    assert list(again.cursor()) == golden["cursor"]
    for y in range(ROWS):
        why = cells_agree(model_cells(again, y), golden["cells"][y])
        assert not why, f"row {y}: {why}"
    if not again.on_alt:
        assert history(again) == golden["history"]


@pytest.mark.parametrize("name", NAMES)
def test_golden_however_the_stream_is_cut(name):
    data = STREAMS[name]
    whole = feed(data)
    for size in (1, 7):
        other = feed(data, size)
        assert other.rows() == whole.rows()
        assert other.cursor() == whole.cursor()
        for y in range(ROWS):
            assert other.cells(y) == whole.cells(y)


def test_the_goldens_cover_every_scenario():
    missing = [name for name in NAMES if name not in GOLDENS]
    assert not missing
    missing = [name for name in scenarios.RESIZE if "resize:" + name not in GOLDENS]
    assert not missing
    stale = [name for name in GOLDENS if name not in STREAMS and name[7:] not in scenarios.RESIZE]
    assert not stale, "goldens with no scenario: regenerate with --write"


@pytest.mark.parametrize("name", sorted(scenarios.RESIZE))
def test_resize_golden(name):
    """A resize between two feeds: rows, cursor and history as VTE's after
    the second feed (`Grid.used` across a shrink included)."""
    cols, rows, before, size, after = scenarios.RESIZE[name]
    screen = feed(before, cols=cols, rows=rows)
    screen.resize(*size)
    tokenizer = termstream.Tokenizer()
    screen.feed(tokenizer.feed(after))
    screen.feed(tokenizer.flush())
    golden = GOLDENS["resize:" + name]
    assert screen.rows() == golden["rows"]
    assert list(screen.cursor()) == golden["cursor"]
    assert history(screen) == golden["history"]


def test_the_recorded_fixtures_hold_no_user_path():
    for session in scenarios.recorded_sessions(FIXTURES):
        with open(os.path.join(FIXTURES, session + ".bin"), "rb") as f:
            data = f.read()
        assert b"/home/" not in data
        assert b"claude.ai/" not in data
        assert b"/collins/.claude" not in data


# --------------------------------------------------------- the snapshot


@pytest.mark.parametrize("name", NAMES)
def test_snapshot_round_trips_into_a_fresh_model(name):
    """`snapshot()` fed to a fresh model gives the same cells, cursor,
    screen and scrollback. The one thing a redraw cannot carry (F14): a
    tab cell that no longer ends on a tab stop, which comes back as
    spaces and reads the same."""
    original = feed(STREAMS[name])
    again = feed(original.snapshot())
    assert again.on_alt == original.on_alt
    assert again.cursor() == original.cursor()
    for y in range(ROWS):
        mine, theirs = original.cells(y), again.cells(y)
        if mine == theirs:
            continue
        # Only a tab lost to its stop may differ, and only by becoming spaces.
        for a, b in zip(mine, theirs, strict=True):
            if a == b:
                continue
            assert a is not None and (a[0] == "\t" or (a[0] == "" and a[1] == 0)), (y, a, b)
            assert b == (" ", 1, a[2]), (y, a, b)
    assert history(again) == history(original)
    for a, b in zip(original.scrollback, again.scrollback, strict=True):
        assert a == b


def test_snapshot_carries_scrollback_with_its_pens():
    """The user's decision of 2026-09-29: a redraw shows old output in its
    colours (F14 round-tripped scrollback as text only)."""
    screen = feed(f"{E}[31mred line\r\n{E}[0;1;44mbold on blue\r\n{E}[0m" + "x\r\n" * 50)
    assert len(screen.scrollback) == 50 + 2 - ROWS + 1
    again = feed(screen.snapshot())
    first = again.scrollback[0]
    assert first == (("red line", 8, (0, 1, None, None)),)
    second = again.scrollback[1]
    assert second == (("bold on blue", 12, (termscreen.BOLD, None, 4, None)),)


def test_snapshot_of_the_alternate_screen_keeps_the_main_screen_underneath():
    screen = feed(f"main one\r\nmain two{E}[?1049h{E}[3;3Hin the alternate")
    again = feed(screen.snapshot())
    assert again.on_alt and again.rows()[2] == "  in the alternate"
    again.feed(termstream.Tokenizer().feed(f"{E}[?1049l back".encode()))
    assert again.rows()[:2] == ["main one", "main two back"]
    assert again.cursor() == (13, 1)


def test_snapshot_rebuilds_a_pending_wrap_and_the_pen():
    screen = feed(f"{E}[1;32m" + "P" * 120)
    assert screen.cursor() == (120, 0)
    again = feed(screen.snapshot())
    assert again.cursor() == (120, 0)
    assert again.grid.pen == (termscreen.BOLD, 2, None, None)
    again.feed(termstream.Tokenizer().feed(b"Z"))
    assert again.rows()[1] == "Z" and again.cells(1)[0][2] == (termscreen.BOLD, 2, None, None)


def test_snapshot_takes_the_mode_preamble():
    """The tracker's preamble (screen=False) is written after the screens,
    before the cursor; the screen's own modes come with it either way."""
    data = f"{E}[?1049h{E}[?2004h{E}[?25l{E}[5;10r{E}[?6h{E}[1;1Htop".encode()
    filt = termstream.StreamFilter(screen=Screen(COLS, ROWS))
    filt.feed(data)
    screen = filt.screen
    snap = screen.snapshot(filt.preamble(screen=False))
    assert b"\x1b[?1049h" in snap
    assert snap.count(b"\x1b[?1049h") == 1  # the preamble leaves the switch out
    assert b"\x1b[?2004h" in snap and b"\x1b[?25l" in snap and b"\x1b[?6h" in snap
    again = feed(snap)
    assert again.on_alt and again.origin and again.rows()[4] == "top"
    assert again.cursor() == screen.cursor()


def test_snapshot_of_ten_thousand_scrollback_rows_is_bounded():
    """The redraw of a model with a full scrollback, measured in bytes and
    milliseconds (for §3.18): one full-width coloured row per line."""
    row = f"{E}[38;2;10;20;30m" + "x" * 60 + f"{E}[0m" + "y" * 60 + "\r\n"
    screen = feed(row * (termscreen.SCROLLBACK_ROWS + ROWS))
    assert len(screen.scrollback) == termscreen.SCROLLBACK_ROWS
    start = time.perf_counter()
    snap = screen.snapshot()
    elapsed = time.perf_counter() - start
    assert len(snap) < 3 * 1024 * 1024
    assert elapsed < 5.0
    again = feed(snap)
    assert len(again.scrollback) == termscreen.SCROLLBACK_ROWS
    assert again.scrollback[-1] == screen.scrollback[-1]


# -------------------------------------------------------- the VTE rules


PENDING_WRAP_TABLE = {
    # F13: what each operation does with the wrap pending: (column, row, row text)
    "el0": (120, 0, "P" * 120),
    "ed3": (120, 0, "P" * 120),
    "ht": (120, 0, "P" * 120),
    "sgr": (120, 0, "P" * 120),
    "decsc-decrc": (120, 0, "P" * 120),
    "bel": (120, 0, "P" * 120),
    "so-si": (120, 0, "P" * 120),
    "charset": (120, 0, "P" * 120),
    "hide-cursor": (120, 0, "P" * 120),
    "sync": (120, 0, "P" * 120),
    "title": (120, 0, "P" * 120),
    "alt-in-out": (120, 0, "P" * 120),
    "irm": (120, 0, "P" * 120),
    "kitty": (120, 0, "P" * 120),
    "cpr": (120, 0, "P" * 120),
    "ed0": (119, 0, "P" * 119),
    "ed1": (119, 0, ""),
    "ed2": (119, 0, ""),
    "el1": (119, 0, ""),
    "el2": (119, 0, ""),
    "ech": (119, 0, "P" * 119),
    "ich": (119, 0, "P" * 119),
    "dch": (119, 0, "P" * 119),
    "cuf": (119, 0, "P" * 120),
    "cha": (119, 0, "P" * 120),
    "bs": (118, 0, "P" * 120),
    "cub": (118, 0, "P" * 120),
    "cr": (0, 0, "P" * 120),
}


@pytest.mark.parametrize("name", sorted(PENDING_WRAP_TABLE))
def test_pending_wrap(name):
    """The pending-wrap table, each row its own case: where the operation
    leaves the cursor and what the row then reads, before the Z."""
    column, row, text = PENDING_WRAP_TABLE[name]
    screen = feed("\r\n" + "P" * 120 + scenarios.PENDING[name])
    assert screen.cursor() == (column, row + 1)
    assert screen.rows()[row + 1] == text


def test_pending_wrap_then_a_character_wraps():
    screen = feed("A" * 120 + "B")
    assert screen.rows()[:2] == ["A" * 120, "B"]
    assert screen.cursor() == (1, 1)
    assert screen.main.wrapped[0]


def test_autowrap_off_parks_the_cursor_and_overwrites_the_last_cell():
    screen = feed(f"{E}[?7l" + "N" * 125)
    assert screen.rows()[0] == "N" * 120
    assert screen.cursor() == (120, 0)
    screen.feed(termstream.Tokenizer().feed(b"X"))
    assert screen.rows()[0] == "N" * 119 + "X"
    assert screen.cursor() == (120, 0)


def test_wide_and_combining_characters():
    screen = feed("x" * 119 + "日本\r\ncafé ä́ ❤️ 👩‍👩‍👧 1️⃣|")
    assert screen.rows()[0] == "x" * 119
    assert screen.rows()[1] == "日本"
    assert screen.cells(1)[0] == ("日", 2, DEFAULT_PEN) and screen.cells(1)[1] == ("", 0, DEFAULT_PEN)
    assert screen.rows()[2] == "café ä́ ❤️ 👩‍👩‍👧 1️⃣|"
    cells = screen.cells(2)
    assert cells[3][0] == "é" and cells[5][0] == "ä́"
    assert screen.cursor() == (18, 2)


def test_wide_character_overwritten_leaves_two_spaces():
    screen = feed(f"日本語\r{E}[1CX")
    assert screen.rows()[0] == " X本語"
    screen = feed(f"日本語{E}[3D{E}[1P|")
    assert screen.rows()[0] == "日 | "  # DCH cut 本, the | then cut 語
    screen = feed(f"日本語{E}[3D{E}[1X|")
    assert screen.rows()[0] == "日 |語"


def test_scroll_region():
    screen = feed("".join(f"line {i}\r\n" for i in range(1, 12)) + f"{E}[3;8r{E}[8;1H" + "in\r\n" * 4)
    rows = screen.rows()
    assert rows[1] == "line 2" and rows[8] == "line 9"
    assert rows[2:8] == ["line 7", "inne 8", "in", "in", "in", ""]
    assert not screen.scrollback  # a region not at the top keeps rows out of scrollback
    screen.feed(termstream.Tokenizer().feed(f"{E}[3;1H{E}M{E}Mup".encode()))
    assert screen.rows()[2] == "up" and screen.rows()[4] == "line 7"


def test_alternate_screen_enter_and_leave_restores_the_main_screen():
    screen = feed(f"main text\r\nsecond{E}[?1049halt screen{E}[2;2Hmore")
    assert screen.on_alt and screen.rows()[1] == " more alt screen"  # the cursor carried over
    screen.feed(termstream.Tokenizer().feed(f"{E}[?1049lback".encode()))
    assert not screen.on_alt
    assert screen.rows()[:2] == ["main text", "secondback"]
    assert screen.cursor() == (10, 1)


def test_saved_cursor_is_per_screen():
    screen = feed(f"ab{E}[?1049h{E}[5;5H{E}7{E}[9;9Hx{E}[?1049lX{E}8Y")
    assert screen.rows()[0] == "abY"  # ESC 8 restores what ?1049h saved on the main screen


def test_ed2_moves_the_rows_the_buffer_holds_into_scrollback():
    """VTE moves the rows its buffer holds (written, erased or scrolled
    onto), blank ones included, so a second ED 2 moves a whole screen; a
    row the cursor merely sat on is not held (measured)."""
    screen = feed("".join(f"kept {i}\r\n" for i in range(1, 6)) + f"{E}[2Jafter")
    assert history(screen) == [f"kept {i}" for i in range(1, 6)]
    assert screen.rows()[5] == "after"
    assert len(feed(f"a\r\nb{E}[2J{E}[2J").scrollback) == 42
    assert len(feed(f"a{E}[20;1H{E}[2J").scrollback) == 1
    assert len(feed(f"a{E}[J{E}[2J").scrollback) == 40
    assert len(feed(f"a{E}[5;1H{E}[K{E}[2J").scrollback) == 5
    assert len(feed(f"{E}[2J").scrollback) == 1
    assert len(feed("x\n" * 45 + f"{E}[2J").scrollback) == 46


def test_ed3_blanks_the_scrollback_and_leaves_the_cursor():
    """VTE keeps the rows and blanks them (measured, pinned by the golden)."""
    screen = feed("".join(f"gone {i}\r\n" for i in range(1, 50)) + f"{E}[3Jstill")
    assert history(screen) == [""] * 10
    assert screen.rows()[-1] == "still"


def test_tab_cells():
    screen = feed("a\tb\t\tc")
    assert screen.rows()[0] == "a\tb\t\tc"
    assert screen.cells(0)[1] == ("\t", 7, DEFAULT_PEN)
    assert screen.cursor() == (25, 0)
    # An overwrite inside a tab leaves spaces before and a shorter tab after.
    screen = feed(f"a\tb\r{E}[3CX")
    assert screen.rows()[0] == "a  X\tb"
    assert screen.cells(0)[4] == ("\t", 4, DEFAULT_PEN)
    # A tab cell takes no background.
    screen = feed(f"{E}[44mi\tj")
    assert screen.cells(0)[1][2] == DEFAULT_PEN
    # With the wrap pending a tab does nothing.
    screen = feed("P" * 120 + "\tZ")
    assert screen.rows()[1] == "Z"


def test_never_written_cells_are_trimmed_and_read_as_spaces_inside():
    screen = feed(f"{E}[5Cx{E}[3C")
    assert screen.rows()[0] == "     x"
    assert screen.cells(0)[0] is None
    screen = feed(f"{E}[44m{E}[5X{E}[0m")
    assert screen.rows()[0] == ""  # erased with a background, past the last written cell
    screen = feed(f"{E}[44m{E}[5X{E}[0m{E}[8Cx")
    assert screen.rows()[0] == "        x"
    assert screen.cells(0)[0] == ("", 1, (0, None, 4, None))


def test_il_and_dl_move_the_cursor_to_column_0():
    screen = feed(f"abc{E}[5G{E}[1L")
    assert screen.cursor() == (0, 0)
    screen = feed(f"abc{E}[5G{E}[1M")
    assert screen.cursor() == (0, 0)


def test_private_prefix_never_reads_as_its_twin():
    screen = feed(f"a{E}[>5ub{E}[<uc{E}[>4;2md{E}[?4me{E}[=1;1uf{E}[>0qg{E}[?ug")
    assert screen.rows()[0] == "abcdefgg"
    assert screen.grid.pen == DEFAULT_PEN


def test_charsets_so_si_and_line_drawing():
    screen = feed(f"{E}(0lqqk{E}(B plain {E})0\x0elqqk\x0f plain")
    assert screen.rows()[0] == "┌──┐ plain ┌──┐ plain"


def test_osc_8_and_osc_99_are_ignored():
    screen = feed(f"{E}]8;;file:///x{E}\\link{E}]8;;{E}\\ {E}]99;i=1;hello{E}\\!")
    assert screen.rows()[0] == "link !"


def test_resize_truncates_and_pads_without_reflow():
    screen = feed("x" * 100 + f"{E}[3;8r\r\nshort")
    screen.resize(50, 10)
    assert screen.columns() == 50 and screen.row_count() == 10
    assert screen.rows()[0] == "x" * 50
    assert screen.rows()[1] == "short"
    assert screen.cursor() == (5, 1)
    assert (screen.top, screen.bottom) == (0, 9)  # a resize clears the region
    screen.resize(120, 40)
    assert screen.rows()[0] == "x" * 50  # no reflow: the cut stays cut
    assert len(screen.rows()) == 40
    assert screen.bottom == 39
    screen.feed(termstream.Tokenizer().feed(b"\t"))
    assert screen.cells(1)[5][1] == 3  # the stops past the old width exist again


def test_resize_grown_from_a_multiple_of_eight_has_every_stop():
    _, _, before, _, after = scenarios.RESIZE["tab-grow-16-to-40"]
    screen = feed(before, cols=16, rows=4)
    screen.resize(40, 4)
    assert sorted(screen.tabs) == [8, 16, 24, 32]
    screen.feed(termstream.Tokenizer().feed(after))
    assert screen.rows()[1] == "\tA\tB\tC\tD"
    assert screen.cursor() == (33, 1)


def test_resize_shorter_keeps_the_cursor_row_and_scrolls_the_top_away():
    screen = feed("".join(f"r{i}\r\n" for i in range(30)) + "here")
    assert screen.cursor() == (4, 30)
    screen.resize(120, 20)
    assert screen.cursor() == (4, 19)
    assert screen.rows()[19] == "here"
    assert termscreen._runs_text(screen.scrollback[0]) == "r0"
    assert len(screen.scrollback) == 11


def test_the_grid_is_clamped_to_the_protocols_bounds():
    from collins.api.protocol import MAX_COLS, MAX_ROWS

    tiny = Screen(0, 0)
    assert (tiny.columns(), tiny.row_count()) == (1, 1)
    tiny.feed(termstream.Tokenizer().feed("日本a".encode()))  # a wide character fills the one column
    assert tiny.rows() == ["a"]
    assert tiny.capture_contents() == "日本a"  # the wraps join, as VTE's capture joins them
    huge = Screen(10**6, 10**6)
    assert (huge.columns(), huge.row_count()) == (MAX_COLS, MAX_ROWS)
    huge.resize(0, 0)
    assert (huge.columns(), huge.row_count()) == (1, 1)


def test_parameters_saturate_and_loops_end():
    screen = feed(f"{E}[70000;70000Hx")
    assert screen.cursor() == (120, 39)
    screen = feed(f"{E}[" + "9" * 1000 + ";3Hy")
    assert screen.cursor() == (3, 39)
    # past termstream's SEQUENCE_MAX the sequence is Bad and draws nothing
    screen = feed(f"{E}[" + "9" * 5000 + ";3Hy")
    assert screen.rows()[0].endswith("y")
    screen = feed(f"a{E}[1000000000I")
    assert screen.cursor() == (119, 0)
    screen = feed(f"{E}[50G{E}[1000000000Z")
    assert screen.cursor() == (0, 0)
    screen = feed(f"a{E}[65535b")
    assert screen.cursor() == (120, 0) and screen.rows()[0] == "a" * 120
    screen = feed(f"{E}[38;5;99999mz{E}[38;2;300;1;1mw")
    assert screen.cells(0)[0][2] == DEFAULT_PEN and screen.cells(0)[1][2] == DEFAULT_PEN


def test_combining_marks_are_capped_as_vte_keeps_them():
    screen = feed("a" + "\u0301" * 40 + "b")
    assert screen.cells(0)[0][0] == "a" + "\u0301" * termscreen.COMBINING_MAX
    assert screen.cursor() == (2, 0)


def test_the_scroll_region_is_shared_by_both_screens():
    screen = feed(scenarios.SYNTHETIC["region-across-alt"])
    assert (screen.top, screen.bottom) == (2, 5)
    assert screen.rows()[:7] == GOLDENS["region-across-alt"]["rows"][:7]
    assert screen.rows()[2] == "L9"  # the region scrolled: L2 to L8 are gone
    screen = feed(scenarios.SYNTHETIC["region-across-alt-back"])
    assert screen.on_alt and screen.rows()[:7] == GOLDENS["region-across-alt-back"]["rows"][:7]
    snap = screen.snapshot()
    assert snap.count(b"\x1b[3;6r") == 1


def test_cursor_reports_a_pending_wrap_as_the_column_count():
    screen = feed("A" * 120)
    assert screen.cursor() == (120, 0)
    filt = termstream.StreamFilter(screen=Screen(COLS, ROWS))
    out = filt.feed(b"A" * 120 + b"\x1b[6n")
    assert out.replies == [b"\x1b[1;120R"]


def test_sgr_answers_decrqss_from_the_pen():
    filt = termstream.StreamFilter(screen=Screen(COLS, ROWS))
    assert filt.feed(b"\x1bP$qm\x1b\\").replies == [b"\x1bP1$r0m\x1b\\"]
    assert filt.feed(b"\x1b[1;31m\x1bP$qm\x1b\\").replies == [b"\x1bP1$r0;1;31m\x1b\\"]


@pytest.mark.parametrize(
    ("sgr", "body"),
    [
        ("4", "0;4"),
        ("4:2", "0;21"),
        ("4:3", "0;4:3"),
        ("38;5;100", "0;38:5:100"),
        ("38;2;10;20;30", "0;38:2::10:20:30"),
        ("58;5;3;4", "0;4;58:5:3"),
        ("58:2::7:8:9;4", "0;4"),  # a direct underline colour is left out
        ("1;2;3;4;5;7;8;9;53;31;41", "0;1;2;3;4;5;7;8;9;53;31;41"),
        ("91;104", "0;91;104"),
    ],
)
def test_decrqss_takes_vtes_form(sgr, body):
    """Measured 2026-10-02; the parity check compares the same pens with VTE."""
    assert feed(f"{E}[{sgr}m").sgr() == body


# ---------------------------------------------------------- the faint rule


@pytest.mark.parametrize(
    ("sgr", "dim"),
    [
        ("2", True),  # faint, default foreground: #808080, two thirds of #C0C0C0
        ("2;37", True),  # faint on a light palette colour: #999999 (measured dim)
        ("2;97", False),  # #AAAAAA: too close to the foreground to be a dimming
        ("90", True),  # no faint at all: #7F7F7F is the foreground scaled down
        ("37", False),
        ("2;90", False),  # #545454: scaled too far
        ("38;2;128;128;128", True),  # a direct mid grey reads dim
        ("2;38;5;250", True),
        ("2;38;5;240", False),
        ("2;38;2;180;180;180", False),  # a direct colour VTE does not dim: #B4B4B4
        ("2;31", False),  # a saturated colour is not a scaled foreground
        ("2;1", False),  # bold: its own tag splits the run
        ("2;3", False),
        ("2;4", False),
        ("2;9", False),
        ("2;5", False),  # blink
        ("2;53", False),  # overline
        ("2;8", True),  # conceal shows as plain text
        ("2;7", False),  # inverse: a background
        ("2;41", False),  # a background
        ("", False),
    ],
)
def test_tail_is_faint_answers_as_todays_html_read(sgr, dim):
    """`vtehtml.is_dim_run` over VTE's HTML of the cursor's tail, measured
    2026-10-02 for each pen (the goldens pin the same for the box
    scenarios): one run, no other tag, the drawn colour a scaled-down
    foreground."""
    screen = feed(f"❯\xa0{E}[{sgr}mTry \"fix\"{E}[0m{E}[1;3H")
    assert screen.tail_is_faint(0, 2) == dim


def test_tail_is_faint_takes_the_clients_colours():
    screen = feed(f"❯\xa0{E}[2mTry \"fix\"{E}[0m{E}[1;3H")
    assert screen.tail_is_faint(0, 2, foreground=(0xDE, 0xDD, 0xDA))  # Adwaita dark's foreground
    # faint on palette 7 draws two thirds of the client's palette entry
    screen = feed(f"❯\xa0{E}[2;37mTry \"fix\"{E}[0m{E}[1;3H")
    palette = list(PALETTE16)
    palette[7] = (0x10, 0x10, 0x10)
    assert not screen.tail_is_faint(0, 2, palette=tuple(palette))


def test_tail_is_faint_wants_text_in_the_tail():
    screen = feed(f"❯\xa0{E}[2m   {E}[0m{E}[1;3H")
    assert not screen.tail_is_faint(0, 2)
    screen = feed(f"❯\xa0{E}[1;3H")
    assert not screen.tail_is_faint(0, 2)


def test_drawn_colours_dim_the_palette_and_not_a_direct_colour():
    assert drawn_colours((FAINT, None, None, None))[0] == tuple(termscreen.dim(c) for c in FOREGROUND)
    assert drawn_colours((FAINT, 1, None, None))[0] == tuple(termscreen.dim(c) for c in PALETTE16[1])
    assert drawn_colours((FAINT, (10, 20, 30), None, None))[0] == (10, 20, 30)
    assert drawn_colours((termscreen.INVERSE, 1, 4, None)) == (PALETTE16[4], PALETTE16[1])


# ----------------------------------------------------------- the grammar


def test_snapshot_sets_the_shared_region_once_and_the_tracker_leaves_it_out():
    filt = termstream.StreamFilter(screen=Screen(COLS, ROWS))
    filt.feed(f"{E}[5;10r{E}[?2004h".encode())
    snap = filt.screen.snapshot(filt.preamble(screen=False))
    assert snap.count(b"\x1b[5;10r") == 1
    assert b"\x1b[?2004h" in snap


def test_takes_prompt_and_entered_prompt_over_the_model():
    """The reads terminal.py makes today, over the model's rows."""
    provider = providers.get_provider("claude")
    screen = feed("❯\xa0fix the tests in foo\r\n" + "─" * 120 + f"{E}[1;3H")
    x, y = screen.cursor()
    assert not provider.takes_prompt(screen.row_text(y), x, screen.tail_is_faint(y, x))
    assert provider.entered_prompt(screen.rows(), y, COLS).text == "fix the tests in foo"
    screen = feed(f"❯\xa0{E}[2mTry \"fix\"{E}[0m\r\n" + "─" * 120 + f"{E}[1;3H")
    x, y = screen.cursor()
    assert provider.takes_prompt(screen.row_text(y), x, screen.tail_is_faint(y, x))


def test_first_column_and_capture_contents():
    screen = feed("".join(f"{i}\r\n" for i in range(45)) + "$ ")
    assert screen.first_column()[-1] == "$"
    assert screen.first_column()[0] == "6"
    text = screen.capture_contents()
    assert text.startswith("0\n1\n2\n")
    assert text.endswith("\n44\n$ ")
    screen.feed(termstream.Tokenizer().feed(f"{E}[?1049h{E}[Hvim".encode()))
    assert screen.capture_contents() == "vim"  # the scrollback is out of reach on the alternate screen


def test_capture_contents_of_a_shell_fixture():
    screen = feed("$ ls\r\na.txt  b.txt\r\n$ " + "echo " + "x" * 130 + "\r\n" + "x" * 130 + "\r\n$ ")
    text = screen.capture_contents()
    assert text.split("\n")[0] == "$ ls"
    # The command wrapped at the margin and reads back as one line, the
    # way VTE's own capture joins a soft-wrapped row with the next.
    assert text.split("\n")[2] == "$ echo " + "x" * 130
    assert text.split("\n")[3] == "x" * 130
    assert text.endswith("$ ")


def test_capture_contents_joins_wrapped_rows_in_the_scrollback():
    screen = Screen(10, 3)
    screen.feed(termstream.Tokenizer().feed(("abcdefghijklmnop\r\n" * 4 + "end").encode()))
    assert screen.capture_contents() == "abcdefghijklmnop\n" * 4 + "end"


# ----------------------------------------------------------- width skew


def test_width_rule_matches_cell_width():
    from collins.dropimages import cell_width

    for text in ("abc", "日本語", "café", "👩‍👩‍👧", "1️⃣", "\t", "한국어"):
        assert sum(termscreen.char_width(c) for c in text) == cell_width(text)


def test_width_skew_is_the_full_table():
    """Every code point VTE 0.84 draws other than the rule says (473 of
    154 998, measured with the spike's `widths` mode); pinned so a change
    to either side is deliberate."""
    total = 0
    for start, end, vte in termscreen.WIDTH_SKEW:
        assert start <= end
        for code in range(start, end + 1):
            assert termscreen.char_width(chr(code)) != vte, hex(code)
            total += 1
    assert total == 473


# -------------------------------------------------------------- bounds


def test_the_service_loads_no_gi():
    code = (
        "import sys, collins.service.termscreen; "
        "print([m for m in sys.modules if m == 'gi' or m.startswith('gi.')])"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_scrollback_is_bounded():
    screen = feed("x\r\n" * 200, cols=20, rows=5)
    assert len(screen.scrollback) == 196
    small = Screen(20, 5, scrollback=10)
    small.feed(termstream.Tokenizer().feed(b"y\r\n" * 100))
    assert len(small.scrollback) == 10


def test_throughput_is_a_thousand_times_the_cli():
    """§3.18: the CLI peaks at 5 KB/s; the model must not be the bottleneck.
    A sanity bound, not a benchmark (the docstring's table is)."""
    frame = (
        f"{E}[?2026h"
        + "".join(
            f"{E}[{y};1H{E}[K{E}[38;2;177;185;249mword {E}[2mfaint{E}[0m text\n" for y in range(1, 41)
        )
        + f"{E}[?2026l"
    ).encode()
    data = frame * 60
    screen = Screen(COLS, ROWS)
    tokenizer = termstream.Tokenizer()
    start = time.perf_counter()
    for i in range(0, len(data), 65536):
        screen.feed(tokenizer.feed(data[i : i + 65536]))
    elapsed = time.perf_counter() - start
    assert len(data) / elapsed > 1_000_000  # over 1 MB/s, two hundred times the CLI


# -- the saved model and the byte budget (PR-1.5)


def test_dump_and_load_round_trip_the_whole_model():
    screen = feed(
        b"\x1b[31mred\x1b[0m\thello \xe6\x97\xa5\xe6\x9c\xac\r\n" + b"line\r\n" * 50
        + b"\x1b7\x1b[?6h\x1b[3;10r\x1b[?1049h\x1b[2Jalt \x1b[4:3m\x1b[38;2;1;2;3mx",
        cols=30,
        rows=8,
    )
    data = screen.dump()
    again = json.loads(json.dumps(data))
    loaded = Screen.load(again)
    assert loaded.dump() == data
    assert loaded.snapshot() == screen.snapshot()
    assert loaded.rows() == screen.rows()
    assert list(loaded.scrollback) == list(screen.scrollback)
    assert (loaded.top, loaded.bottom, loaded.on_alt, loaded.origin) == (2, 7, True, True)
    # A pen names the table once: a screen of one pen is one entry.
    assert len(data["pens"]) == len({tuple(map(str, pen)) for pen in data["pens"]})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.clear(),
        lambda d: d.update(format=2),
        lambda d: d.update(cols=0),
        lambda d: d.update(cols="12"),
        lambda d: d.update(pens="x"),
        lambda d: d.update(pens=[[1, 2, 3]]),
        lambda d: d.update(pens=[[0, 300, None, None]]),
        lambda d: d["main"].update(x=99),
        lambda d: d["main"].update(pen=500),
        lambda d: d["main"]["lines"].pop(),
        lambda d: d["main"]["lines"][0].append(None),
        lambda d: d["main"]["lines"][0].__setitem__(0, ["x" * 40, 1, 0]),
        lambda d: d.update(top=5, bottom=2),
        lambda d: d.update(tabs=[1000]),
        lambda d: d.update(charsets=["BB", "B"]),
        lambda d: d.update(scrollback=[[["a", 1, 99]]], scrollback_wrapped=[False]),
        lambda d: d.update(scrollback=[[["a", 1, 0]]], scrollback_wrapped=[]),
        lambda d: d.update(on_alt="yes"),
    ],
)
def test_load_refuses_a_damaged_model(mutate):
    data = feed(b"abc\r\ndef", cols=10, rows=3).dump()
    mutate(data)
    with pytest.raises(ValueError):
        Screen.load(data)


def test_load_takes_no_other_type():
    for bad in (None, [], "x", 3):
        with pytest.raises(ValueError):
            Screen.load(bad)


def test_the_scrollback_byte_budget_evicts_the_oldest_rows():
    screen = Screen(20, 3, scrollback=1000, scrollback_cost=600)
    tokenizer = termstream.Tokenizer()
    screen.feed(tokenizer.feed(b"".join(b"row %03d\r\n" % i for i in range(100))))
    assert screen.scrollback_bytes <= 600
    assert len(screen.scrollback) < 97  # the row count alone would have kept 97
    assert screen.scrollback_bytes == sum(termscreen._runs_cost(r) for r in screen.scrollback)
    texts = [termscreen._runs_text(r) for r in screen.scrollback]
    assert texts[-1] == "row 097" and texts == sorted(texts)  # the newest kept, in order
    # ED 3 blanks the rows and the cost with them.
    screen.feed(tokenizer.feed(b"\x1b[3J"))
    assert screen.scrollback_bytes == 0


def test_the_row_count_and_the_budget_agree_on_what_is_kept():
    screen = Screen(20, 3, scrollback=5, scrollback_cost=10**6)
    tokenizer = termstream.Tokenizer()
    screen.feed(tokenizer.feed(b"".join(b"r%d\r\n" % i for i in range(20))))
    assert len(screen.scrollback) == 5
    assert screen.scrollback_bytes == sum(termscreen._runs_cost(r) for r in screen.scrollback)
