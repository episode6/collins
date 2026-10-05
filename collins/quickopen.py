# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Quick open: a type-ahead dialog to jump to any file in the project.

Modeled on switcher.py's QuickSwitcher (search entry + list + arrow keys),
backed by the service's `fs.walk` (split-service spec §3.23, PR-2.4: the
walk runs on the service's machine, asked from a background thread and
landed at `GLib.PRIORITY_DEFAULT`). Scoring lives in the GTK-free `fuzzy`
module.

The walked file list is cached per project root so reopening the dialog is
instant; a watch on the root (`fs.watch kind: dir`, the service's
`dir-changed`) drops the cache the moment the top level changes, and every
open re-walks in the background anyway — the agent is creating files all
the time, and a fresh walk quietly replacing a cached list is cheaper than
ever showing a stale one for long.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from . import fuzzy, remotefiles  # noqa: E402
from .api import protocol  # noqa: E402
from .i18n import _  # noqa: E402

log = logging.getLogger(__name__)

_MAX_RESULTS = 50
_ELLIPSIZE_END = 3  # Pango.EllipsizeMode.END

# (root, show_hidden) -> (paths, truncated). Shared across dialogs so a
# reopened quick-open shows something instantly; see _invalidate.
_index_cache: dict[tuple[str, bool], tuple[list[str], bool]] = {}
# root -> the handle of its watch on the service, alive for the app's
# lifetime once a root has been quick-opened at all (one watch per project —
# a modest, bounded cost; a reconnect sends it again, `remotefiles.reset`).
_root_watches: dict[str, str] = {}


def _on_root_changed(event: dict) -> None:
    """The service's `dir-changed` for a watched root: its cached walk is
    stale."""
    root = str(event.get("path"))
    for key in [k for k in _index_cache if k[0] == root]:
        del _index_cache[key]


def _watch_root(root: str) -> None:
    if root in _root_watches:
        return
    _root_watches[root] = remotefiles.watcher().watch(root, _on_root_changed, kind=protocol.WATCH_DIR)


class QuickOpenDialog(Adw.Dialog):
    """Fuzzy-find a file under *root*; `on_choose` gets the absolute path."""

    def __init__(
        self, root: str | Path, on_choose: Callable[[str], None], show_hidden: bool = False
    ) -> None:
        super().__init__(title=_("Open File"))
        self._root = Path(root)
        self._on_choose = on_choose
        self._show_hidden = show_hidden
        self._paths: list[str] = []
        self._truncated = False
        self._indexing = True
        self.set_content_width(560)
        self.set_content_height(460)
        self.set_follows_content_size(False)

        self._entry = Gtk.SearchEntry(placeholder_text=_("Open a file…"))
        self._entry.set_margin_top(10)
        self._entry.set_margin_start(10)
        self._entry.set_margin_end(10)
        self._entry.connect("search-changed", lambda *_a: self._refilter())
        self._entry.connect("activate", lambda *_a: self._activate_selected())

        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key)
        self._entry.add_controller(key)

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list.add_css_class("navigation-sidebar")
        self._list.connect("row-activated", lambda _l, row: self._choose(row))

        scrolled = Gtk.ScrolledWindow(child=self._list, vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._status = Gtk.Label(xalign=0.0)
        self._status.add_css_class("dim-label")
        self._status.add_css_class("caption")
        self._status.set_margin_start(12)
        self._status.set_margin_end(12)
        self._status.set_margin_bottom(8)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(self._entry)
        box.append(scrolled)
        box.append(self._status)
        self.set_child(box)

        self.connect("map", lambda *_a: self._entry.grab_focus())

        cached = _index_cache.get(self._cache_key)
        if cached is not None:
            self._paths, self._truncated = cached
            self._indexing = False
        self._refilter()
        _watch_root(str(self._root))
        self._start_walk()

    @property
    def _cache_key(self) -> tuple[str, bool]:
        return (str(self._root), self._show_hidden)

    # -- indexing ------------------------------------------------------------

    def _start_walk(self) -> None:
        """Re-walk the project (`fs.walk`, on the service) off the main
        loop; a cached list (if any) keeps the dialog usable meanwhile and
        is quietly replaced when this lands."""
        root, hidden = str(self._root), self._show_hidden
        remotefiles.off_main(lambda: remotefiles.walk_root(root, hidden), self._walked, name="quickopen-walk")

    def _walked(self, kind: str, value) -> None:
        if kind == "ok":
            self._apply_walk(*value)
            return
        # Refused (a root the service doesn't know, no `files` capability,
        # gone): whatever was cached stays searchable, and the status says
        # what there is.
        log.info("quickopen: the walk of %s was refused: %s", self._root, remotefiles.refusal_words(value))
        self._indexing = False
        self._sync_status()

    def _apply_walk(self, paths: list[str], truncated: bool) -> bool:
        _index_cache[self._cache_key] = (paths, truncated)
        changed = paths != self._paths or truncated != self._truncated
        self._paths, self._truncated = paths, truncated
        self._indexing = False
        if changed or not self._list.get_row_at_index(0):
            self._refilter()
        else:
            self._sync_status()
        return GLib.SOURCE_REMOVE

    # -- building / filtering ------------------------------------------------

    def _refilter(self) -> None:
        self._list.remove_all()
        query = self._entry.get_text().strip()
        for rel in fuzzy.rank(query, self._paths, _MAX_RESULTS):
            self._list.append(self._make_row(rel))
        first = self._list.get_row_at_index(0)
        if first is not None:
            self._list.select_row(first)
        self._sync_status()

    def _sync_status(self) -> None:
        if self._indexing:
            self._status.set_text(_("Indexing project files…"))
        elif not self._paths:
            self._status.set_text(_("No files found in this project."))
        elif self._truncated:
            self._status.set_text(
                _("Project is large — only the first {count} files are searchable.").format(
                    count=len(self._paths)
                )
            )
        else:
            self._status.set_text("")
        self._status.set_visible(bool(self._status.get_text()))

    def _make_row(self, rel: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.rel_path = rel

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(7)
        box.set_margin_bottom(7)
        box.set_margin_start(12)
        box.set_margin_end(12)

        name = Gtk.Label(label=Path(rel).name, xalign=0.0)
        name.add_css_class("heading")
        name.set_ellipsize(_ELLIPSIZE_END)
        box.append(name)

        parent = str(Path(rel).parent)
        if parent != ".":
            sub = Gtk.Label(label=parent, xalign=0.0)
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
        if row is not None and getattr(row, "rel_path", None) is not None:
            self._on_choose(str(self._root / row.rel_path))
            self.close()
