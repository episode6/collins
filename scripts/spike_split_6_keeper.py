"""Split-service spike 6: the keeper (spec PR-0.1 item 6, design in 3.10).

Not product code. The smallest keeper that can be measured: a process that
forks every pty child, stays their parent, holds a duplicate of every master
and hands the master to a "service" over a Unix socket with SCM_RIGHTS. The
service is then killed and replaced while the child keeps writing.

    python3 scripts/spike_split_6_keeper.py [--scratch DIR] [--no-systemd]
    python3 scripts/spike_split_6_keeper.py --only counter --repeat 20

Cases, each printed as a finding (`--only` names in the second column):

  yes      `yes` behind the pty, the service killed with SIGKILL: is the
           writer blocked in write(), how many bytes did the kernel take
           before it blocked, does it resume under a new service.
  counter  a shell printing 1, 2, 3, ...: the ring two services wrote, one
           after the other, is compared byte for byte with the stream.
           `counter`: the kill falls wherever it falls. It passes when the
           ring is the stream whole, and when it is the stream less one run
           of bytes cut out exactly where the dead service's part ends: the
           kill fell between a read and that read's write to the ring. The
           dead service's own note of what it had read (`--journal`) says
           whether it got as far as knowing about that read. `--repeat`
           gives the rate, which is this harness's and says nothing of a
           service written otherwise.
           `counter-worst`: the service dies between its read() and its
           write to the ring, on purpose. `counter-worst-full`: the same
           with the kernel's buffer full, so that the read that is lost is
           as large as a read gets.
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

    def __init__(self, scratch: str, life: float = LIFE_S):
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
        deadline = time.monotonic() + life
        # Done when told so, and when the scratch directory went away under it.
        while time.monotonic() < deadline and not os.path.exists(self.done) and os.path.exists(self.pids):
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
        try:
            with open(self.pids) as f:
                entries = [line.split() for line in f if line.strip()]
        except OSError:
            return killed
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
    chunk, read from the kernel and written nowhere. With `--journal` it
    notes how many bytes it has read, after each read and before that read
    goes to the ring, so that what it died holding can be read off two
    files: the journal's count less the ring's size."""
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
    journal = os.open(args.journal, os.O_WRONLY | os.O_CREAT, 0o600) if args.journal else None
    taken = 0
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
            if args.die_delay and status["reads"] + 1 == args.die_after_read:
                # Let the kernel fill up first, so the read that is lost is
                # as large as one read gets.
                time.sleep(args.die_delay)
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
            taken += len(data)
            if journal is not None:
                os.pwrite(journal, struct.pack(">Q", taken), 0)
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
    def __init__(self, scratch: str, life: float = LIFE_S):
        self.scratch = scratch
        self.guard = Guard(scratch, life)
        self.procs: list[subprocess.Popen] = []
        self.findings: list[str] = []
        self.outcomes: list[str] = []
        self.no_journal = False
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


def counter_stream(size: int) -> bytes:
    """What a reader of the counter's pty gets, at least `size` bytes of it:
    1, 2, 3, ... with the line discipline's CR LF after each."""
    parts, total, n = [], 0, 0
    while total < size:
        n += 1
        parts.append(b"%d\r\n" % n)
        total += len(parts[-1])
    return b"".join(parts)


def as_read(written: int) -> int:
    """Bytes a reader gets for the first `written` bytes the counter wrote
    (whole lines, LF each): one more per line."""
    total = n = 0
    while True:
        size = len(str(n + 1)) + 1
        if total + size > written:
            return total + n
        n += 1
        total += size


def differences(data: bytes, expected: bytes, limit: int = 6) -> list[dict]:
    """Where a ring leaves the stream, and how: each entry is one place, with
    the ring's offset, the stream's offset there, and what it takes to get
    the two back in step (bytes of the stream missing from the ring, bytes
    of the ring that the stream had already given, or neither found)."""
    found = []
    at = ahead = 0  # ring offset, and how far the stream is ahead of the ring
    while at < len(data) and len(found) < limit:
        rest = data[at:]
        want = expected[at + ahead : at + ahead + len(rest)]
        if rest == want:
            break
        first = next(i for i, (a, b) in enumerate(zip(rest, want, strict=False)) if a != b)
        here = at + first
        probe = data[here : here + 64]
        low = max(0, here + ahead - 262144)
        where = expected.find(probe, low, here + ahead + 262144 + len(probe))
        entry = {
            "ring_offset": here,
            "stream_offset": here + ahead,
            "ring": data[max(0, here - 24) : here + 24],
            "stream": expected[max(0, here + ahead - 24) : here + ahead + 24],
        }
        if where < 0 or len(probe) < 64:
            entry["kind"] = "altered, or out of step by more than 256 KiB"
            found.append(entry)
            break
        shift = where - (here + ahead)
        entry["kind"] = "missing" if shift > 0 else "doubled"
        entry["bytes"] = abs(shift)
        found.append(entry)
        ahead += shift
        at = here
    return found


