"""The link grammars the terminals hover-highlight and Ctrl+click-open.

VTE compiles URL_PATTERN and FILE_PATTERN with PCRE2; these tests exercise
the same patterns with Python's `re`, which the patterns deliberately
restrict themselves to the shared syntax of (see collins/linkpatterns.py).
"""

import re

import pytest

from collins.linkpatterns import FILE_PATTERN, URL_PATTERN, resolve_file_reference

_RX = re.compile(URL_PATTERN)
_FILE_RX = re.compile(FILE_PATTERN)


def _first_match(text: str) -> str | None:
    m = _RX.search(text)
    return m.group(0) if m else None


def _first_file_match(text: str) -> str | None:
    m = _FILE_RX.search(text)
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


# -- FILE_PATTERN ----------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("open collins/terminal.py please", "collins/terminal.py"),
        ("at collins/terminal.py:152 there", "collins/terminal.py:152"),
        ("at collins/terminal.py:152:8 there", "collins/terminal.py:152:8"),
        ("see /etc/hosts for that", "/etc/hosts"),
        ("wrote /tmp/shot.png:12 out", "/tmp/shot.png:12"),
        ("notes in ~/notes/todo.md", "~/notes/todo.md"),
        ("run ./scripts/run first", "./scripts/run"),
        ("or ../sibling/file.txt instead", "../sibling/file.txt"),
        # Reference at the very start of a line (the lookbehind must accept
        # having no character before the match at all).
        ("collins/app.py:3 changed", "collins/app.py:3"),
    ],
)
def test_matches_file_references(text: str, expected: str) -> None:
    assert _first_file_match(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        # Same ending discipline as URLs: sentence punctuation stays outside,
        # with or without a line suffix.
        ("(see collins/foo.py).", "collins/foo.py"),
        ("fixed collins/foo.py:12.", "collins/foo.py:12"),
        ("in `collins/foo.py:3` above", "collins/foo.py:3"),
        ("edit 'collins/foo.py', then", "collins/foo.py"),
        ("[a](collins/foo.py)", "collins/foo.py"),
        ("dir collins/subdir/ listed", "collins/subdir"),
        ("really /var/log/syslog?", "/var/log/syslog"),
    ],
)
def test_file_reference_sheds_trailing_punctuation(text: str, expected: str) -> None:
    assert _first_file_match(text) == expected


def test_prose_slashes_match_as_candidates() -> None:
    # Over-matching prose is fine by design: the filesystem check in
    # resolve_file_reference is the real gate, and `a/b` resolves nowhere.
    assert _first_file_match("either a/b or both") == "a/b"


@pytest.mark.parametrize(
    "text",
    [
        "no path here",
        "bare terminal.py stays inert",
        "colon:separated:words",
        # URLs must keep matching as URLs, never as file references — the
        # slash-bearing tails of these must not produce a file match.
        "https://example.com/a/b",
        "http://example.com/a/b?q=1",
        "file:///home/user/notes.txt",
        "ftp://host/file.tar.gz",
        "visit www.example.com today",
    ],
)
def test_non_paths_do_not_match(text: str) -> None:
    assert _first_file_match(text) is None


def test_urls_still_match_as_urls() -> None:
    for text in ("https://example.com/a/b", "file:///home/user/notes.txt"):
        assert _first_match(text) == text


# -- resolve_file_reference ------------------------------------------------


@pytest.fixture()
def project(tmp_path):
    (tmp_path / "collins").mkdir()
    (tmp_path / "collins" / "foo.py").write_text("print()\n")
    return tmp_path


def test_resolve_relative_against_root(project) -> None:
    assert resolve_file_reference("collins/foo.py", [str(project)]) == (
        str(project / "collins" / "foo.py"),
        None,
        None,
    )


def test_resolve_strips_line_and_col(project) -> None:
    root = str(project)
    path = str(project / "collins" / "foo.py")
    assert resolve_file_reference("collins/foo.py:12", [root]) == (path, 12, None)
    assert resolve_file_reference("collins/foo.py:12:5", [root]) == (path, 12, 5)


def test_resolve_absolute_ignores_roots(project) -> None:
    path = str(project / "collins" / "foo.py")
    assert resolve_file_reference(f"{path}:7", []) == (path, 7, None)


def test_resolve_tries_roots_in_order(tmp_path) -> None:
    first = tmp_path / "worktree"
    second = tmp_path / "project"
    for root in (first, second):
        (root / "collins").mkdir(parents=True)
        (root / "collins" / "foo.py").touch()
    resolved = resolve_file_reference(
        "collins/foo.py", [str(first), str(second)]
    )
    assert resolved == (str(first / "collins" / "foo.py"), None, None)


def test_resolve_skips_none_roots(project) -> None:
    resolved = resolve_file_reference("collins/foo.py", [None, str(project)])
    assert resolved == (str(project / "collins" / "foo.py"), None, None)


def test_resolve_expands_home(project, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(project))
    assert resolve_file_reference("~/collins/foo.py", []) == (
        str(project / "collins" / "foo.py"),
        None,
        None,
    )


def test_resolve_normalizes_dot_segments(project) -> None:
    resolved = resolve_file_reference("./collins/foo.py", [str(project)])
    assert resolved == (str(project / "collins" / "foo.py"), None, None)


def test_resolve_finds_directories(project) -> None:
    # Directories resolve too — the click path sends them to the file
    # manager instead of the editor.
    assert resolve_file_reference("collins", [str(project)]) == (
        str(project / "collins"),
        None,
        None,
    )


def test_resolve_prefers_line_suffix_over_literal_colon_name(project) -> None:
    literal = project / "collins" / "foo.py:1"
    literal.touch()
    resolved = resolve_file_reference("collins/foo.py:1", [str(project)])
    assert resolved == (str(project / "collins" / "foo.py"), 1, None)


def test_resolve_falls_back_to_literal_colon_name(project) -> None:
    literal = project / "collins" / "weird:2"
    literal.touch()
    resolved = resolve_file_reference("collins/weird:2", [str(project)])
    assert resolved == (str(literal), None, None)


def test_resolve_missing_returns_none(project) -> None:
    assert resolve_file_reference("collins/nope.py", [str(project)]) is None
    assert resolve_file_reference("collins/nope.py:3", [str(project)]) is None
