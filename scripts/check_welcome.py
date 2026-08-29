#!/usr/bin/env python3
"""End-to-end check for the first-launch welcome dialog — dev machine.

A fresh install's first launch opens on the welcome (collins/welcome.py):
the CLI group in its found state, the Token use rows, and the MCP tool
switches, with one Continue button. Nothing that waits on it — the GitHub
notice, the expired-login repair — runs before it is answered; Continue
records ``welcome_seen``, runs them in that order, and the next launch
shows no dialog. And the rows are live: None picked for the session title
model in the dialog means a session that appears right after is not sent
to a model (the shim CLI sees no ``-p`` run), while the automatic default
put back sends the next one. None of it is reachable from pytest —
tests/conftest.py blocks the GTK stack, and the gate the unit suite holds
(tests/test_welcomegate.py) is a rule, not a dialog — so it is checked
here, against a real App:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_welcome.py

The launch is staged with an expired login on purpose: the case the repair
exists for, and the one the dialog must hold back. The app's own launch
check waits on the dialog by sequencing, but the sidebar's usage panel maps
under the open dialog, has its first fetch refused, and asks tokenrefresh
for the same repair — which must be refused too, or a throwaway ``claude
-p`` runs while the switch governing it is still being disclosed. So this
check keeps the real ``maybe_repair`` (wrapped, to see what it answered)
and leaves the usage fixture off: with a dead token the fetch fails on the
file, before any network. The relaunch is the positive control — the same
refusal, with the welcome answered, runs the repair (the shim logs a
``-p --model haiku``).

The relaunch is a real second process on the same scratch tree (this
script again, with ``--relaunch <dir>``). The model catalog is a canned
list patched over claudemodels, so nothing here reaches the network; the
CLI is a shim that logs every run and answers a ``-p`` with a title.

Run it behind the headless wrapper, or a window opens on the user's screen.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid

RELAUNCH = "--relaunch"
SECOND = RELAUNCH in sys.argv
E2E = sys.argv[sys.argv.index(RELAUNCH) + 1] if SECOND else tempfile.mkdtemp(prefix="collins-welcome-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum()) + ("b" if SECOND else "")

# Isolation first: every one of these is read at import time somewhere below.
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["COLLINS_CLAUDE_CREDENTIALS"] = f"{E2E}/credentials.json"  # expired: the repair's case
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"
os.environ["XDG_CACHE_HOME"] = f"{E2E}/cache"  # no saved model list from a real run
os.environ["LANG"] = "C"  # the check reads English titles

PROJECT = f"{E2E}/dev/alpha"
ENCODED = re.sub(r"[^A-Za-z0-9]", "-", PROJECT)
LOG = f"{E2E}/launches.log"  # one line per shim run: its argv
SHIM = f"{E2E}/bin/claude"
STATE_FILE = f"{E2E}/config/collins/state.json"

if not SECOND:
    # The project directory exists before launch, so the store's monitor is
    # on it when the transcripts below appear.
    for path in (f"{E2E}/chats", f"{E2E}/bin", f"{E2E}/projects/{ENCODED}", PROJECT):
        os.makedirs(path, exist_ok=True)
    with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
        fh.write("{}")
    # A login an hour past its expiry: the usage panel's fetch fails on the
    # file (no network), and asks for the repair the welcome must hold back.
    with open(f"{E2E}/credentials.json", "w", encoding="utf-8") as fh:
        json.dump(
            {"claudeAiOauth": {"accessToken": "tok-e2e", "expiresAt": (time.time() - 3600) * 1000}},
            fh,
        )
    # No state.json at all: a fresh install, which is the case under test.
    # The CLI the app finds: every run is logged; a `-p` run reads its prompt
    # and answers with a title, anything else draws an idle prompt.
    with open(SHIM, "w", encoding="utf-8") as fh:
        fh.write(
            "#!/usr/bin/env python3\n"
            "import sys, time\n"
            f"log = {LOG!r}\n"
            "with open(log, 'a') as fh:\n"
            "    fh.write(' '.join(sys.argv[1:]) + '\\n')\n"
            "if '-p' in sys.argv[1:]:\n"
            "    sys.stdin.read()\n"
            "    sys.stdout.write('Alpha widgets')\n"
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
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from collins import claudemodels, ghwelcome, i18n, titles, tokenrefresh, welcome  # noqa: E402
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
claudemodels.cache_fetched_at = lambda: 1.0
claudemodels.cache_failed = lambda: False

# The welcome-work that waits on the dialog, recorded rather than run: the
# GitHub notice would land over the window on a runner with gh signed out,
# and the launch check's order is the point, not its run. The app looks both
# up by attribute at call time, so patching the modules is enough.
AFTER: list[str] = []
ghwelcome.maybe_show = lambda *_a, **_k: AFTER.append("ghwelcome")
tokenrefresh.maybe_start = lambda *_a, **_k: AFTER.append("tokenrefresh")

# The mid-run entry stays real — it is the one the welcome doesn't sequence
# — wrapped to record what each ask got back: None for a refusal, a thread
# for a repair under way. The usage panel looks it up by attribute too.
REPAIRS: list[object] = []
_real_maybe_repair = tokenrefresh.maybe_repair


def _recording_maybe_repair(*args, **kwargs):
    result = _real_maybe_repair(*args, **kwargs)
    REPAIRS.append(result)
    return result


tokenrefresh.maybe_repair = _recording_maybe_repair


def headless_runs() -> list[str]:
    """Every `-p` argv the shim was started with, oldest first."""
    try:
        with open(LOG, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    return [line for line in lines if line.split()[:1] == ["-p"]]


# The headless argvs as the shim logs them (sys.argv[1:] space-joined, so
# the empty --tools value shows as two spaces): every run's prefix, and the
# throwaway repair run in full.
HEADLESS_PREFIX = " ".join(titles.headless_argv("claude", "")[1:])
REPAIR_RUN = " ".join(titles.headless_argv("claude", tokenrefresh._MODEL)[1:])


def repair_runs() -> list[str]:
    return [run for run in headless_runs() if run == REPAIR_RUN]


def usage_status() -> tuple[str, str]:
    """What the sidebar's usage panel shows: its stack page, and the status
    line's text — "Claude login expired — run claude to refresh" is the
    refused fetch that asked for the repair."""
    panel = state["win"].sidebar.usage_panel
    return panel._stack.get_visible_child_name(), panel._status.get_text()


def saved_settings() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh).get("settings") or {}
    except OSError:
        return {}


def write_transcript(prompt: str) -> str:
    """A new session appearing under the projects dir while the app runs —
    the thing the title model is asked about."""
    session_id = str(uuid.uuid4())
    line = {
        "type": "user",
        "cwd": PROJECT,
        "sessionId": session_id,
        "timestamp": "2026-08-27T10:00:00.000Z",
        "message": {"role": "user", "content": prompt},
    }
    with open(f"{E2E}/projects/{ENCODED}/{session_id}.jsonl", "w", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    return session_id


# -- reading the widget tree -------------------------------------------------


def walk(widget: Gtk.Widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from walk(child)
        child = child.get_next_sibling()


def find(widget: Gtk.Widget, pred) -> Gtk.Widget | None:
    return next((w for w in walk(widget) if pred(w)), None)


def rows(dialog, kind) -> list:
    return [w for w in walk(dialog) if isinstance(w, kind)]


def button(dialog, label: str) -> Gtk.Button | None:
    return find(dialog, lambda w: isinstance(w, Gtk.Button) and w.get_label() == label)


def later(fn, ms: int = 1000) -> bool:
    GLib.timeout_add(ms, fn)
    return GLib.SOURCE_REMOVE


i18n.init(AppState().get_setting("language"))
app = App()

exit_code = 1
tries = 0
state: dict = {}


# -- the steps ---------------------------------------------------------------


def stage() -> bool:
    """Wait for the window, then a beat for the dialog's present."""
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
    return later(step_relaunched if SECOND else step_opened, 1500)