def case_counter(run: Run, worst: bool, delay: float = 0.0) -> None:
    name = "counter-worst-full" if delay else "counter-worst" if worst else "counter"
    if delay:
        run.say("\n== counter, the service dies between read() and the ring, the read a large one")
    elif worst:
        run.say("\n== counter, the service dies between read() and the ring")
    else:
        run.say("\n== counter: a numbered stream across the service's death")
    keeper = run.keeper()
    ring = os.path.join(run.scratch, f"{name}.ring")
    with contextlib.suppress(FileNotFoundError):
        os.unlink(ring)
    argv = ["--spawn", json.dumps(["sh", "-c", COUNTER]), "--ring", ring]
    journal = None if run.no_journal else f"{ring}.read-by-a"
    if journal is not None:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(journal)
        argv += ["--journal", journal]
    if worst:
        argv += ["--die-after-read", "200", "--die-delay", str(delay)]
    first, status = run.service(f"{name}-a", *argv)
    child = read_json(status)["pid"]
    if worst:
        run.wait(lambda: first.poll() is not None, 10, "service A to die on its 200th read")
    else:
        time.sleep(0.7)
        run.kill(first)
    held = read_json(status).get("died_holding") or 0
    size_at_death = os.path.getsize(ring)
    took = run.blocked(child)
    run.check("the child blocked", took is not None, f"after {took:.2f} s" if took is not None else "")
    written = wchar(child)
    run.say(f"  /proc/{child}/syscall: {proc_text(child, 'syscall').split()[:2]} (1 is write, fd 1)")
    max_read = read_json(status).get("max_read")
    if written is not None:
        run.say(
            f"  blocked, the child had finished writing {written} bytes, {as_read(written)} as read; "
            f"service A had taken {size_at_death + held} of them: the kernel held "
            f"{as_read(written) - size_at_death - held} bytes (to within the line being written)"
        )
    second, status_b = run.service(f"{name}-b", "--adopt", "1", "--ring", ring)
    time.sleep(0.7)
    run.kill(second, signal.SIGTERM)
    with open(ring, "rb") as f:
        data = f.read()
    expected = counter_stream(len(data) + held + 524288)
    run.say(
        f"  ring: {len(data)} bytes, {size_at_death} of them service A's; A's largest read {max_read}, "
        f"B's {read_json(status_b).get('max_read')}"
    )
    if worst:
        run.say(f"  service A died holding a read of {held} bytes")
        rest = len(data) - size_at_death
        after = size_at_death + held
        run.check(
            "byte for byte: the ring is the stream with exactly that read cut out of it",
            held > 0
            and data[:size_at_death] == expected[:size_at_death]
            and data[size_at_death:] == expected[after : after + rest],
            f"bytes {size_at_death} to {after} of the stream are in no ring",
        )
    else:
        # The kill fell where it fell. Two outcomes are what the design
        # promises: the ring is the stream whole, or it is the stream with
        # one run of bytes cut out exactly where service A's part ends, which
        # is a read A took from the kernel and did not live to write down.
        # Anything else is a failure, and says where.
        noted = None
        if journal is not None:
            with contextlib.suppress(OSError, struct.error), open(journal, "rb") as f:
                noted = struct.unpack(">Q", f.read(8))[0] - size_at_death
        rest = len(data) - size_at_death
        places = differences(data, expected)
        cut = places[0]["bytes"] if len(places) == 1 and places[0]["kind"] == "missing" else 0
        one_cut = (
            cut > 0
            and data[:size_at_death] == expected[:size_at_death]
            and data[size_at_death:] == expected[size_at_death + cut : size_at_death + cut + rest]
        )
        if journal is not None:
            run.say(f"  by its own note service A had read {noted} bytes that it had not written to the ring")
        if not places and not noted:
            outcome = "whole"
            run.check("byte for byte: the ring is the stream, nothing missing, nothing twice", True)
        elif one_cut and noted == cut:
            outcome = f"one read lost, {cut} bytes, noted by A as read"
            run.check(
                "byte for byte: the ring is the stream with one read of service A's cut out at its death",
                True,
                f"bytes {size_at_death} to {size_at_death + cut} of the stream are in no ring; "
                f"A had noted reading exactly {cut} bytes more than it wrote",
            )
        elif one_cut and not noted and cut <= 65536:
            outcome = f"one read lost, {cut} bytes, not noted by A"
            run.check(
                "byte for byte: the ring is the stream with one run cut out where service A's part ends",
                True,
                f"bytes {size_at_death} to {size_at_death + cut} of the stream are in no ring; A "
                + (
                    "kept no note"
                    if journal is None
                    else "had not noted them: it died inside read(), or before its next instruction"
                ),
            )
        else:
            outcome = (
                "NEITHER: "
                + (
                    "; ".join(
                        f"{place['kind']} {place.get('bytes', '?')} at ring offset {place['ring_offset']} "
                        f"({place['ring_offset'] - size_at_death:+d} from the end of A's part)"
                        for place in places
                    )
                    or "nothing differs"
                )
                + f", A's note says {noted}"
            )
            run.check("the ring is the stream whole, or with one read of service A's cut out", False, outcome)
        for place in places:
            run.say(
                f"  the ring leaves the stream at ring offset {place['ring_offset']} (stream offset "
                f"{place['stream_offset']}; service A's part ends at {size_at_death}): "
                f"{place['kind']}, {place.get('bytes', '?')} bytes"
            )
            run.say(f"    ring   there: {place['ring']!r}")
            run.say(f"    stream there: {place['stream']!r}")
        run.outcomes.append(outcome)
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
    cases = {
        "yes": lambda: case_yes(run),
        "counter": lambda: case_counter(run, worst=False),
        "counter-worst": lambda: case_counter(run, worst=True),
        "counter-worst-full": lambda: case_counter(run, worst=True, delay=0.5),
        "winch": lambda: case_winch(run),
        "orphan": lambda: case_orphan(run),
        "systemd": lambda: (case_systemd(run, "process"), case_systemd(run, "control-group")),
    }
    chosen = [args.only] if args.only else list(cases)
    if args.no_systemd or not shutil.which("systemd-run"):
        chosen = [name for name in chosen if name != "systemd"]
    life = LIFE_S + 8 * args.repeat * len(chosen)
    run = Run(scratch, life)
    run.no_journal = args.no_journal

    def ended(signum, frame):
        raise SystemExit(f"stopped by signal {signum}")

    # The default for these is to die where it stands, leaving the children
    # to the guard, a unit to systemd and the scratch directory to nobody.
    for sig in (signal.SIGTERM, signal.SIGALRM, signal.SIGHUP):
        signal.signal(sig, ended)
    signal.alarm(int(life))
    print(f"kernel {os.uname().release}, Python {sys.version.split()[0]}, scratch {scratch}")
    try:
        for _ in range(args.repeat):
            for name in chosen:
                cases[name]()
    finally:
        for proc in run.procs:
            if proc.poll() is None:
                run.kill(proc)
        left = run.guard.finish()
        print(f"\ncleanup: {len(left)} process(es) had to be killed at the end: {left}")
        if not args.scratch:
            # A directory this script made is this script's to remove; one
            # it was given keeps its rings and logs.
            os.chdir("/")
            shutil.rmtree(scratch, ignore_errors=True)
    if run.outcomes:
        print(f"\ncounter, the service killed at an arbitrary moment, {len(run.outcomes)} run(s):")
        for outcome in sorted(set(run.outcomes)):
            print(f"  {run.outcomes.count(outcome)} x {outcome}")
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
    parser.add_argument(
        "--only",
        choices=("yes", "counter", "counter-worst", "counter-worst-full", "winch", "orphan", "systemd"),
        help="run this case alone",
    )
    parser.add_argument("--repeat", type=int, default=1, help="run the chosen cases this many times")
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
    parser.add_argument("--die-delay", type=float, default=0.0)
    parser.add_argument("--journal")
    parser.add_argument(
        "--no-journal",
        action="store_true",
        help="counter: service A keeps no note of what it read (one system call less per read)",
    )
    args = parser.parse_args()
    if args.role == "keeper":
        return keeper_main(args)
    if args.role == "service":
        return service_main(args)
    return orchestrate(args)


if __name__ == "__main__":
    sys.exit(main())
