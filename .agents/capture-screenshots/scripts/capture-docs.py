"""Capture one docs screenshot scene of Collins.

Usage: python3 capture-docs.py <repo-root> <output.png> --scene NAME
       [--settle-ms N]

Scenes: main-window, sidebar-search, quick-switcher, tab-emoji,
session-details, mcp-servers, preferences, terminal-panel, hero.

Same isolation env contract as the skill's capture.py (COLLINS_APP_ID,
COLLINS_PROJECTS_DIR, COLLINS_CLAUDE_CONFIG, COLLINS_CHATS_DIR,
XDG_CONFIG_HOME, XDG_STATE_HOME, plus COLLINS_USAGE_FIXTURE and a claude shim
on PATH from stage-docs-data.sh).
"""

import argparse
import sys

parser = argparse.ArgumentParser()
parser.add_argument("repo_root")
parser.add_argument("out_png")
parser.add_argument("--scene", required=True)
parser.add_argument("--settle-ms", type=int, default=3000)
args = parser.parse_args()

sys.path.insert(0, args.repo_root)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Graphene, Gtk  # noqa: E402

import shutil  # noqa: E402

# Keep the typed command a plain `claude` (resolved via PATH -> the staged
# shim) instead of the shim's absolute scratch path.
_orig_which = shutil.which
shutil.which = lambda cmd, *a, **k: (
    "claude" if cmd == "claude" else _orig_which(cmd, *a, **k)
)

from collins import dialogs, i18n  # noqa: E402
from collins.app import App  # noqa: E402
from collins.state import AppState  # noqa: E402

U1 = "11111111-1111-4111-8111-111111111111"
U2 = "22222222-2222-4222-8222-222222222222"

# scene -> (sessions to wait for/open, panel?)
SCENE_SESSIONS = {
    "main-window": ([], False),
    "sidebar-search": ([], False),
    "quick-switcher": ([], False),
    "tab-emoji": ([U1, U2], False),
    "session-details": ([], False),
    "mcp-servers": ([], False),
    "preferences": ([], False),
    "terminal-panel": ([U1], True),
    "hero": ([U1], False),
}
if args.scene not in SCENE_SESSIONS:
    parser.error(f"unknown scene {args.scene}")

i18n.init(AppState().get_setting("language"))
app = App()
exit_code = 1
tries = 0


def stage(win) -> None:
    scene = args.scene
    uuids, panel = SCENE_SESSIONS[scene]
    for uuid in uuids:
        win.open_session(app.store.get_session(uuid))
    if panel:
        win.tab_view.get_selected_page().get_child().show_panel()
    if scene == "sidebar-search":
        win.sidebar.search_entry.set_text("dark")
    elif scene == "quick-switcher":
        win._quick_switch()
        win._switcher._entry.set_text("exp")
    elif scene == "tab-emoji":
        dialogs.emoji_dialog(win, "🚀", lambda _t: None)
    elif scene == "session-details":
        # The details dialog shows the raw cwd; swap in a presentable path
        # (matching the claude.json project key) for the screenshot.
        session = app.store.get_session(U1)
        object.__setattr__(session, "cwd", "/home/ghackett/dev/alpha-widgets")
        win.activate_action("win.session-details", GLib.Variant.new_string(U1))
    elif scene == "mcp-servers":
        dialogs.mcp_browser_dialog(win)
    elif scene == "preferences":
        win.activate_action("win.preferences")


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
        texture.save_to_png(args.out_png)
        print(f"saved {args.out_png} ({w}x{h})")
        exit_code = 0
    except Exception as exc:  # noqa: BLE001
        print(f"capture failed: {exc}", file=sys.stderr)
    app.quit()
    return GLib.SOURCE_REMOVE


def prepare() -> bool:
    global tries
    tries += 1
    win = app.get_active_window()
    uuids, _panel = SCENE_SESSIONS[args.scene]
    ready = win is not None and all(app.store.get_session(u) for u in uuids)
    # also wait for the store's first scan so the sidebar is populated
    ready = ready and len(app.store.sessions) > 0
    if not ready:
        if tries > 40:
            print("timed out waiting for the window/sessions", file=sys.stderr)
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE
    try:
        stage(win)
    except Exception as exc:  # noqa: BLE001
        print(f"scene staging failed: {exc}", file=sys.stderr)
        app.quit()
        return GLib.SOURCE_REMOVE
    GLib.timeout_add(args.settle_ms, capture)
    return GLib.SOURCE_REMOVE


GLib.timeout_add(250, prepare)
app.run([])
sys.exit(exit_code)
