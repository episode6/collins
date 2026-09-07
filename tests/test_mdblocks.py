"""mdblocks: the PR page's markdown block layer, GTK-free.

Golden trees for every block type from the spec's revalidation fixture,
plus the safety properties every markup field must hold: well-formed,
escaped, http(s)-only hrefs, the linkify post-filter, the depth cap, and
the fallback latch.
"""

import re
import time

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
    RepoContext,
    Rule,
    Table,
    Text,
    link_refs,
    link_www,
    parse_blocks,
    relative_href,
    render_inline,
    repo_context,
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


def test_details_closed_inside_its_own_block_is_literal():
    blocks = parse_blocks("<details><summary>a</summary></details>\n\ntext\n\n</details>")
    assert [type(b) for b in blocks] == [Text, Text, Text]
    assert blocks[0].source == "<details><summary>a</summary></details>"
    assert blocks[2] == Text("&lt;/details&gt;", "</details>")


def test_details_pairing_is_one_pass():
    # 9 000 unmatched openers is what prdetail's 100 000-char cap lets
    # through; a scan per opener took ~24 s on the main loop.
    import time

    body = "<details>\n\n" * 9000
    started = time.perf_counter()
    blocks = parse_blocks(body)
    assert time.perf_counter() - started < 3.0
    assert len(blocks) == 9000 and all(isinstance(b, Text) for b in blocks)


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


def test_fuzzy_linkify_is_off_and_www_autolinks_are_linear():
    # The preset's fuzzy linkify (bare domains, emails) is quadratic per
    # paragraph: 50 KB of `www.a.com ` took 6 s, 20 KB of `a@b.com ` 4 s,
    # on the main loop. Off, the same parse in milliseconds; `link_www`
    # puts the `www.` autolinks back in one linear pass.
    started = time.perf_counter()
    (text,) = parse_blocks("www.a.com " * 5000)
    (mail,) = parse_blocks("a@b.com " * 2500)
    assert time.perf_counter() - started < 2.0
    assert text.markup.count('<a href="http://www.a.com">www.a.com</a>') == 5000
    assert "<a" not in mail.markup and "mailto" not in mail.markup


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("see www.foo.org.", 'see <a href="http://www.foo.org">www.foo.org</a>.'),
        ("(www.foo.org/a_(b))", '(<a href="http://www.foo.org/a_(b)">www.foo.org/a_(b)</a>)'),
        ("(www.foo.org/a)", '(<a href="http://www.foo.org/a">www.foo.org/a</a>)'),
        (
            "www.foo.org/x?y=1&z, then",
            '<a href="http://www.foo.org/x?y=1&amp;z">www.foo.org/x?y=1&amp;z</a>, then',
        ),
        ("WWW.Foo.ORG", '<a href="http://WWW.Foo.ORG">WWW.Foo.ORG</a>'),
        ("www.foo", "www.foo"),  # one segment is no domain
        ("a.www.foo.org", "a.www.foo.org"),  # the tail of a word
        ("xwww.foo.org", "xwww.foo.org"),
        ("www.foo.org<b>", '<a href="http://www.foo.org">www.foo.org</a>&lt;b&gt;'),
    ],
)
def test_link_www_boundaries(text, expected):
    assert link_www(text) == expected
    assert markup_ok(link_www(text))


def test_www_inside_a_link_or_code_stays_the_authors():
    (text,) = parse_blocks("[www.foo.org](https://q.io) `www.foo.org` https://www.foo.org/x")
    assert text.markup == (
        '<a href="https://q.io">www.foo.org</a> <tt>www.foo.org</tt> '
        '<a href="https://www.foo.org/x">https://www.foo.org/x</a>'
    )


def test_every_block_carries_its_source():
    for block in walk(parse_blocks(FIXTURE)):
        assert block.source.strip(), block
        if isinstance(block, ListBlock):
            for item in block.items:
                for child in walk(item.children):
                    assert child.source.strip(), child


def test_image_rows_merge_on_a_line_and_split_on_a_break():
    blocks = parse_blocks(FIXTURE)
    assert blocks[13] == ImageRow(
        (BodyImage("https://img/1.png", "alt"), BodyImage("https://img/2.png", "alt2")),
        "![alt](https://img/1.png) ![alt2](https://img/2.png)",
    )
    assert blocks[14] == ImageRow((BodyImage("https://img/3.png", "alt3"),), "![alt3](https://img/3.png)")
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


