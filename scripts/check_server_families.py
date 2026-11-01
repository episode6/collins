#!/usr/bin/env python3
# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The window against a server that does not list a family (server-side spec D5).

For each family in turn, a real window on a real service started with the
`caps_drop` stub (`scripts/e2e_stubs.py`: the family leaves the hello and its
requests are answered `unknown`, each one recorded), and a run with every
family dropped, which is `core` alone. Each holds the window to:

- **no surface of it.** `SURFACES` is a table of what a person would see, each
  entry a predicate that is true when it shows.
- **no request of it.** Every one that reached the service is in the stub's
  record, so the window sent it: nothing may be there.
- **a tab that still opens, prompts and closes.**
- **what it does instead** (`INSTEAD`): the composer's Send through `prompt`,
  a drop refused with a toast, the editor's banner, the mismatch's Quit.

A run drops nothing and holds every `SURFACES` predicate to true, so that a
"hidden" above can't pass because the surface was never there. A last one has
the service refuse the first `subscribe`, which must be the Reconnecting banner
and not a failed startup (spec D6). A `legacy` run is the hello of a Collins
service from before families, with the banner and the restart that are the way
out of it (spec D18), and `upgrade` takes that road for real: that service
restarted full under the window, and the panels it could not show back with no
reopen. `files-back` does the same for the editor's saved files.

Each family's run is its own process (a window builds from the hello it finds),
run a few at a time, so the check stays inside CI's per-check budget:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \
        python3 scripts/check_server_families.py
    python3 scripts/check_server_families.py --family prs   # one, in this process
"""

import argparse
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
RUNS_AT_ONCE = 4
RUN_DEADLINE_S = 100


def driver() -> int:
    """Every run, a few at a time; their output in order, whatever order they end."""
    sys.path.insert(0, REPO)
    from collins.api import protocol

    runs = [cap for cap in protocol.CONFORMANCE_FAMILIES if cap != protocol.CAP_DEBUG]
    runs += ["all", "none", "refused-subscribe", "legacy", "upgrade", "files-back"]

    def one(family: str) -> tuple[str, int, str]:
        try:
            done = subprocess.run(
                [sys.executable, os.path.abspath(__file__), "--family", family],
                capture_output=True,
                text=True,
                timeout=RUN_DEADLINE_S,
            )
        except subprocess.TimeoutExpired as expired:
            text = (expired.stdout or b"").decode("utf-8", "replace") if isinstance(expired.stdout, bytes) else ""
            return family, 124, text + f"\nFAIL  {family}: no end within {RUN_DEADLINE_S} s\n"
        return family, done.returncode, done.stdout + done.stderr

    started = time.monotonic()
    with ThreadPoolExecutor(RUNS_AT_ONCE) as pool:
        results = list(pool.map(one, runs))
    failed = 0
    for family, code, text in results:
        print(f"== {family}")
        print(text.rstrip())
        failed += code != 0
    print(f"{len(results) - failed} of {len(results)} runs passed in {time.monotonic() - started:.0f} s")
    return 1 if failed else 0


def run(family: str) -> int:
    sys.stdout.reconfigure(line_buffering=True)  # each run ends in os._exit, which flushes nothing
    E2E = tempfile.mkdtemp(prefix="collins-families-")
    RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())
    os.environ["COLLINS_DEBUG_API"] = "1"
    os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
    os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
    os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
    os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
    os.environ["COLLINS_UPLOADS_DIR"] = f"{E2E}/uploads"
    os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
    os.environ["XDG_STATE_HOME"] = f"{E2E}/state"
    os.environ["SHIM_LOG"] = f"{E2E}/shim.log"
    PROJECT = f"{E2E}/dev/alpha"
    SID = "11111111-2222-4333-8444-555555555555"
    for path in (f"{E2E}/projects", f"{E2E}/chats", f"{E2E}/bin", f"{E2E}/config/collins", PROJECT):
        os.makedirs(path, exist_ok=True)
    with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
        fh.write("{}")
    subprocess.run(["git", "init", "-q", PROJECT], check=True)
    encoded = "".join(c if c.isalnum() else "-" for c in PROJECT)
    os.makedirs(f"{E2E}/projects/{encoded}", exist_ok=True)
    with open(f"{E2E}/projects/{encoded}/{SID}.jsonl", "w", encoding="utf-8") as fh:
        fh.write(
            '{"type":"user","cwd":"%s","sessionId":"%s","timestamp":"2026-11-01T10:00:00.000Z",'
            '"message":{"role":"user","content":"a session"}}\n' % (PROJECT, SID)
        )
    # The stub: the CLI's box (the prompt and a no-break space), what it is sent
    # logged, an echo so a half-typed line reads as a box that holds text, and
    # Ctrl+C for the graceful close.
    shim = f"{E2E}/bin/claude"
    with open(shim, "w", encoding="utf-8") as fh:
        fh.write(
            r"""#!/usr/bin/env python3
