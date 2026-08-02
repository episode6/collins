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


def test_copy(history):
    history.save("old", "$ make\nok")
    history.copy("old", "new")
    assert history.load("new") == "$ make\nok"
    assert history.load("old") == "$ make\nok"  # source untouched


def test_copy_missing_source_is_noop(history):
    history.copy("missing", "new")
    assert history.load("new") is None


def test_copy_never_overwrites_target(history):
    history.save("old", "from old")
    history.save("new", "already here")
    history.copy("old", "new")
    assert history.load("new") == "already here"


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


# -- multi-tab (save_all / load_all) ----------------------------------------


def test_save_all_roundtrip(history):
    history.save_all("sid-1", ["tab one", "tab two", "tab three"])
    assert history.load_all("sid-1") == ["tab one", "tab two", "tab three"]


def test_load_all_missing_session(history):
    assert history.load_all("nope") == []


def test_first_tab_keeps_legacy_filename(history):
    # History saved before the panel grew tabs restores into the first tab.
    history.save("sid-1", "old single-panel history")
    assert history.load_all("sid-1") == ["old single-panel history"]
    history.save_all("sid-1", ["updated"])
    assert history.load("sid-1") == "updated"


def test_save_all_drops_closed_tabs(history):
    history.save_all("sid-1", ["one", "two", "three"])
    history.save_all("sid-1", ["one"])
    assert history.load_all("sid-1") == ["one"]


def test_save_all_empty_clears_everything(history):
    history.save_all("sid-1", ["one", "two"])
    history.save_all("sid-1", [])
    assert history.load_all("sid-1") == []


def test_save_all_skips_blank_tabs(history):
    history.save_all("sid-1", ["one", " \n ", "three"])
    assert history.load_all("sid-1") == ["one", "three"]


def test_delete_removes_all_tabs(history):
    history.save_all("sid-1", ["one", "two"])
    history.delete("sid-1")
    assert history.load_all("sid-1") == []


def test_copy_copies_all_tabs(history):
    history.save_all("old", ["one", "two"])
    history.copy("old", "new")
    assert history.load_all("new") == ["one", "two"]
    assert history.load_all("old") == ["one", "two"]  # source untouched


def test_copy_noop_when_target_has_any_tab(history):
    history.save_all("old", ["one", "two"])
    history.save("new", "already here", index=1)
    history.copy("old", "new")
    assert history.load_all("new") == ["already here"]


def test_tab_files_never_leak_across_sessions(history):
    # "sid-1.2.txt" belongs to sid-1's third tab, not to a session that
    # happens to share the filename prefix.
    history.save_all("sid-1", ["one", "two", "three"])
    assert history.load_all("sid-11") == []
