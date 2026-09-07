"""Markdown blocks (mdblocks) as GTK widgets — the PR page's render layer.

One widget per block: paragraphs and headings are the selectable wrapped
labels the page always used (a heading's markup wrapped in a size span),
a list is a column of glyph-plus-content rows that nests structurally, a
quote a bordered column, a rule a separator, a table a grid of cell labels
that scrolls sideways on its own (the page body never does), an image row
whatever the page's own image slot builder makes of it, a code block a
read-only GtkSource view built like the Files view's patch view (the
fence's language highlighted, the editor's style scheme threaded in from
the page, its own sideways scroller, a right-click copying the whole
block), a `<details>` an expander wearing
its summary whose children are built the first time it opens, an alert
quote a column with a colored bar under an icon-and-title row.

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
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from . import mdblocks  # noqa: E402
from .copylabel import FLASH_MS  # noqa: E402
from .editor import GtkSource  # noqa: E402 — require_version + friendly exit live there
from .editorfiles import fence_language_id  # noqa: E402
from .formatting import markup_ok  # noqa: E402
from .i18n import N_, _  # noqa: E402

# Leaf widgets one body may build before the rest render as plain text.
WIDGET_BUDGET = 400
# Characters one code block's buffer holds — the page's render cap
# (prview._RENDER_CAP), applied per block: a fence is one block whatever
# its size, and a GtkSource buffer past this is layout the main loop pays
# for on every scroll.
CODE_CAP = 20_000
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
# GitHub's own Octicon for each alert kind (info, light-bulb, report,
# alert, stop), bundled under data/icons; the warning one is the mark a
# conflicted PR already wears.
_ALERT_ICONS = {
    "note": "alert-note-symbolic",
    "tip": "alert-tip-symbolic",
    "important": "alert-important-symbolic",
    "warning": "alert-symbolic",
    "caution": "alert-caution-symbolic",
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
    scheme: GtkSource.StyleScheme | None = None,
) -> list[Gtk.Widget]:
    """Widgets for *blocks*, in order, spending *budget*; *image_row* is
    what turns an `ImageRow`'s images into a widget (the page's own slot
    builder); *page_url* is where the body lives on GitHub — what a capped
    table's "more" link opens; *scheme* is the GtkSource style scheme a
    code block's view wears (the page's, from `editor.style_scheme`). Once
    the budget runs dry the remaining blocks come back as a single plain
    label of their source."""
    widgets: list[Gtk.Widget] = []
    for index, block in enumerate(blocks):
        if budget.left <= 0:
            rest = rest_source(blocks[index:])
            if rest:
                widgets.append(plain_label(rest))
            break
        widgets.append(build_one(block, budget, image_row, depth, page_url, scheme))
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
    scheme: GtkSource.StyleScheme | None = None,
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
        return _list(block, budget, image_row, depth, page_url, scheme)
    if isinstance(block, mdblocks.Quote):
        return _quote(block, budget, image_row, depth, page_url, scheme)
    if isinstance(block, mdblocks.Table):
        return _table(block, budget, page_url)
    if isinstance(block, mdblocks.CodeBlock):
        budget.take()
        return code_view(block, scheme, page_url)
    if isinstance(block, mdblocks.Details):
        budget.take()
        return DetailsExpander(block, budget, image_row, depth, page_url, scheme)
    # A block kind this layer doesn't know: its source, escaped.
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


def code_view(
    block: mdblocks.CodeBlock, scheme: GtkSource.StyleScheme | None, page_url: str = ""
) -> Gtk.Widget:
    """A fence or indented block as a read-only GtkSource view, built like
    the Files view's patch view: no cursor, monospace, 6/4 px margins, no
    line numbers (these are snippets), in its own scroller that scrolls
    sideways only with its natural height propagated — a long line pans
    within the block instead of widening the page. The language is the
    fence's info word through `editorfiles.fence_language_id`, or the word
    itself when GtkSource knows it by that name, or none; *scheme* is the
    editor's style scheme as the page computes it (`style_scheme`), and
    `restyle_code` follows a later change. The text is capped at `CODE_CAP`;
    what that cut is counted in a link to *page_url* under the block, as
    a capped table's rows are. A right-click copies the whole block
    (`CodeBlockView.copy`)."""
    view = CodeBlockView(block, scheme)
    if len(block.text.rstrip("\n")) <= CODE_CAP:
        return view
    lines_cut = max(1, block.text.rstrip("\n").count("\n", CODE_CAP))
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
    box.append(view)
    more = _link_label(_("{n} more lines on GitHub").format(n=lines_cut), page_url)
    more.add_css_class("pr-md-code-more")
    box.append(more)
    return box


class CodeBlockView(Gtk.Overlay):
    """The code block's widget: the view in its scroller, and over its top
    right corner the "Copied to clipboard" pill that shows for a beat
    after a right-click copies the block (`copy`) — the page's copyable
    labels flash the same words in place, but a code block's text is the
    one thing here that must not change under the pointer. The
    right-click is claimed in the capture phase, ahead of the text view's
    own context menu (Cut / Paste / Select all — none of them for a
    read-only snippet); the tooltip says so, since a right-click nobody is
    told about is one nobody finds."""

    def __init__(self, block: mdblocks.CodeBlock, scheme: GtkSource.StyleScheme | None) -> None:
        super().__init__(hexpand=True)
        self.text = block.text.rstrip("\n")
        self._flash: list[int] = []
        self.view = _source_view(block, scheme)
        self.view.set_tooltip_text(_("Right-click to copy"))
        scroller = Gtk.ScrolledWindow(child=self.view, hexpand=True)
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.set_propagate_natural_height(True)
        scroller.add_css_class("pr-md-code-scroller")
        self.set_child(scroller)
        self.copied = Gtk.Label(label=_("Copied to clipboard"), visible=False)
        self.copied.add_css_class("osd")
        self.copied.add_css_class("pr-md-copied")
        self.copied.set_halign(Gtk.Align.END)
        self.copied.set_valign(Gtk.Align.START)
        self.copied.set_can_target(False)
        self.add_overlay(self.copied)
        secondary = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        secondary.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        secondary.connect("pressed", self._on_secondary)
        self.view.add_controller(secondary)

    def _on_secondary(self, gesture: Gtk.GestureClick, _n_press: int, _x: float, _y: float) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.copy()

    def copy(self) -> None:
        """Put the block's whole text on the clipboard and flash the pill."""
        self.get_clipboard().set(self.text)
        self.copied.set_visible(True)
        if self._flash:
            GLib.source_remove(self._flash.pop())
        self._flash.append(GLib.timeout_add(FLASH_MS, self._unflash))

    def _unflash(self) -> bool:
        self._flash.clear()
        self.copied.set_visible(False)
        return GLib.SOURCE_REMOVE