# -- tables ------------------------------------------------------------------


def test_table_golden_tree():
    source = (
        "| Name | Count | Note |\n|:-----|------:|:----:|\n"
        "| **a** | 1 | `x` |\n| b | 22 | [l](https://e.x/) |"
    )
    (table,) = parse_blocks(source)
    assert table == Table(
        ("left", "right", "center"),
        ("Name", "Count", "Note"),
        (("<b>a</b>", "1", "<tt>x</tt>"), ("b", "22", '<a href="https://e.x/">l</a>')),
        source,
    )
    for cell in table.header + tuple(c for row in table.rows for c in row):
        assert markup_ok(cell)


def test_table_rows_are_squared_to_the_header():
    (table,) = parse_blocks("| a | b | c |\n|---|---|---|\n| 1 |\n| 1 | 2 | 3 | 4 |")
    assert table.rows == (("1", "", ""), ("1", "2", "3"))


def test_table_cells_are_inline_only():
    (table,) = parse_blocks("| a |\n|---|\n| ![alt](https://x.example/i.png) |")
    assert table.rows == (('<a href="https://x.example/i.png">alt</a>',),)
    blocks = parse_blocks("| a |\n|---|\n| ![p](https://x.example/i.png) |\n")
    assert not any(isinstance(b, ImageRow) for b in blocks)
    (html,) = parse_blocks('| a |\n|---|\n| <b>x</b> & <img src="https://x.example/i.png" alt="pic"> |')
    assert html.rows == (('&lt;b&gt;x&lt;/b&gt; &amp; <a href="https://x.example/i.png">pic</a>',),)
    (piped,) = parse_blocks("| a |\n|---|\n| x \\| y |")
    assert piped.rows == (("x | y",),)


def test_table_header_only_and_nested():
    (bare,) = parse_blocks("| a | b |\n|---|---|")
    assert bare.header == ("a", "b") and bare.rows == ()
    (item,) = parse_blocks("- item\n\n  | a |\n  |---|\n  | 1 |")
    assert isinstance(item.items[0].children[1], Table)
    (quote,) = parse_blocks("> | a |\n> |---|\n> | 1 |")
    assert isinstance(quote.children[0], Table)


def test_cap_table_leaves_a_small_table_alone():
    (table,) = parse_blocks(FIXTURE)[6:7]
    shown, more_rows, more_columns = mdblocks.cap_table(table)
    assert shown == table and more_rows == 0 and more_columns == 0


def test_cap_table_caps_rows_and_columns():
    width = mdblocks.TABLE_MAX_COLUMNS + 3
    header = "| " + " | ".join(f"h{i}" for i in range(width)) + " |\n|" + "---|" * width + "\n"
    body = "\n".join("| " + " | ".join(f"r{r}c{c}" for c in range(width)) + " |" for r in range(70))
    (table,) = parse_blocks(header + body)
    shown, more_rows, more_columns = mdblocks.cap_table(table)
    assert more_rows == 70 - mdblocks.TABLE_MAX_ROWS and more_columns == 3
    assert len(shown.header) == mdblocks.TABLE_MAX_COLUMNS
    assert len(shown.rows) == mdblocks.TABLE_MAX_ROWS
    assert all(len(row) == mdblocks.TABLE_MAX_COLUMNS for row in shown.rows)
    assert len(shown.aligns) == mdblocks.TABLE_MAX_COLUMNS
    assert shown.rows[-1][0] == f"r{mdblocks.TABLE_MAX_ROWS - 1}c0"
    assert shown.source == table.source


def test_cap_table_squares_a_hand_built_table():
    ragged = Table((None,), ("a", "b", "c"), (("1",), ("1", "2", "3", "4")), "")
    shown, more_rows, more_columns = mdblocks.cap_table(ragged)
    assert shown.aligns == (None, None, None)
    assert shown.rows == (("1", "", ""), ("1", "2", "3"))
    assert (more_rows, more_columns) == (0, 0)


# -- code blocks -------------------------------------------------------------


