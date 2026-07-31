"""What counts as a link in terminal output.

Kept free of GTK/VTE imports so the pattern stays unit-testable on CI, which
has no VTE stack (see tests/conftest.py). The grammar is deliberately simpler
than GNOME Terminal's terminal-regex.h: a scheme'd URL or a bare www. host,
stopping at whitespace, quotes and angle brackets, and refusing to *end* on
punctuation that prose tends to hang off a link — so `(https://a.b/c).`
matches just `https://a.b/c`.

The pattern sticks to syntax PCRE2 and Python's `re` share: VTE compiles it
with PCRE2 at runtime, the tests exercise it with `re`.
"""

# One body-then-final-char pair per alternative: the greedy body backtracks
# until the last character is something a URL can plausibly end on.
_BODY = "[^\\s<>\"']*"
_FINAL = "[^\\s<>\"'.,:;!?)\\]}]"

URL_PATTERN = f"(?:https?|ftp|file)://{_BODY}{_FINAL}|www\\.{_BODY}{_FINAL}"
