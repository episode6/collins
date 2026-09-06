#!/usr/bin/env python3
"""End-to-end check that a PR body renders as markdown blocks — dev machine.

The PR page renders bodies through mdblocks (markdown-it-py) and mdwidgets:
headings as sized labels, lists as glyph-plus-content rows with task-list
glyphs, quotes behind a bar, rules as separators, tables as grids of cell
labels in a sideways-only scroller (capped, with a link to the rest), and
every block the widget layer has no widget for yet as its escaped source.
The fold's preview cut, the "Show more" step and the regex fallback ride
the same walk. None of that is reachable from pytest (tests/conftest.py
blocks the GTK stack), so it is checked here against the real page in a
real window:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_pr_body_blocks.py

`prdetail.fetch` is stubbed with a canned detail — a description made of
the block fixture, one comment — so nothing leaves the machine. Grown per
PR of the markdown stack: a GtkSource.View for code and a Gtk.Expander for
<details> join the assertions as they land.

Run it behind the headless wrapper, or a window opens on the user's screen.
"""

import os
import sys
import tempfile
from dataclasses import replace
from types import SimpleNamespace

E2E = tempfile.mkdtemp(prefix="collins-prblocks-")
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

from collins import i18n, mdblocks, mdwidgets, prdetail, prview  # noqa: E402
from collins.app import apply_gtk_settings  # noqa: E402
from collins.prstatus import PullRequest  # noqa: E402

