"""Split-service spike 4: a snapshot of the screen model, round-tripped
through VTE.

Not product code. The spec's attach falls back on a snapshot when replaying
recorded output cannot reproduce the screen: the service serializes its
screen model to bytes, and a client's fresh VTE, fed those bytes, has to show
what a VTE fed the original output shows. This measures how close that gets.

    spike_split_4_snapshot.py DIR [-v]        (needs a display)

DIR is a recordings directory of spike 3 (`spike_split_3_screen_model.py
record DIR`, `synth DIR`), with its VTE dumps beside them (`vte DIR`): those
are the VTE fed the original bytes. For every scenario this feeds the
prototype the same bytes, serializes its screen, feeds the result to a fresh
VTE and compares the two VTEs: text row by row, the cursor, every drawn cell's
colours and attributes as `Vte.Format.HTML` reports them, one read of the
whole screen (which shows the rows that wrapped), and the grammar's reads.
Then the test attach exists for: the bytes the session wrote *after* the
scenario are fed on top of the snapshot, and the screen is compared with the
next scenario's.

Run under `.agents/capture-screenshots/scripts/with-headless-display.sh`.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import spike_split_3_screen_model as model  # noqa: E402

YES = {True: "yes", False: "NO", None: "-"}
# Pen bits, by the SGR parameter that sets each.
ATTRIBUTES = (
    (model.BOLD, "1"),
    (model.FAINT, "2"),
    (model.ITALIC, "3"),
    (model.BLINK, "5"),
    (model.INVERSE, "7"),
    (model.CONCEAL, "8"),
    (model.STRIKE, "9"),
    (model.OVERLINE, "53"),
)


def colour(value, base: int) -> list[str]:
    """SGR parameters for a colour; `base` is 30, 40 or 58."""
    if value is None:
        return []
    if isinstance(value, tuple):
        if base == 58:
            return [f"58:2::{value[0]}:{value[1]}:{value[2]}"]
        return [str(base + 8), "2", *(str(channel) for channel in value)]
    if base == 58:
        return [f"58:5:{value}"]
    if value < 8:
        return [str(base + value)]
    if value < 16:
        return [str(base + 60 + value - 8)]
    return [str(base + 8), "5", str(value)]


def sgr(pen) -> str:
    """A pen from nothing: reset, then everything it holds."""
    attrs, fg, bg, ul = pen
    parts = ["0"]
    parts += [code for bit, code in ATTRIBUTES if attrs & bit]
    style = (attrs & model.UNDERLINE_MASK) >> model.UNDERLINE_SHIFT
    if style:
        parts.append(f"4:{style}")
    parts += colour(fg, 30) + colour(bg, 40) + colour(ul, 58)
    return "\x1b[" + ";".join(parts) + "m"


class Painter:
    """One screen of the model, as bytes that address every run of cells
    absolutely. Cells nothing was written to are jumped over, so a row's
    text read ends where the original's did; a row that wrapped is left to
    wrap again, so the terminal knows it did."""

    def __init__(self, screen, grid):
        self.screen = screen
        self.grid = grid
        self.out: list[str] = []
        self.pen = None
        self.at = None  # (x, y) of the terminal's cursor, when known
        self.lost_wraps = 0
        self.lost_tabs = 0

    def move(self, x: int, y: int) -> None:
        if self.at != (x, y):
            self.out.append(f"\x1b[{y + 1};{x + 1}H")
            self.at = (x, y)

    def use(self, pen) -> None:
        if pen != self.pen:
            self.out.append(sgr(pen))
            self.pen = pen

    def wraps_into(self, y: int, line: list, last: int) -> bool:
        """Whether row y can be made to wrap into the next by writing it:
        it did wrap, it is written up to where a wrap happens, and the next
        row starts with something to write."""
        grid, cols = self.grid, self.screen.cols
        if not grid.wrapped[y] or y + 1 >= self.screen.rows:
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
                if stop == x + width:
                    self.out.append("\t")
                else:
                    # A tab that no longer ends on a stop can only be spaces.
                    self.out.append(" " * width)
                    self.lost_tabs += 1
            else:
                self.out.append(text)
            x += width
            last = x
            self.at = (x, y) if x < cols else None
        if skip_last:
            return
        if self.wraps_into(y, line, last):
            # The next character wraps on its own and marks the row.
            self.at = (0, y + 1)
        elif grid.wrapped[y]:
            self.lost_wraps += 1

    def paint(self) -> None:
        """The cells, then everything the screen's cursor carries."""
        screen, grid = self.screen, self.grid
        for y in range(screen.rows):
            self.row(y, skip_last=grid.pending and y == grid.y)
        out = self.out
        if (grid.top, grid.bottom) != (0, screen.rows - 1):
            out.append(f"\x1b[{grid.top + 1};{grid.bottom + 1}r")
            self.at = None
        if grid.saved is not None:
            x, y, _, pen, _ = grid.saved
            out.append(f"\x1b[{y + 1};{x + 1}H" + sgr(pen) + "\x1b7")
            self.at, self.pen = None, pen
        if grid.pending:
            # The wrap is pending only after a character was written to
            # the last column: write that one last.
            lead = screen.cols - 1
            cell = grid.lines[grid.y][lead]
            if cell is not None and cell[1] == 0:
                lead -= 1
                cell = grid.lines[grid.y][lead]
            text, pen = (cell[0], cell[2]) if cell is not None and cell[0] else (" ", model.DEFAULT_PEN)
            out.append(f"\x1b[{grid.y + 1};{lead + 1}H" + sgr(pen) + text)
        else:
            out.append(f"\x1b[{grid.y + 1};{grid.x + 1}H")
        out.append(sgr(grid.pen))
        self.pen = grid.pen
        self.at = None


