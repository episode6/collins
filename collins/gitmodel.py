# New in the ghackett fork of agent-session-manager (GPL-3.0).
# Portions adapted from sadick254/hunk-commit-log (the log format and its
# parser) and joshedler/hunk-git-lite (the status parser) (MIT, © 2026
# Sadick, © 2026 Josh Edler); see collins/THIRD_PARTY_LICENSES.md.

"""The git page's panels, as pure functions over what git reports: the
commits list's rows and the files list's sections.

Nothing here runs git or imports a widget. gitops runs git and hands back
the Commits and Status parsed here; the page summarises the diff it read
into FileSummaries (one per file of the loaded diff, with its counts and
rename pair); the sidebar widget (gitsidebar) asks this module which rows
to draw, which of them is the loaded one, and what the confirms and
toasts say — so the panels can be unit-tested without a display
(tests/test_gitmodel.py).

Everything that arrives here is foreign content — a subject line, a path,
a branch name — and is bounded before a widget sees it: subjects are cut to
SUBJECT_MAX_CHARS, paths longer than PATH_MAX_CHARS are dropped, and no
list grows past MAX_ROWS. The widget then puts every string through
`Gtk.Label.set_text`, never Pango markup.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass

from . import gitloads
from .i18n import _

# `git log` as parse_log reads it: NUL between the fields (sha, abbreviated
# sha, subject), a record separator after each commit — the one delimiter a
# subject can't carry (git strips control characters from `%s`), so a
# subject with a comma, a quote or a NUL-free anything survives.
LOG_FORMAT = "--format=%H%x00%h%x00%s%x1e"
# Bounds on foreign content (see the module docstring).
SUBJECT_MAX_CHARS = 200
PATH_MAX_CHARS = gitloads.MAX_PATH_CHARS
MAX_ROWS = 2000
# Between the names of branches sharing one commit on a header row.
TWIN_SEPARATOR = " / "

# The status letters a row may carry: git's own (M A D R T C, U for an
# unmerged path) plus `?` for untracked. Anything else — a code a future git
# adds — drops the row rather than colouring it wrong.
STATUS_CODES = frozenset("MADRTCU?")
# The groups of the commits list, top to bottom: the working tree row (a
# group of its own, above every header, so no caret folds it), the current
# branch (absent when the default branch is checked out: its group would
# repeat the default's), one group per branch of the stack under it
# (stack_group(name), the nearest first), the default branch. The row kinds
# in them.
WORKTREE_GROUP = "worktree"
CURRENT_GROUP = "current"
DEFAULT_GROUP = "default"
STACK_GROUP_PREFIX = "stack:"
ROW_KINDS: tuple[str, ...] = ("header", "worktree", "commit", "more")
# The row ids the widget and the e2e check address rows by.
WORKTREE_ROW_ID = "worktree"

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_ABBREV_SHA = re.compile(r"^[0-9a-f]{4,40}$")


@dataclass(frozen=True)
class Commit:
    """One commit as the commits list shows it."""

    sha: str
    abbrev: str
    subject: str


@dataclass(frozen=True)
class StatusRow:
    """One path in `git status`, on one side (index or working tree); *code*
    is one of STATUS_CODES, *previous_path* set only for a rename or copy."""

    path: str
    code: str
    previous_path: str | None = None


@dataclass(frozen=True)
class Status:
    """The working tree's changes, split the way the files list shows them."""

    unstaged: tuple[StatusRow, ...] = ()
    staged: tuple[StatusRow, ...] = ()


@dataclass(frozen=True)
class BranchRef:
    """A branch as the model needs it: its name and the ref git is given for
    it (`main`, or `origin/main` when only the remote has it), plus its
    *twins* — the other local branches at the same commit, which share
    its row (`a / b`) rather than each getting a group of nothing."""

    name: str
    target: str
    twins: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return branch_label(self.name, self.twins)


