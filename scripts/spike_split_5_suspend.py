"""Split-service spike 5: a libsoup WebSocket across a dead link (spec PR-0.1
item 5, design in 3.2).

Not product code. What does each end of the API's WebSocket see, and after
how long, when the ssh forward under it dies, when the network goes dark,
and when the client's machine sleeps; with and without libsoup's
`keepalive-interval` and `keepalive-pong-timeout`.

    python3 scripts/spike_split_5_suspend.py [--scratch DIR] [--dark 45] [--freeze 20]

The default run suspends nothing and needs no ssh. It measures stand-ins,
and they are stand-ins:

  the forward dies    A relay process stands where `ssh -L` stands (the
                      client dials a Unix socket the relay listens on, the
                      relay dials the service's). It is killed with SIGKILL
                      and, separately, asked to close with SIGTERM.
  the link goes dark  The relay stops forwarding and closes nothing, which
                      is what a client behind `ssh -L` lives through between
                      the link dying and ssh giving up (ServerAliveInterval
                      15 x ServerAliveCountMax 3 = 45 s); then it closes.
                      A second form, the blip: forwarding comes back.
  this end sleeps     The client process is frozen with SIGSTOP and thawed
                      with SIGCONT while the service keeps sending. That is
                      what suspend does to user space, with one difference:
                      CLOCK_MONOTONIC, which GLib's timeouts run on, stops
                      during a real suspend and does not stop here.

Two modes do the real thing and are for a person to run by hand:

    --ssh TARGET      the service on TARGET (key login, python3 with gi and
                      libsoup 3 there), the client here, a real `ssh -L`
                      between them with the spec's options. Kills the
                      forward, then, with --observe N, watches for N seconds
                      while you pull the cable or close the lid.
    --real-suspend    suspends THIS machine (`systemctl suspend`) after you
                      type "suspend", with the socket open; wake it by hand,
                      or pass --wake-after N to set the RTC alarm first
                      (`sudo rtcwake -m no -s N`, asks for your password).
                      Alone it measures client and service asleep together;
                      with --ssh TARGET it measures the laptop sleeping
                      under a service that does not.

Roles (`server`, `client`, `relay`) are this same file run with a first
argument. Sockets are named relative to each scenario's directory, which
every role chdirs into: sun_path holds 108 bytes. Spec F7's traps are
handled: both ends raise `max-incoming-payload-size`, the listener's URIs
are never asked for, and the socket is made 0600 in a 0700 directory.
"""

import argparse
import contextlib
import json
import os
import select
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass

SELF = os.path.abspath(__file__)
API_SOCK = "api.sock"
FWD_SOCK = "fwd.sock"
SERVER_PID = "server.pid"
PATH = "/api/ws"
MAX_PAYLOAD = 16 * 1024 * 1024
# Every ssh this script runs is a connection of its own. Under a user's
# ControlMaster / ControlPersist the forward would live in the mux master:
# killing the ssh that asked for it would kill nothing, and the ServerAlive
# options would be the master's, not these.
SSH_OWN = "-o BatchMode=yes -o ControlMaster=no -o ControlPath=none".split()
SSH_FORWARD = "-o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3".split()
REMOTE_DIR = ".cache/collins-spike5"
# For pkill -f on the far side, written so that it cannot match the command
# line of the shell that carries it.
REMOTE_PATTERN = "[.]cache/collins-spike5/spike[.]py"
SLEEP_WAIT_S = 90  # how long --real-suspend waits, awake, for the machine to sleep


def clocks() -> dict:
    return {
        "t": time.time(),
        "mono": time.clock_gettime(time.CLOCK_MONOTONIC),
        "boot": time.clock_gettime(time.CLOCK_BOOTTIME),
    }


class Log:
    def __init__(self, path: str, who: str):
        self.file = open(path, "a", buffering=1)
        self.who = who

    def __call__(self, ev: str, **fields) -> None:
        self.file.write(json.dumps({**clocks(), "who": self.who, "ev": ev, **fields}) + "\n")


def read_log(path: str) -> list[dict]:
    events = []
    with contextlib.suppress(OSError), open(path) as f:
        for line in f:
            with contextlib.suppress(ValueError):
                events.append(json.loads(line))
    return events


def rss_kib() -> int:
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") // 1024


# ------------------------------------------------------- the two libsoup ends


