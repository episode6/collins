# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""GTK-free helpers for the editor panel: language guessing, open guards,
the rename/paste rules the file tree's context menus act on, and the plan
for following the session's working directory when it moves
(`plan_reroot`). The directory reads behind the tree, quick open and the
follow scope are the service's since PR-2.4 (`projectfiles.py`, served as
`fs.list` / `fs.walk` / `fs.stat` / `cwd.settle`).

Kept GTK-free (like gitinfo.py/projecticons.py) so this stays unit-testable
headless; editor.py, filetree.py and fileclipboard.py own turning these into
widgets, clipboard payloads and GtkSource calls.
"""

from __future__ import annotations

import enum
import shutil
import urllib.parse
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

# The directory reads moved to the service's side in PR-2.4; the names stay
# importable from here for what still calls them (the rename and paste
# rules, PR-2.5's) and for the follow scope's enum.
from .projectfiles import SKIP_DIR_NAMES, FollowScope, is_inside  # noqa: F401

# Highlighted below this; still opened above it (the open cap itself is
# the service's, protocol.FILE_TEXT_MAX).
_MAX_HIGHLIGHT_BYTES = 512 * 1024
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


class LoadGuard(enum.Enum):
    """Why a file may not open: `image_guard`'s answers, and the words the
    editor gives the service's `fs.read` refusals (the text guards — a
    regular file, the size cap, the NUL sniff — are the service's since
    PR-2.3, `service.files.read_file`)."""

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


class RerootAction(enum.Enum):
    """What happens to one open file when the editor re-roots."""

    FOLLOW = "follow"  # the tab moves to the new root's copy, unsaved edits intact
    RELOAD = "reload"  # the tab moves to the new root's copy as it is on disk
    LEAVE = "leave"  # the tab stays on the file it was already showing


class PaneLayout(enum.Enum):
    """Which of the editor pane's two columns are on show."""

    SPLIT = "split"  # picker and open file side by side, the usual layout
    PICKER = "picker"  # narrow: the file tree (and agent files) alone
    FILES = "files"  # narrow: the open file with its tabs alone


def pane_layout(narrow: bool, n_pages: int, picker_requested: bool) -> PaneLayout:
    """What a pane *narrow* enough for one column shows: the open file while
    there is one, unless the user asked for the picker back (the back button
    beside the tabs). A pane with nothing open has nothing but the picker to
    show, whatever was asked for last; a wide pane always shows both."""
    if not narrow:
        return PaneLayout.SPLIT
    if n_pages <= 0 or picker_requested:
        return PaneLayout.PICKER
    return PaneLayout.FILES


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


# Fence info word -> GtkSource language id, for the words PR bodies put after
# ``` (the corpus: python, json, css, c, bash, suggestion). GtkSource's ids
# are not the common names — python3, js and sh, never python/javascript/
# bash — and a `suggestion` fence is GitHub's suggested-change block, plain
# text here. Anything else is None: the widget layer asks GtkSource itself
# for the word, and falls back to plain.
_FENCE_LANGUAGES = {
    "python": "python3",
    "py": "python3",
    "python3": "python3",
    "js": "js",
    "javascript": "js",
    "ts": "js",
    "typescript": "js",
    "jsx": "js",
    "tsx": "js",
    "bash": "sh",
    "sh": "sh",
    "zsh": "sh",
    "shell": "sh",
    "console": "sh",
    "yml": "yaml",
    "yaml": "yaml",
    "json": "json",
    "css": "css",
    "html": "html",
    "c": "c",
    "cpp": "cpp",
    "c++": "cpp",
    "rust": "rust",
    "rs": "rust",
    "go": "go",
    "diff": "diff",
    "patch": "diff",
    "xml": "xml",
    "toml": "toml",
    "ini": "ini",
    "md": "markdown",
    "markdown": "markdown",
    "suggestion": None,
}