def snapshot(screen, history: bool = False) -> tuple[bytes, dict]:
    """The screen as bytes a fresh terminal of the same size can be fed, and
    what could not be carried.

    With `history`, the main screen's scrollback goes first, as plain text:
    the model keeps no pens for it."""
    out: list[str] = ["\x1b[0m"]
    if history and screen.scrollback:
        for row in screen.scrollback:
            out.append(row + "\r\n")
        # Push all of it off the screen before the screen is painted.
        out.append(f"\x1b[{screen.rows};1H" + "\n" * (screen.rows - 1))
    out.append("\x1b[H\x1b[2J")
    for stop in sorted(screen.tabs ^ set(range(8, screen.cols, 8))):
        out.append(f"\x1b[1;{stop + 1}H" + ("\x1bH" if stop in screen.tabs else "\x1b[g"))
    lost = Counter()
    main = Painter(screen, screen.main)
    if screen.on_alt:
        # The screen underneath first, left the way entering the alternate
        # screen saved it, then the one on show.
        under = screen.main
        saved = under.saved
        if saved is not None:
            under_x, under_y, under_pending, under_pen, _ = saved
        else:
            under_x, under_y, under_pending, under_pen = under.x, under.y, under.pending, under.pen
        restore = (under.x, under.y, under.pending, under.pen, under.saved)
        under.x, under.y, under.pending, under.pen, under.saved = (
            under_x, under_y, under_pending, under_pen, None,
        )  # fmt: skip
        main.paint()
        under.x, under.y, under.pending, under.pen, under.saved = restore
        out += main.out
        out.append("\x1b[?1049h")
        top = Painter(screen, screen.alt)
        top.paint()
        out += top.out
        lost["wraps"] = main.lost_wraps + top.lost_wraps
        lost["tabs"] = main.lost_tabs + top.lost_tabs
    else:
        main.paint()
        out += main.out
        lost["wraps"], lost["tabs"] = main.lost_wraps, main.lost_tabs
    grid = screen.grid
    if screen.origin:
        row = grid.y - grid.top
        out.append("\x1b[?6h" + ("" if grid.pending else f"\x1b[{row + 1};{grid.x + 1}H"))
    if screen.insert:
        out.append("\x1b[4h")
    if not screen.autowrap:
        out.append("\x1b[?7l")
    if not screen.cursor_visible:
        out.append("\x1b[?25l")
    if screen.charsets[0] != "B":
        out.append("\x1b(" + screen.charsets[0])
    if screen.charsets[1] != "B":
        out.append("\x1b)" + screen.charsets[1])
    if screen.shift:
        out.append("\x0e")
    return "".join(out).encode(), dict(lost)


