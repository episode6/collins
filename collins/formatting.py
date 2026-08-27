# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-27. Full change history: git log for this file.

"""Small human-readable formatting helpers shared across the UI."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from gi.repository import GLib

from .i18n import _

_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
# Single-star italics run after bold, so the lookarounds only have to keep a
# stray half of a ** pair from matching; underscore italics must not fire
# inside snake_case, so both ends demand a non-word neighbourhood.
_ITALIC_STAR_RE = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_ITALIC_UNDER_RE = re.compile(r"(?<!\w)_([^_\n]+?)_(?!\w)")
_STRIKE_RE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_HEADING_RE = re.compile(r"(?m)^\s*#{1,6}\s+(.*)$")
_BULLET_RE = re.compile(r"(?m)^(\s*)[-*]\s+")
# One ordered-list line: indent, number, its marker (``.`` or ``)``), text.
# The digit cap bounds the ints a body can make this parse (Python won't even
# convert arbitrarily long ones); a longer "number" just isn't a list marker.
_NUMBERED_RE = re.compile(r"^([ \t]*)(\d{1,9})([.)])[ \t]+(.*)$")
# Matched against markup-escaped text, hence &gt; standing for the > markers.
# [ \t] rather than \s throughout: under MULTILINE, \s would walk through
# newlines and fold neighbouring lines into the quote.
_BLOCKQUOTE_RE = re.compile(r"(?m)^([ ]{0,3})((?:&gt;[ \t]?)+)(.*)$")
# The URL half tolerates one level of balanced parens — GitHub-made links
# routinely carry them ("...#L10(disambiguator)") — while a bare ) still ends
# the link, so prose parentheses around a whole link don't get swallowed.
_MD_URL = r"https?://(?:\([^()\s]*\)|[^()\s])+"
_LINK_RE = re.compile(rf"\[([^\]\n]+)\]\(({_MD_URL})\)")
_IMAGE_RE = re.compile(rf"!\[([^\]\n]*)\]\(({_MD_URL})\)")
_AUTOLINK_RE = re.compile(r"<(https?://[^>\s]+)>")
_SENT_A, _SENT_B = chr(0xE000), chr(0xE001)  # PUA sentinels survive markup escaping


def md_to_pango(text: str, links: bool = False) -> str:
    """Render common markdown as Pango markup; code spans are protected first.

    `links` turns markdown links into clickable anchors — off by default so
    chat text keeps rendering URLs verbatim. With links on, images degrade to
    their alt text as a link: nothing here ever fetches a remote resource.
    """
    stash: list[str] = []

    def keep(markup: str) -> str:
        stash.append(markup)
        return f"{_SENT_A}{len(stash) - 1}{_SENT_B}"

    def anchor(url: str, label: str) -> str:
        escaped = GLib.markup_escape_text(url)
        return keep(f'<a href="{escaped}">{GLib.markup_escape_text(label)}</a>')

    text = _FENCE_RE.sub(lambda m: keep(f"<tt>{GLib.markup_escape_text(m.group(1).rstrip())}</tt>"), text)
    text = _INLINE_CODE_RE.sub(lambda m: keep(f"<tt>{GLib.markup_escape_text(m.group(1))}</tt>"), text)
    if links:  # after code spans, so a URL inside backticks stays literal
        # Images first — an image is a link with a ! in front, and the link
        # pattern would otherwise claim it and leave the ! dangling.
        text = _IMAGE_RE.sub(lambda m: anchor(m.group(2), m.group(1) or m.group(2)), text)
        text = _LINK_RE.sub(lambda m: anchor(m.group(2), m.group(1)), text)
        text = _AUTOLINK_RE.sub(lambda m: anchor(m.group(1), m.group(1)), text)
    text = GLib.markup_escape_text(text)
    text = _HEADING_RE.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    text = _BLOCKQUOTE_RE.sub(_blockquote_markup, text)
    text = _BOLD_RE.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    # Bullets before single-star italics: a star bullet's marker is a lone *
    # at line start, and the italic pass would otherwise claim it as an
    # opening delimiter whenever another * appears later on the line.
    text = _BULLET_RE.sub(lambda m: f"{m.group(1)}• ", text)
    text = _ITALIC_STAR_RE.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    text = _ITALIC_UNDER_RE.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    text = _STRIKE_RE.sub(lambda m: f"<s>{m.group(1)}</s>", text)
    text = _renumber_lists(text)
    for i, markup in enumerate(stash):
        text = text.replace(f"{_SENT_A}{i}{_SENT_B}", markup)
    return text


@dataclass(frozen=True)
class BodyImage:
    """An image a body embeds: the URL to fetch, the alt text to fall back
    to, and the width its author asked for (an `<img width=>`, 0 when
    nobody said — markdown has no way to say it). All three are repository
    content, i.e. untrusted — the URL is only ever http(s) (nothing else is
    fetchable or linkable), the alt text only ever reaches a widget through
    markup escaping, and the width is a bounded int or nothing at all."""

    url: str
    alt: str
    width: int = 0


# A body may embed this many images before the rest stay text (they still
# render as their alt-text links, `md_to_pango`'s own fallback). A cap on
# widgets built and fetches started, so a body listing hundreds of URLs
# can't turn opening a PR into a hundred downloads.
MAX_BODY_IMAGES = 20
_MAX_URL = 2_000
# `[![alt](image)](link)` — a linked image, how badges and click-through
# screenshots are written. The image is what shows, so it parses as one
# (the outer link is dropped: the picture opens in the lightbox, and the
# link is a click away on GitHub).
#
# The image's own URL still has to be http(s) — it is the one that gets
# fetched — but the outer target may be anything a link can hold, relative
# paths included. Holding *it* to http(s) too would only mean not matching:
# the inner image would then parse on its own and leave the wrapper's `[`
# and `](target)` around the picture as literal text.
_LINK_TARGET = r"(?:\([^()\s]*\)|[^()\s])*"
_LINKED_IMAGE_RE = re.compile(
    rf"\[!\[([^\]\n]*)\]\(({_MD_URL})\)\]\({_LINK_TARGET}\)"
)
# One `<img>` tag. GitHub bodies mix HTML into markdown freely — an <img>
# with a width= is the usual way to shrink a screenshot — and the markdown
# pass never looked at tags at all, so today they render as literal text.
# The length bound keeps a pathological near-tag cheap to reject.
_IMG_TAG_RE = re.compile(r"<img\b[^<>]{0,1000}>", re.I)
_ATTR_RE = re.compile(r"""([A-Za-z-]{1,20})\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s'">]+))""")
# What an `<img src>` must look like to be fetched: markdown's own http(s)
# rule, restated for a value that arrives quoted rather than parenthesized.
_HTML_SRC_RE = re.compile(r"^https?://[^\s'\"<>]+$")


