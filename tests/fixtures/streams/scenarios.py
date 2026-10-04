"""The termscreen fixtures: recorded streams, synthetic streams, goldens.

Shared by `tests/test_termscreen.py` (the model against the goldens) and
`scripts/check_termscreen_parity.py` (a real VTE against the same goldens,
which also writes them). GTK-free; not a package, both import it by path.

A recorded session is `<session>.bin` with `<session>.marks.json` beside it
(the offsets its scenarios end at, as `scripts/spike_split_3_screen_model.py
record` writes them); a scenario is the stream up to its mark. The synthetic
streams are generated here from the tables below (spike 3's, F13), never
stored. The goldens are `<session>.golden.json` and `synthetic.golden.json`:
what VTE 0.84 showed for each scenario, read by the parity check, one
scenario a line so a diff shows which changed.

Recording rule (spec §3.17): a real `claude` in an isolated $HOME from a
neutral directory, so no path of the user's and no session link is in a
fixture; `grep -a '/home/' *.bin` must find nothing. See the testing skill.
"""

from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
COLS, ROWS = 120, 40
SYNTHETIC_GOLDEN = "synthetic.golden.json"


def _numbered(prefix: str, count: int) -> str:
    return "".join(f"{prefix}{i}\r\n" for i in range(1, count))


