#!/usr/bin/env python3
"""End-to-end check that a PR page refresh only touches what changed — dev machine.

A landed fetch patches the page (prview._Slots) rather than rebuilding it:
a card, check row, file section or list row the new reply describes as the
last one did is the same widget afterwards, with its fold, its expanded
diff, its draft and the keyboard where they were; only what the reply
changed is built again, in its place; and the scroll stays anchored on
what was in view. None of that is reachable from pytest (tests/conftest.py
blocks the GTK stack, and the scroll pin lands in the frame clock's layout
phase, after a real allocation), so it is checked here against the real
page in a real window:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_pr_page_patch.py

`prdetail.fetch` is stubbed to answer whatever detail the step under way
staged — so nothing leaves the machine — and each step stages one change
(none; one check's state; a new comment; an edited description; a reply in
a thread; a thread on a file; one file's patch), lands it the way a real
fetch does (`_fetch(force=True)`, worker thread and all), and reads back
which widgets survived.

Run it behind the headless wrapper, or a window opens on the user's screen.
"""

import os
import sys
import tempfile
from dataclasses import replace
from types import SimpleNamespace

E2E = tempfile.mkdtemp(prefix="collins-prpatch-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

# Isolation first: every one of these is read at import time somewhere below.
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from collins import i18n, prdetail, prview  # noqa: E402
from collins.app import apply_gtk_settings  # noqa: E402
from collins.prstatus import PullRequest  # noqa: E402

PR_URL = "https://github.com/episode6/collins/pull/55"
THREAD_ID = "PRRT_thread1"
DESCRIPTION = "First paragraph of the description.\n\n" + "\n\n".join(
    f"Paragraph {i}, with enough words in it that the fold has something to hide."
    for i in range(2, 16)
)
# Enough comments that the Conversation column scrolls in a 700px window.
COMMENTS = tuple(
    prdetail.PrComment(
        author="reviewer",
        created_at=f"2026-08-16T09:{i:02d}:00Z",
        body=f"Comment number {i}, a line or two of it.\n\nAnd a second paragraph.",
        url=f"{PR_URL}#issuecomment-{i}",
    )
    for i in range(1, 13)
)
# Past the fold, so the Checks list has a "Show more" to leave alone.
CHECKS = tuple(
    prdetail.PrCheck(name=f"check-{i}", state="PASSED" if i else "PENDING", url="")
    for i in range(6)
)
PATCH = "@@ -1,2 +1,2 @@\n-old\n+new\n context\n"
FILES = (
    prdetail.PrFile(path="collins/a.py", additions=1, deletions=1, patch=PATCH),
    prdetail.PrFile(path="collins/b.py", additions=1, deletions=1, patch=PATCH),
)

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


def base_detail() -> prdetail.PullRequestDetail:
    pr = PullRequest(number=55, url=PR_URL, repository="episode6/collins", title="A title",
                     state="OPEN")
    reply = prdetail.PrComment(
        author="reviewer", created_at="2026-08-16T09:00:30Z", body="Is this right?", url=""
    )
    thread = prdetail.PrThread(
        id=THREAD_ID, path="collins/a.py", line=1, is_resolved=False,
        is_outdated=False, comments=(reply,),
    )
    return prdetail.PullRequestDetail(
        summary=pr, body=DESCRIPTION, author="me", created_at="2026-08-16T09:00:00Z",
        base_ref="main", head_ref="topic", base_oid="a", head_oid="b", head_repository="",
        additions=2, deletions=2, changed_files=2, labels=("bug",), checks=CHECKS,
        timeline=(thread, *COMMENTS), files=FILES, threads=(thread,), viewer_is_author=True,
    )


STAGED = {"detail": base_detail()}
prdetail.fetch = lambda url: STAGED["detail"]

HOST = SimpleNamespace(
    archive=None, refresh=lambda: None, prompt_block=lambda: "", confirm_merges=lambda: True
)


# -- reading the widget tree -------------------------------------------------


def walk(widget: Gtk.Widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from walk(child)
        child = child.get_next_sibling()


def find(widget: Gtk.Widget, pred) -> Gtk.Widget | None:
    return next((w for w in walk(widget) if pred(w)), None)


def column(page) -> list[Gtk.Widget]:
    return page._content_slots.widgets


def check_rows(page) -> list[Gtk.Widget]:
    """The Checks preview's rows, in order (see prview._ChecksSection)."""
    return page._checks._shown.widgets


def description_fold(page) -> prview._Fold:
    return page._description_fold


def checks_fold(page) -> prview._Fold:
    return page._checks._fold


def label_text(widget: Gtk.Widget) -> str:
    label = find(widget, lambda w: isinstance(w, Gtk.Label))
    return label.get_text() if label is not None else ""


def in_view(page, widgets: list[Gtk.Widget]) -> tuple[Gtk.Widget | None, float]:
    """The first of *widgets* reaching into the Conversation viewport, and
    where its top sits — what the page's scroll pin anchors on."""
    for widget in widgets:
        ok, bounds = widget.compute_bounds(page._scroller)
        if ok and bounds.get_y() + bounds.get_height() > 0:
            return widget, round(bounds.get_y())
    return None, 0.0


# -- the run ----------------------------------------------------------------

i18n.init("en")
app = Adw.Application(application_id=os.environ["COLLINS_APP_ID"])
exit_code = 1
state: dict = {}


def later(fn, ms: int = 150) -> bool:
    GLib.timeout_add(ms, fn)
    return GLib.SOURCE_REMOVE


def land(detail: prdetail.PullRequestDetail, then) -> bool:
    """Stage *detail*, fetch it the way the page does, and run *then* once it
    has landed and the scroll pin's idle has run."""
    STAGED["detail"] = detail
    page = state["page"]
    page._fetch(force=True)

    def wait() -> bool:
        if page._fetching:
            return later(wait, 50)
        return later(then, 200)

    return later(wait, 50)


def on_activate(app: Adw.Application) -> None:
    apply_gtk_settings()
    win = Adw.ApplicationWindow(application=app, default_width=1000, default_height=700)
    pr = PullRequest(number=55, url=PR_URL, repository="episode6/collins")
    page = prview.PrViewPage(pr, lambda: HOST)
    win.set_content(page)
    win.present()
    state["win"] = win
    state["page"] = page
    later(step_loaded, 800)


def step_loaded() -> bool:
    page = state["page"]
    if page._detail is None and state.setdefault("load_waits", 0) < 20:
        state["load_waits"] += 1
        return later(step_loaded, 250)
    check("the page loaded its detail", page._detail is not None)
    if page._detail is None:
        return done()
    # The reader's state: both folds open, a diff expanded, a draft typed,
    # the keyboard on the fold's toggle, the column scrolled into the middle.
    description_fold(page).set_expanded(True)
    checks_fold(page).set_expanded(True)
    page._sections[1].reveal()
    card = page._thread_cards[0][1]
    card._on_reply_toggled()
    card._text.get_buffer().set_text("a reply draft")
    toggle = find(description_fold(page), lambda w: isinstance(w, Gtk.Button))
    toggle.grab_focus()
    adj = page._scroller.get_vadjustment()
    check("the column scrolls", adj.get_upper() > adj.get_page_size() + 600, adj.get_upper())
    adj.set_value(400.0)
    state["before"] = {
        "column": column(page),
        "rows": check_rows(page),
        "sections": list(page._sections),
        "list": page._list_slots.widgets,
        "cards": list(page._cards),
        "toggle": toggle,
        "mark": page._mark_slot.get_child(),
    }
    return land(base_detail(), step_same)


def step_same() -> bool:
    page, win, before = state["page"], state["win"], state["before"]
    check("an unchanged reply keeps every column child", column(page) == before["column"])
    check("…every check row", check_rows(page) == before["rows"])
    check("…every file section", list(page._sections) == before["sections"])
    check("…every file list row", page._list_slots.widgets == before["list"])
    check("…every thread card", list(page._cards) == before["cards"])
    check("…the header's mark", page._mark_slot.get_child() is before["mark"])
    check("…the description open", description_fold(page).expanded)
    check("…the checks open", checks_fold(page).expanded)
    check("…the second file's diff expanded", page._sections[1]._expander.get_expanded())
    check(
        "…the keyboard where it was",
        win.get_focus() is before["toggle"],
        type(win.get_focus()).__name__,
    )
    adj = page._scroller.get_vadjustment()
    check("…and the scroll where it was", adj.get_value() == 400.0, adj.get_value())
    # One check finishes.
    checks = (replace(CHECKS[0], state="PASSED"), *CHECKS[1:])
    return land(replace(base_detail(), checks=checks), step_check_changed)


def step_check_changed() -> bool:
    page, before = state["page"], state["before"]
    rows = check_rows(page)
    check("a finished check keeps the column", column(page) == before["column"])
    check("…and the checks open", checks_fold(page).expanded)
    # Folded lists sort blockers first, so the pending row led; passed, it
    # keeps gh's order and still leads — as a new row, the rest kept.
    check("…swaps that one row", rows[0] is not before["rows"][0], label_text(rows[0]))
    check("…and keeps the others", rows[1:] == before["rows"][1:])
    state["before"]["rows"] = rows
    state["checks"] = (replace(CHECKS[0], state="PASSED"), *CHECKS[1:])
    # A new comment arrives.
    new = prdetail.PrComment(
        author="reviewer", created_at="2026-08-16T10:00:00Z", body="One more.", url=""
    )
    detail = replace(base_detail(), checks=state["checks"])
    detail = replace(detail, timeline=(*detail.timeline, new))
    return land(detail, step_comment_added)


def step_comment_added() -> bool:
    page, before = state["page"], state["before"]
    now = column(page)
    check("a new comment lands one card longer", len(now) == len(before["column"]) + 1, len(now))
    check("…before the composer", now[-1] is page._composer)
    check("…keeping everything else", now[:-2] == before["column"][:-1])
    adj = page._scroller.get_vadjustment()
    check("…and the scroll where it was", adj.get_value() == 400.0, adj.get_value())
    state["before"]["column"] = now
    # The description is edited, with the reader below it: what is in view
    # must stay where it is, whatever the description's new height does to
    # the numbers above it.
    state["anchor"] = in_view(page, now[1:])
    detail = replace(base_detail(), checks=state["checks"], body=DESCRIPTION + "\n\nAnd more.")
    return land(replace(detail, timeline=page._detail.timeline), step_description_edited)


def step_description_edited() -> bool:
    page, before = state["page"], state["before"]
    now = column(page)
    check("an edited description is a new card", now[0] is not before["column"][0])
    check("…open, as the old one was", description_fold(page).expanded)
    check("…with the rest kept", now[1:] == before["column"][1:])
    anchor, offset = state["anchor"]
    adj = page._scroller.get_vadjustment()
    check(
        "the scroll stays anchored on what was in view",
        anchor is not None and in_view(page, [anchor]) == (anchor, offset),
        (in_view(page, [anchor]), offset, adj.get_value()),
    )
    check("…having moved past the taller card", adj.get_value() > 400.0, adj.get_value())
    state["before"]["column"] = now
    # A reply lands in the thread the draft is typed in.
    card = page._thread_cards[0][1]
    card._text.grab_focus()
    thread = page._detail.threads[0]
    more = prdetail.PrComment(
        author="me", created_at="2026-08-16T11:00:00Z", body="Yes.", url=""
    )
    thread = replace(thread, comments=(*thread.comments, more))
    timeline = (thread, *page._detail.timeline[1:])
    detail = replace(page._detail, threads=(thread,), timeline=timeline)
    state["old_card"] = card
    return land(detail, step_thread_changed)


def step_thread_changed() -> bool:
    page, win, before = state["page"], state["win"], state["before"]
    card = page._thread_cards[0][1]
    check("a changed thread is a new card", card is not state["old_card"])
    check("…with the draft back", card._body() == "a reply draft", card._body())
    check("…and the keyboard in it", win.get_focus() is card._text, type(win.get_focus()).__name__)
    check("…its twin under the file rebuilt too", page._sections[0].thread_cards[0] is not before["cards"][1])
    check("…the file's section kept", page._sections[0] is before["sections"][0])
    check("…and the other file's kept", page._sections[1] is before["sections"][1])
    check("…still expanded", page._sections[1]._expander.get_expanded())
    check("…and the list rows kept", page._list_slots.widgets == before["list"])
    check("…the page's card list follows", set(page._cards) == {card, *page._sections[0].thread_cards})
    # The second file's patch changes.
    files = (FILES[0], replace(FILES[1], patch=PATCH + "+more\n", additions=2))
    return land(replace(page._detail, files=files), step_file_changed)


def step_file_changed() -> bool:
    page, before = state["page"], state["before"]
    check("a changed file is a new section", page._sections[1] is not before["sections"][1])
    check("…the other kept", page._sections[0] is before["sections"][0])
    rows = page._list_slots.widgets
    check("…its list row new", rows[1] is not before["list"][1])
    check("…the other row kept", rows[0] is before["list"][0])
    check("…in order", [label_text(r) for r in rows] == ["collins/a.py", "collins/b.py"])
    return done()


def done() -> bool:
    global exit_code
    print(f"\n{PASSED} passed, {FAILED} failed")
    exit_code = 0 if FAILED == 0 else 1
    app.quit()
    return GLib.SOURCE_REMOVE


app.connect("activate", on_activate)
app.run([])
sys.exit(exit_code)
