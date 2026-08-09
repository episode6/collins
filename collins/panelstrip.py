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

A page may additionally emit "bell" (re-emitted by the strip for the
window's visual bell) and "shell-exited" (shells: the process ended, so
the page closes itself). The strip owns close protection for every kind:
a busy page's X asks first, whatever the kind.

Shell pages are created by the strip itself (the + button); the concrete
shell class is injected as `shell_factory` so this module stays free of
terminal.py (which imports it).
"""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from . import dialogs  # noqa: E402
from .i18n import _  # noqa: E402


class PanelStrip(Gtk.Box):
    """A tab strip of panel pages: `Adw.TabView` + inline `Adw.TabBar`.

    The tab row carries a + button (new shell page, selected immediately),
    a bottom/right swap button for the whole panel (win.swap-panel), and
    each tab an X; shells survive hide/show and die with their tab. When
    the last page closes there is nothing left to show, so the owner hides
    (eventually: collapses) the strip — see "empty"."""

    __gsignals__ = {
        # Emitted when a page's X (or a shell exiting) removed the last page.
        "empty": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # Emitted when any page rings BEL, for the window's visual bell.
        "bell": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, shell_factory) -> None:
        """`shell_factory(number) -> PanelPage` builds the shell page the +
        button appends; *number* is the 1-based ordinal its title shows."""
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._shell_factory = shell_factory
        self._settings: dict | None = None  # last applied; new pages start from it
        self._ever_spawned = False
        self._next_number = 1  # "Terminal N" titles; resets when the strip empties
        self._cwd_lookup = None  # () -> agent cwd, set by the owning tab
        self._close_ok: set[Adw.TabPage] = set()  # busy closes the user confirmed
        self._close_asking: set[Adw.TabPage] = set()  # a confirm dialog is up

        self._view = Adw.TabView(vexpand=True)
        self._view.connect("close-page", self._on_close_page)
        # Clicking a tab should land the cursor in its page.
        self._view.connect("notify::selected-page", self._on_selected)

        # Right-clicking a tab: bulk closes, on top of the X each tab already
        # carries. Which tab was clicked arrives through "setup-menu" — the
        # editor file strip's pattern (see EditorPane). Move/split items
        # arrive with the dock; this menu is their future home.
        self._menu_page: Adw.TabPage | None = None
        tab_menu = Gio.Menu()
        tab_menu.append(_("Close Tab"), "strip.close-tab")
        tab_menu.append(_("Close other tabs"), "strip.close-other-tabs")
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
        swap_btn = Gtk.Button(icon_name="object-rotate-right-symbolic")
        swap_btn.add_css_class("flat")
        swap_btn.set_tooltip_text(_("Move terminal panel bottom/right"))
        swap_btn.set_action_name("win.swap-panel")
        end_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        end_box.append(add_btn)
        end_box.append(swap_btn)
        bar.set_end_action_widget(end_box)

        self.append(bar)
        self.append(self._view)

    # -- pages -------------------------------------------------------------

    @property
    def ever_spawned(self) -> bool:
        """A shell ran in some page at some point in this session tab's life."""
        return self._ever_spawned

    @property
    def page_count(self) -> int:
        return self._view.get_n_pages()

    def pages(self) -> list:
        """Every page widget, in tab order."""
        return [
            self._view.get_nth_page(i).get_child() for i in range(self._view.get_n_pages())
        ]

    def shell_pages(self) -> list:
        """The pages of kind shell, in tab order — the set the shell-wide
        aggregates (capture/clear/busy) range over."""
        return [page for page in self.pages() if page.page_kind == "shell"]

    def selected_page_widget(self):
        page = self._view.get_selected_page()
        return page.get_child() if page is not None else None

    def set_cwd_lookup(self, lookup) -> None:
        """`lookup() -> path` supplies the agent's current cwd for the shells
        the + button (and open) start."""
        self._cwd_lookup = lookup

    def _cwd(self) -> str | None:
        return self._cwd_lookup() if self._cwd_lookup is not None else None

    def open(self, restore_texts: list[str] | None = None) -> None:
        """Make sure at least one shell page exists and points at the agent's
        cwd. `restore_texts` (first open only) recreates one shell per saved
        panel history, oldest first."""
        if self.page_count == 0:
            for text in restore_texts or [None]:
                self.new_shell(restore_text=text, select=False)
            self._view.set_selected_page(self._view.get_nth_page(0))
        else:
            cwd = self._cwd()
            for shell in self.shell_pages():
                shell.open_shell(cwd)

    def new_shell(self, restore_text: str | None = None, select: bool = True):
        """Append a shell page (its shell spawns right away) and optionally
        select it. `restore_text` seeds the scrollback (session restore)."""
        shell = self._shell_factory(self._next_number)
        self._next_number += 1
        if self._settings is not None:
            shell.apply_settings(self._settings)
        shell.connect("shell-exited", self._on_shell_exited)
        shell.connect("bell", lambda *_: self.emit("bell"))
        page = self._view.append(shell)
        page.set_title(shell.page_title())
        icon = shell.page_icon()
        if icon:
            page.set_icon(Gio.ThemedIcon.new(icon))
        shell.open_shell(self._cwd(), restore_text)
        self._ever_spawned = True
        if select:
            self._view.set_selected_page(page)
        return shell

    def _find_page(self, widget) -> Adw.TabPage | None:
        for i in range(self._view.get_n_pages()):
            page = self._view.get_nth_page(i)
            if page.get_child() is widget:
                return page
        return None

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
        if page not in self._close_ok and widget.page_busy():
            # The X on a busy page asks first, mirroring the session tab's
            # own close protection — a build shouldn't die to a stray click.
            view.close_page_finish(page, False)  # keep the tab while we ask
            if page not in self._close_asking:
                self._ask_close_busy(page)
            return True
        self._close_ok.discard(page)
        view.close_page_finish(page, True)
        if view.get_n_pages() == 0:
            self._next_number = 1  # an empty strip restarts the numbering
            self.emit("empty")
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

    def _on_setup_menu(self, view: Adw.TabView, page: Adw.TabPage | None) -> None:
        """The menu is opening on *page* — or closing, which is a None page
        and leaves the stashed one alone: the action fires after the popover
        is gone, and it still has to know which tab it was opened on. An item
        that would close nothing is greyed out rather than left a no-op."""
        if page is None:
            return
        self._menu_page = page
        self._tab_actions["close-other-tabs"].set_enabled(view.get_n_pages() > 1)

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
        """Close every page but the menu's own — through close_page, so each
        busy page still gets its own confirmation."""
        keep = self._menu_target_page()
        if keep is None:
            return
        for widget in self.pages():
            page = self._find_page(widget)
            if page is not None and page is not keep:
                self._view.close_page(page)

    # -- focus and aggregates ------------------------------------------------

    def _on_selected(self, *_args) -> None:
        widget = self.selected_page_widget()
        if widget is not None and self.get_mapped():
            GLib.idle_add(widget.grab_page_focus)

    def grab_page_focus(self) -> None:
        """Land the cursor in the selected page."""
        widget = self.selected_page_widget()
        if widget is not None:
            widget.grab_page_focus()

    def has_page_focus(self) -> bool:
        return any(page.has_page_focus() for page in self.pages())

    def has_running_command(self) -> bool:
        return any(shell.page_busy() for shell in self.shell_pages())

    def select_busy_tab(self) -> None:
        """Bring the first shell with a live command to the front (the close
        confirmation shows the panel to reveal what's about to be killed)."""
        for shell in self.shell_pages():
            if shell.page_busy():
                page = self._find_page(shell)
                if page is not None:
                    self._view.set_selected_page(page)
                return

    def capture_all(self) -> list[str]:
        """Each shell page's scrollback text, in tab order."""
        return [shell.capture_contents() for shell in self.shell_pages()]

    def clear_all(self) -> None:
        for shell in self.shell_pages():
            shell.clear()

    def apply_settings(self, settings: dict) -> None:
        self._settings = settings
        for page in self.pages():
            page.apply_settings(settings)
