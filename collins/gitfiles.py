# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The `.git` reads behind gitinfo, on the machine the repository is on.

Split out of gitinfo.py for the split-service spec's Phase 2 (§3.23,
PR-2.1): the client reads a repository through `git.info` and its per-cwd
mirror (`remotegit`), so the stat calls and small file reads that answer
the footer's branch, the trunk, the GitHub page, the signatures the git
page's freshness check compares and the in-progress markers live here,
where only the service (and the unit tests, over a temp repository) run
them. `gitinfo` keeps the public names and the rule for each answer, and
calls this module for the local fallback; `read_info` is what the
service's `git.info` answers with: every read at once, as a `GitInfo`.

Stdlib only; nothing here imports GTK or gi. Everything read is
repository content (rule 5): bounded, and never trusted as a path or an
argument without the caller's own check.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

REF_PREFIX = "ref:"
BRANCH_REF_PREFIX = "refs/heads/"

# A `[remote "name"]` stanza in a git config; git treats section names
# case-insensitively, so this does too.
_REMOTE_SECTION = re.compile(r'^\[remote\s+"([^"]+)"\]', re.IGNORECASE)

# Which remote speaks for the project when several do. Anything unlisted goes
# after these in name order, so the answer can't wobble between right-clicks.
REMOTE_ORDER = ("origin", "upstream", "github")

# The one host whose remotes get a GitHub page. An enterprise install answers
# on its own domain and isn't recognized here — better no menu item than one
# pointing at github.com for a repository that doesn't live there.
_GITHUB_HOST = "github.com"

# Schemes a remote may be written in. `file:`/`javascript:` and friends never
# get this far: what this module hands back is always an https URL it built
# itself, but the path it builds it out of comes from a repository's config,
# which is untrusted like any other repo content.
_REMOTE_SCHEMES = frozenset({"https", "http", "ssh", "git"})

# `git@github.com:owner/repo.git` — the scp-like form git accepts without a
# scheme. The host is everything before the first colon, after an optional
# `user@`.
_SCP_LIKE = re.compile(r"^(?:[^/@]+@)?(?P<host>[^/:]+):(?P<path>.+)$")

# A GitHub owner or repository name as it may appear in a URL: nothing that
# would escape the path segment or read as a traversal. Both are required
# before a link is built.
_REPO_NAME = re.compile(r"^(?!\.+$)[A-Za-z0-9._-]+$")

_REMOTE_HEAD_FILE = "HEAD"
DEFAULT_BRANCH_NAMES = ("main", "master")

# The files and directories git leaves in the git directory while an
# operation waits on the user: a rebase (either backend; rebase-apply is
# `git am`'s too), a merge, a cherry-pick or a revert, and the sequencer
# a multi-commit cherry-pick / revert keeps between its steps. What
# operation_markers reports and operation_kind reads.
OPERATION_MARKERS: tuple[str, ...] = (
    "rebase-merge",
    "rebase-apply",
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "sequencer",
)
# The markers in the order they are checked (a rebase beats a merge beats a
# cherry-pick), and the kind each names. `sequencer` alone — a multi-commit
# cherry-pick or revert between two of its steps, the stopped step already
# committed by hand — is read from its todo (see operation_kind).
IN_PROGRESS_MARKERS: tuple[tuple[str, str], ...] = (
    ("rebase-merge", "rebase"),
    ("rebase-apply", "rebase"),
    ("MERGE_HEAD", "merge"),
    ("CHERRY_PICK_HEAD", "cherry-pick"),
    ("REVERT_HEAD", "revert"),
    ("sequencer", "cherry-pick"),
)
# The sequencer's todo is read this far to tell a revert's from a
# cherry-pick's: its first line is `pick <sha> …` or `revert <sha> …`.
SEQUENCER_TODO_BYTES = 4096