def branch_label(name: str, twins: Iterable[str]) -> str:
    """The header's words for a branch and its twins: `name / twin / twin`,
    *name* itself left out of the twins if it is listed there."""
    return TWIN_SEPARATOR.join([name, *(twin for twin in twins if twin != name)])


@dataclass(frozen=True)
class BranchPage:
    """One branch of the stack as the commits list shows it: the branch,
    the page of commits read for it (`<the branch below>..<branch>`), and
    whether a `load more…` row is due."""

    branch: BranchRef
    commits: tuple[Commit, ...] = ()
    more: bool = False


@dataclass(frozen=True)
class Row:
    """One line of the commits list. *load* is what a click loads (a
    gitloads.Loaded), None where a click does nothing on its own (the
    default branch's header; `load more…`, which pages instead)."""

    id: str
    kind: str
    group: str
    label: str
    load: gitloads.Loaded | None = None
    sha: str | None = None
    abbrev: str | None = None
    unpushed: bool = False


@dataclass(frozen=True)
class FileSummary:
    """One file of the loaded diff as the files list reads it (the page
    builds one per diffmodel.File): an id, the path, *previous_path* for
    a rename, the line counts and the hunk count — a binary change lists
    0/0/0, which is how its row reads `bin`."""

    id: str
    path: str
    previous_path: str | None
    additions: int
    deletions: int
    hunk_count: int


@dataclass(frozen=True)
class FileRow:
    """One line of the files list. *live* rows come from the loaded diff's
    own files (with counts, and a click reveals); the others from `git
    status` (a click reloads to that side first). *code* is a STATUS_CODES
    letter, or None when nothing said (a live row with no status to match
    it to)."""

    path: str
    code: str | None = None
    previous_path: str | None = None
    additions: int | None = None
    deletions: int | None = None
    live: bool = True
    hunk_count: int | None = None

    @property
    def binary(self) -> bool:
        """Whether the file reads as binary: a FileSummary carries no
        such flag, but a binary change is the one that lists no hunk and no
        line counts — and a text file with nothing to show wouldn't be
        listed at all."""
        return self.live and self.additions == 0 and self.deletions == 0 and self.hunk_count == 0


@dataclass(frozen=True)
class FileSections:
    """What the files list draws: one flat list (*flat*, any load but the
    working tree), or the two working-tree sides with *live* naming the one
    the page has loaded ("unstaged" | "staged") — and, while an operation
    is half-finished, its unmerged paths in *conflicts* of their own,
    above the two sides (they are unstaged-side rows: live on the unstaged
    load, a reload there from the staged one)."""

    mode: str
    live: str | None = None
    unstaged: tuple[FileRow, ...] = ()
    staged: tuple[FileRow, ...] = ()
    flat: tuple[FileRow, ...] = ()
    conflicts: tuple[FileRow, ...] = ()


# -- parsers ------------------------------------------------------------------------


def parse_log(text: str) -> list[Commit]:
    """`git log` output written with LOG_FORMAT, a Commit per record:
    NUL-separated fields, RS-terminated records. A record whose first field
    isn't a full sha is skipped (garbage, a truncated read); an abbreviation
    that isn't hex falls back to the sha's first seven characters; the
    subject is cut to SUBJECT_MAX_CHARS; never more than MAX_ROWS."""
    commits: list[Commit] = []
    for record in (text or "").split("\x1e"):
        line = record[1:] if record.startswith("\n") else record
        if not line.strip():
            continue
        fields = line.split("\0")
        sha = fields[0].strip()
        if not _FULL_SHA.match(sha):
            continue
        abbrev = fields[1].strip() if len(fields) > 1 else ""
        if not _ABBREV_SHA.match(abbrev):
            abbrev = sha[:7]
        subject = fields[2].strip() if len(fields) > 2 else ""
        commits.append(Commit(sha, abbrev, subject[:SUBJECT_MAX_CHARS]))
        if len(commits) >= MAX_ROWS:
            break
    return commits


