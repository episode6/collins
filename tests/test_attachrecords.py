# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The session image log: sighting validation, merge rules, order, cap, I/O."""

from collins import attachrecords
from collins.attachrecords import LIGHTBOX, TRANSCRIPT, Attachment


def shot(key, *, source=LIGHTBOX, now=1000.0, **kw):
    """A sighting, with the two fields every call would otherwise repeat."""
    return attachrecords.sighting(key, source=source, now=now, **kw)


# -- sighting validation --------------------------------------------------


def test_sighting_keeps_an_absolute_local_path():
    one = shot("/tmp/shot.png", caption="The failing row")
    assert one.key == "/tmp/shot.png"
    assert not one.remote
    assert one.caption == "The failing row"
    assert one.at == one.last == 1000.0


def test_sighting_normalizes_a_local_path():
    assert shot("/tmp/./sub/../shot.png").key == "/tmp/shot.png"


def test_sighting_marks_an_http_url_remote_and_leaves_it_alone():
    one = shot("https://example.com/a//b.png")
    assert one.remote
    assert one.key == "https://example.com/a//b.png"


def test_sighting_refuses_a_relative_path():
    assert shot("shot.png") is None
    assert shot("./shot.png") is None
    assert shot("~/shot.png") is None


def test_sighting_refuses_an_unfetchable_scheme():
    # Not a path (no leading slash) and not http(s): nothing downstream
    # could open it, so it never becomes a row.
    assert shot("ftp://example.com/shot.png") is None


def test_sighting_refuses_empty_and_non_string_keys():
    assert shot("") is None
    assert shot("   ") is None
    assert shot(None) is None
    assert shot(b"/tmp/shot.png") is None


def test_sighting_refuses_unknown_kinds_and_sources():
    assert shot("/tmp/a.png", kind="file") is None
    assert shot("/tmp/a.png", source="guesswork") is None


def test_sighting_flattens_and_trims_long_text():
    long_caption = "word " * 100
    one = shot("/tmp/a.png", caption="two\nlines", context=long_caption)
    assert one.caption == "two lines"
    assert len(one.context) == attachrecords.MAX_TEXT
    assert one.context.endswith("…")


def test_sighting_keeps_a_long_origin_whole():
    # The origin is a URL the record has to be able to re-fetch from, not a
    # label — trimming it would break the round trip.
    url = "https://example.com/" + "x" * 300 + ".png"
    assert shot(url, origin=url).origin == url


def test_sighting_treats_blank_text_as_absent():
    one = shot("/tmp/a.png", caption="   ", context="")
    assert one.caption is None
    assert one.context is None


# -- merge rules ----------------------------------------------------------


def test_a_second_sighting_moves_recency_but_not_first_sighting():
    folded = attachrecords.fold({}, shot("/tmp/a.png", now=10.0))
    folded = attachrecords.fold(folded, shot("/tmp/a.png", now=99.0))
    assert list(folded) == ["/tmp/a.png"]
    assert folded["/tmp/a.png"].at == 10.0
    assert folded["/tmp/a.png"].last == 99.0


def test_a_lightbox_caption_survives_a_later_captionless_showing():
    folded = attachrecords.fold({}, shot("/tmp/a.png", caption="Before"))
    folded = attachrecords.fold(folded, shot("/tmp/a.png", now=2000.0))
    assert folded["/tmp/a.png"].caption == "Before"


def test_a_lightbox_caption_replaces_an_earlier_one():
    folded = attachrecords.fold({}, shot("/tmp/a.png", caption="Before"))
    folded = attachrecords.fold(folded, shot("/tmp/a.png", caption="After", now=2000.0))
    assert folded["/tmp/a.png"].caption == "After"


def test_a_transcript_snippet_never_becomes_a_caption():
    folded = attachrecords.fold({}, shot("/tmp/a.png", caption="Shown"))
    folded = attachrecords.fold(
        folded, shot("/tmp/a.png", source=TRANSCRIPT, caption="Mentioned", now=2000.0)
    )
    assert folded["/tmp/a.png"].caption == "Shown"


