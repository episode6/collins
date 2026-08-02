"""Tests for the dropped-image helpers (collins.dropimages)."""

import time

from collins.dropimages import (
    PRUNE_AFTER_SECONDS,
    default_directory,
    mention_text,
    prune_stale,
    save_png,
)

# A fixed moment keeps the expected file names stable; the exact date is
# irrelevant, only that every test agrees on it.
_NOW = time.mktime((2026, 8, 2, 12, 30, 15, 0, 0, -1))
_STEM = time.strftime("drop-%Y%m%d-%H%M%S", time.localtime(_NOW))

# -- mention_text -------------------------------------------------------------

# Stands in for Provider.file_reference: quotes like the Claude provider,
# refuses names with control characters like every provider must.
def _fake_reference(path):
    if any(ord(ch) < 0x20 for ch in path):
        return None
    return f'@"{path}"' if " " in path else f"@{path}"


def test_mention_text_one_token_per_path_with_trailing_spaces():
    text, failed = mention_text(["a.png", "my pic.jpg"], _fake_reference)
    assert text == '@a.png @"my pic.jpg" '
    assert failed == 0


def test_mention_text_counts_refused_names_and_keeps_the_rest():
    text, failed = mention_text(["ok.png", "bad\x0dname.png", "also.png"], _fake_reference)
    assert text == "@ok.png @also.png "
    assert failed == 1


def test_mention_text_all_refused_or_empty():
    assert mention_text(["\x1b.png"], _fake_reference) == ("", 1)
    assert mention_text([], _fake_reference) == ("", 0)


# -- default_directory --------------------------------------------------------


def test_default_directory_honors_xdg_cache_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert default_directory() == tmp_path / "collins" / "dropped-images"


def test_default_directory_falls_back_to_dot_cache(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_directory() == tmp_path / ".cache" / "collins" / "dropped-images"


# -- save_png -----------------------------------------------------------------


def test_save_png_writes_data_and_creates_directory(tmp_path):
    directory = tmp_path / "not" / "yet" / "there"
    path = save_png(b"png-bytes", directory, _NOW)
    assert path == directory / f"{_STEM}.png"
    assert path.read_bytes() == b"png-bytes"


def test_save_png_same_second_gets_distinct_names(tmp_path):
    first = save_png(b"one", tmp_path, _NOW)
    second = save_png(b"two", tmp_path, _NOW)
    third = save_png(b"three", tmp_path, _NOW)
    assert first != second != third
    assert second == tmp_path / f"{_STEM}-2.png"
    assert third == tmp_path / f"{_STEM}-3.png"
    assert first.read_bytes() == b"one"  # never clobbered


# -- prune_stale --------------------------------------------------------------


def test_prune_stale_removes_only_old_files(tmp_path):
    import os

    old = save_png(b"old", tmp_path, _NOW)
    fresh = save_png(b"fresh", tmp_path, _NOW)
    os.utime(old, (_NOW - PRUNE_AFTER_SECONDS - 1, _NOW - PRUNE_AFTER_SECONDS - 1))
    os.utime(fresh, (_NOW - 60, _NOW - 60))
    prune_stale(tmp_path, _NOW)
    assert not old.exists()
    assert fresh.exists()


def test_prune_stale_skips_directories_and_missing_dir(tmp_path):
    (tmp_path / "subdir").mkdir()
    prune_stale(tmp_path, _NOW)  # a directory entry is left alone
    assert (tmp_path / "subdir").is_dir()
    prune_stale(tmp_path / "never-created", _NOW)  # no error
