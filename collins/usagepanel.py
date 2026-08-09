# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Sidebar panel showing Claude subscription usage.

Renders the same limit bars as Claude Code's ``/usage`` screen (session,
weekly all-models, weekly model-scoped) plus extra-usage credits when the
account has them. The panel owns its own poller: fetches run on a daemon
thread and marshal back via ``GLib.idle_add``, and the timer is gated on the
widget being mapped (same pattern as the terminal footer's cwd poll), so a
hidden sidebar or a disabled or collapsed panel costs nothing. Polling also
pauses while the window is suspended (minimized / fully hidden, GTK >= 4.12)
or the session is locked (screensaver ``ActiveChanged`` over D-Bus).
"""

from __future__ import annotations

import threading
import time

from gi.repository import Adw, Gio, GLib, Gtk

from . import usage
from .i18n import _
from .state import AppState

_POLL_INTERVAL_S = 300
# A sidebar toggle remaps the panel; don't re-fetch if the data is this fresh.
_MIN_REFRESH_GAP_S = 30
# The endpoint reports at most session + weekly + a few scoped bars; cap the
# rows we build so a surprise response can't flood the sidebar.
_MAX_BARS = 4

_ELLIPSIZE_END = 3  # Pango.EllipsizeMode.END
# Matches the archive-undo snackbar's timeout (window._UNDO_TOAST_SECONDS).
_ERROR_TOAST_SECONDS = 4

_ERROR_MESSAGES = {
    "no-credentials": lambda: _("Not logged in to Claude"),
    "expired": lambda: _("Claude login expired — run claude to refresh"),
    "auth": lambda: _("Claude login expired — run claude to refresh"),
    "network": lambda: _("Usage unavailable (offline)"),
}


def _bar_title(bar: usage.UsageBar) -> str:
    if bar.kind == "session":
        return _("Session (5h)")
    if bar.kind == "weekly_all":
        return _("Week — all models")
    if bar.kind == "weekly_scoped":
        if bar.model_name:
            return _("Week — {model}").format(model=bar.model_name)
        return _("Week — model")
    return bar.kind.replace("_", " ")


class _BarRow(Gtk.Box):
    """One usage limit: title + percent above a slim bar, reset time below."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._title = Gtk.Label(xalign=0.0, hexpand=True)
        self._title.set_ellipsize(_ELLIPSIZE_END)
        self._title.add_css_class("caption")
        self._percent = Gtk.Label(xalign=1.0)
        self._percent.add_css_class("caption")
        self._percent.add_css_class("dim-label")
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        top.append(self._title)
        top.append(self._percent)
        self.append(top)

        self._bar = Gtk.ProgressBar()
        self._bar.add_css_class("usage-bar")
        self.append(self._bar)

        self._resets = Gtk.Label(xalign=0.0)
        self._resets.add_css_class("caption")
        self._resets.add_css_class("dim-label")
        self.append(self._resets)

        self._data: usage.UsageBar | None = None

    def set_data(self, bar: usage.UsageBar) -> None:
        self._data = bar
        self._render(_bar_title(bar), bar.raw_percent, bar.severity)
        self.update_countdown()

    def set_static(self, title: str, raw_percent: float, sub_text: str,
                   severity: str = "normal") -> None:
        """Render values that aren't an API limit bar (extra-usage credits):
        a fixed sub-line instead of a reset countdown."""
        self._data = None
        self._render(title, round(raw_percent), severity)
        self._resets.set_text(sub_text)
        self._resets.set_visible(bool(sub_text))

    def _render(self, title: str, raw_percent: int, severity: str) -> None:
        self._title.set_text(title)
        self._percent.set_text(f"{raw_percent}%")
        self._bar.set_fraction(min(max(raw_percent, 0), 100) / 100)
        for cls in ("usage-sev-warning", "usage-sev-critical"):
            self._bar.remove_css_class(cls)
        if severity == "normal":  # derive from percent when the API doesn't say
            severity = (
                "critical" if raw_percent >= 90
                else "warning" if raw_percent >= 70
                else "normal"
            )
        if severity not in ("normal", ""):
            cls = "usage-sev-critical" if severity != "warning" else "usage-sev-warning"
            self._bar.add_css_class(cls)

    def update_countdown(self) -> None:
        """Re-render "Resets in …" from the stored data; called on every tick
        so the countdown doesn't go stale between fetches."""
        remaining = usage.time_until(self._data.resets_at) if self._data else ""
        self._resets.set_text(_("Resets in {t}").format(t=remaining) if remaining else "")
        self._resets.set_visible(bool(remaining))


class UsagePanel(Gtk.Box):
    """Compact Claude usage readout for the bottom of the session sidebar."""

    def __init__(self, state: AppState | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("usage-panel")
        self._state = state
        self._collapsed = bool(state.get_setting("usage_panel_collapsed")) if state else False

        # The whole heading is the collapse/expand toggle: a caret plus the
        # "Claude usage" label inside one flat button (styled in app.py to
        # keep looking like the plain caption heading it replaced).
        self._caret = Gtk.Image(valign=Gtk.Align.CENTER)
        self._caret.add_css_class("dim-label")
        title = Gtk.Label(label=_("Claude usage"), xalign=0.0, hexpand=True)
        title.add_css_class("caption-heading")
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        title_box.append(self._caret)
        title_box.append(title)
        toggle = Gtk.Button(child=title_box, hexpand=True)
        toggle.add_css_class("flat")
        toggle.add_css_class("usage-toggle")
        toggle.connect("clicked", lambda *_a: self._set_collapsed(not self._collapsed))
        self._spinner = Gtk.Spinner()
        self._refresh_btn = refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.add_css_class("flat")
        refresh.add_css_class("usage-refresh")
        refresh.set_valign(Gtk.Align.CENTER)
        refresh.set_tooltip_text(_("Refresh usage"))
        refresh.connect("clicked", lambda *_a: self._refresh(manual=True))
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        header.append(toggle)
        header.append(self._spinner)
        header.append(refresh)
        self.append(header)

        self._status = Gtk.Label(xalign=0.0, wrap=True)
        self._status.add_css_class("caption")
        self._status.add_css_class("dim-label")

        self._bars_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._bar_rows = [_BarRow() for _i in range(_MAX_BARS)]
        for row in self._bar_rows:
            row.set_visible(False)
            self._bars_box.append(row)
        # Extra-usage credits: a bar when a limit gives us a percentage,
        # otherwise the plain caption line. Kept out of _bar_rows so the
        # countdown tick never touches it.
        self._credits_bar = _BarRow()
        self._credits_bar.set_visible(False)
        self._bars_box.append(self._credits_bar)
        self._credits = Gtk.Label(xalign=0.0, wrap=True)
        self._credits.add_css_class("caption")
        self._credits.add_css_class("dim-label")
        self._credits.set_visible(False)
        self._bars_box.append(self._credits)

        self._stack = Gtk.Stack()
        self._stack.add_named(self._status, "status")
        self._stack.add_named(self._bars_box, "data")
        self._stack.set_visible_child_name("status")
        self._status.set_text(_("Checking usage…"))
        self.append(self._stack)

        self._snapshot: usage.UsageSnapshot | None = None
        self._fetching = False
        self._manual_fetch = False  # the in-flight fetch was user-requested
        self._error_toast: Adw.Toast | None = None
        self._source: int | None = None
        self._suspended = False  # toplevel minimized / fully hidden
        self._locked = False  # session screen locked
        self._watching_window = False
        self._watch_screen_lock()
        self._set_collapsed(self._collapsed)
        # Poll only while the bars are on screen — collapsing hides the stack,
        # so a collapsed panel doesn't fetch; expanding maps it again and
        # resumes (refreshing right away if the data has gone stale).
        self._stack.connect("map", lambda *_a: self._on_map())

    # -- collapsing --------------------------------------------------------

    def _set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self._caret.set_from_icon_name(
            "pan-end-symbolic" if collapsed else "pan-down-symbolic"
        )
        self._stack.set_visible(not collapsed)
        self._refresh_btn.set_visible(not collapsed)
        if self._state is not None and \
                bool(self._state.get_setting("usage_panel_collapsed")) != collapsed:
            self._state.set_setting("usage_panel_collapsed", collapsed)

    # -- polling -----------------------------------------------------------

    def _on_map(self) -> None:
        self._watch_window()
        self._start()

    def _watch_window(self) -> None:
        if self._watching_window:
            return
        root = self.get_root()
        if root is None:
            return
        self._watching_window = True
        # Gtk.Window:suspended needs GTK 4.12; on older GTK the panel just
        # keeps polling while minimized, as before.
        if root.find_property("suspended") is not None:
            self._suspended = root.get_property("suspended")
            root.connect("notify::suspended", self._on_window_suspended)

    def _on_window_suspended(self, window: Gtk.Window, _pspec: object) -> None:
        self._suspended = window.get_property("suspended")
        self._sync_paused()

    def _watch_screen_lock(self) -> None:
        """GNOME and the freedesktop screensaver interfaces share the same
        ``ActiveChanged(b)`` signal shape; subscribing to both covers most
        desktops, and a desktop with neither simply never signals."""
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            return
        for iface in ("org.freedesktop.ScreenSaver", "org.gnome.ScreenSaver"):
            bus.signal_subscribe(None, iface, "ActiveChanged", None, None,
                                 Gio.DBusSignalFlags.NONE, self._on_lock_signal)

    def _on_lock_signal(self, _bus: Gio.DBusConnection, _sender: str,
                        _path: str, _iface: str, _signal: str,
                        params: GLib.Variant) -> None:
        self._locked = bool(params.unpack()[0])
        self._sync_paused()

    def _paused(self) -> bool:
        return self._suspended or self._locked

    def _sync_paused(self) -> None:
        if self._paused():
            if self._source is not None:
                GLib.source_remove(self._source)
                self._source = None
        elif self._stack.get_mapped():
            self._start()

    def _start(self) -> None:
        if self._paused():
            return
        last = self._snapshot.fetched_at if self._snapshot else 0.0
        if time.time() - last >= _MIN_REFRESH_GAP_S:
            self._refresh()
        if self._source is None:
            self._source = GLib.timeout_add_seconds(_POLL_INTERVAL_S, self._tick)

    def _tick(self) -> bool:
        # Hidden sidebar/panel, collapsed panel, minimized window, or locked
        # screen → stop; map / notify::suspended / ActiveChanged restarts the
        # timer.
        if not self._stack.get_mapped() or self._paused():
            self._source = None
            return GLib.SOURCE_REMOVE
        for row in self._bar_rows:
            row.update_countdown()
        self._refresh()
        return GLib.SOURCE_CONTINUE

    def _refresh(self, manual: bool = False) -> None:
        # A click during an in-flight background fetch still claims its
        # result: the user asked, so a failure owes them the snackbar.
        self._manual_fetch = self._manual_fetch or manual
        if self._fetching:
            return
        self._fetching = True
        self._spinner.set_spinning(True)

        def work() -> None:
            try:
                result: object = usage.fetch_snapshot()
            except usage.UsageError as err:
                result = err
            except Exception as err:  # never let a surprise kill the panel
                result = usage.UsageError("http", str(err))
            GLib.idle_add(self._on_result, result)

        threading.Thread(target=work, name="usage-fetch", daemon=True).start()

    def _on_result(self, result: object) -> bool:
        manual, self._manual_fetch = self._manual_fetch, False
        self._fetching = False
        self._spinner.set_spinning(False)
        if isinstance(result, usage.UsageSnapshot):
            self._snapshot = result
            self._show_snapshot(result)
            # A success outdates whatever failure the snackbar still reports.
            if self._error_toast is not None:
                self._error_toast.dismiss()
            return GLib.SOURCE_REMOVE
        err = result if isinstance(result, usage.UsageError) else None
        if self._snapshot is not None:
            # Keep showing stale data; just note its age in the tooltip.
            age_min = max(1, int((time.time() - self._snapshot.fetched_at) / 60))
            self._stack.set_tooltip_text(_("As of {n}m ago").format(n=age_min))
        else:
            message = _ERROR_MESSAGES.get(err.kind if err else "", None)
            self._status.set_text(message() if message else _("Usage unavailable"))
            self._status.set_tooltip_text(str(err) if err else None)
            self._stack.set_visible_child_name("status")
        if manual:
            self._toast_error(err)
        return GLib.SOURCE_REMOVE

    def _toast_error(self, err: usage.UsageError | None) -> None:
        """A refresh the user asked for failed: say so in a snackbar floated
        over the sessions panel (the same overlay the archive-undo toast
        uses). Background polls stay silent — an offline machine would
        otherwise toast every poll. One snackbar at a time, as with undo: a
        fresh failure replaces the last toast instead of queueing behind it.
        """
        overlay = self.get_ancestor(Adw.ToastOverlay)
        if overlay is None:
            return
        if self._error_toast is not None:
            self._error_toast.dismiss()
        message = _ERROR_MESSAGES.get(err.kind if err else "", None)
        toast = Adw.Toast(
            title=message() if message else _("Couldn't refresh usage"),
            button_label=_("Dismiss"),
            timeout=_ERROR_TOAST_SECONDS,
        )
        # No action to run: the button only takes the toast down.
        toast.connect("button-clicked", lambda t: t.dismiss())
        toast.connect("dismissed", self._on_error_toast_dismissed)
        self._error_toast = toast
        overlay.add_toast(toast)

    def _on_error_toast_dismissed(self, toast: Adw.Toast) -> None:
        # Stops a later failure or success from dismissing a toast the
        # overlay already dropped (mirrors the undo toast's bookkeeping).
        if self._error_toast is toast:
            self._error_toast = None

    # -- rendering ---------------------------------------------------------

    def _show_snapshot(self, snapshot: usage.UsageSnapshot) -> None:
        self._stack.set_tooltip_text(None)
        # strict=False: extra API bars beyond _MAX_BARS rows are dropped on purpose.
        for row, bar in zip(self._bar_rows, snapshot.bars, strict=False):
            row.set_data(bar)
            row.set_visible(True)
        for row in self._bar_rows[len(snapshot.bars):]:
            row.set_visible(False)
        if not snapshot.bars:
            self._status.set_text(_("Usage unavailable"))
            self._stack.set_visible_child_name("status")
            return

        credits = snapshot.credits
        show_bar = show_line = False
        if credits and (credits.enabled or credits.used > 0):
            used = _format_money(credits.used, credits.currency)
            if credits.limit:
                sub = _("{used} of {limit}").format(
                    used=used, limit=_format_money(credits.limit, credits.currency)
                )
                if credits.spend_limit_reached:
                    sub += _(" — limit reached")
                self._credits_bar.set_static(
                    _("Extra usage"),
                    credits.used / credits.limit * 100,
                    sub,
                    severity="critical" if credits.spend_limit_reached else "normal",
                )
                show_bar = True
            else:  # no limit set → no percentage to draw, keep the text line
                text = _("Extra usage: {used}").format(used=used)
                if credits.spend_limit_reached:
                    text += _(" — limit reached")
                self._credits.set_text(text)
                show_line = True
        self._credits_bar.set_visible(show_bar)
        self._credits.set_visible(show_line)
        self._stack.set_visible_child_name("data")


def _format_money(amount: float, currency: str) -> str:
    if currency == "USD":
        return f"${amount:.2f}"
    return f"{amount:.2f} {currency}"
