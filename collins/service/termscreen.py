# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""The screen model: the terminal of record for every automated read.

The service keeps one `Screen` per pty (split-service spec §3.3). It consumes
the tokens `termstream`'s tokenizer cuts a pty's output into, and it is what
the service reads when a `Session` asks whether a prompt would land
(`providers.takes_prompt` over `rows()` and `cursor()`), what the box holds
(`entered_prompt`), whether the spinner moved (`first_column()`), what a
panel shell showed (`capture_contents()`), and what an attaching client is
redrawn with (`snapshot()`, the whole of attach since the user's decision of
2026-09-29: no bytes are recorded, the screen is redrawn from this model at
the client's size, as tmux does).

GTK-free and stdlib only. It also implements `termstream.ScreenHook`, so a
`StreamFilter` built with ``screen=Screen(...)`` answers CPR and DECRQSS from
the screen as it stands at the query.

What it keeps
-------------
Two screens (main and alternate) of cells; a cell is ``None`` (never
written, or erased with the default background) or ``(text, width, pen)``:
the second half of a wide character is ``("", 0, pen)``, a cell erased with
a background ``("", 1, pen)``, a tab over never-written cells ``("\\t", n,
pen)`` followed by ``n - 1`` zero-width cells. A pen is ``(attrs, fg, bg,
underline colour)``, kept whole so a redraw reproduces it: ``attrs`` holds
bold, faint, italic, blink, inverse, conceal, strikethrough, overline and
three bits of underline style; a colour is ``None`` (default), a palette
index or an ``(r, g, b)`` tuple. Each screen has its own cursor with the
deferred wrap, its scroll region and its saved cursor (DECSC is per screen,
as VTE keeps it). The main screen has a scrollback of `SCROLLBACK_ROWS`
rows, each kept as runs of ``(text, width, pen)`` so a redraw shows old
output in its colours. Tab stops, the character sets and shift, origin,
autowrap and insert mode are the screen's; every other mode is the
tracker's (`termstream.ModeTracker`), whose `preamble(screen=False)` the
caller hands to `snapshot()`.

What VTE does that the standards leave open (F13, measured)
----------------------------------------------------------
The model does the same, and `scripts/check_termscreen_parity.py` holds it
to that against a real VTE on every fixture:

- A pending wrap (the cursor past the last column after a character landed
  there) reads as column = cols; CPR reports it so. EL 0, ED 3, HT, SGR,
  mode sets, DECSC/DECRC, SU/SD, BEL, OSC and the alternate-screen switches
  leave it standing; ED 0/1/2, EL 1/2, ECH, ICH, DCH, CUU/CUD/CUF, LF/IND/
  RI, CUP/CHA/VPA clear it and act at the last column; BS and CUB go to
  cols - 2. With DECAWM off the cursor still parks past the margin and the
  next character overwrites the last cell.
- ED 2 on the main screen moves the rows in use into scrollback.
- Tabs are tab cells: HT over never-written cells reads back as ``\\t``, an
  overwrite inside one leaves spaces before and a shorter tab after, and a
  tab cell takes no background. A tab with the wrap pending does nothing.
- A never-written cell is distinct from a space: inside a row it reads as
  a space, at the row's end it is trimmed, and a cell erased with a
  background is trimmed too.
- The saved cursor is per screen. IL and DL move the cursor to column 0.
- A CSI with a private prefix (``<``, ``=``, ``>``, ``?``) that is not a
  mode set is ignored, never read as its unprefixed twin.
- SO and SI select G1 and G0; ``ESC ( 0`` is DEC special graphics, drawn as
  the Unicode box characters VTE draws. OSC 8 (hyperlinks), OSC 99 (kitty
  notifications) and every other string are ignored here (the filter
  raises what matters as events).
- Width is `dropimages.cell_width`'s rule: ``east_asian_width`` W and F are
  two cells, combining marks and format characters zero. F13: that agrees
  with VTE on 154 525 of 154 998 code points; the rest (`WIDTH_SKEW`) are
  Hangul jamo VTE draws at zero, Unicode 16's new wide characters VTE draws
  narrow, and newer combining marks.
- `tail_is_faint` answers what today's HTML read answers (the user's
  decision): faint with a palette or the default foreground is dim, faint
  with a direct (truecolor) foreground is not, and the question is asked
  of what is drawn, inverse applied after.

No reflow (D19): `resize` truncates or pads the rows of both screens and
clears the scroll region, as xterm does; VTE rewraps, so the two disagree
until the program repaints (the CLI repaints everything, F6).

The redraw
----------
`snapshot(preamble)` is the bytes a fresh terminal of the same size is fed
to show this screen: the scrollback rows as text with their pens, each
ended by CR LF (a row that wrapped at the margin wraps again where the next
row lets it), pushed above the screen; the main screen with absolute
addressing and a full SGR per pen change, erased cells erased again so a
row's text still ends where it did; when the alternate screen is up,
``CSI ?1049h`` and then that screen the same way (so leaving it later
shows the main screen, not a blank); the tracker's preamble; the tab stops
and character sets; the cursor (a pending wrap is re-made by writing the
last column's character last); the current pen. What a redraw does not
carry, by measurement (F14): a tab that no longer ends on a tab stop
(spaces), a wrap flag ahead of an empty row, OSC 8 hyperlinks.

Throughput
----------
Measured 2026-10-02 on the dev box (Python 3.14.4), 8 MB fed in 64 KiB
reads, best of three; "model" is the tokenizer's tokens through `apply`,
"filter" the whole `StreamFilter` with the screen attached:

====================================================  ========  ========
Stream                                                Model     Filter
====================================================  ========  ========
The recorded CLI sessions, repeated (the fixtures)    4.9 MB/s  3.7 MB/s
Generated paint: sync pairs, CUP + EL per row, SGR    6.8 MB/s  6.1 MB/s
Plain text lines                                      7.4 MB/s  7.4 MB/s
====================================================  ========  ========

Against the CLI's busiest second, 5.1 KB (F5), the slowest row is seven
hundred times over: under one percent of a core per busy session (§3.18's
**[verify]**, answered). The cost is one tuple per cell written; the fast
path for ASCII runs builds them in one slice assignment.
"""

from __future__ import annotations

import unicodedata
from collections import deque

from .termstream import Bad, Control, Csi, Esc, Str, Text, Token

SCROLLBACK_ROWS = 10_000
# The grid a pty is spawned with when no client says otherwise (§3.3).
DEFAULT_COLS, DEFAULT_ROWS = 120, 40

# The colours VTE draws with by default (and the parity check sets), as 0-255.
FOREGROUND = (0xC0, 0xC0, 0xC0)
BACKGROUND = (0x00, 0x00, 0x00)
PALETTE16 = (
    (0x00, 0x00, 0x00), (0xCD, 0x00, 0x00), (0x00, 0xCD, 0x00), (0xCD, 0xCD, 0x00),
    (0x00, 0x00, 0xEE), (0xCD, 0x00, 0xCD), (0x00, 0xCD, 0xCD), (0xE5, 0xE5, 0xE5),
    (0x7F, 0x7F, 0x7F), (0xFF, 0x00, 0x00), (0x00, 0xFF, 0x00), (0xFF, 0xFF, 0x00),
    (0x5C, 0x5C, 0xFF), (0xFF, 0x00, 0xFF), (0x00, 0xFF, 0xFF), (0xFF, 0xFF, 0xFF),
)  # fmt: skip
# DEC special graphics, for ESC ( 0: what VTE draws for each byte.
LINE_DRAWING = dict(zip("`afgjklmnopqrstuvwxyz{|}~", "◆▒°±┘┐┌└┼⎺⎻─⎼⎽├┤┴┬│≤≥π≠£·", strict=True))

# ------------------------------------------------------------------- pens

BOLD, FAINT, ITALIC, INVERSE, STRIKE, BLINK, CONCEAL, OVERLINE = (1 << i for i in range(8))
UNDERLINE_SHIFT = 8  # style: 0 none, 1 single, 2 double, 3 curly, 4 dotted, 5 dashed
UNDERLINE_MASK = 7 << UNDERLINE_SHIFT
DEFAULT_PEN = (0, None, None, None)
Pen = tuple
Cell = tuple | None
RGB = tuple[int, int, int]

_ZERO_WIDTH = frozenset({"Mn", "Me", "Cf"})
_EAW = unicodedata.east_asian_width
_CATEGORY = unicodedata.category

# Code points whose width this rule gets other than VTE 0.84 does (F13: 473
# of 154 998 assigned). Each is a range (inclusive) with what VTE draws.
WIDTH_SKEW = (
    ("Hangul jamo (vowels and trailing consonants, VTE draws at zero)", 0x1160, 0x11FF, 0),
    ("Hangul jamo extended-B (VTE zero)", 0xD7B0, 0xD7FF, 0),
    ("soft hyphen (VTE one cell)", 0x00AD, 0x00AD, 1),
    ("Unicode 16 wide characters VTE 0.84 draws narrow", 0x1FA89, 0x1FA8F, 1),
    ("Unicode 16 wide characters VTE 0.84 draws narrow", 0x1FADC, 0x1FADF, 1),
)


def char_width(char: str) -> int:
    """`dropimages.cell_width`'s rule for one character."""
    if _CATEGORY(char) in _ZERO_WIDTH:
        return 0
    return 2 if _EAW(char) in ("W", "F") else 1


def dim(channel: int) -> int:
    """VTE 0.84's faint, measured: two thirds, taken in sixteen bits."""
    return (channel * 257 * 2 // 3) >> 8


def palette_rgb(index: int, palette: tuple = PALETTE16) -> RGB:
    if index < 16:
        return palette[index]
    if index < 232:
        index -= 16
        steps = (0, 95, 135, 175, 215, 255)
        return steps[index // 36], steps[index // 6 % 6], steps[index % 6]
    grey = 8 + 10 * (index - 232)
    return grey, grey, grey


def drawn_colours(
    pen: Pen, foreground: RGB = FOREGROUND, background: RGB = BACKGROUND, palette: tuple = PALETTE16
) -> tuple[RGB, RGB]:
    """The foreground and background VTE draws a pen with: faint dims the
    default and a palette colour by two thirds and leaves a direct colour
    alone; inverse swaps them afterwards."""
    attrs, fg, bg, _ = pen
    front = foreground if fg is None else palette_rgb(fg, palette) if isinstance(fg, int) else fg
    back = background if bg is None else palette_rgb(bg, palette) if isinstance(bg, int) else bg
    if attrs & FAINT and not isinstance(fg, tuple):
        front = (dim(front[0]), dim(front[1]), dim(front[2]))
    if attrs & INVERSE:
        front, back = back, front
    return front, back


_ATTRIBUTE_CODES = (
    (BOLD, "1"), (FAINT, "2"), (ITALIC, "3"), (BLINK, "5"), (INVERSE, "7"),
    (CONCEAL, "8"), (STRIKE, "9"), (OVERLINE, "53"),
)  # fmt: skip


def _colour_params(value, base: int) -> list[str]:
    """SGR parameters for a colour; `base` is 30, 40 or 58."""
    if value is None:
        return []
    if isinstance(value, tuple):
        if base == 58:
            return [f"58:2::{value[0]}:{value[1]}:{value[2]}"]
        return [str(base + 8), "2", str(value[0]), str(value[1]), str(value[2])]
    if base == 58:
        return [f"58:5:{value}"]
    if value < 8:
        return [str(base + value)]
    if value < 16:
        return [str(base + 60 + value - 8)]
    return [str(base + 8), "5", str(value)]


def pen_sgr(pen: Pen) -> str:
    """The pen from nothing: a reset, then everything it holds."""
    attrs, fg, bg, ul = pen
    parts = ["0"]
    parts += [code for bit, code in _ATTRIBUTE_CODES if attrs & bit]
    style = (attrs & UNDERLINE_MASK) >> UNDERLINE_SHIFT
    if style:
        parts.append(f"4:{style}")
    parts += _colour_params(fg, 30) + _colour_params(bg, 40) + _colour_params(ul, 58)
    return "\x1b[" + ";".join(parts) + "m"


def blank(cell: Cell) -> bool:
    """Never written, or erased: what VTE's text read trims from a row's end
    and gives as a space inside it."""
    return cell is None or (not cell[0] and cell[1] == 1)


# ------------------------------------------------------------------- grid


class Grid:
    """One screen's cells and the cursor that belongs to it."""

    __slots__ = ("lines", "wrapped", "x", "y", "pending", "pen", "saved", "top", "bottom")

    def __init__(self, cols: int, rows: int):
        self.lines: list[list] = [[None] * cols for _ in range(rows)]
        self.wrapped: list[bool] = [False] * rows
        self.x = self.y = 0
        self.pending = False  # the deferred wrap
        self.pen = DEFAULT_PEN
        self.saved = None  # (x, y, pending, pen, origin)
        self.top, self.bottom = 0, rows - 1


def _trim(line: list) -> int:
    """One past the last cell VTE's text read shows."""
    end = len(line)
    while end and blank(line[end - 1]):
        end -= 1
    return end


def _line_text(line: list) -> str:
    """What VTE's text read gives for a row: the cells up to the last one
    written, a skipped or erased cell reading as a space."""
    out = []
    for cell in line[: _trim(line)]:
        if blank(cell):
            out.append(" ")
        else:
            out.append(cell[0])
    return "".join(out)


def _line_runs(line: list) -> tuple:
    """A row as ``(text, width, pen)`` runs, the way the scrollback keeps it:
    up to the last cell written, blanks as spaces, a tab as its own run."""
    runs: list = []
    text: list[str] = []
    width = 0
    pen = None
    for cell in line[: _trim(line)]:
        if cell is not None and cell[1] == 0:
            continue
        if blank(cell):
            char, w, p = " ", 1, (DEFAULT_PEN if cell is None else cell[2])
        else:
            char, w, p = cell
        if char == "\t":
            if text:
                runs.append(("".join(text), width, pen))
                text, width = [], 0
            runs.append(("\t", w, p))
            pen = None
            continue
        if p != pen:
            if text:
                runs.append(("".join(text), width, pen))
            text, width, pen = [], 0, p
        text.append(char)
        width += w
    if text:
        runs.append(("".join(text), width, pen))
    return tuple(runs)


def _runs_text(runs: tuple) -> str:
    return "".join(run[0] for run in runs)


# ----------------------------------------------------------------- screen


class Screen:
    """The screen model of one pty. Feed it `termstream` tokens with
    `apply`; read it with `rows`, `cursor`, `tail_is_faint`,
    `first_column`, `capture_contents`; redraw it with `snapshot`."""

    def __init__(self, cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS, scrollback: int = SCROLLBACK_ROWS):
        self.cols, self.rows_count = cols, rows
        self._scrollback_max = scrollback
        self._init_state()

    def _init_state(self) -> None:
        cols, rows = self.cols, self.rows_count
        self.main = Grid(cols, rows)
        self.alt = Grid(cols, rows)
        self.grid = self.main
        self.on_alt = False
        self.scrollback: deque[tuple] = deque(maxlen=self._scrollback_max)
        self.scrollback_wrapped: deque[bool] = deque(maxlen=self._scrollback_max)
        self.autowrap = True
        self.origin = False
        self.insert = False
        self.tabs: set[int] = set(range(8, cols, 8))
        self.charsets = ["B", "B"]
        self.shift = 0
        self.last_char = ""

    # -- the hook

    def apply(self, token: Token) -> None:
        cls = type(token)
        if cls is Text:
            self._print(token.raw.decode("utf-8", "replace"))
        elif cls is Csi:
            self._csi(token)
        elif cls is Control:
            self._c0(token.raw[0])
        elif cls is Esc:
            self._esc(token)
        elif cls is Str or cls is Bad:
            pass  # strings are the filter's (queries, title, progress, OSC 8, OSC 99)

    def feed(self, tokens: list) -> None:
        apply = self.apply
        for token in tokens:
            apply(token)

    def cursor(self) -> tuple[int, int]:
        """Column and row, the column as VTE gives it: one past the last
        while the wrap is pending."""
        grid = self.grid
        return (self.cols if grid.pending else grid.x), grid.y

    def sgr(self) -> str:
        """The DECRQSS SGR answer's body for the current pen, in the form
        VTE builds it (a reset, then each attribute, then the colours)."""
        return pen_sgr(self.grid.pen)[2:-1]

    # -- reads (the ScreenPort of §3.5)

    def columns(self) -> int:
        return self.cols

    def row_count(self) -> int:
        return self.rows_count

    def rows(self) -> list[str]:
        return [_line_text(line) for line in self.grid.lines]

    def row_text(self, row: int) -> str:
        return _line_text(self.grid.lines[row])

    def cells(self, row: int) -> list:
        return self.grid.lines[row]

    def screen_text(self) -> str:
        """The screen as one read of VTE's gives it: a row that wrapped runs
        into the next with nothing between them."""
        out = []
        for y, line in enumerate(self.grid.lines):
            out.append(_line_text(line))
            if not self.grid.wrapped[y]:
                out.append("\n")
        return "".join(out)

    def first_column(self) -> tuple[str, ...]:
        """The first cell of every row: the spinner's column."""
        out = []
        for line in self.grid.lines:
            cell = line[0]
            out.append("" if cell is None else cell[0])
        return tuple(out)

    def tail_is_faint(self, row: int, column: int, foreground: RGB = FOREGROUND) -> bool:
        """`vtehtml.is_dim_run`'s question asked of pens: is everything from
        the column to the row's end one run of text drawn as the dimmed
        default foreground, and nothing else? Faint with a palette or
        direct colour draws another colour and reads as not dim, as today."""
        line = self.grid.lines[row]
        tail = [cell for cell in line[column : _trim(line)] if cell is not None and cell[1]]
        if not tail or not any(cell[0].strip() for cell in tail):
            return False
        for cell in tail:
            attrs, fg, bg, _ = cell[2]
            if not cell[0].strip():
                if bg is not None or attrs & (INVERSE | UNDERLINE_MASK | STRIKE):
                    return False
                continue
            if (attrs & ~(BLINK | CONCEAL), fg, bg) != (FAINT, None, None):
                return False
        return True

    def capture_contents(self) -> str:
        """The scrollback and the screen as text, trailing empty rows
        dropped: what `read_terminal` and the panel history want. On the
        alternate screen the scrollback is out of reach, as in VTE."""
        rows = [_runs_text(runs) for runs in self.scrollback] if not self.on_alt else []
        rows.extend(self.rows())
        while rows and not rows[-1]:
            rows.pop()
        return "\n".join(rows)

    # -- resize (no reflow, D19)

    def resize(self, cols: int, rows: int) -> None:
        cols, rows = max(1, cols), max(1, rows)
        for grid in (self.main, self.alt):
            for line in grid.lines:
                if cols < len(line):
                    del line[cols:]
                    _cut_wide(line, cols)
                else:
                    line.extend([None] * (cols - len(line)))
            if rows < len(grid.lines):
                # Rows scroll off the top only as far as keeping the cursor
                # on screen needs, into the scrollback on the main screen.
                above = max(0, grid.y - (rows - 1))
                for _ in range(above):
                    line = grid.lines.pop(0)
                    grid.wrapped.pop(0)
                    if grid is self.main:
                        self._push_scrollback(line, False)
                grid.y -= above
                del grid.lines[rows:]
                del grid.wrapped[rows:]
            else:
                grid.lines.extend([[None] * cols for _ in range(rows - len(grid.lines))])
                grid.wrapped.extend([False] * (rows - len(grid.wrapped)))
            grid.top, grid.bottom = 0, rows - 1
            grid.x = min(grid.x, cols - 1)
            grid.y = min(grid.y, rows - 1)
            if grid.pending and grid.x < cols - 1:
                grid.pending = False
            if grid.saved is not None:
                x, y, pending, pen, origin = grid.saved
                grid.saved = (min(x, cols - 1), min(y, rows - 1), pending and x >= cols - 1, pen, origin)
        if cols > self.cols:
            self.tabs.update(range((self.cols // 8 + 1) * 8, cols, 8))
        self.tabs = {stop for stop in self.tabs if stop < cols}
        self.cols, self.rows_count = cols, rows

    # -- printing

    def _print(self, run: str) -> None:
        grid = self.grid
        cols = self.cols
        if self.charsets[self.shift] == "0":
            run = "".join(LINE_DRAWING.get(char, char) for char in run)
        if run.isascii() and not self.insert:
            # The fast path: every character one cell wide, no controls.
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
            if x > 0 and line[x] is not None and line[x][1] == 0:
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

    # -- movement and scrolling

    def _push_scrollback(self, line: list, wrapped: bool) -> None:
        self.scrollback.append(_line_runs(line))
        self.scrollback_wrapped.append(wrapped)

    def _linefeed(self) -> None:
        grid = self.grid
        if grid.y == grid.bottom:
            self._scroll_up(1)
        elif grid.y < self.rows_count - 1:
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
        cols = self.cols
        for _ in range(n):
            line = grid.lines.pop(top)
            wrapped = grid.wrapped.pop(top)
            if grid is self.main and top == 0:
                self._push_scrollback(line, wrapped)
            grid.lines.insert(grid.bottom, [None] * cols)
            grid.wrapped.insert(grid.bottom, False)

    def _scroll_down(self, n: int, top: int | None = None) -> None:
        grid = self.grid
        top = grid.top if top is None else top
        n = min(n, grid.bottom - top + 1)
        cols = self.cols
        for _ in range(n):
            grid.lines.pop(grid.bottom)
            grid.wrapped.pop(grid.bottom)
            grid.lines.insert(top, [None] * cols)
            grid.wrapped.insert(top, False)

    def _move(self, x: int | None = None, y: int | None = None, clamp_region: bool = False) -> None:
        grid = self.grid
        if x is not None:
            grid.x = max(0, min(self.cols - 1, x))
        if y is not None:
            low, high = (grid.top, grid.bottom) if clamp_region else (0, self.rows_count - 1)
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
            high = grid.bottom if grid.y <= grid.bottom else self.rows_count - 1
            y = min(high, y)
        self._move(y=y)

    def _row_to(self, y: int) -> None:
        if self.origin:
            self._move(y=self.grid.top + y, clamp_region=True)
        else:
            self._move(y=y)

    # -- C0

    def _c0(self, code: int) -> None:
        grid = self.grid
        if code == 0x0D:
            grid.x = 0
            grid.pending = False
        elif code in (0x0A, 0x0B, 0x0C):
            grid.pending = False
            self._linefeed()
        elif code == 0x08:
            grid.pending = False
            if grid.x:
                grid.x -= 1
        elif code == 0x09:
            self._tab(1)
        elif code == 0x0E:
            self.shift = 1
        elif code == 0x0F:
            self.shift = 0
        # BEL, NUL, ENQ and the rest draw nothing.

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

    # -- ESC

    def _esc(self, tok: Esc) -> None:
        grid = self.grid
        final = tok.final
        inter = tok.intermediates
        if inter:
            if inter in (b"(", b")") and final < 0x80:
                self.charsets[0 if inter == b"(" else 1] = chr(final)
            return
        if final == 0x37:  # 7 DECSC
            self._save_cursor()
        elif final == 0x38:  # 8 DECRC
            self._restore_cursor()
        elif final == 0x44:  # D IND
            grid.pending = False
            self._linefeed()
        elif final == 0x4D:  # M RI
            grid.pending = False
            self._reverse_index()
        elif final == 0x45:  # E NEL
            grid.x = 0
            grid.pending = False
            self._linefeed()
        elif final == 0x48:  # H HTS
            self.tabs.add(grid.x)
        elif final == 0x63:  # c RIS
            self._init_state()

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
        grid.y = min(grid.y, self.rows_count - 1)

    # -- CSI

    def _csi(self, tok: Csi) -> None:
        key = tok.prefix + tok.intermediates + bytes((tok.final,))
        handler = _CSI.get(key)
        if handler is None:
            return  # a private prefix never reads as its unprefixed twin
        handler(self, tok)

    @staticmethod
    def _ints(tok: Csi, default: int = 0) -> list[int]:
        params = tok.params
        if not params:
            return [default]
        out = []
        for part in params.split(b";"):
            part = part.partition(b":")[0]
            out.append(int(part) if part.isdigit() else default)
        return out

    def _n(self, tok: Csi) -> int:
        return max(1, self._ints(tok, 1)[0])

    def _cuu(self, tok):
        self._move_rows(-self._n(tok))

    def _cud(self, tok):
        self._move_rows(self._n(tok))

    def _cuf(self, tok):
        self._move(x=self.grid.x + self._n(tok))

    def _cub(self, tok):
        self._move(x=self.grid.x - self._n(tok))

    def _cnl(self, tok):
        self._move_rows(self._n(tok))
        self.grid.x = 0

    def _cpl(self, tok):
        self._move_rows(-self._n(tok))
        self.grid.x = 0

    def _cha(self, tok):
        self._move(x=self._n(tok) - 1)

    def _vpa(self, tok):
        self._row_to(self._n(tok) - 1)

    def _cup(self, tok):
        values = self._ints(tok, 1)
        row = max(1, values[0])
        col = max(1, values[1]) if len(values) > 1 else 1
        self._row_to(row - 1)
        self._move(x=col - 1)

    def _erase_cell(self):
        pen = self.grid.pen
        return None if pen[2] is None else ("", 1, (0, None, pen[2], None))

    def _erase(self, line: list, start: int, end: int) -> None:
        if start >= end:
            return
        self._cut(line, start)
        self._cut(line, end)
        line[start:end] = [self._erase_cell()] * (end - start)

    def _ed(self, tok):
        mode = self._ints(tok)[0]
        grid = self.grid
        cols, rows = self.cols, self.rows_count
        if mode == 3:
            self.scrollback.clear()
            self.scrollback_wrapped.clear()
            return  # VTE: the cursor and a pending wrap are left as they were
        if mode == 0:
            self._erase(grid.lines[grid.y], grid.x, cols)
            grid.wrapped[grid.y] = False
            for y in range(grid.y + 1, rows):
                self._erase(grid.lines[y], 0, cols)
                grid.wrapped[y] = False
        elif mode == 1:
            for y in range(grid.y):
                self._erase(grid.lines[y], 0, cols)
                grid.wrapped[y] = False
            self._erase(grid.lines[grid.y], 0, grid.x + 1)
        elif mode == 2:
            if grid is self.main:
                # VTE scrolls the rows in use off the top instead of
                # dropping them: they are in the scrollback afterwards.
                used = rows
                while used and all(cell is None for cell in grid.lines[used - 1]):
                    used -= 1
                for y in range(used):
                    self._push_scrollback(grid.lines[y], grid.wrapped[y])
            for y in range(rows):
                self._erase(grid.lines[y], 0, cols)
                grid.wrapped[y] = False
            grid.pending = False
            return  # the cursor stays where it was
        grid.pending = False

    def _el(self, tok):
        mode = self._ints(tok)[0]
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

    def _ech(self, tok):
        grid = self.grid
        self._erase(grid.lines[grid.y], grid.x, min(self.cols, grid.x + self._n(tok)))
        grid.pending = False

    def _ich(self, tok):
        grid = self.grid
        cols = self.cols
        n = min(self._n(tok), cols - grid.x)
        line = grid.lines[grid.y]
        self._cut(line, grid.x)
        self._cut(line, cols - n)
        del line[cols - n :]
        line[grid.x : grid.x] = [self._erase_cell()] * n
        grid.pending = False

    def _dch(self, tok):
        grid = self.grid
        n = min(self._n(tok), self.cols - grid.x)
        line = grid.lines[grid.y]
        self._cut(line, grid.x)
        self._cut(line, grid.x + n)
        del line[grid.x : grid.x + n]
        line.extend([self._erase_cell()] * n)
        grid.pending = False

    def _il(self, tok):
        grid = self.grid
        if grid.top <= grid.y <= grid.bottom:
            self._scroll_down(self._n(tok), top=grid.y)
            grid.x = 0
            grid.pending = False

    def _dl(self, tok):
        grid = self.grid
        if grid.top <= grid.y <= grid.bottom:
            n = min(self._n(tok), grid.bottom - grid.y + 1)
            cols = self.cols
            for _ in range(n):
                grid.lines.pop(grid.y)
                grid.wrapped.pop(grid.y)
                grid.lines.insert(grid.bottom, [None] * cols)
                grid.wrapped.insert(grid.bottom, False)
            grid.x = 0
            grid.pending = False

    def _su(self, tok):
        self._scroll_up(self._n(tok))

    def _sd(self, tok):
        self._scroll_down(self._n(tok))

    def _decstbm(self, tok):
        values = self._ints(tok, 0)
        rows = self.rows_count
        top = (values[0] or 1) - 1
        bottom = (values[1] if len(values) > 1 and values[1] else rows) - 1
        bottom = min(bottom, rows - 1)
        if top >= bottom:
            return
        self.grid.top, self.grid.bottom = top, bottom
        self._row_to(0)
        self._move(x=0)

    def _rep(self, tok):
        if self.last_char:
            self._print(self.last_char * self._n(tok))

    def _tbc(self, tok):
        mode = self._ints(tok)[0]
        if mode == 0:
            self.tabs.discard(self.grid.x)
        elif mode == 3:
            self.tabs.clear()

    def _cht(self, tok):
        self._tab(self._n(tok))

    def _cbt(self, tok):
        x = self.grid.x
        for _ in range(self._n(tok)):
            stops = [stop for stop in self.tabs if stop < x]
            x = max(stops) if stops else 0
        self._move(x=x)

    def _scosc(self, tok):
        self._save_cursor()

    def _scorc(self, tok):
        self._restore_cursor()

    def _decstr(self, tok):
        grid = self.grid
        grid.pen = DEFAULT_PEN
        grid.top, grid.bottom = 0, self.rows_count - 1
        grid.saved = None
        self.origin = self.insert = False
        self.autowrap = True
        self.charsets = ["B", "B"]
        self.shift = 0

    def _sm(self, tok, value=True):
        for mode in self._ints(tok):
            if mode == 4:
                self.insert = value

    def _rm(self, tok):
        self._sm(tok, False)

    def _decset(self, tok, value=True):
        for mode in self._ints(tok):
            if mode == 6:
                self.origin = value
                self._row_to(0)
                self._move(x=0)
            elif mode == 7:
                self.autowrap = value
            elif mode in (47, 1047, 1049):
                self._alternate(mode, value)
            elif mode == 1048:
                if value:
                    self._save_cursor()
                else:
                    self._restore_cursor()

    def _decrst(self, tok):
        self._decset(tok, False)

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
        grid.lines = [[None] * self.cols for _ in range(self.rows_count)]
        grid.wrapped = [False] * self.rows_count

    def _sgr(self, tok):
        attrs, fg, bg, ul = self.grid.pen
        params = tok.params
        parts = params.split(b";") if params else [b"0"]
        i = 0
        count = len(parts)
        while i < count:
            part = parts[i]
            i += 1
            if b":" in part:
                sub = part.split(b":")
                head = int(sub[0]) if sub[0].isdigit() else 0
                if head == 4:
                    style = int(sub[1]) if len(sub) > 1 and sub[1].isdigit() else 1
                    attrs = (attrs & ~UNDERLINE_MASK) | (min(style, 5) << UNDERLINE_SHIFT)
                elif head in (38, 48, 58):
                    colour = _colon_colour(sub)
                    if colour is not None:
                        if head == 38:
                            fg = colour
                        elif head == 48:
                            bg = colour
                        else:
                            ul = colour
                continue
            n = int(part) if part.isdigit() else 0
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
        self.grid.pen = (attrs, fg, bg, ul)

    # -- the redraw

    def snapshot(self, preamble: bytes = b"") -> bytes:
        """The bytes a fresh terminal of this size is fed to show this
        screen (the module docstring, "The redraw"). `preamble` is the
        mode tracker's ``preamble(screen=False)``."""
        rows = self.rows_count
        out: list[str] = ["\x1b[0m"]
        if self.scrollback:
            self._paint_scrollback(out)
            # Push every scrollback row above the screen: rows - 1 line
            # feeds reach the bottom and then scroll exactly one row off
            # per row written, whether the rows filled the screen or not.
            out.append("\x1b[0m\r" + "\n" * (rows - 1))
        # Tab stops first: a tab cell is written as a tab where it ends on
        # one of the model's stops, so the terminal must have them already.
        default_tabs = set(range(8, self.cols, 8))
        for stop in sorted(self.tabs ^ default_tabs):
            out.append(f"\x1b[{stop + 1}G" + ("\x1bH" if stop in self.tabs else "\x1b[g"))
        painter = _Painter(self, self.main)
        if self.on_alt:
            # The screen underneath first, as entering the alternate screen
            # saved it, then the one on show.
            painter.paint(under=True)
            out += painter.out
            out.append("\x1b[?1049h")
            top = _Painter(self, self.alt)
            top.paint()
            out += top.out
        else:
            painter.paint()
            out += painter.out
        if preamble:
            out.append(preamble.decode("latin-1"))
        # The screen's own modes, whether or not the preamble repeats them:
        # origin mode and the region home the cursor, which is placed last.
        if self.origin:
            out.append("\x1b[?6h")
        if not self.autowrap:
            out.append("\x1b[?7l")
        if self.insert:
            out.append("\x1b[4h")
        if self.charsets[0] != "B":
            out.append("\x1b(" + self.charsets[0])
        if self.charsets[1] != "B":
            out.append("\x1b)" + self.charsets[1])
        out.append(self._paint_cursor())
        return "".join(out).encode()

    def _paint_scrollback(self, out: list[str]) -> None:
        cols = self.cols
        pen = DEFAULT_PEN
        rows = list(self.scrollback)
        wrapped = list(self.scrollback_wrapped)
        for index, runs in enumerate(rows):
            col = 0
            for text, width, run_pen in runs:
                if run_pen != pen:
                    out.append(pen_sgr(run_pen))
                    pen = run_pen
                if text == "\t":
                    stop = col + width
                    if stop % 8 == 0 or stop == cols - 1:
                        out.append("\t")
                    else:
                        out.append(" " * width)
                else:
                    out.append(text)
                col += width
            following = rows[index + 1] if index + 1 < len(rows) else None
            if (
                col == cols
                and wrapped[index]
                and following
                and following[0][0][0] != "\t"
            ):
                continue  # the next row's first character wraps on its own
            out.append("\r\n")

    def _paint_cursor(self) -> str:
        grid = self.grid
        out = []
        if grid.pending:
            # The wrap is pending only after a character was written to
            # the last column: write that one last.
            lead = self.cols - 1
            cell = grid.lines[grid.y][lead]
            if cell is not None and cell[1] == 0:
                lead -= 1
                cell = grid.lines[grid.y][lead]
            text, pen = (cell[0], cell[2]) if cell is not None and cell[0] else (" ", DEFAULT_PEN)
            if self.origin:
                out.append(f"\x1b[{grid.y - grid.top + 1};{lead + 1}H")
            else:
                out.append(f"\x1b[{grid.y + 1};{lead + 1}H")
            out.append(pen_sgr(pen) + text)
        elif self.origin:
            out.append(f"\x1b[{grid.y - grid.top + 1};{grid.x + 1}H")
        else:
            out.append(f"\x1b[{grid.y + 1};{grid.x + 1}H")
        out.append(pen_sgr(grid.pen))
        return "".join(out)


def _cut_wide(line: list, cols: int) -> None:
    """After a truncation, a wide character cut in half becomes a space."""
    if cols and line and line[cols - 1] is not None and line[cols - 1][1] == 2:
        line[cols - 1] = (" ", 1, line[cols - 1][2])


def _colon_colour(sub: list[bytes]):
    kind = sub[1] if len(sub) > 1 else b""
    if kind == b"5" and len(sub) > 2 and sub[2].isdigit():
        return int(sub[2]) & 255
    if kind == b"2":
        # 38:2::r:g:b (with the colour-space slot) or 38:2:r:g:b.
        values = sub[3:6] if len(sub) >= 6 else sub[2:5]
        if len(values) == 3 and all(v.isdigit() for v in values):
            return tuple(int(v) & 255 for v in values)
    return None


_CSI = {
    b"A": Screen._cuu, b"B": Screen._cud, b"C": Screen._cuf, b"D": Screen._cub,
    b"E": Screen._cnl, b"F": Screen._cpl, b"G": Screen._cha, b"`": Screen._cha,
    b"H": Screen._cup, b"f": Screen._cup, b"d": Screen._vpa,
    b"J": Screen._ed, b"?J": Screen._ed, b"K": Screen._el, b"?K": Screen._el,
    b"X": Screen._ech, b"@": Screen._ich, b"P": Screen._dch, b"L": Screen._il,
    b"M": Screen._dl, b"S": Screen._su, b"T": Screen._sd, b"r": Screen._decstbm,
    b"m": Screen._sgr, b"b": Screen._rep, b"g": Screen._tbc, b"I": Screen._cht,
    b"Z": Screen._cbt, b"s": Screen._scosc, b"u": Screen._scorc, b"!p": Screen._decstr,
    b"h": Screen._sm, b"l": Screen._rm, b"?h": Screen._decset, b"?l": Screen._decrst,
}  # fmt: skip


class _Painter:
    """One screen of the model as bytes that address every run of cells
    absolutely. Cells nothing was written to are jumped over, so a row's
    text read ends where the original's did; a row that wrapped is left to
    wrap again where the next row lets it."""

    def __init__(self, screen: Screen, grid: Grid):
        self.screen = screen
        self.grid = grid
        self.out: list[str] = []
        self.pen = None
        self.at = None  # (x, y) of the terminal's cursor, when known

    def move(self, x: int, y: int) -> None:
        if self.at != (x, y):
            self.out.append(f"\x1b[{y + 1};{x + 1}H")
            self.at = (x, y)

    def use(self, pen) -> None:
        if pen != self.pen:
            self.out.append(pen_sgr(pen))
            self.pen = pen

    def wraps_into(self, y: int, last: int) -> bool:
        grid, cols = self.grid, self.screen.cols
        if not grid.wrapped[y] or y + 1 >= self.screen.rows_count:
            return False
        following = grid.lines[y + 1][0]
        if following is None or not following[0] or following[0] == "\t":
            return False
        if last == cols:
            return True
        # A wide character that did not fit left the last cell empty.
        return last == cols - 1 and following[1] == 2

    def row(self, y: int, skip_last: bool) -> None:
        screen, grid = self.screen, self.grid
        line = grid.lines[y]
        cols = screen.cols
        end = cols - 1 if skip_last else cols
        x = 0
        last = 0  # one past the last cell written
        while x < end:
            cell = line[x]
            if cell is None or cell[1] == 0:
                x += 1
                continue
            text, width, pen = cell
            self.move(x, y)
            self.use(pen)
            if not text:
                # Erased with a background: erased again, not written as a
                # space, so the row's text still ends before it.
                run = 1
                while x + run < end and line[x + run] == cell:
                    run += 1
                self.out.append(f"\x1b[{run}X")
                x += run
                continue
            if text == "\t":
                stops = [stop for stop in screen.tabs if stop > x]
                stop = min(stops) if stops else cols - 1
                # A tab that no longer ends on a stop can only be spaces.
                self.out.append("\t" if stop == x + width else " " * width)
            else:
                self.out.append(text)
            x += width
            last = x
            self.at = (x, y) if x < cols else None
        if skip_last:
            return
        if self.wraps_into(y, last):
            self.at = (0, y + 1)  # the next character wraps on its own

    def paint(self, under: bool = False) -> None:
        """The cells, then the scroll region and the saved cursor. With
        `under`, the screen beneath the alternate one: its cursor is what
        entering the alternate screen saved."""
        screen, grid = self.screen, self.grid
        pending = grid.pending and not under
        for y in range(screen.rows_count):
            self.row(y, skip_last=pending and y == grid.y)
        out = self.out
        if (grid.top, grid.bottom) != (0, screen.rows_count - 1):
            out.append(f"\x1b[{grid.top + 1};{grid.bottom + 1}r")
            self.at = None
        if under:
            # ``?1049h`` saves the cursor it finds: leave it where entering
            # the alternate screen saved it, with that pen, so leaving the
            # alternate screen later restores the same.
            x, y, pen = (grid.x, grid.y, grid.pen)
            if grid.saved is not None:
                x, y, _, pen, _ = grid.saved
            out.append(f"\x1b[{y + 1};{x + 1}H" + pen_sgr(pen))
            self.at, self.pen = None, pen
        elif grid.saved is not None:
            x, y, _, pen, _ = grid.saved
            out.append(f"\x1b[{y + 1};{x + 1}H" + pen_sgr(pen) + "\x1b7")
            self.at, self.pen = None, pen
