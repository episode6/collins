"""Tests for the project's disk rules the service runs (collins.projectfiles,
split-service spec §3.23, PR-2.5): the rename in place (`rename_name_error`,
`rename_target`, `rename_entry`), the paste that never overwrites
(`unique_target`, `paste_target`, `paste_entries`, with the service's
source confinement) and the new folder (`make_directory`). The directory
reads' tests (PR-2.4) are still in test_editorfiles.py."""

import ctypes
import os
import shutil
import stat
from pathlib import Path

import pytest

from collins import projectfiles
from collins.projectfiles import (
    MkdirError,
    PasteError,
    RenameError,
    make_directory,
    name_error,
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


def test_make_directory_takes_a_folder_named_tilde(tmp_path):
    """The name check is `name_error`, not `rename_name_error` against a
    placeholder sibling that a folder called "~" happened to equal
    (review of PR 613)."""
    assert make_directory(tmp_path, tmp_path / "~") is None
    assert (tmp_path / "~").is_dir()
    assert name_error("~") is None and name_error("") is RenameError.EMPTY
    assert name_error("a/b") is RenameError.NOT_A_NAME


def test_make_directory_a_name_taken_between_the_check_and_the_mkdir_is_exists(tmp_path, monkeypatch):
    real_exists = projectfiles._exists

    def plant(path):
        taken = real_exists(path)
        if not taken:
            path.mkdir()  # someone else lands first
        return taken

    monkeypatch.setattr(projectfiles, "_exists", plant)
    assert make_directory(tmp_path, tmp_path / "new") is MkdirError.EXISTS


# -- the placement is exclusive (D44) ------------------------------------------------
#
# Each race is made deterministic by patching `unique_target` (the chooser
# `paste_entries` asks for the next free name) or `rename_target` to plant
# something at the name it answers, once: what the agent in the same
# folder, another client's request or a hostile symlink would do between
# the check and the placement. Nothing is ever written over or through
# what was planted; the paste lands as "(copy)", the rename is `exists`.


def _plant_once(monkeypatch, plant):
    real = projectfiles.unique_target
    planted: list = []

    def chooser(directory, name):
        target = real(directory, name)
        if target is not None and not planted:
            planted.append(target)
            plant(target)
        return target

    monkeypatch.setattr(projectfiles, "unique_target", chooser)
    return planted


def test_paste_lands_beside_a_file_created_between_the_check_and_the_copy(tmp_path, monkeypatch):
    (tmp_path / "dest").mkdir()
    (tmp_path / "src.txt").write_text("pasted")
    _plant_once(monkeypatch, lambda t: t.write_text("the agent wrote this in between"))
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "src.txt")])
    assert outcome.error is None and outcome.target == tmp_path / "dest" / "src (copy).txt"
    assert (tmp_path / "dest" / "src.txt").read_text() == "the agent wrote this in between"
    assert outcome.target.read_text() == "pasted"


def test_paste_never_writes_through_a_symlink_planted_at_its_name(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "dest").mkdir(parents=True)
    (root / "src.txt").write_text("pasted")
    victim = tmp_path / "outside" / "victim.txt"
    victim.parent.mkdir()
    victim.write_text("victim")
    _plant_once(monkeypatch, lambda t: t.symlink_to(victim))
    (outcome,) = paste_entries(root, root / "dest", [str(root / "src.txt")])
    assert outcome.error is None and outcome.target == root / "dest" / "src (copy).txt"
    assert victim.read_text() == "victim"
    assert (root / "dest" / "src.txt").is_symlink()  # the planted link, untouched
    assert outcome.target.read_text() == "pasted" and not outcome.target.is_symlink()


def test_a_cuts_paste_lands_beside_a_name_taken_in_between(tmp_path, monkeypatch):
    (tmp_path / "dest").mkdir()
    (tmp_path / "src.txt").write_text("moved")
    _plant_once(monkeypatch, lambda t: t.write_text("in between"))
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "src.txt")], move=True)
    assert outcome.error is None and outcome.target == tmp_path / "dest" / "src (copy).txt"
    assert (tmp_path / "dest" / "src.txt").read_text() == "in between"
    assert outcome.target.read_text() == "moved" and not (tmp_path / "src.txt").exists()


