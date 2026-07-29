"""Per-project sidebar icons.

A project can ship its own sidebar icon by keeping a ``project-icon.svg``
in its root directory (the directory sessions run in). When present, the
sidebar shows it in place of the generic folder icon.

Kept GTK-free (like gitinfo) so discovery is unit-testable headless; the
sidebar owns turning the path into a widget.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ICON_FILENAME = "project-icon.svg"

# An icon rendered at 16px has no business being large; anything bigger is
# assumed to be a mistake (or not really an icon) and ignored.
_MAX_ICON_BYTES = 256 * 1024


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

    Beyond project_icon_path's checks, the content must look like plain
    SVG/XML text — see _looks_like_svg. Reading and vetting the bytes here
    keeps the sidebar from ever handing a raw repo file to an image loader.
    """
    path = project_icon_path(cwd)
    if path is None:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if not 0 < len(data) <= _MAX_ICON_BYTES:  # re-check: the stat was a race ago
        return None
    return data if _looks_like_svg(data) else None


def _looks_like_svg(data: bytes) -> bool:
    """Cheap gate before repo-controlled bytes reach any parser: reject
    gzip (an svgz could expand far past the size cap) and anything that
    isn't XML-shaped text with an <svg> element near the top (a crafted
    binary for some other image codec)."""
    if data[:2] == b"\x1f\x8b":
        return False
    head = data[:4096]
    if head[:3] == b"\xef\xbb\xbf":  # UTF-8 BOM
        head = head[3:]
    return head.lstrip()[:1] == b"<" and b"<svg" in head
