# New in the ghackett fork of agent-session-manager (GPL-3.0).
# Portions adapted from muzomer/hunk-commit (MIT, © 2026 hunk-jj-stage
# contributors), by way of the collins-git hunk extension's patch.ts and
# staging.ts; see collins/THIRD_PARTY_LICENSES.md.

"""The native diff panel's staging arithmetic: the partial patches a hunk
or a selection of lines becomes, and the plans the diff view's buttons
carry out.

A `git diff` describes a whole file's change; staging one hunk of it, or
three lines of one hunk, means writing a patch that describes only that
much and still applies. `write_selected_hunks` keeps the chosen hunks
verbatim and renumbers the rest's starts by what was left out;
`write_lines` keeps the chosen `+` / `-` lines and follows the rule every
line-level stager ends up with — to stage forward, the index holds the old
side, so an unselected `+` is dropped (the index never saw it) and an
unselected `-` becomes context (the index still has it); to apply in
reverse (unstaging against a `--cached` patch, discarding or reverting in
the working tree) the target holds the new side and the roles swap. Context
is always kept and every count is recomputed, so `git apply` gets a patch
whose header describes exactly what it carries (`--unidiff-zero`, since the
source diff may have had no context to keep, is gitops' business).

The planners (`plan_file`, `plan_hunk`, `plan_lines`) decide what a button
will do before anything is done: they take the File the view loaded, the
patch re-read from git at action time, and the load (`"unstaged"`,
`"staged"`, or a read-only load — a commit, `branch`, a range — where the
one action is a revert), and return a `Plan` the page's worker thread runs
through gitops, or a `Refusal` with the words the user sees. The refusals
are the extension's: binary and oversized files, renames and new or deleted
files at hunk grain (whole file only), a symlink or submodule, a path
outside the repository, and a file whose patch moved since the view loaded
it — `find_disagreement` (the hunk spans) and `same_hunks` (every line) are
compared against the fresh read before a single line number is trusted.
`describe` gives the toast.

Nothing here imports GTK or runs git: gitops runs the plans; diffview asks
for them (tests/test_gitpatch.py). Every path and line that passes through
came out of a diff of a repository the agent edits, so `unsafe_path_reason`
gates what a plan may name and no string here is ever Pango markup.
"""

from __future__ import annotations

import re
from collections.abc import Container, Iterable, Sequence
from dataclasses import dataclass, replace

from . import diffmodel
from .diffmodel import ADD, CONTEXT, DEL, NEW, OLD, File, Hunk, Line
from .i18n import _

# The two working-tree loads; every other load (a commit, `branch`, a
# range) is read-only and its one mutation is a revert into the working
# tree.
UNSTAGED = "unstaged"
STAGED = "staged"

# What a Plan asks gitops to run. The applies take the plan's patch on
# stdin; add / reset / checkout / trash take its paths.
OP_ADD = "add"
OP_RESET = "reset"
OP_APPLY_CACHED = "apply-cached"
OP_APPLY_CACHED_REVERSE = "apply-cached-reverse"
OP_APPLY_WORKTREE_REVERSE = "apply-worktree-reverse"
OP_CHECKOUT = "checkout"
OP_TRASH = "trash"
OPS: tuple[str, ...] = (
    OP_ADD, OP_RESET, OP_APPLY_CACHED, OP_APPLY_CACHED_REVERSE, OP_APPLY_WORKTREE_REVERSE,
    OP_CHECKOUT, OP_TRASH,
)

# The sign each line kind is written with, and the marker git puts after a
# side's last line when the file ends without a newline.
MARKER = {CONTEXT: " ", ADD: "+", DEL: "-"}
NO_NEWLINE_MARKER = "\\ No newline at end of file"

# The modes a patch may name for a file the hunk arithmetic understands: a
# regular file, executable or not. A symlink or submodule arrives as
# ordinary text hunks and neither is a text file.
REGULAR_MODES = frozenset({"100644", "100755"})


def _mode_name(mode: str) -> str | None:
    """What a non-regular mode is, in the user's words; None when unknown."""
    if mode == "120000":
        return _("a symbolic link")
    if mode == "160000":
        return _("a submodule")
    if mode in ("040000", "040755"):
        return _("a directory")
    return None


_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_DRIVE = re.compile(r"^[A-Za-z]:")


class RangeRefusal(ValueError):
    """Raised by `write_lines` for a selection no patch can describe."""


@dataclass(frozen=True, order=True)
class LinePos:
    """A line's place in a file's patch: the hunk's index, then the line's
    index in `hunk.lines`. Ordered by hunk, then by line."""

    hunk: int
    index: int


@dataclass(frozen=True)
class LineRange:
    """An inclusive span of positions in patch order, `start <= end`. A
    selection is never a count of cursor stops: the split layout visits a
    replaced block row by row while the stacked one visits it as the patch
    reads, and both must select the same lines."""

    start: LinePos
    end: LinePos

    def __contains__(self, pos: object) -> bool:
        return isinstance(pos, LinePos) and self.start <= pos <= self.end


@dataclass(frozen=True)
class Plan:
    """What a button does: *op* (one of OPS), the *paths* it names, the
    *patch* the applies feed git, the *confirm* question a destructive plan
    asks first (None when nothing is lost), and *done*, the toast when it
    landed. *lines* counts the `+` / `-` lines a partial patch carries;
    *hunk_index* is the hunk taken whole, or None."""

    op: str
    paths: tuple[str, ...]
    patch: str | None
    confirm: str | None
    done: str
    lines: int = 0
    hunk_index: int | None = None