def test_context_fills_an_empty_slot_and_then_stays_put():
    # The terminal-clicked case: recorded captionless, labelled later by the
    # sentence the path was printed in, and not re-labelled after that.
    folded = attachrecords.fold({}, shot("/tmp/a.png"))
    assert folded["/tmp/a.png"].context is None
    folded = attachrecords.fold(
        folded, shot("/tmp/a.png", source=TRANSCRIPT, context="here is the chart", now=2000.0)
    )
    assert folded["/tmp/a.png"].context == "here is the chart"
    folded = attachrecords.fold(
        folded, shot("/tmp/a.png", source=TRANSCRIPT, context="and again", now=3000.0)
    )
    assert folded["/tmp/a.png"].context == "here is the chart"


def test_being_shown_outranks_being_mentioned_whichever_came_first():
    mentioned = shot("/tmp/a.png", source=TRANSCRIPT, context="see /tmp/a.png")
    shown = shot("/tmp/a.png", caption="Shown", now=2000.0)
    assert attachrecords.fold({}, mentioned, shown)["/tmp/a.png"].source == LIGHTBOX
    assert attachrecords.fold({}, shown, mentioned)["/tmp/a.png"].source == LIGHTBOX


def test_the_first_origin_wins():
    folded = attachrecords.fold({}, shot("/tmp/a.png", origin="docs/a.png"))
    folded = attachrecords.fold(folded, shot("/tmp/a.png", origin="/tmp/a.png", now=2000.0))
    assert folded["/tmp/a.png"].origin == "docs/a.png"


def test_fold_leaves_the_dict_it_was_given_alone():
    before = attachrecords.fold({}, shot("/tmp/a.png"))
    after = attachrecords.fold(before, shot("/tmp/b.png", now=2000.0))
    assert list(before) == ["/tmp/a.png"]
    assert list(after) == ["/tmp/b.png", "/tmp/a.png"]


def test_fold_skips_none_so_callers_can_pass_a_failed_sighting():
    assert attachrecords.fold({}, None, shot("/tmp/a.png")) != {}


# -- order and cap --------------------------------------------------------


def test_newest_sighting_comes_first():
    folded = attachrecords.fold(
        {},
        shot("/tmp/old.png", now=10.0),
        shot("/tmp/new.png", now=30.0),
        shot("/tmp/mid.png", now=20.0),
    )
    assert list(folded) == ["/tmp/new.png", "/tmp/mid.png", "/tmp/old.png"]


def test_a_re_sighting_moves_an_image_back_to_the_top():
    folded = attachrecords.fold(
        {}, shot("/tmp/a.png", now=10.0), shot("/tmp/b.png", now=20.0)
    )
    folded = attachrecords.fold(folded, shot("/tmp/a.png", now=30.0))
    assert list(folded) == ["/tmp/a.png", "/tmp/b.png"]


def test_images_seen_in_the_same_instant_keep_a_stable_order():
    keys = [f"/tmp/{name}.png" for name in ("c", "a", "b")]
    folded = attachrecords.fold({}, *[shot(key, now=5.0) for key in keys])
    assert list(folded) == ["/tmp/a.png", "/tmp/b.png", "/tmp/c.png"]


def test_the_cap_drops_the_oldest():
    shots = [shot(f"/tmp/{index}.png", now=float(index)) for index in range(120)]
    folded = attachrecords.fold({}, *shots)
    assert len(folded) == attachrecords.MAX_RECORDS
    assert list(folded)[0] == "/tmp/119.png"
    assert "/tmp/19.png" not in folded
    assert "/tmp/20.png" in folded


# -- restore union --------------------------------------------------------


