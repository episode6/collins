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
    walk_files,
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


def test_list_dir_with_root_skips_file_symlink_escaping_it(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("nope")
    (root / "leak.txt").symlink_to(secret)
    (root / "real.txt").write_text("x")
    assert list_dir(root, root=root) == [("real.txt", False)]


def test_list_dir_with_root_skips_dir_symlink_escaping_it(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside)
    assert list_dir(root, root=root) == []


def test_list_dir_with_root_keeps_symlink_resolving_inside_it(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "real.txt").write_text("x")
    (root / "alias.txt").symlink_to(root / "real.txt")
    assert list_dir(root, root=root) == [("alias.txt", False), ("real.txt", False)]


def test_list_dir_without_root_lists_symlinks(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("nope")
    (root / "leak.txt").symlink_to(secret)
    assert list_dir(root) == [("leak.txt", False)]


def test_list_dir_missing_directory_is_empty(tmp_path):
    assert list_dir(tmp_path / "nope") == []


def test_list_dir_file_path_is_empty(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert list_dir(f) == []


# -- walk_files ---------------------------------------------------------------


def _touch(root, rel):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")


def test_walk_files_breadth_first_relative_posix(tmp_path):
    _touch(tmp_path, "top.py")
    _touch(tmp_path, "pkg/mod.py")
    _touch(tmp_path, "pkg/sub/deep.py")
    paths, truncated = walk_files(tmp_path)
    assert paths == ["top.py", "pkg/mod.py", "pkg/sub/deep.py"]
    assert truncated is False


def test_walk_files_skips_hidden_and_skip_dirs(tmp_path):
    _touch(tmp_path, "keep.py")
    _touch(tmp_path, ".hidden.py")
    _touch(tmp_path, ".git/config")
    _touch(tmp_path, "node_modules/dep.js")
    _touch(tmp_path, "__pycache__/keep.cpython-312.pyc")
    paths, _ = walk_files(tmp_path)
    assert paths == ["keep.py"]


def test_walk_files_show_hidden_includes_dotfiles_not_skip_dirs(tmp_path):
    _touch(tmp_path, ".env")
    _touch(tmp_path, ".git/config")
    paths, _ = walk_files(tmp_path, show_hidden=True)
    assert paths == [".env"]


def test_walk_files_never_descends_symlinked_directories(tmp_path):
    # Neither an escaping link nor an in-project one: link cycles must not
    # wedge the walk, so the rule matches the file tree's (no expansion at all).
    root = tmp_path / "project"
    _touch(root, "real/a.py")
    (root / "loop").symlink_to(root)
    outside = tmp_path / "outside"
    _touch(outside, "secret.py")
    (root / "escape").symlink_to(outside)
    paths, _ = walk_files(root)
    assert paths == ["real/a.py"]


def test_walk_files_skips_file_symlink_escaping_root(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("nope")
    (root / "leak.txt").symlink_to(secret)
    _touch(root, "ok.txt")
    paths, _ = walk_files(root)
    assert paths == ["ok.txt"]


def test_walk_files_cap_truncates(tmp_path):
    for i in range(5):
        _touch(tmp_path, f"f{i}.txt")
    paths, truncated = walk_files(tmp_path, cap=3)
    assert len(paths) == 3
    assert truncated is True


def test_walk_files_missing_root_is_empty(tmp_path):
    assert walk_files(tmp_path / "nope") == ([], False)
