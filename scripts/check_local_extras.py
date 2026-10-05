#!/usr/bin/env python3
"""End-to-end check for the local extras' gate (split-service spec §3.12,
PR-2.8), the Markdown export over the API and where a spawn lands (D39).

A window on the service's machine has proved it (`local`, D11) and keeps
the things that hand a path of the service's to something on this device:
Open in Ghostty, "Open In…" and Reveal transcript in the sidebar's menus,
the footer's file manager button, app launchers and the panel toggle's
right-click, the file rows' "Open In…", the attachments panel's Open
With… and Show in Folder, a clicked folder in the terminal. A window
that is not local has none of them: **hidden, not greyed out**, and the
actions behind them do nothing.

The check runs one `App()` against a service of its own, reads every one
of those surfaces while the link is `local`, then takes the proof away
(`link.local = False`: what a client over ssh is, which no transport can
produce before Phase 3) and reads them again. What is not a local extra
is asserted to survive: New session here, Details, the Markdown export
(asked of the service both times: `store.transcript-export`), Copy Path.

Then D39: a session whose project directory is gone is opened; the tab
passes the cwd it has, the service's `spawn` falls back to its home and
says so, and the tab's editor and its panel shell land there.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_local_extras.py

Nothing reaches the desktop: `Gtk.FileLauncher` and `Gtk.UriLauncher` are
replaced by a recorder for the whole run, the only programs launched are
stubs in the scratch tree (a `ghostty`, a footer app, the `claude` shim),
and the service runs with a scratch `$HOME`.
"""

import json
import os
import re
import shutil
import sys
import tempfile
import uuid

