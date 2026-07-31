import pytest

gi = pytest.importorskip("gi")

from collins import openwith  # noqa: E402


class _FakeAppInfo:
    """Stand-in for the parts of a Gio.AppInfo the resolver reads."""

    def __init__(self, app_id: str, executable: str = "", name: str = "") -> None:
        self._id = app_id
        self._executable = executable
        self._name = name or app_id

    def get_id(self) -> str:
        return self._id

    def get_executable(self) -> str:
        return self._executable

    def get_display_name(self) -> str:
        return self._name


def test_terminal_rank_prefers_the_listed_terminals():
    xterm = _FakeAppInfo("xterm.desktop", "xterm")
    ghostty = _FakeAppInfo("com.mitchellh.ghostty.desktop", "ghostty")
    unknown = _FakeAppInfo("aterm.desktop", "aterm")
    ranked = sorted([xterm, unknown, ghostty], key=openwith._terminal_rank)
    assert [info.get_id() for info in ranked] == [
        "com.mitchellh.ghostty.desktop",
        "xterm.desktop",
        "aterm.desktop",
    ]


def test_terminal_rank_matches_reverse_dns_ids():
    """org.gnome.Ptyxis is known even though its executable isn't read here."""
    ptyxis = _FakeAppInfo("org.gnome.Ptyxis.desktop")
    assert openwith._terminal_rank(ptyxis)[0] < len(openwith._TERMINAL_PREFERENCE)


def test_terminal_keys_cover_id_stem_and_executable():
    info = _FakeAppInfo("org.gnome.Console.desktop", "/usr/bin/kgx")
    assert openwith._terminal_keys(info) == {"org.gnome.console", "console", "kgx"}


def test_configured_terminal_ids_reads_the_xdg_list(tmp_path, monkeypatch):
    (tmp_path / "xdg-terminals.list").write_text(
        "# a comment\n\nfoo.desktop\nbar.desktop\n", encoding="utf-8"
    )
    monkeypatch.setattr(openwith.GLib, "get_user_config_dir", lambda: str(tmp_path))
    monkeypatch.setattr(openwith.GLib, "get_system_config_dirs", lambda: [])
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
    assert openwith._configured_terminal_ids() == ["foo.desktop", "bar.desktop"]


def test_configured_terminal_ids_puts_the_desktop_specific_list_first(tmp_path, monkeypatch):
    (tmp_path / "xdg-terminals.list").write_text("generic.desktop\n", encoding="utf-8")
    (tmp_path / "gnome-xdg-terminals.list").write_text("gnomeish.desktop\n", encoding="utf-8")
    monkeypatch.setattr(openwith.GLib, "get_user_config_dir", lambda: str(tmp_path))
    monkeypatch.setattr(openwith.GLib, "get_system_config_dirs", lambda: [])
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME:GNOME-Classic")
    assert openwith._configured_terminal_ids() == ["gnomeish.desktop", "generic.desktop"]


def test_configured_terminal_ids_is_empty_without_a_list(tmp_path, monkeypatch):
    monkeypatch.setattr(openwith.GLib, "get_user_config_dir", lambda: str(tmp_path))
    monkeypatch.setattr(openwith.GLib, "get_system_config_dirs", lambda: [])
    assert openwith._configured_terminal_ids() == []


def test_default_terminal_honours_the_configured_id(monkeypatch):
    listed = _FakeAppInfo("listed.desktop", "listed")
    monkeypatch.delenv("TERMINAL", raising=False)
    monkeypatch.setattr(openwith, "_installed_terminals", lambda: [listed])
    monkeypatch.setattr(openwith, "_configured_terminal_ids", lambda: ["listed.desktop"])
    assert openwith.default_terminal() is listed


def test_default_terminal_prefers_terminal_env_var(monkeypatch):
    installed = _FakeAppInfo("xterm.desktop", "/usr/bin/xterm")
    monkeypatch.setenv("TERMINAL", "xterm")
    monkeypatch.setattr(openwith, "_installed_terminals", lambda: [installed])
    monkeypatch.setattr(openwith, "_configured_terminal_ids", lambda: ["other.desktop"])
    assert openwith.default_terminal() is installed


def test_default_terminal_ignores_an_uninstalled_terminal_env_var(monkeypatch):
    installed = _FakeAppInfo("xterm.desktop", "/usr/bin/xterm")
    monkeypatch.setenv("TERMINAL", "definitely-not-installed-xyz")
    monkeypatch.setattr(openwith, "_installed_terminals", lambda: [installed])
    monkeypatch.setattr(openwith, "_configured_terminal_ids", lambda: [])
    assert openwith.default_terminal() is installed


def test_default_terminal_returns_none_when_nothing_is_installed(monkeypatch):
    monkeypatch.delenv("TERMINAL", raising=False)
    monkeypatch.setattr(openwith, "_installed_terminals", lambda: [])
    monkeypatch.setattr(openwith, "_configured_terminal_ids", lambda: [])
    monkeypatch.setattr(openwith.shutil, "which", lambda _cmd: None)
    assert openwith.default_terminal() is None


def test_default_terminal_falls_back_to_an_executable_on_path(monkeypatch):
    monkeypatch.delenv("TERMINAL", raising=False)
    monkeypatch.setattr(openwith, "_installed_terminals", lambda: [])
    monkeypatch.setattr(openwith, "_configured_terminal_ids", lambda: [])
    monkeypatch.setattr(
        openwith.shutil, "which", lambda cmd: "/usr/bin/xterm" if cmd == "xterm" else None
    )
    info = openwith.default_terminal()
    assert info is not None
    assert info.get_executable() == "/usr/bin/xterm"