def test_a_cuts_paste_never_writes_through_a_planted_symlink(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "dest").mkdir(parents=True)
    (root / "src.txt").write_text("moved")
    victim = tmp_path / "victim.txt"
    victim.write_text("victim")
    _plant_once(monkeypatch, lambda t: t.symlink_to(victim))
    (outcome,) = paste_entries(root, root / "dest", [str(root / "src.txt")], move=True)
    assert outcome.target == root / "dest" / "src (copy).txt" and outcome.error is None
    assert victim.read_text() == "victim" and (root / "dest" / "src.txt").is_symlink()
    assert outcome.target.read_text() == "moved" and not (root / "src.txt").exists()


def _force_cross_filesystem(monkeypatch):
    """The move's `EXDEV` branch without a second mount: the primitive and
    a directory's `os.rename` answer as another filesystem would."""
    import errno

    def exdev(*args, **kwargs):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(projectfiles, "_rename_noreplace", exdev)
    real_rename = projectfiles.os.rename

    def rename(src, dst, *args, **kwargs):
        if os.path.isdir(src):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(projectfiles.os, "rename", rename)


def test_a_cut_across_filesystems_is_an_exclusive_copy_then_the_source_removed(tmp_path, monkeypatch):
    _force_cross_filesystem(monkeypatch)
    (tmp_path / "dest").mkdir()
    (tmp_path / "src.txt").write_text("moved")
    os.chmod(tmp_path / "src.txt", 0o640)
    (tmp_path / "tree" / "sub").mkdir(parents=True)
    (tmp_path / "tree" / "sub" / "deep.txt").write_text("deep")
    (tmp_path / "tree" / "link").symlink_to("sub/deep.txt")
    victim = tmp_path / "victim.txt"
    victim.write_text("victim")
    _plant_once(monkeypatch, lambda t: t.symlink_to(victim))
    outcomes = paste_entries(
        tmp_path, tmp_path / "dest", [str(tmp_path / "src.txt"), str(tmp_path / "tree")], move=True
    )
    assert [o.error for o in outcomes] == [None, None]
    assert outcomes[0].target == tmp_path / "dest" / "src (copy).txt"
    assert victim.read_text() == "victim"
    assert outcomes[0].target.read_text() == "moved" and not (tmp_path / "src.txt").exists()
    assert oct(os.stat(outcomes[0].target).st_mode & 0o777) == oct(0o640)
    assert outcomes[1].target == tmp_path / "dest" / "tree"
    assert (tmp_path / "dest" / "tree" / "sub" / "deep.txt").read_text() == "deep"
    assert os.readlink(tmp_path / "dest" / "tree" / "link") == "sub/deep.txt"
    assert not (tmp_path / "tree").exists()


def _other_mount(tmp_path):
    """A directory on another filesystem than *tmp_path*, or None."""
    for candidate in ("/dev/shm", "/tmp", "/var/tmp"):
        try:
            if os.stat(candidate).st_dev != os.stat(tmp_path).st_dev and os.access(candidate, os.W_OK):
                return candidate
        except OSError:
            continue
    return None