E = "\x1b"
SYNTHETIC = {
    "deferred-wrap": "A" * 120 + f"{E}[6nB\r\n" + "C" * 120 + "\r\nD" + "E" * 119 + "\x08F",
    "deferred-wrap-moves": "A" * 120 + f"{E}[1CB\r\n" + "C" * 120 + f"{E}[1DD\r\n" + "E" * 120 + f"{E}[KF",
    "wide-at-margin": "x" * 119 + "日本" + "\r\n" + "y" * 118 + "日本語",
    "wide-overwrite": f"日本語\r{E}[1CX\r\n日本語\rY\r\n日本語{E}[3D{E}[1P|\r\n日本語{E}[3D{E}[1X|",
    "combining": "café ä́ ❤️ 👩‍👩‍👧 1️⃣ 🇩🇪 👍🏽|",
    "tabs": f"a\tb\t\tc\r\n{E}[5Cx\ty\r\n" + "z" * 118 + "\tq\tr",
    "tabs-overwrite": f"a\tb\r{E}[3CX\r\nc\td{E}[2;4H{E}[1X\r\nxxxxxxxxxx\r\ty\r\n"
    f"e\tf\r{E}[1C{E}[1P\r\ng\th\r{E}[K\r\n{E}[44mi\tj{E}[0m\r\nk\t\x08\x08l",
    "erase-with-colour": f"{E}[41mred{E}[K\r\n{E}[0mplain {E}[44m{E}[5X{E}[0m|\r\n"
    f"{E}[42mabc{E}[1K{E}[0m",
    "insert-delete": f"abcdefgh\r{E}[2C{E}[3@\r\nabcdefgh\r{E}[2C{E}[3P\r\n{E}[4habc\rXY{E}[4l\r\n"
    + "w" * 120
    + f"\r{E}[2@",
    "scroll-region": _numbered("line ", 12)
    + f"{E}[3;8r{E}[8;1H"
    + "in\r\n" * 4
    + f"{E}[3;1H{E}M{E}Mup{E}[r{E}[12;1Hend",
    "insert-delete-lines": _numbered("row ", 10)
    + f"{E}[3;5H{E}[2Linserted{E}[7;5H{E}[1Mdeleted{E}[2;9r{E}[4;3H{E}[2L{E}[r",
    "scroll-past-screen": _numbered("n", 95) + "tail",
    "su-sd": _numbered("s", 20) + f"{E}[3S{E}[2Tafter",
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
    "ed-forms": _numbered("e", 10) + f"{E}[5;3H{E}[1J{E}[7;3H{E}[0Jmid",
    "nel-ind": f"a{E}Eb{E}Dc{E}Md",
    "backspace-wrap": "A" * 120 + "\x08\x08X\r\n" + "B" * 120 + "C\x08\x08\x08Y",
    "charset-line-drawing": f"{E}(0lqqk{E}(B plain {E})0\x0elqqk\x0f plain",
    "shell-wrap": "$ " + "long " * 40 + "\r\n" + "日本" * 70 + "\r\nend",
    # What capture_contents has to say about a wrap: a row that wrapped at
    # the margin, a wide character pushed to the next row by the margin,
    # blanks typed up to and across a wrap (PR-1.7's attach check found
    # VTE's capture joins the wrapped rows).
    "capture-wrapped-row": "W" * 130 + "\r\nnext",
    "capture-wide-at-wrap": "v" * 119 + "日本語" + "x\r\nnext",
    "capture-blanks-at-wrap": "b" * 117 + "   " + "  tail\r\n" + "c" * 120 + "   \r\nend",
    "erase-inside-row": f"{E}[44m{E}[5X{E}[0m{E}[8Cx\r\nab{E}[41m{E}[3X{E}[0m{E}[6Cy\r\n"
    f"{E}[42mabc{E}[1K{E}[0m{E}[10Gz",
    "decorations": f"{E}[58:2::200:100:50m{E}[4mcoloured{E}[0m {E}[58:5:196;4:3mcube{E}[0m "
    f"{E}[53mover{E}[0m {E}[5mblink{E}[0m {E}[8mhidden{E}[0m {E}[4;58;2;17;34;51msemi{E}[0m "
    f"{E}[58:2::1:2:3mno line{E}[0m",
    "ed2-into-scrollback": _numbered("kept ", 6) + f"{E}[2Jafter",
    "ed3-clears-scrollback": _numbered("gone ", 50) + f"{E}[3Jstill",
    "faint-ghost-box": f"{E}[2m❯\xa0{E}[22m{E}[2mTry \"fix the tests\"{E}[0m\r\n" + "─" * 120 + f"{E}[1;3H",
    "faint-truecolor-box": f"❯\xa0{E}[2;38;2;180;180;180mdirect{E}[0m\r\n" + "─" * 120 + f"{E}[1;3H",
    "typed-box": "❯\xa0fix the tests in foo\r\n" + "─" * 120 + f"{E}[1;23H",
    # REP never wraps: it fills to the margin and leaves the wrap pending.
    "rep-to-margin": f"a{E}[200b|",
    "rep-pending": "P" * 120 + f"{E}[bZ",
    # One scroll region for both screens (measured).
    "region-across-alt": f"{E}[?1049h{E}[3;6r{E}[?1049l{E}[H" + "".join(f"L{i}\r\n" for i in range(12)),
    "region-across-alt-back": f"{E}[3;6r{E}[?1049h{E}[H" + "".join(f"A{i}\r\n" for i in range(12)),
    # What ED 2 moves into the scrollback: the rows the buffer holds.
    "ed2-twice": f"a\r\nb{E}[2J{E}[2J",
    "ed0-then-ed2": f"a{E}[J{E}[2J",
    "el-then-ed2": f"a{E}[5;1H{E}[K{E}[2J",
    "ech-then-ed2": f"a{E}[7;1H{E}[3X{E}[2J",
    "il-then-ed2": f"a{E}[4;1H{E}[2L{E}[2J",
    "cursor-only-then-ed2": f"a{E}[20;1H{E}[2J",
    "scrolled-then-ed2": "x\n" * 45 + f"{E}[2J",
    # Grid.used across the alternate screen: what ED 2 moves afterwards.
    "alt-round-trip-then-ed2": f"m0\r\nm1{E}[?1049ha0\r\na1\r\na2{E}[?1049l{E}[?1049h{E}[2J",
    "alt-round-trip-main-ed2": f"m0\r\nm1{E}[?1049ha0\r\na1\r\na2{E}[?1049l{E}[?1049h{E}[?1049l{E}[2J",
    # Combining marks past VTE's ten are dropped.
    "combining-cap": "a" + "\u0301" * 40 + "b",
    # Parameters saturate; a colour component out of range is ignored.
    "saturated-params": f"{E}[70000;70000Hx{E}[" + "9" * 20 + f";3Hy{E}[38;5;99999mz{E}[38;2;300;1;1mw{E}[0m",
}
# The dim-tail question, asked at the cursor of an input box: what today's
# read (`vtehtml.is_dim_run` over VTE's HTML) answers for each pen.
for _name, _sgr in {
    "faint-box-2-37": "2;37",
    "faint-box-90": "90",
    "faint-box-grey-direct": "38;2;128;128;128",
    "faint-box-2-38-5-250": "2;38;5;250",
    "faint-box-2-5": "2;5",
    "faint-box-2-8": "2;8",
    "faint-box-2-90": "2;90",
    "faint-box-2-1": "2;1",
    "faint-box-gap": "2",
}.items():
    if _name == "faint-box-gap":
        _body = f"{E}[2mTry{E}[0m{E}[3C{E}[2mfix{E}[0m"  # a never-written cell inside the run
    else:
        _body = f"{E}[{_sgr}mTry \"fix\"{E}[0m"
    SYNTHETIC[_name] = f"❯\xa0{_body}\r\n" + "─" * 120 + f"{E}[1;3H"

