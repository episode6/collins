import pytest

gi = pytest.importorskip("gi")

from collins import openwith  # noqa: E402


class _FakeAppInfo:
    """Stand-in for the parts of a Gio.AppInfo the resolver reads."""

    def __init__(
        self,
        app_id: str,
        executable: str = "",
        name: str = "",
        commandline: str = "",
        keys: dict | None = None,
    ) -> None:
        self._id = app_id
        self._executable = executable
        self._name = name or app_id
        self._commandline = commandline or executable
        self._keys = keys or {}

    def get_id(self) -> str:
        return self._id

    def get_executable(self) -> str:
        return self._executable

    def get_display_name(self) -> str:
        return self._name

    def get_commandline(self) -> str:
        return self._commandline

    def get_string(self, key: str):
        return self._keys.get(key)


@pytest.fixture(autouse=True)
def _no_system_alternative(monkeypatch):
    """Keep the host's own x-terminal-emulator out of the resolver — the tests
    that care about it say so themselves."""
    monkeypatch.setattr(openwith, "_alternatives_terminal", lambda: None)


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


def test_default_terminal_honours_the_system_alternative(monkeypatch):
    """The terminal x-terminal-emulator points at beats our own ranking: the
    user said which one there, we would only be guessing."""
    ranked_first = _FakeAppInfo("org.gnome.Ptyxis.desktop", "ptyxis")
    chosen = _FakeAppInfo("com.raggesilver.BlackBox.desktop", "/usr/bin/flatpak")
    monkeypatch.delenv("TERMINAL", raising=False)
    monkeypatch.setattr(openwith, "_installed_terminals", lambda: [ranked_first, chosen])
    monkeypatch.setattr(openwith, "_configured_terminal_ids", lambda: [])
    monkeypatch.setattr(openwith, "_alternatives_terminal", lambda: "/usr/local/bin/blackbox")
    assert openwith.default_terminal() is chosen


def test_configured_terminal_id_beats_the_system_alternative(monkeypatch):
    listed = _FakeAppInfo("listed.desktop", "listed")
    other = _FakeAppInfo("xterm.desktop", "/usr/bin/xterm")
    monkeypatch.delenv("TERMINAL", raising=False)
    monkeypatch.setattr(openwith, "_installed_terminals", lambda: [listed, other])
    monkeypatch.setattr(openwith, "_configured_terminal_ids", lambda: ["listed.desktop"])
    monkeypatch.setattr(openwith, "_alternatives_terminal", lambda: "/usr/bin/xterm")
    assert openwith.default_terminal() is listed


def test_match_executable_finds_a_flatpak_by_the_app_it_runs(monkeypatch):
    """A Flatpak's executable is flatpak itself, so the wrapper the alternative
    points at (…/blackbox) has to be matched against the entry's ID."""
    blackbox = _FakeAppInfo("com.raggesilver.BlackBox.desktop", "/usr/bin/flatpak")
    assert openwith._match_executable([blackbox], "/usr/local/bin/blackbox") is blackbox


def test_alternatives_terminal_is_none_without_the_alternative(monkeypatch):
    monkeypatch.setattr(openwith.shutil, "which", lambda _cmd: None)
    assert openwith._alternatives_terminal() is None


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


def test_terminal_argv_puts_the_directory_on_the_command_line():
    """A running Flatpak/single-instance terminal ignores the cwd we spawn it
    with, so the directory has to be asked for out loud."""
    blackbox = _FakeAppInfo(
        "com.raggesilver.BlackBox.desktop",
        "/usr/bin/flatpak",
        commandline="/usr/bin/flatpak run --command=blackbox com.raggesilver.BlackBox",
    )
    assert openwith.terminal_argv(blackbox, "/work") == [
        "/usr/bin/flatpak",
        "run",
        "--command=blackbox",
        "com.raggesilver.BlackBox",
        "--working-directory",
        "/work",
    ]


def test_terminal_argv_asks_ptyxis_for_a_new_window():
    """Ptyxis reads --working-directory only alongside a window request."""
    ptyxis = _FakeAppInfo("org.gnome.Ptyxis.desktop", "ptyxis", commandline="ptyxis")
    assert openwith.terminal_argv(ptyxis, "/work") == [
        "ptyxis",
        "--new-window",
        "--working-directory",
        "/work",
    ]


def test_terminal_argv_drops_field_codes():
    kitty = _FakeAppInfo("kitty.desktop", "kitty", commandline="kitty %F")
    assert openwith.terminal_argv(kitty, "/work") == ["kitty", "--directory", "/work"]


def test_terminal_argv_uses_the_flag_the_entry_advertises():
    """X-TerminalArgDir (the XDG terminal-execution spec) answers for the
    terminals we've never heard of."""
    unknown = _FakeAppInfo(
        "com.example.Term.desktop",
        "exampleterm",
        commandline="exampleterm",
        keys={"X-TerminalArgDir": "--cwd"},
    )
    assert openwith.terminal_argv(unknown, "/work") == ["exampleterm", "--cwd", "/work"]


def test_terminal_argv_leaves_an_entry_that_already_says_where_to_start():
    """WezTerm's own entry ships "start --cwd ." — a relative directory that
    the spawn cwd answers, and that a second --cwd would only fight."""
    wezterm = _FakeAppInfo("wezterm.desktop", "wezterm", commandline="wezterm start --cwd .")
    assert openwith.terminal_argv(wezterm, "/work") is None


def test_terminal_argv_is_none_when_nothing_names_a_flag():
    unknown = _FakeAppInfo("aterm.desktop", "aterm", commandline="aterm")
    assert openwith.terminal_argv(unknown, "/work") is None


def test_launch_terminal_spawns_with_the_directory_both_ways(monkeypatch, tmp_path):
    """Flag *and* cwd: the flag is what a single-instance terminal reads, the
    cwd is what a plain one does."""
    spawned = {}
    monkeypatch.setattr(
        openwith.subprocess,
        "Popen",
        lambda argv, **kwargs: spawned.update(argv=argv, cwd=kwargs.get("cwd")),
    )
    ptyxis = _FakeAppInfo("org.gnome.Ptyxis.desktop", "ptyxis", commandline="ptyxis")
    openwith.launch_terminal(ptyxis, str(tmp_path))
    assert spawned["argv"] == ["ptyxis", "--new-window", "--working-directory", str(tmp_path)]
    assert spawned["cwd"] == str(tmp_path)


def test_launch_terminal_falls_back_to_spawning_in_place(monkeypatch, tmp_path):
    launched = {}
    monkeypatch.setattr(
        openwith,
        "launch_app",
        lambda info, cwd, **kwargs: launched.update(cwd=cwd, kwargs=kwargs),
    )
    unknown = _FakeAppInfo("aterm.desktop", "aterm", commandline="aterm")
    openwith.launch_terminal(unknown, str(tmp_path))
    assert launched == {"cwd": str(tmp_path), "kwargs": {"pass_directory": False}}


def test_launch_terminal_falls_back_to_home_for_a_missing_directory(monkeypatch, tmp_path):
    spawned = {}
    monkeypatch.setattr(
        openwith.subprocess,
        "Popen",
        lambda argv, **kwargs: spawned.update(argv=argv, cwd=kwargs.get("cwd")),
    )
    ptyxis = _FakeAppInfo("org.gnome.Ptyxis.desktop", "ptyxis", commandline="ptyxis")
    openwith.launch_terminal(ptyxis, str(tmp_path / "gone"))
    assert spawned["cwd"] == str(openwith.Path.home())
    assert spawned["argv"][-1] == str(openwith.Path.home())