def test_code_block_golden_tree():
    """One CodeBlock per fence, its text verbatim (trailing newline kept,
    markup-free — the widget puts it in a buffer, never in Pango), its lang
    the info string's first word lowercased, its source the fence lines."""
    body = (
        "Before.\n\n```py\nx = 1\n<b>&\n```\n\n~~~ JSON   trailing words\n{}\n~~~\n\n"
        "    indented\n    two\n\nAfter."
    )
    blocks = parse_blocks(body)
    assert [type(b).__name__ for b in blocks] == ["Text", "CodeBlock", "CodeBlock", "CodeBlock", "Text"]
    assert blocks[1] == CodeBlock("x = 1\n<b>&\n", "py", "```py\nx = 1\n<b>&\n```")
    assert blocks[2] == CodeBlock("{}\n", "json", "~~~ JSON   trailing words\n{}\n~~~")
    assert blocks[3] == CodeBlock("indented\ntwo\n", None, "    indented\n    two")


def test_code_block_without_an_info_word_has_no_lang():
    (fence,) = parse_blocks("```\nplain\n```")
    assert fence == CodeBlock("plain\n", None, "```\nplain\n```")
    (empty,) = parse_blocks("```python\n```")
    assert empty == CodeBlock("", "python", "```python\n```")


def test_code_block_inside_a_list_item_and_a_quote():
    (lst,) = parse_blocks("- item\n\n  ```sh\n  echo hi\n  ```")
    (text, code) = lst.items[0].children
    assert code == CodeBlock("echo hi\n", "sh", "  ```sh\n  echo hi\n  ```")
    (quote,) = parse_blocks("> ```\n> a\n> ```")
    assert quote.children == (CodeBlock("a\n", None, "> ```\n> a\n> ```"),)


def test_code_block_line_cost_is_its_lines_capped_at_eight():
    assert mdblocks.line_cost(CodeBlock("a\n", None, "")) == 2  # a line and its newline
    assert mdblocks.line_cost(CodeBlock("a\nb\nc", None, "")) == 3
    assert mdblocks.line_cost(CodeBlock("\n".join(["x"] * 200), None, "")) == 8


def test_unclosed_fence_runs_to_the_end_of_the_body():
    (fence,) = parse_blocks("```js\nlet a\n\nmore")
    assert fence.lang == "js"
    assert fence.text == "let a\n\nmore"


# -- GitHub references ---------------------------------------------------------

HEAD = "0123456789abcdef0123456789abcdef01234567"
CTX = repo_context("episode6/collins", "github.com", HEAD)
REPO = "https://github.com/episode6/collins"


def anchors(markup: str) -> list[tuple[str, str]]:
    return re.findall(r'<a href="([^"]*)">([^<]*)</a>', markup)


def test_repo_context_holds_each_part_to_its_shape():
    assert repo_context("episode6/collins") == RepoContext("episode6/collins", "github.com", "")
    assert repo_context("o/n", "ghe.corp.example", HEAD) == RepoContext("o/n", "ghe.corp.example", HEAD)
    assert repo_context(None) is None
    assert repo_context("") is None
    assert repo_context("no-slash") is None
    assert repo_context("a/b/c") is None
    assert repo_context("o/n", "not a host").host == "github.com"
    assert repo_context("o/n", "github.com", "b").head == ""  # not a full oid
    assert repo_context("o/n", "github.com", "B" * 40).head == ""


def test_refs_link_into_the_page_repository():
    (text,) = parse_blocks("Fixes #12 and other/repo#3, thanks @octocat, see 0123abc.", refs=CTX)
    assert anchors(text.markup) == [
        (f"{REPO}/issues/12", "#12"),
        ("https://github.com/other/repo/issues/3", "other/repo#3"),
        ("https://github.com/octocat", "@octocat"),
        (f"{REPO}/commit/0123abc", "0123abc"),
    ]
    assert markup_ok(text.markup)


def test_refs_use_the_pages_host():
    ctx = repo_context("o/n", "ghe.corp.example")
    (text,) = parse_blocks("#1 @u 0123abc", refs=ctx)
    assert [href for href, _w in anchors(text.markup)] == [
        "https://ghe.corp.example/o/n/issues/1",
        "https://ghe.corp.example/u",
        "https://ghe.corp.example/o/n/commit/0123abc",
    ]


def test_refs_and_www_autolinks_share_a_text_token():
    (text,) = parse_blocks("see www.a.com/x#1 and #2, @u at www.b.org.", refs=CTX)
    assert anchors(text.markup) == [
        ("http://www.a.com/x#1", "www.a.com/x#1"),
        (f"{REPO}/issues/2", "#2"),
        ("https://github.com/u", "@u"),
        ("http://www.b.org", "www.b.org"),
    ]
    assert markup_ok(text.markup)