# What a preview's cut must not land inside: an inline code span, a link or
# image, a bold run, an autolink or a bare URL. Half of any of these renders
# literal — "[Show" or "https://exa…" — so a cut that would split one moves
# back to where it starts instead.
_INLINE_SPAN_RE = re.compile(
    rf"`[^`\n]+`|!?\[[^\]\n]*\]\({_MD_URL}\)|\*\*[^*\n]+\*\*|<https?://[^>\s]+>|https?://\S+"
)


def body_head(text: str, chars: int | None, lines: int | None) -> tuple[str, bool]:
    """The front of *text* that fits the *chars* / *lines* budgets, and
    whether that is all of it — the preview a folded PR description shows.

    Whole lines while the character budget lasts; the line that would
    overrun it is cut mid-way to what is left of the budget, at a word
    boundary and never inside an inline markdown span (`_INLINE_SPAN_RE`),
    so a preview neither renders half a link literally nor ends on a
    broken word. That cut happens wherever the overrunning line falls: a
    body that opens with a heading and then one long paragraph — the
    ``## Why`` shape most PR descriptions here take — gets the paragraph's
    front, not a preview that is the heading alone. A budget of None is
    no budget on that axis.
    """
    if (chars is None or len(text) <= chars) and (lines is None or text.count("\n") < lines):
        return text, True
    kept: list[str] = []
    remaining = chars
    for line in text.split("\n")[:lines]:
        if remaining is not None and len(line) > remaining:
            front = _cut_line(line, remaining)
            if front:
                kept.append(front)
            break
        kept.append(line)
        if remaining is not None:
            remaining -= len(line) + 1  # the newline joining it to the next
    return "\n".join(kept), False


def _cut_line(line: str, budget: int) -> str:
    """The first *budget* characters of *line*, backed off to a word
    boundary and out of any inline span the cut would have split."""
    cut = max(budget, 0)
    if cut < len(line) and not line[cut].isspace():
        space = max(line.rfind(" ", 0, cut), line.rfind("\t", 0, cut))
        if space > 0:
            cut = space
    for match in _INLINE_SPAN_RE.finditer(line):
        if match.start() >= cut:
            break
        if match.end() > cut:
            cut = match.start()
            break
    return line[:cut].rstrip()


