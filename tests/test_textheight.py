from collins import textheight


def test_prefix_keeps_a_short_text_whole():
    text = "one\ntwo\nthree\n"
    assert textheight.measured_prefix(text) == text


def test_prefix_stops_at_the_line_bound():
    text = "x\n" * (textheight.PREFIX_LINES + 10)
    head = textheight.measured_prefix(text)
    assert head.count("\n") == textheight.PREFIX_LINES - 1
    assert text.startswith(head)


def test_prefix_stops_at_the_char_bound():
    text = "y" * (textheight.PREFIX_CHARS * 2)
    assert textheight.measured_prefix(text) == "y" * textheight.PREFIX_CHARS


def test_floor_sits_between_the_bounds():
    assert textheight.floor_for(20, 64, 240) == 64
    assert textheight.floor_for(156, 64, 240) == 156
    assert textheight.floor_for(900, 64, 240) == 240
    assert textheight.floor_for(900, 64, -1) == 900


def test_caught_up_reads_past_the_cap_as_equal():
    # The view laid out only the lines on screen: still held.
    assert not textheight.caught_up(64, 156, 240)
    assert textheight.caught_up(156, 156, 240)
    assert textheight.caught_up(158, 156, 240)
    # A pixel or two of rounding between Pango and the view is caught up;
    # a line short is not.
    assert textheight.caught_up(206, 208, 240)
    assert not textheight.caught_up(190, 208, 240)
    # Both past the cap: the box would show the same height either way.
    assert textheight.caught_up(300, 900, 240)
    assert not textheight.caught_up(200, 900, 240)
    assert not textheight.caught_up(300, 900, -1)
