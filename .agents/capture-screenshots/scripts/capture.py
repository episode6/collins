"""Launch Collins from a given source tree and save a PNG of its main window.

Usage: python3 capture.py <repo-root> <output.png>

Renders the window widget tree in-process via Gsk, so no compositor
screenshot permission is needed. The caller must set COLLINS_APP_ID (never
run this against the user's real app id) plus the data-isolation env vars:
COLLINS_PROJECTS_DIR, COLLINS_CLAUDE_CONFIG, XDG_CONFIG_HOME.
"""

import sys

sys.path.insert(0, sys.argv[1])
out_png = sys.argv[2]

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Graphene, Gtk  # noqa: E402

from collins import i18n  # noqa: E402
from collins.app import App  # noqa: E402
from collins.state import AppState  # noqa: E402

i18n.init(AppState().get_setting("language"))
app = App()
exit_code = 1


def capture() -> bool:
    global exit_code
    win = app.get_active_window()
    try:
        w, h = win.get_width(), win.get_height()
        paintable = Gtk.WidgetPaintable.new(win)
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, w, h)
        node = snapshot.to_node()
        renderer = win.get_native().get_renderer()
        texture = renderer.render_texture(node, Graphene.Rect().init(0, 0, w, h))
        texture.save_to_png(out_png)
        print(f"saved {out_png} ({w}x{h})")
        exit_code = 0
    except Exception as exc:  # noqa: BLE001
        print(f"capture failed: {exc}", file=sys.stderr)
    app.quit()
    return GLib.SOURCE_REMOVE


# Give the store's initial scan + first paint a moment to settle.
GLib.timeout_add(2500, capture)
app.run([])
sys.exit(exit_code)
