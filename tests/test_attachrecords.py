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
