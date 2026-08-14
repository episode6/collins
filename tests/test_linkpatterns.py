"""The link grammars the terminals hover-highlight and Ctrl+click-open.

VTE compiles URL_PATTERN and FILE_PATTERN with PCRE2; these tests exercise
the same patterns with Python's `re`, which the patterns deliberately
restrict themselves to the shared syntax of (see collins/linkpatterns.py).
"""

import re

import pytest

from collins.linkpatterns import (
    FILE_PATTERN,
    URL_PATTERN,
    bare_names_pattern,
    resolve_file_reference,
    resolve_wrapped_reference,
    resolve_wrapped_url,
    token_at_column,
)

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
        # A bare directory reference keeps its structural slash — that slash
        # is what makes it a candidate at all.
        ("the collins/ package", "collins/"),
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


# -- bare_names_pattern ------------------------------------------------------

_ROOT_NAMES = ["README.md", "pyproject.toml", "LICENSE", ".gitignore", "start-debug"]


def _first_bare_match(text: str, names: list[str] = _ROOT_NAMES) -> str | None:
    pattern = bare_names_pattern(names)
    assert pattern is not None
    m = re.search(pattern, text)
    return m.group(0) if m else None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("open README.md please", "README.md"),
        # At the very start of a line, like FILE_PATTERN's lookbehind.
        ("README.md changed", "README.md"),
        ("at README.md:12 there", "README.md:12"),
        ("at README.md:12:5 there", "README.md:12:5"),
        ("see `pyproject.toml` for deps", "pyproject.toml"),
        ("read (LICENSE) first", "LICENSE"),
        ("a hidden .gitignore too", ".gitignore"),
        ("run start-debug now", "start-debug"),
    ],
)
def test_bare_names_match(text: str, expected: str) -> None:
    assert _first_bare_match(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        # The same ending discipline as the other grammars.
        ("update the README.md.", "README.md"),
        ("fixed README.md:12.", "README.md:12"),
        ("README.md, then LICENSE", "README.md"),
        ("is it README.md?", "README.md"),
        ("quote 'LICENSE' end", "LICENSE"),
    ],
)
def test_bare_name_sheds_trailing_punctuation(text: str, expected: str) -> None:
    assert _first_bare_match(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # A known name must never half-match inside a longer token, in
        # either direction.
        "a README.mdx variant",
        "xREADME.md glued on",
        "the README.md-old backup",
        # Slashed references are the path grammar's territory; the bare
        # grammar must stay out (the lookbehind sees the slash).
        "docs/README.md is a path",
        "no names here at all",
    ],
)
def test_bare_name_boundaries(text: str) -> None:
    assert _first_bare_match(text) is None


def test_longer_entry_wins_over_its_prefix() -> None:
    # Sorting inside bare_names_pattern, not caller order, decides: the
    # alternation must try README.md.bak before README.md either way.
    for names in (["README.md", "README.md.bak"], ["README.md.bak", "README.md"]):
        assert _first_bare_match("see README.md.bak", names) == "README.md.bak"


def test_names_with_regex_metacharacters_match_literally() -> None:
    assert _first_bare_match("open note[1]+x.md now", ["note[1]+x.md"]) == "note[1]+x.md"
    # Unescaped, `[1]+` would read as a repeated class and admit this text;
    # the literal must not.
    assert _first_bare_match("open note11x.md now", ["note[1]+x.md"]) is None


def test_unboundable_names_are_filtered() -> None:
    # Whitespace can't be bounded, `:` collides with the line suffix, `/`
    # belongs to the path grammar; nothing usable means no pattern at all.
    assert bare_names_pattern([]) is None
    assert bare_names_pattern(["with space.txt", "a:b", "a/b", ""]) is None
    # ...and unusable names don't poison the usable rest.
    assert _first_bare_match("see README.md", ["with space.txt", "README.md"]) == "README.md"


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
    # manager instead of the editor. With or without the trailing slash a
    # bare directory reference matches with (normpath sheds it).
    expected = (str(project / "collins"), None, None)
    assert resolve_file_reference("collins", [str(project)]) == expected
    assert resolve_file_reference("collins/", [str(project)]) == expected


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


# -- resolve_wrapped_reference ---------------------------------------------
#
# An emitter that hard-wraps output splits a long reference across real
# lines; each on-screen fragment matches FILE_PATTERN on its own but
# resolves nowhere. The stitcher joins a failed fragment with its neighbour
# rows' adjacent tokens, gated on the fragment touching its row's edge.


def _split(path: str, at: int) -> tuple[str, str]:
    return path[:at], path[at:]


