"""Split-service spike 3: a screen-model prototype, measured against VTE.

Not product code. It sizes PR-1.2 (`collins/service/termscreen.py`) and finds
what the spec's sequence list is missing; it decides nothing about design.

    spike_split_3_screen_model.py record  DIR [SESSION ...]
    spike_split_3_screen_model.py synth   DIR
    spike_split_3_screen_model.py vte     DIR      (needs a display)
    spike_split_3_screen_model.py compare DIR [-v]
    spike_split_3_screen_model.py dialogs [DIR]
    spike_split_3_screen_model.py widths           (needs a display)

`record` runs a real `claude` in an isolated HOME and writes one stream per
session (`<session>.bin`) with the offsets its scenarios end at
(`<session>.marks.json`): a scenario is the stream up to its mark. The
sessions are `classic` and `fullscreen` (the CLI's two screen modes; startup,
a typed box, a wrapped one, CJK and emoji, a streamed turn, two permission
dialogs, a tool call with a diff: two short real turns each), their halves
(`classic-boxes`, `classic-dialogs`, `fullscreen-boxes`, `fullscreen-dialogs`:
one turn each) and `worktree` (the dialog on leaving a worktree, in both
modes: no turn). With none named it records `classic fullscreen worktree`.

`synth` writes hand-made streams that exercise the rules the recorded ones
lean on, and what VTE does with each operation while a wrap is pending.
`vte` feeds every scenario to a VTE with no child and dumps rows, cursor and,
cell by cell, what was drawn. `compare` feeds the same bytes to the prototype
and prints the parity table, the census of sequences, and the speed.
`dialogs` shows what the recorder would answer to each permission dialog: the
recorded ones in DIR, and hand-made ones it has to decline. `widths` asks VTE
for the width of every assigned code point and compares it with the
prototype's rule.

Recordings hold paths of the machine they were made on: DIR is scratch, and
nothing in it is committed. The GTK modes run under
`.agents/capture-screenshots/scripts/with-headless-display.sh`; every mode
exits 0, saying what it lacked when it could not run.
"""

from __future__ import annotations

import codecs
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter, deque

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))  # the worktree's collins, not the installed one

COLS, ROWS = 120, 40
WATCHDOG_S = 900

# The colours both sides are told to draw with, so a drawn colour can be
# compared as a number. The sixteen are xterm's.
FOREGROUND = (0xC0, 0xC0, 0xC0)
BACKGROUND = (0x00, 0x00, 0x00)
PALETTE16 = (
    (0x00, 0x00, 0x00), (0xCD, 0x00, 0x00), (0x00, 0xCD, 0x00), (0xCD, 0xCD, 0x00),
    (0x00, 0x00, 0xEE), (0xCD, 0x00, 0xCD), (0x00, 0xCD, 0xCD), (0xE5, 0xE5, 0xE5),
    (0x7F, 0x7F, 0x7F), (0xFF, 0x00, 0x00), (0x00, 0xFF, 0x00), (0xFF, 0xFF, 0x00),
    (0x5C, 0x5C, 0xFF), (0xFF, 0x00, 0xFF), (0x00, 0xFF, 0xFF), (0xFF, 0xFF, 0xFF),
)  # fmt: skip
# The DEC special graphics set, for ESC ( 0.
LINE_DRAWING = dict(zip("`afgjklmnopqrstuvwxyz{|}~", "◆▒°±┘┐┌└┼⎺⎻─⎼⎽├┤┴┬│≤≥π≠£·", strict=True))


