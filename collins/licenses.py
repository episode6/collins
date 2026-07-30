"""Third-party license notices, read from the markdown doc shipped in the package.

THIRD_PARTY_LICENSES.md is the single source of truth: the repo root symlinks
to it, and the About dialog renders it as the sections of its Legal page. Only
the document is edited when a dependency changes — nothing here lists names.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .chatbubbles import md_to_pango

log = logging.getLogger(__name__)

NOTICES_PATH = Path(__file__).resolve().parent / "THIRD_PARTY_LICENSES.md"

_H1_RE = re.compile(r"(?m)^#\s+(.*)$")
_H2_SPLIT_RE = re.compile(r"(?m)^##\s+")
_BULLET_START_RE = re.compile(r"^\s*[-*]\s+")


def _unwrap(block: str) -> str:
    """Join a block's soft-wrapped lines so Pango wraps to the dialog's width.

    Markdown wraps prose at the file's column limit; a Pango label would honour
    those newlines and render a ragged column. Bullets keep their own lines.
    """
    lines: list[str] = []
    for raw in block.splitlines():
        line = raw.strip()
        if not line:
            continue
        if lines and not _BULLET_START_RE.match(line):
            lines[-1] = f"{lines[-1]} {line}"
        else:
            lines.append(line)
    return "\n".join(lines)


def _to_markup(body: str) -> str:
    paragraphs = (_unwrap(block) for block in body.split("\n\n"))
    return md_to_pango("\n\n".join(p for p in paragraphs if p), links=True)


def sections(text: str) -> list[tuple[str, str]]:
    """Split the notices doc into (title, Pango markup) pairs, one per heading.

    The document's `#` title heads the preamble; every `##` heading below it
    becomes its own section — the shape AdwAboutDialog's Legal page wants.
    """
    head, *rest = _H2_SPLIT_RE.split(text)
    title_match = _H1_RE.search(head)
    result: list[tuple[str, str]] = []
    preamble = _H1_RE.sub("", head).strip()
    if preamble:
        result.append((title_match.group(1) if title_match else "Third-party licenses",
                       _to_markup(preamble)))
    for chunk in rest:
        title, _, body = chunk.partition("\n")
        body = body.strip()
        if body:
            result.append((title.strip(), _to_markup(body)))
    return result


def legal_sections() -> list[tuple[str, str]]:
    """The notices as About-dialog sections; empty if the doc can't be read."""
    try:
        return sections(NOTICES_PATH.read_text(encoding="utf-8"))
    except OSError as exc:  # a stripped-down install; the About dialog still works
        log.warning("third-party notices unavailable: %s", exc)
        return []
