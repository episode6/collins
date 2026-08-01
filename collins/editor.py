# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The built-in editor panel: per-tab syntax-highlighted editing with a
project file tree, real saving, and external-change handling.

Soft dependency on GtkSourceView 5: importing this module never raises even
when the `gtksourceview5` typelib isn't installed (see HAVE_GTKSOURCE) — the
caller (terminal.py) degrades to "no editor button" instead of an app that
won't launch, so an existing install survives the upgrade before its package
manager catches up (see `gir1.2-gtksource-5` in debian/control).
"""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

try:
    gi.require_version("GtkSource", "5")
    from gi.repository import GtkSource

    HAVE_GTKSOURCE = True
except (ValueError, ImportError):
    GtkSource = None
    HAVE_GTKSOURCE = False

from . import dialogs, editorfiles  # noqa: E402
from .filetree import FileTree  # noqa: E402
from .i18n import _  # noqa: E402

_MAX_RECENT_FILES = 20  # cap on files a session's editor_state remembers
# How long after a file-monitor event to actually check the file: long
# enough that a run of quick writes (an agent's edit tool) coalesces into one
# check, short enough that "the editor matches disk" still feels immediate.
_EXTERNAL_CHANGE_DEBOUNCE_MS = 300
_TREE_INITIAL_WIDTH = 180


class _OpenFile:
    """One open buffer, independent of whether its tab-strip page is the one
    currently showing."""

    def __init__(self, path: Path, buffer, view, gfile) -> None:
        self.path = path
        self.buffer = buffer
        self.view = view
        self.gfile = gfile
        self.monitor: Gio.FileMonitor | None = None
        self.font_provider: Gtk.CssProvider | None = None


class EditorPane(Gtk.Box):
    """One per `TerminalTab`. Hidden by default; the tab shows/hides it (and,
    from PR 2, pops it out into its own window — not implemented here)."""

    def __init__(self, root: str | Path) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._root = Path(root)
        self._open: dict[str, _OpenFile] = {}  # path str -> _OpenFile
        self._pages: dict[str, Adw.TabPage] = {}
        self._page_key: dict[Adw.TabPage, str] = {}
        self._close_confirmed: set[Adw.TabPage] = set()  # discard-changes already agreed to
        self._style_scheme_setting = ""  # "" = follow the app's own light/dark scheme
        self._show_line_numbers = True
        self._font = ""
        self._banner_click_id: int | None = None
        self._active_search_context: GtkSource.SearchContext | None = None
        self._last_match: tuple[int, int] | None = None  # (start, end) char offsets

        style_manager = Adw.StyleManager.get_default()
        self._dark = style_manager.get_dark()
        style_manager.connect("notify::dark", self._on_style_changed)

        self._banner = Adw.Banner()
        self.append(self._banner)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True)
        paned.set_wide_handle(True)
        paned.set_position(_TREE_INITIAL_WIDTH)

        self._tree = FileTree(self._root)
        self._tree.connect("open-file", lambda _t, path: self.open_file(path))
        paned.set_start_child(self._tree)
        paned.set_resize_start_child(False)
        paned.set_shrink_start_child(False)

        editors = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self._tab_view = Adw.TabView()
        self._tab_view.connect("close-page", self._on_tabview_close_page)
        self._tab_view.connect("notify::selected-page", lambda *_a: self._on_page_switched())
        tab_bar = Adw.TabBar(view=self._tab_view, autohide=False)
        editors.append(tab_bar)
        self._search_bar = self._build_search_bar()
        editors.append(self._search_bar)
        editors.append(self._tab_view)
        paned.set_end_child(editors)
        paned.set_resize_end_child(True)
        paned.set_shrink_end_child(False)

        self.append(paned)
        self.append(self._build_status_row())
        self._install_actions()

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
        self.insert_action_group("editor", actions)

        controller = Gtk.ShortcutController()
        controller.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        for trigger, action in (("<Control>s", "editor.save"), ("<Control>f", "editor.find")):
            controller.add_shortcut(
                Gtk.Shortcut.new(
                    Gtk.ShortcutTrigger.parse_string(trigger), Gtk.NamedAction.new(action)
                )
            )
        self.add_controller(controller)

    # -- opening files -----------------------------------------------------

    def open_file(self, path: str | Path, restore_cursor: list | None = None) -> None:
        path = Path(path)
        key = str(path)
        existing = self._pages.get(key)
        if existing is not None:
            self._tab_view.set_selected_page(existing)
            return
        # Defense in depth behind the tree's own symlink filtering: every
        # caller (tree activation, session restore, future pop-out) funnels
        # through here, and a save would write through a symlink — so nothing
        # resolving outside the project may ever get a buffer.
        if not editorfiles.is_inside(self._root, path):
            self._notify(
                _("{name} is outside this project and can't be opened here.").format(
                    name=path.name
                )
            )
            return
        guard = editorfiles.load_guard(path)
        if guard != editorfiles.LoadGuard.OK:
            self._notify(self._guard_message(path, guard))
            return

        buffer = GtkSource.Buffer()
        self._apply_scheme(buffer)
        manager = GtkSource.LanguageManager.get_default()
        language = manager.guess_language(key, None)
        if language is None:
            hint = editorfiles.guess_language_id(path)
            if hint:
                language = manager.get_language(hint)
        if language is not None and editorfiles.should_highlight(path):
            buffer.set_language(language)

        view = GtkSource.View(buffer=buffer)
        view.set_show_line_numbers(self._show_line_numbers)
        view.set_highlight_current_line(True)
        view.set_monospace(True)
        view.set_auto_indent(True)

        gfile = GtkSource.File(location=Gio.File.new_for_path(key))
        opened = _OpenFile(path, buffer, view, gfile)
        self._apply_font(opened)
        self._open[key] = opened

        scrolled = Gtk.ScrolledWindow(child=view, vexpand=True, hexpand=True)
        page = self._tab_view.append(scrolled)
        page.set_title(path.name)
        page.set_tooltip(key)
        self._pages[key] = page
        self._page_key[page] = key

        buffer.connect("modified-changed", self._on_modified_changed, key)
        buffer.connect("notify::cursor-position", lambda *_a: self._sync_status())

        loader = GtkSource.FileLoader.new(buffer, gfile)
        loader.load_async(
            GLib.PRIORITY_DEFAULT, None, callback=self._on_loaded, user_data=(key, restore_cursor)
        )

        self._tab_view.set_selected_page(page)

    def _on_loaded(self, loader: GtkSource.FileLoader, result: Gio.AsyncResult, data: tuple) -> None:
        key, restore_cursor = data
        opened = self._open.get(key)
        try:
            loader.load_finish(result)
        except GLib.Error as err:
            self._notify(
                _("Couldn't open {name}: {message}").format(name=Path(key).name, message=err.message)
            )
            if opened is not None:
                page = self._pages.get(key)
                if page is not None:
                    self._teardown_page(page, key)
                    self._tab_view.close_page(page)
            return
        if opened is None:
            return
        opened.buffer.set_modified(False)
        if restore_cursor:
            self._apply_cursor(opened, restore_cursor)
        else:
            # FileLoader leaves the insert mark at the end of the loaded
            # text; every other editor opens a file at the top.
            opened.buffer.place_cursor(opened.buffer.get_start_iter())
        self._watch_external_changes(opened)
        self._sync_status()

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
        opened.gfile.check_file_on_disk()
        if opened.gfile.is_externally_modified():
            dialogs.confirm_dialog(
                self.get_root(),
                _("{name} changed on disk").format(name=opened.path.name),
                _("Overwrite it with the changes you made here?"),
                _("Overwrite"),
                lambda: self._do_save(opened),
                default_response="cancel",
            )
            return
        self._do_save(opened)

    def _do_save(self, opened: _OpenFile) -> None:
        saver = GtkSource.FileSaver.new(opened.buffer, opened.gfile)
        saver.save_async(GLib.PRIORITY_DEFAULT, None, callback=self._on_saved, user_data=opened)

    def _on_saved(self, saver: GtkSource.FileSaver, result: Gio.AsyncResult, opened: _OpenFile) -> None:
        try:
            saver.save_finish(result)
        except GLib.Error as err:
            self._notify(
                _("Couldn't save {name}: {message}").format(name=opened.path.name, message=err.message)
            )
            return
        opened.buffer.set_modified(False)

    def dirty_count(self) -> int:
        return sum(1 for opened in self._open.values() if opened.buffer.get_modified())

    # -- external changes ----------------------------------------------------

    def _watch_external_changes(self, opened: _OpenFile) -> None:
        try:
            monitor = Gio.File.new_for_path(str(opened.path)).monitor_file(Gio.FileMonitorFlags.NONE, None)
        except GLib.Error:
            return
        pending = {"queued": False}

        def on_changed(*_args) -> None:
            if pending["queued"]:
                return
            pending["queued"] = True
            GLib.timeout_add(_EXTERNAL_CHANGE_DEBOUNCE_MS, self._check_external, opened, pending)

        monitor.connect("changed", on_changed)
        opened.monitor = monitor

    def _check_external(self, opened: _OpenFile, pending: dict) -> bool:
        pending["queued"] = False
        if self._open.get(str(opened.path)) is not opened:
            return GLib.SOURCE_REMOVE  # closed meanwhile
        opened.gfile.check_file_on_disk()
        if opened.gfile.is_deleted():
            opened.buffer.set_modified(True)  # nothing on disk to save over silently
            self._notify(_("{name} was deleted.").format(name=opened.path.name))
        elif opened.gfile.is_externally_modified():
            if opened.buffer.get_modified():
                self._show_banner(
                    _("{name} changed on disk.").format(name=opened.path.name),
                    _("Reload"),
                    lambda: self._reload_from_disk(opened),
                )
            else:
                self._reload_from_disk(opened)
        return GLib.SOURCE_REMOVE

    def _reload_from_disk(self, opened: _OpenFile) -> None:
        it = opened.buffer.get_iter_at_mark(opened.buffer.get_insert())
        pos = (it.get_line(), it.get_line_offset())
        loader = GtkSource.FileLoader.new(opened.buffer, opened.gfile)
        loader.load_async(GLib.PRIORITY_DEFAULT, None, callback=self._on_reloaded, user_data=(opened, pos))

    def _on_reloaded(self, loader: GtkSource.FileLoader, result: Gio.AsyncResult, data: tuple) -> None:
        opened, pos = data
        try:
            loader.load_finish(result)
        except GLib.Error as err:
            self._notify(
                _("Couldn't reload {name}: {message}").format(name=opened.path.name, message=err.message)
            )
            return
        opened.buffer.set_modified(False)
        self._apply_cursor(opened, pos)

    def _apply_cursor(self, opened: _OpenFile, pos) -> None:
        if not isinstance(pos, (list, tuple)) or len(pos) != 2:
            return
        line, offset = pos
        n_lines = opened.buffer.get_line_count()
        _found, it = opened.buffer.get_iter_at_line(max(0, min(int(line), n_lines - 1)))
        it.set_line_offset(max(0, min(int(offset), it.get_chars_in_line())))
        opened.buffer.place_cursor(it)
        opened.view.scroll_to_iter(it, 0.1, False, 0, 0)

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

            def discard() -> None:
                self._close_confirmed.add(page)
                view.close_page(page)

            dialogs.confirm_dialog(
                self.get_root(),
                _("Close {name} without saving?").format(name=opened.path.name),
                _("Changes since the last save will be lost."),
                _("Discard Changes"),
                discard,
                default_response="cancel",
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
        if opened is not None and opened.monitor is not None:
            opened.monitor.cancel()

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

        row.append(self._status_path)
        row.append(self._status_lang)
        row.append(self._status_cursor)
        row.append(self._save_btn)
        self._sync_status()
        return row

    def _sync_status(self) -> None:
        opened = self._active_open()
        if opened is None:
            self._status_path.set_text("")
            self._status_lang.set_text("")
            self._status_cursor.set_text("")
            self._save_btn.set_sensitive(False)
            return
        self._status_path.set_text(str(opened.path))
        language = opened.buffer.get_language()
        self._status_lang.set_text(language.get_name() if language is not None else _("Plain Text"))
        it = opened.buffer.get_iter_at_mark(opened.buffer.get_insert())
        self._status_cursor.set_text(f"{it.get_line() + 1}:{it.get_line_offset() + 1}")
        self._save_btn.set_sensitive(opened.buffer.get_modified())

    def _on_modified_changed(self, buffer: GtkSource.Buffer, key: str) -> None:
        page = self._pages.get(key)
        opened = self._open.get(key)
        if page is not None and opened is not None:
            page.set_title(("• " if buffer.get_modified() else "") + opened.path.name)
        self._sync_status()

    def _on_page_switched(self) -> None:
        if self._search_bar.get_search_mode():
            self.hide_search()
        self._sync_status()

    def _active_open(self) -> _OpenFile | None:
        page = self._tab_view.get_selected_page()
        key = self._page_key.get(page) if page is not None else None
        return self._open.get(key) if key is not None else None

    # -- appearance ------------------------------------------------------------

    def _on_style_changed(self, manager: Adw.StyleManager, _pspec) -> None:
        self._dark = manager.get_dark()
        if not self._style_scheme_setting:
            for opened in self._open.values():
                self._apply_scheme(opened.buffer)

    def _apply_scheme(self, buffer: GtkSource.Buffer) -> None:
        manager = GtkSource.StyleSchemeManager.get_default()
        scheme_id = self._style_scheme_setting or ("Adwaita-dark" if self._dark else "Adwaita")
        scheme = manager.get_scheme(scheme_id)
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
        self._style_scheme_setting = settings.get("editor_style_scheme") or ""
        self._show_line_numbers = bool(settings.get("editor_show_line_numbers", True))
        self._font = settings.get("editor_font") or ""
        self._tree.set_show_hidden(bool(settings.get("editor_show_hidden_files", False)))
        for opened in self._open.values():
            self._apply_scheme(opened.buffer)
            opened.view.set_show_line_numbers(self._show_line_numbers)
            self._apply_font(opened)

    # -- session state ---------------------------------------------------------

    def open_paths(self) -> list[str]:
        return list(self._open.keys())

    def active_path(self) -> str | None:
        opened = self._active_open()
        return str(opened.path) if opened is not None else None

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
            self._tab_view.set_selected_page(self._pages[active])

    # -- focus -------------------------------------------------------------

    def focus_default(self) -> None:
        opened = self._active_open()
        if opened is not None:
            opened.view.grab_focus()
        else:
            self._tree.grab_focus()
