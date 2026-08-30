import os
from pathlib import Path

import pytest

from collins import notifycenter
from collins.notifycenter import (
    DELIVER_BEEP,
    DELIVER_CARD,
    DELIVER_DESKTOP,
    DELIVER_FLAG,
    DELIVER_FLASH,
    DELIVER_ROW,
    DELIVER_ROW_READ,
    DELIVER_SOUND,
    DELIVERIES,
    FOCUS_ELSEWHERE,
    FOCUS_SELECTED,
    FOCUS_UNFOCUSED,
    KEEP_SECONDS,
    KIND_BELL,
    KIND_FINISHED,
    KIND_MESSAGE,
    ROW_CAP,
    Notification,
    NotificationCenter,
    clean_records,
    delivery,
    green_id,
)

NOW = 1_800_000_000.0
ROOT = Path(__file__).resolve().parent.parent


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def center(records=None, clock=None) -> tuple[NotificationCenter, list]:
    """A center with a fixed clock and a listener counting its changes."""
    clock = clock or Clock()
    c = NotificationCenter(records, clock=clock)
    calls: list[int] = []
    c.connect(lambda: calls.append(c.unread_count()))
    return c, calls


def message(c: NotificationCenter, session_id="s1", body="Look at this", title="Fix it"):
    return c.post(c.make(KIND_MESSAGE, session_id, title, "alpha", body))


def bell(c: NotificationCenter, session_id="s1", title="Build"):
    return c.post(c.make(KIND_BELL, session_id, title, "alpha", "Rang the bell"))


# -- posting ------------------------------------------------------------------


def test_post_puts_the_newest_first_and_announces():
    c, calls = center()
    first = message(c, body="one")
    second = message(c, body="two")
    assert [row.id for row in c.rows()] == [second.id, first.id]
    assert first.id != second.id
    assert c.unread_count() == 2
    assert calls == [1, 2]


def test_make_stamps_the_clock_and_the_kind():
    clock = Clock(123.0)
    c, _ = center(clock=clock)
    row = message(c)
    assert (row.when, row.kind, row.read, row.count) == (123.0, KIND_MESSAGE, False, 1)
    assert (row.session_id, row.title, row.project) == ("s1", "Fix it", "alpha")


def test_messages_never_coalesce():
    c, _ = center()
    message(c, body="one")
    message(c, body="two")
    assert [row.body for row in c.rows()] == ["two", "one"]


def test_bells_coalesce_onto_an_unread_bell_of_the_same_session():
    clock = Clock()
    c, calls = center(clock=clock)
    first = bell(c)
    clock.now += 10
    again = bell(c)
    assert again is first
    assert len(c.rows()) == 1
    assert (first.count, first.when) == (2, NOW + 10)
    assert c.unread_count() == 1
    assert calls == [1, 1]  # both announced; the count didn't move


def test_a_coalesced_bell_comes_back_to_the_top():
    c, _ = center()
    first = bell(c)
    newer = message(c, session_id="s2")
    bell(c)
    assert [row.id for row in c.rows()] == [first.id, newer.id]


def test_bells_of_different_sessions_stay_apart():
    c, _ = center()
    bell(c, "s1")
    bell(c, "s2")
    assert len(c.rows()) == 2


def test_a_read_bell_is_not_bumped():
    # The user dealt with the last one; this is a new one to look at.
    c, _ = center()
    first = bell(c)
    c.mark_read(first.id)
    second = bell(c)
    assert second is not first
    assert [row.count for row in c.rows()] == [1, 1]
    assert c.unread_count() == 1


def test_a_bell_without_a_session_never_coalesces():
    c, _ = center()
    bell(c, session_id="")
    bell(c, session_id="")
    assert len(c.rows()) == 2


def test_post_refuses_synthetic_rows_and_unknown_kinds():
    c, _ = center()
    with pytest.raises(ValueError):
        c.post(c.make(KIND_FINISHED, "s1", "t", "p", "Finished a run"))
    with pytest.raises(ValueError):
        c.post(c.make("shout", "s1", "t", "p", "hey"))


# -- reading ------------------------------------------------------------------


def test_mark_read_moves_one_row_once():
    c, calls = center()
    row = message(c)
    assert c.mark_read(row.id)
    assert row.read
    assert c.unread_count() == 0
    assert not c.mark_read(row.id)  # already read: no announcement either
    assert not c.mark_read("nope")
    assert calls == [1, 0]