# How many directories under refs/heads and refs/remotes refs_signature
# will stat before it stops descending: a branch name makes one directory
# per slash in it, and the walk runs on the footer's 2 s poll.
REFS_DIR_LIMIT = 256
# How many refs list_refs reads of each kind (local heads, remote-tracking
# refs): a repository past this resolves its first few thousand branches
# and no more (protocol.REFS_MAX bounds the wire the same way).
REFS_LIMIT = 4096
# A ref name the wire carries (protocol.NAME_MAX).
_REF_NAME_MAX = 1024
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class GitInfo:
    """Every `.git` read of a repository at once (`read_info`; the
    `git.info` reply, the client's mirror entry). *root* None means the cwd
    is not inside a repository and every other field is empty. *heads* is
    local branch → sha, *remote_heads* `remote/branch` → sha (both bounded
    by REFS_LIMIT), *remotes* the remote names in rank order; *refs* is a
    digest of refs_signature's tuple, *index_mtime* microseconds since the
    epoch (index_mtime's unit, local and on the wire), *markers* operation_markers',
    *operation* the in-progress kind (gitops' OPERATION_KINDS) or None;
    *changes* is (staged, unstaged) when a status was asked for and
    *state* gitops.tree_state_signature's digest when that was, else
    None; *fetched_at* is the mirror's clock."""

    root: str | None = None
    git_dir: str | None = None
    branch: str | None = None
    default_branch: str | None = None
    github_url: str | None = None
    index_mtime: int | None = None
    head: str | None = None
    markers: tuple[str, ...] = ()
    operation: str | None = None
    refs: str | None = None
    remotes: tuple[str, ...] = ()
    heads: dict[str, str] = field(default_factory=dict)
    remote_heads: dict[str, str] = field(default_factory=dict)
    changes: tuple[bool, bool] | None = None
    state: str | None = None
    fetched_at: float = 0.0

    @property
    def repository(self) -> bool:
        return self.root is not None

    def resolve_branch(self, name: str | None) -> tuple[str, str] | None:
        """gitinfo.resolve_branch's rule over the mirror: (target, sha) for
        a local branch, else the first ranked remote's copy."""
        if not safe_branch_name(name) or self.root is None:
            return None
        sha = self.heads.get(name)
        if sha:
            return name, sha
        for remote in self.remotes:
            sha = self.remote_heads.get(f"{remote}/{name}")
            if sha:
                return f"{remote}/{name}", sha
        return None

    def remote_branch_name(self, name: str | None) -> str | None:
        """gitinfo.remote_branch_name's rule over the mirror."""
        if not safe_branch_name(name) or "/" not in name or self.root is None:
            return None
        remote, _, rest = name.partition("/")
        if not remote or not rest or remote not in self.remotes:
            return None
        return rest if self.remote_heads.get(f"{remote}/{rest}") else None

    def to_fields(self, known_refs: str | None = None) -> dict:
        """The `git.info` reply's fields. The heads are left out when the
        client already holds this *refs* digest (``known_refs``)."""
        fields: dict = {"root": self.root}
        if self.root is None:
            return fields
        fields.update(
            git_dir=self.git_dir,
            branch=self.branch,
            default_branch=self.default_branch,
            github_url=self.github_url,
            index_mtime=self.index_mtime,  # microseconds, as index_mtime() reads it
            head=self.head,
            markers=list(self.markers),
            operation=self.operation,
            refs=self.refs,
        )
        if known_refs is None or known_refs != self.refs:
            fields.update(
                remotes=list(self.remotes),
                heads=dict(self.heads),
                remote_heads=dict(self.remote_heads),
            )
        if self.changes is not None:
            fields["changes"] = {"staged": self.changes[0], "unstaged": self.changes[1]}
        if self.state is not None:
            fields["state"] = self.state
        return fields

    @classmethod
    def from_fields(cls, fields: dict, previous: GitInfo | None = None, now: float | None = None) -> GitInfo:
        """A reply's fields as a GitInfo; the heads come from *previous*
        when the reply left them out (the digest matched)."""
        stamp = time.monotonic() if now is None else now
        root = fields.get("root")
        if not isinstance(root, str) or not root:
            return cls(fetched_at=stamp)
        refs = fields.get("refs")
        if "heads" in fields or previous is None or previous.refs != refs:
            remotes = tuple(str(r) for r in (fields.get("remotes") or ())[:REFS_LIMIT])
            heads = {str(k): str(v) for k, v in (fields.get("heads") or {}).items() if _SHA_RE.match(str(v))}
            remote_heads = {
                str(k): str(v) for k, v in (fields.get("remote_heads") or {}).items() if _SHA_RE.match(str(v))
            }
        else:
            remotes, heads, remote_heads = previous.remotes, previous.heads, previous.remote_heads
        changes = fields.get("changes")
        changed = None
        if isinstance(changes, dict):
            changed = (bool(changes.get("staged")), bool(changes.get("unstaged")))
        elif previous is not None and previous.root == root:
            changed = previous.changes
        state = fields.get("state")
        return cls(
            root=root,
            git_dir=fields.get("git_dir") or None,
            branch=fields.get("branch") or None,
            default_branch=fields.get("default_branch") or None,
            github_url=fields.get("github_url") or None,
            index_mtime=fields.get("index_mtime"),
            head=fields.get("head") or None,
            markers=tuple(str(m) for m in (fields.get("markers") or ())[:8]),
            operation=fields.get("operation") or None,
            refs=refs if isinstance(refs, str) else None,
            remotes=remotes,
            heads=heads,
            remote_heads=remote_heads,
            changes=changed,
            state=state if isinstance(state, str) else None,
            fetched_at=stamp,
        )


