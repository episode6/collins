"""GitHub-flavored markdown as a tree of blocks — the PR page's parse layer.

`parse_blocks` hands a markdown body to markdown-it-py (its ``gfm-like``
preset: tables, strikethrough, ``http(s)://`` linkification — the preset's
fuzzy linkify of bare domains and emails is switched off in `_load`, being
quadratic, and ``www.`` autolinks come back from `link_www`) and folds the token
stream into a small frozen dataclass tree in Collins' own vocabulary, so
the widget layer (mdwidgets) never sees a markdown-it token and the parser
could be swapped without touching a widget. Inline runs are rendered here
too, straight to Pango markup, by a walker that keeps an open-tag stack:
every tag it writes is one it also closes, so the markup is well-formed by
construction rather than by a check after the fact.

GTK-free on purpose (GLib alone, for `markup_escape_text`) so the whole
layer runs under the unit suite. markdown-it-py is loaded behind a latch:
where the package is missing — or ever breaks — `available()` is False and
the PR page keeps rendering through `formatting.split_body` +
`formatting.md_to_pango`, the regex pipeline that stays chat's renderer.

Everything that reaches this module is repository content, untrusted:
every text fragment is markup-escaped, every href is escaped and gated to
http(s) on top of markdown-it's own refusal of ``javascript:`` and friends,
HTML is honoured for a five-tag whitelist and otherwise shown as escaped
literal text, nesting is capped at `MAX_DEPTH` (markdown-it's own
``maxNesting`` of 20 is the outer wall), and `formatting.MAX_BODY_IMAGES`
still caps how many pictures one body can ask for. GitHub references
(``#123``, ``owner/repo#123``, ``@user``, a commit's hex) become links
only under a `RepoContext` the page hands in — built from the PR's own
repository, host and head commit, never read off the body — and only in
plain text tokens: a reference inside a code span or an existing link
stays what it was.

Package names, for the record: markdown-it-py 3.0.0 (Ubuntu) and 4.2.0
(Fedora, Arch) both ship ``gfm-like``; ``gfm-like2`` exists only on 4.1+
and is never used here — `tests/test_mdblocks.py` pins the preset name.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from gi.repository import GLib

from .formatting import MAX_BODY_IMAGES, BodyImage, html_image

log = logging.getLogger(__name__)

# The one preset this module ever asks for — see the module docstring.
PRESET = "gfm-like"
# Containers nested past this many levels flatten to the escaped source
# text of the whole subtree: real bodies never get there, and it bounds
# the recursion (and the widget tree) adversarial input can build.
MAX_DEPTH = 6
# GitHub's alert kinds, as written in the marker (`> [!NOTE]`), lowercased
# for `Quote.kind`.
ALERT_KINDS = ("note", "tip", "important", "warning", "caution")
# What a table renders as a grid: the rows and columns past these caps are
# a count in a "more on GitHub" link (`cap_table`). Parsing is unbounded —
# a 5 000-row table parses in a few tens of ms — the cap is on the cell
# labels a grid builds, which is layout the main loop pays for.
TABLE_MAX_ROWS = 50
TABLE_MAX_COLUMNS = 8
# GitHub's username alphabet (it also bans leading/trailing/double hyphens,
# but a 404 on those is harmless — this only has to keep URLs sane). The
# one gate for a login wherever one goes into a URL: `avatars` uses it too.
LOGIN_RE = re.compile(r"^[A-Za-z0-9-]{1,39}$")

_md = None
_import_error: str | None = None


def _load():
    """Construct the parser once; remember a failure the same way."""
    global _md, _import_error
    if _md is not None or _import_error is not None:
        return _md
    try:
        from markdown_it import MarkdownIt

        _md = MarkdownIt(PRESET)
        linkify = getattr(_md, "linkify", None)
        if linkify is not None:
            # The preset's fuzzy linkify (bare domains, emails) is quadratic
            # per paragraph: 50 KB of `www.a.com ` parsed in 6 s, `a@b.com`
            # likewise, on the main loop. Off, the same parses in
            # milliseconds and http(s):// autolinks still come through;
            # `link_www` puts GitHub's `www.` autolinks back in linear time.
            linkify.set({"fuzzy_link": False, "fuzzy_email": False})
    except Exception as exc:  # noqa: BLE001 — ImportError, or the preset's own linkify import
        _import_error = f"{type(exc).__name__}: {exc}"
        log.info("markdown-it-py unavailable, PR bodies use the regex renderer: %s", _import_error)
        return None
    return _md


def available() -> bool:
    """Whether the block layer can parse at all — False means the PR page
    stays on the regex pipeline for every body."""
    return _load() is not None


# -- the block vocabulary ------------------------------------------------------
#
# Every block carries its `source`: the markdown it was folded from (the
# lines its token mapped to, or a paragraph's inline content). The preview
# cut works on a paragraph's source rather than its markup, and a block the
# widget layer can't render yet — or has no widget budget left for — shows
# as escaped source text, never as nothing.


@dataclass(frozen=True)
class Text:
    """A paragraph, pre-rendered to Pango markup. `source` is the
    paragraph's markdown as markdown-it saw it (the inline token's
    content: list indents and quote markers already stripped), so a cut
    front of it re-renders through `render_inline`."""

    markup: str
    source: str


@dataclass(frozen=True)
class Heading:
    level: int
    markup: str
    source: str


@dataclass(frozen=True)
class CodeBlock:
    """A fence or an indented block. `lang` is the fence's info word,
    lowercased, or None; mapping it to a GtkSource language id is the
    widget layer's job."""

    text: str
    lang: str | None
    source: str


