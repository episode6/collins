"""Footer apps: user-configured applications launchable from a tab's footer.

Only desktop-file IDs are persisted (the ``footer_apps`` setting); display
names and icons are resolved live from the installed .desktop entries, so
they track app updates and icon-theme changes for free.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .i18n import _  # noqa: E402

# Exec-line field codes (Desktop Entry spec). %f/%u/%F/%U carry the files
# argument; the rest expand to metadata we don't supply.
_FIELD_CODES = {"%f", "%F", "%u", "%U", "%i", "%c", "%k", "%d", "%D", "%n", "%N", "%v", "%m"}


def resolve_app(app_id: str) -> Gio.DesktopAppInfo | None:
    """The installed app for a desktop-file ID, or None if it's gone."""
    try:
        return Gio.DesktopAppInfo.new(app_id)
    except (TypeError, GLib.Error):
        return None


def resolve_apps(app_ids: list[str]) -> list[tuple[str, Gio.DesktopAppInfo]]:
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


def launch_app(app_info: Gio.AppInfo, cwd: str | None) -> None:
    """Open ``cwd`` with the app; failures are logged, never raised.

    Apps whose Exec line takes a file/URI argument get the directory passed
    to them; for the rest GLib would silently drop it, so they are instead
    spawned *in* the directory (the useful semantic for terminals and IDEs
    with placeholder-less .desktop files).
    """
    if not cwd or not Path(cwd).is_dir():
        cwd = str(Path.home())
    try:
        if app_info.supports_files() or app_info.supports_uris():
            display = Gdk.Display.get_default()
            context = display.get_app_launch_context() if display is not None else None
            app_info.launch([Gio.File.new_for_path(cwd)], context)
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


def app_icon_image(app_info: Gio.AppInfo, pixel_size: int) -> Gtk.Image:
    """The app's real icon at the given size, with a generic fallback."""
    icon = app_info.get_icon() or Gio.ThemedIcon.new("application-x-executable")
    image = Gtk.Image.new_from_gicon(icon)
    image.set_pixel_size(pixel_size)
    return image


class AppPickerDialog(Adw.Dialog):
    """A searchable grid of installed applications, shown as large icons."""

    def __init__(self, exclude_ids: set[str], on_select: Callable[[str], None]) -> None:
        super().__init__(title=_("Add application"))
        self._on_select = on_select
        self.set_content_width(640)
        self.set_content_height(520)
        self.set_follows_content_size(False)

        self._entry = Gtk.SearchEntry(placeholder_text=_("Search applications…"))
        self._entry.set_margin_top(10)
        self._entry.set_margin_start(10)
        self._entry.set_margin_end(10)
        self._entry.connect("search-changed", lambda *_: self._flowbox.invalidate_filter())

        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key)
        self._entry.add_controller(key)

        self._flowbox = Gtk.FlowBox()
        self._flowbox.set_valign(Gtk.Align.START)
        self._flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._flowbox.set_activate_on_single_click(True)
        self._flowbox.set_homogeneous(True)
        self._flowbox.set_min_children_per_line(4)
        self._flowbox.set_max_children_per_line(5)
        self._flowbox.set_row_spacing(6)
        self._flowbox.set_column_spacing(6)
        for edge in ("top", "bottom", "start", "end"):
            getattr(self._flowbox, f"set_margin_{edge}")(10)
        self._flowbox.set_filter_func(self._filter)
        self._flowbox.connect("child-activated", self._on_activated)

        for info in installed_apps():
            app_id = info.get_id()
            if not app_id or app_id in exclude_ids:
                continue
            self._flowbox.append(self._make_cell(app_id, info))

        scrolled = Gtk.ScrolledWindow(child=self._flowbox, vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(self._entry)
        box.append(scrolled)
        self.set_child(box)

        self.connect("map", lambda *_: self._entry.grab_focus())

    def _make_cell(self, app_id: str, info: Gio.AppInfo) -> Gtk.FlowBoxChild:
        name = info.get_display_name() or app_id
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for edge in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{edge}")(8)
        box.append(app_icon_image(info, 48))

        label = Gtk.Label(label=name, justify=Gtk.Justification.CENTER)
        label.set_wrap(True)
        label.set_lines(2)
        label.set_max_width_chars(16)  # keeps homogeneous cells from stretching the grid
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.add_css_class("caption")
        box.append(label)

        child = Gtk.FlowBoxChild(child=box)
        child.app_id = app_id
        generic = getattr(info, "get_generic_name", lambda: None)() or ""
        child.search_text = f"{name} {generic} {app_id}".casefold()
        return child

    def _filter(self, child: Gtk.FlowBoxChild) -> bool:
        query = self._entry.get_text().strip().casefold()
        return not query or query in child.search_text

    def _on_activated(self, _flowbox, child: Gtk.FlowBoxChild) -> None:
        self._on_select(child.app_id)
        self.close()

    def _on_key(self, _ctrl, keyval: int, _keycode: int, _state: Gdk.ModifierType) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False
