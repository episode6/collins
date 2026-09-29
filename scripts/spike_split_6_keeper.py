"""Split-service spike 6: the keeper (spec PR-0.1 item 6, design in 3.10).

Not product code. The smallest keeper that can be measured: a process that
forks every pty child, stays their parent, holds a duplicate of every master
and hands the master to a "service" over a Unix socket with SCM_RIGHTS. The
service is then killed and replaced while the child keeps writing.

    python3 scripts/spike_split_6_keeper.py [--scratch DIR] [--no-systemd]

Cases, each printed as a finding:

  yes      `yes` behind the pty, the service killed with SIGKILL: is the
           writer blocked in write(), how many bytes did the kernel take
           before it blocked, does it resume under a new service.
  counter  a shell printing 1, 2, 3, ...: the ring two services wrote, one
           after the other, is checked for gaps and duplicates. Once with
           the kill falling wherever it falls, once with the service dying
           between its read() and its write to the ring (the worst case).
  winch    resize through the keeper with no service alive, resize from the
           service on the fd it was handed, tcgetpgrp and the file status
           flags on that fd, the child's session and controlling terminal
           across the service's death.
  orphan   the keeper killed while the service lives.
  systemd  the keeper as a transient user unit with KillMode=process and,
           for contrast, KillMode=control-group: what `kill` and `stop` do
           to a pty child.

Roles (`keeper`, `service`) are this same file run with a first argument;
sockets are named relative to the scratch directory, which every role
chdirs into, because sun_path holds 108 bytes and scratch paths are long.

Every child is registered with a guard process that outlives this script and
kills what is left when the deadline passes, whatever happened to the rest.
"""

import argparse
import contextlib
import fcntl
import json
import os
import re
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import time

SELF = os.path.abspath(__file__)
KEEPER_SOCK = "keeper.sock"
LIFE_S = 150  # nothing this script starts lives longer
COUNTER = "i=0; while :; do i=$((i+1)); echo $i; done"
TICKER = (
    "import signal, os, sys, time\n"
    "def size(*a):\n"
    "    s = os.get_terminal_size(0)\n"
    "    print('WINCH' if a else 'START', s.lines, s.columns, flush=True)\n"
    "signal.signal(signal.SIGWINCH, size)\n"
    "size()\n"
    "n = 0\n"
    "while True:\n"
    "    n += 1\n"
    "    print('tick', n, flush=True)\n"
    "    time.sleep(0.02)\n"
)


# --------------------------------------------------------------- plumbing


def winsize(rows: int, cols: int) -> bytes:
    return struct.pack("HHHH", rows, cols, 0, 0)


def get_winsize(fd: int) -> tuple[int, int]:
    rows, cols, _, _ = struct.unpack("HHHH", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8))
    return rows, cols


def ask(sock: socket.socket, message: dict) -> tuple[dict, list[int]]:
    """One request, one reply, with whatever fds rode on the reply."""
    sock.sendall(json.dumps(message).encode() + b"\n")
    data, fds, _flags, _addr = socket.recv_fds(sock, 65536, 16)
    while data and not data.endswith(b"\n"):
        data += sock.recv(65536)
    if not data:
        raise ConnectionError("the keeper closed the connection")
    return json.loads(data), list(fds)


def dial() -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect(KEEPER_SOCK)
    return sock


def write_json(path: str, value: dict) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(value, f)
    os.replace(tmp, path)


