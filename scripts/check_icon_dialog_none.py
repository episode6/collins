#!/usr/bin/env python3
"""End-to-end check for the Generate Icon dialog under a model of None — dev machine.

With the icon generation model set to None (the default), the dialog must
not run anything on open: it waits on a "pick a model" page with the
drop-down reading "Choose a model…" and a Generate button that stays
insensitive until a model is picked, and only the click starts a run — one
run, on the picked model, with the preference left alone. None of that is
reachable from pytest — tests/conftest.py blocks the GTK stack, and the
dialog is widgets and a worker thread around a real subprocess — so it is
checked here, against a real App:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_icon_dialog_none.py

The CLI is a shim: every run is logged, and a ``-p`` run answers with one
small SVG so the dialog's preview lands. The model catalog is a canned list
patched over claudemodels, so nothing here reaches the network.

Run it behind the headless wrapper, or a window opens on the user's screen.
"""

import json
import os
import sys
import tempfile

E2E = tempfile.mkdtemp(prefix="collins-icondialog-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

# Isolation first: every one of these is read at import time somewhere below.
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["COLLINS_USAGE_FIXTURE"] = f"{E2E}/usage-fixture.json"  # no usage poll, no token repair
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"
os.environ["XDG_CACHE_HOME"] = f"{E2E}/cache"  # no saved model list from a real run

CWD = f"{E2E}/work"
LOG = f"{E2E}/launches.log"  # one line per shim run: its argv
SHIM = f"{E2E}/bin/claude"
SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">'
    '<rect width="128" height="128" rx="24" fill="#3465a4"/>'
    '<circle cx="64" cy="64" r="32" fill="#ffffff"/></svg>'
)

for path in (f"{E2E}/chats", f"{E2E}/bin", f"{E2E}/projects", CWD, f"{E2E}/config/collins"):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
with open(f"{E2E}/usage-fixture.json", "w", encoding="utf-8") as fh:
    json.dump({"limits": [], "extra_usage": {"is_enabled": False}}, fh)
with open(f"{CWD}/README.md", "w", encoding="utf-8") as fh:
    fh.write("# A staged project\n")
# The scene: icons on None (the default, but said outright — the check is
# about this value), titles on None too so nothing else asks the shim for a
# `-p` run, no usage panel, and the first-launch welcome answered already.
with open(f"{E2E}/config/collins/state.json", "w", encoding="utf-8") as fh:
    fh.write(
        '{"settings": {"icon_model": "none", "title_model": "none", "show_usage_panel": false, '
        '"welcome_seen": true}}'
    )

# The CLI the dialog spawns. Every run is logged; a `-p` run reads its prompt
# and answers with the SVG above, anything else draws an idle prompt.
with open(SHIM, "w", encoding="utf-8") as fh:
    fh.write(
        "#!/usr/bin/env python3\n"
        "import sys, time\n"
        f"log, svg = {LOG!r}, {SVG!r}\n"
        "with open(log, 'a') as fh:\n"
        "    fh.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "if '-p' in sys.argv[1:]:\n"
        "    sys.stdin.read()\n"
        "    sys.stdout.write(svg)\n"
        "    sys.exit(0)\n"
        "sys.stdout.write('\\u276f  ')\n"
        "sys.stdout.flush()\n"
        "while True:\n"
        "    time.sleep(3600)\n"
    )
os.chmod(SHIM, 0o755)
os.environ["PATH"] = f"{E2E}/bin:{os.environ['PATH']}"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import GLib, Gtk  # noqa: E402

from collins import claudemodels, dialogs, i18n, icongen  # noqa: E402
from collins.app import App  # noqa: E402
from collins.state import AppState  # noqa: E402

PASSED = 0
FAILED = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  ok  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}  {detail}")


# -- the stubs ---------------------------------------------------------------

CATALOG = [
    claudemodels.ClaudeModel("claude-sonnet-5", "Sonnet 5", "2026-03-01"),
    claudemodels.ClaudeModel("claude-haiku-4-5", "Haiku 4.5", "2025-10-01"),
]
claudemodels.available_models = lambda: list(CATALOG)
claudemodels.refresh_models = lambda: list(CATALOG)
claudemodels.cached_models = lambda: list(CATALOG)


