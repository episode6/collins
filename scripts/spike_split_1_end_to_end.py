"""Split-service spike 1: a pty server over a Unix socket into a childless VTE.

Not product code. Spec: ~/specs/collins/split-service-and-client.md, PR-0.1
item 1. Three roles live in this one file:

  driver  (default)  runs the scenarios below one after another, each as a
                     viewer process, and prints the findings. No GTK.
  viewer  (--viewer) a GTK window holding a Vte.Terminal with no child,
                     fed from a WebSocket, its `commit` signal sent back.
  server  (--server) a pty server on the GLib loop (no GTK): libsoup 3
                     WebSocket on a Unix socket, one child on a pty.

Frames are the spec's (section 3.2): JSON text frames, and binary frames
with the 16-byte header tag:u8 flags:u8 reserved:u16 stream:u32 offset:u64.

Scenarios:

  cat      the child is `cat` on a raw pty: transport latency with no CLI
           in the way, how VTE encodes keys before and after the CLI's
           keyboard modes, bracketed paste small and large, mouse reports.
  sink     the child is `cat >/dev/null`: nothing comes back, so the
           terminal paints nothing while the pointer moves. The mouse report
           rate against the rate the motion was injected at.
  pass     a real `claude` starts; the server forwards its queries and
           answers nothing (the first version's design).
  answer   the server answers the queries and still forwards them: what a
           responder without stripping would do.
  misorder answered and stripped, but DA1 answered ahead of the questions
           that came before it: does the order of the answers matter?
  strip    the design: answered by the server, stripped from the stream.
           Then typing, a paste, one real turn, the wheel, the pointer,
           a drag, a Shift+drag, a resize.

Input is synthesized as real events by the headless compositor
(org.gnome.Mutter.RemoteDesktop on the headless shell's own bus), so a key
goes compositor -> GDK -> VTE -> `commit` the way a pressed key does. The
same API on the session bus drives the user's own desktop, and the headless
wrapper leaves the command on the session bus, so the script finds the
headless compositor's private bus by its process and refuses to run when it
cannot (headless_bus_address):

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \
        python3 scripts/spike_split_1_end_to_end.py [--no-turn] [--scratch DIR]

The `strip` scenario spends ONE real turn ("count from 1 to 80 ...") unless
--no-turn is given. Every `claude` runs in an isolated HOME
(spike_split_common) and is left with Ctrl+C Ctrl+C.
"""

import argparse
import base64
import json
import os
import pty
import re
import shutil
import signal
import statistics
import struct
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spike_split_common as common  # noqa: E402

HEADER = struct.Struct(">BBHIQ")
TAG_OUTPUT, TAG_INPUT = 0x01, 0x02
MAX_PAYLOAD = 16 * 1024 * 1024  # F7: the default is 128 KiB and a larger frame closes the socket
MAX_FRAME = 1024 * 1024
WS_PATH = "/api/ws"
APP_ID = "com.episode6.Collins.SpikeSplit1"
PROMPT = "count from 1 to 80, one number per line"

# What the server's responder knows (a slice of F8, enough for the CLI's startup).
QUERIES = (
    ("DA1", re.compile(rb"\x1b\[0?c"), lambda m: b"\x1b[?61;1;21;22;28c"),
    ("XTVERSION", re.compile(rb"\x1b\[>0?q"), lambda m: b"\x1bP>|VTE(8400)\x1b\\"),
    ("kitty", re.compile(rb"\x1b\[\?u"), lambda m: b""),
    (
        "OSC10-12",
        re.compile(rb"\x1b\](1[012]);\?(\x07|\x1b\\)"),
        lambda m: b"\x1b]"
        + m.group(1)
        + (b";rgb:0000/0000/0000" if m.group(1) == b"11" else b";rgb:c0c0/c0c0/c0c0")
        + m.group(2),
    ),
)
# What a terminal's answers look like when they arrive as input.
ANSWERS = (
    ("DA1", re.compile(rb"\x1b\[\?[\d;]+c")),
    ("XTVERSION", re.compile(rb"\x1bP>\|[^\x1b]*\x1b\\")),
    ("OSC10-12", re.compile(rb"\x1b\]1[012];rgb:[0-9a-f/]+(\x07|\x1b\\)")),
    ("focus", re.compile(rb"\x1b\[[IO]")),
    ("scheme", re.compile(rb"\x1b\[\?997;\dn")),
    ("mouse", re.compile(rb"\x1b\[<\d+;\d+;\d+[Mm]")),
    ("paste", re.compile(rb"\x1b\[200~")),
)
KEYSYM = {
    "Escape": 0xFF1B,
    "Return": 0xFF0D,
    "Tab": 0xFF09,
    "BackSpace": 0xFF08,
    "Shift": 0xFFE1,
    "Control": 0xFFE3,
    "Alt": 0xFFE9,
    "space": 0x20,
}
BTN_LEFT = 0x110


def classify(data: bytes) -> dict:
    found = {}
    for name, pattern in ANSWERS:
        n = len(pattern.findall(data))
        if n:
            found[name] = n
    return found


# --------------------------------------------------------------------------
# server
# --------------------------------------------------------------------------


