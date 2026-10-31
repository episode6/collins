# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The tree copies in flight (split-service spec §8, PR-3.6): `fs.paste`
records each top temporary directory in `state.json`'s `inflight_copies`
while it builds a tree and clears it when the tree lands or is undone; a
service that starts over an unfinished copy removes exactly that tree.
GTK-free."""

from __future__ import annotations

import os

from collins import projectfiles
from collins import state as state_mod


class Recorder:
    def __init__(self) -> None:
        self.begun: list[tuple[str, str]] = []
        self.ended: list[object] = []

    def begin(self, dest: str, name: str) -> object:
        self.begun.append((dest, name))
        return (dest, name)

    def end(self, token: object) -> None:
        self.ended.append(token)


def test_a_tree_copy_is_recorded_until_it_lands(tmp_path):
    root = tmp_path / "proj"
    (root / "src" / "deep").mkdir(parents=True)
    (root / "src" / "deep" / "a.txt").write_text("a")
    (root / "dst").mkdir()
    recorder = Recorder()
    outcomes = projectfiles.paste_entries(root, root / "dst", [str(root / "src")], inflight=recorder)
    assert outcomes[0].error is None and (root / "dst" / "src" / "deep" / "a.txt").read_text() == "a"
    # The top temporary directory, once (a subdirectory's temporary name is
    # inside the tree: the top's removal takes it), under the destination,
    # and cleared once the tree landed.
    assert len(recorder.begun) == 1
    dest, name = recorder.begun[0]
    assert dest == str(root / "dst") and projectfiles._TEMP_NAME_RE.match(name)
    assert recorder.ended == [(dest, name)]
    assert not (root / "dst" / name).exists()
    # A file copy records nothing: there is no tree to leave behind.
    recorder = Recorder()
    (root / "one.txt").write_text("1")
    projectfiles.paste_entries(root, root / "dst", [str(root / "one.txt")], inflight=recorder)
    assert recorder.begun == [] and recorder.ended == []


def test_a_failed_tree_copy_is_cleared_with_its_undo(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.txt").write_text("a")
    (root / "dst").mkdir()

    def refused(*_args):
        raise OSError(5, "the landing failed")

    monkeypatch.setattr(projectfiles, "_land_in_place", refused)
    recorder = Recorder()
    outcomes = projectfiles.paste_entries(root, root / "dst", [str(root / "src")], inflight=recorder)
    assert outcomes[0].error is not None
    assert len(recorder.begun) == 1 and recorder.ended == [recorder.begun[0]]
    assert not (root / "dst" / recorder.begun[0][1]).exists()


def test_remove_inflight_takes_only_a_temporary_directory(tmp_path):
    dest = tmp_path / "dst"
    dest.mkdir()
    name = projectfiles._temp_name()
    tree = dest / name
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "f").write_text("x")
    os.chmod(tree / "sub", 0o500)  # a read-only folder goes too (ours)
    assert projectfiles.remove_inflight(str(dest), name) is True
    assert not tree.exists()
    # Not a temporary name, a link, a file, a missing entry: nothing happens.
    (dest / "keep").mkdir()
    assert projectfiles.remove_inflight(str(dest), "keep") is False and (dest / "keep").is_dir()
    other = projectfiles._temp_name()
    os.symlink(dest / "keep", dest / other)
    assert projectfiles.remove_inflight(str(dest), other) is False and (dest / "keep").is_dir()
    assert os.path.islink(dest / other)
    filename = projectfiles._temp_name()
    (dest / filename).write_text("not a tree")
    assert projectfiles.remove_inflight(str(dest), filename) is False and (dest / filename).is_file()
    assert projectfiles.remove_inflight(str(dest), projectfiles._temp_name()) is False
    assert projectfiles.remove_inflight(str(dest / "gone"), name) is False
    assert projectfiles.remove_inflight(42, name) is False
    assert projectfiles.remove_inflight(str(dest), 42) is False


def test_the_state_row_round_trips_and_is_cleaned(app_state):
    state = app_state.AppState()
    state.add_inflight_copy("/p/dst", ".collins-0123456789abcdef01234567")
    state.add_inflight_copy("/p/dst", ".collins-0123456789abcdef01234567")  # once
    state.add_inflight_copy("/q", ".collins-aaaaaaaaaaaaaaaaaaaaaaaa")
    assert state.get_inflight_copies() == [
        {"dest": "/p/dst", "name": ".collins-0123456789abcdef01234567"},
        {"dest": "/q", "name": ".collins-aaaaaaaaaaaaaaaaaaaaaaaa"},
    ]
    again = app_state.AppState()
    assert again.get_inflight_copies() == state.get_inflight_copies()
    again.remove_inflight_copy("/q", ".collins-aaaaaaaaaaaaaaaaaaaaaaaa")
    again.remove_inflight_copy("/never", "recorded")
    assert app_state.AppState().get_inflight_copies() == [
        {"dest": "/p/dst", "name": ".collins-0123456789abcdef01234567"}
    ]
    again.clear_inflight_copies()
    assert app_state.AppState().get_inflight_copies() == []
    # The cleaner (rule 5): only entries with both strings, non-empty.
    good = {"dest": "/a", "name": "n"}
    raw = [good, {"dest": "", "name": "n"}, {"dest": "/a"}, "x", {"dest": 1, "name": "n"}]
    assert state_mod._clean_inflight_copies(raw) == [{"dest": "/a", "name": "n"}]
    assert state_mod._clean_inflight_copies("nope") == []
