import pytest

gi = pytest.importorskip("gi")

from collins import footerapps  # noqa: E402


def test_strip_field_codes_drops_placeholders():
    argv = ["code", "%F", "--new-window", "%u", "%i", "%c", "%k"]
    assert footerapps.strip_field_codes(argv) == ["code", "--new-window"]


def test_strip_field_codes_keeps_plain_args():
    assert footerapps.strip_field_codes(["myapp", "--flag=%f-ish"]) == ["myapp", "--flag=%f-ish"]


def test_resolve_app_returns_none_for_uninstalled():
    assert footerapps.resolve_app("definitely-not-installed-xyz.desktop") is None


def test_resolve_apps_skips_stale_and_preserves_order():
    installed = footerapps.installed_apps()
    if not installed:
        pytest.skip("no .desktop entries on this system")
    real_ids = [info.get_id() for info in installed[:2] if info.get_id()]
    ids = ["stale-first.desktop", *real_ids, "stale-last.desktop"]
    resolved = footerapps.resolve_apps(ids)
    assert [app_id for app_id, _info in resolved] == real_ids


class _FakeAppInfo:
    """Stand-in for a Gio.AppInfo whose Exec line has no file placeholder."""

    def __init__(self, commandline: str) -> None:
        self._commandline = commandline

    def supports_files(self) -> bool:
        return False

    def supports_uris(self) -> bool:
        return False

    def get_commandline(self) -> str:
        return self._commandline

    def get_id(self) -> str:
        return "fake.desktop"


def test_launch_app_falls_back_to_popen_in_cwd(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(footerapps.subprocess, "Popen", lambda *a, **kw: calls.append((a, kw)))
    footerapps.launch_app(_FakeAppInfo("myapp %F --flag %u"), str(tmp_path))
    assert len(calls) == 1
    (argv,), kwargs = calls[0]
    assert argv == ["myapp", "--flag"]
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["start_new_session"] is True


def test_launch_app_missing_cwd_falls_back_to_home(tmp_path, monkeypatch):
    from pathlib import Path

    calls = []
    monkeypatch.setattr(footerapps.subprocess, "Popen", lambda *a, **kw: calls.append((a, kw)))
    footerapps.launch_app(_FakeAppInfo("myapp"), str(tmp_path / "gone"))
    assert calls[0][1]["cwd"] == str(Path.home())


def test_launch_app_swallows_popen_failure(tmp_path, monkeypatch):
    def boom(*_a, **_kw):
        raise OSError("no such executable")

    monkeypatch.setattr(footerapps.subprocess, "Popen", boom)
    footerapps.launch_app(_FakeAppInfo("myapp"), str(tmp_path))  # must not raise