def run_server(args) -> int:
    import gi

    gi.require_version("Soup", "3.0")
    from gi.repository import Gio, GLib, Soup

    assert "gi.repository.Gtk" not in sys.modules
    loop = GLib.MainLoop()
    state = {"pid": None, "fd": None, "conn": None, "offset": 0, "watch": 0, "left": False}
    state["out_watch"] = 0
    pending = []
    log = []
    peers = []

    def note(kind, **fields):
        fields["k"] = kind
        fields["t"] = time.monotonic()
        log.append(fields)

    def send_output(payload: bytes, t_read: float, flags: int = 0):
        conn = state["conn"]
        for start in range(0, len(payload), MAX_FRAME):
            chunk = payload[start : start + MAX_FRAME]
            offset = state["offset"]
            state["offset"] += len(chunk)
            if conn is not None and conn.get_state() == Soup.WebsocketState.OPEN:
                frame = HEADER.pack(TAG_OUTPUT, flags, 0, 1, offset) + chunk
                conn.send_message(Soup.WebsocketDataType.BINARY, GLib.Bytes.new(frame))
            note("out", t_read=t_read, off=offset, n=len(chunk))

    def filter_queries(data: bytes) -> bytes:
        # A spike's filter: whole-chunk regexes, no carry across reads. The
        # real one is termstream's tokenizer (PR-1.1). Answers go out in the
        # order the questions came in: the CLI closes a round of questions
        # with DA1 and stops listening at its answer.
        if args.queries == "pass":
            return data
        found = []
        for name, pattern, answer in QUERIES:
            for match in pattern.finditer(data):
                found.append((match.start(), match.end(), name, answer(match)))
        found.sort()
        kept, at = [], 0
        for start, end, _name, _reply in found:
            kept.append(data[at:start])
            at = end
        if args.misorder:
            found.sort(key=lambda item: [q[0] for q in QUERIES].index(item[2]))
        for _start, _end, name, reply in found:
            note("answered", query=name, n=len(reply))
            if reply:
                pending.append(reply)
        flush()
        if args.queries == "strip":
            return b"".join(kept) + data[at:]
        return data

    def on_pty(fd, condition):
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            return GLib.SOURCE_CONTINUE
        except OSError:
            data = b""
        t_read = time.monotonic()
        if not data:
            note("pty-eof")
            state["watch"] = 0
            finish()
            return GLib.SOURCE_REMOVE
        data = filter_queries(data)
        if data:
            send_output(data, t_read)
        return GLib.SOURCE_CONTINUE

    def spawn(cols: int, rows: int):
        if state["pid"] is not None:
            return
        if args.child == "claude":
            env = common.cli_env(args.home, fullscreen=not args.classic)
            pid, fd = common.spawn_cli(env, cwd=common.workdir(args.home), rows=rows, cols=cols)
        else:
            pid, fd = pty.fork()
            if pid == 0:
                tail = " >/dev/null" if args.child == "sink" else ""
                common.exec_or_die(["sh", "-c", "stty raw -echo; exec cat" + tail])
            common.set_size(fd, rows, cols)
        state["pid"], state["fd"] = pid, fd
        with open(args.log + ".pid", "w") as f:
            f.write(f"{pid} {common.start_time(pid) or ''}")
        os.set_blocking(fd, False)
        note("spawn", child=args.child, cols=cols, rows=rows)
        state["watch"] = GLib.io_add_watch(
            fd, GLib.PRIORITY_DEFAULT, GLib.IOCondition.IN | GLib.IOCondition.HUP, on_pty
        )

    def finish():
        if state["left"]:
            return
        state["left"] = True
        for key in ("watch", "out_watch"):
            if state[key]:
                GLib.source_remove(state[key])
                state[key] = 0
        note("write-blocked", times=state.get("blocked", 0))
        if state["pid"] is not None:
            os.set_blocking(state["fd"], True)
            if args.child == "claude":
                tail = common.leave(state["pid"], state["fd"])
                note("left", n=len(tail))
            else:
                try:
                    os.kill(state["pid"], signal.SIGKILL)
                    os.waitpid(state["pid"], 0)
                except (ProcessLookupError, ChildProcessError):
                    pass
        with open(args.log, "w") as f:
            json.dump({"log": log, "peers": peers}, f)
        loop.quit()

    def flush(*_args):
        # The master is non-blocking and written from a queue: a blocking
        # write of a large paste deadlocks against a child that echoes (the
        # child blocks writing what nobody reads, and stops reading).
        while pending:
            try:
                n = os.write(state["fd"], pending[0])
            except BlockingIOError:
                if not state["out_watch"]:
                    state["blocked"] = state.get("blocked", 0) + 1
                    state["out_watch"] = GLib.io_add_watch(
                        state["fd"], GLib.PRIORITY_DEFAULT, GLib.IOCondition.OUT, flush
                    )
                return GLib.SOURCE_CONTINUE
            except OSError:
                pending.clear()
                break
            if n < len(pending[0]):
                pending[0] = pending[0][n:]
            else:
                pending.pop(0)
        state["out_watch"] = 0
        return GLib.SOURCE_REMOVE

    def on_text(message: dict):
        kind = message.get("t")
        if kind == "spawn":
            spawn(int(message["cols"]), int(message["rows"]))
        elif kind == "resize" and state["fd"] is not None:
            common.set_size(state["fd"], int(message["rows"]), int(message["cols"]))
            note("resize", cols=message["cols"], rows=message["rows"])
        elif kind == "paint":
            # Rule 2 of the spec: bytes the service inserts into the stream.
            send_output(bytes.fromhex(message["data"]), time.monotonic(), flags=0)
        elif kind == "close":
            finish()

    def on_message(conn, kind, data):
        t = time.monotonic()
        payload = data.get_data()
        if kind == Soup.WebsocketDataType.TEXT:
            try:
                message = json.loads(payload)
            except ValueError:
                return
            if isinstance(message, dict):
                on_text(message)
            return
        if len(payload) < HEADER.size or state["fd"] is None:
            return
        tag, _flags, _reserved, _stream, offset = HEADER.unpack_from(payload)
        if tag != TAG_INPUT:
            return
        body = payload[HEADER.size :]
        pending.append(body)
        flush()
        entry = {"off": offset, "n": len(body), "t_in": t, "kinds": classify(body)}
        if len(body) <= 16:
            entry["data"] = body.hex()
        note("in", **entry)

    def on_ws(server, msg, path, conn, *rest):
        sock = msg.get_socket()
        cred = sock.get_credentials() if sock.get_family() == Gio.SocketFamily.UNIX else None
        peers.append(
            {
                "uid": cred.get_unix_user() if cred else None,
                "pid": cred.get_unix_pid() if cred else None,
                "default_max_incoming": conn.get_max_incoming_payload_size(),
            }
        )
        conn.set_max_incoming_payload_size(MAX_PAYLOAD)
        state["conn"] = conn
        conn.connect("message", on_message)
        conn.connect("closed", lambda c: finish())

    directory = os.path.dirname(args.socket)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    server = Soup.Server()
    server.add_websocket_handler(WS_PATH, None, None, on_ws)
    server.listen(Gio.UnixSocketAddress.new(args.socket), Soup.ServerListenOptions(0))
    os.chmod(args.socket, 0o600)  # F7: created 0775 under the default umask
    # F7: never server.get_uris() on a Unix listener.

    # Every way out goes through finish(), which leaves the CLI with
    # Ctrl+C Ctrl+C and reaps it before this process ends: the driver
    # removes the isolated HOME only after that. The signals are GLib
    # sources, not Python handlers, which would wait for the interpreter to
    # get a turn while the loop sleeps in poll().
    def on_signal(number):
        note("signal", number=number)
        finish()
        return GLib.SOURCE_REMOVE

    try:
        gi.require_version("GLibUnix", "2.0")
        from gi.repository import GLibUnix

        add_signal = GLibUnix.signal_add
    except (ImportError, ValueError):
        add_signal = GLib.unix_signal_add
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        add_signal(GLib.PRIORITY_DEFAULT, number, on_signal, number)
    GLib.timeout_add_seconds(args.bound, lambda: finish() or False)
    print("listening", flush=True)
    try:
        loop.run()
    finally:
        finish()
    return 0


# --------------------------------------------------------------------------
# viewer
# --------------------------------------------------------------------------


def headless_bus_address() -> str | None:
    """The private session bus of the headless compositor we are drawn on.

    with-headless-display.sh hands the command the compositor's Wayland
    display and nothing else: the command's own session bus is still the
    user's, where org.gnome.Mutter.RemoteDesktop is the user's live desktop.
    The compositor is found by the display name on its command line, and its
    bus read off its environment. None when there is no such process, when
    its bus does not answer, or when its bus is the session bus (by address
    or by the id the bus gives for itself)."""
    display = os.environ.get("WAYLAND_DISPLAY", "")
    if not display.startswith("collins-e2e-"):
        return None
    wanted = f"--wayland-display={display}".encode()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                words = f.read().split(b"\0")
            if wanted not in words or b"--headless" not in words:
                continue
            if os.path.basename(words[0]) != b"gnome-shell":
                continue
            with open(f"/proc/{pid}/environ", "rb") as f:
                items = f.read().split(b"\0")
        except OSError:
            continue
        for item in items:
            if item.startswith(b"DBUS_SESSION_BUS_ADDRESS="):
                address = item.split(b"=", 1)[1].decode()
                mine = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
                if not address or address.split(",")[0] == mine.split(",")[0]:
                    return None
                return address if is_another_bus(address) else None
    return None