@dataclass(frozen=True)
class Table:
    """Cells are inline Pango markup; `aligns` per column is ``left``,
    ``center``, ``right`` or None. Cells are inline-only, as GitHub's are:
    an image in a cell degrades to its alt-text anchor."""

    aligns: tuple[str | None, ...]
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    source: str


@dataclass(frozen=True)
class Quote:
    """A block quote; `kind` is ``plain`` or one of `ALERT_KINDS` when the
    quote opened with GitHub's ``[!KIND]`` marker (the marker itself is
    dropped from the children)."""

    children: tuple[Block, ...]
    source: str
    kind: str = "plain"


@dataclass(frozen=True)
class ListItem:
    """`check` is None for an ordinary item, True/False for a task-list
    item (``- [x]`` / ``- [ ]``), whose marker is stripped from the text."""

    children: tuple[Block, ...]
    check: bool | None = None


@dataclass(frozen=True)
class ListBlock:
    ordered: bool
    start: int
    items: tuple[ListItem, ...]
    source: str


@dataclass(frozen=True)
class Details:
    """``<details>`` … ``</details>``: `summary` is escaped plain text (tags
    stripped, no markdown), `open` whether the tag asked to start
    expanded, `children` everything up to the matching close."""

    summary: str
    children: tuple[Block, ...]
    open: bool
    source: str


@dataclass(frozen=True)
class ImageRow:
    """Images the source kept on one line — a badge strip, a before/after
    pair — which stay on one line on screen."""

    images: tuple[BodyImage, ...]
    source: str


@dataclass(frozen=True)
class Rule:
    source: str = "---"


Block = Text | Heading | CodeBlock | Table | Quote | ListBlock | Details | ImageRow | Rule


# -- the repository context -----------------------------------------------------


@dataclass(frozen=True)
class RepoContext:
    """Where a body's GitHub references point: the PR's own repository
    (``owner/name``), the host its URL is on, and — for a relative link
    like ``[x](docs/a.md)`` — the head commit to read the file at (a
    40-hex oid, or "" to leave such links as text). Built through
    `repo_context`, which holds each part to its shape; a body never
    gets to choose any of them."""

    repository: str
    host: str = "github.com"
    head: str = ""


_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_OID_RE = re.compile(r"^[0-9a-f]{40}$")


def repo_context(repository: str | None, host: str = "github.com", head: str = "") -> RepoContext | None:
    """A `RepoContext` for *repository* on *host* at *head*, or None when
    the repository isn't ``owner/name``-shaped — then nothing links. A
    host that isn't hostname-shaped falls back to github.com and a head
    that isn't a full oid to "" (relative links stay text)."""
    if not repository or not _REPOSITORY_RE.match(repository):
        return None
    if not host or not _HOST_RE.match(host):
        host = "github.com"
    if not head or not _OID_RE.match(head):
        head = ""
    return RepoContext(repository, host, head)


# -- parsing -----------------------------------------------------------------


def parse_blocks(text: str, images: bool = True, refs: RepoContext | None = None) -> list[Block]:
    """*text* as a list of top-level blocks. With *images* off (the
    ``pr_inline_images`` setting) no `ImageRow` is ever made: an image is
    its alt-text link, as the regex renderer draws it. With *refs*, GitHub
    references in plain text link into that repository (`link_refs`).

    Raises when markdown-it is unavailable (`available()` says so first)
    or refuses the body — the caller's cue to fall back per body.
    """
    md = _load()
    if md is None:
        raise RuntimeError(_import_error or "markdown-it-py unavailable")
    lines = text.split("\n")
    parser = _Folder(lines, images, refs)
    return parser.fold(md.parse(text), 0)


def render_inline(text: str, refs: RepoContext | None = None) -> str:
    """One line or paragraph of markdown as Pango markup through the same
    walker `parse_blocks` uses — how a cut paragraph is re-rendered. Images
    degrade to their alt-text anchors (there is no row to put them in)."""
    md = _load()
    if md is None:
        raise RuntimeError(_import_error or "markdown-it-py unavailable")
    tokens = md.parseInline(text)
    children = tokens[0].children if tokens and tokens[0].children else []
    return _Inline(children, refs).markup()


