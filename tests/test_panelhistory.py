# New in the ghackett fork of agent-session-manager (GPL-3.0).

import pytest

import collins.panelhistory as panelhistory


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setattr(panelhistory, "_HISTORY_DIR", tmp_path / "panel_history")
    return panelhistory


def test_roundtrip(history):
    history.save("sid-1", "$ ls\nfile.txt\n")
    assert history.load("sid-1") == "$ ls\nfile.txt"


def test_missing_returns_none(history):
    assert history.load("nope") is None


def test_blank_save_clears(history):
    history.save("sid-1", "$ ls\n")
    history.save("sid-1", "  \n\n")
    assert history.load("sid-1") is None


def test_delete(history):
    history.save("sid-1", "text")
    history.delete("sid-1")
    assert history.load("sid-1") is None
    history.delete("sid-1")  # deleting again is a no-op, not an error


def test_unsafe_session_ids_rejected(history):
    for bad in ("", "../../etc/passwd", "a/b", ".hidden", "a b"):
        history.save(bad, "text")
        assert history.load(bad) is None
        history.delete(bad)  # no-op, no error
    assert not (history._HISTORY_DIR).exists()


def test_trim_keeps_tail_on_line_boundary(history, monkeypatch):
    monkeypatch.setattr(history, "_MAX_BYTES", 30)
    lines = [f"line-{i:04}" for i in range(10)]  # 9 bytes + newline each
    history.save("sid-1", "\n".join(lines))
    loaded = history.load("sid-1")
    assert loaded.endswith("line-0009")
    assert not loaded.startswith("ine-")  # cut lands on a line boundary
    assert len(loaded.encode()) <= 30


def test_trim_multibyte_safe(history, monkeypatch):
    monkeypatch.setattr(history, "_MAX_BYTES", 9)
    history.save("sid-1", "aaaa\n" + "é" * 20)  # tail line longer than the cap
    loaded = history.load("sid-1")
    assert loaded  # decodes cleanly even when the cut splits a code point
    assert "a" not in loaded
