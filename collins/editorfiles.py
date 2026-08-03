# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""GTK-free helpers for the editor panel: language guessing, open guards,
and directory listing.

Kept GTK-free (like gitinfo.py/projecticons.py) so this stays unit-testable
headless; editor.py and filetree.py own turning these into widgets and
GtkSource calls.
"""

from __future__ import annotations

import enum
import urllib.parse
from collections import deque
from pathlib import Path

# Skipped wherever a directory is listed or expanded: build output,
# dependency trees, and VCS internals nobody wants cluttering a "look at
# what the agent just wrote" file tree.
SKIP_DIR_NAMES = {".git", "node_modules", "__pycache__", ".venv", "target", "dist", "build"}

_BINARY_SNIFF_BYTES = 8192
_MAX_HIGHLIGHT_BYTES = 512 * 1024  # opened normally below this...
_MAX_OPEN_BYTES = 5 * 1024 * 1024  # ...refused outright above this
# Images get their own, far larger cap (screenshots of 4K monitors are
# routinely multi-MB): this only guards the image viewer against decoding
# something absurd, not against ordinary photos.
_MAX_IMAGE_BYTES = 50 * 1024 * 1024

# What the image viewer (lightbox + editor image pages) will try to display:
# the formats a stock gdk-pixbuf install decodes. Anything else keeps the
# regular open path (external app / text buffer).
IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".bmp",
    ".ico",
    ".tiff",
    ".tif",
    ".avif",
}
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


def is_image_path(path: str | Path) -> bool:
    """Whether *path* names a displayable image, by suffix. Content sniffing
    is left to the actual decode (Gdk.Texture), whose failure the viewers
    already handle — this only routes the open."""
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def image_guard(path: str | Path) -> LoadGuard:
    """`load_guard`'s sibling for the image viewers: images are binary by
    nature, so only existence, readability and (a much larger) size cap are
    checked — never BINARY."""
    p = Path(path)
    try:
        if not p.is_file():
            return LoadGuard.NOT_A_FILE
        size = p.stat().st_size
    except OSError:
        return LoadGuard.UNREADABLE
    if size > _MAX_IMAGE_BYTES:
        return LoadGuard.TOO_LARGE
    try:
        with p.open("rb") as f:
            f.read(1)
    except OSError:
        return LoadGuard.UNREADABLE
    return LoadGuard.OK


LIGHTBOX_WINDOW_FRACTION = 0.85  # the lightbox never grows past this much of the window
LIGHTBOX_MIN_W = 240  # floors, so a tiny icon doesn't produce a sliver of a
LIGHTBOX_MIN_H = 240  # dialog the button strip can't fit into
LIGHTBOX_BUTTON_STRIP = 112  # px reserved for the captioned buttons on their side
# Margin around the dialog's content: the image's drop shadow renders inside
# the dialog (whose sheet clips at its bounds), so without this inset the
# shadow would be clipped away entirely.
LIGHTBOX_SHADOW_PAD = 24
_LIGHTBOX_FALLBACK_WINDOW = (1200, 800)  # sizing when the window isn't realized yet


def lightbox_layout(
    image_w: int, image_h: int, window_w: int, window_h: int, side: str | None = None
) -> tuple[str, int, int]:
    """Which side of the lightbox image the button strip goes on, and the
    dialog's content size: ("right" | "below", width, height).

    The strip takes whichever side of the fitted image has more spare screen
    space; the image shows 1:1 when it fits inside the window fraction minus
    the strip, scaled down (aspect kept by the picture's CONTAIN fit) when it
    doesn't. Passing *side* forces the strip's side instead — re-layout after
    a window resize keeps the strip where it already is. GTK-free on purpose
    — the dialog itself (lightbox.py) can't be imported in headless tests."""
    if window_w <= 0 or window_h <= 0:
        window_w, window_h = _LIGHTBOX_FALLBACK_WINDOW
    avail_w = int(window_w * LIGHTBOX_WINDOW_FRACTION)
    avail_h = int(window_h * LIGHTBOX_WINDOW_FRACTION)
    # The spare space around the image as it would fit strip-less decides the
    # side; the image is then refitted with the strip and the shadow inset
    # taken out.
    if side is None:
        plain = min(1.0, avail_w / max(image_w, 1), avail_h / max(image_h, 1))
        side = (
            "right" if window_w - image_w * plain >= window_h - image_h * plain else "below"
        )
    pad = 2 * LIGHTBOX_SHADOW_PAD
    img_w = avail_w - pad - (LIGHTBOX_BUTTON_STRIP if side == "right" else 0)
    img_h = avail_h - pad - (LIGHTBOX_BUTTON_STRIP if side == "below" else 0)
    scale = min(1.0, img_w / max(image_w, 1), img_h / max(image_h, 1))
    width = round(image_w * scale) + pad + (LIGHTBOX_BUTTON_STRIP if side == "right" else 0)
    height = round(image_h * scale) + pad + (LIGHTBOX_BUTTON_STRIP if side == "below" else 0)
    return side, max(width, LIGHTBOX_MIN_W), max(height, LIGHTBOX_MIN_H)


def lightbox_zoom_slot(
    image_w: int,
    image_h: int,
    zoom: float,
    chrome: tuple[int, int],
    window_w: int,
    window_h: int,
) -> tuple[tuple[int, int], bool, tuple[int, int], tuple[int, int]]:
    """The lightbox's geometry at a zoom level: (display size, whether the
    button strip shows, effective chrome, image slot size).

    *chrome* is the strip's reservation as (right, below) px — exactly one
    entry is non-zero. The strip yields to the image: once the zoomed display
    outgrows the space left beside the strip on its axis, keeping it would
    only shrink the image, so it hides (chrome drops to zero) and the slot
    may use the reclaimed space. The slot is the display size capped to the
    window minus the shadow inset and whatever chrome remains — equal to the
    display (no scrolling) until the image hits the window edges. GTK-free
    on purpose, like lightbox_layout."""
    display = (round(image_w * zoom), round(image_h * zoom))
    pad = 2 * LIGHTBOX_SHADOW_PAD
    axis = 0 if chrome[0] else 1
    strip_shown = display[axis] <= max((window_w, window_h)[axis] - pad - chrome[axis], 1)
    eff_chrome = chrome if strip_shown else (0, 0)
    max_slot = (
        max(window_w - pad - eff_chrome[0], 1),
        max(window_h - pad - eff_chrome[1], 1),
    )
    slot = (min(display[0], max_slot[0]), min(display[1], max_slot[1]))
    return display, strip_shown, eff_chrome, slot


def path_from_file_uri(uri: str) -> str | None:
    """The local filesystem path a `file:` URI points at, or None when it
    isn't one (other scheme, or a remote host). Sheds any query/fragment —
    agent CLIs tack `#L10`-style line fragments onto file references."""
    parsed = urllib.parse.urlsplit(uri)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        return None
    path = urllib.parse.unquote(parsed.path)
    return path or None


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


def walk_files(
    root: str | Path, show_hidden: bool = False, cap: int = 20_000
) -> tuple[list[str], bool]:
    """Every file under *root* as project-relative POSIX paths, breadth-first
    (so shallow files land early and quick-open's ties favour them). Reuses
    `list_dir`'s skip rules — hidden files, SKIP_DIR_NAMES, irregular nodes,
    symlinks escaping *root* — and never descends into a symlinked directory
    at all, exactly like the file tree's expansion rule, so a link cycle
    can't wedge the walk. Returns `(paths, truncated)`; *truncated* is True
    when the *cap* stopped the walk early."""
    root = Path(root)
    paths: list[str] = []
    queue: deque[tuple[Path, str]] = deque([(root, "")])
    while queue:
        directory, prefix = queue.popleft()
        for name, is_dir in list_dir(directory, show_hidden, root=root):
            child = directory / name
            if is_dir:
                if not child.is_symlink():
                    queue.append((child, f"{prefix}{name}/"))
            else:
                if len(paths) >= cap:
                    return paths, True
                paths.append(f"{prefix}{name}")
    return paths, False


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