def line_cost(block: Block, image_lines: int = 4) -> int:
    """What *block* costs a fold's line budget — an estimate of the lines
    it draws, so a preview spends its budget on blocks the way it spends
    it on a paragraph's newlines."""
    if isinstance(block, Text):
        return block.source.count("\n") + 1
    if isinstance(block, Heading):
        return 1
    if isinstance(block, CodeBlock):
        return min(block.text.count("\n") + 1, 8)
    if isinstance(block, Table):
        return min(len(block.rows) + 1, 6)
    if isinstance(block, Quote):
        return sum(line_cost(child, image_lines) for child in block.children) or 1
    if isinstance(block, ListBlock):
        return sum(
            sum(line_cost(child, image_lines) for child in item.children) or 1
            for item in block.items
        )
    if isinstance(block, Details):
        return 2
    if isinstance(block, ImageRow):
        return image_lines
    return 1


def cap_table(table: Table) -> tuple[Table, int, int]:
    """*table* trimmed to what a grid draws — `TABLE_MAX_COLUMNS` columns
    of `TABLE_MAX_ROWS` rows, every row squared to the header's width (a
    short row padded with empty cells, a long one cut, as GitHub squares
    them) — and the counts of rows and columns left out. The caps bound
    the cell labels one body can ask for; the counts feed the link to the
    rest on GitHub."""
    width = len(table.header)
    columns = min(width, TABLE_MAX_COLUMNS)
    aligns = tuple(table.aligns[:columns]) + (None,) * (columns - len(table.aligns[:columns]))
    rows = tuple(
        tuple(row[:columns]) + ("",) * (columns - len(row[:columns]))
        for row in table.rows[:TABLE_MAX_ROWS]
    )
    shown = Table(aligns, tuple(table.header[:columns]), rows, table.source)
    return shown, len(table.rows) - len(rows), width - columns


_IMG_TAG_RE = re.compile(r"<img\b[^<>]{0,1000}>", re.I)
_DETAILS_OPEN_RE = re.compile(r"^\s*<details\b([^>]*)>", re.I)
_DETAILS_CLOSE_RE = re.compile(r"^\s*</details\s*>", re.I)
_DETAILS_TAG_RE = re.compile(r"<(/?)details\b", re.I)
_SUMMARY_RE = re.compile(r"<summary\b[^>]*>(.*?)</summary\s*>", re.I | re.S)
_TAG_RE = re.compile(r"<[^<>]{0,1000}>")
_OPEN_ATTR_RE = re.compile(r"(?:^|\s)open(?:\s|=|$)", re.I)
_ALERT_RE = re.compile(r"^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]$")
_TASK_RE = re.compile(r"^\[([ xX])\](?: |$)")


