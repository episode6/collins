# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Best-effort git repository info, read straight from `.git` where it can be.

Finding the branch (`current_branch`), the branch the repository treats as
its trunk (`default_branch`) or the repository's page on GitHub
(`github_url`) is a couple of stat calls and one small file read, with no
`git` processes spawned — cheap enough for the tab footer's 2s poll, and for
a context menu that asks on every right-click. Asking whether the tree is
dirty (`has_changes`) or which entries are ignored (`ignored_names`) can't be
answered that way, so those shell out and are only ever asked on demand.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

_REF_PREFIX = "ref:"
_BRANCH_REF_PREFIX = "refs/heads/"

# A `[remote "name"]` stanza in a git config; git treats section names
# case-insensitively, so this does too.
_REMOTE_SECTION = re.compile(r'^\[remote\s+"([^"]+)"\]', re.IGNORECASE)

# Which remote speaks for the project when several do. Anything unlisted goes
# after these in name order, so the answer can't wobble between right-clicks.
_REMOTE_ORDER = ("origin", "upstream", "github")

# The one host whose remotes get a GitHub page. An enterprise install answers
# on its own domain and isn't recognized here — better no menu item than one
# pointing at github.com for a repository that doesn't live there.
_GITHUB_HOST = "github.com"

# Schemes a remote may be written in. `file:`/`javascript:` and friends never
# get this far: what this module hands back is always an https URL it built
# itself, but the path it builds it out of comes from a repository's config,
# which is untrusted like any other repo content.
_REMOTE_SCHEMES = frozenset({"https", "http", "ssh", "git"})

# A remote written the scp way — `git@github.com:owner/repo.git` — which has
# no scheme for urlsplit to find. Only tried when there is no `://` at all.
_SCP_LIKE = re.compile(r"^(?:[^/@]+@)?(?P<host>[^/:]+):(?P<path>.+)$")

# One path segment of an owner/repo pair, as GitHub mints them: this is the
# gate that keeps whatever a config invented out of the URL a browser is
# handed, so a segment of nothing but dots (which would climb the path) is
# out too.
_REPO_NAME = re.compile(r"^(?!\.+$)[A-Za-z0-9._-]+$")

# Where a clone records which branch its remote considers the main one:
# `refs/remotes/<remote>/HEAD` is a symbolic ref to it (`ref:
# refs/remotes/origin/main`). Only a clone writes one — `git remote add` and
# `git init` don't — so the names git and GitHub mint by default stand in
# where there is none, in the order GitHub's own default came to be.
_REMOTE_HEAD_FILE = "HEAD"
_DEFAULT_BRANCH_NAMES = ("main", "master")

# Long enough for `git status` in a repository of any size worth working in,
# short enough that a menu built on the answer doesn't visibly stall waiting
# for it. A repository slower than this is treated as clean.
_STATUS_TIMEOUT_S = 2.0

# `ignored_names` runs on the GTK main loop (the file tree asks while
# building rows), so its budget is much tighter than the menu-building
# _STATUS_TIMEOUT_S: `check-ignore` reads only the exclude files, never the
# index, and answers in milliseconds — a repository that can't make this
# deadline just renders undimmed rather than stalling every expand.
_IGNORE_TIMEOUT_S = 0.5


def current_branch(cwd: str | Path | None) -> str | None:
    """Name of the branch checked out in the repo enclosing *cwd*.

    Returns None when *cwd* is empty, missing, or not inside a git repo.
    A detached HEAD yields the abbreviated commit hash instead of a name.
    Handles worktrees/submodules, whose `.git` is a pointer file.
    """
    git_dir = _git_dir(cwd)
    return _read_head(git_dir) if git_dir else None


