# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The popped-out editor window: a `TerminalTab`'s live `EditorPane` in an
`Adw.ApplicationWindow` of its own, for a second monitor.

The pane is *reparented* here, never copied — the same widget instance moves
out of the tab and back, so open buffers, cursor positions, dirty state and
file monitors all survive both directions without any serialization. One
editor per terminal tab, in one place at a time: while a pane lives here the
tab's in-tab panel slot is empty, and closing this window (the headerbar
button, the WM close button, or the tab's footer icon) docks it back.
"""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from .i18n import _  # noqa: E402
from .state import AppState, clamp_window_size  # noqa: E402


def _monitor_sizes() -> list[tuple[int, int]]:
    """Same helper as window.py's — duplicated because window.py imports this
    module, so importing it back would be circular."""
    display = Gdk.Display.get_default()
    if display is None:
        return []
    monitors = display.get_monitors()
    sizes = []
    for i in range(monitors.get_n_items()):
        geometry = monitors.get_item(i).get_geometry()
        sizes.append((geometry.width, geometry.height))
    return sizes


class EditorWindow(Adw.ApplicationWindow):
    """Hosts one detached `EditorPane`. Geometry persists app-wide (every
    editor window shares one remembered size, as the main windows do),
    mirroring MainWindow's debounced save."""

    def __init__(
        self,
        pane: Gtk.Widget,
        state: AppState,
        title: str,
        icon_name: str,
        on_dock_back,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.state = state
        self._pane: Gtk.Widget | None = pane
        self._on_dock_back = on_dock_back
        self.set_title(title)
        self.set_icon_name(icon_name)

        width = int(state.get_setting("editor_window_width"))
        height = int(state.get_setting("editor_window_height"))
        self.set_default_size(*clamp_window_size(width, height, _monitor_sizes()))
        if state.get_setting("editor_window_maximized"):
            self.maximize()

        # The dock-back control, symmetric with the pane's own detach button
        # (window-new-symbolic): the headerbar is this window's status row.
        dock_btn = Gtk.Button(icon_name="view-restore-symbolic")
        dock_btn.set_tooltip_text(_("Move editor back into its tab"))
        dock_btn.connect("clicked", lambda *_a: self.close())
        header = Adw.HeaderBar()
        header.pack_start(dock_btn)

        self._toolbar = Adw.ToolbarView()
        self._toolbar.add_top_bar(header)
        self._toolbar.set_content(pane)
        self.set_content(self._toolbar)

        # The pane can re-root while it lives here — its session stepped into a
        # worktree, and the editor followed — and this window is titled after
        # the project the pane is showing.
        self._root_handler = pane.connect(
            "root-changed", lambda _pane, root: self.set_title(Path(root).name)
        )

        self._geometry_save_source: int | None = None
        for prop in ("default-width", "default-height", "maximized"):
            self.connect(f"notify::{prop}", self._schedule_save_geometry)
        self.connect("close-request", self._on_close_request)

    # -- docking back ----------------------------------------------------------

    def _on_close_request(self, *_args) -> bool:
        # Every close is a dock-back: the editor has no "close without
        # docking" — its buffers belong to the tab, which is still open.
        self.release_pane()
        return False

    def release_pane(self) -> None:
        """Hand the pane back to whoever owns the tab (idempotent): unparent
        it from this window and invoke the dock-back callback. Called from
        close-request, and directly (followed by `destroy()`) when the main
        window needs the pane back without waiting on the WM — e.g. its own
        close must not leave an orphan editor window keeping the app alive."""
        if self._pane is None:
            return
        pane = self._pane
        self._pane = None
        # The pane outlives this window — it is going back into its tab, where
        # the tab's own handler keeps everything else in step.
        pane.disconnect(self._root_handler)
        if self._geometry_save_source is not None:
            GLib.source_remove(self._geometry_save_source)
        self._save_geometry()
        self._toolbar.set_content(None)
        self._on_dock_back(pane)

    # -- geometry persistence ----------------------------------------------------

    def _schedule_save_geometry(self, *_args) -> None:
        if self._geometry_save_source is not None:
            GLib.source_remove(self._geometry_save_source)
        self._geometry_save_source = GLib.timeout_add(600, self._save_geometry)

    def _save_geometry(self) -> bool:
        self._geometry_save_source = None
        values = {"editor_window_maximized": bool(self.is_maximized())}
        width, height = self.get_default_size()
        if not self.is_maximized() and width > 0 and height > 0:
            values["editor_window_width"] = width
            values["editor_window_height"] = height
        self.state.update_settings(values)
        return GLib.SOURCE_REMOVE