class _Folder:
    """Folds a markdown-it token stream into blocks, one container at a time."""

    def __init__(self, lines: list[str], images: bool, refs: RepoContext | None = None) -> None:
        self._lines = lines
        self._images = images
        self._refs = refs
        self._image_budget = MAX_BODY_IMAGES
        self._pending: list[Block] = []  # blocks one token unfolded into several

    def source(self, token) -> str:
        if token.map:
            start, stop = token.map
            return "\n".join(self._lines[start:stop])
        return token.content or ""

    def fold(self, tokens: list, depth: int) -> list[Block]:
        blocks: list[Block] = []
        closes = _details_closes(tokens)
        i = 0
        while i < len(tokens):
            block, i = self._one(tokens, i, depth, blocks, closes)
            if block is not None:
                blocks.append(block)
            if self._pending:
                blocks.extend(self._pending)
                self._pending.clear()
        return blocks

    def _one(
        self, tokens: list, i: int, depth: int, blocks: list[Block], closes: dict[int, int]
    ) -> tuple[Block | None, int]:
        token = tokens[i]
        kind = token.type
        if kind == "paragraph_open":
            close = _matching(tokens, i, "paragraph_close")
            inline = tokens[i + 1] if i + 1 < close else None
            if inline is None or inline.type != "inline":
                return None, close + 1
            self._paragraph(inline, blocks)
            return None, close + 1
        if kind == "heading_open":
            close = _matching(tokens, i, "heading_close")
            inline = tokens[i + 1] if i + 1 < close else None
            markup = self._inline(inline.children or []) if inline is not None else ""
            level = int(token.tag[1:]) if token.tag[1:].isdigit() else 1
            return Heading(min(max(level, 1), 6), markup, self.source(token)), close + 1
        if kind in ("fence", "code_block"):
            lang = None
            if kind == "fence" and token.info:
                lang = (token.info.strip().split() or [""])[0].lower() or None
            return CodeBlock(token.content, lang, self.source(token)), i + 1
        if kind == "hr":
            return Rule(self.source(token) or "---"), i + 1
        if kind == "table_open":
            close = _matching(tokens, i, "table_close")
            return self._table(tokens[i + 1 : close], self.source(token)), close + 1
        if kind == "blockquote_open":
            close = _matching(tokens, i, "blockquote_close")
            source = self.source(token)
            if depth + 1 > MAX_DEPTH:
                return _literal(source), close + 1
            inner = tokens[i + 1 : close]
            alert = _alert_kind(inner)
            return Quote(tuple(self.fold(inner, depth + 1)), source, alert or "plain"), close + 1
        if kind in ("bullet_list_open", "ordered_list_open"):
            close = _matching(tokens, i, kind.replace("_open", "_close"))
            source = self.source(token)
            if depth + 1 > MAX_DEPTH:
                return _literal(source), close + 1
            ordered = kind == "ordered_list_open"
            start = _start(token) if ordered else 1
            items = self._items(tokens[i + 1 : close], depth + 1)
            return ListBlock(ordered, start, tuple(items), source), close + 1
        if kind == "html_block":
            return self._html_block(tokens, i, depth, closes.get(i))
        if kind.endswith("_open"):
            # A container this module has no vocabulary for: its whole
            # source, escaped, and the stream skipped past its close.
            close = _matching(tokens, i, kind[: -len("_open")] + "_close")
            return _literal(self.source(token)), close + 1
        if kind.endswith("_close"):
            return None, i + 1
        source = self.source(token)
        return (_literal(source) if source.strip() else None), i + 1

    def _inline(self, children: list) -> str:
        return _Inline(children, self._refs).markup()

    def _paragraph(self, inline, blocks: list[Block]) -> None:
        children = list(inline.children or [])
        source = inline.content or ""
        if self._images and children and _only_images(children):
            self._image_rows(children, source, blocks)
            return
        markup = self._inline(children)
        if markup.strip() or source.strip():
            blocks.append(Text(markup, source))

    def _image_rows(self, children: list, source: str, blocks: list[Block]) -> None:
        """An image-only paragraph as rows: images on one source line share
        a row, and each row's source is its own line; past the body's image
        cap the rest are alt-text anchors."""
        lines = source.split("\n")
        rows: list[tuple[int, list[BodyImage]]] = [(0, [])]
        line = 0
        leftovers: list[str] = []
        for token in children:
            if token.type == "softbreak":
                line += 1
                if rows[-1][1]:
                    rows.append((line, []))
                else:
                    rows[-1] = (line, rows[-1][1])
                continue
            if token.type == "text":
                continue
            image = _image_of(token)
            if image is None:
                leftovers.append(_image_anchor(token))
            elif self._image_budget > 0:
                self._image_budget -= 1
                rows[-1][1].append(image)
            else:
                leftovers.append(_image_anchor(token))
        for line, row in rows:
            if row:
                blocks.append(ImageRow(tuple(row), lines[line] if line < len(lines) else source))
        if leftovers:
            blocks.append(Text(" ".join(leftovers), source))

    def _items(self, tokens: list, depth: int) -> list[ListItem]:
        items: list[ListItem] = []
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token.type != "list_item_open":
                i += 1
                continue
            close = _matching(tokens, i, "list_item_close")
            inner = tokens[i + 1 : close]
            check = _task_check(inner)
            items.append(ListItem(tuple(self.fold(inner, depth)), check))
            i = close + 1
        return items

    def _table(self, tokens: list, source: str) -> Table:
        aligns: list[str | None] = []
        header: list[str] = []
        rows: list[tuple[str, ...]] = []
        row: list[str] | None = None
        in_head = False
        for token in tokens:
            kind = token.type
            if kind == "thead_open":
                in_head = True
            elif kind == "thead_close":
                in_head = False
            elif kind == "tr_open":
                row = []
            elif kind == "tr_close":
                if row is not None and not in_head:
                    rows.append(tuple(row))
                row = None
            elif kind in ("th_open", "td_open"):
                if kind == "th_open":
                    aligns.append(_align(token))
            elif kind == "inline" and row is not None:
                cell = self._inline(token.children or [])
                if in_head:
                    header.append(cell)
                else:
                    row.append(cell)
        if not aligns:
            aligns = [None] * len(header)
        return Table(tuple(aligns), tuple(header), tuple(rows), source)

    def _html_block(
        self, tokens: list, i: int, depth: int, close: int | None
    ) -> tuple[Block | None, int]:
        token = tokens[i]
        content = token.content or ""
        opener = _DETAILS_OPEN_RE.match(content)
        if opener is None:
            return self._html_lines(content, token), i + 1
        if close is None or depth + 1 > MAX_DEPTH:
            return _literal(content.rstrip("\n")), i + 1
        is_open = bool(_OPEN_ATTR_RE.search(opener.group(1)))
        after_tag = content[opener.end() :]
        summary_match = _SUMMARY_RE.search(after_tag)
        first_child = i + 1
        if summary_match is None and first_child < close:
            nxt = tokens[first_child]
            if nxt.type == "html_block" and _SUMMARY_RE.search(nxt.content or ""):
                summary_match = _SUMMARY_RE.search(nxt.content or "")
                rest = after_tag + (nxt.content or "")[summary_match.end() :]
                first_child += 1
            else:
                rest = after_tag
        elif summary_match is not None:
            rest = after_tag[summary_match.end() :]
        else:
            rest = after_tag
        summary = ""
        if summary_match is not None:
            summary = GLib.markup_escape_text(_TAG_RE.sub("", summary_match.group(1)).strip())
        children: list[Block] = []
        if rest.strip():
            children.append(_literal(rest.strip("\n")))
        children += self.fold(tokens[first_child:close], depth + 1)
        start = token.map[0] if token.map else 0
        stop = tokens[close].map[1] if tokens[close].map else start
        source = "\n".join(self._lines[start:stop])
        return Details(summary, tuple(children), is_open, source), close + 1


    def _html_lines(self, content: str, token) -> Block | None:
        """An html_block that isn't a <details>: image rows for the lines
        that are nothing but ``<img>`` tags — an ``<img>`` on a line of its
        own is a CommonMark HTML block (type 7), not inline HTML, and it is
        how most screenshots in PR bodies are written — and escaped literal
        text for everything else. Returns the one block when there is one,
        else appends to the stream through `_pending`."""
        blocks: list[Block] = []
        literal: list[str] = []

        def flush() -> None:
            if literal:
                blocks.append(_literal("\n".join(literal)))
                literal.clear()

        for line in content.rstrip("\n").split("\n"):
            tags = _IMG_TAG_RE.findall(line)
            if tags and self._images and not _IMG_TAG_RE.sub("", line).strip():
                row: list[BodyImage] = []
                anchors: list[str] = []
                for tag in tags:
                    image = html_image(tag)
                    if image is None:
                        anchors.append(GLib.markup_escape_text(tag))
                    elif self._image_budget > 0:
                        self._image_budget -= 1
                        row.append(image)
                    else:
                        anchors.append(_image_anchor_html(tag))
                flush()
                if row:
                    blocks.append(ImageRow(tuple(row), line))
                if anchors:
                    blocks.append(Text(" ".join(anchors), line))
            elif tags and not self._images and not _IMG_TAG_RE.sub("", line).strip():
                flush()
                blocks.append(Text(" ".join(_image_anchor_html(tag) for tag in tags), line))
            else:
                literal.append(line)
        flush()
        if len(blocks) == 1:
            return blocks[0]
        self._pending.extend(blocks)
        return None


