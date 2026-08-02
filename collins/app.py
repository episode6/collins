# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-02. Full change history: git log for this file.

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

from .caffeine import duration_seconds
from .i18n import _
from .prefs import apply_color_scheme
from .state import AppState
from .store import SessionStore
from .window import MainWindow

# Bundled icons (e.g. tab-close-symbolic); found by name when installed.
_BUNDLED_ICONS = Path(__file__).resolve().parent.parent / "data" / "icons"

_CSS = b"""
.group-header { padding: 10px 10px 4px 10px; }

/* insertion line while dragging a project header to a new position */
row.drop-above { box-shadow: inset 0 2px 0 0 @accent_bg_color; }
row.drop-below { box-shadow: inset 0 -2px 0 0 @accent_bg_color; }

/* session-row state badges */
.waiting-badge { color: #e5a50a; }      /* Claude asked a question */
.interrupted-badge { color: #e01b24; }  /* user stopped Claude mid-task */

/* make the active tab clearly stand out from inactive ones. Its background
   color is set dynamically, not here: themes._apply_dynamic_theme_css keeps
   it matched to the current terminal theme's background (see themes.py), so
   the tab reads as part of the terminal it sits above rather than a
   mismatched frame around it. libadwaita marks the active AdwTabBar row
   with the GTK state `:selected`, not `:checked` (`:checked` is for
   checkbox/toggle-style widgets and silently matches nothing here). */
tabbar tab:selected {
  box-shadow: inset 0 -3px 0 #D97757;
}
tabbar tab:selected label { font-weight: bold; }
tabbar tab:not(:selected) label { opacity: 0.6; }

.count-badge {
  background-color: alpha(currentColor, 0.1);
  border-radius: 10px;
  padding: 1px 8px;
  font-size: 0.8em;
}

/* every session row is the same outlined card with a left guide line; what a
   running session adds is a fill, and (for a detached one) a status color on
   that line. The border box is identical in every status, so a row never
   shifts or resizes as its status changes; the selected tab's row is the one
   exception, and it changes only horizontally (see .active-tab below).
   The left indent is a widget margin set in sidebar.py, not a CSS margin
   here: it tracks the configurable project-icon size, so the card always
   starts just past the icon of the project header above it. */
row.session-child {
  margin-right: 16px;
  border: 1px solid alpha(currentColor, 0.15);
  border-left: 2px solid alpha(currentColor, 0.15);
  border-radius: 0 8px 8px 0;
  /* Adwaita's own row padding, restated so it is ours: the barber pole below
     is positioned by counting back over the border and this padding, and a
     theme with a different value would slide the pole off the guide line. */
  padding-left: 8px;
  /* smaller text than a stock sidebar row, and shorter: 34px of content plus
     the 1px borders puts the row at 36px, with the project headers keeping the
     coarser rhythm above it */
  min-height: 34px;
  font-size: 0.9em;
}
/* neither the archive/close button nor the selection-mode check may hold the
   row open at its stock size: entering selection mode must not resize rows */
row.session-child button {
  min-height: 20px;
  min-width: 20px;
  padding: 0 4px;
}
row.session-child checkbutton {
  min-height: 20px;
  min-width: 20px;
  padding: 0;
  margin-right: 4px;  /* keep the shrunken check off the title */
}
row.session-child checkbutton > check {
  min-height: 14px;
  min-width: 14px;
  margin: 0;
}
row.session-child:hover {
  background-color: alpha(currentColor, 0.1);
  border-color: alpha(currentColor, 0.3);
}
/* sessions running in an open tab share one fill, so live work stands out
   from the archive of past sessions as a group; their guide line stays the
   neutral one every row has. Detached (/bg) sessions are the other way round:
   a colored line and no fill. They are running, but there is no tab to return
   to, and the fill is what marks the sessions there is one for. These rules
   must stay below the plain :hover one, whose border-color shorthand would
   otherwise repaint the guide line, and above the active-tab rules, which take
   over the fill for the one session the selected tab is showing. */
row.session-child.running {
  background-color: alpha(currentColor, 0.13);
}
row.session-child.running:hover {
  background-color: alpha(currentColor, 0.18);
}
/* running detached (/bg): the one status the guide line still speaks for */
row.session-child.detached { border-left-color: #e5a50a; }

/* a finished run nobody has looked at yet (see SessionItem.unread): the guide
   line holds solid blue, the color of the pole that was just climbing it,
   until the user returns to the tab. Below the .detached rule so blue wins
   the line while a formerly-detached row still reads as such, and beaten by
   the .busy pole rule (its selector carries one more class), so a session
   sent straight back to work moves again instead of sitting on a stale flag. */
row.session-child.unread { border-left-color: #3584e4; }

/* An agent producing output right now (see activity.py) turns its row's guide
   line into a barber pole: stripes climbing while work is happening, still the
   moment it stops. Only sessions in a tab pole: a detached (/bg) one has no
   terminal to listen to, so it keeps the still yellow line of the .detached
   rule above whatever its agent is doing.

   The pole is painted, never laid out: the row keeps its 2px border-left (made
   transparent so the stripes show through it) and the gradient is a background
   layer exactly as wide as that border and sitting right on it, so a row's
   guide line reads as the same line whether it is still or moving, and nothing
   shifts as the pole starts or stops.

   Landing it there takes two properties, because GTK does not place a
   background where the CSS spec says. It ignores background-origin: the layer
   starts inside the border *and* the padding, 10px in for this row (2px border
   + 8px padding, pinned above), so background-position counts that whole
   distance back. And its default clip stops at the padding edge, which would
   throw the shifted layer away entirely, so the clip is widened to the border
   box. Both are load-bearing; drop either and the pole either sits 10px inside
   the card or vanishes.

   The tile is 2x12px and repeats down the line; the stripe period is 8.485px
   (12 / sqrt 2), the one value at which a 135deg gradient meets itself across
   a 12px vertical seam. Any other period leaves a visible cut where the tiles
   stack. One animation cycle travels exactly one tile, so the loop is
   seamless too. GTK stops all CSS animation when the desktop's animations are
   off, which is the reduced-motion behavior we want for free. */
@keyframes barber-pole {
  from { background-position: -10px 12px; }
  to   { background-position: -10px 0; }
}
row.session-child.running.busy {
  border-left-color: transparent;
  background-clip: border-box;
  background-repeat: repeat-y;
  background-size: 2px 12px;
  animation: barber-pole 900ms linear infinite;
  background-image: repeating-linear-gradient(135deg,
    #1c71d8 0px, #1c71d8 4.243px, #99c1f1 4.243px, #99c1f1 8.485px);
}
/* the session shown in the currently selected tab: the fill says which one it
   is, and the card runs out to the panel's right edge (square-cornered, with
   no right border) so the row reads as joined to the terminal it is showing
   rather than as one more card in the list. The guide line keeps saying what
   the session's status is. Only horizontal geometry changes, so the row keeps
   its height and nothing in the list shifts as the selected tab moves. */
row.session-child.active-tab {
  background-color: alpha(#D97757, 0.16);
  margin-right: 0;
  border-right: none;
  border-radius: 0;
}
row.session-child.active-tab:hover {
  background-color: alpha(#D97757, 0.22);
}
/* ...but only the card runs out to the edge: its content stays where the
   content of every other row is. The 17px the card gave up on the right
   (16px margin + 1px border) comes back as a margin on the content box, so
   the timestamp, the hover buttons and a long folder path all line up with
   the rows above and below instead of sliding out to the panel edge too. */
row.session-child.active-tab > box {
  margin-right: 17px;
}

/* The session list's scrollbar rides the panel's right edge. Stock Adwaita
   insets its trough from that edge (4px as the thin overlay indicator, 8px
   once the pointer nears it and it expands), which leaves the bar floating in
   the gutter beside the row cards, reading as one more column of the list.
   Dropping the margin puts it against the border, where it reads as chrome
   belonging to the panel. The trough keeps its own width in both states, so
   only its position moves, and being an overlay scrollbar it takes no width
   from the list either way: no row reflows. */
.sidebar-scroll > scrollbar.vertical > range > trough {
  margin-right: 0;
}

/* ...and its slider is solid, not translucent, in the same color the panel
   border next to it appears in. That border is Adwaita's paned separator:
   currentColor at --border-opacity composited over the window background, so
   the same mix (against the background rather than transparent) is that
   exact rendered color, opaque. Plain currentColor would be the full-strength
   foreground (white in dark mode); stock Adwaita instead mixes it 20-60% into
   transparent (resting/hover/drag), which lets the row cards bleed through
   the bar. This provider sits at APPLICATION priority, so the one rule
   outranks every state variant in the theme; the fade-in/out of the overlay
   indicator is widget opacity animated by GTK itself, not a style, so idle
   hiding still works. */
.sidebar-scroll > scrollbar.vertical > range > trough > slider {
  background-color: color-mix(in srgb, currentColor var(--border-opacity), var(--window-bg-color));
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

/* visual bell: a terminal's BEL tints the header bar, plus the ringing
   session's tab header and sidebar row, all fading out over the animation.
   An inset shadow rather than background-color, so the flash composites over
   each widget's normal background instead of replacing it. The spread must
   exceed the widget's width: GSK fills an inset spread from the center out,
   not from the edges in, so a smaller spread tints a band in the middle of
   the widget instead of all of it. */
@keyframes bell-flash {
  from { box-shadow: inset 0 0 0 9999px alpha(#D97757, 0.4); }
  to   { box-shadow: inset 0 0 0 9999px alpha(#D97757, 0); }
}
headerbar.bell-flash,
tabbar tab.bell-flash,
row.session-child.bell-flash {
  animation: bell-flash 400ms ease-out;
}
/* a busy row's barber pole also claims the animation property, with one more
   class on its selector, which would silently drop the flash exactly when a
   bell is most likely (the agent is mid-turn): the row that is busy while its
   bell rings must carry both animations in one declaration. */
row.session-child.running.busy.bell-flash {
  animation: barber-pole 900ms linear infinite, bell-flash 400ms ease-out;
}

/* slim per-tab footer row: working directory + terminal-panel buttons */
.tab-footer {
  padding: 1px 8px;
  border-top: 1px solid alpha(currentColor, 0.15);
}
/* The footer buttons read as a row of icons, not as buttons: tight enough
   that the icons sit close together, and with no hover background (the
   tooltip and the pointer are feedback enough at this size). */
.tab-footer button {
  padding: 0 2px;
  min-height: 22px;
  min-width: 20px;
}
.tab-footer button:hover {
  background: none;
  box-shadow: none;
}

/* ...except inside the caret's PR list, which is a menu and reads as one. A
   popover belongs to the widget tree of the button it hangs off, so the two
   rules above apply to its rows too: without these they sit 22px tall with
   2px of side padding, flush against the popover's edge, and stay flat as the
   pointer crosses them. The contents padding gives the list room on every
   side (its longest line was reaching the frame), and each row gets menu-item
   geometry plus a hover fill, so the pointer says what a click would open. */
popover.pr-menu > contents {
  padding: 6px;
}
popover.pr-menu button.pr-menu-row {
  padding: 4px 8px;
  min-height: 28px;
  border-radius: 6px;
}
popover.pr-menu button.pr-menu-row:hover {
  background-color: alpha(currentColor, 0.1);
}
/* The actions submenu's header when there is no list to lead back to (a
   footer chip's own menu): a plain caption naming the PR, padded like the
   rows under it so the column of titles still lines up. */
popover.pr-menu .pr-menu-title {
  padding: 4px 8px;
  min-height: 28px;
}

/* The "Open in <app>" rows of a project's context menu, which carry an icon
   and so can't be menu items (GtkModelButton hides an icon that shares its
   row with a label). Buttons instead, given the geometry of the model buttons
   they sit between: the same height and the same side padding, so an icon row
   lines up with the plain items above and below it. */
popover.menu button.open-with-row {
  padding: 0 10px;
  min-height: 26px;
  border-radius: 6px;
  font-weight: inherit;
}
popover.menu button.open-with-row:hover {
  background-color: alpha(currentColor, 0.1);
}

/* sidebar usage panel: subscription limit bars under the session list */
.usage-panel {
  padding: 8px 12px 10px 12px;
  border-top: 1px solid alpha(currentColor, 0.15);
}
/* The refresh button is a secondary affordance next to the "Claude usage"
   heading: shrunk to roughly the heading's own height so it stops padding
   out the header row and dominating the panel. */
.usage-panel button.usage-refresh {
  padding: 0 2px;
  min-height: 20px;
  min-width: 20px;
}
.usage-panel button.usage-refresh image {
  -gtk-icon-size: 13px;
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
/* a chip that knows its file: clickable, reads as a link */
.chat-tool-link {
  opacity: 0.8;
  text-decoration-line: underline;
}
"""


