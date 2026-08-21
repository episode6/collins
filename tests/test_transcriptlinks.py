"""The transcript-backed link completion behind the screen stitchers
(collins/transcriptlinks.py): what it harvests from a JSONL tail, and which
harvested links the screen around a click is allowed to corroborate."""

import json

from collins import transcriptlinks
from collins.transcriptlinks import completions, harvest_links, transcript_links

URL = "https://github.com/episode6/collins/pull/303/files#diff-abc"
PATH = "collins/linkpatterns.py:319"


def _screen(*rows: str) -> list[str]:
    return list(rows)


# -- harvesting ----------------------------------------------------------------


def test_harvest_takes_urls_and_paths_once_each_in_first_seen_order():
    texts = [f"see {URL} and {PATH}.", f"again {URL}", "plain prose"]
    assert harvest_links(texts) == [URL, PATH]


def _entry(kind: str, content) -> str:
    return json.dumps({"type": kind, "message": {"role": kind, "content": content}})


def test_transcript_links_reads_text_blocks_tool_inputs_and_results(tmp_path):
    jsonl = tmp_path / "s.jsonl"
    lines = [
        _entry("user", f"open {URL}"),
        _entry(
            "assistant",
            [
                {"type": "text", "text": f"Edited {PATH}"},
                {"type": "tool_use", "name": "Bash", "input": {"command": "curl https://x.test/a"}},
            ],
        ),
        _entry("user", [{"type": "tool_result", "content": [{"type": "text", "text": "at /etc/hosts"}]}]),
        json.dumps({"type": "pr-link", "url": "https://github.com/not/from/a/message"}),
        "not json at all",
    ]
    jsonl.write_text("\n".join(lines) + "\n")
    assert transcript_links(jsonl) == [URL, PATH, "https://x.test/a", "/etc/hosts"]


def test_transcript_links_escaped_newline_does_not_extend_a_url(tmp_path):
    jsonl = tmp_path / "s.jsonl"
    jsonl.write_text(_entry("assistant", [{"type": "text", "text": "https://a.test/b\nnext line"}]) + "\n")
    assert transcript_links(jsonl) == ["https://a.test/b"]


def test_transcript_links_reads_only_the_tail_and_skips_its_partial_line(tmp_path, monkeypatch):
    jsonl = tmp_path / "s.jsonl"
    early = _entry("user", "https://early.test/gone")
    late = _entry("user", "https://late.test/kept")
    jsonl.write_text(early + "\n" + late + "\n")
    monkeypatch.setattr(transcriptlinks, "TAIL_BYTES", len(late) + 1 + 10)  # cuts `early` mid-line
    transcriptlinks._cache.clear()
    assert transcript_links(jsonl) == ["https://late.test/kept"]


def test_transcript_links_caches_per_size_and_mtime(tmp_path):
    jsonl = tmp_path / "s.jsonl"
    jsonl.write_text(_entry("user", "https://one.test/") + "\n")
    transcriptlinks._cache.clear()
    first = transcript_links(jsonl)
    assert transcript_links(jsonl) is first
    with jsonl.open("a") as fh:
        fh.write(_entry("user", "https://two.test/") + "\n")
    assert transcript_links(jsonl) == ["https://one.test/", "https://two.test/"]


def test_transcript_links_missing_file_is_empty(tmp_path):
    assert transcript_links(tmp_path / "nope.jsonl") == []


# -- completion -----------------------------------------------------------------


def test_head_fragment_completes_from_the_row_below():
    rows = _screen(
        "⏺ PR is at https://github.com/episode6/collins/pull/303/fil",
        "  es#diff-abc — review requested.",
    )
    frag = "https://github.com/episode6/collins/pull/303/fil"
    assert completions(frag, rows, 0, 20, [URL]) == [("url", URL)]


def test_tail_fragment_completes_from_the_row_above():
    rows = _screen(
        "⏺ PR is at https://github.com/episode6/collins/pull/303/fil",
        "  es#diff-abc — review requested.",
    )
    assert completions("es#diff-abc", rows, 1, 4, [URL]) == [("url", URL)]


def test_middle_fragment_spans_three_rows():
    rows = _screen(
        "  https://github.com/episode6/col",
        "  lins/pull/303/fi",
        "  les#diff-abc",
    )
    assert completions("lins/pull/303/fi", rows, 1, 5, [URL]) == [("url", URL)]


def test_the_row_below_decides_between_links_sharing_a_head():
    rows = _screen("https://github.com/episode6/collins/pull/3", "03/files")
    links = [
        "https://github.com/episode6/collins/pull/31",
        "https://github.com/episode6/collins/pull/303/files",
        "https://github.com/episode6/collins/pull/303",
    ]
    frag = "https://github.com/episode6/collins/pull/3"
    assert completions(frag, rows, 0, 5, links) == [
        ("url", "https://github.com/episode6/collins/pull/303/files"),
        ("url", "https://github.com/episode6/collins/pull/303"),
    ]


def test_longest_corroborated_first_with_shorter_behind_it():
    rows = _screen("see collins/linkpatterns.py:3", "19:7 for it")
    links = ["collins/linkpatterns.py:319:7", "collins/linkpatterns.py:319", "collins/linkpatterns.py"]
    assert completions("collins/linkpatterns.py:3", rows, 0, 6, links) == [
        ("file", "collins/linkpatterns.py:319:7"),
        ("file", "collins/linkpatterns.py:319"),
    ]  # the bare path is shorter than the fragment: not a completion of it


def test_a_link_alone_on_its_row_with_a_word_below_shares_the_geometry_exposure():
    # Both links are in the transcript and the screen genuinely reads
    # `a` ⏎ `bc`; the transcript can't settle that any better than the
    # geometry could. Documented, not fixed.
    rows = _screen("https://example.com/a", "bc is the next word")
    links = ["https://example.com/abc", "https://example.com/a"]
    assert completions("https://example.com/a", rows, 0, 3, links) == [("url", "https://example.com/abc")]


def test_prose_that_is_not_part_of_the_link_on_screen_gets_nothing():
    rows = _screen("https://example.com/abc", "the next line")
    assert completions("the", rows, 1, 1, ["https://example.com/xthe", "https://other.test/the"]) == []


def test_fragment_must_be_a_proper_part_of_the_link():
    rows = _screen("see https://example.com/a now")
    assert completions("https://example.com/a", rows, 0, 6, ["https://example.com/a"]) == []


def test_fragment_absent_from_its_row_or_off_screen_gets_nothing():
    rows = _screen("nothing here")
    assert completions("https://x", rows, 0, 0, ["https://x/y"]) == []
    assert completions("https://x", rows, 5, 0, ["https://x/y"]) == []
    assert completions("", rows, 0, 0, ["https://x/y"]) == []


def test_the_occurrence_under_the_pointer_positions_the_fragment():
    # Two `pull/3` tokens on one row; only the second runs onto the next.
    rows = _screen("pull/3 then https://h.test/pull/3", "03/x")
    links = ["https://h.test/pull/303/x"]
    assert completions("https://h.test/pull/3", rows, 0, 20, links) == [("url", links[0])]


def test_context_reach_is_bounded():
    rows = ["  x"] * 6 + ["https://h.test/a"] + ["  " + "b" * 3] * 6
    far = "https://h.test/a" + "bbb" * 6
    assert completions("https://h.test/a", rows, 6, 2, [far]) == []
    near = "https://h.test/a" + "bbb" * 4
    assert completions("https://h.test/a", rows, 6, 2, [near]) == [("url", near)]