def test_a_cut_across_a_real_mount_lands_exclusively(tmp_path, monkeypatch):
    import tempfile

    mount = _other_mount(tmp_path)
    if mount is None:
        pytest.skip("no second filesystem on this runner")
    elsewhere = Path(tempfile.mkdtemp(prefix="collins-xdev-", dir=mount))
    try:
        (elsewhere / "src.txt").write_text("moved")
        (elsewhere / "tree" / "sub").mkdir(parents=True)
        (elsewhere / "tree" / "sub" / "deep.txt").write_text("deep")
        (tmp_path / "dest").mkdir()
        _plant_once(monkeypatch, lambda t: t.write_text("in between"))
        outcomes = paste_entries(
            tmp_path, tmp_path / "dest", [str(elsewhere / "src.txt"), str(elsewhere / "tree")], move=True
        )
        assert [o.error for o in outcomes] == [None, None]
        assert outcomes[0].target == tmp_path / "dest" / "src (copy).txt"
        assert (tmp_path / "dest" / "src.txt").read_text() == "in between"
        assert outcomes[0].target.read_text() == "moved"
        assert (tmp_path / "dest" / "tree" / "sub" / "deep.txt").read_text() == "deep"
        assert not (elsewhere / "src.txt").exists() and not (elsewhere / "tree").exists()
    finally:
        shutil.rmtree(elsewhere, ignore_errors=True)


def test_a_tree_lands_beside_a_name_taken_in_between_and_never_into_a_planted_one(tmp_path, monkeypatch):
    (tmp_path / "dest").mkdir()
    (tmp_path / "tree" / "sub").mkdir(parents=True)
    (tmp_path / "tree" / "sub" / "a.txt").write_text("a")

    def plant(target):
        # A directory of that name, with something already inside it: a
        # `copytree` that merged into it would land the tree in a stranger's
        # folder.
        target.mkdir()
        (target / "theirs.txt").write_text("theirs")

    _plant_once(monkeypatch, plant)
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "tree")])
    assert outcome.error is None and outcome.target == tmp_path / "dest" / "tree (copy)"
    assert sorted(os.listdir(tmp_path / "dest" / "tree")) == ["theirs.txt"]
    assert (outcome.target / "sub" / "a.txt").read_text() == "a"
    # The same for a cut: `os.mkdir` at the name is the exclusive step.
    (tmp_path / "tree2" / "sub").mkdir(parents=True)
    _plant_once(monkeypatch, plant)
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "tree2")], move=True)
    assert outcome.error is None and outcome.target == tmp_path / "dest" / "tree2 (copy)"
    assert sorted(os.listdir(tmp_path / "dest" / "tree2")) == ["theirs.txt"]
    assert (outcome.target / "sub").is_dir() and not (tmp_path / "tree2").exists()


def test_a_cut_of_a_folder_never_replaces_an_entry_planted_inside_its_placeholder(tmp_path, monkeypatch):
    """The placeholder path (`renameat2` not supported): `os.mkdir` then
    `os.rename`, which replaces only the empty directory just made; an
    entry planted inside it fails `ENOTEMPTY`, which counts as the name
    taken: the paste lands as "(copy)" and the placeholder stays with
    what is not ours (D44 as amended, D46)."""
    monkeypatch.setattr(projectfiles, "_rename_noreplace", lambda src, target: False)
    (tmp_path / "dest").mkdir()
    (tmp_path / "tree").mkdir()
    (tmp_path / "tree" / "a.txt").write_text("a")
    real_mkdir = projectfiles.os.mkdir
    planted: list = []

    def mkdir_then_plant(path, *args, **kwargs):
        real_mkdir(path, *args, **kwargs)
        if not planted:
            planted.append(path)
            (Path(path) / "theirs.txt").write_text("theirs")

    monkeypatch.setattr(projectfiles.os, "mkdir", mkdir_then_plant)
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "tree")], move=True)
    assert outcome.error is None and outcome.target == tmp_path / "dest" / "tree (copy)"
    assert os.listdir(tmp_path / "dest" / "tree") == ["theirs.txt"]  # the placeholder, left
    assert (outcome.target / "a.txt").read_text() == "a" and not (tmp_path / "tree").exists()


# -- the move's primitive (D44 as amended): renameat2(RENAME_NOREPLACE) ----------------
#
# The same matrix on the real primitive and on the placeholder path it falls
# back to (`_rename_noreplace` patched to answer "not supported here"): a
# file, a directory, a relative link, a dangling link and a FIFO moved with
# the inode kept; a name taken by a file, a link and a directory answering
# the next "(copy N)" for a paste and `exists` for a rename.