def test_stitch_downward_from_first_fragment(project) -> None:
    path = str(project / "collins" / "foo.py")
    head, tail = _split(path, 20)
    resolved = resolve_wrapped_reference(
        head, f"  see {head}", [], [f"  {tail} and more prose"], []
    )
    assert resolved == (path, None, None)


def test_stitch_upward_from_continuation_fragment(project) -> None:
    path = str(project / "collins" / "foo.py")
    head, tail = _split(path, 20)
    resolved = resolve_wrapped_reference(
        tail, f"  {tail} and more prose", [f"  see {head}"], [], []
    )
    assert resolved == (path, None, None)


def test_stitch_three_rows_from_middle_fragment(project) -> None:
    path = str(project / "collins" / "foo.py")
    head, rest = _split(path, 15)
    middle, tail = _split(rest, 10)
    resolved = resolve_wrapped_reference(
        middle, f"  {middle}", [f"wrote {head}"], [f"  {tail}, done."], []
    )
    assert resolved == (path, None, None)


def test_stitch_keeps_line_suffix_on_continuation(project) -> None:
    path = str(project / "collins" / "foo.py")
    head, tail = _split(path, 20)
    resolved = resolve_wrapped_reference(
        head, f"  {head}", [], [f"  {tail}:12, then"], []
    )
    assert resolved == (path, 12, None)


def test_no_stitch_when_fragment_is_mid_row(project) -> None:
    path = str(project / "collins" / "foo.py")
    head, tail = _split(path, 20)
    # The fragment has text after it on its own row, so it never wrapped —
    # the row below must not be pulled in even though joining would resolve.
    resolved = resolve_wrapped_reference(
        head, f"  {head} trailing words", [], [f"  {tail}"], []
    )
    assert resolved is None


def test_stitch_that_resolves_nowhere_returns_none(project) -> None:
    resolved = resolve_wrapped_reference(
        "collins/nope", "  collins/nope", [], ["  .py either"], [str(project)]
    )
    assert resolved is None


def test_prose_at_row_start_is_not_poisoned_by_row_above(project) -> None:
    # `collins/foo.py` at the start of its row resolves directly and never
    # reaches the stitcher; a *failing* start-of-row fragment tries the row
    # above, and the bogus join just fails resolution.
    resolved = resolve_wrapped_reference(
        "collins/nope.py", "  collins/nope.py here", ["ends with word"], [], [str(project)]
    )
    assert resolved is None


def test_stitch_upward_sheds_prefix_glued_to_head(project) -> None:
    # Claude Code's tool-call format wraps as `⏺ Read(/…/fo` + `o.py)`: the
    # row above contributes a token with `Read(` still attached, which must
    # be shed for the join to match.
    path = str(project / "collins" / "foo.py")
    head, tail = _split(path, 20)
    resolved = resolve_wrapped_reference(
        tail, f"      {tail})", [f"⏺ Read({head}"], [], []
    )
    assert resolved == (path, None, None)


def test_stitch_slashless_token_candidate(project) -> None:
    # The continuation fragment of a wrapped path often has no slash at all
    # (`o.py:7)`), so it is never a FILE_PATTERN match — the raw token under
    # the pointer stands in as the candidate and still stitches, keeping the
    # line suffix and shedding the trailing paren.
    path = str(project / "collins" / "foo.py")
    head, tail = _split(path, len(path) - 4)  # tail = "o.py", no slash
    resolved = resolve_wrapped_reference(
        f"{tail}:7)", f"      {tail}:7)", [f"⏺ Read({head}"], [], []
    )
    assert resolved == (path, 7, None)


def test_stitch_must_reach_into_the_candidate(project) -> None:
    # A token candidate leading with a boundary character (`("x`) must not
    # resolve to whatever existing path the row above happened to end with —
    # the join has to cover text the click actually touched.
    above = f"see {project / 'collins'}"
    resolved = resolve_wrapped_reference('("x', '("x', [above], [], [])
    assert resolved is None


def test_stitch_token_candidate_with_glued_prefix(project) -> None:
    # Clicking the head fragment of `⏺ Update(collins` + `/foo.py)`: the head
    # has no slash so it matches nothing, and the token under the pointer
    # arrives with `Update(` glued on. The search sheds it and the join still
    # resolves.
    resolved = resolve_wrapped_reference(
        "Update(collins", "⏺ Update(collins", [], ["      /foo.py)"], [str(project)]
    )
    assert resolved == (str(project / "collins" / "foo.py"), None, None)


