# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The native diff view: a parsed `git diff` / `show` drawn as GTK widgets.

`DiffView` is the git page's review stream, in place of hunk's terminal
(spec: ~/specs/collins/native-diff-panel.md). It takes diffmodel's Files
and draws one `_FileSection` per file — a header (fold, icon, path, +/−
counts, what kind of change it is), a side-by-side picture for an image,
then `_GapRow`s and `_HunkSection`s in patch order — inside one vertical
scroller, with the header of the file the reader is scrolled into pinned
over the top. Every hunk is its own `GtkSource.View` (decision 2: the
header row is where the buttons go, a line selection is naturally
hunk-scoped, highlighting stays cheap and the gaps between hunks stay
expandable); the split layout is two views per hunk over diffmodel's
split_rows, padding cells and all, and under wrap a `_SplitPane` allocation
pass evens the two sides' row heights with `pixels-below-lines` tags.

The buffers hold the patch text without its signs; the signs and the two
line-number columns are `GtkSource.GutterRendererText` subclasses reading
per-line arrays (`_NumberRenderer`, `_SignRenderer`), plus a
`_MarkerRenderer` column for the notes and highlights later PRs land. Row
backgrounds are `paragraph-background` tags, word emphasis a `background`
tag, both coloured by diffmodel.palette from the editor's style scheme
(decision 3: the diff follows `editor_style_scheme` and `editor_font`).
Syntax highlighting is the file's GtkSource language over the hunk's own
text; a gap expanded from a `context_reader` (the whole file on one side,
read on a thread) is drawn as more context, highlighted the same way.

Everything that arrives is foreign content: paths, patch text and gap text
go through `set_text` / `Gtk.TextBuffer.set_text`, never Pango markup (the
only markup here colours a sign with a hex the palette computed); sizes
are bounded upstream by diffmodel and here by MAX_EXPAND_ALL.

