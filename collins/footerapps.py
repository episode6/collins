"""Footer apps: user-configured applications launchable from a tab's footer.

Only desktop-file IDs are persisted (the ``footer_apps`` setting); display
names and icons are resolved live from the installed .desktop entries, so
they track app updates and icon-theme changes for free.

GLib/Gio only — no GTK — so the logic here stays importable (and testable)
headless; the widgets live in apppicker.py.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from gi.repository import Gio, GioUnix, GLib

# Exec-line field codes (Desktop Entry spec). %f/%u/%F/%U carry the files
# argument; the rest expand to metadata we don't supply.
_FIELD_CODES = {"%f", "%F", "%u", "%U", "%i", "%c", "%k", "%d", "%D", "%n", "%N", "%v", "%m"}


def resolve_app(app_id: str) -> GioUnix.DesktopAppInfo | None:
    """The installed app for a desktop-file ID, or None if it's gone."""
    # GLib 2.80 moved the Unix-only Gio API into the GioUnix typelib;
    # Gio.DesktopAppInfo is a deprecated alias that returns this same type.
    try:
        return GioUnix.DesktopAppInfo.new(app_id)
    except (TypeError, GLib.Error):
        return None


def resolve_apps(app_ids: list[str]) -> list[tuple[str, GioUnix.DesktopAppInfo]]:
    """Resolve IDs in order, silently dropping uninstalled ones."""
    resolved = []
    for app_id in app_ids:
        info = resolve_app(app_id)
        if info is not None:
            resolved.append((app_id, info))
    return resolved


def installed_apps() -> list[Gio.AppInfo]:
    """Launchable apps the desktop would show in its own menus, A→Z."""
    apps = [info for info in Gio.AppInfo.get_all() if info.should_show()]
    apps.sort(key=lambda info: (info.get_display_name() or "").casefold())
    return apps


def strip_field_codes(argv: list[str]) -> list[str]:
    return [arg for arg in argv if arg not in _FIELD_CODES]


def _launch_context() -> Gio.AppLaunchContext | None:
    """The display's launch context (for startup notification), when a GUI
    is running; None headless."""
    try:
        from gi.repository import Gdk
    except ImportError:
        return None
    display = Gdk.Display.get_default()
    return display.get_app_launch_context() if display is not None else None


def launch_app(app_info: Gio.AppInfo, cwd: str | None, *, pass_directory: bool = True) -> None:
    """Open ``cwd`` with the app; failures are logged, never raised.

    Apps whose Exec line takes a file/URI argument get the directory passed
    to them; for the rest GLib would silently drop it, so they are instead
    spawned *in* the directory (the useful semantic for terminals and IDEs
    with placeholder-less .desktop files).

    ``pass_directory=False`` forces the spawn-in-place path: a terminal that
    does advertise %u would read the directory as a command to run, not as
    the place to start in.
    """
    if not cwd or not Path(cwd).is_dir():
        cwd = str(Path.home())
    try:
        if pass_directory and (app_info.supports_files() or app_info.supports_uris()):
            app_info.launch([Gio.File.new_for_path(cwd)], _launch_context())
            return
        commandline = app_info.get_commandline()
        if not commandline:
            return
        ok, argv = GLib.shell_parse_argv(commandline)
        if not ok:
            return
        argv = strip_field_codes(argv)
        if argv:
            subprocess.Popen(argv, cwd=cwd, start_new_session=True)
    except (GLib.Error, OSError) as exc:
        print(f"footer app launch failed ({app_info.get_id()}): {exc}", file=sys.stderr)