def test_mark_session_read_is_visiting_the_session():
    c, _ = center()
    message(c, "s1")
    bell(c, "s1")
    other = message(c, "s2")
    assert c.mark_session_read("s1") == 2
    assert c.unread_count() == 1
    assert not other.read
    assert c.mark_session_read("s1") == 0
    # Rows with no session id can't be visited by id.
    message(c, "")
    assert c.mark_session_read("") == 0


def test_mark_all_read_takes_the_synthetic_rows_too():
    c, _ = center()
    message(c)
    c.set_green("s2", True, title="t", project="p")
    assert c.mark_all_read() == 2
    assert c.unread_count() == 0
    assert c.is_green("s2")  # the row stays until its green goes
    assert c.mark_all_read() == 0


# -- synthetic rows -----------------------------------------------------------


def test_set_green_inserts_one_unread_row_at_the_top_and_removes_it():
    c, calls = center()
    message(c, "s1")
    assert c.set_green("s2", True, title="Refactor", project="beta")
    row = c.rows()[0]
    assert (row.id, row.session_id, row.kind) == (green_id("s2"), "s2", KIND_FINISHED)
    assert (row.title, row.project, row.body, row.read) == ("Refactor", "beta", "Finished a run", False)
    assert c.unread_count() == 2
    assert c.is_green("s2")
    assert c.set_green("s2", False)
    assert not c.is_green("s2")
    assert c.unread_count() == 1
    assert calls == [1, 2, 1]


def test_set_green_is_idempotent_and_silent_on_non_events():
    c, calls = center()
    assert not c.set_green("s1", False)
    assert c.set_green("s1", True)
    assert not c.set_green("s1", True)
    assert c.set_green("s1", False)
    assert not c.set_green("", True)
    assert calls == [1, 0]


def test_placeholder_to_real_row_handoff_leaves_the_count_steady():
    # A first turn finished before the store discovered the session: the
    # placeholder's row goes down and the session's comes up, and the badge
    # reads 1 before and after.
    c, calls = center()
    c.set_green("placeholder-1", True, title="New Thread", project="alpha")
    assert c.unread_count() == 1
    c.set_green("placeholder-1", False)
    c.set_green("sess-1", True, title="Fix the bug", project="alpha")
    assert c.unread_count() == 1
    assert c.green_sessions() == ["sess-1"]
    assert calls == [1, 0, 1]  # the dip lives between two synchronous calls


def test_rekey_session_moves_the_placeholders_rows_to_the_session():
    # A tab spoke twice under its placeholder id before the store discovered
    # its session; the handoff moves both rows so a click and a visit find
    # them, and the synthetic row is left to set_green.
    c, calls = center()
    msg = message(c, "placeholder-1")
    rung = bell(c, "placeholder-1")
    other = message(c, "s2")
    c.set_green("placeholder-1", True)
    assert c.rekey_session("placeholder-1", "sess-1") == 2
    assert msg.session_id == "sess-1" and rung.session_id == "sess-1"
    assert other.session_id == "s2"
    assert c.green_sessions() == ["placeholder-1"]
    assert calls[-1] == 4  # announced once, the count unmoved
    assert c.mark_session_read("sess-1") == 2
    assert c.unread_sessions() == {"s2", "placeholder-1"}


def test_rekey_session_moves_nothing_for_no_op_keys():
    c, calls = center()
    message(c, "placeholder-1")
    announced = len(calls)
    assert c.rekey_session("placeholder-1", "placeholder-1") == 0
    assert c.rekey_session("", "sess-1") == 0
    assert c.rekey_session("placeholder-1", "") == 0
    assert c.rekey_session("nobody", "sess-1") == 0
    assert len(calls) == announced


def test_unread_sessions_and_has_unread_follow_the_unread_rows():
    c, _ = center()
    msg = message(c, "s1")
    bell(c, "s2")
    c.set_green("s3", True)
    c.post(c.make(KIND_MESSAGE, "", "Untitled", "", "no session"))
    assert c.unread_sessions() == {"s1", "s2", "s3"}
    assert c.has_unread("s1") and c.has_unread("s3") and not c.has_unread("")
    c.mark_read(msg.id)
    assert c.unread_sessions() == {"s2", "s3"}
    assert not c.has_unread("s1")
    c.set_green("s3", False)
    c.mark_all_read()
    assert c.unread_sessions() == frozenset()


def test_green_sessions_lists_the_keys_standing():
    c, _ = center()
    message(c, "s1")
    c.set_green("a", True)
    c.set_green("b", True)
    assert c.green_sessions() == ["b", "a"]


