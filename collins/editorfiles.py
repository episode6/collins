# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""GTK-free helpers for the editor panel: language guessing, open guards,
directory listing, and the rename/paste rules the file tree's context menus
act on.

Kept GTK-free (like gitinfo.py/projecticons.py) so this stays unit-testable
headless; editor.py, filetree.py and fileclipboard.py own turning these into
widgets, clipboard payloads and GtkSource calls.
"""

from __future__ import annotations

import enum
import shutil
import urllib.parse
from collections import deque
from dataclasses import dataclass
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
# How many "(copy N)" names a paste will try before giving up on finding a
# free one (see `unique_target`).
_MAX_COPY_SUFFIXES = 100
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


class RenameError(enum.Enum):
    """Why a rename asked for in the file tree can't happen. Each one gets
    its own message in editor.py — "that didn't work" says nothing about
    which of these it was."""

    EMPTY = "empty"
    NOT_A_NAME = "not_a_name"  # a path, not a name: separators, "." or ".."
    EXISTS = "exists"
    MISSING = "missing"  # what's being renamed is already gone
    OUTSIDE = "outside"


class PasteError(enum.Enum):
    """Why something on the clipboard can't be pasted where it was asked for.
    One entry per rule, for the same reason `RenameError` has them: "that
    didn't work" says nothing about which rule it broke."""

    MISSING = "missing"  # what the clipboard names is no longer on disk
    OUTSIDE = "outside"  # the destination isn't inside the project
    NOT_A_DIR = "not_a_dir"  # the destination folder is gone
    INTO_ITSELF = "into_itself"  # a folder pasted into itself or its contents
    NO_ROOM = "no_room"  # every "(copy N)" name is taken
    FAILED = "failed"  # the copy/move itself failed; `message` says why


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
        if interpreter == "env":
            # Skip env's own flags (-S, -i, ...) and VAR=val assignments to
            # reach the interpreter, e.g. `env -S FOO=bar python3`.
            rest = [p for p in parts[1:] if not p.startswith("-") and "=" not in p]
            if not rest:
                return None
            interpreter = Path(rest[0]).name
        return _SHEBANG_INTERPRETERS.get(interpreter)
    return None


def read_first_line(path: str | Path, max_bytes: int = 512) -> str:
    """The first line of *path* (line ending stripped), decoded leniently —
    just enough for `guess_language_id`'s shebang sniff. Empty string when
    the file can't be read."""
    try:
        with open(path, "rb") as fh:
            raw = fh.readline(max_bytes)
    except OSError:
        return ""
    return raw.decode("utf-8", "replace").rstrip("\r\n")


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


def lightbox_zoombar_inside(bar_h: int, slot_h: int) -> bool:
    """Whether the -/+ zoom bar floats inside the image slot: only while its
    footprint *bar_h* (the bar's height plus its floating margin) takes at
    most half of the visible image height *slot_h*. On smaller images the
    bar would cover most of the picture, so it sits below the image instead.
    GTK-free on purpose, like lightbox_layout."""
    return bar_h * 2 <= slot_h


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


def rename_target(
    root: str | Path, path: str | Path, new_name: str
) -> tuple[Path | None, RenameError | None]:
    """Where renaming *path* to *new_name* would land: `(target, None)` for a
    rename worth doing, `(None, None)` when the name is unchanged (nothing to
    do, and nothing to complain about), `(None, error)` otherwise.

    Only ever a rename *in place* — the entry keeps its directory, so this
    takes a bare name and refuses anything with a path in it. Everything
    else is checked here rather than left to `Path.rename`, whose own answer
    to renaming onto an existing file is to silently replace it."""
    path = Path(path)
    name = new_name.strip()
    if not name:
        return None, RenameError.EMPTY
    # The .name comparison catches separators (and "." on its own, whose name
    # is empty); ".." survives it, and "\0" is the one character Path carries
    # happily right up to the syscall that rejects it.
    if name in (".", "..") or "\x00" in name or Path(name).name != name:
        return None, RenameError.NOT_A_NAME
    if name == path.name:
        return None, None
    try:
        if not path.exists() and not path.is_symlink():
            return None, RenameError.MISSING
    except OSError:
        return None, RenameError.MISSING
    target = path.parent / name
    # Belt and braces behind the bare-name check above: the same rule the
    # tree and the editor apply to everything else they touch — nothing
    # outside the project.
    if not is_inside(root, target):
        return None, RenameError.OUTSIDE
    try:
        if target.exists() or target.is_symlink():
            return None, RenameError.EXISTS
    except OSError:
        return None, RenameError.EXISTS
    return target, None


def renamed_path(old: str | Path, new: str | Path, path: str | Path) -> str | None:
    """*path* rewritten for a rename of *old* to *new*, or None when the
    rename doesn't touch it. Covers the renamed entry itself and — when a
    directory was renamed — everything that was open underneath it."""
    old, new, target = Path(old), Path(new), Path(path)
    if target == old:
        return str(new)
    try:
        relative = target.relative_to(old)
    except ValueError:
        return None
    return str(new / relative)


