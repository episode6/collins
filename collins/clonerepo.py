# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The GTK-free half of Add project → Clone Repository (clonedialog.py).

The dialog has one text box that is two things at once: a filter over the
repositories `gh` can list for the signed-in user (their own, ones they
collaborate on, and every repository their organizations let them see), and
a place to paste a clone address. This module decides which one the text
is, where the clone lands, and what gets run:

- `parse_source` turns typed text into a `CloneSource`, or None when the
  text is only a filter word. A bare `owner/repo` counts as a GitHub
  repository; anything with a scheme or git's `host:path` shape counts as
  an address. Everything else is a filter.
- `fetch_repos` pages `gh api user/repos` (bounded — `MAX_PAGES`) and
  `parse_repos` validates each page, since the reply is foreign content.
- `filter_repos` ranks the list for the typed words (fuzzy over the full
  name, then plain substring over the description).
- `destination` / `destination_status` say where the checkout goes and
  whether git will accept it there — the dialog prints both, so the final
  path is never a surprise.
- `clone_argv` builds the command: `gh repo clone` for GitHub (the user's
  own protocol preference and gh's credentials, no prompt), `git clone --`
  for any other address.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import fuzzy

# The GitHub list: 100 a page (the API's ceiling), newest push first, and at
# most ten pages. A member of a huge organization can see thousands of
# repositories; the filter works on what arrived, and an address or an
# `owner/repo` typed by hand still clones anything past the cut.
REPO_PAGE = 100
MAX_PAGES = 10
# How long one page may take before the list gives up on it.
PAGE_TIMEOUT_S = 30

# Bounds on what a reply may carry into a row: GitHub's own limits are
# 39 (login) + 100 (name), and descriptions are capped at 350.
_MAX_FULL_NAME = 140
_MAX_DESCRIPTION = 350
# How much text the box accepts as an address at all.
_MAX_SOURCE = 2048
# How many rows the dialog shows for one filter.
DEFAULT_LIMIT = 200
# The tail of git's complaint the dialog prints when a clone fails.
_MAX_ERROR = 600

# `owner/repo` as GitHub spells them: a login is alphanumerics and single
# hyphens (never leading), a repository name adds `.` and `_`.
_OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
_REPO = r"[A-Za-z0-9._-]{1,100}"
_GITHUB_SHORT = re.compile(rf"^({_OWNER})/({_REPO})$")
# The schemes git clones from. `ext::` and friends are left out on purpose:
# git itself refuses them by default, and a typed box is no place to opt in.
_SCHEMES = frozenset({"https", "http", "ssh", "git", "git+ssh", "ssh+git", "file"})
# git's scp-like form, `[user@]host:path`: a colon before any slash, and no
# `://` (which would make it a URL).
_SCP = re.compile(r"^(?:[A-Za-z0-9._-]+@)?([A-Za-z0-9.-]+):(?!//)([^:\s]\S*)$")
# git reads `<transport>::<address>` as "run git-remote-<transport>" —
# never something a pasted address should be able to ask for.
_HELPER = re.compile(r"^[A-Za-z0-9+.-]*::")
_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})

GITHUB = "github"
GIT = "git"

# destination_status answers.
READY = "ready"  # nothing there yet; the parent exists
CREATES_PARENT = "creates-parent"  # git will create the missing folders too
EXISTS = "exists"  # a non-empty directory (or a file) is already there
BLOCKED = "blocked"  # something on the way is a file, not a folder
RELATIVE = "relative"  # the target directory isn't an absolute path


@dataclass(frozen=True)
class Repo:
    """One repository the signed-in user can see, as the list shows it."""

    full_name: str
    description: str = ""
    private: bool = False
    fork: bool = False
    archived: bool = False

    @property
    def name(self) -> str:
        return self.full_name.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class CloneSource:
    """What the Clone button would clone.

    *kind* is GITHUB (cloned through `gh repo clone`, which accepts both an
    `owner/repo` and a github.com address) or GIT (a plain `git clone` of
    the address). *spec* is what's handed to that command; *name* is the
    folder the clone makes, the way git derives it."""

    kind: str
    spec: str
    name: str

    @property
    def github_name(self) -> str | None:
        """`owner/repo` for a GitHub source, when it can be read off."""
        if self.kind != GITHUB:
            return None
        if _GITHUB_SHORT.match(self.spec):
            return _strip_git(self.spec)
        return _github_path(self.spec)


def _strip_git(text: str) -> str:
    return text[:-4] if text.endswith(".git") else text


