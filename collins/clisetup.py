"""Where the Claude Code CLI is, asked before anything else is.

Every session Collins starts, resumes, titles or draws an icon with goes
through the `claude` command, found on PATH. A shell has the PATH the user
built; a desktop launch has the PATH the session manager built, which on
most distros never learns about `~/.local/bin` — exactly where Claude
Code's own installer puts its launcher. So the app comes up, finds no CLI,
and every one of those features quietly reads as "no sessions": a blank
sidebar indistinguishable from an empty one.

That is not a degradation to note in passing (as a missing `gh` is — see
ghsetup); without the CLI there is nothing for Collins to manage. So a
launch that can't find it stops and asks, once, where it is (cliwelcome),
and the answer is kept in settings and re-applied at every later launch
before anything goes looking.

Two rules shape what counts as a good answer:

- The path is kept exactly as given, symlinks unexpanded. The native
  installer's `~/.local/bin/claude` is a symlink into a per-version
  directory, repointed on every self-update; resolving it would store a
  path that dies with the next release.
- A path with a version number in it is refused outright, for the same
  reason from the other side: it works today and breaks on the next
  update, silently returning the app to the blank sidebar — but this time
  with a stored answer that looks authoritative. One carve-out: inside a
  version manager's tree (nvm, asdf) *every* path is versioned — there is
  no stable launcher to demand — so those are accepted with a warning
  instead of refused (VERSION_MANAGED, not VERSIONED).

Applying an answer means appending its directory to this process's PATH —
appending, not prepending, so a directory someone once picked can never
shadow `git` or `gh` for the rest of the app. Everything downstream
(providers, titles, icongen, and every tab's shell, which inherits the
environment) then finds the CLI the way it always did: `shutil.which`.

Gtk-free, like ghsetup, so it stays testable where GTK isn't.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

# The executable everything downstream looks up — also the one name an
# answer is allowed to have: pointing PATH at a directory only helps if
# `shutil.which(CLI_NAME)` finds the file there.
CLI_NAME = "claude"

# The setting a confirmed location is stored under, exactly as typed or
# picked (symlinks unexpanded, ~ unexpanded). "" = rely on PATH alone.
PATH_SETTING = "claude_cli_path"

# What validate() can say about a candidate path.
OK = "ok"
MISSING = "missing"  # no executable file there
BAD_NAME = "bad-name"  # an executable, but not one a `claude` lookup will find
VERSIONED = "versioned"  # works now, breaks on the CLI's next self-update
# A versioned path inside a version manager's tree: usable — no stable
# alternative exists to insist on — but not validatable as one, so the ask
# accepts it with a warning rather than a green check.
VERSION_MANAGED = "version-managed"

# A version number in a path component: "2.1.226", "v20.1.0",
# "claude-1.2" — anything with two dot-separated runs of digits.
# Deliberately broad: it also catches a stable-but-dotted install like
# /opt/myco/app-2.1/bin/claude, whose owner can rename or symlink around
# the refusal — the cost of missing a versioned tree is a silently broken
# stored answer, the cost of over-matching is one explained red x.
_VERSION_RE = re.compile(r"\d+\.\d+")

# Directory names that mark a version manager's tree. Exact component
# matches, dotted or bare (~/.nvm at home, /opt/nvm in containers).
_VERSION_MANAGER_DIRS = frozenset({".nvm", "nvm", ".asdf", "asdf"})

# Where an installed CLI tends to be when PATH doesn't say. Ordered:
# the official installer's stable launcher first, then its predecessor,
# then the places an npm/user install lands. Deliberately absent: version
# managers' trees (nvm, asdf, volta's tool dirs) — every path in them is
# versioned, so none makes a pre-fill worth suggesting; one browsed to by
# hand is accepted with a warning (VERSION_MANAGED).
def known_locations() -> list[Path]:
    home = Path.home()
    return [
        home / ".local" / "bin" / CLI_NAME,  # the native installer's launcher
        home / ".claude" / "local" / CLI_NAME,  # older native installs
        home / "bin" / CLI_NAME,
        home / ".npm-global" / "bin" / CLI_NAME,  # npm with a user-level prefix
        Path("/usr/local/bin") / CLI_NAME,  # npm -g under a system node
        Path("/usr/bin") / CLI_NAME,  # a distro package
    ]


def on_path() -> bool:
    """Whether the CLI is findable right now, PATH as it currently stands."""
    return shutil.which(CLI_NAME) is not None


def validate(text: str) -> str:
    """What a candidate path is worth. Never resolves symlinks — the whole
    point of accepting `~/.local/bin/claude` is accepting the symlink."""
    text = text.strip()
    if not text:
        return MISSING
    path = Path(os.path.expanduser(text))
    if not (path.is_file() and os.access(path, os.X_OK)):
        return MISSING
    # Versioned before misnamed: the native installer's real binary is
    # *named* its version ("…/versions/2.1.226"), and "this path dies on
    # update" is the answer that explains what to point at instead.
    if any(_VERSION_RE.search(part) for part in path.parts):
        if any(part in _VERSION_MANAGER_DIRS for part in path.parts):
            # A version manager's tree offers nothing unversioned to point
            # at instead, so this one is usable — but only the actual CLI
            # is: a VERSION_MANAGED answer goes on PATH like any other, so
            # the name still has to be the one everything looks up.
            if path.name != CLI_NAME:
                return BAD_NAME
            return VERSION_MANAGED
        return VERSIONED
    if path.name != CLI_NAME:
        return BAD_NAME
    return OK


def detect() -> str:
    """The best guess to pre-fill the ask with: the first known location
    that would be accepted, as-is (symlinks unexpanded). "" when none is."""
    for candidate in known_locations():
        if validate(str(candidate)) == OK:
            return str(candidate)
    return ""


def apply(text: str) -> bool:
    """Put a confirmed location's directory on this process's PATH, and say
    whether the CLI is now findable.

    Appended, not prepended: the CLI is the only thing being looked for
    here, and a directory someone picked in a dialog must never get to
    shadow `git` or `gh` for every subprocess the app runs from then on.
    Tabs and workers inherit os.environ, so one application covers them all.
    """
    text = text.strip()
    if not text:
        return on_path()
    directory = str(Path(os.path.expanduser(text)).parent)
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if directory not in parts:
        os.environ["PATH"] = os.pathsep.join([*parts, directory])
        log.info("clisetup: added %s to PATH", directory)
    return on_path()


def apply_saved(state) -> None:
    """Re-apply the stored answer, before anything goes looking for the CLI.

    A stored path that has since gone stale (moved, uninstalled) applies as
    a harmless dead PATH entry — the launch check then fails exactly as it
    would with no answer at all, and the ask comes back.
    """
    text = state.get_setting(PATH_SETTING) or ""
    if text.strip():
        apply(text)
