# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The panel strip: a tab strip of panel pages beside the agent terminal.

Generalized from the secondary terminal panel's inner tab strip
(`PanelTabs`), which only ever held shells. A strip holds *pages* — any
widget implementing the duck-typed `PanelPage` protocol:

    page_kind: str                 # "shell", "pr", ... (class attribute)
    page_title() -> str            # tab label ("Terminal 2", "#241")
    page_icon() -> str | None      # symbolic icon for the tab
    grab_page_focus() -> None      # strip shown / tab selected
    has_page_focus() -> bool       # for hide-time focus return
    page_busy() -> bool            # running command -> confirm close
    apply_settings(dict) -> None   # font/theme fan-out (no-op where n/a)
    page_closed() -> None          # optional: tab is closing for real (not
                                   # a transfer) — last chance to rescue
                                   # anything living inside the widget
    holds_escape() -> bool         # optional: keep Escape from the dock's
                                   # restore-from-maximized (paneldock);
                                   # absent means the page never wants it

A page may additionally emit "bell" (re-emitted by the strip for the
window's visual bell) and "shell-exited" (shells: the process ended, so
the page closes itself). Those signals are wired on page-attached and
unwired on page-detached, so they follow a page that moves between strips
(`Adw.TabView.transfer_page` — reparenting without destroying, which is
what keeps a moved shell running). The strip owns close protection for
every kind: a busy page's X asks first, whatever the kind.

