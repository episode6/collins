#!/usr/bin/env python3
"""End-to-end check that a PR body renders as markdown blocks — dev machine.

The PR page renders bodies through mdblocks (markdown-it-py) and mdwidgets:
headings as sized labels, lists as glyph-plus-content rows with task-list
glyphs, quotes behind a bar, rules as separators, tables as grids of cell
labels in a sideways-only scroller (capped, with a link to the rest; a cell
of pictures a picture scaled to its column), code
blocks as read-only GtkSource views highlighted for the fence's language
and wearing the page's style scheme, GitHub references (#123,
owner/repo#123, @user, a commit's hex) and relative links as links into
the page's own repository, <details> as expanders that build their
children on first opening (an unmatched one stays literal text), and
GitHub's alerts as colored columns under an icon-and-title row.
The fold's preview cut, the "Show more" step and the regex fallback ride
the same walk. None of that is reachable from pytest (tests/conftest.py
blocks the GTK stack), so it is checked here against the real page in a
real window:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_pr_body_blocks.py

`prdetail.fetch` is stubbed with a canned detail — a description made of
the block fixture, one comment — so nothing leaves the machine. Grown per
PR of the markdown stack.

Run it behind the headless wrapper, or a window opens on the user's screen.
"""

import os
import re
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
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
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from collins import i18n, mdblocks, mdwidgets, pictures, prdetail, prview  # noqa: E402
from collins.app import apply_gtk_settings  # noqa: E402
from collins.editor import GtkSource  # noqa: E402
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