def split_body(text: str) -> list[str | tuple[BodyImage, ...]]:
    """A markdown body split into text runs and rows of images.

    Text runs come back verbatim, for `md_to_pango` to render exactly as it
    would have; images come out as `BodyImage` tuples, one tuple per row —
    images separated by nothing but spaces belong to one line in the source
    (a row of badges, a before/after pair) and should stay on one line on
    screen. A body with no images comes back as the single string it was,
    so the common case costs one substring search.

    Images inside code — fenced or inline — are left in their text run:
    a body showing someone *how to write* an image is not embedding one.
    The whitespace that only ever separated an image from the text around
    it goes away with the split, since each row is its own widget with its
    own spacing.
    """
    if "![" not in text and "<img" not in text.lower():
        return [text]
    protected = [m.span() for m in _FENCE_RE.finditer(text)]
    protected += [m.span() for m in _INLINE_CODE_RE.finditer(text)]
    found = []
    for regex, parse in (
        (_LINKED_IMAGE_RE, _md_image),
        (_IMAGE_RE, _md_image),
        (_IMG_TAG_RE, _html_image),
    ):
        for match in regex.finditer(text):
            image = parse(match)
            if image is not None:
                found.append((match.start(), match.end(), image))
    # Longest first at a given start, so a linked image beats the plain one
    # nested inside it; anything overlapping something already taken is that
    # nested match, and is dropped.
    found.sort(key=lambda item: (item[0], -item[1]))
    taken: list[tuple[int, int, BodyImage]] = []
    consumed = 0
    for start, stop, image in found:
        if start < consumed or _protected(protected, start, stop):
            continue
        if len(taken) >= MAX_BODY_IMAGES:
            break
        taken.append((start, stop, image))
        consumed = stop
    if not taken:
        return [text]
    parts: list[str | BodyImage] = []
    at = 0
    for start, stop, image in taken:
        parts.append(text[at:start])
        parts.append(image)
        at = stop
    parts.append(text[at:])
    return _rows(parts)


def _rows(parts: list[str | BodyImage]) -> list[str | tuple[BodyImage, ...]]:
    """Alternating text/image parts as text runs and image rows: images the
    source kept on one line join one row, and text that was only the
    whitespace around an image drops out."""
    out: list[str | tuple[BodyImage, ...]] = []
    joinable = False  # did the run just passed leave us on the same line?
    for part in parts:
        if isinstance(part, BodyImage):
            if joinable and out and isinstance(out[-1], tuple):
                out[-1] = out[-1] + (part,)
            else:
                out.append((part,))
            joinable = True
            continue
        joinable = part.strip(" \t") == ""
        # The whitespace at a run's edges only ever separated it from an
        # image, and each row is its own widget with its own spacing now —
        # a run that was nothing but that separation drops out entirely.
        chunk = part.strip()
        if chunk:
            out.append(chunk)
    return out


def _protected(spans: list[tuple[int, int]], start: int, stop: int) -> bool:
    return any(span_start < stop and start < span_stop for span_start, span_stop in spans)


def _md_image(match: re.Match) -> BodyImage | None:
    """`![alt](url)` (or its linked form) as a BodyImage; the pattern has
    already held the URL to http(s)."""
    url = match.group(2)
    if len(url) > _MAX_URL:
        return None
    return BodyImage(url=url, alt=match.group(1).strip())


def _html_image(match: re.Match) -> BodyImage | None:
    """An `<img>` tag as a BodyImage, or None when its src isn't a URL we
    can fetch — a relative path, a `data:` blob, a stray tag with no src."""
    src = alt = width = ""
    for attr in _ATTR_RE.finditer(match.group(0)):
        name = attr.group(1).lower()
        value = attr.group(2) or attr.group(3) or attr.group(4) or ""
        if name == "src" and not src:
            src = value
        elif name == "alt" and not alt:
            alt = value
        elif name == "width" and not width:
            width = value
    # An HTML attribute carries its URL escaped; & is the one that matters
    # (query strings are full of it) and the one GitHub itself writes.
    url = src.strip().replace("&amp;", "&")
    if len(url) > _MAX_URL or not _HTML_SRC_RE.match(url):
        return None
    return BodyImage(url=url, alt=alt.strip(), width=_width(width))


def _width(value: str) -> int:
    """A `width=` attribute as pixels, or 0 for "as big as it comes".

    Shrinking a screenshot with `<img width=>` is the one bit of layout a
    GitHub body can ask for, and the ask is worth honoring — but only when
    it is a plain pixel count in a sane range. A percentage means something
    relative to a page width Collins doesn't have, and a huge value is an
    author asking to be scaled *up*, which no screenshot survives.
    """
    try:
        pixels = int(value.strip())
    except ValueError:
        return 0
    return pixels if 1 <= pixels <= 4_000 else 0


def _blockquote_markup(m: re.Match) -> str:
    """One quoted line: its > markers become dimmed quote bars.

    One bar per nesting level, and the text rides inside the same dim span —
    quoted text is someone else's words, held a step back from the reply
    around it.
    """
    bars = "▎" * m.group(2).count("&gt;")
    return f'{m.group(1)}<span alpha="60%">{bars} {m.group(3)}</span>'