# What VTE does with an operation that arrives while the wrap is pending:
# 120 characters, the operation, then a Z (F13's pending-wrap table).
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

# Two marks each: the stream up to the first is snapshotted, the rest fed
# on top (what a redraw has to carry into the output that follows it).
SYNTHETIC_MARKED = {
    "then-alternate": (f"main one\r\nmain two{E}[?1049h{E}[3;3Hin the alternate", f"{E}[?1049l back"),
    "then-pending": ("\r\n" + "P" * 120, "Z"),
    "then-pen": (f"{E}[1;38;2;10;200;30;48;5;17mset", " more"),
    "then-region": (_numbered("r", 12) + f"{E}[3;8r{E}[8;1H", "in\r\n" * 4 + "done"),
    "then-saved": (f"{E}[5;5H{E}[35m{E}7{E}[0m{E}[1;1Hhome", f"{E}8restored"),
    "then-insert": (f"abcdef\r{E}[4h", "XY"),
    "then-origin": (f"{E}[5;10r{E}[?6h", f"{E}[1;1Htop{E}[2;1Hnext"),
    "then-tab-stops": (f"{E}[3g{E}[1;5H{E}H{E}[1;21H{E}H\r", "a\tb\tc"),
    "then-charset": (f"{E}(0", "lqqk"),
    "then-autowrap-off": (f"{E}[?7l", "N" * 125),
    "then-scrollback": (_numbered("line ", 60), _numbered("more ", 5)),
    "then-scrollback-colour": (f"{E}[31m" + _numbered("red ", 50) + f"{E}[0m", "plain\r\n"),
}


def synthetic_scenarios() -> list[tuple[str, bytes]]:
    """Every synthetic scenario as (name, bytes), generated, in a fixed
    order. A marked session gives two: `<name>@at` and `<name>@after`."""
    out = []
    for name, text in SYNTHETIC.items():
        out.append((name, text.encode()))
    for name, op in PENDING.items():
        out.append(("pending-" + name, ("\r\n" + "P" * 120 + op + "Z").encode()))
    for name, (first, then) in SYNTHETIC_MARKED.items():
        out.append((name + "@at", first.encode()))
        out.append((name + "@after", (first + then).encode()))
    return out


def recorded_sessions(directory: str = HERE) -> list[str]:
    return sorted(
        name[: -len(".marks.json")] for name in os.listdir(directory) if name.endswith(".marks.json")
    )


