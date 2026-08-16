# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Unit tests for the paned-sizing arithmetic (panelsizing)."""

from collins.panelsizing import (
    DEFAULT_FRACTION,
    HANDLE_SIZE,
    MIN_SPLIT_SIZE,
    SizeMemory,
    room_for_a_split,
    split_size,
)


def test_get_defaults_to_zero():
    assert SizeMemory().get("bottom") == 0


def test_record_stores_and_returns_new_size():
    mem = SizeMemory()
    assert mem.record("bottom", 1000, 700) == 300
    assert mem.get("bottom") == 300


def test_record_unchanged_size_returns_none_but_keeps_it():
    mem = SizeMemory()
    mem.record("bottom", 1000, 700)
    assert mem.record("bottom", 1000, 700) is None
    assert mem.get("bottom") == 300


def test_record_ignores_unallocated_paned():
    mem = SizeMemory()
    assert mem.record("bottom", 0, 700) is None
    assert mem.record("bottom", -1, 700) is None
    assert mem.get("bottom") == 0


def test_record_ignores_degenerate_size():
    mem = SizeMemory()
    assert mem.record("bottom", 500, 500) is None  # size 0
    assert mem.record("bottom", 500, 600) is None  # negative
    assert mem.get("bottom") == 0


def test_record_keys_are_independent():
    mem = SizeMemory()
    mem.record("bottom", 1000, 700)
    mem.record("right", 1600, 1100)
    assert mem.get("bottom") == 300
    assert mem.get("right") == 500


def test_set_seeds_a_size():
    mem = SizeMemory()
    mem.set("right", 420)
    assert mem.get("right") == 420


def test_set_rejects_untrusted_values():
    mem = SizeMemory()
    for bad in (0, -5, "300", 2.5, None, True):
        mem.set("right", bad)
    assert mem.get("right") == 0
    assert not mem.snapshot()


def test_snapshot_is_a_detached_copy():
    mem = SizeMemory()
    mem.set("bottom", 300)
    snap = mem.snapshot()
    assert snap == {"bottom": 300}
    snap["bottom"] = 999
    assert mem.get("bottom") == 300


def test_snapshot_empty_is_falsy():
    assert not SizeMemory().snapshot()


def test_target_prefers_remembered_size():
    mem = SizeMemory()
    mem.set("bottom", 300)
    assert mem.target("bottom", 1000, fallback=250) == 700


def test_target_falls_back_to_app_wide_size():
    assert SizeMemory().target("bottom", 1000, fallback=250) == 750


def test_target_default_fraction_when_nothing_remembered():
    assert SizeMemory().target("bottom", 1000) == int(1000 * DEFAULT_FRACTION)


def test_target_oversized_memory_falls_to_fraction_not_fallback():
    # A remembered size with no room to fit stays remembered (for when
    # there's room again) but the divider lands at the default fraction —
    # the fallback must not shadow the user's own choice.
    mem = SizeMemory()
    mem.set("right", 2000)
    assert mem.target("right", 1000, fallback=250) == int(1000 * DEFAULT_FRACTION)
    assert mem.get("right") == 2000


def test_target_oversized_fallback_falls_to_fraction():
    assert SizeMemory().target("right", 1000, fallback=1000) == int(1000 * DEFAULT_FRACTION)


def test_target_none_without_extent():
    mem = SizeMemory()
    mem.set("bottom", 300)
    assert mem.target("bottom", 0) is None
    assert mem.target("bottom", -1) is None


def test_split_size_is_the_app_wide_size_when_it_fits():
    assert split_size(2000, 400) == 400


def test_split_size_falls_back_to_the_same_fraction_the_sizer_does():
    # The size a divider with nothing to go on lands at (SizeMemory.target
    # with no memory and no fallback) is what the split really costs.
    total = 2000
    assert split_size(total) == total - SizeMemory().target("right", total)
    assert split_size(total, 2000) == split_size(total)  # too wide to fit


def test_split_is_free_when_the_gutter_covers_it():
    # 2400 px of terminal, 1200 of which it will ever use: the 400-px panel
    # and the handle come out of the 1200 px of gutter.
    assert room_for_a_split(2400, 1200, 400)


def test_split_is_not_free_when_it_would_reach_past_the_gutter():
    assert not room_for_a_split(1500, 1200, 400)


def test_split_is_free_at_an_exact_fit():
    # Every pixel of gutter spent, and not one of the terminal's own.
    assert room_for_a_split(1200 + 400 + HANDLE_SIZE, 1200, 400)
    assert not room_for_a_split(1200 + 400 + HANDLE_SIZE - 1, 1200, 400)


def test_split_is_never_free_without_a_maximum_width():
    # No maximum: the terminal uses everything it is given, so there is no
    # gutter to hand a new column.
    assert not room_for_a_split(4000, 0, 400)
    assert not room_for_a_split(4000, -1, 400)


def test_split_is_not_free_before_the_terminal_is_allocated():
    assert not room_for_a_split(0, 1200, 400)
    assert not room_for_a_split(-1, 1200, 400)


def test_unsized_split_is_measured_at_its_fraction_not_a_minimum():
    # Nothing sized yet: the column opens at the default fraction, so that
    # is what has to fit in the gutter — a minimum-width test would call a
    # split free and then take a third of the terminal.
    total = 2400
    assert room_for_a_split(total, total - split_size(total) - HANDLE_SIZE)
    assert not room_for_a_split(total, total - split_size(total) - HANDLE_SIZE + 1)


def test_narrow_wanted_is_refused_however_much_room_there_is():
    assert not room_for_a_split(4000, 1200, MIN_SPLIT_SIZE - 1)
    assert room_for_a_split(4000, 1200, MIN_SPLIT_SIZE)