@dataclass(frozen=True)
class Refusal:
    """Why nothing will be done, in the user's words. *stale* says the view
    no longer matches the disk and a reload is the fix."""

    reason: str
    stale: bool = False


@dataclass(frozen=True)
class Selection:
    """A selection of lines the view holds across a reload: the file's path,
    the hunk's `diffmodel.stable_key` and its index when the selection was
    made, the inclusive line indexes, and the load it was made on."""

    path: str
    hunk_key: str
    hunk_index: int
    first: int
    last: int
    load: object


# -- the patch text -----------------------------------------------------------


def header_lines(file: File) -> list[str]:
    """The file's own header lines, verbatim, so a patch written back out
    keeps its modes, blob hashes and rename records — details these writers
    have no reason to understand but `git apply` does."""
    lines = file.patch.removesuffix("\n").split("\n") if file.patch else []
    out: list[str] = []
    for line in lines:
        if _HUNK_HEADER.match(line):
            break
        out.append(line)
    return out


def partial_header_lines(file: File) -> list[str]:
    """The header a *partial* patch — some hunks, or some lines — is written
    with: the file's, minus its `old mode` / `new mode` pair. A patch that
    names both modes tells `git apply` to change the mode too, and a hunk or
    a range of lines is not a mode change: staging "2 lines" must not set the
    executable bit in the index, and discarding them must not take it off
    the working-tree file. Without the pair apply keeps the target's mode as
    it is, and the mode change stays behind for the file button."""
    return [line for line in header_lines(file) if not line.startswith(("old mode ", "new mode "))]


def declared_modes(file: File) -> list[str]:
    """Every file mode the header named, in the order named (both sides of
    a mode change; one mode when `index` carried it for both)."""
    modes: list[str] = []
    for mode in (file.old_mode, file.new_mode):
        if mode is not None and mode not in modes:
            modes.append(mode)
    return modes


def hunk_side_lines(hunk: Hunk, side: str) -> list[str]:
    """The lines this hunk expects to find on one side of the diff."""
    excluded = DEL if side == NEW else ADD
    return [line.text for line in hunk.lines if line.kind != excluded]


def side_count(lines: Sequence[Line], side: str) -> int:
    """How many lines of one side a hunk's lines describe."""
    excluded = DEL if side == NEW else ADD
    return sum(1 for line in lines if line.kind != excluded)


def _span(start: int, count: int) -> str:
    """One side's span the way git spells it: the count only when it is not 1."""
    return str(start) if count == 1 else f"{start},{count}"


def hunk_header(old_start: int, old_count: int, new_start: int, new_count: int, context: str = "") -> str:
    """A hunk header from explicit numbers — what a rewritten hunk needs —
    with git's function context after the closing `@@` when there is one."""
    tail = f" {context}" if context else ""
    return f"@@ -{_span(old_start, old_count)} +{_span(new_start, new_count)} @@{tail}"


def render_lines(lines: Iterable[Line]) -> list[str]:
    """Patch lines back to text, each `\\ No newline` marker after the line
    it belongs to."""
    out: list[str] = []
    for line in lines:
        out.append(MARKER[line.kind] + line.text)
        if line.no_newline:
            out.append(NO_NEWLINE_MARKER)
    return out


def _line_delta(hunk: Hunk) -> int:
    """How many lines a hunk adds to the file it applies to."""
    return side_count(hunk.lines, NEW) - side_count(hunk.lines, OLD)


def write_selected_hunks(file: File, indexes: Container[int], reverse: bool = False) -> str | None:
    """A patch carrying only the hunks in *indexes*, or None when none is.

    Dropping a hunk shifts every later hunk's position in the file the
    patch produces, so the result side's starts are renumbered by the
    running delta of what was left out: forward, the new-side starts (the
    target holds the old side exactly); in *reverse*, the old-side starts
    (the target holds the new side). `git apply` would tolerate stale
    numbers, but a patch that describes a file it does not produce is a
    patch nothing else can trust.
    """
    out = partial_header_lines(file)
    any_selected = False
    dropped = 0
    for hunk in file.hunks:
        if hunk.index not in indexes:
            dropped += _line_delta(hunk)
            continue
        any_selected = True
        old_start = hunk.old_start + dropped if reverse else hunk.old_start
        new_start = hunk.new_start if reverse else hunk.new_start - dropped
        out.append(hunk_header(old_start, hunk.old_count, new_start, hunk.new_count, hunk.context))
        out.extend(render_lines(hunk.lines))
    return "\n".join(out) + "\n" if any_selected else None


# -- selections ---------------------------------------------------------------


def locate_address(file: File, side: str, line: int) -> LinePos | None:
    """The position a `(side, line)` address names, or None when no hunk
    line carries it — a line outside every hunk, or a number that went
    stale with the file. A `-` line answers only to its old-side number and
    a `+` line only to its new-side number; a context line answers to
    either."""
    if side not in (OLD, NEW) or not isinstance(line, int) or isinstance(line, bool):
        return None
    for hunk in file.hunks:
        for index, entry in enumerate(hunk.lines):
            number = entry.old if side == OLD else entry.new
            if number == line:
                return LinePos(hunk.index, index)
    return None


def order_range(a: LinePos, b: LinePos) -> LineRange:
    """The range between two positions, whichever way round they came."""
    return LineRange(a, b) if a <= b else LineRange(b, a)