def _literal(source: str) -> Text:
    return Text(GLib.markup_escape_text(source), source)


def _matching(tokens: list, i: int, close_type: str) -> int:
    """The index of the *close_type* token pairing with the opener at *i*
    — the level's own, not a nested one's — or the end of the stream."""
    open_type = close_type[: -len("_close")] + "_open"
    depth = 0
    for j in range(i + 1, len(tokens)):
        kind = tokens[j].type
        if kind == open_type:
            depth += 1
        elif kind == close_type:
            if depth == 0:
                return j
            depth -= 1
    return len(tokens)


def _details_closes(tokens: list) -> dict[int, int]:
    """Index of the html_block closing each ``<details>`` opener in *tokens*
    (an html_block whose content starts with the tag), paired in one pass
    with a stack — every ``<details`` and ``</details`` tag inside every
    html_block's content counts, in order, so nesting matches its own
    open. An opener absent from the map is unmatched (or closed inside
    its own block) and renders literal rather than as an expander that
    eats the rest of the body. One linear pass: a body of thousands of
    unmatched openers must not cost a scan per opener."""
    closes: dict[int, int] = {}
    stack: list[int | None] = []
    for j, token in enumerate(tokens):
        if token.type != "html_block":
            continue
        content = token.content or ""
        # Only the leading tag of an opener block is a Details; further
        # opens in the same content are anonymous and just nest.
        real = _DETAILS_OPEN_RE.match(content) is not None
        for tag in _DETAILS_TAG_RE.finditer(content):
            if not tag.group(1):
                stack.append(j if real else None)
                real = False
            elif stack:
                opened = stack.pop()
                if opened is not None and opened != j:
                    closes[opened] = j
    return closes


