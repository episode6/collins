"""Per-project sidebar icons.

A project can ship its own sidebar icon by keeping a ``project-icon.svg``
in its root directory (the directory sessions run in). When present, the
sidebar shows it in place of the generic folder icon.

Kept GTK-free (like gitinfo) so discovery is unit-testable headless; the
sidebar owns turning the path into a widget.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ICON_FILENAME = "project-icon.svg"

# An icon rendered at 16px has no business being large; anything bigger is
# assumed to be a mistake (or not really an icon) and ignored.
_MAX_ICON_BYTES = 256 * 1024

# An icon is inert artwork. Anything that could run or fetch when the SVG is
# rendered somewhere less careful than librsvg (which executes none of it) is
# refused outright: script elements, event-handler attributes, and references
# to anything outside the document. An href may point at a local #fragment
# (all inline gradients and <use> reuse need) or carry an inline data:image/*
# URI — projects only appear in the sidebar once trusted, so an embedded
# raster is the project embedding its own artwork, not a foreign fetch. CSS
# url() stays #fragment-only (a data: URI does nothing useful there). xmlns
# declarations carry their URLs in xmlns attributes, so they pass.
_SVG_ACTIVE_CONTENT = re.compile(
    rb"<\s*script"
    rb"|\bon[a-z]+\s*="
    rb"|\b(?:xlink:)?href\s*=\s*[\"'](?!#|data:image/)"
    rb"|\burl\s*\(\s*[\"']?\s*(?!#)"
    rb"|@import\b",
    re.IGNORECASE,
)

# Generated icons are held to the original, stricter rule: pure vector art,
# no data: URIs of any kind. The model designs from shapes and paths — an
# embedded raster in a reply is never intentional artwork, and keeping the
# generator's output free of opaque blobs keeps its previews reviewable.
_SVG_DATA_HREF = re.compile(
    rb"\b(?:xlink:)?href\s*=\s*[\"']data:",
    re.IGNORECASE,
)


def project_icon_path(cwd: str | Path | None) -> Path | None:
    """Path to the project's custom sidebar icon, or None if it has none.

    None when *cwd* is empty or missing, when no ``project-icon.svg`` exists
    in it, or when the file is empty or implausibly large.
    """
    if not cwd:
        return None
    candidate = Path(cwd) / PROJECT_ICON_FILENAME
    try:
        if not candidate.is_file():
            return None
        size = candidate.stat().st_size
    except OSError:
        return None
    if not 0 < size <= _MAX_ICON_BYTES:
        return None
    return candidate


def project_icon_data(cwd: str | Path | None) -> bytes | None:
    """The raw bytes of the project's icon, if it ships a plausible one.

    Beyond project_icon_path's checks, the content must pass
    usable_icon_bytes. Reading and vetting the bytes here keeps the sidebar
    from ever handing a raw repo file to an image loader.
    """
    path = project_icon_path(cwd)
    if path is None:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data if usable_icon_bytes(data) else None


def usable_icon_bytes(data: bytes) -> bool:
    """The gate on-disk project-icon.svg bytes pass before any parser or
    preview sees them: plausible size (the size cap re-checks what
    project_icon_path could only stat a race ago), XML-shaped SVG text, and
    none of the active content an icon has no business carrying. Inline
    data:image/* hrefs are allowed — a hand-shipped icon may embed its own
    raster artwork. Generated replies go through the stricter
    usable_generated_icon_bytes instead."""
    if not 0 < len(data) <= _MAX_ICON_BYTES:
        return False
    return _looks_like_svg(data) and not _SVG_ACTIVE_CONTENT.search(data)


def usable_generated_icon_bytes(data: bytes) -> bool:
    """usable_icon_bytes, plus the generator-only rule: no data: hrefs at
    all. This is the gate icongen.extract_svg applies to model replies, so
    the design brief's "pure vector, no embedded images" requirement is
    enforced rather than trusted to a model reading untrusted repo text."""
    return usable_icon_bytes(data) and not _SVG_DATA_HREF.search(data)


def _looks_like_svg(data: bytes) -> bool:
    """Cheap shape gate before repo-controlled bytes reach any parser: reject
    gzip (an svgz could expand far past the size cap) and anything that
    isn't XML-shaped text with an <svg> element near the top (a crafted
    binary for some other image codec)."""
    if data[:2] == b"\x1f\x8b":
        return False
    head = data[:4096]
    if head[:3] == b"\xef\xbb\xbf":  # UTF-8 BOM
        head = head[3:]
    return head.lstrip()[:1] == b"<" and b"<svg" in head