def hunk_lines_range(file: File, hunk_index: int, first: int, last: int) -> LineRange | None:
    """The range of lines *first*..*last* (inclusive indexes, either order)
    of hunk *hunk_index*, or None when the hunk or an index is not there."""
    if not 0 <= hunk_index < len(file.hunks):
        return None
    count = len(file.hunks[hunk_index].lines)
    if not (0 <= first < count and 0 <= last < count):
        return None
    return order_range(LinePos(hunk_index, first), LinePos(hunk_index, last))


def count_selected(file: File, selected: Container[LinePos]) -> tuple[int, int]:
    """The `+` and `-` lines inside the selection — what the toasts count."""
    added = removed = 0
    for hunk in file.hunks:
        for index, line in enumerate(hunk.lines):
            if line.kind == CONTEXT or LinePos(hunk.index, index) not in selected:
                continue
            if line.kind == ADD:
                added += 1
            else:
                removed += 1
    return added, removed


def _misplaced_marker(lines: Sequence[Line]) -> str | None:
    """A `\\ No newline at end of file` marker means "this is the last line
    of its side, and the file stops without a newline". Writing a marked
    line anywhere but last on a side would describe a file that cannot
    exist — which is what a demoted `-last` followed by a kept `+` does.
    None when every marker sits where it must, else the offending side."""
    for side in (OLD, NEW):
        excluded = DEL if side == NEW else ADD
        on_side = [line for line in lines if line.kind != excluded]
        if any(line.no_newline for line in on_side[:-1]):
            return side
    return None


def _select_hunk_lines(hunk: Hunk, selected: Container[LinePos], reverse: bool) -> list[Line] | None:
    """One hunk's lines with only the selection's changes in it, or None
    when the selection holds no `+` / `-` of this hunk."""
    demoted = ADD if reverse else DEL
    out: list[Line] = []
    changed = False
    for index, line in enumerate(hunk.lines):
        if line.kind == CONTEXT:
            out.append(line)
        elif LinePos(hunk.index, index) in selected:
            out.append(line)
            changed = True
        elif line.kind == demoted:
            out.append(Line(CONTEXT, line.text, line.old, line.new, line.no_newline))
        # The other unselected kind is dropped, its marker with it.
    return out if changed else None


def write_lines(file: File, selected: Container[LinePos], reverse: bool = False) -> str | None:
    """The partial patch for a selection of lines, or None when it holds no
    `+` / `-` line at all.

    Every hunk's counts are recomputed from what was emitted. The source
    side's start stays where git put it (the target still looks exactly
    like that side), while the result side's start is shifted by what the
    earlier hunks — omitted or trimmed — no longer contribute: forward, the
    new-side start moves by the added lines left out; in *reverse*, the
    old-side start moves by the removed lines left out. The header goes
    out without a mode change (`partial_header_lines`). Raises RangeRefusal
    when a `\\ No newline` marker would land mid-side.
    """
    out = partial_header_lines(file)
    shift = 0
    any_selected = False
    for hunk in file.hunks:
        old_total, new_total = side_count(hunk.lines, OLD), side_count(hunk.lines, NEW)
        original_delta = old_total - new_total if reverse else new_total - old_total
        lines = _select_hunk_lines(hunk, selected, reverse)
        if lines is None:
            shift += original_delta
            continue
        if _misplaced_marker(lines) is not None:
            raise RangeRefusal(_("select the whole end-of-file change"))
        old_count, new_count = side_count(lines, OLD), side_count(lines, NEW)
        if reverse:
            header = hunk_header(hunk.old_start - shift, old_count, hunk.new_start, new_count, hunk.context)
        else:
            header = hunk_header(hunk.old_start, old_count, hunk.new_start - shift, new_count, hunk.context)
        out.append(header)
        out.extend(render_lines(lines))
        shift += original_delta - (old_count - new_count if reverse else new_count - old_count)
        any_selected = True
    return "\n".join(out) + "\n" if any_selected else None


def write_selected_lines(
    file: File, hunk_index: int, first: int, last: int, reverse: bool = False
) -> str | None:
    """`write_lines` for lines *first*..*last* of one hunk — the view's
    selection, which is always inside one hunk. None for a selection that
    names no line of the file, or no changed line."""
    selected = hunk_lines_range(file, hunk_index, first, last)
    if selected is None:
        return None
    return write_lines(file, selected, reverse)


# -- the cross-checks and guards ----------------------------------------------


def hunk_spans(file: File) -> list[tuple[int, int]]:
    """Each hunk's inclusive new-side span, in order — what the view knows
    of a file's hunks and `find_disagreement` checks the fresh patch by."""
    return [diffmodel.hunk_range(hunk, NEW) for hunk in file.hunks]


def find_disagreement(fresh: File, spans: Sequence[tuple[int, int] | None]) -> str | None:
    """Whether the fresh patch agrees with what the view shows: the same
    hunk count, and the same new-side span per index (a None span is not
    checked). If the two disagreed about what hunk 2 is, staging hunk 2
    would stage something else — so ask, and refuse when the answer is no.
    None when they agree."""
    if len(fresh.hunks) != len(spans):
        return _("the view shows {shown} hunk(s) where the patch has {actual}").format(
            shown=len(spans), actual=len(fresh.hunks)
        )
    for hunk, span in zip(fresh.hunks, spans, strict=False):
        if span is None:
            continue
        start, end = diffmodel.hunk_range(hunk, NEW)
        if (start, end) != tuple(span):
            return _(
                "hunk {n} spans lines {start}-{end} in the patch but {shown_start}-{shown_end} in the view"
            ).format(n=hunk.index + 1, start=start, end=end, shown_start=span[0], shown_end=span[1])
    return None