def on_sigterm(then) -> None:
    """GLib's own signal source. PyGObject 3.5x calls this name deprecated,
    and the GLibUnix.signal_add it names instead logs a GLib warning about a
    closure on GLib 2.88 (it works all the same); this one is quiet."""
    import warnings

    from gi.repository import GLib

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, then)


def watch(conn, log, loop, quit_on_close: bool):
    """Log everything the connection says about itself."""
    from gi.repository import GLib

    def on_error(conn, err):
        log("error", domain=str(err.domain), code=err.code, message=err.message)

    def on_closing(conn):
        log("closing", state=conn.get_state().value_nick)

    def on_closed(conn):
        log("closed", code=conn.get_close_code(), data=conn.get_close_data())
        if quit_on_close:
            GLib.timeout_add(300, loop.quit, priority=GLib.PRIORITY_DEFAULT)

    conn.connect("error", on_error)
    conn.connect("closing", on_closing)
    conn.connect("closed", on_closed)
    conn.connect("pong", lambda conn, data: log("pong"))


def keepalive(conn, args, log) -> None:
    conn.set_max_incoming_payload_size(MAX_PAYLOAD)
    if args.keepalive:
        conn.set_keepalive_interval(args.keepalive)
    if args.pong:
        conn.set_keepalive_pong_timeout(args.pong)
    log("open", keepalive=conn.get_keepalive_interval(), pong=conn.get_keepalive_pong_timeout())


def server_main(args) -> int:
    import gi

    gi.require_version("Soup", "3.0")
    from gi.repository import Gio, GLib, Soup

    os.chdir(args.dir)
    log = Log("server.log", "server")
    loop = GLib.MainLoop()
    server = Soup.Server()
    state = {"conn": None, "seq": 0, "acked": -1, "last_rx": None, "rss": rss_kib()}
    padding = bytes(max(0, args.frame_bytes - 8))

    def on_message(conn, kind, data):
        now = time.time()
        if state["last_rx"] is not None and now - state["last_rx"] > 1.0:
            log("rx-gap", seconds=round(now - state["last_rx"], 3))
        state["last_rx"] = now
        with contextlib.suppress(ValueError):
            state["acked"] = int(data.get_data().decode())

    def on_ws(server, msg, path, conn, *rest):
        cred = msg.get_socket().get_credentials()
        log("accepted", peer_uid=cred.get_unix_user(), peer_pid=cred.get_unix_pid())
        keepalive(conn, args, log)
        watch(conn, log, loop, quit_on_close=False)
        conn.connect("message", on_message)
        state["conn"] = conn

    def tick():
        conn = state["conn"]
        if conn is not None and conn.get_state() == Soup.WebsocketState.OPEN:
            state["seq"] += 1
            frame = struct.pack(">Q", state["seq"]) + padding
            conn.send_message(Soup.WebsocketDataType.BINARY, GLib.Bytes.new(frame))
        return GLib.SOURCE_CONTINUE

    def report():
        state["rss"] = max(state["rss"], rss_kib())
        log("stat", sent=state["seq"], acked=state["acked"], rss_kib=rss_kib())
        return GLib.SOURCE_CONTINUE

    server.add_websocket_handler(PATH, None, None, on_ws)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(API_SOCK)
    old = os.umask(0o177)
    server.listen(Gio.UnixSocketAddress.new(API_SOCK), Soup.ServerListenOptions(0))
    os.umask(old)
    os.chmod(API_SOCK, 0o600)
    log("listening", mode=oct(os.stat(API_SOCK).st_mode & 0o777))
    with open(SERVER_PID, "w") as f:
        f.write(f"{os.getpid()}\n")
    GLib.timeout_add(args.frame_ms, tick, priority=GLib.PRIORITY_DEFAULT)
    GLib.timeout_add(1000, report, priority=GLib.PRIORITY_DEFAULT)
    GLib.timeout_add_seconds(int(args.life), loop.quit, priority=GLib.PRIORITY_DEFAULT)
    on_sigterm(loop.quit)
    loop.run()
    log("exit", sent=state["seq"], acked=state["acked"], max_rss_kib=state["rss"])
    for name in (API_SOCK, SERVER_PID):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name)
    return 0