def safe_branch_name(name: str | None) -> bool:
    """Whether *name* can be handed to git as a revision without it reading
    as an option or a range: no empty or blank names, no leading dash, no
    `..` (which would make a range of it), no whitespace."""
    if not name or not isinstance(name, str) or any(ch.isspace() for ch in name):
        return False
    return not name.startswith("-") and ".." not in name


# -- the repository's directories ------------------------------------------------


def git_dir(cwd: str | Path | None) -> Path | None:
    """The git directory of the repository enclosing *cwd* — the nearest
    `.git` walking upwards, resolved through the pointer file a worktree or
    submodule has there instead of a directory. None outside a repository, and
    for a pointer file that doesn't point anywhere."""
    found = find_git_entry(cwd)
    if found is None:
        return None
    _root, git = found
    if git.is_dir():
        return git
    return _resolve_gitdir_pointer(git)  # worktree or submodule: "gitdir: <real git dir>"


def find_git_entry(cwd: str | Path | None) -> tuple[Path, Path] | None:
    """(working tree root, its `.git` entry) for the repository enclosing
    *cwd*: the nearest directory walking upwards that has a `.git` — a
    directory in an ordinary checkout, a pointer file in a worktree or
    submodule. None outside a repository."""
    if not cwd:
        return None
    start = Path(cwd)
    if not start.is_dir():
        return None
    for directory in (start, *start.parents):
        git = directory / ".git"
        if git.is_dir() or git.is_file():
            return directory, git
    return None


def repo_root(cwd: str | Path | None) -> Path | None:
    found = find_git_entry(cwd)
    return found[0] if found else None


def in_repository(start: Path) -> bool:
    """Whether *start* is inside a git repository — a couple of stat calls
    (`.git` may be a directory, or a worktree/submodule pointer file), so a
    tree outside any repository never pays for a `git` process."""
    if not start.is_dir():
        return False
    return any((directory / ".git").exists() for directory in (start, *start.parents))


def common_dir(git_dir: Path) -> Path:
    """Where the parts every worktree shares live — the config among them.

    A linked worktree's git directory (`.git/worktrees/<name>`) has its own
    HEAD but no config of its own; its `commondir` file names the directory
    that has one. Everywhere else this is *git_dir* itself.
    """
    try:
        target = (git_dir / "commondir").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return git_dir
    return git_dir / target if target else git_dir  # "/abs" replaces the base


