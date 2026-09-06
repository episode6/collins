"""mdblocks: the PR page's markdown block layer, GTK-free.

Golden trees for every block type from the spec's revalidation fixture,
plus the safety properties every markup field must hold: well-formed,
escaped, http(s)-only hrefs, the linkify post-filter, the depth cap, and
the fallback latch.
"""

import re

import pytest
from gi.repository import GLib

from collins import mdblocks
from collins.formatting import MAX_BODY_IMAGES, BodyImage, markup_ok
from collins.mdblocks import (
    CodeBlock,
    Details,
    Heading,
    ImageRow,
    ListBlock,
    ListItem,
    Quote,
    Rule,
    Table,
    Text,
    parse_blocks,
    render_inline,
)

pytestmark = pytest.mark.skipif(not mdblocks.available(), reason="markdown-it-py not installed")

FIXTURE = """# Title

Setext
------

- [ ] todo
- [x] done
  - nested *em* item
3. three
4. four

> [!NOTE]
> a note
> more

> plain quote
>
> ```py
> x = 1
> ```

| a | b |
|:--|--:|
| 1 | 2 |

```bash
echo hi
```

    indented

***

<details open>
<summary>More</summary>
raw line

hidden **body**

</details>

text <kbd>Ctrl</kbd> H<sub>2</sub>O<br>next <img src="https://x/y.png" width=100> and <b>bold</b>

See example.com and www.foo.org and https://bar.io and me@x.com and [ref][1]
and <https://auto.link> and [rel](docs/a.md) and [js](javascript:alert(1))

![alt](https://img/1.png) ![alt2](https://img/2.png)
![alt3](https://img/3.png)

[1]: https://ref.example
"""


def walk(blocks):
    for block in blocks:
        yield block
        if isinstance(block, (Quote, Details)):
            yield from walk(block.children)
        elif isinstance(block, ListBlock):
            for item in block.items:
                yield from walk(item.children)


def markups(blocks):
    for block in walk(blocks):
        if isinstance(block, (Text, Heading)):
            yield block.markup
        elif isinstance(block, Table):
            yield from block.header
            for row in block.rows:
                yield from row


# -- the preset --------------------------------------------------------------------


def test_preset_is_gfm_like_on_every_supported_version():
    # gfm-like2 exists only on markdown-it-py 4.1+; Ubuntu ships 3.0.0.
    assert mdblocks.PRESET == "gfm-like"
    assert "gfm-like2" not in open(mdblocks.__file__).read().replace("``gfm-like2``", "")


def test_fallback_latch_when_the_parser_is_missing(monkeypatch):
    monkeypatch.setattr(mdblocks, "_md", None)
    monkeypatch.setattr(mdblocks, "_import_error", "ImportError: no markdown_it")
    assert not mdblocks.available()
    with pytest.raises(RuntimeError):
        parse_blocks("hello")
    with pytest.raises(RuntimeError):
        render_inline("hello")


# -- golden trees ------------------------------------------------------------------


def test_headings_atx_and_setext():
    blocks = parse_blocks(FIXTURE)
    assert blocks[0] == Heading(1, "Title", "# Title")
    assert blocks[1] == Heading(2, "Setext", "Setext\n------")


def test_task_list_detector_and_nesting():
    todo = parse_blocks(FIXTURE)[2]
    assert isinstance(todo, ListBlock) and not todo.ordered and todo.start == 1
    assert todo.items[0] == ListItem((Text("todo", "todo"),), check=False)
    done = todo.items[1]
    assert done.check is True
    assert done.children[0] == Text("done", "done")
    nested = done.children[1]
    assert isinstance(nested, ListBlock)
    assert nested.items[0].children[0].markup == "nested <i>em</i> item"
    assert nested.items[0].check is None


@pytest.mark.parametrize(
    "line, check, text",
    [
        ("- [ ] todo", False, "todo"),
        ("- [x] done", True, "done"),
        ("- [X] done", True, "done"),
        ("- [ ]", False, ""),
        ("- [ ]todo", None, "[ ]todo"),
        ("- [y] no", None, "[y] no"),
        ("- text [ ] later", None, "text [ ] later"),
    ],
)
def test_task_marker_shapes(line, check, text):
    (block,) = parse_blocks(line)
    item = block.items[0]
    assert item.check is check
    assert (item.children[0].markup if item.children else "") == text


