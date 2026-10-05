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
    # The pre-check on each source's realpath, and (D47) the held parent
    # directory's own path for the one that passed it.
    assert asked == [
        str(root.resolve() / "a.txt"),
        str(root.resolve()),
        str(outside.resolve()),
        str(outside.resolve()),
    ]
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
    """The move's `EXDEV` branch without a second mount: the primitive
    answers as another filesystem would (the placeholder path is never
    reached: `EXDEV` from step 1 is step 3 at once)."""
    import errno

    def exdev(*args, **kwargs):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(projectfiles, "_rename_noreplace", exdev)


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
    monkeypatch.setattr(projectfiles, "_rename_noreplace", lambda *a: False)
    (tmp_path / "dest").mkdir()
    (tmp_path / "tree").mkdir()
    (tmp_path / "tree" / "a.txt").write_text("a")
    real_mkdir = projectfiles.os.mkdir
    planted: list = []

    def mkdir_then_plant(path, *args, **kwargs):
        real_mkdir(path, *args, **kwargs)
        if not planted:
            planted.append(path)
            inside = os.path.join(path, "theirs.txt")
            fd = os.open(inside, os.O_WRONLY | os.O_CREAT, 0o644, dir_fd=kwargs.get("dir_fd"))
            os.write(fd, b"theirs")
            os.close(fd)

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
        monkeypatch.setattr(projectfiles, "_rename_noreplace", lambda *a: False)
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
        monkeypatch.setattr(projectfiles, "_rename_noreplace", lambda *a: False)
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
    monkeypatch.setattr(projectfiles, "_rename_noreplace", lambda *a: False)
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


def _record_calls(monkeypatch) -> list[tuple]:
    """A recording wrapper on the `os` calls an undo is made of: `("made",
    fd)` for a file created exclusively, `("lstat", name)`, `("unlink",
    name)` and `("close", fd)`, in the order they were called."""
    events: list[tuple] = []
    real_open, real_close = projectfiles.os.open, projectfiles.os.close
    real_lstat, real_unlink = projectfiles.os.lstat, projectfiles.os.unlink

    def open_(path, flags, mode=0o777, *, dir_fd=None):
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if flags & os.O_EXCL:
            events.append(("made", fd))
        return fd

    def close(fd):
        events.append(("close", fd))
        return real_close(fd)

    def lstat(path, *, dir_fd=None):
        events.append(("lstat", os.fspath(path)))
        return real_lstat(path, dir_fd=dir_fd)

    def unlink(path, *, dir_fd=None):
        events.append(("unlink", os.fspath(path)))
        return real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(projectfiles.os, "open", open_)
    monkeypatch.setattr(projectfiles.os, "close", close)
    monkeypatch.setattr(projectfiles.os, "lstat", lstat)
    monkeypatch.setattr(projectfiles.os, "unlink", unlink)
    return events


def _put(dir_fd: int, name: str, text: str) -> None:
    """Another writer's file at *name* of the held directory, replacing
    whatever is there (write-temp-and-rename, as editors and agents do)."""
    fd = os.open(name + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644, dir_fd=dir_fd)
    try:
        os.write(fd, text.encode())
    finally:
        os.close(fd)
    os.rename(name + ".tmp", name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)


def test_the_placeholder_path_never_removes_something_swapped_over_its_placeholder(tmp_path, monkeypatch):
    """The placeholder's descriptor is held across the rename; a failed
    rename removes the placeholder only while `lstat` still shows the
    held inode: something swapped over it meanwhile is not ours and
    stays. The order: made `O_EXCL`, the rename, the undo's `lstat`, no
    `unlink`, and only then the descriptor closed (D46 as amended)."""
    import errno

    monkeypatch.setattr(projectfiles, "_rename_noreplace", lambda *a: False)
    (tmp_path / "dest").mkdir()
    (tmp_path / "a.txt").write_text("A")
    before = _fds()
    events = _record_calls(monkeypatch)
    real_rename = projectfiles.os.rename

    def swap_then_refuse(src, dst, *, src_dir_fd, dst_dir_fd):
        monkeypatch.setattr(projectfiles.os, "rename", real_rename)
        _put(dst_dir_fd, dst, "someone else's")
        events.append(("rename", dst))
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(projectfiles.os, "rename", swap_then_refuse)
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "a.txt")], move=True)
    monkeypatch.undo()
    assert outcome.error is PasteError.FAILED and "denied" in outcome.message
    assert (tmp_path / "dest" / "a.txt").read_text() == "someone else's"
    assert (tmp_path / "a.txt").read_text() == "A"
    (placeholder,) = [fd for kind, fd in events if kind == "made"]
    renamed = events.index(("rename", "a.txt"))
    assert events.index(("made", placeholder)) < renamed
    assert ("close", placeholder) not in events[:renamed]  # held across the rename
    after = events[renamed:]
    assert after.index(("lstat", "a.txt")) < after.index(("close", placeholder))  # the undo, then the close
    assert ("unlink", "a.txt") not in after  # the stranger's inode is not the held one
    assert _fds() == before


