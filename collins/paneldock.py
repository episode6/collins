# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The panel dock: the region around the agent terminal, as a split tree.

One `PanelDock` per session tab. It realizes a `docktree.DockTree` whose
leaves are the agent-terminal widget (exactly one, never closable) and
`PanelStrip`s, each split materialized as a fixed-orientation `Gtk.Paned`
with a hardened `PanedSizer`. New layouts come from new splits — nothing
ever flips an existing paned's axis; the old bottom↔right *swap* is now
"move every shell page to a strip at the other home dock".

Opening, swapping and rotating share one rule: **join, don't split**. A
strip already on the axis being aimed at takes the pages as tabs — the
panel a swap sends across, the one tab the rotate button moves — and only
an axis with no strip at all splits the terminal to make one. Five PRs'
worth of docking would otherwise shred the dock into slivers.

Two things still split an occupied axis, and only where the split is
free: a terminal already wider than its maximum width (`_split_is_free`)
is sitting on gutter it will never use, so the column comes out of that
rather than out of the terminal. The moment the gutter is spent the rule
above takes over again. Opening a docked page is one (`open_page`); the
other is Ctrl+J.

**Ctrl+J is bound to a terminal, not to a strip.** It owns one shell page
— `panel_terminal`, the first one opened in the session and, once that
closes, the next — and shows or hides *that*, wherever it happens to
live. A terminal alone in its strip hides the strip around it, node and
size intact; one sharing a tab row is stowed off screen on its own (see
`_stow`), so the pages beside it stay exactly where they are. A new one
keeps to its own company where it can — the shell strip already on the
home axis, or a fresh column when the width is free. Only when the panel
opens right, the width is spoken for, and a docked page already holds
that edge does it take a seat in that page's tab row (`_panel_target`):
joined, never adopted, so the shortcut still speaks for its terminal
alone, and the next hide stows just that page.

One page at a time can step out of that tree entirely: the tab row's
overlay button *maximizes* it, floating it over the whole session tab —
terminal, every other strip, and the editor column beside the dock — until
the restore button at its top-left (or Escape) drops it back into the tab
row it came from. It is still one of its strip's pages while it is up
there (see `_strip_pages`): its scrollback saves, its running command
blocks a close, and the strip it left stays in the tree however empty,
because "restore it to where it was" has to have a where.

A maximized page also owns the keyboard for as long as it is up: the pane
hides everything the overlay covers, so a keystroke that reached anything
under it would be typed into a window the user cannot see — most of all
the agent's own terminal. The focus trap (`_on_root_focus_changed`) sends
any focus that lands behind the overlay straight back to the page.

The *home strip* is where the shells live: it sits on the home edge of
the terminal (`home_position`, "bottom" | "right"), can be hidden without
closing (pages keep running), and is recreated on demand after it
collapses. It is also the divider the app-wide "home" size seed speaks
for. Any strip whose last page closes collapses — its node is removed and
the sibling promotes — so trees only get as deep as the pages the user
actually keeps.
"""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, GLib, GObject, Gtk, Pango  # noqa: E402

from . import paneldnd, panelkeys, panelsizing  # noqa: E402
from .docktree import DockTree, Leaf, Split  # noqa: E402
from .dockzones import EDGE_ZONES  # noqa: E402
from .i18n import _  # noqa: E402
from .panedsizer import PanedSizer  # noqa: E402
from .tabguard import guard  # noqa: E402

# The tree side a home strip splits off the terminal, per home position.
_HOME_SIDES = {"bottom": "below", "right": "right"}
# What the rotate button flips: an axis, not a side. A strip divided from
# the terminal vertically is on the "bottom" axis whether it sits above or
# below it; horizontally, on the "right" axis whether left or right.
_OTHER_AXIS = {"bottom": "right", "right": "bottom"}


class _MaxPane(Gtk.Box):
    """Where a maximized page lives: a bare `Adw.TabView` of its own under
    a thin bar whose left end carries the button that puts the page back.

    A view rather than a hand-reparented widget, because that is what lets
    `Adw.TabView.transfer_page` move the page here and home again — the
    same reparenting-without-destroying a move between strips uses, so a
    shell's process never notices it left its strip. No tab bar: exactly
    one page is ever in here, and the bar above already names it.

    The pane floats in an overlay the session tab hands the dock
    (`set_maximize_host`), not in the dock's split tree, so what it covers
    is the whole tab rather than the dock's share of it."""

    def __init__(self, on_restore) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, visible=False)
        self.add_css_class("panel-maximized")
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.add_css_class("panel-maximized-bar")
        restore = Gtk.Button(icon_name="view-restore-symbolic")
        restore.add_css_class("flat")
        restore.set_tooltip_text(_("Restore this tab to its size and place in the panel"))
        restore.connect("clicked", lambda *_: on_restore())
        bar.append(restore)
        self._title = Gtk.Label(xalign=0.0, ellipsize=Pango.EllipsizeMode.END)
        self._title.add_css_class("heading")
        bar.append(self._title)
        self._view = Adw.TabView(vexpand=True)
        self.append(bar)
        self.append(self._view)

    @property
    def tab_view(self) -> Adw.TabView:
        """The view a page is transferred into — the `tab_view` any
        transfer target offers (see PanelStrip.transfer_to)."""
        return self._view

    def set_page_title(self, title: str) -> None:
        self._title.set_label(title)


class _StowPane(Gtk.Box):
    """Where Ctrl+J's terminal waits while it is off screen: a bare
    `Adw.TabView` in a box that is never shown.

    Same trick as `_MaxPane` and for the same reason — `transfer_page`
    moves the page out of its strip without destroying it, so the shell's
    process never notices it left — but with nothing to look at: this is
    the hidden half of a toggle, not a way of showing a page. Only a
    terminal sharing its strip with other pages comes here; one alone in
    its strip is hidden by hiding the strip."""

    def __init__(self) -> None:
        super().__init__(visible=False)
        self._view = Adw.TabView()
        self.append(self._view)

    @property
    def tab_view(self) -> Adw.TabView:
        """The view a page is transferred into (see PanelStrip.transfer_to)."""
        return self._view