def _resolve_gitdir_pointer(git_file: Path) -> Path | None:
    try:
        text = git_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("gitdir:"):
            target = line[len("gitdir:") :].strip()
            if target:  # Path("/a") / "/abs" keeps the absolute target as-is
                return git_file.parent / target
    return None


# -- HEAD, refs, remotes ---------------------------------------------------------------


def read_head(git_dir: Path) -> str | None:
    """The branch HEAD names, or a detached HEAD's abbreviated hash."""
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if head.startswith(REF_PREFIX):
        ref = head[len(REF_PREFIX) :].strip()
        if ref.startswith(BRANCH_REF_PREFIX):
            return ref[len(BRANCH_REF_PREFIX) :] or None
        return ref or None
    return head[:8] or None  # detached HEAD


def head_sha(git_dir: Path) -> str | None:
    """The commit HEAD points at: a symbolic HEAD resolved through the loose
    ref or packed-refs in the common dir, a detached HEAD's own hash. None
    for an unborn branch."""
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if head.startswith(REF_PREFIX):
        ref = head[len(REF_PREFIX) :].strip()
        return ref_sha(common_dir(git_dir), ref) if ref else None
    return head or None


def index_mtime(git_dir: Path) -> int | None:
    """The mtime of the repository's index file (in the worktree's own git
    dir, not the common dir) in microseconds since the epoch: one unit on
    both paths, local and over the wire (nanoseconds pass the protocol's
    integer bound, 2**53, only in 2255). None when there is no index yet
    or it can't be stat'd."""
    try:
        return (git_dir / "index").stat().st_mtime_ns // 1000
    except OSError:
        return None


def remote_urls(config: Path) -> dict[str, str]:
    """Every remote's fetch URL in *config*, by remote name.

    Hand-parsed rather than handed to `configparser`: git's format only looks
    like an INI file, and the indentation git writes its variables with is
    what configparser reads as a continuation line. Empty for a config that
    can't be read — the same answer as one with no remotes in it.
    """
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    urls: dict[str, str] = {}
    remote: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("["):
            match = _REMOTE_SECTION.match(line)
            remote = match.group(1) if match else None
            continue
        if remote is None:
            continue
        key, separator, value = line.partition("=")
        # First url wins: a remote repeating the key is git's own
        # last-one-wins, but a config that odd isn't worth a second read.
        if separator and key.strip().lower() == "url":
            urls.setdefault(remote, value.strip())
    return urls


def remote_rank(name: str) -> tuple[int, str]:
    conventional = name.lower() in REMOTE_ORDER
    index = REMOTE_ORDER.index(name.lower()) if conventional else len(REMOTE_ORDER)
    return index, name


def ranked_remotes(common: Path) -> list[str]:
    """The remote names in *common*'s config, best first (remote_rank)."""
    return sorted(remote_urls(common / "config"), key=remote_rank)


def github_page(remote_url: str) -> str | None:
    """The web page for *remote_url*, when it is a GitHub remote.

    Every form a remote is written in comes down to a host and a path:
    `git@github.com:owner/repo.git`, `ssh://git@github.com/owner/repo`,
    `https://github.com/owner/repo.git`. The URL handed back is built from
    the owner and repo, never from the remote's own text.
    """
    text = remote_url.strip()
    if not text:
        return None
    if "://" in text:
        parsed = urlsplit(text)
        if parsed.scheme.lower() not in _REMOTE_SCHEMES:
            return None
        host, path = parsed.hostname or "", parsed.path
    else:
        match = _SCP_LIKE.match(text)
        if match is None:
            return None
        host, path = match.group("host"), match.group("path")
    if host.lower().removeprefix("www.") != _GITHUB_HOST:
        return None
    path = path.strip("/")
    if path.lower().endswith(".git"):
        path = path[: -len(".git")]
    owner, separator, repo = path.partition("/")
    if not separator or not _REPO_NAME.match(owner) or not _REPO_NAME.match(repo):
        return None
    return f"https://{_GITHUB_HOST}/{owner}/{repo}"


