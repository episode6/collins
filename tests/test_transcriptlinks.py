"""The transcript-backed link completion behind the screen stitchers
(collins/transcriptlinks.py): what it harvests from a JSONL tail, which
harvested links the screen around a click is allowed to corroborate, and the
request that asks the service for them (the file itself is read by
service/transcripttail.py, tests/test_transcripttail.py)."""

import json

from collins import transcriptlinks
from collins.api import protocol
from collins.api.protocol import RequestRefused
from collins.transcriptlinks import completions, harvest_links, message_strings

URL = "https://github.com/episode6/collins/pull/303/files#diff-abc"
PATH = "collins/linkpatterns.py:319"


def _screen(*rows: str) -> list[str]:
    return list(rows)


# -- harvesting ----------------------------------------------------------------


def test_harvest_takes_urls_and_paths_once_each_in_first_seen_order():
    texts = [f"see {URL} and {PATH}.", f"again {URL}", "plain prose"]
    assert harvest_links(texts) == [URL, PATH]


def test_message_strings_take_text_blocks_tool_inputs_and_results():
    data = "\n".join(
        [
            json.dumps({"type": "user", "message": {"role": "user", "content": f"open {URL}"}}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "text", "text": f"Edited {PATH}"},
                            {"type": "tool_use", "name": "Bash", "input": {"command": "curl https://x.test/a"}},
                        ]
                    },
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": [{"type": "tool_result", "content": [{"text": "at /etc/hosts"}]}]},
                }
            ),
            json.dumps({"type": "pr-link", "url": "https://github.com/not/from/a/message"}),
            "not json at all",
        ]
    ).encode()
    texts = message_strings(data)
    assert harvest_links(texts) == [URL, PATH, "https://x.test/a", "/etc/hosts"]


def test_an_escaped_newline_does_not_extend_a_url():
    data = json.dumps({"type": "assistant", "message": {"content": "https://a.test/b\nnext line"}}).encode()
    assert harvest_links(message_strings(data)) == ["https://a.test/b"]


def test_fetch_asks_the_service_for_the_sessions_links(monkeypatch):
    asked: list[dict] = []

    def call(message, timeout=None):
        asked.append(message)
        return {"links": [URL, 7, PATH]}

    monkeypatch.setattr(transcriptlinks.apilink, "call", call)
    assert transcriptlinks.fetch("abc") == [URL, PATH]
    assert asked == [{"t": "store.transcript-tail", "session": "abc"}]


def test_fetch_is_empty_when_the_service_refuses(monkeypatch):
    def call(message, timeout=None):
        raise RequestRefused(protocol.ERROR_GONE, "Not connected", {})

    monkeypatch.setattr(transcriptlinks.apilink, "call", call)
    assert transcriptlinks.fetch("abc") == []


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


def test_spaces_within_a_row_are_not_removed():
    # Two whole tokens with a space between them never glue into a link the
    # transcript happens to hold; only row-edge whitespace is a wrap.
    rows = _screen("see a/b c/d here")
    assert completions("a/b", rows, 0, 4, ["a/bc/d"]) == []
    rows = _screen("see https://h.test/a b/c", "d")
    assert completions("https://h.test/a", rows, 0, 6, ["https://h.test/ab/cd"]) == []


def test_only_row_edge_whitespace_joins_rows():
    rows = _screen("  text https://h.test/a   ", "    b/c and more")
    assert completions("https://h.test/a", rows, 0, 9, ["https://h.test/ab/c"]) == [("url", "https://h.test/ab/c")]
