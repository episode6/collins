# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-26. Full change history: git log for this file.
"""Quick switcher: a type-ahead dialog to jump to any session."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from .models import SessionItem
from .store import SessionStore

_MAX_RESULTS = 50
_ELLIPSIZE_END = 3  # Pango.EllipsizeMode.END


class QuickSwitcher(Adw.Dialog):
    def __init__(self, store: SessionStore, on_choose: Callable[[SessionItem], None]) -> None:
        super().__init__(title="Switch session")
        self._store = store
        self._on_choose = on_choose
        self.set_content_width(560)
        self.set_content_height(460)
        self.set_follows_content_size(False)

        self._entry = Gtk.SearchEntry(placeholder_text="Jump to a session…")
        self._entry.set_margin_top(10)
        self._entry.set_margin_start(10)
        self._entry.set_margin_end(10)
        self._entry.connect("search-changed", lambda *_: self._refilter())
        self._entry.connect("activate", lambda *_: self._activate_selected())

        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key)
        self._entry.add_controller(key)

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list.add_css_class("navigation-sidebar")
        self._list.connect("row-activated", lambda _l, row: self._choose(row))

        scrolled = Gtk.ScrolledWindow(child=self._list, vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(self._entry)
        box.append(scrolled)
        self.set_child(box)

        self.connect("map", lambda *_: self._entry.grab_focus())
        self._refilter()

    # -- building / filtering ------------------------------------------------

    def _refilter(self) -> None:
        self._list.remove_all()
        query = self._entry.get_text().strip().lower()
        model = self._store.model
        shown = 0
        for i in range(model.get_n_items()):
            item = model.get_item(i)
            if query and query not in item.search_text:
                continue
            self._list.append(self._make_row(item))
            shown += 1
            if shown >= _MAX_RESULTS:
                break
        first = self._list.get_row_at_index(0)
        if first is not None:
            self._list.select_row(first)

    def _make_row(self, item: SessionItem) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.item = item

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(7)
        box.set_margin_bottom(7)
        box.set_margin_start(12)
        box.set_margin_end(12)

        name = Gtk.Label(label=item.display_name, xalign=0.0)
        name.add_css_class("heading")
        name.set_ellipsize(_ELLIPSIZE_END)
        box.append(name)

        subtitle = item.session.project_name
        if item.session.preview:
            subtitle += f" · {item.session.preview}"
        sub = Gtk.Label(label=subtitle, xalign=0.0)
        sub.add_css_class("dim-label")
        sub.add_css_class("caption")
        sub.set_ellipsize(_ELLIPSIZE_END)
        box.append(sub)

        row.set_child(box)
        return row

    # -- navigation ----------------------------------------------------------

    def _on_key(self, _ctrl, keyval: int, _keycode: int, _state: Gdk.ModifierType) -> bool:
        if keyval == Gdk.KEY_Down:
            self._move(1)
            return True
        if keyval == Gdk.KEY_Up:
            self._move(-1)
            return True
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def _move(self, delta: int) -> None:
        selected = self._list.get_selected_row()
        index = selected.get_index() if selected is not None else -1
        target = self._list.get_row_at_index(index + delta)
        if target is not None:
            self._list.select_row(target)
            target.grab_focus()  # scrolls it into view
            self._entry.grab_focus()  # keep typing in the entry

    # -- choosing ------------------------------------------------------------

    def _activate_selected(self) -> None:
        self._choose(self._list.get_selected_row())

    def _choose(self, row: Gtk.ListBoxRow | None) -> None:
        if row is not None and getattr(row, "item", None) is not None:
            self._on_choose(row.item)
            self.close()