Public face — what the page and the e2e drive: `set_scheme`,
`set_options`, `load` (rebuilds by stable key so an untouched hunk keeps
its widget and, with it, the reader's place), `reveal`, `current`,
`filter`, `focus_hunk` / `focus_file`, the probes `file_rows` /
`hunk_rows`, and the signals `current-changed(path, hunk)`,
`open-requested(path, line)`, `context-requested(path, gap, count)`.
Widget code: exercised by scripts/probe_diffview.py and the git page's e2e,
not the unit suite.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GLib, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402

from . import (  # noqa: E402
    diffmodel,
    editorfiles,
    filetypes,
    gitloads,
    imagediff,
    keyedslots,
    prblobs,
    remoteimages,
)
from .dropimages import cache_directory  # noqa: E402
from .editor import GtkSource  # noqa: E402 — require_version + friendly exit live there
from .i18n import _  # noqa: E402

log = logging.getLogger(__name__)

# The layouts, gitloads' words: `auto` is split when the view is at least
# SPLIT_MIN_WIDTH wide (an Adw.Breakpoint on the view says which side of
# it we are on), else stack. [tune] — hunk picks split by terminal columns.
LAYOUT_AUTO, LAYOUT_SPLIT, LAYOUT_STACK = gitloads.LAYOUTS
SPLIT_MIN_WIDTH = 1000
# Tab stops in the hunk text. The editor has no tab-width setting to follow
# (state.DEFAULT_SETTINGS has none), so this is hunk's own default.
TAB_WIDTH = 4
# A gap's ▲ / ▼ step, and the most lines `all` will draw at once — a gap
# of a hundred thousand unchanged lines is a file, not context.
EXPAND_STEP = 20
MAX_EXPAND_ALL = 10_000
# How long after the last scroll movement the current file / pinned header
# is recomputed, and after an allocation the split rows are re-aligned.
_SCROLL_SETTLE_MS = 60
_ALIGN_SETTLE_MS = 40
# The buffer's word for a split layout's padding cell (a blank line would
# have nothing to hang the background tag on: tags apply to characters).
PAD = "pad"
_PAD_TEXT = " "
# What a gap row's arrows say. Up expands from the bottom of the gap (the
# lines just above the next hunk), down from the top (the lines just below
# the previous one).
UP, DOWN, ALL = "up", "down", "all"

ContextReader = Callable[[diffmodel.File, str], "bytes | None"]


@dataclass(frozen=True)
class _Row:
    """One paragraph of a hunk view's buffer: what kind of line it draws
    (CONTEXT / ADD / DEL / PAD), its text, and its number on each side."""

    kind: str
    text: str
    old: int | None
    new: int | None


# -- module-wide appearance ----------------------------------------------------

_font_provider: Gtk.CssProvider | None = None


def apply_font(font: str) -> None:
    """Give every diff view's text the editor's font (`editor_font`, a Pango
    description; "" for the system monospace). One display-level provider
    keyed on the view's class, since a widget-level provider styles one
    widget and the views are many."""
    global _font_provider
    display = Gdk.Display.get_default()
    if display is None:
        return
    if _font_provider is not None:
        Gtk.StyleContext.remove_provider_for_display(display, _font_provider)
        _font_provider = None
    if not font:
        return
    desc = Pango.FontDescription.from_string(font)
    family = (desc.get_family() or "").replace('"', "")
    size = desc.get_size() / Pango.SCALE
    unit = "px" if desc.get_size_is_absolute() else "pt"
    rules = []
    if family:
        rules.append(f'font-family: "{family}";')
    if size > 0:
        rules.append(f"font-size: {size:g}{unit};")
    if not rules:
        return
    provider = Gtk.CssProvider()
    provider.load_from_string(".git-diff textview { " + " ".join(rules) + " }")
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _font_provider = provider


def palette_for(scheme: GtkSource.StyleScheme | None, dark: bool) -> diffmodel.DiffPalette:
    """diffmodel.palette fed from *scheme*: its `text` background and its
    `diff:added-line` / `diff:removed-line` foregrounds (a style that
    doesn't set one falls back to the Adwaita colours for *dark*)."""

    def colour(style_id: str, prop: str) -> str | None:
        if scheme is None:
            return None
        style = scheme.get_style(style_id)
        if style is None or not style.get_property(f"{prop}-set"):
            return None
        return style.get_property(prop)

    return diffmodel.palette(
        colour("text", "background"),
        colour("diff:added-line", "foreground"),
        colour("diff:removed-line", "foreground"),
        dark,
    )


def _language_for(path: str, first_line: str = "") -> GtkSource.Language | None:
    """The GtkSource language a file's hunks are highlighted with: guessed
    from the path's name (the same call the editor makes), else from the
    suffix / shebang table in editorfiles."""
    manager = GtkSource.LanguageManager.get_default()
    name = PurePosixPath(path).name
    language = manager.guess_language(name, None) if name else None
    if language is None:
        hint = editorfiles.guess_language_id(path, first_line)
        if hint:
            language = manager.get_language(hint)
    return language


def _decode_lines(data: bytes | None) -> list[str] | None:
    """A side's whole file as lines, for gap context: None for no file, a
    binary (a NUL in the first 8000 bytes, hunk's sniff), or one over
    diffmodel's char cap."""
    if data is None or b"\x00" in data[:8000] or len(data) > diffmodel.MAX_PATCH_CHARS:
        return None
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # the newline that ends the file, not an empty last line
    return [line[:-1] if line.endswith("\r") else line for line in lines]


# -- gutter renderers ---------------------------------------------------------


class _TextRenderer(GtkSource.GutterRendererText):
    """A GutterRendererText sized to its widest entry: the base class
    measures nothing but its padding, so a subclass reports the width of
    the widest text it will draw (measured through the renderer's own Pango
    context, once it has one)."""

    def __init__(self, widest: str = "") -> None:
        super().__init__()
        self._widest = widest
        self.set_xpad(4)

    def set_widest(self, widest: str) -> None:
        if widest != self._widest:
            self._widest = widest
            self.queue_resize()

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple:
        if orientation == Gtk.Orientation.HORIZONTAL:
            width, _height = self.measure(self._widest) if self._widest else (0, 0)
            width += 2 * self.get_xpad()
            return (width, width, -1, -1)
        return (0, 0, -1, -1)


class _NumberRenderer(_TextRenderer):
    """A line-number column reading a per-paragraph array (None draws
    nothing: the side the line is not on, a padding cell)."""

    def __init__(self) -> None:
        super().__init__()
        self._numbers: list[int | None] = []
        self._cursor_bold = False
        self.set_xalign(1.0)

    def set_numbers(self, numbers: Sequence[int | None]) -> None:
        self._numbers = list(numbers)
        widest = max((n for n in self._numbers if n is not None), default=0)
        self.set_widest(str(widest) if widest else "")
        self.queue_draw()

    def set_cursor_bold(self, bold: bool) -> None:
        """Whether the cursor line's number is drawn bold (GtkSourceView's
        `current-line-number` style). On while the view has the keyboard;
        off otherwise, or every unfocused hunk would show its first number
        bold for a cursor nobody put there."""
        if bold != self._cursor_bold:
            self._cursor_bold = bold
            self.queue_draw()

    def do_query_data(self, _lines, line: int) -> None:
        number = self._numbers[line] if line < len(self._numbers) else None
        if number is None:
            self.set_text("", -1)
        elif self._cursor_bold:
            self.set_text(str(number), -1)
        else:
            # A weight of its own outranks the scheme's bold (measured).
            self.set_markup(f'<span weight="normal">{number}</span>', -1)


class _SignRenderer(_TextRenderer):
    """The sign column: `+` in the added colour, `−` in the removed one,
    nothing on context and padding."""

    def __init__(self, palette: diffmodel.DiffPalette) -> None:
        super().__init__("+")
        self._kinds: list[str] = []
        self._palette = palette
        self.set_xalign(0.5)

    def set_kinds(self, kinds: Sequence[str]) -> None:
        self._kinds = list(kinds)
        self.queue_draw()

    def set_palette(self, palette: diffmodel.DiffPalette) -> None:
        self._palette = palette
        self.queue_draw()

    def do_query_data(self, _lines, line: int) -> None:
        kind = self._kinds[line] if line < len(self._kinds) else diffmodel.CONTEXT
        if kind == diffmodel.ADD:
            self.set_markup(f'<span foreground="{self._palette.added_fg}">+</span>', -1)
        elif kind == diffmodel.DEL:
            self.set_markup(f'<span foreground="{self._palette.removed_fg}">−</span>', -1)
        else:
            self.set_text("", -1)


class _MarkerRenderer(GtkSource.GutterRendererPixbuf):
    """The marker column: an icon beside a line carrying a note or a
    highlight (later PRs). Empty until `set_marks` names one; sized to the
    icon only while it has any."""

    def __init__(self) -> None:
        super().__init__()
        self._icons: dict[int, str] = {}
        self.set_visible(False)

    def set_marks(self, icons: dict[int, str]) -> None:
        self._icons = dict(icons)
        self.set_visible(bool(self._icons))
        self.queue_draw()

    def do_query_data(self, _lines, line: int) -> None:
        icon = self._icons.get(line)
        if icon:
            self.set_icon_name(icon)
        else:
            try:
                self.set_paintable(None)
            except TypeError:  # an older binding that refuses NULL
                self.set_icon_name("")

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple:
        size = 16 if self._icons else 0
        return (size, size, -1, -1) if orientation == Gtk.Orientation.HORIZONTAL else (0, 0, -1, -1)


# -- one view -------------------------------------------------------------------


class _HunkView:
    """One `GtkSource.View` over a list of `_Row`s: a hunk's lines in the
    stack layout, one side of them in split, or a gap's context lines.

    Owns the buffer, its tags (row backgrounds by kind, the word emphasis,
    the pads the split alignment adds), the gutter renderers (the number
    columns *numbers* names — `old`, `new` or both — the sign, the marker),
    and the scroller the view sits in: horizontal when wrap is off, so a
    long line pans within its hunk while the page never scrolls sideways.
    """

    def __init__(
        self,
        language: GtkSource.Language | None,
        scheme: GtkSource.StyleScheme | None,
        palette: diffmodel.DiffPalette,
        numbers: Sequence[str],
        sign: bool,
        line_numbers: bool,
        wrap: bool,
    ) -> None:
        self.rows: list[_Row] = []
        self._pads: dict[int, int] = {}
        self._pad_tags: dict[int, Gtk.TextTag] = {}
        buffer = GtkSource.Buffer()
        buffer.set_highlight_syntax(True)
        buffer.set_highlight_matching_brackets(False)
        if language is not None:
            buffer.set_language(language)
        if scheme is not None:
            buffer.set_style_scheme(scheme)
        self.buffer = buffer
        view = GtkSource.View(buffer=buffer)
        view.set_editable(False)
        view.set_cursor_visible(False)
        view.set_monospace(True)
        view.set_tab_width(TAB_WIDTH)
        view.set_left_margin(6)
        view.set_right_margin(6)
        view.set_hexpand(True)
        view.set_vexpand(False)
        view.add_css_class("git-hunk-text")
        self.view = view
        self._tags = {
            diffmodel.ADD: buffer.create_tag("git-add", paragraph_background=palette.added_bg),
            diffmodel.DEL: buffer.create_tag("git-del", paragraph_background=palette.removed_bg),
            PAD: buffer.create_tag("git-pad", paragraph_background=palette.padding_bg),
        }
        self._emphasis = {
            diffmodel.ADD: buffer.create_tag("git-emph-add", background=palette.added_emphasis_bg),
            diffmodel.DEL: buffer.create_tag("git-emph-del", background=palette.removed_emphasis_bg),
        }
        gutter = view.get_gutter(Gtk.TextWindowType.LEFT)
        position = 0
        self._numbers: list[_NumberRenderer] = []
        self._number_sides = tuple(numbers)
        for _side in self._number_sides:
            renderer = _NumberRenderer()
            renderer.set_visible(line_numbers)
            gutter.insert(renderer, position)
            position += 1
            self._numbers.append(renderer)
        self._sign: _SignRenderer | None = None
        if sign:
            self._sign = _SignRenderer(palette)
            gutter.insert(self._sign, position)
            position += 1
        self._marker = _MarkerRenderer()
        gutter.insert(self._marker, position)
        scroller = Gtk.ScrolledWindow(child=view, hexpand=True)
        scroller.set_propagate_natural_height(True)
        scroller.set_overlay_scrolling(True)
        self.scroller = scroller
        # The text view validates its layout in an idle after it is first
        # allocated, and the height it then knows does not always reach the
        # scroller (measured: a one-line hunk laid out at 0 px until the
        # next resize). Its vadjustment's upper *is* the layout height, so a
        # move of it re-measures the scroller — under wrap too, where every
        # width change re-flows the lines. Deferred: the adjustment moves
        # from inside the view's own allocation, where a queue_resize on an
        # ancestor being allocated in the same pass is lost (measured).
        self._remeasure_source = 0
        view.get_vadjustment().connect("changed", self._on_layout_height_changed)
        self.set_wrap(wrap)

    def _on_layout_height_changed(self, _adjustment: Gtk.Adjustment) -> None:
        if self._remeasure_source:
            return
        self._remeasure_source = GLib.idle_add(self._remeasure, priority=GLib.PRIORITY_DEFAULT)

    def _remeasure(self) -> bool:
        self._remeasure_source = 0
        self.scroller.queue_resize()
        return GLib.SOURCE_REMOVE

    # -- content --

    def set_rows(
        self, rows: Sequence[_Row], emphasis: dict[int, tuple[tuple[int, int], ...]] | None = None
    ) -> None:
        """Fill the buffer with *rows* and tag them; *emphasis* maps a row
        index to the character spans of its text to emphasise."""
        self.rows = list(rows)
        self._pads = {}
        buffer = self.buffer
        buffer.set_text("\n".join(_PAD_TEXT if row.kind == PAD else row.text for row in self.rows))
        for index, row in enumerate(self.rows):
            tag = self._tags.get(row.kind)
            if tag is not None:
                start, end = self._paragraph(index)
                buffer.apply_tag(tag, start, end)
            spans = emphasis.get(index) if emphasis else None
            emphasis_tag = self._emphasis.get(row.kind)
            if spans and emphasis_tag is not None:
                for first, last in spans:
                    ok_a, start = buffer.get_iter_at_line_offset(index, first)
                    ok_b, end = buffer.get_iter_at_line_offset(index, last)
                    if ok_a and ok_b and start.get_line() == index and end.get_line() == index:
                        buffer.apply_tag(emphasis_tag, start, end)
        for side, renderer in zip(self._number_sides, self._numbers, strict=True):
            renderer.set_numbers([row.old if side == diffmodel.OLD else row.new for row in self.rows])
        if self._sign is not None:
            self._sign.set_kinds([row.kind for row in self.rows])
        # A fresh buffer's cursor sits at its end; the reader's j/k start at
        # the top, and the current-line highlight (while focused) with them.
        buffer.place_cursor(buffer.get_start_iter())

    def _paragraph(self, index: int) -> tuple[Gtk.TextIter, Gtk.TextIter]:
        """The whole paragraph *index* — its newline included, so an empty
        line still has a character to hang a tag on."""
        _ok, start = self.buffer.get_iter_at_line(index)
        end = start.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        end.forward_char()  # the newline; a no-op on the last line
        return start, end

    # -- appearance --

    def set_palette(self, palette: diffmodel.DiffPalette) -> None:
        self._tags[diffmodel.ADD].set_property("paragraph-background", palette.added_bg)
        self._tags[diffmodel.DEL].set_property("paragraph-background", palette.removed_bg)
        self._tags[PAD].set_property("paragraph-background", palette.padding_bg)
        self._emphasis[diffmodel.ADD].set_property("background", palette.added_emphasis_bg)
        self._emphasis[diffmodel.DEL].set_property("background", palette.removed_emphasis_bg)
        if self._sign is not None:
            self._sign.set_palette(palette)

    def set_scheme(self, scheme: GtkSource.StyleScheme | None) -> None:
        if scheme is not None:
            self.buffer.set_style_scheme(scheme)

    def set_line_numbers(self, shown: bool) -> None:
        for renderer in self._numbers:
            renderer.set_visible(shown)

    def set_focused(self, focused: bool) -> None:
        """The keyboard arrived (or left): a live cursor — Shift+arrows
        select only with one (measured: a hidden cursor moves nothing) —
        the current-line highlight, and the bold current line number."""
        self.view.set_cursor_visible(focused)
        self.view.set_highlight_current_line(focused)
        for renderer in self._numbers:
            renderer.set_cursor_bold(focused)

    def set_wrap(self, wrap: bool) -> None:
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR if wrap else Gtk.WrapMode.NONE)
        self.scroller.set_policy(
            Gtk.PolicyType.NEVER if wrap else Gtk.PolicyType.AUTOMATIC,
            Gtk.PolicyType.NEVER,
        )
        if not wrap:
            self.clear_pads()

    def set_marks(self, icons: dict[int, str]) -> None:
        self._marker.set_marks(icons)

    # -- the split alignment's pads --

    def row_height(self, index: int) -> int:
        """Paragraph *index*'s height on screen, less any pad this view
        added to it — its natural height at the current width."""
        _ok, it = self.buffer.get_iter_at_line(index)
        _y, height = self.view.get_line_yrange(it)
        return max(0, height - self._pads.get(index, 0))

    def set_pad(self, index: int, pixels: int) -> None:
        """Put *pixels* under paragraph *index* (0 clears)."""
        current = self._pads.get(index, 0)
        if current == pixels:
            return
        start, end = self._paragraph(index)
        if current:
            self.buffer.remove_tag(self._pad_tags[current], start, end)
        if pixels:
            tag = self._pad_tags.get(pixels)
            if tag is None:
                tag = self.buffer.create_tag(f"git-pad-{pixels}", pixels_below_lines=pixels)
                self._pad_tags[pixels] = tag
            self.buffer.apply_tag(tag, start, end)
            self._pads[index] = pixels
        else:
            self._pads.pop(index, None)

    def clear_pads(self) -> None:
        for index in list(self._pads):
            self.set_pad(index, 0)

    # -- the cursor --

    def place_cursor(self, index: int) -> None:
        if 0 <= index < len(self.rows):
            _ok, it = self.buffer.get_iter_at_line(index)
            self.buffer.place_cursor(it)

    def cursor_row(self) -> int:
        return self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_line()


