# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Unit tests for the tab-DnD constraint policy (tabguard)."""

from collins.tabguard import TabGuard


class View:
    """A stand-in tab view: the guard only ever uses identity."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - test diagnostics
        return f"<View {self.name}>"


def make_guard():
    guard = TabGuard()
    session = View("session")
    strip_a = View("strip-a")
    strip_b = View("strip-b")
    guard.register(session, "session")
    guard.register(strip_a, "dock-1")
    guard.register(strip_b, "dock-1")
    return guard, session, strip_a, strip_b


def test_same_group_transfer_passes():
    guard, _session, strip_a, strip_b = make_guard()
    page = object()
    guard.on_detached(strip_a, page)
    assert guard.should_bounce(strip_b, page) is None


def test_cross_group_drop_bounces_to_origin():
    guard, session, strip_a, _strip_b = make_guard()
    page = object()
    guard.on_detached(strip_a, page)
    verdict = guard.should_bounce(session, page)
    assert verdict == ("dock-1", strip_a)
    group, origin = verdict
    assert guard.bounce_target(group, prefer=origin) is strip_a


def test_bounce_target_prefers_origin_then_group_then_fallback():
    guard, _session, strip_a, strip_b = make_guard()
    assert guard.bounce_target("dock-1", prefer=strip_a) is strip_a
    guard.unregister(strip_a)
    assert guard.bounce_target("dock-1", prefer=strip_a) is strip_b
    guard.unregister(strip_b)
    rescue = View("rescue")
    guard.set_fallback("dock-1", lambda: rescue)
    assert guard.bounce_target("dock-1", prefer=strip_a) is rescue
    guard.clear_fallback("dock-1")
    assert guard.bounce_target("dock-1", prefer=strip_a) is None


def test_fresh_page_attach_is_not_judged():
    guard, session, _a, _b = make_guard()
    assert guard.should_bounce(session, object()) is None


def test_stale_close_record_does_not_misjudge_the_next_attach():
    guard, session, strip_a, _b = make_guard()
    closed = object()
    guard.on_detached(strip_a, closed)  # a close: no attach ever follows
    fresh = object()
    # The next attach is a different widget: the stale record must not
    # bounce it, and must survive for... nothing — it's simply ignored.
    assert guard.should_bounce(session, fresh) is None


def test_each_detach_judges_exactly_one_attach():
    guard, session, strip_a, _b = make_guard()
    page = object()
    guard.on_detached(strip_a, page)
    assert guard.should_bounce(session, page) is not None
    # The record is consumed: re-attaching the same widget elsewhere
    # later (e.g. the bounce's own suppressed transfer already ran) is
    # not judged again.
    assert guard.should_bounce(session, page) is None


def test_unregistered_destination_is_not_judged():
    guard, _session, strip_a, _b = make_guard()
    page = object()
    guard.on_detached(strip_a, page)
    assert guard.should_bounce(View("stranger"), page) is None


def test_detach_from_unregistered_view_records_nothing():
    guard, session, _a, _b = make_guard()
    page = object()
    guard.on_detached(View("stranger"), page)
    assert guard.should_bounce(session, page) is None


def test_suppression_blinds_both_sides():
    guard, session, strip_a, strip_b = make_guard()
    page = object()
    # A bounce transfer in flight: its detach/attach pair crosses groups
    # by construction and must not re-trigger.
    guard.suppressed = True
    guard.on_detached(session, page)
    assert guard.should_bounce(strip_a, page) is None
    guard.suppressed = False
    # And suppression didn't eat an earlier legitimate record.
    other = object()
    guard.on_detached(strip_a, other)
    guard.suppressed = True
    assert guard.should_bounce(session, other) is None
    guard.suppressed = False
    assert guard.should_bounce(session, other) == ("dock-1", strip_a)


def test_group_comparison_is_equality_not_identity():
    guard = TabGuard()
    a, b = View("a"), View("b")
    guard.register(a, ("dock", 1))
    guard.register(b, ("dock", 1))
    page = object()
    guard.on_detached(a, page)
    assert guard.should_bounce(b, page) is None