# -- clearing -----------------------------------------------------------------


def test_clear_drops_everything_but_the_synthetic_rows():
    c, calls = center()
    read = message(c, "s1")
    c.mark_read(read.id)
    message(c, "s2")
    c.set_green("s3", True)
    assert c.clear() == 2
    assert [row.id for row in c.rows()] == [green_id("s3")]
    assert c.unread_count() == 1
    assert c.clear() == 0
    assert calls[-1] == 1


def test_remove_drops_one_row_but_never_a_synthetic_one():
    c, _ = center()
    row = message(c)
    c.set_green("s2", True)
    assert not c.remove(green_id("s2"))
    assert c.remove(row.id)
    assert not c.remove(row.id)
    assert [r.id for r in c.rows()] == [green_id("s2")]


# -- bounds -------------------------------------------------------------------


def test_the_cap_drops_the_oldest_persistable_rows_and_spares_the_green():
    clock = Clock()
    c, _ = center(clock=clock)
    c.set_green("g", True)
    for i in range(ROW_CAP + 5):
        clock.now += 1
        message(c, body=str(i))
    rows = c.rows()
    assert len(rows) == ROW_CAP + 1
    assert rows[0].body == str(ROW_CAP + 4)
    assert rows[-1].id == green_id("g")  # the oldest row of all, kept
    assert rows[-2].body == "5"  # 0..4 went
    assert len(c.to_records()) == ROW_CAP


def test_clean_records_prunes_a_fortnight_back_and_sorts_newest_first():
    raw = [
        {"id": "old", "kind": "message", "when": NOW - KEEP_SECONDS - 1},
        {"id": "kept", "kind": "message", "when": NOW - KEEP_SECONDS + 60},
        {"id": "new", "kind": "bell", "when": NOW - 5, "count": 3, "read": True},
    ]
    cleaned = clean_records(raw, now=NOW)
    assert [row["id"] for row in cleaned] == ["new", "kept"]
    assert (cleaned[0]["count"], cleaned[0]["read"]) == (3, True)


def test_clean_records_tolerates_garbage():
    raw = [
        "not a row",
        None,
        {"id": "", "kind": "message", "when": NOW},  # no id
        {"id": "x", "kind": "shout", "when": NOW},  # unknown kind
        {"id": "y", "kind": "message", "when": "yesterday"},  # bad time
        {"id": "z", "kind": "message", "when": True},  # bools are not times
        {"id": "green:s", "kind": "finished", "when": NOW},  # never persisted
        {"id": "dup", "kind": "message", "when": NOW - 1},  # the older copy, first
        {"id": "dup", "kind": "message", "when": NOW},
        {"id": "ok", "kind": "message", "when": NOW - 2, "title": 7, "count": 0, "read": 1},
    ]
    cleaned = clean_records(raw, now=NOW)
    assert [row["id"] for row in cleaned] == ["dup", "ok"]
    assert cleaned[0]["when"] == NOW  # two rows under one id: the newer wins, not the first
    ok = cleaned[1]
    assert (ok["title"], ok["count"], ok["read"], ok["session_id"]) == ("", 1, True, "")
    assert clean_records("nope") == []
    assert clean_records(None) == []


def test_clean_records_caps_the_list():
    raw = [{"id": str(i), "kind": "message", "when": NOW - i} for i in range(ROW_CAP + 10)]
    cleaned = clean_records(raw, now=NOW)
    assert len(cleaned) == ROW_CAP
    assert cleaned[0]["id"] == "0"


# -- persistence --------------------------------------------------------------


def test_records_round_trip_through_a_fresh_center():
    clock = Clock()
    c, _ = center(clock=clock)
    read = message(c, "s1", body="seen")
    c.mark_read(read.id)
    clock.now += 1
    bell(c, "s2")
    bell(c, "s2")
    c.set_green("s3", True)

    records = c.to_records()
    assert [r["kind"] for r in records] == [KIND_BELL, KIND_MESSAGE]  # no synthetic row
    again = NotificationCenter(records, clock=clock)
    rows = again.rows()
    assert [(r.id, r.kind, r.read, r.count) for r in rows] == [
        (rows[0].id, KIND_BELL, False, 2),
        (read.id, KIND_MESSAGE, True, 1),
    ]
    assert again.unread_count() == 1
    assert again.to_records() == records


