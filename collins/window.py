# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-01. Full change history: git log for this file.
"""Main window: composes the session sidebar with the tabbed terminal area."""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import NamedTuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from . import __version__, chats, dialogs, footerapps, openwith, panelhistory
from .bgstatus import (
    BLOCK_IN_FLIGHT,
    BLOCK_UNREGISTERED,
    BackgroundStatusPoller,
    background_blocker,
    match_background_fork,
)
from .caffeine import DURATION_KEYS, duration_label, duration_seconds, format_remaining
from .chatsessionview import ChatSessionTab
from .formatting import blast_radius_body
from .gitinfo import has_changes
from .i18n import _
from .licenses import legal_sections
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
from .store import SessionStore, emptied_projects
from .switcher import QuickSwitcher
from .taborder import tab_order
from .terminal import TerminalTab

log = logging.getLogger(__name__)

_GHOSTTY = shutil.which("ghostty")

# Quiet period before a background tab is considered "idle" / finished.
_IDLE_NOTIFY_MS = 4000
# Visual bell: how long .bell-flash stays on the header bar. Must outlast the
# CSS animation in app.py, which fades the flash out on its own.
_BELL_FLASH_MS = 450

# Window (and content header) title while the tab bar is showing the tab names.
_APP_TITLE = "Collins"

# Quit-time backgrounding, which runs one session at a time. How long to wait
# for a tab's session id to land before giving up and exiting it cleanly, how
# often to re-check while waiting, and how long to let one handoff hold the
# queue before moving on (the pending detach is on disk either way, so the next
# launch finishes any pairing this gives up on — see _replay_pending_detaches).
_BG_QUEUE_WAIT_MS = 5000
_BG_QUEUE_POLL_MS = 500
_BG_QUEUE_ITEM_TIMEOUT_S = 20

# The header background button's tooltip, per reason it's unavailable.
_BG_TOOLTIPS = {
    "": _("Background session and close tab"),
    BLOCK_UNREGISTERED: _("Waiting for this session to be registered — "
                          "backgrounding it now would leave the agent with no "
                          "way back to it"),
    BLOCK_IN_FLIGHT: _("Another session is still being handed to the "
                       "background — one at a time"),
}

class _KeepProjects(NamedTuple):
    """The "keep the emptied projects" check button and what it applies to."""

    check: Gtk.CheckButton
    projects: list[str]


# Tabs carry no status dot of their own: AdwTabView already marks a tab with
# unread output, and the dot it competed with said only "this tab is open",
# which the tab being there says already.


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


