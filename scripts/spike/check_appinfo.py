"""Gio.AppInfo under gosxappinfo. Spec hardware-only item 4.

Only to know whether hiding Footer apps on darwin is a "later" or a
"never": can GLib enumerate installed apps and name a default handler for
directories and https on macOS at all? PASS if get_all() is non-empty; the
default-handler answers are reported as data.
"""

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GioUnix", "2.0")
from gi.repository import Gio, GioUnix  # noqa: E402


def _describe(info) -> str:
    if info is None:
        return "None"
    try:
        return (
            f"{type(info).__name__} id={info.get_id()!r} name={info.get_name()!r} "
            f"exe={info.get_executable()!r}"
        )
    except Exception as exc:  # noqa: BLE001
        return f"{type(info).__name__} (describe error {exc!r})"


def main() -> int:
    infos = Gio.AppInfo.get_all()
    impl = type(infos[0]).__name__ if infos else "?"
    print(f"AppInfo.get_all(): {len(infos)} entries; implementation={impl}")
    for info in infos[:15]:
        print("  ", _describe(info))
    if len(infos) > 15:
        print(f"   ... {len(infos) - 15} more")

    for ctype in ("inode/directory", "text/plain", "public.folder"):
        try:
            default = Gio.AppInfo.get_default_for_type(ctype, False)
            print(f"get_default_for_type({ctype!r}): {_describe(default)}")
        except Exception as exc:  # noqa: BLE001
            print(f"get_default_for_type({ctype!r}): error {exc!r}")
        try:
            alls = Gio.AppInfo.get_all_for_type(ctype)
            print(f"get_all_for_type({ctype!r}): {len(alls)}")
        except Exception as exc:  # noqa: BLE001
            print(f"get_all_for_type({ctype!r}): error {exc!r}")
    for scheme in ("https", "file"):
        try:
            handler = Gio.AppInfo.get_default_for_uri_scheme(scheme)
            print(f"get_default_for_uri_scheme({scheme!r}): {_describe(handler)}")
        except Exception as exc:  # noqa: BLE001
            print(f"get_default_for_uri_scheme({scheme!r}): error {exc!r}")

    has_desktop = hasattr(GioUnix, "DesktopAppInfo")
    print(f"GioUnix.DesktopAppInfo present: {has_desktop}")
    try:
        cmd = Gio.AppInfo.create_from_commandline("open -R %f", "Reveal", Gio.AppInfoCreateFlags.NONE)
        print(f"create_from_commandline: {_describe(cmd)}")
    except Exception as exc:  # noqa: BLE001
        print(f"create_from_commandline: error {exc!r}")

    default_dir = Gio.AppInfo.get_default_for_type("inode/directory", False)
    default_name = default_dir.get_name() if default_dir else None
    print(
        f"RESULT appinfo get_all={len(infos)} default_inode_directory={default_name!r} "
        f"desktop_app_info={has_desktop}"
    )
    ok = len(infos) > 0
    print(f"{'PASS' if ok else 'FAIL'} appinfo")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