def test_task_marker_only_on_an_items_first_paragraph():
    (block,) = parse_blocks("- one\n\n  [ ] two")
    assert block.items[0].check is None
    assert block.items[0].children[1].markup == "[ ] two"


def test_ordered_list_start_and_none_means_one():
    ordered = parse_blocks(FIXTURE)[3]
    assert ordered == ListBlock(
        True,
        3,
        (ListItem((Text("three", "three"),)), ListItem((Text("four", "four"),))),
        "3. three\n4. four\n",
    )
    (from_one,) = parse_blocks("1. a\n1. b")
    assert from_one.start == 1


def test_alert_quote_drops_its_marker():
    note = parse_blocks(FIXTURE)[4]
    assert note.kind == "note"
    assert note.children == (Text("a note\nmore", "a note\nmore"),)


@pytest.mark.parametrize("body", ["> [!note]\n> x", "> [!NOTE] x", "> x\n> [!NOTE]", "> [!OTHER]\n> x"])
def test_not_an_alert_stays_a_plain_quote(body):
    (quote,) = parse_blocks(body)
    assert quote.kind == "plain"
    assert "[!" in quote.children[0].markup


def test_alert_marker_alone_leaves_an_empty_titled_quote():
    (quote,) = parse_blocks("> [!TIP]")
    assert quote.kind == "tip"
    assert quote.children == ()


def test_plain_quote_with_a_fence_inside():
    quote = parse_blocks(FIXTURE)[5]
    assert quote.kind == "plain"
    assert quote.children[0] == Text("plain quote", "plain quote")
    assert quote.children[1] == CodeBlock("x = 1\n", "py", "> ```py\n> x = 1\n> ```")


def test_table_alignment_and_cells():
    table = parse_blocks(FIXTURE)[6]
    assert table == Table(("left", "right"), ("a", "b"), (("1", "2"),), "| a | b |\n|:--|--:|\n| 1 | 2 |")
    (plain,) = parse_blocks("| a |\n|---|\n| **b** |")
    assert plain.aligns == (None,)
    assert plain.rows == (("<b>b</b>",),)


def test_fences_and_indented_code():
    blocks = parse_blocks(FIXTURE)
    assert blocks[7] == CodeBlock("echo hi\n", "bash", "```bash\necho hi\n```")
    assert blocks[8] == CodeBlock("indented\n", None, "    indented")
    (fence,) = parse_blocks("```Python extra words\nx\n```")
    assert fence.lang == "python"


def test_rule():
    assert parse_blocks(FIXTURE)[9] == Rule("***")


def test_details_with_summary_in_the_open_block():
    details = parse_blocks(FIXTURE)[10]
    assert isinstance(details, Details)
    assert details.summary == "More"
    assert details.open is True
    assert details.children == (
        Text("raw line", "raw line"),
        Text("hidden <b>body</b>", "hidden **body**"),
    )
    assert details.source.startswith("<details open>") and details.source.endswith("</details>")


def test_details_with_a_blank_line_before_the_summary():
    (details,) = parse_blocks("<details>\n\n<summary>Later <b>x</b></summary>\n\nbody\n\n</details>")
    assert details.summary == "Later x"
    assert details.open is False
    assert details.children == (Text("body", "body"),)


def test_unmatched_details_renders_literal():
    blocks = parse_blocks("<details>\n<summary>No close</summary>\n\nrest")
    assert blocks[0] == Text("&lt;details&gt;\n&lt;summary&gt;No close&lt;/summary&gt;",
                             "<details>\n<summary>No close</summary>")
    assert blocks[1] == Text("rest", "rest")


def test_nested_details_close_matches_its_own_open():
    (outer,) = parse_blocks(
        "<details>\n<summary>A</summary>\n\n<details>\n<summary>B</summary>\n\ninner\n\n</details>\n\nouter\n\n</details>"
    )
    assert outer.summary == "A"
    inner = outer.children[0]
    assert isinstance(inner, Details) and inner.summary == "B"
    assert outer.children[1] == Text("outer", "outer")