# -- split layout allocation ---------------------------------------------------


class _SplitPane(Gtk.Widget):
    """Two children side by side, each half the width, that tells its owner
    when its width changed — the hook the split layout's alignment pass
    hangs on. A Gtk.Box allocates through its layout manager and never
    calls `do_size_allocate`; a bare widget does, so this one allocates its
    two halves itself. The pass runs a beat after the allocation (the text
    views validate their wrapped heights in an idle of their own) and
    coalesces across a resize's stream of allocations."""

    __gtype_name__ = "CollinsDiffSplitPane"

    def __init__(self, left: Gtk.Widget, right: Gtk.Widget, on_resized: Callable[[], None]) -> None:
        super().__init__()
        self._left = left
        self._right = right
        self._on_resized = on_resized
        self._size = (-1, -1)
        self._source = 0
        left.set_parent(self)
        right.set_parent(self)

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple:
        if orientation == Gtk.Orientation.HORIZONTAL:
            minimum = natural = 0
            for child in (self._left, self._right):
                child_min, child_nat, _b, _c = child.measure(orientation, -1)
                minimum += child_min
                natural += child_nat
            return (minimum, natural, -1, -1)
        half = for_size // 2 if for_size > 0 else -1
        minimum = natural = 0
        for child, width in ((self._left, half), (self._right, for_size - half if for_size > 0 else -1)):
            child_min, child_nat, _b, _c = child.measure(orientation, width)
            minimum = max(minimum, child_min)
            natural = max(natural, child_nat)
        return (minimum, natural, -1, -1)

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        half = width // 2
        self._left.allocate(half, height, -1, None)
        self._right.allocate(
            width - half, height, -1, Gsk.Transform.new().translate(Graphene.Point().init(half, 0))
        )
        # A new width re-wraps every line; a new height at the same width
        # is a text view's validation landing (or the pass's own pads) —
        # either way the rows want measuring again. The pass converges:
        # once no pad changes, no height changes, and it stops firing.
        if (width, height) != self._size:
            self._size = (width, height)
            if self._source:
                GLib.source_remove(self._source)
            self._source = GLib.timeout_add(_ALIGN_SETTLE_MS, self._fire)

    def _fire(self) -> bool:
        self._source = 0
        self._on_resized()
        return GLib.SOURCE_REMOVE

    def do_dispose(self) -> None:
        if self._source:
            GLib.source_remove(self._source)
            self._source = 0
        for child in (self._left, self._right):
            if child.get_parent() is self:
                child.unparent()
        Gtk.Widget.do_dispose(self)


# -- sections -------------------------------------------------------------------


@dataclass
class _Options:
    """What `DiffView.set_options` decided, as the sections read it:
    *split* is the layout already resolved (auto → the breakpoint's word)."""

    split: bool
    line_numbers: bool
    wrap: bool
    word_diff: bool


