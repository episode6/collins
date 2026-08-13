# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""GTK-free helpers for drops onto an agent terminal.

An image dragged in as raw data (from a browser, a screenshot tool, an
image viewer) has no path an @-mention could name, so a copy is written
here and the mention points at the copy. Dropped *files* are mentioned in
place — only the text built for them (mention_text) lives here, along with
how that text joins whatever is already typed (leading_space, which the
attach-file button shares).

Kept GTK-free (like editorfiles.py/gitinfo.py) so this stays unit-testable
headless; terminal.py and composer.py own the drop targets and turn Gdk
values into the paths and bytes handled here. The composer's preview strip
also leans on this file: remove_mention is how a discarded thumbnail takes
its mention with it.

The copies live under the user's cache directory rather than /tmp: the
mention only gets *read* when the user submits the prompt — maybe minutes
later, maybe after a reboot in a resumed session — and /tmp doesn't
survive that. Cache is the XDG spot for "regeneratable, fine to delete":
stale copies are pruned after PRUNE_AFTER_SECONDS on the next drop, so
the directory can't grow without bound.
"""

from __future__ import annotations

import os
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path

PRUNE_AFTER_SECONDS = 7 * 24 * 60 * 60  # a week: past any plausible submit

# Characters a terminal draws inside the cell of the character before them:
# combining marks (Mn/Me — accents, the variation selector that asks for the
# emoji form) and format controls (Cf — the zero-width joiner in a composed
# emoji). They advance no column.
_ZERO_WIDTH_CATEGORIES = frozenset({"Mn", "Me", "Cf"})

# Bounds the rename loop when a burst of drops lands in the same second;
# hitting it means something is wrong (a clock stuck at one value), and
# failing beats spinning.
_MAX_NAME_ATTEMPTS = 1000


def mention_tokens(
    paths: list[str], file_reference: Callable[[str], str | None]
) -> tuple[list[tuple[str, str]], int]:
    """The (path, reference) pair for every dropped path *file_reference*
    can name — one lookup per path, kept paired so a caller annotating
    paths individually (the composer's preview thumbnails) doesn't ask the
    provider twice. Also returns how many paths got no reference (a None,
    e.g. a control character in the name), so the caller can say some were
    dropped instead of silently thinning the mention."""
    pairs = []
    failed = 0
    for path in paths:
        reference = file_reference(path)
        if reference is None:
            failed += 1
        else:
            pairs.append((path, reference))
    return pairs, failed


def mention_text(paths: list[str], file_reference: Callable[[str], str | None]) -> tuple[str, int]:
    """The text to type for dropped *paths*: one mention token per path
    *file_reference* can name (mention_tokens), each with a trailing space
    (it terminates the CLI's mention token and leaves the cursor ready for
    the next one — or for the user's sentence)."""
    pairs, failed = mention_tokens(paths, file_reference)
    return "".join(reference + " " for _path, reference in pairs), failed


def remove_mention(text: str, reference: str) -> str | None:
    """*text* with the mention token typed for *reference* removed, or None
    when removing it isn't trivially safe: the token is gone already (edited
    away), appears more than once, or every occurrence sits inside a longer
    token (a substring match bounded by non-whitespace) — this function
    refuses to guess which characters a preview thumbnail meant.

    The removed token takes one adjacent space with it — the trailing one
    mention_text typed, or failing that a leading one — so a removed mention
    doesn't leave a double gap where it sat.
    """
    spans = []
    search = 0
    while (start := text.find(reference, search)) != -1:
        end = start + len(reference)
        opens_token = start == 0 or text[start - 1].isspace()
        ends_token = end == len(text) or text[end].isspace()
        if opens_token and ends_token:
            spans.append((start, end))
        search = start + 1
    if len(spans) != 1:
        return None
    start, end = spans[0]
    if end < len(text) and text[end] == " ":
        end += 1
    elif start > 0 and text[start - 1] == " ":
        start -= 1
    return text[:start] + text[end:]


def leading_space(text_before_cursor: str, cursor_column: int) -> str:
    """The space to type in front of a mention, or "" when none is wanted.

    A mention added to a half-written sentence has to keep its distance:
    typed straight in it glues itself onto the word the cursor sits after
    ("look at@main.py"), where the CLI reads no mention token at all.

    *text_before_cursor* is the input line from its start up to the cursor,
    as the terminal has it, and its last character is the whole test. An
    empty input box ends in the whitespace its own prompt marker is drawn
    with (Claude's ❯ and a no-break space) — and so does one showing only
    the agent's dim suggestion, whose cursor stays at the marker — while a
    sentence the user already left a space on ends in that space. None of
    those wants another one.

    *cursor_column* covers the case the text can't speak for: a cursor past
    the end of the written line, where the blank cells in between are
    whitespace the terminal doesn't bother reporting. It is a count of
    *cells*, not of characters, so the comparison goes through cell_width —
    a line of CJK reaches a far higher column than it has characters, and
    reading that as a gap left a mention glued to the last one.
    """
    if not text_before_cursor or cell_width(text_before_cursor) < cursor_column:
        return ""
    return "" if text_before_cursor[-1].isspace() else " "


def cell_width(text: str) -> int:
    """How many terminal cells *text* occupies.

    A terminal counts columns in cells, and not every character is one cell
    wide: CJK and most emoji take two, and a combining mark or a joiner
    takes none — it decorates the character before it. Enough to line a
    string up with a cursor column; the terminal's own tables are the real
    authority, and where they disagree the caller errs toward adding the
    space rather than gluing a mention on (see leading_space).
    """
    width = 0
    for char in text:
        if unicodedata.category(char) in _ZERO_WIDTH_CATEGORIES:
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def cache_directory() -> Path:
    """The app's cache directory. Honors XDG_CACHE_HOME (the same resolution
    GLib.get_user_cache_dir does) so tests and the screenshot harness
    relocate it along with the rest of the app's state. Shared with
    `remoteimages`, whose downloads live in a sibling folder."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "collins"


def default_directory() -> Path:
    """Where dropped-image copies are kept."""
    return cache_directory() / "dropped-images"


def save_png(data: bytes, directory: Path, timestamp: float | None = None) -> Path:
    """Write *data* to a fresh ``drop-YYYYMMDD-HHMMSS[-N].png`` under
    *directory*; see `save_copy`."""
    return save_copy(data, directory, "drop", ".png", timestamp)


def save_copy(
    data: bytes,
    directory: Path,
    prefix: str,
    suffix: str,
    timestamp: float | None = None,
) -> Path:
    """Write *data* to a fresh ``<prefix>-YYYYMMDD-HHMMSS[-N]<suffix>`` under
    *directory* (created if missing) and return its path.

    The name is timestamped so a directory listing reads as a history, and
    opened with 'x' so two saves in the same second (or two app instances)
    get distinct files instead of one clobbering the other. `remoteimages`
    saves its downloads through this too, under its own prefix and whatever
    suffix the fetched content type earned.
    """
    if timestamp is None:
        timestamp = time.time()
    directory.mkdir(parents=True, exist_ok=True)
    stem = time.strftime(f"{prefix}-%Y%m%d-%H%M%S", time.localtime(timestamp))
    for attempt in range(_MAX_NAME_ATTEMPTS):
        name = f"{stem}{suffix}" if attempt == 0 else f"{stem}-{attempt + 1}{suffix}"
        path = directory / name
        try:
            with open(path, "xb") as fh:
                fh.write(data)
        except FileExistsError:
            continue
        return path
    raise OSError(f"no free image name under {directory}")


def prune_stale(directory: Path, now: float | None = None) -> None:
    """Delete copies older than PRUNE_AFTER_SECONDS. Called on each drop
    rather than on startup so an unused feature costs nothing; errors are
    swallowed because pruning is best-effort housekeeping — a file that
    won't delete must not break the drop that triggered it."""
    if now is None:
        now = time.time()
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    for path in entries:
        try:
            if path.is_file() and now - path.stat().st_mtime > PRUNE_AFTER_SECONDS:
                path.unlink()
        except OSError:
            continue