def test_inline_html_whitelist():
    text = parse_blocks(FIXTURE)[11]
    assert text.markup == (
        "text <tt>Ctrl</tt> H<sub>2</sub>O\nnext "
        '<a href="https://x/y.png">https://x/y.png</a> and &lt;b&gt;bold&lt;/b&gt;'
    )


def test_links_linkify_filter_and_gates():
    text = parse_blocks(FIXTURE)[12]
    assert "See example.com and" in text.markup  # fuzzy linkify, refused
    assert '<a href="http://www.foo.org">www.foo.org</a>' in text.markup
    assert '<a href="https://bar.io">https://bar.io</a>' in text.markup
    assert "me@x.com" in text.markup and "mailto" not in text.markup
    assert '<a href="https://ref.example">ref</a>' in text.markup  # reference-style
    assert '<a href="https://auto.link">https://auto.link</a>' in text.markup
    assert " rel " in text.markup and "docs/a.md" not in text.markup  # relative: text alone
    assert "javascript" in text.markup and "<a" not in text.markup.split("auto.link</a>")[1]


def test_image_rows_merge_on_a_line_and_split_on_a_break():
    blocks = parse_blocks(FIXTURE)
    assert blocks[13] == ImageRow(
        (BodyImage("https://img/1.png", "alt"), BodyImage("https://img/2.png", "alt2")),
        blocks[13].source,
    )
    assert blocks[14].images == (BodyImage("https://img/3.png", "alt3"),)
    assert len(blocks) == 15


def test_html_img_alone_on_its_line_is_an_image_row():
    # A tag on a line of its own is a CommonMark HTML *block*, not inline HTML.
    (row,) = parse_blocks('<img src="https://x/y.png" width="120" alt="shot">')
    assert row == ImageRow((BodyImage("https://x/y.png", "shot", 120),), row.source)


def test_html_img_block_lines_split_into_rows_and_literal_text():
    body = (
        '<img src="https://x/a.png"> <img src="https://x/b.png">\n'
        '<img src="https://x/c.png">\n'
        "<p>stray</p>\n"
        '<img src="rel.png">'
    )
    blocks = parse_blocks(body)
    assert [type(b).__name__ for b in blocks] == ["ImageRow", "ImageRow", "Text", "Text"]
    assert len(blocks[0].images) == 2 and len(blocks[1].images) == 1
    assert blocks[2].markup == "&lt;p&gt;stray&lt;/p&gt;"
    assert blocks[3].markup == '&lt;img src=&quot;rel.png&quot;&gt;'


def test_html_img_block_with_images_off_is_an_anchor():
    (text,) = parse_blocks('<img src="https://x/y.png" alt="shot">', images=False)
    assert text.markup == '<a href="https://x/y.png">shot</a>'


def test_html_img_inline_beside_text_is_an_anchor():
    (text,) = parse_blocks('a <img src="https://x/y.png" alt="shot"> b')
    assert text.markup == 'a <a href="https://x/y.png">shot</a> b'


def test_image_with_text_beside_it_degrades_to_an_anchor():
    (text,) = parse_blocks("see ![shot](https://x/y.png) here")
    assert text.markup == 'see <a href="https://x/y.png">shot</a> here'


def test_images_off_makes_anchors_not_rows():
    (text,) = parse_blocks("![shot](https://x/y.png)", images=False)
    assert text == Text('<a href="https://x/y.png">shot</a>', "![shot](https://x/y.png)")


def test_image_cap_holds_per_body():
    body = "\n\n".join(f"![i{n}](https://x/{n}.png)" for n in range(MAX_BODY_IMAGES + 3))
    blocks = parse_blocks(body)
    rows = [b for b in blocks if isinstance(b, ImageRow)]
    assert sum(len(r.images) for r in rows) == MAX_BODY_IMAGES
    texts = [b for b in blocks if isinstance(b, Text)]
    assert len(texts) == 3 and all("<a href=" in t.markup for t in texts)


def test_non_http_image_is_its_alt_text():
    (text,) = parse_blocks("![local](docs/a.png) and ![data](data:image/png;base64,AAAA)")
    assert text.markup == "local and data"


# -- safety properties -------------------------------------------------------------


def test_every_markup_is_well_formed_and_escaped():
    for markup in markups(parse_blocks(FIXTURE)):
        assert markup_ok(markup), markup
        assert not re.search(r"<(?!/?(?:a|b|i|s|tt|sub|sup|span)\b)", markup), markup


