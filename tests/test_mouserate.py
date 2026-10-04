# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The mouse rule of the split's client (collins.mouserate): plain motion
coalesced and de-duplicated, everything else sent at once and in order."""

from collins.mouserate import MotionCoalescer, is_plain_motion


def motion(x, y, code=35, final=b"M"):
    return b"\x1b[<%d;%d;%d%s" % (code, x, y, final)


def test_plain_motion_is_the_no_button_motion_code_with_any_modifiers():
    assert is_plain_motion(35)
    assert is_plain_motion(35 | 4) and is_plain_motion(35 | 8) and is_plain_motion(35 | 16)
    assert not is_plain_motion(32)  # left button held: a drag
    assert not is_plain_motion(0) and not is_plain_motion(2)  # presses
    assert not is_plain_motion(64) and not is_plain_motion(65)  # wheel


def test_a_plain_motion_is_held_until_flushed():
    c = MotionCoalescer()
    assert c.take(motion(10, 5)) == (b"", True)
    assert c.flush() == motion(10, 5)
    assert c.flush() == b""


def test_the_latest_motion_wins_and_a_repeated_cell_is_dropped():
    c = MotionCoalescer()
    c.take(motion(10, 5))
    c.take(motion(11, 5))
    c.take(motion(11, 5))  # the cell of the one before: dropped
    assert c.flush() == motion(11, 5)
    assert c.take(motion(11, 5)) == (b"", False)  # still the same cell
    assert c.take(motion(12, 5)) == (b"", True)


def test_a_press_flushes_the_pending_motion_ahead_of_it():
    c = MotionCoalescer()
    c.take(motion(10, 5))
    now, pending = c.take(motion(10, 5, code=0))  # a left press at the same cell
    assert now == motion(10, 5) + motion(10, 5, code=0)
    assert not pending


def test_a_drag_release_and_wheel_are_never_held():
    c = MotionCoalescer()
    for code, final in ((32, b"M"), (0, b"m"), (64, b"M"), (65, b"M")):
        now, pending = c.take(motion(3, 3, code=code, final=final))
        assert now == motion(3, 3, code=code, final=final) and not pending


def test_keys_mixed_with_motion_keep_their_order():
    c = MotionCoalescer()
    now, pending = c.take(motion(1, 1) + b"a" + motion(2, 2) + b"b")
    assert now == motion(1, 1) + b"a" + motion(2, 2) + b"b"
    assert not pending
    now, pending = c.take(b"x")
    assert now == b"x" and not pending


def test_a_report_split_across_commits_is_not_matched_in_pieces():
    """A report cut by a commit boundary is sent as it came: the client
    never holds plain text back waiting for a sequence to complete."""
    c = MotionCoalescer()
    head, tail = motion(7, 7)[:5], motion(7, 7)[5:]
    assert c.take(head) == (head, False)
    assert c.take(tail) == (tail, False)
