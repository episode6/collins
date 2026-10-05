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


def launcher_path() -> Path | None:
    """The installed `collins` command, if we can point at one.

    `build_deb.sh` can write a bare `Exec=collins` because the .deb puts the
    command in /usr/bin, which every session has on its PATH. A pip or pipx
    install puts it somewhere like ~/.local/bin or a pipx venv, and a desktop
    session is not guaranteed to have that on the PATH it launches apps with —
    so the entry points straight at the script. That is the one we are running
    as, or failing that whatever is on this shell's PATH; None when neither
    exists, i.e. `python3 -m collins` out of a checkout that was never
    installed.
    """
    launcher = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None
    if launcher is not None and launcher.name == "collins" and launcher.is_file():
        return launcher
    if found := shutil.which("collins"):
        return Path(found).resolve()
    return None


def exec_command() -> str:
    """The Exec= value: an absolute path when there is one to give."""
    if (launcher := launcher_path()) is not None:
        return _quote_exec(str(launcher))
    return "collins"


UNIT = "collins-service.service"


def service_launcher_path() -> Path | None:
    """The installed `collins-service` command: beside the `collins`
    launcher when there is one (a pip or pipx install puts both in the
    same bin directory), else whatever is on this shell's PATH."""
    launcher = launcher_path()
    if launcher is not None and (sibling := launcher.with_name("collins-service")).is_file():
        return sibling
    if found := shutil.which("collins-service"):
        return Path(found).resolve()
    return None


def systemd_user_dir(root: Path | None = None) -> Path:
    """Where a user's own units go: ``$XDG_DATA_HOME/systemd/user``."""
    return (root or data_home()) / "systemd" / "user"


def service_unit(template: str, command: str, app_id: str | None = None) -> str:
    """The shipped unit with ``ExecStart=`` resolved to *command* (the same
    resolution `exec_command` does for the launcher) and, for a
    non-default app id, ``Environment=COLLINS_APP_ID=`` appended to the
    service section (split-service spec §3.20)."""
    lines = []
    for line in template.splitlines():
        if line.startswith("ExecStart="):
            line = f"ExecStart={command}"
        lines.append(line)
        if line == "[Service]" and app_id and app_id != APP_ID:
            lines.append(f"Environment=COLLINS_APP_ID={app_id}")
    return "\n".join(lines) + "\n"


def service_exec_command() -> str:
    """The unit's ExecStart: an absolute path when there is one to give."""
    if (launcher := service_launcher_path()) is not None:
        return _quote_exec(str(launcher))
    return "collins-service"


def entry_locations() -> list[Path]:
    """Every applications directory a launcher for us could already sit in.

    XDG search order: the user's data home first, then the system dirs — which
    is where the .deb, the PPA and the AUR package put theirs, and
    data/install.sh puts one in the first.
    """
    system = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    roots = [data_home(), *(Path(p) for p in system.split(":") if p)]
    return [root / "applications" / f"{APP_ID}.desktop" for root in roots]


def is_installed() -> bool:
    """Whether anything has already put a launcher on this machine."""
    return any(path.is_file() for path in entry_locations())


def can_offer_install() -> bool:
    """Whether the app should offer to install a launcher for itself.

    Two ways to answer no: one already exists — a package installed it, or
    data/install.sh did — or we cannot name a command to put in Exec=, which
    is a checkout being run as `python3 -m collins` with nothing installed.
    data/install.sh is the tool for that case, and it knows the checkout path;
    offering a launcher we would have to guess the command for is worse than
    offering nothing.
    """
    return not is_installed() and launcher_path() is not None


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
    unit = _PACKAGE / UNIT
    if missing := [p for p in (template, icon, appdata, unit) if not p.is_file()]:
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
    # The service's user unit (§3.10): installed, never enabled; the app
    # starts it on demand and *Start at login* (a later PR) enables it.
    units = systemd_user_dir(root)
    units.mkdir(parents=True, exist_ok=True)
    unit_path = units / UNIT
    unit_path.write_text(service_unit(unit.read_text(), service_exec_command()))
    _refresh(applications, root / "icons" / "hicolor")
    _reload_units()
    return [entry, icons / icon.name, metainfo / appdata.name, unit_path]


def _reload_units() -> None:
    """Let the user manager see the unit. Best effort: no systemd, no
    session, or a failing daemon-reload leaves the files in place."""
    if shutil.which("systemctl") is None:
        return
    subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
    )


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
