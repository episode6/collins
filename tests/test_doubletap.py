import pytest

from collins.doubletap import DoubleTap

WINDOW = 500_000  # µs, as Ctrl+J's panel swap uses


@pytest.fixture
def gate():
    return DoubleTap(WINDOW)


def test_a_second_tap_inside_the_window_follows_the_first(gate):
    gate.arm("tab", 1_000_000)
    assert gate.follows("tab", 1_400_000)


def test_a_late_tap_is_a_plain_press(gate):
    gate.arm("tab", 1_000_000)
    assert not gate.follows("tab", 1_500_000)  # exactly the window: too late


def test_a_tap_on_something_else_never_follows(gate):
    gate.arm("tab", 1_000_000)
    assert not gate.follows("other tab", 1_100_000)


def test_a_third_tap_in_the_burst_is_a_plain_press_again(gate):
    gate.arm("tab", 1_000_000)
    assert gate.follows("tab", 1_100_000)
    assert not gate.follows("tab", 1_200_000)  # the double-tap was consumed


def test_arming_nothing_disarms(gate):
    gate.arm("tab", 1_000_000)
    gate.arm(None, 1_100_000)  # e.g. a Ctrl+J that hid the panel again
    assert not gate.follows("tab", 1_200_000)


def test_re_arming_restarts_the_window(gate):
    gate.arm("tab", 1_000_000)
    gate.arm("tab", 1_400_000)
    assert gate.follows("tab", 1_800_000)


def test_forgetting_the_armed_target_drops_it(gate):
    gate.arm("tab", 1_000_000)
    gate.forget("tab")  # its tab closed
    assert not gate.follows("tab", 1_100_000)


def test_forgetting_something_else_leaves_the_arming_alone(gate):
    gate.arm("tab", 1_000_000)
    gate.forget("other tab")
    assert gate.follows("tab", 1_100_000)


def test_identity_decides_not_equality(gate):
    """Two equal-but-distinct targets are two different things — the gate
    holds widgets, which compare by identity anyway."""
    gate.arm(["tab"], 1_000_000)
    assert not gate.follows(["tab"], 1_100_000)


def test_nothing_armed_follows_nothing(gate):
    assert not gate.follows("tab", 1_000_000)
