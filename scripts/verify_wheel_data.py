#!/usr/bin/env python3
"""CI check: the wheel and sdist really carry the app's runtime data.

`pip install collins` unpacks a wheel and runs nothing else — no post-install
script, no data_files that land anywhere pipx or a venv would look. So every
file the app needs at runtime ships inside the package: the action icons
app.py puts on the icon search path, the app icon, and the launcher template
and metainfo `collins --install-desktop` writes out, plus the translations.

They get there through `[tool.setuptools.package-data]` globs over paths that
are *symlinks* into data/, and setuptools has not always followed those — the
failure is silent, and the symptom is a released app whose every toolbar icon
is a missing-image glyph. Hence this check, on the built artifacts rather than
on the source tree. The sdist matters as much as the wheel: `python -m build`
builds the sdist first and the wheel out of it, so anything missing there is
missing from what PyPI serves.

Usage: python3 scripts/verify_wheel_data.py [dist-dir]   (default: dist/)
"""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_ID = "com.episode6.Collins"


def expected() -> set[str]:
    """The in-package paths the app reads at runtime, from the source tree."""
    icons = ROOT / "data" / "icons"
    paths = {
        f"collins/icons/{APP_ID}.svg",
        f"collins/icons/{APP_ID}-panel.svg",
        f"collins/{APP_ID}.desktop",
        f"collins/{APP_ID}.metainfo.xml",
        "collins/THIRD_PARTY_LICENSES.md",
    }
    actions = sorted((icons / "hicolor" / "scalable" / "actions").glob("*.svg"))
    if not actions:
        sys.exit(f"error: no action icons under {icons} — is this a full checkout?")
    paths |= {f"collins/icons/hicolor/scalable/actions/{p.name}" for p in actions}
    catalogs = sorted((ROOT / "collins" / "locale").glob("*/LC_MESSAGES/*.mo"))
    paths |= {f"collins/locale/{p.parent.parent.name}/LC_MESSAGES/{p.name}" for p in catalogs}
    return paths


def check(label: str, names: set[str], wanted: set[str]) -> list[str]:
    problems = [f"{label}: missing {name}" for name in sorted(wanted - names)]
    # The Debug artwork is a source-checkout thing (start-debug), left out of
    # the packages for the same reason the .deb leaves it out. Scoped to the
    # icon paths: "Debug" is a plausible thing to find in a module name one
    # day, and a data check has no business failing over that.
    problems += [
        f"{label}: ships the debug-only {n}"
        for n in sorted(names)
        if n.startswith("collins/icons/") and "Debug" in n
    ]
    return problems


def main(argv: list[str]) -> int:
    dist = Path(argv[1]) if len(argv) > 1 else ROOT / "dist"
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if not wheels or not sdists:
        sys.exit(f"error: no wheel and sdist in {dist}; build them first.")

    wanted = expected()
    problems: list[str] = []
    for wheel in wheels:
        problems += check(wheel.name, set(zipfile.ZipFile(wheel).namelist()), wanted)
    for sdist in sdists:
        with tarfile.open(sdist) as tar:
            # Entries are prefixed with the sdist's own <name>-<version>/ dir.
            names = {name.split("/", 1)[-1] for name in tar.getnames()}
        problems += check(sdist.name, names, wanted)

    if problems:
        print("\n".join(problems), file=sys.stderr)
        print(
            f"\n{len(problems)} problem(s). Check [tool.setuptools.package-data] in "
            "pyproject.toml, and that collins/icons is still a symlink to data/icons.",
            file=sys.stderr,
        )
        return 1
    print(f"OK: {len(wanted)} data files in each of {len(wheels)} wheel(s), {len(sdists)} sdist(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