def test_the_placeholder_path_removes_its_own_placeholder_whatever_was_done_to_it(tmp_path, monkeypatch):
    """The held descriptor is the identity, not a stat tuple: a chmod or a
    hard link from outside between the placeholder's making and the
    failed rename (each moves its ctime) leaves it ours, and it goes."""
    import errno

    monkeypatch.setattr(projectfiles, "_rename_noreplace", lambda *a: False)
    (tmp_path / "dest").mkdir()
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "a.txt").write_text("A")

    def touch_then_refuse(src, dst, *, src_dir_fd, dst_dir_fd):
        os.chmod(dst, 0o644, dir_fd=dst_dir_fd)
        os.link(dst, tmp_path / "elsewhere" / "theirs", src_dir_fd=dst_dir_fd)
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(projectfiles.os, "rename", touch_then_refuse)
    before = _fds()
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "a.txt")], move=True)
    assert outcome.error is PasteError.FAILED and _fds() == before
    assert os.listdir(tmp_path / "dest") == [] and (tmp_path / "a.txt").read_text() == "A"
    assert os.listdir(tmp_path / "elsewhere") == ["theirs"]  # their link is theirs


def test_the_placeholder_path_meeting_another_filesystem_takes_the_copy_path(tmp_path, monkeypatch):
    """`EXDEV` from the placeholder's rename: the placeholder undone, then
    step 3, the exclusive copy and the source removed."""
    import errno

    monkeypatch.setattr(projectfiles, "_rename_noreplace", lambda *a: False)

    def exdev(*args, **kwargs):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(projectfiles.os, "rename", exdev)
    (tmp_path / "dest").mkdir()
    (tmp_path / "a.txt").write_text("A")
    os.chmod(tmp_path / "a.txt", 0o640)
    (tmp_path / "tree" / "sub").mkdir(parents=True)
    (tmp_path / "tree" / "sub" / "f").write_text("f")
    before = _fds()
    sources = [str(tmp_path / "a.txt"), str(tmp_path / "tree")]
    outcomes = paste_entries(tmp_path, tmp_path / "dest", sources, move=True)
    assert [o.error for o in outcomes] == [None, None] and _fds() == before
    assert (tmp_path / "dest" / "a.txt").read_text() == "A"
    # The copy's mode, not the placeholder's 0600.
    assert stat.S_IMODE(os.stat(tmp_path / "dest" / "a.txt").st_mode) == 0o640
    assert (tmp_path / "dest" / "tree" / "sub" / "f").read_text() == "f"
    assert sorted(os.listdir(tmp_path)) == ["dest"]


# -- the holds (D47): nothing is named by path again once the hold is lost ---------------


def _fds() -> int:
    return len(os.listdir("/proc/self/fd"))