def step_opened() -> bool:
    win = state["win"]
    dialog = win.get_visible_dialog()
    check(
        "a fresh install opens on the welcome dialog",
        isinstance(dialog, welcome.WelcomeDialog),
        type(dialog).__name__ if dialog else None,
    )
    if not isinstance(dialog, welcome.WelcomeDialog):
        return done()
    state["dialog"] = dialog
    check("it is headed Before you start", dialog.get_title() == "Before you start", dialog.get_title())
    check("nothing that waits on it has run", AFTER == [], AFTER)
    check("welcome_seen is not recorded yet", saved_settings().get("welcome_seen") is not True,
          saved_settings().get("welcome_seen"))
    groups = rows(dialog, Adw.PreferencesGroup)
    titles = [g.get_title() for g in groups]
    check(
        "three groups: the CLI, Token use, Built-in MCP tools",
        titles == ["Claude Code CLI", "Token use", "Built-in MCP tools"],
        titles,
    )
    using = find(
        dialog, lambda w: isinstance(w, Adw.ActionRow) and (w.get_title() or "").startswith("Using claude")
    )
    check(
        "the CLI group names the claude in use",
        using is not None and using.get_title() == f"Using claude at {SHIM}",
        using and using.get_title(),
    )
    check("no path entry in the found state", rows(dialog, Adw.EntryRow) == [])
    combos = {r.get_title(): r for r in rows(dialog, Adw.ComboRow)}
    check(
        "the two model pickers are there",
        set(combos) == {"Session title model", "Icon generation model"},
        set(combos),
    )
    switches = [r.get_title() for r in rows(dialog, Adw.SwitchRow)]
    check("the renew switch is there", "Auto-renew the Claude login" in switches, switches)
    check("and a switch per MCP tool", len(switches) == 9, switches)
    cont = button(dialog, "Continue")
    check("one Continue button, live", cont is not None and cont.get_sensitive())
    check("no Quit in the found state", button(dialog, "Quit") is None)
    check("no Use This CLI either", button(dialog, "Use This CLI") is None)
    state.update(cont=cont, title_combo=combos.get("Session title model"), waits=0)
    return later(step_panel_refused, 250)