def client_main(args) -> int:
    import gi

    gi.require_version("Soup", "3.0")
    from gi.repository import Gio, GLib, Soup

    os.chdir(args.dir)
    log = Log("client.log", "client")
    loop = GLib.MainLoop()
    session = Soup.Session(remote_connectable=Gio.UnixSocketAddress.new(args.sock))
    state = {"conn": None, "seq": 0, "frames": 0, "lost": 0, "last_rx": None}

    def on_message(conn, kind, data):
        now = time.time()
        seq = struct.unpack(">Q", data.get_data()[:8])[0]
        if state["last_rx"] is not None and now - state["last_rx"] > 1.0:
            log("rx-gap", seconds=round(now - state["last_rx"], 3), before=state["seq"], after=seq)
        if state["seq"] and seq != state["seq"] + 1:
            state["lost"] += seq - state["seq"] - 1
        state.update(seq=seq, last_rx=now, frames=state["frames"] + 1)

    def ack():
        conn = state["conn"]
        if conn is not None and conn.get_state() == Soup.WebsocketState.OPEN:
            conn.send_text(str(state["seq"]))
        return GLib.SOURCE_CONTINUE

    def report():
        log("stat", frames=state["frames"], seq=state["seq"], lost=state["lost"])
        return GLib.SOURCE_CONTINUE

    def connected(session, result):
        try:
            conn = session.websocket_connect_finish(result)
        except GLib.Error as err:
            log("connect-failed", domain=str(err.domain), code=err.code, message=err.message)
            loop.quit()
            return
        keepalive(conn, args, log)
        watch(conn, log, loop, quit_on_close=True)
        conn.connect("message", on_message)
        state["conn"] = conn

    message = Soup.Message.new("GET", f"ws://collins{PATH}")
    session.websocket_connect_async(message, None, None, GLib.PRIORITY_DEFAULT, None, connected)
    GLib.timeout_add(250, ack, priority=GLib.PRIORITY_DEFAULT)
    GLib.timeout_add(1000, report, priority=GLib.PRIORITY_DEFAULT)
    GLib.timeout_add_seconds(int(args.life), loop.quit, priority=GLib.PRIORITY_DEFAULT)
    on_sigterm(loop.quit)
    loop.run()
    log("exit", frames=state["frames"], seq=state["seq"], lost=state["lost"])
    return 0


# ------------------------------------------------------------------ the relay


def relay_main(args) -> int:
    """Where `ssh -L` stands. SIGUSR1: go dark (forward nothing, close
    nothing). SIGUSR2: forward again. SIGTERM: close both sides and leave."""
    os.chdir(args.dir)
    log = Log("relay.log", "relay")
    flags = {"dark": False, "stop": False}
    signal.signal(signal.SIGUSR1, lambda *_: flags.update(dark=True))
    signal.signal(signal.SIGUSR2, lambda *_: flags.update(dark=False))
    signal.signal(signal.SIGTERM, lambda *_: flags.update(stop=True))
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(FWD_SOCK)
    old = os.umask(0o177)
    listener.bind(FWD_SOCK)
    os.umask(old)
    listener.listen(1)
    listener.settimeout(0.1)
    deadline = time.monotonic() + args.life
    down = None
    while down is None and not flags["stop"] and time.monotonic() < deadline:
        with contextlib.suppress(TimeoutError):
            down, _ = listener.accept()
    if down is None:
        return 0
    up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    up.connect(API_SOCK)
    for sock in (down, up):
        sock.setblocking(False)
    log("forwarding")
    pending = {down: b"", up: b""}  # bytes waiting to be written to that socket
    other = {down: up, up: down}
    moved = {"to-client": 0, "to-service": 0}
    was_dark = False
    while not flags["stop"] and time.monotonic() < deadline:
        if flags["dark"] != was_dark:
            was_dark = flags["dark"]
            log("dark" if was_dark else "light", **moved)
        if was_dark:
            time.sleep(0.02)
            continue
        readers = [s for s in (down, up) if len(pending[other[s]]) < 1024 * 1024]
        writers = [s for s in (down, up) if pending[s]]
        try:
            readable, writable, _ = select.select(readers, writers, [], 0.05)
        except InterruptedError:
            continue
        ended = False
        for sock in readable:
            try:
                data = sock.recv(65536)
            except BlockingIOError:
                continue
            except OSError:
                data = b""
            if not data:
                ended = True
                break
            pending[other[sock]] += data
        for sock in writable:
            try:
                sent = sock.send(pending[sock])
            except BlockingIOError:
                continue
            except OSError:
                ended = True
                break
            pending[sock] = pending[sock][sent:]
            moved["to-client" if sock is down else "to-service"] += sent
        if ended:
            log("peer-closed", **moved)
            break
    log("closing", **moved)
    for sock in (down, up):
        with contextlib.suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)
        sock.close()
    listener.close()
    with contextlib.suppress(FileNotFoundError):
        os.unlink(FWD_SOCK)
    return 0


