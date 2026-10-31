#!/usr/bin/env bash
# New in the ghackett fork of agent-session-manager (GPL-3.0).
#
# Install collins-service from this checkout, whatever is checked out (any
# branch, uncommitted edits included), for the current user: a box you want
# to reach with Connect to... before a package has what you need.
#
#   ./scripts/install-service.sh
#
# Every running collins-service of this user is stopped first, whatever its
# app id, debug and test instances included. A service with a keeper leaves
# its sessions running for the next one; one without ends them, resumable.
# The keepers are left alone, since stopping one ends its sessions for good.
#
# It goes in with pipx, as the PyPI channel does, and replaces any pipx
# install of collins. Both systemd user units are written under
# $XDG_DATA_HOME/systemd/user with ExecStart pointing at the install; nothing
# is enabled or started (the next window that connects starts the service).
# Run it again to install what is checked out then.
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"

# A process's whole command line must be the service itself: its console
# script (run directly, or through the interpreter its shebang names) or the
# module. Anchored, so a shell, an editor, a `journalctl -u collins-service`
# or an agent whose arguments only mention the name is never signalled.
# tests/test_install_service_script.py pins both patterns.
SERVICE_RE='^([^ ]*/)?(python[0-9.]*( -[^m ][^ ]*)* (-m collins\.service\.main|([^ ]*/)?collins-service)|collins-service)( |$)'
KEEPER_RE='^([^ ]*/)?(python[0-9.]*( -[^m ][^ ]*)* (-m collins\.service\.keeper|([^ ]*/)?collins-keeper)|collins-keeper)( |$)'
# Past the service's own stop sequence (core.STOP_BOUND_S, 30 s), as in
# start-debug: a service with no keeper ends its sessions itself.
STOP_WAIT_S=35

say() { echo "install-service: $*"; }
die() {
  echo "install-service: $*" >&2
  exit 1
}

matching() { pgrep -u "$(id -u)" -f -- "$1" || true; }

# systemd first: a unit that was killed would come back (Restart=on-failure),
# while `stop` signals the service alone and leaves it down (KillMode=process).
stop_services() {
  local pids
  if command -v systemctl >/dev/null \
      && systemctl --user is-active --quiet collins-service.service 2>/dev/null; then
    say "stopping collins-service.service"
    systemctl --user stop collins-service.service || true
  fi
  pids=$(matching "$SERVICE_RE")
  [ -z "$pids" ] && return 0
  say "stopping collins-service: $(echo "$pids" | tr '\n' ' ')"
  # shellcheck disable=SC2086  # one word per pid
  kill -TERM $pids 2>/dev/null || true
  for _ in $(seq $((STOP_WAIT_S * 5))); do
    [ -z "$(matching "$SERVICE_RE")" ] && return 0
    sleep 0.2
  done
  pids=$(matching "$SERVICE_RE")
  [ -z "$pids" ] && return 0
  say "still up after $STOP_WAIT_S s, killing: $(echo "$pids" | tr '\n' ' ')"
  # shellcheck disable=SC2086  # one word per pid
  kill -KILL $pids 2>/dev/null || true
}

# == what the install needs, checked before anything is stopped ================

[ -f "$ROOT/pyproject.toml" ] && [ -d "$ROOT/collins/service" ] \
  || die "$ROOT is not a Collins checkout"
command -v pipx >/dev/null \
  || die "pipx is not installed (sudo apt install pipx, or sudo dnf install pipx)"
command -v pgrep >/dev/null \
  || die "pgrep is not installed (the procps package)"
python3 -c '
import gi
gi.require_version("Soup", "3.0")
from gi.repository import Gio, GLib, GObject, Soup
' 2>/dev/null || die "the service needs PyGObject, GLib and libsoup 3: \
sudo apt install python3-gi gir1.2-glib-2.0 gir1.2-soup-3.0, \
or sudo dnf install python3-gobject-base libsoup3"

# == stop, install ==============================================================

stop_services

# pip builds in the source tree, and setuptools would carry whatever an
# earlier build left in build/ into the wheel: a deleted module, for one.
# Both are build output, ignored by git.
rm -rf "$ROOT/build" "$ROOT/collins.egg-info"

say "installing $ROOT"
pipx install --force --system-site-packages "$ROOT"

# A connected window retries every few seconds, so one may have started the
# old install's service while pipx ran. It starts this one next.
stop_services

VENV="$(pipx environment --value PIPX_LOCAL_VENVS)/collins"
[ -x "$VENV/bin/collins-service" ] || die "pipx left no $VENV/bin/collins-service"

# == the units ==================================================================

# `collins --install-desktop` writes these too, but it needs GTK, which a
# headless box does not have, and it adds a launcher nothing there would show.
if command -v systemctl >/dev/null; then
  "$VENV/bin/python" -I -c '
import sys
from pathlib import Path

from collins import desktopentry as d

bin_dir = Path(sys.argv[1])
units = d.systemd_user_dir()
units.mkdir(parents=True, exist_ok=True)
for unit, command in ((d.UNIT, "collins-service"), (d.KEEPER_UNIT, "collins-keeper")):
    template = (d._PACKAGE / unit).read_text()
    (units / unit).write_text(d.service_unit(template, d._quote_exec(str(bin_dir / command))))
    print(f"install-service: wrote {units / unit}")
' "$VENV/bin"
  systemctl --user daemon-reload 2>/dev/null || true
fi

# == what a window on another machine will find =================================

# The same two rounds as `sshforward.remote_command`: the login profile, then
# the interactive rc. Bounded, since an rc may hand the shell on.
login_shell="${SHELL:-/bin/sh}"
found=$(timeout 5 "$login_shell" -lc 'command -v collins-service' </dev/null 2>/dev/null || true)
[ -n "$found" ] \
  || found=$(timeout 5 "$login_shell" -lic 'command -v collins-service' </dev/null 2>/dev/null || true)
found=${found##*$'\n'}
if [ -z "$found" ]; then
  say "warning: collins-service is not on the login shell's PATH; run: pipx ensurepath"
elif [ "$(readlink -f "$found")" != "$(readlink -f "$VENV/bin/collins-service")" ]; then
  say "warning: the login shell's PATH finds $found first, not this install;"
  say "put $(pipx environment --value PIPX_BIN_DIR) ahead of it, or remove that install"
fi

if [ -n "$(matching "$KEEPER_RE")" ]; then
  say "the keeper is still running, so its sessions are too. It runs the old code until"
  say "it is stopped, which ends them: systemctl --user stop collins-keeper.service"
fi

# Exit 1 here is a missing `claude` or runtime directory: worth reading, and
# nothing the install could have done.
"$VENV/bin/collins-service" --check \
  || say "--check reports something missing above; the install itself is done"
say "installed. The next window that connects starts the new service."
