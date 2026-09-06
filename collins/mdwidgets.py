"""Markdown blocks (mdblocks) as GTK widgets — the PR page's render layer.

One widget per block: paragraphs and headings are the selectable wrapped
labels the page always used (a heading's markup wrapped in a size span),
a list is a column of glyph-plus-content rows that nests structurally, a
quote a bordered column, a rule a separator, a table a grid of cell labels
that scrolls sideways on its own (the page body never does), an image row
whatever the page's own image slot builder makes of it. Blocks with no
widget of their own yet — code blocks, `<details>` — render as a label of
their escaped source, monospace for code: visibly plain, never dropped.

A widget budget bounds what one body can build (`Budget`): past it, the
rest of the blocks become one plain label of their source. The cap is on
*layout*, which is the surface a hostile body attacks once its text is
escaped — ten thousand one-item lists are ten thousand rows and thirty
thousand widgets without it. Real bodies (a few KB) sit far under.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from . import mdblocks  # noqa: E402
from .formatting import markup_ok  # noqa: E402
from .i18n import N_, _  # noqa: E402

# Leaf widgets one body may build before the rest render as plain text.
WIDGET_BUDGET = 400
# Heading sizes as Pango percentages: h1–h3 step down, h4–h6 are bold at
# the reading size. They compose with the page's font-scale provider
# (prview._apply_font_scale), which multiplies through CSS inheritance.
_HEADING_SIZES = {1: "160%", 2: "140%", 3: "120%"}
_TASK_GLYPHS = {False: "☐", True: "☑"}
_ALERT_TITLES = {
    "note": N_("Note"),
    "tip": N_("Tip"),
    "important": N_("Important"),
    "warning": N_("Warning"),
    "caution": N_("Caution"),
}


class Budget:
    """How many leaf widgets a fill may still build."""

    def __init__(self, leaves: int = WIDGET_BUDGET) -> None:
        self.left = leaves

    def take(self) -> bool:
        """Claim one leaf; False once the budget is gone."""
        if self.left <= 0:
            return False
        self.left -= 1
        return True


def build(
    blocks: list,
    budget: Budget,
    image_row: Callable[[tuple], Gtk.Widget],
    depth: int = 0,
    page_url: str = "",
) -> list[Gtk.Widget]:
    """Widgets for *blocks*, in order, spending *budget*; *image_row* is
    what turns an `ImageRow`'s images into a widget (the page's own slot
    builder); *page_url* is where the body lives on GitHub — what a capped
    table's "more" link opens. Once the budget runs dry the remaining
    blocks come back as a single plain label of their source."""
    widgets: list[Gtk.Widget] = []
    for index, block in enumerate(blocks):
        if budget.left <= 0:
            rest = rest_source(blocks[index:])
            if rest:
                widgets.append(plain_label(rest))
            break
        widgets.append(build_one(block, budget, image_row, depth, page_url))
    return widgets


def rest_source(blocks: list) -> str:
    """The source of *blocks* as one text — what the tail past the widget
    budget renders as, in `build` and in the page's own block walk."""
    return "\n\n".join(block.source for block in blocks if block.source)


def build_one(
    block,
    budget: Budget,
    image_row: Callable[[tuple], Gtk.Widget],
    depth: int = 0,
    page_url: str = "",
) -> Gtk.Widget:
    """The widget for one block. Containers recurse through `build`."""
    if isinstance(block, mdblocks.Text):
        budget.take()
        return text_label(block.markup, block.source)
    if isinstance(block, mdblocks.Heading):
        budget.take()
        return heading_label(block)
    if isinstance(block, mdblocks.ImageRow):
        budget.take()
        return image_row(block.images)
    if isinstance(block, mdblocks.Rule):
        budget.take()
        rule = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        rule.add_css_class("pr-md-rule")
        return rule
    if isinstance(block, mdblocks.ListBlock):
        return _list(block, budget, image_row, depth, page_url)
    if isinstance(block, mdblocks.Quote):
        return _quote(block, budget, image_row, depth, page_url)
    if isinstance(block, mdblocks.Table):
        return _table(block, budget, page_url)
    if isinstance(block, mdblocks.CodeBlock):
        budget.take()
        label = text_label(f"<tt>{GLib.markup_escape_text(block.text.rstrip())}</tt>", block.text)
        label.add_css_class("pr-md-code")
        return label
    # <details> waits for its own widget: its source, escaped.
    budget.take()
    return plain_label(block.source)


