"""Resolving the apps that can open a project directory: the desktop's file
manager and terminal emulator.

The user's own picks (the ``footer_apps`` setting) are desktop-file IDs the
app already knows how to resolve and launch — see footerapps.py. These two
are the ones nobody configures in Collins because the desktop already knows
them, so we ask the desktop instead of asking the user again.

GLib/Gio only — no GTK — so this stays importable (and testable) headless.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from gi.repository import Gio, GLib

from .footerapps import resolve_app

# Terminals we'd pick between when the desktop hasn't said which it wants
# (xdg-terminals.list, read below, is where a user who cares says so).
# Matched against both the desktop-file ID stem and the executable name;
# anything installed but unlisted sorts after these, A→Z.
_TERMINAL_PREFERENCE = (
    "ghostty",
    "ptyxis",
    "kgx",
    "gnome-terminal",
    "konsole",
    "alacritty",
    "kitty",
    "wezterm",
    "foot",
    "tilix",
    "xfce4-terminal",
    "terminator",
    "urxvt",
    "xterm",
)


def default_file_manager() -> Gio.AppInfo | None:
    """The desktop's handler for directories, or None if nothing claims them."""
    return Gio.AppInfo.get_default_for_type("inode/directory", False)


def default_terminal() -> Gio.AppInfo | None:
    """The terminal emulator to open a directory in, or None if we can't find
    one.

    In order: ``$TERMINAL``, the XDG terminal list, the best-known installed
    ``TerminalEmulator`` entry, then a bare executable off ``PATH``.
    """
    installed = _installed_terminals()

    env = (os.environ.get("TERMINAL") or "").strip()
    if env:
        info = _match_executable(installed, env) or _from_command(env)
        if info is not None:
            return info

    by_id = {info.get_id(): info for info in installed}
    for app_id in _configured_terminal_ids():
        info = by_id.get(app_id) or resolve_app(app_id)
        if info is not None:
            return info

    if installed:
        return min(installed, key=_terminal_rank)

    # xdg-terminal-exec first: it is the spec's own resolver, so it knows the
    # user's preference even when no .desktop entry advertises the category.
    for command in ("xdg-terminal-exec", *_TERMINAL_PREFERENCE):
        info = _from_command(command)
        if info is not None:
            return info
    return None


def _installed_terminals() -> list[Gio.AppInfo]:
    """Installed apps that call themselves terminal emulators."""
    terminals = []
    for info in Gio.AppInfo.get_all():
        categories = getattr(info, "get_categories", lambda: None)() or ""
        if "TerminalEmulator" in categories.split(";") and info.should_show():
            terminals.append(info)
    return terminals


def _terminal_keys(info: Gio.AppInfo) -> set[str]:
    """The names _TERMINAL_PREFERENCE might know an app by."""
    stem = (info.get_id() or "").removesuffix(".desktop").casefold()
    return {
        stem,
        stem.rsplit(".", 1)[-1],  # org.gnome.Ptyxis → ptyxis
        Path(info.get_executable() or "").name.casefold(),
    }


def _terminal_rank(info: Gio.AppInfo) -> tuple[int, str]:
    keys = _terminal_keys(info)
    for rank, name in enumerate(_TERMINAL_PREFERENCE):
        if name in keys:
            return (rank, "")
    return (len(_TERMINAL_PREFERENCE), (info.get_display_name() or "").casefold())


def _match_executable(infos: list[Gio.AppInfo], command: str) -> Gio.AppInfo | None:
    """The installed entry that runs ``command``, so a $TERMINAL we already
    have a .desktop file for keeps its name and icon."""
    wanted = Path(command).name.casefold()
    for info in infos:
        if Path(info.get_executable() or "").name.casefold() == wanted:
            return info
    return None


def _from_command(command: str) -> Gio.AppInfo | None:
    """A nameless AppInfo for an executable on PATH — the last resort, for
    terminals that ship no .desktop entry we can find."""
    path = shutil.which(command)
    if path is None:
        return None
    try:
        return Gio.AppInfo.create_from_commandline(
            path, Path(command).name, Gio.AppInfoCreateFlags.NONE
        )
    except GLib.Error:
        return None


def _configured_terminal_ids() -> list[str]:
    """Desktop-file IDs from ``xdg-terminals.list`` (the XDG terminal-execution
    spec), most specific config file first.

    Reading the list ourselves — rather than shelling out to
    xdg-terminal-exec — means the menu can show the terminal's real name and
    icon, not a generic launcher.
    """
    desktops = [d for d in (os.environ.get("XDG_CURRENT_DESKTOP") or "").split(":") if d]
    filenames = [f"{d.casefold()}-xdg-terminals.list" for d in desktops]
    filenames.append("xdg-terminals.list")

    app_ids: list[str] = []
    for directory in [GLib.get_user_config_dir(), *GLib.get_system_config_dirs()]:
        for filename in filenames:
            try:
                text = (Path(directory) / filename).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for line in text.splitlines():
                app_id = line.strip()
                if app_id and not app_id.startswith("#") and app_id not in app_ids:
                    app_ids.append(app_id)
    return app_ids