def test_loading_prunes_and_ignores_garbage():
    c, _ = center(
        records=[
            {"id": "stale", "kind": "message", "when": NOW - KEEP_SECONDS - 1},
            {"id": "live", "kind": "message", "when": NOW - 1},
            42,
        ]
    )
    assert [row.id for row in c.rows()] == ["live"]


def test_notification_record_round_trip():
    row = Notification("id", "s", "t", "p", KIND_BELL, "b", 1.0, read=True, count=4)
    assert Notification.from_record(row.to_record()) == row
    assert Notification.from_record("nope") is None


# -- listeners ----------------------------------------------------------------


def test_listeners_connect_once_and_disconnect():
    c = NotificationCenter(clock=Clock())
    calls = []

    def listen():
        calls.append(1)

    assert c.connect(listen) is listen
    c.connect(listen)  # a second connect is not a second call
    message(c)
    assert calls == [1]
    c.disconnect(listen)
    c.disconnect(listen)  # harmless
    message(c)
    assert calls == [1]


# -- the delivery table -------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "focus", "expected"),
    [
        (KIND_MESSAGE, FOCUS_SELECTED, {DELIVER_ROW_READ, DELIVER_FLASH}),
        (
            KIND_MESSAGE,
            FOCUS_ELSEWHERE,
            {DELIVER_CARD, DELIVER_SOUND, DELIVER_ROW, DELIVER_FLAG, DELIVER_FLASH},
        ),
        (KIND_MESSAGE, FOCUS_UNFOCUSED, {DELIVER_DESKTOP, DELIVER_ROW, DELIVER_FLAG}),
        (KIND_BELL, FOCUS_SELECTED, {DELIVER_BEEP, DELIVER_FLASH}),
        (KIND_BELL, FOCUS_ELSEWHERE, {DELIVER_CARD, DELIVER_SOUND, DELIVER_ROW, DELIVER_FLASH}),
        (KIND_BELL, FOCUS_UNFOCUSED, {DELIVER_DESKTOP, DELIVER_ROW, DELIVER_FLASH}),
        (KIND_FINISHED, FOCUS_SELECTED, set()),
        (KIND_FINISHED, FOCUS_ELSEWHERE, {DELIVER_ROW}),
        (KIND_FINISHED, FOCUS_UNFOCUSED, {DELIVER_ROW}),
    ],
)
def test_delivery_table(kind, focus, expected):
    result = delivery(kind, focus)
    assert result == frozenset(expected)
    assert result <= DELIVERIES


@pytest.mark.parametrize(
    ("focus", "expected"),
    [
        (FOCUS_SELECTED, set()),  # a selected tab never goes green
        (FOCUS_ELSEWHERE, {DELIVER_CARD, DELIVER_SOUND, DELIVER_ROW, DELIVER_FLAG, DELIVER_FLASH}),
        (FOCUS_UNFOCUSED, {DELIVER_DESKTOP, DELIVER_ROW, DELIVER_FLAG}),
    ],
)
def test_announce_finished_runs_routes_a_finish_as_a_message(focus, expected):
    assert delivery(KIND_FINISHED, focus, announce_finished_runs=True) == frozenset(expected)


def test_announce_finished_runs_changes_nothing_else():
    for kind in (KIND_MESSAGE, KIND_BELL):
        for focus in (FOCUS_SELECTED, FOCUS_ELSEWHERE, FOCUS_UNFOCUSED):
            assert delivery(kind, focus, announce_finished_runs=True) == delivery(kind, focus)


def test_the_sound_only_ever_plays_beside_a_card():
    # Desktop notifications are sounded by the desktop; ours on top would
    # ring twice.
    for kind in notifycenter.KINDS:
        for focus in notifycenter.FOCUSES:
            for announce in (False, True):
                result = delivery(kind, focus, announce)
                assert (DELIVER_SOUND in result) <= (DELIVER_CARD in result)
                assert not ({DELIVER_CARD, DELIVER_DESKTOP} <= result)


def test_delivery_refuses_unknown_inputs():
    with pytest.raises(ValueError):
        delivery("shout", FOCUS_SELECTED)
    with pytest.raises(ValueError):
        delivery(KIND_BELL, "asleep")


# -- what the bell and the sheet say ------------------------------------------


@pytest.mark.parametrize(
    "unread, text",
    [
        (0, "Notifications"),
        (-1, "Notifications"),
        (1, "1 unread notification"),
        (3, "3 unread notifications"),
    ],
)
def test_bell_tooltip(unread, text):
    assert notifycenter.bell_tooltip(unread) == text