# ------------------------------------------------------------------ the guard


def proc_start(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/stat") as f:
            text = f.read()
    except OSError:
        return None
    return int(text[text.rindex(")") + 2 :].split()[19])


class Guard:
    """A detached process that kills what this script registered, at the
    deadline or as soon as the script says it is done."""

    def __init__(self, scratch: str, life: float):
        self.pids = os.path.join(scratch, "guard.pids")
        self.done = os.path.join(scratch, "guard.done")
        self.lock = threading.Lock()
        open(self.pids, "w").close()
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.done)
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
        started = proc_start(pid)
        if started is not None:
            with self.lock, open(self.pids, "a") as f:
                f.write(f"{pid} {started}\n")

    def sweep(self) -> list[int]:
        killed = []
        try:
            with open(self.pids) as f:
                entries = [line.split() for line in f if line.strip()]
        except OSError:
            return killed
        for pid, started in entries:
            if proc_start(int(pid)) != int(started):
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


# --------------------------------------------------------------- orchestrator


@dataclass
class Scenario:
    name: str
    action: str  # kill, term, dark, blip, freeze
    client: tuple[int, int] = (0, 0)  # keepalive-interval, keepalive-pong-timeout
    server: tuple[int, int] = (0, 0)
    frame_ms: int = 100
    frame_bytes: int = 64


def role_argv(role: str, directory: str, life: float, keep: tuple[int, int] = (0, 0)) -> list[str]:
    return [sys.executable, SELF, role, "--dir", directory, "--life", str(int(life)),
            "--keepalive", str(keep[0]), "--pong", str(keep[1])]  # fmt: skip


