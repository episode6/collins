# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Notes and highlights on the native diff view — the GTK-free half.

A *note* is a card under a hunk anchored to one line of one side (decision
5 of ~/specs/collins/native-diff-panel.md): a summary, an optional
rationale and author, and a source — USER for the ones typed into the
view's own editor, AGENT for the ones the `annotate_diff` tool lands. A
*highlight* is an attention mark on a character range of a line, in one of
TONES. Both live in a `MarkStore` for the tab's life (decision 8:
nothing is written to state.json), keyed to survive a reload: every mark
remembers the stable key of the hunk it sits in (diffmodel.stable_key —
the hunk's body, never its line numbers) and its line's index into that
hunk, so a reload that leaves the hunk's lines alone keeps the mark — with
its line number moved when the lines above it shifted (`prune` renumbers
it) — one that changes or drops the hunk drops it, and a load that doesn't
show the file at all parks it until one does.

A note can also sit on a line no hunk carries — an unchanged stretch the
view draws as a gap, expanded or not (the right-click menu on the
expanded context): its key is CONTEXT_KEY and its anchor is the line
number itself, since there is no hunk body to key on. Such a note stays
at its number across reloads (`gap_address` says which gap row draws it)
and moves into a hunk when a later load's hunk comes to hold that line.

Everything that arrives is foreign content — an agent's summary as much
as a branch name: `bound_text` caps every string at NOTE_MAX_CHARS (a
name at MAX_AUTHOR_CHARS), the widgets show them through `set_text`, and
`resolve_anchor` refuses an address the loaded diff doesn't carry, so a
batch lands whole or not at all (`MarkStore.add_notes`, `add_highlights`).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from . import diffmodel

# The sources a note can come from: typed into the view, or landed by the
# session through the annotate tool.
USER = "user"
AGENT = "agent"
SOURCES: tuple[str, ...] = (USER, AGENT)

# The highlight tones: what the view paints a marked range with. MATCH is
# the default.
TONE_MATCH = "match"
TONE_CURRENT = "current"
TONE_INFO = "info"
TONE_WARNING = "warning"
TONE_ERROR = "error"
TONE_DIM = "dim"
TONES: tuple[str, ...] = (TONE_MATCH, TONE_CURRENT, TONE_INFO, TONE_WARNING, TONE_ERROR, TONE_DIM)

# The hunk key of a note placed on a line outside every hunk (an unchanged
# stretch): anchored by its number alone, never renumbered.
CONTEXT_KEY = "context"
# What `_place` answers for such a mark when no hunk holds its line: drawn
# under the gap `gap_address` names, or parked when none does.
_OUTSIDE = "outside"

# The most characters a summary, a rationale or the editor's text may hold
# (the spec's cap); an author's name is shorter. Beyond either, the text is
# cut, never refused — a note is worth keeping at any length.
NOTE_MAX_CHARS = 4000
MAX_AUTHOR_CHARS = 80
# How many marks one store holds: past these an add is refused (a runaway
# agent loop must not grow the page without bound).
MAX_NOTES = 1000
MAX_HIGHLIGHTS = 5000
# A tool may land this many at once (the schema's caps, mirrored here).
MAX_NOTES_PER_BATCH = 100
MAX_HIGHLIGHTS_PER_BATCH = 500


@dataclass(frozen=True)
class Note:
    """One note: where it sits (*path*, *side*, 1-based *line*), what it
    says, who said it, the key of the hunk it was placed in and the index
    of its line in that hunk's lines — what it is found by again after a
    reload that moved the hunk's numbers (*line* then follows)."""

    id: str
    source: str
    path: str
    side: str
    line: int
    summary: str
    rationale: str | None
    author: str | None
    hunk_key: str
    line_index: int = 0


@dataclass(frozen=True)
class Highlight:
    """One highlight: the code points [*start*, *end*) of a line."""

    id: str
    path: str
    side: str
    line: int
    start: int
    end: int
    tone: str
    hunk_key: str
    line_index: int = 0


@dataclass(frozen=True)
class NoteSpec:
    """What a caller asks for: the address (exactly one of *line* — on
    *side*, new by default — or 1-based *hunk*, whose first line on that
    side is the anchor), the words, an optional author."""

    path: str
    summary: str
    rationale: str | None = None
    author: str | None = None
    side: str | None = None
    line: int | None = None
    hunk: int | None = None


@dataclass(frozen=True)
class HighlightSpec:
    path: str
    line: int
    start: int
    end: int
    side: str | None = None
    tone: str | None = None


@dataclass(frozen=True)
class Anchor:
    """A resolved address: the File, the hunk (its index in file.hunks),
    the Line (its index in hunk.lines), and the side + number it sits at.
    An address outside every hunk (resolve_anchor's *outside*) has no
    hunk: *hunk_index* None, *line_index* -1, the key CONTEXT_KEY."""

    file: diffmodel.File
    hunk_index: int | None
    line_index: int
    side: str
    line: int

    @property
    def outside(self) -> bool:
        return self.hunk_index is None

    @property
    def hunk(self) -> diffmodel.Hunk:
        if self.hunk_index is None:
            raise ValueError("an anchor outside every hunk names no hunk")
        return self.file.hunks[self.hunk_index]

    @property
    def key(self) -> str:
        if self.hunk_index is None:
            return CONTEXT_KEY
        return diffmodel.stable_key(self.file, self.hunk)


class _Keys:
    """The stable keys of the files a batch lands on, computed once per
    file (`Anchor.key` alone walks the file's hunks for every mark)."""

    def __init__(self) -> None:
        self._by_path: dict[str, tuple[str, ...]] = {}

    def of(self, anchor: Anchor) -> str:
        if anchor.hunk_index is None:
            return CONTEXT_KEY
        keys = self._by_path.get(anchor.file.path)
        if keys is None:
            keys = self._by_path[anchor.file.path] = diffmodel.stable_keys(anchor.file)
        return keys[anchor.hunk_index]


# -- text ---------------------------------------------------------------------


# The line and paragraph separators a text widget would break a line on
# besides "\n": folded to it, so a summary is the one line it claims to be.
_SEPARATORS = {"\r\n": "\n", "\r": "\n", "\x85": "\n", "\u2028": "\n", "\u2029": "\n"}


def bound_text(text: object, limit: int = NOTE_MAX_CHARS) -> str:
    """*text* as a note shows it: a string (anything else is ""), CRLF, CR,
    NEL and the Unicode line and paragraph separators folded to newlines,
    other control characters — C0 and C1, DEL — dropped (tab and newline
    kept), surrounding whitespace trimmed, cut at *limit*."""
    if not isinstance(text, str):
        return ""
    for separator, newline in _SEPARATORS.items():
        text = text.replace(separator, newline)
    text = "".join(ch for ch in text if ch in "\n\t" or (ch >= " " and not "\x7f" <= ch <= "\x9f"))
    return text.strip()[:limit].rstrip()


def summary_text(text: object) -> str:
    """A summary is one line: bound_text with its newlines read as
    spaces, so the editor's split (first line, the rest) and a card's
    heading can't disagree about where the summary ends."""
    return " ".join(part.strip() for part in bound_text(text).split("\n") if part.strip())


def split_note_text(text: object) -> tuple[str, str | None]:
    """The editor's text as (summary, rationale): the first non-blank line
    is the summary, whatever follows it the rationale (None when there is
    nothing). "" for an empty text — the caller refuses that."""
    whole = bound_text(text)
    if not whole:
        return "", None
    head, _sep, tail = whole.partition("\n")
    tail = tail.strip()
    return head.strip(), tail or None


def join_note_text(summary: str, rationale: str | None) -> str:
    """The inverse: what the editor opens on for an existing note."""
    return summary if not rationale else f"{summary}\n{rationale}"


def _author(value: object) -> str | None:
    text = bound_text(value, MAX_AUTHOR_CHARS).replace("\n", " ")
    return text or None


# -- addresses ----------------------------------------------------------------


def resolve_anchor(
    files: Sequence[diffmodel.File],
    path: object,
    side: object = None,
    line: object = None,
    hunk: object = None,
    outside: bool = False,
) -> Anchor | str:
    """Where (*path*, *side*, *line* | *hunk*) lands in *files* — or the
    reason it doesn't, as one line naming the offender. *side* defaults to
    the new side; exactly one of *line* (1-based on that side) and *hunk*
    (1-based) is given; a hunk address anchors on the hunk's first line
    that has a number on the side. With *outside*, a line no hunk carries
    is accepted too when a gap of the file can hold it (`gap_address`):
    the anchor then names no hunk (the view's right-click on expanded
    context; the number is the caller's word that the line exists)."""
    if not isinstance(path, str) or not path:
        return "a note needs a file path"
    side = diffmodel.NEW if side is None else side
    if side not in diffmodel.SIDES:
        return f"{path}: side must be one of {', '.join(diffmodel.SIDES)}"
    if (line is None) == (hunk is None):
        return f"{path}: give exactly one of line and hunk"
    file = diffmodel.find_file(files, path, side)
    if file is None:
        return f"{path} is not in the loaded diff"
    if hunk is not None:
        if isinstance(hunk, bool) or not isinstance(hunk, int) or hunk < 1 or hunk > len(file.hunks):
            return f"{path} has no hunk {hunk!r} (it has {len(file.hunks)})"
        target = file.hunks[hunk - 1]
        for index, entry in enumerate(target.lines):
            number = entry.new if side == diffmodel.NEW else entry.old
            if number is not None:
                return Anchor(file, hunk - 1, index, side, number)
        return f"hunk {hunk} of {path} has no lines on the {side} side"
    located = diffmodel.locate(files, path, side, line)
    if located is None:
        if outside and gap_address(file, side, line) is not None:
            return Anchor(file, None, -1, side, int(line))  # type: ignore[arg-type]
        return f"line {line!r} ({side}) of {path} is not in a hunk of the loaded diff"
    _file, hunk_index, line_index = located
    return Anchor(file, hunk_index, line_index, side, int(line))  # type: ignore[arg-type]


def gap_address(file: diffmodel.File, side: object, line: object) -> str | None:
    """The gap row of *file* that draws 1-based *line* on *side* — the
    view's address, `before:<hunk index>` for the stretch above a hunk or
    `trailing:<last hunk index>` past the last one — or None: a line a
    hunk carries, a side the file has no lines on (a new file's old side,
    a deleted file's new), a placeholder file, or a bad number. The
    trailing gap's length is not known here (it needs the file read), so
    every line past the last hunk is its."""
    if not isinstance(side, str) or side not in diffmodel.SIDES:
        return None
    if not isinstance(line, int) or isinstance(line, bool) or line < 1:
        return None
    if not file.hunks or file.kind in (diffmodel.KIND_BINARY, diffmodel.KIND_TOO_LARGE):
        return None
    if side == diffmodel.OLD and (file.kind == diffmodel.KIND_NEW or file.untracked):
        return None
    if side == diffmodel.NEW and file.kind == diffmodel.KIND_DELETED:
        return None
    for gap in diffmodel.gaps(file):
        span = gap.new_range if side == diffmodel.NEW else gap.old_range
        if span is not None and span[0] <= line <= span[1]:
            return f"{gap.position}:{gap.hunk_index}"
    for hunk in file.hunks:
        start, end = diffmodel.hunk_range(hunk, side)
        if start <= line <= end:
            return None
    last = file.hunks[-1]
    if line > diffmodel.hunk_range(last, side)[1]:
        return f"{diffmodel.TRAILING}:{last.index}"
    return None


def _line_text(anchor: Anchor) -> str:
    return anchor.hunk.lines[anchor.line_index].text


# -- the store ----------------------------------------------------------------


class MarkStore:
    """Every note and highlight of one page, in insertion order, with ids
    of its own minting (`n1`, `h1`, …)."""

    def __init__(self) -> None:
        self._notes: dict[str, Note] = {}
        self._highlights: dict[str, Highlight] = {}
        self._serial = 0

    def _mint(self, prefix: str) -> str:
        self._serial += 1
        return f"{prefix}{self._serial}"

    # -- reading --

    def notes(self, path: str | None = None) -> list[Note]:
        return [n for n in self._notes.values() if path is None or n.path == path]

    def highlights(self, path: str | None = None) -> list[Highlight]:
        return [h for h in self._highlights.values() if path is None or h.path == path]

    def note(self, note_id: object) -> Note | None:
        return self._notes.get(note_id) if isinstance(note_id, str) else None

    def __len__(self) -> int:
        return len(self._notes) + len(self._highlights)

    # -- adding --

    def add_notes(
        self,
        files: Sequence[diffmodel.File],
        specs: Sequence[NoteSpec],
        source: str,
        outside: bool = False,
    ) -> list[Note] | str:
        """Land *specs* as notes from *source*, all of them or none: the
        batch is resolved against *files* first, and the first address the
        diff doesn't carry (or an empty summary) refuses the whole batch
        with its reason. With *outside*, a line no hunk carries lands as a
        number-anchored note (resolve_anchor). Returns the Notes in
        order."""
        if source not in SOURCES:
            return f"unknown note source {source!r}"
        if not specs:
            return "no notes given"
        if len(specs) > MAX_NOTES_PER_BATCH:
            return f"at most {MAX_NOTES_PER_BATCH} notes at once"
        if len(self._notes) + len(specs) > MAX_NOTES:
            return f"the page holds at most {MAX_NOTES} notes"
        resolved: list[tuple[Anchor, str, str | None, str | None]] = []
        for spec in specs:
            anchor = resolve_anchor(files, spec.path, spec.side, spec.line, spec.hunk, outside)
            if isinstance(anchor, str):
                return anchor
            summary = summary_text(spec.summary)
            if not summary:
                return f"{spec.path}: a note needs a summary"
            rationale = bound_text(spec.rationale) or None
            resolved.append((anchor, summary, rationale, _author(spec.author)))
        added: list[Note] = []
        keys = _Keys()
        for anchor, summary, rationale, author in resolved:
            note = Note(
                self._mint("n"),
                source,
                anchor.file.path,
                anchor.side,
                anchor.line,
                summary,
                rationale,
                author,
                keys.of(anchor),
                anchor.line_index,
            )
            self._notes[note.id] = note
            added.append(note)
        return added

    def add_highlights(
        self, files: Sequence[diffmodel.File], specs: Sequence[HighlightSpec]
    ) -> list[Highlight] | str:
        """Land *specs* as highlights, all or none (as add_notes). A range
        is [start, end) in code points of the line's text, start < end,
        within the line; the tone one of TONES (match by default)."""
        if not specs:
            return "no highlights given"
        if len(specs) > MAX_HIGHLIGHTS_PER_BATCH:
            return f"at most {MAX_HIGHLIGHTS_PER_BATCH} highlights at once"
        if len(self._highlights) + len(specs) > MAX_HIGHLIGHTS:
            return f"the page holds at most {MAX_HIGHLIGHTS} highlights"
        resolved: list[tuple[Anchor, int, int, str]] = []
        for spec in specs:
            anchor = resolve_anchor(files, spec.path, spec.side, spec.line, None)
            if isinstance(anchor, str):
                return anchor
            tone = TONE_MATCH if spec.tone is None else spec.tone
            if tone not in TONES:
                return f"{spec.path}: tone must be one of {', '.join(TONES)}"
            start, end = spec.start, spec.end
            if any(isinstance(v, bool) or not isinstance(v, int) for v in (start, end)):
                return f"{spec.path}: start and end must be integers"
            width = len(_line_text(anchor))
            if start < 0 or end > width or start >= end:
                return (
                    f"{spec.path} line {anchor.line} ({anchor.side}): range [{start}, {end}) "
                    f"is not within its {width} characters"
                )
            resolved.append((anchor, start, end, tone))
        added: list[Highlight] = []
        keys = _Keys()
        for anchor, start, end, tone in resolved:
            mark = Highlight(
                self._mint("h"),
                anchor.file.path,
                anchor.side,
                anchor.line,
                start,
                end,
                tone,
                keys.of(anchor),
                anchor.line_index,
            )
            self._highlights[mark.id] = mark
            added.append(mark)
        return added

    # -- changing --

    def edit(self, note_id: object, summary: object, rationale: object = None) -> Note | None:
        """Re-word a note: both words are replaced, so a *rationale* left
        None clears the note's (the editor hands over the whole text,
        split). None when there is no such note or no summary."""
        note = self.note(note_id)
        if note is None:
            return None
        words = summary_text(summary)
        if not words:
            return None
        updated = replace(note, summary=words, rationale=bound_text(rationale) or None)
        self._notes[note.id] = updated
        return updated

    def remove(self, mark_id: object) -> bool:
        """Drop one note or highlight by id."""
        if not isinstance(mark_id, str):
            return False
        return self._notes.pop(mark_id, None) is not None or self._highlights.pop(mark_id, None) is not None

    def clear(
        self,
        path: str | None = None,
        notes: bool = False,
        highlights: bool = False,
        include_user: bool = False,
    ) -> int:
        """Drop the marks asked for — *notes* (the agent's; the user's too
        with *include_user*) and / or *highlights* — of *path*, or of every
        file. Returns how many went."""
        gone = 0
        if notes:
            for note in list(self._notes.values()):
                if path is not None and note.path != path:
                    continue
                if note.source == USER and not include_user:
                    continue
                del self._notes[note.id]
                gone += 1
        if highlights:
            for mark in list(self._highlights.values()):
                if path is None or mark.path == path:
                    del self._highlights[mark.id]
                    gone += 1
        return gone

    # -- a reload --

    def prune(self, files: Sequence[diffmodel.File]) -> int:
        """A load landed: drop every mark whose file *files* shows without
        the hunk it was placed in (the hunk's lines changed or went), and
        renumber one whose hunk is there with its lines but at other
        numbers (a range staged out above it, an edit above); a mark on a
        file the load doesn't show stays parked. A mark outside every hunk
        (CONTEXT_KEY) keeps its number whatever the load. Returns how many
        went or moved."""
        shown = {file.path: file for file in files}
        keys = {file.path: diffmodel.stable_keys(file) for file in files}
        changed = 0
        for table in (self._notes, self._highlights):
            for mark in list(table.values()):
                file = shown.get(mark.path)
                if file is None:
                    continue
                where = _place(file, keys[file.path], mark)
                if where is None:
                    del table[mark.id]
                    changed += 1
                    continue
                if where == _OUTSIDE:
                    continue
                entry = file.hunks[where[0]].lines[where[1]]
                number = entry.new if mark.side == diffmodel.NEW else entry.old
                if number is not None and number != mark.line:
                    table[mark.id] = replace(mark, line=number)
                    changed += 1
        return changed

    def placed_notes(self, files: Sequence[diffmodel.File]) -> dict[tuple[str, int], list[tuple[Note, int]]]:
        """The notes *files* shows, by (path, hunk index) → [(note, line
        index into the hunk's lines)], in insertion order."""
        return _placed(files, self._notes.values())

    def placed_highlights(
        self, files: Sequence[diffmodel.File]
    ) -> dict[tuple[str, int], list[tuple[Highlight, int]]]:
        return _placed(files, self._highlights.values())

    def placed_outside_notes(self, files: Sequence[diffmodel.File]) -> dict[tuple[str, str], list[Note]]:
        """The notes *files* shows on lines no hunk carries, by (path, gap
        address — `gap_address`) → [note], in insertion order; one whose
        line a hunk of this load holds is in `placed_notes` instead, and
        one no gap can hold (the file's shape changed under it) is
        parked."""
        shown = {file.path: file for file in files}
        out: dict[tuple[str, str], list[Note]] = {}
        for note in self._notes.values():
            if note.hunk_key != CONTEXT_KEY:
                continue
            file = shown.get(note.path)
            if file is None or diffmodel.locate([file], note.path, note.side, note.line) is not None:
                continue
            address = gap_address(file, note.side, note.line)
            if address is not None:
                out.setdefault((file.path, address), []).append(note)
        return out


def _place(
    file: diffmodel.File, keys: Sequence[str], mark: Note | Highlight
) -> tuple[int, int] | str | None:
    """(hunk index, line index) of *mark* in *file* — *keys* its hunks'
    stable keys — when the hunk it was placed in is still there under its
    key and has a line on the mark's side at the mark's index (an equal
    body always does: the key holds every line's kind). A mark outside
    every hunk is placed by its number: in the hunk that now holds the
    line, else _OUTSIDE (it is a gap's, or parked; never dropped)."""
    if mark.hunk_key == CONTEXT_KEY:
        located = diffmodel.locate([file], mark.path, mark.side, mark.line)
        return (located[1], located[2]) if located is not None else _OUTSIDE
    for hunk, key in zip(file.hunks, keys, strict=True):
        if key != mark.hunk_key:
            continue
        if not 0 <= mark.line_index < len(hunk.lines):
            return None
        entry = hunk.lines[mark.line_index]
        number = entry.new if mark.side == diffmodel.NEW else entry.old
        return (hunk.index, mark.line_index) if number is not None else None
    return None


def _placed(files: Sequence[diffmodel.File], marks: Iterable) -> dict:
    shown = {file.path: file for file in files}
    keys: dict[str, tuple[str, ...]] = {}
    out: dict[tuple[str, int], list] = {}
    for mark in marks:
        file = shown.get(mark.path)
        if file is None:
            continue
        if file.path not in keys:
            keys[file.path] = diffmodel.stable_keys(file)
        where = _place(file, keys[file.path], mark)
        if where is None or where == _OUTSIDE:
            continue
        out.setdefault((file.path, where[0]), []).append((mark, where[1]))
    return out