PR_URL = "https://github.com/episode6/collins/pull/55"
# The fixture: one of every block PR A renders, then enough paragraphs to
# fold. The first paragraph is what the fold's preview must open with.
DESCRIPTION = """First paragraph, with **bold**, `code` and a [link](https://example.com/x).

## Why

- [ ] an open task
- [x] a done task
- a plain bullet with *emphasis*
  - a nested bullet

3. third
4. fourth

> [!NOTE]
> A note quote.

> A plain quote.

---

| col | val |
|-----|----:|
| a   |   1 |

```python
print("hi")
```

<details>
<summary>More</summary>

hidden text

</details>

""" + "\n\n".join(
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


def fake_detail(body: str) -> prdetail.PullRequestDetail:
    pr = PullRequest(number=55, url=PR_URL, repository="episode6/collins", title="A title",
                     state="OPEN")
    comment = prdetail.PrComment(
        author="reviewer", created_at="2026-08-16T09:00:00Z",
        body="A comment with a list:\n\n1. one\n2. two", url="",
    )
    return prdetail.PullRequestDetail(
        summary=pr, body=body, author="me", created_at="2026-08-16T09:00:00Z",
        base_ref="main", head_ref="topic", base_oid="a", head_oid="b", head_repository="",
        additions=1, deletions=1, changed_files=0, labels=(), checks=(), timeline=(comment,),
        files=(), threads=(), viewer_is_author=True,
    )


STAGED = {"detail": fake_detail(DESCRIPTION)}
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


def findall(widget: Gtk.Widget, pred) -> list[Gtk.Widget]:
    return [w for w in walk(widget) if pred(w)]


def labels(widget: Gtk.Widget) -> list[Gtk.Label]:
    return findall(widget, lambda w: isinstance(w, Gtk.Label))


def texts(widget: Gtk.Widget) -> list[str]:
    return [label.get_text() for label in labels(widget)]


def has_class(widget: Gtk.Widget, name: str) -> bool:
    return name in widget.get_css_classes()


def description_card(page) -> Gtk.Widget:
    return page._content_slots.widgets[0]


def fold(page) -> prview._Fold:
    return page._description_fold


# -- the run ----------------------------------------------------------------

i18n.init("en")
app = Adw.Application(application_id=os.environ["COLLINS_APP_ID"])
exit_code = 1
state: dict = {}


def later(fn, ms: int = 150) -> bool:
    GLib.timeout_add(ms, fn)
    return GLib.SOURCE_REMOVE


def land(detail: prdetail.PullRequestDetail, then) -> bool:
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
    check("the block layer is available", mdblocks.available())
    the_fold = fold(page)
    check("the description folds", the_fold is not None)
    if the_fold is None:
        return done()
    # The preview: the first paragraph, as a selectable label with its
    # inline markup, opening the card.
    first = find(the_fold._preview, lambda w: isinstance(w, Gtk.Label))
    check(
        "the preview opens with the first paragraph as a label",
        first is not None and first.get_text().startswith("First paragraph, with bold, code"),
        first.get_text() if first else None,
    )
    check("…selectable", first is not None and first.get_selectable())
    check("…with its link", first is not None and "example.com" in (first.get_label() or ""))
    check(
        "…and the Why heading in a sized label",
        any(has_class(w, "pr-md-h2") and w.get_text() == "Why" for w in labels(the_fold._preview)),
        texts(the_fold._preview),
    )
    the_fold.set_expanded(True)
    return later(step_expanded)


def step_expanded() -> bool:
    page = state["page"]
    full = fold(page)._full
    check("the expanded half is shown", full.get_visible())
    glyphs = [w.get_text() for w in labels(full) if has_class(w, "pr-md-glyph")]
    check("task-list items wear their glyphs", glyphs[:2] == ["☐", "☑"], glyphs)
    check("bullets wear a dot", "•" in glyphs, glyphs)
    check("the ordered list counts from its start", ["3.", "4."] == [g for g in glyphs if g.endswith(".")], glyphs)
    checked = [w for w in labels(full) if has_class(w, "pr-md-glyph") and w.get_text() == "☑"]
    check("a done task's glyph is dim", bool(checked) and has_class(checked[0], "dim-label"))
    item_texts = [w.get_text() for w in labels(full) if has_class(w, "pr-md-text")]
    check(
        "the task marker is gone from the item's text",
        "an open task" in item_texts and "a done task" in item_texts,
        item_texts,
    )
    check(
        "a nested bullet is structural, inside its parent's row",
        any(
            has_class(w, "pr-md-item")
            and find(w, lambda c: isinstance(c, Gtk.Box) and has_class(c, "pr-md-list")) is not None
            for w in walk(full)
        ),
    )
    quotes = [w for w in walk(full) if has_class(w, "pr-md-quote")]
    check("both quotes are bordered columns", len(quotes) == 2, len(quotes))
    check(
        "the alert quote carries its title, the marker gone",
        quotes and "Note" in texts(quotes[0]) and not any("[!NOTE]" in t for t in texts(quotes[0])),
        [texts(q) for q in quotes],
    )
    check("the rule is a separator", find(full, lambda w: isinstance(w, Gtk.Separator)) is not None)
    all_texts = texts(full)
    grid = find(full, lambda w: isinstance(w, Gtk.Grid))
    check("the table renders as a grid", grid is not None)
    check("…its source gone", not any(t.startswith("| col | val |") for t in all_texts), all_texts)
    if grid is not None:
        cells = labels(grid)
        check("…of one label per cell", [c.get_text() for c in cells] == ["col", "val", "a", "1"],
              [c.get_text() for c in cells])
        heads = [c for c in cells if has_class(c, "pr-md-th")]
        check("…the header row wearing .pr-md-th, bold", [c.get_text() for c in heads] == ["col", "val"]
              and all("<b>" in c.get_label() for c in heads))
        check("…the right-aligned column aligned right",
              [c.get_xalign() for c in cells] == [0.0, 1.0, 0.0, 1.0], [c.get_xalign() for c in cells])
        check("…cells selectable", all(c.get_selectable() for c in cells))
        scroller = grid.get_parent().get_parent() if grid.get_parent() is not None else None
        check(
            "…inside a scroller that scrolls sideways only",
            isinstance(scroller, Gtk.ScrolledWindow)
            and scroller.get_policy() == (Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
            and scroller.get_propagate_natural_height(),
            type(scroller).__name__,
        )
        check("…as tall as its rows, not the page", grid is not None and 20 < grid.get_height() < 120,
              grid.get_height())
        check("…with no more-link under a table that fits",
              not findall(full, lambda w: has_class(w, "pr-md-table-more")))
    check(
        "the fence renders as monospace text for now",
        any(t == 'print("hi")' and has_class(w, "pr-md-code") for w, t in zip(labels(full), all_texts, strict=True)),
    )
    check(
        "the <details> renders as its source for now",
        any(t.startswith("<details>") and "hidden text" in t for t in all_texts),
    )
    check("no label is blank (bad markup blanks a GTK 4 label)", all(t.strip() for t in all_texts), all_texts)
    check(
        "the last paragraph is there",
        any(t.startswith("Paragraph 15") for t in all_texts),
    )
    # The comment card renders through the same layer.
    comment = next((w for w in page._content_slots.widgets if "reviewer" in texts(w)), full)
    check(
        "a comment's ordered list counts",
        [w.get_text() for w in labels(comment) if has_class(w, "pr-md-glyph")] == ["1.", "2."],
        texts(comment),
    )
    # A body markdown-it refuses falls back to the regex renderer, per body.
    original = mdblocks.parse_blocks

    def refuse(text, images=True):
        raise ValueError("staged refusal")

    mdblocks.parse_blocks = refuse
    state["restore"] = original
    return land(replace(STAGED["detail"], body="**Fallback** body\n\n- item"), step_fallback)


def step_fallback() -> bool:
    page = state["page"]
    mdblocks.parse_blocks = state["restore"]
    card = description_card(page)
    first = find(card, lambda w: isinstance(w, Gtk.Label) and w.get_text().startswith("Fallback"))
    check("a refused body still renders, through the regex pipeline", first is not None, texts(card))
    check(
        "…as one label of the whole body",
        first is not None and "• item" in first.get_text(),
        first.get_text() if first else None,
    )
    check("…with no block widgets", not any(has_class(w, "pr-md-glyph") for w in walk(card)))
    # And back: the block layer again, unfolded — a short body, no fold.
    return land(replace(STAGED["detail"], body="# Back\n\n- [x] done"), step_back)


def step_back() -> bool:
    page = state["page"]
    card = description_card(page)
    check("a short body has no fold", fold(page) is None)
    check(
        "…and renders its blocks",
        any(has_class(w, "pr-md-h1") and w.get_text() == "Back" for w in labels(card))
        and any(has_class(w, "pr-md-glyph") and w.get_text() == "☑" for w in labels(card)),
        texts(card),
    )
    # A table past the caps: 50 rows of 8 columns drawn, the rest a link.
    width = mdblocks.TABLE_MAX_COLUMNS + 2
    rows = mdblocks.TABLE_MAX_ROWS + 10
    wide = (
        "| " + " | ".join(f"h{c}" for c in range(width)) + " |\n|" + "---|" * width + "\n"
        + "\n".join("| " + " | ".join(f"r{r}c{c}" for c in range(width)) + " |" for r in range(rows))
    )
    return land(replace(STAGED["detail"], body="Before.\n\n" + wide + "\n\nAfter."), step_table_caps)


def step_table_caps() -> bool:
    page = state["page"]
    full = fold(page)._full
    grid = find(full, lambda w: isinstance(w, Gtk.Grid))
    check("a big table still renders as a grid", grid is not None)
    if grid is None:
        return done()
    cells = labels(grid)
    check(
        "…capped at the row and column caps",
        len(cells) == (mdblocks.TABLE_MAX_ROWS + 1) * mdblocks.TABLE_MAX_COLUMNS,
        len(cells),
    )
    last = f"r{mdblocks.TABLE_MAX_ROWS - 1}c{mdblocks.TABLE_MAX_COLUMNS - 1}"
    check("…its last cell the last capped row's", cells[-1].get_text() == last, cells[-1].get_text())
    more = find(full, lambda w: has_class(w, "pr-md-table-more"))
    check(
        "…the rest counted in a link to the PR on GitHub",
        more is not None
        and more.get_text() == "10 more rows on GitHub, 2 more columns on GitHub"
        and f'href="{PR_URL}"' in more.get_label(),
        (more.get_text(), more.get_label()) if more else None,
    )
    check("…dim", more is not None and has_class(more, "dim-label"))
    check("…and the paragraph after it there", "After." in texts(full), texts(full)[-1])
    # The widget budget: a body past it renders its tail as one plain
    # label — never dropped, never a widget per item (the body itself
    # sits under the render cap, so this is the widget budget alone).
    items = "\n".join(f"- item {n}" for n in range(1500))
    return land(replace(STAGED["detail"], body=items + "\n\nafter the list"), step_budget)


def step_budget() -> bool:
    page = state["page"]
    full = fold(page)._full
    widgets = list(walk(full))
    check("a 1500-item list builds a bounded widget tree", len(widgets) < 1500, len(widgets))
    all_texts = texts(full)
    check(
        "…its tail kept as plain text inside the list",
        any(t.endswith("item 1499") and "item 1000" in t for t in all_texts),
        [t[-60:] for t in all_texts][-2:],
    )
    check("…and the paragraph after it too", "after the list" in all_texts, all_texts[-1][-60:])
    check(
        "…with no Show more button past the render cap",
        not findall(full, lambda w: isinstance(w, Gtk.Button)),
    )
    paragraphs = "\n\n".join(f"p{n}" for n in range(500))
    return land(replace(STAGED["detail"], body=paragraphs), step_budget_paragraphs)


def step_budget_paragraphs() -> bool:
    page = state["page"]
    full = fold(page)._full
    body_labels = [w for w in labels(full) if has_class(w, "pr-md-text")]
    check(
        "500 paragraphs: the widget budget's worth of labels, then one of the rest",
        len(body_labels) == mdwidgets.WIDGET_BUDGET + 1,
        len(body_labels),
    )
    check("…ending with the last paragraph", body_labels and body_labels[-1].get_text().endswith("p499"))
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