def _renumber_lists(text: str) -> str:
    """Ordered lists count as rendered, not as written — GitHub's rule.

    Markdown authors write ``1.`` all the way down (or drift after edits);
    renderers count from the first item. Each run of consecutive numbered
    lines at one indent renumbers from its first item's value, so ``1. 1.
    1.`` shows as 1. 2. 3.; a line that isn't a numbered item ends the run.
    Marker style (``.`` or ``)``) stays each line's own.
    """
    out = []
    run_indent: str | None = None
    counter = 0
    for line in text.split("\n"):
        m = _NUMBERED_RE.match(line)
        if m is None:
            run_indent = None
        elif m.group(1) != run_indent:
            run_indent = m.group(1)
            counter = int(m.group(2))
        else:
            counter += 1
        if m is not None:
            line = f"{m.group(1)}{counter}{m.group(3)} {m.group(4)}"
        out.append(line)
    return "\n".join(out)


def display_path(path: str) -> str:
    """A directory path as shown in the UI: the home prefix collapsed to ~."""
    home = str(Path.home())
    if path == home:
        return "~"
    if path.startswith(home + "/"):
        return "~" + path[len(home) :]
    return path


def format_tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def format_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size} B"


def format_timestamp(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ts


def format_relative(ts: str | None, now: datetime | None = None) -> str:
    """A timestamp as a short age — "5m ago", "3h ago" — a date once it's old.

    How the PR view stamps its cards: a review's age is what the reader wants
    while the conversation is live, and past a month the date says more than
    a large day count would. *now* is injectable for tests; anything
    unparseable comes back as it was, like `format_timestamp`.
    """
    if not ts:
        return "—"
    try:
        then = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    now = now if now is not None else datetime.now(timezone.utc)
    seconds = int((now - then).total_seconds())
    if seconds < 60:
        return _("just now")
    if seconds < 3600:
        return _("{n}m ago").format(n=seconds // 60)
    if seconds < 86400:
        return _("{n}h ago").format(n=seconds // 3600)
    if seconds < 30 * 86400:
        return _("{n}d ago").format(n=seconds // 86400)
    return then.astimezone().strftime("%Y-%m-%d")


# Projects named in the "delete archived sessions" confirmation: list them all
# while the list is short, and once it isn't, name only the biggest few and sum
# the rest on one line — enough to see the damage, and the same readable dialog
# whether 3 projects are affected or 300.
_BLAST_RADIUS_MAX = 7  # list every project up to this many
_BLAST_RADIUS_ROWS = 4  # named once the list is capped


def blast_radius_body(total: int, breakdown: list[tuple[str, int, int]]) -> str:
    """Spell out what "all archived sessions" actually means before the user
    commits to it: how many transcripts, spread over which projects, and how
    many of those lose *every* session they have — every row the sidebar keeps
    out of sight, not only the ones archived by hand. Archiving is cheap and
    accumulates, so the pile is usually far bigger than it feels, and a project
    that loses everything is gone from the sidebar unless it's kept.

    `breakdown` is store.archived_breakdown(): (project, archived, total)
    biggest first. A short list is named in full; a long one is cut to the
    biggest few with the rest summed on one line, so the dialog stays a
    readable size with hundreds of projects archived.

    Which projects get named goes by session count, but the lines are then
    ordered shortest first. AdwAlertDialog centres its body, and a centred
    column of ragged lines is hard to read in any order — sorted by length it
    at least tapers evenly instead of jumping about.
    """
    lines = [
        _(
            "{n} session(s) in {p} project(s) have their transcripts moved to "
            "the trash, where they can be restored. Sessions archived with "
            "their whole project — and originals a backgrounded fork replaced "
            "— are included."
        ).format(n=total, p=len(breakdown))
    ]
    if len(breakdown) <= _BLAST_RADIUS_MAX:
        shown, rest = breakdown, []
    else:
        shown, rest = breakdown[:_BLAST_RADIUS_ROWS], breakdown[_BLAST_RADIUS_ROWS:]
    named = [
        _("{project} — {n} of {total}").format(project=name, n=count, total=project_total)
        for name, count, project_total in shown
    ]
    lines.append("")
    lines += sorted(named, key=lambda line: (len(line), line))
    if rest:
        # Always last: it stands for everything the named lines left out.
        lines.append(
            _("…and {p} other project(s) — {n} session(s)").format(
                p=len(rest), n=sum(count for _name, count, _total in rest)
            )
        )
    emptied = sum(1 for _name, count, project_total in breakdown if count >= project_total)
    if emptied:
        lines.append("")
        lines.append(_("{p} of these project(s) lose every session they have.").format(p=emptied))
    return "\n".join(lines)
