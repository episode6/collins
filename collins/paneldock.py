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

from . import paneldnd  # noqa: E402
from .docktree import DockTree, Leaf, Split  # noqa: E402
from .dockzones import EDGE_ZONES  # noqa: E402
from .panedsizer import PanedSizer  # noqa: E402
from .tabguard import guard  # noqa: E402

# The tree side a home strip splits off the terminal, per home position.
_HOME_SIDES = {"bottom": "below", "right": "right"}
# What the rotate button flips: an axis, not a side. A strip divided from
# the terminal vertically is on the "bottom" axis whether it sits above or
# below it; horizontally, on the "right" axis whether left or right.
_OTHER_AXIS = {"bottom": "right", "right": "bottom"}


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
        # The home strip moved to the other axis under a rotation: (mode) —
        # the app-wide panel_position default follows it, as it followed the
        # bottom/right swap this button used to fire.
        "home-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
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
        self._page_factory = None  # (page_dict) -> PanelPage | None, for restore
        self._ever_spawned = False  # any shell ever ran in this dock
        self._next_shell = 1
        self._next_hist = 0  # next shell's persistent panel-history ordinal
        self._restoring = False  # restore_layout is rebuilding the tree
        # The tree's widgets live in a content bin under a dock-wide
        # overlay; the drop zones ride the overlay so a page drag can
        # target every leaf's edges at once (paneldnd.DropZones).
        self._content = Adw.Bin(child=terminal)
        self._zones = paneldnd.DropZones(self._on_zone_drop)
        overlay = Gtk.Overlay(child=self._content)
        overlay.add_overlay(self._zones)
        self.set_child(overlay)
        # The guard group for this dock's strip views dies with the dock.
        self.connect("destroy", lambda *_: guard.clear_fallback(self))

    # -- wiring ------------------------------------------------------------

    def set_size_lookup(self, lookup) -> None:
        """`lookup(mode) -> px` supplies the app-wide last-set strip size
        ("bottom"/"right"), seeding splits this dock hasn't sized yet."""
        self._size_lookup = lookup

    def set_focus_terminal(self, grab) -> None:
        """`grab()` lands the cursor in the agent terminal — called when a
        strip that held focus hides or collapses."""
        self._focus_terminal = grab

    def set_page_factory(self, factory) -> None:
        """`factory(page_dict) -> PanelPage | None` conjures a non-shell page
        from its serialized layout entry (see panellayout) during restore.
        None drops the entry — an unknown kind, or state the factory won't
        trust — and a strip left with nothing collapses right after."""
        self._page_factory = factory

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
        before it, starts counting over). Not mid-restore, though: shells
        rebuild before their strip enters the tree, so the emptiness test
        would reset the count for every one of them ("Terminal 1" thrice)."""
        if not self._restoring and not self.shell_pages():
            self._next_shell = 1
        number = self._next_shell
        self._next_shell += 1
        self._ever_spawned = True
        return number

    def next_hist_ordinal(self) -> int:
        """The persistent panel-history ordinal for a new shell page.
        Unlike the tab-title number, never reused within this dock's life:
        a reused ordinal would adopt a closed shell's saved scrollback."""
        ordinal = self._next_hist
        self._next_hist += 1
        return ordinal

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
        """The record of the paned dividing the home strip's branch from
        the terminal's — the divider whose position *is* the home strip
        size. Not simply the home leaf's parent: splitting a tab inside
        the home strip inserts new splits between the leaf and that
        divider, so it is the split *separating* the two (see
        `DockTree.separator_of`)."""
        if self._home_strip is None:
            return None
        try:
            split = self._tree.separator_of(self._home_strip, self._terminal)
        except ValueError:
            return None  # the strip left the tree (a collapse in flight)
        return self._panes.get(split)

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

    # -- non-shell pages -----------------------------------------------------

    def pages(self) -> list:
        """Every page across every strip, in spatial-then-tab order."""
        return [page for strip in self.strips() for page in strip.pages()]

    def open_page(self, widget) -> None:
        """Open a non-shell page as a tab in a strip beside the terminal:
        the first strip right of it, else a new one split off the
        terminal's right edge. The join-don't-split default keeps opening
        five PRs from carving the dock into five slivers."""
        strip = self._strip_right_of_terminal()
        if strip is None:
            strip = self._new_strip()
            split = self._split_leaf(self._terminal, strip, "right")
            self._panes[split].sizer.apply()
        strip.add_page(widget)
        self._reveal_strip(strip)
        GLib.idle_add(widget.grab_page_focus)

    def reveal_page(self, widget) -> None:
        """Front an existing page: select its tab, and show its strip if it
        is the hidden home strip (the one strip that can hide)."""
        for strip in self.strips():
            if widget in strip.pages():
                strip.select_widget(widget)
                self._reveal_strip(strip)
                GLib.idle_add(widget.grab_page_focus)
                return

    def _reveal_strip(self, strip) -> None:
        if strip is self._home_strip and not strip.get_visible():
            strip.set_visible(True)
            self._home_rec().sizer.apply()

    def _strip_right_of_terminal(self):
        """The first strip in the subtree right of the terminal, or None.

        Walk up from the terminal leaf: the first horizontal split holding
        the terminal's branch on the left has everything to its right in the
        other branch, and that subtree's first strip (spatial order) is the
        join target.
        """
        node = self._tree.find(self._terminal)
        while node.parent is not None:
            parent = node.parent
            if parent.orientation == "h" and parent.slot_of(node) == "a":
                strip = self._first_strip(parent.b)
                if strip is not None:
                    return strip
            node = parent
        return None

    def _first_strip(self, node: Leaf | Split):
        if isinstance(node, Leaf):
            return node.value if node.value is not self._terminal else None
        return self._first_strip(node.a) or self._first_strip(node.b)

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

    def capture_shell_texts(self) -> dict[int, str]:
        """Every shell page's scrollback under its persistent history
        ordinal — the explicit keep-set panelhistory.save_all prunes stale
        files against."""
        return {shell.hist: shell.capture_contents() for shell in self.shell_pages()}

    def clear_shells(self) -> None:
        for strip in self.strips():
            strip.clear_all()

    # -- layout persistence --------------------------------------------------

    def capture_layout(self) -> dict | None:
        """The dock as a `panel_layout` entry (see panellayout): home mode
        and sizes, plus the serialized split tree when any strip holds a
        persistable page. None when the dock was never used at all, so a
        session's saved layout survives tabs that never touched it."""
        sizes = self.home_sizes()
        tree = self._serialize_node(self._tree.root)
        if tree == {"terminal": True}:
            tree = None  # no strips (or none persistable): the fresh default
        if tree is None and not sizes and not self._ever_spawned:
            return None
        entry: dict = {"mode": self._home_position}
        if sizes:
            entry["sizes"] = sizes
        if tree is not None:
            entry["tree"] = tree
        return entry

    def _serialize_node(self, node: Leaf | Split) -> dict | None:
        """One tree node as its layout dict. A strip with nothing
        persistable serializes to None and its split dissolves around it —
        the same promotion an emptied strip's collapse performs live."""
        if isinstance(node, Leaf):
            if node.value is self._terminal:
                return {"terminal": True}
            return self._serialize_strip(node.value)
        a = self._serialize_node(node.a)
        b = self._serialize_node(node.b)
        if a is None:
            return b
        if b is None:
            return a
        rec = self._panes[node]
        rec.sizer.remember()
        axis = "bottom" if node.orientation == "v" else "right"
        return {
            "split": node.orientation,
            "size": rec.sizer.remembered(axis),
            "managed": "b" if rec.sizer.manages_end else "a",
            "a": a,
            "b": b,
        }

    def _serialize_strip(self, strip) -> dict | None:
        """One strip's layout dict, pages serialized by their own
        `page_state`. A page without one (a foreign tab mid-bounce, a kind
        that opted out) simply doesn't persist."""
        pairs = [
            (widget, state)
            for widget in strip.pages()
            if (state := getattr(widget, "page_state", lambda: None)())
        ]
        if not pairs:
            return None
        selected_widget = strip.selected_page_widget()
        selected = next(
            (i for i, (widget, _state) in enumerate(pairs) if widget is selected_widget), 0
        )
        return {
            "strip": {
                "open": bool(strip.get_visible()),
                "home": strip is self._home_strip,
                "selected": selected,
                "pages": [state for _widget, state in pairs],
            }
        }

    def restore_layout(self, tree: dict, shell_texts: dict[int, str]) -> None:
        """Rebuild a saved split tree into this still-fresh dock (session
        restore, before the user could have split anything themselves).
        *tree* must be validated and pruned (see panellayout) — every page
        in it is a kind this dock can conjure: shells spawn with their saved
        scrollback from *shell_texts*, other kinds come back through the
        injected page factory. Hidden strips rebuild hidden, their shells
        running."""
        if len(self._tree) > 1:
            return  # not fresh: the layout lost the race to a user action
        self._content.set_child(None)  # free the terminal for reparenting
        self._restoring = True
        try:
            root = self._restore_node(tree, shell_texts)
        finally:
            self._restoring = False
        root.parent = None
        self._tree.root = root
        self._content.set_child(self._widget_of(root))
        self._next_hist = max((shell.hist for shell in self.shell_pages()), default=-1) + 1
        rec = self._home_rec()
        if rec is not None:
            for mode, size in self._home_sizes.items():
                rec.sizer.set_remembered(mode, size)
        for pane in self._panes.values():
            pane.sizer.apply()
        for strip in self.strips():
            # Every page a strip was saved with may have been refused by the
            # page factory; the empty husk collapses like a live one would.
            if strip.page_count == 0:
                GLib.idle_add(self._collapse_strip, strip)

    def _restore_node(self, spec: dict, shell_texts: dict[int, str]) -> Leaf | Split:
        """Realize one saved node: leaves become the terminal or a freshly
        populated strip, splits their paned — built bottom-up so
        `_realize_split` finds both children's widgets in place."""
        if "terminal" in spec:
            return Leaf(self._terminal)
        if "strip" in spec:
            state = spec["strip"]
            strip = self._new_strip()
            for page in state["pages"]:
                if page["kind"] == "shell":
                    shell = strip.new_shell(
                        restore_text=shell_texts.get(page["hist"]), select=False
                    )
                    shell.hist = page["hist"]
                else:
                    # Non-shell kinds come back through the injected factory
                    # (see set_page_factory); state it won't trust is dropped,
                    # and a strip that ends up with no pages at all collapses
                    # right after restore (see restore_layout).
                    widget = self._page_factory(page) if self._page_factory else None
                    if widget is not None:
                        strip.add_page(widget, select=False)
            widgets = strip.pages()
            if widgets and 0 <= state["selected"] < len(widgets):
                strip.select_widget(widgets[state["selected"]])
            if state["home"]:
                self._home_strip = strip
            if not state["open"]:
                strip.set_visible(False)
            return Leaf(strip)
        split = Split(
            spec["split"],
            self._restore_node(spec["a"], shell_texts),
            self._restore_node(spec["b"], shell_texts),
        )
        rec = self._realize_split(split, spec["managed"])
        if spec["size"]:
            axis = "bottom" if spec["split"] == "v" else "right"
            rec.sizer.set_remembered(axis, spec["size"])
        return split

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

    def move_page(self, strip, widget, target, position: int | None = None) -> None:
        """Move *widget*'s tab from *strip* into *target* — appending
        unless *position* says where — selecting and focusing it there.
        The source collapses if that emptied it."""
        if target is strip or target not in self._tree or strip not in self._tree:
            return
        strip.transfer_to(widget, target, position)
        target.select_widget(widget)
        GLib.idle_add(widget.grab_page_focus)

    def split_page(self, strip, widget, side: str) -> None:
        """Split *strip*'s own node on *side* and move *widget* into the
        new strip there. Splitting a single-page strip is a relocation:
        the emptied source collapses right after."""
        self.split_move(strip, widget, strip, side)

    def split_move(self, strip, widget, at_leaf, side: str) -> None:
        """Split *at_leaf*'s node on *side* — any leaf, the terminal
        included: edge-docking a drop above the agent is deliberately
        expressible — and move *widget* from *strip* into the new strip
        there."""
        if (
            strip not in self._tree
            or at_leaf not in self._tree
            or side not in ("left", "right", "above", "below")
        ):
            return
        target = self._new_strip()
        self._split_leaf(at_leaf, target, side)
        strip.transfer_to(widget, target)
        rec = self._panes.get(self._tree.find(target).parent)
        if rec is not None:
            rec.sizer.apply()
        target.select_widget(widget)
        GLib.idle_add(widget.grab_page_focus)

    def rotate_page(self, strip, widget) -> None:
        """The tab row's rotate button: move *widget* from *strip* to the
        dock's other axis, and nothing else with it. A tab below the
        terminal lands beside it and back again — of whatever kind, since
        this moves one page rather than gathering the shells the way
        `swap_home` does.

        An existing strip on the destination axis takes the tab as another
        *tab* rather than being split around: rotating three shells over
        one at a time reassembles them in one strip on the far side, not
        three slivers. Only an axis with no strip at all splits the
        terminal to make one.
        """
        if strip not in self._tree or widget not in strip.pages():
            return
        dest = _OTHER_AXIS[self._strip_axis(strip)]
        target = self._axis_strip(dest, exclude=strip)
        was_home = strip is self._home_strip
        if was_home:
            # Fold the home size away before the move can collapse the
            # strip out from under `_home_rec` (see `_collapse_strip`).
            rec = self._home_rec()
            if rec is not None:
                rec.sizer.remember()
                self._home_sizes.update(rec.sizer.snapshot())
        if target is not None:
            self.move_page(strip, widget, target)
            self._reveal_strip(target)  # never rotate into the hidden home
        else:
            self.split_move(strip, widget, self._terminal, _HOME_SIDES[dest])
            target = self._strip_of(widget)
        if was_home and strip.page_count == 0 and target is not None:
            self._adopt_home(target, dest)

    def _adopt_home(self, strip, position: str) -> None:
        """Hand the home role to *strip* on *position*'s axis: the rotation
        emptied the old home strip out, so without this Ctrl+J would go on
        toggling a strip that no longer exists — conjuring a fresh one on
        the old edge while the tab the user just rotated sits on the new
        one. The home strip's remembered size comes along, so a panel
        rotated bottom→right opens at the width it always had."""
        self._home_strip = strip
        self._home_position = position
        rec = self._home_rec()
        if rec is not None:
            for mode, size in self._home_sizes.items():
                rec.sizer.set_remembered(mode, size)
            rec.sizer.apply()
        self.emit("home-changed", position)

    def _strip_axis(self, strip) -> str:
        """Which axis *strip* sits on relative to the terminal (see
        _OTHER_AXIS): the orientation of the split that separates the two.
        Strips nested deeper — a bottom strip the user split left/right —
        answer for the branch they are in, so both halves of a split panel
        rotate to the same place."""
        split = self._tree.separator_of(strip, self._terminal)
        return "bottom" if split.orientation == "v" else "right"

    def _axis_strip(self, axis: str, exclude=None):
        """A strip already on *axis*, or None. The home strip wins when it
        qualifies — a rotation should land in the panel Ctrl+J toggles
        rather than beside it — otherwise the tree's spatial order picks."""
        strips = [
            other
            for other in self.strips()
            if other is not exclude and self._strip_axis(other) == axis
        ]
        if self._home_strip in strips:
            return self._home_strip
        return strips[0] if strips else None

    def _strip_of(self, widget):
        """The strip holding *widget*, or None."""
        return next((strip for strip in self.strips() if widget in strip.pages()), None)

    def begin_page_drag(self, strip, widget) -> None:
        """A page drag started: light the drop zones over every visible
        leaf. The terminal offers its four edges (never center); the
        drag's own strip joins in — center included, that's how tabs
        reorder — only when it holds more than the dragged page, since a
        single page's every drop on its own strip reassembles the same
        layout."""
        model = []
        for leaf in self._tree.leaves():
            if not leaf.get_visible():
                continue  # a hidden home strip has no on-screen edges
            if leaf is self._terminal:
                allowed: tuple = EDGE_ZONES
            elif leaf is strip:
                allowed = EDGE_ZONES + ("center",) if strip.page_count > 1 else ()
            else:
                allowed = EDGE_ZONES + ("center",)
            model.append((leaf, allowed))
        self._zones.begin(model)

    def end_page_drag(self) -> None:
        self._zones.end()

    def _on_zone_drop(self, payload, leaf, zone: str, x: float, y: float) -> None:
        """A page drop landed: an edge splits the target leaf and moves
        the page there; center joins the target strip as a tab at the
        pointer's position — on the page's own strip, that's a reorder."""
        if zone != "center":
            self.split_move(payload.strip, payload.widget, leaf, zone)
            return
        position = paneldnd.insert_position(leaf, self._zones, x)
        if leaf is payload.strip:
            leaf.reorder_to(payload.widget, position)
        else:
            self.move_page(payload.strip, payload.widget, leaf, position)

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
        # Native Adwaita tab DnD: this view accepts drops from this dock's
        # other strips and bounces everything else (tabguard). The dock
        # object is the group key; the fallback conjures a strip for a
        # page whose source strip collapsed while it was being dragged.
        paneldnd.guard_view(strip.tab_view, group=self, fallback=self._bounce_view)
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
        guard.unregister(strip.tab_view)
        if refocus and self._focus_terminal is not None:
            self._focus_terminal()
        return GLib.SOURCE_REMOVE

    def _bounce_view(self):
        """The tab guard's fallback destination for this dock's pages: a
        strip — recreated at home if every strip is gone, as happens when
        a single-page strip's only tab is dragged out and dropped
        somewhere it doesn't belong."""
        strips = self.strips()
        if not strips:
            self._create_home_strip()
            strips = self.strips()
        return strips[0].tab_view

    # -- tree <-> widget mirroring -------------------------------------------

    def _widget_of(self, node: Leaf | Split) -> Gtk.Widget:
        return self._panes[node].paned if isinstance(node, Split) else node.value

    def _place(self, node: Leaf | Split) -> None:
        """Put *node*'s widget into the container slot its tree position
        says it occupies (the dock itself for the root)."""
        widget = self._widget_of(node)
        parent = node.parent
        if parent is None:
            self._content.set_child(widget)
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
            self._content.set_child(None)
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
        new_leaf = split.a if split.a.value is new_widget else split.b
        self._realize_split(split, split.slot_of(new_leaf))
        self._place(split)
        return split

    def _realize_split(self, split: Split, managed_slot: str) -> _PaneRec:
        """Build one split's paned and sizer, with the *managed_slot* child
        the one whose pixel size is remembered (the strip a live split just
        added; the recorded slot when restoring a saved layout). Both
        children's widgets must already be realized."""
        vertical = split.orientation == "v"
        paned = Gtk.Paned(
            orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
            hexpand=True,
            vexpand=True,
        )
        paned.set_wide_handle(True)
        axis = "bottom" if vertical else "right"
        managed = self._widget_of(split.a if managed_slot == "a" else split.b)
        sizer = PanedSizer(
            paned,
            key=lambda a=axis: a,
            occupied=managed.get_visible,
            end_child=managed_slot == "b",
        )
        sizer.set_lookup(self._lookup_size)
        sizer.connect("size-changed", self._on_strip_size_changed)
        rec = _PaneRec(paned, sizer, managed)
        self._panes[split] = rec
        # The terminal's side soaks up window resizes; the strip side keeps
        # its pixel size. A strip-only split gives the stretch to the
        # unmanaged side. Nothing is allowed to shrink away.
        if self._contains_terminal(split.a) or self._contains_terminal(split.b):
            a_resize = self._contains_terminal(split.a)
        else:
            a_resize = managed_slot == "b"
        paned.set_resize_start_child(a_resize)
        paned.set_resize_end_child(not a_resize)
        paned.set_shrink_start_child(False)
        paned.set_shrink_end_child(False)
        paned.set_start_child(self._widget_of(split.a))
        paned.set_end_child(self._widget_of(split.b))
        return rec

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
            self._content.set_child(sibling_widget)
        elif grand.slot_of(sibling) == "a":
            self._panes[grand].paned.set_start_child(sibling_widget)
        else:
            self._panes[grand].paned.set_end_child(sibling_widget)

    # -- sizing --------------------------------------------------------------

    def _lookup_size(self, key: str) -> int:
        return int(self._size_lookup(key) or 0) if self._size_lookup is not None else 0

    def _on_strip_size_changed(self, sizer, key: str, size: int) -> None:
        """Only the home strip's divider updates the app-wide axis seeds —
        satellite strips size themselves without shifting the defaults."""
        rec = self._home_rec()
        if rec is not None and rec.sizer is sizer:
            self.emit("size-changed", key, size)