def dim(channel: int) -> int:
    """VTE 0.84's faint, measured: two thirds, taken in sixteen bits."""
    return (channel * 257 * 2 // 3) >> 8


# --------------------------------------------------------------- tokenizer

TOKEN = re.compile(
    r"(?P<text>[^\x00-\x1f\x7f]+)"
    r"|\x1b\[(?P<csi>[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e])"
    r"|\x1b\](?P<osc>[^\x07\x1b]*)(?:\x07|\x1b\\)"
    r"|\x1b(?P<dcs>[P_^X][^\x1b]*)\x1b\\"
    r"|\x1b(?P<esc>[\x20-\x2f]*[\x30-\x4f\x51-\x57\x59\x5a\x5c\x60-\x7e])"
    r"|(?P<c0>[\x00-\x1a\x1c-\x1f\x7f])"
    r"|(?P<partial>\x1b[\s\S]*\Z)"
)
# What an escape sequence cut by a read boundary can look like. Anything
# else after an ESC is malformed, and the ESC is dropped on its own.
# Known gap, left for termstream (PR-1.1): only a malformed sequence that
# runs to the end of a feed is noted. One in the middle of a buffer is
# stepped past by the scan with no note and no census entry, and the split
# feeds of compare_one cut well-formed sequences only. The CLI and VTE's
# own output are well-formed, so nothing measured here depends on it.
PARTIAL = re.compile(
    r"\x1b(?:\[[\x30-\x3f]*[\x20-\x2f]*|\][^\x07\x1b]*\x1b?|[P_^X][^\x1b]*\x1b?|[\x20-\x2f]*)\Z"
)
PARTIAL_MAX = 64 * 1024

# ------------------------------------------------------------------- pens

BOLD, FAINT, ITALIC, INVERSE, STRIKE, BLINK, CONCEAL, OVERLINE = (1 << i for i in range(8))
UNDERLINE_SHIFT = 8  # three bits of style: 0 none, 1 single, 2 double, 3 curly, 4 dotted, 5 dashed
UNDERLINE_MASK = 7 << UNDERLINE_SHIFT
# A pen is (attrs, fg, bg, underline colour). A colour is None (default), an
# int (palette index) or an (r, g, b) tuple.
DEFAULT_PEN = (0, None, None, None)


def char_width(char: str) -> int:
    """dropimages.cell_width's rule for one character."""
    if unicodedata.category(char) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def palette_rgb(index: int) -> tuple[int, int, int]:
    if index < 16:
        return PALETTE16[index]
    if index < 232:
        index -= 16
        steps = (0, 95, 135, 175, 215, 255)
        return steps[index // 36], steps[index // 6 % 6], steps[index % 6]
    grey = 8 + 10 * (index - 232)
    return grey, grey, grey


def drawn_colours(pen, faint: bool | None = None) -> tuple[str, str]:
    """The foreground and background VTE draws a pen with, as hex. `faint`
    overrides the pen's own bit, for telling a faint mismatch from a colour
    mismatch."""
    attrs, fg, bg, _ = pen
    if faint is None:
        faint = bool(attrs & FAINT)
    front = FOREGROUND if fg is None else palette_rgb(fg) if isinstance(fg, int) else fg
    back = BACKGROUND if bg is None else palette_rgb(bg) if isinstance(bg, int) else bg
    if faint and not isinstance(fg, tuple):
        # VTE dims the default colour and the palette's; a direct colour is
        # drawn as it was given.
        front = tuple(dim(channel) for channel in front)
    if attrs & INVERSE:
        front, back = back, front
    return bytes(front).hex().upper(), bytes(back).hex().upper()


class Grid:
    """One screen's cells and the cursor that belongs to it."""

    def __init__(self, cols: int, rows: int):
        self.lines: list[list] = [[None] * cols for _ in range(rows)]
        self.wrapped: list[bool] = [False] * rows
        self.x = self.y = 0
        self.pending = False  # the deferred wrap
        self.pen = DEFAULT_PEN
        self.saved = None
        self.top, self.bottom = 0, rows - 1


class Screen:
    """The prototype. A cell is None (never written, or erased with the
    default background), or (text, width, pen); the second half of a wide
    character is ("", 0, pen), an erased cell with a background ("", 1, pen).
    """

    def __init__(self, cols: int = COLS, rows: int = ROWS, scrollback: int = 10000):
        self.cols, self.rows = cols, rows
        self.main = Grid(cols, rows)
        self.alt = Grid(cols, rows)
        self.grid = self.main
        self.on_alt = False
        self.scrollback: deque[str] = deque(maxlen=scrollback)
        self.autowrap = True
        self.origin = False
        self.insert = False
        self.cursor_visible = True
        self.tabs = set(range(8, cols, 8))
        self.charsets = ["B", "B"]
        self.shift = 0
        self.last_char = ""
        self.modes: dict[int, bool] = {}
        self.progress: str | None = None
        self.progress_seen = False
        self.bells = 0
        self.title = ""
        self.census: Counter = Counter()
        self.ignored: Counter = Counter()
        self.sgr: Counter = Counter()
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._carry = ""

    # -- feeding

    def feed(self, data: bytes) -> None:
        text = self._carry + self._decoder.decode(data)
        self._carry = ""
        pos = 0
        while True:
            restart = None
            for match in TOKEN.finditer(text, pos):
                kind = match.lastgroup
                if kind == "text":
                    self._print(match.group())
                elif kind == "csi":
                    self._csi(match.group("csi"))
                elif kind == "c0":
                    self._c0(match.group())
                elif kind == "osc":
                    self._osc(match.group("osc"))
                elif kind == "esc":
                    self._esc(match.group("esc"))
                elif kind == "dcs":
                    body = match.group("dcs")
                    kind = {"P": "DCS", "_": "APC", "^": "PM", "X": "SOS"}[body[0]]
                    self._note(kind + " " + body[1:4], handled=False)
                else:
                    tail = match.group()
                    if PARTIAL.match(tail) and len(tail) < PARTIAL_MAX:
                        self._carry = tail
                    else:
                        self._note("malformed ESC", handled=False)
                        restart = match.start() + 1
                    break
            if restart is None:
                return
            pos = restart

    def _note(self, key: str, handled: bool = True) -> None:
        self.census[key] += 1
        if not handled:
            self.ignored[key] += 1

    # -- printing

    def _print(self, run: str) -> None:
        grid = self.grid
        cols = self.cols
        if self.charsets[self.shift] == "0":
            run = "".join(LINE_DRAWING.get(char, char) for char in run)
        if run.isascii() and not self.insert:
            # The fast path: every character one cell wide.
            pen = grid.pen
            start = 0
            length = len(run)
            while start < length:
                if grid.pending:
                    self._wrap_or_stay(1)
                line = grid.lines[grid.y]
                room = cols - grid.x
                chunk = run[start : start + room]
                n = len(chunk)
                x = grid.x
                end = x + n
                self._cut(line, x)
                self._cut(line, end)
                line[x:end] = [(char, 1, pen) for char in chunk]
                start += n
                if end >= cols:
                    grid.x = cols - 1
                    grid.pending = True
                else:
                    grid.x = end
            self.last_char = run[-1]
            return
        for char in run:
            self._put(char, char_width(char))
        self.last_char = run[-1]

    def _cut(self, line: list, x: int) -> None:
        """Make column x the start of a cell. Cutting a wide character
        leaves two spaces; cutting a tab leaves spaces before the cut and a
        shorter tab after it, which is what VTE's text read shows."""
        if x <= 0 or x >= self.cols:
            return
        cell = line[x]
        if cell is None or cell[1] != 0:
            return
        lead = x - 1
        while lead > 0 and line[lead] is not None and line[lead][1] == 0:
            lead -= 1
        head = line[lead]
        if head is None or head[1] < 2:
            line[x] = (" ", 1, cell[2])
            return
        end = lead + head[1]
        if head[0] == "\t":
            line[lead:x] = [(" ", 1, head[2])] * (x - lead)
            line[x] = ("\t", end - x, head[2])
        else:
            line[lead:end] = [(" ", 1, head[2])] * (end - lead)

    def _wrap(self) -> None:
        grid = self.grid
        grid.wrapped[grid.y] = True
        grid.pending = False
        grid.x = 0
        self._linefeed()

    def _wrap_or_stay(self, width: int) -> None:
        """What the next character does at the margin: wrap, or with
        autowrap off write over the row's end."""
        if self.autowrap:
            self._wrap()
        else:
            self.grid.pending = False
            self.grid.x = self.cols - width

    def _put(self, char: str, width: int) -> None:
        grid = self.grid
        cols = self.cols
        if width == 0:
            # A mark or joiner decorates the character before it: the cell
            # under the cursor when the wrap is pending, the one before it
            # otherwise (its first half, for a wide character).
            x = grid.x if grid.pending else grid.x - 1
            line = grid.lines[grid.y]
            if x >= 0 and line[x] is not None and line[x][1] == 0 and x > 0:
                x -= 1
            if x >= 0 and line[x] is not None and line[x][0] and line[x][0] != "\t":
                cell = line[x]
                line[x] = (cell[0] + char, cell[1], cell[2])
            return
        if grid.pending or (width == 2 and grid.x == cols - 1):
            self._wrap_or_stay(width)
        line = grid.lines[grid.y]
        x = grid.x
        end = x + width
        if self.insert:
            self._cut(line, x)
            self._cut(line, cols - width)
            del line[cols - width :]
            line[x:x] = [None] * width
        self._cut(line, x)
        self._cut(line, end)
        line[x] = (char, width, grid.pen)
        if width == 2:
            line[x + 1] = ("", 0, grid.pen)
        if end >= cols:
            grid.x = cols - 1
            grid.pending = True
        else:
            grid.x = end

    # -- movement

    def _linefeed(self) -> None:
        grid = self.grid
        if grid.y == grid.bottom:
            self._scroll_up(1)
        elif grid.y < self.rows - 1:
            grid.y += 1

    def _reverse_index(self) -> None:
        grid = self.grid
        if grid.y == grid.top:
            self._scroll_down(1)
        elif grid.y > 0:
            grid.y -= 1

    def _scroll_up(self, n: int, top: int | None = None) -> None:
        grid = self.grid
        top = grid.top if top is None else top
        n = min(n, grid.bottom - top + 1)
        for _ in range(n):
            line = grid.lines.pop(top)
            if grid is self.main and top == 0:
                self.scrollback.append(self._line_text(line))
            grid.wrapped.pop(top)
            grid.lines.insert(grid.bottom, [None] * self.cols)
            grid.wrapped.insert(grid.bottom, False)

    def _scroll_down(self, n: int, top: int | None = None) -> None:
        grid = self.grid
        top = grid.top if top is None else top
        n = min(n, grid.bottom - top + 1)
        for _ in range(n):
            grid.lines.pop(grid.bottom)
            grid.wrapped.pop(grid.bottom)
            grid.lines.insert(top, [None] * self.cols)
            grid.wrapped.insert(top, False)

    def _move(self, x: int | None = None, y: int | None = None, clamp_region: bool = False) -> None:
        grid = self.grid
        if x is not None:
            grid.x = max(0, min(self.cols - 1, x))
        if y is not None:
            low, high = (grid.top, grid.bottom) if clamp_region else (0, self.rows - 1)
            grid.y = max(low, min(high, y))
        grid.pending = False

    def _move_rows(self, delta: int) -> None:
        # CUU and CUD stop at the scroll region's edge when they start inside it.
        grid = self.grid
        y = grid.y + delta
        if delta < 0:
            low = grid.top if grid.y >= grid.top else 0
            y = max(low, y)
        else:
            high = grid.bottom if grid.y <= grid.bottom else self.rows - 1
            y = min(high, y)
        self._move(y=y)

    def _c0(self, char: str) -> None:
        grid = self.grid
        if char == "\r":
            self._note("C0 CR")
            grid.x = 0
            grid.pending = False
        elif char in "\n\x0b\x0c":
            self._note("C0 " + {"\n": "LF", "\x0b": "VT", "\x0c": "FF"}[char])
            grid.pending = False
            self._linefeed()
        elif char == "\x08":
            self._note("C0 BS")
            grid.pending = False
            if grid.x:
                grid.x -= 1
        elif char == "\t":
            self._note("C0 HT")
            self._tab(1)
        elif char == "\x07":
            self._note("C0 BEL")
            self.bells += 1
        elif char in "\x0e\x0f":
            self._note("C0 SO" if char == "\x0e" else "C0 SI")
            self.shift = 1 if char == "\x0e" else 0
        else:
            self._note(f"C0 0x{ord(char):02x}", handled=False)

    def _tab(self, n: int) -> None:
        """A tab over cells nothing was written to is kept as a tab, which
        VTE's text read gives back as one; over anything else it only moves.
        With the wrap pending it does nothing."""
        grid = self.grid
        if grid.pending:
            return
        line = grid.lines[grid.y]
        for _ in range(n):
            x = grid.x
            stops = [stop for stop in self.tabs if stop > x]
            stop = min(stops) if stops else self.cols - 1
            if stop > x and all(cell is None for cell in line[x:stop]):
                line[x] = ("\t", stop - x, DEFAULT_PEN)
                line[x + 1 : stop] = [("", 0, DEFAULT_PEN)] * (stop - x - 1)
            grid.x = stop

    # -- escape sequences

    def _esc(self, body: str) -> None:
        grid = self.grid
        if body == "7":
            self._note("ESC 7 (DECSC)")
            self._save_cursor()
        elif body == "8":
            self._note("ESC 8 (DECRC)")
            self._restore_cursor()
        elif body == "D":
            self._note("ESC D (IND)")
            grid.pending = False
            self._linefeed()
        elif body == "M":
            self._note("ESC M (RI)")
            grid.pending = False
            self._reverse_index()
        elif body == "E":
            self._note("ESC E (NEL)")
            grid.x = 0
            grid.pending = False
            self._linefeed()
        elif body == "H":
            self._note("ESC H (HTS)")
            self.tabs.add(grid.x)
        elif body == "c":
            self._note("ESC c (RIS)")
            self._reset()
        elif body in ("=", ">"):
            self._note(f"ESC {body} (keypad mode)", handled=False)
        elif body[:1] in "()" and len(body) == 2:
            self._note(f"ESC {body} (charset)")
            self.charsets["()".index(body[0])] = body[1]
        else:
            self._note("ESC " + body, handled=False)

    def _reset(self) -> None:
        census, ignored, sgr = self.census, self.ignored, self.sgr
        decoder, carry = self._decoder, self._carry
        self.__init__(self.cols, self.rows, self.scrollback.maxlen)
        self.census, self.ignored, self.sgr = census, ignored, sgr
        self._decoder, self._carry = decoder, carry

    def _save_cursor(self) -> None:
        grid = self.grid
        grid.saved = (grid.x, grid.y, grid.pending, grid.pen, self.origin)

    def _restore_cursor(self) -> None:
        grid = self.grid
        if grid.saved is None:
            grid.x = grid.y = 0
            grid.pending = False
            grid.pen = DEFAULT_PEN
            return
        grid.x, grid.y, grid.pending, grid.pen, self.origin = grid.saved
        grid.x = min(grid.x, self.cols - 1)
        grid.y = min(grid.y, self.rows - 1)

    def _csi(self, body: str) -> None:
        final = body[-1]
        rest = body[:-1]
        prefix = ""
        if rest and rest[0] in "<=>?":
            prefix, rest = rest[0], rest[1:]
        params = rest.rstrip(" !\"#$%&'()*+,-./")
        inter = rest[len(params) :]
        key = prefix + inter + final
        handler = self._CSI.get(key)
        if handler is None:
            self._note(f"CSI {key} ({body[:24]})", handled=False)
            return
        handler(self, params)

    @staticmethod
    def _ints(params: str, default: int = 0) -> list[int]:
        out = []
        for part in params.split(";"):
            part = part.partition(":")[0]
            out.append(int(part) if part.isdigit() else default)
        return out

    def _n(self, params: str) -> int:
        return max(1, self._ints(params, 1)[0])

    def _cuu(self, p):
        self._note("CSI A (CUU)")
        self._move_rows(-self._n(p))

    def _cud(self, p):
        self._note("CSI B (CUD)")
        self._move_rows(self._n(p))

    def _cuf(self, p):
        self._note("CSI C (CUF)")
        self._move(x=self.grid.x + self._n(p))

    def _cub(self, p):
        self._note("CSI D (CUB)")
        self._move(x=self.grid.x - self._n(p))

    def _cnl(self, p):
        self._note("CSI E (CNL)")
        self._move_rows(self._n(p))
        self.grid.x = 0

    def _cpl(self, p):
        self._note("CSI F (CPL)")
        self._move_rows(-self._n(p))
        self.grid.x = 0

    def _cha(self, p):
        self._note("CSI G (CHA)")
        self._move(x=self._n(p) - 1)

    def _hpa(self, p):
        self._note("CSI ` (HPA)")
        self._move(x=self._n(p) - 1)

    def _vpa(self, p):
        self._note("CSI d (VPA)")
        self._row_to(self._n(p) - 1)

    def _row_to(self, y: int) -> None:
        if self.origin:
            self._move(y=self.grid.top + y, clamp_region=True)
        else:
            self._move(y=y)

    def _cup(self, p, name="CSI H (CUP)"):
        self._note(name)
        values = self._ints(p, 1)
        row = max(1, values[0])
        col = max(1, values[1]) if len(values) > 1 else 1
        self._row_to(row - 1)
        self._move(x=col - 1)

    def _hvp(self, p):
        self._cup(p, "CSI f (HVP)")

    def _erase_cell(self):
        pen = self.grid.pen
        return None if pen[2] is None else ("", 1, (0, None, pen[2], None))

    def _erase(self, line: list, start: int, end: int) -> None:
        if start >= end:
            return
        self._cut(line, start)
        self._cut(line, end)
        line[start:end] = [self._erase_cell()] * (end - start)

    def _ed(self, p, name="CSI J (ED)"):
        mode = self._ints(p)[0]
        self._note(f"{name} {mode}")
        grid = self.grid
        if mode == 3:
            self.scrollback.clear()
            return  # VTE: the cursor and a pending wrap are left as they were
        if mode == 0:
            self._erase(grid.lines[grid.y], grid.x, self.cols)
            grid.wrapped[grid.y] = False
            for y in range(grid.y + 1, self.rows):
                self._erase(grid.lines[y], 0, self.cols)
                grid.wrapped[y] = False
        elif mode == 1:
            for y in range(grid.y):
                self._erase(grid.lines[y], 0, self.cols)
                grid.wrapped[y] = False
            self._erase(grid.lines[grid.y], 0, grid.x + 1)
        elif mode == 2:
            if grid is self.main:
                # VTE scrolls the rows in use off the top instead of
                # dropping them: they are in the scrollback afterwards.
                used = self.rows
                while used and all(cell is None for cell in grid.lines[used - 1]):
                    used -= 1
                for line in grid.lines[:used]:
                    self.scrollback.append(self._line_text(line))
            for y in range(self.rows):
                self._erase(grid.lines[y], 0, self.cols)
                grid.wrapped[y] = False
            grid.pending = False
            return  # the cursor stays where it was
        grid.pending = False

    def _decsed(self, p):
        self._ed(p, "CSI ?J (DECSED)")

    def _el(self, p, name="CSI K (EL)"):
        mode = self._ints(p)[0]
        self._note(f"{name} {mode}")
        grid = self.grid
        line = grid.lines[grid.y]
        if mode == 0:
            if grid.pending:
                return  # VTE: nothing is erased and the wrap stays pending
            self._erase(line, grid.x, self.cols)
            grid.wrapped[grid.y] = False
        elif mode == 1:
            self._erase(line, 0, grid.x + 1)
        elif mode == 2:
            self._erase(line, 0, self.cols)
            grid.wrapped[grid.y] = False
        grid.pending = False

    def _decsel(self, p):
        self._el(p, "CSI ?K (DECSEL)")

    def _ech(self, p):
        self._note("CSI X (ECH)")
        grid = self.grid
        self._erase(grid.lines[grid.y], grid.x, min(self.cols, grid.x + self._n(p)))
        grid.pending = False

    def _ich(self, p):
        self._note("CSI @ (ICH)")
        grid = self.grid
        n = min(self._n(p), self.cols - grid.x)
        line = grid.lines[grid.y]
        self._cut(line, grid.x)
        self._cut(line, self.cols - n)
        del line[self.cols - n :]
        line[grid.x : grid.x] = [self._erase_cell()] * n
        grid.pending = False

    def _dch(self, p):
        self._note("CSI P (DCH)")
        grid = self.grid
        n = min(self._n(p), self.cols - grid.x)
        line = grid.lines[grid.y]
        self._cut(line, grid.x)
        self._cut(line, grid.x + n)
        del line[grid.x : grid.x + n]
        line.extend([self._erase_cell()] * n)
        grid.pending = False

    def _il(self, p):
        self._note("CSI L (IL)")
        grid = self.grid
        if grid.top <= grid.y <= grid.bottom:
            self._scroll_down(self._n(p), top=grid.y)
            grid.x = 0
            grid.pending = False

    def _dl(self, p):
        self._note("CSI M (DL)")
        grid = self.grid
        if grid.top <= grid.y <= grid.bottom:
            n = min(self._n(p), grid.bottom - grid.y + 1)
            for _ in range(n):
                grid.lines.pop(grid.y)
                grid.wrapped.pop(grid.y)
                grid.lines.insert(grid.bottom, [None] * self.cols)
                grid.wrapped.insert(grid.bottom, False)
            grid.x = 0
            grid.pending = False

    def _su(self, p):
        self._note("CSI S (SU)")
        self._scroll_up(self._n(p))

    def _sd(self, p):
        self._note("CSI T (SD)")
        self._scroll_down(self._n(p))

    def _decstbm(self, p):
        self._note("CSI r (DECSTBM)")
        values = self._ints(p, 0)
        top = (values[0] or 1) - 1
        bottom = (values[1] if len(values) > 1 and values[1] else self.rows) - 1
        bottom = min(bottom, self.rows - 1)
        if top >= bottom:
            return
        self.grid.top, self.grid.bottom = top, bottom
        self._row_to(0)
        self._move(x=0)

    def _rep(self, p):
        self._note("CSI b (REP)")
        if self.last_char:
            last = self.last_char
            self._print(last * self._n(p))

    def _tbc(self, p):
        mode = self._ints(p)[0]
        self._note(f"CSI g (TBC) {mode}")
        if mode == 0:
            self.tabs.discard(self.grid.x)
        elif mode == 3:
            self.tabs.clear()

    def _cht(self, p):
        self._note("CSI I (CHT)")
        self._tab(self._n(p))

    def _cbt(self, p):
        self._note("CSI Z (CBT)")
        grid = self.grid
        x = grid.x
        for _ in range(self._n(p)):
            stops = [stop for stop in self.tabs if stop < x]
            x = max(stops) if stops else 0
        self._move(x=x)

    def _scosc(self, p):
        self._note("CSI s (SCOSC)")
        self._save_cursor()

    def _scorc(self, p):
        self._note("CSI u (SCORC)")
        self._restore_cursor()

    def _decstr(self, p):
        self._note("CSI !p (DECSTR)")
        grid = self.grid
        grid.pen = DEFAULT_PEN
        grid.top, grid.bottom = 0, self.rows - 1
        grid.saved = None
        self.origin = self.insert = False
        self.autowrap = True
        self.cursor_visible = True

    def _sm(self, p, value=True):
        for mode in self._ints(p):
            if mode == 4:
                self._note("CSI %s (IRM)" % ("h" if value else "l"))
                self.insert = value
            else:
                self._note(f"CSI {mode} {'h' if value else 'l'}", handled=False)

    def _rm(self, p):
        self._sm(p, False)

    def _decset(self, p, value=True):
        letter = "h" if value else "l"
        for mode in self._ints(p):
            self.modes[mode] = value
            handled = True
            if mode == 6:
                self.origin = value
                self._row_to(0)
                self._move(x=0)
            elif mode == 7:
                self.autowrap = value
            elif mode == 25:
                self.cursor_visible = value
            elif mode in (47, 1047, 1049):
                self._alternate(mode, value)
            elif mode == 1048:
                if value:
                    self._save_cursor()
                else:
                    self._restore_cursor()
            else:
                handled = False  # recorded in self.modes: the tracker's, not the screen's
            self._note(f"CSI ?{mode} {letter}", handled=handled)

    def _decrst(self, p):
        self._decset(p, False)

    def _alternate(self, mode: int, value: bool) -> None:
        if value:
            if mode == 1049:
                self._save_cursor()
            if not self.on_alt:
                x, y, pen = self.grid.x, self.grid.y, self.grid.pen
                self.grid, self.on_alt = self.alt, True
                # The cursor carries over; the screens, and what each has
                # saved, are separate.
                self.grid.x, self.grid.y, self.grid.pen = x, y, pen
                self.grid.pending = False
            if mode in (1047, 1049):
                self._clear_grid(self.alt)
        else:
            if self.on_alt:
                if mode in (1047, 1049):
                    self._clear_grid(self.alt)
                x, y, pen = self.grid.x, self.grid.y, self.grid.pen
                self.grid, self.on_alt = self.main, False
                self.grid.x, self.grid.y, self.grid.pen = x, y, pen
                self.grid.pending = False
            if mode == 1049:
                self._restore_cursor()

    def _clear_grid(self, grid: Grid) -> None:
        grid.lines = [[None] * self.cols for _ in range(self.rows)]
        grid.wrapped = [False] * self.rows

    def _sgr(self, p):
        self._note("CSI m (SGR)")
        attrs, fg, bg, ul = self.grid.pen
        parts = p.split(";") if p else ["0"]
        i = 0
        count = len(parts)
        while i < count:
            part = parts[i]
            i += 1
            if ":" in part:
                sub = part.split(":")
                head = int(sub[0]) if sub[0].isdigit() else 0
                if head == 4:
                    style = int(sub[1]) if len(sub) > 1 and sub[1].isdigit() else 1
                    attrs = (attrs & ~UNDERLINE_MASK) | (min(style, 5) << UNDERLINE_SHIFT)
                    self._note(f"SGR 4:{style}")
                elif head in (38, 48, 58):
                    colour = self._colon_colour(sub)
                    self._note(f"SGR {head}:{sub[1] if len(sub) > 1 else ''} (colon form)")
                    if colour is not None:
                        if head == 38:
                            fg = colour
                        elif head == 48:
                            bg = colour
                        else:
                            ul = colour
                else:
                    self._note(f"SGR {part}", handled=False)
                continue
            n = int(part) if part.isdigit() else 0
            if n in (38, 48, 58):
                self.sgr[f"{n};{parts[i] if i < count else ''}"] += 1
            else:
                self.sgr[str(n)] += 1
            if n == 0:
                attrs, fg, bg, ul = DEFAULT_PEN
            elif n == 1:
                attrs |= BOLD
            elif n == 2:
                attrs |= FAINT
            elif n == 3:
                attrs |= ITALIC
            elif n == 4:
                attrs = (attrs & ~UNDERLINE_MASK) | (1 << UNDERLINE_SHIFT)
            elif n == 5 or n == 6:
                attrs |= BLINK
            elif n == 7:
                attrs |= INVERSE
            elif n == 8:
                attrs |= CONCEAL
            elif n == 9:
                attrs |= STRIKE
            elif n == 21:
                attrs = (attrs & ~UNDERLINE_MASK) | (2 << UNDERLINE_SHIFT)
            elif n == 22:
                attrs &= ~(BOLD | FAINT)
            elif n == 23:
                attrs &= ~ITALIC
            elif n == 24:
                attrs &= ~UNDERLINE_MASK
            elif n == 25:
                attrs &= ~BLINK
            elif n == 27:
                attrs &= ~INVERSE
            elif n == 28:
                attrs &= ~CONCEAL
            elif n == 29:
                attrs &= ~STRIKE
            elif 30 <= n <= 37:
                fg = n - 30
            elif n == 39:
                fg = None
            elif 40 <= n <= 47:
                bg = n - 40
            elif n == 49:
                bg = None
            elif n == 53:
                attrs |= OVERLINE
            elif n == 55:
                attrs &= ~OVERLINE
            elif n == 59:
                ul = None
            elif 90 <= n <= 97:
                fg = n - 90 + 8
            elif 100 <= n <= 107:
                bg = n - 100 + 8
            elif n in (38, 48, 58):
                colour = None
                kind = int(parts[i]) if i < count and parts[i].isdigit() else -1
                if kind == 5 and i + 1 < count:
                    colour = int(parts[i + 1] or 0) & 255
                    i += 2
                elif kind == 2 and i + 3 < count:
                    colour = tuple(int(v or 0) & 255 for v in parts[i + 1 : i + 4])
                    i += 4
                else:
                    i = count
                if colour is not None:
                    if n == 38:
                        fg = colour
                    elif n == 48:
                        bg = colour
                    else:
                        ul = colour
            else:
                self._note(f"SGR {n}", handled=False)
        self.grid.pen = (attrs, fg, bg, ul)

    @staticmethod
    def _colon_colour(sub: list[str]):
        kind = sub[1] if len(sub) > 1 else ""
        if kind == "5" and len(sub) > 2 and sub[2].isdigit():
            return int(sub[2]) & 255
        if kind == "2":
            # 38:2::r:g:b (with the colour-space slot) or 38:2:r:g:b.
            values = sub[3:6] if len(sub) >= 6 else sub[2:5]
            if len(values) == 3 and all(v.isdigit() for v in values):
                return tuple(int(v) & 255 for v in values)
        return None

    _CSI = {
        "A": _cuu, "B": _cud, "C": _cuf, "D": _cub, "E": _cnl, "F": _cpl,
        "G": _cha, "`": _hpa, "H": _cup, "f": _hvp, "d": _vpa,
        "J": _ed, "?J": _decsed, "K": _el, "?K": _decsel, "X": _ech,
        "@": _ich, "P": _dch, "L": _il, "M": _dl, "S": _su, "T": _sd,
        "r": _decstbm, "m": _sgr, "b": _rep, "g": _tbc, "I": _cht, "Z": _cbt,
        "s": _scosc, "u": _scorc, "!p": _decstr,
        "h": _sm, "l": _rm, "?h": _decset, "?l": _decrst,
    }  # fmt: skip

    def _osc(self, body: str) -> None:
        number, _, rest = body.partition(";")
        if number == "9" and rest.startswith("4;"):
            self._note("OSC 9;4 (progress)")
            state = rest[2:].partition(";")[0]
            self.progress = state
            if state not in ("", "0"):
                self.progress_seen = True
        elif number in ("0", "2"):
            self._note(f"OSC {number} (title)")
            self.title = rest
        else:
            self._note(f"OSC {number}{' ?' if rest.endswith('?') else ''}", handled=False)

    # -- reads

    @staticmethod
    def _line_text(line: list) -> str:
        """What VTE's text read gives for a row: the cells up to the last one
        written, a cell that was skipped over reading as a space."""
        end = len(line)
        while end and (line[end - 1] is None or (not line[end - 1][0] and line[end - 1][1] == 1)):
            end -= 1
        out = []
        for cell in line[:end]:
            if cell is None or (not cell[0] and cell[1] == 1):
                out.append(" ")
            else:
                out.append(cell[0])
        return "".join(out)

    def row_text(self, row: int) -> str:
        return self._line_text(self.grid.lines[row])

    def rows_text(self) -> list[str]:
        return [self._line_text(line) for line in self.grid.lines]

    def screen_text(self) -> str:
        """The screen as one read of VTE's gives it: a row that wrapped runs
        into the next with nothing between them."""
        out = []
        for y, line in enumerate(self.grid.lines):
            out.append(self._line_text(line))
            if not self.grid.wrapped[y]:
                out.append("\n")
        return "".join(out)

    def cursor(self) -> tuple[int, int]:
        """Column and row, the column as VTE gives it: one past the last
        while the wrap is pending."""
        grid = self.grid
        return (self.cols if grid.pending else grid.x), grid.y

    def cells(self, row: int) -> list:
        return self.grid.lines[row]

    def tail_is_faint(self, row: int, column: int) -> bool:
        """vtehtml.is_dim_run's question, asked of pens: is everything from
        the column to the end of the row one run of faint text, and nothing
        else?"""
        line = self.grid.lines[row]
        end = len(line)
        while end and (line[end - 1] is None or (not line[end - 1][0] and line[end - 1][1] == 1)):
            end -= 1
        tail = [cell for cell in line[column:end] if cell is not None and cell[1]]
        if not tail or not any(cell[0].strip() for cell in tail):
            return False
        plain_faint = (FAINT, None, None)
        colours = {drawn_colours(cell[2]) for cell in tail if cell[0].strip()}
        if len(colours) != 1:
            return False
        for cell in tail:
            attrs, fg, bg, _ = cell[2]
            if not cell[0].strip():
                if bg is not None or attrs & (INVERSE | UNDERLINE_MASK | STRIKE):
                    return False
                continue
            if (attrs & ~(BLINK | CONCEAL), fg, bg) != plain_faint:
                return False
        return True

    def tail_is_faint_bits(self, row: int, column: int) -> bool:
        """The spec's reading of the same question: the faint bit of every
        cell of the tail that shows something, whatever else its pen holds."""
        line = self.grid.lines[row]
        tail = [cell for cell in line[column:] if cell is not None and cell[0].strip()]
        return bool(tail) and all(cell[2][0] & FAINT for cell in tail)

    # The two reads below have no caller in this file. They are the rest of
    # the spec's ScreenPort (section 3.5), kept so the prototype shows the
    # whole read surface PR-1.2 has to offer.
    def first_column(self) -> tuple[str, ...]:
        return tuple((line[0][0] if line[0] is not None else "") for line in self.grid.lines)

    def capture_contents(self) -> str:
        rows = list(self.scrollback) if not self.on_alt else []
        rows.extend(self.rows_text())
        while rows and not rows[-1]:
            rows.pop()
        return "\n".join(rows)


# The census keys the spec names somewhere: §3.3's screen list, §3.3's stream
# filter (mode tracker, queries, progress, bell, title) or F8's table.
# Everything else the streams hold is what the list is missing.
NAMED_PREFIXES = (
    "C0 BS", "C0 HT", "C0 LF", "C0 VT", "C0 FF", "C0 CR", "C0 BEL",
    "ESC D", "ESC M", "ESC E", "ESC 7", "ESC 8", "ESC H",
    "CSI A ", "CSI B ", "CSI C ", "CSI D ", "CSI E ", "CSI F ", "CSI G ", "CSI H ",
    "CSI f ", "CSI d ", "CSI J ", "CSI K ", "CSI X ", "CSI @ ", "CSI P ", "CSI L ",
    "CSI M ", "CSI S ", "CSI T ", "CSI r ", "CSI m ", "CSI b ", "CSI g ",
    "CSI h (IRM)", "CSI l (IRM)", "SGR 4:", "SGR 38:", "SGR 48:", "SGR 58:",
    "CSI ?",  # every DEC private mode is the tracker's
    "CSI >u", "CSI <u", "CSI =u", "CSI >m",  # kitty keyboard stack, modifyOtherKeys
    "CSI c ", "CSI >q", "CSI >c", "CSI =c", "CSI n ", "CSI $p", "CSI t ",  # F8
    "OSC 9;4", "OSC 10 ?", "OSC 11 ?", "OSC 12 ?", "OSC 4 ", "OSC 0", "OSC 2",
    "ESC (", "ESC )",  # "the character sets", in the tracker
)  # fmt: skip


def is_named(key: str) -> bool:
    return key.startswith(NAMED_PREFIXES)


# ---------------------------------------------------------------- scenarios


def scenarios(directory: str) -> list[tuple[str, bytes]]:
    """Every (name, stream) in a recordings directory, a session's in the
    order they were marked."""
    out = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".marks.json"):
            continue
        session = name[: -len(".marks.json")]
        with open(os.path.join(directory, session + ".bin"), "rb") as f:
            data = f.read()
        with open(os.path.join(directory, name)) as f:
            marks = json.load(f)
        for mark in marks:
            out.append((f"{session}.{mark['name']}", data[: mark["offset"]]))
    return out


def feed_chunked(screen: Screen, data: bytes, size: int = 4096) -> None:
    for start in range(0, len(data), size):
        screen.feed(data[start : start + size])


# ------------------------------------------------------------------ record


class Session:
    """A CLI on a pty, its output kept and fed to the prototype as it comes,
    so the recorder can wait for a screen rather than for a number of
    seconds."""

    def __init__(self, common, env, cwd, argv=("claude",)):
        self.common = common
        self.pid, self.fd = common.spawn_cli(env, cwd, ROWS, COLS, argv)
        self.data = bytearray()
        self.screen = Screen()
        self.marks: list[dict] = []
        self.last_output = time.monotonic()

    def pump(self, until=None, quiet: float = 1.0, limit: float = 30.0) -> bool:
        """Read until `until(screen)` holds and nothing has arrived for
        `quiet` seconds. False when `limit` ran out first."""
        import select

        end = time.monotonic() + limit
        while time.monotonic() < end:
            ready, _, _ = select.select([self.fd], [], [], 0.05)
            if ready:
                try:
                    data = os.read(self.fd, 65536)
                except OSError:
                    return False
                if not data:
                    return False
                self.data += data
                self.screen.feed(data)
                self.last_output = time.monotonic()
                for query, reply in self.common.VTE_ANSWERS:
                    if query in data:
                        os.write(self.fd, reply)
                continue
            if time.monotonic() - self.last_output >= quiet and (until is None or until(self.screen)):
                return True
        return False

    def mark(self, name: str) -> None:
        self.marks.append({"name": name, "offset": len(self.data)})
        print(f"  mark {name:<16} at {len(self.data):>7}  cursor {self.screen.cursor()}", flush=True)

    def type(self, text: str) -> None:
        self.common.type_text(self.fd, text.encode())

    def show(self, why: str) -> None:
        print(f"  {why}; the screen:")
        for row in self.screen.rows_text():
            if row.strip():
                print("    " + row)

    def save(self, directory: str, name: str) -> None:
        with open(os.path.join(directory, name + ".bin"), "wb") as f:
            f.write(bytes(self.data))
        with open(os.path.join(directory, name + ".marks.json"), "w") as f:
            json.dump(self.marks, f, indent=1)


def _has(text: str):
    return lambda screen: any(text in row for row in screen.rows_text())


def _at_prompt(screen: Screen) -> bool:
    _, y = screen.cursor()
    return screen.row_text(y).startswith("❯ ")


def _turn_done(screen: Screen) -> bool:
    return screen.progress_seen and screen.progress in ("", "0") and _at_prompt(screen)


def _clear_box(session: Session) -> None:
    from collins import providers

    provider = providers.get_provider("claude")
    for _ in range(3):
        screen = session.screen
        _, y = screen.cursor()
        entered = provider.entered_prompt(screen.rows_text(), y, COLS)
        if entered is None or not entered.text:
            return
        session.type(provider.clear_prompt_keys(entered) or "\x15")
        session.pump(quiet=0.8, limit=5)


def mode_env(common, home: str, mode: str) -> dict:
    """The CLI picks its own screen mode when it is told nothing, and not
    the same one every time (measured: fresh homes came up in either). The
    variable decides it both ways."""
    env = common.cli_env(home)
    env["CLAUDE_CODE_NO_FLICKER"] = "1" if mode == "fullscreen" else "0"
    return env


def record_turns(
    common, directory: str, mode: str, name: str, boxes: bool = True, dialogs: bool = True
) -> int:
    """startup, a typed box, a wrapped box, CJK and emoji and a streamed turn
    (`boxes`, one real turn); two permission dialogs and a tool call with a
    diff (`dialogs`, one real turn)."""
    home = common.make_home(parent=directory)
    turns = 0
    session = None
    try:
        # The user's config may start sessions in a mode that asks nothing.
        argv = ("claude", "--strict-mcp-config", "--permission-mode", "default")
        session = Session(common, mode_env(common, home, mode), common.workdir(home), argv)
        print(f"recording {name} in {home}", flush=True)
        if not session.pump(until=_at_prompt, quiet=2.0, limit=40):
            session.show("the prompt never came up")
            return turns
        session.mark("startup")
        if boxes:
            turns += record_boxes(session)
        if dialogs:
            turns += record_dialogs(session)
    finally:
        if session is not None:
            tail = common.leave(session.pid, session.fd)
            session.data += tail
            session.screen.feed(tail)
            session.mark("exit")
            session.save(directory, name)
        common.remove_home(home)
    return turns


def record_boxes(session: Session) -> int:
    session.type("fix the tests in foo")
    session.pump(quiet=1.0, limit=5)
    session.mark("typed")
    _clear_box(session)
    session.type("the quick brown fox jumps over the lazy dog and keeps going " * 3)
    session.pump(quiet=1.0, limit=5)
    session.mark("typed-wrapped")
    _clear_box(session)
    session.type("日本語のテスト 👍 👩‍👩‍👧 café ❤️ 한국어 ok")
    session.pump(quiet=1.0, limit=5)
    session.mark("cjk-emoji")
    _clear_box(session)
    session.pump(quiet=0.8, limit=3)
    session.mark("cleared")
    session.type("count from 1 to 80, one number per line, nothing else\r")
    done = session.pump(until=_turn_done, quiet=1.5, limit=120)
    session.mark("streamed-turn" if done else "streamed-turn-timeout")
    return 1


DIALOG_FILE = "a.txt"
DIALOG_QUESTIONS = {
    "Create file": "Do you want to create {}?",
    "Edit file": "Do you want to make this edit to {}?",
}
DIALOG_TOOLS = {"Create file": "Write", "Edit file": "Update"}


def dialog_verdict(rows: list[str], cols: int, title: str, filename: str = DIALOG_FILE) -> str:
    """Why the permission dialog on screen must not be answered yes, or ""
    when it is the one the recorder asked for and nothing else: the tool
    named by `title`, on `filename` and no other path, in the tool call's
    own line and in the dialog both, the plain Yes selected. In the
    recorded dialogs the CLI names a file of the directory it runs in by
    its bare name, so a bare name is taken for a file in the throwaway work
    directory; anything with a directory in it is declined. How the CLI
    names a file outside that directory was never recorded."""
    asked = [y for y, row in enumerate(rows) if "Do you want to" in row]
    if not asked:
        return "no dialog on screen"
    question = asked[-1]
    rules = [y for y in range(question) if rows[y] and set(rows[y]) == {"─"} and len(rows[y]) == cols]
    if not rules:
        return "no rule above the question"
    calls = [row.strip() for row in rows[: rules[-1]] if row.startswith("● ")]
    call = f"● {DIALOG_TOOLS[title]}({filename})"
    if not calls or calls[-1] != call:
        return f"the tool call is {calls[-1] if calls else None!r}, not {call!r}"
    body = [row.strip() for row in rows[rules[-1] + 1 : question] if row.strip()]
    if len(body) < 2:
        return "no title and path above the question"
    if body[0] != title:
        return f"the dialog is {body[0]!r}, not {title!r}"
    if body[1] != filename:
        return f"the dialog names {body[1]!r}, not {filename!r}"
    if rows[question].strip() != DIALOG_QUESTIONS[title].format(filename):
        return f"the question is {rows[question].strip()!r}"
    selected = [row.strip() for row in rows[question + 1 :] if row.strip().startswith("❯")]
    if selected != ["❯ 1. Yes"]:
        return f"the selected answer is {selected!r}, not the plain Yes"
    return ""


def answer_dialog(session: Session, title: str, mark: str) -> bool:
    """Mark and accept the dialog when it is the expected one; decline it
    with Escape, and mark nothing, when it is anything else."""
    why = dialog_verdict(session.screen.rows_text(), session.screen.cols, title)
    if why:
        session.show(f"{mark} not captured, the dialog was declined: {why}")
        session.type("\x1b")
        return False
    session.mark(mark)
    session.type("\r")
    return True


def record_dialogs(session: Session) -> int:
    session.screen.progress_seen = False
    session.type(
        f"create {DIALOG_FILE} containing the single line hi, "
        "then use the Edit tool to change hi to bye\r"
    )
    dialog = _has("Do you want to")
    if not session.pump(until=dialog, quiet=1.0, limit=90):
        session.show("no permission dialog")
    elif answer_dialog(session, "Create file", "permission"):
        session.pump(until=lambda screen: not dialog(screen), quiet=0.2, limit=10)
        if session.pump(until=dialog, quiet=1.0, limit=90):
            if not answer_dialog(session, "Edit file", "permission-diff"):
                session.pump(until=_at_prompt, quiet=1.5, limit=30)
                return 1
        done = session.pump(until=_turn_done, quiet=1.5, limit=120)
        session.mark("tool-diff" if done else "tool-diff-timeout")
        return 1
    session.pump(until=_at_prompt, quiet=1.5, limit=30)
    return 1


def dialog_probe(title: str, path: str, question: str, selected: int = 1, tool: str = "") -> str:
    """A hand-made dialog in the CLI's layout, as a stream."""
    options = ("Yes", "Yes, and switch to accept edits for this session (shift+tab)", "No")
    tool = tool or f"{DIALOG_TOOLS[title]}({path})"
    rows = [f"● {tool}", "", "─" * COLS, f" {title}", f" {path}", "╌" * COLS, "  1 hi", "╌" * COLS]
    rows.append(f" {question}")
    for number, option in enumerate(options, 1):
        rows.append(f" {'❯' if number == selected else ' '} {number}. {option}")
    rows += ["", " Esc to cancel · Tab to amend"]
    return "".join(f"\x1b[{y + 3};1H{row}" for y, row in enumerate(rows))


# (what it is, the title the recorder expects, the stream, whether to accept)
DIALOG_PROBES = (
    (
        "the recorder's own file",
        "Create file",
        dialog_probe("Create file", "a.txt", "Do you want to create a.txt?"),
        True,
    ),
    (
        "an absolute path",
        "Create file",
        dialog_probe("Create file", "/home/someone/.bashrc", "Do you want to create .bashrc?"),
        False,
    ),
    (
        "the same name in another directory",
        "Create file",
        dialog_probe("Create file", "../a.txt", "Do you want to create a.txt?"),
        False,
    ),
    (
        "the same name under the home directory",
        "Edit file",
        dialog_probe("Edit file", "~/a.txt", "Do you want to make this edit to a.txt?"),
        False,
    ),
    (
        "another tool",
        "Create file",
        dialog_probe("Bash command", "rm -rf a.txt", "Do you want to proceed?", tool="Bash(rm -rf a.txt)"),
        False,
    ),
    (
        "an edit where a create was expected",
        "Create file",
        dialog_probe("Edit file", "a.txt", "Do you want to make this edit to a.txt?"),
        False,
    ),
    (
        "the right file, the session-wide answer selected",
        "Create file",
        dialog_probe("Create file", "a.txt", "Do you want to create a.txt?", selected=2),
        False,
    ),
    (
        "a tool call on another path than the dialog shows",
        "Create file",
        dialog_probe("Create file", "a.txt", "Do you want to create a.txt?", tool="Write(/etc/a.txt)"),
        False,
    ),
    (
        "a question that names another file than the path row",
        "Create file",
        dialog_probe("Create file", "a.txt", "Do you want to create b.txt?"),
        False,
    ),
)


def dialogs(directory: str | None) -> int:
    """What the recorder would answer: the recorded dialogs (accepted, or
    the recordings could not have been made by it) and the hand-made ones."""
    wrong = 0
    expected = {"permission": "Create file", "permission-diff": "Edit file"}
    found = scenarios(directory) if directory and os.path.isdir(directory) else []
    recorded = [(name, data) for name, data in found if name.rpartition(".")[2] in expected]
    if not recorded:
        print("dialogs: no recorded permission dialogs to check; the hand-made ones only")
    for name, data in recorded:
        screen = Screen()
        feed_chunked(screen, data)
        why = dialog_verdict(screen.rows_text(), screen.cols, expected[name.rpartition(".")[2]])
        print(f"  recorded   {name:<34} {'accepted' if not why else 'DECLINED: ' + why}")
        wrong += bool(why)
    for what, title, stream, accept in DIALOG_PROBES:
        screen = Screen()
        screen.feed(stream.encode())
        why = dialog_verdict(screen.rows_text(), screen.cols, title)
        verdict = "accepted" if not why else "declined: " + why
        right = accept == (not why)
        print(f"  hand-made  {what:<52} {verdict}{'' if right else '   <- WRONG'}")
        wrong += not right
    print(
        f"dialogs: {len(recorded)} recorded and {len(DIALOG_PROBES)} hand-made, "
        f"{wrong} answered other than they should be"
    )
    return 0


def record_worktree(common, directory: str, mode: str) -> None:
    """The dialog the CLI shows on leaving a worktree that holds changes. No
    turn: the change is made from here."""
    import shutil
    import subprocess

    from collins import providers

    repo = os.path.join(directory, "repo-" + mode)
    shutil.rmtree(repo, ignore_errors=True)
    os.makedirs(repo)
    env_git = dict(
        os.environ,
        GIT_AUTHOR_NAME="spike",
        GIT_AUTHOR_EMAIL="spike@example.invalid",
        GIT_COMMITTER_NAME="spike",
        GIT_COMMITTER_EMAIL="spike@example.invalid",
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_CONFIG_SYSTEM="/dev/null",
    )
    with open(os.path.join(repo, "readme.txt"), "w") as f:
        f.write("a throwaway repository\n")
    for argv in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "add", "readme.txt"],
        ["git", "commit", "-q", "-m", "start"],
    ):
        subprocess.run(argv, cwd=repo, env=env_git, check=True, stdin=subprocess.DEVNULL)
    home = common.make_home(parent=directory, trust=(repo,))
    session = None
    try:
        argv = ("claude", "--strict-mcp-config", "-w", "spike")
        session = Session(common, mode_env(common, home, mode), repo, argv)
        print(f"recording worktree-{mode} in {home}", flush=True)
        if not session.pump(until=_at_prompt, quiet=2.0, limit=40):
            session.show("the prompt never came up")
            return
        session.mark("startup")
        trees = os.path.join(repo, ".claude", "worktrees")
        made = [os.path.join(trees, name) for name in os.listdir(trees)] if os.path.isdir(trees) else []
        if not made:
            print("  no worktree was cut under", trees)
            return
        with open(os.path.join(made[0], "change.txt"), "w") as f:
            f.write("a change\n")
        provider = providers.get_provider("claude")

        def dialog(screen):
            # Not provider.worktree_exit_prompt: what this records is also
            # what says whether that grammar still reads the dialog.
            text = "\n".join(screen.rows_text())
            return "Keep worktree" in text and "Remove worktree" in text

        os.write(session.fd, b"\x03")
        time.sleep(0.15)
        os.write(session.fd, b"\x03")
        if session.pump(until=dialog, quiet=1.0, limit=15):
            session.mark("worktree-exit")
            keys = provider.worktree_exit_prompt("\n".join(session.screen.rows_text()))
            print("  provider.worktree_exit_prompt reads the dialog:", keys is not None)
            session.type("\r")
            session.pump(quiet=1.0, limit=10)
            session.mark("after-dialog")
        else:
            session.show("no worktree dialog")
    finally:
        if session is not None:
            tail = common.leave(session.pid, session.fd)
            session.data += tail
            session.save(directory, "worktree-" + mode)
        common.remove_home(home)
        shutil.rmtree(repo, ignore_errors=True)


