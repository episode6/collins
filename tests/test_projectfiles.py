"""Tests for the project's disk rules the service runs (collins.projectfiles,
split-service spec §3.23, PR-2.5): the rename in place (`rename_name_error`,
`rename_target`, `rename_entry`), the paste that never overwrites
(`unique_target`, `paste_target`, `paste_entries`, with the service's
source confinement) and the new folder (`make_directory`). The directory
reads' tests (PR-2.4) are still in test_editorfiles.py."""

import os

from collins.projectfiles import (
    MkdirError,
    PasteError,
    RenameError,
    make_directory,
    paste_entries,
    paste_target,
    rename_entry,
    rename_name_error,
    rename_target,
    unique_target,
)


def _touch(root, rel):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")


# -- rename_name_error ---------------------------------------------------------


def test_rename_name_error_is_the_pure_half():
    assert rename_name_error("/p/a.txt", "   ") is RenameError.EMPTY
    for name in ("sub/b.txt", "../b.txt", "/etc/passwd", ".", "..", "b\x00.txt"):
        assert rename_name_error("/p/a.txt", name) is RenameError.NOT_A_NAME, name
    assert rename_name_error("/p/a.txt", "a.txt") is False
    assert rename_name_error("/p/a.txt", " a.txt ") is False
    assert rename_name_error("/p/a.txt", "b.txt") is None


# -- rename_target -------------------------------------------------------------


def test_rename_target_is_the_new_name_in_the_same_directory(tmp_path):
    _touch(tmp_path, "pkg/old.py")
    target, error = rename_target(tmp_path, tmp_path / "pkg/old.py", "new.py")
    assert error is None
    assert target == tmp_path / "pkg" / "new.py"


def test_rename_target_renames_directories_too(tmp_path):
    (tmp_path / "pkg").mkdir()
    target, error = rename_target(tmp_path, tmp_path / "pkg", "package")
    assert (target, error) == (tmp_path / "package", None)


def test_rename_target_trims_surrounding_whitespace(tmp_path):
    _touch(tmp_path, "a.txt")
    target, error = rename_target(tmp_path, tmp_path / "a.txt", "  b.txt  ")
    assert (target, error) == (tmp_path / "b.txt", None)


def test_rename_target_unchanged_name_is_nothing_to_do(tmp_path):
    _touch(tmp_path, "a.txt")
    assert rename_target(tmp_path, tmp_path / "a.txt", "a.txt") == (None, None)


def test_rename_target_empty_name_is_refused(tmp_path):
    _touch(tmp_path, "a.txt")
    assert rename_target(tmp_path, tmp_path / "a.txt", "   ") == (None, RenameError.EMPTY)


def test_rename_target_refuses_anything_that_isnt_a_bare_name(tmp_path):
    _touch(tmp_path, "a.txt")
    path = tmp_path / "a.txt"
    for name in ("sub/b.txt", "../b.txt", "/etc/passwd", ".", "..", "b\x00.txt"):
        assert rename_target(tmp_path, path, name) == (None, RenameError.NOT_A_NAME), name


def test_rename_target_refuses_an_existing_name(tmp_path):
    _touch(tmp_path, "a.txt")
    _touch(tmp_path, "b.txt")
    assert rename_target(tmp_path, tmp_path / "a.txt", "b.txt") == (None, RenameError.EXISTS)


def test_rename_target_refuses_an_existing_name_that_is_a_broken_symlink(tmp_path):
    _touch(tmp_path, "a.txt")
    (tmp_path / "b.txt").symlink_to(tmp_path / "gone.txt")
    assert rename_target(tmp_path, tmp_path / "a.txt", "b.txt") == (None, RenameError.EXISTS)


def test_rename_target_refuses_when_the_source_is_gone(tmp_path):
    assert rename_target(tmp_path, tmp_path / "a.txt", "b.txt") == (None, RenameError.MISSING)


def test_rename_target_refuses_landing_outside_the_project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _touch(root, "a.txt")
    # A rename inside a directory that is itself a symlink out of the project
    # keeps the bare name and still lands outside it.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.txt").write_text("x")
    (root / "link").symlink_to(outside)
    assert rename_target(root, root / "link" / "a.txt", "b.txt") == (None, RenameError.OUTSIDE)


