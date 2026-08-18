#!/usr/bin/env python3
"""Verify every committed copy of the Collins version agrees.

pyproject.toml is the reference; collins/__init__.py and debian/changelog must
match it exactly, and docs/releases.md must carry a section for it. The
AppStream metainfo and the AUR PKGBUILD track *released* versions instead, so
they may lag behind main (which always carries the next, not-yet-shipped
version) but must never run ahead. Run by CI on every PR; scripts/
ship-release.py re-checks the exact-match cases at ship time.

See RELEASE_CHECKLIST.md for which PR bumps which file.
"""

import os
import re
import sys

PYPROJECT = "pyproject.toml"
INIT_PY = "collins/__init__.py"
DEBIAN_CHANGELOG = "debian/changelog"
CHANGELOG = "docs/releases.md"
METAINFO = "data/com.episode6.Collins.metainfo.xml"
PKGBUILD = "packaging/aur/PKGBUILD"
SRCINFO = "packaging/aur/.SRCINFO"

errors = []


def error(message):
    errors.append(message)


def read_file(path):
    if not os.path.exists(path):
        print(f"Error: {path} not found (run from the repo root).", file=sys.stderr)
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return f.read()


def parse_version(version, source):
    # MAJOR.MINOR.PATCH, plus an optional fourth segment used by hotfixes
    # (0.1.1.1); tuple comparison orders 0.1.1 < 0.1.1.1 < 0.1.2 correctly.
    if not re.fullmatch(r"\d+\.\d+\.\d+(\.\d+)?", version):
        error(f"{source}: version {version!r} is not plain dotted numerals")
        return None
    return tuple(int(part) for part in version.split("."))


def main():
    match = re.search(r'^version\s*=\s*"([^"]+)"', read_file(PYPROJECT), re.MULTILINE)
    if not match:
        print(f"Error: could not find the version in {PYPROJECT}", file=sys.stderr)
        sys.exit(1)
    version = match.group(1)
    version_tuple = parse_version(version, PYPROJECT)

    init_match = re.search(
        r'^__version__\s*=\s*"([^"]+)"', read_file(INIT_PY), re.MULTILINE
    )
    if not init_match:
        error(f"{INIT_PY}: no __version__ found")
    elif init_match.group(1) != version:
        error(f"{INIT_PY}: __version__ is {init_match.group(1)}, expected {version}")

    deb_match = re.match(r"^collins \(([^)]+)\) (\S+);", read_file(DEBIAN_CHANGELOG))
    if not deb_match:
        error(f"{DEBIAN_CHANGELOG}: could not parse the top entry")
    else:
        if deb_match.group(1) != version:
            error(
                f"{DEBIAN_CHANGELOG}: top entry is {deb_match.group(1)}, "
                f"expected {version}"
            )
        if deb_match.group(2) != "UNRELEASED":
            error(
                f"{DEBIAN_CHANGELOG}: top entry targets {deb_match.group(2)}; "
                "it must stay UNRELEASED in git (the PPA series is stamped by "
                "build-ppa-source.sh in its temp tree only)"
            )

    if not re.search(
        rf"^###\s+v{re.escape(version)}(\s|$)", read_file(CHANGELOG), re.MULTILINE
    ):
        error(f"{CHANGELOG}: no '### v{version}' section for the current version")

    meta_match = re.search(r'<release version="([^"]+)"', read_file(METAINFO))
    if not meta_match:
        error(f"{METAINFO}: no <release> entry found")
    else:
        meta_tuple = parse_version(meta_match.group(1), METAINFO)
        if meta_tuple and version_tuple and meta_tuple > version_tuple:
            error(
                f"{METAINFO}: top release {meta_match.group(1)} is ahead of "
                f"the current version {version}"
            )

    pkg_match = re.search(r"^pkgver=(\S+)", read_file(PKGBUILD), re.MULTILINE)
    if not pkg_match:
        error(f"{PKGBUILD}: no pkgver found")
    else:
        pkgver = pkg_match.group(1)
        pkg_tuple = parse_version(pkgver, PKGBUILD)
        if pkg_tuple and version_tuple and pkg_tuple > version_tuple:
            error(f"{PKGBUILD}: pkgver {pkgver} is ahead of the current version {version}")
        srcinfo = read_file(SRCINFO)
        src_match = re.search(r"^\s*pkgver = (\S+)", srcinfo, re.MULTILINE)
        if not src_match or src_match.group(1) != pkgver:
            found = src_match.group(1) if src_match else "<missing>"
            error(f"{SRCINFO}: pkgver is {found}, but {PKGBUILD} says {pkgver}")
        if f"/v{pkgver}.tar.gz" not in srcinfo:
            error(f"{SRCINFO}: source URL does not point at the v{pkgver} tag")

    if errors:
        for message in errors:
            print(f"Error: {message}", file=sys.stderr)
        sys.exit(1)
    print(f"All committed versions are consistent with {PYPROJECT} ({version}).")


if __name__ == "__main__":
    main()