def text_label(markup: str, source: str = "") -> Gtk.Label:
    """The page's reading label: selectable, wrapped, left-aligned. The
    markup is well-formed by construction (mdblocks closes every tag it
    opens); should it ever not be, the escaped source shows instead — GTK
    4's set_markup blanks a label on bad markup rather than raising."""
    label = Gtk.Label(xalign=0.0, selectable=True, wrap=True, hexpand=True)
    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    label.add_css_class("pr-md-text")
    label.set_markup(markup if markup_ok(markup) else GLib.markup_escape_text(source))
    return label


def plain_label(text: str) -> Gtk.Label:
    """Escaped source as a label — the budget's and the not-yet-rendered
    blocks' fallback."""
    return text_label(GLib.markup_escape_text(text), text)


def heading_label(block: mdblocks.Heading) -> Gtk.Label:
    size = _HEADING_SIZES.get(block.level)
    inner = f"<b>{block.markup}</b>"
    markup = f'<span size="{size}">{inner}</span>' if size else inner
    label = text_label(markup, block.source)
    label.add_css_class("pr-md-heading")
    label.add_css_class(f"pr-md-h{block.level}")
    return label


def _list(
    block: mdblocks.ListBlock, budget: Budget, image_row, depth: int, page_url: str = ""
) -> Gtk.Widget:
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
    column.add_css_class("pr-md-list")
    if block.ordered:
        last = block.start + len(block.items) - 1
        width = len(str(max(last, block.start))) + 1
    for position, item in enumerate(block.items):
        if budget.left <= 0:
            # The budget bounds items too, not only what is inside them: a
            # ten-thousand-item list is ten thousand rows and glyphs. The
            # items left become one plain label of their paragraphs.
            rest = rest_source([child for rest in block.items[position:] for child in rest.children])
            if rest:
                column.append(plain_label(rest))
            break
        if item.check is not None:
            glyph = _TASK_GLYPHS[item.check]
        elif block.ordered:
            glyph = f"{block.start + position}."
        else:
            glyph = "•"
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, hexpand=True)
        row.add_css_class("pr-md-item")
        mark = Gtk.Label(label=glyph, xalign=1.0 if block.ordered else 0.5)
        mark.set_valign(Gtk.Align.START)
        mark.add_css_class("pr-md-glyph")
        if block.ordered:
            mark.set_width_chars(width)
        if item.check is not None:
            mark.add_css_class("pr-md-task")
            if item.check:
                mark.add_css_class("dim-label")
        budget.take()
        row.append(mark)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        for widget in build(list(item.children), budget, image_row, depth + 1, page_url):
            content.append(widget)
        row.append(content)
        column.append(row)
    return column


def _quote(
    block: mdblocks.Quote, budget: Budget, image_row, depth: int, page_url: str = ""
) -> Gtk.Widget:
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
    column.add_css_class("pr-md-quote")
    if block.kind != "plain":
        column.add_css_class(f"pr-md-alert-{block.kind}")
        title = Gtk.Label(label=_(_ALERT_TITLES.get(block.kind, "Note")), xalign=0.0)
        title.add_css_class("caption-heading")
        title.add_css_class("pr-md-alert-title")
        budget.take()
        column.append(title)
    for widget in build(list(block.children), budget, image_row, depth + 1, page_url):
        column.append(widget)
    return column


