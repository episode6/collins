"""Whether the GitHub CLI is there to be used, asked at launch.

`gh` is the one optional tool Collins is meaningfully better with. A session's
pull requests come off its transcript, so the chips appear without it — but
everything *about* one (its state, its checks, whether a reviewer is waiting)
and everything the PR menus can *do* about one are `gh` calls, and all of it
degrades quietly to a number and an empty menu (see prstatus).

Quiet is right for the hundredth pull request and wrong for a first launch,
where a tool that isn't installed is indistinguishable from a feature that
doesn't exist. So the app asks this at launch, and on any answer but READY
shows what it is holding back (ghwelcome, which decides how often).

Two things can be missing, and they have different answers. gh may not be
installed at all — that one is not ours to solve, because the install differs
by platform and by package manager and cli.github.com already carries every
version of it, so the notice links there rather than inventing instructions
Collins would then own. Or gh may be installed and never signed in, which is
one command.

Signed-in-ness is asked locally, with `gh auth token` rather than `gh auth
status`: status validates the token against GitHub, so a laptop on a train
would answer "not signed in" and get told to log in again. Whether credentials
*exist* is what this notice is about, and that question needs no network.

Gtk-free, like prstatus and practions, so it stays testable where GTK isn't.
"""

from __future__ import annotations

import logging
import shutil

from .prstatus import gh_succeeds

log = logging.getLogger(__name__)

# What a check can find. READY means both halves are in place — nothing to
# say, and the notice never appears.
READY = "ready"
MISSING = "missing"  # gh isn't on PATH
LOGGED_OUT = "logged-out"  # gh is installed, with no credentials stored

# Where the install lives, for every platform gh has one for. Linked rather
# than transcribed: which package manager to name is a question this app has
# no way to answer and GitHub's own page already has.
INSTALL_URL = "https://cli.github.com/"
# The whole of the other fix. Not translated — it is typed into a shell.
LOGIN_COMMAND = "gh auth login"


def check() -> str:
    """What, if anything, stands between Collins and `gh`.

    Never on the main thread: it spawns a subprocess (see prstatus.gh_succeeds
    for the timeout it runs under).
    """
    if shutil.which("gh") is None:
        log.info("ghsetup: gh not on PATH")
        return MISSING
    if not gh_succeeds(["auth", "token"]):
        log.info("ghsetup: gh has no stored credentials")
        return LOGGED_OUT
    return READY