@pytest.mark.parametrize(
    "age, text",
    [
        (0, "just now"),
        (9, "just now"),
        (-30, "just now"),  # a clock that went backwards is not a negative age
        (12, "12s ago"),
        (59, "59s ago"),
        (60, "1m ago"),
        (6 * 60 + 30, "6m ago"),
        (2 * 3600, "2h ago"),
        (23 * 3600 + 59 * 60, "23h ago"),
        (86400, "yesterday"),
        (2 * 86400 - 1, "yesterday"),
        (3 * 86400, "3d ago"),
        (6 * 86400 + 3600, "6d ago"),
    ],
)
def test_relative_time(age, text):
    assert notifycenter.relative_time(NOW - age, now=NOW) == text


def test_relative_time_past_a_week_is_the_date():
    import time

    when = NOW - 8 * 86400
    assert notifycenter.relative_time(when, now=NOW) == time.strftime("%Y-%m-%d", time.localtime(when))


def test_row_body_counts_a_coalesced_bell_only():
    c, _ = center()
    row = c.post(c.make(KIND_BELL, "s", "T", "P", "Rang the bell"))
    assert notifycenter.row_body(row) == "Rang the bell"
    c.post(c.make(KIND_BELL, "s", "T", "P", "Rang the bell"))
    c.post(c.make(KIND_BELL, "s", "T", "P", "Rang the bell"))
    assert notifycenter.row_body(row) == "Rang the bell ×3"
    message = c.post(c.make(KIND_MESSAGE, "s", "T", "P", "Look at this"))
    assert notifycenter.row_body(message) == "Look at this"


def test_split_rows_keeps_each_half_in_order():
    clock = Clock()
    c, _ = center(clock=clock)
    ids = []
    for i in range(4):
        clock.now += 1
        ids.append(c.post(c.make(KIND_MESSAGE, f"s{i}", "T", "P", "m")).id)
    c.mark_read(ids[1])
    c.mark_read(ids[3])
    unread, earlier = notifycenter.split_rows(c.rows())
    assert [r.id for r in unread] == [ids[2], ids[0]]
    assert [r.id for r in earlier] == [ids[3], ids[1]]
    assert notifycenter.split_rows([]) == ([], [])


@pytest.mark.parametrize(
    "value, name",
    [
        (None, "Default"),
        ("", "Default"),
        ("default", "Default"),
        ("none", "None"),
        ("/home/me/sounds/chime.ogg", "chime.ogg"),
    ],
)
def test_sound_display_name(value, name):
    assert notifycenter.sound_display_name(value) == name


# -- where the user is, and what the switches do ---------------------------------


@pytest.mark.parametrize(
    ("any_active", "tab_window_active", "tab_selected", "expected"),
    [
        (True, True, True, FOCUS_SELECTED),
        (True, True, False, FOCUS_ELSEWHERE),  # another tab in the active window
        (True, False, True, FOCUS_ELSEWHERE),  # the selected tab of a window that isn't active
        (True, False, False, FOCUS_ELSEWHERE),
        (False, False, True, FOCUS_UNFOCUSED),  # nothing on screen is Collins
        (False, False, False, FOCUS_UNFOCUSED),
    ],
)
def test_focus_state(any_active, tab_window_active, tab_selected, expected):
    assert notifycenter.focus_state(any_active, tab_window_active, tab_selected) == expected


def test_without_cards_turns_the_card_and_its_sound_into_a_desktop_notification():
    elsewhere = delivery(KIND_MESSAGE, FOCUS_ELSEWHERE)
    swapped = notifycenter.without_cards(elsewhere)
    assert DELIVER_CARD not in swapped and DELIVER_SOUND not in swapped
    assert DELIVER_DESKTOP in swapped
    assert swapped - {DELIVER_DESKTOP} == elsewhere - {DELIVER_CARD, DELIVER_SOUND}


def test_without_cards_leaves_a_delivery_with_no_card_alone():
    for kind in (KIND_MESSAGE, KIND_BELL):
        for focus in (FOCUS_SELECTED, FOCUS_UNFOCUSED):
            assert notifycenter.without_cards(delivery(kind, focus)) == delivery(kind, focus)


