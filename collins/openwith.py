"""Resolving — and opening — the apps that can open a project directory: the
desktop's file manager and terminal emulator.

The user's own picks (the ``footer_apps`` setting) are desktop-file IDs the
app already knows how to resolve and launch — see footerapps.py. These two
are the ones nobody configures in Collins because the desktop already knows
them, so we ask the desktop instead of asking the user again.

Terminals are also *launched* from here (launch_terminal), because telling one
where to start takes knowing which terminal it is — see _TERMINAL_DIR_FLAGS.

GLib/Gio only — no GTK — so this stays importable (and testable) headless.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from gi.repository import Gio, GLib

from .footerapps import launch_app, resolve_app, strip_field_codes

# Terminals we'd pick between when the desktop hasn't said which it wants
# (xdg-terminals.list, read below, is where a user who cares says so).
# Matched against both the desktop-file ID stem and the executable name;
# anything installed but unlisted sorts after these, A→Z.
_TERMINAL_PREFERENCE = (
    "ghostty",
    "ptyxis",
    "kgx",
    "gnome-terminal",
    "blackbox",
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

# The flags that tell each terminal where to start; the directory follows as
# its own argument.
#
# Spawning the terminal *in* the directory is not enough, and fails silently:
# a terminal that is already running — anything single-instance, D-Bus- or
# Flatpak-activated, which is most of the modern ones — hands the request to
# the instance that is already up and opens wherever *that* process thinks it
# is, ignoring the cwd we spawned with. Asking on the command line is the only
# way to be heard.
#
# Ptyxis carries the extra --new-window because it reads --working-directory
# only alongside --new-window, --tab or -x; bare, it opens no window at all.
# (xdg-terminal-exec builds it the same way, from the entry's own
# X-TerminalArgDir key and its new-window action.)
_TERMINAL_DIR_FLAGS: dict[str, tuple[str, ...]] = {
    "ptyxis": ("--new-window", "--working-directory"),
    "blackbox": ("--working-directory",),
    "ghostty": ("--working-directory",),
    "kgx": ("--working-directory",),
    "gnome-terminal": ("--working-directory",),
    "tilix": ("--working-directory",),
    "xfce4-terminal": ("--working-directory",),
    "terminator": ("--working-directory",),
    "alacritty": ("--working-directory",),
    "foot": ("--working-directory",),
    "konsole": ("--workdir",),
    "kitty": ("--directory",),
    "wezterm": ("start", "--cwd"),
    "urxvt": ("-cd",),
    # xterm and anything else we don't know: no flag, so they are spawned in
    # the directory instead — which works, for a terminal that starts a
    # process of its own every time.
}


def default_file_manager() -> Gio.AppInfo | None:
    """The desktop's handler for directories, or None if nothing claims them."""
    return Gio.AppInfo.get_default_for_type("inode/directory", False)


def default_terminal() -> Gio.AppInfo | None:
    """The terminal emulator to open a directory in, or None if we can't find
    one.

    In order: ``$TERMINAL``, the XDG terminal list, the terminal the system's
    alternatives point ``x-terminal-emulator`` at, the best-known installed
    ``TerminalEmulator`` entry, then a bare executable off ``PATH``.

    Every step before the fourth is somewhere a *user* has named their
    terminal; the fourth is us guessing between whatever is installed, and a
    guess must never overrule an answer.
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

    alternative = _alternatives_terminal()
    if alternative:
        info = _match_executable(installed, alternative) or _from_command(alternative)
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


def launch_terminal(info: Gio.AppInfo, folder: str | None) -> None:
    """Open ``folder`` in the terminal ``info`` describes.

    The directory goes on the command line whenever we know the flag for it,
    and is the spawn cwd either way: the only thing we can do for a terminal
    we don't know, and harmless for the ones we do.
    """
    if not folder or not Path(folder).is_dir():
        folder = str(Path.home())
    argv = terminal_argv(info, folder)
    if argv is None:  # no flag we know of — spawning it in place is the best left
        launch_app(info, folder, pass_directory=False)
        return
    try:
        subprocess.Popen(argv, cwd=folder, start_new_session=True)
    except OSError as exc:
        print(f"terminal launch failed ({info.get_id()}): {exc}", file=sys.stderr)


def terminal_argv(info: Gio.AppInfo, folder: str) -> list[str] | None:
    """The command line that opens ``folder`` in ``info``, or None when we
    have no way to say where it should start."""
    flags = _terminal_dir_flags(info)
    if flags is None:
        return None
    commandline = info.get_commandline()
    if not commandline:
        return None
    ok, argv = GLib.shell_parse_argv(commandline)
    if not ok or not argv:
        return None
    argv = strip_field_codes(argv)
    if any(flag in argv for flag in flags):
        # Its own Exec line already says where to start (WezTerm's ships
        # "start --cwd ."); a second answer would only contradict the first,
        # and a relative one resolves against the cwd we spawn in anyway.
        return None
    return [*argv, *flags, folder]


def _terminal_dir_flags(info: Gio.AppInfo) -> tuple[str, ...] | None:
    """How to tell this terminal where to start — what we know about it, else
    what its own entry advertises."""
    for key in sorted(_terminal_keys(info)):
        if key in _TERMINAL_DIR_FLAGS:
            return _TERMINAL_DIR_FLAGS[key]
    # The XDG terminal-execution spec's key, for the terminals we don't know:
    # the entry names its own flag. Only .desktop-backed apps can answer.
    getter = getattr(info, "get_string", None)
    if getter is None:
        return None
    try:
        advertised = getter("X-TerminalArgDir")
    except (GLib.Error, TypeError):
        return None
    return (advertised,) if advertised else None


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
    """The installed entry that runs ``command``, so a terminal we already
    have a .desktop file for keeps its name, its icon, and the flag that tells
    it where to start.

    Matched against every name the entry goes by, not just its Exec: a
    Flatpak's executable is ``flatpak`` itself, and the wrapper an alternative
    points at (``/usr/local/bin/blackbox``) is named after the app rather than
    after the launcher that ends up running it.
    """
    wanted = Path(command).name.casefold()
    for info in infos:
        if wanted in _terminal_keys(info):
            return info
    return None


def _alternatives_terminal() -> str | None:
    """The executable ``x-terminal-emulator`` resolves to, or None.

    Debian and its derivatives keep the system's terminal in the alternatives
    system, where ``update-alternatives --config x-terminal-emulator`` is how
    you say which one you want — including for a terminal that ships no
    ``TerminalEmulator`` entry of its own, or a Flatpak behind a wrapper
    script. No XDG spec reads it, so without this the user who has said so
    there is still handed whichever installed entry we happen to rank highest.
    """
    path = shutil.which("x-terminal-emulator")
    if path is None:
        return None
    target = os.path.realpath(path)  # /etc/alternatives/… → the terminal itself
    return target if os.access(target, os.X_OK) else None


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