def default_branch(cwd: str | Path | None) -> str | None:
    """The branch the repository enclosing *cwd* treats as its trunk, or None.

    What the remote says first — `refs/remotes/<remote>/HEAD`, a clone's
    record of the remote's default branch, remotes ranked the way
    `github_url` ranks them — and, for a repository that was never cloned
    (no remote HEAD at all), whichever of `main` and `master` exists as a
    local branch. Read off the repository's files like `current_branch`, so
    a context menu can ask on every right-click; in a worktree the refs are
    the main checkout's (`commondir`), which is where every worktree's
    branches live.

    None outside a repository and for one whose trunk can't be named. The
    caller offers to check the branch out, so a guess would be worse than
    no answer.
    """
    git_dir = _git_dir(cwd)
    if git_dir is None:
        return None
    common = _common_dir(git_dir)
    remotes = _remote_urls(common / "config")
    for name in sorted(remotes, key=_remote_rank):
        branch = _remote_head(common, name)
        if branch:
            return branch
    for name in _DEFAULT_BRANCH_NAMES:
        if _has_local_branch(common, name):
            return name
    return None


def github_url(cwd: str | Path | None) -> str | None:
    """The GitHub page of the repository enclosing *cwd*, or None.

    Read out of the repository's own `config` rather than asked of `git` or
    `gh`, so the sidebar can ask while building a context menu. In a worktree
    the config is the main checkout's (`commondir`); in a submodule it is the
    submodule's own, which is what its remote — and so its page — should be.

    None for everything that isn't a repository with a github.com remote:
    no cwd, no `.git`, a config with no remotes, remotes on another host, a
    remote path that isn't a plain `owner/repo`. The caller offers a menu item
    claiming there is a page to open, so anything short of certain means no.
    """
    git_dir = _git_dir(cwd)
    if git_dir is None:
        return None
    urls = _remote_urls(_common_dir(git_dir) / "config")
    for name in sorted(urls, key=_remote_rank):
        page = _github_page(urls[name])
        if page is not None:
            return page
    return None


