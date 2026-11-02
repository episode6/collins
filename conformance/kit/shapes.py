# SPDX-License-Identifier: MIT
# Copyright (c) 2026 episode6, Inc.
#
# Part of the conformance kit for the Collins service API (conformance/),
# licensed MIT like the specification it tests (see conformance/LICENSE),
# unlike the rest of Collins. Standard library only.
"""Messages built from the table: a minimal valid request of any type.

The wire check needs to send a request of every family the server does not
claim, and of every request type, to see each answered (`unknown` for the
unclaimed ones, anything but silence for the rest). The table knows each
type's required fields and their bounds, so a smallest valid message is
derived here rather than written by hand per type.
"""

from __future__ import annotations

import re

from . import proto

SAMPLE_PATH = "/"
SAMPLE_URL = "https://example.invalid/x/y/pull/1"
# A pty no server has (the largest id the table allows): a sample request
# that names a pty must never reach one that exists on the server.
IMPOSSIBLE_PTY = 2**32 - 1


def _sample_string(spec, name: str) -> str:
    if spec.choices:
        return sorted(spec.choices)[0]
    low = int(spec.low or 0)
    pattern = spec.pattern
    if pattern is not None:
        return _match_pattern(pattern, max(low, 1))
    if low <= 0:
        return "" if name not in ("text", "name", "msgid") else "x"
    return "x" * low


def _match_pattern(pattern: re.Pattern, length: int) -> str:
    """A short string the pattern accepts, from the patterns the table uses."""
    source = pattern.pattern
    for candidate in ("a", "abc", "a1", "x.y", "#000000", "ab", "00", "a" * length):
        if pattern.fullmatch(candidate):
            return candidate
    if "0-9a-f" in source:
        return "00" * max(length, 1)
    return "a" * max(length, 1)


def sample(spec, name: str = ""):
    """A value *spec* accepts, as small as the bounds allow."""
    p = proto.load()
    kind = spec.kind
    if kind == p.K_STR:
        return _sample_string(spec, name)
    if kind == p.K_INT:
        if name == "pty":
            return IMPOSSIBLE_PTY
        low = spec.low if spec.low is not None else 0
        return int(max(low, 0))
    if kind == p.K_NUM:
        return float(spec.low or 0)
    if kind == p.K_BOOL:
        return False
    if kind in (p.K_PATH, p.K_HOME_PATH):
        return SAMPLE_PATH
    if kind == p.K_URL:
        return SAMPLE_URL
    if kind == p.K_LIST:
        count = int(spec.low or 0)
        return [sample(spec.item, name) for _ in range(count)] if spec.item is not None else []
    if kind == p.K_OBJ:
        return minimal_fields(spec.fields or {}, spec.one_of)
    if kind == p.K_MAP:
        count = int(spec.low or 0)
        out = {}
        for index in range(count):
            key = _match_pattern(spec.key, 1) if spec.key is not None else f"k{index}"
            out[key + str(index) if index else key] = sample(spec.item, name) if spec.item else None
        return out
    if kind == p.K_JSON_OBJECT:
        return {}
    if kind == p.K_JSON:
        return {}
    if kind == p.K_SCALAR:
        return "x"
    return None


def minimal_fields(fields, one_of=()) -> dict:
    out = {}
    for name, spec in fields.items():
        if spec.required:
            out[name] = sample(spec, name)
    if one_of and not any(name in out for name in one_of):
        first = one_of[0]
        out[first] = sample(fields[first], first)
    return out


def minimal_request(type_name: str) -> dict:
    """A smallest request of *type_name* the table validates."""
    p = proto.load()
    entry = p.TYPES[type_name]
    if entry.request is None:
        raise ValueError(f"{type_name} is not a request type")
    shape = entry.request
    message = {"t": type_name, **minimal_fields(shape.fields, shape.one_of)}
    if type_name == "spawn":
        message["kind"] = "agent"
    return message


def request_types(family: str | None = None, level: int | None = None) -> list[str]:
    """Every request type, or those of one family or core level."""
    p = proto.load()
    out = []
    for name, entry in p.TYPES.items():
        if entry.request is None:
            continue
        if family is not None and entry.family != family:
            continue
        if level is not None and (entry.family != p.FAMILY_CORE or entry.level != level):
            continue
        out.append(name)
    return out


def event_types_from_service() -> list[str]:
    p = proto.load()
    return [
        name for name, entry in p.TYPES.items() if entry.event is not None and entry.event.sender == p.SERVICE
    ]