def parse_status_v2(text: str) -> Status:
    """`git status --porcelain=v2 -z --untracked-files=all` as a Status.

    Entry kinds: `1` (ordinary: `1 XY sub mH mI mW hH hI path`), `2` (rename
    or copy: `2 XY sub mH mI mW hH hI Xscore path`, followed by the original
    path as its own NUL-terminated token), `u` (unmerged: ten fields then the
    path), `?` (untracked) and `!` (ignored, skipped). X is the index side, Y
    the working-tree side; a path lands in `staged` when X is not `.` and in
    `unstaged` when Y is not `.` — both, for a file changed on top of a
    staged change. Unmerged paths are listed under `unstaged` as `U`. A
    path over PATH_MAX_CHARS drops its entry; a status code outside
    STATUS_CODES drops that side.
    """
    staged: list[StatusRow] = []
    unstaged: list[StatusRow] = []
    tokens = (text or "").split("\0")
    index = 0
    while index < len(tokens):
        entry = tokens[index]
        index += 1
        if not entry:
            continue
        kind = entry[0]
        if kind == "?":
            path = entry[2:]
            if _path_ok(path):
                unstaged.append(StatusRow(path, "?"))
            continue
        if kind in "!#":
            continue
        field_count = {"1": 8, "2": 9, "u": 10}.get(kind, 0)
        if field_count == 0:
            continue
        split = _split_fields(entry, field_count)
        if split is None:
            continue
        head, path = split
        xy = head[1] if len(head) > 1 else ".."
        x, y = xy[:1], xy[1:2]
        previous_path: str | None = None
        if kind == "2":
            previous_path = tokens[index] if index < len(tokens) else None
            index += 1
            if previous_path is not None and not _path_ok(previous_path):
                previous_path = None
        if not _path_ok(path):
            continue
        if kind == "u":
            unstaged.append(StatusRow(path, "U"))
            continue
        if x != "." and x in STATUS_CODES and x != "?":
            staged.append(StatusRow(path, x, previous_path))
        if y != "." and y in STATUS_CODES and y != "?":
            unstaged.append(StatusRow(path, y))
        if len(staged) >= MAX_ROWS and len(unstaged) >= MAX_ROWS:
            break
    return Status(tuple(unstaged[:MAX_ROWS]), tuple(staged[:MAX_ROWS]))


def _split_fields(entry: str, count: int) -> tuple[list[str], str] | None:
    """*count* space-separated fields off the front; the rest is the path
    (which may hold spaces of its own). None when the entry is short."""
    head: list[str] = []
    rest = entry
    for _taken in range(count):
        space = rest.find(" ")
        if space < 0:
            return None
        head.append(rest[:space])
        rest = rest[space + 1 :]
    return (head, rest) if rest else None


def _path_ok(path: object) -> bool:
    return isinstance(path, str) and 0 < len(path) <= PATH_MAX_CHARS


def without_untracked(status: Status | None) -> Status | None:
    """*status* with its untracked (`?`) rows left out: what the files list
    shows when the untracked switch is off, so the UNSTAGED section beside
    a staged load lists no file the `diff --exclude-untracked` it would
    load can't hold. None stays None."""
    if status is None:
        return None
    return Status(tuple(row for row in status.unstaged if row.code != "?"), status.staged)


# -- the commits list ------------------------------------------------------------------


def stack_group(name: str) -> str:
    """The group id of a stack branch's rows (`stack:<branch name>`)."""
    return f"{STACK_GROUP_PREFIX}{name}"


def stack_ranges(stack: Sequence[BranchRef], default: BranchRef | None) -> list[tuple[BranchRef, str | None]]:
    """(branch, the target below it) for every branch of *stack*, nearest
    HEAD first: the next branch's target, the default's for the last one,
    None when there is no default — the lower end of each group's `git
    log` range (`<below>..<branch>`) and of its header's three-dot load."""
    if not stack:
        return []
    below = [ref.target for ref in stack[1:]] + [default.target if default is not None else None]
    return list(zip(stack, below, strict=True))


def header_row_id(group: str) -> str:
    return f"header:{group}"


