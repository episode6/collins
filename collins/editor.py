# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The built-in editor panel: per-tab syntax-highlighted editing with a
project file tree, real saving, and external-change handling.

GtkSourceView 5 is a hard dependency — the PR view's diff rendering builds
on it too, so nothing degrades without it. A missing typelib exits with an
install hint instead of a traceback (the `.deb` and the AUR package both
pull it in; only a source checkout can hit this).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

try:
    gi.require_version("GtkSource", "5")
    from gi.repository import GtkSource
except (ValueError, ImportError):
    raise SystemExit(
        "Collins requires GtkSourceView 5, which isn't installed. Install it "
        "(Debian/Ubuntu: gir1.2-gtksource-5, Fedora/Arch: gtksourceview5) "
        "and relaunch."
    ) from None

from . import (  # noqa: E402
    animatedimage,
    contextmenu,
    dialogs,
    editorfiles,
    fileclipboard,
    keybindings,
    keymap,
    openwith,
    openwithrows,
    paneldnd,
    remotefiles,
)
from .api import protocol  # noqa: E402
from .api.protocol import RequestRefused  # noqa: E402
from .filetree import FileTree  # noqa: E402
from .i18n import _, ngettext  # noqa: E402

log = logging.getLogger(__name__)

_MAX_RECENT_FILES = 20  # cap on files a session's editor_state remembers
_MAX_AGENT_FILES = 8  # rows in the "agent files" list pinned above the tree
# A fresh open refused on the load's worker: the file resolves outside the
# pane's root (`fs.stat`'s `inside`, PR-2.4). A marker, never shown: the
# banner says `_outside_message`.
_OUTSIDE_MSGID = "outside-the-editor-root"
# The debounce between a file-monitor event and the check of the file lives
# on the service now (service.files.WATCH_DEBOUNCE_MS, PR-2.3): one
# `file-changed` per burst of an agent's quick writes.
_TREE_INITIAL_WIDTH = 180
# The pane's own floor once it can show one column at a time. The breakpoint
# bin doesn't report its child's minimum (see __init__), so this is the
# narrowest the editor column can be dragged — MIN_SPLIT_SIZE's twin.
_PANE_MIN_WIDTH = 240
_PANE_MIN_HEIGHT = 120  # the bin wants a floor on both axes, or it warns


def _narrow_condition(width: int) -> Adw.BreakpointCondition:
    """The breakpoint below which the pane shows one column at a time.
    `max-width: 0px` never matches (a mapped pane is at least a pixel
    wide), which is what the setting's `0 = never` means."""
    return Adw.BreakpointCondition.parse(f"max-width: {max(0, width)}px")


def style_scheme(setting: str, dark: bool) -> GtkSource.StyleScheme | None:
    """The GtkSource style scheme *setting* names — or, for "" (follow the
    app), the Adwaita scheme matching *dark*. Shared with the PR view's diff
    rendering, so an editor scheme choice restyles those buffers too."""
    manager = GtkSource.StyleSchemeManager.get_default()
    return manager.get_scheme(setting or ("Adwaita-dark" if dark else "Adwaita"))


class _OpenFile:
    """One open buffer, independent of whether its tab-strip page is the one
    currently showing."""

    def __init__(self, path: Path, buffer, view) -> None:
        self.path = path
        self.buffer = buffer
        self.view = view
        # The file as the service last read or wrote it for this buffer
        # (PR-2.3): the mtime a save sends as `expect_mtime` (a moved one
        # is refused `stale`: the "changed on disk" dialog), the encoding
        # the save writes back, and the handle of the service's watch.
        self.mtime: int | None = None
        self.size: int | None = None
        self.encoding = "utf-8"
        # The line ending a save puts back when the buffer's implicit
        # trailing newline is on (what `_fill` stripped).
        self.newline = "\n"
        # Whether the buffer has ever held the file: a save before the
        # first fill would write an empty buffer over it (the view is not
        # editable until then either).
        self.filled = False
        self.watch_handle: str | None = None
        # A `file-changed` that arrived while a save or load of this buffer
        # was in flight: judged against the new mtime once that lands.
        self.pending_change: dict | None = None
        self.saving = False
        # A save asked for while one was in flight: sent from _on_saved
        # with the new mtime, its waiters answered with that save's.
        self.save_again: list | None = None
        self.font_provider: Gtk.CssProvider | None = None
        # Which load this buffer is waiting on, and where to put the cursor
        # when it lands. Bumped per load so a superseded one (a rename that
        # restarted it) can be told apart from the live one — see _start_load.
        self.load_id = 0
        self.loading = False
        self.reloading = False  # the load in flight refills a buffer that already showed the file
        self.pending_cursor: list | None = None


