"""What counts as a link in terminal output.

Kept free of GTK/VTE imports so the patterns stay unit-testable on CI, which
has no VTE stack (see tests/conftest.py). Two grammars, each deliberately
simpler than GNOME Terminal's terminal-regex.h:

- ``URL_PATTERN``: a scheme'd URL or a bare www. host.
- ``FILE_PATTERN``: path-shaped text — absolute, ``~/``, or relative with at
  least one slash, with Claude Code's optional ``:line[:col]`` suffix. A
  path regex necessarily over-matches prose (``a/b``, dates, package
  paths), so a hit is only a *candidate*: ``resolve_file_reference`` checks
  the filesystem at click time, and a candidate that resolves nowhere is
  ignored — the click falls through to the terminal. Bare filenames
  (``terminal.py``) are deliberately out of the grammar; without a slash
  the false-positive rate in ordinary prose is too high.
- ``bare_names_pattern``: the one sound way back in for bare filenames — an
  alternation of *known* names (the entries actually sitting at a tab's
  project root), so the hover underline can only ever land on a name that
  exists. Built per terminal at runtime; see terminal._RootNameLinks.

Both grammars stop at whitespace, quotes and brackets, and refuse to *end*
on punctuation that prose tends to hang off a reference — so
``(https://a.b/c).`` matches just ``https://a.b/c`` and
``collins/foo.py:12.`` keeps the final period out of the line suffix.

The patterns stick to syntax PCRE2 and Python's `re` share: VTE compiles
them with PCRE2 at runtime, the tests exercise them with `re`.
"""

import os
import re
from collections.abc import Iterable

# One body-then-final-char pair per alternative: the greedy body backtracks
# until the last character is something a URL can plausibly end on.
_BODY = "[^\\s<>\"']*"
_FINAL = "[^\\s<>\"'.,:;!?)\\]}]"

URL_PATTERN = f"(?:https?|ftp|file)://{_BODY}{_FINAL}|www\\.{_BODY}{_FINAL}"

# Paths bound tighter than URLs: brackets, backticks and quotes all end them
# (paths with spaces can't be bounded by a regex at all and stay out of
# scope), and `:` is reserved for the line suffix. The lookbehind demands a
# boundary character — or the start of the line — before the match. That
# same lookbehind keeps this grammar out of URLs: inside `https://a.b/c`
# every path-shaped start is preceded by `:`, `/` or a word character, none
# of which are boundaries, so URLs keep matching as URLs only.
_PATH_BOUNDARY = "\\s<>\"'`()\\[\\]{}"
_PATH_PRE = f"(?<![^{_PATH_BOUNDARY}])"
_PATH_CHAR = f"[^{_PATH_BOUNDARY}:]"
_PATH_SEG = f"[^{_PATH_BOUNDARY}:/]"
_PATH_FINAL = f"[^{_PATH_BOUNDARY}:.,;!?/]"
_LINE_SUFFIX = "(?::\\d+(?::\\d+)?)?"

FILE_PATTERN = (
    f"{_PATH_PRE}"
    f"(?:~?/{_PATH_CHAR}*{_PATH_FINAL}"  # absolute, or ~/ home-relative
    # Relative with >= 1 slash. The tail is optional so a bare directory
    # reference (`collins/`) matches too; the absolute alternative keeps a
    # mandatory tail so a lone `/` in prose never becomes a link to the
    # filesystem root.
    f"|{_PATH_SEG}+/(?:{_PATH_CHAR}*{_PATH_FINAL})?)"
    f"{_LINE_SUFFIX}"
)

# Names the bare grammar can't take: whitespace can't be bounded by a regex
# at all, `:` would collide with the line suffix, and `/` belongs to the
# slashed grammar above.
_BARE_UNBOUNDABLE = re.compile("[\\s:/]")


def bare_names_pattern(names: Iterable[str]) -> str | None:
    """A FILE_PATTERN companion matching any of *names* as a bare token.

    Bare filenames stay out of FILE_PATTERN because a *shape* can't help
    over-matching prose — but an alternation of literal names underlines
    only what genuinely exists, so the usual objection disappears. Same
    boundary discipline as paths: a boundary (or line start) before, the
    optional ``:line[:col]`` suffix after, and the token must not continue
    with a character a path could end on — ``README.md.`` sheds its period
    while ``README.mdx`` never half-matches an entry ``README.md``. Longest
    name first, so an entry extending another wins the alternation
    (``README.md.bak`` before ``README.md``). None when nothing survives
    the unboundable-name filter: no regex to register at all.
    """
    usable = sorted(
        {name for name in names if name and not _BARE_UNBOUNDABLE.search(name)},
        key=lambda name: (-len(name), name),
    )
    if not usable:
        return None
    alternatives = "|".join(re.escape(name) for name in usable)
    return f"{_PATH_PRE}(?:{alternatives}){_LINE_SUFFIX}(?!{_PATH_FINAL})"


_SUFFIX = re.compile(r"(.+?):(\d+)(?::(\d+))?")
_FILE_RX = re.compile(FILE_PATTERN)

