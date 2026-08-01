# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The editor panel's project file tree.

A `Gtk.ListView` over a `Gtk.TreeListModel`, lazily populated: a directory's
children are only listed (via `editorfiles.list_dir`) the moment it is first
expanded. Nothing else in the sidebar looks like this — its own two-level
grouping is hand-rolled flat rows — so this is a new pattern, not a reuse.
"""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import editorfiles

# How long after the last change in an expanded directory its row list is
# rebuilt — long enough that an agent mid-rewrite (many quick saves) coalesces
# into one refresh, short enough that "the tree matches disk" still feels
# immediate. Mirrors store.py's own filesystem-change debounce.
_REFRESH_DEBOUNCE_MS = 500


class _Node(GObject.Object):
    """One row: a file or directory, never re-created for the same path
    while its parent directory is listed (see `_refresh_dir`'s splice)."""

    def __init__(self, name: str, path: Path, is_dir: bool) -> None:
        super().__init__()
        self.name = name
        self.path = path
        self.is_dir = is_dir


class FileTree(Gtk.Box):
    """Rooted at a project directory. `open-file(str)` fires when a file row
    is activated (double-click / Enter) — directory rows toggle expansion
    instead."""

    __gsignals__ = {
        "open-file": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, root: str | Path, show_hidden: bool = False) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True, hexpand=True)
        self._root = Path(root)
        self._show_hidden = show_hidden
        # path -> monitor, for every directory this tree has ever expanded.
        # Not torn down on collapse (a modest, tab-lifetime cost) — see
        # module docstring; only ever grows across directories actually
        # opened, never the whole project up front.
        self._monitors: dict[str, Gio.FileMonitor] = {}

        self._root_store = Gio.ListStore(item_type=_Node)
        self._fill_store(self._root_store, self._root)
        self._tree_model = Gtk.TreeListModel.new(
            self._root_store, False, False, self._create_children
        )
        self._selection = Gtk.SingleSelection(model=self._tree_model)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup)
        factory.connect("bind", self._on_bind)

        self._list_view = Gtk.ListView(model=self._selection, factory=factory)
        self._list_view.add_css_class("navigation-sidebar")
        self._list_view.connect("activate", self._on_activate)

        scrolled = Gtk.ScrolledWindow(child=self._list_view, vexpand=True, hexpand=True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.append(scrolled)

    # -- population ----------------------------------------------------------

    def _fill_store(self, store: Gio.ListStore, directory: Path) -> None:
        nodes = [
            _Node(name, directory / name, is_dir)
            for name, is_dir in editorfiles.list_dir(directory, self._show_hidden, root=self._root)
        ]
        store.splice(0, store.get_n_items(), nodes)

    def _create_children(self, item: _Node) -> Gio.ListModel | None:
        """Called by `Gtk.TreeListModel` the moment a row is first expanded —
        this is the "lazily populated" part. A symlinked directory is left
        with no expander at all: per the standing untrusted-repo-content
        rule, the tree never follows one out of the project."""
        if not item.is_dir or item.path.is_symlink():
            return None
        if not editorfiles.is_inside(self._root, item.path):
            return None
        store = Gio.ListStore(item_type=_Node)
        self._fill_store(store, item.path)
        self._watch(item.path, store)
        return store

    # -- live refresh ----------------------------------------------------------

    def _watch(self, path: Path, store: Gio.ListStore) -> None:
        key = str(path)
        if key in self._monitors:
            return
        try:
            monitor = Gio.File.new_for_path(key).monitor_directory(Gio.FileMonitorFlags.NONE, None)
        except GLib.Error:
            return
        pending = {"queued": False}

        def on_changed(_monitor, _file, _other, _event) -> None:
            if pending["queued"]:
                return
            pending["queued"] = True
            GLib.timeout_add(_REFRESH_DEBOUNCE_MS, self._debounced_refresh, path, store, pending)

        monitor.connect("changed", on_changed)
        self._monitors[key] = monitor

    def _debounced_refresh(self, path: Path, store: Gio.ListStore, pending: dict) -> bool:
        pending["queued"] = False
        self._fill_store(store, path)
        return GLib.SOURCE_REMOVE

    def set_show_hidden(self, show_hidden: bool) -> None:
        """Applies immediately to the root and to any directory refreshed
        from here on; a directory already expanded keeps its current rows
        until it next changes on disk or is collapsed and re-expanded."""
        if show_hidden == self._show_hidden:
            return
        self._show_hidden = show_hidden
        self._fill_store(self._root_store, self._root)

    # -- row widgets -------------------------------------------------------

    def _on_setup(self, _factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
        expander = Gtk.TreeExpander()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        icon = Gtk.Image()
        label = Gtk.Label(xalign=0.0, ellipsize=Pango.EllipsizeMode.END)
        box.append(icon)
        box.append(label)
        expander.set_child(box)
        list_item.set_child(expander)

    def _on_bind(self, _factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
        row: Gtk.TreeListRow = list_item.get_item()
        node: _Node = row.get_item()
        expander: Gtk.TreeExpander = list_item.get_child()
        expander.set_list_row(row)
        box = expander.get_child()
        icon: Gtk.Image = box.get_first_child()
        label: Gtk.Label = icon.get_next_sibling()
        icon.set_from_icon_name("folder-symbolic" if node.is_dir else "text-x-generic-symbolic")
        label.set_label(node.name)

    def _on_activate(self, _list_view: Gtk.ListView, position: int) -> None:
        row: Gtk.TreeListRow = self._selection.get_item(position)
        node: _Node = row.get_item()
        if node.is_dir:
            row.set_expanded(not row.get_expanded())
        else:
            self.emit("open-file", str(node.path))