def more_row_id(group: str) -> str:
    return f"more:{group}"


def commit_row_id(sha: str) -> str:
    return f"commit:{sha}"


def row_folded(row: Row, collapsed: Collection[str]) -> bool:
    """Whether *row* hides under a collapsed group: every row of a group in
    *collapsed* but its header, which stays as the handle that unfolds it
    (the sidebar's caret). The working tree row is above every header and
    never folds."""
    return row.kind not in ("header", "worktree") and row.group in collapsed


def _commit_rows(commits: Iterable[Commit], group: str, unpushed: Collection[str]) -> list[Row]:
    rows = []
    for commit in commits:
        rows.append(
            Row(
                id=commit_row_id(commit.sha),
                kind="commit",
                group=group,
                label=commit.subject[:SUBJECT_MAX_CHARS],
                load={gitloads.SHOW_KEY: commit.sha},
                sha=commit.sha,
                abbrev=commit.abbrev,
                unpushed=commit.sha in unpushed,
            )
        )
    return rows


def _more_row(group: str) -> Row:
    return Row(id=more_row_id(group), kind="more", group=group, label=_("load more…"))


def build_rows(
    branch: str,
    parent: BranchRef | None,
    default: BranchRef | None,
    current: Sequence[Commit],
    current_more: bool,
    stack: Sequence[BranchPage],
    default_commits: Sequence[Commit],
    default_more: bool,
    unpushed: Collection[str],
    twins: Iterable[str] = (),
) -> list[Row]:
    """The rows of the commits list, top to bottom: `working tree` (its own
    group, WORKTREE_GROUP, above every header), the current branch (its
    header, its commits `<parent>..HEAD`, `load more…`), then one group per
    branch of *stack* — the branch the current one stacks on first, then
    the one under it, down to the last one above the default branch (each
    its header, its commits `<below>..<branch>`, `load more…`) — then the
    default branch (its header, its latest page). With the default branch
    checked out (*branch* is *default*'s name) the current group is left
    out: its header would repeat the default's name over an empty list,
    so the default's header stands for both, wearing the current *twins*.
    The caller hands the stack as gitops.stack_branches lists it, nearest
    HEAD first, with a page read for each (BranchPage); a parent that is
    the default branch has no stack.

    Header loads follow the spec's table: the current branch's header loads
    "branch" (`<parent>...HEAD`) — or, with no parent at all, the range
    from the oldest listed commit's parent (`{"range": "<sha>^...HEAD"}`),
    nothing when nothing is listed — and a stack branch's header the
    range `<below>...<branch>` (stack_ranges), none when nothing lies
    below. The default branch's header loads nothing: a whole trunk is
    more than a viewer should be handed. Branch names are shown as
    written; branches at one commit share a header (`a / b`,
    branch_label — the current one's *twins* are the other branches at
    HEAD, a stack branch's ride on its BranchRef). *unpushed* is the set
    of shas the `↑` mark goes on. The whole list is capped at MAX_ROWS.
    """
    rows: list[Row] = [Row(WORKTREE_ROW_ID, "worktree", WORKTREE_GROUP, _("working tree"), "unstaged")]
    on_default = default is not None and branch == default.name
    twins = tuple(twins)
    if not on_default:
        oldest = current[-1] if current else None
        if parent is not None:
            current_load: gitloads.Loaded | None = "branch"
        elif oldest is not None and gitloads.safe_ref(f"{oldest.sha}^"):
            current_load = {gitloads.RANGE_KEY: f"{oldest.sha}^...HEAD"}
        else:
            current_load = None
        label = branch_label(branch, twins)
        rows.append(Row(header_row_id(CURRENT_GROUP), "header", CURRENT_GROUP, label, current_load))
        rows.extend(_commit_rows(current, CURRENT_GROUP, unpushed))
        if current_more:
            rows.append(_more_row(CURRENT_GROUP))

    ranges = stack_ranges([page.branch for page in stack], default)
    for page, (ref, below) in zip(stack, ranges, strict=True):
        group = stack_group(ref.name)
        load: gitloads.Loaded | None = None
        if below is not None:
            candidate = {gitloads.RANGE_KEY: f"{below}...{ref.target}"}
            load = candidate if gitloads.is_range(candidate) else None
        rows.append(Row(header_row_id(group), "header", group, ref.label, load))
        rows.extend(_commit_rows(page.commits, group, unpushed))
        if page.more:
            rows.append(_more_row(group))

    if default is not None:
        label = branch_label(default.name, twins) if on_default else default.label
        rows.append(Row(header_row_id(DEFAULT_GROUP), "header", DEFAULT_GROUP, label))
        rows.extend(_commit_rows(default_commits, DEFAULT_GROUP, unpushed))
        if default_more:
            rows.append(_more_row(DEFAULT_GROUP))
    return rows[:MAX_ROWS]


