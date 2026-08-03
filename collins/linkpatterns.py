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

Both grammars stop at whitespace, quotes and brackets, and refuse to *end*
on punctuation that prose tends to hang off a reference — so
``(https://a.b/c).`` matches just ``https://a.b/c`` and
``collins/foo.py:12.`` keeps the final period out of the line suffix.

The patterns stick to syntax PCRE2 and Python's `re` share: VTE compiles
them with PCRE2 at runtime, the tests exercise them with `re`.
"""

import os
import re

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

_SUFFIX = re.compile(r"(.+?):(\d+)(?::(\d+))?")


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
