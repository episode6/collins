"""Install the desktop entry, app icon and metainfo for a pip/pipx install.

`data/install.sh` does this for a checkout and the packages do it system-wide,
but `pip install collins` runs no post-install script at all — so a wheel
install has a working `collins` command and nothing in the app grid. This is
what `collins --install-desktop` runs: the same three files, written under
XDG_DATA_HOME for the current user only.

Nothing here imports gi: the files come out of the package (see the
package-data block in pyproject.toml), and the desktop database and icon cache
are refreshed by the same two commands install.sh calls.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_ID = "com.episode6.Collins"

_PACKAGE = Path(__file__).resolve().parent


def data_home() -> Path:
    """The XDG data root to install into (~/.local/share by default)."""
    if xdg := os.environ.get("XDG_DATA_HOME"):
        return Path(xdg)
    return Path.home() / ".local" / "share"


# The Exec key's reserved characters, verbatim from the Desktop Entry Spec:
# an argument containing any of them must be quoted. A home directory with a
# space in it is the everyday way to meet one.
_RESERVED = set(" \t\n\"'\\><~|&;$*?#()`")
# Inside the double quotes, this shorter set must additionally be
# backslash-escaped -- quoting alone does not neutralize them.
_ESCAPED = '`$"\\'


def _quote_exec(command: str) -> str:
    """A path as a desktop-entry Exec argument."""
    if not _RESERVED.intersection(command):
        return command
    escaped = "".join("\\" + c if c in _ESCAPED else c for c in command)
    return f'"{escaped}"'


def exec_command() -> str:
    """What the launcher should run.

    `build_deb.sh` can write a bare `Exec=collins` because the .deb puts the
    command in /usr/bin, which every session has on its PATH. A pip or pipx
    install puts it somewhere like ~/.local/bin or a pipx venv, and a desktop
    session is not guaranteed to have that on the PATH it launches apps with —
    so point the entry straight at the script we are running as, and fall back
    to the bare name only when there is nothing to point at (running as
    `python3 -m collins`, from a checkout).
    """
    launcher = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None
    if launcher is not None and launcher.name == "collins" and launcher.is_file():
        return _quote_exec(str(launcher))
    if found := shutil.which("collins"):
        return _quote_exec(str(Path(found).resolve()))
    return "collins"


def desktop_entry(template: str, command: str) -> str:
    """The template as a user-install entry: our command, no checkout path.

    Same two edits `build_deb.sh` and the AUR recipe make to it — the shipped
    template is written for a source tree, where Exec runs the module out of a
    hardcoded Path.
    """
    lines = []
    for line in template.splitlines():
        if line.startswith("Path="):
            continue
        lines.append(f"Exec={command}" if line.startswith("Exec=") else line)
    return "\n".join(lines) + "\n"


def _refresh(applications: Path, icons: Path) -> None:
    """Let the shell notice the new entry. Best effort, as in install.sh."""
    for argv in (
        ["update-desktop-database", str(applications)],
        ["gtk-update-icon-cache", "-t", str(icons)],
    ):
        if shutil.which(argv[0]) is None:
            continue
        subprocess.run(argv, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def install(root: Path | None = None) -> list[Path]:
    """Write launcher, icon and metainfo under `root`; return what was written.

    The action icons are deliberately not part of this: they ride inside the
    package now (app.py's `_ICON_ROOTS`), so there is nothing to copy out — and
    copying them into the shared user theme is exactly the mistake install.sh
    still cleans up after.
    """
    root = root or data_home()
    applications = root / "applications"
    icons = root / "icons" / "hicolor" / "scalable" / "apps"
    metainfo = root / "metainfo"

    template = _PACKAGE / f"{APP_ID}.desktop"
    icon = _PACKAGE / "icons" / f"{APP_ID}.svg"
    appdata = _PACKAGE / f"{APP_ID}.metainfo.xml"
    if missing := [p for p in (template, icon, appdata) if not p.is_file()]:
        raise FileNotFoundError(
            "these files are missing from the installed package: "
            + ", ".join(str(p) for p in missing)
        )

    for directory in (applications, icons, metainfo):
        directory.mkdir(parents=True, exist_ok=True)

    entry = applications / f"{APP_ID}.desktop"
    entry.write_text(desktop_entry(template.read_text(), exec_command()))
    shutil.copyfile(icon, icons / icon.name)
    shutil.copyfile(appdata, metainfo / appdata.name)

    _refresh(applications, root / "icons" / "hicolor")
    return [entry, icons / icon.name, metainfo / appdata.name]


def install_cli() -> int:
    """`collins --install-desktop`: install, and say what landed where."""
    try:
        written = install()
    except OSError as exc:
        print(f"collins: could not install the desktop entry: {exc}", file=sys.stderr)
        return 1
    for path in written:
        print(f"Installed: {path}")
    print("Collins should now appear in your app grid (a re-login may be needed).")
    return 0