def test_union_keeps_both_sides_and_merges_the_overlap():
    saved = [
        Attachment(key="/tmp/a.png", caption="Saved", source=LIGHTBOX, at=10.0, last=10.0),
        Attachment(key="/tmp/b.png", source=LIGHTBOX, at=11.0, last=11.0),
    ]
    live = {"/tmp/a.png": shot("/tmp/a.png", now=50.0), "/tmp/c.png": shot("/tmp/c.png", now=60.0)}
    joined = attachrecords.union(saved, live)
    assert list(joined) == ["/tmp/c.png", "/tmp/a.png", "/tmp/b.png"]
    assert joined["/tmp/a.png"].caption == "Saved"  # the live re-sighting had none
    assert joined["/tmp/a.png"].at == 10.0
    assert joined["/tmp/a.png"].last == 50.0


# -- what the handle's badge counts ---------------------------------------


def unseen(listed, noted=(), since=1000.0):
    """The badge set and the just-arrived part of it, for a fold's values."""
    return attachrecords.unseen(listed.values(), noted=noted, since=since)


def test_a_restored_history_is_not_news():
    """The case the baseline exists for: everything a session ever saw
    arrives in one go when a tab adopts it, all of it dated when it
    happened."""
    listed = attachrecords.fold(
        {}, *[shot(f"/tmp/{index}.png", now=float(index)) for index in range(20)]
    )
    assert unseen(listed, since=1000.0) == (set(), set())


def test_a_sighting_after_the_baseline_is_news():
    listed = attachrecords.fold({}, shot("/tmp/old.png", now=10.0), shot("/tmp/new.png", now=2000.0))
    assert unseen(listed) == ({"/tmp/new.png"}, {"/tmp/new.png"})


def test_news_stays_news_until_it_is_cleared():
    """A second offering of the same list is not a second arrival: the badge
    keeps its count, and nothing flashes for it."""
    listed = attachrecords.fold({}, shot("/tmp/new.png", now=2000.0))
    assert unseen(listed, noted={"/tmp/new.png"}) == ({"/tmp/new.png"}, set())


def test_a_second_sighting_of_a_counted_image_doesnt_count_twice():
    listed = attachrecords.fold({}, shot("/tmp/new.png", now=2000.0))
    again = attachrecords.fold(listed, shot("/tmp/new.png", now=3000.0))
    assert unseen(again, noted={"/tmp/new.png"}) == ({"/tmp/new.png"}, set())


def test_a_struck_row_takes_its_share_of_the_badge_with_it():
    """A badge counting rows nobody can open is a badge that can't be
    cleared by opening the panel."""
    listed = attachrecords.fold({}, shot("/tmp/a.png", now=2000.0), shot("/tmp/b.png", now=2000.0))
    struck = attachrecords.strike(listed, {"/tmp/a.png"})
    assert unseen(struck, noted={"/tmp/a.png", "/tmp/b.png"}) == ({"/tmp/b.png"}, set())


def test_a_struck_row_is_never_news_in_the_first_place():
    listed = attachrecords.strike(
        attachrecords.fold({}, shot("/tmp/a.png", now=2000.0)), {"/tmp/a.png"}
    )
    assert unseen(listed) == (set(), set())


def test_a_looked_at_picture_stays_looked_at_once_the_baseline_moves():
    """The set is only what has been announced; the baseline is what keeps an
    announced picture from coming straight back, since this reads the whole
    list every call. Emptying one without moving the other hands a session
    every image it ever showed the next time anything lands."""
    listed = attachrecords.fold({}, shot("/tmp/a.png", now=2000.0))
    assert unseen(listed, since=2500.0) == (set(), set())
    later = attachrecords.fold(listed, shot("/tmp/b.png", now=3000.0))
    assert unseen(later, since=2500.0) == ({"/tmp/b.png"}, {"/tmp/b.png"})


def test_an_undated_sighting_is_not_news():
    """A record read back with no usable timestamp fails closed: a badge for
    a picture nobody can date is a badge nobody can explain."""
    listed = {"/tmp/a.png": Attachment(key="/tmp/a.png")}
    assert attachrecords.unseen(listed.values(), noted=(), since=0.0) == (set(), set())