def bus_id(connection) -> str:
    reply = connection.call_sync(
        "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "GetId",
        None, None, 0, 3000, None,
    )  # fmt: skip
    return reply.unpack()[0]


def is_another_bus(address: str) -> bool:
    """True only when the bus at `address` answers and is not the session bus.

    Addresses are compared as strings first, but a string says little: the
    session bus is found without DBUS_SESSION_BUS_ADDRESS too (through
    $XDG_RUNTIME_DIR/bus), and one bus has many spellings. So both are asked
    who they are (org.freedesktop.DBus.GetId). The found bus not answering
    is a refusal; the session bus not answering leaves nothing to collide
    with. Asking sends no input anywhere."""
    from gi.repository import Gio, GLib

    try:
        found = Gio.DBusConnection.new_for_address_sync(
            address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None,
            None,
        )
        found_id = bus_id(found)
        found.close_sync(None)
    except GLib.Error:
        return False
    if not found_id:
        return False
    try:
        session_id = bus_id(Gio.bus_get_sync(Gio.BusType.SESSION))
    except GLib.Error:
        return True
    return session_id != found_id


class Input:
    """Real input events, through the headless compositor and only it."""

    NAME = "org.gnome.Mutter.RemoteDesktop"

    def __init__(self, Gio, GLib):
        self.GLib = GLib
        address = headless_bus_address()
        if address is None:
            raise GLib.Error("the headless compositor's own bus was not found")
        # Never the session bus: on it this API drives the user's desktop.
        self.bus = Gio.DBusConnection.new_for_address_sync(
            address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None,
            None,
        )
        reply = self.bus.call_sync(
            self.NAME, "/org/gnome/Mutter/RemoteDesktop", self.NAME, "CreateSession",
            None, None, 0, 3000, None,
        )  # fmt: skip
        self.path = reply.unpack()[0]
        self._call("Start", None)

    def _call(self, method, params):
        self.bus.call_sync(
            self.NAME, self.path, self.NAME + ".Session", method, params, None, 0, 3000, None
        )

    def key(self, keysym: int, down: bool):
        self._call("NotifyKeyboardKeysym", self.GLib.Variant("(ub)", (keysym, down)))

    def tap(self, keysym: int, mods=()):
        for mod in mods:
            self.key(KEYSYM[mod], True)
        self.key(keysym, True)
        self.key(keysym, False)
        for mod in reversed(mods):
            self.key(KEYSYM[mod], False)

    def motion(self, dx: float, dy: float):
        self._call("NotifyPointerMotionRelative", self.GLib.Variant("(dd)", (dx, dy)))

    def button(self, down: bool, button: int = BTN_LEFT):
        self._call("NotifyPointerButton", self.GLib.Variant("(ib)", (button, down)))

    def wheel(self, steps: int):
        self._call("NotifyPointerAxisDiscrete", self.GLib.Variant("(ui)", (0, steps)))

    def stop(self):
        try:
            self._call("Stop", None)
        except self.GLib.Error:
            pass


def summary(values_ms):
    if not values_ms:
        return None
    ordered = sorted(values_ms)
    p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
    return {
        "n": len(ordered),
        "mean": round(statistics.fmean(ordered), 3),
        "median": round(statistics.median(ordered), 3),
        "p95": round(p95, 3),
        "max": round(ordered[-1], 3),
        "min": round(ordered[0], 3),
    }