# The characters FILE_PATTERN refuses to *end* a match on (_PATH_FINAL's
# exclusions). When a wrap falls right after one of them — a row ending
# `…/collins/` — the match candidate comes back without it, so the
# end-of-row stitch gate must tolerate a trailing run of exactly these.
_SHED_CHARS = ":.,;!?/"

# How many rows a hard-wrapped reference may be stitched across, per
# direction. Agent CLIs wrap long paths over two rows, occasionally three;
# beyond that the joins are more likely to be accidental than real.
_STITCH_ROWS_UP = 2
_STITCH_ROWS_DOWN = 3


def resolve_file_reference(
    text: str, roots: list[str | None]
) -> tuple[str, int | None, int | None] | None:
    """The file a FILE_PATTERN candidate actually points at, or None.

    The regex is only a shape detector; this is the false-positive gate the
    click runs. Strips the ``:line[:col]`` suffix (preferring that reading
    over a literal filename containing colons), expands ``~``, and tries
    relative paths against each of *roots* in order, skipping None entries.
    Returns ``(path, line, col)`` with line/col as the reference wrote them
    (1-based) or None where it carried no suffix.
    """
    candidates: list[tuple[str, int | None, int | None]] = []
    m = _SUFFIX.fullmatch(text)
    if m is not None:
        candidates.append(
            (m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None)
        )
    candidates.append((text, None, None))
    for raw, line, col in candidates:
        expanded = os.path.expanduser(raw)
        if os.path.isabs(expanded):
            trials = [expanded]
        else:
            trials = [os.path.join(root, expanded) for root in roots if root]
        for trial in trials:
            if os.path.exists(trial):
                return os.path.normpath(trial), line, col
    return None


def token_at_column(text: str, col: int) -> str | None:
    """The whitespace-delimited token covering column *col* of a screen row,
    or None over whitespace / past the end of the text.

    A wrapped reference's continuation fragment frequently contains no slash
    (`o.py:7)`), so it matches nothing and offers no click candidate at all —
    yet it is the half holding the file *name*, the natural place to click.
    The raw token under the pointer stands in as the candidate; the stitcher's
    geometry gates and existence check keep arbitrary prose tokens inert.
    """
    if col < 0 or col >= len(text) or text[col].isspace():
        return None
    start = col
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    end = col + 1
    while end < len(text) and not text[end].isspace():
        end += 1
    return text[start:end]


def resolve_wrapped_reference(
    candidate: str,
    row_text: str,
    rows_above: list[str],
    rows_below: list[str],
    roots: list[str | None],
) -> tuple[str, int | None, int | None] | None:
    """A candidate that resolved nowhere may be a fragment of a reference the
    *emitter* hard-wrapped — a real newline plus continuation indent in the
    output, which no regex over screen text can see past.

    The stitch is geometry-gated: fragments are only joined downward when the
    candidate sits at the very end of its row, and upward when it sits at the
    start (after indent) — the two signatures of a wrapped token. Neighbour
    rows contribute their adjacent whitespace-delimited token, chaining
    further only while a whole row was one token (a middle fragment).
    *rows_above*/*rows_below* are nearest-first. Every join is re-matched
    against FILE_PATTERN and then existence-checked like any other candidate,
    so a stitch that guesses wrong still opens nothing.
    """
    row = row_text.rstrip("\n")
    downs = [""]
    row_r = row.rstrip()
    trail = None
    if row_r.endswith(candidate):
        trail = ""
    else:
        # A wrap that falls right after a character the pattern sheds
        # (`…/collins/` ⏎ `linkpatterns.py`) leaves the candidate short of
        # the row end; the shed run is part of the reference, so it seeds
        # the downward join.
        core = row_r.rstrip(_SHED_CHARS)
        if core != row_r and core.endswith(candidate):
            trail = row_r[len(core):]
    if trail is not None:
        chain = trail
        for below in rows_below[:_STITCH_ROWS_DOWN]:
            frag = below.strip()
            if not frag:
                break
            token = frag.split()[0]
            chain += token
            downs.append(chain)
            if token != frag:
                break
    ups = [""]
    if row.lstrip().startswith(candidate):
        chain = ""
        for above in rows_above[:_STITCH_ROWS_UP]:
            frag = above.strip()
            if not frag:
                break
            token = frag.split()[-1]
            chain = token + chain
            ups.append(chain)
            if token != frag:
                break
    for up in reversed(ups):  # longest joins first
        for down in reversed(downs):
            if not up and not down:
                continue  # the bare candidate already failed
            joined = up + candidate + down
            # The emitter wraps whatever surrounds the path along with it,
            # so a contributed token — or a token-derived candidate — often
            # arrives glued to a prefix: `Read(/a/b/c` is Claude Code's own
            # tool-call format. Searching (rather than an anchored match)
            # sheds that junk wherever it sits; the span guards keep only
            # hits that overlap the clicked fragment itself, so a join never
            # resolves text the click didn't touch.
            for m in _FILE_RX.finditer(joined):
                if m.end() <= len(up):
                    continue
                if m.start() >= len(up) + len(candidate):
                    break
                resolved = resolve_file_reference(m.group(0), roots)
                if resolved is not None:
                    return resolved
    return None