def _source_view(block: mdblocks.CodeBlock, scheme: GtkSource.StyleScheme | None) -> GtkSource.View:
    buffer = GtkSource.Buffer()
    manager = GtkSource.LanguageManager.get_default()
    language_id = fence_language_id(block.lang or "")
    language = manager.get_language(language_id) if language_id else None
    if language is None and block.lang and block.lang != "suggestion":
        # A word the alias map doesn't know but GtkSource does (kotlin,
        # ruby, sql…). The id alphabet is [a-z0-9-]; anything else is no id.
        word = block.lang if all(c.isalnum() or c in "-_." for c in block.lang) else ""
        language = manager.get_language(word) if word else None
    if language is not None:
        buffer.set_language(language)
    buffer.set_highlight_matching_brackets(False)
    if scheme is not None:
        buffer.set_style_scheme(scheme)
    text = block.text[:CODE_CAP].rstrip("\n")
    buffer.set_text(text)
    view = GtkSource.View(buffer=buffer)
    view.set_editable(False)
    view.set_cursor_visible(False)
    view.set_monospace(True)
    view.set_left_margin(6)
    view.set_right_margin(6)
    view.set_top_margin(4)
    view.set_bottom_margin(4)
    view.add_css_class("pr-md-code")
    return view


def restyle_code(root: Gtk.Widget, scheme: GtkSource.StyleScheme | None) -> None:
    """Put *scheme* on every code view under *root* — what the page does
    when the editor's scheme setting or the app's light/dark changes, the
    way its Files view restyles its patch buffers. A `<details>` that has
    not been opened yet has no views to restyle; it is told the scheme,
    so the ones it builds on opening wear the current one."""
    if scheme is None:
        return
    child = root.get_first_child()
    while child is not None:
        if isinstance(child, GtkSource.View) and "pr-md-code" in child.get_css_classes():
            child.get_buffer().set_style_scheme(scheme)
        else:
            if isinstance(child, DetailsExpander):
                child.scheme = scheme
            restyle_code(child, scheme)
        child = child.get_next_sibling()


