"""Tests for the project's disk rules the service runs (collins.projectfiles,
split-service spec §3.23, PR-2.5): the rename in place (`rename_name_error`,
`rename_target`, `rename_entry`), the paste that never overwrites
(`unique_target`, `paste_target`, `paste_entries`, with the service's
source confinement) and the new folder (`make_directory`). The directory
reads' tests (PR-2.4) are still in test_editorfiles.py."""

import os
import shutil
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
    """The move's `EXDEV` branch without a second mount: `os.link` and a
    directory's `os.rename` answer as another filesystem would."""
    import errno

    def exdev(*args, **kwargs):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(projectfiles.os, "link", exdev)
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


def test_a_cut_of_a_folder_never_replaces_an_entry_planted_inside_its_landing(tmp_path, monkeypatch):
    """`os.mkdir` then `os.rename`: the rename replaces only the empty
    directory just made; an entry planted inside it fails `ENOTEMPTY`."""
    (tmp_path / "dest").mkdir()
    (tmp_path / "tree").mkdir()
    (tmp_path / "tree" / "a.txt").write_text("a")
    real_mkdir = projectfiles.os.mkdir

    def mkdir_then_plant(path, *args, **kwargs):
        real_mkdir(path, *args, **kwargs)
        (Path(path) / "theirs.txt").write_text("theirs")

    monkeypatch.setattr(projectfiles.os, "mkdir", mkdir_then_plant)
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "tree")], move=True)
    assert outcome.error is PasteError.FAILED and "not empty" in outcome.message.lower()
    assert (tmp_path / "dest" / "tree" / "theirs.txt").read_text() == "theirs"
    assert (tmp_path / "tree" / "a.txt").read_text() == "a"  # nothing moved


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
