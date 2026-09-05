"""Gio.FileMonitor under the kqueue backend. Spec hardware-only item 3.

Three questions, each with its own RESULT line:
  1. monitor_directory on a temp dir, append to an existing file inside it:
     does any `changed` event fire (and which)?
  2. same monitor, create a new file: does one fire?
  3. monitor_file on the existing file, append: does one fire?

PASS requires 2 and 3 (what the app's monitors are for). Question 1 is
reported either way -- the spec has a branch for "no".
"""

import os
import tempfile

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

SETTLE_MS = 2500


def _backend() -> str:
    try:
        f = Gio.File.new_for_path(tempfile.gettempdir())
        mon = f.monitor_directory(Gio.FileMonitorFlags.NONE, None)
        return type(mon).__name__
    except Exception as exc:  # noqa: BLE001
        return f"error {exc!r}"


def _collect(monitor, action, label: str) -> list[str]:
    """Run *action* under a main loop for SETTLE_MS and return the events."""
    events: list[str] = []
    loop = GLib.MainLoop()

    def on_changed(_mon, file, other, event_type):
        name = file.get_basename() if file else None
        other_name = other.get_basename() if other else None
        events.append(f"{event_type.value_nick}:{name}" + (f"->{other_name}" if other_name else ""))

    hid = monitor.connect("changed", on_changed)
    # Let the monitor arm before acting: kqueue registration is synchronous
    # but the first idle can still matter for the poll thread.
    GLib.timeout_add(300, lambda: (action(), False)[1])
    GLib.timeout_add(SETTLE_MS, loop.quit)
    loop.run()
    monitor.disconnect(hid)
    print(f"{label}: events={events}")
    return events


def main() -> int:
    print(f"file monitor backend: {_backend()}")
    print(f"GIO_USE_FILE_MONITOR={os.environ.get('GIO_USE_FILE_MONITOR')!r}")
    with tempfile.TemporaryDirectory(prefix="collins-spike-kq-") as tmp:
        existing = os.path.join(tmp, "session.jsonl")
        with open(existing, "w") as fh:
            fh.write('{"line": 0}\n')

        dir_mon = Gio.File.new_for_path(tmp).monitor_directory(Gio.FileMonitorFlags.NONE, None)
        # Coalescing hides nothing here: the rate limit only merges bursts.
        dir_mon.set_rate_limit(100)
        print(f"directory monitor: {type(dir_mon).__name__}")

        def append():
            with open(existing, "a") as fh:
                fh.write('{"line": 1}\n')
                fh.flush()
                os.fsync(fh.fileno())

        def create():
            with open(os.path.join(tmp, "fresh.jsonl"), "w") as fh:
                fh.write("{}\n")

        q1 = _collect(dir_mon, append, "dir monitor, append to existing file")
        q2 = _collect(dir_mon, create, "dir monitor, create new file")
        dir_mon.cancel()

        file_mon = Gio.File.new_for_path(existing).monitor_file(Gio.FileMonitorFlags.NONE, None)
        file_mon.set_rate_limit(100)
        print(f"file monitor: {type(file_mon).__name__}")
        q3 = _collect(file_mon, append, "file monitor, append")
        file_mon.cancel()

    print(
        f"RESULT kqueue dir_append_fires={bool(q1)} dir_create_fires={bool(q2)} "
        f"file_append_fires={bool(q3)}"
    )
    print(f"RESULT kqueue events dir_append={q1} dir_create={q2} file_append={q3}")
    ok = bool(q2) and bool(q3)
    print(f"{'PASS' if ok else 'FAIL'} kqueue")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