def fence_language_id(info: str) -> str | None:
    """The GtkSource language id for a fenced code block's *info* string
    (the text after the opening ```): its first word, lower-cased, through
    the alias map above. None for an empty info, a `suggestion` fence, or a
    word the map doesn't know — the caller may still try that word as a
    GtkSource id (a `kotlin` fence highlights that way) before going plain."""
    words = info.strip().split()
    if not words:
        return None
    return _FENCE_LANGUAGES.get(words[0].lower())


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


def first_line(text: str, max_chars: int = 512) -> str:
    """The first line of *text* (line ending stripped, cut at *max_chars*)
    — just enough for `guess_language_id`'s shebang sniff. The text is
    what `fs.read` answered (PR-2.3): the client reads no file itself."""
    head = text.split("\n", 1)[0][:max_chars]
    return head.rstrip("\r\n")


def is_image_path(path: str | Path) -> bool:
    """Whether *path* names a displayable image, by suffix. Content sniffing
    is left to the actual decode (Gdk.Texture), whose failure the viewers
    already handle — this only routes the open."""
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def image_guard(path: str | Path) -> LoadGuard:
    """The lightbox's guard (the text files' is the service's `fs.read`,
    the editor's image pages' `image_stat_guard` over `fs.stat`): images
    are binary by nature, so only existence, readability and (a much
    larger) size cap are checked — never BINARY. The lightbox shows the
    service's files and this device's cached blobs alike, so it stays
    until PR-2.7 moves the lightbox onto the blob GET."""
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


def gallery_step(
    entries: list[tuple[str, str]], current_key: str, step: int
) -> str | None:
    """The key of the image *step* positions from *current_key* when the
    lightbox's arrow keys walk a gallery — the attachments panel's rows.

    *entries* is every row in display order as a `(kind, key)` pair; only the
    `"image"` ones are walked, so a file row between two pictures is stepped
    straight over. The ends don't wrap: an arrow at the first/last image (or
    stepping off either end) returns None, as does a *current_key* that names
    no picture — a file row, or a record struck off the list while its
    lightbox was still up. GTK-free on purpose, like lightbox_layout, so the
    stepping invariants can be unit-tested without a display."""
    images = [key for kind, key in entries if kind == "image"]
    try:
        here = images.index(current_key)
    except ValueError:
        return None
    target = here + step
    if 0 <= target < len(images):
        return images[target]
    return None


def path_from_file_uri(uri: str) -> str | None:
    """The local filesystem path a `file:` URI points at, or None when it
    isn't one (other scheme, or a remote host). Sheds any query/fragment —
    agent CLIs tack `#L10`-style line fragments onto file references."""
    parsed = urllib.parse.urlsplit(uri)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        return None
    path = urllib.parse.unquote(parsed.path)
    return path or None


def should_highlight(size: int | None) -> bool:
    """Above ~512 KB, a file is still opened (`fs.read` allows 5 MiB) but with
    syntax highlighting switched off — GtkSource re-highlights on every
    keystroke, and that cost is only worth paying for files this size or
    smaller. *size* is the one `fs.read` answered (PR-2.3); None (not
    known) highlights."""
    return size is None or size <= _MAX_HIGHLIGHT_BYTES


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


# -- following the session's working directory -----------------------------


@dataclass(frozen=True)
class RerootEntry:
    """One open file's fate in a re-root. `target` is the counterpart the tab
    would move to, None when there is nothing to move to."""

    path: str
    target: str | None
    dirty: bool
    default: RerootAction

    @property
    def needs_asking(self) -> bool:
        """Whether only the user can settle this one: two real files, both
        with content worth keeping — the buffer's unsaved edits here, and
        whatever the new root's copy holds there."""
        return self.dirty and self.target is not None