def _swap_for_link(path: Path, to: Path) -> None:
    """*path* (a directory or a file) replaced by a symlink to *to*."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
    path.symlink_to(to)


def test_a_destination_swapped_for_a_link_out_after_the_check_is_refused_outside(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "dest").mkdir(parents=True)
    (root / "a.txt").write_text("A")
    outside = tmp_path / "outside"
    outside.mkdir()
    real = projectfiles.paste_target

    def check_then_swap(*args, **kwargs):
        target, error = real(*args, **kwargs)
        if target is not None:
            _swap_for_link(root / "dest", outside)
        return target, error

    monkeypatch.setattr(projectfiles, "paste_target", check_then_swap)
    before = _fds()
    (outcome,) = paste_entries(root, root / "dest", [str(root / "a.txt")])
    assert outcome.error is PasteError.OUTSIDE and os.listdir(outside) == [] and _fds() == before
    # The hold is taken once for the whole clipboard: a cut the same.
    (outcome,) = paste_entries(root, root / "dest", [str(root / "a.txt")], move=True)
    assert outcome.error is PasteError.OUTSIDE and (root / "a.txt").exists() and os.listdir(outside) == []


def test_a_source_whose_parent_is_swapped_for_a_link_out_is_source_outside_with_nothing_read(
    tmp_path, monkeypatch
):
    root = tmp_path / "project"
    (root / "dest").mkdir(parents=True)
    (root / "sub").mkdir()
    (root / "sub" / "a.txt").write_text("mine")
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "a.txt"
    canary.write_text("canary")
    asked: list[str] = []

    def allowed(path: str) -> bool:
        asked.append(path)
        if len(asked) == 1:
            _swap_for_link(root / "sub", outside)  # after the realpath pre-check passed
        return path.startswith(str(root.resolve()))

    before = _fds()
    (outcome,) = paste_entries(root, root / "dest", [str(root / "sub" / "a.txt")], False, allowed)
    assert outcome.error is PasteError.SOURCE_OUTSIDE
    assert asked[1] == str(outside.resolve())  # the held parent's own path, confined
    assert os.listdir(root / "dest") == [] and canary.read_text() == "canary" and _fds() == before


def test_a_subdirectory_swapped_for_a_link_out_during_the_walk_is_copied_as_a_link(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "dest").mkdir(parents=True)
    (root / "tree" / "sub").mkdir(parents=True)
    (root / "tree" / "sub" / "inner.txt").write_text("inner")
    (root / "tree" / "top.txt").write_text("top")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    real_scandir = projectfiles.os.scandir
    swapped: list = []

    def swap_on_first(fd):
        if not swapped:
            swapped.append(fd)
            _swap_for_link(root / "tree" / "sub", outside)
        return real_scandir(fd)

    monkeypatch.setattr(projectfiles.os, "scandir", swap_on_first)
    before = _fds()
    (outcome,) = paste_entries(root, root / "dest", [str(root / "tree")])
    assert outcome.error is None and _fds() == before
    copied = root / "dest" / "tree"
    assert (copied / "top.txt").read_text() == "top"
    assert (copied / "sub").is_symlink() and os.readlink(copied / "sub") == str(outside)
    assert sorted(os.listdir(copied)) == ["sub", "top.txt"]  # the link itself, nothing from outside copied in
    assert os.listdir(outside) == ["secret.txt"] and (outside / "secret.txt").read_text() == "secret"


def test_a_link_planted_inside_the_new_tree_at_a_subdirectorys_name_is_an_error_entry(
    tmp_path, monkeypatch
):
    root = tmp_path / "project"
    (root / "dest").mkdir(parents=True)
    (root / "tree" / "sub").mkdir(parents=True)
    (root / "tree" / "sub" / "inner.txt").write_text("inner")
    outside = tmp_path / "outside"
    outside.mkdir()
    real_mkdir = projectfiles.os.mkdir

    def plant(path, *args, **kwargs):
        real_mkdir(path, *args, **kwargs)
        if os.fspath(path) == "sub":
            dir_fd = kwargs.get("dir_fd")
            os.rmdir(path, dir_fd=dir_fd)
            os.symlink(str(outside), path, dir_fd=dir_fd)

    monkeypatch.setattr(projectfiles.os, "mkdir", plant)
    before = _fds()
    (outcome,) = paste_entries(root, root / "dest", [str(root / "tree")])
    # An error entry for the subdirectory, the tree landed short. (Linux
    # answers `ENOTDIR` for a link opened `O_DIRECTORY|O_NOFOLLOW`, `ELOOP`
    # without `O_DIRECTORY`: either way it is not followed.)
    assert outcome.error is PasteError.FAILED and _fds() == before
    # `copytree`'s shape: the entry's two paths as the request named them, then the words.
    assert repr((str(root / "tree" / "sub"), str(root / "dest" / "tree" / "sub")))[:-1] in outcome.message
    assert "Not a directory" in outcome.message or "symbolic link" in outcome.message
    assert os.listdir(outside) == []  # nothing written through
    assert (root / "dest" / "tree" / "sub").is_symlink()  # the planted link, left as it is


def test_a_source_swapped_before_the_unlink_leaves_the_stranger_and_counts_the_move_done(
    tmp_path, monkeypatch
):
    _force_copy_path(monkeypatch)
    (tmp_path / "dest").mkdir()
    (tmp_path / "a.txt").write_text("mine")
    real_lstat = projectfiles.os.lstat

    def swap_then_answer(path, *args, **kwargs):
        if os.fspath(path) == "a.txt" and _dir_of(kwargs.get("dir_fd")) == str(tmp_path.resolve()):
            (tmp_path / "a.txt").unlink()
            (tmp_path / "a.txt").write_text("stranger")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(projectfiles.os, "lstat", swap_then_answer)
    before = _fds()
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "a.txt")], move=True)
    assert outcome.error is None and outcome.target == tmp_path / "dest" / "a.txt" and _fds() == before
    assert (tmp_path / "dest" / "a.txt").read_text() == "mine"
    assert (tmp_path / "a.txt").read_text() == "stranger"


def test_a_cross_filesystem_file_whose_target_is_swapped_before_the_failing_unlink_leaves_the_stranger(
    tmp_path, monkeypatch
):
    """R1: the copy's descriptor is held across the source's `unlink`; when
    that fails the undo compares the held descriptor with what is at the
    name now, and a file another writer put there is not ours."""
    import errno

    _force_copy_path(monkeypatch)
    (tmp_path / "dest").mkdir()
    (tmp_path / "a.txt").write_text("mine")
    before = _fds()
    events = _record_calls(monkeypatch)
    recording_unlink = projectfiles.os.unlink
    dest_fd = os.open(tmp_path / "dest", os.O_RDONLY | os.O_DIRECTORY)

    def swap_target_then_refuse(path, *, dir_fd=None):
        if _dir_of(dir_fd) == str(tmp_path.resolve()):
            # The source's unlink: first a stranger replaces the copy, then the refusal.
            _put(dest_fd, "a.txt", "stranger")
            events.append(("source-unlink", path))
            raise PermissionError(errno.EACCES, "Permission denied")
        return recording_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(projectfiles.os, "unlink", swap_target_then_refuse)
    try:
        (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "a.txt")], move=True)
    finally:
        monkeypatch.undo()
        os.close(dest_fd)
    assert outcome.error is PasteError.FAILED and "denied" in outcome.message
    assert (tmp_path / "dest" / "a.txt").read_text() == "stranger"
    assert (tmp_path / "a.txt").read_text() == "mine"
    (copy,) = [fd for kind, fd in events if kind == "made"]
    refused = events.index(("source-unlink", "a.txt"))
    assert ("close", copy) not in events[:refused]  # held across the source's unlink
    after = events[refused:]
    assert after.index(("lstat", "a.txt")) < after.index(("close", copy))  # the undo, then the close
    assert ("unlink", "a.txt") not in after  # the undo found a stranger and unlinked nothing
    assert _fds() == before


def test_a_source_gone_before_or_at_its_unlink_keeps_the_copy_and_counts_the_move_done(
    tmp_path, monkeypatch
):
    """The undo is for a source that is still there (an `unlink` refused
    is all-or-nothing). A source that vanished after it was copied, before
    the check or between the check and the `unlink`, is the move done:
    the copy is the only one left and stays."""
    _force_copy_path(monkeypatch)
    (tmp_path / "dest").mkdir()
    (tmp_path / "a.txt").write_text("mine")
    (tmp_path / "b.txt").write_text("mine too")
    real_lstat, real_unlink = projectfiles.os.lstat, projectfiles.os.unlink

    def vanish_before_the_check(path, *, dir_fd=None):
        if os.fspath(path) == "a.txt" and _dir_of(dir_fd) == str(tmp_path.resolve()):
            real_unlink(tmp_path / "a.txt")
        return real_lstat(path, dir_fd=dir_fd)

    def vanish_at_the_unlink(path, *, dir_fd=None):
        if os.fspath(path) == "b.txt" and _dir_of(dir_fd) == str(tmp_path.resolve()):
            real_unlink(tmp_path / "b.txt")
        return real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(projectfiles.os, "lstat", vanish_before_the_check)
    monkeypatch.setattr(projectfiles.os, "unlink", vanish_at_the_unlink)
    before = _fds()
    sources = [str(tmp_path / "a.txt"), str(tmp_path / "b.txt")]
    outcomes = paste_entries(tmp_path, tmp_path / "dest", sources, move=True)
    monkeypatch.undo()
    assert [o.error for o in outcomes] == [None, None] and _fds() == before
    assert (tmp_path / "dest" / "a.txt").read_text() == "mine"
    assert (tmp_path / "dest" / "b.txt").read_text() == "mine too"
    assert sorted(os.listdir(tmp_path)) == ["dest"]


def test_a_tree_copied_across_whose_source_was_replaced_is_left_and_the_move_done(
    tmp_path, monkeypatch
):
    """The top source directory's descriptor is held to the check: a name
    that no longer holds it (the tree moved aside, a stranger's in its
    place) is not removed."""
    _force_copy_path(monkeypatch)
    (tmp_path / "dest").mkdir()
    (tmp_path / "tree").mkdir()
    (tmp_path / "tree" / "f").write_text("f")
    real_lstat = projectfiles.os.lstat

    def replace_before_the_check(path, *, dir_fd=None):
        if os.fspath(path) == "tree" and _dir_of(dir_fd) == str(tmp_path.resolve()):
            os.rename(tmp_path / "tree", tmp_path / "aside")
            (tmp_path / "tree").mkdir()
            (tmp_path / "tree" / "theirs").write_text("theirs")
        return real_lstat(path, dir_fd=dir_fd)

    monkeypatch.setattr(projectfiles.os, "lstat", replace_before_the_check)
    before = _fds()
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "tree")], move=True)
    monkeypatch.undo()
    assert outcome.error is None and _fds() == before
    assert (tmp_path / "dest" / "tree" / "f").read_text() == "f"
    assert (tmp_path / "tree" / "theirs").read_text() == "theirs"
    assert (tmp_path / "aside" / "f").read_text() == "f"


def test_a_link_moved_across_is_pinned_on_both_sides(tmp_path, monkeypatch):
    """A link cut across filesystems: the new link and the source link are
    each pinned `O_PATH|O_NOFOLLOW`. The source's `unlink` refused with
    the new link in place, the link is undone; refused after a stranger
    replaced the new link, the stranger stays; and the file the link
    names is never touched."""
    import errno

    _force_copy_path(monkeypatch)
    (tmp_path / "dest").mkdir()
    (tmp_path / "real.txt").write_text("real")
    (tmp_path / "ln").symlink_to("real.txt")
    real_unlink = projectfiles.os.unlink
    swap = [False]

    def refuse_source(path, *, dir_fd=None):
        if os.fspath(path) == "ln" and _dir_of(dir_fd) == str(tmp_path.resolve()):
            if swap[0]:
                real_unlink(tmp_path / "dest" / "ln")
                (tmp_path / "dest" / "ln").write_text("stranger")
            raise PermissionError(errno.EACCES, "Permission denied")
        return real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(projectfiles.os, "unlink", refuse_source)
    before = _fds()
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "ln")], move=True)
    assert outcome.error is PasteError.FAILED and os.listdir(tmp_path / "dest") == [] and _fds() == before
    swap[0] = True
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "ln")], move=True)
    assert outcome.error is PasteError.FAILED and _fds() == before
    landed = tmp_path / "dest" / "ln"
    assert not landed.is_symlink() and landed.read_text() == "stranger"
    assert os.readlink(tmp_path / "ln") == "real.txt" and (tmp_path / "real.txt").read_text() == "real"
    monkeypatch.undo()
    _force_copy_path(monkeypatch)
    os.unlink(tmp_path / "dest" / "ln")
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "ln")], move=True)
    assert outcome.error is None and os.readlink(tmp_path / "dest" / "ln") == "real.txt"
    assert not os.path.lexists(tmp_path / "ln") and (tmp_path / "real.txt").read_text() == "real"


def test_a_rename_whose_directory_is_swapped_for_a_link_out_is_outside(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a.txt").write_text("A")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.txt").write_text("theirs")
    real = projectfiles.rename_target

    def check_then_swap(*args, **kwargs):
        answer = real(*args, **kwargs)
        _swap_for_link(root / "sub", outside)
        return answer

    monkeypatch.setattr(projectfiles, "rename_target", check_then_swap)
    before = _fds()
    assert rename_entry(root, root / "sub" / "a.txt", root / "sub" / "b.txt") == (None, RenameError.OUTSIDE)
    assert os.listdir(outside) == ["a.txt"] and (outside / "a.txt").read_text() == "theirs"
    assert _fds() == before


def test_the_held_path_is_the_kernels_and_is_never_resolved_again(tmp_path):
    """`_held_path` answers where the descriptor is, whatever path it was
    opened by; a removed directory is None; one really named "x (deleted)"
    is itself; and `_held_inside` compares that answer as it stands: a
    component of it turned into a link into the root since is not
    followed back in."""
    root = tmp_path / "project"
    (root / "sub").mkdir(parents=True)
    (tmp_path / "way-in").symlink_to(root)
    fd = os.open(tmp_path / "way-in" / "sub", os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert projectfiles._held_path(fd) == str(root.resolve() / "sub")
        assert projectfiles._held_inside(tmp_path / "way-in", projectfiles._held_path(fd))
        assert projectfiles._held_inside(root / "sub", projectfiles._held_path(fd))
        assert not projectfiles._held_inside(root / "su", projectfiles._held_path(fd))
        os.rmdir(root / "sub")
        assert projectfiles._held_path(fd) is None
        (root / "sub (deleted)").mkdir()  # a stranger at the very path the kernel prints
        assert projectfiles._held_path(fd) is None
    finally:
        os.close(fd)
    named = os.open(root / "sub (deleted)", os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert projectfiles._held_path(named) == str(root.resolve() / "sub (deleted)")
    finally:
        os.close(named)
    outside = tmp_path / "outside"
    (outside / "x").mkdir(parents=True)
    held = os.open(outside / "x", os.O_RDONLY | os.O_DIRECTORY)
    try:
        path = projectfiles._held_path(held)
        os.rename(outside, tmp_path / "moved")
        outside.symlink_to(root)  # the old path now leads into the root
        (root / "x").mkdir()
        assert projectfiles.is_inside(root, path)  # what resolving it again would say
        assert not projectfiles._held_inside(root, path)
    finally:
        os.close(held)


def test_a_folder_really_named_deleted_takes_a_paste_a_rename_and_a_mkdir(tmp_path):
    folder = tmp_path / "old (deleted)"
    folder.mkdir()
    (tmp_path / "a.txt").write_text("A")
    (outcome,) = paste_entries(tmp_path, folder, [str(tmp_path / "a.txt")])
    assert outcome.error is None and (folder / "a.txt").read_text() == "A"
    assert rename_entry(tmp_path, folder / "a.txt", folder / "b.txt") == (folder / "b.txt", None)
    assert make_directory(tmp_path, folder / "made") is None and (folder / "made").is_dir()
    (outcome,) = paste_entries(tmp_path, tmp_path, [str(folder / "b.txt")], move=True)
    assert outcome.error is None and (tmp_path / "b.txt").read_text() == "A"


def test_a_destination_removed_after_the_check_is_not_a_dir(tmp_path, monkeypatch):
    (tmp_path / "dest").mkdir()
    (tmp_path / "a.txt").write_text("A")
    real_open_dir = projectfiles._open_dir

    def open_then_remove(path):
        fd = real_open_dir(path)
        if Path(path).name == "dest":
            os.rmdir(path)
        return fd

    monkeypatch.setattr(projectfiles, "_open_dir", open_then_remove)
    before = _fds()
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "a.txt")])
    assert outcome.error is PasteError.NOT_A_DIR and _fds() == before
    assert (tmp_path / "a.txt").read_text() == "A"


def test_a_mkdir_whose_parent_is_swapped_for_a_link_out_is_outside(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "sub").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    real = projectfiles._exists

    def check_then_swap(path):
        answer = real(path)
        _swap_for_link(root / "sub", outside)
        return answer

    monkeypatch.setattr(projectfiles, "_exists", check_then_swap)
    before = _fds()
    assert make_directory(root, root / "sub" / "made") is MkdirError.OUTSIDE
    assert os.listdir(outside) == [] and _fds() == before


def test_a_target_swapped_for_a_link_out_between_the_bytes_and_the_metadata_touches_nothing_outside(
    tmp_path, monkeypatch
):
    """R2: the metadata goes on the destination descriptor, never by path."""
    root = tmp_path / "project"
    (root / "dest").mkdir(parents=True)
    source = root / "run.sh"
    source.write_text("#!/bin/sh\n")
    os.chmod(source, 0o755)
    os.utime(source, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
    victim = tmp_path / "victim.txt"
    victim.write_text("victim")
    os.chmod(victim, 0o600)
    os.utime(victim, ns=(1_500_000_000_000_000_000, 1_500_000_000_000_000_000))
    real_fchmod = projectfiles.os.fchmod

    def swap_then_chmod(fd, mode):
        (root / "dest" / "run.sh").unlink()
        (root / "dest" / "run.sh").symlink_to(victim)
        return real_fchmod(fd, mode)

    monkeypatch.setattr(projectfiles.os, "fchmod", swap_then_chmod)
    (outcome,) = paste_entries(root, root / "dest", [str(source)])
    assert outcome.error is None
    st = os.stat(victim)
    assert oct(st.st_mode & 0o777) == oct(0o600) and st.st_mtime_ns == 1_500_000_000_000_000_000
    assert (root / "dest" / "run.sh").is_symlink()  # the stranger's link, untouched


def test_the_copy_keeps_mode_xattr_and_the_nanosecond_mtime(tmp_path):
    source = tmp_path / "f.bin"
    source.write_bytes(b"x" * 100_000)
    os.chmod(source, 0o640)
    os.utime(source, ns=(1_600_000_000_123_456_789, 1_600_000_000_987_654_321))
    try:
        os.setxattr(source, "user.probe", b"v")
        xattr = True
    except OSError:
        xattr = False
    (tmp_path / "dest").mkdir()
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(source)])
    st = os.stat(outcome.target)
    assert oct(st.st_mode & 0o777) == oct(0o640)
    assert st.st_mtime_ns == 1_600_000_000_987_654_321 and st.st_atime_ns == 1_600_000_000_123_456_789
    if xattr:
        assert os.getxattr(outcome.target, "user.probe") == b"v"


def test_a_copy_failing_after_bytes_went_leaves_nothing_at_the_destination(tmp_path, monkeypatch):
    import errno

    (tmp_path / "dest").mkdir()
    (tmp_path / "big.bin").write_bytes(b"x" * 100_000)
    real_sendfile = projectfiles.os.sendfile

    def write_then_fail(out_fd, in_fd, offset, count):
        sent = real_sendfile(out_fd, in_fd, offset, 1000)
        assert sent == 1000
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(projectfiles.os, "sendfile", write_then_fail)
    before = _fds()
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "big.bin")])
    assert outcome.error is PasteError.FAILED and "Input/output" in outcome.message
    assert os.listdir(tmp_path / "dest") == [] and _fds() == before
    # And a cut across filesystems, the same.
    _force_copy_path(monkeypatch)
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tmp_path / "big.bin")], move=True)
    assert outcome.error is PasteError.FAILED and os.listdir(tmp_path / "dest") == []
    assert (tmp_path / "big.bin").exists() and _fds() == before


@pytest.mark.parametrize("primitive", ["renameat2", "placeholder", "copy"])
def test_no_descriptor_is_left_open_on_any_refusal_or_failure(tmp_path, monkeypatch, primitive):
    """Every hold is closed in a `finally`: the count of open descriptors
    is the same after each refused or failed copy, move, rename and mkdir,
    on each of the move's three paths."""
    if os.geteuid() == 0:
        pytest.skip("root writes anywhere")
    if primitive == "placeholder":
        monkeypatch.setattr(projectfiles, "_rename_noreplace", lambda *a: False)
    elif primitive == "copy":
        _force_copy_path(monkeypatch)
    elif projectfiles._RENAMEAT2 is None:
        pytest.skip("no renameat2 in this libc")
    dest, locked, closed = tmp_path / "dest", tmp_path / "locked", tmp_path / "closed"
    for folder in (dest, locked, closed, tmp_path / "tree" / "sub"):
        folder.mkdir(parents=True)
    os.mkfifo(tmp_path / "pipe")
    os.mkfifo(tmp_path / "tree" / "sub" / "pipe")
    (tmp_path / "tree" / "f").write_text("f")
    (tmp_path / "unreadable.txt").write_text("U")
    os.chmod(tmp_path / "unreadable.txt", 0)
    (tmp_path / "ok.txt").write_text("ok")
    (tmp_path / "ln").symlink_to("ok.txt")
    for name in ("a.txt", "taken.txt"):
        (locked / name).write_text(name)
    (locked / "ln").symlink_to("a.txt")
    (locked / "folder").mkdir()
    (locked / "folder" / "f").write_text("f")
    os.chmod(locked, 0o555)  # nothing leaves it, nothing lands in it
    os.chmod(closed, 0o555)
    failed, missing = PasteError.FAILED, PasteError.MISSING
    before = _fds()
    try:
        # Copies: a FIFO, a device, a file that cannot be read, one that is gone, a tree holding a FIFO.
        sources = [tmp_path / "pipe", "/dev/null", tmp_path / "unreadable.txt", tmp_path / "gone"]
        sources = [str(source) for source in sources]
        assert [o.error for o in paste_entries(tmp_path, dest, sources)] == [failed, failed, failed, missing]
        assert _fds() == before and os.listdir(dest) == []
        (outcome,) = paste_entries(tmp_path, dest, [str(tmp_path / "tree")])
        assert outcome.error is failed and "Not a regular file" in outcome.message and _fds() == before
        assert (dest / "tree" / "f").read_text() == "f"  # landed short, `copytree`'s own shape
        shutil.rmtree(dest / "tree")
        # Into a folder that takes nothing: a file, a link, a tree, copied and cut.
        into = [str(tmp_path / "ok.txt"), str(tmp_path / "ln"), str(tmp_path / "tree")]
        for move in (False, True):
            assert [o.error for o in paste_entries(tmp_path, closed, into, move)] == [failed] * 3
            assert _fds() == before and os.listdir(closed) == []
        # Out of a folder that gives nothing up: a file, a link, a tree, cut.
        out = [str(locked / "a.txt"), str(locked / "ln"), str(locked / "folder")]
        assert [o.error for o in paste_entries(tmp_path, dest, out, True)] == [failed] * 3
        assert _fds() == before
        assert sorted(os.listdir(locked)) == ["a.txt", "folder", "ln", "taken.txt"]
        # A file or a link whose source could not be unlinked is undone; a tree is kept (D46).
        assert os.listdir(dest) == (["folder"] if primitive == "copy" else [])
        # A destination that is gone, a rename onto a taken name and one refused, a mkdir refused.
        (outcome,) = paste_entries(tmp_path, tmp_path / "gone-dest", [str(tmp_path / "ok.txt")])
        assert outcome.error is PasteError.NOT_A_DIR and _fds() == before
        assert rename_entry(tmp_path, locked / "a.txt", locked / "taken.txt") == (None, RenameError.EXISTS)
        assert rename_entry(tmp_path, tmp_path / "gone", tmp_path / "new") == (None, RenameError.MISSING)
        with pytest.raises(PermissionError):
            rename_entry(tmp_path, locked / "a.txt", locked / "b.txt")
        with pytest.raises(PermissionError):
            make_directory(tmp_path, locked / "made")
        assert make_directory(tmp_path, locked / "folder") is MkdirError.EXISTS
        assert _fds() == before
        assert sorted(os.listdir(locked)) == ["a.txt", "folder", "ln", "taken.txt"]
    finally:
        os.chmod(locked, 0o755)
        os.chmod(closed, 0o755)


