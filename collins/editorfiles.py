# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""GTK-free helpers for the editor panel: language guessing, open guards,
the clipboard payloads the file tree's Copy / Cut / Paste read and write
(`format_copied_files`, `parse_copied_files`, the `collins://` URIs of
D35), and the plan for following the session's working directory when it
moves (`plan_reroot`). The directory reads behind the tree, quick open and
the follow scope are the service's since PR-2.4, and the rename and paste
rules since PR-2.5 (`projectfiles.py`, served as `fs.list` / `fs.walk` /
`fs.stat` / `cwd.settle` / `fs.rename` / `fs.paste` / `fs.mkdir`).

Kept GTK-free (like gitinfo.py/projecticons.py) so this stays unit-testable
headless; editor.py, filetree.py and fileclipboard.py own turning these into
widgets, clipboard payloads and GtkSource calls.
"""

from __future__ import annotations

import enum
import urllib.parse
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

# The directory reads moved to the service's side in PR-2.4 and the rename
# and paste rules in PR-2.5 (`projectfiles.py`); the names stay importable
# from here for the follow scope's enum, the error enums the editor's
# messages switch on, and the pure name check a rename runs first.
from .projectfiles import (  # noqa: F401
    SKIP_DIR_NAMES,
    FollowScope,
    PasteError,
    RenameError,
    is_inside,
    rename_name_error,
)

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
class LoadGuard(enum.Enum):
    """Why a file may not open: `image_stat_guard`'s answers, and the words
    the editor gives the service's `fs.read` refusals (the text guards — a
    regular file, the size cap, the NUL sniff — are the service's since
    PR-2.3, `service.files.read_file`)."""

    OK = "ok"
    TOO_LARGE = "too_large"
    BINARY = "binary"
    NOT_A_FILE = "not_a_file"
    UNREADABLE = "unreadable"


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
    isn't one (other scheme, or a remote host, or no URI at all: a hostile
    clipboard line `urlsplit` refuses). Sheds any query/fragment — agent
    CLIs tack `#L10`-style line fragments onto file references."""
    try:
        parsed = urllib.parse.urlsplit(uri)
    except ValueError:
        return None
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
    """The image pages' guard, from a stat the service made (`fs.stat`'s
    kind and size, PR-2.4): images are binary by nature, so only existence
    and (a much larger) size cap are checked, never BINARY. Whether the
    bytes can be read is the fetch's and the decode's to find out (the
    picture is the service's blob since PR-2.7, and so is the lightbox's,
    which took the old on-disk `image_guard` with it)."""
    if kind != "file":
        return LoadGuard.NOT_A_FILE
    if size is not None and size > _MAX_IMAGE_BYTES:
        return LoadGuard.TOO_LARGE
    return LoadGuard.OK


def format_copied_files(uris: list[str], cut: bool) -> str:
    """The `x-special/gnome-copied-files` payload for *uris*: the operation on
    the first line, one URI per line after it. Every GNOME file manager reads
    this, and it is the only one of the three clipboard payloads that can say
    "cut" — `text/uri-list` carries no such flag, so a cut pasted through it
    would silently become a copy."""
    return "\n".join([("cut" if cut else "copy"), *uris])


def parse_copied_files(
    text: str, service_id: str | None = None, local: bool = True
) -> tuple[list[str], bool]:
    """`format_copied_files` read back: `(paths, cut)`. A `collins://`
    URI names a path on the service *service_id* (one of another service
    is dropped: its paths mean nothing here); a `file:` URI names a path
    on this machine, which is the service's only when *local* (D35), so
    it is dropped otherwise; any other URI is dropped. An unknown
    operation reads as a copy, which is the harmless half of the pair."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return [], False
    cut = lines[0] == "cut"
    paths: list[str] = []
    for uri in lines[1:]:
        path = path_from_collins_uri(uri, service_id)
        if path is None and local:
            path = path_from_file_uri(uri)
        if path is not None:
            paths.append(path)
    return paths, cut


# The scheme the file clipboard carries the service's paths under within
# Collins (split-service spec D35): `collins://<service id>/<path>`. A
# `file:` URI would name a path on the client's machine, which is the
# service's only for a `local` client.
COLLINS_SCHEME = "collins"


def collins_uri(service_id: str, path: str) -> str:
    """`collins://<service id>/<path>` for *path* on the service *service_id*
    (the path percent-encoded as a `file:` URI's would be; a name that is
    not UTF-8, `os.fsdecode`'s surrogates, is encoded byte for byte and
    read back the same way, never a raise)."""
    quoted_path = urllib.parse.quote(path.encode("utf-8", "surrogateescape"))
    return f"{COLLINS_SCHEME}://{urllib.parse.quote(service_id, safe='')}{quoted_path}"


def path_from_collins_uri(uri: str, service_id: str | None) -> str | None:
    """The path a `collins://` URI names on the service *service_id*, or
    None when it is no such URI, names another service, *service_id*
    is unknown (no service, no paths), or it is no URI at all (a hostile
    line `urlsplit` refuses)."""
    try:
        parsed = urllib.parse.urlsplit(uri)
    except ValueError:
        return None
    if parsed.scheme != COLLINS_SCHEME or not service_id:
        return None
    if urllib.parse.unquote(parsed.netloc) != service_id:
        return None
    path = urllib.parse.unquote(parsed.path, errors="surrogateescape")
    return path if path.startswith("/") else None