def _stage_move_matrix(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "file.txt").write_text("file")
    (src / "folder" / "in").mkdir(parents=True)
    (src / "folder" / "in" / "x").write_text("x")
    (src / "rel").symlink_to("file.txt")
    (src / "dangling").symlink_to("/nonexistent/x")
    os.mkfifo(src / "pipe")
    (tmp_path / "dest").mkdir()
    return src, [src / n for n in ("file.txt", "folder", "rel", "dangling", "pipe")]


def _inodes(paths):
    return [os.lstat(p).st_ino for p in paths]


@pytest.mark.parametrize("primitive", ["renameat2", "placeholder"])
def test_a_cut_moves_every_kind_of_entry_with_its_inode_kept(tmp_path, monkeypatch, primitive):
    if primitive == "placeholder":
        monkeypatch.setattr(projectfiles, "_rename_noreplace", lambda src, target: False)
    elif projectfiles._RENAMEAT2 is None:
        pytest.skip("no renameat2 in this libc")
    src, entries = _stage_move_matrix(tmp_path)
    inodes = _inodes(entries)
    outcomes = paste_entries(tmp_path, tmp_path / "dest", [str(e) for e in entries], move=True)
    assert [o.error for o in outcomes] == [None] * 5
    landed = [tmp_path / "dest" / e.name for e in entries]
    assert [o.target for o in outcomes] == landed
    assert _inodes(landed) == inodes
    assert os.listdir(src) == []
    assert (landed[0]).read_text() == "file" and (landed[1] / "in" / "x").read_text() == "x"
    assert os.readlink(landed[2]) == "file.txt" and os.readlink(landed[3]) == "/nonexistent/x"
    assert stat.S_ISFIFO(os.lstat(landed[4]).st_mode)
    # A rename, the same: in place, the inode kept.
    for path in landed:
        renamed, error = rename_entry(tmp_path / "dest", path, path.with_name(path.name + ".r"))
        assert error is None and os.lstat(renamed).st_ino == os.lstat(path.with_name(path.name + ".r")).st_ino
    assert sorted(os.listdir(tmp_path / "dest")) == sorted(e.name + ".r" for e in entries)


@pytest.mark.parametrize("primitive", ["renameat2", "placeholder"])
@pytest.mark.parametrize("taken_by", ["file", "link", "dir"])
def test_a_name_taken_in_between_is_the_next_copy_for_a_paste_and_exists_for_a_rename(
    tmp_path, monkeypatch, primitive, taken_by
):
    if primitive == "placeholder":
        monkeypatch.setattr(projectfiles, "_rename_noreplace", lambda src, target: False)
    elif projectfiles._RENAMEAT2 is None:
        pytest.skip("no renameat2 in this libc")

    def plant(target: Path) -> None:
        if taken_by == "file":
            target.write_text("theirs")
        elif taken_by == "link":
            target.symlink_to(tmp_path / "victim.txt")
        else:
            target.mkdir()
            (target / "theirs.txt").write_text("theirs")

    (tmp_path / "victim.txt").write_text("victim")
    (tmp_path / "dest").mkdir()
    (tmp_path / "a.txt").write_text("moved")
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder" / "x").write_text("x")
    _plant_once(monkeypatch, plant)
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "a.txt")], move=True)
    assert outcome.error is None and outcome.target == tmp_path / "dest" / "a (copy).txt"
    assert outcome.target.read_text() == "moved" and not (tmp_path / "a.txt").exists()
    _plant_once(monkeypatch, plant)
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "folder")], move=True)
    assert outcome.error is None and outcome.target == tmp_path / "dest" / "folder (copy)"
    assert (outcome.target / "x").read_text() == "x" and not (tmp_path / "folder").exists()
    # What was planted is exactly as planted.
    assert (tmp_path / "victim.txt").read_text() == "victim"
    for planted in (tmp_path / "dest" / "a.txt", tmp_path / "dest" / "folder"):
        if taken_by == "file":
            assert planted.read_text() == "theirs"
        elif taken_by == "link":
            assert planted.is_symlink()
        else:
            assert os.listdir(planted) == ["theirs.txt"]
    # A rename onto such a name is `exists`, nothing moved.
    (tmp_path / "b.txt").write_text("B")
    real = projectfiles.rename_target

    def plant_at_rename(root, path, name):
        target, error = real(root, path, name)
        if target is not None:
            plant(target)
        return target, error

    monkeypatch.setattr(projectfiles, "rename_target", plant_at_rename)
    assert rename_entry(tmp_path, tmp_path / "b.txt", tmp_path / "c.txt") == (None, RenameError.EXISTS)
    assert (tmp_path / "b.txt").read_text() == "B" and (tmp_path / "victim.txt").read_text() == "victim"


