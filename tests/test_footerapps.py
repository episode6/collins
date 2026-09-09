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

class _FileTakingAppInfo(_FakeAppInfo):
    """An app that advertises %u — a terminal must still be spawned in the
    directory rather than handed it as an argument."""

    def supports_uris(self) -> bool:
        return True

    def launch(self, *_args) -> None:
        raise AssertionError("the directory must not be passed as an argument")


def test_launch_app_can_refuse_to_pass_the_directory(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(footerapps.subprocess, "Popen", lambda *a, **kw: calls.append((a, kw)))
    footerapps.launch_app(_FileTakingAppInfo("myterm %u"), str(tmp_path), pass_directory=False)
    assert calls[0][0] == (["myterm"],)
    assert calls[0][1]["cwd"] == str(tmp_path)


# -- a file to an app (the git page's files-list "Open In…") -------------------------------


class _FileEditorAppInfo(_FakeAppInfo):
    """An app that takes a file (%f): what the git page's "Open In…" lists.
    *fail* makes its launch raise the GLib.Error a broken entry would."""

    def __init__(self, commandline: str = "edit %f", fail: bool = False) -> None:
        super().__init__(commandline)
        self.launched: list = []
        self._fail = fail

    def supports_files(self) -> bool:
        return True

    def launch(self, files, context) -> None:
        if self._fail:
            raise footerapps.GLib.Error("no display")
        self.launched.append([f.get_path() for f in files])


def test_accepts_files_reads_the_exec_placeholders():
    assert not footerapps.accepts_files(_FakeAppInfo("myterm"))
    assert footerapps.accepts_files(_FileTakingAppInfo("myterm %u"))
    assert footerapps.accepts_files(_FileEditorAppInfo())


def test_launch_app_file_hands_the_file_over(tmp_path):
    path = tmp_path / "f.txt"
    path.write_text("x\n")
    app = _FileEditorAppInfo()
    assert footerapps.launch_app_file(app, str(path))
    assert app.launched == [[str(path)]]


def test_launch_app_file_refuses_without_a_launch(tmp_path):
    path = tmp_path / "f.txt"
    path.write_text("x\n")
    # An app with no file placeholder: GLib would drop the argument.
    assert not footerapps.launch_app_file(_FakeAppInfo("myterm"), str(path))
    # A path that isn't a file — missing, or a directory.
    app = _FileEditorAppInfo()
    assert not footerapps.launch_app_file(app, str(tmp_path / "nosuch.txt"))
    assert not footerapps.launch_app_file(app, str(tmp_path))
    assert not footerapps.launch_app_file(app, "")
    assert app.launched == []


def test_launch_app_file_swallows_a_launch_failure(tmp_path, capsys):
    path = tmp_path / "f.txt"
    path.write_text("x\n")
    assert not footerapps.launch_app_file(_FileEditorAppInfo(fail=True), str(path))
    assert "footer app launch failed (fake.desktop)" in capsys.readouterr().err
