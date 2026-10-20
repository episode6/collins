# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""`panelhistory.history_requests` (PR-1.12b): the `panel.history`
requests a tab's save is split into when its texts pass the frame
budget, so no shell's history is blanked by another's."""

from collins import panelhistory
from collins.api import protocol


def test_one_request_when_it_fits():
    shells = [{"ordinal": 0, "pty": 7}, {"ordinal": 1, "text": "small"}]
    assert panelhistory.history_requests("k", shells, budget=100) == [
        {"t": "panel.history", "key": "k", "shells": shells}
    ]


def test_large_texts_go_one_per_partial_request_behind_a_keep_set():
    shells = [{"ordinal": 0, "pty": 7}, {"ordinal": 1, "text": "x" * 80}, {"ordinal": 2, "text": "y" * 80}]
    # One text's request frames in 180 bytes; the whole mapping's does not.
    out = panelhistory.history_requests("k", shells, budget=180)
    assert out[0] == {
        "t": "panel.history", "key": "k",
        "shells": [{"ordinal": 0, "pty": 7}, {"ordinal": 1, "keep": True}, {"ordinal": 2, "keep": True}],
    }
    assert out[1:] == [
        {"t": "panel.history", "key": "k", "partial": True, "shells": [{"ordinal": 1, "text": "x" * 80}]},
        {"t": "panel.history", "key": "k", "partial": True, "shells": [{"ordinal": 2, "text": "y" * 80}]},
    ]
    assert all("partial" not in r or len(r["shells"]) == 1 for r in out[1:])


def test_a_text_over_the_budget_alone_keeps_its_tail():
    shells = [{"ordinal": 0, "text": "a" * 50 + "b" * 200}]
    out = panelhistory.history_requests("k", shells, budget=100)
    text = out[-1]["shells"][0]["text"]
    assert len(text.encode()) <= 100 and text.endswith("b") and "a" not in text
    assert all(protocol.json_bytes(r) <= 100 for r in out)


def _frames(requests):
    """Every request encodes as a frame: what the link sends, or refuses."""
    for request in requests:
        assert len(protocol.encode(request).encode("utf-8")) <= protocol.MAX_FRAME
        assert protocol.json_bytes(request) <= panelhistory.HISTORY_REQUEST_BUDGET
    return requests


def test_the_budget_is_the_frame_not_the_texts_utf8():
    """The review of PR 604, should-fix 1: a `yes`-style tail of 900 KB
    measures under the budget in UTF-8 and over it once every newline is
    escaped; the one request it made was refused by `protocol.encode` and
    the history was not written. The whole-request road and the per-text
    road both budget what the frame holds."""
    tail = "y\n" * 450_000  # 900 000 UTF-8 bytes, 1 350 000 escaped
    out = _frames(panelhistory.history_requests("k", [{"ordinal": 0, "text": tail}]))
    assert out[0]["shells"] == [{"ordinal": 0, "keep": True}]
    assert out[1]["partial"] is True and out[1]["shells"][0]["text"].endswith("y\n")
    assert protocol.json_bytes(out[1]) > panelhistory.HISTORY_REQUEST_BUDGET - 8  # the tail fills the frame
    # Two shells of escaped-heavy lines that fit the budget in UTF-8 together:
    # the keep-set first, then each text alone and whole.
    dense = "C:\\x\\y\n" * 60_000  # 420 000 UTF-8 bytes each, 600 000 escaped
    shells = [{"ordinal": 0, "pty": 7}, {"ordinal": 1, "text": dense}, {"ordinal": 2, "text": dense}]
    out = _frames(panelhistory.history_requests("k", shells))
    assert [r.get("partial") for r in out] == [None, True, True]
    assert out[1]["shells"][0]["text"] == dense and out[2]["shells"][0]["text"] == dense
    # Control characters cost six bytes a piece; the tail is cut where the
    # frame fills, never past it.
    out = _frames(panelhistory.history_requests("k", [{"ordinal": 0, "text": "\x01" * 300_000}]))
    assert 0 < len(out[1]["shells"][0]["text"]) < 300_000
    # An ordinary 80-column scrollback under the budget still goes whole.
    plain = ("x" * 79 + "\n") * 11_000
    out = _frames(panelhistory.history_requests("k", [{"ordinal": 0, "text": plain}]))
    assert len(out) == 1 and out[0]["shells"][0]["text"] == plain


def test_save_all_keeps_what_keep_names(tmp_path, monkeypatch):
    monkeypatch.setattr(panelhistory, "_HISTORY_DIR", tmp_path)
    panelhistory.save("s", "one", 1)
    panelhistory.save("s", "two", 2)
    panelhistory.save_all("s", {0: "zero"}, keep=[1])
    assert panelhistory.load("s", 0) == "zero"
    assert panelhistory.load("s", 1) == "one"
    assert panelhistory.load("s", 2) is None