def test_the_placeholder_path_refuses_a_rename_it_cannot_do_and_leaves_no_placeholder(tmp_path, monkeypatch):
    if os.geteuid() == 0:
        pytest.skip("root renames anywhere")
    monkeypatch.setattr(projectfiles, "_rename_noreplace", lambda src, target: False)
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "a.txt").write_text("A")
    (tmp_path / "dest").mkdir()
    locked.chmod(0o555)
    try:
        with pytest.raises(PermissionError):
            rename_entry(locked, locked / "a.txt", locked / "b.txt")
        (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(locked / "a.txt")], move=True)
    finally:
        locked.chmod(0o755)
    assert outcome.error is PasteError.FAILED and "denied" in outcome.message
    assert os.listdir(tmp_path / "dest") == [] and os.listdir(locked) == ["a.txt"]


def test_the_placeholder_path_never_removes_something_swapped_over_its_placeholder(tmp_path, monkeypatch):
    """A failed rename removes the file placeholder only while `lstat`
    still shows the inode made: something swapped over it meanwhile is
    not ours and stays."""
    import errno

    monkeypatch.setattr(projectfiles, "_rename_noreplace", lambda src, target: False)
    (tmp_path / "dest").mkdir()
    (tmp_path / "a.txt").write_text("A")
    real_rename = projectfiles.os.rename

    def swap_then_refuse(src, dst, *args, **kwargs):
        os.unlink(dst)
        Path(dst).write_text("someone else's")
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(projectfiles.os, "rename", swap_then_refuse)
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "a.txt")], move=True)
    monkeypatch.setattr(projectfiles.os, "rename", real_rename)
    assert outcome.error is PasteError.FAILED
    assert (tmp_path / "dest" / "a.txt").read_text() == "someone else's"
    assert (tmp_path / "a.txt").read_text() == "A"


def _force_copy_path(monkeypatch):
    """The copy path without a second mount: the primitive and `os.rename`
    answer `EXDEV`, as another filesystem would."""
    import errno

    def exdev(*args, **kwargs):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(projectfiles, "_rename_noreplace", exdev)
    monkeypatch.setattr(projectfiles.os, "rename", exdev)


def test_a_file_copied_across_whose_source_cannot_be_unlinked_leaves_nothing_at_the_destination(
    tmp_path, monkeypatch
):
    """D46: the copy the service just made is removed again, the source
    intact, the entry `failed` with the `unlink`'s words; a retry is the
    same again, never a growing pile."""
    import errno

    _force_copy_path(monkeypatch)
    (tmp_path / "dest").mkdir()
    (tmp_path / "a.txt").write_text("A")
    real_unlink = projectfiles.os.unlink

    def refuse_source(path, *args, **kwargs):
        if os.fspath(path) == str(tmp_path / "a.txt"):
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(projectfiles.os, "unlink", refuse_source)
    for _ in range(2):
        (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "a.txt")], move=True)
        assert outcome.error is PasteError.FAILED and "denied" in outcome.message
        assert os.listdir(tmp_path / "dest") == []
    assert (tmp_path / "a.txt").read_text() == "A"