def ancestors(widget: Gtk.Widget) -> list[Gtk.Widget]:
    out = []
    parent = widget.get_parent()
    while parent is not None:
        out.append(parent)
        parent = parent.get_parent()
    return out


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
    icon = find(quotes[0], lambda w: isinstance(w, Gtk.Image)) if quotes else None
    check(
        "…under GitHub's note icon, the column wearing the kind's class",
        icon is not None and icon.get_icon_name() == "alert-note-symbolic"
        and quotes and has_class(quotes[0], "pr-md-alert-note") and has_class(quotes[0], "pr-md-alert"),
        icon.get_icon_name() if icon else None,
    )
    check("…and the plain quote wears no alert class", quotes and not has_class(quotes[1], "pr-md-alert"))
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
        check("…the header row wearing .pr-md-th (its bold is the CSS's alone)",
              [c.get_text() for c in heads] == ["col", "val"]
              and not any("<b>" in c.get_label() for c in heads))
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
    check("the fence's source is gone from the labels", not any('print("hi")' in t for t in all_texts))
    views = findall(full, lambda w: isinstance(w, GtkSource.View))
    check("the fence renders as a GtkSource view", len(views) == 1, len(views))
    if views:
        view = views[0]
        buffer = view.get_buffer()
        language = buffer.get_language()
        check("…with the python3 language", language is not None and language.get_id() == "python3",
              language.get_id() if language else None)
        check("…holding the fence's text",
              buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True) == 'print("hi")')
        check("…read-only, no cursor, monospace",
              not view.get_editable() and not view.get_cursor_visible() and view.get_monospace())
        check("…no line numbers", not view.get_show_line_numbers())
        check("…wearing .pr-md-code", has_class(view, "pr-md-code"))
        scheme = buffer.get_style_scheme()
        check("…wearing the page's style scheme, not GtkSource's classic default",
              scheme is not None and scheme.get_id() in ("Adwaita", "Adwaita-dark"),
              scheme.get_id() if scheme else None)
        state["scheme_before"] = scheme.get_id() if scheme else None
        scroller = view.get_parent()
        check(
            "…inside a scroller that scrolls sideways only, at its natural height",
            isinstance(scroller, Gtk.ScrolledWindow)
            and scroller.get_policy() == (Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
            and scroller.get_propagate_natural_height()
            and has_class(scroller, "pr-md-code-scroller"),
            type(scroller).__name__,
        )
        check("…as tall as its one line, not the page", 10 < view.get_height() < 80, view.get_height())
        # A scheme change restyles the live view, as it does the diff buffers.
        page._scheme_setting = "classic"
        page._apply_scheme()
        after = buffer.get_style_scheme()
        check("…and follows the editor's scheme setting", after is not None and after.get_id() == "classic",
              after.get_id() if after else None)
        page._scheme_setting = ""
        page._apply_scheme()
    expanders = findall(full, lambda w: isinstance(w, Gtk.Expander))
    check("the <details> renders as an expander", len(expanders) == 1, len(expanders))
    check("…its source gone from the labels", not any(t.startswith("<details>") for t in all_texts))
    if expanders:
        expander = expanders[0]
        summary = expander.summary_label
        check("…wearing the summary", summary.get_label() == "More", summary.get_label())
        check(
            "…as a wrapping label, not the expander's own",
            expander.get_label_widget() is summary and summary.get_wrap()
            and "pr-md-details-summary" in summary.get_css_classes(),
        )
        check("…collapsed by default", not expander.get_expanded())
        check("…its children not built until opened", "hidden text" not in all_texts, all_texts)
        expander.set_expanded(True)
        inner = [w for w in labels(expander) if has_class(w, "pr-md-text")]
        check(
            "…and built as blocks on opening",
            [w.get_text() for w in inner] == ["hidden text"],
            [w.get_text() for w in inner],
        )
        all_texts = texts(full)
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
    # GitHub references, under the page's own repository and head commit:
    # the ones GitHub links, the ones it doesn't, and the places (a code
    # span, an author's own link) a reference is never touched in.
    return land(replace(STAGED["detail"], body=REFS_BODY, head_oid=HEAD), step_refs)


HEAD = "0123456789abcdef0123456789abcdef01234567"
REFS_BODY = (
    "Fixes #12 and other/repo#3, thanks @octocat; see 0123abc and `#99`, "
    "x#5, deadbeef, [#7](https://example.com/z) and [the guide](docs/guide.md)."
)


def step_refs() -> bool:
    page = state["page"]
    card = description_card(page)
    body = [w for w in labels(card) if has_class(w, "pr-md-text")]
    check("the references body is one paragraph label", len(body) == 1, len(body))
    if not body:
        return done()
    markup = body[0].get_label()
    hrefs = re.findall(r'<a href="([^"]*)">([^<]*)</a>', markup)
    check(
        "#123, owner/repo#123, @user and a commit link into the page's repository",
        hrefs[:4]
        == [
            ("https://github.com/episode6/collins/issues/12", "#12"),
            ("https://github.com/other/repo/issues/3", "other/repo#3"),
            ("https://github.com/octocat", "@octocat"),
            ("https://github.com/episode6/collins/commit/0123abc", "0123abc"),
        ],
        hrefs,
    )
    check(
        "…a reference in a code span or on a word's tail, and a hex word, stay text",
        "<tt>#99</tt>" in markup and "x#5" in markup and "deadbeef" in markup
        and "issues/99" not in markup and "issues/5" not in markup and "deadbeef</a>" not in markup,
        markup,
    )
    check(
        "…an author's own link keeps its destination",
        ("https://example.com/z", "#7") in hrefs and "issues/7" not in markup,
        hrefs,
    )
    check(
        "…and a relative link reads the file at the head commit",
        (f"https://github.com/episode6/collins/blob/{HEAD}/docs/guide.md", "the guide") in hrefs,
        hrefs,
    )
    check("…all of it well-formed markup", body[0].get_text().startswith("Fixes #12 and other/repo#3"))
    # Fence languages the alias map doesn't name: one GtkSource knows by
    # that word, a `suggestion` fence, a word nobody knows.
    fences = "```kotlin\nval a = 1\n```\n\n```suggestion\nx\n```\n\n```nosuchlang-2\ny\n```"
    return land(replace(STAGED["detail"], body=fences), step_code_languages)


def step_code_languages() -> bool:
    page = state["page"]
    card = description_card(page)
    views = findall(card, lambda w: isinstance(w, GtkSource.View))
    ids = [
        (lang.get_id() if (lang := v.get_buffer().get_language()) is not None else None) for v in views
    ]
    check("three fences, three views", len(views) == 3, len(views))
    check(
        "a word GtkSource knows highlights, suggestion and unknown words stay plain",
        ids == ["kotlin", None, None],
        ids,
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
    # The same table nested in a list item and in a quote: the link still
    # carries the page's URL through `_list` / `_quote`'s recursion.
    rows = mdblocks.TABLE_MAX_ROWS + 3
    tall = "| h |\n|---|\n" + "\n".join(f"| r{r} |" for r in range(rows))
    nested = "- item\n\n" + "\n".join("  " + line for line in tall.split("\n"))
    quoted = "\n".join("> " + line for line in tall.split("\n"))
    return land(replace(STAGED["detail"], body=nested + "\n\n" + quoted), step_table_nested_caps)


def step_table_nested_caps() -> bool:
    page = state["page"]
    full = fold(page)._full
    grids = findall(full, lambda w: isinstance(w, Gtk.Grid))
    check("a capped table nested in a list and in a quote renders both grids", len(grids) == 2, len(grids))
    links = findall(full, lambda w: has_class(w, "pr-md-table-more"))
    check(
        "…each with its rest counted in a link to the PR on GitHub",
        len(links) == 2
        and all(link.get_text() == "3 more rows on GitHub" for link in links)
        and all(f'href="{PR_URL}"' in link.get_label() for link in links),
        [(link.get_text(), link.get_label()[:80]) for link in links],
    )
    in_list = bool(links) and any(has_class(w, "pr-md-list") for w in ancestors(links[0]))
    in_quote = bool(links) and any(has_class(w, "pr-md-quote") for w in ancestors(links[-1]))
    check("…inside the list row and the quote column", in_list and in_quote, (in_list, in_quote))
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
    return land(replace(STAGED["detail"], body=DETAILS_BODY), step_details)


# <details> in its shapes — open, closed around a fence, unmatched — and
# one alert of every kind.
ALERT_KINDS = ("note", "tip", "important", "warning", "caution")
ALERT_ICONS = {
    "note": "alert-note-symbolic",
    "tip": "alert-tip-symbolic",
    "important": "alert-important-symbolic",
    "warning": "alert-symbolic",
    "caution": "alert-caution-symbolic",
}
DETAILS_BODY = (
    "<details open>\n<summary>Open &amp; shown</summary>\n\nshown at once\n\n</details>\n\n"
    "<details>\n<summary>Closed with code</summary>\n\n```python\ny = 2\n```\n\n</details>\n\n"
    + "\n\n".join(f"> [!{kind.upper()}]\n> the {kind} body" for kind in ALERT_KINDS)
    + "\n\n<details>\n<summary>No close</summary>\n\nafter the unmatched"
)


def step_details() -> bool:
    page = state["page"]
    the_fold = fold(page)
    if the_fold is not None and not the_fold.expanded:
        # The details bodies fold; the blocks live in the expanded half.
        the_fold.set_expanded(True)
        return later(step_details)
    card = the_fold._full if the_fold is not None else description_card(page)
    expanders = findall(card, lambda w: isinstance(w, Gtk.Expander))
    check("two matched <details> are expanders", len(expanders) == 2, len(expanders))
    if len(expanders) != 2:
        return done()
    opened, closed = expanders
    check("<details open> starts expanded", opened.get_expanded())
    check(
        "…its summary's entity read back",
        opened.summary_label.get_label() == "Open &amp; shown" and "Open & shown" in texts(card),
        opened.summary_label.get_label(),
    )
    check("…its children built at once", "shown at once" in texts(opened), texts(opened))
    check("the closed one is collapsed", not closed.get_expanded())
    check("…holding no source view yet", not findall(closed, lambda w: isinstance(w, GtkSource.View)))
    # A scheme change before the expander opens reaches the view it builds.
    page._scheme_setting = "classic"
    page._apply_scheme()
    closed.set_expanded(True)
    views = findall(closed, lambda w: isinstance(w, GtkSource.View))
    scheme = views[0].get_buffer().get_style_scheme() if views else None
    check(
        "…and builds its fence as a view wearing the page's current scheme when opened",
        len(views) == 1 and scheme is not None and scheme.get_id() == "classic",
        (len(views), scheme.get_id() if scheme else None),
    )
    page._scheme_setting = ""
    page._apply_scheme()
    after = views[0].get_buffer().get_style_scheme() if views else None
    check("…and follows the scheme back", after is not None and after.get_id() != "classic",
          after.get_id() if after else None)
    all_texts = texts(card)
    check(
        "an unmatched <details> is literal text",
        any(t.startswith("<details>") and "No close" in t for t in all_texts),
        all_texts,
    )
    check("…and does not eat the body after it", "after the unmatched" in all_texts, all_texts[-1])
    # The author's `open` spends the body's own budget: a body of open
    # details hiding thousands of paragraphs builds the budget's worth of
    # labels, not all of them. (The build layer alone: the page's render
    # cap would gate a body this long behind "Show more".)
    paragraphs = "\n\n".join("w" for _ in range(300))
    body = "\n\n".join(
        f"<details open>\n<summary>s{i}</summary>\n\n{paragraphs}\n\n</details>" for i in range(20)
    )
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    for widget in mdwidgets.build(mdblocks.parse_blocks(body), mdwidgets.Budget(), lambda _i: Gtk.Box()):
        column.append(widget)
    built = [w for w in walk(column) if has_class(w, "pr-md-text")]
    check(
        "<details open> spends the body's widget budget",
        len(built) <= mdwidgets.WIDGET_BUDGET,
        len(built),
    )
    # A summary as long as the parser allows stays a wrapping label whose
    # minimum width is a word's, so it never sets the page's.
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    long_summary = "<details>\n<summary>" + "x" * 5000 + "</summary>\n\nb\n\n</details>"
    blocks = mdblocks.parse_blocks(long_summary)
    for widget in mdwidgets.build(blocks, mdwidgets.Budget(), lambda _i: Gtk.Box()):
        column.append(widget)
    holder = Gtk.Window(child=column)
    expander = column.get_first_child()
    minimum, natural, *_ = expander.measure(Gtk.Orientation.HORIZONTAL, -1)
    check(
        "a long summary is cut and wraps, never widening the page",
        len(expander.summary_label.get_text()) == mdblocks.SUMMARY_MAX and minimum < 80,
        (len(expander.summary_label.get_text()), minimum, natural),
    )
    holder.destroy()
    alerts = [w for w in walk(card) if has_class(w, "pr-md-alert")]
    check("one alert column per kind", len(alerts) == len(ALERT_KINDS), len(alerts))
    kinds = [next((k for k in ALERT_KINDS if has_class(a, f"pr-md-alert-{k}")), None) for a in alerts]
    check("…each wearing its kind's class, in order", kinds == list(ALERT_KINDS), kinds)
    icons = [
        (i.get_icon_name() if (i := find(a, lambda w: isinstance(w, Gtk.Image))) is not None else None)
        for a in alerts
    ]
    check("…each under GitHub's icon for the kind", icons == [ALERT_ICONS[k] for k in ALERT_KINDS], icons)
    titles = [
        (t.get_text() if (t := find(a, lambda w: has_class(w, "pr-md-alert-title"))) is not None else None)
        for a in alerts
    ]
    check(
        "…titled Note / Tip / Important / Warning / Caution",
        titles == ["Note", "Tip", "Important", "Warning", "Caution"],
        titles,
    )
    check(
        "…the marker gone, the body kept",
        all(
            f"the {k} body" in texts(a) and not any("[!" in t for t in texts(a))
            for k, a in zip(ALERT_KINDS, alerts, strict=True)
        ),
        [texts(a) for a in alerts],
    )
    check("…each quote also a bordered column", all(has_class(a, "pr-md-quote") for a in alerts))
    return land(replace(STAGED["detail"], body=INLINE_BODY), step_inline_html)


# The inline HTML whitelist — <kbd>, <sub>, <sup>, <br> — beside a tag
# outside it, a <br> on a line of its own, and a fence to copy.
INLINE_BODY = (
    "Press <kbd>Ctrl</kbd>+<kbd>C</kbd> for H<sub>2</sub>O and x<sup>2</sup><br>next line, "
    "then <span>not</span>.\n\n<br>\n\n```sh\necho copy me\n```"
)


def step_inline_html() -> bool:
    page = state["page"]
    card = description_card(page)
    body = [w for w in labels(card) if has_class(w, "pr-md-text")]
    check("the inline HTML body is one paragraph label, the <br> line dropped", len(body) == 1, len(body))
    if not body:
        return done()
    markup, text = body[0].get_label(), body[0].get_text()
    check(
        "<kbd> is monospace, <sub> and <sup> themselves, <br> a newline",
        "<tt>Ctrl</tt>+<tt>C</tt>" in markup and "H<sub>2</sub>O" in markup and "x<sup>2</sup>" in markup
        and "x2\nnext line" in text,
        markup,
    )
    check("…a tag outside the whitelist shows escaped", "<span>not</span>" in text and "&lt;span&gt;" in markup)
    code = find(card, lambda w: isinstance(w, mdwidgets.CodeBlockView))
    check("the fence is a code block view", code is not None)
    if code is None:
        return done()
    check("…its view saying a right-click copies", code.view.get_tooltip_text() == "Right-click to copy")
    check("…the copied pill hidden", not code.copied.get_visible())
    state["code"] = code
    state["copy_tries"] = 0
    return later(step_copy, 50)


def step_copy() -> bool:
    # A clipboard claim right after present() can be dropped under the
    # headless shell (Wayland wants a recent input serial): copy until it
    # takes, as the capture skill's clipboard shots do.
    code = state["code"]
    clipboard = Gdk.Display.get_default().get_clipboard()
    code.copy()
    state["copy_tries"] += 1
    if not clipboard.is_local() and state["copy_tries"] < 40:
        return later(step_copy, 50)
    check("a copy claims the clipboard", clipboard.is_local(), state["copy_tries"])
    check("…and shows the copied pill", code.copied.get_visible() and has_class(code.copied, "pr-md-copied"))

    def on_text(clip, result) -> None:
        try:
            text = clip.read_text_finish(result)
        except GLib.Error as exc:  # noqa: BLE001
            text = f"error: {exc}"
        check("…of the block's whole text", text == "echo copy me", text)
        later(step_copy_settled, 1500)

    clipboard.read_text_async(None, on_text)
    return GLib.SOURCE_REMOVE


def step_copy_settled() -> bool:
    check("…which hides again after its beat", not state["code"].copied.get_visible())
    # A list first in a body: the preview shows the items that fit,
    # the way it shows a paragraph's front.
    entries = "\n".join(f"- entry {n}" for n in range(40))
    return land(replace(STAGED["detail"], body=entries + "\n\nafter the entries"), step_list_cut)


def step_list_cut() -> bool:
    page = state["page"]
    the_fold = fold(page)
    check("a body opening with a long list folds", the_fold is not None)
    if the_fold is None:
        return done()
    preview_glyphs = [w for w in labels(the_fold._preview) if has_class(w, "pr-md-glyph")]
    check(
        "…its preview showing the first items, not nothing",
        0 < len(preview_glyphs) <= prview._FOLD_LINES,
        len(preview_glyphs),
    )
    check("…the first of them entry 0", "entry 0" in texts(the_fold._preview), texts(the_fold._preview))
    check("…and not the paragraph after the list", "after the entries" not in texts(the_fold._preview))
    the_fold.set_expanded(True)
    full_glyphs = [w for w in labels(the_fold._full) if has_class(w, "pr-md-glyph")]
    check("…the expanded half holding every item", len(full_glyphs) == 40, len(full_glyphs))
    # A fence past the code cap: cut, the rest counted in a link.
    lines = [f"line {n}" for n in range(3000)]
    state["fence_content"] = "\n".join(lines) + "\n"
    return land(replace(STAGED["detail"], body="```\n" + "\n".join(lines) + "\n```"), step_code_cap)


def step_code_cap() -> bool:
    page = state["page"]
    the_fold = fold(page)
    if the_fold is not None and not the_fold.expanded:
        the_fold.set_expanded(True)
    card = description_card(page)
    more = find(card, lambda w: isinstance(w, Gtk.Button) and w.get_label() == "Show more")
    check("a fence past the render cap waits behind Show more", more is not None)
    if more is None:
        return done()
    more.emit("clicked")
    code = find(card, lambda w: isinstance(w, mdwidgets.CodeBlockView))
    check("…then renders as a code block view", code is not None)
    if code is None:
        return done()
    buffer = code.view.get_buffer()
    shown = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
    check("…holding the code cap's worth of text", len(shown) <= mdwidgets.CODE_CAP and shown.startswith("line 0"),
          len(shown))
    content = state["fence_content"].rstrip("\n")
    expected = max(1, content.count("\n", mdwidgets.CODE_CAP))
    link = find(card, lambda w: has_class(w, "pr-md-code-more"))
    check(
        "…the lines cut counted in a link to the PR on GitHub",
        link is not None and link.get_text() == f"{expected} more lines on GitHub"
        and f'href="{PR_URL}"' in link.get_label(),
        (link.get_text(), link.get_label()) if link else None,
    )
    check("…the copy still the whole fence", code.text == content)
    # A table of pictures: the screenshot skill's before/after pair, one
    # <img> per cell, and one wider than the picture cap beside it. The
    # pictures are staged into the fetch cache, so no download runs.
    pngs = {}
    for name, rgb in (("before", 0xCC3333FF), ("after", 0x33AA33FF)):
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 800, 500)
        pixbuf.fill(rgb)
        pngs[name] = f"{E2E}/{name}.png"
        pixbuf.savev(pngs[name], "png", [], [])
        pictures._files[f"https://x.example/{name}.png"] = Path(pngs[name])
    cell = "![i](https://x.example/before.png)"
    wide = "| a | b | c | d |\n|---|---|---|---|\n" + "|" + f" {cell} |" * 4
    return land(replace(STAGED["detail"], body=IMAGE_TABLE + "\n\n" + wide), step_image_table)


def step_image_table() -> bool:
    page = state["page"]
    full = description_card(page)
    grids = findall(full, lambda w: isinstance(w, Gtk.Grid))
    check("the image table and the wide one render as grids", len(grids) == 2, len(grids))
    if len(grids) != 2:
        return done()
    grid, wide = grids
    shots = findall(grid, lambda w: isinstance(w, pictures.BoundedPicture))
    check("the before/after cells are pictures, not links", len(shots) == 2, len(shots))
    cells = findall(grid, lambda w: has_class(w, "pr-md-image-cell"))
    check("…in cells wearing the table's own padding class",
          len(cells) == 2 and all(has_class(c, "pr-md-td") for c in cells))
    scroller = grid.get_parent().get_parent()
    width = scroller.get_width()
    hadj = scroller.get_hadjustment()
    widths = [s.get_width() for s in shots]
    check(
        "…each picture scaled to its column: no wider than asked, both fitting the panel side by side",
        len(widths) == 2 and all(0 < w <= 300 for w in widths) and abs(widths[0] - widths[1]) <= 1
        and sum(widths) <= width,
        (widths, width),
    )
    check(
        "…the grid filling the panel rather than scrolling sideways",
        grid.get_width() == width and round(hadj.get_upper()) == round(hadj.get_page_size()),
        (grid.get_width(), width, hadj.get_upper()),
    )
    check("…and the header labels still labels", [c.get_text() for c in labels(grid)] == ["Before", "After"],
          [c.get_text() for c in labels(grid)])
    check("a table wider than the picture cap keeps its images as alt-text links",
          not findall(wide, lambda w: isinstance(w, pictures.BoundedPicture))
          and [c.get_text() for c in labels(wide)][4:] == ["i"] * 4,
          [c.get_text() for c in labels(wide)])
    return done()


IMAGE_TABLE = (
    "| Before | After |\n| --- | --- |\n"
    '| <img src="https://x.example/before.png" width="300" alt="before" /> '
    '| <img src="https://x.example/after.png" width="300" alt="after" /> |'
)


def done() -> bool:
    global exit_code
    print(f"\n{PASSED} passed, {FAILED} failed")
    exit_code = 0 if FAILED == 0 else 1
    app.quit()
    return GLib.SOURCE_REMOVE


app.connect("activate", on_activate)
app.run([])
sys.exit(exit_code)