def wait_for(test, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if test():
            return True
        time.sleep(0.05)
    return False


def has(path: str, ev: str) -> bool:
    return any(e["ev"] == ev for e in read_log(path))


def end_of(events: list[dict], t0: float) -> dict:
    """What one end saw after the action at t0."""
    out = {"error": None, "closed": None, "gaps": [], "stat": {}, "pongs": 0}
    for e in events:
        if e["ev"] == "stat" or e["ev"] == "exit":
            out["stat"] = e
        if e["t"] < t0:
            continue
        if e["ev"] == "error" and out["error"] is None:
            out["error"] = (e["t"] - t0, f"{e['domain']} {e['code']}: {e['message']}")
        elif e["ev"] == "closed" and out["closed"] is None:
            out["closed"] = (e["t"] - t0, e["code"])
        elif e["ev"] == "rx-gap":
            out["gaps"].append(e)
        elif e["ev"] == "pong":
            out["pongs"] += 1
    return out


def tell(end: dict) -> str:
    if end["closed"] is None:
        return "nothing; still open"
    seconds, code = end["closed"]
    what = f"closed at +{seconds:.1f} s, close code {code}"
    if end["error"] is not None:
        what += f', after error "{end["error"][1]}" at +{end["error"][0]:.1f} s'
    else:
        what += ", no error signal"
    return what


def run_scenario(sc: Scenario, base: str, guard: Guard, dark: float, freeze: float, out: dict) -> None:
    directory = os.path.join(base, sc.name.replace("/", "-").replace(" ", "-"))
    shutil.rmtree(directory, ignore_errors=True)
    os.makedirs(directory, mode=0o700)
    life = dark + freeze + 60
    procs = {}

    def start(role: str, *argv: str, keep=(0, 0)):
        procs[role] = subprocess.Popen(
            [*role_argv(role, directory, life, keep), *argv],
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        guard.register(procs[role].pid)

    def path(name: str) -> str:
        return os.path.join(directory, name)

    note = ""
    try:
        start("server", "--frame-ms", str(sc.frame_ms), "--frame-bytes", str(sc.frame_bytes), keep=sc.server)
        if not wait_for(lambda: os.path.exists(path(API_SOCK)), 10):
            raise RuntimeError("the service never listened")
        start("relay")
        if not wait_for(lambda: os.path.exists(path(FWD_SOCK)), 10):
            raise RuntimeError("the relay never listened")
        start("client", "--sock", FWD_SOCK, keep=sc.client)
        if not wait_for(lambda: has(path("client.log"), "open"), 10):
            raise RuntimeError("the client never connected")
        time.sleep(2.0)
        t0 = time.time()
        both_closed = lambda: has(path("client.log"), "closed") and has(path("server.log"), "closed")  # noqa: E731
        if sc.action == "kill":
            procs["relay"].send_signal(signal.SIGKILL)
            wait_for(both_closed, 8)
        elif sc.action == "term":
            procs["relay"].send_signal(signal.SIGTERM)
            wait_for(both_closed, 8)
        elif sc.action == "dark":
            procs["relay"].send_signal(signal.SIGUSR1)
            time.sleep(dark)
            procs["relay"].send_signal(signal.SIGTERM)
            wait_for(both_closed, 5)
            note = f"the relay closed at +{dark:.0f} s"
        elif sc.action == "blip":
            procs["relay"].send_signal(signal.SIGUSR1)
            time.sleep(freeze)
            procs["relay"].send_signal(signal.SIGUSR2)
            time.sleep(6)
            note = f"forwarding came back at +{freeze:.0f} s"
        elif sc.action == "freeze":
            procs["client"].send_signal(signal.SIGSTOP)
            time.sleep(freeze)
            procs["client"].send_signal(signal.SIGCONT)
            time.sleep(8)
            note = f"the client was thawed at +{freeze:.0f} s"
        for role in ("client", "server", "relay"):
            proc = procs.get(role)
            if proc is not None and proc.poll() is None:
                proc.send_signal(signal.SIGCONT)
                proc.send_signal(signal.SIGTERM)
        for proc in procs.values():
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(5)
        client = end_of(read_log(path("client.log")), t0)
        server = end_of(read_log(path("server.log")), t0)
        out[sc.name] = {"sc": sc, "client": client, "server": server, "note": note}
    except Exception as err:  # noqa: BLE001
        out[sc.name] = {"sc": sc, "failed": repr(err)}
    finally:
        for proc in procs.values():
            if proc.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.send_signal(signal.SIGCONT)
                    proc.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(5)


def keep_text(keep: tuple[int, int]) -> str:
    if not keep[0]:
        return "off"
    return f"{keep[0]} s" + (f", pong {keep[1]} s" if keep[1] else ", no pong timeout")


def versions() -> str:
    import gi

    gi.require_version("Soup", "3.0")
    from gi.repository import GLib, Soup

    soup = f"{Soup.get_major_version()}.{Soup.get_minor_version()}.{Soup.get_micro_version()}"
    glib = f"{GLib.MAJOR_VERSION}.{GLib.MINOR_VERSION}.{GLib.MICRO_VERSION}"
    has_pong = Soup.WebsocketConnection.find_property("keepalive-pong-timeout") is not None
    return (
        f"libsoup {soup}, GLib {glib}, kernel {os.uname().release}, Python {sys.version.split()[0]}; "
        f"keepalive-pong-timeout {'exists' if has_pong else 'DOES NOT EXIST'} on this libsoup"
    )


def stand_ins(args, base: str) -> int:
    dark, freeze = args.dark, args.freeze
    heavy = {"frame_ms": 10, "frame_bytes": 16 * 1024}
    scenarios = [
        Scenario("forward killed (SIGKILL)", "kill"),
        Scenario("forward closed (SIGTERM)", "term"),
        Scenario("dark, keepalive off", "dark"),
        Scenario("dark, client keepalive 5 s alone", "dark", client=(5, 0)),
        Scenario("dark, client 5 s + pong 5 s", "dark", client=(5, 5)),
        Scenario("dark, client 15 s + pong 15 s", "dark", client=(15, 15)),
        Scenario("dark, both ends 5 s + pong 5 s", "dark", client=(5, 5), server=(5, 5)),
        Scenario("blip, keepalive off", "blip"),
        Scenario("blip, client 5 s + pong 5 s", "blip", client=(5, 5)),
        Scenario("frozen client, keepalive off", "freeze", **heavy),
        Scenario("frozen client, client 5 s + pong 5 s", "freeze", client=(5, 5), **heavy),
        Scenario("frozen client, service 5 s + pong 5 s", "freeze", server=(5, 5), **heavy),
    ]
    if args.only:
        scenarios = [sc for sc in scenarios if args.only in sc.name]
    life = dark + freeze + 90
    guard = Guard(base, life)
    signal.alarm(int(life))
    print(versions())
    print(f"dark for {dark:.0f} s, frozen or blipped for {freeze:.0f} s; {len(scenarios)} scenarios at once")
    out: dict = {}
    threads = [
        threading.Thread(target=run_scenario, args=(sc, base, guard, dark, freeze, out), daemon=True)
        for sc in scenarios
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        left = guard.finish()
    failed = 0
    print("\n| Scenario | Client keepalive | Service keepalive | The client saw | The service saw | Note |")
    print("| --- | --- | --- | --- | --- | --- |")
    for sc in scenarios:
        result = out.get(sc.name, {"failed": "did not run"})
        if "failed" in result:
            failed += 1
            print(f"| {sc.name} | | | FAILED: {result['failed']} | | |")
            continue
        print(
            f"| {sc.name} | {keep_text(sc.client)} | {keep_text(sc.server)} | {tell(result['client'])} "
            f"| {tell(result['server'])} | {result['note']} |"
        )
    print()
    for sc in scenarios:
        result = out.get(sc.name, {})
        if "client" not in result or sc.action not in ("blip", "freeze"):
            continue
        client, server = result["client"], result["server"]
        gaps = [(g["seconds"], g.get("before"), g.get("after")) for g in client["gaps"]]
        print(
            f"{sc.name}: client frames {client['stat'].get('frames')}, last sequence "
            f"{client['stat'].get('seq')}, lost {client['stat'].get('lost')}, "
            f"gaps (s, before, after) {gaps}; "
            f"service sent {server['stat'].get('sent')}, largest resident size "
            f"{server['stat'].get('max_rss_kib')} KiB"
        )
    print(f"\ncleanup: {len(left)} process(es) had to be killed at the end: {left}")
    print("\nFinding, read off the rows above:")
    for sc in scenarios:
        result = out.get(sc.name, {})
        if "client" not in result:
            continue
        for who in ("client", "server"):
            end = result[who]
            keep = sc.client if who == "client" else sc.server
            first = min((x[0] for x in (end["error"], end["closed"]) if x is not None), default=None)
            if sc.action in ("kill", "term") or first is None:
                continue
            early = sc.action != "dark" or first < dark - 1
            if keep[1] and early:
                print(
                    f"  {sc.name}: the {who} gave up on a link at +{first:.1f} s "
                    f"(keepalive {keep_text(keep)}), its `closed` came "
                    f"{end['closed'][0] - first:.1f} s after its `error`"
                    if end["closed"] is not None and end["error"] is not None
                    else f"  {sc.name}: the {who} gave up at +{first:.1f} s"
                )
            elif sc.action == "dark":
                print(f"  {sc.name}: the {who} knew nothing until the forward closed (+{first:.1f} s)")
    return 1 if failed else 0


# ------------------------------------------------------- the modes run by hand


def ssh(target: str, command: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", *SSH_OWN, target, command],
        capture_output=True, text=True, stdin=kwargs.pop("stdin", subprocess.DEVNULL), timeout=60, **kwargs,
    )  # fmt: skip


def remote_cleanup_command() -> str:
    """What the far side's shell runs at the end: stop the service by the pid
    it recorded (only if that pid still is this script), fall back to a
    pattern when there is no record, and remove the directory whatever
    happened before."""
    return (
        f"pid=$(cat {REMOTE_DIR}/{SERVER_PID} 2>/dev/null); "
        'case "$pid" in '
        f"''|*[!0-9]*) pkill -u \"$(id -u)\" -f '{REMOTE_PATTERN}' ;; "
        "*) if grep -qa 'spike[.]py' \"/proc/$pid/cmdline\" 2>/dev/null; "
        'then kill "$pid"; fi ;; '
        "esac; "
        f"rm -rf {REMOTE_DIR}"
    )


def forward_gone(path: str, seconds: float = 5.0) -> bool:
    """True once nothing answers on the forward's local socket. A killed ssh
    leaves the socket file behind, and it refuses."""

    def refused() -> bool:
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(1)
        try:
            probe.connect(path)
        except (ConnectionRefusedError, FileNotFoundError):
            return True
        except OSError:
            return False
        finally:
            probe.close()
        return False

    return wait_for(refused, seconds)


def asleep_for(before: dict) -> float:
    """Seconds this machine has slept since `before`: CLOCK_BOOTTIME counts
    them, CLOCK_MONOTONIC does not."""
    now = clocks()
    return (now["boot"] - now["mono"]) - (before["boot"] - before["mono"])


def confirm_suspend(args) -> None:
    print(
        "\nThis will SUSPEND THIS MACHINE with `systemctl suspend`. Everything running on it\n"
        "freezes until it wakes."
        + (
            f" The RTC alarm will be set to wake it after {args.wake_after} s (sudo asks for\nyour password)."
            if args.wake_after
            else " Wake it by hand (power button, lid)."
        )
    )
    if input('Type "suspend" to go on: ').strip() != "suspend":
        raise SystemExit("not suspending")
    if args.wake_after:
        subprocess.run(["sudo", "rtcwake", "-m", "no", "-s", str(args.wake_after)], check=True)


def by_hand(args, base: str) -> int:
    """--ssh and --real-suspend. Written and documented, not run by the
    agent that wrote it: it needs a second machine, or the user's consent to
    put this one to sleep."""
    directory = os.path.join(base, "by-hand")
    shutil.rmtree(directory, ignore_errors=True)
    os.makedirs(directory, mode=0o700)
    life = args.observe + (args.wake_after or 0) + 600
    keep = (args.keepalive, args.pong)
    guard = Guard(base, life)
    procs = []
    print(versions())
    try:
        if args.ssh:
            with open(SELF) as me:
                sent = ssh(args.ssh, f"mkdir -p -m 700 {REMOTE_DIR} && cat > {REMOTE_DIR}/spike.py && "
                           f"rm -f {REMOTE_DIR}/*.log && cd {REMOTE_DIR} && pwd", stdin=me)  # fmt: skip
            if sent.returncode != 0:
                raise SystemExit(f"ssh {args.ssh} failed ({sent.returncode}): {sent.stderr.strip()}")
            remote = sent.stdout.strip().splitlines()[-1]
            server = [
                "ssh", *SSH_OWN, args.ssh,
                f"python3 {shlex.quote(remote)}/spike.py server --dir {shlex.quote(remote)} "
                f"--life {int(life)} --keepalive {keep[0]} --pong {keep[1]}",
            ]  # fmt: skip
            procs.append(subprocess.Popen(server, stdin=subprocess.DEVNULL))
            time.sleep(3)
            forward = [
                "ssh",
                *SSH_OWN,
                *SSH_FORWARD,
                "-N",
                "-L",
                f"{directory}/{FWD_SOCK}:{remote}/{API_SOCK}",
                args.ssh,
            ]
            if len(f"{directory}/{FWD_SOCK}") > 100:
                raise SystemExit("the scratch path is too long for a socket; pass a short --scratch")
            forwarder = subprocess.Popen(forward, stdin=subprocess.DEVNULL)
            procs.append(forwarder)
            sock = FWD_SOCK
        else:
            procs.append(
                subprocess.Popen(role_argv("server", directory, life, keep), stdin=subprocess.DEVNULL)
            )
            sock = API_SOCK
        for proc in procs:
            guard.register(proc.pid)
        if not wait_for(lambda: os.path.exists(os.path.join(directory, sock)), 15):
            raise SystemExit("the socket never appeared")
        client = subprocess.Popen(
            [*role_argv("client", directory, life, keep), "--sock", sock], stdin=subprocess.DEVNULL
        )
        procs.append(client)
        guard.register(client.pid)
        if not wait_for(lambda: has(os.path.join(directory, "client.log"), "open"), 15):
            raise SystemExit("the client never connected")
        print(f"connected, keepalive {keep_text(keep)} on both ends")
        time.sleep(3)
        before = clocks()
        if args.real_suspend:
            confirm_suspend(args)
            before = clocks()
            subprocess.run(["systemctl", "suspend"], check=True)
            print(
                f"asked for suspend; waiting up to {SLEEP_WAIT_S} s of time awake for this machine to have "
                "slept\n(CLOCK_BOOTTIME running ahead of CLOCK_MONOTONIC). The next line is printed "
                "after the wake.",
                flush=True,
            )
            if not wait_for(lambda: asleep_for(before) > 2, SLEEP_WAIT_S):
                print(
                    f"VOID: {SLEEP_WAIT_S} s later this machine has not slept ({asleep_for(before):.1f} s "
                    "asleep). logind took the request\nand nothing happened (an inhibitor, a lid "
                    "policy); nothing was measured."
                )
                return 1
            print(f"awake again after {asleep_for(before):.1f} s asleep")
        elif args.ssh and not args.observe_only:
            print("killing the ssh forward")
            forwarder.kill()
            forwarder.wait(10)
            if not forward_gone(os.path.join(directory, FWD_SOCK)):
                print(
                    "VOID: the ssh that was killed is gone and the forward still answers, so something "
                    "else holds it\n(a connection-sharing master this script could not opt out of?). "
                    "What the WebSocket saw says nothing\nabout a forward dying; nothing was measured."
                )
                return 1
            print("the forward is gone: its local socket refuses")
        print(f"watching for {args.observe} s; pull the cable or close the lid now if that is the test")
        wait_for(lambda: client.poll() is not None, args.observe)
        after = clocks()
        slept = (after["boot"] - after["mono"]) - (before["boot"] - before["mono"])
        print(
            f"wall clock moved {after['t'] - before['t']:.1f} s, "
            f"CLOCK_MONOTONIC {after['mono'] - before['mono']:.1f} s, "
            f"CLOCK_BOOTTIME {after['boot'] - before['boot']:.1f} s: asleep for {slept:.1f} s"
        )
        t0 = before["t"]
        print("the client:", tell(end_of(read_log(os.path.join(directory, "client.log")), t0)))
        if args.ssh:
            fetched = ssh(args.ssh, f"cat {REMOTE_DIR}/server.log")
            events = [json.loads(line) for line in fetched.stdout.splitlines() if line.startswith("{")]
            print("the service (its clock, not this one's):", tell(end_of(events, t0)))
        else:
            print("the service:", tell(end_of(read_log(os.path.join(directory, "server.log")), t0)))
        for name in ("client.log", "server.log"):
            for event in read_log(os.path.join(directory, name)):
                if event["ev"] not in ("stat", "pong") and event["t"] >= t0:
                    print("  ", json.dumps(event))
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.terminate()
        for proc in procs:
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(5)
        if args.ssh:
            with contextlib.suppress(Exception):
                ssh(args.ssh, remote_cleanup_command())
        guard.finish()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("role", nargs="?", default="run", choices=("run", "server", "client", "relay"))
    parser.add_argument("--scratch", help="scratch directory (default: a fresh temp dir)")
    parser.add_argument("--dark", type=float, default=45, help="seconds the link stays dark before it closes")
    parser.add_argument("--freeze", type=float, default=20, help="seconds of SIGSTOP, and of a blip")
    parser.add_argument("--only", help="run the scenarios whose name holds this")
    parser.add_argument("--ssh", metavar="TARGET", help="BY HAND: the service on TARGET behind a real ssh -L")
    parser.add_argument("--real-suspend", action="store_true", help="BY HAND: suspend this machine")
    parser.add_argument("--wake-after", type=int, help="with --real-suspend: set the RTC alarm (sudo)")
    parser.add_argument("--observe", type=int, default=120, help="by hand: seconds to watch afterwards")
    parser.add_argument("--observe-only", action="store_true", help="with --ssh: leave the forward alone")
    parser.add_argument("--keepalive", type=int, default=0)
    parser.add_argument("--pong", type=int, default=0)
    parser.add_argument("--dir")
    parser.add_argument("--life", type=float, default=120)
    parser.add_argument("--sock", default=API_SOCK)
    parser.add_argument("--frame-ms", type=int, default=100)
    parser.add_argument("--frame-bytes", type=int, default=64)
    args = parser.parse_args()
    if args.role == "server":
        return server_main(args)
    if args.role == "client":
        return client_main(args)
    if args.role == "relay":
        return relay_main(args)
    base = args.scratch or tempfile.mkdtemp(prefix="collins-spike5-", dir=os.environ.get("XDG_RUNTIME_DIR"))
    os.makedirs(base, mode=0o700, exist_ok=True)
    os.chmod(base, 0o700)

    def ended(signum, frame):
        raise SystemExit(f"stopped by signal {signum}")

    # The default for both is to die where it stands, leaving the children
    # to the guard and the scratch directory to nobody.
    for sig in (signal.SIGTERM, signal.SIGALRM, signal.SIGHUP):
        signal.signal(sig, ended)
    try:
        if args.ssh or args.real_suspend:
            return by_hand(args, base)
        return stand_ins(args, base)
    finally:
        if not args.scratch:
            # A directory this script made is this script's to remove; one
            # it was given keeps its logs.
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
