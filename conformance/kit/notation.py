# SPDX-License-Identifier: MIT
# Copyright (c) 2026 episode6, Inc.
#
# Part of the conformance kit for the Collins service API (conformance/),
# licensed MIT like the specification it tests (see conformance/LICENSE),
# unlike the rest of Collins. Standard library only.
"""The escape-sequence notation of docs/api/terminal.md, as a file format.

The page writes a sequence as tokens separated by spaces: the names in
`TOKENS` stand for a byte or two, any other token is a run of ASCII taken
literally and joined to its neighbours, `SP` is a space, and a `<name>`
inside a token is a placeholder the row explains. `parse` turns a row into
tokens, `render` fills the placeholders and gives the bytes, and `pattern`
gives a regular expression that matches the bytes a reply may be, with
each placeholder matched by the class `CLASSES` gives it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TOKENS = {
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

# What each placeholder may be in a reply: the decimal numbers of a report,
# hex channels of a colour, VTE's version, a DECRQM state, an SGR body.
CLASSES = {
    "vte": rb"\d{1,8}",
    "row": rb"\d{1,4}",
    "col": rb"\d{1,4}",
    "scheme": rb"[12]",
    "n": rb"\d{1,5}",
    "s": rb"[0-4]",
    "rows": rb"\d{1,4}",
    "cols": rb"\d{1,4}",
    "height": rb"\d{1,6}",
    "width": rb"\d{1,6}",
    "rrrr": rb"[0-9a-fA-F]{4}",
    "gggg": rb"[0-9a-fA-F]{4}",
    "bbbb": rb"[0-9a-fA-F]{4}",
    "i": rb"\d{1,3}",
    "sgr": rb"[0-9;:]*",
}

PLACEHOLDER = re.compile(r"<([a-z]+)>")


class NotationError(ValueError):
    pass


@dataclass(frozen=True)
class Token:
    text: str  # a name in TOKENS, or a literal run (which may hold placeholders)
    named: bool


def parse(text: str) -> list[Token]:
    text = text.strip()
    if not text:
        raise NotationError("an empty sequence")
    tokens = []
    for part in text.split(" "):
        if not part:
            continue
        if part in TOKENS:
            tokens.append(Token(part, True))
            continue
        if not part.isascii() or not part.isprintable():
            raise NotationError(f"a token that is not printable ASCII: {part!r}")
        for name in PLACEHOLDER.findall(part):
            if name not in CLASSES:
                raise NotationError(f"an unknown placeholder <{name}>")
        tokens.append(Token(part, False))
    return tokens


def placeholders(tokens: list[Token]) -> list[str]:
    out = []
    for token in tokens:
        if not token.named:
            out.extend(PLACEHOLDER.findall(token.text))
    return out


def render(tokens: list[Token], values: dict[str, str]) -> bytes:
    """The bytes, with every placeholder filled from *values*."""
    out = b""
    for token in tokens:
        if token.named:
            out += TOKENS[token.text]
            continue
        text = token.text
        for name in PLACEHOLDER.findall(text):
            if name not in values:
                raise NotationError(f"no value for <{name}>")
            text = text.replace(f"<{name}>", str(values[name]))
        out += text.encode("ascii")
    return out


def pattern(tokens: list[Token], values: dict[str, str] | None = None) -> re.Pattern:
    """A regular expression matching the bytes of *tokens*: a placeholder
    with a value in *values* must be that value, the rest its class."""
    values = values or {}
    out = b""
    for token in tokens:
        if token.named:
            out += re.escape(TOKENS[token.text])
            continue
        rest = token.text
        while rest:
            match = PLACEHOLDER.search(rest)
            if match is None:
                out += re.escape(rest.encode("ascii"))
                break
            out += re.escape(rest[: match.start()].encode("ascii"))
            name = match.group(1)
            if name in values:
                out += re.escape(str(values[name]).encode("ascii"))
            else:
                out += b"(?P<" + name.encode() + b">" + CLASSES[name] + b")"
            rest = rest[match.end() :]
    return re.compile(out, re.DOTALL)


def describe(data: bytes) -> str:
    """Bytes in the page's notation, for a message."""
    text = data.decode("latin-1")
    for name, value in (
        ("CSI", "\x1b["),
        ("OSC", "\x1b]"),
        ("DCS", "\x1bP"),
        ("APC", "\x1b_"),
        ("ST", "\x1b\\"),
    ):
        text = text.replace(value, f" {name} ")
    text = text.replace("\x1b", " ESC ").replace("\x07", " BEL ").replace("\x05", " ENQ ")
    return " ".join(text.split()) or "(nothing)"