# -- unique_target -------------------------------------------------------------


def test_unique_target_keeps_a_free_name(tmp_path):
    assert unique_target(tmp_path, "a.txt") == tmp_path / "a.txt"


def test_unique_target_numbers_around_a_taken_name(tmp_path):
    _touch(tmp_path, "a.txt")
    assert unique_target(tmp_path, "a.txt") == tmp_path / "a (copy).txt"
    _touch(tmp_path, "a (copy).txt")
    assert unique_target(tmp_path, "a.txt") == tmp_path / "a (copy 2).txt"
    _touch(tmp_path, "a (copy 2).txt")
    assert unique_target(tmp_path, "a.txt") == tmp_path / "a (copy 3).txt"


def test_unique_target_keeps_the_suffix_and_handles_dotfiles(tmp_path):
    _touch(tmp_path, "archive.tar")
    (tmp_path / ".bashrc").write_text("x")
    (tmp_path / "pkg").mkdir()
    assert unique_target(tmp_path, "archive.tar") == tmp_path / "archive (copy).tar"
    assert unique_target(tmp_path, ".bashrc") == tmp_path / ".bashrc (copy)"
    assert unique_target(tmp_path, "pkg") == tmp_path / "pkg (copy)"


def test_unique_target_keeps_a_tarballs_whole_extension(tmp_path):
    _touch(tmp_path, "archive.tar.gz")
    _touch(tmp_path, "notes.2026.txt")
    assert unique_target(tmp_path, "archive.tar.gz") == tmp_path / "archive (copy).tar.gz"
    # Only ".tar" earns the exception: any other dot in a name is part of it.
    assert unique_target(tmp_path, "notes.2026.txt") == tmp_path / "notes.2026 (copy).txt"


def test_unique_target_counts_a_broken_symlink_as_taken(tmp_path):
    (tmp_path / "a.txt").symlink_to(tmp_path / "gone.txt")
    assert unique_target(tmp_path, "a.txt") == tmp_path / "a (copy).txt"


def test_unique_target_gives_up_once_every_name_is_taken(tmp_path):
    _touch(tmp_path, "a.txt")
    _touch(tmp_path, "a (copy).txt")
    for n in range(2, 101):
        _touch(tmp_path, f"a (copy {n}).txt")
    assert unique_target(tmp_path, "a.txt") is None


# -- paste_target --------------------------------------------------------------


def test_paste_target_is_the_name_inside_the_destination(tmp_path):
    _touch(tmp_path, "a.txt")
    (tmp_path / "pkg").mkdir()
    target, error = paste_target(tmp_path, tmp_path / "pkg", tmp_path / "a.txt")
    assert (target, error) == (tmp_path / "pkg" / "a.txt", None)


def test_paste_target_sidesteps_a_name_already_there(tmp_path):
    _touch(tmp_path, "a.txt")
    target, error = paste_target(tmp_path, tmp_path, tmp_path / "a.txt")
    assert (target, error) == (tmp_path / "a (copy).txt", None)


def test_paste_target_takes_a_source_from_outside_the_project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _touch(tmp_path, "elsewhere/a.txt")
    target, error = paste_target(root, root, tmp_path / "elsewhere" / "a.txt")
    assert (target, error) == (root / "a.txt", None)


def test_paste_target_refuses_a_destination_outside_the_project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _touch(root, "a.txt")
    outside = tmp_path / "outside"
    outside.mkdir()
    assert paste_target(root, outside, root / "a.txt") == (None, PasteError.OUTSIDE)


def test_paste_target_refuses_a_destination_that_is_gone(tmp_path):
    _touch(tmp_path, "a.txt")
    assert paste_target(tmp_path, tmp_path / "nope", tmp_path / "a.txt") == (
        None,
        PasteError.NOT_A_DIR,
    )


def test_paste_target_refuses_a_source_that_is_gone(tmp_path):
    assert paste_target(tmp_path, tmp_path, tmp_path / "gone.txt") == (None, PasteError.MISSING)


