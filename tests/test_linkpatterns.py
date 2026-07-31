"""The URL grammar the terminals hover-highlight and Ctrl+click-open.

VTE compiles URL_PATTERN with PCRE2; these tests exercise the same pattern
with Python's `re`, which the pattern deliberately restricts itself to the
shared syntax of (see collins/linkpatterns.py).
"""

import re

import pytest

from collins.linkpatterns import URL_PATTERN

_RX = re.compile(URL_PATTERN)


def _first_match(text: str) -> str | None:
    m = _RX.search(text)
    return m.group(0) if m else None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("see https://example.com for docs", "https://example.com"),
        ("http://example.com/a/b?q=1&r=2#frag", "http://example.com/a/b?q=1&r=2#frag"),
        ("ftp://host/file.tar.gz", "ftp://host/file.tar.gz"),
        ("file:///home/user/notes.txt", "file:///home/user/notes.txt"),
        ("visit www.example.com today", "www.example.com"),
    ],
)
def test_matches_common_urls(text: str, expected: str) -> None:
    assert _first_match(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        # Punctuation that prose hangs off the end of a link stays outside it.
        ("read (https://example.com/docs).", "https://example.com/docs"),
        ("Really, https://example.com/a, then more", "https://example.com/a"),
        ("is it https://example.com?", "https://example.com"),
        ("[link](https://example.com/p)", "https://example.com/p"),
        ("quote 'https://example.com/q' end", "https://example.com/q"),
    ],
)
def test_trailing_punctuation_is_not_part_of_the_url(text: str, expected: str) -> None:
    assert _first_match(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "no links here",
        "not-a-scheme//example.com",
        "watch example.com without a scheme or www",
    ],
)
def test_plain_text_does_not_match(text: str) -> None:
    assert _first_match(text) is None


def test_url_stops_at_whitespace() -> None:
    assert _first_match("https://a.example/x https://b.example/y") == "https://a.example/x"