def test_standalone_reference_ignores_deceptive_neighbours(project) -> None:
    # A reference alone on its row opens BOTH edge gates (the trimmed row
    # equals the candidate), so the stitcher does probe its neighbours — but
    # every join is a non-existent path, the stitcher returns None, and the
    # click handler's fallback resolves the reference directly. Pins down
    # that active neighbours can't poison a good standalone reference.
    resolved = resolve_wrapped_reference(
        "collins/foo.py",
        "  collins/foo.py",
        ["prose above ending in .claude", "more prose"],
        ["  bar.txt below", "  and more"],
        [str(project)],
    )
    assert resolved is None
    assert resolve_file_reference("collins/foo.py", [str(project)]) == (
        str(project / "collins" / "foo.py"),
        None,
        None,
    )


def test_stitch_downward_across_directory_boundary(project) -> None:
    # The wrap can fall right after a slash: the row ends `…/collins/` but
    # FILE_PATTERN refuses to end a match on `/`, so the candidate arrives
    # without it. The shed slash must still join the fragments — without it
    # the click "resolves" to the parent directory instead of the file.
    path = str(project / "collins" / "foo.py")
    head = str(project / "collins") + "/"
    candidate = str(project / "collins")  # what the pattern hands the handler
    resolved = resolve_wrapped_reference(
        candidate, f"● {head} ", [], ["  foo.py and prose"], []
    )
    assert resolved == (path, None, None)


def test_stitch_downward_across_hidden_dir_boundary(project) -> None:
    # Same shape, wrap after `/.` (start of a dotfile/dot-directory): two
    # shed characters seed the join.
    (project / "collins" / ".hidden").mkdir()
    (project / "collins" / ".hidden" / "bar.py").write_text("x\n")
    path = str(project / "collins" / ".hidden" / "bar.py")
    candidate = str(project / "collins")
    resolved = resolve_wrapped_reference(
        candidate, f"● {candidate}/. ", [], ["  hidden/bar.py:9, done"], []
    )
    assert resolved == (path, 9, None)


def test_prose_hanging_punctuation_does_not_false_join(project) -> None:
    # `collins/foo.py, and` — the comma is prose, not a wrap; the shed-run
    # gate opens but the join `foo.py,and` resolves nowhere and the click
    # falls back to the direct resolution of the candidate itself.
    resolved = resolve_wrapped_reference(
        "collins/foo.py", "  see collins/foo.py,", [], ["  and more"], [str(project)]
    )
    assert resolved is None


# -- resolve_wrapped_url ---------------------------------------------------
#
# The same wrap, one row's worth of URL at a time. A path fragment that
# stitches wrong opens nothing; a URL fragment that stitches wrong opens a
# browser, so here the geometry alone has to tell a wrap from a line break —
# see _break_was_forced.

_WRAP = 60
_URL = "https://github.com/episode6/collins/pull/303/files#diff-abc123"
# Long enough to fill a screen row of its own with room to spare.
_LONG_URL = "https://example.com/" + "segment/" * 12 + "end?id=42"


def test_url_stitches_downward_from_the_head_fragment() -> None:
    head, tail = _split("⏺ Opened " + _URL, _WRAP)
    candidate = head[len("⏺ Opened ") :]
    assert resolve_wrapped_url(candidate, head, [], ["  " + tail + " —"], _WRAP) == _URL


def test_ink_style_wrap_a_column_short_is_still_a_full_row() -> None:
    # Claude Code's renderer breaks at the column before the last and leaves
    # a trailing space there, so the caller's terminal-width guess at the
    # wrap column always overshoots by a little.
    head, tail = _split("⏺ Opened " + _URL, _WRAP - 1)
    candidate = head[len("⏺ Opened ") :]
    assert resolve_wrapped_url(candidate, head + " ", [], ["  " + tail], _WRAP) == _URL


def test_url_stitches_upward_from_the_continuation_fragment() -> None:
    head, tail = _split("⏺ Opened " + _URL, _WRAP)
    row = "  " + tail + " for review"
    assert resolve_wrapped_url(tail, row, [head], [], _WRAP) == _URL


def test_url_stitches_across_a_row_it_fills_by_itself() -> None:
    first, rest = _split("⏺ " + _LONG_URL, _WRAP)
    middle, tail = _split(rest, _WRAP)
    assert len(middle) == _WRAP and " " not in middle  # a middle fragment
    below = [middle, "  " + tail + " — done."]
    assert resolve_wrapped_url(first[2:], first, [], below, _WRAP) == _LONG_URL
    # ...and from the far end, reaching back over the same middle row.
    above = [middle, first]
    assert resolve_wrapped_url(tail, "  " + tail + " — done.", above, [], _WRAP) == _LONG_URL