def test_a_held_descriptor_keeps_its_inode_number(tmp_path):
    """The undo's premise (D46 as amended): an inode number is free only
    once the last link and the last descriptor are gone, so a file whose
    descriptor the service holds cannot be the one swapped over its name,
    whatever the filesystem's reuse habit. A thousand rounds of create,
    hold, unlink, create again: the new file never has the held one's
    `(st_dev, st_ino)`. The unheld variant of the same round (create,
    close, unlink, create again) is recorded, not asserted: ext4 hands the
    number straight back (CI's runner, where the first check on the inode
    alone removed a stranger's file), tmpfs does not."""
    name = tmp_path / "f"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL

    def make() -> tuple[int, os.stat_result]:
        fd = os.open(name, flags, 0o600)
        return fd, os.fstat(fd)

    reused_held = reused_free = 0
    for _ in range(1000):
        fd, free = make()
        os.close(fd)
        os.unlink(name)
        fd, again = make()
        os.close(fd)
        os.unlink(name)
        reused_free += projectfiles._same_inode(again, free)

        fd, held = make()
        try:
            os.unlink(name)
            other, swapped = make()
            os.close(other)
            os.unlink(name)
            reused_held += projectfiles._same_inode(swapped, held)
        finally:
            os.close(fd)
    print(f"inode number reused while held: {reused_held}/1000; unheld: {reused_free}/1000")
    assert reused_held == 0