RECORDINGS = {
    "classic": ("classic", True, True),
    "fullscreen": ("fullscreen", True, True),
    "classic-boxes": ("classic", True, False),
    "classic-dialogs": ("classic", False, True),
    "fullscreen-boxes": ("fullscreen", True, False),
    "fullscreen-dialogs": ("fullscreen", False, True),
}


def record(directory: str, what: list[str]) -> int:
    import spike_split_common as common

    if not common.have_cli():
        print("record: no `claude` on PATH or no login; nothing recorded")
        return 0
    os.makedirs(directory, exist_ok=True)
    turns = 0
    for name in what or ["classic", "fullscreen", "worktree"]:
        if name in RECORDINGS:
            mode, boxes, dialogs = RECORDINGS[name]
            turns += record_turns(common, directory, mode, name, boxes, dialogs)
        elif name == "worktree":
            record_worktree(common, directory, "classic")
            record_worktree(common, directory, "fullscreen")
        else:
            print(f"record: {name} is none of {', '.join(RECORDINGS)}, worktree")
    print("real turns spent:", turns)
    return 0


# ------------------------------------------------------------------- synth


def numbered(prefix: str, count: int) -> str:
    return "".join(f"{prefix}{i}\r\n" for i in range(1, count))


E = "\x1b"
SYNTHETIC = {
    "deferred-wrap": "A" * 120 + f"{E}[6nB\r\n" + "C" * 120 + "\r\nD" + "E" * 119 + "\x08F",
    "deferred-wrap-moves": "A" * 120 + f"{E}[1CB\r\n" + "C" * 120 + f"{E}[1DD\r\n" + "E" * 120 + f"{E}[KF",
    "wide-at-margin": "x" * 119 + "日本" + "\r\n" + "y" * 118 + "日本語",
    "wide-overwrite": f"日本語\r{E}[1CX\r\n日本語\rY\r\n日本語{E}[3D{E}[1P|\r\n日本語{E}[3D{E}[1X|",
    "combining": "café ä́ ❤️ 👩‍👩‍👧 1️⃣ 🇩🇪 👍🏽|",
    "tabs": f"a\tb\t\tc\r\n{E}[5Cx\ty\r\n" + "z" * 118 + "\tq\tr",
    "tabs-overwrite": f"a\tb\r{E}[3CX\r\nc\td{E}[2;4H{E}[1X\r\nxxxxxxxxxx\r\ty\r\n"
    f"e\tf\r{E}[1C{E}[1P\r\ng\th\r{E}[K\r\n{E}[44mi\tj{E}[0m\r\nk\t\x08\x08l",
    "erase-with-colour": f"{E}[41mred{E}[K\r\n{E}[0mplain {E}[44m{E}[5X{E}[0m|\r\n"
    f"{E}[42mabc{E}[1K{E}[0m",
    "insert-delete": f"abcdefgh\r{E}[2C{E}[3@\r\nabcdefgh\r{E}[2C{E}[3P\r\n{E}[4habc\rXY{E}[4l\r\n"
    + "w" * 120
    + f"\r{E}[2@",
    "scroll-region": numbered("line ", 12)
    + f"{E}[3;8r{E}[8;1H"
    + "in\r\n" * 4
    + f"{E}[3;1H{E}M{E}Mup{E}[r{E}[12;1Hend",
    "insert-delete-lines": numbered("row ", 10)
    + f"{E}[3;5H{E}[2Linserted{E}[7;5H{E}[1Mdeleted{E}[2;9r{E}[4;3H{E}[2L{E}[r",
    "scroll-past-screen": numbered("n", 95) + "tail",
    "su-sd": numbered("s", 20) + f"{E}[3S{E}[2Tafter",
    "rep": f"ab{E}[5b|{E}[2;1H日{E}[2b|",
    "origin": f"{E}[5;10r{E}[?6h{E}[1;1Htop{E}[20;1Hclamped{E}[?6l{E}[1;1Hhome{E}[r",
    "alternate": f"main text\r\nsecond{E}[?1049halt screen{E}[2;2Hmore{E}[?1049lback",
    "alternate-47": f"main{E}[?47halt47{E}[?47lback{E}[?1047halt1047{E}[?1047lagain",
    "alternate-saved": f"ab{E}[?1049h{E}[5;5H{E}7{E}[9;9Hx{E}[?1049lX{E}8Y",
    "save-restore": f"{E}[31mred{E}7{E}[0m{E}[5;5Hmoved{E}8same{E}[0m{E}[s{E}[9;9Hx{E}[uy",
    "sgr-forms": f"{E}[1mb{E}[2mbf{E}[22mn{E}[3mi{E}[4mu{E}[4:3mcurly{E}[21mdouble{E}[24m"
    f"{E}[38:2::10:20:30mcolon{E}[38:5:196mc5{E}[38;2;1;2;3msemi{E}[39m"
    f"{E}[48:2:4:5:6mbg{E}[49m{E}[58:2::7:8:9m{E}[4mulc{E}[59m{E}[0m"
    f"{E}[7minv{E}[2mfaintinv{E}[0m{E}[2;31mfaintred{E}[0m{E}[9mstrike{E}[0m"
    f"{E}[1;34mboldblue{E}[0m{E}[94mbright{E}[0m{E}[2;38;2;200;100;50mfainttrue{E}[0m",
    "faint-colours": f"{E}[2;38;5;196mcube{E}[0m {E}[2;38;5;240mgrey{E}[0m {E}[2;91mbright{E}[0m "
    f"{E}[2;7minverse{E}[0m {E}[1;2mboldfaint{E}[0m {E}[2;38;5;3mlow{E}[0m {E}[2;41mfaintonred{E}[0m "
    f"{E}[1;31mboldred{E}[0m {E}[8mconcealed{E}[0m {E}[5mblink{E}[0m {E}[53moverline{E}[0m",
    "private-prefix": f"a{E}[>5ub{E}[<uc{E}[>4;2md{E}[?4me{E}[=1;1uf{E}[>0qg{E}[?ug",
    "autowrap-off": f"{E}[?7l" + "N" * 125 + f"{E}[?7h\r\n" + "W" * 125,
    "cursor-moves": f"{E}[10;10Hx{E}[3Ay{E}[2Bz{E}[5Dw{E}[2Ev{E}[1Fu{E}[20Gt{E}[5ds"
    f"{E}[99;999Hr{E}[1;1Hq{E}[15`p",
    "ed-forms": numbered("e", 10) + f"{E}[5;3H{E}[1J{E}[7;3H{E}[0Jmid",
    "nel-ind": f"a{E}Eb{E}Dc{E}Md",
    "backspace-wrap": "A" * 120 + "\x08\x08X\r\n" + "B" * 120 + "C\x08\x08\x08Y",
    "charset-line-drawing": f"{E}(0lqqk{E}(B plain {E})0\x0elqqk\x0f plain",
    "shell-wrap": "$ " + "long " * 40 + "\r\n" + "日本" * 70 + "\r\nend",
    "erase-inside-row": f"{E}[44m{E}[5X{E}[0m{E}[8Cx\r\nab{E}[41m{E}[3X{E}[0m{E}[6Cy\r\n"
    f"{E}[42mabc{E}[1K{E}[0m{E}[10Gz",
    "decorations": f"{E}[58:2::200:100:50m{E}[4mcoloured{E}[0m {E}[58:5:196;4:3mcube{E}[0m "
    f"{E}[53mover{E}[0m {E}[5mblink{E}[0m {E}[8mhidden{E}[0m {E}[4;58;2;17;34;51msemi{E}[0m "
    f"{E}[58:2::1:2:3mno line{E}[0m",
}