def test_no_context_leaves_refs_as_text():
    (text,) = parse_blocks("Fixes #12, thanks @octocat, see 0123abc.")
    assert "<a" not in text.markup
    assert text.markup == "Fixes #12, thanks @octocat, see 0123abc."


@pytest.mark.parametrize(
    "body, linked",
    [
        ("#12", ["#12"]),
        ("(#12)", ["#12"]),
        ("[#12]", ["#12"]),
        ("**#12**", ["#12"]),
        ("#12.", ["#12"]),
        ("#12,", ["#12"]),
        ("x#12", []),  # on the tail of a word
        ("&#12", []),  # an entity's tail
        ("#12x", []),
        ("#12-x", []),
        ("#12/x", []),
        ("#", []),
        ("#abc", []),
        ("#12345678901", []),  # past ten digits: not a number GitHub minted
    ],
)
def test_issue_ref_boundaries(body, linked):
    (text,) = parse_blocks(body, refs=CTX)
    assert [word for _h, word in anchors(text.markup)] == linked


@pytest.mark.parametrize(
    "body, linked",
    [
        ("other/repo#3", ["other/repo#3"]),
        ("(other/repo#3)", ["other/repo#3"]),
        ("a-b/c.d_e#3", ["a-b/c.d_e#3"]),
        (".foo/bar#5", []),  # a path's tail
        ("x/foo/bar#5", []),  # three segments: no
        ("foo/bar.#6", []),  # a name can't end in a dot
        ("foo/bar#6x", []),
    ],
)
def test_cross_repo_ref_boundaries(body, linked):
    (text,) = parse_blocks(body, refs=CTX)
    assert [word for _h, word in anchors(text.markup)] == linked


@pytest.mark.parametrize(
    "body, linked",
    [
        ("@octocat", ["@octocat"]),
        ("@octocat's", ["@octocat"]),
        ("(@a-b)", ["@a-b"]),
        ("@a1", ["@a1"]),
        ("@" + "a" * 39, ["@" + "a" * 39]),
        ("@" + "a" * 40, []),  # past GitHub's 39
        ("@-x", []),  # a login can't start with a hyphen
        ("@x-", []),  # nor end with one
        ("@@y", []),
        ("@a/b", []),  # a team, or a path
        ("end@z", []),
        ("@", []),
        ("@a_b", []),  # underscore isn't in the alphabet
    ],
)
def test_mention_boundaries(body, linked):
    (text,) = parse_blocks(body, refs=CTX)
    assert [word for _h, word in anchors(text.markup)] == linked


def test_mention_gate_is_the_avatar_gate():
    assert mdblocks.LOGIN_RE.match("octo-cat")
    assert not mdblocks.LOGIN_RE.match("octo_cat")
    assert not mdblocks.LOGIN_RE.match("a" * 40)


@pytest.mark.parametrize(
    "body, linked",
    [
        ("0123abc", ["0123abc"]),
        ("(0123abc)", ["0123abc"]),
        ("0123abc.", ["0123abc"]),
        ("deadbeef", []),  # a word: no digit
        ("1234567", []),  # a number: no letter
        ("0123ab", []),  # six: too short
        ("0123ABC", []),  # commits are lowercase
        ("x0123abc", []),
        ("0123abcx", []),
        ("0123abc-x", []),
        ("sha/0123abc", []),
        ("#0123abc", []),
        (HEAD, [HEAD]),
        (HEAD + "8", []),  # forty-one: not a commit
    ],
)
def test_commit_boundaries(body, linked):
    (text,) = parse_blocks(body, refs=CTX)
    assert [word for _h, word in anchors(text.markup)] == linked


def test_refs_inside_code_spans_stay_literal():
    (text,) = parse_blocks("`#12` and `@octocat` and `0123abc`", refs=CTX)
    assert "<a" not in text.markup
    assert text.markup == "<tt>#12</tt> and <tt>@octocat</tt> and <tt>0123abc</tt>"


def test_refs_inside_links_are_the_links_own_text():
    (text,) = parse_blocks("[#7](https://example.com/z) and [@u](https://example.com/u)", refs=CTX)
    assert anchors(text.markup) == [("https://example.com/z", "#7"), ("https://example.com/u", "@u")]