def _force_copy_path(monkeypatch):
    """The copy path without a second mount: the primitive answers `EXDEV`,
    as another filesystem would."""
    import errno

    def exdev(*args, **kwargs):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(projectfiles, "_rename_noreplace", exdev)


def _dir_of(dir_fd) -> str | None:
    """The held directory a patched `os` call was given, by its own path."""
    return None if dir_fd is None else os.readlink(f"/proc/self/fd/{dir_fd}")


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
        if _dir_of(kwargs.get("dir_fd")) == str(tmp_path.resolve()):
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
    real_stat = projectfiles.os.stat
    seen: list = []

    def swap(path, *args, **kwargs):
        # The copy's own stat (the second one under the held directory, after
        # `_is_dir_entry`'s) says regular; the open right after it meets a link.
        answer = real_stat(path, *args, **kwargs)
        if os.fspath(path) == "a.txt" and kwargs.get("dir_fd") is not None and stat.S_ISREG(answer.st_mode):
            seen.append(path)
            if len(seen) == 2:
                source.unlink()
                source.symlink_to(victim)
        return answer

    monkeypatch.setattr(projectfiles.os, "stat", swap)
    (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(source)])
    assert outcome.error is PasteError.FAILED and "symbolic" in outcome.message
    assert os.listdir(tmp_path / "dest") == [] and victim.read_text() == "secret"


