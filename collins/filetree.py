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
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import editorfiles, fileclipboard, filetypes, gitinfo
from .i18n import _

# How long after the last change in an expanded directory its row list is
# rebuilt — long enough that an agent mid-rewrite (many quick saves) coalesces
# into one refresh, short enough that "the tree matches disk" still feels
# immediate. Mirrors store.py's own filesystem-change debounce.
_REFRESH_DEBOUNCE_MS = 500


def _menu(*items: tuple[str, str]) -> Gio.Menu:
    """A flat menu of `(label, action)` pairs — flat like every other context
    menu in the app (the terminal's, the tabs'), and short enough that it
    never has to scroll."""
    menu = Gio.Menu()
    for label, action in items:
        menu.append(label, action)
    return menu


def _expander_in(widget: Gtk.Widget) -> Gtk.TreeExpander | None:
    """The `Gtk.TreeExpander` inside *widget* — the row's, which is what
    carries the `Gtk.TreeListRow` a node hangs off (see `_on_setup`)."""
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.TreeExpander):
            return child
        found = _expander_in(child)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


class _Node(GObject.Object):
    """One row: a file or directory, never re-created for the same path
    while its parent directory is listed (see `_refresh_dir`'s splice)."""

    def __init__(self, name: str, path: Path, is_dir: bool, dim: bool = False) -> None:
        super().__init__()
        self.name = name
        self.path = path
        self.is_dir = is_dir
        # Drawn at reduced opacity: a dotfile, or something git ignores —
        # still openable, but visibly not part of the project's real content.
        self.dim = dim