def test_paste_target_refuses_a_folder_into_itself_or_its_own_contents(tmp_path):
    (tmp_path / "pkg" / "sub").mkdir(parents=True)
    pkg = tmp_path / "pkg"
    assert paste_target(tmp_path, pkg, pkg) == (None, PasteError.INTO_ITSELF)
    assert paste_target(tmp_path, pkg / "sub", pkg) == (None, PasteError.INTO_ITSELF)


def test_paste_target_copying_a_folder_beside_itself_is_fine(tmp_path):
    (tmp_path / "pkg").mkdir()
    target, error = paste_target(tmp_path, tmp_path, tmp_path / "pkg")
    assert (target, error) == (tmp_path / "pkg (copy)", None)


def test_paste_target_moving_into_the_folder_it_came_from_is_nothing_to_do(tmp_path):
    _touch(tmp_path, "pkg/a.txt")
    assert paste_target(tmp_path, tmp_path / "pkg", tmp_path / "pkg" / "a.txt", move=True) == (
        None,
        None,
    )
    # Copying it there is still a copy, though.
    target, error = paste_target(tmp_path, tmp_path / "pkg", tmp_path / "pkg" / "a.txt")
    assert (target, error) == (tmp_path / "pkg" / "a (copy).txt", None)


# -- paste_entries -------------------------------------------------------------


def test_paste_entries_copies_a_file_and_leaves_the_original(tmp_path):
    _touch(tmp_path, "a.txt")
    (tmp_path / "pkg").mkdir()
    (outcome,) = paste_entries(tmp_path, tmp_path / "pkg", [str(tmp_path / "a.txt")])
    assert outcome.target == tmp_path / "pkg" / "a.txt"
    assert outcome.error is None
    assert (tmp_path / "pkg" / "a.txt").read_text() == "x"
    assert (tmp_path / "a.txt").exists()


def test_paste_entries_moves_a_file_for_a_cut(tmp_path):
    _touch(tmp_path, "a.txt")
    (tmp_path / "pkg").mkdir()
    (outcome,) = paste_entries(tmp_path, tmp_path / "pkg", [str(tmp_path / "a.txt")], move=True)
    assert outcome.target == tmp_path / "pkg" / "a.txt"
    assert not (tmp_path / "a.txt").exists()


def test_paste_entries_copies_a_whole_folder(tmp_path):
    _touch(tmp_path, "pkg/sub/a.txt")
    (tmp_path / "dest").mkdir()
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "pkg")])
    assert outcome.target == tmp_path / "dest" / "pkg"
    assert (tmp_path / "dest" / "pkg" / "sub" / "a.txt").read_text() == "x"


def test_paste_entries_never_overwrites_what_is_already_there(tmp_path):
    _touch(tmp_path, "a.txt")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.txt").write_text("mine")
    (outcome,) = paste_entries(tmp_path, tmp_path / "pkg", [str(tmp_path / "a.txt")])
    assert outcome.target == tmp_path / "pkg" / "a (copy).txt"
    assert (tmp_path / "pkg" / "a.txt").read_text() == "mine"


def test_paste_entries_copies_a_symlink_as_a_symlink(tmp_path):
    _touch(tmp_path, "a.txt")
    (tmp_path / "link.txt").symlink_to(tmp_path / "a.txt")
    (tmp_path / "pkg").mkdir()
    (outcome,) = paste_entries(tmp_path, tmp_path / "pkg", [str(tmp_path / "link.txt")])
    assert outcome.target == tmp_path / "pkg" / "link.txt"
    assert (tmp_path / "pkg" / "link.txt").is_symlink()


def test_paste_entries_carries_on_past_one_that_cant_be_pasted(tmp_path):
    _touch(tmp_path, "a.txt")
    _touch(tmp_path, "b.txt")
    (tmp_path / "pkg").mkdir()
    outcomes = paste_entries(
        tmp_path,
        tmp_path / "pkg",
        [str(tmp_path / "a.txt"), str(tmp_path / "gone.txt"), str(tmp_path / "b.txt")],
    )
    assert [o.target for o in outcomes] == [
        tmp_path / "pkg" / "a.txt",
        None,
        tmp_path / "pkg" / "b.txt",
    ]
    assert [o.error for o in outcomes] == [None, PasteError.MISSING, None]