def test_refs_inside_a_refused_link_stay_text():
    (text,) = parse_blocks("[see #8](/x)", refs=CTX)
    assert "<a" not in text.markup
    # markdown-it refuses a javascript: destination outright, so that one
    # is plain text — in which the reference links, as on GitHub.
    (text,) = parse_blocks("[#7](javascript:alert(1))", refs=CTX)
    assert anchors(text.markup) == [(f"{REPO}/issues/7", "#7")]
    assert "javascript" in text.markup and 'href="javascript' not in text.markup


def test_refs_in_fences_stay_literal():
    (code,) = parse_blocks("```\nsee #12 @octocat 0123abc\n```", refs=CTX)
    assert isinstance(code, CodeBlock)
    assert code.text == "see #12 @octocat 0123abc\n"


def test_refs_in_headings_table_cells_lists_and_quotes():
    body = "# Fixes #1\n\n| #2 | @u |\n|--|--|\n| 0123abc | x |\n\n- see #3\n\n> by @v"
    heading, table, lst, quote = parse_blocks(body, refs=CTX)
    assert anchors(heading.markup) == [(f"{REPO}/issues/1", "#1")]
    assert anchors(table.header[0]) == [(f"{REPO}/issues/2", "#2")]
    assert anchors(table.header[1]) == [("https://github.com/u", "@u")]
    assert anchors(table.rows[0][0]) == [(f"{REPO}/commit/0123abc", "0123abc")]
    assert anchors(lst.items[0].children[0].markup) == [(f"{REPO}/issues/3", "#3")]
    assert anchors(quote.children[0].markup) == [("https://github.com/v", "@v")]


def test_ref_markup_is_escaped_and_well_formed():
    (text,) = parse_blocks("<#1 & @b> 0123abc<x>", refs=CTX)
    assert markup_ok(text.markup)
    assert text.markup.startswith("&lt;<a ")
    assert "&amp;" in text.markup and "&lt;x&gt;" in text.markup
    for markup in markups(parse_blocks(FIXTURE, refs=CTX)):
        assert markup_ok(markup), markup
        assert not re.search(r"<(?!/?(?:a|b|i|s|tt|sub|sup|span)\b)", markup), markup


def test_fixture_refs_line_links_under_a_context():
    text = parse_blocks(FIXTURE, refs=CTX)[12]
    assert "See example.com and" in text.markup  # the linkify post-filter still holds
    assert '<a href="http://www.foo.org">www.foo.org</a>' in text.markup
    assert f'<a href="{REPO}/blob/{HEAD}/docs/a.md">rel</a>' in text.markup


def test_render_inline_links_refs():
    assert render_inline("see #2", CTX) == f'see <a href="{REPO}/issues/2">#2</a>'
    assert render_inline("see #2") == "see #2"


def test_relative_links_resolve_at_the_head_commit():
    (text,) = parse_blocks("[t](docs/a.md) [u](./b.md) [v](docs/a%20b.md)", refs=CTX)
    assert anchors(text.markup) == [
        (f"{REPO}/blob/{HEAD}/docs/a.md", "t"),
        (f"{REPO}/blob/{HEAD}/b.md", "u"),
        (f"{REPO}/blob/{HEAD}/docs/a%20b.md", "v"),
    ]


@pytest.mark.parametrize(
    "href",
    [
        "../c.md",
        "docs/../c.md",
        "/d.md",
        "#frag",
        "docs/a.md?x=1",
        "docs/a.md#top",
        "a//b",
        "mailto:a@b",
        "x:y",
    ],
)
def test_relative_links_github_would_not_take_stay_text(href):
    (text,) = parse_blocks(f"[w]({href})", refs=CTX)
    assert "<a" not in text.markup, text.markup
    assert text.markup == "w"


def test_relative_links_without_a_head_stay_text():
    ctx = repo_context("episode6/collins")
    (text,) = parse_blocks("[t](docs/a.md)", refs=ctx)
    assert text.markup == "t"
    assert relative_href("docs/a.md", ctx) == "docs/a.md"
    assert relative_href("a" * 2000, CTX) == "a" * 2000


def test_relative_link_does_not_touch_a_linkified_domain():
    # `b.md` reads as a domain to linkify; a linkify token is never a path.
    (text,) = parse_blocks("see b.md here", refs=CTX)
    assert "blob" not in text.markup


def test_link_refs_is_linear_on_a_hostile_body():
    import time

    started = time.monotonic()
    for body in ("#" * 100_000, "0123abc" * 15_000, "f" * 100_000, "@" * 100_000, "a/" * 50_000):
        link_refs(body, CTX)
    assert time.monotonic() - started < 2.0