def loaded_row_id(rows: Sequence[Row], loaded: object, resolved_sha: str | None = None) -> str | None:
    """The id of the row that describes what the page has loaded, or None when
    no row does: the working tree row for both working-tree loads, the
    current header for "branch" (the default's when the default branch is
    checked out and the list has no current group), a commit row for a
    {"show": ref} — matched
    by sha prefix, then by *resolved_sha* (what a `show HEAD` or a branch
    name resolves to, when the caller could resolve it) — and the header
    whose range a {"range": "a...b"} is. Anything else (a foreign title,
    None) is no row."""
    if loaded in ("unstaged", "staged"):
        return next((row.id for row in rows if row.kind == "worktree"), None)
    if loaded == "branch":
        for group in (CURRENT_GROUP, DEFAULT_GROUP):
            found = next((row.id for row in rows if row.kind == "header" and row.group == group), None)
            if found is not None:
                return found
        return None
    ref = gitloads.show_ref(loaded)
    if ref is not None:
        for row in rows:
            if row.kind == "commit" and row.sha and row.sha.startswith(ref):
                return row.id
        if resolved_sha:
            for row in rows:
                if row.kind == "commit" and row.sha == resolved_sha:
                    return row.id
        return None
    text = gitloads.range_of(loaded)
    if text is not None:
        for row in rows:
            if row.kind == "header" and gitloads.range_of(row.load) == text:
                return row.id
    return None


# -- the files list --------------------------------------------------------------------


def _live_row(file: FileSummary, codes: dict[str, StatusRow]) -> FileRow:
    """A row for a file of the loaded diff: its counts from the diff, its
    status letter from the matching `git status` row when there is one,
    else `R` for a rename the diff reports, else nothing."""
    status = codes.get(file.path)
    if status is not None:
        code: str | None = status.code
    elif file.previous_path and file.previous_path != file.path:
        code = "R"
    else:
        code = None
    return FileRow(
        path=file.path,
        code=code,
        previous_path=file.previous_path or (status.previous_path if status else None),
        additions=file.additions,
        deletions=file.deletions,
        live=True,
        hunk_count=file.hunk_count,
    )


def _status_row(row: StatusRow) -> FileRow:
    return FileRow(path=row.path, code=row.code, previous_path=row.previous_path, live=False)