def run_viewer(args) -> int:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Vte", "3.91")
    gi.require_version("Soup", "3.0")
    from gi.repository import Gio, GLib, Gtk, Soup, Vte

    results = {"scenario": args.scenario, "steps": {}}
    ctx = {
        "out": [],  # (t_recv, t_fed, offset, payload)
        "commits": [],  # (t, offset, bytes)
        "in_offset": 0,
        "pointer": None,
        "conn": None,
        "closed": None,
    }
    app = Gtk.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)

    def debug(*words):
        if os.environ.get("SPIKE_DEBUG"):
            print(f"[{time.monotonic():.3f}]", *words, file=sys.stderr, flush=True)

    def out_bytes(since_index=0) -> bytes:
        return b"".join(entry[3] for entry in ctx["out"][since_index:])

    def commit_bytes(since_index=0) -> bytes:
        return b"".join(entry[2] for entry in ctx["commits"][since_index:])

    def send_json(message: dict):
        ctx["conn"].send_text(json.dumps(message))

    def paint(data: bytes):
        send_json({"t": "paint", "data": data.hex()})

    # --- the little coroutine driver -------------------------------------

    def sleep(ms):
        yield ("sleep", ms)

    def until(predicate, timeout_ms):
        yield ("until", predicate, timeout_ms)
        return bool(predicate())

    def quiet(ms, timeout_ms, need_output=True):
        start_index = len(ctx["out"])

        def is_quiet():
            if need_output and len(ctx["out"]) == start_index:
                return False
            last = ctx["out"][-1][0] if ctx["out"] else 0
            return time.monotonic() - last > ms / 1000

        return (yield from until(is_quiet, timeout_ms))

    def drive(generator, done):
        def advance():
            try:
                request = next(generator)
            except StopIteration:
                done()
                return GLib.SOURCE_REMOVE
            except Exception as err:  # noqa: BLE001 - a spike reports and leaves
                debug("scenario raised", repr(err))
                results["error"] = repr(err)
                done()
                return GLib.SOURCE_REMOVE
            if request[0] == "sleep":
                GLib.timeout_add(request[1], advance, priority=GLib.PRIORITY_DEFAULT)
            else:
                _, predicate, timeout_ms = request
                deadline = time.monotonic() + timeout_ms / 1000

                def poll():
                    if predicate() or time.monotonic() > deadline:
                        advance()
                        return GLib.SOURCE_REMOVE
                    return GLib.SOURCE_CONTINUE

                GLib.timeout_add(5, poll, priority=GLib.PRIORITY_DEFAULT)
            return GLib.SOURCE_REMOVE

        advance()

    # --- gestures ---------------------------------------------------------

    def screen(term) -> str:
        text = term.get_text_format(Vte.Format.TEXT)
        return (text[0] if isinstance(text, tuple) else text) or ""

    def type_keys(term, inp, text, gap_ms):
        samples = []
        for ch in text:
            n_commits, n_out = len(ctx["commits"]), len(ctx["out"])
            t_press = time.monotonic()
            inp.tap(ord(ch))
            want = ch.encode()

            def echoed(n_commits=n_commits, n_out=n_out, want=want):
                mine = [c for c in ctx["commits"][n_commits:] if c[2] == want]
                if not mine:
                    return False
                return any(o[0] >= mine[0][0] and want in o[3] for o in ctx["out"][n_out:])

            ok = yield from until(echoed, 2000)
            if not ok and not samples and len(text) - text.index(ch) < len(text) - 2:
                debug("no echo for the first three keys; giving up on typing")
                return samples
            if ok:
                commit = [c for c in ctx["commits"][n_commits:] if c[2] == want][0]
                after = [o for o in ctx["out"][n_out:] if o[0] >= commit[0]]
                first = after[0]
                echo = [o for o in after if want in o[3]][0]
                samples.append(
                    {
                        "press_to_commit": (commit[0] - t_press) * 1000,
                        "commit_to_first_frame": (first[0] - commit[0]) * 1000,
                        "commit_to_echo_recv": (echo[0] - commit[0]) * 1000,
                        "commit_to_echo_fed": (echo[1] - commit[0]) * 1000,
                        "in_off": commit[1],
                        "out_off": echo[2],
                        "t_commit": commit[0],
                        "t_recv": echo[0],
                    }
                )
            yield from sleep(gap_ms)
        return samples

    def erase(inp, count):
        for _ in range(count):
            inp.tap(KEYSYM["BackSpace"])
            yield from sleep(15)
        yield from sleep(400)

    def key_encodings(inp):
        table = []
        combos = (
            ("Escape", "Escape", ()),
            ("Return", "Return", ()),
            ("Shift+Return", "Return", ("Shift",)),
            ("Control+Return", "Return", ("Control",)),
            ("Alt+Return", "Return", ("Alt",)),
            ("Tab", "Tab", ()),
            ("Shift+Tab", "Tab", ("Shift",)),
            ("Control+i", ord("i"), ("Control",)),
            ("Control+m", ord("m"), ("Control",)),
            ("Alt+a", ord("a"), ("Alt",)),
            ("Control+Shift+a", ord("a"), ("Control", "Shift")),
            ("Control+BackSpace", "BackSpace", ("Control",)),
            ("Shift+space", "space", ("Shift",)),
            ("Control+period", ord("."), ("Control",)),
        )
        for label, key, mods in combos:
            n = len(ctx["commits"])
            inp.tap(KEYSYM[key] if isinstance(key, str) else key, mods)
            yield from sleep(120)
            table.append([label, commit_bytes(n).decode("latin-1").encode("unicode_escape").decode()])
        return table

    def home_pointer(term, inp, x, y):
        inp.motion(-20000, -20000)
        yield from sleep(150)
        inp.motion(x, y)
        yield from until(lambda: ctx["pointer"] is not None, 1500)
        yield from sleep(150)
        return ctx["pointer"]

    def sweep(term, inp, px_per_s, seconds, every_ms=8):
        """Move the pointer back and forth along one row at a steady speed,
        a step every 8 ms like a 125 Hz mouse (or every `every_ms`). What
        was injected is counted, so the report rate can be read against it."""
        cell_w = term.get_char_width()
        width = term.get_column_count() * cell_w
        n_commits, n_out = len(ctx["commits"]), len(ctx["out"])
        direction = 1
        x = ctx["pointer"][0] if ctx["pointer"] else 100
        start = last = time.monotonic()
        travelled = 0.0
        injected = 0
        while time.monotonic() - start < seconds:
            yield from sleep(every_ms)
            now = time.monotonic()
            step = px_per_s * (now - last)  # by the clock, so the speed holds at any rate
            last = now
            if x + direction * step > width - 20 or x + direction * step < 20:
                direction = -direction
            inp.motion(direction * step, 0)
            injected += 1
            x += direction * step
            travelled += step
        elapsed = time.monotonic() - start
        yield from sleep(200)
        sent = commit_bytes(n_commits)
        reports = re.findall(rb"\x1b\[<(\d+);(\d+);(\d+)[Mm]", sent)
        repeats = sum(1 for a, b in zip(reports, reports[1:], strict=False) if a == b)
        return {
            "px_per_s": px_per_s,
            "seconds": round(elapsed, 2),
            "injected_motions": injected,
            "injected_per_s": round(injected / elapsed, 1),
            "cells_crossed": round(travelled / cell_w),
            "reports": len(reports),
            "reports_same_cell_as_the_one_before": repeats,
            "commits": len(ctx["commits"]) - n_commits,
            "bytes": len(sent),
            "bytes_per_s": round(len(sent) / elapsed),
            "reports_per_s": round(len(reports) / elapsed, 1),
            "buttons_seen": sorted({int(r[0]) for r in reports}),
            "output_bytes_meanwhile": len(out_bytes(n_out)),
        }

    def drag(term, inp, shift):
        cell_w, cell_h = term.get_char_width(), term.get_char_height()
        yield from home_pointer(term, inp, 10.5 * cell_w, 5.5 * cell_h)
        n_commits, n_out = len(ctx["commits"]), len(ctx["out"])
        term.unselect_all()
        if shift:
            inp.key(KEYSYM["Shift"], True)
            yield from sleep(120)
        inp.button(True)
        yield from sleep(120)
        for _ in range(30):
            inp.motion(cell_w, 0)
            yield from sleep(20)
        yield from sleep(150)
        inp.button(False)
        yield from sleep(120)
        if shift:
            inp.key(KEYSYM["Shift"], False)
        yield from sleep(400)
        sent = commit_bytes(n_commits)
        selected = term.get_text_selected(Vte.Format.TEXT) if term.get_has_selection() else None
        produced = out_bytes(n_out)
        return {
            "shift": shift,
            "committed": sent[:60].decode("latin-1").encode("unicode_escape").decode(),
            "committed_tail": sent[-40:].decode("latin-1").encode("unicode_escape").decode(),
            "committed_bytes": len(sent),
            "vte_has_selection": term.get_has_selection(),
            "vte_selected": selected,
            "output_bytes": len(produced),
            "output_has_osc52": b"\x1b]52;" in produced,
        }

    def wheel(term, inp, steps):
        n_commits, n_out = len(ctx["commits"]), len(ctx["out"])
        before = screen(term)
        for _ in range(abs(steps)):
            inp.wheel(1 if steps > 0 else -1)
            yield from sleep(40)
        yield from sleep(600)
        after = screen(term)

        def numbers(text):
            return [int(line) for line in (row.strip() for row in text.splitlines()) if line.isdigit()]

        sent = commit_bytes(n_commits)
        return {
            "steps": steps,
            "committed": sent[:120].decode("latin-1").encode("unicode_escape").decode(),
            "reports": len(re.findall(rb"\x1b\[<6[45];", sent)),
            "output_bytes": len(out_bytes(n_out)),
            "screen_changed": before != after,
            "numbers_before": numbers(before)[:3] + numbers(before)[-1:],
            "numbers_after": numbers(after)[:3] + numbers(after)[-1:],
        }

    def osc52(term):
        """Does VTE act on OSC 52 fed to it: a clipboard write, then a
        clipboard read, then DSR 5 as a sentinel so the silence is bounded."""
        marker = "spike-split-1-osc52"
        payload = base64.b64encode(marker.encode())
        n_commits = len(ctx["commits"])
        paint(b"\x1b]52;c;" + payload + b"\x07" + b"\x1b]52;c;?\x07" + b"\x1b[5n")
        yield from until(lambda: b"\x1b[0n" in commit_bytes(n_commits), 3000)
        yield from sleep(300)
        holder = {}

        def got(clipboard, result):
            try:
                holder["text"] = clipboard.read_text_finish(result)
            except GLib.Error as err:
                holder["error"] = err.message

        term.get_clipboard().read_text_async(None, got)
        yield from until(lambda: bool(holder), 3000)
        sent = commit_bytes(n_commits)
        return {
            "committed": sent.decode("latin-1").encode("unicode_escape").decode(),
            "answered_the_read": b"\x1b]52;" in sent,
            "sentinel_answered": b"\x1b[0n" in sent,
            "clipboard_holds_the_write": holder.get("text") == marker,
            "clipboard": holder.get("text") if "text" in holder else holder.get("error", "no reply"),
        }

    def paste(term, text):
        n_commits, n_out = len(ctx["commits"]), len(ctx["out"])
        term.paste_text(text)
        yield from sleep(200)
        yield from quiet(300, 4000, need_output=False)
        sent = commit_bytes(n_commits)
        return {
            "chars": len(text),
            "commits": len(ctx["commits"]) - n_commits,
            "commit_sizes": [len(c[2]) for c in ctx["commits"][n_commits:]][:8],
            "bracketed": sent.startswith(b"\x1b[200~") and sent.endswith(b"\x1b[201~"),
            "whole": sent.replace(b"\x1b[200~", b"").replace(b"\x1b[201~", b"")
            == text.replace("\n", "\r").encode(),
            "output_bytes": len(out_bytes(n_out)),
        }

    # --- scenarios --------------------------------------------------------

    def startup_facts(term):
        sent = commit_bytes()
        produced = out_bytes()
        return {
            "output_bytes": len(produced),
            "queries_in_stream": {
                name: len(pattern.findall(produced)) for name, pattern, _ in QUERIES
            },
            "viewer_committed": classify(sent),
            "viewer_committed_raw": sent[:200].decode("latin-1").encode("unicode_escape").decode(),
            "decrqm_in_stream": [m.decode() for m in re.findall(rb"\x1b\[\?(\d+)\$p", produced)],
            "alt_screen": b"\x1b[?1049h" in produced,
            "any_motion": b"\x1b[?1003h" in produced,
            "prompt_on_screen": "❯" in screen(term),
        }

    def scenario_cat(term, inp):
        steps = results["steps"]
        yield from sleep(500)
        steps["latency"] = yield from type_keys(term, inp, "thequickbrownfoxjumpsoverthelazydog" * 2, 40)
        steps["keys_legacy"] = yield from key_encodings(inp)
        paint(b"\x1b[>5u\x1b[>4;2m")
        yield from sleep(300)
        steps["keys_after_kitty_push"] = yield from key_encodings(inp)
        paint(b"\x1b[?2004h")
        yield from sleep(300)
        steps["paste_small"] = yield from paste(term, "first line\nsecond line")
        steps["paste_100k"] = yield from paste(term, ("0123456789abcdef" * 64 + "\n") * 100)
        term.reset(True, True)
        steps["osc52"] = yield from osc52(term)
        paint(b"\x1b[?1003h\x1b[?1006h")
        yield from sleep(300)
        cell_w, cell_h = term.get_char_width(), term.get_char_height()
        steps["pointer"] = yield from home_pointer(term, inp, 20 * cell_w, 10.5 * cell_h)
        steps["motion"] = []
        for speed in (200, 800, 2500):
            steps["motion"].append((yield from sweep(term, inp, speed, 2.0)))
        # The same middle speed injected faster and slower: is the report
        # rate the injection's, or a ceiling further down?
        steps["motion_by_injection_rate"] = []
        for every_ms in (2, 4, 16, 33):
            fact = yield from sweep(term, inp, 800, 2.0, every_ms=every_ms)
            steps["motion_by_injection_rate"].append(fact)
        steps["wheel"] = yield from wheel(term, inp, -3)
        steps["drag"] = yield from drag(term, inp, shift=False)
        paint(b"some words to select in the terminal, more than thirty cells of them\r\n" * 8)
        yield from sleep(300)
        steps["shift_drag"] = yield from drag(term, inp, shift=True)

    def scenario_sink(term, inp):
        # Nothing comes back, so the terminal has nothing to paint while the
        # pointer moves: the state a CLI that ignores hover leaves it in.
        steps = results["steps"]
        yield from sleep(500)
        paint(b"\x1b[?1003h\x1b[?1006h")
        yield from sleep(300)
        cell_w, cell_h = term.get_char_width(), term.get_char_height()
        steps["pointer"] = yield from home_pointer(term, inp, 20 * cell_w, 10.5 * cell_h)
        steps["motion_by_injection_rate"] = []
        for every_ms in (1, 2, 4, 8, 16):
            fact = yield from sweep(term, inp, 800, 2.0, every_ms=every_ms)
            steps["motion_by_injection_rate"].append(fact)

    def scenario_startup(term, inp):
        yield from quiet(2000, 30000)
        yield from sleep(500)
        results["steps"]["startup"] = startup_facts(term)

    def scenario_strip(term, inp):
        steps = results["steps"]
        yield from quiet(2000, 30000)
        yield from sleep(500)
        steps["startup"] = startup_facts(term)
        if not steps["startup"]["prompt_on_screen"]:
            steps["aborted"] = "no prompt on screen after startup: " + repr(screen(term)[-400:])
            return
        steps["latency"] = yield from type_keys(term, inp, "thequickbrownfoxjumpsoverthelazydog", 150)
        steps["typed_on_screen"] = "quickbrownfox" in screen(term)
        yield from erase(inp, 40)
        steps["paste_small"] = yield from paste(term, "first line\nsecond line")
        steps["paste_on_screen"] = "second line" in screen(term)
        yield from erase(inp, 30)
        steps["paste_2000"] = yield from paste(term, ("0123456789abcdef" * 5 + "\n") * 25)
        steps["paste_2000_screen_tail"] = [r for r in screen(term).splitlines() if "Pasted" in r][:2]
        yield from erase(inp, 6)
        steps["box_after_erase"] = [r for r in screen(term).splitlines() if "❯" in r][:2]
        if args.turn:
            n_out = len(ctx["out"])
            t0 = time.monotonic()
            term.feed_child(PROMPT.encode())
            yield from sleep(400)
            inp.tap(KEYSYM["Return"])

            def finished():
                produced = out_bytes(n_out)
                states = re.findall(rb"\x1b\]9;4;(\d)", produced)
                cleared = bool(states) and states[-1] == b"0" and any(s != b"0" for s in states)
                last = ctx["out"][-1][0]
                return cleared and time.monotonic() - last > 1.5

            ok = yield from until(finished, 120000)
            seconds = {}
            for entry in ctx["out"][n_out:]:
                second = int(entry[0] - t0)
                seconds[second] = seconds.get(second, 0) + len(entry[3])
            steps["turn"] = {
                "finished_by_progress_clear": ok,
                "seconds": round(time.monotonic() - t0, 1),
                "output_bytes": len(out_bytes(n_out)),
                "busiest_second_bytes": max(seconds.values()) if seconds else 0,
                "frames": len(ctx["out"]) - n_out,
                "has_80": "80" in screen(term),
            }
        cell_w, cell_h = term.get_char_width(), term.get_char_height()
        steps["pointer"] = yield from home_pointer(term, inp, 20 * cell_w, 10.5 * cell_h)
        steps["wheel_up"] = yield from wheel(term, inp, -5)
        steps["wheel_down"] = yield from wheel(term, inp, 5)
        steps["motion"] = []
        for speed in (200, 800, 2500):
            steps["motion"].append((yield from sweep(term, inp, speed, 2.0)))
        steps["drag"] = yield from drag(term, inp, shift=False)
        inp.tap(KEYSYM["Escape"])
        yield from sleep(300)
        steps["shift_drag"] = yield from drag(term, inp, shift=True)
        term.unselect_all()
        n_commits, n_out = len(ctx["commits"]), len(ctx["out"])
        term.set_size(100, 30)
        send_json({"t": "resize", "cols": 100, "rows": 30})
        yield from quiet(800, 6000)
        produced = out_bytes(n_out)
        steps["resize"] = {
            "grid": [term.get_column_count(), term.get_row_count()],
            "output_bytes": len(produced),
            "clear_screen": b"\x1b[2J" in produced,
            "clear_scrollback": b"\x1b[3J" in produced,
            "committed_by_resize": commit_bytes(n_commits)
            .decode("latin-1")
            .encode("unicode_escape")
            .decode(),
            "prompt_on_screen": "❯" in screen(term),
        }

    # --- assembly ---------------------------------------------------------

    def activate(app):
        win = Gtk.ApplicationWindow(application=app)
        win.set_decorated(False)
        term = Vte.Terminal()
        term.set_size(args.cols, args.rows)
        term.set_halign(Gtk.Align.START)
        term.set_valign(Gtk.Align.START)
        term.set_hexpand(False)
        term.set_vexpand(False)
        box = Gtk.Box()
        box.append(term)
        win.set_child(box)
        win.fullscreen()
        win.present()

        motion = Gtk.EventControllerMotion()
        motion.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        motion.connect("motion", lambda c, x, y: ctx.__setitem__("pointer", (x, y)))
        term.add_controller(motion)

        def on_commit(term, text, size):
            t = time.monotonic()
            data = text.encode("utf-8", "surrogateescape")
            debug("commit", data[:40])
            offset = ctx["in_offset"]
            ctx["in_offset"] += len(data)
            ctx["commits"].append((t, offset, data))
            conn = ctx["conn"]
            if conn is not None and conn.get_state() == Soup.WebsocketState.OPEN:
                for start in range(0, len(data), MAX_FRAME):
                    chunk = data[start : start + MAX_FRAME]
                    frame = HEADER.pack(TAG_INPUT, 0, 0, 1, offset + start) + chunk
                    conn.send_message(Soup.WebsocketDataType.BINARY, GLib.Bytes.new(frame))

        term.connect("commit", on_commit)

        def on_message(conn, kind, data):
            t_recv = time.monotonic()
            payload = data.get_data()
            if kind != Soup.WebsocketDataType.BINARY or len(payload) < HEADER.size:
                return
            tag, _flags, _reserved, _stream, offset = HEADER.unpack_from(payload)
            if tag != TAG_OUTPUT:
                return
            body = payload[HEADER.size :]
            term.feed(body)
            ctx["out"].append((t_recv, time.monotonic(), offset, body))

        def finish():
            results["grid"] = [term.get_column_count(), term.get_row_count()]
            results["window_active"] = win.is_active()
            results["default_max_incoming_client"] = ctx.get("default_max")
            results["totals"] = {
                "output_bytes": len(out_bytes()),
                "input_bytes": len(commit_bytes()),
                "commit_kinds": classify(commit_bytes()),
            }
            conn = ctx["conn"]
            if conn is not None and conn.get_state() == Soup.WebsocketState.OPEN:
                send_json({"t": "close"})
            with open(args.results, "w") as f:
                json.dump(results, f)
            # The raw streams, for reading by hand. Scratch only: a recording
            # holds paths and is never committed.
            with open(args.results + ".out.bin", "wb") as f:
                f.write(out_bytes())
            with open(args.results + ".in.bin", "wb") as f:
                f.write(commit_bytes())
            if ctx.get("inp") is not None:
                ctx["inp"].stop()
            # Let the close frame leave before the process does.
            GLib.timeout_add(400, lambda: app.quit() or False, priority=GLib.PRIORITY_DEFAULT)

        def connected(session, result):
            try:
                conn = session.websocket_connect_finish(result)
            except GLib.Error as err:
                results["error"] = "connect: " + err.message
                finish()
                return
            ctx["default_max"] = conn.get_max_incoming_payload_size()
            conn.set_max_incoming_payload_size(MAX_PAYLOAD)
            ctx["conn"] = conn
            conn.connect("message", on_message)
            conn.connect("closed", lambda c: ctx.__setitem__("closed", time.monotonic()))
            try:
                ctx["inp"] = Input(Gio, GLib)
            except GLib.Error as err:
                results["error"] = "no RemoteDesktop on this bus: " + err.message
                finish()
                return
            send_json({"t": "spawn", "cols": args.cols, "rows": args.rows})
            term.grab_focus()
            scenario = {
                "cat": scenario_cat,
                "sink": scenario_sink,
                "pass": scenario_startup,
                "answer": scenario_startup,
                "misorder": scenario_startup,
                "strip": scenario_strip,
            }[args.scenario]

            def begin():
                def all_of_it():
                    yield from until(lambda: win.is_active() and term.has_focus(), 10000)
                    yield from sleep(300)
                    debug("active", win.is_active(), "focus", term.has_focus())
                    yield from scenario(term, ctx["inp"])

                drive(all_of_it(), finish)
                return GLib.SOURCE_REMOVE

            GLib.timeout_add(300, begin, priority=GLib.PRIORITY_DEFAULT)

        session = Soup.Session(remote_connectable=Gio.UnixSocketAddress.new(args.socket))
        ctx["session"] = session
        message = Soup.Message.new("GET", "ws://collins" + WS_PATH)
        session.websocket_connect_async(
            message, None, None, GLib.PRIORITY_DEFAULT, None, connected
        )

    def watchdog():
        results["error"] = "watchdog"
        with open(args.results, "w") as f:
            json.dump(results, f)
        os._exit(3)

    app.connect("activate", activate)
    GLib.timeout_add_seconds(args.bound, watchdog)
    app.run([])
    return 0


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------