# -- details and alerts --------------------------------------------------------


@pytest.mark.parametrize("kind", mdblocks.ALERT_KINDS)
def test_every_alert_kind_is_detected_and_its_marker_dropped(kind):
    (quote,) = parse_blocks(f"> [!{kind.upper()}]\n> the **body**\n> more")
    assert quote == Quote(
        (Text("the <b>body</b>\nmore", "the **body**\nmore"),),
        f"> [!{kind.upper()}]\n> the **body**\n> more",
        kind,
    )


def test_alert_marker_must_be_the_first_block():
    # A marker after a paragraph, or in a nested quote's second block, is
    # literal; only the quote's opening line names a kind.
    (quote,) = parse_blocks("> intro\n>\n> [!TIP]\n> x")
    assert quote.kind == "plain"
    assert "[!TIP]" in quote.children[1].markup
    (outer,) = parse_blocks("> > [!TIP]\n> > inner")
    assert outer.kind == "plain"
    assert outer.children[0].kind == "tip"


def test_alert_with_blocks_after_its_first_paragraph():
    (quote,) = parse_blocks("> [!WARNING]\n> first\n>\n> - item\n>\n> ```\n> code\n> ```")
    assert quote.kind == "warning"
    assert [type(c) for c in quote.children] == [Text, ListBlock, CodeBlock]
    assert quote.children[0] == Text("first", "first")


def test_details_open_attribute_forms():
    body = "<details{attrs}>\n<summary>S</summary>\n\nbody\n\n</details>"
    for attrs, expected in (
        (" open", True),
        (' open=""', True),
        (" OPEN", True),
        (' class="x" open', True),
        ("", False),
        (' class="open"', False),
        (' data-open="1"', False),
    ):
        (details,) = parse_blocks(body.format(attrs=attrs))
        assert isinstance(details, Details), attrs
        assert details.open is expected, attrs
        assert details.summary == "S"


def test_details_without_a_summary():
    (details,) = parse_blocks("<details>\n\nbody\n\n</details>")
    assert details == Details("", (Text("body", "body"),), False, "<details>\n\nbody\n\n</details>")


def test_details_summary_is_escaped_plain_text():
    (details,) = parse_blocks(
        '<details>\n<summary>A &amp; B <a href="x">*not md*</a> &lt;kbd&gt;</summary>\n\nb\n\n</details>'
    )
    assert details.summary == "A &amp; B *not md* &lt;kbd&gt;"
    assert markup_ok(details.summary)


def test_details_summary_whitespace_collapses_and_entities_stay_one_line():
    # A newline written as &#10; (or a tab, or a real line break inside the
    # tag) is one space in the label: the summary is one line of text.
    (details,) = parse_blocks(
        "<details>\n<summary>one&#10;two&#9;three\n  four</summary>\n\nb\n\n</details>"
    )
    assert details.summary == "one two three four"


def test_details_summary_is_capped():
    huge = "x" * 50_000
    (details,) = parse_blocks(f"<details>\n<summary>{huge}</summary>\n\nb\n\n</details>")
    assert len(details.summary) == mdblocks.SUMMARY_MAX
    assert details.summary.endswith("\u2026")
    # The cut lands before the escape, so a cut entity cannot leak.
    words = "&amp; " * 200
    (details,) = parse_blocks(f"<details>\n<summary>{words}</summary>\n\nb\n\n</details>")
    assert markup_ok(details.summary)
    assert len(details.summary.replace("&amp;", "&")) <= mdblocks.SUMMARY_MAX
    assert mdblocks.summary_text(" a  b ") == "a b"


def test_details_raw_lines_after_the_summary_are_one_literal_child():
    (details,) = parse_blocks(
        "<details>\n<summary>S</summary>\n**raw** <b>line</b>\nsecond raw\n\n*md*\n\n</details>"
    )
    assert details.children == (
        Text("**raw** &lt;b&gt;line&lt;/b&gt;\nsecond raw", "**raw** <b>line</b>\nsecond raw"),
        Text("<i>md</i>", "*md*"),
    )


def test_details_holding_every_block_kind():
    (details,) = parse_blocks(
        "<details>\n<summary>All</summary>\n\n# h\n\n- a\n\n> [!NOTE]\n> n\n\n| x |\n|---|\n| 1 |\n\n"
        "```py\nz\n```\n\n---\n\n</details>"
    )
    assert [type(c) for c in details.children] == [Heading, ListBlock, Quote, Table, CodeBlock, Rule]
    assert details.children[2].kind == "note"


