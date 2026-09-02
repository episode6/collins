#!/usr/bin/env python3
"""End-to-end check for the Token use preferences group — dev machine.

The settings that spend the user's Claude quota sit together in a Token use
group directly under General: the two model pickers, the login-renew
switch, and the Model list row (which says it is free). The Built-in MCP
tools group follows it, up from the bottom of the page. The rows are built
by tokensettings and only wrapped for search by prefs, so this checks that
the page really is laid out that way, that the search bar still finds every
moved row, and that the renew switch writes its setting. None of it is
reachable from pytest — tests/conftest.py blocks the GTK stack, and the
layout promise the unit suite holds (tests/test_prefslayout.py) is data,
not widgets — so it is checked here, against a real App:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_token_use_prefs.py

The model catalog is a canned list patched over claudemodels, so nothing
here reaches the network; the CLI is a shim that draws an idle prompt.

Run it behind the headless wrapper, or a window opens on the user's screen.
"""

import json
import os
import sys
import tempfile

E2E = tempfile.mkdtemp(prefix="collins-tokenprefs-")
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
os.environ["LANG"] = "C"  # the check reads English titles

SHIM = f"{E2E}/bin/claude"

for path in (f"{E2E}/chats", f"{E2E}/bin", f"{E2E}/projects", f"{E2E}/config/collins"):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
with open(f"{E2E}/usage-fixture.json", "w", encoding="utf-8") as fh:
    json.dump({"limits": [], "extra_usage": {"is_enabled": False}}, fh)
# Titles on None so nothing asks the shim for a `-p` run; no usage panel;
# the first-launch welcome answered already.
with open(f"{E2E}/config/collins/state.json", "w", encoding="utf-8") as fh:
    fh.write(
        '{"settings": {"title_model": "none", "show_usage_panel": false, "language": "", '
        '"welcome_seen": true}}'
    )

with open(SHIM, "w", encoding="utf-8") as fh:
    fh.write(
        "#!/usr/bin/env python3\n"
        "import sys, time\n"
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
from gi.repository import Adw, GLib  # noqa: E402

from collins import claudemodels, i18n, mcptools, prefslayout  # noqa: E402
from collins.app import App  # noqa: E402
from collins.prefs import PreferencesDialog  # noqa: E402
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


def later(fn, ms: int = 1000) -> bool:
    GLib.timeout_add(ms, fn)
    return GLib.SOURCE_REMOVE


def titles(group) -> list[str]:
    return [row.get_title() for row in group.rows]


def visible_rows(page) -> list[str]:
    return [
        row.get_title()
        for group in page.groups
        if group.get_visible()
        for row in group.rows
        if row.get_visible()
    ]


# The page's groups, by prefslayout name, as their headings read.
HEADINGS = {
    "cli": "",
    "general": "General",
    "token_use": "Token use",
    "mcp_tools": "Built-in MCP tools",
    "sessions": "Session behavior",
    "notifications": "Notifications",
    "composer": "Composer",
    "terminal": "Terminal",
    "footer_apps": "Footer apps",
    "pull_requests": "Pull requests",
    "git": "Git",
    "caffeine": "Caffeine Mode",
    "editor": "Editor",
}

i18n.init(AppState().get_setting("language"))
app = App()

exit_code = 1
tries = 0
state: dict = {}


# -- the steps ---------------------------------------------------------------


def stage() -> bool:
    """Wait for the window, then open preferences the menu item would."""
    global tries
    tries += 1
    win = app.get_active_window()
    if win is None:
        if tries > 40:  # ~10s
            print("timed out waiting for the window", file=sys.stderr)
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE
    dialog = PreferencesDialog(win.state, lambda: state.setdefault("changes", []).append(1))
    dialog.present(win)
    state.update(win=win, dialog=dialog)
    return later(step_layout)


def step_layout() -> bool:
    dialog = state["dialog"]
    page = dialog._page
    headings = [g.get_title() or "" for g in page.groups]
    check(
        "the groups come in prefslayout's order",
        headings == [HEADINGS[name] for name in prefslayout.GROUPS],
        headings,
    )
    check("Token use is directly under General", headings[1:3] == ["General", "Token use"], headings)
    check("Built-in MCP tools follows it", headings[3:4] == ["Built-in MCP tools"], headings)
    token = page.groups[2]
    check(
        "the Token use group holds its four rows in order",
        titles(token)
        == ["Session title model", "Icon generation model", "Auto-renew the Claude login", "Model list"],
        titles(token),
    )
    check(
        "the group says what it is for",
        (token.get_description() or "").startswith("Each of these runs Claude on your behalf"),
        token.get_description(),
    )
    check("the model rows left General", "Icon generation model" not in titles(page.groups[1]))
    check("the title picker left Session behavior", "Session title model" not in titles(page.groups[4]))
    mcp = page.groups[3]
    check(
        "the MCP group's description carries the disclosure",
        "read_terminal" in (mcp.get_description() or ""),
        mcp.get_description(),
    )
    check("the MCP group still lists every tool", len(mcp.rows) == len(mcptools.TOOLS), len(mcp.rows))
    renew = token.rows[2]
    status = token.rows[3]
    state.update(page=page, renew=renew, status=status, pickers=token.rows[:2])
    check("the renew switch opens on (the default)", isinstance(renew, Adw.SwitchRow) and renew.get_active())
    check(
        "the Model list row says it is free",
        (status.get_subtitle() or "").endswith("free, no tokens"),
        status.get_subtitle(),
    )
    return later(step_catalog)


def step_catalog() -> bool:
    # The canned catalog lands on the pickers off a worker: None, the
    # default, then the two models.
    for row in state["pickers"]:
        model = row.get_model()
        labels = [model.get_string(i) for i in range(model.get_n_items())]
        check(
            f"{row.get_title()} lists None, the default, then the catalog",
            labels[:1] == ["None"]
            and labels[1].startswith("Default")
            and labels[2:] == ["Sonnet 5", "Haiku 4.5"],
            labels,
        )
    check(
        "the Model list row dates the catalog and stays free",
        "2 models" in (state["status"].get_subtitle() or "")
        and (state["status"].get_subtitle() or "").endswith("free, no tokens"),
        state["status"].get_subtitle(),
    )
    # The switch writes its setting and tells the window.
    state["renew"].set_active(False)
    check("switching renew off writes the setting", AppState().get_setting("auto_renew_login") is False)
    check("and calls on_change", len(state.get("changes", [])) == 1, state.get("changes"))
    state["renew"].set_active(True)
    check("switching it back writes the setting", AppState().get_setting("auto_renew_login") is True)
    return later(step_search, 200)


def step_search() -> bool:
    dialog, page = state["dialog"], state["page"]
    entry = dialog._search_entry
    for query, expected in (
        ("oauth", ["Auto-renew the Claude login"]),
        ("auto-generate", ["Session title model"]),
        ("models api", ["Model list"]),
        (
            "quota",
            ["Session title model", "Icon generation model", "Auto-renew the Claude login", "Model list"],
        ),
        # Not read_terminal: the group's description names it, and a group
        # whose own text matches keeps every row, by design.
        ("run_in_terminal", ["Run commands in the terminal panel"]),
    ):
        entry.set_text(query)
        dialog._apply_filter()
        rows = visible_rows(page)
        check(f"searching {query!r} finds {expected}", rows == expected, rows)
    entry.set_text("")
    dialog._apply_filter()
    check("clearing the search shows everything again", len(visible_rows(page)) > 40, len(visible_rows(page)))
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