def test_renameat2_answers_the_name_taken_and_not_here(tmp_path, monkeypatch):
    """`_rename_noreplace` itself: True on a move, `FileExistsError` on a
    taken name, False where the flag is not supported, and the move's
    own error otherwise."""
    import errno

    if projectfiles._RENAMEAT2 is None:
        pytest.skip("no renameat2 in this libc")
    cwd = projectfiles._AT_FDCWD
    (tmp_path / "a").write_text("a")
    (tmp_path / "b").write_text("b")
    assert projectfiles._rename_noreplace(cwd, tmp_path / "a", cwd, tmp_path / "c") is True
    with pytest.raises(FileExistsError):
        projectfiles._rename_noreplace(cwd, tmp_path / "c", cwd, tmp_path / "b")
    with pytest.raises(FileNotFoundError):
        projectfiles._rename_noreplace(cwd, tmp_path / "gone", cwd, tmp_path / "d")
    assert sorted(os.listdir(tmp_path)) == ["b", "c"]
    # Under held directories, as the operations call it.
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert projectfiles._rename_noreplace(fd, "c", fd, "d") is True
        with pytest.raises(FileExistsError):
            projectfiles._rename_noreplace(fd, "d", fd, "b")
    finally:
        os.close(fd)
    # A NUL in either name is refused before the call, as `os.rename` refuses it.
    with pytest.raises(ValueError):
        projectfiles._rename_noreplace(cwd, tmp_path / "d", cwd, str(tmp_path / "cut") + "\x00/../../x")
    with pytest.raises(ValueError):
        projectfiles._rename_noreplace(cwd, "d\x00", cwd, tmp_path / "e")
    assert sorted(os.listdir(tmp_path)) == ["b", "d"]

    class Fake:
        def __init__(self, code):
            self.code = code

        def __call__(self, *args):
            ctypes.set_errno(self.code)
            return -1

    for code in sorted(projectfiles._NOT_HERE_ERRNOS):
        monkeypatch.setattr(projectfiles, "_RENAMEAT2", Fake(code))
        assert projectfiles._rename_noreplace(cwd, tmp_path / "d", cwd, tmp_path / "e") is False
    monkeypatch.setattr(projectfiles, "_RENAMEAT2", Fake(errno.EXDEV))
    with pytest.raises(OSError) as raised:
        projectfiles._rename_noreplace(cwd, tmp_path / "d", cwd, tmp_path / "e")
    assert raised.value.errno == errno.EXDEV
    monkeypatch.setattr(projectfiles, "_RENAMEAT2", None)
    assert projectfiles._rename_noreplace(cwd, tmp_path / "d", cwd, tmp_path / "e") is False


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



