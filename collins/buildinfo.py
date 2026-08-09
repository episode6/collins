# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""What source the running debug build came from.

The debug build runs straight out of a git checkout, so "which build is
this?" is really a question about that checkout: the commit, the branch, and
whether the tree had uncommitted changes. The answer is captured once at
startup — dirtiness especially is a statement about launch time, since the
checkout keeps changing under a long-lived instance — and the About dialog
shows whatever was captured.

Everything here is best-effort. A missing git, a checkout that isn't a
repository (an installed package), or a slow answer all degrade to "no build
info", never to an error: this is a developer convenience, not a feature the
app depends on.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import gitinfo

log = logging.getLogger(__name__)

# Startup runs this synchronously, so the budget matches gitinfo's: long
# enough for `git log`/`git status` in any repository worth working in,
# short enough that launch never visibly stalls on it.
_TIMEOUT_S = 2.0

# The checkout the running code was imported from: this file lives at
# <repo>/collins/buildinfo.py.
_SOURCE_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class BuildInfo:
    """The source checkout's git state, as captured at startup."""

    sha: str  # short commit hash
    title: str  # the commit's subject line
    branch: str | None  # None on a detached HEAD
    dirty: bool  # uncommitted changes at launch (staged, unstaged or untracked)

    def chip(self) -> str:
        """Build-metadata suffix for the About dialog's version chip — the
        part of the story that fits on the dialog's front page."""
        return f"+{self.sha}" + (".dirty" if self.dirty else "")

    def describe(self) -> str:
        """The About-dialog paragraph. Deliberately not translated: it only
        ever appears in the debug build, for whoever is developing Collins."""
        where = f"on {self.branch}" if self.branch else "detached"
        text = f"Debug build: {self.sha} “{self.title}” {where}"
        if self.dirty:
            text += "\nThe worktree had uncommitted changes at launch."
        return text


_captured: BuildInfo | None = None


def capture() -> None:
    """Record the source checkout's git state; called once at startup."""
    global _captured
    _captured = _read(_SOURCE_ROOT)


def captured() -> BuildInfo | None:
    """Whatever `capture` recorded, or None when it found no repository."""
    return _captured


def _read(root: Path) -> BuildInfo | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        result = subprocess.run(
            [git, "--no-optional-locks", "log", "-1", "--format=%h%n%s"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            cwd=str(root),
        )
    except (OSError, subprocess.SubprocessError) as err:
        log.debug("buildinfo: git log in %s failed: %s", root, err)
        return None
    if result.returncode != 0:
        return None
    sha, _, title = result.stdout.strip().partition("\n")
    if not sha:
        return None
    # A branch name read straight off .git/HEAD, and the same dirty question
    # the PR menu asks. On a detached HEAD current_branch echoes back a
    # prefix of the commit hash, which the description already leads with.
    branch = gitinfo.current_branch(root)
    if branch and (branch.startswith(sha) or sha.startswith(branch)):
        branch = None
    return BuildInfo(
        sha=sha,
        title=title,
        branch=branch,
        dirty=gitinfo.has_changes(root),
    )