def test_tool_reply_names_where_the_message_went():
    assert notifycenter.tool_reply(delivery(KIND_MESSAGE, FOCUS_ELSEWHERE)) == notifycenter.REPLY_IN_APP
    assert notifycenter.tool_reply(delivery(KIND_MESSAGE, FOCUS_UNFOCUSED)) == notifycenter.REPLY_DESKTOP
    assert notifycenter.tool_reply(delivery(KIND_MESSAGE, FOCUS_SELECTED)) == notifycenter.REPLY_SELECTED
    # With cards off, "elsewhere" is a desktop notification and says so.
    swapped = notifycenter.without_cards(delivery(KIND_MESSAGE, FOCUS_ELSEWHERE))
    assert notifycenter.tool_reply(swapped) == notifycenter.REPLY_DESKTOP


def test_tool_replies_are_the_three_the_spec_promises():
    assert notifycenter.REPLY_IN_APP == "The user was notified in Collins."
    assert notifycenter.REPLY_DESKTOP == "The user was notified on their desktop."
    assert notifycenter.REPLY_SELECTED == (
        "The user is looking at this session; the message is in their notification history."
    )


def test_bell_body_is_the_bell_rows_text():
    assert notifycenter.bell_body() == "Rang the bell"


# -- the sound ----------------------------------------------------------------


def test_sound_display_names():
    assert notifycenter.sound_display_name(None) == "Default"
    assert notifycenter.sound_display_name("") == "Default"
    assert notifycenter.sound_display_name(notifycenter.SOUND_DEFAULT) == "Default"
    assert notifycenter.sound_display_name(notifycenter.SOUND_NONE) == "None"
    assert notifycenter.sound_display_name("/home/me/sounds/chime.ogg") == "chime.ogg"


def test_sound_subtitle_describes_the_choice():
    assert notifycenter.sound_subtitle("default") == "Default: the desktop's message sound"
    assert notifycenter.sound_subtitle("") == "Default: the desktop's message sound"
    assert notifycenter.sound_subtitle("none") == "Silent"
    assert notifycenter.sound_subtitle("/home/me/chime.ogg", home="/home/me") == "~/chime.ogg"
    assert notifycenter.sound_subtitle("/usr/share/x.ogg", home="/home/me") == "/usr/share/x.ogg"
    assert notifycenter.sound_subtitle("/home/meow/x.ogg", home="/home/me") == "/home/meow/x.ogg"


def test_sound_is_silent_only_for_none():
    assert notifycenter.sound_is_silent("none")
    assert not notifycenter.sound_is_silent("default")
    assert not notifycenter.sound_is_silent("")
    assert not notifycenter.sound_is_silent("/x.ogg")


def test_default_sound_walks_the_desktops_theme_then_freedesktop():
    assert notifycenter.sound_candidates("Yaru") == [
        "/usr/share/sounds/Yaru/stereo/message-new-instant.oga",
        "/usr/share/sounds/freedesktop/stereo/message-new-instant.oga",
    ]
    # No theme, the fallback theme itself, or a theme name that is a path
    # (a hand-edited key): the freedesktop theme alone.
    for theme in ("", "freedesktop", "../etc"):
        assert notifycenter.sound_candidates(theme) == [
            "/usr/share/sounds/freedesktop/stereo/message-new-instant.oga"
        ]


def test_sound_file_resolves_default_at_play_time():
    present = {"/usr/share/sounds/freedesktop/stereo/message-new-instant.oga"}
    exists = present.__contains__
    assert notifycenter.sound_file("default", "Yaru", exists) == (
        "/usr/share/sounds/freedesktop/stereo/message-new-instant.oga"
    )
    present.add("/usr/share/sounds/Yaru/stereo/message-new-instant.oga")
    assert notifycenter.sound_file("default", "Yaru", exists) == (
        "/usr/share/sounds/Yaru/stereo/message-new-instant.oga"
    )
    # A minimal install with no theme at all: nothing to play, the beep.
    assert notifycenter.sound_file("default", "Yaru", lambda _p: False) == ""


def test_sound_file_for_a_custom_path_and_silence():
    exists = {"/home/me/chime.ogg"}.__contains__
    assert notifycenter.sound_file("/home/me/chime.ogg", "", exists) == "/home/me/chime.ogg"
    assert notifycenter.sound_file("/home/me/gone.ogg", "", exists) == ""  # deleted: the beep
    assert notifycenter.sound_file("chime.ogg", "", lambda _p: True) == ""  # relative: refused
    assert notifycenter.sound_file("none", "Yaru", lambda _p: True) == ""