# What VTE does with an operation that arrives while the wrap is pending:
# 120 characters, the operation, then a Z.
PENDING = {
    "el0": f"{E}[K", "el1": f"{E}[1K", "el2": f"{E}[2K", "ed0": f"{E}[J", "ed1": f"{E}[1J",
    "ed2": f"{E}[2J", "ed3": f"{E}[3J", "ech": f"{E}[X", "ich": f"{E}[@", "dch": f"{E}[P",
    "il": f"{E}[L", "dl": f"{E}[M", "sgr": f"{E}[31m", "decsc-decrc": f"{E}7{E}8",
    "cuu": f"{E}[A", "cud": f"{E}[B", "cuf": f"{E}[C", "cub": f"{E}[D", "cha": f"{E}[120G",
    "cup": f"{E}[2;120H", "vpa": f"{E}[2d", "ht": "\t", "cr": "\r", "lf": "\n", "vt": "\x0b",
    "bs": "\x08", "bel": "\x07", "so-si": "\x0e\x0f", "charset": f"{E}(B",
    "hide-cursor": f"{E}[?25l", "sync": f"{E}[?2026h", "decstbm": f"{E}[1;40r",
    "awm-off-on": f"{E}[?7l{E}[?7h", "rep": f"{E}[b", "combining": "́",
    "su": f"{E}[S", "sd": f"{E}[T", "ri": f"{E}M", "ind": f"{E}D", "title": f"{E}]0;t{E}\\",
    "alt-in-out": f"{E}[?1049h{E}[?1049l", "irm": f"{E}[4h{E}[4l", "kitty": f"{E}[>5u",
    "cpr": f"{E}[6n", "wide": "日",
}  # fmt: skip
for _name, _op in PENDING.items():
    SYNTHETIC["pending-" + _name] = "\r\n" + "P" * 120 + _op + "Z"

