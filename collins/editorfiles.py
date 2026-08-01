# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""GTK-free helpers for the editor panel: language guessing, open guards,
and directory listing.

Kept GTK-free (like gitinfo.py/projecticons.py) so this stays unit-testable
headless; editor.py and filetree.py own turning these into widgets and
GtkSource calls.
"""

from __future__ import annotations

import enum
from pathlib import Path

# Skipped wherever a directory is listed or expanded: build output,
# dependency trees, and VCS internals nobody wants cluttering a "look at
# what the agent just wrote" file tree.
SKIP_DIR_NAMES = {".git", "node_modules", "__pycache__", ".venv", "target", "dist", "build"}

_BINARY_SNIFF_BYTES = 8192
_MAX_HIGHLIGHT_BYTES = 512 * 1024  # opened normally below this...
_MAX_OPEN_BYTES = 5 * 1024 * 1024  # ...refused outright above this
# A directory this size is either a build artifact that slipped past
# SKIP_DIR_NAMES or a mistake; either way the tree stops rather than stalling
# on it. Sorted first, so what's dropped is always the tail, alphabetically.
_MAX_DIR_ENTRIES = 5000


class LoadGuard(enum.Enum):
    OK = "ok"
    TOO_LARGE = "too_large"
    BINARY = "binary"
    NOT_A_FILE = "not_a_file"
    UNREADABLE = "unreadable"


# Suffix -> GtkSource language id, for the common cases worth a fast, GTK-free
# hint before GtkSource.LanguageManager.guess_language does the real
# (content-aware) resolution in the widget. Deliberately not exhaustive: this
# only has to beat "no hint yet" while a file is loading.
_SUFFIX_LANGUAGES = {
    ".py": "python3",
    ".pyi": "python3",
    ".js": "js",
    ".mjs": "js",
    ".cjs": "js",
    ".jsx": "js",
    ".ts": "js",
    ".tsx": "js",
    ".json": "json",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".md": "markdown",
    ".markdown": "markdown",
    ".sh": "sh",
    ".bash": "sh",
    ".zsh": "sh",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".rs": "rust",
    ".go": "go",
    ".rb": "ruby",
    ".java": "java",
    ".sql": "sql",
    ".ini": "ini",
    ".cfg": "ini",
}

_SHEBANG_INTERPRETERS = {
    "python": "python3",
    "python3": "python3",
    "bash": "sh",
    "sh": "sh",
    "zsh": "sh",
    "node": "js",
    "ruby": "ruby",
    "perl": "perl",
}


def guess_language_id(path: str | Path, first_line: str = "") -> str | None:
    """A fast hint at the GtkSource language id for *path*, from its suffix
    or (failing that) a `#!` shebang line. None when nothing matches — the
    caller falls back to GtkSource.LanguageManager.guess_language, which also
    sniffs content GtkSource ships definitions for."""
    suffix = Path(path).suffix.lower()
    if suffix in _SUFFIX_LANGUAGES:
        return _SUFFIX_LANGUAGES[suffix]
    if first_line.startswith("#!"):
        parts = first_line[2:].strip().split()
        if not parts:
            return None
        interpreter = Path(parts[0]).name
        if interpreter == "env" and len(parts) > 1:
            interpreter = Path(parts[1]).name
        return _SHEBANG_INTERPRETERS.get(interpreter)
    return None


def load_guard(path: str | Path) -> LoadGuard:
    """Whether *path* looks safe to load into the editor. Binary = a NUL byte
    in the first 8 KB. Refuses outright above ~5 MB; a caller opening
    anything past `should_highlight`'s threshold should turn highlighting
    off rather than refuse it."""
    p = Path(path)
    try:
        if not p.is_file():
            return LoadGuard.NOT_A_FILE
        size = p.stat().st_size
    except OSError:
        return LoadGuard.UNREADABLE
    if size > _MAX_OPEN_BYTES:
        return LoadGuard.TOO_LARGE
    try:
        with p.open("rb") as f:
            head = f.read(_BINARY_SNIFF_BYTES)
    except OSError:
        return LoadGuard.UNREADABLE
    if b"\x00" in head:
        return LoadGuard.BINARY
    return LoadGuard.OK


def should_highlight(path: str | Path) -> bool:
    """Above ~512 KB, a file is still opened (see `load_guard`) but with
    syntax highlighting switched off — GtkSource re-highlights on every
    keystroke, and that cost is only worth paying for files this size or
    smaller."""
    try:
        return Path(path).stat().st_size <= _MAX_HIGHLIGHT_BYTES
    except OSError:
        return True


def is_inside(root: str | Path, path: str | Path) -> bool:
    """Whether *path* resolves to somewhere inside *root* — the guard against
    a symlink walking the file tree out of the project."""
    try:
        resolved_root = Path(root).resolve()
        resolved_path = Path(path).resolve()
    except OSError:
        return False
    return resolved_path == resolved_root or resolved_root in resolved_path.parents


def list_dir(
    path: str | Path, show_hidden: bool = False, root: str | Path | None = None
) -> list[tuple[str, bool]]:
    """Sorted `(name, is_dir)` entries directly inside *path*: directories
    first, then case-insensitive by name. Skips dotfiles unless
    `show_hidden`, VCS/dependency directories (`SKIP_DIR_NAMES`), and
    anything that is neither a regular file nor a directory (FIFOs, sockets,
    devices — never worth showing, never worth opening). When *root* is
    given, a symlink resolving outside it is skipped too — file symlinks
    included, so an untrusted repo can't surface (and the editor can't write
    through) `leak.txt -> ~/.ssh/id_rsa`. Truncated at `_MAX_DIR_ENTRIES` so
    a pathological directory can't stall the tree."""
    try:
        entries = list(Path(path).iterdir())
    except OSError:
        return []
    result: list[tuple[str, bool]] = []
    for entry in entries:
        name = entry.name
        if not show_hidden and name.startswith("."):
            continue
        try:
            is_dir = entry.is_dir()
            is_file = entry.is_file()
            is_symlink = entry.is_symlink()
        except OSError:
            continue
        if not is_dir and not is_file:
            continue
        if root is not None and is_symlink and not is_inside(root, entry):
            continue
        if is_dir and name in SKIP_DIR_NAMES:
            continue
        result.append((name, is_dir))
    result.sort(key=lambda item: (not item[1], item[0].casefold()))
    return result[:_MAX_DIR_ENTRIES]
