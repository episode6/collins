# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""`collins-keeper`: the process that is every pty child's parent.

Split-service spec §3.10 item 2 (PR-3.6). The keeper is a small program on
a Unix socket beside the service's: it forks every pty child on the
service's request, stays their parent, holds a duplicate of every master
and hands the master to the service over the socket with ``SCM_RIGHTS``.
**It reads and writes no pty.** When the service goes away (a restart, an
upgrade, a crash) the kernel's pty buffer fills, the agent blocks in
``write()`` and nothing dies; the service that starts next asks the keeper
for its masters (`list`) and goes on where the last one stopped (the
adoption in `ptyserver.PtyServer.adopt`). A keeper that crashes loses
parenthood for good (F16): its children are reparented to ``systemd
--user`` (or init), which reaps them, so their exit statuses are gone; the
service hands its masters to a restarted keeper with `adopt` so the next
service restart still has something to ask, and such a child's exit is
reported with ``status: null``.

The keeper's protocol is six messages, one JSON object per line each way,
versioned apart from the API's (`KEEPER_PROTOCOL`), fds riding on the
reply with ``SCM_RIGHTS``:

    spawn  {id, argv, cwd, env, cols, rows}  -> {ok, pid}        + the master
    list   {fds?}                            -> {ok, ptys: [...]} + every master (with fds)
    resize {id, cols, rows}                  -> {ok}
    signal {id, signal}                      -> {ok}
    reap   {id, wait?}                       -> {ok, status, alive}   (the entry dropped once known)
    adopt  {id, pid}         + a master       -> {ok}