def _start(token) -> int:
    value = token.attrGet("start")
    if value is None:
        return 1
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 1


def _align(token) -> str | None:
    style = token.attrGet("style") or ""
    for name in ("left", "center", "right"):
        if f"text-align:{name}" in style.replace(" ", ""):
            return name
    return None


def _first_text(tokens: list):
    """The first paragraph's inline and its first child, if the container
    opens with a paragraph — where a task marker or an alert marker sits."""
    if len(tokens) < 2 or tokens[0].type != "paragraph_open" or tokens[1].type != "inline":
        return None, None
    inline = tokens[1]
    children = inline.children or []
    if not children or children[0].type != "text":
        return inline, None
    return inline, children[0]


def _task_check(tokens: list) -> bool | None:
    inline, first = _first_text(tokens)
    if first is None:
        return None
    match = _TASK_RE.match(first.content)
    if match is None:
        return None
    first.content = first.content[match.end() :]
    inline.content = inline.content[match.end() :]
    return match.group(1) != " "


def _alert_kind(tokens: list) -> str | None:
    inline, first = _first_text(tokens)
    if first is None:
        return None
    match = _ALERT_RE.match(first.content)
    if match is None:
        return None
    children = inline.children
    if len(children) > 1 and children[1].type != "softbreak":
        return None
    del children[: 2 if len(children) > 1 else 1]
    inline.content = inline.content.split("\n", 1)[1] if "\n" in inline.content else ""
    return match.group(1).lower()


# -- inline rendering ------------------------------------------------------------

_HREF_OK_RE = re.compile(r"^https?://", re.I)
_MAX_HREF = 2_000
_HTML_TAG_RE = re.compile(r"^<(/?)([A-Za-z][A-Za-z0-9]*)")
# Inline HTML that renders as something other than its escaped self, and
# the Pango tag each becomes: exactly the corpus's tags plus GitHub's few
# text-level ones. <img> and <br> are handled apart (an image, a newline).
_HTML_TAGS = {"sub": "sub", "sup": "sup", "kbd": "tt"}
_SIMPLE = {
    "strong_open": ("strong", "b"),
    "em_open": ("em", "i"),
    "s_open": ("s", "s"),
}
_SIMPLE_CLOSE = {"strong_close": "strong", "em_close": "em", "s_close": "s"}


def _only_images(children: list) -> bool:
    """Whether a paragraph is nothing but images and the whitespace between
    them — the shape that becomes image rows rather than text."""
    seen = False
    for token in children:
        if token.type == "image" or (token.type == "html_inline" and _tag_name(token.content) == ("", "img")):
            seen = True
        elif token.type == "softbreak":
            continue
        elif token.type == "text" and not token.content.strip():
            continue
        else:
            return False
    return seen


def _tag_name(content: str) -> tuple[str, str] | None:
    match = _HTML_TAG_RE.match(content or "")
    if match is None:
        return None
    return match.group(1), match.group(2).lower()


def _image_of(token) -> BodyImage | None:
    """A markdown `image` token or an ``<img>`` html_inline as a BodyImage,
    or None when its source isn't fetchable."""
    if token.type == "html_inline":
        return html_image(token.content)
    src = token.attrGet("src") or ""
    if len(src) > _MAX_HREF or not _HREF_OK_RE.match(src):
        return None
    return BodyImage(url=src, alt=(token.content or "").strip())


def _image_anchor(token) -> str:
    """An image as text: its alt (or URL) as a link to the picture, the
    same degrade `md_to_pango` applies — or the escaped alt alone when the
    source isn't linkable."""
    if token.type == "html_inline":
        image = html_image(token.content)
        if image is None:
            return GLib.markup_escape_text(token.content)
        src, alt = image.url, image.alt
    else:
        src = token.attrGet("src") or ""
        alt = (token.content or "").strip()
    if len(src) > _MAX_HREF or not _HREF_OK_RE.match(src):
        return GLib.markup_escape_text(alt or src)
    label = GLib.markup_escape_text(alt or src)
    return f'<a href="{GLib.markup_escape_text(src)}">{label}</a>'


