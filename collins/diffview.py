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
`_MarkerRenderer` column for the notes and highlights (the glyph beside a
line carrying one). Row
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
`filter`, `focus_hunk` / `focus_file` / `focus_annotated`,
`expand_gap_before_focus`, the find bar's `search` / `search_step` /
`search_position` / `search_clear` (plain-text, case-insensitive matches
over the shown hunks' rows, counted here so "3 of 12" is exact and
synchronous — a GtkSource.SearchContext per buffer only paints the
highlights), `apply_keybindings` (the `git.*` chords, a capture-phase
controller scoped to the view: bare letters must beat the text views and
never reach the agent's terminal), the notes and highlights — `notes` /
`highlights`, `add_notes` / `add_highlights` / `clear_marks` (the agent
tools' doors: a batch lands whole or not at all), `add_note_at_cursor`
(`c`), `edit_first_note` (`E`), `delete_note` (the card's *Delete*; its
*Edit* is the card's own), `set_agent_notes_shown` (`a`), `editing` — held in a
diffnotes.MarkStore for the tab's life and drawn as `_NoteCard`s under
their hunk with a glyph in the marker column (decision 5), the probes
`file_rows` / `hunk_rows` / `note_rows`, and the signals
`current-changed(path, hunk)`, `open-requested(path, line)`,
`context-requested(path, gap, count)`, `mutation-requested(request)`,
`notes-changed()`, `editing-changed(bool)`.
Widget code: exercised by scripts/probe_diffview.py and the git page's e2e,
not the unit suite.
"""

from __future__ import annotations

import hashlib
import itertools
import logging
import re
import threading
import weakref
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402

from . import (  # noqa: E402
    diffmodel,
    diffnotes,
    editorfiles,
    filetypes,
    gitloads,
    gitpatch,
    imagediff,
    keybindings,
    keyedslots,
    keymap,
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
# The most matches the find bar walks: a search for a single letter over a
# huge diff stops counting here and says so through the total.
MAX_SEARCH_MATCHES = 5_000
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
# The action group each hunk section inserts on itself for its context
# menu (the popover finds it from its parent view, up the tree).
_HUNK_ACTIONS = "hunk"
# The marker column's glyphs (bundled icons: the theme's may be absent
# under CI), and what a highlight of each tone paints its range with —
# Gtk.TextTag properties; `current` is the reverse-video stand-in.
_NOTE_ICON = "chat-bubble-symbolic"
_HIGHLIGHT_ICON = "circle-fill-symbolic"
_TONE_STYLES: dict[str, dict[str, str]] = {
    diffnotes.TONE_MATCH: {"background": "rgba(255,196,0,0.38)"},
    diffnotes.TONE_CURRENT: {"background": "#3584e4", "foreground": "#ffffff"},
    diffnotes.TONE_INFO: {"background": "rgba(53,132,228,0.30)"},
    diffnotes.TONE_WARNING: {"background": "rgba(255,120,0,0.35)"},
    diffnotes.TONE_ERROR: {"background": "rgba(224,27,36,0.35)"},
    diffnotes.TONE_DIM: {"foreground": "#8c8c8c"},
}
# The most pixels a note's editor grows to before it scrolls.
_NOTE_EDITOR_MAX_HEIGHT = 220
# How long a dropped note card stays in the tree, hidden, before it is
# unparented. A Gtk.TextView unrealized within a few milliseconds of its
# keyboard focus leaving segfaults GTK's Wayland input method (measured
# in scripts/probe_diffview.py --notes, split layout: the compositor's
# text-input reply lands after the widget is gone and the handler asks
# it for its display); with half a second in between it never has.
_CARD_REAP_MS = 500

ContextReader = Callable[[diffmodel.File, str], "bytes | None"]


@dataclass(frozen=True)
class _Row:
    """One paragraph of a hunk view's buffer: what kind of line it draws
    (CONTEXT / ADD / DEL / PAD), its text, its number on each side, and
    the index of the patch line it shows in `hunk.lines` (None for a
    padding cell and for expanded context, which no selection counts)."""

    kind: str
    text: str
    old: int | None
    new: int | None
    line: int | None = None


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


def _selection_bounds(buffer: Gtk.TextBuffer) -> tuple[Gtk.TextIter, Gtk.TextIter] | None:
    """(start, end) of the buffer's selection, or None. PyGObject hands
    `get_selection_bounds` back as an empty tuple with no selection and
    as the two iters (sometimes behind the flag) with one."""
    if not buffer.get_has_selection():
        return None
    bounds = buffer.get_selection_bounds()
    if not bounds or len(bounds) < 2:
        return None
    start, end = bounds[-2], bounds[-1]
    return (start, end) if not start.equal(end) else None


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
    highlight. Empty until `set_marks` names one; sized to the
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
        on_selection: Callable[[_HunkView], None] | None = None,
    ) -> None:
        self.rows: list[_Row] = []
        self.marks: dict[int, str] = {}  # row index → marker icon (set_marks)
        # The highlights on this view's rows: (row, start, end, tone), and
        # one tag per tone, made as a tone first shows (set_highlights).
        self.highlights: list[tuple[int, int, int, str]] = []
        self._highlight_tags: dict[str, Gtk.TextTag] = {}
        self._pads: dict[int, int] = {}
        self._pad_tags: dict[int, Gtk.TextTag] = {}
        # The selection model (decision 4): the buffer's own selection,
        # snapped to whole paragraphs on every move of its two marks —
        # a drag in the text, Shift+arrows, a drag or click on the line
        # numbers (the gutter gesture below) all land in one select_range.
        # *on_selection* hears every settled move; _snapping guards the
        # re-entry the snap's own select_range causes.
        self._on_selection = on_selection
        self._snapping = False
        self._gutter_anchor: int | None = None
        # Whether the buffer's selection is a line selection of the
        # model's (snapped here) rather than the find bar's current match
        # (select_match, left as the characters it found): only the first
        # counts for the buttons and the keys.
        self._owned = False
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
        if on_selection is not None:
            buffer.connect("mark-set", self._on_mark_set)
            # Click, shift-click or drag on the line numbers: whole lines,
            # from the press to the pointer. The gutter is the LEFT text
            # window's widget, so its y is that window's.
            drag = Gtk.GestureDrag()
            drag.connect("drag-begin", self._on_gutter_drag_begin)
            drag.connect("drag-update", self._on_gutter_drag_update)
            gutter.add_controller(drag)
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
        # A dropped view is unrealized: the idle it may hold is let go
        # there rather than run over a scroller nobody shows (a re-parented
        # one re-validates its layout on realize and re-arms it).
        scroller.connect("unrealize", lambda _w: self._cancel_remeasure())
        self.set_wrap(wrap)

    def _on_layout_height_changed(self, _adjustment: Gtk.Adjustment) -> None:
        if self._remeasure_source:
            return
        self._remeasure_source = GLib.idle_add(self._remeasure, priority=GLib.PRIORITY_DEFAULT)

    def _cancel_remeasure(self) -> None:
        if self._remeasure_source:
            GLib.source_remove(self._remeasure_source)
            self._remeasure_source = 0

    def _remeasure(self) -> bool:
        self._remeasure_source = 0
        self.scroller.queue_resize()
        return GLib.SOURCE_REMOVE

    # -- content --

    def set_rows(
        self, rows: Sequence[_Row], emphasis: dict[int, tuple[tuple[int, int], ...]] | None = None
    ) -> None:
        """Fill the buffer with *rows* and tag them; *emphasis* maps a row
        index to the character spans of its text to emphasise.

        The rows kept are what the buffer shows: a patch line's text put
        through diffmodel.display_text (a trailing CR dropped, a lone CR
        or U+2029 — which GtkTextBuffer would split a paragraph on, so
        paragraph i would no longer be row i — shown as a symbol), so the
        search's offsets over `rows` and the buffer agree; a span past a
        dropped CR is clamped to the line.
        """
        self.rows = [
            row
            if row.kind == PAD
            else _Row(row.kind, diffmodel.display_text(row.text), row.old, row.new, row.line)
            for row in rows
        ]
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
                width = len(row.text)
                for first, last in spans:
                    last = min(last, width)
                    if first >= last:
                        continue
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
        self.marks = dict(icons)
        self._marker.set_marks(icons)

    def set_highlights(self, entries: Sequence[tuple[int, int, int, str]]) -> None:
        """Paint *entries* — (row, start, end, tone): code points [start,
        end) of paragraph *row* in the tone's tag (_TONE_STYLES) — in
        place of whatever was painted before. A span past the row's
        text (a dropped trailing CR) is clamped to it."""
        buffer = self.buffer
        if self._highlight_tags:
            start, end = buffer.get_bounds()
            for tag in self._highlight_tags.values():
                buffer.remove_tag(tag, start, end)
        self.highlights = []
        for row, first, last, tone in entries:
            style = _TONE_STYLES.get(tone)
            if style is None or not 0 <= row < len(self.rows):
                continue
            tag = self._highlight_tags.get(tone)
            if tag is None:
                tag = buffer.create_tag(f"git-hl-{tone}", **style)
                self._highlight_tags[tone] = tag
            width = len(self.rows[row].text)
            last = min(last, width)
            if first >= last:
                continue
            ok_a, a = buffer.get_iter_at_line_offset(row, first)
            ok_b, b = buffer.get_iter_at_line_offset(row, last)
            if ok_a and ok_b and a.get_line() == row and b.get_line() == row:
                buffer.apply_tag(tag, a, b)
                self.highlights.append((row, first, last, tone))

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
        """The row the insert mark is on — for a line selection, its last
        selected row whichever end the mark is on: the snap parks a
        downward drag's mark at the start of the line after, which is no
        row the user chose, and an upward drag's (Shift+Up, a drag that
        ends above where it started) at the first row. `c` and `e` speak
        of the selection's last line either way."""
        rows = self.selected_rows()
        if rows is not None:
            return rows[1]
        return self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_line()

    # -- the selection --

    def _on_mark_set(self, buffer: Gtk.TextBuffer, _iter: Gtk.TextIter, mark: Gtk.TextMark) -> None:
        if self._snapping or mark.get_name() not in ("insert", "selection_bound"):
            return
        self._snapping = True
        try:
            self._snap()
            self._owned = _selection_bounds(buffer) is not None
        finally:
            self._snapping = False
        if self._on_selection is not None:
            self._on_selection(self)

    def select_match(self, row: int, start: int, end: int) -> bool:
        """Select the find bar's match — characters *start*..*end* of
        paragraph *row* — without it becoming a line selection: the snap
        stays out, and the buttons read the hunk as unselected."""
        ok_a, first = self.buffer.get_iter_at_line_offset(row, start)
        ok_b, last = self.buffer.get_iter_at_line_offset(row, end)
        if not (ok_a and ok_b):
            return False
        self._snapping = True
        try:
            self.buffer.select_range(first, last)
            self._owned = False
        finally:
            self._snapping = False
        if self._on_selection is not None:
            self._on_selection(self)
        return True

    def _snap(self) -> None:
        """Widen the buffer's selection to whole paragraphs — the start of
        its first line to the start of the line after its last (the
        newline included, so the highlight runs to the edge) — keeping
        which end the insert mark is on, so Shift+arrows keep extending
        from the same side. Nothing to do without a selection, or when
        it is already snapped (the re-entry from select_range)."""
        buffer = self.buffer
        bounds = _selection_bounds(buffer)
        if bounds is None:
            return
        start, end = bounds
        first = start.get_line()
        last = end.get_line()
        if last > first and end.get_line_offset() == 0:
            last -= 1  # the selection ends exactly on the newline of *last*
        _ok, a = buffer.get_iter_at_line(first)
        _ok, b = buffer.get_iter_at_line(last)
        if not b.ends_line():
            b.forward_to_line_end()
        b.forward_char()  # the newline; a no-op on the last line
        if start.equal(a) and end.equal(b):
            return
        insert = buffer.get_iter_at_mark(buffer.get_insert())
        if insert.equal(end):
            buffer.select_range(b, a)
        else:
            buffer.select_range(a, b)

    def selected_rows(self) -> tuple[int, int] | None:
        """The inclusive row span the buffer's (snapped) selection covers,
        or None — for the find bar's match too."""
        if not self._owned:
            return None
        bounds = _selection_bounds(self.buffer)
        if bounds is None:
            return None
        start, end = bounds
        first = start.get_line()
        last = end.get_line()
        if last > first and end.get_line_offset() == 0:
            last -= 1
        last = min(last, len(self.rows) - 1)
        if last < first:
            return None
        return first, last

    def select_rows(self, first: int, last: int) -> None:
        """Select rows *first*..*last* (inclusive, either order) as a drag
        would — the insert mark at the end, so Shift+Down extends it."""
        if not self.rows:
            return
        first, last = sorted((max(0, first), min(last, len(self.rows) - 1)))
        _ok, a = self.buffer.get_iter_at_line(first)
        _ok, b = self.buffer.get_iter_at_line(last)
        if not b.ends_line():
            b.forward_to_line_end()
        b.forward_char()
        self.buffer.select_range(b, a)

    def clear_selection(self) -> None:
        buffer = self.buffer
        if buffer.get_has_selection():
            buffer.place_cursor(buffer.get_iter_at_mark(buffer.get_insert()))

    def selection_text(self) -> str:
        """The selected text, or with no selection the whole view's."""
        bounds = _selection_bounds(self.buffer)
        start, end = bounds if bounds is not None else self.buffer.get_bounds()
        return self.buffer.get_text(start, end, False)

    def _gutter_row(self, y: float) -> int | None:
        """The row under gutter y (the LEFT text window's coordinates)."""
        if not self.rows:
            return None
        _x, buffer_y = self.view.window_to_buffer_coords(Gtk.TextWindowType.LEFT, 0, int(y))
        # PyGObject hands back (target_iter, line_top) — the C out
        # parameters in order, no boolean first.
        it, _top = self.view.get_line_at_y(buffer_y)
        return min(it.get_line(), len(self.rows) - 1)

    def gutter_y(self, row: int) -> float | None:
        """Probe: a y in the gutter (the LEFT text window's coordinates)
        over *row*, the inverse of _gutter_row — None before the view has
        a layout."""
        if not self.rows or not 0 <= row < len(self.rows):
            return None
        _ok, it = self.buffer.get_iter_at_line(row)
        rect = self.view.get_iter_location(it)
        if rect.height <= 0:
            return None
        middle = rect.y + rect.height // 2
        _wx, wy = self.view.buffer_to_window_coords(Gtk.TextWindowType.LEFT, rect.x, middle)
        return float(wy)

    def gutter_press(self, y: float, shift: bool = False) -> bool:
        """A press on the line numbers at gutter *y*: the row under it is
        selected (with Shift, the selection extends from the far end of
        what is selected); a drag then goes through gutter_extend."""
        row = self._gutter_row(y)
        if row is None:
            return False
        current = self.selected_rows()
        if shift and current is not None:
            self._gutter_anchor = current[0] if row >= current[0] else current[1]
        else:
            self._gutter_anchor = row
        self.view.grab_focus()
        self.select_rows(self._gutter_anchor, row)
        return True

    def gutter_extend(self, y: float) -> bool:
        """The drag after gutter_press reached gutter *y*."""
        if self._gutter_anchor is None:
            return False
        row = self._gutter_row(y)
        if row is None:
            return False
        self.select_rows(self._gutter_anchor, row)
        return True

    def _on_gutter_drag_begin(self, gesture: Gtk.GestureDrag, _x: float, y: float) -> None:
        shift = bool(gesture.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK)
        if self.gutter_press(y, shift):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_gutter_drag_update(self, gesture: Gtk.GestureDrag, _dx: float, dy: float) -> None:
        _ok, _x, y = gesture.get_start_point()
        self.gutter_extend(y + dy)


class _ActionButton(Gtk.Button):
    """A header's button: a label that becomes a spinner while the page
    runs what it asked for (a Gtk.Stack, so the width holds)."""

    def __init__(self, on_click: Callable[[], None]) -> None:
        super().__init__()
        self.add_css_class("flat")
        self.add_css_class("git-action")
        self._stack = Gtk.Stack()
        self._label = Gtk.Label()
        self._stack.add_named(self._label, "label")
        self._stack.add_named(Gtk.Spinner(spinning=True), "spinner")
        self.set_child(self._stack)
        self.connect("clicked", lambda *_a: on_click())

    def set_text(self, text: str) -> None:
        self._label.set_text(text)

    def text(self) -> str:
        return self._label.get_text()

    def set_spinning(self, spinning: bool) -> None:
        self._stack.set_visible_child_name("spinner" if spinning else "label")


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


_HUNK_SERIALS = itertools.count(1)


class _NoteCard(Gtk.Box):
    """One note under its hunk (decision 5: a card, not an inline row):
    who wrote it and where it sits, the summary and the rationale — or,
    while it is being written, a text editor with *Save* / *Cancel*
    (`Ctrl+Enter` saves, `Esc` cancels). A draft (*note* None) is a card
    for a note not yet in the store; cancelling one removes it. Every
    word shows through `set_text`; the editor's text is bounded by
    diffnotes when it is saved."""

    def __init__(
        self,
        section: _HunkSection,
        owner: DiffView,
        note: diffnotes.Note | None,
        side: str,
        line: int,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("git-note")
        self.section = section
        self._owner = owner
        self.note = note
        self.side = side
        self.line = line
        self.editing = False
        self._commit_button: Gtk.Button | None = None

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.add_css_class("git-note-header")
        self._source = Gtk.Label(xalign=0.0)
        self._source.add_css_class("caption-heading")
        header.append(self._source)
        self._where = Gtk.Label(xalign=0.0, hexpand=True)
        self._where.add_css_class("caption")
        self._where.add_css_class("dim-label")
        header.append(self._where)
        self._edit = Gtk.Button(label=_("Edit"))
        self._edit.add_css_class("flat")
        self._edit.add_css_class("caption")
        self._edit.connect("clicked", lambda _b: self.start_edit())
        header.append(self._edit)
        self._delete = Gtk.Button(label=_("Delete"))
        self._delete.add_css_class("flat")
        self._delete.add_css_class("caption")
        self._delete.connect("clicked", lambda _b: self._owner.delete_note(self.note.id if self.note else ""))
        header.append(self._delete)
        self.append(header)

        self._stack = Gtk.Stack()
        self._stack.set_hhomogeneous(False)
        self._stack.set_vhomogeneous(False)
        shown = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._summary = Gtk.Label(xalign=0.0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self._summary.add_css_class("git-note-summary")
        shown.append(self._summary)
        self._rationale = Gtk.Label(xalign=0.0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self._rationale.add_css_class("dim-label")
        self._rationale.add_css_class("git-note-rationale")
        shown.append(self._rationale)
        self._stack.add_named(shown, "show")
        editor = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._text = Gtk.TextView()
        self._text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._text.set_accepts_tab(False)
        self._text.set_left_margin(6)
        self._text.set_right_margin(6)
        self._text.set_top_margin(4)
        self._text.set_bottom_margin(4)
        self._text.add_css_class("git-note-editor")
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_editor_key)
        self._text.add_controller(keys)
        scroller = Gtk.ScrolledWindow(child=self._text)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(_NOTE_EDITOR_MAX_HEIGHT)
        scroller.set_min_content_height(48)
        scroller.add_css_class("git-note-editor-frame")
        editor.append(scroller)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hint = Gtk.Label(xalign=0.0, hexpand=True)
        hint.set_text(_("Ctrl+Enter saves · Esc cancels"))
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        buttons.append(hint)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda _b: self.cancel())
        buttons.append(cancel)
        save = Gtk.Button(label=_("Save"))
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda _b: self.commit())
        buttons.append(save)
        editor.append(buttons)
        self._stack.add_named(editor, "edit")
        self.append(self._stack)
        self.set_note(note)

    # -- words --

    def set_note(self, note: diffnotes.Note | None) -> None:
        self.note = note
        if note is not None:
            self.side, self.line = note.side, note.line
        source = note.source if note is not None else diffnotes.USER
        for css in ("git-note-user", "git-note-agent"):
            self.remove_css_class(css)
        self.add_css_class(f"git-note-{source}")
        who = _("You") if source == diffnotes.USER else _("Agent")
        if note is not None and note.author:
            who = f"{who} · {note.author}"
        self._source.set_text(who)
        where = _("new line {n}") if self.side == diffmodel.NEW else _("old line {n}")
        self._where.set_text(where.format(n=self.line))
        self._summary.set_text(note.summary if note is not None else "")
        self._rationale.set_text(note.rationale or "" if note is not None else "")
        self._rationale.set_visible(bool(note is not None and note.rationale))
        self._edit.set_visible(note is not None and source == diffnotes.USER and not self.editing)
        self._delete.set_visible(note is not None and not self.editing)

    @property
    def draft(self) -> bool:
        return self.note is None

    # -- the editor --

    def start_edit(self) -> None:
        """Open the editor on the note's text (empty for a draft) and put
        the keyboard in it."""
        if self.editing:
            self._text.grab_focus()
            return
        self.editing = True
        text = diffnotes.join_note_text(self.note.summary, self.note.rationale) if self.note else ""
        buffer = self._text.get_buffer()
        buffer.set_text(text)
        buffer.place_cursor(buffer.get_end_iter())
        self._stack.set_visible_child_name("edit")
        self._edit.set_visible(False)
        self._delete.set_visible(False)
        # The keyboard first, then the view (which may drop another draft:
        # its editor must not be the focus widget when it goes).
        self._text.grab_focus()
        self._owner.on_note_editing(self, True)
        GLib.idle_add(self._refocus, priority=GLib.PRIORITY_DEFAULT)

    def _refocus(self) -> bool:
        if self.editing and self._text.get_root() is not None:
            self._text.grab_focus()
        return GLib.SOURCE_REMOVE

    def text(self) -> str:
        buffer = self._text.get_buffer()
        start, end = buffer.get_bounds()
        return buffer.get_text(start, end, False)

    def set_text(self, text: str) -> None:
        self._text.get_buffer().set_text(text)

    def commit(self) -> bool:
        """`Ctrl+Enter` / *Save*: hand the text to the view; an empty text
        keeps the editor open (a note needs a summary)."""
        if not self.editing:
            return False
        return self._owner.on_note_saved(self, self.text())

    def cancel(self) -> None:
        """`Esc` / *Cancel*: close the editor, keeping the note as it was;
        a draft goes away."""
        if not self.editing:
            return
        self._owner.on_note_cancelled(self)

    def finish(self) -> None:
        """Back to the card (the view calls it after a save or cancel)."""
        self.close_quietly()
        self._owner.on_note_editing(self, False)

    def close_quietly(self) -> None:
        """Back to the card without telling the view (it already knows)."""
        self.editing = False
        self._stack.set_visible_child_name("show")
        self.set_note(self.note)

    def _on_editor_key(self, _ctrl, keyval: int, _keycode: int, state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.cancel()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter) and (
            state & Gdk.ModifierType.CONTROL_MASK
        ):
            self.commit()
            return True
        return False


class _HunkSection(Gtk.Box):
    """One hunk: its `@@` header row — the ranges, the context, and the
    buttons (*Stage hunk* · *Discard hunk*, whose words follow the load
    and the selection: gitpatch.action_labels) — over its view(s). Wears
    `.git-hunk`, and `.git-hunk-focused` while one of its views has the
    keyboard — the lit rail that says "current hunk". A right-click on a
    view pops the same actions plus Copy, Open in editor, Add note and
    Expand context, through the `hunk.*` action group on the section.
    Under the views sit the hunk's note cards (`set_marks`: the notes and
    highlights the store placed in this hunk, drawn as cards and as the
    marker column's glyphs, the highlights as tags on their rows)."""

    def __init__(
        self,
        file: diffmodel.File,
        hunk: diffmodel.Hunk,
        language: GtkSource.Language | None,
        scheme: GtkSource.StyleScheme | None,
        palette: diffmodel.DiffPalette,
        options: _Options,
        on_focus: Callable[[_HunkSection, bool], None],
        owner: DiffView,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("git-hunk")
        self.file = file
        self.hunk = hunk
        # A number no other section ever gets (an id() could be reused
        # once a dropped widget is freed): the probe that says "this hunk
        # kept its widget across a reload" compares these.
        self.serial = next(_HUNK_SERIALS)
        self._language = language
        self._scheme = scheme
        self._palette = palette
        self._options = options
        self._on_focus = on_focus
        self._owner: DiffView = owner
        self.views: list[_HunkView] = []
        self._body: Gtk.Widget | None = None
        self._pane: _SplitPane | None = None
        self._focused = False
        self._menu_view: _HunkView | None = None
        self._menu_popover: Gtk.PopoverMenu | None = None
        # The marks placed in this hunk — (mark, line index into
        # hunk.lines) — and the cards by note id; drafts are cards with no
        # note yet, kept at the end of the notes box.
        self._placed_notes: list[tuple[diffnotes.Note, int]] = []
        self._placed_highlights: list[tuple[diffnotes.Highlight, int]] = []
        self._cards: dict[str, _NoteCard] = {}
        self._drafts: list[_NoteCard] = []
        self._agent_notes_shown = True

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
        # The buttons: the stage / unstage / revert one, and discard on
        # the unstaged load (sync_actions words them).
        self.actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.actions.add_css_class("git-hunk-actions")
        self.primary_button = _ActionButton(lambda: self._owner_request(False))
        self.discard_button = _ActionButton(lambda: self._owner_request(True))
        self.actions.append(self.primary_button)
        self.actions.append(self.discard_button)
        header.append(self.actions)
        self.header = header
        self.append(header)
        self._notes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._notes_box.add_css_class("git-hunk-notes")
        self._notes_box.set_visible(False)
        self.append(self._notes_box)
        self._install_actions()
        self._build_body()
        self.sync_actions()

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
            on_selection=self._on_view_selection,
        )
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", self._on_focus_enter)
        focus.connect("leave", self._on_focus_leave)
        view.view.add_controller(focus)
        # The context menu: claimed in the capture phase, ahead of the
        # text view's own (Cut / Paste / Select all — none of them the
        # point here).
        secondary = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        secondary.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        secondary.connect("pressed", lambda g, n, x, y, v=view: self._on_secondary_click(g, v, x, y))
        view.view.add_controller(secondary)
        return view

    def _install_actions(self) -> None:
        """The `hunk.*` actions the context menu fires (found from the
        popover's parent, a view, up the tree)."""
        group = Gio.SimpleActionGroup()
        for name, handler in (
            ("primary", lambda: self._owner_request(False)),
            ("discard", lambda: self._owner_request(True)),
            ("copy", self._copy),
            ("open", lambda: self._owner.open_from(self)),
            ("note", lambda: self._owner.request_note(self)),
            ("expand", lambda: self._owner.expand_gap_before(self)),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, run=handler: run())
            group.add_action(action)
        self.insert_action_group(_HUNK_ACTIONS, group)

    def _on_secondary_click(self, gesture: Gtk.GestureClick, view: _HunkView, x: float, y: float) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._menu_view = view
        selected = self.selection() is not None
        if not selected:
            # The menu's *Add note* and *Open in editor* speak of the
            # cursor line: put it under the pointer (a selection stays —
            # placing the cursor would clear it).
            _bx, by = view.view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
            it, _top = view.view.get_line_at_y(by)  # (target_iter, line_top): no boolean first
            view.place_cursor(min(it.get_line(), max(0, len(view.rows) - 1)))
        popover = Gtk.PopoverMenu.new_from_model(self.context_menu(selected))
        popover.set_parent(view.view)
        popover.set_has_arrow(False)
        popover.set_halign(Gtk.Align.START)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        # Unparent from the main loop, not the idle: under a busy frame
        # clock (CI's Xvfb) a default-idle callback never runs.
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent, priority=GLib.PRIORITY_DEFAULT))
        self._menu_popover = popover
        popover.popup()

    def context_menu(self, selected: bool) -> Gio.Menu:
        """The right-click menu's model: the hunk's two actions worded for
        the load and the selection, then Copy / Open in editor / Add note /
        Expand context, over the `hunk.*` group."""
        primary, discard = gitpatch.action_labels(self._owner.loaded, gitpatch.HUNK, selected)
        menu = Gio.Menu()
        acts = Gio.Menu()
        acts.append(primary, f"{_HUNK_ACTIONS}.primary")
        if discard is not None:
            acts.append(discard, f"{_HUNK_ACTIONS}.discard")
        menu.append_section(None, acts)
        more = Gio.Menu()
        more.append(_("Copy"), f"{_HUNK_ACTIONS}.copy")
        more.append(_("Open in editor"), f"{_HUNK_ACTIONS}.open")
        more.append(_("Add note"), f"{_HUNK_ACTIONS}.note")
        more.append(_("Expand context"), f"{_HUNK_ACTIONS}.expand")
        menu.append_section(None, more)
        return menu

    def open_context_menu(self, view: _HunkView, row: int) -> Gtk.PopoverMenu | None:
        """Probe: a right-click on *row* of *view*, through the gesture's
        handler with the pointer over the row (the WIDGET window's
        coordinates); the popover it left up, for its model and popdown."""
        if view not in self.views or not view.rows or not 0 <= row < len(view.rows):
            return None
        _ok, it = view.buffer.get_iter_at_line(row)
        rect = view.view.get_iter_location(it)
        if rect.height <= 0:
            return None
        middle = rect.y + rect.height // 2
        x, y = view.view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, rect.x + 4, middle)
        self._on_secondary_click(Gtk.GestureClick(), view, float(x), float(y))
        return self._menu_popover

    def _copy(self) -> None:
        view = self._menu_view if self._menu_view in self.views else self.focused_view
        if view is None and self.views:
            view = self.views[0]
        if view is None:
            return
        display = self.get_display()
        if display is not None:
            display.get_clipboard().set(view.selection_text())

    # -- the buttons --

    def _owner_request(self, discard: bool) -> None:
        self._owner.request_hunk(self, discard)

    def sync_actions(self) -> None:
        """Word the buttons for the load and the selection (the spec's
        table), with the keys' hints; the discard button shows on the
        unstaged load alone."""
        selected = self.selection() is not None
        primary, discard = gitpatch.action_labels(self._owner.loaded, gitpatch.HUNK, selected)
        self.primary_button.set_text(primary)
        self.primary_button.set_tooltip_text(keybindings.with_hint(primary, "git.stage"))
        self.discard_button.set_visible(discard is not None)
        if discard is not None:
            self.discard_button.set_text(discard)
            self.discard_button.set_tooltip_text(keybindings.with_hint(discard, "git.discard"))
        self.actions.set_visible(self._owner.loaded is not None)

    def button(self, discard: bool) -> _ActionButton:
        return self.discard_button if discard else self.primary_button

    def set_busy(self, busy: bool, acting: _ActionButton | None) -> None:
        for button in (self.primary_button, self.discard_button):
            button.set_sensitive(not busy)
            button.set_spinning(busy and button is acting)

    # -- the selection --

    def _on_view_selection(self, view: _HunkView) -> None:
        if view not in self.views:
            return  # a view still being built
        self._owner.on_hunk_selection(self, view)

    def selection(self) -> tuple[int, int] | None:
        """The selected patch lines as inclusive indexes into
        `hunk.lines` — the lowest and highest a selected row shows (a
        span in patch order, as gitpatch.LineRange reads it) — or None
        when no row is selected, or only padding is."""
        for view in self.views:
            rows = view.selected_rows()
            if rows is None:
                continue
            lines = [row.line for row in view.rows[rows[0] : rows[1] + 1] if row.line is not None]
            if lines:
                return min(lines), max(lines)
        return None

    def clear_selection(self) -> None:
        for view in self.views:
            view.clear_selection()

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
            line_index = {id(line): i for i, line in enumerate(hunk.lines)}
            old_rows = [
                _Row(r.old.kind, r.old.text, r.old.old, None, line_index.get(id(r.old)))
                if r.old
                else _Row(PAD, "", None, None)
                for r in rows
            ]
            new_rows = [
                _Row(r.new.kind, r.new.text, None, r.new.new, line_index.get(id(r.new)))
                if r.new
                else _Row(PAD, "", None, None)
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
            rows = [
                _Row(line.kind, line.text, line.old, line.new, index) for index, line in enumerate(hunk.lines)
            ]
            by_line = {entry.line_index: entry.spans for entry in emphasis}
            view = self._make_view((diffmodel.OLD, diffmodel.NEW))
            view.set_rows(rows, by_line)
            self.views = [view]
            self._body = view.scroller
        # Between the header and the note cards (a rebuilt body must not
        # land under the notes).
        self.insert_child_after(self._body, self.header)
        self._apply_view_marks()

    # -- notes and highlights --

    def set_marks(
        self,
        notes: Sequence[tuple[diffnotes.Note, int]],
        highlights: Sequence[tuple[diffnotes.Highlight, int]],
    ) -> None:
        """Show *notes* and *highlights* — each with the index into
        hunk.lines the store placed it at — as this hunk's: the cards
        (kept by note id, so one being edited keeps its editor), the
        marker glyphs and the highlight tags on the views."""
        self._placed_notes = list(notes)
        self._placed_highlights = list(highlights)
        wanted = {note.id: note for note, _index in notes}
        for note_id in list(self._cards):
            if note_id not in wanted:
                self._remove_card(self._cards.pop(note_id))
        for note, _index in notes:
            card = self._cards.get(note.id)
            if card is None:
                card = _NoteCard(self, self._owner, note, note.side, note.line)
                self._cards[note.id] = card
            else:
                card.set_note(note)
        # Re-order: the store's order, drafts last.
        for child in list(self._cards.values()) + list(self._drafts):
            if child.get_parent() is self._notes_box:
                self._notes_box.remove(child)
        for note, _index in notes:
            self._notes_box.append(self._cards[note.id])
        for draft in self._drafts:
            self._notes_box.append(draft)
        self._sync_cards_shown()
        self._apply_view_marks()

    def set_agent_notes_shown(self, shown: bool) -> None:
        """`a`: the agent's cards fold away (their markers stay)."""
        self._agent_notes_shown = shown
        self._sync_cards_shown()

    def _sync_cards_shown(self) -> None:
        any_shown = False
        for card in self._cards.values():
            agent = card.note is not None and card.note.source == diffnotes.AGENT
            visible = self._agent_notes_shown or not agent
            card.set_visible(visible)
            any_shown = any_shown or visible
        self._notes_box.set_visible(any_shown or bool(self._drafts))

    def _apply_view_marks(self) -> None:
        """The marker column's glyphs and the highlight tags per view: a
        row carrying a note shows the note's glyph (over a highlight's),
        the side's view in split, the one view in stack."""
        for view in self.views:
            side = self.side_of(view)
            both = len(self.views) == 1
            icons: dict[int, str] = {}
            spans: list[tuple[int, int, int, str]] = []
            for mark, index in self._placed_highlights:
                if both or mark.side == side:
                    row = self._row_for(view, index)
                    icons[row] = _HIGHLIGHT_ICON
                    spans.append((row, mark.start, mark.end, mark.tone))
            for note, index in self._placed_notes:
                if both or note.side == side:
                    icons[self._row_for(view, index)] = _NOTE_ICON
            view.set_marks(icons)
            view.set_highlights(spans)

    def anchor_at_cursor(self, view: _HunkView | None = None) -> tuple[str, int]:
        """(side, 1-based line) a note asked for here would sit at: the
        cursor row of *view* (the focused view, else the last), its new
        number when it has one, else its old; a padding row takes the
        next numbered row; failing all, the hunk's first numbered line."""
        view = view if view in self.views else (self.focused_view or (self.views[-1] if self.views else None))
        if view is not None and view.rows:
            start = min(view.cursor_row(), len(view.rows) - 1)
            for row in view.rows[start:]:
                if row.new is not None:
                    return diffmodel.NEW, row.new
                if row.old is not None:
                    return diffmodel.OLD, row.old
        for line in self.hunk.lines:
            if line.new is not None:
                return diffmodel.NEW, line.new
            if line.old is not None:
                return diffmodel.OLD, line.old
        return diffmodel.NEW, 0

    def add_draft(self, side: str, line: int) -> _NoteCard:
        """A card for a note not yet written, at the end of the notes."""
        card = _NoteCard(self, self._owner, None, side, line)
        self._drafts.append(card)
        self._notes_box.append(card)
        self._notes_box.set_visible(True)
        return card

    def drop_draft(self, card: _NoteCard) -> None:
        if card in self._drafts:
            self._drafts.remove(card)
            self._remove_card(card)
            self._sync_cards_shown()

    def _remove_card(self, card: _NoteCard) -> None:
        """Take *card* out of the tree: the keyboard moves off it first
        (the hunk's view takes it), it hides now, and it is unparented
        _CARD_REAP_MS later — never in the turn its editor's focus left
        (see the constant)."""
        root = self.get_root()
        focus = root.get_focus() if root is not None else None
        if focus is not None and (focus is card or focus.is_ancestor(card)):
            if not self.grab():
                root.set_focus(None)
        card.set_visible(False)

        def unparent() -> bool:
            if card.get_parent() is self._notes_box:
                self._notes_box.remove(card)
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(_CARD_REAP_MS, unparent)

    def cards(self) -> list[_NoteCard]:
        """Every card in the box's order, drafts included."""
        return [c for c in self._cards.values()] + list(self._drafts)

    def first_user_card(self) -> _NoteCard | None:
        for card in self._cards.values():
            if card.note is not None and card.note.source == diffnotes.USER:
                return card
        return None

    @property
    def placed_notes(self) -> list[tuple[diffnotes.Note, int]]:
        return list(self._placed_notes)

    @property
    def placed_highlights(self) -> list[tuple[diffnotes.Highlight, int]]:
        return list(self._placed_highlights)

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

    @property
    def marked(self) -> bool:
        """Whether a note or highlight marker sits on one of the hunk's
        lines (what `}` / `{` walk)."""
        return any(view.marks for view in self.views)

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

    @property
    def focused_view(self) -> _HunkView | None:
        """The view holding the keyboard (one side's, in split), else None."""
        for view in self.views:
            root = view.view.get_root()
            if view.view.has_focus() or (root is not None and root.get_focus() is view.view):
                return view
        return None

    def side_of(self, view: _HunkView) -> str:
        """Which side *view* draws: OLD for the split's left view, NEW for
        the right one and for the stack's single view."""
        return diffmodel.OLD if len(self.views) == 2 and view is self.views[0] else diffmodel.NEW

    def cursor_line(self) -> tuple[str, int] | None:
        """(side, 1-based line number) under the cursor of the focused
        view, for `open-requested`: the new side's number when the line has
        one, else the old side's."""
        view = self.focused_view
        if view is None or not view.rows:
            return None
        row = view.rows[min(view.cursor_row(), len(view.rows) - 1)]
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
        # Stage file / Discard file (Unstage file; Revert file), worded by
        # sync_actions for the load.
        self.actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.actions.add_css_class("git-file-actions")
        self.primary_button = _ActionButton(lambda: owner.request_file(self, False))
        self.discard_button = _ActionButton(lambda: owner.request_file(self, True))
        self.actions.append(self.primary_button)
        self.actions.append(self.discard_button)
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
        if file.untracked or file.kind == diffmodel.KIND_NEW:
            # An untracked file reads `new` too (the files list's `?` row
            # already says it is not in the index).
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
        # A kept section still holds the Hunk of the load it was built for:
        # the key leaves the index out on purpose (a hunk above going away
        # must not rebuild the ones below), so re-point every section at
        # this read's Hunk — the items list yields one section per
        # file.hunks entry, in order — or `]`, `z`, reveal and the sidebar
        # would keep speaking the old indexes.
        for section, hunk in zip(self.hunks, file.hunks, strict=True):
            section.hunk = hunk
            section.file = file
        for gap in self.gaps:  # a kept gap likewise: its file is this read's
            gap.file = file
        self.sync_actions()

    def sync_actions(self) -> None:
        """Word the file buttons for the load (gitpatch.action_labels), and
        every hunk's after them."""
        primary, discard = gitpatch.action_labels(self._owner.loaded, gitpatch.FILE)
        self.primary_button.set_text(primary)
        self.primary_button.set_tooltip_text(keybindings.with_hint(primary, "git.stage-file"))
        self.discard_button.set_visible(discard is not None)
        if discard is not None:
            self.discard_button.set_text(discard)
            self.discard_button.set_tooltip_text(discard)
        self.actions.set_visible(self._owner.loaded is not None)
        for section in self.hunks:
            section.sync_actions()

    def button(self, discard: bool) -> _ActionButton:
        return self.discard_button if discard else self.primary_button

    def set_busy(self, busy: bool, acting: _ActionButton | None) -> None:
        for button in (self.primary_button, self.discard_button):
            button.set_sensitive(not busy)
            button.set_spinning(busy and button is acting)
        for section in self.hunks:
            section.set_busy(busy, acting)

    def _make_hunk(self, hunk: diffmodel.Hunk, language: GtkSource.Language | None) -> _HunkSection:
        owner = self._owner
        return _HunkSection(
            self.file, hunk, language, owner.scheme, owner.palette, owner.options, owner.on_hunk_focus, owner
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
        # A button or key asked for a mutation: a gitpatch.MutationRequest
        # (the file, the load, the grain, the hunk and lines). The page
        # reads the file's patch, plans, confirms, runs and reloads.
        "mutation-requested": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        # The notes or highlights changed (added, edited, deleted, cleared,
        # or pruned by a reload): what the page and the tools re-read
        # `notes()` / `highlights()` on.
        "notes-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # A note editor opened (True) or closed (False): the page must
        # disable the page-local letter chords meanwhile, or `e` types
        # nothing and `c` opens another card.
        "editing-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
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
        # The one line selection (decision 4: contiguous, inside one hunk):
        # the section holding it — its view's buffer holds the range;
        # selecting in another hunk clears it. The button a request came
        # from spins while the page runs it (set_busy).
        self._selection: _HunkSection | None = None
        self._acting: _ActionButton | None = None
        self._busy = False
        self._pinned_section: _FileSection | None = None
        # The notes and highlights (decision 8: the page's for the tab's
        # life, keyed by hunk to survive a reload), the one note editor
        # open, and whether the agent's cards are shown (`a`).
        self._store = diffnotes.MarkStore()
        self._editing: _NoteCard | None = None
        self._agent_notes = True
        # The find bar's half: the query, one GtkSource.SearchContext per
        # hunk buffer (the highlight of every occurrence; weakly keyed so a
        # rebuilt hunk's context goes with its view), and the matches in
        # document order — (hunk section, view, row, start, end), walked by
        # search_step.
        self._search_text = ""
        self._search_settings = GtkSource.SearchSettings()
        self._search_settings.set_case_sensitive(False)
        self._search_settings.set_wrap_around(False)
        self._search_contexts: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
        self._matches: list[tuple[_HunkSection, _HunkView, int, int, int]] = []
        self._match_index = -1
        # The page-scoped chords (keybindings.GROUP_GIT), see apply_keybindings.
        self._shortcuts: Gtk.ShortcutController | None = None

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
        # The pinned header carries the file's buttons too (spec): they
        # act on the section pinned at the time of the click.
        pinned_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        pinned_actions.add_css_class("git-file-actions")
        self._pinned_primary = _ActionButton(lambda: self._request_pinned(False))
        self._pinned_discard = _ActionButton(lambda: self._request_pinned(True))
        pinned_actions.append(self._pinned_primary)
        pinned_actions.append(self._pinned_discard)
        pinned.append(pinned_actions)
        pinned.set_visible(False)
        # A wheel over the header scrolls the stream under it (the header
        # is the overlay's child, not the scroller's, so the event would
        # otherwise stop here).
        wheel = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.connect("scroll", self._on_pinned_scroll)
        pinned.add_controller(wheel)
        self._pinned = pinned
        overlay.add_overlay(pinned)
        self._stack.add_named(overlay, "files")
        bin_.set_child(self._stack)
        self.append(bin_)
        scroller.get_vadjustment().connect("value-changed", self._on_scrolled)
        scroller.get_vadjustment().connect("changed", self._on_scrolled)
        # Escape clears the selection — and only then: with none, the key
        # goes on to whoever wants it (the dock's restore-from-maximized).
        escape = Gtk.EventControllerKey()
        escape.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        escape.connect("key-pressed", self._on_key_pressed)
        self.add_controller(escape)
        self.connect("destroy", self._on_destroy)

    # -- public face -------------------------------------------------------------

    def apply_keybindings(self, custom) -> None:
        """Bind the `git.*` chords of keybindings.BINDINGS (with *custom*,
        the "keybindings" setting, applied) on the view: a controller
        scoped to the view — it fires only while the keyboard is inside it
        — in the capture phase, so a bare letter (`]`, `z`, `e`) beats the
        GtkSource.View under it, which would otherwise swallow the press as
        text it can't insert. The actions themselves live in the page's
        `git` action group (an ancestor's groups are found from here)."""
        if self._shortcuts is not None:
            self.remove_controller(self._shortcuts)
        self._shortcuts = keymap.shortcut_controller(custom, "git", Gtk.PropagationPhase.CAPTURE)
        self._shortcuts.set_scope(Gtk.ShortcutScope.LOCAL)
        self.add_controller(self._shortcuts)

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
        # A kept hunk keeps its widget, its buffer and so its selection; a
        # rebuilt one lost it (and a section built now words its buttons
        # for this load in its constructor).
        if self._selection is not None and self._selection.get_parent() is None:
            self._selection = None
        # The marks: a note on a hunk this read still carries stays (the
        # kept widget keeps its cards too), one on a hunk that changed or
        # went is dropped; a draft being written in a dropped hunk went
        # with it.
        pruned = self._store.prune(self._files)
        if self._editing is not None and self._editing.get_root() is None:
            editing = self._editing
            self._editing = None
            editing.editing = False
            self.emit("editing-changed", False)
        self._apply_marks()
        if pruned:
            self.emit("notes-changed")
        self._rescan_search()
        self._schedule_scroll_sync()

    def reveal(
        self, path: str, hunk: int | None = None, side: str | None = None, line: int | None = None
    ) -> bool:
        """Scroll to *path*'s section — or its hunk *hunk* (0-based), or the
        hunk holding 1-based *line* on *side* (new by default) — and focus
        that hunk's view with the cursor on the line. A line no hunk
        carries (an unchanged stretch) lands on the nearest hunk: the file
        is in the diff, which is what the caller asked about (`holds_line`
        says whether the line itself was). False only when the file isn't
        in the load. Synchronous."""
        side = side if side in diffmodel.SIDES else diffmodel.NEW
        section = self._section_for(path, side)
        if section is None:
            return False
        line_index: int | None = None
        if hunk is None and line is not None:
            located = diffmodel.locate(self._files, section.file.path, side, line)
            if located is not None:
                _file, hunk, line_index = located
            else:
                hunk = diffmodel.nearest_hunk(section.file, side, line)
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

    def holds_line(self, path: str, side: str | None, line: int) -> bool:
        """Whether a hunk of the load carries 1-based *line* of *path* on
        *side* (new by default) — what reveal landed on exactly, against
        the nearest hunk it settles for otherwise."""
        side = side if side in diffmodel.SIDES else diffmodel.NEW
        return diffmodel.locate(self._files, path, side, line) is not None

    def current(self) -> tuple[str | None, int | None, tuple[str, int, int, int] | None]:
        """(path, hunk index, selection) — the file the reader is in (the
        focused hunk's, else the one at the top of the viewport), its
        current hunk (None when none), and the line selection as (path,
        hunk index, first, last) — inclusive indexes into that hunk's
        lines — or None."""
        path, hunk = self._current
        return (path or None, hunk if hunk >= 0 else None, self.selection())

    def selection(self) -> tuple[str, int, int, int] | None:
        """The selected lines: (path, hunk index, first, last), or None."""
        section = self._selection
        if section is None or section.get_parent() is None:
            return None
        lines = section.selection()
        if lines is None:
            return None
        return section.file.path, section.hunk.index, lines[0], lines[1]

    def has_selection(self) -> bool:
        return self.selection() is not None

    def clear_selection(self) -> bool:
        """`Esc`: drop the line selection. False when there was none."""
        section = self._selection
        self._selection = None
        if section is None or section.get_parent() is None:
            return False
        had = section.selection() is not None
        section.clear_selection()
        section.sync_actions()
        return had

    # -- the staging keys --

    def request_stage(self) -> bool:
        """`x`: stage / unstage / revert the selected lines, or the focused
        hunk (the current one when none has the keyboard)."""
        section = self._target_hunk()
        if section is None:
            return False
        self.request_hunk(section, False)
        return True

    def request_stage_file(self) -> bool:
        """`X`: the focused (or current) hunk's file, whole."""
        section = self._target_file()
        if section is None:
            return False
        self.request_file(section, False)
        return True

    def request_discard(self) -> bool:
        """`D`: discard (revert, on a read-only load) the selected lines or
        the focused hunk, after the page's confirmation."""
        section = self._target_hunk()
        if section is None:
            return False
        self.request_hunk(section, True)
        return True

    def _target_hunk(self) -> _HunkSection | None:
        focused = self._focused_hunk
        if focused is not None and focused.get_parent() is not None:
            return focused
        selected = self._selection
        if selected is not None and selected.get_parent() is not None:
            return selected
        path, index = self._current
        section = self._section_for(path, diffmodel.NEW)
        if section is None:
            return None
        return next((h for h in section.hunks if h.hunk.index == index), None)

    def _target_file(self) -> _FileSection | None:
        hunk = self._target_hunk()
        if hunk is not None:
            return self._section_for(hunk.file.path, diffmodel.NEW)
        return self._section_for(self._current[0], diffmodel.NEW)

    def request_hunk(self, section: _HunkSection, discard: bool) -> None:
        """A hunk header's button (or its menu, or `x` / `D`): the selected
        lines when the selection is in this hunk, else the whole hunk."""
        if self._busy or self.loaded is None:
            return
        lines = section.selection() if self._selection is section else None
        if lines is not None:
            request = gitpatch.MutationRequest(
                section.file, self.loaded, gitpatch.LINES, discard, section.hunk.index, lines[0], lines[1]
            )
        else:
            request = gitpatch.MutationRequest(
                section.file, self.loaded, gitpatch.HUNK, discard, section.hunk.index
            )
        self._acting = section.button(discard)
        self.emit("mutation-requested", request)

    def request_file(self, section: _FileSection, discard: bool, acting: _ActionButton | None = None) -> None:
        """A file header's button (or `X`, or the pinned header's copy —
        *acting* names the button that spins): the file, whole."""
        if self._busy or self.loaded is None:
            return
        self._acting = acting or section.button(discard)
        request = gitpatch.MutationRequest(section.file, self.loaded, gitpatch.FILE, discard)
        self.emit("mutation-requested", request)

    def _request_pinned(self, discard: bool) -> None:
        section = self._pinned_section
        if section is not None and section.get_parent() is not None:
            self.request_file(section, discard, self._pinned_discard if discard else self._pinned_primary)

    def set_busy(self, busy: bool) -> None:
        """The page is running (or planning) a request: every header
        button goes insensitive, the one the request came from spins."""
        self._busy = busy
        acting = self._acting if busy else None
        for section in self._sections():
            section.set_busy(busy, acting)
        for button in (self._pinned_primary, self._pinned_discard):
            button.set_sensitive(not busy)
            button.set_spinning(busy and button is acting)
        if not busy:
            self._acting = None

    def open_from(self, section: _HunkSection) -> None:
        """The context menu's *Open in editor*: the hunk's file at the
        cursor line (the first line of the hunk when none)."""
        where = section.cursor_line()
        line = where[1] if where else (section.hunk.lines[0].new or section.hunk.lines[0].old or 0)
        self.emit("open-requested", section.file.path, line or 0)

    def request_note(self, section: _HunkSection) -> None:
        """The context menu's *Add note*: a draft card under the hunk,
        anchored to the line the menu opened on."""
        view = section._menu_view if section._menu_view in section.views else None
        self._open_draft(section, section.anchor_at_cursor(view))

    # -- notes and highlights --

    def notes(self, path: str | None = None) -> list[diffnotes.Note]:
        """Every note held (of *path*), in the order they were made — the
        ones parked off the current load included (diffnotes.MarkStore)."""
        return self._store.notes(path)

    def highlights(self, path: str | None = None) -> list[diffnotes.Highlight]:
        return self._store.highlights(path)

    def add_notes(
        self, specs: Sequence[diffnotes.NoteSpec], focus: bool = False, source: str = diffnotes.AGENT
    ) -> list[str] | str:
        """Land *specs* as *source*'s notes (the annotate tool's door):
        the whole batch or none, validated against the loaded diff first
        — a refusal is its reason, a success the ids. With *focus* the
        view reveals the first one's hunk (without taking the keyboard
        from where it was, unless it is already in the view)."""
        added = self._store.add_notes(self._files, specs, source)
        if isinstance(added, str):
            return added
        self._apply_marks()
        self.emit("notes-changed")
        if focus and added:
            self._reveal_mark(added[0])
        return [note.id for note in added]

    def add_highlights(self, specs: Sequence[diffnotes.HighlightSpec], focus: bool = False) -> int | str:
        """Land *specs* as highlights (the highlight tool's door), the
        batch or none; the count, or the reason."""
        added = self._store.add_highlights(self._files, specs)
        if isinstance(added, str):
            return added
        self._apply_marks()
        self.emit("notes-changed")
        if focus and added:
            self._reveal_mark(added[0])
        return len(added)

    def clear_marks(
        self,
        path: str | None = None,
        notes: bool = False,
        highlights: bool = False,
        include_user: bool = False,
    ) -> int:
        """Drop the agent's notes (the user's too with *include_user*)
        and / or the highlights, of *path* or of every file."""
        gone = self._store.clear(path, notes, highlights, include_user)
        if gone:
            self._apply_marks()
            self.emit("notes-changed")
        return gone

    def add_note_at_cursor(self) -> bool:
        """`c`: a draft card under the focused (or current) hunk, anchored
        to the cursor line, its editor open. With an editor already open,
        the keyboard goes back to it. False with no hunk."""
        if self._editing is not None and self._editing.get_root() is not None:
            self._editing.start_edit()
            return True
        section = self._target_hunk()
        if section is None:
            return False
        return self._open_draft(section, section.anchor_at_cursor())

    def edit_first_note(self) -> bool:
        """`E`: the focused (or current) hunk's first user note opens in
        its editor. False with none."""
        section = self._target_hunk()
        card = section.first_user_card() if section is not None else None
        if card is None:
            return False
        card.start_edit()
        return True

    def delete_note(self, note_id: object) -> bool:
        """A card's *Delete*: the note goes (agent or user; a note with
        replies would stay, but replies are a later PR's)."""
        card = self._card_for(note_id)
        if not self._store.remove(note_id):
            return False
        if card is not None and card is self._editing:
            self._editing = None
            card.editing = False
            self.emit("editing-changed", False)
        self._apply_marks()
        self.emit("notes-changed")
        if card is not None:
            card.section.grab()
        return True

    def set_agent_notes_shown(self, shown: bool) -> None:
        """`a`: show or fold the agent's cards (the markers stay, `}` still
        finds the hunk)."""
        self._agent_notes = bool(shown)
        for section in self._sections():
            for hunk in section.hunks:
                hunk.set_agent_notes_shown(self._agent_notes)

    def editing(self) -> bool:
        """Whether a note editor is open (the page's letter chords are off
        meanwhile, and Escape is the editor's)."""
        return self._editing is not None and self._editing.get_root() is not None

    def _open_draft(self, section: _HunkSection, where: tuple[str, int]) -> bool:
        side, line = where
        anchor = diffnotes.resolve_anchor(self._files, section.file.path, side, line)
        if isinstance(anchor, str):
            log.info("note refused: %s", anchor)
            return False
        card = section.add_draft(side, line)
        card.start_edit()
        return True

    def _card_for(self, note_id: object) -> _NoteCard | None:
        for section in self._sections():
            for hunk in section.hunks:
                for card in hunk.cards():
                    if card.note is not None and card.note.id == note_id:
                        return card
        return None

    def _apply_marks(self) -> None:
        """Hand every hunk section the marks the store places in it."""
        notes = self._store.placed_notes(self._files)
        highlights = self._store.placed_highlights(self._files)
        for section in self._sections():
            for hunk in section.hunks:
                key = (section.file.path, hunk.hunk.index)
                hunk.set_marks(notes.get(key, []), highlights.get(key, []))
                hunk.set_agent_notes_shown(self._agent_notes)

    def _reveal_mark(self, mark: diffnotes.Note | diffnotes.Highlight) -> None:
        section = self._section_for(mark.path, mark.side)
        if section is None:
            return
        located = diffmodel.locate(self._files, section.file.path, mark.side, mark.line)
        if located is None:
            return
        _file, index, _line = located
        target = next((h for h in section.hunks if h.hunk.index == index), None)
        if target is None:
            return
        section.set_folded(False)
        keyedslots.scroll_to(self._scroller, target)
        self._set_current(section.file.path, target.hunk.index)

    # -- what the cards call back --

    def on_note_editing(self, card: _NoteCard, editing: bool) -> None:
        if editing:
            was_editing = self.editing()
            previous = self._editing
            self._editing = card
            if previous is not None and previous is not card and previous.editing:
                # One editor at a time: the other closes unsaved, a draft
                # going with it.
                previous.close_quietly()
                if previous.draft:
                    previous.section.drop_draft(previous)
            if not was_editing:
                self.emit("editing-changed", True)
            return
        if self._editing is card:
            self._editing = None
            self.emit("editing-changed", False)

    def on_note_saved(self, card: _NoteCard, text: str) -> bool:
        """`Ctrl+Enter` / *Save* on *card*: the text's first line is the
        summary, the rest the rationale; an empty text keeps the editor
        open. A draft becomes a user note; an existing note is re-worded."""
        summary, rationale = diffnotes.split_note_text(text)
        if not summary:
            return False
        section = card.section
        if card.note is None:
            spec = diffnotes.NoteSpec(section.file.path, summary, rationale, side=card.side, line=card.line)
            added = self._store.add_notes(self._files, [spec], diffnotes.USER)
            if isinstance(added, str):
                log.info("note not saved: %s", added)
                self.on_note_cancelled(card)
                return False
            card.editing = False
            section.drop_draft(card)
            if self._editing is card:
                self._editing = None
                self.emit("editing-changed", False)
        else:
            self._store.edit(card.note.id, summary, rationale)
            card.finish()
        self._apply_marks()
        self.emit("notes-changed")
        section.grab()
        return True

    def on_note_cancelled(self, card: _NoteCard) -> None:
        """`Esc` / *Cancel*: a draft goes away, an edit is dropped."""
        section = card.section
        if card.draft:
            card.editing = False
            section.drop_draft(card)
            if self._editing is card:
                self._editing = None
                self.emit("editing-changed", False)
        else:
            card.finish()
        section.grab()

    def expand_gap_before(self, section: _HunkSection) -> bool:
        """Draw every unchanged line above *section* (the context menu's
        *Expand context*; `z` is expand_gap_before_focus)."""
        file_section = self._section_for(section.file.path, diffmodel.NEW)
        if file_section is None:
            return False
        key = f"{diffmodel.BEFORE}:{section.hunk.index}"
        gap = next((g for g in file_section.gaps if g.key == key and g.remaining > 0), None)
        if gap is None:
            return False
        self.on_gap_expand(gap, ALL, 0)
        return True

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
        self._rescan_search()
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

    def focus_annotated(self, delta: int) -> bool:
        """`}` / `{`: the keyboard moves to the next (previous) hunk wearing
        a note or highlight marker after (before) the focused one — the
        first (last) such hunk when nothing is focused. False with none."""
        hunks = [h for s in self._sections() if s.get_visible() and not s.folded for h in s.hunks]
        annotated = [h for h in hunks if h.marked]
        if not annotated:
            return False
        index = self._focused_index(hunks)
        if index is None:
            return self._focus(annotated[0] if delta >= 0 else annotated[-1])
        if delta >= 0:
            after = [h for h in hunks[index + 1 :] if h.marked]
            return bool(after) and self._focus(after[0])
        before = [h for h in hunks[:index] if h.marked]
        return bool(before) and self._focus(before[-1])

    def step_cursor(self, delta: int) -> bool:
        """`j` / `k`: the cursor a row down (up) in the focused hunk's view,
        the column scrolling to keep the line on screen; past the hunk's
        last (first) row the keyboard moves into the next (previous)
        hunk's first (last) row on the same side, as a reader walking the
        stream expects. With nothing focused, the first (last) hunk's
        edge. False at the end of the stream."""
        focused = self._focused_hunk
        if focused is None or focused.get_parent() is None:
            if not self.focus_hunk(1 if delta >= 0 else -1):
                return False
            focused = self._focused_hunk
            view = focused.focused_view if focused is not None else None
            if view is None:
                return False
            view.place_cursor(0 if delta >= 0 else len(view.rows) - 1)
            self._show_cursor(view)
            return True
        view = focused.focused_view
        if view is None:
            return focused.grab()
        target = view.cursor_row() + delta
        if 0 <= target < len(view.rows):
            view.place_cursor(target)
            self._show_cursor(view)
            return True
        hunks = [h for s in self._sections() if s.get_visible() and not s.folded for h in s.hunks]
        index = self._focused_index(hunks)
        if index is None:
            return False
        index += 1 if delta > 0 else -1
        if not 0 <= index < len(hunks):
            return False
        section = hunks[index]
        side = focused.side_of(view)
        old_side = len(section.views) == 2 and side == diffmodel.OLD
        entering = section.views[0] if old_side else section.views[-1]
        entering.place_cursor(0 if delta > 0 else len(entering.rows) - 1)
        ok = section.grab(None, side)
        self._set_current(section.file.path, section.hunk.index)
        self._show_cursor(entering)
        return ok

    def _show_cursor(self, view: _HunkView) -> None:
        """Scroll the column so *view*'s cursor line is on screen (each hunk
        sits in a scroller that never scrolls vertically: the column
        must). Placed now and again from a low idle, past the viewport's
        own scroll-to-focus and a fresh buffer's estimated heights."""

        def place() -> bool:
            if view.view.get_parent() is None:
                return GLib.SOURCE_REMOVE
            it = view.buffer.get_iter_at_mark(view.buffer.get_insert())
            y, height = view.view.get_line_yrange(it)
            _x, top = view.view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, 0, y)
            # In the column's coordinates (plus its top margin: the
            # viewport's content), which hold whatever the scroll value
            # is — bounds against the scroller itself are stale until the
            # next layout after a set_value.
            ok, bounds = view.view.compute_bounds(self._column)
            if not ok:
                return GLib.SOURCE_REMOVE
            adj = self._scroller.get_vadjustment()
            line_top = bounds.get_y() + self._column.get_margin_top() + top
            line_bottom = line_top + height
            value = adj.get_value()
            page = adj.get_page_size()
            margin = height  # a line of air, so the next step is on screen too
            if line_top - value < margin:
                adj.set_value(max(0.0, line_top - margin))
            elif line_bottom - value > page - margin:
                end = adj.get_upper() - page
                adj.set_value(max(0.0, min(end, line_bottom - page + margin)))
            return GLib.SOURCE_REMOVE

        place()
        GLib.idle_add(place, priority=GLib.PRIORITY_LOW)

    def expand_gap_before_focus(self) -> bool:
        """`z`: draw every unchanged line above the focused hunk (the
        current one when none has the keyboard) — hunk's gap toggle, one
        way: the row folds itself away once nothing is left to show."""
        hunk = self._focused_hunk
        if hunk is None or hunk.get_parent() is None:
            path, index = self._current
            section = self._section_for(path, diffmodel.NEW)
            hunk = next((h for h in (section.hunks if section else []) if h.hunk.index == index), None)
        if hunk is None:
            return False
        return self.expand_gap_before(hunk)

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

    # -- find --

    def search(self, text: str) -> tuple[int, int]:
        """The find bar's query: every occurrence of *text* (case-insensitive,
        plain text) in the shown hunks' lines is highlighted, the first is
        selected and scrolled to. Returns (1-based current, total) —
        (0, 0) for no query or no match; the total stops at
        MAX_SEARCH_MATCHES."""
        self._search_text = text or ""
        self._search_settings.set_search_text(self._search_text or None)
        self._rescan_search()
        if self._matches:
            self._match_index = 0
            self._show_match()
        return self.search_position()

    def search_step(self, forward: bool) -> tuple[int, int]:
        """Enter / Shift+Enter: select the next (previous) match, wrapping
        around the whole diff, across hunks and files."""
        if not self._matches:
            return self.search_position()
        self._match_index = (self._match_index + (1 if forward else -1)) % len(self._matches)
        self._show_match()
        return self.search_position()

    def search_position(self) -> tuple[int, int]:
        """(1-based current match, total) as the find bar reports them."""
        if not self._matches:
            return 0, 0
        return self._match_index + 1, len(self._matches)

    def search_clear(self) -> None:
        """The find bar closed: no query, no highlights."""
        self._search_text = ""
        self._search_settings.set_search_text(None)
        self._matches = []
        self._match_index = -1

    def focus_search_match(self) -> bool:
        """Put the keyboard in the hunk holding the current match (the find
        bar is closing; the reader carries on from there)."""
        if not self._matches or not 0 <= self._match_index < len(self._matches):
            return False
        hunk, view, _row, _start, _end = self._matches[self._match_index]
        if hunk.get_parent() is None:
            return False
        return view.view.grab_focus()

    def _rescan_search(self) -> None:
        """Recompute the matches over what is shown now (a reload, a filter,
        a layout change); the position is kept when it still exists. The
        stack layout reads each hunk's one view; split reads the old view
        for deletions and context and the new for additions, so a context
        line counts once."""
        previous = self._matches[self._match_index] if 0 <= self._match_index < len(self._matches) else None
        self._matches = []
        self._match_index = -1
        text = self._search_text
        if not text:
            return
        pattern = re.compile(re.escape(text), re.IGNORECASE)
        for section in self._sections():
            if not section.get_visible() or section.folded:
                continue
            for hunk in section.hunks:
                for position, view in enumerate(hunk.views):
                    self._search_context_for(view)
                    skip_context = position == 1
                    for row_index, row in enumerate(view.rows):
                        if row.kind == PAD or (skip_context and row.kind == diffmodel.CONTEXT):
                            continue
                        for match in pattern.finditer(row.text):
                            self._matches.append((hunk, view, row_index, match.start(), match.end()))
                            if len(self._matches) >= MAX_SEARCH_MATCHES:
                                break
                        if len(self._matches) >= MAX_SEARCH_MATCHES:
                            break
        if previous is not None and previous in self._matches:
            self._match_index = self._matches.index(previous)
        elif self._matches:
            self._match_index = 0

    def _search_context_for(self, view: _HunkView) -> None:
        if view in self._search_contexts:
            return
        context = GtkSource.SearchContext.new(view.buffer, self._search_settings)
        context.set_highlight(True)
        self._search_contexts[view] = context

    def _show_match(self) -> None:
        hunk, view, row, start, end = self._matches[self._match_index]
        if hunk.get_parent() is None:
            return
        if not view.select_match(row, start, end):
            return
        _ok, first = view.buffer.get_iter_at_line_offset(row, start)
        keyedslots.scroll_to(self._scroller, hunk)
        view.view.scroll_to_iter(first, 0.1, False, 0, 0)
        self._set_current(hunk.file.path, hunk.hunk.index)

    # -- probes (the e2e's) --

    def file_rows(self) -> list[tuple[str, str, bool]]:
        """(path, kind, shown) per section, in order."""
        return [(s.file.path, s.file.kind, s.get_visible()) for s in self._sections()]

    def hunk_rows(self, path: str) -> list[str]:
        """The hunk headers of *path*'s section, in order."""
        section = self._section_for(path, diffmodel.NEW)
        return [h.hunk.header for h in section.hunks] if section is not None else []

    def hunk_serials(self, path: str) -> list[int]:
        """The serial of each hunk section of *path*, in order — a number
        minted once per widget, so a check can say across a reload which
        hunks kept theirs (and hold no reference to a widget for it)."""
        section = self._section_for(path, diffmodel.NEW)
        return [h.serial for h in section.hunks] if section is not None else []

    def hunk_indexes(self, path: str) -> list[int]:
        """The `hunk.index` each hunk section of *path* speaks for, in
        order — `list(range(n))` after any reload, kept widgets included."""
        section = self._section_for(path, diffmodel.NEW)
        return [h.hunk.index for h in section.hunks] if section is not None else []

    def badge_rows(self) -> list[tuple[str, str, bool]]:
        """(path label, badge, has picture) per section, in order — the
        header's words as drawn, and whether an image preview sits under
        it."""
        return [(*s.header_text(), s._preview is not None) for s in self._sections()]

    def set_scroll(self, fraction: float) -> None:
        """Scroll the stream to *fraction* of its range (0.0 top, 1.0
        bottom), as a wheel would — the current file follows after the
        settle."""
        adjustment = self._scroller.get_vadjustment()
        span = adjustment.get_upper() - adjustment.get_page_size()
        adjustment.set_value(adjustment.get_lower() + max(span, 0.0) * min(max(fraction, 0.0), 1.0))

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

    def select_lines(self, path: str, hunk: int, first: int, last: int) -> bool:
        """Select lines *first*..*last* (indexes into the hunk's lines) of
        hunk *hunk* of *path* as a drag on the line numbers would — in the
        stack layout's one view, or the split's new side (the old for a
        selection of deletions alone)."""
        section = self._section_for(path, diffmodel.NEW)
        if section is None or not 0 <= hunk < len(section.hunks):
            return False
        target = section.hunks[hunk]
        wanted = set(range(min(first, last), max(first, last) + 1))
        for view in reversed(target.views):  # the new side first
            rows = [i for i, row in enumerate(view.rows) if row.line in wanted]
            if rows:
                view.view.grab_focus()
                view.select_rows(min(rows), max(rows))
                return True
        return False

    def hunk_action_labels(self, path: str, hunk: int) -> tuple[str, str | None]:
        """(the primary button's words, the discard button's or None when
        hidden) on hunk *hunk* of *path*."""
        section = self._section_for(path, diffmodel.NEW)
        if section is None or not 0 <= hunk < len(section.hunks):
            return "", None
        target = section.hunks[hunk]
        discard = target.discard_button.text() if target.discard_button.get_visible() else None
        return target.primary_button.text(), discard

    def file_action_labels(self, path: str) -> tuple[str, str | None]:
        section = self._section_for(path, diffmodel.NEW)
        if section is None:
            return "", None
        discard = section.discard_button.text() if section.discard_button.get_visible() else None
        return section.primary_button.text(), discard

    def click_hunk_action(self, path: str, hunk: int, discard: bool = False) -> bool:
        """Press a hunk header's button as a click would."""
        section = self._section_for(path, diffmodel.NEW)
        if section is None or not 0 <= hunk < len(section.hunks):
            return False
        section.hunks[hunk].button(discard).emit("clicked")
        return True

    def click_file_action(self, path: str, discard: bool = False) -> bool:
        """Press a file header's button as a click would."""
        section = self._section_for(path, diffmodel.NEW)
        if section is None:
            return False
        section.button(discard).emit("clicked")
        return True

    def busy(self) -> bool:
        return self._busy

    def acting_button_spinning(self) -> bool:
        """Probe: the button the running request came from shows its
        spinner (set_busy keeps it for the request's whole life: the plan
        read, the confirm dialog, the run)."""
        acting = self._acting
        return acting is not None and acting._stack.get_visible_child_name() == "spinner"

    def _probe_view(self, path: str, hunk: int, row: int) -> tuple[_HunkSection, _HunkView] | None:
        """The hunk section and the view a gutter or pointer probe drives:
        the stack's one view, or the split's new side."""
        section = self._section_for(path, diffmodel.NEW)
        if section is None or not 0 <= hunk < len(section.hunks):
            return None
        target = section.hunks[hunk]
        view = target.views[-1] if target.views else None
        if view is None or not 0 <= row < len(view.rows):
            return None
        return target, view

    def gutter_press(self, path: str, hunk: int, row: int, shift: bool = False) -> bool:
        """Probe: a press on the line numbers over *row* (a row index of
        the hunk's view) through the gutter mapping the gesture uses."""
        found = self._probe_view(path, hunk, row)
        if found is None:
            return False
        _section, view = found
        y = view.gutter_y(row)
        return y is not None and view.gutter_press(y, shift)

    def gutter_drag(self, path: str, hunk: int, row: int) -> bool:
        """Probe: the drag after gutter_press reached *row*."""
        found = self._probe_view(path, hunk, row)
        if found is None:
            return False
        _section, view = found
        y = view.gutter_y(row)
        return y is not None and view.gutter_extend(y)

    def context_menu_labels(self, path: str, hunk: int, row: int) -> list[str] | None:
        """Probe: right-click *row* of the hunk's view as the gesture
        would; the labels of the menu that opened (popped down again), or
        None when none did."""
        found = self._probe_view(path, hunk, row)
        if found is None:
            return None
        section, view = found
        popover = section.open_context_menu(view, row)
        if popover is None:
            return None
        labels: list[str] = []
        model = popover.get_menu_model()
        for i in range(model.get_n_items() if model is not None else 0):
            part = model.get_item_link(i, Gio.MENU_LINK_SECTION)
            for j in range(part.get_n_items() if part is not None else 0):
                label = part.get_item_attribute_value(j, Gio.MENU_ATTRIBUTE_LABEL, GLib.VariantType("s"))
                if label is not None:
                    labels.append(label.get_string())
        popover.popdown()
        return labels

    def note_rows(self, path: str, hunk: int) -> list[tuple[str, str, str, int, str, bool]]:
        """Probe: the cards under *path*'s hunk *hunk* as (id, source,
        side, line, summary, shown) — a draft's id is ""."""
        section = self._section_for(path, diffmodel.NEW)
        target = next((h for h in (section.hunks if section else []) if h.hunk.index == hunk), None)
        if target is None:
            return []
        return [
            (
                card.note.id if card.note else "",
                card.note.source if card.note else diffnotes.USER,
                card.side,
                card.line,
                card.note.summary if card.note else card.text(),
                card.get_visible(),
            )
            for card in target.cards()
        ]

    def note_marks(self, path: str, hunk: int) -> list[int]:
        """Probe: the indexes into the hunk's lines that carry a note."""
        section = self._section_for(path, diffmodel.NEW)
        target = next((h for h in (section.hunks if section else []) if h.hunk.index == hunk), None)
        return sorted({index for _note, index in target.placed_notes}) if target else []

    def highlight_rows(self, path: str, hunk: int) -> list[tuple[int, int, int, str]]:
        """Probe: (line index, start, end, tone) of the highlights painted
        on the hunk's views, as the views hold them."""
        section = self._section_for(path, diffmodel.NEW)
        target = next((h for h in (section.hunks if section else []) if h.hunk.index == hunk), None)
        if target is None:
            return []
        return sorted((index, m.start, m.end, m.tone) for m, index in target.placed_highlights)

    def note_editor_text(self) -> str | None:
        return self._editing.text() if self.editing() else None

    def set_note_editor_text(self, text: str) -> bool:
        if not self.editing():
            return False
        self._editing.set_text(text)
        return True

    def commit_note(self) -> bool:
        """Probe: what `Ctrl+Enter` does in the open editor."""
        return self.editing() and self._editing.commit()

    def cancel_note(self) -> bool:
        """Probe: what `Esc` does in the open editor."""
        if not self.editing():
            return False
        self._editing.cancel()
        return True

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

    def on_hunk_selection(self, section: _HunkSection, view: _HunkView) -> None:
        """A view's selection moved (snapped already): one selection in
        the whole stream — a new one clears the others, in every other
        hunk and on the hunk's other side — and the hunk's buttons follow."""
        if view.selected_rows() is None:
            if self._selection is section and section.selection() is None:
                self._selection = None
            section.sync_actions()
            return
        previous = self._selection
        self._selection = section
        if previous is not None and previous is not section and previous.get_parent() is not None:
            previous.clear_selection()
            previous.sync_actions()
        for other in section.views:
            if other is not view:
                other.clear_selection()
        section.sync_actions()

    def _on_key_pressed(self, _ctrl, keyval: int, _keycode: int, _state) -> bool:
        if self.editing():
            return False  # the note editor's own controller reads Escape
        if keyval == Gdk.KEY_Escape and self.has_selection():
            self.clear_selection()
            return True
        return False

    def _on_pinned_scroll(self, controller: Gtk.EventControllerScroll, _dx: float, dy: float) -> bool:
        adjustment = self._scroller.get_vadjustment()
        step = dy
        if controller.get_unit() == Gdk.ScrollUnit.WHEEL:
            step = dy * (adjustment.get_page_size() ** (2.0 / 3.0))
        upper = adjustment.get_upper() - adjustment.get_page_size()
        adjustment.set_value(max(0.0, min(upper, adjustment.get_value() + step)))
        return True

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
        self._rescan_search()  # a layout change rebuilt the views the matches point into

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
            self._pinned_section = top
            self._pinned_primary.set_text(top.primary_button.text())
            self._pinned_primary.set_tooltip_text(top.primary_button.get_tooltip_text())
            self._pinned_discard.set_visible(top.discard_button.get_visible())
            self._pinned_discard.set_text(top.discard_button.text())
            self._pinned_discard.set_tooltip_text(top.discard_button.get_tooltip_text())
        else:
            self._pinned_section = None
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