def test_url_stitch_crosses_a_shed_character() -> None:
    url = "https://example.com/docs/guide.html"
    row = "⏺ See " + url[:31]  # the wrap fell right after `guide.`
    candidate = url[:30]  # ...which the pattern refuses to end a match on
    assert resolve_wrapped_url(candidate, row, [], ["  html, then"], len(row)) == url


def test_bare_host_url_stitches_in_its_own_form() -> None:
    url = "www.example.com/docs/getting-started/index.html"
    head, tail = _split(url, 40)
    row = "⏺ See " + head
    # No scheme is invented here; the click handler prefixes http:// as it
    # does for any www. match.
    assert resolve_wrapped_url(head, row, [], ["  " + tail], len(row)) == url


def test_url_stitch_sheds_a_glued_tool_call_prefix() -> None:
    url = "https://example.com/a/very/long/reference/page?id=42"
    head, tail = _split(url, 40)
    above = "⏺ Fetch(" + head
    row = "  " + tail + ")"
    assert resolve_wrapped_url(tail + ")", row, [above], [], len(above)) == url


def test_url_alone_on_its_row_keeps_the_prose_below_out() -> None:
    # The commonest shape of all: a link on its own line, the next sentence
    # under it. The row stopped well short of the wrap column, so nothing
    # broke a token there and there is nothing to stitch.
    url = "https://example.com/pr/303"
    row = "  " + url
    assert resolve_wrapped_url(url, row, [], ["  Ready for review."], _WRAP) is None
    assert resolve_wrapped_url("Ready", "  Ready for review.", [row], [], _WRAP) is None


def test_a_word_too_long_for_the_row_below_is_still_not_a_wrap() -> None:
    # The row below opens with a word that could not have fitted above — but
    # that is what an ordinary word wrap does too, so it says nothing. Only
    # the unfilled row above does, and it says the link ended there.
    url = "https://example.com/pr/303"
    row = "⏺ Opened " + url  # 25 columns short of the wrap
    below = ["  Documentation-and-review follows."]
    assert resolve_wrapped_url(url, row, [], below, _WRAP) is None


def test_a_row_below_bearing_its_own_scheme_is_a_separate_link() -> None:
    head, _tail = _split("⏺ Opened " + _URL, _WRAP)
    candidate = head[len("⏺ Opened ") :]
    below = ["  https://example.com/other/page"]
    assert resolve_wrapped_url(candidate, head, [], below, _WRAP) is None


def test_a_word_takes_nothing_from_the_link_above_it() -> None:
    # Upward the scheme comes from the neighbour, not from what was clicked,
    # so a full row ending in a finished link sits one row above every prose
    # word — and `y` must not become `https://another.com/xy`.
    above = "⏺ Opened https://another.com/x"
    for word in ("y", "page", "Ready", "review."):
        row = word + " and more text"
        assert resolve_wrapped_url(word, row, [above], [], len(above)) is None
    # The row above bearing a scheme is not itself the objection: that is
    # exactly what a genuine head fragment does, and a tail that carries
    # anything a word doesn't still stitches.
    assert resolve_wrapped_url("2/files", "2/files and more", [above], [], len(above)) == (
        "https://another.com/x2/files"
    )


def test_no_url_stitch_when_the_fragment_is_mid_row() -> None:
    head, tail = _split("⏺ Opened " + _URL, _WRAP)
    candidate = head[len("⏺ Opened ") :]
    row = head + " and more"
    assert resolve_wrapped_url(candidate, row, [], ["  " + tail], _WRAP) is None


def test_url_stitch_stops_at_a_blank_row() -> None:
    head, tail = _split("⏺ Opened " + _URL, _WRAP)
    candidate = head[len("⏺ Opened ") :]
    assert resolve_wrapped_url(candidate, head, [], ["", "  " + tail], _WRAP) is None


def test_url_stitch_that_adds_nothing_returns_none() -> None:
    # A neighbour joins on, but no URL match reaches across the seam — the
    # click opens what it matched rather than a re-derived same-again.
    url = "https://example.com/pr/303"
    row = "⏺ Opened " + url
    assert resolve_wrapped_url(url, row, [], ["  ' and prose"], len(row)) is None


def test_token_at_column() -> None:
    text = "  wrote o.py:7) done"
    assert token_at_column(text, 8) == "o.py:7)"
    assert token_at_column(text, 14) == "o.py:7)"
    assert token_at_column(text, 1) is None  # whitespace
    assert token_at_column(text, 99) is None  # past the end
    assert token_at_column(text, -1) is None
    assert token_at_column(text, 17) == "done"