def stop_server(server, pid_path, patience: float = 15.0) -> None:
    """Wait for the server to leave its child and go; ask it to (SIGTERM,
    which it answers by leaving the CLI with Ctrl+C Ctrl+C) when it has not.
    Only a server that ignores that is killed, and then the child it left
    behind is waited for too, so the HOME is never removed under a CLI that
    is still writing its records."""
    if server.poll() is None:
        try:
            server.wait(timeout=patience)
        except subprocess.TimeoutExpired:
            server.terminate()
            try:
                server.wait(timeout=patience)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
    try:
        with open(pid_path) as f:
            words = f.read().split()
        child, started = int(words[0]), (words[1] if len(words) > 1 else None)
    except (OSError, ValueError, IndexError):
        return
    if started is None or common.start_time(child) != started:
        # Gone, or the pid is somebody else's by now: nothing of ours to wait
        # for and nothing of ours to kill.
        try:
            os.unlink(pid_path)
        except OSError:
            pass
        return
    # The killed server's master closed with it, so the child has its
    # SIGHUP already; give it the time leave() would have.
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline and os.path.exists(f"/proc/{child}"):
        time.sleep(0.1)
    if common.start_time(child) == started:
        try:
            os.killpg(child, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        time.sleep(0.3)
    try:
        os.unlink(pid_path)
    except OSError:
        pass


def run_scenario(name, scratch, runtime, turn, bound) -> dict:
    """One server, one viewer, one child. Returns what both wrote down."""
    socket_path = os.path.join(runtime, f"{name}.sock")
    log_path = os.path.join(scratch, f"{name}.server.json")
    results_path = os.path.join(scratch, f"{name}.viewer.json")
    for path in (log_path, results_path):
        if os.path.exists(path):
            os.unlink(path)
    child = name if name in ("cat", "sink") else "claude"
    home = common.make_home(parent=scratch) if child == "claude" else None
    try:
        return _run_scenario(name, scratch, turn, bound, child, home, socket_path, log_path, results_path)
    finally:
        if home:
            common.remove_home(home)
        if os.path.exists(socket_path):
            os.unlink(socket_path)


def _run_scenario(name, scratch, turn, bound, child, home, socket_path, log_path, results_path):
    runtime = os.path.dirname(socket_path)
    me = os.path.abspath(__file__)
    server_cmd = [
        sys.executable, me, "--server", "--socket", socket_path, "--log", log_path,
        "--child", child, "--queries", name if name in ("pass", "answer") else "strip",
        "--bound", str(bound + 20),
    ]  # fmt: skip
    if name == "misorder":
        server_cmd.append("--misorder")
    if home:
        server_cmd += ["--home", home]
    server = subprocess.Popen(server_cmd, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    out = {"scenario": name}
    try:
        line = server.stdout.readline()
        if b"listening" not in line:
            out["error"] = "the server did not come up"
            return out
        out["socket_mode"] = oct(os.stat(socket_path).st_mode & 0o777)
        out["socket_dir_mode"] = oct(os.stat(runtime).st_mode & 0o777)
        viewer_cmd = [
            sys.executable, me, "--viewer", "--socket", socket_path, "--results", results_path,
            "--scenario", name, "--bound", str(bound),
        ]  # fmt: skip
        if turn:
            viewer_cmd.append("--turn")
        try:
            with open(os.path.join(scratch, f"{name}.viewer.err"), "wb") as err:
                viewer = subprocess.run(
                    viewer_cmd, stdin=subprocess.DEVNULL, stdout=err, stderr=err, timeout=bound + 10
                )
            out["viewer_exit"] = viewer.returncode
            with open(os.path.join(scratch, f"{name}.viewer.err"), "rb") as err:
                out["viewer_stderr"] = err.read().decode(errors="replace")[-600:]
        except subprocess.TimeoutExpired:
            out["viewer_exit"] = "timeout"
        stop_server(server, log_path + ".pid")
        if os.path.exists(results_path):
            with open(results_path) as f:
                out["viewer"] = json.load(f)
        if os.path.exists(log_path):
            with open(log_path) as f:
                out["server"] = json.load(f)
    finally:
        stop_server(server, log_path + ".pid")
    return out


def latency_report(run) -> dict:
    """Join the viewer's samples with the server's log on stream offsets."""
    samples = run.get("viewer", {}).get("steps", {}).get("latency") or []
    log = run.get("server", {}).get("log", [])
    ins = {e["off"]: e for e in log if e["k"] == "in"}
    outs = sorted((e for e in log if e["k"] == "out"), key=lambda e: e["off"])
    legs = {"up": [], "child": [], "down": []}
    for sample in samples:
        arrived = ins.get(sample["in_off"])
        frame = next((o for o in outs if o["off"] <= sample["out_off"] < o["off"] + o["n"]), None)
        if arrived is None or frame is None:
            continue
        legs["up"].append((arrived["t"] - sample["t_commit"]) * 1000)
        legs["child"].append((frame["t_read"] - arrived["t"]) * 1000)
        legs["down"].append((sample["t_recv"] - frame["t_read"]) * 1000)
    report = {
        key: summary([s[key] for s in samples])
        for key in (
            "press_to_commit",
            "commit_to_first_frame",
            "commit_to_echo_recv",
            "commit_to_echo_fed",
        )
    }
    report["commit_to_pty_write"] = summary(legs["up"])
    report["pty_write_to_pty_read"] = summary(legs["child"])
    report["pty_read_to_viewer_recv"] = summary(legs["down"])
    return report


def answers_reaching_pty(run) -> dict:
    total = {}
    for entry in run.get("server", {}).get("log", []):
        if entry["k"] == "in":
            for kind, n in entry.get("kinds", {}).items():
                total[kind] = total.get(kind, 0) + n
    from_server = {}
    for entry in run.get("server", {}).get("log", []):
        if entry["k"] == "answered" and entry["n"]:
            from_server[entry["query"]] = from_server.get(entry["query"], 0) + 1
    return {"from_the_viewer": total, "from_the_server": from_server}


def verdicts(runs) -> list:
    """The findings in sentences, from whichever scenarios ran."""
    lines = []

    def steps(name):
        return runs.get(name, {}).get("viewer", {}).get("steps", {})

    for name, label in (("cat", "`cat` behind the pty"), ("strip", "`claude` behind the pty")):
        if steps(name).get("latency"):
            report = latency_report(runs[name])
            total, child = report["commit_to_echo_fed"], report["pty_write_to_pty_read"]
            lines.append(
                f"Keystroke to echo, {label}: commit to feed mean {total['mean']} ms, median "
                f"{total['median']}, p95 {total['p95']}, max {total['max']} (n={total['n']}); "
                f"of that the child took {child['mean'] if child else '?'} ms."
            )
    for name in ("cat", "sink", "strip"):
        motion = steps(name).get("motion")
        if motion:
            rates = ", ".join(f"{m['bytes_per_s']} B/s at {m['px_per_s']} px/s" for m in motion)
            reports = sum(m["reports"] for m in motion)
            cells = sum(m["cells_crossed"] for m in motion)
            repeats = sum(m["reports_same_cell_as_the_one_before"] for m in motion)
            injected = sum(m["injected_motions"] for m in motion)
            lines.append(
                f"Pointer motion ({name}): mouse reports arrive through `commit`: {rates}; "
                f"{injected} motions injected, {reports} reports, {cells} cells crossed, "
                f"{repeats} reports naming the same cell as the report before."
            )
        by_rate = steps(name).get("motion_by_injection_rate")
        if by_rate:
            pairs = ", ".join(
                f"{m['injected_per_s']}/s in -> {m['reports_per_s']}/s out ({m['bytes_per_s']} B/s)"
                for m in by_rate
            )
            lines.append(f"Report rate against injection rate ({name}, 800 px/s): {pairs}.")
        fact = steps(name).get("osc52")
        if fact:
            lines.append(
                f"OSC 52 fed to VTE ({name}): clipboard holds the write "
                f"{fact['clipboard_holds_the_write']}, the read was answered "
                f"{fact['answered_the_read']}, committed {fact['committed']!r}."
            )
        for key in ("paste_small", "paste_100k", "paste_2000"):
            fact = steps(name).get(key)
            if fact:
                lines.append(
                    f"Paste of {fact['chars']} chars ({name}): {fact['commits']} commit(s), "
                    f"bracketed {fact['bracketed']}, whole {fact['whole']}."
                )
        for key in ("drag", "shift_drag"):
            fact = steps(name).get(key)
            if fact:
                lines.append(
                    f"{key} ({name}): {fact['committed_bytes']} bytes committed, VTE selection "
                    f"{fact['vte_has_selection']}, OSC 52 in the reply {fact['output_has_osc52']}."
                )
    for key in ("wheel_up", "wheel_down"):
        fact = steps("strip").get(key)
        if fact:
            lines.append(
                f"{key}: {fact['reports']} reports committed, the CLI wrote {fact['output_bytes']} "
                f"bytes, numbers on screen {fact['numbers_before']} -> {fact['numbers_after']}."
            )
    if steps("strip").get("resize"):
        fact = steps("strip")["resize"]
        lines.append(
            f"Resize to {fact['grid']}: the CLI wrote {fact['output_bytes']} bytes, ED 2 "
            f"{fact['clear_screen']}, ED 3 {fact['clear_scrollback']}; the resize itself committed "
            f"{fact['committed_by_resize']!r}."
        )
    for name in ("pass", "answer", "misorder", "strip"):
        if "startup" in steps(name):
            reached = answers_reaching_pty(runs[name])
            lines.append(
                f"Startup, queries `{name}`: answers into the pty from the viewer "
                f"{reached['from_the_viewer']}, from the server {reached['from_the_server']}; "
                f"DECRQM asked {steps(name)['startup']['decrqm_in_stream']}."
            )
    return lines


def show(title, value):
    print(f"\n## {title}")
    if isinstance(value, (dict, list)):
        print(json.dumps(value, indent=1, ensure_ascii=False))
    else:
        print(value)


def versions() -> dict:
    found = {"python": sys.version.split()[0]}
    try:
        import gi

        gi.require_version("Vte", "3.91")
        gi.require_version("Soup", "3.0")
        from gi.repository import Soup, Vte

        found["vte"] = f"{Vte.get_major_version()}.{Vte.get_minor_version()}.{Vte.get_micro_version()}"
        found["libsoup"] = (
            f"{Soup.get_major_version()}.{Soup.get_minor_version()}.{Soup.get_micro_version()}"
        )
    except (ImportError, ValueError) as err:
        found["missing"] = str(err)
    if shutil.which("claude"):
        try:
            found["claude"] = subprocess.run(
                ["claude", "--version"], capture_output=True, text=True, timeout=20,
                stdin=subprocess.DEVNULL,
            ).stdout.strip()  # fmt: skip
        except (OSError, subprocess.SubprocessError):
            pass
    return found


def run_driver(args) -> int:
    def terminated(*_args):
        raise SystemExit(143)

    signal.signal(signal.SIGTERM, terminated)
    found = versions()
    show("versions", found)
    if "missing" in found:
        print("skipped: VTE or libsoup typelibs are missing:", found["missing"])
        return 0
    if headless_bus_address() is None:
        print(
            "skipped: not inside the headless display wrapper. This spike types through the "
            "compositor's remote-desktop API and would type into your own desktop. Run it as\n"
            "  bash .agents/capture-screenshots/scripts/with-headless-display.sh "
            "python3 scripts/spike_split_1_end_to_end.py"
        )
        return 0
    scratch = args.scratch or tempfile.mkdtemp(prefix="spike-split-1-")
    os.makedirs(scratch, exist_ok=True)
    # A Unix socket path holds 108 bytes, so the socket lives where the
    # design puts it: a 0700 directory under the runtime dir.
    runtime = tempfile.mkdtemp(
        prefix="collins-spike-split1-", dir=os.environ.get("XDG_RUNTIME_DIR") or None
    )
    os.chmod(runtime, 0o700)
    names = ["cat", "sink", "pass", "answer", "misorder", "strip"]
    if args.only:
        names = args.only.split(",")
    if not common.have_cli():
        print("no `claude` or no login here: only the `cat` scenario runs")
        names = [n for n in names if n in ("cat", "sink")]
    failed = False
    runs = {}
    try:
        for name in names:
            turn = name == "strip" and not args.no_turn
            bound = 240 if name == "strip" else 90
            run = run_scenario(name, scratch, runtime, turn, bound)
            runs[name] = run
            with open(os.path.join(scratch, f"{name}.run.json"), "w") as f:
                json.dump(run, f)
            viewer = run.get("viewer", {})
            steps = viewer.get("steps", {})
            show(f"scenario {name}", {
                "viewer_exit": run.get("viewer_exit"),
                "error": run.get("error") or viewer.get("error"),
                "grid": viewer.get("grid"),
                "window_active": viewer.get("window_active"),
                "socket_mode": run.get("socket_mode"),
                "socket_dir_mode": run.get("socket_dir_mode"),
                "peer": run.get("server", {}).get("peers"),
                "client_default_max_incoming": viewer.get("default_max_incoming_client"),
                "totals": viewer.get("totals"),
            })  # fmt: skip
            if run.get("viewer_exit") != 0 or run.get("error") or viewer.get("error"):
                failed = True
                show(f"{name}: viewer stderr", run.get("viewer_stderr"))
            if "startup" in steps:
                show(f"{name}: startup", steps["startup"])
                show(f"{name}: terminal answers that reached the pty", answers_reaching_pty(run))
            if "latency" in steps:
                show(f"{name}: keystroke latency, ms", latency_report(run))
            for key in steps:
                if key not in ("startup", "latency"):
                    show(f"{name}: {key}", steps[key])
    finally:
        shutil.rmtree(runtime, ignore_errors=True)
    show("findings", verdicts(runs))
    print("\nscratch:", scratch)
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--viewer", action="store_true")
    parser.add_argument("--socket")
    parser.add_argument("--log")
    parser.add_argument("--results")
    parser.add_argument("--home")
    parser.add_argument("--child", default="cat", choices=("cat", "sink", "claude"))
    parser.add_argument("--queries", default="strip", choices=("pass", "answer", "strip"))
    parser.add_argument(
        "--scenario",
        default="cat",
        choices=("cat", "sink", "pass", "answer", "misorder", "strip"),
    )
    parser.add_argument("--classic", action="store_true", help="do not force fullscreen mode")
    parser.add_argument("--misorder", action="store_true", help="answer DA1 before what preceded it")
    parser.add_argument("--turn", action="store_true")
    parser.add_argument("--no-turn", action="store_true", help="spend no real turn")
    parser.add_argument("--only", help="comma-separated scenarios to run")
    parser.add_argument("--scratch", help="where recordings and logs go (not committed)")
    parser.add_argument("--cols", type=int, default=120)
    parser.add_argument("--rows", type=int, default=40)
    parser.add_argument("--bound", type=int, default=90, help="watchdog, seconds")
    args = parser.parse_args()
    if args.server:
        return run_server(args)
    if args.viewer:
        return run_viewer(args)
    return run_driver(args)


if __name__ == "__main__":
    sys.exit(main())
