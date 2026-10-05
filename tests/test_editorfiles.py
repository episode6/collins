"""Tests for the editor panel's GTK-free helpers (collins.editorfiles)."""

import os

import pytest

from collins.editorfiles import (
    _MAX_HIGHLIGHT_BYTES,
    _MAX_IMAGE_BYTES,
    LIGHTBOX_BUTTON_STRIP,
    LIGHTBOX_MIN_H,
    LIGHTBOX_MIN_W,
    LIGHTBOX_SHADOW_PAD,
    LoadGuard,
    PaneLayout,
    RerootAction,
    collins_uri,
    fence_language_id,
    first_line,
    format_copied_files,
    gallery_step,
    guess_language_id,
    image_stat_guard,
    is_image_path,
    lightbox_layout,
    lightbox_zoom_slot,
    lightbox_zoombar_inside,
    pane_layout,
    parse_copied_files,
    path_from_collins_uri,
    path_from_file_uri,
    plan_reroot,
    renamed_path,
    reroot_counterparts,
    should_highlight,
)
from collins.projectfiles import FollowScope, follow_scope, is_inside, list_dir, list_entries, walk_files


def _plan(old_root, new_root, open_paths, dirty_paths=frozenset()):
    """`plan_reroot` with the counterparts the service's `fs.stat` would
    call files: those that are files on this disk."""
    files = {p for p in reroot_counterparts(old_root, new_root, open_paths) if os.path.isfile(p)}
    return plan_reroot(old_root, new_root, open_paths, dirty_paths, files=files)


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


# -- first_line (of the text fs.read answered) ---------------------------------


def test_first_line_strips_line_ending():
    assert first_line("#!/bin/bash\necho hi\n") == "#!/bin/bash"


def test_first_line_crlf():
    assert first_line("#!/usr/bin/env python3\r\nprint(1)\r\n") == "#!/usr/bin/env python3"


def test_first_line_of_nothing_is_empty():
    assert first_line("") == ""


def test_first_line_caps_chars():
    assert first_line("x" * 4096) == "x" * 512


def test_first_line_feeds_shebang_guess(tmp_path):
    line = first_line("#!/usr/bin/env bash\nset -euo pipefail\n")
    assert guess_language_id(tmp_path / "deploy", line) == "sh"


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


# -- gallery_step -------------------------------------------------------------

# Rows as (kind, key) in display order: oldest at the top, newest at the
# bottom, with a non-picture file sitting among the pictures.
_GALLERY = [
    ("image", "/i1"),
    ("image", "/i2"),
    ("file", "/f1"),
    ("image", "/i3"),
]


def test_gallery_step_next_and_previous():
    assert gallery_step(_GALLERY, "/i2", 1) == "/i3"
    assert gallery_step(_GALLERY, "/i2", -1) == "/i1"


def test_gallery_step_skips_file_rows():
    # /i2 -> /i3 steps straight over the file between them.
    assert gallery_step(_GALLERY, "/i2", 1) == "/i3"
    assert gallery_step(_GALLERY, "/i3", -1) == "/i2"


def test_gallery_step_does_not_wrap_at_the_ends():
    assert gallery_step(_GALLERY, "/i1", -1) is None
    assert gallery_step(_GALLERY, "/i3", 1) is None


def test_gallery_step_from_a_non_picture_key_is_none():
    # A file row's key names no picture, so there is nothing to step from.
    assert gallery_step(_GALLERY, "/f1", 1) is None
    assert gallery_step(_GALLERY, "/f1", -1) is None


def test_gallery_step_from_a_missing_key_is_none():
    # Struck off the list while its lightbox was still up.
    assert gallery_step(_GALLERY, "/gone", 1) is None


def test_gallery_step_single_image_has_no_neighbours():
    lone = [("image", "/only")]
    assert gallery_step(lone, "/only", 1) is None
    assert gallery_step(lone, "/only", -1) is None


def test_lightbox_layout_unrealized_window_uses_fallback():
    side, w, h = lightbox_layout(800, 500, 0, 0)
    assert side == "right"
    assert (w, h) == (800 + LIGHTBOX_BUTTON_STRIP + _PAD, 500 + _PAD)


def test_lightbox_layout_zero_size_image_does_not_divide_by_zero():
    side, w, h = lightbox_layout(0, 0, 1920, 1080)
    assert (w, h) == (LIGHTBOX_MIN_W, LIGHTBOX_MIN_H)