def test_paste_entries_reports_a_failed_copy_with_the_os_message(tmp_path):
    source = tmp_path / "a.txt"
    source.write_text("x")
    dest = tmp_path / "pkg"
    dest.mkdir()
    dest.chmod(0o500)  # readable, not writable: the copy itself fails
    try:
        (outcome,) = paste_entries(tmp_path, dest, [str(source)])
    finally:
        dest.chmod(0o700)
    assert outcome.target is None
    assert outcome.error is PasteError.FAILED
    assert outcome.message


# -- rename_entry (fs.rename's work) -------------------------------------------


def test_rename_entry_renames_in_place(tmp_path):
    _touch(tmp_path, "a.txt")
    landed, error = rename_entry(tmp_path, tmp_path / "a.txt", tmp_path / "b.txt")
    assert (landed, error) == (tmp_path / "b.txt", None)
    assert (tmp_path / "b.txt").read_text() == "x" and not (tmp_path / "a.txt").exists()


def test_rename_entry_refuses_another_directory_and_an_existing_name(tmp_path):
    _touch(tmp_path, "a.txt")
    _touch(tmp_path, "sub/c.txt")
    moved = rename_entry(tmp_path, tmp_path / "a.txt", tmp_path / "sub" / "a.txt")
    assert moved == (None, RenameError.NOT_A_NAME)
    assert rename_entry(tmp_path, tmp_path / "a.txt", tmp_path / "sub") == (None, RenameError.EXISTS)
    assert (tmp_path / "a.txt").exists() and (tmp_path / "sub" / "c.txt").exists()
    assert rename_entry(tmp_path, tmp_path / "a.txt", tmp_path / "a.txt") == (None, None)


def test_rename_entry_refuses_an_entry_outside_the_root(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _touch(tmp_path, "other/x.txt")
    other = tmp_path / "other"
    assert rename_entry(root, other / "x.txt", other / "y.txt") == (None, RenameError.OUTSIDE)
    assert (tmp_path / "other" / "x.txt").exists()


# -- the service's source confinement -------------------------------------------


def test_paste_entries_asks_source_allowed_on_the_resolved_source(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "pkg").mkdir()
    _touch(root, "a.txt")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (root / "link.txt").symlink_to(outside)
    asked: list[str] = []

    def allowed(real: str) -> bool:
        asked.append(real)
        return real.startswith(str(root.resolve()))

    sources = [str(root / "a.txt"), str(outside), str(root / "link.txt")]
    outcomes = paste_entries(root, root / "pkg", sources, False, allowed)
    assert [o.error for o in outcomes] == [None, PasteError.SOURCE_OUTSIDE, PasteError.SOURCE_OUTSIDE]
    assert asked == [str(root.resolve() / "a.txt"), str(outside.resolve()), str(outside.resolve())]
    assert sorted(os.listdir(root / "pkg")) == ["a.txt"]


# -- make_directory (fs.mkdir's work) ----------------------------------------------


def test_make_directory_makes_one_folder_inside_the_root(tmp_path):
    assert make_directory(tmp_path, tmp_path / "new") is None
    assert (tmp_path / "new").is_dir()
    assert make_directory(tmp_path, tmp_path / "new") is MkdirError.EXISTS
    _touch(tmp_path, "a.txt")
    assert make_directory(tmp_path, tmp_path / "a.txt") is MkdirError.EXISTS
    (tmp_path / "broken").symlink_to(tmp_path / "gone")
    assert make_directory(tmp_path, tmp_path / "broken") is MkdirError.EXISTS
    assert make_directory(tmp_path, tmp_path / "no" / "parent") is MkdirError.NO_PARENT
    assert make_directory(tmp_path, tmp_path / "..") is MkdirError.NOT_A_NAME
    root = tmp_path / "project"
    root.mkdir()
    assert make_directory(root, tmp_path / "elsewhere") is MkdirError.OUTSIDE
    other = tmp_path / "other"
    other.mkdir()
    (root / "link").symlink_to(other)
    assert make_directory(root, root / "link" / "made") is MkdirError.OUTSIDE
    assert not (other / "made").exists() and not (tmp_path / "elsewhere").exists()
