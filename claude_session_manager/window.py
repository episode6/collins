"""Main window: composes the session sidebar with the tabbed terminal area."""

from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from . import __version__, dialogs
from .chatsessionview import ChatSessionTab
from .i18n import _
from .models import SessionItem
from .prefs import PreferencesDialog
from .providers import available_providers, get_provider
from .replayview import ReplayTab
from .sessions import Session, export_markdown, session_from_file
from .sidebar import SessionSidebar
from .state import AppState, clamp_window_size
from .store import SessionStore
from .switcher import QuickSwitcher
from .terminal import TerminalTab

_GHOSTTY = shutil.which("ghostty")

# Quiet period before a background tab is considered "idle" / finished.
_IDLE_NOTIFY_MS = 4000

# Tab status dots, matching the sidebar (.status-dot CSS in app.py).
_STATUS_COLORS = {"open": "#2ec27e", "attention": "#3584e4"}
_status_icon_cache: dict[str, Gio.Icon] = {}


def _monitor_sizes() -> list[tuple[int, int]]:
    display = Gdk.Display.get_default()
    if display is None:
        return []
    monitors = display.get_monitors()
    sizes = []
    for i in range(monitors.get_n_items()):
        geometry = monitors.get_item(i).get_geometry()
        sizes.append((geometry.width, geometry.height))
    return sizes