Shell pages are created by the strip itself (the + button); the concrete
shell class is injected as `shell_factory` so this module stays free of
terminal.py (which imports it). Moving, splitting, rotating and maximizing
need to see the whole dock, so those menu items — and the tab row's rotate
and overlay buttons — call back into an injected *page mover* (the
PanelDock) rather than anything strip-local.
"""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from . import dialogs, paneldnd  # noqa: E402
from .i18n import _, ngettext  # noqa: E402


class PanelStrip(Gtk.Box):
    """A tab strip of panel pages: `Adw.TabView` + inline `Adw.TabBar`.

    The tab row carries a + button (new shell page, selected immediately),
    an overlay button that floats the tab on top over the whole session
    tab, a rotate button that sends it to the dock's other axis (any kind
    of tab, not just shells), and each tab an X; shells survive hide/show
    and die with their tab. When the last page closes (or transfers away)
    there is nothing left to show, so the owner collapses the strip — see
    "empty"."""

    __gsignals__ = {
        # Emitted when a page's X (or a shell exiting) removed the last page.
        "empty": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # Emitted when any page rings BEL, for the window's visual bell.
        "bell": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # Emitted with the page widget whenever a *protocol* page arrives in
        # this strip or comes to the front of it: the dock's record of which
        # panel tab the rotate shortcut acts on (see PanelDock._on_page_touched).
        # The bool says which of the two it was — True for an arrival, which
        # is the only kind Ctrl+J's free binding will attach itself to.
        # Gated like every other protocol touch here, so the foreign tab native
        # DnD can land for one main-loop turn is never what gets remembered.
        "page-touched": (GObject.SignalFlags.RUN_FIRST, None, (object, bool)),
    }

    def __init__(self, shell_factory) -> None:
        """`shell_factory() -> PanelPage` builds the shell page the + button
        appends (numbering lives with the dock, so titles stay unique when
        pages move between strips)."""
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._shell_factory = shell_factory
        self._settings: dict | None = None  # last applied; new pages start from it
        self._cwd_lookup = None  # () -> agent cwd, set by the owning tab
        self._page_mover = None  # the dock's move/split interface, if docked
        self._move_targets: list = []  # (label, strip) stash for menu actions
        self._close_ok: set[Adw.TabPage] = set()  # busy closes the user confirmed
        self._close_asking: set[Adw.TabPage] = set()  # a confirm dialog is up
        self._page_signals: dict = {}  # page widget -> its handler ids here
        self._quiet_focus = False  # one selection that must not take the cursor

        self._view = Adw.TabView(vexpand=True)
        self._view.connect("close-page", self._on_close_page)
        # Page signals ride attach/detach so they follow transfers.
        self._view.connect("page-attached", self._on_page_attached)
        self._view.connect("page-detached", self._on_page_detached)
        # Clicking a tab should land the cursor in its page.
        self._view.connect("notify::selected-page", self._on_selected)

        # Right-clicking a tab: bulk closes, on top of the X each tab already
        # carries, plus the dock's move/split items when a page mover is
        # wired. Which tab was clicked arrives through "setup-menu" — the
        # editor file strip's pattern (see EditorPane). The move section is
        # rebuilt on every open: its targets are whatever strips exist then.
        self._menu_page: Adw.TabPage | None = None
        tab_menu = Gio.Menu()
        close_section = Gio.Menu()
        close_section.append(_("Close Tab"), "strip.close-tab")
        close_section.append(_("Close other tabs"), "strip.close-other-tabs")
        tab_menu.append_section(None, close_section)
        self._move_section = Gio.Menu()
        tab_menu.append_section(None, self._move_section)
        self._view.set_menu_model(tab_menu)
        self._view.connect("setup-menu", self._on_setup_menu)
        self._tab_actions: dict[str, Gio.SimpleAction] = {}
        group = Gio.SimpleActionGroup()
        for name, handler in (
            ("close-tab", self._close_menu_tab),
            ("close-other-tabs", self._close_other_tabs),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, h=handler: h())
            group.add_action(action)
            self._tab_actions[name] = action
        move_action = Gio.SimpleAction.new("move-to", GLib.VariantType.new("i"))
        move_action.connect("activate", self._on_move_to)
        group.add_action(move_action)
        split_action = Gio.SimpleAction.new("split-page", GLib.VariantType.new("s"))
        split_action.connect("activate", self._on_split_page)
        group.add_action(split_action)
        self.insert_action_group("strip", group)

        # autohide off: the bar is also where the + button lives, so it must
        # stay up even with a single tab.
        bar = Adw.TabBar(view=self._view, autohide=False)
        bar.add_css_class("inline")
        bar.set_expand_tabs(False)
        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.add_css_class("flat")
        add_btn.set_tooltip_text(_("New terminal tab"))
        add_btn.connect("clicked", lambda *_: self.new_shell())
        max_btn = Gtk.Button(icon_name="view-fullscreen-symbolic")
        max_btn.add_css_class("flat")
        max_btn.set_tooltip_text(_("Overlay this tab over the whole session"))
        max_btn.connect("clicked", lambda *_: self.maximize_selected())
        rotate_btn = Gtk.Button(icon_name="object-rotate-right-symbolic")
        rotate_btn.add_css_class("flat")
        rotate_btn.set_tooltip_text(_("Move this tab to the other side (Ctrl+;)"))
        rotate_btn.connect("clicked", lambda *_: self.rotate_selected())
        end_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        end_box.append(add_btn)
        end_box.append(max_btn)
        end_box.append(rotate_btn)
        # The fallback grip: hidden while per-tab drag handles are on (the
        # default), shown when the panel_tab_drag_handles setting opts out
        # of the private-API path (see paneldnd and _apply_tab_drag).
        self._grip = paneldnd.make_grip(self)
        self._grip.set_visible(False)
        end_box.append(self._grip)
        bar.set_end_action_widget(end_box)

        self._bar = bar
        self.append(bar)
        self.append(self._view)
        # Before any page arrives and before the first apply_settings: the
        # native drag has to be down from the strip's first press, not from
        # its first settings pass.
        self._apply_tab_drag()

    # -- pages -------------------------------------------------------------

    @property
    def tab_view(self) -> Adw.TabView:
        """The strip's `Adw.TabView` — what registers with the tab-DnD
        guard and what a guard bounce transfers pages into."""
        return self._view

    @property
    def tab_bar(self) -> Adw.TabBar:
        """The strip's inline `Adw.TabBar` — walked for the private tab
        widgets the per-tab drag sources ride on (see paneldnd)."""
        return self._bar

    @property
    def tab_drag_handles(self) -> bool:
        """Whether tabs drag by their own handles (the private-API path).
        Off is the panel_tab_drag_handles fallback: native tab dragging
        plus the end-of-bar grip."""
        settings = self._settings or {}
        return bool(settings.get("panel_tab_drag_handles", True))

    @property
    def page_mover(self):
        """The dock's move/split interface, or None while undocked."""
        return self._page_mover

    @property
    def page_count(self) -> int:
        return self._view.get_n_pages()

    def pages(self) -> list:
        """Every page widget, in tab order."""
        return [
            self._view.get_nth_page(i).get_child() for i in range(self._view.get_n_pages())
        ]

    def panel_pages(self) -> list:
        """Every page that speaks the PanelPage protocol, in tab order —
        what a whole-strip move (the swap merging this panel into another
        strip) ranges over. `getattr`, not a bare attribute: native tab DnD
        can land a *foreign* page (a session or editor tab) here for the one
        main-loop turn before the tab guard bounces it out, and every
        protocol touch in this class has to shrug at it rather than raise
        (see tabguard)."""
        return [page for page in self.pages() if getattr(page, "page_kind", None) is not None]

    def shell_pages(self) -> list:
        """The pages of kind shell, in tab order — the set this strip's
        shell-wide questions (busy, home role) range over. The dock's own
        aggregates (capture, clear) ask it, since they have to count a
        maximized page this strip no longer physically holds."""
        return [page for page in self.panel_pages() if page.page_kind == "shell"]

    def selected_page_widget(self):
        page = self._view.get_selected_page()
        return page.get_child() if page is not None else None

    def set_cwd_lookup(self, lookup) -> None:
        """`lookup() -> path` supplies the agent's current cwd for the shells
        the + button (and Ctrl+J) start."""
        self._cwd_lookup = lookup

    def _cwd(self) -> str | None:
        return self._cwd_lookup() if self._cwd_lookup is not None else None

    def new_shells(self, restore_texts: list[str] | None = None, focus: bool = True) -> list:
        """Append one shell page per saved panel-history text — a single
        blank one when there is no history — oldest first, and select the
        first of them (*focus* False without taking the keyboard). Returns
        them in that order: the dock binds Ctrl+J to the first (see
        PanelDock.show_panel_terminal)."""
        shells = [
            self.new_shell(restore_text=text, select=False)
            for text in (restore_texts or [None])
        ]
        if shells:
            self.select_widget(shells[0], focus=focus)
        return shells

    def refresh_shell(self, shell) -> None:
        """Re-point an existing shell page at the agent's cwd — what showing
        a hidden terminal again does, since the agent may have moved into a
        worktree while it was off screen. Idle shells only; one with a
        command running is left alone (see PanelPage.open_shell)."""
        shell.open_shell(self._cwd())

    def new_shell(self, restore_text: str | None = None, select: bool = True):
        """Append a shell page (its shell spawns right away) and optionally
        select it. `restore_text` seeds the scrollback (session restore)."""
        shell = self._shell_factory()
        self.add_page(shell, select=False)
        shell.open_shell(self._cwd(), restore_text)
        if select:
            self.select_widget(shell)
        return shell

    def add_page(self, widget, select: bool = True, focus: bool = True) -> None:
        """Append any PanelPage as a tab — the non-shell kinds' way in (a
        shell needs `new_shell`, which also spawns it). Settings fan out to
        the new page like they do to the rest.

        Selecting a tab normally lands the cursor in it (`_on_selected`);
        *focus* False makes this one selection quiet, for a page that
        appears without having been asked for."""
        if self._settings is not None:
            widget.apply_settings(self._settings)
        page = self._view.append(widget)
        self._sync_tab(page)
        if select:
            # Read and cleared inside the selection notify below, which
            # Adw.TabView emits synchronously from set_selected_page; the
            # reset after covers a selection that never fired.
            self._quiet_focus = not focus
            self._view.set_selected_page(page)
            self._quiet_focus = False

    def _find_page(self, widget) -> Adw.TabPage | None:
        for i in range(self._view.get_n_pages()):
            page = self._view.get_nth_page(i)
            if page.get_child() is widget:
                return page
        return None

    def transfer_to(self, widget, other, position: int | None = None) -> None:
        """Move *widget*'s tab into *other*, appending unless *position*
        says where. The page widget is reparented, never destroyed — a
        shell's process rides along.

        *other* is anything with a `tab_view`: another strip, or the
        dock's maximize pane, which hosts a lifted page in a bare view of
        its own (see paneldock._MaxPane)."""
        page = self._find_page(widget)
        if page is not None:
            view = other.tab_view
            if position is None:
                position = view.get_n_pages()
            self._view.transfer_page(page, view, position)

    def select_widget(self, widget, focus: bool = True) -> None:
        """Front *widget*'s tab. *focus* False keeps the selection quiet
        (see add_page): for fronting a page without being asked, where the
        keyboard must stay wherever it already is."""
        page = self._find_page(widget)
        if page is not None:
            self._quiet_focus = not focus
            self._view.set_selected_page(page)
            self._quiet_focus = False

    def close_widget(self, widget) -> None:
        """Close *widget*'s tab through the same funnel its own X uses —
        busy-ask and all (the dock's `close_page`)."""
        page = self._find_page(widget)
        if page is not None:
            self._view.close_page(page)

    def reorder_to(self, widget, insert_index: int) -> None:
        """Reorder *widget*'s tab to sit before the tab currently at
        *insert_index* (an index that counts the moved tab itself, as
        dockzones.insert_index yields): moving right, the departure shifts
        everything after it left by one, so the final position is one less
        than the insertion point."""
        page = self._find_page(widget)
        if page is None:
            return
        current = self._view.get_page_position(page)
        position = insert_index - 1 if current < insert_index else insert_index
        self._view.reorder_page(page, max(position, 0))

    # -- page signal lifecycle ----------------------------------------------

    def _on_page_attached(self, _view, page: Adw.TabPage, _position) -> None:
        """Wire the page's optional signals to *this* strip — on creation
        and again whenever a transfer lands it here — give its tab the drag
        handle, and report the arrival ("page-touched"). All three are gated
        to protocol pages: a foreign tab that native DnD drops here for the
        one turn before the guard bounces it must leave unmarked, unwired,
        and unremembered (see shell_pages)."""
        widget = page.get_child()
        ids = []
        if GObject.signal_lookup("shell-exited", widget.__gtype__):
            ids.append(widget.connect("shell-exited", self._on_shell_exited))
        if GObject.signal_lookup("bell", widget.__gtype__):
            ids.append(widget.connect("bell", lambda *_: self.emit("bell")))
        if GObject.signal_lookup("title-changed", widget.__gtype__):
            # A page whose title/icon follow live state (a PR page's state
            # icon) re-syncs its tab whenever that state moves.
            ids.append(widget.connect("title-changed", self._on_title_changed))
        self._page_signals[widget] = ids
        if getattr(widget, "page_kind", None) is not None:
            if self.tab_drag_handles:
                page.set_indicator_icon(Gio.ThemedIcon.new(paneldnd.HANDLE_ICON))
                paneldnd.wire_tab_drag(self, widget)
            self.emit("page-touched", widget, True)

    def _sync_tab(self, page: Adw.TabPage) -> None:
        """Bring a tab's title and icon in line with its page widget."""
        widget = page.get_child()
        page.set_title(widget.page_title())
        icon = widget.page_icon()
        page.set_icon(Gio.ThemedIcon.new(icon) if icon else None)

    def _on_title_changed(self, widget) -> None:
        page = self._find_page(widget)
        if page is not None:
            self._sync_tab(page)

    def _on_page_detached(self, _view, page: Adw.TabPage, _position) -> None:
        """Unwire a departing page (close or transfer-out) and report an
        emptied strip — the owner collapses it. Detach is the one funnel
        both paths share, so "empty" lives here rather than in close."""
        widget = page.get_child()
        for handler in self._page_signals.pop(widget, ()):
            widget.disconnect(handler)
        if self._view.get_n_pages() == 0:
            self.emit("empty")

    # -- closing -----------------------------------------------------------

    def _on_shell_exited(self, shell) -> None:
        """Typing `exit` in a shell closes its page (the tab would otherwise
        sit on a dead screen). A shell already gone by teardown finds no
        page and is a no-op."""
        page = self._find_page(shell)
        if page is not None:
            self._view.close_page(page)

    def _on_close_page(self, view: Adw.TabView, page: Adw.TabPage) -> bool:
        widget = page.get_child()
        if page not in self._close_ok and getattr(widget, "page_busy", bool)():
            # The X on a busy page asks first, mirroring the session tab's
            # own close protection — a build shouldn't die to a stray click.
            view.close_page_finish(page, False)  # keep the tab while we ask
            if page not in self._close_asking:
                self._ask_close_busy(page)
            return True
        self._close_ok.discard(page)
        # The close is happening for sure now: a page with something to
        # rescue (the composer's live view) gets it out before the widget
        # goes down with its tab. Transfers never come this way.
        closed = getattr(widget, "page_closed", None)
        if closed is not None:
            closed()
        view.close_page_finish(page, True)  # page-detached reports "empty"
        return True  # close_page_finish already ran

    def _ask_close_busy(self, page: Adw.TabPage) -> None:
        self._close_asking.add(page)
        self._view.set_selected_page(page)  # show what's about to be killed

        def do_close() -> None:
            self._close_asking.discard(page)
            # The tab may have emptied on its own while the dialog sat open
            # (the command finished and the user typed `exit`).
            if self._find_page(page.get_child()) is page:
                self._close_ok.add(page)
                self._view.close_page(page)

        dialogs.confirm_dialog(
            self,
            _("Close tab with a running command?"),
            _("A command is still running in this terminal tab and will be terminated."),
            _("Close Tab"),
            do_close,
            on_dismiss=lambda: self._close_asking.discard(page),
            default_response="confirm",
        )

    # -- tab context menu ----------------------------------------------------

    def set_page_mover(self, mover) -> None:
        """Wire the dock's move/split interface: `move_targets(strip) ->
        [(label, strip)]`, `move_page(strip, widget, target)`,
        `split_page(strip, widget, side)`, `rotate_page(strip, widget)` and
        `maximize_page(strip, widget)`. Without one (a strip under test, or
        mid-adoption) the menu simply has no move section and the rotate
        and overlay buttons no-op."""
        self._page_mover = mover

    def maximize_selected(self) -> None:
        """The tab row's overlay button: float the tab on top over the
        whole session tab, hiding the terminal, every other strip and the
        editor column under it until its restore button puts it back.
        Where "the whole tab" reaches is a fact about the dock and the
        session around it, so an undocked strip has nothing to cover."""
        widget = self.selected_page_widget()
        if widget is not None and self._page_mover is not None:
            self._page_mover.maximize_page(self, widget)

    def rotate_selected(self) -> None:
        """The tab row's rotate button: send the tab on top to the dock's
        other axis — below the terminal to beside it, and back. Only that
        one tab moves, whatever kind it is; its siblings stay put. Which
        way "the other axis" runs is a fact about the whole dock, so an
        undocked strip has nothing to rotate into."""
        widget = self.selected_page_widget()
        if widget is not None and self._page_mover is not None:
            self._page_mover.rotate_page(self, widget)

    def _on_setup_menu(self, view: Adw.TabView, page: Adw.TabPage | None) -> None:
        """The menu is opening on *page* — or closing, which is a None page
        and leaves the stashed one alone: the action fires after the popover
        is gone, and it still has to know which tab it was opened on. An item
        that would close nothing is greyed out rather than left a no-op."""
        if page is None:
            return
        self._menu_page = page
        self._tab_actions["close-other-tabs"].set_enabled(view.get_n_pages() > 1)
        self._rebuild_move_section()

    def _rebuild_move_section(self) -> None:
        """The move/split half of the tab menu, recomputed per open: "Move
        to" lists the dock's other strips by their selected page's title,
        and the four Split items carve this strip's own node. The targets
        stash pairs each menu index with its strip, so the action — firing
        after the popover closes — still knows where to send the page."""
        self._move_section.remove_all()
        if self._page_mover is None:
            return
        self._move_targets = self._page_mover.move_targets(self)
        if self._move_targets:
            move_menu = Gio.Menu()
            for index, (label, _strip) in enumerate(self._move_targets):
                item = Gio.MenuItem.new(label, None)
                item.set_action_and_target_value("strip.move-to", GLib.Variant("i", index))
                move_menu.append_item(item)
            self._move_section.append_submenu(_("Move to"), move_menu)
        for label, side in (
            (_("Split Left"), "left"),
            (_("Split Right"), "right"),
            (_("Split Up"), "above"),
            (_("Split Down"), "below"),
        ):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value("strip.split-page", GLib.Variant("s", side))
            self._move_section.append_item(item)

    def _on_move_to(self, _action, param) -> None:
        page = self._menu_target_page()
        index = param.get_int32()
        if page is None or self._page_mover is None:
            return
        if not 0 <= index < len(self._move_targets):
            return  # the dock changed under the open menu
        self._page_mover.move_page(self, page.get_child(), self._move_targets[index][1])

    def _on_split_page(self, _action, param) -> None:
        page = self._menu_target_page()
        if page is None or self._page_mover is None:
            return
        self._page_mover.split_page(self, page.get_child(), param.get_string())

    def _menu_target_page(self) -> Adw.TabPage | None:
        page = self._menu_page
        if page is None or self._find_page(page.get_child()) is not page:
            return None  # closed by other means since the menu opened
        return page

    def _close_menu_tab(self) -> None:
        page = self._menu_target_page()
        if page is not None:
            self._view.close_page(page)

    def _close_other_tabs(self) -> None:
        """Close every page but the menu's own. Busy pages are gathered
        behind a single confirmation for the whole bulk — routing each
        through its own close_page ask would stack one dialog per busy
        page, each also flipping the visible tab."""
        keep = self._menu_target_page()
        if keep is None:
            return
        targets = [
            page
            for widget in self.pages()
            if (page := self._find_page(widget)) is not None and page is not keep
        ]
        busy = [page for page in targets if getattr(page.get_child(), "page_busy", bool)()]
        if not busy:
            for page in targets:
                self._view.close_page(page)
            return
        self._view.set_selected_page(busy[0])  # show what's about to be killed

        def do_close() -> None:
            for page in targets:
                # A page may have emptied on its own while the dialog sat
                # open (the command finished and the user typed `exit`).
                if self._find_page(page.get_child()) is page:
                    self._close_ok.add(page)
                    self._view.close_page(page)

        dialogs.confirm_dialog(
            self,
            _("Close tabs with running commands?"),
            ngettext(
                "A command is still running in one of these tabs and will be terminated.",
                "Commands are still running in {count} of these tabs and will be terminated.",
                len(busy),
            ).format(count=len(busy)),
            _("Close Tabs"),
            do_close,
            default_response="confirm",
        )

    # -- focus and aggregates ------------------------------------------------

    def _on_selected(self, *_args) -> None:
        # getattr twice: a just-dropped foreign page (see shell_pages) becomes
        # the selection before the guard bounces it — nothing to focus, and
        # nothing the dock should remember as the tab to rotate either.
        widget = self.selected_page_widget()
        if getattr(widget, "page_kind", None) is not None:
            self.emit("page-touched", widget, False)
        quiet, self._quiet_focus = self._quiet_focus, False
        grab = getattr(widget, "grab_page_focus", None)
        if grab is not None and not quiet and self.get_mapped():
            GLib.idle_add(grab)

    def grab_page_focus(self) -> None:
        """Land the cursor in the selected page."""
        grab = getattr(self.selected_page_widget(), "grab_page_focus", None)
        if grab is not None:
            grab()

    def has_page_focus(self) -> bool:
        return any(
            page.has_page_focus()
            for page in self.pages()
            if hasattr(page, "has_page_focus")
        )

    def has_running_command(self) -> bool:
        return any(shell.page_busy() for shell in self.shell_pages())

    def select_busy_page(self) -> None:
        """Bring the first shell with a live command to the front (the close
        confirmation shows the panel to reveal what's about to be killed)."""
        for shell in self.shell_pages():
            if shell.page_busy():
                page = self._find_page(shell)
                if page is not None:
                    self._view.set_selected_page(page)
                return

    def apply_settings(self, settings: dict) -> None:
        self._settings = settings
        for page in self.pages():
            if hasattr(page, "apply_settings"):  # see shell_pages
                page.apply_settings(settings)
        self._apply_tab_drag()

    def _apply_tab_drag(self) -> None:
        """Bring the drag affordances in line with the setting, live: per-tab
        handles come and go on every page, Adwaita's own tab drag stands
        down exactly while they're up (it wins a fast flick otherwise — see
        paneldnd.disarm_native_drag), and the fallback grip shows exactly
        when they're off."""
        enabled = self.tab_drag_handles
        self._grip.set_visible(not enabled)
        if enabled:
            paneldnd.disarm_native_drag(self._bar)
        else:
            paneldnd.restore_native_drag(self._bar)
        for widget in self.pages():
            if getattr(widget, "page_kind", None) is None:
                continue  # see shell_pages
            page = self._find_page(widget)
            if enabled:
                if page is not None and page.get_indicator_icon() is None:
                    page.set_indicator_icon(Gio.ThemedIcon.new(paneldnd.HANDLE_ICON))
                paneldnd.wire_tab_drag(self, widget)
            else:
                if page is not None:
                    page.set_indicator_icon(None)
                paneldnd.unwire_tab_drag(self, widget)
