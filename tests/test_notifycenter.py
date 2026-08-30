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
