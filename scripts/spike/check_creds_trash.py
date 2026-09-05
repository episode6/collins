"""Peer pid over a unix socket, and where Gio.File.trash() lands.

Spec resolved questions 6 and 4, asserted once on real hardware.

Peer pid: mirrors the MCP shim's shape (mcpserver.py `_peer_pid`) -- this
process listens on a unix socket, a child process connects, the accepted
end is wrapped in a Gio.Socket and `get_credentials().get_unix_pid()` must
equal the child's pid. (A socketpair would be the wrong test: both ends are
created by the same process, so LOCAL_PEERPID names ourselves.)

Trash: a file under $HOME and one under $TMPDIR are trashed; report what
`~/.Trash` gained.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

CHILD = """
import os, socket, sys, time
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect(sys.argv[1])
s.sendall(str(os.getpid()).encode() + b"\\n")
time.sleep(3)
"""


def check_peer_pid() -> tuple[bool, str]:
    with tempfile.TemporaryDirectory(prefix="cs-") as tmp:
        path = os.path.join(tmp, "s.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(1)
        child = subprocess.Popen([sys.executable, "-c", CHILD, path])
        srv.settimeout(15)
        conn, _ = srv.accept()
        claimed = int(conn.recv(64).split(b"\n")[0])
        gsock = Gio.Socket.new_from_fd(conn.fileno())
        try:
            creds = gsock.get_credentials()
            pid = creds.get_unix_pid()
            uid = creds.get_unix_user()
            detail = (
                f"peer_pid={pid} child_pid={child.pid} child_claims={claimed} uid={uid} "
                f"me={os.getuid()} creds={creds.to_string()!r}"
            )
            ok = pid == child.pid == claimed
        except GLib.Error as exc:
            detail = f"get_credentials error: {exc.message!r}"
            ok = False
        finally:
            child.terminate()
            child.wait(timeout=10)
            conn.close()
            srv.close()
        return ok, detail


def _trash_listing() -> set[str]:
    trash = os.path.expanduser("~/.Trash")
    try:
        return set(os.listdir(trash))
    except OSError as exc:
        print(f"listing ~/.Trash failed: {exc!r}")
        return set()


def check_trash() -> tuple[bool, str]:
    stamp = f"collins-spike-{int(time.time())}"
    home_file = os.path.join(os.path.expanduser("~"), f"{stamp}-home.txt")
    tmp_file = os.path.join(tempfile.gettempdir(), f"{stamp}-tmp.txt")
    for p in (home_file, tmp_file):
        with open(p, "w") as fh:
            fh.write("spike\n")
    before = _trash_listing()
    results = []
    for p in (home_file, tmp_file):
        try:
            ok = Gio.File.new_for_path(p).trash(None)
            results.append(f"{os.path.basename(p)}: trash()={ok} still_exists={os.path.exists(p)}")
        except GLib.Error as exc:
            results.append(
                f"{os.path.basename(p)}: error domain={exc.domain} code={exc.code} {exc.message!r}"
            )
    time.sleep(1)
    gained = sorted(_trash_listing() - before)
    home_landed = any(f"{stamp}-home" in n for n in gained)
    tmp_landed = any(f"{stamp}-tmp" in n for n in gained)
    detail = (
        f"{'; '.join(results)}; ~/.Trash gained={gained} "
        f"home_landed={home_landed} tmp_landed={tmp_landed}"
    )
    for p in (home_file, tmp_file):  # tidy if trash() left them behind
        try:
            os.unlink(p)
        except OSError:
            pass
    return home_landed, detail


def main() -> int:
    pid_ok, pid_detail = check_peer_pid()
    print(f"peer pid: {pid_detail}")
    print(f"RESULT peer_pid ok={pid_ok} {pid_detail}")
    trash_ok, trash_detail = check_trash()
    print(f"trash: {trash_detail}")
    print(f"RESULT trash ok={trash_ok} {trash_detail}")
    print(f"{'PASS' if pid_ok else 'FAIL'} peer_pid")
    print(f"{'PASS' if trash_ok else 'FAIL'} trash")
    return 0 if (pid_ok and trash_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