# Sessions with more than one mark, for what a snapshot has to carry into
# the output that follows it (spike 4): the stream up to the first mark is
# snapshotted, the rest is fed on top.
SYNTHETIC_MARKED = {
    "then-alternate": (f"main one\r\nmain two{E}[?1049h{E}[3;3Hin the alternate", f"{E}[?1049l back"),
    "then-pending": ("\r\n" + "P" * 120, "Z"),
    "then-pen": (f"{E}[1;38;2;10;200;30;48;5;17mset", " more"),
    "then-region": (numbered("r", 12) + f"{E}[3;8r{E}[8;1H", "in\r\n" * 4 + "done"),
    "then-saved": (f"{E}[5;5H{E}[35m{E}7{E}[0m{E}[1;1Hhome", f"{E}8restored"),
    "then-insert": (f"abcdef\r{E}[4h", "XY"),
    "then-origin": (f"{E}[5;10r{E}[?6h", f"{E}[1;1Htop{E}[2;1Hnext"),
    "then-tab-stops": (f"{E}[3g{E}[1;5H{E}H{E}[1;21H{E}H\r", "a\tb\tc"),
    "then-charset": (f"{E}(0", "lqqk"),
    "then-autowrap-off": (f"{E}[?7l", "N" * 125),
    "then-scrollback": (numbered("line ", 60), numbered("more ", 5)),
}