def test_details_nested_past_the_depth_cap_is_literal():
    opens = "".join(f"<details>\n<summary>d{i}</summary>\n\n" for i in range(mdblocks.MAX_DEPTH + 2))
    closes = "</details>\n\n" * (mdblocks.MAX_DEPTH + 2)
    (outer,) = parse_blocks(opens + "deep\n\n" + closes)
    node = outer
    depth = 0
    while isinstance(node, Details):
        depth += 1
        node = node.children[0]
    assert depth == mdblocks.MAX_DEPTH
    assert isinstance(node, Text) and node.markup.startswith("&lt;details&gt;")


# -- polish: linked images, <br> lines, encoded steps, the preview cut ----------


def test_linked_image_alone_is_an_image_row():
    (row,) = parse_blocks("[![alt](https://img/1.png)](https://link/x)")
    assert row == ImageRow(
        (BodyImage(url="https://img/1.png", alt="alt"),),
        "[![alt](https://img/1.png)](https://link/x)",
    )
    (row,) = parse_blocks('[<img src="https://img/2.png" alt="pic">](https://link/y)')
    assert isinstance(row, ImageRow) and row.images[0].url == "https://img/2.png"


def test_linked_image_with_siblings_keeps_the_link_and_never_nests_anchors():
    (text,) = parse_blocks("see [![alt](https://img/1.png)](https://link/x) now")
    assert text.markup == 'see <a href="https://link/x">alt</a> now'
    (text,) = parse_blocks('see [<img src="https://img/2.png" alt="pic">](https://link/y) now')
    assert text.markup == 'see <a href="https://link/y">pic</a> now'
    (text,) = parse_blocks("[![alt](https://img/1.png)](https://link/x)", images=False)
    assert text.markup == '<a href="https://link/x">alt</a>'
    assert markup_ok(text.markup) and text.markup.count("<a ") == 1


def test_br_alone_on_a_line_is_dropped():
    assert parse_blocks("a\n\n<br>\n\nb") == [Text("a", "a"), Text("b", "b")]
    assert parse_blocks("a\n\n<br/>\n<br />\n\nb") == [Text("a", "a"), Text("b", "b")]
    assert parse_blocks("<br>") == []
    (text,) = parse_blocks("a<br/>b<BR />c</br>d")
    assert text.markup == "a\nb\nc&lt;/br&gt;d"


@pytest.mark.parametrize("href", ["docs/%2e%2e/x", "docs%2f..%2fx", "%2E%2E/x"])
def test_relative_link_refuses_an_encoded_step_up(href):
    ctx = RepoContext("o/r", head="a" * 40)
    assert relative_href(href, ctx) == href


def test_cut_block_takes_a_lists_first_items():
    (block,) = parse_blocks("\n".join(f"- item {n}" for n in range(20)))
    front = mdblocks.cut_block(block, None, 5)
    assert isinstance(front, ListBlock) and len(front.items) == 5
    assert front.items == block.items[:5] and front.source == block.source
    front = mdblocks.cut_block(block, 32, None)
    assert front is not None and len(front.items) == 5  # "item N" is 6 chars
    assert mdblocks.cut_block(block, None, 0) is None  # not even one item
    assert mdblocks.cut_block(block, None, 20) is None  # all of it fits: nothing to cut
    assert mdblocks.cut_block(block, None, None) is None


def test_cut_block_takes_a_tables_header_and_first_rows():
    (table,) = parse_blocks("| h |\n|---|\n" + "\n".join(f"| r{n} |" for n in range(10)))
    front = mdblocks.cut_block(table, None, 4)
    assert isinstance(front, Table) and front.rows == table.rows[:3] and front.header == table.header
    assert mdblocks.cut_block(table, None, 1) is None  # the header alone shows nothing
    assert mdblocks.cut_block(table, None, 11) is None
    by_chars = mdblocks.cut_block(table, 5, None)
    assert by_chars is not None and len(by_chars.rows) == 2


def test_cut_block_leaves_other_blocks_alone():
    for body in ("para", "```\ncode\n```", "> q", "# h", "---"):
        (block,) = parse_blocks(body)
        assert mdblocks.cut_block(block, 1, 1) is None