def test_a_cut_from_a_folder_the_user_cannot_write_fails_and_leaves_nothing(tmp_path):
    """N3 of the verification, on the real path: a `0555` source folder,
    `failed`, the destination empty, no link and no copy left, a retry
    the same."""
    if os.geteuid() == 0:
        pytest.skip("root unlinks anywhere")
    ro = tmp_path / "ro"
    ro.mkdir()
    (ro / "keep.txt").write_text("keep")
    (tmp_path / "dest").mkdir()
    ro.chmod(0o555)
    try:
        for _ in range(2):
            (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(ro / "keep.txt")], move=True)
            assert outcome.error is PasteError.FAILED and "denied" in outcome.message
            assert os.listdir(tmp_path / "dest") == []
        assert (ro / "keep.txt").read_text() == "keep"
    finally:
        ro.chmod(0o755)


def test_a_file_copied_across_a_mount_from_a_read_only_folder_leaves_nothing(tmp_path):
    """N3's case across a real second mount: the exclusive copy lands, the
    `unlink` is refused, the copy is removed again (D46)."""
    import tempfile

    if os.geteuid() == 0:
        pytest.skip("root unlinks anywhere")
    mount = _other_mount(tmp_path)
    if mount is None:
        pytest.skip("no second filesystem on this runner")
    elsewhere = Path(tempfile.mkdtemp(prefix="collins-xdev-", dir=mount))
    try:
        (elsewhere / "ro").mkdir()
        (elsewhere / "ro" / "k.txt").write_text("k")
        (elsewhere / "ro").chmod(0o555)
        (tmp_path / "dest").mkdir()
        source = str(elsewhere / "ro" / "k.txt")
        try:
            (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [source], move=True)
        finally:
            (elsewhere / "ro").chmod(0o755)
        assert outcome.error is PasteError.FAILED and "denied" in outcome.message
        assert os.listdir(tmp_path / "dest") == [] and (elsewhere / "ro" / "k.txt").read_text() == "k"
    finally:
        shutil.rmtree(elsewhere, ignore_errors=True)


def test_a_tree_copied_across_whose_rmtree_fails_keeps_both_halves(tmp_path, monkeypatch):
    """D46: part of the source may already be gone, so the copy may be
    the one complete set; both stay, the entry `failed`."""
    _force_copy_path(monkeypatch)
    (tmp_path / "dest").mkdir()
    (tmp_path / "tree" / "sub").mkdir(parents=True)
    (tmp_path / "tree" / "sub" / "a.txt").write_text("a")

    def refuse_rmtree(path, *args, **kwargs):
        raise OSError(13, "Permission denied", str(path))

    monkeypatch.setattr(projectfiles.shutil, "rmtree", refuse_rmtree)
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "tree")], move=True)
    assert outcome.error is PasteError.FAILED and "denied" in outcome.message
    assert (tmp_path / "dest" / "tree" / "sub" / "a.txt").read_text() == "a"
    assert (tmp_path / "tree" / "sub" / "a.txt").read_text() == "a"


def test_a_file_the_user_cannot_read_is_renamed_and_cut_with_its_inode(tmp_path):
    """N5 of the verification: a `0000` file (standing in for another
    owner's) moves as `rename` moved it, no read or ownership needed."""
    if os.geteuid() == 0:
        pytest.skip("root reads anything")
    theirs = tmp_path / "theirs.txt"
    theirs.write_text("T")
    theirs.chmod(0)
    inode = theirs.stat().st_ino
    (tmp_path / "dest").mkdir()
    try:
        renamed, error = rename_entry(tmp_path, theirs, tmp_path / "r.txt")
        assert error is None and renamed.stat().st_ino == inode
        (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(renamed)], move=True)
        assert outcome.error is None and outcome.target.stat().st_ino == inode
        assert not renamed.exists()
    finally:
        (tmp_path / "dest" / "r.txt").chmod(0o600)