def differences(original: dict, copy: dict) -> dict:
    rows = [y for y in range(len(original["row_text"])) if original["row_text"][y] != copy["row_text"][y]]
    cells: Counter = Counter()
    examples = []
    for y, drawn in enumerate(original["cells"]):
        theirs = {cell[0]: cell for cell in drawn}
        mine = {cell[0]: cell for cell in copy["cells"][y]}
        for x in sorted(set(theirs) | set(mine)):
            a, b = theirs.get(x), mine.get(x)
            if a == b:
                continue
            if a is None or b is None or a[1] != b[1]:
                kind = "text"
            elif a[2] != b[2]:
                kind = "foreground"
            elif a[3] != b[3]:
                kind = "background"
            elif a[4] != b[4]:
                kind = "attributes"
            elif a[5] != b[5]:
                kind = "underline style"
            else:
                kind = "underline colour"
            cells[kind] += 1
            if len(examples) < 4:
                examples.append(f"{kind} at ({x},{y}): original {a!r}, snapshot {b!r}")
    grammar = all(original[key] == copy[key] for key in ("takes_prompt", "entered", "tail_is_dim"))
    wraps = original["screen_text"].rstrip("\n") == copy["screen_text"].rstrip("\n")
    return {
        "rows": rows,
        "cursor": original["cursor"] == copy["cursor"],
        "cells": cells,
        "examples": examples,
        "grammar": grammar,
        "wraps": wraps,
        "exact": not rows and not cells and original["cursor"] == copy["cursor"] and grammar and wraps,
    }


def explain(label: str, diff: dict, original: dict, copy: dict) -> None:
    for y in diff["rows"][:4]:
        print(f"      {label}row {y}: original {original['row_text'][y]!r}"[:200])
        print(f"      {label}row {y}: snapshot {copy['row_text'][y]!r}"[:200])
    for example in diff["examples"]:
        print(f"      {label}{example}"[:200])
    if not diff["cursor"]:
        print(f"      {label}cursor: original {original['cursor']}, snapshot {copy['cursor']}")
    if not diff["wraps"] and not diff["rows"]:
        print(f"      {label}the rows read the same one by one, but not the rows that wrapped")


def unwrapped(text: str) -> str:
    return "".join(text.split("\n"))