# -- striking a record off ------------------------------------------------


def test_strike_marks_and_visible_hides():
    listed = attachrecords.fold({}, shot("/tmp/a.png"), shot("/tmp/b.png"))
    struck = attachrecords.strike(listed, {"/tmp/a.png"})
    assert struck["/tmp/a.png"].hidden is True
    assert struck["/tmp/b.png"].hidden is False
    assert [one.key for one in attachrecords.visible(struck.values())] == ["/tmp/b.png"]


def test_striking_survives_a_later_sighting():
    """The whole point of a tombstone: the transcript still mentions the
    image, so a record that merely vanished would be back on the next scan."""
    struck = attachrecords.strike(attachrecords.fold({}, shot("/tmp/a.png")), {"/tmp/a.png"})
    again = attachrecords.fold(struck, shot("/tmp/a.png", now=9000.0))
    assert again["/tmp/a.png"].hidden is True
    assert again["/tmp/a.png"].last == 9000.0


def test_striking_a_row_never_pushes_a_listed_one_out():
    """Removing an image must not cost the panel an image."""
    listed = attachrecords.fold(
        {}, *[shot(f"/tmp/{index}.png", now=float(index)) for index in range(120)]
    )
    struck = attachrecords.strike(listed, set(listed))
    assert len(attachrecords.visible(struck.values())) == 0
    both = attachrecords.fold(struck, *[shot(f"/tmp/new{n}.png", now=500.0 + n) for n in range(100)])
    assert len(attachrecords.visible(both.values())) == attachrecords.MAX_RECORDS


def test_tombstones_have_a_budget_of_their_own():
    listed = attachrecords.fold(
        {}, *[shot(f"/tmp/{index}.png", now=float(index)) for index in range(80)]
    )
    struck = attachrecords.strike(listed, set(listed))
    assert len(struck) == attachrecords.MAX_STRUCK
    assert list(struck)[0] == "/tmp/79.png"  # the oldest tombstones go first


def test_striking_drops_the_label_nobody_will_read_again():
    listed = attachrecords.fold({}, shot("/tmp/a.png", caption="Cap", context="Ctx", origin="o"))
    (one,) = attachrecords.strike(listed, {"/tmp/a.png"}).values()
    assert (one.caption, one.context, one.origin) == (None, None, None)
    assert one.key == "/tmp/a.png" and one.at and one.last


def test_a_tombstone_round_trips_through_a_record():
    struck = attachrecords.strike(attachrecords.fold({}, shot("/tmp/a.png")), {"/tmp/a.png"})
    (record,) = attachrecords.to_records(struck.values())
    assert record["hidden"] is True
    assert attachrecords.from_record(record).hidden is True


def test_a_listed_record_says_nothing_about_hiding():
    (record,) = attachrecords.to_records([shot("/tmp/a.png")])
    assert "hidden" not in record
    assert attachrecords.from_record(record).hidden is False


# -- records on disk ------------------------------------------------------


def test_a_record_round_trips():
    one = shot("/tmp/a.png", caption="Cap", context="Ctx", origin="a.png", now=1234.5)
    (back,) = attachrecords.from_records(attachrecords.to_records([one]))
    assert back == one


def test_a_remote_record_round_trips():
    url = "https://example.com/shot.png"
    one = shot(url, caption="Chart", origin=url)
    record = attachrecords.to_record(one)
    assert record["remote"] is True
    assert attachrecords.from_record(record) == one


def test_unknown_fields_are_left_out_of_the_record():
    record = attachrecords.to_record(shot("/tmp/a.png"))
    assert set(record) == {"key", "kind", "source", "at", "last"}


