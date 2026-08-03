"""Tests for the editor panel's GTK-free helpers (collins.editorfiles)."""

import os

from collins.editorfiles import (
    _MAX_HIGHLIGHT_BYTES,
    _MAX_IMAGE_BYTES,
    _MAX_OPEN_BYTES,
    LIGHTBOX_BUTTON_STRIP,
    LIGHTBOX_MIN_H,
    LIGHTBOX_MIN_W,
    LIGHTBOX_SHADOW_PAD,
    LoadGuard,
    guess_language_id,
    image_guard,
    is_image_path,
    is_inside,
    lightbox_layout,
    lightbox_zoom_slot,
    lightbox_zoombar_inside,
    list_dir,
    load_guard,
    path_from_file_uri,
    read_first_line,
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


def test_guess_language_env_s_flag_and_assignments_are_skipped():
    assert guess_language_id("script", "#!/usr/bin/env -S python3") == "python3"
    assert guess_language_id("script", "#!/usr/bin/env -S FOO=bar bash") == "sh"
    assert guess_language_id("script", "#!/usr/bin/env -S") is None


def test_guess_language_unknown_shebang_interpreter_is_none():
    assert guess_language_id("script", "#!/usr/bin/env made-up-lang") is None
    assert guess_language_id("script", "not a shebang") is None


# -- read_first_line ----------------------------------------------------------


def test_read_first_line_strips_line_ending(tmp_path):
    f = tmp_path / "script"
    f.write_text("#!/bin/bash\necho hi\n")
    assert read_first_line(f) == "#!/bin/bash"


def test_read_first_line_crlf(tmp_path):
    f = tmp_path / "script"
    f.write_bytes(b"#!/usr/bin/env python3\r\nprint(1)\r\n")
    assert read_first_line(f) == "#!/usr/bin/env python3"


def test_read_first_line_missing_file_is_empty(tmp_path):
    assert read_first_line(tmp_path / "missing") == ""


def test_read_first_line_caps_bytes(tmp_path):
    f = tmp_path / "long"
    f.write_text("x" * 4096)
    assert read_first_line(f) == "x" * 512


def test_read_first_line_feeds_shebang_guess(tmp_path):
    f = tmp_path / "deploy"
    f.write_text("#!/usr/bin/env bash\nset -euo pipefail\n")
    assert guess_language_id(f, read_first_line(f)) == "sh"


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


# -- is_image_path ----------------------------------------------------------------


def test_is_image_path_by_suffix():
    assert is_image_path("shot.png")
    assert is_image_path("/a/b/photo.JPEG")  # case-insensitive suffix
    assert is_image_path("icon.svg")
    assert is_image_path("anim.webp")


def test_is_image_path_non_images():
    assert not is_image_path("a.py")
    assert not is_image_path("png")  # no suffix at all
    assert not is_image_path("archive.png.gz")


# -- image_guard ------------------------------------------------------------------


def test_image_guard_ok(tmp_path):
    f = tmp_path / "a.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)  # NUL bytes are fine here
    assert image_guard(f) == LoadGuard.OK


def test_image_guard_missing_and_directory(tmp_path):
    assert image_guard(tmp_path / "missing.png") == LoadGuard.NOT_A_FILE
    assert image_guard(tmp_path) == LoadGuard.NOT_A_FILE


def test_image_guard_too_large(tmp_path):
    f = tmp_path / "huge.png"
    with f.open("wb") as fh:
        fh.seek(_MAX_IMAGE_BYTES)
        fh.write(b"x")
    assert image_guard(f) == LoadGuard.TOO_LARGE


def test_image_guard_unreadable(tmp_path):
    f = tmp_path / "noperm.png"
    f.write_bytes(b"x")
    os.chmod(f, 0o000)
    try:
        if os.access(f, os.R_OK):  # root in the test environment: skip
            return
        assert image_guard(f) == LoadGuard.UNREADABLE
    finally:
        os.chmod(f, 0o644)


# -- path_from_file_uri -------------------------------------------------------------


def test_path_from_file_uri_plain():
    assert path_from_file_uri("file:///home/u/shot.png") == "/home/u/shot.png"


def test_path_from_file_uri_sheds_fragment_and_query():
    assert path_from_file_uri("file:///a/b.png#L10") == "/a/b.png"
    assert path_from_file_uri("file:///a/b.png#L2-4") == "/a/b.png"
    assert path_from_file_uri("file:///a/b.png?raw=1") == "/a/b.png"


def test_path_from_file_uri_percent_decodes():
    assert path_from_file_uri("file:///a/with%20space.png") == "/a/with space.png"


def test_path_from_file_uri_localhost_ok_remote_rejected():
    assert path_from_file_uri("file://localhost/a.png") == "/a.png"
    assert path_from_file_uri("file://nas/share/a.png") is None


def test_path_from_file_uri_other_schemes_rejected():
    assert path_from_file_uri("https://example.com/a.png") is None
    assert path_from_file_uri("/a/b.png") is None  # not a URI at all


# -- lightbox_layout ----------------------------------------------------------------


_PAD = 2 * LIGHTBOX_SHADOW_PAD  # the shadow inset, both sides


def test_lightbox_layout_wide_window_puts_buttons_right_at_one_to_one():
    side, w, h = lightbox_layout(800, 500, 1920, 1080)
    assert side == "right"
    assert (w, h) == (800 + LIGHTBOX_BUTTON_STRIP + _PAD, 500 + _PAD)


def test_lightbox_layout_tall_window_puts_buttons_below():
    side, w, h = lightbox_layout(1600, 600, 800, 1000)
    assert side == "below"
    assert w == int(800 * 0.85)  # image scaled to the width cap
    scale = (int(800 * 0.85) - _PAD) / 1600
    assert h == round(600 * scale) + LIGHTBOX_BUTTON_STRIP + _PAD


def test_lightbox_layout_large_image_scales_down_keeping_aspect():
    side, w, h = lightbox_layout(4000, 2000, 1920, 1080)
    assert side == "right"
    assert w <= int(1920 * 0.85)
    assert h <= int(1080 * 0.85)
    # aspect preserved by the fit itself (picture CONTAIN handles the rest)
    assert abs(((w - LIGHTBOX_BUTTON_STRIP - _PAD) / (h - _PAD)) - 2.0) < 0.02


def test_lightbox_layout_never_upscales():
    side, w, h = lightbox_layout(400, 300, 3840, 2160)
    assert side == "right"
    assert (w, h) == (400 + LIGHTBOX_BUTTON_STRIP + _PAD, 300 + _PAD)


def test_lightbox_layout_tiny_image_clamped_to_minimum():
    side, w, h = lightbox_layout(16, 16, 1920, 1080)
    assert (w, h) == (LIGHTBOX_MIN_W, LIGHTBOX_MIN_H)


def test_lightbox_layout_forced_side_wins_over_spare_space():
    # 1600x600 in an 800x1000 window prefers "below" (see the test above);
    # forcing "right" (re-layout after a resize keeps the strip put) must
    # honor it and reserve the strip in the width instead.
    side, w, h = lightbox_layout(1600, 600, 800, 1000, side="right")
    assert side == "right"
    scale = (int(800 * 0.85) - _PAD - LIGHTBOX_BUTTON_STRIP) / 1600
    assert w == round(1600 * scale) + LIGHTBOX_BUTTON_STRIP + _PAD
    assert h == round(600 * scale) + _PAD


# -- lightbox_zoom_slot -------------------------------------------------------

# An 800x600 image in a 1200x800 window with the strip on the right: the
# space beside the strip is 1200 - _PAD - strip = 1040 wide, so the strip
# yields once the zoomed display width passes 1040.
_CHROME_R = (LIGHTBOX_BUTTON_STRIP, 0)
_BESIDE_W = 1200 - _PAD - LIGHTBOX_BUTTON_STRIP


def test_lightbox_zoom_slot_fit_keeps_strip_and_matches_display():
    display, strip_shown, chrome, slot = lightbox_zoom_slot(
        800, 600, 1.0, _CHROME_R, 1200, 800
    )
    assert display == (800, 600)
    assert strip_shown and chrome == _CHROME_R
    assert slot == display  # no scrolling while the display fits


def test_lightbox_zoom_slot_strip_stays_until_its_space_is_needed():
    zoom = _BESIDE_W / 800  # display width exactly the space beside the strip
    display, strip_shown, chrome, slot = lightbox_zoom_slot(
        800, 600, zoom, _CHROME_R, 1200, 800
    )
    assert display[0] == _BESIDE_W and strip_shown and chrome == _CHROME_R


def test_lightbox_zoom_slot_strip_yields_and_slot_reclaims_its_space():
    zoom = (_BESIDE_W + 1) / 800  # one px past the space beside the strip
    display, strip_shown, chrome, slot = lightbox_zoom_slot(
        800, 600, zoom, _CHROME_R, 1200, 800
    )
    assert not strip_shown and chrome == (0, 0)
    assert slot[0] == _BESIDE_W + 1  # wider than the with-strip cap: reclaimed
    assert slot[1] == min(display[1], 800 - _PAD)  # other axis unaffected


def test_lightbox_zoom_slot_caps_slot_at_window_minus_shadow_pad():
    display, strip_shown, chrome, slot = lightbox_zoom_slot(
        800, 600, 4.0, _CHROME_R, 1200, 800
    )
    assert display == (3200, 2400)
    assert not strip_shown
    assert slot == (1200 - _PAD, 800 - _PAD)  # scrolls: display exceeds the slot


def test_lightbox_zoom_slot_below_strip_thresholds_on_height():
    chrome = (0, LIGHTBOX_BUTTON_STRIP)
    over = (1000 - _PAD - LIGHTBOX_BUTTON_STRIP + 1) / 600
    display, strip_shown, _chrome, _slot = lightbox_zoom_slot(
        1600, 600, over / 2, chrome, 800, 1000
    )
    assert strip_shown  # height still fits above the strip
    display, strip_shown, eff, slot = lightbox_zoom_slot(
        1600, 600, over, chrome, 800, 1000
    )
    assert not strip_shown and eff == (0, 0)
    assert slot[1] == display[1]  # the reclaimed height


# -- lightbox_zoombar_inside --------------------------------------------------


def test_lightbox_zoombar_floats_while_at_most_half_the_image():
    assert lightbox_zoombar_inside(58, 116)  # exactly half: still floats


def test_lightbox_zoombar_moves_below_past_half_the_image():
    assert not lightbox_zoombar_inside(58, 115)


def test_lightbox_zoombar_moves_below_tiny_images():
    assert not lightbox_zoombar_inside(58, 48)  # a 48px icon at 1:1


def test_lightbox_layout_unrealized_window_uses_fallback():
    side, w, h = lightbox_layout(800, 500, 0, 0)
    assert side == "right"
    assert (w, h) == (800 + LIGHTBOX_BUTTON_STRIP + _PAD, 500 + _PAD)


def test_lightbox_layout_zero_size_image_does_not_divide_by_zero():
    side, w, h = lightbox_layout(0, 0, 1920, 1080)
    assert (w, h) == (LIGHTBOX_MIN_W, LIGHTBOX_MIN_H)


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