def _exists(path: Path) -> bool:
    """Whether *path* is taken — a broken symlink included, which `exists()`
    alone says nothing about and which `rename`/`copy` would still clobber.
    An unreadable answer counts as taken: nothing here should write over
    something it couldn't look at."""
    try:
        return path.exists() or path.is_symlink()
    except OSError:
        return True


def unique_target(directory: str | Path, name: str) -> Path | None:
    """Where an entry called *name* can land in *directory* without replacing
    anything: `name` itself when it is free, then "name (copy).ext",
    "name (copy 2).ext"… None once even those are taken (a directory holding
    a hundred copies of one name is doing something else entirely).

    Never handing back an existing path is the point: both `shutil.copy2` and
    `shutil.move` overwrite what they land on without a word, and a paste is
    nobody's idea of a way to delete a file."""
    directory = Path(directory)
    stem, suffix = Path(name).stem, Path(name).suffix
    for attempt in range(_MAX_COPY_SUFFIXES + 1):
        if attempt == 0:
            candidate = name
        elif attempt == 1:
            candidate = f"{stem} (copy){suffix}"
        else:
            candidate = f"{stem} (copy {attempt}){suffix}"
        target = directory / candidate
        if not _exists(target):
            return target
    return None


def paste_target(
    root: str | Path, dest_dir: str | Path, source: str | Path, move: bool = False
) -> tuple[Path | None, PasteError | None]:
    """Where pasting *source* into *dest_dir* would land: `(target, None)` for
    a paste worth doing, `(None, None)` when there is nothing to do (a cut
    entry pasted back into the folder it came from), `(None, error)` otherwise.

    *source* is deliberately allowed to live outside the project — a copy
    taken in a file manager is exactly what paste is for — but the
    destination never is, and a folder can't be pasted into itself or into
    anything it contains, which would either fail halfway or recurse."""
    dest = Path(dest_dir)
    src = Path(source)
    if not is_inside(root, dest):
        return None, PasteError.OUTSIDE
    if not dest.is_dir():
        return None, PasteError.NOT_A_DIR
    if not _exists(src):
        return None, PasteError.MISSING
    if src.is_dir() and is_inside(src, dest):
        return None, PasteError.INTO_ITSELF
    if move and _same_dir(src.parent, dest):
        return None, None  # already where the paste would put it
    target = unique_target(dest, src.name)
    if target is None:
        return None, PasteError.NO_ROOM
    return target, None


def _same_dir(one: Path, other: Path) -> bool:
    try:
        return one.resolve() == other.resolve()
    except OSError:
        return False


@dataclass
class PasteOutcome:
    """What became of one clipboard entry. *target* is where it landed (None
    when it didn't), *error* why not, and *message* the OS's own words for a
    `FAILED` one."""

    source: Path
    target: Path | None = None
    error: PasteError | None = None
    message: str = ""


def paste_entries(
    root: str | Path, dest_dir: str | Path, sources: list[str], move: bool = False
) -> list[PasteOutcome]:
    """Paste every entry in *sources* into *dest_dir* — copying, or moving
    when *move* (a cut). One outcome per source, in order: a clipboard holding
    several files is normal (it came from a file manager), and one of them
    being gone is no reason to drop the rest.

    Symlinks are copied as symlinks rather than followed: the tree already
    refuses to show one that leaves the project, and following one here would
    quietly duplicate whatever it points at into the repo."""
    outcomes: list[PasteOutcome] = []
    for source in sources:
        src = Path(source)
        target, error = paste_target(root, dest_dir, src, move)
        if target is None:
            outcomes.append(PasteOutcome(src, None, error))
            continue
        try:
            if move:
                shutil.move(str(src), str(target))
            elif src.is_dir() and not src.is_symlink():
                shutil.copytree(src, target, symlinks=True)
            else:
                shutil.copy2(src, target, follow_symlinks=False)
        except (OSError, shutil.Error) as err:
            message = getattr(err, "strerror", None) or str(err)
            outcomes.append(PasteOutcome(src, None, PasteError.FAILED, message))
            continue
        outcomes.append(PasteOutcome(src, target))
    return outcomes


def format_copied_files(uris: list[str], cut: bool) -> str:
    """The `x-special/gnome-copied-files` payload for *uris*: the operation on
    the first line, one URI per line after it. Every GNOME file manager reads
    this, and it is the only one of the three clipboard payloads that can say
    "cut" — `text/uri-list` carries no such flag, so a cut pasted through it
    would silently become a copy."""
    return "\n".join([("cut" if cut else "copy"), *uris])


def parse_copied_files(text: str) -> tuple[list[str], bool]:
    """`format_copied_files` read back: `(paths, cut)`. Non-`file:` URIs are
    dropped — a paste can only act on something local — and an unknown
    operation reads as a copy, which is the harmless half of the pair."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return [], False
    cut = lines[0] == "cut"
    paths = [path for uri in lines[1:] if (path := path_from_file_uri(uri)) is not None]
    return paths, cut


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
