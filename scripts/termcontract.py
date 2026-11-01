"""The terminal contract page's tables, read in its own notation.

`docs/api/terminal.md` spells every query and reply as a row of tokens
(`CSI ?61;1;21;22;28c`, `DCS !|7E565445 ST`): a named control or a run of
literal ASCII, joined with nothing between, a placeholder in angle brackets
standing for a value the row explains. `scripts/check_termstream_answers.py
--table` reads the page with this module and feeds each row to a real VTE
and to the service's filter, so the page cannot drift from the measurement;
`tests/test_api_reference.py` reads it too, so a row that does not parse
fails the unit suite before the e2e shard runs. GTK-free, standard library
only, GPL like the rest of `scripts/` (the page it reads is MIT).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NAMED: dict[str, bytes] = {
    "ESC": b"\x1b",
    "CSI": b"\x1b[",
    "OSC": b"\x1b]",
    "DCS": b"\x1bP",
    "APC": b"\x1b_",
    "ST": b"\x1b\\",
    "BEL": b"\x07",
    "ENQ": b"\x05",
    "SP": b" ",
}

# The headings of the page's three tables, as the page spells them.
ANSWERED = "### Queries the server answers"
SILENT = "### Queries stripped without an answer"
PASSED = "### Passed through, for example"
HEADINGS = (ANSWERED, SILENT, PASSED)

# What a placeholder may stand for, as a regex over the reply's bytes. A
# placeholder a row uses that is not here fails the parse: the check would
# otherwise accept any bytes for it.
CLASSES: dict[str, bytes] = {
    "vte": rb"\d+",
    "row": rb"\d+",
    "col": rb"\d+",
    "rows": rb"\d+",
    "cols": rb"\d+",
    "height": rb"\d+",
    "width": rb"\d+",
    "n": rb"\d+",
    "s": rb"[0-4]",
    "i": rb"\d+",
    "scheme": rb"[12]",
    "rrrr": rb"[0-9a-f]{4}",
    "gggg": rb"[0-9a-f]{4}",
    "bbbb": rb"[0-9a-f]{4}",
    "sgr": rb"[0-9;:]*",
}

_PLACEHOLDER = re.compile(r"<([a-z]+)>")
_CODE_SPAN = re.compile(r"`([^`]*)`")


@dataclass(frozen=True)
class Placeholder:
    name: str


Part = bytes | Placeholder


class NotationError(ValueError):
    pass


def parse(spelling: str) -> list[Part]:
    """*spelling* as parts: literal bytes and placeholders, in order."""
    parts: list[Part] = []
    if not spelling.strip():
        raise NotationError("an empty spelling")
    for token in spelling.split():
        if token in NAMED:
            parts.append(NAMED[token])
            continue
        pos = 0
        for match in _PLACEHOLDER.finditer(token):
            if match.group(1) not in CLASSES:
                raise NotationError(f"unknown placeholder <{match.group(1)}> in {spelling!r}")
            if match.start() > pos:
                parts.append(_literal(token[pos : match.start()], spelling))
            parts.append(Placeholder(match.group(1)))
            pos = match.end()
        if pos < len(token):
            parts.append(_literal(token[pos:], spelling))
    return _joined(parts)


def _literal(text: str, spelling: str) -> bytes:
    # `>` and `<` are literal in a token (DA2 is `CSI > c`); only a
    # `<name>` is a placeholder, and `parse` has taken those out.
    try:
        data = text.encode("ascii")
    except UnicodeEncodeError as exc:
        raise NotationError(f"a non-ASCII literal in {spelling!r}") from exc
    if any(byte < 0x20 or byte == 0x7F for byte in data):
        raise NotationError(f"a control character spelled literally in {spelling!r}")
    return data


def _joined(parts: list[Part]) -> list[Part]:
    out: list[Part] = []
    for part in parts:
        if isinstance(part, bytes) and out and isinstance(out[-1], bytes):
            out[-1] = out[-1] + part
        else:
            out.append(part)
    return out


def placeholders(parts: list[Part]) -> list[str]:
    return [part.name for part in parts if isinstance(part, Placeholder)]


def render(parts: list[Part], values: dict[str, str]) -> bytes:
    """The bytes of *parts* with every placeholder given by *values*."""
    out = b""
    for part in parts:
        if isinstance(part, bytes):
            out += part
        elif part.name in values:
            out += values[part.name].encode("ascii")
        else:
            raise NotationError(f"no value for <{part.name}>")
    return out


def pattern(parts: list[Part], values: dict[str, str]) -> re.Pattern[bytes]:
    """A regex over bytes: the literals escaped, a placeholder in *values*
    rendered, any other placeholder as its class."""
    out = b""
    for part in parts:
        if isinstance(part, bytes):
            out += re.escape(part)
        elif part.name in values:
            out += re.escape(values[part.name].encode("ascii"))
        else:
            out += CLASSES[part.name]
    return re.compile(out, re.S)


@dataclass(frozen=True)
class Row:
    query: list[Part]
    reply: list[Part] | None  # None in the tables with no reply column
    cells: tuple[str, ...]


def tables(markdown: str) -> dict[str, list[Row]]:
    """The page's three tables by heading: each row's query (the first
    cell's code span) and, for the answered table, its reply (the second
    cell's)."""
    out: dict[str, list[Row]] = {}
    for heading in HEADINGS:
        if heading + "\n" not in markdown:
            raise NotationError(f"the page has no {heading!r}")
        section = markdown.split(heading + "\n", 1)[1].split("\n## ", 1)[0].split("\n### ", 1)[0]
        rows = [line for line in section.splitlines() if line.startswith("|")]
        if len(rows) < 3:
            raise NotationError(f"{heading!r} has no table")
        body = rows[2:]  # the header and its rule
        out[heading] = [_row(line, with_reply=heading == ANSWERED) for line in body]
    return out


def _cells(line: str) -> tuple[str, ...]:
    """A GFM table line's cells: split on pipes outside code spans, with
    `\\|` an escaped pipe (GFM lets a pipe into a code span only escaped;
    the renderer shows it as a pipe)."""
    cells: list[str] = []
    current = ""
    in_code = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line) and line[i + 1] == "|":
            current += "|"
            i += 2
            continue
        if ch == "`":
            in_code = not in_code
        if ch == "|" and not in_code:
            cells.append(current)
            current = ""
        else:
            current += ch
        i += 1
    cells.append(current)
    inner = cells[1:-1] if len(cells) >= 2 and not cells[0].strip() and not cells[-1].strip() else cells
    return tuple(cell.strip() for cell in inner)


def _row(line: str, *, with_reply: bool) -> Row:
    cells = _cells(line)
    if len(cells) < (3 if with_reply else 2):
        raise NotationError(f"too few cells in {line!r}")
    query = parse(_span(cells[0], line))
    reply = parse(_span(cells[1], line)) if with_reply else None
    return Row(query, reply, cells)


def _span(cell: str, line: str) -> str:
    match = _CODE_SPAN.search(cell)
    if not match:
        raise NotationError(f"no code span in a cell of {line!r}")
    return match.group(1)