def plan_reroot(
    old_root: str | Path,
    new_root: str | Path,
    open_paths: list[str],
    dirty_paths: set[str] | frozenset[str] = frozenset(),
    *,
    files: Collection[str],
) -> list[RerootEntry]:
    """What should become of each open file when the editor moves from
    *old_root* to *new_root*, in the order given. *files* are the
    counterparts (`reroot_counterparts`) that are files under *new_root*:
    the service's `fs.stat` answers, asked off the main loop (PR-2.4), so
    this reads no disk itself.

    A file open from inside *old_root* has a counterpart at the same
    project-relative path under *new_root* — usually the same source file on a
    different branch, the session having stepped into a worktree — and that is
    what the editor follows. A file with no counterpart there, or one that was
    never inside *old_root* to begin with, has nowhere to go and stays put:
    leaving it open costs nothing and loses nothing, and its own on-disk
    monitor goes on working from outside the tree.

    Defaults are the answer that can't destroy anything. A clean buffer takes
    the new root's copy (RELOAD) — there was nothing of the user's in it to
    lose. A dirty one stays where it is (LEAVE), because the edits were made
    against *this* file and this is where saving them belongs; following them
    across is a real choice, and the entries flagged `needs_asking` are
    exactly the ones the user gets asked about."""
    old_root, new_root = Path(old_root), Path(new_root)
    entries: list[RerootEntry] = []
    for path in open_paths:
        dirty = path in dirty_paths
        moved = renamed_path(old_root, new_root, path)
        if moved is None or moved == path or moved not in files:
            entries.append(RerootEntry(path, None, dirty, RerootAction.LEAVE))
            continue
        entries.append(
            RerootEntry(
                path,
                moved,
                dirty,
                RerootAction.LEAVE if dirty else RerootAction.RELOAD,
            )
        )
    return entries


def reroot_counterparts(old_root: str | Path, new_root: str | Path, open_paths: list[str]) -> list[str]:
    """The paths under *new_root* a re-root from *old_root* would move
    *open_paths* to: the ones whose `fs.stat` decides `plan_reroot`'s
    *files*."""
    found: list[str] = []
    for path in open_paths:
        moved = renamed_path(Path(old_root), Path(new_root), path)
        if moved is not None and moved != path:
            found.append(moved)
    return found


def image_stat_guard(kind: str, size: int | None) -> LoadGuard:
    """`image_guard`'s answer from a stat the service made (`fs.stat`'s
    kind and size, PR-2.4): the editor's image pages. Whether the bytes
    can be read is the decode's to find out."""
    if kind != "file":
        return LoadGuard.NOT_A_FILE
    if size is not None and size > _MAX_IMAGE_BYTES:
        return LoadGuard.TOO_LARGE
    return LoadGuard.OK


def _exists(path: Path) -> bool:
    """Whether *path* is taken — a broken symlink included, which `exists()`
    alone says nothing about and which `rename`/`copy` would still clobber.
    An unreadable answer counts as taken: nothing here should write over
    something it couldn't look at."""
    try:
        return path.exists() or path.is_symlink()
    except OSError:
        return True


def _copy_split(name: str) -> tuple[str, str]:
    """*name* cut into the part "(copy)" goes after and the extension it goes
    before. `Path.suffix` alone stops at the last dot, which makes
    `archive.tar.gz` into `archive.tar (copy).gz`; the `.tar` of a compressed
    tarball is part of the extension, and that pair is the one compound
    suffix worth the exception — the same one GNOME's own file manager
    makes. A leading dot is a name, not an extension: `.bashrc` splits whole,
    so a dotfile's copy stays a dotfile."""
    stem, suffix = Path(name).stem, Path(name).suffix
    if suffix and Path(stem).suffix == ".tar":
        stem, suffix = Path(stem).stem, ".tar" + suffix
    return stem, suffix


def unique_target(directory: str | Path, name: str) -> Path | None:
    """Where an entry called *name* can land in *directory* without replacing
    anything: `name` itself when it is free, then "name (copy).ext",
    "name (copy 2).ext"… None once even those are taken (a directory holding
    a hundred copies of one name is doing something else entirely).

    Never handing back an existing path is the point: both `shutil.copy2` and
    `shutil.move` overwrite what they land on without a word, and a paste is
    nobody's idea of a way to delete a file."""
    directory = Path(directory)
    stem, suffix = _copy_split(name)
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