def read_json(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def proc_stat(pid: int) -> dict | None:
    """State, parent, process group, session, controlling terminal and its
    foreground group, off /proc/<pid>/stat."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            text = f.read()
    except OSError:
        return None
    fields = text[text.rindex(")") + 2 :].split()
    return {
        "state": fields[0],
        "ppid": int(fields[1]),
        "pgrp": int(fields[2]),
        "session": int(fields[3]),
        "tty_nr": int(fields[4]),
        "tpgid": int(fields[5]),
        "starttime": int(fields[19]),
    }


def proc_text(pid: int, name: str) -> str:
    try:
        with open(f"/proc/{pid}/{name}") as f:
            return f.read().strip()
    except OSError as err:
        return f"unreadable ({err.strerror})"


def wchar(pid: int) -> int | None:
    match = re.search(r"^wchar: (\d+)$", proc_text(pid, "io"), re.M)
    return int(match.group(1)) if match else None


def comm(pid: int) -> str:
    return proc_text(pid, "comm")


def alive(pid: int) -> bool:
    stat = proc_stat(pid)
    return stat is not None and stat["state"] != "Z"


# ------------------------------------------------------------------ guard


class Guard:
    """A detached process that kills what this script registered, at the
    deadline or as soon as the script says it is done."""

    def __init__(self, scratch: str):
        self.pids = os.path.join(scratch, "guard.pids")
        self.done = os.path.join(scratch, "guard.done")
        open(self.pids, "w").close()
        pid = os.fork()
        if pid:
            os.waitpid(pid, 0)
            return
        os.setsid()
        if os.fork():
            os._exit(0)
        deadline = time.monotonic() + LIFE_S
        while time.monotonic() < deadline and not os.path.exists(self.done):
            time.sleep(0.25)
        self.sweep()
        os._exit(0)

    def register(self, pid: int) -> None:
        stat = proc_stat(pid)
        if stat is not None:
            with open(self.pids, "a") as f:
                f.write(f"{pid} {stat['starttime']}\n")

    def sweep(self) -> list[int]:
        killed = []
        with open(self.pids) as f:
            entries = [line.split() for line in f if line.strip()]
        for pid, started in entries:
            stat = proc_stat(int(pid))
            if stat is None or stat["starttime"] != int(started):
                continue
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(int(pid), signal.SIGCONT)
                os.kill(int(pid), signal.SIGKILL)
                killed.append(int(pid))
        return killed

    def finish(self) -> list[int]:
        killed = self.sweep()
        open(self.done, "w").close()
        return killed


# ----------------------------------------------------------------- keeper


def keeper_spawn(argv: list[str], rows: int, cols: int, close: list[int]) -> tuple[int, int]:
    master, slave = os.openpty()
    fcntl.ioctl(master, termios.TIOCSWINSZ, winsize(rows, cols))
    pid = os.fork()
    if pid == 0:
        try:
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
            for fd in (0, 1, 2):
                os.dup2(slave, fd)
            for fd in [master, slave, *close]:
                if fd > 2:
                    with contextlib.suppress(OSError):
                        os.close(fd)
            for sig in (signal.SIGTERM, signal.SIGPIPE, signal.SIGHUP):
                signal.signal(sig, signal.SIG_DFL)
            os.execvp(argv[0], argv)
        finally:
            os._exit(127)
    os.close(slave)
    return pid, master


def keeper_main(args) -> int:
    """Five messages: spawn, list, resize, signal, reap. It reads and writes
    no pty."""
    os.chdir(args.dir)
    ptys: dict[int, dict] = {}
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(KEEPER_SOCK)
    old = os.umask(0o177)
    listener.bind(KEEPER_SOCK)
    os.umask(old)
    listener.listen(8)
    clients: list[socket.socket] = []
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(True))

    def fds_open() -> list[int]:
        return [listener.fileno(), *(c.fileno() for c in clients), *(p["fd"] for p in ptys.values())]

    def handle(message: dict) -> tuple[dict, list[int]]:
        kind = message.get("t")
        if kind == "spawn":
            pid, master = keeper_spawn(message["argv"], message["rows"], message["cols"], fds_open())
            pty_id = len(ptys) + 1
            ptys[pty_id] = {"pid": pid, "fd": master, "status": None}
            return {"ok": True, "id": pty_id, "pid": pid, "keeper": os.getpid()}, [master]
        if kind == "list":
            listed = [
                {
                    "id": pty_id,
                    "pid": p["pid"],
                    "status": p["status"],
                    "flags": fcntl.fcntl(p["fd"], fcntl.F_GETFL),
                    "size": get_winsize(p["fd"]),
                }
                for pty_id, p in sorted(ptys.items())
            ]
            fds = [ptys[entry["id"]]["fd"] for entry in listed]
            return {"ok": True, "ptys": listed, "keeper": os.getpid()}, fds
        entry = ptys.get(message.get("id"))
        if entry is None:
            return {"ok": False, "error": "unknown"}, []
        if kind == "resize":
            fcntl.ioctl(entry["fd"], termios.TIOCSWINSZ, winsize(message["rows"], message["cols"]))
            return {"ok": True}, []
        if kind == "signal":
            try:
                os.kill(entry["pid"], message["signal"])
            except ProcessLookupError:
                return {"ok": False, "error": "gone"}, []
            return {"ok": True}, []
        if kind == "reap":
            if entry["status"] is None:
                end = time.monotonic() + message.get("wait", 2.0)
                while time.monotonic() < end:
                    pid, status = os.waitpid(entry["pid"], os.WNOHANG)
                    if pid:
                        entry["status"] = status
                        break
                    time.sleep(0.02)
            return {"ok": entry["status"] is not None, "status": entry["status"]}, []
        return {"ok": False, "error": "unknown"}, []

    deadline = time.monotonic() + args.life
    while not stop and time.monotonic() < deadline:
        try:
            ready, _, _ = select.select([listener, *clients], [], [], 0.2)
        except InterruptedError:
            continue
        for sock in ready:
            if sock is listener:
                client, _ = listener.accept()
                clients.append(client)
                continue
            try:
                data = sock.recv(65536)
            except OSError:
                data = b""
            if not data:
                clients.remove(sock)
                sock.close()
                continue
            for line in data.splitlines():
                reply, fds = handle(json.loads(line))
                payload = json.dumps(reply).encode() + b"\n"
                try:
                    if fds:
                        socket.send_fds(sock, [payload], fds)
                    else:
                        sock.sendall(payload)
                except OSError:
                    break
    for entry in ptys.values():
        if entry["status"] is None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(entry["pid"], signal.SIGCONT)
                os.kill(entry["pid"], signal.SIGKILL)
            with contextlib.suppress(ChildProcessError):
                os.waitpid(entry["pid"], 0)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(KEEPER_SOCK)
    return 0


# ---------------------------------------------------------------- service


def service_main(args) -> int:
    """Gets a master from the keeper, reads it, appends what it read to the
    ring. `--die-after-read N` is the worst case: it dies holding the Nth
    chunk, read from the kernel and written nowhere."""
    os.chdir(args.dir)
    sock = dial()
    if args.spawn:
        reply, fds = ask(sock, {"t": "spawn", "argv": json.loads(args.spawn), "rows": 40, "cols": 120})
        pid, fd = reply["pid"], fds[0]
    else:
        reply, fds = ask(sock, {"t": "list"})
        index = [entry["id"] for entry in reply["ptys"]].index(args.adopt)
        pid, fd = reply["ptys"][index]["pid"], fds[index]
        for other in fds:
            if other != fd:
                os.close(other)
    status = {"service": os.getpid(), "pid": pid, "keeper": reply["keeper"], "fd": fd}
    status["flags_received"] = fcntl.fcntl(fd, fcntl.F_GETFL)
    try:
        status["tcgetpgrp"] = os.tcgetpgrp(fd)
    except OSError as err:
        status["tcgetpgrp"] = f"failed: {err}"
    status["size_received"] = get_winsize(fd)
    if args.nonblock:
        os.set_blocking(fd, False)
        status["flags_set"] = fcntl.fcntl(fd, fcntl.F_GETFL)
    try:
        os.waitpid(pid, os.WNOHANG)
        status["waitpid"] = "allowed"
    except ChildProcessError as err:
        status["waitpid"] = f"refused: {err}"
    ring = os.open(args.ring, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600) if args.ring else None
    status.update(reads=0, bytes=0, max_read=0, end=None)
    write_json(args.status, status)
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(True))
    started = time.monotonic()
    deadline = started + args.life
    last = 0.0
    while not stop and time.monotonic() < deadline:
        try:
            ready, _, _ = select.select([fd], [], [], 0.1)
        except InterruptedError:
            continue
        if args.rows and "size_set" not in status and time.monotonic() - started > 0.5:
            # Late enough that the child has its terminal and its handler.
            try:
                status["tcgetpgrp_later"] = os.tcgetpgrp(fd)
                fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize(args.rows, args.cols))
                status["size_set"] = get_winsize(fd)
            except OSError as err:
                status["size_set"] = f"failed: {err}"
        if ready:
            try:
                data = os.read(fd, 65536)
            except BlockingIOError:
                continue
            except OSError as err:
                status["end"] = f"read failed: errno {err.errno} ({err.strerror})"
                break
            if not data:
                status["end"] = "read returned 0 bytes"
                break
            status["reads"] += 1
            if args.die_after_read and status["reads"] == args.die_after_read:
                status["died_holding"] = len(data)
                write_json(args.status, status)
                os.kill(os.getpid(), signal.SIGKILL)
            if ring is not None:
                os.write(ring, data)
            status["bytes"] += len(data)
            status["max_read"] = max(status["max_read"], len(data))
        if time.monotonic() - last > 0.1:
            last = time.monotonic()
            write_json(args.status, status)
    write_json(args.status, status)
    return 0


# ----------------------------------------------------------- orchestrator


class Run:
    def __init__(self, scratch: str):
        self.scratch = scratch
        self.guard = Guard(scratch)
        self.procs: list[subprocess.Popen] = []
        self.findings: list[str] = []
        self.failures: list[str] = []

    def say(self, text: str) -> None:
        print(text, flush=True)

    def check(self, what: str, ok: bool, detail: str = "") -> None:
        mark = "ok  " if ok else "FAIL"
        self.say(f"  [{mark}] {what}{': ' + detail if detail else ''}")
        if not ok:
            self.failures.append(what)

    def start(self, role: str, *argv: str) -> subprocess.Popen:
        proc = subprocess.Popen(
            [sys.executable, SELF, role, "--dir", self.scratch, *argv],
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.procs.append(proc)
        self.guard.register(proc.pid)
        return proc

    def keeper(self) -> subprocess.Popen:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(KEEPER_SOCK)
        proc = self.start("keeper", "--life", str(LIFE_S))
        self.wait(lambda: os.path.exists(KEEPER_SOCK), 5, "the keeper's socket")
        return proc

    def service(self, name: str, *argv: str) -> tuple[subprocess.Popen, str]:
        status = os.path.join(self.scratch, f"{name}.json")
        with contextlib.suppress(FileNotFoundError):
            os.unlink(status)
        proc = self.start("service", "--status", status, "--life", "60", *argv)
        self.wait(lambda: "reads" in read_json(status), 5, f"service {name}")
        child = read_json(status)["pid"]
        self.guard.register(child)
        return proc, status

    def wait(self, test, seconds: float, what: str) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if test():
                return True
            time.sleep(0.02)
        self.failures.append(f"timed out waiting for {what}")
        self.say(f"  [FAIL] timed out waiting for {what}")
        return False

    def kill(self, proc: subprocess.Popen, sig: int = signal.SIGKILL) -> None:
        with contextlib.suppress(ProcessLookupError):
            proc.send_signal(sig)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(5)

    def stop_keeper(self, keeper: subprocess.Popen) -> None:
        self.kill(keeper, signal.SIGTERM)

    def blocked(self, pid: int, seconds: float = 5.0) -> float | None:
        """Seconds until the child stopped writing: asleep at every one of
        30 looks over 300 ms, its wchar standing still (wchar counts write(),
        not splice(), so for `yes` the sleep is the whole test), or None."""
        start = time.monotonic()
        while time.monotonic() - start < seconds:
            since = time.monotonic()
            before = wchar(pid)
            asleep = True
            for _ in range(30):
                stat = proc_stat(pid)
                if stat is None or stat["state"] != "S":
                    asleep = False
                    break
                time.sleep(0.01)
            if asleep and wchar(pid) == before:
                return since - start
        return None


def drain_count(fd: int, quiet: float = 0.3) -> tuple[int, int]:
    """Everything the kernel holds for this master: bytes, and reads it took."""
    os.set_blocking(fd, False)
    total = reads = 0
    last = time.monotonic()
    while time.monotonic() - last < quiet:
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            time.sleep(0.01)
            continue
        if not data:
            break
        total += len(data)
        reads += 1
        last = time.monotonic()
    return total, reads


def case_yes(run: Run) -> None:
    run.say("\n== yes: the service dies with SIGKILL while `yes` writes")
    keeper = run.keeper()
    first, status = run.service("yes-a", "--spawn", json.dumps(["yes"]))
    child = read_json(status)["pid"]
    time.sleep(1.0)
    before = proc_stat(child)
    rate = read_json(status)
    run.check("the keeper is the child's parent", before["ppid"] == keeper.pid, f"ppid {before['ppid']}")
    run.say(
        f"  service A read {rate['bytes']} bytes in {rate['reads']} reads in about 1 s, "
        f"largest read {rate['max_read']} bytes"
    )
    run.kill(first)
    took = run.blocked(child)
    stat = proc_stat(child)
    run.check("the child is alive after the service died", alive(child), f"state {stat['state']}")
    run.check(
        "the child stopped writing", took is not None, f"within {took:.2f} s" if took is not None else ""
    )
    run.say(f"  /proc/{child}/wchan: {proc_text(child, 'wchan')}")
    run.say(f"  /proc/{child}/syscall: {proc_text(child, 'syscall')}")
    run.say("  (first field: 1 is write, 275 is splice on x86-64; coreutils' yes splices)")
    sock = dial()
    reply, fds = ask(sock, {"t": "list"})
    master = fds[0]
    pending = struct.unpack("i", fcntl.ioctl(master, termios.FIONREAD, b"\0" * 4))[0]
    os.kill(child, signal.SIGSTOP)
    time.sleep(0.1)
    held, reads = drain_count(master)
    run.say(
        f"  the kernel held {held} bytes for the dead service ({reads} reads to take them); "
        f"FIONREAD had said {pending}"
    )
    os.set_blocking(master, True)
    os.close(master)
    sock.close()
    os.kill(child, signal.SIGCONT)
    second, status = run.service("yes-b", "--adopt", "1")
    time.sleep(1.0)
    after = proc_stat(child)
    rate = read_json(status)
    run.check(
        "the child writes again under service B",
        rate["bytes"] > 1024 * 1024,
        f"B read {rate['bytes']} bytes in about 1 s",
    )
    same = all(before[k] == after[k] for k in ("ppid", "pgrp", "session", "tty_nr", "tpgid"))
    run.check("parent, group, session, terminal and foreground group unchanged", same, json.dumps(after))
    run.kill(second)
    ask_sock = dial()
    ask(ask_sock, {"t": "signal", "id": 1, "signal": signal.SIGTERM})
    reaped, _ = ask(ask_sock, {"t": "reap", "id": 1})
    ask_sock.close()
    run.check(
        "the keeper reaps the child and has its status",
        reaped["ok"] and os.WIFSIGNALED(reaped["status"]),
        f"wait status {reaped['status']} (killed by signal {os.WTERMSIG(reaped['status'])})",
    )
    run.stop_keeper(keeper)


def audit_ring(path: str) -> dict:
    """The counter's ring: the numbers in order, where they break."""
    with open(path, "rb") as f:
        data = f.read()
    lines = data.split(b"\r\n")
    tail = lines.pop()
    gaps, dups, broken = [], [], []
    last = 0
    for line in lines:
        if not line.isdigit():
            broken.append(line[:40])
            continue
        n = int(line)
        if n == last + 1:
            last = n
        elif n > last + 1:
            gaps.append((last, n))
            last = n
        else:
            dups.append(n)
    return {
        "bytes": len(data),
        "lines": len(lines),
        "last": last,
        "gaps": gaps,
        "dups": dups,
        "broken": broken,
        "tail": tail,
    }


def case_counter(run: Run, worst: bool) -> None:
    name = "counter-worst" if worst else "counter"
    run.say(
        "\n== counter, the service dies between read() and the ring"
        if worst
        else "\n== counter: a numbered stream across the service's death"
    )
    keeper = run.keeper()
    ring = os.path.join(run.scratch, f"{name}.ring")
    with contextlib.suppress(FileNotFoundError):
        os.unlink(ring)
    argv = ["--spawn", json.dumps(["sh", "-c", COUNTER]), "--ring", ring]
    if worst:
        argv += ["--die-after-read", "200"]
    first, status = run.service(f"{name}-a", *argv)
    child = read_json(status)["pid"]
    if worst:
        run.wait(lambda: first.poll() is not None, 10, "service A to die on its 200th read")
    else:
        time.sleep(0.7)
        run.kill(first)
    held = read_json(status).get("died_holding")
    size_at_death = os.path.getsize(ring)
    took = run.blocked(child)
    run.check("the child blocked", took is not None, f"after {took:.2f} s" if took is not None else "")
    written = wchar(child)
    run.say(f"  /proc/{child}/syscall: {proc_text(child, 'syscall').split()[:2]} (1 is write, fd 1)")
    max_read = read_json(status).get("max_read")
    second, status_b = run.service(f"{name}-b", "--adopt", "1", "--ring", ring)
    time.sleep(0.7)
    run.kill(second, signal.SIGTERM)
    audit = audit_ring(ring)
    run.say(
        f"  ring: {audit['bytes']} bytes, {audit['lines']} lines, last number {audit['last']}; "
        f"{size_at_death} bytes were A's; A's largest read {max_read}, "
        f"B's {read_json(status_b).get('max_read')}"
    )
    if written is not None:
        # What the child had written when it blocked, as the ring would hold
        # it: every line two bytes longer than the child wrote it... one
        # byte: LF became CR LF.
        with open(ring, "rb") as f:
            data = f.read()
        lines = line_bytes = 0
        for line in data.split(b"\r\n"):
            if line_bytes + len(line) + 1 > written:
                break
            line_bytes += len(line) + 1
            lines += 1
        in_ring = line_bytes + lines
        run.say(
            f"  when it blocked the child had finished writing {written} bytes, {lines} lines; the "
            f"kernel held {in_ring - size_at_death - (held or 0)} bytes (as read) for the next reader"
        )
    if worst:
        lost = sum(len(str(n)) + 2 for a, b in audit["gaps"] for n in range(a + 1, b))
        run.say(f"  service A died holding a read of {held} bytes")
        run.check(
            "exactly that read is missing from the ring, and nothing else",
            len(audit["gaps"]) <= 1 and not audit["dups"] and abs(lost - (held or 0)) <= 16,
            f"gaps {audit['gaps']}, about {lost} bytes of numbers missing, broken lines {audit['broken']}",
        )
    else:
        run.check(
            "no number missing, none twice",
            not audit["gaps"] and not audit["dups"] and not audit["broken"],
            f"gaps {audit['gaps']} dups {audit['dups']} broken {audit['broken']}",
        )
    with contextlib.suppress(ProcessLookupError):
        os.kill(child, signal.SIGKILL)
    run.stop_keeper(keeper)


def case_winch(run: Run) -> None:
    run.say("\n== winch: sizes, the foreground group and the flags on a handed fd")
    keeper = run.keeper()
    ring = os.path.join(run.scratch, "winch.ring")
    with contextlib.suppress(FileNotFoundError):
        os.unlink(ring)
    child_argv = json.dumps([sys.executable, "-c", TICKER])
    first, status = run.service(
        "winch-a", "--spawn", child_argv, "--ring", ring, "--rows", "30", "--cols", "100"
    )
    time.sleep(1.0)
    a = read_json(status)
    child = a["pid"]
    before = proc_stat(child)
    run.say(
        f"  tcgetpgrp on the handed fd the moment the keeper answered: {a['tcgetpgrp']} "
        "(0 means the child had not taken the terminal yet)"
    )
    run.check(
        "tcgetpgrp works on the handed fd",
        a.get("tcgetpgrp_later") == before["pgrp"],
        f"{a.get('tcgetpgrp_later')} half a second later (the child's group is {before['pgrp']})",
    )
    run.check("TIOCSWINSZ works on the handed fd", a.get("size_set") == [30, 100], str(a.get("size_set")))
    run.say(f"  waitpid on the child from the service: {a['waitpid']}")
    run.kill(first)
    time.sleep(0.2)
    sock = dial()
    ask(sock, {"t": "resize", "id": 1, "rows": 50, "cols": 132})
    time.sleep(0.3)
    second, status_b = run.service("winch-b", "--adopt", "1", "--ring", ring, "--nonblock")
    time.sleep(0.6)
    b = read_json(status_b)
    run.check("tcgetpgrp works for service B", b["tcgetpgrp"] == before["pgrp"], str(b["tcgetpgrp"]))
    listed, fds = ask(sock, {"t": "list"})
    for fd in fds:
        os.close(fd)
    sock.close()
    keeper_flags = listed["ptys"][0]["flags"]
    run.check(
        "O_NONBLOCK set by the service shows on the keeper's duplicate (one open file description)",
        bool(keeper_flags & os.O_NONBLOCK) and not (b["flags_received"] & os.O_NONBLOCK),
        f"service received {b['flags_received']:#o}, set {b.get('flags_set', 0):#o}, "
        f"keeper now {keeper_flags:#o}",
    )
    run.check(
        "service B finds the size the keeper set", b["size_received"] == [50, 132], str(b["size_received"])
    )
    run.kill(second, signal.SIGTERM)
    with open(ring, "rb") as f:
        text = f.read().decode(errors="replace")
    marks = re.findall(r"(START|WINCH) (\d+) (\d+)", text)
    run.say(f"  the child reported: {marks}")
    run.check(
        "the child got SIGWINCH for both resizes, the second with no service alive",
        ("WINCH", "30", "100") in marks and ("WINCH", "50", "132") in marks,
    )
    ticks = [int(n) for n in re.findall(r"tick (\d+)\r\n", text)]
    run.check(
        "the ticks are whole across the service's death",
        ticks == list(range(1, len(ticks) + 1)) and len(ticks) > 10,
        f"{len(ticks)} ticks",
    )
    after = proc_stat(child)
    same = all(before[k] == after[k] for k in ("ppid", "pgrp", "session", "tty_nr", "tpgid"))
    run.check("session and controlling terminal survive", same and after["tty_nr"] != 0, json.dumps(after))
    with contextlib.suppress(ProcessLookupError):
        os.kill(child, signal.SIGKILL)
    run.stop_keeper(keeper)


def case_orphan(run: Run) -> None:
    run.say("\n== orphan: the keeper dies with SIGKILL, the service lives")
    keeper = run.keeper()
    ring = os.path.join(run.scratch, "orphan.ring")
    with contextlib.suppress(FileNotFoundError):
        os.unlink(ring)
    service, status = run.service(
        "orphan-a", "--spawn", json.dumps([sys.executable, "-c", TICKER]), "--ring", ring
    )
    time.sleep(0.5)
    child = read_json(status)["pid"]
    run.kill(keeper)
    time.sleep(0.5)
    stat = proc_stat(child)
    run.check("the child is alive", alive(child), f"state {stat['state']}")
    run.say(f"  its parent is now pid {stat['ppid']} ({comm(stat['ppid'])})")
    before = read_json(status)["bytes"]
    time.sleep(0.5)
    run.check("the service still reads it", read_json(status)["bytes"] > before)
    os.kill(child, signal.SIGTERM)
    run.wait(lambda: read_json(status).get("end") is not None, 5, "the service to see the pty end")
    end = read_json(status)
    run.say(f"  when the child ended the service's read said: {end.get('end')}")
    run.say(f"  the service can wait for the child: {end.get('waitpid')}")
    run.check("the child is gone, reaped by its new parent", proc_stat(child) is None)
    run.kill(service, signal.SIGTERM)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(KEEPER_SOCK)


def systemctl(*argv: str) -> str:
    result = subprocess.run(
        ["systemctl", "--user", *argv], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=20
    )
    return (result.stdout + result.stderr).strip()


def case_systemd(run: Run, mode: str) -> None:
    run.say(f"\n== systemd: the keeper as a transient user unit, KillMode={mode}")
    unit = f"collins-spike6-{os.getpid()}-{mode}.service"
    with contextlib.suppress(FileNotFoundError):
        os.unlink(KEEPER_SOCK)
    started = subprocess.run(
        ["systemd-run", "--user", "--collect", "--quiet", "--unit", unit, "-p", f"KillMode={mode}",
         sys.executable, SELF, "keeper", "--dir", run.scratch, "--life", "40"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=20,
    )  # fmt: skip
    if started.returncode != 0:
        run.say(f"  skipped: systemd-run said {started.stderr.strip()!r}")
        return
    try:
        if not run.wait(lambda: os.path.exists(KEEPER_SOCK), 5, "the unit's socket"):
            return
        main = int(systemctl("show", unit, "-p", "MainPID", "--value") or 0)
        run.guard.register(main)
        sock = dial()
        reply, fds = ask(sock, {"t": "spawn", "argv": ["sleep", "40"], "rows": 40, "cols": 120})
        child = reply["pid"]
        run.guard.register(child)
        run.say(f"  the child's cgroup: {proc_text(child, 'cgroup')}")
        systemctl("kill", "--kill-whom=main", "--signal=SIGKILL", unit)
        time.sleep(1.0)
        state = systemctl("show", unit, "-p", "ActiveState", "-p", "SubState", "-p", "Result")
        run.say(f"  after the main process was killed: child alive {alive(child)}; {state.split()}")
        if alive(child):
            stat = proc_stat(child)
            run.say(f"  the child's parent is now pid {stat['ppid']} ({comm(stat['ppid'])})")
            out = systemctl("stop", unit)
            time.sleep(1.0)
            run.say(f"  after `systemctl --user stop`: child alive {alive(child)} {out}")
        for fd in fds:
            os.close(fd)
        sock.close()
        with contextlib.suppress(ProcessLookupError):
            os.kill(child, signal.SIGKILL)
    finally:
        systemctl("stop", unit)
        systemctl("reset-failed", unit)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(KEEPER_SOCK)


def orchestrate(args) -> int:
    scratch = args.scratch or tempfile.mkdtemp(prefix="collins-spike6-")
    os.makedirs(scratch, mode=0o700, exist_ok=True)
    os.chmod(scratch, 0o700)
    os.chdir(scratch)
    signal.alarm(LIFE_S)
    run = Run(scratch)
    print(f"kernel {os.uname().release}, Python {sys.version.split()[0]}, scratch {scratch}")
    try:
        case_yes(run)
        case_counter(run, worst=False)
        case_counter(run, worst=True)
        case_winch(run)
        case_orphan(run)
        if not args.no_systemd and shutil.which("systemd-run"):
            case_systemd(run, "process")
            case_systemd(run, "control-group")
    finally:
        for proc in run.procs:
            if proc.poll() is None:
                run.kill(proc)
        left = run.guard.finish()
        print(f"\ncleanup: {len(left)} process(es) had to be killed at the end: {left}")
    if run.failures:
        print("\nNOT CONFIRMED: " + "; ".join(run.failures))
        return 1
    print("\nFinding: the writer blocks, nothing dies, and what is lost is what the dead service had")
    print("read and not yet written down. See the lines above for the sizes.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("role", nargs="?", default="run", choices=("run", "keeper", "service"))
    parser.add_argument("--scratch", help="scratch directory (default: a fresh temp dir)")
    parser.add_argument("--no-systemd", action="store_true", help="skip the transient unit cases")
    parser.add_argument("--dir")
    parser.add_argument("--life", type=float, default=60)
    parser.add_argument("--status")
    parser.add_argument("--spawn")
    parser.add_argument("--adopt", type=int)
    parser.add_argument("--ring")
    parser.add_argument("--rows", type=int)
    parser.add_argument("--cols", type=int)
    parser.add_argument("--nonblock", action="store_true")
    parser.add_argument("--die-after-read", type=int)
    args = parser.parse_args()
    if args.role == "keeper":
        return keeper_main(args)
    if args.role == "service":
        return service_main(args)
    return orchestrate(args)


if __name__ == "__main__":
    sys.exit(main())
