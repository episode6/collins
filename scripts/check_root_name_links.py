#!/usr/bin/env python3
"""Wiring check for terminal._RootNameLinks — run on a dev machine.

Exercises the stateful side of bare root-name links that the unit tests in
tests/test_linkpatterns.py can't reach: map-time root resolution through a
real TerminalTab, match-tag registration, the monitor-driven rebuild when
the root's name set changes (and *only* then), and teardown on destroy.

This is a script, not a pytest test, on purpose: tests/conftest.py blocks
the GTK-stack namespaces for the whole suite so local runs reproduce CI
(which installs python3-gi only — no gir packages, no display). Testing
widgets for real means running this by hand:

    python3 scripts/check_root_name_links.py

No window is ever shown; the tab is driven unrealized and the child command
is `true`, so no agent CLI launches either.
"""

import gc
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

E2E = tempfile.mkdtemp(prefix="collins-rootlinks-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())
os.environ["COLLINS_DEBUG_API"] = "1"
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"
for _path in (f"{E2E}/projects", f"{E2E}/chats"):
    os.makedirs(_path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as _fh:
    _fh.write("{}")

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
import e2e_service  # noqa: E402
from gi.repository import GLib  # noqa: E402

import collins.terminal as terminal_mod  # noqa: E402
from collins import apilink, remotefiles  # noqa: E402


def _pump(ctx: GLib.MainContext, seconds: float, until=lambda: False) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline and not until():
        ctx.iteration(False)
        time.sleep(0.02)


def main() -> int:
    root = tempfile.mkdtemp(prefix="rootlinks-check-")
    for name in ("README.md", "notes.txt", "with space.txt"):
        open(os.path.join(root, name), "w").close()
    os.mkdir(os.path.join(root, "docs"))

    # The tab spawns its shell on a service of this check's own (PR-1.12b).
    # The root's names are the service's (`fs.names`) and its watch the
    # service's too (`fs.watch {kind: dir}`): the link carries the files
    # transport, as the app installs it on every connect (PR-2.6).
    remotefiles.install(e2e_service.harness_link())
    assert apilink.current() is not None
    tab = terminal_mod.TerminalTab(cwd=root, command_override="true")
    assert tab.link_root == root, tab.link_root

    matchers = [
        m
        for m in gc.get_objects()
        if isinstance(m, terminal_mod._RootNameLinks) and m._terminal is tab.terminal
    ]
    assert len(matchers) == 1, f"expected 1 matcher for the tab terminal, got {len(matchers)}"
    matcher = matchers[0]

    # The tab is the terminal's ancestor already (no realization needed):
    # drive the map handler the way GTK would.
    matcher._on_map(tab.terminal)
    ctx = GLib.MainContext.default()
    _pump(ctx, 5, until=lambda: matcher._names is not None)  # the names are asked of the service
    assert matcher._root == root, matcher._root
    # Directories excluded; the space name is listed here and filtered later
    # by bare_names_pattern (a name-set change to it still means a rebuild).
    assert matcher._names == {"README.md", "notes.txt", "with space.txt"}, matcher._names
    assert matcher._tag is not None
    assert matcher._tag_kinds[matcher._tag] == "file"
    assert matcher._monitor is not None
    first_tag = matcher._tag
    print(f"map wiring OK: tag {first_tag}, names {sorted(matcher._names)}")

    # A new root file must swap the tag for a rebuilt one...
    open(os.path.join(root, "CHANGELOG.md"), "w").close()
    _pump(ctx, 5, until=lambda: "CHANGELOG.md" in (matcher._names or ()))
    assert "CHANGELOG.md" in matcher._names, matcher._names
    assert matcher._tag is not None and matcher._tag != first_tag
    assert first_tag not in matcher._tag_kinds
    assert matcher._tag_kinds[matcher._tag] == "file"
    print(f"monitor rebuild OK: tag {first_tag} -> {matcher._tag}")

    # ...while a content-only write leaves the name set, and so the tag, alone.
    second_tag = matcher._tag
    with open(os.path.join(root, "README.md"), "w") as f:
        f.write("content only\n")
    _pump(ctx, 1.5)
    assert matcher._tag == second_tag, "content-only write must not swap the tag"
    print("content-only write left the tag alone")

    matcher._on_destroy(tab.terminal)
    assert matcher._monitor is None and matcher._refresh_source is None
    print("teardown OK — ALL WIRING CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