def headless_runs() -> list[str]:
    """Every `-p` argv the shim was started with, oldest first."""
    try:
        with open(LOG, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    return [line for line in lines if line.split()[:1] == ["-p"]]


# -- reading the widget tree -------------------------------------------------


def walk(widget: Gtk.Widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from walk(child)
        child = child.get_next_sibling()


def find(widget: Gtk.Widget, pred) -> Gtk.Widget | None:
    return next((w for w in walk(widget) if pred(w)), None)


def dropdown_labels(drop: Gtk.DropDown) -> list[str]:
    model = drop.get_model()
    return [model.get_string(i) for i in range(model.get_n_items())]


def later(fn, ms: int = 1500) -> bool:
    GLib.timeout_add(ms, fn)
    return GLib.SOURCE_REMOVE


check("the shim's SVG passes the generated-icon gate", icongen.extract_svg(SVG) is not None)

i18n.init(AppState().get_setting("language"))
app = App()

exit_code = 1
tries = 0
state: dict = {}


# -- the steps ---------------------------------------------------------------


def stage() -> bool:
    """Wait for the window, then open the dialog the sidebar's menu item would."""
    global tries
    tries += 1
    win = app.get_active_window()
    if win is None:
        if tries > 40:  # ~10s
            print("timed out waiting for the window", file=sys.stderr)
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE
    state["win"] = win
    dialogs.generate_icon_dialog(win, CWD, "work", on_saved=lambda: None)
    return later(step_opened)


def step_opened() -> bool:
    win = state["win"]
    stack = find(win, lambda w: isinstance(w, Gtk.Stack) and w.get_child_by_name("pick") is not None)
    drop = find(win, lambda w: isinstance(w, Gtk.DropDown))
    generate = find(win, lambda w: isinstance(w, Gtk.Button) and w.get_label() == "Generate")
    check(
        "the dialog opened on the pick page",
        stack is not None and stack.get_visible_child_name() == "pick",
        stack and stack.get_visible_child_name(),
    )
    check("nothing ran on open", headless_runs() == [], headless_runs())
    if stack is None or drop is None or generate is None:
        return done()
    state.update(stack=stack, drop=drop, generate=generate)
    labels = dropdown_labels(drop)
    check("item 0 reads Choose a model…", labels[:1] == ["Choose a model…"], labels)
    check("the catalog follows it", labels[1:] == [m.display_name for m in CATALOG], labels)
    check("Generate is insensitive until a pick", not generate.get_sensitive())
    check("Generate is what the button says", generate.get_label() == "Generate", generate.get_label())
    drop.set_selected(1)
    return later(step_picked, 300)


def step_picked() -> bool:
    generate = state["generate"]
    check("a pick makes Generate sensitive", generate.get_sensitive())
    check("a pick writes no preference", AppState().get_setting("icon_model") == "none",
          AppState().get_setting("icon_model"))
    check("still nothing ran", headless_runs() == [], headless_runs())
    state["drop"].set_selected(0)
    check("back on Choose a model… it sleeps again", not generate.get_sensitive())
    state["drop"].set_selected(1)
    generate.emit("clicked")
    return later(step_ran, 500)


def step_ran() -> bool:
    stack = state["stack"]
    # The shim answers at once, but the run is a thread and the preview an
    # idle: wait for it — bounded, like every wait here.
    if stack.get_visible_child_name() != "preview" and state.setdefault("waits", 0) < 40:
        state["waits"] += 1
        return later(step_ran, 250)
    runs = headless_runs()
    check("Generate ran exactly once", len(runs) == 1, runs)
    check("on the picked model", runs[:1] == [f"-p --model {CATALOG[0].id}"], runs)
    check("the preview landed", stack.get_visible_child_name() == "preview", stack.get_visible_child_name())
    check("the button is Regenerate from here on", state["generate"].get_label() == "Regenerate",
          state["generate"].get_label())
    check("the preference is still None", AppState().get_setting("icon_model") == "none",
          AppState().get_setting("icon_model"))
    return done()


def done() -> bool:
    global exit_code
    print(f"\n{PASSED} passed, {FAILED} failed")
    exit_code = 0 if FAILED == 0 else 1
    app.quit()
    return GLib.SOURCE_REMOVE


GLib.timeout_add(250, stage)
app.run([])
sys.exit(exit_code)
