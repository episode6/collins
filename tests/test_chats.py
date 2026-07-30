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


def test_sweep_spares_the_fallback_dir(chats_dir):
    fallback = chats.fallback_chat_dir()

    chats.sweep_orphan_chat_dirs(set())  # nothing references it, and it's empty

    assert os.path.isdir(fallback)


def test_fallback_chat_dir_is_created_under_the_root(chats_dir):
    fallback = chats.fallback_chat_dir()
    assert os.path.isdir(fallback)
    assert os.path.dirname(fallback) == str(chats_dir)
    assert chats.is_fallback_chat_dir(fallback)
    # It groups under Chats like any other chat directory...
    assert chats.is_chat_cwd(fallback)
    # ...but is never mistaken for a throwaway chat of its own.
    assert not chats.is_fallback_chat_dir(chats.create_chat_dir())


def test_fallback_chat_dir_is_idempotent(chats_dir):
    assert chats.fallback_chat_dir() == chats.fallback_chat_dir()


def test_chat_cwd_or_fallback_recreates_the_chats_own_dir(chats_dir):
    cwd = chats.create_chat_dir()
    os.rmdir(cwd)  # e.g. reaped by another instance's orphan sweep

    assert chats.chat_cwd_or_fallback(cwd) == cwd
    assert os.path.isdir(cwd)


def test_chat_cwd_or_fallback_keeps_an_existing_dir(chats_dir):
    cwd = chats.create_chat_dir()
    assert chats.chat_cwd_or_fallback(cwd) == cwd


def test_chat_cwd_or_fallback_never_lands_in_home(chats_dir, tmp_path):
    gone = str(tmp_path / "deleted-project")  # not a chat dir: not recreated

    for cwd in (None, "", gone):
        landed = chats.chat_cwd_or_fallback(cwd)
        assert chats.is_fallback_chat_dir(landed)
        assert os.path.isdir(landed)
    assert not os.path.exists(gone)


def test_is_degraded_chat_cwd_flags_fallback_and_home(chats_dir, monkeypatch):
    home = chats_dir.parent / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    own = chats.create_chat_dir()

    assert chats.is_degraded_chat_cwd(own, chats.fallback_chat_dir())
    assert chats.is_degraded_chat_cwd(own, str(home))
    # Where the chat actually belongs, and a worktree it moved to itself.
    assert not chats.is_degraded_chat_cwd(own, own)
    assert not chats.is_degraded_chat_cwd(own, str(home / "dev" / "proj"))
    assert not chats.is_degraded_chat_cwd(own, None)


def test_is_degraded_chat_cwd_ignores_real_projects(chats_dir, monkeypatch):
    home = chats_dir.parent / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # A real project session that ran in $HOME chose to; leave it alone.
    assert not chats.is_degraded_chat_cwd(str(home / "dev" / "proj"), str(home))


@pytest.fixture
def claude_config(tmp_path, monkeypatch):
    config = tmp_path / "claude.json"
    monkeypatch.setattr(chats, "CLAUDE_CONFIG", config)
    return config


def _trusted(config_path, cwd):
    import json

    data = json.loads(config_path.read_text())
    return data.get("projects", {}).get(cwd, {}).get("hasTrustDialogAccepted")


def test_trust_chat_dir_creates_config_and_entry(chats_dir, claude_config):
    cwd = chats.create_chat_dir()
    chats.trust_chat_dir(cwd)
    assert _trusted(claude_config, cwd) is True


def test_trust_chat_dir_preserves_existing_config(chats_dir, claude_config):
    import json

    claude_config.write_text(
        json.dumps(
            {
                "mcpServers": {"linear": {"type": "http"}},
                "projects": {"/home/user/alpha": {"hasTrustDialogAccepted": False, "lastCost": 1}},
            }
        )
    )
    cwd = chats.create_chat_dir()
    chats.trust_chat_dir(cwd)

    data = json.loads(claude_config.read_text())
    assert data["mcpServers"] == {"linear": {"type": "http"}}
    assert data["projects"]["/home/user/alpha"] == {"hasTrustDialogAccepted": False, "lastCost": 1}
    assert _trusted(claude_config, cwd) is True


def test_trust_chat_dir_ignores_non_chat_cwd(chats_dir, claude_config, tmp_path):
    chats.trust_chat_dir(str(tmp_path / "not-a-chat"))
    assert not claude_config.exists()


def test_trust_chat_dir_tolerates_corrupt_config(chats_dir, claude_config):
    claude_config.write_text("not json {")
    cwd = chats.create_chat_dir()
    chats.trust_chat_dir(cwd)  # must not raise
    assert claude_config.read_text() == "not json {"  # left untouched


def test_trust_chat_dir_writes_realpath_key_for_symlinked_root(tmp_path, monkeypatch, claude_config):
    real = tmp_path / "real-chats"
    real.mkdir()
    link = tmp_path / "link-chats"
    link.symlink_to(real)
    monkeypatch.setattr(chats, "CHATS_DIR", link)

    cwd = chats.create_chat_dir()  # path goes through the symlink
    chats.trust_chat_dir(cwd)

    import os

    assert _trusted(claude_config, os.path.normpath(cwd)) is True
    assert _trusted(claude_config, os.path.realpath(cwd)) is True
    assert os.path.normpath(cwd) != os.path.realpath(cwd)
