#!/usr/bin/env python3
# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""End-to-end check for the Git preferences group — dev machine.

The git page's knobs sit in a Git group directly under Pull requests: hunk's
layout and theme, the untracked-files switch, the commits panel's page size,
and a default parent branch. The two free-text rows keep a name only when
it can stand as one (a half-typed or flag-shaped word wears the error style
and leaves the stored answer alone), and only once the typing settles —
Enter, leaving the row and closing the dialog save at once — since a theme
change restarts hunk on every open git page; the search bar finds the group
by the words people have for it. None of it is reachable from pytest —
tests/conftest.py blocks the GTK stack, and the layout promise the unit
suite holds (tests/test_prefslayout.py) is data, not widgets — so it is
checked here, against a real App:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_git_prefs.py

The CLI is a shim that draws an idle prompt; nothing here starts hunk or
reaches the network.

Run it behind the headless wrapper, or a window opens on the user's screen.
"""

import json
import os
import sys
import tempfile

E2E = tempfile.mkdtemp(prefix="collins-gitprefs-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

# Isolation first: every one of these is read at import time somewhere below.
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["COLLINS_USAGE_FIXTURE"] = f"{E2E}/usage-fixture.json"  # no usage poll, no token repair
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"
os.environ["XDG_CACHE_HOME"] = f"{E2E}/cache"
os.environ["LANG"] = "C"  # the check reads English titles

SHIM = f"{E2E}/bin/claude"

for path in (f"{E2E}/chats", f"{E2E}/bin", f"{E2E}/projects", f"{E2E}/config/collins"):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
with open(f"{E2E}/usage-fixture.json", "w", encoding="utf-8") as fh:
    json.dump({"limits": [], "extra_usage": {"is_enabled": False}}, fh)
# Titles on None so nothing asks the shim for a `-p` run; no usage panel;
# the first-launch welcome answered already; the git settings at their
# defaults, so the rows open on them.
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

from collins import i18n, prefslayout  # noqa: E402
from collins.app import App  # noqa: E402
from collins.prefs import PreferencesDialog  # noqa: E402
from collins.state import AppState  # noqa: E402

PASSED = 0
FAILED = 0

ROW_TITLES = ["Layout", "Theme", "Show untracked files", "Commits per page", "Default parent branch"]


def focus_chain(widget) -> list:
    """Why grab_focus might refuse: each ancestor's sensitivity, visibility
    and focusability, innermost first."""
    out = []
    w = widget
    while w is not None:
        out.append(
            (
                type(w).__name__,
                w.get_sensitive(),
                w.is_sensitive(),
                w.get_visible(),
                w.get_focusable(),
                w.get_can_focus(),
            )
        )
        w = w.get_parent()
    return out


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  ok  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}  {detail}")


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


def setting(key: str):
    # A fresh AppState reads the file back: what the dialog saved, not what
    # its own state object holds.
    return AppState().get_setting(key)


def settle() -> None:
    """Run what the main loop has ready (never the settle timer: that is
    waited for where the check is about it)."""
    context = GLib.MainContext.default()
    for _ in range(50):
        if not context.pending():
            break
        context.iteration(False)


def typed(row, text: str) -> None:
    """Type *text* and press Enter: the row keeps the word at once rather
    than after the settle delay."""
    row.set_text(text)
    settle()
    row.emit("entry-activated")
    settle()


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


def changes() -> int:
    return len(state.get("changes", []))


def step_layout() -> bool:
    dialog = state["dialog"]
    page = dialog._page
    headings = [g.get_title() or "" for g in page.groups]
    index = prefslayout.GROUPS.index("git")
    check(
        "the Git group sits right after Pull requests",
        headings[index - 1 : index + 1] == ["Pull requests", "Git"],
        headings,
    )
    git = page.groups[index]
    check(
        "the group says what it is for",
        (git.get_description() or "").startswith("The git page:"),
        git.get_description(),
    )
    # The two entries each carry a title-less reason row beneath them, the
    # way the CLI path row does.
    check("the group holds its five rows in order", [t for t in titles(git) if t] == ROW_TITLES, titles(git))
    check("each entry has a reason row under it", titles(git)[2] == "" and titles(git)[-1] == "", titles(git))
    layout, theme, _r1, untracked, log_page, parent, _r2 = git.rows
    model = layout.get_model()
    labels = [model.get_string(i) for i in range(model.get_n_items())]
    check("the Layout row lists hunk's three modes", labels == ["Automatic", "Split", "Stacked"], labels)
    check("and opens on Automatic (the default)", layout.get_selected() == 0, layout.get_selected())
    check(
        "the Theme box opens empty",
        isinstance(theme, Adw.EntryRow) and theme.get_text() == "",
        theme.get_text(),
    )
    check("untracked files open shown", isinstance(untracked, Adw.SwitchRow) and untracked.get_active())
    check("the page size opens on twenty", isinstance(log_page, Adw.SpinRow) and log_page.get_value() == 20)
    check(
        "the parent box opens empty",
        isinstance(parent, Adw.EntryRow) and parent.get_text() == "",
        parent.get_text(),
    )
    state.update(
        page=page, git=git, layout=layout, theme=theme, untracked=untracked, log_page=log_page, parent=parent
    )
    return later(step_writes, 200)


def step_writes() -> bool:
    layout, theme = state["layout"], state["theme"]

    layout.set_selected(1)
    check("picking Split writes hunk's mode word", setting("git_layout") == "split", setting("git_layout"))
    check("and calls on_change", changes() == 1, changes())

    # Typing alone saves nothing until it settles: "dr", "dra"… on the way
    # to a theme name must not each restart hunk.
    theme.set_text("dr")
    settle()
    theme.set_text("dra")
    settle()
    check("a keystroke alone saves nothing yet", setting("git_theme") == "", setting("git_theme"))
    check("and calls on_change no earlier", changes() == 1, changes())
    check("but the box already says the word can stand", not theme.has_css_class("error"))
    theme.set_text("bad name")
    settle()
    check("and when it can't, at once", theme.has_css_class("error"))
    theme.set_text("dracula")
    settle()
    return later(step_writes_settled, 900)


def step_writes_settled() -> bool:
    theme, untracked = state["theme"], state["untracked"]
    log_page, parent = state["log_page"], state["parent"]
    check("the name lands once the typing settles", setting("git_theme") == "dracula", setting("git_theme"))
    check("and calls on_change once", changes() == 2, changes())

    typed(theme, "nord")
    check("a theme name is kept on Enter", setting("git_theme") == "nord", setting("git_theme"))
    check("and calls on_change", changes() == 3, changes())
    typed(theme, " bad name")
    check("a name with a space in it is not", setting("git_theme") == "nord", setting("git_theme"))
    check("and wears the error style", theme.has_css_class("error"))
    check("without calling on_change", changes() == 3, changes())
    typed(theme, "-x")
    check("nor is a flag-shaped one", setting("git_theme") == "nord", setting("git_theme"))
    typed(theme, "")
    check("clearing the box is hunk's default again", setting("git_theme") == "", setting("git_theme"))
    check("and drops the error style", not theme.has_css_class("error"))
    check("and calls on_change", changes() == 4, changes())

    untracked.set_active(False)
    check("switching untracked files off writes the setting", setting("git_untracked") is False)
    check("and calls on_change", changes() == 5, changes())

    log_page.set_value(50)
    check("the page size writes an int", setting("git_log_page") == 50, repr(setting("git_log_page")))
    check("and calls on_change", changes() == 6, changes())

    typed(parent, "develop")
    check("a branch name is kept", setting("git_parent_branch") == "develop", setting("git_parent_branch"))
    check("and calls on_change", changes() == 7, changes())
    typed(parent, "-x")
    check("a flag-shaped one is not", setting("git_parent_branch") == "develop", setting("git_parent_branch"))
    check("and wears the error style", parent.has_css_class("error"))
    typed(parent, "main..develop")
    check("nor is a range", setting("git_parent_branch") == "develop", setting("git_parent_branch"))
    typed(parent, "release/v1")
    check("a slash is fine", setting("git_parent_branch") == "release/v1", setting("git_parent_branch"))
    check("and drops the error style", not parent.has_css_class("error"))
    # Kept as typed; gitinfo.parent_branch takes it as develop where origin
    # has one (tests/test_gitinfo.py holds that promise).
    typed(parent, "origin/develop")
    check(
        "and so is a remote's name",
        setting("git_parent_branch") == "origin/develop",
        setting("git_parent_branch"),
    )
    typed(parent, "")
    check(
        "clearing the box is automatic again",
        setting("git_parent_branch") == "",
        setting("git_parent_branch"),
    )
    check("no other setting moved", changes() == 10, changes())
    # Focus leaving the row saves at once, too.
    dialog = state["dialog"]
    root = parent.get_root()
    grabbed = parent.grab_focus()
    focus_in = (grabbed, dialog.get_focus(), root.get_focus() if root else None)
    parent.set_text("develop")
    settle()
    left = state["untracked"].grab_focus()
    settle()
    focus_out = (left, dialog.get_focus(), root.get_focus() if root else None)
    check(
        "leaving the row keeps the word without waiting",
        setting("git_parent_branch") == "develop",
        (
            setting("git_parent_branch"),
            "in", focus_in,
            "out", focus_out,
            "active", root.is_active() if root else None,
            "mapped", parent.get_mapped(),
            "chain", focus_chain(parent),
            "switch", focus_chain(state["untracked"]),
        ),
    )
    typed(parent, "")
    return later(step_search, 200)


def step_search() -> bool:
    dialog, page, git = state["dialog"], state["page"], state["git"]
    entry = dialog._search_entry
    for query, expected in (
        ("new files", ["Show untracked files"]),
        # The entry and the reason row under it show and hide together.
        ("trunk", ["Default parent branch", ""]),
        ("load more", ["Commits per page"]),
        # A theme name finds the terminal's palette row too.
        ("dracula", ["Color theme", "Theme", ""]),
        # The group's own words keep every row, by design.
        ("hunk", titles(git)),
        ("untracked", titles(git)),
        ("parent branch", titles(git)),
    ):
        entry.set_text(query)
        dialog._apply_filter()
        rows = visible_rows(page)
        check(f"searching {query!r} finds {expected}", rows == expected, rows)
    entry.set_text("")
    dialog._apply_filter()
    check("clearing the search shows everything again", len(visible_rows(page)) > 40, len(visible_rows(page)))
    check(
        "show_group opens on the group's title",
        dialog.show_group("git") and entry.get_text() == "Git",
        entry.get_text(),
    )
    dialog._apply_filter()
    rows = visible_rows(page)
    check("and the group's rows are all showing", all(t in rows for t in ROW_TITLES), rows)
    # Closing the dialog on a word still settling keeps it.
    state["theme"].set_text("gruvbox")
    settle()
    check("a settling word is not saved before the close", setting("git_theme") == "", setting("git_theme"))
    dialog.force_close()
    return later(step_closed, 300)


def step_closed() -> bool:
    check("closing the dialog keeps the word", setting("git_theme") == "gruvbox", setting("git_theme"))
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