def test_the_sendfile_fallback_copies_the_same_bytes(tmp_path, monkeypatch):
    import errno

    data = os.urandom(3 * 1024 * 1024 + 17)
    (tmp_path / "big.bin").write_bytes(data)
    (tmp_path / "dest").mkdir()
    calls: list = []

    def no_sendfile(*args, **kwargs):
        calls.append(args)
        raise OSError(errno.EINVAL, "Invalid argument")

    monkeypatch.setattr(projectfiles.os, "sendfile", no_sendfile)
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "big.bin")])
    assert outcome.error is None and outcome.target.read_bytes() == data and len(calls) == 1
    # And on the fast path itself.
    monkeypatch.undo()
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "big.bin")])
    assert outcome.target == tmp_path / "dest" / "big (copy).bin" and outcome.target.read_bytes() == data


def test_a_source_swapped_for_a_link_after_the_islink_check_is_refused(tmp_path, monkeypatch):
    """The copy's source is opened `O_NOFOLLOW`: a link swapped in after
    the check is `ELOOP`, never read through past the confinement."""
    victim = tmp_path / "victim.txt"
    victim.write_text("secret")
    (tmp_path / "dest").mkdir()
    source = tmp_path / "a.txt"
    source.write_text("A")
    real_islink = projectfiles.os.path.islink

    def swap(path):
        answer = real_islink(path)
        if os.fspath(path) == str(source) and not answer:
            source.unlink()
            source.symlink_to(victim)
        return answer

    monkeypatch.setattr(projectfiles.os.path, "islink", swap)
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(source)])
    assert outcome.error is PasteError.FAILED and os.listdir(tmp_path / "dest") == []


def test_renameat2_answers_the_name_taken_and_not_here(tmp_path, monkeypatch):
    """`_rename_noreplace` itself: True on a move, `FileExistsError` on a
    taken name, False where the flag is not supported, and the move's
    own error otherwise."""
    import errno

    if projectfiles._RENAMEAT2 is None:
        pytest.skip("no renameat2 in this libc")
    (tmp_path / "a").write_text("a")
    (tmp_path / "b").write_text("b")
    assert projectfiles._rename_noreplace(tmp_path / "a", tmp_path / "c") is True
    with pytest.raises(FileExistsError):
        projectfiles._rename_noreplace(tmp_path / "c", tmp_path / "b")
    with pytest.raises(FileNotFoundError):
        projectfiles._rename_noreplace(tmp_path / "gone", tmp_path / "d")
    assert sorted(os.listdir(tmp_path)) == ["b", "c"]

    class Fake:
        def __init__(self, code):
            self.code = code

        def __call__(self, *args):
            ctypes.set_errno(self.code)
            return -1

    for code in sorted(projectfiles._NOT_HERE_ERRNOS):
        monkeypatch.setattr(projectfiles, "_RENAMEAT2", Fake(code))
        assert projectfiles._rename_noreplace(tmp_path / "c", tmp_path / "e") is False
    monkeypatch.setattr(projectfiles, "_RENAMEAT2", Fake(errno.EXDEV))
    with pytest.raises(OSError) as raised:
        projectfiles._rename_noreplace(tmp_path / "c", tmp_path / "e")
    assert raised.value.errno == errno.EXDEV
    monkeypatch.setattr(projectfiles, "_RENAMEAT2", None)
    assert projectfiles._rename_noreplace(tmp_path / "c", tmp_path / "e") is False


def test_a_writer_that_takes_every_name_is_no_room_and_nothing_is_overwritten(tmp_path, monkeypatch):
    (tmp_path / "dest").mkdir()
    (tmp_path / "src.txt").write_text("pasted")
    real = projectfiles.unique_target

    def plant_always(directory, name):
        target = real(directory, name)
        if target is not None:
            target.write_text("taken")
        return target

    monkeypatch.setattr(projectfiles, "unique_target", plant_always)
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "src.txt")])
    assert outcome.error is PasteError.NO_ROOM and outcome.target is None
    names = os.listdir(tmp_path / "dest")
    assert len(names) == 101 and all((tmp_path / "dest" / n).read_text() == "taken" for n in names)


