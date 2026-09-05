# macOS port spike (PR 0 of ~/specs/collins/macos-homebrew-port.md)

Throwaway. Nothing here is imported by the app or run by the Linux CI. The
scripts answer the spec's "still hardware-only" questions on a GitHub
`macos-15` (Apple Silicon) runner via `.github/workflows/macos-spike.yml`,
and they will be deleted or folded into the real macOS CI job in PR 4.

Every check prints one `RESULT ... ` line with the raw observation and one
`PASS`/`FAIL` line, so the workflow's step summary can grep them. A FAIL is
data, not a broken build: the spec has a designed-in branch for each answer.

| Script | Spec item | What it answers |
| --- | --- | --- |
| `gi_smoke.py` | 2 | every `gi.require_version` the app uses imports under brew's python |
| `check_vte_window.py` | 1 | a `Gtk.Window` + `Vte.Terminal` spawns `$SHELL`, takes input, renders (default renderer and `GSK_RENDERER=cairo`); saves a `screencapture` |
| `check_bus.py` | 2 (hardware) | `Gio.bus_get_sync(SESSION)` without a bus, with a hand-started `dbus-daemon` published via `launchctl setenv`, and via `DBUS_SESSION_BUS_ADDRESS`; whether a second `Gio.Application` with the same id is refused |
| `check_kqueue.py` | 3 | does `monitor_directory` fire on an append to an existing file? on a new file? does `monitor_file` see the append? |
| `check_appinfo.py` | 4 | `Gio.AppInfo.get_all()` and `get_default_for_type("inode/directory")` under `gosxappinfo` |
| `check_creds_trash.py` | resolved 4, 6 | `get_credentials().get_unix_pid()` across a unix socket equals the peer's pid; `Gio.File.trash()` lands in `~/.Trash` |
| `launch_collins.py` | 5 | Collins itself from the checkout for ~20s, stderr captured, `screencapture` taken; optionally with `proctree` stubbed via `stub/collins_spike_stub.py` (`SPIKE_STUB_PROCTREE=1`) |

The full unit suite is the workflow's own step (item f): it is GTK-free by
design and `tests/test_proctree.py` already skips itself without `/proc`.
