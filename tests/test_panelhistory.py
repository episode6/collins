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


# -- multi-shell (save_all / load_all, keyed by ordinal) ---------------------


def test_save_all_roundtrip(history):
    history.save_all("sid-1", {0: "shell zero", 1: "shell one", 2: "shell two"})
    assert history.load_all("sid-1") == {0: "shell zero", 1: "shell one", 2: "shell two"}


def test_load_all_missing_session(history):
    assert history.load_all("nope") == {}


def test_ordinal_zero_keeps_legacy_filename(history):
    # History saved before the panel grew tabs restores as ordinal 0.
    history.save("sid-1", "old single-panel history")
    assert history.load_all("sid-1") == {0: "old single-panel history"}
    history.save_all("sid-1", {0: "updated"})
    assert history.load("sid-1") == "updated"


def test_legacy_positional_files_adopt_index_as_ordinal(history):
    # Positional-era files were already named <session>.<index>.txt, so the
    # adoption is just reading them under the same numbers.
    history.save("sid-1", "one", 0)
    history.save("sid-1", "two", 1)
    assert history.load_all("sid-1") == {0: "one", 1: "two"}
    assert history.ordinals("sid-1") == [0, 1]


def test_ordinals_survive_gaps(history):
    history.save_all("sid-1", {0: "one", 4: "five"})
    assert history.ordinals("sid-1") == [0, 4]
    assert history.load("sid-1", 4) == "five"


def test_save_all_keep_set_drops_closed_shells(history):
    history.save_all("sid-1", {0: "one", 1: "two", 2: "three"})
    history.save_all("sid-1", {2: "three"})  # 0 and 1 closed; 2 keeps its key
    assert history.load_all("sid-1") == {2: "three"}


def test_save_all_empty_clears_everything(history):
    history.save_all("sid-1", {0: "one", 1: "two"})
    history.save_all("sid-1", {})
    assert history.load_all("sid-1") == {}


def test_save_all_skips_blank_shells(history):
    history.save_all("sid-1", {0: "one", 1: " \n ", 2: "three"})
    assert history.load_all("sid-1") == {0: "one", 2: "three"}


def test_delete_removes_all_shells(history):
    history.save_all("sid-1", {0: "one", 1: "two"})
    history.delete("sid-1")
    assert history.load_all("sid-1") == {}


def test_copy_keeps_ordinals(history):
    history.save_all("old", {0: "one", 3: "four"})
    history.copy("old", "new")
    assert history.load_all("new") == {0: "one", 3: "four"}
    assert history.load_all("old") == {0: "one", 3: "four"}  # source untouched


def test_copy_noop_when_target_has_any_shell(history):
    history.save_all("old", {0: "one", 1: "two"})
    history.save("new", "already here", ordinal=1)
    history.copy("old", "new")
    assert history.load_all("new") == {1: "already here"}


def test_shell_files_never_leak_across_sessions(history):
    # "sid-1.2.txt" belongs to sid-1's ordinal 2, not to a session that
    # happens to share the filename prefix.
    history.save_all("sid-1", {0: "one", 1: "two", 2: "three"})
    assert history.load_all("sid-11") == {}