APP_ID = "com.episode6.Collins"

# Colors that must follow the light/dark scheme, keyed by "is dark". GTK CSS has
# no working prefers-color-scheme query, so these are re-applied from a second
# provider whenever the effective scheme flips (see _apply_scheme_css).
#
# The footer's PR marks use GitHub's own pairs of shades, so a merged PR or a
# red build reads here exactly as it does on the PR page, in either theme.
_SCHEME_CSS = """
.pr-merged { color: %(merged_purple)s; }
/* GitHub's open-PR green is its checks-passed green, so the open base icon
   shares the shade; a draft (and a PR nothing is known about) takes GitHub's
   muted grey; the conflict badge borrows the pending yellow — attention-
   colored, but not the red of a failed check, since a rebase fixes it
   without a CI run being wrong. */
.pr-open { color: %(passed_green)s; }
.pr-draft { color: %(draft_grey)s; }
.pr-conflict { color: %(pending_yellow)s; }
.pr-checks-failed { color: %(failed_red)s; }
.pr-checks-pending { color: %(pending_yellow)s; }
"""
_MARK_COLORS = {
    False: {  # light
        "merged_purple": "#8250df",
        "passed_green": "#1a7f37",
        "failed_red": "#cf222e",
        "pending_yellow": "#bf8700",
        "draft_grey": "#59636e",
    },
    True: {  # dark
        "merged_purple": "#a371f7",
        "passed_green": "#3fb950",
        "failed_red": "#f85149",
        "pending_yellow": "#d29922",
        "draft_grey": "#9198a1",
    },
}


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

        # Scheme-dependent colors ride in their own provider so a light/dark
        # flip only reloads these few rules, never the stylesheet above.
        self._scheme_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            display, self._scheme_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        style = Adw.StyleManager.get_default()
        style.connect("notify::dark", lambda *_: self._apply_scheme_css())
        self._apply_scheme_css()

        # Caffeine Mode: non-None while we hold a sleep/idle inhibitor.
        # The live toggle is deliberately not persisted — a restart must
        # never silently keep the machine awake unless the user explicitly
        # opted in via the caffeine_on_launch setting.
        self._caffeine_cookie: int | None = None
        # The optional shut-off timer: when it's running, a monotonic deadline
        # (µs) and the once-a-second source that both drives every window's
        # countdown and turns Caffeine Mode off on reaching it.
        self._caffeine_deadline: int | None = None
        self._caffeine_tick: int | None = None

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

    def _apply_scheme_css(self) -> None:
        """Load the scheme's colors. Runs at startup and on every light/dark
        flip, whether that came from the setting or from the system."""
        dark = Adw.StyleManager.get_default().get_dark()
        self._scheme_provider.load_from_data((_SCHEME_CSS % _MARK_COLORS[dark]).encode())

    @property
    def caffeine_enabled(self) -> bool:
        return self._caffeine_cookie is not None

    @property
    def caffeine_remaining(self) -> int | None:
        """Seconds left before Caffeine Mode turns itself off, or None when no
        timer is running (it stays on until someone turns it off)."""
        if self._caffeine_deadline is None:
            return None
        left = self._caffeine_deadline - GLib.get_monotonic_time()
        return max(0, -(-left // 1_000_000))  # round up, so a 1h timer opens at 1:00:00

    def set_caffeine_enabled(self, enabled: bool, seconds: int | None = None) -> None:
        """Toggle Caffeine Mode: inhibit suspend and screen blanking app-wide.

        `seconds` arms a shut-off timer that turns Caffeine Mode off again when
        it runs out; None leaves it on indefinitely. Any timer already running
        is cancelled either way, so re-picking a duration restarts the clock and
        a plain toggle never leaves a stale one armed.
        """
        self._cancel_caffeine_timer()
        if enabled != self.caffeine_enabled:
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
        # Nothing to count down to if the inhibit didn't take (or was refused).
        if seconds and self.caffeine_enabled:
            self._caffeine_deadline = GLib.get_monotonic_time() + seconds * 1_000_000
            self._caffeine_tick = GLib.timeout_add_seconds(1, self._on_caffeine_tick)
        self._sync_caffeine_windows()

    def _cancel_caffeine_timer(self) -> None:
        if self._caffeine_tick is not None:
            GLib.source_remove(self._caffeine_tick)
        self._caffeine_tick = None
        self._caffeine_deadline = None

    def _on_caffeine_tick(self) -> bool:
        """Once a second while a timer runs: redraw the countdowns, and turn
        Caffeine Mode off when the deadline passes."""
        if self.caffeine_remaining:
            self._sync_caffeine_windows()
            return GLib.SOURCE_CONTINUE
        # Forget the source before turning off: this callback *is* the source,
        # and returning REMOVE below is what disposes of it.
        self._caffeine_tick = None
        self.set_caffeine_enabled(False)
        return GLib.SOURCE_REMOVE

    def _sync_caffeine_windows(self) -> None:
        for window in self.get_windows():
            sync = getattr(window, "sync_caffeine_toggle", None)
            if sync is not None:
                sync()

    def _new_window(self) -> MainWindow:
        window = MainWindow(application=self, state=self.state, store=self.store)
        window.present()
        return window

    def _main_window(self) -> MainWindow | None:
        """The active window, unless that's a popped-out editor window (or
        some other non-main window): those must never be handed a main
        window's job, so fall back to any main window that exists."""
        window = self.get_active_window()
        if isinstance(window, MainWindow):
            return window
        return next((w for w in self.get_windows() if isinstance(w, MainWindow)), None)

    def _on_focus_session(self, _action, param: GLib.Variant) -> None:
        window = self._main_window()
        if window is None:
            return
        window.present()
        session_id = param.get_string()
        if session_id:
            window.focus_session(session_id)

    def do_activate(self) -> None:
        window = self._main_window()
        if window is None:
            window = self._new_window()
            # Fresh launch: reopen the session that was active when the app
            # was last closed. Extra windows (Ctrl+Shift+N) start empty.
            window.restore_last_session()
            if self.state.get_setting("caffeine_on_launch"):
                self.set_caffeine_enabled(
                    True,
                    seconds=duration_seconds(self.state.get_setting("caffeine_launch_timer") or ""),
                )
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
