"""Tests for the editor panel's GTK-free helpers (collins.editorfiles)."""

import os

from collins.editorfiles import (
    _MAX_HIGHLIGHT_BYTES,
    _MAX_OPEN_BYTES,
    LoadGuard,
    guess_language_id,
    is_inside,
    list_dir,
    load_guard,
    should_highlight,
)

# -- guess_language_id --------------------------------------------------------


def test_guess_language_by_suffix():
    assert guess_language_id("foo.py") == "python3"
    assert guess_language_id("foo.tsx") == "js"
    assert guess_language_id("foo.md") == "markdown"
    assert guess_language_id("/a/b/foo.RS") == "rust"  # case-insensitive suffix


def test_guess_language_unknown_suffix_and_no_shebang_is_none():
    assert guess_language_id("foo.xyz") is None
    assert guess_language_id("Makefile") is None


def test_guess_language_by_shebang_when_suffix_unknown():
    assert guess_language_id("script", "#!/usr/bin/env python3") == "python3"
    assert guess_language_id("script", "#!/bin/bash") == "sh"
    assert guess_language_id("script", "#!/usr/bin/perl") == "perl"


def test_guess_language_suffix_wins_over_shebang():
    assert guess_language_id("script.py", "#!/bin/bash") == "python3"


def test_guess_language_unknown_shebang_interpreter_is_none():
    assert guess_language_id("script", "#!/usr/bin/env made-up-lang") is None
    assert guess_language_id("script", "not a shebang") is None


# -- load_guard -----------------------------------------------------------------


def test_load_guard_ok_for_plain_text(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    assert load_guard(f) == LoadGuard.OK


def test_load_guard_missing_path_is_not_a_file(tmp_path):
    assert load_guard(tmp_path / "missing.txt") == LoadGuard.NOT_A_FILE


def test_load_guard_directory_is_not_a_file(tmp_path):
    assert load_guard(tmp_path) == LoadGuard.NOT_A_FILE


def test_load_guard_binary_detected_by_nul_byte(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello\x00world")
    assert load_guard(f) == LoadGuard.BINARY


def test_load_guard_too_large(tmp_path):
    f = tmp_path / "big.txt"
    with f.open("wb") as fh:
        fh.seek(_MAX_OPEN_BYTES)
        fh.write(b"x")
    assert load_guard(f) == LoadGuard.TOO_LARGE


def test_load_guard_at_size_cap_is_ok(tmp_path):
    f = tmp_path / "cap.txt"
    f.write_bytes(b"x" * _MAX_OPEN_BYTES)
    assert load_guard(f) == LoadGuard.OK


def test_load_guard_unreadable(tmp_path):
    f = tmp_path / "noperm.txt"
    f.write_text("hi")
    os.chmod(f, 0o000)
    try:
        if os.access(f, os.R_OK):  # root in the test environment: skip
            return
        assert load_guard(f) == LoadGuard.UNREADABLE
    finally:
        os.chmod(f, 0o644)


# -- should_highlight -----------------------------------------------------------


def test_should_highlight_small_file(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x")
    assert should_highlight(f) is True


def test_should_highlight_false_above_cap(tmp_path):
    f = tmp_path / "big.py"
    f.write_bytes(b"x" * (_MAX_HIGHLIGHT_BYTES + 1))
    assert should_highlight(f) is False


def test_should_highlight_true_for_missing_file():
    assert should_highlight("/nonexistent/path") is True


# -- is_inside --------------------------------------------------------------------


def test_is_inside_true_for_child(tmp_path):
    child = tmp_path / "sub" / "file.py"
    child.parent.mkdir()
    child.write_text("x")
    assert is_inside(tmp_path, child) is True


def test_is_inside_true_for_root_itself(tmp_path):
    assert is_inside(tmp_path, tmp_path) is True


def test_is_inside_false_for_sibling(tmp_path):
    sibling = tmp_path.parent / "sibling-not-really-there"
    assert is_inside(tmp_path, sibling) is False


def test_is_inside_false_for_symlink_escape(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("nope")
    link = root / "escape"
    link.symlink_to(outside)
    assert is_inside(root, link / "secret.txt") is False


# -- list_dir ---------------------------------------------------------------------


def test_list_dir_sorts_dirs_first_then_case_insensitive(tmp_path):
    (tmp_path / "b.py").write_text("")
    (tmp_path / "A.py").write_text("")
    (tmp_path / "zsub").mkdir()
    (tmp_path / "Asub").mkdir()
    assert list_dir(tmp_path) == [
        ("Asub", True),
        ("zsub", True),
        ("A.py", False),
        ("b.py", False),
    ]


def test_list_dir_skips_hidden_by_default(tmp_path):
    (tmp_path / ".hidden").write_text("")
    (tmp_path / "visible.txt").write_text("")
    assert list_dir(tmp_path) == [("visible.txt", False)]


def test_list_dir_shows_hidden_when_asked(tmp_path):
    (tmp_path / ".hidden").write_text("")
    names = [name for name, _is_dir in list_dir(tmp_path, show_hidden=True)]
    assert ".hidden" in names


def test_list_dir_skips_vcs_and_dependency_dirs(tmp_path):
    for name in (".git", "node_modules", "__pycache__", ".venv", "target", "dist", "build"):
        (tmp_path / name).mkdir()
    (tmp_path / "src").mkdir()
    assert list_dir(tmp_path) == [("src", True)]


def test_list_dir_skips_non_regular_nodes(tmp_path):
    (tmp_path / "real.txt").write_text("x")
    fifo = tmp_path / "a.fifo"
    os.mkfifo(fifo)
    assert list_dir(tmp_path) == [("real.txt", False)]


def test_list_dir_missing_directory_is_empty(tmp_path):
    assert list_dir(tmp_path / "nope") == []


def test_list_dir_file_path_is_empty(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert list_dir(f) == []