class DetailsExpander(Gtk.Expander):
    """A `<details>` block: a `Gtk.Expander` wearing the summary (GitHub's
    "Details" when the author gave none), collapsed unless the tag said
    ``open``, whose children are built on the first ``notify::expanded``
    — a details block hiding two hundred blocks costs a label until it is
    opened. A ``<details open>`` builds its children at construction
    against the body's own *budget*, the one `build_one` is spending: the
    ``open`` attribute is the author's, so what it shows counts like any
    other block. Only a reader's click builds against a fresh `Budget` —
    nothing under a closed expander existed to count. What the children
    need from the page — the image-slot builder, the body's `page_url`,
    the code blocks' scheme — is kept until then; `restyle_code`
    refreshes the scheme meanwhile. The summary is its own wrapping label
    (`summary_label`): GTK's built-in expander label neither wraps nor
    ellipsizes, and a one-sentence summary would set the page's minimum
    width."""

    def __init__(
        self,
        block: mdblocks.Details,
        budget: Budget,
        image_row: Callable[[tuple], Gtk.Widget],
        depth: int = 0,
        page_url: str = "",
        scheme: GtkSource.StyleScheme | None = None,
    ) -> None:
        super().__init__(hexpand=True)
        self._children = block.children
        self._image_row = image_row
        self._depth = depth
        self._page_url = page_url
        self.scheme = scheme
        self._built = False
        self.add_css_class("pr-md-details")
        # The summary is escaped plain text already (mdblocks strips its
        # tags and caps it); as markup it shows the author's ampersands
        # and angle brackets as written.
        summary = Gtk.Label(xalign=0.0, wrap=True, hexpand=True)
        summary.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        summary.add_css_class("pr-md-details-summary")
        summary.set_markup(block.summary or GLib.markup_escape_text(_("Details")))
        self.set_label_widget(summary)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        body.add_css_class("pr-md-details-body")
        self.set_child(body)
        self.connect("notify::expanded", self._on_expanded)
        if block.open:
            self._build(budget)
            self.set_expanded(True)

    @property
    def summary_label(self) -> Gtk.Label:
        """The label wearing the summary (its text is the markup shown)."""
        return self.get_label_widget()

    def _on_expanded(self, *_args) -> None:
        if self._built or not self.get_expanded():
            return
        self._build(Budget())

    def _build(self, budget: Budget) -> None:
        self._built = True
        body = self.get_child()
        for widget in build(
            list(self._children),
            budget,
            self._image_row,
            self._depth + 1,
            self._page_url,
            self.scheme,
        ):
            body.append(widget)
        self._children = ()


def _list(
    block: mdblocks.ListBlock,
    budget: Budget,
    image_row,
    depth: int,
    page_url: str = "",
    scheme: GtkSource.StyleScheme | None = None,
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
        for widget in build(list(item.children), budget, image_row, depth + 1, page_url, scheme):
            content.append(widget)
        row.append(content)
        column.append(row)
    return column


def _quote(
    block: mdblocks.Quote,
    budget: Budget,
    image_row,
    depth: int,
    page_url: str = "",
    scheme: GtkSource.StyleScheme | None = None,
) -> Gtk.Widget:
    """A quote: its children behind a left bar. An alert — GitHub's
    ``> [!NOTE]`` and kin — is the same column wearing
    ``.pr-md-alert-<kind>`` (the bar and the title take the kind's color
    from app.py's CSS: tip green, warning yellow, caution red, note the
    accent, important purple) under a row of GitHub's own icon for the
    kind and its title."""
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
    column.add_css_class("pr-md-quote")
    if block.kind != "plain":
        column.add_css_class("pr-md-alert")
        column.add_css_class(f"pr-md-alert-{block.kind}")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.add_css_class("pr-md-alert-head")
        icon = Gtk.Image.new_from_icon_name(_ALERT_ICONS.get(block.kind, "alert-note-symbolic"))
        icon.add_css_class("pr-md-alert-icon")
        row.append(icon)
        title = Gtk.Label(label=_(_ALERT_TITLES.get(block.kind, "Note")), xalign=0.0)
        title.add_css_class("heading")
        title.add_css_class("pr-md-alert-title")
        row.append(title)
        budget.take()
        column.append(row)
    for widget in build(list(block.children), budget, image_row, depth + 1, page_url, scheme):
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
        # Bold comes from `.pr-md-th` in app.py's CSS — the one place.
        label = _cell(cell, shown.aligns[column])
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
        # Chaining up hands back whatever the C vfunc left in its baseline
        # out-args (zeros, from PyGObject) and GTK warns of "a horizontal
        # baseline"; a scroller has none.
        minimum, natural, _, _ = Gtk.ScrolledWindow.do_measure(self, orientation, for_size)
        return minimum, natural, -1, -1


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
    label = _link_label(", ".join(parts), page_url)
    label.add_css_class("pr-md-table-more")
    return label


def _link_label(text: str, page_url: str) -> Gtk.Label:
    """A dim caption saying *text*, linked to *page_url* when that is an
    http(s) URL of sane length — the "the rest is on GitHub" line under a
    capped table or code block."""
    text = GLib.markup_escape_text(text)
    label = Gtk.Label(xalign=0.0)
    label.add_css_class("caption")
    label.add_css_class("dim-label")
    if page_url.lower().startswith(("http://", "https://")) and len(page_url) <= 2_000:
        label.set_markup(f'<a href="{GLib.markup_escape_text(page_url)}">{text}</a>')
    else:
        label.set_markup(text)
    return label