def synth(directory: str) -> int:
    os.makedirs(directory, exist_ok=True)
    sessions = {name: (("end", text),) for name, text in SYNTHETIC.items()}
    for name, (first, then) in SYNTHETIC_MARKED.items():
        sessions[name] = (("at", first), ("after", then))
    for name, parts in sessions.items():
        data = b""
        marks = []
        for mark, text in parts:
            data += text.encode()
            marks.append({"name": mark, "offset": len(data)})
        with open(os.path.join(directory, f"synth-{name}.bin"), "wb") as f:
            f.write(data)
        with open(os.path.join(directory, f"synth-{name}.marks.json"), "w") as f:
            json.dump(marks, f)
    print(f"wrote {len(sessions)} synthetic streams to {directory}")
    return 0


# --------------------------------------------------------------------- vte

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


def have_display() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))


def read_range(term, fmt, row0, col0, row1, col1) -> str:
    got = term.get_text_range_format(fmt, row0, col0, row1, col1)
    return (got[0] if isinstance(got, tuple) else got) or ""


def dump_terminal(term, Vte, cursor: tuple[int, int], top: int) -> dict:
    """Rows, cursor and drawn cells of a VTE, plus the reads terminal.py
    makes. VTE counts rows from the top of its scrollback: `cursor` is where
    the stream left the cursor and `top` the screen's first row, both by
    that count and both VTE's own (see run_vte)."""
    from collins import providers, vtehtml

    cols, rows = term.get_column_count(), term.get_row_count()
    column, abs_row = cursor
    row_text = []
    cells = []
    for y in range(rows):
        text = read_range(term, Vte.Format.TEXT, top + y, 0, top + y, cols)
        row_text.append(text.rstrip("\n"))
        drawn = []
        for x in range(cols):
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
            drawn.append(
                [
                    x,
                    char,
                    fg.group(1).upper() if fg else None,
                    bg.group(1).upper() if bg else None,
                    "".join(flag for flag, tag in CELL_TAGS if tag in html),
                    style.group(1) if style else "",
                    line.group(1).upper() if line else None,
                ]
            )
        cells.append(drawn)
    # terminal.py's own reads, cursor-anchored.
    anchor = max(0, abs_row - rows + 1)
    screen = read_range(term, Vte.Format.TEXT, anchor, 0, abs_row + rows, cols)
    lines = providers.split_screen_rows(screen, cols)
    line = read_range(term, Vte.Format.TEXT, abs_row, 0, abs_row, cols).rstrip("\n")
    tail = read_range(term, Vte.Format.HTML, abs_row, column, abs_row, cols)
    provider = providers.get_provider("claude")
    dim_tail = vtehtml.is_dim_run(tail, FOREGROUND)
    entered = provider.entered_prompt(lines, abs_row - anchor, cols)
    return {
        "cols": cols,
        "rows": rows,
        "cursor": [column, abs_row - top],
        "top": top,
        "history": read_range(term, Vte.Format.TEXT, 0, 0, top - 1, cols) if top > 0 else "",
        "screen_text": read_range(term, Vte.Format.TEXT, top, 0, top + rows - 1, cols),
        "row_text": row_text,
        "cells": cells,
        "tail_is_dim": dim_tail,
        "takes_prompt": provider.takes_prompt(line, column, dim_tail),
        "entered": None if entered is None else entered.text,
        "worktree_exit": provider.worktree_exit_prompt(screen),
    }