def _app_icon_name(window: Gtk.Window) -> str:
    """The debug build (and any COLLINS_APP_ID derived from its id) wears a
    recolored icon so the two apps read apart in a dock."""
    app = window.get_application()
    app_id = app.get_application_id() if app is not None else None
    if app_id and app_id.startswith("com.episode6.Collins.Debug"):
        return "com.episode6.Collins.Debug"
    return "com.episode6.Collins"


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, state: AppState, store: SessionStore, **kwargs) -> None:
        super().__init__(**kwargs)
        self.state = state
        self.store = store
        self.set_title(_APP_TITLE)
        self.set_icon_name(_app_icon_name(self))
        self._restore_window_geometry()
        self._pages: dict[str, Adw.TabPage] = {}  # session_id -> open tab
        self._confirmed_closes: set[Adw.TabPage] = set()
        self._closing_pages: dict[Adw.TabPage, int] = {}  # graceful close in progress -> attempts
        self._close_asking: set[Adw.TabPage] = set()  # busy-tab confirm dialog open
        self._close_ok: set[Adw.TabPage] = set()  # user okayed closing the busy tab
        self._bg_ok: set[Adw.TabPage] = set()  # user chose to background the agent instead
        # Archive requested for an open session: applied only once its tab
        # really closes (page -> session id).
        self._archive_on_close: dict[Adw.TabPage, str] = {}
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
        # Sessions assumed to be running detached because a /bg was fed, until
        # the agent list reports them: session id -> safety-timeout source
        # (see _mark_backgrounding).
        self._pending_bg: dict[str, int] = {}
        # The subset of those whose detach isn't confirmed yet — the window in
        # which the row must stay disabled, because neither resuming nor
        # attaching would do the right thing.
        self._detaching: set[str] = set()
        # Quit-time backgrounding runs one session at a time (see
        # _start_quit_backgrounding): tabs still waiting their turn, the timer
        # bounding the current one, and the progress notice over them.
        self._bg_queue: list[Adw.TabPage] = []
        self._bg_queue_timeout: int | None = None
        self._bg_queue_waited_ms = 0
        self._bg_queue_done = 0
        self._bg_queue_total = 0
        self._bg_queue_dialog: Adw.AlertDialog | None = None
        self._switcher: QuickSwitcher | None = None
        # Set while the app pushes shared Caffeine state into this window's
        # toggle, so the resulting "toggled" isn't mistaken for a click and
        # bounced back at the app (which would cancel the timer it just armed).
        self._syncing_caffeine = False
        # Set while _sort_tabs moves pages around, so the "page-reordered" it
        # provokes isn't mistaken for the user dragging a tab; the source id is
        # the snap-back that a real drag schedules.
        self._sorting_tabs = False
        self._sort_tabs_source: int | None = None

        self._install_actions()
        self._install_shortcuts()

        # --- content pane: header + tab bar + tab view ---
        self.tab_view = Adw.TabView()
        self.tab_view.connect("close-page", self._on_close_page)
        self.tab_view.connect("notify::selected-page", self._on_selected_page_changed)
        self.tab_view.connect("setup-menu", self._on_tab_setup_menu)
        # Window title follows the active tab while the tab bar is hidden, so
        # every page's renames have to be watched (see _sync_window_title).
        self._title_handlers: dict[Adw.TabPage, int] = {}
        self.tab_view.connect("page-attached", self._on_page_attached)
        self.tab_view.connect("page-detached", self._on_page_detached)
        # The tab bar's own drag-to-reorder would put the two panes out of step
        # (see _on_page_reordered).
        self.tab_view.connect("page-reordered", self._on_page_reordered)

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
        # Kept for the visual bell: a terminal's BEL flashes this bar.
        self._content_header = content_header
        self._bell_flash_source: int | None = None
        self.sidebar_toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", active=True)
        self.sidebar_toggle.set_tooltip_text(_("Toggle sidebar (F9)"))
        content_header.pack_start(self.sidebar_toggle)

        new_menu = Gio.Menu()
        for provider in available_providers():
            new_menu.append(
                _("New {name} session…").format(name=provider.name),
                f"win.new-session-provider::{provider.id}",
            )
        # The sidebar's Chats project: a session in a throwaway directory
        # ("chat" here ≠ the native streaming chat entries below).
        new_menu.append(_("New chat (scratch folder)"), "win.new-session-in-chats")
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

        # Caffeine Mode: first pack_end child, so it sits immediately left of
        # the window controls (minimize/maximize/close).
        self.caffeine_btn = Gtk.ToggleButton(
            active=bool(getattr(self.get_application(), "caffeine_enabled", False))
        )
        self.caffeine_btn.connect("toggled", self._on_caffeine_toggled)
        # A right-click asks how long to stay awake for; it never reaches the
        # button itself, which only activates on a primary click.
        caffeine_menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        caffeine_menu.connect("pressed", lambda *_: self._show_caffeine_menu())
        self.caffeine_btn.add_controller(caffeine_menu)
        content_header.pack_end(self.caffeine_btn)
        # Packed after the button, so it lands to its left: the shut-off timer
        # counting down, hidden whenever there isn't one running.
        self.caffeine_timer = Gtk.Label(visible=False)
        self.caffeine_timer.add_css_class("numeric")  # tabular figures: no jitter per tick
        self.caffeine_timer.add_css_class("dim-label")
        content_header.pack_end(self.caffeine_timer)
        self._sync_caffeine_visuals()

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
        # A row's PR actions need to know whether that session is sitting at an
        # empty prompt, and what its terminal's working tree looks like; only
        # the tab knows either, and only the window holds the tabs.
        self.sidebar.takes_prompt = self._session_takes_prompt
        self.sidebar.has_changes = self._session_has_changes
        self.sidebar.connect("open-session", self._on_sidebar_open)
        self.sidebar.connect("open-many", self._on_sidebar_open_many)
        self.sidebar.connect("trash-many", self._on_sidebar_trash_many)
        self.sidebar.connect("archive-many", self._on_sidebar_archive_many)
        self.sidebar.connect("open-placeholder", self._on_sidebar_open_placeholder)
        self.sidebar.connect("close-placeholder", self._on_sidebar_close_placeholder)
        self.sidebar.connect("rows-reordered", lambda *_: self._sort_tabs())
        self.store.connect("refreshed", self._on_store_refreshed)

        # Yellow "running detached" guide lines: keep the set of backgrounded session
        # ids fresh (see bgstatus.py for the trigger strategy).
        self._bg_status = BackgroundStatusPoller(on_change=self._on_background_ids_changed)
        self._bg_status.start(
            [d for p in available_providers() if (d := p.background_watch_dir()) is not None]
        )
        self._bg_status.set_polling(bool(self.state.get_setting("background_status_poll")))
        self.connect("destroy", lambda *_: self._bg_status.stop())
        # Any /bg the last run couldn't see through to the end gets one more go.
        self._replay_pending_detaches()

        self.sidebar.set_size_request(180, -1)  # minimum drag width
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
            # each one ask again. Agents still get their graceful exit
            # (or /bg, if the user chose to background them).
            pages = [self.tab_view.get_nth_page(i) for i in range(self.tab_view.get_n_pages())]
            for page in pages:
                self._close_ok.add(page)
            if background:
                self._start_quit_backgrounding(pages)
                return
            # _on_close_page reissues the window close once the last tab drains
            # (immediately, if every close completes synchronously).
            self._close_all_tabs()

        can_background = any(self._quit_backgroundable(self.tab_view.get_nth_page(i))
                             for i in range(self.tab_view.get_n_pages()))
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
            keys={"e": "confirm", "b": "extra", "c": "cancel"},
        )

    # -- quit-time backgrounding ---------------------------------------------

    def _quit_backgroundable(self, page: Adw.TabPage) -> bool:
        """Whether this tab is worth queueing for the quit-time handoff: a busy
        agent whose provider can detach and that has an id of its own to record
        the handoff against. Registration isn't checked here — the queue
        re-checks it on arrival, by which time a tab that was still resolving
        may well have landed."""
        tab = page.get_child()
        return (
            isinstance(tab, TerminalTab)
            and tab.has_running_command()
            and tab.provider.background_exit() is not None
            and not tab.fork
        )

    def _start_quit_backgrounding(self, pages: list[Adw.TabPage]) -> None:
        """Hand every busy agent to the background, one at a time.

        Serialized because the pairing is a guess: match_background_fork()
        fingerprints the conversation by its first message uuid and falls back
        to the working directory, and a fork that hasn't written its copy of
        the transcript yet has no readable uuid. Fire several /bg at once over
        one project and the fallback can pair each old session to the wrong
        new agent — or both to the same one. Waiting for each new id before
        feeding the next removes the ambiguity.

        Tabs that can't be backgrounded are closed straight away with the
        normal graceful exit."""
        self._bg_queue = [page for page in pages if self._quit_backgroundable(page)]
        self._bg_queue_total = len(self._bg_queue)
        self._bg_queue_done = 0
        self._bg_queue_waited_ms = 0
        queued = set(self._bg_queue)
        for page in pages:
            if page not in queued:
                self.tab_view.close_page(page)
        if not self._bg_queue:
            self._finish_quit_backgrounding()
            return
        log.info("bg-queue: backgrounding %s session(s) one at a time", self._bg_queue_total)
        self._bg_queue_dialog = dialogs.progress_dialog(
            self,
            _("Backgrounding sessions…"),
            self._bg_queue_body(),
            _("Quit Now"),
            self._abandon_bg_queue,
        )
        self._advance_bg_queue()

    def _bg_queue_body(self) -> str:
        return _("Handing each session to a background agent, one at a time, so "
                 "every one is paired with the agent it becomes. {done} of {total} "
                 "done.").format(done=self._bg_queue_done, total=self._bg_queue_total)

    def _cancel_bg_queue_timeout(self) -> None:
        if self._bg_queue_timeout is not None:
            GLib.source_remove(self._bg_queue_timeout)
            self._bg_queue_timeout = None

    def _advance_bg_queue(self) -> bool:
        """Send the next queued session to the background. Called again as each
        handoff settles (via _on_detach_settled) or when one outstays its
        budget."""
        self._cancel_bg_queue_timeout()
        while self._bg_queue:
            page = self._bg_queue[0]
            if not self._page_alive(page):
                self._bg_queue.pop(0)  # exited on its own while it waited
                continue
            blocker = self._background_blocker(page)
            if (
                blocker in (BLOCK_UNREGISTERED, BLOCK_IN_FLIGHT)
                and self._bg_queue_waited_ms < _BG_QUEUE_WAIT_MS
            ):
                # Its session id may be a resolver tick away; a short wait
                # beats exiting an agent the user asked to keep running.
                self._bg_queue_waited_ms += _BG_QUEUE_POLL_MS
                self._bg_queue_timeout = GLib.timeout_add(
                    _BG_QUEUE_POLL_MS, self._advance_bg_queue
                )
                return GLib.SOURCE_REMOVE
            self._bg_queue.pop(0)
            self._bg_queue_waited_ms = 0
            self._bg_queue_done += 1
            self._update_bg_queue_dialog()
            if blocker:
                log.warning("bg-queue: %s never registered (%s); exiting it cleanly",
                            self._session_id_of(page) or "unresolved tab", blocker)
                self.tab_view.close_page(page)
                continue  # nothing to wait for; straight on to the next
            self._bg_ok.add(page)
            self._bg_queue_timeout = GLib.timeout_add_seconds(
                _BG_QUEUE_ITEM_TIMEOUT_S, self._bg_queue_item_timed_out
            )
            self.tab_view.close_page(page)  # _graceful_close feeds the /bg
            return GLib.SOURCE_REMOVE
        self._finish_quit_backgrounding()
        return GLib.SOURCE_REMOVE

    def _bg_queue_item_timed_out(self) -> bool:
        """This handoff is taking too long to hold the quit up. Move on: the
        pending detach is already on disk, so the next launch finishes the
        pairing (see _replay_pending_detaches)."""
        self._bg_queue_timeout = None
        log.info("bg-queue: handoff still unconfirmed after %ss; leaving it to "
                 "the next launch", _BG_QUEUE_ITEM_TIMEOUT_S)
        self._advance_bg_queue()
        return GLib.SOURCE_REMOVE

    def _update_bg_queue_dialog(self) -> None:
        if self._bg_queue_dialog is not None:
            self._bg_queue_dialog.set_body(self._bg_queue_body())

    def _close_bg_queue_dialog(self) -> None:
        # Cleared first: closing emits "response", which lands in
        # _abandon_bg_queue, and this is what tells it the queue is already done.
        dialog, self._bg_queue_dialog = self._bg_queue_dialog, None
        if dialog is not None:
            dialog.close()

    def _abandon_bg_queue(self) -> None:
        """"Quit Now": stop waiting on the remaining handoffs. Whatever was
        already fed keeps its on-disk pending detach, so the next launch can
        still pair it; the rest get the normal graceful exit."""
        if self._bg_queue_dialog is None:
            return  # the queue finished on its own and closed the dialog
        self._bg_queue_dialog = None
        self._cancel_bg_queue_timeout()
        log.info("bg-queue: abandoned with %s session(s) left; exiting them", len(self._bg_queue))
        self._bg_queue.clear()
        self._close_all_tabs()
        if self.tab_view.get_n_pages() == 0:  # nothing left to reissue the close
            GLib.idle_add(self.close)

    def _finish_quit_backgrounding(self) -> None:
        self._cancel_bg_queue_timeout()
        self._close_bg_queue_dialog()
        # Tabs still draining reissue the window close as the last one goes;
        # with none left there is nothing to wait for.
        if self.tab_view.get_n_pages() == 0:
            GLib.idle_add(self.close)

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
            "refresh": lambda *_: self.store.refresh(force_rebuild=True),
            "new-session": lambda *_: self._new_session(),
            "new-session-in-chats": lambda *_: self._new_session_in_chats(),
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
            "trash-archived": lambda *_: self._trash_archived(),
            "archive-current-session": lambda *_: self._archive_current_session(),
        }
        for name, callback in plain.items():
            action = Gio.SimpleAction(name=name)
            action.connect("activate", callback)
            self.add_action(action)

        # Greyed out until something is actually archived; kept in sync on
        # every store refresh (archiving/restoring goes through one).
        self._trash_archived_action = self.lookup_action("trash-archived")
        self._sync_trash_archived_action()

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
            "stop-session": lambda _a, p: self._close_session_tab(p.get_string()),
            "background-session": lambda _a, p: self._close_session_tab(
                p.get_string(), background=True
            ),
            "archive-session": self._on_archive_session,
            "archive-project": self._on_archive_project,
            "forget-project": lambda _a, p: self.store.forget_project(p.get_string()),
            "trash-session": self._on_trash_session,
            "open-folder": self._on_open_folder,
            "open-folder-terminal": self._on_open_folder_terminal,
        }
        for name, callback in per_session.items():
            action = Gio.SimpleAction(name=name, parameter_type=GLib.VariantType("s"))
            action.connect("activate", callback)
            self.add_action(action)

        # The two-part targets: (desktop-file ID, folder), and the session plus
        # the prompt a row's PR menu wants typed into it.
        open_folder_app = Gio.SimpleAction(
            name="open-folder-app", parameter_type=GLib.VariantType("(ss)")
        )
        open_folder_app.connect("activate", self._on_open_folder_app)
        self.add_action(open_folder_app)

        # The Caffeine button's context menu: the picked duration's key.
        caffeine_timer = Gio.SimpleAction(
            name="caffeine-timer", parameter_type=GLib.VariantType("s")
        )
        caffeine_timer.connect("activate", self._on_caffeine_timer)
        self.add_action(caffeine_timer)

        send_prompt = Gio.SimpleAction(
            name="send-prompt", parameter_type=GLib.VariantType("(ss)")
        )
        send_prompt.connect("activate", lambda _a, p: self._send_prompt(*p.unpack()))
        self.add_action(send_prompt)

        show_archived = Gio.SimpleAction.new_stateful(
            "show-archived", None, GLib.Variant.new_boolean(False)
        )
        show_archived.connect("change-state", self._on_show_archived)
        self.add_action(show_archived)

        select_sessions = Gio.SimpleAction.new_stateful(
            "select-sessions", None, GLib.Variant.new_boolean(False)
        )
        select_sessions.connect("change-state", self._on_select_sessions)
        self.add_action(select_sessions)

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
            ("<Control><Shift>a", "win.archive-current-session"),
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
        keep = self._keep_projects_check([item.session_id for item in items])

        def do_trash() -> None:
            self._apply_keep_projects(keep)
            errors = []
            for item in items:
                error = self.store.trash(item.session_id)
                if error:
                    errors.append(f"{item.display_name}: {error}")
                    continue
                self._forget_transcript(item.session_id)
            if errors:
                dialogs.error_dialog(self, _("Some transcripts could not be trashed"), "\n".join(errors))

        dialogs.confirm_dialog(
            self,
            _("Move {n} transcript(s) to trash?").format(n=len(items)),
            _("The files are moved to the trash and can be restored."),
            _("Move to Trash"),
            do_trash,
            extra_child=keep.check if keep else None,
        )

    def _on_sidebar_archive_many(self, _sidebar, items: list[SessionItem]) -> None:
        self.store.archive_many([item.session_id for item in items])
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
        # A live /bg fork with no row of its own, which this row stands in for:
        # the tab binds to it, so the CLI attaches to the running agent.
        attach_id: str | None = None
        if not fork:
            if self._detaching_now(session.session_id):
                # Mid-/bg handoff: attach isn't possible until the CLI lists
                # the agent, so opening now would resume a new foreground turn
                # over the transcript. The sidebar row is disabled; guard the
                # other entry paths (switcher, session restore) too.
                return
            # A backgrounded session may have continued under a new id (older
            # CLIs' /bg forked the conversation): open the live end of the
            # chain instead of the stale original. Forks stay on the id the
            # user picked.
            forward = self.store.forward_state(session)
            target = self.state.resolve_forward(session.session_id)
            if forward == "moved":
                session = self.store.get_session(target)
            elif forward == "syncing":
                # The fork exists but the store hasn't scanned it yet.
                # Its sidebar row is disabled during this window; guard
                # the other entry paths (switcher, session restore) too
                # rather than open the stale original.
                return
            elif target != session.session_id and target in self._bg_status.background_ids:
                # The fork is running but has only a metadata stub for a
                # transcript, so it has no row of its own and may never get
                # one (see SessionStore.rows_representing). Bind the tab to
                # the fork's id — resume_command attaches to the live agent —
                # while the sidebar keeps showing the row that was clicked.
                attach_id = target
            # Otherwise the forward is stale (fork transcript gone, or a dead
            # /bg stub with no live agent): open the original normally.
            page = self._page_for(session.session_id)
            if page is not None:
                self.tab_view.set_selected_page(page)
                return

        cwd = resume_cwd(session)
        # A chat's throwaway directory may have been swept or trashed since;
        # recreate it rather than letting the terminal fall back to $HOME.
        chats.ensure_chat_dir(cwd)
        # The tab is bound to the id the CLI actually runs, which for an
        # attached fork is the fork's — including its (still stubby) transcript.
        bound_id = attach_id or session.session_id
        jsonl_path = (
            str(Path(session.jsonl_path).parent / f"{attach_id}.jsonl")
            if attach_id
            else session.jsonl_path
        )
        tab = TerminalTab(
            cwd=cwd,
            session_id=bound_id,
            fork=fork,
            settings=self.state.settings,
            provider=provider,
            jsonl_path=jsonl_path,
        )
        title = f"{self.store.display_name(session)} (fork)" if fork else self._tab_title(session)
        project = "Chats" if chats.is_chat_cwd(session.cwd) else session.project_name
        page = self._add_tab(tab, title, f"{project} — {bound_id}")
        if not fork:
            self._pages[bound_id] = page
            self._sync_status(bound_id)
            saved_panel = self.state.get_panel_state(bound_id)
            if saved_panel:  # reopen the panel the way this session left it
                tab.restore_panel_state(saved_panel)
            # The PRs this session opened, back on the footer row before the
            # first transcript poll (and including any that only a lookup knew).
            tab.restore_prs(self.state.get_session_prs(bound_id))

    def _tab_title(self, session: Session) -> str:
        """Tab title with the session's saved emoji prefix (tabs only)."""
        name = self.store.display_name(session)
        emoji = self.state.get_emoji(session.session_id)
        return f"{emoji} {name}" if emoji else name

    def _default_provider(self):
        """First installed agent (Claude when present), used by the quick button."""
        providers = available_providers()
        return providers[0] if providers else get_provider("claude")

    def _visible_project_dir(self) -> str | None:
        """Project directory of the session in the visible tab: the directory
        the session was started in, not its current working directory (which
        may have moved into a git worktree). None when the visible tab has no
        bound session or the directory no longer exists."""
        session_id = self._active_session_id()
        if session_id is None:
            return None
        session = self.store.get_session(session_id)
        if session is not None and session.cwd and Path(session.cwd).is_dir():
            return session.cwd
        return None

    def _new_session(self, provider=None) -> None:
        """Start in the visible session's project directory, else ask."""
        provider = provider or self._default_provider()
        default = self._visible_project_dir()
        if default and chats.is_chat_cwd(default):
            # The visible tab is a chat: its directory is throwaway, so a new
            # session gets a fresh one instead of silently sharing it.
            self._new_session_in_chats(provider)
        elif default:
            self._start_new_session(default, provider)
        else:
            self._choose_new_session_folder(provider)

    def _new_session_in_chats(self, provider=None) -> None:
        """A session in the virtual Chats project: launched in a fresh
        throwaway directory instead of a real project folder."""
        try:
            cwd = chats.create_chat_dir()
        except OSError as err:
            dialogs.error_dialog(self, _("Could not create chat directory"), str(err))
            return
        # Skip the CLI's folder-trust prompt: we created this directory
        # ourselves two lines ago, empty.
        chats.trust_chat_dir(cwd)
        # Unknown groups start collapsed; the first chat must not vanish the
        # moment its placeholder resolves into a real row. (Key matches the
        # sidebar's _group_state_key for CHATS_GROUP.)
        self.state.set_group_expanded("chats:", True)
        self._start_new_session(cwd, provider)

    def _choose_new_session_folder(self, provider=None) -> None:
        self._new_session_provider = provider or self._default_provider()
        dialog = Gtk.FileDialog(title=_("Choose project directory"))
        default = self._visible_project_dir()
        if default:
            dialog.set_initial_folder(Gio.File.new_for_path(default))
        dialog.select_folder(self, None, self._on_new_session_folder)

    def _on_new_session_folder(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return  # cancelled
        cwd = folder.get_path()
        self._start_new_session(cwd, getattr(self, "_new_session_provider", None))

    def _start_new_session(self, cwd: str, provider=None, options=None) -> None:
        provider = provider or self._default_provider()
        tab = TerminalTab(
            cwd=cwd, session_id=None, settings=self.state.settings, provider=provider,
            options=options,
        )
        page = self._add_tab(
            tab,
            _("New chat") if chats.is_chat_cwd(cwd) else GLib.path_get_basename(cwd),
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
        default = self._visible_project_dir()
        if default:
            dialog.set_initial_folder(Gio.File.new_for_path(default))
        dialog.select_folder(self, None, self._on_advanced_folder)

    def _on_advanced_folder(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        cwd = folder.get_path()
        provider = getattr(self, "_adv_provider", None) or self._default_provider()
        dialogs.new_session_options_dialog(
            self, provider, lambda opts: self._start_new_session(cwd, provider, opts)
        )

    def _continue_session(self, provider) -> None:
        self._cont_provider = provider or self._default_provider()
        dialog = Gtk.FileDialog(title=_("Choose project directory"))
        default = self._visible_project_dir()
        if default:
            dialog.set_initial_folder(Gio.File.new_for_path(default))
        dialog.select_folder(self, None, self._on_continue_folder)

    def _on_continue_folder(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        cwd = folder.get_path()
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
        tab.connect("bell", self._on_bell)
        tab.connect("prs-changed", self._on_tab_prs_changed)
        tab.set_panel_size_lookup(lambda mode: int(self.state.get_setting(f"panel_size_{mode}") or 0))
        tab.terminal.connect("contents-changed", self._on_terminal_output, page)
        self.tab_view.set_selected_page(page)
        self.content_stack.set_visible_child_name("tabs")
        self._sort_tabs()  # appended at the end; slot it in beside its row
        GLib.idle_add(tab.grab_terminal_focus)
        return page

    # -- tab order mirrors the sidebar ---------------------------------------

    def _tab_row_id(self, page: Adw.TabPage, rows_by_session: dict[str, str]) -> str | None:
        """The sidebar row a tab belongs under, or None for a tab no row stands
        for — a chat, a replay, or a session archived out of the list while its
        tab stayed open."""
        placeholder_id = self._placeholder_pages.get(page)
        if placeholder_id is not None:
            return placeholder_id
        tab = page.get_child()
        if not isinstance(tab, TerminalTab) or not tab.session_id:
            return None
        return rows_by_session.get(tab.session_id)

    def _sort_tabs(self) -> None:
        """Put the tab bar back in the sidebar's order, left to right.

        Every path that opens a tab appends it, and the sidebar reshuffles
        itself whenever a project moves or a session appears, so this runs
        after both. Tabs are moved into place one at a time from the left: each
        page already sitting at its target index stays put, so a bar that is
        already in order does no work at all.
        """
        if self._sorting_tabs:
            return
        pages = [self.tab_view.get_nth_page(i) for i in range(self.tab_view.get_n_pages())]
        if len(pages) < 2:
            return
        order = self.sidebar.row_order()
        # A tab is bound to the id the CLI runs, which after a /bg is a fork's;
        # the row it belongs under is the one further up that chain. Topmost
        # row wins if two claim the same id.
        rows_by_session: dict[str, str] = {}
        for row_id in order:
            for session_id in self.state.forward_chain(row_id):
                rows_by_session.setdefault(session_id, row_id)
        row_ids = [self._tab_row_id(page, rows_by_session) for page in pages]
        wanted = tab_order(row_ids, order)
        if wanted == list(range(len(pages))):
            return
        self._sorting_tabs = True
        try:
            for position, index in enumerate(wanted):
                self.tab_view.reorder_page(pages[index], position)
        finally:
            self._sorting_tabs = False

    def _on_page_reordered(self, _view, _page: Adw.TabPage, _position: int) -> None:
        """A tab dragged to a new spot snaps back: the bar's order is the
        sidebar's, and the sidebar is where it can be changed (drag a project
        header there instead). Deferred to an idle so the snap lands after the
        tab bar has finished settling the drag."""
        if self._sorting_tabs or self._sort_tabs_source is not None:
            return
        self._sort_tabs_source = GLib.idle_add(self._snap_tabs_back)

    def _snap_tabs_back(self) -> bool:
        self._sort_tabs_source = None
        self._sort_tabs()
        return GLib.SOURCE_REMOVE

    def _on_bell(self, _tab: TerminalTab) -> None:
        """Visual bell: flash the header bar once (the audible bell is VTE's
        own). A bell arriving mid-flash is folded into it — restarting the
        CSS animation would need a frame without the class, and one flash
        already tells the story."""
        if self._bell_flash_source is not None:
            return

        def clear() -> bool:
            self._bell_flash_source = None
            self._content_header.remove_css_class("bell-flash")
            return GLib.SOURCE_REMOVE

        self._content_header.add_css_class("bell-flash")
        self._bell_flash_source = GLib.timeout_add(_BELL_FLASH_MS, clear)

    def _on_session_resolved(self, tab: TerminalTab, session_id: str, page: Adw.TabPage) -> None:
        """A fresh tab (new / continue) discovered its session id: bind the tab
        to the session so the sidebar highlight, open-dedup, rename and status sync
        work exactly like a tab opened from the sidebar."""
        if self._pages.get(session_id) not in (None, page):
            return  # another tab already owns this session
        self._pages[session_id] = page
        # A `--continue` tab lands on a session that may already have PRs
        # saved; a brand-new one has none, and this is a no-op for it.
        tab.restore_prs(self.state.get_session_prs(session_id))
        self._pending_resolved[page] = (session_id, page.get_title())
        self._sync_status(session_id)
        self._update_active_row()  # the resolved tab may be the selected one
        self._apply_resolved_sessions()
        # Half of registering: the id is known. The gate stays shut until the
        # store gives it a row, which _on_store_refreshed picks up.
        self._refresh_background_affordances()

    def _on_tab_prs_changed(self, tab: TerminalTab, records: object) -> None:
        """A tab's PR row changed: save it against that tab's session.

        A fork writes nothing (its tab shares the original's session id and
        would overwrite its list), and neither does a tab whose session isn't
        resolved yet — it has nowhere to write, and its list is re-derived from
        the transcript the moment it is.

        The sidebar reads the same saved list for its own PR button, so it is
        told the moment one is written: a session's first PR is what puts that
        button on its row.
        """
        if tab.fork or not tab.session_id:
            return
        self.state.set_session_prs(tab.session_id, list(records or []))
        self.sidebar.sync_session_prs(tab.session_id)

    def _on_panel_size_changed(self, _tab: TerminalTab, mode: str, size: int) -> None:
        """A divider was dragged: remember the size app-wide, so every panel
        opened from now on (in any tab) defaults to it."""
        key = f"panel_size_{mode}"
        if self.state.get_setting(key) != size:
            self.state.set_setting(key, size)

    def _on_store_refreshed(self, _store, _order_changed: bool) -> None:
        self._sync_trash_archived_action()
        if self._restore_session_id is not None:
            self._apply_restore_session()
        if self._pending_resolved:
            self._apply_resolved_sessions()
        self._sync_transcript_paths()
        self._refresh_tab_titles()
        # Freshly discovered rows start with no status; re-assert yellow lines
        # for sessions known to be running detached (no-op when unchanged).
        for session_id in self._bg_status.background_ids:
            self._sync_status(session_id)
        # A pending /bg that forked is assumed detached on the old row until
        # something more solid takes over; once the store discovers the fork,
        # the old row is hidden ("moved") and the fork's own row carries the
        # line, so the assumption can go.
        for session_id in list(self._pending_bg):
            target = self.state.resolve_forward(session_id)
            if target != session_id and self.store.get_session(target) is not None:
                self._clear_backgrounding(session_id, "fork discovered; row handed off")
        # Rows just appeared or went away, and a row is what a handoff needs.
        self._refresh_background_affordances()

    def _sync_transcript_paths(self) -> None:
        """Re-aim tabs whose transcript moved out from under them.

        The CLI keys a session's transcript by its working directory, so a
        session that enters a git worktree has its file re-homed under a new
        project directory. The tab resolved its path once, when the session
        started, and would otherwise tail a path that no longer exists for the
        rest of the run — no PR chips, no prompt cards, no status.

        Only a tab whose own path has gone missing is touched, and only when
        the store has found that same session somewhere that exists, so a tab
        deliberately pointed at another file (an attached fork tails the
        fork's transcript) is never dragged off it.
        """
        for session_id, page in list(self._pages.items()):
            tab = page.get_child()
            if not isinstance(tab, TerminalTab):
                continue
            current = tab.transcript_path
            if not current or Path(current).exists():
                continue
            session = self.store.get_session(session_id)
            if session is None:
                continue
            moved = str(session.jsonl_path)
            if moved == current or not Path(moved).exists():
                continue
            log.info("transcript moved: %s -> %s", current, moved)
            tab.relocate_transcript(moved)

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
        default = self._visible_project_dir()
        if default:
            dialog.set_initial_folder(Gio.File.new_for_path(default))
        dialog.select_folder(self, None, self._on_new_chat_folder)

    def _on_new_chat_folder(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return  # cancelled
        cwd = folder.get_path()
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
        self.tab_view.set_selected_page(page)
        self.content_stack.set_visible_child_name("tabs")

    def _on_tab_bar_toggled(self, button: Gtk.ToggleButton) -> None:
        """Purely visual: the tabs (and their sessions) keep running underneath."""
        show = button.get_active()
        self.tab_bar.set_visible(show)
        self.state.set_setting("show_tab_bar", show)
        self._sync_window_title()

    # -- window title --------------------------------------------------------

    def _sync_window_title(self) -> None:
        """With the tab bar hidden nothing on screen names the session you are
        looking at, so the window title (content header, alt-tab, dock) carries
        the active tab's title instead of the bare app name. Showing the tab
        bar hands that job back to the tabs and restores "Collins"."""
        page = self.tab_view.get_selected_page()
        title = page.get_title().strip() if page is not None else ""
        if self.tab_bar.get_visible() or not title:
            self.set_title(_APP_TITLE)
        else:
            self.set_title(title)

    def _on_page_attached(self, _view: Adw.TabView, page: Adw.TabPage, _pos: int) -> None:
        # Titles are set from a dozen places (session rename, emoji, store
        # refresh…); watching the page itself catches all of them.
        self._title_handlers[page] = page.connect("notify::title", self._on_page_title_changed)

    def _on_page_detached(self, _view: Adw.TabView, page: Adw.TabPage, _pos: int) -> None:
        handler = self._title_handlers.pop(page, None)
        if handler is not None:
            page.disconnect(handler)

    def _on_page_title_changed(self, page: Adw.TabPage, _pspec) -> None:
        if page is self.tab_view.get_selected_page():
            self._sync_window_title()

    def _on_caffeine_toggled(self, button: Gtk.ToggleButton) -> None:
        if self._syncing_caffeine:  # the app pushing its state in, not a click
            return
        app = self.get_application()
        if hasattr(app, "set_caffeine_enabled"):
            # A plain click is deliberately untimed: the durations are what the
            # button's context menu is for.
            app.set_caffeine_enabled(button.get_active())
        self._sync_caffeine_visuals()

    def _show_caffeine_menu(self) -> None:
        """Right-click on the Caffeine button: how long to stay awake for."""
        menu = Gio.Menu()
        for key in DURATION_KEYS:
            menu.append(duration_label(key), f"win.caffeine-timer::{key}")
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(self.caffeine_btn)
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()

    def _on_caffeine_timer(self, _action, param: GLib.Variant) -> None:
        """A duration was picked: turn Caffeine Mode on for that long, replacing
        whatever timer was running before."""
        app = self.get_application()
        if hasattr(app, "set_caffeine_enabled"):
            app.set_caffeine_enabled(True, seconds=duration_seconds(param.get_string()))

    def sync_caffeine_toggle(self) -> None:
        """Called by the app so every window's button tracks the shared state."""
        app = self.get_application()
        self._syncing_caffeine = True
        try:
            self.caffeine_btn.set_active(bool(getattr(app, "caffeine_enabled", False)))
        finally:
            self._syncing_caffeine = False
        self._sync_caffeine_visuals()

    def _sync_caffeine_visuals(self) -> None:
        on = self.caffeine_btn.get_active()
        remaining = getattr(self.get_application(), "caffeine_remaining", None) if on else None
        self.caffeine_btn.set_icon_name(
            "caffeine-cup-full-symbolic" if on else "caffeine-cup-empty-symbolic"
        )
        if remaining is None:
            tooltip = (
                _("Caffeine Mode is on — the computer will stay awake")
                if on
                else _("Caffeine Mode: keep the computer awake and the screen on")
            )
        else:
            tooltip = _("Caffeine Mode turns off in {time}").format(
                time=format_remaining(remaining)
            )
        self.caffeine_btn.set_tooltip_text(tooltip)
        self.caffeine_timer.set_visible(remaining is not None)
        if remaining is not None:
            self.caffeine_timer.set_label(format_remaining(remaining))
            self.caffeine_timer.set_tooltip_text(tooltip)

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
        if page is not None and not self._background_blocker(page):
            self._close_ok.add(page)
            self._bg_ok.add(page)
            self.tab_view.close_page(page)

    def _close_session_tab(self, session_id: str, background: bool = False) -> None:
        """Sidebar row buttons: exit (or detach) the session's own tab, whether
        or not it is the focused one — same no-dialog behavior as the header
        buttons. A session with no open tab has nothing to close."""
        page = self._page_for(session_id)
        if page is None:
            return
        if background and self._background_blocker(page):
            return  # the row's button is disabled; ignore the action either way
        self._close_ok.add(page)
        if background:
            self._bg_ok.add(page)
        self.tab_view.close_page(page)

    def _session_takes_prompt(self, session_id: str) -> bool:
        """Whether a prompt sent to this session would land in an empty input.

        What the sidebar asks before offering a row's PR prompt actions (see
        SessionSidebar.takes_prompt): the tab that would receive one is the
        window's, and only it can read what its agent is waiting at.
        """
        tab = self._session_tab(session_id)
        return tab is not None and tab.takes_prompt()

    def _session_has_changes(self, session_id: str) -> bool:
        """Whether this session's terminal is sitting over uncommitted work.

        The other half of what a row's PR menu asks (see
        SessionSidebar.has_changes) — and the cwd it is asked about is the
        tab's live one, since an agent that moved into a worktree took the
        changes worth opening a pull request for with it.
        """
        tab = self._session_tab(session_id)
        return tab is not None and has_changes(tab.current_agent_cwd())

    def _send_prompt(self, session_id: str, prompt: str) -> None:
        """A sidebar PR menu's prompt action: type it into the session's own
        tab, and bring that tab to the front so the answer is where the user is
        looking.

        Both checks again rather than assuming: rows are built long before they
        are clicked, and the tab may have been closed — or its prompt started —
        since the menu was opened."""
        tab = self._session_tab(session_id)
        if tab is None or not tab.takes_prompt():
            return
        self.tab_view.set_selected_page(self._page_for(session_id))
        tab.inject_prompt(prompt)

    def _session_tab(self, session_id: str) -> TerminalTab | None:
        """The terminal tab a session is open in, if it is open in one."""
        page = self._page_for(session_id)
        tab = page.get_child() if page is not None else None
        return tab if isinstance(tab, TerminalTab) else None

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

        blocker = self._background_blocker(page)
        can_background = agent_busy and not blocker
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
        elif agent_busy and blocker == BLOCK_UNREGISTERED:
            body += " " + _("Backgrounding isn't available yet: this session "
                            "hasn't been registered, so a detached agent would "
                            "have no way back to it.")
        elif agent_busy and blocker == BLOCK_IN_FLIGHT:
            body += " " + _("Backgrounding isn't available right now: another "
                            "session is still being handed to the background.")
        # A panel-only-busy tab has no agent session to exit — say "Close Tab".
        confirm_label = _("Exit Session") if agent_busy else _("Close Tab")
        def dismiss() -> None:
            self._close_asking.discard(page)
            self._archive_on_close.pop(page, None)  # cancelled: keep the session visible

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
            keys={"e": "confirm", "b": "extra", "c": "cancel"},
        )

    def _graceful_close(self, page: Adw.TabPage) -> None:
        """Ask the agent to exit cleanly (e.g. Claude's Ctrl+C Ctrl+C) — or to detach
        (e.g. /bg) if the user chose to background it — then close once the
        shell returns. Falls back to a force-close after a timeout. Agents with
        no clean-exit command are force-closed directly."""
        tab = page.get_child()
        if not isinstance(tab, TerminalTab):
            self._close_confirmed(page)
            return
        exit_text = None
        if page in self._bg_ok:
            # Last line of defence for the gate: every affordance that sets
            # _bg_ok is disabled while a handoff would be untrackable, but a
            # /bg fed anyway detaches the agent for real and strands it — the
            # fork's transcript is usually a stub the scan skips, so it would
            # get no row and nothing would ever pair it. Exiting cleanly
            # instead at least leaves a resumable transcript behind.
            blocker = self._background_blocker(page)
            if blocker:
                log.warning(
                    "bg: refusing to detach %s (%s); exiting it cleanly instead",
                    tab.session_id or "unresolved tab", blocker,
                )
            else:
                exit_text = tab.provider.background_exit()
        if exit_text:
            self._watch_background_fork(tab)
            # Show the yellow guide line as soon as the tab closes, and keep the
            # row unopenable until the detach is confirmed (or times out). The
            # gate guarantees a session id here.
            self._mark_backgrounding(tab.session_id)
        else:
            exit_text = tab.provider.graceful_exit()
        if not exit_text:
            self._close_confirmed(page)
            return
        self._closing_pages[page] = 0
        # Raw keystrokes, exactly as the provider spells them: a control byte
        # for Claude's Ctrl+C Ctrl+C, and for a typed command like /bg the
        # Enter that submits it — carriage return in a raw-mode TUI, not
        # newline.
        tab.feed_child_text(exit_text)
        GLib.timeout_add(300, self._poll_graceful, page, tab)

    def _watch_background_fork(self, tab: TerminalTab) -> None:
        """Confirm the session is running detached, and record its successor
        id if the CLI forked one. Despite the docs suggesting in-place
        detaches, current Claude CLIs have been observed (2026-07) forking on
        /bg: the background agent runs under a *new* session id whose
        transcript is a copy of the conversation, leaving the original behind
        as a stale duplicate. Watch the agent list (off the main thread) for
        either outcome. The tab's own session id appearing means an in-place
        detach: nothing to record. A fresh id with a matching conversation
        means a fork: record old -> new, so the stale row is hidden, the
        user's name/emoji/favorite carry over, and opening the old session
        redirects to the live one.

        Either way the CLI may keep the terminal after /bg (parked on its
        agent-list screen), which would hang the pending close until the
        force-close safety net — so once the session is confirmed running
        detached, the CLI gets an exit nudge if it still holds the terminal."""
        old_id = tab.session_id
        provider = tab.provider
        if not old_id or tab.fork:
            return
        cwd = tab.current_agent_cwd()
        old_session = self.store.get_session(old_id)
        old_uuid = (
            first_message_uuid(old_session.jsonl_path) if old_session is not None else None
        )
        # Remembered on disk so a restart mid-handoff can finish the pairing
        # instead of stranding the agent (see _replay_pending_detaches).
        self.state.set_pending_detach(
            old_id, provider=provider.id, cwd=cwd or "", uuid=old_uuid or ""
        )
        known = {a.session_id for a in provider.background_agents()}

        def work() -> None:
            for attempt in range(30):  # the agent entry appears within seconds of /bg
                found = match_background_fork(provider, old_id, cwd, old_uuid, known)
                log.debug("bg-watch: attempt %s for %s: %r", attempt, old_id, found)
                if found is not None:
                    GLib.idle_add(self._on_backgrounded, tab, old_id, found)
                    return
                time.sleep(1)
            log.info("bg-watch: %s never appeared in the agent list; giving up", old_id)
            # Never confirmed: the detach presumably failed — drop the
            # pre-emptive yellow line and re-enable the row.
            GLib.idle_add(self._clear_backgrounding, old_id, "confirmation watch gave up")

        threading.Thread(target=work, daemon=True).start()

    # -- pending /bg detaches across restarts --------------------------------

    def _replay_pending_detaches(self) -> None:
        """Finish the /bg handoffs that were still in flight when the app last
        closed. The fork watcher lives only as long as the process, so a quit
        (or crash) during the seconds between feeding /bg and the CLI listing
        the agent used to lose the old -> new pairing for good: the row went on
        pointing at the frozen pre-/bg transcript, and the live agent had no row
        to reach it from at all. Worse, resuming that row and backgrounding it
        again spawned a second agent, so they piled up unreachable.

        The evidence needed for the pairing is persisted with the pending
        detach, so replay it once at startup against the current agent list. A
        record that matches nothing is dropped — its agent is gone."""
        pending = self.state.get_pending_detaches()
        if not pending:
            return
        log.info("bg-replay: %s pending detach(es) to re-check", len(pending))
        # An agent some other row already forwards to is spoken for; it can't
        # be the one this record is looking for. That is all the standing in
        # for `known` there is this long after the /bg, so pairing also has to
        # be strict: see match_background_fork's unique_cwd.
        claimed = set(self.state.session_forwards.values())

        def work() -> None:
            for old_id, info in pending.items():
                provider = get_provider(info.get("provider") or "claude")
                found = match_background_fork(
                    provider,
                    old_id,
                    info.get("cwd") or "",
                    info.get("uuid") or "",
                    claimed,
                    unique_cwd=True,
                )
                GLib.idle_add(self._on_detach_replayed, old_id, found)

        threading.Thread(target=work, daemon=True).start()

    def _on_detach_replayed(self, old_id: str, found: str | None) -> bool:
        """A pending detach re-checked at startup: `found` is the fork's id, ""
        for an in-place detach, or None when nothing in the agent list matches."""
        if found is None:
            log.info("bg-replay: %s matches no running agent; dropping the record", old_id)
        else:
            log.info("bg-replay: %s paired with %s", old_id, found or "itself (in place)")
            if found:
                self.store.record_forward(old_id, found)
            self._sync_status(old_id)  # yellow line, through the forward
        self.state.clear_pending_detach(old_id)
        return GLib.SOURCE_REMOVE

    def _on_backgrounded(self, tab: TerminalTab, old_id: str, new_id: str) -> bool:
        """The tab's session is confirmed running detached (new_id is its
        fork's session id, or "" when it detached in place)."""
        log.info("bg-watch: %s confirmed detached (fork id: %s)", old_id, new_id or "none")
        if new_id:
            self.store.record_forward(old_id, new_id)
        self.state.clear_pending_detach(old_id)  # paired; nothing left to replay
        # Confirmed detached: the row is clickable again — it opens the live
        # agent, not the stale original — while _is_detached keeps it yellow
        # through the forward for as long as the agent runs.
        self._confirm_backgrounding(old_id)
        # The agent list already shows the detached session: refresh now so
        # the yellow line lands promptly even if the jobs-dir monitor misses it.
        self._bg_status.refresh()
        # Give the CLI a moment to exit on its own before nudging it off any
        # screen it parked on (see _watch_background_fork).
        GLib.timeout_add(700, self._nudge_cli_exit, tab)
        return GLib.SOURCE_REMOVE

    def _nudge_cli_exit(self, tab: TerminalTab) -> bool:
        """The CLI was asked to leave (by its exit keystroke or /bg) yet still
        owns the tab's terminal — typically parked on its session-list screen.
        Feed the exit keystroke to dismiss it so the pending close can finish.
        A no-op when the CLI already exited (then the keystroke would only
        reach the shell, which the close is about to end anyway)."""
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
        # One ask doesn't always land. A mid-turn agent spends the first
        # Ctrl+C Ctrl+C interrupting itself and clearing its input box rather
        # than exiting, and /bg sometimes drops the CLI to its session-list
        # screen instead (seen with tabs attached to a detached session) —
        # either would hang the close until the force-close below. A CLI still
        # owning the terminal this long after being asked to leave is the
        # tell: ask again. Safe for a merely-slow exit too — the extra input
        # queues behind the pending command and is discarded when the CLI
        # exits.
        if self._closing_pages[page] in (8, 24):  # ~2.4s / ~7.2s
            self._nudge_cli_exit(tab)
        if self._closing_pages[page] >= 40:  # ~12s safety net
            self._close_confirmed(page)
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _chain(self, session_id: str) -> set[str]:
        """Every id a row's conversation has run under — its own and each /bg
        fork's. They all stand for the same conversation, so a row's tab,
        status and pending-detach state can hang off any of them.

        The whole chain, not just its ends: a session backgrounded twice is
        mid-handoff under its previous fork's id while the newest one is being
        recorded, and that id is in the middle."""
        return set(self.state.forward_chain(session_id))

    def _page_for(self, session_id: str) -> Adw.TabPage | None:
        """The tab a sidebar row's session is open in. A row standing in for a
        live /bg fork is opened by attaching to that fork, so its tab is bound
        to the fork's id — look through the forwards too, newest first, so the
        liveliest binding wins if more than one is open."""
        return next(
            (
                page
                for sid in reversed(self.state.forward_chain(session_id))
                if (page := self._pages.get(sid))
            ),
            None,
        )

    def _is_detached(self, session_id: str) -> bool:
        """Whether a row's conversation is running as a background agent — under
        its own id, or under the id its /bg fork runs as. A pending detach
        counts: assume the /bg worked until the confirmation watch says
        otherwise."""
        chain = self._chain(session_id)
        return bool(chain & self._bg_status.background_ids or chain & set(self._pending_bg))

    def _detaching_now(self, session_id: str) -> bool:
        """Whether a /bg was fed for this row's conversation and hasn't been
        confirmed yet — the window in which the row stays disabled."""
        return bool(self._chain(session_id) & self._detaching)

    def _row_status(self, row_id: str) -> str:
        page = self._page_for(row_id)
        if page is None:
            # No tab — but the session may still be running detached (/bg).
            return "background" if self._is_detached(row_id) else ""
        return "attention" if page.get_needs_attention() else "open"

    def _sync_status(self, session_id: str) -> None:
        """Recompute the status of every row this session shows up as: its own,
        plus any row standing in for it as a /bg fork with no row of its own."""
        for row_id in self.store.rows_representing(session_id):
            status = self._row_status(row_id)
            log.debug("status: %s -> %s", row_id, status or "(none)")
            self.store.set_status(row_id, status)
        self.sidebar.update_footer()
        # A row's background button appears with its tab; whether it's pressable
        # is the gate's call.
        self._refresh_background_affordances()

    def _on_background_ids_changed(self, changed: set[str]) -> None:
        # Confirmed detaches: membership owns the yellow line from here on, so
        # the assumed-detached state can go. A /bg that forked is confirmed by
        # its fork's id turning up, not by the row's own.
        live = changed & self._bg_status.background_ids
        for session_id in list(self._pending_bg):
            if self._chain(session_id) & live:
                self._clear_backgrounding(session_id, "detach confirmed by the agent list")
        for session_id in changed:
            self._sync_status(session_id)

    # -- pre-emptive /bg status ----------------------------------------------

    def _set_row_backgrounding(self, session_id: str, flag: bool) -> None:
        """Disable (or re-enable) every row standing for this session — the
        row may be the one its /bg fork forked from, not its own."""
        for row_id in self.store.rows_representing(session_id):
            self.store.set_backgrounding(row_id, flag)

    def _mark_backgrounding(self, session_id: str) -> None:
        """A /bg was just fed: treat the session as backgrounded right away —
        yellow guide line once its tab closes, sidebar row disabled — instead of
        waiting for the agent CLI to list it. The row re-enables as soon as the
        detach is confirmed; the assumed-detached state lasts until the agent
        list reports it, the confirmation watch gives up, or the safety timeout
        fires."""
        if session_id in self._pending_bg:
            return
        log.info("bg-pending: %s marked (detach fed, awaiting confirmation)", session_id)
        self._detaching.add(session_id)
        self._pending_bg[session_id] = GLib.timeout_add_seconds(
            45, self._backgrounding_expired, session_id
        )
        self._set_row_backgrounding(session_id, True)
        self._refresh_background_affordances()  # the gate closes app-wide

    def _confirm_backgrounding(self, session_id: str) -> None:
        """The detach is confirmed. Re-enable the row immediately: clicking it
        now attaches to the live agent instead of resuming a stale transcript.
        Waiting for the fork's row instead would mean waiting out the safety
        timeout whenever the fork's agent detaches without doing any work — it
        leaves only a metadata stub, so that row may never arrive. The
        assumed-detached state (and with it the yellow line) stays until the
        agent list catches up, so the line never blinks."""
        if session_id not in self._detaching:
            return
        log.info("bg-pending: %s row re-enabled (detach confirmed)", session_id)
        self._detaching.discard(session_id)
        self._set_row_backgrounding(session_id, False)
        self._on_detach_settled()

    def _clear_backgrounding(self, session_id: str, reason: str = "") -> None:
        source = self._pending_bg.pop(session_id, None)
        if source is None:
            return
        log.info("bg-pending: %s cleared (%s)", session_id, reason or "unspecified")
        GLib.source_remove(source)
        self.state.clear_pending_detach(session_id)
        self._detaching.discard(session_id)
        self._set_row_backgrounding(session_id, False)
        self._sync_status(session_id)  # the line follows the agent list again
        self._on_detach_settled()

    def _backgrounding_expired(self, session_id: str) -> bool:
        # Confirmation never arrived (e.g. the agent exited right after
        # detaching): stop pretending, re-enable the row.
        log.info("bg-pending: %s expired (safety timeout, never confirmed)", session_id)
        self._pending_bg.pop(session_id, None)
        # Keep the persisted record: the agent may still be starting up, and
        # the next launch gets one more chance to pair them.
        self._detaching.discard(session_id)
        self._set_row_backgrounding(session_id, False)
        self._sync_status(session_id)
        self._on_detach_settled()
        return GLib.SOURCE_REMOVE

    def _update_active_row(self) -> None:
        """Tell the sidebar which session (or new-session placeholder) the
        selected tab is showing. A tab keeps its placeholder highlighted even
        after resolving, until the store discovers the session's real row."""
        page = self.tab_view.get_selected_page()
        row_id = None
        if page is not None:
            row_id = self._placeholder_pages.get(page)
            if row_id is None and (session_id := self._session_id_of(page)):
                # A tab attached to a /bg fork runs under an id the sidebar has
                # no row for; the row it forked from is the one to highlight.
                rows = self.store.rows_representing(session_id)
                row_id = rows[0] if rows else session_id
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
            session_id = self._session_id_of(page)
            if session_id:
                self._sync_status(session_id)
        if self.state.get_setting("notify_idle"):
            self._schedule_idle_notify(page)

    # -- terminal panel ------------------------------------------------------

    def _update_close_buttons(self, page: Adw.TabPage | None) -> None:
        """The header exit/background buttons act on the focused session, so
        they show only while a session tab is selected — and backgrounding
        only for providers that support detaching.

        The background button additionally greys out whenever the handoff
        couldn't be tracked (see _background_blocker). Greyed rather than
        hidden: a button that vanishes for the couple of seconds a new thread
        takes to register reads as a glitch, and the tooltip can say why."""
        tab = page.get_child() if page is not None else None
        is_session = isinstance(tab, TerminalTab)
        self.exit_btn.set_visible(is_session)
        self.background_btn.set_visible(
            is_session and tab.provider.background_exit() is not None
        )
        blocker = self._background_blocker(page)
        self.background_btn.set_sensitive(not blocker)
        self.background_btn.set_tooltip_text(_BG_TOOLTIPS.get(blocker, _BG_TOOLTIPS[""]))

    # -- the background gate ---------------------------------------------------

    def _background_blocker(self, page: Adw.TabPage | None) -> str:
        """Why this tab can't be handed to the background right now, or "" when
        it can. See bgstatus.background_blocker for what the reasons mean."""
        tab = page.get_child() if page is not None else None
        is_session = isinstance(tab, TerminalTab)
        session_id = tab.session_id if is_session else None
        return background_blocker(
            is_session=is_session,
            supports_detach=is_session and tab.provider.background_exit() is not None,
            is_fork=is_session and tab.fork,
            session_id=session_id,
            # A row to disable now and redirect once the fork id lands. For a
            # tab attached to a live fork that row is the one it forked from,
            # not one of the fork's own — it may never get one.
            has_row=bool(session_id and self.store.rows_representing(session_id)),
            detach_in_flight=bool(self._detaching),
        )

    def _refresh_background_affordances(self) -> None:
        """Re-evaluate every background affordance at once. The gate is
        app-wide — only one handoff runs at a time — so a detach starting or
        settling changes every row, not just the session being handed over.

        Only a session with an open tab has anything to hand over, so the gate
        is evaluated per open tab and every other row is simply switched off.
        Walking the rows instead would be quadratic: rows_representing scans
        them all."""
        self._update_close_buttons(self.tab_view.get_selected_page())
        allowed: set[str] = set()
        for session_id, page in self._pages.items():
            if not self._background_blocker(page):
                # The button lives on whichever row stands for this session —
                # for a tab attached to a live fork, the one it forked from.
                allowed.update(self.store.rows_representing(session_id))
        for row_id in self.store.row_ids():
            self.store.set_can_background(row_id, row_id in allowed)

    def _on_detach_settled(self) -> None:
        """A /bg handoff stopped being in flight — confirmed, abandoned or
        timed out. The gate reopens, and a quit-time queue can send the next."""
        self._refresh_background_affordances()
        if self._bg_queue or self._bg_queue_dialog is not None:
            self._advance_bg_queue()

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
        self._sync_window_title()
        if page is None:
            return
        self._cancel_idle(page)  # foreground now; no "finished" notification
        if page.get_needs_attention():
            page.set_needs_attention(False)
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
        page = self._page_for(session_id)
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
                # A graceful exit is already in flight; keep the tab open
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
            if agent_busy:  # confirmed: start a graceful exit in the background
                self._graceful_close(page)
                view.close_page_finish(page, False)  # keep the tab until it exits cleanly
                return True
        self._confirmed_closes.discard(page)
        self._closing_pages.pop(page, None)
        self._close_asking.discard(page)
        self._close_ok.discard(page)
        self._bg_ok.discard(page)
        archive_session_id = self._archive_on_close.pop(page, None)
        if archive_session_id:
            self.store.set_archived(archive_session_id, True)
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
            # Only if this page is the one actually bound to the session.
            # _on_session_resolved refuses to rebind an id another tab already
            # owns, but the losing tab keeps the id on itself — popping
            # unconditionally handed the winner's mapping away as the loser
            # closed, leaving a live tab its row could no longer reach.
            if self._pages.get(session_id) is page:
                self._pages.pop(session_id)
            self._sync_status(session_id)
        view.close_page_finish(page, True)
        self._refresh_background_affordances()  # a row without a tab can't be backgrounded
        if view.get_n_pages() == 0:
            self.content_stack.set_visible_child_name("empty")
            # Last tab drained — finish the window close, unless the quit-time
            # queue is still working through its handoffs (closing now would
            # abandon the ones it hasn't fed yet).
            if self._quitting and self._bg_queue_dialog is None:
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
            page = self._page_for(session.session_id)
            if page is not None:
                page.set_title(self._tab_title(session))

        dialogs.rename_dialog(
            self,
            session.preview or session.session_id,
            self.state.get_name(session.session_id) or "",
            save,
        )

    def _on_open_folder(self, _action, param: GLib.Variant) -> None:
        """Show the folder in the desktop's file manager. FileLauncher is the
        fallback: it goes through the portal, which finds a handler even when
        nothing has registered for inode/directory."""
        folder = param.get_string()
        info = openwith.default_file_manager()
        if info is not None:
            footerapps.launch_app(info, folder)
            return
        Gtk.FileLauncher.new(Gio.File.new_for_path(folder)).launch(self, None, None)

    def _on_open_folder_terminal(self, _action, param: GLib.Variant) -> None:
        """Open the folder in the desktop's own terminal emulator.

        The sidebar only offers this when one was found; the tab footer's
        terminal button offers it on every right-click, so a desktop with no
        terminal at all says so rather than swallowing the click.
        """
        info = openwith.default_terminal()
        if info is None:
            dialogs.error_dialog(
                self,
                _("No terminal application found"),
                _("Set $TERMINAL, or install a terminal emulator, to open folders here."),
            )
            return
        openwith.launch_terminal(info, param.get_string())

    def _on_open_folder_app(self, _action, param: GLib.Variant) -> None:
        app_id, folder = param.unpack()
        info = footerapps.resolve_app(app_id)
        if info is not None:
            footerapps.launch_app(info, folder)

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

    def _on_archive_session(self, _action, param: GLib.Variant) -> None:
        self._archive_session(param.get_string())

    def _archive_current_session(self) -> None:
        page = self.tab_view.get_selected_page()
        tab = page.get_child() if page is not None else None
        if isinstance(tab, TerminalTab) and tab.session_id:
            self._archive_session(tab.session_id)

    def _archive_session(self, session_id: str) -> None:
        archived = not self.state.is_archived(session_id)
        page = self._page_for(session_id) if archived else None
        if page is not None:
            # Close the tab through the normal close-page flow, so a busy tab
            # still gets its confirmation dialog — and archive the session
            # only once the tab really closes: cancelling the dialog keeps it
            # visible.
            self._archive_on_close[page] = session_id
            self.tab_view.close_page(page)
            return
        self.store.set_archived(session_id, archived)

    def _on_archive_project(self, _action, param: GLib.Variant) -> None:
        name = param.get_string()
        self.store.set_project_archived(name, not self.state.is_project_archived(name))

    def _on_show_archived(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        action.set_state(value)
        self.store.set_show_archived(value.get_boolean())

    def _on_select_sessions(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        action.set_state(value)
        self.sidebar.set_selection_mode(value.get_boolean())

    def _sync_trash_archived_action(self) -> None:
        self._trash_archived_action.set_enabled(bool(self.store.archived_sessions()))

    def _trash_archived(self) -> None:
        """Sidebar menu → trash every session the sidebar keeps out of sight:
        the ones archived individually plus everything inside an archived
        project."""
        sessions = self.store.archived_sessions()
        if not sessions:
            return

        keep = self._keep_projects_check([s.session_id for s in sessions])

        def do_trash() -> None:
            self._apply_keep_projects(keep)
            errors = self.store.trash_many([s.session_id for s in sessions])
            for session in sessions:
                if session.session_id in errors:
                    continue
                self._forget_transcript(session.session_id)
                # The row is gone for good — don't leave its id in the
                # archived set forever. An archived *project* stays archived:
                # new sessions started there should still land out of sight.
                self.state.set_archived(session.session_id, False)
            if errors:
                dialogs.error_dialog(
                    self,
                    _("Some transcripts could not be trashed"),
                    "\n".join(
                        f"{self.store.display_name(s)}: {errors[s.session_id]}"
                        for s in sessions
                        if s.session_id in errors
                    ),
                )

        dialogs.confirm_dialog(
            self,
            _("Delete {n} archived session(s)?").format(n=len(sessions)),
            blast_radius_body(len(sessions), self.store.archived_breakdown()),
            _("Move to Trash"),
            do_trash,
            extra_child=keep.check if keep else None,
        )

    def _keep_projects_check(self, session_ids: list[str]) -> _KeepProjects | None:
        """A check button for the projects that lose *every* session they have
        when `session_ids` go, so they can stay in the sidebar as empty headers
        instead of vanishing with their sessions. None when no project empties
        out. Checked by default: losing a project you never removed is the
        surprise, and an empty header costs nothing."""
        emptied = emptied_projects(self.store.sessions.values(), set(session_ids))
        if not emptied:
            return None
        return _KeepProjects(
            Gtk.CheckButton(
                label=_("Keep the {p} emptied project(s) in the sidebar").format(p=len(emptied)),
                active=True,
                halign=Gtk.Align.CENTER,
            ),
            emptied,
        )

    def _apply_keep_projects(self, keep: _KeepProjects | None) -> None:
        """Act on the check button — call before the transcripts go, while the
        projects still have sessions to take their folder from."""
        if keep is not None and keep.check.get_active():
            self.store.keep_projects(keep.projects)

    def _forget_transcript(self, session_id: str) -> None:
        """Drop everything the app kept for a session whose transcript just
        went away: its tab, its panel scrollback, its panel layout, its PRs."""
        page = self._pages.get(session_id)
        if page is not None:
            self.tab_view.close_page(page)
        panelhistory.delete(session_id)
        self.state.set_panel_state(session_id, None)
        self.state.set_session_prs(session_id, [])

    def _on_trash_session(self, _action, param: GLib.Variant) -> None:
        session = self._session_for(param)
        if session is None:
            return
        keep = self._keep_projects_check([session.session_id])

        def do_trash() -> None:
            self._apply_keep_projects(keep)
            error = self.store.trash(session.session_id)
            if error:
                dialogs.error_dialog(self, _("Could not trash transcript"), error)
                return
            self._forget_transcript(session.session_id)

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
            extra_child=keep.check if keep else None,
        )

    def _on_delete_session(self, _action, param: GLib.Variant) -> None:
        session = self._session_for(param)
        if session is None:
            return
        keep = self._keep_projects_check([session.session_id])

        def do_delete() -> None:
            self._apply_keep_projects(keep)
            error = self.store.delete(session.session_id)
            if error:
                dialogs.error_dialog(self, _("Could not delete transcript"), error)
                return
            self._forget_transcript(session.session_id)

        dialogs.confirm_dialog(
            self,
            _("Delete session permanently?"),
            _("“{name}” and its transcript file will be permanently deleted. "
              "This cannot be undone.").format(name=self.store.display_name(session)),
            _("Delete permanently"),
            do_delete,
            extra_child=keep.check if keep else None,
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
            application_icon=_app_icon_name(self),
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
        # Third-party notices live in THIRD_PARTY_LICENSES.md; each of its
        # headings becomes a section under the dialog's own Legal page.
        for title, markup in legal_sections():
            about.add_legal_section(title, None, Gtk.License.CUSTOM, markup)
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
        self.sidebar.refresh_project_icon_size()
        self._bg_status.set_polling(bool(self.state.get_setting("background_status_poll")))

    def _apply_settings_to_tabs(self) -> None:
        for i in range(self.tab_view.get_n_pages()):
            tab = self.tab_view.get_nth_page(i).get_child()
            if isinstance(tab, TerminalTab):
                tab.apply_settings(self.state.settings)