class EditorPane(Gtk.Box):
    """One per `TerminalTab`. Hidden by default; the tab shows/hides it, and
    can pop the live pane out into an `EditorWindow` (editorwindow.py) —
    reparented, so buffers and dirty state travel with it."""

    __gsignals__ = {
        # The status row's detach button was clicked: whoever hosts the pane
        # (the window, via the tab) should reparent it into its own window.
        "request-pop-out": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # "Add to chat" was picked somewhere in the pane: reference this file
        # in the tab's agent chat. Payload is (absolute path, start line,
        # end line) — 1-based inclusive lines, (0, 0) meaning the whole file.
        # The tab turns it into the agent's mention token and types it (see
        # TerminalTab._on_editor_add_to_chat); the pane deliberately knows
        # nothing about any agent's syntax.
        "add-to-chat": (GObject.SignalFlags.RUN_FIRST, None, (str, int, int)),
        # The pane is now rooted at a different project directory, having
        # followed the session's working directory somewhere new. Payload is
        # the new root; the tab re-points what it roots at the editor (bare
        # root-name links, quick open) and a popped-out window re-titles.
        "root-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, root: str | Path) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._root = Path(root)
        self._open: dict[str, _OpenFile] = {}  # path str -> _OpenFile
        self._watched: dict[str, _OpenFile] = {}  # a watch's handle -> the file it watches
        self._shut = False  # `shutdown` ran: a read or write landing later installs nothing
        self._pages: dict[str, Adw.TabPage] = {}
        self._page_key: dict[Adw.TabPage, str] = {}
        self._close_confirmed: set[Adw.TabPage] = set()  # discard-changes already agreed to
        self._style_scheme_setting = ""  # "" = follow the app's own light/dark scheme
        self._show_line_numbers = True
        self._font = ""
        self._banner_click_id: int | None = None
        self._reroot_asking = False  # the follow-the-working-directory dialog is up
        self._reroot_queued: Path | None = None  # a move that arrived while it was
        # A re-root's stats are the service's (PR-2.4): the one in flight,
        # by number, so a newer request_root supersedes it.
        self._reroot_seq = 0
        # How a queued move is judged again (`_follow_queued`): the tab's
        # session's `cwd.settle` with `judge` (`set_follow_judge`); None for
        # a pane with no session behind it, which only offers.
        self._follow_judge = None
        # The Agent files list's check (`set_agent_files`): one `fs.stat`
        # pass in flight at a time, the latest paths kept for the next.
        self._agent_wanted: list[str] = []
        self._agent_checking = False
        self._agent_again = False
        # Image pages being opened: their `fs.stat` is in flight.
        self._image_opening: set[str] = set()
        self._active_search_context: GtkSource.SearchContext | None = None
        self._last_match: tuple[int, int] | None = None  # (start, end) char offsets
        # One column at a time (see _sync_layout): whether the pane is
        # currently narrower than the editor_narrow_width setting, and
        # whether the back button beside the tabs asked for the picker
        # since a file was last opened.
        self._narrow = False
        self._picker_requested = False

        style_manager = Adw.StyleManager.get_default()
        self._dark = style_manager.get_dark()
        style_manager.connect("notify::dark", self._on_style_changed)

        self._banner = Adw.Banner()
        self.append(self._banner)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True)
        paned.set_wide_handle(True)
        paned.set_position(_TREE_INITIAL_WIDTH)
        # The pane is built hidden (terminal.py shows it on demand), and a
        # position set while the paned has no allocation is clamped away, so
        # the tree came up squeezed to its minimum — icons with no room for
        # names. Re-assert the default once, from an idle after the first
        # map, when the real allocation exists; later maps leave whatever
        # width the user has dragged it to. Same first-show race as the
        # outer editor paned's apply-pending gate (see PanedSizer.apply).
        map_handler: list[int] = []

        def _on_first_map(widget: Gtk.Paned) -> None:
            widget.disconnect(map_handler[0])
            GLib.idle_add(widget.set_position, _TREE_INITIAL_WIDTH)

        map_handler.append(paned.connect("map", _on_first_map))

        self._tree = FileTree(self._root)
        self._tree.connect("open-file", lambda _t, path: self.open_file(path))
        self._tree.connect("add-to-chat", lambda _t, path: self.emit("add-to-chat", path, 0, 0))
        self._tree.connect("rename-request", lambda _t, path, is_dir: self._prompt_rename(path, is_dir))
        self._tree.connect("paste-request", lambda _t, dest: self._paste_into(dest))
        self._tree.connect("open-with-request", lambda _t, path, app_id: self._open_file_with(path, app_id))
        # The footer_apps setting (apply_settings), for the Agent files rows'
        # "Open In…" submenu; the tree keeps its own copy.
        self._footer_apps: list[str] = []
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        left.append(self._build_agent_files())
        left.append(self._tree)
        self._left = left
        paned.set_start_child(left)
        paned.set_resize_start_child(False)
        paned.set_shrink_start_child(False)

        editors = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self._tab_view = Adw.TabView()
        self._tab_view.connect("close-page", self._on_tabview_close_page)
        self._tab_view.connect("notify::selected-page", lambda *_a: self._on_page_switched())
        # Right-clicking a tab: the bulk closes, on top of the X each tab
        # already carries. Which tab was clicked arrives through "setup-menu"
        # — the same pattern as the window's session tabs (see
        # MainWindow._on_tab_setup_menu).
        self._menu_page: Adw.TabPage | None = None
        tab_menu = Gio.Menu()
        tab_menu.append(_("Close other tabs"), "editor.close-other-tabs")
        tab_menu.append(_("Close tabs to the right"), "editor.close-tabs-to-the-right")
        tab_menu.append(_("Close all tabs"), "editor.close-all-tabs")
        self._tab_view.set_menu_model(tab_menu)
        self._tab_view.connect("setup-menu", self._on_tab_setup_menu)
        # Native tab DnD is process-global: without this, a panel-strip
        # shell tab could be dropped onto the file tab bar — or a file tab
        # dragged into a strip (tabguard). Each editor is its own group.
        paneldnd.guard_view(self._tab_view, self)
        tab_bar = Adw.TabBar(view=self._tab_view, autohide=False)
        self._tab_bar = tab_bar
        # Narrow pane, file on show: the way back to the picker, in the
        # tab bar's own slot for "a widget left of the tabs". Hidden while
        # both columns are up (see _sync_layout).
        self._back_btn = Gtk.Button(icon_name="go-previous-symbolic", visible=False)
        self._back_btn.add_css_class("flat")
        self._back_btn.set_tooltip_text(_("Back to files"))
        self._back_btn.connect("clicked", lambda *_a: self._show_picker())
        tab_bar.set_start_action_widget(self._back_btn)
        double_click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        double_click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        double_click.connect("pressed", self._on_tab_bar_pressed)
        tab_bar.add_controller(double_click)
        editors.append(tab_bar)
        self._search_bar = self._build_search_bar()
        editors.append(self._search_bar)
        editors.append(self._tab_view)
        self._editors = editors
        paned.set_end_child(editors)
        paned.set_resize_end_child(True)
        paned.set_shrink_end_child(False)
        # Closing the last tab in a narrow pane leaves nothing to look at
        # but an empty tab bar, so it goes back to the picker. Watched on
        # n-pages rather than close-page: that handler runs before the page
        # is actually gone (and may hold it open to ask about unsaved
        # changes), so counting there would be one too many.
        self._tab_view.connect("notify::n-pages", lambda *_a: self._on_n_pages_changed())

        # Below editor_narrow_width the paned shows one child at a time —
        # a Gtk.Paned hands its whole allocation to its only visible child,
        # handle and all, and leaves `position` alone, so the tree width the
        # user dragged survives a trip through the narrow layout. The width
        # is judged by an Adw.BreakpointBin around the paned: its
        # apply/unapply arrive from the bin's own allocation, the one place
        # a layout switch can be made without a size-allocate hack or a
        # settle timer. The bin reports the size request as its minimum
        # rather than its child's, which is also what lets the column be
        # dragged below what two side-by-side columns would demand.
        self._breakpoint_bin = Adw.BreakpointBin(child=paned, vexpand=True)
        self._breakpoint_bin.set_size_request(_PANE_MIN_WIDTH, _PANE_MIN_HEIGHT)
        self._breakpoint = Adw.Breakpoint.new(_narrow_condition(0))
        self._breakpoint.connect("apply", lambda *_a: self._on_narrow(True))
        self._breakpoint.connect("unapply", lambda *_a: self._on_narrow(False))
        self._breakpoint_bin.add_breakpoint(self._breakpoint)

        self.append(self._breakpoint_bin)
        self.append(self._build_status_row())
        self._install_actions()

    # -- one column at a time ------------------------------------------------

    def _on_narrow(self, narrow: bool) -> None:
        self._narrow = narrow
        self._sync_layout()

    def _sync_layout(self) -> None:
        """Show the columns `editorfiles.pane_layout` says this pane has
        room for. Idempotent, and cheap when nothing changes: GTK ignores a
        visibility set to what it already is."""
        layout = editorfiles.pane_layout(
            self._narrow, self._tab_view.get_n_pages(), self._picker_requested
        )
        self._left.set_visible(layout is not editorfiles.PaneLayout.FILES)
        self._editors.set_visible(layout is not editorfiles.PaneLayout.PICKER)
        self._back_btn.set_visible(layout is editorfiles.PaneLayout.FILES)

    def _on_n_pages_changed(self) -> None:
        self._sync_layout()
        # Closing the last tab in a narrow pane lands on the picker with the
        # view that had focus freshly hidden — hand focus to the tree, the
        # way the back button does, rather than letting it fall wherever
        # GTK drops it. Opens (0 -> 1) leave focus to the load path.
        if self._narrow and self._tab_view.get_n_pages() == 0:
            self._tree.grab_focus()

    def _show_picker(self) -> None:
        """The back button: the picker instead of the open file, until the
        next file is opened. Focus goes with it — the view that had it is
        about to be hidden."""
        self._picker_requested = True
        self._sync_layout()
        self._tree.grab_focus()

    def _select_page(self, page: Adw.TabPage) -> None:
        """Bring *page* to the front — and, in a narrow pane, the editor
        column with it. Every successful open lands here; the guards that
        refuse a file (outside the project, binary, undecodable) don't, so a
        refused open leaves the picker where it was."""
        self._tab_view.set_selected_page(page)
        self._picker_requested = False
        self._sync_layout()

    # -- widget-local actions ------------------------------------------------

    def _install_actions(self) -> None:
        """A `"editor"` action group, the same pattern as the terminal's
        `"term"` group (see terminal.py's context-menu setup). Bound through a
        BUBBLE-phase controller on the pane itself — never the window's
        CAPTURE-phase one, which would steal Ctrl+S from the agent terminal
        whenever the editor isn't what's focused."""
        actions = Gio.SimpleActionGroup()
        save_action = Gio.SimpleAction.new("save", None)
        save_action.connect("activate", lambda *_a: self.save_current())
        actions.add_action(save_action)
        find_action = Gio.SimpleAction.new("find", None)
        find_action.connect("activate", lambda *_a: self.toggle_search())
        actions.add_action(find_action)
        add_to_chat = Gio.SimpleAction.new("add-to-chat", None)
        add_to_chat.connect("activate", lambda *_a: self._add_selection_to_chat())
        actions.add_action(add_to_chat)
        # The agent-files rows' context-menu action; the clicked row's path
        # is stashed by the right-click handler just before its popover opens.
        self._menu_file_path = ""
        add_file = Gio.SimpleAction.new("add-file-to-chat", None)
        add_file.connect("activate", lambda *_a: self._add_menu_file_to_chat())
        actions.add_action(add_file)
        open_file_with = Gio.SimpleAction.new("open-file-with", GLib.VariantType.new("s"))
        open_file_with.connect(
            "activate", lambda _a, param: self._open_file_with(self._menu_file_path, param.get_string())
        )
        actions.add_action(open_file_with)
        # The tab context menu's bulk closes. Kept to hand so `setup-menu` can
        # grey out the ones that would close nothing for the clicked tab.
        self._tab_actions: dict[str, Gio.SimpleAction] = {}
        for name, handler in (
            ("close-other-tabs", self._close_other_tabs),
            ("close-tabs-to-the-right", self._close_tabs_to_the_right),
            ("close-all-tabs", self._close_all_tabs),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, run=handler: run())
            actions.add_action(action)
            self._tab_actions[name] = action
        self.insert_action_group("editor", actions)

        # Appended by GtkTextView to every source view's built-in context
        # menu (see open_file); one model shared by all of them.
        self._view_extra_menu = Gio.Menu()
        self._view_extra_menu.append(_("Add to chat"), "editor.add-to-chat")

        # The `editor.*` chords; rebuilt by apply_keybindings when the
        # Keyboard Bindings dialog changes them.
        self._shortcut_controller: Gtk.ShortcutController | None = None
        self.apply_keybindings(keybindings.current())

    def apply_keybindings(self, custom) -> None:
        """Bind the editor's chords from the keybindings catalogue with
        *custom* (the "keybindings" setting) applied."""
        if self._shortcut_controller is not None:
            self.remove_controller(self._shortcut_controller)
        self._shortcut_controller = keymap.shortcut_controller(
            custom, "editor", Gtk.PropagationPhase.BUBBLE
        )
        self.add_controller(self._shortcut_controller)

    # -- tab-bar double-click ------------------------------------------------

    def _on_tab_bar_pressed(self, _gesture, n_press: int, x: float, y: float) -> None:
        """Double-clicking a tab reveals its file in the tree. Observed from
        a capture-phase gesture that never claims the press, so the tab bar's
        own selection and drag-reorder handling keep working underneath."""
        if n_press != 2:
            return
        page = self._tab_page_at(x, y)
        key = self._page_key.get(page) if page is not None else None
        if key is not None:
            if self._narrow:
                self._show_picker()  # a reveal in a hidden tree shows nothing
            self._tree.reveal(key)

    def _tab_page_at(self, x: float, y: float) -> Adw.TabPage | None:
        """The page whose tab-bar header sits under (*x*, *y*), or None for
        the bar's empty space. Walks up from the picked widget to the
        enclosing "AdwTab" — the same private-libadwaita-type dependency as
        MainWindow._tab_widget, tolerated for the same reason: a rename in a
        libadwaita bump makes this return None and the double-click quietly
        does nothing."""
        widget = self._tab_bar.pick(x, y, Gtk.PickFlags.DEFAULT)
        while widget is not None and widget is not self._tab_bar:
            if widget.__gtype__.name == "AdwTab":
                return widget.get_property("page")
            widget = widget.get_parent()
        return None

    # -- tab context menu ----------------------------------------------------

    def _on_tab_setup_menu(self, view: Adw.TabView, page: Adw.TabPage | None) -> None:
        """The menu is opening on *page* — or closing, which is a None page
        and leaves the stashed one alone: the action fires after the popover
        is gone, and it still has to know which tab it was opened on.

        An item that would close nothing is greyed out rather than left to be
        a no-op click: with one file open, "close other tabs" has nothing to
        act on, and neither has "close tabs to the right" on the last tab."""
        if page is None:
            return
        self._menu_page = page
        position = view.get_page_position(page)
        n_pages = view.get_n_pages()
        self._tab_actions["close-other-tabs"].set_enabled(n_pages > 1)
        self._tab_actions["close-tabs-to-the-right"].set_enabled(position < n_pages - 1)

    def _menu_target_page(self) -> Adw.TabPage | None:
        """The tab the context menu was opened on, falling back to the visible
        one — a page closed since the menu opened is no longer this view's."""
        page = self._menu_page
        if page is not None and page in self._page_key:
            return page
        return self._tab_view.get_selected_page()

    def _close_other_tabs(self) -> None:
        page = self._menu_target_page()
        if page is not None:
            self._tab_view.close_other_pages(page)

    def _close_tabs_to_the_right(self) -> None:
        page = self._menu_target_page()
        if page is not None:
            self._tab_view.close_pages_after(page)

    def _close_all_tabs(self) -> None:
        # Back to front, the way libadwaita's own bulk closes walk: a page
        # that closes takes the positions above it with it, never one this
        # loop still has to reach. Each close goes through close-page, so a
        # file with unsaved changes still gets its own ask (and a Cancel
        # there only keeps that one file, not the rest).
        for position in reversed(range(self._tab_view.get_n_pages())):
            self._tab_view.close_page(self._tab_view.get_nth_page(position))

    # -- renaming --------------------------------------------------------------

    def _prompt_rename(self, path: str, is_dir: bool) -> None:
        """The file tree's "Rename…", asked and then carried out here: the
        pane is what knows whether the thing being renamed is open in a tab."""
        dialogs.rename_path_dialog(
            self.get_root(),
            Path(path).name,
            is_dir,
            lambda name: self._rename(path, name),
        )

    def _rename(self, path: str, new_name: str) -> None:
        """The rename, on the service (`fs.rename`, §3.23): the name is
        checked here first (`rename_name_error`, pure: an empty or
        path-shaped name never makes a round trip), the rest — the entry
        is there, the name is free, both inside the root — on the service,
        which does the rename and answers the renamed file's mtime."""
        name = new_name.strip()
        verdict = editorfiles.rename_name_error(path, name)
        if verdict is False:
            return  # the name came back unchanged
        if verdict is not None:
            self._notify(self._rename_error_message(Path(path).name, name, verdict))
            return
        target = str(Path(path).parent / name)
        root = str(self._root)

        def land(kind: str, value) -> None:
            if self._shut:
                return
            if kind != "ok":
                reason = remotefiles.rename_reason(value)
                if reason is not None:
                    self._notify(self._rename_error_message(Path(path).name, name, reason))
                else:
                    self._notify(
                        _("Couldn't rename {name}: {message}").format(
                            name=Path(path).name, message=remotefiles.refusal_words(value)
                        )
                    )
                return
            self._retarget_open(Path(path), Path(target), mtime=value)
            # The old path is now nothing: a directory renamed out from under the
            # tree leaves its rows and its monitor watching a name that's gone.
            self._tree.forget_dir(path)
            self._tree.refresh_dir(Path(target).parent)
            self._tree.reveal(Path(target))

        self._off_main(lambda: remotefiles.rename_path(path, target, root), land)

    def _rename_error_message(self, name: str, new_name: str, error) -> str:
        RE = editorfiles.RenameError
        if error is RE.EMPTY:
            return _("A name is needed to rename {name}.").format(name=name)
        if error is RE.NOT_A_NAME:
            return _("“{new_name}” isn't a name — renaming can't move things elsewhere.").format(
                new_name=new_name
            )
        if error is RE.EXISTS:
            return _("“{new_name}” already exists here.").format(new_name=new_name)
        if error is RE.MISSING:
            return _("{name} is no longer there.").format(name=name)
        return _("{name} can't be renamed to something outside this project.").format(name=name)

    # -- pasting ---------------------------------------------------------------

    def _paste_into(self, dest_dir: str) -> None:
        """The file tree's "Paste", carried out here rather than in the tree:
        a cut pasted somewhere else *moves* files, and an open one has to
        follow its file exactly as it does through a rename."""
        fileclipboard.read_files(
            self.get_clipboard(), lambda paths, cut: self._paste(dest_dir, paths, cut)
        )

    def _paste(self, dest_dir: str, paths: list[str], move: bool) -> None:
        """*paths* as the clipboard handed them over — possibly several, and
        possibly from outside the project (a copy taken in a file manager is
        exactly what paste is for, on a local client; the service refuses a
        source outside every root it knows for any other). Nothing is ever
        overwritten: a name already taken lands as "name (copy)" instead.
        The copies and moves are the service's (`fs.paste`, §3.23)."""
        if not paths:
            self._notify(_("There's nothing on the clipboard to paste here."))
            return
        root = str(self._root)

        def land(kind: str, value) -> None:
            if self._shut:
                return
            if kind != "ok":
                self._notify(_("Couldn't paste: {message}").format(message=remotefiles.refusal_words(value)))
                return
            self._pasted(dest_dir, value, move)

        self._off_main(lambda: remotefiles.paste_files(paths, dest_dir, move, root), land)

    def _pasted(self, dest_dir: str, outcomes: list[remotefiles.PasteOutcome], move: bool) -> None:
        """What the service's paste did: the tree refreshed where things
        landed and left, an open file that moved re-keyed, the cut spent."""
        pasted = [outcome for outcome in outcomes if outcome.target is not None]
        for outcome in pasted:
            if not move:
                continue
            self._retarget_open(Path(outcome.source), Path(outcome.target))
            # What moved is now nothing: a directory moved out from under the
            # tree leaves its rows and its monitor watching a path that's gone.
            self._tree.forget_dir(outcome.source)
            self._tree.refresh_dir(Path(outcome.source).parent)
        self._tree.refresh_dir(dest_dir)
        if len(pasted) == 1:
            self._tree.reveal(Path(pasted[0].target))
        if move and pasted:
            self._spend_cut(outcomes)
        failed = [outcome for outcome in outcomes if outcome.error is not None]
        if failed:
            self._notify(self._paste_error_message(failed))

    def _spend_cut(self, outcomes: list[remotefiles.PasteOutcome]) -> None:
        """What is left of a cut once a paste of it has moved what it could.
        A cut is spent the moment it lands — what it named isn't there any
        more, so a second paste could only report it missing — but only for
        what actually landed. Anything that failed (a permission error, a
        folder refused into itself) is still sitting where it was, and
        clearing the clipboard wholesale would mean going back to cut it
        again. Those stay on it, still cut, so the paste can be retried."""
        clipboard = self.get_clipboard()
        if clipboard.get_content() is None:
            return  # someone else's cut — not ours to rewrite
        still_cut = [
            outcome.source
            for outcome in outcomes
            if outcome.target is None and outcome.error is not editorfiles.PasteError.MISSING
        ]
        if still_cut:
            fileclipboard.set_files(clipboard, still_cut, cut=True)
        else:
            clipboard.set_content(None)

    def _paste_error_message(self, failed: list[remotefiles.PasteOutcome]) -> str:
        """Why a paste didn't happen — named, the way the rename errors are.
        Several at once (a clipboard full of files, half of them gone) is
        counted rather than listed: a banner is one line."""
        if len(failed) > 1:
            return ngettext(
                "{count} item couldn't be pasted.",
                "{count} items couldn't be pasted.",
                len(failed),
            ).format(count=len(failed))
        outcome = failed[0]
        name = Path(outcome.source).name
        PE = editorfiles.PasteError
        if outcome.error is PE.MISSING:
            return _("{name} is no longer there.").format(name=name)
        if outcome.error is PE.INTO_ITSELF:
            return _("{name} can't be pasted into itself.").format(name=name)
        if outcome.error is PE.NOT_A_DIR:
            # Names the destination, which the outcome doesn't carry — and
            # which the tree has stopped showing anyway.
            return _("That folder is no longer there.")
        if outcome.error is PE.OUTSIDE:
            return _("{name} can't be pasted outside this project.").format(name=name)
        if outcome.error is PE.SOURCE_OUTSIDE:
            return _("{name} is outside every project the service knows.").format(name=name)
        if outcome.error is PE.NO_ROOM:
            return _("There are already too many copies of {name} here.").format(name=name)
        return _("Couldn't paste {name}: {message}").format(name=name, message=outcome.message)

    def _retarget_open(self, old: Path, new: Path, mtime: int | None = None) -> None:
        """Follow a rename through the open tabs: the renamed file — or every
        open file inside a renamed folder — keeps its buffer, its unsaved
        changes and its place in the strip, now pointed at the new path.
        Reopening it as a fresh tab instead would throw away edits that
        haven't been saved yet. *mtime* is the renamed file's as the service
        answered it (`fs.rename`): the open file at *new* takes it, so the
        next save expects the file as it is."""
        for key in list(self._pages):
            moved = editorfiles.renamed_path(old, new, key)
            if moved is None or moved == key:
                continue
            page = self._pages.pop(key)
            self._pages[moved] = page
            self._page_key[page] = moved
            page.set_tooltip(moved)
            opened = self._open.pop(key, None)
            if opened is None:  # an image page: no buffer, nothing else to move
                page.set_title(Path(moved).name)
                continue
            opened.path = Path(moved)
            # A save writes to `opened.path`, so this is what keeps Ctrl+S
            # from recreating the old name; a rename keeps the file's mtime,
            # so the one the next save expects stays — unless the service
            # answered another, which is then the truth.
            if mtime is not None and Path(moved) == new and opened.mtime != mtime:
                opened.mtime = mtime
            if opened.loading:
                # The load in flight is reading the old path, so it is
                # already doomed: start it again from the new one, which
                # supersedes it.
                self._start_load(opened, opened.pending_cursor, reload=opened.reloading)
            else:
                self._watch_external_changes(opened)
            self._open[moved] = opened
            page.set_title(("• " if opened.buffer.get_modified() else "") + opened.path.name)
        self._sync_status()

    # -- add to chat ---------------------------------------------------------

    def _add_selection_to_chat(self) -> None:
        """The source view context menu's "Add to chat": reference the
        selection, or the whole file when nothing is selected.

        A partial-line selection is rounded outward to whole lines — the
        agent's range syntax has no column precision, and a column suffix is
        worse than none (see ClaudeProvider.file_reference: the range would
        be silently dropped). One trim on the way out: a selection ending at
        column 0 stops short of that line, so dragging through a trailing
        newline doesn't pull the next line into the reference."""
        opened = self._active_open()
        if opened is None:
            return
        start_line = end_line = 0
        bounds = opened.buffer.get_selection_bounds()
        if bounds:
            start, end = bounds
            start_line = start.get_line() + 1
            end_line = end.get_line() + 1
            if end.get_line_offset() == 0 and end_line > start_line:
                end_line -= 1
        self.emit("add-to-chat", str(opened.path), start_line, end_line)

    def _add_menu_file_to_chat(self) -> None:
        if self._menu_file_path:
            self.emit("add-to-chat", self._menu_file_path, 0, 0)

    # -- agent files ---------------------------------------------------------

    def _build_agent_files(self) -> Gtk.Widget:
        """The "recently touched by the agent" list pinned above the file
        tree: fed by the tab's transcript tail (see set_agent_files), hidden
        until the session's agent has actually written something."""
        self._agent_paths: list[str] = []  # what the rows currently show
        self._agent_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False)

        header = Gtk.Label(label=_("Agent files"), xalign=0.0)
        header.add_css_class("caption-heading")
        header.add_css_class("dim-label")
        header.set_margin_top(8)
        header.set_margin_start(12)
        header.set_margin_bottom(2)
        self._agent_box.append(header)

        self._agent_list = Gtk.ListBox()
        self._agent_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._agent_list.add_css_class("navigation-sidebar")
        self._agent_list.connect("row-activated", self._on_agent_row)
        self._agent_box.append(self._agent_list)
        self._agent_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        return self._agent_box

    def set_agent_files(self, paths: list[str]) -> None:
        """Show *paths* (most recent first) in the agent-files list. Called on
        every transcript update, so it must be cheap: the check is one
        `fs.stat` per path on a worker thread (`_check_agent_files`), one
        pass at a time, a call meanwhile only remembered for the next.
        Drops anything outside the project or no longer a file on disk."""
        self._agent_wanted = list(paths)
        if self._agent_checking:
            self._agent_again = True
            return
        self._check_agent_files()

    def _check_agent_files(self) -> None:
        paths, root = list(self._agent_wanted), str(self._root)
        self._agent_checking = True

        def work() -> list[str]:
            shown: list[str] = []
            for path in paths:
                if len(shown) >= _MAX_AGENT_FILES:
                    break
                try:
                    found = remotefiles.stat_path(path, root)
                except RequestRefused as refusal:
                    if refusal.error == protocol.ERROR_GONE:
                        raise  # no service to ask: the list stays as it was
                    continue  # a path the service won't stat (not absolute): not a row
                if found.is_file and found.inside:
                    shown.append(str(Path(path)))
            return shown

        self._off_main(work, self._agent_files_checked)

    def _agent_files_checked(self, kind: str, value) -> None:
        self._agent_checking = False
        if self._shut:
            return
        if kind == "ok":
            self._show_agent_files(value)
        if self._agent_again:
            self._agent_again = False
            self._check_agent_files()

    def _show_agent_files(self, shown: list[str]) -> None:
        if shown == self._agent_paths:
            return
        self._agent_paths = shown
        self._agent_list.remove_all()
        for path in shown:
            self._agent_list.append(self._make_agent_row(path))
        self._agent_box.set_visible(bool(shown))

    def _make_agent_row(self, path: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.file_path = path
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        icon = Gtk.Image.new_from_icon_name("document-edit-symbolic")
        label = Gtk.Label(label=Path(path).name, xalign=0.0)
        label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        box.append(icon)
        box.append(label)
        row.set_child(box)
        try:
            row.set_tooltip_text(str(Path(path).relative_to(self._root)))
        except ValueError:
            row.set_tooltip_text(path)
        right_click = Gtk.GestureClick(button=3)
        right_click.connect("pressed", self._on_agent_row_right_click, row)
        row.add_controller(right_click)
        return row

    def _on_agent_row_right_click(
        self, gesture: Gtk.GestureClick, _n_press: int, x: float, y: float, row: Gtk.ListBoxRow
    ) -> None:
        """Add to chat and the "Open In…" submenu the file tree's file rows
        get (see FileTree `_on_right_click`) — these rows are files too, and
        a right-click working below the separator but not above it would
        read as broken."""
        path = getattr(row, "file_path", None)
        if not path:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._menu_file_path = path

        menu = Gio.Menu()
        menu.append(_("Add to chat"), "editor.add-file-to-chat")
        rows: list[Gtk.Widget] = []
        submenu = openwithrows.file_open_with_menu(rows, self._footer_apps, path, "editor.open-file-with")
        menu.append_submenu(_("Open In…"), submenu)
        popover = Gtk.PopoverMenu.new_from_model(menu)
        openwithrows.slot_them(popover, rows)
        contextmenu.popup_at(popover, row, x, y)

    def _open_file_with(self, path: str, app_id: str) -> None:
        """An "Open In…" pick from the tree or an Agent files row: the file
        handed to the footer app, or to the desktop's default app through
        xdg-open (openwith.open_file_with); a refusal shows in the banner,
        the pane's one line for saying something."""
        if not path:
            return
        failure = openwith.open_file_with(app_id, path)
        if failure:
            self._show_banner(failure, _("Dismiss"), lambda: None)

    def agent_file_open_with_labels(self, path: str) -> list[str]:
        """The labels an Agent files row's "Open In…" submenu would list for
        *path* (for the e2e)."""
        return [label for _id, _icon, label in openwith.file_open_with_entries(self._footer_apps, path)]

    def _on_agent_row(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        path = getattr(row, "file_path", None)
        if path:
            self.open_file(path)

    # -- opening files -----------------------------------------------------

    def open_file(self, path: str | Path, restore_cursor: list | None = None) -> None:
        path = Path(path)
        key = str(path)
        existing = self._pages.get(key)
        if existing is not None:
            self._select_page(existing)
            if restore_cursor:
                # A clicked `path:line` reference re-targets a page that is
                # already open (image pages have no buffer and stay put).
                opened = self._open.get(key)
                if opened is not None:
                    self._apply_cursor(opened, restore_cursor)
            return
        # Defense in depth behind the tree's own symlink filtering: every
        # caller (tree activation, session restore, future pop-out) funnels
        # through here, and a save would write through a symlink — so nothing
        # resolving outside the project may ever get a buffer. Whether it
        # does is the service's `fs.stat` (`inside` of this root, PR-2.4),
        # asked on the load's worker before the read: a fresh open of a
        # file outside closes its tab again with `_outside_message`.
        if editorfiles.is_image_path(path):
            self._open_image(key, path)
            return
        # The guards on what may be loaded (a regular file, under the size
        # cap, not binary) are the service's now: `fs.read` refuses or
        # flags, and _on_loaded tells the user (PR-2.3).

        buffer = GtkSource.Buffer()
        self._apply_scheme(buffer)

        view = GtkSource.View(buffer=buffer)
        view.set_show_line_numbers(self._show_line_numbers)
        view.set_highlight_current_line(True)
        view.set_monospace(True)
        view.set_auto_indent(True)
        view.set_extra_menu(self._view_extra_menu)
        view.set_editable(False)  # until the first fill: nothing typed into an empty buffer is saved

        opened = _OpenFile(path, buffer, view)
        self._apply_font(opened)
        self._open[key] = opened

        scrolled = Gtk.ScrolledWindow(child=view, vexpand=True, hexpand=True)
        page = self._tab_view.append(scrolled)
        page.set_title(path.name)
        page.set_tooltip(key)
        self._pages[key] = page
        self._page_key[page] = key

        # Carries the _OpenFile, not the key it was opened under: a rename
        # re-keys the page (see _retarget_open), and a handler holding the
        # old path would stop finding the tab it titles.
        buffer.connect("modified-changed", self._on_modified_changed, opened)
        buffer.connect("notify::cursor-position", lambda *_a: self._sync_status())

        self._start_load(opened, restore_cursor)

        self._select_page(page)

    def _start_load(self, opened: _OpenFile, restore_cursor: list | None, reload: bool = False) -> None:
        """(Re)fill the buffer from the file at `opened.path`, read on the
        service (`fs.read`, PR-2.3) from a worker thread; the text lands in
        `_on_loaded` at PRIORITY_DEFAULT.

        Carries the `_OpenFile` rather than the path it was opened under: a
        rename re-keys the page while a big file is still loading, and a
        completion holding the old path would find nothing to finish. That
        rename also makes the read in flight one of the wrong path —
        `_retarget_open` starts a fresh one, and `load_id` is what makes the
        superseded one's answer a no-op instead of a banner. *reload* says
        the buffer already showed the file (a failure keeps the page)."""
        opened.load_id += 1
        opened.loading = True
        opened.reloading = reload
        opened.pending_cursor = restore_cursor
        load_id = opened.load_id
        path = str(opened.path)
        root = str(self._root)

        def work() -> remotefiles.FileText:
            # A fresh open is confined to this pane's root (`open_file`'s
            # guard); a reload is not: a file left open outside the root
            # by a re-root goes on reloading from where it is.
            if not reload:
                found = remotefiles.stat_path(path, root)
                # Nothing there is the read's `gone` to say, not "outside"
                # (the service answers `inside: false` for a missing path).
                if found.kind != "missing" and not found.inside:
                    raise RequestRefused(protocol.ERROR_REFUSED, _OUTSIDE_MSGID, {})
            return remotefiles.read(path)

        self._off_main(work, lambda kind, value: self._on_loaded(opened, load_id, kind, value))

    @staticmethod
    def _off_main(work, land) -> None:
        """*work* on a daemon thread (a blocking call of the service's);
        `land(kind, value)` on the main loop at PRIORITY_DEFAULT with
        ``("ok", result)`` or ``("refused", RequestRefused)``."""

        def run() -> None:
            try:
                result = ("ok", work())
            except RequestRefused as refusal:
                result = ("refused", refusal)
            except Exception as exc:  # the link gone mid-call, a bug: a banner, never a hang
                log.exception("editor: a file request failed")
                result = ("refused", RequestRefused(protocol.ERROR_FAILED, str(exc), {}))
            GLib.idle_add(lambda: land(*result) and False, priority=GLib.PRIORITY_DEFAULT)

        threading.Thread(target=run, name="editor-files", daemon=True).start()

    def _outside_message(self, path: Path) -> str:
        return _("{name} is outside this project and can't be opened here.").format(name=path.name)

    def _open_image(self, key: str, path: Path) -> None:
        """An image's page, once the service's `fs.stat` says the file is
        inside this root, a file, and not absurdly large
        (`editorfiles.image_stat_guard`); asked off the main loop."""
        if key in self._image_opening:
            return
        self._image_opening.add(key)
        root = str(self._root)
        self._off_main(
            lambda: remotefiles.stat_path(key, root),
            lambda kind, value: self._image_stat_landed(key, path, kind, value),
        )

    def _image_stat_landed(self, key: str, path: Path, kind: str, value) -> None:
        self._image_opening.discard(key)
        if self._shut:
            return
        existing = self._pages.get(key)
        if existing is not None:
            self._select_page(existing)
            return
        if kind != "ok":
            self._notify(
                _("Couldn't open {name}: {message}").format(
                    name=path.name, message=remotefiles.refusal_words(value)
                )
            )
            return
        if not value.inside:
            self._notify(self._outside_message(path))
            return
        guard = editorfiles.image_stat_guard(value.kind, value.size)
        if guard != editorfiles.LoadGuard.OK:
            self._notify(self._guard_message(path, guard))
            return
        self._open_image_page(key, path)

    def _open_image_page(self, key: str, path: Path) -> None:
        """A read-only `Gtk.Picture` page — images never get a buffer
        (`fs.read` would refuse them as binary), so none of the
        save/dirty/search machinery applies: `_open` stays text-only and
        every `_open` consumer skips these pages. The guard ran first
        (`_image_stat_landed`)."""
        # Through animatedimage like every other image surface, so a GIF
        # opened here plays rather than showing its first frame.
        paintable = animatedimage.load(key)
        if paintable is None:
            self._notify(_("{name} couldn't be decoded as an image.").format(name=path.name))
            return
        picture = Gtk.Picture.new_for_paintable(paintable)
        picture.set_can_shrink(True)
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        picture.set_hexpand(True)
        picture.set_vexpand(True)
        page = self._tab_view.append(picture)
        page.set_title(path.name)
        page.set_tooltip(key)
        page.set_icon(Gio.ThemedIcon.new("image-x-generic-symbolic"))
        self._pages[key] = page
        self._page_key[page] = key
        self._select_page(page)

    def _on_loaded(self, opened: _OpenFile, load_id: int, kind: str, value) -> None:
        if self._shut:
            return  # the tab closed while the read was in flight: nothing to fill or watch
        if load_id != opened.load_id:
            return  # a newer load (a rename's) owns this buffer now
        opened.loading = False
        reload = opened.reloading
        opened.reloading = False
        restore_cursor = opened.pending_cursor
        # Looked up under the file's *current* path, since a rename may have
        # moved it since the load started — and by identity, so a page closed
        # and reopened meanwhile isn't mistaken for this one.
        key = str(opened.path)
        still_open = self._open.get(key) is opened
        if not still_open:
            return
        failure: str | None = None
        outside = kind != "ok" and value.msgid == _OUTSIDE_MSGID
        if outside:
            failure = self._outside_message(opened.path)
        elif kind != "ok":
            failure = remotefiles.refusal_words(value)
        elif value.binary:
            failure = self._guard_message(opened.path, editorfiles.LoadGuard.BINARY)
        if failure is not None:
            if reload:
                self._notify(
                    _("Couldn't reload {name}: {message}").format(name=opened.path.name, message=failure)
                )
                # A `file-changed` that waited for this reload is judged all
                # the same: the file may be gone or binary now, and that
                # event is what tells the buffer so (review of PR 609).
                self._settle_pending_change(opened)
            else:
                self._notify(
                    failure
                    if outside
                    else _("Couldn't open {name}: {message}").format(name=opened.path.name, message=failure)
                )
                page = self._pages.get(key)
                if page is not None:
                    self._teardown_page(page, key)
                    self._tab_view.close_page(page)
            return
        read: remotefiles.FileText = value
        if not reload:
            manager = GtkSource.LanguageManager.get_default()
            language = manager.guess_language(key, None)
            if language is None:
                hint = editorfiles.guess_language_id(opened.path, editorfiles.first_line(read.text))
                if hint:
                    language = manager.get_language(hint)
            if language is not None and editorfiles.should_highlight(read.size):
                opened.buffer.set_language(language)
        self._fill(opened, read)
        if restore_cursor:
            self._apply_cursor(opened, restore_cursor)
        else:
            # set_text leaves the insert mark at the end of the loaded text;
            # every other editor opens a file at the top.
            opened.buffer.place_cursor(opened.buffer.get_start_iter())
        if not reload:
            self._watch_external_changes(opened)
        self._sync_status()
        self._settle_pending_change(opened)

    def _fill(self, opened: _OpenFile, read: remotefiles.FileText) -> None:
        """The buffer's text replaced by what the service read, outside the
        undo history (as the loader's load was), and the file's facts kept
        for the save that follows. The file's final newline is stripped
        when the buffer's implicit trailing newline is on (what the loader
        did; `_do_save` puts it back), so a file does not show an empty
        last line."""
        text = read.text
        newline = "\n"
        if opened.buffer.get_implicit_trailing_newline():
            if text.endswith("\r\n"):
                text, newline = text[:-2], "\r\n"
            elif text.endswith("\n"):
                text = text[:-1]
        opened.buffer.begin_irreversible_action()
        opened.buffer.set_text(text)
        opened.buffer.end_irreversible_action()
        opened.buffer.set_modified(False)
        opened.newline = newline
        opened.mtime = read.mtime
        opened.size = read.size
        opened.encoding = read.encoding
        opened.filled = True
        opened.view.set_editable(True)
        remotefiles.watcher().update(opened.watch_handle, read.mtime)

    def _guard_message(self, path: Path, guard) -> str:
        LG = editorfiles.LoadGuard
        if guard is LG.TOO_LARGE:
            return _("{name} is too large to open in the editor.").format(name=path.name)
        if guard is LG.BINARY:
            return _("{name} looks like a binary file and can't be opened here.").format(name=path.name)
        if guard is LG.NOT_A_FILE:
            return _("{name} is not a file.").format(name=path.name)
        return _("Couldn't open {name}.").format(name=path.name)

    # -- saving --------------------------------------------------------------

    def save_current(self) -> None:
        opened = self._active_open()
        if opened is not None:
            self._save(opened)

    def _save(self, opened: _OpenFile) -> None:
        """Ctrl+S: the write expects the mtime of the last read or write;
        a file that moved underneath the buffer is refused `stale` by the
        service (nothing written) and the user is asked before it is
        overwritten (`_on_saved`)."""
        self._do_save(opened, expect=opened.mtime)

    def _do_save(self, opened: _OpenFile, on_done=None, expect: int | None = None) -> None:
        """`on_done(success)` runs once the async save resolves — the close
        flows use it to only proceed past a Save that actually landed. The
        write runs on the service (`fs.write`, PR-2.3) from a worker thread;
        with *expect* None it writes regardless of what is on disk (the
        close flows' Save and the Overwrite the user confirmed). A buffer
        the file never filled is not saved (an empty buffer over the file);
        a save while one is in flight waits for it and goes next, with the
        mtime that save answers (two quick Ctrl+S are two writes, no
        spurious "changed on disk")."""
        if not opened.filled:
            self._notify(_("{name} is still loading.").format(name=opened.path.name))
            if on_done is not None:
                on_done(False)
            return
        if opened.saving:
            if opened.save_again is None:
                opened.save_again = []
            if on_done is not None:
                opened.save_again.append(on_done)
            return
        if opened.reloading and not opened.buffer.get_modified():
            # Ctrl+S in the middle of a silent reload: nothing of the user's
            # to write, and the read in flight is about to move the mtime.
            if on_done is not None:
                on_done(True)
            return
        start, end = opened.buffer.get_bounds()
        text = opened.buffer.get_text(start, end, True)
        if text and opened.buffer.get_implicit_trailing_newline():
            text += opened.newline  # what `_fill` stripped (the saver's implicit newline)
        path = str(opened.path)
        encoding = opened.encoding
        opened.saving = True
        self._off_main(
            lambda: remotefiles.write(path, text, expect, encoding),
            lambda kind, value: self._on_saved(opened, on_done, expect, kind, value),
        )

    def _on_saved(self, opened: _OpenFile, on_done, expect: int | None, kind: str, value) -> None:
        opened.saving = False
        if self._shut:
            if on_done is not None:
                on_done(kind == "ok")
            return
        waiters, opened.save_again = opened.save_again, None
        if kind != "ok":
            refusal: RequestRefused = value
            for waiter in waiters or ():
                waiter(False)
            if remotefiles.is_stale(refusal) and expect is not None:
                dialogs.confirm_dialog(
                    self.get_root(),
                    _("{name} changed on disk").format(name=opened.path.name),
                    _("Overwrite it with the changes you made here?"),
                    _("Overwrite"),
                    lambda: self._do_save(opened, on_done),
                    default_response="cancel",
                    on_dismiss=(lambda: on_done(False)) if on_done is not None else None,
                )
                return
            self._notify(
                _("Couldn't save {name}: {message}").format(
                    name=opened.path.name, message=remotefiles.refusal_words(refusal)
                )
            )
            if on_done is not None:
                on_done(False)
            self._settle_pending_change(opened)
            return
        written: remotefiles.Written = value
        if written.encoding != opened.encoding:
            # The read's encoding could not carry the text: it was saved as
            # UTF-8, which the user should hear once.
            self._notify(
                _("{name} was saved as UTF-8: its {encoding} encoding couldn't carry the text.").format(
                    name=opened.path.name, encoding=opened.encoding
                )
            )
        opened.mtime = written.mtime
        opened.size = written.size
        opened.encoding = written.encoding
        remotefiles.watcher().update(opened.watch_handle, written.mtime)
        if waiters is not None:
            # Typed into while the save was in flight: saved again now, over
            # exactly what was just written.
            if on_done is not None:
                waiters.insert(0, on_done)

            def done_all(ok: bool) -> None:
                for waiter in waiters:
                    waiter(ok)

            self._do_save(opened, done_all, expect=written.mtime)
            return
        opened.buffer.set_modified(False)
        self._settle_pending_change(opened)
        if on_done is not None:
            on_done(True)

    def save_all(self, on_done) -> None:
        """Save every dirty buffer, then `on_done(all_succeeded)`. Backs the
        Save choice in the close flows' "Save Changes?" dialog — that choice
        is the user's explicit write consent, so unlike Ctrl+S this doesn't
        re-ask about files the agent modified on disk underneath; a failed
        save raises the banner naming the file instead."""
        dirty = [opened for opened in self._open.values() if opened.buffer.get_modified()]
        if not dirty:
            on_done(True)
            return
        state = {"left": len(dirty), "ok": True}

        def finish(success: bool) -> None:
            state["ok"] = state["ok"] and success
            state["left"] -= 1
            if state["left"] == 0:
                on_done(state["ok"])

        for opened in dirty:
            self._do_save(opened, finish)

    def dirty_count(self) -> int:
        return sum(1 for opened in self._open.values() if opened.buffer.get_modified())

    def dirty_names(self) -> list[str]:
        return [
            opened.path.name for opened in self._open.values() if opened.buffer.get_modified()
        ]

    # -- external changes ----------------------------------------------------

    def _watch_external_changes(self, opened: _OpenFile) -> None:
        """The file's monitor, on the service (`fs.watch`, PR-2.3): every
        `file-changed` for it (debounced there) lands in `_check_external`.
        Idempotent: a file that gets re-watched (a rename, a restarted load)
        must not leave the watch on its old path running."""
        self._unwatch(opened)
        if self._shut:
            return
        handle = remotefiles.watcher().watch(str(opened.path), self._on_file_changed, mtime=opened.mtime)
        opened.watch_handle = handle
        self._watched[handle] = opened

    def _unwatch(self, opened: _OpenFile) -> None:
        if opened.watch_handle is not None:
            self._watched.pop(opened.watch_handle, None)
            remotefiles.watcher().unwatch(opened.watch_handle)
            opened.watch_handle = None

    def _on_file_changed(self, event: dict) -> None:
        """The watcher's listener (a bound method, held weakly there, so a
        pane that was dropped without `shutdown` is not kept alive by its
        watches): the `_OpenFile` is looked up by the event's handle."""
        opened = self._watched.get(str(event.get("handle")))
        if opened is not None:
            self._check_external(opened, event)

    def shutdown(self) -> None:
        """The pane's tab is closing for good: every file's watch on the
        service is dropped (`TerminalTab.release_editor`). Without this
        the watches, and through their listener the pane and its buffers,
        would live as long as the module's watcher. A read or write still
        in flight lands on `_shut` and installs nothing."""
        self._shut = True
        for opened in list(self._open.values()):
            self._unwatch(opened)
        self._watched.clear()
        self._tree.shutdown()  # the tree's directory watches (PR-2.4)

    def _check_external(self, opened: _OpenFile, event: dict) -> None:
        """A `file-changed` for *opened*: judged against the mtime and size
        of the buffer's last read or write. One that arrives while a save
        or load is in flight waits for it (`pending_change`): the save's
        own write is what the monitor saw, and the reply's mtime says so."""
        if self._open.get(str(opened.path)) is not opened or event.get("handle") != opened.watch_handle:
            return  # closed or re-watched meanwhile
        if opened.saving or opened.loading:
            opened.pending_change = event
            return
        opened.pending_change = None
        if event.get("gone"):
            if opened.mtime is None:
                return  # already told
            opened.mtime = None
            opened.buffer.set_modified(True)  # nothing on disk to save over silently
            self._notify(_("{name} was deleted.").format(name=opened.path.name))
        elif (event.get("mtime"), event.get("size")) != (opened.mtime, opened.size):
            if opened.buffer.get_modified():
                self._show_banner(
                    _("{name} changed on disk.").format(name=opened.path.name),
                    _("Reload"),
                    lambda: self._reload_from_disk(opened),
                )
            else:
                self._reload_from_disk(opened)

    def _settle_pending_change(self, opened: _OpenFile) -> None:
        """The `file-changed` that waited for a save or load, judged now."""
        event = opened.pending_change
        if event is not None:
            opened.pending_change = None
            self._check_external(opened, event)

    def _reload_from_disk(self, opened: _OpenFile) -> None:
        if opened.loading:
            return  # the load in flight brings what is on disk now
        it = opened.buffer.get_iter_at_mark(opened.buffer.get_insert())
        pos = [it.get_line(), it.get_line_offset()]
        self._start_load(opened, pos, reload=True)

    def _apply_cursor(self, opened: _OpenFile, pos) -> None:
        if not isinstance(pos, (list, tuple)) or len(pos) != 2:
            return
        line, offset = pos
        n_lines = opened.buffer.get_line_count()
        _found, it = opened.buffer.get_iter_at_line(max(0, min(int(line), n_lines - 1)))
        it.set_line_offset(max(0, min(int(offset), it.get_chars_in_line())))
        opened.buffer.place_cursor(it)
        # On a freshly loaded buffer, line heights are estimates until the
        # view's validation idles run: scroll_to_iter silently stays at the
        # top, and even scroll_to_mark's pending scroll lands hundreds of
        # lines short on a large file. Scroll now for the cheap case, then
        # re-issue at PRIORITY_LOW — validation runs at a far higher idle
        # priority, so by the time the re-scroll fires the heights are exact
        # (a clicked `path:602` reference must actually land on line 602).
        opened.view.scroll_to_mark(opened.buffer.get_insert(), 0.1, False, 0.0, 0.0)
        view = opened.view

        def rescroll() -> bool:
            buffer = view.get_buffer()
            if buffer is not None:
                view.scroll_to_mark(buffer.get_insert(), 0.1, False, 0.0, 0.0)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(rescroll, priority=GLib.PRIORITY_LOW)

    # -- following the session's working directory ------------------------------

    def request_root(self, root: str | Path) -> None:
        """Re-root the pane at *root*: the file tree, the guard on what may be
        opened here, and the root quick open searches all move with it.

        Open tabs come across too, each landing on the same project-relative
        path under the new root — but never at the cost of unsaved work.
        Buffers that are dirty *and* have a counterpart over there are put to
        the user first (`dialogs.follow_working_dir_dialog`), and declining
        there calls the whole move off, tree included. Everything else
        `plan_reroot` already has a safe answer for."""
        new_root = Path(root)
        if self._reroot_asking:
            # One dialog at a time — but the session has moved on since that
            # one went up, and whatever sent this considers the directory
            # acted on and won't send it again (the poll settles a working
            # directory exactly once). So it is remembered, and picked up
            # when the open dialog is answered. Only the latest is worth
            # keeping; a move back to where the pane already is cancels it.
            self._reroot_queued = None if new_root == self._root else new_root
            return
        self._reroot_seq += 1  # a re-root still asking the service is superseded
        if new_root == self._root:
            return
        # Whether *root* is a directory, and which open files have a
        # counterpart there, are the service's `fs.stat` answers, asked off
        # the main loop (PR-2.4); the move goes on in `_reroot_checked`.
        seq, old_root = self._reroot_seq, self._root
        counterparts = editorfiles.reroot_counterparts(old_root, new_root, list(self._pages))

        def work() -> set[str] | None:
            if not remotefiles.stat_path(str(new_root)).is_dir:
                return None
            return {path for path in counterparts if remotefiles.stat_path(path).is_file}

        self._off_main(work, lambda kind, value: self._reroot_checked(seq, old_root, new_root, kind, value))

    def _reroot_checked(self, seq: int, old_root: Path, new_root: Path, kind: str, files) -> None:
        if self._shut or seq != self._reroot_seq or self._root != old_root or self._reroot_asking:
            return  # superseded, or the pane moved (or asked) meanwhile
        if kind != "ok" or files is None:
            return  # not a directory over there (or the service couldn't say): no move
        entries = editorfiles.plan_reroot(
            old_root, new_root, list(self._pages), self._dirty_paths(), files=files
        )
        defaults = {entry.path: entry.default for entry in entries}
        asking = [entry for entry in entries if entry.needs_asking]
        if not asking:
            self._apply_reroot(new_root, defaults)
            return

        answered = {"move": False}

        def move(choices: dict) -> None:
            answered["move"] = True
            # The pane may have moved again (or the tab been closed) while the
            # dialog sat open. Re-planning would be answering a question the
            # user was never shown, so a move that is no longer the one asked
            # about is simply dropped.
            if self._root != old_root:
                return
            self._apply_reroot(new_root, {**defaults, **choices})

        def done() -> None:
            self._reroot_asking = False
            queued = self._reroot_queued
            self._reroot_queued = None
            if queued is not None and queued != self._root:
                # The session moved again while the dialog was up. That is
                # where it actually is now, so it wins over the answer just
                # given — but from a root that may have changed underneath
                # it, so how far it goes is judged again rather than
                # inherited: silently swapping in a different project is
                # exactly what OFFER exists to prevent. Deferred so the
                # dialog being answered is gone before the next is presented.
                GLib.idle_add(self._follow_queued, queued)
                return
            # Declining means "not now", not "never": the banner leaves the
            # move one click away without the poll asking again, which it
            # won't — a settled working directory is acted on exactly once.
            if not answered["move"] and self._root == old_root:
                self.offer_root(str(new_root))

        self._reroot_asking = True
        dialogs.follow_working_dir_dialog(
            self.get_root(), str(old_root), str(new_root), asking, move, done
        )

    def set_follow_judge(self, judge) -> None:
        """How a queued move is judged again: `judge(cwd, root)` answering a
        `FollowScope` or None, blocking (the tab's session's `judge_cwd`,
        the service's `cwd.settle` with `judge`; asked from a worker)."""
        self._follow_judge = judge

    def _follow_queued(self, root: Path) -> bool:
        """A move that arrived while the follow dialog was up, taken as far as
        it goes from wherever the pane ended up (see `request_root`). The
        scope is the service's (`cwd.settle` with `judge`, PR-2.4), asked
        off the main loop; with no judge (a pane with no session) it is
        only offered — never a silent swap."""
        judge = self._follow_judge
        if judge is None:
            self.offer_root(str(root))
            return GLib.SOURCE_REMOVE
        pane_root = self._root
        self._off_main(
            lambda: judge(str(root), str(pane_root)),
            lambda kind, scope: self._queued_judged(root, pane_root, kind, scope),
        )
        return GLib.SOURCE_REMOVE

    def _queued_judged(self, root: Path, pane_root: Path, kind: str, scope) -> None:
        if self._shut or kind != "ok" or self._root != pane_root:
            return  # closed, unanswered, or the pane moved again meanwhile
        if scope is editorfiles.FollowScope.AUTO:
            self.request_root(root)
        elif scope is editorfiles.FollowScope.OFFER:
            self.offer_root(str(root))

    def offer_root(self, root: str) -> None:
        """Offer a move the pane won't make on its own — the session working
        somewhere outside the project this editor belongs to, where re-rooting
        would swap out every open file (see `projectfiles.follow_scope`).
        Ignoring the banner is a real answer, so nothing expires it."""
        self._show_banner(
            _("Session moved to {name}").format(name=Path(root).name),
            _("Follow"),
            lambda: self.request_root(root),
        )

    def _dirty_paths(self) -> set[str]:
        return {key for key, opened in self._open.items() if opened.buffer.get_modified()}

    def _apply_reroot(self, new_root: Path, decisions: dict) -> None:
        RA = editorfiles.RerootAction
        old_root = self._root
        self._root = Path(new_root)
        self._tree.set_root(self._root)
        # Whatever the banner was saying belonged to the old root — a file that
        # changed on disk over there, or the offer that got us here.
        self._banner.set_revealed(False)
        for path, action in decisions.items():
            if action is RA.LEAVE:
                continue
            page = self._pages.get(path)
            if page is None:
                continue  # closed while the dialog was open
            target = editorfiles.renamed_path(old_root, self._root, path)
            if target is None or target == path:
                continue
            opened = self._open.get(path)
            if opened is None:
                # An image page: its texture was decoded from the old file and
                # can't be re-pointed, so it is closed and opened again. It
                # lands at the end of the strip — the one thing about a page a
                # re-root doesn't keep.
                self._tab_view.close_page(page)
                self.open_file(target)
                continue
            # Same machinery a rename uses: the buffer, its unsaved changes and
            # its place in the strip stay put; only the file underneath moves.
            self._retarget_open(Path(path), Path(target))
            if action is RA.RELOAD:
                moved = self._open.get(target)
                if moved is not None:
                    self._reload_from_disk(moved)
        active = self._selected_key()
        if active:
            self._tree.reveal(active)  # a no-op for a file the tree can't show
        self._sync_status()
        self.emit("root-changed", str(self._root))

    # -- notices ---------------------------------------------------------------

    def _notify(self, message: str) -> None:
        if self._banner_click_id is not None:
            self._banner.disconnect(self._banner_click_id)
            self._banner_click_id = None
        self._banner.set_title(message)
        self._banner.set_button_label("")
        self._banner.set_revealed(True)

    def _show_banner(self, title: str, button_label: str, on_click) -> None:
        if self._banner_click_id is not None:
            self._banner.disconnect(self._banner_click_id)
            self._banner_click_id = None

        def handle_click(*_args) -> None:
            self._banner.set_revealed(False)
            on_click()

        self._banner.set_title(title)
        self._banner.set_button_label(button_label)
        self._banner_click_id = self._banner.connect("button-clicked", handle_click)
        self._banner.set_revealed(True)

    # -- closing files -----------------------------------------------------

    def _on_tabview_close_page(self, view: Adw.TabView, page: Adw.TabPage) -> bool:
        key = self._page_key.get(page)
        opened = self._open.get(key) if key else None
        if opened is not None and opened.buffer.get_modified() and page not in self._close_confirmed:
            view.close_page_finish(page, False)  # keep it open while we ask

            def close() -> None:
                self._close_confirmed.add(page)
                view.close_page(page)

            def save_then_close() -> None:
                self._do_save(opened, lambda ok: close() if ok else None)

            dialogs.save_changes_dialog(
                self.get_root(),
                _(
                    "“{name}” contains unsaved changes. "
                    "Changes which are not saved will be permanently lost."
                ).format(name=opened.path.name),
                save_then_close,
                close,
            )
            return True
        self._close_confirmed.discard(page)
        self._teardown_page(page, key)
        return False

    def _teardown_page(self, page: Adw.TabPage, key: str | None) -> None:
        if key is None:
            return
        opened = self._open.pop(key, None)
        self._pages.pop(key, None)
        self._page_key.pop(page, None)
        if opened is not None:
            self._unwatch(opened)

    # -- search --------------------------------------------------------------

    def _build_search_bar(self) -> Gtk.SearchBar:
        bar = Gtk.SearchBar()
        entry = Gtk.SearchEntry(hexpand=True, placeholder_text=_("Find in file…"))
        self._search_entry = entry
        self._search_settings = GtkSource.SearchSettings()
        self._search_settings.set_wrap_around(True)
        entry.connect("search-changed", self._on_search_changed)
        entry.connect("activate", lambda *_a: self._search_step(forward=True))
        entry.connect("next-match", lambda *_a: self._search_step(forward=True))
        entry.connect("previous-match", lambda *_a: self._search_step(forward=False))
        entry.connect("stop-search", lambda *_a: self.hide_search())

        prev_btn = Gtk.Button(icon_name="go-up-symbolic", tooltip_text=_("Previous match"))
        prev_btn.connect("clicked", lambda *_a: self._search_step(forward=False))
        next_btn = Gtk.Button(icon_name="go-down-symbolic", tooltip_text=_("Next match"))
        next_btn.connect("clicked", lambda *_a: self._search_step(forward=True))

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.append(entry)
        box.append(prev_btn)
        box.append(next_btn)
        bar.set_child(box)
        bar.connect_entry(entry)
        bar.set_show_close_button(True)
        bar.connect("notify::search-mode-enabled", self._on_search_mode_changed)
        return bar

    def toggle_search(self) -> None:
        if self._active_open() is None:
            return
        if self._search_bar.get_search_mode():
            self.hide_search()
        else:
            self._search_bar.set_search_mode(True)
            self._search_entry.grab_focus()

    def hide_search(self) -> None:
        self._search_bar.set_search_mode(False)

    def _on_search_mode_changed(self, bar: Gtk.SearchBar, _pspec) -> None:
        if not bar.get_search_mode():
            self._active_search_context = None
            self._last_match = None

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        opened = self._active_open()
        if opened is None:
            return
        text = entry.get_text()
        self._search_settings.set_search_text(text or None)
        context = GtkSource.SearchContext.new(opened.buffer, self._search_settings)
        context.set_highlight(True)
        self._active_search_context = context
        self._last_match = None  # a changed query invalidates wherever the old one landed
        if text:
            self._search_step(forward=True)

    def _search_step(self, forward: bool) -> None:
        """Async, not the sync forward()/backward(): the search index GtkSource
        builds for a fresh SearchContext isn't ready the instant a keystroke
        creates one, and the sync calls simply miss a match that exists —
        async waits for the index instead of racing it."""
        opened = self._active_open()
        context = self._active_search_context
        if opened is None or context is None:
            return
        if self._last_match is not None:
            # Pivot on the match's far edge *for this direction*, not the
            # cursor: switching direction from where the last match landed
            # must still step past it, not re-find it.
            start_off, end_off = self._last_match
            start_iter = opened.buffer.get_iter_at_offset(end_off if forward else start_off)
        else:
            start_iter = opened.buffer.get_iter_at_mark(opened.buffer.get_insert())
        data = (context, opened)
        if forward:
            context.forward_async(start_iter, callback=self._on_search_forward_done, user_data=data)
        else:
            context.backward_async(start_iter, callback=self._on_search_backward_done, user_data=data)

    def _on_search_forward_done(self, context, result: Gio.AsyncResult, data: tuple) -> None:
        self._apply_search_result(*data, context.forward_finish(result))

    def _on_search_backward_done(self, context, result: Gio.AsyncResult, data: tuple) -> None:
        self._apply_search_result(*data, context.backward_finish(result))

    def _apply_search_result(self, context, opened: _OpenFile, outcome: tuple) -> None:
        if context is not self._active_search_context:
            return  # a newer keystroke superseded this search before it resolved
        found, match_start, match_end, _wrapped = outcome
        if not found:
            return
        self._last_match = (match_start.get_offset(), match_end.get_offset())
        opened.buffer.select_range(match_start, match_end)
        opened.view.scroll_to_iter(match_start, 0.1, False, 0, 0)

    # -- status row ------------------------------------------------------------

    def _build_status_row(self) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("tab-footer")  # same slim-row look as the terminal footer

        self._status_path = Gtk.Label(xalign=0.0, hexpand=True, ellipsize=Pango.EllipsizeMode.START)
        self._status_path.add_css_class("caption")
        self._status_path.add_css_class("dim-label")
        self._status_lang = Gtk.Label()
        self._status_lang.add_css_class("caption")
        self._status_lang.add_css_class("dim-label")
        self._status_cursor = Gtk.Label()
        self._status_cursor.add_css_class("caption")
        self._status_cursor.add_css_class("dim-label")
        self._status_cursor.add_css_class("numeric")
        self._save_btn = Gtk.Button(icon_name="document-save-symbolic")
        self._save_btn.add_css_class("flat")
        self._save_btn.set_tooltip_text(_("Save (Ctrl+S)"))
        self._save_btn.set_action_name("editor.save")
        # Rightmost: pop the pane out into its own window. Hidden while
        # already popped out — the EditorWindow headerbar carries the
        # symmetric dock-back button instead.
        self._detach_btn = Gtk.Button(icon_name="window-new-symbolic")
        self._detach_btn.add_css_class("flat")
        self._detach_btn.set_tooltip_text(_("Move editor to its own window"))
        self._detach_btn.connect("clicked", lambda *_a: self.emit("request-pop-out"))

        row.append(self._status_path)
        row.append(self._status_lang)
        row.append(self._status_cursor)
        row.append(self._save_btn)
        row.append(self._detach_btn)
        self._sync_status()
        return row

    def set_detached(self, detached: bool) -> None:
        """Reflect where the pane lives: the detach button only makes sense
        while it is still inside its tab."""
        self._detach_btn.set_visible(not detached)

    def _sync_status(self) -> None:
        opened = self._active_open()
        if opened is None:
            # An image page still names itself in the status row; everything
            # else there (language, cursor, save) is buffer-only.
            key = self._selected_key()
            self._status_path.set_text(key or "")
            self._status_lang.set_text(_("Image") if key else "")
            self._status_cursor.set_text("")
            self._save_btn.set_sensitive(False)
            return
        self._status_path.set_text(str(opened.path))
        language = opened.buffer.get_language()
        self._status_lang.set_text(language.get_name() if language is not None else _("Plain Text"))
        it = opened.buffer.get_iter_at_mark(opened.buffer.get_insert())
        self._status_cursor.set_text(f"{it.get_line() + 1}:{it.get_line_offset() + 1}")
        self._save_btn.set_sensitive(opened.buffer.get_modified())

    def _on_modified_changed(self, buffer: GtkSource.Buffer, opened: _OpenFile) -> None:
        page = self._pages.get(str(opened.path))
        if page is not None:
            page.set_title(("• " if buffer.get_modified() else "") + opened.path.name)
        self._sync_status()

    def _on_page_switched(self) -> None:
        if self._search_bar.get_search_mode():
            self.hide_search()
        self._sync_status()

    def _selected_key(self) -> str | None:
        page = self._tab_view.get_selected_page()
        return self._page_key.get(page) if page is not None else None

    def _active_open(self) -> _OpenFile | None:
        key = self._selected_key()
        return self._open.get(key) if key is not None else None

    # -- appearance ------------------------------------------------------------

    def _on_style_changed(self, manager: Adw.StyleManager, _pspec) -> None:
        self._dark = manager.get_dark()
        if not self._style_scheme_setting:
            for opened in self._open.values():
                self._apply_scheme(opened.buffer)

    def _apply_scheme(self, buffer: GtkSource.Buffer) -> None:
        scheme = style_scheme(self._style_scheme_setting, self._dark)
        if scheme is not None:
            buffer.set_style_scheme(scheme)

    def _apply_font(self, opened: _OpenFile) -> None:
        if opened.font_provider is not None:
            opened.view.get_style_context().remove_provider(opened.font_provider)
            opened.font_provider = None
        if not self._font:
            return
        desc = Pango.FontDescription.from_string(self._font)
        size = desc.get_size() / Pango.SCALE
        unit = "pt" if not desc.get_size_is_absolute() else "px"
        css = f'textview {{ font-family: "{desc.get_family()}"; font-size: {size}{unit}; }}'
        provider = Gtk.CssProvider()
        provider.load_from_string(css)
        opened.view.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        opened.font_provider = provider

    def apply_settings(self, settings: dict) -> None:
        self.apply_keybindings(settings.get(keybindings.SETTING))
        self._style_scheme_setting = settings.get("editor_style_scheme") or ""
        self._show_line_numbers = bool(settings.get("editor_show_line_numbers", True))
        self._font = settings.get("editor_font") or ""
        self._tree.set_show_hidden(bool(settings.get("editor_show_hidden_files", True)))
        footer_apps = settings.get("footer_apps") or []
        self._footer_apps = [str(app_id) for app_id in footer_apps if isinstance(app_id, str)]
        self._tree.set_footer_apps(self._footer_apps)
        try:
            narrow_width = int(settings.get("editor_narrow_width") or 0)
        except (TypeError, ValueError):
            narrow_width = 0
        self._breakpoint.set_condition(_narrow_condition(narrow_width))
        # The bin judges its breakpoints when it allocates; a changed
        # condition on its own doesn't make it look again.
        self._breakpoint_bin.queue_resize()
        for opened in self._open.values():
            self._apply_scheme(opened.buffer)
            opened.view.set_show_line_numbers(self._show_line_numbers)
            self._apply_font(opened)

    # -- session state ---------------------------------------------------------

    @property
    def root(self) -> Path:
        """The project directory the file tree is rooted at (names the
        popped-out window)."""
        return self._root

    def open_paths(self) -> list[str]:
        # _pages, not _open: image pages have no buffer but should still be
        # part of the session's remembered files.
        return list(self._pages.keys())

    def active_path(self) -> str | None:
        return self._selected_key()

    def cursor_positions(self) -> dict[str, list[int]]:
        positions = {}
        for key, opened in self._open.items():
            it = opened.buffer.get_iter_at_mark(opened.buffer.get_insert())
            positions[key] = [it.get_line(), it.get_line_offset()]
        return positions

    def restore(self, files: list[str], active: str | None, cursors: dict[str, list]) -> None:
        for key in files[:_MAX_RECENT_FILES]:
            self.open_file(key, restore_cursor=cursors.get(key))
        if active and active in self._pages:
            self._select_page(self._pages[active])

    # -- focus -------------------------------------------------------------

    def focus_default(self) -> None:
        opened = self._active_open()
        # A narrow pane showing the picker has its view hidden; focus can't
        # land there, so the tree takes it even with a file active.
        if opened is not None and self._editors.get_visible():
            opened.view.grab_focus()
        else:
            self._tree.grab_focus()