# -- should_highlight -----------------------------------------------------------


def test_should_highlight_small_file():
    assert should_highlight(1) is True
    assert should_highlight(_MAX_HIGHLIGHT_BYTES) is True


def test_should_highlight_false_above_cap():
    assert should_highlight(_MAX_HIGHLIGHT_BYTES + 1) is False


def test_should_highlight_true_for_an_unknown_size():
    assert should_highlight(None) is True


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


# -- renamed_path --------------------------------------------------------------


def test_renamed_path_rewrites_the_renamed_entry_itself():
    assert renamed_path("/p/old.py", "/p/new.py", "/p/old.py") == "/p/new.py"


def test_renamed_path_rewrites_everything_under_a_renamed_directory():
    assert renamed_path("/p/pkg", "/p/package", "/p/pkg/a/b.py") == "/p/package/a/b.py"


def test_renamed_path_leaves_untouched_paths_alone():
    assert renamed_path("/p/pkg", "/p/package", "/p/other/b.py") is None
    # A prefix match on the name, not on the directory, is not a match.
    assert renamed_path("/p/pkg", "/p/package", "/p/pkg2/b.py") is None


# -- the gnome-copied-files payload --------------------------------------------


def test_format_copied_files_leads_with_the_operation():
    assert format_copied_files(["file:///a", "file:///b"], cut=False) == "copy\nfile:///a\nfile:///b"
    assert format_copied_files(["file:///a"], cut=True) == "cut\nfile:///a"


def test_parse_copied_files_round_trips_both_operations():
    assert parse_copied_files("copy\nfile:///a\nfile:///b") == (["/a", "/b"], False)
    assert parse_copied_files("cut\nfile:///a") == (["/a"], True)


def test_parse_copied_files_unquotes_and_drops_what_isnt_local():
    assert parse_copied_files("copy\nfile:///a%20b.txt\nhttp://x/y\nfile://host/z") == (
        ["/a b.txt"],
        False,
    )


def test_parse_copied_files_takes_an_unknown_operation_as_a_copy():
    assert parse_copied_files("file:///a") == ([], False)  # no operation line at all
    assert parse_copied_files("link\nfile:///a") == (["/a"], False)
    assert parse_copied_files("") == ([], False)


# -- the collins:// URIs of the file clipboard (D35) -------------------------------


def test_collins_uri_names_the_service_and_the_path():
    assert collins_uri("svc1", "/home/u/p/a.txt") == "collins://svc1/home/u/p/a.txt"
    assert collins_uri("svc1", "/home/u/p/a b#1.txt") == "collins://svc1/home/u/p/a%20b%231.txt"
    assert path_from_collins_uri(collins_uri("svc1", "/home/u/p/a b#1.txt"), "svc1") == "/home/u/p/a b#1.txt"


def test_path_from_collins_uri_is_for_one_service():
    assert path_from_collins_uri("collins://svc1/p/a.txt", "svc1") == "/p/a.txt"
    assert path_from_collins_uri("collins://svc2/p/a.txt", "svc1") is None  # another service's paths
    assert path_from_collins_uri("collins://svc1/p/a.txt", None) is None  # no service, no paths
    assert path_from_collins_uri("file:///p/a.txt", "svc1") is None
    assert path_from_collins_uri("collins://svc1", "svc1") is None  # no path at all


def test_parse_copied_files_reads_collins_uris_and_file_uris_only_when_local():
    text = "cut\ncollins://svc1/p/a.txt\ncollins://svc2/p/b.txt\nfile:///p/c.txt\nhttp://x/y"
    assert parse_copied_files(text, "svc1", local=True) == (["/p/a.txt", "/p/c.txt"], True)
    # Not local: a file: URI names a file on the wrong machine.
    assert parse_copied_files(text, "svc1", local=False) == (["/p/a.txt"], True)
    # No service known: only this machine's files, and only when local.
    assert parse_copied_files(text, None, local=True) == (["/p/c.txt"], True)
    assert parse_copied_files(text, None, local=False) == ([], True)


# -- following the session's working directory ---------------------------------


def _worktree(repo, name="wt"):
    path = repo / ".claude" / "worktrees" / name
    path.mkdir(parents=True)
    return path


