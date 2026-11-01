#!/usr/bin/env python3
"""End-to-end check for the connect dialog's form: the Advanced row's
discovery command, what Test hands ssh and what Add saves.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \
        python3 scripts/check_connect_dialog.py

`ssh` is a stub first on the PATH that logs its last argument and prints a
socket's path, so nothing leaves this machine. Connecting for real over a
forward takes a box the CI lacks: that half is by hand, against the ssh
target (`collins-testing`).
"""

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

E2E = tempfile.mkdtemp(prefix="collins-connectdlg-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())

# Isolation first: every one of these is read at import time somewhere below.
os.environ["COLLINS_DEBUG_API"] = "1"  # the e2e probe (debug.*): served only with this set
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"
os.environ["COLLINS_UPLOADS_DIR"] = f"{E2E}/uploads"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"
SSH_LOG = f"{E2E}/ssh.log"
UI_STATE = f"{E2E}/config/collins/ui-state.json"

for path in (f"{E2E}/projects", f"{E2E}/chats", f"{E2E}/bin", f"{E2E}/config/collins"):
    os.makedirs(path, exist_ok=True)
with open(f"{E2E}/claude.json", "w", encoding="utf-8") as fh:
    fh.write("{}")
with open(f"{E2E}/config/collins/state.json", "w", encoding="utf-8") as fh:
    fh.write('{"settings": {"welcome_seen": true, "gh_welcome_dismissed": true, "title_model": "none"}}')
with open(f"{E2E}/bin/ssh", "w", encoding="utf-8") as fh:
    fh.write(
        "#!/bin/sh\n"
        'for last; do :; done\n'
        f'printf "%s\\n" "$last" >> "{SSH_LOG}"\n'
        "echo /run/box/api.sock\n"
    )
os.chmod(f"{E2E}/bin/ssh", 0o755)
with open(f"{E2E}/bin/claude", "w", encoding="utf-8") as fh:
    fh.write("#!/bin/sh\nexit 0\n")
os.chmod(f"{E2E}/bin/claude", 0o755)
os.environ["PATH"] = f"{E2E}/bin:{os.environ['PATH']}"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
import e2e_service  # noqa: E402
from gi.repository import GLib  # noqa: E402

from collins import sshremote  # noqa: E402
from collins.app import App  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {name}{f': {detail}' if not ok else ''}")
    if not ok:
        failures.append(name)


def ssh_commands() -> list[str]:
    try:
        with open(SSH_LOG, encoding="utf-8") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def saved_rows() -> list[dict]:
    with open(UI_STATE, encoding="utf-8") as fh:
        return json.load(fh).get("connections", [])


def pump_until(predicate, timeout_s: float = 10.0) -> bool:
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        ctx.iteration(False)
        time.sleep(0.01)
    return predicate()


def run_test(dialog) -> str:
    """Test, and the words it leaves when its thread has landed."""
    dialog.test_btn.emit("clicked")
    pump_until(lambda: not dialog._testing)
    return dialog.status.get_text()


def drive(app) -> None:
    win = app.get_active_window()
    win.activate_action("win.connect-to")
    pump_until(lambda: win.get_visible_dialog() is not None)
    dialog = win.get_visible_dialog()

    check("the default is the placeholder", dialog.command_entry.get_placeholder_text()
          == sshremote.REMOTE_COMMAND, dialog.command_entry.get_placeholder_text())
    check("the entry is bounded", dialog.command_entry.get_max_length() == sshremote.COMMAND_MAX)
    check("Advanced starts folded", not dialog.advanced_row.get_expanded())
    dialog.advanced_row.set_expanded(True)
    check("Advanced holds the row", dialog.command_row.get_ancestor(type(dialog.advanced_row))
          is dialog.advanced_row)

    # Empty: the default command, byte for byte.
    dialog.name_row.set_text("box")
    dialog.target_row.set_text("me@box.example.net:2222")
    words = run_test(dialog)
    check("Test with the default answers", "/run/box/api.sock" in words, words)
    check("Test ran the default command", ssh_commands()[-1:]
          == [sshremote.remote_command(sshremote.REMOTE_COMMAND)], ssh_commands())

    # A server's own command takes its place, as typed.
    dialog.command_entry.set_text("  clinic-server --socket 'a b'  ")
    words = run_test(dialog)
    check("Test with a command answers", "/run/box/api.sock" in words, words)
    check("Test ran that command", ssh_commands()[-1:]
          == [sshremote.remote_command("clinic-server --socket 'a b'")], ssh_commands())

    # What does not fit is said, and neither runs nor saves.
    ran = len(ssh_commands())
    dialog.command_entry.set_text("two\nlines")
    words = run_test(dialog)
    check("Test refuses two lines", "one line" in words and len(ssh_commands()) == ran, words)
    dialog.add_btn.emit("clicked")
    check("Add refuses two lines", app.connections.all() == [] and "one line" in dialog.status.get_text())
    dialog.command_entry.set_text("clinic‮server")
    words = run_test(dialog)
    check("Test refuses a format character", "one line" in words and len(ssh_commands()) == ran, words)

    # Add saves it, and clears the form.
    dialog.command_entry.set_text("clinic-server --socket")
    dialog.add_btn.emit("clicked")
    rows = app.connections.all()
    check("Add keeps the command", len(rows) == 1 and rows[0].command == "clinic-server --socket", rows)
    check("the file holds it", saved_rows()[0].get("command") == "clinic-server --socket", saved_rows())
    check("the form is clear",
          dialog.command_entry.get_text() == "" and not dialog.advanced_row.get_expanded())

    # With none typed the row has no command at all.
    dialog.name_row.set_text("plain")
    dialog.target_row.set_text("me@plain.example.net")
    dialog.add_btn.emit("clicked")
    plain = [row for row in saved_rows() if row["name"] == "plain"]
    check("a default row stores no command", len(plain) == 1 and "command" not in plain[0], plain)
    dialog.close()


def finish(code: int) -> None:
    sys.stdout.flush()
    e2e_service._stop_all()
    os._exit(code)


def stage(app) -> bool:
    if app.get_active_window() is None or not hasattr(app, "connections"):
        return GLib.SOURCE_CONTINUE
    try:
        drive(app)
    except Exception as exc:  # an exception in a GLib callback is swallowed
        import traceback

        traceback.print_exc()
        failures.append(repr(exc))
    print("FAILED: " + ", ".join(failures) if failures else "ALL PASS")
    finish(1 if failures else 0)
    return GLib.SOURCE_REMOVE


def deadline() -> bool:
    print("FAIL deadline")
    finish(3)
    return GLib.SOURCE_REMOVE


if __name__ == "__main__":
    e2e_service.start_service()
    application = App()
    GLib.timeout_add_seconds(90, deadline)
    GLib.timeout_add(250, stage, application)
    application.run([])