def github_url_of(common: Path) -> str | None:
    """The GitHub page of the first ranked remote that has one."""
    urls = remote_urls(common / "config")
    for name in sorted(urls, key=remote_rank):
        page = github_page(urls[name])
        if page is not None:
            return page
    return None


def remote_head(common: Path, remote: str) -> str | None:
    """The branch `refs/remotes/<remote>/HEAD` points at, or None — for a
    remote that has no HEAD ref (never cloned from, or pruned), or one whose
    HEAD is detached (`git remote set-head --delete` leaves none; a bare
    commit hash in there is a remote with no default to speak of)."""
    ref_file = common / "refs" / "remotes" / remote / _REMOTE_HEAD_FILE
    try:
        head = ref_file.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not head.startswith(REF_PREFIX):
        return None
    prefix = f"refs/remotes/{remote}/"
    ref = head[len(REF_PREFIX) :].strip()
    if not ref.startswith(prefix):
        return None
    return ref[len(prefix) :] or None


def default_branch_of(common: Path) -> str | None:
    """gitinfo.default_branch's rule: the ranked remotes' HEAD first, then
    a local `main` or `master`."""
    for name in ranked_remotes(common):
        branch = remote_head(common, name)
        if branch:
            return branch
    for name in DEFAULT_BRANCH_NAMES:
        if has_local_branch(common, name):
            return name
    return None


def has_local_branch(common: Path, name: str) -> bool:
    """Whether `refs/heads/<name>` exists — as a loose ref file, or packed
    into `packed-refs` (`<hash> refs/heads/<name>` per line), which is where
    `git gc` moves it."""
    if (common / "refs" / "heads" / name).is_file():
        return True
    return packed_ref(common, f"{BRANCH_REF_PREFIX}{name}") is not None


def ref_sha(common: Path, ref: str) -> str | None:
    """The hash *ref* (`refs/heads/main`, `refs/remotes/origin/main`) holds:
    the loose ref file first, then `packed-refs`. None for a ref that exists
    nowhere, and for a loose ref that is itself symbolic (git writes those
    only for HEADs, which never come through here)."""
    try:
        text = (common / ref).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        text = ""
    if text and not text.startswith(REF_PREFIX):
        return text
    return packed_ref(common, ref)


def packed_ref(common: Path, ref: str) -> str | None:
    """The hash `packed-refs` records for *ref*, or None. Peeled lines (`^`)
    and the header are skipped; the first match wins, as git's own reader
    takes the file as sorted."""
    try:
        packed = (common / "packed-refs").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in packed.splitlines():
        if line.startswith(("#", "^")):
            continue
        parts = line.split(" ", 1)
        if len(parts) == 2 and parts[1].strip() == ref:
            return parts[0].strip() or None
    return None


def list_refs(common: Path) -> tuple[dict[str, str], dict[str, str]]:
    """(local heads, remote-tracking refs) of *common*: branch name → sha
    and `remote/branch` → sha, loose refs over packed ones, at most
    REFS_LIMIT of each kind, names bounded. What `GitInfo.resolve_branch`
    answers from, so a mirror can resolve any name without a round trip."""
    heads: dict[str, str] = {}
    remotes: dict[str, str] = {}
    try:
        packed = (common / "packed-refs").read_text(encoding="utf-8", errors="replace")
    except OSError:
        packed = ""
    for line in packed.splitlines():
        if line.startswith(("#", "^")):
            continue
        parts = line.split(" ", 1)
        if len(parts) != 2:
            continue
        sha, ref = parts[0].strip(), parts[1].strip()
        if not _SHA_RE.match(sha):
            continue
        if ref.startswith(BRANCH_REF_PREFIX):
            _put(heads, ref[len(BRANCH_REF_PREFIX) :], sha)
        elif ref.startswith("refs/remotes/"):
            _put(remotes, ref[len("refs/remotes/") :], sha)
    for base, table in ((common / "refs" / "heads", heads), (common / "refs" / "remotes", remotes)):
        _walk_loose(base, base, table)
    return heads, remotes