def test_follow_scope_takes_a_worktree_of_the_same_project(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert follow_scope(repo, str(_worktree(repo))) is FollowScope.AUTO


def test_follow_scope_takes_a_subdirectory_of_the_project(tmp_path):
    repo = tmp_path / "repo"
    (repo / "packages" / "api").mkdir(parents=True)
    assert follow_scope(repo, str(repo / "packages" / "api")) is FollowScope.AUTO


def test_follow_scope_takes_the_way_back_out_of_a_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    worktree = _worktree(repo)
    # Rooted *in* the worktree, the repository it belongs to is still home...
    assert follow_scope(worktree, str(repo)) is FollowScope.AUTO
    # ...and so is a sibling worktree of that same repository.
    assert follow_scope(worktree, str(_worktree(repo, "other"))) is FollowScope.AUTO


def test_follow_scope_takes_the_way_back_out_of_a_subdirectory(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    api = repo / "packages" / "api"
    api.mkdir(parents=True)
    web = repo / "packages" / "web"
    web.mkdir()
    # Rooted at a subdirectory the editor already followed the agent into, the
    # repository around it is the boundary — not the subdirectory itself, or
    # the way back out would read as leaving the project.
    assert follow_scope(api, str(repo)) is FollowScope.AUTO
    assert follow_scope(api, str(web)) is FollowScope.AUTO


def test_follow_scope_only_offers_a_sibling_repository(tmp_path):
    checkouts = tmp_path / "dev"
    repo = checkouts / "repo"
    (repo / ".git").mkdir(parents=True)
    other = checkouts / "other"
    (other / ".git").mkdir(parents=True)
    # The enclosing checkout tops the walk out: a sibling repository is its
    # own project even though a plain directory holds both.
    assert follow_scope(repo, str(other)) is FollowScope.OFFER


def test_follow_scope_only_offers_somewhere_else_entirely(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    assert follow_scope(repo, str(other)) is FollowScope.OFFER


def test_follow_scope_ignores_a_non_move(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert follow_scope(repo, str(repo)) is FollowScope.NONE
    assert follow_scope(repo, str(repo) + "/.") is FollowScope.NONE
    assert follow_scope(repo, None) is FollowScope.NONE
    assert follow_scope(repo, str(tmp_path / "gone")) is FollowScope.NONE
    assert follow_scope(repo, str(repo / "file.txt")) is FollowScope.NONE


def test_plan_reroot_reloads_a_clean_file_that_exists_over_there(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.py").write_text("old")
    worktree = _worktree(repo)
    (worktree / "src").mkdir(parents=True)
    (worktree / "src" / "a.py").write_text("new")

    (entry,) = _plan(repo, worktree, [str(repo / "src" / "a.py")])
    assert entry.target == str(worktree / "src" / "a.py")
    assert entry.default is RerootAction.RELOAD
    assert not entry.needs_asking


def test_plan_reroot_leaves_a_dirty_file_but_asks_about_it(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("old")
    worktree = _worktree(repo)
    (worktree / "a.py").write_text("new")

    path = str(repo / "a.py")
    (entry,) = _plan(repo, worktree, [path], {path})
    assert entry.default is RerootAction.LEAVE
    assert entry.needs_asking


def test_plan_reroot_never_asks_when_there_is_nothing_to_move_to(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("old")
    worktree = _worktree(repo)  # no a.py over there at all

    path = str(repo / "a.py")
    (entry,) = _plan(repo, worktree, [path], {path})
    assert entry.target is None
    assert entry.default is RerootAction.LEAVE
    assert not entry.needs_asking


def test_plan_reroot_leaves_a_file_that_was_never_inside_the_old_root(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "notes.md"
    outside.write_text("x")

    (entry,) = _plan(repo, _worktree(repo), [str(outside)])
    assert entry.target is None
    assert entry.default is RerootAction.LEAVE


def test_plan_reroot_keeps_the_order_it_was_given(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    worktree = _worktree(repo)
    for name in ("a.py", "b.py", "c.py"):
        (repo / name).write_text("x")
        (worktree / name).write_text("y")
    paths = [str(repo / name) for name in ("c.py", "a.py", "b.py")]
    assert [entry.path for entry in _plan(repo, worktree, paths)] == paths


def test_plan_reroot_leaves_a_directory_counterpart_alone(tmp_path):
    # The counterpart has to be a *file*: a path that is a directory over there
    # would otherwise be handed to the editor to open.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docs").write_text("a file here")
    worktree = _worktree(repo)
    (worktree / "docs").mkdir()

    (entry,) = _plan(repo, worktree, [str(repo / "docs")])
    assert entry.target is None
    assert entry.default is RerootAction.LEAVE


# -- pane_layout ---------------------------------------------------------------


def test_pane_layout_wide_is_always_split():
    assert pane_layout(False, 0, False) is PaneLayout.SPLIT
    assert pane_layout(False, 3, False) is PaneLayout.SPLIT
    assert pane_layout(False, 3, True) is PaneLayout.SPLIT


def test_pane_layout_narrow_shows_the_open_file():
    assert pane_layout(True, 1, False) is PaneLayout.FILES


def test_pane_layout_narrow_with_nothing_open_shows_the_picker():
    assert pane_layout(True, 0, False) is PaneLayout.PICKER
    assert pane_layout(True, 0, True) is PaneLayout.PICKER


def test_pane_layout_narrow_back_button_shows_the_picker():
    assert pane_layout(True, 2, True) is PaneLayout.PICKER


# -- fence_language_id ---------------------------------------------------------


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        ("python", "python3"),
        ("py", "python3"),
        ("python3", "python3"),
        ("js", "js"),
        ("javascript", "js"),
        ("ts", "js"),
        ("typescript", "js"),
        ("jsx", "js"),
        ("tsx", "js"),
        ("bash", "sh"),
        ("sh", "sh"),
        ("zsh", "sh"),
        ("shell", "sh"),
        ("console", "sh"),
        ("yml", "yaml"),
        ("yaml", "yaml"),
        ("json", "json"),
        ("css", "css"),
        ("html", "html"),
        ("c", "c"),
        ("cpp", "cpp"),
        ("c++", "cpp"),
        ("rust", "rust"),
        ("rs", "rust"),
        ("go", "go"),
        ("diff", "diff"),
        ("patch", "diff"),
        ("xml", "xml"),
        ("toml", "toml"),
        ("ini", "ini"),
        ("md", "markdown"),
        ("markdown", "markdown"),
    ],
)
def test_fence_language_alias_map(info, expected):
    assert fence_language_id(info) == expected


def test_fence_language_takes_the_first_word_lowercased():
    assert fence_language_id("Python extra words") == "python3"
    assert fence_language_id("  BASH\t") == "sh"
    assert fence_language_id("json5 title") is None


def test_fence_language_suggestion_unknown_and_empty_are_none():
    assert fence_language_id("suggestion") is None
    assert fence_language_id("kotlin") is None
    assert fence_language_id("") is None
    assert fence_language_id("   ") is None


# -- the service's reads (projectfiles, PR-2.4) and the image pages' guard ----


def test_list_entries_marks_a_symlinked_directory_and_says_when_it_cut(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "pkg").mkdir(parents=True)
    (root / "a.txt").write_text("x")
    (root / "alias").symlink_to(root / "pkg")
    (root / "same.txt").symlink_to(root / "a.txt")
    entries, truncated = list_entries(root, root=root)
    assert entries == [("alias", "symlink"), ("pkg", "dir"), ("a.txt", "file"), ("same.txt", "file")]
    assert truncated is False
    monkeypatch.setattr("collins.projectfiles.MAX_DIR_ENTRIES", 2)
    entries, truncated = list_entries(root, root=root)
    assert [name for name, _kind in entries] == ["alias", "pkg"] and truncated is True


def test_image_stat_guard():
    assert image_stat_guard("file", 10) is LoadGuard.OK
    assert image_stat_guard("dir", None) is LoadGuard.NOT_A_FILE
    assert image_stat_guard("missing", None) is LoadGuard.NOT_A_FILE
    assert image_stat_guard("file", _MAX_IMAGE_BYTES + 1) is LoadGuard.TOO_LARGE


def test_walk_files_caps_the_folders_it_queues(tmp_path):
    for index in range(5):
        (tmp_path / f"d{index}").mkdir()
        (tmp_path / f"d{index}" / "f.txt").write_text("x")
    paths, truncated = walk_files(tmp_path, dirs_cap=3)
    assert truncated is True and paths == ["d0/f.txt", "d1/f.txt"]
