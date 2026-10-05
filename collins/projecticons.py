"""Per-project sidebar icons.

A project can ship its own sidebar icon by keeping a ``project-icon.svg``
in its root directory (the directory sessions run in). When present, the
sidebar shows it in place of the generic folder icon.

Kept GTK-free so the gates are unit-testable headless. The file itself is
the service's to read (split-service spec §3.23, PR-2.7): it serves the
bytes as `GET /api/blob?kind=icon&root=…` (`service.blobs.read_icon`, which
applies `usable_icon_bytes` before anything leaves the machine), and the
client asks for them (`remoteicons`) and applies the same gate again —
rule 5: an icon from the service is as untrusted as one from the repo.
"""

from __future__ import annotations

import re

PROJECT_ICON_FILENAME = "project-icon.svg"

# An icon rendered at 16px has no business being large; anything bigger is
# assumed to be a mistake (or not really an icon) and ignored.
MAX_ICON_BYTES = 256 * 1024

# An icon is inert artwork. Anything that could run or fetch when the SVG is
# rendered somewhere less careful than librsvg (which executes none of it) is
# refused outright: script elements, event-handler attributes, and references
# to anything outside the document. An href may point at a local #fragment
# (all inline gradients and <use> reuse need) or carry an inline
# data:image/png URI — projects only appear in the sidebar once trusted, so
# an embedded raster is the project embedding its own artwork, not a foreign
# fetch. PNG is the one embedded format allowed: image/svg+xml is XML whose
# base64 payload could smuggle the very script/handler content the literal
# checks above can't see, and the other raster codecs stay off the list
# until an icon actually needs one. CSS url() stays #fragment-only (a data:
# URI does nothing useful there). xmlns declarations carry their URLs in
# xmlns attributes, so they pass.
_SVG_ACTIVE_CONTENT = re.compile(
    rb"<\s*script"
    rb"|\bon[a-z]+\s*="
    rb"|\b(?:xlink:)?href\s*=\s*[\"'](?!#|data:image/png[;,])"
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


def usable_icon_bytes(data: bytes) -> bool:
    """The gate on-disk project-icon.svg bytes pass before any parser or
    preview sees them: plausible size (the size cap re-checks what
    the read could only stat a race ago), XML-shaped SVG text, and
    none of the active content an icon has no business carrying. Inline
    data:image/png hrefs are allowed — a hand-shipped icon may embed its own
    raster artwork. The service reads the file (`service.blobs.read_icon`)
    and the client decodes what it is sent; both apply this. Generated replies go through the stricter
    usable_generated_icon_bytes instead."""
    if not 0 < len(data) <= MAX_ICON_BYTES:
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