def recorded_scenarios(directory: str = HERE) -> list[tuple[str, bytes]]:
    """Every recorded scenario as (`<session>.<mark>`, bytes)."""
    out = []
    for session in recorded_sessions(directory):
        with open(os.path.join(directory, session + ".bin"), "rb") as f:
            data = f.read()
        with open(os.path.join(directory, session + ".marks.json")) as f:
            marks = json.load(f)
        for mark in marks:
            out.append((f"{session}.{mark['name']}", data[: mark["offset"]]))
    return out


def golden_path(scenario: str, directory: str = HERE) -> str:
    session, dot, _ = scenario.partition(".")
    if dot and session in recorded_sessions(directory):
        return os.path.join(directory, session + ".golden.json")
    return os.path.join(directory, SYNTHETIC_GOLDEN)


def load_goldens(directory: str = HERE) -> dict[str, dict]:
    """Every golden by scenario name."""
    out: dict[str, dict] = {}
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".golden.json"):
            continue
        with open(os.path.join(directory, name)) as f:
            out.update(json.load(f))
    return out


def dump_goldens(path: str, goldens: dict[str, dict]) -> None:
    """One scenario a line: ``{`` then ``"name": {...},`` per scenario."""
    names = sorted(goldens)
    with open(path, "w") as f:
        f.write("{\n")
        for i, name in enumerate(names):
            f.write(json.dumps(name) + ": " + json.dumps(goldens[name], ensure_ascii=False))
            f.write(",\n" if i + 1 < len(names) else "\n")
        f.write("}\n")


def all_scenarios(directory: str = HERE) -> list[tuple[str, bytes]]:
    return recorded_scenarios(directory) + synthetic_scenarios()


# Scenarios with a resize between two feeds: name -> (cols, rows, before,
# (cols, rows) after the resize, after). The goldens are VTE's reads after
# the second feed, at the second size; the check resizes VTE between them.
RESIZE = {
    "tab-grow-16-to-40": (16, 4, b"x", (40, 4), b"\r\n\tA\tB\tC\tD"),
    # Grid.used after a shrink that scrolls rows off the top: three rows
    # written on ten, the cursor on the ninth, six rows left, then ED 2.
    "shrink-then-ed2": (20, 10, b"r0\r\nr1\r\nr2\x1b[9;1H", (20, 6), b"\x1b[2J"),
    "shrink-written-low-then-ed2": (20, 10, b"r0\r\nr1\r\nr2\x1b[9;1Hlow", (20, 6), b"\x1b[2J"),
}


def normalise_capture(text: str) -> str:
    """A whole-text capture as the two sides are compared: VTE's
    `write_contents_sync` writes a never-written cell as NUL and keeps
    every row its buffer holds; the model's `capture_contents` gives a
    blank as a space and drops the trailing empty rows. Both are read
    with NUL as a space, each row's trailing blanks dropped, the trailing
    empty rows dropped."""
    rows = [row.replace("\x00", " ").rstrip(" ") for row in text.split("\n")]
    while rows and not rows[-1]:
        rows.pop()
    return "\n".join(rows)


def merge_runs(cells: list) -> list:
    """Drawn cells ``[x, text, fg, bg, flags, style, underline colour]``
    merged into runs of adjacent cells drawn alike, the goldens' shape:
    ``[x, text of the run, fg, bg, flags, style, underline colour]``. A
    wide character's second half is absent from the cells, so a run's text
    may be shorter than its span."""
    out: list = []
    for cell in cells:
        x, text, *look = cell
        if out and out[-1][2:] == look and out[-1][0] + out[-1][7] == x:
            out[-1][1] += text
            out[-1][7] += 2 if _wide(text) else 1
            continue
        out.append([x, text, *look, 2 if _wide(text) else 1])
    return [run[:7] for run in out]


def _wide(text: str) -> bool:
    import unicodedata

    return any(unicodedata.east_asian_width(c) in ("W", "F") for c in text)
