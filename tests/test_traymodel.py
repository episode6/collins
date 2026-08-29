import pytest

from collins import traymodel
from collins.traymodel import (
    ACTION_FOCUS,
    ACTION_NEW_WINDOW,
    ACTION_QUIT,
    ACTION_SHOW,
    MARKER_UNREAD,
    MARKER_WORKING,
    STATUS_ACTIVE,
    STATUS_ATTENTION,
    STATUS_PASSIVE,
    TraySession,
    badge_text,
    menu_entries,
    session_label,
    status_for,
    tooltip_for,
    tray_view,
)


def session(session_id: str, **overrides) -> TraySession:
    facts = dict(project="alpha", title="do the thing", last_active=0.0)
    facts.update(overrides)
    return TraySession(session_id=session_id, **facts)


def labels(entries) -> list[str]:
    return ["----" if entry.separator else entry.label for entry in entries]


def targets(entries) -> list[str]:
    return [entry.target for entry in entries if entry.action == ACTION_FOCUS]


# -- status -------------------------------------------------------------------


def test_status_escalates_with_what_is_open():
    assert status_for(0, 0) == STATUS_PASSIVE
    assert status_for(3, 0) == STATUS_ACTIVE
    assert status_for(3, 1) == STATUS_ATTENTION


def test_unread_always_wins_the_status():
    # An unread flag never outlives its tab, so this shouldn't arise — but a
    # badge on a Passive item is invisible (the host defers every property
    # change until it goes Active), so unread must never leave it Passive.
    assert status_for(0, 2) == STATUS_ATTENTION


def test_view_status_counts_placeholder_tabs():
    # A tab whose session id hasn't resolved is still a session on screen.
    assert tray_view([], placeholders=1).status == STATUS_ACTIVE
    # Its unread flag reaches the badge as the center's synthetic row.
    assert tray_view([], placeholders=1, unread=1).status == STATUS_ATTENTION


# -- the badge ----------------------------------------------------------------


def test_badge_is_empty_below_one():
    assert badge_text(0) == ""
    assert badge_text(-1) == ""


def test_badge_counts_up_to_nine_then_says_more():
    assert badge_text(1) == "1"
    assert badge_text(9) == "9"
    assert badge_text(10) == "9+"
    assert badge_text(999) == "9+"


def test_badge_is_the_number_it_is_given_never_the_sessions_flags():
    # The count is the notification center's (see notifycenter): a session's
    # unread flag decides its menu marker, never the badge. Three sessions,
    # two of them working: the badge means "waiting for you", so starting a
    # run must never move it — and it can't, since the app only reports a
    # green row for a session that isn't working.
    view = tray_view(
        [session("a", busy=True), session("b", busy=True), session("c", unread=True)], unread=1
    )
    assert view.badge == "1"
    assert (view.sessions, view.working, view.unread) == (3, 2, 1)
    # The flags alone light nothing.
    assert tray_view([session("c", unread=True)]).badge == ""


def test_badge_counts_what_no_session_list_can_see():
    # A message from a session whose tab has since closed still waits in the
    # history; the badge says so even with nothing open.
    view = tray_view([], unread=2)
    assert (view.badge, view.sessions, view.unread) == ("2", 0, 2)
    assert view.status == STATUS_ATTENTION
    assert view.tooltip == "Collins — no sessions open"


def test_flagged_session_that_goes_back_to_work_reads_as_working():
    # The flag itself stays up — the row keeps it, under the barber pole that
    # outranks the green pulse — but a session mid-turn is not waiting for
    # anyone, so the app reports no green for it until the run ends (see
    # App._sync_green, which asks session_marker).
    view = tray_view([session("a", unread=True, busy=True), session("b", unread=True)], unread=1)
    assert (view.badge, view.working, view.unread) == ("1", 1, 1)
    assert view.tooltip == "Collins — 2 sessions, 1 working, 1 unread"
    assert view.status == STATUS_ATTENTION
    assert [e.marker for e in view.menu if e.action == ACTION_FOCUS] == [MARKER_WORKING, MARKER_UNREAD]


