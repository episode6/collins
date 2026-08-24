"""Launch Collins from a given source tree and save a PNG of its main window.

Usage: python3 capture.py <repo-root> <output.png> \
           [--open-session UUID] [--panel] [--settle-ms N]

Renders the window widget tree in-process via Gsk, so no compositor
screenshot permission is needed. The caller must set COLLINS_APP_ID — freshly
generated per run, never the user's real app id and never a fixed e2e one that
a concurrent agent session would share — plus the data-isolation env vars,
each pointing into a per-run scratch tree: COLLINS_PROJECTS_DIR,
COLLINS_CLAUDE_CONFIG, COLLINS_CHATS_DIR, XDG_CONFIG_HOME, XDG_STATE_HOME.
COLLINS_CHATS_DIR especially: without it this run reaps the user's live chat
working directories (see the skill).

Run this behind scripts/with-headless-display.sh, or the window opens on the
user's screen and takes focus for the whole settle period.

--open-session waits for the store to discover the given session, then opens
its tab (spawning a real shell that runs the provider CLI). --panel also
shows that tab's secondary terminal panel — e.g. to demo restored panel
history. --settle-ms tunes how long rendering may settle before the shot
(default 2500; raise it if frames come out half-populated).
"""

import argparse
import sys

parser = argparse.ArgumentParser()
parser.add_argument("repo_root")
parser.add_argument("out_png")
parser.add_argument("--open-session", metavar="UUID",
                    help="open this session's tab once the store finds it")
parser.add_argument("--panel", action="store_true",
                    help="also show the opened tab's secondary terminal panel")
parser.add_argument("--editor", action="store_true",
                    help="also show the opened tab's editor pane")
parser.add_argument("--new-session", metavar="DIR",
                    help="start a new session in DIR: the tab opens onto the "
                         "new-chat screen (the directory is trusted first)")
parser.add_argument("--new-session-text", metavar="TEXT", default="",
                    help="seed the new-chat screen's composer with TEXT")
parser.add_argument("--settle-ms", type=int, default=2500,
                    help="delay before the shot, for scan/paint to settle")
args = parser.parse_args()
if (args.panel or args.editor) and not (args.open_session or args.new_session):
    parser.error("--panel/--editor require --open-session or --new-session")
if args.open_session and args.new_session:
    parser.error("--open-session and --new-session are exclusive")

sys.path.insert(0, args.repo_root)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Graphene, Gtk  # noqa: E402

from collins import i18n, trust  # noqa: E402
from collins.app import App  # noqa: E402
from collins.state import AppState  # noqa: E402

i18n.init(AppState().get_setting("language"))
if args.new_session:
    trust.trust_dir(trust.trust_root(args.new_session))  # no trust dialog over the shot
app = App()
exit_code = 1
tries = 0


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
    """Poll until the window (and the requested session) exists, then stage
    the scene and schedule the shot."""
    global tries
    tries += 1
    win = app.get_active_window()
    session = app.store.get_session(args.open_session) if args.open_session else None
    if win is None or (args.open_session and session is None):
        if tries > 40:  # ~10s: the store scan should long since have landed
            print("timed out waiting for the window/session", file=sys.stderr)
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE
    if session is not None:
        win.open_session(session)
    elif args.new_session:
        win._start_new_session(args.new_session)
        if args.new_session_text:
            tab = win.tab_view.get_selected_page().get_child()
            tab._new_chat.set_text(args.new_session_text)
    if session is not None or args.new_session:
        if args.panel:
            win.tab_view.get_selected_page().get_child().show_panel()
        if args.editor:
            win.tab_view.get_selected_page().get_child().show_editor()
    GLib.timeout_add(args.settle_ms, capture)
    return GLib.SOURCE_REMOVE


GLib.timeout_add(250, prepare)
app.run([])
sys.exit(exit_code)