def test_rename_onto_a_name_taken_between_the_check_and_the_rename_is_exists(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("A")
    real = projectfiles.rename_target

    def plant(root, path, name):
        target, error = real(root, path, name)
        if target is not None:
            target.write_text("precious")
        return target, error

    monkeypatch.setattr(projectfiles, "rename_target", plant)
    assert rename_entry(tmp_path, tmp_path / "a.txt", tmp_path / "b.txt") == (None, RenameError.EXISTS)
    assert (tmp_path / "b.txt").read_text() == "precious" and (tmp_path / "a.txt").read_text() == "A"
    # A folder's rename, the same: `os.mkdir` at the name is the exclusive step.
    (tmp_path / "pkg").mkdir()
    assert rename_entry(tmp_path, tmp_path / "pkg", tmp_path / "package") == (None, RenameError.EXISTS)
    assert (tmp_path / "package").read_text() == "precious" and (tmp_path / "pkg").is_dir()


def test_rename_keeps_the_inode_and_renames_a_symlink_as_the_link(tmp_path):
    (tmp_path / "a.txt").write_text("A")
    inode = os.stat(tmp_path / "a.txt").st_ino
    landed, error = rename_entry(tmp_path, tmp_path / "a.txt", tmp_path / "b.txt")
    assert error is None and os.stat(landed).st_ino == inode
    (tmp_path / "link").symlink_to("b.txt")
    landed, error = rename_entry(tmp_path, tmp_path / "link", tmp_path / "link2")
    assert error is None and os.readlink(landed) == "b.txt" and not (tmp_path / "link").is_symlink()
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "x").write_text("x")
    landed, error = rename_entry(tmp_path, tmp_path / "pkg", tmp_path / "package")
    assert error is None and (tmp_path / "package" / "x").exists() and not (tmp_path / "pkg").exists()


def test_rename_entry_refuses_a_target_name_with_surrounding_whitespace(tmp_path):
    """A request is landed exactly where it asks or refused: the dialog's
    trimming is `rename_target`'s, the client's to do first (review of PR
    613)."""
    (tmp_path / "a.txt").write_text("A")
    assert rename_entry(tmp_path, tmp_path / "a.txt", tmp_path / " b.txt ") == (None, RenameError.NOT_A_NAME)
    assert os.listdir(tmp_path) == ["a.txt"]


def test_paste_entries_normalizes_a_source_ending_in_dot_dot(tmp_path):
    """`rootA/sub/..` is `rootA`, not an entry called ".." (review of PR 613)."""
    root_a = tmp_path / "rootA"
    root_b = tmp_path / "rootB"
    (root_a / "sub").mkdir(parents=True)
    (root_a / "keep.txt").write_text("k")
    root_b.mkdir()
    (outcome,) = paste_entries(root_b, root_b, [str(root_a / "sub") + "/.."])
    assert outcome.source == root_a and outcome.target == root_b / "rootA"
    assert sorted(os.listdir(root_b)) == ["rootA"] and (root_b / "rootA" / "keep.txt").exists()


def test_paste_refuses_a_device_or_a_fifo_as_a_source(tmp_path):
    """The exclusive copy opens the source itself and reads only a regular
    file: `/dev/zero` would fill the disk, a FIFO would wait for a writer."""
    (tmp_path / "dest").mkdir()
    os.mkfifo(tmp_path / "pipe")
    outcomes = paste_entries(tmp_path, tmp_path / "dest", ["/dev/null", str(tmp_path / "pipe")])
    assert [o.error for o in outcomes] == [PasteError.FAILED, PasteError.FAILED]
    assert all("regular file" in o.message for o in outcomes)
    assert os.listdir(tmp_path / "dest") == []


def test_copied_files_keep_their_mode_and_times(tmp_path):
    source = tmp_path / "run.sh"
    source.write_text("#!/bin/sh\n")
    os.chmod(source, 0o750)
    os.utime(source, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
    (tmp_path / "dest").mkdir()
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(source)])
    st = os.stat(outcome.target)
    assert oct(st.st_mode & 0o777) == oct(0o750) and st.st_mtime_ns == 1_600_000_000_000_000_000
