"""Widgets for footer apps: the app icon helper and the large-icon picker.

Split from footerapps.py so the launch/resolve logic there stays importable
without GTK (the headless test environment has GLib/Gio only).
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk, Pango  # noqa: E402

from .footerapps import installed_apps  # noqa: E402
from .i18n import _  # noqa: E402


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
