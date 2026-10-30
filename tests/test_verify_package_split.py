# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""scripts/verify_package_split.py: the collins / collins-service split
(split-service spec §3.24, PR-3.7) read off both listings' formats."""

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "verify_package_split.py"
_spec = importlib.util.spec_from_file_location("verify_package_split", _PATH)
split = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(split)

SITE = "usr/lib/python3/dist-packages/collins"
SERVICE = {
    "usr/bin/collins-service",
    "usr/bin/collins-keeper",
    "usr/lib/systemd/user/collins-service.service",
    "usr/lib/systemd/user/collins-keeper.service",
    f"{SITE}/service/main.py",
    f"{SITE}/service/keeper.py",
    f"{SITE}/mcp_shim.py",
    f"{SITE}/app.py",
    "usr/share/doc/collins-service/copyright",
}
APP = {
    "usr/bin/collins",
    "usr/share/applications/com.episode6.Collins.desktop",
    "usr/share/metainfo/com.episode6.Collins.metainfo.xml",
    "usr/share/icons/hicolor/scalable/apps/com.episode6.Collins.svg",
    "usr/share/collins/icons/hicolor/scalable/actions/tab-close-symbolic.svg",
    "usr/share/doc/collins/copyright",
}


def test_dpkg_listings_drop_directories_and_the_dot():
    text = (
        "drwxr-xr-x root/root         0 2026-10-29 21:41 ./usr/bin/\n"
        "-rwxr-xr-x root/root       101 2026-10-29 21:41 ./usr/bin/collins\n"
        "lrwxrwxrwx root/root         0 2026-10-29 21:41 ./usr/bin/c -> collins\n"
    )
    assert split.parse_listing(text) == {"usr/bin/collins", "usr/bin/c"}


def test_rpm_listings_drop_directories_and_the_slash():
    text = (
        "drwxr-xr-x /usr/lib/python3.14/site-packages/collins\n"
        "-rw-r--r-- /usr/lib/python3.14/site-packages/collins/app.py\n"
        "-rwxr-xr-x /usr/bin/collins-keeper\n"
    )
    assert split.parse_listing(text) == {
        "usr/lib/python3.14/site-packages/collins/app.py",
        "usr/bin/collins-keeper",
    }


def test_a_right_split_has_no_problems():
    assert split.problems(SERVICE, APP) == []


def test_the_site_directory_may_differ_per_channel():
    rpm_site = "usr/lib/python3.14/site-packages/collins"
    service = {p.replace(SITE, rpm_site) for p in SERVICE}
    assert split.problems(service, APP) == []


def test_a_file_in_both_is_a_problem():
    shared = "usr/share/doc/collins/README.md"
    assert split.problems(SERVICE | {shared}, APP | {shared}) == [f"in both packages: {shared}"]


def test_a_missing_keeper_unit_is_named():
    unit = "usr/lib/systemd/user/collins-keeper.service"
    assert split.problems(SERVICE - {unit}, APP) == [f"collins-service lacks {unit}"]


def test_the_window_holds_no_module_unit_or_service_command():
    app = APP | {f"{SITE}/window.py", "usr/lib/systemd/user/x.service", "usr/bin/collins-service"}
    assert split.problems(SERVICE - {"usr/bin/collins-service"}, app) == [
        "collins-service lacks usr/bin/collins-service",
        f"collins holds {SITE}/window.py",
        "collins holds usr/lib/systemd/user/x.service",
        "collins holds usr/bin/collins-service",
    ]


def test_the_service_holds_nothing_a_desktop_shell_reads():
    desktop = "usr/share/applications/com.episode6.Collins.desktop"
    assert split.problems(SERVICE | {desktop}, APP - {desktop}) == [
        f"collins lacks {desktop}",
        f"collins-service holds {desktop}",
    ]


def test_a_bad_command_line_prints_the_usage(capsys):
    assert split.main(["verify_package_split.py", "tar", "a", "b"]) == 2
    assert "deb <collins-service.deb>" in capsys.readouterr().err
