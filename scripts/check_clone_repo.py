#!/usr/bin/env python3
# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""End-to-end check for Add project → Clone repository.

The sidebar's Add project button is a menu: *Open folder…* (the old folder
picker) and *Clone repository…*, which opens clonedialog.CloneDialog. Its one
box filters the repositories gh lists for the account, or takes a clone
address; the target directory starts at the clone_directory setting; the
destination is printed in full with a line saying whether git will accept
it. A clone lands in the sidebar as a project the way a picked folder does.

`gh` is a shim: `auth token` succeeds, `api user/repos…` serves three
repositories, and `repo clone <spec> <dest>` logs its arguments and clones a
local repository. The address path runs the real `git clone` against the
same local repository over file://.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_clone_repo.py

Run it behind the headless wrapper, or a window opens on the user's screen.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

E2E = tempfile.mkdtemp(prefix="collins-clone-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

# Isolation first: every one of these is read at import time somewhere below.
os.environ["COLLINS_DEBUG_API"] = "1"  # the e2e probe (debug.*): served only with this set
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["COLLINS_USAGE_FIXTURE"] = f"{E2E}/usage-fixture.json"  # no usage poll, no token repair
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"
os.environ["XDG_CACHE_HOME"] = f"{E2E}/cache"
os.environ["GH_LOG"] = f"{E2E}/gh.log"
os.environ["LANG"] = "C"  # the check reads English text

SOURCE = f"{E2E}/src/widget"  # the repository every clone copies
CODE = f"{E2E}/code"  # the clone_directory setting
OTHER = f"{E2E}/other"
GH_LOG = os.environ["GH_LOG"]

for path in (f"{E2E}/projects", f"{E2E}/chats", f"{E2E}/bin", f"{E2E}/config/collins", CODE):
    os.makedirs(path, exist_ok=True)
# Everything under the scratch root is trusted already, so a finished clone
# goes straight into the sidebar (the trust dialog is Open folder's too, and
# checked elsewhere).
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    json.dump({"projects": {E2E: {"hasTrustDialogAccepted": True}}}, fh)
with open(f"{E2E}/usage-fixture.json", "w", encoding="utf-8") as fh:
    json.dump({"limits": [], "extra_usage": {"is_enabled": False}}, fh)
with open(f"{E2E}/bin/claude", "w", encoding="utf-8") as fh:
    fh.write("#!/bin/sh\nexit 0\n")

_GIT = ["git", "-c", "user.name=e2e", "-c", "user.email=e2e@example.invalid"]
subprocess.run(["git", "init", "-q", SOURCE], check=True)
with open(f"{SOURCE}/README", "w", encoding="utf-8") as fh:
    fh.write("widget\n")
subprocess.run([*_GIT, "-C", SOURCE, "add", "README"], check=True)
subprocess.run([*_GIT, "-C", SOURCE, "commit", "-q", "-m", "init"], check=True)

REPOS = [
    {"full_name": "episode6/collins", "description": "Agent-first IDE", "private": False},
    {"full_name": "episode6/widget", "description": "A widget", "private": True},
    {"full_name": "ghackett/dots", "description": None, "archived": True},
]
_GH = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a", encoding="utf-8") as fh:
    fh.write(" ".join(args) + "\n")
if args[:2] == ["auth", "token"]:
    print("gho_e2e")
    sys.exit(0)
if args[:1] == ["api"] and args[1].startswith("user/repos"):
    print(json.dumps(REPOS if "&page=1&" in args[1] else []))
    sys.exit(0)
if args[:2] == ["repo", "clone"]:
    sys.exit(subprocess.call(["git", "clone", "-q", "--", SOURCE, args[3]]))
sys.exit(1)
'''.replace("REPOS", repr(REPOS)).replace("SOURCE", repr(SOURCE))
with open(f"{E2E}/bin/gh", "w", encoding="utf-8") as fh:
    fh.write(_GH)
for name in ("gh", "claude"):
    os.chmod(f"{E2E}/bin/{name}", 0o755)
os.environ["PATH"] = f"{E2E}/bin:{os.environ['PATH']}"

with open(f"{E2E}/config/collins/state.json", "w", encoding="utf-8") as fh:
    json.dump(
        {
            "settings": {
                "title_model": "none",
                "show_usage_panel": False,
                "welcome_seen": True,
                "gh_welcome_dismissed": True,
                "archive_on_claude_ai": False,
                "restore_last_session": False,
                "refresh_prs_on_launch": False,
                "check_for_updates": False,
                "clone_directory": CODE,
            },
        },
        fh,
    )

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import GLib, Gtk  # noqa: E402

from collins import clonedialog, i18n, prefslayout  # noqa: E402
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


def settle() -> None:
    context = GLib.MainContext.default()
    for _ in range(50):
        if not context.pending():
            break
        context.iteration(False)


def poll(predicate, then, timeout_ms: int = 15000, what: str = "") -> None:
    """Run *then* once *predicate* holds, checking every 100 ms."""
    waited = [0]

    def tick() -> bool:
        if predicate():
            then()
            return GLib.SOURCE_REMOVE
        waited[0] += 100
        if waited[0] >= timeout_ms:
            check(f"waited for {what}", False, "timed out")
            done()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    GLib.timeout_add(100, tick)


def rows(dialog) -> list[str]:
    out = []
    index = 0
    while (row := dialog._list.get_row_at_index(index)) is not None:
        out.append(row.repo.full_name)
        index += 1
    return out


def selected(dialog) -> str | None:
    row = dialog._list.get_selected_row()
    return row.repo.full_name if row is not None else None


def type_into(dialog, text: str) -> None:
    dialog._entry.set_text(text)
    dialog._refilter()  # past the search entry's own typing delay
    settle()


i18n.init(AppState().get_setting("language"))
app = App()
exit_code = 1
tries = 0
state: dict = {}


def stage() -> bool:
    global tries
    tries += 1
    win = app.get_active_window()
    if win is None:
        if tries > 40:
            print("timed out waiting for the window", file=sys.stderr)
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE
    other = win.get_visible_dialog()
    if other is not None:
        print(f"closing a dialog presented at launch: {other}", file=sys.stderr)
        other.force_close()
    state["win"] = win

    button = win.sidebar._add_project_btn
    check("Add project is a menu button", isinstance(button, Gtk.MenuButton), type(button).__name__)
    model = button.get_menu_model()
    items = [
        (
            model.get_item_attribute_value(i, "label", GLib.VariantType("s")).get_string(),
            model.get_item_attribute_value(i, "action", GLib.VariantType("s")).get_string(),
        )
        for i in range(model.get_n_items())
    ]
    check(
        "its menu offers Open folder and Clone repository",
        items == [("Open folder…", "win.add-project"), ("Clone repository…", "win.clone-project")],
        items,
    )
    check("both actions exist", all(win.lookup_action(a.split(".")[1]) for _l, a in items))

    check("the dialog opens from its action", win.activate_action("win.clone-project", None))
    settle()
    dialog = win.get_visible_dialog()
    check("…as the clone dialog", isinstance(dialog, clonedialog.CloneDialog), dialog)
    if not isinstance(dialog, clonedialog.CloneDialog):
        return done()
    state["dialog"] = dialog
    check("Clone into starts at the setting", dialog._target.get_text() == CODE, dialog._target.get_text())
    check("nothing picked, nothing to clone", not dialog._clone_btn.get_sensitive())
    check(
        "the destination shows the placeholder under the target",
        dialog._dest_label.get_text() == f"{CODE}/<repository>",
        dialog._dest_label.get_text(),
    )
    poll(lambda: not dialog._loading, step_filter, what="the repository list")
    return GLib.SOURCE_REMOVE


def step_filter() -> None:
    dialog = state["dialog"]
    check(
        "gh's list fills the dialog, newest push first",
        rows(dialog) == ["episode6/collins", "episode6/widget", "ghackett/dots"],
        rows(dialog),
    )
    check("an empty box selects nothing", selected(dialog) is None, selected(dialog))
    with open(GH_LOG, encoding="utf-8") as fh:
        calls = fh.read()
    check(
        "the list asks for owned, collaborator and organization repositories",
        "affiliation=owner,collaborator,organization_member" in calls,
        calls,
    )

    type_into(dialog, "wid")
    check("typing filters the list", rows(dialog) == ["episode6/widget"], rows(dialog))
    check("and aims at the best match", selected(dialog) == "episode6/widget", selected(dialog))
    check(
        "the destination is the target plus the repository's folder",
        dialog._dest_label.get_text() == f"{CODE}/widget",
        dialog._dest_label.get_text(),
    )
    check(
        "the note names the source and the tool",
        "episode6/widget" in dialog._dest_note.get_text() and "gh" in dialog._dest_note.get_text(),
        dialog._dest_note.get_text(),
    )
    check("Clone is live", dialog._clone_btn.get_sensitive())

    # Something already there: git would refuse, so Clone is off.
    os.makedirs(f"{CODE}/dots")
    with open(f"{CODE}/dots/file", "w", encoding="utf-8") as fh:
        fh.write("x")
    type_into(dialog, "dots")
    check("a taken destination turns Clone off", not dialog._clone_btn.get_sensitive())
    check(
        "and says why, in red",
        "already there" in dialog._dest_note.get_text() and dialog._dest_note.has_css_class("error"),
        dialog._dest_note.get_text(),
    )

    type_into(dialog, "episode6/nothere")
    check("an owner/repo off the list selects nothing", selected(dialog) is None)
    check(
        "…but is cloned as typed",
        dialog._dest_label.get_text() == f"{CODE}/nothere" and dialog._clone_btn.get_sensitive(),
        dialog._dest_label.get_text(),
    )
    placeholder = dialog._placeholder.get_text()
    check("the list says so", "cloned from GitHub" in placeholder, placeholder)
    check("in place of the list", dialog._list_stack.get_visible_child_name() == "empty")

    type_into(dialog, f"file://{SOURCE}")
    check("an address empties the list", rows(dialog) == [], rows(dialog))
    check(
        "and names its own folder",
        dialog._dest_label.get_text() == f"{CODE}/widget",
        dialog._dest_label.get_text(),
    )
    check("with git", "with git" in dialog._dest_note.get_text(), dialog._dest_note.get_text())

    dialog._target.set_text("relative/dir")
    settle()
    check("a relative target turns Clone off", not dialog._clone_btn.get_sensitive())
    note = dialog._dest_note.get_text()
    check("and asks for a full path", "full path" in note, note)
    dialog._target.set_text(f"{E2E}/new/deeper")
    settle()
    check(
        "a missing target is created along the way",
        "will be created" in dialog._dest_note.get_text() and dialog._clone_btn.get_sensitive(),
        dialog._dest_note.get_text(),
    )

    # The gh path: a row from the list, into the setting's folder.
    dialog._target.set_text(CODE)
    type_into(dialog, "wid")
    dialog._start_clone()
    settle()
    check("cloning locks the box", not dialog._entry.get_sensitive())
    win = state["win"]
    poll(
        lambda: dialog._closed and "widget" in win.state.get_virtual_projects(),
        step_cloned,
        what="the gh clone to land as a project",
    )


def step_cloned() -> None:
    win = state["win"]
    with open(GH_LOG, encoding="utf-8") as fh:
        calls = fh.read()
    check(
        "a listed repository clones through gh repo clone",
        f"repo clone episode6/widget {CODE}/widget" in calls,
        calls,
    )
    check("the checkout is there", os.path.isfile(f"{CODE}/widget/README"))
    check(
        "the sidebar holds it as a project at its path",
        win.state.get_virtual_projects().get("widget") == f"{CODE}/widget",
        win.state.get_virtual_projects(),
    )

    # A second open: the cached list shows at once; an address clones
    # with plain git.
    win.activate_action("win.clone-project", None)
    settle()
    dialog = win.get_visible_dialog()
    state["dialog"] = dialog
    check("a reopened dialog shows the cached list at once", len(rows(dialog)) == 3, rows(dialog))
    dialog._target.set_text(OTHER)
    type_into(dialog, "file:///nonexistent/e2e/repo.git")
    dialog._start_clone()
    poll(lambda: dialog._error.get_visible(), step_failed, what="a failed clone to report")


def step_failed() -> None:
    dialog = state["dialog"]
    check("a failed clone keeps the dialog open", not dialog._closed)
    check("and prints git's complaint", "repo.git" in dialog._error.get_text(), dialog._error.get_text())
    check("the box is usable again", dialog._entry.get_sensitive() and dialog._clone_btn.get_sensitive())
    check("nothing was left behind", not os.path.exists(f"{OTHER}/repo"))

    type_into(dialog, f"file://{SOURCE}/.git")
    dialog._start_clone()
    win = state["win"]
    poll(
        lambda: dialog._closed and f"{OTHER}/widget" in win.state.get_virtual_projects().values(),
        step_address,
        what="the git clone to land as a project",
    )


def step_address() -> None:
    check("an address clones with git into the dialog's target", os.path.isfile(f"{OTHER}/widget/README"))
    check("the setting was not changed by the dialog", AppState().get_setting("clone_directory") == CODE)
    step_preferences()


def step_preferences() -> None:
    """Preferences → General → Clone repositories into: the path in the
    subtitle, a reset back to home once one was picked, found by search."""
    win = state["win"]
    dialog = PreferencesDialog(win.state, lambda: None)
    dialog.present(win)
    settle()
    general = dialog._page.groups[prefslayout.GROUPS.index("general")]
    row = next((r for r in general.rows if r.get_title() == "Clone repositories into"), None)
    check("General holds the clone folder row", row is not None, [r.get_title() for r in general.rows])
    if row is None:
        dialog.force_close()
        done()
        return
    check("its subtitle names the folder", CODE in row.get_subtitle(), row.get_subtitle())
    check("a picked folder offers the reset", dialog._clone_dir_reset.get_visible())
    dialog._clone_dir_reset.emit("clicked")
    settle()
    check("reset writes the home folder", AppState().get_setting("clone_directory") == "~")
    check("and says so", "starts in ~" in row.get_subtitle(), row.get_subtitle())
    check("with the reset gone", not dialog._clone_dir_reset.get_visible())
    dialog._search_entry.set_text("clone")
    dialog._apply_filter()
    visible = [
        r.get_title() for g in dialog._page.groups if g.get_visible() for r in g.rows if r.get_visible()
    ]
    check("search finds it by 'clone'", "Clone repositories into" in visible, visible)
    dialog.force_close()
    done()


def done() -> bool:
    global exit_code
    print(f"\n{PASSED} passed, {FAILED} failed")
    exit_code = 0 if FAILED == 0 else 1
    app.quit()
    return GLib.SOURCE_REMOVE


def deadline() -> bool:
    print("check_clone_repo: deadline hit", file=sys.stderr)
    shutil.rmtree(E2E, ignore_errors=True)
    os._exit(1)


GLib.timeout_add(250, stage)
GLib.timeout_add(100_000, deadline)
try:
    app.run([])
finally:
    shutil.rmtree(E2E, ignore_errors=True)
sys.exit(exit_code)