def _put(table: dict[str, str], name: str, sha: str) -> None:
    if name and len(name) <= _REF_NAME_MAX and not name.endswith("/HEAD") and len(table) < REFS_LIMIT:
        table[name] = sha
    elif name in table:
        table[name] = sha  # a loose ref over the packed one


def _walk_loose(base: Path, directory: Path, table: dict[str, str]) -> None:
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return
    for entry in entries:
        if len(table) >= REFS_LIMIT:
            return
        try:
            if entry.is_dir():
                _walk_loose(base, entry, table)
                continue
            if entry.name == _REMOTE_HEAD_FILE and base.name == "remotes":
                continue
            text = entry.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if _SHA_RE.match(text):
            name = entry.relative_to(base).as_posix()
            if len(name) <= _REF_NAME_MAX:
                table[name] = text


# -- the signatures -------------------------------------------------------------------


def operation_markers(git_dir: Path | None) -> tuple[str, ...]:
    """Which of OPERATION_MARKERS exist in the repository's own git
    directory (a worktree's, where git keeps them) — empty outside a
    repository or with nothing half-finished. Stats only, no git: part of
    the tree signature, so the page notices `git merge --quit` and its
    kin, which forget an operation without moving the index or HEAD."""
    if git_dir is None:
        return ()
    found = []
    for marker in OPERATION_MARKERS:
        try:
            if (git_dir / marker).exists():
                found.append(marker)
        except OSError:
            continue
    return tuple(found)


def operation_kind(git_dir: str | Path | None) -> str | None:
    """What is half-finished in the repository whose git directory is
    *git_dir* — a rebase, a `git am` (rebase-apply with an `applying` file
    in it), a merge, a cherry-pick or a revert (a lone `sequencer` is one
    of the last two between two steps of a multi-commit run; its todo's
    first word says which) — as gitops' OPERATION_KINDS name it, or None
    when nothing is, or there is no directory to look in. File checks
    only, no git."""
    if not git_dir:
        return None
    base = Path(git_dir)
    for marker, kind in IN_PROGRESS_MARKERS:
        try:
            if not (base / marker).exists():
                continue
            if marker == "rebase-apply" and (base / marker / "applying").exists():
                kind = "am"
            elif marker == "sequencer" and _sequencer_reverts(base / marker / "todo"):
                kind = "revert"
        except OSError:
            continue
        return kind
    return None


def _sequencer_reverts(todo: Path) -> bool:
    """Whether the sequencer's *todo* names reverts: its first
    non-comment line starts with `revert` (a cherry-pick's with `pick`).
    A todo that can't be read is a cherry-pick's — the likelier of the
    two, and the words are all that ride on it."""
    try:
        with todo.open("rb") as handle:
            text = handle.read(SEQUENCER_TODO_BYTES).decode("utf-8", "replace")
    except OSError:
        return False
    for line in text.splitlines():
        word = line.strip().split(" ", 1)[0]
        if not word or word.startswith("#"):
            continue
        return word == "revert"
    return False