def has_changes(cwd: str | Path | None) -> bool:
    """Whether the repo enclosing *cwd* has work in it that isn't committed.

    Staged, unstaged and untracked all count: all three are changes a new pull
    request would be opened for, which is the one question this answers (see
    practions.NEW_PR). Ignored files don't — `git status` leaves them out, and
    so does the pull request.

    A subprocess (like `ignored_names`), and the reason it is asked on demand
    rather than from the footer's poll: "is this tree dirty?" means comparing
    every tracked file against the index, which is `git status`' whole job and
    not something to re-derive off `.git`. `--no-optional-locks` keeps it from
    taking the index lock or writing a refreshed index, so it can't collide
    with the agent's own git commands in the same repository.

    False for every question that can't be answered — no cwd, no git, not a
    repository, a git that took too long. What is built on the answer is a
    menu item claiming there is something to open a pull request *for*, so
    anything short of git saying so means no.
    """
    if not cwd or not Path(cwd).is_dir():
        return False
    git = shutil.which("git")
    if git is None:
        return False
    try:
        result = subprocess.run(
            [git, "--no-optional-locks", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=_STATUS_TIMEOUT_S,
            cwd=str(cwd),
        )
    except (OSError, subprocess.SubprocessError) as err:
        log.debug("gitinfo: git status in %s failed: %s", cwd, err)
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def ignored_names(directory: str | Path | None, names: list[str]) -> set[str]:
    """Which of *names* (entries directly inside *directory*) git ignores.

    One batched `git check-ignore --stdin -z` per call — the file tree asks
    once per directory listing (on expand and on the debounced refresh), never
    per row, so this stays one short-lived process per user action. `-z` on
    both ends keeps any filename byte-clean in transit.

    The caller is on the GTK main loop, so this is kept cheap: outside a
    repository no process is spawned at all (a pure-filesystem `.git` walk,
    like `current_branch`'s, answers first), and inside one the subprocess
    gets only `_IGNORE_TIMEOUT_S` before the answer becomes "nothing".

    Empty set for every case that can't be answered — no git on PATH, not a
    repository, a timeout. What is built on the answer is only a dimmed row,
    so anything short of git saying "ignored" means shown at full strength.
    """
    if not directory or not names or not _in_repository(Path(directory)):
        return set()
    git = shutil.which("git")
    if git is None:
        return set()
    try:
        result = subprocess.run(
            [git, "--no-optional-locks", "check-ignore", "-z", "--stdin"],
            input="\0".join(names) + "\0",
            capture_output=True,
            text=True,
            timeout=_IGNORE_TIMEOUT_S,
            cwd=str(directory),
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as err:
        log.debug("gitinfo: git check-ignore in %s failed: %s", directory, err)
        return set()
    # 0 = some ignored, 1 = none ignored; anything else (128: not a repo,
    # bad input) means "don't know", which reads the same as "none".
    if result.returncode != 0:
        return set()
    return {name for name in result.stdout.split("\0") if name}


def _git_dir(cwd: str | Path | None) -> Path | None:
    """The git directory of the repository enclosing *cwd* — the nearest
    `.git` walking upwards, resolved through the pointer file a worktree or
    submodule has there instead of a directory. None outside a repository, and
    for a pointer file that doesn't point anywhere."""
    if not cwd:
        return None
    start = Path(cwd)
    if not start.is_dir():
        return None
    for directory in (start, *start.parents):
        git = directory / ".git"
        if git.is_dir():
            return git
        if git.is_file():  # worktree or submodule: "gitdir: <real git dir>"
            return _resolve_gitdir_pointer(git)
    return None


def _common_dir(git_dir: Path) -> Path:
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


def _remote_urls(config: Path) -> dict[str, str]:
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


def _remote_rank(name: str) -> tuple[int, str]:
    conventional = name.lower() in _REMOTE_ORDER
    index = _REMOTE_ORDER.index(name.lower()) if conventional else len(_REMOTE_ORDER)
    return index, name


def _github_page(remote_url: str) -> str | None:
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


def _in_repository(start: Path) -> bool:
    """Whether *start* is inside a git repository — a couple of stat calls
    (`.git` may be a directory, or a worktree/submodule pointer file), so a
    tree outside any repository never pays for a `git` process."""
    if not start.is_dir():
        return False
    return any((directory / ".git").exists() for directory in (start, *start.parents))


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


def _remote_head(common_dir: Path, remote: str) -> str | None:
    """The branch `refs/remotes/<remote>/HEAD` points at, or None — for a
    remote that has no HEAD ref (never cloned from, or pruned), or one whose
    HEAD is detached (`git remote set-head --delete` leaves none; a bare
    commit hash in there is a remote with no default to speak of)."""
    ref_file = common_dir / "refs" / "remotes" / remote / _REMOTE_HEAD_FILE
    try:
        head = ref_file.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not head.startswith(_REF_PREFIX):
        return None
    prefix = f"refs/remotes/{remote}/"
    ref = head[len(_REF_PREFIX) :].strip()
    if not ref.startswith(prefix):
        return None
    return ref[len(prefix) :] or None


def _has_local_branch(common_dir: Path, name: str) -> bool:
    """Whether `refs/heads/<name>` exists — as a loose ref file, or packed
    into `packed-refs` (`<hash> refs/heads/<name>` per line), which is where
    `git gc` moves it."""
    if (common_dir / "refs" / "heads" / name).is_file():
        return True
    try:
        packed = (common_dir / "packed-refs").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    wanted = f"{_BRANCH_REF_PREFIX}{name}"
    for line in packed.splitlines():
        if line.startswith(("#", "^")):
            continue
        parts = line.split(" ", 1)
        if len(parts) == 2 and parts[1].strip() == wanted:
            return True
    return False


def _read_head(git_dir: Path) -> str | None:
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if head.startswith(_REF_PREFIX):
        ref = head[len(_REF_PREFIX) :].strip()
        if ref.startswith(_BRANCH_REF_PREFIX):
            return ref[len(_BRANCH_REF_PREFIX) :] or None
        return ref or None
    return head[:8] or None  # detached HEAD
