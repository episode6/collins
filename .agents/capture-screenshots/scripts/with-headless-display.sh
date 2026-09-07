#!/bin/bash
# Run a command against a private, headless GNOME Shell compositor, so an e2e
# capture never opens a window on the user's screen or steals their focus.
#
#   with-headless-display.sh python3 .../capture.py <repo-root> out.png
#
# GNOME Shell's --headless mode draws to a virtual monitor instead of a real
# one. It needs its own session bus (the user's live session already owns
# org.gnome.Shell) and its own Wayland display name, both of which this script
# provides and tears down again. Nothing here touches the user's session.
#
# HEADLESS_SIZE sets the virtual monitor size (default 1920x1200). Keep it
# comfortably larger than the window you are capturing: the compositor
# constrains a window to its monitor, so a monitor the same size as the window
# yields a shrunken shot.
#
# Falls back to the current display, with a warning, when a headless
# compositor isn't available — the capture still works, it just becomes
# visible again.
#
# The headless shell has its own display but not its own sound server: every
# compositor bell it rings (each Gdk.Display.beep the app under test makes,
# and check_notifications.py makes a dozen a run) plays the desktop's alert
# sound on the user's real speakers, and the app's own notification sound
# plays there too. So both the shell and the command are cut off from audio,
# by pointing every client library at a server that isn't there. HEADLESS_AUDIO=1
# keeps the sound, for a run whose point is to hear it.
set -u

if [ "${HEADLESS_AUDIO:-0}" != 1 ]; then
    export PULSE_SERVER=unix:/nonexistent/collins-headless-no-audio
    export PIPEWIRE_REMOTE=collins-headless-no-audio
    export CANBERRA_DRIVER=null
fi

if [ "$#" -eq 0 ]; then
    echo "usage: with-headless-display.sh <command> [args...]" >&2
    exit 2
fi

if ! command -v gnome-shell >/dev/null 2>&1 || ! command -v dbus-run-session >/dev/null 2>&1; then
    echo "with-headless-display: gnome-shell/dbus-run-session not found;" \
         "running on the current display (a window will appear)" >&2
    exec "$@"
fi

size=${HEADLESS_SIZE:-1920x1200}
runtime=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
wl_name="collins-e2e-$$-${RANDOM}"
log=$(mktemp -t collins-headless-XXXXXX.log)

# setsid gives the compositor its own process group, so cleanup can take down
# dbus-run-session and the shell it spawns without pattern-matching pids.
setsid dbus-run-session -- \
    gnome-shell --headless --virtual-monitor "$size" --wayland-display="$wl_name" \
    >"$log" 2>&1 &
shell_pgid=$!

cleanup() {
    kill -TERM -"$shell_pgid" 2>/dev/null || kill -TERM "$shell_pgid" 2>/dev/null
    for _ in $(seq 1 20); do
        kill -0 -"$shell_pgid" 2>/dev/null || break
        sleep 0.1
    done
    kill -KILL -"$shell_pgid" 2>/dev/null
    # The shell doesn't remove its own socket on the way out.
    rm -f "$log" "$runtime/$wl_name" "$runtime/$wl_name.lock"
}
trap cleanup EXIT INT TERM

for _ in $(seq 1 100); do   # ~20s; a cold shell start takes a few seconds
    [ -S "$runtime/$wl_name" ] && break
    sleep 0.2
done
if [ ! -S "$runtime/$wl_name" ]; then
    echo "with-headless-display: compositor never came up, see below;" \
         "running on the current display instead" >&2
    tail -5 "$log" >&2
    exec "$@"
fi

WAYLAND_DISPLAY="$wl_name" GDK_BACKEND=wayland DISPLAY= "$@"