def _status_icon(status: str) -> Gio.Icon | None:
    if status not in _STATUS_COLORS:
        return None
    icon = _status_icon_cache.get(status)
    if icon is None:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16">'
            f'<circle cx="8" cy="8" r="4" fill="{_STATUS_COLORS[status]}"/></svg>'
        ).encode()
        icon = Gio.BytesIcon.new(GLib.Bytes.new(svg))
        _status_icon_cache[status] = icon
    return icon


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, state: AppState, store: SessionStore, **kwargs) -> None:
        super().__init__(**kwargs)
        self.state = state
        self.store = store
        self.set_title("Agent Session Manager")
        self.set_icon_name("io.github.r4nd3l.AgentSessionManager")
        self._restore_window_geometry()
        self._pages: dict[str, Adw.TabPage] = {}  # session_id -> open tab
        self._confirmed_closes: set[Adw.TabPage] = set()
        self._closing_pages: dict[Adw.TabPage, int] = {}  # graceful close in progress -> attempts
        self._panel_close_asking: set[Adw.TabPage] = set()  # busy-panel dialog open
        self._panel_close_ok: set[Adw.TabPage] = set()  # user okayed killing the panel job
        self._quitting = False  # window close confirmed; draining tabs
        self._quit_asking = False  # the single quit-confirmation dialog is open
        self._menu_page: Adw.TabPage | None = None
        self._base_titles: dict[Adw.TabPage, str] = {}  # fork/new tab title without emoji
        # Tabs whose session id was just resolved, waiting for the store to
        # discover the session: page -> (session id, title at resolution).
        self._pending_resolved: dict[Adw.TabPage, tuple[str, str]] = {}
        self._idle_sources: dict[Adw.TabPage, int] = {}  # pending idle-notify timers
        self._switcher: QuickSwitcher | None = None

        self._install_actions()
        self._install_shortcuts()

        # --- content pane: header + tab bar + tab view ---
        self.tab_view = Adw.TabView()
        self.tab_view.connect("close-page", self._on_close_page)
        self.tab_view.connect("notify::selected-page", self._on_selected_page_changed)
        self.tab_view.connect("setup-menu", self._on_tab_setup_menu)

        tab_menu = Gio.Menu()
        tab_menu.append(_("Rename…"), "win.rename-tab")
        tab_menu.append(_("Set emoji…"), "win.set-tab-emoji")
        tab_menu.append(_("Copy session ID"), "win.copy-tab-session-id")
        tab_menu.append(_("Close"), "win.close-menu-tab")
        self.tab_view.set_menu_model(tab_menu)

        tab_bar = Adw.TabBar(view=self.tab_view)
        tab_bar.set_autohide(False)

        content_header = Adw.HeaderBar()
        self.sidebar_toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", active=True)
        self.sidebar_toggle.set_tooltip_text(_("Toggle sidebar (F9)"))
        content_header.pack_start(self.sidebar_toggle)

        new_menu = Gio.Menu()
        for provider in available_providers():
            new_menu.append(
                _("New {name} session…").format(name=provider.name),
                f"win.new-session-provider::{provider.id}",
            )
        for provider in available_providers():
            new_menu.append(
                _("New {name} session (advanced)…").format(name=provider.name),
                f"win.new-session-advanced::{provider.id}",
            )
            new_menu.append(
                _("Continue last {name} session…").format(name=provider.name),
                f"win.continue-session::{provider.id}",
            )
        for provider in available_providers():
            for variant in provider.chat_variants():  # native streaming chat
                if variant.label:
                    text = _("New {name} chat ({mode})").format(
                        name=provider.name, mode=_(variant.label)
                    )
                else:
                    text = _("New {name} chat…").format(name=provider.name)
                new_menu.append(text, f"win.new-chat-provider::{provider.id}:{variant.key}")
        new_menu.append(_("New window"), "app.new-window")
        new_btn = Adw.SplitButton(icon_name="tab-new-symbolic")
        new_btn.set_tooltip_text(_("New session (Ctrl+Shift+T)"))
        new_btn.set_menu_model(new_menu)
        new_btn.connect("clicked", lambda *_: self._new_session())
        content_header.pack_start(new_btn)

        self.close_all_btn = Gtk.Button(icon_name="tab-close-symbolic", visible=False)
        self.close_all_btn.set_tooltip_text(_("Close all tabs"))
        self.close_all_btn.connect("clicked", lambda *_: self._close_all_tabs())
        content_header.pack_start(self.close_all_btn)
        self.tab_view.connect(
            "notify::n-pages",
            lambda *_: self.close_all_btn.set_visible(self.tab_view.get_n_pages() > 1),
        )

        placeholder = Adw.StatusPage(
            icon_name="utilities-terminal-symbolic",
            title=_("No session open"),
            description=_("Pick a session from the sidebar, or start a new one."),
        )

        self.content_stack = Gtk.Stack()
        self.content_stack.add_named(placeholder, "empty")
        self.content_stack.add_named(self.tab_view, "tabs")

        # Small corner buttons controlling the current tab's terminal panel.
        toggle_panel_btn = Gtk.Button(icon_name="utilities-terminal-symbolic")
        toggle_panel_btn.set_tooltip_text(_("Show/hide terminal panel (Ctrl+J)"))
        toggle_panel_btn.connect("clicked", lambda *_: self._toggle_panel())
        swap_panel_btn = Gtk.Button(icon_name="object-rotate-right-symbolic")
        swap_panel_btn.set_tooltip_text(_("Move terminal panel bottom/right"))
        swap_panel_btn.connect("clicked", lambda *_: self._swap_panel())
        self._panel_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._panel_buttons.set_halign(Gtk.Align.END)
        self._panel_buttons.set_valign(Gtk.Align.END)
        self._panel_buttons.set_margin_end(6)
        self._panel_buttons.set_margin_bottom(6)
        self._panel_buttons.set_visible(False)
        for btn in (toggle_panel_btn, swap_panel_btn):
            btn.add_css_class("osd")
            btn.add_css_class("circular")
            self._panel_buttons.append(btn)

        content_overlay = Gtk.Overlay(child=self.content_stack)
        content_overlay.add_overlay(self._panel_buttons)

        content_view = Adw.ToolbarView()
        content_view.add_top_bar(content_header)
        content_view.add_top_bar(tab_bar)
        content_view.set_content(content_overlay)

        # --- sidebar ---
        self.sidebar = SessionSidebar(self.store)
        self.sidebar.connect("open-session", self._on_sidebar_open)
        self.sidebar.connect("open-many", self._on_sidebar_open_many)
        self.sidebar.connect("trash-many", self._on_sidebar_trash_many)
        self.store.connect("refreshed", self._on_store_refreshed)

        self.sidebar.set_size_request(220, -1)  # minimum drag width
        self.split = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.split.set_start_child(self.sidebar)
        self.split.set_end_child(content_view)
        self.split.set_resize_start_child(False)  # window resize grows the content, not the sidebar
        self.split.set_shrink_start_child(False)
        self.split.set_position(int(self.state.get_setting("sidebar_width")))
        self.split.connect("notify::position", self._schedule_save_sidebar_width)
        self.set_content(self.split)

        # Toggle button reflects (and controls) sidebar visibility.
        self._sidebar_width_save_source: int | None = None
        self.sidebar.bind_property(
            "visible",
            self.sidebar_toggle,
            "active",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )
        self.sidebar_toggle.connect(
            "toggled", lambda b: self.sidebar.set_visible(b.get_active())
        )

        # Persist window geometry (debounced, like the sidebar width).
        self._geometry_save_source: int | None = None
        for prop in ("default-width", "default-height", "maximized"):
            self.connect(f"notify::{prop}", self._schedule_save_window_geometry)
        self.connect("close-request", self._on_close_request)

    # -- window geometry persistence -----------------------------------------

    def _restore_window_geometry(self) -> None:
        """Reopen at the last used size, clamped to fit the current monitors.

        GTK4 has no API to read or set a window's on-screen position (Wayland
        compositors own placement), so only size + maximized state persist."""
        width = int(self.state.get_setting("window_width"))
        height = int(self.state.get_setting("window_height"))
        self.set_default_size(*clamp_window_size(width, height, _monitor_sizes()))
        if self.state.get_setting("window_maximized"):
            self.maximize()

    def _schedule_save_window_geometry(self, *_args) -> None:
        if self._geometry_save_source is not None:
            GLib.source_remove(self._geometry_save_source)
        self._geometry_save_source = GLib.timeout_add(600, self._save_window_geometry)

    def _save_window_geometry(self) -> bool:
        self._geometry_save_source = None
        values = {"window_maximized": bool(self.is_maximized())}
        # default-width/height track the floating (unmaximized) size live.
        width, height = self.get_default_size()
        if not self.is_maximized() and width > 0 and height > 0:
            values["window_width"] = width
            values["window_height"] = height
        self.state.update_settings(values)
        return GLib.SOURCE_REMOVE

    def _on_close_request(self, *_args) -> bool:
        if self._geometry_save_source is not None:
            GLib.source_remove(self._geometry_save_source)
        self._save_window_geometry()
        self._flush_panel_layouts()
        if self._quitting:
            return False  # tabs drained (or the user insisted) — really close
        busy = self._busy_tab_count()
        if busy == 0:
            return False  # nothing running; continue with the normal close
        if not self._quit_asking:  # one dialog for however many sessions are busy
            self._confirm_quit(busy)
        return True

    def _flush_panel_layouts(self) -> None:
        """Persist divider positions the 500ms debounce hasn't saved yet."""
        for i in range(self.tab_view.get_n_pages()):
            page = self.tab_view.get_nth_page(i)
            tab = page.get_child()
            session_id = self._session_id_of(page)
            if session_id and isinstance(tab, TerminalTab):
                self.state.set_panel_positions(session_id, tab.panel_positions)

    def _busy_tab_count(self) -> int:
        count = 0
        for i in range(self.tab_view.get_n_pages()):
            tab = self.tab_view.get_nth_page(i).get_child()
            if isinstance(tab, TerminalTab) and (
                tab.has_running_command() or tab.panel_has_running_command()
            ):
                count += 1
        return count

    def _confirm_quit(self, busy: int) -> None:
        self._quit_asking = True

        def do_quit() -> None:
            self._quit_asking = False
            self._quitting = True
            # The window-level dialog already covered the panels: don't let
            # each tab ask again. Agents still get their graceful /exit.
            for i in range(self.tab_view.get_n_pages()):
                self._panel_close_ok.add(self.tab_view.get_nth_page(i))
            # _on_close_page reissues the window close once the last tab drains
            # (immediately, if every close completes synchronously).
            self._close_all_tabs()

        dialogs.confirm_dialog(
            self,
            _("Close window with {n} active session(s)?").format(n=busy),
            _("Agents are asked to exit cleanly first; "
              "other running commands will be terminated."),
            _("Close Window"),
            do_quit,
            on_dismiss=lambda: setattr(self, "_quit_asking", False),
        )

    # -- sidebar width persistence -------------------------------------------

    def _schedule_save_sidebar_width(self, *_args) -> None:
        if not self.sidebar.get_visible():
            return  # don't persist the collapsed position
        if self._sidebar_width_save_source is not None:
            GLib.source_remove(self._sidebar_width_save_source)
        self._sidebar_width_save_source = GLib.timeout_add(600, self._save_sidebar_width)

    def _save_sidebar_width(self) -> bool:
        self._sidebar_width_save_source = None
        position = self.split.get_position()
        if position >= 150:
            self.state.set_setting("sidebar_width", position)
        return GLib.SOURCE_REMOVE

    # -- actions / shortcuts -------------------------------------------------

    def _install_actions(self) -> None:
        plain = {
            "refresh": lambda *_: self.store.refresh(),
            "new-session": lambda *_: self._new_session(),
            "preferences": lambda *_: self._show_preferences(),
            "mcp-servers": lambda *_: dialogs.mcp_browser_dialog(self),
            "focus-search": lambda *_: self.sidebar.focus_search(),
            "close-tab": lambda *_: self._close_current_tab(),
            "next-tab": lambda *_: self.tab_view.select_next_page(),
            "prev-tab": lambda *_: self.tab_view.select_previous_page(),
            "about": lambda *_: self._show_about(),
            "quick-switch": lambda *_: self._quick_switch(),
            "rename-tab": lambda *_: self._rename_tab(),
            "set-tab-emoji": lambda *_: self._set_tab_emoji(),
            "toggle-tab-emoji": lambda *_: self._toggle_tab_emoji(),
            "open-session-file": lambda *_: self._open_session_file(),
            "copy-tab-session-id": lambda *_: self._copy_tab_session_id(),
            "close-menu-tab": lambda *_: self._close_menu_tab(),
            "toggle-panel": lambda *_: self._toggle_panel(),
            "swap-panel": lambda *_: self._swap_panel(),
            "toggle-sidebar": lambda *_: self.sidebar.set_visible(
                not self.sidebar.get_visible()
            ),
        }
        for name, callback in plain.items():
            action = Gio.SimpleAction(name=name)
            action.connect("activate", callback)
            self.add_action(action)

        per_session = {
            "new-session-provider": lambda _a, p: self._choose_new_session_folder(
                get_provider(p.get_string())
            ),
            "new-session-in": lambda _a, p: self._start_new_session(p.get_string()),
            "new-chat-provider": lambda _a, p: self._new_chat_session_target(p.get_string()),
            "new-session-advanced": lambda _a, p: self._new_session_advanced(get_provider(p.get_string())),
            "continue-session": lambda _a, p: self._continue_session(get_provider(p.get_string())),
            "open-session": self._on_open_action,
            "fork-session": self._on_fork_action,
            "replay-session": self._on_replay_action,
            "resume-chat": self._on_resume_chat_action,
            "delete-session": self._on_delete_session,
            "open-ghostty": self._on_open_ghostty,
            "rename-session": self._on_rename_action,
            "toggle-favorite": lambda _a, p: self.store.toggle_favorite(p.get_string()),
            "copy-session-id": lambda _a, p: self.get_clipboard().set(p.get_string()),
            "reveal-transcript": self._on_reveal_transcript,
            "export-session": self._on_export_session,
            "session-details": self._on_session_details,
            "hide-session": self._on_hide_session,
            "trash-session": self._on_trash_session,
        }
        for name, callback in per_session.items():
            action = Gio.SimpleAction(name=name, parameter_type=GLib.VariantType("s"))
            action.connect("activate", callback)
            self.add_action(action)

        show_hidden = Gio.SimpleAction.new_stateful(
            "show-hidden", None, GLib.Variant.new_boolean(False)
        )
        show_hidden.connect("change-state", self._on_show_hidden)
        self.add_action(show_hidden)

    def _install_shortcuts(self) -> None:
        controller = Gtk.ShortcutController()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        for trigger, action in (
            ("<Control><Shift>f", "win.focus-search"),
            ("<Control><Shift>t", "win.new-session"),
            ("<Control><Shift>w", "win.close-tab"),
            ("<Control>Page_Down", "win.next-tab"),
            ("<Control>Page_Up", "win.prev-tab"),
            ("<Control>comma", "win.preferences"),
            ("<Control><Shift>k", "win.quick-switch"),
            ("<Control><Shift>e", "win.toggle-tab-emoji"),
            ("<Control>j", "win.toggle-panel"),
            ("F9", "win.toggle-sidebar"),
        ):
            controller.add_shortcut(
                Gtk.Shortcut.new(
                    Gtk.ShortcutTrigger.parse_string(trigger), Gtk.NamedAction.new(action)
                )
            )
        self.add_controller(controller)

    # -- sidebar signal handlers -------------------------------------------------

    def _on_sidebar_open(self, _sidebar, item: SessionItem, fork: bool) -> None:
        self.open_session(item.session, fork=fork)

    def _on_sidebar_open_many(self, _sidebar, items: list[SessionItem]) -> None:
        for item in items:
            self.open_session(item.session)

    def _on_sidebar_trash_many(self, _sidebar, items: list[SessionItem]) -> None:
        def do_trash() -> None:
            errors = []
            for item in items:
                error = self.store.trash(item.session_id)
                if error:
                    errors.append(f"{item.display_name}: {error}")
                    continue
                page = self._pages.get(item.session_id)
                if page is not None:
                    self.tab_view.close_page(page)
            if errors:
                dialogs.error_dialog(self, _("Some transcripts could not be trashed"), "\n".join(errors))

        dialogs.confirm_dialog(
            self,
            _("Move {n} transcript(s) to trash?").format(n=len(items)),
            _("The files are moved to the trash and can be restored."),
            _("Move to Trash"),
            do_trash,
        )

    # -- tabs --------------------------------------------------------------

    def open_session(self, session: Session, fork: bool = False) -> None:
        provider = get_provider(session.provider)
        fork = fork and provider.supports_fork
        if not fork:
            page = self._pages.get(session.session_id)
            if page is not None:
                self.tab_view.set_selected_page(page)
                return

        tab = TerminalTab(
            cwd=session.cwd,
            session_id=session.session_id,
            fork=fork,
            settings=self.state.settings,
            provider=provider,
            jsonl_path=session.jsonl_path,
        )
        tab.set_panel_positions(self.state.get_panel_positions(session.session_id))
        title = f"{self.store.display_name(session)} (fork)" if fork else self._tab_title(session)
        page = self._add_tab(tab, title,
                             f"{session.project_name} — {session.session_id}")
        if not fork:
            self._pages[session.session_id] = page
            self._sync_status(session.session_id)

    def _tab_title(self, session: Session) -> str:
        """Tab title with the session's saved emoji prefix (tabs only)."""
        name = self.store.display_name(session)
        emoji = self.state.get_emoji(session.session_id)
        return f"{emoji} {name}" if emoji else name

    def _default_provider(self):
        """First installed agent (Claude when present), used by the quick button."""
        providers = available_providers()
        return providers[0] if providers else get_provider("claude")

    def _new_session(self, provider=None) -> None:
        """Start in the remembered folder if it still exists, else ask."""
        provider = provider or self._default_provider()
        default = self.state.get_setting("new_session_dir")
        if default and Path(default).is_dir():
            self._start_new_session(default, provider)
        else:
            self._choose_new_session_folder(provider)

    def _choose_new_session_folder(self, provider=None) -> None:
        self._new_session_provider = provider or self._default_provider()
        dialog = Gtk.FileDialog(title=_("Choose project directory"))
        default = self.state.get_setting("new_session_dir")
        if default and Path(default).is_dir():
            dialog.set_initial_folder(Gio.File.new_for_path(default))
        dialog.select_folder(self, None, self._on_new_session_folder)

    def _on_new_session_folder(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return  # cancelled
        cwd = folder.get_path()
        self.state.set_setting("new_session_dir", cwd)  # remember for next time
        self._start_new_session(cwd, getattr(self, "_new_session_provider", None))

    def _start_new_session(self, cwd: str, provider=None, options=None) -> None:
        provider = provider or self._default_provider()
        tab = TerminalTab(
            cwd=cwd, session_id=None, settings=self.state.settings, provider=provider,
            options=options,
        )
        self._add_tab(
            tab,
            GLib.path_get_basename(cwd),
            f"new {provider.name} session — {cwd}",
        )

    # -- advanced new session / continue -----------------------------------

    def _new_session_advanced(self, provider) -> None:
        self._adv_provider = provider or self._default_provider()
        dialog = Gtk.FileDialog(title=_("Choose project directory"))
        default = self.state.get_setting("new_session_dir")
        if default and Path(default).is_dir():
            dialog.set_initial_folder(Gio.File.new_for_path(default))
        dialog.select_folder(self, None, self._on_advanced_folder)

    def _on_advanced_folder(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        cwd = folder.get_path()
        self.state.set_setting("new_session_dir", cwd)
        provider = getattr(self, "_adv_provider", None) or self._default_provider()
        dialogs.new_session_options_dialog(
            self, provider, lambda opts: self._start_new_session(cwd, provider, opts)
        )

    def _continue_session(self, provider) -> None:
        self._cont_provider = provider or self._default_provider()
        dialog = Gtk.FileDialog(title=_("Choose project directory"))
        default = self.state.get_setting("new_session_dir")
        if default and Path(default).is_dir():
            dialog.set_initial_folder(Gio.File.new_for_path(default))
        dialog.select_folder(self, None, self._on_continue_folder)

    def _on_continue_folder(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        cwd = folder.get_path()
        self.state.set_setting("new_session_dir", cwd)
        provider = getattr(self, "_cont_provider", None) or self._default_provider()
        tab = TerminalTab(
            cwd=cwd, settings=self.state.settings, provider=provider,
            command_override=provider.continue_command(),
        )
        self._add_tab(tab, GLib.path_get_basename(cwd), f"continue {provider.name} — {cwd}")

    def _add_tab(self, tab: TerminalTab, title: str, tooltip: str) -> Adw.TabPage:
        page = self.tab_view.append(tab)
        page.set_title(title)
        page.set_tooltip(tooltip)
        tab.connect("process-exited", self._on_process_exited, page)
        tab.connect("session-resolved", self._on_session_resolved, page)
        tab.connect("panel-layout-changed", self._on_panel_layout_changed, page)
        tab.terminal.connect("contents-changed", self._on_terminal_output, page)
        self.tab_view.set_selected_page(page)
        self.content_stack.set_visible_child_name("tabs")
        self._apply_tab_status(page)
        GLib.idle_add(tab.grab_terminal_focus)
        return page

    def _on_session_resolved(self, _tab: TerminalTab, session_id: str, page: Adw.TabPage) -> None:
        """A fresh tab (new / continue) discovered its session id: bind the tab
        to the session so the sidebar dot, open-dedup, rename and status sync
        work exactly like a tab opened from the sidebar."""
        if self._pages.get(session_id) not in (None, page):
            return  # another tab already owns this session
        _tab.set_panel_positions(self.state.get_panel_positions(session_id))
        self._pages[session_id] = page
        self._pending_resolved[page] = (session_id, page.get_title())
        self._sync_status(session_id)
        self._apply_resolved_sessions()

    def _on_panel_layout_changed(self, tab: TerminalTab, page: Adw.TabPage) -> None:
        session_id = self._session_id_of(page)
        if session_id:
            self.state.set_panel_positions(session_id, tab.panel_positions)

    def _on_store_refreshed(self, _store, _order_changed: bool) -> None:
        if self._pending_resolved:
            self._apply_resolved_sessions()

    def _apply_resolved_sessions(self) -> None:
        """Finish attaching resolved tabs once the store discovers their sessions
        (its rescan is debounced, so this may run a couple of seconds after the
        transcript appears)."""
        for page, (session_id, title) in list(self._pending_resolved.items()):
            session = self.store.get_session(session_id)
            if session is None:
                continue  # not scanned yet; retried on the next store refresh
            del self._pending_resolved[page]
            if page.get_title() == title:  # keep any manual rename/emoji
                page.set_title(self._tab_title(session))
            page.set_tooltip(f"{session.project_name} — {session.session_id}")
            self._sync_status(session_id)

    # -- chat sessions (headless streaming) --------------------------------

    def _new_chat_session_target(self, target: str) -> None:
        provider_id, _, variant_key = target.partition(":")
        self._new_chat_session(get_provider(provider_id), variant_key)

    def _new_chat_session(self, provider, variant_key: str = "") -> None:
        self._new_chat_provider = provider
        self._new_chat_variant_key = variant_key
        dialog = Gtk.FileDialog(title=_("Choose project directory"))
        default = self.state.get_setting("new_session_dir")
        if default and Path(default).is_dir():
            dialog.set_initial_folder(Gio.File.new_for_path(default))
        dialog.select_folder(self, None, self._on_new_chat_folder)

    def _on_new_chat_folder(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return  # cancelled
        cwd = folder.get_path()
        self.state.set_setting("new_session_dir", cwd)
        self._start_new_chat_session(
            cwd,
            getattr(self, "_new_chat_provider", None),
            getattr(self, "_new_chat_variant_key", ""),
        )

    def _start_new_chat_session(self, cwd: str, provider=None, variant_key: str = "") -> None:
        provider = provider or self._default_provider()
        variants = provider.chat_variants()
        variant = provider.chat_variant(variant_key) or (variants[0] if variants else None)
        if variant is None:
            return
        tab = ChatSessionTab(cwd=cwd, provider=provider, variant=variant)
        page = self.tab_view.append(tab)
        page.set_title(_("Chat — {dir}").format(dir=GLib.path_get_basename(cwd)))
        page.set_tooltip(f"{provider.name} chat — {cwd}")
        page.set_icon(_status_icon("open"))
        self.tab_view.set_selected_page(page)
        self.content_stack.set_visible_child_name("tabs")

    def _on_resume_chat_action(self, _action, param: GLib.Variant) -> None:
        variant_key, _, session_id = param.get_string().partition(":")
        session = self.store.get_session(session_id)
        if session is None:
            return
        provider = get_provider(session.provider)
        variants = provider.chat_variants()
        variant = provider.chat_variant(variant_key) or (variants[0] if variants else None)
        if variant is None:
            return
        tab = ChatSessionTab(
            cwd=session.cwd, provider=provider, variant=variant, resume_session_id=session_id
        )
        page = self.tab_view.append(tab)
        page.set_title(_("Chat — {name}").format(name=self.store.display_name(session)))
        page.set_tooltip(f"{provider.name} chat — {session.session_id}")
        page.set_icon(_status_icon("open"))
        self.tab_view.set_selected_page(page)
        self.content_stack.set_visible_child_name("tabs")

    # -- session replay ----------------------------------------------------

    def _on_replay_action(self, _action, param: GLib.Variant) -> None:
        session = self._session_for(param)
        if session is not None:
            self._open_replay(session)

    def _open_replay(self, session: Session) -> None:
        tab = ReplayTab(session, session.provider)
        page = self.tab_view.append(tab)
        page.set_title(_("Replay — {name}").format(name=self.store.display_name(session)))
        page.set_tooltip(f"replay — {session.project_name} — {session.session_id}")
        page.set_icon(_status_icon("open"))
        self.tab_view.set_selected_page(page)
        self.content_stack.set_visible_child_name("tabs")

    def _apply_tab_status(self, page: Adw.TabPage) -> None:
        """Mirror the sidebar status dot onto the tab itself."""
        status = "attention" if page.get_needs_attention() else "open"
        page.set_icon(_status_icon(status))

    def _close_current_tab(self) -> None:
        page = self.tab_view.get_selected_page()
        if page is not None:
            self.tab_view.close_page(page)

    def _close_all_tabs(self) -> None:
        pages = [self.tab_view.get_nth_page(i) for i in range(self.tab_view.get_n_pages())]
        for page in pages:
            self.tab_view.close_page(page)

    def _close_confirmed(self, page: Adw.TabPage) -> None:
        """Force-close (terminate the child) — the graceful-close fallback."""
        self._confirmed_closes.add(page)
        self.tab_view.close_page(page)

    def _page_alive(self, page: Adw.TabPage) -> bool:
        return any(
            self.tab_view.get_nth_page(i) is page for i in range(self.tab_view.get_n_pages())
        )

    def _ask_panel_close(self, page: Adw.TabPage, tab: TerminalTab) -> None:
        """Confirm closing a tab whose panel shell has a command running."""
        self._panel_close_asking.add(page)
        tab.show_panel()  # reveal what's about to be killed (a busy shell is never cd'd)

        def do_close() -> None:
            self._panel_close_asking.discard(page)
            self._panel_close_ok.add(page)
            if self._page_alive(page):  # continue: graceful agent close, then teardown
                self.tab_view.close_page(page)

        dialogs.confirm_dialog(
            self,
            _("Close tab with a running command?"),
            _("A command is still running in this tab's terminal panel "
              "and will be terminated."),
            _("Close Anyway"),
            do_close,
            on_dismiss=lambda: self._panel_close_asking.discard(page),
        )

    def _graceful_close(self, page: Adw.TabPage) -> None:
        """Ask the agent to exit cleanly (e.g. Claude's /exit), then close once the
        shell returns. Falls back to a force-close after a timeout. Agents with no
        clean-exit command (e.g. Cursor) are force-closed directly."""
        tab = page.get_child()
        if not isinstance(tab, TerminalTab):
            self._close_confirmed(page)
            return
        exit_text = tab.provider.graceful_exit()
        if not exit_text:
            self._close_confirmed(page)
            return
        self._closing_pages[page] = 0
        # Enter in a raw-mode TUI is carriage return, not newline.
        tab.feed_child_text(exit_text)
        GLib.timeout_add(300, self._poll_graceful, page, tab)

    def _poll_graceful(self, page: Adw.TabPage, tab: TerminalTab) -> bool:
        if page not in self._closing_pages:
            return GLib.SOURCE_REMOVE  # already closed
        if not tab.has_running_command():
            tab.feed_child_text("exit\r")  # close the shell → child-exited closes the tab
            return GLib.SOURCE_REMOVE
        self._closing_pages[page] += 1
        if self._closing_pages[page] >= 40:  # ~12s safety net
            self._close_confirmed(page)
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _sync_status(self, session_id: str) -> None:
        page = self._pages.get(session_id)
        if page is None:
            status = ""
        elif page.get_needs_attention():
            status = "attention"
        else:
            status = "open"
        self.store.set_status(session_id, status)
        self.sidebar.update_footer()

    def _session_id_of(self, page: Adw.TabPage) -> str | None:
        tab = page.get_child()
        if isinstance(tab, TerminalTab) and tab.session_id and not tab.fork:
            return tab.session_id
        return None

    def _on_terminal_output(self, _terminal, page: Adw.TabPage) -> None:
        if self.tab_view.get_selected_page() is page:
            return
        if not page.get_needs_attention():
            page.set_needs_attention(True)
            self._apply_tab_status(page)
            session_id = self._session_id_of(page)
            if session_id:
                self._sync_status(session_id)
        if self.state.get_setting("notify_idle"):
            self._schedule_idle_notify(page)

    # -- terminal panel ------------------------------------------------------

    def _current_terminal_tab(self) -> TerminalTab | None:
        page = self.tab_view.get_selected_page()
        tab = page.get_child() if page is not None else None
        return tab if isinstance(tab, TerminalTab) else None

    def _toggle_panel(self) -> None:
        tab = self._current_terminal_tab()
        if tab is not None:  # a freshly opened panel uses the last-used mode
            tab.toggle_panel(self.state.get_setting("panel_position"))

    def _swap_panel(self) -> None:
        tab = self._current_terminal_tab()
        if tab is not None:  # remember the choice as the default for new tabs
            self.state.set_setting("panel_position", tab.swap_panel())

    def _on_selected_page_changed(self, view: Adw.TabView, _pspec) -> None:
        page = view.get_selected_page()
        self._panel_buttons.set_visible(
            page is not None and isinstance(page.get_child(), TerminalTab)
        )
        if page is None:
            return
        self._cancel_idle(page)  # foreground now; no "finished" notification
        if page.get_needs_attention():
            page.set_needs_attention(False)
            self._apply_tab_status(page)
            session_id = self._session_id_of(page)
            if session_id:
                self._sync_status(session_id)
        if isinstance(page.get_child(), TerminalTab):
            GLib.idle_add(page.get_child().grab_terminal_focus)

    # -- idle notifications --------------------------------------------------

    def _schedule_idle_notify(self, page: Adw.TabPage) -> None:
        self._cancel_idle(page)
        self._idle_sources[page] = GLib.timeout_add(_IDLE_NOTIFY_MS, self._fire_idle_notify, page)

    def _cancel_idle(self, page: Adw.TabPage) -> None:
        source = self._idle_sources.pop(page, None)
        if source is not None:
            GLib.source_remove(source)

    def _fire_idle_notify(self, page: Adw.TabPage) -> bool:
        self._idle_sources.pop(page, None)
        if page is self.tab_view.get_selected_page() or not self.state.get_setting("notify_idle"):
            return GLib.SOURCE_REMOVE
        app = self.get_application()
        if app is not None:
            session_id = self._session_id_of(page) or ""
            notification = Gio.Notification.new(page.get_title())
            notification.set_body("Claude finished responding.")
            notification.set_default_action_and_target_value(
                "app.focus-session", GLib.Variant("s", session_id)
            )
            app.send_notification(session_id or page.get_title(), notification)
        return GLib.SOURCE_REMOVE

    def focus_session(self, session_id: str) -> None:
        page = self._pages.get(session_id)
        if page is not None:
            self.tab_view.set_selected_page(page)

    # -- tab rename / menu ---------------------------------------------------

    def _on_tab_setup_menu(self, _view: Adw.TabView, page: Adw.TabPage | None) -> None:
        if page is not None:
            self._menu_page = page

    def _rename_tab(self) -> None:
        page = self._menu_page or self.tab_view.get_selected_page()
        if page is None:
            return
        session_id = self._session_id_of(page)
        if session_id:  # real session tab → rename the session (syncs sidebar)
            session = self.store.get_session(session_id)
            if session is not None:
                self._prompt_rename_session(session)
            return
        # fork / new-session tab → local title rename only
        dialogs.rename_dialog(
            self,
            _("Tab name"),
            page.get_title(),
            lambda name: page.set_title(name.strip() or page.get_title()),
        )

    def _set_tab_emoji(self) -> None:
        page = self._menu_page or self.tab_view.get_selected_page()
        if page is None:
            return
        session_id = self._session_id_of(page)
        if session_id:
            current = self.state.get_emoji(session_id) or ""

            def save(emoji: str) -> None:
                self.state.set_emoji(session_id, emoji.strip())
                session = self.store.get_session(session_id)
                if session is not None:
                    page.set_title(self._tab_title(session))

            dialogs.emoji_dialog(self, current, save)
        else:  # fork / new tab: no persisted session, set a local prefix
            base = self._base_titles.get(page, page.get_title())
            self._base_titles[page] = base

            def save(emoji: str) -> None:
                emoji = emoji.strip()
                page.set_title(f"{emoji} {base}" if emoji else base)

            dialogs.emoji_dialog(self, "", save)

    def _toggle_tab_emoji(self) -> None:
        """Ctrl+Shift+E: toggle a 😊 marker on the current tab — no menu."""
        page = self.tab_view.get_selected_page()
        if page is None:
            return
        smile = "😊"
        session_id = self._session_id_of(page)
        if session_id:
            current = self.state.get_emoji(session_id) or ""
            self.state.set_emoji(session_id, "" if current == smile else smile)
            session = self.store.get_session(session_id)
            if session is not None:
                page.set_title(self._tab_title(session))
        else:  # fork / new tab: toggle a local title prefix
            base = self._base_titles.get(page, page.get_title())
            self._base_titles[page] = base
            page.set_title(base if page.get_title() != base else f"{smile} {base}")

    def _copy_tab_session_id(self) -> None:
        page = self._menu_page or self.tab_view.get_selected_page()
        if page is None:
            return
        tab = page.get_child()
        if isinstance(tab, TerminalTab) and tab.session_id:
            self.get_clipboard().set(tab.session_id)

    def _close_menu_tab(self) -> None:
        page = self._menu_page or self.tab_view.get_selected_page()
        if page is not None:
            self.tab_view.close_page(page)

    def _on_process_exited(self, _tab: TerminalTab, _status: int, page: Adw.TabPage) -> None:
        self.tab_view.close_page(page)

    def _on_close_page(self, view: Adw.TabView, page: Adw.TabPage) -> bool:
        tab = page.get_child()
        if (
            isinstance(tab, TerminalTab)
            and page not in self._confirmed_closes
            and page not in self._panel_close_ok
            and tab.panel_has_running_command()
        ):
            # The panel shell is busy. Unlike the agent there's no graceful
            # exit for an arbitrary command, so ask before killing it.
            view.close_page_finish(page, False)  # keep the tab while we ask
            if page not in self._panel_close_asking:
                self._ask_panel_close(page, tab)
            return True
        if (
            isinstance(tab, TerminalTab)
            and page not in self._confirmed_closes
            and tab.has_running_command()
        ):
            if page not in self._closing_pages:  # start a graceful /exit in the background
                self._graceful_close(page)
            view.close_page_finish(page, False)  # keep the tab until it exits cleanly
            return True
        self._confirmed_closes.discard(page)
        self._closing_pages.pop(page, None)
        self._panel_close_asking.discard(page)
        self._panel_close_ok.discard(page)
        self._base_titles.pop(page, None)
        self._pending_resolved.pop(page, None)
        self._cancel_idle(page)
        session_id = self._session_id_of(page)
        if session_id:
            if isinstance(tab, TerminalTab):  # catch a drag the debounce missed
                self.state.set_panel_positions(session_id, tab.panel_positions)
            self._pages.pop(session_id, None)
            self._sync_status(session_id)
        view.close_page_finish(page, True)
        if view.get_n_pages() == 0:
            self.content_stack.set_visible_child_name("empty")
            if self._quitting:  # last tab drained — finish the window close
                GLib.idle_add(self.close)
        return True  # we handled it

    # -- per-session actions ---------------------------------------------------

    def _session_for(self, param: GLib.Variant) -> Session | None:
        return self.store.get_session(param.get_string())

    def _on_open_action(self, _action, param: GLib.Variant) -> None:
        session = self._session_for(param)
        if session:
            self.open_session(session)

    def _on_fork_action(self, _action, param: GLib.Variant) -> None:
        session = self._session_for(param)
        if session:
            self.open_session(session, fork=True)

    def _on_open_ghostty(self, _action, param: GLib.Variant) -> None:
        session = self._session_for(param)
        if session is None or _GHOSTTY is None:
            return
        provider = get_provider(session.provider)
        if shutil.which(provider.cli) is None:
            return
        cwd = session.cwd if session.cwd and Path(session.cwd).is_dir() else str(Path.home())
        subprocess.Popen(
            [_GHOSTTY, f"--working-directory={cwd}", "-e",
             provider.cli, "--resume", session.session_id],
            start_new_session=True,
        )

    def _on_rename_action(self, _action, param: GLib.Variant) -> None:
        session = self._session_for(param)
        if session is not None:
            self._prompt_rename_session(session)

    def _prompt_rename_session(self, session: Session) -> None:
        def save(name: str) -> None:
            self.store.rename(session.session_id, name)
            page = self._pages.get(session.session_id)
            if page is not None:
                page.set_title(self._tab_title(session))

        dialogs.rename_dialog(
            self,
            session.preview or session.session_id,
            self.state.get_name(session.session_id) or "",
            save,
        )

    def _on_reveal_transcript(self, _action, param: GLib.Variant) -> None:
        session = self._session_for(param)
        if session is None:
            return
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(str(session.jsonl_path)))
        launcher.open_containing_folder(self, None, None)

    def _on_export_session(self, _action, param: GLib.Variant) -> None:
        session = self._session_for(param)
        if session is None:
            return
        title = self.store.display_name(session)
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip() or "session"
        dialog = Gtk.FileDialog(title=_("Export session as Markdown"), initial_name=f"{safe}.md")
        dialog.save(self, None, lambda d, r: self._on_export_save(d, r, session, title))

    def _on_export_save(self, dialog: Gtk.FileDialog, result, session: Session, title: str) -> None:
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error:
            return  # cancelled
        dest = gfile.get_path()

        def work() -> None:
            error = None
            try:
                text = export_markdown(session.jsonl_path, title, session.session_id, session.cwd)
                Path(dest).write_text(text, encoding="utf-8")
            except OSError as err:
                error = str(err)
            if error:
                GLib.idle_add(dialogs.error_dialog, self, _("Export failed"), error)

        threading.Thread(target=work, daemon=True).start()

    def _on_session_details(self, _action, param: GLib.Variant) -> None:
        session = self._session_for(param)
        if session is not None:
            dialogs.details_dialog(self, session, self.store.display_name(session))

    def _on_hide_session(self, _action, param: GLib.Variant) -> None:
        session_id = param.get_string()
        self.store.set_hidden(session_id, not self.state.is_hidden(session_id))

    def _on_show_hidden(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        action.set_state(value)
        self.store.set_show_hidden(value.get_boolean())

    def _on_trash_session(self, _action, param: GLib.Variant) -> None:
        session = self._session_for(param)
        if session is None:
            return

        def do_trash() -> None:
            error = self.store.trash(session.session_id)
            if error:
                dialogs.error_dialog(self, _("Could not trash transcript"), error)
                return
            page = self._pages.get(session.session_id)
            if page is not None:
                self.tab_view.close_page(page)

        dialogs.confirm_dialog(
            self,
            _("Move transcript to trash?"),
            _("“{name}” will be removed from Claude's history.").format(
                name=self.store.display_name(session)
            )
            + "\n"
            + _("The file is moved to the trash and can be restored."),
            _("Move to Trash"),
            do_trash,
        )

    def _on_delete_session(self, _action, param: GLib.Variant) -> None:
        session = self._session_for(param)
        if session is None:
            return

        def do_delete() -> None:
            error = self.store.delete(session.session_id)
            if error:
                dialogs.error_dialog(self, _("Could not delete transcript"), error)
                return
            page = self._pages.get(session.session_id)
            if page is not None:
                self.tab_view.close_page(page)

        dialogs.confirm_dialog(
            self,
            _("Delete session permanently?"),
            _("“{name}” and its transcript file will be permanently deleted. "
              "This cannot be undone.").format(name=self.store.display_name(session)),
            _("Delete permanently"),
            do_delete,
        )

    # -- open transcript from file -----------------------------------------

    def _open_session_file(self) -> None:
        dialog = Gtk.FileDialog(title=_("Open session transcript"))
        jsonl_filter = Gtk.FileFilter()
        jsonl_filter.set_name(_("Session transcripts (*.jsonl)"))
        jsonl_filter.add_pattern("*.jsonl")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(jsonl_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(jsonl_filter)
        claude_dir = Path.home() / ".claude"
        if claude_dir.is_dir():
            dialog.set_initial_folder(Gio.File.new_for_path(str(claude_dir)))
        dialog.open(self, None, self._on_session_file_chosen)

    def _on_session_file_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return  # cancelled
        session = session_from_file(Path(gfile.get_path()))
        if session is None:
            dialogs.error_dialog(
                self,
                _("Could not open transcript"),
                _("The file couldn't be read as a session transcript."),
            )
            return
        self._open_replay(session)

    # -- preferences / about -------------------------------------------------

    def _show_about(self) -> None:
        about = Adw.AboutDialog(
            application_name="Agent Session Manager",
            application_icon="io.github.r4nd3l.AgentSessionManager",
            developer_name="Máté Molnár",
            version=__version__,
            license_type=Gtk.License.GPL_3_0,
            comments=(
                _("Manage and resume your AI coding agent sessions.\n\n"
                "Unofficial community tool — not affiliated with or endorsed by Anthropic.")
            ),
            website="https://github.com/r4nd3l/agent-session-manager",
            issue_url="https://github.com/r4nd3l/agent-session-manager/issues",
        )
        about.present(self)

    def _quick_switch(self) -> None:
        if self._switcher is not None:  # already open — don't stack another
            return
        self._switcher = QuickSwitcher(self.store, lambda item: self.open_session(item.session))
        self._switcher.connect("closed", lambda *_: setattr(self, "_switcher", None))
        self._switcher.present(self)

    def _show_preferences(self) -> None:
        PreferencesDialog(self.state, self._apply_preferences).present(self)

    def _apply_preferences(self) -> None:
        self._apply_settings_to_tabs()
        self.sidebar.refresh_folder_path()

    def _apply_settings_to_tabs(self) -> None:
        for i in range(self.tab_view.get_n_pages()):
            tab = self.tab_view.get_nth_page(i).get_child()
            if isinstance(tab, TerminalTab):
                tab.apply_settings(self.state.settings)
