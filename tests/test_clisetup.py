"""Tests for clisetup — the launch-time question of where the Claude Code
CLI is, and what counts as a good answer. GTK-free, like the module."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from collins import clisetup


def _make_exec(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A throwaway $HOME, so known_locations() and ~ point nowhere real."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


# -- validate ----------------------------------------------------------------


def test_empty_is_missing(home):
    assert clisetup.validate("") == clisetup.MISSING
    assert clisetup.validate("   ") == clisetup.MISSING


def test_nonexistent_is_missing(home):
    assert clisetup.validate(str(home / "nope" / "claude")) == clisetup.MISSING


def test_non_executable_is_missing(home):
    path = home / "bin" / "claude"
    path.parent.mkdir(parents=True)
    path.write_text("#!/bin/sh\n")  # exists, not executable
    assert clisetup.validate(str(path)) == clisetup.MISSING


def test_directory_is_missing(home):
    (home / "claude").mkdir()
    assert clisetup.validate(str(home / "claude")) == clisetup.MISSING


def test_executable_named_claude_is_ok(home):
    path = _make_exec(home / "tools" / "claude")
    assert clisetup.validate(str(path)) == clisetup.OK


def test_tilde_is_expanded(home):
    _make_exec(home / "tools" / "claude")
    assert clisetup.validate("~/tools/claude") == clisetup.OK


def test_wrong_name_is_bad_name(home):
    path = _make_exec(home / "tools" / "claude-wrapper")
    assert clisetup.validate(str(path)) == clisetup.BAD_NAME


def test_version_in_directory_is_versioned(home):
    # The native installer's real binary: …/versions/<semver>.
    path = _make_exec(home / ".local" / "share" / "claude" / "versions" / "2.1.226")
    assert clisetup.validate(str(path)) == clisetup.VERSIONED


def test_version_in_any_component_is_versioned(home):
    # An nvm-style tree: the version rides in a parent directory.
    path = _make_exec(home / ".nvm" / "versions" / "node" / "v20.1.0" / "bin" / "claude")
    assert clisetup.validate(str(path)) == clisetup.VERSIONED


def test_versioned_wins_over_bad_name(home):
    # "…/versions/2.1.226" fails both rules; the versioned answer is the
    # one that explains what to point at instead.
    path = _make_exec(home / "versions" / "2.1.226")
    assert clisetup.validate(str(path)) == clisetup.VERSIONED


def test_lone_number_component_is_not_versioned(home):
    # A single run of digits is a name, not a version — only dotted runs
    # ("2.1") read as one.
    path = _make_exec(home / "area51" / "claude")
    assert clisetup.validate(str(path)) == clisetup.OK


def test_symlink_is_judged_unresolved(home):
    # The stable launcher is a symlink into a versioned tree. Judging the
    # symlink — not its target — is the whole point.
    target = _make_exec(home / ".local" / "share" / "claude" / "versions" / "2.1.226")
    link = home / ".local" / "bin" / "claude"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    assert clisetup.validate(str(link)) == clisetup.OK


# -- detect ------------------------------------------------------------------


def test_detect_finds_the_native_launcher(home):
    link = home / ".local" / "bin" / "claude"
    target = _make_exec(home / ".local" / "share" / "claude" / "versions" / "2.1.226")
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    assert clisetup.detect() == str(link)  # the symlink, never its target


def test_detect_skips_locations_that_do_not_validate(home):
    # An empty file where the launcher would be, and a real install in a
    # later location: the later one wins.
    bad = home / ".local" / "bin" / "claude"
    bad.parent.mkdir(parents=True)
    bad.write_text("")  # not executable
    good = _make_exec(home / "bin" / "claude")
    assert clisetup.detect() == str(good)


def test_detect_empty_when_nowhere(home):
    assert clisetup.detect() == ""


# -- apply -------------------------------------------------------------------


def test_apply_appends_to_path(home, monkeypatch):
    path = _make_exec(home / "tools" / "claude")
    monkeypatch.setenv("PATH", "/usr/bin")
    assert clisetup.apply(str(path)) is True
    # Appended, not prepended: the picked directory never shadows system
    # tools for the rest of the app.
    assert os.environ["PATH"] == f"/usr/bin{os.pathsep}{home / 'tools'}"
    assert clisetup.on_path()


def test_apply_is_idempotent(home, monkeypatch):
    path = _make_exec(home / "tools" / "claude")
    monkeypatch.setenv("PATH", f"/usr/bin{os.pathsep}{home / 'tools'}")
    assert clisetup.apply(str(path)) is True
    assert os.environ["PATH"] == f"/usr/bin{os.pathsep}{home / 'tools'}"


def test_apply_expands_tilde(home, monkeypatch):
    _make_exec(home / "tools" / "claude")
    monkeypatch.setenv("PATH", "/usr/bin")
    assert clisetup.apply("~/tools/claude") is True
    assert str(home / "tools") in os.environ["PATH"].split(os.pathsep)


def test_apply_stale_dir_reports_not_found(home, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent-for-test")
    assert clisetup.apply(str(home / "gone" / "claude")) is False


# -- apply_saved -------------------------------------------------------------


class _State:
    def __init__(self, value: str) -> None:
        self.value = value

    def get_setting(self, key: str):
        assert key == clisetup.PATH_SETTING
        return self.value


def test_apply_saved_applies_a_stored_path(home, monkeypatch):
    path = _make_exec(home / "tools" / "claude")
    monkeypatch.setenv("PATH", "/usr/bin")
    clisetup.apply_saved(_State(str(path)))
    assert clisetup.on_path()


def test_apply_saved_ignores_the_empty_default(home, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    clisetup.apply_saved(_State(""))
    assert os.environ["PATH"] == "/usr/bin"