def run_vte(jobs: list[tuple[str, bytes]], done, read_cells: bool = True) -> int:
    """Feed each job to a fresh childless VTE and hand `done(name, dump,
    commits)` what it shows. VTE parses what it is fed on the main loop; the
    sentinel of spec F3 says when it has parsed it all."""
    import gi

    gi.require_version("Gdk", "4.0")
    gi.require_version("Gtk", "4.0")
    gi.require_version("Vte", "3.91")
    from gi.repository import Gdk, Gio, GLib, Gtk, Vte

    app = Gtk.Application(
        application_id=f"com.episode6.Collins.SpikeSplit3.p{os.getpid()}",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    state = {"waiting": None, "commits": []}

    def rgba(rgb):
        colour = Gdk.RGBA()
        colour.parse("#" + bytes(rgb).hex())
        return colour

    def on_commit(term, text, size):
        state["commits"].append(text)
        if state["waiting"] is not None and text.endswith("\x1b[0n"):
            callback, state["waiting"] = state["waiting"], None
            GLib.timeout_add(30, callback, priority=GLib.PRIORITY_DEFAULT)

    def fresh(window):
        term = Vte.Terminal()
        term.set_size(COLS, ROWS)
        term.set_halign(Gtk.Align.START)
        term.set_valign(Gtk.Align.START)
        term.set_scrollback_lines(10000)
        term.set_colors(rgba(FOREGROUND), rgba(BACKGROUND), [rgba(c) for c in PALETTE16])
        term.connect("commit", on_commit)
        window.set_child(Gtk.ScrolledWindow(child=term))
        return term

    def activate(app):
        window = Gtk.ApplicationWindow(application=app)
        window.set_default_size(1800, 1100)
        fresh(window)
        window.present()
        queue = list(jobs)

        def next_job():
            if not queue:
                app.quit()
                return GLib.SOURCE_REMOVE
            name, data = queue.pop(0)
            term = fresh(window)
            state["commits"].clear()
            seen = {}

            def feed():
                term.feed(data)
                state["waiting"] = parsed
                term.feed(b"\x1b[5n")
                return GLib.SOURCE_REMOVE

            def parsed():
                # Where the stream left the cursor, and what it made VTE
                # say. Then the screen's first row, from VTE alone: with
                # origin mode off, home is the screen's first cell wherever
                # the scroll region is, and the cursor's row there is that
                # row by VTE's count. (A cursor report would do on the main
                # screen, but it counts from the region's top in origin
                # mode, and nothing of the prototype's may be used to undo
                # that.) No cell changes; the cursor was read before.
                seen["cursor"] = tuple(term.get_cursor_position())
                seen["commits"] = list(state["commits"])
                state["waiting"] = read
                term.feed(b"\x1b[?6l\x1b[H\x1b[5n")
                return GLib.SOURCE_REMOVE

            def read():
                grid = (term.get_column_count(), term.get_row_count())
                if grid != (COLS, ROWS):
                    print(f"{name}: the grid is {grid}, not {(COLS, ROWS)}; nothing read")
                elif not read_cells:
                    done(name, None, seen["commits"])
                else:
                    top = term.get_cursor_position()[1]
                    done(name, dump_terminal(term, Vte, seen["cursor"], top), seen["commits"])
                GLib.timeout_add(10, next_job, priority=GLib.PRIORITY_DEFAULT)
                return GLib.SOURCE_REMOVE

            GLib.timeout_add(150, feed, priority=GLib.PRIORITY_DEFAULT)
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(600, next_job, priority=GLib.PRIORITY_DEFAULT)

    app.connect("activate", activate)
    GLib.timeout_add_seconds(WATCHDOG_S, lambda: os._exit(3))
    app.run([])
    return 0


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inside_a_checkout(directory: str) -> str | None:
    """The repository *directory* lies in, if any. Recordings hold paths
    (and, from a real HOME, more): they are kept out of every checkout."""
    path = os.path.realpath(directory)
    while True:
        if os.path.exists(os.path.join(path, ".git")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def vte(directory: str) -> int:
    if not have_display():
        print("vte: no display (run under with-headless-display.sh); nothing read")
        return 0
    jobs = scenarios(directory)
    if not jobs:
        print("vte: no recordings in", directory)
        return 0

    digests = {name: digest(data) for name, data in jobs}

    def done(name, dump, commits):
        dump["commits"] = commits
        # Which bytes this dump is of: compare refuses another recording's.
        dump["sha256"] = digests.get(name)
        with open(os.path.join(directory, name + ".vte.json"), "w") as f:
            json.dump(dump, f, ensure_ascii=False)
        print(
            f"{name:<40} cursor {tuple(dump['cursor'])!s:<9} "
            f"takes_prompt {dump['takes_prompt']!s:<5} entered {dump['entered']!r}"[:160],
            flush=True,
        )

    return run_vte(jobs, done)


# ----------------------------------------------------------------- compare

YES = {True: "yes", False: "NO", None: "-"}
DEFAULT_FG = bytes(FOREGROUND).hex().upper()
DEFAULT_BG = bytes(BACKGROUND).hex().upper()
FLAG_BITS = (
    ("b", BOLD),
    ("i", ITALIC),
    ("u", UNDERLINE_MASK),
    ("s", STRIKE),
    ("o", OVERLINE),
    ("k", BLINK),
)
UNDERLINES = ("", "solid", "double", "wavy", "dotted", "dashed")
# VTE keeps an underline's colour in fewer bits than it was given in
# (measured: 7;8;9 comes back as 08 0C 08, 200;100;50 within a step of 16).
UNDERLINE_COLOUR_STEP = 16


def blank(cell) -> bool:
    """Never written, or erased: what VTE's text read trims from a row's
    end and gives as a space inside it."""
    return cell is None or (not cell[0] and cell[1] == 1)


def model_cells(screen: Screen, row: int) -> dict[int, tuple]:
    """The row's cells by column, as the VTE dump has them: up to the last
    one written, the second halves of wide characters left out."""
    out = {}
    line = screen.cells(row)
    end = len(line)
    while end and blank(line[end - 1]):
        end -= 1
    for x, cell in enumerate(line[:end]):
        if blank(cell):
            out[x] = (" ", DEFAULT_PEN if cell is None else cell[2])
        elif cell[1]:
            out[x] = (cell[0], cell[2])
    return out


def near(a: str, b: str, step: int = 1) -> bool:
    return all(abs(int(a[i : i + 2], 16) - int(b[i : i + 2], 16)) <= step for i in (0, 2, 4))


def unseen(screen: Screen) -> Counter:
    """The cells of the screen that hold something none of VTE's reads can
    show, by what it is. A scenario that matches is no evidence for these.

    Measured on VTE 0.84: a cell erased with a background reads as a space
    with that background when something written follows it on the row, and
    as nothing at all when nothing does; concealed text reads as plain
    text. (Blink, overline and an underline's colour do show in the HTML
    read, and are compared.)"""
    counts: Counter = Counter()
    for line in screen.grid.lines:
        end = len(line)
        while end and blank(line[end - 1]):
            end -= 1
        for cell in line[end:]:
            if cell is not None and cell[2][2] is not None:
                counts["erased with a background, past the row's last written cell"] += 1
        for cell in line[:end]:
            if cell is None or not cell[0].strip():
                continue
            if cell[2][0] & CONCEAL:
                counts["concealed"] += 1
            if cell[2][3] is not None and not cell[2][0] & UNDERLINE_MASK:
                counts["an underline colour with no underline"] += 1
    return counts


def from_outside(name: str) -> bool:
    session = name.rpartition(".")[0]
    known = (*RECORDINGS, "worktree-classic", "worktree-fullscreen")
    return not session.startswith("synth-") and session not in known


def compare_cells(screen: Screen, dump: dict) -> tuple[Counter, list[str]]:
    counts: Counter = Counter()
    notes = []
    for y, drawn in enumerate(dump["cells"]):
        mine = model_cells(screen, y)
        theirs = {cell[0]: cell for cell in drawn}
        for x in sorted(set(mine) | set(theirs)):
            if x not in mine or x not in theirs or mine[x][0] != theirs[x][1]:
                counts["text"] += 1
                continue
            text, pen = mine[x]
            _, _, fg, bg, flags, style, line = theirs[x]
            fg, bg = fg or DEFAULT_FG, bg or DEFAULT_BG
            want_fg, want_bg = drawn_colours(pen)
            shown = bool(text.strip())
            if (shown and not near(fg, want_fg)) or not near(bg, want_bg):
                other_fg, other_bg = drawn_colours(pen, faint=not pen[0] & FAINT)
                kind = "faint" if near(fg, other_fg) and near(bg, other_bg) else "colour"
                counts[kind] += 1
                if len(notes) < 6:
                    notes.append(
                        f"{kind} at ({x},{y}) {text!r}: VTE {fg} on {bg}, "
                        f"model {want_fg} on {want_bg} (pen {pen!r})"
                    )
            want_flags = "".join(flag for flag, bit in FLAG_BITS if pen[0] & bit)
            want_style = UNDERLINES[(pen[0] & UNDERLINE_MASK) >> UNDERLINE_SHIFT]
            if shown and (want_flags, want_style) != (flags, style):
                counts["attr"] += 1
                if len(notes) < 6:
                    notes.append(
                        f"attr at ({x},{y}) {text!r}: VTE {flags!r} {style!r}, "
                        f"model {want_flags!r} {want_style!r}"
                    )
            want_line = None
            if shown and pen[0] & UNDERLINE_MASK and pen[3] is not None:
                want_line = bytes(palette_rgb(pen[3]) if isinstance(pen[3], int) else pen[3]).hex().upper()
            if shown and (
                (want_line is None) != (line is None)
                or (want_line and not near(line, want_line, UNDERLINE_COLOUR_STEP))
            ):
                counts["attr"] += 1
                if len(notes) < 6:
                    notes.append(
                        f"underline colour at ({x},{y}) {text!r}: VTE {line}, model {want_line}"
                    )
    return counts, notes


def compare_one(name: str, data: bytes, dump: dict, verbose: bool) -> dict:
    from collins import providers

    screen = Screen(dump["cols"], dump["rows"])
    feed_chunked(screen, data)
    provider = providers.get_provider("claude")
    rows = screen.rows_text()
    exact = [y for y in range(len(rows)) if rows[y] != dump["row_text"][y]]
    stripped = [y for y in exact if rows[y].rstrip() != dump["row_text"][y].rstrip()]
    counts, notes = compare_cells(screen, dump)
    # The same screen however the stream is cut.
    split = True
    for size in (1, 7):
        other = Screen(dump["cols"], dump["rows"])
        feed_chunked(other, data, size)
        if (other.rows_text(), other.cursor()) != (rows, screen.cursor()):
            split = False
    if screen.on_alt:
        history = None
    else:
        theirs = "".join(dump.get("history", "").split("\n"))
        history = "".join(screen.scrollback) == theirs
    wraps = screen.screen_text().rstrip("\n") == dump.get("screen_text", "").rstrip("\n")
    x, y = screen.cursor()
    faint = screen.tail_is_faint(y, x)
    faint_bits = screen.tail_is_faint_bits(y, x)
    takes = provider.takes_prompt(rows[y], x, faint)
    entered = provider.entered_prompt(rows, y, screen.cols)
    entered = None if entered is None else entered.text
    result = {
        "name": name,
        "bytes": len(data),
        "alt": screen.on_alt,
        "rows": len(stripped),
        "rows_exact": len(exact),
        "cursor": [x, y] == dump["cursor"],
        "counts": counts,
        "tail": faint == dump["tail_is_dim"],
        "tail_bits": faint_bits == dump["tail_is_dim"],
        "takes": takes == dump["takes_prompt"],
        "entered": entered == dump["entered"],
        "split": split,
        "history": history,
        "wraps": wraps,
        "census": screen.census,
        "unseen": unseen(screen),
        "outside": from_outside(name),
    }
    if verbose or exact:
        for row in exact[:8]:
            notes.append(f"row {row:2d} {'≠' if row in stripped else '~'} model {rows[row]!r}")
            notes.append(f"         VTE   {dump['row_text'][row]!r}")
    if not result["cursor"]:
        notes.append(f"cursor: model {(x, y)}, VTE {tuple(dump['cursor'])}")
    if not result["entered"]:
        notes.append(f"entered_prompt: model {entered!r}, VTE {dump['entered']!r}")
    if not result["takes"]:
        notes.append(
            f"takes_prompt: model {takes} (faint tail {faint}), "
            f"VTE {dump['takes_prompt']} (dim tail {dump['tail_is_dim']})"
        )
    if not result["tail_bits"]:
        notes.append(
            f"the faint bit alone says the tail is faint: {faint_bits}; VTE's drawn colour says "
            f"{dump['tail_is_dim']}"
        )
    if not wraps:
        notes.append("the rows that run into the next (soft wraps) are not VTE's")
    for what, count in result["unseen"].items():
        notes.append(f"not evidence for {count} cells {what}: VTE's reads cannot show them")
    result["notes"] = notes
    return result


def speed(directory: str) -> list[str]:
    out = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".bin") or name.startswith("synth-"):
            continue
        with open(os.path.join(directory, name), "rb") as f:
            data = f.read()
        if len(data) < 2000:
            continue
        copies = max(1, 4 * 1024 * 1024 // len(data))
        screen = Screen()
        screen._note = lambda key, handled=True: None  # the census is the spike's, not the model's
        screen.sgr = Counter()
        started = time.perf_counter()
        for _ in range(copies):
            feed_chunked(screen, data)
        seconds = time.perf_counter() - started
        total = copies * len(data)
        out.append(
            f"{name:<28} {len(data) / 1e3:6.1f} KB x {copies:<4} "
            f"tokenizer + model {total / seconds / 1e6:5.2f} MB/s"
        )
    return out


def model_lines() -> int:
    with open(__file__) as f:
        source = f.read()
    start = source.index("# --------------------------------------------------------------- tokenizer")
    end = source.index("# The census keys the spec names")
    return source[start:end].count("\n")


def compare(directory: str, verbose: bool) -> int:
    jobs = scenarios(directory)
    if not jobs:
        print("compare: no recordings in", directory, "(run `record` and `synth` first)")
        return 0
    results = []
    missing = 0
    stale = []
    for name, data in jobs:
        path = os.path.join(directory, name + ".vte.json")
        if not os.path.exists(path):
            missing += 1
            continue
        with open(path) as f:
            dump = json.load(f)
        if dump.get("sha256") != digest(data):
            stale.append(name)
            continue
        results.append(compare_one(name, data, dump, verbose))
    if missing:
        print(f"compare: {missing} scenarios have no VTE dump (run `vte`)")
    if stale:
        print(
            f"compare: {len(stale)} VTE dumps are of other bytes than the recording beside them "
            f"and were not compared (run `vte` again): {', '.join(stale[:6])}"
            + (" …" if len(stale) > 6 else "")
        )
    if not results:
        return 0
    print(
        f"{'scenario':<38} {'bytes':>6} {'on':>4} {'rows':>5} {'cursor':>6} {'text':>5} {'faint':>5} "
        f"{'colour':>6} {'attr':>5} {'tail':>4} {'bits':>4} {'takes':>5} {'entered':>7} "
        f"{'wraps':>5} {'hist':>4} {'split':>5} {'unseen':>6}"
    )
    clean = 0
    partly = 0
    hidden: Counter = Counter()
    outside = sorted({r["name"].rpartition(".")[0] for r in results if r["outside"]})
    for r in results:
        c = r["counts"]
        name = r["name"] + (" †" if r["outside"] else "")
        print(
            f"{name:<38} {r['bytes']:>6} {'alt' if r['alt'] else 'main':>4} "
            f"{r['rows']}/{r['rows_exact']:<3} {YES[r['cursor']]:>6} {c['text']:>5} {c['faint']:>5} "
            f"{c['colour']:>6} {c['attr']:>5} {YES[r['tail']]:>4} {YES[r['tail_bits']]:>4} "
            f"{YES[r['takes']]:>5} {YES[r['entered']]:>7} {YES[r['wraps']]:>5} "
            f"{YES[r['history']]:>4} {YES[r['split']]:>5} {sum(r['unseen'].values()):>6}"
        )
        for note in r["notes"]:
            print("      " + note[:200])
        same = (
            not r["rows_exact"]
            and not sum(c.values())
            and all(r[key] for key in ("cursor", "tail", "takes", "entered", "split", "wraps"))
            and r["history"] is not False
        )
        clean += same
        partly += same and bool(r["unseen"])
        hidden.update(r["unseen"])
    print(
        f"\n{clean} of {len(results)} scenarios read the same in the prototype and in VTE, in "
        "everything VTE's reads show."
    )
    if partly:
        print(
            f"{partly} of those hold cells VTE's reads cannot show, and are no evidence for them:\n  "
            + "\n  ".join(f"{count} cells {what}" for what, count in hidden.items())
        )
    if outside:
        print(
            f"† {', '.join(outside)}: not one of the recorder's sessions. `record` as it stands "
            "cannot make it;\n  it is a recording from outside it, compared like the others."
        )
    print(
        "rows: differing after rstrip / differing exactly. text, faint, colour, attr: cells, of\n"
        "every cell VTE drew something in (attr: bold, italic, underline and its style and colour,\n"
        "strike, overline, blink). tail: tail_is_faint against vtehtml.is_dim_run at the\n"
        "cursor; bits: the same with the faint bit alone. takes, entered: the grammar over the\n"
        "prototype's rows against the grammar over VTE's, read the way terminal.py reads.\n"
        "wraps: one read of the whole screen (rows that wrapped run together). hist: the text\n"
        "that scrolled off. split: fed a byte at a time and seven at a time, the same screen.\n"
        "unseen: cells holding what VTE's reads cannot show."
    )
    census: Counter = Counter()
    ignored: Counter = Counter()
    sgr: Counter = Counter()
    for name in sorted(os.listdir(directory)):
        if name.endswith(".bin") and not name.startswith("synth-"):
            screen = Screen()
            with open(os.path.join(directory, name), "rb") as f:
                feed_chunked(screen, f.read())
            census.update(screen.census)
            ignored.update(screen.ignored)
            sgr.update(screen.sgr)
    if census:
        print("\nSequences in the recorded streams (whole sessions), by count:")
        for key, count in census.most_common():
            named = "named" if is_named(key) else "NOT NAMED in §3.3"
            acted = ", ignored by the prototype" if key in ignored else ""
            print(f"  {count:>7}  {key:<34} {named}{acted}")
        print("\nSGR parameters in the recorded streams:")
        print("  " + "  ".join(f"{code} x{count}" for code, count in sgr.most_common()))
    lines = speed(directory)
    if lines:
        print("\nSpeed (4 KiB feeds, the census off):")
        for line in lines:
            print("  " + line)
    print(f"\nthe prototype (tokenizer, pens, screen) is {model_lines()} lines of this file")
    return 0


# ------------------------------------------------------------------ widths


def widths() -> int:
    """VTE's width for every assigned code point against char_width's."""
    if not have_display():
        print("widths: no display (run under with-headless-display.sh); nothing measured")
        return 0
    skipped = ("Cn", "Co", "Cs", "Cc")
    points = [cp for cp in range(0x20, 0x110000) if unicodedata.category(chr(cp)) not in skipped]
    batch = 4000
    jobs = []
    for start in range(0, len(points), batch):
        chunk = points[start : start + batch]
        # One column in, so a mark has something to sit on: the width is how
        # far past it the cursor ends.
        data = "".join(f"\r\x1b[2K.{chr(cp)}\x1b[6n" for cp in chunk).encode()
        jobs.append((str(start), data))
    wrong: Counter = Counter()
    examples: dict[tuple, list] = {}
    totals: Counter = Counter()

    def done(name, dump, commits):
        start = int(name)
        chunk = points[start : start + batch]
        replies = re.findall(r"\x1b\[(\d+);(\d+)R", "".join(commits))
        if len(replies) != len(chunk):
            print(f"widths: batch {name} answered {len(replies)} of {len(chunk)}")
            return
        for cp, (_, col) in zip(chunk, replies, strict=True):
            theirs = int(col) - 2
            mine = char_width(chr(cp))
            totals["checked"] += 1
            if theirs != mine:
                char = chr(cp)
                key = (mine, theirs, unicodedata.category(char), unicodedata.east_asian_width(char))
                wrong[key] += 1
                examples.setdefault(key, [])
                if len(examples[key]) < 6:
                    examples[key].append(f"U+{cp:04X}")

    run_vte(jobs, done, read_cells=False)
    print("code points checked:", totals["checked"], "of", len(points))
    print("unicodedata", unicodedata.unidata_version)
    print("differing:", sum(wrong.values()))
    for key, count in wrong.most_common():
        print(
            f"  {count:>6}  rule says {key[0]}, VTE {key[1]}  category {key[2]}, "
            f"east asian width {key[3]}  e.g. {' '.join(examples[key])}"
        )
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in ("record", "synth", "vte", "compare", "dialogs", "widths"):
        print(__doc__)
        return 0
    mode = argv[1]
    if mode == "widths":
        return widths()
    if mode == "dialogs":
        return dialogs(argv[2] if len(argv) > 2 else None)
    if len(argv) < 3:
        print(f"{mode}: name the recordings directory")
        return 0
    directory = argv[2]
    if mode in ("record", "synth"):
        checkout = inside_a_checkout(directory)
        if checkout:
            print(f"{mode}: {directory} is inside the checkout {checkout}; recordings hold paths "
                  "and are kept out of every repository. Name a scratch directory.")  # fmt: skip
            return 2
    if mode == "record":
        return record(directory, argv[3:])
    if mode == "synth":
        return synth(directory)
    if not os.path.isdir(directory):
        print(f"{mode}: {directory} is not a directory; nothing to do")
        return 0
    if mode == "vte":
        return vte(directory)
    return compare(directory, "-v" in argv[3:] or "--verbose" in argv[3:])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
