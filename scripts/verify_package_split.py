#!/usr/bin/env python3
# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""CI check: the two system packages divide the install between them.

Every channel builds two packages from the one wheel (split-service spec
§3.24, PR-3.7): ``collins-service``, the headless half (the Python package,
the ``collins-service`` and ``collins-keeper`` commands, both systemd user
units), and ``collins``, the window (its launcher, desktop entry, icons and
metainfo). A file both claim is a conflict dpkg or rpm refuses at install
time; a file in the wrong one is a headless box missing its service, or a
service package that drags in a desktop. This reads the two built packages
and holds each to its half. For a .deb pair it also checks that
collins-service declares Breaks and Replaces on a pre-split collins, without
which an upgrade from 0.1.4 fails on the first file it takes over.

Usage:
    python3 scripts/verify_package_split.py deb <collins-service.deb> <collins.deb>
    python3 scripts/verify_package_split.py rpm <collins-service.rpm> <collins.rpm>
"""

from __future__ import annotations

import fnmatch
import subprocess
import sys

APP_ID = "com.episode6.Collins"

# Paths are compared without their leading "/" (dpkg-deb lists "./usr/...",
# rpm "/usr/..."). A pattern is fnmatch'd, so "*" spans directories: the
# site directory differs per channel (dist-packages, site-packages/pythonX.Y).
SERVICE_NEEDS = (
    "usr/bin/collins-service",
    "usr/bin/collins-keeper",
    "usr/lib/systemd/user/collins-service.service",
    "usr/lib/systemd/user/collins-keeper.service",
    "usr/lib/python3*/collins/service/main.py",
    "usr/lib/python3*/collins/service/keeper.py",
    # The MCP shim the CLI spawns from the service's config file.
    "usr/lib/python3*/collins/mcp_shim.py",
    # The Python package is one tree: the window's modules are here too.
    "usr/lib/python3*/collins/app.py",
)
APP_NEEDS = (
    "usr/bin/collins",
    f"usr/share/applications/{APP_ID}.desktop",
    f"usr/share/metainfo/{APP_ID}.metainfo.xml",
    f"usr/share/icons/hicolor/scalable/apps/{APP_ID}.svg",
)
# What each package must not hold: the window's package no Python module, no
# unit and no service command; the service's nothing a desktop shell reads
# (nor the window's private icon root). Neither enables a unit (a link under
# a *.wants/ directory: "*" spans "/", so usr/lib/systemd/user/*/* matches
# one and not the units themselves) nor ships a file under /etc.
APP_REFUSES = (
    "usr/lib/python3*",
    "usr/lib/systemd/*",
    "usr/bin/collins-service",
    "usr/bin/collins-keeper",
    "etc/*",
)
SERVICE_REFUSES = (
    "usr/bin/collins",
    "usr/share/applications/*",
    "usr/share/metainfo/*",
    "usr/share/icons/*",
    "usr/share/collins/*",
    "usr/lib/systemd/user/*/*",
    "etc/*",
)

# The deb transition (Debian Policy §7.6.1): collins-service takes the Python
# tree over from every pre-split collins, so it must declare both fields on
# it, at the marker that never moves (debian/control explains). rpm and
# pacman move the files within the one transaction the exact-version
# dependency forces, so the rpm check has no metadata rule.
DEB_TRANSITION = "collins (<< 0.1.5~)"
DEB_TRANSITION_FIELDS = ("Breaks", "Replaces")


def parse_listing(text: str) -> set[str]:
    """The non-directory paths of a ``perms path`` listing (``dpkg-deb -c``'s
    columns, or rpm's ``%{FILEMODES:perms} %{FILENAMES}``), each without its
    leading ``./`` or ``/``; a symlink's `` -> target`` is dropped."""
    paths: set[str] = set()
    for line in text.splitlines():
        fields = line.split()
        if not fields or fields[0].startswith("d"):
            continue
        # dpkg-deb -c: perms owner size date time path [-> target];
        # rpm: perms path.
        if len(fields) >= 6:
            path = fields[5]
        elif len(fields) == 2:
            path = fields[1]
        else:
            continue
        paths.add(path.removeprefix("./").lstrip("/"))
    return paths


def _matches(paths: set[str], pattern: str) -> list[str]:
    return sorted(p for p in paths if fnmatch.fnmatchcase(p, pattern))


def problems(service: set[str], app: set[str]) -> list[str]:
    """Everything wrong with the split, one line each; empty when right."""
    found = [f"in both packages: {p}" for p in sorted(service & app)]
    found += [f"collins-service lacks {p}" for p in SERVICE_NEEDS if not _matches(service, p)]
    found += [f"collins lacks {p}" for p in APP_NEEDS if not _matches(app, p)]
    found += [f"collins holds {m}" for p in APP_REFUSES for m in _matches(app, p)]
    found += [f"collins-service holds {m}" for p in SERVICE_REFUSES for m in _matches(service, p)]
    return found


def deb_metadata_problems(fields: dict[str, str]) -> list[str]:
    """What collins-service's control fields lack for the transition."""
    return [
        f"collins-service's {name} lacks {DEB_TRANSITION}"
        for name in DEB_TRANSITION_FIELDS
        if DEB_TRANSITION not in [part.strip() for part in fields.get(name, "").split(",")]
    ]


def deb_fields(package: str) -> dict[str, str]:
    """The transition fields of a .deb's control file (absent ones empty)."""
    out = subprocess.run(
        ["dpkg-deb", "-f", package, *DEB_TRANSITION_FIELDS], check=True, capture_output=True, text=True
    ).stdout
    fields: dict[str, str] = {}
    for line in out.splitlines():
        name, sep, value = line.partition(":")
        if sep and name in DEB_TRANSITION_FIELDS:
            fields[name] = value.strip()
    return fields


def listing(kind: str, package: str) -> str:
    if kind == "deb":
        argv = ["dpkg-deb", "-c", package]
    else:
        argv = ["rpm", "-qp", "--qf", "[%{FILEMODES:perms} %{FILENAMES}\n]", package]
    return subprocess.run(argv, check=True, capture_output=True, text=True).stdout


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] not in ("deb", "rpm"):
        print(__doc__.split("Usage:")[1].rstrip(), file=sys.stderr)
        return 2
    kind, service_pkg, app_pkg = argv[1:]
    service = parse_listing(listing(kind, service_pkg))
    app = parse_listing(listing(kind, app_pkg))
    found = problems(service, app)
    if kind == "deb":
        found += deb_metadata_problems(deb_fields(service_pkg))
    if found:
        print("\n".join(found), file=sys.stderr)
        print(f"\n{len(found)} problem(s) in the collins / collins-service split.", file=sys.stderr)
        return 1
    print(f"OK: collins-service holds {len(service)} files, collins {len(app)}, none in both.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
