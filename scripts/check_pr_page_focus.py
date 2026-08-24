#!/usr/bin/env python3
"""End-to-end check that a PR page rebuild leaves no text selected — dev machine.

Every landed fetch rebuilds the Conversation and Files views wholesale
(`PrViewPage._rebuild` / `_rebuild_files`), background ones included. If the
keyboard focus was on anything in the box being emptied, GTK re-places it
after the next paint — and, for a widget that stays unparented, lands it on
the first focusable thing in the emptied box: the description's first label,
which, being selectable, selects all of itself on focus. None of this is
reachable from pytest (tests/conftest.py blocks the GTK stack, and the
relocation is an after-paint step of a real window), so it is checked here
against the real page in a real window:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \
        python3 scripts/check_pr_page_focus.py

`prdetail.fetch` is stubbed with a canned detail — a folded description, a
review thread, no files — so nothing leaves the machine. Each step puts the
focus where a click would have (a button, a label, an editor), rebuilds the
way a landed fetch does, and reads back where the focus went and which labels
carry a selection 150 ms later, past the paint the relocation waits for.

The other route into a label is covered too: the page sits in an
`Adw.TabView` beside a second page, and switching away and back hands the
page its last focus (a clicked label) — which, with GTK's select-on-focus
left on, selected it; `app.apply_gtk_settings` turns that off, and the last
steps check that a re-grab and a Tab into a label select nothing.

Run it behind the headless wrapper, or a window opens on the user's screen.
"""

import os
import sys
import tempfile
from types import SimpleNamespace

E2E = tempfile.mkdtemp(prefix="collins-prfocus-")
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


def fake_detail_fetch(url: str) -> prdetail.PullRequestDetail:
    pr = PullRequest(number=55, url=url, repository="episode6/collins", title="A title",
                     state="OPEN")
    comment = prdetail.PrComment(
        author="reviewer", created_at="2026-08-16T09:00:00Z", body="Is this right?", url=""
    )
    thread = prdetail.PrThread(
        id=THREAD_ID, path="collins/prview.py", line=12, is_resolved=False,
        is_outdated=False, comments=(comment,),
    )
    return prdetail.PullRequestDetail(
        summary=pr, body=DESCRIPTION, author="me", created_at="2026-08-16T09:00:00Z",
        base_ref="main", head_ref="topic", base_oid="a", head_oid="b", head_repository="",
        additions=1, deletions=1, changed_files=0, labels=(), checks=(), timeline=(thread,),
        files=(), threads=(thread,), viewer_is_author=True,
    )


prdetail.fetch = fake_detail_fetch

# The page's host: what the header's actions would drive. None of them run
# here, but the page reads a couple of its answers while building.
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


def selected_labels(page: Gtk.Widget) -> list[str]:
    return [
        w.get_text()[:24]
        for w in walk(page)
        if isinstance(w, Gtk.Label) and w.get_selection_bounds()[0]
    ]


def describe(widget: Gtk.Widget | None) -> str:
    if widget is None:
        return "None"
    name = type(widget).__name__
    if isinstance(widget, Gtk.Label):
        name += f"({widget.get_text()[:24]!r})"
    return name


def description_label(page: Gtk.Widget) -> Gtk.Label:
    return find(page, lambda w: isinstance(w, Gtk.Label) and w.get_text().startswith("First"))


def show_more_button(page: Gtk.Widget) -> Gtk.Button:
    # The description's fold toggle: the one button whose child is a box
    # (the word and the caret), see prview._fold.
    return find(page, lambda w: isinstance(w, Gtk.Button) and isinstance(w.get_child(), Gtk.Box))


def thread_card(page) -> prview._ThreadCard:
    return page._thread_cards[0][1]


def clear_selections(page: Gtk.Widget) -> None:
    for w in walk(page):
        if isinstance(w, Gtk.Label):
            w.select_region(0, 0)


# -- the run ----------------------------------------------------------------

i18n.init("en")
app = Adw.Application(application_id=os.environ["COLLINS_APP_ID"])
exit_code = 1
state: dict = {}


def later(fn, ms: int = 150) -> bool:
    GLib.timeout_add(ms, fn)
    return GLib.SOURCE_REMOVE


def on_activate(app: Adw.Application) -> None:
    apply_gtk_settings()  # what App.do_startup does; the switch under test
    win = Adw.ApplicationWindow(application=app, default_width=1000, default_height=700)
    pr = PullRequest(number=55, url=PR_URL, repository="episode6/collins")
    page = prview.PrViewPage(pr, lambda: HOST)
    # Beside a second page in a tab view, the way the dock holds it: the tab
    # view's own focus handling is one of the routes under test.
    view = Adw.TabView()
    other = Gtk.Box()
    other.append(Gtk.Entry())
    state["pr_tab"] = view.append(page)
    state["other_tab"] = view.append(other)
    view.set_selected_page(state["pr_tab"])
    win.set_content(view)
    win.present()
    state["win"] = win
    state["page"] = page
    state["view"] = view
    state["other"] = other
    later(step_loaded, 800)  # the (stubbed) fetch lands from an idle