def _kind_words(kind: str) -> str:
    if kind == CONTEXT:
        return _("context")
    return _("an addition") if kind == ADD else _("a removal")


def same_hunks(loaded: File, fresh: File) -> str | None:
    """None when the loaded patch and the fresh one describe the same hunks
    — the same count, and per hunk the same line kinds and texts — else a
    short reason. This is the "did the working copy change since the view
    loaded" check for a selection: the line indexes it is built from came
    out of the view, and they mean nothing against a patch with other lines
    in it. Headers are not compared; `find_disagreement` covers the spans."""
    if len(loaded.hunks) != len(fresh.hunks):
        return _("the view shows {shown} hunk(s) but the disk has {actual}").format(
            shown=len(loaded.hunks), actual=len(fresh.hunks)
        )
    for shown, current in zip(loaded.hunks, fresh.hunks, strict=False):
        n = shown.index + 1
        if len(shown.lines) != len(current.lines):
            return _("hunk {n} has {shown} lines in the view but {actual} on disk").format(
                n=n, shown=len(shown.lines), actual=len(current.lines)
            )
        for index, (line, other) in enumerate(zip(shown.lines, current.lines, strict=False)):
            if line.kind != other.kind:
                return _("hunk {n} differs: line {line} is {shown} in the view but {actual} on disk").format(
                    n=n, line=index + 1, shown=_kind_words(line.kind), actual=_kind_words(other.kind)
                )
            if line.text != other.text:
                return _(
                    "hunk {n} differs: line {line} is `{shown}` in the view but `{actual}` on disk"
                ).format(n=n, line=index + 1, shown=line.text, actual=other.text)
    return None


def unsupported_mode_reason(modes: Iterable[str]) -> str | None:
    """Why the file these modes describe cannot be staged by the hunk, or
    None when it can. An allowlist: partial staging of a symlink or a
    submodule is not something this code has reasoned about."""
    for mode in modes:
        if mode in REGULAR_MODES:
            continue
        name = _mode_name(mode)
        if name is None:
            return _("it has an unrecognised file mode ({mode})").format(mode=mode)
        return _("it is {what}, which hunks cannot describe").format(what=name)
    return None


def unsafe_path_reason(path: object) -> str | None:
    """Why this path must not be touched, or None when it is safe. Every
    path here is parsed out of patch text; git never emits an absolute
    path, a `..` segment or a leading dash for a working copy, so refusing
    them costs nothing (and a leading dash would read as an option)."""
    if not isinstance(path, str) or path == "":
        return _("the patch does not name a file")
    if path.startswith(("/", "\\")) or _DRIVE.match(path):
        return _("it is an absolute path")
    if ".." in re.split(r"[/\\]", path):
        return _("it points outside the repository with a `..` segment")
    if path.startswith("-"):
        return _("it starts with a dash")
    if "\0" in path or "\n" in path:
        return _("it carries a control character")
    return None


def unreadable_reason(file: File) -> str | None:
    """Why a File is not one to write partial patches from: diffmodel keeps
    a stanza whose hunk body stopped short or held a stray line, minus that
    hunk and the ones after it — a patch with `@@` headers it has no hunks
    for is one this code did not read whole. None for a placeholder (a
    binary or an oversized file has no hunks by design) or a clean parse."""
    if file.kind in (diffmodel.KIND_BINARY, diffmodel.KIND_TOO_LARGE):
        return None
    headers = sum(1 for line in file.patch.split("\n") if _HUNK_HEADER.match(line))
    if headers != len(file.hunks):
        return _("hunk {n} could not be read").format(n=len(file.hunks) + 1)
    return None


def parse_file_patch(text: object, path: str, previous_path: str | None = None) -> File | None:
    """The File a single-file `git diff -- <path>` (or `<old> <new>` for a
    rename) re-read at action time describes, or None when the text has no
    stanza for it. A stream with exactly one stanza is taken as the file's
    whatever path it names (the caller then gates that path)."""
    files = diffmodel.parse(text)
    wanted = {path} | ({previous_path} if previous_path else set())
    for file in files:
        if file.path in wanted or (file.previous_path is not None and file.previous_path in wanted):
            return file
    return files[0] if len(files) == 1 else None


# -- the planners -------------------------------------------------------------


def working_side(load: object) -> str | None:
    """UNSTAGED or STAGED for a working-tree load, None for a read-only one."""
    return load if load in (UNSTAGED, STAGED) else None


def _is_rename(file: File) -> bool:
    return file.kind == diffmodel.KIND_RENAME or (
        file.previous_path is not None and file.previous_path != file.path
    )


def is_deletion(file: File) -> bool:
    """Whether the file is gone on the new side: KIND_DELETED, or a binary or
    oversized placeholder whose header says `deleted file mode` (diffmodel
    keeps the placeholder kind, so the header is where the deletion shows)."""
    if file.kind == diffmodel.KIND_DELETED:
        return True
    return any(line.startswith("deleted file mode ") for line in header_lines(file))


def _paths(file: File) -> tuple[str, ...]:
    if file.previous_path is not None and file.previous_path != file.path:
        return (file.previous_path, file.path)
    return (file.path,)


def _lines_words(count: int) -> str:
    return _("1 line") if count == 1 else _("{n} lines").format(n=count)


