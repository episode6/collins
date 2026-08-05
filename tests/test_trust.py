"""Unit tests for the folder-trust helpers (collins/trust.py)."""

import json
import os

import pytest

from collins import sessions as sessions_mod
from collins import trust


@pytest.fixture
def claude_config(tmp_path, monkeypatch):
    config = tmp_path / "claude.json"
    monkeypatch.setattr(sessions_mod, "CLAUDE_CONFIG", config)
    return config


def _write(config, projects):
    config.write_text(json.dumps({"projects": projects}))


def _entry(config, cwd):
    return json.loads(config.read_text())["projects"].get(cwd, {}).get("hasTrustDialogAccepted")


# -- trust_root ---------------------------------------------------------------


def test_trust_root_is_the_directory_itself():
    assert trust.trust_root("/home/u/dev/proj/") == "/home/u/dev/proj"


def test_trust_root_of_a_worktree_is_its_repository():
    assert (
        trust.trust_root("/home/u/dev/proj/.claude/worktrees/lively-otter")
        == "/home/u/dev/proj"
    )


# -- is_trusted ---------------------------------------------------------------


def test_is_trusted_false_without_a_config(claude_config):
    assert not trust.is_trusted("/home/u/dev/proj")


def test_is_trusted_false_for_empty_cwd(claude_config):
    _write(claude_config, {"/": {"hasTrustDialogAccepted": True}})
    assert not trust.is_trusted("")


def test_is_trusted_reads_the_directorys_own_entry(claude_config):
    _write(claude_config, {"/home/u/dev/proj": {"hasTrustDialogAccepted": True}})
    assert trust.is_trusted("/home/u/dev/proj")


def test_is_trusted_false_when_the_entry_says_so(claude_config):
    _write(claude_config, {"/home/u/dev/proj": {"hasTrustDialogAccepted": False}})
    assert not trust.is_trusted("/home/u/dev/proj")


def test_is_trusted_false_for_an_entry_without_the_key(claude_config):
    _write(claude_config, {"/home/u/dev/proj": {"lastCost": 3}})
    assert not trust.is_trusted("/home/u/dev/proj")


def test_is_trusted_inherits_from_an_ancestor(claude_config):
    # The CLI honours a trusted parent for everything beneath it, so a project
    # under an already-trusted directory must not be asked about again.
    _write(claude_config, {"/home/u/dev": {"hasTrustDialogAccepted": True}})
    assert trust.is_trusted("/home/u/dev/proj")


def test_is_trusted_covers_worktrees_of_a_trusted_repository(claude_config):
    _write(claude_config, {"/home/u/dev/proj": {"hasTrustDialogAccepted": True}})
    assert trust.is_trusted("/home/u/dev/proj/.claude/worktrees/lively-otter")


def test_is_trusted_does_not_leak_to_a_sibling(claude_config):
    _write(claude_config, {"/home/u/dev/proj": {"hasTrustDialogAccepted": True}})
    assert not trust.is_trusted("/home/u/dev/proj-fork")


def test_is_trusted_follows_symlinked_paths(tmp_path, claude_config):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    _write(claude_config, {str(real): {"hasTrustDialogAccepted": True}})
    assert trust.is_trusted(str(link))


def test_is_trusted_tolerates_a_corrupt_config(claude_config):
    claude_config.write_text("not json {")
    assert not trust.is_trusted("/home/u/dev/proj")


# -- trust_dir ----------------------------------------------------------------


def test_trust_dir_creates_the_config(claude_config):
    assert trust.trust_dir("/home/u/dev/proj") is True
    assert _entry(claude_config, "/home/u/dev/proj") is True


def test_trust_dir_preserves_the_rest_of_the_config(claude_config):
    claude_config.write_text(
        json.dumps(
            {
                "mcpServers": {"linear": {"type": "http"}},
                "projects": {"/home/u/alpha": {"hasTrustDialogAccepted": False, "lastCost": 1}},
            }
        )
    )
    trust.trust_dir("/home/u/dev/proj")

    data = json.loads(claude_config.read_text())
    assert data["mcpServers"] == {"linear": {"type": "http"}}
    assert data["projects"]["/home/u/alpha"] == {"hasTrustDialogAccepted": False, "lastCost": 1}
    assert _entry(claude_config, "/home/u/dev/proj") is True


def test_trust_dir_writes_both_spellings_of_a_symlinked_path(tmp_path, claude_config):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)

    trust.trust_dir(str(link))

    assert _entry(claude_config, os.path.normpath(str(link))) is True
    assert _entry(claude_config, os.path.realpath(str(link))) is True


def test_trust_dir_leaves_a_corrupt_config_alone(claude_config):
    claude_config.write_text("not json {")
    assert trust.trust_dir("/home/u/dev/proj") is False
    assert claude_config.read_text() == "not json {"


def test_trust_dir_ignores_an_empty_path(claude_config):
    assert trust.trust_dir("") is False
    assert not claude_config.exists()


def test_trust_dir_answers_is_trusted(claude_config):
    trust.trust_dir("/home/u/dev/proj")
    assert trust.is_trusted("/home/u/dev/proj")
    assert trust.is_trusted("/home/u/dev/proj/.claude/worktrees/lively-otter")
