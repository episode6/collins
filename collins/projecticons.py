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
