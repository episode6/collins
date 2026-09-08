# New in the ghackett fork of agent-session-manager (GPL-3.0).
# The stream parser grew out of the former collins-git extension's patch.ts,
# whose single-file parser was itself adapted from muzomer/hunk-commit (MIT,
# © 2026 hunk-jj-stage contributors); see collins/THIRD_PARTY_LICENSES.md.

"""The native diff panel's model: a parsed `git diff` / `git show` stream
and the arithmetic the view draws it with.

`parse` turns the whole multi-file stream into Files of Hunks of Lines,
reading every header form git writes (modes, `new file` / `deleted file`,
`similarity index` + `rename from` / `rename to`, `copy from` / `copy to`,
`Binary files … differ`, `GIT binary patch`, `\\ No newline at end of file`,
c-quoted paths). `parse_numstat` reads the `--numstat -z` pre-pass and
`too_large` applies the size caps (TOO_LARGE_LINES changed lines or
TOO_LARGE_BYTES of patch) so an oversized file becomes a placeholder
instead of twenty thousand widgets. `gaps` names the unchanged stretches
around each hunk by position (`before:<i>` / `trailing:<i>`),
`split_rows` pairs deletions with additions row by row for the split layout,
`word_emphasis` marks the words that differ inside
a paired deletion/addition (difflib over word tokens, ratio ≥ 0.5),
`palette` blends a style scheme's diff colours into its text background
(0.18 for a row, 0.40 for the emphasis), `locate` finds the hunk and line a
(path, side, line) address names, and `stable_key` is what a reload matches
old widgets to new hunks by.

Nothing here imports GTK or runs git: gitops runs git and hands the text
here; diffview draws what comes back (tests/test_diffmodel.py). Everything
that arrives is foreign content — a diff of a repository the agent edits —
and is bounded before a widget sees it: the stream is cut at
MAX_PATCH_CHARS, never more than MAX_FILES files come back, a path over
MAX_PATH_CHARS drops its file, and a malformed hunk is dropped rather than
guessed at. The view then puts every string through `set_text` /
`Gtk.TextBuffer.set_text`, never Pango markup.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# The large-file caps: a file whose patch changes more lines than this, or
# weighs more than this, is a placeholder with no hunks (`too_large`).
TOO_LARGE_LINES = 20_000
TOO_LARGE_BYTES = 1_048_576
# Bounds on the stream itself. `git show` of a squash that touched every
# file in a large repository is still one load; past these the tail is
# dropped, not guessed at. The stream arrives decoded, so its cap counts
# characters (a byte at least each); a stanza's own weight is measured on
# its UTF-8 bytes (TOO_LARGE_BYTES).
MAX_FILES = 2000
MAX_PATCH_CHARS = 64 * 1024 * 1024
# A path longer than this drops its file (gitloads.MAX_PATH_CHARS's value,
# spelled here so this module depends on nothing of the page's).
MAX_PATH_CHARS = 512
# The word-emphasis pass is quadratic in the worst case; a hunk this long,
# or a line this wide, goes without (the row colours still say what moved).
MAX_EMPHASIS_LINES = 2000
MAX_EMPHASIS_CHARS = 2000
# The similarity below which two paired lines are "different lines", not
# "one line edited", and get no word emphasis.
EMPHASIS_MIN_RATIO = 0.5
# The blend factors (see `palette`): how far the text background is pushed
# toward the sign colour for a changed row and for the words inside it.
ROW_BLEND = 0.18
EMPHASIS_BLEND = 0.40
PADDING_BLEND = 0.06

# Line kinds, file kinds, sides and gap positions — the vocabulary the rest
# of the model and the view share.
CONTEXT = "context"
ADD = "add"
DEL = "del"
LINE_KINDS: tuple[str, ...] = (CONTEXT, ADD, DEL)
KIND_CHANGE = "change"
KIND_NEW = "new"
KIND_DELETED = "deleted"
KIND_RENAME = "rename"
KIND_MODE = "mode"
KIND_BINARY = "binary"
KIND_TOO_LARGE = "too-large"
FILE_KINDS: tuple[str, ...] = (
    KIND_CHANGE, KIND_NEW, KIND_DELETED, KIND_RENAME, KIND_MODE, KIND_BINARY, KIND_TOO_LARGE,
)
OLD = "old"
NEW = "new"
SIDES: tuple[str, ...] = (OLD, NEW)
BEFORE = "before"
TRAILING = "trailing"

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_MODE = re.compile(r"^\d{6}$")
_DEV_NULL = "/dev/null"
_WORD_TOKENS = re.compile(r"\w+|\s+|[^\w\s]", re.UNICODE)
_HEX_COLOR = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_ESCAPES = {"\\": "\\", '"': '"', "a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}
_OCTAL = frozenset("01234567")
# A conflict marker line as git writes it: seven `<`, `=`, `>` or `|`
# (diff3's base marker) at the start, then the end of the line or a space
# and a label (`<<<<<<< HEAD`, `>>>>>>> a1b2c3d (subject)`).
_CONFLICT_MARKER = re.compile(r"^(<{7}|={7}|>{7}|\|{7})( |$)")
# How far a conflict marker row's background is pushed toward the
# palette's conflict tone.
CONFLICT_BLEND = 0.30
# The conflict tone itself, one orange for every scheme (no scheme names
# one): the marker rows' foreground, and what their background blends
# toward.
CONFLICT_TONE = "#e5a50a"


@dataclass(frozen=True)
class Line:
    """One patch line: its *kind* (CONTEXT / ADD / DEL), its *text* without
    the sign, and its 1-based number on each side (None on the side the line
    is not on). *no_newline* is set when `\\ No newline at end of file`
    followed it — the writers in gitpatch put the marker back."""

    kind: str
    text: str
    old: int | None
    new: int | None
    no_newline: bool = False


@dataclass(frozen=True)
class Hunk:
    """One `@@` hunk: its 0-based *index* in the file, the header line
    verbatim, the four numbers it declares, the *context* git wrote after the
    closing `@@` (stripped), and its lines."""

    index: int
    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    context: str
    lines: tuple[Line, ...]


@dataclass(frozen=True)
class File:
    """One file of the stream.

    *path* is the file's current name (its old name when it was deleted);
    *previous_path* is set for a rename or copy. *kind* is one of FILE_KINDS:
    KIND_BINARY and KIND_TOO_LARGE are placeholders with no hunks, KIND_MODE
    a mode change with no content change. *old_mode* / *new_mode* are the six
    digit modes the header named (None when it did not), *similarity* the
    rename's percentage. *untracked* marks a working-tree file git does not
    know yet, synthesized by gitops from `diff --no-index`. *patch* is the
    whole stanza verbatim — what `git apply` and gitpatch's writers read —
    and *patch_hash* its digest, the key marks and highlights survive a
    reload by. *conflict* marks an unmerged path of a half-finished merge,
    rebase, cherry-pick or revert, synthesized by gitops from `diff --ours`
    (the working tree, conflict markers and all, against our side): the
    view paints the marker rows and offers no partial staging on it.
    """

    path: str
    previous_path: str | None
    kind: str
    old_mode: str | None
    new_mode: str | None
    similarity: int | None
    untracked: bool
    hunks: tuple[Hunk, ...]
    additions: int
    deletions: int
    patch: str
    patch_hash: str
    conflict: bool = False


def is_conflict_marker(text: object) -> bool:
    """Whether a patch line's *text* (without its sign) is one of git's
    conflict markers — `<<<<<<<`, `=======`, `>>>>>>>` or diff3's
    `|||||||` — alone or followed by a space and a label."""
    return isinstance(text, str) and _CONFLICT_MARKER.match(text) is not None


@dataclass(frozen=True)
class Gap:
    """An unchanged stretch the diff left out: *position* BEFORE hunk
    *hunk_index* or TRAILING after it (the last hunk's), its inclusive
    1-based range on each side (None when that side has no lines there) and
    the number of lines."""

    position: str
    hunk_index: int
    old_range: tuple[int, int] | None
    new_range: tuple[int, int] | None
    count: int


@dataclass(frozen=True)
class SplitRow:
    """One row of the split layout: the old side's line and the new side's,
    either None where the other side has no partner (a padding cell)."""

    old: Line | None
    new: Line | None


@dataclass(frozen=True)
class Emphasis:
    """The words that differ on one side of a paired deletion/addition:
    *side* OLD or NEW, *line_index* into the hunk's lines, and the half-open
    character *spans* of the line's text to emphasise."""

    side: str
    line_index: int
    spans: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class DiffPalette:
    """The colours a hunk view paints with, as `#rrggbb` strings: the row
    backgrounds, the word-emphasis backgrounds, the padding cells of the
    split layout, and the sign colours themselves."""

    added_bg: str
    removed_bg: str
    added_emphasis_bg: str
    removed_emphasis_bg: str
    padding_bg: str
    added_fg: str
    removed_fg: str
    # A conflict marker row of an unmerged file: its background (the text
    # background pushed CONFLICT_BLEND toward CONFLICT_TONE) and the
    # marker's own foreground.
    conflict_bg: str = CONFLICT_TONE
    conflict_fg: str = CONFLICT_TONE


# -- parsing the stream -------------------------------------------------------


def parse(text: object, untracked: bool = False, conflict: bool = False) -> list[File]:
    """A `git diff` / `git show --format=` stream as Files, in git's order.

    Stanzas start at `diff --git`; anything before the first (a commit
    message git was asked not to print, a warning) and any stanza of another
    kind (`diff --cc` of a merge) is ignored. A stanza is dropped when its
    path can't be worked out or is longer than MAX_PATH_CHARS; a hunk whose
    body ends before its declared counts are satisfied, or that carries a
    line no unified diff has, is dropped together with the rest of that
    stanza's hunks (the text so far is kept, the raw stanza always is). A
    stream over MAX_PATCH_CHARS loses its last, possibly cut, stanza and
    everything after; never more than MAX_FILES come back. *untracked*
    marks every file the way gitops does for a synthesized new-file diff;
    *conflict* marks every file as an unmerged path (gitops's `diff
    --ours` read of a half-finished operation's clashes).
    """
    if not isinstance(text, str) or not text:
        return []
    truncated = False
    if len(text) > MAX_PATCH_CHARS:
        text = text[:MAX_PATCH_CHARS]
        truncated = True
    stanzas: list[list[str]] = []
    # The stream's final newline is not an empty line (a hunk cut short
    # would otherwise borrow it as a bare context line).
    for line in text.removesuffix("\n").split("\n"):
        if line.startswith("diff "):
            stanzas.append([line])
        elif stanzas:
            stanzas[-1].append(line)
    if truncated and stanzas:
        stanzas.pop()
    files: list[File] = []
    for lines in stanzas:
        if not lines[0].startswith("diff --git "):
            continue
        parsed = _parse_stanza(lines, untracked, conflict)
        if parsed is not None:
            files.append(parsed)
            if len(files) >= MAX_FILES:
                break
    return files


class _Header:
    """What the lines above a stanza's first `@@` say."""

    def __init__(self) -> None:
        self.old_path = ""
        self.new_path = ""
        self.git_line: tuple[str, str] | None = None
        self.rename_from: str | None = None
        self.rename_to: str | None = None
        self.explicit_kind: str | None = None
        self.old_mode: str | None = None
        self.new_mode: str | None = None
        self.similarity: int | None = None
        self.binary = False
        self.body_start = 0


def _parse_stanza(lines: list[str], untracked: bool, conflict: bool = False) -> File | None:
    header = _parse_header(lines)
    hunks, additions, deletions = _parse_hunks(lines, header.body_start)
    path, previous_path = _resolve_paths(header)
    if not path or len(path) > MAX_PATH_CHARS or _DEV_NULL in (path, previous_path):
        return None
    if previous_path is not None and len(previous_path) > MAX_PATH_CHARS:
        previous_path = None
    patch = "\n".join(lines)
    if patch and not patch.endswith("\n"):
        patch += "\n"
    kind = _resolve_kind(header, hunks, previous_path)
    if kind != KIND_BINARY and too_large(additions, deletions, len(patch.encode("utf-8", "replace"))):
        kind = KIND_TOO_LARGE
    if kind in (KIND_BINARY, KIND_TOO_LARGE):
        hunks = ()
    return File(
        path=path,
        previous_path=previous_path,
        kind=kind,
        old_mode=header.old_mode,
        new_mode=header.new_mode,
        similarity=header.similarity,
        untracked=untracked,
        hunks=hunks,
        additions=additions,
        deletions=deletions,
        patch=patch,
        patch_hash=_digest(patch),
        conflict=conflict,
    )


def _parse_header(lines: list[str]) -> _Header:
    header = _Header()
    header.body_start = len(lines)
    for index, raw in enumerate(lines):
        if _HUNK_HEADER.match(raw):
            header.body_start = index
            break
        line = raw.rstrip("\r")
        if line.startswith("diff --git "):
            header.git_line = _git_line_paths(line[len("diff --git "):])
        elif line.startswith("--- "):
            header.old_path = _target(line[4:], "a/")
        elif line.startswith("+++ "):
            header.new_path = _target(line[4:], "b/")
        elif line.startswith("rename from ") or line.startswith("copy from "):
            header.rename_from = _unquote(line.split(" from ", 1)[1])
        elif line.startswith("rename to ") or line.startswith("copy to "):
            header.rename_to = _unquote(line.split(" to ", 1)[1])
        elif line.startswith("new file mode "):
            header.explicit_kind = KIND_NEW
            header.new_mode = _mode(line[len("new file mode "):])
        elif line.startswith("deleted file mode "):
            header.explicit_kind = KIND_DELETED
            header.old_mode = _mode(line[len("deleted file mode "):])
        elif line.startswith("old mode "):
            header.old_mode = _mode(line[len("old mode "):])
        elif line.startswith("new mode "):
            header.new_mode = _mode(line[len("new mode "):])
        elif line.startswith("index "):
            # `index <old>..<new> <mode>`: the mode appears only when it did
            # not change, and then it is both sides'.
            parts = line[len("index "):].split(" ")
            if len(parts) > 1:
                mode = _mode(parts[1])
                if mode is not None:
                    header.old_mode = header.old_mode or mode
                    header.new_mode = header.new_mode or mode
        elif line.startswith("similarity index ") or line.startswith("dissimilarity index "):
            header.similarity = _percent(line.split(" index ", 1)[1])
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            header.binary = True
    return header


def _parse_hunks(lines: list[str], start: int) -> tuple[tuple[Hunk, ...], int, int]:
    """The hunks from *start* on: each body read by count, patch.ts's way —
    counting, not pattern-matching, is what keeps an empty context line
    (written as `""` by some emitters) from ending a hunk. A body that ends
    short or carries an unknown line ends the stanza's hunks."""
    hunks: list[Hunk] = []
    additions = deletions = 0
    cursor = start
    while cursor < len(lines):
        match = _HUNK_HEADER.match(lines[cursor])
        if not match:
            cursor += 1
            continue
        old_start = int(match.group(1))
        old_count = int(match.group(2)) if match.group(2) is not None else 1
        new_start = int(match.group(3))
        new_count = int(match.group(4)) if match.group(4) is not None else 1
        body = _parse_body(lines, cursor + 1, old_start, old_count, new_start, new_count)
        if body is None:
            break
        parsed, cursor = body
        hunks.append(
            Hunk(
                index=len(hunks),
                header=match.group(0).rstrip("\r"),
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                context=match.group(5).strip(),
                lines=tuple(parsed),
            )
        )
        additions += sum(1 for line in parsed if line.kind == ADD)
        deletions += sum(1 for line in parsed if line.kind == DEL)
    return tuple(hunks), additions, deletions


def _parse_body(
    lines: list[str], start: int, old_start: int, old_count: int, new_start: int, new_count: int
) -> tuple[list[Line], int] | None:
    body: list[Line] = []
    remaining_old, remaining_new = old_count, new_count
    old_no, new_no = old_start, new_start
    cursor = start
    while (remaining_old > 0 or remaining_new > 0) and cursor < len(lines):
        line = lines[cursor]
        cursor += 1
        marker, text = line[:1], line[1:]
        if marker == "+":
            body.append(Line(ADD, text, None, new_no))
            new_no += 1
            remaining_new -= 1
        elif marker == "-":
            body.append(Line(DEL, text, old_no, None))
            old_no += 1
            remaining_old -= 1
        elif marker == " " or line == "":
            body.append(Line(CONTEXT, text, old_no, new_no))
            old_no += 1
            new_no += 1
            remaining_old -= 1
            remaining_new -= 1
        elif marker == "\\":
            _mark_no_newline(body)
        else:
            return None
    # A `\ No newline` marker for the hunk's last line sits past the counted lines.
    if cursor < len(lines) and lines[cursor].startswith("\\"):
        _mark_no_newline(body)
        cursor += 1
    if remaining_old > 0 or remaining_new > 0:
        return None
    return body, cursor


def _mark_no_newline(body: list[Line]) -> None:
    if body:
        last = body[-1]
        body[-1] = Line(last.kind, last.text, last.old, last.new, True)


def _resolve_paths(header: _Header) -> tuple[str, str | None]:
    """The stanza's current path and, for a rename or copy, its old one."""
    renamed = header.rename_from is not None and header.rename_to is not None
    old_path = header.old_path or (header.git_line[0] if header.git_line else "")
    new_path = header.new_path or (header.git_line[1] if header.git_line else "")
    if renamed:
        return header.rename_to or "", header.rename_from or None
    if new_path and new_path != _DEV_NULL:
        return new_path, None
    return old_path, None


def _resolve_kind(header: _Header, hunks: tuple[Hunk, ...], previous_path: str | None) -> str:
    if header.binary:
        return KIND_BINARY
    if header.explicit_kind is not None:
        return header.explicit_kind
    if previous_path is not None:
        return KIND_RENAME
    if header.old_path == _DEV_NULL:
        return KIND_NEW
    if header.new_path == _DEV_NULL:
        return KIND_DELETED
    if not hunks and header.old_mode and header.new_mode and header.old_mode != header.new_mode:
        return KIND_MODE
    return KIND_CHANGE


def _git_line_paths(rest: str) -> tuple[str, str] | None:
    """Both paths off a `diff --git a/x b/y` line — the one place they still
    appear in a binary or mode-only stanza, which has no `---`/`+++`. The
    form is ambiguous for paths containing " b/", so prefer a split where
    both sides agree (only a rename makes them differ, and a rename names
    its paths on their own lines)."""
    rest = rest.rstrip("\r")
    if rest.startswith('"'):
        # Quoted paths: `"a/x y" "b/x y"` — split at the closing quote.
        end = _quoted_end(rest)
        if end is None or end + 1 >= len(rest) or rest[end + 1] != " ":
            return None
        old, new = _unquote(rest[: end + 1]), _unquote(rest[end + 2 :])
        return (_strip_prefix(old, "a/"), _strip_prefix(new, "b/"))
    if not rest.startswith("a/"):
        return None
    candidates: list[tuple[str, str]] = []
    at = rest.find(" b/")
    while at != -1:
        candidates.append((rest[2:at], rest[at + 3 :]))
        at = rest.find(" b/", at + 1)
    for old, new in candidates:
        if old == new:
            return old, new
    return candidates[0] if candidates else None


def _quoted_end(text: str) -> int | None:
    at = 1
    while at < len(text):
        if text[at] == "\\":
            at += 2
            continue
        if text[at] == '"':
            return at
        at += 1
    return None


def _strip_prefix(path: str, prefix: str) -> str:
    return path[len(prefix):] if path.startswith(prefix) else path


def _target(name: str, prefix: str) -> str:
    """A ---/+++ line's path with git's a/ or b/ shed; /dev/null as it is."""
    name = name.rstrip("\r")
    if not name.startswith('"'):
        name = name.split("\t")[0]  # some emitters trail a tab + timestamp
    name = _unquote(name)
    if name == _DEV_NULL:
        return name
    return _strip_prefix(name, prefix)


def _unquote(name: str) -> str:
    """git's c-quoted path spelling undone; a bare name comes back as it is.
    Octal escapes are UTF-8 bytes (`\\303\\251` for é) and decode at the end
    with replacement — a path is untrusted input."""
    if len(name) < 2 or not (name.startswith('"') and name.endswith('"')):
        return name
    body = name[1:-1]
    out = bytearray()
    at = 0
    while at < len(body):
        char = body[at]
        if char == "\\" and at + 1 < len(body):
            escape = body[at + 1]
            if escape in _ESCAPES:
                out += _ESCAPES[escape].encode()
                at += 2
                continue
            octal = ""
            while len(octal) < 3 and at + 1 + len(octal) < len(body) and body[at + 1 + len(octal)] in _OCTAL:
                octal += body[at + 1 + len(octal)]
            if octal and int(octal, 8) < 256:
                out.append(int(octal, 8))
                at += 1 + len(octal)
                continue
        out += char.encode("utf-8")
        at += 1
    return out.decode("utf-8", "replace")


def _mode(value: str) -> str | None:
    mode = value.strip()
    return mode if _MODE.match(mode) else None


def _percent(value: str) -> int | None:
    digits = value.strip().rstrip("%").strip()
    if not digits.isdigit():
        return None
    return max(0, min(100, int(digits)))


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


# -- the numstat pre-pass and the caps ----------------------------------------


def parse_numstat(text: object) -> dict[str, tuple[int, int]]:
    """`git diff --numstat -z` as {path: (additions, deletions)}.

    Records are `add TAB del TAB path NUL`; a rename or copy writes an empty
    path followed by the old and new paths as their own NUL-terminated
    tokens (so does `diff --no-index` for a /dev/null pair) and is keyed by
    the new path. Binary files, which git writes as `- TAB -`, have no line
    counts and are left out — the size cap is the only one that can apply to
    them. Newline-separated output (no `-z`) reads too, path verbatim. A
    malformed record is skipped.
    """
    if not isinstance(text, str) or not text:
        return {}
    truncated = False
    if len(text) > MAX_PATCH_CHARS:
        # The same bound parse() puts on the stream, before the whole text
        # is tokenized. The cut lands inside a record, whose remains would
        # read as a shortened path: dropped.
        text = text[:MAX_PATCH_CHARS]
        truncated = True
    counts: dict[str, tuple[int, int]] = {}
    tokens = text.split("\0") if "\0" in text else text.split("\n")
    if truncated:
        tokens.pop()
    index = 0
    while index < len(tokens):
        record = tokens[index]
        index += 1
        if not record:
            continue
        fields = record.split("\t", 2)
        if len(fields) != 3:
            continue
        added, deleted, path = fields
        if path == "" and "\0" in text:
            # A rename: the old and new paths follow as their own tokens.
            if index + 1 >= len(tokens):
                break
            path = tokens[index + 1]
            index += 2
        if not path or len(path) > MAX_PATH_CHARS:
            continue
        if not (added.isdigit() and deleted.isdigit()):
            continue  # `-\t-`: binary
        counts[path] = (int(added), int(deleted))
        if len(counts) >= MAX_FILES:
            break
    return counts


def too_large(additions: int, deletions: int, size: int) -> bool:
    """The large-file rule: over TOO_LARGE_LINES changed lines, or a patch
    over TOO_LARGE_BYTES, is shown as a placeholder."""
    return additions + deletions > TOO_LARGE_LINES or size > TOO_LARGE_BYTES


# -- gaps ---------------------------------------------------------------------


def hunk_range(hunk: Hunk, side: str) -> tuple[int, int]:
    """One hunk's inclusive line span on one side, by the view's convention:
    a zero-count side (a pure insertion's old side) still spans one line."""
    start = hunk.new_start if side == NEW else hunk.old_start
    count = hunk.new_count if side == NEW else hunk.old_count
    return start, start + max(count, 1) - 1


def gaps(file: File, old_len: int | None = None, new_len: int | None = None) -> list[Gap]:
    """The unchanged stretches around *file*'s hunks: BEFORE each hunk (from
    the previous hunk's end, or the top of the file) and TRAILING after the
    last one when a side's length is known. Ranges are 1-based inclusive;
    a side with no lines in the gap (an empty old file) has None. Only gaps
    with lines come back, so a hunk that starts at line 1 has none before
    it, and a placeholder file (no hunks) has none at all."""
    result: list[Gap] = []
    prev_old_end = prev_new_end = 0
    for hunk in file.hunks:
        old_first = hunk.old_start if hunk.old_count else hunk.old_start + 1
        new_first = hunk.new_start if hunk.new_count else hunk.new_start + 1
        count = min(max(old_first - prev_old_end - 1, 0), max(new_first - prev_new_end - 1, 0))
        if count > 0:
            result.append(
                Gap(
                    BEFORE,
                    hunk.index,
                    (old_first - count, old_first - 1),
                    (new_first - count, new_first - 1),
                    count,
                )
            )
        prev_old_end = hunk.old_start + hunk.old_count - 1 if hunk.old_count else hunk.old_start
        prev_new_end = hunk.new_start + hunk.new_count - 1 if hunk.new_count else hunk.new_start
    if file.hunks and (old_len is not None or new_len is not None):
        old_gap = max(old_len - prev_old_end, 0) if old_len is not None else None
        new_gap = max(new_len - prev_new_end, 0) if new_len is not None else None
        known = [gap for gap in (old_gap, new_gap) if gap is not None]
        count = min(known) if known else 0
        if count > 0:
            result.append(
                Gap(
                    TRAILING,
                    file.hunks[-1].index,
                    (prev_old_end + 1, prev_old_end + count),
                    (prev_new_end + 1, prev_new_end + count),
                    count,
                )
            )
    return result


# -- rows and emphasis --------------------------------------------------------


def _change_blocks(lines: Sequence[Line]) -> Iterable[tuple[list[int], list[int]]]:
    """Each run of deletions and additions between context lines, as the
    indexes of its DEL lines and of its ADD lines."""
    dels: list[int] = []
    adds: list[int] = []
    for index, line in enumerate(lines):
        if line.kind == CONTEXT:
            if dels or adds:
                yield dels, adds
                dels, adds = [], []
        elif line.kind == DEL:
            dels.append(index)
        else:
            adds.append(index)
    if dels or adds:
        yield dels, adds


def split_rows(hunk: Hunk) -> list[SplitRow]:
    """The split layout's rows: a context line is one row on both sides; inside
    a change block the i-th deletion pairs with the i-th addition, and the
    longer side's extras sit beside padding (None)."""
    rows: list[SplitRow] = []
    lines = hunk.lines
    cursor = 0
    for dels, adds in _change_blocks(lines):
        first = min(dels[0] if dels else len(lines), adds[0] if adds else len(lines))
        for index in range(cursor, first):
            rows.append(SplitRow(lines[index], lines[index]))
        for offset in range(max(len(dels), len(adds))):
            old = lines[dels[offset]] if offset < len(dels) else None
            new = lines[adds[offset]] if offset < len(adds) else None
            rows.append(SplitRow(old, new))
        cursor = max(dels[-1] if dels else -1, adds[-1] if adds else -1) + 1
    for index in range(cursor, len(lines)):
        rows.append(SplitRow(lines[index], lines[index]))
    return rows


def word_emphasis(hunk: Hunk) -> list[Emphasis]:
    """The words that differ inside each paired deletion/addition (paired as
    split_rows pairs them). Tokens are words, whitespace runs and single
    punctuation marks; a pair whose SequenceMatcher ratio is under
    EMPHASIS_MIN_RATIO is two different lines and gets nothing, and neither
    does a hunk over MAX_EMPHASIS_LINES or a line over MAX_EMPHASIS_CHARS.
    Spans are half-open character offsets into Line.text, merged when
    adjacent; a side whose text is wholly kept has no entry."""
    lines = hunk.lines
    if len(lines) > MAX_EMPHASIS_LINES:
        return []
    result: list[Emphasis] = []
    for dels, adds in _change_blocks(lines):
        for old_index, new_index in zip(dels, adds, strict=False):
            old_text, new_text = lines[old_index].text, lines[new_index].text
            if old_text == new_text or max(len(old_text), len(new_text)) > MAX_EMPHASIS_CHARS:
                continue
            old_tokens = _WORD_TOKENS.findall(old_text)
            new_tokens = _WORD_TOKENS.findall(new_text)
            matcher = difflib.SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
            if matcher.ratio() < EMPHASIS_MIN_RATIO:
                continue
            old_spans: list[tuple[int, int]] = []
            new_spans: list[tuple[int, int]] = []
            old_offsets = _offsets(old_tokens)
            new_offsets = _offsets(new_tokens)
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag == "equal":
                    continue
                if i2 > i1:
                    _add_span(old_spans, old_offsets[i1], old_offsets[i2])
                if j2 > j1:
                    _add_span(new_spans, new_offsets[j1], new_offsets[j2])
            if old_spans:
                result.append(Emphasis(OLD, old_index, tuple(old_spans)))
            if new_spans:
                result.append(Emphasis(NEW, new_index, tuple(new_spans)))
    return result


def _offsets(tokens: Sequence[str]) -> list[int]:
    """Character offset of each token's start, plus the end of the last."""
    offsets = [0]
    for token in tokens:
        offsets.append(offsets[-1] + len(token))
    return offsets


def _add_span(spans: list[tuple[int, int]], start: int, end: int) -> None:
    if spans and spans[-1][1] == start:
        spans[-1] = (spans[-1][0], end)
    else:
        spans.append((start, end))


# -- colours ------------------------------------------------------------------

# What the blend falls back to when a scheme names no usable colour: the
# Adwaita schemes' text background and their diff foregrounds.
_DEFAULTS = {
    True: ("#1e1e1e", "#33b2a4", "#f66151"),
    False: ("#ffffff", "#26a269", "#c01c28"),
}


def palette(text_bg: object, added_fg: object, removed_fg: object, dark: bool = False) -> DiffPalette:
    """The view's colours from a style scheme's: its text background and its
    `diff:added-line` / `diff:removed-line` foregrounds, `#rgb`, `#rrggbb`
    or `#rrggbbaa` (alpha dropped). A row is the background pushed
    ROW_BLEND of the way toward the sign colour, the emphasis EMPHASIS_BLEND,
    and the split layout's padding cells PADDING_BLEND toward white on a
    dark scheme, black on a light one. A colour that doesn't parse falls
    back to the Adwaita scheme's for *dark*."""
    default_bg, default_added, default_removed = _DEFAULTS[bool(dark)]
    bg = _rgb(text_bg) or _rgb(default_bg) or (0, 0, 0)
    added = _rgb(added_fg) or _rgb(default_added) or (0, 0, 0)
    removed = _rgb(removed_fg) or _rgb(default_removed) or (0, 0, 0)
    toward = (255, 255, 255) if dark else (0, 0, 0)
    return DiffPalette(
        added_bg=_hex(blend(bg, added, ROW_BLEND)),
        removed_bg=_hex(blend(bg, removed, ROW_BLEND)),
        added_emphasis_bg=_hex(blend(bg, added, EMPHASIS_BLEND)),
        removed_emphasis_bg=_hex(blend(bg, removed, EMPHASIS_BLEND)),
        padding_bg=_hex(blend(bg, toward, PADDING_BLEND)),
        added_fg=_hex(added),
        removed_fg=_hex(removed),
        conflict_bg=_hex(blend(bg, _rgb(CONFLICT_TONE) or (229, 165, 10), CONFLICT_BLEND)),
        conflict_fg=CONFLICT_TONE,
    )


def blend(base: tuple[int, int, int], toward: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    """*base* pushed *amount* (0..1) of the way toward *toward*, per channel,
    rounded to the nearest byte."""
    amount = max(0.0, min(1.0, float(amount)))
    channels = [
        max(0, min(255, round(b * (1.0 - amount) + t * amount))) for b, t in zip(base, toward, strict=True)
    ]
    return channels[0], channels[1], channels[2]


def _rgb(value: object) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    match = _HEX_COLOR.match(value.strip())
    if not match:
        return None
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


# -- addressing ---------------------------------------------------------------


def find_file(files: Iterable[File], path: object, side: str = NEW) -> File | None:
    """The file *path* names: by its current path, or on the OLD side also by
    the name it had before a rename."""
    if not isinstance(path, str) or not path:
        return None
    for file in files:
        if file.path == path or (side == OLD and file.previous_path == path):
            return file
    return None


def locate(files: Iterable[File], path: object, side: object, line: object) -> tuple[File, int, int] | None:
    """The (file, hunk index, line index) that (path, side, line) names:
    the hunk whose *side* span holds 1-based *line* and, in it, the Line
    numbered so on that side. None when the file isn't in *files*, the side
    isn't OLD or NEW, or no hunk carries the line (a placeholder file, a line
    in an unchanged stretch). What the annotate and highlight tools and the
    view's reveal share, so both agree on what "line 12" is."""
    if not isinstance(side, str) or side not in SIDES:
        return None
    if not isinstance(line, int) or isinstance(line, bool) or line < 1:
        return None
    file = find_file(files, path, side)
    if file is None:
        return None
    for hunk in file.hunks:
        start, end = hunk_range(hunk, side)
        if line < start or line > end:
            continue
        for index, entry in enumerate(hunk.lines):
            number = entry.new if side == NEW else entry.old
            if number == line:
                return file, hunk.index, index
    return None


def nearest_hunk(file: File, side: object, line: object) -> int | None:
    """The index of *file*'s hunk whose *side* span is closest to 1-based
    *line* (the one holding it when one does; the earlier of two equally
    far) — where the view lands when an address names a line of the file
    that no hunk carries (an unchanged stretch). None for a file with no
    hunks or a bad address."""
    if not isinstance(side, str) or side not in SIDES:
        return None
    if not isinstance(line, int) or isinstance(line, bool) or line < 1:
        return None
    best: tuple[int, int] | None = None
    for hunk in file.hunks:
        start, end = hunk_range(hunk, side)
        distance = 0 if start <= line <= end else min(abs(line - start), abs(line - end))
        if best is None or distance < best[0]:
            best = (distance, hunk.index)
    return best[1] if best is not None else None


# The characters GtkTextBuffer splits a paragraph on besides the newline (a
# lone CR, U+2029) and the ones Pango breaks a line at (NEL, U+2028), each
# shown as a same-width symbol so that the buffer's paragraph i is patch row
# i and every character offset (emphasis, search) holds.
_DISPLAY_TABLE = str.maketrans({"\r": "␍", "\x85": "␤", "\u2028": "␤", "\u2029": "¶"})


def display_text(text: str) -> str:
    """What a hunk view's buffer holds for a patch line's *text*: a trailing
    CR (a CRLF file) dropped, and every other paragraph or line separator
    inside it replaced by a visible stand-in of the same width."""
    if text.endswith("\r"):
        text = text[:-1]
    return text.translate(_DISPLAY_TABLE)


def stable_key(file: File, hunk: Hunk | None = None) -> str:
    """What a reload matches widgets by: the file's path (with its old name
    for a rename), and for a hunk its old and new spans plus a digest of its
    lines — so an untouched hunk keeps its widget, selection and notes
    across a reload, and a hunk that moved or changed gets a fresh one."""
    head = file.path if file.previous_path is None else f"{file.previous_path}\x00{file.path}"
    if hunk is None:
        return f"{head}\x00{file.kind}\x00{file.patch_hash}"
    body = "\n".join(f"{line.kind}{line.text}" for line in hunk.lines)
    return (
        f"{head}\x00{hunk.old_start},{hunk.old_count}\x00{hunk.new_start},{hunk.new_count}"
        f"\x00{_digest(body)}"
    )