def step_loaded() -> bool:
    page = state["page"]
    if page._detail is None and state.setdefault("load_waits", 0) < 20:
        state["load_waits"] += 1
        return later(step_loaded, 250)
    check("the page loaded its detail", page._detail is not None)
    if page._detail is None:
        return done()
    check("nothing is selected after the load", not selected_labels(page), selected_labels(page))
    # 1. "Show more" pressed (a click leaves the focus on the button).
    show_more_button(page).grab_focus()
    page._rebuild()
    return later(step_after_show_more)


def step_after_show_more() -> bool:
    page, win = state["page"], state["win"]
    check(
        "a rebuild after \"Show more\" selects nothing",
        not selected_labels(page),
        selected_labels(page),
    )
    check(
        "…and parks the keyboard on the scroller",
        win.get_focus() is page._scroller,
        describe(win.get_focus()),
    )
    # 2. A click in the description: the label takes focus without a
    # selection (GTK's in_click), and the focus survives the click.
    label = description_label(page)
    label.grab_focus()
    label.select_region(0, 0)
    page._rebuild()
    return later(step_after_description_click)


def step_after_description_click() -> bool:
    page, win = state["page"], state["win"]
    check(
        "a rebuild after a click in the description selects nothing",
        not selected_labels(page),
        selected_labels(page),
    )
    check(
        "…and parks the keyboard on the scroller",
        win.get_focus() is page._scroller,
        describe(win.get_focus()),
    )
    # 3. Typing in the comment box.
    text = page._composer._text
    text.get_buffer().set_text("half a comm")
    text.grab_focus()
    page._rebuild()
    return later(step_after_composer)


def step_after_composer() -> bool:
    page, win = state["page"], state["win"]
    text = page._composer._text
    check(
        "a rebuild while typing a comment keeps the cursor in the box",
        win.get_focus() is text,
        describe(win.get_focus()),
    )
    buffer = text.get_buffer()
    check(
        "…with the text intact",
        buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True) == "half a comm",
    )
    check("…and selects nothing", not selected_labels(page), selected_labels(page))
    # 4. Typing a reply in a thread: the editor is rebuilt with its draft.
    card = thread_card(page)
    card._on_reply_toggled()
    card._text.get_buffer().set_text("a reply draft")
    card._text.grab_focus()
    page._rebuild()
    return later(step_after_thread_reply)


def step_after_thread_reply() -> bool:
    page, win = state["page"], state["win"]
    card = thread_card(page)
    check(
        "a rebuild while typing a thread reply lands in the rebuilt editor",
        win.get_focus() is card._text,
        describe(win.get_focus()),
    )
    check("…which is open", card._reveal.get_reveal_child())
    check("…and selects nothing", not selected_labels(page), selected_labels(page))
    # 5. A thread's Reply button pressed, nothing typed: the new card's editor
    # isn't open, so the scroller keeps the keyboard.
    del page._thread_drafts[THREAD_ID]
    page._rebuild()
    clear_selections(page)
    reply = find(thread_card(page), lambda w: isinstance(w, Gtk.Button) and w.get_label())
    reply.grab_focus()
    page._rebuild()
    return later(step_after_reply_button)


def step_after_reply_button() -> bool:
    page, win = state["page"], state["win"]
    check(
        "a rebuild after a thread's button selects nothing",
        not selected_labels(page),
        selected_labels(page),
    )
    check(
        "…and parks the keyboard on the scroller",
        win.get_focus() is page._scroller,
        describe(win.get_focus()),
    )
    # 6. The keyboard elsewhere entirely is left alone.
    win.set_focus(None)
    page._rebuild()
    return later(step_after_no_focus)


def step_after_no_focus() -> bool:
    page, win = state["page"], state["win"]
    check(
        "a rebuild with no focus in the page leaves it nowhere",
        win.get_focus() is None,
        describe(win.get_focus()),
    )
    check("…and selects nothing", not selected_labels(page), selected_labels(page))
    # 7. A click in the description, then the tab switched away and back:
    # the tab view hands the page its last focus — the label — by a plain
    # grab, which used to select it.
    label = description_label(page)
    label.grab_focus()
    label.select_region(0, 0)
    state["view"].set_selected_page(state["other_tab"])
    return later(step_switched_away)


def step_switched_away() -> bool:
    win, other = state["win"], state["other"]
    check(
        "switching tabs moves the keyboard to the other page",
        win.get_focus() is not None and win.get_focus().is_ancestor(other),
        describe(win.get_focus()),
    )
    state["view"].set_selected_page(state["pr_tab"])
    return later(step_switched_back)


def step_switched_back() -> bool:
    page, win = state["page"], state["win"]
    check(
        "switching back hands the page its last focus, the label",
        win.get_focus() is description_label(page),
        describe(win.get_focus()),
    )
    check("…without selecting it", not selected_labels(page), selected_labels(page))
    # 8. Tab from the scroller into the header's title label.
    page._scroller.grab_focus()
    win.child_focus(Gtk.DirectionType.TAB_FORWARD)
    return later(step_tabbed)


def step_tabbed() -> bool:
    page, win = state["page"], state["win"]
    check(
        "Tab from the scroller lands on the header's title label",
        win.get_focus() is page._title,
        describe(win.get_focus()),
    )
    check("…without selecting it", not selected_labels(page), selected_labels(page))
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
