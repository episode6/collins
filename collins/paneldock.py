# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The panel dock: the region around the agent terminal, as a split tree.

One `PanelDock` per session tab. It realizes a `docktree.DockTree` whose
leaves are the agent-terminal widget (exactly one, never closable) and
`PanelStrip`s, each split materialized as a fixed-orientation `Gtk.Paned`
with a hardened `PanedSizer`. New layouts come from new splits — nothing
ever flips an existing paned's axis; the old bottom↔right *swap* is now
"move every shell page to a strip at the other home dock".

The *home strip* is the strip Ctrl+J toggles: it lives on the home edge
of the terminal (`home_position`, "bottom" | "right"), can be hidden
without closing (pages keep running), and is recreated on demand after it
collapses. Any strip whose last page closes collapses — its node is
removed and the sibling promotes — so trees only get as deep as the pages
the user actually keeps.
"""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, GLib, GObject, Gtk  # noqa: E402

from .docktree import DockTree, Leaf, Split  # noqa: E402
from .panedsizer import PanedSizer  # noqa: E402

# The tree side a home strip splits off the terminal, per home position.
_HOME_SIDES = {"bottom": "below", "right": "right"}


class _PaneRec:
    """One split's GTK realization: its paned, sizer, and managed strip
    (the child whose pixel size the sizer remembers)."""

    def __init__(self, paned: Gtk.Paned, sizer: PanedSizer, managed: Gtk.Widget) -> None:
        self.paned = paned
        self.sizer = sizer
        self.managed = managed


class PanelDock(Adw.Bin):
    """GTK realization of the dock tree around one session's terminal."""

    __gsignals__ = {
        # Re-emitted from any strip page ringing BEL, for the visual bell.
        "bell": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # The user resized the home strip: (mode, px) — the axis seed the
        # window persists app-wide (panel_size_bottom / panel_size_right).
        "size-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, int)),
    }

    def __init__(self, terminal: Gtk.Widget, strip_factory, home_position: str) -> None:
        """*terminal* is the agent-terminal widget (the dock's fixed leaf);
        `strip_factory() -> PanelStrip` builds a strip wired for shells;
        *home_position* seeds where Ctrl+J's strip opens."""
        super().__init__()
        self._terminal = terminal
        self._strip_factory = strip_factory
        self._home_position = home_position if home_position in _HOME_SIDES else "bottom"
        self._home_strip = None
        # The home strip's remembered per-axis sizes, surviving the strip
        # (and its paned) collapsing and being recreated. Live values are
        # folded back in whenever the home paned goes away.
        self._home_sizes: dict[str, int] = {}
        self._tree = DockTree(terminal)
        self._panes: dict[Split, _PaneRec] = {}
        self._settings: dict | None = None
        self._size_lookup = None  # (key) -> app-wide px seed, set by the window
        self._focus_terminal = None  # () -> None, grabs the agent VTE
        self._ever_spawned = False  # any shell ever ran in this dock
        self._next_shell = 1
        self.set_child(terminal)

    # -- wiring ------------------------------------------------------------

    def set_size_lookup(self, lookup) -> None:
        """`lookup(mode) -> px` supplies the app-wide last-set strip size
        ("bottom"/"right"), seeding splits this dock hasn't sized yet."""
        self._size_lookup = lookup

    def set_focus_terminal(self, grab) -> None:
        """`grab()` lands the cursor in the agent terminal — called when a
        strip that held focus hides or collapses."""
        self._focus_terminal = grab

    def apply_settings(self, settings: dict) -> None:
        self._settings = settings
        for strip in self.strips():
            strip.apply_settings(settings)

    # -- queries -----------------------------------------------------------

    @property
    def ever_spawned(self) -> bool:
        """A shell ran in some strip at some point in this tab's life."""
        return self._ever_spawned

    @property
    def home_position(self) -> str:
        return self._home_position

    @property
    def home_visible(self) -> bool:
        return self._home_strip is not None and self._home_strip.get_visible()

    def strips(self) -> list:
        """Every strip, in the tree's spatial order."""
        return [leaf for leaf in self._tree.leaves() if leaf is not self._terminal]

    def shell_pages(self) -> list:
        """Every shell page across every strip, in spatial-then-tab order —
        the stable order panel history is captured in."""
        return [shell for strip in self.strips() for shell in strip.shell_pages()]

    def has_running_command(self) -> bool:
        return any(shell.page_busy() for shell in self.shell_pages())

    def next_shell_number(self) -> int:
        """The 1-based ordinal for a new shell's tab title. Dock-wide, so
        titles stay unique across strips; restarts from 1 once no shell
        pages remain anywhere (an emptied dock, like an emptied strip
        before it, starts counting over)."""
        if not self.shell_pages():
            self._next_shell = 1
        number = self._next_shell
        self._next_shell += 1
        self._ever_spawned = True
        return number

    # -- the home strip ----------------------------------------------------

    def show_home(self, restore_texts: list[str] | None = None) -> None:
        """Show the shells' home strip, creating it (on the home edge of
        the terminal) on first use; `restore_texts` recreates one shell per
        saved panel history when the strip spawns its first shells."""
        if self._home_strip is None:
            self._create_home_strip()
        strip = self._home_strip
        strip.open(restore_texts)
        if not strip.get_visible():
            strip.set_visible(True)
            self._home_rec().sizer.apply()

    def hide_home(self) -> None:
        """Hide (don't close) the home strip: pages keep running, the node
        and its size survive for the next show."""
        strip = self._home_strip
        if strip is None or not strip.get_visible():
            return
        rec = self._home_rec()
        rec.sizer.remember()
        refocus = strip.has_page_focus()
        strip.set_visible(False)
        if refocus and self._focus_terminal is not None:
            self._focus_terminal()

    def focus_home(self) -> None:
        if self._home_strip is not None:
            self._home_strip.grab_page_focus()

    def set_home_position(self, mode: str) -> None:
        """Re-home a (typically hidden) strip: session restore applying the
        saved mode before showing anything."""
        if mode not in _HOME_SIDES or mode == self._home_position:
            return
        self._home_position = mode
        if self._home_strip is not None:
            self._relocate_home()

    def swap_home(self) -> str:
        """The swap action's new meaning: flip the home position, relocate
        the home strip there, and gather every shell page into it (other
        strips empty out and collapse). Visually the same strip relocation
        as the old orientation flip. Returns the new position."""
        self._home_position = "right" if self._home_position == "bottom" else "bottom"
        if self._home_strip is not None:
            self._relocate_home()
        elif any(strip.shell_pages() for strip in self.strips()):
            self._create_home_strip()
        home = self._home_strip
        if home is not None:
            for strip in self.strips():
                if strip is home:
                    continue
                for shell in strip.shell_pages():
                    strip.transfer_to(shell, home)
        return self._home_position

    def home_sizes(self) -> dict[str, int]:
        """The home strip's per-axis sizes for per-session persistence,
        live divider position included. Falsy when never sized."""
        rec = self._home_rec()
        if rec is not None:
            rec.sizer.remember()
            self._home_sizes.update(rec.sizer.snapshot())
        return dict(self._home_sizes)

    def seed_home_sizes(self, sizes: dict) -> None:
        """Adopt a session's saved home-strip sizes (untrusted input; the
        sizer re-validates on use)."""
        for mode in _HOME_SIDES:
            size = sizes.get(mode)
            if isinstance(size, int) and not isinstance(size, bool) and size > 0:
                self._home_sizes[mode] = size
        rec = self._home_rec()
        if rec is not None:
            for mode, size in self._home_sizes.items():
                rec.sizer.set_remembered(mode, size)

    def _home_rec(self) -> _PaneRec | None:
        if self._home_strip is None:
            return None
        return self._panes.get(self._tree.find(self._home_strip).parent)

    def _create_home_strip(self) -> None:
        strip = self._new_strip()
        self._home_strip = strip
        self._split_leaf(self._terminal, strip, _HOME_SIDES[self._home_position])
        rec = self._home_rec()
        for mode, size in self._home_sizes.items():
            rec.sizer.set_remembered(mode, size)
        rec.sizer.apply()

    def _relocate_home(self) -> None:
        """Detach the home strip's node and re-split it onto the terminal's
        current home edge, pages, visibility and focus intact."""
        strip = self._home_strip
        rec = self._home_rec()
        rec.sizer.remember()
        self._home_sizes.update(rec.sizer.snapshot())
        visible = strip.get_visible()
        refocus = strip.has_page_focus()
        self._remove_leaf(strip)
        self._split_leaf(self._terminal, strip, _HOME_SIDES[self._home_position])
        rec = self._home_rec()
        for mode, size in self._home_sizes.items():
            rec.sizer.set_remembered(mode, size)
        strip.set_visible(visible)
        if visible:
            rec.sizer.apply()
        if refocus:
            GLib.idle_add(strip.grab_page_focus)

    # -- shells across strips ----------------------------------------------

    def select_busy_shell(self) -> None:
        """Front the first busy shell — revealing a hidden home strip if
        that's where it lives — so a close confirmation's "will be
        terminated" points at something visible."""
        for strip in self.strips():
            if any(shell.page_busy() for shell in strip.shell_pages()):
                if strip is self._home_strip and not strip.get_visible():
                    self.show_home()
                strip.select_busy_page()
                return

    def capture_shell_texts(self) -> list[str]:
        """Every shell page's scrollback, in the dock's stable page order."""
        return [shell.capture_contents() for shell in self.shell_pages()]

    def clear_shells(self) -> None:
        for strip in self.strips():
            strip.clear_all()

    # -- moving pages and splitting -----------------------------------------

    def move_targets(self, strip) -> list[tuple[str, object]]:
        """The other strips a page of *strip* could move to, labeled by
        their selected page's title (the strip context menu's "Move to")."""
        targets = []
        for other in self.strips():
            if other is strip:
                continue
            selected = other.selected_page_widget()
            if selected is not None:
                targets.append((selected.page_title(), other))
        return targets

    def move_page(self, strip, widget, target) -> None:
        """Move *widget*'s tab from *strip* into *target*, selecting and
        focusing it there. The source collapses if that emptied it."""
        if target is strip or target not in self._tree or strip not in self._tree:
            return
        strip.transfer_to(widget, target)
        target.select_widget(widget)
        GLib.idle_add(widget.grab_page_focus)

    def split_page(self, strip, widget, side: str) -> None:
        """Split *strip*'s own node on *side* and move *widget* into the
        new strip there. Splitting a single-page strip is a relocation:
        the emptied source collapses right after."""
        if strip not in self._tree or side not in ("left", "right", "above", "below"):
            return
        target = self._new_strip()
        self._split_leaf(strip, target, side)
        strip.transfer_to(widget, target)
        rec = self._panes.get(self._tree.find(target).parent)
        if rec is not None:
            rec.sizer.apply()
        target.select_widget(widget)
        GLib.idle_add(widget.grab_page_focus)

    def move_focused_page_next(self) -> None:
        """Cycle the focused page to the next strip (win.move-panel-page)."""
        strips = self.strips()
        if len(strips) < 2:
            return
        source = next((s for s in strips if s.has_page_focus()), None)
        if source is None:
            return
        widget = source.selected_page_widget()
        if widget is None:
            return
        target = strips[(strips.index(source) + 1) % len(strips)]
        self.move_page(source, widget, target)

    # -- strip lifecycle -----------------------------------------------------

    def _new_strip(self):
        strip = self._strip_factory()
        if self._settings is not None:
            strip.apply_settings(self._settings)
        strip.set_page_mover(self)
        strip.connect("bell", lambda *_: self.emit("bell"))
        strip.connect("empty", self._on_strip_empty)
        return strip

    def _on_strip_empty(self, strip) -> None:
        # Deferred: "empty" can fire mid-transfer (the page has left the
        # source view but not yet landed in its target), and collapsing
        # reparents widgets the transfer is still working around.
        GLib.idle_add(self._collapse_strip, strip)

    def _collapse_strip(self, strip) -> bool:
        if strip not in self._tree or strip.page_count > 0:
            return GLib.SOURCE_REMOVE  # repopulated or already gone
        root = self.get_root()
        focus = root.get_focus() if root is not None else None
        refocus = focus is None or focus is strip or focus.is_ancestor(strip)
        if strip is self._home_strip:
            rec = self._home_rec()
            rec.sizer.remember()
            self._home_sizes.update(rec.sizer.snapshot())
            self._home_strip = None
        self._remove_leaf(strip)
        if refocus and self._focus_terminal is not None:
            self._focus_terminal()
        return GLib.SOURCE_REMOVE

    # -- tree <-> widget mirroring -------------------------------------------

    def _widget_of(self, node: Leaf | Split) -> Gtk.Widget:
        return self._panes[node].paned if isinstance(node, Split) else node.value

    def _place(self, node: Leaf | Split) -> None:
        """Put *node*'s widget into the container slot its tree position
        says it occupies (the dock itself for the root)."""
        widget = self._widget_of(node)
        parent = node.parent
        if parent is None:
            self.set_child(widget)
        elif parent.slot_of(node) == "a":
            self._panes[parent].paned.set_start_child(widget)
        else:
            self._panes[parent].paned.set_end_child(widget)

    def _unplace(self, node: Leaf | Split) -> None:
        """Detach *node*'s widget from its container, leaving the slot
        empty. Python references (the tree, self._panes) keep the widget —
        and any VTE children riding in it — alive across the move."""
        parent = node.parent
        if parent is None:
            self.set_child(None)
        elif parent.slot_of(node) == "a":
            self._panes[parent].paned.set_start_child(None)
        else:
            self._panes[parent].paned.set_end_child(None)

    def _contains_terminal(self, node: Leaf | Split) -> bool:
        if isinstance(node, Leaf):
            return node.value is self._terminal
        return self._contains_terminal(node.a) or self._contains_terminal(node.b)

    def _split_leaf(self, at_widget, new_widget, side: str) -> Split:
        """Mirror `tree.split` onto widgets: a new fixed-axis paned takes
        the split leaf's old slot, with the leaf and the new strip as its
        children and a PanedSizer managing the strip's share."""
        leaf = self._tree.find(at_widget)
        self._unplace(leaf)
        split = self._tree.split(at_widget, new_widget, side)
        vertical = split.orientation == "v"
        paned = Gtk.Paned(
            orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
            hexpand=True,
            vexpand=True,
        )
        paned.set_wide_handle(True)
        axis = "bottom" if vertical else "right"
        new_leaf = split.a if split.a.value is new_widget else split.b
        managed_end = new_leaf is split.b
        sizer = PanedSizer(
            paned,
            key=lambda a=axis: a,
            occupied=new_widget.get_visible,
            end_child=managed_end,
        )
        sizer.set_lookup(self._lookup_size)
        sizer.connect("size-changed", self._on_strip_size_changed, new_widget)
        self._panes[split] = _PaneRec(paned, sizer, new_widget)
        # The terminal's side soaks up window resizes; the strip side keeps
        # its pixel size. A strip-only split gives the stretch to the
        # pre-existing (unmanaged) side. Nothing is allowed to shrink away.
        if self._contains_terminal(split.a) or self._contains_terminal(split.b):
            a_resize = self._contains_terminal(split.a)
        else:
            a_resize = managed_end
        paned.set_resize_start_child(a_resize)
        paned.set_resize_end_child(not a_resize)
        paned.set_shrink_start_child(False)
        paned.set_shrink_end_child(False)
        paned.set_start_child(self._widget_of(split.a))
        paned.set_end_child(self._widget_of(split.b))
        self._place(split)
        return split

    def _remove_leaf(self, widget) -> None:
        """Mirror `tree.remove`: dissolve the leaf's parent paned and
        promote the sibling's widget into its slot. The leaf's widget is
        left unparented — the caller keeps it (relocation) or lets it be
        collected (collapse)."""
        leaf = self._tree.find(widget)
        parent = leaf.parent
        rec = self._panes.pop(parent)
        sibling = parent.sibling_of(leaf)
        sibling_widget = self._widget_of(sibling)
        rec.paned.set_start_child(None)
        rec.paned.set_end_child(None)
        grand = parent.parent
        self._tree.remove(widget)
        if grand is None:
            self.set_child(sibling_widget)
        elif grand.slot_of(sibling) == "a":
            self._panes[grand].paned.set_start_child(sibling_widget)
        else:
            self._panes[grand].paned.set_end_child(sibling_widget)

    # -- sizing --------------------------------------------------------------

    def _lookup_size(self, key: str) -> int:
        return int(self._size_lookup(key) or 0) if self._size_lookup is not None else 0

    def _on_strip_size_changed(self, _sizer, key: str, size: int, strip) -> None:
        """Only the home strip's divider updates the app-wide axis seeds —
        satellite strips size themselves without shifting the defaults."""
        if strip is self._home_strip:
            self.emit("size-changed", key, size)