def refs_signature(git_dir: Path) -> tuple:
    """What moves when a ref is written — a commit on another branch (in
    another worktree), a branch created or deleted, a push, a fetch —
    folded into one comparable value for the git page's poll: the mtime
    of `packed-refs` (where `git gc` and `fetch --prune` rewrite refs
    wholesale) and, for every directory under `refs/heads` and
    `refs/remotes` (each remote, and each slash-separated prefix of a
    branch name), its mtime — git writes a loose ref by renaming a lock
    file into its directory, which moves that directory's mtime and no
    other. All read from the common dir (a worktree's refs are the main
    checkout's). A repository with no refs at all answers a value that
    stays put."""
    common = common_dir(git_dir)
    try:
        packed = (common / "packed-refs").stat().st_mtime_ns
    except OSError:
        packed = None
    directories: list[tuple[str, int]] = []
    pending = [common / "refs" / "remotes", common / "refs" / "heads"]
    while pending and len(directories) < REFS_DIR_LIMIT:
        directory = pending.pop()
        try:
            stamp = directory.stat().st_mtime_ns
            children = [entry for entry in directory.iterdir() if entry.is_dir()]
        except OSError:
            continue
        directories.append((str(directory.relative_to(common)), stamp))
        pending.extend(sorted(children, reverse=True))
    return packed, tuple(sorted(directories))


def digest(value: object) -> str:
    """A short stable digest of a signature tuple, for the wire."""
    return hashlib.sha1(repr(value).encode("utf-8", "replace")).hexdigest()[:16]


# -- the two reads that need git ---------------------------------------------------------

# The whole-tree status check (`status_porcelain`): one subprocess, asked
# on demand only, with a budget that fits a large tree and refuses a hung
# git.
STATUS_TIMEOUT_S = 2.0


def has_git() -> bool:
    """Whether a `git` is on this machine's PATH."""
    return shutil.which("git") is not None


def status_porcelain(cwd: str | Path | None, timeout: float = STATUS_TIMEOUT_S) -> str | None:
    """`git --no-optional-locks status --porcelain` in *cwd* (gitops'
    runner, this machine's git), or None when git can't answer: no cwd,
    no git on PATH, not a repository, a non-zero exit, a run longer than
    *timeout*. What gitinfo.has_changes / change_summary read, here on
    the service and in the local fallback."""
    if not cwd or not Path(cwd).is_dir() or not has_git():
        return None
    from . import gitinfo, gitops  # at call time: both import this module

    result = gitops.run_git(cwd, gitinfo.status_porcelain_argv(), timeout=timeout)
    return result.stdout if result.ok else None


# -- everything at once ----------------------------------------------------------------

_refs_cache: dict[str, tuple[str, tuple[str, ...], dict[str, str], dict[str, str]]] = {}
_REFS_CACHE_MAX = 64


def read_info(cwd: str | Path | None, now: float | None = None) -> GitInfo:
    """Every read at once, for `git.info`: what the service answers a
    client's mirror with. The heads are re-listed only when the refs
    digest moved (a small cache per common dir), so a tick's refresh is
    stats and a few small reads."""
    stamp = time.monotonic() if now is None else now
    found = find_git_entry(cwd)
    git = git_dir(cwd) if found is not None else None
    if found is None or git is None:
        return GitInfo(fetched_at=stamp)
    root, _entry = found
    common = common_dir(git)
    refs_digest = digest(refs_signature(git))
    key = str(common)
    cached = _refs_cache.get(key)
    if cached is not None and cached[0] == refs_digest:
        _d, remotes, heads, remote_heads = cached
    else:
        remotes = tuple(ranked_remotes(common))
        heads, remote_heads = list_refs(common)
        if len(_refs_cache) >= _REFS_CACHE_MAX:
            _refs_cache.pop(next(iter(_refs_cache)))
        _refs_cache[key] = (refs_digest, remotes, heads, remote_heads)
    return GitInfo(
        root=str(root),
        git_dir=str(git),
        branch=read_head(git),
        default_branch=default_branch_of(common),
        github_url=github_url_of(common),
        index_mtime=index_mtime(git),
        head=head_sha(git),
        markers=operation_markers(git),
        operation=operation_kind(git),
        refs=refs_digest,
        remotes=remotes,
        heads=heads,
        remote_heads=remote_heads,
        fetched_at=stamp,
    )