import os, sys, tty
if sys.argv[1:2] == ["agents"]:
    print("[]")
    sys.exit(0)
tty.setraw(0)
log = os.environ["SHIM_LOG"]
text = ""
def draw():
    sys.stdout.write("\r\x1b[K\u276f\u00a0" + text)
    sys.stdout.flush()
draw()
while True:
    chunk = os.read(0, 4096)
    if not chunk:
        break
    for b in chunk.replace(b"\x1b[200~", b"").replace(b"\x1b[201~", b""):
        if b == 0x7F:
            text = text[:-1]
        elif b == 0x03:
            sys.exit(0)
        elif b == 0x0D:
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
            sys.stdout.write("\r\n")
            text = ""
        elif 0x20 <= b < 0x7F:
            text += chr(b)
    draw()
"""
        )
    os.chmod(shim, 0o755)
    os.environ["PATH"] = f"{E2E}/bin:{os.environ['PATH']}"
    # The gh card is a surface of `prs`: left to show (on a box with no gh) only
    # where the check says it must not, since a dialog over the window is what
    # every other assertion here would then be reading.
    dismissed = "false" if family in ("prs", "all") else "true"
    SERVICE_ID = "0123456789abcdef0123456789abcdef"
    with open(f"{E2E}/config/collins/state.json", "w", encoding="utf-8") as fh:
        fh.write(
            '{"service_id": "%s", "settings": {"welcome_seen": true, "gh_welcome_dismissed": %s,'
            ' "title_model": "none"}}' % (SERVICE_ID, dismissed)
        )
    if family == "refused-subscribe":
        # A launch with something to do once it connects: the session that was
        # active, by the setting, with no `open_tabs` recorded.
        with open(f"{E2E}/config/collins/ui-state.json", "w", encoding="utf-8") as fh:
            fh.write(
                '{"device": {"settings": {"restore_last_session": true}},'
                ' "services": {"%s": {"last_active_session": "%s"}}}' % (SERVICE_ID, SID)
            )
    elif family in ("legacy", "upgrade", "files", "files-back"):
        with open(f"{PROJECT}/kept.txt", "w", encoding="utf-8") as fh:
            fh.write("a file the editor had open")
        # Records a family's absence must not shrink (ruling 20): a dock with a
        # shell page, and an editor with a file open.
        import json

        layout = {
            "mode": "bottom",
            "sizes": {"bottom": 278},
            "tree": {
                "split": "v",
                "size": 278,
                "managed": True,
                "a": {"terminal": True},
                "b": {
                    "strip": {
                        "open": True,
                        "home": True,
                        "selected": 0,
                        "pages": [{"kind": "shell", "hist": 0}],
                    }
                },
            },
        }
        editor = {"open": False, "width": 400, "files": [f"{PROJECT}/kept.txt"], "active": 0, "cursors": {}}
        block = {"panel_layout": {SID: layout}, "editor_states": {SID: editor}}
        with open(f"{E2E}/config/collins/ui-state.json", "w", encoding="utf-8") as fh:
            json.dump({"services": {SERVICE_ID: block}}, fh)

    sys.path.insert(0, REPO)
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("Vte", "3.91")
    import e2e_service
    from gi.repository import Gio, GLib, Gtk

    from collins import apilink, blobcache, checkouts, claudemodels, gitinfo, i18n, modelcatalog
    from collins import proctree, remoteicons, sandboxstatus
    from collins import prefs as prefs_mod
    from collins import remotefiles, trust
    from collins.api import protocol
    from collins.api.client import ProtocolMismatch
    from collins.app import App
    from collins.connection import ConnectionManager
    from collins.prefs import PreferencesDialog
    from collins.prstatus import PullRequest
    from collins.sidebar import SessionRow
    from collins.state import AppState

    i18n.init(AppState().get_setting("language"))
    trust.trust_dir(PROJECT)
    AppState().set_group_expanded(f"proj:{os.path.basename(PROJECT)}", True)

    every = [cap for cap in protocol.CONFORMANCE_FAMILIES if cap != protocol.CAP_DEBUG]
    since_families = [cap for cap in every if cap not in (protocol.CAP_GIT, protocol.CAP_FILES)]
    dropped = {
        "all": every,
        "none": [],
        "refused-subscribe": [],
        "legacy": since_families,
        "upgrade": since_families,
        "files-back": [protocol.CAP_FILES],
    }.get(family, [family])
    results = {"passed": 0, "failed": 0}

    def check(label: str, ok: object, detail: object = "") -> None:
        if ok:
            results["passed"] += 1
            print(f"  ok  {label}")
        else:
            results["failed"] += 1
            print(f"FAIL  {label}  {detail}")

    def wait_until(predicate, timeout_s: float = 8.0) -> bool:
        return e2e_service.wait_until(predicate, timeout_s=timeout_s)

    def actions(model) -> list[str]:
        """Every action a menu offers, its sections and submenus included."""
        found: list[str] = []
        for index in range(model.get_n_items()):
            action = model.get_item_attribute_value(index, "action", GLib.VariantType.new("s"))
            if action is not None:
                found.append(action.get_string())
            for link in ("section", "submenu"):
                linked = model.get_item_link(index, link)
                if linked is not None:
                    found.extend(actions(linked))
        return found

    def shim_said(text: str) -> bool:
        try:
            with open(os.environ["SHIM_LOG"], encoding="utf-8") as fh:
                return text in fh.read()
        except OSError:
            return False

    def session_row(win):
        index = 0
        while (row := win.sidebar.list.get_row_at_index(index)) is not None:
            if isinstance(row, SessionRow) and row.item.session_id == SID:
                return row
            index += 1
        return None

    def header_row(win):
        return next((row for row in win.sidebar._header_rows.values() if row.cwd == PROJECT), None)

    def build_context(win, tab) -> dict:
        sidebar = win.sidebar
        got: list = []
        sidebar._popup_menu = lambda menu, row, x, y, custom_rows=None: got.append(actions(menu))
        sidebar.show_row_menu(session_row(win), 0, 0)
        row_menu = got.pop() if got else []
        sidebar.show_group_menu(header_row(win), 0, 0)
        wait_until(lambda: bool(got))
        group_menu = got.pop() if got else []
        prefs = PreferencesDialog(win.state, lambda: None)
        labels = prefs._quit_behavior_row.get_model()
        return {
            "dialog": prefs,
            "rows": [row for group in prefs._groups.values() for row in group.rows],
            "quit_choices": [
                value
                for value, label in prefs_mod._QUIT_BEHAVIORS
                if any(labels.get_string(i) == i18n._(label) for i in range(labels.get_n_items()))
            ],
            "row": row_menu,
            "group": group_menu,
            "add": actions(sidebar._add_menu),
            "app": actions(sidebar._menu),
            "new": actions(win._new_menu),
            "prefs": {name for name, _side in prefs._groups},
            "tab": tab,
            "win": win,
            "row_widget": session_row(win),
        }

    # What a person would see of each family. True when it shows.
    SURFACES = {
        protocol.CAP_GIT: [
            ("the footer's git toggle", lambda c: c["tab"]._git_toggle_btn.get_visible()),
            ("the footer's branch", lambda c: c["tab"]._branch_label.get_visible()),
            ("the project menu's Git pull", lambda c: "win.git-pull" in c["group"]),
            ("Preferences' Git group", lambda c: "git" in c["prefs"]),
        ],
        protocol.CAP_FILES: [
            # D5 and ruling 17: the editor stays, and says so in its banner
            # (`instead`). Whether a project is a checkout is an `fs.stat`.
            ("the project menu's Git pull", lambda c: "win.git-pull" in c["group"]),
        ],
        protocol.CAP_PRS: [
            ("the footer's PR refresh", lambda c: c["tab"]._pr_refresh_btn.get_visible()),
            ("Preferences' PR group", lambda c: "pull_requests" in c["prefs"]),
        ],
        protocol.CAP_SANDBOX: [
            ("Preferences' Sandbox group", lambda c: "sandbox" in c["prefs"]),
        ],
        protocol.CAP_TOOLS: [
            ("the attachments handle", lambda c: c["tab"]._attachments_btn.get_visible()),
            ("Preferences' session tools", lambda c: "mcp_tools" in c["prefs"]),
        ],
        protocol.CAP_JOBS: [
            ("Add project's Clone", lambda c: "win.clone-project" in c["add"]),
            ("the New menu's chat", lambda c: "win.new-session-in-chats" in c["new"]),
            ("the project menu's Generate Icon", lambda c: "win.generate-icon" in c["group"]),
            ("the row menu's Repair", lambda c: "win.repair-session" in c["row"]),
            ("Preferences' clone folder", lambda c: hasattr(c["dialog"], "_clone_dir_row")),
            (
                "Preferences' worktree-archive row",
                lambda c: any("archive_worktree" in getattr(row, "settings", ()) for row in c["rows"]),
            ),
        ],
        protocol.CAP_USAGE: [
            ("the usage panel", lambda c: c["win"].sidebar.usage_panel.get_visible()),
            ("Preferences' Token use", lambda c: "token_use" in c["prefs"]),
            ("Preferences' usage switch", lambda c: c["dialog"]._usage_panel_row.get_parent() is not None),
            ("the project menu's Generate Icon", lambda c: "win.generate-icon" in c["group"]),
        ],
        protocol.CAP_BACKGROUND: [
            ("the row's background button", lambda c: c["row_widget"]._can_background),
            ("the row menu's Repair", lambda c: "win.repair-session" in c["row"]),
            ("Preferences' background poll", lambda c: c["dialog"]._bg_poll_row.get_parent() is not None),
            (
                "Preferences' background choice on quit",
                lambda c: "background" in c["quit_choices"],
            ),
        ],
        protocol.CAP_SHELLS: [
            ("the footer's panel toggle", lambda c: c["tab"]._panel_toggle_btn.get_visible()),
        ],
        protocol.CAP_COMPOSER: [
            # The composer stays, on `prompt` (`instead`); what goes is typing into it.
            ("Preferences' typing switch", lambda c: c["dialog"]._composer_typing_row.get_parent() is not None),
        ],
        protocol.CAP_INTERNAL: [
            ("the app menu's Restart service", lambda c: "win.restart-service" in c["app"]),
            ("Preferences' CLI rows", lambda c: "cli" in c["prefs"]),
            ("the footer's PR refresh", lambda c: c["tab"]._pr_refresh_btn.get_visible()),
        ],
    }

    def instead(cap: str, win, tab, toasts: list) -> None:
        """What each family's absence does beyond hiding."""

        def box_is_empty() -> bool:
            return bool(tab.probe_call("takes_prompt"))

        if cap == protocol.CAP_GIT:
            check("no git asks: no branch", gitinfo.current_branch(PROJECT) is None)
            check("F6 opens no git page", tab.toggle_git_page() is False and tab._git_page is None)
            win.lookup_action("toggle-git").activate(None)
            check("…nor does its action", tab._git_page is None)
        elif cap == protocol.CAP_FILES:
            target = f"{PROJECT}/notes.txt"
            with open(target, "w", encoding="utf-8") as fh:
                fh.write("hello")
            tab._editor.open_file(target)
            wait_until(lambda: tab._editor._banner.get_revealed())
            check(
                "the editor's banner says so",
                "no files" in (tab._editor._banner.get_title() or ""),
                tab._editor._banner.get_title(),
            )
            check("a stat is refused here", remotefiles.present(target) is False)
            check("a project has the generic icon", remoteicons.icon_bytes(PROJECT) is None)
            answers: list = []
            checkouts.ask_answer(PROJECT, answers.append)
            check("a folder is no checkout, so a launch is plain", answers == [False], answers)
            # A window that is not on the service's machine (D60's way to say
            # so) has no picker to browse with: a session still needs a folder
            # to start in, so the asks stay and take a typed path (ruling 26).
            e2e_service.drop_local(apilink.current())
            win.apply_families()
            new_menu, add_menu = actions(win._new_menu), actions(win.sidebar._add_menu)
            check("not local: New session stays", "win.new-session-provider" in new_menu, new_menu)
            check("not local: Continue last session stays", "win.continue-session" in new_menu, new_menu)
            check("not local: Open folder stays", "win.add-project" in add_menu, add_menu)
            win.lookup_action("new-session-provider").activate(GLib.Variant("s", "claude"))
            wait_until(lambda: win.get_visible_dialog() is not None)
            dialog = win.get_visible_dialog()
            entry = dialog.get_extra_child() if dialog is not None else None
            check("New session asks for a typed path", isinstance(entry, Gtk.Entry), dialog)
            check("…prefilled with the service's home", entry is not None and entry.get_text().startswith("/"))
            if entry is not None:
                entry.set_text("not/absolute")
                check("…which can't go while it names no folder", not dialog.get_response_enabled("open"))
                entry.set_text(PROJECT)
                before = win.tab_view.get_n_pages()
                dialog.emit("response", "open")
                dialog.force_close()
                check(
                    "the typed folder opens a session's screen",
                    wait_until(lambda: win.tab_view.get_n_pages() > before),
                )
                pages = [win.tab_view.get_nth_page(i) for i in range(win.tab_view.get_n_pages())]
                page = next(one for one in pages if getattr(one.get_child(), "is_new_chat", False))
                typed_tab = page.get_child()
                check("…in that folder", typed_tab._cwd == PROJECT, typed_tab._cwd)
                typed_tab._new_chat.emit("send-requested", "a typed folder's prompt", False, False, "", "")
                check("Send reaches spawn, in that folder", wait_until(lambda: shim_said("a typed folder's prompt"), 20.0))
                check(
                    "…where the service says it landed",
                    wait_until(lambda: typed_tab.session.cwd == PROJECT),
                    typed_tab.session.cwd,
                )
                win._graceful_close(page)
                def tab_gone() -> bool:
                    pages = range(win.tab_view.get_n_pages())
                    return all(win.tab_view.get_nth_page(i).get_child() is not typed_tab for i in pages)

                check("…and that tab closes", wait_until(tab_gone, 15.0))
            apilink.current().local = True
            win.apply_families()
        elif cap == protocol.CAP_PRS:
            tab.open_pr_page(PullRequest(number=1, url="https://github.com/o/r/pull/1"))
            win.lookup_action("open-pr-page").activate(None)
            check(
                "no PR page opens",
                not [p for p in tab._dock.pages() if getattr(p, "page_kind", "") == "pr"],
            )
            e2e_service.settle(900)
            check("no gh card over the window", win.get_visible_dialog() is None, win.get_visible_dialog())
        elif cap == protocol.CAP_SANDBOX:
            check("the probe's word is unknown", sandboxstatus.probe_reason() is None, sandboxstatus.probe_reason())
            check("the project menu has no pin", "sidebar.project-sandbox" not in build_context(win, tab)["group"])
            check("no chip", tab._sandbox_chip.get_visible() is False)
        elif cap == protocol.CAP_TOOLS:
            tab.open_attachments()
            check("the gallery does not open", not tab.attachments_open())
            texture = _a_texture()
            check("a dropped image is refused", tab._drop_texture(texture) is False)
            check("…with a toast", wait_until(lambda: any("dropped files" in text for text in toasts)), toasts)
            with _raises(ValueError):
                blobcache.fetch(blobcache.file_url("/x"), ".png")
        elif cap == protocol.CAP_JOBS:
            win.lookup_action("new-session-in-chats").activate(None)
            check("no chat folder is asked for", win.get_visible_dialog() is None)
        elif cap == protocol.CAP_USAGE:
            check("the catalog is empty, not an error", modelcatalog.cache_failed() is False)
            check("the pickers have the static list", bool(claudemodels.FALLBACK_MODELS))
            check("the CLI's defaults answer nothing", modelcatalog.cli_default_model(PROJECT) is None)
        elif cap == protocol.CAP_BACKGROUND:
            page = win.tab_view.get_page(tab)
            check("the window's gate refuses a /bg", bool(win._background_blocker(page)))
            check("…and so does the session", tab.session.begin_close("/bg", True) is False)
        elif cap == protocol.CAP_SHELLS:
            win.lookup_action("toggle-panel").activate(None)
            check("Ctrl+J opens no shell", not tab._dock.shell_pages())
            check("the tool's way in is refused", tab.open_panel_shell() is None)
            tab.feed_message("a line in the tab")  # `paint` is core (ruling 22): no refusal in the record
            # The line moved the cursor off the box. The CLI redraws its own;
            # this stub redraws on input. Asked of the live screen, as a prompt
            # into an empty box is: the mirror's word is a settle behind, and
            # still says yes while the line is landing.
            tab.session.write_text("x\x7f")
            check("…and the box is back under it", wait_until(box_is_empty))
            check("a saved layout's shell does not come back", "shell" not in tab._restorable_kinds())
        elif cap == protocol.CAP_COMPOSER:
            check("a key is the CLI's own", tab._typing_opens_composer(ord("a"), 0) is False)
            check("…so no composer opened", not tab.composer_open())
            tab.open_composer()
            wait_until(lambda: tab.composer_open())
            tab._composer.set_text("composed prompt")
            tab._on_composer_send(None, "composed prompt")
            check("Send goes through prompt", wait_until(lambda: shim_said("composed prompt")))
            check("…and clears the draft", wait_until(lambda: not tab._composer.peek_text()))
            wait_until(box_is_empty)
            tab.switch_model("sonnet")
            check("the model menu types /model", wait_until(lambda: shim_said("/model sonnet")))
            wait_until(box_is_empty)
            tab.switch_effort("low")
            check("the effort menu types /effort", wait_until(lambda: shim_said("/effort low")))
            wait_until(box_is_empty)
            tab.session.write_text("half")
            wait_until(lambda: not box_is_empty())
            tab._on_composer_send(None, "a second one")
            check("a box that holds text is said in a toast", wait_until(lambda: bool(toasts)), toasts)
            check("…and nothing was typed", not shim_said("a second one"))
            tab.session.write_text("\x7f\x7f\x7f\x7f")
            wait_until(box_is_empty)
            wait_until(tab.takes_prompt)  # the composer's open reads the mirror's word
            tab.open_composer()
            wait_until(lambda: tab.composer_open())
            tab._composer.set_text("a draft")
            tab.close_composer()
            check("a closed draft stays in the stash", tab._composer_stash == "a draft", tab._composer_stash)
            e2e_service.settle(150)
            check("…not in the box", box_is_empty() and not shim_said("a draft"))
            tab._stash_draft("")
        elif cap == protocol.CAP_INTERNAL:
            before = protocol.PROTOCOL + 1
            app_ = win.get_application()
            words = app_._mismatch_text(ProtocolMismatch("x", 0, before, [protocol.CAP_GIT]))
            check("the mismatch dialog offers Quit only", words[2] is False)
            words = app_._mismatch_text(ProtocolMismatch("x", 0, before, [protocol.CAP_INTERNAL]))
            check("…unless the server lists internal", words[2] is True)
            words = app_._mismatch_text(ProtocolMismatch("x", 0, before))
            check("…or named no caps, as only a pre-family service did", words[2] is True)
            win.lookup_action("restart-service").activate(None)
            e2e_service.settle(200)
            check("Restart service asks nothing", win.get_visible_dialog() is None)

    def _raises(kind):
        class Raises:
            def __enter__(self):
                return self

            def __exit__(self, got, _value, _trace):
                check(f"raises {kind.__name__}", got is not None and issubclass(got, kind), got)
                return True

        return Raises()

    def _a_texture():
        from gi.repository import Gdk

        bytes_ = GLib.Bytes.new(b"\xff\xff\xff\xff")
        return Gdk.MemoryTexture.new(1, 1, Gdk.MemoryFormat.R8G8B8A8, bytes_, 4)

    caffeine_asked: list = []
    real_caffeine = App.set_caffeine_enabled

    def set_caffeine_enabled(self, enabled, duration=None):
        caffeine_asked.append(enabled)
        real_caffeine(self, enabled, duration)

    App.set_caffeine_enabled = set_caffeine_enabled

    os.environ.pop("COLLINS_KEEPER", None)
    banner_read: list = []
    if family == "refused-subscribe":
        # Twice: the first subscribe's refusal and the reconnect's (ruling 32).
        e2e_service.start_service(stubs={"refuse_subscribe": 2})
        # The reconnect's first backoff is a second, which a window built on a
        # loaded runner can outlast: it would be connected, and the banner down,
        # before anybody read it. So the retry waits for the read, on the
        # manager's own timer.
        real_retry = ConnectionManager._retry

        def retry_after_the_read(manager) -> bool:
            if not banner_read:
                manager._pending = manager._schedule(100, manager._retry)
                return False
            return real_retry(manager)

        ConnectionManager._retry = retry_after_the_read
    elif family in ("legacy", "upgrade"):
        service = e2e_service.start_service(stubs={"caps_drop": dropped, "legacy_hello": True})
    else:
        service = e2e_service.start_service(stubs={"caps_drop": dropped})
    app = App()
    outcome = {"code": 1}

    def go() -> bool:
        win = app.get_active_window()
        if family == "refused-subscribe":
            if win is None:
                return GLib.SOURCE_CONTINUE
            check("the window opens under the Reconnecting banner", wait_until(win.reconnect_banner.get_revealed))
            check("…with no dialog over it", win.get_visible_dialog() is None, win.get_visible_dialog())
            banner_read.append(True)
        elif win is None or session_row(win) is None or header_row(win) is None:
            return GLib.SOURCE_CONTINUE
        try:
            if family == "refused-subscribe":
                check(
                    "the reconnect subscribes, and the row comes",
                    wait_until(lambda: session_row(win) is not None and header_row(win) is not None, 20.0),
                )
                refusals = [call for call in e2e_service.stub_calls() if call["stub"] == "refused_subscribe"]
                check("…past a second refusal, whose retry came too", len(refusals) == 2, refusals)
                check("the banner comes down", wait_until(lambda: not win.reconnect_banner.get_revealed()))
                # The launch it held back, whole (the review of PR 646).
                check("the launch's last session comes back", wait_until(lambda: win._page_for(SID) is not None))
                # Asked, not granted: with no session manager (CI's container)
                # `inhibit` answers 0, and Caffeine rightly reads as off.
                check("…and it asks for Caffeine on launch", wait_until(lambda: caffeine_asked == [True]), caffeine_asked)
            drive(win)
        except Exception:
            import traceback

            traceback.print_exc()
            results["failed"] += 1
        outcome["code"] = 1 if results["failed"] else 0
        print(f"{family}: {results['passed']} ok, {results['failed']} failed")
        e2e_service._stop_all()
        os._exit(outcome["code"])
        return GLib.SOURCE_REMOVE

    def drive(win) -> None:
        check(
            "the hello lists what the stub left",
            all(not apilink.has(cap) for cap in dropped)
            and all(apilink.has(cap) for cap in every if cap not in dropped),
            apilink.current().families,
        )
        banner = win.reconnect_banner
        road = [0, 0]  # the legacy run's stretch of the stub's record under a hello it faked
        if family in ("legacy", "upgrade"):
            check("it is read as a pre-family service", apilink.predates_families())
            check("the banner is up", wait_until(banner.get_revealed), banner.get_title())
            check("…and says so", "predates this window" in (banner.get_title() or ""), banner.get_title())
            check(
                "…naming no command for another app id, whose service no unit runs",
                "systemctl" not in (banner.get_title() or ""),
                banner.get_title(),
            )
            check("…with its button", banner.get_button_label() == "Restart service", banner.get_button_label())
            check("the app menu still has no Restart", "win.restart-service" not in actions(win.sidebar._menu))
        else:
            check("not read as a pre-family service", not apilink.predates_families())
            check("no banner", wait_until(lambda: not banner.get_revealed()), banner.get_title())
        session = win.store.get_session(SID)
        opened_at = time.monotonic()
        win.open_session(session)
        page = win._page_for(SID)
        tab = page.get_child()
        heard: list = []  # every word on the process facts that reached this tab, and when
        real_apply = tab.session.apply

        def apply_heard(event: dict) -> set:
            if "agent_running" in event or "running_command" in event:
                heard.append(
                    (round(time.monotonic() - opened_at, 2), event.get("agent_running"), event.get("running_command"))
                )
            return real_apply(event)

        tab.session.apply = apply_heard
        toasts: list = []
        tab.connect("toast", lambda _tab, text: toasts.append(text))
        check("a tab opens", wait_until(lambda: tab.pty_id is not None), tab.pty_id)
        check("the agent comes up at its box", wait_until(lambda: tab.takes_prompt(), 20.0))
        # Read off the process table on a poll, so it can come a beat after the
        # box does; the composer's open asks for it.
        started = time.monotonic()
        running = wait_until(tab._agent_is_running, 8.0)
        waited = time.monotonic() - started
        detail = ""
        print(f"  ..  running after {waited:.2f}s: {running}")
        if not running:
            # What the service itself believes, against what reached this tab.
            pids = tab.probe_call("candidate_pids") or []
            detail = (
                f"after {waited:.1f}s; service says agent_is_running={tab.probe_call('agent_is_running')} "
                f"candidates={[(pid, proctree.process_cmdline(pid), proctree.process_cwd(pid)) for pid in pids]} "
                f"sent={tab.probe_call('host._sent.get', 'agent_running')} "
                f"sent_cmd={tab.probe_call('host._sent.get', 'running_command')} "
                f"poll={tab.probe('activity._process_poll')} "
                f"cli={tab.probe('provider.cli')}; "
                f"mirror says running_command={tab.session.running_command} "
                f"takes_prompt={tab.session.takes_prompt} pid={tab.session.pid} "
                f"pty={tab.pty_id} exited={tab.session.exited} agent_running={tab.session.agent_running} "
                f"loop_pending={GLib.MainContext.default().pending()} "
                f"heard(t, agent, cmd)={heard} since_open={time.monotonic() - opened_at:.2f}"
            )
        check("…and it reads as running", running, detail)
        # A cwd tick has passed for the runs it is about: `cwd.settle` and
        # `git.info` go out on the tab's map and on each 2 s tick.
        e2e_service.settle(2300 if family in ("git", "internal", "all", "none") else 300)

        context = build_context(win, tab)
        # A surface two families share (Generate Icon, the row's Repair) needs
        # both: gone with either, so it is said once, of the one dropped.
        gone = {label for cap in dropped for label, _shows in SURFACES[cap]}
        for cap, surfaces in SURFACES.items():
            for label, shows in surfaces:
                if cap in dropped:
                    check(f"{cap}: no {label}", not shows(context))
                elif label not in gone:
                    # A mirror's word, some of them (the branch off `git.info`, the
                    # row's button off its `item`), so it is waited for.
                    check(f"{cap}: {label} shows", wait_until(lambda shows=shows: shows(context)))

        if family == "upgrade":
            # The normal upgrade: nobody touched the dock, and the restart
            # leaves this tab attached (ruling 20; the review of PR 646).
            check("the saved panels are a view: no shell page", not tab._dock.shell_pages())
            check("…whose record stands", tab._layout_record is not None)
            refused = [call["args"][0] for call in e2e_service.stub_calls() if call["stub"] == "refused"]
            check("the old service was asked nothing it does not list", not refused, refused)
            tab.grab_terminal_focus()
            wait_until(lambda: win.get_focus() is not None)
            focused = win.get_focus()
            e2e_service.stop_service(service)
            e2e_service.start_service(stubs={})
            check("the next hello lists the families", wait_until(lambda: apilink.has(protocol.CAP_SHELLS), 30.0))
            check("the banner comes down", wait_until(lambda: not banner.get_revealed(), 10.0), banner.get_title())
            check("the panels come back with no reopen", wait_until(lambda: bool(tab._dock.shell_pages()), 15.0))
            check("…the record with them", tab._layout_record is None, tab._layout_record)
            shell = next(iter(tab._dock.shell_pages()), None)
            check("…with a real shell", shell is not None and wait_until(shell._has_pty, 10.0))
            check("the agent's tab is still attached", wait_until(lambda: tab.takes_prompt(), 15.0))
            check("the keyboard stayed where it was", win.get_focus() is focused, win.get_focus())
        elif family == "files-back":
            check("the saved editor state is a view", tab._editor_record is not None, tab._editor_record)
            e2e_service.stop_service(service)
            e2e_service.start_service(stubs={})
            check("the next hello lists files", wait_until(lambda: apilink.has(protocol.CAP_FILES), 30.0))
            check(
                "the saved file comes back with no reopen",
                wait_until(lambda: f"{PROJECT}/kept.txt" in tab._editor.open_paths(), 15.0),
                tab._editor.open_paths(),
            )
            check("…the record with it", tab._editor_record is None, tab._editor_record)

        if family == "legacy":
            win._on_banner_button(banner)
            wait_until(lambda: win.get_visible_dialog() is not None)
            dialog = win.get_visible_dialog()
            check("the button asks with the blast radius", dialog is not None, dialog)
            if dialog is not None:
                dialog.force_close()
                e2e_service.settle(200)
            win.get_application().cancel_service_restart()  # `service.restart`: served, and the record below says so
            # What was built under this hello, read again by the next: the
            # upgrade's own road (the review of PR 646). A strip that exists
            # now, a docked composer's, is one of them.
            tab.open_composer()
            wait_until(lambda: tab.composer_open())
            tab._composer.emit("dock-toggle-requested")
            wait_until(lambda: bool(tab._dock.strips()))
            strip = tab._dock.strips()[0] if tab._dock.strips() else None
            built_down = {
                "the New menu's chat": lambda: "win.new-session-in-chats" in actions(win._new_menu),
                "Add project's Clone": lambda: "win.clone-project" in actions(win.sidebar._add_menu),
                "the app menu's Restart": lambda: "win.restart-service" in actions(win.sidebar._menu),
                "the editor's follow judge": lambda: tab._editor._follow_judge is not None,
                "the strip's add button": lambda: strip is not None and strip._add_btn.get_visible(),
                "the row's background button": lambda: session_row(win)._can_background,
                "the footer's panel toggle": lambda: tab._panel_toggle_btn.get_visible(),
                "the attachments handle": lambda: tab._attachments_btn.get_visible(),
            }
            for label, shows in built_down.items():
                check(f"before the restart: no {label}", not shows())
            link = apilink.current()
            hello, families = link.hello, link.families
            # While this hello stands the window rightly asks what it lists,
            # and this one service still refuses it: that stretch of the
            # record is the road's own, and is left out of the assertion below.
            road_began = len(e2e_service.stub_calls())
            link.hello = {**hello, "implementation": "collins-service"}
            link.families = families | frozenset(every)
            win.apply_families()
            check("the next hello that lists a family takes the banner down", not banner.get_revealed())
            for label, shows in built_down.items():
                check(f"…and {label} comes back", wait_until(shows))
            e2e_service.settle(450)  # past the beat a record's return would wait
            check(
                "a dock the person changed keeps its record",
                tab._layout_record is not None and not tab._dock.shell_pages(),
                tab._layout_record,
            )
            link.hello, link.families = hello, families
            win.apply_families()
            for label, shows in built_down.items():
                check(f"another hello without it: no {label}", not shows())
            e2e_service.settle(400)
            road[:] = [road_began, len(e2e_service.stub_calls())]
        elif family not in ("upgrade", "files-back"):
            for cap in dropped:
                instead(cap, win, tab, toasts)

        check("the box is empty on the service", wait_until(lambda: tab.probe_call("takes_prompt")))
        check("a prompt lands", tab.inject_prompt("a prompt", when_empty=True))
        check("…in the agent", wait_until(lambda: shim_said("a prompt")))
        win._graceful_close(page)
        check("the tab closes", wait_until(lambda: win.tab_view.get_n_pages() == 0, 15.0), win.tab_view.get_n_pages())
        e2e_service.settle(200)
        # The legacy run's Restart went through, and stayed out of this list.
        calls = e2e_service.stub_calls()
        calls = calls[: road[0]] + calls[road[1] :]
        refused = sorted({call["args"][0] for call in calls if call["stub"] == "refused"})
        check("the service was asked nothing of what it does not list", not refused, refused)
        if family in ("legacy", "upgrade", "files", "files-back"):
            import json

            with open(f"{E2E}/config/collins/ui-state.json", encoding="utf-8") as fh:
                saved = json.load(fh).get("services", {}).get(SERVICE_ID, {})
            if family in ("legacy", "upgrade"):
                entry = saved.get("panel_layout", {}).get(SID) or {}
                check("the saved layout kept its shell", "shell" in json.dumps(entry.get("tree")), entry)
            else:
                entry = saved.get("editor_states", {}).get(SID) or {}
                check("the saved editor state kept its file", entry.get("files") == [f"{PROJECT}/kept.txt"], entry)

    GLib.timeout_add(250, go)
    GLib.timeout_add_seconds(RUN_DEADLINE_S - 10, lambda: (print("timed out"), e2e_service._stop_all(), os._exit(3)))
    app.run([])
    return outcome["code"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--family", help="one run, in this process: a family's cap, `all` or `none`")
    arguments = parser.parse_args()
    sys.exit(run(arguments.family) if arguments.family else driver())