def _table(block: mdblocks.Table, budget: Budget, page_url: str = "") -> Gtk.Widget:
    """A grid of cell labels — the header row bold, each column's text
    aligned as the delimiter row asked — inside a scroller that scrolls
    sideways only and takes its natural height (the pattern the Files
    view's patch scroller uses), so a wide table scrolls within its own
    band and the page body never does. Rows and columns past
    `mdblocks.TABLE_MAX_ROWS` / `TABLE_MAX_COLUMNS` — or past the widget
    budget, which each row spends one leaf of (the columns are capped at
    eight, a constant factor, the way a list item's glyph rides along
    with its label) — are a dim link to the rest on GitHub, under the
    grid. Cells are inline-only markup (mdblocks degrades an image in one
    to its alt-text anchor), each wrapping past `_CELL_WRAP_CHARS` so one
    long cell can't make the grid a mile wide."""
    shown, more_rows, more_columns = mdblocks.cap_table(block)
    grid = Gtk.Grid(column_spacing=0, row_spacing=0)
    grid.add_css_class("pr-md-table")
    grid.set_halign(Gtk.Align.START)
    budget.take()
    for column, cell in enumerate(shown.header):
        label = _cell(f"<b>{cell}</b>", shown.aligns[column])
        label.add_css_class("pr-md-th")
        grid.attach(label, column, 0, 1, 1)
    for index, row in enumerate(shown.rows):
        if not budget.take():
            # The budget bounds rows too, not only the caps: what is left
            # of the table joins the count in the link.
            more_rows += len(shown.rows) - index
            break
        for column, cell in enumerate(row):
            label = _cell(cell, shown.aligns[column])
            label.add_css_class("pr-md-td")
            grid.attach(label, column, index + 1, 1, 1)
    scroller = _TableScroller(child=grid, hexpand=True)
    scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
    scroller.set_propagate_natural_height(True)
    # The viewport gives its child its *minimum* width by default, which
    # for wrapping labels is a character or two: the grid would squeeze
    # every column to a sliver and never scroll. Natural policy starts the
    # scrolling where the grid's natural width overruns the panel's.
    scroller.get_child().set_hscroll_policy(Gtk.ScrollablePolicy.NATURAL)
    scroller.add_css_class("pr-md-table-scroller")
    if not more_rows and not more_columns:
        return scroller
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
    box.append(scroller)
    box.append(_more_link(more_rows, more_columns, page_url))
    return box


class _TableScroller(Gtk.ScrolledWindow):
    """The table's scroller, measured as tall as its grid is at the grid's
    natural width. A Gtk.ScrolledWindow asks its child for a height at
    width -1, which a height-for-width grid of wrapping labels answers
    with its height at its *minimum* width — every cell wrapped a word
    per line, a two-row table four thousand pixels tall. The grid is
    allocated its natural width whatever the panel's (halign START inside
    a viewport that scrolls where that overruns), so the height at that
    width is the height it will draw."""

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        grid = self.get_child().get_child() if self.get_child() is not None else None
        if orientation == Gtk.Orientation.VERTICAL and grid is not None:
            _, width, _, _ = grid.measure(Gtk.Orientation.HORIZONTAL, -1)
            minimum, natural, _, _ = grid.measure(Gtk.Orientation.VERTICAL, width)
            return minimum, natural, -1, -1
        return Gtk.ScrolledWindow.do_measure(self, orientation, for_size)


# Where a cell wraps, in characters: wide enough for a sentence, narrow
# enough that one long cell can't push the grid — and the scrollbar the
# reader must drag to see the rest — out to the width of a paragraph.
_CELL_WRAP_CHARS = 60


def _cell(markup: str, align: str | None) -> Gtk.Label:
    """One table cell: the page's selectable label, aligned per its column
    and wrapping past `_CELL_WRAP_CHARS`; a bad markup shows the escaped
    text rather than blanking the cell."""
    label = Gtk.Label(selectable=True, wrap=True, yalign=0.0)
    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    label.set_max_width_chars(_CELL_WRAP_CHARS)
    label.set_xalign({"center": 0.5, "right": 1.0}.get(align, 0.0))
    label.set_halign(Gtk.Align.FILL)
    label.set_markup(markup if markup_ok(markup) else GLib.markup_escape_text(markup))
    return label


def _more_link(more_rows: int, more_columns: int, page_url: str) -> Gtk.Label:
    """The dim line under a capped table: the counts of what the grid left
    out, linked to *page_url* — the body's own place on GitHub — when
    there is an http(s) one to link to. Plain `_()` strings with the count
    formatted in: the translations carry no plurals."""
    parts = []
    if more_rows:
        parts.append(_("{n} more rows on GitHub").format(n=more_rows))
    if more_columns:
        parts.append(_("{n} more columns on GitHub").format(n=more_columns))
    text = GLib.markup_escape_text(", ".join(parts))
    label = Gtk.Label(xalign=0.0)
    label.add_css_class("caption")
    label.add_css_class("dim-label")
    label.add_css_class("pr-md-table-more")
    if page_url.lower().startswith(("http://", "https://")) and len(page_url) <= 2_000:
        label.set_markup(f'<a href="{GLib.markup_escape_text(page_url)}">{text}</a>')
    else:
        label.set_markup(text)
    return label
