"""Capture one docs screenshot scene of Collins.

Usage: python3 capture-docs.py <repo-root> <output.png> --scene NAME
       [--settle-ms N] [--size WxH] [--set KEY=JSON ...]

Scenes: main-window, hero, quick-switcher, session-details, mcp-servers,
preferences, terminal-panel, new-chat, composer, pr-page, editor-panel,
attachments-panel.

Same isolation env contract as the skill's capture.py (a per-run COLLINS_APP_ID,
COLLINS_PROJECTS_DIR, COLLINS_CLAUDE_CONFIG, COLLINS_CHATS_DIR,
XDG_CONFIG_HOME, XDG_STATE_HOME, plus COLLINS_USAGE_FIXTURE, HOME=<dir> and
a claude shim on PATH from stage-docs-data.sh), and the same
scripts/with-headless-display.sh wrapper. refresh-docs-screenshots.sh runs
every scene with that env and drops the PNGs where the docs read them.

--size and --set edit the staged state.json's settings before the app starts
(the window's size comes from there, as do the panel widths), so one staged
tree serves every scene.
"""

import argparse
import json
import os
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("repo_root")
parser.add_argument("out_png")
parser.add_argument("--scene", required=True)
parser.add_argument("--settle-ms", type=int, default=3000)
parser.add_argument("--size", metavar="WxH", help="window size for this shot")
parser.add_argument("--set", action="append", default=[], metavar="KEY=JSON",
                    help="a settings key to write into the staged state.json")
args = parser.parse_args()

SCENES = (
    "main-window", "hero", "quick-switcher", "session-details", "mcp-servers",
    "preferences", "terminal-panel", "new-chat", "composer", "pr-page",
    "editor-panel", "attachments-panel",
)
if args.scene not in SCENES:
    parser.error(f"unknown scene {args.scene}")


def edit_settings() -> None:
    path = os.path.join(os.environ["XDG_CONFIG_HOME"], "collins", "state.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    settings = data.setdefault("settings", {})
    if args.size:
        w, h = args.size.lower().split("x")
        settings["window_width"], settings["window_height"] = int(w), int(h)
    for item in args.set:
        key, _, value = item.partition("=")
        settings[key] = json.loads(value)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


edit_settings()

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

from collins import dialogs, i18n, prdetail, trust  # noqa: E402
from collins.prstatus import PullRequest  # noqa: E402

U1 = "11111111-1111-4111-8111-111111111111"
# Every session the scene stages (stage-docs-data.sh): the shot waits for
# all of them, so a sidebar is never caught mid-scan with a project missing.
STAGED = tuple(f"{d * 8}-{d * 4}-4{d * 3}-8{d * 3}-{d * 12}" for d in "123456")
ALPHA = os.path.expanduser("~/dev/alpha-widgets")
PR_URL = "https://github.com/episode6/alpha-widgets/pull/214"

NEW_CHAT_TEXT = (
    "Add a reduced-motion variant of the spinner: respect prefers-reduced-motion, "
    "swap the rotation for a subtle opacity pulse, and cover both paths in the "
    "dashboard test suite.\n\nOpen a PR when the suite is green."
)
COMPOSER_TEXT = (
    "Add a reduced-motion variant of the spinner: respect prefers-reduced-motion, "
    "swap the rotation for a subtle opacity pulse, and cover both paths in the "
    "dashboard test suite.\n\nThen rerun the flake check 50x and paste the timing "
    "summary here."
)


def _iso(seconds_ago: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds_ago))


