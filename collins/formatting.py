# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-10. Full change history: git log for this file.

"""Small human-readable formatting helpers shared across the UI."""

from __future__ import annotations

import re
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
        return "~" + path[len(home):]
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
        return (
            datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
        )
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
        _("{n} session(s) in {p} project(s) have their transcripts moved to "
          "the trash, where they can be restored. Sessions archived with "
          "their whole project — and originals a backgrounded fork replaced "
          "— are included.").format(n=total, p=len(breakdown))
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
        lines.append(
            _("{p} of these project(s) lose every session they have.").format(p=emptied)
        )
    return "\n".join(lines)