def test_badge_and_status_clear_when_every_flag_is_under_a_pole():
    view = tray_view([session("a", unread=True, busy=True)], unread=0)
    assert (view.badge, view.unread, view.working) == ("", 0, 1)
    assert view.status == STATUS_ACTIVE


def test_badge_counts_placeholder_rows_through_the_same_number():
    view = tray_view([session("a", unread=True)], placeholders=2, unread=3)
    assert view.badge == "3"
    assert view.sessions == 3


def test_a_negative_unread_reads_as_none():
    view = tray_view([session("a")], unread=-2)
    assert (view.badge, view.unread, view.status) == ("", 0, STATUS_ACTIVE)
    assert tray_view([], unread=-1).status == STATUS_PASSIVE


# -- hidden windows -----------------------------------------------------------


def test_hidden_windows_hold_the_item_active():
    # The icon is a hidden window's only reliable way back, and a host may
    # take a Passive item out of its panel — so hiddenness alone keeps it up,
    # even with nothing countable open.
    assert status_for(0, 0, hidden_windows=True) == STATUS_ACTIVE
    assert tray_view([], hidden_windows=True).status == STATUS_ACTIVE


def test_hidden_windows_never_downgrade_attention():
    assert status_for(0, 1, hidden_windows=True) == STATUS_ATTENTION
    assert tray_view([session("a", unread=True)], unread=1, hidden_windows=True).status == STATUS_ATTENTION


def test_a_hidden_window_reads_active_by_its_session_count_alone():
    # A hidden window's tabs stay in the aggregate (App.tray_view walks every
    # window, on screen or not), so the ordinary count already answers
    # "sessions open but nothing on screen" — the flag is belt and braces.
    view = tray_view([session("a", busy=True)], hidden_windows=True)
    assert view.status == STATUS_ACTIVE
    assert (view.sessions, view.working) == (1, 1)


def test_menu_first_row_names_the_hidden_state():
    assert labels(menu_entries([], hidden_windows=True))[0] == "Show Collins (Hidden)"
    # Nothing hidden: the row is a plain raise and keeps its plain name.
    assert labels(menu_entries([]))[0] == "Show Collins"


def test_hidden_first_row_still_dispatches_show():
    entries = menu_entries([session("a")], hidden_windows=True)
    first = next(e for e in entries if not e.separator)
    assert (first.label, first.action) == ("Show Collins (Hidden)", ACTION_SHOW)


def test_view_menu_carries_the_hidden_first_row():
    view = tray_view([session("a")], hidden_windows=True)
    assert view.menu[0].label == "Show Collins (Hidden)"
    assert tray_view([session("a")]).menu[0].label == "Show Collins"


# -- the tooltip --------------------------------------------------------------


def test_tooltip_with_nothing_open():
    assert tooltip_for(0, 0, 0) == "Collins — no sessions open"


def test_tooltip_drops_the_zero_clauses():
    assert tooltip_for(2, 0, 0) == "Collins — 2 sessions"
    assert tooltip_for(2, 1, 0) == "Collins — 2 sessions, 1 working"
    assert tooltip_for(2, 0, 1) == "Collins — 2 sessions, 1 unread"


def test_tooltip_pluralizes_every_clause():
    assert tooltip_for(1, 1, 1) == "Collins — 1 session, 1 working, 1 unread"
    assert tooltip_for(3, 2, 2) == "Collins — 3 sessions, 2 working, 2 unread"


def test_tooltip_names_the_instance_it_was_given():
    # A debug build says so, rather than being indistinguishable from the real
    # item sitting next to it in the panel.
    assert tooltip_for(0, 0, 0, name="Collins (debug)").startswith("Collins (debug) — ")


def test_view_tooltip_carries_both_numbers():
    view = tray_view([session("a", busy=True), session("b", unread=True)], unread=1)
    assert view.tooltip == "Collins — 2 sessions, 1 working, 1 unread"


# -- the menu -----------------------------------------------------------------


def test_menu_without_sessions_collapses_its_separators():
    entries = menu_entries([])
    assert labels(entries) == ["Show Collins", "----", "New window", "Quit"]
    # Ids are handed out after the collapse, so they stay contiguous.
    assert [entry.id for entry in entries] == [1, 2, 3, 4]


