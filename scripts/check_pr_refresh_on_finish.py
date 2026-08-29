#!/usr/bin/env python3
"""End-to-end check for the PR refresh a finished run triggers — dev machine.

The finish edge (ActivityTracker's on_finished, the same one that flags a row
unread) is supposed to re-ask GitHub about the session's pull requests: the
chips' status through TerminalTab.note_run_finished, and any PR page open
beside the tab through PrViewPage.refresh_if_stale. None of that is reachable
from pytest — tests/conftest.py blocks the GTK stack, and the wiring runs
between a real window, a real tab and a real panel dock — so it is checked
here, against a real App:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \
        python3 scripts/check_pr_refresh_on_finish.py

A staged transcript carries a ``pr-link`` record, so the tab picks up a pull
request the way a real session does. Everything that would leave the machine
is stubbed: `prstatus.gh_json` answers a canned ``gh pr view`` (and records
every call), `prdetail.fetch` records the page's own load, and the CLI the tab
spawns is a shim that draws an idle prompt and sleeps.

Run it behind the headless wrapper, or a window opens on the user's screen.
"""

import json
import os
import sys
import tempfile
import threading
import uuid

E2E = tempfile.mkdtemp(prefix="collins-prrefresh-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

# Isolation first: every one of these is read at import time somewhere below.
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"

PR_URL = "https://github.com/episode6/collins/pull/55"
CWD = f"{E2E}/work"
SESSION = str(uuid.uuid4())
SHIM = f"{E2E}/bin/claude"

for path in (f"{E2E}/chats", f"{E2E}/bin", CWD, f"{E2E}/config/collins"):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")

# The session Collins discovers: one user turn (so the row has a title) and the
# pr-link record the CLI appends when a PR passes through its tool output.
_PROJECT = f"{E2E}/projects/" + "".join(c if c.isalnum() else "-" for c in CWD)
os.makedirs(_PROJECT, exist_ok=True)
_LINES = [
    {
        "type": "user",
        "timestamp": "2026-08-16T09:00:00Z",
        "cwd": CWD,
        "sessionId": SESSION,
        "message": {"role": "user", "content": "Open a PR"},
    },
    {
        "type": "pr-link",
        "sessionId": SESSION,
        "prNumber": 55,
        "prUrl": PR_URL,
        "prRepository": "episode6/collins",
        "timestamp": "2026-08-16T09:01:00Z",
    },
]
with open(f"{_PROJECT}/{SESSION}.jsonl", "w", encoding="utf-8") as fh:
    for line in _LINES:
        fh.write(json.dumps(line) + "\n")
with open(f"{E2E}/config/collins/state.json", "w", encoding="utf-8") as fh:
    fh.write('{"settings": {"title_model": "none"}}')

# The CLI the tab spawns: an idle prompt and nothing else. The real one would
# go looking for a session id that doesn't exist and print whatever it makes of
# that, which is noise this check would then have to ride out.
with open(SHIM, "w", encoding="utf-8") as fh:
    fh.write(
        "#!/usr/bin/env python3\n"
        "import sys, time\n"
        'sys.stdout.write("❯  ")\n'
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
from gi.repository import GLib  # noqa: E402

from collins import i18n, prdetail, prstatus, trust  # noqa: E402
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


# -- the stubs: every gh call this run would have made, counted --------------
#
# Both land on fetch threads, so the lists are taken under a lock.

_lock = threading.Lock()
gh_calls: list[list] = []
detail_calls: list[str] = []


def fake_gh_json(args, cwd=None, timeout=None):
    with _lock:
        gh_calls.append(list(args))
    if list(args[:2]) == ["pr", "view"]:
        return {
            "url": PR_URL,
            "title": "A staged pull request",
            "state": "OPEN",
            "isDraft": False,
            "statusCheckRollup": [{"conclusion": "SUCCESS"}],
            "mergeable": "MERGEABLE",
            "comments": [],
            "commits": [],
        }
    return None


def fake_detail_fetch(url):
    with _lock:
        detail_calls.append(url)
    return None  # the page shows its banner; only the call is under test


prstatus.gh_json = fake_gh_json
prdetail.fetch = fake_detail_fetch


def status_fetches() -> int:
    with _lock:
        return len([call for call in gh_calls if call[:2] == ["pr", "view"]])


def page_loads() -> list[str]:
    with _lock:
        return list(detail_calls)


def forget_page_loads() -> None:
    with _lock:
        detail_calls.clear()


i18n.init(AppState().get_setting("language"))
trust.trust_dir(CWD)
app = App()

exit_code = 1
tries = 0
state: dict = {}


def finish_edge() -> None:
    """The tracker's own finish edge for this session — what a turn ending
    looks like from the window's side."""
    activity = state["win"]._activity
    activity.mark(SESSION)
    activity.finish(SESSION)


def age_the_throttles() -> None:
    """Stand in for the ten seconds both refreshes ask for between reads."""
    state["tab"]._pr_focus_refresh_at = 0
    page = state.get("page")
    if page is not None:
        page._fetched_at = 0


def later(fn, ms: int = 1500) -> bool:
    GLib.timeout_add(ms, fn)
    return GLib.SOURCE_REMOVE


# -- the steps ---------------------------------------------------------------


def stage() -> bool:
    """Wait for the store to find the staged session, then open its tab."""
    global tries
    tries += 1
    win = app.get_active_window()
    session = app.store.get_session(SESSION)
    if win is None or session is None:
        if tries > 40:  # ~10s: the store scan should long since have landed
            print("timed out waiting for the window/session", file=sys.stderr)
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE
    win.open_session(session)
    state["win"] = win
    state["tab"] = win.tab_view.get_selected_page().get_child()
    return later(step_chips, 1000)  # the tab's first poll, and its PR fetch


def step_chips() -> bool:
    tab = state["tab"]
    # The chips ride the first update and the fetch rides the chips; how long
    # either takes is the runner's business, not the check's. Wait for both —
    # bounded, like every wait here.
    ready = [pr.url for pr in tab._footer_prs] == [PR_URL] and status_fetches() >= 1
    if not ready and state.setdefault("chip_waits", 0) < 40:
        state["chip_waits"] += 1
        return later(step_chips, 500)
    check("the transcript's PR is on the tab", [pr.url for pr in tab._footer_prs] == [PR_URL],
          [pr.url for pr in tab._footer_prs])
    check("its status was fetched at least once", status_fetches() >= 1, gh_calls)
    age_the_throttles()
    state["before"] = status_fetches()
    finish_edge()
    return later(step_status_refetched)


def step_status_refetched() -> bool:
    # The refetch may ride the next poll tick rather than the finish itself:
    # note_run_finished's own update request is dropped when a poll is
    # mid-flight (_request_update's _updating gate), and the invalidation is
    # then spent a tick later. A fixed wait is enough on a dev machine but
    # not on a loaded CI runner, so wait for it — bounded.
    if status_fetches() <= state["before"] and state.setdefault("refetch_waits", 0) < 20:
        state["refetch_waits"] += 1
        return later(step_status_refetched, 500)
    check("a finish edge refetches the PR's status", status_fetches() > state["before"],
          f"{state['before']} -> {status_fetches()}")
    # No aging this time: a session that goes quiet between every permission
    # prompt must not turn into a `gh` call per pause.
    state["before"] = status_fetches()
    finish_edge()
    return later(step_throttled)


def step_throttled() -> bool:
    check("a second finish seconds later is throttled", status_fetches() == state["before"],
          f"{state['before']} -> {status_fetches()}")
    state["tab"].open_pr_page_url(PR_URL)
    return later(step_page_open)


def step_page_open() -> bool:
    page = next(
        (p for p in state["tab"]._dock.pages() if getattr(p, "page_kind", None) == "pr"), None
    )
    check("the PR page opened", page is not None)
    if page is None:
        return done()
    state["page"] = page
    check("the PR page is mapped", page.get_mapped())
    age_the_throttles()
    forget_page_loads()
    finish_edge()
    return later(step_page_reread)


def step_page_reread() -> bool:
    check("a finish edge re-reads the open PR page", page_loads() == [PR_URL], page_loads())
    # A detach's parting progress-clear lands here as a finish too, and must
    # spend nothing: the run is being handed on, not completing.
    state["win"]._detaching.add(SESSION)
    age_the_throttles()
    forget_page_loads()
    state["before"] = status_fetches()
    finish_edge()
    return later(step_detaching)


def step_detaching() -> bool:
    check(
        "a detaching session's finish is skipped",
        status_fetches() == state["before"] and not page_loads(),
        f"{state['before']} -> {status_fetches()}, {page_loads()}",
    )
    state["win"]._detaching.discard(SESSION)
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