Every reply carries ``keeper`` (this process's pid) and ``v``. A `spawn`
whose exec failed answers ``ok: false, error: "spawn", errno, stage`` (the
report pipe of `fork_child`: ENOENT for an argv or a cwd that is not there,
as `ptyserver.spawn` raised before the keeper). A `reap` of a child the
keeper forked waits ``wait`` seconds at most for its status and drops the
entry (and closes its master) once the status is known; one of an adopted
pid, which the keeper cannot wait for, answers ``status: null`` and
``alive`` off ``/proc``. The keeper exits on SIGTERM and SIGINT, ending
every child it still holds (SIGHUP to its group, then SIGKILL): under
``KillMode=process`` a ``systemctl --user stop collins-keeper`` is how
every session is stopped for good, where stopping the service alone
leaves them running for the next service.

The socket is ``keeper.sock`` beside the API socket (`--socket`, or
``$XDG_RUNTIME_DIR/collins/<app id>/keeper.sock``); the keeper writes its
pid to ``keeper.pid`` beside it. Stdlib only, no GLib: the keeper must
never be the thing that breaks.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import json
import logging
import os
import select
import signal
import socket
import struct
import sys
import termios
import time

log = logging.getLogger(__name__)

KEEPER_PROTOCOL = 1
SOCKET_NAME = "keeper.sock"
PID_NAME = "keeper.pid"
# A request or a reply is one line; a spawn carries an environment.
LINE_MAX = 1024 * 1024
# How long a reap waits for a child's status by default.
REAP_WAIT_S = 0.5
# How long a client of the keeper waits for a reply.
CALL_TIMEOUT_S = 10.0
# At exit, a child that ignores SIGHUP is killed this much later.
END_GRACE_S = 3.0

_STAGE_SETUP, _STAGE_CHDIR, _STAGE_EXEC = 0, 1, 2


class KeeperError(OSError):
    """The keeper refused, or is gone: `errno` is ECONNREFUSED / EPIPE for a
    connection that failed and EINVAL for a refusal; `reply` the refusal."""

    def __init__(self, code: int, text: str, reply: dict | None = None) -> None:
        super().__init__(code, text)
        self.reply = reply or {}


# -- the child -------------------------------------------------------------------------


def fork_child(argv: list[str], cwd: str, env: dict[str, str], master: int, slave: int, report: int) -> None:
    """The child side of a pty spawn: never returns. Only os-level calls
    between fork and exec (no allocation of note, no locks: safe in a
    threaded parent, as `pty.fork` and VTE are). `os.login_tty(slave)` makes
    a new session with the slave its controlling terminal and the standard
    fds; the signal dispositions Python inherited are reset; a failure
    before the exec is reported through *report* as ``(errno, stage)``."""
    stage = _STAGE_SETUP
    try:
        os.close(master)
        os.login_tty(slave)
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
        signal.signal(signal.SIGXFSZ, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        signal.pthread_sigmask(signal.SIG_SETMASK, set())
        stage = _STAGE_CHDIR
        os.chdir(cwd)
        stage = _STAGE_EXEC
        os.execvpe(argv[0], argv, env)
    except OSError as exc:
        code = exc.errno or errno.EIO
    except BaseException:
        code = errno.EIO
    try:
        os.write(report, struct.pack("iB", code, stage))
    except OSError:
        pass
    os._exit(127)


def spawn_failure_what(stage: int, argv: list[str], cwd: str) -> str:
    """What a failed spawn could not reach, for the `SpawnError`."""
    return cwd if stage == _STAGE_CHDIR else argv[0] if stage == _STAGE_EXEC else "the pty"


def spawn_on_pty(argv: list[str], cwd: str, env: dict[str, str], cols: int, rows: int) -> tuple[int, int]:
    """Fork *argv* on a fresh pty sized *cols* x *rows*: ``(pid, master)``.
    Raises ``OSError`` carrying the child's errno (``filename`` what could
    not be reached) when the exec failed, with the master closed and the
    child reaped. What `ptyserver.PtyServer.spawn` does without a keeper
    and the keeper does for it."""
    master, slave = os.openpty()
    report_r, report_w = os.pipe()  # close-on-exec by default (PEP 446)
    try:
        set_window_size(slave, cols, rows)
        pid = os.fork()
    except BaseException:
        for fd in (master, slave, report_r, report_w):
            os.close(fd)
        raise
    if pid == 0:
        fork_child(argv, cwd, env, master, slave, report_w)  # never returns
    os.close(slave)
    os.close(report_w)
    try:
        failure = os.read(report_r, 5)  # EOF on a successful exec
    finally:
        os.close(report_r)
    if failure:
        os.close(master)
        with contextlib.suppress(ChildProcessError):
            os.waitpid(pid, 0)
        code, stage = struct.unpack("iB", failure.ljust(5, b"\0"))
        raise OSError(code, os.strerror(code), spawn_failure_what(stage, argv, cwd))
    return pid, master


def set_window_size(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def get_window_size(fd: int) -> tuple[int, int]:
    """``(cols, rows)`` off the pty."""
    rows, cols, _, _ = struct.unpack("HHHH", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8))
    return cols, rows


def pid_alive(pid: int) -> bool:
    """Whether *pid* exists and is not a zombie."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            text = fh.read()
    except OSError:
        return False
    try:
        return text[text.rindex(")") + 2 :].split()[0] != "Z"
    except (ValueError, IndexError):
        return False


# -- the wire ---------------------------------------------------------------------------


def _send(sock: socket.socket, message: dict, fds: list[int] = ()) -> None:
    payload = json.dumps(message, separators=(",", ":")).encode() + b"\n"
    if fds:
        socket.send_fds(sock, [payload], list(fds))
    else:
        sock.sendall(payload)


def _recv_line(sock: socket.socket, buffer: bytearray) -> tuple[bytes | None, list[int]]:
    """One line off *sock* (the bytes before the newline, or None at EOF)
    and the fds that rode on what was read; *buffer* carries the rest."""
    fds: list[int] = []
    while b"\n" not in buffer:
        data, got, _flags, _addr = socket.recv_fds(sock, 65536, 64)
        fds.extend(got)
        if not data:
            for fd in fds:
                os.close(fd)
            return None, []
        buffer.extend(data)
        if len(buffer) > LINE_MAX:
            for fd in fds:
                os.close(fd)
            raise KeeperError(errno.EMSGSIZE, "a keeper line over the bound")
    line, _, rest = bytes(buffer).partition(b"\n")
    buffer[:] = rest
    return line, fds


# -- the keeper -------------------------------------------------------------------------


class _Entry:
    __slots__ = ("id", "pid", "fd", "status", "adopted", "exited_at")

    def __init__(self, pty_id: int, pid: int, fd: int, adopted: bool = False) -> None:
        self.id = pty_id
        self.pid = pid
        self.fd = fd
        self.status: int | None = None
        self.adopted = adopted
        self.exited_at: float | None = None

    def listed(self) -> dict:
        try:
            cols, rows = get_window_size(self.fd)
        except OSError:
            cols, rows = 0, 0
        try:
            nonblock = bool(fcntl.fcntl(self.fd, fcntl.F_GETFL) & os.O_NONBLOCK)
        except OSError:
            nonblock = False
        return {
            "id": self.id,
            "pid": self.pid,
            "status": self.status,
            "alive": self.status is None and pid_alive(self.pid),
            "adopted": self.adopted,
            "cols": cols,
            "rows": rows,
            # The file status flags are the open file description's, shared
            # with the service's duplicate (F16): what it set shows here.
            "nonblock": nonblock,
        }


class Keeper:
    """The keeper's table and its message handlers; `serve` is its loop."""

    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path
        self.pid_path = os.path.join(os.path.dirname(socket_path), PID_NAME)
        self.ptys: dict[int, _Entry] = {}
        self.listener: socket.socket | None = None
        self.clients: dict[socket.socket, bytearray] = {}
        self._stop = False
        self._wake_r, self._wake_w = os.pipe()
        os.set_blocking(self._wake_w, False)

    # -- life

    def listen(self) -> None:
        directory = os.path.dirname(self.socket_path)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.socket_path)
        old = os.umask(0o177)
        try:
            listener.bind(self.socket_path)
        finally:
            os.umask(old)
        listener.listen(16)
        listener.setblocking(False)
        self.listener = listener
        tmp = f"{self.pid_path}.{os.getpid()}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(str(os.getpid()))
        os.replace(tmp, self.pid_path)

    def _wake(self, *_args) -> None:
        with contextlib.suppress(OSError):
            os.write(self._wake_w, b"x")

    def _on_term(self, *_args) -> None:
        self._stop = True
        self._wake()

    def serve(self) -> int:
        """Accept, answer, reap, until SIGTERM or SIGINT; then end every
        child still held, remove the socket and the pid file."""
        signal.signal(signal.SIGTERM, self._on_term)
        signal.signal(signal.SIGINT, self._on_term)
        signal.signal(signal.SIGCHLD, self._wake)
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)
        try:
            while not self._stop:
                self.step()
        finally:
            self.close()
        return 0

    def step(self, timeout: float = 1.0) -> None:
        """One turn of the loop: wait for a client, a line or a wake-up,
        answer what arrived and reap what exited."""
        assert self.listener is not None
        watched = [self.listener, self._wake_r, *self.clients]
        try:
            ready, _, _ = select.select(watched, [], [], timeout)
        except InterruptedError:
            ready = []
        for sock in ready:
            if sock is self._wake_r:
                with contextlib.suppress(OSError):
                    os.read(self._wake_r, 4096)
            elif sock is self.listener:
                with contextlib.suppress(OSError):
                    client, _ = self.listener.accept()
                    client.setblocking(True)
                    self.clients[client] = bytearray()
            else:
                self._serve_client(sock)
        self.reap_exited()

    def _serve_client(self, sock: socket.socket) -> None:
        buffer = self.clients[sock]
        try:
            line, fds = _recv_line(sock, buffer)
        except (OSError, KeeperError):
            line, fds = None, []
        if line is None:
            self._drop_client(sock)
            return
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError("not an object")
        except ValueError:
            for fd in fds:
                os.close(fd)
            self._reply(sock, {"ok": False, "error": "invalid"})
            return
        reply, out_fds = self.handle(message, fds)
        self._reply(sock, reply, out_fds)

    def _reply(self, sock: socket.socket, reply: dict, fds: list[int] = ()) -> None:
        reply = {**reply, "keeper": os.getpid(), "v": KEEPER_PROTOCOL}
        try:
            _send(sock, reply, fds)
        except OSError:
            self._drop_client(sock)

    def _drop_client(self, sock: socket.socket) -> None:
        self.clients.pop(sock, None)
        with contextlib.suppress(OSError):
            sock.close()

    # -- the messages

    def handle(self, message: dict, fds: list[int]) -> tuple[dict, list[int]]:
        """Answer one request; the fds that arrived with it are the handler's
        to keep or close."""
        kind = message.get("t")
        version = message.get("v", KEEPER_PROTOCOL)
        if version != KEEPER_PROTOCOL:
            for fd in fds:
                os.close(fd)
            return {"ok": False, "error": "protocol", "protocol": KEEPER_PROTOCOL}, []
        try:
            if kind == "spawn":
                return self._spawn(message)
            if kind == "list":
                return self._list(bool(message.get("fds", True)))
            if kind == "adopt":
                return self._adopt(message, fds), []
            entry = self.ptys.get(_pty_id(message.get("id")))
            if entry is None:
                return {"ok": False, "error": "unknown"}, []
            if kind == "resize":
                set_window_size(entry.fd, _grid(message.get("cols")), _grid(message.get("rows")))
                return {"ok": True}, []
            if kind == "signal":
                number = message.get("signal")
                if not isinstance(number, int) or not 1 <= number < 65:
                    return {"ok": False, "error": "invalid"}, []
                try:
                    os.kill(entry.pid, number)
                except ProcessLookupError:
                    return {"ok": False, "error": "gone"}, []
                return {"ok": True}, []
            if kind == "reap":
                return self._reap(entry, message.get("wait")), []
        except (ValueError, TypeError, OSError) as exc:
            log.warning("keeper: %s refused: %s", kind, exc)
            for fd in fds:
                with contextlib.suppress(OSError):
                    os.close(fd)
            return {"ok": False, "error": "invalid", "detail": str(exc)}, []
        for fd in fds:
            os.close(fd)
        return {"ok": False, "error": "unknown"}, []

    def _spawn(self, message: dict) -> tuple[dict, list[int]]:
        pty_id = _pty_id(message.get("id"))
        if pty_id in self.ptys:
            return {"ok": False, "error": "duplicate"}, []
        argv = message.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise ValueError("argv")
        cwd = message.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            raise ValueError("cwd")
        env = message.get("env")
        if not isinstance(env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in env.items()
        ):
            raise ValueError("env")
        cols, rows = _grid(message.get("cols")), _grid(message.get("rows"))
        try:
            pid, master = spawn_on_pty(argv, cwd, env, cols, rows)
        except OSError as exc:
            code = exc.errno or errno.EIO
            stage = _STAGE_SETUP
            if exc.filename == cwd:
                stage = _STAGE_CHDIR
            elif exc.filename == argv[0]:
                stage = _STAGE_EXEC
            return {"ok": False, "error": "spawn", "errno": code, "stage": stage}, []
        self.ptys[pty_id] = _Entry(pty_id, pid, master)
        return {"ok": True, "pid": pid}, [master]

    def _list(self, with_fds: bool) -> tuple[dict, list[int]]:
        entries = [self.ptys[key] for key in sorted(self.ptys)]
        listed = [entry.listed() for entry in entries]
        fds = [entry.fd for entry in entries] if with_fds else []
        return {"ok": True, "ptys": listed, "fds": with_fds}, fds

    def _adopt(self, message: dict, fds: list[int]) -> dict:
        pty_id = _pty_id(message.get("id"))
        pid = message.get("pid")
        if len(fds) != 1:
            for fd in fds:
                os.close(fd)
            raise ValueError("adopt takes one fd")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
            os.close(fds[0])
            raise ValueError("pid")
        if pty_id in self.ptys:
            os.close(fds[0])
            return {"ok": False, "error": "duplicate"}
        self.ptys[pty_id] = _Entry(pty_id, pid, fds[0], adopted=True)
        return {"ok": True}

    def _reap(self, entry: _Entry, wait: object) -> dict:
        seconds = wait if isinstance(wait, (int, float)) and not isinstance(wait, bool) else REAP_WAIT_S
        seconds = max(0.0, min(float(seconds), 30.0))
        deadline = time.monotonic() + seconds
        while True:
            self.reap_exited()
            if entry.status is not None or entry.adopted and not pid_alive(entry.pid):
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        alive = entry.status is None and pid_alive(entry.pid)
        if not alive:
            # Known for good (or unknowable): the entry goes with its master.
            self.ptys.pop(entry.id, None)
            with contextlib.suppress(OSError):
                os.close(entry.fd)
        return {"ok": True, "status": entry.status, "alive": alive}

    def reap_exited(self) -> None:
        """Collect every child that exited (SIGCHLD, or a look between)."""
        by_pid = {
            entry.pid: entry for entry in self.ptys.values() if not entry.adopted and entry.status is None
        }
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if pid == 0:
                return
            entry = by_pid.get(pid)
            if entry is not None:
                entry.status = status
                entry.exited_at = time.monotonic()

    # -- the end

    def close(self) -> None:
        """End every child still held and remove the socket and pid file."""
        for sock in list(self.clients):
            self._drop_client(sock)
        if self.listener is not None:
            with contextlib.suppress(OSError):
                self.listener.close()
            self.listener = None
        live = [entry for entry in self.ptys.values() if entry.status is None and pid_alive(entry.pid)]
        for entry in live:
            _signal_group(entry.pid, signal.SIGHUP)
        deadline = time.monotonic() + END_GRACE_S
        while time.monotonic() < deadline:
            self.reap_exited()
            if all(entry.status is not None or not pid_alive(entry.pid) for entry in live):
                break
            time.sleep(0.05)
        for entry in live:
            if entry.status is None and pid_alive(entry.pid):
                _signal_group(entry.pid, signal.SIGKILL)
        time.sleep(0.05)
        self.reap_exited()
        for entry in self.ptys.values():
            with contextlib.suppress(OSError):
                os.close(entry.fd)
        self.ptys.clear()
        for path in (self.socket_path, self.pid_path):
            with contextlib.suppress(FileNotFoundError, OSError):
                os.unlink(path)


