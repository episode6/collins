# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""`panelhistory.history_requests` (PR-1.12b): the `panel.history`
requests a tab's save is split into when its texts pass the frame
budget, so no shell's history is blanked by another's."""

from collins import panelhistory


def test_one_request_when_it_fits():
    shells = [{"ordinal": 0, "pty": 7}, {"ordinal": 1, "text": "small"}]
    assert panelhistory.history_requests("k", shells, budget=100) == [
        {"t": "panel.history", "key": "k", "shells": shells}
    ]


def test_large_texts_go_one_per_partial_request_behind_a_keep_set():
    shells = [{"ordinal": 0, "pty": 7}, {"ordinal": 1, "text": "x" * 80}, {"ordinal": 2, "text": "y" * 80}]
    out = panelhistory.history_requests("k", shells, budget=100)
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


def test_save_all_keeps_what_keep_names(tmp_path, monkeypatch):
    monkeypatch.setattr(panelhistory, "_HISTORY_DIR", tmp_path)
    panelhistory.save("s", "one", 1)
    panelhistory.save("s", "two", 2)
    panelhistory.save_all("s", {0: "zero"}, keep=[1])
    assert panelhistory.load("s", 0) == "zero"
    assert panelhistory.load("s", 1) == "one"
    assert panelhistory.load("s", 2) is None