class _Inline:
    """Renders an inline token run to Pango markup with an open-tag stack.

    Each entry on the stack is (kind, pango_tag): the markdown-it or HTML
    construct that opened it, and the Pango tag written for it (None when
    the construct emitted nothing — a link that failed the http(s) gate
    still has its close to swallow). A close pops down to its opener,
    closing whatever was left open inside; a close with no opener on the
    stack renders as literal text; and whatever is still open at the end
    is closed then, so the result is well-formed whatever the input did.
    """

    def __init__(self, children: list, refs: RepoContext | None = None) -> None:
        self._children = children
        self._refs = refs
        self._out: list[str] = []
        self._stack: list[tuple[str, str | None]] = []

    def markup(self) -> str:
        children = self._children
        i = 0
        while i < len(children):
            token = children[i]
            kind = token.type
            if kind == "text":
                self._out.append(self._text(token.content))
            elif kind in _SIMPLE:
                self._open(*_SIMPLE[kind])
            elif kind in _SIMPLE_CLOSE:
                self._close(_SIMPLE_CLOSE[kind])
            elif kind == "code_inline":
                self._out.append(f"<tt>{GLib.markup_escape_text(token.content)}</tt>")
            elif kind in ("softbreak", "hardbreak"):
                self._out.append("\n")
            elif kind == "link_open":
                self._link(token, children, i)
            elif kind == "link_close":
                self._close("link", literal=False)
            elif kind == "image":
                self._out.append(_image_anchor(token))
            elif kind == "html_inline":
                self._html(token.content)
            elif token.children:
                self._out.append(_Inline(token.children, self._refs).markup())
            elif token.content:
                self._out.append(GLib.markup_escape_text(token.content))
            i += 1
        while self._stack:
            _kind, tag = self._stack.pop()
            if tag:
                self._out.append(f"</{tag}>")
        return "".join(self._out)

    def _text(self, content: str) -> str:
        """A text token as markup: its ``www.`` autolinks linked, and with
        a context its GitHub references too — unless a link is already
        open around it (text inside an anchor, or inside a link the gate
        refused, is the author's link text, not a reference). Code spans
        never come here: they are `code_inline` tokens."""
        if any(kind == "link" for kind, _tag in self._stack):
            return GLib.markup_escape_text(content)
        if self._refs is None:
            return link_www(content)
        return link_refs(content, self._refs)

    def _open(self, kind: str, tag: str | None) -> None:
        self._stack.append((kind, tag))
        if tag:
            self._out.append(f"<{tag}>")

    def _close(self, kind: str, literal: str | None = None) -> None:
        if not any(entry[0] == kind for entry in self._stack):
            if literal:
                self._out.append(GLib.markup_escape_text(literal))
            return
        while self._stack:
            open_kind, tag = self._stack.pop()
            if tag:
                self._out.append(f"</{tag}>")
            if open_kind == kind:
                return

    def _link(self, token, children: list, i: int) -> None:
        href = token.attrGet("href") or ""
        if self._refs is not None and self._refs.head and token.markup != "linkify":
            href = relative_href(href, self._refs)
        ok = len(href) <= _MAX_HREF and bool(_HREF_OK_RE.match(href))
        if ok and token.markup == "linkify":
            # GitHub's extended autolinks take http(s)://, www. and email
            # only; markdown-it's fuzzy linkify would link `example.com`.
            visible = _visible_text(children, i).lower()
            ok = visible.startswith(("http://", "https://", "www."))
        if ok:
            self._open("link", "a")
            self._out[-1] = f'<a href="{GLib.markup_escape_text(href)}">'
        else:
            self._open("link", None)

    def _html(self, content: str) -> None:
        name = _tag_name(content)
        if name is None:
            self._out.append(GLib.markup_escape_text(content))
            return
        closing, tag = name
        if tag == "img" and not closing:
            self._out.append(_image_anchor_html(content))
        elif tag == "br" and not closing:
            self._out.append("\n")
        elif tag in _HTML_TAGS:
            if closing:
                self._close(f"html:{tag}", literal=content)
            else:
                self._open(f"html:{tag}", _HTML_TAGS[tag])
        else:
            self._out.append(GLib.markup_escape_text(content))


# GitHub's extended `www.` autolink (GFM §6.9): `www.` at the start of a
# word, a domain of at least two dot-separated segments, then anything up
# to whitespace or `<`; trailing punctuation is left outside the link, as
# is a `)` with no `(` in the link to match it. linkify-it's fuzzy_link
# would find these too — quadratically (see `_load`); one linear scan of
# each text token does the same job.
_WWW_RE = re.compile(r"(?<![\w/@.-])www\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+[^\s<]*", re.I)
_WWW_TRAIL = "?!.,:*_~"


def link_www(text: str) -> str:
    """*text* escaped, with every ``www.`` autolink in it an ``http://``
    anchor — the one shape of GitHub's extended autolinks markdown-it no
    longer supplies with fuzzy linkify off (``http(s)://`` still come
    through as linkify tokens; emails are never linked, as a ``mailto:``
    would not pass the http(s) gate)."""
    out: list[str] = []
    end = 0
    for match in _WWW_RE.finditer(text):
        word = match.group(0).rstrip(_WWW_TRAIL)
        while word.endswith(")") and word.count(")") > word.count("("):
            word = word[:-1].rstrip(_WWW_TRAIL)
        if len(word) > _MAX_HREF - len("http://"):
            continue
        out.append(GLib.markup_escape_text(text[end : match.start()]))
        label = GLib.markup_escape_text(word)
        out.append(f'<a href="http://{label}">{label}</a>')
        end = match.start() + len(word)
    out.append(GLib.markup_escape_text(text[end:]))
    return "".join(out)