def step_panel_refused() -> bool:
    """The usage panel, mapped under the dialog, has had its fetch refused
    (expired) and asked for the repair — bounded wait for the ask, which is
    a thread and an idle away from the map."""
    if not REPAIRS and state["waits"] < 20:
        state["waits"] += 1
        return later(step_panel_refused, 250)
    page, text = usage_status()
    check(
        "under the dialog, the usage panel's fetch is refused for the expired login",
        (page, text) == ("status", "Claude login expired — run claude to refresh"),
        (page, text),
    )
    check("the panel asked for a repair, and was refused", REPAIRS != [] and all(r is None for r in REPAIRS),
          REPAIRS)
    check("so no throwaway run while the dialog is up", headless_runs() == [], headless_runs())
    return later(step_pick_none, 250)


def step_pick_none() -> bool:
    combo = state["title_combo"]
    if combo is None:
        return done()
    model = combo.get_model()
    labels = [model.get_string(i) for i in range(model.get_n_items())]
    check("the title picker lists None first", labels[:1] == ["None"], labels)
    combo.set_selected(0)
    check(
        "picking None writes the setting at once",
        saved_settings().get("title_model") == "none",
        saved_settings(),
    )
    check("and still nothing has run", AFTER == [], AFTER)
    state["cont"].emit("clicked")
    return later(step_continued, 800)


def step_continued() -> bool:
    win = state["win"]
    check("Continue closes the dialog", win.get_visible_dialog() is None,
          type(win.get_visible_dialog()).__name__ if win.get_visible_dialog() else None)
    check("welcome_seen is recorded", saved_settings().get("welcome_seen") is True, saved_settings())
    check(
        "then the GitHub notice, then the login repair — in that order",
        AFTER == ["ghwelcome", "tokenrefresh"],
        AFTER,
    )
    check("no headless run so far", headless_runs() == [], headless_runs())
    # A session started right after, under None: the store sees it and asks
    # no model. Bounded wait — the monitor, the rescan and the title queue
    # all have their turn well inside it.
    write_transcript("Fix the flaky spinner animation on the dashboard")
    return later(step_untitled, 4000)


def step_untitled() -> bool:
    check("a session started under None is not sent to a model", headless_runs() == [], headless_runs())
    # The control: the automatic default put back, the next session is —
    # and so is the one None skipped, which is still untitled: every rescan
    # offers every unnamed session, and now the gate is open.
    app.state.set_setting("title_model", "")
    write_transcript("Add dark mode support to the settings page")
    state["waits"] = 0
    return later(step_titled, 1000)


def step_titled() -> bool:
    if len(headless_runs()) < 2 and state["waits"] < 12:
        state["waits"] += 1
        return later(step_titled, 500)
    runs = headless_runs()
    check("with a model back, both untitled sessions are titled", len(runs) == 2, runs)
    check("by trimmed -p runs", all(run.startswith(HEADLESS_PREFIX) for run in runs), runs)
    return done()


def step_relaunched() -> bool:
    win = state["win"]
    dialog = win.get_visible_dialog()
    check("the relaunch shows no welcome", not isinstance(dialog, welcome.WelcomeDialog),
          type(dialog).__name__ if dialog else None)
    check("welcome_seen is still recorded", saved_settings().get("welcome_seen") is True)
    check("the welcome-work ran at once", AFTER == ["ghwelcome", "tokenrefresh"], AFTER)
    state["waits"] = 0
    return later(step_repaired, 250)


def step_repaired() -> bool:
    """The positive control: the same refused fetch, with the welcome
    answered, runs the repair — so it was the dialog holding it back, not
    the harness. The shim can't renew anything, so the run just logs."""
    if repair_runs() == [] and state["waits"] < 32:  # the shim run is a subprocess away
        state["waits"] += 1
        return later(step_repaired, 250)
    check("the panel asked for the repair again", REPAIRS != [] and REPAIRS[-1] is not None, REPAIRS)
    check("and with the welcome answered, the throwaway run happened", len(repair_runs()) == 1,
          headless_runs())
    return done()


def done() -> bool:
    global exit_code
    print(f"\n{PASSED} passed, {FAILED} failed")
    exit_code = 0 if FAILED == 0 else 1
    app.quit()
    return GLib.SOURCE_REMOVE


GLib.timeout_add(250, stage)
app.run([])
if exit_code == 0 and not SECOND:
    # The relaunch: this script again, on the same scratch tree, as a second
    # process — the state file it reads is the one Continue wrote.
    print("\n=== relaunch ===", flush=True)
    result = subprocess.run([sys.executable, os.path.abspath(__file__), RELAUNCH, E2E])
    exit_code = result.returncode
sys.exit(exit_code)