def test_from_records_drops_what_it_cannot_vouch_for():
    assert attachrecords.from_records(
        [
            None,
            "/tmp/a.png",
            {},
            {"key": "", "kind": "image"},
            {"key": "relative.png", "kind": "image"},
            {"key": "/tmp/a.png"},  # no kind
            {"key": "/tmp/a.png", "kind": "file"},  # the deferred kind
            {"key": "/tmp/good.png", "kind": "image", "at": 5, "last": 5},
        ]
    ) == [Attachment(key="/tmp/good.png", at=5.0, last=5.0)]


def test_from_records_survives_a_file_that_is_not_a_list():
    assert attachrecords.from_records(None) == []
    assert attachrecords.from_records({"key": "/tmp/a.png"}) == []


def test_a_nonsense_timestamp_reads_as_unknown():
    (one,) = attachrecords.from_records(
        [{"key": "/tmp/a.png", "kind": "image", "at": "yesterday", "last": True}]
    )
    assert one.at == 0.0
    assert one.last == 0.0


def test_a_record_missing_one_timestamp_borrows_the_other():
    (one,) = attachrecords.from_records([{"key": "/tmp/a.png", "kind": "image", "last": 42.0}])
    assert one.at == 42.0


def test_an_unrecognized_source_reads_as_the_weaker_one():
    (one,) = attachrecords.from_records(
        [{"key": "/tmp/a.png", "kind": "image", "source": "elsewhere"}]
    )
    assert one.source == TRANSCRIPT


def test_from_records_re_derives_order_and_the_cap():
    saved = [
        {"key": "/tmp/a.png", "kind": "image", "at": 1, "last": 1},
        {"key": "/tmp/b.png", "kind": "image", "at": 9, "last": 9},
    ]
    assert [one.key for one in attachrecords.from_records(saved)] == ["/tmp/b.png", "/tmp/a.png"]


def test_a_duplicated_key_on_disk_collapses_to_one_record():
    saved = [
        {"key": "/tmp/a.png", "kind": "image", "caption": "Cap", "source": "lightbox",
         "at": 1, "last": 1},
        {"key": "/tmp/a.png", "kind": "image", "at": 9, "last": 9},
    ]
    (one,) = attachrecords.from_records(saved)
    assert one.caption == "Cap"
    assert one.last == 9.0


def test_records_are_json_safe():
    import json

    records = attachrecords.to_records(attachrecords.fold({}, shot("/tmp/a.png")).values())
    assert json.loads(json.dumps(records)) == records


# -- labels ---------------------------------------------------------------


def test_the_label_prefers_caption_then_context_then_basename():
    assert shot("/tmp/a.png", caption="Cap", context="Ctx").label == "Cap"
    assert shot("/tmp/a.png", context="Ctx").label == "Ctx"
    assert shot("/tmp/a.png").label == "a.png"


def test_a_remote_label_falls_back_to_the_last_path_segment():
    assert shot("https://example.com/charts/cpu.png").label == "cpu.png"


# -- scanning a message ---------------------------------------------------


def make(tmp_path, name):
    """An image that really is on disk, since that is what scanning asks."""
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG")
    return path


def keys(text, tmp_path=None, **kw):
    roots = [str(tmp_path)] if tmp_path is not None else []
    return [one.key for one in attachrecords.scan(text, roots=roots, **kw)]


def test_scan_takes_an_absolute_path_to_an_image_that_exists(tmp_path):
    shot_png = make(tmp_path, "shot.png")
    assert keys(f"the failing row: {shot_png}") == [str(shot_png)]


def test_scan_resolves_a_relative_path_against_the_message_cwd(tmp_path):
    make(tmp_path, "docs/mock.jpg")
    assert keys("see docs/mock.jpg for the layout", tmp_path) == [
        str(tmp_path / "docs/mock.jpg")
    ]


def test_scan_reads_through_a_line_and_column_suffix(tmp_path):
    shot_png = make(tmp_path, "shot.png")
    assert keys(f"{shot_png}:12:3 is the frame", tmp_path) == [str(shot_png)]