def files_sections(
    status: Status | None,
    session_files: Sequence[FileSummary],
    loaded: object,
    untracked: bool = True,
) -> FileSections:
    """The files list's sections. When the working tree is loaded and the
    status is known, the loaded side's rows come from the diff's own files
    (counts; a click reveals) and the other side's from `git status` (a
    click reloads there first); every other load — and a working tree with
    no status — is one flat list of the diff's files. With *untracked* off
    the status's `?` rows are dropped: the diff read already leaves
    untracked files out, and a `?` row on the other side would be a file
    a click could never load. The status's unmerged (`U`) rows leave the
    unstaged side for *conflicts*: on the unstaged load the diff's own
    row for each (gitops read them as `diff --ours`), or a status row for
    one the read didn't carry, so the list names every clash the moment
    the operation stops."""
    files = [file for file in session_files if _path_ok(file.path)][:MAX_ROWS]
    if loaded not in ("unstaged", "staged") or status is None:
        return FileSections(mode="flat", flat=tuple(_live_row(file, {}) for file in files))
    if not untracked:
        status = without_untracked(status)
    unmerged = tuple(row for row in status.unstaged if row.code == "U")
    unmerged_paths = {row.path for row in unmerged}
    side = status.unstaged if loaded == "unstaged" else status.staged
    codes = {row.path: row for row in side}
    live = tuple(_live_row(file, codes) for file in files)
    if loaded == "unstaged":
        conflicts = tuple(row for row in live if row.path in unmerged_paths)
        carried = {row.path for row in conflicts}
        conflicts += tuple(_status_row(row) for row in unmerged if row.path not in carried)
        return FileSections(
            mode="split",
            live="unstaged",
            unstaged=tuple(row for row in live if row.path not in unmerged_paths),
            staged=tuple(map(_status_row, status.staged)),
            conflicts=conflicts,
        )
    return FileSections(
        mode="split",
        live="staged",
        unstaged=tuple(_status_row(row) for row in status.unstaged if row.code != "U"),
        staged=live,
        conflicts=tuple(map(_status_row, unmerged)),
    )


# -- the action row's words ---------------------------------------------------------


def stage_noun(count: int) -> str:
    """"change" or "changes" for *count* — two `_()` forms, since the
    translations carry no plurals (see i18n)."""
    return _("change") if count == 1 else _("changes")


def plan_all(status: Status | None, stage: bool) -> tuple[int, str]:
    """(count, confirm heading) for Stage all / Unstage all: how many
    files the confirm names — the unstaged rows for a stage, the staged
    ones for an unstage; the direction is the button's, not the loaded
    side's — and the heading, _("Stage all {n} {noun}?") or
    _("Unstage all {n} {noun}?"). (0, "") when there is nothing to do, or
    no status."""
    rows = () if status is None else (status.unstaged if stage else status.staged)
    count = len(rows)
    if count == 0:
        return 0, ""
    noun = stage_noun(count)
    if stage:
        return count, _("Stage all {n} {noun}?").format(n=count, noun=noun)
    return count, _("Unstage all {n} {noun}?").format(n=count, noun=noun)


def all_done(count: int, stage: bool) -> str:
    """The toast after Stage all / Unstage all landed: _("Staged {n}
    {noun}") / _("Unstaged {n} {noun}")."""
    noun = stage_noun(count)
    if stage:
        return _("Staged {n} {noun}").format(n=count, noun=noun)
    return _("Unstaged {n} {noun}").format(n=count, noun=noun)


def fixup_options(commits: Iterable[Commit]) -> list[str]:
    """The rows of the "Fix up which commit?" picker: `abbrev  subject`,
    in the order given (newest first, as the log lists them)."""
    return [f"{commit.abbrev}  {commit.subject[:SUBJECT_MAX_CHARS]}" for commit in commits]


def revert_done(abbrev: str, commit: bool, head: str | None) -> str:
    """The toast after a revert landed: the commit made (`Reverted a1b2c3d
    as e4f5a6b`, the undo named) or the reverse change staged in the
    working tree, nothing committed."""
    if commit:
        return _("Reverted {sha} as {head} — undo with `git reset --keep HEAD~1`").format(
            sha=abbrev, head=head or "?"
        )
    return _("Reverted {sha} into the working tree — staged, nothing committed").format(sha=abbrev)


def revert_failed(abbrev: str, stderr_line: str, conflicts: bool) -> str:
    """The toast after a revert failed: a revert stopped on *conflicts*
    (unmerged paths left behind) names the way out (`git revert
    --continue` / `--abort`); any other refusal is git's own first line."""
    if conflicts:
        return _(
            "Reverting {sha} left conflicts — resolve them, then `git revert --continue`,"
            " or `git revert --abort`"
        ).format(sha=abbrev)
    return stderr_line or _("git revert failed")