def _dir_name(path: str) -> str | None:
    """The folder git names a clone of *path* after: the last component,
    trailing slashes and `/.git` and `.git` stripped. None for a name no
    folder can have (empty, `.`, `..`)."""
    path = path.rstrip("/")
    if path.endswith("/.git"):
        path = path[:-5].rstrip("/")
    name = _strip_git(path.rsplit("/", 1)[-1].rsplit(":", 1)[-1])
    if name in ("", ".", "..") or "\0" in name:
        return None
    return name


def _github_path(text: str) -> str | None:
    """`owner/repo` out of a github.com address, or None."""
    m = _SCP.match(text)
    if m and m.group(1).lower() in _GITHUB_HOSTS:
        path = m.group(2)
    else:
        try:
            parts = urlsplit(text)
        except ValueError:
            return None
        if (parts.hostname or "").lower() not in _GITHUB_HOSTS:
            return None
        path = parts.path
    path = _strip_git(path.strip("/"))
    return path if _GITHUB_SHORT.match(path) else None


def parse_source(text: str) -> CloneSource | None:
    """The clone *text* stands for, or None when it's only a filter.

    `owner/repo` is a GitHub repository; an address with a scheme git
    clones from, or in git's `host:path` form, is an address (a GitHub
    one goes through gh all the same). Nothing starting with `-` is ever
    a source: it would reach the command line as an option.
    """
    text = (text or "").strip()
    if not text or len(text) > _MAX_SOURCE or text.startswith("-"):
        return None
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in text):
        return None
    if _HELPER.match(text):
        return None
    m = _GITHUB_SHORT.match(text)
    if m:
        name = _dir_name(m.group(2))
        return CloneSource(GITHUB, _strip_git(text), name) if name else None
    if "://" in text:
        try:
            parts = urlsplit(text)
        except ValueError:
            return None
        if parts.scheme.lower() not in _SCHEMES:
            return None
        if parts.scheme.lower() != "file" and not parts.hostname:
            return None
        name = _dir_name(parts.path)
        if not name:
            return None
        kind = GITHUB if _github_path(text) else GIT
        return CloneSource(kind, text, name)
    m = _SCP.match(text)
    if m:
        name = _dir_name(m.group(2))
        if not name:
            return None
        kind = GITHUB if _github_path(text) else GIT
        return CloneSource(kind, text, name)
    return None


def is_address(text: str) -> bool:
    """Whether *text* is a clone address rather than an `owner/repo` or a
    filter word — the one case where what's typed outranks a selected row."""
    source = parse_source(text)
    return source is not None and not _GITHUB_SHORT.match(source.spec)


def repos_page_args(page: int) -> list[str]:
    """The `gh` argv for one page of the user's repositories: owned,
    collaborated on, and reachable through an organization, newest push
    first, trimmed to the fields a row shows."""
    return [
        "api",
        f"user/repos?per_page={REPO_PAGE}&page={page}&sort=pushed"
        "&affiliation=owner,collaborator,organization_member",
        "--jq",
        "map({full_name, description, private, fork, archived})",
    ]


def parse_repos(data: object) -> list[Repo] | None:
    """One page of `repos_page_args`' reply as Repos; None when the reply
    isn't a list at all. Entries that don't fit the shape are dropped."""
    if not isinstance(data, list):
        return None
    repos = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        full_name = entry.get("full_name")
        if (
            not isinstance(full_name, str)
            or len(full_name) > _MAX_FULL_NAME
            or not _GITHUB_SHORT.match(full_name)
        ):
            continue
        description = entry.get("description")
        if not isinstance(description, str):
            description = ""
        description = " ".join(description.split())[:_MAX_DESCRIPTION]
        repos.append(
            Repo(
                full_name=full_name,
                description=description,
                private=entry.get("private") is True,
                fork=entry.get("fork") is True,
                archived=entry.get("archived") is True,
            )
        )
    return repos


def fetch_repos(
    fetch: Callable[[list[str]], object | None],
    on_page: Callable[[list[Repo]], None] | None = None,
) -> list[Repo] | None:
    """Every repository the user can see, a page at a time, through *fetch*
    (a `gh_json`-shaped callable). *on_page* hears the running total after
    each page, so a long list fills in as it arrives. None when the first
    page fails; a later failure keeps what already arrived."""
    repos: list[Repo] = []
    seen: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        batch = parse_repos(fetch(repos_page_args(page)))
        if batch is None:
            return None if page == 1 else repos
        for repo in batch:
            if repo.full_name not in seen:
                seen.add(repo.full_name)
                repos.append(repo)
        if on_page is not None:
            on_page(list(repos))
        if len(batch) < REPO_PAGE:
            break
    return repos