def test_text_is_escaped():
    (text,) = parse_blocks("a < b & c > d <script>x</script>")
    assert text.markup == "a &lt; b &amp; c &gt; d &lt;script&gt;x&lt;/script&gt;"


@pytest.mark.parametrize(
    "body",
    [
        "[x](javascript:alert(1))",
        "[x](data:text/html,hi)",
        "[x](ftp://host/f)",
        "[x](mailto:a@b)",
        "[x](/relative)",
        "<a href='https://x'>x</a>",
    ],
)
def test_hrefs_are_http_s_only(body):
    for markup in markups(parse_blocks(body)):
        for href in re.findall(r'href="([^"]*)"', markup):
            assert href.startswith(("http://", "https://")), markup


def test_href_is_escaped():
    (text,) = parse_blocks("[q](https://x/?a=1&b=2)")
    assert text.markup == '<a href="https://x/?a=1&amp;b=2">q</a>'


def test_stray_closing_html_is_literal_and_open_tags_are_closed():
    (text,) = parse_blocks("a</sub> **b <sup>c** d")
    assert markup_ok(text.markup)
    assert "&lt;/sub&gt;" in text.markup
    assert text.markup == "a&lt;/sub&gt; <b>b <sup>c</sup></b> d"


def test_html_close_before_markdown_close_stays_well_formed():
    (text,) = parse_blocks("<sub>a **b</sub> c** d")
    assert markup_ok(text.markup)


def test_depth_cap_flattens_to_source():
    body = "> " * (mdblocks.MAX_DEPTH + 1) + "deep"
    (outer,) = parse_blocks(body)
    block = outer
    for _level in range(mdblocks.MAX_DEPTH - 1):
        assert isinstance(block, Quote), block
        block = block.children[0]
    assert isinstance(block, Quote)
    (leaf,) = block.children
    assert isinstance(leaf, Text)
    # The source is the document's own lines, outer markers included.
    assert leaf.source == body and leaf.markup == GLib.markup_escape_text(body)


def test_deep_lists_flatten_too():
    body = "\n".join("  " * n + "- item" for n in range(mdblocks.MAX_DEPTH + 3))
    blocks = parse_blocks(body)
    assert any(isinstance(b, Text) and b.source.strip().startswith("- item") for b in walk(blocks))
    for markup in markups(blocks):
        assert markup_ok(markup)


def test_unknown_html_block_is_literal():
    (text,) = parse_blocks("<table><tr><td>x</td></tr></table>")
    assert text.markup == "&lt;table&gt;&lt;tr&gt;&lt;td&gt;x&lt;/td&gt;&lt;/tr&gt;&lt;/table&gt;"


def test_strong_soft_nesting_no_longer_blanks():
    (text,) = parse_blocks("**strong *soft***")
    assert text.markup == "<b>strong <i>soft</i></b>"


# -- render_inline and line_cost -----------------------------------------------------


def test_render_inline_reuses_the_walker():
    assert render_inline("a **b** `c` [d](https://e) ![f](https://g/h.png)") == (
        'a <b>b</b> <tt>c</tt> <a href="https://e">d</a> <a href="https://g/h.png">f</a>'
    )
    assert render_inline("") == ""


def test_line_costs():
    blocks = parse_blocks(FIXTURE)
    costs = {type(b).__name__: mdblocks.line_cost(b) for b in blocks}
    assert costs["Heading"] == 1 and costs["Rule"] == 1 and costs["Details"] == 2
    assert mdblocks.line_cost(blocks[2]) == 3  # todo, done, nested
    assert mdblocks.line_cost(blocks[6]) == 2  # header + 1 row
    assert mdblocks.line_cost(blocks[13], image_lines=4) == 4
    assert mdblocks.line_cost(Text("a\nb\nc", "a\nb\nc")) == 3
    long_code = CodeBlock("\n".join(["x"] * 30), None, "")
    assert mdblocks.line_cost(long_code) == 8
    big_table = Table((), (), tuple(("x",) for _ in range(50)), "")
    assert mdblocks.line_cost(big_table) == 6


def test_empty_body_is_no_blocks():
    assert parse_blocks("") == []
    assert parse_blocks("\n\n  \n") == []