def test_scan_expands_a_home_relative_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    make(tmp_path, "Pictures/wide.webp")
    assert keys("saved to ~/Pictures/wide.webp") == [str(tmp_path / "Pictures/wide.webp")]


def test_scan_ignores_a_path_nothing_is_at(tmp_path):
    """A transcript is full of images that were proposed, renamed or never
    written; only what is on disk right now becomes a row."""
    assert keys(f"I'll write it to {tmp_path}/planned.png", tmp_path) == []


def test_scan_ignores_paths_that_are_not_images(tmp_path):
    make(tmp_path, "notes.md")
    make(tmp_path, "collins/terminal.py")
    assert keys("notes.md and collins/terminal.py:44 both changed", tmp_path) == []


def test_scan_takes_a_remote_url_by_its_own_suffix():
    text = "the badge is https://example.com/img/ci.svg?v=2 and the docs are at "
    text += "https://example.com/guide"
    assert keys(text) == ["https://example.com/img/ci.svg?v=2"]


def test_scan_refuses_a_url_it_could_never_fetch():
    """Only http(s) reaches the downloader, so nothing else becomes a row —
    and a URL with no suffix stays out too: `show_image` catches those, and
    a looser grammar would put every link in the session in the panel."""
    assert keys("ftp://example.com/a.png and https://example.com/render?id=7") == []


def test_scan_does_not_read_a_url_as_a_path_as_well():
    assert keys("https://example.com/img/ci.png") == ["https://example.com/img/ci.png"]


def test_scan_records_transcript_sightings_with_no_caption(tmp_path):
    shot_png = make(tmp_path, "shot.png")
    (one,) = attachrecords.scan(f"look at {shot_png}", now=1234.0)
    assert one.source == TRANSCRIPT
    assert one.caption is None
    assert one.at == one.last == 1234.0
    assert one.origin is None  # a local record is already its own path


def test_scan_keeps_the_url_a_remote_record_came_from():
    (one,) = attachrecords.scan("https://example.com/ci.png")
    assert one.remote and one.origin == "https://example.com/ci.png"


def test_scan_collapses_a_path_mentioned_twice(tmp_path):
    shot_png = make(tmp_path, "shot.png")
    (one,) = attachrecords.scan(f"{shot_png} — and again, {shot_png}", now=5.0)
    assert one.key == str(shot_png)


def test_scan_stops_checking_the_disk_after_a_listing(tmp_path, monkeypatch):
    """A message naming hundreds of image paths is `ls`, not a conversation:
    the stat calls stop even though the parse doesn't."""
    monkeypatch.setattr(attachrecords, "MAX_SCAN_CANDIDATES", 3)
    real = make(tmp_path, "real.png")
    text = "\n".join([f"{tmp_path}/missing{n}.png" for n in range(10)] + [str(real)])
    assert keys(text, tmp_path) == []


def test_the_listing_cap_does_not_hold_back_urls(tmp_path, monkeypatch):
    """It is a budget for stat calls, and a URL spends none of it."""
    monkeypatch.setattr(attachrecords, "MAX_SCAN_CANDIDATES", 3)
    text = "\n".join(
        [f"{tmp_path}/missing{n}.png" for n in range(10)] + ["https://example.com/ci.png"]
    )
    assert keys(text, tmp_path) == ["https://example.com/ci.png"]


# -- the context snippet --------------------------------------------------


def context(text, tmp_path):
    (one,) = attachrecords.scan(text, roots=[str(tmp_path)])
    return one.context


def test_the_snippet_is_the_line_around_the_reference(tmp_path):
    make(tmp_path, "out/shot.png")
    text = "First line, unrelated.\nHere's the failing row: out/shot.png — see the badge.\nAfter."
    assert context(text, tmp_path) == "Here's the failing row: — see the badge."