E2E = tempfile.mkdtemp(prefix="collins-localx-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

# Isolation first: every one of these is read at import time somewhere below.
os.environ["COLLINS_DEBUG_API"] = "1"  # the e2e probe (debug.*): served only with this set
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"
os.environ["XDG_CACHE_HOME"] = f"{E2E}/cache"
os.environ["XDG_DATA_HOME"] = f"{E2E}/data"  # where the footer app's desktop entry is staged
# Mesa's shader cache stays where it was: a scratch cache home would empty
# it and slow the first frame (check_pr_body_blocks.py found this).
os.environ.setdefault(
    "MESA_SHADER_CACHE_DIR", os.path.join(os.path.expanduser("~"), ".cache", "mesa_shader_cache")
)

PROJECT = f"{E2E}/dev/alpha"
GONE = f"{E2E}/dev/gone"  # never made: the D39 session's cwd
SERVICE_HOME = f"{E2E}/home"
SHIM = f"{E2E}/bin/claude"
LAUNCHED = f"{E2E}/launched.log"  # what the stub programs were started with
APP_ID = "collins-e2e-opener.desktop"

for path in (
    f"{E2E}/projects", f"{E2E}/chats", f"{E2E}/bin", PROJECT, SERVICE_HOME, f"{E2E}/data/applications",
    f"{E2E}/cache",
):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
with open(f"{PROJECT}/README.md", "w", encoding="utf-8") as fh:
    fh.write("# alpha\n")
with open(f"{PROJECT}/notes.pdf", "wb") as fh:
    fh.write(b"%PDF-1.4\n")
os.makedirs(f"{E2E}/config/collins", exist_ok=True)
with open(f"{E2E}/config/collins/state.json", "w", encoding="utf-8") as fh:
    fh.write('{"settings": {"welcome_seen": true, "gh_welcome_dismissed": true}}')


def _stub(name: str, body: str) -> None:
    with open(f"{E2E}/bin/{name}", "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(f"{E2E}/bin/{name}", 0o755)


_stub("claude", "#!/bin/sh\nexit 0\n")
# The programs a local extra would start: each only writes down that it ran.
_stub("ghostty", f'#!/bin/sh\necho "ghostty $*" >> {LAUNCHED}\n')
_stub("collins-e2e-opener", f'#!/bin/sh\necho "opener $*" >> {LAUNCHED}\n')
_stub("xdg-open", f'#!/bin/sh\necho "xdg-open $*" >> {LAUNCHED}\n')
with open(f"{E2E}/data/applications/{APP_ID}", "w", encoding="utf-8") as fh:
    fh.write(
        "[Desktop Entry]\nType=Application\nName=E2E Opener\n"
        f"Exec={E2E}/bin/collins-e2e-opener %f\nIcon=text-x-generic\n"
    )
os.environ["PATH"] = f"{E2E}/bin:{os.environ['PATH']}"


def _transcript(cwd: str, summary: str, said: str) -> str:
    enc = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    sid = str(uuid.uuid4())
    os.makedirs(f"{E2E}/projects/{enc}", exist_ok=True)
    with open(f"{E2E}/projects/{enc}/{sid}.jsonl", "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "summary", "cwd": cwd, "summary": summary}) + "\n")
        fh.write(
            json.dumps(
                {
                    "type": "user",
                    "cwd": cwd,
                    "sessionId": sid,
                    "timestamp": "2026-08-01T00:00:00Z",
                    "message": {"role": "user", "content": said},
                }
            )
            + "\n"
        )
    return sid


SID = _transcript(PROJECT, "an old thread", "hello from the export")
GONE_SID = _transcript(GONE, "a thread whose folder went", "where am I")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
import e2e_service  # noqa: E402
from gi.repository import Gio, GLib, Gtk  # noqa: E402

# -- nothing reaches the desktop: the two launchers are recorders for the whole run --------

LAUNCHER_CALLS: list[tuple[str, str]] = []


class _Launcher:
    def __init__(self, what: str) -> None:
        self.what = what

    def launch(self, *_args) -> None:
        LAUNCHER_CALLS.append(("launch", self.what))

    def open_containing_folder(self, *_args) -> None:
        LAUNCHER_CALLS.append(("reveal", self.what))

    def set_always_ask(self, _ask: bool) -> None:
        pass


Gtk.FileLauncher.new = staticmethod(lambda gfile: _Launcher(gfile.get_path() or gfile.get_uri()))
Gtk.UriLauncher.new = staticmethod(lambda uri: _Launcher(uri))

import collins.attachpanel as attachpanel_mod  # noqa: E402
import collins.terminal as terminal_mod  # noqa: E402
from collins import apilink, footerapps, i18n, openwith, remotefiles, remotestore, trust  # noqa: E402
from collins.app import App  # noqa: E402
from collins.attachrecords import Attachment  # noqa: E402
from collins.sidebar import SessionRow  # noqa: E402
from collins.state import AppState  # noqa: E402

# The desktop's own terminal and file manager are never started by this
# check, whatever a gate does: the two functions that would are recorders.
# (The gates inside `footerapps` and `openwith` are the unit suite's:
# tests/test_footerapps.py, tests/test_openwith.py.)
APP_LAUNCHES: list[tuple[str, str]] = []
_real_launch_app = footerapps.launch_app


def _launch_app(info, cwd, *, pass_directory=True):
    APP_LAUNCHES.append((info.get_id() or "", cwd or ""))
    if info.get_id() == APP_ID:
        _real_launch_app(info, cwd, pass_directory=pass_directory)  # the stub in the scratch tree


footerapps.launch_app = _launch_app
openwith.launch_app = _launch_app
openwith.launch_terminal = lambda info, folder: APP_LAUNCHES.append(("terminal", folder or ""))
openwith.default_file_manager = lambda: None  # so Open folder takes the (recorded) FileLauncher road

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


def menu_labels(model: Gio.MenuModel) -> list[str]:
    """Every label of a menu, its sections and submenus included."""
    labels: list[str] = []
    for index in range(model.get_n_items()):
        label = model.get_item_attribute_value(index, "label", GLib.VariantType.new("s"))
        if label is not None:
            labels.append(label.get_string())
        for link in ("section", "submenu"):
            linked = model.get_item_link(index, link)
            if linked is not None:
                labels.extend(menu_labels(linked))
    return labels


def launched() -> list[str]:
    try:
        with open(LAUNCHED, encoding="utf-8") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def session_row(win, session_id: str) -> SessionRow | None:
    index = 0
    while (row := win.sidebar.list.get_row_at_index(index)) is not None:
        if isinstance(row, SessionRow) and row.item.session_id == session_id:
            return row
        index += 1
    return None


def header_row(win, cwd: str):
    for row in win.sidebar._header_rows.values():
        if row.cwd == cwd:
            return row
    return None


def new_chat_tabs(win) -> list:
    tabs = []
    for i in range(win.tab_view.get_n_pages()):
        tab = win.tab_view.get_nth_page(i).get_child()
        if getattr(tab, "is_new_chat", False):
            tabs.append(tab)
    return tabs


def footer_buttons(tab) -> int:
    count, child = 0, tab._footer_apps_box.get_first_child()
    while child is not None:
        count += 1
        child = child.get_next_sibling()
    return count


def sidebar_menus(win) -> tuple[list[str], int, list[str], int]:
    """(the session row's menu labels, its icon rows, the project header's
    labels, its icon rows): what `_popup_menu` is handed, never popped up."""
    sidebar = win.sidebar
    got: list[tuple[list[str], int]] = []
    sidebar._popup_menu = lambda menu, row, x, y, custom_rows=None: got.append(
        (menu_labels(menu), len(custom_rows or ()))
    )
    sidebar.show_row_menu(session_row(win, SID), 0, 0)
    row_menu = got.pop() if got else ([], 0)
    # The project menu is built when the service's checkout answer lands.
    sidebar.show_group_menu(header_row(win, PROJECT), 0, 0)
    e2e_service.wait_until(lambda: bool(got), timeout_s=5.0)
    group_menu = got.pop() if got else ([], 0)
    return row_menu[0], row_menu[1], group_menu[0], group_menu[1]


def attachment_menu(view, one: Attachment) -> list[str]:
    got: list[list[str]] = []
    real = attachpanel_mod.contextmenu.popup_at
    attachpanel_mod.contextmenu.popup_at = lambda popover, *_a: got.append(
        menu_labels(popover.get_menu_model())
    )
    try:
        view.popup_menu(view, one, 0, 0)
    finally:
        attachpanel_mod.contextmenu.popup_at = real
    return got[0] if got else []


def surfaces(win, local: bool) -> None:
    """Read every gated surface; *local* is what the link says right now."""
    say = "local" if local else "not local"
    check(f"[{say}] app.local and the link agree", app.local is local and apilink.is_local() is local)
    check(f"[{say}] the clipboard's scope says so", remotefiles.clipboard_scope().local is local)

    row_labels, row_icons, group_labels, group_icons = sidebar_menus(win)
    for label in ("Open in Ghostty", "Open In…", "Reveal transcript"):
        check(f"[{say}] the session menu's {label}", (label in row_labels) is local, row_labels)
    check(f"[{say}] the project menu's Open In…", ("Open In…" in group_labels) is local, group_labels)
    # The submenu's rows are widgets (an icon each): the footer app, the
    # file manager, and a terminal where the desktop names one.
    check(f"[{say}] …with its app rows", (row_icons >= 2) is local and (group_icons >= 2) is local,
          (row_icons, group_icons))
    # What is no local extra stays, both ways.
    for label in ("Open", "Details…", "Export as Markdown…", "Copy session ID"):
        check(f"[{say}] the session menu keeps {label}", label in row_labels, row_labels)
    kept = "New session here" in group_labels
    check(f"[{say}] the project menu keeps New session here", kept, group_labels)

    readme = f"{PROJECT}/README.md"
    entries = openwith.file_open_with_entries([APP_ID], readme)
    check(f"[{say}] a file's Open In… rows", bool(entries) is local, entries)

    # The attachments panel: a bare view, its menu read off the popover.
    notes: list[str] = []
    view = attachpanel_mod.AttachmentsView(lambda *_a: None, lambda _key: None, notes.append)
    document = Attachment(key=f"{PROJECT}/notes.pdf", kind="file")
    labels = attachment_menu(view, document)
    for label in ("Open With…", "Show in Folder"):
        check(f"[{say}] the attachment menu's {label}", (label in labels) is local, labels)
    for label in ("Copy Path", "Remove From List"):
        check(f"[{say}] the attachment menu keeps {label}", label in labels, labels)
    before, key = len(LAUNCHER_CALLS), document.key
    view._records[document.key] = document
    view.open(document)  # a row that is no picture: the default app
    view._on_show_folder(None, GLib.Variant("s", document.key))
    view._on_open_with(None, GLib.Variant("s", document.key))
    made = LAUNCHER_CALLS[before:]
    check(
        f"[{say}] its launches",
        made == ([("launch", key), ("reveal", key), ("launch", key)] if local else []),
        made,
    )

    # (The lightbox's Open With… is check_drop_upload.py's: the service's
    # own file for a local client, this device's cached copy otherwise.)

    # The window's actions: what a stale menu or a binding would reach.
    before, before_apps, before_log = len(LAUNCHER_CALLS), len(APP_LAUNCHES), len(launched())
    win.lookup_action("open-folder").activate(GLib.Variant("s", PROJECT))
    win.lookup_action("open-folder-terminal").activate(GLib.Variant("s", PROJECT))
    win.lookup_action("open-folder-app").activate(GLib.Variant("(ss)", (APP_ID, PROJECT)))
    win.lookup_action("reveal-transcript").activate(GLib.Variant("s", SID))
    win.lookup_action("open-ghostty").activate(GLib.Variant("s", SID))
    e2e_service.wait_until(lambda: len(launched()) >= before_log + 2, timeout_s=3.0 if local else 0.6)
    made, apps, log = LAUNCHER_CALLS[before:], APP_LAUNCHES[before_apps:], launched()[before_log:]
    if local:
        check("[local] Open folder goes to the file manager", ("launch", PROJECT) in made, made)
        check("[local] Reveal transcript reveals it", any(k == "reveal" and SID in w for k, w in made), made)
        check("[local] the footer app is started on the folder", (APP_ID, PROJECT) in apps, apps)
        check("[local] …for real (the stub ran)", any(line.startswith("opener") for line in log), log)
        check("[local] Open in Ghostty starts it on the session",
              any(line.startswith("ghostty") and SID in line for line in log), log)
    else:
        check("[not local] the window's actions reach no launcher", made == [], made)
        check("[not local] …no app", apps == [], apps)
        check("[not local] …and no program", log == [], log)


def footer(tab, local: bool) -> None:
    say = "local" if local else "not local"
    check(f"[{say}] the footer's file manager button", tab._files_btn.get_visible() is local)
    buttons = footer_buttons(tab)
    check(f"[{say}] the footer's app launchers", buttons == (1 if local else 0), buttons)
    labels = tab._editor._tree.open_with_labels(f"{PROJECT}/README.md")
    check(f"[{say}] the file tree's Open In… rows", bool(labels) is local, labels)
    labels = tab._editor.agent_file_open_with_labels(f"{PROJECT}/README.md")
    check(f"[{say}] the Agent files' Open In… rows", bool(labels) is local, labels)
    # A clicked folder, a file outside the project, a file: URI with no path.
    before = len(LAUNCHER_CALLS)
    terminal_mod._launch_default(tab.terminal, PROJECT)
    made = LAUNCHER_CALLS[before:]
    check(f"[{say}] a clicked folder in the terminal", made == ([("launch", PROJECT)] if local else []), made)
    before = len(LAUNCHER_CALLS)
    terminal_mod._launch_uri(tab.terminal, "https://example.test/")
    check(f"[{say}] a clicked web link opens either way",
          LAUNCHER_CALLS[before:] == [("launch", "https://example.test/")], LAUNCHER_CALLS[before:])


seed = AppState()
i18n.init(seed.get_setting("language"))
seed.set_group_expanded("proj:alpha", True)
seed.set_group_expanded("proj:gone", True)
seed.set_setting("footer_apps", [APP_ID])
trust.trust_dir(PROJECT)
trust.trust_dir(GONE)
# The service is its own process, with a home of its own: D39's fallback
# is the service's `$HOME`, which must not be this user's.
e2e_service.start_service({**os.environ, "HOME": SERVICE_HOME})
app = App()

tries = 0
state: dict = {}


def stage() -> bool:
    """Wait for the window, both projects' rows and the footer app."""
    global tries
    tries += 1
    win = app.get_active_window()
    ready = (
        win is not None
        and header_row(win, PROJECT) is not None
        and session_row(win, SID) is not None
        and session_row(win, GONE_SID) is not None
    )
    if not ready:
        if tries > 120:
            print("timed out waiting for the window and its rows", file=sys.stderr)
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE
    state["win"] = win
    check("the footer app's desktop entry resolves", footerapps.resolve_app(APP_ID) is not None)
    check("the app's link proved it is local", apilink.is_local() is True)

    # -- the Markdown export: the service reads and renders the transcript -------------
    text = remotestore.transcript_export(SID)
    check("the export is the service's render of the transcript",
          text.startswith("# ") and "### You\n\nhello from the export" in text, text[:200])
    check("…under the session's id and project", f"`{SID}`" in text and f"`{PROJECT}`" in text, text[:200])
    state["export"] = text

    surfaces(win, True)
    # A tab, for the footer: the project header's row opens a new-chat screen.
    win.sidebar.list.emit("row-activated", header_row(win, PROJECT))
    GLib.timeout_add(900, with_a_local_tab)
    return GLib.SOURCE_REMOVE


def with_a_local_tab() -> bool:
    win = state["win"]
    tabs = new_chat_tabs(win)
    check("a tab opened in the project", len(tabs) == 1, len(tabs))
    if not tabs:
        app.quit()
        return GLib.SOURCE_REMOVE
    state["local_tab"] = tabs[0]
    footer(tabs[0], True)

    # -- the proof taken away: what a client over ssh is ---------------------------------
    apilink.current().local = False
    surfaces(win, False)
    check("[not local] the export is still the service's, unchanged",
          remotestore.transcript_export(SID) == state["export"])
    # The tab that was built local: its launchers do nothing now.
    before_apps, before_log = len(APP_LAUNCHES), len(launched())
    tabs[0]._on_footer_app_clicked(None, footerapps.resolve_app(APP_ID))
    tabs[0]._on_open_file_manager(None)
    check("[not local] a footer built before does nothing",
          APP_LAUNCHES[before_apps:] == [] and launched()[before_log:] == [], APP_LAUNCHES[before_apps:])
    # A tab built now has no such buttons at all.
    win.lookup_action("new-session-in-new-window").activate(GLib.Variant("s", PROJECT))
    GLib.timeout_add(1200, with_a_remote_tab)
    return GLib.SOURCE_REMOVE


def with_a_remote_tab() -> bool:
    fresh = None
    for window in app.get_windows():
        for tab in new_chat_tabs(window) if hasattr(window, "tab_view") else []:
            if tab is not state["local_tab"]:
                fresh = tab
    check("[not local] a tab opened in the project", fresh is not None)
    if fresh is not None:
        footer(fresh, False)
        tip = fresh._files_btn.get_parent()
        check("[not local] the footer is still there", tip is not None)

    # -- D39: a session whose project directory is gone ------------------------------------
    apilink.current().local = True  # back on the service's machine
    win = state["win"]
    session = win.store.get_session(GONE_SID)
    check("the session whose folder went is in the store", session is not None and session.cwd == GONE)
    win.open_session(session)
    GLib.timeout_add(400, after_the_gone_spawn)
    return GLib.SOURCE_REMOVE


def gone_tab(win):
    for i in range(win.tab_view.get_n_pages()):
        tab = win.tab_view.get_nth_page(i).get_child()
        if getattr(tab, "session_id", None) == GONE_SID:
            return tab
    return None


def after_the_gone_spawn() -> bool:
    win = state["win"]
    tab = gone_tab(win)
    check("the tab opened", tab is not None)
    if tab is None:
        app.quit()
        return GLib.SOURCE_REMOVE
    state["gone_tab"] = tab
    landed = e2e_service.wait_until(lambda: tab.editor_root == SERVICE_HOME, timeout_s=6.0)
    check("the tab's editor landed in the service's home", landed, tab.editor_root)
    check("…and the bare-name links' root with it", tab.link_root == SERVICE_HOME, tab.link_root)
    check("…as the session's cwd says", tab._cwd == SERVICE_HOME, tab._cwd)
    pty = tab.session.pty
    info = apilink.call({"t": "pty.info", "pty": pty}) if pty is not None else {}
    check("the pty's row says where it started", info.get("cwd") == SERVICE_HOME, info.get("cwd"))
    said = e2e_service.wait_until(
        lambda: "no longer exists" in (tab.probe_call("visible_screen_text") or ""), timeout_s=5.0
    )
    check("…and the session said so in the terminal", said)
    # A panel shell asked to start in the folder that is gone.
    shell = tab.open_panel_shell()
    state["shell"] = shell
    GLib.timeout_add(700, after_the_shell)
    return GLib.SOURCE_REMOVE


def after_the_shell() -> bool:
    tab, shell = state["gone_tab"], state["shell"]
    check("a panel shell opened", shell is not None)
    if shell is not None:
        # Its own spawn, in the folder that is gone, by hand: the tab's shell
        # above started where the session already is.
        started = shell.started_cwd
        check("the tab's shell started where the session is", started == SERVICE_HOME, started)
        info = shell._pty_info() or {}
        check("…and the service's row agrees", info.get("cwd") == SERVICE_HOME, info.get("cwd"))
        reply = apilink.call({"t": "spawn", "kind": "shell", "cwd": GONE, "cols": 80, "rows": 24})
        check("a shell spawned in a folder that is gone starts in the service's home",
              reply.get("cwd") == SERVICE_HOME, reply.get("cwd"))
        reply = apilink.call({"t": "spawn", "kind": "shell", "cwd": PROJECT, "cols": 80, "rows": 24})
        check("…and one spawned in a folder that is there starts in it",
              reply.get("cwd") == PROJECT, reply.get("cwd"))
        # `cd` into a folder that is gone is never typed (the service's
        # `fs.stat` says it is no directory), and one that is there is.
        shell._sync_cwd(GONE)
        e2e_service.settle(600)
        where = (shell._pty_info() or {}).get("process_cwd")
        check("a shell is never moved into a folder that is gone", where == SERVICE_HOME, where)
        check("an idle shell follows into a folder that is there", shell.follow_cwd(PROJECT))
        moved = e2e_service.wait_until(
            lambda: (shell._pty_info() or {}).get("process_cwd") == PROJECT, timeout_s=5.0
        )
        check("…once the service has said it is one", moved, (shell._pty_info() or {}).get("process_cwd"))
    check("nothing was launched on the desktop that this check did not record",
          all(line.split(" ", 1)[0] in ("ghostty", "opener") for line in launched()), launched())
    tab._close_ok = True
    app.quit()
    return GLib.SOURCE_REMOVE


def deadline() -> bool:
    print("timed out", file=sys.stderr)
    print(f"\n{PASSED} passed, {FAILED} failed (and the check timed out)")
    os._exit(1)


GLib.timeout_add_seconds(90, deadline)
GLib.timeout_add(250, stage)
app.run([])
shutil.rmtree(E2E, ignore_errors=True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED or not PASSED else 0)
