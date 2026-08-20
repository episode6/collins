import stat
from pathlib import Path

import pytest

from collins import desktopentry

APP_ID = desktopentry.APP_ID

TEMPLATE = """# a comment the template carries
[Desktop Entry]
Type=Application
Name=Collins
Exec=python3 -m collins
Path=/opt/collins
Icon=com.episode6.Collins
"""


def test_data_home_follows_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    assert desktopentry.data_home() == tmp_path / "share"


def test_data_home_defaults_under_home(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert desktopentry.data_home() == tmp_path / ".local" / "share"


def test_desktop_entry_rewrites_exec_and_drops_path():
    entry = desktopentry.desktop_entry(TEMPLATE, "/home/u/.local/bin/collins")
    lines = entry.splitlines()
    assert "Exec=/home/u/.local/bin/collins" in lines
    assert not [line for line in lines if line.startswith("Path=")]
    # Everything else survives, comment included -- the template is the one in
    # data/, which the .deb and the AUR recipe edit the same two ways.
    assert lines[0] == "# a comment the template carries"
    assert "Icon=com.episode6.Collins" in lines
    assert entry.endswith("\n")


@pytest.mark.parametrize(
    "command, expected",
    [
        ("/usr/bin/collins", "/usr/bin/collins"),
        ("/home/a b/.local/bin/collins", '"/home/a b/.local/bin/collins"'),
        ('/tmp/we"ird/collins', '"/tmp/we\\"ird/collins"'),
        # Quoted *and* backslash-escaped: quoting alone does not neutralize
        # these three inside the double quotes.
        ("/tmp/a$b/collins", '"/tmp/a\\$b/collins"'),
        ("/tmp/a`b/collins", '"/tmp/a\\`b/collins"'),
        ("/tmp/a\\b/collins", '"/tmp/a\\\\b/collins"'),
        # Reserved, so quoted -- but not escaped inside the quotes.
        ("/tmp/a&b/collins", '"/tmp/a&b/collins"'),
        ("/tmp/a;b/collins", '"/tmp/a;b/collins"'),
        ("/tmp/~b/collins", '"/tmp/~b/collins"'),
        ("/tmp/a#b/collins", '"/tmp/a#b/collins"'),
        ("/tmp/a(b)/collins", '"/tmp/a(b)/collins"'),
    ],
)
def test_exec_quoting(command, expected):
    assert desktopentry._quote_exec(command) == expected


def test_every_reserved_character_triggers_quoting():
    # The spec's list, verbatim: an argument holding any of these must be
    # quoted, so none may slip through unquoted.
    for char in " \t\n\"'\\><~|&;$*?#()`":
        quoted = desktopentry._quote_exec(f"/tmp/a{char}b/collins")
        assert quoted.startswith('"') and quoted.endswith('"'), char


def test_exec_command_prefers_the_running_script(monkeypatch, tmp_path):
    script = tmp_path / "collins"
    script.write_text("#!/bin/sh\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(desktopentry.sys, "argv", [str(script), "--install-desktop"])
    assert desktopentry.exec_command() == str(script)


def test_exec_command_falls_back_to_the_path(monkeypatch, tmp_path):
    # `python3 -m collins --install-desktop`: argv[0] is __main__.py, so the
    # command has to be looked up instead.
    monkeypatch.setattr(desktopentry.sys, "argv", [str(tmp_path / "__main__.py")])
    monkeypatch.setattr(desktopentry.shutil, "which", lambda name: "/usr/bin/collins")
    assert desktopentry.exec_command() == "/usr/bin/collins"


def test_exec_command_last_resort_is_the_bare_name(monkeypatch, tmp_path):
    monkeypatch.setattr(desktopentry.sys, "argv", [str(tmp_path / "__main__.py")])
    monkeypatch.setattr(desktopentry.shutil, "which", lambda name: None)
    assert desktopentry.exec_command() == "collins"


def _isolate_xdg(monkeypatch, tmp_path):
    """Point both XDG data roots at empty scratch dirs.

    Without this the defaults leak in — /usr/share/applications holds a real
    entry on any machine with the .deb installed, so the test would pass or
    fail depending on the developer's box.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_DIRS", f"{tmp_path / 'sys1'}:{tmp_path / 'sys2'}")


def _write_entry(root):
    entry = root / "applications" / f"{APP_ID}.desktop"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("[Desktop Entry]\n")
    return entry


def test_entry_locations_cover_data_home_then_system(monkeypatch, tmp_path):
    _isolate_xdg(monkeypatch, tmp_path)
    assert desktopentry.entry_locations() == [
        tmp_path / "home" / "applications" / f"{APP_ID}.desktop",
        tmp_path / "sys1" / "applications" / f"{APP_ID}.desktop",
        tmp_path / "sys2" / "applications" / f"{APP_ID}.desktop",
    ]


def test_is_installed_sees_a_user_entry(monkeypatch, tmp_path):
    _isolate_xdg(monkeypatch, tmp_path)
    assert not desktopentry.is_installed()
    _write_entry(tmp_path / "home")
    assert desktopentry.is_installed()


def test_is_installed_sees_a_system_entry(monkeypatch, tmp_path):
    # What the .deb, the PPA and the AUR package install.
    _isolate_xdg(monkeypatch, tmp_path)
    _write_entry(tmp_path / "sys2")
    assert desktopentry.is_installed()


def test_can_offer_install_only_when_missing_and_launchable(monkeypatch, tmp_path):
    _isolate_xdg(monkeypatch, tmp_path)
    monkeypatch.setattr(desktopentry.sys, "argv", [str(tmp_path / "__main__.py")])
    monkeypatch.setattr(desktopentry.shutil, "which", lambda name: "/usr/bin/collins")
    assert desktopentry.can_offer_install()

    # An entry already on the machine: nothing to offer.
    entry = _write_entry(tmp_path / "home")
    assert not desktopentry.can_offer_install()
    entry.unlink()

    # No installed command to point Exec= at: `python3 -m collins` from a
    # checkout, where data/install.sh is the right tool.
    monkeypatch.setattr(desktopentry.shutil, "which", lambda name: None)
    assert not desktopentry.can_offer_install()


def test_install_writes_the_three_files(monkeypatch, tmp_path):
    monkeypatch.setattr(desktopentry, "_refresh", lambda *a: None)
    monkeypatch.setattr(desktopentry, "exec_command", lambda: "collins")

    written = desktopentry.install(tmp_path)

    entry = tmp_path / "applications" / f"{APP_ID}.desktop"
    icon = tmp_path / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"
    appdata = tmp_path / "metainfo" / f"{APP_ID}.metainfo.xml"
    assert written == [entry, icon, appdata]
    assert all(p.is_file() for p in written)

    text = entry.read_text()
    assert "Exec=collins" in text
    assert "Path=" not in text
    assert icon.read_text().lstrip().startswith("<")
    assert f"<id>{APP_ID}</id>" in appdata.read_text()


def test_install_names_what_the_package_is_missing(monkeypatch, tmp_path):
    # A package that lost its data files should say which ones, not fail later
    # with a bare copy error.
    monkeypatch.setattr(desktopentry, "_PACKAGE", tmp_path / "nowhere")
    with pytest.raises(FileNotFoundError, match="missing from the installed package"):
        desktopentry.install(tmp_path / "share")


def test_install_cli_reports_failure(monkeypatch, capsys):
    monkeypatch.setattr(desktopentry, "install", lambda: (_ for _ in ()).throw(PermissionError("nope")))
    assert desktopentry.install_cli() == 1
    assert "could not install the desktop entry" in capsys.readouterr().err


def test_package_carries_the_action_icons():
    # app.py's first icon-search root. Ships in the wheel through the
    # package-data globs; scripts/verify_wheel_data.py checks the built
    # artifacts, this checks the tree the globs are resolved against.
    actions = desktopentry._PACKAGE / "icons" / "hicolor" / "scalable" / "actions"
    assert (actions / "tab-close-symbolic.svg").is_file()
    assert (desktopentry._PACKAGE / "icons" / f"{APP_ID}.svg").is_file()