# The action words: what the guards say a file can only be taken whole with.
_STAGE, _UNSTAGE, _DISCARD, _REVERT = "stage", "unstage", "discard", "revert"


@dataclass(frozen=True)
class _Wording:
    """The words one action's guards use: *action* (one of the four keys
    above) and the file button the user is sent to instead."""

    action: str

    @property
    def verb(self) -> str:
        words = {_STAGE: _("stage"), _UNSTAGE: _("unstage"), _DISCARD: _("discard"), _REVERT: _("revert")}
        return words[self.action]

    @property
    def button(self) -> str:
        return {
            _STAGE: _("Stage file"), _UNSTAGE: _("Unstage file"),
            _DISCARD: _("Discard file"), _REVERT: _("Revert file"),
        }[self.action]

    def whole(self, path: str, why: str) -> str:
        """Why *path* can only be taken whole: *why* is binary, too-large,
        untracked, rename, new or deleted."""
        if self.action == _DISCARD:
            if why == "binary":
                return _("{path} is binary: use git from a shell").format(path=path)
            if why == "too-large":
                return _("{path} is too large to discard by hunk: use git from a shell").format(path=path)
            if why == "untracked":
                return _("{path} is untracked: Discard file moves it to the trash").format(path=path)
            if why == "deleted":
                return _("{path} is deleted: Discard file restores it whole").format(path=path)
            return _(
                "discard reverts changes inside a modified file: "
                "use git from a shell for a new or renamed file"
            )
        button = self.button
        if why == "binary":
            if self.action == _REVERT:
                return _("{path} is binary: use git from a shell").format(path=path)
            return _("{path} is binary: use {button}").format(path=path, button=button)
        if why == "too-large":
            return _("{path} is too large to {action} by hunk: use {button}").format(
                path=path, action=self.verb, button=button
            )
        if why == "untracked":
            return _("{path} is untracked: use {button}").format(path=path, button=button)
        if why == "rename":
            return _("renames {action} whole: use {button}").format(action=self.verb, button=button)
        return _("{path} is a new or deleted file: use {button}").format(path=path, button=button)

    def nothing(self, path: str) -> Refusal:
        return Refusal(
            _("nothing to {action} in {path}: reloading").format(action=self.verb, path=path), stale=True
        )

    def changed(self, path: str, detail: str) -> Refusal:
        return Refusal(
            _("{path} changed since it was loaded, reloading ({detail})").format(path=path, detail=detail),
            stale=True,
        )


_DELETED_WHOLE = "deleted"


def _guard_partial(
    file: File, fresh_patch: object, wording: _Wording, deleted_whole: bool = False
) -> Refusal | File | str:
    """The guards a hunk or a selection meets before any line number is
    read, in the order the user would want to hear about them: what the view
    says of the file, then what the fresh patch says, then whether the two
    still agree. Returns the fresh File when everything holds. With
    *deleted_whole*, a patch that deletes the file comes back as the string
    _DELETED_WHOLE instead of a refusal, before the hunk comparisons: the
    caller takes the file whole."""
    if file.kind == diffmodel.KIND_BINARY:
        return Refusal(wording.whole(file.path, "binary"))
    if file.kind == diffmodel.KIND_TOO_LARGE:
        return Refusal(wording.whole(file.path, "too-large"))
    if file.untracked:
        return Refusal(wording.whole(file.path, "untracked"))
    if _is_rename(file):
        return Refusal(wording.whole(file.path, "rename"))
    if not isinstance(fresh_patch, str) or not fresh_patch.strip():
        return wording.nothing(file.path)
    fresh = parse_file_patch(fresh_patch, file.path, file.previous_path)
    unreadable = _("no stanza for it") if fresh is None else unreadable_reason(fresh)
    if fresh is None or unreadable is not None:
        return Refusal(_("cannot read the patch for {path}: {why}").format(path=file.path, why=unreadable))
    if fresh.kind == diffmodel.KIND_BINARY:
        return Refusal(wording.whole(file.path, "binary"))
    unsafe = unsafe_path_reason(fresh.path)
    if unsafe is not None:
        return Refusal(_("refusing {path}: {why}").format(path=fresh.path, why=unsafe))
    mode = unsupported_mode_reason(declared_modes(fresh))
    if mode is not None:
        return Refusal(
            _("cannot {action} {path} by hunk: {why}").format(action=wording.verb, path=file.path, why=mode)
        )
    if fresh.kind == diffmodel.KIND_RENAME:
        return Refusal(wording.whole(file.path, "rename"))
    if fresh.kind == diffmodel.KIND_DELETED and deleted_whole:
        return _DELETED_WHOLE
    if fresh.kind in (diffmodel.KIND_NEW, diffmodel.KIND_DELETED):
        # A partial patch for a new or deleted file would need its header
        # rewritten (no `new file mode`, a real path for `/dev/null`).
        return Refusal(wording.whole(file.path, "new" if fresh.kind == diffmodel.KIND_NEW else "deleted"))
    disagreement = find_disagreement(fresh, hunk_spans(file))
    if disagreement is not None:
        return wording.changed(file.path, disagreement)
    shown_unreadable = unreadable_reason(file)
    if shown_unreadable is not None:
        return Refusal(
            _("cannot read the view's patch for {path}: {why}").format(path=file.path, why=shown_unreadable)
        )
    drift = same_hunks(file, fresh)
    if drift is not None:
        return wording.changed(file.path, drift)
    return fresh