def test_sound_choices_are_the_pickers_rows_in_order():
    values = [value for value, _label in notifycenter.sound_choices()]
    assert values[:2] == ["default", "none"]
    assert values[2:6] == ["theme:bell", "theme:complete", "theme:message", "theme:dialog-information"]
    assert values[6:] == [
        "bundled:zen",
        "bundled:soft",
        "bundled:glass",
        "bundled:confirmation",
        "bundled:pluck",
    ]
    labels = [label for _value, label in notifycenter.sound_choices()]
    assert labels == [
        "Default", "None", "Bell", "Complete", "Message", "Information",
        "Zen", "Soft", "Glass", "Confirmation", "Pluck",
    ]  # fmt: skip
    # No two rows can read the same, or the combo can't tell them apart.
    assert len(set(labels)) == len(labels)


def test_theme_and_bundled_values_are_named_and_described():
    assert notifycenter.sound_display_name("theme:bell") == "Bell"
    assert notifycenter.sound_display_name("bundled:zen") == "Zen"
    assert notifycenter.sound_subtitle("theme:complete") == "The desktop's “complete” sound"
    assert notifycenter.sound_subtitle("bundled:pluck") == (
        "Ships with Collins: Kenney Interface Sounds, pluck_002 (CC0)"
    )
    # An event or a name Collins doesn't know (a setting from another build)
    # is neither: it reads like a file, and resolves like a missing one.
    assert notifycenter.sound_theme_event("theme:trash-empty") is None
    assert notifycenter.sound_bundled_name("bundled:gong") is None
    assert notifycenter.sound_display_name("theme:trash-empty") == "theme:trash-empty"
    assert notifycenter.sound_file("theme:trash-empty", "Yaru", lambda _p: True) == ""
    assert notifycenter.sound_file("bundled:gong", "Yaru", lambda _p: True) == ""
    assert not notifycenter.sound_is_silent("theme:bell")
    assert not notifycenter.sound_is_silent("bundled:zen")


def test_a_theme_event_walks_the_themes_like_default_does():
    assert notifycenter.sound_candidates("Yaru", "bell") == [
        "/usr/share/sounds/Yaru/stereo/bell.oga",
        "/usr/share/sounds/freedesktop/stereo/bell.oga",
    ]
    # Yaru has no dialog-information: the freedesktop theme answers.
    present = {"/usr/share/sounds/freedesktop/stereo/dialog-information.oga"}
    assert notifycenter.sound_file("theme:dialog-information", "Yaru", present.__contains__) == (
        "/usr/share/sounds/freedesktop/stereo/dialog-information.oga"
    )
    assert notifycenter.sound_file("theme:bell", "Yaru", present.__contains__) == ""


def test_a_bundled_sound_is_the_packages_file():
    path = notifycenter.sound_file("bundled:zen", "Yaru", lambda _p: True)
    assert path == os.path.join(notifycenter.SOUNDS_DIR, "zen.oga")
    assert notifycenter.sound_file("bundled:zen", "Yaru", lambda _p: False) == ""  # not shipped: the beep


def test_every_bundled_sound_ships_and_is_accounted_for():
    # The files are package data (pyproject.toml's sounds/*.oga glob, guarded
    # by scripts/verify_wheel_data.py); each has to exist, be small enough
    # to be a chime, and be named in the third-party notices with the source
    # the subtitle credits.
    notices = (ROOT / "collins" / "THIRD_PARTY_LICENSES.md").read_text()
    for name, _label, source in notifycenter.SOUND_BUNDLED:
        path = notifycenter.bundled_sound_file(name)
        assert os.path.isfile(path), path
        assert os.path.getsize(path) < 16_000, path
        assert f"`{name}.oga`" in notices, name
        assert source.split(", ")[1] in notices, source


# -- the update row (see updatecheck) ------------------------------------------


def update_row(c: NotificationCenter, version="0.1.3"):
    row = c.make(notifycenter.KIND_UPDATE, "", f"Collins {version} is available", "", "You're running 0.1.2")
    row.id = notifycenter.update_id(version)
    row.url = f"https://github.com/episode6/collins/releases/tag/v{version}"
    return row


