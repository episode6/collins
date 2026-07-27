# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-26. Full change history: git log for this file.
"""Main window: composes the session sidebar with the tabbed terminal area."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from . import __version__, dialogs, panelhistory
from .chatsessionview import ChatSessionTab
from .i18n import _
from .models import SessionItem
from .prefs import PreferencesDialog
from .providers import available_providers, get_provider
from .replayview import ReplayTab
from .sessions import (
    Session,
    export_markdown,
    first_message_uuid,
    resume_cwd,
    session_from_file,
)
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
        self.set_title("Collins")
        self.set_icon_name("com.episode6.Collins")
        self._restore_window_geometry()
        self._pages: dict[str, Adw.TabPage] = {}  # session_id -> open tab
        self._confirmed_closes: set[Adw.TabPage] = set()
        self._closing_pages: dict[Adw.TabPage, int] = {}  # graceful close in progress -> attempts
        self._close_asking: set[Adw.TabPage] = set()  # busy-tab confirm dialog open
        self._close_ok: set[Adw.TabPage] = set()  # user okayed closing the busy tab
        self._bg_ok: set[Adw.TabPage] = set()  # user chose to background the agent instead
        # Hide requested for an open session: applied only once its tab
        # really closes (page -> session id).
        self._hide_on_close: dict[Adw.TabPage, str] = {}
        self._quitting = False  # window close confirmed; draining tabs
        self._quit_asking = False  # the single quit-confirmation dialog is open
        # Active tab's session at the first close request, before the tab
        # drain disturbs the selection ("" = none); persisted when the last
        # window really closes so the next launch can reopen it.
        self._last_active_session = ""
        # Session to reopen once the store's first scan delivers it.
        self._restore_session_id: str | None = None
        self._menu_page: Adw.TabPage | None = None
        self._base_titles: dict[Adw.TabPage, str] = {}  # fork/new tab title without emoji
        # Tabs whose session id was just resolved, waiting for the store to
        # discover the session: page -> (session id, title at resolution).
        self._pending_resolved: dict[Adw.TabPage, tuple[str, str]] = {}
        # New-session tabs shown as "New Thread" placeholder rows in the
        # sidebar until the store discovers their session: page -> placeholder id.
        self._placeholder_pages: dict[Adw.TabPage, str] = {}
        self._placeholder_seq = 0
        # Tabs renamed locally before their session was bound: never auto-sync
        # their titles from the store.
        self._local_titles: set[Adw.TabPage] = set()
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

        self.tab_bar = Adw.TabBar(view=self.tab_view)
        self.tab_bar.set_autohide(False)
        self.tab_bar.set_visible(bool(self.state.get_setting("show_tab_bar")))

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
        self.tab_bar_toggle = Gtk.ToggleButton(
            icon_name="view-paged-symbolic",
            active=self.tab_bar.get_visible(),
        )
        self.tab_bar_toggle.set_tooltip_text(_("Show or hide the tab bar"))
        self.tab_bar_toggle.connect("toggled", self._on_tab_bar_toggled)
        content_header.pack_start(self.tab_bar_toggle)

        new_btn.connect("clicked", lambda *_: self._new_session())
        content_header.pack_start(new_btn)

        self.exit_btn = Gtk.Button(icon_name="tab-close-symbolic", visible=False)
        self.exit_btn.set_tooltip_text(_("Exit session and close tab"))
        self.exit_btn.connect("clicked", lambda *_: self._exit_current_tab())
        content_header.pack_start(self.exit_btn)
        self.background_btn = Gtk.Button(icon_name="document-save-symbolic", visible=False)
        self.background_btn.set_tooltip_text(_("Background session and close tab"))
        self.background_btn.connect("clicked", lambda *_: self._background_current_tab())
        content_header.pack_start(self.background_btn)

        placeholder = Adw.StatusPage(
            icon_name="utilities-terminal-symbolic",
            title=_("No session open"),
            description=_("Pick a session from the sidebar, or start a new one."),
        )

        self.content_stack = Gtk.Stack()
        self.content_stack.add_named(placeholder, "empty")
        self.content_stack.add_named(self.tab_view, "tabs")

        content_view = Adw.ToolbarView()
        content_view.add_top_bar(content_header)
        content_view.add_top_bar(self.tab_bar)
        content_view.set_content(self.content_stack)

        # --- sidebar ---
        self.sidebar = SessionSidebar(self.store)
        self.sidebar.connect("open-session", self._on_sidebar_open)
        self.sidebar.connect("open-many", self._on_sidebar_open_many)
        self.sidebar.connect("trash-many", self._on_sidebar_trash_many)
        self.sidebar.connect("hide-many", self._on_sidebar_hide_many)
        self.sidebar.connect("open-placeholder", self._on_sidebar_open_placeholder)
        self.sidebar.connect("close-placeholder", self._on_sidebar_close_placeholder)
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
        if self._quitting:
            # The user insisted mid-drain: any tabs still alive skip the
            # per-tab drain, so capture their panel histories and state now
            # (a no-op for tabs that already drained through _on_close_page).
            self._save_panel_data()
            self._persist_last_session()
            return False  # tabs drained (or the user insisted) — really close
        self._last_active_session = self._active_session_id() or ""
        busy = self._busy_tab_count()
        if busy == 0:
            # This close skips the per-tab drain, so capture panel histories
            # and state here.
            self._save_panel_data()
            self._persist_last_session()
            return False  # nothing running; continue with the normal close
        if not self._quit_asking:  # one dialog for however many sessions are busy
            self._confirm_quit(busy)
        return True

    def _active_session_id(self) -> str | None:
        page = self.tab_view.get_selected_page()
        return self._session_id_of(page) if page is not None else None

    def _persist_last_session(self) -> None:
        """Remember the active tab's session when the last window closes, so
        the next launch reopens it. A non-session active tab (fork, chat,
        replay, unresolved new session) clears the memory instead — reopening
        would restore a tab the user wasn't actually looking at."""
        app = self.get_application()
        windows = app.get_windows() if app is not None else []
        if any(isinstance(w, MainWindow) and w is not self for w in windows):
            return  # another window remains; its close records the session
        if self.state.get_setting("last_active_session") != self._last_active_session:
            self.state.set_setting("last_active_session", self._last_active_session)

    def _save_panel_data(self) -> None:
        for i in range(self.tab_view.get_n_pages()):
            tab = self.tab_view.get_nth_page(i).get_child()
            if isinstance(tab, TerminalTab):
                tab.save_panel_history()
                self._save_panel_state(tab)

    def _save_panel_state(self, tab: TerminalTab) -> None:
        """Persist the tab's panel open/mode/size for its session. A tab that
        never used the panel leaves the session's saved state alone."""
        if tab.fork or not tab.session_id:
            return
        state = tab.capture_panel_state()
        if state is not None:
            self.state.set_panel_state(tab.session_id, state)

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

        def do_quit(background: bool = False) -> None:
            self._quit_asking = False
            self._quitting = True
            # The window-level dialog already covered every tab: don't let
            # each one ask again. Agents still get their graceful /exit
            # (or /bg, if the user chose to background them).
            for i in range(self.tab_view.get_n_pages()):
                page = self.tab_view.get_nth_page(i)
                self._close_ok.add(page)
                if background:
                    self._bg_ok.add(page)
            # _on_close_page reissues the window close once the last tab drains
            # (immediately, if every close completes synchronously).
            self._close_all_tabs()

        can_background = any(
            isinstance(tab := self.tab_view.get_nth_page(i).get_child(), TerminalTab)
            and tab.has_running_command()
            and tab.provider.background_exit() is not None
            for i in range(self.tab_view.get_n_pages())
        )
        body = _("Agents are asked to exit cleanly first; "
                 "other running commands will be terminated.")
        if can_background:
            body = _("Agents are asked to exit cleanly first; other running "
                     "commands will be terminated. Backgrounding instead keeps "
                     "the agents running detached — reopen a session later to "
                     "re-attach.")
        dialogs.confirm_dialog(
            self,
            _("Close window with {n} active session(s)?").format(n=busy),
            body,
            _("Exit Sessions"),
            do_quit,
            on_dismiss=lambda: setattr(self, "_quit_asking", False),
            default_response="confirm",
            extra_label=_("Background Sessions") if can_background else None,
            on_extra=(lambda: do_quit(background=True)) if can_background else None,
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
            "clear-panel": lambda *_: self._clear_panel(),
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
            "regenerate-name": lambda _a, p: self.store.regenerate_name(p.get_string()),
            "toggle-favorite": lambda _a, p: self.store.toggle_favorite(p.get_string()),
            "copy-session-id": lambda _a, p: self.get_clipboard().set(p.get_string()),
            "reveal-transcript": self._on_reveal_transcript,
            "export-session": self._on_export_session,
            "session-details": self._on_session_details,
            "hide-session": self._on_hide_session,
            "hide-project": self._on_hide_project,
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
            ("<Control>w", "win.close-tab"),
            ("<Control>Page_Down", "win.next-tab"),
            ("<Control>Page_Up", "win.prev-tab"),
            ("<Control>comma", "win.preferences"),
            ("<Control><Shift>k", "win.quick-switch"),
            ("<Control><Shift>e", "win.toggle-tab-emoji"),
            ("<Control>j", "win.toggle-panel"),
            ("<Control>k", "win.clear-panel"),
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
                panelhistory.delete(item.session_id)
                self.state.set_panel_state(item.session_id, None)
            if errors:
                dialogs.error_dialog(self, _("Some transcripts could not be trashed"), "\n".join(errors))

        dialogs.confirm_dialog(
            self,
            _("Move {n} transcript(s) to trash?").format(n=len(items)),
            _("The files are moved to the trash and can be restored."),
            _("Move to Trash"),
            do_trash,
        )

    def _on_sidebar_hide_many(self, _sidebar, items: list[SessionItem]) -> None:
        self.store.hide_many([item.session_id for item in items])
        for item in items:
            self._close_session_tab(item.session_id)

    # -- tabs --------------------------------------------------------------

    def restore_last_session(self) -> None:
        """Reopen the session that was in the active tab when the app was last
        closed. Called only for a launch's first window; the store scans in
        the background, so the open may wait for its first refresh."""
        session_id = self.state.get_setting("last_active_session")
        if not session_id:
            return
        session = self.store.get_session(session_id)
        if session is not None:
            self.open_session(session)
        else:
            self._restore_session_id = str(session_id)

    def _apply_restore_session(self) -> None:
        """One shot on the store's first refresh: if the remembered session
        still exists (and the user hasn't opened anything first), reopen it."""
        session_id, self._restore_session_id = self._restore_session_id, None
        if not session_id or self.tab_view.get_n_pages() > 0:
            return
        session = self.store.get_session(session_id)
        if session is not None:
            self.open_session(session)

    def open_session(self, session: Session, fork: bool = False) -> None:
        provider = get_provider(session.provider)
        fork = fork and provider.supports_fork
        if not fork:
            # A backgrounded session may have continued under a new id (/bg
            # forks the conversation): open the live end of the chain instead
            # of the stale original. Forks stay on the id the user picked.
            forward = self.store.forward_state(session)
            if forward == "moved":
                session = self.store.get_session(
                    self.state.resolve_forward(session.session_id)
                )
            elif forward == "syncing":
                # The fork exists but the store hasn't scanned it yet.
                # Its sidebar row is disabled during this window; guard
                # the other entry paths (switcher, session restore) too
                # rather than open the stale original.
                return
            # Fork transcript gone (e.g. trashed) or never a real session
            # (dead /bg stub): stale forward — open the original normally.
            page = self._pages.get(session.session_id)
            if page is not None:
                self.tab_view.set_selected_page(page)
                return

        tab = TerminalTab(
            cwd=resume_cwd(session),
            session_id=session.session_id,
            fork=fork,
            settings=self.state.settings,
            provider=provider,
            jsonl_path=session.jsonl_path,
        )
        title = f"{self.store.display_name(session)} (fork)" if fork else self._tab_title(session)
        page = self._add_tab(tab, title,
                             f"{session.project_name} — {session.session_id}")
        if not fork:
            self._pages[session.session_id] = page
            self._sync_status(session.session_id)
            saved_panel = self.state.get_panel_state(session.session_id)
            if saved_panel:  # reopen the panel the way this session left it
                tab.restore_panel_state(saved_panel)

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
        page = self._add_tab(
            tab,
            GLib.path_get_basename(cwd),
            f"new {provider.name} session — {cwd}",
        )
        self._add_placeholder(page, cwd)

    # -- sidebar placeholders for unresolved new-session tabs ----------------

    def _add_placeholder(self, page: Adw.TabPage, cwd: str) -> None:
        """Give a fresh new-session tab a transient "New Thread" sidebar row
        until the store discovers the real session."""
        self._placeholder_seq += 1
        placeholder_id = f"placeholder-{self._placeholder_seq}"
        self._placeholder_pages[page] = placeholder_id
        self.sidebar.add_placeholder(placeholder_id, cwd)
        self._update_active_row()

    def _remove_placeholder(self, page: Adw.TabPage) -> None:
        placeholder_id = self._placeholder_pages.pop(page, None)
        if placeholder_id is not None:
            self.sidebar.remove_placeholder(placeholder_id)

    def _placeholder_page(self, placeholder_id: str) -> Adw.TabPage | None:
        for page, pid in self._placeholder_pages.items():
            if pid == placeholder_id:
                return page
        return None

    def _on_sidebar_open_placeholder(self, _sidebar, placeholder_id: str) -> None:
        page = self._placeholder_page(placeholder_id)
        if page is not None:
            self.tab_view.set_selected_page(page)

    def _on_sidebar_close_placeholder(self, _sidebar, placeholder_id: str) -> None:
        page = self._placeholder_page(placeholder_id)
        if page is not None:  # usual close flow: busy tabs confirm first
            self.tab_view.close_page(page)

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
        tab.connect("panel-size-changed", self._on_panel_size_changed)
        tab.set_panel_size_lookup(lambda mode: int(self.state.get_setting(f"panel_size_{mode}") or 0))
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
        self._pages[session_id] = page
        self._pending_resolved[page] = (session_id, page.get_title())
        self._sync_status(session_id)
        self._update_active_row()  # the resolved tab may be the selected one
        self._apply_resolved_sessions()

    def _on_panel_size_changed(self, _tab: TerminalTab, mode: str, size: int) -> None:
        """A divider was dragged: remember the size app-wide, so every panel
        opened from now on (in any tab) defaults to it."""
        key = f"panel_size_{mode}"
        if self.state.get_setting(key) != size:
            self.state.set_setting(key, size)

    def _on_store_refreshed(self, _store, _order_changed: bool) -> None:
        if self._restore_session_id is not None:
            self._apply_restore_session()
        if self._pending_resolved:
            self._apply_resolved_sessions()
        self._refresh_tab_titles()

    def _apply_resolved_sessions(self) -> None:
        """Finish attaching resolved tabs once the store discovers their sessions
        (its rescan is debounced, so this may run a couple of seconds after the
        transcript appears)."""
        for page, (session_id, title) in list(self._pending_resolved.items()):
            session = self.store.get_session(session_id)
            if session is None:
                continue  # not scanned yet; retried on the next store refresh
            del self._pending_resolved[page]
            self._remove_placeholder(page)  # the real sidebar row exists now
            if page.get_title() == title:  # keep any manual rename/emoji
                page.set_title(self._tab_title(session))
            else:
                self._local_titles.add(page)
            page.set_tooltip(f"{session.project_name} — {session.session_id}")
            self._sync_status(session_id)
        self._update_active_row()  # hand the highlight from placeholder to real row

    def _refresh_tab_titles(self) -> None:
        """Keep tab titles in sync with the store: auto-generated titles arrive
        after the tab is already open, and sidebar renames / regenerated names
        land through here too. Session-bound titles are always derived from
        persisted state, so recomputing them never loses a manual rename."""
        for session_id, page in self._pages.items():
            if page in self._pending_resolved or page in self._local_titles:
                continue
            session = self.store.get_session(session_id)
            if session is None:
                continue
            title = self._tab_title(session)
            if page.get_title() != title:
                page.set_title(title)

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
            cwd=resume_cwd(session), provider=provider, variant=variant,
            resume_session_id=session_id,
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
        tab = ReplayTab(session)
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

    def _on_tab_bar_toggled(self, button: Gtk.ToggleButton) -> None:
        """Purely visual: the tabs (and their sessions) keep running underneath."""
        show = button.get_active()
        self.tab_bar.set_visible(show)
        self.state.set_setting("show_tab_bar", show)

    def _close_current_tab(self) -> None:
        page = self.tab_view.get_selected_page()
        if page is not None:
            self.tab_view.close_page(page)

    def _close_all_tabs(self) -> None:
        pages = [self.tab_view.get_nth_page(i) for i in range(self.tab_view.get_n_pages())]
        for page in pages:
            self.tab_view.close_page(page)

    def _exit_current_tab(self) -> None:
        """Header button: exit the focused session and close its tab without
        the confirmation dialog — the click itself is the confirmation."""
        page = self.tab_view.get_selected_page()
        if page is not None:
            self._close_ok.add(page)
            self.tab_view.close_page(page)

    def _background_current_tab(self) -> None:
        """Header button: background (detach) the focused session and close
        its tab without the confirmation dialog."""
        page = self.tab_view.get_selected_page()
        if page is not None:
            self._close_ok.add(page)
            self._bg_ok.add(page)
            self.tab_view.close_page(page)

    def _close_confirmed(self, page: Adw.TabPage) -> None:
        """Force-close (terminate the child) — the graceful-close fallback."""
        self._confirmed_closes.add(page)
        self.tab_view.close_page(page)

    def _page_alive(self, page: Adw.TabPage) -> bool:
        return any(
            self.tab_view.get_nth_page(i) is page for i in range(self.tab_view.get_n_pages())
        )

    def _ask_tab_close(self, page: Adw.TabPage, tab: TerminalTab) -> None:
        """Confirm closing a tab with an active agent session and/or a command
        running in its panel shell."""
        self._close_asking.add(page)
        agent_busy = tab.has_running_command()
        panel_busy = tab.panel_has_running_command()
        if panel_busy:
            tab.show_panel()  # reveal what's about to be killed (a busy shell is never cd'd)

        def do_close(background: bool = False) -> None:
            self._close_asking.discard(page)
            self._close_ok.add(page)
            if background:
                self._bg_ok.add(page)
            if self._page_alive(page):  # continue: graceful agent close, then teardown
                self.tab_view.close_page(page)

        can_background = agent_busy and tab.provider.background_exit() is not None
        if agent_busy and panel_busy:
            heading = _("Close tab with an active session?")
            body = _("The agent is asked to exit cleanly first; the command "
                     "running in this tab's terminal panel will be terminated.")
        elif agent_busy:
            heading = _("Close tab with an active session?")
            body = _("The agent is asked to exit cleanly first.")
        else:
            heading = _("Close tab with a running command?")
            body = _("A command is still running in this tab's terminal panel "
                     "and will be terminated.")
        if can_background:
            body += " " + _("Backgrounding instead keeps the agent running "
                            "detached — reopen the session later to re-attach.")
        # A panel-only-busy tab has no agent session to exit — say "Close Tab".
        confirm_label = _("Exit Session") if agent_busy else _("Close Tab")
        def dismiss() -> None:
            self._close_asking.discard(page)
            self._hide_on_close.pop(page, None)  # cancelled: keep the session visible

        dialogs.confirm_dialog(
            self,
            heading,
            body,
            confirm_label,
            do_close,
            on_dismiss=dismiss,
            default_response="confirm",
            extra_label=_("Background Session") if can_background else None,
            on_extra=(lambda: do_close(background=True)) if can_background else None,
        )

    def _graceful_close(self, page: Adw.TabPage) -> None:
        """Ask the agent to exit cleanly (e.g. Claude's /exit) — or to detach
        (e.g. /bg) if the user chose to background it — then close once the
        shell returns. Falls back to a force-close after a timeout. Agents with
        no clean-exit command are force-closed directly."""
        tab = page.get_child()
        if not isinstance(tab, TerminalTab):
            self._close_confirmed(page)
            return
        exit_text = tab.provider.background_exit() if page in self._bg_ok else None
        if exit_text:
            self._watch_background_fork(tab)
        else:
            exit_text = tab.provider.graceful_exit()
        if not exit_text:
            self._close_confirmed(page)
            return
        self._closing_pages[page] = 0
        # Enter in a raw-mode TUI is carriage return, not newline.
        tab.feed_child_text(exit_text)
        GLib.timeout_add(300, self._poll_graceful, page, tab)

    def _watch_background_fork(self, tab: TerminalTab) -> None:
        """/bg doesn't detach the session in place: Claude spawns a background
        agent under a *new* session id whose transcript is a copy of the
        conversation, leaving the original behind as a stale duplicate. Watch
        the agent list (off the main thread) for that successor and record
        old -> new, so the stale row is hidden, the user's name/emoji/favorite
        carry over, and opening the old session redirects to the live one.

        A tab that was *attached* to an already-detached session is different:
        /bg spawns no fork there — the CLI just drops back to its agent-list
        screen and keeps the terminal, so the close would hang until the
        force-close safety net. Its own session id showing up as a background
        agent is the tell; either way, once the session is confirmed running
        detached, the CLI gets an /exit nudge if it still holds the terminal."""
        old_id = tab.session_id
        provider = tab.provider
        if not old_id or tab.fork:
            return
        cwd = tab.current_agent_cwd()
        old_session = self.store.get_session(old_id)
        old_uuid = (
            first_message_uuid(old_session.jsonl_path) if old_session is not None else None
        )
        known = {a.session_id for a in provider.background_agents()}

        def matches(agent) -> bool:
            if agent.session_id in known or agent.session_id == old_id:
                return False
            # Same conversation = same copied first-message uuid. That
            # disambiguates several same-project tabs backgrounded at once
            # (e.g. the quit flow); fall back to cwd when uuids are
            # unavailable (transcript not yet written / no messages).
            path = next(
                (
                    p
                    for p in provider.transcripts_for_cwd(agent.cwd)
                    if p.stem == agent.session_id
                ),
                None,
            )
            new_uuid = first_message_uuid(path) if path is not None else None
            if old_uuid and new_uuid:
                return old_uuid == new_uuid
            return bool(cwd and agent.cwd and agent.cwd == cwd)

        def work() -> None:
            for _attempt in range(30):  # the fork appears within seconds of the /bg
                for agent in provider.background_agents():
                    if agent.session_id == old_id:
                        # Already detached in place (this tab was attached to
                        # a running background agent): no fork to record.
                        GLib.idle_add(self._on_backgrounded, tab, old_id, "")
                        return
                    if matches(agent):
                        GLib.idle_add(self._on_backgrounded, tab, old_id, agent.session_id)
                        return
                time.sleep(1)

        threading.Thread(target=work, daemon=True).start()

    def _on_backgrounded(self, tab: TerminalTab, old_id: str, new_id: str) -> bool:
        """The tab's session is confirmed running detached (new_id is its
        fork's session id, or "" when it detached in place)."""
        if new_id:
            self.store.record_forward(old_id, new_id)
        # Give the CLI a moment to exit on its own before nudging it off any
        # screen it parked on (see _watch_background_fork).
        GLib.timeout_add(700, self._nudge_cli_exit, tab)
        return GLib.SOURCE_REMOVE

    def _nudge_cli_exit(self, tab: TerminalTab) -> bool:
        """The CLI was asked to leave (via /exit or /bg) yet still owns the
        tab's terminal — typically parked on its session-list screen. Feed
        /exit to dismiss it so the pending close can finish. A no-op when
        the CLI already exited (then the text would only reach the shell,
        which the close is about to end anyway)."""
        if tab.get_root() is not None and tab.has_running_command():
            exit_text = tab.provider.graceful_exit()
            if exit_text:
                tab.feed_child_text(exit_text)
        return GLib.SOURCE_REMOVE

    def _poll_graceful(self, page: Adw.TabPage, tab: TerminalTab) -> bool:
        if page not in self._closing_pages:
            return GLib.SOURCE_REMOVE  # already closed
        if not tab.has_running_command():
            tab.feed_child_text("exit\r")  # close the shell → child-exited closes the tab
            return GLib.SOURCE_REMOVE
        self._closing_pages[page] += 1
        # /exit and /bg sometimes drop the CLI to its session-list screen
        # instead of exiting (seen with tabs attached to a detached session),
        # which would hang the close until the force-close below. A CLI still
        # owning the terminal this long after being asked to leave is the
        # tell: nudge it with /exit again. Safe for a merely-slow exit too —
        # the extra input queues behind the pending command and is discarded
        # when the CLI exits.
        if self._closing_pages[page] in (8, 24):  # ~2.4s / ~7.2s
            self._nudge_cli_exit(tab)
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

    def _update_active_row(self) -> None:
        """Tell the sidebar which session (or new-session placeholder) the
        selected tab is showing. A tab keeps its placeholder highlighted even
        after resolving, until the store discovers the session's real row."""
        page = self.tab_view.get_selected_page()
        row_id = None
        if page is not None:
            row_id = self._placeholder_pages.get(page) or self._session_id_of(page)
        self.sidebar.set_active_session(row_id)

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

    def _update_close_buttons(self, page: Adw.TabPage | None) -> None:
        """The header exit/background buttons act on the focused session, so
        they show only while a session tab is selected — and backgrounding
        only for providers that support detaching."""
        tab = page.get_child() if page is not None else None
        is_session = isinstance(tab, TerminalTab)
        self.exit_btn.set_visible(is_session)
        self.background_btn.set_visible(
            is_session and tab.provider.background_exit() is not None
        )

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

    def _clear_panel(self) -> None:
        tab = self._current_terminal_tab()
        if tab is not None:
            tab.clear_panel_history()

    def _on_selected_page_changed(self, view: Adw.TabView, _pspec) -> None:
        page = view.get_selected_page()
        self._update_active_row()
        self._update_close_buttons(page)
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
            notification.set_default_action_and_target(
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
        if isinstance(tab, TerminalTab) and page not in self._confirmed_closes:
            agent_busy = tab.has_running_command()
            panel_busy = tab.panel_has_running_command()
            if page in self._closing_pages and agent_busy:
                # A graceful /exit is already in flight; keep the tab open
                # until the shell drains.
                view.close_page_finish(page, False)
                return True
            if (agent_busy or panel_busy) and page not in self._close_ok:
                # The agent session and/or the panel shell is busy: ask before
                # ending the session or killing the panel job.
                view.close_page_finish(page, False)  # keep the tab while we ask
                if page not in self._close_asking:
                    self._ask_tab_close(page, tab)
                return True
            if agent_busy:  # confirmed: start a graceful /exit in the background
                self._graceful_close(page)
                view.close_page_finish(page, False)  # keep the tab until it exits cleanly
                return True
        self._confirmed_closes.discard(page)
        self._closing_pages.pop(page, None)
        self._close_asking.discard(page)
        self._close_ok.discard(page)
        self._bg_ok.discard(page)
        hide_session_id = self._hide_on_close.pop(page, None)
        if hide_session_id:
            self.store.set_hidden(hide_session_id, True)
        self._base_titles.pop(page, None)
        self._pending_resolved.pop(page, None)
        self._remove_placeholder(page)
        self._local_titles.discard(page)
        self._cancel_idle(page)
        if isinstance(tab, TerminalTab):
            tab.save_panel_history()  # before the widget (and its VTE buffer) is destroyed
            self._save_panel_state(tab)
        session_id = self._session_id_of(page)
        if session_id:
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
        cwd = resume_cwd(session)
        cwd = cwd if cwd and Path(cwd).is_dir() else str(Path.home())
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
        hidden = not self.state.is_hidden(session_id)
        page = self._pages.get(session_id) if hidden else None
        if page is not None:
            # Close the tab through the normal close-page flow, so a busy tab
            # still gets its confirmation dialog — and hide the session only
            # once the tab really closes: cancelling the dialog keeps it
            # visible.
            self._hide_on_close[page] = session_id
            self.tab_view.close_page(page)
            return
        self.store.set_hidden(session_id, hidden)

    def _on_hide_project(self, _action, param: GLib.Variant) -> None:
        name = param.get_string()
        self.store.set_project_hidden(name, not self.state.is_project_hidden(name))

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
            panelhistory.delete(session.session_id)
            self.state.set_panel_state(session.session_id, None)

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
            panelhistory.delete(session.session_id)
            self.state.set_panel_state(session.session_id, None)

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
            application_name="Collins",
            application_icon="com.episode6.Collins",
            developer_name="Máté Molnár",
            version=__version__,
            license_type=Gtk.License.GPL_3_0,
            comments=(
                _("Manage and resume your AI coding agent sessions.\n\n"
                "Unofficial community tool — not affiliated with or endorsed by Anthropic.")
            ),
            website="https://github.com/episode6/collins",
            issue_url="https://github.com/episode6/collins/issues",
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
        self.sidebar.refresh_usage_panel()

    def _apply_settings_to_tabs(self) -> None:
        for i in range(self.tab_view.get_n_pages()):
            tab = self.tab_view.get_nth_page(i).get_child()
            if isinstance(tab, TerminalTab):
                tab.apply_settings(self.state.settings)
