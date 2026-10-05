# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The client opens no project file and runs no program of the project's
(split-service spec §3.23 "The pathless test"; created in PR-2.1 with the
walker and the allowlist, made the final list in PR-2.8).

Every filesystem and subprocess site `tests/pathless.py` finds in the
GTK modules and the client helpers must be in `tests/pathless_allowlist.
ALLOWLIST`, and every entry there must still match a site: the list
never grows, and a site that moved to the service comes off it in the
same PR. The git page's, gitinfo's and window._run_git's sites are gone
since PR-2.1 (git goes over the API), and the walker covers the modules
PR-2.1 added for it."""

import pathless
from pathless_allowlist import ALLOWLIST


def test_no_site_outside_the_allowlist():
    found = pathless.walk()
    new = sorted(found - ALLOWLIST)
    assert not new, f"new filesystem or subprocess sites in the client: {new}"


def test_no_stale_entry_in_the_allowlist():
    found = pathless.walk()
    stale = sorted(ALLOWLIST - found)
    assert not stale, f"allowlist entries nothing matches any more (remove them): {stale}"


def test_the_git_pages_sites_are_gone():
    found = pathless.walk()
    for prefix in ("gitpage:", "gitinfo:", "gitsidebar:", "remotegit:", "gitloads:", "gitpatch:"):
        left = sorted(s for s in found if s.startswith(prefix))
        assert not left, left
    assert not any(site.startswith("window:MainWindow._run_git") for site in found)
    assert not any(site.startswith("window:MainWindow._on_git_") for site in found)


def test_the_walker_covers_the_gtk_modules_and_the_helpers():
    walked = set(pathless.modules())
    for name in ("window", "app", "terminal", "gitpage", "diffview", "editor", "sidebar", "prefs"):
        assert name in walked, name
    for name in pathless.CLIENT_HELPERS:
        assert name in walked, name
    for name in ("gitops", "sessions", "store", "state", "chats", "trust", "sandboxplan"):
        assert name not in walked, name  # shared with the service: the service's reads
