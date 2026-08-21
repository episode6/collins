"""Finish a hard-wrapped link off the session's transcript.

The screen stitchers in linkpatterns rebuild a reference the CLI broke across
rows from geometry alone — and for URLs geometry is all they have, so they
err towards declining (see resolve_wrapped_url). The transcript has what was
actually written, unwrapped: every assistant reply, user prompt and tool
result the screen rendered is a JSON string in the session's ``.jsonl``. So
when the geometry comes up empty, the click asks the transcript for every
link it knows whose text *contains* the clicked fragment, and keeps the ones
the screen corroborates: the whole link must appear, character for
character, in the rows around the click once the renderer's whitespace —
the newline it wrapped at and the indent it continued with — is removed.
A completion is therefore never text the user can't see; the transcript
only says where one link ends and the next token begins, which is the one
thing the screen can't.

It works for finished turns: the CLI appends a message to the transcript
when the message completes, so a link still streaming onto the screen has
no transcript line yet and the click falls back to the geometry stitchers.

Kept free of GTK/VTE imports so it stays unit-testable on CI (see
tests/conftest.py); terminal.py supplies the screen rows.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .linkpatterns import FILE_PATTERN, URL_PATTERN

# How much of the transcript's end to read on a click. The screen shows the
# most recent output, and a couple of megabytes of JSONL is hundreds of turns
# — while reading the whole of a long session's transcript on every click
# would be felt. The tail starts mid-line; that line fails to parse and is
# skipped.
TAIL_BYTES = 2 * 1024 * 1024

# Rows on each side of the click that may hold the rest of the link: the URL
# stitcher's reach, which already covers a link filling whole rows.
CONTEXT_ROWS = 4

_URL_RX = re.compile(URL_PATTERN)
_FILE_RX = re.compile(FILE_PATTERN)
_WS = re.compile(r"\s+")

# One parse per transcript version: the (size, mtime) the links came from.
_cache: dict[str, tuple[tuple[int, float], list[str]]] = {}


def transcript_links(path: str | Path) -> list[str]:
    """Every URL- or path-shaped token in the strings of the last TAIL_BYTES
    of *path*'s transcript, deduplicated, in order of first appearance.
    Empty when the file can't be read."""
    path = str(path)
    try:
        st = os.stat(path)
    except OSError:
        return []
    stamp = (st.st_size, st.st_mtime)
    cached = _cache.get(path)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    try:
        with open(path, "rb") as fh:
            if st.st_size > TAIL_BYTES:
                fh.seek(st.st_size - TAIL_BYTES)
            data = fh.read()
    except OSError:
        return []
    links = harvest_links(_strings(data))
    _cache.clear()  # one transcript's links at a time is all a click needs
    _cache[path] = (stamp, links)
    return links


def _strings(data: bytes) -> list[str]:
    """The message strings of each JSONL entry in *data*: text blocks, tool
    inputs, tool results — whatever the CLI rendered came from one of them.
    Taken from the parsed JSON, never the raw bytes: an escaped ``\\n``
    inside a JSON string is made of characters a URL may contain."""
    out: list[str] = []
    for raw in data.split(b"\n"):
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        if isinstance(entry, dict):
            message = entry.get("message")
            if isinstance(message, dict):
                _collect(message.get("content"), out)
    return out


def _collect(node: object, out: list[str]) -> None:
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for value in node.values():
            _collect(value, out)
    elif isinstance(node, list):
        for value in node:
            _collect(value, out)


def harvest_links(texts: list[str]) -> list[str]:
    """URL and path candidates in *texts*, deduplicated, first-seen order.
    Paths are candidates only: the caller checks them against the
    filesystem the way a direct click would."""
    seen: dict[str, None] = {}
    for text in texts:
        for rx in (_URL_RX, _FILE_RX):
            for m in rx.finditer(text):
                seen.setdefault(m.group(0), None)
    return list(seen)


def completions(
    fragment: str, rows: list[str], row: int, col: int, links: list[str]
) -> list[tuple[str, str]]:
    """The links in *links* that *fragment* — the token clicked at
    (*row*, *col*) of the screen *rows* — is a proper part of, and that the
    screen around the click spells out whole. Longest first, each tagged
    ``"url"`` or ``"file"``; the caller opens the first that it can.

    The corroboration is positional: the rows CONTEXT_ROWS either side of
    the click, whitespace removed, must contain the candidate at a span
    covering where the fragment sits. That a candidate merely *contains* the
    fragment is not enough — ``…/pull/3`` is the head of ``…/pull/303`` and
    ``…/pull/31`` alike, and the characters on the next row decide which.
    """
    if not fragment or not (0 <= row < len(rows)):
        return []
    row_txt = rows[row]
    start = _fragment_start(row_txt, fragment, col)
    if start is None:
        return []
    lo = max(0, row - CONTEXT_ROWS)
    before = _WS.sub("", "".join(rows[lo:row]) + row_txt[:start])
    after = _WS.sub("", row_txt[start + len(fragment) :] + "".join(rows[row + 1 : row + 1 + CONTEXT_ROWS]))
    context = before + fragment + after
    off, end = len(before), len(before) + len(fragment)
    found: list[tuple[str, str]] = []
    for link in sorted(links, key=len, reverse=True):
        if len(link) <= len(fragment) or fragment not in link:
            continue
        s = context.find(link)
        while s != -1:
            if s <= off and s + len(link) >= end:
                kind = "url" if _URL_RX.fullmatch(link) else "file"
                found.append((kind, link))
                break
            s = context.find(link, s + 1)
    return found


def _fragment_start(row_txt: str, fragment: str, col: int) -> int | None:
    """Where *fragment* sits on its row: the occurrence under *col* if one
    is, else the first — the column is the click's, and a match's exact
    span isn't something VTE reports."""
    first = None
    s = row_txt.find(fragment)
    while s != -1:
        if first is None:
            first = s
        if s <= col < s + len(fragment):
            return s
        s = row_txt.find(fragment, s + 1)
    return first
