"""Session bus reachability and GApplication uniqueness on macOS.

Spec hardware-only item 2 and resolved question 5. Run twice by the
workflow: before and after `brew services start dbus`.

    python3 check_bus.py <expect: nobus|bus>

Observes:
  * Gio.bus_get_sync(SESSION) -- succeeds, or fails cleanly with which error.
  * A Gio.Application registered with a fresh id, then a second process
    registering the same id: is the second one remote (refused) or does it
    become a second primary (silently non-unique)?

PASS means the observation matches the spec's prediction for that mode
(nobus: bus fails, second instance is NOT remote; bus: bus works, second
instance IS remote). Either way the RESULT line is the data.
"""

import os
import subprocess
import sys
import uuid

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

CHILD = """
import sys, gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio
app = Gio.Application(application_id=sys.argv[1], flags=Gio.ApplicationFlags.FLAGS_NONE)
ok = app.register(None)
has_bus = app.get_dbus_connection() is not None
print("child registered=%s is_remote=%s dbus=%s" % (ok, app.get_is_remote(), has_bus))
"""


def probe_bus() -> tuple[bool, str]:
    try:
        conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        return True, f"unique_name={conn.get_unique_name()}"
    except GLib.Error as exc:
        return False, f"GLib.Error domain={exc.domain} code={exc.code} message={exc.message!r}"


def main() -> int:
    expect = sys.argv[1] if len(sys.argv) > 1 else "nobus"
    for var in ("DBUS_SESSION_BUS_ADDRESS", "DBUS_LAUNCHD_SESSION_BUS_SOCKET"):
        print(f"env {var}={os.environ.get(var)!r}")
    try:
        launchctl = subprocess.run(
            ["launchctl", "getenv", "DBUS_LAUNCHD_SESSION_BUS_SOCKET"], capture_output=True, text=True
        )
        print(
            f"launchctl getenv DBUS_LAUNCHD_SESSION_BUS_SOCKET -> rc={launchctl.returncode} "
            f"{launchctl.stdout.strip()!r}"
        )
    except FileNotFoundError:
        print("launchctl: not on this platform")

    bus_ok, bus_detail = probe_bus()
    print(f"bus_get_sync(SESSION): ok={bus_ok} {bus_detail}")

    app_id = "com.episode6.CollinsSpike.U" + uuid.uuid4().hex[:8]
    primary = Gio.Application(application_id=app_id, flags=Gio.ApplicationFlags.FLAGS_NONE)
    registered = primary.register(None)
    print(
        f"primary registered={registered} is_remote={primary.get_is_remote()} "
        f"dbus={primary.get_dbus_connection() is not None}"
    )
    # Hold the primary registration while a second process tries the same id.
    # The primary must be spinning its main loop: a remote register() reads
    # the primary's org.gtk.Application properties over the bus and times
    # out otherwise (seen on Linux while writing this).
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD, app_id], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    loop = GLib.MainLoop()

    def poll():
        if child.poll() is None:
            return True
        loop.quit()
        return False

    GLib.timeout_add(100, poll)
    GLib.timeout_add(60_000, loop.quit)
    loop.run()
    if child.poll() is None:
        child.kill()
    out, err = child.communicate(timeout=10)
    print(out.strip())
    if err.strip():
        print("child stderr:", err.strip())
    second_remote = "is_remote=True" in out

    if expect == "bus":
        ok = bus_ok and second_remote
    else:
        ok = (not bus_ok) and (not second_remote)
    print(
        f"RESULT bus mode={expect} bus_ok={bus_ok} second_instance_remote={second_remote} "
        f"detail={bus_detail!r}"
    )
    print(f"{'PASS' if ok else 'FAIL'} bus {expect}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