# -- the in-progress bar (gitoperation.py) -----------------------------------------
# The words for a half-finished rebase / merge / cherry-pick / revert /
# `git am` (gitops.in_progress) the page's bar shows over a working-tree
# load: a title per kind, a hint that names the two buttons' commands, the
# abort confirm, and the toasts. *kind* is one of gitops.OPERATION_KINDS;
# *label* its translated name ("rebase", "git am", …).

_OPERATION_TITLES: dict[str, str] = {
    "rebase": "Rebase in progress",
    "am": "git am in progress",
    "merge": "Merge in progress",
    "cherry-pick": "Cherry-pick in progress",
    "revert": "Revert in progress",
}


def unmerged_count(status: Status | None) -> int:
    """How many paths *status* (gitops.read_status) lists as unmerged —
    the `U` rows of `git status --porcelain=v2` — what stands in a
    half-finished operation's way; 0 without a status."""
    if status is None:
        return 0
    return sum(1 for row in status.unstaged if row.code == "U")


def operation_title(kind: str) -> str:
    """The bar's heading: "Rebase in progress", "Merge in progress", …"""
    return _(_OPERATION_TITLES.get(kind, "Operation in progress"))


def operation_hint(kind: str, unmerged: int) -> str:
    """The bar's one-line hint under the heading: what stands in the way
    (*unmerged* paths, `git status`'s `U` rows) and what the two buttons
    run — Continue is `git <kind> --continue` with the step's message
    taken as it is, Abort is `git <kind> --abort`. *kind* goes into the
    command snippets raw on purpose: it is the literal git subcommand
    (`am`, not the translated label "git am"), and must stay so."""
    if unmerged > 0:
        return _(
            "Unmerged files: {count}. Resolve and stage them, then Continue"
            " (`git {kind} --continue`) — or Abort (`git {kind} --abort`) to put the tree back."
        ).format(count=unmerged, kind=kind)
    return _(
        "Nothing is left unmerged. Continue (`git {kind} --continue`) commits the step with"
        " the message it has and carries on — or Abort (`git {kind} --abort`) puts the tree back."
    ).format(kind=kind)


def abort_heading(label: str) -> str:
    """The abort confirm's heading: "Abort the rebase?"."""
    return _("Abort the {operation}?").format(operation=label)


def abort_body(kind: str, label: str, cwd: str) -> str:
    """The abort confirm's body: the command, where it runs, and what
    is lost — the resolutions made since the operation stopped."""
    return _(
        "`git {kind} --abort` in {cwd}: the {operation} is forgotten and the tree goes back"
        " to where it stood before it started. Conflict resolutions made since are lost."
    ).format(kind=kind, cwd=cwd, operation=label)


def abort_done(label: str) -> str:
    """The toast after an abort landed."""
    return _("Aborted the {operation} — the tree is back where it stood").format(operation=label)


def continue_done(label: str, still: str | None) -> str:
    """The toast after a continue landed: the operation finished, or it
    stopped again (*still*: the label of what is now in progress — a
    rebase's next conflicting commit, the sequencer's next pick)."""
    if still:
        return _("Continued the {operation} — it stopped again on the next step").format(
            operation=still
        )
    return _("Finished the {operation}").format(operation=label)


def operation_failed(kind: str, stderr_line: str, abort: bool) -> str:
    """The toast after a continue or abort failed: git's own first line
    (the usual: "You must edit all merge conflicts and then mark them as
    resolved using git add"), else the command's name."""
    return stderr_line or _("`git {kind} {flag}` failed").format(
        kind=kind, flag="--abort" if abort else "--continue"
    )


def autosquash_command(abbrev: str, is_root: bool) -> str:
    """The fold-in command the fixup confirm names (never runs): `git
    rebase -i --autosquash --autostash <abbrev>^`, `--root` for a root
    commit (which has no parent to rebase onto)."""
    if is_root:
        return "git rebase -i --autosquash --autostash --root"
    return f"git rebase -i --autosquash --autostash {abbrev}^"