def _write_range(fresh: File, selected: LineRange, reverse: bool) -> Refusal | tuple[str, int]:
    """A partial patch for the selection and the number of changed lines it
    carries, or the refusal when the selection cannot make one."""
    try:
        patch = write_lines(fresh, selected, reverse)
    except RangeRefusal as refusal:
        return Refusal(str(refusal))
    if patch is None:
        return Refusal(_("no changes in the selection"))
    added, removed = count_selected(fresh, selected)
    return patch, added + removed


def _select(fresh: File, hunk_index: int, first: int, last: int, path: str) -> LineRange | Refusal:
    if not 0 <= hunk_index < len(fresh.hunks):
        return Refusal(_("no hunk {n} in {path}").format(n=hunk_index + 1, path=path))
    selected = hunk_lines_range(fresh, hunk_index, first, last)
    if selected is None:
        return Refusal(
            _("the selection is not inside hunk {n} of {path}").format(n=hunk_index + 1, path=path)
        )
    return selected


def plan_file(file: File, load: object, discard: bool = False) -> Plan | Refusal:
    """What the file button does: on the unstaged load *Stage file* (`add`,
    both paths of a rename) or, with *discard*, *Discard file* — a tracked
    file's changes checked out of the index, a deleted file restored from
    it, an untracked file moved to the trash (never unlinked), each after a
    confirmation; on the staged load *Unstage file* (`reset`); on a
    read-only load *Revert file*, that diff's whole patch applied in
    reverse to the working tree after a confirmation (a binary cannot be
    reverted from a patch without its data)."""
    unsafe = unsafe_path_reason(file.path)
    if unsafe is None and file.previous_path is not None:
        unsafe = unsafe_path_reason(file.previous_path)
    if unsafe is not None:
        return Refusal(_("refusing {path}: {why}").format(path=file.path, why=unsafe))
    side = working_side(load)
    if side is None:
        wording = _Wording(_REVERT)
        if file.kind == diffmodel.KIND_BINARY:
            return Refusal(wording.whole(file.path, "binary"))
        if file.kind == diffmodel.KIND_TOO_LARGE:
            return Refusal(_("{path} is too large to revert: use git from a shell").format(path=file.path))
        if not file.patch.strip():
            return wording.nothing(file.path)
        return Plan(
            OP_APPLY_WORKTREE_REVERSE,
            _paths(file),
            file.patch,
            _("Revert {path} in the working tree? This cannot be undone.").format(path=file.path),
            _("Reverted {path}").format(path=file.path),
            lines=file.additions + file.deletions,
        )
    if discard:
        if side == STAGED:
            return Refusal(_("Discard works on the working tree: load the Unstaged view"))
        if file.untracked:
            return Plan(
                OP_TRASH, (file.path,), None,
                _("Move {path} to the trash?").format(path=file.path),
                _("Moved {path} to the trash").format(path=file.path),
            )
        if is_deletion(file):
            return Plan(
                OP_CHECKOUT, (file.path,), None,
                _("Restore {path} from the index?").format(path=file.path),
                _("Restored {path}").format(path=file.path),
            )
        if _is_rename(file) or file.kind == diffmodel.KIND_NEW:
            return Refusal(_Wording(_DISCARD).whole(file.path, "new"))
        return Plan(
            OP_CHECKOUT, (file.path,), None,
            _("Discard the changes to {path}? This cannot be undone.").format(path=file.path),
            _("Discarded the changes to {path}").format(path=file.path),
            lines=file.additions + file.deletions,
        )
    if side == UNSTAGED:
        return Plan(OP_ADD, _paths(file), None, None, _("Staged {path}").format(path=file.path))
    return Plan(OP_RESET, _paths(file), None, None, _("Unstaged {path}").format(path=file.path))


def plan_hunk(
    file: File, hunk_index: int | None, load: object, fresh_patch: object, discard: bool = False
) -> Plan | Refusal:
    """What the hunk button does for hunk *hunk_index* of *file* (the File
    the view loaded), given the file's patch re-read from git now.

    On a working-tree load without *discard*: a partial patch for `git
    apply --cached` (reversed on the staged load), or the whole file where
    a single hunk makes no sense (an untracked file, a rename, no hunk
    selected — *hunk_index* None is the file header's button), or a
    refusal. The refusals come first whatever *hunk_index* is: a binary or
    oversized file has no hunk to select, and the hunk button on one must
    say "use Stage file" rather than quietly become it. With *discard*, the
    hunk is taken back out of the working tree (a deleted file is restored
    whole); on a read-only load it is reverted into the working tree. Both
    ask first.
    """
    side = working_side(load)
    if side is None:
        return _plan_revert(file, hunk_index, None, fresh_patch)
    if discard:
        return _plan_discard(file, hunk_index, None, load, fresh_patch)
    stage = side == UNSTAGED
    wording = _Wording(_STAGE if stage else _UNSTAGE)
    if file.kind == diffmodel.KIND_BINARY:
        return Refusal(wording.whole(file.path, "binary"))
    if file.kind == diffmodel.KIND_TOO_LARGE:
        return Refusal(wording.whole(file.path, "too-large"))
    if file.untracked or _is_rename(file):
        return plan_file(file, load)
    if not isinstance(fresh_patch, str) or not fresh_patch.strip():
        return plan_file(file, load) if file.kind == diffmodel.KIND_NEW else wording.nothing(file.path)
    fresh = parse_file_patch(fresh_patch, file.path, file.previous_path)
    unreadable = _("no stanza for it") if fresh is None else unreadable_reason(fresh)
    if fresh is None or unreadable is not None:
        return Refusal(_("cannot read the patch for {path}: {why}").format(path=file.path, why=unreadable))
    if fresh.kind == diffmodel.KIND_BINARY:
        return Refusal(wording.whole(file.path, "binary"))
    unsafe = unsafe_path_reason(fresh.path)
    if unsafe is not None:
        return Refusal(_("refusing {path}: {why}").format(path=fresh.path, why=unsafe))
    mode = unsupported_mode_reason(declared_modes(fresh))
    if mode is not None:
        return Refusal(
            _("cannot {action} {path} by hunk: {why}").format(action=wording.verb, path=file.path, why=mode)
        )
    if hunk_index is None:
        return plan_file(file, load)  # the file header's button, not a hunk's
    disagreement = find_disagreement(fresh, hunk_spans(file))
    if disagreement is not None:
        return wording.changed(file.path, disagreement)
    if not 0 <= hunk_index < len(fresh.hunks):
        return Refusal(_("no hunk {n} in {path}").format(n=hunk_index + 1, path=file.path))
    patch = write_selected_hunks(fresh, {hunk_index}, reverse=not stage)
    if patch is None:
        return Refusal(_("nothing to {action} in {path}").format(action=wording.verb, path=file.path))
    hunk = fresh.hunks[hunk_index]
    changed = sum(1 for line in hunk.lines if line.kind != CONTEXT)
    done = _("Staged hunk {n} of {path}") if stage else _("Unstaged hunk {n} of {path}")
    return Plan(
        OP_APPLY_CACHED if stage else OP_APPLY_CACHED_REVERSE,
        (fresh.path,),
        patch,
        None,
        done.format(n=hunk_index + 1, path=file.path),
        lines=changed,
        hunk_index=hunk_index,
    )