def test_menu_with_sessions_keeps_both_separators():
    entries = menu_entries([session("a", project="alpha", title="refactor store")])
    assert labels(entries) == [
        "Show Collins",
        "----",
        "alpha — refactor store",
        "----",
        "New window",
        "Quit",
    ]


def test_menu_rows_dispatch_the_actions_they_should():
    entries = menu_entries([session("sess-1")])
    assert [(e.action, e.target) for e in entries if not e.separator] == [
        (ACTION_SHOW, ""),
        (ACTION_FOCUS, "sess-1"),
        (ACTION_NEW_WINDOW, ""),
        (ACTION_QUIT, ""),
    ]


def test_menu_orders_rows_most_recently_active_first():
    sessions = [
        session("old", last_active=10.0),
        session("newest", last_active=30.0),
        session("middle", last_active=20.0),
    ]
    assert targets(menu_entries(sessions)) == ["newest", "middle", "old"]


def test_menu_order_is_stable_between_rebuilds():
    # Tabs opened in the same second must not shuffle when the menu is rebuilt.
    sessions = [session("b", last_active=1.0), session("a", last_active=1.0)]
    assert targets(menu_entries(sessions)) == ["a", "b"]


def test_menu_caps_the_jump_list():
    sessions = [session(f"s{i}", last_active=float(i)) for i in range(12)]
    rows = targets(menu_entries(sessions))
    assert len(rows) == traymodel.SESSION_ROW_CAP
    # The cap takes the most recent, not the first ones handed over.
    assert rows == ["s11", "s10", "s9", "s8", "s7", "s6", "s5", "s4"]


def test_menu_marks_working_and_unread_rows():
    entries = menu_entries(
        [
            session("working", busy=True, last_active=3.0),
            session("unread", unread=True, last_active=2.0),
            session("idle", last_active=1.0),
        ]
    )
    markers = [entry.marker for entry in entries if entry.action == ACTION_FOCUS]
    assert markers == [MARKER_WORKING, MARKER_UNREAD, ""]


def test_a_working_row_that_is_also_unread_reads_as_working():
    entries = menu_entries([session("both", busy=True, unread=True)])
    assert [e.marker for e in entries if e.action == ACTION_FOCUS] == [MARKER_WORKING]


def test_placeholder_tabs_get_no_row():
    # There is no session id to jump to yet, so a row for one would go nowhere.
    view = tray_view([], placeholders=2, unread=1)
    assert targets(view.menu) == []
    assert view.sessions == 2


# -- row labels ---------------------------------------------------------------


def test_label_joins_project_and_title():
    assert session_label(session("a", project="alpha", title="refactor store")) == (
        "alpha — refactor store"
    )


def test_label_drops_a_missing_half():
    assert session_label(session("a", project="alpha", title="")) == "alpha"
    assert session_label(session("a", project="", title="refactor store")) == "refactor store"


def test_label_falls_back_to_the_session_id():
    assert session_label(session("sess-1", project="  ", title="")) == "sess-1"


def test_label_truncates_a_whole_first_message():
    long = session("a", project="alpha", title="fix the thing " * 20)
    label = session_label(long)
    assert len(label) <= traymodel.LABEL_MAX
    assert label.endswith("…")
    assert label.startswith("alpha — fix the thing")


def test_tokened_action_brackets_the_action_with_its_token():
    calls = []
    run = traymodel.tokened_action(
        lambda: calls.append("action"), "token-a", calls.append
    )
    assert calls == []  # nothing delivered at wrap time
    run()
    assert calls == ["token-a", "action", ""]


def test_tokened_action_withdraws_the_token_when_the_action_raises():
    calls = []

    def explode():
        raise RuntimeError("boom")

    run = traymodel.tokened_action(explode, "token-a", calls.append)
    with pytest.raises(RuntimeError):
        run()
    assert calls == ["token-a", ""]


def test_tokened_action_without_a_token_or_receiver_stays_bare():
    def action():
        pass

    assert traymodel.tokened_action(action, "", list().append) is action
    assert traymodel.tokened_action(action, "token-a", None) is action
