# New in the ghackett fork of agent-session-manager (GPL-3.0).
# Portions adapted from sadick254/hunk-commit-log (the log format and its
# parser) and joshedler/hunk-git-lite (the status parser) (MIT, © 2026
# Sadick, © 2026 Josh Edler); see collins/THIRD_PARTY_LICENSES.md.

"""The git page's native panels, as pure functions over what git and hunk
report: the commits list's rows and the files list's sections.

Nothing here runs git, touches hunk or imports a widget. gitops runs git
and hands back the Commits and Status parsed here; hunkctl parses hunk's
`session get` reply into SessionFiles; the sidebar widget (gitsidebar, a
later PR) asks this module which rows to draw, which of them is the loaded
one, and what the confirms and toasts say — the same split the collins-git
extension's model.ts had, ported so the panels can be unit-tested without
a terminal (tests/test_gitmodel.py).

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

from . import hunkctl
from .i18n import _

# `git log` as parse_log reads it: NUL between the fields (sha, abbreviated
# sha, subject), a record separator after each commit — the one delimiter a
# subject can't carry (git strips control characters from `%s`), so a
# subject with a comma, a quote or a NUL-free anything survives.
LOG_FORMAT = "--format=%H%x00%h%x00%s%x1e"
# Bounds on foreign content (see the module docstring).
SUBJECT_MAX_CHARS = 200
PATH_MAX_CHARS = hunkctl.MAX_PATH_CHARS
MAX_ROWS = 2000

# The status letters a row may carry: git's own (M A D R T C, U for an
# unmerged path) plus `?` for untracked. Anything else — a code a future git
# adds — drops the row rather than colouring it wrong.
STATUS_CODES = frozenset("MADRTCU?")
# The groups of the commits list, top to bottom, and the row kinds in them.
GROUPS: tuple[str, ...] = ("current", "parent", "default")
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
    it (`main`, or `origin/main` when only the remote has it)."""

    name: str
    target: str


@dataclass(frozen=True)
class Row:
    """One line of the commits list. *load* is what a click loads (a
    hunkctl.Loaded), None where a click does nothing on its own (the
    default branch's header; `load more…`, which pages instead)."""

    id: str
    kind: str
    group: str
    label: str
    load: hunkctl.Loaded | None = None
    sha: str | None = None
    abbrev: str | None = None
    unpushed: bool = False


@dataclass(frozen=True)
class FileRow:
    """One line of the files list. *live* rows come from hunk's own files
    (with counts, and a click navigates); the others from `git status` (a
    click reloads to that side first). *code* is a STATUS_CODES letter, or
    None when nothing said (a live row with no status to match it to)."""

    path: str
    code: str | None = None
    previous_path: str | None = None
    additions: int | None = None
    deletions: int | None = None
    live: bool = True
    hunk_count: int | None = None

    @property
    def binary(self) -> bool:
        """Whether the file reads as binary: hunk's session record has no
        such flag, but a binary change is the one that lists no hunk and no
        line counts (verified against hunk 0.21.1: a changed `img.bin`
        lists `additions: 0, deletions: 0, hunkCount: 0`) — and a text
        file with nothing to show wouldn't be listed at all."""
        return self.live and self.additions == 0 and self.deletions == 0 and self.hunk_count == 0


@dataclass(frozen=True)
class FileSections:
    """What the files list draws: one flat list (*flat*, any load but the
    working tree), or the two working-tree sides with *live* naming the one
    hunk has loaded ("unstaged" | "staged")."""

    mode: str
    live: str | None = None
    unstaged: tuple[FileRow, ...] = ()
    staged: tuple[FileRow, ...] = ()
    flat: tuple[FileRow, ...] = ()


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


def header_row_id(group: str) -> str:
    return f"header:{group}"


def more_row_id(group: str) -> str:
    return f"more:{group}"


def commit_row_id(sha: str) -> str:
    return f"commit:{sha}"