class _LiftRec:
    """A page lifted out of its strip — maximized over the tab, or stowed
    off screen by Ctrl+J: which strip it came out of, and the tab position
    it drops back into. `handlers` are the page signals rewired to the dock
    for as long as no strip is carrying them."""

    def __init__(self, strip, widget, position: int) -> None:
        self.strip = strip
        self.widget = widget
        self.position = position
        self.handlers: list[int] = []


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
        # The user resized one of the two strips whose size is an app-wide
        # default: (scope, mode, px), scope "home" for the shells' panel and
        # "page" for the strip docked pages open into. The window persists
        # each under its own setting (see state.panel_size_key).
        "size-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, str, int)),
        # The home strip moved to the other axis under a rotation: (mode) —
        # the app-wide panel_position default follows it, as it followed the
        # bottom/right swap this button used to fire.
        "home-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, terminal: Gtk.Widget, strip_factory, home_position: str) -> None:
        """*terminal* is the agent-terminal widget (the dock's fixed leaf);
        `strip_factory() -> PanelStrip` builds a strip wired for shells;
        *home_position* seeds which edge the shells open on."""
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
        self._size_lookup = None  # (scope, axis) -> app-wide px seed, set by the window
        self._focus_terminal = None  # () -> None, grabs the agent VTE
        self._page_factory = None  # (page_dict) -> PanelPage | None, for restore
        self._ever_spawned = False  # any shell ever ran in this dock
        self._next_shell = 1
        self._next_hist = 0  # next shell's persistent panel-history ordinal
        self._restoring = False  # restore_layout is rebuilding the tree
        # The shell page Ctrl+J shows and hides (see `panel_terminal`), and
        # the record of where it came out of while it waits off screen in
        # the stow pane. The pane hangs off the dock's own overlay so the
        # page it holds keeps a parent — and its shell a process — however
        # long it stays hidden; it is never made visible.
        self._toggle_shell = None
        self._stowed: _LiftRec | None = None
        self._stow_pane = _StowPane()
        # The page Ctrl+; rotates when the focus has moved on to the agent
        # terminal: the last one added to a strip or brought to its front
        # (see _recent_page).
        self._recent = None
        # The tree's widgets live in a content bin under a dock-wide
        # overlay; the drop zones ride the overlay so a page drag can
        # target every leaf's edges at once (paneldnd.DropZones).
        self._content = Adw.Bin(child=terminal)
        self._zones = paneldnd.DropZones(self._on_zone_drop)
        overlay = Gtk.Overlay(child=self._content)
        overlay.add_overlay(self._zones)
        overlay.add_overlay(self._stow_pane)
        self.set_child(overlay)
        # The maximized page and the record of where it came from. The pane
        # starts in the dock's own overlay so a dock nobody hands a wider
        # one to still works; the session tab swaps in its own right after
        # construction, which is what makes the overlay cover the editor
        # column too (see set_maximize_host).
        self._max: _LiftRec | None = None
        self._max_pane = _MaxPane(self.restore_maximized)
        self._max_host = overlay
        overlay.add_overlay(self._max_pane)
        # Escape puts a maximized page back down. CAPTURE, so it is decided
        # before the page's own widgets see it — a maximized shell's VTE
        # consumes every key it is given, and a bubbling Escape would never
        # reach here (see `_on_max_key` for what a shell keeps).
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_max_key)
        self._max_pane.add_controller(keys)
        # The focus trap that keeps the keyboard inside a maximized page:
        # the window whose focus it watches while one is up, the handler on
        # it, and the re-entrancy latch its own grab trips.
        self._focus_root: Gtk.Window | None = None
        self._focus_handler = 0
        self._refocusing = False
        # The guard group for this dock's strip views dies with the dock.
        self.connect("destroy", self._on_destroy)

    # -- wiring ------------------------------------------------------------

    def set_size_lookup(self, lookup) -> None:
        """`lookup(scope, mode) -> px` supplies the app-wide last-set strip
        size for a scope ("home"/"page") on an axis ("bottom"/"right"),
        seeding splits this dock hasn't sized yet."""
        self._size_lookup = lookup

    def set_focus_terminal(self, grab) -> None:
        """`grab()` lands the cursor in the agent terminal — called when a
        strip that held focus hides or collapses."""
        self._focus_terminal = grab

    def set_maximize_host(self, overlay: Gtk.Overlay) -> None:
        """Adopt *overlay* as the layer a maximized page floats in. The
        session tab passes its own — the one wrapping the dock *and* the
        editor column — so the overlay button covers everything in the tab
        rather than only the dock's share of it. Without a call here the
        dock's own overlay serves, which covers the split tree alone."""
        if overlay is self._max_host:
            return
        self._max_host.remove_overlay(self._max_pane)
        self._max_host = overlay
        overlay.add_overlay(self._max_pane)

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
        for rec in (self._max, self._stowed):
            # A lifted page is out of every strip's fan-out and would
            # otherwise be the one page a font change misses.
            if rec is not None:
                rec.widget.apply_settings(settings)

    # -- queries -----------------------------------------------------------

    @property
    def ever_spawned(self) -> bool:
        """A shell ran in some strip at some point in this tab's life."""
        return self._ever_spawned

    @property
    def home_position(self) -> str:
        return self._home_position

    @property
    def panel_terminal(self):
        """The one shell page Ctrl+J shows and hides, or None when it has
        none: nothing has ever been opened here, or the page it was bound
        to has closed. The binding is checked here rather than watched —
        a page leaves a strip for a transfer as readily as for a close, and
        "is it still somewhere in this dock" is the only form of the
        question with one answer. Once it is free, the next shell to turn
        up anywhere in the dock takes it (see `_on_page_touched`)."""
        shell = self._toggle_shell
        if shell is None:
            return None
        for rec in (self._max, self._stowed):
            if rec is not None and rec.widget is shell:
                return shell  # lifted out of every strip, but still ours
        if self._strip_of(shell) is None:
            self._toggle_shell = None
            return None
        return shell

    @property
    def panel_terminal_showing(self) -> bool:
        """Whether the panel terminal is on screen — the state Ctrl+J
        toggles. False when it is stowed, when the strip holding it is
        hidden, and when there is no panel terminal at all.

        Also false when it is a *background* tab of a strip that is up: the
        row is showing something else, and a shortcut whose whole job is
        "let me see my terminal" has to front it before it may hide it.
        The second press then hides it, as always."""
        shell = self.panel_terminal
        if shell is None or self._stowed is not None:
            return False
        if self._max is not None and self._max.widget is shell:
            return True
        strip = self._strip_of(shell)
        return (
            strip is not None
            and strip.get_visible()
            and strip.selected_page_widget() is shell
        )

    def strips(self) -> list:
        """Every strip, in the tree's spatial order."""
        return [leaf for leaf in self._tree.leaves() if leaf is not self._terminal]

    def shell_pages(self) -> list:
        """Every shell page across every strip, in spatial-then-tab order —
        the stable order panel history is captured in. A maximized shell is
        one of them: it is still its strip's page (see `_strip_pages`), and
        dropping it here would lose its scrollback to the history prune and
        let a close skip its running command."""
        return [page for page in self.pages() if getattr(page, "page_kind", None) == "shell"]

    def _strip_pages(self, strip) -> list:
        """*strip*'s pages, including any lifted out of it — the maximized
        one, the terminal Ctrl+J stowed — back at the position it will
        return to, so every count, capture and serialization of a strip
        reads the same whether its tab is up over the session, waiting off
        screen, or sitting in its row.

        A stowed terminal therefore persists as the tab of this strip it
        will be again: a saved layout has one hidden state per *strip*, and
        one page of a shared row being off screen isn't it. It comes back
        with the session showing, which is the honest half-truth — the
        shell, its scrollback and its place in the row are all where the
        user left them."""
        pages = strip.pages()
        for rec in (self._max, self._stowed):
            if rec is not None and rec.strip is strip:
                pages.insert(min(rec.position, len(pages)), rec.widget)
        return pages

    def _strip_selected(self, strip):
        """The page *strip* is showing — the maximized one when that came
        from here, since that is the tab it will be showing again."""
        rec = self._max
        if rec is not None and rec.strip is strip:
            return rec.widget
        return strip.selected_page_widget()

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

    # -- the panel terminal (Ctrl+J) ----------------------------------------

    def show_panel_terminal(
        self, restore_texts: list[str] | None = None, focus: bool = True
    ) -> None:
        """Put Ctrl+J's terminal on screen: the page it is bound to, back
        from wherever it was hidden, or a fresh shell when it has none —
        which then becomes the binding. `restore_texts` recreates one shell
        per saved panel history the first time a session's panel opens; the
        first of them takes the binding.

        A terminal already open is re-pointed at the agent's cwd on the way
        up, since the agent may have moved into a worktree while it was
        hidden.

        Where a *new* one opens is `_panel_target`'s call: a column of its
        own out of the gutter when the terminal has width to spare
        (`_split_is_free`, exactly the spare width `open_page` yields to),
        else the shell strip already on the home axis, else — the panel
        set to open right with a docked page holding that edge and no
        width free for another column — a seat in that page's tab row.
        The seat is joined, never adopted: a shortcut that hides what it
        shows has no business making a home of someone else's panel, and
        hiding from a shared row stows the terminal alone (`_stow`), so
        the page beside it keeps its row either way.

        *focus* False keeps every selection along the way quiet — a show
        that isn't the user's own keystroke (session restore, the
        run_in_terminal tool) must not move the keyboard."""
        # No strip is visible under a maximized page: the tab comes back to
        # its row first, so what this shows is something the user can see.
        self.restore_maximized()
        shell = self.panel_terminal
        if shell is None:
            self._open_panel_terminal(restore_texts, focus=focus)
            return
        if self._stowed is not None:
            self._unstow(focus=focus)
            return
        strip = self._strip_of(shell)
        self._reveal_strip(strip)
        strip.refresh_shell(shell)
        strip.select_widget(shell, focus=focus)
        # The tab Ctrl+J just put on screen is the freshest one there is, so
        # Ctrl+; rotates it. Without this the remembered page would still be
        # whatever was fronted last — a PR tab in another strip, say, which
        # the keyboard would then rotate instead of the terminal the user
        # was looking at (see _recent_page).
        self._recent = shell

    def hide_panel_terminal(self) -> None:
        """Take Ctrl+J's terminal off screen without closing it — its shell
        keeps running either way.

        Alone in its strip, it hides the strip around it: the node and its
        size survive for the next show, as they always did. Sharing a tab
        row, it is stowed on its own (`_stow`) and the row stays up — the
        PR page or composer beside it is not what the user asked to hide."""
        self.restore_maximized()  # a page up over the tab is in no strip to hide
        shell = self.panel_terminal
        if shell is None or self._stowed is not None:
            return
        strip = self._strip_of(shell)
        if strip is None or not strip.get_visible():
            return
        refocus = strip.has_page_focus()
        if strip.page_count > 1:
            self._stow(shell, strip)
        else:
            self._hide_strip(strip)
        if refocus and self._focus_terminal is not None:
            self._focus_terminal()

    def focus_panel_terminal(self) -> None:
        shell = self.panel_terminal
        if shell is not None and self._stowed is None:
            shell.grab_page_focus()

    def _open_panel_terminal(
        self, restore_texts: list[str] | None = None, focus: bool = True
    ) -> None:
        """Spawn the terminal Ctrl+J binds to, in the strip `_panel_target`
        nominates. Saved panel history opens as one shell per file; the
        oldest is the one the shortcut keeps."""
        strip = self._panel_target()
        shells = strip.new_shells(restore_texts, focus=focus)
        if not shells:
            return
        # The arrival has almost certainly bound this already (a free
        # binding takes the first shell to turn up — see `_on_page_touched`,
        # which runs synchronously inside `new_shells`). Said outright
        # anyway, because "the shortcut owns what the shortcut just opened"
        # is this method's whole job, and it shouldn't rest on the order two
        # signals happen to fire in.
        self._toggle_shell = shells[0]
        self._recent = shells[0]

    def _panel_target(self):
        """The strip Ctrl+J's terminal opens in: a column split off the
        terminal whenever that width is free, else a strip on the home
        axis that is already showing shells, else a new strip on the home
        edge — unless that edge is "right" and a docked page already holds
        it. There a second column would squeeze the terminal below its
        maximum width for an edge that already has a panel, so the shell
        takes a seat in the docked page's tab row instead: joined, never
        adopted (see `show_panel_terminal`), leaving the home role — and
        the sizes it speaks for — untouched."""
        axis = self._home_position
        if axis == "right" and self._split_is_free("home"):
            return self._create_home_strip()
        target = next(
            (
                strip
                for strip in self.strips()
                if strip.shell_pages() and self._strip_axis(strip) == axis
            ),
            None,
        )
        if target is not None:
            if target is not self._home_strip:
                self._adopt_home(target, axis)
            return target
        if axis == "right":
            # The width is not free — the split at the top would have taken
            # it — so a new column here comes out of the terminal itself.
            # An edge the user has split into several strips seats the
            # shell in the first by `strips()`' spatial order — the
            # topmost, deliberately: it reads as "the" panel on that edge.
            docked = next(
                (
                    strip
                    for strip in self.strips()
                    if strip.get_visible() and self._strip_axis(strip) == axis
                ),
                None,
            )
            if docked is not None:
                return docked
        return self._create_home_strip()

    def _stow(self, shell, strip) -> None:
        """Lift the panel terminal out of a strip it shares with other
        pages: off screen, still running, remembering the tab position it
        drops back into. Hiding the strip around it would take those other
        pages down with it — the whole reason Ctrl+J is bound to a terminal
        rather than to a strip."""
        position = strip.pages().index(shell)
        self._stowed = _LiftRec(strip, shell, position)  # before the transfer
        strip.transfer_to(shell, self._stow_pane, 0)
        self._wire_stowed(shell)

    def _unstow(self, focus: bool = True) -> None:
        """Drop the stowed terminal back into its tab row, selected: the
        strip it left when that is still in the tree, else wherever a fresh
        one would open. (Emptying the strip around a stowed page hides it
        rather than collapsing it — see `_collapse_strip` — so the fallback
        is for a collapse that raced this.) *focus* False keeps the
        keyboard where it is, like `select_widget`'s."""
        rec = self._stowed
        if rec is None:
            return
        self._stowed = None
        for handler in rec.handlers:
            rec.widget.disconnect(handler)
        rec.handlers.clear()
        view = self._stow_pane.tab_view
        page = view.get_nth_page(0) if view.get_n_pages() else None
        if page is None:
            self._toggle_shell = None  # gone from under us; the binding is free
            return
        strip = rec.strip if rec.strip in self._tree else self._panel_target()
        target = strip.tab_view
        view.transfer_page(page, target, min(rec.position, target.get_n_pages()))
        self._reveal_strip(strip)
        strip.refresh_shell(rec.widget)
        strip.select_widget(rec.widget, focus=focus)
        self._recent = rec.widget

    def _wire_stowed(self, widget) -> None:
        """Carry the stowed page's signals while it is out of every strip,
        the way `_wire_max_page` does for a maximized one: a shell that
        exits off screen still has to be noticed, and a bell it rings is
        still the session tab's bell."""
        rec = self._stowed
        if GObject.signal_lookup("shell-exited", widget.__gtype__):
            rec.handlers.append(widget.connect("shell-exited", self._on_stowed_shell_exited))
        if GObject.signal_lookup("bell", widget.__gtype__):
            rec.handlers.append(widget.connect("bell", lambda *_: self.emit("bell")))

    def _on_stowed_shell_exited(self, shell) -> None:
        """A stowed shell ran out — a long command that ended in `exit`, or
        a login shell timing out. Its page goes with it (there is no tab row
        to leave a dead screen in), and Ctrl+J's binding is free for the
        next terminal opened."""
        rec = self._stowed
        if rec is None or rec.widget is not shell:
            return
        self._stowed = None
        for handler in rec.handlers:
            shell.disconnect(handler)
        rec.handlers.clear()
        if self._toggle_shell is shell:
            self._toggle_shell = None
        closed = getattr(shell, "page_closed", None)
        if closed is not None:
            closed()
        view = self._stow_pane.tab_view
        page = view.get_nth_page(0) if view.get_n_pages() else None
        if page is not None:
            view.close_page(page)

    def _hide_strip(self, strip) -> None:
        """Hide a strip whole, remembering its size on the way down: the
        divider parks somewhere meaningless once the child it sizes is
        gone (see PanedSizer.occupied)."""
        rec = self._rec_for(strip)
        if rec is not None:
            rec.sizer.remember()
            if strip is self._home_strip:
                self._home_sizes.update(rec.sizer.snapshot())
        strip.set_visible(False)

    def set_home_position(self, mode: str) -> None:
        """Re-home a (typically hidden) strip: session restore applying the
        saved mode before showing anything."""
        if mode not in _HOME_SIDES or mode == self._home_position:
            return
        self._home_position = mode
        if self._home_strip is not None:
            self._relocate_home()

    def swap_home(self) -> str:
        """The swap action's new meaning: flip the home position and put
        the panel there — the shells as a group (win.swap-panel), where
        `rotate_page` moves a single tab of any kind.

        A strip already on the destination axis takes the panel's pages as
        tabs and becomes the new home, the same join-don't-split rule
        `rotate_page` follows: the swap should land the shells *in* the PR
        strip already beside the terminal rather than carving a second
        column next to it. With that side empty the home strip relocates
        there bodily, as it always did. Either way the shell pages parked
        in other strips gather back in. Returns the new position."""
        self.restore_maximized()  # a page floating over the tab can't be gathered
        self._home_position = "right" if self._home_position == "bottom" else "bottom"
        home = self._home_strip
        target = self._axis_strip(self._home_position, exclude=home)
        if home is not None and target is not None:
            self._merge_home_into(home, target)
        elif home is not None:
            self._relocate_home()
        elif target is not None:
            # No panel of its own, but a strip already sits where one would
            # go: adopt it rather than splitting the terminal beside it.
            self._adopt_home(target, self._home_position)
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

    def _merge_home_into(self, home, target) -> None:
        """Empty the home strip into *target*, which inherits the home
        role: the swap's join-don't-split half. Every page moves, not just
        the shells — the panel arrives whole, still showing the tab it was
        showing — and the emptied strip collapses behind it."""
        rec = self._home_rec()
        if rec is not None:
            # Fold the old edge's size away before the strip collapses out
            # from under `_home_rec`, so swapping back restores it.
            rec.sizer.remember()
            self._home_sizes.update(rec.sizer.snapshot())
        if self._stowed is not None and self._stowed.strip is home:
            # The panel terminal is off screen but it is one of the shells
            # this swap is gathering: it comes along by re-pointing the row
            # it drops back into, since a page in no strip can't be moved
            # between them. Without this it would wait on the old edge for
            # a strip that is about to collapse.
            self._stowed.strip = target
            self._stowed.position = target.page_count
        refocus = home.has_page_focus()
        selected = home.selected_page_widget()
        for widget in home.panel_pages():
            home.transfer_to(widget, target)
        if selected is not None:
            target.select_widget(selected)
        self._adopt_home(target, self._home_position)
        if refocus:
            GLib.idle_add(target.grab_page_focus)

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

    def _rec_for(self, strip) -> _PaneRec | None:
        """The record of the paned dividing *strip*'s branch from the
        terminal's — the divider whose position *is* that strip's size.
        Not simply the leaf's parent: splitting a tab inside the strip
        inserts new splits between the leaf and that divider, so it is the
        split *separating* the two (see `DockTree.separator_of`)."""
        if strip is None:
            return None
        try:
            split = self._tree.separator_of(strip, self._terminal)
        except ValueError:
            return None  # the strip left the tree (a collapse in flight)
        return self._panes.get(split)

    def _home_rec(self) -> _PaneRec | None:
        """The home strip's divider — the one the app-wide "home" size seed
        speaks for (see `_scope_of`)."""
        return self._rec_for(self._home_strip)

    def _stash_home_size(self) -> None:
        """Fold the live home divider's position into the remembered set,
        for a home strip about to stop being one (collapsed, merged away,
        or replaced by a fresh column): `_home_rec` answers for whichever
        strip holds the role, so the old one's size has to be taken while
        it still does."""
        rec = self._home_rec()
        if rec is not None:
            rec.sizer.remember()
            self._home_sizes.update(rec.sizer.snapshot())

    def _create_home_strip(self):
        """Split a fresh strip off the terminal's home edge and give it the
        home role, at the size the last home strip was left at. Returns it."""
        self._stash_home_size()
        strip = self._new_strip()
        self._home_strip = strip
        self._split_leaf(self._terminal, strip, _HOME_SIDES[self._home_position])
        rec = self._home_rec()
        for mode, size in self._home_sizes.items():
            rec.sizer.set_remembered(mode, size)
        rec.sizer.apply()
        return strip

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
        """Every page across every strip, in spatial-then-tab order, a
        lifted one among its strip's (see `_strip_pages`). A stowed
        terminal whose strip has left the tree brings up the rear rather
        than dropping out: its shell is running, and a page nothing counts
        is a page whose scrollback isn't saved and whose live command
        doesn't block a close."""
        pages = [page for strip in self.strips() for page in self._strip_pages(strip)]
        rec = self._stowed
        if rec is not None and rec.widget not in pages:
            pages.append(rec.widget)
        return pages

    def open_page(self, widget, side: str = "right", focus: bool = True) -> None:
        """Open a non-shell page as a tab in a strip beside the terminal:
        the first strip on *side* of it ("right" | "below"), else a new one
        split off that edge. The join-don't-split default keeps opening
        five PRs from carving the dock into five slivers — and lands the
        composer in a bottom home strip as a tab rather than beneath it.

        *focus* False leaves the keyboard where it is, for a page that opens
        without being asked for: nobody typing at an agent should have the
        next word land in a panel that appeared by itself.

        Joining yields to spare *width*, though: on a screen wide enough
        that the terminal has stopped growing (see `_split_is_free`), a
        page opening to the right gets a column of its own rather than a
        tab in the one already there, since the room for it is gutter the
        terminal wasn't using. That's the whole point of the maximum
        width — the pixels past it are there to be spent — and one page
        per column beats two stacked in a tab row whenever nothing has to
        give them up. Once the free width is gone the next page joins as
        before, so the dock still can't be shredded into slivers.

        A right-docked *home* strip is one of the strips this steps past,
        deliberately: with the room to spare, a pull request opens beside
        the shells rather than in their tab row, and the shells' panel is
        left exactly as it was — the new column comes out of the terminal's
        branch, so the home divider (`_home_rec`, the split *separating*
        the two) is still the one that sizes it."""
        self.restore_maximized(focus=focus)  # a page opening behind the overlay is a page lost
        if side not in ("right", "below"):
            side = "right"
        # Suppressing the strip's own grab is not enough to leave the cursor
        # alone: the split itself moves the focus (see _quiet_focus), so where
        # it was has to be noted here and put back on the other side.
        root = None if focus else self.get_root()
        keep = root.get_focus() if root is not None else None
        strip = self._strip_past_terminal("h" if side == "right" else "v")
        minimum = int(getattr(widget, "column_floor", 0) or 0)
        if strip is not None and side == "right" and self._split_is_free(minimum=minimum):
            strip = None
        if strip is None:
            strip = self._new_strip()
            split = self._split_leaf(self._terminal, strip, side)
            # The page goes in before the sizer is asked: its floor is read
            # off the strip's pages when the apply lands.
            strip.add_page(widget, focus=focus)
            self._panes[split].sizer.apply()
        else:
            strip.add_page(widget, focus=focus)
        self._reveal_strip(strip)
        if focus:
            GLib.idle_add(widget.grab_page_focus)
        elif root is not None:
            GLib.idle_add(self._quiet_focus, root, keep, widget)

    @staticmethod
    def _quiet_focus(root, keep, opened) -> bool:
        """Leave the keyboard exactly where the quiet open found it —
        including nowhere.

        Both halves are needed. A window that *had* a focus loses it to the
        split: the terminal is unparented to be re-placed under the new
        paned, an unparented widget takes the keyboard with it, and GTK
        hands the focus on to the first thing it finds — the page that has
        just appeared. And a window that had *no* focus is handed the same
        page for the same reason, which is how a panel nobody asked for
        ends up owning the keyboard in a window where nothing did; so the
        focus is dropped again rather than left in it.

        Only ever *out of* the new page: a focus that landed anywhere else
        in the meantime is somebody's own doing and is left alone, as is a
        remembered widget that has since left this window."""
        if keep is not None:
            if keep.get_root() is root:
                keep.grab_focus()
            return GLib.SOURCE_REMOVE
        landed = root.get_focus()
        if landed is not None and (landed is opened or landed.is_ancestor(opened)):
            root.set_focus(None)
        return GLib.SOURCE_REMOVE

    def room_for_a_column(self) -> bool:
        """Whether a page opening on the right would get a column of its own
        rather than a tab in the strip already there — `_split_is_free`, for
        a caller that has to decide *whether* to open a page at all.

        A panel that opens by itself is welcome where it costs the terminal
        nothing and an imposition where it doesn't, so "is there room?" is
        the same question `open_page` asks itself a moment later, asked one
        step earlier."""
        return self._split_is_free()

    def close_page(self, widget) -> None:
        """Close *widget*'s tab wherever it lives, through the strip's own
        close funnel (busy-ask, page_closed hook, collapse-when-empty) —
        how the docked composer's chrome close reaches its tab's X.

        A *maximized* widget comes down first: it has no strip to be closed
        from while it is up, and its own chrome's close button is one of
        the ways here. Closing anything else leaves the overlay alone —
        that page is out of sight behind it either way, and dropping the
        overlay for it would be a side effect no caller asked for.

        A *stowed* terminal comes back for the same reason: closing it has
        to go through its strip's funnel, busy-ask and all, and it has no
        strip while it waits off screen."""
        if self._max is not None and self._max.widget is widget:
            self.restore_maximized()
        if self._stowed is not None and self._stowed.widget is widget:
            self._unstow()
        strip = self._strip_of(widget)
        if strip is not None:
            strip.close_widget(widget)

    def reveal_page(self, widget, focus: bool = True) -> None:
        """Front an existing page: select its tab, and show its strip if
        that was hidden. A page already maximized is as fronted as a page
        gets; any *other* one comes down first, since nothing behind the
        overlay can be shown. The stowed terminal comes back to its row —
        it is off screen, not merely behind something.

        *focus* False fronts the page without handing it the keyboard —
        the same bargain as `open_page`'s, for a page revealed without
        being asked for."""
        if self._max is not None and self._max.widget is widget:
            if focus:
                GLib.idle_add(widget.grab_page_focus)
            return
        self.restore_maximized(focus=focus)
        if self._stowed is not None and self._stowed.widget is widget:
            self._unstow(focus=focus)
            if focus:
                GLib.idle_add(widget.grab_page_focus)
            return
        for strip in self.strips():
            if widget in strip.pages():
                strip.select_widget(widget, focus=focus)
                self._reveal_strip(strip)
                if focus:
                    GLib.idle_add(widget.grab_page_focus)
                return

    def _reveal_strip(self, strip) -> None:
        """Show a strip that was hidden — the panel terminal's own is the
        only kind that ever is — re-applying the size its divider parked at
        on the way down."""
        if strip is None or strip.get_visible():
            return
        rec = self._rec_for(strip)
        strip.set_visible(True)
        if rec is not None:
            rec.sizer.apply()

    def _strip_past_terminal(self, orientation: str):
        """The first strip in the subtree right of ("h") or below ("v") the
        terminal, or None — `DockTree.first_beyond`, which can only yield
        strips: the terminal itself is in the near branch of any split the
        walk answers from."""
        return self._tree.first_beyond(self._terminal, orientation)

    # -- shells across strips ----------------------------------------------

    def select_busy_shell(self) -> None:
        """Front the first busy shell — bringing it back from a hidden
        strip or the stow pane if that's where it is — so a close
        confirmation's "will be terminated" points at something visible."""
        if self._max is not None and getattr(self._max.widget, "page_busy", bool)():
            return  # already the only thing on screen
        self.restore_maximized()  # nothing behind the overlay can be pointed at
        rec = self._stowed
        if rec is not None and rec.widget.page_busy():
            self._unstow()
            return
        for strip in self.strips():
            if any(shell.page_busy() for shell in strip.shell_pages()):
                self._reveal_strip(strip)
                strip.select_busy_page()
                return

    def open_shell_page(self):
        """Append a fresh shell tab beside the last shell page — the
        + button's move, made for run_in_terminal when every open shell is
        busy. Returns the new shell unselected (the caller reveals it), or
        None when there is no strip to sit beside — no shells at all, which
        is `show_panel_terminal`'s case, not this one."""
        for shell in reversed(self.shell_pages()):
            strip = self._strip_of(shell)  # skips a lifted page's shell
            if strip is not None:
                return strip.new_shell(select=False)
        return None

    def capture_shell_texts(self) -> dict[int, str]:
        """Every shell page's scrollback under its persistent history
        ordinal — the explicit keep-set panelhistory.save_all prunes stale
        files against."""
        return {shell.hist: shell.capture_contents() for shell in self.shell_pages()}

    def clear_shells(self) -> None:
        for shell in self.shell_pages():  # a maximized one included
            shell.clear()

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
        that opted out) simply doesn't persist.

        A maximized page persists as the tab of this strip it will be
        again, selected: maximizing is a way of looking at a page, not a
        place a page lives, and nothing about it is worth restoring a
        session into."""
        pairs = [
            (widget, state)
            for widget in self._strip_pages(strip)
            if (state := getattr(widget, "page_state", lambda: None)())
        ]
        if not pairs:
            return None
        selected_widget = self._strip_selected(strip)
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

        Rotating the home strip's last *shell* away hands the home role
        after it (see `_adopt_home`): the role names the divider the
        app-wide panel size speaks for, so it follows the shells rather
        than staying with an edge they have left. What Ctrl+J shows and
        hides needs no help here — it is bound to the terminal itself, and
        a rotation carries the binding along with the page.
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
        # The home role follows an emptied strip, as it always did, and now
        # also follows the last shell out of one that keeps other tabs.
        took_last_shell = (
            getattr(widget, "page_kind", None) == "shell" and not strip.shell_pages()
        )
        if was_home and target is not None and (strip.page_count == 0 or took_last_shell):
            self._adopt_home(target, dest)

    # -- maximizing a page ---------------------------------------------------

    @property
    def maximized_page(self):
        """The page floating over the session tab, or None."""
        return self._max.widget if self._max is not None else None

    def maximize_page(self, strip, widget) -> None:
        """The tab row's overlay button: float *widget* over the whole
        session tab — terminal, other strips and the editor column alike —
        with only the maximize pane's own restore button showing.

        The page is *transferred* into that pane's view rather than
        reparented by hand, so it keeps its `Adw.TabPage` and a shell's
        process rides along exactly as it does across a move between
        strips. Its old tab position is remembered and its strip is kept
        alive even when this emptied it (see `_collapse_strip`): "restore
        it to its normal size and location" needs a location to return to.

        Only one page can be up at a time — a second call restores the
        first, though with every strip covered there is no button left to
        make one."""
        if strip not in self._tree or widget not in strip.pages():
            return
        self.restore_maximized()
        position = strip.pages().index(widget)
        self._max = _LiftRec(strip, widget, position)  # before the transfer can empty strip
        strip.transfer_to(widget, self._max_pane, 0)
        self._wire_max_page(widget)
        self._max_pane.set_page_title(widget.page_title())
        self._max_pane.set_visible(True)
        self._arm_focus_trap()
        GLib.idle_add(widget.grab_page_focus)

    def restore_maximized(self, focus: bool = True) -> bool:
        """Drop the maximized page back into its own tab row, at the
        position it left from, selected and focused. False when nothing was
        up — every dock action that would otherwise happen unseen behind
        the overlay calls this first, and most of the time it does nothing.

        *focus* False restores the page without *following* it with the
        keyboard, for the quiet paths (`open_page` / `reveal_page` with
        their own *focus* False): what brought the overlay down there was
        nobody's click, so the cursor stays wherever it is. Wherever it
        is can be the maximized page itself — the focus trap keeps it
        there while one is up — and the transfer would drop it on the
        floor, so a page that held the keyboard is re-granted it even on
        a quiet restore: that is keeping, not moving.

        A strip that left the tree while its page was up (nothing does that
        today, but a collapse racing an idle would) sends the page to the
        tab guard's fallback strip rather than nowhere."""
        rec = self._max
        if rec is None:
            return False
        if not focus:
            # Asked before the transfer below unparents the page and takes
            # the answer with it. getattr, like the strip's aggregate: not
            # every page kind reports focus, and one that can't counts as
            # not holding it.
            holds = getattr(rec.widget, "has_page_focus", None)
            focus = bool(holds()) if holds is not None else False
        self._max = None
        self._disarm_focus_trap()
        self._max_pane.set_visible(False)
        for handler in rec.handlers:
            rec.widget.disconnect(handler)
        view = self._max_pane.tab_view
        page = view.get_nth_page(0) if view.get_n_pages() else None
        if page is None:
            return True
        home = rec.strip.tab_view if rec.strip in self._tree else self._bounce_view()
        view.transfer_page(page, home, min(rec.position, home.get_n_pages()))
        strip = self._strip_of(rec.widget)
        if strip is not None:
            strip.select_widget(rec.widget, focus=focus)
            self._reveal_strip(strip)
            if focus:
                GLib.idle_add(rec.widget.grab_page_focus)
        return True

    def _wire_max_page(self, widget) -> None:
        """Carry the page's optional signals while it is out of every
        strip: the strip unwired them on the way out (they ride
        attach/detach so they follow a page between strips), and a shell
        that exits, a bell that rings or a title that moves still has to
        land somewhere. Mirrors `PanelStrip._on_page_attached`."""
        rec = self._max
        if GObject.signal_lookup("shell-exited", widget.__gtype__):
            rec.handlers.append(widget.connect("shell-exited", self._on_max_shell_exited))
        if GObject.signal_lookup("bell", widget.__gtype__):
            rec.handlers.append(widget.connect("bell", lambda *_: self.emit("bell")))
        if GObject.signal_lookup("title-changed", widget.__gtype__):
            rec.handlers.append(
                widget.connect(
                    "title-changed", lambda page: self._max_pane.set_page_title(page.page_title())
                )
            )

    def _on_max_key(self, _controller, keyval: int, _keycode: int, state) -> bool:
        """Escape, typed anywhere inside a maximized page: put it back down.

        Bare Escape only, and only where the page doesn't want the key for
        itself — the decision is `panelkeys.escape_restores` (GTK-free, so
        the tests can reach it). A page states its claim with the optional
        `holds_escape()` hook: a maximized shell running vim or a pager
        needs Escape to reach it, and at a bare prompt it doesn't. Pages
        without the hook never keep it.
        """
        rec = self._max
        if rec is None:
            return False
        holds = getattr(rec.widget, "holds_escape", None)
        if not panelkeys.escape_restores(
            int(keyval), int(state), holds if holds is not None else lambda: False
        ):
            return False
        self.restore_maximized()
        return True

    def _arm_focus_trap(self) -> None:
        """Watch the window's focus for as long as a page is maximized.

        Everything the pane covers is still there, focusable and one Tab
        (or one tab switch, which re-grabs the agent terminal) away — and
        typing into a widget hidden under an opaque overlay is typing
        blind. Nothing is made insensitive to achieve it: the agent's VTE
        stays live, it just doesn't get to hold the keyboard."""
        root = self.get_root()
        if root is None or self._focus_handler:
            return
        self._focus_root = root
        self._focus_handler = root.connect(
            "notify::focus-widget", self._on_root_focus_changed
        )

    def _disarm_focus_trap(self) -> None:
        if self._focus_handler and self._focus_root is not None:
            self._focus_root.disconnect(self._focus_handler)
        self._focus_root = None
        self._focus_handler = 0

    def _on_root_focus_changed(self, root, _pspec) -> None:
        """Focus moved: pull it back if it landed behind the overlay.

        "Behind" is the maximize host's own child — the whole session tab
        minus its footer, which the pane deliberately doesn't cover (see
        `set_maximize_host`). Focus anywhere else is left alone: a dialog,
        a popover, the sidebar, another tab, and the footer's chips are all
        outside what the page is covering, so none of them is a keystroke
        going somewhere unseen."""
        rec = self._max
        if rec is None or self._refocusing:
            return
        focus = root.get_focus()
        behind = self._max_host.get_child()
        if focus is None or behind is None:
            return
        if focus is not behind and not focus.is_ancestor(behind):
            return
        self._refocusing = True
        try:
            rec.widget.grab_page_focus()
        finally:
            self._refocusing = False

    def _on_destroy(self, *_args) -> None:
        self._disarm_focus_trap()
        guard.clear_fallback(self)

    def _on_max_shell_exited(self, shell) -> None:
        """`exit` typed in a maximized shell: the page goes home and then
        closes, the same end its tab's X would give it — and the same
        collapse behind it, which the restore has just re-enabled."""
        self.restore_maximized()
        self.close_page(shell)

    def _adopt_home(self, strip, position: str) -> None:
        """Hand the home role to *strip* on *position*'s axis, the old home
        strip having run out of shells (a rotation took its last one),
        given way (a swap merged it in), or never existed (Ctrl+J opening
        its terminal in a shell strip that was already there). The role is
        about *size and edge*, not about the toggle: it names the divider
        the app-wide "home" seed speaks for, so it has to follow the shells
        wherever they end up. Its remembered size comes along, so a panel
        sent bottom→right opens at the width it always had."""
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
        qualifies — a rotation should land in the shells' own panel rather
        than beside it — otherwise the tree's spatial order picks."""
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

    def _on_page_touched(self, _strip, widget, arrived: bool) -> None:
        """A page arrived in a strip (*arrived*) or came to its front.
        Remembered rather than looked up on demand because the rotate
        shortcut is usually pressed with the focus back in the agent
        terminal, where no strip can answer for it.

        An arrival is also where a free Ctrl+J binding finds its terminal:
        with none bound, the next shell to turn up anywhere in the dock
        takes the shortcut — whether Ctrl+J opened it, a strip's own + did,
        or a session restore rebuilt it (see `panel_terminal`). Arrivals
        only, so that closing the bound terminal hands the shortcut to the
        next one *opened* rather than to whichever tab the closing tab row
        happened to select behind it."""
        self._recent = widget
        if (
            arrived
            and getattr(widget, "page_kind", None) == "shell"
            and self.panel_terminal is None
        ):
            self._toggle_shell = widget

    def _recent_page(self):
        """(strip, widget) for the panel tab the rotate shortcut acts on:
        whichever page holds the focus right now, else the last one added or
        fronted. Hidden strips don't answer — the strip Ctrl+J took off
        screen has a selected tab still, and rotating a panel nobody can
        see would be a move with nothing to watch it happen. Nor does a
        stowed terminal: `_strip_of` finds it in no strip at all."""
        for strip in self.strips():
            if strip.get_visible() and strip.has_page_focus():
                return strip, strip.selected_page_widget()
        strip = self._strip_of(self._recent)
        if strip is not None and strip.get_visible():
            return strip, self._recent
        return None, None

    def rotate_recent_page(self) -> None:
        """Send the focused (or last-touched) panel tab to the dock's other
        axis — the tab row's rotate button, reached from the keyboard
        (win.rotate-panel-page)."""
        strip, widget = self._recent_page()
        if strip is not None and widget is not None:
            self.rotate_page(strip, widget)

    def close_recent_page(self) -> bool:
        """Close one panel tab — a maximized page first, else the focused (or
        last-touched) one — through the strip's own close funnel, busy-ask and
        all. This is Ctrl+W's first stop (win.close-tab), so False has to mean
        "nothing on show to close": the caller closes the whole session tab on
        it, and no visible page may be left behind by that answer. Hence the
        fallback past `_recent_page` to any visible strip's selected tab — the
        last-touched page can be the terminal Ctrl+J has since taken off
        screen while a satellite strip is still up.

        True while a busy shell's confirmation is only *asked*: the press has
        landed on the panel either way, and taking the session tab out from
        under the dialog is the one thing it must not do."""
        widget = self.maximized_page or self._recent_page()[1]
        if widget is None:
            strip = next((s for s in self.strips() if s.get_visible() and s.page_count), None)
            widget = strip.selected_page_widget() if strip is not None else None
        if widget is None:
            return False
        self.close_page(widget)  # brings a maximized page down on the way
        return True

    # -- strip lifecycle -----------------------------------------------------

    def _new_strip(self):
        strip = self._strip_factory()
        if self._settings is not None:
            strip.apply_settings(self._settings)
        strip.set_page_mover(self)
        strip.connect("bell", lambda *_: self.emit("bell"))
        strip.connect("empty", self._on_strip_empty)
        strip.connect("page-touched", self._on_page_touched)
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
        if self._max is not None and strip is self._max.strip:
            # Emptied only because its one page is floating over the tab.
            # The strip holds its place (invisible under the overlay) so
            # the restore button has somewhere to put the page back.
            return GLib.SOURCE_REMOVE
        root = self.get_root()
        focus = root.get_focus() if root is not None else None
        refocus = focus is None or focus is strip or focus.is_ancestor(strip)
        if self._stowed is not None and strip is self._stowed.strip:
            # Emptied around a stowed terminal: its tab row's other pages
            # have all closed, and what is left of the strip is the page
            # Ctrl+J is holding off screen. Rather than collapse the row it
            # has to come back to, put it back and hide the strip around
            # it — the same off screen, by the other of the two means (see
            # `hide_panel_terminal`).
            self._unstow()
            self._hide_strip(strip)
            if refocus and self._focus_terminal is not None:
                self._focus_terminal()
            return GLib.SOURCE_REMOVE
        if strip is self._home_strip:
            self._stash_home_size()
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
        sizer.set_lookup(lambda axis, s=sizer: self._lookup_size(s, axis))
        sizer.set_floor(lambda axis, total, s=sizer: self._column_floor(s, axis, total))
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

    def _page_rec(self, axis: str) -> _PaneRec | None:
        """The record of the paned dividing the terminal from the strip
        `open_page` lands a docked page in on *axis* — the divider whose
        position *is* the PR/attachments/composer panel's size. None when
        that side is empty, or holds the home strip (whose size is the home
        seed's business, not this one's).

        Found the way `_home_rec` finds its own: the split *separating* the
        two, since strips split off that one carry it deeper into the tree.
        """
        strip = self._strip_past_terminal("v" if axis == "bottom" else "h")
        if strip is None or strip is self._home_strip:
            return None
        try:
            split = self._tree.separator_of(strip, self._terminal)
        except ValueError:
            return None  # the strip left the tree (a collapse in flight)
        return self._panes.get(split)

    def _split_is_free(self, scope: str = "page", minimum: int = 0) -> bool:
        """Whether a new column beside the terminal would cost the terminal
        nothing — the reason `open_page` and Ctrl+J will split an axis that
        already has a strip on it instead of joining.

        The terminal stops growing at the "terminal_max_width" setting and
        centers itself in whatever it was given, so on a wide screen it is
        sitting on gutter it will never use: a strip carved out of that is
        width nobody loses. The seed handed over is the very one the new
        strip's own sizer would open at — *scope*'s, "page" for a docked
        page's column and "home" for the shells' — so what's measured is
        the column that is actually about to appear, not a guess at it (see
        `panelsizing.room_for_a_split`).

        False whenever the answer isn't certain — no maximum width set, no
        settings yet, a terminal that hasn't been allocated — which leaves
        the plain join-don't-split rule in charge. Two pages opened in the
        same frame are that last case: `_split_leaf` unparents the terminal
        to re-place it under the new paned, and an unparented widget
        measures 0 until the next layout pass, so the second page joins.
        Nothing the user can do by hand opens two pages without a frame in
        between.

        *minimum* is the opening page's `column_floor`, when it has one:
        the column is then measured at the floor the gutter raises it to
        (see `panelsizing.spare_floor`), the very width `_column_floor`
        hands the new sizer."""
        wanted = int(self._size_lookup(scope, "right") or 0) if self._size_lookup else 0
        return panelsizing.room_for_a_split(
            self._terminal.get_width(), self._terminal_keep(), wanted, minimum
        )

    def _terminal_keep(self) -> int:
        """The "terminal_max_width" setting as the width the terminal is
        owed — 0 (no maximum, so no free gutter) when unset or unreadable."""
        settings = self._settings or {}
        try:
            return int(settings.get("terminal_max_width") or 0)
        except (TypeError, ValueError):
            return 0  # untrusted setting; treat it as "no maximum"

    def _column_floor(self, sizer, axis: str, total: int) -> int:
        """The least a docked-page column opens at, for the sizer of a split
        *total* px across: the spare-gutter floor of the widest
        `column_floor` among the pages in the strip it manages (a PR page's
        320px, doubled when the terminal's gutter can pay for it, else as
        much of the gutter as covers it at all — see
        `panelsizing.spare_floor`). 0 for the home strip, for a column below
        the terminal, and for a strip of pages that declare no floor, which
        leaves the ordinary seed in charge.

        Read at apply time, once the page is in the strip, so a page whose
        first fetch is still in flight opens at its full width and never
        grows under its own data; and only for the split *separating* the
        strip from the terminal, whose total is what the terminal had."""
        if axis != "right" or self._scope_of(sizer, axis) != "page":
            return 0
        rec = next((r for r in self._panes.values() if r.sizer is sizer), None)
        if rec is None or rec.managed is self._home_strip:
            return 0
        pages = getattr(rec.managed, "panel_pages", lambda: [])()
        minimum = max((int(getattr(p, "column_floor", 0) or 0) for p in pages), default=0)
        return panelsizing.spare_floor(total, self._terminal_keep(), minimum)

    def _scope_of(self, sizer, axis: str) -> str:
        """Which app-wide seed a divider speaks for: "home" for the shells'
        strip, "page" for anything else. Dividers deeper in the tree (a
        strip a drag split in two) speak for no seed of their own — they
        still *read* the page one, which sizes the column they live in."""
        rec = self._home_rec()
        return "home" if rec is not None and rec.sizer is sizer else "page"

    def _lookup_size(self, sizer, axis: str) -> int:
        if self._size_lookup is None:
            return 0
        return int(self._size_lookup(self._scope_of(sizer, axis), axis) or 0)

    def _on_strip_size_changed(self, sizer, key: str, size: int) -> None:
        """The two dividers that update the app-wide axis seeds: the home
        strip's, and the one sizing the strip docked pages open into — so a
        PR page dragged wider is that wide the next time one opens. Deeper
        strips size themselves without shifting either default."""
        scope = self._scope_of(sizer, key)
        rec = self._home_rec() if scope == "home" else self._page_rec(key)
        if rec is not None and rec.sizer is sizer:
            self.emit("size-changed", scope, key, size)