def staged_detail(_url: str) -> prdetail.PullRequestDetail:
    """The PR page's data, fabricated: nothing here reaches GitHub."""
    pr = PullRequest(
        number=214, url=PR_URL, repository="episode6/alpha-widgets",
        title="Fix the flaky spinner animation on the dashboard", state="OPEN",
        passed=3, failed=0, pending=0, mergeable="MERGEABLE",
    )
    body = (
        "The flake came from animating `width` during layout — every cycle "
        "invalidated the dashboard grid.\n\n"
        "- animate `transform: rotate()` instead of width\n"
        "- pin it with two regression tests in the dashboard suite"
    )
    checks = tuple(prdetail.PrCheck(name=n, state="passed", url="") for n in ("lint", "test", "e2e"))
    review = prdetail.PrComment(
        author="claude", created_at=_iso(2 * 3600),
        body="Reviewed — the transform-only animation removes the layout thrash, "
             "and the regression tests cover the flake. LGTM.",
        url="",
    )
    css_patch = (
        "@@ -5,7 +5,7 @@\n"
        "   height: 28px;\n"
        "   border-radius: 50%;\n"
        "   border: 3px solid var(--accent-dim);\n"
        "-  animation: grow 800ms linear infinite;\n"
        "+  animation: spin 800ms linear infinite;\n"
        " }\n"
        " \n"
        "-@keyframes grow {\n"
        "-  from { width: 28px; }\n"
        "-  to   { width: 34px; }\n"
        "+@keyframes spin {\n"
        "+  from { transform: rotate(0deg); }\n"
        "+  to   { transform: rotate(360deg); }\n"
        " }\n"
    )
    test_patch = (
        "@@ -0,0 +1,9 @@\n"
        "+from dashboard import spinner\n"
        "+\n"
        "+\n"
        "+def test_spinner_animates_transform_only():\n"
        "+    rules = spinner.animated_properties()\n"
        "+    assert rules == {\"transform\"}\n"
        "+\n"
        "+\n"
        "+def test_spinner_respects_reduced_motion():\n"
    )
    files = (
        prdetail.PrFile(path="src/dashboard/spinner.css", additions=12, deletions=9, patch=css_patch),
        prdetail.PrFile(path="tests/test_spinner.py", additions=36, deletions=8, patch=test_patch,
                        change_type="added"),
    )
    return prdetail.PullRequestDetail(
        summary=pr, body=body, author="ghackett", created_at=_iso(3 * 86400),
        base_ref="main", head_ref="fix/spinner-flake", base_oid="a" * 40, head_oid="b" * 40,
        head_repository="episode6/alpha-widgets", additions=48, deletions=17, changed_files=2,
        labels=("bug",), checks=checks, timeline=(review,), files=files, threads=(),
        viewer_is_author=True,
    )


if args.scene == "pr-page":
    prdetail.fetch = staged_detail

from collins.app import App  # noqa: E402
from collins.state import AppState  # noqa: E402

i18n.init(AppState().get_setting("language"))
if args.scene == "new-chat":
    trust.trust_dir(trust.trust_root(ALPHA))  # no trust dialog over the shot
app = App()
exit_code = 1
tries = 0


def open_tab(win, uuid: str):
    win.open_session(app.store.get_session(uuid))
    return win.tab_view.get_selected_page().get_child()


def selected_tab(win):
    return win.tab_view.get_selected_page().get_child()


def stage(win) -> list[tuple[int, callable]]:
    """Open what the scene needs now, and return the steps that follow —
    (delay-ms, callable) pairs run one after another before the shot. A
    tab needs a beat to settle before its panels can be asked for."""
    scene = args.scene
    if scene == "hero":
        open_tab(win, U1)
    elif scene == "quick-switcher":
        win._quick_switch()
        win._switcher._entry.set_text("exp")
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
    elif scene == "terminal-panel":
        open_tab(win, U1).show_panel()
    elif scene == "new-chat":
        win._start_new_session(ALPHA)
        selected_tab(win).seed_new_chat_text(NEW_CHAT_TEXT)
    elif scene == "composer":
        tab = open_tab(win, U1)
        tab._agent_is_running = lambda: True  # the gate on opening it by button
        return [(1500, tab.open_composer), (700, lambda: tab._composer.set_text(COMPOSER_TEXT))]
    elif scene == "pr-page":
        tab = open_tab(win, U1)
        return [(1500, lambda: tab.open_pr_page_url(PR_URL))]
    elif scene == "editor-panel":
        tab = open_tab(win, U1)
        return [
            (1500, tab.show_editor),
            (500, lambda: tab._editor.open_file(os.path.join(ALPHA, "src/dashboard/spinner.css"))),
            # The status row prints the file's raw path; the scratch HOME
            # reads as ~ everywhere else in the shot, so make it so here too.
            (800, lambda: tab._editor._status_path.set_text(
                tab._editor._status_path.get_text().replace(os.path.expanduser("~"), "~", 1))),
        ]
    elif scene == "attachments-panel":
        tab = open_tab(win, U1)
        return [(1500, lambda: tab.dock_attachments(focus=False))]
    return []


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


def run_steps(steps: list) -> None:
    if not steps:
        GLib.timeout_add(args.settle_ms, capture)
        return
    delay, fn = steps[0]

    def step() -> bool:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            print(f"scene step failed: {exc}", file=sys.stderr)
            app.quit()
            return GLib.SOURCE_REMOVE
        run_steps(steps[1:])
        return GLib.SOURCE_REMOVE

    GLib.timeout_add(delay, step)


def prepare() -> bool:
    global tries
    tries += 1
    win = app.get_active_window()
    ready = win is not None and all(app.store.get_session(u) for u in STAGED)
    if not ready:
        if tries > 40:
            print("timed out waiting for the window/sessions", file=sys.stderr)
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE
    try:
        steps = stage(win)
    except Exception as exc:  # noqa: BLE001
        print(f"scene staging failed: {exc}", file=sys.stderr)
        app.quit()
        return GLib.SOURCE_REMOVE
    run_steps(steps)
    return GLib.SOURCE_REMOVE


GLib.timeout_add(250, prepare)
app.run([])
sys.exit(exit_code)