def _image_anchor_html(content: str) -> str:
    image = html_image(content)
    if image is None:
        return GLib.markup_escape_text(content)
    label = GLib.markup_escape_text(image.alt or image.url)
    return f'<a href="{GLib.markup_escape_text(image.url)}">{label}</a>'


def _visible_text(children: list, i: int) -> str:
    """The text a link at *i* shows, up to its close."""
    parts: list[str] = []
    depth = 0
    for token in children[i + 1 :]:
        if token.type == "link_open":
            depth += 1
        elif token.type == "link_close":
            if depth == 0:
                break
            depth -= 1
        elif token.type in ("text", "code_inline"):
            parts.append(token.content)
    return "".join(parts).strip()


# -- GitHub references ---------------------------------------------------------
#
# A post-pass over the text a body writes in the open — never inside a
# code span (its own token) or a link (the walker's stack says). The
# boundaries are GitHub's: `#123` and `owner/repo#123` not on the tail of
# a word or an `&` (an entity), `@user` on the login alphabet and not on
# the tail of a word or another `@` (an email is a linkify token anyway),
# and a commit's hex only as a whole word — lowercase, seven to forty
# characters, with at least one digit and one letter, so a phone number,
# a date or a word like "deadbeef" is left alone. A word character or a
# hyphen right after any of them means it was something else.

_REF_RE = re.compile(
    r"(?<![\w&/#@.-])(?:"
    r"(?P<owner>[A-Za-z0-9-]{1,39})/(?P<repo>[A-Za-z0-9._-]{0,99}[A-Za-z0-9_-])#(?P<xnum>\d{1,10})"
    r"|#(?P<num>\d{1,10})"
    r"|@(?P<login>[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)"
    r"|(?P<sha>(?=[0-9a-f]{0,39}[a-f])(?=[0-9a-f]{0,39}\d)[0-9a-f]{7,40})"
    r")(?![\w/-])"
)
# A link destination that is a path into the repository: no scheme, no
# leading slash, fragment or query, only path characters, no `..` step.
_RELATIVE_RE = re.compile(r"^(?:\./)?(?!/)(?:[A-Za-z0-9._~%-]+/)*[A-Za-z0-9._~%-]+$")
_MAX_RELATIVE = 1_000


def link_refs(text: str, refs: RepoContext) -> str:
    """*text* escaped, with every GitHub reference in it an anchor into
    *refs*' repository: ``#123`` and ``owner/repo#123`` to the issue (GitHub
    forwards a PR's number from there), ``@user`` to the profile, a commit's
    hex to the commit. Nothing in the URL comes from the body but the
    reference's own characters, which the pattern holds to their alphabets.
    The text between references goes through `link_www`, so a ``www.``
    autolink links too (a ``#``, ``@`` or hex run inside one sits on the
    tail of a word or after a ``/``, where the boundaries refuse it).
    """
    out: list[str] = []
    end = 0
    for match in _REF_RE.finditer(text):
        out.append(link_www(text[end : match.start()]))
        out.append(_ref_anchor(match, refs))
        end = match.end()
    out.append(link_www(text[end:]))
    return "".join(out)


def _ref_anchor(match: re.Match, refs: RepoContext) -> str:
    word = match.group(0)
    base = f"https://{refs.host}"
    if match.group("xnum") is not None:
        href = f"{base}/{match.group('owner')}/{match.group('repo')}/issues/{match.group('xnum')}"
    elif match.group("num") is not None:
        href = f"{base}/{refs.repository}/issues/{match.group('num')}"
    elif match.group("login") is not None:
        href = f"{base}/{match.group('login')}"
    else:
        href = f"{base}/{refs.repository}/commit/{match.group('sha')}"
    return f'<a href="{GLib.markup_escape_text(href)}">{GLib.markup_escape_text(word)}</a>'


def relative_href(href: str, refs: RepoContext) -> str:
    """*href* as written, or — when it is a path into the repository and
    *refs* names a head commit — the file's page at that commit
    (``https://host/owner/name/blob/<head>/<path>``), as GitHub resolves a
    relative link in a body. Anything else (an absolute URL, a fragment, a
    ``..`` step, a path GitHub wouldn't take) comes back untouched for the
    http(s) gate to judge."""
    if not refs.head or not href or len(href) > _MAX_RELATIVE:
        return href
    if not _RELATIVE_RE.match(href) or ".." in href.split("/"):
        return href
    path = href[2:] if href.startswith("./") else href
    return f"https://{refs.host}/{refs.repository}/blob/{refs.head}/{path}"