def filter_repos(repos: list[Repo], query: str, limit: int = DEFAULT_LIMIT) -> list[Repo]:
    """The best *limit* of *repos* for *query*, best first.

    Every whitespace-separated word must match: fuzzily against the full
    name (so `e6 coll` finds episode6/collins, the repository name
    outranking the owner), or failing that as a plain substring of the
    description. Name matches rank above description-only ones; ties keep
    the list's own order, newest push first. An empty query is the list
    as it came."""
    words = query.lower().split()
    if not words:
        return repos[:limit]
    scored: list[tuple[int, int, Repo]] = []
    for index, repo in enumerate(repos):
        description = repo.description.lower()
        total = 0
        for word in words:
            score = fuzzy.match(word, repo.full_name)
            if score is None:
                if word not in description:
                    break
                score = -1000  # description-only: below every name hit
            total += score
        else:
            scored.append((-total, index, repo))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [repo for _score, _index, repo in scored[:limit]]


def parent_directory(setting: str | None, home: str | None = None) -> str:
    """The clone_directory setting as a path: `~` expanded, empty meaning
    the home folder."""
    home = home if home is not None else str(Path.home())
    text = (setting or "").strip()
    if not text or text == "~":
        return home
    if text.startswith("~/"):
        return os.path.join(home, text[2:])
    return text


def destination(parent: str, source: CloneSource | None, home: str | None = None) -> Path | None:
    """Where *source* lands under *parent* (typed as in the dialog, `~`
    allowed): the parent plus the folder name git gives the clone. None
    without a source or with a relative parent."""
    if source is None:
        return None
    path = parent_directory(parent, home)
    if not os.path.isabs(path):
        return None
    return Path(os.path.normpath(path)) / source.name


def destination_status(parent: str, dest: Path | None, home: str | None = None) -> str:
    """Whether git will clone into *dest*: READY, CREATES_PARENT (the
    target directory doesn't exist yet — git makes it), EXISTS (something
    that isn't an empty folder is there; git refuses), BLOCKED (a file
    sits where a folder has to be) or RELATIVE (the typed target isn't an
    absolute path)."""
    if not os.path.isabs(parent_directory(parent, home)):
        return RELATIVE
    if dest is None:
        return READY
    try:
        if dest.is_dir():
            return EXISTS if any(dest.iterdir()) else READY
        if dest.exists() or dest.is_symlink():
            return EXISTS
        for ancestor in dest.parents:
            if ancestor.exists():
                if not ancestor.is_dir():
                    return BLOCKED
                return READY if ancestor == dest.parent else CREATES_PARENT
    except OSError:
        return BLOCKED
    return BLOCKED


def clone_argv(source: CloneSource, dest: Path, gh: str | None, git: str = "git") -> list[str]:
    """The command that clones *source* into *dest*.

    A GitHub source goes through `gh repo clone` when gh is here: it clones
    over the protocol the user configured (or the one the address spells)
    with gh's own credentials, which is what makes a private repository
    work without a credential prompt nobody could answer. Without gh an
    `owner/repo` becomes its https address. Everything else is `git clone`,
    with `--` so the address can never be read as an option."""
    if source.kind == GITHUB and gh:
        return [gh, "repo", "clone", source.spec, str(dest)]
    spec = source.spec
    if source.kind == GITHUB and _GITHUB_SHORT.match(spec):
        spec = f"https://github.com/{spec}.git"
    return [git, "clone", "--", spec, str(dest)]


def clone_env(base: dict[str, str]) -> dict[str, str]:
    """The environment a clone runs in: *base* with every interactive
    prompt turned off. The clone has no terminal to ask on — a credential
    prompt would hang it — so a missing login fails fast with git's own
    message instead."""
    env = dict(base)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GH_PROMPT_DISABLED"] = "1"
    return env


def error_summary(output: str) -> str:
    """What to print when a clone fails: the last few meaningful lines of
    its output, progress redraws (`\\r`) collapsed to their final state,
    bounded."""
    lines = []
    # Not splitlines(): it breaks on the \r as well, and those are the
    # redraws to collapse, not lines.
    for raw in (output or "").split("\n"):
        line = raw.rsplit("\r", 1)[-1].strip()
        if line:
            lines.append(line)
    text = "\n".join(lines[-4:])
    if len(text) > _MAX_ERROR:
        text = "…" + text[-_MAX_ERROR:]
    return text