def main(argv: list[str]) -> int:
    args = [arg for arg in argv[1:] if not arg.startswith("-")]
    verbose = "-v" in argv or "--verbose" in argv
    if not args:
        print(__doc__)
        return 0
    directory = args[0]
    if not os.path.isdir(directory):
        print(f"{directory} is not a directory; nothing to do")
        return 0
    if not model.have_display():
        print("no display (run under with-headless-display.sh); nothing measured")
        return 0
    scenarios = model.scenarios(directory)
    originals = {}
    for name, _ in scenarios:
        path = os.path.join(directory, name + ".vte.json")
        if os.path.exists(path):
            with open(path) as f:
                originals[name] = json.load(f)
    if not originals:
        print(f"no VTE dumps in {directory}: run spike 3's `record` or `synth`, then its `vte`")
        return 0

    jobs = []
    sizes = {}
    hidden = {}
    lost: Counter = Counter()
    seconds = 0.0
    for index, (name, data) in enumerate(scenarios):
        if name not in originals:
            continue
        screen = model.Screen()
        model.feed_chunked(screen, data)
        import time

        started = time.perf_counter()
        snap, could_not = snapshot(screen)
        seconds += time.perf_counter() - started
        lost.update(could_not)
        sizes[name] = (len(data), len(snap))
        hidden[name] = model.unseen(screen)
        jobs.append((name + "|snapshot", snap))
        if not screen.on_alt and screen.scrollback:
            jobs.append((name + "|history", snapshot(screen, history=True)[0]))
        following = scenarios[index + 1] if index + 1 < len(scenarios) else None
        if following and following[0].rpartition(".")[0] == name.rpartition(".")[0]:
            if following[0] in originals:
                jobs.append((name + "|then|" + following[0], snap + following[1][len(data) :]))

    results = {}
    model.run_vte(jobs, lambda name, dump, commits: results.__setitem__(name, dump))

    print(
        f"{'scenario':<38} {'stream':>6} {'snap':>5} {'rows':>4} {'cursor':>6} {'cells':>5} "
        f"{'grammar':>7} {'wraps':>5} {'history':>7} {'unseen':>6}  | then {'rows':>4} {'cursor':>6} "
        f"{'cells':>5} {'grammar':>7} {'wraps':>5}"
    )
    totals: Counter = Counter()
    kinds: Counter = Counter()
    unseen: Counter = Counter()
    for name, _ in scenarios:
        copy = results.get(name + "|snapshot")
        if copy is None:
            continue
        diff = differences(originals[name], copy)
        history = None
        if name + "|history" in results:
            with_history = results[name + "|history"]
            history = (
                unwrapped(with_history["history"]) == unwrapped(originals[name]["history"])
                and not differences(originals[name], with_history)["rows"]
            )
            totals["history"] += 1
            totals["history exact"] += history
        then = [key for key in results if key.startswith(name + "|then|")]
        after = None
        tail = "-"
        if then:
            following = then[0].rpartition("|")[2]
            after = differences(originals[following], results[then[0]])
            tail = (
                f"{len(after['rows']):>4} {YES[after['cursor']]:>6} {sum(after['cells'].values()):>5} "
                f"{YES[after['grammar']]:>7} {YES[after['wraps']]:>5}"
            )
            totals["then"] += 1
            totals["then exact"] += after["exact"]
        stream, size = sizes[name]
        print(
            f"{name:<38} {stream:>6} {size:>5} {len(diff['rows']):>4} {YES[diff['cursor']]:>6} "
            f"{sum(diff['cells'].values()):>5} {YES[diff['grammar']]:>7} {YES[diff['wraps']]:>5} "
            f"{YES[history]:>7} {sum(hidden[name].values()):>6}  | then {tail}"
        )
        totals["scenarios"] += 1
        totals["exact"] += diff["exact"]
        totals["exact, with cells unseen"] += diff["exact"] and bool(hidden[name])
        unseen.update(hidden[name])
        kinds.update(diff["cells"])
        for what, count in hidden[name].items():
            print(f"      not evidence for {count} cells {what}: VTE's reads cannot show them")
        if verbose or not diff["exact"]:
            explain("", diff, originals[name], copy)
        if after is not None and (verbose or not after["exact"]):
            explain("then, ", after, originals[then[0].rpartition("|")[2]], results[then[0]])
    print(
        f"\n{totals['exact']} of {totals['scenarios']} snapshots give a VTE that reads exactly as the "
        "original's: text, cursor, every drawn cell, wrapped rows, the grammar's reads."
    )
    if totals["exact, with cells unseen"]:
        print(
            f"{totals['exact, with cells unseen']} of those hold cells VTE's reads cannot show, and are "
            "no evidence that a snapshot carries them:\n  "
            + "\n  ".join(f"{count} cells {what}" for what, count in unseen.items())
        )
    print(
        f"{totals['then exact']} of {totals['then']} still do after the session's next bytes are fed "
        "on top of the snapshot."
    )
    if totals["history"]:
        print(
            f"{totals['history exact']} of {totals['history']} snapshots with the scrollback's text "
            "ahead of them leave the same text in VTE's scrollback."
        )
    if kinds:
        print("cells that differ, by what differs:", dict(kinds))
    print("what the serializer knew it could not carry:", dict(lost))
    print(f"serializing {totals['scenarios']} screens took {seconds * 1000:.1f} ms")
    print(
        "\nstream, snap: bytes of the original output and of the snapshot. rows, cells: differing.\n"
        "grammar: takes_prompt, entered_prompt and the dim tail, read off the snapshot's VTE.\n"
        "wraps: one read of the whole screen, in which rows that wrapped run together.\n"
        "unseen: cells of the prototype's screen holding what VTE's reads cannot show (erased\n"
        "with a background past a row's last written cell, concealed, an underline colour with\n"
        "no underline). Also not measured:\n"
        "hyperlinks (OSC 8; the prototype keeps none), the modes the stream filter's tracker owns."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