def plan_lines(
    file: File,
    hunk_index: int,
    first: int,
    last: int,
    load: object,
    fresh_patch: object,
    discard: bool = False,
) -> Plan | Refusal:
    """What the lines button does for lines *first*..*last* (inclusive
    indexes into the hunk's lines, either order) of hunk *hunk_index*: a
    partial patch for `git apply --cached` (reversed on the staged load),
    with *discard* one taken back out of the working tree, on a read-only
    load one reverted into it — or a refusal. There is no whole-file
    fall-through here: a selection names lines, and a file that can only
    go whole is told so. A selection meets more guards than a hunk: its
    indexes came out of the view, so besides the hunk spans
    (`find_disagreement`) the view's lines are compared with the fresh ones
    (`same_hunks`) before either number is trusted."""
    side = working_side(load)
    if side is None:
        return _plan_revert(file, hunk_index, (first, last), fresh_patch)
    if discard:
        return _plan_discard(file, hunk_index, (first, last), load, fresh_patch)
    stage = side == UNSTAGED
    wording = _Wording(_STAGE if stage else _UNSTAGE)
    guard = _guard_partial(file, fresh_patch, wording)
    if isinstance(guard, Refusal):
        return guard
    if isinstance(guard, str):  # not asked for without deleted_whole; here for the types
        return Refusal(wording.whole(file.path, "deleted"))
    selected = _select(guard, hunk_index, first, last, file.path)
    if isinstance(selected, Refusal):
        return selected
    written = _write_range(guard, selected, reverse=not stage)
    if isinstance(written, Refusal):
        return written
    patch, count = written
    done = _("Staged {lines} of {path}") if stage else _("Unstaged {lines} of {path}")
    return Plan(
        OP_APPLY_CACHED if stage else OP_APPLY_CACHED_REVERSE,
        (guard.path,),
        patch,
        None,
        done.format(lines=_lines_words(count), path=file.path),
        lines=count,
    )


def _plan_discard(
    file: File, hunk_index: int | None, lines: tuple[int, int] | None, load: object, fresh_patch: object
) -> Plan | Refusal:
    """Discard: the patch `git apply --reverse` takes back out of the working
    tree — the hunk, or the selected lines — or, for a file deleted in the
    working tree, the restore that brings it back whole. Only the unstaged
    load qualifies (the staged one shows the index, which a discard does
    not touch), and only a plain modification has an "inside" to revert
    part of: a new or renamed file is refused, a deleted one restored when
    the hunk is asked for (a selection in it is refused — the restore is the
    whole file). A whole hunk goes through the range writer too, as the
    range of all its lines: that renumbers the earlier-omitted hunks' starts
    for a reverse apply (the working tree holds the patch's new side
    exactly, so it is the old-side starts that move)."""
    if working_side(load) == STAGED:
        return Refusal(_("Discard works on the working tree: load the Unstaged view"))
    wording = _Wording(_DISCARD)
    restore = Plan(
        OP_CHECKOUT, (file.path,), None,
        _("Restore {path} from the index?").format(path=file.path),
        _("Restored {path}").format(path=file.path),
    )
    if lines is None and is_deletion(file) and not file.untracked:
        # The view says the file is gone: no patch to read — `git checkout`
        # puts the index's copy back, a binary's included. An empty patch
        # means it is already back and the view is behind.
        if not isinstance(fresh_patch, str) or not fresh_patch.strip():
            return wording.nothing(file.path)
        return restore
    guard = _guard_partial(file, fresh_patch, wording, deleted_whole=lines is None)
    if isinstance(guard, Refusal):
        return guard
    if isinstance(guard, str):
        return restore
    if lines is None:
        if hunk_index is None:
            return Refusal(_("select a hunk: Discard hunk takes a hunk, Discard lines a selection"))
        if not 0 <= hunk_index < len(guard.hunks):
            return Refusal(_("no hunk {n} in {path}").format(n=hunk_index + 1, path=file.path))
        hunk = guard.hunks[hunk_index]
        whole = LineRange(LinePos(hunk_index, 0), LinePos(hunk_index, max(len(hunk.lines) - 1, 0)))
        written = _write_range(guard, whole, reverse=True)
        if isinstance(written, Refusal):
            return Refusal(
                _("nothing to discard in hunk {n} of {path}").format(n=hunk_index + 1, path=file.path)
            )
        patch, count = written
        return Plan(
            OP_APPLY_WORKTREE_REVERSE,
            (guard.path,),
            patch,
            _("Discard hunk {n} in {path}? This cannot be undone.").format(n=hunk_index + 1, path=file.path),
            _("Discarded hunk {n} of {path}").format(n=hunk_index + 1, path=file.path),
            lines=count,
            hunk_index=hunk_index,
        )
    if hunk_index is None:
        return Refusal(_("select a hunk: Discard hunk takes a hunk, Discard lines a selection"))
    selected = _select(guard, hunk_index, lines[0], lines[1], file.path)
    if isinstance(selected, Refusal):
        return selected
    written = _write_range(guard, selected, reverse=True)
    if isinstance(written, Refusal):
        return written
    patch, count = written
    return Plan(
        OP_APPLY_WORKTREE_REVERSE,
        (guard.path,),
        patch,
        _("Discard {lines} in {path}? This cannot be undone.").format(
            lines=_lines_words(count), path=file.path
        ),
        _("Discarded {lines} of {path}").format(lines=_lines_words(count), path=file.path),
        lines=count,
    )


