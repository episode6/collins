"""Tests for the dropped-image helpers (collins.dropimages)."""

import time

from collins.dropimages import (
    PRUNE_AFTER_SECONDS,
    cell_width,
    default_directory,
    leading_space,
    mention_text,
    prune_stale,
    save_png,
)

# Claude Code draws its input box as a ❯ and a no-break space (the space
# below is that one, not an ordinary one), then whatever has been typed.
_PROMPT = "❯ "

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


# -- leading_space ------------------------------------------------------------


def test_leading_space_none_for_an_empty_input_box():
    # The cursor sits right after the marker, whose no-break space is the
    # last character before it — the box holds nothing of the user's.
    assert leading_space(_PROMPT, len(_PROMPT)) == ""


def test_leading_space_separates_a_half_written_sentence():
    line = _PROMPT + "look at"
    assert leading_space(line, len(line)) == " "


def test_leading_space_none_when_the_sentence_already_ends_in_one():
    line = _PROMPT + "look at "
    assert leading_space(line, len(line)) == ""


def test_leading_space_separates_mid_sentence_and_after_punctuation():
    # The cursor moved back into the sentence: only what precedes it counts.
    assert leading_space(_PROMPT + "look at", len(_PROMPT) + 4) == " "
    line = _PROMPT + "compare these:"
    assert leading_space(line, len(line)) == " "


def test_leading_space_none_past_the_end_of_the_written_line():
    # A cursor beyond what the terminal reported: the cells in between are
    # blank, so the mention already has its distance.
    assert leading_space(_PROMPT + "look at", 40) == ""
    assert leading_space("", 0) == ""


def test_leading_space_separates_a_sentence_ending_in_a_wide_character():
    # Measured from VTE: 見て sits at cursor column 6 on a line of only four
    # characters. Counting characters instead of cells read that as a cursor
    # past the end of the line, and glued the mention onto the て.
    line = _PROMPT + "見て"
    assert leading_space(line, 6) == " "
    assert leading_space(_PROMPT + "look 🚀", 9) == " "


def test_leading_space_none_after_a_wide_character_and_a_space():
    assert leading_space(_PROMPT + "見て ", 7) == ""


# -- cell_width ---------------------------------------------------------------


def test_cell_width_counts_cells_not_characters():
    assert cell_width("look at") == 7
    assert cell_width("見て") == 4  # two cells each
    assert cell_width("🚀") == 2
    assert cell_width(_PROMPT) == 2  # the ❯ and the no-break space, one each


def test_cell_width_ignores_marks_and_joiners():
    assert cell_width("é") == 1  # e + combining acute
    assert cell_width("‍") == 0  # a zero-width joiner on its own
    assert cell_width("👩‍🚀") == 4  # two emoji joined; VTE draws both
    assert cell_width("") == 0


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