class _HunkSection(Gtk.Box):
    """One hunk: its `@@` header row (the buttons' home in the next PR) over
    its view(s). Wears `.git-hunk`, and `.git-hunk-focused` while one of
    its views has the keyboard — the lit rail that says "current hunk"."""

    def __init__(
        self,
        file: diffmodel.File,
        hunk: diffmodel.Hunk,
        language: GtkSource.Language | None,
        scheme: GtkSource.StyleScheme | None,
        palette: diffmodel.DiffPalette,
        options: _Options,
        on_focus: Callable[[_HunkSection, bool], None],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("git-hunk")
        self.file = file
        self.hunk = hunk
        self._language = language
        self._scheme = scheme
        self._palette = palette
        self._options = options
        self._on_focus = on_focus
        self.views: list[_HunkView] = []
        self._body: Gtk.Widget | None = None
        self._pane: _SplitPane | None = None
        self._focused = False

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.add_css_class("git-hunk-header")
        ranges = Gtk.Label(xalign=0.0)
        ranges.set_text(f"@@ -{hunk.old_start},{hunk.old_count} +{hunk.new_start},{hunk.new_count} @@")
        ranges.add_css_class("git-hunk-ranges")
        header.append(ranges)
        context = Gtk.Label(xalign=0.0, hexpand=True)
        context.set_text(hunk.context)
        context.set_ellipsize(Pango.EllipsizeMode.END)
        context.set_single_line_mode(True)
        context.add_css_class("dim-label")
        context.add_css_class("git-hunk-context")
        header.append(context)
        # Where the next PR's Stage / Discard buttons go.
        self.actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.actions.add_css_class("git-hunk-actions")
        header.append(self.actions)
        self.header = header
        self.append(header)
        self._build_body()

    # -- building --

    def _make_view(self, numbers: Sequence[str]) -> _HunkView:
        view = _HunkView(
            self._language,
            self._scheme,
            self._palette,
            numbers,
            sign=True,
            line_numbers=self._options.line_numbers,
            wrap=self._options.wrap,
        )
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", self._on_focus_enter)
        focus.connect("leave", self._on_focus_leave)
        view.view.add_controller(focus)
        return view

    def _build_body(self) -> None:
        if self._body is not None:
            self.remove(self._body)
            self._body = None
            self._pane = None
        self.views = []
        hunk = self.hunk
        emphasis = diffmodel.word_emphasis(hunk) if self._options.word_diff else []
        if self._options.split:
            rows = diffmodel.split_rows(hunk)
            old_rows = [
                _Row(r.old.kind, r.old.text, r.old.old, None) if r.old else _Row(PAD, "", None, None)
                for r in rows
            ]
            new_rows = [
                _Row(r.new.kind, r.new.text, None, r.new.new) if r.new else _Row(PAD, "", None, None)
                for r in rows
            ]
            # Emphasis speaks in hunk line indexes; the buffers in row indexes.
            old_index = {id(r.old): i for i, r in enumerate(rows) if r.old is not None}
            new_index = {id(r.new): i for i, r in enumerate(rows) if r.new is not None}
            old_emphasis: dict[int, tuple[tuple[int, int], ...]] = {}
            new_emphasis: dict[int, tuple[tuple[int, int], ...]] = {}
            for entry in emphasis:
                line = hunk.lines[entry.line_index]
                if entry.side == diffmodel.OLD and id(line) in old_index:
                    old_emphasis[old_index[id(line)]] = entry.spans
                elif entry.side == diffmodel.NEW and id(line) in new_index:
                    new_emphasis[new_index[id(line)]] = entry.spans
            old_view = self._make_view((diffmodel.OLD,))
            new_view = self._make_view((diffmodel.NEW,))
            old_view.set_rows(old_rows, old_emphasis)
            new_view.set_rows(new_rows, new_emphasis)
            self.views = [old_view, new_view]
            pane = _SplitPane(old_view.scroller, new_view.scroller, self._align_rows)
            pane.add_css_class("git-hunk-split")
            self._pane = pane
            self._body = pane
        else:
            rows = [_Row(line.kind, line.text, line.old, line.new) for line in hunk.lines]
            by_line = {entry.line_index: entry.spans for entry in emphasis}
            view = self._make_view((diffmodel.OLD, diffmodel.NEW))
            view.set_rows(rows, by_line)
            self.views = [view]
            self._body = view.scroller
        self.append(self._body)

    def apply_options(self, options: _Options) -> None:
        """Re-read the options: a layout or word-diff change rebuilds the
        body, line numbers and wrap are flipped in place."""
        previous = self._options
        self._options = options
        if options.split != previous.split or options.word_diff != previous.word_diff:
            self._build_body()
            return
        for view in self.views:
            view.set_line_numbers(options.line_numbers)
            view.set_wrap(options.wrap)
        if options.wrap and self._pane is not None:
            GLib.timeout_add(_ALIGN_SETTLE_MS, lambda: (self._align_rows(), GLib.SOURCE_REMOVE)[1])

    def set_scheme(self, scheme: GtkSource.StyleScheme | None, palette: diffmodel.DiffPalette) -> None:
        self._scheme = scheme
        self._palette = palette
        for view in self.views:
            view.set_scheme(scheme)
            view.set_palette(palette)

    # -- the split alignment pass --

    def _align_rows(self) -> None:
        """Even the two sides' row heights under wrap: whichever side's
        paragraph wrapped taller sets the row, the other gets the
        difference as pixels below its line."""
        if len(self.views) != 2 or not self._options.wrap:
            return
        old, new = self.views
        if not old.view.get_mapped() or not new.view.get_mapped():
            return
        count = min(len(old.rows), len(new.rows))
        for index in range(count):
            left, right = old.row_height(index), new.row_height(index)
            if left == 0 or right == 0:
                continue  # not validated yet; the next allocation asks again
            target = max(left, right)
            old.set_pad(index, target - left)
            new.set_pad(index, target - right)

    # -- focus --

    def _view_of(self, widget: Gtk.Widget) -> _HunkView | None:
        return next((view for view in self.views if view.view is widget), None)

    def _on_focus_enter(self, controller: Gtk.EventControllerFocus) -> None:
        view = self._view_of(controller.get_widget())
        if view is not None:
            view.set_focused(True)
        if not self._focused:
            self._focused = True
            self.add_css_class("git-hunk-focused")
            self._on_focus(self, True)

    def _on_focus_leave(self, controller: Gtk.EventControllerFocus) -> None:
        view = self._view_of(controller.get_widget())
        if view is not None:
            view.set_focused(False)
        if self._focused:
            self._focused = False
            self.remove_css_class("git-hunk-focused")
            self._on_focus(self, False)

    @property
    def focused(self) -> bool:
        return self._focused

    def grab(self, line_index: int | None = None, side: str = diffmodel.NEW) -> bool:
        """Focus the view (the *side*'s in split) with the cursor on hunk
        line *line_index* (a Line's index in hunk.lines) when given."""
        if not self.views:
            return False
        view = self.views[0]
        if len(self.views) == 2 and side == diffmodel.NEW:
            view = self.views[1]
        if line_index is not None and 0 <= line_index < len(self.hunk.lines):
            view.place_cursor(self._row_for(view, line_index))
        return view.view.grab_focus()

    def _row_for(self, view: _HunkView, line_index: int) -> int:
        line = self.hunk.lines[line_index]
        if len(self.views) == 1:
            return line_index
        wanted = line.old if view is self.views[0] else line.new
        for index, row in enumerate(view.rows):
            number = row.old if view is self.views[0] else row.new
            if number is not None and number == wanted and row.kind == line.kind:
                return index
        return 0

    def cursor_line(self) -> tuple[str, int] | None:
        """(side, 1-based line number) under the cursor of the focused
        view, for `open-requested`: the new side's number when the line has
        one, else the old side's."""
        for view in self.views:
            root = view.view.get_root()
            if view.view.has_focus() or (root is not None and root.get_focus() is view.view):
                row = view.rows[view.cursor_row()] if view.rows else None
                if row is None:
                    return None
                if row.new is not None:
                    return diffmodel.NEW, row.new
                if row.old is not None:
                    return diffmodel.OLD, row.old
        return None


class _GapRow(Gtk.Box):
    """An unchanged stretch between (or around) hunks: the `⋯ n unchanged
    lines` row with its ▲ 20 / ▼ 20 / all buttons, and the context views
    the expansions grow above and below it. Up reveals the lines just above
    the next hunk, down the lines just below the previous one; when nothing
    is left the row hides and the context stands in its place. A trailing
    gap (`gap` None) is asked about on first click: its size needs the
    file's length, which only the context read knows."""

    def __init__(
        self,
        file: diffmodel.File,
        gap: diffmodel.Gap | None,
        position: str,
        hunk_index: int,
        language: GtkSource.Language | None,
        scheme: GtkSource.StyleScheme | None,
        palette: diffmodel.DiffPalette,
        options: _Options,
        on_expand: Callable[[_GapRow, str, int], None],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.file = file
        self.gap = gap
        self.position = position
        self.hunk_index = hunk_index
        self._language = language
        self._scheme = scheme
        self._palette = palette
        self._options = options
        self._on_expand = on_expand
        self._top: _HunkView | None = None
        self._bottom: _HunkView | None = None
        self._top_lines: list[_Row] = []
        self._bottom_lines: list[_Row] = []
        self.shown_top = 0
        self.shown_bottom = 0
        self.known = gap is not None  # a trailing gap's count is read lazily

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.add_css_class("git-gap")
        self._label = Gtk.Label(xalign=0.0, hexpand=True)
        self._label.add_css_class("dim-label")
        self._label.add_css_class("caption")
        row.append(self._label)
        self._up = self._button("pan-up-symbolic", _("Expand {n} lines up").format(n=EXPAND_STEP), UP)
        self._down = self._button("pan-down-symbolic", _("Expand {n} lines down").format(n=EXPAND_STEP), DOWN)
        self._all = Gtk.Button(label=_("all"))
        self._all.add_css_class("flat")
        self._all.add_css_class("git-gap-button")
        self._all.set_tooltip_text(_("Expand every unchanged line"))
        self._all.connect("clicked", lambda _b: self._on_expand(self, ALL, 0))
        if position == diffmodel.TRAILING:
            self._up.set_visible(False)
        row.append(self._up)
        row.append(self._down)
        row.append(self._all)
        self._row = row
        self.append(row)
        self._sync_label()

    def _button(self, icon: str, tooltip: str, direction: str) -> Gtk.Button:
        button = Gtk.Button()
        button.set_child(Gtk.Box(spacing=2))
        child = button.get_child()
        child.append(Gtk.Image.new_from_icon_name(icon))
        child.append(Gtk.Label(label=str(EXPAND_STEP)))
        button.add_css_class("flat")
        button.add_css_class("git-gap-button")
        button.set_tooltip_text(tooltip)
        button.connect("clicked", lambda _b: self._on_expand(self, direction, EXPAND_STEP))
        return button

    @property
    def key(self) -> str:
        """hunk's address for this gap: `before:<i>` / `trailing:<i>`."""
        return f"{self.position}:{self.hunk_index}"

    @property
    def remaining(self) -> int:
        if self.gap is None:
            return 0
        return max(0, self.gap.count - self.shown_top - self.shown_bottom)

    def _sync_label(self) -> None:
        if self.gap is None:
            self._label.set_text("⋯" if not self.known else "")
        else:
            self._label.set_text(_("⋯ {n} unchanged lines").format(n=self.remaining))
        # A row with nothing left to show, or with no gap at all, folds away.
        self._row.set_visible(self.gap is not None and self.remaining > 0)

    def set_gap(self, gap: diffmodel.Gap | None) -> None:
        """The trailing gap's answer, once the file's length is known."""
        self.gap = gap
        self.known = True
        self._sync_label()

    def expand(self, lines: Sequence[str] | None, direction: str, count: int) -> None:
        """Draw *count* more lines (`all`: everything left, up to
        MAX_EXPAND_ALL) from *lines*, the whole file on the side the gap's
        ranges index into. None (no file to read) leaves the row as it is."""
        gap = self.gap
        if gap is None or lines is None:
            return
        span = gap.new_range or gap.old_range
        if span is None:
            return
        remaining = self.remaining
        if direction == ALL:
            count = min(remaining, MAX_EXPAND_ALL)
            direction = DOWN
        count = min(count, remaining)
        if count <= 0:
            return
        first_in_file = span[0]  # 1-based, on the side *lines* is
        old0 = gap.old_range[0] if gap.old_range else None
        new0 = gap.new_range[0] if gap.new_range else None

        def row_at(offset: int) -> _Row:
            number = first_in_file + offset
            text = lines[number - 1] if 0 <= number - 1 < len(lines) else ""
            return _Row(
                diffmodel.CONTEXT,
                text,
                old0 + offset if old0 is not None else None,
                new0 + offset if new0 is not None else None,
            )

        if direction == DOWN:
            start = self.shown_top
            self._top_lines.extend(row_at(offset) for offset in range(start, start + count))
            self.shown_top += count
            if self._top is None:
                self._top = self._context_view()
                self.insert_child_after(self._top.scroller, None)
            self._top.set_rows(self._top_lines)
        else:
            end = gap.count - self.shown_bottom  # exclusive offset
            fresh = [row_at(offset) for offset in range(end - count, end)]
            self._bottom_lines = fresh + self._bottom_lines
            self.shown_bottom += count
            if self._bottom is None:
                self._bottom = self._context_view()
                self.append(self._bottom.scroller)
            self._bottom.set_rows(self._bottom_lines)
        self._sync_label()

    def _context_view(self) -> _HunkView:
        view = _HunkView(
            self._language,
            self._scheme,
            self._palette,
            (diffmodel.OLD, diffmodel.NEW),
            sign=False,
            line_numbers=self._options.line_numbers,
            wrap=self._options.wrap,
        )
        view.view.add_css_class("git-gap-text")
        view.view.set_can_focus(False)
        return view

    @property
    def views(self) -> list[_HunkView]:
        return [view for view in (self._top, self._bottom) if view is not None]

    def apply_options(self, options: _Options) -> None:
        self._options = options
        for view in self.views:
            view.set_line_numbers(options.line_numbers)
            view.set_wrap(options.wrap)

    def set_scheme(self, scheme: GtkSource.StyleScheme | None, palette: diffmodel.DiffPalette) -> None:
        self._scheme = scheme
        self._palette = palette
        for view in self.views:
            view.set_scheme(scheme)
            view.set_palette(palette)


class _FileSection(Gtk.Box):
    """One file: the header row (fold, type icon, path — `old → new` for a
    rename — the +/− counts and what kind of change it is), then, unless
    folded, the picture for an image, and the gap rows and hunk sections
    in patch order, patched by stable key on every `update`."""

    def __init__(
        self,
        file: diffmodel.File,
        owner: DiffView,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.add_css_class("git-file")
        self.file = file
        self._owner = owner
        self._folded = False
        self._preview_key: object = None
        self._preview: Gtk.Widget | None = None
        self.hunks: list[_HunkSection] = []
        self.gaps: list[_GapRow] = []

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.add_css_class("git-file-header")
        self._fold = Gtk.Button.new_from_icon_name("pan-down-symbolic")
        self._fold.add_css_class("flat")
        self._fold.add_css_class("circular")
        self._fold.set_tooltip_text(_("Fold this file"))
        self._fold.connect("clicked", lambda _b: self.set_folded(not self._folded))
        header.append(self._fold)
        self._icon = Gtk.Image()
        header.append(self._icon)
        self._path = Gtk.Label(xalign=0.0, hexpand=True)
        self._path.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._path.add_css_class("git-file-path")
        header.append(self._path)
        self._added = Gtk.Label()
        self._added.add_css_class("caption")
        self._added.add_css_class("pr-checks-passed")
        header.append(self._added)
        self._removed = Gtk.Label()
        self._removed.add_css_class("caption")
        self._removed.add_css_class("pr-checks-failed")
        header.append(self._removed)
        self._badge = Gtk.Label()
        self._badge.add_css_class("caption")
        self._badge.add_css_class("dim-label")
        self._badge.add_css_class("git-file-badge")
        header.append(self._badge)
        # Where the next PR's Stage file / Discard file go.
        self.actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.actions.add_css_class("git-file-actions")
        header.append(self.actions)
        self.header = header
        self.append(header)

        self._body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._body.add_css_class("git-file-body")
        self.append(self._body)
        self._preview_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._body.append(self._preview_slot)
        self._slots = keyedslots.BoxSlots(self._body, anchor=self._preview_slot)
        self.update(file)

    # -- header words --

    @property
    def path(self) -> str:
        return self.file.path

    def header_text(self) -> tuple[str, str]:
        """(path label, badge) as the header shows them — what the pinned
        header copies."""
        return self._path.get_text(), self._badge.get_text()

    def _sync_header(self) -> None:
        file = self.file
        icon_name, colour = filetypes.icon_for(PurePosixPath(file.path).name)
        self._icon.set_from_icon_name(icon_name)
        self._icon.set_css_classes([colour] if colour else [])
        if file.previous_path is not None and file.previous_path != file.path:
            self._path.set_text(f"{file.previous_path} → {file.path}")
        else:
            self._path.set_text(file.path)
        self._path.set_tooltip_text(file.path)
        self._added.set_text(f"+{file.additions}")
        self._removed.set_text(f"−{file.deletions}")
        counts = file.kind not in (diffmodel.KIND_BINARY, diffmodel.KIND_MODE)
        self._added.set_visible(counts)
        self._removed.set_visible(counts)
        badge = self._badge_text(file)
        self._badge.set_text(badge)
        self._badge.set_visible(bool(badge))

    @staticmethod
    def _badge_text(file: diffmodel.File) -> str:
        words: list[str] = []
        if file.untracked:
            words.append(_("untracked"))
        elif file.kind == diffmodel.KIND_NEW:
            words.append(_("new"))
        if file.kind == diffmodel.KIND_DELETED:
            words.append(_("deleted"))
        elif file.kind == diffmodel.KIND_BINARY:
            words.append(_("binary"))
        elif file.kind == diffmodel.KIND_TOO_LARGE:
            words.append(_("too large: {n} changed lines").format(n=file.additions + file.deletions))
        elif file.kind == diffmodel.KIND_RENAME:
            words.append(
                _("renamed") if file.similarity is None else _("renamed {n}%").format(n=file.similarity)
            )
        elif file.previous_path is not None and file.similarity is not None:
            words.append(_("renamed {n}%").format(n=file.similarity))
        if file.old_mode and file.new_mode and file.old_mode != file.new_mode:
            words.append(_("mode {a} → {b}").format(a=file.old_mode, b=file.new_mode))
        return " · ".join(words)

    # -- content --

    def update(self, file: diffmodel.File) -> None:
        """Bring the section up to *file*: the header words, the picture
        (rebuilt only when the file's bytes changed), and the gaps and hunks
        patched by key — an untouched hunk keeps its widget."""
        self.file = file
        self._sync_header()
        owner = self._owner
        preview_key = (
            file.path,
            file.previous_path,
            file.kind,
            file.patch_hash,
            owner.repo_key,
            repr(owner.loaded),
        )
        if preview_key != self._preview_key:
            self._preview_key = preview_key
            self._set_preview(owner.image_preview(file))
        language = _language_for(
            file.path, file.hunks[0].lines[0].text if file.hunks and file.hunks[0].lines else ""
        )
        items: list[tuple[object, object, Callable[[], Gtk.Widget]]] = []
        gaps = {(gap.position, gap.hunk_index): gap for gap in diffmodel.gaps(file)}
        for hunk in file.hunks:
            gap = gaps.get((diffmodel.BEFORE, hunk.index))
            if gap is not None:
                items.append(
                    (
                        f"gap:{diffmodel.BEFORE}:{hunk.index}",
                        (gap.old_range, gap.new_range, gap.count, file.patch_hash),
                        lambda g=gap, h=hunk: self._make_gap(g, diffmodel.BEFORE, h.index),
                    )
                )
            items.append(
                (
                    diffmodel.stable_key(file, hunk),
                    None,  # the key says it all: equal key, equal hunk
                    lambda h=hunk, lang=language: self._make_hunk(h, lang),
                )
            )
        if file.hunks and file.kind not in (diffmodel.KIND_BINARY, diffmodel.KIND_TOO_LARGE):
            last = file.hunks[-1]
            items.append(
                (
                    f"gap:{diffmodel.TRAILING}",
                    (last.index, file.patch_hash),
                    lambda h=last: self._make_gap(None, diffmodel.TRAILING, h.index),
                )
            )
        plan = self._slots.plan(items)
        owner.park_focus(plan.dropped)
        plan.commit()
        self.hunks = [w for w in plan.widgets if isinstance(w, _HunkSection)]
        self.gaps = [w for w in plan.widgets if isinstance(w, _GapRow)]
        for section in self.hunks:
            section.file = file

    def _make_hunk(self, hunk: diffmodel.Hunk, language: GtkSource.Language | None) -> _HunkSection:
        owner = self._owner
        return _HunkSection(
            self.file, hunk, language, owner.scheme, owner.palette, owner.options, owner.on_hunk_focus
        )

    def _make_gap(self, gap: diffmodel.Gap | None, position: str, hunk_index: int) -> _GapRow:
        owner = self._owner
        language = _language_for(self.file.path)
        return _GapRow(
            self.file,
            gap,
            position,
            hunk_index,
            language,
            owner.scheme,
            owner.palette,
            owner.options,
            owner.on_gap_expand,
        )

    def _set_preview(self, preview: Gtk.Widget | None) -> None:
        if self._preview is not None:
            self._preview_slot.remove(self._preview)
        self._preview = preview
        if preview is not None:
            preview.add_css_class("git-file-preview")
            self._preview_slot.append(preview)
        self._preview_slot.set_visible(preview is not None)

    def set_folded(self, folded: bool) -> None:
        self._folded = folded
        self._body.set_visible(not folded)
        self._fold.set_icon_name("pan-end-symbolic" if folded else "pan-down-symbolic")
        self._fold.set_tooltip_text(_("Unfold this file") if folded else _("Fold this file"))

    @property
    def folded(self) -> bool:
        return self._folded

    def apply_options(self, options: _Options) -> None:
        for section in self.hunks:
            section.apply_options(options)
        for gap in self.gaps:
            gap.apply_options(options)

    def set_scheme(self, scheme: GtkSource.StyleScheme | None, palette: diffmodel.DiffPalette) -> None:
        for section in self.hunks:
            section.set_scheme(scheme, palette)
        for gap in self.gaps:
            gap.set_scheme(scheme, palette)

    def matches(self, needle: str) -> bool:
        """The files-list filter's rule: a case-insensitive substring of the
        path (or the old path of a rename)."""
        if not needle:
            return True
        folded = needle.casefold()
        return folded in self.file.path.casefold() or (
            self.file.previous_path is not None and folded in self.file.previous_path.casefold()
        )


# -- the view -----------------------------------------------------------------


class DiffView(Gtk.Box):
    """The git page's review stream: every file of a load, drawn native.
    See the module docstring for the shape; the public face is below."""

    __gtype_name__ = "CollinsDiffView"

    __gsignals__ = {
        # The file / hunk the sidebar should highlight moved: the file at
        # the top of the viewport (hunk -1 when none of its hunks is), or
        # the hunk the keyboard moved into. (path "" = nothing loaded.)
        "current-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, int)),
        # Open this file in the editor at this 1-based line (0: unknown) —
        # the `e` key / context-menu item of later PRs; nothing emits it yet
        # but `request_open`.
        "open-requested": (GObject.SignalFlags.RUN_FIRST, None, (str, int)),
        # A gap is being expanded: the file, the gap's address
        # (`before:<i>` / `trailing:<i>`) and the line count asked for
        # (0 = all). Informational — the view reads the context itself.
        "context-requested": (GObject.SignalFlags.RUN_FIRST, None, (str, str, int)),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("git-diff")
        self.set_hexpand(True)
        self.set_vexpand(True)
        self._files: list[diffmodel.File] = []
        self.loaded: object = None
        self.repo_key = ""
        self._reader: ContextReader | None = None
        self._gen = 0
        self._context_cache: dict[tuple[str, str], list[str] | None] = {}
        self._pending: dict[tuple[str, str], list[Callable[[list[str] | None], None]]] = {}
        self.scheme: GtkSource.StyleScheme | None = None
        self._dark = False
        self.palette = diffmodel.palette(None, None, None, False)
        self._layout = LAYOUT_AUTO
        self._narrow = False
        self._line_numbers = True
        self._wrap = False
        self._word_diff = True
        self.options = _Options(split=False, line_numbers=True, wrap=False, word_diff=True)
        self._filter = ""
        self._current: tuple[str, int] = ("", -1)
        self._focused_hunk: _HunkSection | None = None
        self._refocus = False
        self._scroll_source = 0

        # The breakpoint is what resolves `auto`: split at SPLIT_MIN_WIDTH
        # and up, stack below. The bin wants a floor on both axes or it
        # warns per allocation; this one is the page's, not a real minimum.
        bin_ = Adw.BreakpointBin()
        bin_.set_size_request(200, 100)
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(f"max-width: {SPLIT_MIN_WIDTH - 1}px"))
        breakpoint.connect("apply", lambda _b: self._set_narrow(True))
        breakpoint.connect("unapply", lambda _b: self._set_narrow(False))
        bin_.add_breakpoint(breakpoint)
        self._bin = bin_

        self._stack = Gtk.Stack()
        self._stack.set_hhomogeneous(False)
        self._stack.set_vhomogeneous(False)
        empty = Adw.StatusPage()
        empty.set_icon_name("git-merge-symbolic")
        empty.set_title(_("No changes"))
        empty.add_css_class("compact")
        self._empty = empty
        self._stack.add_named(empty, "empty")

        overlay = Gtk.Overlay()
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_hexpand(True)
        scroller.set_vexpand(True)
        self._scroller = scroller
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        column.set_margin_start(6)
        column.set_margin_end(6)
        column.set_margin_top(6)
        column.set_margin_bottom(12)
        column.add_css_class("git-diff-column")
        self._column = column
        scroller.set_child(column)
        self._slots = keyedslots.BoxSlots(column)
        overlay.set_child(scroller)
        pinned = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        pinned.add_css_class("git-pinned-header")
        pinned.set_valign(Gtk.Align.START)
        pinned.set_halign(Gtk.Align.FILL)
        self._pinned_icon = Gtk.Image()
        pinned.append(self._pinned_icon)
        self._pinned_path = Gtk.Label(xalign=0.0, hexpand=True)
        self._pinned_path.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._pinned_path.add_css_class("git-file-path")
        pinned.append(self._pinned_path)
        self._pinned_badge = Gtk.Label()
        self._pinned_badge.add_css_class("caption")
        self._pinned_badge.add_css_class("dim-label")
        pinned.append(self._pinned_badge)
        pinned.set_visible(False)
        pinned.set_can_target(False)
        self._pinned = pinned
        overlay.add_overlay(pinned)
        self._stack.add_named(overlay, "files")
        bin_.set_child(self._stack)
        self.append(bin_)
        scroller.get_vadjustment().connect("value-changed", self._on_scrolled)
        scroller.get_vadjustment().connect("changed", self._on_scrolled)
        self.connect("destroy", self._on_destroy)

    # -- public face -------------------------------------------------------------

    def set_scheme(self, scheme: GtkSource.StyleScheme | None, dark: bool) -> None:
        """Restyle every buffer with the editor's *scheme* (editor.style_scheme
        already resolved "" against *dark*); the palette follows."""
        self.scheme = scheme
        self._dark = dark
        self.palette = palette_for(scheme, dark)
        for section in self._sections():
            section.set_scheme(scheme, self.palette)

    def set_options(self, layout: str, line_numbers: bool, wrap: bool, word_diff: bool) -> None:
        """Preferences → Git's words: *layout* one of gitloads.LAYOUTS
        (`auto` resolved by width), the line-number columns, wrap, the word
        emphasis pass. Applied to every section now and to the ones built
        later."""
        self._layout = layout if layout in gitloads.LAYOUTS else LAYOUT_AUTO
        self._line_numbers = bool(line_numbers)
        self._wrap = bool(wrap)
        self._word_diff = bool(word_diff)
        self._sync_options()

    def load(
        self,
        files: Sequence[diffmodel.File],
        load: object,
        context_reader: ContextReader | None,
        repo: str = "",
    ) -> None:
        """Show *files* (gitops.read_diff's, in order) for the Loaded *load*.
        *context_reader(file, side)* is the blocking read of a whole file on
        one side (gitops.file_at through gitops.side_ref), called on a
        thread when a gap expands or an image preview is drawn; *repo*
        names the repository for the preview cache's keys. Sections are
        patched by key: a file keeps its section, an untouched hunk its
        widget, the scroll its place."""
        self._gen += 1
        self.loaded = load
        self.repo_key = repo
        self._reader = context_reader
        self._context_cache = {}
        self._pending = {}
        self._files = list(files)
        items: list[tuple[object, object, Callable[[], Gtk.Widget]]] = [
            ((file.previous_path, file.path), None, lambda f=file: _FileSection(f, self))
            for file in self._files
        ]
        plan = self._slots.plan(items)
        self.park_focus(plan.dropped)
        keyedslots.pin_scroll(self._scroller, plan.kept)
        plan.commit()
        by_key = {(file.previous_path, file.path): file for file in self._files}
        for section in self._sections():
            file = by_key.get((section.file.previous_path, section.file.path))
            if file is not None and section not in plan.built:
                section.update(file)
            section.set_visible(section.matches(self._filter))
        self._stack.set_visible_child_name("files" if self._files else "empty")
        if self._refocus:
            # The keyboard was parked off a dropped hunk: back into the view.
            self._refocus = False
            self.grab_focus()
        self._schedule_scroll_sync()

    def reveal(
        self, path: str, hunk: int | None = None, side: str | None = None, line: int | None = None
    ) -> bool:
        """Scroll to *path*'s section — or its hunk *hunk* (0-based), or the
        hunk holding 1-based *line* on *side* (new by default) — and focus
        that hunk's view with the cursor on the line. False when the file
        (or the line) isn't in the load. Synchronous."""
        side = side if side in diffmodel.SIDES else diffmodel.NEW
        section = self._section_for(path, side)
        if section is None:
            return False
        line_index: int | None = None
        if hunk is None and line is not None:
            located = diffmodel.locate(self._files, section.file.path, side, line)
            if located is None:
                return False
            _file, hunk, line_index = located
        section.set_folded(False)
        target: _HunkSection | None = None
        if hunk is not None and 0 <= hunk < len(section.hunks):
            target = section.hunks[hunk]
        elif section.hunks:
            target = section.hunks[0]
        keyedslots.scroll_to(self._scroller, target if hunk is not None and target is not None else section)
        if target is not None:
            target.grab(line_index, side)
            self._set_current(section.file.path, target.hunk.index)
        else:
            self._set_current(section.file.path, -1)
        return True

    def current(self) -> tuple[str | None, int | None, None]:
        """(path, hunk index, selection) — the file the reader is in (the
        focused hunk's, else the one at the top of the viewport), its
        current hunk (None when none), and the line selection (None until
        the next PR)."""
        path, hunk = self._current
        return (path or None, hunk if hunk >= 0 else None, None)

    def filter(self, text: str) -> int:
        """Show only the files whose path contains *text* (case-insensitive);
        "" shows all. Sections hide, they are not destroyed. Returns how
        many are shown."""
        self._filter = (text or "").strip()
        shown = 0
        for section in self._sections():
            visible = section.matches(self._filter)
            section.set_visible(visible)
            shown += visible
        self._schedule_scroll_sync()
        return shown

    def focus_hunk(self, delta: int) -> bool:
        """Move the keyboard *delta* hunks on from the current one (the
        first / last when nothing is focused), across files, skipping
        hidden sections; scrolls it into view."""
        hunks = [h for s in self._sections() if s.get_visible() and not s.folded for h in s.hunks]
        if not hunks:
            return False
        index = self._focused_index(hunks)
        if index is None:
            index = 0 if delta >= 0 else len(hunks) - 1
        else:
            index = max(0, min(len(hunks) - 1, index + delta))
        return self._focus(hunks[index])

    def focus_file(self, delta: int) -> bool:
        """Move the keyboard to the first hunk of the file *delta* files on
        from the current one (a placeholder file with no hunks is skipped)."""
        sections = [s for s in self._sections() if s.get_visible() and s.hunks]
        if not sections:
            return False
        current_path = self._current[0]
        index = next((i for i, s in enumerate(sections) if s.file.path == current_path), None)
        if index is None:
            index = 0 if delta >= 0 else len(sections) - 1
        else:
            index = max(0, min(len(sections) - 1, index + delta))
        section = sections[index]
        section.set_folded(False)
        return self._focus(section.hunks[0])

    def request_open(self) -> bool:
        """Emit `open-requested` for the file and line under the cursor."""
        focused = self._focused_hunk
        if focused is None:
            path = self._current[0]
            if not path:
                return False
            self.emit("open-requested", path, 0)
            return True
        where = focused.cursor_line()
        self.emit("open-requested", focused.file.path, where[1] if where else 0)
        return True

    # -- probes (the e2e's) --

    def file_rows(self) -> list[tuple[str, str, bool]]:
        """(path, kind, shown) per section, in order."""
        return [(s.file.path, s.file.kind, s.get_visible()) for s in self._sections()]

    def hunk_rows(self, path: str) -> list[str]:
        """The hunk headers of *path*'s section, in order."""
        section = self._section_for(path, diffmodel.NEW)
        return [h.hunk.header for h in section.hunks] if section is not None else []

    def gap_rows(self, path: str) -> list[tuple[str, int, int]]:
        """(address, remaining, shown) per gap row of *path*."""
        section = self._section_for(path, diffmodel.NEW)
        if section is None:
            return []
        return [(g.key, g.remaining, g.shown_top + g.shown_bottom) for g in section.gaps]

    def expand_gap(self, path: str, address: str, direction: str = DOWN, count: int = EXPAND_STEP) -> bool:
        """Expand *path*'s gap *address* as a click on its button would."""
        section = self._section_for(path, diffmodel.NEW)
        if section is None:
            return False
        for gap in section.gaps:
            if gap.key == address:
                self.on_gap_expand(gap, direction, count)
                return True
        return False

    def is_split(self) -> bool:
        return self.options.split

    def pinned_header_text(self) -> str | None:
        return self._pinned_path.get_text() if self._pinned.get_visible() else None

    def do_grab_focus(self) -> bool:
        """The composite's focus lands on the current hunk's view (a Box's
        own grab stops at the scroller)."""
        hunks = [h for s in self._sections() if s.get_visible() and not s.folded for h in s.hunks]
        if not hunks:
            return False
        index = self._focused_index(hunks)
        if index is None:
            path, hunk = self._current
            index = next((i for i, h in enumerate(hunks) if h.file.path == path and h.hunk.index == hunk), 0)
        return hunks[index].grab()

    # -- what the sections call back --------------------------------------------

    def on_hunk_focus(self, section: _HunkSection, focused: bool) -> None:
        if focused:
            self._focused_hunk = section
            self._set_current(section.file.path, section.hunk.index)
        elif self._focused_hunk is section:
            self._focused_hunk = None

    def on_gap_expand(self, gap: _GapRow, direction: str, count: int) -> None:
        """A gap's button: read the file on the gap's side (a thread), then
        draw the lines. A trailing gap is measured first."""
        file = gap.file
        self.emit("context-requested", file.path, gap.key, 0 if direction == ALL else count)
        side = self._gap_side(gap)
        if side is None:
            return

        def landed(lines: list[str] | None) -> None:
            if gap.get_parent() is None:
                return
            if gap.position == diffmodel.TRAILING and not gap.known:
                length = len(lines) if lines is not None else None
                old_len = length if side == diffmodel.OLD else None
                new_len = length if side == diffmodel.NEW else None
                trailing = [
                    g for g in diffmodel.gaps(file, old_len, new_len) if g.position == diffmodel.TRAILING
                ]
                gap.set_gap(trailing[0] if trailing else None)
            gap.expand(lines, direction, count)

        self._read_context(file, side, landed)

    def park_focus(self, going: Sequence[Gtk.Widget]) -> None:
        """Move the keyboard off widgets about to be removed, so GTK does
        not re-place it somewhere the view didn't choose."""
        root = self.get_root()
        focus = root.get_focus() if root is not None else None
        if focus is None:
            return
        for widget in going:
            if focus is widget or focus.is_ancestor(widget):
                self._focused_hunk = None
                self._refocus = True
                root.set_focus(None)
                return

    def image_preview(self, file: diffmodel.File) -> Gtk.Widget | None:
        """The before / after pictures for an image *file*, their bytes read
        through the context reader on a thread; None for a text file or
        with no reader."""
        if self._reader is None or not prblobs.is_image(file.path):
            return None
        sides: list[imagediff.ImageSide] = []
        if file.kind == diffmodel.KIND_NEW or file.untracked:
            wanted = [diffmodel.NEW]
        elif file.kind == diffmodel.KIND_DELETED:
            wanted = [diffmodel.OLD]
        else:
            wanted = [diffmodel.OLD, diffmodel.NEW]
        both = len(wanted) > 1
        reader = self._reader
        for side in wanted:
            path = file.previous_path if side == diffmodel.OLD and file.previous_path else file.path
            key = f"git-blob://{self.repo_key}/{self.loaded!r}/{side}/{path}#{file.patch_hash}"
            caption = "" if not both else (_("Before") if side == diffmodel.OLD else _("After"))
            sides.append(
                imagediff.ImageSide(
                    key=key,
                    path=path,
                    caption=caption,
                    fetcher=lambda key=key, file=file, side=side, path=path: _blob_to_file(
                        key, path, reader(file, side)
                    ),
                )
            )
        return imagediff.preview_row(sides)

    # -- internals ----------------------------------------------------------------

    def _sections(self) -> list[_FileSection]:
        return [w for w in self._slots.widgets if isinstance(w, _FileSection)]

    def _section_for(self, path: object, side: str) -> _FileSection | None:
        if not isinstance(path, str) or not path:
            return None
        for section in self._sections():
            file = section.file
            if file.path == path or (side == diffmodel.OLD and file.previous_path == path):
                return section
        return None

    def _focused_index(self, hunks: list[_HunkSection]) -> int | None:
        focused = self._focused_hunk
        if focused is None:
            return None
        return next((i for i, h in enumerate(hunks) if h is focused), None)

    def _focus(self, section: _HunkSection) -> bool:
        keyedslots.scroll_to(self._scroller, section)
        ok = section.grab()
        self._set_current(section.file.path, section.hunk.index)
        return ok

    def _set_current(self, path: str, hunk: int) -> None:
        if (path, hunk) == self._current:
            return
        self._current = (path, hunk)
        self.emit("current-changed", path, hunk)

    def _set_narrow(self, narrow: bool) -> None:
        if narrow != self._narrow:
            self._narrow = narrow
            self._sync_options()

    def _sync_options(self) -> None:
        split = self._layout == LAYOUT_SPLIT or (self._layout == LAYOUT_AUTO and not self._narrow)
        options = _Options(
            split=split, line_numbers=self._line_numbers, wrap=self._wrap, word_diff=self._word_diff
        )
        if options == self.options:
            return
        self.options = options
        for section in self._sections():
            section.apply_options(options)

    # -- scroll → current file, pinned header --

    def _on_scrolled(self, _adjustment) -> None:
        self._schedule_scroll_sync()

    def _schedule_scroll_sync(self) -> None:
        if self._scroll_source:
            GLib.source_remove(self._scroll_source)
        self._scroll_source = GLib.timeout_add(_SCROLL_SETTLE_MS, self._sync_from_scroll)

    def _sync_from_scroll(self) -> bool:
        self._scroll_source = 0
        top: _FileSection | None = None
        top_y = 0.0
        for section in self._sections():
            if not section.get_visible():
                continue
            ok, bounds = section.compute_bounds(self._scroller)
            if ok and bounds.get_y() + bounds.get_height() > 0:
                top, top_y = section, bounds.get_y()
                break
        self._sync_pinned(top, top_y)
        if top is None:
            self._set_current("", -1)
            return GLib.SOURCE_REMOVE
        # A hunk the keyboard is in, still on screen, stays current: the
        # scroll takes over once it has left the viewport.
        focused = self._focused_hunk
        if focused is not None and focused.get_parent() is not None and self._in_view(focused):
            return GLib.SOURCE_REMOVE
        hunk = -1
        for section in top.hunks:
            ok, bounds = section.compute_bounds(self._scroller)
            if ok and bounds.get_y() + bounds.get_height() > 0:
                hunk = section.hunk.index
                break
        self._set_current(top.file.path, hunk)
        return GLib.SOURCE_REMOVE

    def _in_view(self, widget: Gtk.Widget) -> bool:
        ok, bounds = widget.compute_bounds(self._scroller)
        if not ok:
            return False
        height = self._scroller.get_height()
        return bounds.get_y() + bounds.get_height() > 0 and bounds.get_y() < height

    def _sync_pinned(self, top: _FileSection | None, top_y: float) -> None:
        """The pinned header shows while the top section's own header has
        scrolled off (its top above the viewport by more than its height)."""
        if top is None:
            self._pinned.set_visible(False)
            return
        header_height = top.header.get_height() or 0
        show = top_y < -header_height
        if show:
            path, badge = top.header_text()
            self._pinned_path.set_text(path)
            self._pinned_badge.set_text(badge)
            self._pinned_badge.set_visible(bool(badge))
            icon_name, colour = filetypes.icon_for(PurePosixPath(top.file.path).name)
            self._pinned_icon.set_from_icon_name(icon_name)
            self._pinned_icon.set_css_classes([colour] if colour else [])
        self._pinned.set_visible(show)

    # -- context reads --

    @staticmethod
    def _gap_side(gap: _GapRow) -> str | None:
        """Which side a gap's lines are read from: the new side when the
        file has one there, else the old."""
        if gap.gap is not None:
            if gap.gap.new_range is not None:
                return diffmodel.NEW
            return diffmodel.OLD if gap.gap.old_range is not None else None
        # A trailing gap not yet measured: the side the file exists on after.
        return diffmodel.OLD if gap.file.kind == diffmodel.KIND_DELETED else diffmodel.NEW

    def _read_context(
        self, file: diffmodel.File, side: str, callback: Callable[[list[str] | None], None]
    ) -> None:
        key = (diffmodel.stable_key(file), side)
        if key in self._context_cache:
            callback(self._context_cache[key])
            return
        waiting = self._pending.get(key)
        if waiting is not None:
            waiting.append(callback)
            return
        self._pending[key] = [callback]
        reader = self._reader
        gen = self._gen

        def work() -> None:
            lines: list[str] | None = None
            try:
                data = reader(file, side) if reader is not None else None
                lines = _decode_lines(data)
            except Exception:  # a reader must never take the view with it
                log.debug("diffview: context read failed for %s", file.path, exc_info=True)
            GLib.idle_add(land, lines, priority=GLib.PRIORITY_DEFAULT)

        def land(lines: list[str] | None) -> bool:
            if gen != self._gen:
                return GLib.SOURCE_REMOVE  # a later load; its own reads answer
            self._context_cache[key] = lines
            for waiting in self._pending.pop(key, []):
                waiting(lines)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, daemon=True, name="collins-diff-context").start()

    def _on_destroy(self, *_args) -> None:
        if self._scroll_source:
            GLib.source_remove(self._scroll_source)
            self._scroll_source = 0
        self._gen += 1


def _blob_to_file(key: str, path: str, data: bytes | None) -> Path:
    """`imagediff`'s fetcher half for a git blob: *data* saved under the
    cache as a file named by the key, so the picture can go to the
    lightbox (and to another app) from disk. Raises for no data — the
    stand-in's reason. Worker thread."""
    if data is None:
        raise ValueError(_("No such file on this side."))
    directory = cache_directory() / "git-blobs"
    directory.mkdir(parents=True, exist_ok=True)
    remoteimages.prune_stale(directory)
    suffix = PurePosixPath(path).suffix.lower()[:8]
    target = directory / (hashlib.sha1(key.encode("utf-8", "replace")).hexdigest() + suffix)
    if not target.exists():
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(target)
    return target
