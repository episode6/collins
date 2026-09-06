# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Notes and highlights on the native diff view — the GTK-free half.

A *note* is a card under a hunk anchored to one line of one side (decision
5 of ~/specs/collins/native-diff-panel.md): a summary, an optional
rationale and author, and a source — USER for the ones typed into the
view's own editor, AGENT for the ones the `annotate_diff` tool lands. A
*highlight* is an attention mark on a character range of a line, in one of
hunk's tones. Both live in a `MarkStore` for the tab's life (decision 8:
nothing is written to state.json), keyed to survive a reload: every mark
remembers the stable key of the hunk it sits in (diffmodel.stable_key —
the hunk's ranges and content), so a reload that leaves that hunk alone
keeps the mark, one that changes or drops the hunk drops it, and a load
that doesn't show the file at all parks it until one does.

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

# hunk's highlight tones (`highlight add --tone`): what the view paints a
# marked range with. MATCH is the default.
TONE_MATCH = "match"
TONE_CURRENT = "current"
TONE_INFO = "info"
TONE_WARNING = "warning"
TONE_ERROR = "error"
TONE_DIM = "dim"
TONES: tuple[str, ...] = (TONE_MATCH, TONE_CURRENT, TONE_INFO, TONE_WARNING, TONE_ERROR, TONE_DIM)

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
    says, who said it, and the key of the hunk it was placed in."""

    id: str
    source: str
    path: str
    side: str
    line: int
    summary: str
    rationale: str | None
    author: str | None
    hunk_key: str


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
    the Line (its index in hunk.lines), and the side + number it sits at."""

    file: diffmodel.File
    hunk_index: int
    line_index: int
    side: str
    line: int

    @property
    def hunk(self) -> diffmodel.Hunk:
        return self.file.hunks[self.hunk_index]

    @property
    def key(self) -> str:
        return diffmodel.stable_key(self.file, self.hunk)


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
) -> Anchor | str:
    """Where (*path*, *side*, *line* | *hunk*) lands in *files* — or the
    reason it doesn't, as one line naming the offender. *side* defaults to
    the new side; exactly one of *line* (1-based on that side) and *hunk*
    (1-based) is given; a hunk address anchors on the hunk's first line
    that has a number on the side."""
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
        return f"line {line!r} ({side}) of {path} is not in a hunk of the loaded diff"
    _file, hunk_index, line_index = located
    return Anchor(file, hunk_index, line_index, side, int(line))  # type: ignore[arg-type]


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
        self, files: Sequence[diffmodel.File], specs: Sequence[NoteSpec], source: str
    ) -> list[Note] | str:
        """Land *specs* as notes from *source*, all of them or none: the
        batch is resolved against *files* first, and the first address the
        diff doesn't carry (or an empty summary) refuses the whole batch
        with its reason. Returns the Notes in order."""
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
            anchor = resolve_anchor(files, spec.path, spec.side, spec.line, spec.hunk)
            if isinstance(anchor, str):
                return anchor
            summary = summary_text(spec.summary)
            if not summary:
                return f"{spec.path}: a note needs a summary"
            rationale = bound_text(spec.rationale) or None
            resolved.append((anchor, summary, rationale, _author(spec.author)))
        added: list[Note] = []
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
                anchor.key,
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
        for anchor, start, end, tone in resolved:
            mark = Highlight(
                self._mint("h"), anchor.file.path, anchor.side, anchor.line, start, end, tone, anchor.key
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
        the hunk it was placed in (the hunk changed, moved or went); a mark
        on a file the load doesn't show stays parked. Returns how many
        went."""
        shown = {file.path: file for file in files}
        gone = 0
        for table in (self._notes, self._highlights):
            for mark in list(table.values()):
                file = shown.get(mark.path)
                if file is None:
                    continue
                if _place(files, file, mark) is None:
                    del table[mark.id]
                    gone += 1
        return gone

    def placed_notes(self, files: Sequence[diffmodel.File]) -> dict[tuple[str, int], list[tuple[Note, int]]]:
        """The notes *files* shows, by (path, hunk index) → [(note, line
        index into the hunk's lines)], in insertion order."""
        return _placed(files, self._notes.values())

    def placed_highlights(
        self, files: Sequence[diffmodel.File]
    ) -> dict[tuple[str, int], list[tuple[Highlight, int]]]:
        return _placed(files, self._highlights.values())


def _place(
    files: Sequence[diffmodel.File], file: diffmodel.File, mark: Note | Highlight
) -> tuple[int, int] | None:
    """(hunk index, line index) of *mark* in *file*, when the hunk it was
    placed in is still there under its key and carries the line."""
    for hunk in file.hunks:
        if diffmodel.stable_key(file, hunk) != mark.hunk_key:
            continue
        for index, entry in enumerate(hunk.lines):
            number = entry.new if mark.side == diffmodel.NEW else entry.old
            if number == mark.line:
                return hunk.index, index
    return None


def _placed(files: Sequence[diffmodel.File], marks: Iterable) -> dict:
    shown = {file.path: file for file in files}
    out: dict[tuple[str, int], list] = {}
    for mark in marks:
        file = shown.get(mark.path)
        if file is None:
            continue
        where = _place(files, file, mark)
        if where is None:
            continue
        out.setdefault((file.path, where[0]), []).append((mark, where[1]))
    return out