def _signal_group(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, sig)


def _pty_id(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value < 2**32:
        raise ValueError("id")
    return value


def _grid(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 10000:
        raise ValueError("grid")
    return value


# -- the service's end ------------------------------------------------------------------


class KeeperClient:
    """The service's connection to a keeper: one blocking request at a time
    (the calls are rare: a spawn, a reap, the list at start). Raises
    `KeeperError` when the keeper refuses or is gone."""

    def __init__(self, socket_path: str, timeout: float = CALL_TIMEOUT_S) -> None:
        self.socket_path = socket_path
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self.pid: int | None = None
        self._buffer = bytearray()

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.socket_path)
        except OSError as exc:
            sock.close()
            code = exc.errno or errno.ECONNREFUSED
            raise KeeperError(code, f"no keeper at {self.socket_path}: {exc}") from exc
        self.sock = sock
        self._buffer = bytearray()
        reply, _ = self.call({"t": "list", "fds": False})
        self.pid = reply.get("keeper")

    @property
    def connected(self) -> bool:
        return self.sock is not None

    def close(self) -> None:
        if self.sock is not None:
            with contextlib.suppress(OSError):
                self.sock.close()
            self.sock = None

    def call(self, message: dict, fds: list[int] = ()) -> tuple[dict, list[int]]:
        """One request; the reply and the fds that rode on it (the
        caller's to close). A refusal is a `KeeperError` with the reply."""
        if self.sock is None:
            raise KeeperError(errno.ENOTCONN, "the keeper is not connected")
        try:
            _send(self.sock, {**message, "v": KEEPER_PROTOCOL}, fds)
            line, got = _recv_line(self.sock, self._buffer)
        except KeeperError:
            self.close()
            raise
        except OSError as exc:
            self.close()
            raise KeeperError(exc.errno or errno.EPIPE, f"the keeper went away: {exc}") from exc
        if line is None:
            self.close()
            raise KeeperError(errno.EPIPE, "the keeper closed the connection")
        try:
            reply = json.loads(line)
        except ValueError as exc:
            for fd in got:
                os.close(fd)
            self.close()
            raise KeeperError(errno.EPROTO, "the keeper answered nonsense") from exc
        if not isinstance(reply, dict) or not reply.get("ok"):
            for fd in got:
                os.close(fd)
            error = reply.get("error") if isinstance(reply, dict) else "invalid"
            details = reply if isinstance(reply, dict) else {}
            raise KeeperError(errno.EINVAL, f"the keeper refused: {error}", details)
        return reply, got

    # -- the six, for the service

    def spawn(
        self, pty_id: int, argv: list[str], cwd: str, env: dict[str, str], cols: int, rows: int
    ) -> tuple[int, int]:
        """``(pid, master)``; an exec failure is an ``OSError`` with the
        child's errno and what it could not reach, as `spawn_on_pty`."""
        try:
            reply, fds = self.call(
                {"t": "spawn", "id": pty_id, "argv": argv, "cwd": cwd, "env": env, "cols": cols, "rows": rows}
            )
        except KeeperError as exc:
            if exc.reply.get("error") == "spawn":
                code = int(exc.reply.get("errno") or errno.EIO)
                stage = exc.reply.get("stage")
                raise OSError(code, os.strerror(code), spawn_failure_what(stage, argv, cwd)) from None
            raise
        if len(fds) != 1:
            for fd in fds:
                os.close(fd)
            raise KeeperError(errno.EPROTO, "the keeper handed no master")
        return int(reply["pid"]), fds[0]

    def list(self, with_fds: bool = True) -> list[dict]:
        """Every pty the keeper holds, each with ``fd`` (the caller's to
        close) when *with_fds*."""
        reply, fds = self.call({"t": "list", "fds": with_fds})
        entries = reply.get("ptys") or []
        if with_fds:
            if len(fds) != len(entries):
                for fd in fds:
                    os.close(fd)
                raise KeeperError(errno.EPROTO, "the keeper's list and its fds differ")
            for entry, fd in zip(entries, fds, strict=True):
                entry["fd"] = fd
        return entries

    def resize(self, pty_id: int, cols: int, rows: int) -> None:
        self.call({"t": "resize", "id": pty_id, "cols": cols, "rows": rows})

    def signal(self, pty_id: int, number: int) -> None:
        self.call({"t": "signal", "id": pty_id, "signal": number})

    def reap(self, pty_id: int, wait: float = REAP_WAIT_S) -> tuple[int | None, bool]:
        """``(status, alive)``: the wait status (None when unknown, or while
        the child lives) and whether it still lives."""
        reply, _ = self.call({"t": "reap", "id": pty_id, "wait": wait})
        status = reply.get("status")
        return (status if isinstance(status, int) else None), bool(reply.get("alive"))

    def adopt(self, pty_id: int, pid: int, master: int) -> None:
        """Hand the keeper a master the service holds (a restarted keeper)."""
        self.call({"t": "adopt", "id": pty_id, "pid": pid}, [master])


def default_socket_path(app_id: str) -> str | None:
    """``$XDG_RUNTIME_DIR/collins/<app id>/keeper.sock``, or None without a
    runtime directory (the service then names the path with ``--socket``,
    from `api.server.runtime_dir`'s fallback)."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        return None
    return os.path.join(runtime, "collins", app_id, SOCKET_NAME)


def read_pid(socket_path: str) -> int | None:
    """The pid the keeper at *socket_path* wrote beside it, if it lives."""
    try:
        with open(os.path.join(os.path.dirname(socket_path), PID_NAME)) as fh:
            pid = int(fh.read().strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 1 and pid_alive(pid) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="collins-keeper", description="The Collins pty keeper.")
    parser.add_argument("--app-id", default=os.environ.get("COLLINS_APP_ID") or "com.episode6.Collins")
    parser.add_argument("--socket", help="the socket to serve on (default: beside the API socket)")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=(os.environ.get("COLLINS_LOG") or "WARNING").upper(),
        format="collins-keeper %(levelname)s: %(message)s",
    )
    path = args.socket or default_socket_path(args.app_id)
    if not path:
        print("collins-keeper: no $XDG_RUNTIME_DIR; pass --socket", file=sys.stderr)
        return 1
    keeper = Keeper(path)
    try:
        keeper.listen()
    except OSError as exc:
        print(f"collins-keeper: could not listen on {path}: {exc}", file=sys.stderr)
        return 1
    log.info("collins-keeper %d listening on %s", os.getpid(), path)
    return keeper.serve()


if __name__ == "__main__":
    sys.exit(main())