def _commit_rows(commits: Iterable[Commit], group: str, unpushed: Collection[str]) -> list[Row]:
    rows = []
    for commit in commits:
        rows.append(
            Row(
                id=commit_row_id(commit.sha),
                kind="commit",
                group=group,
                label=commit.subject[:SUBJECT_MAX_CHARS],
                load={hunkctl.SHOW_KEY: commit.sha},
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
    parent_commits: Sequence[Commit],
    parent_more: bool,
    default_commits: Sequence[Commit],
    default_more: bool,
    unpushed: Collection[str],
) -> list[Row]:
    """The rows of the commits list, top to bottom: the current branch (its
    header, `working tree`, its commits `<parent>..HEAD`, `load more…`),
    the parent branch when it isn't the default (its header, its commits
    `<default>..<parent>`), then the default branch (its header, its latest
    page).

    Header loads follow the spec's table: the current branch's header loads
    "branch" (`<parent>...HEAD`) — or, with no parent at all, the range
    from the oldest listed commit's parent (`{"range": "<sha>^...HEAD"}`),
    nothing when nothing is listed — and the parent's header the range
    `<default>...<parent>`. The default branch's header loads nothing: a
    whole trunk is more than a viewer should be handed. Branch names are
    shown as written. *unpushed* is the set of shas the `↑` mark goes on.
    The whole list is capped at MAX_ROWS.
    """
    rows: list[Row] = []
    oldest = current[-1] if current else None
    if parent is not None:
        current_load: hunkctl.Loaded | None = "branch"
    elif oldest is not None and hunkctl.safe_ref(f"{oldest.sha}^"):
        current_load = {hunkctl.RANGE_KEY: f"{oldest.sha}^...HEAD"}
    else:
        current_load = None
    rows.append(Row(header_row_id("current"), "header", "current", branch, current_load))
    rows.append(Row(WORKTREE_ROW_ID, "worktree", "current", _("working tree"), "unstaged"))
    rows.extend(_commit_rows(current, "current", unpushed))
    if current_more:
        rows.append(_more_row("current"))

    parent_is_default = parent is None or (default is not None and parent.name == default.name)
    if parent is not None and not parent_is_default:
        parent_load: hunkctl.Loaded | None = None
        if default is not None:
            candidate = {hunkctl.RANGE_KEY: f"{default.target}...{parent.target}"}
            parent_load = candidate if hunkctl.is_range(candidate) else None
        rows.append(Row(header_row_id("parent"), "header", "parent", parent.name, parent_load))
        rows.extend(_commit_rows(parent_commits, "parent", unpushed))
        if parent_more:
            rows.append(_more_row("parent"))

    if default is not None:
        rows.append(Row(header_row_id("default"), "header", "default", default.name))
        rows.extend(_commit_rows(default_commits, "default", unpushed))
        if default_more:
            rows.append(_more_row("default"))
    return rows[:MAX_ROWS]


def loaded_row_id(rows: Sequence[Row], loaded: object, resolved_sha: str | None = None) -> str | None:
    """The id of the row that describes what hunk has loaded, or None when
    no row does: the working tree row for both working-tree loads, the
    current header for "branch", a commit row for a {"show": ref} — matched
    by sha prefix, then by *resolved_sha* (what a `show HEAD` or a branch
    name resolves to, when the caller could resolve it) — and the header
    whose range a {"range": "a...b"} is. Anything else (a foreign title,
    None) is no row."""
    if loaded in ("unstaged", "staged"):
        return next((row.id for row in rows if row.kind == "worktree"), None)
    if loaded == "branch":
        return next((row.id for row in rows if row.kind == "header" and row.group == "current"), None)
    ref = hunkctl.show_ref(loaded)
    if ref is not None:
        for row in rows:
            if row.kind == "commit" and row.sha and row.sha.startswith(ref):
                return row.id
        if resolved_sha:
            for row in rows:
                if row.kind == "commit" and row.sha == resolved_sha:
                    return row.id
        return None
    text = hunkctl.range_of(loaded)
    if text is not None:
        for row in rows:
            if row.kind == "header" and hunkctl.range_of(row.load) == text:
                return row.id
    return None


# -- the files list --------------------------------------------------------------------


def _live_row(file: hunkctl.SessionFile, codes: dict[str, StatusRow]) -> FileRow:
    """A row for a file hunk has loaded: its counts from hunk, its status
    letter from the matching `git status` row when there is one, else `R`
    for a rename hunk reports, else nothing."""
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
    session_files: Sequence[hunkctl.SessionFile],
    loaded: object,
    untracked: bool = True,
) -> FileSections:
    """The files list's sections. When the working tree is loaded and the
    status is known, the loaded side's rows come from hunk's own files
    (counts; a click navigates) and the other side's from `git status` (a
    click reloads there first); every other load — and a working tree with
    no status — is one flat list of hunk's files. With *untracked* off the
    status's `?` rows are dropped: hunk's own list already respects
    `--exclude-untracked`, and a `?` row on the other side would be a file
    a click could never load."""
    files = [file for file in session_files if _path_ok(file.path)][:MAX_ROWS]
    if loaded not in ("unstaged", "staged") or status is None:
        return FileSections(mode="flat", flat=tuple(_live_row(file, {}) for file in files))
    if not untracked:
        status = without_untracked(status)
    side = status.unstaged if loaded == "unstaged" else status.staged
    codes = {row.path: row for row in side}
    live = tuple(_live_row(file, codes) for file in files)
    if loaded == "unstaged":
        return FileSections(
            mode="split", live="unstaged", unstaged=live, staged=tuple(map(_status_row, status.staged))
        )
    return FileSections(
        mode="split", live="staged", unstaged=tuple(map(_status_row, status.unstaged)), staged=live
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


def autosquash_command(abbrev: str, is_root: bool) -> str:
    """The fold-in command the fixup confirm names (never runs): `git
    rebase -i --autosquash --autostash <abbrev>^`, `--root` for a root
    commit (which has no parent to rebase onto)."""
    if is_root:
        return "git rebase -i --autosquash --autostash --root"
    return f"git rebase -i --autosquash --autostash {abbrev}^"