def test_a_line_naming_two_images_keeps_neither_path_in_the_snippets(tmp_path):
    """Each row's label would otherwise carry the other row's path — a
    caption pointing at the wrong picture."""
    make(tmp_path, "out/before.png")
    make(tmp_path, "out/after.png")
    text = "out/before.png became out/after.png after the fix"
    contexts = [one.context for one in attachrecords.scan(text, roots=[str(tmp_path)])]
    assert contexts == ["became after the fix", "became after the fix"]


def test_an_unresolved_image_is_still_elided_from_the_snippet(tmp_path):
    make(tmp_path, "out/after.png")
    text = "out/never-written.png became out/after.png"
    (one,) = attachrecords.scan(text, roots=[str(tmp_path)])
    assert one.context == "became"


def test_punctuation_hanging_off_the_reference_goes_with_it(tmp_path):
    """Left behind, a mark that belonged to the path strands as " ." between
    the words either side and reads as a typo."""
    make(tmp_path, "out/shot.png")
    assert context("docked at last: out/shot.png. Next up, the strip.", tmp_path) == (
        "docked at last: Next up, the strip."
    )
    assert context("the edge, out/shot.png, never fights it", tmp_path) == (
        "the edge, never fights it"
    )
    assert context("see (out/shot.png) for the layout", tmp_path) == "see for the layout"


def test_an_ordinary_link_stays_in_the_snippet(tmp_path):
    make(tmp_path, "out/shot.png")
    text = "as https://example.com/guide describes, out/shot.png is the result"
    (one,) = attachrecords.scan(text, roots=[str(tmp_path)])
    assert one.context == "as https://example.com/guide describes, is the result"


def test_a_reference_alone_on_its_line_has_no_snippet(tmp_path):
    make(tmp_path, "out/shot.png")
    assert context("nothing to say\n\nout/shot.png\n\n", tmp_path) is None


def test_a_long_line_is_trimmed_towards_the_reference(tmp_path):
    make(tmp_path, "out/shot.png")
    text = f"{'front ' * 40}the thing itself out/shot.png right here{' tail' * 40}"
    snippet = context(text, tmp_path)
    assert len(snippet) <= attachrecords.MAX_TEXT
    assert "the thing itself right here" in snippet
    assert snippet.startswith("…") and snippet.endswith("…")


def test_a_long_head_alone_is_trimmed_from_the_front(tmp_path):
    make(tmp_path, "out/shot.png")
    snippet = context(f"{'front ' * 60}and finally out/shot.png", tmp_path)
    assert len(snippet) <= attachrecords.MAX_TEXT
    assert snippet.startswith("…") and snippet.endswith("and finally")


# -- a file delivered straight to the user --------------------------------


def test_delivered_records_an_existing_image_as_a_captioned_lightbox_sighting(tmp_path):
    shot = make(tmp_path, "sheet.png")
    one = attachrecords.delivered(str(shot), caption="icon candidates", now=5.0)
    assert one.key == str(shot)
    assert one.source == LIGHTBOX
    assert one.caption == "icon candidates"
    assert one.context is None
    assert one.at == one.last == 5.0


def test_delivered_resolves_a_relative_path_against_the_roots(tmp_path):
    shot = make(tmp_path, "out/shot.png")
    one = attachrecords.delivered("out/shot.png", roots=[None, str(tmp_path)])
    assert one.key == str(shot)


def test_delivered_expands_a_home_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    shot = make(tmp_path, "shot.png")
    assert attachrecords.delivered("~/shot.png").key == str(shot)


def test_delivered_refuses_what_is_not_an_image_on_disk(tmp_path):
    report = tmp_path / "report.txt"
    report.write_text("words")
    assert attachrecords.delivered(str(report)) is None  # not an image
    assert attachrecords.delivered(str(tmp_path / "gone.png")) is None  # nothing there
    assert attachrecords.delivered("https://example.com/ci.png") is None  # not local
    assert attachrecords.delivered("bare.png") is None  # no root to try against
    assert attachrecords.delivered(None) is None
    assert attachrecords.delivered("   ") is None