def test_update_delivery_is_a_card_in_collins_and_the_desktop_away():
    in_collins = {DELIVER_CARD, DELIVER_SOUND, DELIVER_ROW}
    assert delivery(notifycenter.KIND_UPDATE, FOCUS_SELECTED) == in_collins
    assert delivery(notifycenter.KIND_UPDATE, FOCUS_ELSEWHERE) == in_collins
    assert delivery(notifycenter.KIND_UPDATE, FOCUS_UNFOCUSED) == {DELIVER_DESKTOP, DELIVER_ROW}
    # Nothing to flag or flash: no session raised it.
    for focus in (FOCUS_SELECTED, FOCUS_ELSEWHERE, FOCUS_UNFOCUSED):
        assert not delivery(notifycenter.KIND_UPDATE, focus) & {DELIVER_FLAG, DELIVER_FLASH}
    assert notifycenter.without_cards(in_collins) == {DELIVER_DESKTOP, DELIVER_ROW}


def test_update_ids_name_the_version():
    assert notifycenter.update_id("0.1.3") == "update:0.1.3"
    assert notifycenter.update_version("update:0.1.3") == "0.1.3"
    assert notifycenter.update_version(green_id("s1")) == ""
    assert notifycenter.update_version("abc") == ""


def test_update_row_url_survives_the_record():
    c, _calls = center()
    row = c.post(update_row(c))
    record = row.to_record()
    assert record["url"] == row.url and record["kind"] == notifycenter.KIND_UPDATE
    back = Notification.from_record(record)
    assert back.url == row.url and back.id == "update:0.1.3"
    # Every other kind's record is what it always was: no url in it, and
    # "" read back.
    plain = message(c).to_record()
    assert "url" not in plain
    assert Notification.from_record(plain).url == ""
    assert Notification.from_record({**record, "url": 3}).url == ""


def test_update_rows_persist_and_are_cleaned_like_any_other():
    c, _calls = center()
    c.post(update_row(c))
    records = c.to_records()
    assert [r["kind"] for r in records] == [notifycenter.KIND_UPDATE]
    kept = clean_records(records, now=NOW + 60)
    assert [r["id"] for r in kept] == ["update:0.1.3"]
    assert clean_records(records, now=NOW + KEEP_SECONDS + 1) == []


def test_posting_an_update_replaces_the_one_standing():
    c, calls = center()
    old = c.post(update_row(c, "0.1.3"))
    c.mark_read(old.id)
    message(c)
    new = c.post(update_row(c, "0.1.4"))
    kinds = [(r.kind, r.id) for r in c.rows()]
    assert kinds == [(notifycenter.KIND_UPDATE, "update:0.1.4"), (KIND_MESSAGE, kinds[1][1])]
    assert not new.read
    assert c.get(old.id) is None
    # Re-posting the same version is one row, not two.
    c.post(update_row(c, "0.1.4"))
    assert [r.id for r in c.rows() if r.kind == notifycenter.KIND_UPDATE] == ["update:0.1.4"]
    assert calls[-1] == 2


def test_has_unread_kind_follows_the_update_row():
    c, _calls = center()
    assert not c.has_unread_kind(notifycenter.KIND_UPDATE)
    row = c.post(update_row(c))
    assert c.has_unread_kind(notifycenter.KIND_UPDATE)
    assert not c.has_unread_kind(KIND_BELL)
    # No session key stands for it.
    assert c.unread_sessions() == frozenset()
    c.mark_read(row.id)
    assert not c.has_unread_kind(notifycenter.KIND_UPDATE)
    c.post(update_row(c, "0.1.4"))
    c.remove("update:0.1.4")
    assert not c.has_unread_kind(notifycenter.KIND_UPDATE)


def test_update_rows_go_with_mark_all_read_and_clear():
    c, _calls = center()
    c.post(update_row(c))
    assert c.mark_all_read() == 1
    assert c.unread_count() == 0
    assert c.clear() == 1
    assert c.rows() == []
# -- the card's own light/dark ----------------------------------------------


def test_the_card_schemes_are_the_setting_values_in_the_rows_order():
    assert notifycenter.CARD_SCHEMES == ("app", "light", "dark")
    assert notifycenter.CARD_SCHEME_APP == "app"


def test_a_pinned_scheme_is_a_class_on_the_card():
    assert notifycenter.card_scheme_class("light") == "notification-card-light"
    assert notifycenter.card_scheme_class("dark") == "notification-card-dark"
    assert notifycenter.card_scheme_class("light") != notifycenter.card_scheme_class("dark")


def test_following_the_app_is_no_class_at_all():
    assert notifycenter.card_scheme_class("app") == ""


def test_a_value_the_setting_does_not_take_follows_the_app():
    for stray in (None, "", "system", "DARK", 3):
        assert notifycenter.card_scheme_class(stray) == ""