def test_a_copied_tree_keeps_what_copytree_kept(tmp_path):
    """The walk's parity with `shutil.copytree(symlinks=True)` and `copy2`:
    every file's bytes, mode and nanosecond times, every directory's mode
    and times (set after its entries, so filling it did not move them), a
    `user.` xattr on a file and on a directory, a link as a link (to a
    file, to a directory, dangling), an empty directory, a name that is
    not UTF-8."""
    tree = tmp_path / "tree"
    (tree / "pkg" / "deep").mkdir(parents=True)
    (tree / "empty").mkdir()
    (tree / "pkg" / "run.sh").write_text("#!/bin/sh\n")
    (tree / "pkg" / "deep" / "data.bin").write_bytes(os.urandom(70_000))
    (tree / "top.txt").write_text("top")
    odd = os.fsencode(tree) + b"/bad-\xff-name"
    with open(odd, "wb") as handle:
        handle.write(b"odd")
    (tree / "to-file").symlink_to("top.txt")
    (tree / "to-dir").symlink_to("pkg")
    (tree / "dangling").symlink_to("nowhere")
    os.chmod(tree / "pkg" / "run.sh", 0o750)
    os.chmod(tree / "top.txt", 0o400)
    xattr = True
    try:
        os.setxattr(tree / "top.txt", "user.probe", b"on a file")
        os.setxattr(tree / "pkg", "user.probe", b"on a directory")
    except OSError:
        xattr = False
    stamp = 1_600_000_000_123_456_789
    entries = sorted(tree.rglob("*"), reverse=True) + [tree]  # children before their directory
    for index, path in enumerate(entries):
        if not path.is_symlink():
            os.utime(path, ns=(stamp + index, stamp + 1000 + index))
    os.chmod(tree / "pkg" / "deep", 0o500)  # the copy must fill a directory it then makes read-only
    os.chmod(tree / "empty", 0o711)
    (tmp_path / "dest").mkdir()
    before = _fds()
    try:
        (outcome,) = paste_entries(tmp_path, tmp_path / "dest", [str(tree)])
        assert outcome.error is None and _fds() == before
        copied = tmp_path / "dest" / "tree"
        assert sorted(os.listdir(os.fsencode(copied))) == sorted(os.listdir(os.fsencode(tree)))
        for index, path in enumerate(entries):
            twin = copied / path.relative_to(tree)
            if path.is_symlink():
                assert twin.is_symlink() and os.readlink(twin) == os.readlink(path)
                continue
            was, now = os.stat(path), os.stat(twin)
            assert stat.S_IFMT(now.st_mode) == stat.S_IFMT(was.st_mode), path
            assert stat.S_IMODE(now.st_mode) == stat.S_IMODE(was.st_mode), path
            assert now.st_mtime_ns == stamp + 1000 + index, path
            if not path.is_dir():  # a directory's atime moves with every listing, this test's too
                assert now.st_atime_ns == stamp + index, path
            assert now.st_ino != was.st_ino
        data = Path("pkg") / "deep" / "data.bin"
        assert (copied / data).read_bytes() == (tree / data).read_bytes()
        with open(os.fsencode(copied) + b"/bad-\xff-name", "rb") as handle:
            assert handle.read() == b"odd"
        if xattr:
            assert os.getxattr(copied / "top.txt", "user.probe") == b"on a file"
            assert os.getxattr(copied / "pkg", "user.probe") == b"on a directory"
    finally:
        for folder in (tree, tmp_path / "dest" / "tree"):
            if (folder / "pkg" / "deep").is_dir():
                os.chmod(folder / "pkg" / "deep", 0o755)

