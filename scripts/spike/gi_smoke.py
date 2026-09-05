"""Import every gi namespace/version Collins asks for. Spec item 2.

Prints one line per namespace and a final PASS/FAIL. Exit status 1 on any
failure so the workflow step goes red, but the loop never stops early: the
whole list is the data.
"""

import sys
import traceback

import gi

# Keep in sync with `grep -rn require_version collins/`.
REQUIRED = [
    ("GLib", "2.0"),
    ("GObject", "2.0"),
    ("Gio", "2.0"),
    ("GioUnix", "2.0"),
    ("GdkPixbuf", "2.0"),
    ("Pango", "1.0"),
    ("PangoCairo", "1.0"),
    ("Gdk", "4.0"),
    ("Gtk", "4.0"),
    ("Adw", "1"),
    ("Vte", "3.91"),
    ("GtkSource", "5"),
    ("Spelling", "1"),
    ("Gst", "1.0"),
]


def main() -> int:
    print(f"python {sys.version.split()[0]} at {sys.executable}")
    gi_path = list(getattr(gi, "__path__", []))
    print(f"gi {getattr(gi, 'version_info', None)} from {gi.__file__} path={gi_path}")
    failed = []
    for ns, ver in REQUIRED:
        try:
            gi.require_version(ns, ver)
            mod = __import__(f"gi.repository.{ns}", fromlist=[ns])
            detail = ""
            if ns == "Gtk":
                detail = f"{mod.get_major_version()}.{mod.get_minor_version()}.{mod.get_micro_version()}"
            elif ns == "Adw":
                detail = f"{mod.get_major_version()}.{mod.get_minor_version()}.{mod.get_micro_version()}"
            elif ns == "Vte":
                detail = f"{mod.get_major_version()}.{mod.get_minor_version()}.{mod.get_micro_version()}"
            elif ns == "GLib":
                detail = f"{mod.MAJOR_VERSION}.{mod.MINOR_VERSION}.{mod.MICRO_VERSION}"
            elif ns == "GioUnix":
                detail = "DesktopAppInfo=" + ("present" if hasattr(mod, "DesktopAppInfo") else "absent")
            print(f"ok   {ns} {ver} {detail}".rstrip())
        except Exception:  # noqa: BLE001 - report everything
            failed.append(f"{ns} {ver}")
            print(f"FAIL {ns} {ver}")
            traceback.print_exc()
    print(f"RESULT gi_smoke failed={failed or 'none'}")
    print("PASS gi_smoke" if not failed else "FAIL gi_smoke")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
