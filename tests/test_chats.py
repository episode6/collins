"""Unit tests for the throwaway chat-directory helpers (collins/chats.py)."""

import os
from types import SimpleNamespace

import pytest

from collins import chats


@pytest.fixture
def chats_dir(tmp_path, monkeypatch):
    root = tmp_path / "chats"
    monkeypatch.setattr(chats, "CHATS_DIR", root)
    return root


def test_create_chat_dir_lands_under_root(chats_dir):
    cwd = chats.create_chat_dir()
    assert os.path.isdir(cwd)
    assert os.path.dirname(cwd) == str(chats_dir)
    assert os.path.basename(cwd).startswith("chat-")


def test_create_chat_dir_makes_unique_dirs(chats_dir):
    assert chats.create_chat_dir() != chats.create_chat_dir()


def test_is_chat_cwd_true_for_created_dir(chats_dir):
    assert chats.is_chat_cwd(chats.create_chat_dir())


def test_is_chat_cwd_rejects_prefix_sibling(chats_dir):
    assert not chats.is_chat_cwd(str(chats_dir) + "-sibling/chat-abc")


def test_is_chat_cwd_rejects_root_itself(chats_dir):
    assert not chats.is_chat_cwd(str(chats_dir))
    assert not chats.is_chat_cwd(str(chats_dir) + os.sep)


def test_is_chat_cwd_rejects_none_and_empty(chats_dir):
    assert not chats.is_chat_cwd(None)
    assert not chats.is_chat_cwd("")


def test_is_chat_cwd_accepts_realpath_of_symlinked_root(tmp_path, monkeypatch):
    real = tmp_path / "real-chats"
    real.mkdir()
    link = tmp_path / "link-chats"
    link.symlink_to(real)
    monkeypatch.setattr(chats, "CHATS_DIR", link)
    # Transcripts record the physical path even when the configured root
    # goes through the symlink.
    assert chats.is_chat_cwd(str(real / "chat-abc"))
    assert chats.is_chat_cwd(str(link / "chat-abc"))


def test_delete_chat_dir_refuses_outside_root(chats_dir, tmp_path):
    victim = tmp_path / "precious"
    victim.mkdir()
    (victim / "file.txt").write_text("data")
    error = chats.delete_chat_dir(str(victim))
    assert error is not None
    assert (victim / "file.txt").exists()


def test_delete_chat_dir_removes_tree(chats_dir):
    cwd = chats.create_chat_dir()
    nested = os.path.join(cwd, "sub")
    os.makedirs(nested)
    with open(os.path.join(nested, "file.txt"), "w") as fh:
        fh.write("data")
    assert chats.delete_chat_dir(cwd) is None
    assert not os.path.exists(cwd)


def test_delete_chat_dir_missing_is_ok(chats_dir):
    assert chats.delete_chat_dir(str(chats_dir / "chat-gone")) is None


def test_trash_chat_dir_refuses_outside_root(chats_dir, tmp_path):
    victim = tmp_path / "precious"
    victim.mkdir()
    assert chats.trash_chat_dir(str(victim)) is not None
    assert victim.exists()


def test_trash_chat_dir_missing_is_ok(chats_dir):
    assert chats.trash_chat_dir(str(chats_dir / "chat-gone")) is None


def test_trash_chat_dir_invokes_gio(chats_dir, monkeypatch):
    cwd = chats.create_chat_dir()
    trashed = []
    fake_gio = SimpleNamespace(
        File=SimpleNamespace(
            new_for_path=lambda path: SimpleNamespace(trash=lambda cancellable: trashed.append(path))
        )
    )
    monkeypatch.setattr(chats, "Gio", fake_gio)
    assert chats.trash_chat_dir(cwd) is None
    assert trashed == [cwd]


def test_ensure_chat_dir_recreates_missing(chats_dir):
    cwd = str(chats_dir / "chat-lost")
    chats.ensure_chat_dir(cwd)
    assert os.path.isdir(cwd)


def test_ensure_chat_dir_ignores_non_chat_cwd(chats_dir, tmp_path):
    outside = tmp_path / "not-a-chat"
    chats.ensure_chat_dir(str(outside))
    assert not outside.exists()


def test_sweep_removes_only_empty_unreferenced(chats_dir):
    referenced_empty = chats.create_chat_dir()
    orphan_empty = chats.create_chat_dir()
    orphan_full = chats.create_chat_dir()
    with open(os.path.join(orphan_full, "artifact.txt"), "w") as fh:
        fh.write("keep me")

    chats.sweep_orphan_chat_dirs({referenced_empty})

    assert os.path.isdir(referenced_empty)
    assert not os.path.exists(orphan_empty)
    assert os.path.isdir(orphan_full)


def test_sweep_tolerates_missing_root(tmp_path, monkeypatch):
    monkeypatch.setattr(chats, "CHATS_DIR", tmp_path / "never-created")
    chats.sweep_orphan_chat_dirs(set())
