# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The editor panel's project file tree.

A `Gtk.ListView` over a `Gtk.TreeListModel`, lazily populated: a directory's
children are only listed the moment it is first expanded. Nothing else in
the sidebar looks like this — its own two-level grouping is hand-rolled flat
rows — so this is a new pattern, not a reuse.

The directories are the service's (split-service spec §3.23, PR-2.4): a
listing is `fs.list` (the entries in the tree's order with the ignored names
marked, one request off the main loop, landed at `GLib.PRIORITY_DEFAULT`),
and an expanded directory (the root too) is kept fresh by `fs.watch kind:
dir` — the service's debounced `dir-changed`, which lists it again; a
collapse drops the watch and a re-expansion lists again. A listing reuses
the rows of the entries that are still there, so an expanded folder stays
expanded through its parent's refresh. `Gtk.TreeListModel` calls the
children function for every directory row it binds, to draw its expander,
and drops what it gets; so a row's children store lives on the row's node
(`_Node.children`) and is only listed and watched once the row is really
expanded (`_open_dir`, from the row's `notify::expanded`).
"""

from __future__ import annotations

import logging
import weakref
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import contextmenu, fileclipboard, filetypes, openwith, openwithrows, remotefiles
from .api import protocol
from .i18n import _

log = logging.getLogger(__name__)


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
    """One row: a file or directory, never re-created for the same entry
    while its parent directory is listed (see `_splice`). *kind* is the
    listing's: ``file``, ``dir``, or ``symlink`` (a link to a folder
    inside the project: shown as one, never expanded)."""

    def __init__(self, name: str, path: Path, kind: str, dim: bool = False) -> None:
        super().__init__()
        self.name = name
        self.path = path
        self.kind = kind
        self.is_dir = kind != "file"
        self.expandable = kind == "dir"
        # Drawn at reduced opacity: a dotfile, or something git ignores —
        # still openable, but visibly not part of the project's real content.
        self.dim = dim
        # The rows under this one, made the first time the tree model asks
        # (`_create_children`) and kept: the model asks again at every bind
        # and every expansion, and must get the same store each time.
        self.children: Gio.ListStore | None = None
        self.open = False  # the row is expanded (`_on_row_expanded`)
        # The row box showing this node right now (`_on_bind`; rows are
        # recycled, so `set_dim` checks the box still shows this node).
        self.box: Gtk.Widget | None = None

    def set_dim(self, dim: bool) -> None:
        """The dimming changed (a name git now ignores, or no longer):
        kept on this node, so an expanded folder keeps its row, its
        expansion and its watch, and the row on screen is restyled."""
        self.dim = dim
        box = self.box
        if box is not None and getattr(box, "node", None) is self:
            box.set_css_classes(["filetree-dim"] if dim else [])


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
        # A file row's "Open In…" submenu picked an app. Payload is (absolute
        # path, the app id — a footer app's desktop-file id or
        # openwith.DEFAULT_APP_ID). The launch, and saying when it failed,
        # belong to the pane (see EditorPane._open_file_with).
        "open-with-request": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
    }

    def __init__(self, root: str | Path, show_hidden: bool = False) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True, hexpand=True)
        self._root = Path(root)
        self._show_hidden = show_hidden
        # The footer_apps setting, for the file rows' "Open In…" submenu
        # (set_footer_apps — the pane relays it from apply_settings).
        self._footer_apps: list[str] = []
        # path -> the handle of its `fs.watch kind: dir`, for every directory
        # this tree has expanded (and handle -> path for the events). Not
        # torn down on collapse (a modest, tab-lifetime cost); only ever grows
        # across directories actually opened, never the whole project up
        # front. The root has one too (it is the folder always open); a
        # collapsed folder's is dropped (`_close_dir`).
        self._watches: dict[str, str] = {}
        self._watched: dict[str, str] = {}
        # path -> the store holding that directory's rows, for every listed
        # directory (the root included). Lets a change this tree made itself
        # show up at once instead of waiting out the watch — see
        # `refresh_dir`.
        self._stores: dict[str, Gio.ListStore] = {}
        # The listings: the directories with an `fs.list` in flight, those
        # owed another once it lands (a change during the listing), and
        # those whose store has had at least one answer (what `reveal`
        # waits for). `_epoch` turns over with the root, so a listing of
        # the old root lands nowhere.
        self._listing: set[str] = set()
        self._relist: set[str] = set()
        self._filled: set[str] = set()
        self._epoch = 0
        # The path `reveal` is still walking towards, waiting on listings.
        self._reveal_target: Path | None = None
        # The rows whose `notify::expanded` is heard (`_on_bind`).
        self._hooked: weakref.WeakSet = weakref.WeakSet()
        # path -> the node whose folder it is, for every folder opened; and
        # the folders whose watch a collapse dropped (their rows kept, so a
        # re-expansion shows them at once and lists them again). A watch
        # costs the service a monitor under a per-client bound, so only
        # what is on screen holds one (review of PR 611).
        self._owners: dict[str, _Node] = {}
        self._parked: set[str] = set()
        self._shut = False
        # A reconnect re-sends the watches but the listings missed meanwhile
        # are gone: every folder shown is listed again (`_on_reconnect`).
        remotefiles.watcher().on_reset(self._on_reconnect)

        self._root_store = Gio.ListStore(item_type=_Node)
        self._list(self._root, self._root_store)
        # The root is the one folder always open: it is watched too.
        self._watch(self._root)
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
        open_with = Gio.SimpleAction.new("open-with", GLib.VariantType.new("s"))
        open_with.connect("activate", self._on_open_with)
        actions.add_action(open_with)
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

    def _list(self, directory: Path, store: Gio.ListStore) -> None:
        """(Re)list *directory* into *store*: `fs.list` off the main loop,
        landed by `_listed`. One listing per directory at a time; asked
        again meanwhile, it runs once more when the first lands."""
        key = str(directory)
        if self._stores.get(key) is not store:
            self._filled.discard(key)  # a new store for the path: nothing in it yet
        self._stores[key] = store
        if key in self._listing:
            self._relist.add(key)
            return
        self._listing.add(key)
        root, hidden, epoch = str(self._root), self._show_hidden, self._epoch
        remotefiles.off_main(
            lambda: remotefiles.list_dir(key, root, hidden),
            lambda kind, value: self._listed(key, store, epoch, kind, value),
            name="filetree-list",
        )

    def _listed(self, key: str, store: Gio.ListStore, epoch: int, kind: str, value) -> None:
        self._listing.discard(key)
        again = key in self._relist
        self._relist.discard(key)
        if self._shut or epoch != self._epoch or self._stores.get(key) is not store:
            # Re-rooted, forgotten or shut meanwhile: the rows are no one's.
            # A listing asked for the path's current store while this one
            # was in flight waited on it (`_relist`): it runs now.
            current = self._stores.get(key)
            if again and not self._shut and current is not None:
                self._list(Path(key), current)
            return
        if kind == "ok":
            entries, _truncated = value
            self._splice(store, Path(key), entries)
        elif value.error == protocol.ERROR_GONE and value.msgid == protocol.FOLDER_GONE_MSGID:
            # The folder is not there any more: its rows go, and so does
            # what the tree remembers under it (the root keeps its store and
            # its watch: a folder that comes back is listed again).
            self._splice(store, Path(key), [])
            if key != str(self._root):
                self.forget_dir(key)
        else:
            # Refused or failed (no `files` capability, the service
            # unreachable): the rows stay as they were, and a reconnect
            # lists again (`_on_reconnect`).
            log.info("filetree: listing %s refused: %s", key, remotefiles.refusal_words(value))
        self._filled.add(key)
        if again:
            self._list(Path(key), store)
        self._continue_reveal()

    def _splice(self, store: Gio.ListStore, directory: Path, entries: list) -> None:
        """*store* made to hold *entries*, keeping the node (and with it the
        row, its expansion and its children) of every entry that was there
        already: a refresh of a folder must not collapse the folders open
        inside it. Folders that left the listing are forgotten."""
        old = [store.get_item(index) for index in range(store.get_n_items())]
        by_key = {(node.name, node.kind): node for node in old}
        target: list[_Node] = []
        for entry in entries:
            dim = entry.name.startswith(".") or entry.ignored
            node = by_key.get((entry.name, entry.kind))
            if node is None:
                node = _Node(entry.name, directory / entry.name, entry.kind, dim=dim)
            elif node.dim != dim:
                node.set_dim(dim)
            target.append(node)
        keep = {id(node) for node in target}
        for node in old:
            if id(node) not in keep and node.expandable:
                self.forget_dir(node.path)
        old_ids = {id(node) for node in old}
        kept_old = [node for node in old if id(node) in keep]
        kept_new = [node for node in target if id(node) in old_ids]
        if not kept_old or kept_old != kept_new:
            # Nothing kept (a first listing), or reordered: one splice.
            store.splice(0, store.get_n_items(), target)
            return
        for index in reversed(range(len(old))):
            if id(old[index]) not in keep:
                store.remove(index)
        for index, node in enumerate(target):
            if index >= store.get_n_items() or store.get_item(index) is not node:
                store.insert(index, node)

    def _create_children(self, item: _Node) -> Gio.ListModel | None:
        """Called by `Gtk.TreeListModel` for every directory row it binds
        (to draw its expander, the store then dropped) and again when one
        is expanded: so the store is made once, on the node, and costs
        nothing until `_open_dir` lists it. A symlinked directory is left
        with no expander at all: per the standing untrusted-repo-content
        rule, the tree never follows one (the service lists only links that
        stay inside the project, and marks those to folders `symlink`)."""
        if not item.expandable:
            return None
        if item.children is None:
            item.children = Gio.ListStore(item_type=_Node)
        return item.children

    def _open_dir(self, node: _Node) -> None:
        """A directory row was expanded: its listing and its watch the
        first time; after a collapse, its watch again with a listing that
        catches up on what the collapse missed — the rows were kept, so
        they show at once."""
        if not node.expandable or node.children is None or self._shut:
            return
        node.open = True
        key = str(node.path)
        if self._stores.get(key) is not node.children:
            self._owners[key] = node
            self._list(node.path, node.children)
            self._watch(node.path)
            return
        if key in self._parked:
            self._parked.discard(key)
            self._list(node.path, node.children)
            self._watch(node.path)

    def _close_dir(self, node: _Node) -> None:
        """A directory row was collapsed: the watches of it and of every
        folder under it are dropped (parked: the rows stay). GTK collapses
        the rows under it too (a re-expansion shows them closed), so the
        folders under it are closed here as well; each lists again when it
        is next expanded."""
        key = str(node.path)
        for inner, owner in self._owners.items():
            if inner == key or inner.startswith(key + "/"):
                owner.open = False
        node.open = False
        for watched in [k for k in self._watches if k == key or k.startswith(key + "/")]:
            if watched != str(self._root):
                self._unwatch(watched)
                self._parked.add(watched)

    def _on_row_expanded(self, row: Gtk.TreeListRow, _pspec) -> None:
        node = row.get_item()
        if not isinstance(node, _Node):
            return
        if row.get_expanded():
            self._open_dir(node)
        else:
            self._close_dir(node)

    def _on_reconnect(self) -> None:
        """The link came back (`remotefiles.reset`): the watches were sent
        again, and every folder on screen is listed again — a pane made
        while the service was unreachable fills in."""
        if self._shut:
            return
        for key, store in list(self._stores.items()):
            if key not in self._parked:
                self._list(Path(key), store)

    # -- live refresh ----------------------------------------------------------

    def _watch(self, path: Path) -> None:
        key = str(path)
        if key in self._watches:
            return
        handle = remotefiles.watcher().watch(key, self._on_dir_changed, kind=protocol.WATCH_DIR)
        self._watches[key] = handle
        self._watched[handle] = key

    def _unwatch(self, key: str) -> None:
        handle = self._watches.pop(key, None)
        if handle is not None:
            self._watched.pop(handle, None)
            remotefiles.watcher().unwatch(handle)

    def _on_dir_changed(self, event: dict) -> None:
        """The service's `dir-changed` (debounced there, 300 ms): list the
        directory again, if this tree still shows it."""
        key = self._watched.get(str(event.get("handle")))
        store = self._stores.get(key) if key is not None else None
        if store is not None and not self._shut:
            self._list(Path(key), store)

    def forget_dir(self, path: str | Path) -> None:
        """Drop what this tree remembers about *path* and anything under it:
        the row stores and the watches keeping them fresh. For a directory
        that has just been renamed or removed — its watch now watches a path
        nothing will ever change again, and expanding the new name builds its
        store fresh. Without this the entries would sit there for the tab's
        lifetime, which is the one cost this tree's watches deliberately
        accept for directories that still exist (see `_watch`)."""
        prefix = str(path)
        gone = [
            key
            for key in list(self._stores) + list(self._watches)
            if key == prefix or key.startswith(prefix + "/")
        ]
        for key in gone:
            self._stores.pop(key, None)
            self._filled.discard(key)
            self._owners.pop(key, None)
            self._unwatch(key)
        for key in [k for k in self._parked if k == prefix or k.startswith(prefix + "/")]:
            self._parked.discard(key)
            self._owners.pop(key, None)

    def refresh_dir(self, path: str | Path) -> None:
        """Re-list *path* now, if this tree is showing it. For changes the
        app made itself (a rename): the watches would get there on their
        own, a debounce later."""
        store = self._stores.get(str(path))
        if store is not None:
            self._list(Path(path), store)

    def shutdown(self) -> None:
        """The pane is closing for good (`EditorPane.shutdown`): every
        directory's watch on the service is dropped, and a listing still in
        flight lands nowhere."""
        self._shut = True
        self._reveal_target = None
        for key in list(self._watches):
            self._unwatch(key)
        remotefiles.watcher().off_reset(self._on_reconnect)

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
        for key in list(self._watches):
            self._unwatch(key)
        self._stores.clear()
        self._filled.clear()
        self._relist.clear()
        self._owners.clear()
        self._parked.clear()
        self._epoch += 1  # a listing of the old root lands nowhere
        self._reveal_target = None
        self._root = new_root
        self._menu_dir = new_root
        # The old root's rows go at once (none of them is this root's), and a
        # TreeListModel drops the child models built for them along with it;
        # the new root's land with its listing.
        self._root_store.remove_all()
        self._list(self._root, self._root_store)
        self._watch(self._root)

    def reveal(self, path: str | Path) -> None:
        """Expand the directories above *path* and select its row (without
        stealing focus). A path the tree can't show — outside the project,
        under a symlinked directory, or hidden while hidden files are off —
        is a no-op. The listings are the service's, so the walk goes as far
        as the rows already listed and waits on each listing it needs
        (`_continue_reveal`, run again as each lands); a newer reveal, or a
        re-root, replaces it."""
        self._reveal_target = Path(path)
        self._continue_reveal()

    def _waiting_on(self, key: str) -> bool:
        return key not in self._filled or key in self._listing

    def _continue_reveal(self) -> None:
        target = self._reveal_target
        if target is None:
            return
        try:
            parts = target.relative_to(self._root).parts
        except ValueError:
            parts = ()
        if not parts:
            self._reveal_target = None
            return
        if self._waiting_on(str(self._root)):
            return  # the root's rows are still coming
        depth = 0
        position = 0
        while position < self._tree_model.get_n_items():
            row: Gtk.TreeListRow = self._tree_model.get_item(position)
            if row.get_depth() < depth:
                break  # walked out of the expanded ancestor without a match
            node: _Node = row.get_item()
            if row.get_depth() == depth and node.name == parts[depth]:
                if depth == len(parts) - 1:
                    self._reveal_target = None
                    self._list_view.scroll_to(position, Gtk.ListScrollFlags.SELECT, None)
                    return
                if not node.expandable:
                    break
                # Children splice into the flat model right after this row,
                # so the sequential scan walks straight into them — once
                # they are listed.
                row.set_expanded(True)
                self._open_dir(node)
                if self._waiting_on(str(node.path)):
                    return
                depth += 1
            position += 1
        self._reveal_target = None

    def set_show_hidden(self, show_hidden: bool) -> None:
        """Applies immediately to the root and to any directory refreshed
        from here on; a directory already expanded keeps its current rows
        until it next changes on disk or is collapsed and re-expanded."""
        if show_hidden == self._show_hidden:
            return
        self._show_hidden = show_hidden
        self._list(self._root, self._root_store)

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
        if node.expandable and row not in self._hooked:
            # An expansion (a click, the keyboard, `reveal`) is when the
            # directory is listed and watched (`_open_dir`).
            self._hooked.add(row)
            row.connect("notify::expanded", self._on_row_expanded)
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
        box.node = node  # what `_Node.set_dim` checks before restyling
        node.box = box
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
        menu = _menu(*items)
        rows: list[Gtk.Widget] = []
        if not node.is_dir:
            # The same submenu the diff page's files list offers: the footer
            # apps that take a file, then the desktop's default app.
            submenu = openwithrows.file_open_with_menu(
                rows, self._footer_apps, str(node.path), "tree.open-with"
            )
            menu.append_submenu(_("Open In…"), submenu)
        self._popup_menu(menu, x, y, rows)

    def _popup_menu(self, menu: Gio.Menu, x: float, y: float, rows: list[Gtk.Widget] | None = None) -> None:
        # Paste's state is settled here rather than per menu: every menu that
        # carries it opens through this.
        self._paste_action.set_enabled(fileclipboard.has_files(self.get_clipboard()))
        popover = Gtk.PopoverMenu.new_from_model(menu)
        openwithrows.slot_them(popover, list(rows or ()))
        contextmenu.popup_at(popover, self._list_view, x, y)

    def set_footer_apps(self, app_ids: list[str]) -> None:
        """The footer_apps setting's ids, listed (those that take a file) in
        the file rows' "Open In…" submenu."""
        self._footer_apps = list(app_ids)

    def open_with_labels(self, path: str) -> list[str]:
        """The labels a file row's "Open In…" submenu would list for *path*
        (for the e2e)."""
        return [label for _id, _icon, label in openwith.file_open_with_entries(self._footer_apps, path)]

    def activate_open_with(self, path: str, app_id: str) -> None:
        """Pick *app_id* from the row of *path*'s "Open In…" submenu as a
        click would (for the e2e)."""
        self._menu_path, self._menu_is_dir = path, False
        self.activate_action("tree.open-with", GLib.Variant("s", app_id))

    def _on_open_with(self, _action: Gio.SimpleAction, param: GLib.Variant) -> None:
        if self._menu_path and not self._menu_is_dir:
            self.emit("open-with-request", self._menu_path, param.get_string())

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
