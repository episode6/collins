# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-28. Full change history: git log for this file.

"""Application entry point."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .i18n import _
from .prefs import apply_color_scheme
from .state import AppState
from .store import SessionStore
from .window import MainWindow

# Bundled icons (e.g. tab-close-symbolic); found by name when installed.
_BUNDLED_ICONS = Path(__file__).resolve().parent.parent / "data" / "icons"

_CSS = b"""
.status-dot {
  min-width: 8px;
  min-height: 8px;
  border-radius: 100%;
  background-color: alpha(currentColor, 0.25);
}
.status-dot.open { background-color: #2ec27e; }
.status-dot.attention { background-color: #3584e4; }
.status-dot.background { background-color: #e5a50a; }  /* running detached */

.group-header { padding: 10px 10px 4px 10px; }

/* insertion line while dragging a project header to a new position */
row.drop-above { box-shadow: inset 0 2px 0 0 @accent_bg_color; }
row.drop-below { box-shadow: inset 0 -2px 0 0 @accent_bg_color; }

/* session-row state badges */
.waiting-badge { color: #e5a50a; }      /* Claude asked a question */
.interrupted-badge { color: #e01b24; }  /* user stopped Claude mid-task */

/* make the active tab clearly stand out from inactive ones */
tabbar tab:checked {
  background-color: alpha(#D97757, 0.22);
  box-shadow: inset 0 -3px 0 #D97757;
}
tabbar tab:checked label { font-weight: bold; }
tabbar tab:not(:checked) label { opacity: 0.6; }

.count-badge {
  background-color: alpha(currentColor, 0.1);
  border-radius: 10px;
  padding: 1px 8px;
  font-size: 0.8em;
}

/* children connect to their group via a left guide line, on a faint card */
row.session-child {
  margin-left: 20px;
  margin-right: 16px;
  background-color: alpha(currentColor, 0.06);
  border-left: 2px solid alpha(currentColor, 0.15);
  border-radius: 0 8px 8px 0;
}
row.session-child:hover {
  background-color: alpha(currentColor, 0.1);
  border-left-color: alpha(currentColor, 0.3);
}
/* the session shown in the currently selected tab */
row.session-child.active-tab {
  background-color: alpha(#D97757, 0.16);
  border-left-color: #D97757;
}
row.session-child.active-tab:hover {
  background-color: alpha(#D97757, 0.22);
}

/* interactive prompt card overlaid on the terminal */
.chat-card {
  background-color: @window_bg_color;
  border: 1px solid alpha(#D97757, 0.6);
  border-radius: 12px;
  padding: 12px;
  box-shadow: 0 2px 12px alpha(black, 0.35);
}
.chat-card-header {
  color: #D97757;
  font-weight: bold;
  font-size: 0.85em;
}
.chat-option {
  padding: 8px 12px;
}
.chat-option-static {
  padding: 4px 8px;
}

/* slim per-tab footer row: working directory + terminal-panel buttons */
.tab-footer {
  padding: 1px 8px;
  border-top: 1px solid alpha(currentColor, 0.15);
}
.tab-footer button {
  padding: 0 6px;
  min-height: 22px;
  min-width: 22px;
}

/* sidebar usage panel: subscription limit bars under the session list */
.usage-panel {
  padding: 8px 12px 10px 12px;
  border-top: 1px solid alpha(currentColor, 0.15);
}
.usage-panel progressbar.usage-bar trough,
.usage-panel progressbar.usage-bar progress {
  min-height: 6px;
  border-radius: 3px;
}
.usage-panel progressbar.usage-bar progress { background-color: #D97757; }
.usage-panel progressbar.usage-bar.usage-sev-warning progress { background-color: #e5a50a; }
.usage-panel progressbar.usage-bar.usage-sev-critical progress { background-color: #e01b24; }

/* chat-session tab: streaming bubbles + tool chips */
.chat-bubble {
  padding: 8px 12px;
  border-radius: 14px;
}
.chat-user {
  background-color: #D97757;
  color: white;
}
.chat-assistant {
  background-color: alpha(currentColor, 0.08);
}
.chat-tool {
  font-size: 0.85em;
  opacity: 0.6;
  padding: 2px 4px;
}
"""


APP_ID = "com.episode6.Collins"


class App(Adw.Application):
    def __init__(self) -> None:
        # COLLINS_APP_ID lets a demo instance run alongside the real one (for screenshots).
        super().__init__(application_id=os.environ.get("COLLINS_APP_ID") or APP_ID)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        display = Gdk.Display.get_default()
        provider = Gtk.CssProvider()
        provider.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        if _BUNDLED_ICONS.is_dir():  # running from source; installed icons live in the system theme
            Gtk.IconTheme.get_for_display(display).add_search_path(str(_BUNDLED_ICONS))

        # Caffeine Mode: non-None while we hold a sleep/idle inhibitor.
        # Deliberately not persisted — a restart must never silently keep
        # the machine awake.
        self._caffeine_cookie: int | None = None

        # Shared across all windows so scans/monitors aren't duplicated and
        # state.json writes don't race.
        self.state = AppState()
        apply_color_scheme(self.state.get_setting("color_scheme"))
        self.store = SessionStore(self.state)
        self.store.start()

        focus = Gio.SimpleAction.new("focus-session", GLib.VariantType("s"))
        focus.connect("activate", self._on_focus_session)
        self.add_action(focus)

        new_window = Gio.SimpleAction.new("new-window", None)
        new_window.connect("activate", lambda *_: self._new_window())
        self.add_action(new_window)
        self.set_accels_for_action("app.new-window", ["<Control><Shift>n"])

    @property
    def caffeine_enabled(self) -> bool:
        return self._caffeine_cookie is not None

    def set_caffeine_enabled(self, enabled: bool) -> None:
        """Toggle Caffeine Mode: inhibit suspend and screen blanking app-wide."""
        if enabled == self.caffeine_enabled:
            return
        if enabled:
            # inhibit() returns 0 when the platform can't inhibit; treating
            # that as "still off" makes every window's toggle snap back.
            self._caffeine_cookie = (
                self.inhibit(
                    self.get_active_window(),
                    Gtk.ApplicationInhibitFlags.SUSPEND | Gtk.ApplicationInhibitFlags.IDLE,
                    _("Caffeine Mode is on"),
                )
                or None
            )
        else:
            self.uninhibit(self._caffeine_cookie)
            self._caffeine_cookie = None
        for window in self.get_windows():
            sync = getattr(window, "sync_caffeine_toggle", None)
            if sync is not None:
                sync()

    def _new_window(self) -> MainWindow:
        window = MainWindow(application=self, state=self.state, store=self.store)
        window.present()
        return window

    def _on_focus_session(self, _action, param: GLib.Variant) -> None:
        window = self.get_active_window()
        if window is None:
            return
        window.present()
        session_id = param.get_string()
        if session_id and hasattr(window, "focus_session"):
            window.focus_session(session_id)

    def do_activate(self) -> None:
        window = self.get_active_window()
        if window is None:
            window = self._new_window()
            # Fresh launch: reopen the session that was active when the app
            # was last closed. Extra windows (Ctrl+Shift+N) start empty.
            window.restore_last_session()
        window.present()


def main() -> int:
    from . import i18n
    from .state import AppState

    # COLLINS_LOG=INFO (or DEBUG) surfaces diagnostic logs on the console,
    # e.g. bgstatus's watch-dir and refresh activity.
    logging.basicConfig(level=(os.environ.get("COLLINS_LOG") or "WARNING").upper())
    i18n.init(AppState().get_setting("language"))
    app = App()
    return app.run(sys.argv)
