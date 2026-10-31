# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Who owns a pty's size, as one client reads the service's word
(collins.sizerole; split-service spec §3.3, §3.24 "Two clients", PR-3.5)."""

import pytest

from collins import sizerole
from collins.api import protocol
from collins.sizerole import MOVED, PINNED, RELEASED, UNCHANGED, SizeRole


def test_a_fresh_role_is_active_sends_its_grid_and_pins_nothing():
    role = SizeRole()
    assert role.active and role.sends_grid and role.pinned is None and role.sized_for == ""


def test_an_attach_reply_saying_another_client_owns_the_size_pins_to_the_ptys_grid():
    role = SizeRole()
    assert role.apply({"cols": 100, "rows": 30, "active": False, "sized_for": "phone", "modes": []}) == PINNED
    assert not role.active and not role.sends_grid
    assert role.pinned == (100, 30) and role.sized_for == "phone"


def test_the_active_clients_attach_reply_changes_nothing():
    role = SizeRole()
    assert role.apply({"cols": 100, "rows": 30, "active": True, "sized_for": "laptop"}) == UNCHANGED
    assert role.active and role.pinned is None


def _pty(cols, rows, active, sized_for):
    return {"t": "pty", "pty": 3, "cols": cols, "rows": rows, "active": active, "sized_for": sized_for}


def test_a_pty_event_moves_the_pin_and_another_releases_it():
    role = SizeRole()
    role.apply(_pty(80, 24, False, "phone"))
    # The active client resized: same role, a new grid.
    assert role.apply(_pty(120, 40, False, "phone")) == MOVED
    assert role.pinned == (120, 40)
    # Another device took the size at the same grid.
    assert role.apply(_pty(120, 40, False, "tablet")) == MOVED
    # The same word again (the pid of a respawn, a cwd) is no change.
    assert role.apply(_pty(120, 40, False, "tablet")) == UNCHANGED
    # This client took it back.
    assert role.apply(_pty(120, 40, True, "laptop")) == RELEASED
    assert role.active and role.sends_grid and role.pinned is None and role.sized_for == ""


def test_a_message_that_does_not_say_active_leaves_the_role_alone():
    role = SizeRole()
    role.apply({"cols": 90, "rows": 20, "active": False, "sized_for": "phone"})
    for message in ({"cols": 10, "rows": 10}, {}, None, "pty", []):
        assert role.apply(message) == UNCHANGED
    assert role.pinned == (90, 20) and role.sized_for == "phone"


@pytest.mark.parametrize(
    "cols, rows",
    [
        (0, 24),
        (80, 0),
        (protocol.MAX_COLS + 1, 24),
        (80, protocol.MAX_ROWS + 1),
        ("80", 24),
        (True, 24),
        (None, None),
    ],
)
def test_a_grid_out_of_bounds_keeps_the_grid_the_role_had(cols, rows):
    role = SizeRole()
    role.apply({"cols": 90, "rows": 20, "active": False, "sized_for": "phone"})
    role.apply({"cols": cols, "rows": rows, "active": False, "sized_for": "phone"})
    assert role.pinned == (90, 20)
    # And a client that lost the size with no usable grid at all pins
    # nothing, yet sends no grid either: the service's word is the role.
    fresh = SizeRole()
    assert fresh.apply({"cols": cols, "rows": rows, "active": False}) == PINNED
    assert fresh.pinned is None and not fresh.sends_grid


def test_reset_is_a_fresh_attach():
    role = SizeRole()
    role.apply({"cols": 90, "rows": 20, "active": False, "sized_for": "phone"})
    role.reset()
    assert role.active and role.pinned is None and role.sized_for == ""


def test_a_sized_for_that_is_no_string_is_no_device_and_a_long_one_is_bounded():
    role = SizeRole()
    role.apply({"cols": 90, "rows": 20, "active": False, "sized_for": 7})
    assert role.sized_for == ""
    role.apply({"cols": 90, "rows": 20, "active": False, "sized_for": "x" * 1000})
    assert len(role.sized_for) == protocol.HOST_MAX


# -- the banner's words ---------------------------------------------------------------


def test_the_banner_names_the_device():
    assert sizerole.banner_title("phone") == "Sized for phone"


def test_the_banner_without_a_device_names_none():
    for device in ("", None, 7, "\x00\x1b‮", "   "):
        assert sizerole.banner_title(device) == "Sized for another device", repr(device)


def test_the_device_label_drops_what_draws_as_something_else():
    # Controls (an escape that would restyle a terminal, a newline that
    # would make two lines), bidi overrides and zero-width characters go;
    # runs of whitespace collapse; markup is kept as text (the banner shows
    # plain text, never markup).
    assert sizerole.device_label("lap\x1b[31mtop") == "lap[31mtop"
    assert sizerole.device_label("my\nbox\t two") == "my box two"
    assert sizerole.device_label("evil‮exe.txt") == "evilexe.txt"
    assert sizerole.device_label("zero​width") == "zerowidth"
    assert sizerole.device_label("<b>host</b> & co") == "<b>host</b> & co"
    assert sizerole.device_label("höst-ü") == "höst-ü"


def test_the_device_label_is_bounded_with_an_ellipsis():
    label = sizerole.device_label("a" * 200)
    assert len(label) == sizerole.LABEL_MAX and label.endswith("…")
    assert sizerole.device_label("a" * sizerole.LABEL_MAX) == "a" * sizerole.LABEL_MAX