class FileTree(Gtk.Box):
    """Rooted at a project directory. `open-file(str)` fires when a file row
    is activated (double-click / Enter) — directory rows toggle expansion
    instead."""

    __gsignals__ = {
        "open-file": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # A file row's context menu asked for the file to be referenced in
        # the tab's agent chat. Payload is the absolute path.
        "add-to-chat": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # A row's context menu asked to rename it. Payload is (absolute path,
        # is a directory). The tree only asks: the rename itself belongs to
        # the pane, which is what knows whether the thing being renamed is
        # open in a tab (see EditorPane._prompt_rename).
        "rename-request": (GObject.SignalFlags.RUN_FIRST, None, (str, bool)),
        # A folder's (or the empty space's) context menu asked to paste the
        # clipboard into it. Payload is the destination directory. Copying is
        # done here — it is only a clipboard write — but a paste can *move*
        # files, and a moved file that is open has to keep its tab, which
        # again only the pane knows about (see EditorPane._paste_into).
        "paste-request": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
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
        # path -> the store holding that directory's rows, for every listed
        # directory (the root included, which no monitor covers). Lets a
        # change this tree made itself show up at once instead of waiting out
        # the monitor's debounce — see `refresh_dir`.
        self._stores: dict[str, Gio.ListStore] = {}

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
        # One gesture for the whole tree — the rows and the empty space below
        # them — rather than one per row widget: which row a press landed on
        # (if any) is worked out from the coordinates in `_row_at`.
        right_click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        right_click.connect("pressed", self._on_right_click)
        self._list_view.add_controller(right_click)

        # The context menu's actions; what was clicked is stashed here by the
        # right-click handlers just before the popover opens — the path and
        # kind of the row, and the directory a paste would land in (the
        # clicked folder, or the root for a click on the empty space below
        # the rows).
        self._menu_path = ""
        self._menu_is_dir = False
        self._menu_dir = self._root
        actions = Gio.SimpleActionGroup()
        for name, handler in (
            ("add-to-chat", self._on_add_to_chat),
            ("rename", self._on_rename),
            ("copy", self._on_copy),
            ("cut", self._on_cut),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            actions.add_action(action)
        # Kept to hand: Paste is greyed out, not dropped, when the clipboard
        # holds nothing to paste — an item that vanishes reads as a bug, and
        # an empty-space menu would otherwise have no items at all.
        self._paste_action = Gio.SimpleAction.new("paste", None)
        self._paste_action.connect("activate", self._on_paste)
        actions.add_action(self._paste_action)
        self.insert_action_group("tree", actions)

        scrolled = Gtk.ScrolledWindow(child=self._list_view, vexpand=True, hexpand=True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.append(scrolled)

    def do_grab_focus(self) -> bool:
        """Focus means the list. A plain Gtk.Box hands grab_focus to its
        children in turn, and the ScrolledWindow in between isn't focusable
        and doesn't pass it on — so without this, focusing the tree quietly
        did nothing (the editor's focus_default and the narrow pane's back
        button both rely on it)."""
        return self._list_view.grab_focus()

    # -- population ----------------------------------------------------------

    def _fill_store(self, store: Gio.ListStore, directory: Path) -> None:
        self._stores[str(directory)] = store
        entries = editorfiles.list_dir(directory, self._show_hidden, root=self._root)
        ignored = gitinfo.ignored_names(directory, [name for name, _ in entries])
        nodes = [
            _Node(name, directory / name, is_dir, dim=name.startswith(".") or name in ignored)
            for name, is_dir in entries
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
        # A store the tree has since dropped — the directory was renamed away,
        # or the whole tree was re-rooted (`set_root`) — is no longer in the
        # model, and re-listing it would only put its key back in `_stores`.
        if self._stores.get(str(path)) is not store:
            return GLib.SOURCE_REMOVE
        self._fill_store(store, path)
        return GLib.SOURCE_REMOVE

    def forget_dir(self, path: str | Path) -> None:
        """Drop what this tree remembers about *path* and anything under it:
        the row stores and the file monitors watching them. For a directory
        that has just been renamed or removed — its monitor now watches a path
        nothing will ever change again, and expanding the new name builds its
        store fresh. Without this the entries would sit there for the tab's
        lifetime, which is the one cost this tree's monitors deliberately
        accept for directories that still exist (see `_watch`)."""
        prefix = str(path)
        gone = [
            key
            for key in list(self._stores) + list(self._monitors)
            if key == prefix or key.startswith(prefix + "/")
        ]
        for key in gone:
            self._stores.pop(key, None)
            monitor = self._monitors.pop(key, None)
            if monitor is not None:
                monitor.cancel()

    def refresh_dir(self, path: str | Path) -> None:
        """Re-list *path* now, if this tree is showing it. For changes the
        app made itself (a rename): the directory monitors would get there on
        their own, half a second later, and the root has no monitor at all."""
        store = self._stores.get(str(path))
        if store is not None:
            self._fill_store(store, Path(path))

    @property
    def root(self) -> Path:
        return self._root

    def set_root(self, root: str | Path) -> None:
        """Point the whole tree at a different directory: every monitor is
        cancelled, every remembered store dropped, and the root row list built
        again from scratch.

        Expansion state is deliberately *not* carried over. The rows are of a
        different directory tree now — matching them up by name would expand
        paths the user never opened here — and the tree is small enough that
        re-expanding is cheaper than getting that wrong. Whoever re-roots is
        expected to `reveal` whatever should be showing afterwards."""
        new_root = Path(root)
        if new_root == self._root:
            return
        for monitor in self._monitors.values():
            monitor.cancel()
        self._monitors.clear()
        self._stores.clear()
        self._root = new_root
        self._menu_dir = new_root
        # Re-listing the root store empties the model of every old row, and a
        # TreeListModel drops the child models built for them along with it.
        self._fill_store(self._root_store, self._root)

    def reveal(self, path: str | Path) -> None:
        """Expand the directories above *path* and select its row (without
        stealing focus). A path the tree can't show — outside the project,
        under a symlinked directory, or hidden while hidden files are off —
        is a no-op."""
        try:
            parts = Path(path).relative_to(self._root).parts
        except ValueError:
            return
        if not parts:
            return
        depth = 0
        position = 0
        while position < self._tree_model.get_n_items():
            row: Gtk.TreeListRow = self._tree_model.get_item(position)
            if row.get_depth() < depth:
                return  # walked out of the expanded ancestor without a match
            node: _Node = row.get_item()
            if row.get_depth() == depth and node.name == parts[depth]:
                if depth == len(parts) - 1:
                    self._list_view.scroll_to(position, Gtk.ListScrollFlags.SELECT, None)
                    return
                # Children splice into the flat model right after this row,
                # so the sequential scan walks straight into them.
                row.set_expanded(True)
                depth += 1
            position += 1

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
        icon_name, color_class = filetypes.icon_for(node.name, node.is_dir)
        icon.set_from_icon_name(icon_name)
        # Rows are recycled, so both class lists are replaced wholesale on
        # every bind — never added to — or a row would keep the color and
        # dimming of whatever node it showed last.
        icon.set_css_classes([color_class] if color_class else [])
        box.set_css_classes(["filetree-dim"] if node.dim else [])
        label.set_label(node.name)

    def _on_activate(self, _list_view: Gtk.ListView, position: int) -> None:
        row: Gtk.TreeListRow = self._selection.get_item(position)
        node: _Node = row.get_item()
        if node.is_dir:
            row.set_expanded(not row.get_expanded())
        else:
            self.emit("open-file", str(node.path))

    # -- context menu --------------------------------------------------------

    def _row_at(self, x: float, y: float) -> Gtk.TreeListRow | None:
        """The row under (*x*, *y*) in the list view, or None for the empty
        space below the last one.

        Hit-tests the row widgets rather than picking whatever is under the
        pointer: a row is only partly covered by the widgets that make it up
        — the indent left of the icon and the space right of the name belong
        to no child at all — so picking would call two thirds of every row
        empty space. Only rows on screen are the list view's children (it
        recycles the rest), which is exactly the set a pointer can be over."""
        bands: list[tuple[float, float, Gtk.Widget]] = []
        child = self._list_view.get_first_child()
        while child is not None:
            found, bounds = child.compute_bounds(self._list_view)
            if found:
                bands.append((bounds.origin.y, bounds.origin.y + bounds.size.height, child))
            child = child.get_next_sibling()
        bands.sort(key=lambda band: band[0])
        for index, (top, bottom, widget) in enumerate(bands):
            # A row owns the seam below it as well as itself: the rows carry a
            # couple of pixels of margin, which is inside no widget's bounds,
            # and a menu that came up "on nothing" every twentieth pixel would
            # be a mystery. Only the last row stops at its own edge — past
            # that really is the empty space.
            if index + 1 < len(bands):
                bottom = bands[index + 1][0]
            if top <= y < bottom:
                expander = _expander_in(widget)
                return expander.get_list_row() if expander is not None else None
        return None

    def _on_right_click(
        self, gesture: Gtk.GestureClick, _n_press: int, x: float, y: float
    ) -> None:
        """Right-clicking the tree. On a row: both kinds can be copied, cut
        and renamed; only a file can be referenced in the chat — the agent's
        mention syntax is for files, and an item that quietly did nothing
        would read as broken — and only a folder can be pasted into, being the
        one kind with an inside. Below the last row: a paste into the project
        root, the only thing there is to do to a piece of empty tree."""
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        row = self._row_at(x, y)
        node: _Node | None = row.get_item() if row is not None else None

        if node is None:
            self._menu_path = ""
            self._menu_is_dir = False
            self._menu_dir = self._root
            self._popup_menu(_menu((_("Paste"), "tree.paste")), x, y)
            return

        # Selected as well as menued: with the whole row clickable, the
        # highlight is what says which one the menu is about.
        self._selection.set_selected(row.get_position())
        self._menu_path = str(node.path)
        self._menu_is_dir = node.is_dir
        self._menu_dir = node.path if node.is_dir else self._root

        items = [] if node.is_dir else [(_("Add to chat"), "tree.add-to-chat")]
        items += [(_("Copy"), "tree.copy"), (_("Cut"), "tree.cut")]
        if node.is_dir:
            items.append((_("Paste"), "tree.paste"))
        items.append((_("Rename…"), "tree.rename"))
        self._popup_menu(_menu(*items), x, y)

    def _popup_menu(self, menu: Gio.Menu, x: float, y: float) -> None:
        # Paste's state is settled here rather than per menu: every menu that
        # carries it opens through this.
        self._paste_action.set_enabled(fileclipboard.has_files(self.get_clipboard()))
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(self._list_view)
        popover.set_has_arrow(False)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()

    def _on_add_to_chat(self, _action: Gio.SimpleAction, _param) -> None:
        if self._menu_path:
            self.emit("add-to-chat", self._menu_path)

    def _on_rename(self, _action: Gio.SimpleAction, _param) -> None:
        if self._menu_path:
            self.emit("rename-request", self._menu_path, self._menu_is_dir)

    def _on_copy(self, _action: Gio.SimpleAction, _param) -> None:
        if self._menu_path:
            fileclipboard.set_files(self.get_clipboard(), [self._menu_path])

    def _on_cut(self, _action: Gio.SimpleAction, _param) -> None:
        # Nothing moves yet: a cut only says what a later paste should move,
        # and until then the file stays exactly where it is (the same bargain
        # every file manager makes).
        if self._menu_path:
            fileclipboard.set_files(self.get_clipboard(), [self._menu_path], cut=True)

    def _on_paste(self, _action: Gio.SimpleAction, _param) -> None:
        self.emit("paste-request", str(self._menu_dir))