def _plan_revert(
    file: File, hunk_index: int | None, lines: tuple[int, int] | None, fresh_patch: object
) -> Plan | Refusal:
    """Revert, from a commit, `branch` or a range: the hunk or the selected
    lines of that diff applied in reverse to the working tree, after a
    confirmation. The "fresh" patch is that diff's file re-read (`git show
    <ref> -- <path>`); it only moves when the ref does. A new or deleted
    file, a rename and a binary revert whole or not at all."""
    wording = _Wording(_REVERT)
    guard = _guard_partial(file, fresh_patch, wording)
    if isinstance(guard, Refusal):
        return guard
    if isinstance(guard, str):
        return Refusal(wording.whole(file.path, "deleted"))
    if hunk_index is None:
        return Refusal(_("select a hunk: Revert hunk takes a hunk, Revert lines a selection"))
    if lines is None:
        if not 0 <= hunk_index < len(guard.hunks):
            return Refusal(_("no hunk {n} in {path}").format(n=hunk_index + 1, path=file.path))
        hunk = guard.hunks[hunk_index]
        selected = LineRange(LinePos(hunk_index, 0), LinePos(hunk_index, max(len(hunk.lines) - 1, 0)))
    else:
        found = _select(guard, hunk_index, lines[0], lines[1], file.path)
        if isinstance(found, Refusal):
            return found
        selected = found
    written = _write_range(guard, selected, reverse=True)
    if isinstance(written, Refusal):
        if lines is None:
            return Refusal(
                _("nothing to revert in hunk {n} of {path}").format(n=hunk_index + 1, path=file.path)
            )
        return written
    patch, count = written
    if lines is None:
        confirm = _("Revert hunk {n} of {path} in the working tree? This cannot be undone.").format(
            n=hunk_index + 1, path=file.path
        )
        done = _("Reverted hunk {n} of {path}").format(n=hunk_index + 1, path=file.path)
    else:
        confirm = _("Revert {lines} of {path} in the working tree? This cannot be undone.").format(
            lines=_lines_words(count), path=file.path
        )
        done = _("Reverted {lines} of {path}").format(lines=_lines_words(count), path=file.path)
    return Plan(
        OP_APPLY_WORKTREE_REVERSE, (guard.path,), patch, confirm, done,
        lines=count, hunk_index=hunk_index if lines is None else None,
    )


def describe(plan: Plan | Refusal) -> str:
    """The toast: what a plan did once it landed, or why it was refused."""
    return plan.reason if isinstance(plan, Refusal) else plan.done


# -- a selection across reloads -----------------------------------------------


def select(file: File, hunk_index: int, first: int, last: int, load: object) -> Selection | None:
    """The Selection for lines *first*..*last* of hunk *hunk_index*, keyed
    by the hunk's stable key so it can be found again after a reload; None
    when the hunk or an index is not there."""
    selected = hunk_lines_range(file, hunk_index, first, last)
    if selected is None:
        return None
    hunk = file.hunks[hunk_index]
    return Selection(
        file.path,
        diffmodel.stable_key(file, hunk),
        hunk_index,
        selected.start.index,
        selected.end.index,
        load,
    )


def rebind_selection(selection: Selection, files: Iterable[File], load: object) -> Selection | None:
    """After a reload: the selection re-pointed at its hunk's new index when
    the same load shows a file with its path whose hunk still has the very
    same lines (its stable key — the spans and a digest of the lines), else
    None: a hunk that moved or changed, a file that is gone, another load."""
    if load != selection.load:
        return None
    file = diffmodel.find_file(files, selection.path)
    if file is None:
        return None
    for hunk in file.hunks:
        if diffmodel.stable_key(file, hunk) == selection.hunk_key:
            return replace(selection, hunk_index=hunk.index)
    return None
