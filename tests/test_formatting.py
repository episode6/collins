from datetime import datetime, timezone

from collins.formatting import blast_radius_body, format_relative, md_to_pango


# -- md_to_pango: the markdown PR bodies and comments lean on -----------------


def test_md_bold_and_headings_still_render():
    assert md_to_pango("# Title\n**bold**") == "<b>Title</b>\n<b>bold</b>"


def test_md_star_italics():
    assert md_to_pango("an *emphasis* here") == "an <i>emphasis</i> here"


def test_md_bold_with_italics_beside_it():
    assert md_to_pango("**strong** and *soft*") == "<b>strong</b> and <i>soft</i>"


def test_md_underscore_italics():
    assert md_to_pango("an _aside_ here") == "an <i>aside</i> here"


def test_md_underscores_inside_identifiers_stay():
    # snake_case must never italicize — these are code names in prose.
    assert md_to_pango("call save_all_files_now please") == "call save_all_files_now please"


def test_md_strikethrough():
    assert md_to_pango("was ~~wrong~~ right") == "was <s>wrong</s> right"


def test_md_loose_tildes_stay():
    assert md_to_pango("~~ not a strike ~~") == "~~ not a strike ~~"


def test_md_blockquote_dims_behind_a_bar():
    assert md_to_pango("> quoted words") == '<span alpha="60%">▎ quoted words</span>'


def test_md_blockquote_nests_a_bar_per_level():
    assert md_to_pango("> > deeper") == '<span alpha="60%">▎▎ deeper</span>'


def test_md_blockquote_keeps_inline_markdown():
    assert md_to_pango("> **bold** quote") == '<span alpha="60%">▎ <b>bold</b> quote</span>'


def test_md_blockquote_leaves_neighbouring_lines_alone():
    # A blank line above the quote must not be folded into it.
    assert md_to_pango("before\n\n> quote\nafter") == (
        'before\n\n<span alpha="60%">▎ quote</span>\nafter'
    )


def test_md_numbered_lists_count_as_rendered():
    # GitHub's rule: authors write "1." all the way down, renderers count.
    assert md_to_pango("1. first\n1. second\n1. third") == "1. first\n2. second\n3. third"


def test_md_numbered_lists_start_where_the_author_did():
    assert md_to_pango("3) a\n3) b") == "3) a\n4) b"


def test_md_numbered_runs_reset_across_other_text():
    assert md_to_pango("1. a\n1. b\n\nprose\n\n1. x\n1. y") == (
        "1. a\n2. b\n\nprose\n\n1. x\n2. y"
    )


def test_md_numbered_lists_keep_indent_levels_apart():
    assert md_to_pango("1. a\n   1. inner\n   1. inner\n2. b") == (
        "1. a\n   1. inner\n   2. inner\n2. b"
    )


def test_md_absurd_numbers_are_not_list_markers():
    text = "9" * 5000 + ". not a list"
    assert md_to_pango(text) == text


def test_md_links_render_as_anchors_only_when_asked():
    text = "see [docs](https://example.com/d)"
    assert md_to_pango(text) == "see [docs](https://example.com/d)"
    assert md_to_pango(text, links=True) == (
        'see <a href="https://example.com/d">docs</a>'
    )


def test_md_links_survive_balanced_parens_in_the_url():
    # GitHub-generated links carry parens ("...#L10(note)"); the link must
    # swallow the pair but still stop at a bare closing paren.
    assert md_to_pango("[fix](https://ex.com/a(b)c)", links=True) == (
        '<a href="https://ex.com/a(b)c">fix</a>'
    )
    assert md_to_pango("([docs](https://ex.com/d))", links=True) == (
        '(<a href="https://ex.com/d">docs</a>)'
    )


def test_md_images_degrade_to_their_alt_text_link():
    # No remote fetches, ever: an image renders as its alt text linking out.
    assert md_to_pango("![a chart](https://example.com/c.png)", links=True) == (
        '<a href="https://example.com/c.png">a chart</a>'
    )


def test_md_bare_image_keeps_its_url_as_the_label():
    assert md_to_pango("![](https://example.com/c.png)", links=True) == (
        '<a href="https://example.com/c.png">https://example.com/c.png</a>'
    )


def test_md_code_spans_shield_their_contents():
    assert md_to_pango("`*not italic*` and *italic*") == (
        "<tt>*not italic*</tt> and <i>italic</i>"
    )


def test_md_escapes_markup_in_plain_text():
    assert md_to_pango("a < b & *c*") == "a &lt; b &amp; <i>c</i>"


# -- format_relative ----------------------------------------------------------


def test_relative_ages():
    now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)
    assert format_relative("2026-08-10T11:59:40Z", now=now) == "just now"
    assert format_relative("2026-08-10T11:05:00Z", now=now) == "55m ago"
    assert format_relative("2026-08-10T03:00:00Z", now=now) == "9h ago"
    assert format_relative("2026-08-01T12:00:00Z", now=now) == "9d ago"


def test_relative_falls_back_to_a_date_when_old():
    now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)
    assert format_relative("2025-12-25T12:00:00Z", now=now).startswith("2025-12-2")


def test_relative_handles_junk_like_format_timestamp():
    assert format_relative(None) == "—"
    assert format_relative("not a date") == "not a date"


def _breakdown(count: int, archived: int = 2, total: int = 2):
    return [(f"project-{i:03d}", archived, total) for i in range(count)]


def _list_lines(body: str) -> list[str]:
    """The per-project block: the paragraph above it and the warning below are
    separated by blank lines."""
    return body.split("\n\n")[1].splitlines()


def _named(body: str) -> list[str]:
    """The same block minus the "…and N others" line, which sums up the
    projects the list didn't name."""
    return [line for line in _list_lines(body) if not line.startswith("…")]


def test_blast_radius_names_every_project_when_it_fits():
    body = blast_radius_body(6, _breakdown(3))
    assert "project-000 — 2 of 2" in body
    assert "project-002 — 2 of 2" in body
    assert "other project" not in body
    assert "3 of these project(s) lose every session they have." in body


def test_blast_radius_names_a_full_short_list():
    # Seven still fits: naming them all beats cutting to four plus a summary
    # line for the three that are left.
    body = blast_radius_body(14, _breakdown(7))
    assert "project-006 — 2 of 2" in body
    assert "other project" not in body


def test_blast_radius_caps_the_list_once_it_grows():
    body = blast_radius_body(16, _breakdown(8))
    assert len(_named(body)) == 4
    assert "…and 4 other project(s) — 8 session(s)" in body


def test_blast_radius_stays_the_same_size_at_scale():
    body = blast_radius_body(400, _breakdown(200))
    assert len(_named(body)) == 4  # 200 projects must not grow the dialog
    assert "…and 196 other project(s) — 392 session(s)" in body
    assert "400 session(s) in 200 project(s)" in body


def test_blast_radius_orders_the_lines_shortest_first():
    # Centred in the dialog, the column tapers evenly instead of jumping about.
    # Which projects get named still goes by session count, not by length.
    breakdown = [("epsilon-web-frontend", 9, 9), ("beta", 4, 4), ("gamma-api", 2, 2)]
    assert _named(blast_radius_body(15, breakdown)) == [
        "beta — 4 of 4",
        "gamma-api — 2 of 2",
        "epsilon-web-frontend — 9 of 9",
    ]


def test_blast_radius_keeps_the_summed_line_last():
    # It stands for the projects the named lines left out, however short it is.
    breakdown = [(f"a-project-with-a-long-name-{i}", 5, 5) for i in range(8)]
    assert _list_lines(blast_radius_body(40, breakdown))[-1] == (
        "…and 4 other project(s) — 20 session(s)"
    )


def test_blast_radius_omits_the_warning_when_no_project_empties():
    body = blast_radius_body(3, [("alpha", 3, 9)])
    assert "alpha — 3 of 9" in body
    assert "lose every session" not in body
